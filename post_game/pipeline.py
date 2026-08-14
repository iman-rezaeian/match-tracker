"""End-to-end orchestrator for Tier A."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

from . import config, firestore_io
from .calibration import (
    FieldProjector,
    aim_from_calibration,
    compute_tile_aims,
    dedupe_detections_by_field_position,
    pixel_to_field_batch,
)
from .detection import Detector
from .formation import compute_formation
from .highlights import extract_clips
from .identity import assign_identities, half_windows, period_clock_to_video_time_factory, _onfield_intervals
from .identity_assign import assign_identities_v2
from .reid_stitch import stitch_tracklets, stitch_stats
from .tracklet_thumbs import generate_tracklet_thumbnails
from .tv_view import (build_review_label_track, extract_auto_highlights,
                      render_tv_reel, tv_reel_meta_from_existing)
from .stats import compute_player_stats
from .team_classifier import classify_tracks, sample_jersey_hsv
from .tracking import Tracker, TrackedDetection, to_dataframe
from .video import crop_bbox_to_equirect, iter_frames, open_video, render_perspective

log = logging.getLogger(__name__)


def run(
    game_id: str,
    field_name: str | None = None,
    tv_view: bool = False,
    max_play_s: float | None = None,
    debug_frames_every_s: float | None = None,
    skip_clips: bool = False,
    skip_upload: bool = False,
    smoke_windows: list[tuple[float, float]] | None = None,
    reuse_tv_reel: bool = False,
    stats_only: bool = False,
    pin_partition: tuple[dict[int, int], dict[int, int]] | None = None,
    skip_calibration_qc: bool = False,
    vlm_identity: bool | None = None,
) -> dict:
    """Run the full Tier A pipeline on one game. Returns the analytics dict
    written to Firestore.

    Calibration source: the per-game `calibration` field on the game doc
    (preferred). If absent and `field_name` is given, falls back to the
    legacy `teams/main/fields/<name>` collection.

    `tv_view=True` also renders a full-game broadcast view + auto-highlight
    reel after analytics complete.
    """

    # 1. Inputs
    game = firestore_io.get_game(game_id)
    roster = firestore_io.get_roster()
    field_cal = firestore_io.get_game_calibration(game_id)
    if field_cal is None and field_name:
        field_cal = firestore_io.get_field(field_name)
    if field_cal is None:
        # Launch the multi-point sphere calibration tool, wait for the user
        # to click the reference landmarks and save, then re-read.
        from . import calibrate_flat
        log.info("No calibration found. Launching multi-point sphere calibration UI...")
        calibrate_flat.calibrate_flat(game_id)
        field_cal = firestore_io.get_game_calibration(game_id)
        if field_cal is None:
            raise RuntimeError(
                f"Game {game_id} still has no calibration after the browser tool exited."
            )

    # 1a. Calibration-quality gate. A present-but-bad calibration silently
    # corrupts every field-meter stat, so hard-block BEFORE the multi-hour
    # tracking pass rather than discover it after. Stats-only refreshes reuse
    # cached tracks (the coach is deliberately re-deriving) and are exempt; the
    # --skip-calibration-qc override downgrades the block to a warning.
    if not stats_only:
        from .calibration_qc import evaluate_calibration
        # The evaluator needs the RAW calibration dict (solver tag + rms live in
        # ground_similarity), not the parsed FieldCalibration.
        _snap = firestore_io._team_doc().collection("games").document(game_id).get()
        _doc = _snap.to_dict() or {}
        _cal_dict = _doc.get("calibration")
        _field_key = (_cal_dict or {}).get("field_key") or _doc.get("fieldName")
        _prior = firestore_io.get_field_scale(_field_key) if _field_key else None
        _verdict = evaluate_calibration(_cal_dict, _prior)
        if not _verdict.ok:
            if skip_calibration_qc:
                log.warning("Calibration QC FAILED but --skip-calibration-qc set; running anyway:\n%s",
                            _verdict.summary())
            else:
                raise RuntimeError(
                    "Calibration quality gate blocked this run. Re-calibrate the field "
                    "(or pass --skip-calibration-qc to override).\n" + _verdict.summary()
                )
        else:
            log.info("Calibration QC: %s", _verdict.summary().splitlines()[0])

        # 1b. Kickoff-confirmation gate. Every on-field window (and thus minutes,
        # distance, formation, identity budget) is anchored on the H1/H2 kickoff
        # offsets. An UNconfirmed offset is likely a silent default 0 that shifts
        # every player's window (a bench player reads as on-field, etc.). Require
        # the coach to have deliberately confirmed BOTH halves in calibration.
        # Same --skip-calibration-qc override; stats-only exempt (reuses cache).
        _missing = [h for h, ok in (("1st", game.video_offset_h1_confirmed),
                                    ("2nd", game.video_offset_h2_confirmed)) if not ok]
        if _missing:
            _msg = (f"Kickoff not confirmed for the {' & '.join(_missing)} half. "
                    "Mark + confirm both kickoffs in the calibration step "
                    "(their offsets anchor every player's on-field time / minutes) "
                    "or pass --skip-calibration-qc to override.")
            if skip_calibration_qc:
                log.warning("Kickoff-confirmation gate: %s (overridden)", _msg)
            else:
                raise RuntimeError(_msg)

    # Stats-only refresh runs entirely off the cached tracks checkpoint, so the
    # (possibly long-deleted) source video is never needed — synthesize the bits
    # of `meta` we still use (fps, duration) from the parquet and skip the open.
    _stats_only_cached = (
        stats_only and (config.OUTPUTS_DIR / game_id / "tracks_raw.parquet").exists()
    )
    projector = FieldProjector(field_cal)
    if _stats_only_cached:
        import pandas as pd
        _peek = pd.read_parquet(
            config.OUTPUTS_DIR / game_id / "tracks_raw.parquet", columns=["frame", "time_s"])
        _t = _peek["time_s"].to_numpy(dtype=float)
        _f = _peek["frame"].to_numpy(dtype=float)
        _ok = _t > 0
        _fps = float(np.median(_f[_ok] / _t[_ok])) if _ok.any() else 30.0
        meta = {"fps": _fps, "duration_s": (float(_t.max()) if len(_t) else 0.0),
                "width": 0, "height": 0}
        video_path = None
        tile_aims = []
        log.info("Stats-only: using cached tracks (source video not opened); "
                 "fps≈%.2f duration≈%.0fs", _fps, meta["duration_s"])
    else:
        if not game.video_url:
            raise RuntimeError(f"Game {game_id} has no videoUrl set.")
        video_path = _ensure_local_video(game.video_url, game_id)
        meta = open_video(str(video_path))
        eq_w, eq_h = meta["width"], meta["height"]
        log.info("Video: %dx%d @ %.2f fps (%.0fs)", eq_w, eq_h, meta["fps"], meta["duration_s"])

        # 1b. Aim the virtual camera at OUR field (away from the back-hemisphere
        # field). Used only for the legacy single-aim fallback and for ball
        # crop centering; detection itself now uses multi-tile coverage.
        aim_lon, aim_lat, aim_fov = aim_from_calibration(
            field_cal.src_points_px, eq_w, eq_h,
        )
        log.info("Field aim (single, legacy): lon=%.1f° lat=%.1f° fov=%.1f°",
                 aim_lon, aim_lat, aim_fov)

        # 1c. Build the multi-tile detection aims. Three 75° tiles spread across
        # the pitch cover the ~170° horizontal angle that one perspective crop
        # can't (X5 on a 16ft pole 3m behind the near sideline). Each tile is
        # processed independently by YOLO and detections merged in field space.
        log.info("Projection model: %s", "sphere" if projector.use_sphere else "planar-homography")
        tile_aims = compute_tile_aims(
            projector, field_cal.length_m, field_cal.width_m,
            n_tiles=config.DETECT_N_TILES, fov_deg=config.DETECT_TILE_FOV_DEG,
        )
        for i, (lon, lat, fov) in enumerate(tile_aims):
            log.info("  tile %d/%d: lon=%+.1f° lat=%+.1f° fov=%.0f°",
                     i + 1, len(tile_aims), lon, lat, fov)

    # 2. Detection + tracking on perspective crops
    # Compute play windows (1st half, 2nd half) so we skip warmup/halftime/post.
    play_windows = half_windows(game, meta["duration_s"])
    log.info("Play windows: 1H=[%.1fs - %.1fs] 2H=[%.1fs - %.1fs] (halftime + dead time skipped)",
             play_windows[0][0], play_windows[0][1],
             play_windows[1][0], play_windows[1][1])
    h1_end_s = play_windows[0][1]
    # The coach-derived break, captured BEFORE smoke mode overwrites the windows
    # below — the halftime split hints its detector with this, and the smoke
    # windows would be a nonsense hint.
    logged_break_s = (play_windows[0][1], play_windows[1][0]) if len(play_windows) > 1 else None

    # Smoke-test mode: keep a window of `max_play_s` seconds centered on the
    # MIDDLE of each half. Sampling from the middle catches active play
    # (warmup at the start and stoppage near the end are not representative).
    if smoke_windows:
        log.info("SMOKE-TEST (explicit): %s", smoke_windows)
        play_windows = [(float(a), float(b)) for (a, b) in smoke_windows]
        h1_end_s = play_windows[0][1]
    elif max_play_s is not None and max_play_s > 0:
        clipped: list[tuple[float, float]] = []
        for a, b in play_windows:
            half_len = b - a
            take = min(max_play_s, half_len)
            mid = a + half_len / 2.0
            ca = max(a, mid - take / 2.0)
            cb = min(b, ca + take)
            clipped.append((ca, cb))
        log.info("SMOKE-TEST: sampling %.0fs from the middle of each half (windows %s)",
                 max_play_s, clipped)
        play_windows = clipped
        h1_end_s = play_windows[0][1]

    fps_sampled = meta["fps"] / config.SAMPLE_RATE

    def _new_tracker():
        # Flag-gated trackers (default OFF): associate in field METERS instead of
        # the distorted equirect frame. Same update() signature, so the call sites
        # below are unchanged. `projector` (built at :86) is captured from the
        # enclosing scope. Lazy imports keep the prod import graph untouched when
        # the flags are OFF. TRACK_PITCH (the DeepSORT-on-pitch rebuild) wins over
        # the older B2 surrogate if both are somehow set.
        if config.TRACK_PITCH:
            from .tracking_pitch import PitchTracker
            return PitchTracker(
                projector,
                frame_rate=max(1, int(round(fps_sampled))),
                track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
                # kit hexes feed the team-color association gate (green vs blue is
                # the one cross-team signal that survives on same-kit U10s).
                our_color_hex=_our_color(game),
                opp_color_hex=game.away_color,
            )
        if config.TRACK_FIELD_SPACE:
            from .tracking_field import FieldSpaceTracker
            return FieldSpaceTracker(
                projector,
                frame_rate=max(1, int(round(fps_sampled))),
                track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
            )
        return Tracker(
            frame_rate=max(1, int(round(fps_sampled))),
            track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
        )

    # Stage-2 checkpoint: skip the multi-hour detection + tracking pass if a
    # previous run already produced it. Lets us iterate on downstream stages
    # (filtering, identity, stats, tv-view) without burning the whole pipeline.
    # Smoke-test runs use a SEPARATE checkpoint file so a 4-minute smoke
    # checkpoint never gets mistaken for (or clobbers) a full-game one.
    ckpt_dir = config.OUTPUTS_DIR / game_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_suffix = ".smoke" if (smoke_windows or (max_play_s is not None and max_play_s > 0)) else ""
    tracks_ckpt = ckpt_dir / f"tracks_raw{ckpt_suffix}.parquet"
    jersey_ckpt = ckpt_dir / f"jersey_samples{ckpt_suffix}.npz"
    emb_ckpt = ckpt_dir / f"embeddings{ckpt_suffix}.npz"
    prov_ckpt = ckpt_dir / f"tracks_raw{ckpt_suffix}.provenance.json"
    votes_ckpt = ckpt_dir / f"kit_votes{ckpt_suffix}.npz"

    # A pinned-partition refresh skips classify+stitch, so the (multi-GB,
    # often-deleted) jersey npz isn't needed — only tracks_raw is.
    if tracks_ckpt.exists() and (jersey_ckpt.exists() or pin_partition is not None):
        import pandas as pd
        log.info("Stage 2/6: loading cached tracks from %s", tracks_ckpt)
        _check_tracks_provenance(prov_ckpt, tracks_ckpt)
        tracks_df = pd.read_parquet(tracks_ckpt)
        if jersey_ckpt.exists():
            with np.load(jersey_ckpt, allow_pickle=True) as nz:
                track_jersey_samples = {
                    int(k): list(nz[k]) for k in nz.files
                }
        else:
            track_jersey_samples = {}  # unused on the pinned path
        # Per-track Re-ID embeddings (sidecar; absent on pre-embedding caches →
        # stitching falls back to jersey-HSV).
        track_embeddings: dict[int, np.ndarray] = {}
        if emb_ckpt.exists():
            with np.load(emb_ckpt, allow_pickle=True) as nz:
                track_embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}
            log.info("  -> loaded Re-ID embeddings for %d tracks", len(track_embeddings))
        # Kit-hue votes (sidecar; absent on caches taken before kit voting →
        # classification falls back to the jersey-HSV path).
        kit_votes: dict[int, tuple[int, int]] = {}
        if votes_ckpt.exists():
            with np.load(votes_ckpt) as nz:
                kit_votes = {int(k): (int(nz[k][0]), int(nz[k][1])) for k in nz.files}
            log.info("  -> loaded kit votes for %d tracks", len(kit_votes))
    else:
        detector = Detector()
        tracker = _new_tracker()
        current_half = 1

        # Per-track kit-hue tally: track_id -> (n_our, n_opp). Filled from the
        # video frame during tracking; consumed by classify_from_kit_votes.
        kit_votes: dict[int, tuple[int, int]] = {}
        # Raw torso VALUES per detection, kept only for the value axis so the kit
        # anchors can be fitted to the footage after the pass (see the
        # fit_value_anchors call below). A hex is the fabric in a swatch; a black
        # kit in sunlight photographs at V150-200, not its nominal V10, and on
        # the value axis that gap is the whole decision.
        kit_value_samples: dict[int, list[float]] = {}
        _kit_vote_on = config.KIT_VOTE_ENABLED and bool(game.away_color)
        if _kit_vote_on:
            from .kit_vote import hex_to_hsv, pick_axis, torso_roi, vote_detection
            _our_hex, _opp_hex = _our_color(game), game.away_color
            # The team owns a green kit and a black one, and they need different
            # discriminators: green separates from a blue opponent by HUE, black
            # from a white/grey one only by BRIGHTNESS (S0 — it has no hue). The
            # axis is chosen from the two anchors so both regimes work.
            _kit_axis = pick_axis(_our_hex, _opp_hex)
            log.info("  kit vote ON: ours %s %s vs opp %s %s — deciding on %s",
                     _our_hex, tuple(int(v) for v in hex_to_hsv(_our_hex)),
                     _opp_hex, tuple(int(v) for v in hex_to_hsv(_opp_hex)),
                     _kit_axis.upper())
        elif config.KIT_VOTE_ENABLED:
            log.warning("  kit vote OFF: game has no awayColor to compare against")

        # Opponent pre-filter accounting (see the 2a block below). Logged after
        # the pass so a run that quietly filtered nothing — or filtered far too
        # much — is visible rather than inferred from downstream weirdness.
        n_dropped_opp = 0
        n_dropped_off = 0
        n_kept_after_filter = 0

        all_tracks: list[TrackedDetection] = []
        track_jersey_samples: dict[int, list[np.ndarray]] = {}
        # Latest smoothed Re-ID embedding per track (boxmot's smooth_feat is an
        # EMA over the track's life, so the last one summarizes its appearance).
        track_embeddings: dict[int, np.ndarray] = {}

        log.info("Stage 2/6: detection + tracking...")
        # Optional debug overlay: every N video seconds, dump the current
        # crop with bbox + track_id overlays to disk so the user can eyeball
        # detection quality mid-run without waiting for the TV reel.
        debug_dir = None
        next_debug_t = 0.0
        if debug_frames_every_s and debug_frames_every_s > 0:
            import cv2 as _cv2
            debug_dir = ckpt_dir / "debug_frames"
            debug_dir.mkdir(parents=True, exist_ok=True)
            log.info("DEBUG-FRAMES: writing annotated previews to %s every %.1fs",
                     debug_dir, debug_frames_every_s)
        # Progress accounting — total video seconds we'll actually process
        # (sum of play_windows). Lets us estimate ETA from wall-clock rate.
        total_play_s = sum(b - a for a, b in play_windows)
        stage2_t0 = time.time()
        last_log_t = stage2_t0
        n_samples = 0
        for sample in iter_frames(
            str(video_path),
            sample_rate=config.SAMPLE_RATE,
            windows=play_windows,
            render_crop=False,
        ):
            n_samples += 1
            # Reset tracker at halftime — track IDs must not bridge halves
            # (players swap ends, teams swap sides, anyone could be off the field).
            # But CARRY the id counter across the reset: a fresh tracker restarts
            # ids at 1 (boxmot resets BaseTrack._count in __init__; PitchTracker
            # resets self._next_id), so without this, half-2 ids collide with
            # half-1 ids and two different players get folded into one track_id —
            # corrupting team classification and per-player coverage/stats. All
            # three tracker types expose `_next_id` (see _new_tracker), so this is
            # uniform. Mirrors tracking/retrack_smoke.py's --full-game path.
            if current_half == 1 and sample.time_s >= h1_end_s:
                next_id_carry = tracker._next_id
                tracker = _new_tracker()
                tracker._next_id = next_id_carry
                current_half = 2

            # --- Multi-tile detection ---
            # Render N perspective tiles from the same equirect frame, run
            # YOLO on the whole batch, then unify detections in equirect
            # space + dedupe by ground-plane position. This covers the
            # whole pitch from our centerline+3m+5m X5 mount (~170° H FOV).
            tile_crops = [
                render_perspective(sample.eq_frame, lon, lat, fov, config.CROP_W, config.CROP_H)
                for (lon, lat, fov) in tile_aims
            ]
            det_lists = detector.detect_persons(tile_crops)
            dets: list = []
            for crop_idx, det_list in enumerate(det_lists):
                lon, lat, fov = tile_aims[crop_idx]
                for d in det_list:
                    d.frame_index = sample.frame_index
                    d.bbox_eq = crop_bbox_to_equirect(
                        d.bbox_crop, lon, lat, fov,
                        eq_w, eq_h, config.CROP_W, config.CROP_H,
                    )
                    # For the tracker: use equirect bbox as the working
                    # coordinate system so detections from different tiles
                    # are directly comparable.
                    d.bbox_crop = d.bbox_eq
                    dets.append(d)
            dets = dedupe_detections_by_field_position(
                dets, projector, config.DETECT_TILE_DEDUPE_M,
            )

            # 2a0. OFF-FIELD PRE-FILTER — the stage-3b test, moved before the
            # tracker. Runs FIRST because it is pure geometry on coordinates we
            # already have, so it shrinks the set the (pixel-reading) kit vote
            # below has to process. Same +-1.5 m buffer as stage 3b, so the two
            # agree and 3b becomes a no-op on whatever this already removed.
            if config.TRACK_DROP_OFFFIELD and dets:
                _feet = np.array([[(d.bbox_eq[0] + d.bbox_eq[2]) / 2.0, d.bbox_eq[3]]
                                  for d in dets], dtype=np.float64)
                _xy = projector.pixel_to_field_batch(_feet)
                _L, _W = field_cal.length_m, field_cal.width_m
                _ok = ((_xy[:, 0] >= -1.5) & (_xy[:, 0] <= _L + 1.5)
                       & (_xy[:, 1] >= -1.5) & (_xy[:, 1] <= _W + 1.5)
                       & np.isfinite(_xy).all(axis=1))
                n_dropped_off += int((~_ok).sum())
                dets = [d for d, k in zip(dets, _ok) if k]

            # 2a. OPPONENT PRE-FILTER — decide team BEFORE association, not after.
            # Halves the number of bodies the tracker can confuse ours with (see
            # config.TRACK_DROP_OPPONENTS for the measurement). Only a CONFIDENT
            # opponent read removes a detection; `vote_detection` returns 0 when
            # the torso is too small, too washed, or sits between the two kit
            # anchors, and those are KEPT — an unclear frame must never be able to
            # end a real player's track. The per-track tally below re-votes on the
            # surviving boxes and still has the final say.
            if _kit_vote_on and config.TRACK_DROP_OPPONENTS:
                _keep = []
                for d in dets:
                    if vote_detection(sample.eq_frame, d.bbox_eq,
                                      _our_hex, _opp_hex, axis=_kit_axis,
                                      min_s=config.PITCH_COLOR_MIN_S,
                                      min_px=config.PITCH_COLOR_MIN_PIXELS,
                                      hue_margin=config.PITCH_COLOR_MARGIN_DEG,
                                      value_margin=config.KIT_VOTE_VALUE_MARGIN) == -1:
                        n_dropped_opp += 1
                        continue
                    _keep.append(d)
                n_kept_after_filter += len(_keep)
                dets = _keep

            # ReID + tracker run on the equirect frame directly. BotSort
            # crops ROIs by xyxy so any frame works as long as the bboxes
            # are in that frame's coordinate space.
            tracked = tracker.update(sample.eq_frame, dets, time_s=sample.time_s)
            for t in tracked:
                all_tracks.append(t)
                # Sample jersey HSV on EVERY detection. The classifier needs
                # >=2 tracks with samples or it bails out with team_id=-1 for
                # everyone. Old gate (frame_index % 30 == 0) failed for short
                # smoke windows. sample_jersey_hsv is cheap (a few hundred
                # pixel reads on a ROI we already have in memory).
                hsv = sample_jersey_hsv(sample.eq_frame, t.bbox_eq)
                if len(hsv) > 0:
                    track_jersey_samples.setdefault(t.track_id, []).append(hsv)
                if t.appearance_embedding is not None:
                    track_embeddings[t.track_id] = t.appearance_embedding
                # Kit vote, taken here because it needs the FRAME: which of the
                # two kit hues is this torso nearer? sample_jersey_hsv above
                # can't answer it — it drops the grass band, and our green kit
                # lives inside that band while the opponent's blue does not, so
                # its samples are missing exactly the colour that identifies us.
                if _kit_vote_on:
                    _v = vote_detection(sample.eq_frame, t.bbox_eq,
                                        _our_hex, _opp_hex, axis=_kit_axis,
                                        min_s=config.PITCH_COLOR_MIN_S,
                                        min_px=config.PITCH_COLOR_MIN_PIXELS,
                                        hue_margin=config.PITCH_COLOR_MARGIN_DEG,
                                        value_margin=config.KIT_VOTE_VALUE_MARGIN)
                    if _v:
                        _o, _p = kit_votes.get(t.track_id, (0, 0))
                        kit_votes[t.track_id] = (_o + (_v == 1), _p + (_v == -1))
                    # Stash the torso brightness so the anchors can be re-fitted
                    # once the whole game's distribution is known. Cheap (one
                    # median per detection) and only on the axis that needs it.
                    if _kit_axis == "value":
                        import cv2 as _cv2
                        _roi = torso_roi(sample.eq_frame, t.bbox_eq)
                        if _roi is not None and _roi.size:
                            _v_med = float(np.median(
                                _cv2.cvtColor(_roi, _cv2.COLOR_BGR2HSV)[:, :, 2]))
                            kit_value_samples.setdefault(t.track_id, []).append(_v_med)

            # Debug-frame dump (cheap; runs only when requested). Renders a
            # downscaled equirect preview with ALL detection bboxes drawn,
            # so you can see whether the whole field is being covered.
            if debug_dir is not None and sample.time_s >= next_debug_t:
                import cv2 as _cv2
                # Downscale 5760x2880 -> 1920x960 for a viewable jpg.
                scale = 1920.0 / sample.eq_frame.shape[1]
                preview = _cv2.resize(
                    sample.eq_frame, None, fx=scale, fy=scale,
                    interpolation=_cv2.INTER_AREA,
                )
                for t in tracked:
                    x1, y1, x2, y2 = (int(v * scale) for v in t.bbox_eq)
                    _cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    _cv2.putText(
                        preview, f"#{t.track_id} {t.confidence:.2f}",
                        (x1, max(0, y1 - 6)),
                        _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, _cv2.LINE_AA,
                    )
                label = f"t={sample.time_s:7.1f}s  n_tracks={len(tracked)}  n_dets={len(dets)}  tiles={len(tile_aims)}"
                _cv2.putText(preview, label, (10, 24),
                             _cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, _cv2.LINE_AA)
                fname = debug_dir / f"frame_{int(sample.time_s):06d}s.jpg"
                _cv2.imwrite(str(fname), preview, [_cv2.IMWRITE_JPEG_QUALITY, 80])
                next_debug_t = sample.time_s + debug_frames_every_s

            # Heartbeat every ~30s of wall time so the user can see progress
            # and ETA. Cheap (no per-sample work in the common case).
            now = time.time()
            if now - last_log_t >= 30.0:
                # Map sample.time_s into "play seconds processed so far" by
                # accumulating completed windows + position within current one.
                processed_play_s = 0.0
                for a, b in play_windows:
                    if sample.time_s >= b:
                        processed_play_s += b - a
                    elif sample.time_s > a:
                        processed_play_s += sample.time_s - a
                        break
                    else:
                        break
                elapsed = now - stage2_t0
                rate = n_samples / elapsed if elapsed > 0 else 0.0
                frac = processed_play_s / total_play_s if total_play_s > 0 else 0.0
                eta_s = elapsed * (1 - frac) / frac if frac > 0 else float("inf")
                log.info(
                    "  stage2: %5.1f%% | video %6.1fs / %.1fs | samples=%d "
                    "(%.1f/s) | tracks=%d | elapsed=%.0fs eta=%.0fs",
                    frac * 100.0, processed_play_s, total_play_s,
                    n_samples, rate, len({t.track_id for t in all_tracks}),
                    elapsed, eta_s,
                )
                last_log_t = now

        if config.TRACK_DROP_OFFFIELD:
            log.info("  -> off-field pre-filter: dropped %d detections before tracking",
                     n_dropped_off)
            if n_dropped_off == 0:
                log.warning("  !! off-field pre-filter removed NOTHING — expected "
                            "17-27%% on this footage; check the calibration")

        if config.TRACK_DROP_OPPONENTS and _kit_vote_on:
            _seen = n_dropped_opp + n_kept_after_filter
            log.info("  -> opponent pre-filter: dropped %d of %d detections (%.0f%%) "
                     "before tracking; %d kept (ours + unclear)",
                     n_dropped_opp, _seen,
                     100.0 * n_dropped_opp / max(_seen, 1), n_kept_after_filter)
            if _seen and n_dropped_opp / _seen > 0.75:
                log.warning("  !! pre-filter removed >75%% of detections — the kit "
                            "anchors are probably inverted; expect our team to be "
                            "missing from the output")

        tracks_df = to_dataframe(all_tracks, fps=fps_sampled)

        # 2b. RE-VOTE on data-fitted kit anchors (value axis only).
        # The per-detection votes above used the kit hexes, which describe the
        # fabric in a swatch rather than in sunlight. On mrhvbvwi1gjpn the black
        # kit's nominal V10 fell BELOW the entire observed range (p1-p99 =
        # 28-250), so every one of our players sat nearer the opponent anchor:
        # the split came out 948/2217 where 7v7 demands ~1:1, and 54% of the
        # "opponent" tracks were achromatic — called opponent purely for being
        # bright. Now that the whole game's distribution is in hand, fit the
        # anchors to it and recount. Hue needs none of this (it is roughly
        # illumination-invariant), so this touches only the value axis and
        # leaves every hue game byte-identical.
        if _kit_vote_on and _kit_axis == "value" and kit_value_samples:
            from .kit_vote import fit_value_anchors
            _per_track = {t: float(np.median(v)) for t, v in kit_value_samples.items()}
            _our_v, _opp_v, _note = fit_value_anchors(
                list(_per_track.values()), _our_hex, _opp_hex)
            if _our_v is None:
                # Abstained: the distribution can't support anchors (one team
                # barely present, flat light). Keep the hex votes rather than
                # bisect one blob and confidently mislabel half a team.
                log.warning("  -> kit anchors NOT fitted, keeping hex values: %s", _note)
            else:
                _margin = config.KIT_VOTE_VALUE_MARGIN
                _before = sum(1 for o, p in kit_votes.values() if o > p)
                kit_votes = {}
                for _t, _vals in kit_value_samples.items():
                    _o = _p = 0
                    for _val in _vals:
                        _d_our, _d_opp = abs(_val - _our_v), abs(_val - _opp_v)
                        if _d_opp - _d_our >= _margin:
                            _o += 1
                        elif _d_our - _d_opp >= _margin:
                            _p += 1
                    if _o or _p:
                        kit_votes[_t] = (_o, _p)
                _after = sum(1 for o, p in kit_votes.values() if o > p)
                log.info("  -> kit anchors fitted from footage: %s", _note)
                log.info("     tracks voting OURS: %d -> %d (of %d)",
                         _before, _after, len(kit_votes))

        # 2c. PRUNE OPPONENT TRACKS — the tag-don't-drop replacement for the
        # pre-tracking filter (see TRACK_DROP_OPPONENTS in config for why that
        # one deleted a third of our own team).
        #
        # This runs HERE, after the block above has fitted the kit anchors to
        # the footage, so the threshold separating the two kits is the measured
        # one rather than the hex midpoint. Nothing was deleted during tracking:
        # every detection was tagged, the votes accumulated per track, and only
        # now — with a whole track's worth of evidence and the right anchors —
        # is anything removed.
        #
        # Requires a strong majority of DECISIVE votes (abstains ignored, they
        # are not evidence either way). Asymmetric on purpose: dropping a real
        # player costs their whole stint, keeping an opponent costs one extra
        # candidate in the association.
        n_pruned_opp = 0
        if config.TRACK_TAG_OPPONENTS and kit_votes and not tracks_df.empty:
            _opp_tracks = set()
            for _t, (_o, _p) in kit_votes.items():
                _decisive = _o + _p
                if _decisive < config.KIT_TAG_MIN_VOTES:
                    continue
                if _p / _decisive >= config.KIT_TAG_TRACK_MAJORITY:
                    _opp_tracks.add(_t)
            if _opp_tracks:
                _before_rows = len(tracks_df)
                tracks_df = tracks_df[
                    ~tracks_df["track_id"].isin(_opp_tracks)].reset_index(drop=True)
                n_pruned_opp = _before_rows - len(tracks_df)
                # Report what was REMOVED, not just what survived. The drop
                # version was only auditable because a pre-filter cache happened
                # to survive on disk; a filter should not need luck to be
                # reviewable.
                log.info("  -> opponent track prune: removed %d tracks "
                         "(%d detections, %.1f%%) at >=%.0f%% of >=%d decisive votes",
                         len(_opp_tracks), n_pruned_opp,
                         100.0 * n_pruned_opp / max(1, _before_rows),
                         100 * config.KIT_TAG_TRACK_MAJORITY, config.KIT_TAG_MIN_VOTES)
            else:
                log.warning("  -> TRACK_TAG_OPPONENTS on but nothing met the "
                            "prune bar (%d tracks voted)", len(kit_votes))

        # Persist before any downstream filter touches the data — so a bug in
        # filtering / identity / stats doesn't cost another detection pass.
        tracks_df.to_parquet(tracks_ckpt)
        np.savez(
            jersey_ckpt,
            **{str(k): np.array(v, dtype=object) for k, v in track_jersey_samples.items()},
        )
        if track_embeddings:
            np.savez(emb_ckpt, **{str(k): v for k, v in track_embeddings.items()})
        if kit_votes:
            np.savez(votes_ckpt, **{str(k): np.array(v, dtype=np.int32)
                                    for k, v in kit_votes.items()})
        _write_tracks_provenance(prov_ckpt)
        log.info("  -> checkpoint written: %s + %s%s", tracks_ckpt.name, jersey_ckpt.name,
                 (" + " + emb_ckpt.name) if track_embeddings else " (no embeddings captured)")
        _n_unique_tracks = tracks_df["track_id"].nunique() if not tracks_df.empty else 0
        _n_with_samples = len(track_jersey_samples)
        _sample_counts = [len(v) for v in track_jersey_samples.values()]
        _avg = (sum(_sample_counts) / len(_sample_counts)) if _sample_counts else 0
        log.info(
            "  -> jersey samples collected for %d / %d tracks (avg %.1f samples/track). "
            "Need >=2 for classifier to run.",
            _n_with_samples, _n_unique_tracks, _avg,
        )

    log.info("  -> %d detections across %d tracks",
             len(tracks_df), tracks_df["track_id"].nunique() if not tracks_df.empty else 0)

    # 3. Pixel (equirectangular) -> field meters (projector built in stage 1c)
    foot_px = tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy() if not tracks_df.empty else np.zeros((0, 2))
    xy = projector.pixel_to_field_batch(foot_px)
    if len(xy):
        tracks_df["x_m"] = xy[:, 0]
        tracks_df["y_m"] = xy[:, 1]

        # 3b. OFF-FIELD FILTER — drop spectators, bench kids, parents,
        # opposing-team players on the other pitch. 1.5 m buffer outside
        # touchlines so throw-in run-ups and goal-line saves aren't cut.
        L, W = field_cal.length_m, field_cal.width_m
        on_field = (
            (tracks_df["x_m"] >= -1.5) & (tracks_df["x_m"] <= L + 1.5)
            & (tracks_df["y_m"] >= -1.5) & (tracks_df["y_m"] <= W + 1.5)
        )
        dropped_off = int((~on_field).sum())
        tracks_df = tracks_df.loc[on_field].reset_index(drop=True)

        # 3b2. NEVER-ENTERS-THE-PITCH FILTER — the touchline coaches.
        # The buffer above is a per-DETECTION test, so it cannot tell a coach
        # standing 0.5 m outside the line from a player taking a throw-in from
        # the same spot. A whole TRACK can: a player crosses the line and comes
        # back, a coach never does.
        #
        # The threshold is TUNED, not safe-by-construction — an earlier version
        # of this comment claimed the distribution was sharply bimodal with a
        # thin valley, which measurement refuted (the middle holds 591/482
        # substantial tracks). See DROP_NEVER_OUTSIDE_FRAC in config for the
        # tuning table; 1.0 leaked ~50k touchline detections per game because a
        # single frame of projection noise saved a track.
        #
        # Confirmed these are adults, not our kids: within 5-15 m of the camera
        # (where the coaches stand) their boxes are 1.61x taller than on-pitch
        # players at the same distance. Beyond 15 m the ratio inverts (0.79-0.89)
        # because those are distant spectators, not coaches.
        #
        # This is deliberately NOT the near-sideline-only rule it looks like it
        # should be. The coach reported these appear on the near side, but the
        # FAR sideline holds more never-enter tracks in both games (245 vs 112,
        # 188 vs 111) — near-side ones are simply larger and easier to notice.
        # A side-specific buffer would fix the visible half and miss the bigger
        # half, while also cutting the ~10x more player-detections that share
        # that strip.
        if config.DROP_NEVER_ONFIELD and not tracks_df.empty:
            _outside = ((tracks_df["x_m"] < 0) | (tracks_df["x_m"] > L)
                        | (tracks_df["y_m"] < 0) | (tracks_df["y_m"] > W))
            _per = tracks_df.assign(_o=_outside).groupby("track_id")["_o"].agg(
                ["mean", "size"])
            _never = _per.index[(_per["mean"] >= config.DROP_NEVER_OUTSIDE_FRAC)
                                & (_per["size"] >= config.DROP_NEVER_MIN_DETS)]
            if len(_never):
                _before_n = len(tracks_df)
                tracks_df = tracks_df[~tracks_df["track_id"].isin(_never)].reset_index(drop=True)
                log.info("  -> never-on-pitch filter: dropped %d tracks (%d detections) "
                         "that spent their whole life outside the touchlines",
                         len(_never), _before_n - len(tracks_df))

        # 3c. TOP-N PER FRAME — cap the bodies kept per frame, ranked by
        # (track lifetime × detection confidence) so established tracks beat
        # one-off background detections that survived the off-field filter.
        #
        # The budget scales with the match format (config.topn_per_frame): 20 for
        # 7v7, 24 for 9v9. It MUST scale, because the rank key favours long-lived
        # tracks — what a too-small cap deletes is the NEWEST track, i.e. a
        # just-subbed-on player or one re-acquired after an occlusion, exactly
        # when they are most fragile. That loss is per-player and invisible in
        # the team-level ratios, so it gets a WARNING rather than an info line.
        topn = config.topn_per_frame(getattr(game, "game_format", None))
        if not tracks_df.empty and "track_id" in tracks_df.columns:
            lifetime = tracks_df.groupby("track_id").size().rename("track_lifetime")
            tracks_df = tracks_df.merge(lifetime, on="track_id")
            conf_col = "conf" if "conf" in tracks_df.columns else None
            score = tracks_df["track_lifetime"].astype(float)
            if conf_col:
                score = score * tracks_df[conf_col].astype(float).clip(lower=0.1)
            tracks_df["_rank_score"] = score
            ranked = tracks_df.sort_values(["frame", "_rank_score"], ascending=[True, False])
            top_n = ranked.groupby("frame", group_keys=False).head(topn)
            dropped_topn = len(tracks_df) - len(top_n)
            _frames_capped = int((tracks_df.groupby("frame").size() > topn).sum())
            _frames_total = int(tracks_df["frame"].nunique())
            tracks_df = top_n.drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True)
        else:
            dropped_topn = 0
            _frames_capped = _frames_total = 0

        log.info("  -> filters: dropped %d off-field, %d below top-%d/frame (%s); %d kept",
                 dropped_off, dropped_topn, topn,
                 getattr(game, "game_format", None) or config.FORMAT_DEFAULT, len(tracks_df))
        if _frames_capped:
            log.warning(
                "  -> top-%d/frame cap BIT on %d of %d frames (%.1f%%): %d detections cut. "
                "The cap drops the SHORTEST-LIVED tracks first, so a just-subbed-on or "
                "re-acquired player can lose exactly the frames they are hardest to track in. "
                "If this is high, check the format (%s) is right for this game.",
                topn, _frames_capped, _frames_total,
                100.0 * _frames_capped / max(_frames_total, 1), dropped_topn,
                getattr(game, "game_format", None) or config.FORMAT_DEFAULT,
            )

    # 3.4 Halftime split — enforce "no track spans the break". The tracker is
    # reset at halftime and its id counter carried across (see _new_tracker call
    # site), but that only rules out id COLLISION; a boundary derived from the
    # coach's taps can still be misplaced relative to the real whistle. Detect
    # the break from the footage (the pitch empties) and cut anything that still
    # spans it, so the invariant holds regardless of which cause produced it.
    # Runs BEFORE gap-split/classification so one id universe flows downstream.
    # Skipped on the pinned path (ids must stay byte-stable for coach overrides).
    # NOT skipped for smoke runs: excluding them meant a smoke run could never
    # rehearse this stage, which is most of what a smoke run is for. Safety comes
    # from detect_halftime_break itself — it returns None unless the footage
    # actually contains a long enough empty-pitch run near the logged break, so
    # on a window that doesn't span halftime this is a no-op that says so.
    if (config.HALFTIME_SPLIT_ENABLED and not tracks_df.empty
            and pin_partition is None):
        from .halftime_split import detect_halftime_break, split_tracks_at_halftime
        # Hint the detector with the coach-derived break so an injury stoppage
        # can't be mistaken for halftime; it falls back to the logged time when
        # the footage is inconclusive.
        _logged_break = logged_break_s
        _bw = detect_halftime_break(tracks_df, logged_break=_logged_break)
        if _bw is None:
            log.info("  -> halftime split: no break detected in the footage; "
                     "keeping ids as-is (logged break %s)", _logged_break)
        else:
            _n0 = tracks_df["track_id"].nunique()
            tracks_df, track_jersey_samples, track_embeddings, _ = split_tracks_at_halftime(
                tracks_df, _bw, track_jersey_samples, track_embeddings,
            )
            _shift = (_bw[0] - _logged_break[0]) if _logged_break else 0.0
            log.info("  -> halftime split: break %.0f-%.0fs (logged %.0f-%.0fs, "
                     "shift %+.0fs) · %d -> %d track ids",
                     _bw[0], _bw[1],
                     (_logged_break[0] if _logged_break else 0.0),
                     (_logged_break[1] if _logged_break else 0.0),
                     _shift, _n0, tracks_df["track_id"].nunique())

    # 3.5 Gap-split — break "zombie" tracks (one id kept alive across long gaps,
    # teleporting between bodies) into clean contiguous sub-tracks BEFORE team
    # classification, so one id universe flows through stitch/assign/stats. Gated;
    # rebinds the three names every downstream stage reads. See post_game/gap_split.py.
    if config.GAP_SPLIT_ENABLED and not tracks_df.empty and pin_partition is None:
        from .gap_split import gap_split_tracks
        _n0 = tracks_df["track_id"].nunique()
        tracks_df, track_jersey_samples, track_embeddings, _ = gap_split_tracks(
            tracks_df, track_jersey_samples, track_embeddings,
            split_gap_s=config.SPLIT_GAP_S,
        )
        log.info("  -> gap-split: %d tracks -> %d sub-tracks (gap > %.1fs)",
                 _n0, tracks_df["track_id"].nunique(), config.SPLIT_GAP_S)

    # 4. Team classification
    # PIN PARTITION: a one-off surgical refresh (e.g. applying a confidence
    # recalibration to an already-labelled game) can pass the ORIGINAL
    # team/tracklet partition recovered from the live doc. We then SKIP
    # classify+stitch so tracklet ids stay byte-stable and the coach's
    # tracklet-keyed overrides all map (re-stitching would reshuffle ids and
    # mis-apply them). Restricted to the tracks that survived stage-3 here.
    if pin_partition is not None:
        _pin_team, _pin_tl = pin_partition
        _ids = set(tracks_df["track_id"].astype(int)) if not tracks_df.empty else set()
        team_of_track = {t: int(_pin_team.get(t, 2)) for t in _ids}
        tracklet_of_track = {t: int(_pin_tl.get(t, t)) for t in _ids}
        _our = sum(1 for v in team_of_track.values() if v == 0)
        log.info("Stage 4/6: PINNED partition from doc — %d tracks (%d ours), "
                 "%d tracklets; classify+stitch SKIPPED",
                 len(team_of_track), _our, len(set(tracklet_of_track.values())))
    else:
        log.info("Stage 4/6: team classification...")
        our_color = _our_color(game)
        # Prefer the kit-hue votes taken during tracking. They see the raw torso
        # pixels; the jersey-HSV path below sees them after the grass drop has
        # removed our green kit's own colour, which splits the teams ~3.9:1
        # instead of the ~1:1 a 7v7 game must produce. Falls back automatically
        # for caches taken before voting existed.
        if kit_votes:
            from .team_classifier import classify_from_kit_votes
            team_of_track = classify_from_kit_votes(tracks_df, kit_votes)
            _vc = sum(1 for v in team_of_track.values() if v == 0)
            _vo = sum(1 for v in team_of_track.values() if v == 1)
            log.info("  -> kit-hue votes: %d ours / %d opp / %d unknown (ratio %.2f:1)",
                     _vc, _vo, sum(1 for v in team_of_track.values() if v == -1),
                     (_vc / _vo) if _vo else float("inf"))
        else:
            team_of_track = classify_tracks(
                tracks_df, track_jersey_samples,
                our_home_color_hex=our_color,
                opp_color_hex=game.away_color,
                ref_color_hex=game.ref_color,
            )
        _team_counts: dict[int, int] = {}
        for _t in team_of_track.values():
            _team_counts[_t] = _team_counts.get(_t, 0) + 1
        log.info("  -> our_color=%s · %d tracks classified · team breakdown: %s "
                 "(0=ours, 1=opp, 2=ref/unknown)",
                 our_color, len(team_of_track), dict(sorted(_team_counts.items())))
        if _team_counts.get(0, 0) == 0:
            log.warning("  -> NO tracks classified as OUR TEAM. "
                        "Check home/away color hex on the game doc + jersey color in the smoke window. "
                        "Identity assignment will produce zero players.")

        # 4b. Tracklet stitching — collapse fragmented tracks of the same player
        # (BoT-SORT yields ~100 fragments/player) into player-consistent tracklets
        # using Re-ID embeddings (+ jersey-HSV fallback) and spatiotemporal gating.
        tracklet_of_track = stitch_tracklets(
            tracks_df, team_of_track,
            track_embeddings=track_embeddings,
            track_jersey_samples=track_jersey_samples,
            # kit hexes enable the team-color CANNOT-LINK guard (only engages
            # under the PitchTracker color gate; equirect/prod stitch unchanged).
            our_color_hex=_our_color(game), opp_color_hex=game.away_color,
        )
        _ss = stitch_stats(tracklet_of_track, team_of_track)
        log.info("  -> stitching: %d our fragments -> %d tracklets (%d merged, largest=%d frags)",
                 _ss["our_fragments"], _ss["our_tracklets"], _ss["merged_tracklets"],
                 _ss["largest_tracklet_fragments"])

    # 5. Identity — coach-log global assignment over stitched tracklets, with the
    # v0 per-fragment voter kept as a fallback if v2 collapses (e.g. no coach
    # POSITION events to anchor on).
    log.info("Stage 5/6: identity assignment...")
    clock_to_video = period_clock_to_video_time_factory(game)

    # --- camera-corrected on-field windows (sub_correct) ---------------------
    # Coaches tap SUB late during multi-sub moments, biasing the logged on/off
    # times that drive minutes/distance/identity. Use the tracklets the coach
    # ACCEPTED (identity_overrides → player) as ground truth: the union of a
    # player's accepted tracklet spans corrects their on-field window. ADDITIVE
    # (SUB events untouched), recomputed each run, guarded (half-clamp +
    # plausibility). Derived from COACH accepts (not the greedy pass's output)
    # and BEFORE assign, so the corrected minute budget reflects confirmed
    # presence, not the assignment we're about to make. Empty → no-op (default).
    sub_corrections: dict[str, dict] = {}
    if game.identity_overrides:
        from .sub_correct import compute_sub_corrections
        _tl_span = (tracks_df.assign(
            _tl=tracks_df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t))))
            .groupby("_tl")["time_s"].agg(["min", "max"]))
        _accepted: dict[str, list[tuple[float, float]]] = {}
        for _k, _pid in game.identity_overrides.items():
            if not _pid or str(_pid).startswith("__"):
                continue  # non-player sentinel / drop
            try:
                _tl = int(_k)
            except (TypeError, ValueError):
                continue
            if _tl in _tl_span.index:
                _accepted.setdefault(str(_pid), []).append(
                    (float(_tl_span.loc[_tl, "min"]), float(_tl_span.loc[_tl, "max"])))
        _logged = _onfield_intervals(game.starting_lineup, game.events, clock_to_video)
        sub_corrections = compute_sub_corrections(
            _accepted, play_windows, _logged)
        if sub_corrections:
            log.info("  -> sub-timeline: camera-corrected on-field window for %d player(s)",
                     len(sub_corrections))

    # Board orientation per period, resolved by the identity search — reused by
    # the tag pre-fill (3.3) to map field meters back to coach zone vocab.
    board_flips: dict[int, tuple] = {}
    # Periods whose lateral board orientation was near-tied (team may be mirrored).
    # Only populated when ID_ORIENT_AMBIG_REL_MARGIN > 0 (default off).
    board_orient_ambiguous: list = []
    assignments = assign_identities_v2(
        tracks_df=tracks_df,
        tracklet_of_track=tracklet_of_track,
        team_of_track=team_of_track,
        events=game.events,
        roster=roster,
        starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=clock_to_video,
        periods_video=play_windows,
        field_length_m=field_cal.length_m,
        field_width_m=field_cal.width_m,
        overrides=game.identity_overrides,
        squad=game.squad,
        resolved_flips_out=board_flips,
        orientation_ambiguous_out=board_orient_ambiguous,
        onfield_corrections=sub_corrections or None,
    )
    if game.identity_overrides:
        log.info("  -> %d coach identity override(s) loaded from game doc",
                 len(game.identity_overrides))
    _n_players_v2 = len({a.player_id for a in assignments if a.player_id})
    if _n_players_v2 < 3:
        log.warning("  -> v2 assignment produced only %d players; falling back to v0 voter.", _n_players_v2)
        assignments = assign_identities(
            tracks_df=tracks_df,
            team_of_track=team_of_track,
            events=game.events,
            roster=roster,
            starting_lineup=game.starting_lineup,
            gk_player_id=game.gk_player_id,
            gk_changes=game.gk_changes,
            period_clock_to_video_time=clock_to_video,
            field_length_m=field_cal.length_m,
            field_width_m=field_cal.width_m,
        )
    identity_by_track = {a.track_id: a.player_id for a in assignments if a.player_id}
    team_of_player = _flip_team_map(team_of_track, identity_by_track)
    _status_counts: dict[str, int] = {}
    for _a in assignments:
        _status_counts[_a.status] = _status_counts.get(_a.status, 0) + 1
    log.info("  -> %d assignments · status breakdown: %s · %d tracks mapped to a player",
             len(assignments), dict(sorted(_status_counts.items())), len(identity_by_track))
    if not identity_by_track:
        log.warning("  -> NO tracks assigned to any player. "
                    "Likely causes: (1) team classification flagged everyone as opponent, "
                    "(2) coach events fall outside the smoke window so no votes were cast, "
                    "(3) tracks at event times don't match team 0.")

    # 6. Stats + Formation
    log.info("Stage 6/6: stats, formation...")
    attack_dir = _attack_direction(game, tracks_df, identity_by_track, field_cal.length_m)
    # Coach-logged minutes per player (ground truth) = on-field intervals
    # (lineup + subs) clipped to the play windows. Used for minutes_played.
    # `sub_corrections` (camera-derived, from accepted tracklets) override a late
    # SUB tap on the relevant edge; empty → identical to the pure coach log.
    _onf = _onfield_intervals(game.starting_lineup, game.events, clock_to_video,
                              corrections=sub_corrections or None)
    # `played_minutes` below is the coach's ANSWER for how long each child was on
    # — the denominator for coverage and the base for extrapolation. It must keep
    # using the raw log: widening it would hand every player minutes they didn't
    # play. Only the attribution FILTER gets slack, so a detection near a messy
    # rotation stops being deleted while the minutes it counts against stay honest.
    from .sub_slack import relax_intervals, sub_times_from_events
    _onf_filter = relax_intervals(
        _onf, sub_times_from_events(game.events, clock_to_video), log_fn=log.info)

    played_minutes: dict[str, float] = {}
    for _pid, _ivs in _onf.items():
        _tot = 0.0
        for (_a, _b) in _ivs:
            for (_pa, _pb) in play_windows:
                _lo, _hi = max(_a, _pa), min(_b, _pb)
                if _hi > _lo:
                    _tot += _hi - _lo
        played_minutes[str(_pid)] = _tot / 60.0

    # Analytics echo of the camera-corrections, for the PWA to SHOW the coach
    # (before→after) and to source the corrected minutes from (its own
    # playerSeconds uses a different, wall-clock axis). Times converted back to
    # game-clock (period+elapsed) for display; correctedSeconds = the corrected
    # on-field length clipped to play windows (matches played_minutes*60).
    sub_corrections_echo: list[dict] = []
    if sub_corrections:
        from .sub_correct import clock_or_none_factory
        # Renders a correction edge as a game clock, or None for the "played to
        # the final whistle" sentinel (never-subbed-off), so the echo never
        # carries a 1e9-derived garbage clock time.
        _clk = clock_or_none_factory(play_windows, clock_to_video)
        for _pid, _c in sub_corrections.items():
            sub_corrections_echo.append({
                "playerId": _pid,
                "correctedSeconds": round(played_minutes.get(str(_pid), 0.0) * 60.0, 1),
                "onCorrected": _clk(_c.get("onS")), "offCorrected": _clk(_c.get("offS")),
                "onLogged": _clk(_c.get("loggedOnS")), "offLogged": _clk(_c.get("loggedOffS")),
            })

    # Personalized sprint thresholds (plan 4.5): per player,
    # max(floor, frac × season speed) from prior games' analytics docs.
    # Median of per-game p99s, dropping cap-pinned (swap-polluted) games.
    sprint_thresholds: dict[str, float] = {}
    try:
        _prior = firestore_io.collect_prior_player_top_speeds(exclude_game_id=game_id)
        _cap = 0.95 * config.MAX_PLAUSIBLE_SPEED_MS
        for _pid, _speeds in _prior.items():
            _clean = [v for v in _speeds if v < _cap]
            if _clean:
                sprint_thresholds[_pid] = max(
                    config.SPRINT_PERSONAL_FLOOR_MS,
                    config.SPRINT_PERSONAL_FRAC * float(np.median(_clean)),
                )
        log.info("  sprint thresholds: %d personalized (%.1f–%.1f m/s), fallback %.1f",
                 len(sprint_thresholds),
                 min(sprint_thresholds.values(), default=0.0),
                 max(sprint_thresholds.values(), default=0.0),
                 config.SPRINT_THRESHOLD_MS)
    except Exception as e:
        log.warning("Personalized sprint thresholds failed (using fixed %.1f): %s",
                    config.SPRINT_THRESHOLD_MS, e)
    # Physically-impossible instants (one player in two places at once — a
    # tracker/stitch merge) are dropped from every stat and reported here so the
    # coach can see which of his labelled tracklets are in conflict.
    player_conflicts: dict[str, dict] = {}
    player_stats = compute_player_stats(
        tracks_field_df=tracks_df,
        identity_by_track=identity_by_track,
        field_length_m=field_cal.length_m,
        field_width_m=field_cal.width_m,
        fps_after_sample=fps_sampled,
        we_attack_right=attack_dir.get(1, True),
        periods=play_windows,
        gk_player_id=game.gk_player_id,
        played_minutes=played_minutes,
        sprint_thresholds=sprint_thresholds,
        conflicts_out=player_conflicts,
        tracklet_of_track=tracklet_of_track,
        # The coach's SUB taps as an independent filter: a detection credited to a
        # player while the log says he was on the bench is the wrong child. The
        # boundaries are relaxed by each substitution's own tap spread first —
        # the taps are trustworthy about WHO changed, but only as trustworthy
        # about WHEN as the rotation was quick to enter. See sub_slack.
        onfield_intervals={str(k): v for k, v in _onf_filter.items()},
    )
    # The report mixes two independent defect kinds per player, so every read is
    # .get(): `conflict_seconds` (physically impossible — two places at once) and
    # `offwindow_frac` (credited while the SUB log says he was on the bench). A
    # player can have either, both, or neither.
    if player_conflicts:
        _conf = {p: v for p, v in player_conflicts.items() if v.get("conflict_seconds")}
        _off = {p: v for p, v in player_conflicts.items() if v.get("offwindow_detections")}
        if _conf:
            _cs = sum(v.get("conflict_seconds", 0) for v in _conf.values())
            log.warning("  identity CONFLICTS: %d player(s), %.0fs of provably "
                        "impossible time excluded from stats (worst: %s)",
                        len(_conf), _cs,
                        ", ".join(f"{p}={v.get('conflict_seconds', 0)}s" for p, v in
                                  sorted(_conf.items(),
                                         key=lambda kv: -kv[1].get("conflict_seconds", 0))[:3]))
        if _off:
            log.warning("  BENCH-TIME leak: %d player(s) had detections credited while "
                        "the SUB log says they were off (worst: %s)",
                        len(_off),
                        ", ".join(f"{p}={100*v.get('offwindow_frac', 0):.0f}%" for p, v in
                                  sorted(_off.items(),
                                         key=lambda kv: -kv[1].get("offwindow_frac", 0))[:3]))
    formation_snaps, team_ts = compute_formation(
        tracks_df, identity_by_track, team_of_player,
        periods=_periods_seconds(game, meta["duration_s"], clock_to_video),
        gk_player_id=game.gk_player_id,
        coach_events=game.events,
        starting_lineup=game.starting_lineup,
    )
    # GK positioning analysis removed — not used in the film room.

    # 4.6 Field tilt: team-centroid third-occupancy %, attack-normalized per
    # half — the best no-ball possession proxy available pre-8K. Accumulated
    # per period so the film room can show H1 vs H2 tilt (a team that pins the
    # opponent back in H1 but sits deep in H2 is the whole point of the metric);
    # a game-wide roll-up is also emitted for backward compatibility with older
    # PWA builds that read the flat dict.
    field_tilt = None
    try:
        ts_t, ts_cx = team_ts.times_s, team_ts.centroid_x_m
        if ts_t:
            L = field_cal.length_m
            per_period = {}  # period(int) -> [def, mid, att]
            for t, x in zip(ts_t, ts_cx):
                pi = next((i + 1 for i, (a, b) in enumerate(play_windows) if a <= t <= b), 1)
                depth = (x / L) if attack_dir.get(pi, True) else (1.0 - x / L)
                b = 0 if depth < 1.0 / 3 else (1 if depth < 2.0 / 3 else 2)
                per_period.setdefault(pi, [0, 0, 0])[b] += 1

            def _tilt(counts):
                n = sum(counts)
                if not n:
                    return None
                return {"def_pct": 100.0 * counts[0] / n,
                        "mid_pct": 100.0 * counts[1] / n,
                        "att_pct": 100.0 * counts[2] / n}

            total = [sum(c[i] for c in per_period.values()) for i in range(3)]
            gw = _tilt(total)
            if gw:
                by_half = []
                for pi in sorted(per_period):
                    ph = _tilt(per_period[pi])
                    if ph:
                        by_half.append({"period": pi, **ph})
                field_tilt = {**gw, "byHalf": by_half}
    except Exception as e:
        log.warning("Field tilt failed: %s", e)

    # --- Stats-only refresh: re-run purely to apply FIX-IDS overrides. Recompute
    # the identity-dependent analytics and MERGE them in, leaving the reel / audio /
    # broadcast-index (identity-independent) untouched — no re-render, no re-upload.
    if stats_only:
        # The VLM draft stage lives at 7c-bis, well past this early return, and
        # it needs the source video to render number crops — which a stats-only
        # refresh deliberately never opens. Passing --vlm-identity here used to
        # be accepted and then silently do nothing; say so instead.
        if (config.VLM_IDENTITY if vlm_identity is None else vlm_identity):
            log.warning("--vlm-identity has no effect with --stats-only: the VLM "
                        "stage renders crops from the source video, which a "
                        "stats-only refresh does not open. Re-run without "
                        "--stats-only (cached tracks are still reused).")
        _tlrecs = _build_tracklet_index(tracks_df, tracklet_of_track, assignments,
                                        fps_sampled, field_cal.length_m, field_cal.width_m,
                                        track_jersey_samples=track_jersey_samples,
                                        our_color_hex=_our_color(game),
                                        opp_color_hex=game.away_color)
        try:  # preserve existing thumbnail URLs (crops don't change with identity)
            _prev = firestore_io.read_analytics(game_id) or {}
            _thumbs = {t.get("tracklet_id"): t.get("thumb_url")
                       for t in (_prev.get("tracklets") or []) if t.get("thumb_url")}
            for _r in _tlrecs:
                if not _r.get("thumb_url") and _thumbs.get(_r["tracklet_id"]):
                    _r["thumb_url"] = _thumbs[_r["tracklet_id"]]
        except Exception as e:
            log.warning("stats-only: thumbnail preserve failed: %s", e)
        _update = {
            "identity_assignments": [asdict(a) for a in assignments],
            "tracklets": _tlrecs,
            "player_stats": [_player_stat_to_dict(s) for s in player_stats],
            "formation_snapshots": [
                {**asdict(f),
                 "avg_positions": {k: list(v) for k, v in f.avg_positions.items()},
                 "coach_positions_norm": {k: list(v) for k, v in f.coach_positions_norm.items()}}
                for f in formation_snaps
            ],
            "team_time_series": asdict(team_ts),
            "field_tilt": field_tilt,
            "sub_corrections": sub_corrections_echo,
            # Provably-impossible spans (one player in two places at once) that
            # were EXCLUDED from the stats above, so the coach can see which of
            # his labelled tracklets conflict instead of trusting a silent average.
            "player_conflicts": player_conflicts,
            "generated_at_ms": int(time.time() * 1000),
        }
        if board_orient_ambiguous:
            _update["orientation_ambiguous_periods"] = board_orient_ambiguous
        firestore_io.write_analytics_merge(game_id, _sanitize_json(_update))
        log.info("Stats-only refresh: %s — %d players; reel/audio/broadcast-index preserved",
                 game_id, len(player_stats))
        return _update

    # 7. Highlight clips (per-event MP4s). Slow because we re-seek the source
    # video for every tagged event — skip during iteration.
    clips = []
    if skip_clips:
        log.info("Stage 7/7: SKIPPED highlight clips (--skip-clips)")
    else:
        log.info("Stage 7/7: highlight clips...")
        try:
            clips = extract_clips(
                video_path=str(video_path),
                events=game.events,
                tracks_field_df=tracks_df,
                identity_by_track=identity_by_track,
                projector=projector,
                period_clock_to_video_time=clock_to_video,
                game_id=game_id,
                upload=not skip_upload,
            )
        except Exception as e:
            log.warning("Highlight extraction failed: %s", e)
            clips = []

    # 7b. Optional TV-view + auto-highlight reel
    tv_reel_meta = None
    auto_hl_meta = None
    if tv_view:
        log.info("Stage 7b: TV reel + auto-highlights...")
        try:
            if reuse_tv_reel:
                tv_reel_meta = tv_reel_meta_from_existing(
                    game_id=game_id,
                    play_windows=play_windows,
                    upload=not skip_upload,
                )
                if tv_reel_meta is None:
                    log.info("  --reuse-tv-reel: no local reel to reuse; rendering fresh.")
            if tv_reel_meta is None:
                tv_reel_meta = render_tv_reel(
                    video_path=str(video_path),
                    tracks_field_df=tracks_df,
                    projector=projector,
                    game_id=game_id,
                    field_length_m=field_cal.length_m,
                    field_width_m=field_cal.width_m,
                    upload=not skip_upload,
                    play_windows=play_windows,
                    events=game.events,
                    clock_to_video=clock_to_video,
                )
        except Exception as e:
            log.warning("TV reel failed: %s", e)
        try:
            auto_hl_meta = extract_auto_highlights(
                video_path=str(video_path),
                events=game.events,
                tracks_field_df=tracks_df,
                projector=projector,
                period_clock_to_video_time=clock_to_video,
                game_id=game_id,
                field_length_m=field_cal.length_m,
                field_width_m=field_cal.width_m,
                upload=not skip_upload,
                analyzed_windows=play_windows,
            )
        except Exception as e:
            log.warning("Auto-highlights failed: %s", e)

    # 7b-pub. Public-reel audio swap: replace the original audio (coach voice /
    # kids' names / sideline chatter) with a stadium bed + goal roars so the
    # PUBLIC reel is privacy-safe. The original reels stay the dugout copy. This
    # is a fast remux (video stream-copied), runs on the just-rendered reels.
    public_tv_url = None
    public_hl_url = None
    if tv_view and config.PUBLIC_AUDIO_ENABLED:
        from .public_audio import render_public_audio, goal_video_times
        _gvts = goal_video_times(game, clock_to_video)
        _tvdir = config.OUTPUTS_DIR / game_id / "tv_view"
        for _meta, _src, _dst, _key, _slot in (
            (tv_reel_meta, "tv_reel.mp4", "tv_reel_public.mp4", f"tv_view/{game_id}/tv_reel_public.mp4", "tv"),
            (auto_hl_meta, "auto_highlights.mp4", "auto_highlights_public.mp4", f"tv_view/{game_id}/auto_highlights_public.mp4", "hl"),
        ):
            if not _meta:
                continue
            try:
                _out = render_public_audio(str(_tvdir / _src), str(_tvdir / _dst),
                                           segments=_meta.segments, goal_video_times=_gvts)
                if _out and not skip_upload:
                    _url = firestore_io.upload_clip(_out, _key)
                    if _slot == "tv":
                        public_tv_url = _url
                    else:
                        public_hl_url = _url
            except Exception as e:
                log.warning("public-audio swap (%s) failed: %s", _src, e)

    # 7c. Build per-event broadcast index. Used by the PWA overlay layer
    # to draw a live scorebug + goal/sub popups while the user watches the
    # tv_reel or auto_highlights mp4. Done here (post-pipeline) because we
    # need clock_to_video + the reel segment list before we can map each
    # source-video event time into reel-relative time.
    # Tag pre-fill (Phase 3.3): per-event suggestedZone / suggestedPressure
    # from the assigned player's tracked position at the action moment. The
    # PWA confirm queue pre-selects these so tagging is confirm, not create.
    try:
        tag_suggestions = _build_tag_suggestions(
            game=game,
            tracks_df=tracks_df,
            identity_by_track=identity_by_track,
            team_of_track=team_of_track,
            period_clock_to_video_time=clock_to_video,
            board_flips=board_flips,
            field_length_m=field_cal.length_m,
            field_width_m=field_cal.width_m,
        )
        log.info("  tag pre-fill: suggestions for %d/%d events "
                 "(%d with zone, %d with pressure)",
                 len(tag_suggestions), len(game.events),
                 sum(1 for s in tag_suggestions.values() if s.get("zone")),
                 sum(1 for s in tag_suggestions.values() if s.get("pressure")))
    except Exception as e:
        log.warning("Tag pre-fill failed: %s", e)
        tag_suggestions = {}

    events_index = _build_broadcast_events_index(
        game=game,
        roster=roster,
        period_clock_to_video_time=clock_to_video,
        tv_reel_segments=(tv_reel_meta.segments if tv_reel_meta else play_windows),
        auto_highlights_segments=(auto_hl_meta.segments if auto_hl_meta else []),
        tag_suggestions=tag_suggestions,
    )

    # 7c2. Review label track (plan 3.7): per-second name-chip keyframes for
    # the coach reel overlay. Derived from the same aim stream as the reel —
    # no video re-render. Coach-only consumer (analytics doc, not public).
    review_labels_url = None
    if tv_reel_meta is not None:
        try:
            review_labels_url = build_review_label_track(
                tracks_field_df=tracks_df,
                identity_by_track=identity_by_track,
                projector=projector,
                game_id=game_id,
                field_length_m=field_cal.length_m,
                field_width_m=field_cal.width_m,
                tv_meta=tv_reel_meta,
                events=game.events,
                clock_to_video=clock_to_video,
                upload=not skip_upload,
                team_of_track=team_of_track,
                tracklet_of_track=tracklet_of_track,
                # Coach-rejected tracklets (override = None / "__not_player__"
                # sentinels) are decided, not pending — no "?" chips for them.
                rejected_tracklets={
                    int(k) for k, v in (game.identity_overrides or {}).items()
                    if (not v or str(v).startswith("__")) and str(k).lstrip("-").isdigit()
                },
            )
        except Exception as e:
            log.warning("Review label track failed: %s", e)

    # 7d. Per-tracklet records + thumbnails for the coach IdentityFixView. The
    # records let the PWA list each stitched tracklet (worst-confidence first)
    # with its current player + a representative crop so the coach can fix swaps;
    # corrections come back as `identityOverrides` on the game doc.
    tracklet_records = _build_tracklet_index(tracks_df, tracklet_of_track, assignments, fps_sampled,
                                             field_cal.length_m, field_cal.width_m,
                                             track_jersey_samples=track_jersey_samples,
                                             our_color_hex=_our_color(game),
                                             opp_color_hex=game.away_color)

    # 7c-bis. VLM jersey-number identity DRAFTS. Runs HERE, inside the run, so
    # drafts key by THIS run's tracklet ids — the exact ids the analytics doc +
    # PWA FIX-IDS use (a standalone tool can't reproduce them). Reads the number
    # off number-optimized crops, writes suggestions to game.identityDrafts;
    # never auto-applies. ON by default: it lifts naming from ~4.6% of tracked
    # time to ~35% for ~9 min of a ~2 h run, and jersey numbers are the only
    # signal that tells identically-dressed children apart. Needs the raw video
    # and an Opus token; the try/except below keeps a missing token from costing
    # the run, since everything else is already computed by this point.
    _vlm_on = config.VLM_IDENTITY if vlm_identity is None else vlm_identity
    if _vlm_on and tracklet_records and not stats_only:
        try:
            from tracking.vlm_identity import generate_drafts
            _cur = {int(r["tracklet_id"]): (r.get("player_id"), float(r.get("minutes") or 0.0))
                    for r in tracklet_records}
            _vlm_team: dict[int, str] = {}
            _drafts = generate_drafts(
                tracks_df=tracks_df, tracklet_of_track=tracklet_of_track,
                team_of_track=team_of_track, roster=roster, game=game,
                video_path=str(video_path), field_length_m=field_cal.length_m,
                field_width_m=field_cal.width_m, current_of_tl=_cur,
                model=config.VLM_IDENTITY_MODEL, min_conf=config.VLM_IDENTITY_MIN_CONF,
                max_tracklets=config.VLM_IDENTITY_MAX_TRACKLETS,
                # Camera ground position, so crop choice can prefer frames where
                # the player's back is turned. Read from the calibration rather
                # than assumed: on this field the camera sits BEYOND the far
                # touchline (y=34.6 of a 30 m pitch), so guessing the sideline
                # would invert the test.
                cam_xy=_camera_ground_xy(field_cal),
                min_digit_px=config.VLM_MIN_DIGIT_PX, min_away=config.VLM_MIN_AWAY,
                dt=(1.0 / fps_sampled if fps_sampled else 0.1),
                log_fn=lambda m: log.info("%s", m), team_out=_vlm_team)
            firestore_io.write_identity_drafts(game_id, _drafts)
            log.info("  -> VLM identity: wrote %d draft(s) to game.identityDrafts", len(_drafts))
            # VLM team-read prune: the VLM reliably tags each tracklet's team by
            # kit colour. Drop UNASSIGNED tracklets it calls opponent/other from
            # the review list (the coarse-pixel prune misses washed opponents;
            # the VLM read catches them). Coach-decided tracklets stay visible.
            if _vlm_team:
                _kept, _dropped = [], 0
                for _r in tracklet_records:
                    _t = _vlm_team.get(int(_r["tracklet_id"]))
                    if not _r.get("player_id") and _t in ("opponent", "other"):
                        _dropped += 1
                        continue
                    _kept.append(_r)
                if _dropped:
                    log.info("  -> VLM team-read pruned %d opponent/coach tracklet(s) from review list",
                             _dropped)
                tracklet_records = _kept
        except Exception as e:
            log.warning("VLM identity drafts failed (non-fatal): %s", e)

    if tracklet_records:
        try:
            thumbs = generate_tracklet_thumbnails(
                tracks_df=tracks_df,
                tracklet_of_track=tracklet_of_track,
                tracklet_records=tracklet_records,
                video_path=str(video_path),
                game_id=game_id,
                upload=not skip_upload,
            )
            for r in tracklet_records:
                if r["tracklet_id"] in thumbs:
                    r["thumb_url"] = thumbs[r["tracklet_id"]]
        except Exception as e:
            log.warning("Tracklet thumbnails failed: %s", e)

    analytics = {
        "version": config.ANALYTICS_DOC_VERSION,
        "field_name": field_name,
        "video_meta": meta,
        "identity_assignments": [asdict(a) for a in assignments],
        # Per-tracklet review records for the coach IdentityFixView (Phase 3).
        "tracklets": tracklet_records,
        "player_stats": [_player_stat_to_dict(s) for s in player_stats],
        "formation_snapshots": [
            {
                **asdict(f),
                "avg_positions": {k: list(v) for k, v in f.avg_positions.items()},
                "coach_positions_norm": {k: list(v) for k, v in f.coach_positions_norm.items()},
            }
            for f in formation_snaps
        ],
        "team_time_series": asdict(team_ts),
        "clip_count": len(clips),
        "tv_reel": _tv_meta_to_dict(tv_reel_meta),
        "auto_highlights": _tv_meta_to_dict(auto_hl_meta),
        # Convenience top-level URLs the PWA can read without diving into
        # the nested meta dicts above.
        "tv_reel_url": (tv_reel_meta.r2_url if tv_reel_meta else None),
        "auto_highlights_url": (auto_hl_meta.r2_url if auto_hl_meta else None),
        # Coach-only review overlay (3.7): name-chip keyframes for the reel.
        "review_labels_url": review_labels_url,
        # Team-centroid third occupancy (4.6) — no-ball possession proxy.
        "field_tilt": field_tilt,
        "sub_corrections": sub_corrections_echo,
        # See the stats-only branch: impossible spans excluded from every stat.
        "player_conflicts": player_conflicts,
        "tv_reel_duration_s": (tv_reel_meta.duration_s if tv_reel_meta else None),
        "auto_highlights_duration_s": (auto_hl_meta.duration_s if auto_hl_meta else None),
        # Per-event timeline for the on-screen scorebug / goal-popup overlay.
        "broadcast_events": events_index,
        # The scorebug is us/them-oriented, NOT physical home/away: "home" is
        # always us (left), "away" the opponent (right) — matching the live
        # in-game scorebug. home_color/away_color are already our/opp jersey
        # colors (GameSetup labels: "LASALLE STOMPERS JERSEY" -> home_color,
        # "OPPONENT JERSEY" -> away_color), so the names must follow the same
        # convention. Swapping names by is_home (the old behavior) paired the
        # opponent name with our score/color on away games.
        "home_name": "Stompers",
        "away_name": game.opponent or "OPP",
        "home_color": game.home_color,
        "away_color": game.away_color,
        "generated_at_ms": int(time.time() * 1000),
    }
    if board_orient_ambiguous:
        analytics["orientation_ambiguous_periods"] = board_orient_ambiguous
    firestore_io.write_analytics(game_id, _sanitize_json(analytics))

    # Public-safe slice on the game doc itself so parents can render the
    # broadcast video + scorebug without being able to read the rest of
    # the analytics subcollection. Firestore rules then lock analytics/
    # to coaches.
    public_fields: dict = {}
    # Public fields point at the AMBIENCE (stadium-audio) copies when present so
    # parents never hear the original audio; the coach analytics doc above keeps
    # the original-audio URLs for the dugout. Falls back to original if the swap
    # is disabled or failed.
    if tv_reel_meta and tv_reel_meta.r2_url:
        public_fields["videoFullGameUrl"] = public_tv_url or tv_reel_meta.r2_url
        public_fields["videoFullGameDurationS"] = float(tv_reel_meta.duration_s or 0.0)
    if auto_hl_meta and auto_hl_meta.r2_url:
        public_fields["videoHighlightsUrl"] = public_hl_url or auto_hl_meta.r2_url
        public_fields["videoHighlightsDurationS"] = float(auto_hl_meta.duration_s or 0.0)
    # Public overlay docs are NOT version-scoped, so only the canonical "v1" run may
    # write them — a shadow A/B run (ANALYTICS_DOC_VERSION=v1-shadow) must never
    # clobber the live public reel/broadcast docs.
    if (public_fields or events_index) and config.ANALYTICS_DOC_VERSION == "v1":
        # broadcastEvents (the big one) now lives in games/<id>/public/broadcast,
        # fetched on demand when a reel opens — keeps the game doc (pulled for
        # every game on dugout/public load) lean. Light overlay metadata stays
        # on the doc; the events index goes to the subcollection.
        public_fields["broadcastHomeName"] = analytics["home_name"]
        public_fields["broadcastAwayName"] = analytics["away_name"]
        public_fields["broadcastHomeColor"] = analytics["home_color"]
        public_fields["broadcastAwayColor"] = analytics["away_color"]
        firestore_io.set_public_reels(game_id, public_fields)
        firestore_io.set_public_broadcast_events(game_id, _sanitize_json(events_index))
        # Clear the legacy on-doc field so re-run docs converge to lean.
        firestore_io.clear_legacy_broadcast_events(game_id)

    # Clean up legacy clip docs from older pipeline runs that wrote the
    # tv_reel / auto_highlights records into the per-event clips/ collection.
    # They render as broken "· P 0' · —" rows in the PWA highlight list.
    # Skipped on shadow runs — touches the live game's clips/ collection.
    if config.ANALYTICS_DOC_VERSION == "v1":
        _purge_legacy_reel_clip_docs(game_id)
    log.info("Wrote analytics for game %s - %d players", game_id, len(player_stats))
    return analytics


# --- helpers -------------------------------------------------------------

# Modules whose content determines what Stage 2 produces. A cache written before
# any of these changed may be stale in a way no downstream stage can detect —
# the parquet looks perfectly well-formed either way. Deliberately NOT the whole
# repo: edits to stats/tv_view/identity don't invalidate a tracking pass.
# Every module whose code changes what Stage 2 WRITES. kit_vote.py earns its
# place because the kit votes are produced in the Stage-2 loop and cached beside
# the tracks: when the value anchors moved from the kit hex to a fitted
# threshold, the fingerprint stayed identical and the guard said nothing about a
# cache whose votes were built the old way — the very silence this guard was
# added to break.
_TRACKING_SOURCES = ("tracking.py", "tracking_field.py", "tracking_pitch.py",
                     "detection.py", "video.py", "calibration.py", "kit_vote.py")
# Config values that change Stage-2 output. Recorded so a cache taken at a
# different sample rate / tile count is never silently mixed with a fresh one.
_TRACKING_CONFIG_KEYS = ("SAMPLE_RATE", "DETECT_N_TILES", "DETECT_TILE_FOV_DEG",
                         "DETECT_CONFIDENCE", "TRACK_BUFFER_S", "CROP_W", "CROP_H",
                         "YOLO_MODEL", "TRACK_FIELD_SPACE", "TRACK_PITCH",
                         # Association thresholds + the lost-track rescue. These
                         # live in config.py, which is NOT in _TRACKING_SOURCES,
                         # so without them here a threshold sweep would change
                         # Stage-2 output while leaving the fingerprint identical
                         # and a stale cache would be reused in silence.
                         "TRACK_HIGH_THRESH", "TRACK_NEW_THRESH",
                         "TRACK_LOW_THRESH", "TRACK_RESCUE_LOST",
                         "TRACK_APPEARANCE", "TRACK_APPEARANCE_THRESH",
                         "TRACK_DROP_OPPONENTS", "TRACK_DROP_OFFFIELD",
                         # Inference resolution changes what the detector sees,
                         # so a cache taken at another imgsz is not comparable.
                         "DETECT_IMGSZ",
                         "TRACK_HEADING_WEIGHT", "TRACK_HEADING_MIN_SPEED_FRAC",
                         "TRACK_HEADING_CAP",
                         # Tag-don't-drop opponent pruning and the never-on-pitch
                         # threshold. Both change which tracks reach the cached
                         # parquet, so both must move the fingerprint.
                         # The association algorithm itself. This sat in config
                         # unused for months while BotSort was hardcoded, so a
                         # cache taken on one tracker must never be reused for
                         # another.
                         "TRACKER_TYPE",
                         "TRACK_TAG_OPPONENTS", "KIT_TAG_TRACK_MAJORITY",
                         "KIT_TAG_MIN_VOTES", "DROP_NEVER_ONFIELD",
                         "DROP_NEVER_OUTSIDE_FRAC", "DROP_NEVER_MIN_DETS")


def _tracking_fingerprint() -> dict:
    """Fingerprint of everything that determines Stage-2 output."""
    import hashlib
    h = hashlib.sha256()
    for name in _TRACKING_SOURCES:
        p = Path(__file__).with_name(name)
        if p.exists():
            h.update(p.read_bytes())
    return {
        "code_sha256": h.hexdigest()[:16],
        "config": {k: str(getattr(config, k, None)) for k in _TRACKING_CONFIG_KEYS},
        "written_at_ms": int(time.time() * 1000),
    }


def _write_tracks_provenance(path: Path) -> None:
    """Record what produced this checkpoint, next to it."""
    import json
    try:
        path.write_text(json.dumps(_tracking_fingerprint(), indent=2))
    except OSError as e:   # a missing sidecar only costs the staleness warning
        log.warning("Could not write cache provenance %s: %s", path.name, e)


def _check_tracks_provenance(path: Path, tracks_ckpt: Path) -> None:
    """Warn when a reused Stage-2 cache predates the current tracking code.

    This is the guard that was missing when the halftime id-collision fix
    (`_next_id` carried across the tracker reset) landed: every existing cache
    predated it, so months of offline measurement ran on data where ~40-57% of
    tracked time sat in ids that welded two different children together — and
    nothing in the pipeline said a word, because the checkpoint reuse test is
    just `tracks_ckpt.exists()`. Warn loudly rather than block: a stale cache is
    still the right input for a deliberate stats-only refresh.
    """
    import json
    cur = _tracking_fingerprint()
    if not path.exists():
        log.warning(
            "  !! cache %s has NO provenance sidecar — it predates provenance "
            "tracking and may have been produced by different tracking code. "
            "Re-track before trusting any measurement derived from it.",
            tracks_ckpt.name)
        return
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        log.warning("  !! cache provenance %s unreadable (%s); treating as stale",
                    path.name, e)
        return
    if old.get("code_sha256") != cur["code_sha256"]:
        log.warning(
            "  !! cache %s was produced by DIFFERENT tracking code "
            "(cache %s vs current %s). Downstream numbers may not reflect the "
            "current pipeline — re-track for a clean baseline.",
            tracks_ckpt.name, old.get("code_sha256"), cur["code_sha256"])
    drift = {k: (v, cur["config"].get(k))
             for k, v in (old.get("config") or {}).items()
             if cur["config"].get(k) != v}
    if drift:
        log.warning("  !! cache %s config drift: %s", tracks_ckpt.name,
                    ", ".join(f"{k}: {o} -> {n}" for k, (o, n) in sorted(drift.items())))


def _build_broadcast_events_index(
    game,
    roster,
    period_clock_to_video_time,
    tv_reel_segments: list[tuple[float, float]],
    auto_highlights_segments: list[tuple[float, float]],
    tag_suggestions: Optional[dict] = None,
) -> list[dict]:
    """Per-event timeline used by the PWA on-screen overlay layer.

    For each game event in time order, emit:
      - id, type, period, elapsed (clock seconds in the period)
      - playerId, playerName, jerseyNumber
      - assistPlayerId, assistPlayerName, assistJerseyNumber (when present)
      - videoTimeS:       seconds into the original source video
      - tvReelTimeS:      seconds into the rendered tv_reel mp4 (or None)
      - autoHighlightsTimeS: seconds into the auto_highlights mp4 (or None)
      - ourScoreAfter / oppScoreAfter: running team scores AFTER this event
      - team: 'us' | 'them' (for GOAL only)
      - suggestedZone / suggestedPressure: tag pre-fill from tracking (3.3),
        consumed by the PWA confirm queue; None when unavailable.
    """
    roster_by_id = {p.id: p for p in roster}

    def _name_pair(pid):
        if not pid:
            return (None, None)
        p = roster_by_id.get(pid)
        if not p:
            return (None, None)
        first = (p.name or "").split()[0] if p.name else None
        return (first, p.jersey_number)

    def _segments_to_reel_time(t: float, segs: list[tuple[float, float]]) -> Optional[float]:
        if not segs:
            return None
        acc = 0.0
        for (a, b) in segs:
            if a <= t <= b:
                return acc + (t - a)
            acc += max(0.0, b - a)
        return None

    ordered = sorted(game.events, key=lambda e: (e.period, e.elapsed, e.at))
    our_score = 0
    opp_score = 0
    out: list[dict] = []
    for ev in ordered:
        # Running score: GOAL increments us; OPP_GOAL increments them. Stay
        # tolerant to the few historical naming variants.
        et = (ev.type or "").upper()
        team = None
        if et in ("GOAL",):
            our_score += 1
            team = "us"
        elif et in ("OPP_GOAL", "OPPONENT_GOAL", "GOAL_AGAINST"):
            opp_score += 1
            team = "them"

        try:
            video_t = float(period_clock_to_video_time(ev.period, ev.elapsed))
        except Exception:
            video_t = -1.0
        if video_t < 0:
            video_t = 0.0

        first, num = _name_pair(ev.player_id)
        assist_pid = (
            ev.extras.get("assistPlayerId")
            or ev.extras.get("assistId")
            or ev.extras.get("assist")
        )
        a_first, a_num = _name_pair(assist_pid)
        # SUB events store the player coming ON in `subOnPlayerId` and the
        # player going OFF in `playerId` (see logSubEvent in the PWA). Keep the
        # older inPlayerId/outPlayerId fallbacks for any legacy events.
        sub_in_pid = (
            ev.extras.get("subOnPlayerId")
            or ev.extras.get("inPlayerId")
            or ev.extras.get("subInId")
        )
        sub_out_pid = ev.extras.get("outPlayerId") or ev.extras.get("subOutId")
        if et in ("SUB", "SUBSTITUTION") and not sub_out_pid:
            sub_out_pid = ev.player_id
        in_first, in_num = _name_pair(sub_in_pid)
        out_first, out_num = _name_pair(sub_out_pid)

        sug = (tag_suggestions or {}).get(ev.id, {})
        out.append({
            "id": ev.id,
            "type": et,
            "period": ev.period,
            "elapsed": ev.elapsed,
            "playerId": ev.player_id,
            "playerFirstName": first,
            "jerseyNumber": num,
            "assistPlayerId": assist_pid,
            "assistFirstName": a_first,
            "assistJerseyNumber": a_num,
            "inPlayerId": sub_in_pid,
            "inFirstName": in_first,
            "inJerseyNumber": in_num,
            "outPlayerId": sub_out_pid,
            "outFirstName": out_first,
            "outJerseyNumber": out_num,
            "videoTimeS": video_t,
            "tvReelTimeS": _segments_to_reel_time(video_t, tv_reel_segments),
            "autoHighlightsTimeS": _segments_to_reel_time(video_t, auto_highlights_segments),
            "ourScoreAfter": our_score,
            "oppScoreAfter": opp_score,
            "team": team,
            "suggestedZone": sug.get("zone"),
            "suggestedPressure": sug.get("pressure"),
        })
    return out


# Tag pre-fill scope (Phase 3.4 trimmed sets — mirror the PWA constants):
# zone only where location is the insight; pressure only on decision events.
_SUGGEST_ZONE_TYPES = {"GOAL", "SHOT_ON", "SHOT_OFF", "TURNOVER", "BALL_WIN"}
_SUGGEST_PRESSURE_TYPES = {"KEY_PASS", "GIVE_GO", "GATES", "SHOT_ON", "SHOT_OFF", "TURNOVER"}
# Shooting events: the action moment is the player's DEEPEST attacking second
# in the window, not the centroid-nearest one — after a shot/goal everyone
# regroups at the kickoff circle/goal kick, which IS the centroid, so the
# centroid pick lands on the restart huddle (measured: shots suggested M-C).
_SUGGEST_ATTACK_DEPTH_TYPES = {"GOAL", "SHOT_ON", "SHOT_OFF"}


def _field_to_zone(x: float, y: float, flip_d, flip_l, L: float, W: float):
    """Inverse of identity_assign._zone_center: field meters -> coach 3x3 zone
    id ('A-C', 'D-L', ...) through the period's resolved board orientation.
    Returns None when the orientation is unknown (no board that period)."""
    if flip_d is None or flip_l is None:
        return None
    depth = min(max(x / max(L, 1e-9), 0.0), 1.0)
    lat = min(max(y / max(W, 1e-9), 0.0), 1.0)
    d_own = (1.0 - depth) if flip_d else depth   # depth fraction from OUR goal
    la = (1.0 - lat) if flip_l else lat          # lateral fraction left->right
    band = "D" if d_own < 1.0 / 3 else ("M" if d_own < 2.0 / 3 else "A")
    side = "L" if la < 1.0 / 3 else ("C" if la < 2.0 / 3 else "R")
    return f"{band}-{side}"


def _build_tag_suggestions(
    game,
    tracks_df,
    identity_by_track: dict[int, str],
    team_of_track: dict[int, int],
    period_clock_to_video_time,
    board_flips: dict,
    field_length_m: float,
    field_width_m: float,
) -> dict[str, dict]:
    """eventId -> {"zone": str|None, "pressure": 'open'|'pressure'|None}.

    The action moment is picked the same way the identity event-votes do: the
    coach logs late, so search [-ASSIGN_EVENT_BEFORE_S, +ASSIGN_EVENT_AFTER_S]
    around the logged clock and take the second where the assigned player sits
    closest to the team centroid (U10 swarm ~= the ball). Zone = the player's
    position at that second mapped through the period's resolved board
    orientation; pressure = nearest opponent within SUGGEST_PRESSURE_RADIUS_M.
    Events without an assigned+tracked player get no suggestion — by design,
    an absurd suggestion would mean a misassigned identity (free FIX IDS lead).
    """
    if tracks_df is None or tracks_df.empty or not {"x_m", "y_m"}.issubset(tracks_df.columns):
        return {}
    player_tracks: dict[str, set[int]] = {}
    for tid, pid in identity_by_track.items():
        player_tracks.setdefault(pid, set()).add(int(tid))
    our_tracks = {int(t) for t, tm in team_of_track.items() if tm == 0}
    opp_tracks = {int(t) for t, tm in team_of_track.items() if tm == 1}
    df = tracks_df[["track_id", "time_s", "x_m", "y_m"]].copy()
    df["_sec"] = df["time_s"].astype(int)
    our_df = df[df["track_id"].isin(our_tracks)]
    opp_df = df[df["track_id"].isin(opp_tracks)]

    out: dict[str, dict] = {}
    for ev in game.events:
        et = (ev.type or "").upper()
        want_zone = et in _SUGGEST_ZONE_TYPES
        want_pressure = et in _SUGGEST_PRESSURE_TYPES
        if not (want_zone or want_pressure):
            continue
        pid = ev.player_id
        tids = player_tracks.get(pid) if pid else None
        if not tids:
            continue
        try:
            t = float(period_clock_to_video_time(ev.period, ev.elapsed))
        except Exception:
            continue
        w0 = t - config.ASSIGN_EVENT_BEFORE_S
        w1 = t + config.ASSIGN_EVENT_AFTER_S
        pdf = df[(df["track_id"].isin(tids)) & (df["time_s"] >= w0) & (df["time_s"] <= w1)]
        if pdf.empty:
            continue
        ppos = pdf.groupby("_sec")[["x_m", "y_m"]].median()
        fd, fl = board_flips.get(int(ev.period or 1), (None, None))

        best_sec = None
        if et in _SUGGEST_ATTACK_DEPTH_TYPES and fd is not None:
            # Shot/goal: deepest attacking second (see _SUGGEST_ATTACK_DEPTH_TYPES).
            depth = ppos["x_m"] / max(field_length_m, 1e-9)
            d_own = (1.0 - depth) if fd else depth
            best_sec = int(d_own.idxmax())
        else:
            # Action second = the player's best centroid-proximity moment
            # (U10 swarm ≈ the ball; turnovers/ball-wins happen in the scrum).
            wour = our_df[(our_df["time_s"] >= w0) & (our_df["time_s"] <= w1)]
            cent = wour.groupby("_sec")[["x_m", "y_m"]].median()
            best_d2 = None
            for sec, row in ppos.iterrows():
                if sec not in cent.index:
                    continue
                dx = float(row["x_m"]) - float(cent.loc[sec, "x_m"])
                dy = float(row["y_m"]) - float(cent.loc[sec, "y_m"])
                d2 = dx * dx + dy * dy
                if best_d2 is None or d2 < best_d2:
                    best_sec, best_d2 = sec, d2
        if best_sec is None:
            # No team context that window — fall back to the player's median
            # second nearest the logged time.
            best_sec = min(ppos.index, key=lambda s: abs(s - t))
        px = float(ppos.loc[best_sec, "x_m"])
        py = float(ppos.loc[best_sec, "y_m"])

        sug: dict = {}
        if want_zone:
            sug["zone"] = _field_to_zone(px, py, fd, fl, field_length_m, field_width_m)
        if want_pressure:
            osec = opp_df[opp_df["_sec"] == best_sec]
            if not osec.empty:
                opos = osec.groupby("track_id")[["x_m", "y_m"]].median()
                dmin = float(np.sqrt(((opos["x_m"] - px) ** 2 + (opos["y_m"] - py) ** 2).min()))
                sug["pressure"] = "pressure" if dmin <= config.SUGGEST_PRESSURE_RADIUS_M else "open"
            else:
                # Zero opponent detections that second = missing data, not
                # evidence of being open — suggest nothing.
                sug["pressure"] = None
        if sug.get("zone") or sug.get("pressure"):
            out[ev.id] = sug
    return out


def _purge_legacy_reel_clip_docs(game_id: str) -> None:
    """Older pipeline runs wrote tv_reel + auto_highlights records into the
    per-event `clips/` subcollection. They show up as broken rows in the
    PWA. Delete them defensively on every run."""
    try:
        coll = firestore_io._team_doc().collection("games").document(game_id).collection("clips")
        for legacy_id in ("tv_reel", "auto_highlights"):
            try:
                coll.document(legacy_id).delete()
            except Exception:
                pass
    except Exception as e:
        log.debug("Legacy reel clip purge skipped: %s", e)


def _ensure_local_video(url: str, game_id: str) -> Path:
    if url.startswith("file://"):
        return Path(url.replace("file://", ""))
    if not url.startswith("http"):
        return Path(url)
    ext = Path(url.split("?")[0]).suffix or ".mp4"
    dest = config.CACHE_DIR / f"{game_id}{ext}"
    return firestore_io.download_video(url, dest)


def _camera_ground_xy(field_cal) -> Optional[tuple[float, float]]:
    """Where the camera stands, in field metres — or None on the planar model.

    The sphere solve puts the camera at the origin of its own frame, so the
    similarity that maps camera-frame ground points into field coordinates sends
    (0, 0) to exactly (tx, ty). Used to work out which way a player is facing:
    the number is on their back, so it is only readable while they run away from
    THIS point. Worth deriving rather than assuming a sideline — on W8 the rig
    sits at y=34.6 m on a 30 m-wide pitch, i.e. beyond the far touchline, and an
    assumed y=0 would get the sign backwards on every tracklet.
    """
    s = getattr(field_cal, "sphere", None)
    if not s:
        return None
    try:
        return (float(s["tx"]), float(s["ty"]))
    except (KeyError, TypeError, ValueError):
        return None


def _our_color(game) -> str:
    # home_color is ALWAYS our jersey — GameSetup labels it "LASALLE STOMPERS
    # JERSEY" (away_color is the opponent's), independent of is_home. The old
    # logic returned away_color on away games, feeding the OPPONENT's jersey
    # color to the team classifier and inverting the team split.
    return game.home_color or game.away_color or "#A3E635"


def _flip_team_map(team_of_track, identity_by_track):
    return {pid: team_of_track[tid] for tid, pid in identity_by_track.items() if tid in team_of_track}


def _periods_seconds(game, video_duration_s, clock_to_video) -> list[tuple[float, float]]:
    # Now identical to half_windows() — kept as a thin wrapper for back-compat.
    return half_windows(game, video_duration_s)


def _attack_direction(game, tracks_df, identity_by_track, field_length_m) -> dict[int, bool]:
    if "x_m" not in tracks_df.columns or not game.gk_player_id:
        return {1: True, 2: False}
    player_to_track = {pid: tid for tid, pid in identity_by_track.items()}
    gk_track = player_to_track.get(game.gk_player_id)
    if gk_track is None:
        return {1: True, 2: False}
    sub = tracks_df[tracks_df["track_id"] == gk_track]
    if sub.empty:
        return {1: True, 2: False}
    attack_right_p1 = float(sub["x_m"].median()) < field_length_m / 2.0
    return {1: attack_right_p1, 2: not attack_right_p1}


def _build_tracklet_index(tracks_df, tracklet_of_track, assignments, fps_sampled: float,
                          field_length_m: float, field_width_m: float,
                          track_jersey_samples: Optional[dict] = None,
                          our_color_hex: Optional[str] = None,
                          opp_color_hex: Optional[str] = None) -> list[dict]:
    """Aggregate the per-track identity assignments into per-tracklet records for
    the coach IdentityFixView. Our-team tracklets only (opponent tracks carry no
    `breakdown.tracklet`). Sorted worst-confidence first so the coach reviews the
    shakiest tracklets at the top. `thumb_url` is filled in later (Phase 2).

    `minutes` is ACTUAL tracked coverage (detection count ÷ sample rate), NOT the
    span end-start: stitched fragments can span the whole game while holding only
    a few seconds of real detections, so span would let pure noise through the
    review filter."""
    df = tracks_df.copy()
    df["tracklet"] = df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))
    grp = df.groupby("tracklet")["time_s"]
    spans = grp.agg(["min", "max"])
    counts = grp.size()
    fps = fps_sampled if fps_sampled and fps_sampled > 0 else 1.0

    # Per-tracklet fraction of detections that land ON the pitch (within a small
    # margin of the lines). Refs/coaches/spectators next to the touchline camera
    # get mis-classified as our team by jersey color but sit OFF the pitch in
    # field coords — a low on-pitch fraction lets us drop them from the review list.
    onpitch_frac: dict[int, float] = {}
    if {"x_m", "y_m"}.issubset(df.columns):
        m = config.TRACKLET_REVIEW_ONPITCH_MARGIN_M
        inb = ((df["x_m"] >= -m) & (df["x_m"] <= field_length_m + m)
               & (df["y_m"] >= -m) & (df["y_m"] <= field_width_m + m))
        onpitch_frac = df.assign(_inb=inb).groupby("tracklet")["_inb"].mean().to_dict()
    # Coarse OPPONENT-colour prune: the fine-hue team classifier mis-teams
    # opponents into our team (66% of the W8 review list was opp/coach). When we
    # can CONFIDENTLY bucket a tracklet's jersey to the OPPONENT's coarse colour
    # family (e.g. blue), drop it from the review list. Conservative: a washed /
    # achromatic / unsure tracklet returns None → stays (never hide a real player
    # we're unsure about). Only prunes UNASSIGNED tracklets (an assigned/coach
    # tracklet is a decision to keep visible).
    opp_family = None
    tl_family: dict[int, str] = {}
    if opp_color_hex and track_jersey_samples:
        from .team_color import color_family, tracklet_family
        opp_family = color_family(hex=opp_color_hex)
        our_family = color_family(hex=our_color_hex) if our_color_hex else None
        if opp_family and opp_family != our_family:   # only if the kits differ coarsely
            members: dict[int, list[int]] = {}
            for _t, _r in tracklet_of_track.items():
                members.setdefault(int(_r), []).append(int(_t))
            for _tl in set(tracklet_of_track.values()):
                _samps = []
                for _m in members.get(int(_tl), [int(_tl)]):
                    _samps.extend(track_jersey_samples.get(_m, []))
                _fam = tracklet_family(_samps)
                if _fam is not None:
                    tl_family[int(_tl)] = _fam

    by_tl: dict[int, object] = {}
    for a in assignments:
        tl = (a.breakdown or {}).get("tracklet")
        if tl is None:
            continue
        by_tl[int(tl)] = a  # member tracks share the tracklet-level assignment
    out: list[dict] = []
    for tl, a in by_tl.items():
        if tl not in spans.index:
            continue
        t0 = float(spans.loc[tl, "min"]); t1 = float(spans.loc[tl, "max"])
        minutes = float(counts.loc[tl]) / fps / 60.0  # real tracked coverage
        # Stitching leaves many tiny unmerged fragments that carry no meaningful
        # player-time. Only review tracklets that matter: those already assigned
        # to a player (could be a wrong swap to fix) OR sizeable unassigned ones
        # worth rescuing. Drop the rest so the list is a few dozen, not ~400.
        # Assigned tracklets always show; unassigned ones must be substantial AND
        # mostly on the pitch (filters out sideline refs/coaches/spectators).
        if not a.player_id:
            if minutes < config.TRACKLET_REVIEW_MIN_MINUTES:
                continue
            if onpitch_frac and onpitch_frac.get(tl, 1.0) < config.TRACKLET_REVIEW_ONPITCH_FRAC:
                continue  # mostly off-pitch → not one of our players
            if opp_family and tl_family.get(tl) == opp_family:
                continue  # confidently the opponent's kit colour → not ours
        out.append({
            "tracklet_id": int(tl),
            "player_id": a.player_id,
            "confidence": round(float(a.confidence), 3),
            "status": a.status,
            "minutes": round(minutes, 1),
            "t_start_s": round(t0, 1),
            "t_end_s": round(t1, 1),
            "thumb_url": None,
        })
    out.sort(key=lambda r: (r["confidence"], -r["minutes"]))
    return out


def _sanitize_json(obj):
    """Recursively replace NaN/inf with None so Firestore accepts it."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    if isinstance(obj, np.generic):
        return _sanitize_json(obj.item())
    return obj


def _tv_meta_to_dict(meta) -> dict | None:
    """asdict(TvViewMeta) but with segments flattened to a list of dicts.

    Firestore disallows arrays inside arrays; the dataclass stores
    segments as list[tuple[float, float]] which sanitizes to nested
    lists \u2192 \"Property tv_reel contains an invalid nested entity.\" Map
    each segment to {\"start_s\": a, \"end_s\": b} instead.
    """
    if meta is None:
        return None
    d = asdict(meta)
    segs = d.get("segments") or []
    d["segments"] = [
        {"start_s": float(a), "end_s": float(b)} for a, b in segs
    ]
    return d

def _player_stat_to_dict(s) -> dict:
    """asdict(PlayerStats) but with heatmap_grid flattened.

    PlayerStats.heatmap_grid is list[list[int]] (12×8). Firestore disallows
    arrays inside arrays, so we flatten row-major and record shape so any
    consumer can rebuild it. No PWA code currently reads heatmap_grid.
    """
    d = asdict(s)
    grid = d.get("heatmap_grid") or []
    rows = len(grid)
    cols = len(grid[0]) if rows and isinstance(grid[0], (list, tuple)) else 0
    flat: list[int] = []
    for row in grid:
        flat.extend(int(v) for v in row)
    d["heatmap_grid"] = flat
    d["heatmap_grid_rows"] = rows
    d["heatmap_grid_cols"] = cols
    return d
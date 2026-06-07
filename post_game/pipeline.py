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
from .tv_view import extract_auto_highlights, render_tv_reel, tv_reel_meta_from_existing
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

    # 1c. Build the FieldProjector + the multi-tile detection aims. Three
    # 75° tiles spread across the pitch cover the ~170° horizontal angle
    # that one perspective crop can't (X5 on a 16ft pole 3m behind the
    # near sideline). Each tile is processed independently by YOLO and
    # detections are then merged in field space.
    projector = FieldProjector(field_cal)
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

    def _new_tracker() -> Tracker:
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

    if tracks_ckpt.exists() and jersey_ckpt.exists():
        import pandas as pd
        log.info("Stage 2/6: loading cached tracks from %s", tracks_ckpt)
        tracks_df = pd.read_parquet(tracks_ckpt)
        with np.load(jersey_ckpt, allow_pickle=True) as nz:
            track_jersey_samples = {
                int(k): list(nz[k]) for k in nz.files
            }
        # Per-track Re-ID embeddings (sidecar; absent on pre-embedding caches →
        # stitching falls back to jersey-HSV).
        track_embeddings: dict[int, np.ndarray] = {}
        if emb_ckpt.exists():
            with np.load(emb_ckpt, allow_pickle=True) as nz:
                track_embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}
            log.info("  -> loaded Re-ID embeddings for %d tracks", len(track_embeddings))
    else:
        detector = Detector()
        tracker = _new_tracker()
        current_half = 1

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
            if current_half == 1 and sample.time_s >= h1_end_s:
                tracker = _new_tracker()
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

        tracks_df = to_dataframe(all_tracks, fps=fps_sampled)
        # Persist before any downstream filter touches the data — so a bug in
        # filtering / identity / stats doesn't cost another detection pass.
        tracks_df.to_parquet(tracks_ckpt)
        np.savez(
            jersey_ckpt,
            **{str(k): np.array(v, dtype=object) for k, v in track_jersey_samples.items()},
        )
        if track_embeddings:
            np.savez(emb_ckpt, **{str(k): v for k, v in track_embeddings.items()})
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

        # 3c. TOP-N PER FRAME — soccer has ≤ 16 people on the field (7v7 + ref
        # + occasional coach for injury). Cap at 20 per frame ranked by
        # (track lifetime × detection confidence) so established tracks beat
        # one-off background detections that survived the off-field filter.
        if not tracks_df.empty and "track_id" in tracks_df.columns:
            lifetime = tracks_df.groupby("track_id").size().rename("track_lifetime")
            tracks_df = tracks_df.merge(lifetime, on="track_id")
            conf_col = "conf" if "conf" in tracks_df.columns else None
            score = tracks_df["track_lifetime"].astype(float)
            if conf_col:
                score = score * tracks_df[conf_col].astype(float).clip(lower=0.1)
            tracks_df["_rank_score"] = score
            ranked = tracks_df.sort_values(["frame", "_rank_score"], ascending=[True, False])
            top_n = ranked.groupby("frame", group_keys=False).head(20)
            dropped_topn = len(tracks_df) - len(top_n)
            tracks_df = top_n.drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True)
        else:
            dropped_topn = 0

        log.info("  -> filters: dropped %d off-field, %d below top-20/frame; %d kept",
                 dropped_off, dropped_topn, len(tracks_df))

    # 4. Team classification
    log.info("Stage 4/6: team classification...")
    our_color = _our_color(game)
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

    # 6. Stats + Formation + GK positioning
    log.info("Stage 6/6: stats, formation, GK positioning...")
    attack_dir = _attack_direction(game, tracks_df, identity_by_track, field_cal.length_m)
    # Coach-logged minutes per player (ground truth) = on-field intervals
    # (lineup + subs) clipped to the play windows. Used for minutes_played.
    _onf = _onfield_intervals(game.starting_lineup, game.events, clock_to_video)
    played_minutes: dict[str, float] = {}
    for _pid, _ivs in _onf.items():
        _tot = 0.0
        for (_a, _b) in _ivs:
            for (_pa, _pb) in play_windows:
                _lo, _hi = max(_a, _pa), min(_b, _pb)
                if _hi > _lo:
                    _tot += _hi - _lo
        played_minutes[str(_pid)] = _tot / 60.0
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
    )
    formation_snaps, team_ts = compute_formation(
        tracks_df, identity_by_track, team_of_player,
        periods=_periods_seconds(game, meta["duration_s"], clock_to_video),
        gk_player_id=game.gk_player_id,
        coach_events=game.events,
        starting_lineup=game.starting_lineup,
    )
    # GK positioning analysis removed — not used in the film room.

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

    # 7c. Build per-event broadcast index. Used by the PWA overlay layer
    # to draw a live scorebug + goal/sub popups while the user watches the
    # tv_reel or auto_highlights mp4. Done here (post-pipeline) because we
    # need clock_to_video + the reel segment list before we can map each
    # source-video event time into reel-relative time.
    events_index = _build_broadcast_events_index(
        game=game,
        roster=roster,
        period_clock_to_video_time=clock_to_video,
        tv_reel_segments=(tv_reel_meta.segments if tv_reel_meta else play_windows),
        auto_highlights_segments=(auto_hl_meta.segments if auto_hl_meta else []),
    )

    # 7d. Per-tracklet records + thumbnails for the coach IdentityFixView. The
    # records let the PWA list each stitched tracklet (worst-confidence first)
    # with its current player + a representative crop so the coach can fix swaps;
    # corrections come back as `identityOverrides` on the game doc.
    tracklet_records = _build_tracklet_index(tracks_df, tracklet_of_track, assignments, fps_sampled,
                                             field_cal.length_m, field_cal.width_m)
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
    firestore_io.write_analytics(game_id, _sanitize_json(analytics))

    # Public-safe slice on the game doc itself so parents can render the
    # broadcast video + scorebug without being able to read the rest of
    # the analytics subcollection. Firestore rules then lock analytics/
    # to coaches.
    public_fields: dict = {}
    if tv_reel_meta and tv_reel_meta.r2_url:
        public_fields["videoFullGameUrl"] = tv_reel_meta.r2_url
        public_fields["videoFullGameDurationS"] = float(tv_reel_meta.duration_s or 0.0)
    if auto_hl_meta and auto_hl_meta.r2_url:
        public_fields["videoHighlightsUrl"] = auto_hl_meta.r2_url
        public_fields["videoHighlightsDurationS"] = float(auto_hl_meta.duration_s or 0.0)
    if public_fields or events_index:
        public_fields["broadcastEvents"] = _sanitize_json(events_index)
        public_fields["broadcastHomeName"] = analytics["home_name"]
        public_fields["broadcastAwayName"] = analytics["away_name"]
        public_fields["broadcastHomeColor"] = analytics["home_color"]
        public_fields["broadcastAwayColor"] = analytics["away_color"]
        firestore_io.set_public_reels(game_id, public_fields)

    # Clean up legacy clip docs from older pipeline runs that wrote the
    # tv_reel / auto_highlights records into the per-event clips/ collection.
    # They render as broken "· P 0' · —" rows in the PWA highlight list.
    _purge_legacy_reel_clip_docs(game_id)
    log.info("Wrote analytics for game %s - %d players", game_id, len(player_stats))
    return analytics


# --- helpers -------------------------------------------------------------

def _build_broadcast_events_index(
    game,
    roster,
    period_clock_to_video_time,
    tv_reel_segments: list[tuple[float, float]],
    auto_highlights_segments: list[tuple[float, float]],
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
        })
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
                          field_length_m: float, field_width_m: float) -> list[dict]:
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
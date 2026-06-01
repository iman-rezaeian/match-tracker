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
from .calibration import aim_from_calibration, pixel_to_field_batch
from .detection import Detector
from .formation import compute_formation
from .gk_positioning import compute_gk_positions
from .highlights import extract_clips
from .identity import assign_identities, half_windows, period_clock_to_video_time_factory
from .tv_view import extract_auto_highlights, render_tv_reel
from .stats import compute_player_stats
from .team_classifier import classify_tracks, sample_jersey_hsv
from .tracking import Tracker, TrackedDetection, to_dataframe
from .video import crop_bbox_to_equirect, iter_frames, open_video

log = logging.getLogger(__name__)


def run(game_id: str, field_name: str | None = None, tv_view: bool = False) -> dict:
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
        # Launch the local browser-based calibration tool, wait for the user
        # to mark the 4 corners and save, then re-read the calibration.
        from . import calibrate_local
        log.info("No calibration found. Launching browser calibration UI...")
        calibrate_local.calibrate_in_browser(game_id)
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
    # field). Computed from the calibration corners — single aim for the
    # whole game, no panning. This eliminates the "two fields" problem at
    # detection time, so the model never sees the other pitch.
    aim_lon, aim_lat, aim_fov = aim_from_calibration(
        field_cal.src_points_px, eq_w, eq_h,
    )
    log.info("Field aim: lon=%.1f° lat=%.1f° fov=%.1f°", aim_lon, aim_lat, aim_fov)

    # 2. Detection + tracking on perspective crops
    # Compute play windows (1st half, 2nd half) so we skip warmup/halftime/post.
    play_windows = half_windows(game, meta["duration_s"])
    log.info("Play windows: 1H=[%.1fs - %.1fs] 2H=[%.1fs - %.1fs] (halftime + dead time skipped)",
             play_windows[0][0], play_windows[0][1],
             play_windows[1][0], play_windows[1][1])
    h1_end_s = play_windows[0][1]

    detector = Detector()
    fps_sampled = meta["fps"] / config.SAMPLE_RATE

    def _new_tracker() -> Tracker:
        return Tracker(
            frame_rate=max(1, int(round(fps_sampled))),
            track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
        )

    tracker = _new_tracker()
    current_half = 1

    all_tracks: list[TrackedDetection] = []
    track_jersey_samples: dict[int, list[np.ndarray]] = {}

    log.info("Stage 2/6: detection + tracking...")
    for sample in iter_frames(
        str(video_path),
        sample_rate=config.SAMPLE_RATE,
        aim=(aim_lon, aim_lat, aim_fov),
        windows=play_windows,
    ):
        # Reset tracker at halftime — track IDs must not bridge halves
        # (players swap ends, teams swap sides, anyone could be off the field).
        if current_half == 1 and sample.time_s >= h1_end_s:
            tracker = _new_tracker()
            current_half = 2
        det_lists = detector.detect_persons([sample.crop])
        dets = det_lists[0] if det_lists else []
        for d in dets:
            d.frame_index = sample.frame_index
            d.bbox_eq = crop_bbox_to_equirect(
                d.bbox_crop,
                sample.crop_lon_deg, sample.crop_lat_deg, sample.crop_fov_deg,
                eq_w, eq_h, config.CROP_W, config.CROP_H,
            )
        tracked = tracker.update(sample.crop, dets, time_s=sample.time_s)
        for t in tracked:
            all_tracks.append(t)
            if t.frame_index % (config.SAMPLE_RATE * 10) == 0:
                hsv = sample_jersey_hsv(sample.crop, t.bbox_crop)
                if len(hsv) > 0:
                    track_jersey_samples.setdefault(t.track_id, []).append(hsv)

    tracks_df = to_dataframe(all_tracks, fps=fps_sampled)
    log.info("  -> %d detections across %d tracks",
             len(tracks_df), tracks_df["track_id"].nunique() if not tracks_df.empty else 0)

    # 3. Pixel (equirectangular) -> field meters
    H = np.array(field_cal.homography, dtype=np.float64)
    foot_px = tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy() if not tracks_df.empty else np.zeros((0, 2))
    xy = pixel_to_field_batch(H, foot_px)
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
            conf_col = "confidence" if "confidence" in tracks_df.columns else None
            score = tracks_df["track_lifetime"].astype(float)
            if conf_col:
                score = score * tracks_df[conf_col].astype(float).clip(lower=0.1)
            tracks_df["_rank_score"] = score
            ranked = tracks_df.sort_values(["frame_index", "_rank_score"], ascending=[True, False])
            top_n = ranked.groupby("frame_index", group_keys=False).head(20)
            dropped_topn = len(tracks_df) - len(top_n)
            tracks_df = top_n.drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True)
        else:
            dropped_topn = 0

        log.info("  -> filters: dropped %d off-field, %d below top-20/frame; %d kept",
                 dropped_off, dropped_topn, len(tracks_df))

    # 4. Team classification
    log.info("Stage 4/6: team classification...")
    team_of_track = classify_tracks(
        tracks_df, track_jersey_samples,
        our_home_color_hex=_our_color(game),
    )

    # 5. Identity
    log.info("Stage 5/6: identity assignment...")
    clock_to_video = period_clock_to_video_time_factory(game)
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

    # 6. Stats + Formation + GK positioning
    log.info("Stage 6/6: stats, formation, GK positioning...")
    attack_dir = _attack_direction(game, tracks_df, identity_by_track, field_cal.length_m)
    player_stats = compute_player_stats(
        tracks_field_df=tracks_df,
        identity_by_track=identity_by_track,
        field_length_m=field_cal.length_m,
        field_width_m=field_cal.width_m,
        fps_after_sample=fps_sampled,
        we_attack_right=attack_dir.get(1, True),
    )
    formation_snaps, team_ts = compute_formation(
        tracks_df, identity_by_track, team_of_player,
        periods=_periods_seconds(game, meta["duration_s"], clock_to_video),
        gk_player_id=game.gk_player_id,
    )
    gk_positions = compute_gk_positions(
        events=game.events,
        tracks_field_df=tracks_df,
        identity_by_track=identity_by_track,
        gk_player_id=game.gk_player_id,
        gk_changes=game.gk_changes,
        we_attack_right_in_period=attack_dir,
        period_clock_to_video_time=clock_to_video,
        field_length_m=field_cal.length_m,
        field_width_m=field_cal.width_m,
    )

    # 7. Highlight clips
    log.info("Stage 7/7: highlight clips...")
    try:
        clips = extract_clips(
            video_path=str(video_path),
            events=game.events,
            tracks_field_df=tracks_df,
            identity_by_track=identity_by_track,
            H=H,
            period_clock_to_video_time=clock_to_video,
            game_id=game_id,
            upload=True,
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
            tv_reel_meta = render_tv_reel(
                video_path=str(video_path),
                tracks_field_df=tracks_df,
                H=H,
                game_id=game_id,
                upload=True,
                play_windows=play_windows,
            )
        except Exception as e:
            log.warning("TV reel failed: %s", e)
        try:
            auto_hl_meta = extract_auto_highlights(
                video_path=str(video_path),
                events=game.events,
                tracks_field_df=tracks_df,
                H=H,
                period_clock_to_video_time=clock_to_video,
                game_id=game_id,
                upload=True,
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

    analytics = {
        "version": config.ANALYTICS_DOC_VERSION,
        "field_name": field_name,
        "video_meta": meta,
        "identity_assignments": [asdict(a) for a in assignments],
        "player_stats": [asdict(s) for s in player_stats],
        "formation_snapshots": [
            {**asdict(f), "avg_positions": {k: list(v) for k, v in f.avg_positions.items()}}
            for f in formation_snaps
        ],
        "team_time_series": asdict(team_ts),
        "gk_positions": [_gk_to_dict(g) for g in gk_positions],
        "clip_count": len(clips),
        "tv_reel": asdict(tv_reel_meta) if tv_reel_meta else None,
        "auto_highlights": asdict(auto_hl_meta) if auto_hl_meta else None,
        # Convenience top-level URLs the PWA can read without diving into
        # the nested meta dicts above.
        "tv_reel_url": (tv_reel_meta.r2_url if tv_reel_meta else None),
        "auto_highlights_url": (auto_hl_meta.r2_url if auto_hl_meta else None),
        "tv_reel_duration_s": (tv_reel_meta.duration_s if tv_reel_meta else None),
        "auto_highlights_duration_s": (auto_hl_meta.duration_s if auto_hl_meta else None),
        # Per-event timeline for the on-screen scorebug / goal-popup overlay.
        "broadcast_events": events_index,
        "home_name": ("Stompers" if game.is_home else (game.opponent or "OPP")),
        "away_name": ((game.opponent or "OPP") if game.is_home else "Stompers"),
        "home_color": game.home_color,
        "away_color": game.away_color,
        "generated_at_ms": int(time.time() * 1000),
    }
    firestore_io.write_analytics(game_id, _sanitize_json(analytics))
    # Clean up legacy clip docs from older pipeline runs that wrote the
    # tv_reel / auto_highlights records into the per-event clips/ collection.
    # They render as broken "· P 0' · —" rows in the PWA highlight list.
    _purge_legacy_reel_clip_docs(game_id)
    log.info("Wrote analytics for game %s - %d players, %d GK events",
             game_id, len(player_stats), len(gk_positions))
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
        sub_in_pid = ev.extras.get("inPlayerId") or ev.extras.get("subInId")
        sub_out_pid = ev.extras.get("outPlayerId") or ev.extras.get("subOutId")
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
    if game.is_home and game.home_color:
        return game.home_color
    if not game.is_home and game.away_color:
        return game.away_color
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


def _gk_to_dict(g) -> dict:
    d = asdict(g)
    d["gk_pos_m"] = list(g.gk_pos_m)
    if g.shooter_pos_m is not None:
        d["shooter_pos_m"] = list(g.shooter_pos_m)
    return d


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

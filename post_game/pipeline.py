"""End-to-end orchestrator for Tier A."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import config, firestore_io
from .calibration import pixel_to_field_batch
from .detection import Detector
from .formation import compute_formation
from .gk_positioning import compute_gk_positions
from .highlights import extract_clips
from .identity import assign_identities, period_clock_to_video_time_factory
from .stats import compute_player_stats
from .team_classifier import classify_tracks, sample_jersey_hsv
from .tracking import Tracker, TrackedDetection, to_dataframe
from .video import crop_bbox_to_equirect, iter_frames, open_video

log = logging.getLogger(__name__)


def run(game_id: str, field_name: str) -> dict:
    """Run the full Tier A pipeline on one game. Returns the analytics dict
    written to Firestore."""

    # 1. Inputs
    game = firestore_io.get_game(game_id)
    roster = firestore_io.get_roster()
    field_cal = firestore_io.get_field(field_name)
    if field_cal is None:
        raise RuntimeError(
            f"Field '{field_name}' not yet calibrated. "
            "Open the game in the coach app and use FIELD CALIBRATION."
        )
    if not game.video_url:
        raise RuntimeError(f"Game {game_id} has no videoUrl set.")

    video_path = _ensure_local_video(game.video_url, game_id)
    meta = open_video(str(video_path))
    eq_w, eq_h = meta["width"], meta["height"]
    log.info("Video: %dx%d @ %.2f fps (%.0fs)", eq_w, eq_h, meta["fps"], meta["duration_s"])

    # 2. Detection + tracking on perspective crops
    detector = Detector()
    fps_sampled = meta["fps"] / config.SAMPLE_RATE
    tracker = Tracker(
        frame_rate=max(1, int(round(fps_sampled))),
        track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
    )

    all_tracks: list[TrackedDetection] = []
    track_jersey_samples: dict[int, list[np.ndarray]] = {}

    log.info("Stage 2/6: detection + tracking...")
    for sample in iter_frames(str(video_path), sample_rate=config.SAMPLE_RATE):
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
        tracks_df["x_m"] = np.clip(xy[:, 0], -2.0, field_cal.length_m + 2.0)
        tracks_df["y_m"] = np.clip(xy[:, 1], -2.0, field_cal.width_m + 2.0)

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
        "generated_at_ms": int(time.time() * 1000),
    }
    firestore_io.write_analytics(game_id, _sanitize_json(analytics))
    log.info("Wrote analytics for game %s - %d players, %d GK events",
             game_id, len(player_stats), len(gk_positions))
    return analytics


# --- helpers -------------------------------------------------------------

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
    half_s = game.half_length_min * 60
    end_p1 = clock_to_video(1, half_s)
    start_p2 = clock_to_video(2, 0)
    end_p2 = clock_to_video(2, half_s)
    end_p2 = min(end_p2, video_duration_s)
    return [(0.0, min(end_p1, video_duration_s)), (start_p2, end_p2)]


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

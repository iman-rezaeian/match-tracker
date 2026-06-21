"""Gap-split pre-pass — break "zombie" tracks into clean contiguous sub-tracks.

The tracker keeps an id alive across long detection gaps and re-attaches a later,
possibly-different body to it (a "zombie" id that teleports across the field). Such
ids span most of the game but are sparse, and they pollute identity assignment +
inflate distance. Splitting each track wherever its internal sample gap exceeds
SPLIT_GAP_S yields contiguous, single-body fragments that stitch + assignment handle
cleanly (validated in tracking/st_stitch_probe.py: 2887 raw -> ~5765 clean on the
first 8K game).

This is a pure, deterministic relabel: same input -> same sub-track ids (fixed
sorted-traversal counter), so tracklet ids are stable across reruns. Sub-tracks
inherit their parent's jersey samples + Re-ID embedding (every downstream consumer
reads them read-only: classify takes the median HSV, reid_stitch takes the mean /
cosine). Run BEFORE team classification so one id universe flows through the rest of
the pipeline.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import config


def gap_split_tracks(
    tracks_df: pd.DataFrame,
    track_jersey_samples: Optional[dict[int, list]] = None,
    track_embeddings: Optional[dict[int, np.ndarray]] = None,
    *,
    split_gap_s: float = config.SPLIT_GAP_S,
) -> tuple[pd.DataFrame, dict[int, list], dict[int, np.ndarray], dict[int, int]]:
    """Relabel `track_id` by internal time gaps > `split_gap_s`.

    Returns (new_tracks_df, new_jersey_samples, new_embeddings, sub_to_parent).
    `new_tracks_df` is a copy with a fresh integer `track_id` per contiguous run;
    the aux dicts are re-keyed so each sub-track inherits its parent's value.
    `sub_to_parent` is provenance only (downstream correctness rebuilds every
    keyed dict from the returned aux dicts).
    """
    track_jersey_samples = track_jersey_samples or {}
    track_embeddings = track_embeddings or {}
    if tracks_df.empty or "track_id" not in tracks_df.columns:
        return tracks_df, dict(track_jersey_samples), dict(track_embeddings), {}

    df = tracks_df.sort_values(["track_id", "time_s"]).reset_index(drop=True)
    new_id = np.empty(len(df), dtype=np.int64)
    sub_to_parent: dict[int, int] = {}
    counter = 0
    # groupby on the sorted frame preserves contiguous row blocks per track_id.
    for parent, idx in df.groupby("track_id", sort=True).indices.items():
        t = df["time_s"].to_numpy()[idx]
        # segment index increments at each internal gap > split_gap_s
        seg = np.concatenate([[0], (np.diff(t) > split_gap_s).cumsum()]) if len(t) > 1 else np.zeros(1, dtype=np.int64)
        ids = counter + seg
        new_id[idx] = ids
        for s in np.unique(ids):
            sub_to_parent[int(s)] = int(parent)
        counter = int(ids.max()) + 1

    df["track_id"] = new_id
    new_jersey = {sub: track_jersey_samples.get(par, []) for sub, par in sub_to_parent.items()}
    new_emb = {sub: track_embeddings[par] for sub, par in sub_to_parent.items() if par in track_embeddings}
    return df, new_jersey, new_emb, sub_to_parent


def switch_split_tracks(
    tracks_df: pd.DataFrame,
    track_jersey_samples: Optional[dict[int, list]] = None,
    track_embeddings: Optional[dict[int, np.ndarray]] = None,
    *,
    max_speed_ms: float = config.SWITCH_MAX_SPEED_MS,
    min_jump_m: float = config.SWITCH_MIN_JUMP_M,
    reversal: bool = config.SWITCH_REVERSAL_ENABLED,
    reversal_deg: float = config.SWITCH_REVERSAL_DEG,
) -> tuple[pd.DataFrame, dict[int, list], dict[int, np.ndarray], dict[int, int]]:
    """Split each track at MID-RUN identity swaps (no time gap), using FIELD coords.

    gap_split_tracks cuts on time gaps; this cuts where the tracked body TELEPORTS
    — a single step whose implied speed > `max_speed_ms` AND distance > `min_jump_m`
    (dual gate so a genuine sprint, ~1 m/step, never trips it). The team-blind
    tracker grabs the nearest body during crossings, so such a jump = a different
    person. Optionally also cuts on a sharp heading reversal (same-area crossing).

    Requires `x_m`, `y_m`, `time_s`. Deterministic relabel (fixed sorted-traversal
    counter), so sub-track ids are stable across reruns — same contract as
    gap_split_tracks. Run AFTER gap_split + field projection.
    """
    track_jersey_samples = track_jersey_samples or {}
    track_embeddings = track_embeddings or {}
    need = {"x_m", "y_m", "time_s", "track_id"}
    if tracks_df.empty or not need.issubset(tracks_df.columns):
        return tracks_df, dict(track_jersey_samples), dict(track_embeddings), {}

    df = tracks_df.sort_values(["track_id", "time_s"]).reset_index(drop=True)
    x = df["x_m"].to_numpy(); y = df["y_m"].to_numpy(); t = df["time_s"].to_numpy()
    new_id = np.empty(len(df), dtype=np.int64)
    sub_to_parent: dict[int, int] = {}
    counter = 0
    for parent, idx in df.groupby("track_id", sort=True).indices.items():
        if len(idx) <= 1:
            new_id[idx] = counter
            sub_to_parent[counter] = int(parent)
            counter += 1
            continue
        xi, yi, ti = x[idx], y[idx], t[idx]
        dx, dy, dt = np.diff(xi), np.diff(yi), np.diff(ti)
        dist = np.hypot(dx, dy)
        with np.errstate(divide="ignore", invalid="ignore"):
            speed = np.where(dt > 1e-6, dist / dt, np.inf)
        cut = (speed > max_speed_ms) & (dist > min_jump_m)   # teleport (dual gate)
        if reversal and len(dx) > 1:
            # heading reversal between consecutive non-trivial steps
            ang = np.arctan2(dy, dx)
            dang = np.abs(np.degrees(np.diff(ang)))
            dang = np.minimum(dang, 360.0 - dang)
            moving = (dist[:-1] > 0.3) & (dist[1:] > 0.3)
            rev = np.zeros(len(dx), dtype=bool)
            rev[1:] = (dang > reversal_deg) & moving
            cut = cut | rev
        seg = np.concatenate([[0], cut.astype(np.int64).cumsum()])
        ids = counter + seg
        new_id[idx] = ids
        for s in np.unique(ids):
            sub_to_parent[int(s)] = int(parent)
        counter = int(ids.max()) + 1

    df["track_id"] = new_id
    new_jersey = {sub: track_jersey_samples.get(par, []) for sub, par in sub_to_parent.items()}
    new_emb = {sub: track_embeddings[par] for sub, par in sub_to_parent.items() if par in track_embeddings}
    return df, new_jersey, new_emb, sub_to_parent

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

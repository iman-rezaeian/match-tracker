"""Find the real halftime break in the footage, and cut tracks across it.

Why this is not just "use the coach's halftime tap"
---------------------------------------------------
The coach taps halftime on the PWA the moment the referee whistles, but that tap
is recorded on the GAME clock, which is anchored to the logged kickoff. The
kickoff tap and the kickoff in the video are not synced, so any error in the H1
offset shifts the derived break by the same amount:
`half_windows` computes `h1_end = video_offset_h1_kickoff_s + h1_play_s`, where
`h1_play_s` is a WALLCLOCK delta between taps. (`h2_start` is safer — it uses
`video_offset_h2_kickoff_s` when the coach set one.)

The footage settles it. At halftime everyone walks off, so the on-pitch body
count collapses to zero while play sits at 14–19 bodies. Measured on 3 games:

    game             logged break        measured empty        error
    mri01pvelv46d    1586..1689 (103s)   1586..1688 (102s)     +0s / −1s
    mqcf9axlvtuyt    1563..1878 (315s)   1516..1877 (361s)    **−47s** / −1s
    mpyo67cl4uflh    1933..2257 (324s)   1933..2256 (323s)     +0s / −1s

So the taps are usually right to the second — and when they are not, the error is
exactly the whistle-to-tap lag the coach described. Detect from the video, fall
back to the logged time only when detection is inconclusive.

What the split enforces
-----------------------
**No tracklet may span halftime.** A player cannot be one continuous body across
the break, so any track id that survives it has welded two different children
together. Measured: 71–92% of our tracked time sits in such tracklets, and the
weld is inside single BoT-SORT ids (they outlive the break), not in the stitcher,
which already refuses links longer than STITCH_MAX_GAP_S = 10 s.

This is deliberately NARROWER than `gap_split.py`, which cuts at EVERY internal
gap > SPLIT_GAP_S. That shatters good tracks to fix bad ones: measured at 96%
sub-30 s pieces and a resolvable regression (r +0.733 → +0.370 on common
players). Cutting only at the break removes the impossible welds and leaves
within-half continuity alone.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# A second counts as "pitch empty" when the smoothed body count falls to this
# fraction of the typical in-play count (or below EMPTY_ABS, whichever is
# larger) — robust to a stray coach/ref still wandering across the frame.
EMPTY_REL = 0.20
EMPTY_ABS = 2.0
# Median filter width (seconds) on the per-second body count.
SMOOTH_S = 11
# A real halftime is at least this long; anything shorter is a stoppage.
MIN_BREAK_S = 45.0
# Only trust a detected break this far from the logged one; beyond that the
# detector has probably locked onto an injury stoppage or a recording glitch.
MAX_SHIFT_S = 240.0


def detect_halftime_break(
    tracks_df: pd.DataFrame,
    logged_break: Optional[tuple[float, float]] = None,
    *,
    max_shift_s: float = MAX_SHIFT_S,
) -> Optional[tuple[float, float]]:
    """(start_s, end_s) of the true break in video seconds, or None.

    Finds the longest run of consecutive empty-pitch seconds. When
    `logged_break` is given, the run must overlap or sit within `max_shift_s`
    of it — so an injury stoppage elsewhere in the game can't be mistaken for
    halftime. Returns None when nothing qualifies (caller keeps the logged time).
    """
    if tracks_df.empty or "time_s" not in tracks_df.columns:
        return None
    t = tracks_df["time_s"].astype(float)
    n = int(t.max()) + 1
    if n < int(2 * MIN_BREAK_S):
        return None

    per_sec = (tracks_df.assign(_s=t.astype(int))
               .groupby("_s")["track_id"].nunique())
    counts = pd.Series(0.0, index=range(n), dtype=float)
    counts.loc[per_sec.index] = per_sec.to_numpy(dtype=float)
    smooth = counts.rolling(SMOOTH_S, center=True, min_periods=1).median()

    play_level = float(smooth.median())
    if play_level <= 0:
        return None
    thresh = max(EMPTY_ABS, EMPTY_REL * play_level)
    empty = (smooth <= thresh).to_numpy()

    # Longest empty run, ignoring the first/last 10% (pre-kickoff dead air and
    # post-game are empty too and would otherwise win).
    lo, hi = int(0.10 * n), int(0.90 * n)
    best_len, best = 0, None
    i = lo
    while i < hi:
        if not empty[i]:
            i += 1
            continue
        j = i
        while j < hi and empty[j]:
            j += 1
        if (j - i) > best_len:
            best_len, best = j - i, (float(i), float(j))
        i = j

    if best is None or best_len < MIN_BREAK_S:
        return None
    if logged_break is not None:
        lb0, lb1 = float(logged_break[0]), float(logged_break[1])
        overlaps = best[0] <= lb1 and best[1] >= lb0
        near = min(abs(best[0] - lb0), abs(best[1] - lb1)) <= max_shift_s
        if not (overlaps or near):
            log.warning(
                "  halftime: detected empty window %.0f-%.0fs is far from the "
                "logged break %.0f-%.0fs — keeping the logged time",
                best[0], best[1], lb0, lb1)
            return None
    return best


def split_tracks_at_halftime(
    tracks_df: pd.DataFrame,
    break_window: tuple[float, float],
    track_jersey_samples: Optional[dict[int, list]] = None,
    track_embeddings: Optional[dict[int, np.ndarray]] = None,
) -> tuple[pd.DataFrame, dict[int, list], dict[int, np.ndarray], dict[int, int]]:
    """Relabel `track_id` so no track spans the break.

    Mirrors `gap_split.gap_split_tracks`' contract — returns
    (new_tracks_df, new_jersey_samples, new_embeddings, sub_to_parent) with the
    aux dicts re-keyed so each side inherits its parent's value — but cuts at
    exactly ONE time per track instead of at every internal gap.

    Only tracks with detections on BOTH sides are renumbered; everything else
    keeps its id, so the change is a no-op on a game with no welds.
    """
    track_jersey_samples = track_jersey_samples or {}
    track_embeddings = track_embeddings or {}
    if tracks_df.empty or "track_id" not in tracks_df.columns:
        return tracks_df, dict(track_jersey_samples), dict(track_embeddings), {}

    b0, b1 = float(break_window[0]), float(break_window[1])
    mid = 0.5 * (b0 + b1)
    df = tracks_df.copy()
    tid = df["track_id"].to_numpy()
    after = (df["time_s"].astype(float).to_numpy() >= mid)

    # Which ids actually straddle? (present on both sides of the break)
    both = [t for t, g in pd.DataFrame({"t": tid, "a": after}).groupby("t")["a"]
            if bool(g.any()) and not bool(g.all())]
    if not both:
        log.info("  halftime split: no track spans the break — nothing to do")
        return df, dict(track_jersey_samples), dict(track_embeddings), {}

    straddling = set(int(t) for t in both)
    next_id = int(tid.max()) + 1
    new_for: dict[int, int] = {}
    for t in sorted(straddling):
        new_for[t] = next_id
        next_id += 1

    new_tid = tid.copy()
    sel = np.isin(tid, list(straddling)) & after
    new_tid[sel] = [new_for[int(t)] for t in tid[sel]]
    df["track_id"] = new_tid

    # Second-half halves inherit the parent's colour/appearance; that is the same
    # assumption gap_split makes, and classification re-derives from pixels anyway.
    sub_to_parent = {new: old for old, new in new_for.items()}
    new_jersey = dict(track_jersey_samples)
    new_emb = dict(track_embeddings)
    for new, old in sub_to_parent.items():
        if old in track_jersey_samples:
            new_jersey[new] = track_jersey_samples[old]
        if old in track_embeddings:
            new_emb[new] = track_embeddings[old]

    log.info("  halftime split: cut %d track(s) spanning the %.0f-%.0fs break",
             len(straddling), b0, b1)
    return df, new_jersey, new_emb, sub_to_parent

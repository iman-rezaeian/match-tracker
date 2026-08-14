"""Keep near-camera sideline adults out of the team-shape metrics.

Why this exists
---------------
Team-level metrics (centroid, width, depth, compactness, field tilt) are
functions of the SET of body positions, so they are immune to identity
confusion: permuting which name attaches to which body leaves them exactly
unchanged. That is why they survive the association failure that makes
per-player metrics untrustworthy.

They are NOT immune to wrong-TEAM leakage. Measured against the coach's
hand-labels on the two blind-GT games, sideline adults (coaches, spectators)
are the dominant pollutant -- on `mqcf9axlvtuyt` they are 41,040 detection rows
against our players' 30,759, i.e. they OUTNUMBER the team. Their effect on the
metrics they pollute:

    game             centroid error   width error
    mqcf9axlvtuyt        409 px          297 px
    mqcjsjugchb2i        155 px          100 px

Team width's own mean is ~88 px, so unfiltered these metrics are dominated by
adults standing at the touchline rather than by the shape of the team.

The signal: box HEIGHT, per track
---------------------------------
Sideline adults stand close to the camera and are physically taller, so they
project much larger. Per-track median box height, from the labels:

    label        mqcf9axlvtuyt   mqcjsjugchb2i
    ours              69 px           71 px
    __other__        123 px          129 px

Nearly 2x, consistent across both games.

It MUST be a per-TRACK cut, not per-detection. A coach standing at halfway
projects his feet ~31 cm inside the touchline, so a per-detection "is he on the
pitch" test cannot exclude him -- that is already recorded in the touchline
findings, and is why the existing never-on-pitch filter also works per track.

What it buys, and what it does NOT
----------------------------------
At `h < 120`, weighted by detection rows (what actually pollutes the metric):

    game             adults removed   our players lost
    mqcf9axlvtuyt         76.3%             13.2%
    mqcjsjugchb2i         81.9%              5.7%

Composition of labelled detections improves 43% -> 71% ours (G1) and
55% -> 80% (G2).

⚠ This is an IMPROVEMENT, NOT A SOLUTION. 33-34 adult tracks survive in each
game (9,742 and 4,758 rows), with median heights of 74-91 px -- genuinely
player-sized, because they are standing further from the camera. A far-side
coach is not separable from a child by size, so the residual is structural.
Treat filtered team width as directional, not precise.

⚠⚠ HOW TO SCORE THIS FILTER -- read before quoting any error figure. Measuring
median error over ALL time bins reports **0 px** after filtering, and that
number is an ARTEFACT, not a result. Two separate framings produced it: the
metric's own `>= 4 bodies` gate drops most bins that contain a surviving adult,
so the comparison silently excludes the hard cases and scores the filter on the
bins it never had to fix. The honest split -- error on the bins that actually
still contain a non-our-team body:

    game             arm          ALL bins   POLLUTED bins   share polluted
    mqcf9axlvtuyt    unfiltered     415 px       468 px           90%
                     h<120            0 px       256 px           25%
    mqcjsjugchb2i    unfiltered      165 px       369 px           67%
                     h<120            0 px       223 px           34%

So the real effect is: **the share of polluted bins falls 90% -> 25% and
67% -> 34%, and the error on those that remain roughly halves (468 -> 256,
369 -> 223 px).** That is a genuine, large win. It is NOT the elimination that
the all-bins column implies. Quote the polluted-bin column.

⚠ The threshold is TUNED, not safe-by-construction. The height distribution has
a populated middle; 120 px is a measured trade-off, not a gap in the data. Do
not describe it as bimodal (the never-on-pitch filter's docstring made exactly
that overclaim and it was false).

Rejected alternatives, measured
-------------------------------
* movement-based cut (`std of position > 150`): removes only 52%/10% of adults
  while losing 33%/25% of our players. Much worse.
* height AND movement: removes barely more adults (78.6%/82.8%) for 2-3x the
  loss of our own players (31.4%/15.7%).

Height alone is the right filter.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

# Per-track median box height, in pixels, above which a body is treated as a
# near-camera adult. Measured trade-off (see module docstring): our players sit
# at 69-71 px median, sideline adults at 123-129 px.
ADULT_BOX_H_PX = 120.0

# Below this many detections a track's median height is too noisy to judge, so
# it is KEPT. Conservative by design: never drop a real player to tidy a metric.
MIN_ROWS_TO_JUDGE = 5


def adult_track_ids(
    tracks_df: pd.DataFrame,
    threshold_px: float = ADULT_BOX_H_PX,
) -> set[int]:
    """Track ids whose median box height marks them as near-camera adults.

    Returns an empty set (keeping everything) when the height column is absent,
    so a caller on an older cache degrades to today's behaviour rather than
    silently dropping every track.
    """
    if "bbox_h_crop" not in tracks_df.columns or tracks_df.empty:
        log.warning("  adult_filter: no bbox_h_crop column — filter INACTIVE")
        return set()
    g = tracks_df.groupby("track_id")["bbox_h_crop"]
    med = g.median()
    n = g.size()
    judged = med[(n >= MIN_ROWS_TO_JUDGE) & (med >= threshold_px)]
    return {int(t) for t in judged.index}


def drop_sideline_adults(
    tracks_df: pd.DataFrame,
    threshold_px: float = ADULT_BOX_H_PX,
    report: dict | None = None,
) -> pd.DataFrame:
    """Remove near-camera adult tracks ahead of the TEAM-level metrics.

    Intended for team shape/tilt only. It is deliberately NOT applied to
    per-player stats: those are already gated on identity, and dropping a
    genuine player's near-camera detections there would bias his own numbers
    rather than clean a shared aggregate.

    `report`, if given, is filled with what was removed so the caller can
    surface it. A filter must report its deletions as an artefact rather than a
    log line -- the two shipped filters that were scored only on survivors both
    turned out to be cutting into our own team.
    """
    ids = adult_track_ids(tracks_df, threshold_px)
    if not ids:
        if report is not None:
            report.update(dropped_tracks=0, dropped_rows=0, kept_rows=len(tracks_df))
        return tracks_df
    mask = tracks_df["track_id"].isin(ids)
    out = tracks_df[~mask]
    if report is not None:
        report.update(
            dropped_tracks=len(ids),
            dropped_rows=int(mask.sum()),
            kept_rows=len(out),
            threshold_px=float(threshold_px),
            dropped_track_ids=sorted(ids)[:50],
        )
    log.info(
        "  adult_filter: dropped %d tracks / %d detections (%.1f%%) with median "
        "box height >= %.0f px — near-camera sideline adults. ~20%% of adult mass "
        "survives (far-side adults are player-sized); team width is directional.",
        len(ids), int(mask.sum()), 100.0 * mask.sum() / max(1, len(tracks_df)),
        threshold_px)
    return out

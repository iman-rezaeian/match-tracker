"""Keep non-player bodies out of the team-shape metrics, by box size.

Why this exists
---------------
Team-level metrics (centroid, width, depth, compactness, field tilt) are
functions of the SET of body positions, so they are immune to identity
confusion: permuting which name attaches to which body leaves them exactly
unchanged. That is why they survive the association failure that makes
per-player metrics untrustworthy.

They are NOT immune to wrong-BODY leakage. On a clicked frame of
`mrhvbvwi1gjpn`, only 24.8% of tracked rows are one of our players; the rest are
opponents, touchline adults, and phantoms. Team width's own mean is ~88 px, so
unfiltered these metrics describe the crowd as much as the team.

⚠⚠ THE ORIGINAL ONE-SIDED VERSION OF THIS FILTER WAS WRONG. It cut `h >= 120`
on the premise that our players are SMALL (69 px) and the pollutant is TALL
(123 px), measured on `mqcf9axlvtuyt`. Re-scored against the coach's clicks --
ground truth that did not exist when it was written -- the premise inverts:

    on clicked frames of mrhvbvwi1gjpn
        our clicked players   median  77 px   (p10 53, p90 127)
        unmatched bodies      median  62 px   (p10 39, p90 193)

Our players are the MIDDLE of the height distribution, not the bottom. The old
game read differently only because its tracking predates the reprojection fixes
and runs at a different scale entirely (rowwise median 94 px, vs 69/71 px on the
two clean-tracked games) -- so an absolute pixel threshold tuned there does not
transfer. P(is one of ours | height band), on clicked frames:

    <=50 px    4.1%     (2,140 rows)  far-side bodies, phantoms
    50-70     31.4%
    70-90     45.6%  <- our players live here
    90-110    37.4%
    110-130   29.5%
    130-160   23.4%
    >160      10.2%     (1,112 rows)  near-camera adults

Non-monotone: a hump. So the cut must be TWO-SIDED. The small-box tail is a
PURER pollutant (4.1% ours) than the tall tail the old filter targeted (10.2%),
and the old filter did not touch it at all.

The signal: box HEIGHT, per track, both tails
---------------------------------------------
It MUST be a per-TRACK cut, not per-detection. A coach standing at halfway
projects his feet ~31 cm inside the touchline, so a per-detection "is he on the
pitch" test cannot exclude him -- that is already recorded in the touchline
findings, and is why the existing never-on-pitch filter also works per track.

What it buys, and what it does NOT
----------------------------------
Scored on clicked frames of `mrhvbvwi1gjpn` (ours = a tracked box containing one
of the coach's clicks), weighted by detection rows:

    filter            ours kept   unmatched cut   purity
    none                 100.0%            0.0%    24.8%
    h>=120 (old)          90.1%           19.8%    27.0%
    outside 50-160        94.1%           40.0%    34.1%

The two-sided band keeps MORE of our own players than the old one-sided cut
while removing twice as much pollutant. Cuts 31.7% / 31.0% of rows on the two
clean-tracked games -- consistent, so the band transfers.

⚠ This is an IMPROVEMENT, NOT A SOLUTION. Purity goes 24.8% -> 34.1%: the
median team-shape frame still holds two non-players for every player. Most of
the residual is OPPONENTS, who are exactly player-sized and player-placed and
cannot be separated by geometry at all -- that is what the kit classifier is
for. Treat filtered team width as directional, never precise.

⚠⚠ HOW TO SCORE THIS FILTER -- read before quoting any error figure. Measuring
median error over ALL time bins reports **0 px** after filtering, and that
number is an ARTEFACT, not a result: the metric's own `>= 4 bodies` gate drops
most bins that contain a surviving adult, so the comparison silently excludes
the hard cases and scores the filter on the bins it never had to fix. Always
report `ours kept` ALONGSIDE `unmatched cut` -- a filter that reduces pollution
by deleting bodies scores well on any survivor-only metric, which is how the
opponent filter came to be shipped while eating a third of our own team.

⚠ The band is TUNED, not safe-by-construction. The height distribution has a
populated middle and no gap; 50-160 px is a measured trade-off. Do not describe
it as bimodal (the never-on-pitch filter's docstring made exactly that overclaim
and it was false).

Rejected alternatives, measured
-------------------------------
* one-sided `h >= 120` (the original): purity 27.0% for a 9.9% loss of our own
  players. Dominated by the band on both axes.
* tighter band `outside 55-150`: cuts more pollutant (50.9%) but drops 17.6% of
  our confirmed players. Too destructive for a 1.5-point purity gain.
* wider band `outside 45-180`: safer (97.1% ours kept) but only 32.0% purity.
  Kept as the conservative option if the band ever looks too aggressive.
* movement-based cut (`std of position > 150`): removes only 52%/10% of adults
  while losing 33%/25% of our players. Much worse.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

# Per-track median box height band, in pixels, INSIDE which a body is treated as
# plausibly one of our players. Measured trade-off (see module docstring): our
# clicked players run 53-127 px (p10-p90, median 77); below 50 px only 4% of rows
# are ours, above 160 px only 10%.
PLAYER_BOX_H_MIN_PX = 50.0
PLAYER_BOX_H_MAX_PX = 160.0

# Retained so the pre-2026-08-14 one-sided threshold stays greppable; the band
# above replaced it after the clicks showed its premise was inverted.
ADULT_BOX_H_PX = PLAYER_BOX_H_MAX_PX

# Below this many detections a track's median height is too noisy to judge, so
# it is KEPT. Conservative by design: never drop a real player to tidy a metric.
MIN_ROWS_TO_JUDGE = 5


def adult_track_ids(
    tracks_df: pd.DataFrame,
    min_px: float = PLAYER_BOX_H_MIN_PX,
    max_px: float = PLAYER_BOX_H_MAX_PX,
) -> set[int]:
    """Track ids whose median box height puts them outside the player band.

    Two-sided: both a near-camera adult (too tall) and a far-side body or
    phantom (too small) are non-players, and the small tail is the purer
    pollutant of the two.

    Returns an empty set (keeping everything) when the height column is absent,
    so a caller on an older cache degrades to today's behaviour rather than
    silently dropping every track.
    """
    if "bbox_h_crop" not in tracks_df.columns or tracks_df.empty:
        log.warning("  adult_filter: no bbox_h_crop column — filter INACTIVE")
        return set()
    g = tracks_df.groupby("track_id")["bbox_h_crop"]
    med = g.median()
    judged = med[(g.size() >= MIN_ROWS_TO_JUDGE) & ((med < min_px) | (med > max_px))]
    return {int(t) for t in judged.index}


def drop_sideline_adults(
    tracks_df: pd.DataFrame,
    min_px: float = PLAYER_BOX_H_MIN_PX,
    max_px: float = PLAYER_BOX_H_MAX_PX,
    report: dict | None = None,
) -> pd.DataFrame:
    """Remove out-of-band (non-player-sized) tracks ahead of TEAM-level metrics.

    Intended for team shape/tilt only. It is deliberately NOT applied to
    per-player stats: those are already gated on identity, and dropping a
    genuine player's near-camera detections there would bias his own numbers
    rather than clean a shared aggregate.

    `report`, if given, is filled with what was removed so the caller can
    surface it. A filter must report its deletions as an artefact rather than a
    log line -- the two shipped filters that were scored only on survivors both
    turned out to be cutting into our own team.
    """
    ids = adult_track_ids(tracks_df, min_px, max_px)
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
            band_px=[float(min_px), float(max_px)],
            dropped_track_ids=sorted(ids)[:50],
        )
    log.info(
        "  adult_filter: dropped %d tracks / %d detections (%.1f%%) whose median "
        "box height falls outside %.0f-%.0f px — non-player-sized bodies. Keeps "
        "~94%% of clicked players and cuts ~40%% of non-players; the residual is "
        "mostly OPPONENTS, so team width stays directional, not precise.",
        len(ids), int(mask.sum()), 100.0 * mask.sum() / max(1, len(tracks_df)),
        min_px, max_px)
    return out

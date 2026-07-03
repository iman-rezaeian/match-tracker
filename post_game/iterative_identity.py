"""Iterative anchor-coupled re-stitch (Phase A of the identity-bottleneck plan).

Same-kit appearance is dead and pure geometric stitching hits a fragmentation
floor, so most of our fragments never get a name. The one signal nothing
off-the-shelf uses is the coach event log. This module couples stitching and
identity so that log can re-link fragments geometry alone can't:

    round 1:  geometry-only stitch (tight, high-precision)
              → assign_identities_v2, harvesting HIGH-MARGIN seeds
                (tracklets backed by an individuating event/SUB anchor or a
                 detected keeper window — never the board template alone)
    round n:  expand seeds tracklet→fragment, feed them to stitch_tracklets as
              MUST-LINK (same seed → merge across long gaps the player was off
              frame) + CANNOT-LINK (different seed → never merge, even if
              geometrically plausible) → re-stitch → re-assign → new seeds
    stop:     when the seed set stops changing, or after MAX_ROUNDS

Because a confirmed identity now carries across a whole chain, one coach
confirmation (or FIX-IDS override) names far more on-field minutes than before.

Geometry-only means: call ``stitch_tracklets`` with NO embeddings / jersey
samples (OSNet is kit-dead and its 0.55 cosine gate would reject valid same-kit
merges) — identical to how ``build_coherent_stage4`` re-stitches. Appearance
never enters Phase A.

Returns ``(tracklet_of_track, assignments)``; the caller uses the returned
stitching for all downstream stages (stats, tracklet index, drafts).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Optional

import pandas as pd

from . import config
from .firestore_io import CoachEvent, RosterPlayer
from .identity import IdentityAssignment
from .identity_assign import assign_identities_v2
from .reid_stitch import stitch_tracklets, stitch_stats

log = logging.getLogger(__name__)


def _our_tracklet_members(
    team_of_track: dict[int, int], tracklet_of_track: dict[int, int]
) -> dict[int, list[int]]:
    """{tracklet_id: [our-team fragment track_ids]} for the current stitching."""
    members: dict[int, list[int]] = defaultdict(list)
    for t, tm in team_of_track.items():
        if tm == 0:
            members[tracklet_of_track.get(int(t), int(t))].append(int(t))
    return members


def _seeds_to_must_link(
    seeds: dict[int, object],
    team_of_track: dict[int, int],
    tracklet_of_track: dict[int, int],
) -> dict[int, object]:
    """Expand {tracklet: player_id} seeds to {fragment_track_id: player_id}."""
    members = _our_tracklet_members(team_of_track, tracklet_of_track)
    must: dict[int, object] = {}
    for tl, seed in seeds.items():
        pid = seed[0] if isinstance(seed, tuple) else seed  # (player_id, source)
        for t in members.get(tl, []):
            must[t] = pid
    return must


def assign_identities_iterative(
    tracks_df: pd.DataFrame,
    team_of_track: dict[int, int],
    events: list[CoachEvent],
    roster: list[RosterPlayer],
    starting_lineup: list[str],
    gk_player_id: Optional[str],
    period_clock_to_video_time: Callable[[int, int], float],
    periods_video: list[tuple[float, float]],
    field_length_m: float,
    field_width_m: float,
    overrides: Optional[dict] = None,
    squad: Optional[list[str]] = None,
    resolved_flips_out: Optional[dict] = None,
    orientation_ambiguous_out: Optional[list] = None,
    *,
    seeds_out: Optional[dict] = None,
    max_rounds: int = config.ID_ITERATIVE_MAX_ROUNDS,
    geom_max_gap_s: float = config.ID_ITERATIVE_GAP_S,
    geom_dist_cap_m: float = config.ID_ITERATIVE_DIST_CAP_M,
) -> tuple[dict[int, int], list[IdentityAssignment]]:
    """Run the stitch↔identity coupling loop.

    Forwards the coach-log / roster / calibration arguments to
    ``assign_identities_v2`` unchanged; owns the stitching internally. Coach
    ``overrides`` are honoured on every assign round (they win, and their
    identity seeds the constraints too, so an override propagates across gaps).

    ``seeds_out`` (optional) is filled with the FINAL round's high-margin seeds
    {tracklet_id: player_id} for draft generation / diagnostics.
    """

    def _stitch(must_link: Optional[dict]) -> dict[int, int]:
        # Geometry only (no embeddings / jersey samples — appearance is kit-dead)
        # and PRECISION-SAFE: tight gap + absolute distance cap so the un-seeded
        # majority doesn't over-merge across same-team crossings. Long-gap
        # bridging is identity-gated via MUST-LINK (its own, permissive cap).
        return stitch_tracklets(
            tracks_df, team_of_track,
            max_gap_s=geom_max_gap_s,
            geom_dist_cap_m=geom_dist_cap_m,
            must_link=must_link,
        )

    def _assign(tracklet_of_track: dict[int, int], seeds: Optional[dict],
                flips: Optional[dict], ambig: Optional[list]) -> list[IdentityAssignment]:
        return assign_identities_v2(
            tracks_df=tracks_df,
            tracklet_of_track=tracklet_of_track,
            team_of_track=team_of_track,
            events=events,
            roster=roster,
            starting_lineup=starting_lineup,
            gk_player_id=gk_player_id,
            period_clock_to_video_time=period_clock_to_video_time,
            periods_video=periods_video,
            field_length_m=field_length_m,
            field_width_m=field_width_m,
            overrides=overrides,
            squad=squad,
            resolved_flips_out=flips,
            orientation_ambiguous_out=ambig,
            anchor_seeds_out=seeds,
        )

    tracklet_of_track = _stitch(None)
    prev_must: Optional[dict] = None

    for rnd in range(1, max_rounds + 1):
        seeds: dict[int, object] = {}
        # Only the converged/final assign should populate the caller's flip /
        # ambiguity out-params; intermediate rounds use throwaways.
        final_candidate = (rnd == max_rounds)
        assignments = _assign(
            tracklet_of_track, seeds,
            resolved_flips_out if final_candidate else None,
            orientation_ambiguous_out if final_candidate else None,
        )
        must = _seeds_to_must_link(seeds, team_of_track, tracklet_of_track)
        _ss = stitch_stats(tracklet_of_track, team_of_track)
        log.info("  iterative round %d/%d: %d tracklets, %d high-margin seeds",
                 rnd, max_rounds, _ss["our_tracklets"], len(seeds))

        if not must or must == prev_must:
            # Converged (seed set stable) or no anchors to couple → this
            # assignment already matches the current stitching. Done.
            if seeds_out is not None:
                seeds_out.clear()
                seeds_out.update(seeds)
            log.info("  iterative: converged after %d round(s)", rnd)
            return tracklet_of_track, assignments

        prev_must = must
        tracklet_of_track = _stitch(must)

    # Rounds exhausted without convergence: the last loop iteration re-stitched
    # after its assign, so run one final assign on that stitching to keep the
    # returned assignments consistent with the returned tracklet_of_track.
    seeds = {}
    assignments = _assign(tracklet_of_track, seeds,
                          resolved_flips_out, orientation_ambiguous_out)
    if seeds_out is not None:
        seeds_out.clear()
        seeds_out.update(seeds)
    _ss = stitch_stats(tracklet_of_track, team_of_track)
    log.info("  iterative: reached max_rounds=%d (%d tracklets, %d seeds)",
             max_rounds, _ss["our_tracklets"], len(seeds))
    return tracklet_of_track, assignments

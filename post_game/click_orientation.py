"""Which end is OUR net, per half, so both halves read in one frame.

Why this is not optional
------------------------
Teams switch ends at half time. Without a per-half flip, a player who stood in
exactly the same place all match appears to have moved the length of the pitch:
the first pass over the coach's clicks reported Zaidan drifting **+28.9 m** up
the field between halves when he had simply changed ends. The coach caught it
immediately -- "no, they switched side" -- and every drift figure in that table
was wrong by construction.

How the end is decided
----------------------
The keeper is the anchor, and with clicks he is a far better one than with
tracks. The coach NAMES him, so his samples are known-good rather than inferred:
whichever end his clicks cluster at IS our net for that half. `stats.py` has to
work this out from goalmouth occupancy across all tracks; here it is a median.

Both halves are decided independently when each has keeper clicks. When only one
does, the other is taken as the opposite end -- teams alternate, so one anchored
half fixes the rest. With no keeper clicks at all the caller gets None and must
say the drift is unavailable rather than publish an unflipped number.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# A keeper sits within roughly this fraction of the pitch length from his line.
# Deliberately loose: he comes out to collect and takes goal kicks, and the
# median is what matters rather than any single sample.
KEEPER_MAX_FRAC = 0.35


def our_net_at_x0_from_keeper(
    pts: list[dict],
    gk_player_id: Optional[str],
    field_length_m: float,
    period_of,
) -> Optional[dict[int, bool]]:
    """{period: our net is at x=0} from the keeper's own clicks, or None.

    `pts` are projected clicks carrying `x_m` and `player_id`. `period_of` maps a
    video timestamp to a 1-based period index.
    """
    if not gk_player_id:
        log.warning("  click_orientation: no keeper on the game doc — cannot "
                    "orient halves, so half-to-half drift is unavailable")
        return None

    by_period: dict[int, list[float]] = {}
    for p in pts:
        if str(p.get("player_id")) != str(gk_player_id):
            continue
        by_period.setdefault(int(period_of(float(p["video_time_s"]))), []).append(
            float(p["x_m"]))
    if not by_period:
        log.warning("  click_orientation: the keeper has no clicks — half-to-half "
                    "drift is unavailable until he is sampled in each half")
        return None

    anchored: dict[int, bool] = {}
    for per, xs in by_period.items():
        med = float(np.median(xs))
        near_zero = med < field_length_m * KEEPER_MAX_FRAC
        near_far = med > field_length_m * (1.0 - KEEPER_MAX_FRAC)
        if near_zero or near_far:
            anchored[per] = near_zero
        else:
            # Mid-pitch median means these are not really keeper samples (a
            # mis-click, or an outfield spell). Refuse rather than guess: a wrong
            # flip mirrors an entire half.
            log.warning("  click_orientation: keeper median x=%.1f m in period %d "
                        "is mid-pitch — that half is not anchored", med, per)
    if not anchored:
        return None

    # Teams alternate ends, so one anchored half determines every other.
    base_per = min(anchored)
    base = anchored[base_per]
    out: dict[int, bool] = {}
    for per in sorted(set(list(by_period) + list(anchored))):
        out[per] = anchored.get(
            per, base if (per - base_per) % 2 == 0 else not base)
    log.info("  click_orientation: our net at x=0 by period: %s "
             "(anchored on the keeper's clicks in period %d)", out, base_per)
    return out

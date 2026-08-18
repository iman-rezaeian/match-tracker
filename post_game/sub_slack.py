"""Widen on-field windows by how sloppy each substitution actually was.

The coach logs subs one tap at a time on a phone at the touchline. A single
swap gets tapped immediately and its timestamp is good. A mass rotation — five
or six children changing at once, which is how U10 games are actually managed —
takes the length of the rotation to enter, so the last tap can land a long way
after the whistle. Measured on mri01pvelv46d: 5 of 8 substitution moments
involve 3+ players, and the taps within one moment span **80 s median, 117 s
worst case**.

`stats._drop_offwindow` treats those timestamps as exact and deletes every
detection outside them. That removes 20.7% of all attributed detections — and
the drops are not spread through the game the way genuine misattribution would
be: **96% land within 180 s of a substitution moment, median 58 s.** Most of
what is being deleted is tap lag, not another child.

A flat tolerance is the wrong instrument: big enough to cover a 117 s rotation
(240 s is what the identity assigner already uses on the same log) and the
filter stops rejecting anything at all — measured, a 240 s flat tolerance drops
0.1% instead of 20.7%. So the slack is derived per boundary instead: taps are
grouped into substitution moments, and each moment's own spread sets how much
its boundaries move. A clean single tap keeps a tight window and still catches
real misattribution; a messy rotation gets exactly as much room as it was messy.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

from . import config

log = logging.getLogger(__name__)


def cluster_sub_times(times: Iterable[float], gap_s: float) -> list[list[float]]:
    """Group sub-tap times into substitution MOMENTS.

    Consecutive taps closer than `gap_s` belong to the same moment — the coach
    entering one rotation. Returns a list of clusters, each a sorted list of
    times.
    """
    ts = sorted(float(t) for t in times)
    if not ts:
        return []
    clusters: list[list[float]] = [[ts[0]]]
    for t in ts[1:]:
        if t - clusters[-1][-1] > gap_s:
            clusters.append([t])
        else:
            clusters[-1].append(t)
    return clusters


def slack_for_time(
    t: float,
    clusters: list[list[float]],
    *,
    base_s: float,
    per_cluster_cap_s: float,
) -> float:
    """How far a window boundary at `t` may move, in seconds.

    A boundary belongs to whichever substitution moment it sits in (or nearest).
    Its slack is `base_s` plus that moment's own tap spread, capped. A boundary
    from a single clean tap has zero spread and so gets only `base_s`; one from
    a six-player rotation gets the full width of that rotation.
    """
    if not clusters:
        return base_s
    best = min(clusters, key=lambda c: min(abs(t - c[0]), abs(t - c[-1])))
    # Only credit the spread when the boundary actually came from this moment;
    # a boundary far from every tap (kickoff, final whistle) gets just the base.
    if not (best[0] - base_s <= t <= best[-1] + base_s):
        return base_s
    spread = best[-1] - best[0]
    return base_s + min(spread, per_cluster_cap_s)


def relax_intervals(
    intervals: dict[str, list[tuple[float, float]]],
    sub_times: Iterable[float],
    *,
    enabled: Optional[bool] = None,
    base_s: Optional[float] = None,
    gap_s: Optional[float] = None,
    cap_s: Optional[float] = None,
    log_fn: Callable[[str], None] = log.info,
) -> dict[str, list[tuple[float, float]]]:
    """Widen every on-field interval boundary by its substitution's slack.

    Returns a new dict; the input is not modified. With the feature disabled
    the input is returned unchanged, so callers can wire this in unconditionally.
    A window's start is never pushed below 0.
    """
    enabled = config.SUB_SLACK_ENABLED if enabled is None else enabled
    if not enabled or not intervals:
        return intervals
    base_s = config.SUB_SLACK_BASE_S if base_s is None else base_s
    gap_s = config.SUB_SLACK_CLUSTER_GAP_S if gap_s is None else gap_s
    cap_s = config.SUB_SLACK_MAX_S if cap_s is None else cap_s

    clusters = cluster_sub_times(sub_times, gap_s)
    if clusters:
        spreads = [c[-1] - c[0] for c in clusters]
        multi = sum(1 for c in clusters if len(c) >= 3)
        log_fn(
            "  sub-slack: %d substitution moment(s), %d with 3+ taps; "
            "spread median %.0fs max %.0fs (base %.0fs, cap %.0fs)"
            % (len(clusters), multi,
               sorted(spreads)[len(spreads) // 2], max(spreads), base_s, cap_s)
        )

    out: dict[str, list[tuple[float, float]]] = {}
    for pid, ivs in intervals.items():
        widened = []
        for lo, hi in ivs:
            lo2 = max(0.0, lo - slack_for_time(lo, clusters, base_s=base_s,
                                               per_cluster_cap_s=cap_s))
            hi2 = hi + slack_for_time(hi, clusters, base_s=base_s,
                                      per_cluster_cap_s=cap_s)
            widened.append((lo2, hi2))
        out[pid] = widened
    return out


def sub_times_from_events(events, period_clock_to_video_time) -> list[float]:
    """Video-second timestamps of every SUB tap."""
    return [
        float(period_clock_to_video_time(e.period, e.elapsed))
        for e in events
        if (getattr(e, "type", "") or "").upper() == "SUB"
    ]

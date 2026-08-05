"""Correct a player's on-field window from the tracklets the coach ACCEPTED.

Coaches log SUB events by tapping, and when several subs happen at once they tap
late — so a player's logged sub-on/sub-off time can be off by a chunk. Those SUB
times are the sole source of every player's on-field window
(`identity._onfield_intervals`), which drives the identity minute budget, reported
minutes, the rate-scaled distance/sprint extrapolation, formation, and the PWA
minutes. So a late tap quietly biases the whole stat line.

The camera is better ground truth for WHEN a player was on the pitch. When the
coach accepts tracklet→player assignments in FIX PLAYER IDS, the union of that
player's accepted tracklet spans tells us their real presence. This module turns
those spans into a per-player on-field correction — ADDITIVELY (it never edits
the coach's SUB events; the pipeline applies it on top of the event-derived
intervals, and it's recomputed each run from the current accepts).

Everything here is pure (no Firestore / video / pandas) so it unit-tests in
isolation. Times are VIDEO SECONDS (the axis half_windows / _onfield_intervals
use); the video→clock inverse is only for coach-facing display.
"""
from __future__ import annotations

from typing import Callable, Optional

# A correction is trusted only if the implied shift from the logged time is
# within a plausible "late tap" — a bigger jump is more likely a bad tracklet
# span than a real late sub, so we decline it (leave that edge uncorrected).
DEFAULT_MAX_SHIFT_S = 300.0        # 5 min
# A span covering almost the whole half is a chimera / stitched-through-gaps
# artifact, not one stint — don't let it define the window.
_MAX_HALF_COVER_FRAC = 0.9


def _half_of(span_mid: float, half_windows: list[tuple[float, float]]) -> Optional[tuple[float, float]]:
    """The (start,end) of the half the span midpoint falls in, else the nearest."""
    for (a, b) in half_windows:
        if a <= span_mid <= b:
            return (a, b)
    # midpoint outside both halves (halftime / pre-kickoff) → nearest by center
    if not half_windows:
        return None
    return min(half_windows, key=lambda w: abs((w[0] + w[1]) / 2.0 - span_mid))


def _logged_edges(logged_intervals: Optional[list[tuple[float, float]]]) -> tuple[Optional[float], Optional[float]]:
    """(logged_on, logged_off) = earliest start / latest end of a player's logged
    on-field intervals, or (None, None) if the coach logged none."""
    if not logged_intervals:
        return None, None
    return (min(a for a, _ in logged_intervals),
            max(b for _, b in logged_intervals))


def compute_sub_corrections(
    accepted_spans_by_player: dict[str, list[tuple[float, float]]],
    half_windows: list[tuple[float, float]],
    logged_intervals_by_player: dict[str, list[tuple[float, float]]],
    *,
    max_shift_s: float = DEFAULT_MAX_SHIFT_S,
) -> dict[str, dict]:
    """Per-player on-field correction from accepted tracklet spans.

    `accepted_spans_by_player`: {player_id: [(t_start_s, t_end_s), ...]} — the
        VIDEO-second spans of every tracklet the COACH accepted for that player.
    `half_windows`: [(t1s,t1e),(t2s,t2e)] video seconds (from identity.half_windows).
    `logged_intervals_by_player`: {player_id: [(on,off),...]} from the coach's
        SUB events (identity._onfield_intervals with corrections=None) — the
        baseline we measure the shift against.

    Returns {player_id: {"onS": float|None, "offS": float|None,
                         "loggedOnS": float|None, "loggedOffS": float|None}}
    for players whose correction passed the guards. An edge is None when it did
    not pass (leave that edge as logged). Players with no trusted edge are omitted.
    """
    out: dict[str, dict] = {}
    for pid, spans in (accepted_spans_by_player or {}).items():
        spans = [(float(a), float(b)) for a, b in spans if b >= a]
        if not spans:
            continue
        on_s = min(a for a, _ in spans)     # UNION: earliest first-appearance
        off_s = max(b for _, b in spans)    #        latest last-appearance
        half = _half_of((on_s + off_s) / 2.0, half_windows)
        if half is None:
            continue
        ha, hb = half
        # CLAMP to the half — kills near-whole-game / cross-halftime chimeras.
        on_s = max(on_s, ha)
        off_s = min(off_s, hb)
        if off_s <= on_s:
            continue
        # Chimera guard: a span covering ~the whole half isn't one stint.
        half_len = max(hb - ha, 1e-6)
        if (off_s - on_s) / half_len > _MAX_HALF_COVER_FRAC:
            continue
        logged_on, logged_off = _logged_edges(logged_intervals_by_player.get(pid))
        # Plausibility guard per edge: only trust a shift within a late-tap-sized
        # window. If the coach logged nothing for this player, accept both edges
        # (there's no logged time to be biased against).
        corr = {"onS": None, "offS": None, "loggedOnS": logged_on, "loggedOffS": logged_off}
        if logged_on is None or abs(on_s - logged_on) <= max_shift_s:
            corr["onS"] = on_s
        if logged_off is None or abs(off_s - logged_off) <= max_shift_s:
            corr["offS"] = off_s
        if corr["onS"] is not None or corr["offS"] is not None:
            out[pid] = corr
    return out


def apply_corrections_to_intervals(
    intervals: dict[str, list[tuple[float, float]]],
    corrections: dict[str, dict],
) -> dict[str, list[tuple[float, float]]]:
    """Return a copy of `intervals` with each corrected player's FIRST-interval
    start replaced by onS and LAST-interval end replaced by offS (only the edges
    the correction filled). A player with a correction but no logged interval
    gets a single [onS, offS]. Corrections with only one edge keep the other from
    the logged interval (or fall back to the correction's own edge if none)."""
    out = {pid: list(ivs) for pid, ivs in (intervals or {}).items()}
    for pid, corr in (corrections or {}).items():
        on_s, off_s = corr.get("onS"), corr.get("offS")
        if on_s is None and off_s is None:
            continue
        ivs = out.get(pid)
        if not ivs:
            # No logged interval — synthesize one from whichever edge(s) we have.
            a = on_s if on_s is not None else off_s
            b = off_s if off_s is not None else on_s
            if b >= a:
                out[pid] = [(float(a), float(b))]
            continue
        ivs = sorted(ivs)
        if on_s is not None:
            ivs[0] = (float(on_s), ivs[0][1])
        if off_s is not None:
            ivs[-1] = (ivs[-1][0], float(off_s))
        out[pid] = ivs
    return out


def clock_or_none_factory(
    half_windows: list[tuple[float, float]],
    period_clock_to_video_time: Callable[[int, int], float],
    *,
    end_pad_s: float = 60.0,
) -> Callable[[Optional[float]], Optional[dict]]:
    """Coach-facing echo of a correction edge as {"period","elapsed"} — or None.

    A player who's never subbed off carries the "played to the final whistle"
    sentinel (identity._onfield_intervals video_end_s=1e9). That's a marker, not
    a real clock time, so any edge at/beyond the last half's end (+pad) echoes as
    None (the PWA renders a None edge as "to end") rather than a garbage clock
    time like 999998311s. None in → None out.
    """
    to_clock = video_time_to_period_clock_factory(half_windows, period_clock_to_video_time)
    end_sentinel = (half_windows[-1][1] if half_windows else 0.0) + end_pad_s

    def f(video_s: Optional[float]) -> Optional[dict]:
        if video_s is None or float(video_s) >= end_sentinel:
            return None
        p, e = to_clock(float(video_s))
        return {"period": p, "elapsed": round(e, 1)}

    return f


def video_time_to_period_clock_factory(
    half_windows: list[tuple[float, float]],
    period_clock_to_video_time: Callable[[int, int], float],
) -> Callable[[float], tuple[int, float]]:
    """Inverse of identity.period_clock_to_video_time: video_s -> (period, elapsed_s).

    The forward map is piecewise-linear slope-1 per period; the per-period video
    origin is `period_clock_to_video_time(period, 0)`. Pick the period whose
    window contains video_s (nearest if between halves) and subtract that origin.
    For coach-facing display of a corrected on/off time as a game clock.
    """
    origins = [(p, float(period_clock_to_video_time(p, 0))) for p in (1, 2)]

    def f(video_s: float) -> tuple[int, float]:
        period = 1
        for p, (a, b) in zip((1, 2), half_windows[:2]):
            if a <= video_s <= b:
                period = p
                break
        else:
            # outside both halves → nearest half by center
            if len(half_windows) >= 2:
                period = min((1, 2), key=lambda p: abs(
                    (half_windows[p - 1][0] + half_windows[p - 1][1]) / 2.0 - video_s))
        origin = dict(origins)[period]
        return period, max(0.0, float(video_s) - origin)

    return f

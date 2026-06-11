"""Team formation, compactness, and width over time."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


@dataclass
class FormationSnapshot:
    period: int
    label: str
    avg_positions: dict[str, tuple[float, float]]
    # Source of `label` and `coach_positions_norm`: "coach" when ≥3 coach
    # POSITION events were available in the period, else "tracks".
    label_source: str = "tracks"
    # Per-player coach-board positions in normalized half-field coords
    # (x∈[0,1] left→right from coach POV, y∈[0,1] halfway→own goal). Empty
    # dict when no coach POSITION events were available.
    coach_positions_norm: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class TeamTimeSeries:
    times_s: list[float]
    compactness_m: list[float]
    width_m: list[float]
    depth_m: list[float]
    centroid_x_m: list[float]


def _label_formation_outfield(xs: np.ndarray) -> str:
    """Row-count label ("2-3-1") from 1-D depth values, defense row first.

    Rows are contiguous in depth, so the optimal 3-way split is found exactly
    by trying every pair of split points (n is tiny) and keeping the minimum
    within-row variance — deterministic, unlike the KMeans it replaced.
    """
    n = len(xs)
    if n == 0:
        return "?"
    if n < 4:
        return f"({n} outfield)"
    v = np.sort(np.asarray(xs, dtype=float))

    def _var_sum(a: np.ndarray) -> float:
        return float(((a - a.mean()) ** 2).sum()) if len(a) else 0.0

    best: tuple[float, tuple[int, int, int]] | None = None
    for i in range(1, n - 1):
        for j in range(i + 1, n):
            cost = _var_sum(v[:i]) + _var_sum(v[i:j]) + _var_sum(v[j:])
            if best is None or cost < best[0]:
                best = (cost, (i, j - i, n - j))
    return "-".join(str(c) for c in best[1])


# Board state at KICKOFF defines the formation — the coach's intended shape.
# ONLY kickoff-window drags count: they all describe the same instant. Mid-
# half drags move kids between slots at different moments, so aggregating
# them mixes snapshots of the rotation and mislabels (measured on real games:
# 2-3-1 all game read as 2-2-2 / 1-3-1 in 2nd halves). A period with no
# kickoff board (coach didn't re-set at halftime) INHERITS the previous
# period's formation — shape changes are explicit acts.
# MIRROR: coachKickoffFormation in soccer_team_app.jsx uses the same rules.
FORMATION_KICKOFF_WINDOW_S = 120.0
# Minimum kickoff-window outfield drags for a period to own a formation
# (a full board re-set writes everyone; 1-2 stray early drags are not a board).
FORMATION_MIN_KICKOFF_PLAYERS = 4


def _coach_positions_for_period(
    coach_events: Iterable[Any],
    period_index_1based: int,
) -> dict[str, tuple[float, float]]:
    """Kickoff-board POSITION per player: the last drag inside
    FORMATION_KICKOFF_WINDOW_S of the given period's clock (pre-kickoff
    corrections settle). Players only dragged later in the period are
    EXCLUDED — see the kickoff-only rationale above.

    Accepts any iterable of objects with `.type`, `.player_id`, `.period`,
    `.elapsed`, `.at`, and `.extras` (dict with optional `x`, `y`). Returns
    {player_id: (x, y)} in normalized [0,1] half-field coords.
    """
    early: dict[str, tuple[int, float, float]] = {}
    for e in coach_events or []:
        if getattr(e, "type", None) != "POSITION":
            continue
        if int(getattr(e, "period", 0) or 0) != period_index_1based:
            continue
        pid = getattr(e, "player_id", None)
        if not pid:
            continue
        extras = getattr(e, "extras", {}) or {}
        x = extras.get("x")
        y = extras.get("y")
        if x is None or y is None:
            continue
        try:
            x = float(x); y = float(y)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue
        try:
            elapsed = float(getattr(e, "elapsed", 0) or 0)
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed > FORMATION_KICKOFF_WINDOW_S:
            continue
        at = int(getattr(e, "at", 0) or 0)
        prev = early.get(pid)
        if prev is None or at >= prev[0]:
            early[pid] = (at, x, y)
    return {pid: (x, y) for pid, (_, x, y) in early.items()}


def _onfield_at_period_start(
    starting_lineup: list[str],
    coach_events: Iterable[Any],
    period_1based: int,
) -> set[str]:
    """Reconstruct the set of players on the field at the kickoff of a period:
    the starting lineup plus every SUB applied in earlier periods."""
    on = set(starting_lineup or [])
    subs = sorted(
        (e for e in (coach_events or []) if getattr(e, "type", None) == "SUB"),
        key=lambda e: (int(getattr(e, "period", 0) or 0), int(getattr(e, "elapsed", 0) or 0), int(getattr(e, "at", 0) or 0)),
    )
    for e in subs:
        if int(getattr(e, "period", 0) or 0) >= period_1based:
            break  # only subs from *earlier* periods affect this kickoff
        off = getattr(e, "player_id", None)
        on_pid = (getattr(e, "extras", {}) or {}).get("subOnPlayerId")
        if off in on:
            on.discard(off)
        if on_pid:
            on.add(on_pid)
    return on


def compute_formation(
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    team_of_player: dict[str, int],
    periods: list[tuple[float, float]],
    gk_player_id: Optional[str] = None,
    coach_events: Optional[Iterable[Any]] = None,
    starting_lineup: Optional[list[str]] = None,
) -> tuple[list[FormationSnapshot], TeamTimeSeries]:
    df = tracks_field_df.copy()
    df["player_id"] = df["track_id"].map(identity_by_track)
    df = df[df["player_id"].notna()]
    df["team"] = df["player_id"].map(lambda p: team_of_player.get(p, -1))
    snaps: list[FormationSnapshot] = []

    for i, (start_s, end_s) in enumerate(periods):
        sub = df[(df["time_s"] >= start_s) & (df["time_s"] <= end_s) & (df["team"] == 0)]
        if sub.empty:
            positions: dict[str, tuple[float, float]] = {}
        else:
            avg = (
                sub.groupby("player_id")[["x_m", "y_m"]].median().to_dict(orient="index")
            )
            positions = {str(pid): (float(v["x_m"]), float(v["y_m"])) for pid, v in avg.items()}

        # Coach POSITION events (ground truth): build the formation from the
        # KICKOFF BOARD of the period, restricted to players actually on the
        # field at kickoff (lineup + subs from earlier periods). No mid-half
        # or earlier-period fallback positions — see the kickoff-only
        # rationale above _coach_positions_for_period.
        coach_norm = _coach_positions_for_period(coach_events or [], i + 1)
        onfield = _onfield_at_period_start(starting_lineup or [], coach_events or [], i + 1)
        onfield_outfield = {p for p in onfield if p != gk_player_id}
        coach_outfield = {
            pid: xy for pid, xy in coach_norm.items()
            if pid != gk_player_id and (not onfield_outfield or pid in onfield_outfield)
        }
        prev_coach_label = next(
            (s.label for s in reversed(snaps) if s.label_source.startswith("coach")), None)
        if len(coach_outfield) >= FORMATION_MIN_KICKOFF_PLAYERS:
            # Coach board: y=0 is halfway/attacking, y=1 is own goal. Use the
            # depth axis (1 - y) for row clustering so deeper defenders sit
            # in the first cluster — matching the tracks-based convention
            # where label rows are read defense → attack.
            depth = np.array([1.0 - xy[1] for xy in coach_outfield.values()])
            label = _label_formation_outfield(depth)
            label_source = "coach"
        elif prev_coach_label is not None:
            # No kickoff board this period (no halftime re-set) → the shape
            # carried over from the previous period.
            label = prev_coach_label
            label_source = "coach-carryover"
        else:
            outfield_xs = np.array([
                xy[0] for pid, xy in positions.items() if pid != gk_player_id
            ])
            label = _label_formation_outfield(outfield_xs)
            label_source = "tracks"

        if not positions and not coach_norm:
            continue

        snaps.append(FormationSnapshot(
            period=i + 1,
            label=label,
            avg_positions=positions,
            label_source=label_source,
            coach_positions_norm={pid: (float(x), float(y)) for pid, (x, y) in coach_norm.items()},
        ))

    our = df[df["team"] == 0]
    if our.empty:
        ts = TeamTimeSeries([], [], [], [], [])
    else:
        t0 = float(our["time_s"].min())
        t1 = float(our["time_s"].max())
        times, comp, width, depth, cx = [], [], [], [], []
        cur = t0
        while cur <= t1:
            window = our[(our["time_s"] >= cur) & (our["time_s"] < cur + 1.0)]
            if not window.empty and window["player_id"].nunique() >= 3:
                xs = window.groupby("player_id")["x_m"].mean().to_numpy()
                ys = window.groupby("player_id")["y_m"].mean().to_numpy()
                pairwise = np.sqrt(
                    (xs[:, None] - xs[None, :]) ** 2 + (ys[:, None] - ys[None, :]) ** 2
                )
                comp.append(float(pairwise[np.triu_indices_from(pairwise, k=1)].mean()))
                width.append(float(ys.max() - ys.min()))
                depth.append(float(xs.max() - xs.min()))
                cx.append(float(xs.mean()))
                times.append(cur)
            cur += 1.0
        ts = TeamTimeSeries(times, comp, width, depth, cx)
    return snaps, ts

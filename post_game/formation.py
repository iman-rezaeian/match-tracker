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
    if len(xs) == 0:
        return "?"
    from sklearn.cluster import KMeans
    if len(xs) >= 4:
        km = KMeans(n_clusters=3, n_init=10, random_state=0).fit(xs.reshape(-1, 1))
        order = np.argsort(km.cluster_centers_.flatten())
        counts = np.bincount(km.labels_, minlength=3)[order]
        return "-".join(str(int(c)) for c in counts)
    return f"({len(xs)} outfield)"


def _coach_positions_for_period(
    coach_events: Iterable[Any],
    period_index_1based: int,
) -> dict[str, tuple[float, float]]:
    """Last POSITION event per player within the given period.

    Accepts any iterable of objects with `.type`, `.player_id`, `.period`,
    `.at`, and `.extras` (dict with optional `x`, `y`). Returns
    {player_id: (x, y)} in normalized [0,1] half-field coords.
    """
    by_player: dict[str, tuple[int, float, float]] = {}
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
        at = int(getattr(e, "at", 0) or 0)
        prev = by_player.get(pid)
        if prev is None or at >= prev[0]:
            by_player[pid] = (at, x, y)
    return {pid: (x, y) for pid, (_, x, y) in by_player.items()}


def _latest_positions(coach_events: Iterable[Any]) -> dict[str, tuple[float, float]]:
    """Latest POSITION per player across ALL events (by `at`). Fallback for
    on-field players who weren't re-dragged in a later period."""
    by_player: dict[str, tuple[int, float, float]] = {}
    for e in coach_events or []:
        if getattr(e, "type", None) != "POSITION":
            continue
        pid = getattr(e, "player_id", None)
        if not pid:
            continue
        extras = getattr(e, "extras", {}) or {}
        x, y = extras.get("x"), extras.get("y")
        if x is None or y is None:
            continue
        try:
            x = float(x); y = float(y)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue
        at = int(getattr(e, "at", 0) or 0)
        prev = by_player.get(pid)
        if prev is None or at >= prev[0]:
            by_player[pid] = (at, x, y)
    return {pid: (x, y) for pid, (_, x, y) in by_player.items()}


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
        # players ACTUALLY ON THE FIELD at this period's kickoff (lineup + subs
        # from earlier periods) — not everyone who appeared in the period, which
        # would include subs and yield 11-player shapes (e.g. 3-6-2) on a 7v7.
        coach_norm = _coach_positions_for_period(coach_events or [], i + 1)
        onfield = _onfield_at_period_start(starting_lineup or [], coach_events or [], i + 1)
        onfield_outfield = {p for p in onfield if p != gk_player_id}
        # Position for each on-field outfield player: prefer this period's drag,
        # else their last known position from any earlier period.
        global_pos = _latest_positions(coach_events or [])
        coach_outfield = {}
        for pid in onfield_outfield:
            xy = coach_norm.get(pid) or global_pos.get(pid)
            if xy is not None:
                coach_outfield[pid] = xy
        # If we couldn't reconstruct the on-field set (no lineup/subs), fall back
        # to all coach positions in the period (minus GK).
        if not coach_outfield:
            coach_outfield = {pid: xy for pid, xy in coach_norm.items() if pid != gk_player_id}
        if len(coach_outfield) >= 3:
            # Coach board: y=0 is halfway/attacking, y=1 is own goal. Use the
            # depth axis (1 - y) for row clustering so deeper defenders sit
            # in the first cluster — matching the tracks-based convention
            # where label rows are read defense → attack.
            depth = np.array([1.0 - xy[1] for xy in coach_outfield.values()])
            label = _label_formation_outfield(depth)
            label_source = "coach"
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

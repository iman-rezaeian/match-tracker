"""Team formation, compactness, and width over time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class FormationSnapshot:
    period: int
    label: str
    avg_positions: dict[str, tuple[float, float]]


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


def compute_formation(
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    team_of_player: dict[str, int],
    periods: list[tuple[float, float]],
    gk_player_id: Optional[str] = None,
) -> tuple[list[FormationSnapshot], TeamTimeSeries]:
    df = tracks_field_df.copy()
    df["player_id"] = df["track_id"].map(identity_by_track)
    df = df[df["player_id"].notna()]
    df["team"] = df["player_id"].map(lambda p: team_of_player.get(p, -1))
    snaps: list[FormationSnapshot] = []

    for i, (start_s, end_s) in enumerate(periods):
        sub = df[(df["time_s"] >= start_s) & (df["time_s"] <= end_s) & (df["team"] == 0)]
        if sub.empty:
            continue
        avg = (
            sub.groupby("player_id")[["x_m", "y_m"]].median().to_dict(orient="index")
        )
        positions = {str(pid): (float(v["x_m"]), float(v["y_m"])) for pid, v in avg.items()}
        outfield_xs = np.array([
            xy[0] for pid, xy in positions.items() if pid != gk_player_id
        ])
        label = _label_formation_outfield(outfield_xs)
        snaps.append(FormationSnapshot(period=i + 1, label=label, avg_positions=positions))

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

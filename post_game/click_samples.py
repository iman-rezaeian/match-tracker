"""Turn coach clicks into per-player POSITION metrics.

Why this exists when five other approaches were killed
------------------------------------------------------
Every earlier route to per-player numbers attached a name to a TRACK and
inherited its trajectory, so all of them inherited the association failure
(6.0 s median track lifespan, ~23% wrong-child contamination) and capped around
12-20% coverage. A click is its own datum: the coach supplies both the identity
AND the position, and this module supplies only the homography and the
arithmetic. Nothing here reads a track id.

Measured accuracy, sampling real trajectories on both coach-labelled games:

    clicks/player   mean-position error   as % of gap between two players
        10               134 px                    20%
        20                98 px                    15%
        50                49 px                     7%
       100                38 px                     6%

Two players sit 565-676 px apart, which is the scale that matters. 50
clicks/player is the target: ~400 clicks, ~13 min of coach time, ~7% error.

Design rules that come from measurement, not taste
--------------------------------------------------
* **Only sample-based metrics.** Average position, territory, thirds, heatmap,
  width/depth, half-to-half drift. These are estimators over instants, so they
  converge with click count and carry no systematic bias.
* **NO distance, sprints or speed — not even caveated.** A click is a position,
  not a path; you cannot integrate motion from 50 samples. A number on screen
  gets quoted regardless of its footnote, so this module must not emit one at
  all.
* **Refuse below MIN_CLICKS.** At 10 clicks the error is 20% of the distance
  between two different children, which is not a metric. Report the player as
  under-sampled instead of publishing a number.
* Clicks must be SPREAD across the match. Clustered clicks plateau the error at
  ~170 px however many are added, so `spread_score` is reported alongside every
  player and a poor spread is a caveat on the whole game.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Below this many clicks a player's position estimate is not reported. 20 is
# where the measured error is ~15% of the player-to-player gap; 10 is 20%.
MIN_CLICKS = 20
# Thirds boundaries along the depth axis, matching config.THIRDS_FRACTIONS.
THIRDS = (1 / 3, 2 / 3)


@dataclass
class ClickPlayerStats:
    player_id: str
    n_clicks: int
    # canonical field coords: depth 0 = our net, width consistent left->right
    avg_depth_m: float
    avg_width_m: float
    p10_depth_m: float
    p90_depth_m: float
    p10_width_m: float
    p90_width_m: float
    pct_def_third: float
    pct_mid_third: float
    pct_att_third: float
    depth_spread_m: float
    width_spread_m: float
    spread_score: float                  # 0..1, how evenly clicks span the match
    by_half: dict = field(default_factory=dict)
    heatmap: list = field(default_factory=list)
    # deliberately absent: distance, speed, sprints. See module docstring.


def load_clicks(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def to_field(
    clicks: list[dict], field_cal,
) -> list[dict]:
    """Project each click from equirect pixels to field metres."""
    from . import calibration as _cal

    fp = _cal.FieldProjector(field_cal)
    out = []
    for c in clicks:
        if c.get("player_id") in (None, "__not_ours__"):
            continue
        x_m, y_m = fp.pixel_to_field(float(c["click_x_eq"]), float(c["click_y_eq"]))
        out.append({**c, "x_m": float(x_m), "y_m": float(y_m)})
    return out


def spread_score(times: np.ndarray, t0: float, t1: float, bins: int = 10) -> float:
    """How evenly do a player's clicks span the match? 1.0 = perfectly spread.

    This is not decoration. Clustered sampling plateaus the position error at
    ~170 px regardless of volume, so a player with 60 clicks all in one passage
    is worse than one with 25 spread across the game, and the caller needs to
    see that.
    """
    if len(times) < 2 or t1 <= t0:
        return 0.0
    h, _ = np.histogram(times, bins=bins, range=(t0, t1))
    return float((h > 0).sum() / bins)


def compute_click_stats(
    clicks: list[dict],
    field_cal,
    periods: Optional[list[tuple[float, float]]] = None,
    our_net_at_x0: Optional[dict[int, bool]] = None,
    min_clicks: int = MIN_CLICKS,
    heatmap_shape: tuple[int, int] = (6, 4),
) -> tuple[list[ClickPlayerStats], dict]:
    """Per-player position metrics from clicks. Returns (stats, report).

    `our_net_at_x0` maps period -> whether our net is at x=0, so both halves are
    flipped into one canonical frame (a left-back reads bottom-left in both).
    Without it, half-to-half comparisons are mirrored and meaningless.
    """
    L = float(field_cal.length_m)
    W = float(field_cal.width_m)
    pts = to_field(clicks, field_cal)
    report: dict = {"n_clicks_total": len(clicks), "n_usable": len(pts),
                    "under_sampled": [], "min_clicks": min_clicks}
    if not pts:
        return [], report

    def period_of(t: float) -> int:
        for i, (a, b) in enumerate(periods or [], start=1):
            if a <= t <= b:
                return i
        return 1

    t_all = np.array([p["video_time_s"] for p in pts], dtype=float)
    t0, t1 = float(t_all.min()), float(t_all.max())

    by_player: dict[str, list[dict]] = {}
    for p in pts:
        by_player.setdefault(str(p["player_id"]), []).append(p)

    out: list[ClickPlayerStats] = []
    for pid, rows in sorted(by_player.items()):
        if len(rows) < min_clicks:
            report["under_sampled"].append({"player_id": pid, "n_clicks": len(rows)})
            continue
        t = np.array([r["video_time_s"] for r in rows], dtype=float)
        x = np.array([r["x_m"] for r in rows], dtype=float)
        y = np.array([r["y_m"] for r in rows], dtype=float)

        # canonical orientation per half
        if our_net_at_x0:
            flip = np.array([not our_net_at_x0.get(period_of(tt), True) for tt in t])
            d = np.where(flip, L - x, x)
            w = np.where(flip, W - y, y)
        else:
            d, w = x, y

        att = d >= L * THIRDS[1]
        mid = (d >= L * THIRDS[0]) & (d < L * THIRDS[1])
        dfn = d < L * THIRDS[0]

        halves: dict = {}
        for per in sorted({period_of(tt) for tt in t}):
            m = np.array([period_of(tt) == per for tt in t])
            if m.sum() >= max(5, min_clicks // 3):
                halves[str(per)] = {
                    "n_clicks": int(m.sum()),
                    "avg_depth_m": round(float(d[m].mean()), 2),
                    "avg_width_m": round(float(w[m].mean()), 2),
                }

        hm, _, _ = np.histogram2d(
            d, w, bins=heatmap_shape, range=[[0, L], [0, W]])
        tot = max(1.0, hm.sum())

        out.append(ClickPlayerStats(
            player_id=pid, n_clicks=len(rows),
            avg_depth_m=round(float(d.mean()), 2),
            avg_width_m=round(float(w.mean()), 2),
            p10_depth_m=round(float(np.quantile(d, 0.10)), 2),
            p90_depth_m=round(float(np.quantile(d, 0.90)), 2),
            p10_width_m=round(float(np.quantile(w, 0.10)), 2),
            p90_width_m=round(float(np.quantile(w, 0.90)), 2),
            pct_def_third=round(100.0 * dfn.mean(), 1),
            pct_mid_third=round(100.0 * mid.mean(), 1),
            pct_att_third=round(100.0 * att.mean(), 1),
            depth_spread_m=round(float(d.std()), 2),
            width_spread_m=round(float(w.std()), 2),
            spread_score=round(spread_score(t, t0, t1), 2),
            by_half=halves,
            heatmap=(hm / tot).round(3).tolist(),
        ))

    if report["under_sampled"]:
        log.warning(
            "  click_samples: %d player(s) below %d clicks — reported as "
            "under-sampled rather than published: %s",
            len(report["under_sampled"]), min_clicks,
            ", ".join(u["player_id"] for u in report["under_sampled"]))
    return out, report

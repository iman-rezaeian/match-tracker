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
# How far outside the pitch a click may project and still be treated as a real
# player clamped to the line rather than a mis-click. The far touchline is the
# horizon in this geometry, so a player standing on it can project several metres
# negative from a few pixels of click error.
FAR_CLAMP_M = 8.0
# Heatmap kernel width in metres. A raw histogram discards almost everything the
# clicks carry -- two clicks either side of a cell boundary contribute nothing to
# each other -- so a 12x8 grid scored 0.15 split-half agreement, i.e. noise. A
# Gaussian kernel lifts the same clicks to 0.67.
#
# ⚠ Do NOT raise this to make the map look better. Agreement rises monotonically
# with bandwidth because every player converges on the same featureless blob:
# measured, bw=12 m reaches 0.88 agreement but players are then 50% similar to
# each other, which is a prettier picture of nothing. Scored on reliability minus
# between-player similarity, 6 m is the optimum, and Silverman's rule on this data
# independently suggests 3.9 m.
HEATMAP_BANDWIDTH_M = 6.0


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


def drop_last_click(path: str | Path) -> dict | None:
    """Remove and return the most recently appended click.

    The file is append-only JSONL, so undo is a truncation: read every line,
    drop the last, rewrite. Rewriting via a temporary file and a replace keeps
    the log intact if the process dies mid-write -- losing a whole session's
    clicks to a botched undo would be far worse than the mistake being undone.
    """
    p = Path(path)
    if not p.exists():
        return None
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    removed = json.loads(lines[-1])
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("".join(l + "\n" for l in lines[:-1]))
    tmp.replace(p)
    return removed


def to_field(
    clicks: list[dict], field_cal, report: dict | None = None,
) -> list[dict]:
    """Project each click from equirect pixels to field metres.

    ⚠ Clicks near the FAR touchline project to negative width, and that is
    geometry rather than coach error. The far line is effectively the horizon
    here: measured on Game 1 at mid-pitch, pixel row 2030 maps to y = +0.4 m
    while 2019 -- eleven pixels higher -- maps to **-3.0 m**, and 1995 maps to
    -13.5 m. A player standing ON the far line is therefore a few pixels from
    reading as several metres off the pitch.

    Such clicks are CLAMPED to the touchline and counted, not discarded: the
    player was really there, and the depth error is a known property of the rig
    (see ACCURACY_AUDIT.md on far-touchline compression). A click far outside the
    pitch in either axis is a genuine mis-click and IS dropped.
    """
    from . import calibration as _cal

    fp = _cal.FieldProjector(field_cal)
    L, W = float(field_cal.length_m), float(field_cal.width_m)
    rep = report if report is not None else {}
    rep.setdefault("clamped_far_touchline", 0)
    rep.setdefault("dropped_off_pitch", 0)
    out = []
    for c in clicks:
        if c.get("player_id") in (None, "__not_ours__"):
            continue
        x_m, y_m = fp.pixel_to_field(float(c["click_x_eq"]), float(c["click_y_eq"]))
        if np.isnan(x_m) or np.isnan(y_m):
            rep["dropped_off_pitch"] += 1
            continue
        # Beyond the far line by a plausible projection error -> clamp.
        if -FAR_CLAMP_M <= y_m < 0.0:
            y_m = 0.0
            rep["clamped_far_touchline"] += 1
        elif y_m > W + FAR_CLAMP_M or y_m < -FAR_CLAMP_M \
                or x_m < -FAR_CLAMP_M or x_m > L + FAR_CLAMP_M:
            rep["dropped_off_pitch"] += 1
            continue
        out.append({**c, "x_m": float(np.clip(x_m, 0.0, L)),
                    "y_m": float(np.clip(y_m, 0.0, W))})
    return out


def kde_heatmap(
    d: np.ndarray, w: np.ndarray, length_m: float, width_m: float,
    shape: tuple[int, int], bandwidth_m: float = HEATMAP_BANDWIDTH_M,
) -> np.ndarray:
    """Occupancy grid by kernel density rather than binning.

    Every click contributes a Gaussian bump, so nearby clicks reinforce and the
    estimate is defined everywhere instead of only where a click happened to
    land. Measured on the coach's clicks, this is what makes a fine grid usable
    at all: split-half agreement at 12x8 rises from 0.15 (histogram) to 0.67.

    Returns a `shape` grid with rows along DEPTH and columns along WIDTH,
    normalised to sum to 1.
    """
    gx, gy = shape
    cx = (np.arange(gx) + 0.5) * (length_m / gx)          # depth centres
    cy = (np.arange(gy) + 0.5) * (width_m / gy)           # width centres
    DX = cx[:, None, None] - np.asarray(d)[None, None, :]
    DY = cy[None, :, None] - np.asarray(w)[None, None, :]
    k = np.exp(-(DX * DX + DY * DY) / (2.0 * bandwidth_m * bandwidth_m))
    grid = k.sum(axis=2)
    s = grid.sum()
    return grid / s if s > 0 else grid


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
    report: dict = {"n_clicks_total": len(clicks), "under_sampled": [],
                    "min_clicks": min_clicks}
    pts = to_field(clicks, field_cal, report)
    report["n_usable"] = len(pts)
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

        hm = kde_heatmap(d, w, L, W, heatmap_shape)
        tot = max(1e-12, hm.sum())

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

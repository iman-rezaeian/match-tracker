#!/usr/bin/env python3
"""Publish click-derived per-player POSITION stats to the analytics doc.

Why a separate key
------------------
These numbers do NOT belong in `player_stats`. That array is the tracking-derived
per-player deck, which is identity-dependent and known bad: roughly 23% of a
player's attributed frames belong to another child, so its distances run 3-4x low
and its positions drift toward whoever it confused him with. Click stats come from
the coach naming bodies himself, so they are a different and far more trustworthy
source. Merging them into one array would hide that distinction behind identical
styling and invite the coach to read across.

So they go to `click_stats`, and the PWA presents them as their own thing, with
their measured error bar attached.

What is published, and what is deliberately not
----------------------------------------------
Published: average position, territory (p10-p90 both axes), thirds occupancy,
side tendency, a 12x8 kernel-density heatmap, area covered, per-half positions,
and the half-to-half drift WITH its bootstrap confidence interval.

NOT published: distance, speed, sprints. A click samples a position; between two
samples 30 s apart a child could have run 5 m or 80 m and the clicks are
identical. No estimator recovers information that was never captured, and a
number on screen gets quoted regardless of its footnote.

Every player also carries `pos_err_m` -- the measured 1-sigma uncertainty on his
average position, from split-half resampling of his own clicks. The PWA shows it
rather than implying the numbers are exact.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_publish \
        --game-id mrhvbvwi1gjpn [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

# Below this many clicks a player is reported as under-sampled rather than given
# numbers: at 8 clicks the position error is ~20% of the gap between two children.
MIN_CLICKS_PUBLISH = 12
# Bootstrap draws for the error bars and the drift CI.
TRIALS = 800


def _boot_mean(v: np.ndarray, trials: int, rng) -> np.ndarray:
    n = len(v)
    return np.array([v[rng.integers(0, n, n)].mean() for _ in range(trials)])


def build_payload(game_id: str, root: Path) -> dict:
    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import (HEATMAP_BANDWIDTH_M, kde_heatmap,
                                        load_clicks, to_field)

    game = firestore_io.get_game(game_id)
    cal = firestore_io.get_game_calibration(game_id)
    if cal is None:
        raise SystemExit(f"{game_id} has no calibration — clicks cannot be projected")
    L, W = float(cal.length_m), float(cal.width_m)
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)

    clicks = load_clicks(root / "clicks.jsonl")
    if not clicks:
        raise SystemExit(f"no clicks in {root}")
    proj_report: dict = {}
    pts = to_field(clicks, cal, proj_report)

    def period_of(t: float) -> int:
        return 1 if t < h2 else 2

    net = our_net_at_x0_from_keeper(pts, game.gk_player_id, L, period_of)
    # Without an orientation the halves cannot be compared, so per-half figures
    # and drift are withheld rather than published in an undefined frame.
    oriented = net is not None

    rows: dict[str, dict] = {}
    for p in pts:
        per = period_of(float(p["video_time_s"]))
        flip = (not net.get(per, True)) if oriented else False
        e = rows.setdefault(str(p["player_id"]), {"d": [], "w": [], "half": []})
        e["d"].append((L - p["x_m"]) if flip else p["x_m"])
        e["w"].append((W - p["y_m"]) if flip else p["y_m"])
        e["half"].append(per)

    rng = np.random.default_rng(0)
    out, under = [], []
    for pid, v in rows.items():
        d = np.asarray(v["d"], float)
        w = np.asarray(v["w"], float)
        half = np.asarray(v["half"], int)
        if len(d) < MIN_CLICKS_PUBLISH:
            under.append({"player_id": pid, "n_clicks": int(len(d))})
            continue

        # measured 1-sigma on the mean position, by split-half resampling
        gaps = []
        for _ in range(TRIALS // 2):
            idx = rng.permutation(len(d))
            a, b = idx[: len(d) // 2], idx[len(d) // 2: 2 * (len(d) // 2)]
            gaps.append(np.hypot(d[a].mean() - d[b].mean(), w[a].mean() - w[b].mean()))
        pos_err = float(np.median(gaps)) / 2.0

        rec = {
            "player_id": pid,
            "n_clicks": int(len(d)),
            "avg_depth_m": round(float(d.mean()), 1),
            "avg_width_m": round(float(w.mean()), 1),
            "pos_err_m": round(pos_err, 1),
            "p10_depth_m": round(float(np.quantile(d, 0.10)), 1),
            "p90_depth_m": round(float(np.quantile(d, 0.90)), 1),
            "p10_width_m": round(float(np.quantile(w, 0.10)), 1),
            "p90_width_m": round(float(np.quantile(w, 0.90)), 1),
            "pct_defensive_third": round(100.0 * float((d < L / 3).mean()), 1),
            "pct_middle_third": round(
                100.0 * float(((d >= L / 3) & (d < 2 * L / 3)).mean()), 1),
            "pct_attacking_third": round(100.0 * float((d >= 2 * L / 3).mean()), 1),
            "pct_left": round(100.0 * float((w < W / 3).mean()), 1),
            "pct_centre": round(
                100.0 * float(((w >= W / 3) & (w < 2 * W / 3)).mean()), 1),
            "pct_right": round(100.0 * float((w >= 2 * W / 3).mean()), 1),
            # Area covered: a 2-sd ellipse, robust with a few dozen samples. A
            # work-rate PROXY, comparable between players because everyone is
            # sampled on the same frame grid. NOT metres run.
            "area_covered_m2": round(float(np.pi * 2 * d.std() * 2 * w.std())),
            # FLAT row-major, not a list of lists: Firestore rejects a nested
            # array with "invalid nested entity". Shape travels alongside in
            # `heatmap_shape` so the PWA can rebuild the grid.
            "heatmap": [round(float(v), 4)
                        for v in kde_heatmap(d, w, L, W, (12, 8)).ravel()],
        }

        if oriented:
            per_half = {}
            for h in (1, 2):
                m = half == h
                if m.sum() >= 5:
                    per_half[str(h)] = {
                        "n_clicks": int(m.sum()),
                        "avg_depth_m": round(float(d[m].mean()), 1),
                        "avg_width_m": round(float(w[m].mean()), 1),
                    }
            rec["by_half"] = per_half
            if len(per_half) == 2:
                a, b = d[half == 1], d[half == 2]
                dif = _boot_mean(b, TRIALS, rng) - _boot_mean(a, TRIALS, rng)
                lo, hi = (float(x) for x in np.percentile(dif, [2.5, 97.5]))
                rec["drift"] = {
                    "depth_m": round(float(np.mean(dif)), 1),
                    "ci_low_m": round(lo, 1),
                    "ci_high_m": round(hi, 1),
                    # A drift whose interval spans zero is not evidence of
                    # anything; the PWA must not render it as a finding.
                    "significant": bool((lo > 0) == (hi > 0)),
                }
        out.append(rec)

    out.sort(key=lambda r: r["avg_depth_m"])
    errs = [r["pos_err_m"] for r in out]
    return {
        "click_stats": {
            "players": out,
            "under_sampled": under,
            "n_clicks": len(clicks),
            "n_frames": len({round(float(c["video_time_s"]), 2) for c in clicks}),
            "field_length_m": round(L, 1),
            "field_width_m": round(W, 1),
            "oriented": oriented,
            "median_pos_err_m": round(float(np.median(errs)), 1) if errs else None,
            "heatmap_shape": [12, 8],
            "heatmap_bandwidth_m": HEATMAP_BANDWIDTH_M,
            "min_clicks": MIN_CLICKS_PUBLISH,
            "clamped_far_touchline": proj_report.get("clamped_far_touchline", 0),
            "dropped_off_pitch": proj_report.get("dropped_off_pitch", 0),
            "source": "coach_clicks",
        }
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    payload = build_payload(args.game_id, root)
    cs = payload["click_stats"]
    print(f"{len(cs['players'])} players from {cs['n_clicks']} clicks over "
          f"{cs['n_frames']} frames; median error {cs['median_pos_err_m']} m")
    if cs["under_sampled"]:
        print(f"  under {cs['min_clicks']} clicks (withheld): "
              f"{[u['player_id'] for u in cs['under_sampled']]}")
    if not cs["oriented"]:
        print("  ⚠ halves NOT oriented — per-half figures and drift withheld")

    if args.dry_run:
        print("\n--dry-run: nothing written. Payload size "
              f"{len(json.dumps(payload)) / 1024:.0f} KB")
        return

    from post_game import firestore_io
    firestore_io.write_analytics_merge(args.game_id, payload)
    print(f"\nwrote click_stats to analytics/{args.game_id} (merge — other keys "
          "untouched)")


if __name__ == "__main__":
    main()

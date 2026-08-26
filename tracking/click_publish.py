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
# A per-ROLE heatmap needs its own samples. 25 is the coach's chosen bar
# (2026-08-25): at ~20 clicks the position error is ~15% of the gap between two
# children, so 25 keeps a role map honest while still unlocking within a couple
# of clicked games. Roles under it publish their minutes and click count but no
# map, so the app can show a "pooling" state instead of a misleading sparse grid.
MIN_CLICKS_ROLE = 25
# The KEEPER's phase split needs no tactical board: his job changes with where
# the REST of the team is. A frame counts as "we were attacking" when the mean
# depth of the tagged outfielders is past the halfway line — a mean rather than
# a headcount because tags per frame vary (median 6 outfielders), and a mean
# position is the steadier statistic at that sample size. Frames with fewer than
# this many tagged outfielders carry no reliable team position, so they are
# excluded from both phases rather than guessed into one.
GK_MIN_OUTFIELD = 4


def _boot_mean(v: np.ndarray, trials: int, rng) -> np.ndarray:
    n = len(v)
    return np.array([v[rng.integers(0, n, n)].mean() for _ in range(trials)])


def build_payload(game_id: str, root: Path) -> dict:
    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game import roles as roles_mod
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

    # Role stints from the tactical board (post_game.roles). Clicks get tagged
    # with the role in force at that moment, so a child who played wide-mid then
    # forward yields two separate position maps instead of one blended blob.
    half_s = float(getattr(game, "half_length_min", 25) or 25) * 60.0
    h1 = float(getattr(game, "video_offset_h1_kickoff_s", 0.0) or 0.0)
    stints = roles_mod.build_stints(
        game.events, half_s,
        getattr(game, "starting_lineup", None), game.gk_player_id)

    def game_clock(video_t: float) -> float:
        """Video seconds -> game clock, using the confirmed kickoff offsets."""
        return (video_t - h2) + half_s if (h2 and video_t >= h2) else (video_t - h1)

    net = our_net_at_x0_from_keeper(pts, game.gk_player_id, L, period_of)
    # Without an orientation the halves cannot be compared, so per-half figures
    # and drift are withheld rather than published in an undefined frame.
    oriented = net is not None

    rows: dict[str, dict] = {}
    for p in pts:
        per = period_of(float(p["video_time_s"]))
        flip = (not net.get(per, True)) if oriented else False
        e = rows.setdefault(str(p["player_id"]),
                            {"d": [], "w": [], "half": [], "role": []})
        e["d"].append((L - p["x_m"]) if flip else p["x_m"])
        e["w"].append((W - p["y_m"]) if flip else p["y_m"])
        e["half"].append(per)
        e["role"].append(roles_mod.role_at(
            stints.get(str(p["player_id"]), []),
            game_clock(float(p["video_time_s"]))))

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
        # --- per-ROLE positions (the coach plays children in several roles) ---
        # Minutes come from the board+SUB stints, so EVERY role he played is
        # listed even when it has too few clicks to map. That distinction is the
        # point: the app shows the minutes always, the heatmap only when earned.
        pstints = stints.get(pid, [])
        if pstints:
            rmins = roles_mod.minutes_by_role(pstints)
            rarr = np.asarray([r or "" for r in v["role"]])
            per_role = []
            for role, mins in rmins.items():
                m = rarr == role
                blk = {"role": role, "minutes": mins, "n_clicks": int(m.sum())}
                if m.sum() >= MIN_CLICKS_ROLE:
                    dr, wr = d[m], w[m]
                    blk.update({
                        "avg_depth_m": round(float(dr.mean()), 1),
                        "avg_width_m": round(float(wr.mean()), 1),
                        "p10_depth_m": round(float(np.quantile(dr, 0.10)), 1),
                        "p90_depth_m": round(float(np.quantile(dr, 0.90)), 1),
                        "area_covered_m2": round(float(np.pi * 2 * dr.std() * 2 * wr.std())),
                        "heatmap": [round(float(z), 4)
                                    for z in kde_heatmap(dr, wr, L, W, (12, 8)).ravel()],
                    })
                per_role.append(blk)
            rec["by_role"] = per_role
            rec["minutes_by_role"] = rmins

        out.append(rec)

    # --- KEEPER: one map for our attacking phase, one for defending ----------
    # Measured on the two tagged games: the split lands 43/46 and 21/17 frames,
    # both sides comfortably over MIN_CLICKS_ROLE, so the maps render at once.
    # The depth difference itself is SMALL and not significant on a single game
    # (Jul-12 G1: 2.5 m out attacking vs 1.7 m defending, p=0.11), which is why
    # both figures are published for the app to state plainly — two similar
    # blobs must not be left to imply a difference the data has not earned.
    gk = game.gk_player_id
    if gk and str(gk) in rows:
        frames: dict[float, list[dict]] = {}
        for pt in pts:
            frames.setdefault(round(float(pt["video_time_s"]), 2), []).append(pt)
        phase_pts: dict[str, dict[str, list[float]]] = {
            "attacking": {"d": [], "w": []}, "defending": {"d": [], "w": []}}
        for t, cs in frames.items():
            per = period_of(t)
            flip = (not net.get(per, True)) if oriented else False
            def _d(c):
                return (L - c["x_m"]) if flip else c["x_m"]
            def _w(c):
                return (W - c["y_m"]) if flip else c["y_m"]
            outfield = [_d(c) for c in cs if str(c["player_id"]) != str(gk)]
            keeper = [(_d(c), _w(c)) for c in cs if str(c["player_id"]) == str(gk)]
            if len(outfield) < GK_MIN_OUTFIELD or not keeper:
                continue
            ph = "attacking" if (sum(outfield) / len(outfield)) > L / 2 else "defending"
            phase_pts[ph]["d"].append(keeper[0][0])
            phase_pts[ph]["w"].append(keeper[0][1])
        gk_rec = next((r for r in out if r["player_id"] == str(gk)), None)
        if gk_rec is not None:
            blocks = []
            for ph in ("attacking", "defending"):
                dd = np.asarray(phase_pts[ph]["d"], float)
                ww = np.asarray(phase_pts[ph]["w"], float)
                blk = {"phase": ph, "n_clicks": int(len(dd))}
                if len(dd):
                    blk["avg_depth_m"] = round(float(dd.mean()), 1)
                if len(dd) >= MIN_CLICKS_ROLE:
                    blk["heatmap"] = [round(float(z), 4) for z in
                                      kde_heatmap(dd, ww, L, W, (12, 8)).ravel()]
                blocks.append(blk)
            if any(b["n_clicks"] for b in blocks):
                gk_rec["by_gk_phase"] = blocks

    out.sort(key=lambda r: r["avg_depth_m"])
    errs = [r["pos_err_m"] for r in out]
    # Role TIMELINE for every player who took the field — independent of
    # clicks, so it lands on games that were never click-sampled.
    timeline = []
    for pid, ss in stints.items():
        timeline.append({
            "player_id": pid,
            "total_minutes": round(sum(s.seconds for s in ss) / 60.0, 1),
            "minutes_by_role": roles_mod.minutes_by_role(ss),
            "stints": [{"start_s": round(s.start_s, 1), "end_s": round(s.end_s, 1),
                        "role": s.role} for s in ss],
        })
    timeline.sort(key=lambda r: -r["total_minutes"])

    return {
        "click_stats": {
            "players": out,
            "role_timeline": timeline,
            "half_length_s": half_s,
            "min_clicks_role": MIN_CLICKS_ROLE,
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

    # Refresh the season-view projection, or the PWA keeps showing this game as
    # untagged: the season table and the pooled heatmap read analytics/summary,
    # not analytics/v1, and only the pipeline used to write it. Rebuilt from the
    # STORED doc rather than from `payload`, which holds click_stats alone —
    # summarising the partial write would blank player_stats and field_tilt.
    full = firestore_io.read_analytics(args.game_id)
    if full:
        firestore_io.write_analytics_summary(args.game_id, full)
        print("refreshed analytics/summary so the season view sees the tags")
    else:
        print("⚠ could not re-read the doc — analytics/summary NOT refreshed; "
              "the PWA will still show this game as untagged")


if __name__ == "__main__":
    main()

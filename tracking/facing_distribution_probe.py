#!/usr/bin/env python3
"""Why does jersey-number reading stall at ~28% coverage — facing, or distance?

`JERSEY_NUMBER_DECISION.md` attributes the ceiling to three causes at once —
"facing the wrong way, too far away, or occluded" — without splitting them. The
three have completely different fixes, so the bundling hides the decision:

  facing   -> a marker the camera can see from more angles
  too far  -> bigger numbers, or nothing (optics)
  occluded -> neither

This probe splits them, and the answer on both clean games is DISTANCE, not
facing. Measured on mri01pvelv46d (54.7x30.3 m pitch, rig at y=34.6 m):

  * Backs ARE turned: among frames where the player is actually moving, 38% are
    back-to-camera and only 22% are side-on profile. 99% of tracklets turn their
    back at some point. Facing is NOT the limiter, and a sideline rig does not
    mostly see profile — play runs radially to a camera parked beyond the far
    touchline, not across it.
  * The digits are the limiter: the MEDIAN tracklet's best available digit is
    ~19 px, and a quarter of tracklets top out in the 14-20 px band — passing
    the size gate while being genuinely illegible smears of moving fabric.
  * So ~71% of tracklets clear the prescreen and reach the VLM, but only ~28%
    come back with a number. The loss is at the READ, and it is optical.

Corollary for anyone reaching for a cheap fix: no marker printed on cloth
(front numbers, sleeve patches, sock colours) survives 5 px/m in the far band.
Only making the existing numbers physically BIGGER has a mechanism, and it only
reaches the ~25% of tracklets stuck in the 14-20 px band.

Thresholds are read from `post_game.config` (VLM_MIN_DIGIT_PX / VLM_MIN_AWAY)
rather than duplicated, so this keeps measuring production as production
changes. Crop SELECTION mirrors `vlm_identity._readable_rows` (height + 1.5x
away-ness, top-k), because the prescreen only ever sees the selected crops.

Cheap by construction: reads the cached `tracks_raw.parquet`, projects with the
same `FieldProjector` the pipeline uses, and infers facing from motion. **No
video decode, no VLM calls, no API key** — so it re-runs on games whose raw
footage is long deleted. Read-only; opens Firestore for the calibration and
writes nothing back.

Run:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.facing_distribution_probe \\
        --game-id mri01pvelv46d --game-id mrhvbvwi1gjpn
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_game import config, firestore_io                    # noqa: E402
from post_game.calibration import FieldProjector              # noqa: E402
from post_game.pipeline import _camera_ground_xy              # noqa: E402
from tracking.vlm_identity import _readable_rows              # noqa: E402

# `away` is a cosine, so these cut the unit circle into three honest bands: 60
# deg either side of "straight away"/"straight at", leaving the middle third as
# profile. Reporting-only — the pass/fail gates come from config.
_AWAY_COS = 0.5
_TOWARD_COS = -0.5
# Below this per-step displacement the direction of travel is noise, not facing.
# Same constant `_readable_rows` uses to zero out a standing player.
_MIN_STEP_M = 0.05
# A squad number is ~17% of body height on this footage (JERSEY_NUMBER_DECISION).
_DIGIT_FRAC = 0.17
# How many crops the production draft run feeds the VLM per tracklet.
_CROPS = 6


def _facing(df: pd.DataFrame, cam_xy: tuple[float, float]) -> pd.DataFrame:
    """Attach `away` = cos(travel direction, camera->player ray), per detection.

    +1 is running straight away from the rig (back turned, number visible), -1
    straight at it. Frames where the player is essentially still get 0: standing
    still says nothing about facing, and counting those as "profile" is exactly
    the artefact that made a first pass of this probe report 54% profile when
    the moving-only figure is 22%.
    """
    cx, cy = cam_xy
    out = []
    for _tid, sub in df.groupby("track_id", sort=False):
        s = sub.sort_values("time_s")
        if len(s) < 3:
            continue
        x = s["x_m"].to_numpy(dtype=float)
        y = s["y_m"].to_numpy(dtype=float)
        dx, dy = np.gradient(x), np.gradient(y)
        rx, ry = x - cx, y - cy
        rn, sn = np.hypot(rx, ry), np.hypot(dx, dy)
        with np.errstate(invalid="ignore", divide="ignore"):
            away = (dx * rx + dy * ry) / np.where(rn * sn > 0, rn * sn, np.nan)
        away = np.nan_to_num(away, nan=0.0)
        away[sn < _MIN_STEP_M] = 0.0
        s = s.copy()
        s["away"] = away
        s["moving"] = sn >= _MIN_STEP_M
        out.append(s)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _load(game_id: str, min_track_s: float) -> tuple[pd.DataFrame, tuple[float, float], float, float]:
    field_cal = firestore_io.get_game_calibration(game_id)
    if field_cal is None:
        raise SystemExit(f"{game_id}: no calibration in Firestore — cannot project to metres.")
    cam_xy = _camera_ground_xy(field_cal)
    if cam_xy is None:
        raise SystemExit(f"{game_id}: planar calibration has no camera position (sphere model required).")
    pq = config.OUTPUTS_DIR / game_id / "tracks_raw.parquet"
    if not pq.exists():
        raise SystemExit(f"{game_id}: no cached tracks at {pq}")

    df = pd.read_parquet(pq)
    proj = FieldProjector(field_cal)
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]

    L, W = field_cal.length_m, field_cal.width_m
    df = df[(df["x_m"] >= -1.5) & (df["x_m"] <= L + 1.5)
            & (df["y_m"] >= -1.5) & (df["y_m"] <= W + 1.5)].reset_index(drop=True)

    # Restrict to substantial tracks: a 2-second fragment has no identity worth
    # assigning, so its facing mix would dilute the number that matters.
    span = df.groupby("track_id")["time_s"].agg(["min", "max"])
    keep = span[(span["max"] - span["min"]) >= min_track_s].index
    df = df[df["track_id"].isin(keep)].reset_index(drop=True)
    if df.empty:
        raise SystemExit(f"{game_id}: no tracks longer than {min_track_s}s.")
    return df, cam_xy, L, W


def analyse(game_id: str, min_track_s: float = 30.0) -> dict:
    df, cam_xy, L, W = _load(game_id, min_track_s)
    min_digit = config.VLM_MIN_DIGIT_PX
    min_away = config.VLM_MIN_AWAY

    f = _facing(df, cam_xy)
    if f.empty:
        raise SystemExit(f"{game_id}: no tracks with enough samples to infer facing.")
    f["h_px"] = f["y2_eq"] - f["y1_eq"]
    f["digit_px"] = _DIGIT_FRAC * f["h_px"]

    moving = f[f["moving"]]

    def _bands(frame: pd.DataFrame) -> dict:
        if frame.empty:
            return {}
        a = frame["away"].to_numpy()
        return {"away": float((a >= _AWAY_COS).mean()),
                "profile": float(((a > _TOWARD_COS) & (a < _AWAY_COS)).mean()),
                "toward": float((a <= _TOWARD_COS).mean())}

    # What production actually inspects: the top-k crops `_readable_rows` picks,
    # not every frame. Reusing the real selector keeps this honest if its
    # scoring changes.
    best_digit, best_away, passes = [], [], []
    for _tid, sub in f.groupby("track_id", sort=False):
        sel = _readable_rows(sub, _CROPS, cam_xy=cam_xy)
        if sel is None or sel.empty:
            continue
        d = float((_DIGIT_FRAC * (sel["y2_eq"] - sel["y1_eq"])).max())
        a = float(sel["away"].max()) if "away" in sel.columns else float(sub["away"].max())
        best_digit.append(d)
        best_away.append(a)
        passes.append(d >= min_digit and a >= min_away)
    bd = np.asarray(best_digit, dtype=float)
    ba = np.asarray(best_away, dtype=float)

    # Size histogram of the BEST frame each tracklet can offer. The 14-20 px
    # bucket is the interesting one: it passes the gate and is still illegible.
    edges = [0.0, min_digit, min_digit + 6.0, 30.0, np.inf]
    hist = {}
    for lo, hi in zip(edges[:-1], edges[1:]):
        key = f"{lo:.0f}-{'inf' if np.isinf(hi) else f'{hi:.0f}'}px"
        hist[key] = round(float(np.mean((bd >= lo) & (bd < hi))), 3) if len(bd) else 0.0

    return {
        "game_id": game_id,
        "camera_xy_m": [round(cam_xy[0], 2), round(cam_xy[1], 2)],
        "field_m": [round(L, 1), round(W, 1)],
        "gates": {"min_digit_px": min_digit, "min_away": min_away, "crops": _CROPS},
        "tracks": int(f["track_id"].nunique()),
        "frames": int(len(f)),
        "moving_frac": round(float(f["moving"].mean()), 3),
        "frame_bands_all": {k: round(v, 3) for k, v in _bands(f).items()},
        "frame_bands_moving": {k: round(v, 3) for k, v in _bands(moving).items()},
        "tracklets_ever_away": round(float((ba >= _AWAY_COS).mean()), 3) if len(ba) else 0.0,
        "tracklets_pass_prescreen": round(float(np.mean(passes)), 3) if passes else 0.0,
        "best_digit_px_pcts": {f"p{p}": round(float(np.percentile(bd, p)), 1)
                               for p in (10, 25, 50, 75, 90)} if len(bd) else {},
        "best_digit_px_hist": hist,
    }


def _report(r: dict) -> None:
    g = r["gates"]
    bm, ba_ = r["frame_bands_moving"], r["frame_bands_all"]
    print(f"\n=== facing vs distance — {r['game_id']} ===")
    print(f"camera at {r['camera_xy_m']} m on a {r['field_m'][0]}x{r['field_m'][1]} m pitch")
    print(f"{r['tracks']} tracks >=30s, {r['frames']:,} detections, "
          f"{r['moving_frac']:.0%} of frames moving")
    print(f"gates from config: digit>={g['min_digit_px']:.0f}px, away>={g['min_away']:+.2f}, "
          f"{g['crops']} crops/tracklet\n")

    print("per-FRAME facing            all / moving-only:")
    for k, label in (("away", "away  (back turned, number visible)"),
                     ("profile", "profile (side-on, no number either side)"),
                     ("toward", "toward (chest-on)")):
        print(f"  {label:<42} {ba_[k]:>6.1%} / {bm.get(k, 0):>6.1%}")

    print("\nbest digit size each tracklet can offer (top-%d crops):" % g["crops"])
    p = r["best_digit_px_pcts"]
    print("  " + "  ".join(f"{k}={v}px" for k, v in p.items()))
    for k, v in r["best_digit_px_hist"].items():
        note = "  <- passes the gate but is an illegible smear" if k.startswith(
            f"{g['min_digit_px']:.0f}-") else ""
        print(f"  {k:<12} {v:>6.1%}{note}")

    print(f"\ntracklets that ever turn their back:   {r['tracklets_ever_away']:.1%}")
    print(f"tracklets that pass the prescreen:     {r['tracklets_pass_prescreen']:.1%}")
    print("measured VLM coverage (JERSEY_NUMBER_DECISION.md): ~28%")

    print("\ninterpretation:")
    if r["tracklets_ever_away"] > 0.9 and r["tracklets_pass_prescreen"] > 0.5:
        print("  FACING IS NOT THE LIMITER — nearly every tracklet turns its back, and")
        print("  most clear the prescreen. The gap between prescreen-pass and actual")
        print("  coverage is the READ failing on digits that are too small.")
        print("  -> markers on cloth (front numbers, sleeve patches) will NOT help;")
        print("     only physically bigger digits, and only for the mid-size band.")
    else:
        print("  Facing may be contributing — compare 'ever turns its back' against")
        print("  the prescreen pass rate to see which gate is binding.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", action="append", required=True,
                    help="repeatable; pool evidence across games")
    ap.add_argument("--min-track-s", type=float, default=30.0)
    ap.add_argument("--json", type=Path, help="also write the raw numbers here")
    args = ap.parse_args()

    results = []
    for gid in args.game_id:
        try:
            r = analyse(gid, args.min_track_s)
        except SystemExit as e:
            print(f"[skip] {e}")
            continue
        results.append(r)
        _report(r)

    if args.json and results:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()

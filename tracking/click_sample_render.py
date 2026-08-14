#!/usr/bin/env python3
"""Pre-render sampling frames so the coach can click players without waiting.

Why pre-render
--------------
A random seek into the 8K H.265 source costs ~0.9 s (measured on
VID_20260712_Game1.mp4, 7680x3840 @ 29.97 fps). Doing that inside the labeling
loop would mean the coach waits on the disk for a quarter of the session.
Rendering once, offline, turns a slow interactive job into a fast one -- the
same reason `stint_label_render.py` exists.

Why the frames are SPLIT into stacked bands
-------------------------------------------
This is the design decision the whole tool turns on, so it is recorded here.

The pitch occupies a narrow horizontal strip of the equirect frame -- the crop is
~3640 px wide and, once trimmed to the rows players occupy, only ~665 px tall.
Rendering that as ONE row on a screen shrinks the players below recognition,
because fitting 3640 px into a ~1200 px column scales everything by a third. The
median detection box is 77 px tall and 63% are under 100 px, so anything below
~60 px is hopeless for naming a same-kit child.

Splitting the strip into stacked bands keeps native resolution while fitting a
screen. Three bands over this crop gives ~1213 px per band at scale 1.0, which
renders a median player at ~102 px -- the coach confirmed he can read jersey
numbers at that size.

⚠ Do NOT pass a `--band-w` larger than `crop_width / bands`. That UPSCALES: it
inflates the image and the disk cost without adding any detail. `--band-w 0`
(the default) picks the native width. An earlier render at `--band-w 1600` over a
1213 px segment was resampling by 1.32x for nothing.

Trimming the crop is what makes this work. The first version padded the top by
the 98th-percentile box height, which is driven by near-camera ADULTS, so half the
image was sky, treeline and empty foreground: 1291 px tall of which only 663 held
any player. Cropping to the actual head-and-foot rows of player-sized bodies
halves the height, which is what allows three bands instead of two.

Sampling instants
-----------------
The app -- not the coach -- chooses WHEN to sample. Measured: clicks bunched
into a few viewing windows plateau the position error at ~170 px no matter how
many are added, while spread clicks reach 49 px at 50 per player. If the coach
picks the moments he picks where the ball is, which is exactly the clustered
case. So instants are a fixed grid across both halves.

Halftime is skipped using the coach's kickoff offsets, which are on the game doc
(`video_offset_h1_kickoff_s`, `video_offset_h2_kickoff_s`) and are the same
anchors the rest of the pipeline uses.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_sample_render \\
        --game-id mrhvbvwi1gjpn --interval 30 --limit 20
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# The pitch strip in equirect pixels, as a fraction-free absolute box. Derived
# from the detection distribution (99% interval) rather than hardcoded geometry,
# then padded so a player at the edge is not half-cropped.
PAD_PX = 60
# A player must render at roughly this height to be nameable; drives band count.
MIN_PLAYER_PX = 60.0


def pitch_bbox(tracks_df: pd.DataFrame, pad: int = PAD_PX) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) from where bodies are. FALLBACK ONLY — prefer
    `pitch_bbox_from_calibration`.

    Kept because it needs no calibration, but it is measurably polluted: the
    detection cloud includes touchline spectators, the team tent and bodies on
    ADJACENT pitches at this venue, so the resulting crop showed tents and
    neighbouring games while clipping the near touchline. Verified by eye on the
    first render.
    """
    x0 = float(tracks_df.foot_x_eq.quantile(0.005)) - pad
    x1 = float(tracks_df.foot_x_eq.quantile(0.995)) + pad
    y0 = float(tracks_df.foot_y_eq.quantile(0.005)) - pad * 2
    y1 = float(tracks_df.foot_y_eq.quantile(0.995)) + pad
    return int(max(0, x0)), int(max(0, y0)), int(x1), int(y1)


def pitch_bbox_from_calibration(
    field_cal, tracks_df: pd.DataFrame | None = None,
) -> tuple[int, int, int, int]:
    """The play area, defined by the homography rather than by the bodies.

    Two subtleties, both found by measuring rather than assuming:

    * **Sample the boundary densely, not just the four corners.** In equirect
      the touchlines bow, so four corners understate the vertical extent
      (measured: corners give a 65 px band, a dense boundary gives 940 px).
    * **The projected boundary is the FEET plane.** Bodies extend UPWARD from
      their feet, so cropping to the feet band decapitates every player. Pad the
      top by the 98th-percentile on-pitch box height (~240 px here, since
      near-camera bodies are large) plus a margin.

    Result on Game 1: a 3640x1291 crop at aspect 2.82, which at 2 bands renders
    a median player at ~80 px -- comfortably above the ~60 px legibility floor.
    """
    from post_game import calibration as _cal

    fp = _cal.FieldProjector(field_cal)
    L, W = field_cal.length_m, field_cal.width_m
    pts = []
    for f in np.linspace(0.0, 1.0, 60):
        pts.append(fp.field_to_pixel(f * L, 0.0))
        pts.append(fp.field_to_pixel(f * L, W))
    for f in np.linspace(0.0, 1.0, 20):
        pts.append(fp.field_to_pixel(0.0, f * W))
        pts.append(fp.field_to_pixel(L, f * W))
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)

    # Trim to the rows PLAYERS actually occupy. Padding the top by the 98th
    # percentile box height (~240 px, driven by near-camera adults) left half the
    # crop as sky, treeline and empty foreground: 1291 px tall of which only 663
    # held any player. Halving the height is what lets the band render at 3
    # segments and ~100 px per player instead of 80.
    top_pad, bottom_pad = 260, 40
    if tracks_df is not None and "bbox_h_crop" in tracks_df.columns:
        on = tracks_df[
            (tracks_df.foot_x_eq > xs.min()) & (tracks_df.foot_x_eq < xs.max())
            & (tracks_df.foot_y_eq > ys.min() - 20) & (tracks_df.foot_y_eq < ys.max() + 20)
            & (tracks_df.bbox_h_crop < 120)]        # players, not touchline adults
        if len(on) > 100:
            # Highest head and lowest foot among actual players, plus a margin.
            head = float((on.foot_y_eq - on.bbox_h_crop).quantile(0.005))
            foot = float(on.foot_y_eq.quantile(0.995))
            return (int(max(0, xs.min() - 30)), int(max(0, head - 25)),
                    int(xs.max() + 30), int(foot + 35))
    return (int(max(0, xs.min() - 30)), int(max(0, ys.min() - top_pad)),
            int(xs.max() + 30), int(ys.max() + bottom_pad))


def sample_times(
    h1_kick: float, h2_kick: float, duration_s: float, interval: float,
    half_len_s: float = 25 * 60,
) -> list[float]:
    """Fixed grid of video-time instants across both halves, halftime skipped.

    Deliberately NOT coach-chosen -- see the module docstring on clustering.
    """
    out: list[float] = []
    for kick in (h1_kick, h2_kick):
        if kick is None:
            continue
        t = float(kick)
        end = min(float(kick) + half_len_s, duration_s)
        while t < end:
            out.append(round(t, 2))
            t += interval
    return sorted(set(out))


def render_frame(
    frame: np.ndarray, box: tuple[int, int, int, int], bands: int, band_w: int,
) -> tuple[np.ndarray, dict]:
    """Crop to the pitch strip and stack it into `bands` rows at ~native scale.

    Returns (canvas, geometry) where geometry lets a click on the canvas be
    mapped back to an equirect pixel -- the inverse transform the app needs.
    """
    x0, y0, x1, y1 = box
    strip = frame[y0:y1, x0:x1]
    sh, sw = strip.shape[:2]
    seg_w = sw // bands
    scale = band_w / seg_w
    seg_h = int(round(sh * scale))
    rows = []
    for i in range(bands):
        a = i * seg_w
        b = sw if i == bands - 1 else (i + 1) * seg_w
        seg = strip[:, a:b]
        rows.append(cv2.resize(seg, (band_w, seg_h), interpolation=cv2.INTER_AREA))
    canvas = np.vstack(rows)
    geom = {
        "box": [x0, y0, x1, y1],
        "bands": bands,
        "band_w": band_w,
        "band_h": seg_h,
        "seg_w": seg_w,
        "scale": scale,
        "canvas": [canvas.shape[1], canvas.shape[0]],
    }
    return canvas, geom


def canvas_to_equirect(cx: float, cy: float, geom: dict) -> tuple[float, float]:
    """Inverse of `render_frame`: a canvas click -> equirect pixel.

    Kept next to the forward transform on purpose; a click tool whose inverse
    lives in another file drifts out of sync silently and every position is then
    quietly wrong.
    """
    band = int(cy // geom["band_h"])
    band = max(0, min(geom["bands"] - 1, band))
    y_in = cy - band * geom["band_h"]
    x_strip = band * geom["seg_w"] + cx / geom["scale"]
    y_strip = y_in / geom["scale"]
    return geom["box"][0] + x_strip, geom["box"][1] + y_strip


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--interval", type=float, default=30.0)
    # 3 bands over a ~3640 px crop is ~1213 px per band, so --band-w 1213 keeps
    # scale at 1.0. Rendering wider than that UPSCALES: it makes the image bigger
    # without adding detail, costing disk and screen for nothing.
    ap.add_argument("--bands", type=int, default=3)
    ap.add_argument("--band-w", type=int, default=0,
                    help="0 = native (no resampling), which is what you want")
    ap.add_argument("--limit", type=int, default=0, help="0 = all (pilot: 20)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from post_game import firestore_io

    game = firestore_io.get_game(args.game_id)
    url = game.video_url or ""
    path = url[len("file://"):] if url.startswith("file://") else url
    if not path or not Path(path).exists():
        raise SystemExit(f"video not on disk for {args.game_id}: {url!r}")

    root = Path(args.out or f"tracking/outputs/click_samples/{args.game_id}")
    root.mkdir(parents=True, exist_ok=True)

    tracks = pd.read_parquet(
        Path("post_game/outputs") / args.game_id / "tracks_raw.parquet")
    field_cal = firestore_io.get_game_calibration(args.game_id)
    if field_cal is not None:
        box = pitch_bbox_from_calibration(field_cal, tracks)
        log.info("crop from CALIBRATION: %s", box)
    else:
        box = pitch_bbox(tracks)
        log.warning("no calibration — falling back to the detection-quantile "
                    "crop, which is polluted by touchline and adjacent pitches")

    band_w = args.band_w or int((box[2] - box[0]) / max(1, args.bands))

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 29.97
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    times = sample_times(
        getattr(game, "video_offset_h1_kickoff_s", None),
        getattr(game, "video_offset_h2_kickoff_s", None), dur, args.interval)
    if args.limit:
        # Spread the pilot subset across the WHOLE match rather than taking the
        # first N, so a pilot cannot accidentally sample only the first minutes.
        idx = np.linspace(0, len(times) - 1, args.limit).round().astype(int)
        times = [times[i] for i in sorted(set(idx.tolist()))]

    log.info("rendering %d frames (%d bands @ %d px) -> %s",
             len(times), args.bands, band_w, root)
    index = []
    for i, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            log.warning("  seek failed at %.1fs — skipped", t)
            continue
        canvas, geom = render_frame(frame, box, args.bands, band_w)
        # Deciseconds in the name: two samples closer than a second must not
        # collide (a bug already fixed once in the stint clip renderer).
        name = f"f_{int(round(t*10)):07d}.jpg"
        cv2.imwrite(str(root / name), canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
        det = tracks[np.isclose(tracks.time_s, t, atol=0.06)]
        index.append({
            "video_time_s": t, "image": name, "geom": geom,
            "detections": [
                {"track_id": int(r.track_id),
                 "foot_x_eq": float(r.foot_x_eq), "foot_y_eq": float(r.foot_y_eq),
                 "bbox_h": float(r.bbox_h_crop)}
                for r in det.itertuples()],
        })
        if (i + 1) % 10 == 0:
            log.info("  %d/%d", i + 1, len(times))
    cap.release()

    (root / "index.json").write_text(json.dumps(
        {"game_id": args.game_id, "pitch_box": list(box),
         "player_px_estimate": round(77.0 * band_w / (box[2]-box[0]) * args.bands, 1),
         "frames": index}, indent=2))
    log.info("wrote %d frames + index.json", len(index))
    log.info("estimated player height on canvas: %.0f px (need >= %.0f)",
             77.0 * band_w / (box[2]-box[0]) * args.bands, MIN_PLAYER_PX)


if __name__ == "__main__":
    main()

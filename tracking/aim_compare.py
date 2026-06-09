"""Side-by-side aim comparison: OLD (legacy) vs NEW (full aim-quality).

Renders the SAME source-video window twice — once with the legacy aim
(density-along-X + boxcar moving average) and once with the new aim-quality
chain (sphere heat-map + Kalman lead + dead-zone hysteresis + event framing) —
then stacks them side by side with labels so the difference can be eyeballed.

Only the cheap aim stream differs between the two sides; the perspective render
is identical, so any visible difference is purely the aiming.

Labels are burned in with cv2.putText (no ffmpeg `drawtext` dependency — some
ffmpeg builds ship without libfreetype). The two crops are hconcat'd per frame
and written in a single encode pass.

Usage:
    GOOGLE_APPLICATION_CREDENTIALS=~/.config/stompers/firebase-adminsdk.json \
    FIRESTORE_PROJECT_ID=lasalle-stompers \
    python -m tracking.aim_compare --game-id mpyo67cl4uflh \
        --start 1046 --end 1072 --out /tmp/aim_compare.mp4
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from post_game import firestore_io, tv_view
from post_game.calibration import FieldProjector
from post_game.identity import period_clock_to_video_time_factory
from post_game.tv_aim import AimConfig
from post_game.video import H264PipeWriter, render_perspective

OLD_LABEL = "OLD: density-x + boxcar (fixed 70 FOV)"
NEW_LABEL = "NEW: same motion + auto zoom-out"


def _label(frame, text):
    """Burn a label into the top-left with a dark background box."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.9, 2
    (tw, th), bl = cv2.getTextSize(text, font, scale, thick)
    cv2.rectangle(frame, (12, 12), (12 + tw + 16, 12 + th + bl + 16), (0, 0, 0), -1)
    cv2.putText(frame, text, (20, 12 + th + 8), font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return frame


def _aim_stream(df, projector, a, b, L, W, cfg, events, clk):
    return tv_view._build_aim_stream(
        df, projector, a, b, L, W,
        aim_cfg=cfg, events=events, clock_to_video=clk,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--start", type=float, required=True, help="window start (source-video seconds)")
    ap.add_argument("--end", type=float, required=True, help="window end (source-video seconds)")
    ap.add_argument("--out", default="/tmp/aim_compare.mp4")
    ap.add_argument("--smoke", action="store_true", help="use the .smoke tracks checkpoint")
    ap.add_argument("--half-h", type=int, default=720, help="height of each side (px)")
    args = ap.parse_args()

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit(f"Game {args.game_id} has no calibration.")
    projector = FieldProjector(cal)
    L, W = cal.length_m, cal.width_m
    video_path = game.video_url.replace("file://", "")
    if not Path(video_path).exists():
        raise SystemExit(f"Source video not found: {video_path}")

    df = tv_view.load_tracks_field_df(args.game_id, projector, L, W, smoke=args.smoke)
    clk = period_clock_to_video_time_factory(game)

    legacy = AimConfig(
        aim_mode="density_x", motion_model="legacy_boxcar",
        use_event_framing=False, use_learned=False, use_dynamic_fov=False,
        base_fov_deg=tv_view.TV_FOV_DEG, out_w=tv_view.TV_RESOLUTION[0],
        out_h=tv_view.TV_RESOLUTION[1], aim_hz=tv_view.TV_AIM_HZ,
        boxcar_window=tv_view.TV_SMOOTH_WINDOW,
    )
    new = AimConfig(
        aim_mode="density_x", motion_model="legacy_boxcar",
        use_event_framing=True, use_learned=False, use_dynamic_fov=True,
        base_fov_deg=tv_view.TV_FOV_DEG, out_w=tv_view.TV_RESOLUTION[0],
        out_h=tv_view.TV_RESOLUTION[1], aim_hz=tv_view.TV_AIM_HZ,
        boxcar_window=tv_view.TV_SMOOTH_WINDOW,
    )

    print("Building aim streams...")
    ot, olon, olat, ofov = _aim_stream(df, projector, args.start, args.end, L, W, legacy, game.events, clk)
    nt, nlon, nlat, nfov = _aim_stream(df, projector, args.start, args.end, L, W, new, game.events, clk)

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out_w, out_h = tv_view.TV_RESOLUTION

    half_h = args.half_h
    half_w = int(round(half_h * out_w / out_h))   # keep 16:9
    total_w = half_w * 2 + 4                       # +4 px divider

    writer = H264PipeWriter(args.out, fps, total_w, half_h, crf=18, preset="medium")

    start_f = int(round(args.start * fps))
    end_f = int(round(args.end * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    print(f"Rendering {end_f - start_f} frames side by side -> {args.out}")
    for f in range(start_f, end_f):
        ok, frame = cap.read()
        if not ok:
            break
        t = f / fps

        olon_uw = float(np.interp(t, ot, olon)); olat_v = float(np.interp(t, ot, olat))
        ofov_v = float(np.interp(t, ot, ofov))
        old_crop = render_perspective(
            frame, ((olon_uw + 180) % 360) - 180, olat_v, ofov_v,
            out_w, out_h, interp=cv2.INTER_LANCZOS4,
        )

        nlon_uw = float(np.interp(t, nt, nlon)); nlat_v = float(np.interp(t, nt, nlat))
        nfov_v = float(np.interp(t, nt, nfov))
        new_crop = render_perspective(
            frame, ((nlon_uw + 180) % 360) - 180, nlat_v, nfov_v,
            out_w, out_h, interp=cv2.INTER_LANCZOS4,
        )

        old_s = _label(cv2.resize(old_crop, (half_w, half_h)), OLD_LABEL)
        new_s = _label(cv2.resize(new_crop, (half_w, half_h)), NEW_LABEL)
        divider = np.full((half_h, 4, 3), 255, dtype=np.uint8)
        writer.write(np.hstack([old_s, divider, new_s]))

    writer.close()
    cap.release()
    print(f"\nDone -> {args.out}  ({total_w}x{half_h})")


if __name__ == "__main__":
    main()

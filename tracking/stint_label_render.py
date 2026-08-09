#!/usr/bin/env python3
"""Render short annotated clips so the coach can label WHO the follower is on.

Why clips, and why video at all
-------------------------------
Identity here cannot come from a still frame. Measured on Game 1, the median
detection box is 77 px tall, 63% are under 100 px, and 38% under 60 — and the
jersey-number work already established that numbers only read on the BACK, with
74 of 105 tracklets returning no number at all. Nobody, model or human, can look
at a 77-pixel crop of a same-kit U10 and name the child.

What a human CAN do is watch someone move for a few seconds and say "yes, still
him" — identity by continuity. So the labeling unit is a short clip with the
followed body boxed, not a crop strip.

CAVEAT, found by looking at the first real render: at this crop scale names and
numbers ARE often legible — a sample frame clearly showed "HASSOUN 11" plus
numbers 6, 15, 1, 12 and 99. The "numbers don't read" finding came from crops
sampled at detection-box scale over the whole pitch; it is not true of every
frame, and near/side-on players are frequently readable. That matters beyond
labeling: it suggests biasing crop SELECTION toward legible moments is a live
lever, which is what the jersey-VLM work concluded independently.

Also note the kit is black-AND-GREEN (black shirt, green shorts and socks),
while the opponent here wears bright green shirts with black shorts. The
`home_color` hex `#0a0a0a` describes only part of our kit, so "black vs green"
understates how close these two teams look to a colour classifier.

Why pre-render rather than scrub live
-------------------------------------
A random seek into the 8K source costs ~2 s. Scrubbing interactively would mean
the coach waits on the disk all session. Rendering the checkpoints offline once
turns a slow interactive job into a fast one: the labeling pass then plays
local clips with no seeking at all.

What gets rendered
------------------
For each chosen stint, a clip at every checkpoint: `--clip-s` seconds of video
around the checkpoint, cropped to a window around the followed body, with the
follower's box drawn on it. The coach answers one question per clip — is the
boxed player the named child, yes or no — which is exactly the label needed to
measure whether a follow survives a full-half stint.

Never writes to Firestore. Reads the raw video and cached Stage-2 tracks.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.stint_label_render \\
        --game-id mrhvbvwi1gjpn --tag x_full --top-stints 4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import warnings
from pathlib import Path

import numpy as np

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
# A window around the player, in metres, converted to pixels per frame. Wide
# enough to show who they are running with (the context that makes a swap
# visible), tight enough that the player is not a speck.
CTX_M = 8.0
# How far inside the touchline a body must come, at least once, to count as
# someone who was actually ON the field. A player crosses well in; a coach
# hovering by the line never does.
EDGE_M = 1.5


def _draw(frame, box, colour=(0, 235, 255), width=6):
    import cv2
    x1, y1, x2, y2 = [int(v) for v in box]
    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, width)
    return frame


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tag", default="x_full",
                    help="cached Stage-2 checkpoint to follow on")
    ap.add_argument("--top-stints", type=int, default=4,
                    help="how many of the LONGEST stints to label")
    ap.add_argument("--checkpoint-s", type=float, default=120.0,
                    help="seconds between checkpoints within a stint")
    ap.add_argument("--clip-s", type=float, default=4.0,
                    help="clip length at each checkpoint")
    ap.add_argument("--out-fps", type=float, default=10.0)
    ap.add_argument("--max-clips", type=int, default=0, help="0 = no cap")
    args = ap.parse_args()

    import cv2
    from post_game import firestore_io
    from post_game.pipeline import _ensure_local_video
    from post_game.stint_follow import Seed, follow_stints
    from tracking.stint_follow_eval import build_stints
    from tracking.stint_follow_probe import build_frame_index, load_frames

    on, L, W = load_frames(args.game_id, args.tag)
    # Keep only bodies ON the pitch. `load_frames` allows a 1.5 m margin, and
    # the raw Stage-2 cache legitimately still holds touchline coaches,
    # spectators and players on the neighbouring field — 47% of detections and
    # 751 whole tracks on Game 1 sit outside the lines. The pipeline removes
    # them later at stage 3b2 (DROP_NEVER_ONFIELD); this tool has to do it
    # itself or the coach gets asked to identify a parent under a gazebo.
    before = len(on)
    on = on[(on.x_m >= 0) & (on.x_m <= L) & (on.y_m >= 0) & (on.y_m <= W)].copy()

    # A per-detection box test is NOT enough to exclude touchline figures. A
    # coach standing a foot off the line at halfway projects to y=30.8 on a
    # 31.1 m pitch — 31 cm inside — and is indistinguishable from a player
    # taking a throw-in. The first seed clip rendered was one of our own
    # coaches, and on a normal game our coaches wear BLACK, the same as the
    # team, so colour cannot separate them either.
    #
    # The distinguishing fact is only visible over a WHOLE track: a player
    # crosses into the field and comes back, a coach never does. This is the
    # same rule the pipeline applies at stage 3b2 (DROP_NEVER_ONFIELD,
    # post_game/test_never_onfield.py); apply it here rather than a box test.
    core = ((on.x_m > EDGE_M) & (on.x_m < L - EDGE_M)
            & (on.y_m > EDGE_M) & (on.y_m < W - EDGE_M))
    per = on.assign(_c=core).groupby("track_id")["_c"].agg(["mean", "size"])
    touchline = set(per.index[(per["mean"] <= 0.0) & (per["size"] >= 10)])
    on = on[~on.track_id.isin(touchline)].copy()
    print(f"on-pitch filter: {before} -> {len(on)} detections "
          f"({100*(1-len(on)/max(before,1)):.0f}% removed); "
          f"{len(touchline)} never-entered-the-field tracks dropped")
    times, byt = build_frame_index(on)
    frames_in = [(float(t), byt[t][:, :3]) for t in times]
    stints, game = build_stints(args.game_id)

    t_lo, t_hi = float(times.min()), float(times.max())
    usable = [(p, max(a, t_lo), min(b, t_hi)) for (p, a, b) in stints
              if b > t_lo and a < t_hi and min(b, t_hi) - max(a, t_lo) > 120]
    usable.sort(key=lambda r: -(r[2] - r[1]))
    chosen = usable[:args.top_stints]
    if not chosen:
        raise SystemExit("no stint long enough inside the cached window.")

    print(f"game {args.game_id} vs {game.opponent}   tag {args.tag}")
    for pid, a, b in chosen:
        print(f"  {pid:<14} {a:6.0f}-{b:6.0f}s  {(b-a)/60:5.1f} min")

    # Seed each chosen stint on the longest-lived body present at its start,
    # then follow. The BOX the coach sees is the follower's own belief, so a
    # "no" answer is exactly a swap observation.
    lifetimes = on.groupby("track_id").time_s.size()
    seeds, meta = [], {}
    for pid, a, b in chosen:
        i = int(np.searchsorted(times, a))
        fr = byt[times[i]]
        alive = [r for r in fr if int(r[3]) in lifetimes.index]
        if not alive:
            continue
        best = max(alive, key=lambda r: lifetimes.loc[int(r[3])])
        key = f"{pid}@{a:.0f}"
        seeds.append(Seed(player_id=key, t0=float(times[i]),
                          xy=(float(best[0]), float(best[1])), t_end=b))
        meta[key] = {"player_id": pid, "t0": float(a), "t1": float(b)}
    out = follow_stints(frames_in, seeds)

    # Map follower samples back to pixels for drawing. The parquet holds the
    # equirect box per (time, foot position), so match on the foot point the
    # follower attached to.
    px = {}
    for t, g in on.groupby("time_s"):
        px[float(t)] = g[["x1_eq", "y1_eq", "x2_eq", "y2_eq",
                          "foot_x_eq", "foot_y_eq"]].to_numpy()
    proj = None
    from post_game.calibration import FieldProjector
    proj = FieldProjector(firestore_io.get_game_calibration(args.game_id))

    video = _ensure_local_video(game.video_url, args.game_id)
    cap = cv2.VideoCapture(str(video))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    outdir = LABELS_ROOT / f"{args.game_id}_stint_labels"
    outdir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for tg in out:
        if not tg.samples:
            continue
        m = meta[tg.player_id]
        t0 = tg.samples[0][0]
        cps = np.arange(t0, tg.samples[-1][0], args.checkpoint_s)
        seen_t: set[float] = set()
        for cp in cps:
            # nearest sample at/after the checkpoint
            cand = [s for s in tg.samples if s[0] >= cp]
            if not cand:
                continue
            # Several checkpoints can resolve to the SAME sample when the
            # follower has a long gap — the next available attach is shared.
            # Clips are named by rounded second, so those collided and
            # overwrote each other: 60 manifest rows became 40 files, one name
            # written five times, and the manifest then described clips that
            # did not exist. Keep the first checkpoint to reach a sample.
            if round(cand[0][0], 1) in seen_t:
                continue
            seen_t.add(round(cand[0][0], 1))
            # Carry the follower's whole path over the clip window so the box
            # can track the player frame by frame instead of being pinned to
            # the checkpoint instant — a player crossing the window would
            # otherwise run out from under a static box.
            lo, hi = cand[0][0] - args.clip_s, cand[0][0] + args.clip_s
            fpath = {s[0]: (s[1], s[2]) for s in tg.samples if lo <= s[0] <= hi}
            tasks.append((tg.player_id, m["player_id"], float(cand[0][0]),
                          float(cand[0][1]), float(cand[0][2]), float(t0), fpath))
    if args.max_clips:
        tasks = tasks[:args.max_clips]
    print(f"\n{len(tasks)} checkpoints to render "
          f"({args.clip_s:.0f}s each, every {args.checkpoint_s:.0f}s)")

    manifest = []
    for n, (key, pid, t_cp, x_m, y_m, t0, fpath) in enumerate(tasks):
        cx, cy = proj.field_to_pixel(x_m, y_m)
        # Context window in pixels, sized from the metre window at this point.
        # Capped: perspective makes a near player's window enormous (an early
        # render came out 4322 px wide, mostly grass, spectators and a gazebo),
        # which buries the 77-pixel child the question is actually about.
        x2p, _ = proj.field_to_pixel(x_m + CTX_M, y_m)
        half = float(np.clip(abs(x2p - cx), 200.0, 700.0))
        x0, y0 = int(cx - half), int(cy - half * 0.62)
        w = h = int(half * 2)
        name = f"{key.replace('@','_')}_{t_cp:.0f}.mp4"
        path = outdir / name
        # OpenCV's mp4v writes an MPEG-4 Part 2 stream, which no browser will
        # play — the labeling app showed a black player with working controls.
        # Write to a temp file then transcode to H.264 + yuv420p with
        # faststart, which is what <video> actually needs.
        tmp = outdir / f".raw_{name}"
        vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"),
                             args.out_fps, (w, int(w * 0.62)))
        step = max(1, int(round(src_fps / args.out_fps)))
        cap.set(cv2.CAP_PROP_POS_MSEC, (t_cp - args.clip_s / 2) * 1000)
        wrote = 0
        want = int(args.clip_s * args.out_fps)
        fi = 0
        while wrote < want:
            ok, fr = cap.read()
            if not ok:
                break
            if fi % step == 0:
                t_now = (t_cp - args.clip_s / 2) + fi / src_fps
                # Where the follower believes the player is AT THIS INSTANT.
                if fpath:
                    kk = min(fpath, key=lambda z: abs(z - t_now))
                    cx_now, cy_now = proj.field_to_pixel(*fpath[kk])
                else:
                    cx_now, cy_now = cx, cy
                sub = fr[max(0, y0):y0 + int(w * 0.62), max(0, x0):x0 + w]
                if sub.size:
                    # Draw ONLY the followed player, in yellow. The first
                    # version drew every raw detection in the same grey, which
                    # made the question unanswerable — the coach could not tell
                    # which box was the one being asked about — and included
                    # off-pitch bodies (touchline coaches, opponents warming up
                    # on the next field). Those are legitimately present in the
                    # raw Stage-2 cache because DROP_NEVER_ONFIELD runs later,
                    # at stage 3b2; they are not a tracking failure, but they
                    # have no business in a labeling clip.
                    kt = min(px, key=lambda z: abs(z - t_now)) if px else None
                    if kt is not None and abs(kt - t_now) < 0.2:
                        rows = px[kt]
                        d = np.hypot(rows[:, 4] - cx_now, rows[:, 5] - cy_now)
                        k = int(np.argmin(d))
                        if d[k] < half * 0.5:
                            _draw(sub, (rows[k, 0] - x0, rows[k, 1] - y0,
                                        rows[k, 2] - x0, rows[k, 3] - y0))
                    sub = cv2.resize(sub, (w, int(w * 0.62)))
                    vw.write(sub)
                    wrote += 1
            fi += 1
        vw.release()
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(tmp),
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(path)],
            check=True)
        tmp.unlink(missing_ok=True)
        manifest.append({
            "clip": name, "stint_key": key, "player_id": pid,
            "t_checkpoint_s": t_cp, "t_stint_start_s": t0,
            "elapsed_in_follow_s": t_cp - t0,
            "follower_xy_m": [x_m, y_m],
        })
        if (n + 1) % 5 == 0 or n + 1 == len(tasks):
            print(f"  rendered {n+1}/{len(tasks)}", flush=True)
    cap.release()

    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\nwrote {len(manifest)} clips + manifest.json to {outdir}")
    print("Next: label them with tracking/stint_label_app.py")


if __name__ == "__main__":
    main()

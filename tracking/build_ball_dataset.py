"""Step 1 of ball fine-tuning: build an auto-labeled bootstrap dataset.

Samples in-play frames across a game, renders the production TV crop at the
player-centroid aim (exactly what inference will see), runs every available ball
detector, and writes a proposed YOLO box per crop. A human then verifies/corrects
in `ball_labeler.html` — most frames are one keystroke.

Output (under outputs/ball_dataset/<game_id>/):
  images/<frame>.jpg        the rendered crops
  manifest.json             per-image proposals (pixel xyxy + source + conf)

Usage:
  python -m tracking.build_ball_dataset --game-id mpyo67cl4uflh --every-s 2 --max 600
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import typer

from post_game import config, firestore_io
from post_game.ball import _player_centroid_lonlat
from post_game.detection import Detector
from post_game.video import render_perspective

app = typer.Typer(add_completion=False)


def _in_play_windows(game_id: str):
    """Return (video_path, [(start_s,end_s), ...]) using the game's kickoff offsets."""
    snap = firestore_io._team_doc().collection("games").document(game_id).get()
    d = snap.to_dict()
    if not d:
        raise typer.BadParameter(f"game {game_id} not found")
    video = (d.get("videoUrl") or "").replace("file://", "")
    half = float(d.get("halfLengthMin", 30)) * 60.0
    h1 = d.get("videoOffsetH1KickoffS")
    h2 = d.get("videoOffsetH2KickoffS")
    wins = []
    if h1 is not None:
        wins.append((float(h1), float(h1) + half))
    if h2 is not None:
        wins.append((float(h2), float(h2) + half))
    if not wins:
        raise typer.BadParameter("game has no kickoff offsets; set them first")
    return video, wins


def _ball_models():
    """All available ball detectors as (name, YOLO, ball_class_ids|None)."""
    from ultralytics import YOLO
    mdir = config.MODELS_DIR
    out = [("coco", YOLO(config.YOLO_MODEL), [config.BALL_CLASS_ID])]
    for fn, name in [("soccer_ball_yolo11n.pt", "soccer"),
                     ("football_players_yolov8.pt", "uisikdag")]:
        p = mdir / fn
        if p.exists():
            m = YOLO(str(p))
            names = m.names
            cls = None if len(names) == 1 else [i for i, n in names.items() if "ball" in str(n).lower()]
            out.append((name, m, cls))
    return out


@app.command()
def build(
    game_id: str = typer.Option(..., "--game-id"),
    every_s: float = typer.Option(2.0, "--every-s", help="Sample one frame every N in-play seconds."),
    max_frames: int = typer.Option(600, "--max", help="Cap total crops."),
):
    video, wins = _in_play_windows(game_id)
    out_dir = config.OUTPUTS_DIR / "ball_dataset" / game_id
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise typer.BadParameter(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    aim_det = Detector()
    models = _ball_models()
    print(f"Ball detectors: {[n for n, _, _ in models]}")

    # Build the list of sample times across in-play windows.
    times = []
    for a, b in wins:
        t = a
        while t < b:
            times.append(t)
            t += every_s
    # Even subsample to the cap.
    if len(times) > max_frames:
        step = len(times) / max_frames
        times = [times[int(i * step)] for i in range(max_frames)]

    manifest = []
    for k, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        aim = _player_centroid_lonlat(frame, aim_det)
        if aim is None:
            continue
        lon, lat = aim
        crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)

        proposals = []
        for name, model, cls in models:
            res = model.predict(crop, classes=cls, conf=0.12, device=config.DEVICE, verbose=False)
            if res and res[0].boxes is not None:
                for box in res[0].boxes:
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    proposals.append({"xyxy": [x1, y1, x2, y2],
                                      "conf": float(box.conf[0].item()), "src": name})

        # Consensus: a proposal corroborated by >=2 sources (centers within 50px).
        def center(p):
            return ((p["xyxy"][0] + p["xyxy"][2]) / 2, (p["xyxy"][1] + p["xyxy"][3]) / 2)
        best = None
        if proposals:
            for p in proposals:
                cx, cy = center(p)
                agree = sum(1 for q in proposals
                            if (center(q)[0] - cx) ** 2 + (center(q)[1] - cy) ** 2 <= 50 ** 2)
                p["_agree"] = agree
            # prefer most-corroborated, then highest conf
            best = max(proposals, key=lambda p: (p["_agree"], p["conf"]))

        fname = f"{int(t*fps):07d}.jpg"
        cv2.imwrite(str(img_dir / fname), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        manifest.append({
            "file": fname, "time_s": round(t, 2),
            "w": config.CROP_W, "h": config.CROP_H,
            "proposals": proposals,
            "proposed": best["xyxy"] if best else None,
            "proposed_src": best["src"] if best else None,
            "proposed_agree": best.get("_agree", 0) if best else 0,
        })
        if (k + 1) % 25 == 0:
            print(f"  {k+1}/{len(times)} crops...")

    cap.release()
    (out_dir / "manifest.json").write_text(json.dumps(manifest))
    # Copy the labeler in so http.server serves it same-origin as manifest/images.
    labeler_src = Path(__file__).parent / "ball_labeler.html"
    if labeler_src.exists():
        (out_dir / "index.html").write_text(labeler_src.read_text())
    n_prop = sum(1 for m in manifest if m["proposed"])
    n_cons = sum(1 for m in manifest if m["proposed_agree"] >= 2)
    print(f"\nWrote {len(manifest)} crops to {img_dir}")
    print(f"  with a proposed box: {n_prop}  (consensus >=2 sources: {n_cons})")
    print(f"  manifest: {out_dir/'manifest.json'}")
    print(f"\nLabel them:")
    print(f"  cd {out_dir} && python3 -m http.server 8009")
    print(f"  open http://localhost:8009/   (Export labels.json when done)")
    print(f"Then train:")
    print(f"  python -m tracking.train_ball --game-id {game_id} --labels <path/to/labels.json>")


if __name__ == "__main__":
    app()

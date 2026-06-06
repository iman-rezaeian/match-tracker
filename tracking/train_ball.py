"""Step 3 of ball fine-tuning: train yolo11 on the corrected crops.

Reads the dataset built by `build_ball_dataset.py` plus the `labels.json`
exported from `ball_labeler.html`, materializes a YOLO-format split
(train/val), writes data.yaml, and fine-tunes a single-class 'ball' detector.

labels.json: { "<file>.jpg": [cx,cy,w,h] (normalized) | null, ... }
  null = explicit no-ball frame (kept as a negative — image with empty label).

Usage:
  python -m tracking.train_ball --game-id mpyo67cl4uflh \
      --labels ~/Downloads/labels.json --base yolo11s.pt --epochs 80
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import typer

from post_game import config

app = typer.Typer(add_completion=False)


@app.command()
def train(
    game_id: str = typer.Option(..., "--game-id"),
    labels: Path = typer.Option(..., "--labels", help="labels.json exported from the labeler."),
    base: str = typer.Option("yolo11s.pt", "--base", help="Base weights to fine-tune."),
    epochs: int = typer.Option(80, "--epochs"),
    imgsz: int = typer.Option(960, "--imgsz"),
    val_frac: float = typer.Option(0.2, "--val-frac"),
    seed: int = typer.Option(0, "--seed"),
):
    ds = config.OUTPUTS_DIR / "ball_dataset" / game_id
    img_src = ds / "images"
    if not img_src.exists():
        raise typer.BadParameter(f"no images at {img_src}; run build_ball_dataset first")
    lab = json.loads(Path(labels).expanduser().read_text())
    decided = {f: v for f, v in lab.items() if (img_src / f).exists()}
    pos = {f: v for f, v in decided.items() if v}
    neg = {f: v for f, v in decided.items() if not v}
    if len(pos) < 20:
        raise typer.BadParameter(f"only {len(pos)} ball frames labeled — label more before training")
    print(f"Labeled: {len(pos)} ball, {len(neg)} no-ball ({len(decided)} total)")

    root = ds / "yolo"
    if root.exists():
        shutil.rmtree(root)
    files = list(decided.items())
    random.Random(seed).shuffle(files)
    n_val = max(1, int(len(files) * val_frac))
    splits = {"val": files[:n_val], "train": files[n_val:]}

    for split, items in splits.items():
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        for fname, box in items:
            shutil.copy(img_src / fname, root / "images" / split / fname)
            txt = root / "labels" / split / (Path(fname).stem + ".txt")
            if box:
                cx, cy, w, h = box
                txt.write_text(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")
            else:
                txt.write_text("")  # negative sample

    data_yaml = root / "data.yaml"
    data_yaml.write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\nnames:\n  0: ball\n"
    )
    print(f"Dataset: {root}  (train={len(splits['train'])}, val={len(splits['val'])})")

    from ultralytics import YOLO
    model = YOLO(base)
    model.train(
        data=str(data_yaml), epochs=epochs, imgsz=imgsz, device=config.DEVICE,
        project=str(ds / "runs"), name="ball_finetune", exist_ok=True,
        patience=20, batch=8, plots=False,
    )
    best = ds / "runs" / "ball_finetune" / "weights" / "best.pt"
    dest = config.MODELS_DIR / "ball_finetuned.pt"
    if best.exists():
        shutil.copy(best, dest)
        print(f"\n✓ Best weights -> {dest}")
        print(f"  Re-run the gate to measure lift: edit tracking/ball_gate_models.py to add this model,")
        print(f"  or point detect_ball at it.")


if __name__ == "__main__":
    app()

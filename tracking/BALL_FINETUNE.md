# Ball fine-tuning workflow

Off-the-shelf detectors fail on our 360°-derived crops (5–19% hit rate) purely
from **domain gap** — they were trained on TV broadcast, not our rig. But the
ball is clearly visible (~25px) in the rendered crop, so a detector fine-tuned
on *our own* crops is the path to accurate ball tracking. See the memory note
`ball-detection-gate.md` for the full evidence trail.

Three steps:

## 1. Build the bootstrap dataset

Renders in-play TV crops across the game and runs every available ball detector
to pre-propose a box (green dashed in the labeler — most frames are one keystroke).

```bash
python -m tracking.build_ball_dataset --game-id mpyo67cl4uflh --every-s 2 --max 600
```

Writes `post_game/outputs/ball_dataset/<game_id>/{images/, manifest.json, index.html}`.

## 2. Label / correct in the browser

```bash
cd post_game/outputs/ball_dataset/<game_id>
python3 -m http.server 8011
# open http://localhost:8011/
```

Controls:
- **click** the ball → places a box at the cursor (magnifier loupe helps on the ~25px ball)
- **drag** → draw an exact box; **wheel** → resize
- **A** / **Enter** → confirm ball, next   ·   **N** → no ball here, next
- **R** → reset to the AI proposal   ·   **←/→** → prev/next
- Work autosaves to the browser. Click **Export labels.json** when done.

Aim for ~300+ ball frames. Keep the no-ball frames too — they train the model
not to fire on grass/lines/players.

## 3. Fine-tune

```bash
python -m tracking.train_ball --game-id mpyo67cl4uflh \
    --labels ~/Downloads/labels.json --base yolo11s.pt --epochs 80
```

Best weights are copied to `post_game/models/ball_finetuned.pt`. Measure the
lift by adding that model to `tracking/ball_gate_models.py` and re-running the
gate. If it clears the ≥40% bar, wire it into `post_game/ball.py` (pass-2) and
unshelve step 7 (possession + passes). Layer the motion + Kalman work from
`tracking/ball_motion_track2.py` on top as a gap-filler / false-positive gate.
```

# Jersey-number OCR — feasibility findings

Question: is a jersey-number OCR identity pass worth building? OCR is the one
signal that could resolve the residual **outfield identity swaps** (a player's
number is unique, where the coach-log positional prior can't separate roaming
players who share zones). Value hinges on whether numbers are actually legible
in the footage.

## Verdict (2026-06, 5.7K footage): NOT worth it yet — revisit at 8K

Probed on `mpyo67cl4uflh` (Windsor Fury, 5.7K / 5760×2880 equirect, sideline 360 cam):

- **Pixel sizes** — field players are mostly **42–120 px tall** (median 72,
  p90 162), so estimated jersey **digits ≈ 9–26 px**. ~49% of detections clear
  ~16 px, ~36% clear ~20 px — but that's an upper bound (assumes the number
  faces the camera and isn't blurred).
- **The biggest, sharpest crops are NOT players** — the largest near-camera
  detections were the **sideline coach + a spectator** (the 360 cam is at the
  touchline, so the closest/biggest subjects are bystanders).
- **Real field players (96–111 px) were side-on and too soft** to read a number
  in every sampled crop; the median player (72 px) is smaller still.

Three compounding limits at 5.7K: **digit size** (mostly < the ~20–30 px OCR
wants), **orientation** (number only readable when facing toward/away from cam —
most frames are side-on), and **motion blur** + small youth-kit numbers. Net:
few readable-and-facing instances for actual players → low, unreliable yield for
a high build cost (OCR model + per-detection crop/infer over ~460k boxes +
integration). It would not dependably fix the outfield swaps.

## Update 2026-06 — per-PLAYER best-frame probe (`tracking/jersey_ocr_probe.py`)
Re-tested the coach's "but the numbers ARE readable" point properly: for each
assigned player in Windsor, took their largest/sharpest crops and looked.
- **Digit SIZE is fine** on best frames: ~26–40 px for 11/12 players (only the GK,
  small/far, fell short). Size is no longer the blocker on the best frames.
- **But readability is killed by ORIENTATION.** Of 8 players' best crops, **only 1
  (#19) was readable** — the rest front/side-facing, showing the chest **crest, not
  the back number.** Geometry: number is on the BACK; the largest/closest frames to
  a touchline cam are players facing the field (front). Back-facing frames happen
  when running toward the FAR goal → farther → smaller digit. Inherent
  **orientation × distance tension** a single touchline cam can't escape.
- **Net yield ceiling ≈ low** (~1 in 8 best frames). OCR would tag a handful of
  lucky close-AND-back tracklets per game (like #19), not reliably ID the team.

**Cheapest real fix = FRONT numbers** (jersey front or shorts). The frames we
already get in abundance — close, front-facing — become readable, sidestepping
orientation entirely. No hardware, one kit decision. Recommend for next season's
kit before spending on 8K/OCR.

## Why 8K likely flips this
Resolution helps but does NOT remove the orientation tension above. At 8K (7680×3840) players/digits scale ~1.3–1.8×,
putting near-side players' digits in the **~15–45 px** range where OCR starts
working. Plan OCR as a **strong tiebreaker layered on the coach-log prior**
(resolving near-side / camera-facing tracklets), not a standalone fix.

## How to re-test (run on the first 8K game)
```bash
python tracking/ocr_legibility_test.py \
  --tracks post_game/outputs/<game_id>/tracks_raw.parquet \
  --video  /Users/irezaeian/Movies/stompers/<game>.mp4 \
  --out    /tmp/ocr_test --n 12
```
Reports the digit-height distribution and dumps upscaled crops to eyeball. If a
decent fraction of **field** players (ignore sideline adults) show a readable
number → build OCR. Compare the `≥20px` / `≥25px` percentages against this 5.7K
baseline (≈36% / ≈27%, but unreadable in practice).

## If/when we build it (sketch)
- Per tracklet, run a digit/number detector+OCR on the sharpest, most
  frontal/back-facing crops (gate by bbox height + a simple orientation/sharpness
  score); aggregate a **number vote** per tracklet.
- Feed the number as a high-weight signal into `identity_assign.py` (each roster
  number is unique → near-certain match), on top of the existing coach-log
  Hungarian + minute-budget. Keep it a tiebreaker so a wrong OCR can't override
  strong positional+budget evidence.

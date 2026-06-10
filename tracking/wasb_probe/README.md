# WASB ball-in-crop probe

Tests whether the ball is detectable inside the virtual broadcast crop (where it
is larger) instead of on the full equirect sphere (where it is ~3–6 px and
generic YOLO managed only 18.7%, shelved).

**Result at 5.7K (game `mpyo67cl4uflh`, match-distance play): 0.4% — FAIL.**
Resolution-gated, not method-gated. Re-test on the first 8K game. See
`/memories/repo/phase0-ball-tracking.md` for the full writeup.

## Why it fails at 5.7K
WASB resizes every input to **512×288**, so the crop FOV is the only lever:
- `--fov 70` (broadcast crop): ~7 px/deg — *worse* than the 16 px/deg sphere.
  WASB fires on white background blobs (tree line / other pitches) → fake "hits".
- `--fov 18` (tight ball-cam): ~28 px/deg, the regime WASB was trained on. False
  positives vanish — but so do the real hits (~0%), because a 6 px ball upscaled
  adds no texture for WASB to lock onto.

## Reproduce (one-time setup)
The upstream repo + weights are gitignored. Recreate them:

```bash
cd tracking/wasb_probe
git clone --depth 1 https://github.com/nttcom/WASB-SBDT.git
mkdir -p weights
python -m gdown "1pg0MpMtKZ6ziYEr4oyfKYPOO3hjLw94l" -O weights/wasb_soccer_best.pth.tar
```

Notes:
- Needs `torch` (MPS works) + `gdown`. `omegaconf` is intentionally NOT required
  (its wheel host is blocked on the corp VPN) — `probe.py` uses a tiny DotDict
  wrapper for WASB's dot-access config instead.
- WASB soccer weights live on Google Drive (id `1pg0MpMtKZ6ziYEr4oyfKYPOO3hjLw94l`,
  from the repo's `MODEL_ZOO.md`).

## Run
```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/stompers/firebase-adminsdk.json
export FIRESTORE_PROJECT_ID=lasalle-stompers
python -m tracking.wasb_probe.probe --game-id <game-id> \
    --start 1046 --end 1072 --fov 18 --save-crops /tmp/wasb_hits
```

Prints detection rate + ball pixel size and saves annotated hit crops. The
`>=40%` gate decides whether to build the two-crop ball-tracking design (tight
ball-cam for tracking + wide view for broadcast).

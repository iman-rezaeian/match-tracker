# 8K re-test checklist

Everything we deferred to "re-test at 8K." The first game shot in **8K**
(7680×3840 equirect, ~1.3–1.8× more pixels on each player than 5.7K) is the trigger
to work through this. Context for each item is in the linked files / memory.

## 0. Shooting + export (do this when recording/exporting the 8K .insv)
- **Record:** 8K. Use **PureVideo** (AI denoise) for evening/overcast/low-light games
  → cleaner frames = the detector finds more small players = better coverage.
- **Export from Insta360 Studio for analytics:** full **equirectangular (360)**, **max
  bitrate**, H.265, **360° Horizon-Lock ON** (keeps the projection consistent
  frame-to-frame = stable calibration on a fixed pole).
- **NEVER** use **Deep Track / AI reframe / auto-follow** for the analytics file — it
  produces a moving cropped view that breaks the fixed-camera calibration and drops
  off-ball players. (Fine only for a separate highlight edit.)
- Re-calibrate the 8K game (projection differs from 5.7K). Expect **longer runs**
  (more pixels). Launch via `./run_game_detached.sh <game-id> --tv-view` or Terminal
  `caffeinate …` — NOT the assistant's background tasks (reaped on session roll).

## 1. Ball detection → unlock ball-following reel camera
- **Why:** reel/highlights aim at the *player centroid* today (no ball). It lags on
  long balls and under-frames corners/sidelines. Generic COCO ball detection was
  18.7% at 5.7K (shelved). The reel code is ALREADY wired to swap **ball position**
  in as the primary aim once detection clears the 40% gate (`post_game/tv_view.py`).
- **Re-test with a purpose-built model, NOT generic YOLO:**
  - **TrackNet v2/v3** (heatmap + 3-frame temporal — built for tiny/fast/blurred balls)
  - **WASB** ("Widely Applicable Strong Baseline", beats TrackNet across sports)
  - **SoccerNet** ball-tracking / ball-action benchmarks (`sn-tracking`, `sn-gamestate`)
  - Add **trajectory/Kalman smoothing** (ballistic prior) to interpolate through
    missed frames — you only need a smooth path, not every frame.
- Run the existing gate: `python -m post_game.cli ball-gate --game-id <8k-game>` (but
  swap in a TrackNet/WASB detector). 8K ball is ~1.3–1.8× bigger → likely clears 40%.
- See `[[ball-detection-gate]]` memory + `JERSEY_OCR_FEASIBILITY.md` style writeup.

## 2. Reel camera aiming (software-only, can do even without ball)
- **Predictive lead:** Kalman/velocity-extrapolate the aim so it ANTICIPATES the play
  instead of trailing (fixes "left behind"). Shorten `TV_SMOOTH_WINDOW` (currently 15
  = 3 s) to cut lag.
- **Event-aware framing from the COACH LOG (free edge):** we already log corners /
  throw-ins / goal-kicks → zoom-out + tilt-to-corner on those events.
- Caveat: aim math is cheap but the reel RENDER is multi-hour → bake changes into a
  game's run, don't iterate by re-rendering.

## 3. Jersey-number OCR → identity tiebreaker
- Re-run `python tracking/jersey_ocr_probe.py --game <8k-game> --video <mp4> --out /tmp/jersey_probe`.
- 5.7K finding: digit SIZE was fine (~26–40 px) but **orientation** killed it — back
  numbers, but the largest/closest frames are front-facing (chest crest). Only ~1/8
  best crops readable. 8K makes back-facing-but-far digits readable → yield should rise.
- **No kit change planned**, so the orientation tension remains — **FRONT numbers on
  the kit are still the cheapest real fix** (makes the abundant front-facing frames
  readable). Recommend lobbying for it. Full writeup: `JERSEY_OCR_FEASIBILITY.md`.

## 4. Player appearance profile / cross-game gallery
- Re-run `python tracking/player_profile_probe.py --games <8k-gameA> <8k-gameB> …`
  (include some games with the SAME kit — team runs TWO kits).
- 5.7K baseline: within-game 25–32% (3–4× chance, tiny ~0.02–0.03 cosine margin —
  kit dominates), cross-KIT ~chance (10%). Raw OSNet is kit-dominated → not viable.
- **Gate at 8K:** within-game >~60% + strong SAME-KIT cross-game → build **per-kit
  galleries** (2/player, routed by the coach's home/away color). Strong **CROSS-KIT**
  → a single **kit-invariant profile** is achievable (needs a part-based/attribute
  model on head/legs/build, not raw OSNet). Seed galleries from FIX-IDS labels,
  tagged by kit. Body-height cue needs field-distance normalization to mean stature.

## 5. Coverage / distance accuracy + team classification
- Re-measure **per-player tracked coverage** (was only ~14% of on-field minutes at
  5.7K → distances 3–4× low, "directional not absolute"). Expect higher at 8K.
- **Green-on-grass** stays the hardest team-classification case (no kit change). The
  jersey-sampler fix (`sample_jersey_hsv`, 2026-06) only applies on a FRESH re-track,
  so it kicks in automatically on 8K games — re-check the opponent↔us cross-
  contamination rate (was ≥17% each way on Leamington). 8K + the fix should reduce it.
- Under-tracked players show "⚠ LOW TRACKING"; swap-polluted show "⚠ INFLATED" — see
  if both shrink at 8K.

## Tools recap
- `post_game/cli ball-gate` — ball-detection hit-rate gate (swap in TrackNet/WASB)
- `tracking/jersey_ocr_probe.py` — per-player best-frame jersey legibility/yield
- `tracking/player_profile_probe.py` — appearance separability gate (kit-aware)
- `run_game_detached.sh` — session-roll- and sleep-proof long runs

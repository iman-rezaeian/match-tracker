# Accuracy Audit — why per-player/team metrics aren't close (2026-07-31)

Full-stack audit (13-agent fan-out: 6 specialist lenses → adversarial verification → synthesis,
all grounded in the real code + re-measured parquets). Companion to `METRIC_ACCURACY_ROADMAP.md`
and `METRICS_RELEVANCE_PLAN.md`. **This audit overturns the roadmap's own diagnosis.**

## Verdict: the "identity assignment is the bottleneck" diagnosis is a SYMPTOM, not the cause

The real accuracy ceiling is **upstream** of identity, stacked in three layers, none of which the
identity/VLM work can touch. Even with perfect identity, per-player distance/speed would still be
wrong — they inherit corrupted positions from stage 1. Five of six specialists converged on this.

1. **Geometry/calibration** — pixel→field-meters with no camera tilt, no lens distortion, and a
   self-referential in-sample RMS. Biases every position → every distance/speed/formation metric.
2. **Detection + tracking** — YOLO at half-res on distorted equirect; tracker+Re-ID fed equirect
   pixels (not the rectified tiles detection ran on) → 1,778–2,887 tracklet fragments/game.
3. **Metric math** — distance integrated as a raw, unsmoothed sum of noisy per-frame steps.

Identity's ~0.03 auto-recall is the arithmetic consequence of that starved, fragmented, weakly-
positioned input — not an independent failure. **Stop treating jersey-VLM/identity as the frontier;
it is third in line.**

## Ranked defects (verified; most impactful first)

### Genuine bugs
- **B1 — Camera pitch/roll never solved → always 0.** `calibrate_flat.py` fits only a 4-DOF 2D
  similarity (`solveSimilarity2D`); no `pitch`/`roll` in the payload, so `calibration.py:180-181`
  defaults tilt to 0.0. The runtime `FieldProjector` supports tilt (`calibration.py:185-189`) but
  never receives it. On a low grazing camera this puts systematic, range-growing error into every
  position. **Highest leverage.** Fix: solve pitch/roll/height via `least_squares` reprojection
  over the up-to-13 reference points already collected (`calibration.py:140-153`). MEDIUM.
- **B2 — Tracker + Re-ID run on the equirect frame, not rectified tiles.** `pipeline.py:264`
  (`bbox_crop = bbox_eq`), `:273` (`tracker.update(sample.eq_frame, …)`). Detection is on
  undistorted tiles (`:248`) but association happens in stretched equirect space → fragmentation.
  The `tracking.py:1-6` docstring claims the opposite (stale). Fix: associate in field-meters
  (ground positions already computed at `pipeline.py:368` — reorder stage 2→3) + crop Re-ID from
  rectified tiles. MEDIUM–HIGH.
- **B3 — YOLO at imgsz=640 on 1280-px tiles → far players downsampled 2×.** No `imgsz` was passed
  (`detection.py:38`). **FIXED 2026-07-31** via `config.DETECT_IMGSZ=1280` — staged, not yet
  measured (needs a re-track; all GT raws were deleted, so measure on the next new game).
- **B4 — Distance = raw sum of unsmoothed steps → biased high.** `stats.py:132,193`. The 5-tap
  boxcar is applied to speed only, never to position before integration. (The audit walked back an
  overstated "21 km" severity — the teleport gate caps it — but the bias is real at low σ.) Fix:
  smooth/Kalman position in field-space before integrating. MEDIUM.
- **B5 — dt-clip manufactures motion across gaps.** `stats.py:114-115` clamps dt ≤2s, turning a
  6–8s occlusion into fake running. Fix: drop gap steps from the distance integral. QUICK WIN.
- **B6 — `rms_weighted_m` read by cli/ui but never written** → dead display. TRIVIAL.

### Sub-optimal-but-working
- **S1** no lens/distortion model anywhere (structural, pairs with B1).
- **S2** per-window 1:1 Hungarian assigner (σ=18m gate, board = zone not individual) — real but
  DOWNSTREAM of B1–B3; the ~0.03 recall is precision-throttled on a starved input.
- **S3** Re-ID: single terminal EMA embedding/track (no gallery), greedy stitch, greedy budget —
  correct diagnoses, impact unquantified (OSNet already kit-dead).
- **S4** TRACK_BUFFER_S=20s over-coasting + four disabled band-aid flags.
- **S5** team width/depth use non-robust max−min extrema (`formation.py:271-272`); field_tilt
  correctly uses the robust centroid. Team-0 contamination (ref 44.5min, opp 9min) evidenced.
- **S6** 10 Hz sampling caps sprint/speed; the 5-sample smooth == 5-sample min sprint length.

## Honest accuracy ceiling on this single-camera 8K-360 rig
- **Can be accurate (software-fixable):** event metrics (coach taps); **team-level** metrics
  (centroid, field_tilt, compactness) once B1 is fixed; **near-half** player position/distance.
- **Physically capped (no software fix):** **far-half per-player distance & top speed** (depth
  error grows ~range²; 1px far-side flicker ≈ 0.4–0.5m at 50m), absolute per-player speed (10Hz +
  far-field), fine individuation in the U10 swarm, ball metrics (already shelved).
- **The blunt truth:** the goal "accurate player AND team metrics" is only half-achievable per-
  player on this rig. Far-half per-player accuracy needs a higher/more-central mount or a 2nd
  camera — optics, not code.

## Recommended path (re-ordered from the roadmap)
1. **QUICK: `imgsz=1280`** (done, measure next game) + **exclude occlusion-gap steps** (B5).
2. **STRUCTURAL, highest leverage: solve camera pitch/roll/height** (B1) + add a held-out
   reprojection error map so far-field error is finally visible.
3. **Move tracking + Re-ID into field-meters / rectified crops** (B2).
4. **Only after 1–3: re-measure identity recall**, then decide on the assigner rewrite (global
   min-cost flow) and the jersey-VLM Opus ceiling. Identity work before the upstream fixes is
   measuring noise.
5. **Hardware conversation** if far-half per-player accuracy is non-negotiable.

## Uncertainty flags (verification caught these)
- Specific pixel/resolution figures ("5760/7680 wide", "25–40px players") trace to a stale debug
  comment (`pipeline.py:292`) — treat as unverified; the qualitative distortion argument stands.
- B4's σ≥0.30 severity table is numerically wrong (ignores the teleport gate).
- The team-classifier "no reject class" claim is overstated — a `team_id=-1` path exists
  (`team_classifier.py:155-164`); contamination is still evidenced in the eval JSONs.
- S2/S3 severities are correct as code descriptions but impact-unquantified.

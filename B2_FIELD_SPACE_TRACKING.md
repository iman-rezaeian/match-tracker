# B2 — Field-space tracking + rectified-crop Re-ID (design, NOT yet shipped)

**Status: DESIGN + prototype plan. Do NOT merge until a raw game exists to re-track and validate
against GT** (all 3 tracked games' raws were deleted; B2 is the audit's highest-risk change and is
unmeasurable today). Companion to `ACCURACY_AUDIT.md` (defect B2).

## The defect (verified in code)

`pipeline.py:264` sets `d.bbox_crop = d.bbox_eq` and `:273` calls `tracker.update(sample.eq_frame, …)`,
so the BoT-SORT tracker associates on the **distorted equirectangular frame**. Detection ran on
undistorted perspective tiles (`:248`), but tracking + Re-ID then operate in latitude-stretched
equirect space. `tracking.py:1-6` docstring claims the opposite ("runs in crop pixel space") — it's
stale and wrong. Ground positions (`x_m,y_m`) are computed at `pipeline.py:368`, but only *after*
tracking, so they inform nothing.

**Why it corrupts accuracy:** BoT-SORT association has two terms and equirect corrupts BOTH:
1. **Motion** — Kalman + IoU on equirect boxes. A player moving at constant field speed changes
   apparent box size/velocity nonlinearly with latitude → the Kalman motion model mispredicts →
   IoU gate misses → track breaks. This is a prime driver of the 1,778–2,887 fragments/game.
2. **Appearance** — OSNet crops pulled from the distorted equirect frame → warped, off-aspect
   player crops → weaker Re-ID embeddings (compounding the known "kit-dead" problem).

**Best-in-class confirmation:** the SoccerNet-GSR 2024 winner ("From Broadcast to Minimap",
arXiv 2504.06357, GS-HOTA 63.81) tracks players in **real-world pitch coordinates** (DeepSORT on
the field plane), not image coordinates, and credits its fragmentation-reducing post-processing as
the biggest association-accuracy gain. B2's direction is state-of-the-art, not novel.

## Design — two independent fixes, smallest-blast-radius first

boxmot's `BotSort` is a black box: it does Kalman+IoU on the bboxes you pass and OSNet on crops from
the frame you pass. We do NOT fork its internals. Two clean levers:

### Fix 1 (LOWER RISK): rectified-crop appearance — attack the Re-ID corruption
The tracker already receives detections that came from rectified tiles. Instead of passing the
equirect frame + equirect bboxes, pass boxmot a representation where the **appearance crops are
rectified**. Options, in order of preference:
- **1a.** Keep the per-frame equirect for geometry but feed boxmot's embedder rectified tile crops
  for each detection (boxmot supports supplying detections against a frame; the cleanest path is to
  build a per-frame canvas of the rectified tiles OR call the embedder directly on tile crops and
  inject `smooth_feat`). Requires checking boxmot's embed hook.
- **1b.** Simpler fallback: leave the tracker as-is for association but recompute the persisted
  Re-ID embedding (`track_embeddings`, `pipeline.py:284-285`) and the jersey-HSV sample
  (`:281`) from the **rectified tile crop** instead of `sample.eq_frame`. This alone de-corrupts the
  embeddings that feed offline stitching (`reid_stitch.py`) and jersey classification — a real,
  isolated win even if the online tracker is untouched. **This is the safe, measurable-in-isolation
  slice.**

### Fix 2 (HIGHER RISK, the real B2): field-plane association
Replace equirect-space motion association with field-meter association. Two sub-approaches:
- **2a. Wrap boxmot (least invasive):** feed boxmot bboxes in a *metric-linear* surrogate space
  instead of raw equirect. Concretely, for each detection compute its field (x_m,y_m) via the
  projector BEFORE tracking, then hand the tracker a synthetic bbox whose center is the field
  position scaled to a fixed px/m and whose size is a constant (or player-height-normalized) box.
  Kalman+IoU then operate in metric space where constant field speed = constant pixel speed and
  box overlap is distance-based. Appearance still comes from Fix 1's rectified crops. Downstream
  `to_dataframe` keeps using the true `bbox_eq` (carried alongside, as today via `det_idx`).
  **This is the recommended core: it reuses boxmot's mature association in the RIGHT space with no
  library fork.**
- **2b. Custom field-space tracker (most work):** replace boxmot with a DeepSORT-style tracker
  operating directly on (x_m,y_m) + rectified-crop embeddings (mirrors the GSR winner). Highest
  fidelity, highest effort, only justified if 2a underperforms.

### Ordering / seam
- Compute field positions for detections *before* `tracker.update` (move the `pixel_to_field_batch`
  call, `pipeline.py:368`, to run per-frame on detections). Cheap.
- `TrackedDetection` already carries both `bbox_crop` and `bbox_eq`; keep `bbox_eq` as the truth for
  `to_dataframe`/foot position, use the metric surrogate only for association.
- Fix the stale `tracking.py:1-6` docstring.

## Prototype plan (build, don't merge)
1. `tracking_field.py` (new) — a `FieldSpaceTracker` implementing 2a: projector-based metric
   surrogate bbox + boxmot association + rectified-crop embeddings (Fix 1). Keep the existing
   `Tracker` untouched behind a `config.TRACK_FIELD_SPACE` flag (default OFF), so prod is unchanged.
2. Unit-test the surrogate mapping: a detection at constant field velocity across latitudes must
   produce constant surrogate-space velocity (the property equirect breaks).
3. **Validation gate (requires a raw game):** re-track ONE GT game with the flag ON, compare against
   the flag-OFF baseline on the SAME game: fragment count (target: well below ~2,887), and per-player
   GT recall via `tracking/player_gt_eval.py`. Only flip the default after the fragment count drops
   AND GT recall does not regress.

## Risks / honesty
- **Unmeasurable now** — no raw game. The prototype + unit tests can be built and reasoned about, but
  the real proof is the re-track fragment-count + GT-recall comparison. Ship nothing to prod until
  that runs.
- **2a surrogate-box sizing** is a design knob (constant vs height-normalized); IoU behavior depends
  on it — the unit test + the re-track sweep settle it.
- Fix 1b is independently shippable and safe (only changes what pixels feed the embedder/HSV, not
  the association) — could go first if a smaller, lower-risk win is wanted before the full 2a.
- Keep `distance_m`/raw fields intact (the 8K before/after comparison depends on them).

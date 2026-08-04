# Insta360 X5 for whole-game per-player tracking — research findings (2026-08-03)

Deep research (11-agent web workflow: 5 strands → adversarial fact-check → synthesis), grounded in
this rig's real specs and the *measured* failure mode. Verified claims are marked; the fact-checker's
caveats are kept in. See also `ACCURACY_AUDIT.md`, `B2_FIELD_SPACE_TRACKING.md`, memory
`per-player-coverage-diagnosis`.

## The rig (from the code, not assumed)
Insta360 X5, single camera, on the **centerline ~3 m behind the near sideline, ~5 m up** on a pole.
8K equirect export (7680×3840). ~50 m-wide U10 pitch subtends ~170° horizontal; far corners at
lon ≈ ±84°. Camera is on the **sideline** → both goals equidistant; the hard limit is the far
**touchline**, not a "far half".

## The core finding: the goal has TWO independent requirements, and the X5 sits on opposite sides

**(A) Continuous COVERAGE (track a body the whole game) — SOLVABLE in software, no new hardware.**
This rig *is* the academic benchmark: **SoccerTrack** (IEEE CVPRW 2022 — single fixed 8K fisheye,
whole pitch, GNSS ground truth). **TeamTrack** (arXiv:2404.13868) shows a fixed fisheye **sideline
view beats a drone top-view** (HOTA 59.3 vs 53.7). Using one sideline 360 cam is *validated*, not a
compromise, for coverage.

**(B) Per-player IDENTITY on 11 same-kit U10s — effectively a dead end from pixels alone.**
**Veo (trained on millions of games) does NOT attempt per-player vision heatmaps** — it gives
TEAM-level heatmaps and derives individual stats from **jersey-number OCR + manual lineup linking**.
The one consumer product that reliably individualizes players (**Trace**) uses a **worn GPS pod**.
No camera or tracker change removes this wall. → *Whole-game per-player heatmaps you can trust by
name require anchoring identity off the pixels.*

## Optics reality (verified arithmetic)
8K equirect = 21.3 px/degree. For a ~1.3 m kid, camera 5 m up: 30 m → ~52 px, 50 m → ~32 px,
far touchline (~53 m) → **~30 px tall, ~8 px wide** (nominal). Reframed 8K effectively resolves
~4K–2.7K, so far-touchline kids are ~17 effective px — the **soft floor** of reliable detection
(gradual degradation, not a cliff). **Near ~two-thirds of the pitch (≤30 m) is comfortably resolved.**
The far touchline is a genuine **physical** dead zone for one camera at 5 m — only a taller mast
(partial) or a 2nd camera (full) recovers it. Everything else (the ~50% coverage / fragmentation) is
an **association software loss**, not a detection loss.

## Why the B2 field-space attempt regressed (52%→28%) — diagnosed, not a mystery
Three stacking causes in `post_game/tracking_field.py`:
1. **Wrong association METRIC, right space.** Tracking in meters was correct; using a **constant 4 m
   box + BoT-SORT IoU** was the error. Constant-box IoU is documented to fail in dense sports — that's
   why **Deep-EIoU / Expansion-IoU** (arXiv:2306.13074) exists. U10 kids cluster < 4 m apart → boxes
   overlap wrong neighbors → Hungarian swaps/drops → fragments.
2. **A projection-DROP bug bleeding coverage.** `tracking_field.py` silently drops every detection
   whose foot ray → NaN field coords (`n_dropped_nan`) — exactly the far-touchline grazing rays. Part
   of the 52%→28% may be coverage thrown away at projection time, not association.
3. **Same-kit appearance can't rescue it.** OSNet cosine is near-uniform on identical U10 kits. Also a
   latent bug: embeddings were computed from the warped equirect frame, not the rectilinear tile.

## The standard method for this rig (what actually wins)
GTATrack (arXiv:2602.00484, the SoccerTrack winner) **discards the Kalman motion model** and uses
**Deep-EIoU + strong ReID + offline global tracklet association (GTA-Link)**. The SoccerNet-GSR
minimap winner (arXiv:2504.06357, GS-HOTA 63.81) runs **DeepSORT in pitch coordinates**. The correct
rebuild for our pipeline:
- **Meter-space distance / Mahalanobis gate + matching cascade** (a POINT gate — two kids 3 m apart
  are 3 m apart, not "overlapping boxes"), hard-gated at a physical U10 max step (~2–3 m at 10 fps).
- **ReID crops from the rectilinear TILE, not the equirect frame.**
- **Offline global tracklet association** (min-cost flow / GTA-style) — our biggest FREE lever since
  we're fully post-game. Caveat: GTA's headline gains lean on appearance (near-noise on same-kit), so
  the spatio-temporal/kinematic half must carry it — stitch on field-space kinematics + tile crops.
- CylindTrack (arXiv:2606.30097) found a full spherical motion model does NOT reliably beat a simpler
  periodic-pixel cost → try the motion-simple route first.

## Verified caveats (do not over-trust)
- The "~0.6× effective resolution" and "17 px" figures are engineering judgment, not spec — order of
  magnitude only.
- The "raw-fisheye → rectilinear tiles via `insv-stitch` MEI model" source-format idea is real in
  *direction* but the rectilinear-tile code **does not exist** (inference), and the calibration is from
  one specific X5 unit — metric accuracy on our camera is unvalidated. R&D, not a quick win.
- FlowState/stabilization **corrupts geometry per-frame** (counter-rotates the sphere → lon/lat→meters
  drifts) — a candidate cause of the regression, and a mandatory future-capture fix (export
  stabilization OFF). NOTE: requires the `.insv` source, so it's **future-games-only** here.
- Several recalled numbers were flagged wrong (lens baseline, PSNR, sensor res) — re-verify any single
  figure before relying on it.

## Ranked recommendations (tied to which requirement each clears)
- **#0 Export FlowState OFF + verify landmark stationarity** — free, but needs `.insv` → **future games only**.
- **#1 Rebuild the tracker correctly** (meter-distance gate + cascade, fix NaN-drop + tile-ReID, offline
  stitch) — ~1–3 wks, $0, **works off cached `tracks_raw.parquet`**. Clears (A) coverage for the near
  two-thirds; NOT identity.
- **#3 Raise mast 5 m → 7–8 m** — biggest *physical* lever for swarm occlusion + far touchline. Clears
  (A) broadly; not (B). (Budget realistically — a real 7.4 m rig, not $100.)
- **#4 2nd 360 on the opposite sideline** — fixes far-touchline resolution/coverage fully; HIGH effort
  (sync, dual-cal, fusion); does NOT touch identity.
- **#5 Anchor IDENTITY off pixels** — jersey-number VLM (the paused Opus probe) + coach lineup-linking,
  or worn GPS pods (total-distance only, ±1–4%; high-speed metrics unreliable on a small pitch). **This,
  not the camera, is the gate on "whole-game per-player".**

## Brutal-truth summary
Not a dead end for **coverage** — the rig is the benchmark, the winning method exists, and the B2
regression was a diagnosable wrong-metric + drop-bug, not "field space is bad". The far touchline is a
real physical dead zone. It IS effectively a dead end for pure-vision per-player **identity** on same-kit
U10s from one sideline camera — that requires identity anchoring, which no camera/tracker change provides.

# Calibration: shape-from-clicks + per-field scale anchor

## Context / the problem

The calibration assumed a fixed 50×35 m field, which is wrong per pitch and put ~2.5 m error on
every corner (measured on Belle River). The instinct "solve field size from the clicks" was tested
and **fails**: with a single grazing camera and no known real-world length in the scene, absolute
scale is not identifiable — a synthetic 58×37 field recovers as 48×30 at low RMS. The coach then
noted the **goal size also varies between U10 fields**, so the goal mouth can't anchor scale either.
Nothing in the image has a known real-world size.

**What the data CAN and CAN'T give (measured):**
- **Shape / aspect ratio + camera tilt: recoverable.** A wrong aspect ratio produces a visibly high
  fit RMS (forcing W 30→35 pushed RMS 0.5→1.44 m), so minimizing RMS over (aspect, tilt) finds the
  right proportions.
- **Absolute scale: NOT recoverable from video** (monocular scale ambiguity). A uniform size error
  maps ~1:1 to distance/speed error (±10% size → ±10% distance).
- Therefore: heatmaps, thirds, formation, field-tilt, and all player-vs-player / game-to-game
  RELATIVE comparisons are exact once shape is right; only ABSOLUTE distance/speed needs true scale.

**Decision (coach):** recover shape from the clicks; fix absolute scale from ONE length the coach
reads off a satellite map (Google Maps) per field — and **persist that per field** so it's entered
once and reused for every future game on that pitch.

## Design

### 1. Solver: recover shape + tilt, take scale as an input (`calibration_solve.py`)
- Replace the "solve L,W freely" idea (proven non-identifiable) with: solve **(pitch, roll, and the
  field ASPECT RATIO)** by RMS-minimization over the clicks, with **absolute scale supplied
  externally**. Concretely: parametrize the field as (L, W) but constrain them by a known
  scale anchor — the map length along one axis (e.g. touchline length L_map). Given L fixed = L_map,
  solve W (aspect) + pitch + roll. That's identifiable (aspect shows up in RMS; L_map fixes scale).
- Keep the current fixed-dims tilt solve (`solve_sphere_tilt`) as the fallback when no scale anchor
  is available.

### 2. Per-field scale store (`firestore_io.py` + `teams/main/fields/<field_key>`)
- Reuse the existing `fields` collection. A field doc holds: `field_key` (coach label, e.g.
  "belle-river-home"), `map_length_m` (the Google-Maps touchline length), `map_source` (a note/URL),
  and optionally the last solved aspect/tilt for reference.
- Games gain a `fieldName` link (currently always None). At calibrate time the coach picks an
  existing field or creates one; the game's calibration records which field it used.

### 3. Calibration UI/flow (`ui_app.py` + `calibrate_flat.py`)
- Before/within calibration: a **field selector** (existing fields dropdown + "new field" with a
  name) and, for a new field, a **map-length input** ("measure the touchline on Google Maps, enter
  meters") with a one-line how-to. Existing field → prefill its stored `map_length_m`, no re-entry.
- On SAVE: the solver uses the field's `map_length_m` as the scale anchor; the solved calibration +
  the field link are written to the game doc, and the field doc is created/updated with the length.

## Open design decisions (resolve before building)
1. **Which axis does the map length measure?** Touchline (length L) is easiest to read on a map
   (longer, clearer). Confirm the coach measures L, and the solver then solves W as aspect.
2. **Field key / naming** — free-text label vs a picked map pin. Start simple: coach types a short
   name; dedupe against existing field docs.
3. **Retro-apply?** Existing games (Belle River etc.) could be re-solved once their field length is
   known — cheap (stats-only recompute), optional.

## Validation (before trusting it)
- Unit test: synthetic field with KNOWN L (as the "map length") + known W/tilt → solver must recover
  W and tilt accurately (this IS identifiable, unlike free L,W — prove it with numbers).
- Real: enter Belle River's map length, confirm the solved W + RMS are sensible (~0.5 m) and the
  corners' residuals drop.
- Confirm relative/shape metrics unchanged when only the scale anchor changes (scale should factor
  out of heatmaps/thirds/formation).

## Honesty note
Even with the map anchor, absolute distance/speed inherit the map-reading precision (±a few %),
plus the rig's far-field depth error (audit). That's fine — it's far better than a blind 50×35
guess, and shape/relative metrics are exact regardless. Report physical metrics acknowledging the
map-anchored scale.

# Click-sampling for per-player position metrics — build plan

**Written 2026-08-13** on `worktree-anchor-queue`. The coach's idea: play the
game back, click on players' names/bodies as it goes, and use the clicks
themselves as the data.

**This is the first per-player approach that measures out as viable.** Every
prior scheme (automatic identity, checkpoint seeding, fragment verification,
click-and-fix, the anchored-claim queue) tried to attach a name to a TRACK and
inherit its trajectory, so all of them inherited the tracking failure and
capped at 12–20% coverage. This one does not use tracks at all: each click is
its own position sample. Tracking quality becomes irrelevant.

---

## 1. The measurement that justifies building it

Position metrics are SAMPLES, not integrals. Measured on both coach-labelled
games (`mqcf9axlvtuyt`, `mqcjsjugchb2i`), sampling a player's real trajectory:

| clicks per player | mean-position error | as % of gap between two players |
|---|---|---|
| 10 | 134 px | 20% |
| 20 | 98 px | 15% |
| **50** | **49 px** | **7%** |
| 100 | 38 px | 6% |
| 200 | 31 px | 5% |

Territory (p10/p90 of depth) lands at 33 px at 50 clicks. Two different players
sit 565–676 px apart, which is the scale that matters.

**Target: 50 clicks/player = ~400 clicks = ~13 min of coach time per game, for
~7% error.** For comparison, per-player metrics today are corrupted by ~23%
contamination that moves a player 36% of the way toward a teammate. This is
roughly 5x better AND honest.

### Two objections, both tested

**Click imprecision does NOT matter.** Adding 30 px of Gaussian jitter to every
click: 55 px → 63 px error at 50 clicks, and at 100 clicks the jittered run was
*lower* than clean. Random error averages out — that is the point of a sample
estimator. **The coach does not need to click precisely on the body.**

**Click CLUSTERING does matter — this is the binding design constraint.** If
clicks bunch into a few viewing windows, error plateaus at ~160–170 px and stops
improving past 20 clicks. Spread is what buys accuracy, not volume.

→ **The app must choose WHEN to sample, not the coach.** Scheduled pauses at
fixed intervals across the whole match. If the coach picks the moments, he picks
where the ball is, and the estimator degrades to the clustered column.

### What this does NOT deliver

**Distance, sprints, top speed.** A click is a position, not a path. You cannot
integrate motion from 50 samples. This limit is real and unaffected by clicking
more. Distance still needs a wearable — that conclusion is unchanged.

---

## 2. Verified technical foundations

All four confirmed live before writing this:

* **`pixel_to_field` works and is already built.** `calibration.FieldProjector`
  (`post_game/calibration.py:159`). Game-level calibration is stored per game
  (`firestore_io.get_game_calibration`), NOT on the game doc — both GT games
  return a 50.0 × 35.0 m field at frame 7680 × 3840. A test click at
  (3939, 2141) projects to (29.5 m, 16.5 m). So a click converts to field
  metres with existing code.
* **Snapping to the nearest detection is usually safe but must be gated.**
  Median distance to the nearest other body is 172 px; only 3.2% of bodies have
  a neighbour within 50 px, but 20.7% have one within 100 px. → snap only when
  the nearest detection is unambiguous (2nd-nearest ≥ 2× further), otherwise
  keep the raw click. Never silently snap to the wrong child.
* **Video is required, and two games have it.** The GT games' raws are DELETED,
  but the two clean-tracked Jul-12 games are on disk at
  `~/Movies/stompers/VID_20260712_Game{1,2}.mp4` (76 GB and 75 GB), both with
  calibration (`mrhvbvwi1gjpn` 55×31 m, `mri01pvelv46d` 55×30 m). **These are
  the pilot candidates.** Note the archival policy deletes raws after verified
  analysis — do the pilot before that happens to these two.
* **Calibration must exist and pass QC**, since a click is meaningless without
  a homography. Both GT games have one; the calibration-quality gate already
  enforces RMS ≤ 1.0 m.

---

## 3. Architecture

Streamlit, matching the existing labeling tools (`stint_label_app.py`,
`composition_app.py`) — NOT the PWA. Reasons: it runs locally where the 8K video
lives, the PWA cannot be tested on localhost (no sign-in), and this is a
coach-desk workflow rather than a phone one.

```
tracking/click_sample_render.py   pre-render sampling frames from the 8K source
tracking/click_sample_app.py      Streamlit: show frame, click, record
post_game/click_samples.py        load samples -> field metres -> position stats
post_game/test_click_samples.py   unit tests
```

### 3.1 Pre-render (`click_sample_render.py`)

A random seek into 8K H.265 costs ~2–4 s. Rendering offline once turns a slow
interactive job into a fast one — the same reason `stint_label_render.py` exists.

* Sample instants on a fixed grid: every `--interval` s (default 30 s) across
  both halves, restricted to play windows (skip halftime via the existing
  `halftime_split`).
* At each instant write a downscaled full-pitch frame (the coach must see ALL
  players to click them) plus a JSON sidecar of that frame's detections
  (`track_id`, foot px) for optional snapping.
* ~53 min / 30 s ≈ 106 frames per game. At 8 visible players that is ~850
  potential clicks, comfortably above the 400 target.

### 3.2 The labeling app (`click_sample_app.py`)

One frame at a time. For each frame:

1. Show the full-pitch frame, downscaled to fit, at known scale.
2. Coach clicks a body, then picks the player from a roster button row
   (`st.columns` of buttons — one tap, no dropdown).
3. Record `(video_time_s, player_id, click_x_px, click_y_px, snapped_track_id?)`.
4. `SKIP` if nobody is identifiable; `NEXT` when done with the frame.

Design rules carried from the earlier labeling attempts:

* **`can't tell` / SKIP is first-class**, never coerced into a guess. The
  composition sampler got 26/30 `__cant_tell__` when it forced a choice.
* **The referee is a fourth category** — he belongs to neither team and roams,
  so offer an explicit button rather than hoping geometry excludes him.
* Progress must be **saved after every frame**, not at the end. A 13-minute
  session that loses its work is worse than useless.
* Show a **per-player click counter** so the coach can see who is under-sampled
  and prioritise, since coverage skew is the main quality risk (measured: some
  players get 0.3 min of clean track today).

### 3.3 Stats (`post_game/click_samples.py`)

* Load samples, project each click through `FieldProjector` → (x_m, y_m).
* Canonical per-half orientation, reusing the existing `our_net_at_x0` logic in
  `stats.py` so heatmaps read the same way as today's.
* Emit ONLY sample-based metrics, per player, each with `n_clicks`:
  average position, territory (p10–p90 depth/width), thirds occupancy, heatmap
  grid, width/depth discipline, H1-vs-H2 drift.
* **Emit NO distance, sprint or speed field at all** — not even caveated. A
  number on screen gets quoted regardless of its footnote.
* Refuse to emit a player's metrics below `MIN_CLICKS` (default 20, where error
  is still 15%); report him as under-sampled instead.

---

## 4. Phasing, with a real gate

**Phase 1 — renderer + 20-frame pilot (half a day).** Render 20 frames from one
game with video, and have the coach click through them. This measures the ONE
thing no simulation can: **can he actually identify players in a downscaled
full-pitch 8K frame?** Median box is 77 px, so this is a genuine risk. Success =
he names ≥4 players per frame with a low SKIP rate.

**GATE: if the pilot shows he cannot identify players from these frames, STOP.**
Fallbacks in order of preference, only if needed: zoomed tiles instead of a full
frame (costs clicks per frame), short clips instead of stills (identity from
movement — the stint work found stills unlabelable at detection-box scale, so
this may be necessary), or dropping to the near half only.

**Phase 2 — full app + one complete game (1 day).** All 106 frames, real
session, timed. Produces the first honest per-player position metrics.

**Phase 3 — validate (half a day).** ⚠ Note the awkward split: the 209 coach
hand-labels are on the GT games, whose **video is deleted**, and the games with
video (Jul-12) have almost no surviving overrides (`mrhvbvwi1gjpn` has 7,
`mri01pvelv46d` has 0 — a re-track wiped its 99). So we cannot directly score
clicks against labels on the same game.

Two usable substitutes:
1. **Internal consistency** — split one game's clicks into two halves of the
   sample and compare the two position estimates per player. Agreement bounds
   the sampling error without needing external truth. This is the same
   odd/even-control idea the half-split scorer already uses.
2. **Click-vs-detection agreement** — where a click snaps unambiguously to a
   detection, the pixel gap measures click precision directly.

Do NOT re-track a pilot game before sampling it: re-tracking is what destroyed
W8's 99 labels, and it would also invalidate any detection sidecar used for
snapping.

**Phase 4 — surface in the PWA (1 day).** Only after Phase 3. Position/territory
cards per player, each showing `n_clicks` and an error band. Never a distance
number from this source.

---

## 5. Risks, honestly

| risk | severity | mitigation |
|---|---|---|
| coach cannot identify players in a still frame | **HIGH — kills it** | Phase 1 pilot before anything else; clip fallback |
| clicks cluster → error plateaus at 170 px | HIGH | app schedules the instants, coach never chooses |
| per-player skew (some kids barely clicked) | MEDIUM | live per-player counter; `MIN_CLICKS` refusal |
| 13 min/game is more than the coach will spend | MEDIUM | 20 clicks/player (~5 min) still gives 15%; degrade gracefully |
| wrong-player clicks poison a player's mean | MEDIUM | measured tolerance is good (10% anchor error ≈ 3% drift), and one bad click is 1/50 of the estimate rather than a whole run |
| no video for a game | LOW | check up front; raws are deleted after analysis by policy |

**The honest framing for the coach:** this delivers *where each kid plays* —
position, territory, role discipline, how it shifts between halves — per player,
for ~13 minutes of his time per game. It does not deliver how far they ran.

---

## 6. Why this is worth building when five other approaches were killed

Every previous attempt needed tracking to connect a name across time. This one
needs tracking for nothing: the coach supplies both the identity AND the
position, and the software supplies only the homography and the arithmetic. The
6.0 s median track lifespan — the wall that closed the tracker bake-off and
parked stint-following — does not appear anywhere in this design.

That is also the reason to be slightly suspicious of it, and why Phase 1 is a
gate rather than a formality: the risk has moved off the algorithm and onto
whether a human can name a 77-pixel child on a screen. That is measurable in
half a day with 20 frames.

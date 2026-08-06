# What to fix before the next re-track

Written 2026-08-06, during the stage-by-stage pipeline walk on `mri01pvelv46d`
(the only game with a valid scale-anchored calibration — the other two fail
calibration QC with the legacy no-map-length solver).

A full re-track is ~2 h of GPU on the 80 GB source, so **land every fix here in
one pass** rather than re-tracking per fix. Everything below is measured on the
clean post-collision-fix cache, not predicted.

---

## 1. Team classification erases our own kit — CRITICAL

`team_classifier.sample_jersey_hsv` drops pixels in the grass band
(`35 <= H <= 85 & S > 60 & V > 50`).

| | hue | inside the dropped band? |
|---|---|---|
| our kit `#16a34a` | **H71** S221 V163 | **YES — deleted** |
| opp kit `#2563eb` | H110 S215 V235 | no — kept |

The filter is asymmetric by construction: it can only damage the green team.
When the drop removes nearly everything the fallback returns the *unfiltered*
ROI, so a green player ends up characterised by grass, skin and shorts.

**Measured** (`tracking/grass_filter_probe.py`, commit `3806c84`) — both paths
over the SAME detections in the SAME real frames:

| | ours:opp | per frame (ours / opp) |
|---|---|---|
| production `sample_jersey_hsv` | **3.9 : 1** | 14 / 2 |
| nearest-kit-hue `_det_kit_color` | **1.11 : 1** | **8 / 7** |
| truth for 7v7 | ~1 : 1 | ~7 / ~7 |

After the grass drop production's own samples are a coin flip: 48% of
detections read nearer our hue, 52% nearer the opponent's. The signal is gone.

**Why it matters more than anything else found in the walk.** Roughly a third of
the "our team" candidate pool is opposition. That inflates the review list (521
player-minutes shown against the 336 our 11 children actually played = 1.55x),
guarantees a large `unknown` share (opponents can never match our roster), and
drags every naming and coverage number computed on top of it.

**Fix.** Route classification through nearest-kit-hue
(`post_game/tracking_pitch.py:62 _det_kit_color`) instead of grass-dropping. Its
docstring already documents this exact failure; it is wired only into
`TRACK_PITCH`, which is default OFF, so production never benefits.

**Two things that make this not a one-liner:**
- `_det_kit_color` needs the **video frame**, not the stored HSV samples, so the
  vote has to be taken inside the Stage 2 loop and carried forward. The cached
  `jersey_samples.npz` is post-drop (only 0.5% of its pixels remain in the grass
  band) — the green signature cannot be recovered from disk.
- It votes **per detection**; a track needs one team. Majority vote across the
  track's detections is the obvious aggregation and should be stable (abstain
  rate is only 3.1%), but decide it explicitly.

---

## 2. Stitcher slack swamps the speed gate — HIGH

`reid_stitch.py:345`:

```python
max_move = min(config.MAX_PLAUSIBLE_SPEED_MS * max(gap, 0.0) + slack_m, ...)
```

`slack_m` (3.0 m, for foot-position noise) is **added** to the speed budget, so
at short gaps it *is* the budget:

| gap | speed budget | +slack | implied max speed |
|---|---|---|---|
| 0.1 s | 0.9 m | 3.0 m | **39 m/s** |
| 0.2 s | 1.8 m | 3.0 m | **24 m/s** |
| 1.0 s | 9.0 m | 3.0 m | 12 m/s |
| 5.0 s | 45.0 m | 3.0 m | 9.6 m/s |

The physics gate is inert exactly where adjacent fragments are most confusable.

**Measured:** 48 of 1358 stitch joins (4%) require >9 m/s, observed up to
**23.7 m/s**, every one at a 0.2–0.3 s gap with a 4.1–5.6 m move — inside the
permitted envelope above, so this is the cause, not a coincidence.

**Fix.** Cap the *implied speed* regardless of slack — e.g. also require
`dist / max(gap, min_dt) <= MAX_PLAUSIBLE_SPEED_MS` — so the slack absorbs
jitter without licensing teleports.

**Preserve this good news:** the stitcher is otherwise sound. The concurrency
test (two raw tracks alive at the same instant inside one tracklet = provably
two people) finds **0 of 517** multi-track tracklets affected, and the tolerance
sweep shows **0% off-schedule time at every tolerance 0–120 s**, so the old
"65% impure tracklets" finding is resolved by the halftime split + sub-slack.
Caveat: concurrency proves merges aren't *provable*, not that they're correct.

---

## 3. Association gate over-reach — carried from prior research

Memory (`tracking-accuracy-findings`) measures the tracker's association gate
over-reaching 13–20x: a player moves ~0.3 m/step while the gate is 3.9–6.0 m,
and a 1.0–1.5 m gate has zero rivals in reach. This walk found the supporting
evidence: **1.2% of per-step moves imply >9 m/s, and they cluster NEAR the
camera (1.54%) rather than far (0.79%)** — the opposite of a projection-geometry
signature, so it is ID switching under congestion, not measurement noise.

Note this is the **same class of bug as #2**: a gate whose slack term swamps its
physics term. Worth fixing as one theme.

Projection noise is not the blocker: per-step distance is 0.10 m median / 0.34 m
p90, matching real U10 motion, so a 1.0–1.5 m gate sits comfortably above the
measurement floor (see the appendix).

---

## 4. Identity has almost no evidence to work with — STRUCTURAL

Not a bug: `identity_assign.py` is a reasonable algorithm run on a starved
input. Worth recording because it caps what any upstream fix can buy.

**The entire identity evidence for this game is 72 anchors**, for 176 tracklets:

| coach event | count | usable as an identity anchor? |
|---|---|---|
| POSITION | 45 | yes — the board template the assigner is built on |
| action events (GOAL/ASSIST/SHOT/…) | 27 | yes |
| SUB | 28 | only gates *who is eligible*, not which tracklet |
| GK_CHANGE | 1 | GK window only |

POSITION is the load-bearing signal, and it is **median 3 events per player for
a whole game** (min 2, max 7, 12 players). 16 of the 45 land in the first ~9
minutes — the coach sets the board at kickoff and adjusts occasionally. So the
"positional template" is a formation diagram sampled a handful of times, not a
record of where a child was.

Worse for discrimination: across all players the board spots span x = 12–44 m of
a 55 m pitch, and individual players' spots range up to 30 m in x. The template
positions overlap heavily, so matching a tracklet to the nearest template entry
is a weak discriminator between *our own* children — which is exactly what
memory's `phase-a-coach-log-outfield-dead` measured (coach-log anchors
individuate only the GK).

**Implication for the re-track.** Fixing #1 removes the opposition from the
candidate pool, which should raise naming a lot in relative terms — but the
ceiling is still set by 72 anchors over 176 tracklets. Do not expect the grass
fix alone to produce high automatic naming. The per-player signal that beat this
ceiling in prior work is the jersey-number VLM
(`phase-b-vlm-jersey-number-works`, precision ~0.79, coverage ~28%), which is
`VLM_IDENTITY`, default OFF and NOT enabled on the clean-baseline run.

**Suggested measurement order after the re-track:** (a) grass fix alone, to
isolate how much of the unknown mass was opposition; (b) then VLM on top, to see
what the only real per-player signal adds against a clean candidate pool.

---

## Appendix: projection sensitivity

A 10 px error in the bbox bottom moves the projected ground point ~1.0 m median
(4.0 m p90); 20 px gives 2.1 m / 8.9 m. In practice the error is correlated
frame-to-frame and cancels — measured per-step distance is 0.10 m median — but
it bounds how tight any distance gate can sensibly be. The 1.0–1.5 m gate in #3
is comfortably above that floor.

---

## Verified healthy — do not re-investigate

- **Stage 2 detection/tracking.** Median **15** bodies inside the pitch (7v7 =
  14 + ref); inside-pitch tracked mass 44,329 s vs ~45,000 s expected = **0.99x**;
  **zero** pairs of distinct track_ids within 1.0 m (dedupe works). The
  "raw tracks hold 156% of on-field time" scare was `stint_purity_confirm`
  counting the 35% of detections that are bench/coaches/parents outside the
  pitch as our-team tracks — a measurement artifact in the audit tool.
- **Stage 3 filters.** Off-field filter (±1.5 m buffer) drops 27% and leaves
  median 16/frame; top-20 cap fires on 5.8% of frames and removes 0.57%, and the
  tracks it touches are longer-lived than average, so it is not cutting players.
- **Halftime.** Collision fixed (H2 ids now start at 4727 > H1 max 4724; 0 ids
  span the break, down from 1257). The footage-based split detected the break at
  +0 s from the logged time and had nothing left to cut — it is now
  defence-in-depth rather than the primary fix.

## Also landed since the last re-track

- Sub-tap slack (`post_game/sub_slack.py`) — off-window drops 23.8% → 10.6%.
- Cache provenance sidecar — warns when a reused checkpoint predates the current
  tracking code, the gap that let the halftime collision go unnoticed for months.

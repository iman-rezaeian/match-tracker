# Anchored per-player metrics — build plan

**Goal:** accurate per-player POSITION metrics without a wearable, by trading
coverage for purity. Written 2026-08-13 on `worktree-anchor-queue` (based on
`dev` @ `b453ad3`).

---

## The premise, and the one measurement it rests on

Per-player metrics fail today because ~23% of a player's attributed frames
belong to another child, and position error scales LINEARLY with that
contamination (30% wrong-child frames moves a player's mean position 36% of the
way toward a teammate's). Coverage is not the problem — purity is.

The measurement that makes this buildable:

| pure coverage | clump length | error on mean position |
|---|---|---|
| **14%** | 2 s | **0.3%** |
| 14% | 5 s | 0.3% |
| 35% | 5 s | 0.2% |

**14% pure coverage is as accurate as 35%.** So the job is not to fix
association (measured dead: 4 BoT-SORT knobs inert, 5 alternative trackers
worse, OSNet appearance confirmed inert three independent ways). The job is to
claim a small amount of trajectory we can PROVE belongs to the right child.

There are **1,191 min of clean teleport-free trajectory** in Game 1 across
11,448 runs. It is not missing. It is unnamed.

### Why a targeted queue rather than post-game narration

Anchors placed where the BALL is (narration) land in already-claimed or short
runs. Anchors placed on the LONGEST clean runs are ~2x more efficient per tap:

| taps | our-team coverage | coach time @4 s/tap |
|---|---|---|
| 50 | 14% | 3 min |
| 100 | 23% | 7 min |
| **200** | **34%** | **13 min** |
| 400 | 50% | 27 min |
| 800 | 69% | 53 min |

vs ~17% for 53 minutes of narrating. (These assume taps land on the longest
runs, so treat them as an UPPER BOUND — Phase 0 measures the real curve.)

---

## Phase 0 — validate on existing labels BEFORE building any UI

**Rationale: anchor correctness is the single load-bearing assumption.** One
wrong name poisons its entire ~90 s run. We have 99 coach hand-labels on W8
`mri01pvelv46d` (37 `__opp__`, 28 `__other__`, 34 real player) already in
`game.identityOverrides`. Use them as ground truth on paper first.

**Deliverable:** `tracking/anchor_queue_probe.py` (READ-ONLY), reporting:

1. **Clean-run inventory** — cut every track at teleports; run-length
   distribution; total clean minutes; how much is our-team.
2. **The real coverage-per-tap curve** — longest-first, restricted to our team,
   against the 424 player-min denominator. Replaces the upper-bound estimate.
3. **Anchor precision by source** — for each of coach events / VLM reads /
   existing overrides, how often does the anchor's name match the coach label
   on the run it lands in?
4. **Poisoning sensitivity** — inject anchor error at 0/5/10/20% and report the
   resulting position-metric error. Tells us the precision bar the queue needs.

**Gate:** if anchor precision is below ~0.9, or the real curve is far under
34% at 200 taps, STOP and report rather than build the UI.

### Scoring rule (inherited from the Stage-2 bake-off — do not repeat its trap)

Judge coverage gains on **clean-minutes-unlocked per tap**, with these as
GUARDS, never objectives:

- **run purity** — a longer run is only better if it is still one child. A
  "win" from welding two children scores well on every volume metric at once.
- **bodies/frame** — never buy a metric by deleting players. Three arms in the
  bake-off showed large teleport reductions that were all illusory (ImprAssoc
  by shattering tracks to 0.1 s, OC-SORT by dropping 20→16 bodies/frame, the
  tag filter by deleting 48% of detections).

Teleports are the weld guard here, not the target.

---

## Phase 1 — the claim pipeline (no UI)

`post_game/anchor_claim.py` + tests:

1. **`cut_clean_runs(tracks_df)`** — split every track at teleports (>7 m/s,
   the established oracle) and time gaps. Returns runs with `(track_id, t0, t1,
   rows)`. Clean BY CONSTRUCTION.
2. **`collect_anchors(game)`** — named instants from coach events, VLM
   `identityDrafts`, and `identityOverrides`. Each carries `(player_id, t,
   source, confidence)`.
3. **`claim(runs, anchors)`** — attach each anchor's name to the run containing
   it. **Unclaimed runs are DISCARDED, not guessed.** Conflicting anchors on one
   run (two different names) → drop the run and report it; never pick a winner.
4. **`priority_queue(runs)`** — unclaimed runs sorted by clean-minutes
   unlocked, for the review pass.

Purity invariant to assert in tests: a claimed run contains no teleport and no
conflicting anchor. Loud failure over silent guessing — the one durable lesson
from the stint-following work.

## Phase 2 — position-only stats on claimed runs

Extend `post_game/stats.py` with a claimed-run path that emits ONLY
sample-based metrics, per player, each with honest coverage:

- average position / heat centroid
- territory (p10–p90 depth and width)
- time in defensive / middle / attacking third
- heatmap grid
- width & depth discipline
- H1 vs H2 positional drift

**Explicitly NOT emitted:** distance, sprints, top speed. They are integrals;
at 34% coverage they measure 34% of the running, biased worst on the hardest
workers. This path must not produce them at all, rather than produce them
caveated — a number on screen gets quoted regardless of its footnote.

Every metric carries `coverage_frac` and `n_runs`.

## Phase 3 — the review queue UI

Reuse `tracking/stint_label_render.py` (pre-renders clips; a random 8K seek
costs ~2 s, so offline rendering is what makes the pass fast) and the Streamlit
app pattern from `stint_label_app.py`.

**Interaction: "Who is this?"** — one clip, one boxed body, coach picks from
roster / `opponent` / `coach-ref` / `can't tell`.

Constraints carried over from the earlier labeling attempts:

- **Clips, not stills.** Median box is 77 px; identity comes from watching
  movement. (Though the renderer's own docstring notes names/numbers ARE often
  legible at clip crop scale — so bias clip selection toward legible moments.)
- **The referee is a fourth category.** He belongs to neither team and roams,
  so no touchline test excludes him. Offer the button.
- **`can't tell` is a first-class answer**, recorded, never coerced into a guess.
- Transcode H.264 + yuv420p + faststart. OpenCV's `mp4v` plays in no browser.

Risk to watch: the composition sampler got 26 of 30 `__cant_tell__` when asking
"who is this?". That was 4-second clips of arbitrary tracklets; this asks about
the longest, highest-value runs, which should be far more answerable. **Phase 0
cannot de-risk this** — it is measurable only with a real pilot, so Phase 3
starts with ~20 clips to check the can't-tell rate before building the full pass.

---

## Out of scope

- Fixing association (measured dead)
- Distance / sprint metrics (need a wearable; not affordable)
- Live voice narration (rejected — game-day stress)
- Post-game narration as an ANCHOR source (2x worse per minute than the queue;
  still worth building separately for EVENT metrics — ball wins, duels,
  clearances — which feed the already-trustworthy score)

## Sequence

Phase 0 → gate → Phase 1 → Phase 2 → Phase 3 pilot (20 clips) → full queue.

Phase 0 is READ-ONLY and answers whether the rest is worth building.

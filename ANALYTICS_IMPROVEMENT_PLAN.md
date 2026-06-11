# Analytics improvement plan

Outcome of an independent review (2026-06-10) of the team/player analysis stack:
live coach logging + scoring (`soccer_team_app.jsx`), post-game pipeline
(`post_game/`), and capture workflow. Ordered by priority. 8K-gated items stay
in `EIGHT_K_RETEST.md` — this plan is everything that does NOT wait for 8K.

**Core theme:** the coach log is the unique asset, but two of its richest
channels are write-only today (action events unused as identity anchors;
zone/pressure tags feed nothing). And the sideline is the worst place to create
granular data — move data creation to video/post-game, keep live capture
minimal.

**Decisions made (2026-06-10):**
- ❌ Focus-player rotation REJECTED — instead: mark **every player** post-game
  from video. Bias is fixed by completeness + evenness, not sampling design.
- ✅ Voice capture PROMOTED to the capture-workflow phase (it is the
  load-bearing seed channel that makes full-roster post-game marking
  sustainable; without seeds, weekly full re-watch dies on coach stamina).
- ✅ Every event gets a provenance stamp (`source: live | bookmark-confirmed |
  voice-confirmed | video-added`).

---

## Phase 0 — Regression harness ✅ COMPLETE (2026-06-10)

Phases 1–2 change numbers parents see. Freeze a baseline first.

- ✅ **0.1 Golden-game snapshot.** `tracking/baseline_snapshot.py` dumps
  per-game PWA scores (all players), identity tables + player_stats from
  analytics docs, identityOverrides, season scores, golden-game candidates.
  Baseline written: `tracking/outputs/baseline/pre-phase1/baseline.json`
  (2 finished games, both with analytics). Re-run after every change with
  `--label <name>` and diff against pre-phase1.
  Run: `set -a; source .env; set +a; .venv-post-game/bin/python -m tracking.baseline_snapshot --label <name>`
- ✅ **0.2 Audit tool fixed.** Scoring replication extracted to
  `tracking/pwa_score.py` (exact PWA port incl. gkFraction blend, JS
  rounding, season/per-game paths + their quirks — see module docstring);
  `tracking/audit_player_score.py` rewritten on top of it. Verified: blend
  activates on Leamington's 5-keeper rotation (gk_frac 0.13–0.87). Coach
  spot-check vs app display still welcome.
  NEW QUIRK FOUND while porting: the PWA SEASON score never counts own goals
  (season event filter excludes OPP_GOAL.ownGoalById; per-game counts them) —
  fold the fix into the Phase 2 scoring release.
  EVIDENCE for 2.2: in the Leamington baseline, 4 partial-stint keepers each
  earned a FULL clean-sheet bonus via the 60 s floor.
- ✅ **0.3 Identity ground truth.** 203 coach-labeled tracklets already exist
  (far beyond the ~30 target) → `tracking/outputs/baseline/identity_labels.json`.
  **Golden games:** keeper-swap = `mq01kuce2i81r` (Leamington, 8 gkChanges);
  messy-identity = `mpyo67cl4uflh` (Windsor Fury, 120 labels).

## Phase 1 — Identity accuracy (pipeline-only, re-runnable from checkpoints)

**STATUS 2026-06-10: PHASE 1 ✅ COMPLETE (1.1–1.4 all landed + published).**
1.4 verification (post-phase1 vs pre-phase1 snapshot): PWA scores byte-
identical (expected — no scoring change shipped); assigned-track recall way
up (Leamington unknowns 1840→1637 with 168 lowconf + 38 review now feeding
stats; Windsor similar); tracked distances rose strongly for most players
(e.g. Kerr 263→2242 m, Hahn 1545→4213 m) and DROPPED for a few (Windsor
Arian 1159→694 m) — reassignment to rightful owners. Distances remain
directional-not-absolute until 4.4 (rate-based display). Bonus shipped:
FIX IDS thumbnails now draw a yellow box around the tracked player (next
re-run). Coach added 11 new Leamington labels post-publish (83→94).
NOTE: a crashed first launch (object-dtype npz arrays vs np.cos in
_circ_mean_hue) was fixed with a float32 cast — npz-loaded jersey samples
are object arrays; cast before ufuncs. Eval harness: `tracking/eval_identity.py` re-runs stages 3-5
from checkpoints READ-ONLY (never writes analytics — pipeline.run would
overwrite production docs). First run per game builds a stage-4 cache
(`tracking/outputs/identity_eval/<game>.stage4.*`); later runs take seconds.
Run: `set -a; source .env; set +a; .venv-post-game/bin/python -m tracking.eval_identity --game-id <id> --label <name>`

- ✅ **1.1 GK-change bug fix.** `_gk_windows()` in `identity_assign.py`,
  built from **GK_CHANGE events** (game-clock → `clock_to_video`), NOT
  `game.gkChanges` (wall-clock ms; also keyed `gkPlayerId`, which legacy
  `_gk_segments` misreads as `playerId` — second latent bug, left in v0).
  Keeper tracklets detected per GK window → assigned to whoever was in goal
  THEN; players excluded from outfield matching only while actually in goal
  (`_gk_overlap_frac > 0.5`). No pipeline.py change needed (events already
  passed). **Validated on Leamington:** 9 GK windows / 5 keepers all
  attributed correctly (pre-fix: everything → starting GK); recovered
  Arian's GK stint; single-keeper games reduce to old behavior by
  construction (Windsor verified).
- ✅ **1.2 Event votes — landed; honest findings:**
  - `_event_votes()` implemented (−25 s/+5 s window; location proxy =
    per-second team centroid, U10 swarm ≈ ball; zone tag sharpens via
    `_zone_center()` through the resolved board orientation;
    `ASSIGN_W_VOTES` wired). Bonus: periods with no POSITION events still
    get event votes (v2 no longer collapses there).
  - **FINDING: both golden games have ZERO zone tags** → votes ran on
    centroid alone → little within-team discrimination. Mechanism ready but
    data-starved; Phase 3's tagging queue (zone backfill) is the unlock.
    Re-measure once zone tags exist.
  - **FINDING (big): the minute budget charged stitched SPAN, not coverage**
    (BoT-SORT keeps ids across ≤20 s gaps → "47-min" tracklets holding
    minutes of detections). It silently strangled recall: on the
    coach-labeled set the OLD code auto-assigned **0/49 player-labeled
    tracklets correctly** — its apparent accuracy was junk being correctly
    left unassigned. Fixed `_tl_minutes` → detections × sample interval.
    To contain the resulting low-conf flood (junk = opponents misclassified
    into our team), `ID_CONFIDENCE_STATS_MIN` 0.20 → **0.35** (sweep in
    `tracking/outputs/identity_eval/mq01kuce2i81r.sweep.json`; coach-tunable;
    revisit after 1.3).
  - **CAVEAT on all label numbers:** coach overrides are *corrections* → the
    eval set is adversarial by construction. Relative deltas meaningful,
    absolutes ≠ overall accuracy. Landed config on hard cases:
    Leamington 23/83, Windsor 36/120.
- ✅ **1.2b SUB anchors.** Tracklet whose FIRST detection appears within
  −30 s/+45 s of a logged sub-on AND within 5 m of a touchline → strong vote
  (`ASSIGN_SUB_W=3.0`) for the incoming player; symmetric LAST-detection ↔
  sub-off. Measured: +2 correct on BOTH golden games (Leamington 23→25,
  Windsor 36→38 on the hard-case label set) — small but consistent.
- ✅ **1.3 Hue-wrap fix — landed as wrap-GATED circular hue, NOT Lab.**
  What landed: per-track circular-safe hue medians + a per-game hue
  rotation, both engaged ONLY when saturated color mass sits at both ends
  of the hue axis (i.e., a red kit). **Byte-identical to production on all
  current footage** (verified: both golden games reproduce the exact
  baseline partition, 83/83 + 120/120 labels) and fixes red kits when they
  appear (unit test: `tracking/test_lab_color.py`).
  **What was tried and FAILED (do not retry blindly):** Lab nearest-anchor
  (78% of tracks pulled onto the green anchor), Lab cluster→anchor (66%),
  cyl-space nearest-anchor (team 0 collapsed to 30 fragments), cyl
  cluster→anchor (63%). Root cause: fixed hex anchors sit far from the
  desaturated grass-tinted colors kits actually have on video, so ANY
  absolute-space change reshuffles the partition. ALSO: coach-override
  labels cannot validate classifier changes (they only exist for tracklets
  the OLD partition put in team 0 — change the partition and they stop
  mapping). The `__opp__` contamination problem therefore stays OPEN —
  real fix is appearance-based / per-kit galleries, gated on 8K (see
  EIGHT_K_RETEST item 5). Numbers preserved in
  `tracking/outputs/identity_eval/`.
- ✅ **1.4 Production re-run + publish — DONE** (both games re-published
  2026-06-10 ~21:10 with all Phase-1 fixes + coach overrides re-applied;
  verification results in the STATUS block above; snapshots in
  `tracking/outputs/baseline/{pre,post}-phase1/`).

## Phase 2 — Scoring integrity ✅ SHIPPED (v2, 2026-06-10)

`SCORING_VERSION = 2`; recalibration note shown in the stats help + scoring
docs. New-reference snapshot: `tracking/outputs/baseline/post-phase2/`.
Verified: full-minute starters barely move (David 2.9/2.3 per-game, season
2.6 ≈ v1); Ben Hahn's relief-keeper inflation gone (Leamington 11.3 → 8.6,
DEF 18.8 → 12.3). Both current games are typed Scrimmage (0.5×) — uniform,
so relative season standings hold.

- ✅ **2.1 Shrinkage.** `(points + (M/20)·squadRate) / ((minutes+M)/20)`,
  M = 12 virtual minutes (⚙ Scoring → FAIRNESS). Squad prior = outfield-
  valued rates over the same game set. Applied per-game AND season.
- ✅ **2.2 Pro-rated clean sheet.** × `secondsAsGK/gameSeconds`, 60 s floor
  removed; fractional clean sheets display rounded to 0.1.
- ✅ **2.3 INV cleanup.** Own goals out of INV; TURNOVER/DUEL_LOSE/FOUL_BY
  count 0 toward INV (`INV_EXCLUDED`).
- ⏳ **2.4 DECISION POINT — per-pillar z-scoring. STILL OPEN** (shipped raw;
  the units caveat is documented in-app). Decide later: z-scored coach view
  vs raw public view.
- ✅ **2.5 Game-type weights.** Season aggregate weights games by
  `tournament` type (scrimmage 0.5 / festival 0.75 / default 1.0; editable
  in FAIRNESS; 0 excludes a type). Season is now built from per-game pillar
  POINTS (weighted), not pooled events.
- ✅ **2.6 Provenance.** `source: 'live'` stamped at logEvent creation;
  future capture paths (bookmark/voice/video) stamp their own.
- ✅ **2.7 `tracking/pwa_score.py` mirrored to v2** (audit + snapshot tools
  updated; per-game shrinkage needs `roster` passed). In-app docs updated
  (stats help, scoring section, weights section → "Three tabs").
- ✅ **2.8 Season own-goal fix** — by construction: season now sums per-game
  `pillarPoints` (full event list incl. `OPP_GOAL.ownGoalById`) instead of
  the pooled playerId-filtered list that silently dropped own goals.
- NOT folded in: pressure multiplier (plan 4.3) — still a decision point,
  pairs with the Phase 3 tagging queue.

## Phase 3 — Capture & review workflow (the centerpiece)

Live job shrinks to: goals, subs, shots, saves, pens + one reflex
bookmark/voice cue. Everything else is created or confirmed from video.

- **3.0 START NOW, ZERO CODE:** record a phone voice memo for the full next
  game, noting the kickoff moment. Narrate naturally. This is the test audio
  (wind, crowd, mid-coaching speech) that the whole voice idea lives or dies
  on — get it before building anything.
- **3.1 Confirm-queue UI** in the film room — shared landing zone for
  untagged events, bookmarks, voice drafts. Each card: event cued in the reel
  via the existing `videoTimeS`/`tvReelTimeS` index, suggested tags
  pre-selected, one-tap confirm/correct → next.
- **3.2 BOOKMARK button** in live flow (1 hour of work; ships with 3.1).
  Single tap, timestamp only, player optional. Insurance channel for when
  speaking isn't possible.
- **3.3 Tag pre-fill from the pipeline.** Write `suggestedZone` (assigned
  player's tracked field position at event time → zone vocab) and
  `suggestedPressure` (nearest-opponent distance < ~3 m) into the
  analytics/broadcast events index. Absurd suggested zone ⇒ free identity
  check feeding FIX IDS.
- **3.4 Trim tag scope.** Zone tags ONLY where location is the insight:
  shots (shot map), turnovers (where we lose it), ball wins (pressing
  height). Pressure on decision events only. Zone/pressure REMOVED from live
  flow entirely — tags happen in the queue.
- **3.5 Voice probe (1 day, gate for 3.6).** Whisper (local, M-series) on the
  3.0 memo. Check: are jersey numbers legible? timestamp drift acceptable?
  Conventions: cue **jersey numbers not names**; fixed ~6-phrase vocabulary
  mapping 1:1 to event types (parsing = lookup, not NLP).
- **3.6 Voice → drafts pipeline (only if 3.5 passes).** Transcript →
  timestamped draft events (`source: 'voice'`) landing in the queue.
  Phone-memo + kickoff offset is fine for season 1; later move recording into
  the PWA (it knows the game clock exactly → sync is free).

## Phase 4 — New analytics views (no correctness risk; interleave freely)

Ordered by coach value per effort.

- **4.1 Momentum chart.** 5-min buckets of shots/ball-wins/turnovers
  for-vs-against from `game.events`, goal/sub markers. Pure client-side.
- **4.2 Shot map from zone tags.** Per-game + season half-field shot chart
  (reuse tactical-board rendering); SHOT_ON/SHOT_OFF/GOAL by zone. Doubles as
  the zone-tagging audit surface.
- **4.3 Pressure multiplier.** DEC points × ~1.5 when `pressure==='pressure'`
  (one line; fold into Phase 2 release if timing aligns). Viable now that
  tags are cheap via the queue.
- **4.4 Rate-based physical stats.** Headline distance =
  `distance_per_tracked_min × coach_minutes` with tracked-coverage % shown
  beside it (coverage is systematically unequal across players — raw sums are
  biased, not just scaled). Keep raw sum in the doc for the 8K before/after.
  Sprint rate too.
- **4.5 Personalized sprint threshold.** Per player:
  `max(4.0 m/s, 0.8 × own season p99 speed)`, from prior analytics docs;
  fallback 4.5 for new players. Record threshold used in output.
- **4.6 Field tilt.** Team-centroid third-occupancy % from existing
  `TeamTimeSeries` — best no-ball possession proxy; gives compactness/width a
  narrative home.

## Phase 5 — 8K-gated (see `EIGHT_K_RETEST.md`) + two additions

- Lock exposure/WB at kickoff if X5 firmware allows (helps team
  classification + Lab distance; zero cost).
- Shoot two consecutive 8K games in the SAME kit (fair same-kit test for the
  appearance-gallery probe in one pass).
- Re-run the Phase 0 baseline on the first 8K game — program-wide
  before/after.

## Ongoing process

- Bookmark/voice classification largely replaces the monthly
  "recount-from-video" audit; keep an eye on per-event-type miss rates via
  the provenance field.
- POSITION-staleness nudge in PWA (banner when an on-field player has no
  POSITION event this period) — protects identity v2's main prior.

## Open decision points (owner: coach)

1. **2.4** z-scored vs raw pillars; coach view vs public view split.
2. **3.5 gate** voice viability after the probe (fallback: bookmarks +
   partial re-watch).

## Sequencing summary

| Order | Item | Why |
|---|---|---|
| 1 | ✅ 0.1–0.3 harness + audit fix (done 2026-06-10) | Everything else needs a trustworthy diff |
| — | 3.0 voice memo at next game | Zero code; de-risks Phase 3 early |
| 2 | ✅ Phase 1 COMPLETE (1.1–1.4, published 2026-06-10) | Contamination fix proper is 8K-gated (appearance); color-space swaps measured + rejected |
| 3 | ✅ 2.x scoring v2 SHIPPED 2026-06-10 (2.4 z-scoring still open) | One coordinated, versioned change |
| 4 | 3.1–3.6 capture workflow | Queue → bookmark → voice probe → voice pipeline |
| 5 | 4.1–4.6 analytics views | Value-add, interleave |
| 6 | 5.x | Blocked on first 8K game |

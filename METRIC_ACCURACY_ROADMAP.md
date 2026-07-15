# Metric-accuracy roadmap — path to trustworthy player & team performance analysis

**Written 2026-07-13. Author: analysis pass over the metric surface + the two 8K GT games.**
Companion to `METRICS_RELEVANCE_PLAN.md` (identity bottleneck) and `ANALYTICS_IMPROVEMENT_PLAN.md`
(the 6-phase plan). This doc reframes "improve accuracy" around the project's actual goal —
helping players and coaches find individual and team strengths/weaknesses and plan growth —
and prioritizes by ROI × how-blocked.

**STATUS (2026-07-13): Tiers 1 & 2 shipped to `beta`.**
- Tier 1 DONE: pressure multiplier (DEC ×1.5) + "vs Squad" percentile toggle (commit 667f426).
- Tier 2 DONE: TEAM SHAPE sparkline card (surfaced the hidden `team_time_series`) + per-half
  `field_tilt` (pipeline fix + both 8K games re-run stats-only, `byHalf` confirmed live) (commit 00d63ed).
- NEXT: Tier 3 (honest-reporting finish) — then Tier 4 (identity recall, VLM-gated).
Both tiers await coach validation on `beta.match-tracker-843.pages.dev` before promotion to main.

## The core finding: two metric systems, opposite accuracy profiles

The system produces metrics from two independent sources. They must be reasoned about
separately, because they fail in completely different ways.

### System A — event-based (PWA), identity-INDEPENDENT — **already trustworthy**
Attribution is the coach's tap (now + voice-confirmed drafts), not CV. Immune to the
identity-recall problem entirely.

- Performance score + ATK/DEF/DEC/INV pillars, per-game & season — `soccer_team_app.jsx`
  `computePerformanceScore` (~:653), `pillarPoints` (~:573), `seasonScores` (~:10935).
- Per-player season counting stats (goals/assists/saves/duels/…) — `StatsView` (~:10890).
- GK metrics (conceded, penalties, pro-rated clean sheets) — `gkExtrasForGame` (~:455).
- Team: momentum (~:9427), shot map (~:9497), season W/D/L/CS (~:8234), formation LABEL
  (coach POSITION board, `formation.py` ~:36).

Accuracy limiter here is **logging completeness**, not CV. Score v2 already added the big
correctness fixes (shrinkage M=12, INV cleanup, pro-rated clean sheet, game-type weights,
season own-goal fix; `SCORING_VERSION=2`).

### System B — tracking-based (CV), per-player, identity-DEPENDENT — **broken at the individual level**
All in `post_game/stats.py compute_player_stats` (~:66), keyed on `identity_by_track`:
distance, top/avg speed, sprints, thirds occupancy, heatmap, work-rate.

Per the two blind-GT 8K games, auto-assignment recall is ~0.03 → ~78% of our own fragments
never get named → **3–4× LOW per-player distance** and same-kit misattribution. The code
already refuses to lie: ⚠ LOW TRACKING (coverage <0.08), ⚠ INFLATED (implausible steps ≥0.30),
movement tiles dashed to "—", `ID_CONFIDENCE_STATS_MIN=0.35` gate. But the *individual*
physical numbers are fundamentally capped until an individuating signal exists.

### System B′ — tracking-based, TEAM-level — **robust-ish**
Aggregates over the whole team-0 centroid, so individual misattribution largely cancels:
field_tilt (`pipeline.py` ~:588), team compactness/width/depth/centroid (`formation.py`
~:254). These give real tactical (team-shape) insight *without* needing individual identity.

## Implication for "the whole point of the project"

Finding **individual** strengths/weaknesses is served TODAY by System A (the score, pillars,
counting stats) — and that's trustworthy. Finding **team** strengths/weaknesses is served by
System A (momentum, shot map, formation) + System B′ (shape metrics) — also robust. The
**broken** part (System B individual physical metrics) is real added value but is the
hardest, most-blocked tier and NOT where the primary insight comes from. So the accuracy
investment should go A → B′ → honest-B → (only then) individual-B recall.

## Prioritized plan

### Tier 1 — Sharpen the trustworthy metrics (unblocked, highest ROI, on-mission)
The event-based system is where coaches actually read individual/team strength & weakness.
Make it sharper at *comparison* (the growth-planning use case), all pure PWA work:

1. **Per-pillar z-scoring (plan 2.4, OPEN).** Raw pillar points are in mixed units; a coach
   can't tell "is this player's DEF strong *relative to the squad*." z-score each pillar
   against the squad distribution → a clean strengths/weaknesses radar per player. Decision
   already framed in the plan as coach-view-vs-public-view. Not yet built (confirmed: no
   `zscore`/`percentile` in the jsx). **This is the single most on-mission unblocked item.**
2. **Pressure multiplier (plan 4.3, OPEN).** DEC points ×~1.5 when `pressure==='pressure'`.
   Now viable because pressure tags accumulate via the confirm queue. ~one-line scoring
   change, coach call. Rewards hard actions, sharpening the DEC signal.

### Tier 2 — Surface & validate the robust team-shape metrics (unblocked)
3. **Team tactical view.** field_tilt, compactness/width/depth, momentum are computed and
   identity-robust but under-surfaced. Validate them against the two GT games (team centroid
   is reliable even when names aren't) and present a "team shape / phases" panel. This is
   novel team-level strength/weakness insight with no identity dependency.

### Tier 3 — Make the broken metrics never lie (partly shipped)
4. **Honest-reporting finish.** Generalize coverage-shrinkage (∝1/coverage), rates-over-
   tracked-time, and confidence bands to every System-B metric so distance/speed/sprints are
   always shown as *labeled estimates with uncertainty*, never as fact. A dashed/banded
   number beats a confident 3×-low one. Prefer spatial/relative (heatmaps, zones, thirds,
   percentiles) over fragile cumulative absolutes.

### Tier 4 — Raise individual tracking recall (blocked / low-confidence, do LAST)
5. **Coach FIX-ID UX** — the only thing producing coverage in production; optimizing it is
   unblocked but is workflow, not accuracy-of-the-algorithm.
6. **Leftover/budget-pass tuning** in `assign_identities_v2` — a measured attempt to raise
   auto-recall while holding precision, evaluated offline on the 2 GT games. GT evidence says
   the ceiling is low without an individuating signal, so expect modest gains.
7. **VLM jersey numbers** — the real individuating signal, but the full build is PAUSED:
   Haiku below the useful bar, deciding Opus run corp-gateway-blocked. Unblock = `ant` oauth
   or an off-VPN key. Highest ceiling, worst current ROI.

## Recommendation
Start Tier 1 (z-scoring + pressure multiplier), then Tier 2 (team-shape view), then Tier 3
(honest reporting). These are unblocked, directly serve strength/weakness analysis, and don't
wait on the corp-blocked identity work. Treat Tier 4 as a separate track gated on VLM access.

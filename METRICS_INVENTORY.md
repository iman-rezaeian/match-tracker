# What this app measures, from where, and how much to trust it

Written 2026-08-14. There was no single place stating this, which is why the app
felt scattered even where it was correct.

Read this before adding a metric, moving one between screens, or quoting a number
in a conversation with the coach.

---

## The one rule

**Every number belongs to exactly one of four sources, and its trustworthiness is
a property of the source, not of the metric.** Two numbers from different sources
must never share a card, a bar or a heatmap widget — the coach reads across, and
identical styling is a claim of equal quality.

| # | Source | Trust | What it can carry |
|---|--------|-------|-------------------|
| 1 | **Coach taps** (events, subs, GK changes) | **High** — he was there | Goals, assists, saves, minutes, performance score |
| 2 | **Coach tags** (click sampling) | **High** — ±1.7 m measured | Per-player position, territory, thirds, heatmap |
| 3 | **Team shape** (body set per instant) | **Medium** — directional | Field tilt, compactness, width, depth |
| 4 | ~~Per-player tracking~~ | **RETIRED** | *nothing — see below* |

### Why source 4 was retired (2026-08-14)

Tracked per-player movement carried ~23% wrong-child contamination on a 6 s
median track life. Distance ran 3–4× low; identity swaps inflated sprints. The
per-game card had grown **four** caveat blocks explaining, per player, why the
three numbers above them should not be read — which is the tell that they should
not have been rendered at all. A number on a card gets quoted regardless of its
footnote.

Removed: `AVG km/h`, `M/MIN`, `SPRINTS`, the tracked heatmap and thirds fallback,
the trust-grade tile chrome, team `KM TOTAL` / `SPRINTS` / `TIME BY THIRD`, and
the season table's distance / sprint / work-rate columns.

**Do not reintroduce any of these without a new source.** The full measurement of
all five recovery paths is in memory under `per-player-numbers-need-a-wearable`:
automatic ~20%, checkpoint seeding 35%, fragment verification 52–62%,
click-and-fix ~1,900 clicks/game, wearable ~100%.

### Why source 3 survives when 4 does not

Team shape is a function of the **set** of body positions at an instant.
Permuting which name attaches to which body leaves it exactly unchanged, so
identity confusion cannot touch it. A distance sum integrates each player's
**path**, which is precisely what contamination corrupts. Aggregation does not
repair a contaminated input when the operation is a sum or a mean over paths —
that is why the team distance roll-up went with the per-player one.

Team shape *is* exposed to wrong-**body** leakage, which is what the size filter
addresses (below).

---

## Source 2: click sampling — the one that works

The coach watches sampled frames and clicks each of his players by name. He
supplies both identity and position, so the only remaining error is sampling
noise.

* **Measured accuracy:** ±1.7 m median (split-half resampling), 649 clicks over
  97 frames on `mrhvbvwi1gjpn`.
* **Optimal effort:** ~20 frames per half (75 s interval), ~10 min/game. The
  binding constraint is roster coverage, not precision.
* **Published:** average position, territory (p10–p90), thirds, side tendency,
  12×8 KDE heatmap, area covered, per-half positions, half-to-half drift with a
  bootstrap CI.
* **Deliberately NOT published:** distance, speed, sprints. A click samples a
  position; between two samples 30 s apart a child could have run 5 m or 80 m.
  **Position metrics are SAMPLES and survive sparsity; distance is an INTEGRAL
  and does not** — biased low, worst on the hardest workers.

Code: `post_game/click_samples.py`, `tracking/click_sample_app.py` (sampler),
`tracking/click_publish.py` (writes `click_stats`).

⚠ Match clicks against the **torso**, never `foot_*_eq` — there is a 45 px median
offset that has already produced one fake accuracy figure.

---

## Source 3: team shape, and the size filter

Only **24.8%** of tracked rows on a clicked frame are one of our players; the rest
are opponents, touchline adults and phantoms. Since team shape reads the whole
body set, every one of them moves the number.

`post_game/adult_filter.py` keeps tracks whose median box height is
player-sized (50–160 px), behind `TEAM_SHAPE_SIZE_FILTER` (default ON).

| filter | ours kept | non-players cut | purity |
|---|---|---|---|
| none | 100.0% | 0.0% | 24.8% |
| `h>=120` (old, one-sided) | 90.1% | 19.8% | 27.0% |
| **outside 50–160 (shipped)** | **94.1%** | **40.0%** | **34.1%** |

⚠ **Purity is still 34%.** Two non-players survive per player, mostly
**opponents** — exactly player-sized and player-placed, so geometry cannot
separate them. Team shape is **directional, never precise**.

⚠ The earlier one-sided version was tuned on a game whose tracking predates the
reprojection fixes and runs at a different scale (rowwise median 94 px vs 69/71).
An absolute pixel threshold does not transfer between those regimes.

---

## Where a coach finds each of these today

| Screen | Route | Sources | Notes |
|---|---|---|---|
| **STATS** | Home → STATS | 1 only | Season event totals + performance score |
| **FILM ROOM** | Home → FILM ROOM | 1, 2, 3 | Per-game analytics panel per game |
| **SEASON ANALYTICS** | Film Room → 📈 button | 1, 2 | Squad table, shot map, W/D/L |
| **Analytics panel** | Film Room → a game | 1, 2, 3 | The per-player deck lives here |

This layout is the actual scattering complaint: **STATS** and **SEASON
ANALYTICS** are both season-level and reachable from different places, and the
per-game deck is buried two taps into Film Room. See
`STATS_CONSOLIDATION_PLAN.md` for the proposal.

---

## Traps that have already cost real measurements

Each of these produced a number that was quoted before being caught.

1. **A bare number is the failure mode.** Six figures died in one week from a
   missing qualifier, not from being wrong. Every number needs window + spatial
   crop + which cache, **stated when created** — by quote time the context is
   gone. Note that `load_frames` and `sweep_score.load` both crop to on-pitch
   ±1.5 m.
2. **Score a filter on what it REMOVED, not what remains.** Both filters shipped
   on 2026-08-08 were scored only on survivors; one was deleting a third of our
   own team. Report `ours kept` alongside `pollutant cut`, always.
3. **An intervention that reduces error by reducing bodies must be scored
   per-BODY.** The opponent filter genuinely cut teleports 3.7×/min — by deleting
   the players you would swap between.
4. **`>= N bodies` gates create fake zeros.** Measuring the size filter over all
   time bins reports **0 px** error, because the metric's own 4-body gate drops
   the bins containing a surviving adult. Report the polluted-bin column.
5. **Tests passing ≠ code running.** The size filter sat written, tested and
   uncalled for a week. `test_adult_filter.py` now asserts the pipeline calls it.
6. **Verify against production first.** Reconcile any offline number against
   `read_analytics(game_id)` before reporting it.

---

## Firestore shape

* `analytics/v1` — the full doc, 420–970 KB/game. The film room opens one.
* `analytics/summary` — projection carrying only the five fields the season view
  reads, ~2.5 KB. The season view fans out over **these**; against the full docs
  it pulled 4.95 MB for seven games (3.4 MB of it `identity_assignments`, which
  it never reads) and opened to a black screen on a phone.

Widening the season table means widening `_SUMMARY_PLAYER_KEYS` in
`firestore_io.py` — `test_analytics_summary.py` pins the two together.

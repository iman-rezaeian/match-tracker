# Centralising stats in the PWA — proposal

Written 2026-08-14, in response to: *"it seems we have stats in many different
places… it seems unorganized."*

Read `METRICS_INVENTORY.md` first — it establishes what each number is and how
much to trust it. This document is only about **where the coach finds it**.

---

## The actual problem, measured

Stats live in **four** places reachable by **three** different routes:

```
Home ─┬─ STATS ──────────────── season, event metrics only
      │                          per-player table: GP · MIN · G · A · SCORE
      │
      └─ FILM ROOM ─┬─ 📈 SEASON ANALYTICS ── season, tracked + tagged
                    │                          per-player table: GP · MIN · tagged
                    │                          + W/D/L, shot map
                    │
                    └─ a game ── ANALYTICS PANEL ── that one game
                                                    per-player deck, team shape,
                                                    field tilt, momentum, reel
```

Three concrete faults:

1. **Two season-level per-player tables.** Both list every player with **GP** and
   **MIN**. `STATS` then goes to goals/assists/score; `SEASON ANALYTICS` goes to
   tagged thirds. Neither mentions the other exists. This is the "stats all over
   the place" complaint in its most literal form.
2. **Season analytics is hidden inside a per-game screen.** "Film Room" is where
   you go to watch *one game*; the season roll-up is a button in the middle of
   that list. Nothing on the Home screen suggests season analytics exist —
   `STATS` looks like it is the season view, and it is a different one.
3. **The best per-player data is two taps deep and per-game only.** Click-tagged
   positions — the ±1.7 m source, the whole point of the rig — appear only inside
   one game's analytics panel. A coach asking "where does Adam play?" has no
   season-level answer even when several games are tagged.

Not a fault, worth stating: the **split itself is principled**. `STATS` is purely
event-derived (source 1) and `SEASON ANALYTICS` mixes sources 1–3. That
distinction is real and worth preserving — it is just invisible, and expressed as
two menu entries instead of one screen with two tabs.

---

## Proposal: one STATS destination, three tabs

Keep exactly one stats entry point on Home. Everything else becomes a tab or a
drill-down inside it.

```
Home ─┬─ STATS ─┬─ [SEASON]  per-player season table, ONE table
      │         │            event columns + tagged-position columns together
      │         │            → tap a player = his season detail
      │         │
      │         ├─ [GAMES]   one row per game (W/D/L, score, date)
      │         │            → tap a game = today's ANALYTICS PANEL
      │         │
      │         └─ [TEAM]    season shape: W/D/L, GD, shot map, field tilt trend
      │
      └─ FILM ROOM ───────── video only: reels, highlights, tagging queue
```

### What moves

| Today | Proposed |
|---|---|
| Home → STATS | STATS → **SEASON** tab |
| Film Room → 📈 SEASON ANALYTICS | merged into **SEASON** + **TEAM** tabs |
| Film Room → a game → Analytics panel | STATS → **GAMES** tab → a game |
| Film Room (reels, tagging, confirm queue) | unchanged — it becomes video-only |

### The merged SEASON table

One row per player, columns grouped by source with a visible boundary:

```
              │ from your taps        │ from your tags
PLAYER    GP  │ MIN   G   A   SCORE   │ TAGGED   AVG m OUT   DEF/MID/ATT
```

The `TAGGED` column (`3/7`) is what makes the tag columns legible: it says how
many of his games the numbers rest on, and shows `—` when none. Tag columns
average over **tagged games only** — never blended with untagged ones.

Tapping a player gives his season detail: the event pillar breakdown that
`PlayerStatsDetail` already renders, plus his season heatmap (KDE over all
tagged games' clicks pooled) and minutes trend.

### Why tabs rather than one long scroll

The three tabs answer three different questions a coach actually asks — "how is
this kid doing", "what happened in that game", "how is the team playing" — and a
phone scroll that mixes them is how the current layout became confusing. Tab
state is cheap and needs no new data.

---

## Season-level tagged positions (new capability)

`SEASON` and the player detail need per-player click stats **pooled across
games**, which nothing computes today.

The `analytics/summary` doc already carries per-game `click_stats.players[]` with
thirds and `avg_depth_m`, so the season table's columns need **no new pipeline
work** — just a weighted mean over tagged games in the client.

A pooled season *heatmap* does need pipeline work: heatmaps are excluded from the
summary doc on purpose (96 floats per player per game). Options, cheapest first:

1. **Client-side pool from the full docs, on demand** — only when a player detail
   is opened, fetching that player's games. No pipeline change; one extra fetch
   per drill-down.
2. **Add a pooled `season_click_stats` doc** written by a new script that reads
   every game's clicks at once. Correct place for it long-term, since pooling
   clicks in field coordinates is what the KDE wants anyway.

Recommend (1) first — it needs no backfill and proves the feature is wanted.

⚠ Pooling across games requires the **half-orientation flip** to be applied per
game before combining, or two games' halves cancel each other. `click_publish.py`
already resolves orientation from the keeper (`our_net_at_x0_from_keeper`) and
applies the flip before computing every published figure, so pool the
**published** numbers, never raw clicks.

⚠ And check `click_stats.oriented` before pooling a game. When the keeper's
median sits mid-pitch the resolver **refuses** rather than guessing — a wrong flip
mirrors a whole half — and that game's figures are then in an undefined frame.
`oriented: false` games must be excluded from the pool, not silently averaged in.
The summary doc carries the flag for exactly this reason.

---

## Sequencing

Each step ships independently and leaves the app coherent.

| # | Step | Size | Risk |
|---|---|---|---|
| 1 | Move SEASON ANALYTICS out of Film Room into a tabbed STATS | small | layout only |
| 2 | Merge the two per-player season tables into one | medium | must keep the source boundary visible |
| 3 | Move the per-game analytics panel under a GAMES tab | small | it is already a modal; only the opener moves |
| 4 | Season heatmap in the player detail (option 1 above) | medium | new fetch path |
| 5 | Strip Film Room to video + tagging | small | do last, once nothing else routes through it |

Steps 1 and 3 are pure navigation and could ship together in one pass.

---

## Deliberately not proposed

* **A single mega-screen.** The complaint is disorganisation, not depth. Four
  surfaces on three routes is the problem; one surface with 40 numbers is worse.
* **Merging STATS into FILM ROOM.** Film Room's job is video. Loading season
  aggregates there is what buried the season view in the first place.
* **Reviving any retired movement metric** to fill the columns freed up in the
  merged table. The empty space is honest; see `METRICS_INVENTORY.md`.
* **A "trust score" per number.** Already tried as per-tile grades. Grading a
  number is a way of shipping one you do not believe; the fix was removing it.

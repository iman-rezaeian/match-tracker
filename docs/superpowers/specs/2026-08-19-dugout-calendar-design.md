# One calendar for the dugout and the parents

**Status:** design, approved in chat 2026-08-19. Not yet implemented.

## Why

The team's schedule lives in TeamSnap. The PWA has been asking the coach to
retype it into a list that exists only in one browser's local storage, so a game
scheduled on the phone is invisible on the laptop and invisible to every parent.
A cron now mirrors the TeamSnap iCal feed into Firestore
(`teams/main/teamsnapEvents`, 116 events as of this writing), which gives us
practices, tournaments, team events and off-days for the first time — data the
app has never had.

That data has nowhere to go. The dugout shows a flat list of coach-entered games;
the parent view shows a flat list of upcoming games. Neither can express "two
games on Saturday at the Gatorade Invitational, then a practice Tuesday", and
neither shows a practice at all.

This replaces both lists with one calendar, used by coaches and parents, and
folds the existing scheduler into it so the two feel like one module rather than
two screens sharing data.

## Scope

In:

* A `CalendarView` component rendering a month grid plus a selected-day detail
  list, used in both the dugout and the parent view.
* A pure merge function combining three sources into one day-keyed model.
* Reusing the coach's `schedule`, which already syncs via Firestore in
  production (see below) — no migration needed.
* Rewiring the dugout: the `SCHEDULE` tile becomes `CALENDAR`, and the
  `UPCOMING GAMES` and `PAST GAMES` sections are removed.
* Rewiring the parent view: the `UPCOMING GAMES` rows become the calendar. Its
  tile grid, including `PAST GAMES`, is untouched.

Out:

* Any change to `StatsView` or `FilmRoomView`. Their boundary was settled in
  `STATS_CONSOLIDATION_PLAN.md` and re-confirmed in this discussion; merging
  them is explicitly rejected there.
* Any change to `AnalyticsPanel`. The calendar becomes a new caller of it, not a
  reimplementation.
* Any change to the TeamSnap sync itself, which is built and running.
* Per-day attendance marking in the parent view (noted as a follow-up).

## Data model

### Three sources, one day

| Source | Where | Owner | Carries |
| --- | --- | --- | --- |
| `teamsnapEvents` | Firestore `teams/main/teamsnapEvents` | the cron | practices, tournaments, team events, off-days, tryouts |
| `schedule` | Firestore, `schedule` array on `teams/main` | the coach | games with match-day setup |
| `games` | Firestore `teams/main/games` | the app | finished games with results |

The merge is a pure function — `buildCalendarModel({ teamsnapEvents, schedule,
games, today })` — returning a map of `YYYY-MM-DD` to an ordered array of
entries. Pure so it can be unit-tested without the app; this is where the bugs
will be.

### Entry kinds

Each entry is one of:

* `game_finished` — from `games`. Carries result (W/L/D) and score. Bar is the
  result colour; tapping opens `AnalyticsPanel`.
* `game_scheduled` — from `schedule`. Carries the full match-day setup, so the
  row can show `READY`, squad count, format, field and a `START` button.
* `practice`, `tryout`, `team_event` — from `teamsnapEvents`.
* `tournament_block` — a `teamsnapEvents` all-day competition day with no
  coach-entered games yet.
* `off` — an explicit "no practice" day. Renders no bar (see Visual language).

### The tournament merge rule

This is the rule the whole design turns on, and it comes from real data. For
2026-08-22 TeamSnap supplies a single all-day block titled
`Gatorade Invitational`, with no kickoff time and no opponent. The coach's
schedule already holds two real games that day — 8:00 AM vs North Oakland and
1:20 PM vs Nationals Macomb, both on field 8N.

Therefore:

* When a day has coach-entered games **and** a TeamSnap all-day competition
  block, the **games** render as the day's bars and the block's title becomes
  their `tournament` context chip. The block does **not** get a bar of its own.
  Aug 22 shows two bars, not three.
* When a day has a competition block and **no** games yet, the block renders as
  a single bar and is the affordance for adding one.
* A `game_finished` supersedes its `game_scheduled` counterpart, matched on the
  existing `date|opponent-lowercased` key.

A day may hold several entries of different kinds — a tournament game and an
indoor practice both fall on 2026-10-23 in the current feed.

### Identity and dedupe

`game_scheduled` entries are keyed by their own `id`, not by
`date|opponent`. The existing dedupe key is retained only for matching a
finished game to the scheduled item it came from, because multi-game tournament
days make `date|opponent` collisions plausible in a way a single-game-per-day
schedule never did.

`teamsnapEvents` entries are keyed by the TeamSnap `uid`, which the sync
guarantees is stable and unique across polls.

## The schedule is already in Firestore

An earlier draft of this design called for migrating the schedule out of local
storage. **That work is unnecessary — it is already done.** `_sync_html.py`
rewrites the app for production, and among its transforms it replaces the
local-storage `persistSchedule` with `teamDoc().set({ schedule: next },
{ merge: true })`, and the local-storage load with a `teamDoc().onSnapshot`
listener that calls `setSchedule(data.schedule)`.

So in production the schedule is a `schedule` array field on `teams/main`: it
already syncs across the coach's devices and is already readable by every allowed
family, because `teams/main` is family-readable. The `storageGet`/`storageSet`
path visible in `soccer_team_app.jsx` is the **local-dev** path only, which is why
reading the JSX alone suggests otherwise.

Two consequences for this work:

* There is **no migration task and no migration risk**. The calendar reads
  `schedule` from the state the app already maintains.
* Anything this plan changes about `persistSchedule` or the schedule-loading
  `useEffect` must keep `_sync_html.py`'s replacements matching. That script
  locates the code to replace by **exact source text** and calls `SystemExit`
  when a match fails, so an innocuous edit to those blocks breaks the production
  build. If a change there is unavoidable, update the corresponding string in
  `_sync_html.py` in the same commit and run the script to prove it still
  applies.

### Interaction with the TeamSnap sync

The coach chose "TeamSnap always wins" for conflicting values, with one
refinement settled in discussion: **when TeamSnap has no value and the coach
supplies one, the coach's value persists.** This is not a corner case — 16 of the
34 games in the feed are all-day blocks with `time: ""` and no field, so kickoff
times and fields for tournament days can only ever come from the coach.

Coach-entered detail therefore lives in the `schedule` array on `teams/main`,
never in the `teamsnapEvents` docs the cron overwrites. The sync physically cannot reach it.
A scheduled game may reference the TeamSnap event it sits under via a
`teamsnapUid` field, which is how a tournament day links its games to its block.

## Visual language

Settled over three mockup rounds. Bars are ~6px in a day cell.

| Kind | Bar |
| --- | --- |
| Game (scheduled/upcoming) | blue `#378ADD` |
| Game won | green `#639922` |
| Game lost | red `#E24B4A` |
| Game drawn | dark grey `#5F5E5A` |
| Practice | purple `#7F77DD` |
| Tryout | amber `#EF9F27` |
| Team event | light grey `#B4B2A9` |
| Off day | no bar at all |

The two greys are deliberately far apart on the ramp: a drawn game is `#5F5E5A`
and a team event is `#B4B2A9`, three stops lighter. Two near-identical greys
would read as a rendering bug rather than a distinction. If they still confuse in
use, move the team event further toward the light end rather than nudging the
draw, which needs to stay dark enough to sit alongside the win and loss colours.

Practices carry the same visual weight as games — a first mockup rendered them as
small dots and that was rejected. An off-day renders no bar because its entire
meaning is that nothing is happening; a bar would imply the opposite.

Cancelled events keep their kind's colour at its 600 stop with an X cross-hatch
over it, 0.8px strokes on a 6px tile (1.0px on the 8px detail badge). Strokes are
the palest tint of the same ramp — **except team-event grey, which needs black
`#2C2C2A` strokes**, because grey has no dark end and a light stroke on
`#B4B2A9` is invisible. Pick the stroke by contrast against the actual fill.

The day cell also carries a dashed border when any of its entries is cancelled,
and a count badge when it holds more than one entry. The grid caps at three bars
per day and relies on the tap-through beyond that; when it truncates, the count
badge still shows the true total.

## Layout

The dugout and the parent view use the same component with different
affordances.

```
┌ CALENDAR ─────────────── + ADD GAME · 🏷️ OPPONENTS ┐   header actions, coach only
│  ‹  August 2026  ›            Synced 8m ago         │
│  S  M  T  W  T  F  S                                │   month grid
│           …  ▁▁  …                                  │   one bar per entry
│                                                     │
├─ Selected day ──────────────────────────────────────┤
│  Aug 22 · 2 events                                  │   detail rows, reusing
│  ⚽ vs North Oakland  GATORADE  9V9  8:00 AM  📍8N  │   the existing row
│     READY · 12 players                    [START]   │   vocabulary
│  ⚽ vs Nationals Macomb  GATORADE  9V9  1:20 PM     │
├─ Next up ───────────────────────────────────────────┤
│  … the next few entries regardless of month …       │   agenda strip
└─────────────────────────────────────────────────────┘
```

The grid orients; the rows are where work happens. The agenda strip preserves the
one thing a month grid is worse at than the old list — scanning everything
upcoming at once, across month boundaries — and is why this reads as one surface
rather than a calendar bolted onto a list.

`Synced Nm ago` is not decoration. TeamSnap's CDN caches the feed for four hours
and their published TTL is one hour, so the calendar can legitimately be behind
what the coach sees in TeamSnap. Without this line a parent assumes realtime and
blames the app.

Detail rows reuse `TournamentChip`, `FormatChip`, the field pill, the map link,
the squad count and the `READY` badge already built in `ScheduleView.renderRow`.
No new row vocabulary.

## Interaction

Tapping a day opens the existing `ScheduleView` form as a sheet, prefilled by
what is on that day:

| Day state | Result |
| --- | --- |
| Has a scheduled game | Opens that game for editing |
| Has a finished game | Light score summary; tapping through opens `AnalyticsPanel` |
| All-day competition block, no games | New-game form with date, tournament name and venue prefilled |
| Timed TeamSnap game (e.g. a scrimmage) | New-game form with time prefilled too |
| Practice / team event / tryout | Read-only detail; these are TeamSnap's to edit |
| Empty | Blank new-game form with the date prefilled |

Row actions — edit, delete, cancel/uncancel, `START` — are carried over
unchanged.

### Feature parity with the SCHEDULE tile

The `SCHEDULE` tile is replaced, not kept alongside, so every capability must
survive. The form component itself is reused rather than reimplemented, so all
fifteen inputs (opponent with autocomplete, date, time, tournament, location,
field, home/away, format, half length, both jersey colours with custom pickers,
squad picker) come along by construction.

Two capabilities need explicit homes because they are not per-day:

* **🏷️ OPPONENTS** (`OpponentManagerModal`) renames an opponent across every
  game and schedule item at once. It is season-wide data hygiene, not a date
  operation, and it matters because opponent spelling drives the
  schedule-to-game match. It becomes a calendar header action, where it already
  lives today.
* **+ ADD GAME** opens a blank form without first picking a day. Today the form
  is permanently open at the top of the screen, so "open SCHEDULE and start
  typing" is existing muscle memory; this preserves it.

## Removals

* Dugout `UPCOMING GAMES` section — the calendar and its agenda strip replace it.
* Dugout `PAST GAMES` section — a second list of finished games alongside Film
  Room's, same filter, same sort, same badges, no distinct job. Results at a
  glance move onto the grid; per-game detail was always `AnalyticsPanel`, which
  the calendar now reaches directly. `STATS_CONSOLIDATION_PLAN.md` set the
  governing rule: one panel reached from several places is fine, two different
  views of the same data is not.
* The `SCHEDULE` tile, replaced by `CALENDAR`.

The parent view's `PAST GAMES` tile stays. Parents have no Film Room and no
STATS, so for them it is the only route to a finished game.

## Structure

`soccer_team_app.jsx` is ~15,800 lines and `ScheduleView` alone is ~560. Adding
a calendar and a merge inline makes a known problem worse, so:

* `buildCalendarModel` is a standalone pure function, unit-tested directly.
* `CalendarView` is its own component taking the model plus a capability flag
  for coach-vs-parent affordances.
* `ScheduleView`'s form is extracted as a component both the calendar sheet and
  any remaining caller can mount. This is the one piece of refactoring the work
  requires; it is not optional, because reimplementing fifteen inputs is how
  parity gets silently lost.

## Testing

The merge function carries the risk, so it gets real tests against the actual
feed data already captured in this repo's probe output:

* Aug 22 2026 — two coach games plus a TeamSnap all-day block yields two
  entries, not three, with the tournament name on both.
* Oct 23 2026 — a tournament block and an indoor practice on the same day yield
  two entries of different kinds.
* A competition block with no coach games yields one `tournament_block` entry.
* A finished game supersedes its scheduled counterpart rather than doubling.
* The four `[CANCELED]` events yield cancelled entries with titles that do not
  also end in the word "cancelled".
* The four `off` days yield entries that render no bar.
* An empty day yields no entry.

Visual states — the five colours, the cancelled cross-hatch on each, the
multi-bar day, the count badge, the truncation at three — are checked in the
browser against the deployed beta, not localhost: the coach cannot sign in on
localhost, so coach-view changes have to be verified on the beta Pages URL.

## Risks

**The `_sync_html.py` coupling.** The production build rewrites the JSX by
matching exact source text. Editing `persistSchedule`, the schedule-loading
`useEffect`, or the surrounding persist functions breaks the build with a
`SystemExit`. Any such edit must update the matching string in `_sync_html.py`
in the same commit, verified by running the script.

**Muscle memory.** The permanently-open form becomes a sheet. `+ ADD GAME`
preserves the old path, but this is a real change to a daily flow.

**Parity by extraction.** Reusing the form component is what makes parity
structural rather than a checklist. If it proves impractical to extract, the
correct response is to stop and re-plan, not to hand-copy inputs.

**Freshness expectations.** The calendar can be up to four hours behind TeamSnap
through no fault of the app. The `Synced Nm ago` line is the only thing standing
between that and a bug report.

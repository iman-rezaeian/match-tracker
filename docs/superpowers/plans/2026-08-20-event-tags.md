# Event Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single free-text `tournament` box with a tag picker — scrimmage / festival / tournament / league — plus a separate free-text name, on games and on coach-created practices, tryouts and team events.

**Architecture:** Split one overloaded field into two. `gameType` holds a value from a fixed set and is the only thing scoring reads; `tournament` keeps its existing free-text role as the competition's *name*. Both render as chips. Existing data needs no migration because the current values (`Festival`, `Scrimmage`) already match the new set once lowercased.

**Tech Stack:** React in a single in-browser-transpiled JSX file, Tailwind, Firestore via `window.fbDb`, `node:test`.

**Spec:** none — this is a bounded follow-up to `docs/superpowers/specs/2026-08-19-dugout-calendar-design.md`. Decisions came from the coach directly and are recorded below.

## Why this is not cosmetic

`tournament` secretly drives player scoring. In `seasonScores`:

```javascript
const typeWeight = (g) => {
  const t = String(g.tournament || '').toLowerCase();
  return (W.gameTypes[t] != null) ? Number(W.gameTypes[t]) : Number(W.gameTypes.default);
};
```

`DEFAULT_WEIGHTS.gameTypes` is `{ scrimmage: 0.5, festival: 0.75, default: 1.0 }`. So the field must match a key **exactly** after lowercasing. A free-text box means `Scrimmage vs Caboto` scores at 1.0 instead of 0.5 — the coach's typing silently changes his players' season scores.

**Measured before starting:** all 14 finished games currently hold either `Festival` (12) or `Scrimmage` (2), both of which lowercase to valid keys. **No existing score is wrong**, so this fixes a latent bug rather than a live one, and no score should move when this ships. That is the regression test.

## Global Constraints

- **Single-file app.** All component code in `soccer_team_app.jsx`. No bundler, no imports, no new npm runtime deps.
- **`_sync_html.py` matches source text exactly** and raises `SystemExit` on a miss. Do NOT edit `persistSchedule`, `persistRoster`, `persistGames`, `persistWeights`, `persistTeamLiveInput`, or the schedule-loading `useEffect`. Run `python3 _sync_html.py` after every change; it must exit 0.
- **The generated block** between `// ── BEGIN calendarModel (generated) ──` and `// ── END calendarModel (generated) ──` comes from `js/calendarModel.mjs` via `scripts/inline_calendar_model.py`. Edit the module, then re-run the inliner. Never hand-edit the block.
- **Tests:** `npm test` (globs `test/**/*.test.mjs`). Never `node --test test/` — Node 26 treats a bare directory as a module entry point and dies.
- **Scoring is versioned.** `SCORING_VERSION` exists so score changes are traceable. This change must NOT alter any existing score, so it does NOT bump the version — if a score moves, something is wrong.
- **Backwards compatibility is mandatory.** Every game and schedule item predates `gameType`. Absent `gameType` must fall back to reading `tournament`, or 14 games of history silently reweight.
- **Commit format:** Conventional Commits, `<type>: <subject>` ≤50 chars, imperative, lowercase, no trailing period. Body wrapped at 72 explaining *why*. No `Co-authored-by`, no mention of Claude.

---

### Task 1: The tag vocabulary and a backwards-compatible weight lookup

**Files:**
- Modify: `soccer_team_app.jsx` — near `FORMATS`/`DEFAULT_WEIGHTS` (~line 580) and `seasonScores` (~line 12415)

**Interfaces:**
- Produces:
  - `GAME_TYPES` — ordered array of `{ key, label }` for `scrimmage`, `festival`, `tournament`, `league`
  - `gameTypeOf(item)` — resolves an item's scoring type with the legacy fallback
  - `DEFAULT_WEIGHTS.gameTypes` gains `tournament: 1.0` and `league: 1.0`

- [ ] **Step 1: Add the vocabulary**

Above `DEFAULT_WEIGHTS`:

```javascript
// The four kinds of fixture, in the order they appear in the picker. `key` is
// what gets stored and what scoring weights are keyed on; nothing else may be
// stored in `gameType`, because an unrecognised value silently scores as a
// full-weight league game.
const GAME_TYPES = [
  { key: 'scrimmage', label: 'SCRIMMAGE' },
  { key: 'festival', label: 'FESTIVAL' },
  { key: 'tournament', label: 'TOURNAMENT' },
  { key: 'league', label: 'LEAGUE' },
];

/**
 * An item's scoring type.
 *
 * `gameType` is the field the picker writes. Every game and schedule item
 * created before it existed has only the free-text `tournament` box, whose value
 * scoring read directly — so fall back to it and keep 14 games of history
 * weighted exactly as they are today. The fallback only matches when the old
 * text happens to BE a type name ("Festival", "Scrimmage"), which is what the
 * real data holds; anything else lands on `league` at weight 1.0, which is what
 * the old code did with it anyway.
 */
function gameTypeOf(item) {
  const explicit = String(item?.gameType || '').toLowerCase();
  if (GAME_TYPES.some((t) => t.key === explicit)) return explicit;
  const legacy = String(item?.tournament || '').toLowerCase();
  if (GAME_TYPES.some((t) => t.key === legacy)) return legacy;
  return 'league';
}
```

- [ ] **Step 2: Add the two new weights**

```javascript
  gameTypes: { scrimmage: 0.5, festival: 0.75, tournament: 1.0, league: 1.0, default: 1.0 },
```

`tournament` and `league` default to 1.0 so **no score changes**; they exist so the coach can tune them in ⚙ Scoring. Keep `default` — `mergeWeights` and the settings UI both read it.

- [ ] **Step 3: Point scoring at the resolver**

In `seasonScores`, replace the inline lowercase with:

```javascript
    const typeWeight = (g) => {
      const t = gameTypeOf(g);
      return (W.gameTypes[t] != null) ? Number(W.gameTypes[t]) : Number(W.gameTypes.default);
    };
```

- [ ] **Step 4: Prove no score moved**

This is the whole point of the task. Capture the season table before and after:

```bash
git stash && python3 _sync_html.py >/dev/null 2>&1
```

There is no headless harness for `seasonScores`, so verify by reasoning against the real data and state it explicitly in your report: all 14 finished games hold `Festival` or `Scrimmage`; `gameTypeOf` finds no `gameType`, falls back to `tournament`, lowercases to `festival`/`scrimmage`, and those keys carry their existing 0.75/0.5 weights. Therefore every `typeWeight` return is unchanged. If you find a game whose value is neither, STOP and report — that game's weight WILL move.

```bash
git stash pop
```

- [ ] **Step 5: Verify and commit**

```bash
python3 _sync_html.py && npm test
```
Expected: exit 0, 24 pass.

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: name the four fixture types for scoring"
```

---

### Task 2: The picker in the form, and both chips on the row

**Files:**
- Modify: `soccer_team_app.jsx` — `GameForm`, `saveCalendarEntry`, `TournamentChip`, `CalendarDayRows`

**Interfaces:**
- Consumes: `GAME_TYPES`, `gameTypeOf` (Task 1).
- Produces: `GameForm` accepts and returns `gameType`; `saveCalendarEntry` persists it; a new `GameTypeChip` component.

- [ ] **Step 1: Add the picker to `GameForm`**

Replace the single `Tournament / Festival` text input with two controls:

1. A segmented control over `GAME_TYPES`, matching the existing home/away toggle idiom (`bg-lime-500 text-stone-950` when active). Seed with `gameTypeOf(initial)` so editing an old game preselects the type its text implies rather than defaulting to league.
2. The existing free-text input, **relabelled** to `placeholder="Competition name (e.g. Canton Cup)"` and bound to `tournament`. This is now the *name*, not the type.

Show both for **every** event type, not just games — the coach asked for tags on practices too. A practice tagged `tournament` with the name `Canton Cup` is meaningful: it is the session at that tournament.

Include `gameType` in the object passed to `onSubmit`.

- [ ] **Step 2: Persist it**

In `saveCalendarEntry`, add `gameType: v.gameType || 'league'` to **both** field sets (game and non-game), and add `tournament` to the non-game set — Task 3 of the previous plan deliberately dropped it, and this restores it now that it is a persisted name rather than a silently-discarded input.

- [ ] **Step 3: Split the chip**

`TournamentChip` currently colours by guessing at the value: `scrimmage` violet, `festival` teal, everything else amber. That guess is exactly what the split removes. Add a dedicated component and keep the existing colours so nothing in the UI shifts:

```javascript
/** The fixture's scoring type. Colours match what TournamentChip used to infer. */
function GameTypeChip({ value }) {
  const t = String(value || '').toLowerCase();
  if (!t) return null;
  const cls = t === 'scrimmage' ? 'bg-violet-500/10 text-violet-400 border-violet-500/30'
            : t === 'festival'  ? 'bg-teal-500/20 text-teal-200 border-teal-400/50'
            : t === 'tournament' ? 'bg-amber-400/25 text-amber-100 border-amber-300/60'
            : 'bg-stone-700/40 text-stone-300 border-stone-600/60';
  return (
    <span className={`inline-block ${cls} border font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded`}>
      {t.toUpperCase()}
    </span>
  );
}
```

`league` gets the neutral stone treatment: it is the default and does not need to shout.

Leave `TournamentChip` in place for the competition *name*, but stop it colouring by inference — a name like "Canton Cup" should not pick up scrimmage violet because of a substring. Give it one neutral style.

- [ ] **Step 4: Render both on rows**

Wherever a row shows `<TournamentChip value={item.tournament} />`, render `<GameTypeChip value={gameTypeOf(item)} />` first, then `TournamentChip` only when a name exists. Use `gameTypeOf`, never the raw field, so legacy rows show the right type.

Search for every `TournamentChip` usage and update each — there are several (calendar day rows, the agenda strip, the parent view).

- [ ] **Step 5: Verify**

```bash
python3 _sync_html.py && npm test && python3 scripts/inline_calendar_model.py
```
Expected: exit 0, 24 pass, "no change".

Then confirm by reading: a legacy game with `tournament: 'Festival'` and no `gameType` shows a FESTIVAL type chip and **no** name chip (the name would be a duplicate of the type). Decide that case deliberately and say what you chose.

- [ ] **Step 6: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: pick a fixture tag instead of typing it"
```

---

### Task 3: Expose the new weights in ⚙ Scoring

**Files:**
- Modify: `soccer_team_app.jsx` — the weights editor (~line 13141, `draft.gameTypes[k]`)

- [ ] **Step 1: Check whether it already iterates**

The editor renders `draft.gameTypes[k]`, so it may already loop over the object's keys — in which case `tournament` and `league` appear automatically and this task is verification only. Read it first and report which case applies.

- [ ] **Step 2: If it hardcodes the list, drive it from `GAME_TYPES`**

Iterate `GAME_TYPES` rather than a literal array, so a future type appears without touching this screen. Exclude `default` from the picker-driven rows but keep its existing editor row if it has one.

- [ ] **Step 3: Verify and commit**

```bash
python3 _sync_html.py && npm test
```

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: tune tournament and league weights"
```

---

### Task 4: Ship to beta

- [ ] **Step 1: Pre-flight**

```bash
npm test && python3 scripts/inline_calendar_model.py && python3 _sync_html.py
```

- [ ] **Step 2: Push**

Beta needs the personal GitHub account: `gh auth switch --user iman-rezaeian`, push `dev`, merge into `beta`, push `beta`, then `gh auth switch --user IRezaeian_rockfoc`. **Never push `main`.**

If `soccer_team_app_standalone_backup.html` shows a diff of only added blank lines, that is known `_sync_html.py` nondeterminism — discard it rather than committing noise.

- [ ] **Step 3: Confirm and hand over**

```bash
curl -sS https://beta.match-tracker-843.pages.dev/ | grep -c GameTypeChip
```

Checklist for the coach: schedule a game and pick each of the four tags; add a competition name alongside; confirm both chips appear on the row; open an old game and confirm its tag is preselected from its old text; check ⚙ Scoring shows tournament and league; confirm season scores are unchanged from before this shipped.

---

## Self-Review

**Coverage:** the coach asked for scrimmage / festival / tournament tags plus a custom field, on games and other events. Task 1 defines the vocabulary and keeps scoring stable; Task 2 delivers the picker plus the separate name field on all event types; Task 3 exposes the new weights; Task 4 ships.

**Placeholder scan:** no TBD/TODO. Every code step carries real code or an explicitly checkable instruction.

**Type consistency:** `GAME_TYPES` and `gameTypeOf` are defined in Task 1 Step 1 and consumed in Task 2 Steps 1/4 and Task 3 Step 2. `gameType` is written by Task 2 Step 2 and read by `gameTypeOf`. `GameTypeChip` is defined in Task 2 Step 3 and used in Step 4.

**Deliberate non-goal:** no data migration. `gameTypeOf`'s legacy fallback means old items keep working untouched, which is safer than rewriting 14 game docs and a schedule array for a field that can be derived.

**Risk:** the free-text box changes meaning from "type or name" to "name only". A coach who types "Scrimmage" into it out of habit now gets a LEAGUE-weighted game with the name "Scrimmage". Mitigation: the picker is adjacent and preselected, and the type chip is always visible on the row, so the mismatch is on screen rather than hidden. Worth watching on beta.

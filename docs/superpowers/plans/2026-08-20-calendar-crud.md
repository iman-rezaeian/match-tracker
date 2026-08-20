# Calendar per-day CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the coach edit, delete and add on every selected day for every event type — not just coach-scheduled games.

**Architecture:** The app-owned `schedule` array gains a `type` field so practices, tryouts and team events can be created alongside games. TeamSnap-mirrored events cannot be deleted at source (read-only feed, partner-gated API, re-mirrored every 15 min), so "delete" on them writes a **hide flag to a separate Firestore collection the cron cannot reach**, and the merge filters hidden events out.

**Tech Stack:** React in a single in-browser-transpiled JSX file, Tailwind, Firestore via `window.fbDb`, `node:test` for the merge.

**Spec:** `docs/superpowers/specs/2026-08-19-dugout-calendar-design.md` (this extends it; the decisions here are recorded in the coach's `parent-calendar-view` memory entry)

## Global Constraints

- **Single-file app.** All component code in `soccer_team_app.jsx`. No bundler, no imports, no new npm runtime deps.
- **`_sync_html.py` matches source text exactly** and raises `SystemExit` on a miss. Do NOT edit `persistSchedule`, `persistRoster`, `persistGames`, `persistWeights`, `persistTeamLiveInput`, or the schedule-loading `useEffect`. Run `python3 _sync_html.py` after every change; it must exit 0.
- **The generated block** between `// ── BEGIN calendarModel (generated) ──` and `// ── END calendarModel (generated) ──` is produced by `scripts/inline_calendar_model.py` from `js/calendarModel.mjs`. Edit the **module**, then re-run the inliner. Never hand-edit the block.
- **Tests:** `npm test` (globs `test/**/*.test.mjs`). Never `node --test test/` — Node 26 treats a bare directory as a module entry point and dies.
- **The merge stays pure** — no React, no `window`, no clock inside `buildCalendarModel`.
- **Firestore rules are NOT deployed by committing them.** Any new collection needs `npx firebase deploy --only firestore:rules --project lasalle-stompers`, which requires the coach's interactive login. A service account bypasses rules, so server-side checks cannot detect a missing rule — verify client reads in the browser console.
- **Never write coach state into `teams/main/teamsnapEvents`.** The cron PATCHes those docs every 15 minutes; anything stored there is lost.
- **Commit format:** Conventional Commits, `<type>: <subject>` ≤50 chars, imperative, lowercase, no trailing period. Body wrapped at 72 explaining *why*. No `Co-authored-by`, no mention of Claude.

---

### Task 1: Event types in the merge and the hide filter

**Files:**
- Modify: `js/calendarModel.mjs`
- Modify: `test/calendarModel.test.mjs`

**Interfaces:**
- Consumes: existing `buildCalendarModel({ teamsnapEvents, schedule, games, today })`.
- Produces:
  - a new optional param `hidden` — `Set<string>` or array of hidden keys
  - `schedule` items may now carry `type: 'game' | 'practice' | 'tryout' | 'team_event'`; absent means `'game'` (every existing item predates the field)
  - entry kinds gain `practice_own`, `tryout_own`, `team_event_own` for coach-created non-game events, so a row can tell app-owned (deletable, editable) from TeamSnap-owned (hideable)
  - `HIDEABLE_KINDS: Set<string>` — the TeamSnap-owned kinds a hide flag applies to

- [ ] **Step 1: Write the failing tests**

Append to `test/calendarModel.test.mjs`:

```javascript
test('a coach-created practice is its own kind, not a game', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [], games: [], today: TODAY,
    schedule: [{ id: 'p1', type: 'practice', title: 'Extra keeper session',
                 date: '2026-08-25', time: '18:00', location: 'Vollmer' }],
  });
  const e = m.days.get('2026-08-25')[0];
  assert.equal(e.kind, 'practice_own');
  assert.equal(e.scheduleId, 'p1', 'app-owned, so it carries a scheduleId');
  assert.equal(e.teamsnapUid, null);
});

test('a schedule item with no type is still a game', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [], games: [], today: TODAY,
    schedule: [{ id: 's1', opponent: 'Caboto', date: '2026-08-25', time: '11:00' }],
  });
  assert.equal(m.days.get('2026-08-25')[0].kind, 'game_scheduled');
});

test('a hidden teamsnap event is dropped from the model', () => {
  const args = {
    teamsnapEvents: [TS.practice23], schedule: [], games: [], today: TODAY,
  };
  const shown = buildCalendarModel(args);
  assert.equal(shown.days.get('2026-10-23').length, 1);

  const hiddenModel = buildCalendarModel({ ...args, hidden: [`ts:${TS.practice23.uid}`] });
  assert.equal(hiddenModel.days.has('2026-10-23'), false, 'day drops out entirely');
});

test('hiding is keyed by entry key, so it cannot hide the wrong event', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.practice23, TS.terror23], schedule: [], games: [], today: TODAY,
    hidden: [`ts:${TS.practice23.uid}`],
  });
  const kinds = m.days.get('2026-10-23').map((e) => e.kind);
  assert.deepEqual(kinds, ['tournament_block'], 'only the named event is hidden');
});

test('coach-created events are never hideable, they are deletable', () => {
  assert.equal(HIDEABLE_KINDS.has('practice'), true);
  assert.equal(HIDEABLE_KINDS.has('tournament_block'), true);
  assert.equal(HIDEABLE_KINDS.has('practice_own'), false);
  assert.equal(HIDEABLE_KINDS.has('game_scheduled'), false);
});

test('own-practice colours match their teamsnap equivalents', () => {
  assert.equal(entryColor({ kind: 'practice_own' }), ENTRY_COLORS.practice);
  assert.equal(entryColor({ kind: 'tryout_own' }), ENTRY_COLORS.tryout);
  assert.equal(entryColor({ kind: 'team_event_own' }), ENTRY_COLORS.team_event);
});
```

Update the import line to pull in `HIDEABLE_KINDS`.

- [ ] **Step 2: Run to verify they fail**

Run: `npm test`
Expected: the six new tests fail (`HIDEABLE_KINDS` is not exported; `practice_own` is not produced).

- [ ] **Step 3: Implement in the module**

In `js/calendarModel.mjs`:

1. Add to `ENTRY_COLORS`, reusing the same hues so an app-owned practice looks identical to a synced one — the coach should not have to care which system owns it:

```javascript
  practice_own: '#7F77DD',
  tryout_own: '#EF9F27',
  team_event_own: '#B4B2A9',
```

Add the matching entries to `CANCELLED_FILL` and `CANCELLED_STROKE` (`practice_own` mirrors `practice`, `tryout_own` mirrors `tryout`, and `team_event_own` mirrors `team_event` — **black `#2C2C2A` strokes**, because grey has no dark end).

2. Export the hideable set, with the reason in a comment:

```javascript
/**
 * Kinds a hide flag applies to: everything TeamSnap owns. The cron re-mirrors
 * the feed every 15 minutes and the feed is read-only, so these cannot be
 * deleted — only hidden locally. Coach-created entries are genuinely deletable
 * and must NOT appear here, or delete would silently become hide.
 */
export const HIDEABLE_KINDS = new Set([
  'practice', 'tryout', 'team_event', 'game_unscheduled', 'tournament_block',
]);
```

3. Add `hidden = []` to the signature and build a lookup:

```javascript
export function buildCalendarModel({ teamsnapEvents = [], schedule = [], games = [], today, hidden = [] }) {
  const hiddenKeys = hidden instanceof Set ? hidden : new Set(hidden);
```

4. In the `push` helper, drop hidden entries before they reach a day — so a day whose only event is hidden disappears from the map entirely rather than rendering an empty cell:

```javascript
  const push = (entry) => {
    const k = dayKey(entry.date);
    if (!k) return;
    if (hiddenKeys.has(entry.key)) return;
    if (!days.has(k)) days.set(k, []);
    days.get(k).push(entry);
  };
```

5. In the schedule loop, branch on `type`. A non-game schedule item has no opponent, so it must not render as "vs undefined":

```javascript
  for (const s of schedule) {
    const k = dayKey(s.date);
    const sType = s.type || 'game';
    if (sType !== 'game') {
      push({
        key: `sched:${s.id}`,
        kind: `${sType}_own`,
        date: k,
        time: s.time || '',
        title: s.title || sType.replace('_', ' '),
        opponent: '', tournament: '', field: s.field || '',
        location: s.location || '', venue: '', arrival: '',
        allDay: false, cancelled: !!s.cancelled,
        result: null, ourScore: null, oppScore: null,
        gameId: null, scheduleId: s.id, teamsnapUid: null,
        raw: s,
      });
      continue;
    }
    if (finishedKeys.has(`${k}|${norm(s.opponent)}`)) continue;
    scheduledDays.add(k);
    // ... existing game_scheduled push unchanged
  }
```

Note `scheduledDays` must only collect **game** days, or a coach-created practice on a tournament day would absorb the tournament block.

- [ ] **Step 4: Run to verify they pass**

Run: `npm test`
Expected: all tests pass (18 existing + 6 new = 24).

- [ ] **Step 5: Re-run the whole-feed check**

Run: `node scripts/calendar_model_check.mjs test/fixtures/teamsnap-sample.ics`
Expected: `OK`, unchanged counts — 116 entries, 0 invalid kinds. The new kinds only appear when a coach creates one, so real-feed output must not move.

- [ ] **Step 6: Commit**

```bash
git add js/calendarModel.mjs test/calendarModel.test.mjs
git commit -m "feat: let the coach own practices and hide feed events"
```

---

### Task 2: Persist the hide flags

**Files:**
- Modify: `soccer_team_app.jsx` (a new listener + writer in `App`, and the same listener in `PublicHomePage`)
- Modify: `firestore.rules`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (the model takes `hidden` as data).
- Produces: `hiddenEventKeys` (a `Set<string>`) plus `hideEvent(entryKey)` / `unhideEvent(entryKey)` in `App`; `hiddenEventKeys` in `PublicHomePage`.

Storage: `teams/main/calendarHidden/{docId}`, one doc per hidden entry, `{ key, hiddenAt }`. A **separate collection** because the cron PATCHes every doc in `teamsnapEvents` — anything stored there is destroyed on the next tick. Doc ids must be sanitised: an entry key is `ts:9230745-366776389`, and `/` is illegal in a Firestore id while `__…__` is reserved.

- [ ] **Step 1: Add the rule**

In `firestore.rules`, beside the `teamsnapEvents` block:

```
      // --- calendarHidden/<id>: coach-hidden calendar entries ----------
      // TeamSnap events cannot be deleted — the feed is read-only and the cron
      // re-mirrors it every 15 minutes — so "delete" on a synced event records
      // a hide flag here instead. Deliberately NOT stored on the teamsnapEvents
      // docs, which the cron overwrites wholesale each run.
      //
      // Families read it so a hidden event disappears for them too; only a
      // coach writes it.
      match /calendarHidden/{docId} {
        allow read: if isAllowed();
        allow write: if isCoach();
      }
```

- [ ] **Step 2: Add the listener and writers in `App`**

Next to the `teamsnapEvents` listener (a NEW self-contained effect — do not extend the existing one, which `_sync_html.py` rewrites by exact text):

```javascript
  // Coach-hidden calendar entries. Separate collection because the cron
  // overwrites every teamsnapEvents doc on each run.
  const [hiddenEventKeys, setHiddenEventKeys] = useState(() => new Set());
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('teams').doc('main').collection('calendarHidden')
      .onSnapshot(
        (snap) => {
          const s = new Set();
          snap.forEach((d) => { const k = d.data()?.key; if (k) s.add(k); });
          setHiddenEventKeys(s);
        },
        (err) => console.error('calendarHidden listen failed', err)
      );
    return () => unsub();
  }, []);
```

Doc id sanitiser and writers — `hideDocId` must be deterministic so unhide finds the same doc:

```javascript
  // ':' and '/' are not usable in a Firestore doc id, and '__x__' is reserved.
  const hideDocId = (key) => String(key).replace(/[^A-Za-z0-9-]/g, '_');

  const hideEvent = async (key) => {
    if (!window.fbDb || !key) return;
    try {
      await window.fbDb.collection('teams').doc('main').collection('calendarHidden')
        .doc(hideDocId(key)).set({ key, hiddenAt: Date.now() });
      showToast('🙈 Hidden from the calendar');
    } catch (e) { console.error('hide failed', e); showToast('⚠️ Could not hide that'); }
  };

  const unhideEvent = async (key) => {
    if (!window.fbDb || !key) return;
    try {
      await window.fbDb.collection('teams').doc('main').collection('calendarHidden')
        .doc(hideDocId(key)).delete();
      showToast('👁️ Restored');
    } catch (e) { console.error('unhide failed', e); showToast('⚠️ Could not restore that'); }
  };
```

- [ ] **Step 3: Mirror the listener into `PublicHomePage`**

`PublicHomePage` is a **separate React root** reached by a route branch, not a child of `App`, so it cannot receive props. Add the same read-only listener there (no writers — parents never hide).

- [ ] **Step 4: Pass `hidden` into both models**

In both `buildCalendarModel` `useMemo` calls, pass `hidden: hiddenEventKeys` and add it to the dependency array.

- [ ] **Step 5: Verify**

```bash
python3 _sync_html.py && npm test && python3 scripts/inline_calendar_model.py
```
Expected: exit 0, 24 tests pass, "no change" from the inliner.

Then confirm the listener survived into the bundle:
```bash
grep -c "calendarHidden" soccer_team_app_standalone_backup.html
```
Expected: ≥ 2 (coach + parent listeners).

- [ ] **Step 6: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html firestore.rules
git commit -m "feat: store coach-hidden calendar entries"
```

- [ ] **Step 7: Tell the coach the rules need deploying**

Rules do NOT ship with code. Report that this command is required, and that hiding will fail with `PERMISSION_DENIED` until it runs:

```bash
cd ~/match-tracker && npx firebase deploy --only firestore:rules --project lasalle-stompers
```

---

### Task 3: A type picker in the form, and actions on every row

**Files:**
- Modify: `soccer_team_app.jsx` — `GameForm`, `CalendarDayRows`, `CalendarView`, and `App`'s save handler

**Interfaces:**
- Consumes: `hideEvent`/`unhideEvent` (Task 2), the new kinds (Task 1).
- Produces: `GameForm` accepts and returns `type`; `CalendarDayRows` takes `onHideEntry`.

- [ ] **Step 1: Add the type picker to `GameForm`**

A segmented control at the top — GAME / PRACTICE / TRYOUT / TEAM EVENT — matching the existing home/away toggle idiom (`bg-lime-500 text-stone-950` when active). Seed from `initial.type || 'game'` and include `type` in the object handed to `onSubmit`.

When the type is not `game`, hide the game-only fields — opponent, home/away, format, half length, both jersey colours, squad — and show a **title** input instead (placeholder `e.g. Extra keeper session`). Those fields are meaningless for a practice, and the submit guard must change with them: `game` requires opponent + date, everything else requires date only.

Keep date, time, location and field for all types.

- [ ] **Step 2: Branch the save handler**

`saveCalendarGame` currently returns early unless `v.opponent` is set, which would silently discard every practice. Rename to `saveCalendarEntry` and branch:

```javascript
  const saveCalendarEntry = (v, scheduleId) => {
    const type = v.type || 'game';
    if (!v.date) return;
    if (type === 'game' && !v.opponent) return;
    const fields = type === 'game'
      ? { type: 'game', opponent: v.opponent, date: v.date, time: v.time || '',
          tournament: v.tournament, location: v.location, field: v.field,
          isHome: v.isHome, format: v.format, halfLengthMin: v.halfLengthMin,
          homeColor: v.homeColor, awayColor: v.awayColor,
          squadIds: Array.isArray(v.squadIds) ? v.squadIds : [] }
      : { type, title: v.title || '', date: v.date, time: v.time || '',
          location: v.location, field: v.field };
    // ... existing add-vs-edit branch, with the toast wording keyed on type
  };
```

Update every call site and prop that referenced `saveCalendarGame`.

- [ ] **Step 3: Give every row its actions**

In `CalendarDayRows`, under `canEdit`:

- `practice_own`, `tryout_own`, `team_event_own` — edit ✏️ (opens `GameForm` with that type), cancel 🚫, delete 🗑️ with confirm. Same actions as `game_scheduled`; these are app-owned.
- `practice`, `tryout`, `team_event`, `game_unscheduled`, `tournament_block` — a **hide** action (🙈) with a confirm that says plainly what hiding means: *"TeamSnap will keep sending this event. Hiding removes it from the calendar for you and for parents."* Do NOT offer edit or delete on these: an edit would be overwritten within 15 minutes, and a delete is impossible.

`game_unscheduled` and `tournament_block` keep their existing "schedule this / + GAME" action alongside hide.

- [ ] **Step 4: Add an unhide list**

In `CalendarView`'s header under `canEdit`, when `hidden.size > 0`, show a `🙈 HIDDEN (n)` button opening a small panel that lists hidden entries with a **RESTORE** button each. Without this, hiding is irreversible from the UI and the coach has no way to discover what they hid — the entries are gone from the grid by definition.

The panel needs the hidden entries' details, which the model has filtered out. Simplest correct approach: build a second model with `hidden: []` and read the entries whose keys are in the hidden set.

- [ ] **Step 5: Verify**

```bash
python3 _sync_html.py && npm test && python3 scripts/inline_calendar_model.py
```
Expected: exit 0, 24 pass, no inliner change.

Then walk each kind and confirm the intended actions are present and no others:
- coach game → edit, cancel, delete, START
- coach practice/tryout/team event → edit, cancel, delete, **no START**
- TeamSnap practice/tryout/team event → hide only
- TeamSnap unscheduled game / tournament block → schedule + hide
- off day → no actions
- parent view (`canEdit={false}`) → no actions at all on any kind

- [ ] **Step 6: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: edit, delete and add any event from a day"
```

---

### Task 4: Ship to beta and hand over

**Files:** none (build + deploy)

- [ ] **Step 1: Pre-flight**

```bash
npm test && python3 scripts/inline_calendar_model.py && python3 _sync_html.py
```

- [ ] **Step 2: Push dev, merge to beta**

Beta deploys need the personal GitHub account: `gh auth switch --user iman-rezaeian`, push, merge `dev` into `beta`, push `beta`, then `gh auth switch --user IRezaeian_rockfoc` to restore corp state. **Never push `main`** — promotion is the coach's.

- [ ] **Step 3: Confirm the bundle**

```bash
curl -sS https://beta.match-tracker-843.pages.dev/ | grep -c calendarHidden
```
Expected: ≥ 2.

- [ ] **Step 4: Hand over with the rules-deploy reminder**

Hiding will fail with `PERMISSION_DENIED` until the coach runs the rules deploy from Task 2 Step 7. State this first, not as a footnote — it is the one step that blocks the whole feature.

Checklist for the coach: add a practice from an empty day; edit and delete it; hide a TeamSnap practice and confirm it vanishes for parents too; restore it from `🙈 HIDDEN`; confirm a hidden event stays hidden after the next 15-minute sync (the real test of the design); confirm parents see no action buttons.

---

## Self-Review

**Spec coverage:** the coach asked for edit / delete / add per day for any event type. Task 1 models coach-owned non-game events and the hide filter; Task 2 persists hides durably; Task 3 delivers the UI for all three verbs across every kind; Task 4 ships it.

**Placeholder scan:** no TBD/TODO. Every code step carries real code or an explicit, checkable description.

**Type consistency:** `HIDEABLE_KINDS` is defined in Task 1 and consumed in Task 3. `hidden` is a param in Task 1, supplied in Task 2 Step 4, and surfaced in Task 3 Step 4. `hideEvent`/`unhideEvent`/`hideDocId` are defined in Task 2 and used in Task 3. The `*_own` kinds are produced in Task 1, coloured in Task 1 Step 3, and given rows in Task 3 Step 3. `saveCalendarGame` → `saveCalendarEntry` is renamed once, in Task 3 Step 2, with call sites updated in the same step.

**Known risk:** the unhide panel builds a second model with `hidden: []`. That doubles merge work on a view that already memoises, which is acceptable for ~120 events but should be revisited if the feed grows much larger.

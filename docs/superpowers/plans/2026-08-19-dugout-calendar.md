# Dugout Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dugout's and parent view's flat game lists with one shared month calendar that merges TeamSnap-synced events, coach-scheduled games and finished games, with the existing scheduler folded into it.

**Architecture:** A pure `buildCalendarModel()` function merges three data sources into a day-keyed map; a `CalendarView` component renders a month grid plus a selected-day detail list and is mounted by both the dugout and the parent view with a capability flag. `ScheduleView`'s game form is extracted into a reusable component so the calendar's edit sheet is the same form, not a copy.

**Tech Stack:** React (no build step — a single JSX file transpiled in-browser), Tailwind classes, Firestore via the compat `window.fbDb` global, Node's built-in `node:test` for unit tests.

**Spec:** `docs/superpowers/specs/2026-08-19-dugout-calendar-design.md`

## Global Constraints

- **Single-file app.** All component code lives in `soccer_team_app.jsx` (~15,800 lines). Do not introduce a bundler, module imports, or new npm runtime dependencies. The app is transpiled in-browser from one file.
- **`_sync_html.py` matches source text exactly.** It rewrites the JSX for production and raises `SystemExit` when a match fails. Do NOT edit `persistSchedule`, `persistRoster`, `persistGames`, `persistWeights`, `persistTeamLiveInput`, or the schedule-loading `useEffect` without updating the matching string in `_sync_html.py` in the same commit and re-running it. Tasks in this plan are designed to avoid touching them.
- **The schedule already syncs.** In production the `schedule` array is a field on `teams/main`, read via `onSnapshot` and written by `persistSchedule`. The `storageGet`/`storageSet` code in the JSX is the local-dev path only. There is no migration.
- **No test runner exists.** Tests use `node:test` + `node:assert`. Run them with `npm test`, which globs `test/**/*.test.mjs`. Do NOT pass a bare directory (`node --test test/`) — Node 26 resolves that as a module entry point and dies before running anything. Do not add jest/vitest.
- **Pure merge logic must stay pure** — no React, no `window`, no `Date.now()` inside `buildCalendarModel`; `today` is a parameter. This is what makes it testable.
- **Colours are fixed** (spec §Visual language): game (scheduled or unscheduled) `#378ADD`, won `#639922`, lost `#E24B4A`, drawn `#5F5E5A`, practice `#7F77DD`, tryout `#EF9F27`, team event `#B4B2A9`, off = no bar. Cancelled = same kind's 600 stop + X cross-hatch, 0.8px stroke on a 6px tile, 1.0px on the 8px badge, light strokes except team-event grey which uses black `#2C2C2A`.
- **Verify coach-view changes on the deployed beta**, not localhost — the coach cannot sign in on localhost.
- **Commit message format:** Conventional Commits, `<type>: <subject>` ≤50 chars imperative lowercase, body wrapped at 72 explaining *why*. No `Co-authored-by` trailers, no mention of Claude. If a commit subject quoted in a task exceeds 50 chars, the 50-char rule wins — shorten it.

---

### Task 1: The merge function and its test harness

Establishes `node:test` in a repo with no test runner, and builds the pure merge that carries all the design risk.

**Files:**
- Create: `js/calendarModel.mjs`
- Create: `test/calendarModel.test.mjs`
- Modify: `package.json` (add a `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `buildCalendarModel({ teamsnapEvents, schedule, games, today }) -> { days: Map<string, Entry[]>, byMonth: Map<string, string[]> }`
  - `Entry = { key, kind, date, time, title, tournament, opponent, field, location, venue, arrival, cancelled, result, ourScore, oppScore, gameId, scheduleId, teamsnapUid, allDay, raw }`
  - `kind` ∈ `'game_finished' | 'game_scheduled' | 'game_unscheduled' | 'practice' | 'tryout' | 'team_event' | 'tournament_block' | 'off'`
  - `ENTRY_COLORS: Record<kind|'won'|'lost'|'drawn', string>`

`js/calendarModel.mjs` is an ES module so `node:test` can import it. Task 6 inlines its source into the JSX (the app has no module loader); the module stays the source of truth and the test target.

- [ ] **Step 1: Write the failing test**

Create `test/calendarModel.test.mjs`. Fixtures use real UIDs and dates from the live feed.

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCalendarModel } from '../js/calendarModel.mjs';

const TS = {
  ecslEnd:   { uid: '9230745-355316144', title: 'ECSL End Festival- confirmed', type: 'game', date: '2026-08-22', time: '', allDay: true, canceled: false, venue: '', arrival: '12:00 PM' },
  gatorade22:{ uid: '9230745-366776389', title: 'Gatorade Invitational in Brighton Michigan', type: 'game', date: '2026-08-22', time: '', allDay: true, canceled: false, venue: 'Brighton', arrival: '1:00 PM' },
  gatorade23:{ uid: '9230745-366776390', title: 'Gatorade Invitational in Brighton Michigan', type: 'game', date: '2026-08-23', time: '', allDay: true, canceled: false, venue: 'Brighton', arrival: '1:00 PM' },
  terror23:  { uid: '9230745-371912449', title: 'Terror Island Invitational ( tentative tournament)', type: 'game', date: '2026-10-23', time: '', allDay: true, canceled: false, venue: '', arrival: '' },
  practice23:{ uid: '9230745-371918337', title: 'Practice indoor', type: 'practice', date: '2026-10-23', time: '18:30', allDay: false, canceled: false, venue: 'Legacy Oak Trail School', arrival: '' },
  cancelled: { uid: '9230745-363188254', title: 'Practice- Outdoor', type: 'practice', date: '2026-06-23', time: '17:30', allDay: false, canceled: true, venue: 'Vollmer', arrival: '' },
  offDay:    { uid: '9230745-363188259', title: 'Off- no practice', type: 'off', date: '2026-07-28', time: '', allDay: true, canceled: false, venue: '', arrival: '' },
  tryout:    { uid: '9230745-371669189', title: 'Tryouts 2026-27 Tentative', type: 'tryout', date: '2026-09-16', time: '18:00', allDay: false, canceled: false, venue: 'Vollmer', arrival: '' },
};

const SCHED = {
  northOakland:   { id: 's1', opponent: 'North Oakland', date: '2026-08-22', time: '08:00', tournament: 'Gatorade Invitational', field: '8N', format: '9v9', squadIds: ['p1'] },
  nationalsMacomb:{ id: 's2', opponent: 'Nationals Macomb', date: '2026-08-22', time: '13:20', tournament: 'Gatorade Invitational', field: '8N', format: '9v9', squadIds: [] },
};

const TODAY = '2026-08-19';

test('coach games replace an all-day block on the same day', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.gatorade22, TS.ecslEnd],
    schedule: [SCHED.northOakland, SCHED.nationalsMacomb],
    games: [], today: TODAY,
  });
  const day = m.days.get('2026-08-22');
  assert.equal(day.length, 2, 'two games, no separate tournament bars');
  assert.deepEqual(day.map(e => e.kind), ['game_scheduled', 'game_scheduled']);
  assert.deepEqual(day.map(e => e.opponent), ['North Oakland', 'Nationals Macomb']);
  assert.equal(day[0].time, '08:00', 'ordered by time');
});

test('an all-day block with no coach games stands alone', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.gatorade23], schedule: [], games: [], today: TODAY,
  });
  const day = m.days.get('2026-08-23');
  assert.equal(day.length, 1);
  assert.equal(day[0].kind, 'tournament_block');
  assert.equal(day[0].arrival, '1:00 PM');
});

test('a tournament block and a practice coexist on one day', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.terror23, TS.practice23], schedule: [], games: [], today: TODAY,
  });
  const kinds = m.days.get('2026-10-23').map(e => e.kind).sort();
  assert.deepEqual(kinds, ['practice', 'tournament_block']);
});

test('a finished game supersedes its scheduled counterpart', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [],
    schedule: [{ id: 's9', opponent: 'Caboto Strikers', date: '2026-07-12', time: '11:00' }],
    games: [{ id: 'g9', status: 'finished', opponent: 'Caboto Strikers', date: '2026-07-12', ourScore: 6, oppScore: 0 }],
    today: TODAY,
  });
  const day = m.days.get('2026-07-12');
  assert.equal(day.length, 1, 'not doubled');
  assert.equal(day[0].kind, 'game_finished');
  assert.equal(day[0].result, 'won');
  assert.equal(day[0].gameId, 'g9');
});

test('results are classified win, loss and draw', () => {
  const mk = (ourScore, oppScore) => buildCalendarModel({
    teamsnapEvents: [], schedule: [],
    games: [{ id: 'g', status: 'finished', opponent: 'X', date: '2026-05-01', ourScore, oppScore }],
    today: TODAY,
  }).days.get('2026-05-01')[0].result;
  assert.equal(mk(3, 1), 'won');
  assert.equal(mk(1, 2), 'lost');
  assert.equal(mk(2, 2), 'drawn');
});

test('an unfinished game is not treated as a result', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [], schedule: [],
    games: [{ id: 'g', status: 'live', opponent: 'X', date: '2026-08-19', ourScore: 1, oppScore: 0 }],
    today: TODAY,
  });
  assert.equal(m.days.has('2026-08-19'), false, 'live games are not calendar entries');
});

test('a cancelled practice keeps its kind and is flagged', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.cancelled], schedule: [], games: [], today: TODAY,
  });
  const e = m.days.get('2026-06-23')[0];
  assert.equal(e.kind, 'practice');
  assert.equal(e.cancelled, true);
  assert.equal(/cancell?ed/i.test(e.title), false, 'title not doubly marked');
});

test('an off day is an entry that renders no bar', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.offDay], schedule: [], games: [], today: TODAY,
  });
  assert.equal(m.days.get('2026-07-28')[0].kind, 'off');
});

test('a tryout keeps its own kind', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.tryout], schedule: [], games: [], today: TODAY,
  });
  assert.equal(m.days.get('2026-09-16')[0].kind, 'tryout');
});

test('days with no events are absent from the map', () => {
  const m = buildCalendarModel({ teamsnapEvents: [], schedule: [], games: [], today: TODAY });
  assert.equal(m.days.size, 0);
});

test('events missing from the feed are dropped', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [{ ...TS.tryout, missingFromFeed: true }],
    schedule: [], games: [], today: TODAY,
  });
  assert.equal(m.days.size, 0);
});

test('a timed teamsnap fixture becomes an unscheduled game', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [{ uid: 'u1', title: 'Scrimmage vs Caboto', type: 'game', date: '2026-05-01', time: '19:15', allDay: false, canceled: false, venue: 'Holy Names', arrival: '' }],
    schedule: [], games: [], today: TODAY,
  });
  const e = m.days.get('2026-05-01')[0];
  assert.equal(e.kind, 'game_unscheduled', 'not the raw teamsnap type');
  assert.equal(e.time, '19:15');
});

test('no entry keeps a raw teamsnap type as its kind', () => {
  const KINDS = new Set(['game_finished', 'game_scheduled', 'game_unscheduled',
    'practice', 'tryout', 'team_event', 'tournament_block', 'off']);
  const m = buildCalendarModel({
    teamsnapEvents: [TS.gatorade23, TS.practice23, TS.tryout, TS.offDay,
      { uid: 'u2', title: 'Scrimmage vs WSC', type: 'game', date: '2026-06-03', time: '19:00', allDay: false, canceled: false, venue: '', arrival: '' }],
    schedule: [], games: [], today: TODAY,
  });
  for (const e of [...m.days.values()].flat()) assert.ok(KINDS.has(e.kind), `bad kind: ${e.kind}`);
});

test('byMonth indexes the days it holds', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.gatorade22, TS.gatorade23], schedule: [], games: [], today: TODAY,
  });
  assert.deepEqual(m.byMonth.get('2026-08'), ['2026-08-22', '2026-08-23']);
});

test('entry keys are unique and stable', () => {
  const m = buildCalendarModel({
    teamsnapEvents: [TS.gatorade22, TS.ecslEnd],
    schedule: [SCHED.northOakland, SCHED.nationalsMacomb], games: [], today: TODAY,
  });
  const keys = [...m.days.values()].flat().map(e => e.key);
  assert.equal(new Set(keys).size, keys.length);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/calendarModel.test.mjs`
Expected: FAIL — cannot find module `../js/calendarModel.mjs`.

- [ ] **Step 3: Write the implementation**

Create `js/calendarModel.mjs`:

```javascript
/**
 * Merge the three schedule sources into one day-keyed model.
 *
 * Pure by design — no React, no window, no clock. `today` is a parameter so the
 * whole thing is unit-testable, which matters because this is where the bugs
 * live: three sources describe overlapping reality and only some of the overlap
 * is duplication.
 */

export const ENTRY_COLORS = {
  game_scheduled: '#378ADD',
  game_unscheduled: '#378ADD',
  won: '#639922',
  lost: '#E24B4A',
  drawn: '#5F5E5A',
  practice: '#7F77DD',
  tryout: '#EF9F27',
  team_event: '#B4B2A9',
  tournament_block: '#378ADD',
  off: null, // an off day means nothing is happening; a bar would say the opposite
};

/** Cancelled bars keep their kind's hue at the 600 stop with an X over it. */
export const CANCELLED_FILL = {
  game_scheduled: '#185FA5',
  game_unscheduled: '#185FA5',
  tournament_block: '#185FA5',
  practice: '#534AB7',
  tryout: '#854F0B',
  team_event: '#B4B2A9',
};

/** Light strokes read on the dark fills; grey has no dark end, so it needs black. */
export const CANCELLED_STROKE = {
  game_scheduled: '#E6F1FB',
  game_unscheduled: '#E6F1FB',
  tournament_block: '#E6F1FB',
  practice: '#EEEDFE',
  tryout: '#FAEEDA',
  team_event: '#2C2C2A',
};

const COMPETITION = /\b(tournament|festival|invitational|classic|cup)\b/i;
const norm = (s) => (s || '').trim().toLowerCase();
const dayKey = (d) => (d || '').slice(0, 10);
const monthKey = (d) => (d || '').slice(0, 7);

/** Sort within a day: timed entries by clock, all-day entries last. */
function byTime(a, b) {
  if (!a.time && b.time) return 1;
  if (a.time && !b.time) return -1;
  if (a.time !== b.time) return a.time.localeCompare(b.time);
  return (a.title || '').localeCompare(b.title || '');
}

export function buildCalendarModel({ teamsnapEvents = [], schedule = [], games = [], today }) {
  const days = new Map();
  const push = (entry) => {
    const k = dayKey(entry.date);
    if (!k) return;
    if (!days.has(k)) days.set(k, []);
    days.get(k).push(entry);
  };

  // 1. Finished games win outright: a played game is the most authoritative
  //    record of a day, so it shadows whatever was scheduled for it.
  const finishedKeys = new Set();
  for (const g of games) {
    if (g.status !== 'finished') continue;
    const k = dayKey(g.date);
    finishedKeys.add(`${k}|${norm(g.opponent)}`);
    const our = Number(g.ourScore) || 0;
    const opp = Number(g.oppScore) || 0;
    push({
      key: `game:${g.id}`,
      kind: 'game_finished',
      date: k,
      time: g.time || '',
      title: `vs ${g.opponent}`,
      opponent: g.opponent || '',
      tournament: g.tournament || '',
      field: g.field || '',
      location: g.location || '',
      venue: '', arrival: '', allDay: false, cancelled: false,
      result: our > opp ? 'won' : our < opp ? 'lost' : 'drawn',
      ourScore: our, oppScore: opp,
      gameId: g.id, scheduleId: null, teamsnapUid: g.teamsnapUid || null,
      raw: g,
    });
  }

  // 2. Scheduled games, unless the same fixture already finished.
  const scheduledDays = new Set();
  for (const s of schedule) {
    const k = dayKey(s.date);
    if (finishedKeys.has(`${k}|${norm(s.opponent)}`)) continue;
    scheduledDays.add(k);
    push({
      key: `sched:${s.id}`,
      kind: 'game_scheduled',
      date: k,
      time: s.time || '',
      title: `vs ${s.opponent}`,
      opponent: s.opponent || '',
      tournament: s.tournament || '',
      field: s.field || '',
      location: s.location || '',
      venue: '', arrival: '', allDay: false,
      cancelled: !!s.cancelled,
      result: null, ourScore: null, oppScore: null,
      gameId: null, scheduleId: s.id, teamsnapUid: s.teamsnapUid || null,
      raw: s,
    });
  }

  // 3. TeamSnap events. An all-day COMPETITION block on a day that already has
  //    coach games is not an event in its own right — it is those games'
  //    context, so it contributes a title and no bar. Aug 22 2026 is the real
  //    case: one Gatorade block (plus an ECSL one) over two entered games.
  for (const ev of teamsnapEvents) {
    if (ev.missingFromFeed) continue;
    const k = dayKey(ev.date);
    const isBlock = ev.type === 'game' && (ev.allDay || COMPETITION.test(ev.title || ''));

    if (isBlock && scheduledDays.has(k)) {
      for (const e of days.get(k) || []) {
        if (e.kind === 'game_scheduled' && !e.tournament) e.tournament = ev.title;
        if (e.kind === 'game_scheduled' && !e.teamsnapUid) e.teamsnapUid = ev.uid;
      }
      continue;
    }

    // TeamSnap's own type vocabulary is not ours. A timed `game` it knows about
    // but the coach has not set up (the 7 scrimmages in the current feed) is
    // `game_unscheduled`: it draws as a game and offers "schedule this", but it
    // has no squad, colours or START because none of that exists yet.
    const kind = isBlock
      ? 'tournament_block'
      : ev.type === 'game'
        ? 'game_unscheduled'
        : (ev.type || 'team_event');

    push({
      key: `ts:${ev.uid}`,
      kind,
      date: k,
      time: ev.time || '',
      title: ev.title || '',
      opponent: '',
      tournament: isBlock ? (ev.title || '') : '',
      field: '',
      location: ev.address || '',
      venue: ev.venue || '',
      // Midnight is TeamSnap's placeholder, not a real arrival time.
      arrival: /^12:00\s*AM$/i.test(ev.arrival || '') ? '' : (ev.arrival || ''),
      allDay: !!ev.allDay,
      cancelled: !!ev.canceled,
      result: null, ourScore: null, oppScore: null,
      gameId: null, scheduleId: null, teamsnapUid: ev.uid,
      raw: ev,
    });
  }

  for (const list of days.values()) list.sort(byTime);

  const byMonth = new Map();
  for (const k of [...days.keys()].sort()) {
    const m = monthKey(k);
    if (!byMonth.has(m)) byMonth.set(m, []);
    byMonth.get(m).push(k);
  }

  return { days, byMonth, today };
}

/** The bar colour for an entry, or null when it should not draw one. */
export function entryColor(entry) {
  if (entry.kind === 'game_finished') return ENTRY_COLORS[entry.result] || null;
  return ENTRY_COLORS[entry.kind] ?? ENTRY_COLORS.team_event;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/calendarModel.test.mjs`
Expected: PASS, 15 tests.

- [ ] **Step 5: Add the test script**

In `package.json`, add to `scripts`:

```json
"test": "node --test \"test/**/*.test.mjs\""
```

- [ ] **Step 6: Run the suite through the script**

Run: `npm test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add js/calendarModel.mjs test/calendarModel.test.mjs package.json
git commit -m "feat: merge schedule sources into a calendar model"
```

---

### Task 2: Verify the merge against the whole live feed

The unit tests use eight hand-picked events. This proves the merge survives all 116 and that no day silently doubles.

**Files:**
- Create: `scripts/calendar_model_check.mjs`
- Test: run against a saved copy of the feed

**Interfaces:**
- Consumes: `buildCalendarModel`, `entryColor` from Task 1.
- Produces: a CLI check; no app code depends on it.

- [ ] **Step 1: Confirm the feed snapshot is present**

A snapshot is already staged at `test/fixtures/teamsnap-sample.ics` (116 events).

```bash
grep -c 'BEGIN:VEVENT' test/fixtures/teamsnap-sample.ics
```
Expected: `116`.

It is **gitignored on purpose** (`.gitignore` line for `test/fixtures/*.ics`). The
file is not a secret — the feed URL is the credential and lives in a Worker
secret — but it records where the kids will be and when, which is the same reason
`parent_contacts.local.json` is ignored. Do not commit it and do not hardcode the
feed URL anywhere. If the snapshot is missing, ask the coach to re-fetch it
locally rather than putting the URL in a file.

- [ ] **Step 2: Write the check script**

Create `scripts/calendar_model_check.mjs`:

```javascript
/**
 * Run the calendar merge over a real .ics snapshot and print what it produced.
 * The unit tests cover chosen cases; this catches whole-feed surprises — a day
 * that doubles, an unclassified kind, a bar with no colour.
 *
 * Usage: node scripts/calendar_model_check.mjs test/fixtures/teamsnap-sample.ics
 */
import fs from 'node:fs';
import { buildCalendarModel, entryColor } from '../js/calendarModel.mjs';

const TEAM = /^\s*Stompers\s*\d*\s*boys\s*-\s*/i;
const CANCEL = /^\s*\[CANCELED\]\s*/i;

function classify(title) {
  const t = title.toLowerCase();
  if (/\boff\b\s*[-–]?\s*no\s+practice/.test(t) || /\bno\s+practice\b/.test(t)) return 'off';
  if (t.includes('tryout')) return 'tryout';
  if (t.includes('practice')) return 'practice';
  if (/\b(tournament|festival|invitational|classic|cup|scrimmage|game)\b/.test(t)) {
    if (t.includes('parents vs players') || t.includes('pizza')) return 'team_event';
    return 'game';
  }
  return 'team_event';
}

const raw = fs.readFileSync(process.argv[2], 'utf8').replace(/\r?\n[ \t]/g, '');
const events = [];
for (const block of raw.match(/BEGIN:VEVENT[\s\S]*?END:VEVENT/g) || []) {
  const get = (k) => (block.match(new RegExp('^' + k + '(?:;[^:]*)?:(.*)$', 'm')) || [])[1]?.trim() || '';
  const summary = get('SUMMARY');
  const canceled = CANCEL.test(summary);
  let title = summary.replace(CANCEL, '').replace(TEAM, '').trim();
  if (canceled) title = title.replace(/[\s-]*cancell?ed\s*$/i, '').trim();
  const ds = block.match(/^DTSTART(?:;TZID=([^:]+))?:(\S+)/m);
  if (!ds) continue;
  const stamp = ds[2];
  const allDay = stamp.endsWith('T000000') && !ds[1];
  const desc = get('DESCRIPTION');
  events.push({
    uid: get('UID'), title, type: classify(title), canceled,
    date: `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)}`,
    time: allDay ? '' : `${stamp.slice(9, 11)}:${stamp.slice(11, 13)}`,
    allDay,
    venue: (desc.match(/Location:\s*(.*?)(?:\\n|$)/) || [])[1]?.trim() || '',
    address: get('LOCATION').replace(/\\n/g, ', ').replace(/\\,/g, ','),
    arrival: (desc.match(/Arrival Time:\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)/i) || [])[1] || '',
  });
}

const model = buildCalendarModel({ teamsnapEvents: events, schedule: [], games: [], today: '2026-08-19' });
const all = [...model.days.values()].flat();
const byKind = {};
for (const e of all) byKind[e.kind] = (byKind[e.kind] || 0) + 1;

console.log(`parsed ${events.length} events -> ${all.length} entries over ${model.days.size} days`);
console.log('byKind:', byKind);

const dupKeys = all.length - new Set(all.map((e) => e.key)).size;
const noColour = all.filter((e) => e.kind !== 'off' && !entryColor(e));
const multi = [...model.days.entries()].filter(([, v]) => v.length > 1);

console.log(`duplicate keys: ${dupKeys}`);
console.log(`entries with no colour (excluding off): ${noColour.length}`);
console.log(`days with more than one entry: ${multi.length}`);
for (const [d, v] of multi) console.log(`  ${d}: ${v.map((e) => e.kind).join(', ')}`);

const VALID = new Set(['game_finished', 'game_scheduled', 'game_unscheduled',
  'practice', 'tryout', 'team_event', 'tournament_block', 'off']);
const badKinds = all.filter((e) => !VALID.has(e.kind));
console.log(`entries with an invalid kind: ${badKinds.length}`);
for (const e of badKinds.slice(0, 5)) console.log(`  ${e.kind}: ${e.title}`);

if (dupKeys || noColour.length || badKinds.length || events.length === 0) {
  console.error('FAIL');
  process.exit(1);
}
console.log('OK');
```

- [ ] **Step 3: Run it**

Run: `node scripts/calendar_model_check.mjs test/fixtures/teamsnap-sample.ics`
Expected: `OK`, 116 entries over 113 days, `duplicate keys: 0`, `entries with no colour: 0`, `entries with an invalid kind: 0`, and 3 multi-entry days (2026-07-23, 2026-08-22, 2026-10-23). `byKind` should read `practice: 66, tournament_block: 27, team_event: 9, game_unscheduled: 7, off: 4, tryout: 3` — note **no bare `game`**, which was a real bug caught by running this check while the plan was being written.

- [ ] **Step 4: Commit the script only**

The fixture stays untracked (see Step 1), so commit just the checker and the
`.gitignore` rule that keeps snapshots out of history.

```bash
git add scripts/calendar_model_check.mjs .gitignore
git commit -m "test: check the calendar merge against the whole feed"
```

---

### Task 3: Read teamsnapEvents into app state

The merge needs the synced events. This adds the Firestore read only — no UI yet.

**Files:**
- Modify: `soccer_team_app.jsx` — add state + loader near the existing team-doc listener (around line 1305)
- Modify: `_sync_html.py` — add the production listener

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `teamsnapEvents` array in `App` state, shaped as the docs written by `worker/src/teamsnap.ts` (`uid, title, type, canceled, date, time, allDay, endDate, venue, address, arrival, tz, modified, missingFromFeed`).

**`teamsnapSyncedAt` was NOT implemented, deliberately.** The only candidate
field is `modified`, which the Worker fills from the feed's `LAST-MODIFIED` —
i.e. when the *coach last edited the event in TeamSnap*, not when our cron last
ran. `max(modified)` would therefore read as days or weeks old seconds after a
successful sync, and would never advance while the coach isn't editing TeamSnap:
a "Synced 9d ago" line that actively misinforms. Nothing in Firestore currently
records the cron's own run time, and inventing a write to store one was out of
scope for this task. Task 5 should pass `syncedAt={null}` and leave the line
hidden (its interface already allows null) until a sync-time field is added to
the Worker as its own change.

`teamsnapEvents` is a Firestore **subcollection**, so unlike `schedule` it needs its own listener. Add a NEW `useEffect` — do not modify the existing team-doc effect, which `_sync_html.py` replaces by exact text.

- [ ] **Step 1: Add the state and a local-dev stub**

In `App`, next to the other `useState` declarations (near line 1080 `const [schedule, setSchedule] = useState([]);`), add:

```javascript
  // TeamSnap-synced calendar events (practices, tournaments, team events).
  // Written every 15 min by the Worker cron; read-only here. Local dev has no
  // Firestore, so this stays empty and the calendar simply shows fewer kinds.
  const [teamsnapEvents, setTeamsnapEvents] = useState([]);
```

- [ ] **Step 2: Add a listener that is inert in local dev**

Immediately AFTER the existing `useEffect` that loads the team live input (search for `STORAGE_KEYS.TEAM_LIVE_INPUT`), add a new self-contained effect:

```javascript
  // Synced TeamSnap events. Separate effect (and a subcollection, not a team-doc
  // field) so it can be added without touching the loaders _sync_html.py
  // rewrites by exact source text.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('teams').doc('main').collection('teamsnapEvents')
      .onSnapshot(
        (snap) => {
          const list = [];
          snap.forEach((d) => list.push({ uid: d.id, ...d.data() }));
          setTeamsnapEvents(list);
        },
        (err) => console.error('teamsnapEvents listen failed', err)
      );
    return () => unsub();
  }, []);
```

- [ ] **Step 3: Verify the JSX still parses and the sync script still applies**

```bash
node --input-type=module -e "import('node:fs').then(fs=>{const s=fs.readFileSync('soccer_team_app.jsx','utf8');if(!s.includes('teamsnapEvents'))throw new Error('missing');console.log('present')})"
python3 _sync_html.py
```
Expected: `present`, and `_sync_html.py` completes without `SystemExit`.

- [ ] **Step 4: Confirm the production HTML carries the listener**

```bash
grep -c "teamsnapEvents" soccer_team_app_standalone_backup.html
```
Expected: at least 1. The effect is plain code the script copies through untouched — no `_sync_html.py` change is needed, which is why it was written as its own effect.

- [ ] **Step 5: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: read synced teamsnap events into app state"
```

---

### Task 4: Extract the game form from ScheduleView

The calendar's edit sheet must be the *same* form, not a copy — reimplementing fifteen inputs is how parity gets silently lost. Extraction is mandatory per the spec.

**Files:**
- Modify: `soccer_team_app.jsx` — extract from `ScheduleView` (starts line 13074) into a new `GameForm` component defined immediately above it

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  ```
  <GameForm
    initial={{ opponent, date, time, tournament, location, field,
               isHome, format, halfLengthMin, homeColor, awayColor, squadIds }}
    opponentSuggestions={string[]}
    editing={boolean}
    onSubmit={(values) => void}
    onCancel={() => void}
    onEditSquad={() => void}
    showToast={fn}
  />
  ```
  `values` carries exactly the keys of `initial`. `GameForm` owns all its input state; callers own persistence.

  **`onEditSquad` receives the LIVE values, not `initial`.** `GameForm` calls
  `onEditSquad(values())` so the caller can persist what the coach has typed
  *before* navigating away to the squad picker. Reading `initial` instead would
  silently discard every edit made since the last save — the squad detour's whole
  job is to not lose them.

  **Re-seeding must key on a value signature, not object identity.** In
  production `_sync_html.py` wires ONE `teamDoc().onSnapshot` that sets roster,
  weights *and* schedule, so any roster or weights write hands `setSchedule` a
  brand-new array. A re-seed `useEffect`/`useMemo` keyed on the array or on a
  freshly-built object therefore refires on unrelated writes and wipes the form
  while the coach is typing — in production only. Key on a JSON value signature
  plus the editing id plus a nonce that a reset bumps (see `formSig` /
  `formNonce` in `ScheduleView`).

- [ ] **Step 1: Create GameForm with the existing markup**

Define `function GameForm({ initial, opponentSuggestions = [], editing, onSubmit, onCancel, onEditSquad, showToast })` directly above `function ScheduleView`. Move into it, unchanged: the `useState` declarations for `opponent`, `date`, `time`, `tournament`, `location`, `field`, `isHome`, `format`, `halfLengthMin`, `halfLenTouched`, `homeColor`, `awayColor`, `squadIds`, `showSetup`; the helpers `pickFormat`, `setHalfLenManually`, `isLightColor`; and the entire "Add form" JSX block (`{/* Add form */}` through the submit button, around lines 13350–13600). Seed each `useState` from `initial`, and re-seed with a `useEffect` on `initial` so reopening the sheet for a different day updates the fields.

Replace the submit handler's body with `onSubmit({ opponent: opponent.trim(), date, time, tournament: tournament.trim(), location: location.trim(), field: field.trim(), isHome, format, halfLengthMin, homeColor, awayColor, squadIds })`, keeping the existing `disabled={!opponent.trim() || !date}` guard.

- [ ] **Step 2: Mount GameForm inside ScheduleView**

Replace the extracted block in `ScheduleView` with:

```jsx
      <GameForm
        initial={formInitial}
        opponentSuggestions={opponentSuggestions}
        editing={!!editingId}
        onSubmit={handleFormSubmit}
        onCancel={resetForm}
        onEditSquad={handleEditSquad}
        showToast={showToast}
      />
```

Keep `ScheduleView`'s `editingId`, `handleEdit`, `handleDelete`, `handleToggleCancel`, `renderRow`, the opponent manager and the past-games section as they are. `handleFormSubmit` contains the add-vs-edit branch that `handleAdd` had. `handleEditSquad(values)` persists the live form values and *then* navigates, which is why `GameForm` hands them out rather than the caller reading stale state.

`formInitial` is memoised on `[editingId, formSig, formNonce]` — never on the `schedule` array itself, for the production re-seed reason in the Interfaces block above.

- [ ] **Step 3: Verify no behaviour changed**

```bash
python3 _sync_html.py && node --check <(npx --yes @babel/cli --version >/dev/null 2>&1; echo "")
grep -c "GameForm" soccer_team_app.jsx
```
Expected: sync succeeds; `GameForm` appears at least 3 times (definition, mount, and closing usage).

Then load the app and confirm by hand: add a game, edit it, cancel it, delete it, pick a squad, and check the `READY` badge still appears when squad + home/away + half length + both colours are set.

- [ ] **Step 4: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "refactor: extract the game form out of ScheduleView"
```

---

### Task 5: The CalendarView component

**Files:**
- Modify: `soccer_team_app.jsx` — add `CalendarMonthGrid`, `CalendarDayRows` and `CalendarView` above `ScheduleView`

**Interfaces:**
- Consumes: `buildCalendarModel`, `entryColor`, `CANCELLED_FILL`, `CANCELLED_STROKE` (inlined in Task 6); `GameForm` from Task 4.
- Produces:
  ```
  <CalendarView
    model={CalendarModel} canEdit={boolean} today={'YYYY-MM-DD'}
    syncedAt={number|null} roster={Player[]} opponentSuggestions={string[]}
    onOpenGame={(gameId) => void}
    onStartGame={(scheduleItem) => void}
    onSaveGame={(values, scheduleId|null) => void}
    onDeleteGame={(scheduleId) => void}
    onToggleCancel={(scheduleId) => void}
    onEditSquad={({ id, opponent, squadIds }) => void}
    onManageOpponents={() => void}
  />
  ```

- [ ] **Step 1: Build the month grid**

Add `CalendarMonthGrid({ model, month, selected, today, onSelect })`. It renders a 7-column grid with weekday headers, leading blanks for the first-of-month weekday, and one cell per day. Each cell shows the day number, up to three bars, a count badge when `entries.length > 1`, a dashed border when any entry is cancelled, and a 2px solid border on `today`. Bars use `entryColor(entry)`; entries whose colour is `null` (off days) render no bar. Cancelled bars render an SVG `<rect>` filled with a per-kind `<pattern>` built from `CANCELLED_FILL` and `CANCELLED_STROKE` at 0.8 stroke width on a 6px tile.

Define the SVG `<defs>` once at the `CalendarView` level with ids namespaced per kind (e.g. `calx-practice`), so the patterns are not redefined per cell.

- [ ] **Step 2: Build the day rows**

Add `CalendarDayRows({ entries, canEdit, ... })`. Each row reuses the existing chip vocabulary — `TournamentChip`, `FormatChip`, the field pill, the map link, the squad count, the `READY` badge — matching `ScheduleView.renderRow` (line 13244).

Per kind:
- `game_scheduled` — full row; when `canEdit`, show edit/cancel/delete actions and `START`.
- `game_finished` — result badge, score, and a tap target calling `onOpenGame(entry.gameId)`.
- `game_unscheduled` — a TeamSnap fixture with a real time that the coach has not set up. Shows title, time, venue; when `canEdit`, a "schedule this" action opening `GameForm` prefilled with the date, time and opponent parsed from the title (`Scrimmage vs Caboto` → `Caboto`). No `START` and no `READY` — there is no setup yet.
- `tournament_block` — title, venue, and `Arrive <arrival>` when present; when `canEdit`, an "add a game" action.
- `practice`, `tryout`, `team_event` — read-only detail (time, venue, map link). TeamSnap owns these; an edit here would revert within 15 minutes.
- `off` — a muted "No practice" line.

Cancelled rows keep the struck-through title plus a `Cancelled` badge.

- [ ] **Step 2b: Verify the row reuse by eye**

Open the app, select a day holding a scheduled game, and confirm the row is visually identical to the same game's row in the old `UPCOMING` list — same chips, same order, same badge.

- [ ] **Step 3: Assemble CalendarView**

Header: month name with ‹ › steppers, `Synced Nm ago` (from `syncedAt`, hidden when null), and when `canEdit`, `+ ADD GAME` and `🏷️ OPPONENTS` actions. Body: `CalendarMonthGrid`, then `CalendarDayRows` for the selected day, then a "Next up" agenda strip listing the next four entries at or after `today` **across month boundaries** (this is the capability a month grid loses versus the old flat list). Footer: the colour legend.

Tapping a day selects it. When `canEdit`, tapping an empty day, or `+ ADD GAME`, or a tournament block's add action, opens `GameForm` in a sheet with `initial` prefilled per the spec's interaction matrix: date always; tournament and location from a same-day `tournament_block`; time when the TeamSnap event carries one.

Two rules carried over from Task 4, both load-bearing: memoise the sheet's `initial` on a **value signature plus a nonce**, never on the `schedule` array (a roster or weights write refires the shared team-doc snapshot and would wipe the form mid-typing in production); and wire `onEditSquad` to a handler that persists the **values `GameForm` hands out**, not the `initial` it was opened with.

- [ ] **Step 4: Verify all five colours and both greys render**

Load the app on a month containing a game, a practice, a tryout, a team event and a cancelled event. Confirm each bar colour matches the Global Constraints table, that the two greys are visibly different, and that cancelled bars show visible X strokes — including a cancelled team event, whose strokes must be black rather than white.

- [ ] **Step 5: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: add the shared calendar view"
```

---

### Task 6: Inline the merge into the app

The app has no module loader, so `calendarModel.mjs` cannot be imported at runtime. Its source is inlined, with the module remaining the source of truth and the test target.

**Files:**
- Modify: `soccer_team_app.jsx` — insert the merge source above `CalendarView`
- Create: `scripts/inline_calendar_model.py`

**Interfaces:**
- Consumes: `js/calendarModel.mjs` from Task 1.
- Produces: `buildCalendarModel`, `entryColor`, `ENTRY_COLORS`, `CANCELLED_FILL`, `CANCELLED_STROKE` as top-level functions/consts in the JSX.

- [ ] **Step 1: Write the inliner**

Create `scripts/inline_calendar_model.py`: read `js/calendarModel.mjs`, strip `export ` prefixes, and replace the region between the markers `// ── BEGIN calendarModel (generated) ──` and `// ── END calendarModel (generated) ──` in `soccer_team_app.jsx`. Exit non-zero if the markers are absent, so drift is loud rather than silent.

- [ ] **Step 2: Add the markers and run it**

Insert the two marker comments above `CalendarView` in the JSX, then:

```bash
python3 scripts/inline_calendar_model.py
```
Expected: the generated block appears between the markers.

- [ ] **Step 3: Verify parity between module and inline copy**

```bash
npm test
python3 scripts/inline_calendar_model.py && git diff --exit-code soccer_team_app.jsx && echo "inline copy already current"
```
Expected: tests pass, and re-running the inliner produces no diff.

- [ ] **Step 4: Commit**

```bash
git add scripts/inline_calendar_model.py soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "build: inline the calendar model into the app bundle"
```

---

### Task 7: Wire the dugout — CALENDAR replaces SCHEDULE

**Files:**
- Modify: `soccer_team_app.jsx` — `HomeView` tile (line 2826), the `UPCOMING GAMES` block (line 2847), the `PAST GAMES` heading (line 2907), and `App`'s view routing (lines 2217–2243, 2515)

**Interfaces:**
- Consumes: `CalendarView` (Task 5), `buildCalendarModel` (Task 6), `teamsnapEvents` (Task 3).
- Produces: a `calendar` view route; `HomeView` no longer renders `UPCOMING GAMES` or `PAST GAMES`.

- [ ] **Step 1: Swap the tile**

Replace the `SCHEDULE` tile at line 2826 with a `CALENDAR` tile whose `sub` counts upcoming entries from the model rather than from `schedule` alone, and route it to `setView('calendar')`.

- [ ] **Step 2: Remove the two home sections**

Delete the `UPCOMING GAMES` block (the IIFE at line 2847) and the `PAST GAMES` section (line 2907) from `HomeView`. The calendar's grid and agenda strip replace them; per-game detail was always `AnalyticsPanel`.

- [ ] **Step 3: Mount the calendar view**

Add a `view === 'calendar'` branch in `App` that builds the model with `useMemo` and renders `CalendarView` with `canEdit`, wiring `onSaveGame`/`onDeleteGame`/`onToggleCancel` to `persistSchedule` (called, not edited — see Global Constraints), `onStartGame` to the existing `onStartScheduled` handler, `onOpenGame` to the `AnalyticsPanel` route, and `onEditSquad` to the existing squad-picker detour.

- [ ] **Step 4: Verify the parity checklist by hand**

Confirm each capability from the spec's parity table: add a game (via a day tap and via `+ ADD GAME`), edit, delete, cancel/uncancel, `START` on a match-day game, the `READY` badge, opponent autocomplete, the squad picker round-trip, and `🏷️ OPPONENTS` bulk rename. Confirm the agenda strip shows entries beyond the displayed month.

- [ ] **Step 5: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: put the calendar in the dugout"
```

---

### Task 8: Wire the parent view

**Files:**
- Modify: `soccer_team_app.jsx` — the parent `UPCOMING GAMES` block (line 15351)

**Interfaces:**
- Consumes: `CalendarView` (Task 5), `buildCalendarModel` (Task 6).
- Produces: nothing new.

- [ ] **Step 1: Replace the rows with the calendar**

Replace the `UPCOMING GAMES` IIFE at line 15351 with `CalendarView` at `canEdit={false}`. Leave the tile grid — including `PAST GAMES` — untouched: parents have no Film Room and no STATS, so that tile is their only route to a finished game.

- [ ] **Step 2: Verify the read-only surface**

On the deployed beta signed in as a parent, confirm: no `+ ADD GAME`, no `🏷️ OPPONENTS`, no edit/delete/cancel/`START` actions, that tapping a finished game opens the read-only game view a parent is permitted, and that practices appear.

- [ ] **Step 3: Commit**

```bash
git add soccer_team_app.jsx soccer_team_app_standalone_backup.html
git commit -m "feat: show the calendar to parents"
```

---

### Task 9: Deploy and verify on the beta

**Files:**
- Modify: none (build + deploy only)

- [ ] **Step 1: Build and deploy to beta**

Follow the project's deploy flow (`_sync_html.py`, then the Pages beta branch). Do not deploy to production.

- [ ] **Step 2: Verify as coach on the beta URL**

Localhost sign-in does not work for the coach, so this must happen on the beta Pages URL. Walk the parity checklist from Task 7 Step 4 again against real synced data, then confirm `Synced Nm ago` shows a plausible age and that 2026-08-22 renders the two Gatorade games as two bars — not four alongside the two all-day blocks.

- [ ] **Step 3: Verify as a parent on the beta URL**

Repeat Task 8 Step 2 against real data.

- [ ] **Step 4: Report**

Summarise what was verified and anything left open. Do not promote to production without the coach's say-so; the branch promotion chain is theirs to trigger.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| Three sources, one day | 1 |
| Entry kinds | 1 |
| Tournament merge rule | 1 (tests), 2 (whole-feed) |
| Identity and dedupe | 1 |
| Schedule already in Firestore | Global Constraints (no task needed) |
| Interaction with the TeamSnap sync | 1 (block contributes context, not a bar), 5 (practices read-only) |
| Visual language | 5 (grid + rows), Global Constraints (values) |
| Layout | 5 |
| Interaction matrix | 5 |
| Feature parity, OPPONENTS, + ADD GAME | 4, 5, 7 |
| Removals | 7 (dugout), 8 (parent view keeps its tile) |
| Structure | 1 (pure fn), 4 (form extraction), 6 (inlining) |
| Testing | 1, 2, and hand-verification in 5, 7, 8, 9 |
| Risks | Global Constraints (`_sync_html.py`), 4 (parity by extraction), 5 (`Synced Nm ago`) |

Two spec items are deliberately not tasks: the per-day attendance follow-up is out of scope, and the migration was removed when the spec was corrected.

**Placeholder scan:** no TBD/TODO; every code step carries real code. Task 2 Step 1 depends on the coach supplying the feed URL, which is stated explicitly rather than assumed, because the URL is a credential that must not be committed.

**Type consistency:** `buildCalendarModel` returns `{ days, byMonth, today }` in Task 1 and is consumed that way in Tasks 5, 7 and 8. `entryColor(entry)` is used in Tasks 2 and 5. `GameForm`'s `initial`/`onSubmit` shape in Task 4 matches the prefill in Task 5 Step 3. Entry kind strings are identical across Tasks 1, 2 and 5. `ENTRY_COLORS` keys cover every kind plus the three results, and `CANCELLED_FILL`/`CANCELLED_STROKE` cover every kind that draws a bar.

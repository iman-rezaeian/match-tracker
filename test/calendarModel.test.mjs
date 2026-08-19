import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCalendarModel, entryColor, ENTRY_COLORS } from '../js/calendarModel.mjs';

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

test('an off day gets no bar colour', () => {
  // Regression: ENTRY_COLORS.off IS null, so a `??` fallback treated it as
  // absent and returned team-event grey — drawing the one bar the spec forbids.
  assert.equal(entryColor({ kind: 'off' }), null);
});

test('every declared kind resolves to its own colour', () => {
  for (const kind of ['game_scheduled', 'game_unscheduled', 'practice', 'tryout',
                      'team_event', 'tournament_block']) {
    assert.equal(entryColor({ kind }), ENTRY_COLORS[kind], `${kind} colour`);
  }
  assert.equal(entryColor({ kind: 'game_finished', result: 'won' }), ENTRY_COLORS.won);
  assert.equal(entryColor({ kind: 'game_finished', result: 'lost' }), ENTRY_COLORS.lost);
  assert.equal(entryColor({ kind: 'game_finished', result: 'drawn' }), ENTRY_COLORS.drawn);
});

test('an unknown kind still gets a visible fallback colour', () => {
  assert.equal(entryColor({ kind: 'something_new' }), ENTRY_COLORS.team_event);
});

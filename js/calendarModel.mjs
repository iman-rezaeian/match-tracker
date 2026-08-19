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

/**
 * The bar colour for an entry, or null when it should not draw one.
 *
 * `off` deliberately returns null: an off day means nothing is happening, so a
 * bar would say the opposite. Note the lookup cannot use `??` to fall back —
 * `ENTRY_COLORS.off` IS null, and `??` treats that as absent and would hand back
 * team-event grey, drawing exactly the bar the spec forbids. Use `in` so a
 * declared null stays null and only genuinely unknown kinds fall back.
 */
export function entryColor(entry) {
  if (entry.kind === 'game_finished') return ENTRY_COLORS[entry.result] || null;
  if (entry.kind in ENTRY_COLORS) return ENTRY_COLORS[entry.kind];
  return ENTRY_COLORS.team_event;
}

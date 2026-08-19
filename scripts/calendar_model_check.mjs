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
// `off` is excluded because it SHOULD have no colour. Assert that separately,
// or the exclusion hides a real bug: entryColor once fell through to grey for
// off days, and this filter would never have noticed.
const noColour = all.filter((e) => e.kind !== 'off' && !entryColor(e));
const offWithColour = all.filter((e) => e.kind === 'off' && entryColor(e) !== null);
const multi = [...model.days.entries()].filter(([, v]) => v.length > 1);

console.log(`duplicate keys: ${dupKeys}`);
console.log(`entries with no colour (excluding off): ${noColour.length}`);
console.log(`off days wrongly given a colour: ${offWithColour.length}`);
console.log(`days with more than one entry: ${multi.length}`);
for (const [d, v] of multi) console.log(`  ${d}: ${v.map((e) => e.kind).join(', ')}`);

const VALID = new Set(['game_finished', 'game_scheduled', 'game_unscheduled',
  'practice', 'tryout', 'team_event', 'tournament_block', 'off']);
const badKinds = all.filter((e) => !VALID.has(e.kind));
console.log(`entries with an invalid kind: ${badKinds.length}`);
for (const e of badKinds.slice(0, 5)) console.log(`  ${e.kind}: ${e.title}`);

if (dupKeys || noColour.length || offWithColour.length || badKinds.length || events.length === 0) {
  console.error('FAIL');
  process.exit(1);
}
console.log('OK');

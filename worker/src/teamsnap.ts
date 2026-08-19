/**
 * TeamSnap iCal → Firestore schedule sync (cron).
 *
 * WHY A CRON AND NOT A CLIENT FETCH: the feed is cross-origin (no CORS headers)
 * so a browser cannot read it, and a phone-triggered sync only runs while the
 * app is open. Polling server-side means every device and every parent sees the
 * same schedule without anyone opening the app.
 *
 * FRESHNESS CEILING — do not expect realtime. Two delays stack and only the
 * first is ours:
 *   1. this cron's interval (15 min, per the coach's request), and
 *   2. TeamSnap's own CDN cache, which sends `cache-control: max-age=14400`
 *      — FOUR HOURS — and serves `cf-cache-status: HIT` in between. Their
 *      `X-PUBLISHED-TTL` advertises PT1H.
 * So a 15-minute cron re-reads identical bytes ~16 times per cache window. It
 * is kept because the coach asked for it and because it costs almost nothing
 * once conditional requests are used: we send If-None-Match and a 304 exits
 * before any parsing or Firestore write. If TeamSnap ever shortens that cache,
 * the tighter cadence starts paying off on its own.
 *
 * SECRET: TEAMSNAP_ICS_URL. That URL *is* the credential — anyone holding it can
 * read the team's whole schedule (dates, times, venues, kids' locations). Worker
 * secret only, never `[vars]` (which is committed and injected into the bundle —
 * see the COACH_PASS note in wrangler.toml).
 */

// ─── iCal parsing ─────────────────────────────────────────────────────────────

/** iCal folds long lines with a leading space/tab on the continuation. */
const unfold = (raw: string) => raw.replace(/\r?\n[ \t]/g, '');

const TEAM_PREFIX = /^\s*Stompers\s*\d*\s*boys\s*-\s*/i;
const CANCEL_PREFIX = /^\s*\[CANCELED\]\s*/i;

function prop(block: string, key: string): string | null {
  const m = block.match(new RegExp('^' + key + '(?:;[^:]*)?:(.*)$', 'm'));
  return m ? m[1].trim() : null;
}

/**
 * Event type from the title. The feed carries NO type field — no CATEGORIES,
 * nothing — so free text in SUMMARY is the only signal. Measured against the
 * real 116-event feed: 66 practice, 34 game, 9 team_event, 4 off, 3 tryout.
 *
 * ORDER IS LOAD-BEARING:
 *   - `off` first: "Off- no practice" is the ABSENCE of a practice and a naive
 *     substring match on "practice" would claim it.
 *   - `tryout` before practice: tryouts happen once a year and are not training.
 *   - `practice` before the game keywords, so "Full Turf Practice fun Scrimmage
 *     vs 2014 boys" stays a practice. Genuinely ambiguous; this ordering decides
 *     it and the coach can override per event.
 */
export function classify(title: string): string {
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

export interface TsEvent {
  uid: string;
  title: string;
  type: string;
  canceled: boolean;
  date: string;      // YYYY-MM-DD, local wall date
  time: string;      // HH:MM, '' for all-day
  allDay: boolean;
  endDate: string;
  venue: string;     // human name, from DESCRIPTION ("Location: ...")
  address: string;   // street address, from LOCATION
  arrival: string;   // e.g. "8:00 AM", only in DESCRIPTION prose
  tz: string;
  modified: string;
}

export function parseIcs(raw: string): TsEvent[] {
  const text = unfold(raw);
  const out: TsEvent[] = [];
  const blocks = text.match(/BEGIN:VEVENT[\s\S]*?END:VEVENT/g) || [];

  for (const block of blocks) {
    const summary = prop(block, 'SUMMARY') || '';
    const canceled = CANCEL_PREFIX.test(summary);
    let title = summary.replace(CANCEL_PREFIX, '').replace(TEAM_PREFIX, '').trim();
    // Some titles ALSO end in "Cancelled" on top of the [CANCELED] prefix, which
    // would render the cancellation three ways over (struck title + badge + word).
    if (canceled) title = title.replace(/[\s-]*cancell?ed\s*$/i, '').trim();

    const ds = block.match(/^DTSTART(?:;TZID=([^:]+))?:(\S+)/m);
    if (!ds) continue;
    const tz = ds[1] || '';
    const stamp = ds[2];
    const de = block.match(/^DTEND(?:;TZID=[^:]+)?:(\S+)/m);

    // A midnight-to-midnight block with no TZID is how TeamSnap represents a
    // tournament DAY — not a game with a real kickoff. Outdoor festivals look
    // like this; indoor tournaments carry a real 0800 start instead.
    const allDay = stamp.endsWith('T000000') && !tz;

    const desc = prop(block, 'DESCRIPTION') || '';
    const arrival = desc.match(/Arrival Time:\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)/i);
    const venue = desc.match(/Location:\s*(.*?)(?:\\n|$)/);

    out.push({
      uid: prop(block, 'UID') || stamp,
      title,
      type: classify(title),
      canceled,
      date: `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)}`,
      time: allDay ? '' : `${stamp.slice(9, 11)}:${stamp.slice(11, 13)}`,
      allDay,
      endDate: de ? `${de[1].slice(0, 4)}-${de[1].slice(4, 6)}-${de[1].slice(6, 8)}` : '',
      venue: venue ? venue[1].trim() : '',
      address: (prop(block, 'LOCATION') || '').replace(/\\n/g, ', ').replace(/\\,/g, ','),
      arrival: arrival ? arrival[1] : '',
      // The 19 all-day blocks carry no TZID. Pin them to the team's zone rather
      // than leaving them floating, or they drift by the UTC offset.
      tz: tz || 'America/Toronto',
      modified: prop(block, 'LAST-MODIFIED') || '',
    });
  }
  return out;
}

// ─── Google service-account auth (for Firestore REST) ─────────────────────────
// The cron has no user to borrow a token from, so it signs a JWT with the
// service account key and exchanges it for an access token.

const enc = new TextEncoder();
const b64url = (b: ArrayBuffer | Uint8Array) => {
  const bytes = b instanceof Uint8Array ? b : new Uint8Array(b);
  let s = '';
  for (const byte of bytes) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

async function accessToken(saJson: string): Promise<string> {
  const sa = JSON.parse(saJson);
  const now = Math.floor(Date.now() / 1000);
  const claim = {
    iss: sa.client_email,
    scope: 'https://www.googleapis.com/auth/datastore',
    aud: 'https://oauth2.googleapis.com/token',
    iat: now,
    exp: now + 3600,
  };
  const head = b64url(enc.encode(JSON.stringify({ alg: 'RS256', typ: 'JWT' })));
  const body = b64url(enc.encode(JSON.stringify(claim)));
  const input = `${head}.${body}`;

  const pem = sa.private_key.replace(/-----(BEGIN|END) PRIVATE KEY-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(pem), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    'pkcs8', der, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, enc.encode(input));

  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: `${input}.${b64url(sig)}`,
    }),
  });
  if (!res.ok) throw new Error(`token exchange failed: ${res.status} ${await res.text()}`);
  return (await res.json() as any).access_token;
}

// ─── Firestore write ──────────────────────────────────────────────────────────

const PROJECT = 'lasalle-stompers';
const DOCS = `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents`;
const COLL = 'teams/main/teamsnapEvents';

const sv = (v: string | boolean) =>
  typeof v === 'boolean' ? { booleanValue: v } : { stringValue: v };

/**
 * Mirror every event in ONE Firestore :commit call.
 *
 * This used to be a PATCH per event. That worked locally but died in production
 * with "Too many subrequests by single Worker invocation": a Worker invocation
 * is capped at 50 outbound fetches (1000 on paid plans) and 116 events meant
 * ~120. Batching is not an optimisation here, it is what makes the job possible
 * at all — and it keeps the whole mirror atomic, so a partial write can never
 * leave the calendar half-updated.
 *
 * Firestore caps a commit at 500 writes, so chunk defensively even though this
 * feed is ~116 events; a busier season must not silently hit the ceiling.
 *
 * `coachType` is deliberately absent from every updateMask. The coach chose
 * "TeamSnap always wins" for date/time/location, but a *type* override is the
 * one exception: the classifier reads free text and will misfile some events,
 * and each poll would otherwise re-assert the same wrong type forever.
 */
const COMMIT_CHUNK = 400;

function writeFor(ev: TsEvent) {
  const fields: Record<string, any> = {
    uid: sv(ev.uid), title: sv(ev.title), type: sv(ev.type),
    canceled: sv(ev.canceled), date: sv(ev.date), time: sv(ev.time),
    allDay: sv(ev.allDay), endDate: sv(ev.endDate), venue: sv(ev.venue),
    address: sv(ev.address), arrival: sv(ev.arrival), tz: sv(ev.tz),
    modified: sv(ev.modified), source: sv('teamsnap'),
    missingFromFeed: { booleanValue: false },
  };
  return {
    update: {
      name: `projects/${PROJECT}/databases/(default)/documents/${COLL}/${encodeURIComponent(ev.uid)}`,
      fields,
    },
    updateMask: { fieldPaths: Object.keys(fields) },
  };
}

/** Events the feed no longer lists are tombstoned, not deleted — a vanished
 *  event still needs to read as "gone" to a parent who saw it yesterday. */
function tombstoneFor(uid: string) {
  return {
    update: {
      name: `projects/${PROJECT}/databases/(default)/documents/${COLL}/${encodeURIComponent(uid)}`,
      fields: { missingFromFeed: { booleanValue: true } },
    },
    updateMask: { fieldPaths: ['missingFromFeed'] },
  };
}

/**
 * A doc recording when the CRON last ran, which is NOT the same thing as any
 * event's `modified` field: that carries the feed's own LAST-MODIFIED, i.e. when
 * the coach last edited the event in TeamSnap. Deriving freshness from it would
 * report ~22h of age one second after a clean sync, and would freeze whenever the
 * coach is not editing. The calendar's "Synced Nm ago" line needs OUR clock.
 *
 * It lives at `<COLL>/__sync__` — inside the collection the calendar already
 * subscribes to, so it costs no extra read and needs no new Firestore rule. The
 * leading underscores keep it clear of the UID namespace (TeamSnap UIDs look like
 * `9230745-366776389`); consumers MUST skip it when building events.
 */
function syncMetaWrite(now: number, eventCount: number) {
  return {
    update: {
      name: `projects/${PROJECT}/databases/(default)/documents/${COLL}/__sync__`,
      fields: {
        syncedAt: { integerValue: String(now) },
        eventCount: { integerValue: String(eventCount) },
      },
    },
    updateMask: { fieldPaths: ['syncedAt', 'eventCount'] },
  };
}

async function commit(token: string, writes: any[]): Promise<number> {
  let done = 0;
  for (let i = 0; i < writes.length; i += COMMIT_CHUNK) {
    const chunk = writes.slice(i, i + COMMIT_CHUNK);
    const res = await fetch(`${DOCS}:commit`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ writes: chunk }),
    });
    if (!res.ok) throw new Error(`commit failed: ${res.status} ${await res.text()}`);
    done += chunk.length;
  }
  return done;
}

async function listUids(token: string): Promise<string[]> {
  const uids: string[] = [];
  let pageToken = '';
  // Page size is maxed and the loop bounded: this runs inside the same
  // subrequest budget as the commits above, so it must not grow without limit.
  for (let page = 0; page < 4; page++) {
    const q = new URLSearchParams({ pageSize: '1000', 'mask.fieldPaths': 'uid' });
    if (pageToken) q.set('pageToken', pageToken);
    const res = await fetch(`${DOCS}/${COLL}?${q}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) return uids;
    const body = await res.json() as any;
    for (const d of body.documents || []) {
      const id = decodeURIComponent(d.name.split('/').pop());
      // `__sync__` is our own freshness stamp, not a TeamSnap event. Listing it
      // here would tombstone it as "missing from feed" on the very next run.
      if (id === '__sync__') continue;
      uids.push(id);
    }
    pageToken = body.nextPageToken || '';
    if (!pageToken) break;
  }
  return uids;
}

// ─── Entry point ──────────────────────────────────────────────────────────────

export interface SyncResult {
  ok: boolean;
  skipped?: 'not-modified' | 'no-url';
  events?: number;
  written?: number;
  tombstoned?: number;
  byType?: Record<string, number>;
  error?: string;
}

/**
 * Fetch the feed and mirror it into Firestore. Conditional on ETag: TeamSnap's
 * CDN caches for 4h, so most 15-minute runs get a 304 and cost one request.
 */
export async function syncTeamsnap(env: any): Promise<SyncResult> {
  try {
    return await runSync(env);
  } catch (err: any) {
    // Record the failure where the unauthenticated status endpoint can read it.
    // A cron failure is otherwise invisible without an authenticated tail, which
    // is exactly when you need to know why it broke.
    const msg = String(err && err.message || err).slice(0, 400);
    if (env.SYNC_STATE) {
      await env.SYNC_STATE.put('last-error', JSON.stringify({ at: Date.now(), msg }));
    }
    throw err;
  }
}

async function runSync(env: any): Promise<SyncResult> {
  const url = env.TEAMSNAP_ICS_URL;
  if (!url) return { ok: false, skipped: 'no-url', error: 'TEAMSNAP_ICS_URL not set' };

  const prevTag = env.SYNC_STATE ? await env.SYNC_STATE.get('ics-etag') : null;
  const res = await fetch(url, {
    headers: prevTag ? { 'If-None-Match': prevTag } : {},
  });
  if (res.status === 304) return { ok: true, skipped: 'not-modified' };
  if (!res.ok) return { ok: false, error: `feed fetch ${res.status}` };

  const events = parseIcs(await res.text());
  if (!events.length) return { ok: false, error: 'feed parsed to zero events' };

  const token = await accessToken(env.FIREBASE_SA_JSON);
  const seen = new Set(events.map((e) => e.uid));

  // One commit for the whole mirror, plus tombstones for anything the feed
  // dropped. Total subrequests: feed + token + list + ~1 commit — well inside
  // the per-invocation cap that the old per-event loop blew straight through.
  const stale = (await listUids(token)).filter((uid) => !seen.has(uid));
  const written = await commit(token, [
    ...events.map(writeFor),
    ...stale.map(tombstoneFor),
    syncMetaWrite(Date.now(), events.length),
  ]) - 1; // the meta doc is not an event
  const tombstoned = stale.length;

  const tag = res.headers.get('etag');
  if (tag && env.SYNC_STATE) await env.SYNC_STATE.put('ics-etag', tag);

  const byType: Record<string, number> = {};
  for (const e of events) byType[e.type] = (byType[e.type] || 0) + 1;

  return { ok: true, events: events.length, written, tombstoned, byType };
}

/**
 * Aggregate-only view of what the cron has mirrored, for verifying a run
 * without Firestore credentials on hand. Counts and type split only — never
 * venue, time or address, so this stays safe to expose unauthenticated.
 */
export async function teamsnapStatus(env: any): Promise<any> {
  const token = await accessToken(env.FIREBASE_SA_JSON);
  const res = await fetch(`${DOCS}/${COLL}?pageSize=1000`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return { ok: false, error: `list ${res.status}` };
  const body = await res.json() as any;
  const docs = body.documents || [];
  const byType: Record<string, number> = {};
  let canceled = 0, missing = 0, overrides = 0;
  for (const d of docs) {
    const f = d.fields || {};
    const t = f.type?.stringValue || '?';
    byType[t] = (byType[t] || 0) + 1;
    if (f.canceled?.booleanValue) canceled++;
    if (f.missingFromFeed?.booleanValue) missing++;
    if (f.coachType) overrides++;
  }
  const etag = env.SYNC_STATE ? await env.SYNC_STATE.get('ics-etag') : null;
  const lastError = env.SYNC_STATE ? await env.SYNC_STATE.get('last-error') : null;
  const syncDoc = docs.find((d: any) => d.name.endsWith('/__sync__'));
  return { ok: true, mirrored: docs.length, byType, canceled, missingFromFeed: missing,
           coachOverrides: overrides, haveEtag: !!etag,
           syncedAt: syncDoc?.fields?.syncedAt?.integerValue
             ? Number(syncDoc.fields.syncedAt.integerValue) : null,
           lastError: lastError ? JSON.parse(lastError) : null };
}

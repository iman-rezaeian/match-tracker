# Parent Access & Family View Plan

*2026-08-18 — supersedes the chat-only draft. Owner: Iman. Status: approved direction, not yet built.*

## Why

The access audit found the app's "public" tier is effectively open to anyone
with a Google account, and the data layer exposes more than the UI implies:

- `teams/main` (readable by ANY signed-in Google account): kids' full names,
  inline base64 photos, the schedule with field locations, and
  `teamLiveInput` including the **live stream key**.
- Game docs (readable with **no sign-in at all**): raw dugout audio URLs in
  `voiceSegments[]` (public R2), plus playerId-keyed events that join back to
  the roster for any signed-in reader.
- `allowedUsers` listable by any signed-in user (coach email harvesting);
  `viewerLog` world-writable.

Decisions made: **request-access model** (approval queue, not a typed invite
list — half the registry emails are hotmail/yahoo and may not be Google
accounts), **parents associated to their kids** via `playerIds`, and a
**parent tile home** scoped to their own child.

## Standing rules (do not violate)

1. **No parent phone numbers in Firestore. Ever.** Birthdays also excluded.
2. Parent contact data never enters the git repo (Pages deploys from it).
   The extracted roster contacts live in the git-ignored
   `parent_contacts.local.json` (16 players, 29 contacts, emails+names only).
3. A parent can only read their OWN kid's performance data — enforced in
   Firestore rules via `playerIds`, not just hidden in the UI.
4. Parents see outcome stats only (goals, assists, GK saves, minutes).
   No INV/mistake counts, no performance score, no distance/speed
   (retired metric family). Team-level content (scores, reels, highlights,
   live feed scorer names) stays shared across approved families.
5. `rezaian.iman@gmail.com` is owner-coach AND a parent — imports/approvals
   must never downgrade `role: 'coach'`; add `playerIds` alongside.

## Data model

```
allowedUsers/{email}            # email lowercased = doc ID (rules compare lowercase)
  role: 'coach' | 'parent'
  name: string                  # parent display name
  relationship?: 'mom'|'dad'    # when known from the roster import
  playerIds: [playerId, ...]    # the kid link — authorization primitive
  addedVia: 'roster-import' | 'request' | 'console'
  addedAt: timestamp

accessRequests/{email}          # transient; deleted on approve/deny
  name, photo, note?, ts, status: 'pending'

teams/main/parentSeason/{playerId}   # pipeline-published, tiny (~10-20KB)
  games: [{ gameId, date, opponent, ourScore, oppScore,
            attended: bool, minutes, goals, assists, saves,
            heatmap: int[96]?, heatmapRows: 12, heatmapCols: 8,
            coverage: float }]
```

Notes: parent info lives in the same Firestore DB as everything else;
`viewerLog` already stores emails/names/photos on every sign-in, so this is
structuring existing exposure, not adding a new class of it. `accessRequests`
docs are deleted after action. Heatmap grids come from the analytics docs the
pipeline already writes (`heatmap_grid`, currently unread by the PWA).

## Phases

### Phase 1 — Seed the allowlist
One-time admin script `scripts/seed_allowed_users.py` (service-account creds,
same as pipeline):
- Read live roster from `teams/main` (NOT SEED_ROSTER); match the 16 players
  by name; report matches/misses before writing.
- Upsert `allowedUsers/{lowercased email}` for all contacts in
  `parent_contacts.local.json`: role parent, name, relationship, playerIds.
- Owner special-case per standing rule 5.
- No phones, no birthdays anywhere (rule 1).

### Phase 2 — Request-access flow (app)
- Post-sign-in gate (shell/App level, before Firestore listeners mount):
  read own `allowedUsers` doc → coach: dugout as today · parent/allowed:
  family surface · missing: REQUEST ACCESS screen.
- Request screen writes `accessRequests/{email}` (name, photo, optional
  "I'm ___'s parent" note) → waiting state; live-listens so approval flips
  the app on without a reload. Pending users see nothing else.
- App code changes go in `soccer_team_app.jsx` + the AuthGate in
  `soccer_team_app_standalone_backup.html`, synced via `_sync_html.py`.

### Phase 3 — MANAGE ACCESS panel (dugout)
- Pending queue with approve/deny; approve opens a roster player picker
  (multi-select) and writes the allowedUsers doc, then deletes the request.
- Members list grouped by player; remove = delete doc.
- Pending-count badge on the dugout home tile.
- Any coach can approve parents; only the owner manages coach roles.

### Phase 4 — Rules lockdown (`firestore.rules`, deployed via console)
- `isAllowed()` helper: `exists(allowedUsers/$(token.email.lower()))`.
- `teams/main` read: coach OR allowed (was: any signed-in).
- `games/{id}` + `games/{id}/public/*` read: allowed (was: world-readable).
- `allowedUsers`: parents `get` their own doc only; coach reads all;
  writes coach-only, role 'coach' writable only by owner email.
- `accessRequests`: create/get own doc only (id must equal token email,
  status forced 'pending'); coach reads/updates/deletes all.
- `viewerLog` create/update: signed-in only (was: world-writable).

### Phase 5 — Secrets & dugout-audio relocation
- `teamLiveInput` (rtmpsUrl + streamKey) → coach-only subdoc
  `teams/main/private/liveInput`; game docs keep ONLY the public `hlsUrl`,
  secret fields move to a coach-only game subdoc. Update StartingLineupView
  + live-input delete flows.
- `voiceSegments` → `games/{id}/voice/*` subcollection (coach-only by the
  existing default rule). Update `_voiceUpload` writer + `post_game` readers.
- `scripts/migrate_private_fields.py`: move existing data, strip old fields.

### Phase 6 — Access rollout (lockout-proof order)
1. Seed allowlist (Phase 1).
2. Deploy app with request flow to **beta** (localhost can't sign in;
   verify on the beta Pages URL).
3. Test: work email = unknown account → request → approve from owner.
4. Reconcile `viewerLog` distinct emails vs the allowlist; pre-approve
   regulars (grandparents etc.) so nobody is locked out on game day.
5. Flip rules in console. Re-verify parent + coach + unknown paths on beta.
6. Promote dev → beta → main per the usual chain. Not on a game day.

### Phase 7 — Per-kid season data (pipeline)
- Pipeline publisher step: after analytics, write/refresh
  `teams/main/parentSeason/{playerId}` rows for that game (attended from
  squad + on-field windows; G/A from goal events incl. assist fields; saves
  for GK; minutes from sub-corrected windows).
- Heatmap honesty gate: include the 12×8 grid only when
  `coverage_frac ≥ 0.3` (trust dial: ≳0.5 solid, <0.25 sliver); else omit →
  UI shows "–" for that game's map. Label "from tracked minutes".
- Rules: `parentSeason/{playerId}` readable iff coach OR
  `playerId in get(allowedUsers/$(email)).data.playerIds`.
- `scripts/backfill_parent_season.py`: build season docs for all finished
  games from analytics docs already in Firestore (no pipeline re-runs).

### Phase 8 — Parent home redesign (tile hub)
Layout (coach's spec):

```
┌─────────────────────────────────┐
│ Featured hero card              │  live scoreboard / today's result /
├────────────────┬────────────────┤  next kickoff — stays on top
│ 🔥 HEATMAPS    │ 📊 MATCH STATS │  per-kid tiles (playerIds-gated;
├────────────────┼────────────────┤  hidden for kid-less viewers)
│ 🏟 PAST GAMES  │ 🎬 TRAINING    │  absorb old inline list + video band
├────────────────┴────────────────┤
│ UPCOMING GAMES (inline)         │  the ONLY inline section below tiles
└─────────────────────────────────┘
```

- HEATMAPS tile → chronological grid of per-game mini-pitches (reuse
  `PlayerHeatmap`), tap to enlarge; "–" where absent or below coverage gate.
- MATCH STATS tile → season table: date/opponent/result, G, A, (saves), min;
  full "–" row when not attended.
- PAST GAMES tile → existing list → per-game pages (scoreboard, full-game
  reel, highlights) unchanged inside.
- TRAINING tile → existing TrainingHub.
- Multi-kid switcher renders only when `playerIds.length > 1`.

## Defaults already agreed

- Coaches (not just owner) can approve parent requests.
- Pending requesters see only the waiting screen.
- Featured hero card stays above the tiles.
- Approved viewers without a kid link get PAST GAMES + TRAINING + schedule.

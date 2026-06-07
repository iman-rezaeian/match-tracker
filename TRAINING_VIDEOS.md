# Training Videos — public-page YouTube playlist integration

Adds a **Training Videos** section to the public home page that surfaces two
YouTube playlists as a native, in-app thumbnail grid with an inline player
(not just outbound links). Videos always stream from YouTube's free embed — no
hosting or streaming cost.

- Commit: `4097563` on `dev`, merged to `beta` (`4a896f5`). Both pushed to origin.
- Playlists:
  - `PL1KXWwWfqmixlAhd_t--UOnux0EcGDo5o`
  - `PL1KXWwWfqmiwkxyAZaAAyx8o9LT3oCHW9`

## What was built

### Front-end — `soccer_team_app.jsx`
- **`TRAINING_PLAYLISTS`** config constant holding the two playlist IDs. Section
  titles come from each playlist's own YouTube title (fetched at runtime), so
  adding/removing a playlist here is the only change needed.
- **`YouTubeEmbed`** gained an `interactive` prop. When set: normal YouTube
  controls (play/pause, scrub, keyboard, fullscreen, CC) and no click-blocking
  overlay. Default (live scorebug) path is unchanged — still locked down.
- **`TrainingVideosSection`** — reusable block that fetches each playlist from
  the worker and renders:
  - per-playlist heading + video count,
  - a responsive thumbnail grid (`grid-cols-2` on mobile → `sm:grid-cols-3`),
  - loading skeletons and an error/retry state,
  - an inline modal player (`<YouTubeEmbed interactive />`) on tap.
- Embedded as `<TrainingVideosSection showHeading />` in **`PublicHomePage`**
  (the bare-URL public page), after the PAST GAMES list.
- Bonus: coach-side **TRAINING** home tile + full-screen `TrainingVideosView`
  wrapper that reuses the same section (behind `?coach`).

### Worker — `worker/src/index.ts`
- New **`GET /youtube-playlist?id=<playlistId>`** endpoint:
  - public read (no password), CORS via the existing `corsHeaders`/`json()`,
  - validates the id, 500 if `YOUTUBE_API_KEY` missing,
  - fetches the playlist title + items (paginated, capped at ~100 videos),
  - skips private/deleted entries,
  - returns `{ playlistId, title, items: [{ videoId, title, thumbnail, position }] }`,
  - **edge-cached 1h** (`caches.default` + `Cache-Control: public, max-age=3600`)
    so repeat viewers don't each spend YouTube API quota.
- Mirrors the existing `/youtube-live` handler and reuses its `YOUTUBE_API_KEY`
  secret (already configured on the worker — no new config needed).

### Generated artifacts
- `soccer_team_app_standalone_backup.html` and `_site/index.html` regenerated via
  `python3 _sync_html.py`.

## Verification done
Front-end verified in the local preview against a **stubbed** playlist response
(real worker not reachable from the dev environment):
- ✅ section renders on the public page with both playlists, titles + counts
- ✅ responsive grid (2-col mobile, 3-col desktop)
- ✅ tap → inline player plays with full YouTube controls
- ✅ close returns to the grid
- ✅ no runtime console errors

## Status: DONE (2026-06-07)

- ✅ **Worker deployed** — `stompers-upload`, version `d855c560-a2ae-4ee6-83e8-34e86e7a21a0`.
  `GET /youtube-playlist` is live; R2 binding + vars intact; secrets (incl.
  `YOUTUBE_API_KEY`) preserved across the deploy.
- ✅ **End-to-end verified** — both playlists return real data:
  - Soccer Training — 57 videos
  - Goalkeeper Training — 39 videos
- ✅ **PWA published** — front-end ships in `soccer_team_app.jsx` (deployed on
  dev/beta/main via Cloudflare Pages); the public Training Videos section now
  populates from the live endpoint.

### How the worker was deployed past the corp-VPN npm block
`npm`/`npx` can't reach the public registry on the VPN, but the Artifactory npm
**direct-proxy** repo works (the *virtual* repo had stale dep metadata):

```bash
cd worker
export npm_config_registry="https://artifactory.foc.zone/artifactory/api/npm/npm-review/"
export npm_config_ignore_scripts=true     # skip sharp/fsevents native postinstall (not needed for deploy)
npx -y wrangler@latest login              # browser oauth
npx -y wrangler@latest deploy
```

## Notes
- `_site/_mock.html` is a local-only preview harness (stubs Firebase auth + the
  playlist fetch so the page renders without Google sign-in). It is gitignored
  and is wiped on the next `python3 _sync_html.py` run; regenerate with
  `python3 /tmp/build_mock.py`.
- Adding a coach-facing UI to manage `TRAINING_PLAYLISTS` from within the app was
  considered and declined — the hardcoded list is intentional.

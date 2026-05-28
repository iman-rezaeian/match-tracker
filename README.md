# Match Tracker

LaSalle Stompers U10 — match tracking PWA.

## Setup after cloning

Enable the tracked git hooks:

```bash
git config core.hooksPath .githooks
```

This activates the pre-push hook that blocks direct pushes to `main` (which auto-deploys to production). Use `git push --no-verify` to bypass when intentional.

## Deployment

- **Hosting**: Cloudflare Pages (project: `match-tracker`, build = `python3 _sync_html.py`, output = `_site`)
- **Production**: `main` branch → `stompers2016.com` (also `match-tracker-843.pages.dev`)
- **Preview**: `beta` branch → Cloudflare Pages preview URL
- **Auth/DB**: Firebase (project `lasalle-stompers`) — Google sign-in + Firestore
- **Video storage**: Cloudflare R2 via worker `stompers-upload.rezaian-iman.workers.dev`
- **Access roles**: Firestore collection `allowedUsers/{email}` with `role: 'coach' | 'viewer'`

## Local dev

Edit `soccer_team_app.jsx`, then run `python3 _sync_html.py` to regenerate the standalone HTML and `_site/index.html`. Open the HTML file in a browser to test.

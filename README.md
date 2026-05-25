# Match Tracker

## Setup after cloning

Enable the tracked git hooks:

```bash
git config core.hooksPath .githooks
```

This activates the pre-push hook that blocks direct pushes to `main` (which auto-deploys to production). Use `git push --no-verify` to bypass when intentional.

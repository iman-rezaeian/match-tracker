#!/usr/bin/env bash
# Post-game analytics runner.
#
# Usage:
#   ./run_analytics.sh                # interactive: list recent games, pick one
#   ./run_analytics.sh <game-id>      # run directly on this game
#   ./run_analytics.sh list           # just show recent games and exit
#
# Calibration is read from the per-game `calibration` field on the game doc.
# If missing, a local browser tab opens to mark the 4 field corners.
#
# Creates a local venv on first run, installs requirements, then runs the
# Tier-A pipeline. Results are written to Firestore and appear in the
# Analytics panel in the app.

set -euo pipefail

cd "$(dirname "$0")"

GAME_ID="${1:-}"

if [ "${GAME_ID}" = "-h" ] || [ "${GAME_ID}" = "--help" ]; then
  sed -n '2,15p' "$0"
  exit 0
fi

# Load local secrets (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT,
# R2_PUBLIC_BASE, GOOGLE_APPLICATION_CREDENTIALS, etc.) from .env if present.
# Keep .env gitignored — it holds credentials.
if [ -f .env ]; then
  echo "→ Loading .env"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Friendly warnings — pipeline will fail later without these, surface it early.
for v in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT; do
  if [ -z "${!v:-}" ]; then
    echo "⚠  $v not set (needed for clip uploads to R2). Add it to .env."
  fi
done

VENV_DIR=".venv-post-game"

if [ ! -d "$VENV_DIR" ]; then
  echo "→ Creating venv at $VENV_DIR (one-time setup)…"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Corp VPN blocks pypi.org — must go through Artifactory mirror.
PIP_INDEX_URL="https://artifactory.foc.zone/artifactory/api/pypi/rdf-pypi-virtual/simple"
PIP_EXTRA_INDEX_URL="https://artifactory.foc.zone/artifactory/api/pypi/pypi-remote/simple"
PIP_OPTS=(--index-url "$PIP_INDEX_URL" --extra-index-url "$PIP_EXTRA_INDEX_URL")

# Install requirements if the marker is missing or requirements.txt is newer.
MARKER="$VENV_DIR/.requirements-installed"
if [ ! -f "$MARKER" ] || [ "post_game/requirements.txt" -nt "$MARKER" ]; then
  echo "→ Installing/updating requirements (via Artifactory mirror)…"
  pip install --quiet --upgrade "${PIP_OPTS[@]}" pip
  pip install --quiet "${PIP_OPTS[@]}" -r post_game/requirements.txt
  touch "$MARKER"
fi

# `list` mode: just show recent games and exit.
if [ "${GAME_ID}" = "list" ]; then
  python -m post_game.cli list --limit 20
  exit 0
fi

# No game-id given → list recent games and let the user pick by number.
if [ -z "${GAME_ID}" ]; then
  echo "→ No game-id given. Fetching recent games…"
  TMP_JSON="$(mktemp)"
  python - <<'PY' > "$TMP_JSON"
import json
from post_game import firestore_io
print(json.dumps(firestore_io.list_recent_games_snapshots(limit=15)))
PY
  python -m post_game.cli list --limit 15
  echo ""
  read -r -p "Pick a game by number (or paste a game-id, or q to quit): " PICK
  if [ -z "$PICK" ] || [ "$PICK" = "q" ] || [ "$PICK" = "Q" ]; then
    echo "Aborted."
    rm -f "$TMP_JSON"
    exit 0
  fi
  if [[ "$PICK" =~ ^[0-9]+$ ]]; then
    IDX=$((PICK - 1))
    GAME_ID="$(python -c "import json,sys; rows=json.load(open('$TMP_JSON')); print(rows[$IDX]['id'])" 2>/dev/null || true)"
    if [ -z "$GAME_ID" ]; then
      echo "✗ Invalid selection."
      rm -f "$TMP_JSON"
      exit 1
    fi
    echo "→ Selected: $GAME_ID"
  else
    GAME_ID="$PICK"
  fi
  rm -f "$TMP_JSON"
fi

echo "→ Running analytics for game $GAME_ID…"
python -m post_game.cli run --game-id "$GAME_ID"

echo "✓ Done. Refresh the Analytics panel in the app to see results."
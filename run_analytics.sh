#!/usr/bin/env bash
# Post-game analytics runner.
#
# Usage:
#   ./run_analytics.sh                # interactive: list recent games, pick one
#   ./run_analytics.sh <game-id>      # run directly on this game
#   ./run_analytics.sh list           # just show recent games and exit
#
# Flags (may appear before or after <game-id>):
#   --skip-install   Don't touch pip — use the venv as-is. Useful when the
#                    Artifactory mirror is down but the venv is already populated.
#   -h, --help       Show this help.
#
# Calibration is read from the per-game `calibration` field on the game doc.
# If missing, a local browser tab opens to mark the 4 field corners.
#
# Creates a local venv on first run, installs requirements, then runs the
# Tier-A pipeline. Results are written to Firestore and appear in the
# Analytics panel in the app.

set -euo pipefail

cd "$(dirname "$0")"

SKIP_INSTALL=0
GAME_ID=""
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) if [ -z "$GAME_ID" ]; then GAME_ID="$arg"; fi ;;
  esac
done

# Honor env-var override too, for CI / aliases.
if [ "${SKIP_PIP_INSTALL:-0}" = "1" ]; then SKIP_INSTALL=1; fi

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

# Pick a Python that has a working ensurepip. Prefer 3.13 → 3.12 → 3.11 → system python3.
# (Some installs of 3.14 ship a broken ensurepip; skip those.)
pick_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c "import ensurepip" >/dev/null 2>&1; then
        echo "$candidate"; return 0
      fi
    fi
  done
  echo ""; return 1
}

if [ ! -d "$VENV_DIR" ]; then
  PY="$(pick_python)"
  if [ -z "$PY" ]; then
    echo "✗ Could not find a Python with a working ensurepip. Install via: brew install python@3.13"
    exit 1
  fi
  echo "→ Creating venv at $VENV_DIR using $PY (one-time setup)…"
  "$PY" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Corp VPN blocks pypi.org — must go through Artifactory mirror.
PIP_INDEX_URL="https://artifactory.foc.zone/artifactory/api/pypi/rdf-pypi-virtual/simple"
PIP_EXTRA_INDEX_URL="https://artifactory.foc.zone/artifactory/api/pypi/pypi-remote/simple"
PIP_OPTS=(--index-url "$PIP_INDEX_URL" --extra-index-url "$PIP_EXTRA_INDEX_URL")

# Install requirements if the marker is missing or requirements.txt is newer.
# Skipped entirely when --skip-install is set (handy if Artifactory is down).
MARKER="$VENV_DIR/.requirements-installed"
if [ "$SKIP_INSTALL" = "1" ]; then
  echo "→ Skipping pip install (--skip-install)."
elif [ ! -f "$MARKER" ] || [ "post_game/requirements.txt" -nt "$MARKER" ]; then
  echo "→ Installing/updating requirements (via Artifactory mirror)…"
  if ! pip install --quiet --upgrade "${PIP_OPTS[@]}" pip; then
    echo "⚠  pip self-upgrade failed (Artifactory issue?). Re-run with --skip-install if your venv is already populated."
    exit 1
  fi
  if ! pip install --quiet "${PIP_OPTS[@]}" -r post_game/requirements.txt; then
    echo "⚠  Requirements install failed (Artifactory issue?). Re-run with --skip-install if your venv is already populated."
    exit 1
  fi
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
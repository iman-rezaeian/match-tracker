#!/usr/bin/env bash
# Post-game analytics runner.
#
# Usage:
#   ./run_analytics.sh <game-id> <field-name>
#
# Creates a local venv on first run, installs requirements, then runs the
# Tier-A pipeline. Results are written to Firestore and appear in the
# Analytics panel in the app.

set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <game-id> <field-name>"
  echo "Example: $0 mpo9z0083esgx \"test\""
  exit 1
fi

GAME_ID="$1"
FIELD_NAME="$2"

cd "$(dirname "$0")"

VENV_DIR=".venv-post-game"

if [ ! -d "$VENV_DIR" ]; then
  echo "→ Creating venv at $VENV_DIR (one-time setup)…"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install requirements if the marker is missing or requirements.txt is newer.
MARKER="$VENV_DIR/.requirements-installed"
if [ ! -f "$MARKER" ] || [ "post_game/requirements.txt" -nt "$MARKER" ]; then
  echo "→ Installing/updating requirements…"
  pip install --quiet --upgrade pip
  pip install --quiet -r post_game/requirements.txt
  touch "$MARKER"
fi

echo "→ Running analytics for game $GAME_ID on field \"$FIELD_NAME\"…"
python -m post_game.cli run \
  --game-id "$GAME_ID" \
  --field-name "$FIELD_NAME"

echo "✓ Done. Refresh the Analytics panel in the app to see results."

#!/usr/bin/env bash
# Launch the click-sampling app.
#
#   ./run_click_sampler.sh [game-id]
#
# Defaults to Game 1 (mrhvbvwi1gjpn), which has pilot frames already rendered.
# Note `. ./.env` rather than `. .env`: zsh does not search the current
# directory for a bare filename, so the shorter form fails with
# ".: no such file or directory".
set -euo pipefail

GAME_ID="${1:-mrhvbvwi1gjpn}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="/Users/irezaeian/match-tracker/.venv-post-game"

cd "$REPO"
if [[ ! -e .env ]]; then
  echo "no .env in $REPO — symlink it from the main checkout first" >&2
  exit 1
fi
set -a; . ./.env; set +a

if [[ ! -f "tracking/outputs/click_samples/$GAME_ID/index.json" ]]; then
  echo "No rendered frames for $GAME_ID. Render them first:" >&2
  echo "  PYTHONPATH=. $VENV/bin/python -m tracking.click_sample_render \\" >&2
  echo "      --game-id $GAME_ID --interval 30 --limit 20" >&2
  exit 1
fi

# Pick a free port. 8501/8502 are usually taken by the calibration UI and the
# older labelling app, and Streamlit silently lands on a different port when its
# default is busy -- which leaves you refreshing someone else's app.
PORT="${PORT:-8511}"
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done
echo "starting on http://localhost:$PORT"

exec env PYTHONPATH="$REPO" "$VENV/bin/streamlit" run tracking/click_sample_app.py \
  --server.port "$PORT" -- --game-id "$GAME_ID"

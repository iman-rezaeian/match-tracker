#!/usr/bin/env bash
# Launch the Stompers Match Workbench — the ONE Mac app for post-game work:
# attach/calibrate/run, click sampling, narration, review & confirm, publish.
#
#   ./run_workbench.sh
#
# Replaces launching ui_app and the click sampler separately (both still work
# standalone). `. ./.env` not `. .env`: zsh does not search the current
# directory for a bare filename.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv-post-game"

cd "$REPO"
if [[ ! -e .env ]]; then
  echo "no .env in $REPO" >&2
  exit 1
fi
set -a; . ./.env; set +a

# 8501/8502/8511 are the historical single-app ports; keep clear of them.
PORT="${PORT:-8530}"
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done
echo "Workbench starting on http://localhost:$PORT"

exec env PYTHONPATH="$REPO" "$VENV/bin/streamlit" run workbench/app.py \
  --server.port "$PORT" -- "$@"

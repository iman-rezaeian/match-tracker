#!/usr/bin/env bash
# Launch the stitch-pair labeling UI (Phase 0 ground truth).
#
# Usage:  ./run_labeler.sh
#
# Opens a local Streamlit app to label the tracklet pairs in
# tracking/labels/<game>/pairs.csv as same / different / can't-tell.
# Labels save straight back into the CSVs; then run:
#   python -m tracking.stitch_pr_eval
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; source .env; set +a; fi

VENV_DIR=".venv-post-game"
if [ ! -d "$VENV_DIR" ]; then
  echo "✗ $VENV_DIR not found. Run ./run_analytics.sh once to create it."
  exit 1
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "→ Opening the stitch labeler at http://localhost:8521"
echo "  Keys: 1/S = same · 2/D = different · 3/U = can't tell · ← = back"
exec streamlit run tracking/stitch_label_app.py --server.port 8521

#!/bin/bash
# Overnight tight-gate sweep — evidence-backed re-tracks of the field-space tracker.
#
# WHY THESE VALUES (measured 2026-08-05 on W8 cached tracks, on-field real motion):
#   a player moves ~0.10 m median / 0.32 m p90 / 0.67 m p99 per 0.1 s step,
#   but nearest-neighbour spacing is 4.24 m median / 1.96 m at p10. The share of
#   detections with a WRONG body inside the association gate:
#       1.0-1.5 m ->  0%      3.0 m -> 30%
#       2.0 m     -> 11%      3.9 m -> 45%   (current fresh gate)
#                             6.0 m -> 68%   (current stale cap)
#   So the shipped gate over-reaches 13-20x what a player can actually move, and
#   a 1.0-1.5 m gate has ZERO rivals in reach while still being 3-5x a real step.
#   That is the region this sweep explores.
#
# Each config is a FULL both-halves re-track (~90 min) into a TAGGED cache, then
# scored. Zero Firestore writes; the live analytics doc and the unsuffixed live
# cache are never touched (retrack_smoke writes tracks_raw.<tag>.parquet only).
#
# Gate to beat: coverage > 52.1% AND MIXED-second <= 15.5% (equirect baseline).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && source .env; set +a
source .venv-post-game/bin/activate

GAME=mri01pvelv46d
OUT=/tmp/${GAME}.gate_sweep.log
: > "$OUT"

say() { echo "$(date '+%H:%M:%S') $*" | tee -a "$OUT"; }

say "=== OVERNIGHT TIGHT-GATE SWEEP · $GAME ==="
say "baseline to beat: coverage 52.1% | MIXED-second 15.5%"
say ""

# tag|SLACK|CAP|BUFFER   (gate = min(9.0*dt*(tsu+1) + SLACK, CAP))
# g1: base gate 0.9+0.5=1.4m, cap 2.0 -> squarely in the ZERO-rival region
# g2: slightly looser base but still under real spacing, tighter stale reach
# g3: g1 gate + short buffer so a lost track can't linger and reacquire wrong
CONFIGS=(
  "g1_slack0.5_cap2.0|0.5|2.0|20"
  "g2_slack1.0_cap3.0|1.0|3.0|20"
  "g3_slack0.5_cap2.0_buf8|0.5|2.0|8"
)

for cfg in "${CONFIGS[@]}"; do
  IFS='|' read -r TAG SLACK CAP BUF <<< "$cfg"
  say ">>> $TAG : PITCH_SLACK_M=$SLACK PITCH_GATE_CAP_M=$CAP TRACK_BUFFER_S=$BUF"

  if [ -f "post_game/outputs/$GAME/tracks_raw.$TAG.parquet" ]; then
    say "    (cache exists, skipping re-track)"
  else
    TRACK_PITCH=1 PITCH_SLACK_M="$SLACK" PITCH_GATE_CAP_M="$CAP" TRACK_BUFFER_S="$BUF" \
      python -m tracking.retrack_smoke --game-id "$GAME" --full-game --tag "$TAG" \
      >> "/tmp/${GAME}.${TAG}.retrack.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then say "    RE-TRACK FAILED (rc=$rc) — see /tmp/${GAME}.${TAG}.retrack.log"; continue; fi
    say "    re-track done"
  fi

  # Score: named-coverage + fragments, then the swap gauge.
  python -m tracking.eval_stitch_assign --game-id "$GAME" --ckpt-suffix "$TAG" --label "$TAG" 2>/dev/null \
    | grep -E "fragments|NAMED-COVERAGE" | sed 's/^/    /' | tee -a "$OUT"
  python -m tracking.eval_swap_mix --game-id "$GAME" --npz "$TAG" --label "$TAG" 2>/dev/null \
    | grep -E "MIXED-second" | sed 's/^/    /' | tee -a "$OUT"
  say ""
done

say "=== SWEEP COMPLETE ==="
say "compare each config's coverage vs 52.1% and MIXED-second vs 15.5%."
say "If NONE clears BOTH, the honest conclusion is to shelve field-space tracking"
say "and leave TRACK_PITCH default OFF (see memory tracking-accuracy-findings)."

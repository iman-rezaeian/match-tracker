#!/bin/bash
# Detached pipeline runner — survives Claude session rolls AND Mac idle sleep.
#
# Background (`run_in_background`) tasks are tied to the launching Claude session
# and get reaped when the session rolls over (context management), which kept
# killing long pipeline runs mid-Stage-2. This launches the run in its OWN
# session (setsid via Python) wrapped in `caffeinate -dimsu`, so it's detached
# from both the Claude task system and the controlling terminal, and the Mac
# won't idle-sleep while it runs.
#
# Usage: ./run_game_detached.sh <game-id> [extra pipeline flags...]
#   tails to /tmp/<game-id>.run.log ; prints the detached PID and exits.
set -euo pipefail
cd "$(dirname "$0")"
GAME_ID="${1:?usage: run_game_detached.sh <game-id> [flags...]}"
shift || true
LOG="/tmp/${GAME_ID}.run.log"

# Inner command: load env + venv, then run under caffeinate.
#
# The Anthropic token (Stage 5's VLM jersey-number reads) comes from the macOS
# Keychain, not .env — a bearer token in a dotfile is one `cat` away from a log,
# a screen-share, or a chat transcript. `security` prints it only to this
# process's stdout, and it lives solely in the child's environment. Absent or
# expired just means no drafts: the pipeline wraps the VLM stage in try/except,
# so tracking still completes (re-mint with `ant auth print-credentials
# --access-token` and re-run with --stats-only... except the VLM needs the raw
# video, so a fresh token BEFORE the run is worth the ten seconds).
read -r -d '' INNER <<EOF || true
cd "$(pwd)"
set -a; [ -f .env ] && source .env; set +a
export ANTHROPIC_OAUTH_TOKEN="\$(security find-generic-password -s anthropic-oauth-token -a "\$USER" -w 2>/dev/null)"
[ -n "\$ANTHROPIC_OAUTH_TOKEN" ] || echo "WARNING: no anthropic-oauth-token in Keychain — VLM drafts will be skipped"
source .venv-post-game/bin/activate
exec caffeinate -dimsu python -m post_game.cli run --game-id "${GAME_ID}" ${*:-}
EOF

# setsid-equivalent on macOS (no /usr/bin/setsid): Python os.setsid() then exec
# bash with the inner command. The new session detaches it from this shell's
# process group, so it outlives this Bash call and any session roll.
INNER="$INNER" LOG="$LOG" python3 - <<'PY'
import os, sys
log = os.environ["LOG"]; inner = os.environ["INNER"]
pid = os.fork()
if pid > 0:
    # Parent: wait briefly so the child can setsid, then report and exit.
    os._exit(0)
os.setsid()  # new session — detaches from controlling terminal + pgroup
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY); os.dup2(devnull, 0)
os.execvp("bash", ["bash", "-lc", inner])
PY
sleep 1
PIDS=$(pgrep -f "post_game.cli run --game-id ${GAME_ID}" || true)
echo "Detached run launched for ${GAME_ID}. PID(s): ${PIDS:-<starting>}  log: ${LOG}"

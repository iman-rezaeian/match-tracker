"""Streamlit UI for the post-game pipeline — run locally on the Mac.

Launch:
    streamlit run post_game/ui_app.py

Pipeline + calibration run in **subprocesses** so the UI stays responsive
and streams stdout/stderr live. Source video stays on the Mac (file://);
clips/reels still upload to R2 for the parent-facing app.
"""

from __future__ import annotations

import os

# macOS: Streamlit is multi-threaded. When we later fork() to spawn the
# pipeline subprocess, Objective-C's libdispatch crashes with
# "BUG IN CLIENT OF LIBDISPATCH: trying to lock recursively". This env var
# MUST be set BEFORE any cv2/AVFoundation/PyObjC import happens — that's
# why it's at the very top of the file, before `import streamlit`.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import streamlit as st

# Streamlit runs this file as a top-level script (not as `post_game.ui_app`),
# so relative imports fail. Make sure the repo root is importable and use
# absolute imports.
import sys
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from post_game import firestore_io


def _parse_offset_str(s: str) -> float:
    """Accept HH:MM:SS, MM:SS, or raw seconds (float)."""
    s = s.strip()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        if len(parts) == 3:
            h, m, sec = parts
        elif len(parts) == 2:
            h, m, sec = 0.0, parts[0], parts[1]
        else:
            raise ValueError(f"Bad time format: {s}")
        return h * 3600 + m * 60 + sec
    return float(s)


def _seconds_to_hms(s: float) -> str:
    s = max(0.0, float(s))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{sec:05.2f}"


# --- session-state keys --------------------------------------------------

K_PROC = "pg_proc"
K_PROC_KIND = "pg_kind"
K_PROC_GAME = "pg_game_id"
K_LOG = "pg_log"
K_LOG_QUEUE = "pg_log_queue"
K_STARTED_AT = "pg_started_at"


for k, default in [
    (K_PROC, None),
    (K_PROC_KIND, None),
    (K_PROC_GAME, None),
    (K_LOG, []),
    (K_LOG_QUEUE, None),
    (K_STARTED_AT, None),
]:
    if k not in st.session_state:
        st.session_state[k] = default


# --- helpers -------------------------------------------------------------

@st.cache_data(ttl=20)
def _list_games(limit: int, only_unprocessed: bool) -> list[dict]:
    rows = firestore_io.list_recent_games_snapshots(limit=max(limit, 5))
    if only_unprocessed:
        rows = [r for r in rows if r["has_video"] and not r["has_analytics"]]
    return rows[:limit]


def _format_row(r: dict) -> str:
    score = f"{r['our_score']}-{r['opp_score']}"
    flags = "".join([
        "🎥" if r["has_video"] else "  ",
        "⏱" if r.get("has_video_offset") else "  ",
        "📐" if r["has_calibration"] else "  ",
        "📊" if r["has_analytics"] else "  ",
    ])
    return f"{r['date'] or '—'}  vs {r['opponent'] or '—':<20}  {score:>5}  [{r['status'] or '—':<8}]  {flags}  ({r['id']})"


def _start_subprocess(kind: str, game_id: str, args: list[str]) -> None:
    """Start `python -m post_game.cli ...` and pump stdout into a queue."""
    if st.session_state[K_PROC] is not None and st.session_state[K_PROC].poll() is None:
        st.warning("Another job is already running. Stop it first.")
        return

    cmd = [sys.executable, "-u", "-m", "post_game.cli", *args]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # macOS Python crashes with "BUG IN CLIENT OF LIBDISPATCH: trying to lock
    # recursively" when a multi-threaded process (Streamlit) forks a child
    # that uses Objective-C frameworks (cv2, AVFoundation via ffmpeg, etc).
    # Disabling the post-fork init check is the standard workaround.
    env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    q: queue.Queue = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line.rstrip("\n"))
        q.put(f"__EXIT__:{proc.wait()}")

    threading.Thread(target=reader, daemon=True).start()

    st.session_state[K_PROC] = proc
    st.session_state[K_PROC_KIND] = kind
    st.session_state[K_PROC_GAME] = game_id
    st.session_state[K_LOG] = [f"$ {' '.join(cmd)}"]
    st.session_state[K_LOG_QUEUE] = q
    st.session_state[K_STARTED_AT] = time.time()


def _drain_log_queue() -> bool:
    """Pull any new lines into the log list. Returns True if process just exited."""
    q = st.session_state[K_LOG_QUEUE]
    if q is None:
        return False
    finished = False
    while True:
        try:
            line = q.get_nowait()
        except queue.Empty:
            break
        if line.startswith("__EXIT__:"):
            code = line.split(":", 1)[1]
            st.session_state[K_LOG].append(f"[process exited with code {code}]")
            st.session_state[K_PROC] = None
            finished = True
        else:
            st.session_state[K_LOG].append(line)
    return finished


def _stop_running() -> None:
    proc = st.session_state[K_PROC]
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    st.session_state[K_PROC] = None


# --- UI ------------------------------------------------------------------

st.set_page_config(page_title="Post-game pipeline", layout="wide")
st.title("⚽ Post-game pipeline (local)")
st.caption("Lists games from Firestore. Source video stays on this Mac; clips upload to R2 for parents.")

with st.sidebar:
    st.header("Game list")
    limit = st.slider("Show last N games", 5, 50, 15)
    only_unprocessed = st.checkbox("Only unprocessed (has-video & no-analytics)", value=False)
    if st.button("Refresh list"):
        _list_games.clear()

rows = _list_games(limit=limit, only_unprocessed=only_unprocessed)

if not rows:
    st.warning("No games found.")
    st.stop()

st.markdown("**Legend**: 🎥 video attached · ⏱ 1st-half kickoff set · 📐 calibrated · 📊 analytics ran")
labels = [_format_row(r) for r in rows]
idx = st.radio("Pick a game", range(len(rows)), format_func=lambda i: labels[i])
game = rows[idx]
game_id = game["id"]

st.divider()
st.subheader(f"Selected: {game['date']} vs {game['opponent']}  ({game_id})")

cols = st.columns(3)
cols[0].metric("Score", f"{game['our_score']}-{game['opp_score']}")
cols[1].metric("Status", game["status"] or "—")
cols[2].metric("Analytics", "✓" if game["has_analytics"] else "—")

try:
    game_doc = firestore_io.get_game(game_id)
    current_video = game_doc.video_url or ""
except Exception as e:
    st.error(f"Could not load game doc: {e}")
    st.stop()

if current_video:
    st.success(f"📼 Current videoUrl: `{current_video}`")
else:
    st.info("📼 No video attached yet.")

is_running = st.session_state[K_PROC] is not None and st.session_state[K_PROC].poll() is None

# --- 1. attach local video ----------------------------------------------

st.markdown("### 1. Attach a local stitched MP4")

default_dir = str(Path.home() / "Movies")

K_PICKED_PATH = "picked_video_path"
col_pick, col_path = st.columns([1, 4])
with col_pick:
    if st.button("📁 Browse…", disabled=is_running, help="Open a native file picker"):
        # tkinter file dialog — works because the app runs locally on macOS.
        # Must run in a fresh subprocess; importing tkinter inside Streamlit's
        # main thread can hang on macOS.
        import subprocess, sys
        script = (
            "import tkinter as tk; from tkinter import filedialog; "
            "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
            f"p = filedialog.askopenfilename(initialdir={default_dir!r}, "
            "title='Select stitched MP4', "
            "filetypes=[('MP4 video', '*.mp4 *.mov *.m4v'), ('All files', '*.*')]); "
            "print(p)"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=120,
            )
            picked = result.stdout.strip()
            if picked:
                st.session_state[K_PICKED_PATH] = picked
                st.rerun()
        except Exception as e:
            st.error(f"File picker failed: {e}")

with col_path:
    local_path_str = st.text_input(
        "Absolute path to stitched MP4 on this Mac",
        value=st.session_state.get(K_PICKED_PATH, ""),
        placeholder=f"{default_dir}/2026-05-31-vs-Spurs.mp4",
        help="Use the file Insta360 Studio exported.",
    )

if st.button("Attach video", disabled=not local_path_str or is_running):
    p = Path(local_path_str).expanduser()
    if not p.exists():
        st.error(f"File does not exist: {p}")
    elif not p.is_file():
        st.error(f"Not a file: {p}")
    else:
        try:
            firestore_io.set_video_url(game_id, str(p))
            st.success(f"✓ videoUrl set to file://{p.resolve()}")
            _list_games.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Firestore write failed: {e}")

# --- 1.5 1st-half kickoff offset ----------------------------------------

st.divider()
st.markdown("### 1.5 Mark 1st-half kickoff in the video")
st.caption(
    "Scrub the source file in QuickTime/VLC to the kickoff whistle and enter the timestamp "
    "(HH:MM:SS or seconds). Halftime + 2nd-half kickoff + final whistle positions are derived "
    "automatically from Firestore wallclock data."
)

current_offset = float(game.get("video_offset_h1_kickoff_s") or 0.0)
offset_str = st.text_input(
    "1st-half kickoff timestamp in the source video",
    value=_seconds_to_hms(current_offset) if current_offset else "",
    placeholder="00:01:23  or  83",
    help="Where in the recording the ref blew the 1st-half kickoff whistle.",
)

if st.button("Save kickoff offset", disabled=is_running or not offset_str.strip()):
    try:
        seconds = _parse_offset_str(offset_str)
        if seconds < 0:
            raise ValueError("Offset must be non-negative.")
        firestore_io.set_video_offset_h1_kickoff_s(game_id, seconds)
        st.success(f"\u2713 1st-half kickoff @ {_seconds_to_hms(seconds)} ({seconds:.1f}s)")
        _list_games.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Bad timestamp: {e}")

# Show derived boundaries so the user can sanity-check
if current_offset > 0:
    try:
        g = firestore_io.get_game(game_id)
        # Use a generous duration estimate (full video) - identity.half_windows clamps for us
        from post_game.identity import half_windows as _hw
        windows = _hw(g, video_duration_s=10 ** 9)
        st.info(
            f"Derived play windows \u2014 "
            f"1H: {_seconds_to_hms(windows[0][0])} \u2192 {_seconds_to_hms(windows[0][1])}  \u00b7  "
            f"2H: {_seconds_to_hms(windows[1][0])} \u2192 {_seconds_to_hms(windows[1][1])}"
        )
    except Exception as e:
        st.caption(f"(Could not preview windows: {e})")

# --- 2. calibrate -------------------------------------------------------

st.divider()
st.markdown("### 2. Calibrate the field (tap 4 corners)")
st.caption("Opens a browser tab with a 360° sphere viewer of the game video. Tap the 4 field corners → SAVE.")

if st.button("📐 Calibrate field", disabled=not current_video or is_running):
    _start_subprocess("calibrate", game_id, ["calibrate", "--game-id", game_id])
    st.rerun()

# --- 3. run pipeline ----------------------------------------------------

st.divider()
st.markdown("### 3. Run pipeline")

run_cols = st.columns([1, 1, 2])
tv_view = run_cols[0].checkbox("--tv-view (broadcast + auto-highlights)", value=False)
verbose = run_cols[1].checkbox("Verbose logs", value=False)

if not current_video:
    st.warning("Attach a video before running.")
if not game["has_calibration"]:
    st.warning("Game has no calibration — calibrate first (step 2) or the pipeline will fail.")

if st.button("▶︎ Run analytics", type="primary", disabled=not current_video or is_running):
    args = ["run", "--game-id", game_id]
    if tv_view:
        args.append("--tv-view")
    if verbose:
        args.append("--verbose")
    _start_subprocess("run", game_id, args)
    st.rerun()

# --- job status + log stream --------------------------------------------

st.divider()
st.markdown("### Job status")

just_finished = _drain_log_queue()
is_running = st.session_state[K_PROC] is not None and st.session_state[K_PROC].poll() is None

status_cols = st.columns([3, 1])
if is_running:
    kind = st.session_state[K_PROC_KIND]
    elapsed = int(time.time() - (st.session_state[K_STARTED_AT] or time.time()))
    status_cols[0].info(f"⏳ {kind} running for {elapsed}s · game `{st.session_state[K_PROC_GAME]}`")
    if status_cols[1].button("⏹ Stop"):
        _stop_running()
        st.rerun()
else:
    last = st.session_state[K_LOG][-1] if st.session_state[K_LOG] else ""
    status_cols[0].success(f"Idle. Last: {last[:120]}" if last else "Idle.")

if st.session_state[K_LOG]:
    log_text = "\n".join(st.session_state[K_LOG][-400:])
    st.code(log_text, language="log")

# Auto-refresh while a job runs.
if is_running:
    time.sleep(1.0)
    st.rerun()
elif just_finished:
    _list_games.clear()
    st.rerun()

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

# Silence the spammy "Please replace st.components.v1.html with st.iframe"
# deprecation warning. Our 1s auto-rerun loop re-renders components.html on
# every tick, so without this the terminal floods with hundreds of warnings.
# We attack it from THREE angles because Streamlit's deprecation pipeline has
# multiple emission paths (in-browser banner, stdlib logger, direct stderr).
import logging as _logging

class _DropComponentsHtmlDeprecation(_logging.Filter):
    def filter(self, record: _logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return "st.components.v1.html" not in msg

# 1. Filter the streamlit root logger + deprecation_util logger.
for _lname in ("streamlit", "streamlit.deprecation_util"):
    _logging.getLogger(_lname).addFilter(_DropComponentsHtmlDeprecation())

# 2. Monkey-patch the emitter as a fallback (some Streamlit versions skip
#    the logger entirely on certain code paths).
try:
    import streamlit.deprecation_util as _du
    _orig_show = _du.show_deprecation_warning
    def _quiet_show(message, *a, **kw):
        try:
            if "st.components.v1.html" in str(message):
                return
        except Exception:
            pass
        return _orig_show(message, *a, **kw)
    _du.show_deprecation_warning = _quiet_show
except Exception:
    pass

# 3. Wrap stderr to drop any line that mentions the deprecated API. Catches
#    direct `sys.stderr.write(...)` calls that bypass both logging and the
#    deprecation_util module.
import sys as _sys
class _StderrFilter:
    def __init__(self, wrapped):
        self._w = wrapped
        self._buf = ""
    def write(self, s):
        if not s:
            return 0
        self._buf += s
        out_lines = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if "st.components.v1.html" in line:
                continue
            out_lines.append(line + "\n")
        if out_lines:
            self._w.write("".join(out_lines))
        return len(s)
    def flush(self):
        if self._buf and "st.components.v1.html" not in self._buf:
            self._w.write(self._buf)
        self._buf = ""
        self._w.flush()
    def __getattr__(self, name):
        return getattr(self._w, name)

if not isinstance(_sys.stderr, _StderrFilter):
    _sys.stderr = _StderrFilter(_sys.stderr)

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


# --- inline video preview (range-capable local HTTP server) --------------
#
# Streamlit's st.video() loads the entire file into memory — unusable for a
# 67 GB source MP4. Instead we spin up a tiny localhost HTTP server in a
# background thread that serves a SINGLE file with HTTP Range support, and
# embed an HTML5 <video> tag pointing at it. The browser handles seeking.

import http.server
import socket
import socketserver
import urllib.parse


_PREVIEW_SERVER: dict | None = None  # module-level singleton


def _make_range_handler(file_path: Path):
    fp = str(file_path)
    file_size = file_path.stat().st_size
    # Best-effort content type
    suffix = file_path.suffix.lower()
    ctype = {".mp4": "video/mp4", ".mov": "video/quicktime",
             ".mkv": "video/x-matroska", ".webm": "video/webm"}.get(suffix, "application/octet-stream")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):  # silence
            pass

        def _serve(self, head_only: bool):
            rng = self.headers.get("Range")
            try:
                if rng and rng.startswith("bytes="):
                    start_s, _, end_s = rng[6:].partition("-")
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else file_size - 1
                    end = min(end, file_size - 1)
                    if start > end or start >= file_size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.end_headers()
                        return
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    if head_only:
                        return
                    with open(fp, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                return
                            remaining -= len(chunk)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    if head_only:
                        return
                    with open(fp, "rb") as f:
                        while True:
                            chunk = f.read(1024 * 1024)
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                return
            except Exception:
                pass

        def do_HEAD(self):
            self._serve(head_only=True)

        def do_GET(self):
            self._serve(head_only=False)

    return Handler


class _ThreadedTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _ensure_preview_server(file_path: Path) -> str:
    """Return a localhost URL serving file_path with Range support.

    Idempotent across Streamlit reruns — caches the server keyed by the path.
    """
    global _PREVIEW_SERVER
    fp = str(file_path.resolve())
    if _PREVIEW_SERVER and _PREVIEW_SERVER.get("path") == fp:
        return _PREVIEW_SERVER["url"]
    # Shut down any prior server (different file)
    if _PREVIEW_SERVER:
        try:
            _PREVIEW_SERVER["server"].shutdown()
            _PREVIEW_SERVER["server"].server_close()
        except Exception:
            pass

    # Pick a free port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    handler = _make_range_handler(file_path)
    server = _ThreadedTCPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # URL-encode the filename so spaces/unicode work
    encoded = urllib.parse.quote(file_path.name)
    url = f"http://127.0.0.1:{port}/{encoded}"
    _PREVIEW_SERVER = {"path": fp, "url": url, "server": server, "thread": t}
    return url


def _local_video_path(video_url: str) -> Path | None:
    """Convert a stored videoUrl (file:// or absolute path) to a local Path."""
    if not video_url:
        return None
    if video_url.startswith("file://"):
        video_url = urllib.parse.unquote(video_url[len("file://"):])
    p = Path(video_url)
    if p.exists() and p.is_file():
        return p
    return None


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
    "Scrub the inline player below (or QuickTime/VLC) to the kickoff whistle and enter the timestamp "
    "(HH:MM:SS or seconds). Halftime + 2nd-half kickoff + final whistle positions are derived "
    "automatically from Firestore wallclock data."
)

# Inline scrubbable preview backed by a local Range-capable HTTP server.
_preview_path = _local_video_path(current_video)
if _preview_path is not None:
    try:
        _preview_url = _ensure_preview_server(_preview_path)
        # Estimate auto-derived H2 to use as default seek for the player.
        _h1_for_seek = float(game.get("video_offset_h1_kickoff_s") or 0.0)
        _h2_auto_guess = max(
            _h1_for_seek + float(game.get("half_length_min") or 30) * 60 + 300.0,
            _h1_for_seek + 60.0,
        )
        _start_at = int(_h2_auto_guess) if _h1_for_seek > 0 else 0
        import streamlit.components.v1 as components

        components.html(
            f"""
            <div style="font-family: -apple-system, sans-serif; color:#ddd;">
              <video id="pgvid" controls preload="metadata" style="width:100%; max-height:360px; background:#000;"
                     src="{_preview_url}#t={_start_at}"></video>
              <div style="display:flex; gap:8px; align-items:center; margin-top:6px;">
                <button id="pgcopy" style="padding:6px 12px; background:#1f6feb; color:#fff;
                        border:none; border-radius:6px; cursor:pointer; font-weight:600;">
                  \u270e Copy current time
                </button>
                <span id="pgtime" style="font-family: ui-monospace, monospace; font-size: 13px;
                        background:#222; padding:4px 8px; border-radius:4px;">00:00:00.00</span>
                <span id="pgstatus" style="font-size:12px; color:#888;">paste into the field below</span>
              </div>
            </div>
            <script>
              const v = document.getElementById('pgvid');
              const t = document.getElementById('pgtime');
              const btn = document.getElementById('pgcopy');
              const s = document.getElementById('pgstatus');
              function fmt(sec) {{
                if (!isFinite(sec)) return '00:00:00.00';
                const h = Math.floor(sec/3600);
                const m = Math.floor((sec%3600)/60);
                const r = sec - h*3600 - m*60;
                return String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0')
                     + ':' + r.toFixed(2).padStart(5,'0');
              }}
              v.addEventListener('timeupdate', () => {{ t.textContent = fmt(v.currentTime); }});
              btn.addEventListener('click', async () => {{
                const txt = fmt(v.currentTime);
                try {{
                  await navigator.clipboard.writeText(txt);
                  s.textContent = '\u2713 copied ' + txt;
                  setTimeout(() => {{ s.textContent = 'paste into the field below'; }}, 2500);
                }} catch (e) {{ s.textContent = 'copy failed \u2014 select the text manually'; }}
              }});
            </script>
            """,
            height=440,
        )
    except Exception as _e:
        st.caption(f"(Preview unavailable: {_e})")
else:
    st.caption("(Attach a video above to enable the inline scrub preview.)")

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

# Optional 2nd-half kickoff override (when the "start 2nd half" button was
# pressed late, etc.). Leave blank / 0 to fall back to the wallclock-derived
# H2 start.
current_h2_offset = float(game.get("video_offset_h2_kickoff_s") or 0.0)
h2_offset_str = st.text_input(
    "2nd-half kickoff timestamp in the source video (optional override)",
    value=_seconds_to_hms(current_h2_offset) if current_h2_offset else "",
    placeholder="00:39:12  or  2352",
    help="Override when 'start 2nd half' was pressed late. Leave blank to auto-derive from halftime wallclock.",
)
if st.button("Save 2nd-half kickoff override", disabled=is_running):
    try:
        seconds = _parse_offset_str(h2_offset_str) if h2_offset_str.strip() else 0.0
        if seconds < 0:
            raise ValueError("Offset must be non-negative.")
        firestore_io.set_video_offset_h2_kickoff_s(game_id, seconds)
        if seconds > 0:
            st.success(f"\u2713 2nd-half kickoff override @ {_seconds_to_hms(seconds)} ({seconds:.1f}s)")
        else:
            st.success("\u2713 Cleared 2nd-half override (back to auto-derived)")
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
st.markdown("### 2. Calibrate the field (multi-point sphere fit)")
st.caption(
    "Opens a browser tab on `localhost:8766` with the calibration frame. Click "
    "13 reference landmarks (4 corners · halfway-line endpoints · field "
    "center · 4 goal posts · 2 goal mid-points) → SAVE. The fit solves "
    "for camera height, pitch and roll on top of the sphere ray-trace, so "
    "curved touchlines on the equirect are handled correctly."
)

# Show current calibration status (read fresh from Firestore — bypasses the
# cached game list so it reflects a just-saved calibration after rerun).
try:
    _existing_cal = firestore_io.get_game_calibration(game_id)
except Exception as e:
    _existing_cal = None
    st.caption(f"(Could not read calibration: {e})")

if _existing_cal:
    fw, fh = _existing_cal.video_frame_size
    _saved_ts = None
    _rms_w = None
    try:
        from post_game.firestore_io import _team_doc as _td
        _raw = (_td().collection("games").document(game_id).get().to_dict() or {}).get("calibration") or {}
        _ts_ms = _raw.get("created_at") or _raw.get("refined_at")
        if isinstance(_ts_ms, (int, float)) and _ts_ms > 0:
            from datetime import datetime
            _saved_ts = datetime.fromtimestamp(_ts_ms / 1000).strftime("%Y-%m-%d %H:%M")
        _gs = _raw.get("ground_similarity") or {}
        _rms_w = _gs.get("rms_weighted_m")
    except Exception:
        pass
    _when = f" · saved {_saved_ts}" if _saved_ts else ""
    _sph = _existing_cal.sphere
    if _sph:
        _rms_str = f"RMS {_sph['rms_m']:.2f} m"
        if isinstance(_rms_w, (int, float)) and _rms_w > 0:
            _rms_str += f" (weighted {float(_rms_w):.2f} m)"
        st.success(
            f"✓ Sphere calibration — cam_h={_sph['cam_h_m']:.2f} m, "
            f"pitch={_sph['pitch_deg']:+.2f}°, roll={_sph['roll_deg']:+.2f}°, "
            f"{_rms_str} on {fw}×{fh} frame"
            f" ({_existing_cal.length_m:g}×{_existing_cal.width_m:g} m field){_when}"
        )
    else:
        # Legacy planar homography (no sphere model). Pipeline still works
        # via the planar fallback but the math is less accurate — nudge to recalibrate.
        st.warning(
            f"⚠️ Legacy planar calibration on {fw}×{fh} frame"
            f" ({_existing_cal.length_m:g}×{_existing_cal.width_m:g} m field){_when}."
            " Re-calibrate to get the more accurate sphere model."
        )
else:
    st.info("No calibration yet. Click below to mark the reference landmarks.")

_cal_btn_label = "📐 Re-calibrate field" if _existing_cal else "📐 Calibrate field"
if st.button(_cal_btn_label, disabled=not current_video or is_running):
    _start_subprocess("calibrate", game_id, ["calibrate", "--game-id", game_id])
    st.rerun()

# --- 3. run pipeline ----------------------------------------------------

st.divider()
st.markdown("### 3. Run pipeline")

run_cols = st.columns([1, 1, 2])
tv_view = run_cols[0].checkbox("--tv-view (broadcast + auto-highlights)", value=False)
verbose = run_cols[1].checkbox("Verbose logs", value=False)

smoke_cols = st.columns([1, 1, 1, 1])
smoke_test = smoke_cols[0].checkbox(
    "Smoke test (2 min from middle of each half)",
    value=False,
    help="Runs the full pipeline on a tiny slice (~3-5 min wall time) so you can validate accuracy before a full-game run.",
)
debug_frames = smoke_cols[1].checkbox(
    "Save debug preview frames every 30s",
    value=False,
    help="Writes annotated JPGs to outputs/<game>/debug_frames/ during stage 2 so you can eyeball detection quality mid-run.",
)
skip_clips = smoke_cols[2].checkbox(
    "Skip per-event clip rendering",
    value=True,
    help="Default ON. The PWA seeks tv_reel.mp4 to any event via broadcast_events — per-event clips are only needed for downloadable montages (e.g. end-of-season highlight reels). Adds ~1GB/game to R2 when enabled.",
)
skip_upload = smoke_cols[3].checkbox(
    "Skip R2 upload",
    value=False,
    help="Keep all generated MP4s local under outputs/<game>/. Saves bandwidth + R2 cost during iteration.",
)

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
    if smoke_test:
        args += ["--max-play-s", "120"]
    if debug_frames:
        args += ["--debug-frames-every-s", "30"]
    if skip_clips:
        args.append("--skip-clips")
    if skip_upload:
        args.append("--skip-upload")
    _start_subprocess("run", game_id, args)
    st.rerun()

# --- delete analytics (re-run cleanly) ----------------------------------
#
# Wipes the analytics/ + clips/ subcollections and clears the public
# broadcast fields on the game doc so `has_analytics` flips back to False
# and the pipeline can be re-run from scratch. Source video, calibration,
# and the coach's events / score are untouched.

if game["has_analytics"]:
    st.markdown("#### Re-run from scratch")
    st.caption(
        "Deletes this game's analytics subcollection, per-event clip docs, and "
        "the public broadcast fields (videoHighlightsUrl / videoFullGameUrl / "
        "broadcastEvents / ...). The source video, calibration, and the coach's "
        "events are kept. After deleting, the game shows as 'unprocessed' and "
        "the ▶︎ Run analytics button works again."
    )
    K_DEL_CONFIRM = f"pg_del_confirm__{game_id}"
    if K_DEL_CONFIRM not in st.session_state:
        st.session_state[K_DEL_CONFIRM] = False

    del_cols = st.columns([1, 1, 2])
    keep_local = del_cols[0].checkbox(
        "Keep local outputs/",
        value=False,
        help="Off (default) also removes post_game/outputs/<game_id>/ on this Mac. "
             "On keeps the local artefacts (debug frames, raw tracks JSON, etc.) for inspection.",
    )

    if not st.session_state[K_DEL_CONFIRM]:
        if del_cols[1].button("🗑 Delete analytics", disabled=is_running):
            st.session_state[K_DEL_CONFIRM] = True
            st.rerun()
    else:
        st.warning(
            f"Really delete analytics for **{game['date']} vs {game['opponent']}** "
            f"(`{game_id}`)? This is reversible only by re-running the pipeline."
        )
        confirm_cols = st.columns([1, 1, 4])
        if confirm_cols[0].button("Yes, delete", type="primary"):
            try:
                summary = firestore_io.delete_analytics(game_id)
                import shutil
                from post_game import config as _pg_config
                out_dir = _pg_config.OUTPUTS_DIR / game_id
                local_removed = False
                if not keep_local and out_dir.exists():
                    shutil.rmtree(out_dir)
                    local_removed = True
                st.success(
                    f"Deleted: {summary['analytics_docs']} analytics doc(s), "
                    f"{summary['clip_docs']} clip doc(s), "
                    f"{summary['public_fields_cleared']} public field(s)"
                    + (f", local outputs/ removed" if local_removed else "")
                    + "."
                )
                st.session_state[K_DEL_CONFIRM] = False
                _list_games.clear()
                time.sleep(0.5)
                st.rerun()
            except Exception as e:
                st.session_state[K_DEL_CONFIRM] = False
                st.error(f"Delete failed: {e}")
        if confirm_cols[1].button("Cancel"):
            st.session_state[K_DEL_CONFIRM] = False
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

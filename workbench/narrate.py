"""Workbench page: post-game narration at the Mac.

The coach watches the local game video and narrates over it (coach decision
2026-08-24: narration happens HERE, not in the PWA — the phone carries at most
a few in-game notes). Two independent capture halves, joined by wall-clock:

  * the player component reports every play/pause/seek/rate tick with wall_ms
    and video_t (workbench/components/narrate_player/);
  * ffmpeg records the mic from t0_wall_ms.

That pair makes audio→video alignment exact under pauses and rewinds — no
kickoff anchors, no cross-correlation (tracking/narration_align.py). One
session = one directory under tracking/outputs/narration/<game>/ holding
audio.m4a + session.json; the Review page consumes it from there.
"""
from __future__ import annotations

import json
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

REPO = Path(__file__).resolve().parent.parent
COMPONENT_DIR = REPO / "workbench" / "components" / "narrate_player"
# The video is served by our own Range-capable sidecar (media_server.py): the
# component asset route buffers whole files in memory, and Streamlit's
# /app/static route 404s anything over 200 MB — the reels are ~86 GB.
MEDIA_ROOT = REPO / "workbench" / "media"
MEDIA_LINK = MEDIA_ROOT / "current.mp4"
NARR_ROOT = REPO / "tracking" / "outputs" / "narration"

_player = components.declare_component("narrate_player", path=str(COMPONENT_DIR))


@st.cache_resource
def _media_port() -> int:
    """One sidecar per Streamlit process; cache_resource makes it a singleton."""
    from workbench import media_server
    return media_server.serve(MEDIA_ROOT)


@st.cache_data(ttl=300)
def _mic_devices() -> list[tuple[int, str]]:
    """AVFoundation audio devices, parsed from ffmpeg's device listing."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True).stderr
    devs, in_audio = [], False
    for line in out.splitlines():
        if "audio devices" in line.lower():
            in_audio = True
            continue
        if in_audio:
            m = re.search(r"\[(\d+)\]\s+(.+)$", line)
            if m:
                devs.append((int(m.group(1)), m.group(2).strip()))
            elif "devices" in line.lower():
                break
    return devs


def _video_candidates(game_id: str) -> list[Path]:
    cands = []
    tv = REPO / "post_game" / "outputs" / game_id / "tv_view" / "tv_reel.mp4"
    if tv.exists():
        cands.append(tv)
    movies = Path.home() / "Movies" / "stompers"
    if movies.exists():
        cands += sorted(movies.glob("*.mp4"))
    return cands


def render() -> None:
    game_id = st.session_state.get("wb_game_id")
    if not game_id:
        st.error("Pick a game in the sidebar first.")
        return

    st.title("🎙 Narrate")
    st.caption("Play the video, talk over it. Pauses and rewinds are fine — "
               "every player action is logged and alignment uses the log.")

    # --- video selection ---------------------------------------------------
    cands = _video_candidates(game_id)
    labels = [str(p) for p in cands] + ["(type a path…)"]
    pick = st.selectbox("Video", labels, index=0 if cands else len(labels) - 1)
    path_str = st.text_input("Path", value="" if pick == "(type a path…)" else pick,
                             label_visibility="collapsed") if pick == "(type a path…)" else pick
    video_path = Path(path_str) if path_str else None

    if video_path and video_path.exists():
        # A HARDLINK into workbench/static: zero bytes copied (same APFS
        # volume in practice; falls back to a copy across volumes), and the
        # static route realpath()s to a path that is genuinely inside the
        # served dir — a symlink would resolve outside and be rejected. The
        # nonce (source path) tells the persistent iframe when to reload.
        MEDIA_LINK.parent.mkdir(parents=True, exist_ok=True)
        import os as _os
        same = MEDIA_LINK.exists() and _os.path.samefile(MEDIA_LINK, video_path)
        if not same:
            MEDIA_LINK.unlink(missing_ok=True)
            try:
                _os.link(video_path.resolve(), MEDIA_LINK)
            except OSError:
                import shutil
                shutil.copyfile(video_path, MEDIA_LINK)
        port = _media_port()
        val = _player(nonce=str(video_path),
                      video_url=f"http://localhost:{port}/current.mp4",
                      key="narr_player", default=None)
        if val and val.get("ticks"):
            st.session_state["narr_ticks"] = val["ticks"]
    else:
        st.info("Pick a video file to load the player.")

    st.divider()

    # --- recorder ------------------------------------------------------------
    devs = _mic_devices()
    if not devs:
        st.error("No AVFoundation audio devices found — is ffmpeg installed "
                 "and does the terminal have microphone permission?")
        return
    # AirPods first when present: the established recording convention.
    default_i = next((i for i, (_, n) in enumerate(devs) if "airpod" in n.lower()), 0)
    dev = st.selectbox("Microphone", devs, index=default_i,
                       format_func=lambda d: f"[{d[0]}] {d[1]}")

    proc = st.session_state.get("narr_proc")
    recording = proc is not None and proc.poll() is None

    c1, c2, c3 = st.columns([1, 1, 3])
    if not recording:
        if c1.button("⏺ Start recording", type="primary",
                     disabled=not (video_path and video_path.exists())):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sess_dir = NARR_ROOT / game_id / f"sess_{ts}"
            sess_dir.mkdir(parents=True, exist_ok=True)
            audio = sess_dir / "audio.m4a"
            t0 = time.time() * 1000.0
            p = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                 "-f", "avfoundation", "-i", f":{dev[0]}",
                 "-ac", "1", "-ar", "48000", "-c:a", "aac", "-b:a", "96k",
                 "-y", str(audio)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            st.session_state.update(narr_proc=p, narr_t0=t0,
                                    narr_dir=str(sess_dir),
                                    narr_video=str(video_path))
            st.rerun()
    else:
        elapsed = (time.time() * 1000 - st.session_state["narr_t0"]) / 1000
        c1.error(f"⏺ REC {int(elapsed // 60)}:{int(elapsed % 60):02d}")
        if c2.button("⏹ Stop & save", type="primary"):
            proc.send_signal(signal.SIGINT)   # ffmpeg finalizes the m4a on INT
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            sess_dir = Path(st.session_state["narr_dir"])
            sess = {
                "game_id": game_id,
                "audio": str(sess_dir / "audio.m4a"),
                "t0_wall_ms": st.session_state["narr_t0"],
                "t_stop_wall_ms": time.time() * 1000.0,
                "video_path": st.session_state["narr_video"],
                "mic": dev[1],
                "ticks": st.session_state.get("narr_ticks") or [],
            }
            (sess_dir / "session.json").write_text(json.dumps(sess, indent=2))
            st.session_state["narr_proc"] = None
            st.success(f"Saved {sess_dir.name} "
                       f"({len(sess['ticks'])} timeline ticks). "
                       "Process it on the Review page.")
        c3.caption("Talk naturally; say first names. Shouting at the pitch is "
                   "fine — the cleaner removes instructions by content.")

    if not recording and st.session_state.get("narr_ticks"):
        st.caption(f"{len(st.session_state['narr_ticks'])} timeline ticks buffered "
                   "from the player.")

    # --- existing sessions ----------------------------------------------------
    sess_root = NARR_ROOT / game_id
    if sess_root.exists():
        rows = []
        for d in sorted(sess_root.iterdir()):
            sj = d / "session.json"
            if sj.exists():
                s = json.loads(sj.read_text())
                dur = (s.get("t_stop_wall_ms", 0) - s["t0_wall_ms"]) / 60000
                rows.append(f"**{d.name}** — {dur:.1f} min, "
                            f"{len(s.get('ticks') or [])} ticks, mic: {s.get('mic', '?')}")
        if rows:
            st.divider()
            st.markdown("**Sessions for this game**")
            for r in rows:
                st.markdown("- " + r)

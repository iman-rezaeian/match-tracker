"""Workbench page: post-game narration at the Mac.

The coach watches the local game video and narrates over it (coach decision
2026-08-24: narration happens HERE, not in the PWA — the phone carries at most
a few in-game notes). Two independent capture halves, joined by wall-clock:

  * the player component reports every play/pause/seek/rate tick with wall_ms
    and video_t (workbench/components/narrate_player/);
  * ffmpeg records the mic in SEGMENTS that follow the video: play starts a
    segment, pause/end closes it (coach decision 2026-08-26: ONE control —
    the video's own play/pause — drives both, so rec and video stay in sync).

Stop & save concatenates the segments into audio.m4a and writes session.json
with per-segment wall-clock anchors; narration_align maps concatenated audio
time through the segment table to wall time, then to video time via the tick
log. One session = one directory under tracking/outputs/narration/<game>/;
the Review page consumes it from there.

Recording robustness (both learned from the 2026-08-26 silent-failure take,
where a stale device INDEX pointed at the wrong mic and ffmpeg waited 3.5 min
for frames that never came, writing nothing):
  * the mic is re-resolved BY NAME from a fresh device listing when the coach
    arms — AVFoundation indices reshuffle whenever a device (dis)connects;
  * a health check on every rerun kills a segment whose ffmpeg died or has
    produced no audio bytes, and surfaces its stderr instead of showing REC.
"""
from __future__ import annotations

import json
import os
import re
import shutil
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

# A segment must be producing bytes by this age or it is declared dead. 96 kbps
# AAC writes ~12 KB/s; reruns arrive on the 8 s player heartbeat, so the first
# check lands at ~8 s with ~90 KB expected — 4 KB is a generous floor.
HEALTH_AGE_S = 6.0
HEALTH_MIN_BYTES = 4096

_player = components.declare_component("narrate_player", path=str(COMPONENT_DIR))


@st.cache_resource
def _media_port() -> int:
    """One sidecar per Streamlit process; cache_resource makes it a singleton."""
    from workbench import media_server
    return media_server.serve(MEDIA_ROOT)


def _list_mics() -> list[tuple[int, str]]:
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


@st.cache_data(ttl=300)
def _mic_devices() -> list[tuple[int, str]]:
    return _list_mics()


def _resolve_mic_index(name: str) -> int | None:
    """Fresh listing → index for a device NAME (indices shift; names don't)."""
    for i, n in _list_mics():
        if n == name:
            return i
    return None


def _video_candidates(game_id: str) -> list[Path]:
    cands = []
    tv = REPO / "post_game" / "outputs" / game_id / "tv_view" / "tv_reel.mp4"
    if tv.exists():
        cands.append(tv)
    movies = Path.home() / "Movies" / "stompers"
    if movies.exists():
        cands += sorted(movies.glob("*.mp4"))
    return cands


def _ffprobe_dur_s(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _stderr_tail(path: str, n: int = 12) -> str:
    try:
        return "\n".join(Path(path).read_text().strip().splitlines()[-n:])
    except OSError:
        return "(no stderr captured)"


# --- segment lifecycle -------------------------------------------------------
# One open segment at a time, held in session_state["narr_seg"]; closed ones
# accumulate in session_state["narr_segments"].

def _start_segment() -> None:
    if st.session_state.get("narr_seg"):
        return
    n = st.session_state.get("narr_seg_n", 0) + 1
    st.session_state["narr_seg_n"] = n
    sess_dir = Path(st.session_state["narr_dir"])
    audio = sess_dir / f"seg_{n:03d}.m4a"
    errf = sess_dir / f"seg_{n:03d}.stderr.txt"
    idx = st.session_state["narr_mic_idx"]
    t0 = time.time() * 1000.0
    p = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "warning",
         "-f", "avfoundation", "-i", f":{idx}",
         "-ac", "1", "-ar", "48000", "-c:a", "aac", "-b:a", "96k",
         "-y", str(audio)],
        stdout=subprocess.DEVNULL, stderr=open(errf, "w"))
    st.session_state["narr_seg"] = {
        "proc": p, "file": str(audio), "stderr": str(errf), "t0": t0}


def _close_segment() -> None:
    """Stop the open segment's ffmpeg; keep it if it produced audio."""
    seg = st.session_state.pop("narr_seg", None)
    if not seg:
        return
    p = seg["proc"]
    if p.poll() is None:
        p.send_signal(signal.SIGINT)   # ffmpeg finalizes the m4a on INT
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
    f = Path(seg["file"])
    if f.exists() and f.stat().st_size > 0:
        st.session_state.setdefault("narr_segments", []).append(
            {"file": seg["file"], "t0_wall_ms": seg["t0"],
             "t_stop_wall_ms": time.time() * 1000.0})
    else:
        st.session_state["narr_err"] = (
            "Recording segment produced no audio. ffmpeg said:\n"
            + _stderr_tail(seg["stderr"]))


def _check_segment_health() -> None:
    """Fail fast: a dead or byte-less ffmpeg must not keep showing REC."""
    seg = st.session_state.get("narr_seg")
    if not seg:
        return
    age_s = (time.time() * 1000.0 - seg["t0"]) / 1000.0
    died = seg["proc"].poll() is not None
    f = Path(seg["file"])
    silent = age_s > HEALTH_AGE_S and (
        not f.exists() or f.stat().st_size < HEALTH_MIN_BYTES)
    if died or silent:
        why = "ffmpeg exited" if died else f"no audio after {age_s:.0f}s"
        seg["proc"].kill()
        st.session_state.pop("narr_seg", None)
        f.unlink(missing_ok=True)
        st.session_state["narr_armed"] = False
        st.session_state["narr_err"] = (
            f"Recording failed ({why}) — check the mic and re-arm. "
            "ffmpeg said:\n" + _stderr_tail(seg["stderr"]))


def _drive_from_ticks(ticks: list[dict]) -> None:
    """The video's play/pause IS the record button: open/close segments to
    match the ticks that arrived since the last rerun."""
    i = st.session_state.get("narr_tick_i", 0)
    if len(ticks) < i:          # component reset its log (video changed)
        _close_segment()
        i = 0
    for t in ticks[i:]:
        k = t.get("k")
        if k == "play":
            _start_segment()
        elif k in ("pause", "end"):
            _close_segment()
    st.session_state["narr_tick_i"] = len(ticks)


def _is_playing(ticks: list[dict]) -> bool:
    playing = False
    for t in ticks:
        if t.get("k") in ("play", "hb"):
            playing = True
        elif t.get("k") in ("pause", "end"):
            playing = False
    return playing


def _finalize_session(game_id: str) -> str | None:
    """Concat segments → audio.m4a, write session.json. Returns error or None."""
    _close_segment()
    sess_dir = Path(st.session_state["narr_dir"])
    segs = st.session_state.get("narr_segments") or []
    if not segs:
        return "No audio was captured in this session — nothing to save."

    virtual = 0.0
    seg_meta = []
    for s in segs:
        # virtual_start uses MEASURED audio durations so it matches the
        # concatenated file's own timeline (wall duration drifts slightly).
        d = _ffprobe_dur_s(s["file"])
        seg_meta.append({**s, "dur_s": d, "virtual_start_s": virtual})
        virtual += d

    audio = sess_dir / "audio.m4a"
    if len(segs) == 1:
        try:
            os.link(segs[0]["file"], audio)
        except OSError:
            shutil.copyfile(segs[0]["file"], audio)
    else:
        lst = sess_dir / "concat.txt"
        lst.write_text("".join(f"file '{s['file']}'\n" for s in segs))
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-y", str(audio)],
            capture_output=True, text=True)
        if r.returncode != 0 or not audio.exists():
            return "Concat failed: " + (r.stderr or "?")[-500:]

    sess = {
        "game_id": game_id,
        "audio": str(audio),
        "t0_wall_ms": segs[0]["t0_wall_ms"],
        "t_stop_wall_ms": time.time() * 1000.0,
        "video_path": st.session_state["narr_video"],
        "mic": st.session_state["narr_mic_name"],
        "segments": seg_meta,
        "ticks": st.session_state.get("narr_ticks") or [],
    }
    (sess_dir / "session.json").write_text(json.dumps(sess, indent=2))
    return None


def _reset_session_state() -> None:
    for k in ("narr_armed", "narr_dir", "narr_video", "narr_mic_name",
              "narr_mic_idx", "narr_seg_n", "narr_segments", "narr_err"):
        st.session_state.pop(k, None)


def render() -> None:
    game_id = st.session_state.get("wb_game_id")
    if not game_id:
        st.error("Pick a game in the sidebar first.")
        return

    st.title("🎙 Narrate")
    st.caption("Arm once, then the video's play/pause is the ONE control: "
               "play records, pause pauses the mic too. Rewinds are fine — "
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
        same = MEDIA_LINK.exists() and os.path.samefile(MEDIA_LINK, video_path)
        if not same:
            MEDIA_LINK.unlink(missing_ok=True)
            try:
                os.link(video_path.resolve(), MEDIA_LINK)
            except OSError:
                shutil.copyfile(video_path, MEDIA_LINK)
        port = _media_port()
        val = _player(nonce=str(video_path),
                      video_url=f"http://localhost:{port}/current.mp4",
                      key="narr_player", default=None)
        if isinstance(val, dict) and "ticks" in val:
            st.session_state["narr_ticks"] = val["ticks"]
    else:
        st.info("Pick a video file to load the player.")

    ticks = st.session_state.get("narr_ticks") or []
    if st.session_state.get("narr_armed"):
        _drive_from_ticks(ticks)
        _check_segment_health()

    st.divider()

    # --- recorder ------------------------------------------------------------
    devs = _mic_devices()
    if not devs:
        st.error("No AVFoundation audio devices found — is ffmpeg installed "
                 "and does the terminal have microphone permission?")
        return

    armed = st.session_state.get("narr_armed", False)
    seg = st.session_state.get("narr_seg")
    closed = st.session_state.get("narr_segments") or []

    if err := st.session_state.get("narr_err"):
        st.error(err)

    # AirPods first when present: the established recording convention.
    default_i = next((i for i, (_, n) in enumerate(devs) if "airpod" in n.lower()), 0)
    dev = st.selectbox("Microphone", devs, index=default_i,
                       format_func=lambda d: f"[{d[0]}] {d[1]}",
                       disabled=armed)

    c1, c2, c3 = st.columns([1, 1, 3])
    if not armed:
        if c1.button("⏺ Arm recording", type="primary",
                     disabled=not (video_path and video_path.exists())):
            idx = _resolve_mic_index(dev[1])   # fresh listing, by NAME
            if idx is None:
                st.error(f"Mic “{dev[1]}” is no longer connected — "
                         "refresh the device list.")
                _mic_devices.clear()
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                sess_dir = NARR_ROOT / game_id / f"sess_{ts}"
                sess_dir.mkdir(parents=True, exist_ok=True)
                st.session_state.update(
                    narr_armed=True, narr_dir=str(sess_dir),
                    narr_video=str(video_path),
                    narr_mic_name=dev[1], narr_mic_idx=idx,
                    narr_seg_n=0, narr_segments=[], narr_err=None,
                    narr_tick_i=len(ticks))
                if _is_playing(ticks):
                    _start_segment()
                st.rerun()
        # A failed mid-session disarm can leave good segments behind — still
        # let the coach save what was captured.
        if closed and st.session_state.get("narr_dir"):
            if c2.button("⏹ Save captured audio"):
                e = _finalize_session(game_id)
                if e:
                    st.error(e)
                else:
                    st.success("Saved partial session — process it on the "
                               "Review page.")
                    _reset_session_state()
    else:
        rec_s = sum((s["t_stop_wall_ms"] - s["t0_wall_ms"]) / 1000.0
                    for s in closed)
        if seg:
            rec_s += (time.time() * 1000.0 - seg["t0"]) / 1000.0
            c1.error(f"⏺ REC {int(rec_s // 60)}:{int(rec_s % 60):02d} "
                     f"· seg {st.session_state.get('narr_seg_n', 0)}")
        else:
            c1.info(f"⏸ armed · {int(rec_s // 60)}:{int(rec_s % 60):02d} "
                    "recorded — press ▶ on the video")
        if c2.button("⏹ Stop & save", type="primary"):
            e = _finalize_session(game_id)
            if e:
                st.error(e)
                st.session_state["narr_armed"] = False
            else:
                n_seg = len(st.session_state.get("narr_segments") or [])
                st.success(f"Saved {Path(st.session_state['narr_dir']).name} "
                           f"({n_seg} audio segment(s), "
                           f"{len(ticks)} timeline ticks). "
                           "Process it on the Review page.")
                _reset_session_state()
        c3.caption("Talk naturally; say first names. Shouting at the pitch is "
                   "fine — the cleaner removes instructions by content. The "
                   "video's play/pause also starts/stops the mic.")

    # --- existing sessions ----------------------------------------------------
    sess_root = NARR_ROOT / game_id
    if sess_root.exists():
        rows = []
        for d in sorted(sess_root.iterdir()):
            sj = d / "session.json"
            if sj.exists():
                s = json.loads(sj.read_text())
                dur = (s.get("t_stop_wall_ms", 0) - s["t0_wall_ms"]) / 60000
                no_audio = "" if Path(s.get("audio", "")).exists() \
                    else " · **⚠ no audio file**"
                rows.append(f"**{d.name}** — {dur:.1f} min, "
                            f"{len(s.get('segments') or []) or 1} segment(s), "
                            f"{len(s.get('ticks') or [])} ticks, "
                            f"mic: {s.get('mic', '?')}{no_audio}")
        if rows:
            st.divider()
            st.markdown("**Sessions for this game**")
            for r in rows:
                st.markdown("- " + r)

    # --- this lane's publish state (voice events land via Review accepts) ----
    st.divider()
    from workbench import pubstatus
    pubstatus.voice_lane(game_id)

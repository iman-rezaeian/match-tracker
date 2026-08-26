"""Workbench page: process a narration session and confirm its events.

Pipeline (each step a subprocess of the existing CLI, skipped when its output
already exists): transcribe (voice_probe/mlx-whisper) → clean (voice_clean,
content-based shout removal) → extract (voice_extract, draft events) → align
(narration_align, audio→video via the session tick log) → union (voice_union,
dedupe vs the live tap log). The confirm queue then writes accepted events
straight to the game doc (coach decision 2026-08-24: post-game review happens
at the Mac, not in the PWA queue) via firestore_io.append_confirmed_event,
which mirrors the PWA's confirmVoiceDraft write exactly.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parent.parent
NARR_ROOT = REPO / "tracking" / "outputs" / "narration"
PROBE_DIR = REPO / "tracking" / "outputs" / "voice_probe"
CLEAN_DIR = REPO / "tracking" / "outputs" / "voice_clean"
PY = sys.executable


@st.cache_data(ttl=60)
def _game_raw(game_id: str) -> dict:
    from post_game import firestore_io
    return (firestore_io._team_doc().collection("games")
            .document(game_id).get().to_dict() or {})


@st.cache_data(ttl=300)
def _roster() -> list[dict]:
    from post_game import firestore_io
    return [{"id": p.id, "num": getattr(p, "jersey_number", None), "name": p.name}
            for p in firestore_io.get_roster()]


def _run_step(label: str, cmd: list[str], out_file: Path, force: bool) -> bool:
    """Run one CLI step unless its output exists. Returns success."""
    if out_file.exists() and not force:
        st.success(f"{label}: ✓ (cached: `{out_file.name}`)")
        return True
    with st.status(f"{label} …", expanded=False) as status:
        st.code(" ".join(cmd), language="bash")
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-14:])
        st.text(tail)
        ok = r.returncode == 0 and out_file.exists()
        status.update(state="complete" if ok else "error",
                      label=f"{label}: {'done' if ok else 'FAILED'}")
    return ok


def _decisions_path(sess_dir: Path) -> Path:
    return sess_dir / "decisions.json"


def _load_decisions(sess_dir: Path) -> dict:
    p = _decisions_path(sess_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_decisions(sess_dir: Path, d: dict) -> None:
    _decisions_path(sess_dir).write_text(json.dumps(d, indent=2))


def render() -> None:
    game_id = st.session_state.get("wb_game_id")
    if not game_id:
        st.error("Pick a game in the sidebar first.")
        return

    st.title("✅ Review & confirm")

    sess_root = NARR_ROOT / game_id
    sessions = sorted([d for d in sess_root.iterdir() if (d / "session.json").exists()]) \
        if sess_root.exists() else []
    if not sessions:
        st.info("No narration sessions for this game yet — record one on the "
                "Narrate page.")
        return
    sess_dir = st.selectbox("Session", sessions, index=len(sessions) - 1,
                            format_func=lambda p: p.name)
    sess = json.loads((sess_dir / "session.json").read_text())
    audio = Path(sess["audio"])
    label = f"{game_id}_{sess_dir.name}"

    game = _game_raw(game_id)
    h1 = float(game.get("videoOffsetH1KickoffS")
               or game.get("video_offset_h1_kickoff_s") or 0.0)
    h2 = float(game.get("videoOffsetH2KickoffS")
               or game.get("video_offset_h2_kickoff_s") or 0.0)
    if not h1 or not h2:
        st.warning("Kickoff offsets missing on the game doc — align/union will "
                   "be wrong. Confirm both kickoffs in calibration first.")

    force = st.checkbox("Re-run steps even when cached", value=False)

    probe_out = PROBE_DIR / f"{label}.json"
    annot_out = CLEAN_DIR / f"{label}.annotated.json"
    events_out = CLEAN_DIR / f"{label}.events.json"
    aligned_out = CLEAN_DIR / f"{label}.events.aligned.json"
    union_out = CLEAN_DIR / f"{label}.events.aligned.union.json"

    if st.button("▶ Process narration", type="primary"):
        ok = _run_step("1/5 Transcribe (whisper)",
                       [PY, "-m", "tracking.voice_probe", "--audio", str(audio),
                        "--label", label, "--model", "small"],
                       probe_out, force)
        ok = ok and _run_step("2/5 Clean (drop instructions/shouts)",
                              [PY, "-m", "tracking.voice_clean", "--audio", str(audio),
                               "--segments", str(probe_out), "--label", label],
                              annot_out, force)
        ok = ok and _run_step("3/5 Extract draft events",
                              [PY, "-m", "tracking.voice_extract",
                               "--annotated", str(annot_out), "--label", label,
                               "--game-id", game_id],
                              events_out, force)
        ok = ok and _run_step("4/5 Align to the game clock",
                              [PY, "-m", "tracking.narration_align",
                               "--events", str(events_out),
                               "--session", str(sess_dir / "session.json"),
                               "--h1-off", str(h1)],
                              aligned_out, force)
        # Pre-shifted timeline (see narration_align): boundary 1 = H2 kickoff
        # minus H1 kickoff, boundary 2 = "rest of the video".
        ok = ok and _run_step("5/5 Union vs the live log",
                              [PY, "-m", "tracking.voice_union",
                               "--events", str(aligned_out), "--game-id", game_id,
                               "--source", "post",
                               "--boundaries", f"{max(1.0, h2 - h1)},999999"],
                              union_out, force)
        if ok:
            st.toast("Narration processed — queue below.")

    if not union_out.exists():
        st.caption("Run the pipeline to populate the confirm queue.")
        return

    # --- confirm queue -------------------------------------------------------
    st.divider()
    union = json.loads(union_out.read_text())
    decisions = _load_decisions(sess_dir)
    roster = _roster()
    r_ids = [r["id"] for r in roster]
    r_label = {r["id"]: f"#{r['num'] or '?'} {r['name']}" for r in roster}

    def _row_key(d: dict) -> str:
        return f"{d['type']}_{d['period']}_{d['elapsed']}_{d.get('player_id') or d.get('player_first_name') or 'na'}"

    def _default_pid(d: dict) -> str | None:
        if d.get("player_id") in r_ids:
            return d["player_id"]
        first = (d.get("player_first_name") or "").strip().lower()
        if first:
            hits = [r for r in roster if r["name"].lower().startswith(first)]
            if len(hits) == 1:
                return hits[0]["id"]
        return None

    new_drafts = union.get("new_drafts", [])
    enrich = union.get("enrichments", [])
    done = sum(1 for d in new_drafts if decisions.get(_row_key(d)))
    st.subheader(f"New events from voice ({done}/{len(new_drafts)} decided)")
    st.caption("Voice adds what you never tapped. Accept writes a real event "
               "(source = voice-confirmed) to the game doc — GOALs move the "
               "score, exactly like confirming in the app.")

    from post_game import firestore_io
    for d in sorted(new_drafts, key=lambda d: (d["period"], d["elapsed"])):
        key = _row_key(d)
        state = decisions.get(key, {}).get("status")
        c0, c1, c2, c3 = st.columns([4, 2, 1, 1])
        mm, ss = divmod(int(d["elapsed"]), 60)
        c0.markdown(f"**P{d['period']} {mm}:{ss:02d} · {d['type']}** "
                    f"(conf {d.get('confidence')})  \n_{d.get('quote') or ''}_")
        if state:
            c1.success("accepted" if state == "accepted" else "dismissed")
            continue
        pid = c1.selectbox("player", [None] + r_ids, key=f"pid_{key}",
                           index=([None] + r_ids).index(_default_pid(d)),
                           format_func=lambda p: r_label.get(p, "— no player"),
                           label_visibility="collapsed")
        if c2.button("✓", key=f"acc_{key}", help="Accept → write event"):
            period, elapsed = int(d["period"]), int(d["elapsed"])
            ev_type = d["type"]
            event = {
                "id": f"v_{period}_{elapsed}_{ev_type}_{pid or 'na'}",
                "type": ev_type, "playerId": pid, "period": period,
                "elapsed": elapsed,
                "at": firestore_io.voice_event_at_ms(game, period, elapsed),
                "source": "voice-confirmed",
                **({"voiceQuote": d["quote"]} if d.get("quote") else {}),
            }
            delta = "us" if ev_type == "GOAL" else (
                "opp" if ev_type == "OPP_GOAL" else None)
            firestore_io.append_confirmed_event(game_id, event, delta)
            decisions[key] = {"status": "accepted", "event_id": event["id"]}
            _save_decisions(sess_dir, decisions)
            _game_raw.clear()
            st.rerun()
        if c3.button("✕", key=f"dis_{key}", help="Dismiss"):
            decisions[key] = {"status": "dismissed"}
            _save_decisions(sess_dir, decisions)
            st.rerun()

    if enrich:
        st.subheader(f"Player fills for live events ({len(enrich)})")
        st.caption("Voice named the player on an event you tapped without one. "
                   "v1 shows them for reference — apply in the app's event "
                   "editor (updating an existing event is a whole-doc write).")
        for d in enrich:
            mm, ss = divmod(int(d["elapsed"]), 60)
            st.markdown(f"- P{d['period']} {mm}:{ss:02d} **{d['type']}** → "
                        f"{d.get('player_first_name') or d.get('player_id')} "
                        f"(live event at {d.get('enriches_live_at')}s) — "
                        f"_{d.get('quote') or ''}_")

    dups = union.get("dups", [])
    if dups:
        st.caption(f"{len(dups)} narrated events matched something you already "
                   "tapped and were dropped as duplicates.")

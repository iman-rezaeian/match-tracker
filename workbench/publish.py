"""Workbench page: what has landed on the game doc, and the publish actions.

Read-mostly: status tiles for the pipeline gates plus the two publishable
products this page can push — click position stats (click_publish) and a view
of voice-confirmed events. Heavy publishing (clips/reels/analytics) stays in
the pipeline itself (Game page)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


@st.cache_data(ttl=30)
def _game_raw(game_id: str) -> dict:
    from post_game import firestore_io
    return (firestore_io._team_doc().collection("games")
            .document(game_id).get().to_dict() or {})


def render() -> None:
    game_id = st.session_state.get("wb_game_id")
    if not game_id:
        st.error("Pick a game in the sidebar first.")
        return

    st.title("📤 Publish & status")
    g = _game_raw(game_id)
    if not g:
        st.error("Game doc not found.")
        return

    c = st.columns(4)
    c[0].metric("Video", "✓" if g.get("videoUrl") else "—")
    kicks = bool(g.get("videoOffsetH1Confirmed") and g.get("videoOffsetH2Confirmed"))
    c[1].metric("Kickoffs", "✓" if kicks else "—")
    c[2].metric("Events", len(g.get("events") or []))
    voice_n = sum(1 for e in (g.get("events") or [])
                  if (e.get("source") == "voice-confirmed"))
    c[3].metric("Voice-confirmed", voice_n)

    st.divider()
    st.subheader("Click position stats")
    clicks_file = REPO / "tracking" / "outputs" / "click_samples" / game_id / "clicks.jsonl"
    n_clicks = sum(1 for l in clicks_file.read_text().splitlines() if l.strip()) \
        if clicks_file.exists() else 0
    has_click_stats = bool(g.get("click_stats") or g.get("clickStats"))
    st.markdown(f"- clicks on disk: **{n_clicks}** · published to doc: "
                f"**{'yes' if has_click_stats else 'no'}**")
    col1, col2 = st.columns([1, 1])
    if col1.button("Preview (dry run)", disabled=not n_clicks):
        r = subprocess.run([PY, "-m", "tracking.click_publish",
                            "--game-id", game_id, "--dry-run"],
                           capture_output=True, text=True, cwd=REPO)
        st.code((r.stdout + r.stderr)[-3000:])
    if col2.button("Publish click stats →", type="primary", disabled=not n_clicks):
        r = subprocess.run([PY, "-m", "tracking.click_publish",
                            "--game-id", game_id],
                           capture_output=True, text=True, cwd=REPO)
        st.code((r.stdout + r.stderr)[-3000:])
        _game_raw.clear()

    st.divider()
    st.subheader("Voice-confirmed events on the doc")
    voice_evs = [e for e in (g.get("events") or [])
                 if e.get("source") == "voice-confirmed"]
    if not voice_evs:
        st.caption("None yet — accept drafts on the Review page.")
    for e in sorted(voice_evs, key=lambda e: (e.get("period", 0), e.get("elapsed", 0))):
        mm, ss = divmod(int(e.get("elapsed") or 0), 60)
        st.markdown(f"- P{e.get('period')} {mm}:{ss:02d} **{e.get('type')}** "
                    f"{e.get('playerId') or ''} — _{e.get('voiceQuote') or ''}_")

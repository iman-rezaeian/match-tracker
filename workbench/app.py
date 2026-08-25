"""Stompers Match Workbench — the one Mac app for analyzing a game.

One launcher, one port, five pages that used to be scattered tools:

  🏟 Game       — pick/attach video, calibrate, run the pipeline (post_game/ui_app)
  🖱 Clicks     — click-sampling for per-player position stats (tracking/click_sample_app)
  🎙 Narrate    — post-game narration over the local video (new)
  ✅ Review     — voice pipeline + Mac confirm queue (new)
  📤 Publish    — doc status, click-stats publish, confirmed events (new)

The PWA stays the product (live taps, family views); this is the workbench
(everything that needs the Mac's video files and compute). Coach decision
2026-08-24: post-game narration and its review happen HERE, not in the PWA.

The two pre-existing Streamlit apps run unmodified via runpy — each was
written as a standalone script, and wrapping beats rewriting: they keep
working standalone AND as pages. st.set_page_config is patched to a no-op
inside page runs (the root sets it once; pages calling it again would warn).

Launch:  ./run_workbench.sh
"""
from __future__ import annotations

import os

# Must be set before any cv2/PyObjC import (ui_app forks subprocesses; see the
# matching comment at the top of post_game/ui_app.py).
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import runpy
import sys
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

st.set_page_config(page_title="Stompers Workbench", page_icon="⚽", layout="wide")


@st.cache_data(ttl=60)
def _games(limit: int = 20) -> list[dict]:
    from post_game import firestore_io
    return firestore_io.list_recent_games_snapshots(limit=limit)


def _run_script(rel_path: str) -> None:
    """Run a standalone Streamlit script as a page, config call neutralized."""
    orig = st.set_page_config
    st.set_page_config = lambda *a, **k: None
    try:
        runpy.run_path(str(REPO / rel_path), run_name="__main__")
    finally:
        st.set_page_config = orig


def page_game() -> None:
    _run_script("post_game/ui_app.py")


def page_clicks() -> None:
    _run_script("tracking/click_sample_app.py")


def page_narrate() -> None:
    from workbench import narrate
    narrate.render()


def page_review() -> None:
    from workbench import review
    review.render()


def page_publish() -> None:
    from workbench import publish
    publish.render()


# --- shared sidebar: ONE game selection for Clicks/Narrate/Review/Publish ---
# (the Game page keeps its own richer picker — it existed first and also
# drives attach/calibrate; this selector is the cross-page context.)
with st.sidebar:
    st.markdown("### ⚽ Workbench")
    try:
        rows = _games()
    except Exception as e:
        rows = []
        st.error(f"Firestore unavailable: {type(e).__name__}")
    if rows:
        ids = [r["id"] for r in rows]
        labels = {r["id"]: f"{r.get('date') or '—'} vs {r.get('opponent') or '—'}"
                  for r in rows}
        cur = st.session_state.get("wb_game_id")
        idx = ids.index(cur) if cur in ids else 0
        st.session_state["wb_game_id"] = st.selectbox(
            "Game", ids, index=idx, format_func=lambda i: labels.get(i, i))
    st.divider()

nav = st.navigation([
    st.Page(page_game, title="Game", icon="🏟", default=True),
    st.Page(page_clicks, title="Clicks", icon="🖱"),
    st.Page(page_narrate, title="Narrate", icon="🎙"),
    st.Page(page_review, title="Review", icon="✅"),
    st.Page(page_publish, title="Publish", icon="📤"),
])
nav.run()

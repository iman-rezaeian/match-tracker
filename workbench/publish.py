"""Workbench page: the cross-check that all three lanes got published.

Coach decision 2026-08-26: each lane tab (Game / Clicks / Narrate) carries its
own publish action; THIS page renders the same three panels (workbench/
pubstatus.py) as the final checklist — green across the board means the game
doc has everything."""
from __future__ import annotations

import streamlit as st

from workbench import pubstatus


def render() -> None:
    game_id = st.session_state.get("wb_game_id")
    if not game_id:
        st.error("Pick a game in the sidebar first.")
        return

    st.title("📤 Publish — all three lanes")
    if not pubstatus._game_raw(game_id):
        st.error("Game doc not found.")
        return

    ok_game = pubstatus.game_lane(game_id)
    st.divider()
    ok_clicks = pubstatus.clicks_lane(game_id)
    st.divider()
    ok_voice = pubstatus.voice_lane(game_id, show_events=True)

    st.divider()
    if ok_game and ok_clicks and ok_voice:
        st.success("All three lanes published ✅ — this game is done.")
    else:
        missing = [n for n, ok in [("Game analytics", ok_game),
                                   ("Click stats", ok_clicks),
                                   ("Narration", ok_voice)] if not ok]
        st.warning("Still open: " + ", ".join(missing))

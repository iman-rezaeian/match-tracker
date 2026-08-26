"""Shared per-lane publish panels for the workbench.

Coach decision 2026-08-26: every lane tab carries its OWN publish action so
work publishes where it happens — Game (analytics ride the ▶︎ Run analytics
pipeline), Clicks (click_publish button on the Clicks tab), Narrate (voice
events land one-by-one via Review accepts). The Publish tab renders the same
three panels as the cross-check that all three lanes made it onto the doc.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parent.parent
NARR_ROOT = REPO / "tracking" / "outputs" / "narration"
CLEAN_DIR = REPO / "tracking" / "outputs" / "voice_clean"
PY = sys.executable


@st.cache_data(ttl=30)
def _game_raw(game_id: str) -> dict:
    from post_game import firestore_io
    return (firestore_io._team_doc().collection("games")
            .document(game_id).get().to_dict() or {})


@st.cache_data(ttl=30)
def _has_analytics(game_id: str) -> bool:
    from post_game import firestore_io
    coll = firestore_io._team_doc().collection("games")
    try:
        return bool(list(coll.document(game_id)
                         .collection("analytics").limit(1).stream()))
    except Exception:
        return False


def clear_caches() -> None:
    _game_raw.clear()
    _has_analytics.clear()


def _badge(done: bool) -> str:
    return "✅ published" if done else "— not yet"


def game_lane(game_id: str) -> bool:
    """Game analytics lane. Publishing IS the pipeline run (upload with run)."""
    g = _game_raw(game_id)
    pub = _has_analytics(game_id)
    st.subheader(f"🏟 Game analytics · {_badge(pub)}")
    c = st.columns(4)
    c[0].metric("Video", "✓" if g.get("videoUrl") else "—")
    kicks = bool(g.get("videoOffsetH1Confirmed") and g.get("videoOffsetH2Confirmed"))
    c[1].metric("Kickoffs", "✓" if kicks else "—")
    c[2].metric("Calibration", "✓" if g.get("calibration") else "—")
    c[3].metric("Analytics", "✓" if pub else "—")
    st.caption("Publishing for this lane rides **▶︎ Run analytics** on the "
               "Game tab — clips, reels, and analytics docs upload as part "
               "of the run (unless *skip upload* was checked).")
    return pub


def clicks_lane(game_id: str) -> bool:
    """Click position stats lane: dry-run preview + publish.

    The Clicks tab itself carries the richer inline publish (delta badge +
    button in click_sample_app's sidebar); this panel is the Publish-page
    view of the same lane. Both share the `.published` count marker the click
    app writes next to clicks.jsonl, so publishing from either place keeps
    the other's "up to date" verdict honest.
    """
    g = _game_raw(game_id)
    clicks_root = REPO / "tracking" / "outputs" / "click_samples" / game_id
    clicks_file = clicks_root / "clicks.jsonl"
    n_clicks = sum(1 for l in clicks_file.read_text().splitlines() if l.strip()) \
        if clicks_file.exists() else 0
    marker = clicks_root / ".published"   # same marker as click_sample_app
    try:
        n_published = int(marker.read_text().strip() or 0) if marker.exists() else 0
    except ValueError:
        n_published = 0
    n_new = max(0, n_clicks - n_published)
    pub = bool(g.get("click_stats") or g.get("clickStats"))
    st.subheader(f"🖱 Click position stats · {_badge(pub)}")
    st.markdown(f"- clicks on disk: **{n_clicks}** · published to doc: "
                f"**{'yes' if pub else 'no'}** · new since last publish: "
                f"**{n_new}**")
    col1, col2 = st.columns([1, 1])
    if col1.button("Preview (dry run)", disabled=not n_clicks,
                   key="pub_clicks_preview"):
        r = subprocess.run([PY, "-m", "tracking.click_publish",
                            "--game-id", game_id, "--dry-run"],
                           capture_output=True, text=True, cwd=REPO)
        st.code((r.stdout + r.stderr)[-3000:])
    if col2.button("Publish click stats →", type="primary",
                   disabled=not n_clicks, key="pub_clicks_go"):
        r = subprocess.run([PY, "-m", "tracking.click_publish",
                            "--game-id", game_id],
                           capture_output=True, text=True, cwd=REPO)
        st.code((r.stdout + r.stderr)[-3000:])
        if r.returncode == 0:
            marker.write_text(str(n_clicks))
        clear_caches()
    return pub


def voice_lane(game_id: str, show_events: bool = False) -> bool:
    """Narration lane: sessions on disk vs drafts decided vs events on doc.

    'Publish' here is accepting drafts on the Review page — each accept writes
    a voice-confirmed event straight to the game doc, so this panel is status
    plus a pointer, not another write path.
    """
    g = _game_raw(game_id)
    voice_evs = [e for e in (g.get("events") or [])
                 if e.get("source") == "voice-confirmed"]
    sess_root = NARR_ROOT / game_id
    sess_dirs = sorted([d for d in sess_root.iterdir()
                        if (d / "session.json").exists()]) \
        if sess_root.exists() else []

    total_drafts = decided = 0
    unprocessed = []
    for d in sess_dirs:
        union = CLEAN_DIR / f"{game_id}_{d.name}.events.aligned.union.json"
        if not union.exists():
            unprocessed.append(d.name)
            continue
        drafts = json.loads(union.read_text()).get("new_drafts", [])
        total_drafts += len(drafts)
        dec_p = d / "decisions.json"
        decisions = json.loads(dec_p.read_text()) if dec_p.exists() else {}
        decided += sum(1 for v in decisions.values() if v.get("status"))

    pub = bool(voice_evs) or (sess_dirs and not unprocessed
                              and total_drafts == decided)
    st.subheader(f"🎙 Narration events · {_badge(bool(voice_evs))}")
    st.markdown(f"- sessions on disk: **{len(sess_dirs)}** · drafts decided: "
                f"**{decided}/{total_drafts}** · voice-confirmed events on "
                f"doc: **{len(voice_evs)}**")
    if unprocessed:
        st.warning("Unprocessed session(s): " + ", ".join(unprocessed)
                   + " — run the pipeline on the **Review** page.")
    elif total_drafts > decided:
        st.info(f"{total_drafts - decided} draft(s) awaiting a decision on "
                "the **Review** page — accepting is what publishes them.")
    elif not sess_dirs:
        st.caption("No narration sessions yet — record one on the Narrate page.")

    if show_events and voice_evs:
        for e in sorted(voice_evs,
                        key=lambda e: (e.get("period", 0), e.get("elapsed", 0))):
            mm, ss = divmod(int(e.get("elapsed") or 0), 60)
            st.markdown(f"- P{e.get('period')} {mm}:{ss:02d} "
                        f"**{e.get('type')}** {e.get('playerId') or ''} — "
                        f"_{e.get('voiceQuote') or ''}_")
    return pub

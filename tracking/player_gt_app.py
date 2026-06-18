"""Streamlit app for UNBIASED per-player ground-truth labeling (Tier 1 #1).

Shows each meaningful tracklet's crop strip BLIND to the pipeline's guess and
records the TRUE player (or not-a-player / can't-tell). Writes straight back to
tracking/labels/<game>_player_gt/gt.csv so `python -m tracking.player_gt_eval`
reads it with no extra step. Reuses the stitch_label_app skeleton.

Launch:
    set -a; source .env; set +a
    streamlit run tracking/player_gt_app.py

Keys:  N = not a player · U = can't tell · ← = back   (players: click)
"""
from __future__ import annotations

import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import csv
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from post_game import firestore_io

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
NOT_PLAYER, CANT_TELL = "__not_player__", "__cant_tell__"

st.set_page_config(page_title="Player GT Labeler", layout="wide")


def discover_csvs() -> list[Path]:
    return sorted(LABELS_ROOT.glob("*_player_gt/gt.csv"))


@st.cache_data(show_spinner=False)
def load_csv(path_str: str, mtime: float) -> list[dict]:
    with open(path_str) as f:
        return list(csv.DictReader(f))


@st.cache_data(show_spinner=False)
def squad_of(game_id: str) -> list[dict]:
    """[{id, label}] for the game's dressed squad, sorted by jersey number."""
    game = firestore_io.get_game(game_id)
    roster = {r.id: r for r in firestore_io.get_roster()}
    ids = [p for p in (game.squad or []) if p in roster] or list(roster)
    out = [{"id": p, "num": roster[p].jersey_number,
            "label": f"#{roster[p].jersey_number or '?'} {roster[p].name}"} for p in ids]
    return sorted(out, key=lambda d: (d["num"] is None, d["num"] or 0))


def save_label(csv_path: Path, tracklet_id: str, true_pid: str, label: str) -> None:
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    for r in rows:
        if str(r["tracklet_id"]) == str(tracklet_id):
            r["true_player_id"], r["label"] = true_pid, label
            break
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


csvs = discover_csvs()
if not csvs:
    st.error(f"No GT sets under {LABELS_ROOT}/*_player_gt/. "
             "Run `python -m tracking.player_gt_sampler` first.")
    st.stop()

records: list[dict] = []
for cp in csvs:
    for r in load_csv(str(cp), cp.stat().st_mtime):
        r = dict(r)
        r["_csv"], r["_img"] = str(cp), str(cp.parent / r["image"])
        records.append(r)
df = pd.DataFrame(records)
df["label"] = df.get("label", "").fillna("").astype(str)

st.sidebar.title("🪪 Player GT Labeler")
game_opts = sorted(df["game_id"].unique())
sel_game = st.sidebar.selectbox("Game", game_opts)
only_unlabeled = st.sidebar.checkbox("Only unlabeled", value=True)

view = df[df["game_id"] == sel_game].copy()
# Biggest-time tracklets first — most attribution at stake.
view = view.sort_values("minutes", key=lambda s: s.astype(float), ascending=False)
if only_unlabeled:
    view = view[view["label"] == ""]
view = view.reset_index(drop=True)

done = int((df[df["game_id"] == sel_game]["label"] != "").sum())
total = int((df["game_id"] == sel_game).sum())
st.sidebar.progress(done / total if total else 0.0, text=f"{done} / {total} labeled")
counts = df[df["game_id"] == sel_game]["label"].value_counts().to_dict()
st.sidebar.markdown(
    f"- 🙋 player: **{sum(v for k, v in counts.items() if k not in ('', NOT_PLAYER, CANT_TELL))}**\n"
    f"- 🚫 not a player: **{counts.get(NOT_PLAYER, 0)}**\n"
    f"- 🤷 can't tell: **{counts.get(CANT_TELL, 0)}**\n"
    f"- ⬜ remaining: **{counts.get('', 0)}**"
)
if st.sidebar.button("⬇ Reload from disk"):
    load_csv.clear()
    squad_of.clear()
    st.rerun()

if "idx" not in st.session_state:
    st.session_state.idx = 0
if len(view) == 0:
    st.success("🎉 Nothing left in this view.")
    if done == total:
        st.balloons()
    st.stop()
st.session_state.idx = max(0, min(st.session_state.idx, len(view) - 1))
i = st.session_state.idx
row = view.iloc[i]
squad = squad_of(sel_game)
name_of = {s["id"]: s["label"] for s in squad}


def _apply(true_pid: str, label: str):
    save_label(Path(row["_csv"]), row["tracklet_id"], true_pid, label)
    load_csv.clear()
    if not only_unlabeled:
        st.session_state.idx = min(st.session_state.idx + 1, len(view) - 1)
    st.rerun()


st.subheader(f"{sel_game} · tracklet {row['tracklet_id']}  ({i + 1} of {len(view)} in view)")
m = st.columns(4)
m[0].metric("tracked", f"{float(row['minutes']):.1f} min")
m[1].metric("span", f"{float(row['t_start_s']):.0f}–{float(row['t_end_s']):.0f}s")
m[2].metric("detections", row["n_det"])
cur = row["label"]
cur_txt = name_of.get(cur, "🚫 not a player" if cur == NOT_PLAYER
                       else "🤷 can't tell" if cur == CANT_TELL else "unlabeled")
m[3].metric("current", cur_txt if cur else "unlabeled")

st.image(row["_img"], use_container_width=True)
st.caption("Same physical kid across the strip? Pick the player. **Blind on purpose** — "
           "the pipeline's guess is hidden so this stays unbiased.")

# Roster picker grid (4 per row) + sentinels.
cols = st.columns(4)
for j, s in enumerate(squad):
    if cols[j % 4].button(s["label"], use_container_width=True, key=f"p_{s['id']}"):
        _apply(s["id"], "player")
b = st.columns(3)
if b[0].button("🚫 Not a player (N)", use_container_width=True):
    _apply("", NOT_PLAYER)
if b[1].button("🤷 Can't tell (U)", use_container_width=True):
    _apply("", CANT_TELL)
if b[2].button("⬅ Back", use_container_width=True):
    st.session_state.idx = max(0, st.session_state.idx - 1)
    st.rerun()

components.html(
    """
    <script>
    const doc = window.parent.document;
    const clickByText = (txt) => {
      const b = Array.from(doc.querySelectorAll('button')).find(el => el.innerText.trim().startsWith(txt));
      if (b) b.click();
    };
    if (!window.__pgt_keys__) {
      window.__pgt_keys__ = true;
      doc.addEventListener('keydown', (e) => {
        if (e.target && /(INPUT|TEXTAREA|SELECT)/.test(e.target.tagName)) return;
        if (e.key.toLowerCase() === 'n') clickByText('🚫');
        else if (e.key.toLowerCase() === 'u') clickByText('🤷');
        else if (e.key === 'ArrowLeft') clickByText('⬅');
      });
    }
    </script>
    """,
    height=0,
)
st.caption("⌨️  **N** = not a player · **U** = can't tell · **←** = back · players: click")

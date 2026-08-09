"""Streamlit app: label WHO the follower is on, across a long stint.

What this produces, and why it is the bottleneck
------------------------------------------------
Every survival number measured so far was scored against the tracker's own
clean segments, which run ~90 s. The question that matters — does a follow
survive a 20-25 minute stint — cannot be answered with a 90-second answer key,
and three attempts to work around that produced results that had to be
retracted:

  * seven stints seeded on the SAME body, read as a "3.3-minute wall";
  * 86% coverage that was really "we only asked 48-second questions";
  * a front-loaded hazard curve that was the answer key expiring, not the
    follower failing.

The common cause is that no per-frame ground truth exists. This app is how the
coach supplies it.

Two label types, both needed
----------------------------
**SEED** — at each stint start: which of these bodies is the player? The
automatic stand-in ("longest-lived track alive at the stint start") has no idea
who anyone is, and demonstrably picks wrong: it seeded the KEEPER's stint on a
body standing at midfield, and only 13% of that follow was ever near a goal.
Without real seeds every downstream number is measuring the wrong child.

**CHECKPOINT** — every couple of minutes after that: is the box still on him?
A "no" is a swap observation with a known time, which is exactly what a
survival curve needs.

Identity comes from watching movement, not from a still: the median detection
box is 77 px. Sometimes a name or number is legible (a sample frame read
HASSOUN 11) and then the label is definitive — say so with the "certain" flag,
because those labels are worth more.

Writes only to tracking/labels/<game>_stint_labels/labels.csv. No Firestore.

Launch:
    set -a; source .env; set +a
    streamlit run tracking/stint_label_app.py
"""
from __future__ import annotations

import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import csv
import json
import sys
from pathlib import Path

# `streamlit run` puts THIS file's directory on sys.path, not the repo root, so
# `from post_game import ...` fails without help. Same fix as player_gt_app.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
import streamlit as st

from post_game import firestore_io

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
FIELDS = ["clip", "stint_key", "player_id", "t_checkpoint_s",
          "elapsed_in_follow_s", "verdict", "true_player_id", "certain", "note"]
# verdict vocabulary
SAME, WRONG, UNSURE = "same", "wrong", "unsure"
# Non-player answers. "coach / staff" is deliberately separate from "opponent":
# our coaches wear BLACK, the same as the team on a normal game, so a boxed
# coach is a colour-classifier failure of a different kind — and the first seed
# clip rendered was in fact one of our own coaches on the touchline. Recording
# them apart is what lets the scorer say which failure mode is costing what.
COACH, OPPONENT, OTHER = "__coach__", "__opponent__", "__other__"
NON_PLAYER = ["coach / staff (ours)", "opponent player", "other / can't say"]
_NON_PLAYER_ID = {NON_PLAYER[0]: COACH, NON_PLAYER[1]: OPPONENT,
                  NON_PLAYER[2]: OTHER}

st.set_page_config(page_title="Stint Label", layout="wide")


def discover() -> list[Path]:
    return sorted(LABELS_ROOT.glob("*_stint_labels/manifest.json"))


@st.cache_data(show_spinner=False)
def roster_options(game_id: str) -> list[dict]:
    game = firestore_io.get_game(game_id)
    roster = {r.id: r for r in firestore_io.get_roster()}
    ids = [p for p in (game.squad or []) if p in roster] or list(roster)
    out = [{"id": p, "num": roster[p].jersey_number,
            "label": f"#{roster[p].jersey_number or '?'} {roster[p].name}"}
           for p in ids]
    return sorted(out, key=lambda d: (d["num"] is None, d["num"] or 0))


def load_labels(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path) as f:
        return {r["clip"]: r for r in csv.DictReader(f)}


def save_label(path: Path, row: dict) -> None:
    cur = load_labels(path)
    cur[row["clip"]] = {**{k: "" for k in FIELDS}, **row}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(cur.values())


sets = discover()
if not sets:
    st.error(f"No clip sets under {LABELS_ROOT}/*_stint_labels/. "
             "Run `python -m tracking.stint_label_render --game-id <id>` first.")
    st.stop()

st.sidebar.title("🎯 Stint labeler")
sel = st.sidebar.selectbox("Clip set", sets,
                           format_func=lambda p: p.parent.name)
outdir = sel.parent
game_id = outdir.name.replace("_stint_labels", "")
manifest = json.loads(sel.read_text())
labels_csv = outdir / "labels.csv"
done = load_labels(labels_csv)

only_todo = st.sidebar.checkbox("Only unlabelled", value=True)
items = [m for m in manifest if not (only_todo and m["clip"] in done)]
st.sidebar.metric("Labelled", f"{len(done)}/{len(manifest)}")
if not items:
    st.success("Everything in this set is labelled. "
               "Score it with `python -m tracking.stint_label_score`.")
    st.stop()

# Group by stint so the coach works through one player at a time — context
# carries over, which is the whole point of labelling by continuity.
by_stint: dict[str, list[dict]] = {}
for m in items:
    by_stint.setdefault(m["stint_key"], []).append(m)
for v in by_stint.values():
    v.sort(key=lambda m: m["t_checkpoint_s"])

stint_key = st.sidebar.selectbox("Stint", sorted(by_stint),
                                 format_func=lambda k: f"{k}  ({len(by_stint[k])} left)")
queue = by_stint[stint_key]
cur = queue[0]

opts = roster_options(game_id)
label_of = {o["id"]: o["label"] for o in opts}
nominal = cur["player_id"]

st.subheader(f"{label_of.get(nominal, nominal)} — "
             f"{cur['elapsed_in_follow_s']/60:.1f} min into the follow")
st.caption(f"clip {cur['clip']} · video t={cur['t_checkpoint_s']:.0f}s · "
           f"stint {stint_key}")

left, right = st.columns([3, 2])
with left:
    st.video(str(outdir / cur["clip"]))
    st.caption("The YELLOW box is who the follower currently believes this is.")

with right:
    is_seed = cur["elapsed_in_follow_s"] < 1.0
    if is_seed:
        st.info("**SEED frame.** Who is actually in the yellow box? This sets "
                "the starting truth for the whole stint — the automatic guess "
                "is unreliable here.")
    else:
        st.write("**Is the yellow box still the same player?**")

    certain = st.checkbox("I could read a name or number (certain)", value=False,
                          help="Definitive labels are worth more than "
                               "continuity judgements — flag them.")
    note = st.text_input("Note (optional)", "")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Still him", use_container_width=True, type="primary"):
            save_label(labels_csv, {
                "clip": cur["clip"], "stint_key": stint_key,
                "player_id": nominal,
                "t_checkpoint_s": cur["t_checkpoint_s"],
                "elapsed_in_follow_s": cur["elapsed_in_follow_s"],
                "verdict": SAME, "true_player_id": nominal,
                "certain": int(certain), "note": note})
            st.rerun()
    with c2:
        if st.button("🤷 Can't tell", use_container_width=True):
            save_label(labels_csv, {
                "clip": cur["clip"], "stint_key": stint_key,
                "player_id": nominal,
                "t_checkpoint_s": cur["t_checkpoint_s"],
                "elapsed_in_follow_s": cur["elapsed_in_follow_s"],
                "verdict": UNSURE, "true_player_id": "",
                "certain": 0, "note": note})
            st.rerun()

    st.divider()
    st.write("**Wrong player — who is it really?**")
    st.caption("Pick the actual child, or say what it is instead. "
               "Our coaches wear black like the team, so 'coach / staff' is "
               "its own answer — not 'opponent'.")
    who = st.selectbox("Actually", ["— pick —"] + NON_PLAYER +
                       [o["label"] for o in opts], index=0)
    if st.button("❌ Wrong", use_container_width=True):
        if who == "— pick —":
            st.warning("Say who or what it is first.")
        else:
            tid = _NON_PLAYER_ID.get(
                who, next((o["id"] for o in opts if o["label"] == who), ""))
            save_label(labels_csv, {
                "clip": cur["clip"], "stint_key": stint_key,
                "player_id": nominal,
                "t_checkpoint_s": cur["t_checkpoint_s"],
                "elapsed_in_follow_s": cur["elapsed_in_follow_s"],
                "verdict": WRONG, "true_player_id": tid,
                "certain": int(certain), "note": note})
            st.rerun()

st.divider()
prog = pd.DataFrame([{
    "stint": k,
    "labelled": sum(1 for m in manifest
                    if m["stint_key"] == k and m["clip"] in done),
    "total": sum(1 for m in manifest if m["stint_key"] == k),
} for k in sorted({m["stint_key"] for m in manifest})])
st.dataframe(prog, hide_index=True, use_container_width=True)

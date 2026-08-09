"""Streamlit app: is this tracklet ONE child, or several?

The question, and why it is not "who is this?"
-----------------------------------------------
The previous GT app asked the coach to NAME each tracklet. On 30 strips he
could name 4 and could not name 26 — and `26/30 = 87%` was then read as "87% of
tracklets hold more than one child". It is not. It is the share he could not
identify, and the two are different claims: a tracklet is unnameable when it is
mixed, but also when it is small, distant, back-turned or blurred.

Composition survives that. "Does this strip show one child or several?" is
answerable on a player nobody can name — which, given the median detection box
is 77 px and every jersey read comes off the BACK, is most of them.

`Can't tell` is a FIRST-CLASS answer here, not a failure. Forcing an illegible
strip into a composition verdict is precisely the conflation that produced the
retired figure.

Writes only to tracking/labels/<game>_composition/labels.csv. No Firestore.

Launch:
    set -a; source .env; set +a
    streamlit run tracking/composition_app.py
"""
from __future__ import annotations

import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import csv
import json
import sys
from pathlib import Path

# `streamlit run` puts this file's directory on sys.path, not the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
import streamlit as st

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
FIELDS = ["tracklet_id", "image", "duration_s", "n_det",
          "verdict", "n_children", "note"]
ONE, SEVERAL, UNSURE = "one", "several", "cant_tell"

st.set_page_config(page_title="Composition", layout="wide")


def load_labels(p: Path) -> dict[str, dict]:
    if not p.exists():
        return {}
    with open(p) as f:
        return {r["tracklet_id"]: r for r in csv.DictReader(f)}


def save(p: Path, row: dict) -> None:
    cur = load_labels(p)
    cur[str(row["tracklet_id"])] = {**{k: "" for k in FIELDS}, **row}
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(cur.values())


sets = sorted(LABELS_ROOT.glob("*_composition/manifest.json"))
if not sets:
    st.error(f"No strips under {LABELS_ROOT}/*_composition/. Run "
             "`python -m tracking.composition_sampler --game-id <id>` first.")
    st.stop()

st.sidebar.title("🧬 Composition")
sel = st.sidebar.selectbox("Set", sets, format_func=lambda p: p.parent.name)
outdir = sel.parent
manifest = json.loads(sel.read_text())
labels_csv = outdir / "labels.csv"
done = load_labels(labels_csv)
st.sidebar.metric("Judged", f"{len(done)}/{len(manifest)}")

todo = [m for m in manifest if str(m["tracklet_id"]) not in done]
if not todo:
    st.success("All strips judged.")
    df = pd.DataFrame(load_labels(labels_csv).values())
    if not df.empty:
        n = df.verdict.value_counts().to_dict()
        one, sev = n.get(ONE, 0), n.get(SEVERAL, 0)
        st.write(f"**one child: {one} · several: {sev} · "
                 f"can't tell: {n.get(UNSURE, 0)}**")
        if one + sev:
            st.write(f"Of the {one+sev} strips where composition was "
                     f"judgeable, **{100*sev/(one+sev):.0f}% are mixed**.")
            st.caption("Quote that denominator explicitly. The retired 87% "
                       "figure came from dividing by the whole sample "
                       "including the can't-tells.")
        st.dataframe(df, hide_index=True, use_container_width=True)
    st.stop()

cur = todo[0]
st.subheader(f"Tracklet {cur['tracklet_id']} — {cur['duration_s']:.0f}s, "
             f"{cur['n_crops']} crops across its life")
st.caption("Crops run left to right in time. **Is this the same child "
           "throughout, or does it change?** You do NOT need to know who it is.")
st.image(str(outdir / cur["image"]), use_container_width=True)

note = st.text_input("Note (optional)", "")
c1, c2, c3 = st.columns(3)
base = {"tracklet_id": cur["tracklet_id"], "image": cur["image"],
        "duration_s": cur["duration_s"], "n_det": cur["n_det"], "note": note}
with c1:
    if st.button("✅ ONE child", use_container_width=True, type="primary"):
        save(labels_csv, {**base, "verdict": ONE, "n_children": 1})
        st.rerun()
with c2:
    n_kids = st.number_input("how many?", 2, 6, 2, label_visibility="collapsed")
    if st.button("🔀 SEVERAL children", use_container_width=True):
        save(labels_csv, {**base, "verdict": SEVERAL, "n_children": int(n_kids)})
        st.rerun()
with c3:
    if st.button("🤷 Can't tell", use_container_width=True):
        save(labels_csv, {**base, "verdict": UNSURE, "n_children": ""})
        st.rerun()

st.divider()
st.caption("'Can't tell' is a real answer — an illegible strip is illegible, "
           "not mixed. Recording it as mixed is the exact error that produced "
           "the retired 87% chain-impurity figure.")

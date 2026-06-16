"""Streamlit labeling app for stitch-pair ground truth (Phase 0).

Shows each side-by-side crop (yellow box A = end of track A, green box B = start
of track B, red bar = the stitch decision) with its metadata, and one-click /
one-keypress labeling. Writes straight back into tracking/labels/<game>/pairs.csv
so `python -m tracking.stitch_pr_eval` reads the result with no extra step.

Launch:
    streamlit run tracking/stitch_label_app.py

Keys:  1 / S = same · 2 / D = different · 3 / U = can't tell · ← = back
"""
from __future__ import annotations

import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import csv
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
LABEL_MEANING = {"1": "✅ same", "0": "❌ different", "-1": "🤷 can't tell"}
LABEL_COLOR = {"1": "#22c55e", "0": "#ef4444", "-1": "#a8a29e", "": "#44403c"}

st.set_page_config(page_title="Stitch Pair Labeler", layout="wide")


# ---- data ----------------------------------------------------------------
def discover_csvs() -> list[Path]:
    return sorted(LABELS_ROOT.glob("*/pairs.csv"))


@st.cache_data(show_spinner=False)
def load_csv(path_str: str, mtime: float) -> list[dict]:
    """Load a pairs.csv. `mtime` busts the cache when we write back."""
    with open(path_str) as f:
        return list(csv.DictReader(f))


def save_label(csv_path: Path, pair_id: str, label: str, note: str | None = None) -> None:
    """Rewrite the CSV with one row's label (and optional note) updated."""
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys() if rows else []
    for r in rows:
        if r["pair_id"] == pair_id:
            r["label"] = label
            if note is not None:
                r["note"] = note
            break
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)


# ---- build the working set ----------------------------------------------
csvs = discover_csvs()
if not csvs:
    st.error(f"No label sets found under {LABELS_ROOT}. "
             "Run `python -m tracking.stitch_label_sampler` first.")
    st.stop()

records: list[dict] = []
for cp in csvs:
    game_dir = cp.parent.name
    for r in load_csv(str(cp), cp.stat().st_mtime):
        r = dict(r)
        r["_csv"] = str(cp)
        r["_game_dir"] = game_dir
        r["_img"] = str(cp.parent / r["image"])
        records.append(r)
df = pd.DataFrame(records)
df["label"] = df.get("label", "").fillna("").astype(str)

# ---- sidebar: filters + progress ----------------------------------------
st.sidebar.title("🧵 Stitch Labeler")

game_opts = ["(all)"] + sorted(df["_game_dir"].unique())
stratum_opts = ["(all)"] + sorted(df["stratum"].unique())
sel_game = st.sidebar.selectbox("Game", game_opts)
sel_stratum = st.sidebar.selectbox("Stratum", stratum_opts)
only_unlabeled = st.sidebar.checkbox("Only unlabeled", value=True)

view = df.copy()
if sel_game != "(all)":
    view = view[view["_game_dir"] == sel_game]
if sel_stratum != "(all)":
    view = view[view["stratum"] == sel_stratum]
if only_unlabeled:
    view = view[view["label"] == ""]
view = view.reset_index(drop=True)

# Interleave strata round-robin so the queue ALTERNATES instead of front-loading
# 25 trivial cross-team negatives. Same-team pairs (where geometry can actually
# cause a wrong merge — the valuable labels) then surface immediately. Stable
# within each stratum. Skipped when a single stratum is already selected.
if sel_stratum == "(all)" and len(view) > 1:
    from itertools import zip_longest
    groups = [list(idxs) for _, idxs in
              view.groupby("stratum", sort=True).groups.items()]
    order = [x for tup in zip_longest(*groups) for x in tup if x is not None]
    view = view.loc[order].reset_index(drop=True)

# overall progress (across ALL pairs, not just the filtered view)
done = int((df["label"] != "").sum())
total = len(df)
st.sidebar.progress(done / total if total else 0.0, text=f"{done} / {total} labeled")

# label distribution
counts = df["label"].value_counts().to_dict()
st.sidebar.markdown("**Labeled so far**")
st.sidebar.markdown(
    f"- ✅ same: **{counts.get('1', 0)}**\n"
    f"- ❌ different: **{counts.get('0', 0)}**\n"
    f"- 🤷 can't tell: **{counts.get('-1', 0)}**\n"
    f"- ⬜ remaining: **{counts.get('', 0)}**"
)

# per-stratum remaining
st.sidebar.markdown("**Remaining by stratum**")
rem = df[df["label"] == ""].groupby("stratum").size().to_dict()
for s in sorted(df["stratum"].unique()):
    st.sidebar.markdown(f"- {s}: {rem.get(s, 0)}")

if st.sidebar.button("⬇ Reload from disk"):
    load_csv.clear()
    st.rerun()

# ---- navigation state ----------------------------------------------------
if "idx" not in st.session_state:
    st.session_state.idx = 0
# Keep idx in range when the filtered view shrinks (e.g. after labeling).
if len(view) == 0:
    st.success("🎉 Nothing left to label in this view. "
               "Uncheck 'Only unlabeled' or pick another stratum to review.")
    if done == total:
        st.balloons()
    st.stop()
st.session_state.idx = max(0, min(st.session_state.idx, len(view) - 1))
i = st.session_state.idx
row = view.iloc[i]


def _apply(label: str):
    save_label(Path(row["_csv"]), row["pair_id"], label)
    load_csv.clear()           # bust cache so reload reflects the write
    # advance: when filtering to unlabeled, the current row drops out, so the
    # same idx now points at the next item; otherwise step forward.
    if not only_unlabeled:
        st.session_state.idx = min(st.session_state.idx + 1, len(view) - 1)
    st.rerun()


# ---- main panel ----------------------------------------------------------
top = st.columns([3, 1])
with top[0]:
    st.subheader(f"{row['_game_dir']} · {row['stratum']}  ({i + 1} of {len(view)} in view)")
with top[1]:
    cur = row["label"]
    st.markdown(
        f"<div style='text-align:right;padding-top:8px'>current: "
        f"<b style='color:{LABEL_COLOR.get(cur,'#999')}'>"
        f"{LABEL_MEANING.get(cur,'unlabeled')}</b></div>",
        unsafe_allow_html=True,
    )

# metadata strip
m = st.columns(5)
m[0].metric("gap", f"{float(row['gap_s']):.1f} s")
m[1].metric("distance", f"{float(row['dist_m']):.1f} m")
m[2].metric("needed speed", f"{float(row['need_speed_ms']):.1f} m/s")
m[3].metric("teams", f"{row['team_a']} → {row['team_b']}")
m[4].metric("OSNet cos", row["cos_app"] if row["cos_app"] else "—")

st.image(row["_img"], use_container_width=True)
st.caption("Yellow **A** = end of track A (left) · Green **B** = start of track B (right) · "
           "red bar = the stitch decision. Are A and B the **same physical player**?")

# action buttons
b = st.columns([1, 1, 1, 1, 2])
if b[0].button("✅ Same (1)", use_container_width=True, type="primary"):
    _apply("1")
if b[1].button("❌ Different (2)", use_container_width=True):
    _apply("0")
if b[2].button("🤷 Can't tell (3)", use_container_width=True):
    _apply("-1")
if b[3].button("⬅ Back", use_container_width=True):
    st.session_state.idx = max(0, st.session_state.idx - 1)
    st.rerun()
with b[4]:
    jump = st.number_input("jump to #", min_value=1, max_value=len(view),
                           value=i + 1, step=1, label_visibility="collapsed")
    if jump - 1 != i:
        st.session_state.idx = int(jump - 1)
        st.rerun()

# ---- keyboard shortcuts --------------------------------------------------
# Component runs in an iframe; reach the parent doc (same origin on localhost)
# and click the Streamlit button whose label matches the pressed key.
components.html(
    """
    <script>
    const doc = window.parent.document;
    const clickByText = (txt) => {
      const btns = Array.from(doc.querySelectorAll('button'));
      const b = btns.find(el => el.innerText.trim().startsWith(txt));
      if (b) b.click();
    };
    if (!window.__stitch_keys__) {
      window.__stitch_keys__ = true;
      doc.addEventListener('keydown', (e) => {
        if (e.target && /(INPUT|TEXTAREA|SELECT)/.test(e.target.tagName)) return;
        if (e.key === '1' || e.key.toLowerCase() === 's') clickByText('✅');
        else if (e.key === '2' || e.key.toLowerCase() === 'd') clickByText('❌');
        else if (e.key === '3' || e.key.toLowerCase() === 'u') clickByText('🤷');
        else if (e.key === 'ArrowLeft') clickByText('⬅');
      });
    }
    </script>
    """,
    height=0,
)
st.caption("⌨️  **1**/S = same · **2**/D = different · **3**/U = can't tell · **←** = back")

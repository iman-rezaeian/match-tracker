#!/usr/bin/env python3
"""Streamlit: click players on a band of the pitch to sample their positions.

The interaction
---------------
One band of the pitch at a time, clicked directly. No tile picker.

A tile picker was built first and removed at the coach's request. It diced the
pitch into 750x600 panels and offered them as radio buttons named by pitch area.
Two things were wrong with it: the labels DUPLICATED in 9 of 20 frames
("defensive third, centre" appearing twice in one row, so the buttons identified
nothing), and even unique names would have made the coach translate a zone phrase
onto a photograph he was already looking at. He confirmed he can read players at
band scale, so navigating tiles bought nothing and cost a decision per view.

What makes a band legible is trimming the crop to the rows players occupy. The
renderer's first version padded the top by the 98th-percentile box height, which
is driven by near-camera adults, so half the image was sky, treeline and empty
foreground -- 1291 px tall of which only 663 held any player. Cropping to actual
head-and-foot rows halves the height, which is what lets three bands render at
~102 px per player in an 874 px-tall image that fits on a screen.

Budget unchanged: ~38 frames to reach the ~400 clicks that buy 7% position error.

What a click records
--------------------
`(video_time_s, player_id, click_x_eq, click_y_eq, snapped_track_id)`. The click
is converted panel -> equirect via `canvas_to_equirect`'s sibling logic here, then
equirect -> field metres downstream by the existing homography. Nothing about a
TRACK is required: the click itself is the datum, which is the whole reason this
approach survives a 6 s median track lifespan.

Snapping is offered but GATED. Measured: only 3.2% of bodies have a neighbour
within 50 px, but 20.7% have one within 100 px -- so snapping to "the nearest
detection" would sometimes silently attach the name to the wrong child. A snap is
accepted only when the nearest detection is both close and unambiguous
(2nd-nearest at least 2x further); otherwise the raw click is kept. Raw clicks
are fine: adding 30 px of jitter to every click changed the position error from
55 px to 63 px at 50 clicks/player, because random error averages out in a
sample estimator.

Run:
    cd <repo> && .venv-post-game/bin/streamlit run tracking/click_sample_app.py -- \\
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Streamlit re-runs this file top-to-bottom on every interaction, so the repo
# root has to be importable before anything from post_game is touched.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402
from PIL import Image  # noqa: E402

PANEL_W, PANEL_H = 750, 600
# A snap is only trusted when the nearest detection is this close (px) AND the
# runner-up is at least SNAP_RATIO further away.
SNAP_MAX_PX = 60.0
SNAP_RATIO = 2.0
# A ratio gate alone is not enough. Two children 30 px apart with the click
# between them can satisfy any ratio while the snap is still a coin flip, so the
# two nearest candidates must ALSO be separated in absolute terms. 3.2% of
# bodies have a neighbour within 50 px; those are the cases this protects.
SNAP_MIN_SEPARATION_PX = 50.0
# Width-axis band (metres from the FAR touchline) inside which the projection
# cannot distinguish a player on the pitch from a spectator behind it -- the far
# touchline is the horizon in this geometry. Detections here stay clickable (a
# player really can be at the far side) but never drive the panel ranking.
FAR_TOUCHLINE_BAND_M = 3.0


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    known, _ = ap.parse_known_args()
    return known


@st.cache_data(show_spinner=False)
def load_index(root: str) -> dict:
    return json.loads((Path(root) / "index.json").read_text())


@st.cache_data(show_spinner=False)
def load_roster(game_id: str) -> list[dict]:
    """Roster from Firestore; falls back to a bare list so the UI still runs."""
    try:
        from post_game import firestore_io
        players = firestore_io.get_roster()
        return [{"id": p.id, "name": getattr(p, "name", p.id),
                 "number": getattr(p, "number", None)} for p in players]
    except Exception as exc:  # pragma: no cover - UI convenience
        st.warning(f"roster unavailable ({type(exc).__name__}) — using generic ids")
        return [{"id": f"p{i}", "name": f"Player {i}", "number": i} for i in range(1, 13)]


@st.cache_data(show_spinner=False)
def load_onfield(game_id: str) -> tuple[dict[str, list[tuple[float, float]]], str | None]:
    """Per-player on-field intervals from the coach's lineup + SUB taps.

    This is what keeps the roster buttons honest. The full club roster is 16
    names, the matchday squad is 12, and only **7 are on the pitch at any
    instant** (6 outfield + keeper). Offering all 16 invites a click on a child
    who was not even at the game, and every such click is a silently wrong
    position sample that no downstream check can catch.

    The intervals come from `identity._onfield_intervals`, the same reconstruction
    the identity stage uses, so the app agrees with the rest of the pipeline
    rather than inventing a second notion of who was playing.
    """
    from post_game import firestore_io
    from post_game.identity import (_onfield_intervals,
                                    period_clock_to_video_time_factory)
    game = firestore_io.get_game(game_id)
    c2v = period_clock_to_video_time_factory(game)
    return (_onfield_intervals(game.starting_lineup, game.events, c2v),
            game.gk_player_id)


@st.cache_resource(show_spinner=False)
def load_projector(game_id: str):
    """(FieldProjector, (length_m, width_m)) or (None, None) without calibration.

    Used to test whether a detection is actually ON the pitch. Without it the
    panel ranking counts spectators and the adjacent game.
    """
    try:
        from post_game import calibration, firestore_io
        cal = firestore_io.get_game_calibration(game_id)
        if cal is None:
            return None, None
        return calibration.FieldProjector(cal), (cal.length_m, cal.width_m)
    except Exception:  # pragma: no cover - UI convenience
        return None, None


def onfield_at(
    intervals: dict[str, list[tuple[float, float]]], t: float, slack_s: float = 3.0,
) -> list[str]:
    """Player ids on the pitch at video-time `t`.

    `slack_s` covers the kickoff boundary: a frame rendered exactly at the
    kickoff offset can fall a hair before every interval opens and return an
    empty list (observed at t=40.9 s on Game 1, the H1 kickoff).
    """
    out = [p for p, ivs in intervals.items()
           if any(a - slack_s <= t <= b + slack_s for a, b in ivs)]
    return sorted(out)


def occupied_panels(
    frame: dict, box: list[int], projector=None, field_dims=None,
) -> list[dict]:
    """Panels worth showing, ranked by how much CONFIDENT play they contain.

    ⚠ Read this before reintroducing a body count to the UI. Three versions of
    this ranking were wrong, each for a different reason:

    1. **Raw detection count.** Counted every body at the venue -- parents in
       chairs, the adjacent game, people by the treeline -- so a panel of seated
       spectators ranked first while a panel of eight real players ranked fourth.
    2. **Count of bodies inside the pitch polygon.** Better, but still reported
       "8 players" for a panel containing about three children and five seated
       spectators.
    3. The reason (2) fails is geometric, not a tuning problem. Measured on Game
       1: the far touchline sits at pixel row 2028; ten pixels above it projects
       to y = -3.2 m, forty pixels to -17.5 m, and beyond that to NaN. **The far
       touchline is the horizon in this camera geometry**, so everyone past it
       projects onto y ~ 0 and reads as on-pitch. Apparent size cannot separate
       them either: a child genuinely at the far side is 30-40 px and so is a
       seated adult just beyond the line. This is the far-touchline compression
       already recorded in ACCURACY_AUDIT.md.

    So this function no longer claims to count players. It counts detections it
    can be CONFIDENT about -- those comfortably inside the far touchline -- and
    ranks by that. Bodies in the uncertain far band are tallied separately and
    never drive the ranking. The caller must not display `confident` as "N
    players": the coach can tell three children from five chairs at a glance and
    the software demonstrably cannot.

    Falls back to the raw count without a projector, so the app still runs on an
    uncalibrated game, with the old and worse ordering.
    """
    grid: dict[tuple[int, int], dict] = {}
    for d in frame["detections"]:
        gx = int((d["foot_x_eq"] - box[0]) // PANEL_W)
        gy = int((d["foot_y_eq"] - box[1]) // PANEL_H)
        if gx < 0 or gy < 0:
            continue
        cell = grid.setdefault((gx, gy), {"confident": 0, "uncertain": 0,
                                          "adults": 0, "off": 0,
                                          "fx": [], "fy": []})
        if projector is None or field_dims is None:
            cell["confident"] += 1
            continue
        L, W = field_dims
        fx, fy = projector.pixel_to_field(d["foot_x_eq"], d["foot_y_eq"])
        if np.isnan(fx) or not (-1.0 <= fx <= L + 1.0 and -1.0 <= fy <= W + 1.0):
            cell["off"] += 1
        elif d.get("bbox_h", 0) >= 120:
            cell["adults"] += 1
        elif fy < FAR_TOUCHLINE_BAND_M:
            # Inside the polygon arithmetically, but within the band where
            # everything beyond the line also lands. Not trusted, not ranked.
            cell["uncertain"] += 1
        else:
            cell["confident"] += 1
            cell["fx"].append(fx)
            cell["fy"].append(fy)

    out = [{"gx": gx, "gy": gy, **v} for (gx, gy), v in grid.items()
           if v["confident"] > 0 or v["uncertain"] > 0]
    # Rank by confident play, then prefer panels further from the far line.
    out.sort(key=lambda c: (-c["confident"],
                            -(np.median(c["fy"]) if c["fy"] else 0.0)))
    return out


def panel_label(cell: dict, field_dims=None) -> str:
    """Where on the pitch this panel is, from FIELD coordinates.

    Two earlier versions of this were wrong and both are worth recording.

    The first showed the detector's body count ("#1 (5 bodies)") -- internal
    plumbing that told the coach nothing about where to look, and was inaccurate
    besides, because most of those bodies were spectators.

    The second derived the label from grid row/column arithmetic, which produced
    "left, far side" three times in one frame and called nearly everything "far
    side". The grid is a pixel rectangle laid over a curved pitch, so several
    cells cover the same real area and a pixel ROW is not a side of the field.

    So the label comes from the median field position of the players actually in
    the cell: which third along the length, and which side across the width.
    """
    if field_dims is None or not cell.get("fx"):
        return "pitch area"
    L, W = field_dims
    fx = float(np.median(cell["fx"]))
    fy = float(np.median(cell["fy"]))
    third = ("defensive third", "middle third", "attacking third")[
        min(2, int(max(0.0, fx) / max(1e-6, L) * 3))]
    frac = max(0.0, min(1.0, fy / max(1e-6, W)))
    side = "left" if frac < 0.33 else ("right" if frac > 0.67 else "centre")
    return f"{third}, {side}"


def panel_click_to_equirect(
    cx: float, cy: float, gx: int, gy: int, box: list[int],
) -> tuple[float, float]:
    """A click inside a 1:1 panel -> equirect pixel. No scaling involved."""
    return box[0] + gx * PANEL_W + cx, box[1] + gy * PANEL_H + cy


def snap(
    ex: float, ey: float, dets: list[dict],
) -> tuple[float, float, int | None]:
    """Snap to a detection only when it is close AND unambiguous."""
    cands = [d for d in dets if d.get("bbox_h", 0) < 120]
    if not cands:
        return ex, ey, None
    dist = sorted(
        ((float(np.hypot(d["foot_x_eq"] - ex, d["foot_y_eq"] - ey)), d)
         for d in cands), key=lambda t: t[0])
    best_d, best = dist[0]
    if best_d > SNAP_MAX_PX:
        return ex, ey, None
    if len(dist) > 1:
        runner_d, runner = dist[1]
        if runner_d < best_d * SNAP_RATIO:
            return ex, ey, None      # ambiguous by ratio
        gap = float(np.hypot(runner["foot_x_eq"] - best["foot_x_eq"],
                             runner["foot_y_eq"] - best["foot_y_eq"]))
        if gap < SNAP_MIN_SEPARATION_PX:
            return ex, ey, None      # two bodies too close to tell apart at all
    return float(best["foot_x_eq"]), float(best["foot_y_eq"]), int(best["track_id"])


def samples_path(root: Path) -> Path:
    return root / "clicks.jsonl"


def append_sample(root: Path, rec: dict) -> None:
    """Append-only, flushed per click. A 13-minute session that loses its work
    is worse than useless, so nothing is held in memory until the end."""
    with samples_path(root).open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def load_samples(root: Path) -> list[dict]:
    p = samples_path(root)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main() -> None:
    a = _args()
    root = Path(a.dir or f"tracking/outputs/click_samples/{a.game_id}")
    st.set_page_config(page_title="Click sampling", layout="wide")

    if not (root / "index.json").exists():
        st.error(f"No rendered frames at {root}. Run click_sample_render first.")
        return

    idx = load_index(str(root))
    frames = idx["frames"]
    box = idx["pitch_box"]
    roster = load_roster(a.game_id)
    done = load_samples(root)
    try:
        onfield_iv, gk_id = load_onfield(a.game_id)
    except Exception as exc:
        st.warning(f"on-field windows unavailable ({type(exc).__name__}) — "
                   "showing the whole roster")
        onfield_iv, gk_id = {}, None
    # Matchday squad = anyone the coach's log ever put on the pitch (12), not the
    # 16-name club roster.
    squad = {p for p in onfield_iv} or {p["id"] for p in roster}
    by_id = {p["id"]: p for p in roster}

    st.title("Click sampling — name the players you can see")
    counts: dict[str, int] = {}
    for s in done:
        counts[s["player_id"]] = counts.get(s["player_id"], 0) + 1

    with st.sidebar:
        st.metric("clicks recorded", len(done))
        st.caption("Target ~400 total (~50/player) for ~7% position error. "
                   "20/player (~15% error) is still usable.")
        st.write("**per player** (matchday squad)")
        for pid in sorted(squad, key=lambda i: by_id.get(i, {}).get("name", i)):
            n = counts.get(pid, 0)
            nm = by_id.get(pid, {}).get("name", pid)
            st.write(f"{'🟢' if n >= 50 else '🟡' if n >= 20 else '⚪'} "
                     f"{nm}{' (GK)' if pid == gk_id else ''}: {n}")
        st.divider()
        st.caption("Clicks must be SPREAD across the match. Frames are on a "
                   "fixed grid for that reason — please don't skip ahead to "
                   "the exciting bits, it biases every position.")

    fi = st.number_input("frame", 0, len(frames) - 1, 0, 1)
    frame = frames[int(fi)]
    st.caption(f"video t = {frame['video_time_s']:.1f}s "
               f"({frame['video_time_s']/60:.1f} min) — frame {int(fi)+1} of {len(frames)}")
    _on = onfield_at(onfield_iv, float(frame["video_time_s"]))
    if _on:
        st.info("**On the pitch now:** " + ", ".join(
            by_id.get(p, {}).get("name", p) + (" (GK)" if p == gk_id else "")
            for p in _on))

    img = Image.open(root / frame["image"])
    # The rendered canvas is banded; rebuild the flat strip so panel maths is
    # simple and independent of how many bands the renderer used.
    geom = frame["geom"]
    bands, bw, bh = geom["bands"], geom["band_w"], geom["band_h"]
    flat = Image.new("RGB", (bw * bands, bh))
    for b in range(bands):
        flat.paste(img.crop((0, b * bh, bw, (b + 1) * bh)), (b * bw, 0))
    # Undo the render scale so panels are true 1:1 equirect pixels.
    sc = geom["scale"]
    if abs(sc - 1.0) > 0.01:
        flat = flat.resize((int(flat.width / sc), int(flat.height / sc)))

    proj, dims = load_projector(a.game_id)
    st.write("**Click a player, then pick their name.** Spectators sit behind "
             "the far touchline and the camera cannot tell them from players — "
             "ignore anything that looks like a folding chair.")

    try:
        from streamlit_image_coordinates import streamlit_image_coordinates as sic
    except ImportError:
        st.error("Needs `streamlit-image-coordinates`:\n\n"
                 "`.venv-post-game/bin/pip install streamlit-image-coordinates`")
        return

    # One band at a time, no tile picker.
    #
    # The picker was removed rather than improved. Its labels duplicated in 9 of
    # 20 frames ("defensive third, centre" twice in one row, so the buttons
    # identified nothing), and even unique names would have made the coach
    # translate a zone phrase onto a photograph. He confirmed he can read players
    # at band scale, so navigating tiles bought nothing.
    band_i = st.radio(
        "band", range(bands), horizontal=True,
        format_func=lambda i: f"band {i+1} of {bands}"
    ) if bands > 1 else 0
    seg_w = flat.width // bands
    crop = flat.crop((int(band_i) * seg_w, 0,
                      min((int(band_i) + 1) * seg_w, flat.width), flat.height))

    key = f"click_{fi}_{band_i}"
    pt = sic(crop, key=key)
    if pt:
        ex = box[0] + int(band_i) * seg_w + pt["x"]
        ey = box[1] + pt["y"]
        sx, sy, tid = snap(ex, ey, frame["detections"])
        st.session_state["pending"] = {
            "video_time_s": frame["video_time_s"],
            "click_x_eq": sx, "click_y_eq": sy,
            "raw_x_eq": ex, "raw_y_eq": ey,
            "snapped_track_id": tid,
        }

    pend = st.session_state.get("pending")
    if pend:
        st.success(f"Clicked at ({pend['click_x_eq']:.0f}, {pend['click_y_eq']:.0f})"
                   + (f" — snapped to track {pend['snapped_track_id']}"
                      if pend["snapped_track_id"] is not None else " — raw click"))
        st.write("**Who is it?**")
        # Only the players the coach's log says were ON THE PITCH at this
        # instant: 7 of a 12-strong squad, not the 16-name club roster.
        on_now = onfield_at(onfield_iv, float(frame["video_time_s"]))
        choices = [by_id.get(p, {"id": p, "name": p}) for p in on_now]
        if not choices:
            st.warning("The coach log shows nobody on the pitch at this instant "
                       "(kickoff boundary or a missing SUB tap) — showing the "
                       "whole squad.")
            choices = [by_id.get(p, {"id": p, "name": p}) for p in sorted(squad)]
        cols = st.columns(min(4, len(choices)))
        for i, p in enumerate(choices):
            label = p.get("name", p["id"])
            if p["id"] == gk_id:
                label += " (GK)"
            if cols[i % len(cols)].button(label, key=f"pick_{p['id']}_{fi}_{band_i}",
                                          use_container_width=True):
                append_sample(root, {**pend, "player_id": p["id"]})
                st.session_state.pop("pending", None)
                st.rerun()
        with st.expander(f"not in these {len(choices)}? show whole squad"):
            # Escape hatch: a late or missed SUB tap would otherwise make the
            # right child unclickable. Recorded identically, so a correction here
            # is not second-class data.
            other = [by_id.get(p, {"id": p, "name": p})
                     for p in sorted(squad) if p not in {c["id"] for c in choices}]
            ocols = st.columns(4) if other else []
            for i, p in enumerate(other):
                if ocols[i % 4].button(p.get("name", p["id"]),
                                       key=f"pickx_{p['id']}_{fi}_{band_i}"):
                    append_sample(root, {**pend, "player_id": p["id"],
                                         "off_window": True})
                    st.session_state.pop("pending", None)
                    st.rerun()
        st.divider()
        c1, c2 = st.columns(2)
        # Both of these are first-class answers, not failures. Forcing a choice
        # is what produced 26 "can't tell" of 30 in the earlier composition pass.
        if c1.button("↩︎ can't tell — discard"):
            st.session_state.pop("pending", None)
            st.rerun()
        if c2.button("opponent / ref / adult"):
            append_sample(root, {**pend, "player_id": "__not_ours__"})
            st.session_state.pop("pending", None)
            st.rerun()


if __name__ == "__main__":
    main()

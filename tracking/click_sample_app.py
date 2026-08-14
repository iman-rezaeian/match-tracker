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
from PIL import Image, ImageDraw  # noqa: E402

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
        # The field is `jersey_number`, NOT `number`. Reading the wrong attribute
        # via getattr(..., None) failed silently and dropped every shirt number
        # from the buttons, which is exactly the failure mode a default hides.
        return [{"id": p.id, "name": getattr(p, "name", p.id),
                 "number": getattr(p, "jersey_number", None)} for p in players]
    except Exception as exc:  # pragma: no cover - UI convenience
        st.warning(f"roster unavailable ({type(exc).__name__}) — using generic ids")
        return [{"id": f"p{i}", "name": f"Player {i}", "number": i} for i in range(1, 13)]


def _num(p: dict) -> int:
    """Sort key: shirt number ascending, unnumbered last."""
    try:
        return int(str(p.get("number")))
    except (TypeError, ValueError):
        return 999


def button_label(p: dict, gk_id: str | None = None) -> str:
    """"#7 Liam Gibala" — number FIRST, because that is what is on the shirt.

    The coach reads the number off the kit in the band, then finds the matching
    button; leading with the number makes that a scan of one column rather than
    of the whole name.
    """
    n = p.get("number")
    label = f"#{n} {p.get('name', p['id'])}" if n else str(p.get("name", p["id"]))
    return label + " (GK)" if p["id"] == gk_id else label


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
    from post_game.identity import (_gk_segments, _onfield_intervals,
                                    period_clock_to_video_time_factory)
    game = firestore_io.get_game(game_id)
    c2v = period_clock_to_video_time_factory(game)
    # Keeper segments rather than the single game-wide `gk_player_id`, so the
    # (GK) suffix follows whoever is actually in the net. On this game there are
    # no changes and Garland kept throughout, but a game where the keeper rotates
    # would otherwise mark the wrong child for the whole match.
    return (_onfield_intervals(game.starting_lineup, game.events, c2v),
            _gk_segments(game.gk_player_id, game.gk_changes or []))


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


@st.cache_data(show_spinner=False)
def load_kickoff_offsets(game_id: str) -> tuple[float, float]:
    """(h1_kickoff_s, h2_kickoff_s) in VIDEO seconds, for the clock conversion."""
    try:
        from post_game import firestore_io
        g = firestore_io.get_game(game_id)
        return (float(getattr(g, "video_offset_h1_kickoff_s", 0.0) or 0.0),
                float(getattr(g, "video_offset_h2_kickoff_s", 0.0) or 0.0))
    except Exception:  # pragma: no cover - UI convenience
        return 0.0, 0.0


def video_to_elapsed_ms(t_video: float, h1_off: float, h2_off: float) -> int:
    """Video seconds -> match-clock milliseconds, which is what the coach's taps use.

    The clock RESTARTS at the second-half kickoff, so a frame after `h2_off`
    measures from there, not from H1. Treating the video timeline as one
    continuous clock would put every second-half frame far beyond the end of the
    match and match the wrong keeper segment.
    """
    if h2_off and t_video >= h2_off:
        return int(max(0.0, t_video - h2_off) * 1000)
    return int(max(0.0, t_video - h1_off) * 1000)


def gk_at(gk_segments: list[dict], elapsed_ms: int) -> str | None:
    """Whoever is in the net at `elapsed_ms` of match clock.

    ⚠ Segment bounds are MATCH-CLOCK MILLISECONDS (the coach's taps), not video
    seconds. Passing a video timestamp here would compare seconds against
    milliseconds and always return the starting keeper -- silently correct on a
    game with one keeper, silently wrong on any game where they rotate.
    """
    for seg in gk_segments or []:
        if seg["from"] <= elapsed_ms and (seg["to"] is None or elapsed_ms < seg["to"]):
            return seg["playerId"]
    return None


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
        onfield_iv, gk_segs = load_onfield(a.game_id)
    except Exception as exc:
        st.warning(f"on-field windows unavailable ({type(exc).__name__}) — "
                   "showing the whole roster")
        onfield_iv, gk_segs = {}, []
    # Offsets to convert a frame's VIDEO time into match-clock ms, which is what
    # the keeper segments are keyed on.
    h1_off, h2_off = load_kickoff_offsets(a.game_id)
    # The sidebar totals span the WHOLE game, so they mark the game's keeper
    # rather than whoever is in the net at the frame on screen. (The per-frame
    # keeper is resolved further down, once a frame is chosen.)
    game_gk_id = gk_segs[0]["playerId"] if gk_segs else None
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
        for pid in sorted(squad, key=lambda i: _num(by_id.get(i, {}))):
            n = counts.get(pid, 0)
            p = by_id.get(pid, {"id": pid, "name": pid})
            st.write(f"{'🟢' if n >= 50 else '🟡' if n >= 20 else '⚪'} "
                     f"{button_label(p, game_gk_id)}: {n}")
        st.divider()
        st.caption("Clicks must be SPREAD across the match. Frames are on a "
                   "fixed grid for that reason — please don't skip ahead to "
                   "the exciting bits, it biases every position.")

    # Resume where the last session stopped. Restarting at frame 0 made the app
    # re-show every frame already worked, with no sign that the earlier clicks
    # had been saved at all -- they had, but invisibly.
    if "fi" not in st.session_state:
        done_times = {round(float(s["video_time_s"]), 2) for s in done}
        # Resume AFTER the furthest frame worked, not at the first untouched one.
        # A deliberately skipped frame (nobody nameable, or all bands empty of
        # ours) is legitimately left blank, so "first blank" would send the coach
        # back to the start every session. The skipped frames stay reachable via
        # prev / the progress strip.
        last_done = max((i for i, f in enumerate(frames)
                         if round(float(f["video_time_s"]), 2) in done_times),
                        default=-1)
        st.session_state.fi = min(last_done + 1, len(frames) - 1)
        if done:
            st.toast(f"Resuming at frame {st.session_state.fi + 1} — "
                     f"{len(done)} clicks already saved across "
                     f"{len(done_times)} frame(s).")
    _c1, _c2, _c3 = st.columns([1, 1, 6])
    if _c1.button("◀ prev", disabled=st.session_state.fi <= 0):
        st.session_state.fi -= 1
        st.rerun()
    if _c2.button("next ▶", disabled=st.session_state.fi >= len(frames) - 1):
        st.session_state.fi += 1
        st.rerun()
    fi = _c3.number_input("frame", 0, len(frames) - 1,
                          st.session_state.fi, 1, key="fi")
    # A done/todo strip, so progress through the game is visible at a glance
    # rather than something the coach has to remember between sessions.
    _dt = {round(float(s["video_time_s"]), 2) for s in done}
    st.caption("progress  " + "".join(
        "●" if round(float(f["video_time_s"]), 2) in _dt else "○"
        for f in frames) + f"   ({len(_dt)}/{len(frames)} frames, {len(done)} clicks)")
    frame = frames[int(fi)]
    gk_id = gk_at(gk_segs, video_to_elapsed_ms(
        float(frame["video_time_s"]), h1_off, h2_off))
    st.caption(f"video t = {frame['video_time_s']:.1f}s "
               f"({frame['video_time_s']/60:.1f} min) — frame {int(fi)+1} of {len(frames)}")
    _on = onfield_at(onfield_iv, float(frame["video_time_s"]))
    if _on:
        st.info("**On the pitch now:** " + ", ".join(
            button_label(by_id.get(p, {"id": p, "name": p}), gk_id)
            for p in sorted(_on, key=lambda i: _num(by_id.get(i, {})))))

    # What has already been recorded on THIS frame, so the coach can see his own
    # work instead of guessing whether a click landed.
    this_frame = [s for s in done
                  if abs(float(s["video_time_s"]) - float(frame["video_time_s"])) < 0.01]
    if this_frame:
        st.success("**Already marked here:** " + ", ".join(
            button_label(by_id.get(s["player_id"], {"id": s["player_id"],
                                                    "name": s["player_id"]}))
            for s in this_frame))

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
    # Per-band tally of bodies worth clicking, so an empty band is visibly empty
    # rather than something the coach has to scan grass to rule out. Counts only
    # child-sized bodies inside the pitch and clear of the far-touchline band --
    # coaches standing at the touchline are excluded by size, and benched subs
    # are usually not detected at all.
    # ⚠ Count OUR players, not bodies. The first version counted every
    # child-sized body on the pitch and called them "players", so a band holding
    # nothing but the opposition advertised "3 players" and sent the coach
    # hunting for his own kids in it. The detector has no idea which team it is
    # looking at; the kit vote does.
    seg_px = (box[2] - box[0]) / max(1, bands)
    ours = [0] * bands
    other = [0] * bands
    for _d in frame["detections"]:
        if _d.get("bbox_h", 0) >= 120:
            continue
        if proj is not None and dims is not None:
            _L, _W = dims
            _fx, _fy = proj.pixel_to_field(_d["foot_x_eq"], _d["foot_y_eq"])
            if np.isnan(_fx) or not (-1.0 <= _fx <= _L + 1.0
                                     and FAR_TOUCHLINE_BAND_M <= _fy <= _W + 1.0):
                continue
        _b = min(bands - 1, max(0, int((_d["foot_x_eq"] - box[0]) // seg_px)))
        if _d.get("kit") == "opponent":
            other[_b] += 1
        else:
            # "ours" and "unknown" both count as ours: an unknown kit is still
            # possibly one of our children, and under-counting would hide a band
            # worth checking. Over-counting only costs a glance.
            ours[_b] += 1

    def _band_label(i: int) -> str:
        if not ours[i]:
            return (f"band {i+1} — none of ours ⊘" if other[i]
                    else f"band {i+1} — empty ⊘")
        return f"band {i+1} — {ours[i]} of ours"

    band_i = st.radio(
        "band", range(bands), horizontal=True, format_func=_band_label
    ) if bands > 1 else 0
    if not ours[int(band_i)]:
        st.warning(
            f"None of our players here{' (opposition only)' if other[int(band_i)] else ''}"
            " — skip to the next band, or the next frame if they're all empty.")
    seg_w = flat.width // bands
    crop = flat.crop((int(band_i) * seg_w, 0,
                      min((int(band_i) + 1) * seg_w, flat.width), flat.height))

    # Draw every click already recorded on this frame, plus the one awaiting a
    # name. Without this the coach has no way to tell who he has marked and ends
    # up either re-clicking the same child or skipping one.
    crop = crop.convert("RGB")
    _draw = ImageDraw.Draw(crop)
    _x0 = box[0] + int(band_i) * seg_w
    for s in this_frame:
        cx = float(s["click_x_eq"]) - _x0
        cy = float(s["click_y_eq"]) - box[1]
        if not (0 <= cx < crop.width and 0 <= cy < crop.height):
            continue          # recorded in a different band
        nm = by_id.get(s["player_id"], {}).get("number") or "?"
        _draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], outline=(0, 255, 60), width=4)
        _draw.text((cx + 20, cy - 8), f"#{nm}", fill=(0, 255, 60))
    _pend0 = st.session_state.get("pending")
    if _pend0 and abs(float(_pend0["video_time_s"]) - float(frame["video_time_s"])) < 0.01:
        px = float(_pend0["click_x_eq"]) - _x0
        py = float(_pend0["click_y_eq"]) - box[1]
        if 0 <= px < crop.width and 0 <= py < crop.height:
            _draw.ellipse([px - 18, py - 18, px + 18, py + 18],
                          outline=(255, 210, 0), width=4)
            _draw.text((px + 22, py - 8), "who?", fill=(255, 210, 0))

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
        st.rerun()            # redraw immediately so the yellow ring appears

    pend = st.session_state.get("pending")
    if pend:
        st.warning("⬤ **Marked in yellow on the image — now pick the name below.**")
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
        # Shirt-number order, so the buttons sit in the same sequence every frame
        # and the coach builds muscle memory instead of re-reading the row.
        choices.sort(key=_num)
        cols = st.columns(min(4, len(choices)))
        for i, p in enumerate(choices):
            if cols[i % len(cols)].button(button_label(p, gk_id),
                                          key=f"pick_{p['id']}_{fi}_{band_i}",
                                          use_container_width=True):
                append_sample(root, {**pend, "player_id": p["id"]})
                st.session_state.pop("pending", None)
                st.toast(f"✅ saved {button_label(p)}")
                st.rerun()
        with st.expander(f"not in these {len(choices)}? show whole squad"):
            # Escape hatch: a late or missed SUB tap would otherwise make the
            # right child unclickable. Recorded identically, so a correction here
            # is not second-class data.
            other = [by_id.get(p, {"id": p, "name": p})
                     for p in sorted(squad) if p not in {c["id"] for c in choices}]
            ocols = st.columns(4) if other else []
            other.sort(key=_num)
            for i, p in enumerate(other):
                if ocols[i % 4].button(button_label(p, gk_id),
                                       key=f"pickx_{p['id']}_{fi}_{band_i}"):
                    append_sample(root, {**pend, "player_id": p["id"],
                                         "off_window": True})
                    st.session_state.pop("pending", None)
                    st.toast(f"✅ saved {button_label(p)} (outside his logged window)")
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
            st.toast("✅ saved as not-ours")
            st.rerun()


if __name__ == "__main__":
    main()

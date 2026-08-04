"""Dense-swarm ID-stability tests for the meter-space PitchTracker.

THE B2 lesson made a test: the surrogate tracker passed a 3-well-separated-player
synthetic check and still shattered the real U10 swarm. So this exercises the hard
case directly — 5+ players crossing paths within <3 m — and asserts IDs do NOT
swap. Pure geometry, no video/Firestore/boxmot.

Run: `python -m post_game.test_tracking_pitch` (or pytest).
"""
from __future__ import annotations

import numpy as np

from .calibration import FieldCalibration, FieldProjector
from .detection import Detection
from .tracking_pitch import PitchTracker, _det_kit_color, _hue_from_hex

EQ_W, EQ_H = 5760, 2880
L, W = 54.0, 34.0

# The two Stompers-vs-blue kit hexes this fix is tuned on (green H71 / blue H111).
OUR_GREEN_HEX = "#16a34a"
OPP_BLUE_HEX = "#2563eb"
# BGR fills for painting jersey ROIs in a synthetic frame.
GREEN_BGR = (0x4a, 0xa3, 0x16)   # #16a34a
BLUE_BGR = (0xeb, 0x63, 0x25)    # #2563eb
GRASS_BGR = (60, 140, 40)        # a saturated pitch-green (must NOT read as our kit)


def _projector() -> FieldProjector:
    sphere = {"a": 1.0, "b": 0.0, "tx": 0.0, "ty": 0.0, "cam_h_m": 5.0,
              "pitch_deg": 0.0, "roll_deg": 0.0, "eq_w": EQ_W, "eq_h": EQ_H}
    cal = FieldCalibration("syn", L, W, [(0, 0)] * 4,
                           [(0, 0), (L, 0), (L, W), (0, W)],
                           [[1, 0, 0], [0, 1, 0], [0, 0, 1]], (EQ_W, EQ_H), sphere)
    return FieldProjector(cal)


def _det(proj: FieldProjector, x_m: float, y_m: float, fi: int) -> Detection:
    px, py = proj.field_to_pixel(x_m, y_m)
    box = (px - 8, py - 32, px + 8, py)
    return Detection(frame_index=fi, cls=0, confidence=0.9, bbox_crop=box, bbox_eq=box)


def test_no_id_swaps_through_a_dense_crossing():
    """6 players in a <3 m cluster; two of them swap sides (paths cross). Each GT
    player must keep ONE dominant track_id across the whole sequence."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    fps = 10.0
    n = 30
    y0 = W / 2.0
    # 6 GT players. Two cross (p0 moves +x, p1 moves -x, starting 2.5 m apart);
    # the other four weave slowly inside the same cluster.
    def gt_pos(pi, f):
        t = f / fps
        if pi == 0:  # crosses left->right
            return (23.0 + 2.0 * t, y0)
        if pi == 1:  # crosses right->left (paths cross p0 mid-sequence)
            return (26.0 - 2.0 * t, y0)
        # weavers within the cluster, small oscillation, <3 m spacing
        base = 24.0 + (pi - 2) * 0.8
        return (base + 0.3 * np.sin(t * 2 + pi), y0 + 0.5 * (pi - 3))

    seen = {pi: [] for pi in range(6)}
    for f in range(n):
        dets = [_det(proj, *gt_pos(pi, f), f) for pi in range(6)]
        out = trk.update(np.zeros((10, 10, 3), np.uint8), dets, time_s=f / fps)
        # map each emitted track back to which GT player it came from by matching
        # bbox_eq to the det we generated (same order preserved isn't guaranteed, so
        # match by nearest foot pixel).
        for pi in range(6):
            gx, gy = proj.field_to_pixel(*gt_pos(pi, f))
            best, bestd = None, 1e9
            for td in out:
                fx = (td.bbox_eq[0] + td.bbox_eq[2]) / 2
                fy = td.bbox_eq[3]
                dd = (fx - gx) ** 2 + (fy - gy) ** 2
                if dd < bestd:
                    bestd, best = dd, td.track_id
            if best is not None:
                seen[pi].append(best)

    # Each GT player should be dominated by a single track_id for >=90% of frames.
    swaps = []
    for pi, ids in seen.items():
        if not ids:
            continue
        dominant = max(set(ids), key=ids.count)
        frac = ids.count(dominant) / len(ids)
        if frac < 0.90:
            swaps.append((pi, frac, len(set(ids))))
    assert not swaps, f"ID instability (player, dominant_frac, #ids): {swaps}"


def test_teleport_detection_spawns_new_id_not_a_steal():
    """A detection 5 m from any track (>> the ~1 m/frame gate) must start a NEW
    track, never hijack an existing one."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    # establish one steady track over a few frames
    for f in range(5):
        trk.update(np.zeros((10, 10, 3), np.uint8), [_det(proj, 20.0, 17.0, f)], time_s=f / 10.0)
    ids_before = {t.track_id for t in trk._tracks}
    # next frame: the real track continues AND a detection appears 5 m away
    out = trk.update(np.zeros((10, 10, 3), np.uint8),
                     [_det(proj, 20.1, 17.0, 5), _det(proj, 25.1, 17.0, 5)], time_s=0.5)
    tids = {td.track_id for td in out}
    assert len(tids) == 2, tids                    # two distinct tracks this frame
    assert len(trk._tracks) >= 2                    # a genuinely new track was created
    # the far detection must be a NEW id, not one of the pre-existing ones reused
    new_ids = tids - ids_before
    assert len(new_ids) >= 1, "teleport det did not spawn a new id"


def test_above_horizon_detection_is_kept_not_dropped():
    """An above-horizon (NaN-projecting) foot must still be emitted and counted —
    B2 silently dropped these and bled far-touchline coverage."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    above = Detection(frame_index=0, cls=0, confidence=0.9,
                      bbox_crop=(100, 5, 120, 20), bbox_eq=(100, 5, 120, 20))
    out = trk.update(np.zeros((10, 10, 3), np.uint8), [above], time_s=0.0)
    assert len(out) == 1, "above-horizon det was dropped"
    assert trk.n_kept_unprojectable == 1


def test_track_with_no_in_gate_detection_does_not_crash():
    """Regression: linear_sum_assignment raises on an all-infinite row, which
    happens when an existing track has NO detection within its gate this frame
    (common on real data). The tracker must handle it — the track goes unmatched
    (ages toward lost), the far detection spawns its own track, no exception."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    # establish a track at ~(20,17)
    for f in range(4):
        trk.update(np.zeros((10, 10, 3), np.uint8), [_det(proj, 20.0, 17.0, f)], time_s=f / 10.0)
    # next frame: the ONLY detection is far away (out of the established track's gate)
    out = trk.update(np.zeros((10, 10, 3), np.uint8), [_det(proj, 40.0, 25.0, 4)], time_s=0.4)
    assert len(out) == 1                      # emitted the far det (as a new track)
    assert len(trk._tracks) >= 2              # old track kept (aging), new one spawned


def test_many_frames_stress_no_crash():
    """Drive a moderately dense scene for many frames with players entering/leaving
    to exercise all-inf rows, empty layers, spawns and deletions — must not raise."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=30)
    for f in range(60):
        dets = []
        # 4 steady players + 1 that blinks in/out every few frames
        for pi in range(4):
            dets.append(_det(proj, 15.0 + pi * 6 + 0.2 * np.sin(f / 5), 17.0, f))
        if f % 7 < 4:
            dets.append(_det(proj, 45.0, 10.0, f))  # intermittent far player
        trk.update(np.zeros((10, 10, 3), np.uint8), dets, time_s=f / 10.0)
    # no assertion beyond "did not raise"; a smoke stress test


def test_bbox_eq_invariant_and_contract():
    """bbox_eq is passed through untouched; empty-in returns []."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    assert trk.update(np.zeros((10, 10, 3), np.uint8), [], time_s=0.0) == []
    d = _det(proj, 20.0, 17.0, 0)
    out = trk.update(np.zeros((10, 10, 3), np.uint8), [d], time_s=0.0)
    assert len(out) == 1
    assert out[0].bbox_eq == d.bbox_eq            # true equirect box preserved
    assert out[0].frame_index == 0
    assert out[0].appearance_embedding is None    # v1 motion-only


# --------------------------------------------------------------------------
# Team-color association gate (PITCH_COLOR_GATE). These paint real green/blue
# jersey pixels into a synthetic frame so _det_kit_color actually reads them.
# --------------------------------------------------------------------------

def _frame() -> np.ndarray:
    """A blank equirect-sized frame to paint jersey ROIs into (BGR)."""
    return np.zeros((EQ_H, EQ_W, 3), np.uint8)


def _paint_jersey(frame: np.ndarray, bbox_eq, bgr) -> None:
    """Fill a detection's jersey ROI (the same 0.18-0.50h x 0.28-0.72w window
    _det_kit_color samples) with a solid BGR color."""
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_eq)
    h_box, w_box = y2 - y1, x2 - x1
    jy1 = max(0, y1 + int(0.18 * h_box)); jy2 = min(frame.shape[0], y1 + int(0.50 * h_box))
    jx1 = max(0, x1 + int(0.28 * w_box)); jx2 = min(frame.shape[1], x2 - int(0.28 * w_box))
    frame[jy1:jy2, jx1:jx2] = bgr


def _cdet(proj, x_m, y_m, fi, frame, bgr):
    """A detection at (x_m,y_m) whose jersey ROI is painted `bgr` in `frame`."""
    d = _det(proj, x_m, y_m, fi)
    _paint_jersey(frame, d.bbox_eq, bgr)
    return d


def test_det_kit_color_reads_green_not_just_blue():
    """THE grass-drop regression: our GREEN kit (#16a34a, H71 S221) must read +1.
    A naive grass drop (H35-85 & S>60, as sample_jersey_hsv does) would delete
    the saturated-green kit entirely and make this a blue-only gate — this test
    fails loudly if that drop ever creeps back in. Blue reads -1; a truly
    desaturated (grey) ROI abstains (0)."""
    proj = _projector()
    our_h, opp_h = _hue_from_hex(OUR_GREEN_HEX), _hue_from_hex(OPP_BLUE_HEX)
    assert 65 <= our_h <= 78, our_h     # green really is ~71
    assert 104 <= opp_h <= 118, opp_h   # blue really is ~111
    box = (1000, 1000, 1000 + 16, 1000 + 40)  # >=14 px tall, >=4 wide

    fr = _frame(); _paint_jersey(fr, box, GREEN_BGR)
    assert _det_kit_color(fr, box, our_h, opp_h, 35.0, 10, 6.0) == +1, "green kit read as not-ours"

    fr = _frame(); _paint_jersey(fr, box, BLUE_BGR)
    assert _det_kit_color(fr, box, our_h, opp_h, 35.0, 10, 6.0) == -1, "blue kit read as not-opp"

    # Grass shares green's hue basin and is inseparable from the green kit by
    # color alone — the CENTRAL TORSO ROI (mostly jersey) is what keeps grass
    # from dominating, not a hue filter. Grass therefore reads +1 ("our-ish"),
    # which is acceptable (it never flips a green player to blue). Documented so
    # the intent is explicit, not an accidental pass.
    fr = _frame(); _paint_jersey(fr, box, GRASS_BGR)
    assert _det_kit_color(fr, box, our_h, opp_h, 35.0, 10, 6.0) != -1, "grass read as OPP kit"

    fr = _frame(); _paint_jersey(fr, box, (90, 90, 90))  # grey / desaturated
    assert _det_kit_color(fr, box, our_h, opp_h, 35.0, 10, 6.0) == 0, "desaturated frame voted a kit"


def test_committed_green_track_rejects_a_colocated_blue_detection():
    """The swap-prevention core: a track that has committed to GREEN must NOT
    absorb a BLUE detection that appears right where it is (well inside the
    motion gate) — it must spawn a new track instead. This is the exact
    green<->blue mixing the fix targets."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100,
                       our_color_hex=OUR_GREEN_HEX, opp_color_hex=OPP_BLUE_HEX)
    assert trk.color_gate
    # Build a green track over enough frames to COMMIT (|score| >= 3).
    for f in range(6):
        fr = _frame()
        d = _cdet(proj, 20.0, 17.0, f, fr, GREEN_BGR)
        trk.update(fr, [d], time_s=f / 10.0)
    green_ids = {t.track_id for t in trk._tracks}
    assert green_ids and max(abs(t.color_score) for t in trk._tracks) >= 3

    # Next frame: a BLUE detection appears 0.5 m away (well inside the ~3.9 m gate).
    fr = _frame()
    dblue = _cdet(proj, 20.5, 17.0, 6, fr, BLUE_BGR)
    out = trk.update(fr, [dblue], time_s=0.6)
    assert len(out) == 1
    new_id = out[0].track_id
    assert new_id not in green_ids, "committed green track absorbed a blue detection (SWAP)"


def test_color_gate_off_when_no_kit_colors_given():
    """Motion-only fallback: constructing without kit hexes disables the color
    gate entirely (prod byte-behaviour), so a blue det right on a green track
    WOULD be absorbed (no color veto) — proving the gate is what changes it."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)  # no colors
    assert not trk.color_gate
    for f in range(6):
        fr = _frame()
        trk.update(fr, [_cdet(proj, 20.0, 17.0, f, fr, GREEN_BGR)], time_s=f / 10.0)
    ids_before = {t.track_id for t in trk._tracks}
    fr = _frame()
    out = trk.update(fr, [_cdet(proj, 20.3, 17.0, 6, fr, BLUE_BGR)], time_s=0.6)
    # motion-only: the near det continues the existing track (no color veto)
    assert out[0].track_id in ids_before, "color gate leaked into the no-color path"


def test_unknown_color_detection_never_rejected():
    """Fail-safe: a desaturated/ambiguous detection (color unknown) must be
    matched on motion alone — color never rejects on absence of color."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100,
                       our_color_hex=OUR_GREEN_HEX, opp_color_hex=OPP_BLUE_HEX)
    for f in range(6):
        fr = _frame()
        trk.update(fr, [_cdet(proj, 20.0, 17.0, f, fr, GREEN_BGR)], time_s=f / 10.0)
    ids_before = {t.track_id for t in trk._tracks}
    fr = _frame()
    # grey jersey -> unknown color; sits on the green track
    out = trk.update(fr, [_cdet(proj, 20.2, 17.0, 6, fr, (90, 90, 90))], time_s=0.6)
    assert out[0].track_id in ids_before, "unknown-color det was rejected instead of matched on motion"


def test_gate_cap_bounds_stale_track_reach():
    """The gate-growth cap: a track lost for ~2 s must NOT reacquire a body far
    enough away that the UNCAPPED gate (9*0.1*(tsu+1)+3 ~ 22 m at tsu=20) would
    have admitted but the cap (6 m) forbids. Without color (isolate the cap)."""
    import numpy as _np
    proj = _projector()
    # No kit colors -> color gate OFF, so this isolates the motion gate cap.
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=300)  # long buffer
    # establish a track at (20,17)
    for f in range(4):
        trk.update(_np.zeros((10, 10, 3), _np.uint8), [_det(proj, 20.0, 17.0, f)], time_s=f / 10.0)
    est_ids = {t.track_id for t in trk._tracks}
    # 25 empty frames -> the track goes stale (tsu ~ 25) but stays alive (buffer)
    for f in range(4, 29):
        trk.update(_np.zeros((10, 10, 3), _np.uint8), [], time_s=f / 10.0)
    # a detection appears 10 m away: uncapped gate (~26 m) would grab it; cap (6 m) must not.
    out = trk.update(_np.zeros((10, 10, 3), _np.uint8), [_det(proj, 30.0, 17.0, 29)], time_s=2.9)
    assert out[0].track_id not in est_ids, "stale track reached 10 m past the gate cap (swap)"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} pitch-tracker tests passed.")

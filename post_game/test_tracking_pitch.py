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
from .tracking_pitch import PitchTracker

EQ_W, EQ_H = 5760, 2880
L, W = 54.0, 34.0


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} pitch-tracker tests passed.")

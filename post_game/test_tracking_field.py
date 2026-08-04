"""Unit tests for the field-space tracker's surrogate mapping (accuracy-audit B2).

Pure-function / geometry tests — no video, no Firestore, no boxmot construction
for the mapping tests. Run standalone: `python -m post_game.test_tracking_field`
or under pytest: `pytest post_game/test_tracking_field.py -q`.

The property under test is EXACTLY what equirect breaks and the surrogate fixes:
a target moving at a constant field velocity produces a constant velocity in
surrogate space regardless of latitude (equirect row), whereas its raw equirect
pixel velocity is not constant across latitudes. That non-constancy is what makes
BoT-SORT's constant-velocity Kalman gate miss and fragment tracks.
"""
from __future__ import annotations

import numpy as np

from .calibration import FieldCalibration, FieldProjector
from .detection import Detection
from .tracking_field import (
    FieldSpaceTracker,
    _field_to_surrogate_xy,
    SURROGATE_PX_PER_M,
    SURROGATE_MARGIN_M,
    SURROGATE_BOX_M,
)


LENGTH_M, WIDTH_M = 54.0, 34.0
EQ_W, EQ_H = 5760, 2880


def _synthetic_projector() -> FieldProjector:
    """A calibrated sphere projector with no Firestore dependency.

    Identity similarity (a=1,b=0,tx=ty=0), camera 5 m up, no tilt — enough to
    exercise pixel_to_field / field_to_pixel end to end.
    """
    sphere = {
        "a": 1.0, "b": 0.0, "tx": 0.0, "ty": 0.0,
        "cam_h_m": 5.0, "pitch_deg": 0.0, "roll_deg": 0.0,
        "eq_w": EQ_W, "eq_h": EQ_H,
    }
    cal = FieldCalibration(
        name="synthetic",
        length_m=LENGTH_M, width_m=WIDTH_M,
        src_points_px=[(0, 0), (0, 0), (0, 0), (0, 0)],
        dst_points_m=[(0, 0), (LENGTH_M, 0), (LENGTH_M, WIDTH_M), (0, WIDTH_M)],
        homography=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        video_frame_size=(EQ_W, EQ_H),
        sphere=sphere,
    )
    return FieldProjector(cal)


def _det_at_field(proj: FieldProjector, x_m: float, y_m: float) -> Detection:
    """A Detection whose bbox_eq foot point projects to field (x_m, y_m)."""
    px, py = proj.field_to_pixel(x_m, y_m)
    # A small box whose bottom-center (foot) is (px, py); _surrogate_bbox uses
    # foot_x=(x1+x2)/2, foot_y=y2.
    return Detection(
        frame_index=0, cls=0, confidence=0.9,
        bbox_crop=(px - 10, py - 40, px + 10, py),
        bbox_eq=(px - 10, py - 40, px + 10, py),
    )


def test_surrogate_velocity_constant_across_latitudes():
    """Constant field velocity -> constant surrogate velocity at BOTH a near and
    a far depth band (the invariant equirect violates)."""
    proj = _synthetic_projector()
    step_m = 2.0  # constant field displacement per frame, along +x (goal-to-goal)
    y_center = WIDTH_M / 2.0

    def surrogate_dx(x0: float) -> float:
        d0 = _det_at_field(proj, x0, y_center)
        d1 = _det_at_field(proj, x0 + step_m, y_center)
        s0 = _surrogate_center(proj, d0)
        s1 = _surrogate_center(proj, d1)
        return s1[0] - s0[0]

    near_dx = surrogate_dx(8.0)    # near the camera baseline
    far_dx = surrogate_dx(40.0)    # far downfield -> a very different equirect row
    expected = step_m * SURROGATE_PX_PER_M

    assert abs(near_dx - expected) < 1e-3, (near_dx, expected)
    assert abs(far_dx - expected) < 1e-3, (far_dx, expected)
    assert abs(near_dx - far_dx) < 1e-3, "surrogate velocity must be latitude-invariant"


def test_equirect_velocity_is_NOT_constant_across_latitudes():
    """The contrast that documents the defect: the SAME constant field step maps
    to different equirect pixel displacements at near vs far bands, and the two
    bands sit on different equirect rows."""
    proj = _synthetic_projector()
    step_m = 2.0
    y_center = WIDTH_M / 2.0

    def equirect_dx_and_row(x0: float):
        px0, py0 = proj.field_to_pixel(x0, y_center)
        px1, _ = proj.field_to_pixel(x0 + step_m, y_center)
        return abs(px1 - px0), py0

    near_dpx, near_row = equirect_dx_and_row(8.0)
    far_dpx, far_row = equirect_dx_and_row(40.0)

    # Genuinely different latitudes (the test actually crosses rows).
    assert abs(near_row - far_row) > 50.0, (near_row, far_row)
    # Equirect pixel velocity is NOT constant — differs well beyond 20%.
    assert abs(near_dpx - far_dpx) / max(near_dpx, far_dpx) > 0.20, (near_dpx, far_dpx)


def test_surrogate_origin_offset_nonnegative():
    """Field (0,0) maps to the margin origin, keeping off-field points >= 0."""
    sx, sy = _field_to_surrogate_xy(np.array([0.0]), np.array([0.0]))
    assert float(sx[0]) == SURROGATE_MARGIN_M * SURROGATE_PX_PER_M
    assert float(sy[0]) == SURROGATE_MARGIN_M * SURROGATE_PX_PER_M
    # A modestly off-field point (run-up behind the line) stays non-negative.
    sx2, _ = _field_to_surrogate_xy(np.array([-5.0]), np.array([-5.0]))
    assert float(sx2[0]) >= 0.0


def test_above_horizon_detection_is_dropped():
    """A foot ray at/above the horizon has no field position -> _surrogate_bbox
    returns None (locks the drop contract that Step 3 exposes)."""
    tracker = _tracker(_synthetic_projector())
    # py above the equator (< EQ_H/2) with pitch=0 points at/above the horizon.
    above = Detection(
        frame_index=0, cls=0, confidence=0.9,
        bbox_crop=(100, 5, 120, 20), bbox_eq=(100, 5, 120, 20),
    )
    assert tracker._surrogate_bbox(above) is None


def test_surrogate_box_is_constant_size():
    """Constant metric box -> IoU association behaves as a metric distance gate."""
    proj = _synthetic_projector()
    tracker = _tracker(proj)
    for x0 in (8.0, 25.0, 45.0):
        d = _det_at_field(proj, x0, WIDTH_M / 2.0)
        sb = tracker._surrogate_bbox(d)
        assert sb is not None
        w = sb[2] - sb[0]
        h = sb[3] - sb[1]
        assert abs(w - SURROGATE_BOX_M * SURROGATE_PX_PER_M) < 1e-6
        assert abs(h - SURROGATE_BOX_M * SURROGATE_PX_PER_M) < 1e-6


def test_box_size_clears_iou_gate_for_realistic_motion():
    """Regression guard for the surrogate-box-sizing bug: consecutive boxes for a
    player at a realistic per-frame speed MUST overlap enough to clear BoT-SORT's
    IoU gate (match_thresh=0.8 => needs IoU >= 0.2), or tracks fragment every
    frame. A 1 m box moving 0.9 m/frame (the 9 m/s cap at 10 fps) has ZERO overlap
    — this locks SURROGATE_BOX_M large enough to prevent that regression."""
    box_px = SURROGATE_BOX_M * SURROGATE_PX_PER_M
    max_step_px = 0.9 * SURROGATE_PX_PER_M  # 9 m/s @ 10 fps = 0.9 m/frame
    inter = max(0.0, box_px - max_step_px)
    union = 2 * box_px * box_px - inter * box_px
    iou = (inter * box_px) / union if union > 0 else 0.0
    assert iou >= 0.2, (
        f"SURROGATE_BOX_M={SURROGATE_BOX_M} too small: IoU={iou:.3f} at the "
        f"9 m/s cap fails BoT-SORT's gate -> tracks would fragment."
    )


# --- helpers that need a tracker instance (constructs boxmot; venv has it) ---
_TRACKER_CACHE: list = []


def _tracker(proj: FieldProjector) -> FieldSpaceTracker:
    if not _TRACKER_CACHE:
        _TRACKER_CACHE.append(FieldSpaceTracker(proj, frame_rate=10, track_buffer_frames=100))
    return _TRACKER_CACHE[0]


def _surrogate_center(proj: FieldProjector, det: Detection) -> tuple[float, float]:
    sb = _tracker(proj)._surrogate_bbox(det)
    assert sb is not None
    return ((sb[0] + sb[2]) / 2.0, (sb[1] + sb[3]) / 2.0)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} field-space tracker tests passed.")

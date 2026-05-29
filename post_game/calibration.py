"""4-corner field homography (pixel ↔ meters).

Mirror of the JS solver in `soccer_team_app.jsx::solveHomography4Point` —
direct linear transform from 4 point correspondences. The React UI computes
and saves the homography; this module also recomputes it for tests + provides
the numpy-batched apply function the pipeline uses.
"""

from __future__ import annotations

import numpy as np

from .firestore_io import FieldCalibration


def compute_homography(
    src_points_px: list[tuple[float, float]],
    field_length_m: float,
    field_width_m: float,
) -> np.ndarray:
    if len(src_points_px) != 4:
        raise ValueError("Need exactly 4 source points (TL, TR, BR, BL).")
    dst = np.array(
        [[0, 0], [field_length_m, 0], [field_length_m, field_width_m], [0, field_width_m]],
        dtype=np.float64,
    )
    A = []
    for (x, y), (u, v) in zip(src_points_px, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    A = np.asarray(A, dtype=np.float64)
    # Solve Ah = 0 via SVD; h is the right-singular vector with smallest sv.
    _, _, vt = np.linalg.svd(A)
    h = vt[-1]
    H = h.reshape(3, 3)
    if abs(H[2, 2]) < 1e-12:
        raise ValueError("Degenerate homography (h33 ~ 0).")
    return H / H[2, 2]


def pixel_to_field(H: np.ndarray, x_px: float, y_px: float) -> tuple[float, float]:
    v = H @ np.array([x_px, y_px, 1.0])
    return (float(v[0] / v[2]), float(v[1] / v[2]))


def pixel_to_field_batch(H: np.ndarray, pts_px: np.ndarray) -> np.ndarray:
    if pts_px.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    H = np.asarray(H, dtype=np.float64)
    homog = np.column_stack([pts_px, np.ones(len(pts_px))])
    proj = homog @ H.T
    w = proj[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return proj[:, :2] / w


def validate(H: np.ndarray, pts_px: np.ndarray, field_length_m: float, field_width_m: float) -> dict:
    if len(pts_px) == 0:
        return {"in_field_pct": 0.0, "p95_outside_m": 0.0, "ok": False}
    xy = pixel_to_field_batch(H, pts_px)
    in_field = (
        (xy[:, 0] >= -1) & (xy[:, 0] <= field_length_m + 1)
        & (xy[:, 1] >= -1) & (xy[:, 1] <= field_width_m + 1)
    )
    outside = ~in_field
    if outside.any():
        dx = np.maximum(0, np.maximum(-xy[outside, 0], xy[outside, 0] - field_length_m))
        dy = np.maximum(0, np.maximum(-xy[outside, 1], xy[outside, 1] - field_width_m))
        p95 = float(np.percentile(np.sqrt(dx * dx + dy * dy), 95))
    else:
        p95 = 0.0
    pct = float(in_field.mean() * 100)
    return {"in_field_pct": pct, "p95_outside_m": p95, "ok": pct >= 75.0}


def calibration_to_doc(
    name: str,
    src_points_px: list[tuple[float, float]],
    field_length_m: float,
    field_width_m: float,
    video_frame_size: tuple[int, int],
) -> FieldCalibration:
    H = compute_homography(src_points_px, field_length_m, field_width_m)
    dst = [(0.0, 0.0), (field_length_m, 0.0), (field_length_m, field_width_m), (0.0, field_width_m)]
    return FieldCalibration(
        name=name,
        length_m=float(field_length_m),
        width_m=float(field_width_m),
        src_points_px=[(float(x), float(y)) for x, y in src_points_px],
        dst_points_m=dst,
        homography=H.tolist(),
        video_frame_size=(int(video_frame_size[0]), int(video_frame_size[1])),
    )

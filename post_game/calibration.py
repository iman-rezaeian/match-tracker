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
) -> FieldCalibration:    H = compute_homography(src_points_px, field_length_m, field_width_m)
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


def aim_from_calibration(
    src_points_px: list[tuple[float, float]],
    eq_w: int,
    eq_h: int,
    fov_margin: float = 1.15,
    max_fov_deg: float = 110.0,
    min_fov_deg: float = 60.0,
) -> tuple[float, float, float]:
    """Compute (lon_deg, lat_deg, fov_deg) of a virtual camera that just
    covers the calibration corners with a small margin.

    Each equirectangular pixel maps to a unit sphere direction. We average
    the four direction vectors (Cartesian) to get the field center, then
    measure the max angular distance from any corner to that center to set
    the FOV. Cartesian averaging handles the seam (longitude wrap) naturally.
    """
    if eq_w <= 0 or eq_h <= 0 or len(src_points_px) != 4:
        return (0.0, 0.0, max_fov_deg)

    # equirect pixel -> (lon, lat) radians
    pts_dir = []
    for (px, py) in src_points_px:
        lon = (px / eq_w - 0.5) * 2.0 * np.pi
        lat = (0.5 - py / eq_h) * np.pi
        cl, sl = np.cos(lat), np.sin(lat)
        pts_dir.append(np.array([cl * np.sin(lon), sl, cl * np.cos(lon)]))
    pts_dir = np.stack(pts_dir)

    center = pts_dir.mean(axis=0)
    center /= np.linalg.norm(center) + 1e-12

    # back to lon/lat
    lat_c = np.arcsin(np.clip(center[1], -1.0, 1.0))
    lon_c = np.arctan2(center[0], center[2])

    # angular radius = max angle from center to any corner
    cos_angles = np.clip(pts_dir @ center, -1.0, 1.0)
    max_angle_deg = float(np.degrees(np.arccos(cos_angles.min())))

    # Diagonal half-angle ≈ angular radius. Full FOV needs *2 * margin and
    # we use horizontal FOV ~= diagonal angle (good enough for our 16:9 crop).
    fov_deg = max(min_fov_deg, min(max_fov_deg, 2.0 * max_angle_deg * fov_margin))

    return (float(np.degrees(lon_c)), float(np.degrees(lat_c)), float(fov_deg))

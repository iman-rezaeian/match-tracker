"""Camera-tilt calibration solver (accuracy-audit B1).

The legacy flat calibrator forces the camera level (pitch = roll = 0) and then
fits only a 2D similarity to absorb the mismatch — so on a grazing single 360
camera the residual error is smeared across the field, worst far from the
camera, and biases every downstream distance/speed/position metric.

This module solves the tilt the projector was always ready to consume
(`calibration.FieldProjector` builds R = Rx(pitch) @ Rz(roll) but never received
a non-zero value). It jointly fits (pitch, roll, cam_h) plus the 2D similarity
(a, b, tx, ty) by minimizing reprojection error over the clicked reference
points.

Formulation — separable / variable-projection least squares: for a fixed
(pitch, roll) at the coach-measured camera height the ground points (Xc, Zc) are
determined, and the OPTIMAL similarity (a, b, tx, ty) is the closed-form Umeyama
fit. So we optimize only the 2 nonlinear tilt parameters and derive the 4 linear
ones in closed form at each step — fewer DOF, no scale/rotation ambiguity,
guaranteed-optimal linear part.

Camera height is NOT fitted: from coplanar ground points it is degenerate with
the similarity SCALE (you can trade height for scale and get the same ground
projection), so a fitted height is not independently identifiable and lands on a
random value while RMS stays low. Height is a physical measurement the coach
enters; we hold it fixed and let the similarity carry the residual scale.

The forward model here MUST match FieldProjector.pixel_to_field
(calibration.py:203-213) exactly, or we would optimize a different model than
the one that runs at inference.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares


def _rays_from_pixels(px: np.ndarray, py: np.ndarray, eq_w: int, eq_h: int) -> np.ndarray:
    """Equirect pixel -> unit camera-frame ray. Mirrors calibration.py:203-206."""
    lon = (px / eq_w) * 2.0 * np.pi - np.pi
    lat = np.pi / 2.0 - (py / eq_h) * np.pi
    cl = np.cos(lat)
    return np.stack([np.sin(lon) * cl, np.sin(lat), -np.cos(lon) * cl], axis=1)


def _rotation(pitch: float, roll: float) -> np.ndarray:
    """R = Rx(pitch) @ Rz(roll). Mirrors calibration.py:185-189."""
    cp, sp = np.cos(pitch), np.sin(pitch)
    cr, sr = np.cos(roll), np.sin(roll)
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    Rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return Rx @ Rz


def _ground_points(rays_cam: np.ndarray, pitch: float, roll: float, cam_h: float):
    """Rotate rays to world, intersect ground at y = -cam_h. Returns (Xc, Zc)
    (N,2) and a boolean mask of rays that actually hit the ground (point below
    the horizon). Mirrors calibration.py:207-211."""
    R = _rotation(pitch, roll)
    rw = rays_cam @ R.T  # (N,3): row-wise R @ ray
    ry = rw[:, 1]
    ok = ry < -1e-9  # ray must point below horizon
    t = np.where(ok, -cam_h / np.where(ok, ry, -1.0), 0.0)
    Xc = rw[:, 0] * t
    Zc = rw[:, 2] * t
    return np.stack([Xc, Zc], axis=1), ok


def _umeyama_similarity(src: np.ndarray, dst: np.ndarray):
    """Closed-form 2D similarity dst ~= [[a,b],[-b,a]] @ src + [tx,ty] (Umeyama).
    Mirrors the JS solveSimilarity2D. Returns (a, b, tx, ty) or None."""
    n = len(src)
    if n < 2:
        return None
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    ds = src - mu_s
    dd = dst - mu_d
    sxx = float(np.sum(ds[:, 0] * dd[:, 0] + ds[:, 1] * dd[:, 1]))
    syx = float(np.sum(ds[:, 0] * dd[:, 1] - ds[:, 1] * dd[:, 0]))
    var_s = float(np.sum(ds * ds))
    denom = np.hypot(sxx, syx)
    if denom < 1e-12 or var_s < 1e-12:
        return None
    cos, sin = sxx / denom, syx / denom
    s = denom / var_s
    a, b = s * cos, -s * sin
    tx = mu_d[0] - (a * mu_s[0] + b * mu_s[1])
    ty = mu_d[1] - (-b * mu_s[0] + a * mu_s[1])
    return a, b, tx, ty


def _apply_similarity(src: np.ndarray, a: float, b: float, tx: float, ty: float) -> np.ndarray:
    x = a * src[:, 0] + b * src[:, 1] + tx
    y = -b * src[:, 0] + a * src[:, 1] + ty
    return np.stack([x, y], axis=1)


def solve_sphere_tilt(reference_points, eq_w: int, eq_h: int,
                      cam_h: float = 5.0) -> dict | None:
    """Solve camera (pitch, roll) + 2D similarity from reference points at the
    given (fixed) camera height, minimizing reprojection error in field meters.

    reference_points: iterable of dicts with px, py, field_x_m, field_y_m
        (and optional key/label). Needs >= 4 usable points.
    cam_h: coach-measured camera height (meters), held FIXED (see module docstring
        — it is degenerate with similarity scale from coplanar points).
    Returns a dict with pitch_deg, roll_deg, cam_h_m, a, b, tx, ty, rms_m,
    per_point (list of {key, err_m}), n — or None if it cannot solve.
    """
    pts = []
    keys = []
    for r in reference_points or []:
        try:
            px = float(r["px"]); py = float(r["py"])
            fx = float(r["field_x_m"]); fy = float(r["field_y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((px, py, fx, fy))
        keys.append(r.get("key") or r.get("label") or f"p{len(keys)}")
    if len(pts) < 4:
        return None
    arr = np.array(pts, dtype=np.float64)
    rays = _rays_from_pixels(arr[:, 0], arr[:, 1], eq_w, eq_h)
    dst = arr[:, 2:4]

    def linear_fit(params):
        pitch, roll = params
        ground, ok = _ground_points(rays, pitch, roll, cam_h)
        if not ok.all():
            return None, ground, ok
        sim = _umeyama_similarity(ground, dst)
        return sim, ground, ok

    def residuals(params):
        sim, ground, ok = linear_fit(params)
        if sim is None:
            # Heavily penalize params that push points above the horizon or are
            # degenerate — steers the optimizer back to a valid basin.
            return np.full(len(dst) * 2, 1e3, dtype=np.float64)
        pred = _apply_similarity(ground, *sim)
        return (pred - dst).ravel()

    x0 = np.array([0.0, 0.0])
    # pitch/roll bounded to a physically sane grazing-camera range.
    bounds = ([np.deg2rad(-35.0), np.deg2rad(-35.0)],
              [np.deg2rad(35.0), np.deg2rad(35.0)])
    try:
        res = least_squares(residuals, x0, bounds=bounds, method="trf",
                            max_nfev=500, ftol=1e-10, xtol=1e-10)
    except Exception:
        return None

    pitch, roll = res.x
    sim, ground, ok = linear_fit(res.x)
    if sim is None:
        return None
    a, b, tx, ty = sim
    pred = _apply_similarity(ground, a, b, tx, ty)
    errs = np.hypot(pred[:, 0] - dst[:, 0], pred[:, 1] - dst[:, 1])
    rms = float(np.sqrt(np.mean(errs ** 2)))
    return {
        "pitch_deg": float(np.rad2deg(pitch)),
        "roll_deg": float(np.rad2deg(roll)),
        "cam_h_m": float(cam_h),
        "a": float(a), "b": float(b), "tx": float(tx), "ty": float(ty),
        "rms_m": rms,
        "per_point": [{"key": k, "err_m": float(e)} for k, e in zip(keys, errs)],
        "n": int(len(dst)),
    }

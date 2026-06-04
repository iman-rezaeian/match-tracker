"""Field calibration: pixel ↔ field-meter projection for the post-game pipeline.

Two models are supported:

1. **Sphere model** (preferred, written by `post_game.calibrate_flat`).
   For 360° equirect frames. Each pixel → 3D ray on the unit sphere →
   intersect with the ground plane at `y = -camera_height` after applying
   small pitch/roll correction → camera-frame ground point (Xc, Zc) →
   field coords via a 2D similarity (rotation+scale+translation).

   This is the **physically correct** model for our X5: planar
   homographies don't work because the touchlines project as sphere
   curves in equirect, not straight lines.

2. **Planar homography** (legacy, kept for backward compatibility).
   What the old JSX `FieldCalibrationModal` produced. Use only if
   `ground_similarity` is missing from the calibration doc.

The `FieldProjector` class exposes both directions
(`pixel_to_field` / `field_to_pixel`) so call sites don't need to know
which model is active.
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


# --- Sphere model + unified projector ------------------------------------

class FieldProjector:
    """Bidirectional pixel ↔ field-meters projection for one FieldCalibration.

    Chooses sphere model if `cal.sphere` is populated, else falls back to
    the planar homography in `cal.homography`. Either way, the public API
    is the same:

        proj = FieldProjector(field_cal)
        x_m, y_m = proj.pixel_to_field(px, py)           # single point
        xy = proj.pixel_to_field_batch(pts_px)            # (N,2) -> (N,2)
        px, py = proj.field_to_pixel(x_m, y_m)            # single point
    """

    def __init__(self, cal: FieldCalibration):
        self.cal = cal
        self.use_sphere = cal.sphere is not None
        if self.use_sphere:
            s = cal.sphere
            self.a = float(s["a"]); self.b = float(s["b"])
            self.tx = float(s["tx"]); self.ty = float(s["ty"])
            self.cam_h = float(s["cam_h_m"])
            self.pitch = float(np.deg2rad(s.get("pitch_deg", 0.0)))
            self.roll  = float(np.deg2rad(s.get("roll_deg",  0.0)))
            self.eq_w = int(s["eq_w"]); self.eq_h = int(s["eq_h"])
            # Pre-compute rotation matrix R = R_x(pitch) @ R_z(roll).
            # Rays in camera frame -> world frame: ray_world = R @ ray_cam.
            cp, sp = np.cos(self.pitch), np.sin(self.pitch)
            cr, sr = np.cos(self.roll),  np.sin(self.roll)
            Rx = np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])
            Rz = np.array([[cr,-sr,0],[sr,cr,0],[0,0,1]])
            self._R = Rx @ Rz
            self._Rt = self._R.T
            # Inverse similarity helpers: M = [[a,b],[-b,a]], det = a²+b²
            self._det = self.a*self.a + self.b*self.b
        else:
            self._H = np.asarray(cal.homography, dtype=np.float64)
            self._H_inv = np.linalg.inv(self._H)

    # ---- pixel -> field ----
    def pixel_to_field(self, px: float, py: float) -> tuple[float, float]:
        if not self.use_sphere:
            v = self._H @ np.array([px, py, 1.0])
            return float(v[0]/v[2]), float(v[1]/v[2])
        # equirect pixel -> sphere direction (camera frame)
        lon = (px / self.eq_w) * 2.0 * np.pi - np.pi
        lat = np.pi/2.0 - (py / self.eq_h) * np.pi
        cl = np.cos(lat)
        ray_cam = np.array([np.sin(lon)*cl, np.sin(lat), -np.cos(lon)*cl])
        ray_world = self._R @ ray_cam
        if ray_world[1] >= -1e-9:
            return (float('nan'), float('nan'))  # ray points to/above horizon
        t = -self.cam_h / ray_world[1]
        Xc, Zc = ray_world[0]*t, ray_world[2]*t
        return (self.a*Xc + self.b*Zc + self.tx,
                -self.b*Xc + self.a*Zc + self.ty)

    def pixel_to_field_batch(self, pts_px: np.ndarray) -> np.ndarray:
        """(N,2) px coords -> (N,2) field meters."""
        pts_px = np.asarray(pts_px, dtype=np.float64)
        if pts_px.size == 0:
            return np.zeros((0, 2), dtype=np.float64)
        if not self.use_sphere:
            return pixel_to_field_batch(self._H, pts_px)
        lon = (pts_px[:, 0] / self.eq_w) * 2.0 * np.pi - np.pi
        lat = np.pi/2.0 - (pts_px[:, 1] / self.eq_h) * np.pi
        cl = np.cos(lat)
        rays_cam = np.stack([np.sin(lon)*cl, np.sin(lat), -np.cos(lon)*cl], axis=1)
        rays_world = rays_cam @ self._R.T
        y = rays_world[:, 1]
        bad = y >= -1e-9
        y_safe = np.where(bad, -1e-9, y)
        t = -self.cam_h / y_safe
        Xc = rays_world[:, 0] * t
        Zc = rays_world[:, 2] * t
        out = np.column_stack([
            self.a*Xc + self.b*Zc + self.tx,
            -self.b*Xc + self.a*Zc + self.ty,
        ])
        if bad.any():
            out[bad] = np.nan
        return out

    # ---- field -> pixel ----
    def field_to_pixel(self, x_m: float, y_m: float) -> tuple[float, float]:
        if not self.use_sphere:
            v = self._H_inv @ np.array([x_m, y_m, 1.0])
            return float(v[0]/v[2]), float(v[1]/v[2])
        # Invert similarity to get (Xc, Zc)
        dx, dy = x_m - self.tx, y_m - self.ty
        Xc = ( self.a*dx - self.b*dy) / self._det
        Zc = ( self.b*dx + self.a*dy) / self._det
        # World ray to that ground point: (Xc, -cam_h, Zc), then to camera frame.
        ray_world = np.array([Xc, -self.cam_h, Zc])
        ray_cam = self._Rt @ ray_world
        n = np.linalg.norm(ray_cam)
        if n < 1e-12:
            return (float('nan'), float('nan'))
        ray_cam /= n
        lon = np.arctan2(ray_cam[0], -ray_cam[2])
        lat = np.arcsin(np.clip(ray_cam[1], -1.0, 1.0))
        px = ((lon + np.pi) / (2.0*np.pi)) * self.eq_w
        py = ((np.pi/2.0 - lat) / np.pi) * self.eq_h
        return float(px), float(py)

    def field_to_lonlat(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Field meters -> (lon_deg, lat_deg). Used by the TV-reel aim loop.

        For the sphere model this short-circuits the pixel round-trip and
        returns the intermediate spherical coords directly. For the planar
        fallback it derives lon/lat from the projected pixel.
        """
        if self.use_sphere:
            dx, dy = x_m - self.tx, y_m - self.ty
            Xc = ( self.a*dx - self.b*dy) / self._det
            Zc = ( self.b*dx + self.a*dy) / self._det
            ray_world = np.array([Xc, -self.cam_h, Zc])
            ray_cam = self._Rt @ ray_world
            n = np.linalg.norm(ray_cam)
            if n < 1e-12:
                return (float('nan'), float('nan'))
            ray_cam /= n
            lon = np.arctan2(ray_cam[0], -ray_cam[2])
            lat = np.arcsin(np.clip(ray_cam[1], -1.0, 1.0))
            return float(np.degrees(lon)), float(np.degrees(lat))
        # Planar fallback: need eq_w/eq_h from the cal to translate px->lonlat.
        eq_w, eq_h = self.cal.video_frame_size
        if not eq_w or not eq_h:
            return (float('nan'), float('nan'))
        px, py = self.field_to_pixel(x_m, y_m)
        lon = (px / eq_w) * 360.0 - 180.0
        lat = 90.0 - (py / eq_h) * 180.0
        return lon, lat


def compute_tile_aims(
    projector: "FieldProjector",
    field_length_m: float,
    field_width_m: float,
    n_tiles: int,
    fov_deg: float,
) -> list[tuple[float, float, float]]:
    """Tile the field horizontally with N overlapping perspective crops.

    Each tile aims at one of N evenly-spaced points along the field-X axis
    (across the y-midline of the pitch), projected to (lon, lat) by the
    sphere model. Returns a list of (lon_deg, lat_deg, fov_deg) suitable
    for `render_perspective` / `iter_frames`.

    The lat is the average of all tile lats so every crop has a consistent
    horizon position — players don't appear to vertically jump when a
    track is handed off between tiles.

    Used by pipeline stage 2 to capture the whole pitch from our
    centerline+3m+5m X5 mount, where one perspective crop can only see
    ~half the field at a usable pixel density.
    """
    if n_tiles < 1:
        raise ValueError(f"n_tiles must be >= 1, got {n_tiles}")
    aims: list[tuple[float, float, float]] = []
    # Aim centers at field-X frac = i/(N-1) so the outer tiles point at the
    # end-lines (x=0 and x=L). This is critical: from a centerline mount the
    # far touchline corners sit at lon ≈ ±84° but the midfield touchline is
    # only at ±31° — aiming the outer tiles at the end-lines (lon ≈ ±55°)
    # lets a single tile's FOV reach the corner. For N=1 we just aim at
    # the centroid.
    cy = field_width_m / 2.0
    lons_lats: list[tuple[float, float]] = []
    for i in range(n_tiles):
        if n_tiles == 1:
            frac = 0.5
        else:
            frac = i / (n_tiles - 1)
        cx = frac * field_length_m
        lon, lat = projector.field_to_lonlat(cx, cy)
        lons_lats.append((lon, lat))
    # Common lat = mean (consistent horizon across tiles).
    lat_common = float(np.mean([ll[1] for ll in lons_lats]))
    for lon, _lat in lons_lats:
        aims.append((float(lon), lat_common, float(fov_deg)))
    return aims


def dedupe_detections_by_field_position(
    dets: list,
    projector: "FieldProjector",
    dedupe_m: float,
) -> list:
    """Drop tile-overlap duplicates by foot-position proximity.

    Walks detections in confidence-descending order; for each one that
    survives, suppress any later detection whose foot is within `dedupe_m`
    meters on the ground. Foot = bottom-center of the equirect bbox.

    Detections that project off-field (NaN — ray points above the horizon
    or to the empty middle of an over-tilted camera) are kept by pixel-IoU
    instead so we don't lose legitimate near-bench picks.
    """
    if not dets:
        return []
    # Pre-compute foot pixels + field positions in equirect coords.
    foot_eq = np.array([
        [(d.bbox_eq[0] + d.bbox_eq[2]) * 0.5, d.bbox_eq[3]]
        for d in dets
    ], dtype=np.float64)
    foot_field = projector.pixel_to_field_batch(foot_eq)

    order = sorted(range(len(dets)), key=lambda i: -float(dets[i].confidence))
    kept_idx: list[int] = []
    suppressed = set()
    for i in order:
        if i in suppressed:
            continue
        kept_idx.append(i)
        fxi, fyi = foot_field[i]
        for j in order:
            if j == i or j in suppressed:
                continue
            fxj, fyj = foot_field[j]
            if np.isnan(fxi) or np.isnan(fxj):
                # Fallback to pixel IoU
                b1, b2 = dets[i].bbox_eq, dets[j].bbox_eq
                ix1 = max(b1[0], b2[0]); iy1 = max(b1[1], b2[1])
                ix2 = min(b1[2], b2[2]); iy2 = min(b1[3], b2[3])
                iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
                inter = iw * ih
                a1 = max(0.0, (b1[2]-b1[0])) * max(0.0, (b1[3]-b1[1]))
                a2 = max(0.0, (b2[2]-b2[0])) * max(0.0, (b2[3]-b2[1]))
                iou = inter / (a1 + a2 - inter + 1e-9)
                if iou > 0.4:
                    suppressed.add(j)
            else:
                if (fxj - fxi) ** 2 + (fyj - fyi) ** 2 < dedupe_m * dedupe_m:
                    suppressed.add(j)
    # Preserve original detection order among the kept set.
    keep_set = set(kept_idx)
    return [d for i, d in enumerate(dets) if i in keep_set]


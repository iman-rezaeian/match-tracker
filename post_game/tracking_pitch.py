"""Field-meter player tracker (DeepSORT-on-pitch) — the accuracy rebuild.

PROTOTYPE / flag-gated (`config.TRACK_PITCH`), default OFF; prod uses
`tracking.Tracker`. This is the standard fixed-camera-sports method the SoccerNet-
GSR / SoccerTrack winners use, and the correct version of what the B2 surrogate
tracker (`tracking_field.py`) tried to do.

Why this and not B2's surrogate box: B2 projected feet to meters but then handed
BoT-SORT a constant 4 m box and let IoU associate. At ~10 fps a U10 kid moves only
~0.9 m/frame, so a 4 m box is 4.4x the real move and overlaps the WRONG neighbors
in the swarm -> ID swaps -> MORE fragments (it regressed coverage 52%->28%). The
fix is to associate on the POINT distance in meters, hard-gated at the physical
per-frame step. Two kids 3 m apart are 3 m apart — never "overlapping boxes".

Design (DeepSORT, but metric 2-D instead of image-box):
  * Per track: a constant-velocity Kalman filter in FIELD METERS ([x,y,vx,vy]).
  * Gate: Euclidean point distance (meters) between each track's predicted position
    and each detection's projected foot, HARD-gated at MAX_PLAUSIBLE_SPEED_MS * dt *
    (frames since last update) + slack. Above the gate = impossible.
  * Matching cascade: match tracks seen most recently first, so a stale/occluded
    track can't steal a detection from a track that was just observed.
  * NaN / above-horizon detections are KEPT (emitted, spawn/continue a track by
    pixel fallback) and COUNTED — never silently dropped (B2's drop bled coverage).
  * Appearance (Re-ID) is OMITTED in v1: OSNet is near-noise on same-kit U10 kits.
    Motion carries association; the offline global stitch reassembles the rest.

Contract: same update(frame, detections, time_s) -> list[TrackedDetection] as
tracking.Tracker, and TrackedDetection.bbox_eq stays the TRUE equirect box so every
downstream stage (foot projection, stats) is unchanged.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import config
from .detection import Detection
from .tracking import TrackedDetection


def _foot_px(bbox_eq: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_eq
    return (x1 + x2) / 2.0, y2


class _KalmanMeters:
    """Minimal constant-velocity Kalman in 2-D field meters. State [x,y,vx,vy]."""

    def __init__(self, x_m: float, y_m: float, meas_noise: float, proc_noise: float):
        self.x = np.array([x_m, y_m, 0.0, 0.0], dtype=float)
        # generous initial velocity uncertainty; position known to ~meas_noise
        self.P = np.diag([meas_noise, meas_noise, 25.0, 25.0]).astype(float)
        self.r = float(meas_noise)
        self.q = float(proc_noise)

    def predict(self, dt: float) -> None:
        F = np.array([[1, 0, dt, 0],
                      [0, 1, 0, dt],
                      [0, 0, 1, 0],
                      [0, 0, 0, 1]], dtype=float)
        self.x = F @ self.x
        # process noise scaled by dt (acceleration-driven position/velocity growth)
        q = self.q
        Q = np.diag([q * dt * dt, q * dt * dt, q * dt, q * dt]).astype(float)
        self.P = F @ self.P @ F.T + Q

    def update(self, z_x: float, z_y: float) -> None:
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        R = np.diag([self.r, self.r]).astype(float)
        z = np.array([z_x, z_y], dtype=float)
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P

    @property
    def pos(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])


class _Track:
    __slots__ = ("track_id", "kf", "time_since_update", "hits", "confirmed",
                 "last_px", "unprojectable")

    def __init__(self, track_id: int, kf: Optional[_KalmanMeters],
                 last_px: tuple[float, float], unprojectable: bool):
        self.track_id = track_id
        self.kf = kf
        self.time_since_update = 0
        self.hits = 1
        self.confirmed = False
        self.last_px = last_px           # last equirect foot pixel (fallback for NaN dets)
        self.unprojectable = unprojectable


class PitchTracker:
    """Drop-in for tracking.Tracker that associates in field meters."""

    # confirm a tentative track after this many consecutive hits
    N_INIT = 3
    # measurement / process noise for the meter Kalman (metres, metres/s^2-ish)
    MEAS_NOISE_M = 0.5
    PROC_NOISE = 4.0
    # pixel-distance gate for the rare all-NaN (unprojectable) association fallback
    PX_GATE = 150.0

    def __init__(self, projector, *, frame_rate: int = 10, track_buffer_frames: int = 200):
        self.projector = projector
        self.frame_rate = max(1, int(frame_rate))
        self.max_age = int(track_buffer_frames)
        self.slack_m = float(config.STITCH_SLACK_M)
        self.max_speed = float(config.MAX_PLAUSIBLE_SPEED_MS)
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._last_time_s: Optional[float] = None
        self.n_kept_unprojectable = 0   # dets with no field pos we KEPT (not dropped)

    def _project(self, det: Detection) -> tuple[Optional[float], Optional[float], tuple[float, float]]:
        fx, fy = _foot_px(det.bbox_eq)
        mx, my = self.projector.pixel_to_field(fx, fy)
        if not (np.isfinite(mx) and np.isfinite(my)):
            return None, None, (fx, fy)
        return float(mx), float(my), (fx, fy)

    def update(self, frame: np.ndarray, detections: list[Detection], time_s: float) -> list[TrackedDetection]:
        if not detections:
            # still age tracks so a truly empty frame advances lost timers
            for t in self._tracks:
                t.time_since_update += 1
            self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
            self._last_time_s = time_s
            return []

        dt = (time_s - self._last_time_s) if self._last_time_s is not None else 1.0 / self.frame_rate
        if dt <= 0:
            dt = 1.0 / self.frame_rate
        self._last_time_s = time_s
        frame_index = detections[0].frame_index

        # 1. Project detections to meters (keep pixel fallback for NaN ones).
        meas = []  # (mx|None, my|None, px, det)
        for d in detections:
            mx, my, px = self._project(d)
            meas.append((mx, my, px, d))

        # 2. Predict all tracks forward to this frame.
        for t in self._tracks:
            if t.kf is not None:
                t.kf.predict(dt)

        # 3. Matching cascade: tracks with the SMALLEST time_since_update first,
        #    so a just-seen track claims its detection before a stale one can.
        unmatched_det = set(range(len(meas)))
        assigned: dict[int, int] = {}  # det_idx -> track_id
        proj_tracks = [t for t in self._tracks if t.kf is not None]
        depths = sorted({t.time_since_update for t in proj_tracks})
        for depth in depths:
            layer = [t for t in proj_tracks if t.time_since_update == depth and t.track_id not in assigned.values()]
            dets_here = [i for i in unmatched_det if meas[i][0] is not None]
            if not layer or not dets_here:
                continue
            # cost = metric distance; gate grows with how long a track's been unseen
            cost = np.full((len(layer), len(dets_here)), np.inf, dtype=float)
            for r, t in enumerate(layer):
                tx, ty = t.kf.pos
                gate = self.max_speed * dt * (t.time_since_update + 1) + self.slack_m
                for c, di in enumerate(dets_here):
                    mx, my, _, _ = meas[di]
                    d = float(np.hypot(mx - tx, my - ty))
                    if d <= gate:
                        cost[r, c] = d
            from scipy.optimize import linear_sum_assignment
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                if np.isfinite(cost[r, c]):
                    di = dets_here[c]
                    assigned[di] = layer[r].track_id
                    unmatched_det.discard(di)

        # 3b. Unprojectable (NaN) dets: try to CONTINUE an existing track by pixel
        #     proximity (fallback) rather than drop — B2's drop bled coverage.
        by_id = {t.track_id: t for t in self._tracks}
        for di in list(unmatched_det):
            mx, my, px, d = meas[di]
            if mx is not None:
                continue
            self.n_kept_unprojectable += 1
            best_t, best_d = None, self.PX_GATE
            for t in self._tracks:
                if t.track_id in assigned.values():
                    continue
                pd_ = float(np.hypot(px[0] - t.last_px[0], px[1] - t.last_px[1]))
                if pd_ < best_d:
                    best_d, best_t = pd_, t
            if best_t is not None:
                assigned[di] = best_t.track_id
                unmatched_det.discard(di)

        # 4. Apply matches: KF update + bookkeeping.
        matched_tids = set(assigned.values())
        for di, tid in assigned.items():
            t = by_id[tid]
            mx, my, px, d = meas[di]
            if t.kf is not None and mx is not None:
                t.kf.update(mx, my)
            t.time_since_update = 0
            t.hits += 1
            t.last_px = px
            if t.hits >= self.N_INIT:
                t.confirmed = True

        # 5. Age unmatched tracks.
        for t in self._tracks:
            if t.track_id not in matched_tids:
                t.time_since_update += 1

        # 6. Spawn new tracks for still-unmatched detections.
        for di in list(unmatched_det):
            mx, my, px, d = meas[di]
            kf = None
            unproj = mx is None
            if mx is not None:
                kf = _KalmanMeters(mx, my, self.MEAS_NOISE_M, self.PROC_NOISE)
            t = _Track(self._next_id, kf, px, unproj)
            self._next_id += 1
            self._tracks.append(t)
            assigned[di] = t.track_id

        # 7. Delete tracks lost longer than the buffer.
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]

        # 8. Emit one TrackedDetection per detection, carrying the TRUE bbox_eq.
        out: list[TrackedDetection] = []
        for di, (mx, my, px, d) in enumerate(meas):
            tid = assigned.get(di)
            if tid is None:
                continue  # (shouldn't happen — every det is matched or spawned)
            out.append(TrackedDetection(
                frame_index=frame_index,
                time_s=time_s,
                cls=d.cls,
                confidence=d.confidence,
                bbox_crop=d.bbox_crop,   # feeds bbox_h_crop only
                bbox_eq=d.bbox_eq,       # INVARIANT: true equirect box
                track_id=int(tid),
                appearance_embedding=None,  # v1: motion-only (same-kit ReID is noise)
            ))
        return out

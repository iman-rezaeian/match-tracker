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

import cv2
import numpy as np

from . import config
from .detection import Detection
from .tracking import TrackedDetection


def _foot_px(bbox_eq: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_eq
    return (x1 + x2) / 2.0, y2


def _hue_from_hex(hex_str: str) -> float:
    """OpenCV hue (0-179) of a kit hex — the tracker's team-color anchor."""
    h = (hex_str or "").lstrip("#")
    if len(h) != 6:
        return 90.0
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    bgr = np.uint8([[[b, g, r]]])
    return float(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0, 0, 0])


def _circ_dist(a: float, b: float) -> float:
    """Distance between two OpenCV hues on the 0-179 circle."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _det_kit_color(frame: np.ndarray, bbox_eq, our_h: float, opp_h: float,
                   min_s: float, min_px: int, margin: float) -> int:
    """+1 = our kit, -1 = opp kit, 0 = UNKNOWN, for ONE detection's jersey ROI.

    Discriminates OUR kit vs OPP kit by which of the two kit-hue anchors the
    ROI's saturated-pixel median hue is nearer to on the hue circle — an
    assignment-free green-vs-blue decision, NOT an absolute team_id.

    Crucially it does NOT grass-drop. sample_jersey_hsv drops H35-85 with S>60,
    which deletes our GREEN kit (#16a34a is H71 S221 — the kit itself is
    saturated green, indistinguishable from pitch grass by hue+saturation). So a
    grass drop here would erase exactly the class we most need to detect. Instead
    we sample the CENTRAL TORSO ROI (where the jersey dominates and grass/skin is
    minimal) and let the nearest-anchor decision carry it: a green player's torso
    reads ~H71 -> +1, a blue player's ~H111 -> -1. A neutral `margin` around the
    green/blue midpoint (~91) makes desaturated/washed frames ABSTAIN (return 0,
    never reject) — the fail-safe: we reject only on the *presence of the wrong*
    kit, never on the absence of color.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_eq)
    h_box, w_box = y2 - y1, x2 - x1
    if h_box < 14 or w_box < 4:
        return 0
    jy1 = y1 + int(0.18 * h_box)
    jy2 = y1 + int(0.50 * h_box)
    jx1 = x1 + int(0.28 * w_box)
    jx2 = x2 - int(0.28 * w_box)
    jy1 = max(0, jy1); jx1 = max(0, jx1)
    jy2 = min(frame.shape[0], jy2); jx2 = min(frame.shape[1], jx2)
    if jx2 <= jx1 or jy2 <= jy1:
        return 0
    hsv = cv2.cvtColor(frame[jy1:jy2, jx1:jx2], cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    h, s = hsv[:, 0], hsv[:, 1]
    keep = s >= min_s   # only chromatic pixels carry a hue; NO grass drop (see above)
    if int(keep.sum()) < min_px:
        return 0
    hue = float(np.median(h[keep]))
    d_our = _circ_dist(hue, our_h)
    d_opp = _circ_dist(hue, opp_h)
    if d_opp - d_our >= margin:
        return 1
    if d_our - d_opp >= margin:
        return -1
    return 0


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
                 "last_px", "unprojectable", "color_score")

    def __init__(self, track_id: int, kf: Optional[_KalmanMeters],
                 last_px: tuple[float, float], unprojectable: bool):
        self.track_id = track_id
        self.kf = kf
        self.time_since_update = 0
        self.hits = 1
        self.confirmed = False
        self.last_px = last_px           # last equirect foot pixel (fallback for NaN dets)
        self.unprojectable = unprojectable
        # Running team-color vote: +1 per matched our-kit(green) frame, -1 per
        # opp-kit(blue) frame, 0 for unknown. Clipped in update(); a track only
        # asserts color once |score| >= commit threshold. Seeded at spawn.
        self.color_score = 0


class PitchTracker:
    """Drop-in for tracking.Tracker that associates in field meters."""

    # confirm a tentative track after this many consecutive hits
    N_INIT = 3
    # measurement / process noise for the meter Kalman (metres, metres/s^2-ish)
    MEAS_NOISE_M = 0.5
    PROC_NOISE = 4.0

    def __init__(self, projector, *, frame_rate: int = 10, track_buffer_frames: int = 200,
                 our_color_hex: Optional[str] = None, opp_color_hex: Optional[str] = None):
        self.projector = projector
        self.frame_rate = max(1, int(frame_rate))
        self.max_age = int(track_buffer_frames)
        # slack + gate cap: PITCH_* under TRACK_PITCH (env-overridable), else the
        # legacy STITCH_SLACK_M so a non-flagged construction is byte-unchanged.
        self.slack_m = float(config.PITCH_SLACK_M if config.TRACK_PITCH else config.STITCH_SLACK_M)
        self.gate_cap_m = float(config.PITCH_GATE_CAP_M)
        self.max_speed = float(config.MAX_PLAUSIBLE_SPEED_MS)
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._last_time_s: Optional[float] = None
        self.n_kept_unprojectable = 0   # dets with no field pos we KEPT (not dropped)
        # NaN pixel-fallback clamp.
        self.px_gate = float(config.PITCH_PX_GATE)
        self.nan_max_tsu = int(config.PITCH_NAN_MAX_TSU)
        # Team-color association gate (needs both kit hexes; else stays motion-only).
        self.color_gate = (bool(config.PITCH_COLOR_GATE)
                           and our_color_hex is not None and opp_color_hex is not None)
        self.our_h = _hue_from_hex(our_color_hex) if our_color_hex else 71.0
        self.opp_h = _hue_from_hex(opp_color_hex) if opp_color_hex else 111.0
        self.c_min_s = float(config.PITCH_COLOR_MIN_S)
        self.c_min_px = int(config.PITCH_COLOR_MIN_PIXELS)
        self.c_margin = float(config.PITCH_COLOR_MARGIN_DEG)
        self.c_commit = int(config.PITCH_COLOR_COMMIT_VOTES)
        self.c_clip = int(config.PITCH_COLOR_COMMIT_CLIP)
        self.c_max_tsu = int(config.PITCH_COLOR_MAX_TSU)
        self.color_penalty_m = float(config.PITCH_COLOR_PENALTY_M)

    def _track_color(self, t: _Track) -> int:
        """A track's asserted kit sign (+1 our / -1 opp / 0 none) for gating.

        Only a COMMITTED (|score| >= c_commit) and not-too-stale track asserts
        color; a stale track's frozen color memory must not veto a reacquire."""
        if abs(t.color_score) >= self.c_commit and t.time_since_update <= self.c_max_tsu:
            return 1 if t.color_score > 0 else -1
        return 0

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

        # 1. Project detections to meters (keep pixel fallback for NaN ones) and
        #    read each detection's kit color once (green/blue/unknown) for the
        #    team-color association gate. Sampling here (not per-pair) keeps it
        #    one cv2 call per detection.
        meas = []  # (mx|None, my|None, px, det, col)
        for d in detections:
            mx, my, px = self._project(d)
            col = (_det_kit_color(frame, d.bbox_eq, self.our_h, self.opp_h,
                                  self.c_min_s, self.c_min_px, self.c_margin)
                   if self.color_gate else 0)
            meas.append((mx, my, px, d, col))

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
            # cost = metric distance; gate grows with how long a track's been unseen.
            # linear_sum_assignment raises on an all-infinite row/col, which happens
            # whenever a track has NO in-gate detection this layer. So use a large
            # FINITE sentinel for out-of-gate pairs (the solver always succeeds),
            # then reject any assignment that landed on a sentinel (never in-gate).
            BIG = 1e6
            cost = np.full((len(layer), len(dets_here)), BIG, dtype=float)
            gated = np.zeros_like(cost, dtype=bool)
            for r, t in enumerate(layer):
                tx, ty = t.kf.pos
                # Cap the gate's growth with staleness so a long-lost track can't
                # reach across the pitch and grab a wrong body (the stale-reacquire
                # swap vector). The cap only bites large-tsu tracks.
                gate = min(self.max_speed * dt * (t.time_since_update + 1) + self.slack_m,
                           self.gate_cap_m)
                t_col = self._track_color(t)
                for c, di in enumerate(dets_here):
                    mx, my, _, _, dcol = meas[di]
                    d = float(np.hypot(mx - tx, my - ty))
                    if d > gate:
                        continue
                    # Team-color penalty (SOFT): a committed track seeing a
                    # CONFIDENT opposite-kit detection pays a large cost so the
                    # solver prefers a same-kit alternative, but the pair stays
                    # selectable — a hard veto shattered tracks on transient
                    # occlusion/mis-sample. Unknown on either side = no penalty
                    # (fail-safe to motion).
                    cross = (t_col != 0 and dcol != 0 and t_col != dcol)
                    if cross and not np.isfinite(self.color_penalty_m):
                        continue  # inf penalty == old hard-reject (comparison mode)
                    cost[r, c] = d + (self.color_penalty_m if cross else 0.0)
                    gated[r, c] = True
            from scipy.optimize import linear_sum_assignment
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                if gated[r, c]:   # a real in-gate match, not a sentinel filler
                    di = dets_here[c]
                    assigned[di] = layer[r].track_id
                    unmatched_det.discard(di)

        # 3b. Unprojectable (NaN) dets: try to CONTINUE an existing track by pixel
        #     proximity (fallback) rather than drop — B2's drop bled coverage.
        by_id = {t.track_id: t for t in self._tracks}
        for di in list(unmatched_det):
            mx, my, px, d, dcol = meas[di]
            if mx is not None:
                continue
            self.n_kept_unprojectable += 1
            best_t, best_d = None, self.px_gate
            for t in self._tracks:
                if t.track_id in assigned.values():
                    continue
                # A NaN det carries no meters, so pixel proximity is the only
                # guard — only a just-seen track may absorb one (stale tracks
                # reacquiring on raw pixel distance is a swap vector), and it's
                # color-gated like any real match.
                if t.time_since_update > self.nan_max_tsu:
                    continue
                t_col = self._track_color(t)
                if t_col != 0 and dcol != 0 and t_col != dcol:
                    continue
                pd_ = float(np.hypot(px[0] - t.last_px[0], px[1] - t.last_px[1]))
                if pd_ < best_d:
                    best_d, best_t = pd_, t
            if best_t is not None:
                assigned[di] = best_t.track_id
                unmatched_det.discard(di)

        # 4. Apply matches: KF update + bookkeeping + team-color vote.
        matched_tids = set(assigned.values())
        for di, tid in assigned.items():
            t = by_id[tid]
            mx, my, px, d, dcol = meas[di]
            if t.kf is not None and mx is not None:
                t.kf.update(mx, my)
            t.time_since_update = 0
            t.hits += 1
            t.last_px = px
            # Accumulate the kit vote, clipped so an early wrong commit can be
            # out-voted within ~a second rather than locking the track.
            if dcol:
                t.color_score = int(np.clip(t.color_score + dcol, -self.c_clip, self.c_clip))
            if t.hits >= self.N_INIT:
                t.confirmed = True

        # 5. Age unmatched tracks.
        for t in self._tracks:
            if t.track_id not in matched_tids:
                t.time_since_update += 1

        # 6. Spawn new tracks for still-unmatched detections.
        for di in list(unmatched_det):
            mx, my, px, d, dcol = meas[di]
            kf = None
            unproj = mx is None
            if mx is not None:
                kf = _KalmanMeters(mx, my, self.MEAS_NOISE_M, self.PROC_NOISE)
            t = _Track(self._next_id, kf, px, unproj)
            t.color_score = int(dcol)   # seed the kit vote from the spawning frame
            self._next_id += 1
            self._tracks.append(t)
            assigned[di] = t.track_id

        # 7. Delete tracks lost longer than the buffer.
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]

        # 8. Emit one TrackedDetection per detection, carrying the TRUE bbox_eq.
        out: list[TrackedDetection] = []
        for di, (mx, my, px, d, dcol) in enumerate(meas):
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

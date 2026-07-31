"""Field-space multi-object tracker (accuracy-audit B2 — prototype, flag-gated).

PROTOTYPE / NOT DEFAULT. See B2_FIELD_SPACE_TRACKING.md. Enabled only via
`config.TRACK_FIELD_SPACE`; prod uses `tracking.Tracker` unchanged.

Problem it fixes: the production tracker associates on the DISTORTED equirect
frame, so BoT-SORT's Kalman+IoU motion model sees a player at constant field
speed as a nonlinearly-varying pixel velocity/box (latitude stretch) → the
motion gate misses → tracks fragment. Best-in-class (SoccerNet-GSR 2024 winner)
associates on the field plane instead.

Approach 2a — metric surrogate: project each detection's foot point to field
meters (x_m, y_m) via the calibrated projector BEFORE tracking, then hand boxmot
a SYNTHETIC bbox whose center is that field position scaled to a fixed px/m and
whose size is constant. In this surrogate space, constant field velocity = constant
pixel velocity and IoU overlap is distance-based, so boxmot's mature association
runs in the RIGHT geometry with no library fork. The TRUE equirect bbox is carried
through untouched for all downstream stages (foot position, stats, projection).

Appearance/Re-ID de-corruption (Fix 1) is a separate lever tracked in the design
doc; this module focuses on the motion-space fix. It reuses the same boxmot
embedder for now (unchanged from prod), so any Re-ID gain here is purely from
better association continuity, not better crops.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from . import config
from .detection import Detection
from .tracking import TrackedDetection


# Surrogate-space layout. The synthetic canvas maps field meters → pixels at a
# fixed scale; a generous margin keeps off-field detections (run-ups, keepers
# behind the line) at non-negative coordinates. Box size is CONSTANT so IoU
# association is effectively a metric distance gate.
SURROGATE_PX_PER_M = 20.0        # 1 m -> 20 px in the surrogate frame
SURROGATE_MARGIN_M = 20.0        # off-field buffer (meters) folded into the origin
SURROGATE_BOX_M = 1.0            # constant surrogate box side (meters) → 20 px box


def _field_to_surrogate_xy(x_m: np.ndarray, y_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    sx = (x_m + SURROGATE_MARGIN_M) * SURROGATE_PX_PER_M
    sy = (y_m + SURROGATE_MARGIN_M) * SURROGATE_PX_PER_M
    return sx, sy


class FieldSpaceTracker:
    """Drop-in alternative to tracking.Tracker that associates in field-metric
    surrogate space. Same update() signature + TrackedDetection output, so the
    pipeline can swap it in behind a flag."""

    def __init__(
        self,
        projector,
        reid_weights: str = config.REID_WEIGHTS,
        device: str = config.DEVICE,
        frame_rate: int = 10,
        track_buffer_frames: int = 200,
    ) -> None:
        from boxmot import BotSort
        self.projector = projector
        weights_path = config.MODELS_DIR / reid_weights
        self.impl = BotSort(
            reid_weights=Path(weights_path),
            device=device,
            half=False,
            track_high_thresh=0.45,
            track_low_thresh=0.1,
            new_track_thresh=0.5,
            track_buffer=track_buffer_frames,
            match_thresh=0.8,
            proximity_thresh=0.5,
            appearance_thresh=0.25,
            frame_rate=frame_rate,
        )
        # Surrogate canvas dimensions (field + 2x margin), for the frame we hand
        # boxmot. A mid-grey canvas is fine — appearance still comes from the
        # real crops the embedder is given via the detection ROIs on this frame.
        L = float(getattr(projector.cal, "length_m", 50.0))
        W = float(getattr(projector.cal, "width_m", 35.0))
        self._canvas_w = int((L + 2 * SURROGATE_MARGIN_M) * SURROGATE_PX_PER_M)
        self._canvas_h = int((W + 2 * SURROGATE_MARGIN_M) * SURROGATE_PX_PER_M)

    def _surrogate_bbox(self, det: Detection) -> Optional[tuple[float, float, float, float]]:
        """Foot point of the equirect bbox → field meters → surrogate bbox."""
        x1, y1, x2, y2 = det.bbox_eq
        foot_x = (x1 + x2) / 2.0
        foot_y = y2
        fx, fy = self.projector.pixel_to_field(foot_x, foot_y)
        if not (np.isfinite(fx) and np.isfinite(fy)):
            return None
        sx, sy = _field_to_surrogate_xy(np.array([fx]), np.array([fy]))
        half = 0.5 * SURROGATE_BOX_M * SURROGATE_PX_PER_M
        cx, cy = float(sx[0]), float(sy[0])
        return (cx - half, cy - half, cx + half, cy + half)

    def update(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        time_s: float,
    ) -> list[TrackedDetection]:
        # Build the surrogate canvas + surrogate bboxes; drop dets that don't
        # project to the ground (above horizon / bad geometry).
        canvas = np.full((self._canvas_h, self._canvas_w, 3), 128, dtype=np.uint8)
        rows = []
        kept: list[Detection] = []
        for d in detections:
            sb = self._surrogate_bbox(d)
            if sb is None:
                continue
            rows.append([sb[0], sb[1], sb[2], sb[3], d.confidence, d.cls])
            kept.append(d)
        if not rows:
            self.impl.update(np.empty((0, 6)), canvas)
            return []
        arr = np.array(rows, dtype=np.float64)
        tracks = self.impl.update(arr, canvas)

        feat_by_id: dict[int, np.ndarray] = {}
        try:
            for st in getattr(self.impl, "active_tracks", None) or []:
                f = getattr(st, "smooth_feat", None)
                if f is not None:
                    feat_by_id[int(st.id)] = np.asarray(f, dtype=np.float32)
        except Exception:
            feat_by_id = {}

        out: list[TrackedDetection] = []
        # boxmot track row: x1,y1,x2,y2,track_id,conf,cls,det_index,...
        for row in tracks:
            x1, y1, x2, y2, tid, conf, cls = row[:7]
            det_idx = int(row[7]) if row.shape[0] > 7 else -1
            src = kept[det_idx] if 0 <= det_idx < len(kept) else None
            bbox_eq = src.bbox_eq if src is not None else (0.0, 0.0, 0.0, 0.0)
            out.append(
                TrackedDetection(
                    frame_index=detections[0].frame_index,
                    time_s=time_s,
                    cls=int(cls),
                    confidence=float(conf),
                    # bbox_crop here is the SURROGATE box (association space);
                    # bbox_eq is the TRUTH used by every downstream stage.
                    bbox_crop=(float(x1), float(y1), float(x2), float(y2)),
                    bbox_eq=bbox_eq,
                    track_id=int(tid),
                    appearance_embedding=feat_by_id.get(int(tid)),
                )
            )
        return out

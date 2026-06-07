"""BoT-SORT multi-object tracker wrapper.

Uses `boxmot.BotSort` with OSNet-x0.25 Re-ID embeddings. The tracker runs in
crop pixel space (that's where YOLO detected); we attach equirectangular
bbox coordinates to each TrackedDetection so downstream stages can use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from . import config
from .detection import Detection


@dataclass
class TrackedDetection:
    frame_index: int
    time_s: float
    cls: int
    confidence: float
    bbox_crop: tuple[float, float, float, float]
    bbox_eq: tuple[float, float, float, float]
    track_id: int
    appearance_embedding: Optional[np.ndarray] = field(default=None, repr=False)


class Tracker:
    def __init__(
        self,
        reid_weights: str = config.REID_WEIGHTS,
        device: str = config.DEVICE,
        frame_rate: int = 10,
        track_buffer_frames: int = 200,
    ) -> None:
        from boxmot import BotSort
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

    def update(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        time_s: float,
    ) -> list[TrackedDetection]:
        if not detections:
            self.impl.update(np.empty((0, 6)), frame)
            return []
        arr = np.array(
            [[d.bbox_crop[0], d.bbox_crop[1], d.bbox_crop[2], d.bbox_crop[3], d.confidence, d.cls] for d in detections],
            dtype=np.float64,
        )
        tracks = self.impl.update(arr, frame)
        # Pull the current smoothed OSNet Re-ID feature per track from boxmot's
        # internal STrack list so it can be persisted for offline tracklet
        # stitching. boxmot 11.x: BotSort.active_tracks -> STrack(.id, .smooth_feat).
        # Best-effort: if the internal layout changes, embeddings stay None and
        # stitching falls back to jersey-HSV.
        feat_by_id: dict[int, np.ndarray] = {}
        try:
            for st in getattr(self.impl, "active_tracks", None) or []:
                f = getattr(st, "smooth_feat", None)
                if f is not None:
                    feat_by_id[int(st.id)] = np.asarray(f, dtype=np.float32)
        except Exception:
            feat_by_id = {}
        out: list[TrackedDetection] = []
        # tracks layout (boxmot): x1, y1, x2, y2, track_id, conf, cls, det_index, ...
        for row in tracks:
            x1, y1, x2, y2, tid, conf, cls = row[:7]
            det_idx = int(row[7]) if row.shape[0] > 7 else -1
            bbox_eq = detections[det_idx].bbox_eq if 0 <= det_idx < len(detections) else (0.0, 0.0, 0.0, 0.0)
            out.append(
                TrackedDetection(
                    frame_index=detections[0].frame_index,
                    time_s=time_s,
                    cls=int(cls),
                    confidence=float(conf),
                    bbox_crop=(float(x1), float(y1), float(x2), float(y2)),
                    bbox_eq=bbox_eq,
                    track_id=int(tid),
                    appearance_embedding=feat_by_id.get(int(tid)),
                )
            )
        return out


def to_dataframe(tracks: Iterable[TrackedDetection], fps: float) -> pd.DataFrame:
    rows = []
    for t in tracks:
        x1, y1, x2, y2 = t.bbox_eq
        foot_x = (x1 + x2) / 2.0
        foot_y = y2  # bottom-center of bbox
        rows.append({
            "frame": t.frame_index,
            "time_s": t.time_s,
            "track_id": t.track_id,
            "cls": t.cls,
            "conf": t.confidence,
            "x1_eq": x1, "y1_eq": y1, "x2_eq": x2, "y2_eq": y2,
            "foot_x_eq": foot_x,
            "foot_y_eq": foot_y,
            "bbox_h_crop": t.bbox_crop[3] - t.bbox_crop[1],
        })
    if not rows:
        return pd.DataFrame(columns=[
            "frame", "time_s", "track_id", "cls", "conf",
            "x1_eq", "y1_eq", "x2_eq", "y2_eq",
            "foot_x_eq", "foot_y_eq", "bbox_h_crop",
        ])
    df = pd.DataFrame(rows)
    df.sort_values(["track_id", "time_s"], inplace=True, ignore_index=True)
    return df

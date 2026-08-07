"""BoT-SORT multi-object tracker wrapper.

Uses `boxmot.BotSort` with OSNet-x0.25 Re-ID embeddings. NOTE: this production
tracker associates on the EQUIRECTANGULAR frame (`pipeline.py` passes
`sample.eq_frame` and sets `bbox_crop = bbox_eq`), i.e. in distorted equirect
pixel space — not the rectified tile space YOLO detected on. The accuracy audit
(B2) flags this as a fragmentation source; `tracking_field.FieldSpaceTracker` is
the field-metric-space alternative, gated behind `config.TRACK_FIELD_SPACE`.
Each TrackedDetection carries the equirect bbox for downstream stages.
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


def _make_rescuing_botsort():
    """BotSort subclass whose low-confidence round can also revive LOST tracks.

    Built lazily so importing this module never requires boxmot (the tests and
    several offline tools import `to_dataframe` without it installed).

    What upstream does. BoT-SORT associates in two rounds: a high-confidence
    round over all tracks, then a second round for detections between
    `track_low_thresh` and `track_high_thresh`. The second round is restricted
    to tracks that are still `TrackState.Tracked`::

        r_tracked_stracks = [strack_pool[i] for i in u_track_first
                             if strack_pool[i].state == TrackState.Tracked]

    so once a track goes Lost, only a HIGH-confidence detection can bring it
    back — the weak-detection safety net is switched off for exactly the tracks
    that most need it.

    Why that hurts here. On mrhvbvwi1gjpn the tracker produced 4269 ids for ~15
    players with a 6.0 s median lifespan, and 65% of tracks died mid-field. At
    those deaths 99.3% of bodies reappear within 2.0 s while `TRACK_BUFFER_S`
    keeps the lost track alive for 20 s, and replaying the frames showed YOLO
    still saw the body 56% of the time — 36% of those boxes scoring under the
    0.50 needed to start a track. The evidence to re-associate is there; the
    filter above is what discards it.

    The override adds Lost tracks to the second-round pool and nothing else:
    same IoU distance, same 0.5 threshold. The matching branch that calls
    `re_activate(..., new_id=False)` already exists upstream — it is simply
    unreachable while the pool is filtered — so a revived track keeps its id.
    """
    from boxmot.trackers.botsort.botsort import BotSort
    from boxmot.trackers.botsort.basetrack import TrackState
    from boxmot.utils.matching import iou_distance, linear_assignment
    from boxmot.trackers.botsort.botsort import STrack

    class _RescuingBotSort(BotSort):
        def _second_association(self, dets_second, activated_stracks,
                                lost_stracks, refind_stracks, u_track_first,
                                strack_pool):
            detections_second = ([STrack(det, max_obs=self.max_obs)
                                  for det in dets_second] if len(dets_second) else [])
            # The one change: Lost tracks are eligible too.
            pool = [strack_pool[i] for i in u_track_first
                    if strack_pool[i].state in (TrackState.Tracked, TrackState.Lost)]

            matches, u_track, u_detection = linear_assignment(
                iou_distance(pool, detections_second), thresh=0.5)
            for itracked, idet in matches:
                track, det = pool[itracked], detections_second[idet]
                if track.state == TrackState.Tracked:
                    track.update(det, self.frame_count)
                    activated_stracks.append(track)
                else:
                    track.re_activate(det, self.frame_count, new_id=False)
                    refind_stracks.append(track)
            for it in u_track:
                track = pool[it]
                if track.state != TrackState.Lost:
                    track.mark_lost()
                    lost_stracks.append(track)
            return matches, u_track, u_detection

    return _RescuingBotSort


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
        # Thresholds come from config (defaults are the previous literals). They
        # need to be tunable because they disagreed with the detector about what
        # counts as a person — see config.TRACK_HIGH_THRESH for the measurement.
        impl_cls = _make_rescuing_botsort() if config.TRACK_RESCUE_LOST else BotSort
        self.impl = impl_cls(
            reid_weights=Path(weights_path),
            device=device,
            half=False,
            track_high_thresh=config.TRACK_HIGH_THRESH,
            track_low_thresh=config.TRACK_LOW_THRESH,
            new_track_thresh=config.TRACK_NEW_THRESH,
            track_buffer=track_buffer_frames,
            match_thresh=0.8,
            proximity_thresh=0.5,
            appearance_thresh=0.25,
            frame_rate=frame_rate,
        )

    @property
    def _next_id(self) -> int:
        """The id the tracker will assign to its NEXT new track.

        boxmot ids come from the class-level `BaseTrack._count` counter, which
        `BotSort.__init__` resets to 0 — so a fresh tracker restarts ids at 1.
        Exposing this as `_next_id` gives all three tracker types (prod, field,
        pitch) one uniform hook the pipeline can carry across the halftime reset,
        so half-2 ids never collide with half-1 ids (which would fold two
        different players into one track_id). See pipeline._new_tracker.
        """
        from boxmot.trackers.botsort.basetrack import BaseTrack
        return int(BaseTrack._count) + 1

    @_next_id.setter
    def _next_id(self, value: int) -> None:
        from boxmot.trackers.botsort.basetrack import BaseTrack
        # next_id() pre-increments, so _count = value - 1 makes the next id `value`.
        BaseTrack._count = int(value) - 1

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

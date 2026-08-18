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

import inspect
import logging

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from . import config
from .detection import Detection

log = logging.getLogger(__name__)


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


def heading_penalty(track_xy, track_v, det_xy, box_h=None, min_speed_frac=0.01,
                    cap=1.0):
    """Cost added when a detection sits OPPOSITE a track's direction of travel.

    0.0 dead ahead, 1.0 directly behind, 0.5 perpendicular, scaled by `cap`.

    Why this exists. The association cost is overlap-with-the-prediction and
    nothing else: it asks "how far?", never "in what direction?". For a track
    just lost, the prediction is already stale, so a body BEHIND the player
    scores as well as one where they were actually running. Measured on
    mrhvbvwi1gjpn over 474 unambiguous continuations, the true successor is
    ahead of the exit velocity **76%** of the time (82% at 2-4 m/s) — against
    53% for the OSNet appearance embedding, which is a coin flip. Direction is
    the strongest per-track signal available on same-kit players.

    The speed gate is a FRACTION OF BOX HEIGHT, not an absolute pixel count.
    The first version used 2.0 px/frame and silenced almost everything it was
    meant to help: a distant player moves 0.21 px/frame (91% below the gate)
    while a near one moves 1.7, so an absolute threshold in a space where
    apparent speed scales with distance mutes the far players entirely. Box
    height is the natural per-detection scale, so `min_speed_frac` of it means
    the same physical speed near and far. Falls back to an absolute 0.3 px when
    no box height is supplied.

    `cap` bounds the penalty so a player who genuinely doubles back is
    disadvantaged rather than excluded — 24% of true continuations ARE behind.
    """
    vx, vy = float(track_v[0]), float(track_v[1])
    speed = (vx * vx + vy * vy) ** 0.5
    floor = (min_speed_frac * float(box_h)) if box_h else 0.3
    if speed < floor:
        return 0.0
    dx = float(det_xy[0]) - float(track_xy[0])
    dy = float(det_xy[1]) - float(track_xy[1])
    dist = (dx * dx + dy * dy) ** 0.5
    if dist < 1e-6:
        return 0.0
    cos = (vx * dx + vy * dy) / (speed * dist)      # +1 ahead, -1 behind
    return float(cap * (1.0 - cos) / 2.0)


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
        def _first_association(self, dets, dets_first, active_tracks, unconfirmed,
                               img, detections, activated_stracks, refind_stracks,
                               strack_pool):
            """Upstream's high-confidence round, plus a heading term.

            The cost upstream builds is overlap-with-the-prediction only, so a
            LOST track — whose prediction is already drifting — scores a body
            behind it exactly as well as one where the player was actually
            running. Adding the heading penalty here (and only for lost tracks,
            whose prediction is the untrustworthy one) is the whole change; the
            matching, thresholds and bookkeeping are upstream's.
            """
            if not config.TRACK_HEADING_WEIGHT:
                return super()._first_association(
                    dets, dets_first, active_tracks, unconfirmed, img, detections,
                    activated_stracks, refind_stracks, strack_pool)

            STrack.multi_predict(strack_pool)
            warp = self.cmc.apply(img, dets)
            STrack.multi_gmc(strack_pool, warp)
            STrack.multi_gmc(unconfirmed, warp)

            ious_dists = iou_distance(strack_pool, detections)
            ious_mask = ious_dists > self.proximity_thresh
            if self.fuse_first_associate:
                from boxmot.utils.matching import fuse_score
                ious_dists = fuse_score(ious_dists, detections)

            if self.with_reid:
                from boxmot.utils.matching import embedding_distance
                emb = embedding_distance(strack_pool, detections) / 2.0
                emb[emb > self.appearance_thresh] = 1.0
                emb[ious_mask] = 1.0
                dists = np.minimum(ious_dists, emb)
            else:
                dists = ious_dists

            w = float(config.TRACK_HEADING_WEIGHT)
            for ti, t in enumerate(strack_pool):
                # Only lost tracks: a Tracked track's prediction is one frame old
                # and already reliable, so nudging it adds noise, not signal.
                if t.state != TrackState.Lost or t.mean is None:
                    continue
                txy, tv = t.mean[:2], t.mean[4:6]
                for di, d in enumerate(detections):
                    if ious_mask[ti, di]:
                        continue                     # already out of the gate
                    dists[ti, di] += w * heading_penalty(
                        txy, tv, d.xywh[:2],
                        box_h=float(t.mean[3]) if t.mean is not None else None,
                        min_speed_frac=config.TRACK_HEADING_MIN_SPEED_FRAC,
                        cap=config.TRACK_HEADING_CAP)

            matches, u_track, u_detection = linear_assignment(
                dists, thresh=self.match_thresh)
            for itracked, idet in matches:
                track, det = strack_pool[itracked], detections[idet]
                if track.state == TrackState.Tracked:
                    track.update(det, self.frame_count)
                    activated_stracks.append(track)
                else:
                    track.re_activate(det, self.frame_count, new_id=False)
                    refind_stracks.append(track)
            return matches, u_track, u_detection

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
        # The subclass carries BOTH the lost-track rescue and the heading term,
        # each independently flag-gated inside it. Select it if EITHER is on —
        # keying only on TRACK_RESCUE_LOST would make TRACK_HEADING_WEIGHT a
        # silent no-op, and a sweep on it would report "no effect" from runs
        # that never applied it.
        _need_subclass = config.TRACK_RESCUE_LOST or config.TRACK_HEADING_WEIGHT
        # TRACKER_TYPE selects the association algorithm. It sat in config for
        # months documented with three options and referenced NOWHERE — BotSort
        # was hardcoded here — so "try a different tracker" looked done and had
        # never been run. It matters because a sweep of all four BotSort knobs
        # (thresholds, buffer, heading, appearance) came back inert against a
        # 5.7 s median track lifespan, and those results say nothing about a
        # different algorithm.
        #
        # The rescue/heading subclass is BotSort-specific, so any other type
        # ignores those flags — made loud rather than silent, since a sweep that
        # thinks it is testing heading on OcSort would be measuring nothing.
        _type = str(getattr(config, "TRACKER_TYPE", "botsort") or "botsort").lower()
        if _type == "botsort":
            impl_cls = _make_rescuing_botsort() if _need_subclass else BotSort
        else:
            if _need_subclass:
                raise SystemExit(
                    f"TRACKER_TYPE={_type} cannot honour TRACK_RESCUE_LOST/"
                    f"TRACK_HEADING_WEIGHT (both are BotSort subclass features). "
                    f"Unset them, or use botsort.")
            # boxmot's TRACKERS is a list of NAMES, not a name->class mapping,
            # so it validates but cannot construct. Map explicitly.
            import boxmot
            _classes = {"bytetrack": "ByteTrack", "ocsort": "OcSort",
                        "deepocsort": "DeepOcSort", "strongsort": "StrongSort",
                        "hybridsort": "HybridSort", "imprassoc": "ImprAssocTrack"}
            if _type not in _classes:
                raise SystemExit(f"unknown TRACKER_TYPE={_type!r}; "
                                 f"offered: botsort, {', '.join(sorted(_classes))}")
            impl_cls = getattr(boxmot, _classes[_type])
        # Trackers disagree about their constructor signature — ByteTrack takes
        # no Re-ID at all, OcSort has no appearance gate. Pass only what this
        # one accepts, so an unsupported kwarg is a no-op rather than a crash,
        # and log what was dropped so a missing knob is never silent.
        _wanted = dict(
            reid_weights=Path(weights_path),
            device=device,
            half=False,
            track_high_thresh=config.TRACK_HIGH_THRESH,
            track_low_thresh=config.TRACK_LOW_THRESH,
            new_track_thresh=config.TRACK_NEW_THRESH,
            track_buffer=track_buffer_frames,
            match_thresh=0.8,
            proximity_thresh=0.5,
            appearance_thresh=config.TRACK_APPEARANCE_THRESH,
            frame_rate=frame_rate,
            # HybridSort requires this explicitly; the others derive an
            # equivalent from new_track_thresh. Same value either way, so no
            # tracker gets a different detection bar than the rest.
            det_thresh=config.TRACK_NEW_THRESH,
        )
        _accepted = set(inspect.signature(impl_cls.__init__).parameters)
        _kwargs = {k: v for k, v in _wanted.items() if k in _accepted}
        if _type != "botsort":
            _skipped = sorted(set(_wanted) - set(_kwargs))
            log.info("tracker=%s (%s); ignored kwargs: %s", _type,
                     impl_cls.__name__, ", ".join(_skipped) or "none")
        self.impl = impl_cls(**_kwargs)
        # Associate on motion alone when appearance is measured to be noise on
        # this kit (see config.TRACK_APPEARANCE).
        #
        # NOT by setting `with_reid = False`: that also stops boxmot computing
        # `smooth_feat`, so `update()` below would persist no embeddings and the
        # OFFLINE stitcher would lose its appearance input without saying a word.
        # Those are different decisions — the stitcher compares whole tracklets
        # with far more context than a single frame-to-frame gate — so keep
        # Re-ID running and neutralise the gate instead.
        #
        # The gate is neutralised at 0.0, NOT 1.0. Upstream fuses with
        # `emb_dists[emb_dists > thresh] = 1.0; dists = min(ious, emb_dists)`,
        # so appearance can only ever LOWER a cost: raising the threshold admits
        # MORE appearance, which is backwards. At 0.0 every embedding distance
        # is forced to 1.0 and `min(ious, 1.0)` is just IoU.
        if not config.TRACK_APPEARANCE:
            self.impl.appearance_thresh = 0.0

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

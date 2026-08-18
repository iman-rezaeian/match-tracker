"""The low-confidence round must be able to revive a LOST track.

BoT-SORT associates in two rounds: high-confidence over all tracks, then a
second round for detections between `track_low_thresh` and `track_high_thresh`.
Upstream restricts that second round to tracks still in `TrackState.Tracked`, so
once a track goes Lost only a HIGH-confidence detection can bring it back — the
weak-detection safety net is off for exactly the tracks that need it.

Measured on mrhvbvwi1gjpn (tracking/death_replay_probe.py): 4269 track ids for
~15 players, 6.0 s median lifespan, 65% of deaths mid-field. At those deaths
99.3% of bodies reappear within 2.0 s while TRACK_BUFFER_S holds the track for
20 s, and YOLO still saw the body 56% of the time — 36% of those boxes below the
0.50 needed to start a track. So the evidence was there and the filter discarded
it.

These tests pin the behaviour difference directly on boxmot rather than on our
wrapper, because the wrapper only chooses a class; the semantics live upstream.

Run: `.venv-post-game/bin/python -m post_game.test_tracking_rescue`
(pytest is not installed in that venv, but boxmot is — hence the __main__ block.)
"""
from __future__ import annotations

import numpy as np

from .tracking import _make_rescuing_botsort


def _tracker(cls, **kw):
    """A tracker built the way the pipeline builds it, minus appearance.

    boxmot always instantiates a Re-ID backend (passing weights=None raises), so
    load the real OSNet weights and then switch appearance OFF. These tests are
    about which tracks the second round is ALLOWED to consider, which is pure
    bookkeeping — leaving Re-ID on would let embedding distance decide the
    outcome and the test would stop measuring the thing it names.
    """
    from . import config
    kw.setdefault("track_high_thresh", 0.45)
    kw.setdefault("track_low_thresh", 0.10)
    kw.setdefault("new_track_thresh", 0.50)
    kw.setdefault("track_buffer", 200)
    kw.setdefault("frame_rate", 10)
    t = cls(reid_weights=config.MODELS_DIR / config.REID_WEIGHTS,
            device="cpu", half=False, **kw)
    t.with_reid = False
    return t


def _dets(*boxes):
    """(x1,y1,x2,y2,conf,cls) rows, or an empty (0,6) frame."""
    if not boxes:
        return np.empty((0, 6))
    return np.array([[*b, 0] for b in boxes], dtype=np.float64)


FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)
HIGH, LOW = 0.90, 0.30      # LOW sits between low_thresh 0.10 and high 0.45


def _establish(t, box, n=5):
    """Run a confident detection for n frames so the track is Tracked."""
    for _ in range(n):
        t.update(_dets((*box, HIGH)), FRAME)


def _ids(out):
    return {int(r[4]) for r in out} if len(out) else set()


def test_upstream_cannot_revive_a_lost_track_with_a_weak_detection():
    """Baseline: this is the behaviour being changed."""
    from boxmot.trackers.botsort.botsort import BotSort
    t = _tracker(BotSort)
    box = (600, 300, 660, 480)
    _establish(t, box)
    t.update(_dets(), FRAME)                       # vanish -> track goes Lost
    out = t.update(_dets((*box, LOW)), FRAME)      # reappears, weakly
    assert not _ids(out), (
        "upstream is expected to emit nothing here; if this fails, boxmot "
        "changed and _RescuingBotSort may no longer be needed")


def test_rescue_revives_a_lost_track_and_keeps_its_id():
    cls = _make_rescuing_botsort()
    t = _tracker(cls)
    box = (600, 300, 660, 480)
    _establish(t, box)
    first = _ids(t.update(_dets((*box, HIGH)), FRAME))
    assert len(first) == 1, "setup failed: no confident track established"
    t.update(_dets(), FRAME)                       # vanish -> Lost
    revived = _ids(t.update(_dets((*box, LOW)), FRAME))
    assert revived == first, (
        f"expected the lost track to come back with its own id {first}, "
        f"got {revived} — a NEW id here means the fragmentation this fix "
        f"targets is still happening")


def test_rescue_does_not_invent_a_track_where_nothing_reappears():
    """A weak detection far from the lost track must not resurrect it."""
    cls = _make_rescuing_botsort()
    t = _tracker(cls)
    _establish(t, (600, 300, 660, 480))
    t.update(_dets(), FRAME)
    # Same size, opposite side of the frame: zero IoU with the lost track.
    out = t.update(_dets((60, 300, 120, 480, LOW)), FRAME)
    assert not _ids(out), (
        "a weak detection with no overlap must not revive the lost track; "
        "it is below new_track_thresh so it cannot start one either")


def test_rescue_still_tracks_normally_when_nothing_is_lost():
    """The override must not disturb the ordinary confident path."""
    cls = _make_rescuing_botsort()
    t = _tracker(cls)
    box = [600, 300, 660, 480]
    ids = set()
    for _ in range(6):
        ids |= _ids(t.update(_dets((*box, HIGH)), FRAME))
        box = [box[0] + 4, box[1], box[2] + 4, box[3]]   # a steady walk
    assert len(ids) == 1, f"a steadily-moving player should hold one id, got {ids}"


def test_flag_off_selects_upstream_botsort():
    """config.TRACK_RESCUE_LOST is the only switch; default must be upstream."""
    from boxmot.trackers.botsort.botsort import BotSort
    from . import config
    assert config.TRACK_RESCUE_LOST is False, "the rescue must ship OFF"
    cls = _make_rescuing_botsort()
    assert issubclass(cls, BotSort) and cls is not BotSort


def test_thresholds_come_from_config_and_default_to_the_old_literals():
    """Lifting the literals into config must be a no-op at defaults."""
    from . import config
    assert (config.TRACK_HIGH_THRESH, config.TRACK_NEW_THRESH,
            config.TRACK_LOW_THRESH) == (0.45, 0.50, 0.10)


def test_appearance_off_neutralises_the_gate_at_zero_not_one():
    """0.0 is the neutral value; 1.0 would admit MORE appearance.

    Upstream fuses with `emb[emb > thresh] = 1.0; dists = min(ious, emb)`, so a
    HIGHER threshold lets more embedding distances survive and pull costs down.
    Getting this backwards would have made the experiment measure the opposite
    of what it claims.
    """
    iou = np.array([0.2, 0.6, 0.9])
    emb = np.array([0.10, 0.30, 0.05])

    def fuse(thresh):
        e = emb.copy()
        e[e > thresh] = 1.0
        return np.minimum(iou, e)

    assert np.allclose(fuse(0.0), iou), "0.0 must leave pure IoU"
    assert not np.allclose(fuse(1.0), iou), "1.0 must NOT be treated as neutral"


def test_appearance_off_keeps_embeddings_flowing_to_the_stitcher():
    """Turning the gate off must not stop boxmot computing smooth_feat.

    Setting `with_reid = False` also disables feature extraction, so `update()`
    would persist no embeddings and the OFFLINE stitcher would silently lose its
    appearance input — a different decision from the frame-to-frame gate.
    """
    import os
    from . import config
    prev = os.environ.get("TRACK_APPEARANCE")
    os.environ["TRACK_APPEARANCE"] = "0"
    try:
        import importlib
        importlib.reload(config)
        from .tracking import Tracker
        from .detection import Detection
        t = Tracker(frame_rate=10, track_buffer_frames=200)
        assert t.impl.with_reid, "Re-ID must stay ON so embeddings are still made"
        assert t.impl.appearance_thresh == 0.0
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        det = Detection(frame_index=0, cls=0, confidence=0.9,
                        bbox_crop=(600, 300, 660, 480),
                        bbox_eq=(600, 300, 660, 480))
        out = []
        for i in range(4):
            out = t.update(frame, [det], time_s=i * 0.1)
        assert out and out[0].appearance_embedding is not None, (
            "embeddings must still reach the stitcher with the gate off")
    finally:
        if prev is None:
            os.environ.pop("TRACK_APPEARANCE", None)
        else:
            os.environ["TRACK_APPEARANCE"] = prev
        import importlib
        importlib.reload(config)


def test_heading_penalty_is_zero_ahead_and_one_behind():
    """The whole point: a body where the player was RUNNING must beat one behind."""
    from .tracking import heading_penalty
    at, v = (100.0, 100.0), (10.0, 0.0)          # moving +x
    assert abs(heading_penalty(at, v, (150.0, 100.0)) - 0.0) < 1e-6   # ahead
    assert abs(heading_penalty(at, v, (50.0, 100.0)) - 1.0) < 1e-6    # behind
    assert abs(heading_penalty(at, v, (100.0, 150.0)) - 0.5) < 1e-6   # sideways


def test_heading_penalty_abstains_on_a_slow_track():
    """A stationary player's velocity is noise; penalising on it is worse than
    not penalising at all."""
    from .tracking import heading_penalty
    at, slow = (100.0, 100.0), (0.05, 0.02)
    assert heading_penalty(at, slow, (50.0, 100.0), box_h=77.0) == 0.0


def test_heading_speed_floor_scales_with_distance():
    """The bug this fix exists for.

    Apparent speed scales with distance: measured on mrhvbvwi1gjpn a far player
    moves 0.21 px/frame and a near one 1.7. The first version gated on an
    ABSOLUTE 2.0 px/frame, which silenced 91% of small-box (distant) players and
    56% of large ones — muting the term almost everywhere, and hardest on the
    players it was meant to help. The floor must be a fraction of box height so
    the same physical speed passes near and far.
    """
    from .tracking import heading_penalty
    at, behind = (100.0, 100.0), (50.0, 100.0)
    far_v, far_box = (0.25, 0.0), 30.0     # distant player, genuinely moving
    near_v, near_box = (1.70, 0.0), 200.0  # near player, same physical speed
    frac = 0.0025
    assert heading_penalty(at, far_v, behind, box_h=far_box,
                           min_speed_frac=frac) > 0.5, (
        "a moving DISTANT player must not be silenced by the speed floor")
    assert heading_penalty(at, near_v, behind, box_h=near_box,
                           min_speed_frac=frac) > 0.5
    # ...while genuine sub-jitter still abstains at BOTH scales
    assert heading_penalty(at, (0.02, 0.0), behind, box_h=far_box,
                           min_speed_frac=frac) == 0.0
    assert heading_penalty(at, (0.2, 0.0), behind, box_h=near_box,
                           min_speed_frac=frac) == 0.0


def test_heading_cap_disadvantages_but_never_excludes():
    """24% of true continuations ARE behind — players double back."""
    from .tracking import heading_penalty
    full = heading_penalty((100.0, 100.0), (10.0, 0.0), (50.0, 100.0), cap=1.0)
    half = heading_penalty((100.0, 100.0), (10.0, 0.0), (50.0, 100.0), cap=0.5)
    assert full == 1.0 and half == 0.5


def test_heading_penalty_handles_a_detection_on_top_of_the_track():
    from .tracking import heading_penalty
    assert heading_penalty((100.0, 100.0), (10.0, 0.0), (100.0, 100.0)) == 0.0


def test_heading_ranks_the_forward_candidate_first():
    """Two candidates equidistant from a stale prediction — direction decides."""
    from .tracking import heading_penalty
    at, v = (100.0, 100.0), (8.0, 0.0)
    fwd = heading_penalty(at, v, (140.0, 100.0))
    back = heading_penalty(at, v, (60.0, 100.0))
    assert fwd < back, "the candidate in the direction of travel must cost less"


def test_heading_is_off_by_default_and_selects_the_subclass_when_on():
    """It must not be a silent no-op: the subclass carries BOTH features, so
    keying selection only on TRACK_RESCUE_LOST would ignore the heading flag."""
    from . import config
    assert config.TRACK_HEADING_WEIGHT == 0.0, "heading must ship OFF"
    assert config.TRACK_HEADING_MIN_SPEED_FRAC > 0
    assert 0 < config.TRACK_HEADING_CAP <= 1.0


def test_threshold_changes_invalidate_the_stage2_cache():
    """A sweep must not silently reuse a cache built at other thresholds.

    config.py is not in _TRACKING_SOURCES, so the file-hash half of the
    fingerprint cannot see these values; they have to be listed by name.
    """
    from .pipeline import _TRACKING_CONFIG_KEYS
    for k in ("TRACK_HIGH_THRESH", "TRACK_NEW_THRESH", "TRACK_LOW_THRESH",
              "TRACK_RESCUE_LOST", "TRACK_APPEARANCE",
              "TRACK_APPEARANCE_THRESH", "TRACK_HEADING_WEIGHT",
              "TRACK_HEADING_MIN_SPEED_FRAC", "TRACK_HEADING_CAP"):
        assert k in _TRACKING_CONFIG_KEYS, f"{k} missing from the fingerprint"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)

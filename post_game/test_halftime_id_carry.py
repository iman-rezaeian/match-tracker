"""Regression: track ids must stay DISJOINT across the halftime tracker reset.

The stage-2 loop resets the tracker at halftime (`pipeline.py`: fresh
`_new_tracker()` when `time_s >= h1_end_s`). Every tracker restarts its id
counter from 1 on construction — boxmot's `Tracker`/`FieldSpaceTracker` because
`BotSort.__init__` calls `BaseTrack.clear_count()`, and `PitchTracker` because it
sets `self._next_id = 1`. Without carrying the counter across the reset, half-2
ids COLLIDE with half-1 ids: the same integer appears in both halves and folds
two different players' detections + jersey samples into one `track_id`, which
corrupts team classification and per-player coverage/stats (measured: 1257
colliding ids on the equirect baseline for mri01pvelv46d, 354 on pitch tracks).

The fix carries the id counter across the reset via the uniform `_next_id` hook
all three trackers expose. These tests assert the two halves produce disjoint id
sets — the PitchTracker end-to-end (boxmot-free), and the boxmot-backed
`_next_id` property against its real `BaseTrack._count` backing store.

Run: `python -m post_game.test_halftime_id_carry` (or pytest).
"""
from __future__ import annotations

import numpy as np

from .calibration import FieldCalibration, FieldProjector
from .detection import Detection
from .tracking_pitch import PitchTracker

EQ_W, EQ_H = 5760, 2880
L, W = 54.0, 34.0


def _projector() -> FieldProjector:
    sphere = {"a": 1.0, "b": 0.0, "tx": 0.0, "ty": 0.0, "cam_h_m": 5.0,
              "pitch_deg": 0.0, "roll_deg": 0.0, "eq_w": EQ_W, "eq_h": EQ_H}
    cal = FieldCalibration("syn", L, W, [(0, 0)] * 4,
                           [(0, 0), (L, 0), (L, W), (0, W)],
                           [[1, 0, 0], [0, 1, 0], [0, 0, 1]], (EQ_W, EQ_H), sphere)
    return FieldProjector(cal)


def _det(proj: FieldProjector, x_m: float, y_m: float, fi: int) -> Detection:
    px, py = proj.field_to_pixel(x_m, y_m)
    box = (px - 8, py - 32, px + 8, py)
    return Detection(frame_index=fi, cls=0, confidence=0.9, bbox_crop=box, bbox_eq=box)


def _run_window(proj, trk, x_positions, t0=0.0, fps=10.0):
    """Drive `trk` for len(x_positions) frames with N steady players at the given
    field-x positions; return the set of track_ids it emitted."""
    ids: set[int] = set()
    for f, _ in enumerate(range(len(x_positions[0]))):
        dets = [_det(proj, xs[f], 17.0, f) for xs in x_positions]
        out = trk.update(np.zeros((10, 10, 3), np.uint8), dets, time_s=t0 + f / fps)
        ids.update(td.track_id for td in out)
    return ids


def test_pitch_tracker_carry_makes_halves_disjoint():
    """Two windows with a halftime tracker reset, carrying `_next_id` across it:
    the id sets from the two halves must be DISJOINT (the collision bug this
    fixes)."""
    proj = _projector()

    # --- half 1: three steady players ---
    trk1 = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    # 8-frame tracks for three well-separated players
    xs = [[15.0] * 8, [27.0] * 8, [39.0] * 8]
    h1_ids = _run_window(proj, trk1, xs, t0=0.0)
    assert h1_ids, "half 1 produced no tracks"

    # --- halftime reset WITH carry (mirrors pipeline._new_tracker) ---
    next_id_carry = trk1._next_id
    trk2 = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    trk2._next_id = next_id_carry
    h2_ids = _run_window(proj, trk2, xs, t0=1000.0)
    assert h2_ids, "half 2 produced no tracks"

    assert h1_ids.isdisjoint(h2_ids), (
        f"half ids collide after carry: {sorted(h1_ids & h2_ids)}"
    )


def test_pitch_tracker_without_carry_would_collide():
    """Control: a fresh tracker with NO carry restarts ids at 1, so the halves
    DO collide — this is the bug, proving the carry above is what fixes it."""
    proj = _projector()
    xs = [[15.0] * 8, [27.0] * 8, [39.0] * 8]

    trk1 = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    h1_ids = _run_window(proj, trk1, xs, t0=0.0)

    trk2 = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)  # NO carry
    h2_ids = _run_window(proj, trk2, xs, t0=1000.0)

    assert h1_ids & h2_ids, "expected collision without carry (control invalid)"


def test_pitch_tracker_next_id_advances_monotonically():
    """`_next_id` reflects the counter and only ever moves forward within a half,
    so carrying it into the next half guarantees strictly-larger ids there."""
    proj = _projector()
    trk = PitchTracker(proj, frame_rate=10, track_buffer_frames=100)
    start = trk._next_id
    assert start == 1
    trk.update(np.zeros((10, 10, 3), np.uint8),
               [_det(proj, 20.0, 17.0, 0), _det(proj, 30.0, 17.0, 0)], time_s=0.0)
    assert trk._next_id == start + 2  # two new tracks spawned


def test_boxmot_next_id_property_roundtrips():
    """The boxmot-backed `_next_id` (shared by prod `Tracker` and
    `FieldSpaceTracker`) reads/writes the real class-level `BaseTrack._count`, so
    setting it makes the NEXT boxmot-assigned id equal the value we set.

    Exercises THE SHIPPED getter/setter from both wrapper classes without
    instantiating BotSort (which needs model weights): a `property` descriptor
    works on any instance, so we bind it to a bare object made with
    `object.__new__` (no `__init__`)."""
    try:
        from boxmot.trackers.botsort.basetrack import BaseTrack
    except Exception as e:  # boxmot absent (e.g. minimal CI) — skip, not fail
        print(f"  skip test_boxmot_next_id_property_roundtrips (no boxmot: {e})")
        return

    from .tracking import Tracker
    from .tracking_field import FieldSpaceTracker

    saved = BaseTrack._count
    try:
        for cls in (Tracker, FieldSpaceTracker):
            # Bare instance: skip __init__ so no BotSort/model weights are needed;
            # the `_next_id` property is a class-level descriptor and still binds.
            trk = object.__new__(cls)

            # Simulate a first half that consumed some ids.
            BaseTrack.clear_count()
            assert (BaseTrack.next_id(), BaseTrack.next_id()) == (1, 2)
            assert trk._next_id == 3          # getter: next id would be 3
            carry = trk._next_id              # carry across the halftime reset

            # Halftime: a fresh BotSort would clear_count() back to 0.
            BaseTrack.clear_count()
            assert trk._next_id == 1          # without carry, ids restart (the bug)

            # Apply the carry via the setter; the next boxmot id must be `carry`,
            # strictly greater than any first-half id -> disjoint halves.
            trk._next_id = carry
            assert BaseTrack.next_id() == carry == 3, cls.__name__
    finally:
        BaseTrack._count = saved


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} halftime-id-carry tests passed.")

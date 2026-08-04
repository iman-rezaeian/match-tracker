"""Team-color CANNOT-LINK guard in the tracklet stitch.

Locks two contracts:
  1. Under the PitchTracker color gate (TRACK_PITCH on), two same-team-classified
     fragments whose jersey samples vote CONFIDENTLY-OPPOSITE kits are NOT merged,
     even when geometry + timing would otherwise chain them.
  2. With TRACK_PITCH OFF (prod/equirect), the guard is a NO-OP — the stitch is
     byte-identical to the geometry-only behaviour — so the prod baseline can't
     silently change.

Pure synthetic fragments; no video/Firestore/boxmot.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .reid_stitch import stitch_tracklets

GREEN_HEX, BLUE_HEX = "#16a34a", "#2563eb"
# vivid green / blue jersey HSV pixel blocks (OpenCV H green~71, blue~111)
_GREEN_HSV = np.tile(np.array([71, 200, 150], np.float32), (30, 1))
_BLUE_HSV = np.tile(np.array([111, 200, 150], np.float32), (30, 1))


def _frag(track_id: int, t0: float, x0: float) -> pd.DataFrame:
    """A short fragment: 5 detections over 0.5 s, near-stationary at (x0, 17)."""
    ts = t0 + np.arange(5) * 0.1
    return pd.DataFrame({
        "track_id": track_id, "time_s": ts, "frame": (ts * 10).astype(int),
        "x_m": x0 + np.zeros(5), "y_m": 17.0 + np.zeros(5),
        "foot_x_eq": 0.0, "foot_y_eq": 0.0, "conf": 0.9,
    })


def _run(monkey_pitch: bool):
    """Two fragments 0.3 m apart, 0.2 s gap — trivially chainable by geometry —
    but A votes GREEN and B votes BLUE. Returns whether they were merged."""
    a, b = 1, 2
    tracks = pd.concat([_frag(a, 0.0, 20.0), _frag(b, 0.7, 20.3)], ignore_index=True)
    team = {a: 0, b: 0}  # both classified our-team (the mis-classification the guard catches)
    # one HSV block PER detection (5 each) so the vote clears the >=3-vote floor
    jersey = {a: [_GREEN_HSV] * 5, b: [_BLUE_HSV] * 5}
    old = config.TRACK_PITCH
    config.TRACK_PITCH = monkey_pitch
    try:
        mapping = stitch_tracklets(
            tracks, team, track_embeddings={}, track_jersey_samples=jersey,
            mode="greedy", our_color_hex=GREEN_HEX, opp_color_hex=BLUE_HEX)
    finally:
        config.TRACK_PITCH = old
    return mapping[a] == mapping[b]   # merged?


def test_guard_blocks_opposite_kit_merge_under_track_pitch():
    assert config.PITCH_COLOR_GATE, "test assumes PITCH_COLOR_GATE default on"
    merged = _run(monkey_pitch=True)
    assert not merged, "color guard should REFUSE to merge a green and a blue fragment"


def test_guard_is_noop_without_track_pitch():
    # With TRACK_PITCH off the guard must not engage: geometry alone chains them
    # (0.3 m move over a 0.2 s gap is trivially plausible), so they DO merge.
    merged = _run(monkey_pitch=False)
    assert merged, "with TRACK_PITCH off the stitch must be geometry-only (prod unchanged)"


def test_same_kit_fragments_still_merge_under_guard():
    """Sanity: two GREEN fragments must still chain under the guard (it only
    blocks confidently-opposite kits, never same-kit)."""
    a, b = 1, 2
    tracks = pd.concat([_frag(a, 0.0, 20.0), _frag(b, 0.7, 20.3)], ignore_index=True)
    team = {a: 0, b: 0}
    jersey = {a: [_GREEN_HSV] * 5, b: [_GREEN_HSV] * 5}
    old = config.TRACK_PITCH
    config.TRACK_PITCH = True
    try:
        mapping = stitch_tracklets(
            tracks, team, track_embeddings={}, track_jersey_samples=jersey,
            mode="greedy", our_color_hex=GREEN_HEX, opp_color_hex=BLUE_HEX)
    finally:
        config.TRACK_PITCH = old
    assert mapping[a] == mapping[b], "two same-kit green fragments should still merge"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} stitch color-guard tests passed.")

"""Unit tests for the stitch interior-overlap guard (_overlaps_in_time).

Two fragments that are alive AT THE SAME TIME cannot be the same person, so the
stitcher must never chain them. The old guard compared only endpoints
(`sb.t0 - sa.t1 < -0.5`), which describes the fragment ENVELOPE. Real tracks have
interior holes, so the envelope lies in both directions:

  * a fragment with a hole looks continuous, so a second fragment genuinely
    coexisting inside part of its span could still pass an endpoint check;
  * conversely a fragment living entirely INSIDE another's hole is a legal
    continuation, and must NOT be rejected just because the envelopes intersect.

Measured on W8: 20 tracklets / 123 s of real interior overlap once the halftime
collision artefacts are excluded (633782 s including them).

Pure; no I/O. Run: python -m post_game.test_stitch_overlap
"""
from __future__ import annotations

import numpy as np

from post_game.reid_stitch import _overlaps_in_time


def _frag(times):
    t = np.asarray(times, dtype=float)
    return {"t0": float(t[0]), "t1": float(t[-1]), "ts": t}


def test_disjoint_fragments_do_not_overlap():
    a = _frag(np.arange(0, 5, 0.1))       # 0..4.9s
    b = _frag(np.arange(10, 15, 0.1))     # 10..14.9s
    assert _overlaps_in_time(a, b) is False


def test_plainly_simultaneous_fragments_overlap():
    a = _frag(np.arange(0, 10, 0.1))
    b = _frag(np.arange(3, 8, 0.1))       # entirely inside a, sampled throughout
    assert _overlaps_in_time(a, b) is True


def test_fragment_living_inside_a_hole_is_NOT_an_overlap():
    # THE KEY CASE the endpoint check gets wrong in the permissive direction:
    # a is 0-20s but absent 8-14s; b lives only in that hole. They never coexist,
    # so this is a legal continuation and must be joinable.
    ta = np.concatenate([np.arange(0, 8, 0.1), np.arange(14, 20, 0.1)])
    a = _frag(ta)
    b = _frag(np.arange(9, 13, 0.1))
    assert a["t0"] < b["t0"] and b["t1"] < a["t1"], "b is inside a's envelope"
    assert _overlaps_in_time(a, b) is False


def test_partial_interleave_inside_the_hole_is_an_overlap():
    # b starts in a's hole but runs on past it, so they DO share live samples.
    ta = np.concatenate([np.arange(0, 8, 0.1), np.arange(14, 20, 0.1)])
    a = _frag(ta)
    b = _frag(np.arange(12, 18, 0.1))     # overlaps a's second run (14-18)
    assert _overlaps_in_time(a, b) is True


def test_touching_endpoints_within_tolerance_is_not_overlap():
    # a ends 5.0, b starts 5.05 — a normal continuation at 10 Hz, not coexistence.
    a = _frag(np.arange(0, 5.01, 0.1))
    b = _frag(np.arange(5.4, 9, 0.1))
    assert _overlaps_in_time(a, b) is False


def test_symmetric():
    a = _frag(np.arange(0, 10, 0.1))
    b = _frag(np.arange(5, 15, 0.1))
    assert _overlaps_in_time(a, b) == _overlaps_in_time(b, a) is True


def test_summaries_without_samples_fall_back_to_envelope():
    # Defensive: a summary lacking "ts" (older cache) uses the envelope only.
    a = {"t0": 0.0, "t1": 10.0}
    b = {"t0": 5.0, "t1": 15.0}
    assert _overlaps_in_time(a, b) is True
    c = {"t0": 20.0, "t1": 30.0}
    assert _overlaps_in_time(a, c) is False


def test_single_sample_fragments():
    a = _frag([4.0])
    b = _frag([4.05])
    assert _overlaps_in_time(a, b) is True      # same instant → two bodies
    c = _frag([50.0])
    assert _overlaps_in_time(a, c) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} stitch-overlap tests passed.")

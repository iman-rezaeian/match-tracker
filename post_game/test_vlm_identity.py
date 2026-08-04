"""Unit tests for tracking/vlm_identity pure logic — no network, video, or
Firestore. Covers the number->player mapping (+ duplicate-number guard),
multi-frame vote + confidence gate, and the draft-shape / all its refusal cases.
The VLM read + crop render are injected as fakes.

Run: python -m post_game.test_vlm_identity  (or pytest)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd

from tracking.vlm_identity import (
    build_number_map, vote_number, make_draft, read_tracklet_number,
)


def _roster(pairs):
    return [SimpleNamespace(id=pid, name=nm, jersey_number=jn) for pid, nm, jn in pairs]


# ---- build_number_map ----------------------------------------------------
def test_number_map_basic_and_none():
    r = _roster([("p1", "A", 7), ("p2", "B", 11), ("p3", "C", None)])
    num_of, pon, dup = build_number_map(r)
    assert num_of == {"p1": 7, "p2": 11}       # None-number player excluded
    assert pon == {7: "p1", 11: "p2"}
    assert dup == set()


def test_duplicate_number_guard():
    # two dressed players share #9 -> #9 must NOT map to either (ambiguous)
    r = _roster([("p1", "A", 9), ("p2", "B", 9), ("p3", "C", 10)])
    num_of, pon, dup = build_number_map(r)
    assert dup == {9}
    assert 9 not in pon
    assert pon == {10: "p3"}


def test_number_map_str_ints():
    r = _roster([("p1", "A", "7"), ("p2", "B", "x")])   # "x" is unparseable -> skip
    _, pon, dup = build_number_map(r)
    assert pon == {7: "p1"} and dup == set()


# ---- vote_number ---------------------------------------------------------
def test_vote_majority_wins():
    reads = [{"number": 7, "confidence": 0.8}, {"number": 7, "confidence": 0.6},
             {"number": 11, "confidence": 0.9}]
    num, conf, votes = vote_number(reads, min_conf=0.5)
    assert num == 7 and votes == 2 and conf == 0.8   # conf = max among supporters


def test_vote_confidence_gate_drops_low():
    reads = [{"number": 7, "confidence": 0.45}, {"number": 7, "confidence": 0.4}]
    assert vote_number(reads, min_conf=0.5) == (None, 0.0, 0)


def test_vote_zero_sentinel_is_no_number():
    reads = [{"number": 0, "confidence": 0.99}, {"number": 0, "confidence": 0.9}]
    assert vote_number(reads, min_conf=0.5) == (None, 0.0, 0)


def test_vote_tie_broken_by_confidence():
    reads = [{"number": 7, "confidence": 0.6}, {"number": 11, "confidence": 0.9}]
    num, conf, votes = vote_number(reads, min_conf=0.5)
    assert num == 11 and votes == 1 and conf == 0.9   # 1-1 tie -> higher conf


# ---- make_draft ----------------------------------------------------------
_PON = {7: "p1", 11: "p2"}


def test_make_draft_shape_and_deterministic_id():
    d = make_draft(42, 7, 0.83, _PON, set(), {"p1", "p2"}, "reads 7 on back",
                   current_player_id="p9", minutes=5.7)
    assert d == {
        "id": "vid_42", "trackletId": 42, "suggestedPlayerId": "p1",
        "jerseyNumber": 7, "confidence": 0.83, "reasoning": "reads 7 on back",
        "currentPlayerId": "p9", "minutes": 5.7, "source": "vlm_draft"}


def test_make_draft_refusals():
    # no number
    assert make_draft(1, None, 0.9, _PON, set(), None, "", None, 1.0) is None
    # duplicate number
    assert make_draft(1, 9, 0.9, _PON, {9}, None, "", None, 1.0) is None
    # number maps to nobody
    assert make_draft(1, 99, 0.9, _PON, set(), None, "", None, 1.0) is None
    # player not in the logged squad
    assert make_draft(1, 7, 0.9, _PON, set(), {"p2"}, "", None, 1.0) is None
    # valid_ids None => squad-unrestricted, so p1 is allowed
    assert make_draft(1, 7, 0.9, _PON, set(), None, "", None, 1.0)["suggestedPlayerId"] == "p1"


# ---- read_tracklet_number (VLM + render injected) ------------------------
def _sub(n=12):
    # n detections with varying heights so _tallest_rows can pick the closest
    return pd.DataFrame({
        "time_s": [i * 0.1 for i in range(n)],
        "x1_eq": [10.0] * n, "y1_eq": [10.0] * n,
        "x2_eq": [30.0] * n, "y2_eq": [40.0 + i for i in range(n)],  # increasing height
    })


def _render_all(video, tall, k, tmp, tl):
    return [f"img{i}" for i in range(len(tall))]


def test_read_tracklet_votes_across_batches():
    # render returns one fake crop per requested; two batches both read our #7
    calls = {"n": 0}
    def fake_read(imgs, nums, model, our=None, opp=None):
        calls["n"] += 1
        return {"team": "ours", "number": 7, "confidence": 0.7, "reasoning": "seven"}
    num, conf, votes, reason, team = read_tracklet_number(
        "vid.mp4", _sub(), Path("/tmp"), 5, [7, 11], "m", crops=3, min_conf=0.5,
        batches=2, read_fn=fake_read, render_fn=_render_all)
    assert num == 7 and votes == 2 and conf == 0.7 and reason == "seven" and team == "ours"
    assert calls["n"] == 2                       # one read per batch


def test_read_tracklet_no_crops_is_none():
    num, conf, votes, reason, team = read_tracklet_number(
        "vid.mp4", _sub(), Path("/tmp"), 5, [7], "m", crops=3, min_conf=0.5,
        batches=2, read_fn=lambda *a, **k: {"team": "ours", "number": 7, "confidence": 0.9},
        render_fn=lambda *a: [])
    assert num is None and votes == 0 and reason == "no-crops" and team == "other"


def test_read_tracklet_gate_filters_disagreeing_lowconf():
    # batch1 reads 7 high-conf, batch2 reads 11 low-conf -> 11 dropped, 7 wins
    seq = iter([{"team": "ours", "number": 7, "confidence": 0.8, "reasoning": "7"},
                {"team": "ours", "number": 11, "confidence": 0.3, "reasoning": "11?"}])
    num, conf, votes, _r, team = read_tracklet_number(
        "vid.mp4", _sub(), Path("/tmp"), 5, [7, 11], "m", crops=3, min_conf=0.5,
        batches=2, read_fn=lambda *a, **k: next(seq), render_fn=_render_all)
    assert num == 7 and votes == 1 and team == "ours"


def test_read_tracklet_reports_opponent_team():
    # both batches read an opponent → team 'opponent' (caller must NOT draft it)
    num, conf, votes, _r, team = read_tracklet_number(
        "vid.mp4", _sub(), Path("/tmp"), 5, [7], "m", crops=3, min_conf=0.5,
        batches=2, render_fn=_render_all,
        read_fn=lambda *a, **k: {"team": "opponent", "number": 7, "confidence": 0.9, "reasoning": "blue"})
    assert team == "opponent"


def test_vote_team_majority_and_tie():
    from tracking.vlm_identity import vote_team
    assert vote_team([{"team": "ours"}, {"team": "ours"}, {"team": "opponent"}]) == "ours"
    assert vote_team([{"team": "ours"}, {"team": "opponent"}]) == "other"   # tie → conservative
    assert vote_team([]) == "other"
    assert vote_team([{"team": "opponent"}, {"team": "opponent"}]) == "opponent"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} vlm_identity tests passed.")

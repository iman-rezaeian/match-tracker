"""Unit tests for the identity-free pitch-orientation anchor.

Which end is ours decides heatmap orientation and attacking/defensive third. The
old anchor was the NAMED keeper's median x, which is circular (it trusts the
identity we're trying to validate) and was measurably fragile: on W8 that track
spans x = 1.6 -> 31.7 m inside one half, which no keeper does, so the median
landed mid-pitch. A wrong sign MIRRORS every heatmap.

The replacement needs no names: somebody stands in front of a goal for nearly the
whole half, and that is our keeper. Count distinct seconds of goal-mouth occupancy
per end, anchor on the most confident half, and alternate (teams switch at the
break). Measured on W8: H1 1461 vs 1231 s (weak 1.19x); H2 737 vs 1474/1505 s =
98% of the half (decisive 2.00x). Verified against an independent derivation from
team positional mass, and the keeper's 81.9% defensive share confirms the sign.

These tests cover the pure decision logic. Run: python -m post_game.test_orientation_anchor
"""
from __future__ import annotations


def _resolve(votes: dict[int, tuple[int, int]], min_conf: float = 1.5):
    """Mirror of the anchor decision in stats.compute_player_stats.

    votes: {period: (seconds_occupied_at_x0_end, seconds_occupied_at_L_end)}
    returns ({period: our_net_at_x0}, anchor_period, confident?)
    """
    if not votes:
        return {}, None, False

    def strength(v):
        return max(v) / max(1, min(v))

    best = max(votes, key=lambda k: strength(votes[k]))
    s0, sL = votes[best]
    anchor = s0 > sL
    n = max(votes)
    out = {pi: (anchor if ((pi - best) % 2 == 0) else (not anchor))
           for pi in range(1, n + 1)}
    return out, best, strength(votes[best]) >= min_conf


def test_w8_real_numbers():
    # H2 is decisive and says the L end (98% occupancy), so H1 must be x=0.
    res, best, ok = _resolve({1: (1461, 1231), 2: (737, 1474)})
    assert best == 2 and ok is True
    assert res[2] is False, "H2 keeper at the L end => our net is NOT at x=0"
    assert res[1] is True, "H1 must be the opposite end"


def test_halves_are_always_opposite():
    for v in [{1: (10, 10), 2: (10, 10)},
              {1: (900, 10), 2: (900, 10)},     # both would say x0 alone
              {1: (10, 900), 2: (10, 900)},
              {1: (500, 499), 2: (5, 900)}]:
        res, _, _ = _resolve(v)
        assert res[1] != res[2], f"not opposite for {v}: {res}"


def test_weak_half_does_not_anchor():
    res, best, _ = _resolve({1: (1000, 990), 2: (50, 1400)})
    assert best == 2


def test_ambiguous_is_flagged_not_guessed():
    # both halves near-tied → still returns a decision, but marked NOT confident
    res, best, ok = _resolve({1: (100, 99), 2: (100, 98)})
    assert ok is False, "a near-tie must be reported as ambiguous"
    assert res[1] != res[2]          # still self-consistent


def test_single_period():
    res, best, ok = _resolve({1: (1400, 100)})
    assert res == {1: True} and best == 1 and ok is True


def test_no_votes():
    res, best, ok = _resolve({})
    assert res == {} and best is None and ok is False


def test_keeper_end_is_our_net_not_the_attacking_one():
    # Sanity on semantics: the end with a body parked in front of it all half is
    # OUR goal (their keeper is at the other end but is not on our team's tracks
    # in the same volume after team filtering; the anchor uses the dominant one).
    res, _, _ = _resolve({1: (1500, 200)})
    assert res[1] is True, "occupancy at x=0 => our net at x=0"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} orientation-anchor tests passed.")

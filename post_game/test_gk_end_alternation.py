"""Regression test: the GK end-vote must ALTERNATE between halves.

Teams switch ends at halftime, so the two periods must resolve to OPPOSITE ends.
The old code voted each period independently with no such constraint, and on a
real game (W8 mri01pvelv46d) both halves voted the same end — H1's vote was a weak
1.56x (near0/nearL 31433/49162) while H2's was a decisive 3.81x — so the whole
first half hunted the keeper at the OPPONENT's goal and credited a non-keeper
tracklet. Independently confirmed from team positional mass: H1 our-end = end0,
H2 our-end = endL (they DO alternate).

The fix anchors on the most confident period and alternates from it. These tests
lock that behaviour on the pure decision logic.

Run: python -m post_game.test_gk_end_alternation
"""
from __future__ import annotations


def _resolve(votes: dict[int, tuple[int, int]]) -> dict[int, bool]:
    """Mirror of the anchored+alternating decision in identity_assign.

    votes: {period_index: (near0_samples, nearL_samples)}
    returns {period_index: our_end_is_0}
    """
    if not votes:
        return {}

    def strength(v):
        a, b = v
        return max(a, b) / max(1, min(a, b))

    pi_best = max(votes, key=lambda k: strength(votes[k]))
    a, b = votes[pi_best]
    anchor = a >= b
    return {pi: (anchor if ((pi - pi_best) % 2 == 0) else (not anchor))
            for pi in votes}


def test_w8_real_numbers_alternate_and_follow_the_strong_half():
    # The actual measured W8 votes. H2 is decisive (3.81x) and says endL (near0
    # 13457 < nearL 51249 => our_end_is_0 False). H1 must therefore be end0.
    out = _resolve({0: (31433, 49162), 1: (13457, 51249)})
    assert out[1] is False, "H2 (the confident half) must keep its own verdict"
    assert out[0] is True, "H1 must be flipped to the OPPOSITE end"
    assert out[0] != out[1], "halves must be opposite — teams switch at halftime"


def test_weak_half_never_overrides_strong_half():
    # H1 nearly tied (weak), H2 lopsided. The lopsided one anchors.
    out = _resolve({0: (1000, 1010), 1: (50, 5000)})
    assert out[1] is False and out[0] is True


def test_anchor_can_be_the_first_half():
    # If H1 is the confident one, it anchors and H2 alternates off it.
    out = _resolve({0: (9000, 100), 1: (500, 520)})
    assert out[0] is True, "H1 decisive: near0 >> nearL => end0"
    assert out[1] is False


def test_single_period_behaves_as_before():
    # One period => no alternation to apply; verdict is its own vote.
    assert _resolve({0: (10, 900)}) == {0: False}
    assert _resolve({0: (900, 10)}) == {0: True}


def test_no_votes_is_empty():
    assert _resolve({}) == {}


def test_halves_are_always_opposite_whatever_the_votes():
    # Property: for any two-period input, the results must differ.
    cases = [
        {0: (1, 1), 1: (1, 1)},
        {0: (100, 1), 1: (100, 1)},          # both would vote end0 independently
        {0: (1, 100), 1: (1, 100)},          # both would vote endL independently
        {0: (5000, 4999), 1: (2, 9000)},
    ]
    for v in cases:
        out = _resolve(v)
        assert out[0] != out[1], f"halves not opposite for {v}: {out}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} gk-end-alternation tests passed.")

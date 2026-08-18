"""The heatmap is a kernel density estimate, not a histogram.

A raw histogram discarded almost everything the coach's clicks carried: at a 12x8
grid two independent halves of one player's clicks agreed at 0.15, which is noise.
The same clicks through a Gaussian kernel agree at 0.67. The coach was right that
the data supported a finer map than was being produced.
"""

from __future__ import annotations

import numpy as np
import pytest

from post_game.click_samples import HEATMAP_BANDWIDTH_M, kde_heatmap


def test_grid_is_normalised_and_correctly_shaped():
    g = kde_heatmap(np.array([10.0, 20.0]), np.array([5.0, 15.0]),
                    50.0, 30.0, (12, 8))
    assert g.shape == (12, 8)
    assert g.sum() == pytest.approx(1.0)


def test_mass_concentrates_where_the_clicks_are():
    """A player clicked only near his own goal must not show attacking presence."""
    d = np.full(20, 5.0)
    w = np.full(20, 15.0)
    g = kde_heatmap(d, w, 60.0, 30.0, (6, 4))
    assert g[0].sum() > g[-1].sum() * 20


def test_every_cell_is_defined_unlike_a_histogram():
    """The point of a kernel: no cell is empty just because no click landed in it,
    which is what made a fine histogram grid unusable."""
    g = kde_heatmap(np.array([25.0]), np.array([15.0]), 50.0, 30.0, (12, 8))
    assert (g > 0).all()


def test_two_distinct_players_produce_distinct_maps():
    """Guards the blur trap: a bandwidth wide enough to smooth everything makes
    every player look the same, which scores well on stability and is useless."""
    left = kde_heatmap(np.random.default_rng(0).normal(10, 3, 40),
                       np.random.default_rng(1).normal(5, 3, 40),
                       60.0, 30.0, (12, 8))
    right = kde_heatmap(np.random.default_rng(2).normal(50, 3, 40),
                        np.random.default_rng(3).normal(25, 3, 40),
                        60.0, 30.0, (12, 8))
    corr = np.corrcoef(left.ravel(), right.ravel())[0, 1]
    assert corr < 0.3, "opposite corners of the pitch must not look alike"


def test_bandwidth_stays_in_the_measured_range():
    """Reliability rises monotonically with bandwidth while distinctness falls;
    6 m was the measured optimum and 12 m was a prettier picture of nothing."""
    assert 3.0 <= HEATMAP_BANDWIDTH_M <= 8.0


def test_wider_bandwidth_provably_blurs_neighbouring_players_together():
    """Pins the reason the bandwidth is capped, so nobody raises it to make the
    agreement number look better.

    Uses ADJACENT players, which is the case that matters and the case that
    breaks. Two players at OPPOSITE corners behave the opposite way -- their
    correlation goes more NEGATIVE as the kernel widens, because each one's mass
    spreads into the region the other leaves empty. Testing that pair would have
    "proved" wide bandwidths keep players distinct, which is exactly backwards for
    the teammates a coach actually needs to tell apart.
    """
    rng = np.random.default_rng(0)
    a_d, a_w = rng.normal(28, 4, 40), rng.normal(14, 4, 40)
    b_d, b_w = rng.normal(36, 4, 40), rng.normal(17, 4, 40)   # ~8 m away
    tight = np.corrcoef(
        kde_heatmap(a_d, a_w, 60.0, 30.0, (12, 8), 4.0).ravel(),
        kde_heatmap(b_d, b_w, 60.0, 30.0, (12, 8), 4.0).ravel())[0, 1]
    blurred = np.corrcoef(
        kde_heatmap(a_d, a_w, 60.0, 30.0, (12, 8), 20.0).ravel(),
        kde_heatmap(b_d, b_w, 60.0, 30.0, (12, 8), 20.0).ravel())[0, 1]
    assert blurred > tight + 0.2, (
        f"a wide kernel must merge neighbouring players "
        f"(tight {tight:.2f} -> blurred {blurred:.2f})")


def test_split_half_agreement_beats_a_histogram_on_the_same_points():
    """The headline claim, reproduced on synthetic data with the same shape as the
    real clicks: ~23 samples over a 12x8 grid."""
    rng = np.random.default_rng(7)
    d = rng.normal(30, 8, 24)
    w = rng.normal(15, 6, 24)

    def halves(build):
        cs = []
        for _ in range(80):
            idx = rng.permutation(len(d))
            a, b = idx[:12], idx[12:]
            A, B = build(d[a], w[a]), build(d[b], w[b])
            if A.std() > 0 and B.std() > 0:
                cs.append(np.corrcoef(A.ravel(), B.ravel())[0, 1])
        return float(np.median(cs))

    def hist(dd, ww):
        H, _, _ = np.histogram2d(dd, ww, bins=(12, 8), range=[[0, 60], [0, 30]])
        return H

    kde = halves(lambda dd, ww: kde_heatmap(dd, ww, 60.0, 30.0, (12, 8)))
    assert kde > halves(hist)

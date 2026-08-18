"""The heatmap colour ramp must distinguish a keeper from an outfielder.

A KDE with a 6 m bandwidth produces a STRICTLY POSITIVE grid — the far end of the
pitch holds ~0.0001, not 0. The original ramp treated "not exactly zero" as
"present" and applied an alpha FLOOR of 0.25, so all 96 cells painted. Measured on
Garland's real published grid (78% of his mass in his own two rows, a 4000x range
top to bottom), alpha spanned only 0.25 -> 0.37: a uniform green sheet, with the
pitch markings showing through as spurious "bands". The keeper looked like he had
played everywhere, contradicting the `Def 99% / avg 2.4 m out` printed beside it.

This mirrors the JSX ramp in Python and asserts the properties that make the
picture readable, rather than pinning exact colours.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"

FLOOR = 0.04


def alpha(v: float, peak: float) -> float:
    """Python mirror of PlayerHeatmap's `heat()` alpha."""
    rel = v / peak if peak else 0.0
    if not rel >= FLOOR:
        return 0.0
    a = ((rel - FLOOR) / (1 - FLOOR)) ** 0.75
    return 0.10 + 0.80 * a


def _grid(row_mass: list[float]) -> list[float]:
    """12x8 grid with each row's mass spread over a centred bump, normalised."""
    w = [0.05, 0.10, 0.18, 0.17, 0.17, 0.18, 0.10, 0.05]
    g = [m * x for m in row_mass for x in w]
    s = sum(g)
    return [v / s for v in g]


# Garland's real per-row mass from analytics/v1 on mrhvbvwi1gjpn.
KEEPER = _grid([0.442, 0.334, 0.157, 0.047, 0.010, 0.004,
                0.003, 0.002, 0.001, 0.0002, 0.0001, 0.0001])
OUTFIELD = _grid([0.02, 0.05, 0.09, 0.13, 0.16, 0.16,
                  0.14, 0.10, 0.07, 0.05, 0.02, 0.01])


def _painted(grid: list[float]) -> int:
    peak = max(grid)
    return sum(1 for v in grid if alpha(v, peak) > 0)


def test_a_keeper_does_not_paint_the_whole_pitch():
    """The bug: 96/96 cells painted, so his box was indistinguishable."""
    assert _painted(KEEPER) < len(KEEPER) / 2


def test_the_keepers_far_half_is_completely_unpainted():
    """He never went there; the grid says ~0.0001, which must render as nothing."""
    peak = max(KEEPER)
    far = KEEPER[6 * 8:]          # rows 6-11 = opponent half
    assert all(alpha(v, peak) == 0 for v in far)


def test_an_outfielder_still_paints_broadly():
    """The threshold must not erase a player who genuinely roamed."""
    assert _painted(OUTFIELD) > len(OUTFIELD) * 0.7


def test_keeper_and_outfielder_are_visually_distinguishable():
    assert _painted(OUTFIELD) > _painted(KEEPER) * 2


def test_the_peak_uses_most_of_the_alpha_range():
    """A peak that paints at the same opacity as the tail conveys nothing."""
    peak = max(KEEPER)
    assert alpha(peak, peak) > 0.85
    lo = min(a for a in (alpha(v, peak) for v in KEEPER) if a > 0)
    assert alpha(peak, peak) - lo > 0.5, "alpha range is too compressed to read"


def test_normalisation_is_against_the_peak_not_one():
    """`Math.max(1, ...grid)` made every cell near-zero: these are densities
    summing to 1, so a 12x8 peak cell is ~0.1, never near 1."""
    src = JSX.read_text()
    i = src.index("function PlayerHeatmap(")
    body = src[i:i + 2500]
    assert "Math.max(...grid) || 1" in body
    assert "Math.max(1, ...grid)" not in body


def test_the_jsx_keeps_a_threshold_and_no_alpha_floor():
    src = JSX.read_text()
    i = src.index("function PlayerHeatmap(")
    body = src[i:i + 3000]
    assert re.search(r"const FLOOR = 0?\.04", body), "visibility threshold gone"
    assert "0.25 + 0.55" not in body, "the 0.25 alpha floor is back"


def test_markings_are_drawn_after_the_heat_cells():
    """Semi-transparent cells over the lines washed them into fake 'bands'."""
    src = JSX.read_text()
    i = src.index("function PlayerHeatmap(")
    body = src[i:i + 4000]
    heat = body.index("heat cells UNDER the markings") if "heat cells UNDER the markings" in body \
        else body.index("Heat cells UNDER the markings")
    marks = body.index("pitch markings, drawn last")
    assert heat < marks


if __name__ == "__main__":
    import traceback
    bad = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                v()
                print(f"ok   {k}")
            except Exception:
                bad += 1
                print(f"FAIL {k}")
                traceback.print_exc()
    raise SystemExit(1 if bad else 0)

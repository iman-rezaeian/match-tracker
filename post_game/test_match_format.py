"""Match format (7v7 / 9v9) and everything the pipeline derives from it.

The team plays 7v7 in Canadian festivals and 9v9 in US tournaments from the
2026-27 season. Two properties matter more than the new behaviour itself:

* **7v7 must be bit-identical to before the format existed.** Every game in the
  corpus predates the field, so any change to the 7v7 path silently invalidates
  a cached track set or moves a published number.
* **The 9v9 path must not fail silently.** The per-frame detection cap is the
  dangerous one: it drops the shortest-lived tracks first, so a cap that is too
  small deletes a just-subbed-on player rather than raising anything.
"""

from __future__ import annotations

import numpy as np
import pytest

from . import config
from .formation import _label_formation_outfield


# --- format -> derived numbers -------------------------------------------

def test_legacy_and_unknown_formats_read_as_7v7():
    """An absent format means 7v7, not unknown — every pre-2026-27 game."""
    for value in (None, "", "7v7"):
        assert config.on_field_per_side(value) == 7
    # Garbage must not crash or silently become a bigger game.
    assert config.on_field_per_side("11v11") == 7
    assert config.on_field_per_side("nonsense") == 7


def test_9v9_reads_as_nine_a_side():
    assert config.on_field_per_side("9v9") == 9


def test_topn_budget_is_unchanged_for_7v7():
    """20 was the old hardcoded literal. Changing it would invalidate every
    cached Stage-2 track set, so this is a compatibility lock, not a preference."""
    assert config.topn_per_frame("7v7") == 20
    assert config.topn_per_frame(None) == 20


def test_topn_budget_grows_with_the_format():
    """9v9 puts 20 players + a referee on the pitch, over the old cap of 20."""
    assert config.topn_per_frame("9v9") == 24
    assert config.topn_per_frame("9v9") > 2 * config.on_field_per_side("9v9")


def test_topn_budget_always_clears_both_teams_plus_referee():
    for fmt in ("7v7", "9v9"):
        assert config.topn_per_frame(fmt) >= 2 * config.on_field_per_side(fmt) + 1


# --- calibration width band ---------------------------------------------

def test_width_band_admits_a_legal_9v9_pitch():
    """US Youth Soccer 9v9 is 45-55 yd (41-50 m) wide. The band must not merely
    contain 50 m — calibration_qc rejects a width sitting within 0.1 m of a
    bound (it means the optimizer ran out of room), so the ceiling has to clear
    a legal wide pitch by more than that."""
    assert config.CALIB_WIDTH_MAX >= 50.0 + 0.1
    assert config.CALIB_WIDTH_MIN <= 41.0


def test_width_band_still_rejects_implausible_fields():
    """The band is a real gate, not a formality — it must still catch a fit that
    solved to something no youth pitch could be."""
    assert config.CALIB_WIDTH_MIN >= 15.0
    assert config.CALIB_WIDTH_MAX <= 80.0


def test_solver_bounds_come_from_the_same_source_as_the_qc_gate():
    """These were two separate literals encoding one fact. A width pinned at a
    bound the gate then accepts is exactly the silent-bad-calibration case."""
    import inspect

    from . import calibration_solve

    sig = inspect.signature(calibration_solve.solve_sphere_scaled)
    # None means "resolve from config at call time" rather than a frozen copy.
    assert sig.parameters["w_bounds"].default is None


# --- formation labels ----------------------------------------------------

def _label(depths: list[float]) -> str:
    return _label_formation_outfield(np.array(depths, dtype=float))


@pytest.mark.parametrize("depths,expected", [
    # 7v7: 6 outfield. These are the shapes the coach's board actually produces.
    ([0.10, 0.12, 0.45, 0.47, 0.49, 0.90], "2-3-1"),
    ([0.10, 0.12, 0.14, 0.50, 0.52, 0.90], "3-2-1"),
])
def test_7v7_labels_are_three_rows(depths, expected):
    assert _label(depths) == expected


def test_a_genuine_9v9_four_row_shape_is_labelled_with_four_rows():
    """8 outfield in four clear banks. The old labeller always returned three
    numbers, so this read as a plausible-looking wrong shape."""
    assert _label([0.10, 0.12, 0.14, 0.40, 0.42, 0.68, 0.70, 0.95]) == "3-2-2-1"
    assert _label([0.10, 0.12, 0.42, 0.44, 0.46, 0.70, 0.72, 0.96]) == "2-3-2-1"


def test_a_genuine_9v9_three_row_shape_stays_three_rows():
    """A fourth row always fits at least as well as three, so it has to pay for
    itself. 3-3-2 must not be over-fitted into four banks."""
    assert _label([0.10, 0.12, 0.14, 0.45, 0.47, 0.49, 0.88, 0.90]) == "3-3-2"


def test_an_evenly_spread_line_is_not_read_as_four_banks():
    """Eight players at even depths are not a four-row formation; without a
    cost-ratio test this came out as 2-2-2-2."""
    assert _label(list(np.linspace(0.1, 0.9, 8))).count("-") == 2


def test_only_a_real_9v9_board_can_gain_a_fourth_row():
    """Deliberately an exact count, not a minimum: a board carrying MORE than 8
    outfielders is a corrupt board (halftime-welded tracklets put 10 on one
    board in the real corpus), and relabelling those as a richer formation would
    dress up a known data bug."""
    assert config.on_field_per_side("9v9") - 1 == 8
    # 10 outfield, four obvious banks — still three rows, because the board is
    # not a formation we believe in.
    welded = [0.10, 0.11, 0.12, 0.40, 0.41, 0.68, 0.69, 0.70, 0.95, 0.96]
    assert _label(welded).count("-") == 2

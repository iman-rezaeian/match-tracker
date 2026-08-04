"""Unit tests for the calibration-quality gate (calibration_qc.evaluate_calibration).

Pure-function tests — no Firestore, no video. Fixtures mirror the REAL stored
calibrations (the two good scaled_lsq fits + the legacy un-anchored ones) plus
synthetic edge cases the live data doesn't cover yet. Run standalone:
`python -m post_game.test_calibration_qc` or under pytest.
"""
from __future__ import annotations

from .calibration_qc import evaluate_calibration
from . import config


def _scaled(rms, width, length=54.68):
    """A scaled_lsq calibration dict (the trustworthy solver)."""
    return {
        "length_m": length,
        "width_m": width,
        "field_key": "test-field",
        "ground_similarity": {"solver": "scaled_lsq", "rms_m": rms},
    }


def _legacy(rms, width=35.0):
    """A legacy planar calibration (no scale anchor, template width)."""
    return {
        "length_m": 50.0,
        "width_m": width,
        "ground_similarity": {"rms_m": rms},  # no 'solver' tag
    }


# --- Real-data-shaped cases -------------------------------------------------

def test_good_scaled_fits_pass():
    # W7-field (0.27 m / 31.1 m) and W8 (0.65 m / 30.3 m) — the real good fits.
    assert evaluate_calibration(_scaled(0.2674, 31.07)).ok
    assert evaluate_calibration(_scaled(0.6473, 30.32)).ok


def test_legacy_unanchored_fits_are_blocked():
    # The five real legacy games (RMS 0.94-1.47) must all block toward re-calibration.
    for rms in (0.94, 1.27, 1.32, 1.35, 1.47):
        v = evaluate_calibration(_legacy(rms))
        assert not v.ok
        assert any("scale-approximate" in r.lower() for r in v.reasons)


# --- RMS gate ---------------------------------------------------------------

def test_scaled_fit_over_rms_limit_blocks():
    v = evaluate_calibration(_scaled(config.CALIB_MAX_RMS_M + 0.5, 31.0))
    assert not v.ok
    assert any("RMS" in r for r in v.reasons)


def test_scaled_fit_just_under_rms_limit_passes():
    assert evaluate_calibration(_scaled(config.CALIB_MAX_RMS_M - 0.01, 31.0)).ok


# --- Width sanity -----------------------------------------------------------

def test_implausible_width_blocks():
    assert not evaluate_calibration(_scaled(0.4, 12.0)).ok   # too narrow
    assert not evaluate_calibration(_scaled(0.4, 60.0)).ok   # too wide


def test_bound_pinned_width_blocks():
    v = evaluate_calibration(_scaled(0.4, config.CALIB_WIDTH_MAX))
    assert not v.ok
    assert any("pinned" in r.lower() for r in v.reasons)


# --- Cross-field consistency ------------------------------------------------

def test_first_calibration_no_prior_passes_and_carries_width():
    # No stored width for this field yet -> consistency check skipped.
    v = evaluate_calibration(_scaled(0.4, 30.5), prior_field=None)
    assert v.ok
    assert v.width_m == 30.5   # the caller records this for next time


def test_consistent_width_with_prior_passes():
    v = evaluate_calibration(_scaled(0.5, 30.3), prior_field={"width_m": 31.1, "length_m": 54.68})
    assert v.ok  # 0.8 m apart < 2.5 m tol -> the real W7/W8 case


def test_inconsistent_width_with_prior_blocks():
    v = evaluate_calibration(_scaled(0.4, 25.0), prior_field={"width_m": 31.0, "length_m": 54.68})
    assert not v.ok
    assert any("disagrees" in r.lower() for r in v.reasons)


# --- Length anchor mismatch -------------------------------------------------

def test_length_mismatch_with_prior_blocks():
    v = evaluate_calibration(_scaled(0.4, 30.5, length=50.0),
                             prior_field={"width_m": 30.0, "length_m": 54.68})
    assert not v.ok
    assert any("length" in r.lower() for r in v.reasons)


# --- Degenerate inputs ------------------------------------------------------

def test_missing_calibration_blocks():
    assert not evaluate_calibration(None).ok
    assert not evaluate_calibration({}).ok


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} calibration-QC tests passed.")

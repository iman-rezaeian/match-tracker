"""Calibration-quality gate — makes "Run Analysis" self-sufficient.

The pipeline used to validate only that a calibration EXISTS, never that it is
GOOD, so a present-but-wrong calibration ran a multi-hour track silently and a
developer had to eyeball RMS / notice width inconsistencies by hand. This module
is the single source of truth for "is this calibration trustworthy enough to
run?" — consumed by both the Streamlit UI (to disable the Run button) and the
pipeline (to hard-block before Stage 2). Pure function, no I/O, so it is trivial
to unit-test against the real stored calibrations.

Thresholds live in config (CALIB_*) and are justified there by the real data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class CalibrationVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)   # why it was blocked (empty if ok)
    rms_m: Optional[float] = None
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    solver: Optional[str] = None

    def summary(self) -> str:
        head = "OK" if self.ok else "BLOCKED"
        bits = []
        if self.rms_m is not None:
            bits.append(f"RMS {self.rms_m:.2f} m")
        if self.width_m is not None:
            bits.append(f"width {self.width_m:.1f} m")
        if self.length_m is not None:
            bits.append(f"length {self.length_m:.1f} m")
        if self.solver:
            bits.append(self.solver)
        line = f"Calibration {head}" + (" — " + ", ".join(bits) if bits else "")
        if self.reasons:
            line += "\n  - " + "\n  - ".join(self.reasons)
        return line


def evaluate_calibration(
    cal: Optional[dict],
    prior_field: Optional[dict] = None,
) -> CalibrationVerdict:
    """Judge whether a calibration doc is trustworthy enough to run analytics.

    Args:
        cal: the raw per-game `calibration` dict (as stored on the game doc —
             has `ground_similarity`, `length_m`, `width_m`, `field_key`). Not
             the parsed FieldCalibration, which drops the solver tag/residuals.
        prior_field: the stored field-scale record for THIS game's field_key
             (firestore_io.get_field_scale), or None if the field is new. Used
             for the run-to-run width-consistency check.

    Returns a CalibrationVerdict; `ok=False` with `reasons` when it should block.
    """
    if not cal:
        return CalibrationVerdict(ok=False, reasons=["No calibration on this game — calibrate first (step 2)."])

    gs = cal.get("ground_similarity") or {}
    solver = gs.get("solver")
    rms_m = gs.get("rms_m")
    width_m = cal.get("width_m")
    length_m = cal.get("length_m")
    reasons: list[str] = []

    # --- Solver era: only the scale-anchored solver is trustworthy for metrics.
    # Legacy planar / tilt-only fits have no absolute scale (width was forced to
    # the template default), so distance/speed are scale-approximate. Block and
    # send the coach to re-calibrate with the map length.
    if solver != "scaled_lsq":
        reasons.append(
            "Scale-approximate calibration (no map-length anchor) — re-calibrate "
            "with the field length so distances/speeds are metric."
        )

    # --- RMS gate (only meaningful for the scaled solver).
    if solver == "scaled_lsq":
        if rms_m is None:
            reasons.append("Calibration has no RMS residual — re-calibrate.")
        elif rms_m > config.CALIB_MAX_RMS_M:
            reasons.append(
                f"Calibration fit is poor: RMS {rms_m:.2f} m > {config.CALIB_MAX_RMS_M:.1f} m "
                f"limit — re-click the landmarks more precisely."
            )

    # --- Width sanity: plausible band + not pinned at the solver's bounds.
    if width_m is None:
        reasons.append("Calibration has no field width — re-calibrate.")
    else:
        if width_m < config.CALIB_WIDTH_MIN or width_m > config.CALIB_WIDTH_MAX:
            reasons.append(
                f"Solved field width {width_m:.1f} m is implausible "
                f"(expected {config.CALIB_WIDTH_MIN:.0f}-{config.CALIB_WIDTH_MAX:.0f} m) — "
                f"check the length anchor and corner clicks."
            )
        else:
            # A width sitting exactly on a bound means the optimizer ran out of
            # room and the shape is non-identifiable from the clicks.
            edge_tol = 0.1
            if (abs(width_m - config.CALIB_WIDTH_MIN) < edge_tol
                    or abs(width_m - config.CALIB_WIDTH_MAX) < edge_tol):
                reasons.append(
                    f"Solved width {width_m:.1f} m is pinned at a solver bound — "
                    f"the clicks don't constrain the field shape; re-calibrate."
                )

        # --- Cross-field consistency: the SAME field must solve to ~the same
        # width run-to-run. A big jump means one calibration mis-clicked the
        # touchline. First calibration of a field has no prior -> skip (record it).
        prior_w = (prior_field or {}).get("width_m")
        if prior_w is not None and width_m is not None:
            if abs(width_m - float(prior_w)) > config.CALIB_WIDTH_CONSISTENCY_TOL_M:
                reasons.append(
                    f"Field width {width_m:.1f} m disagrees with this field's prior "
                    f"calibration ({float(prior_w):.1f} m) by more than "
                    f"{config.CALIB_WIDTH_CONSISTENCY_TOL_M:.1f} m — one of them "
                    f"mis-clicked the touchline; re-calibrate."
                )

    # --- Length must match the map anchor it was solved against (it's pinned,
    # so a mismatch signals a solver/data bug). Only checkable with a prior.
    prior_len = (prior_field or {}).get("length_m")
    if solver == "scaled_lsq" and prior_len is not None and length_m is not None:
        if abs(length_m - float(prior_len)) > 1.0:
            reasons.append(
                f"Solved length {length_m:.1f} m diverges from the field's map "
                f"length {float(prior_len):.1f} m — anchor mismatch; re-calibrate."
            )

    return CalibrationVerdict(
        ok=(len(reasons) == 0),
        reasons=reasons,
        rms_m=float(rms_m) if rms_m is not None else None,
        width_m=float(width_m) if width_m is not None else None,
        length_m=float(length_m) if length_m is not None else None,
        solver=solver,
    )

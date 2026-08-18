"""Keep tracking/pwa_score.py's event set in step with the JSX EVENT_TYPES map.

The JS scorer derives its non-silent set from EVENT_TYPES directly (`!def.silent`),
so adding an event type there is free. The Python mirror hardcodes the same set,
so the two drift silently — PEN_MISSED / OPP_PEN_MISSED were live in the app for
some time while Python ignored them, under-counting Involvement on any game
containing one and shifting the squad prior for every other player. That makes
pre-change and post-change offline baselines incomparable, which is worse than
the raw scoring error. This test fails the moment they diverge again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"
PY_SCORE = REPO / "tracking" / "pwa_score.py"


def _jsx_event_types() -> dict[str, str]:
    """{EVENT_ID: rest-of-line} for every entry of the JSX EVENT_TYPES map."""
    src = JSX.read_text()
    start = src.index("const EVENT_TYPES = {")
    depth = 0
    end = None
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "unterminated EVENT_TYPES map"
    return dict(re.findall(r"^\s{2}([A-Z_]+)\s*:\s*\{(.*)$", src[start:end], re.M))


def _python_nonsilent() -> set[str]:
    m = re.search(r"KNOWN_NONSILENT_EVENTS = frozenset\(\{(.*?)\}\)",
                  PY_SCORE.read_text(), re.S)
    assert m, "KNOWN_NONSILENT_EVENTS not found in pwa_score.py"
    return set(re.findall(r'"([A-Z_]+)"', m.group(1)))


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_python_mirror_matches_jsx_nonsilent_events():
    types = _jsx_event_types()
    jsx_nonsilent = {k for k, rest in types.items() if "silent: true" not in rest}
    py = _python_nonsilent()
    assert jsx_nonsilent, "parsed no event types — the parser broke, not the mirror"
    assert jsx_nonsilent == py, (
        f"pwa_score.KNOWN_NONSILENT_EVENTS is out of step with EVENT_TYPES.\n"
        f"  missing from Python: {sorted(jsx_nonsilent - py)}\n"
        f"  stale in Python:     {sorted(py - jsx_nonsilent)}"
    )


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_silent_events_are_excluded_from_the_python_set():
    types = _jsx_event_types()
    silent = {k for k, rest in types.items() if "silent: true" in rest}
    assert silent, "expected at least POSITION/BOOKMARK to be silent"
    assert not (silent & _python_nonsilent())


# --------------------------------------------------------------------------
# Logging-coverage weighting (Aug 2026)
#
# The event SET was mirrored but the season RATE FORMULA was not, so when the
# per-pillar logging weight was added to the JSX the Python scorer silently kept
# dividing by all minutes played. Offline scores would then disagree with the app
# for every player — the exact class of drift this file exists to prevent.
# --------------------------------------------------------------------------

def _jsx_pillar_types() -> dict[str, set[str]]:
    src = JSX.read_text()
    start = src.index("const PILLAR_TYPES = {")
    block = src[start:src.index("};", start)]
    out: dict[str, set[str]] = {}
    for key in ("atk", "def", "dec"):
        m = re.search(rf"{key}: \[(.*?)\]", block, re.S)
        assert m, f"PILLAR_TYPES.{key} not found in the JSX"
        out[key] = set(re.findall(r"'([A-Z_]+)'", m.group(1)))
    return out


def _py_pillar_types() -> dict[str, set[str]]:
    src = PY_SCORE.read_text()
    start = src.index("PILLAR_EVENT_TYPES")
    block = src[start:src.index("\n}", start)]
    out: dict[str, set[str]] = {}
    for key in ("atk", "def", "dec"):
        m = re.search(rf'"{key}": frozenset\(\{{(.*?)\}}\)', block, re.S)
        assert m, f"PILLAR_EVENT_TYPES['{key}'] not found in pwa_score.py"
        out[key] = set(re.findall(r'"([A-Z_]+)"', m.group(1)))
    return out


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_pillar_coverage_types_match():
    assert _jsx_pillar_types() == _py_pillar_types()


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_the_coverage_floor_matches():
    jsx = re.search(r"const LOG_FLOOR = ([\d.]+)", JSX.read_text())
    py = re.search(r"LOG_COVERAGE_FLOOR = ([\d.]+)", PY_SCORE.read_text())
    assert jsx and py, "logging-coverage floor missing from one side"
    assert float(jsx.group(1)) == float(py.group(1))


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_both_sides_divide_each_pillar_by_its_own_logged_minutes():
    """The bug this guards: one side per-pillar, the other by all minutes."""
    jsx = JSX.read_text()
    assert "rate(row.atk, squadRates.atk, row.wmin.atk)" in jsx
    assert "rate(row.def, squadRates.def, row.wmin.def)" in jsx
    py = PY_SCORE.read_text()
    assert 'rate(row["atk"], squad_rates["atk"], row["wmin"]["atk"])' in py
    assert 'rate(row["def"], squad_rates["def"], row["wmin"]["def"])' in py
    # And neither may still divide by a single scalar wmin.
    assert "(row.wmin + M)" not in jsx
    assert '(row["wmin"] + M)' not in py

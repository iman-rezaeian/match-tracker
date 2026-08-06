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

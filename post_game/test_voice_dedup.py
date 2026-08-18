"""Tests for collapsing the coach's excited repeats into one event per occurrence.

The bug these pin was a DUPLICATE-GOAL GENERATOR: re-extracting the Amherstburg
narration turned one goal into four GOAL drafts, because the coach shouts "Goal!
Goal!" for a minute and a half and the old window was measured from the kept event,
so each >20 s gap started a fresh "occurrence". Accepting those drafts would have
overstated the score — the one number in the app nobody would think to doubt.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracking.voice_extract import (CELEBRATION_WINDOW_S, DEFAULT_WINDOW_S,
                                    dedupe_repeats)


def _ev(t, typ, player=None, conf=0.5):
    return {"videoTimeS": float(t), "type": typ, "player_id": player,
            "player_first_name": player, "confidence": conf}


def test_one_goal_shouted_for_96s_is_one_goal():
    """The real Amherstburg case, with its real timings.

    "Goal!" at 267, 299, 325, 363 — 96 s of one celebration. Every consecutive gap
    (32, 26, 38 s) exceeded the old 20 s window, so the old code emitted FOUR goals.
    """
    out = dedupe_repeats([_ev(t, "GOAL") for t in (267, 299, 325, 363)])
    assert len(out) == 1, f"one goal became {len(out)} drafts"
    assert out[0]["videoTimeS"] == 267, "the event must keep the FIRST mention's time"


def test_two_different_scorers_in_one_window_stay_two_goals():
    """The failure mode the fix must not introduce.

    A long celebration window would happily merge a genuine second goal. Keying on
    the player keeps them apart — deleting a real goal is worse than keeping a dupe.
    """
    out = dedupe_repeats([_ev(100, "GOAL", "p_jason"),
                          _ev(130, "GOAL", "p_luca")])
    assert len(out) == 2
    assert {e["player_id"] for e in out} == {"p_jason", "p_luca"}


def test_a_bare_repeat_folds_into_the_named_goal():
    """"Goal by Jason" then "Goal! Goal!" is one goal, credited to Jason."""
    out = dedupe_repeats([_ev(100, "GOAL", "p_jason", conf=0.9),
                          _ev(120, "GOAL", None, conf=0.4),
                          _ev(150, "GOAL", None, conf=0.3)])
    assert len(out) == 1
    assert out[0]["player_id"] == "p_jason"


def test_a_name_heard_late_is_backfilled():
    """The first shout is often just "Goal!"; the name comes a beat later."""
    out = dedupe_repeats([_ev(100, "GOAL", None, conf=0.5),
                          _ev(115, "GOAL", "p_jason", conf=0.85)])
    assert len(out) == 1
    assert out[0]["player_id"] == "p_jason"
    assert out[0]["videoTimeS"] == 100, "time stays at the action, not the name"
    assert out[0]["confidence"] == 0.85, "keep the better confidence"


def test_two_duels_25s_apart_are_two_duels():
    """Process events must NOT get the celebration window.

    Nobody shouts "he lost it" for 75 s; two duels 25 s apart are two duels, and
    over-merging them would silently shrink the process-event corpus the season
    score is already starved of.
    """
    out = dedupe_repeats([_ev(100, "DUEL_LOSE", "p_ben"),
                          _ev(125, "DUEL_LOSE", "p_ben")])
    assert len(out) == 2


def test_a_process_repeat_inside_its_window_still_collapses():
    out = dedupe_repeats([_ev(100, "BALL_WIN", "p_ben"),
                          _ev(110, "BALL_WIN", "p_ben")])
    assert len(out) == 1


def test_chaining_collapses_a_long_run_of_close_mentions():
    """15 s apart each, 150 s total — one long celebration, not ten goals.

    This is what "measure from the last absorbed repeat" buys: the run collapses
    even though the span far exceeds any single window.
    """
    out = dedupe_repeats([_ev(t, "GOAL") for t in range(100, 251, 15)])
    assert len(out) == 1


def test_a_genuinely_later_goal_is_not_swallowed():
    """Beyond the window with no bridging mentions, it is a new goal."""
    out = dedupe_repeats([_ev(100, "GOAL"), _ev(100 + CELEBRATION_WINDOW_S + 10, "GOAL")])
    assert len(out) == 2


def test_different_types_never_merge():
    out = dedupe_repeats([_ev(100, "GOAL", "p_a"), _ev(105, "SHOT_ON", "p_a")])
    assert len(out) == 2


def test_the_windows_are_ordered_as_intended():
    assert CELEBRATION_WINDOW_S > DEFAULT_WINDOW_S


def test_empty_input():
    assert dedupe_repeats([]) == []


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

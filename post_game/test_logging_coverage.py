"""Logging-coverage weighting must not inflate outcome-driven pillars.

The coach taps while coaching, so how much gets logged varies enormously between
games. Measured over 12 real games: total action events swing 95 -> 18, and the
DEF share of them falls from 60% to 3% as he increasingly taps only goals and
shots. The old season rate divided each pillar's points by ALL minutes played, so
a defender's rating was watered down by every game the coach was too busy to tap.

Weighting each pillar's minutes by its own logging coverage fixes that — but the
FIRST version of the fix over-corrected badly, and these tests pin the reason.

⚠ THE TRAP: not every tap is discretionary. Measured across the same 12 games:

    SAVE      logged in 11/12 games, stdev 2.3  -> tracks OPPONENT SHOTS
    DUEL_WIN  ZERO in 8/12 games,    stdev 6.7  -> tracks COACH ATTENTION

Counting saves toward DEF coverage shrank the keeper's DEF denominator from 313
to 88 weighted minutes, inflating his rate 3.6x and putting him at 20.3 overall
against 10.1 for the next player. Outcome events must be excluded from coverage,
and only the discretionary SHARE of each pillar's exposure may be discounted.
"""

from __future__ import annotations

import pytest

from tracking.pwa_score import (DISCRETIONARY_SHARE, LOG_COVERAGE_FLOOR,
                                PILLAR_EVENT_TYPES, season_score)

OUTCOME = {"GOAL", "ASSIST", "SHOT_ON", "SHOT_OFF", "SAVE",
           "PEN_AWARDED", "PEN_CONCEDED", "OWN_GOAL"}


def test_outcome_events_are_not_coverage_signals():
    """Their count reflects what happened, not how much the coach tapped."""
    for pillar, types in PILLAR_EVENT_TYPES.items():
        leaked = types & OUTCOME
        assert not leaked, (
            f"{pillar} coverage counts outcome event(s) {sorted(leaked)} — this is "
            "what inflated the keeper's DEF rate 3.6x")


def test_saves_specifically_are_excluded():
    """The exact regression: SAVE is logged 11/12 games regardless of the coach."""
    assert "SAVE" not in PILLAR_EVENT_TYPES["def"]


def test_discretionary_shares_are_fractions_and_ordered():
    """DEC is nearly all discretionary; ATK is nearly all outcome events."""
    for k, v in DISCRETIONARY_SHARE.items():
        assert 0.0 <= v <= 1.0, f"{k} share out of range"
    assert DISCRETIONARY_SHARE["dec"] > DISCRETIONARY_SHARE["def"]
    assert DISCRETIONARY_SHARE["def"] > DISCRETIONARY_SHARE["atk"]


def test_the_floor_keeps_a_thin_game_contributing():
    """A zero denominator would produce an explosive rate."""
    assert 0.0 < LOG_COVERAGE_FLOOR < 1.0


# --------------------------------------------------------------------------
# End-to-end on synthetic games
# --------------------------------------------------------------------------

def _game(gid: str, *, duels: int, saves: int, minutes: int = 50,
          players=("p_gk", "p_out")) -> dict:
    """One finished game; both players on for the whole match."""
    start = 1_700_000_000_000
    events = []
    for i in range(duels):
        events.append({"id": f"d{i}", "type": "DUEL_WIN", "playerId": "p_out",
                       "period": 1, "elapsed": 60 + i, "at": start + 60_000 + i})
    for i in range(saves):
        events.append({"id": f"s{i}", "type": "SAVE", "playerId": "p_gk",
                       "period": 1, "elapsed": 120 + i, "at": start + 120_000 + i})
    return {
        "id": gid, "status": "finished", "date": f"2026-06-{int(gid[1:]) + 1:02d}",
        "startedAt": start, "endedAt": start + minutes * 60_000,
        "ourScore": 1, "oppScore": 1, "opponent": "X", "tournament": "league",
        "halfLengthMin": 25, "startingLineup": list(players),
        "gkPlayerId": "p_gk", "gkChanges": [], "events": events,
    }


ROSTER = [{"id": "p_gk", "name": "Keeper", "position": "GK"},
          {"id": "p_out", "name": "Outfielder"}]


def test_keeper_rate_is_not_inflated_by_sparse_duel_logging():
    """The regression, end to end.

    Two games: one where the coach logged duels heavily, one where he logged none.
    The keeper made the same saves in both. His DEF rate must NOT jump just
    because the second game's duel logging was thin.
    """
    both_logged = [_game("g1", duels=20, saves=4), _game("g2", duels=20, saves=4)]
    one_thin = [_game("g1", duels=20, saves=4), _game("g2", duels=0, saves=4)]

    gk_both = season_score("p_gk", both_logged, roster=ROSTER)
    gk_thin = season_score("p_gk", one_thin, roster=ROSTER)
    assert gk_both and gk_thin

    # Allow a modest shift, but nothing like the 3.6x the first version produced.
    ratio = gk_thin["defending"] / max(gk_both["defending"], 1e-9)
    assert ratio < 1.6, (
        f"keeper DEF inflated {ratio:.2f}x by another player's missing duel taps")


def test_an_unlogged_game_dilutes_a_defender_less_than_it_used_to():
    """The point of the change — but only PART of the dilution is removable.

    Same player, same duels per logged minute, in a season where the second game
    got no duel taps. Three reference points:

        old formula (no coverage weighting)  ~50% of the undiluted rate
        this implementation                  ~65%
        no dilution at all                   100%

    We deliberately stop short of 100%. DISCRETIONARY_SHARE['def'] = 0.5 keeps
    half of DEF's exposure undiscounted because half its POINTS are saves and
    other outcome events, whose count does not shrink when the coach stops
    tapping. Removing the rest of the dilution would re-inflate the keeper (the
    3.6x regression this file documents). Voice-driven post-game logging is the
    real fix for the missing taps; this only stops the score punishing them.
    """
    logged_once = [_game("g1", duels=20, saves=2), _game("g2", duels=0, saves=2)]
    logged_twice = [_game("g1", duels=20, saves=2), _game("g2", duels=20, saves=2)]

    a = season_score("p_out", logged_once, roster=ROSTER)
    b = season_score("p_out", logged_twice, roster=ROSTER)
    assert a and b
    ratio = a["defending"] / b["defending"]
    assert ratio > 0.55, f"barely better than the old full dilution ({ratio:.2f})"
    assert ratio < 0.95, (
        f"dilution fully removed ({ratio:.2f}) — check the keeper has not "
        "re-inflated; that trade is what DISCRETIONARY_SHARE guards")


def test_coverage_is_reported_for_the_ui():
    sc = season_score("p_out", [_game("g1", duels=20, saves=2)], roster=ROSTER)
    assert sc and "coverage" in sc
    for k in ("atk", "def", "dec"):
        assert 0 <= sc["coverage"][k] <= 100


def test_a_single_game_season_is_full_coverage():
    """With one game it IS the best-logged game, so nothing is discounted."""
    sc = season_score("p_out", [_game("g1", duels=20, saves=2)], roster=ROSTER)
    assert sc["coverage"]["def"] == pytest.approx(100.0, abs=0.5)


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

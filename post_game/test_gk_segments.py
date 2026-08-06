"""`_gk_segments` must read the key the PWA actually writes.

setGameGK in soccer_team_app.jsx appends `{at, gkPlayerId}` to game.gkChanges.
This function read `playerId`, so every keeper change was skipped and the
starting GK was treated as being in goal for the whole match — the mid-game
rotations these U10 games are full of vanished.
"""

from __future__ import annotations

from post_game.identity import _gk_segments


def test_reads_the_gkplayerid_key_the_pwa_writes():
    segs = _gk_segments("alice", [{"at": 600_000, "gkPlayerId": "bob"}])
    assert [s["playerId"] for s in segs] == ["alice", "bob"]
    assert segs[0]["to"] == 600_000
    assert segs[1]["from"] == 600_000 and segs[1]["to"] is None


def test_still_reads_the_legacy_playerid_key():
    segs = _gk_segments("alice", [{"at": 600_000, "playerId": "bob"}])
    assert [s["playerId"] for s in segs] == ["alice", "bob"]


def test_gkplayerid_wins_when_both_present():
    segs = _gk_segments("alice", [{"at": 1, "gkPlayerId": "bob", "playerId": "carol"}])
    assert segs[-1]["playerId"] == "bob"


def test_multiple_rotations_chain_in_order():
    segs = _gk_segments("alice", [
        {"at": 300_000, "gkPlayerId": "bob"},
        {"at": 900_000, "gkPlayerId": "carol"},
    ])
    assert [s["playerId"] for s in segs] == ["alice", "bob", "carol"]
    assert [s["from"] for s in segs] == [0, 300_000, 900_000]
    assert [s["to"] for s in segs] == [300_000, 900_000, None]


def test_change_with_no_keeper_is_skipped():
    """A cleared GK ({gkPlayerId: null}) must not open a nameless segment."""
    segs = _gk_segments("alice", [{"at": 300_000, "gkPlayerId": None}])
    assert [s["playerId"] for s in segs] == ["alice"]
    assert segs[0]["to"] is None


def test_no_starting_gk_still_picks_up_changes():
    segs = _gk_segments(None, [{"at": 300_000, "gkPlayerId": "bob"}])
    assert [s["playerId"] for s in segs] == ["bob"]


def test_no_data_yields_no_segments():
    assert _gk_segments(None, []) == []

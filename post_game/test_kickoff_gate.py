"""Unit tests for the kickoff-confirmation gate — both halves must be marked AND
confirmed by the coach before a full analytics run (the offsets anchor every
player's on-field window / minutes). Pure logic; no Firestore.

Run: python -m post_game.test_kickoff_gate
"""
from __future__ import annotations

from types import SimpleNamespace


def _missing(g) -> list[str]:
    """Mirror of the pipeline 1b gate (post_game/pipeline.py): which halves are
    not yet confirmed."""
    return [h for h, ok in (("1st", g.video_offset_h1_confirmed),
                            ("2nd", g.video_offset_h2_confirmed)) if not ok]


def _game(h1=False, h2=False):
    return SimpleNamespace(video_offset_h1_confirmed=h1, video_offset_h2_confirmed=h2)


def test_both_unconfirmed_blocks():
    assert _missing(_game(False, False)) == ["1st", "2nd"]


def test_h1_only_confirmed_still_blocks_on_h2():
    assert _missing(_game(True, False)) == ["2nd"]


def test_h2_only_confirmed_still_blocks_on_h1():
    assert _missing(_game(False, True)) == ["1st"]


def test_both_confirmed_passes():
    assert _missing(_game(True, True)) == []


def test_ui_gate_condition():
    # the UI disables Run Analysis unless BOTH confirmed (game dict form)
    def _kick_ok(d):
        return bool(d.get("video_offset_h1_confirmed")) and bool(d.get("video_offset_h2_confirmed"))
    assert not _kick_ok({})
    assert not _kick_ok({"video_offset_h1_confirmed": True})
    assert _kick_ok({"video_offset_h1_confirmed": True, "video_offset_h2_confirmed": True})


def test_gamedoc_confirm_fields_default_false():
    # the confirm fields exist on GameDoc and default to False (an unconfirmed
    # game blocks) — checked via the dataclass field defaults, not instantiation.
    from post_game.firestore_io import GameDoc
    import dataclasses
    defaults = {f.name: f.default for f in dataclasses.fields(GameDoc)}
    assert defaults.get("video_offset_h1_confirmed") is False
    assert defaults.get("video_offset_h2_confirmed") is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} kickoff-gate tests passed.")

"""Keep the voice extractor's vocabulary usable and useful.

Three lists have to agree or the feature quietly fails:

  * `voice_extract.EVENT_TYPES` — what the LLM may emit
  * `EVENT_TYPES` in soccer_team_app.jsx — what the confirm queue can ACCEPT
  * `pwa_score.PILLAR_EVENT_TYPES` — the discretionary events the season score
    coverage-weights, i.e. the ones live tapping actually loses

A type the extractor emits but the app lacks becomes a draft the coach can never
accept — worse than no draft, because it sits in the queue forever. That already
happened with CORNER, OFFSIDE and SUB.

And a vocabulary made only of outcome events (goals, shots, saves) adds volume
while fixing nothing: those are the events the coach already taps reliably. The
DEF and DEC pillars run on process events, which are absent from 8 of 12 games.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tracking.pwa_score import PILLAR_EVENT_TYPES
from tracking.voice_extract import EVENT_TYPES as VOICE_TYPES
from tracking.voice_extract import _SCHEMA

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"
GUIDE = REPO / "POST_GAME_NARRATION_GUIDE.md"


def _app_event_types() -> set[str]:
    src = JSX.read_text()
    start = src.index("const EVENT_TYPES = {")
    depth = 0
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return set(re.findall(r"^\s{2}([A-Z_]+):", src[start:end], re.M))


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_every_extractable_type_can_be_accepted_in_the_app():
    """A draft whose type the app lacks is un-actionable forever."""
    unknown = sorted(set(VOICE_TYPES) - _app_event_types())
    assert not unknown, (
        f"voice_extract emits {unknown}, which soccer_team_app.jsx cannot accept — "
        "the confirm queue will silently drop the Accept")


def test_the_schema_enum_matches_the_vocabulary():
    """The LLM is constrained by the schema, not by the constant."""
    enum = _SCHEMA["properties"]["events"]["items"]["properties"]["type"]["enum"]
    assert enum == VOICE_TYPES


def test_the_vocabulary_covers_the_events_live_tapping_loses():
    """The point of extending it: recover the DEF/DEC pillar inputs.

    GIVE_GO is the one deliberate omission — it needs a partner player, which
    narration rarely states.
    """
    discretionary = set().union(*PILLAR_EVENT_TYPES.values())
    missing = sorted(discretionary - set(VOICE_TYPES) - {"GIVE_GO"})
    assert not missing, (
        f"these coverage-weighted events are not extractable from voice: {missing} — "
        "without them, narrating cannot repair the DEF/DEC pillars")


def test_the_vocabulary_is_not_only_outcome_events():
    """The original list was outcome-only, so voice added volume but no signal."""
    outcome = {"GOAL", "ASSIST", "SHOT_ON", "SHOT_OFF", "SAVE",
               "PEN_AWARDED", "OPP_GOAL"}
    process = set(VOICE_TYPES) - outcome
    assert len(process) >= 10, f"only {len(process)} process types: {sorted(process)}"


def test_retired_types_do_not_come_back():
    """CORNER/OFFSIDE never existed in the app; SUB needs two players."""
    for t in ("CORNER", "OFFSIDE", "SUB"):
        assert t not in VOICE_TYPES


@pytest.mark.skipif(not JSX.exists(), reason="JSX source not present")
def test_the_prompt_teaches_the_process_phrasings():
    """The model will not emit a type it was never shown language for.

    Scanning the coach's three LIVE narrations found almost only "won the ball"
    (BALL_WIN 11/6) and nothing else — so the mapping has to be explicit, and the
    narration guide has to exist for the coach to know what to say.
    """
    src = Path(REPO / "tracking" / "voice_extract.py").read_text()
    prompt = src[src.index("def _extract("):]
    for t in ("DUEL_WIN", "DUEL_LOSE", "TURNOVER", "HOLDS_BALL", "GATES",
              "KEY_PASS", "BLOCK", "CLEAR", "KICK_OUT"):
        assert f"-> {t}" in prompt or f"→ {t}" in prompt, (
            f"the prompt gives no phrasing that maps to {t}")


def test_the_narration_guide_exists_and_covers_each_process_type():
    assert GUIDE.exists(), "POST_GAME_NARRATION_GUIDE.md is missing"
    text = GUIDE.read_text()
    for t in ("BALL_WIN", "DUEL_WIN", "DUEL_LOSE", "BLOCK", "CLEAR", "KICK_OUT",
              "KEY_PASS", "GATES", "TURNOVER", "HOLDS_BALL"):
        assert t in text, f"the guide never tells the coach how to produce {t}"


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

"""The goal roar must not arrive before the ball crosses.

The coach: "the cheers are happening before the score." The roar was deliberately
started PUBLIC_ROAR_LEAD_S = 7 s early, with a 2.5 s fade-in, as compensation for not
knowing when the goal really happened — the tap was assumed to trail the goal, and a
slow swell was meant to hide the uncertainty.

Once events carried exact source-video times that compensation became pure error, and
no improvement in timestamps could fix it: a 7 s pre-roll is 7 s early by
construction. These pin the corrected placement.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from post_game import config


def test_the_roar_does_not_start_before_the_goal():
    """`lead_s` shifts the roar EARLIER, so a positive value is a pre-roll."""
    assert config.PUBLIC_ROAR_LEAD_S <= 0.0, (
        f"roar starts {config.PUBLIC_ROAR_LEAD_S}s BEFORE the goal")


def test_the_roar_starts_close_to_the_goal_not_long_after():
    """Overcorrecting is its own bug — a cheer 5 s late reads as a mistake too."""
    assert -3.0 <= config.PUBLIC_ROAR_LEAD_S <= 0.0


def test_the_fade_is_short_enough_to_read_as_a_reaction():
    """A long build was the other half of the slop-hiding: it made the early onset
    more audible, not less."""
    assert config.PUBLIC_ROAR_FADE_S <= 1.0


def test_the_delay_expression_shifts_later_for_a_negative_lead():
    """Guards the SIGN CONVENTION, which is the easy thing to get backwards.

    The filtergraph uses `rt - lead_s`, so a negative lead must move the roar later.
    """
    from post_game import public_audio
    src = inspect_source(public_audio)
    assert "rt - lead_s" in src, "delay expression changed — re-check the sign"
    rt, lead = 100.0, config.PUBLIC_ROAR_LEAD_S
    assert (rt - lead) >= rt, "negative lead must delay, not advance, the roar"


def inspect_source(mod) -> str:
    import inspect
    return inspect.getsource(mod)


def test_the_roar_is_audible_above_the_bed():
    """If the fix ever became "just make the cheer quiet", that is not a fix.

    ⚠ The two dB knobs are NOT directly comparable: they are gains applied to assets
    of very different loudness. Measured with ffmpeg volumedetect, goal_roar.mp3 has
    a mean of -11.3 dB against stadium_ambience.mp3's -25.3 dB, so the roar carries
    14 dB more before either gain. Effective level is asset + gain — which is why
    roar_db (-13) being numerically BELOW bed_db (-8) still leaves the roar ~9 dB
    above the bed. Comparing the raw constants is the trap.
    """
    ROAR_ASSET_MEAN_DB = -11.3
    BED_ASSET_MEAN_DB = -25.3
    roar = ROAR_ASSET_MEAN_DB + config.PUBLIC_ROAR_DB
    bed = BED_ASSET_MEAN_DB + config.PUBLIC_BED_DB
    assert roar > bed + 3.0, (
        f"roar {roar:.1f}dB is not clearly above the bed {bed:.1f}dB")


def test_the_old_seven_second_preroll_is_gone():
    """Named explicitly so a revert to the historical value fails loudly."""
    assert abs(config.PUBLIC_ROAR_LEAD_S - 7.0) > 1e-6


def test_the_comment_records_why_the_preroll_was_removed():
    """Without the reason, the next person 'fixes' a late-sounding cheer by
    reinstating the pre-roll."""
    src = Path(__file__).resolve().parents[1] / "post_game" / "config.py"
    text = src.read_text()
    i = text.index("PUBLIC_ROAR_LEAD_S")
    context = text[max(0, i - 1200):i]
    assert re.search(r"7\s*s", context), "the old 7s value is not documented"


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

"""The parent-facing reel must never carry the dugout audio.

What shipped: `PUBLIC_AUDIO_ENABLED` defaulted OFF (it required the env var to be
literally "1", which was in nobody's .env), so the ambience swap never ran. The
publisher then fell back to `public_tv_url or tv_reel_meta.r2_url` — meaning the
PUBLIC field pointed at the original-audio cut. Every parent-facing full game and
highlight reel went out with the coach's voice and the children's names on it.

Two independent things had to both be wrong for that to happen, so both are pinned
here: the control defaults ON, and its failure mode is to publish NOTHING.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_the_audio_swap_defaults_on():
    """A privacy control that is off by default is not a control."""
    import importlib
    import os

    from post_game import config
    saved = os.environ.pop("PUBLIC_AUDIO_ENABLED", None)
    try:
        importlib.reload(config)
        assert config.PUBLIC_AUDIO_ENABLED is True
    finally:
        if saved is not None:
            os.environ["PUBLIC_AUDIO_ENABLED"] = saved
        importlib.reload(config)


def test_it_can_still_be_turned_off_deliberately():
    import importlib
    import os

    from post_game import config
    saved = os.environ.get("PUBLIC_AUDIO_ENABLED")
    os.environ["PUBLIC_AUDIO_ENABLED"] = "0"
    try:
        importlib.reload(config)
        assert config.PUBLIC_AUDIO_ENABLED is False
    finally:
        if saved is None:
            os.environ.pop("PUBLIC_AUDIO_ENABLED", None)
        else:
            os.environ["PUBLIC_AUDIO_ENABLED"] = saved
        importlib.reload(config)


def _publish_block(strip_comments: bool = True) -> str:
    """The source of the public-field publishing block.

    Comments are stripped by default: the block deliberately QUOTES the leaking
    expression to explain why it must not return, and a scan that counted that
    would fail on its own documentation.
    """
    from post_game import pipeline
    src = inspect.getsource(pipeline)
    i = src.index("public_fields: dict = {}")
    block = src[i:i + 2600]
    if strip_comments:
        block = "\n".join(re.sub(r"#.*$", "", ln) for ln in block.splitlines())
    return block


def test_the_public_url_never_falls_back_to_the_original_reel():
    """The exact expression that leaked: `public_tv_url or tv_reel_meta.r2_url`."""
    block = _publish_block()
    for bad in (r"public_tv_url\s+or\s+tv_reel_meta",
                r"public_hl_url\s+or\s+auto_hl_meta"):
        assert not re.search(bad, block), (
            f"public field falls back to the dugout reel ({bad})")


def test_the_public_fields_are_only_set_from_the_public_urls():
    block = _publish_block()
    for field, src_var in (("videoFullGameUrl", "public_tv_url"),
                           ("videoHighlightsUrl", "public_hl_url")):
        m = re.search(rf'public_fields\["{field}"\]\s*=\s*([^\n]+)', block)
        assert m, f"{field} is no longer assigned — did the block move?"
        assert m.group(1).strip() == src_var, (
            f"{field} is assigned from {m.group(1).strip()}, not {src_var}")


def test_a_missing_public_reel_is_logged_as_a_privacy_event():
    """Silent omission would be its own trap: the coach must be able to see why
    the parent-facing video vanished."""
    block = _publish_block(strip_comments=False)
    assert block.count("PRIVACY") >= 2


def test_the_assets_the_swap_needs_actually_exist():
    """The swap returns None when an asset is missing, which — before the fallback
    was removed — degraded straight into publishing the original audio."""
    from post_game import config
    root = Path(__file__).resolve().parents[1]
    for p in (config.PUBLIC_AMBIENCE_PATH, config.PUBLIC_ROAR_PATH):
        assert (root / p).exists(), f"missing public-audio asset: {p}"


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

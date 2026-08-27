"""Tests for the opt-in reel SR pass (tv_sr)."""
from __future__ import annotations

import numpy as np
import pytest

from post_game import config, tv_sr


def _frame() -> np.ndarray:
    rng = np.random.default_rng(7)
    img = (rng.random((216, 384, 3)) * 255).astype(np.uint8)
    img[100:120, :, :] = 255  # a hard edge so sharpness is measurable
    return img


def test_disabled_is_identity(monkeypatch):
    monkeypatch.setattr(config, "TV_SR", False)
    f = _frame()
    assert tv_sr.enhance(f) is f


def test_missing_weights_is_inert(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TV_SR", True)
    monkeypatch.setattr(tv_sr, "_WEIGHTS", tmp_path / "nope.pth")
    monkeypatch.setattr(tv_sr, "_state", {"model": None, "dev": None, "failed": False})
    f = _frame()
    assert tv_sr.enhance(f) is f
    assert tv_sr._state["failed"]


@pytest.mark.skipif(not tv_sr._WEIGHTS.exists(), reason="SR weights not present")
def test_enabled_preserves_shape_and_sharpens(monkeypatch):
    import cv2

    monkeypatch.setattr(config, "TV_SR", True)
    monkeypatch.setattr(config, "TV_SR_BLEND", 1.0)
    monkeypatch.setattr(tv_sr, "_state", {"model": None, "dev": None, "failed": False})
    f = _frame()
    out = tv_sr.enhance(f)
    assert out.shape == f.shape and out.dtype == f.dtype
    assert not np.array_equal(out, f)
    # blend=0 must return (numerically) the input frame
    monkeypatch.setattr(config, "TV_SR_BLEND", 0.0)
    out0 = tv_sr.enhance(f)
    assert np.array_equal(out0, cv2.addWeighted(out0, 0, f, 1.0, 0))

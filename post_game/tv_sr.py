"""Neural super-resolution for rendered reel frames (opt-in, see config.TV_SR).

Runs realesr-general-x4v3 (SRVGGNetCompact, plain-PyTorch port — no basicsr
dependency) on each rendered 1080p frame: x4 upscale, Lanczos back down to the
frame size, optional alpha-blend with the input. Net effect is synthesized
detail where Lanczos upscaling left mush.

Model choice is coach-gated: RealESRGAN_x2plus was REJECTED (2026-08-27,
"players and spectators ... look like cartoons"); general-v3 passed the stills
review as photographic. Do not swap models without a new coach A/B.

Follows the ball-pass convention: if torch or the weight files are missing the
stage is inert (enhance() returns its input) and warns once, so a machine
without the weights still renders reels.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from post_game import config

log = logging.getLogger(__name__)

_MODELS_DIR = Path(__file__).parent / "models"
_WEIGHTS = _MODELS_DIR / "realesr-general-x4v3.pth"
_WEIGHTS_WDN = _MODELS_DIR / "realesr-general-wdn-x4v3.pth"

_TILE = 640  # MPS-friendly tile size; PAD absorbs conv edge effects
_PAD = 8
_UPSCALE = 4

_state: dict = {"model": None, "dev": None, "failed": False}


def _build_model():
    import torch
    import torch.nn as nn

    class SRVGGNetCompact(nn.Module):
        def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32):
            super().__init__()
            self.body = nn.ModuleList()
            self.body.append(nn.Conv2d(num_in_ch, num_feat, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=num_feat))
            for _ in range(num_conv):
                self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
                self.body.append(nn.PReLU(num_parameters=num_feat))
            self.body.append(nn.Conv2d(num_feat, num_out_ch * _UPSCALE * _UPSCALE, 3, 1, 1))
            self.upsampler = nn.PixelShuffle(_UPSCALE)

        def forward(self, x):
            import torch.nn.functional as F

            out = x
            for m in self.body:
                out = m(out)
            out = self.upsampler(out)
            return out + F.interpolate(x, scale_factor=_UPSCALE, mode="nearest")

    dn = float(np.clip(config.TV_SR_DN, 0.0, 1.0))
    a = torch.load(_WEIGHTS, map_location="cpu", weights_only=True)["params"]
    if dn < 1.0:
        b = torch.load(_WEIGHTS_WDN, map_location="cpu", weights_only=True)["params"]
        a = {k: dn * a[k] + (1.0 - dn) * b[k] for k in a}
    model = SRVGGNetCompact()
    model.load_state_dict(a, strict=True)
    if torch.backends.mps.is_available():
        dev = torch.device("mps")
    elif torch.cuda.is_available():
        dev = torch.device("cuda")
    else:
        dev = torch.device("cpu")
    model.eval().to(dev)
    if dev.type in ("mps", "cuda"):
        model.half()
    return model, dev


def _get_model():
    if _state["failed"]:
        return None, None
    if _state["model"] is None:
        try:
            if not _WEIGHTS.exists() or (config.TV_SR_DN < 1.0 and not _WEIGHTS_WDN.exists()):
                raise FileNotFoundError(f"SR weights missing under {_MODELS_DIR}")
            _state["model"], _state["dev"] = _build_model()
            log.info("tv_sr: model on %s (dn=%.2f blend=%.2f)",
                     _state["dev"], config.TV_SR_DN, config.TV_SR_BLEND)
        except Exception:
            _state["failed"] = True
            log.warning("tv_sr: disabled — model unavailable", exc_info=True)
            return None, None
    return _state["model"], _state["dev"]


def enhance(bgr: np.ndarray) -> np.ndarray:
    """SR-enhance one rendered frame; identity when disabled or unavailable."""
    if not config.TV_SR:
        return bgr
    model, dev = _get_model()
    if model is None:
        return bgr

    import torch

    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    out = np.zeros((h * _UPSCALE, w * _UPSCALE, 3), np.float32)
    for y0 in range(0, h, _TILE):
        for x0 in range(0, w, _TILE):
            y1, x1 = min(y0 + _TILE, h), min(x0 + _TILE, w)
            yp0, xp0 = max(0, y0 - _PAD), max(0, x0 - _PAD)
            yp1, xp1 = min(h, y1 + _PAD), min(w, x1 + _PAD)
            t = torch.from_numpy(rgb[yp0:yp1, xp0:xp1].transpose(2, 0, 1))[None].to(dev)
            if dev.type in ("mps", "cuda"):
                t = t.half()
            with torch.no_grad():
                o = model(t).float().clamp_(0, 1)[0].cpu().numpy().transpose(1, 2, 0)
            oy0, ox0 = (y0 - yp0) * _UPSCALE, (x0 - xp0) * _UPSCALE
            out[y0 * _UPSCALE:y1 * _UPSCALE, x0 * _UPSCALE:x1 * _UPSCALE] = \
                o[oy0:oy0 + (y1 - y0) * _UPSCALE, ox0:ox0 + (x1 - x0) * _UPSCALE]

    big = (out * 255.0).round().astype(np.uint8)
    sr = cv2.resize(cv2.cvtColor(big, cv2.COLOR_RGB2BGR), (w, h),
                    interpolation=cv2.INTER_LANCZOS4)
    if config.TV_SR_CHROMA == "orig":
        # Sharpness lives in luma; the model's chroma slightly shifts
        # saturation (coach-flagged 2026-08-27). Keep the original color plane.
        sr_y = cv2.cvtColor(sr, cv2.COLOR_BGR2YCrCb)
        orig_y = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        orig_y[:, :, 0] = sr_y[:, :, 0]
        sr = cv2.cvtColor(orig_y, cv2.COLOR_YCrCb2BGR)
    alpha = float(np.clip(config.TV_SR_BLEND, 0.0, 1.0))
    if alpha >= 1.0:
        return sr
    return cv2.addWeighted(sr, alpha, bgr, 1.0 - alpha, 0)

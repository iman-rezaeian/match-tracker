"""Coarse color-family bucketing for team identity — "green means green".

The coach picks a rough swatch at kickoff (our kit + opponent kit). Those picks
are approximate, so we must NOT do fine nearest-hue-within-N-degrees matching
against the exact hex. Instead bucket every color — the coach's picks AND the
jersey pixels — into a small set of coarse FAMILIES (green / blue / red / ...)
and compare at the family level. The coach's two picks tell us which family is
OURS vs the OPPONENT's; a jersey is "ours" iff its family == the ours family.

Pure (OpenCV HSV math only), no I/O, unit-tested. Used by:
  * vlm_identity — (the strong signal is the VLM's own read; this is the pixel
    fallback / prune helper)
  * pipeline._build_tracklet_index — prune a review-list tracklet only when its
    CONFIDENT family is the opponent's (washed/unsure → None → leave it in).

NOTE the pixel path is deliberately conservative: U10 jerseys on this footage
desaturate badly (a third of even our players' median pixel reads near-black),
so tracklet_family returns a family ONLY when enough saturated pixels agree —
otherwise None ("unknown", don't act on it).
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

# Coarse families keyed by OpenCV hue (0-179), gated first on value/saturation
# for the achromatic families. Boundaries are broad on purpose — this is
# category-level ("blue-ish"), not a fine hue match.
_BLACK_V = 45.0        # below this value → black regardless of hue
_GRAY_S = 45.0         # below this saturation (and not black) → white/gray
_WHITE_V = 160.0       # gray vs white split by value


def color_family(hsv=None, *, hex: Optional[str] = None) -> str:
    """Coarse family name for a color given as an OpenCV HSV triple (h 0-179,
    s/v 0-255) or a '#rrggbb' hex. One of:
    black, gray, white, red, orange, yellow, green, cyan, blue, purple, pink."""
    if hex is not None:
        s = (hex or "").lstrip("#")
        if len(s) != 6:
            return "gray"
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        hsv = cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2HSV)[0, 0]
    h, s, v = float(hsv[0]), float(hsv[1]), float(hsv[2])
    if v < _BLACK_V:
        return "black"
    if s < _GRAY_S:
        return "white" if v >= _WHITE_V else "gray"
    # chromatic — coarse hue bands (OpenCV 0-179)
    if h < 10 or h >= 170:
        return "red"
    if h < 23:
        return "orange"
    if h < 34:
        return "yellow"
    if h < 85:
        return "green"
    if h < 96:
        return "cyan"
    if h < 130:
        return "blue"
    if h < 155:
        return "purple"
    return "pink"


# Families that mean "no usable team color" — an achromatic jersey (dark/white/
# gray) can't be assigned to a colored kit, so it's never a confident opponent.
_ACHROMATIC = frozenset({"black", "gray", "white"})


def tracklet_family(
    jersey_samples: list,
    *,
    min_sat: float = 60.0,
    min_pixels: int = 40,
    dominance: float = 0.5,
) -> Optional[str]:
    """Coarse family of a tracklet's jersey, or None when not confident.

    `jersey_samples`: list of per-detection (N,3) HSV pixel arrays (as stored in
    jersey_samples.npz). Pool the CHROMATIC pixels (s >= min_sat) across the
    tracklet, bucket each into a family, and return the plurality family only if
    it holds >= `dominance` of the chromatic pixels and there are >= `min_pixels`
    of them. Otherwise None ("washed / unsure — don't act"). Achromatic families
    (black/gray/white) are never returned as a confident color (a dark kit tells
    us nothing about which colored team it is)."""
    chrom = []  # collected chromatic pixels
    for s in jersey_samples or []:
        a = np.asarray(s, dtype=np.float32)
        if a.ndim != 2 or a.shape[1] != 3 or a.size == 0:
            continue
        chrom.append(a[a[:, 1] >= min_sat])
    if not chrom:
        return None
    px = np.vstack(chrom)
    if len(px) < min_pixels:
        return None
    fams = [color_family(p) for p in px]
    from collections import Counter
    counts = Counter(f for f in fams if f not in _ACHROMATIC)
    if not counts:
        return None
    fam, cnt = counts.most_common(1)[0]
    if cnt / max(1, len(px)) < dominance:
        return None
    return fam

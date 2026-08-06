"""Decide which of the two kits a detection is wearing, per frame.

This exists because the team owns exactly two kits and they need two different
discriminators:

  * **green `#16a34a`** — H71, S221. Chromatic: hue separates it cleanly from a
    blue or red opponent.
  * **black `#0a0a0a`** — S0, V10. Achromatic: it has NO hue. Neither does the
    white (`#f5f5f4`, S1) and light-grey (`#d4d4d4`, S0) the opponents wore in
    those games. Hue is pure noise here; **brightness** is the whole signal.

Picking one axis for both is what has bitten this pipeline twice already.
`team_classifier.sample_jersey_hsv` drops the grass band to protect small ROIs,
which deletes our GREEN kit (H71 sits inside 35–85) while leaving a blue
opponent intact — measured, that splits the teams 3.9:1 where 7v7 needs ~1:1.
Its own comment records the mirror-image bug: dropping low-saturation pixels
"erased WHITE and BLACK kits ... the bug that pushed white opponents onto our
(green) team". Two kits, two failure modes, same root cause — a fixed rule
applied to a colour space one of the kits doesn't live in.

So the axis is chosen from the kit anchors themselves: if both are chromatic,
vote on hue; otherwise vote on value. Either way the decision is *relative*
(which anchor is nearer), never a fixed threshold, and it abstains inside a
neutral margin rather than guessing.
"""

from __future__ import annotations

import numpy as np

try:                                  # cv2 is present in the pipeline venv
    import cv2
except ImportError:                   # pragma: no cover - tests stub the ROI
    cv2 = None

# Below this saturation a pixel's hue is noise, so a kit at/below it must be
# discriminated by brightness instead. Black S0 and white S1 are far below;
# green S221 and blue S215 are far above.
CHROMATIC_S = 40.0

OURS, OPP, UNKNOWN = 1, -1, 0


def hex_to_hsv(hex_color: str) -> tuple[float, float, float]:
    """#rrggbb -> OpenCV HSV (H 0-179, S 0-255, V 0-255)."""
    s = (hex_color or "").lstrip("#")
    if len(s) != 6:
        return (0.0, 0.0, 0.0)
    rgb = np.uint8([[[int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16)]]])
    if cv2 is None:                   # pragma: no cover
        return (0.0, 0.0, 0.0)
    h, sat, v = cv2.cvtColor(rgb, cv2.COLOR_BGR2HSV)[0, 0]
    return (float(h), float(sat), float(v))


def circ_dist(a: float, b: float) -> float:
    """Distance between two OpenCV hues on the 0-179 circle."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def pick_axis(our_hex: str, opp_hex: str) -> str:
    """'hue' when both kits carry usable colour, else 'value'.

    One achromatic kit is enough to disqualify hue: black-vs-blue has no
    meaningful hue distance for the black side, and comparing its noise hue to
    a real anchor would produce confident nonsense.
    """
    _, s_our, _ = hex_to_hsv(our_hex)
    _, s_opp, _ = hex_to_hsv(opp_hex)
    return "hue" if (s_our >= CHROMATIC_S and s_opp >= CHROMATIC_S) else "value"


def torso_roi(frame, bbox, min_h: int = 14):
    """Central upper-torso slice of a bbox — where the jersey dominates.

    Same geometry the production sampler uses: below the head, above the
    shorts, inset from the arms, so grass and skin are minimal.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    h_box, w_box = y2 - y1, x2 - x1
    if h_box < min_h or w_box < 4:
        return None
    jy1, jy2 = y1 + int(0.18 * h_box), y1 + int(0.50 * h_box)
    jx1, jx2 = x1 + int(0.28 * w_box), x2 - int(0.28 * w_box)
    jy1, jx1 = max(0, jy1), max(0, jx1)
    jy2, jx2 = min(frame.shape[0], jy2), min(frame.shape[1], jx2)
    if jx2 <= jx1 or jy2 <= jy1:
        return None
    return frame[jy1:jy2, jx1:jx2]


def vote_hue(hsv: np.ndarray, our_h: float, opp_h: float,
             min_s: float, min_px: int, margin: float) -> int:
    """Nearest kit HUE. No grass drop — that is what deletes the green kit."""
    sel = hsv[hsv[:, 1] >= min_s]
    if len(sel) < min_px:
        return UNKNOWN
    hue = float(np.median(sel[:, 0]))
    d_our, d_opp = circ_dist(hue, our_h), circ_dist(hue, opp_h)
    if d_opp - d_our >= margin:
        return OURS
    if d_our - d_opp >= margin:
        return OPP
    return UNKNOWN


def vote_value(hsv: np.ndarray, our_v: float, opp_v: float,
               min_px: int, margin: float) -> int:
    """Nearest kit BRIGHTNESS — the black-vs-white/grey case.

    Every pixel counts here: filtering on saturation would discard the whole
    ROI, since an achromatic kit is by definition unsaturated.
    """
    if len(hsv) < min_px:
        return UNKNOWN
    val = float(np.median(hsv[:, 2]))
    d_our, d_opp = abs(val - our_v), abs(val - opp_v)
    if d_opp - d_our >= margin:
        return OURS
    if d_our - d_opp >= margin:
        return OPP
    return UNKNOWN


def vote_detection(frame, bbox, our_hex: str, opp_hex: str, *,
                   axis: str | None = None,
                   min_s: float = 35.0, min_px: int = 10,
                   hue_margin: float = 6.0,
                   value_margin: float = 12.0) -> int:
    """+1 ours, -1 opponent, 0 unknown, for one detection's jersey ROI."""
    roi = torso_roi(frame, bbox)
    if roi is None or roi.size == 0:
        return UNKNOWN
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    axis = axis or pick_axis(our_hex, opp_hex)
    h_our, s_our, v_our = hex_to_hsv(our_hex)
    h_opp, s_opp, v_opp = hex_to_hsv(opp_hex)
    if axis == "hue":
        return vote_hue(hsv, h_our, h_opp, min_s, min_px, hue_margin)
    return vote_value(hsv, v_our, v_opp, min_px, value_margin)

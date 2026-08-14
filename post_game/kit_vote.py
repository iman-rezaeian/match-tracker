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
opponent intact — measured, that splits the teams 3.9:1 where both sides field
the same count and must come out ~1:1.
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

from typing import Optional

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

    `our_v`/`opp_v` should come from `fit_value_anchors` where the footage
    supports it — a kit hex is the colour of the FABRIC, not of the fabric in
    sunlight, and on the value axis that difference is the whole decision. See
    that function for the measurement.
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


def _otsu(x: np.ndarray, bins: int = 256) -> float:
    """Threshold maximising between-class variance (classic 1-D Otsu)."""
    hist, edges = np.histogram(x, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2.0
    w = hist.astype(float)
    if w.sum() <= 0:
        return float(np.median(x))
    w0 = np.cumsum(w)
    w1 = w.sum() - w0
    m0 = np.cumsum(w * centres)
    with np.errstate(invalid="ignore", divide="ignore"):
        between = w0 * w1 * (m0 / w0 - (m0[-1] - m0) / w1) ** 2
    return float(centres[int(np.argmax(np.nan_to_num(between, nan=-1.0)))])


def bimodality(x: np.ndarray, thresh: float) -> float:
    """How much of the spread the split explains: between-class variance ratio.

    Returns eta-squared — between-group variance over total variance, in [0, 1].

    The obvious metric (standardised mean gap between the two groups) does NOT
    work here, and the reason is worth recording: Otsu returns the best split of
    ANY distribution, including one blob. Bisecting a single Gaussian yields two
    half-normals whose means differ by ~2.5 pooled SDs *of the halves*, because
    halving shrinks each side's SD as much as it separates the means. Measured:
    N(120,15) scored 2.54 by that metric versus 8.55 for a genuinely bimodal
    sample — the same order, so no threshold could separate them.

    Comparing against the UNDIVIDED spread has no such blind spot: a real split
    explains most of the total variance (eta^2 -> 1), while bisecting one blob
    explains only the part its own halves account for (~0.6 for a Gaussian).
    """
    lo, hi = x[x < thresh], x[x >= thresh]
    if len(lo) < 2 or len(hi) < 2:
        return 0.0
    total = x.var()
    if total <= 1e-9:
        return 0.0
    grand = x.mean()
    between = (len(lo) * (lo.mean() - grand) ** 2
               + len(hi) * (hi.mean() - grand) ** 2) / len(x)
    return float(between / total)


def fit_value_anchors(track_values, our_hex: str, opp_hex: str,
                      min_tracks: int = 50, min_separation: float = 0.70
                      ) -> tuple[Optional[float], Optional[float], str]:
    """Kit VALUE anchors taken from the footage, not from the kit hexes.

    A hex is the colour of the fabric in a swatch. Under a July sun a black
    `#0a0a0a` shirt photographs around V150-200, nowhere near its nominal V10 —
    so the hex-midpoint boundary sits in the wrong place and, measured on
    mrhvbvwi1gjpn, the "ours" anchor fell BELOW the entire observed range
    (p1-p99 = 28-250). Every one of our players was therefore nearer the
    opponent anchor: the split came out 948/2217 where the sides must be ~1:1, and 54%
    of the "opponent" tracks were achromatic — called opponent purely for being
    bright. Fitting the threshold to the data instead moves that to 0.91:1.

    Hue does not need this (it is roughly illumination-invariant), which is why
    the hue-axis game was unaffected; this is a value-axis correction only.

    The hexes are still used, but only for POLARITY — which side of the split is
    ours. That is a fact about the kits that lighting cannot invert: if our kit
    is the darker of the two, our players are the darker cluster.

    Returns ``(our_v, opp_v, note)``. Both None when the data cannot support
    anchors, in which case the caller keeps the hex values: a unimodal
    distribution (one team barely present, flat light) would otherwise be split
    down the middle and half of one team confidently mislabelled. Abstaining
    costs a game that was already going to be wrong; guessing corrupts one that
    would have been right.

    `min_separation` is eta^2 (see `bimodality`). Synthetic samples: one Gaussian
    scores 0.62-0.66 however wide, two overlapping clusters 0.65, two clean
    clusters 0.84-0.95. But REAL footage sits lower than clean synthetic —
    mrhvbvwi1gjpn 0.762, mri01pvelv46d 0.755 — because refs, spectators and
    unclassifiable tracks widen the distribution around the two kit modes. 0.70
    sits in the empty band between the highest unimodal case (0.66) and the
    lowest real game (0.755), with headroom on both sides; at 0.75 a valid game
    cleared the gate by 0.005 and any extra noise would have silently dropped it
    back onto the hex anchors — the exact failure this function exists to fix,
    reintroduced without an error. Two real clusters closer than ~90 V still
    abstain, which is correct: kits that similar cannot be told apart by
    brightness.
    """
    x = np.asarray([v for v in track_values if v is not None], dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < min_tracks:
        return None, None, f"too few tracks ({len(x)} < {min_tracks})"

    thresh = _otsu(x)
    sep = bimodality(x, thresh)
    if sep < min_separation:
        return None, None, f"not bimodal (eta^2 {sep:.2f} < {min_separation})"

    lo, hi = x[x < thresh], x[x >= thresh]
    dark_v, bright_v = float(np.median(lo)), float(np.median(hi))
    _, _, v_our = hex_to_hsv(our_hex)
    _, _, v_opp = hex_to_hsv(opp_hex)
    ours_darker = v_our < v_opp
    our_v, opp_v = (dark_v, bright_v) if ours_darker else (bright_v, dark_v)
    return our_v, opp_v, (f"fitted V ours={our_v:.0f} opp={opp_v:.0f} "
                          f"(threshold {thresh:.0f}, separation {sep:.1f}sd, "
                          f"{len(x)} tracks)")


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

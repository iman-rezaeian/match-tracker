"""KMeans on jersey-region color -> team_id in {0, 1}.

Per track_id, sample HSV pixels from the upper-half of bbox crops across many
frames, then 2-cluster all track median colors. The cluster center closer to
the team's `homeColor` becomes team 0 ("us").

Color space: plain OpenCV HSV with Euclidean distance — the production-
validated geometry — PLUS a minimal fix for the hue wrap (hue is circular,
0-179: red sits at BOTH ends, so raw hue medians/distances split red kits):

  1. per-track medians center each track's hue distribution before taking
     the median (circular-safe; identical result when hues don't straddle
     the wrap);
  2. one per-game hue ROTATION puts the saturation-weighted bulk of all
     jersey colors at h=90, so no kit straddles the wrap during clustering /
     anchor matching. A rotation is an isometry of the hue circle: when
     nothing wraps (all current footage), every pairwise distance — and so
     every classification — is unchanged.

Do NOT "upgrade" this to Lab or to a chroma-scaled cylinder: both were tried
on real footage (2026-06-10) and failed hard — fixed hex anchors sit far
from the desaturated, grass-tinted colors kits actually have on video, so
absolute-space changes reshuffle the team split wildly (Lab: 33%→78% of
tracks "ours"; cyl nearest-anchor: →30 fragments). The coach-override labels
can't validate classifier changes either (they only exist for tracklets the
OLD partition put in team 0). Empirical-cluster + relative anchor matching
is the only direction that survives the anchor-vs-video gap; revisit at 8K.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _hex_to_hsv(hex_color: str) -> np.ndarray:
    s = (hex_color or "").lstrip("#")
    if len(s) != 6:
        return np.array([0, 0, 0], dtype=np.float32)
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    bgr = np.uint8([[[b, g, r]]])
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0, 0]
    return hsv.astype(np.float32)


def _circ_mean_hue(h: np.ndarray, w: np.ndarray) -> float:
    """Saturation-weighted circular mean of OpenCV hues (0-180 wrap)."""
    ang = h * (np.pi / 90.0)
    c = float((np.cos(ang) * w).sum())
    s = float((np.sin(ang) * w).sum())
    if abs(c) < 1e-9 and abs(s) < 1e-9:
        return 90.0
    return float(np.arctan2(s, c) * (90.0 / np.pi)) % 180.0


# Wrap-risk gate: the circular-hue machinery engages ONLY when saturated
# color mass sits near BOTH ends of the hue axis (i.e., a red kit straddles
# the 0/180 wrap). Any rotation moves whichever hues cross the cut by ±180
# in the LINEAR space KMeans/Euclidean operate in, which reshuffles the
# partition — so on wrap-free footage (all current games) we must be
# byte-identical to the production classifier and do nothing.
_WRAP_ZONE = 20.0       # "near the wrap" = hue within this of 0 or 180
_WRAP_MIN_FRAC = 0.05   # both ends need ≥ this fraction of chromatic weight
_CHROMATIC_S = 60.0     # pixels/tracks below this saturation carry hue noise


def _has_wrap_risk(h: np.ndarray, s: np.ndarray) -> bool:
    chroma = s >= _CHROMATIC_S
    if not chroma.any():
        return False
    hc = h[chroma]
    w = s[chroma]
    tot = float(w.sum())
    lo = float(w[hc < _WRAP_ZONE].sum())
    hi = float(w[hc > 180.0 - _WRAP_ZONE].sum())
    return tot > 0 and lo / tot >= _WRAP_MIN_FRAC and hi / tot >= _WRAP_MIN_FRAC


def _median_hsv(stacked: np.ndarray) -> np.ndarray:
    """Per-track median HSV; circular-safe hue median ONLY under wrap risk.

    A red track samples hues at both ~0 and ~178; a plain median lands mid-
    axis (≈ green). When that risk is detected, rotate the track's hues so
    their circular mean sits at 90, median there, rotate back. All other
    tracks take the plain median — bit-identical to production."""
    # npz checkpoints loaded with allow_pickle yield OBJECT-dtype stacks;
    # ufuncs like np.cos choke on those — force float32 first.
    stacked = np.asarray(stacked, dtype=np.float32)
    h, s = stacked[:, 0], stacked[:, 1]
    if not _has_wrap_risk(h, s):
        return np.median(stacked, axis=0).astype(np.float32)
    center = _circ_mean_hue(h, s + 1.0)
    h_rot = (h - center + 90.0) % 180.0
    h_med = (float(np.median(h_rot)) + center - 90.0) % 180.0
    return np.array([h_med, float(np.median(s)), float(np.median(stacked[:, 2]))],
                    dtype=np.float32)


def classify_tracks(
    tracks_df: pd.DataFrame,
    track_jersey_samples: dict[int, list[np.ndarray]],
    our_home_color_hex: str,
    opp_color_hex: str | None = None,
    ref_color_hex: str | None = None,
) -> dict[int, int]:
    track_ids = sorted(set(int(t) for t in tracks_df["track_id"].unique()))
    means: dict[int, np.ndarray] = {}
    for tid in track_ids:
        samples = track_jersey_samples.get(tid, [])
        if not samples:
            continue
        means[tid] = _median_hsv(np.vstack(samples))

    # Per-game hue rotation, engaged ONLY under wrap risk (a red kit): put
    # the saturation-weighted bulk of jersey colors at h=90 so the kit stops
    # straddling the 0/180 wrap during clustering / anchor matching. Without
    # wrap risk delta stays 0 — byte-identical to production (any rotation
    # moves hues across the cut by ±180 in linear space and reshuffles the
    # partition; measured on Windsor: 120/120 labels reproduced → 3/120).
    anchors_hsv = {
        0: _hex_to_hsv(our_home_color_hex),
        1: _hex_to_hsv(opp_color_hex) if opp_color_hex else None,
        -1: _hex_to_hsv(ref_color_hex) if ref_color_hex else None,
    }
    if means:
        _mh = np.array([m[0] for m in means.values()], dtype=np.float32)
        _ms = np.array([m[1] for m in means.values()], dtype=np.float32)
        _anchor_wrap = any(
            a is not None and (a[0] < _WRAP_ZONE or a[0] > 180.0 - _WRAP_ZONE)
            and a[1] >= _CHROMATIC_S
            for a in anchors_hsv.values()
        )
        if _has_wrap_risk(_mh, _ms) or _anchor_wrap:
            delta = (_circ_mean_hue(_mh, _ms + 1.0) - 90.0) % 180.0
            log.info("Team classifier: hue wrap risk detected — rotating hues by -%.1f", delta)
            for m in means.values():
                m[0] = (m[0] - delta) % 180.0
            for a in anchors_hsv.values():
                if a is not None:
                    a[0] = (a[0] - delta) % 180.0

    # Supervised 3-anchor: when the coach has logged a referee color (distinct
    # from both kits), classify each track to the NEAREST of the three known
    # colors instead of unsupervised KMeans. ours→0, opp→1, ref→-1 (excluded).
    # This reliably pulls the on-pitch referee out of our-team tracks — KMeans
    # can't, because the ref fragments into many tracks and a black-clad ref
    # clusters with a black kit. Gated on ref_color so other games are unchanged.
    if ref_color_hex and opp_color_hex and len(means) >= 1:
        anchors = [(0, anchors_hsv[0]), (1, anchors_hsv[1]), (-1, anchors_hsv[-1])]
        out = {tid: -1 for tid in track_ids}
        for tid, m in means.items():
            out[tid] = min(anchors, key=lambda a: float(np.linalg.norm(m - a[1])))[0]
        from collections import Counter
        c = Counter(out.values())
        log.info("Team classifier (supervised, ref-color set): ours=%d opp=%d ref/excluded=%d",
                 c.get(0, 0), c.get(1, 0), c.get(-1, 0))
        return out

    if len(means) < 2:
        log.warning(
            "Team classifier: only %d / %d tracks have jersey samples — cannot "
            "cluster. Returning team_id=-1 for everything (identity will fail). "
            "Likely cause: bboxes are tiny (lots of <30 px high) so sample_jersey_hsv "
            "drops them, or jersey colors are mostly grass/sky (low saturation).",
            len(means), len(track_ids),
        )
        return {tid: -1 for tid in track_ids}

    from sklearn.cluster import KMeans
    X = np.stack(list(means.values()))

    # Try 3 clusters to isolate the referee (typically yellow or black, rare
    # on the field). If the smallest cluster is plausibly the ref (<15% of
    # tracks and there are enough samples to support 3 clusters), drop it as
    # team_id = -1 ("ignore"). Otherwise fall back to k=2.
    use_three = len(X) >= 6
    drop_label = None
    if use_three:
        km3 = KMeans(n_clusters=3, n_init=10, random_state=0).fit(X)
        counts3 = np.bincount(km3.labels_, minlength=3)
        smallest = int(np.argmin(counts3))
        if counts3[smallest] / counts3.sum() < 0.15 and counts3[smallest] >= 1:
            # Looks like a ref/coach singleton cluster. Drop it.
            drop_label = smallest
            labels = km3.labels_
            centers = km3.cluster_centers_
        else:
            use_three = False
    if not use_three:
        km = KMeans(n_clusters=2, n_init=10, random_state=0).fit(X)
        labels = km.labels_
        centers = km.cluster_centers_

    target = anchors_hsv[0]  # our color, already hue-rotated with the means
    # Pick which remaining cluster is "us".
    candidate_centers = [(i, c) for i, c in enumerate(centers) if i != drop_label]
    us_cluster = min(candidate_centers, key=lambda ic: np.linalg.norm(ic[1] - target))[0]

    out = {tid: -1 for tid in track_ids}
    for (tid, _), lbl in zip(means.items(), labels):
        if lbl == drop_label:
            out[tid] = -1
        else:
            out[tid] = 0 if lbl == us_cluster else 1
    if drop_label is not None:
        n_drop = int((labels == drop_label).sum())
        log.info("Team classifier: dropped %d track(s) as ref/non-player (3rd cluster)", n_drop)
    return out


def sample_jersey_hsv(crop: np.ndarray, bbox_crop: tuple[float, float, float, float]) -> np.ndarray:
    """Sample HSV pixels from the upper jersey region of one bbox.

    Returns (N, 3) HSV pixel array. Excludes grass-green and very-low-saturation
    (gray/white sky) pixels.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_crop)
    h_box = y2 - y1
    # Lowered from 30 to 14 so distant U10 players on a wide 1280×720 crop
    # (often ~16–25 px tall) still contribute jersey samples instead of being
    # silently dropped — which is what caused all tracks to get team_id=-1.
    if h_box < 14:
        return np.empty((0, 3), dtype=np.float32)
    # CENTRAL torso ROI (upper-chest/back, central width): below the head, above
    # the shorts, away from the arms — so the jersey dominates and grass/skin is
    # minimal. Tighter than before precisely so we DON'T need an aggressive pixel
    # filter that erased the defining colors of white/black/green kits.
    w_box = x2 - x1
    jy1 = y1 + int(0.18 * h_box)
    jy2 = y1 + int(0.50 * h_box)
    jx1 = x1 + int(0.28 * w_box)
    jx2 = x2 - int(0.28 * w_box)
    jx1 = max(0, jx1); jy1 = max(0, jy1)
    jx2 = min(crop.shape[1], jx2); jy2 = min(crop.shape[0], jy2)
    if jx2 <= jx1 or jy2 <= jy1:
        return np.empty((0, 3), dtype=np.float32)
    roi = crop[jy1:jy2, jx1:jx2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    # Drop ONLY clearly-grass pixels (saturated green) so a small player's grass
    # background can't dominate. Do NOT drop low-saturation pixels — that erased
    # WHITE and BLACK kits (their defining pixels are low-S), the bug that pushed
    # white opponents onto our (green) team and vice-versa. A green kit shares
    # grass's hue, so some of its pixels are dropped too, but the tight central
    # ROI keeps the jersey dominant in what remains.
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    grass = (h >= 35) & (h <= 85) & (s > 60) & (v > 50)
    kept = hsv[~grass]
    # If grass removal nuked almost everything (e.g. a green kit), fall back to
    # the full ROI rather than returning noise.
    return kept if len(kept) >= max(8, len(hsv) // 5) else hsv

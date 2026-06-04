"""KMeans on jersey-region color -> team_id in {0, 1}.

Per track_id, sample HSV pixels from the upper-half of bbox crops across many
frames, then 2-cluster all track median colors. The cluster center closer to
the team's `homeColor` becomes team 0 ("us").
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _hex_to_hsv(hex_color: str) -> np.ndarray:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return np.array([0, 0, 0], dtype=np.float32)
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    bgr = np.uint8([[[b, g, r]]])
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0, 0]
    return hsv.astype(np.float32)


def classify_tracks(
    tracks_df: pd.DataFrame,
    track_jersey_samples: dict[int, list[np.ndarray]],
    our_home_color_hex: str,
) -> dict[int, int]:
    track_ids = sorted(set(int(t) for t in tracks_df["track_id"].unique()))
    means: dict[int, np.ndarray] = {}
    for tid in track_ids:
        samples = track_jersey_samples.get(tid, [])
        if not samples:
            continue
        stacked = np.vstack(samples)
        means[tid] = np.median(stacked, axis=0)

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

    target = _hex_to_hsv(our_home_color_hex)
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
    jy1 = y1
    jy2 = y1 + h_box // 2
    w_box = x2 - x1
    jx1 = x1 + w_box // 5
    jx2 = x2 - w_box // 5
    jx1 = max(0, jx1); jy1 = max(0, jy1)
    jx2 = min(crop.shape[1], jx2); jy2 = min(crop.shape[0], jy2)
    if jx2 <= jx1 or jy2 <= jy1:
        return np.empty((0, 3), dtype=np.float32)
    roi = crop[jy1:jy2, jx1:jx2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    h, s, _v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    keep = ~((h >= 35) & (h <= 85) & (s > 40)) & (s >= 25)
    return hsv[keep]

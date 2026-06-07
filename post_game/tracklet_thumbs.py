"""Per-tracklet representative thumbnails for the coach IdentityFixView.

For each our-team stitched tracklet, pick a good representative detection (large,
high-confidence, person-shaped → closest to the camera and most recognizable),
crop it from the source equirect frame, upscale, and upload to R2 at
``tv_view/<game>/tracklets/<tracklet_id>.jpg``. The PWA shows these next to each
tracklet so the coach can recognize the kid at a glance and fix mislabels.

Cheap: ~one ffmpeg seek+crop per our-team tracklet (a few dozen), small JPEGs.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from . import firestore_io

log = logging.getLogger(__name__)

# Skip frames in the first/last bit of a tracklet's life (entering/leaving the
# frame tends to give partial boxes). A representative detection is large, confident
# and person-shaped.
_MIN_CONF = 0.5
_MIN_ASPECT = 1.3   # h/w — reject squashed/edge boxes
_MAX_ASPECT = 4.5
_TOP_K = 25         # consider the K tallest in-spec detections, pick the median-tall one
_UPSCALE = 4
_PAD_FRAC = 0.35    # pad the crop by this fraction of bbox height for headroom


def _pick_detection(sub: pd.DataFrame):
    """Choose a representative row (large, confident, person-shaped) for a tracklet."""
    s = sub.copy()
    s["h"] = s["y2_eq"] - s["y1_eq"]
    s["w"] = (s["x2_eq"] - s["x1_eq"]).clip(lower=1.0)
    s["aspect"] = s["h"] / s["w"]
    good = s[(s["conf"] >= _MIN_CONF) & (s["aspect"] >= _MIN_ASPECT) & (s["aspect"] <= _MAX_ASPECT) & (s["h"] > 30)]
    if good.empty:
        good = s[s["h"] > 0]
    if good.empty:
        return None
    # Take the K tallest, then the median of those — avoids a single blown-up
    # outlier (mis-sized box) while still favouring a near-camera, legible crop.
    top = good.nlargest(min(_TOP_K, len(good)), "h").sort_values("h")
    return top.iloc[len(top) // 2]


def generate_tracklet_thumbnails(
    tracks_df: pd.DataFrame,
    tracklet_of_track: dict[int, int],
    tracklet_records: list[dict],
    video_path: str,
    game_id: str,
    upload: bool = True,
) -> dict[int, str]:
    """Render + (optionally) upload one thumbnail per our-team tracklet.

    Returns { tracklet_id: thumb_url }. Mutates nothing; the caller stitches the
    urls back onto `tracklet_records`. Best-effort: failures are logged & skipped.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        log.warning("ffmpeg not on PATH — skipping tracklet thumbnails")
        return {}
    if not video_path or not Path(video_path).exists():
        log.warning("source video missing (%s) — skipping tracklet thumbnails", video_path)
        return {}

    df = tracks_df.copy()
    df["tracklet"] = df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))
    want = {int(r["tracklet_id"]) for r in tracklet_records}
    out: dict[int, str] = {}
    tmp = Path(tempfile.mkdtemp(prefix=f"tlthumb_{game_id}_"))
    n_ok = 0
    for tl, sub in df.groupby("tracklet"):
        tl = int(tl)
        if tl not in want:
            continue
        row = _pick_detection(sub)
        if row is None:
            continue
        h = float(row["y2_eq"] - row["y1_eq"])
        pad = h * _PAD_FRAC
        cx = max(0, int(row["x1_eq"] - pad))
        cy = max(0, int(row["y1_eq"] - pad))
        cw = int((row["x2_eq"] - row["x1_eq"]) + 2 * pad)
        ch = int(h + 2 * pad)
        dst = tmp / f"{tl}.jpg"
        try:
            subprocess.run(
                [ff, "-nostdin", "-loglevel", "error", "-ss", f"{float(row['time_s'])}",
                 "-i", video_path,
                 "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale=iw*{_UPSCALE}:ih*{_UPSCALE}:flags=lanczos",
                 "-frames:v", "1", "-q:v", "3", str(dst)],
                check=True, timeout=60,
            )
        except Exception as e:
            log.warning("  thumb render failed for tracklet %d: %s", tl, e)
            continue
        if not dst.exists() or dst.stat().st_size == 0:
            continue
        if upload:
            try:
                url = firestore_io.upload_image(str(dst), f"tv_view/{game_id}/tracklets/{tl}.jpg")
                out[tl] = url
                n_ok += 1
            except Exception as e:
                log.warning("  thumb upload failed for tracklet %d: %s", tl, e)
        else:
            out[tl] = f"file://{dst}"
            n_ok += 1
    log.info("  -> tracklet thumbnails: %d/%d generated%s", n_ok, len(want),
             "" if upload else " (local only, --skip-upload)")
    return out

"""Global constants for the post_game pipeline."""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
POST_GAME_ROOT = REPO_ROOT / "post_game"
CACHE_DIR = POST_GAME_ROOT / ".cache"          # downloaded models, intermediate frames
OUTPUTS_DIR = POST_GAME_ROOT / "outputs"       # per-game analytics + clips (local mirror)
MODELS_DIR = POST_GAME_ROOT / "models"

for _d in (CACHE_DIR, OUTPUTS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Device --------------------------------------------------------------

# Lazy import so the lightweight UI (which only imports firestore_io -> config)
# doesn't require torch in its venv.
def _detect_device() -> str:
    try:
        import torch  # noqa: WPS433
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class _LazyDevice(str):
    """Behaves like a str, but only probes torch on first access."""
    _resolved: str | None = None

    def _resolve(self) -> str:
        if _LazyDevice._resolved is None:
            _LazyDevice._resolved = _detect_device()
        return _LazyDevice._resolved

    def __str__(self) -> str:        # noqa: D401
        return self._resolve()

    def __repr__(self) -> str:
        return repr(self._resolve())

    def __eq__(self, other) -> bool:
        return self._resolve() == other

    def __hash__(self) -> int:
        return hash(self._resolve())


DEVICE = _LazyDevice()

# --- Video ---------------------------------------------------------------

# Sample 1-of-N frames through the pipeline. 3 == 10 Hz from a 30 fps source.
SAMPLE_RATE = 3

# Perspective crop size rendered from equirectangular for detection
CROP_W = 1280
CROP_H = 720
CROP_FOV_DEG = 80.0

# --- Multi-tile detection ------------------------------------------------
#
# A single perspective crop cannot cover the whole field from our mount
# (centerline + 3m behind sideline + 5m up) — a 50m-wide pitch subtends
# ~170° of horizontal angle from there, but pushing one crop past ~90° FOV
# squishes far-side players below YOLO's detect threshold.
#
# Instead, we render N overlapping tiles per video frame, run YOLO on the
# whole batch, project each detection to the field via the sphere
# projector, and dedupe detections within DETECT_TILE_DEDUPE_M of each
# other (same player picked up by two tiles where they overlap).
#
# Tile aims are computed from the field calibration once per pipeline run:
# the outer tiles aim at the end-lines (field-X = 0 and L) and inner tiles
# are evenly spaced between them, all projected through the sphere model.
# Each tile is rendered at DETECT_TILE_FOV_DEG. From our centerline+3m+5m
# X5 mount the far touchline corners sit at lon ≈ ±84°. With 3 tiles at
# 75° FOV the total horizontal span is ~177° with ~24° overlap between
# adjacent tiles — covers the whole pitch and gives any handed-off track
# a chance to be re-detected in the neighbor tile.
DETECT_N_TILES = 3
DETECT_TILE_FOV_DEG = 75.0
DETECT_TILE_DEDUPE_M = 1.5     # foot positions within this distance (m)
                               # are merged; keep the higher-confidence det.

# --- Detection -----------------------------------------------------------

YOLO_MODEL = "yolo11s.pt"
DETECT_CONFIDENCE = 0.30
PERSON_CLASS_ID = 0
BALL_CLASS_ID = 32                              # COCO sports ball

# --- Tracking ------------------------------------------------------------

TRACKER_TYPE = "botsort"                         # bytetrack | botsort | deepocsort
REID_WEIGHTS = "osnet_x0_25_msmt17.pt"
TRACK_BUFFER_S = 20                              # how long a lost track is kept

# --- Identity ------------------------------------------------------------

# Fusion weights (sum to 1.0). Coach log dominates; gait/cleat dropped vs old plan.
ID_WEIGHTS = {
    "coach_log": 0.60,
    "jersey_ocr": 0.25,
    "face":       0.10,
    "height":     0.05,
}

ID_CONFIDENCE_AUTO = 0.80      # auto-assign if fused score >= this
ID_CONFIDENCE_REVIEW = 0.50    # 0.50..0.80 → flag for coach review

MIN_BBOX_H_FOR_OCR = 80        # px; smaller → don't bother running OCR
MIN_BBOX_H_FOR_FACE = 90       # px

# --- Tracklet stitching (reid_stitch.py) ---
STITCH_MAX_GAP_S = 10.0        # max temporal gap between fragments to consider linking
STITCH_SLACK_M = 3.0           # plausible-move slack for near-zero gaps (foot-pos noise)
STITCH_APPEARANCE_COS = 0.55   # OSNet Re-ID cosine ≥ this → same player (appearance gate)
STITCH_HSV_COS = 0.90          # jersey-HSV cosine gate (fallback; mainly rejects cross-color)
STITCH_GAP_WEIGHT = 0.5        # link-cost weight on temporal gap (s)
STITCH_APP_WEIGHT = 5.0        # link-cost weight on (1 - appearance cosine)

# --- Coach-log identity assignment (identity_assign.py) ---
# Board (coach tactical drag) coords: x∈[0,1] left→right (coach POV),
# y∈[0,1] 0=halfway/attacking → 1=own goal.
ASSIGN_POS_SIGMA_M = 18.0      # Gaussian width for tracklet↔expected-position distance
ASSIGN_W_POSITION = 1.0        # weight: agreement with coach board position over time
ASSIGN_W_VOTES = 1.5           # weight: coach action-event votes (player did X here)
ASSIGN_W_ONFIELD = 1.0         # weight: on-field-window overlap (lineup+subs, tolerant)
ASSIGN_GK_BONUS = 3.0          # (legacy) GK now handled separately, not via bonus
ASSIGN_MATCH_MAX_FRAC = 0.55   # reject a tracklet↔player window-match beyond this
                               # fraction of field length (kills far-fetched votes)
ASSIGN_MINUTE_SLACK = 1.5      # per-player budget = coach-logged minutes + this
                               # (a player can't own more track-time than played)
# Coach IdentityFixView: an UNASSIGNED tracklet shorter than this (span minutes)
# is a stitching fragment with no meaningful player-time — hidden from review so
# the fix list stays a few dozen cards, not ~400. Assigned tracklets always show.
TRACKLET_REVIEW_MIN_MINUTES = 1.0

# --- Stats ---------------------------------------------------------------

SPRINT_THRESHOLD_MS = 4.5                        # m/s; U10 sprint = ~16 km/h
SPEED_SMOOTH_WINDOW = 5                          # samples (≈0.5s at SAMPLE_RATE=3)
# Physical sanity ceiling. No U10 outfield player exceeds ~9 m/s (~32 km/h);
# anything faster is an identity-swap teleport, not real motion. Used to clamp
# per-step displacement so absurd top speeds (6000+ km/h) and teleport-inflated
# distances can't occur.
MAX_PLAUSIBLE_SPEED_MS = 9.0                      # ~32 km/h

# Field thirds (defensive / mid / attacking) split along long axis
THIRDS_FRACTIONS = (1 / 3, 2 / 3)

# --- Highlights ----------------------------------------------------------

CLIP_PRE_SECONDS = 12
CLIP_POST_SECONDS = 8
CLIP_EVENT_TYPES = ("GOAL", "ASSIST", "SAVE", "SHOT_ON", "KEY_PASS")
CLIP_RESOLUTION = (1920, 1080)
CLIP_FOV_DEG = 70.0                              # narrow so field fills frame on
                                                 # low sideline pole (was 95°;
                                                 # wider just imports sky).
CLIP_LAT_TILT_DEG = -7.0                         # downward tilt to push horizon
                                                 # to top edge — see tv_view.py
                                                 # TV_LAT_TILT_DEG for rationale.

# --- GK positioning ------------------------------------------------------

GK_SHOT_LOOKBACK_S = 0.5                         # sample GK pos this much before event timestamp
GK_EVENT_TYPES = ("SHOT_ON", "GOAL", "SAVE")

# --- Firestore / R2 ------------------------------------------------------

FIRESTORE_PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "stompers-tracker")
FIRESTORE_TEAM_DOC = "teams/main"
ANALYTICS_DOC_VERSION = "v1"                     # bump if schema breaks

R2_BUCKET = os.environ.get("R2_BUCKET", "stompers-videos")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")  # set in env, never committed
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "")

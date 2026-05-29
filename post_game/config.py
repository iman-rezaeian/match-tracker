"""Global constants for the post_game pipeline."""

from __future__ import annotations

import os
from pathlib import Path

import torch

# --- Paths ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
POST_GAME_ROOT = REPO_ROOT / "post_game"
CACHE_DIR = POST_GAME_ROOT / ".cache"          # downloaded models, intermediate frames
OUTPUTS_DIR = POST_GAME_ROOT / "outputs"       # per-game analytics + clips (local mirror)
MODELS_DIR = POST_GAME_ROOT / "models"

for _d in (CACHE_DIR, OUTPUTS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Device --------------------------------------------------------------

if torch.backends.mps.is_available():
    DEVICE = "mps"
elif torch.cuda.is_available():
    DEVICE = "cuda"
else:
    DEVICE = "cpu"

# --- Video ---------------------------------------------------------------

# Sample 1-of-N frames through the pipeline. 3 == 10 Hz from a 30 fps source.
SAMPLE_RATE = 3

# Perspective crop size rendered from equirectangular for detection
CROP_W = 1280
CROP_H = 720
CROP_FOV_DEG = 80.0

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

# --- Stats ---------------------------------------------------------------

SPRINT_THRESHOLD_MS = 4.5                        # m/s; U10 sprint = ~16 km/h
SPEED_SMOOTH_WINDOW = 5                          # samples (≈0.5s at SAMPLE_RATE=3)

# Field thirds (defensive / mid / attacking) split along long axis
THIRDS_FRACTIONS = (1 / 3, 2 / 3)

# --- Highlights ----------------------------------------------------------

CLIP_PRE_SECONDS = 12
CLIP_POST_SECONDS = 8
CLIP_EVENT_TYPES = ("GOAL", "ASSIST", "SAVE", "SHOT_ON", "KEY_PASS")
CLIP_RESOLUTION = (1920, 1080)
CLIP_FOV_DEG = 95.0                              # slightly wider than play crop

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

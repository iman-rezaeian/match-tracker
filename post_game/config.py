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
# YOLO inference resolution. Ultralytics defaults to 640, which downsamples
# our 1280-wide detection tiles 2x and loses far/small players (the accuracy
# audit's B3 — a prime source of missed far-side detections + fragmentation).
# Set to the tile width so inference runs at native tile resolution. Ultralytics
# resizes to a multiple of 32; 1280 is already aligned.
DETECT_IMGSZ = 1280
PERSON_CLASS_ID = 0
BALL_CLASS_ID = 32                              # COCO sports ball

# --- Tracking ------------------------------------------------------------

TRACKER_TYPE = "botsort"                         # bytetrack | botsort | deepocsort
REID_WEIGHTS = "osnet_x0_25_msmt17.pt"
# How long a lost track is kept alive for re-acquisition. Env-overridable so a
# tuning sweep can vary it without a code edit (a long buffer lets a lost track
# linger and re-acquire the WRONG same-kit body once it reappears).
TRACK_BUFFER_S = float(os.environ.get("TRACK_BUFFER_S", "20"))
# Accuracy-audit B2 (default OFF): associate tracks in field-metric surrogate
# space instead of the distorted equirect frame. See B2_FIELD_SPACE_TRACKING.md +
# post_game/tracking_field.py. Re-ID de-corruption (real crops via boxmot embs) +
# _new_tracker wiring + surrogate-velocity unit test have landed; the tracker is
# correct and swappable. Env-overridable (like the other tuning flags below) so a
# validation re-track can enable it for ONE run without flipping the prod default.
# Do NOT change the default to True until a re-track on a game with a fresh raw
# video AND blind GT labels shows the fragment count drops AND per-player GT
# recall does not regress.
TRACK_FIELD_SPACE = os.environ.get("TRACK_FIELD_SPACE", "0") != "0"
# Accuracy rebuild: the DeepSORT-on-pitch tracker (post_game/tracking_pitch.py).
# Associates in field-METERS with a point-distance gate + matching cascade + a
# per-track Kalman in meters — the standard fixed-camera-sports method. Unlike the
# B2 surrogate (constant-box IoU, which merged neighbors in the U10 swarm and
# regressed), a point gate at ~1 m sits far below U10 spacing so it can't swap
# neighbors. Independent of TRACK_FIELD_SPACE; if both set, TRACK_PITCH wins.
# Default OFF, env-overridable for a single validation re-track; do NOT flip the
# default until a re-track beats the equirect baseline on fragments AND coverage.
TRACK_PITCH = os.environ.get("TRACK_PITCH", "0") != "0"

# --- VLM jersey-number identity drafts (tracking/vlm_identity.py) -------------
# When on, the pipeline reads each our-team tracklet's jersey number with a VLM
# after building the tracklet index, and writes per-tracklet SUGGESTION drafts to
# game.identityDrafts (the PWA FIX-IDS view surfaces them as one-click Accept
# chips → identityOverrides). Keyed by the run's OWN tracklet ids so they match
# the analytics doc the coach sees.
#
# Default ON as of 2026-08-06, measured on mri01pvelv46d against a candidate pool
# that the kit-vote fix had just cleaned of opposition:
#   * coach-log anchors alone name  ~4.6% of tracked time
#   * + VLM jersey numbers          ~35%   (30 drafts, 26 at confidence >= 0.8)
#   * cost: ~9 min on top of a ~2 h run, and it also pruned 18 opponent
#     tracklets the colour classifier had let through
# Jersey numbers are the only per-player signal that separates children in
# identical kit — the tactical board cannot (45 POSITION events for a whole
# game, overlapping heavily), which is what held naming near zero for so long.
#
# MUST run in the same pass as tracking: it renders number crops from the RAW
# video, and that 80 GB source is deleted after a verified analysis. There is no
# second chance to add drafts later, and --stats-only skips this stage entirely
# (the pipeline warns if you ask for both).
VLM_IDENTITY = os.environ.get("VLM_IDENTITY", "1") != "0"
VLM_IDENTITY_MODEL = os.environ.get("VLM_IDENTITY_MODEL", "claude-opus-4-8")
VLM_IDENTITY_MIN_CONF = float(os.environ.get("VLM_IDENTITY_MIN_CONF", "0.5"))
VLM_IDENTITY_MAX_TRACKLETS = int(os.environ.get("VLM_IDENTITY_MAX_TRACKLETS", "120"))
# Crop-selection + prescreen. The squad number is on the BACK, so a frame only
# carries one while the player runs away from the camera — but crops used to be
# chosen purely by bbox height, i.e. by closeness, which is uncorrelated with
# facing. Measured: 74 of 105 tracklets returned no number, and every read that
# DID succeed described the number as being on the back. The literature is
# blunter still: only ~5% of a tracklet's frames are legible, and choosing them
# deliberately beats a better recogniser (+37.8%, arXiv:2309.06285).
# A number is ~17% of body height here, so a 71 px median body gives a 12 px
# digit — below what any recogniser reads. Skipping those before the call is
# free accuracy and fewer VLM calls.
VLM_MIN_DIGIT_PX = float(os.environ.get("VLM_MIN_DIGIT_PX", "14"))
# Cosine of travel direction vs the camera→player ray: +1 = running straight
# away (back turned), -1 = straight at the camera. Require the tracklet's best
# frame to clear this. Deliberately lenient — a wrongly-skipped tracklet is a
# player the coach then has to name by hand.
VLM_MIN_AWAY = float(os.environ.get("VLM_MIN_AWAY", "-0.30"))

# --- PitchTracker association gates (TRACK_PITCH only; env-overridable) -------
# The meter-space tracker's fragment win (775->90) came partly from GLUING
# different players into one track: 46% of its colored track-seconds were in
# color-MIXED (green+blue) tracks vs the equirect baseline's 16% — i.e. it
# swaps between our (green) and opponent (blue) players in the U10 swarm. These
# gates attack that. All read only when TRACK_PITCH is on; prod is untouched.
#
# (1) Team-color association gate. Green (ours) vs blue (opp) is the ONE
# cross-team signal that survives on same-kit U10s (OSNet is noise). A track
# that has committed to one kit refuses a detection of the other kit. The
# anchors are the two kit hues (derived per-game from the hexes at construction,
# compared nearest-of-two on the OpenCV hue circle 0-179), so no team_id
# assignment is needed at tracking time. The tracker samples color with its OWN
# raw-ROI reader (_det_kit_color), NOT sample_jersey_hsv: that fn grass-drops
# H35-85 (team_classifier.py:253), which would delete our green kit (H71 sits in
# that band) and make the gate blue-only.
PITCH_COLOR_GATE = os.environ.get("PITCH_COLOR_GATE", "1") != "0"
# Per-pixel S floor for the color decision — deliberately LOW so a grass-washed
# green still reads green-vs-blue (decoupled from any downstream saturation bar).
PITCH_COLOR_MIN_S = float(os.environ.get("PITCH_COLOR_MIN_S", "35"))
PITCH_COLOR_MIN_PIXELS = int(os.environ.get("PITCH_COLOR_MIN_PIXELS", "10"))
# Nearest-anchor margin (hue-deg): the pixel-median must be at least this much
# closer to one kit anchor than the other to count as that kit; inside the
# margin -> UNKNOWN (never rejects). Green(71)/blue(111) are ~40 apart, midpoint
# ~91; a small margin claims each basin while leaving a neutral dead-zone around
# the desaturated ~H90-107 collapse center so a washed track can't flip.
PITCH_COLOR_MARGIN_DEG = float(os.environ.get("PITCH_COLOR_MARGIN_DEG", "6.0"))
# Cross-team association cost. A committed track seeing a CONFIDENT opposite-kit
# detection adds this many METERS of cost to that pair instead of hard-rejecting
# it. SOFT (a penalty) not HARD (a veto), because a hard veto shatters a track
# whenever an opponent transiently occludes it or a frame mis-samples color: the
# track's own continuation gets refused, it ages, and the detection spawns a new
# track -> a full-game re-track exploded 90 fragments -> 6399 (54% <5 s) and
# overshot the team split to 79% ours. The penalty (>> the ~6 m gate cap) keeps
# the solver STRONGLY preferring same-kit, so a real cross-team grab still loses
# to any same-kit alternative, but a track with ONLY an opposite-kit detection
# in range still holds its id through the occlusion instead of fragmenting. Set
# to inf to restore the old hard-reject behaviour for comparison.
PITCH_COLOR_PENALTY_M = float(os.environ.get("PITCH_COLOR_PENALTY_M", "50.0"))
# A track "commits" to a kit only after this many NET votes (green +1 / blue -1),
# so one mis-sampled frame can't lock it; the running score is clipped to
# +/-COMMIT_CLIP so a genuinely wrong early commit can be out-voted within ~1 s.
PITCH_COLOR_COMMIT_VOTES = int(os.environ.get("PITCH_COLOR_COMMIT_VOTES", "3"))
PITCH_COLOR_COMMIT_CLIP = int(os.environ.get("PITCH_COLOR_COMMIT_CLIP", "8"))
# A STALE track has no business asserting color authority (its color memory is
# frozen at its last match). Above this time_since_update the color gate is
# disabled for that track -> motion + cap decide, and it can re-acquire and
# re-vote instead of dead-locking (refusing the only dets that would un-commit).
PITCH_COLOR_MAX_TSU = int(os.environ.get("PITCH_COLOR_MAX_TSU", "3"))
#
# (2) Motion clamps.
# (a) Cap the per-frame gate's unbounded growth with time_since_update. At the
#     shipped rate (fps/SAMPLE_RATE = 30/3 = 10 fps -> dt=0.1 s, verified on W8)
#     the fresh gate is MAX_PLAUSIBLE_SPEED_MS*dt + slack = 3.9 m and the cap
#     first bites at tsu=2 (4.8 m stays under 6.0); the old code let it grow to
#     ~22 m at tsu=20 (2 s lost) -> a cross-pitch grab. So the cap only bites
#     multi-frame-stale tracks and CANNOT re-fragment an in-view one. NOTE: this
#     6.0 m assumes dt~=0.1 s; a materially larger dt (lower fps or higher
#     SAMPLE_RATE) shrinks the headroom above the fresh gate, so revisit the cap
#     if the sampled frame rate changes.
PITCH_GATE_CAP_M = float(os.environ.get("PITCH_GATE_CAP_M", "6.0"))
# (b) Per-frame slack. Default UNCHANGED at 3.0 (== STITCH_SLACK_M) so the color
#     gate carries swap reduction without risking re-fragmentation. Cut toward
#     1.5 ONLY as a fallback if color under-delivers (see fix-design memory).
PITCH_SLACK_M = float(os.environ.get("PITCH_SLACK_M", "3.0"))
# (c) NaN/unprojectable pixel-fallback: shrink 150->80 px and forbid stale
#     tracks. A NaN det carries no meters, so pixel proximity is the only guard;
#     only a just-seen track (tsu<=NAN_MAX_TSU) may absorb one, color-gated like
#     any other match.
PITCH_PX_GATE = float(os.environ.get("PITCH_PX_GATE", "80"))
PITCH_NAN_MAX_TSU = int(os.environ.get("PITCH_NAN_MAX_TSU", "1"))

# --- Calibration quality gate --------------------------------------------
# "Run Analysis" hard-blocks on a calibration that fails these, so the coach
# never spends hours tracking a bad calibration and never needs a developer to
# eyeball it. Thresholds set from the real stored calibrations: good scaled_lsq
# fits are 0.27-0.65 m RMS; the un-anchored legacy fits are 0.94-1.47 m. See
# post_game/calibration_qc.py (the only consumer) + CALIBRATION_SCALE_PLAN.md.
CALIB_MAX_RMS_M = 1.0            # scaled_lsq fit RMS above this = re-calibrate
CALIB_WIDTH_MIN = 20.0          # plausible field width band (also the solver's bounds)
CALIB_WIDTH_MAX = 50.0
CALIB_WIDTH_CONSISTENCY_TOL_M = 2.5   # same field's width must agree run-to-run within this
                                      # (two real scaled fits of adjacent fields agreed to 0.8 m)

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
# Confidence saturates toward 1 only with this many supporting match windows
# (each ≈ WINDOW_S=5s). Stops a 1-window positional fluke from reading 100% on a
# 1-second fragment; ~this many windows of agreement ≈ 63% confidence.
ID_CONFIDENCE_EVIDENCE_VOTES = 8
# RECALL floor: assign a tracklet to its best-guess player (so it feeds the
# player's distance/heatmap) down to this confidence, not just the REVIEW tier.
# Below this it's too weak and is dropped. Keeps under-tracked players from
# collapsing to near-zero distance; the minute budget still caps over-assignment.
# Raised 0.20 → 0.35 (2026-06-10): the minute budget used to charge stitched
# SPAN (massively over-counted), which silently strangled recall and acted as
# an accidental precision gate. With the budget fixed to charge real tracked
# coverage, 0.20 floods stats with junk tracklets (opponents mis-classified
# into our team by jersey color). 0.35 is the measured middle on the
# coach-labeled eval set (see tracking/eval_identity.py + the improvement
# plan); revisit after the Lab-color classifier change lands.
ID_CONFIDENCE_STATS_MIN = 0.35

# --- Tier 0 identity calibration (identity_assign.py) ---------------------
# The auto-assigner's only signal for telling teammates apart is the coach's
# STATIC board-formation template (a per-window Hungarian match, σ=18 m). That
# identifies ZONE/ROLE, not the individual, yet the confidence formula rewards
# geometric CONSISTENCY not correctness — so a tracklet consistently nearest the
# WRONG teammate's slot still reads "auto"/100%. When enabled, a tracklet whose
# CHOSEN player has no individuating anchor (action-event vote or SUB anchor;
# board EXCLUDED) can't read confident. GK geometry + coach overrides use
# separate code paths and are never capped. ON by default as of 2026-06-17
# (8K proof on mqcf9axlvtuyt: demotes 18 confident-wrong tracklets, zero
# accuracy regression, GK/coach paths untouched). Set =0 to disable.
ID_ANCHOR_CAP_ENABLED = os.environ.get("ID_ANCHOR_CAP_ENABLED", "1") != "0"
# Min summed anchor weight (event-vote weight + SUB weight; board EXCLUDED) for a
# (tracklet, chosen) pair to count as individuating-anchored. One event vote
# ≈ ASSIGN_W_VOTES*sc (≤1.5); one SUB anchor = ASSIGN_SUB_W (3.0). 1.0 ⇒ "at
# least one solid event vote, or any SUB anchor".
ID_ANCHOR_MIN_W = float(os.environ.get("ID_ANCHOR_MIN_W", "1.0"))
# Confidence ceiling for template-only (un-anchored) tracklets when the cap is
# on. 0.49 keeps them strictly below ID_CONFIDENCE_REVIEW (0.50) → 'lowconf'
# (still feeds stats, honest low confidence), never 'auto'/'review'/green.
ID_TEMPLATE_ONLY_CONF_CAP = float(os.environ.get("ID_TEMPLATE_ONLY_CONF_CAP", "0.49"))
# Orientation guardrail: flag a period when best vs 2nd-best LATERAL board
# orientation total-cost are within this RELATIVE margin (the search may have
# mirrored the whole team left↔right). 0.0 = no-op (emit nothing).
ID_ORIENT_AMBIG_REL_MARGIN = float(os.environ.get("ID_ORIENT_AMBIG_REL_MARGIN", "0.0"))

MIN_BBOX_H_FOR_OCR = 80        # px; smaller → don't bother running OCR
MIN_BBOX_H_FOR_FACE = 90       # px

# --- Tracklet stitching (reid_stitch.py) ---
STITCH_MAX_GAP_S = 10.0        # max temporal gap between fragments to consider linking
STITCH_SLACK_M = 3.0           # plausible-move slack for near-zero gaps (foot-pos noise)
# The slack above is ADDED to the speed budget, so at short gaps it IS the
# budget and the physics gate stops filtering: at a 0.1 s gap it permits 39 m/s,
# at 0.2 s it permits 24 m/s — exactly where adjacent fragments are most
# confusable. Measured: 48 of 1358 joins (4%) require >9 m/s, up to 23.7 m/s,
# every one at a 0.2-0.3 s gap. This caps the IMPLIED SPEED as well, so the
# slack still absorbs foot-position jitter but can't license a teleport. The
# floor keeps a sub-frame gap from dividing by ~0.
STITCH_SPEED_CAP_ENABLED = os.environ.get("STITCH_SPEED_CAP_ENABLED", "1") != "0"
STITCH_SPEED_CAP_MIN_DT_S = float(os.environ.get("STITCH_SPEED_CAP_MIN_DT_S", "0.5"))
STITCH_APPEARANCE_COS = 0.55   # OSNet Re-ID cosine ≥ this → same player (appearance gate)
STITCH_HSV_COS = 0.90          # jersey-HSV cosine gate (fallback; mainly rejects cross-color)
STITCH_GAP_WEIGHT = 0.5        # link-cost weight on temporal gap (s)
# Link-cost weight on (1 - appearance cosine). OSNet embeddings are empirically
# kit-dominated on this footage (random cross-track cosine 0.62-0.75 — they can't
# even separate teams), so appearance carries little signal; env-overridable to A/B
# lowering it. Committed default unchanged (5.0).
STITCH_APP_WEIGHT = float(os.environ.get("STITCH_APP_WEIGHT", "5.0"))
# Absolute cap (m) on the A-end -> B-start move, on top of speed*gap+slack.
#
# The speed term alone is far too permissive here because it assumes the child
# sprints in a DEAD-STRAIGHT LINE for the whole unobserved gap: at 9 m/s over the
# 10 s max gap it sanctions a 93 m move on a 55x30 m pitch. Measured on this
# game's 761 in-tracklet joins, the median join already spans 5.4 m and the p90
# spans 24 m, with a 64 m maximum — longer than the pitch. Real U10s cover ~1 m/s
# median and change direction constantly, so those long joins are how one
# tracklet ends up holding two different children. Independent evidence: reading
# the same tracklet with the VLM at two times seconds apart returns two different
# squad numbers (see NEXT_RETRACK.md #5).
#
# 12 m rejects 26% of joins — every physically absurd one — while keeping the
# large tracklets that are easiest for the coach to recognise. Simulated cost to
# him: the taps needed to cover 90% of tracked time go 71 -> 122, and tracklets
# of 2 min or more only fall 50 -> 41. Tightening further is a bad trade: 8 m
# costs another 37 taps and destroys 6 more of those big tracklets, while cutting
# genuine merges of a child who simply jogged 9 m during an occlusion.
#
# A rejected join is not lost data — it becomes a separate tracklet the coach can
# name. A wrong merge silently credits one child's running to another, which no
# amount of naming repairs.
STITCH_DIST_CAP_M = float(os.environ.get("STITCH_DIST_CAP_M", "12.0"))
# Stitch chaining mode: "greedy" (each fragment grabs its locally-cheapest
# successor — the shipped behavior) or "global" (min-cost bipartite matching over
# the same gated edges, so a locally-cheap link never orphans a fragment that had
# a better global pairing). Both use the IDENTICAL gates + cost; only the chaining
# differs. Env-overridable for A/B; default greedy so prod is unchanged.
STITCH_MODE = os.environ.get("STITCH_MODE", "greedy")

# May two fragments be chained when their [t0,t1] envelopes intersect but they
# never actually coexist (b lives inside a's interior gap)? That IS a legal
# continuation, and the shipped endpoint rule wrongly refuses it — on W8 the
# endpoint rule rejected 99123 of 103819 candidate pairs vs 17288 under the
# interior test, so this is a large slice of the fragmentation. But permitting
# them is a far bigger behavioural change than the overlap BUG FIX (which is
# always on), and it measured 52.1% -> 49.0% named-coverage in a first A/B, so
# it stays OFF until validated on GT rather than on coverage alone.
STITCH_JOIN_ACROSS_HOLES = os.environ.get("STITCH_JOIN_ACROSS_HOLES", "0") != "0"

# --- Iterative anchor-coupled re-stitch (post_game/iterative_identity.py) -----
# Couple stitching and identity: stitch (geometry-only) → seed identities from
# individuating coach-log anchors (event/SUB/keeper) → use those seeds as
# MUST-LINK / CANNOT-LINK constraints → re-stitch → re-assign, for a few rounds.
# Lets one coach confirmation cover a longer, cleaner chain. OFF by default until
# the GT A/B clears the precision guardrail.
ID_ITERATIVE_ENABLED = os.environ.get("ID_ITERATIVE_ENABLED", "0") != "0"
ID_ITERATIVE_MAX_ROUNDS = int(os.environ.get("ID_ITERATIVE_MAX_ROUNDS", "3"))
# PRECISION-SAFE geometry for the iterative rounds. The un-seeded majority is
# stitched by geometry alone, so it must stay tight or same-team crossings
# over-merge into cross-player chimeras (the "499→814 was partly false merges"
# trap). METRICS_RELEVANCE_PLAN's within-team finding: gap 5s + abs dist-cap 12m
# → ~814 CLEAN chains. Long-gap bridging is left to identity-gated MUST-LINK
# (safe: a confirmed identity says it IS the same player), not to loose geometry.
ID_ITERATIVE_GAP_S = float(os.environ.get("ID_ITERATIVE_GAP_S", "5.0"))
ID_ITERATIVE_DIST_CAP_M = float(os.environ.get("ID_ITERATIVE_DIST_CAP_M", "12.0"))

# --- Public-reel audio swap (public_audio.py, stage 7b) ---
# Replace the PUBLIC reel's audio with a stadium-ambience bed + goal roars so the
# coach voice / kids' names never leave the dugout. Dugout reel keeps original.
PUBLIC_AUDIO_ENABLED = os.environ.get("PUBLIC_AUDIO_ENABLED", "") == "1"
PUBLIC_AMBIENCE_PATH = os.environ.get("PUBLIC_AMBIENCE_PATH", "tracking/assets/stadium_ambience.mp3")
PUBLIC_ROAR_PATH = os.environ.get("PUBLIC_ROAR_PATH", "tracking/assets/goal_roar.mp3")
PUBLIC_BED_DB = float(os.environ.get("PUBLIC_BED_DB", "-8"))     # stadium bed level (dB rel. to source) — was -20 (too dim)
PUBLIC_ROAR_DB = float(os.environ.get("PUBLIC_ROAR_DB", "-13"))  # goal-roar level — was -6 (too loud vs bed); ~8dB above bed now
# The coach logs a goal ~tap_delay AFTER it happens, and goal-moment detection is
# unreliable here (near-mic chatter / far-side crowd). So lead the roar earlier and
# fade it IN so it BUILDS rather than banging at a wrong instant — the build hides
# the timing slop and reads like a real crowd swelling as the goal goes in.
PUBLIC_ROAR_LEAD_S = float(os.environ.get("PUBLIC_ROAR_LEAD_S", "7"))   # start the roar this many s before the tap
PUBLIC_ROAR_FADE_S = float(os.environ.get("PUBLIC_ROAR_FADE_S", "2.5")) # fade-in (build) duration

# --- Halftime split (pipeline.py, stage 3 -> 4) ---
# No player is one continuous body across the halftime break, so any track_id
# with detections on both sides has welded two different children together.
# Two independent causes, and the id-carry only fixes the first:
#   1. ID COLLISION — a fresh tracker restarts ids at 1, so half-2 ids reuse
#      half-1 ids. Fixed at the source by carrying `_next_id` across the reset
#      (pipeline._new_tracker call site). Measured on the pre-fix caches:
#      1257/3555, 867/2887, 873/3609 ids affected = 39-57% of tracked time.
#   2. MISPLACED BOUNDARY — the reset fires at `h1_end_s`, derived from the
#      coach's taps (wallclock delta + video offset), which lags the whistle by
#      up to 47 s on real games. A late boundary lands on empty frames and is
#      harmless; nothing guarantees that in general.
# This pass detects the break from the FOOTAGE (body count collapses to ~0 for
# the whole break) and hard-splits anything still spanning it, so the invariant
# holds regardless of cause. Lossless: relabels ids, never drops detections.
# Default ON — it is a no-op on a clean game (nothing spans the break).
HALFTIME_SPLIT_ENABLED = os.environ.get("HALFTIME_SPLIT_ENABLED", "1") != "0"

# --- Gap-split pre-pass (pipeline.py, stage 3 -> 4) ---
# Split each track_id at internal time gaps > SPLIT_GAP_S into clean contiguous
# sub-tracks, removing "zombie" ids kept alive across long gaps (they teleport
# between bodies and inflate distance). Off by default; flip only after A/B.
# NOTE: deliberately much broader than HALFTIME_SPLIT_ENABLED above, which cuts
# at exactly one time per track. Gap-split shatters good tracks to fix bad ones
# (96% sub-30s pieces, r +0.733 -> +0.370 on common players), which is why that
# one stays off and the halftime cut stays on.
GAP_SPLIT_ENABLED = os.environ.get("GAP_SPLIT_ENABLED", "") == "1"
SPLIT_GAP_S = float(os.environ.get("SPLIT_GAP_S", "1.0"))

# --- Switch-detection split (gap_split.py switch_split_tracks) ---
# gap-split cuts only on TIME gaps; a contiguous run can still contain a mid-run
# identity swap (the team-blind tracker briefly latches onto a nearby body during
# a crossing/scramble, no time gap). Switch-split cuts a run where the body
# TELEPORTS: a single sampled step whose implied speed exceeds SWITCH_MAX_SPEED_MS
# AND whose distance exceeds SWITCH_MIN_JUMP_M (the dual gate keeps real sprints,
# ~1 m/step, from tripping it). Off by default. SWITCH_MAX_SPEED_MS mirrors
# MAX_PLAUSIBLE_SPEED_MS (=9.0, defined below in the stats block).
SWITCH_SPLIT_ENABLED = os.environ.get("SWITCH_SPLIT_ENABLED", "") == "1"
SWITCH_MAX_SPEED_MS = float(os.environ.get("SWITCH_MAX_SPEED_MS", "9.0"))
SWITCH_MIN_JUMP_M = float(os.environ.get("SWITCH_MIN_JUMP_M", "3.0"))
# Secondary, conservative: also split on a sharp heading reversal (same-area
# crossing swap). Off by default — validate teleport-split first.
SWITCH_REVERSAL_ENABLED = os.environ.get("SWITCH_REVERSAL_ENABLED", "") == "1"
SWITCH_REVERSAL_DEG = float(os.environ.get("SWITCH_REVERSAL_DEG", "120.0"))

# --- Coach-log identity assignment (identity_assign.py) ---
# Board (coach tactical drag) coords: x∈[0,1] left→right (coach POV),
# y∈[0,1] 0=halfway/attacking → 1=own goal.
ASSIGN_POS_SIGMA_M = 18.0      # Gaussian width for tracklet↔expected-position distance
ASSIGN_W_POSITION = 1.0        # weight: agreement with coach board position over time
ASSIGN_W_VOTES = 1.5           # weight: coach action-event votes (player did X here)
# Action-event vote window relative to the LOGGED clock time. Coach logs lag
# the real action, so the action almost always precedes the log: look back
# far, forward a little.
ASSIGN_EVENT_BEFORE_S = 25.0
ASSIGN_EVENT_AFTER_S = 5.0
# Gaussian widths for event votes. The action's location proxy is the team
# centroid (U10 swarm ≈ where the ball is); the zone tag (3×3 grid, ~1/3-field
# cells) is coarser, so it gets a wider sigma and only damps, never zeroes.
ASSIGN_EVENT_SIGMA_M = 8.0
ASSIGN_ZONE_SIGMA_M = 12.0
# SUB anchors: a tracklet that STARTS near a touchline around a logged sub-on
# is very likely the incoming player (symmetric: ENDS near touchline ↔
# sub-off). Stronger than a positional vote, weaker than a coach override.
ASSIGN_SUB_W = 3.0
ASSIGN_SUB_BEFORE_S = 30.0     # sub logged late → look back
ASSIGN_SUB_AFTER_S = 45.0      # kid takes a while to actually enter/exit
ASSIGN_SUB_TOUCHLINE_M = 5.0   # "near the touchline" margin
ASSIGN_W_ONFIELD = 1.0         # weight: on-field-window overlap (lineup+subs, tolerant)
ASSIGN_GK_BONUS = 3.0          # (legacy) GK now handled separately, not via bonus
ASSIGN_MATCH_MAX_FRAC = 0.55   # reject a tracklet↔player window-match beyond this
                               # fraction of field length (kills far-fetched votes)
ASSIGN_MINUTE_SLACK = 1.5      # per-player budget = coach-logged minutes + this
# How far OUTSIDE his coach-logged on-field window a player stays eligible for a
# tracklet during assignment (identity_assign: per-window candidate gate and the
# per-tracklet vote filter). The historical value is 240 s — four minutes — which
# at U10 sub intervals leaves nearly every squad member eligible in nearly every
# window, so the coach's SUB log barely constrains the decision where it is made.
# The log then bites AFTERWARDS, at exact boundaries, in stats._drop_offwindow,
# which deletes ~30% of attributed detections as the wrong child. Lowering this
# moves the log from a post-hoc filter to a real assignment constraint.
# Default 240.0 reproduces the previous behaviour exactly (no-op).
ID_ONFIELD_TOLERANCE_S = float(os.environ.get("ID_ONFIELD_TOLERANCE_S", "240.0"))

# --- Sub-tap slack (post_game/sub_slack.py, applied to stats' on-field windows)
# The other half of the asymmetry described just above: the assigner tolerates
# 240 s of error in the SUB log while stats._drop_offwindow tolerates ZERO, and
# deletes anything outside the logged window. The coach taps a mass rotation one
# child at a time, so those timestamps are only as good as the rotation was fast.
# Measured on mri01pvelv46d: 5 of 8 substitution moments involve 3+ players, taps
# within a moment span 80 s median / 117 s worst; and 96% of the 20.7% of
# detections the filter deletes sit within 180 s of a substitution moment
# (median 58 s) — a signature of tap lag, not of the tracker following the wrong
# child, which would be spread evenly through the game.
# A flat tolerance can't fix this: 240 s (matching the assigner) drops 0.1%
# instead of 20.7%, i.e. it switches the filter off. So each boundary is widened
# by ITS OWN substitution moment's tap spread — clean single taps stay tight and
# keep catching real misattribution; messy rotations get exactly as much room as
# they were messy. Default ON; SUB_SLACK_ENABLED=0 restores the old behaviour.
SUB_SLACK_ENABLED = os.environ.get("SUB_SLACK_ENABLED", "1") != "0"
# Floor applied to every boundary — covers ordinary reaction time on a clean tap.
SUB_SLACK_BASE_S = float(os.environ.get("SUB_SLACK_BASE_S", "20.0"))
# Taps closer together than this are one substitution moment (one rotation).
SUB_SLACK_CLUSTER_GAP_S = float(os.environ.get("SUB_SLACK_CLUSTER_GAP_S", "90.0"))
# Ceiling on the spread credited to any one moment, so a pathological log can't
# widen a window until the filter stops filtering.
SUB_SLACK_MAX_S = float(os.environ.get("SUB_SLACK_MAX_S", "150.0"))

# --- Kit-hue team vote (pipeline stage 2 -> 4) --------------------------------
# team_classifier.sample_jersey_hsv drops the grass band (35<=H<=85, S>60, V>50)
# to stop pitch pixels dominating a small player's ROI. Our kit #16a34a is H71
# S221 V163 — INSIDE that band — while the opponent's #2563eb is H110, outside
# it. The filter is therefore asymmetric by construction: it deletes exactly one
# team's defining colour, and when the drop removes almost everything the
# fallback returns the unfiltered ROI, so a green player ends up characterised
# by grass, skin and shorts. Measured consequence: the classifier splits
# 2479 ours : 634 opp (3.9:1, 14 vs 2 bodies per frame) where 7v7 needs ~1:1.
# Deciding instead by WHICH kit hue the torso is nearer needs no grass drop at
# all; on the same frames that splits 1.11:1 with 8 ours / 7 opp per frame
# (tracking/grass_filter_probe.py). The vote has to be taken during tracking
# because it needs the video frame, not the stored post-drop samples.
KIT_VOTE_ENABLED = os.environ.get("KIT_VOTE_ENABLED", "1") != "0"
# The team owns TWO kits and they need different discriminators. Green
# (#16a34a, S221) separates from a blue opponent by hue. Black (#0a0a0a, S0)
# has no hue at all, and neither do the white (#f5f5f4, S1) and light-grey
# (#d4d4d4, S0) kits it has been played against — for those, BRIGHTNESS is the
# entire signal (V10 vs V245/V212, a huge margin). kit_vote.pick_axis reads the
# two anchors and chooses; this is the neutral dead-zone for the value axis,
# in V units, inside which a detection abstains instead of guessing.
KIT_VOTE_VALUE_MARGIN = float(os.environ.get("KIT_VOTE_VALUE_MARGIN", "25.0"))
# Tag pre-fill (Phase 3.3): suggestedPressure = an opponent within this radius
# of the assigned player at the action moment. ~3 m ≈ closing-down range at U10.
SUGGEST_PRESSURE_RADIUS_M = 3.0
                               # (a player can't own more track-time than played)
# Coach IdentityFixView: an UNASSIGNED tracklet shorter than this (span minutes)
# is a stitching fragment with no meaningful player-time — hidden from review so
# the fix list stays a few dozen cards, not ~400. Assigned tracklets always show.
TRACKLET_REVIEW_MIN_MINUTES = 1.0
# Refs/coaches/spectators next to the touchline 360 cam get mis-classified as our
# team by jersey color (dark clothing ≈ black kit) and clutter the review list.
# They live OFF the pitch in field coords, so drop UNASSIGNED tracklets that spend
# less than this fraction of their detections within the field (± margin metres).
# Assigned (coach-log-matched) tracklets are never dropped.
TRACKLET_REVIEW_ONPITCH_FRAC = 0.6
TRACKLET_REVIEW_ONPITCH_MARGIN_M = 3.0

# --- Stats ---------------------------------------------------------------

SPRINT_THRESHOLD_MS = 4.5                        # m/s; U10 sprint = ~16 km/h (fallback for new players)
# Personalized sprint threshold (plan 4.5): per player,
# max(FLOOR, FRAC × own season p99 speed) from prior analytics docs. Robust
# input: MEDIAN of per-game top_speed_ms (each already a p99), dropping
# cap-pinned games (≥95% of MAX_PLAUSIBLE_SPEED_MS = identity-swap pollution).
SPRINT_PERSONAL_FLOOR_MS = 4.0
SPRINT_PERSONAL_FRAC = 0.8
SPEED_SMOOTH_WINDOW = 5                          # samples (≈0.5s at SAMPLE_RATE=3)
# Physical sanity ceiling. No U10 outfield player exceeds ~9 m/s (~32 km/h);
# anything faster is an identity-swap teleport, not real motion. Used to clamp
# per-step displacement so absurd top speeds (6000+ km/h) and teleport-inflated
# distances can't occur.
MAX_PLAUSIBLE_SPEED_MS = 9.0                      # ~32 km/h
# Cap on the distance_est_m extrapolation multiplier (coach_min / tracked_min).
# The rate-based estimate scales the tracked slice up to full coach-logged
# minutes; at low coverage that multiplier explodes (a 19%-coverage player would
# be blown up ×5.3) and — because low-coverage tracks are activity-biased (the
# tracker keeps a player while they're moving) — the extrapolation is inflated,
# not just noisy. Cap it so a thin sliver yields a conservative estimate instead
# of a fabricated headline. Binds only below ~50% coverage; above that it never
# fires. 2.0 = "never claim more than double the tracked distance".
DIST_EST_MAX_MULT = 2.0

# Below this tracked/logged coverage fraction, don't extrapolate distance or
# sprints AT ALL — report the real tracked slice and flag it. The MAX_MULT cap
# above bounds how far a rate is stretched, but a rate measured on a thin,
# activity-biased sliver is unreliable at any multiplier: at 20% coverage we'd be
# characterising a whole game from a few minutes the tracker happened to hold,
# which skew fast. Reporting a smaller honest number beats publishing a
# confident-looking projection the data cannot support. `dist_est_capped` is set
# so the UI marks these indicative rather than measured.
DIST_EST_MIN_COVERAGE = float(os.environ.get("DIST_EST_MIN_COVERAGE", "0.25"))

# Boxcar width (samples) applied to POSITION before the distance integral. Raw
# per-frame projection jitter never cancels — it only adds path length. Measured
# on W8 over 7,121 near-stationary 2 s windows: true net displacement 0.18 m but
# summed path 1.27 m, a 7.2x over-count. Calibrated by sweeping the window against
# TWO opposing tests — phantom on standing players (want -> 0.18) and path/net on
# genuinely fast runs (want -> 1.0, i.e. real sprints keep their length):
#     win   standing   fast path/net   total
#       1     1.274 m      1.328x      86.4 km   <- today
#       5     0.530 m      1.047x      54.8 km
#       7     0.433 m      1.033x      51.7 km   <- chosen; returns flatten here
#      11     0.333 m      1.020x      48.4 km
# 7 removes ~2/3 of the standing-still phantom while costing real motion almost
# nothing. >=15% of the distance total is provably phantom; the total drop is
# larger and part of that excess IS real direction change, so claim only the 15%.
DIST_POS_SMOOTH_WINDOW = int(os.environ.get("DIST_POS_SMOOTH_WINDOW", "7"))
# Never smooth across a gap longer than this — bridging unobserved time would
# invent a straight-line path the player never walked.
DIST_POS_SMOOTH_MAX_GAP_S = float(os.environ.get("DIST_POS_SMOOTH_MAX_GAP_S", "0.5"))

# Minimum goal-occupancy ratio between the two ends for the pitch-orientation
# anchor to be considered decided. Below this, heatmaps/thirds are flagged as
# possibly mirrored rather than presented as fact. On W8 the decisive half scores
# 2.00x (a body in front of one goal for 1474 of 1505 s) while the other half is
# only 1.19x — hence anchoring on the confident half and alternating.
ORIENT_MIN_CONFIDENCE = float(os.environ.get("ORIENT_MIN_CONFIDENCE", "1.5"))

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
ANALYTICS_DOC_VERSION = os.environ.get("ANALYTICS_DOC_VERSION", "v1")  # bump if schema breaks; env-override for shadow A/B runs

R2_BUCKET = os.environ.get("R2_BUCKET", "stompers-videos")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")  # set in env, never committed
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "")

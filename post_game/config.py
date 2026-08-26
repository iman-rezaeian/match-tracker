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

# --- Render speed (2026-08-25 — the Aug-9 full run took ~12 h wall) -------
#
# Four multipliers stacked on every reel frame: software 8K HEVC decode, a
# full trig warp-map rebuild, a Lanczos4 resample, and a preset=slow software
# x264 encode — all on one thread. Each knob below removes one and is
# env-overridable back to the old behavior.

# Hardware-accelerated DECODE for every cv2.VideoCapture (VideoToolbox via
# OpenCV's ffmpeg backend). Read at capture-open time, so setting it here
# covers stage-2 detection AND the reel/highlight renders. "0" disables.
if os.environ.get("DECODE_HWACCEL", "1") != "0":
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "hwaccel;videotoolbox")

# Reel/clip ENCODER. ⚠ QUALITY IS THE COACH'S EXPLICIT PRIORITY here (stated
# 2026-08-25): the reel is a small slice of the sphere blown up to 1080p, so
# crf 18 + preset slow were deliberately raised from 23/veryfast — the encoder
# and resample stay at maximum quality BY DEFAULT and speed comes from the
# quality-neutral knobs below. "videotoolbox" (Apple media engine, ~2x encode
# throughput, needs ~TV_VT_BITRATE≈20M to visually match crf18) is opt-in.
TV_ENCODER = os.environ.get("TV_ENCODER", "x264")
TV_VT_BITRATE = os.environ.get("TV_VT_BITRATE", "20M")

# Warp resample: Lanczos4 — the coach's deliberate sharpness choice (the crop
# is enlarged from few source pixels; bilinear visibly softens it). "linear"
# exists for experiments only.
TV_REMAP_INTERP = os.environ.get("TV_REMAP_INTERP", "lanczos")

# Warp maps are smooth fields — compute them at 1/N resolution and upscale
# (subpixel error, invisible), cutting the per-frame trig cost ~N².
# 1 restores the per-pixel rebuild.
TV_MAP_SCALE = int(os.environ.get("TV_MAP_SCALE", "4"))

# Run the jersey-VLM identity pass CONCURRENTLY with the reel render: VLM is
# network-bound (CPU idle) and the render is compute-bound, so overlapping
# them hides the shorter stage entirely. "0" restores sequential order.
VLM_OVERLAP_RENDER = os.environ.get("VLM_OVERLAP_RENDER", "1") != "0"

# Prefetch decoded frames on a reader thread during reel/highlight renders,
# overlapping the ~28 ms/frame 8K decode with the warp+encode of the previous
# frame (cv2 releases the GIL inside read()). Quality-neutral — same frames,
# same order. "0" restores the serial decode→warp→write loop.
TV_DECODE_PREFETCH = os.environ.get("TV_DECODE_PREFETCH", "1") != "0"

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

# Detector weights. `yolo11s` is the second-SMALLEST of the family
# (n < s < m < l < x) and was never chosen on evidence — it arrived with the
# first commit as a default and stayed.
#
# Measured 2026-08-07 by replaying 150 frames where the tracker lost a player
# and asking which detector still finds a body there (tracking/detector_bakeoff.py,
# Wilson 95% intervals):
#
#     yolo11s (current)  72%  [64, 79]   191 ms/frame
#     yolo11m            75%  [67, 81]   248        <- inside the noise
#     yolo11x            87%  [81, 92]   576        <- SEPARATED, real at 95%
#     11s + 5 tiles      83%  [77, 88]   295        <- inside the noise
#     11m + 5t + 1600px  86%  [80, 91]   481        <- SEPARATED
#
# Only two arms clear the baseline's interval, and they are indistinguishable
# from each other, so take the simpler one: a bigger model changes one line,
# while the tile-geometry arm perturbs rendering that later stages depend on.
# Those misses are NOT occlusion — the nearest other body is over 4 m away 43%
# of the time — which is why raw model capacity moves the number at all.
#
# Env-overridable so a validation run can A/B without a code edit. Fingerprinted
# in pipeline._TRACKING_CONFIG_KEYS, so switching invalidates the Stage-2 cache.
# NOT yet the default: 87% recall on known misses is measured, but the false
# positives a larger model also finds are not, and yolo11x costs ~3x the runtime
# (~2 h -> ~6 h per game).
YOLO_MODEL = os.environ.get("YOLO_MODEL", "yolo11s.pt")
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

# Association algorithm. Until 2026-08-09 this was a bare literal that nothing
# read — `Tracker.__init__` hardcoded BotSort — so the knob documented three
# options and delivered one, and "try another tracker" looked done while never
# having been run once. Now wired, env-overridable, and fingerprinted.
#
# It matters because a sweep of every BotSort knob (thresholds, buffer, heading,
# appearance) came back inert against a 5.7 s median track lifespan. Those are
# results about BotSort's tuning, not about association in general.
#
# boxmot 11.0.5 offers: botsort, bytetrack, deepocsort, hybridsort, imprassoc,
# ocsort, strongsort — all installed, no new dependency.
#
# ⚠ THE SWEEP HAS NOW RUN, AND BOTSORT WON. Six arms on one 15-min window of
# Game 1, detector and window held fixed, median track lifespan as the objective:
#
#     BotSort 6.0 s | OcSort 3.7 s | DeepOcSort 3.7 s | ByteTrack 3.4 s
#     ImprAssoc 0.1 s (10,451 tracks — shattered, not better)
#
# So leave this on botsort unless testing something specific. Read the teleport
# column with care if you re-run it: three arms showed large teleport REDUCTIONS
# that were all illusory — ImprAssoc by shattering tracks too short to teleport,
# OcSort and DeepOcSort by carrying 16 bodies/frame instead of 20. Judge on
# lifespan and same-person-pairs-left, with teleports only as a weld guard.
#
# Incidentally: DeepOcSort is OcSort PLUS appearance and the two are
# indistinguishable here (3.7 s, ~1,344 tracks), which is a third independent
# confirmation that OSNet appearance is inert on identical kits.
TRACKER_TYPE = os.environ.get("TRACKER_TYPE", "botsort")
REID_WEIGHTS = "osnet_x0_25_msmt17.pt"
# How long a lost track is kept alive for re-acquisition. Env-overridable so a
# tuning sweep can vary it without a code edit (a long buffer lets a lost track
# linger and re-acquire the WRONG same-kit body once it reappears).
TRACK_BUFFER_S = float(os.environ.get("TRACK_BUFFER_S", "20"))
# BoT-SORT association thresholds. These were literals inside Tracker.__init__
# until a Stage-2 audit found they disagree with the detector about what counts
# as a person: DETECT_CONFIDENCE lets YOLO emit boxes down to 0.30, but a track
# needs 0.50 to be created and 0.45 to associate in the high-confidence round.
# Measured on mrhvbvwi1gjpn by replaying the frame after each mid-field track
# death (tracking/death_replay_probe.py): YOLO still saw the body 56% of the
# time, and of those boxes 36% scored below 0.50 and 29% below 0.45 — detected,
# but invisible to the tracker for the purpose of resuming an identity.
#
# Defaults are exactly the previous literals, so lifting them into config is a
# no-op. Env-overridable so a smoke sweep can A/B them without a code edit; all
# four are fingerprinted in pipeline._TRACKING_CONFIG_KEYS, so changing one
# invalidates the Stage-2 cache instead of silently reusing it.
TRACK_HIGH_THRESH = float(os.environ.get("TRACK_HIGH_THRESH", "0.45"))
TRACK_NEW_THRESH = float(os.environ.get("TRACK_NEW_THRESH", "0.50"))
TRACK_LOW_THRESH = float(os.environ.get("TRACK_LOW_THRESH", "0.10"))
# Let the low-confidence second association round also revive LOST tracks
# (default OFF — see post_game/tracking._RescuingBotSort for what upstream does
# and why this exists). Measured: 99.3% of bodies reappear within 2.0 s of a
# track death while TRACK_BUFFER_S keeps the track alive for 20 s, so the
# evidence to re-associate is present and upstream declines to use it.
TRACK_RESCUE_LOST = os.environ.get("TRACK_RESCUE_LOST", "0") != "0"
# Re-ID appearance gate, and a switch to drop appearance entirely.
#
# OSNet is trained on adult pedestrians in varied clothing. On 14 same-kit U10s
# it has almost nothing to separate: measured on mrhvbvwi1gjpn over tracks that
# are PROVABLY different children (alive in the same frame), the median cosine
# distance is 0.271 and **42.5% score below the 0.25 match threshold**. So on
# nearly half of confusable pairs appearance does not merely fail to help, it
# actively votes for the wrong child — which is the likeliest reason the
# id-switch rate stayed pinned at 100% across a full threshold sweep.
#
# Geometry, by contrast, is healthy: only 5.4% of true same-player consecutive
# pairs fall below `proximity_thresh=0.5`. TRACK_APPEARANCE=0 tests whether the
# tracker does better on clean motion evidence than on motion plus a coin flip.
# Defaults preserve today's behaviour.
# Drop confidently-OPPONENT detections BEFORE the tracker sees them.
#
# The tracker currently associates over every body on the pitch — our 7, their
# 7, the ref, and whoever is standing near the touchline — so roughly half its
# candidates are people who provably cannot be our player. Team was only ever
# used as a LABEL applied after tracking (classify_from_kit_votes runs in stage
# 4), never as a CONSTRAINT on association, so the tracker makes all of its
# wrong-child decisions with the opponent still in the pool.
#
# Measured: detections with another body within 2 m drop from 11.5% to 6.0%
# (mrhvbvwi1gjpn) and 11.0% to 4.2% (mri01pvelv46d) once the opponent is
# removed. Unlike every threshold swept, this does not trade fragmentation
# against wrong merges; it deletes candidates that are not our player at all.
#
# AMBIGUOUS detections are KEPT, never dropped. A single frame's vote is
# decisive on 59-73% of tracks but sits in the middle on 10-17%, and deleting a
# real player ends their track outright — strictly worse than leaving a
# confusable body in the pool. The per-track tally in stage 4 still gets the
# final say on anything unclear.
#
# *** DEFAULT OFF as of 2026-08-08. THIS FILTER DELETES OUR OWN PLAYERS. ***
#
# It was default ON for one day. On a VALUE-axis game it removed roughly a third
# of our own team, every frame, because the vote runs at the wrong time:
#
#     during tracking   pre-filter votes with the RAW kit hexes  <- wrong line
#     after tracking    fit_value_anchors derives the real line  <- too late
#
# A `#0a0a0a` shirt is V10 as fabric but photographs at V~145 in July sun, so the
# hex midpoint lands at 98 and our own players sit ABOVE it — read as opponents
# and deleted. `fit_value_anchors` exists precisely to fix that (it derives 131
# from the footage) but runs at stage 2b, after tracking, where it can only
# re-label survivors. A deleted detection cannot be recovered.
#
# Replaying 2158 real on-pitch detections through the production call, game 1:
#
#     box height     ours +1    opp -1    unknown
#     <40 px             10%       83%         7%
#     >200 px            59%       34%         8%
#     ALL                27%       68%         5%    <- a 7v7 is ~1:1
#
# Game 2 (HUE axis, green vs blue) votes 49/47 with no size gradient, so hue
# games are unaffected — hue is roughly illumination-invariant and needs no
# fitted anchors. The bug is value-axis only, i.e. whenever our kit is dark.
#
# Blast radius, game 1 window 720-1518 s, filter OFF vs ON:
#
#     bodies/frame              18  ->  7
#     median track lifespan    6.0s -> 2.1s   (the "composition" caveat this
#                                              comment used to carry was the bug)
#     pitch thirds own/mid/far  .28/.35/.38 -> .44/.35/.22
#     far-goal occupancy       100%  -> 77%
#
# The -66.5%/-52.8% teleport reduction once claimed here is REAL PER MINUTE
# (289/min -> 77/min) and worthless as an association result: it prevents swaps
# by deleting the players you would swap between. Any intervention that reduces
# confusion by reducing bodies must be scored per-BODY, not per-minute.
#
# Do not simply re-enable this. The replacement is TRACK_TAG_OPPONENTS below,
# which tags instead of deleting and prunes at TRACK level after the anchors are
# fitted. See memory `opponent-filter-value-axis-bug` and the preserved
# before/after caches in post_game/outputs/_prefilter_evidence/.
TRACK_DROP_OPPONENTS = os.environ.get("TRACK_DROP_OPPONENTS", "0") != "0"
# Tag opponents instead of deleting them: the replacement for the filter above.
#
# Three changes, each fixing one thing that broke the drop version:
#
# 1. ORDER. Nothing is removed during tracking. The vote is recorded per
#    detection and the pruning happens at stage 2b, AFTER `fit_value_anchors`
#    has derived the real threshold from the footage. The correct line exists
#    before anything is discarded.
#
# 2. GRANULARITY. It prunes whole TRACKS, not detections. A track is ~100 looks
#    at one child; a detection is one glance. Voting across a track survives a
#    shadow, a turn, or a bad crop that would flip a single frame — the same
#    reason DROP_NEVER_ONFIELD works at track level and a per-detection
#    touchline test cannot. Requires a clear majority (KIT_TAG_TRACK_MAJORITY)
#    over enough votes (KIT_TAG_MIN_VOTES); anything short of that is kept.
#
# 3. AUDITABILITY. A tagged detection can be re-scored next week; a deleted one
#    is gone. The damage done by the drop version was only measurable because a
#    pre-filter cache happened to survive on disk. That was luck, and a filter
#    should not need luck to be reviewable — so this one reports what it pruned
#    as data, not as a log line.
#
# Default OFF until measured on BOTH games (black/value and green/hue). Enabling
# it changes Stage-2 output, so it is in _TRACKING_CONFIG_KEYS and will
# correctly invalidate the cache.
TRACK_TAG_OPPONENTS = os.environ.get("TRACK_TAG_OPPONENTS", "0") != "0"
# Share of a track's decisive votes that must say "opponent" before it is
# pruned. 0.8 is deliberately far above a bare majority: the cost of dropping a
# real player is their whole stint, while keeping an opponent costs one extra
# candidate in the association.
KIT_TAG_TRACK_MAJORITY = float(os.environ.get("KIT_TAG_TRACK_MAJORITY", "0.8"))
# Minimum DECISIVE votes (ignoring abstains) before a track may be pruned. A
# handful of frames is not a kit reading.
KIT_TAG_MIN_VOTES = int(os.environ.get("KIT_TAG_MIN_VOTES", "10"))
# Drop OFF-FIELD detections before the tracker sees them, for the same reason.
#
# The off-field test already exists (stage 3b) but runs AFTER tracking, framed
# as a cleanup for the stats rather than a constraint on association — so the
# tracker spends its effort following the crowd and then the result is thrown
# away. Measured: 17% (mrhvbvwi1gjpn) and 27% (mri01pvelv46d) of all detections
# handed to the tracker are off-field, and 788 / 1200 raw track ids never touch
# the pitch at all. Worse than the wasted work, those bodies sit in the
# candidate pool, so a player near the touchline can be associated with a
# spectator standing behind them.
#
# Nothing in the projection needs the tracker: a foot pixel becomes a metre
# position from the calibration alone, and the stage-2 loop already projects
# per frame. The +-1.5 m buffer is preserved exactly as stage 3b uses it, so a
# throw-in run-up or a keeper behind the goal line still survives — 10-12% of
# tracks legitimately cross the line and must not be cut.
#
# Stage 3b still runs afterwards and is then a no-op on anything this dropped,
# which keeps the two paths consistent when the flag is off.
TRACK_DROP_OFFFIELD = os.environ.get("TRACK_DROP_OFFFIELD", "0") != "0"
# Drop whole tracks that NEVER set foot on the pitch — the touchline coaches.
#
# The 1.5 m buffer is a per-detection test and cannot separate a coach standing
# half a metre outside the line from a player taking a throw-in at the same
# spot. A whole track can: the player crosses the line and comes back, the coach
# never does.
#
# CORRECTION 2026-08-08 — this comment used to claim the fraction-of-life-outside
# is "sharply bimodal with a thin valley", making `>= 1.0` a safe cut rather than
# a tuned one. That was asserted, not measured, and it is FALSE. Counting
# substantial tracks (>= DROP_NEVER_MIN_DETS) on both games:
#
#     outside-fraction    game 1    game 2
#     0.00 - 0.05           2322      2077
#     0.05 - 0.95            591       482   <- the "thin valley", 16% / 13%
#     0.95 - <1.00            79        70
#     == 1.00                781      1039
#
# The middle is populated. This IS a tuned threshold and must be justified as
# one. At 1.0 a single frame of projection noise saves a track, so ~50k
# touchline-adult detections per game escaped — including the coach at
# core-fraction 0.021 that broke a labeling run.
#
# Tuned to 0.95, which drops ~75 more tracks per game for 2 (g1) / 6 (g2) large
# ones as collateral, and "large" means near-camera, which is where the coaches
# stand. NOT lower: at 0.75 the collateral reaches 22/36 and starts eating
# tracks that genuinely enter the pitch.
#
# What it removes is genuinely off-pitch: median 2.42 m (g1) / 3.18 m (g2)
# outside the lines, only 8% / 3% within a metre of it, median span 7-8 s. A
# substitute warming up on the touchline who later comes on is NOT at risk —
# once they step on, their fraction drops below the threshold and they are kept
# by construction.
#
# They are adults: within 5-15 m of the camera their boxes run 1.61x taller than
# on-pitch players at the same distance.
#
# Applies AFTER tracking by necessity — you cannot know a track never came in
# until you have the whole track — which is fine, since sweeping a pre-tracking
# off-field cut showed it buys nothing for association anyway (tracks -1.5%,
# teleports +0.9%): distant stationary bodies were never competing for a
# player's identity. This filter is about not attributing coach minutes to
# players, not about association.
DROP_NEVER_ONFIELD = os.environ.get("DROP_NEVER_ONFIELD", "1") != "0"
# Fraction of a track's life that must be outside the lines before it is cut.
# See the tuning table above: 1.0 leaks ~50k detections/game, 0.75 starts eating
# real players. 0.95 sits between them.
DROP_NEVER_OUTSIDE_FRAC = float(os.environ.get("DROP_NEVER_OUTSIDE_FRAC", "0.95"))
# Minimum detections before the rule may fire. A 3-frame blip that happens to
# land outside is noise, not a coach, and dropping it gains nothing.
DROP_NEVER_MIN_DETS = int(os.environ.get("DROP_NEVER_MIN_DETS", "10"))

# Restrict the TEAM-SHAPE metrics (centroid, width, depth, compactness, field
# tilt) to bodies whose per-track median box height is player-sized.
#
# Only 24.8% of tracked rows on a clicked frame are one of our players; the rest
# are opponents, touchline adults and phantoms, and team shape is a function of
# the SET of bodies, so every one of them moves the number. Scored against the
# coach's clicks, the band keeps 94.1% of confirmed players while removing 40%
# of non-players (purity 24.8% -> 34.1%). See post_game/adult_filter.py for the
# full table and for why the earlier one-sided `h >= 120` version was inverted.
#
# ON by default: it is a strict improvement on both axes over the unfiltered
# metric. It applies ONLY to team aggregates — per-player stats are gated on
# identity instead, and cutting a real player's near-camera frames there would
# bias his own numbers rather than clean a shared one.
#
# ⚠ It does NOT make team shape precise. The residual is mostly OPPONENTS, who
# are exactly player-sized; 2 non-players per player survive. Directional only.
TEAM_SHAPE_SIZE_FILTER = os.environ.get("TEAM_SHAPE_SIZE_FILTER", "1") != "0"

# Penalise a candidate that sits OPPOSITE a lost track's direction of travel.
#
# The association cost is overlap-with-the-prediction and nothing else — it asks
# "how far?", never "in what direction?". For a track that has just been lost the
# prediction is already stale, so a body BEHIND the player scores as well as one
# where they were actually running. Measured on mrhvbvwi1gjpn, extrapolating exit
# velocity picks a different successor than raw distance on 17% of ambiguous
# joins, which is signal currently thrown away.
#
# 0 disables it (upstream behaviour). The weight is in the same units as the IoU
# cost (0-1), and the penalty is 0 dead ahead / 0.5 perpendicular / 1.0 directly
# behind — so 0.3 makes a fully-reversed candidate cost 0.3 more, enough to lose
# a close race without overriding strong geometry.
TRACK_HEADING_WEIGHT = float(os.environ.get("TRACK_HEADING_WEIGHT", "0"))
# Speed floor below which a track's heading is treated as noise, expressed as a
# FRACTION OF BOX HEIGHT rather than absolute pixels.
#
# The first version used 2.0 px/frame and was self-defeating: apparent speed
# scales with distance, so a far player moves 0.21 px/frame (91% of them below
# the gate) while a near one moves 1.7 (56% below). An absolute threshold
# therefore muted the heading term almost everywhere, and hardest on the distant
# players it was meant to help. Box height is the natural per-detection scale,
# so a fraction of it means the same physical speed near and far.
#
# 0.0025 is derived from the measured medians rather than guessed: a 30 px box
# moves 0.21 px/frame and a 200 px box 1.49, so a floor at one third of typical
# motion is 0.07 and 0.50 respectively — and 0.0025 hits both. (0.01, the first
# attempt at a scaled floor, still sat ABOVE typical motion at both scales and
# would have reproduced the original bug in a new coordinate system.)
TRACK_HEADING_MIN_SPEED_FRAC = float(
    os.environ.get("TRACK_HEADING_MIN_SPEED_FRAC", "0.0025"))
# Ceiling on the penalty. 24% of true continuations ARE behind the exit
# velocity — players do double back — so a reversed candidate should be
# disadvantaged, never excluded outright.
TRACK_HEADING_CAP = float(os.environ.get("TRACK_HEADING_CAP", "1.0"))
TRACK_APPEARANCE = os.environ.get("TRACK_APPEARANCE", "1") != "0"
TRACK_APPEARANCE_THRESH = float(os.environ.get("TRACK_APPEARANCE_THRESH", "0.25"))
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
# Plausible field width band. These ARE the solver's bounds too — calibration_solve
# .solve_sphere_scaled defaults w_bounds to them, so the QC gate and the optimizer
# can never drift apart (they used to be separate literals encoding one fact).
# Upper bound covers 9v9: US Youth Soccer specifies 45-55 yd (41-50 m) wide, so a
# 50 m ceiling put a legal wide pitch exactly ON the bound, where the "pinned at a
# solver bound" check then rejected it. Our 7v7 fields solve to ~31 m.
CALIB_WIDTH_MIN = 20.0
CALIB_WIDTH_MAX = 60.0
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
#
# ⚠ DEFAULTS ON, and it must stay that way. This used to default OFF (requiring
# PUBLIC_AUDIO_ENABLED=1, which was in nobody's .env), so the stage silently never
# ran — and because the publisher falls back to the original-audio URL when the
# _public file is absent, every parent-facing reel shipped with the coach's voice
# and the kids' names on it. A privacy control that is off by default is not a
# control. Set PUBLIC_AUDIO_ENABLED=0 to disable deliberately.
PUBLIC_AUDIO_ENABLED = os.environ.get("PUBLIC_AUDIO_ENABLED", "1") != "0"
PUBLIC_AMBIENCE_PATH = os.environ.get("PUBLIC_AMBIENCE_PATH", "tracking/assets/stadium_ambience.mp3")
PUBLIC_ROAR_PATH = os.environ.get("PUBLIC_ROAR_PATH", "tracking/assets/goal_roar.mp3")
PUBLIC_BED_DB = float(os.environ.get("PUBLIC_BED_DB", "-8"))     # stadium bed level (dB rel. to source) — was -20 (too dim)
PUBLIC_ROAR_DB = float(os.environ.get("PUBLIC_ROAR_DB", "-13"))  # goal-roar level — was -6 (too loud vs bed); ~8dB above bed now
# Roar placement. A real crowd reacts just AFTER the ball crosses, so the roar
# starts a beat late and rises fast.
#
# ⚠ THE LEAD USED TO BE 7 s AND MUST NOT GO BACK. That was deliberate compensation
# for not knowing when the goal actually happened: the tap was assumed to trail the
# goal, and a long fade-in was meant to "hide the timing slop" by having the crowd
# swell through the uncertainty. Once events carry exact source-video times
# (video_event_times), the slop is gone and the lead became pure error — the coach
# heard the cheer before the ball crossed. A 7 s pre-roll cannot be corrected by
# better timestamps; it has to be removed.
#
# The knob shifts the roar EARLIER, so a small NEGATIVE value starts it slightly
# after the goal — which is what a crowd does: a beat of recognition, then noise.
# Keep the fade short so it reads as a reaction, not a build.
PUBLIC_ROAR_LEAD_S = float(os.environ.get("PUBLIC_ROAR_LEAD_S", "-0.4"))  # NEGATIVE lead = start AFTER the goal
PUBLIC_ROAR_FADE_S = float(os.environ.get("PUBLIC_ROAR_FADE_S", "0.35"))  # quick rise, not a swell

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

# --- Match format --------------------------------------------------------
# Players per side, by the game doc's `format` field (written by the PWA). 7v7
# for Canadian festivals/tournaments; 9v9 for US tournaments from the 2026-27
# season. Games predating the field are all 7v7.
FORMAT_ON_FIELD = {"7v7": 7, "9v9": 9}
FORMAT_DEFAULT = "7v7"
# Extra bodies the top-N-per-frame filter must tolerate beyond the 2x on-field
# players: the referee, plus slack for a coach stepping on for an injury and for
# the brief overlap while a substitution is in progress.
TOPN_EXTRA_BODIES = int(os.environ.get("TOPN_EXTRA_BODIES", "6"))


def on_field_per_side(game_format: str | None) -> int:
    """Players per side for a format string, defaulting to 7v7."""
    return FORMAT_ON_FIELD.get(str(game_format or FORMAT_DEFAULT), FORMAT_ON_FIELD[FORMAT_DEFAULT])


def topn_per_frame(game_format: str | None) -> int:
    """Per-frame detection budget for the stage-3c top-N filter.

    2 x on-field + slack: 20 for 7v7 (unchanged from the old hardcoded literal,
    so existing caches are bit-identical) and 24 for 9v9. Measured on the two
    clean-tracked 7v7 games, RAW detections already average 22-24 bodies/frame
    with 77-95% of frames at or over 20, so this cap is load-bearing rather than
    a formality — at 9v9 a fixed 20 would silently delete real players.
    """
    return 2 * on_field_per_side(game_format) + TOPN_EXTRA_BODIES


# --- Kit-hue team vote (pipeline stage 2 -> 4) --------------------------------
# team_classifier.sample_jersey_hsv drops the grass band (35<=H<=85, S>60, V>50)
# to stop pitch pixels dominating a small player's ROI. Our kit #16a34a is H71
# S221 V163 — INSIDE that band — while the opponent's #2563eb is H110, outside
# it. The filter is therefore asymmetric by construction: it deletes exactly one
# team's defining colour, and when the drop removes almost everything the
# fallback returns the unfiltered ROI, so a green player ends up characterised
# by grass, skin and shorts. Measured consequence: the classifier splits
# 2479 ours : 634 opp (3.9:1, 14 vs 2 bodies per frame) where the two teams must
# come out ~1:1. Deciding instead by WHICH kit hue the torso is nearer needs no
# grass drop at all; on the same frames that splits 1.11:1 with 8 ours / 7 opp
# per frame (tracking/grass_filter_probe.py). The vote has to be taken during
# tracking because it needs the video frame, not the stored post-drop samples.
# NOTE: the ~1:1 target holds for ANY format (both teams field the same count);
# the absolute per-frame counts quoted above are from 7v7 games and are ~4/frame
# higher at 9v9.
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
# "Statues aren't players": a track attributed to a player that sits inside a
# ~3 m circle for 3+ minutes of PLAY time is a touchline stander / waiting sub
# / coach welded into his identity, not a child playing soccer. Measured on
# G1 Jul-12: two such tracks put 80% of Rezaeian's heatmap mass into two
# touchline cells and buried the real map. Statue tracks are excluded from the
# HEATMAP GRID only — motion metrics already self-defend (statues add ~zero
# distance), and thirds/coverage semantics stay untouched until measured.
STATUE_MIN_DURATION_S = float(os.environ.get("STATUE_MIN_DURATION_S", "180"))
STATUE_MAX_RADIUS_M = float(os.environ.get("STATUE_MAX_RADIUS_M", "3.0"))
# Track fragmentation defeats the per-track test (mean lifespan ~6 s: a
# 10-minute stander shatters into dozens of short tracks at the same spot),
# so statues are ALSO detected by SPOT: any 2 m grid cell holding 90+
# cumulative seconds of one player's dwell is a stander post — real players
# pass through a 2 m patch in seconds, dozens of times at most.
STATUE_SPOT_CELL_M = float(os.environ.get("STATUE_SPOT_CELL_M", "2.0"))
STATUE_SPOT_DWELL_S = float(os.environ.get("STATUE_SPOT_DWELL_S", "90"))
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
# Companion doc holding ONLY the keys the season view reads. It fans out over
# every finished game at once, and the full docs (420-970 KB each, ~3.4 MB of it
# `identity_assignments` it never touches) made that open to a black screen on a
# phone. See firestore_io.write_analytics_summary.
ANALYTICS_SUMMARY_DOC = os.environ.get("ANALYTICS_SUMMARY_DOC", "summary")

R2_BUCKET = os.environ.get("R2_BUCKET", "stompers-videos")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")  # set in env, never committed
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "")

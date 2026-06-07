# Spec: coach identity-correction UI (human-in-the-loop, Tier 2)

Highest-ROI no-hardware accuracy lever (see PLAYER_ID_RESEARCH.md). The coach-log
auto-assignment already gets ~80–90%; this lets a coach fix the few residual
outfield swaps in minutes, getting per-player heatmaps/distance/speed to
near-perfect. Reuses the stitched tracklets + per-card confidence we already have.

## Principle
Review **stitched tracklets** (a few dozen our-team tracklets), NOT raw tracks
(hundreds) and NOT the dead "295 review" list. Sort worst-confidence first; coach
reassigns each to the correct roster player (or "not our team"). Corrections are
stored per-game and **re-applied on every pipeline re-run** so they compound and
survive re-processing.

## Scope decision: PER-GAME (not cross-game)
Identity assignment + corrections are **per-game**, and should stay that way:
- Tracks/tracklets are inherently per-game (the tracker resets each game; IDs don't
  relate across games), and the lineup/subs/POSITION ground truth is per-game — each
  game has everything it needs to assign itself.
- Appearance is NOT stable across games for youth (kit colors change per game,
  weather/lighting/growth) — a cross-game Re-ID gallery would add errors, not remove
  them. So do NOT build a cross-game identity/appearance model.
- The only cross-game-stable identifier is the **jersey number**, already in the
  roster (`player.number`) → a direct lookup if/when 8K + OCR lands. No global model
  needed.
- "All games" belongs only to a **season stats rollup** (sum each game's per-player
  analytics by `player_id`) — a separate feature that sits ON TOP of per-game
  identity; it does not need cross-game identification. Corrections therefore live on
  each game doc (`identityOverrides`) and never span games.

## Architecture (respect the existing split)
The PWA is **read-only** over Firestore and has no track data, so it can't
recompute stats client-side. So: PWA writes *correction intents*; the Mac pipeline
*applies* them on a (fast, cached) re-run. Two-part build.

### Part 1 — Pipeline emits reviewable tracklets + accepts overrides
`post_game/`:
1. **Per-tracklet record** in the analytics doc (`analytics/v1`): add
   `tracklets: [{ tracklet_id, player_id, confidence, status, minutes,
   t_start_s, t_end_s, thumb_url }]` — aggregate the existing per-track
   `identity_assignments` by `breakdown.tracklet` (we already store that).
2. **Representative thumbnail per tracklet** → R2. In a new light stage (or piggyback
   on stage 2/7b), for each our-team tracklet pick the sharpest, largest, most
   frontal detection, crop it from the source frame, upload to
   `tv_view/<game>/tracklets/<tracklet_id>.jpg`, set `thumb_url`. (~dozens of small
   JPEGs; cheap. Orientation/sharpness score = simple gradient/bbox-height heuristic.)
3. **Apply overrides** in `identity_assign.py`: read `game.identityOverrides`
   `{ <tracklet_id>: <player_id|null> }`; for any overridden tracklet, FORCE that
   assignment (player_id, confidence=1.0, status="coach") and skip it in the
   greedy/budget loop; `null` → drop (not our team). Overrides win over auto. This
   makes corrections idempotent across re-runs.
4. Re-run is fast: `--reuse-tv-reel` reuses the reel, stages 2 cached; only
   stats/assignment recompute (+ highlights re-render, ~15 min). A `--stats-only`
   flag that skips 7b and preserves broadcastEvents would make it ~2 min (nice-to-have).

### Part 2 — PWA correction screen (coach-only, `soccer_team_app.jsx`)
- Entry: a "Fix identities" button in the Analytics panel header (coach only).
- `IdentityFixView`: list `doc.tracklets` sorted by confidence ASC (worst first),
  filter to our-team. Each row:
  - tracklet **thumbnail** (`thumb_url`), current player (avatar + name + #),
    confidence badge, minutes, time range (e.g. "P2 12'–34'").
  - a **player picker** (roster dropdown/sheet) + a "Not a player / opponent" option.
  - optional: tap thumb → scrub the tv_reel to `t_start_s` to eyeball who it is
    (reuse BroadcastVideoPlayer seek).
- On change: write to game doc `identityOverrides[tracklet_id] = player_id|null`
  (merge). Show a sticky "Apply (re-run analytics)" hint with the count of pending
  corrections.
- Keep the per-card confidence badge on the main cards as the signal for "which to
  review."

### Apply loop
- Simplest: coach taps Save → overrides in Firestore → coach re-runs
  `./run_analytics.sh <game>` on the Mac (now applies overrides). Corrections persist.
- Better later: a worker/RemoteTrigger "re-run" button, or a `--stats-only` fast path.

## Optional enhancement — kickoff anchoring
Before/instead of post-hoc fixes: a one-time "tag the starters" step — on a kickoff
frame, coach taps each starter once to bind player→track; identity propagates via
stitching. Seeds the assignment with ground-truth anchors (fewer swaps to fix).
Reuses the same overrides mechanism (anchor = an override at t≈kickoff).

## Build phases
1. Pipeline: aggregate `tracklets[]` + apply `identityOverrides` (no thumbs yet) —
   unblocks correctness; test by hand-writing an override and re-running.
2. Pipeline: per-tracklet thumbnails → R2.
3. PWA: `IdentityFixView` (list + picker + save).
4. PWA: tap-thumb → reel seek; `--stats-only` fast re-apply.
5. (Optional) kickoff anchoring.

## Effort / payoff
- Phases 1–3 are the meat (~moderate). Payoff: coach fixes ~5–15 tracklets in a few
  minutes → near-perfect per-player stats at current 5.7K, no hardware, no OCR.
- Complements the offline-global-association and 8K-OCR work later; the overrides
  mechanism is reused by both.

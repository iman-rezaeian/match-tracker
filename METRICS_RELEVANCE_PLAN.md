# Individual-metric relevance — diagnosis & plan (2026-06-13)

Why per-player metrics are "off too much," what the 8K data actually shows, the
foundation-model research that frames the options, and the revised priority plan.
Companion to `ANALYTICS_IMPROVEMENT_PLAN.md` and `EIGHT_K_RETEST.md`.

## TL;DR — the bottleneck moved

At 5.7K the problem was *seeing* players (~14% tracked coverage). **At 8K,
detection is basically solved (~86% of bodies found per frame). The error in
individual metrics is now almost entirely DOWNSTREAM: fragment stitching +
identity assignment.** Spend effort on *connecting and naming the fragments you
already have*, not on better detection / ball / jersey models.

## Evidence — Game 1 (Belle River `mqcf9axlvtuyt`, 8K) funnel

From `tracks_raw.parquet` (461,069 detections) + the run log:

| Stage | Result | Read |
|---|---|---|
| Raw detection | median **15 bodies/frame** (p10 12, p90 19), 10 Hz | **~86% gross coverage** if 16 on field (76–98% over 14–18) |
| Raw tracklets | **2,887** fragments, median **14 s**; 44% < 10 s, 62% < 30 s; 886 ≥ 120 s | severe, bimodal fragmentation |
| Team classify | 2,707 tracks → 1,537 ours / 1,170 opp (`our_color=#0a0a0a`) | classifier OK |
| Stitching | 1,537 our fragments → **499 tracklets** | only ~3:1 merge |
| Identity assign | **344 tracks mapped to a player**, **1,193 unknown** | ~78% of our own fragments never get a name |

Mechanism of the 3–4× LOW distance: per-player distance is summed only over the
fragments confidently assigned to that player — a sliver of their true on-field
detections. The rest sits in the 1,193 "unknown" pile, and same-kit
mis-assignment pollutes attribution. The signal is present; we fail to connect/name it.

(Counts are pre-write from the log + raw parquet; exact per-player coverage
confirms when the analytics doc writes, after the reel render.)

## CORRECTION (within-team re-run, 2026-06-13) — identity is THE lever, not stitching

Reproduced team labels from cache (`classify_tracks` + `jersey_samples.npz`, no reel
wait) and re-ran stitching WITHIN team at precision-safe settings (gap-split, gap 5 s,
abs dist-cap 12 m): result ~**814 clean ours chains** — MORE than the pipeline's 499,
not fewer. The earlier "5–6× better" was a loose-gap + cross-team-merge artifact and is
RETRACTED. The pipeline's 499 is only lower because it keeps teleport-zombies as whole
tracks (the distance inflation). **Geometry can make ~814 CLEAN fragments but cannot
collapse them toward ~16 — only coach-log-anchored IDENTITY can** (appearance dead,
geometry precision-limited to short links). Net: stitching tweaks won't fix the metrics;
**identity assignment is #1**. Gap-split is still worth keeping (removes teleport
inflation) but only pays off coupled with identity re-linking.

## Revised plan (priority order)  [superseded by CORRECTION above — identity now #1]

1. **Gap-split, THEN pure spatio-temporal re-stitch — MEASURED, biggest lever.**
   Prototype `tracking/st_stitch_probe.py` on cached `tracks_raw.parquet` (no rerun)
   revised the hypothesis with data:
   - The raw tracking has TWO pathologies: ~1,674 dense-short fragments (~6 s each)
     AND ~891 "zombie" tracks (density <0.1) that hold 43% of tracked-time and
     **teleport between bodies** (the "⚠ INFLATED" distance source). Median
     contiguous tracked-time per raw track is only **6.4 s**; just 2 tracks exceed 300 s.
   - Endpoint-stitching the RAW tracks barely helps (861→819) — zombies overlap
     everything and can't be chained as units.
   - **Fix: (a) gap-split every track at internal gaps >1 s into clean contiguous
     runs (2887→5765 clean frags; also removes teleport inflation), THEN (b) pure
     spatio-temporal greedy stitch with NO appearance term and a looser gap.**
     Measured: gap=20 s → ~112 ours-equiv tracklets; gap=30 s → ~80 ours-equiv,
     90% of tracked-time in ~44 entities — **~5–6× fewer/cleaner than the current
     appearance-stitch's 499.**
   - Two confirmed config culprits: `STITCH_APP_WEIGHT=5.0` (useless on identical
     kits) and `STITCH_MAX_GAP_S=10` (too tight). Wire a gap-split pre-pass into
     `reid_stitch`, drop/lower the appearance weight, raise the gap to ~20–30 s.
   - PRECISION CHECK DONE (crop review `tracking/stitch_review.py` + appearance veto) —
     **count-reduction was partly false merges; do NOT ship the loose-gap setting.** Findings:
     - Appearance can't validate: OSNet embeddings so kit-dead that random cross-track
       cosine is 0.62–0.75 — can't even separate TEAMS. Confirms dropping APP_WEIGHT;
       also means no automated precision proxy exists → human review required.
     - Eyeballing worst joins: gap=20–30 s over-merges badly (light-kit↔dark-kit,
       adult-sideline↔player). Even gap=5 s shows cross-team merges + a 42.5 m "run"
       in 4.9 s allowed (distance gate `speed×gap+slack` far too loose).
     - THREE fixes before this is real: (1) stitch WITHIN team (prototype merged all
       bodies — needs team labels, which only land in the analytics doc after the reel);
       (2) add an ABSOLUTE distance cap (not just speed×gap); (3) keep the gap SHORT
       (~2–3 s) where geometry is trustworthy — long-gap bridging MUST come from the
       coach-log/identity layer, not geometry. So step 2 (identity) is REQUIRED to reach
       ~16, not optional.
     - Counts after gap-split (all bodies, ×0.57≈ours): gap2→1898, gap3→1252, gap5→724,
       gap10→367, gap20→197, gap30→143. Pick the operating point by PRECISION (within-team
       + human review), not by this count.
   - Tools built (uncommitted on dev): `tracking/st_stitch_probe.py`,
     `tracking/stitch_review.py`. Greedy one-to-one is also beatable by global min-cost.
2. **Coach-log-anchored identity + selective VLM for the hard residual.** The
   unique asset no commercial system has. Who's on the field per window (subs)
   prunes the search hard (SUB anchors already add 217 pairs — lean harder).
   Reserve a small MPS VLM (à la SoccerNet-2025 winner's LLaMA-3.2-Vision) ONLY
   for genuinely ambiguous tracklets. Appearance-only re-ID is empirically capped
   here (OSNet within-game 25–32%, cross-kit ~chance) — stop asking it to carry it.
3. **Trajectory gap-fill** within the now-longer, correctly-assigned tracks
   (constant-velocity/Kalman/spline bridge across short gaps; cap by gap length +
   plausibility; never bridge an ID-switch). Recovers within-track distance.
4. **Honest-reporting layer** (will bite fewer players once 1–3 land): empirical-Bayes
   shrinkage with strength ∝ 1/coverage (already done for the v2 score — generalize
   it); rates over *tracked* time not totals (partly done: `distance_est_m`);
   confidence-gate + uncertainty bands; distance-from-camera-aware error; prefer
   spatial/relative metrics (heatmaps, zones, thirds, field tilt, percentiles)
   that degrade gracefully over fragile cumulative absolutes.
5. **Raise raw coverage at source (later/bigger):** SAMURAI/SAM2MOT on the
   perspective crops for occlusion robustness — but detection isn't the current
   bottleneck, so this ranks below stitch/ID.
6. **Ground truth:** hand-label one near + one far player across a few windows →
   true error, the coverage threshold where metrics become trustworthy, and the
   distance-vs-error curve for the bands. Bake into a regression check.

## Step 2 SCOPE + prototype result (2026-06-13) — coach-log-anchored identity

Existing `assign_identities_v2` strands fragments via per-window 1:1 Hungarian
(~1 tracklet/player/window → ~29/30 dropped → only 344 named). Coach log for this
game is RICH: 7-a-side, 12 squad, 72 POSITION (board template/player), 25 SUB, GK
known, 25-min halves. Resolved orientation P1=(flip_d,flip_l)=(T,T), P2=(T,F).

Prototype `tracking/coach_assign_probe.py` (reuses production helpers
`_onfield_intervals`, `_player_board_positions`, `_board_to_field`; read-only, no GPU):
relax to "player accumulates many NON-OVERLAPPING fragments, gated by on-field
windows + board zone." Result on Game 1:
- **Named fragment-TIME 70%** (1340/3262 frags, 18632/26724 s) vs the pipeline's
  344 named tracks — the coverage lever WORKS, data supports good per-player coverage.
- BUT **46% of named time is AMBIGUOUS** (2nd-best board template within 3 m of best),
  and several players show >100% coverage (Hassoun 111%, Cardoso 101%) = over-assignment.
  Static board template only separates by ZONE, can't disambiguate same-zone roamers.
  GK (Garland) is clean (deep template distinct, 14% ambiguous).
- DESIGN CONCLUSION: template-nearest is not enough. Real algorithm = **anchor-and-
  propagate**: anchor fragments with HIGH-confidence signals (GK geometry; action-event
  votes from GOAL/ASSIST/SAVE giving player+time+place; board template only where margin
  is high), then PROPAGATE identity along spatio-temporal continuity chains (the ST
  links), with on-field + no-overlap as hard constraints. Continuity carries identity
  through zone-ambiguous stretches the template can't resolve. Validate precision via
  `stitch_review.py`-style crops on a sample + a few hand-labeled players.

ITER 2 DONE (`tracking/coach_assign_anchor_probe.py`): chains(814)+anchors(uniquely-
closest template margin>=6m, GK geometry)+propagate along continuity. Named time 66%,
split: ~47% ANCHOR-backed + ~20% PROPAGATION-rescued (template couldn't place, chain-
mate did = the win) + ~33% still shaky template-fill. GK + distinct roles clean
(Garland 88% anchor, Hahn 72%, Hassoun 86%); clustered central players stay fuzzy
(Yaacoub 0% anchor, Zaidan 10%). Residual over-assignment persists (Hassoun 110% cover).
CONCLUSION: anchor-and-propagate recovers a ~67% CONFIDENT core but ~1/3 of attribution
is IRREDUCIBLY uncertain with available signals (board template can't separate clustered
players; no number/appearance tiebreak). => confidence-gated reporting is MANDATORY:
roll per-fragment anchor-backing into a per-player-metric confidence that drives
shrinkage + gating (report Garland/Hahn/Hassoun confidently; flag/widen Yaacoub/Zaidan).
Further levers (diminishing): action-event anchors (only ~15 events, temporal-only,
marginal); the bigger one is LONGER continuity chains (better tracking) so propagation
reaches more fragments from each anchor.

## Deep-research summary (foundation/DL/LLM landscape, 2024–2026)

107-agent deep-research pass, 24 sources, 25 claims adversarially verified.

- **No end-to-end "video → all analytics" model exists.** The SoccerNet 2025 Game
  State Reconstruction *winner* (KIST-GSR, GS-HOTA 63.90) is a MODULAR stack
  (YOLO-X + Deep-EIoU/OSNet + keypoint calibration + LLaMA-3.2-Vision identity) —
  nearly our pipeline's shape plus a VLM identity stage. Trajectory: better
  modules, not one model. (arXiv 2508.19182)
- **Auto-calibration to kill the 13-click step:** PnLCalib / "No Bells, Just
  Whistles" ship runnable pretrained weights (HRNetV2 keypoint+line heatmaps →
  Points-and-Lines optim). Highest-effort-to-payoff UX win — but broadcast/pinhole-
  trained, OOD for equirect ⇒ run on perspective crops, likely fine-tune on own
  footage. (github mguti97/PnLCalib, /No-Bells-Just-Whistles). TVCalib's
  "eliminates manual" framing was REFUTED (1-2). NOTE: not an accuracy fix (RMS
  already sub-meter); it's UX.
- **SAM2 tracking:** SAMURAI (training-free, motion-aware memory) + SAM2MOT (SOTA
  occlusion, DanceTrack HOTA 75.8). SAM2 fails on RAW equirect (PanoSAM2 fixes) but
  our perspective crops neutralize that. Open risk: MPS throughput + small far players.
- **Pose:** RTMPose (pragmatic, top-down, consumes our YOLO boxes — but "90+ FPS"
  is x86 ONNX, needs CoreML/ONNX for MPS); ViTPose (100M–1B, dated 2022); Sapiens
  (most capable, multi-task orientation cues, but 1.169B params heavy + CC-BY-NC-4.0
  non-commercial).
- **Recurring blocker:** equirect domain gap on every surveyed model. **Our coach
  event log is a unique weak-supervision prior none of them exploit** — the most
  defensible place to invest.
- **Caveats:** benchmark speeds are NOT MPS numbers; some 2026 sources (PanoSAM2)
  self-reported/unreproduced; SoccerNet winner already surpassed (SoccerMaster
  GS-HOTA 64.1). Respects shelved limits (ball imaging, jersey-OCR, cross-kit re-ID).

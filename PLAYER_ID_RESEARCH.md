# Player identification — SOTA research & options

Goal: accurately identify players for per-player analytics (heatmaps, distance,
speed) from our setup — a single fixed 360° camera (5.7K → 8K), ~72 px median
youth players in identical kits, plus a rich coach event log. Researched 2026-06.

## Framing: this is "Game State Reconstruction" (GSR)
What we're building has an academic name. The **SoccerNet GSR** task = track +
identify players/role/jersey/team from a single camera onto a pitch minimap. The
winning 2024 system and the 2025 SOTA ("From Broadcast to Minimap") use exactly:
detection → pitch calibration → tracking + **Re-ID + orientation + jersey-number
+ team/role** fused to a minimap. **Our pipeline already implements most of this**
(detect, BoT-SORT track, OSNet Re-ID stitching, team-color, pitch projection) and
adds a **coach-log prior + minute budget** that academic single-cam systems don't
get. So we're not missing a category — we're at the hard residual everyone hits:
**separating identical-kit teammates who share zones.**

## Technique landscape, by signal
- **Tracking / association** — sports-tuned trackers (SportsMOT methods, BoT-SORT/
  OC-SORT/Deep-OC-SORT, DCTracker). Biggest non-hardware lever: **offline global
  association** (min-cost flow / graph-hierarchy linking) over the whole game using
  appearance + motion + coach-log + the hard constraint "≤1 track/player at a time,
  exactly N on field" — the principled version of our greedy minute-budget.
- **Appearance Re-ID (have: OSNet)** — great for stitching ONE player's fragments;
  fundamentally weak at telling identical-kit teammates apart (low inter-class
  variance). Sports-ReID fine-tuning / pose-guided helps marginally.
- **Jersey-number recognition** — the canonical "who". SOTA = keyframe selection +
  temporal/transformer aggregation over a tracklet, uncertainty-aware reads.
  Resolution-bound → not viable at 5.7K (see JERSEY_OCR_FEASIBILITY.md); revisit at 8K.
- **Orientation / quality gating** — predict player orientation; only trust the
  number/appearance when facing the cam + sharp. Cheap, high-leverage add-on.
- **Sensor fusion (wearables)** — GPS/GNSS or UWB vests give a unique per-player ID
  → definitive identity + accurate distance/speed/HR independent of video res, fused
  to video spatio-temporally. What pro/academy teams use; consumer youth options
  (PlayerData, SPT, Catapult One).
- **Human-in-the-loop** — anchor at kickoff + a coach correction UI fixes the few
  swaps. Near-perfect for minutes of effort. Standard in commercial tools.

## Best options for us, ranked
1. **Definitive — wearable GPS** (if kids will wear them): identity becomes a
   hardware fact AND physical stats beat any video method. Video still gives the
   reel + tactics. Biggest accuracy unlock; barrier is cost/parental buy-in.
2. **Best no-hardware, do now — human-in-the-loop on stitched tracklets**: coach
   reviews the few dozen low-confidence tracklets (not 295 raw tracks) and drag-fixes
   them; the coach-log prior already does 80–90%. Highest ROI at 5.7K today.
   → spec in `PLAYER_ID_CORRECTION_UI.md`.
3. **Engine upgrade — offline global association** with game constraints (min-cost
   flow / graph hierarchy). Pure software; reduces swaps; complements 2 & 4.
4. **8K + jersey-OCR tiebreaker** — keyframe + temporal transformer, uncertainty-
   aware, orientation-gated, layered on the coach-log prior (not standalone).

Stack any of these on the coach-log prior — that prior is our edge over academic
single-camera systems.

## Sources
- SoccerNet GSR (CVPRW'24): https://arxiv.org/abs/2404.11335 · code https://github.com/SoccerNet/sn-gamestate
- From Broadcast to Minimap, SOTA GSR (2025): https://arxiv.org/abs/2504.06357
- SportsMOT: https://ar5iv.labs.arxiv.org/html/2304.05170
- Long-term tracking w/ graph hierarchies (2025): https://arxiv.org/pdf/2502.21242
- Jersey number from low-res broadcast (keyframe + spatio-temporal): https://ar5iv.labs.arxiv.org/html/2309.06285
- Single-stage uncertainty-aware jersey recognition (CVPRW'25): https://openaccess.thecvf.com/content/CVPR2025W/CVSPORTS/papers/Grad_Single-Stage_Uncertainty-Aware_Jersey_Number_Recognition_in_Soccer_CVPRW_2025_paper.pdf
- DCTracker (dual-view soccer MOT): https://www.sciencedirect.com/science/article/abs/pii/S0950705124011626
- Optical tracking in team sports (survey): https://arxiv.org/pdf/2204.04143

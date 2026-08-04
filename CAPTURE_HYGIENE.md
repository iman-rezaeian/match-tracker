# Capture hygiene — go-forward rules for new game footage

Written 2026-08-03 after losing the original source files for all analyzed games (only stitched,
stabilized equirect MP4s remained; the raw `.insv` were gone). That loss permanently closed two
accuracy levers for those games (see `INSTA360_TRACKING_RESEARCH.md` #0 and #2). These rules keep
those doors open for future games. Reconciles with `raw-video-archival-policy` (memory).

## The capture → export → analyze → archive order (do NOT skip steps)

1. **Record on the Insta360 X5** at max resolution (8K), single sideline mount (centerline, ~3 m
   back, mast as high as practical — see below).

2. **Keep the raw `.insv` files** off the camera. These are the dual-fisheye originals. They are the
   ONLY source that supports:
   - re-exporting with **stabilization OFF** (FlowState corrupts the lon/lat→field-meters mapping
     frame-to-frame — a static calibration is invalid on stabilized footage), and
   - any future raw-fisheye → rectilinear-tile processing (`insv-stitch`/MEI path).
   A stitched, stabilized equirect MP4 CANNOT be un-stabilized or re-projected cleanly — the
   geometry is baked in. Losing the `.insv` = losing these levers forever for that game.

3. **Export to equirect with stabilization / FlowState / Horizon-Lock OFF.** In Insta360 Studio,
   360 mode ties FlowState and Horizon Lock to one toggle — turn it off. Then **verify**: a fixed
   landmark (corner flag, goalpost) must sit at CONSTANT pixel coordinates across the whole clip.
   If it drifts, stabilization is still on. This is the export the pipeline should ingest.

4. **Run the pipeline** (calibrate → Run Analysis). The calibration-quality gate
   (`per-player-coverage-diagnosis`, `calibration-quality-gate`) assumes a geometrically-static
   equirect — which only holds if step 3 was done.

5. **Archive/delete order:** per `raw-video-archival-policy`, the raw 8K equirect (~75–80 GB) can be
   deleted after verified analysis (cached tracks + reel + analytics doc are the durable ~1 GB). BUT
   **keep at least the `.insv`** (or a FlowState-OFF equirect) for any game you might re-track with a
   future/better tracker, until that tracker exists. Do NOT delete `.insv` for a game you haven't
   re-exported stabilization-OFF from. When in doubt, keep the `.insv` — it's the irreplaceable one.

## Hardware note (future capture, not code)
Raising the mast from ~5 m toward ~7–8 m is the single biggest *physical* lever for tracking: it
flattens the viewing angle (less swarm occlusion) and pushes the far touchline out of the worst
latitude-stretch band. A second X5 on the opposite sideline fully closes the far-touchline
resolution gap but adds sync + dual-calibration + fusion work. Neither fixes per-player IDENTITY on
same-kit teams — that needs jersey-OCR/coach-linking or worn pods (see research doc #5).

## What we lost (so it's on the record)
For all games analyzed before 2026-08-03, only stitched+stabilized equirect survived (or was itself
deleted). So: FlowState drift on those exports can't be corrected, and raw-fisheye reprocessing is
impossible for them. The cached `tracks_raw.parquet` + `embeddings.npz` DID survive, so the
software-side tracker rebuild (research #1) is unaffected — it runs off cached tracks.

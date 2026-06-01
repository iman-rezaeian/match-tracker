# Auto Ball Tracking — Phase 0: Validation

**Goal**: Prove the detection + smoothing pipeline can produce a watchable TV-mode video from a 360° equirectangular source, BEFORE committing to the full Modal.com serverless build.

## What this validates

| Question | Pass criteria |
|---|---|
| Can YOLOv8 detect U10-sized players in equirectangular projection? | mAP@50 > 0.6 on test frames |
| Can TrackNetV3 (or YOLO) detect the ball reliably enough? | Ball F1 > 0.6 across full clip |
| Does the smoothed `(lon, lat, fov)` stream produce TV-quality motion? | **You watch the rendered preview and judge** — this is the real test |
| Does the output JSON shape integrate cleanly with the existing Three.js player? | One-to-one mapping with `targetLon/Lat/Fov` |

## How to run

1. Open `phase0_validation.ipynb` in **Google Colab** (free tier with T4 GPU works)
2. Runtime → Change runtime type → **T4 GPU**
3. Upload a sample clip. Either:
   - **Use the provided sample**: run `./samples/fetch_sample.sh` locally (requires `yt-dlp` + `ffmpeg`) → uploads `southarmfc_x5_60sec.mp4` (60 sec of a real Insta360 X5 soccer match from 3m mount — exact mirror of our setup). Source: [SouthArm FC on YouTube](https://www.youtube.com/watch?v=-bMV7EumCAU).
   - **Use your own X5 footage**: Insta360 Studio → export 30-60 sec as equirectangular MP4
4. Run cells top to bottom
5. Notebook outputs:
   - `tracks.json` — the JSON your client would read
   - `preview_tv.mp4` — a rendered TV-mode crop video (watch this — it's the verdict)
   - `preview_compare.mp4` — side-by-side wide + TV crop for sanity check

## Decision tree after running

- **Preview looks good** → proceed to Phase 1 (Modal.com pipeline)
- **Preview is jittery but ball is mostly tracked** → tune smoothing params, re-run
- **Ball detection fails repeatedly** → fine-tune YOLO on 200 hand-labeled X5 frames (Phase 0.5)
- **Players not detected reliably** → step back; rethink approach

## Files

- `phase0_validation.ipynb` — the notebook
- `requirements.txt` — pip dependencies (Colab installs these in the first cell)
- `sample_clip_url.txt` — URL to a SoccerTrack v2 sample if you don't have X5 footage yet

## Cost

- Colab free tier: $0
- ~5-10 min runtime per 60-sec clip

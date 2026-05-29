# post_game — Post-Game Analytics Pipeline

Local-Mac (MPS) pipeline that consumes a finished game's 360° video + coach event log from
Firestore and writes per-player and team analytics back to Firestore + clips to R2.

See `/memories/repo/post-game-analytics-plan.md` for the locked plan.

## Run

```bash
# one-time setup (M-series Mac)
python3 -m venv .venv
source .venv/bin/activate
pip install -r post_game/requirements.txt

# run on a finished game
python -m post_game.cli --game-id <firestoreGameId> --field-name "Riverside Park 2"
```

## Module map

| File | Stage | Status |
|---|---|---|
| `cli.py` | Entry point | skeleton |
| `pipeline.py` | Orchestrator | skeleton |
| `firestore_io.py` | Read game/roster/field; write analytics | skeleton |
| `calibration.py` | 4-corner homography (pixel ↔ meters) | skeleton |
| `video.py` | Equirectangular → perspective crop iterator | skeleton |
| `detection.py` | YOLO11s player detection on crop | skeleton |
| `tracking.py` | BoT-SORT MOT | skeleton |
| `team_classifier.py` | KMeans on jersey color → team_id | skeleton |
| `identity.py` | Coach-log-anchored ID + OCR + face fusion | skeleton |
| `stats.py` | distance / speed / sprints / heatmaps / zones | skeleton |
| `formation.py` | Avg formation per period, compactness, width | skeleton |
| `gk_positioning.py` | GK position at each SHOT_ON/SAVE/GOAL | skeleton |
| `highlights.py` | ±15s event clip ffmpeg extraction + R2 upload | skeleton |
| `ball.py` | Phase-0 hybrid ball detector (gate test in step 6.5) | skeleton |
| `config.py` | Constants, thresholds, model versions | done |

## Firestore I/O contract

**Reads:**
- `teams/main/games/<gameId>` — events, startingLineup, gkPlayerId, gkChanges, halves, videoUrl, ourScore/oppScore
- `teams/main` — roster (jersey numbers, names, photo paths)
- `teams/main/fields/<fieldName>` — homography matrix + corner pixels + field dims

**Writes:**
- `teams/main/fields/<fieldName>` (first time only) — saved corner calibration
- `teams/main/games/<gameId>/analytics/v1` — single doc with all metrics
- `teams/main/games/<gameId>/clips/<eventId>` — R2 URL + metadata per highlight

## Step status

- [x] 1. Package skeleton (this commit)
- [ ] 2. Tap-the-corners calibration React page
- [ ] 3. Tier A pipeline end-to-end
- [ ] 4. Highlight reel generator
- [ ] 5. Coach Game Review page
- [ ] 6. Public anonymized + GK positioning
- [ ] 6.5. Ball-detection hit-rate gate
- [ ] 7. (gated) Ball tracking → possession + passes
- [ ] 8. (last) Defensive + off-ball overlays

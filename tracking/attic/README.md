# Attic — research instruments out of the operator path

Moved here 2026-08-24 during the workbench consolidation. These are one-off
labeling UIs whose experiments concluded; they still run (path bootstraps
adjusted for the extra directory level), they're just not part of analyzing a
game anymore. The live operator surface is `./run_workbench.sh`.

| app | what it was for | status of its program |
|---|---|---|
| `stitch_label_app.py` (+ `run_labeler.sh`) | label tracklet pairs same/different for the stitch ground truth | concluded — produced the Phase-0 GT |
| `composition_app.py` | tracklet composition labeling | retired — source of the withdrawn "87% mixed" figure |
| `player_gt_app.py` | blind per-player GT labeling (Tier-1 regression instrument) | dormant — re-run only if the identity pipeline changes |
| `stint_label_app.py` | stint labeling for the seed-and-follow experiment | parked with `feat/stint-following` |

Launch any of them standalone if ever needed:
`.venv-post-game/bin/streamlit run tracking/attic/<app>.py`

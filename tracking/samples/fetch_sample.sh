#!/bin/zsh
# Downloads a 60-sec sample clip from a publicly available Insta360 X5 full-match
# YouTube upload (SouthArm FC, captured at 3m mount height — exact mirror of our setup).
# Used by phase0_validation.ipynb for testing before we have our own X5 footage.
#
# Source: https://www.youtube.com/watch?v=-bMV7EumCAU
# Requires: yt-dlp, ffmpeg
#
# Usage:  ./fetch_sample.sh           # default: 30:00-31:00 @ 1440p equirectangular
#         ./fetch_sample.sh 00:45:00 00:46:00 313   # custom range + format (313 = 4K)

set -e
START="${1:-00:30:00}"
END="${2:-00:31:00}"
FORMAT="${3:-271}"   # 271 = 2560x1440 equirectangular VP9 (Phase 0 default)
OUT="southarmfc_x5_$(echo $START | tr ':' '')_$(echo $END | tr ':' '').mp4"

cd "$(dirname "$0")"
echo "Downloading $START-$END at format $FORMAT..."
yt-dlp -f "$FORMAT" \
  --download-sections "*${START}-${END}" \
  -o "tmp.%(ext)s" \
  "https://www.youtube.com/watch?v=-bMV7EumCAU"

# Find whatever ext yt-dlp picked (webm for vp9, mp4 for h264) and remux to mp4
SRC=$(ls tmp.* | head -1)
ffmpeg -y -i "$SRC" -c:v libx264 -crf 22 -preset fast -an "$OUT"
rm "$SRC"
echo ""
echo "✓ Wrote $OUT"
ls -lh "$OUT"

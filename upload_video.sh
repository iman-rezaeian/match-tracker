#!/bin/bash
# upload_video.sh — Upload a 360° video to R2 and print the public URL
# Usage: ./upload_video.sh <path-to-video> [custom-filename]
#
# Examples:
#   ./upload_video.sh ~/Downloads/test_360_h264.mp4
#   ./upload_video.sh ~/Downloads/game.mp4 festival-vs-lions-2026-05-27.mp4

set -euo pipefail

R2_ENDPOINT="https://c0ce0a0153cdf8665278ec19a0aa455a.r2.cloudflarestorage.com"
R2_BUCKET="stompers-videos"
R2_PUBLIC="https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev"
R2_ACCESS_KEY="31b570f8a7c65b2ff627d6a380985609"
R2_SECRET_KEY="23add30efb15b4f7ea8d696567b45e087a7838fd14238c9fb788ca9dae8184f3"

FILE="${1:?Usage: ./upload_video.sh <file> [filename]}"
if [[ ! -f "$FILE" ]]; then
  echo "❌ File not found: $FILE" >&2
  exit 1
fi

# Use custom filename or derive from input
KEY="${2:-$(basename "$FILE" | sed 's/[^a-zA-Z0-9._-]/_/g')}"
CONTENT_TYPE="video/mp4"

echo "📤 Uploading $(du -h "$FILE" | cut -f1) → $KEY"
echo ""

HTTP_CODE=$(curl -X PUT "${R2_ENDPOINT}/${R2_BUCKET}/${KEY}" \
  --header "Content-Type: ${CONTENT_TYPE}" \
  --aws-sigv4 "aws:amz:auto:s3" \
  --user "${R2_ACCESS_KEY}:${R2_SECRET_KEY}" \
  --upload-file "$FILE" \
  --progress-bar -o /dev/null -w "%{http_code}")

echo ""
if [[ "$HTTP_CODE" == "200" ]]; then
  PUBLIC_URL="${R2_PUBLIC}/${KEY}"
  echo "✅ Upload complete!"
  echo ""
  echo "🔗 Public URL (paste into LINK 360° VIDEO):"
  echo "   $PUBLIC_URL"
  echo ""
  # Copy to clipboard on macOS
  echo -n "$PUBLIC_URL" | pbcopy 2>/dev/null && echo "   (copied to clipboard)"
else
  echo "❌ Upload failed with HTTP $HTTP_CODE" >&2
  exit 1
fi

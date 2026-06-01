#!/bin/bash
# upload_video.sh — Upload a 360° video to R2 and print the public URL
# Usage: ./upload_video.sh <path-to-video> [custom-filename]
#
# Examples:
#   ./upload_video.sh ~/Downloads/test_360_h264.mp4
#   ./upload_video.sh ~/Downloads/game.mp4 festival-vs-lions-2026-05-27.mp4

set -euo pipefail

# Load credentials from .env (gitignored). Variable names match the rest
# of the project: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT,
# R2_BUCKET, R2_PUBLIC_BASE.
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${R2_ENDPOINT:?Set R2_ENDPOINT in .env}"
: "${R2_BUCKET:?Set R2_BUCKET in .env}"
: "${R2_ACCESS_KEY_ID:?Set R2_ACCESS_KEY_ID in .env}"
: "${R2_SECRET_ACCESS_KEY:?Set R2_SECRET_ACCESS_KEY in .env}"
R2_PUBLIC="${R2_PUBLIC_BASE:?Set R2_PUBLIC_BASE in .env}"

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
  --user "${R2_ACCESS_KEY_ID}:${R2_SECRET_ACCESS_KEY}" \
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

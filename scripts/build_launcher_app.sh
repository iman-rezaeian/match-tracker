#!/usr/bin/env bash
# Build "Match Tracker.app" — a macOS launcher for the Streamlit UI.
#
# After running this once you can:
#   • Drag the .app into /Applications  (or Launchpad)
#   • Spotlight: ⌘-Space → "Match Tracker"
#   • Pin to Dock
#
# The app opens Terminal with live Streamlit logs and launches the browser
# at http://localhost:8501. Quit the app by closing the Terminal window.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Match Tracker"
BUILD_DIR="${REPO_ROOT}/dist"
APP_PATH="${BUILD_DIR}/${APP_NAME}.app"
VENV_PY="${REPO_ROOT}/.venv-post-game/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv python not found at $VENV_PY" >&2
  echo "       create it first: python3 -m venv .venv-post-game && pip install -r post_game/requirements.txt" >&2
  exit 1
fi

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# --- Info.plist ---------------------------------------------------------
cat > "$APP_PATH/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>com.stompers.matchtracker</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launch</string>
    <key>CFBundleIconFile</key><string>icon</string>
    <key>LSMinimumSystemVersion</key><string>10.13</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# --- launcher executable -------------------------------------------------
# This shell stub opens ONE Terminal window with the streamlit command so the
# user sees live logs. Streamlit auto-opens the browser at :8501.
# IMPORTANT: the outer heredoc is quoted ('LAUNCH') so nothing expands at
# build time. We inject REPO_ROOT via sed after to keep things robust.
cat > "$APP_PATH/Contents/MacOS/launch" <<'LAUNCH'
#!/usr/bin/env bash
REPO="__REPO_ROOT__"
# do script (without "activate" first) launches Terminal AND runs the cmd in
# one window. Using `activate` first spawns an extra blank window.
/usr/bin/osascript <<APPLESCRIPT
tell application "Terminal"
    do script "cd '$REPO' && [ -f .env ] && set -a && source .env && set +a; source .venv-post-game/bin/activate && exec streamlit run post_game/ui_app.py"
    activate
end tell
APPLESCRIPT
LAUNCH
# Substitute the real repo path
sed -i '' "s|__REPO_ROOT__|${REPO_ROOT}|g" "$APP_PATH/Contents/MacOS/launch"
chmod +x "$APP_PATH/Contents/MacOS/launch"

# --- icon ---------------------------------------------------------------
# Generate a simple soccer-ball emoji icon if no icon.icns is provided.
ICON_PNG="${REPO_ROOT}/scripts/launcher_icon.png"
if [[ -f "$ICON_PNG" ]]; then
  sips -s format icns "$ICON_PNG" --out "$APP_PATH/Contents/Resources/icon.icns" >/dev/null 2>&1 || true
fi

# Refresh Launch Services so Spotlight/Launchpad pick it up immediately.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP_PATH" 2>/dev/null || true

echo ""
echo "✓ Built: $APP_PATH"
echo ""
echo "Next steps:"
echo "  1. Drag \"$APP_PATH\" into /Applications  (or just Launchpad)"
echo "  2. Spotlight (⌘-Space) → type 'Match Tracker'"
echo "  3. First launch: Right-click → Open (to bypass Gatekeeper)"
echo ""
echo "Optional: drop a 1024×1024 PNG at scripts/launcher_icon.png and re-run"
echo "this script for a custom icon."

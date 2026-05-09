#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
APP_NAME="Mac Agent OS"
PRODUCT_NAME="NativeMacApp"
CONFIG_SOURCE="$ROOT_DIR/Config/$ENVIRONMENT.json"
DIST_ROOT="$ROOT_DIR/dist"
BUILD_OUTPUT_ROOT="${BUILD_OUTPUT_ROOT:-/tmp/MacAgentOS-build}"
BUILD_ROOT="$BUILD_OUTPUT_ROOT/$ENVIRONMENT"
APP_DIR="$BUILD_ROOT/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RESOURCES_DIR="$APP_DIR/Contents/Resources"
ICONSET_DIR="$ROOT_DIR/.build/AppIcon.iconset"
ICON_PATH="$RESOURCES_DIR/AppIcon.icns"
ICON_SOURCE="$ROOT_DIR/Assets/app-icon-source.png"
ICON_SQUARE="$ROOT_DIR/.build/app-icon-square.png"
PLIST_PATH="$APP_DIR/Contents/Info.plist"
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
CONFIG_DEST="$RESOURCES_DIR/runtime-config.json"
if [[ -z "${DEVELOPER_DIR:-}" ]]; then
  if [[ -d "/Applications/Xcode.app/Contents/Developer" ]]; then
    DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
  else
    DEVELOPER_DIR="/Library/Developer/CommandLineTools"
  fi
fi
CLANG_MODULE_CACHE_PATH="${CLANG_MODULE_CACHE_PATH:-/tmp/clang-module-cache}"
SWIFT_BUILD_CONFIGURATION="${SWIFT_BUILD_CONFIGURATION:-release}"
BUILD_DIR="$ROOT_DIR/.build/arm64-apple-macosx/$SWIFT_BUILD_CONFIGURATION"
EXECUTABLE_PATH="$BUILD_DIR/$PRODUCT_NAME"
MODULE_CACHE_DIR="$BUILD_DIR/ModuleCache"

if [[ ! -f "$CONFIG_SOURCE" ]]; then
  echo "Missing config profile: $CONFIG_SOURCE" >&2
  exit 1
fi

mkdir -p "$DIST_ROOT" "$BUILD_ROOT" "$CLANG_MODULE_CACHE_PATH"

export DEVELOPER_DIR
export CLANG_MODULE_CACHE_PATH

# Copied worktrees can keep a stale Swift module cache tied to another absolute path.
# Purge only this local cache so the package can rebuild cleanly from MacAgentOS.
rm -rf "$MODULE_CACHE_DIR"

cd "$ROOT_DIR"
echo "Building for production..." >&2
swift build -c "$SWIFT_BUILD_CONFIGURATION" >&2

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cp "$EXECUTABLE_PATH" "$MACOS_DIR/$APP_NAME"
cp "$CONFIG_SOURCE" "$CONFIG_DEST"

# ── Optional: bundle PyInstaller backend binary ───────────────────────────────
# Set BACKEND_BINARY to the path of the MacAgentServer binary produced by:
#   cd "$PROJECT_ROOT"
#   .venv312/bin/pip install pyinstaller
#   .venv312/bin/pyinstaller server.spec
# Example:
#   BACKEND_BINARY="$PROJECT_ROOT/dist/MacAgentServer" ./build_and_bundle.sh
BACKEND_BINARY="${BACKEND_BINARY:-}"
if [[ -n "$BACKEND_BINARY" && -f "$BACKEND_BINARY" ]]; then
  cp "$BACKEND_BINARY" "$RESOURCES_DIR/MacAgentServer"
  chmod +x "$RESOURCES_DIR/MacAgentServer"
  echo "✅ Backend binary bundled: $BACKEND_BINARY" >&2
else
  echo "ℹ️  No BACKEND_BINARY set — backend must be started externally (dev mode)" >&2
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Optional: bundle ChatGPT Bridge runtime ──────────────────────────────────
# The bridge runtime can be an opencode-compatible standalone binary. When it is
# present in Resources, users can connect from the app without installing a CLI.
CHATGPT_BRIDGE_BINARY="${CHATGPT_BRIDGE_BINARY:-${OPENCODE_BIN:-}}"
if [[ -n "$CHATGPT_BRIDGE_BINARY" && -f "$CHATGPT_BRIDGE_BINARY" ]]; then
  BRIDGE_DEST_NAME="ChatGPTBridge"
  if [[ "$(basename "$CHATGPT_BRIDGE_BINARY")" == "codex" ]]; then
    BRIDGE_DEST_NAME="codex"
  fi
  cp "$CHATGPT_BRIDGE_BINARY" "$RESOURCES_DIR/$BRIDGE_DEST_NAME"
  chmod +x "$RESOURCES_DIR/$BRIDGE_DEST_NAME"
  echo "✅ ChatGPT Bridge bundled: $CHATGPT_BRIDGE_BINARY" >&2
else
  echo "ℹ️  No CHATGPT_BRIDGE_BINARY set — ChatGPT Bridge login requires a bundled runtime or dev fallback" >&2
fi
# ─────────────────────────────────────────────────────────────────────────────

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

if [[ -f "$ICON_SOURCE" ]]; then
  sips -c 1024 1024 "$ICON_SOURCE" --out "$ICON_SQUARE" >/dev/null
  sips -z 16 16 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
  sips -z 32 32 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
  sips -z 64 64 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
  sips -z 256 256 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
  sips -z 512 512 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$ICON_SQUARE" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET_DIR" -o "$ICON_PATH"
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>fr</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleIdentifier</key>
  <string>com.macagent.os</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundleVersion</key>
  <string>$BUILD_NUMBER</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.productivity</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
PLIST

xattr -cr "$APP_DIR" 2>/dev/null || true

if [[ -n "$SIGN_IDENTITY" ]]; then
  codesign --force --deep --options runtime --sign "$SIGN_IDENTITY" --timestamp=none "$APP_DIR" >&2
else
  codesign --force --deep --sign - "$APP_DIR" >&2
fi

echo "$APP_DIR"

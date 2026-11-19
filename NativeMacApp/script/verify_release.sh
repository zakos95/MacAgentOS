#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
RELEASE_VERSION="${RELEASE_VERSION:-$VERSION}"
ARTIFACT="MacAgentOS-$RELEASE_VERSION-macos-$(uname -m)"
ZIP_PATH="$ROOT_DIR/dist/releases/$RELEASE_VERSION/$ENVIRONMENT/$ARTIFACT.zip"
VERIFY_ROOT="${VERIFY_ROOT:-/tmp/MacAgentOS-verify}"
APP_PATH="$VERIFY_ROOT/Mac Agent OS.app"

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "Release zip not found: $ZIP_PATH" >&2
  exit 1
fi

rm -rf "$VERIFY_ROOT"
mkdir -p "$VERIFY_ROOT"
ditto -x -k "$ZIP_PATH" "$VERIFY_ROOT"

plutil -p "$APP_PATH/Contents/Info.plist" >/dev/null
codesign --verify --deep --strict "$APP_PATH"
codesign -dvvv "$APP_PATH" >/dev/null

echo "Release verified: $APP_PATH"

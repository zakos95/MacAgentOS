#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
APP_NAME="Mac Agent OS"
RELEASE_DIR="$ROOT_DIR/dist/releases/$VERSION/$ENVIRONMENT"
ZIP_PATH="$RELEASE_DIR/$APP_NAME-$VERSION-$ENVIRONMENT.zip"
DMG_PATH="$RELEASE_DIR/$APP_NAME-$VERSION-$ENVIRONMENT.dmg"
DMG_STAGING_ROOT="${DMG_STAGING_ROOT:-/tmp/MacAgentOS-dmg}"
DMG_CONTENT_DIR="$DMG_STAGING_ROOT/content"
DMG_SIGN_IDENTITY="${DMG_SIGN_IDENTITY:-Apple Development: krunshiin@gmail.com (W24CPYM89U)}"

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "Missing release zip: $ZIP_PATH" >&2
  exit 1
fi

rm -rf "$DMG_STAGING_ROOT"
mkdir -p "$DMG_CONTENT_DIR"

ditto -x -k "$ZIP_PATH" "$DMG_CONTENT_DIR"
ln -s /Applications "$DMG_CONTENT_DIR/Applications"
xattr -cr "$DMG_CONTENT_DIR" 2>/dev/null || true
rm -f "$DMG_PATH"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_CONTENT_DIR" \
  -format UDZO \
  -imagekey zlib-level=9 \
  "$DMG_PATH" >/dev/null

xattr -cr "$DMG_PATH" 2>/dev/null || true

if [[ -n "$DMG_SIGN_IDENTITY" ]]; then
  codesign --force --sign "$DMG_SIGN_IDENTITY" --timestamp=none "$DMG_PATH" >/dev/null 2>&1
else
  codesign --force --sign - "$DMG_PATH" >/dev/null 2>&1
fi

echo "$DMG_PATH"

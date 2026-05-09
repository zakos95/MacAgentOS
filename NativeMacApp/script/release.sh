#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
APP_NAME="Mac Agent OS"
RELEASE_DIR="$ROOT_DIR/dist/releases/$VERSION/$ENVIRONMENT"
ZIP_PATH="$RELEASE_DIR/$APP_NAME-$VERSION-$ENVIRONMENT.zip"
DMG_PATH="$RELEASE_DIR/$APP_NAME-$VERSION-$ENVIRONMENT.dmg"
CHECKSUMS_PATH="$RELEASE_DIR/SHA256SUMS.txt"
BUILD_INFO_PATH="$RELEASE_DIR/build-info.json"
SIGN_IDENTITY="${SIGN_IDENTITY:-Apple Development: krunshiin@gmail.com (W24CPYM89U)}"
STAGING_ROOT="${STAGING_ROOT:-/tmp/MacAgentOS-release}"

mkdir -p "$RELEASE_DIR"
mkdir -p "$STAGING_ROOT"

BUILT_APP="$(zsh "$SCRIPT_DIR/build_and_bundle.sh" | tail -n 1)"
APP_BUNDLE_PATH="$STAGING_ROOT/$APP_NAME.app"
rm -rf "$APP_BUNDLE_PATH"
ditto "$BUILT_APP" "$APP_BUNDLE_PATH"
xattr -cr "$APP_BUNDLE_PATH" 2>/dev/null || true
if [[ -n "$SIGN_IDENTITY" ]]; then
  codesign --force --deep --options runtime --sign "$SIGN_IDENTITY" --timestamp=none "$APP_BUNDLE_PATH" >/dev/null 2>&1
else
  codesign --force --deep --sign - "$APP_BUNDLE_PATH" >/dev/null 2>&1
fi
ditto -c -k --keepParent "$APP_BUNDLE_PATH" "$ZIP_PATH"
DMG_OUTPUT="$(zsh "$SCRIPT_DIR/package_dmg.sh" | tail -n 1)"

ZIP_NAME="$(basename "$ZIP_PATH")"
DMG_NAME="$(basename "$DMG_OUTPUT")"
ZIP_SHA="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
DMG_SHA="$(shasum -a 256 "$DMG_OUTPUT" | awk '{print $1}')"
APP_SHA="$(shasum -a 256 "$APP_BUNDLE_PATH/Contents/MacOS/Mac Agent OS" | awk '{print $1}')"

cat > "$CHECKSUMS_PATH" <<EOF
$ZIP_SHA  $ZIP_NAME
$DMG_SHA  $DMG_NAME
$APP_SHA  $APP_NAME/Contents/MacOS/Mac Agent OS
EOF

cat > "$BUILD_INFO_PATH" <<EOF
{
  "app_name": "$APP_NAME",
  "version": "$VERSION",
  "build_number": "$BUILD_NUMBER",
  "environment": "$ENVIRONMENT",
  "built_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "staged_bundle_path": "$APP_BUNDLE_PATH",
  "zip_path": "$ZIP_PATH",
  "dmg_path": "$DMG_PATH"
}
EOF

echo "$RELEASE_DIR"

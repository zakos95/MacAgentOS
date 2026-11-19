#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
RELEASE_VERSION="${RELEASE_VERSION:-$VERSION}"
ARTIFACT="MacAgentOS-$RELEASE_VERSION-macos-$(uname -m)"
APP_NAME="Mac Agent OS"
DMG_PATH="$ROOT_DIR/dist/releases/$RELEASE_VERSION/$ENVIRONMENT/$ARTIFACT.dmg"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found: $DMG_PATH" >&2
  exit 1
fi

hdiutil verify "$DMG_PATH" >/dev/null
codesign --verify "$DMG_PATH"
codesign -dvvv "$DMG_PATH" >/dev/null

if spctl -a -vv -t open --context context:primary-signature "$DMG_PATH" >/dev/null 2>&1; then
  echo "DMG gatekeeper assessment: passed"
else
  echo "DMG gatekeeper assessment: not accepted with current signing identity"
  echo "Note: this is expected for a local or development signature. Use Developer ID + notarization for public distribution."
fi

echo "DMG verified: $DMG_PATH"

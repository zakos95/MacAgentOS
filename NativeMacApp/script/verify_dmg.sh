#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT="${ENVIRONMENT:-prod}"
VERSION="${VERSION:-$(cat "$ROOT_DIR/VERSION")}"
APP_NAME="Mac Agent OS"
DMG_PATH="$ROOT_DIR/dist/releases/$VERSION/$ENVIRONMENT/$APP_NAME-$VERSION-$ENVIRONMENT.dmg"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found: $DMG_PATH" >&2
  exit 1
fi

codesign --verify "$DMG_PATH"
codesign -dvvv "$DMG_PATH" >/dev/null

if spctl -a -vv -t open --context context:primary-signature "$DMG_PATH" >/dev/null 2>&1; then
  echo "DMG gatekeeper assessment: passed"
else
  echo "DMG gatekeeper assessment: not accepted with current signing identity"
  echo "Note: this is expected with Apple Development signing. Use Developer ID + notarization for public distribution."
fi

echo "DMG verified: $DMG_PATH"

#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
cd "$PROJECT_ROOT"

"$PYTHON_BIN" -m PyInstaller --clean --noconfirm packaging/macos/integrated-term.spec

APP_BUNDLE="$PROJECT_ROOT/dist/集成终端控制台.app"
xattr -cr "$APP_BUNDLE"
# Prefer an installed Developer ID/Apple Development identity so Finder does
# not treat the distributed bundle as an ad-hoc executable. Set
# CODESIGN_IDENTITY explicitly for CI or notarized release builds; fall back to
# ad-hoc signing only when no usable certificate is installed.
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  SIGNING_IDENTITY="$CODESIGN_IDENTITY"
else
  SIGNING_IDENTITY="$($PYTHON_BIN - <<'PY'
import subprocess

output = subprocess.run(
    ["security", "find-identity", "-v", "-p", "codesigning"],
    check=False,
    capture_output=True,
    text=True,
).stdout
identities = []
for line in output.splitlines():
    if '"' in line:
        identities.append(line.split('"', 2)[1])
preferred = next((item for item in identities if item.startswith("Developer ID Application:")), None)
preferred = preferred or next((item for item in identities if item.startswith("Apple Development:")), None)
print(preferred or "-")
PY
)"
fi
printf 'Signing identity: %s\n' "$SIGNING_IDENTITY"
codesign --force --deep --sign "$SIGNING_IDENTITY" "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"

ditto -c -k --sequesterRsrc --keepParent \
  "$APP_BUNDLE" \
  "$PROJECT_ROOT/dist/集成终端控制台-macos-arm64.zip"

printf 'Built: %s\nArchive: %s\n' \
  "$PROJECT_ROOT/dist/集成终端控制台.app" \
  "$PROJECT_ROOT/dist/集成终端控制台-macos-arm64.zip"

#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
cd "$PROJECT_ROOT"

"$PYTHON_BIN" -m PyInstaller --clean --noconfirm packaging/macos/integrated-term.spec

ditto -c -k --sequesterRsrc --keepParent \
  "$PROJECT_ROOT/dist/集成终端控制台.app" \
  "$PROJECT_ROOT/dist/集成终端控制台-macos-arm64.zip"

printf 'Built: %s\nArchive: %s\n' \
  "$PROJECT_ROOT/dist/集成终端控制台.app" \
  "$PROJECT_ROOT/dist/集成终端控制台-macos-arm64.zip"

#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
cd "$PROJECT_ROOT"

"$PYTHON_BIN" -m PyInstaller --clean --noconfirm packaging/linux/integrated-term.spec

APP_DIR="$PROJECT_ROOT/dist/集成终端控制台"
cp packaging/linux/integrated-term.desktop "$APP_DIR/集成终端控制台.desktop"
cp assets/integrated-term-icon-opaque.png "$APP_DIR/integrated-term-icon-opaque.png"

tar -C "$PROJECT_ROOT/dist" -czf "$PROJECT_ROOT/dist/集成终端控制台-linux.tar.gz" "集成终端控制台"
printf 'Built: %s\nArchive: %s\n' "$APP_DIR" "$PROJECT_ROOT/dist/集成终端控制台-linux.tar.gz"

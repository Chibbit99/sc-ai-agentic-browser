#!/usr/bin/env bash
# Remove the installed SC.AI binaries and menu entry.
# Run with: bash uninstall.sh (or ./uninstall.sh after bash build.sh).
# User data and browser profiles are intentionally preserved.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$PWD"
PREFIX="${SCAI_PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/512x512/apps"

# Repair executable mode bits in a checkout that was cloned with 0644 files.
chmod +x "$ROOT/build.sh" "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/make-desktop-entry.sh" 2>/dev/null || true

rm -f "$BIN_DIR/sc-ai-launcher" "$BIN_DIR/sc-ai-runtime"
rm -f "$APP_DIR/sc-ai.desktop"
rm -f "$ICON_DIR/sc-ai.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

echo "SC.AI uninstalled."
echo "Removed binaries, menu entry, and icon from: $PREFIX"
echo "Your data was kept at: $HOME/.config/sc-ai"
#!/usr/bin/env bash
# Remove the installed SC.AI binaries and menu entry.
# Run with: bash uninstall.sh (or make uninstall).
#
# Your chat history, NVIDIA key and browser profiles are kept at
# ~/.config/sc-ai — this script never touches them.
set -euo pipefail
cd "$(dirname "$0")"

PREFIX="${SCAI_PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/512x512/apps"

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

echo "SC.AI uninstalled. Your data was kept at:"
echo "  $HOME/.config/sc-ai"
echo "Remove that directory manually if you want to delete all SC.AI data."
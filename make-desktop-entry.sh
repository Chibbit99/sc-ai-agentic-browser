#!/usr/bin/env bash
# Refresh only the user-local KDE menu entry after a build/install.
# Usage: bash make-desktop-entry.sh [optional-prefix]
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
chmod +x "$ROOT/build.sh" "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/make-desktop-entry.sh" 2>/dev/null || true

PREFIX="${1:-${SCAI_PREFIX:-$HOME/.local}}"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/512x512/apps"

if [ ! -x "$BIN_DIR/sc-ai-launcher" ] || [ ! -x "$BIN_DIR/sc-ai-runtime" ]; then
    echo "Installed binaries not found under $BIN_DIR. Run bash install.sh first." >&2
    exit 1
fi

mkdir -p "$APP_DIR" "$ICON_DIR"
if [ -f build/dist/sc-ai-icon.png ]; then
    install -m 644 build/dist/sc-ai-icon.png "$ICON_DIR/sc-ai.png"
elif [ -f launcher/icon.png ]; then
    install -m 644 launcher/icon.png "$ICON_DIR/sc-ai.png"
fi

sed -e "s|@EXEC@|$BIN_DIR/sc-ai-launcher|" \
    -e "s|@ICON@|$ICON_DIR/sc-ai.png|" \
    installer/sc-ai.desktop > "$APP_DIR/sc-ai.desktop"
chmod 644 "$APP_DIR/sc-ai.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

echo "KDE menu entry refreshed: $APP_DIR/sc-ai.desktop"
echo "Icon: $ICON_DIR/sc-ai.png"
echo "If it is still not visible, log out/in or restart the KDE application launcher."
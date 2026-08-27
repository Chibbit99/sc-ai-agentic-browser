#!/usr/bin/env bash
# Install the built SC.AI binaries + KDE menu entry for the current user.
# Run with: bash install.sh (or ./install.sh after bash build.sh).
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$PWD"
PREFIX="${SCAI_PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/512x512/apps"

# Repair executable mode bits in a checkout that was cloned with 0644 files.
chmod +x "$ROOT/build.sh" "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/make-desktop-entry.sh" 2>/dev/null || true

# Installation is intentionally atomic from the user's perspective: never
# install a launcher without the runtime it must start.
if [ ! -f build/dist/sc-ai-launcher ] || [ ! -f build/dist/sc-ai-runtime ] || [ ! -f build/dist/sc-ai-icon.png ]; then
    echo "SC.AI build artifacts are incomplete." >&2
    echo "Run: bash build.sh && bash install.sh" >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

install -m 755 build/dist/sc-ai-launcher "$BIN_DIR/sc-ai-launcher"
install -m 755 build/dist/sc-ai-runtime "$BIN_DIR/sc-ai-runtime"
install -m 644 build/dist/sc-ai-icon.png "$ICON_DIR/sc-ai.png"

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

echo "SC.AI installed."
echo "  Launcher: $BIN_DIR/sc-ai-launcher"
echo "  Runtime:  $BIN_DIR/sc-ai-runtime"
echo "  Menu:     $APP_DIR/sc-ai.desktop"
echo
echo "Open the application menu and search for SC.AI."
echo "To pin it: right-click SC.AI → Add to Favorites."
echo "To uninstall later: bash $ROOT/uninstall.sh"
#!/usr/bin/env bash
# Install the built SC.AI binaries + KDE menu entry for the current user.
#
# Everything is installed under ~/.local (override with SCAI_PREFIX), which
# is the standard per-user location and requires no root. The .desktop file
# is generated with the real install paths — no development paths leak in.
set -euo pipefail
cd "$(dirname "$0")"

PREFIX="${SCAI_PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/512x512/apps"

if [ ! -x build/dist/sc-ai-launcher ] || [ ! -x build/dist/sc-ai-runtime ]; then
    echo "Build artifacts not found in build/dist/. Run ./build.sh first." >&2
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

echo "SC.AI installed."
echo "  Launcher: $BIN_DIR/sc-ai-launcher"
echo "  Runtime:  $BIN_DIR/sc-ai-runtime"
echo "  Menu:     $APP_DIR/sc-ai.desktop"
echo
echo "Open the application menu and search for SC.AI, right-click it and"
echo "choose 'Add to Favorites' to pin it to the taskbar, or run:"
echo "  $BIN_DIR/sc-ai-launcher"
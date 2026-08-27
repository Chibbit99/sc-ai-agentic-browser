#!/usr/bin/env bash
# Build the SC.AI desktop application into build/dist/ using PyInstaller.
#
# Produces two standalone binaries (no Python required on the target system):
#   build/dist/sc-ai-launcher   — the GUI the user launches from the menu
#   build/dist/sc-ai-runtime    — the Selenium runtime (server + browser)
#   build/dist/sc-ai-icon.png   — a copy of launcher/icon.png
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$PWD"
VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"
RUNTIME_TMP="${XDG_CACHE_HOME:-$HOME/.cache}/sc-ai/pyi"

# A clone may have lost executable mode bits. This line runs because the
# script is already being interpreted by bash, and repairs the checkout for
# the next invocation (`./build.sh`).
chmod +x "$ROOT/build.sh" "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/make-desktop-entry.sh" 2>/dev/null || true

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "python3 is required to build SC.AI." >&2
    exit 1
fi

# ---- virtualenv -----------------------------------------------------------
if [ ! -d "$VENV" ]; then
    "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r requirements.txt

# ---- icon -----------------------------------------------------------------
# Supply your own PNG at launcher/icon.png. It is embedded into the launcher
# and installed as the KDE application icon; no icon is generated for you.
if [ ! -s "$ROOT/launcher/icon.png" ]; then
    echo "Missing custom icon: place your PNG at launcher/icon.png" >&2
    exit 1
fi

# ---- PyInstaller ----------------------------------------------------------
# Never leave a partially built installation available. The install script
# also validates all three artifacts before replacing the user's binaries.
mkdir -p build/dist build/work
rm -rf build/dist/*

# Selenium runtime: bundles selenium + index.html.
# --runtime-tmpdir avoids /tmp noexec issues on some distros (onefile
#   extracts to this directory at every launch).
# NOTE: --add-data paths must be absolute — with --specpath build,
#   PyInstaller resolves relative data paths against the spec directory.
"$VENV/bin/pyinstaller" --noconfirm --clean \
    --onefile --windowed \
    --name sc-ai-runtime \
    --paths "$ROOT" \
    --distpath build/dist --workpath build/work --specpath build \
    --runtime-tmpdir "$RUNTIME_TMP" \
    --collect-all selenium \
    --add-data "$ROOT/app/index.html:." \
    app/seleniumTest.py

# Launcher: tkinter GUI. No selenium needed, so this stays small.
"$VENV/bin/pyinstaller" --noconfirm --clean \
    --onefile --windowed \
    --icon "$ROOT/launcher/icon.png" \
    --name sc-ai-launcher \
    --paths "$ROOT" \
    --distpath build/dist --workpath build/work --specpath build \
    --runtime-tmpdir "$RUNTIME_TMP" \
    --add-data "$ROOT/launcher/icon.png:." \
    launcher/launcher.py

cp launcher/icon.png build/dist/sc-ai-icon.png

# Fail the build if any artifact is absent or not a regular file.
for artifact in build/dist/sc-ai-launcher build/dist/sc-ai-runtime build/dist/sc-ai-icon.png; do
    if [ ! -f "$artifact" ]; then
        echo "Build failed: missing $artifact" >&2
        exit 1
    fi
done

echo
echo "Build complete. Artifacts in build/dist/:"
ls -lh build/dist
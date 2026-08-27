#!/usr/bin/env bash
# Build the SC.AI desktop application into build/dist/ using PyInstaller.
#
# Produces two standalone binaries (no Python required on the target system):
#   build/dist/sc-ai-launcher   — the GUI the user launches from the menu
#   build/dist/sc-ai-runtime    — the Selenium runtime (server + browser)
#   build/dist/sc-ai-icon.png   — the application icon
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$PWD"
VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"
RUNTIME_TMP="${XDG_CACHE_HOME:-$HOME/.cache}/sc-ai/pyi"

# This checkout may be unpacked or cloned without executable mode bits.
# Build scripts are run explicitly with bash so this works either way.

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
"$VENV/bin/python" build/make_icon.py --out launcher/icon.png

# ---- PyInstaller ----------------------------------------------------------
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
    --name sc-ai-launcher \
    --paths "$ROOT" \
    --distpath build/dist --workpath build/work --specpath build \
    --runtime-tmpdir "$RUNTIME_TMP" \
    --add-data "$ROOT/launcher/icon.png:." \
    launcher/launcher.py

cp launcher/icon.png build/dist/sc-ai-icon.png

echo
echo "Build complete. Artifacts in build/dist/:"
ls -lh build/dist
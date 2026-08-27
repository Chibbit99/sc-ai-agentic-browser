#!/usr/bin/env bash
# Compatibility alias: install/refresh SC.AI's KDE menu entry.
# Usage: bash install-desktop.sh
set -euo pipefail
cd "$(dirname "$0")"
exec bash make-desktop-entry.sh "$@"
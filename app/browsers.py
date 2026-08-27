"""Installed-browser registry and detection for SC.AI.

The launcher uses detect_browsers() to show the user what is installed; the
runtime uses the same registry to validate the browser the launcher chose and
to pick the right Selenium driver kind.

Detection order per browser id (first hit wins):
    1. Native executables on PATH (shutil.which) + well-known absolute paths.
    2. Snap wrappers in /snap/bin.
    3. Flatpak export wrappers (~/.local/share/flatpak/exports/bin and
       /var/lib/flatpak/exports/bin).

Snap / Flatpak notes
--------------------
Selenium Manager can usually match a driver because the wrapper forwards
`--version`. However, strict Snap confinement (AppArmor) can block Selenium's
remote-debugging channel or writes to profile directories outside the Snap
home, so a Snap browser may refuse to start. Flatpak is generally fine
because the SC.AI profile lives under the user's home directory, which the
Flatpak sandbox maps read-write. When a browser fails to launch, the runtime
surfaces the real error and the launcher shows it, so a packaging quirk never
results in a silent failure.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserSpec:
    id: str
    name: str
    executables: tuple[str, ...]
    snap_names: tuple[str, ...] = ()
    flatpak_ids: tuple[str, ...] = ()
    driver_kind: str = "chrome"  # "chrome" (Chrome/Chromium) or "firefox"


BROWSER_SPECS: list[BrowserSpec] = [
    BrowserSpec(
        id="chrome",
        name="Google Chrome",
        executables=("google-chrome-stable", "google-chrome", "chrome"),
        flatpak_ids=("com.google.Chrome",),
        driver_kind="chrome",
    ),
    BrowserSpec(
        id="chromium",
        name="Chromium",
        executables=("chromium", "chromium-browser"),
        snap_names=("chromium",),
        flatpak_ids=("org.chromium.Chromium",),
        driver_kind="chrome",
    ),
    BrowserSpec(
        id="firefox",
        name="Firefox",
        executables=("firefox",),
        snap_names=("firefox",),
        flatpak_ids=("org.mozilla.firefox",),
        driver_kind="firefox",
    ),
]

# Well-known absolute paths for distros that do not put these on PATH.
_EXTRA_PATHS: dict[str, tuple[str, ...]] = {
    "chrome": (
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/google-chrome",
    ),
    "chromium": (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ),
    "firefox": (
        "/usr/bin/firefox",
        "/usr/lib/firefox/firefox",
        "/opt/firefox/firefox",
    ),
}


@dataclass(frozen=True)
class DetectedBrowser:
    spec: BrowserSpec
    path: str
    kind: str  # "native", "snap" or "flatpak"

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def driver_kind(self) -> str:
        return self.spec.driver_kind

    @property
    def label(self) -> str:
        if self.kind == "snap":
            return f"{self.name} (Snap)"
        if self.kind == "flatpak":
            return f"{self.name} (Flatpak)"
        return self.name


def get_spec(browser_id: str) -> BrowserSpec | None:
    for spec in BROWSER_SPECS:
        if spec.id == browser_id:
            return spec
    return None


def _flatpak_bin_dirs() -> list[Path]:
    dirs = [
        Path.home() / ".local" / "share" / "flatpak" / "exports" / "bin",
        Path("/var/lib/flatpak/exports/bin"),
    ]
    return [d for d in dirs if d.is_dir()]


def detect_browsers() -> list[DetectedBrowser]:
    """Return every installed supported browser (one entry per id)."""
    found: dict[str, DetectedBrowser] = {}

    def add(browser: DetectedBrowser) -> None:
        if browser.id not in found:
            found[browser.id] = browser

    for spec in BROWSER_SPECS:
        # 1) Native executables on PATH.
        for name in spec.executables:
            path = shutil.which(name)
            if path:
                add(DetectedBrowser(spec, path, "native"))
                break
        if spec.id in found:
            continue
        # 2) Well-known absolute paths.
        for candidate in _EXTRA_PATHS.get(spec.id, ()):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                add(DetectedBrowser(spec, candidate, "native"))
                break
        if spec.id in found:
            continue
        # 3) Snap wrappers.
        for name in spec.snap_names:
            path = Path("/snap/bin") / name
            if path.is_file():
                add(DetectedBrowser(spec, str(path), "snap"))
                break
        if spec.id in found:
            continue
        # 4) Flatpak export wrappers.
        for flatpak_id in spec.flatpak_ids:
            for directory in _flatpak_bin_dirs():
                path = directory / flatpak_id
                if path.is_file():
                    add(DetectedBrowser(spec, str(path), "flatpak"))
                    break
            if spec.id in found:
                break

    return list(found.values())


def detect_browser(browser_id: str) -> DetectedBrowser | None:
    for browser in detect_browsers():
        if browser.id == browser_id:
            return browser
    return None
"""Installed browser detection for SC.AI."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserSpec:
    id: str
    name: str
    executables: tuple[str, ...]
    snap_names: tuple[str, ...] = ()
    flatpak_ids: tuple[str, ...] = ()
    driver_kind: str = "chrome"


BROWSER_SPECS = [
    BrowserSpec("chrome", "Google Chrome", ("google-chrome-stable", "google-chrome", "chrome"), flatpak_ids=("com.google.Chrome",)),
    BrowserSpec("chromium", "Chromium", ("chromium", "chromium-browser"), snap_names=("chromium",), flatpak_ids=("org.chromium.Chromium",)),
    BrowserSpec("firefox", "Firefox", ("firefox",), snap_names=("firefox",), flatpak_ids=("org.mozilla.firefox",), driver_kind="firefox"),
]

_EXTRA_PATHS = {
    "chrome": ("/usr/bin/google-chrome-stable", "/usr/bin/google-chrome", "/opt/google/chrome/google-chrome"),
    "chromium": ("/usr/bin/chromium", "/usr/bin/chromium-browser"),
    "firefox": ("/usr/bin/firefox", "/usr/lib/firefox/firefox", "/opt/firefox/firefox"),
}


@dataclass(frozen=True)
class DetectedBrowser:
    spec: BrowserSpec
    path: str
    kind: str

    @property
    def id(self): return self.spec.id
    @property
    def name(self): return self.spec.name
    @property
    def driver_kind(self): return self.spec.driver_kind
    @property
    def label(self): return f"{self.name} ({self.kind.title()})" if self.kind != "native" else self.name


def get_spec(browser_id: str):
    return next((spec for spec in BROWSER_SPECS if spec.id == browser_id), None)


def _flatpak_bin_dirs():
    return [p for p in (Path.home() / ".local/share/flatpak/exports/bin", Path("/var/lib/flatpak/exports/bin")) if p.is_dir()]


def _snap_firefox_binary() -> Path | None:
    """Find Firefox's real binary through the installed Snap metadata."""
    mounts: list[Path] = []
    snap_command = shutil.which("snap")
    if snap_command:
        try:
            result = subprocess.run(
                [snap_command, "run", "--shell", "firefox", "-c", "printf '%s' \"$SNAP\""],
                capture_output=True, text=True, timeout=5, check=False,
            )
            snap_mount = result.stdout.strip()
            if snap_mount:
                mounts.append(Path(snap_mount))
        except (OSError, subprocess.SubprocessError):
            pass
    root = Path("/snap/firefox")
    if root.is_dir():
        mounts.append(root / "current")
        mounts.extend(p for p in root.iterdir() if p.is_dir() and p.name.isdigit())
    seen: set[str] = set()
    for mount in mounts:
        try:
            key = str(mount.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        for relative in ("usr/lib/firefox/firefox", "usr/lib/firefox/firefox-bin"):
            candidate = mount / relative
            if _is_firefox_binary(candidate):
                return candidate
    return None


def _is_firefox_binary(path: Path) -> bool:
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        return path.read_bytes()[:4] == b"\x7fELF"
    except OSError:
        return False


def _firefox_binary(path: str) -> str | None:
    """Return a real Firefox binary, never /usr/bin/firefox wrappers."""
    candidates = []
    snap_binary = _snap_firefox_binary()
    if snap_binary:
        candidates.append(snap_binary)
    supplied = Path(path)
    if _is_firefox_binary(supplied):
        candidates.append(supplied)
    for root in (Path("/usr/lib/firefox"), Path("/usr/lib/firefox-esr"), Path("/opt/firefox"), Path.home() / ".local/firefox"):
        candidates.extend(root / name for name in ("firefox", "firefox-bin"))
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            if not _is_firefox_binary(candidate):
                continue
            result = subprocess.run([str(candidate), "--version"], capture_output=True, text=True, timeout=5, check=False)
            if result.returncode == 0 and "firefox" in (result.stdout + result.stderr).lower():
                return str(candidate)
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def detect_browsers() -> list[DetectedBrowser]:
    found = {}
    def add(browser):
        found.setdefault(browser.id, browser)
    for spec in BROWSER_SPECS:
        if spec.id == "firefox":
            wrapper = shutil.which("firefox")
            if wrapper:
                resolved = _firefox_binary(wrapper)
                if resolved:
                    add(DetectedBrowser(spec, resolved, "snap" if resolved.startswith("/snap/") else "native"))
                    continue
        for name in spec.executables:
            if (path := shutil.which(name)):
                add(DetectedBrowser(spec, path, "native")); break
        if spec.id in found: continue
        for path in _EXTRA_PATHS.get(spec.id, ()):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                add(DetectedBrowser(spec, path, "native")); break
        if spec.id in found: continue
        for name in spec.snap_names:
            path = Path("/snap/bin") / name
            if path.is_file(): add(DetectedBrowser(spec, str(path), "snap")); break
        if spec.id in found: continue
        for app_id in spec.flatpak_ids:
            for directory in _flatpak_bin_dirs():
                path = directory / app_id
                if path.is_file(): add(DetectedBrowser(spec, str(path), "flatpak")); break
            if spec.id in found: break
    return list(found.values())


def detect_browser(browser_id: str) -> DetectedBrowser | None:
    browser = next((b for b in detect_browsers() if b.id == browser_id), None)
    if browser and browser.driver_kind == "firefox":
        resolved = _firefox_binary(browser.path)
        return DetectedBrowser(browser.spec, resolved, browser.kind) if resolved else None
    return browser
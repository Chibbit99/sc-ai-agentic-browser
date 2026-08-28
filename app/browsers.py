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
import re
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


def _snap_firefox_binary() -> Path | None:
    """Find the real Firefox binary behind the current Snap revision."""
    snap_root = Path("/snap/firefox")
    mounts = [snap_root / "current"]
    if snap_root.is_dir():
        mounts.extend(p for p in snap_root.iterdir() if p.is_dir() and p.name.isdigit())
    for mount in mounts:
        for relative in (Path("usr/lib/firefox/firefox"), Path("usr/lib/firefox/firefox-bin")):
            candidate = mount / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _firefox_binary(path: str) -> str | None:
    """Return a real Firefox executable, never a package wrapper."""
    candidates: list[Path] = []
    candidate = Path(path)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        try:
            header = candidate.read_bytes()[:4]
            if header == b"\\x7fELF":
                candidates.append(candidate)
        except OSError:
            pass
    snap_binary = _snap_firefox_binary()
    if snap_binary:
        candidates.append(snap_binary)
    for root in (Path("/usr/lib/firefox"), Path("/usr/lib/firefox-esr"), Path("/opt/firefox"), Path.home() / ".local/firefox"):
        candidates.extend(root / name for name in ("firefox", "firefox-bin"))
    for item in candidates:
        try:
            item = item.resolve()
            if item.read_bytes()[:4] != b"\\x7fELF" or not os.access(item, os.X_OK):
                continue
            result = subprocess.run([str(item), "--version"], capture_output=True, text=True, timeout=5, check=False)
            if result.returncode == 0 and "firefox" in result.stdout.lower():
                return str(item)
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def detect_browsers() -> list[DetectedBrowser]:
    """Return every installed supported browser (one entry per id)."""
    found: dict[str, DetectedBrowser] = {}

    def add(browser: DetectedBrowser) -> None:
        if browser.id not in found:
            found[browser.id] = browser

    for spec in BROWSER_SPECS:
        # Firefox's /usr/bin/firefox is frequently a Snap wrapper. Resolve it
        # before recording the result so the launcher never persists the
        # wrapper path when the real Snap binary is available.
        if spec.id == "firefox":
            path = shutil.which("firefox")
            if path:
                resolved = _firefox_binary(path)
                if resolved:
                    add(DetectedBrowser(spec, resolved, "snap" if str(resolved).startswith("/snap/") else "native"))
                    continue
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


def _snap_firefox_binary() -> Path | None:
    """Find Firefox's real executable inside an installed Snap revision.

    /snap/firefox/current is a Snap-maintained symlink and therefore is
    intentionally discovered at runtime rather than hardcoded to a revision.
    """
    snap_root = Path("/snap")
    for mount in (snap_root / "firefox" / "current", snap_root / "firefox"):
        if not mount.exists():
            continue
        for relative in (
            Path("usr/lib/firefox/firefox"),
            Path("usr/lib/firefox/firefox-bin"),
            Path("firefox"),
        ):
            candidate = mount / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _firefox_binary(path: str) -> str | None:
    """Resolve a usable Firefox executable, including distro wrappers.

    Ubuntu/Debian's ``/usr/bin/firefox`` may be a Snap/package wrapper. It
    cannot be passed as ``binary_location``. Prefer a real ELF Firefox binary
    discovered from the wrapper's arguments, common install directories, or
    Firefox's own version output. Never return the wrapper itself.
    """
    candidate = Path(path)
    candidates: list[Path] = []

    # A symlink is safe to resolve; a shell script is not a Selenium binary.
    if candidate.is_symlink():
        candidates.append(candidate.resolve())
    if candidate.is_file() and os.access(candidate, os.X_OK):
        try:
            data = candidate.read_bytes()[:4096]
            if data.startswith(b"\\x7fELF"):
                candidates.append(candidate)
            elif data.startswith(b"#!"):
                text = data.decode("utf-8", errors="ignore")
                for match in re.findall(r"(?:^|[ =])(/[^\\s\"']*(?:firefox|firefox-bin))(?:$|[\\s\"'])", text):
                    candidates.append(Path(match))
        except OSError:
            pass

    snap_binary = _snap_firefox_binary()
    if snap_binary is not None:
        candidates.append(snap_binary)

    roots = [
        Path("/usr/lib/firefox"),
        Path("/usr/lib/firefox-esr"),
        Path("/usr/lib64/firefox"),
        Path("/opt/firefox"),
        Path.home() / ".local" / "firefox",
    ]
    for root in roots:
        candidates.extend((root / name for name in ("firefox", "firefox-bin")))

    seen: set[str] = set()
    for item in candidates:
        try:
            item = item.resolve()
        except OSError:
            continue
        key = str(item)
        if key in seen or not item.is_file() or not os.access(item, os.X_OK):
            continue
        seen.add(key)
        try:
            data = item.read_bytes()[:4]
        except OSError:
            continue
        if data != b"\\x7fELF":
            continue
        try:
            result = subprocess.run(
                [str(item), "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and "firefox" in result.stdout.lower():
            return str(item)
    return None


def detect_browser(browser_id: str) -> DetectedBrowser | None:
    for browser in detect_browsers():
        if browser.id == browser_id:
            if browser.driver_kind == "firefox":
                binary = _firefox_binary(browser.path)
                if binary:
                    return DetectedBrowser(browser.spec, binary, browser.kind)
                continue
            return browser
    return None
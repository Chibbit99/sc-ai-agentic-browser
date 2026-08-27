"""Shared helpers for SC.AI — imported by both the launcher and the runtime.

Keeping the shared bits in one module guarantees the launcher and the Selenium
runtime always agree on where data lives, how the config is stored, and how
single-instance locking works.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> Path:
    """Per-user application data directory (platform aware).

    Linux   $XDG_CONFIG_HOME/sc-ai      (usually ~/.config/sc-ai)
    macOS   ~/Library/Application Support/SC.AI
    Windows %APPDATA%/SC.AI
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "SC.AI"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SC.AI"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc-ai"
    return base


def config_file() -> Path:
    return app_data_dir() / "config.json"


def state_file() -> Path:
    return app_data_dir() / "state.json"


def lock_file() -> Path:
    return app_data_dir() / "runtime.lock"


def profiles_dir() -> Path:
    return app_data_dir() / "profiles"


def profile_dir(browser_id: str) -> Path:
    return profiles_dir() / browser_id


def log_file(name: str) -> Path:
    return app_data_dir() / "logs" / f"{name}.log"


def read_config() -> dict:
    try:
        with config_file().open("r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(value: dict) -> None:
    """Persist the config atomically and restrict its permissions.

    The config may contain the NVIDIA API key, so it is written via a
    temporary file + rename (atomic) and chmod'd to 0600.
    """
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2)
    tmp.replace(path)
    _chmod(path, 0o600)


def read_state() -> dict:
    try:
        with state_file().open("r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(payload: dict) -> None:
    """Write the runtime status file the launcher polls.

    Contains only transient data (pid, port, status) — never keys or
    browsing data.
    """
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        tmp.replace(path)
        _chmod(path, 0o600)
    except OSError:
        pass


def clear_state() -> None:
    try:
        state_file().unlink(missing_ok=True)
    except OSError:
        pass


def ensure_private_dir(path: Path, mode: int = 0o700) -> None:
    """Create a directory with restrictive permissions.

    Browser profiles contain cookies, sessions and history, so everything
    under the SC.AI app-data directory is created private.
    """
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, mode)


def setup_logger(name: str) -> logging.Logger:
    """File logger under <app-data>/logs/<name>.log."""
    logger = logging.getLogger(f"scai.{name}")
    if logger.handlers:
        return logger
    log_dir = app_data_dir() / "logs"
    ensure_private_dir(log_dir)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


class InstanceLock:
    """Advisory single-instance lock.

    POSIX uses flock(); Windows uses msvcrt.locking(). The lock is released
    automatically when the owning process exits or crashes, so a stale lock
    can never block future launches.
    """

    def __init__(self, path: Path):
        self._path = path
        self._fh = None

    def try_acquire(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fh = self._path.open("a+")
            if sys.platform.startswith("win"):
                import msvcrt

                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    fh.write(b"0")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh = fh
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform.startswith("win"):
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
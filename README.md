# SC.AI — Agentic Browser Desktop App

SC.AI is an AI chat application that opens its UI inside a real browser
controlled by Selenium. It is a **Linux desktop application**: you install it
like any other app, launch it from the KDE application menu / taskbar, choose
which installed browser it should use, and SC.AI opens that browser with a
**persistent, dedicated SC.AI profile**. Chat runs against NVIDIA NIM through
a local proxy server.

SC.AI **never bundles a browser** — it detects the browsers you already have
installed and lets you pick one.

---

## Architecture

```text
SC.AI Launcher (sc-ai-launcher, tkinter GUI)
      │
      ├── Detect installed browsers (native / Snap / Flatpak)
      ├── Let user choose browser  (remembered in config.json)
      ├── Single-instance check (runtime.lock)
      └── Spawn SC.AI runtime with: --browser <id> --browser-path <path>
                    │
                    └── SC.AI runtime (sc-ai-runtime)
                          ├── Start local HTTP server on 127.0.0.1:<random port>
                          ├── Start Selenium (driver auto-managed by Selenium Manager)
                          ├── Open browser with dedicated persistent profile
                          └── Serve index.html + NVIDIA NIM proxy
                                └── SC.AI chat UI (frontend)
```

```
sc-ai/
├── launcher/
│   ├── launcher.py        # Desktop GUI: pick browser → launch runtime
│   └── icon.png           # Generated app icon
├── app/
│   ├── __init__.py
│   ├── scai_common.py     # Shared: paths, config, lock, logging
│   ├── browsers.py        # Browser registry + detection
│   ├── seleniumTest.py    # Runtime: server + Selenium + profile
│   └── index.html         # SC.AI frontend (unchanged apart from copy)
├── installer/
│   └── sc-ai.desktop      # KDE menu entry (template)
├── build/
│   └── make_icon.py       # Dependency-free icon generator
├── build.sh               # PyInstaller build → build/dist/
├── install.sh             # User-local install + menu registration
├── make-desktop-entry.sh  # Refresh menu/icon without rebuilding
├── install-desktop.sh     # Compatibility alias for menu refresh
├── uninstall.sh
├── requirements.txt
└── Makefile
```

### Launcher vs runtime

The launcher is the window users pin to the taskbar. It only picks the
browser; then it starts the runtime and exits. The runtime owns the local
HTTP server, Selenium, and the browser process, and lives exactly as long as
the browser window is open. This keeps the two concerns separate and means
closing the launcher never kills a running SC.AI session.

---

## Browser detection

Detection is per browser id and tries, in order: **native executables on
PATH**, well-known absolute paths, **Snap** wrappers (`/snap/bin/...`), then
**Flatpak** export wrappers (`~/.local/share/flatpak/exports/bin/...`).
Adding a new browser later means adding one `BrowserSpec` entry.

| Browser | Executables checked | Snap | Flatpak |
|---|---|---|---|
| Google Chrome | `google-chrome-stable`, `google-chrome`, `chrome` | — | `com.google.Chrome` |
| Chromium | `chromium`, `chromium-browser` | `chromium` | `org.chromium.Chromium` |
| Firefox | `firefox` | `firefox` | `org.mozilla.firefox` |

**Snap / Flatpak caveats.** The runtime reports the real error if a browser
fails to launch, and the launcher shows it:

* **Snap** (Chromium/Firefox): strict AppArmor confinement can block
  Selenium's remote-debugging channel or writes to a profile outside the
  Snap's home. It *often* works, but if a Snap browser refuses to start,
  install a native/Firefox build instead.
* **Flatpak**: generally works because the SC.AI profile lives under your
  real home directory, which the Flatpak sandbox maps read-write.

---

## Persistent browser profiles

Browsing data (cookies, login sessions, local storage, IndexedDB, history)
persists between launches **inside a dedicated SC.AI profile** — never your
normal browser profile:

```
~/.config/sc-ai/
├── config.json          # NVIDIA key + last browser choice  (chmod 600)
├── state.json           # transient runtime status for the launcher
├── runtime.lock         # single-instance lock
├── logs/                # runtime.log + launcher.log
└── profiles/
    ├── chrome/          # Chrome/Chromium user-data-dir (Default inside)
    ├── chromium/
    └── firefox/         # Firefox -profile directory
```

Switching from Chrome to Firefox keeps both profiles intact.

**Security / privacy**

* Profiles stay entirely local; they are never uploaded, never sent to
  NVIDIA, and **never served over HTTP** (the server only serves
  `index.html` and the `/api/*` proxy, and binds to `127.0.0.1`).
* The app-data directory and profiles are created with restrictive
  permissions (`0700` dirs, `0600` config).
* The NVIDIA key is stored only in `config.json` and is only used by the
  local server to authenticate NVIDIA requests — the frontend never sends it
  to any third-party site.

**Profile locking / single instance.** Both the launcher and the runtime use
a shared `flock`-based lock (`runtime.lock`). It prevents two SC.AI
instances from opening the same profile at once (which Chrome would refuse,
and which could corrupt the profile). The lock is released automatically if
the process crashes. Firefox stale lock files are cleaned up on start.

---

## Selenium Manager / drivers

Selenium ≥ 4.6 ships **Selenium Manager**, which resolves and downloads the
matching driver automatically:

| Browser | Driver | Notes |
|---|---|---|
| Chrome / Chromium | `chromedriver` | Version-matched to the installed browser; Selenium queries `binary_location --version` |
| Firefox | `geckodriver` | Browser-agnostic |

- Drivers are cached in `~/.cache/selenium`; the first launch needs network.
- You do **not** need to install `chromedriver`/`geckodriver` manually, and
  SC.AI never downloads or installs a browser.
- If a driver cannot be resolved (offline first run, weird browser build),
  the runtime writes the real error to `state.json` and the launcher shows
  it; the full trace also lands in `~/.config/sc-ai/logs/runtime.log`.

---

## Build & install

Prerequisites: `python3`, a venv-capable Python, and (for dev runs) the
system `python3-tk` package. The **installed** app needs neither Python nor
Tk — PyInstaller bundles them.

```bash
# 1) Build both binaries + icon into build/dist/
# bash is intentional: cloned files may not have executable permission bits.
bash build.sh      # or: make build

# 2) Install for the current user (no root)
bash install.sh    # or: make install
```

This installs `sc-ai-launcher` / `sc-ai-runtime` into `~/.local/bin`,
copies the icon to `~/.local/share/icons/...`, and writes
`~/.local/share/applications/sc-ai.desktop` (with real paths — no
development directory is hardcoded).

Then:

1. Open the KDE **application menu** → search **SC.AI**.
2. Right-click → **Add to Favorites** to pin it to the taskbar.
3. Launch SC.AI → pick your browser → SC.AI opens in a dedicated profile.
4. Sign in to Google/NVIDIA etc. — sessions persist on the next launch.

Uninstall (keeps your data): `bash uninstall.sh` or `make uninstall`.
If you rebuilt or moved the installed files and only need to refresh KDE's
menu entry, run `bash make-desktop-entry.sh` (or `bash install-desktop.sh`,
or `make refresh-menu`).

The scripts are intentionally documented with `bash script.sh` rather than
`./script.sh`, because Git checkouts, ZIP archives, and some file systems can
lose executable permission bits. If desired, you can also restore them with
`chmod +x build.sh install.sh uninstall.sh`.

---

## Testing from a clean environment

```bash
git clone <repo> sc-ai && cd sc-ai

# Development virtualenv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1) See what the launcher detects
.venv/bin/python launcher/launcher.py --list

# 2) Run the launcher GUI (or --console for a text picker)
.venv/bin/python launcher/launcher.py

# 3) Run the runtime directly (skips the launcher)
.venv/bin/python app/seleniumTest.py \
    --browser chrome --browser-path "$(command -v google-chrome-stable)"

# 4) Full build + install
bash build.sh && bash install.sh
```

When running the runtime directly, the browser's Selenium window opens with
the SC.AI profile; closing the browser window stops the runtime and the
server.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No supported browser found" | Install Chrome, Chromium or Firefox. SC.AI never bundles one. |
| Browser window opens but SC.AI never appears | Check `~/.config/sc-ai/logs/runtime.log`; likely a driver download or Snap confinement issue. |
| First launch is slow | Selenium Manager downloads the matching driver once; subsequent launches are fast. |
| PyInstaller error about `/tmp` (noexec) | `build.sh` already sets `--runtime-tmpdir ~/.cache/sc-ai/pyi`. |
| PyInstaller: "Python shared library (libpython3.X) was not found" | Install the matching system package, e.g. `sudo apt install libpython3.10` (Ubuntu/Kubuntu). Desktop installs normally include it. |
| "SC.AI is already running" | Another SC.AI session is using the profile. Close the browser window first. |
| Snap browser fails to start | Prefer a native or Flatpak install of that browser. |
| Dev run: `ModuleNotFoundError: _tkinter` | Install `python3-tk` (Ubuntu/Kubuntu). Installed users don't need it. |
| `./build.sh: Permission denied` | Run `bash build.sh`; it now repairs permissions automatically. Future checkouts can also restore them with `chmod +x build.sh install.sh uninstall.sh make-desktop-entry.sh`. |
| `./uninstall.sh: Permission denied` | Run `bash uninstall.sh`; the uninstaller now repairs its own permission for later use. |
| Menu entry does not appear immediately | Run `kbuildsycoca6 --noincremental` (KDE 6) or `kbuildsycoca5 --noincremental` (KDE 5), then search for SC.AI again. |

---

## Cross-platform direction

The core (`app/scai_common.py`) is platform-aware (Linux `~/.config/sc-ai`,
macOS `~/Library/Application Support/SC.AI`, Windows `%APPDATA%/SC.AI`) and
the lock supports POSIX `flock` plus Windows `msvcrt`. The launcher's
browser detection and the `.desktop` installer are Linux-focused; adding
macOS/Windows support means extending `browsers.py` detection and providing
an installer for each platform.
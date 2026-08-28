"""SC.AI launcher — the desktop application users launch from the menu.

The launcher is intentionally small:
  1. Detect installed browsers.
  2. Let the user pick one (remembering the choice).
  3. Start the SC.AI runtime with that browser and get out of the way.

All the heavy lifting (local server, Selenium, persistent profile) happens in
the sc-ai-runtime binary. This file is what the .desktop entry points at, so
it is also what users pin to the KDE taskbar.

The GUI is built with Tkinter (bundled with Python and with PyInstaller). If
Tkinter or a display is unavailable, a plain console picker is used instead,
so the launcher still works over SSH or on minimal systems.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import browsers, scai_common as common

logger = common.setup_logger("launcher")

NO_BROWSER_MESSAGE = (
    "SC.AI could not find a supported browser on this system.\n\n"
    "SC.AI requires Google Chrome, Chromium or Firefox. Install one of them\n"
    "and launch SC.AI again. SC.AI does not bundle a browser."
)


def launcher_icon_path() -> Path:
    if common.is_frozen():
        return Path(getattr(sys, "_MEIPASS", ".")) / "icon.png"
    return Path(__file__).resolve().parent / "icon.png"


def runtime_command(selected: browsers.DetectedBrowser) -> list[str]:
    """Return the runtime executable, requiring a completed installation."""
    if common.is_frozen():
        # The launcher and runtime are installed side by side in ~/.local/bin.
        # Resolve from the real executable path, not _MEIPASS, so this remains
        # correct for PyInstaller one-file binaries.
        exe = Path(sys.executable).resolve().with_name("sc-ai-runtime")
        if not exe.is_file():
            raise FileNotFoundError(
                f"SC.AI runtime is missing: {exe}. Run 'bash build.sh && bash install.sh'."
            )
        return [str(exe), "--browser", selected.id, "--browser-path", selected.path]
    script = Path(__file__).resolve().parent.parent / "app" / "seleniumTest.py"
    return [sys.executable, str(script), "--browser", selected.id, "--browser-path", selected.path]


def spawn_runtime(selected: browsers.DetectedBrowser) -> tuple[subprocess.Popen | None, str]:
    try:
        return subprocess.Popen(
            runtime_command(selected),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ), ""
    except (OSError, FileNotFoundError) as error:
        return None, f"Could not start the SC.AI runtime: {error}"


def wait_for_runtime(proc, timeout: float = 90.0, progress=None) -> tuple[bool, str]:
    """Wait until the runtime reports it is up (or fails).

    Returns (ok, message). The runtime writes its status to the shared state
    file; the launcher polls it so it can show a clear error if the browser
    or the driver fails to start (e.g. a Snap browser that AppArmor blocks).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = common.read_state()
        if state.get("status") == "running":
            return True, ""
        if state.get("status") == "error":
            return False, str(state.get("message") or "SC.AI could not start.")
        if proc is not None and proc.poll() is not None:
            state = common.read_state()
            if state.get("status") == "error":
                return False, str(state.get("message") or "SC.AI could not start.")
            return False, (
                "SC.AI exited before it could start "
                f"(exit code {proc.returncode}). See the log: {common.log_file('runtime')}"
            )
        if progress is not None:
            try:
                progress.config(text="Starting SC.AI…")
                progress.update()
            except Exception:
                pass
        time.sleep(0.25)
    # Timed out while still starting (e.g. Selenium Manager downloading a
    # driver on the first run). Assume it is coming up and leave it running.
    return True, ""


def show_dialog(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except Exception:
        print(f"{title}: {message}")


def _gui_available() -> bool:
    try:
        import tkinter
    except ImportError:
        return False
    try:
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def run_gui(detected: list[browsers.DetectedBrowser]) -> int:
    import tkinter as tk
    import tkinter.ttk as ttk

    # SC Lite (Paper skin) palette, matching the frontend.
    BG = "#f4f1ea"
    CARD = "#fbfaf6"
    TEXT = "#24211c"
    MUTED = "#7c766a"
    BORDER = "#e4dfd3"
    ACCENT = "#1f6f6b"
    ACCENT_HOVER = "#17544f"

    root = tk.Tk()
    root.title("SC.AI")
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root.tk.call("wm", "class", ".", "sc-ai")
    except Exception:
        pass
    try:
        icon = tk.PhotoImage(file=str(launcher_icon_path()))
        root.iconphoto(True, icon)
    except Exception:
        pass

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#ffffff",
        font=("Sans", 11, "bold"),
        padding=(20, 9),
        borderwidth=0,
        focusthickness=0,
    )
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", "#9bb8b6")],
        foreground=[("disabled", "#ffffff")],
    )

    # Header.
    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=28, pady=(26, 8))
    tk.Label(header, text="SC.AI", font=("Sans", 28, "bold"), fg=ACCENT, bg=BG).pack(anchor="w")
    tk.Label(header, text="Your private AI browser workspace", font=("Sans", 11), fg=MUTED, bg=BG).pack(
        anchor="w", pady=(2, 0)
    )

    # Browser list card.
    card = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    card.pack(fill="x", padx=26, pady=(8, 12))
    tk.Label(card, text="BROWSER", bg=CARD, fg=MUTED, font=("Sans", 9, "bold")).pack(
        anchor="w", padx=16, pady=(14, 4)
    )

    saved_id = (common.read_config().get("selectedBrowser") or {}).get("id", "")
    initial = saved_id if any(b.id == saved_id for b in detected) else detected[0].id
    selected = tk.StringVar(value=initial)

    for browser in detected:
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=12, pady=5)
        radio = tk.Radiobutton(
            row,
            variable=selected,
            value=browser.id,
            text=browser.name,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=ACCENT,
            selectcolor=CARD,
            font=("Sans", 12, "bold"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        radio.pack(side="left", anchor="w")
        path_label = tk.Label(row, text=browser.path, bg=CARD, fg=MUTED, font=("Sans", 9))
        path_label.pack(side="left", padx=(30, 0), anchor="w")
        if browser.kind != "native":
            tk.Label(row, text=f"({browser.kind})", bg=CARD, fg=ACCENT, font=("Sans", 9, "bold")).pack(
                side="right", anchor="e"
            )
        # Clicking anywhere on the row selects it.
        for widget in (row, radio, path_label):
            widget.bind("<Button-1>", lambda _e, r=radio: r.select())

    # Settings card.
    settings = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    settings.pack(fill="x", padx=26, pady=(0, 12))
    settings_header = tk.Frame(settings, bg=CARD)
    settings_header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(settings_header, text="NVIDIA NIM", bg=CARD, fg=TEXT, font=("Sans", 11, "bold")).pack(side="left")
    key_status = tk.Label(settings_header, bg=CARD, fg=ACCENT, font=("Sans", 9))
    key_status.pack(side="right")
    key_row = tk.Frame(settings, bg=CARD)
    key_row.pack(fill="x", padx=16, pady=(0, 12))
    key_var = tk.StringVar(value=str(common.read_config().get("apiKey", "")))
    key_entry = tk.Entry(key_row, textvariable=key_var, show="•", relief="flat", bd=0,
                         bg="#f1eee6", fg=TEXT, insertbackground=TEXT, font=("Sans", 10))
    key_entry.pack(side="left", fill="x", expand=True, ipady=7)

    def refresh_key_status():
        key_status.config(text="Key saved" if common.read_config().get("apiKey") else "Not configured")

    def save_nim_key():
        config = common.read_config()
        value = key_var.get().strip()
        if value:
            config["apiKey"] = value
        else:
            config.pop("apiKey", None)
        common.write_config(config)
        refresh_key_status()

    ttk.Button(key_row, text="Save", command=save_nim_key).pack(side="left", padx=(8, 0))
    refresh_key_status()

    # Footer.
    footer = tk.Frame(root, bg=BG)
    footer.pack(fill="x", padx=26, pady=(0, 26))
    status_label = tk.Label(
        footer,
        text="You can change your browser anytime by launching SC.AI again.",
        bg=BG,
        fg=MUTED,
        font=("Sans", 9),
    )
    status_label.pack(side="left")
    launch_btn = ttk.Button(footer, text="Launch SC.AI", style="Accent.TButton")
    launch_btn.pack(side="right")

    result = {"code": 0}
    if not common.is_frozen():
        uninstall_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    else:
        uninstall_path = Path(getattr(sys, "_MEIPASS", ".")) / "uninstall.sh"

    def confirm_uninstall():
        from tkinter import messagebox
        if not messagebox.askyesno("Uninstall SC.AI", "Remove SC.AI from the application menu and delete its installed files?\\n\\nYour browser profiles and API key will be kept.", parent=root):
            return
        try:
            if common.is_frozen() and not uninstall_path.is_file():
                raise FileNotFoundError("The uninstaller is not installed")
            subprocess.Popen(["bash", str(uninstall_path)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            root.destroy()
        except OSError as error:
            messagebox.showerror("Uninstall failed", str(error), parent=root)

    def on_launch():
        chosen = next((b for b in detected if b.id == selected.get()), None)
        if chosen is None:
            return
        # Remember the choice for future launches.
        config = common.read_config()
        config["selectedBrowser"] = {
            "id": chosen.id,
            "name": chosen.name,
            "path": chosen.path,
            "kind": chosen.kind,
        }
        common.write_config(config)
        logger.info("launching SC.AI with %s (%s)", chosen.name, chosen.path)

        launch_btn.config(state="disabled")
        status_label.config(text="Starting SC.AI…", fg=ACCENT)
        status_label.update()
        root.update()

        # The runtime is guaranteed to have been built and installed before
        # this menu entry exists. Do not package or build from the launcher.
        proc, error = spawn_runtime(chosen)
        if proc is None:
            status_label.config(text=error, fg="#b3261e")
            status_label.update()
            root.destroy()
            result["code"] = 1
            return
        ok, message = wait_for_runtime(proc, progress=status_label)
        root.destroy()
        if not ok:
            show_dialog("SC.AI could not start", message)
            result["code"] = 1

    launch_btn.config(command=on_launch)
    uninstall_btn = ttk.Button(footer, text="Uninstall", command=confirm_uninstall)
    uninstall_btn.pack(side="right", padx=(0, 8))

    # Size + center on screen.
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    x = max((root.winfo_screenwidth() - w) // 2, 0)
    y = max((root.winfo_screenheight() - h) // 3, 0)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.mainloop()
    return result["code"]


def run_cli(detected: list[browsers.DetectedBrowser]) -> int:
    print("SC.AI — choose your browser")
    for index, browser in enumerate(detected, 1):
        print(f"  {index}) {browser.label}  ({browser.path})")
    saved = (common.read_config().get("selectedBrowser") or {}).get("id")
    default = next((i for i, b in enumerate(detected, 1) if b.id == saved), 1)
    try:
        raw = input(f"Select a browser [1-{len(detected)}] (default {default}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return 1
    try:
        index = int(raw) if raw else default
    except ValueError:
        print("Invalid selection.")
        return 1
    if not (1 <= index <= len(detected)):
        print("Invalid selection.")
        return 1
    chosen = detected[index - 1]
    config = common.read_config()
    config["selectedBrowser"] = {
        "id": chosen.id,
        "name": chosen.name,
        "path": chosen.path,
        "kind": chosen.kind,
    }
    common.write_config(config)
    print(f"Launching SC.AI with {chosen.name}…")
    proc, error = spawn_runtime(chosen)
    if proc is None:
        print(f"Failed to start SC.AI: {error}")
        return 1
    ok, message = wait_for_runtime(proc)
    if not ok:
        print(f"SC.AI failed to start: {message}")
        return 1
    print("SC.AI is running. Close the browser window to quit.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sc-ai-launcher", description="SC.AI launcher")
    parser.add_argument("--list", action="store_true", help="print detected browsers and exit")
    parser.add_argument("--console", action="store_true", help="use the console picker instead of the GUI")
    parser.add_argument("--settings", action="store_true", help="open launcher settings")
    parser.add_argument("--uninstall", action="store_true", help="open the uninstall confirmation")
    args = parser.parse_args(argv)

    # If a runtime is already running, do not start a second one: two
    # runtimes would fight over the same dedicated browser profile.
    probe = common.InstanceLock(common.lock_file())
    if not probe.try_acquire():
        logger.info("another SC.AI instance is already running")
        show_dialog(
            "SC.AI is already running",
            "SC.AI is already running.\n\nClose the SC.AI browser window, then launch SC.AI again.",
        )
        return 1
    probe.release()

    detected = browsers.detect_browsers()
    logger.info("detected browsers: %s", [(b.id, b.path) for b in detected])

    if args.uninstall and not _gui_available():
        print("Run the graphical launcher to confirm uninstall.")
        return 1

    if args.list:
        for browser in detected:
            print(f"{browser.id}\t{browser.name}\t{browser.path}\t{browser.kind}")
        return 0

    if not detected:
        logger.warning("no supported browsers detected")
        show_dialog("No supported browser found", NO_BROWSER_MESSAGE)
        return 1

    if args.console or not _gui_available():
        return run_cli(detected)
    return run_gui(detected)


if __name__ == "__main__":
    sys.exit(main())
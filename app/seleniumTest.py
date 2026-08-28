"""SC.AI runtime — local server + Selenium browser.

The runtime is the process that actually runs SC.AI. It is normally started
by the SC.AI launcher, which has already decided which browser to use:

    sc-ai-runtime --browser chrome --browser-path /usr/bin/google-chrome-stable

What it does
------------
1. Start a local HTTP server bound to 127.0.0.1 on a random free port
   (ThreadingHTTPServer(("127.0.0.1", 0), ...)).
2. Serve the SC.AI frontend (index.html) and proxy NVIDIA NIM requests.
3. Launch the chosen browser through Selenium with a dedicated, persistent
   SC.AI profile (never the user's normal browser profile).
4. Exit cleanly when the browser window is closed, shutting down the server.

The runtime deliberately does NOT decide which browser to use — that is the
launcher's job. If --browser is omitted it falls back to the browser the
launcher last saved in the config, purely as a development convenience.

Security notes
--------------
* The server binds to 127.0.0.1 only and serves no CORS headers, so only the
  SC.AI page itself can talk to it. The browser profile directory is NEVER
  served over HTTP and is never sent anywhere.
* The NVIDIA API key is stored in the SC.AI config file (chmod 0600) and is
  only used to authenticate NVIDIA requests from the local server.
"""

import argparse
import atexit
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if not getattr(sys, "frozen", False):
    # Allow `python app/seleniumTest.py` from a source checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import browsers, scai_common as common

NVIDIA_API = "https://integrate.api.nvidia.com/v1"
BUILD_VERSION = "scai-runtime-2026-08-27-v1"

if getattr(sys, "frozen", False):
    BASE_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
else:
    BASE_PATH = Path(__file__).parent  # directory containing index.html

logger = common.setup_logger("runtime")

# Filled in by main() before the server starts; exposed via /api/info so the
# frontend can tell the user which browser/profile it is running on.
RUNTIME_INFO: dict[str, str] = {}


class AppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/config":
            self.send_json({"hasApiKey": bool(common.read_config().get("apiKey"))})
            return
        if route == "/api/config/key":
            self.send_json({"apiKey": common.read_config().get("apiKey", "")})
            return
        if route == "/api/models":
            self.proxy_models()
            return
        if route == "/api/info":
            self.send_json(RUNTIME_INFO)
            return
        if route == "/":
            try:
                content = (BASE_PATH / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except OSError:
                self.send_error(404, "index.html not found")
            return
        self.send_error(404)

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/config":
            self.save_config()
            return
        if route == "/api/chat":
            self.proxy_chat()
            return
        self.send_error(404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def save_config(self):
        try:
            payload = json.loads(self.read_body() or b"{}")
            api_key = str(payload.get("apiKey", "")).strip()
            config = common.read_config()
            if api_key:
                config["apiKey"] = api_key
            else:
                config.pop("apiKey", None)
            common.write_config(config)
            self.send_json({"saved": True, "hasApiKey": bool(api_key)})
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self.send_json({"saved": False, "error": str(error)}, 400)

    def proxy_models(self):
        api_key = common.read_config().get("apiKey", "")
        if not api_key:
            self.send_json({"error": "No NVIDIA API key saved"}, 401)
            return
        try:
            request = Request(
                f"{NVIDIA_API}/models", headers={"Authorization": f"Bearer {api_key}"}
            )
            with urlopen(request, timeout=30) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except (HTTPError, URLError, OSError) as error:
            self.send_json({"error": f"NVIDIA model request failed: {error}"}, 502)

    def proxy_chat(self):
        api_key = common.read_config().get("apiKey", "")
        if not api_key:
            self.send_json({"error": "No NVIDIA API key saved"}, 401)
            return
        try:
            request = Request(
                f"{NVIDIA_API}/chat/completions",
                data=self.read_body(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )
            with urlopen(request, timeout=300) as response:
                logger.info("NVIDIA upstream chat status %s", response.status)
                upstream_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in upstream_type:
                    # Stream SSE line-by-line. Reading 8KB chunks would make
                    # http.client accumulate many events before returning, so
                    # the browser would see the whole reply arrive at once.
                    # Each readline() returns as soon as a line is available,
                    # so tokens reach the browser as they are generated.
                    self.send_response(response.status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        # Normalize CRLF to LF so events are separated by \n\n.
                        line = line.replace(b"\r\n", b"\n")
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                else:
                    # Non-streaming JSON response: forward with the upstream
                    # content type so the page's JSON fallback path handles it.
                    body = response.read()
                    self.send_response(response.status)
                    self.send_header("Content-Type", upstream_type or "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            logger.info("NVIDIA upstream error %s: %s", error.code, detail[:300])
            self.send_json({"error": detail or str(error)}, error.code)
        except (URLError, OSError) as error:
            logger.info("NVIDIA upstream failure: %s", error)
            self.send_json({"error": f"NVIDIA chat request failed: {error}"}, 502)

    def send_json(self, value, status=200):
        content = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _create_chromium(browser_path: str, profile_path: Path):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    options = ChromeOptions()
    if browser_path:
        options.binary_location = browser_path
    # Dedicated SC.AI profile. Chrome creates <profile>/Default inside it;
    # cookies, sessions, history and storage all persist between launches.
    options.add_argument(f"--user-data-dir={profile_path}")
    options.add_argument("--disable-dev-shm-usage")
    if _running_as_root():
        options.add_argument("--no-sandbox")
    return webdriver.Chrome(options=options)


def _create_firefox(browser_path: str, profile_path: Path):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options as FirefoxOptions

    options = FirefoxOptions()
    if browser_path:
        options.binary_location = browser_path
    # Use the persistent SC.AI profile IN PLACE. We deliberately avoid
    # webdriver.FirefoxProfile: it copies the profile to a temporary
    # directory that Selenium deletes on quit, which would wipe every login
    # session. Passing -profile keeps Firefox working directly on the SC.AI
    # profile directory.
    options.add_argument("-profile")
    options.add_argument(str(profile_path))
    return webdriver.Firefox(options=options)


def create_driver(driver_kind: str, browser_path: str, profile_path: Path):
    try:
        if driver_kind == "firefox":
            return _create_firefox(browser_path, profile_path)
        return _create_chromium(browser_path, profile_path)
    except ImportError as error:
        raise RuntimeError(
            "Selenium is not installed. Install it with: pip install -r requirements.txt"
        ) from error


def _clear_firefox_stale_locks(profile_path: Path) -> None:
    """Remove Firefox profile lock files left behind by a crash.

    Safe because the SC.AI single-instance lock guarantees no other process
    is using this profile right now.
    """
    for name in ("lock", ".parentlock"):
        try:
            (profile_path / name).unlink(missing_ok=True)
        except OSError:
            pass


def resolve_browser(args) -> tuple[str | None, str | None]:
    """Decide which browser to launch (the launcher's choice wins)."""
    browser_id = args.browser
    browser_path = args.browser_path
    if not browser_id:
        saved = common.read_config().get("selectedBrowser") or {}
        if saved.get("id"):
            browser_id = saved["id"]
            browser_path = browser_path or saved.get("path")
    if not browser_id:
        return None, None
    # Always re-resolve the selected browser at runtime. This handles
    # distro launcher scripts and Firefox installations whose real binary is
    # not the path originally saved in config.json.
    detected = browsers.detect_browser(browser_id)
    if detected:
        browser_path = detected.path
    elif not browser_path or not os.path.isfile(browser_path):
        browser_path = None
    return browser_id, browser_path


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="sc-ai-runtime", description="SC.AI Selenium runtime")
    parser.add_argument(
        "--browser",
        choices=[spec.id for spec in browsers.BROWSER_SPECS],
        help="browser id: chrome, chromium or firefox",
    )
    parser.add_argument("--browser-path", help="full path to the browser executable")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger.info(
        "SC.AI %s starting (browser=%s path=%s)", BUILD_VERSION, args.browser, args.browser_path
    )

    common.ensure_private_dir(common.app_data_dir())
    common.ensure_private_dir(common.profiles_dir())

    # Single-instance lock: only one runtime may run at a time so the
    # dedicated browser profile is never opened by two processes at once.
    lock = common.InstanceLock(common.lock_file())
    if not lock.try_acquire():
        message = "SC.AI is already running. Close the SC.AI browser window first."
        logger.info(message)
        common.write_state({"status": "error", "message": message})
        return 1
    atexit.register(lock.release)

    browser_id, browser_path = resolve_browser(args)
    if not browser_id:
        common.write_state(
            {
                "status": "error",
                "message": "No browser was selected. Launch SC.AI from the launcher instead.",
            }
        )
        logger.error("no browser selected")
        return 1
    spec = browsers.get_spec(browser_id)
    if spec is None:
        common.write_state({"status": "error", "message": f"Unknown browser id: {browser_id!r}"})
        logger.error("unknown browser id %r", browser_id)
        return 1
    if not browser_path:
        common.write_state(
            {
                "status": "error",
                "message": (
                    f"{spec.name} is not installed on this system. "
                    "Choose another browser in the SC.AI launcher."
                ),
            }
        )
        logger.error("browser %s not detected", browser_id)
        return 1
    if not os.access(browser_path, os.X_OK):
        common.write_state(
            {"status": "error", "message": f"Browser is not runnable: {browser_path}"}
        )
        logger.error("browser not executable: %s", browser_path)
        return 1

    profile_path = common.profile_dir(browser_id)
    common.ensure_private_dir(profile_path)
    if spec.driver_kind == "firefox":
        _clear_firefox_stale_locks(profile_path)

    # --- Local HTTP server on a random free port ---------------------------
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    except OSError as error:
        common.write_state(
            {"status": "error", "message": f"Could not start the local server: {error}"}
        )
        logger.error("server start failed: %s", error)
        return 1
    port = server.server_address[1]
    RUNTIME_INFO.update(
        {
            "browser": browser_id,
            "browserName": spec.name,
            "profile": str(profile_path),
            "version": BUILD_VERSION,
        }
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    common.write_state({"status": "starting", "browser": browser_id, "port": port, "pid": os.getpid()})
    logger.info("local server up at 127.0.0.1:%d", port)

    try:
        from selenium.common.exceptions import WebDriverException
    except ImportError:  # pragma: no cover - selenium is bundled
        WebDriverException = Exception

    driver = None
    try:
        driver = create_driver(spec.driver_kind, browser_path, profile_path)
        url = f"http://127.0.0.1:{port}/"
        driver.get(url)
        common.write_state(
            {"status": "running", "browser": browser_id, "port": port, "pid": os.getpid()}
        )
        logger.info("SC.AI opened in %s", spec.name)
        # Stay alive until the user closes the browser window. Polling the
        # URL is the standard way to notice that the window went away.
        while True:
            try:
                _ = driver.current_url
                time.sleep(0.5)
            except WebDriverException:
                logger.info("browser window closed by the user")
                break
    except KeyboardInterrupt:
        logger.info("interrupted")
    except Exception as error:
        logger.exception("runtime error")
        detail = str(error)
        if spec.driver_kind == "firefox" and "not a Firefox executable" in detail:
            detail = (
                f"The selected Firefox path is not a Firefox executable: {browser_path}. "
                "Install Firefox from your distribution, or choose the native Firefox "
                "executable rather than a package-manager wrapper."
            )
        common.write_state({"status": "error", "message": detail})
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logger.exception("error while quitting the driver")
        try:
            server.shutdown()
        except Exception:
            pass
        common.clear_state()
        logger.info("SC.AI runtime exited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
""" build command: cd ~/Documents/coding/seleniumTest
venv/bin/pyinstaller --onefile --runtime-tmpdir ~/Downloads --collect-submodules=selenium --collect-all selenium --clean seleniumTest.py --add-data "index.html:."

test command: ./venv/bin/python3 seleniumTest.py
"""

import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

BUILD_VERSION = "scai-launcher-2026-08-27-v8"
NVIDIA_API = "https://integrate.api.nvidia.com/v1"


def app_data_path():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SC.AI"
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc-ai"
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SC.AI"


CONFIG_DIR = app_data_path()
CONFIG_FILE = CONFIG_DIR / "config.json"


def read_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(value):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = CONFIG_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(value, file)
    temporary_file.replace(CONFIG_FILE)
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


class AppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/config":
            self.send_json({"hasApiKey": bool(read_config().get("apiKey"))})
            return
        if route == "/api/config/key":
            self.send_json({"apiKey": read_config().get("apiKey", "")})
            return
        if route == "/api/models":
            self.proxy_models()
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
            config = read_config()
            if api_key:
                config["apiKey"] = api_key
            else:
                config.pop("apiKey", None)
            write_config(config)
            self.send_json({"saved": True, "hasApiKey": bool(api_key)})
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self.send_json({"saved": False, "error": str(error)}, 400)

    def proxy_models(self):
        api_key = read_config().get("apiKey", "")
        if not api_key:
            self.send_json({"error": "No NVIDIA API key saved"}, 401)
            return
        try:
            request = Request(f"{NVIDIA_API}/models", headers={"Authorization": f"Bearer {api_key}"})
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
        api_key = read_config().get("apiKey", "")
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
                print(f"[NVIDIA upstream] chat status {response.status}")
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
            print(f"[NVIDIA upstream] error {error.code}: {detail[:300]}")
            self.send_json({"error": detail or str(error)}, error.code)
        except (URLError, OSError) as error:
            print(f"[NVIDIA upstream] failure: {error}")
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


def browser_candidates():
    """Yield (kind, path) for each installed browser, best first."""
    definitions = (
        (
            "chrome",
            ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "chrome"),
            (
                "%PROGRAMFILES%\\Google\\Chrome\\Application\\chrome.exe",
                "%PROGRAMFILES(X86)%\\Google\\Chrome\\Application\\chrome.exe",
                "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe",
            ),
            ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
        ),
        (
            "edge",
            ("microsoft-edge", "microsoft-edge-stable", "msedge"),
            (
                "%PROGRAMFILES%\\Microsoft\\Edge\\Application\\msedge.exe",
                "%PROGRAMFILES(X86)%\\Microsoft\\Edge\\Application\\msedge.exe",
            ),
            ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
        ),
        (
            "brave",
            ("brave-browser", "brave"),
            ("%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",),
            ("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",),
        ),
        (
            "firefox",
            ("firefox",),
            (
                "%PROGRAMFILES%\\Mozilla Firefox\\firefox.exe",
                "%PROGRAMFILES(X86)%\\Mozilla Firefox\\firefox.exe",
            ),
            ("/Applications/Firefox.app/Contents/MacOS/firefox",),
        ),
        (
            "opera",
            ("opera",),
            ("%PROGRAMFILES%\\Opera\\launcher.exe",),
            ("/Applications/Opera.app/Contents/MacOS/Opera",),
        ),
    )
    for kind, names, windows_paths, macos_paths in definitions:
        found = False
        for name in names:
            path = shutil.which(name)
            if path:
                yield kind, path
                found = True
                break
        if found:
            continue
        if sys.platform == "win32":
            for template in windows_paths:
                path = os.path.expandvars(template)
                if os.path.exists(path):
                    yield kind, path
                    break
        elif sys.platform == "darwin":
            for path in macos_paths:
                if os.path.exists(path):
                    yield kind, path
                    break


def launch_browser(url):
    """Launch the first installed browser and open url in its main tab."""
    for kind, path in browser_candidates():
        try:
            if kind == "firefox":
                from selenium.webdriver.firefox.options import Options as FirefoxOptions

                options = FirefoxOptions()
                if path:
                    options.binary_location = path
                driver = webdriver.Firefox(options=options)
            elif kind == "edge":
                from selenium.webdriver.edge.options import Options as EdgeOptions

                options = EdgeOptions()
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                if path:
                    options.binary_location = path
                driver = webdriver.Edge(options=options)
            elif kind == "opera":
                from selenium.webdriver.opera.options import Options as OperaOptions

                options = OperaOptions()
                if path:
                    options.binary_location = path
                driver = webdriver.Opera(options=options)
            else:  # chrome / brave (Chromium-based)
                options = Options()
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                if path:
                    options.binary_location = path
                driver = webdriver.Chrome(options=options)
            print(f"[browser] {kind} ({path or 'auto-detected'})")
            driver.get(url)
            return driver
        except Exception as error:
            print(f"[browser] {kind} unavailable: {error}")
    raise RuntimeError("No supported browser found. Install Chrome, Chromium, Edge, Brave, Firefox, or Opera.")


if getattr(sys, "frozen", False):
    BASE_PATH = Path(sys._MEIPASS)
else:
    BASE_PATH = Path(__file__).parent.absolute()

server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

driver = None
try:
    print(f"SC.AI launcher {BUILD_VERSION}")
    print(f"Local config: {CONFIG_FILE}")
    driver = launch_browser(f"http://127.0.0.1:{port}/")
    main_tab = driver.current_window_handle
    print("SC.AI is running as a desktop-style browser window.")
    print("Close the SC.AI tab to quit the whole window.")
    while True:
        try:
            if main_tab not in driver.window_handles:
                print("\nSC.AI tab was closed; closing the browser window.")
                break
            time.sleep(0.5)
        except WebDriverException:
            print("\nBrowser window was closed by the user.")
            break
    driver.quit()
    server.shutdown()
    sys.exit(0)
except KeyboardInterrupt:
    if driver:
        driver.quit()
    server.shutdown()
    sys.exit(0)
except Exception as error:
    print(f"An error occurred: {error}")
    server.shutdown()
    sys.exit(1)

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
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

BUILD_VERSION = "scai-launcher-2026-08-27-v4"


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
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/config":
            self.send_json({"hasApiKey": bool(read_config().get("apiKey"))})
            return
        if route == "/api/config/key":
            self.send_json({"apiKey": read_config().get("apiKey", "")})
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
        if urlparse(self.path).path != "/api/config":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
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


def find_browser():
    browsers = ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome", "chrome")
    return next((path for name in browsers if (path := shutil.which(name))), None)


def build_options(browser_path):
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    if browser_path:
        options.binary_location = browser_path
    return options


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
    driver = webdriver.Chrome(options=build_options(find_browser()))
    driver.get(f"http://127.0.0.1:{port}/")
    while True:
        try:
            _ = driver.current_url
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

""" build command: cd ~/Documents/coding/seleniumTest
venv/bin/pyinstaller --onefile --runtime-tmpdir ~/Downloads --collect-submodules=selenium --collect-all selenium --clean seleniumTest.py --add-data "index.html:."

test command: ./venv/bin/python3 seleniumTest.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

BUILD_VERSION = "scai-launcher-2026-08-27-v3"


def get_app_data_path():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SC.AI"
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc-ai"
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SC.AI"


def create_profile_path():
    try:
        profile_path = get_app_data_path() / "browser-profile"
        profile_path.mkdir(parents=True, exist_ok=True)
        probe = profile_path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return profile_path.resolve()
    except OSError as error:
        print(f"Persistent browser storage unavailable: {error}")
        return None


def create_temporary_profile():
    """Create a Chrome profile in a writable directory with a real path."""
    return Path(tempfile.mkdtemp(prefix="scai-chrome-", dir=tempfile.gettempdir())).resolve()


def find_browser():
    browsers = ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome", "chrome")
    return next((path for name in browsers if (path := shutil.which(name))), None)


def build_options(browser_path, profile_path=None):
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--allow-file-access-from-files")
    if profile_path:
        chrome_options.add_argument(f"--user-data-dir={profile_path}")
        chrome_options.add_argument("--profile-directory=Default")
    if browser_path:
        chrome_options.binary_location = browser_path
    return chrome_options


browser_path = find_browser()
if getattr(sys, "frozen", False):
    base_path = Path(sys._MEIPASS)
else:
    base_path = Path(__file__).parent.absolute()

html_file_path = base_path / "index.html"
driver = None

try:
    print(f"SC.AI launcher {BUILD_VERSION}")
    persistent_profile = create_profile_path()
    try:
        driver = webdriver.Chrome(options=build_options(browser_path, persistent_profile))
    except WebDriverException as first_error:
        if persistent_profile is None:
            raise
        print(f"Saved Chrome profile could not be opened; retrying with a temporary profile: {first_error}")
        temporary_profile = create_temporary_profile()
        driver = webdriver.Chrome(options=build_options(browser_path, temporary_profile))

    driver.get(html_file_path.as_uri())
    while True:
        try:
            _ = driver.current_url
            time.sleep(0.5)
        except WebDriverException:
            print("\nBrowser window was closed by the user.")
            break
    driver.quit()
    sys.exit(0)

except KeyboardInterrupt:
    print("\nScript interrupted by user (Ctrl+C). Exiting...")
    if driver:
        driver.quit()
    sys.exit(0)
except Exception as error:
    print(f"An error occurred: {error}")
    sys.exit(1)

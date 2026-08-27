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


def get_app_data_path():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SC.AI"
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc-ai"
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SC.AI"


def create_profile_path():
    """Return a writable persistent profile path, or None if unavailable."""
    try:
        app_data_path = get_app_data_path()
        app_data_path.mkdir(parents=True, exist_ok=True)
        profile_path = app_data_path / "browser-profile"
        profile_path.mkdir(parents=True, exist_ok=True)
        probe = profile_path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return profile_path
    except (OSError, PermissionError) as error:
        print(f"Persistent browser storage unavailable: {error}")
        return None


possible_browsers = [
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
    "google-chrome",
    "chrome",
]

if getattr(sys, "frozen", False):
    base_path = Path(sys._MEIPASS)
else:
    base_path = Path(__file__).parent.absolute()

html_file_path = base_path / "index.html"
file_url = html_file_path.as_uri()

# PyInstaller extracts bundled files into a temporary directory. Keep the
# browser profile outside that directory so localStorage survives relaunches.
app_data_path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SC.AI"
if sys.platform == "darwin":
    app_data_path = Path.home() / "Library" / "Application Support" / "SC.AI"
elif sys.platform.startswith("linux"):
    app_data_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc-ai"
app_data_path.mkdir(parents=True, exist_ok=True)

options.add_argument(f"--user-data-dir={app_data_path / 'browser-profile'}")

browser_path = None
for browser in possible_browsers:
    found_path = shutil.which(browser)
    if found_path:
        browser_path = found_path
        break


try:
    try:
        driver = webdriver.Chrome(options=build_options(persistent_profile))
    except WebDriverException as first_error:
        if persistent_profile is None:
            raise
        print(f"Could not open the saved Chrome profile; using a temporary profile: {first_error}")
        temporary_profile = Path(tempfile.mkdtemp(prefix="scai-chrome-"))
        driver = webdriver.Chrome(options=build_options(temporary_profile))

    driver.get(file_url)

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
    try:
        driver.quit()
    except (NameError, WebDriverException):
        pass
    sys.exit(0)
except Exception as error:
    print(f"An error occurred: {error}")
    sys.exit(1)

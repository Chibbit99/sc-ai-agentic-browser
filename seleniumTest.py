""" build command: cd ~/Documents/coding/seleniumTest
venv/bin/pyinstaller --onefile --runtime-tmpdir ~/Downloads --collect-submodules=selenium --collect-all selenium --clean seleniumTest.py --add-data "index.html:."

test command: ./venv/bin/python3 seleniumTest.py
"""


import shutil
import sys
import os
import time
from pathlib import Path
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--allow-file-access-from-files")

possible_browsers = [
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
    "google-chrome",
    "chrome",
]

# Flawless pathing using Pathlib
if getattr(sys, 'frozen', False):
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

if browser_path:
    options.binary_location = browser_path

try:
    driver = webdriver.Chrome(options=options)
    driver.get(file_url)

    # Clean Monitoring Loop
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
    except:
        pass
    sys.exit(0)
except Exception as e:
    print(f"An error occurred: {e}")
    sys.exit(1)

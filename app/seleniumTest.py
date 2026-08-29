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

Agentic capabilities
--------------------
The runtime exposes browser automation tools that allow the AI to control
the browser via HTTP endpoints:
- GET  /api/tabs            List all open tabs
- POST /api/tool/open-tab   Open a new tab with a URL
- POST /api/tool/read-tab   Read the text/HTML content of a tab
- POST /api/tool/search-tab Search for a string in a tab
- POST /api/tool/run-js     Run JavaScript on a tab
- POST /api/tool/click-element  Click an element on a page
- POST /api/tool/type-text      Type text into an input on a page
"""

import argparse
import atexit
import json
import logging
import os
import re
import sys
from html.parser import HTMLParser
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
BUILD_VERSION = "scai-runtime-2026-08-28-v2"

if getattr(sys, "frozen", False):
    BASE_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
else:
    BASE_PATH = Path(__file__).parent  # directory containing index.html

logger = common.setup_logger("runtime")

# Filled in by main() before the server starts; exposed via /api/info so the
# frontend can tell the user which browser/profile it is running on.
RUNTIME_INFO: dict[str, str] = {}

# Global reference to the Selenium driver, shared with the HTTP handler.
_DRIVER = None
_DRIVER_LOCK = threading.Lock()


# ============================================================
# System prompt for agentic browser control
# ============================================================

SYSTEM_PROMPT = """You are SC.AI, an agentic browser assistant. You control the user's browser using tools.

WORKFLOW:
1. First call list_tabs to discover the open tabs. From those results pick the exact tab you need and always pass its tab_index to tab tools.
2. Use the reasoning tool to think step by step before complex actions and after each tool result.
3. For each task decide the right sequence (open -> read -> search -> reason -> click/type -> reason -> summarize).
4. When the task is complete, reply to the user with a clear summary in normal chat text.

RULES:
- ALWAYS pass tab_index for read_tab, search_tab, run_javascript, click_element, and type_text. Take the index from a recent list_tabs result — never assume index 0 is the right tab.
- Prefer read_tab with format "text" (clean visible text). Use "html" only when you need structure, links, or attributes — HTML output has no <head>, scripts, or styles.
- If a tool result is confusing or incomplete, run_javascript to inspect the page further (e.g. query the DOM) rather than guessing.
- If running into an interactive page, combine read/search with click_element and type_text to navigate.
- The reasoning tool is only for your internal analysis. Never use it to answer the user — final answers are normal chat text.

TOOLS:
- reasoning: Think through the task step by step. Pass your analysis as the argument. Call it before planning actions and after every tool result that changes what you do next. It does not interact with the browser or the user.
- open_tab: Open a URL in a new tab. Always pass a full URL.
- read_tab: Read the page content. Pass tab_index (0-based) to target a specific tab and format "text" or "html". Returns page content.
- search_tab: Search a page for a string. Pass query and tab_index. Default searches visible page text; pass format "html" to search the raw HTML source (includes attributes, links and script-generated markup). If a text search finds nothing, it automatically falls back to searching the HTML.
- run_javascript: Execute JS on a page and return the result. Pass code and tab_index. The value of the last expression in the code is returned to you (objects and page elements are described, not just "undefined"), and anything the code logs with console.log/console.warn/console.error is also returned to you.
- list_tabs: List all open tabs with titles and URLs.
- switch_tab: Switch to a specific tab by index.
- close_tab: Close a browser tab by its index. Pass tab_index. The SC.AI interface tab cannot be closed.
- click_element: Click an element on the page. Pass selector (CSS selector, or XPath starting with //) and optionally index for the nth match, and tab_index.
- type_text: Type text into an input, textarea, or contenteditable. Pass selector, text, and tab_index; pass clear=false to append instead of replacing.
"""

# Tool definitions for NVIDIA NIM function calling
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "reasoning",
            "description": "Think through the task step by step. Use it to plan before acting and to evaluate each tool result. Does not touch the browser or the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Your step-by-step analysis and plan"
                    }
                },
                "required": ["reasoning"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_tab",
            "description": "Open a new browser tab with the specified URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open in the new tab"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_tab",
            "description": "Read the content of the current browser tab",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "description": "Return format: 'text' for visible text only, 'html' for full HTML source",
                        "default": "text"
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Index of the tab to read (0-based). Defaults to the currently active tab.",
                        "default": -1
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_tab",
            "description": "Search for a string within the current browser tab's content",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The string to search for (case-insensitive)"
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "description": "Search 'text' (visible page text, default) or 'html' (raw HTML source including attributes). Falls back to HTML automatically when text yields no match.",
                        "default": "text"
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Index of the tab to search (0-based). Defaults to the currently active tab.",
                        "default": -1
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_javascript",
            "description": "Execute JavaScript code on the current browser tab and return the result",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "JavaScript code to execute. The return value will be sent back."
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Index of the tab to run JS on (0-based). Defaults to the currently active tab.",
                        "default": -1
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tabs",
            "description": "List all open browser tabs with their titles and URLs",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "switch_tab",
            "description": "Switch to a specific browser tab by its index",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "The 0-based index of the tab to switch to"
                    }
                },
                "required": ["tab_index"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_tab",
            "description": "Close a browser tab by its index. The SC.AI interface tab cannot be closed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "The 0-based index of the tab to close"
                    }
                },
                "required": ["tab_index"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click an element on the page (link, button, checkbox...) using a CSS selector",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element (or XPath starting with //)"
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which matching element to click if the selector matches several (0-based)",
                        "default": 0
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Index of the tab to act on (0-based). Defaults to the tab the user is viewing.",
                        "default": -1
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into an input field, textarea, or contenteditable element",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the field (or XPath starting with //)"
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to type"
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Clear existing content first (default true)",
                        "default": True
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which matching element to use if the selector matches several (0-based)",
                        "default": 0
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Index of the tab to act on (0-based). Defaults to the tab the user is viewing.",
                        "default": -1
                    }
                },
                "required": ["selector", "text"]
            }
        }
    }
]


class _CleanHTMLParser(HTMLParser):
    """Remove scripts/styles and all inline style/event attributes."""

    _blocked = {"script", "style", "noscript", "template", "svg"}
    _events = re.compile(r"^on", re.IGNORECASE)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._blocked:
            self.depth += 1
            return
        if self.depth:
            return
        clean_attrs = []
        for name, value in attrs:
            if name.lower() == "style" or self._events.match(name):
                continue
            clean_attrs.append((name, value))
        attr_text = "".join(
            " " + name + (f'="{str(value).replace(chr(34), "&quot;")}"' if value is not None else "")
            for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{attr_text}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if not self.depth and tag.lower() not in self._blocked:
            self.parts[-1] = self.parts[-1][:-1] + " />"

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._blocked:
            self.depth = max(0, self.depth - 1)
            return
        if not self.depth:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)

    def handle_comment(self, _data):
        pass


def _clean_html(source):
    # Drop the <head> entirely (link/meta/preload/junk) and, when a <body>
    # exists, keep only its contents so HTML reads are dominated by real page
    # content rather than asset plumbing.
    source = re.sub(
        r"<head[^>]*>[\s\S]*?</head>", "", source, flags=re.IGNORECASE
    )
    body_match = re.search(r"<body[^>]*>([\s\S]*?)</body>", source, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        source = body_match.group(1)
    parser = _CleanHTMLParser()
    try:
        parser.feed(source)
        parser.close()
        return "".join(parser.parts)
    except Exception:
        return re.sub(r"<script\\b[^>]*>[\\s\\S]*?</script\\s*>|<style\\b[^>]*>[\\s\\S]*?</style\\s*>", "", source, flags=re.IGNORECASE)


def _active_tab_handle(driver):
    """Return the handle of the tab the user is actually viewing.

    Selenium's current_window_handle becomes stale when the user clicks a
    different tab in the browser chrome, so detect the visible document
    instead. Falls back to the first live window.
    """
    handles = driver.window_handles
    if not handles:
        return None
    if len(handles) == 1:
        return handles[0]
    fallback = None
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            if fallback is None:
                fallback = handle
            if driver.execute_script("return document.visibilityState") == "visible":
                return handle
        except Exception:
            continue
    return fallback


def _payload_tab_index(payload):
    """Coerce a payload tab_index field to an int, defaulting to -1.

    The model may pass 1, "1" or 1.0; 1.0 would otherwise fail Python's
    isinstance(int) check in _resolve_tab and silently target the wrong tab.
    """
    try:
        value = payload.get("tab_index", -1)
        return int(value)
    except (TypeError, ValueError):
        return -1


def _resolve_tab(driver, tab_index):
    """Switch the driver to the requested tab index, or the user's active tab."""
    handles = driver.window_handles
    if (
        tab_index is not None
        and isinstance(tab_index, int)
        and 0 <= tab_index < len(handles)
    ):
        driver.switch_to.window(handles[tab_index])
        return handles[tab_index]
    handle = _active_tab_handle(driver)
    if handle is not None:
        driver.switch_to.window(handle)
    return handle


def _find_element(driver, selector, index=0):
    """Find an element by CSS selector (or XPath when it starts with //)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    by = By.XPATH if selector.startswith("//") or selector.startswith("(") else By.CSS_SELECTOR
    try:
        elements = WebDriverWait(driver, 10).until(
            lambda d: d.find_elements(by, selector)
        )
    except Exception as error:
        raise RuntimeError(f"No element found for selector '{selector}'") from error
    if not elements:
        raise RuntimeError(f"No element found for selector '{selector}'")
    if index >= len(elements):
        raise RuntimeError(
            f"Selector '{selector}' matched {len(elements)} element(s) "
            f"but index {index} was requested"
        )
    element = elements[index]
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    except Exception:
        pass
    return element


def _flash_element(driver, element, color=None):
    """Glow an element the AI is about to interact with, so the action is
    obvious to the user. A soft ring that fades out automatically."""
    if color is None:
        color = "rgba(31, 111, 107, 0.85)"
    script = (
        "(function(el, color){"
        "  if (!el || !el.getBoundingClientRect) return;"
        "  var oldShadow = el.style.boxShadow, oldOutline = el.style.outline,"
        "      oldOffset = el.style.outlineOffset, oldTransition = el.style.transition;"
        "  el.style.transition = 'box-shadow .18s ease, outline .18s ease';"
        "  el.style.boxShadow = '0 0 0 4px ' + color + ', 0 0 22px 8px ' + color;"
        "  el.style.outline = '2px solid ' + color;"
        "  el.style.outlineOffset = '2px';"
        "  setTimeout(function(){"
        "    el.style.boxShadow = oldShadow; el.style.outline = oldOutline;"
        "    el.style.outlineOffset = oldOffset; el.style.transition = oldTransition;"
        "  }, 2000);"
        "})(arguments[0], arguments[1]);"
    )
    try:
        driver.execute_script(script, element, color)
    except Exception:
        pass


def _serialize_js_result(result, depth=0):
    """Turn a run_js result into a compact, readable string.

    Handles the values LLM-generated JS commonly returns (objects, arrays,
    DOM elements, deep/cyclic structures) that would otherwise make
    json.dumps blow up, so run_js reliably reports real info back to the AI.
    """
    if depth > 5:
        return "..."
    from selenium.webdriver.remote.webelement import WebElement
    if result is None:
        return "null"
    if isinstance(result, bool):
        return "true" if result else "false"
    if isinstance(result, (int, float)):
        return repr(result)
    if isinstance(result, str):
        return result
    if isinstance(result, WebElement):
        try:
            parts = [f"<{result.tag_name}"]
            for attr in ("id", "class", "name", "href", "type", "src", "aria-label", "title", "placeholder", "data-testid"):
                value = result.get_attribute(attr)
                if value:
                    parts.append(f' {attr}="{str(value)[:60]}"')
            text = (result.text or "").strip()[:120]
            if text:
                parts.append(f'>{text}')
            else:
                parts.append(">")
            return "".join(parts)
        except Exception:
            return "<element>"
    if isinstance(result, list):
        items = [_serialize_js_result(item, depth + 1) for item in result[:25]]
        if len(result) > 25:
            items.append(f"... (+{len(result) - 25} more)")
        return "[" + ", ".join(items) + "]"
    if isinstance(result, dict):
        items = []
        for key, value in list(result.items())[:25]:
            items.append(f"{key}: {_serialize_js_result(value, depth + 1)}")
        if len(result) > 25:
            items.append(f"... (+{len(result) - 25} more)")
        return "{" + ", ".join(items) + "}"
    try:
        return repr(result)
    except Exception:
        return str(result)


def _run_js_capture(driver, code):
    """Execute JS and capture any console.log/info/warn/error output.

    Selenium cannot read console output directly, so a console interceptor is
    installed on the page first, the user's script runs, and the collected
    lines are returned alongside the script's result value.
    """
    setup = (
        "(function(){"
        "  if (typeof window.__scaiLogs === 'undefined') {"
        "    window.__scaiLogs = [];"
        "    ['log','info','warn','error','debug'].forEach(function(lv){"
        "      var orig = console[lv].bind(console);"
        "      console[lv] = function(){"
        "        var parts = [];"
        "        for (var i = 0; i < arguments.length; i++){"
        "          var a = arguments[i];"
        "          if (a && typeof a === 'object'){"
        "            try { parts.push(JSON.stringify(a)); } catch (e) { parts.push(String(a)); }"
        "          } else { parts.push(String(a)); }"
        "        }"
        "        try { window.__scaiLogs.push(lv + ': ' + parts.join(' ')); } catch (e) {}"
        "        return orig.apply(console, arguments);"
        "      };"
        "    });"
        "  }"
        "  window.__scaiLogs.length = 0;"
        "  return true;"
        "})();"
    )
    driver.execute_script(setup)
    result = driver.execute_script(code)
    logs = driver.execute_script("return (window.__scaiLogs || []).slice();")
    return result, logs


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
        if route == "/api/tabs":
            self.handle_list_tabs()
            return
        if route == "/api/tools":
            # Return tool definitions for the frontend
            self.send_json({"tools": TOOL_DEFINITIONS, "system_prompt": SYSTEM_PROMPT})
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
        if route == "/api/tool/open-tab":
            self.handle_open_tab()
            return
        if route == "/api/tool/read-tab":
            self.handle_read_tab()
            return
        if route == "/api/tool/search-tab":
            self.handle_search_tab()
            return
        if route == "/api/tool/run-js":
            self.handle_run_js()
            return
        if route == "/api/tool/switch-tab":
            self.handle_switch_tab()
            return
        if route == "/api/tool/close-tab":
            self.handle_close_tab()
            return
        if route == "/api/tool/click-element":
            self.handle_click_element()
            return
        if route == "/api/tool/type-text":
            self.handle_type_text()
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
                    # Stream SSE line-by-line
                    self.send_response(response.status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        line = line.replace(b"\r\n", b"\n")
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                else:
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

    # ============================================================
    # Browser automation tool handlers
    # ============================================================

    def handle_list_tabs(self):
        """List all open browser tabs."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            with _DRIVER_LOCK:
                active_handle = _active_tab_handle(driver)
                tabs = []
                for i, handle in enumerate(driver.window_handles):
                    try:
                        driver.switch_to.window(handle)
                        title = driver.title
                        url = driver.current_url
                    except Exception:
                        title = "(loading)"
                        url = ""
                    tabs.append({
                        "index": i,
                        "handle": handle,
                        "url": url,
                        "title": title,
                        "active": handle == active_handle
                    })
                if active_handle:
                    try:
                        driver.switch_to.window(active_handle)
                    except Exception:
                        pass
            self.send_json({"tabs": tabs})
        except Exception as error:
            logger.exception("list_tabs error")
            self.send_json({"error": str(error)}, 500)

    def handle_switch_tab(self):
        """Switch to a specific tab by index."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                handle = _resolve_tab(driver, tab_index)
                if handle is None:
                    self.send_json({"error": "No tabs are open"}, 400)
                    return
                self.send_json({
                    "success": True,
                    "url": driver.current_url,
                    "title": driver.title
                })
        except Exception as error:
            logger.exception("switch_tab error")
            self.send_json({"error": str(error)}, 500)

    def handle_open_tab(self):
        """Open a new browser tab with the specified URL."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            url = payload.get("url", "").strip()
            if not url:
                self.send_json({"error": "url is required"}, 400)
                return
            if not url.startswith(("http://", "https://", "file://")):
                url = "https://" + url
            with _DRIVER_LOCK:
                before = set(driver.window_handles)
                driver.execute_script("window.open(arguments[0], '_blank');", url)
                # Wait for the new tab to appear, then switch to it.
                new_handle = None
                deadline = time.time() + 10
                while time.time() < deadline:
                    for handle in driver.window_handles:
                        if handle not in before:
                            new_handle = handle
                            break
                    if new_handle:
                        break
                    time.sleep(0.1)
                if new_handle:
                    driver.switch_to.window(new_handle)
                else:
                    driver.switch_to.window(driver.window_handles[-1])
                try:
                    from selenium.webdriver.support.ui import WebDriverWait
                    WebDriverWait(driver, 10).until(
                        lambda d: d.execute_script('return document.readyState') in ('complete', 'interactive')
                    )
                except Exception:
                    time.sleep(2)
            self.send_json({
                "result": f"Opened {url}\nTitle: {driver.title}",
                "success": True,
                "url": driver.current_url,
                "title": driver.title
            })
        except Exception as error:
            logger.exception("open_tab error")
            self.send_json({"error": str(error)}, 500)

    def handle_read_tab(self):
        """Read the content of a browser tab."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            fmt = payload.get("format", "text")
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                _resolve_tab(driver, tab_index)
                if fmt == "html":
                    content = _clean_html(driver.page_source)
                else:
                    content = driver.execute_script("return document.body.innerText;") or ""
                    if not content.strip():
                        time.sleep(1.0)
                        content = driver.execute_script("return document.body.innerText;") or ""
                title = driver.title
                url = driver.current_url
            truncated = content[:30000]
            if len(content) > 30000:
                truncated += f"\n\n[...truncated {len(content) - 30000} chars]"
            summary = f"Page: {title}\nURL: {url}\n\n{truncated}"
            self.send_json({
                "result": summary,
                "url": url,
                "title": title,
                "content": truncated,
                "format": fmt
            })
        except Exception as error:
            logger.exception("read_tab error")
            self.send_json({"error": str(error)}, 500)

    def handle_search_tab(self):
        """Search for a string within a browser tab (visible text or HTML)."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            query = payload.get("query", "").strip()
            if not query:
                self.send_json({"error": "query is required"}, 400)
                return
            fmt = payload.get("format", "text")
            tab_index = _payload_tab_index(payload)
            used = fmt
            with _DRIVER_LOCK:
                _resolve_tab(driver, tab_index)
                if fmt == "html":
                    text = _clean_html(driver.page_source)
                else:
                    text = driver.execute_script("return document.body.innerText;") or ""
                    if not text.strip():
                        time.sleep(1.0)
                        text = driver.execute_script("return document.body.innerText;") or ""
                    # If the string is not in the visible text, fall back to the
                    # HTML source: the match may live in an attribute, a link,
                    # or markup that never surfaces in innerText.
                    if query.lower() not in text.lower():
                        html_source = _clean_html(driver.page_source)
                        if query.lower() in html_source.lower():
                            text = html_source
                            used = "html"
                title = driver.title
                url = driver.current_url
            matches = []
            lower_text = text.lower()
            lower_query = query.lower()
            start = 0
            while True:
                pos = lower_text.find(lower_query, start)
                if pos == -1:
                    break
                ctx_start = max(0, pos - 100)
                ctx_end = min(len(text), pos + len(query) + 100)
                matches.append(text[ctx_start:ctx_end].strip())
                start = pos + 1
                if len(matches) >= 5:
                    break
            if not matches:
                result = f"No matches for '{query}' on {title} ({url})"
            else:
                where = "HTML" if used == "html" else "page text"
                result = f"Found {len(matches)} match(es) for '{query}' in {where} on {title} ({url}):\n"
                for i, m in enumerate(matches, 1):
                    result += f"{i}. ...{m}...\n"
            self.send_json({
                "result": result,
                "url": url,
                "title": title,
                "query": query,
                "format": used,
                "found": len(matches) > 0,
                "matches": matches
            })
        except Exception as error:
            logger.exception("search_tab error")
            self.send_json({"error": str(error)}, 500)

    def handle_run_js(self):
        """Run JavaScript on a browser tab."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            code = payload.get("code", "").strip()
            if not code:
                self.send_json({"error": "code is required"}, 400)
                return
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                _resolve_tab(driver, tab_index)
                result, logs = _run_js_capture(driver, code)
                title = driver.title
                url = driver.current_url
            result_str = _serialize_js_result(result)
            log_lines = [str(line) for line in logs if str(line).strip()]
            if log_lines:
                log_text = "\n".join(log_lines)[:8000]
                full = (
                    f"JS result from {title} ({url}):\n{result_str[:8000]}\n\n"
                    f"console output:\n{log_text}"
                )
            else:
                full = f"JS result from {title} ({url}):\n{result_str[:8000]}"
            self.send_json({
                "result": full,
                "url": url,
                "title": title,
                "js_result": result_str[:8000],
                "logs": "\n".join(log_lines)[:8000]
            })
        except Exception as error:
            logger.exception("run_js error")
            self.send_json({"error": str(error)}, 500)

    def handle_close_tab(self):
        """Close a browser tab by index (never the SC.AI interface tab)."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                handles = driver.window_handles
                if not handles:
                    self.send_json({"error": "No tabs are open"}, 400)
                    return
                if not (0 <= tab_index < len(handles)):
                    self.send_json({
                        "error": f"Invalid tab_index {tab_index}. Only {len(handles)} tab(s) are open (0-based)."
                    }, 400)
                    return
                handle = handles[tab_index]
                try:
                    driver.switch_to.window(handle)
                    title = driver.title
                    url = driver.current_url
                except Exception:
                    title, url = "(unavailable)", ""
                # Closing the tab that is actually hosting SC.AI would silently
                # kill the app, so disallow it.
                scai_url = f"http://127.0.0.1:{self.server.server_address[1]}/"
                if isinstance(url, str) and url.split("?", 1)[0].rstrip("/") == scai_url.rstrip("/"):
                    self.send_json({
                        "error": "Cannot close the SC.AI interface tab. Choose another tab_index."
                    }, 400)
                    return
                driver.close()
                time.sleep(0.3)
                remaining = driver.window_handles
                if remaining:
                    target = _active_tab_handle(driver) or remaining[0]
                    try:
                        driver.switch_to.window(target)
                    except Exception:
                        pass
            self.send_json({
                "success": True,
                "result": f"Closed tab #{tab_index} ({title}). {len(remaining)} tab(s) still open." if remaining else "Closed the only browser tab.",
                "closed": title,
                "remaining": len(remaining)
            })
        except Exception as error:
            logger.exception("close_tab error")
            self.send_json({"error": str(error)}, 500)

    def handle_click_element(self):
        """Click an element on the current page."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            selector = (payload.get("selector") or "").strip()
            index = int(payload.get("index", 0) or 0)
            if not selector:
                self.send_json({"error": "selector is required"}, 400)
                return
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                _resolve_tab(driver, tab_index)
                element = _find_element(driver, selector, index)
                _flash_element(driver, element)
                try:
                    element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", element)
                title = driver.title
                url = driver.current_url
            self.send_json({
                "result": f"Clicked '{selector}' on {title} ({url})",
                "success": True,
                "url": url,
                "title": title
            })
        except Exception as error:
            logger.exception("click_element error")
            self.send_json({"error": str(error)}, 500)

    def handle_type_text(self):
        """Type text into an input on the current page."""
        driver = _DRIVER
        if driver is None:
            self.send_json({"error": "Browser not ready"}, 503)
            return
        try:
            payload = json.loads(self.read_body() or b"{}")
            selector = (payload.get("selector") or "").strip()
            text = payload.get("text", "")
            clear = bool(payload.get("clear", True))
            index = int(payload.get("index", 0) or 0)
            if not selector:
                self.send_json({"error": "selector is required"}, 400)
                return
            if not isinstance(text, str):
                text = str(text)
            tab_index = _payload_tab_index(payload)
            with _DRIVER_LOCK:
                _resolve_tab(driver, tab_index)
                element = _find_element(driver, selector, index)
                if clear:
                    try:
                        element.clear()
                    except Exception:
                        pass
                _flash_element(driver, element)
                element.send_keys(text)
                title = driver.title
                url = driver.current_url
            self.send_json({
                "result": f"Typed {len(text)} character(s) into '{selector}' on {title}",
                "success": True,
                "url": url,
                "title": title
            })
        except Exception as error:
            logger.exception("type_text error")
            self.send_json({"error": str(error)}, 500)

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
    # Keep the profile persistent, but avoid Selenium's profile preference
    # marshalling. Snap Firefox can reject the generated preferences payload;
    # passing the profile to Firefox itself avoids that path entirely.
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
    for name in ("lock", ".parentlock", "parent.lock"):
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
    global _DRIVER
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
    if spec.driver_kind == "firefox":
        resolved = browsers._firefox_binary(browser_path)
        if not resolved:
            message = (
                f"Firefox was detected at {browser_path}, but that path is a package-manager "
                "wrapper, not the Firefox executable. Install the native Firefox package "
                "or ensure the real binary is under /usr/lib/firefox."
            )
            common.write_state({"status": "error", "message": message})
            logger.error(message)
            return 1
        browser_path = resolved

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
        _DRIVER = driver  # Expose driver to HTTP handlers for tool execution
        url = f"http://127.0.0.1:{port}/"
        driver.get(url)
        common.write_state(
            {"status": "running", "browser": browser_id, "port": port, "pid": os.getpid()}
        )
        logger.info("SC.AI opened in %s with agentic capabilities", spec.name)
        # Stay alive until the user closes the browser window. Polling the
        # URL is the standard way to notice that the window went away, but
        # the handle can go stale when the user closes or switches tabs, so
        # recover instead of quitting unless every window is gone.
        while True:
            try:
                _ = driver.current_url
                time.sleep(0.5)
            except WebDriverException:
                try:
                    if not driver.window_handles:
                        logger.info("browser window closed by the user")
                        break
                    with _DRIVER_LOCK:
                        handle = _active_tab_handle(driver)
                        if handle is None:
                            logger.info("browser window closed by the user")
                            break
                        driver.switch_to.window(handle)
                    logger.info("recovered from a stale or closed window handle")
                    time.sleep(0.5)
                except Exception:
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
                "Install Firefox from your distribution, or ensure the installed Snap "
                "contains its real Firefox binary. The package-manager wrapper itself "
                "cannot be passed to Selenium."
            )
        common.write_state({"status": "error", "message": detail})
        return 1
    finally:
        _DRIVER = None
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

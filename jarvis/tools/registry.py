"""Central registry: Anthropic tool-use schemas + dispatch to the actual
Python functions in apps.py / windows_control.py / system.py /
system_monitor.py / files.py / web.py.

To add a new tool: write the function, add a schema entry to TOOL_SCHEMAS,
and add a matching entry to TOOL_DISPATCH. That's it -- the brain's
tool-use loop and the schemas are decoupled from any single skill.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from jarvis.tools import apps, files, system, system_monitor, web, windows_control

logger = logging.getLogger("jarvis.tools.registry")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "open_app",
        "description": "Open/launch an application by name, e.g. 'notepad', 'chrome', 'spotify', or a full path.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Application name or path to launch."}},
            "required": ["name"],
        },
    },
    {
        "name": "close_app",
        "description": "Close/terminate all running processes matching a name, e.g. 'notepad', 'chrome'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Process/application name to close."}},
            "required": ["name"],
        },
    },
    {
        "name": "list_running_apps",
        "description": "List currently running application processes.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_windows",
        "description": "List titles of all currently open windows.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "focus_window",
        "description": "Bring a window to the foreground and give it focus, matched by a substring of its title.",
        "input_schema": {
            "type": "object",
            "properties": {"title_substring": {"type": "string", "description": "Substring of the target window's title."}},
            "required": ["title_substring"],
        },
    },
    {
        "name": "minimize_window",
        "description": "Minimize a window, matched by a substring of its title.",
        "input_schema": {
            "type": "object",
            "properties": {"title_substring": {"type": "string"}},
            "required": ["title_substring"],
        },
    },
    {
        "name": "maximize_window",
        "description": "Maximize a window, matched by a substring of its title.",
        "input_schema": {
            "type": "object",
            "properties": {"title_substring": {"type": "string"}},
            "required": ["title_substring"],
        },
    },
    {
        "name": "close_window",
        "description": "Close a window, matched by a substring of its title.",
        "input_schema": {
            "type": "object",
            "properties": {"title_substring": {"type": "string"}},
            "required": ["title_substring"],
        },
    },
    {
        "name": "set_volume",
        "description": "Set the system output volume to a percentage (0-100).",
        "input_schema": {
            "type": "object",
            "properties": {"percent": {"type": "number", "description": "Target volume, 0-100."}},
            "required": ["percent"],
        },
    },
    {
        "name": "get_volume",
        "description": "Get the current system output volume.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_screenshot",
        "description": "Capture a screenshot of the full screen and save it to disk.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lock_workstation",
        "description": "Lock the Windows workstation (requires the user's password to unlock again).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_files",
        "description": "Search for files by (partial) filename under the user's home directory, or a given root path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to match against filenames."},
                "root": {"type": "string", "description": "Optional directory to search under. Defaults to the user's home directory."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_file",
        "description": "Open a file with its default associated application.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path to the file."}},
            "required": ["path"],
        },
    },
    {
        "name": "read_text_file",
        "description": "Read the text contents of a plain-text file (e.g. .txt, .md, .py, .log) to answer questions about it.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path to the text file."}},
            "required": ["path"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the default web browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to open."}},
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "Runs a real DuckDuckGo web search and returns the top few results (title + snippet) as text, no browser needed. Good for current-events or factual questions you don't already know the answer to. Falls back to opening a full browser search only if the search itself fails.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query."}},
            "required": ["query"],
        },
    },
    {
        "name": "wikipedia_lookup",
        "description": "Fetches a short summary of a Wikipedia article and returns it as text. Best for 'who/what is X' questions about a specific person, place, or thing.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Topic or article title to look up, e.g. 'Ada Lovelace' or 'Mount Everest'."}},
            "required": ["query"],
        },
    },
    {
        "name": "get_system_status",
        "description": "Reports current CPU usage, RAM usage, main-drive disk space, and battery status (if this machine has a battery).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_brightness",
        "description": "Sets screen brightness to a percentage (0-100). Only works on displays with software brightness support (most laptop screens; typically not external/desktop monitors).",
        "input_schema": {
            "type": "object",
            "properties": {"percent": {"type": "number", "description": "Target brightness, 0-100."}},
            "required": ["percent"],
        },
    },
    {
        "name": "get_brightness",
        "description": "Gets the current screen brightness percentage, if the display supports software brightness control.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_DISPATCH: dict[str, Callable[..., str]] = {
    "open_app": lambda name: apps.open_app(name),
    "close_app": lambda name: apps.close_app(name),
    "list_running_apps": lambda: apps.list_running_apps(),
    "list_windows": lambda: windows_control.list_windows(),
    "focus_window": lambda title_substring: windows_control.focus_window(title_substring),
    "minimize_window": lambda title_substring: windows_control.minimize_window(title_substring),
    "maximize_window": lambda title_substring: windows_control.maximize_window(title_substring),
    "close_window": lambda title_substring: windows_control.close_window(title_substring),
    "set_volume": lambda percent: system.set_volume(percent),
    "get_volume": lambda: system.get_volume(),
    "take_screenshot": lambda: system.take_screenshot(),
    "lock_workstation": lambda: system.lock_workstation(),
    "search_files": lambda query, root="": files.search_files(query, root),
    "open_file": lambda path: files.open_file(path),
    "read_text_file": lambda path: files.read_text_file(path),
    "open_url": lambda url: web.open_url(url),
    "web_search": lambda query: web.web_search(query),
    "wikipedia_lookup": lambda query: web.wikipedia_lookup(query),
    "get_system_status": lambda: system_monitor.get_system_status(),
    "set_brightness": lambda percent: system.set_brightness(percent),
    "get_brightness": lambda: system.get_brightness(),
}


_recent_calls: list[str] = []
_MAX_RECENT = 50
_total_calls = 0


def _format_call(name: str, tool_input: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    return f"{name}({args})"


def call_count() -> int:
    """Monotonic count of tool invocations since startup -- used by the
    desktop UI to diff before/after a single brain turn and know exactly
    which calls belong to it, independent of _recent_calls' trimming."""
    return _total_calls


def get_recent_calls(n: int = 6) -> list[str]:
    """Returns the last `n` tool calls (formatted as "name(args)"), most
    recent last. Used by the desktop UI's telemetry panel and to attach
    tool badges to a reply."""
    return _recent_calls[-n:] if n > 0 else []


def run_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Executes a tool by name with the given input dict. Never raises --
    errors are caught and returned as a string so the model can react to
    them (e.g. apologize / try something else) instead of the app crashing."""
    global _total_calls

    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return f"Unknown tool '{name}'."
    try:
        result = fn(**tool_input)
        return str(result)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, tools must never crash the assistant
        logger.exception("Tool '%s' raised an exception", name)
        return f"Tool '{name}' failed: {exc}"
    finally:
        _total_calls += 1
        _recent_calls.append(_format_call(name, tool_input))
        if len(_recent_calls) > _MAX_RECENT:
            del _recent_calls[: len(_recent_calls) - _MAX_RECENT]

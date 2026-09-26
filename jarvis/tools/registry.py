"""Central registry: Anthropic tool-use schemas + dispatch to the actual
Python functions in apps.py / windows_control.py / system.py /
system_monitor.py / files.py / web.py.

To add a new tool: write the function, add a schema entry to TOOL_SCHEMAS,
and add a matching entry to TOOL_DISPATCH. That's it -- the brain's
tool-use loop and the schemas are decoupled from any single skill.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from jarvis.tools import apps, files, location, memory_tools, reminders, safety, system, system_monitor, web, windows_control

logger = logging.getLogger("jarvis.tools.registry")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "open_app",
        "description": "Open/launch an application by the name you would see in the Windows Start menu, e.g. 'notepad', 'chrome', 'Mocha Pro', 'Calculator', or open a file or folder by full path. It works for any installed app or Store app, and tells you whether it was really found and started. If several apps match, it lists them: ask the user which one instead of guessing. It only opens things: it cannot run commands, scripts or PowerShell, or pass options to a program.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Application name or path to launch."}},
            "required": ["name"],
        },
    },
    {
        "name": "close_app",
        "description": "Close/terminate all running processes matching a name, e.g. 'notepad', 'chrome'. Asks the user to confirm first (the first call returns CONFIRMATION REQUIRED).",
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
        "description": "Close a window, matched by a substring of its title. Asks the user to confirm first (the first call returns CONFIRMATION REQUIRED).",
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
        "name": "get_default_output_device",
        "description": "Which audio device Windows is sending sound to. Use when the user can't hear you or audio seems silent.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_screenshot",
        "description": "Capture a screenshot of the full screen and save it to disk.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lock_workstation",
        "description": "Lock the Windows workstation (requires the user's password to unlock again). Asks the user to confirm first (the first call returns CONFIRMATION REQUIRED).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_files",
        "description": "Find files by (partial) name or wildcard like '*.pdf' under the user's folder or a given folder. To see what's in a folder use list_folder.",
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
        "description": "Open a URL in the browser. Only when the user explicitly asks to open a site; never to answer a question.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to open."}},
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web in the background and return the top results as text to summarize. Use for current events, weather and facts you don't know. Never use open_url for this.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query."}},
            "required": ["query"],
        },
    },
    {
        "name": "wikipedia_lookup",
        "description": "Short Wikipedia summary of a person, place or thing, returned as text (opens no browser).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Topic or article title to look up, e.g. 'Ada Lovelace' or 'Mount Everest'."}},
            "required": ["query"],
        },
    },
    {
        "name": "set_timer",
        "description": "Start a countdown timer. It is spoken aloud when it finishes. Use for relative durations like 'in 10 minutes' or 'a 25 minute timer'. Convert the duration to seconds yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "description": "Length of the timer in SECONDS (10 minutes = 600)."},
                "label": {"type": "string", "description": "Optional short name, e.g. 'tea'."},
            },
            "required": ["duration_seconds"],
        },
    },
    {
        "name": "set_reminder",
        "description": "Reminder for a specific date and time, spoken when due. Work out the exact time from the current date/time in the system prompt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Local date and time as 'YYYY-MM-DD HH:MM' in 24-hour format, e.g. '2026-09-27 17:00'."},
                "message": {"type": "string", "description": "What to remind the user about, phrased as the reminder itself, e.g. 'call mom'."},
            },
            "required": ["when", "message"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List the timers and reminders that are currently set.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a timer or reminder, matched by its id or by words from its message or label.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words from the reminder, or its id."}},
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": "Save something the user asked you to remember about themselves (name, family, preferences). Use a short label so a later value replaces an older one. Only when they explicitly ask.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short label, e.g. 'favourite tea'."},
                "value": {"type": "string", "description": "The thing to remember."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "forget",
        "description": "Delete a remembered fact, matched by (part of) its label.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Label of the fact to forget."}},
            "required": ["key"],
        },
    },
    {
        "name": "list_folder",
        "description": "Summarize a folder: file/folder counts, counts by type, and the most recent files. Accepts a name (Downloads, Documents, Desktop...) or a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder name (Downloads, Documents, Desktop, Pictures, Videos, Music) or a full path."},
                "limit": {"type": "number", "description": "How many recent files to list (default 40)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "organize_folder",
        "description": "Sort a folder's loose files into sub-folders by type (Documents, Images, Videos, Audio, Installers, Archives, Other). Never deletes or overwrites; only inside the user's profile; can be undone. ALWAYS call with dry_run true first, explain, and run with dry_run false only after the user agrees. Use this instead of scripts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder name (e.g. Downloads) or full path."},
                "keep_originals": {"type": "boolean", "description": "true = copy files and leave the originals where they are; false (default) = move them."},
                "dry_run": {"type": "boolean", "description": "true (default) = only preview; false = actually do it."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "undo_organize",
        "description": "Undo the most recent organize_folder: put moved files back where they were (or remove the copies).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_text_file",
        "description": "Write a new document for the user: saves the text as a file (in Jarvis's own documents folder unless told otherwise) and opens it. A .txt opens in Notepad; a .docx is a Word document and opens in Word (use .docx when the user asks for Word). Use for a letter, note, list or any text. Never overwrites.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name, e.g. 'leave_application.txt', or .docx for Word."},
                "content": {"type": "string", "description": "The full text of the document."},
                "folder": {"type": "string", "description": "Folder name or path. Leave out to use Jarvis's documents folder."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "type_into_window",
        "description": "Type text into a window that is already open, at its cursor, then verify it arrived. Only for when the user asks to type into an existing window; to write a new document use create_text_file. Refuses command/terminal windows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Part of the window's title, e.g. 'Notepad'."},
                "text": {"type": "string", "description": "The text to type."},
            },
            "required": ["title_substring", "text"],
        },
    },
    {
        "name": "get_current_location",
        "description": "Approximate location (city level, from the internet connection, not GPS). Say it is approximate.",
        "input_schema": {"type": "object", "properties": {}},
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
    "get_default_output_device": lambda: system.get_default_output_device(),
    "take_screenshot": lambda: system.take_screenshot(),
    "lock_workstation": lambda: system.lock_workstation(),
    "search_files": lambda query, root="": files.search_files(query, root),
    "open_file": lambda path: files.open_file(path),
    "read_text_file": lambda path: files.read_text_file(path),
    "open_url": lambda url: web.open_url(url),
    "web_search": lambda query: web.web_search(query),
    "wikipedia_lookup": lambda query: web.wikipedia_lookup(query),
    "list_folder": lambda path, limit=40: files.list_folder(path, limit),
    "organize_folder": lambda path, keep_originals=False, dry_run=True: files.organize_folder(path, keep_originals, dry_run),
    "undo_organize": lambda: files.undo_organize(),
    "create_text_file": lambda filename, content, folder="": files.create_text_file(filename, content, folder),
    "type_into_window": lambda title_substring, text: windows_control.type_into_window(title_substring, text),
    "get_current_location": lambda: location.get_current_location(),
    "set_timer": lambda duration_seconds, label="": reminders.set_timer(duration_seconds, label),
    "set_reminder": lambda when, message: reminders.set_reminder(when, message),
    "list_reminders": lambda: reminders.list_reminders(),
    "cancel_reminder": lambda query: reminders.cancel_reminder(query),
    "remember": lambda key, value: memory_tools.remember(key, value),
    "forget": lambda key: memory_tools.forget(key),
    "get_system_status": lambda: system_monitor.get_system_status(),
    "set_brightness": lambda percent: system.set_brightness(percent),
    "get_brightness": lambda: system.get_brightness(),
}


_tool_listener: Optional[Callable[[str], None]] = None


def set_tool_listener(listener: Optional[Callable[[str], None]]) -> None:
    """Called with a tool's name just before it runs, so the UI can show what
    Jarvis is doing while a long request is in progress."""
    global _tool_listener
    _tool_listener = listener


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
    needs_confirmation = safety.check(name, tool_input)
    if needs_confirmation is not None:
        _total_calls += 1  # counted so the UI still shows what was attempted
        _recent_calls.append(_format_call(name, tool_input) + " (awaiting confirmation)")
        return needs_confirmation
    if _tool_listener is not None:
        try:
            _tool_listener(name)
        except Exception:  # noqa: BLE001 - a broken status display must never stop a tool
            logger.debug("Tool listener failed", exc_info=True)
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

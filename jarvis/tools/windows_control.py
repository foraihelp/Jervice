"""Window management: focus, minimize, maximize, close, list windows."""

from __future__ import annotations

import logging

logger = logging.getLogger("jarvis.tools.windows_control")


def _get_windows_by_title(title_substring: str):
    import pygetwindow as gw

    title_substring = title_substring.lower()
    return [w for w in gw.getAllWindows() if title_substring in w.title.lower() and w.title.strip()]


def list_windows() -> str:
    import pygetwindow as gw

    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    if not titles:
        return "No open windows found."
    return ", ".join(titles[:30])


def focus_window(title_substring: str) -> str:
    matches = _get_windows_by_title(title_substring)
    if not matches:
        return f"No window matching '{title_substring}' found."
    win = matches[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        return f"Focused window '{win.title}'."
    except Exception as exc:
        logger.warning("Failed to focus window: %s", exc)
        return f"Found '{win.title}' but couldn't focus it: {exc}"


def minimize_window(title_substring: str) -> str:
    matches = _get_windows_by_title(title_substring)
    if not matches:
        return f"No window matching '{title_substring}' found."
    win = matches[0]
    win.minimize()
    return f"Minimized window '{win.title}'."


def maximize_window(title_substring: str) -> str:
    matches = _get_windows_by_title(title_substring)
    if not matches:
        return f"No window matching '{title_substring}' found."
    win = matches[0]
    win.maximize()
    return f"Maximized window '{win.title}'."


def close_window(title_substring: str) -> str:
    matches = _get_windows_by_title(title_substring)
    if not matches:
        return f"No window matching '{title_substring}' found."
    win = matches[0]
    win.close()
    return f"Closed window '{win.title}'."

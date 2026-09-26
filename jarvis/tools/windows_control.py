"""Window management: focus, minimize, maximize, close, list windows, and typing text into a window."""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("jarvis.tools.windows_control")

MAX_TYPED_CHARS = 10000
# Pasting several lines into a console would run them as commands.
_TERMINAL_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class", "mintty", "virtualconsoleclass"}
_TERMINAL_TITLES = ("command prompt", "powershell", "windows terminal", "git bash", "cmd.exe", "administrator: ")


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


# --------------------------------------------------------------- foreground handling


def visible_windows() -> dict[int, str]:
    """{handle: title} of every visible top-level window that has a title."""
    import win32gui

    found: dict[int, str] = {}

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                found[hwnd] = title

    win32gui.EnumWindows(visit, None)
    return found


def force_foreground(hwnd: int) -> bool:
    """Brings a window to the front and gives it keyboard focus. Windows refuses this for a
    background program by default (the window just flashes in the taskbar), so borrow the
    foreground window's input queue for a moment, which is what makes it allowed. Returns
    whether the window really is in front afterwards."""
    import win32api
    import win32con
    import win32gui
    import win32process

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        current = win32api.GetCurrentThreadId()
        front = win32gui.GetForegroundWindow()
        front_thread = win32process.GetWindowThreadProcessId(front)[0] if front else 0
        attached = bool(front_thread and front_thread != current)
        if attached:
            win32process.AttachThreadInput(current, front_thread, True)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current, front_thread, False)
    except Exception:  # noqa: BLE001
        logger.debug("SetForegroundWindow failed for %s", hwnd, exc_info=True)
    time.sleep(0.15)
    return win32gui.GetForegroundWindow() == hwnd


def wait_for_window(title_substring: str, timeout: float = 8.0, ignore: Optional[set[int]] = None) -> Optional[int]:
    """Handle of a visible window whose title contains `title_substring`, waiting for it to appear."""
    wanted = title_substring.lower()
    deadline = time.time() + timeout
    while True:
        for hwnd, title in visible_windows().items():
            if wanted in title.lower() and (not ignore or hwnd not in ignore):
                return hwnd
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


def focus_window(title_substring: str) -> str:
    matches = _get_windows_by_title(title_substring)
    if not matches:
        return f"No window matching '{title_substring}' found."
    win = matches[0]
    try:
        if force_foreground(win._hWnd):
            return f"Focused window '{win.title}'."
        return f"Found '{win.title}' but Windows would not let me bring it to the front."
    except Exception as exc:
        logger.warning("Failed to focus window: %s", exc)
        return f"Found '{win.title}' but couldn't focus it: {exc}"


# ------------------------------------------------------------------ typing text


def _press(*keys: int) -> None:
    """Presses the keys together (e.g. Ctrl+V) and releases them."""
    import win32api
    import win32con

    for k in keys:
        win32api.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        win32api.keybd_event(k, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.08)


def _get_clipboard_text() -> Optional[str]:
    import win32clipboard
    import win32con

    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001 - another program may hold the clipboard for a moment
        return None


def _set_clipboard_text(text: str) -> bool:
    import win32clipboard
    import win32con

    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False


def _is_terminal(hwnd: int, title: str) -> bool:
    import win32gui

    return win32gui.GetClassName(hwnd).lower() in _TERMINAL_CLASSES or any(t in title.lower() for t in _TERMINAL_TITLES)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))


def type_into_window(title_substring: str, text: str) -> str:
    """Types `text` into the window whose title contains `title_substring`, at its cursor.
    The text is pasted (fast, and it keeps any language intact), then read back to confirm it
    arrived. Refuses terminal windows, where pasting would run the text as commands."""
    import win32api
    import win32con
    import win32gui

    if not text:
        return "There is no text to type."
    if len(text) > MAX_TYPED_CHARS:
        return f"That is too long to type in one go ({len(text)} characters; the limit is {MAX_TYPED_CHARS})."

    matches = [w for w in _get_windows_by_title(title_substring) if w.visible]
    if not matches:
        return f"No window matching '{title_substring}' found. Open it first."
    front = win32gui.GetForegroundWindow()
    win = next((w for w in matches if w._hWnd == front), matches[0])
    if _is_terminal(win._hWnd, win.title):
        return (f"I won't type into '{win.title}': it is a command window, where pasting text would "
                "run it as commands.")

    if not force_foreground(win._hWnd):
        return f"I found '{win.title}' but Windows would not let me bring it to the front to type in it."

    saved = _get_clipboard_text()
    try:
        if not _set_clipboard_text(text):
            return "I couldn't use the clipboard to type that (another program is holding it)."
        _press(win32con.VK_CONTROL, ord("V"))
        time.sleep(0.4)

        # Read the document back (Select all, Copy) to confirm the text really arrived.
        _set_clipboard_text("\0jarvis-check\0")
        _press(win32con.VK_CONTROL, ord("A"))
        _press(win32con.VK_CONTROL, ord("C"))
        time.sleep(0.3)
        document = _get_clipboard_text() or ""
        _press(win32con.VK_CONTROL, win32con.VK_END)  # drop the selection, cursor to the end
        arrived = _normalize(text) in _normalize(document)
    finally:
        if saved is not None:
            _set_clipboard_text(saved)

    if arrived:
        return f"Typed {len(text)} characters into '{win.title}'."
    return (f"I pasted the text into '{win.title}', but I couldn't confirm that it arrived. "
            "Please check the window.")


# --------------------------------------------------------------- the rest


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

"""Open and close applications on Windows.

`open_app` opens anything installed, the way you would by typing its name into the
Start menu. It builds an index of every app Windows knows about and matches what
you said against it, tolerantly (word order, a missing "Microsoft"/"Adobe", a
speech-recognition slip like "Mokha Pro"):

  1. The Start menu's own app list (`Get-StartApps`): desktop programs *and*
     Microsoft Store apps. This is what Start search uses.
  2. Start Menu and Desktop shortcuts (.lnk).
  3. Programs registered with Windows under "App Paths".
  4. The installed-programs list (Add/Remove Programs), for apps that have no
     shortcut at all.

It opens things; it does not run commands. And it reports what really happened.
An earlier version fell back to `cmd /c start`, which said "Opened" the moment cmd
itself started, even for a bogus name or a PowerShell script the AI model passed in
as the "app name", so a task could silently do nothing while being reported as done.
Now a missing app, a broken shortcut, an ambiguous name, and an app that never
appeared are all said out loud.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psutil

from jarvis.tools import files

logger = logging.getLogger("jarvis.tools.apps")

# Letters, digits, spaces, and a few characters real app names use. Anything with
# quotes, pipes, redirects, semicolons, $ and so on is a command, not a name.
_APP_NAME = re.compile(r"^[\w .+()'-]{1,80}$")
_SETTINGS_URI = re.compile(r"^ms-[a-z]+:[\w/?=.-]*$", re.IGNORECASE)
_REFUSAL = (
    "I can only open an app by its name (like 'notepad') or a file or folder path. "
    "I can't run commands or scripts, or pass options to a program."
)

_START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]
def _desktop_dirs() -> list[Path]:
    """The Desktop folders of whoever is logged in on this PC: the shared one, the user's own,
    and wherever Windows really keeps it (OneDrive often moves it)."""
    dirs = [Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop", Path.home() / "Desktop"]
    real = files._known_folder("desktop")
    if real is not None and real not in dirs:
        dirs.append(real)
    return dirs


_DESKTOP_DIRS = _desktop_dirs()

# Where programs live when they have no Start menu entry or registry record (portable apps, plug-in
# bundles, tools unzipped into a folder). Searched only when nothing else matches.
_SCAN_SKIP_STEMS = ("unins", "uninstall", "setup", "install", "update", "updater", "crash", "helper", "redist",
                    "vcredist", "dotnet", "diagnostic", "report", "service", "host", "elevate", "launcher_")
_SCAN_MAX_DEPTH = 9
_SCAN_SKIP_DIRS = {"windowsapps", "windows defender", "windows nt", "windowspowershell", "dotnet", "modifiableWindowsApps".lower(),
                   "installer", "cache", "temp", "logs", "crashpad", "crashes", "node_modules", "__pycache__", "assets",
                   "microsoft shared", "reference assemblies", "msbuild", "windows kits", "package cache"}
_GENERIC_DIRS = {"bin", "x64", "x86", "win64", "win32", "app", "application", "release", "resources", "lib", "program",
                 "programs", "mochaui", "plugins", "common files", "files", "core", "main"}
_SCAN_MAX_DIRS = 25000
_SCAN_TTL_SECONDS = 600

# Entries that sit next to an app but aren't the app.
_NOT_THE_APP = ("uninstall", "readme", "release notes", "user guide", "userguide", "manual", "manuals",
                "documentation", "website", "changelog", "what s new", "bug report", "links")
_FILLER_WORDS = {"the", "app", "application", "program", "software", "please", "my"}
# What people call things, mapped to what Windows calls them.
_ALIASES = {
    "vs code": "visual studio code", "vscode": "visual studio code", "ppt": "powerpoint",
    "ae": "after effects", "aftereffects": "after effects", "explorer": "file explorer",
    "cmd": "command prompt", "powershell": "windows powershell", "browser": "chrome",
    "media player": "media player", "notes": "sticky notes", "snip": "snipping tool",
    "task mgr": "task manager", "devtools": "visual studio code",
}
_SOURCE_PRIORITY = {"start": 0, "shortcut": 1, "programs": 2, "apppaths": 3, "scan": 4}
_FUZZY_MIN_RATIO = 0.86
_LAUNCH_CONFIRM_SECONDS = 8
_WINDOW_WAIT_SECONDS = 4
_INDEX_TTL_SECONDS = 300
_REBUILD_ON_MISS_AFTER = 30


@dataclass
class AppEntry:
    name: str
    source: str            # "start" | "shortcut" | "apppaths" | "programs"
    launch: str            # what to hand to os.startfile
    exe: Optional[str] = None
    norm: str = field(init=False)
    words: list[str] = field(init=False)
    compact: str = field(init=False)

    def __post_init__(self) -> None:
        self.words = _words(self.name)
        self.norm = " ".join(self.words)
        self.compact = "".join(self.words)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _is_extra(name: str) -> bool:
    """A manual, uninstaller, release-notes link and the like, not the app itself.
    Whole words only, so "Get Help" or "Foundry License Utility" are not caught."""
    padded = f" {_norm(name)} "
    return any(f" {term} " in padded for term in _NOT_THE_APP)


def _words(text: str) -> list[str]:
    """Comparable words: lower-case, no punctuation, no leading 'Microsoft'/'MS'."""
    words = _norm(text).split()
    while words and words[0] in ("microsoft", "ms"):
        words = words[1:]
    return words


# ---------------------------------------------------------------------- the index


def _powershell(command: str, timeout: int = 20) -> str:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except Exception:  # noqa: BLE001
        logger.debug("PowerShell command failed: %s", command[:60], exc_info=True)
        return ""


def _start_apps() -> list[AppEntry]:
    try:
        data = json.loads(_powershell("Get-StartApps | ConvertTo-Json -Compress") or "[]")
    except json.JSONDecodeError:
        return []
    entries = []
    for app in data if isinstance(data, list) else [data]:
        name, app_id = str(app.get("Name") or ""), str(app.get("AppID") or "")
        if name and app_id:
            exe = app_id if app_id.lower().endswith(".exe") and os.path.exists(app_id) else None
            entries.append(AppEntry(name, "start", "shell:AppsFolder\\" + app_id, exe))
    return entries


def _shortcut_entries() -> list[AppEntry]:
    entries = []
    for folder in _START_MENU_DIRS + _DESKTOP_DIRS:
        if not folder.is_dir():
            continue
        try:
            for lnk in folder.rglob("*.lnk"):
                entries.append(AppEntry(lnk.stem, "shortcut", str(lnk)))
        except OSError:
            continue
    return entries


def _registry_values(hive: int, path: str, view: int = 0) -> list[tuple[str, dict]]:
    """(subkey name, its values) for every subkey of `path`."""
    rows = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                sub = winreg.EnumKey(root, i)
                values = {}
                try:
                    with winreg.OpenKey(root, sub) as key:
                        for j in range(winreg.QueryInfoKey(key)[1]):
                            name, value, _ = winreg.EnumValue(key, j)
                            values[name] = value
                except OSError:
                    continue
                rows.append((sub, values))
    except OSError:
        pass
    return rows


def _app_paths_entries() -> list[AppEntry]:
    entries = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub, values in _registry_values(hive, base):
            exe = str(values.get("") or "").strip().strip('"')
            if sub.lower().endswith(".exe") and exe.lower().endswith(".exe") and os.path.exists(exe):
                entries.append(AppEntry(Path(sub).stem, "apppaths", exe, exe))
    return entries


def _programs_entries() -> list[AppEntry]:
    entries = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    sources = [
        (winreg.HKEY_LOCAL_MACHINE, base, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, base.replace("SOFTWARE", r"SOFTWARE\WOW6432Node"), 0),
        (winreg.HKEY_CURRENT_USER, base, 0),
    ]
    for hive, path, view in sources:
        for _, values in _registry_values(hive, path, view):
            name = str(values.get("DisplayName") or "").strip()
            icon = str(values.get("DisplayIcon") or "").split(",")[0].strip().strip('"')
            if (not name or values.get("SystemComponent") == 1 or values.get("ParentKeyName")
                    or not icon.lower().endswith(".exe") or not os.path.exists(icon)
                    or "unins" in icon.lower() or "uninstall" in icon.lower()):
                continue
            entries.append(AppEntry(name, "programs", icon, icon))
    return entries


def _looks_like_a_name(text: str) -> bool:
    """Not a version number ("153.0.4234.48"), a hash or a bare symbol."""
    return sum(c.isalpha() for c in text) >= 3 and not re.fullmatch(r"[\d._\- ]+", text)


def _app_folder_name(folder: Path, root: Path) -> Optional[str]:
    """The folder that names the app an exe belongs to: the nearest ancestor below the install
    root with a real name (skipping "bin", "x64", version numbers and the like)."""
    while folder != root and folder.parent != folder:
        if _looks_like_a_name(folder.name) and folder.name.lower() not in _GENERIC_DIRS:
            return folder.name
        folder = folder.parent
    return None


def _scan_roots() -> list[Path]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(str(Path(local) / "Programs"))
    seen: set[str] = set()
    result = []
    for r in roots:
        if r and os.path.isdir(r) and os.path.normcase(r) not in seen:
            seen.add(os.path.normcase(r))
            result.append(Path(r))
    return result


def _scan_entries() -> list[AppEntry]:
    """Programs found by looking inside the usual install folders: an .exe named like the
    app, or sitting in a folder named like it. The last resort, so it is only run when the
    Start menu, shortcuts and registry all came up empty."""
    entries: list[AppEntry] = []
    dirs_seen = 0
    for root in _scan_roots():
        stack = [(root, 0)]
        while stack and dirs_seen < _SCAN_MAX_DIRS:
            folder, depth = stack.pop()
            dirs_seen += 1
            try:
                with os.scandir(folder) as it:
                    children = list(it)
            except OSError:
                continue
            for child in children:
                try:
                    if child.is_dir(follow_symlinks=False):
                        if (depth < _SCAN_MAX_DEPTH and not child.name.startswith((".", "$"))
                                and child.name.lower() not in _SCAN_SKIP_DIRS):
                            stack.append((Path(child.path), depth + 1))
                    elif child.name.lower().endswith(".exe"):
                        stem = Path(child.name).stem
                        if any(term in stem.lower() for term in _SCAN_SKIP_STEMS):
                            continue
                        if _looks_like_a_name(stem):
                            entries.append(AppEntry(stem, "scan", child.path, child.path))
                        # A folder named for the app holding an exe with a different name
                        # ("MochaPro2026\...\mochapro.exe") should match either way.
                        app_folder = _app_folder_name(folder, root)
                        if app_folder and _norm(app_folder) != _norm(stem):
                            entries.append(AppEntry(app_folder, "scan", child.path, child.path))
                except OSError:
                    continue
    return entries


_scan_cache: tuple[float, list[AppEntry]] = (0.0, [])


def _scanned_apps() -> list[AppEntry]:
    global _scan_cache
    stamp, entries = _scan_cache
    if time.time() - stamp > _SCAN_TTL_SECONDS:
        entries = _scan_entries()
        _scan_cache = (time.time(), entries)
        logger.info("Scanned install folders: %d programs", len(entries))
    return entries


def _build_index() -> list[AppEntry]:
    """Every app we can find, best source first, one entry per name."""
    seen: set[str] = set()
    index: list[AppEntry] = []
    for entry in _start_apps() + _shortcut_entries() + _app_paths_entries() + _programs_entries():
        if not entry.norm or _is_extra(entry.name):
            continue
        if entry.compact in seen:   # "Live captions" and "LiveCaptions" are one app
            continue
        seen.add(entry.compact)
        index.append(entry)
    return index


_index: list[AppEntry] = []
_index_time = 0.0
_index_lock = threading.Lock()


def get_index(force: bool = False) -> list[AppEntry]:
    global _index, _index_time
    with _index_lock:
        if force or not _index or time.time() - _index_time > _INDEX_TTL_SECONDS:
            _index = _build_index()
            _index_time = time.time()
            logger.info("App index built: %d apps", len(_index))
        return _index


def warm_index() -> None:
    """Builds the index ahead of time (it takes a couple of seconds), so the first
    "open ..." request doesn't wait for it. Safe to call from a background thread."""
    try:
        get_index()
    except Exception:  # noqa: BLE001
        logger.debug("Could not warm the app index", exc_info=True)


# ------------------------------------------------------------------- matching


def _clean_query(name: str) -> list[str]:
    words = [w for w in _words(name) if w not in _FILLER_WORDS] or _words(name)
    alias = _ALIASES.get(" ".join(words)) or _ALIASES.get("".join(words))
    return alias.split() if alias else words


def _score(entry: AppEntry, q_words: list[str], q_compact: str, fuzzy: bool) -> Optional[int]:
    """0 same name; 1 starts with it; 2 every word you said begins a word of the name;
    3 every word of the name is in what you said; 4 (only if `fuzzy`) close spelling,
    which is what a speech-recognition slip looks like."""
    if not entry.words:
        return None
    if entry.words == q_words or entry.compact == q_compact:
        return 0
    query = " ".join(q_words)
    if entry.norm.startswith(query + " ") or (len(q_compact) >= 4 and entry.compact.startswith(q_compact)):
        return 1
    if all(any(w.startswith(qw) for w in entry.words) for qw in q_words):
        return 2
    if any(len(w) >= 3 for w in entry.words) and all(w in q_words for w in entry.words):
        return 3
    # Only a whole-name near miss ("Mokha Pro" for "Mocha Pro"). Being looser opens the wrong
    # app for one that isn't installed ("Photoshop" scores 0.8 against "Photos").
    if fuzzy and len(q_compact) >= 5 and difflib.SequenceMatcher(None, entry.compact, q_compact).ratio() >= _FUZZY_MIN_RATIO:
        return 4
    return None


def find_app(name: str) -> tuple[Optional[AppEntry], list[str], bool]:
    """(app, choices, guessed). `app` is what to open for `name`. If several apps fit
    equally well, `app` is None and `choices` lists them for the user to pick from. If
    nothing fits, both are empty. `guessed` is True when the match was by similar
    spelling rather than by name, so the caller can say so."""
    q_words = _clean_query(name)
    q_compact = "".join(q_words)
    if not q_compact:
        return None, [], False
    guessed = False
    scored: list[tuple[int, AppEntry]] = []
    # The Start menu, shortcuts and registry first; only if they know nothing of it, look inside the
    # install folders (slower, so its result is cached).
    for pool in (get_index, _scanned_apps):
        entries = pool()
        scored = [(s, e) for e in entries if (s := _score(e, q_words, q_compact, fuzzy=False)) is not None]
        if not scored:
            scored = [(s, e) for e in entries if (s := _score(e, q_words, q_compact, fuzzy=True)) is not None]
            guessed = bool(scored)
        if scored:
            break
    if not scored:
        return None, [], False

    best = min(s for s, _ in scored)
    tied = sorted((e for s, e in scored if s == best), key=lambda e: len(e.norm))
    if len(tied) == 1 or all(e.norm.startswith(tied[0].norm) for e in tied):
        return tied[0], [], guessed  # one match, or versions/variants of the same app: take the plainest name
    # The same app often appears from several places (a Start entry plus a bare
    # program name); if exactly one candidate comes from the most trustworthy
    # source, that is the one meant.
    top = min(_SOURCE_PRIORITY[e.source] for e in tied)
    preferred = [e for e in tied if _SOURCE_PRIORITY[e.source] == top]
    if len(preferred) == 1 and top < max(_SOURCE_PRIORITY[e.source] for e in tied):
        return preferred[0], [], guessed
    return None, [e.name for e in tied[:5]], False


def _did_you_mean(name: str) -> list[str]:
    q = "".join(_clean_query(name))
    names = {e.compact: e.name for e in get_index()}
    return [names[c] for c in difflib.get_close_matches(q, list(names), n=3, cutoff=0.6)]


# -------------------------------------------------------------------- launching


def _shortcut_target(lnk: str) -> Optional[str]:
    quoted = lnk.replace("'", "''")  # PowerShell single-quoted string: only ' needs escaping
    out = _powershell(f"(New-Object -ComObject WScript.Shell).CreateShortcut('{quoted}').TargetPath", timeout=10)
    return out.strip() or None


def _running(exe: str) -> bool:
    wanted = os.path.normcase(exe)
    for proc in psutil.process_iter(["exe"]):
        try:
            if os.path.normcase(proc.info.get("exe") or "") == wanted:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _appeared(exe: str) -> bool:
    """Waits a few seconds for a process running `exe` to exist."""
    deadline = time.time() + _LAUNCH_CONFIRM_SECONDS
    while time.time() < deadline:
        if _running(exe):
            return True
        time.sleep(0.5)
    return _running(exe)


def _bring_forward(entry: AppEntry, before: set[int]) -> None:
    """Once an app is started, put its window in front. Windows keeps a window started by a
    background program behind everything else (it only flashes in the taskbar), which looks
    exactly like "it didn't open". Best effort: apps that live in the tray have no window."""
    from jarvis.tools import windows_control as wc

    deadline = time.time() + _WINDOW_WAIT_SECONDS
    words = [w for w in entry.words if len(w) >= 3] or entry.words
    while time.time() < deadline:
        windows = wc.visible_windows()
        fresh = {h: t for h, t in windows.items() if h not in before}
        pool = fresh or {h: t for h, t in windows.items() if any(w in _norm(t) for w in words)}
        named = {h: t for h, t in pool.items() if any(w in _norm(t) for w in words)}
        target = next(iter(named or fresh), None)
        if target is not None:
            wc.force_foreground(target)
            return
        time.sleep(0.3)


def _launch(entry: AppEntry) -> str:
    exe = entry.exe
    if entry.source == "shortcut":
        target = _shortcut_target(entry.launch)
        if target and not os.path.exists(target):
            return f"I found '{entry.name}', but the program it points to is missing: {target}"
        exe = target if target and target.lower().endswith(".exe") else None
    elif entry.source in ("apppaths", "programs", "scan") and not os.path.exists(entry.launch):
        return f"I found '{entry.name}', but its program is missing: {entry.launch}"

    was_running = bool(exe and _running(exe))
    try:
        from jarvis.tools import windows_control as wc

        before = set(wc.visible_windows())
    except Exception:  # noqa: BLE001
        before = set()
    try:
        os.startfile(entry.launch)  # type: ignore[attr-defined]  (Windows-only)
    except OSError as exc:
        logger.warning("Failed to open %s (%s): %s", entry.name, entry.launch, exc)
        return f"I found '{entry.name}' but couldn't start it: {exc.strerror or exc}"
    if exe is None or was_running or _appeared(exe):
        try:
            _bring_forward(entry, before)
        except Exception:  # noqa: BLE001 - never let window handling turn a successful launch into a failure
            logger.debug("Could not bring %s to the front", entry.name, exc_info=True)
        return f"Opened {entry.name}."
    return (f"I started {entry.name}, but I couldn't confirm that it opened. "
            "It may still be loading, or it may have failed to start.")


def open_app(name: str) -> str:
    """Opens an application by name (e.g. "notepad", "Mocha Pro", "vs code"), or a
    file or folder by path. Returns what actually happened, including failure."""
    name = name.strip().strip('"')
    if not name:
        return "No application name given."

    is_path = False
    try:
        is_path = Path(name).expanduser().exists()
    except OSError:
        pass

    if not is_path:
        if not (_APP_NAME.match(name) or _SETTINGS_URI.match(name)):
            return _REFUSAL
        # "chrome --incognito", "cmd /c ..." : options make it a command, not a name.
        if any(token.startswith(("-", "/")) for token in name.split()[1:]):
            return _REFUSAL

    if is_path:
        try:
            os.startfile(name)  # type: ignore[attr-defined]  (Windows-only)
            return f"Opened {name}."
        except OSError as exc:
            return f"I couldn't open '{name}': {exc.strerror or exc}"

    # Look it up among the installed apps first: that gives a confirmed launch, and asks
    # instead of guessing when several apps fit ("git" is Git Bash, Git GUI, Git CMD...).
    entry, choices, guessed = find_app(name)
    if entry is None and not choices and time.time() - _index_time > _REBUILD_ON_MISS_AFTER:
        get_index(force=True)             # maybe it was installed a moment ago
        entry, choices, guessed = find_app(name)
    if choices:
        return f"Several apps match '{name}': " + ", ".join(choices) + ". Which one should I open?"
    if entry is not None and not guessed:
        return _launch(entry)

    # Not an app we know by that name: an executable Windows itself resolves (calc, regedit, mstsc...).
    try:
        os.startfile(name)  # type: ignore[attr-defined]
        return f"Opened {name}."
    except OSError:
        pass

    if entry is not None:                 # nothing exact, but one close spelling ("Mokha Pro")
        result = _launch(entry)
        if result.startswith("Opened"):
            result += f" (There is no app called '{name}'; that is the closest match.)"
        return result

    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        try:
            os.startfile(found)  # type: ignore[attr-defined]
            return f"Opened {name}."
        except OSError as exc:
            logger.warning("Failed to open %r (%s): %s", name, found, exc)

    suggestions = _did_you_mean(name)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    return f"I couldn't find an app or file called '{name}'.{hint}"


def close_app(name: str) -> str:
    """Terminates all running processes whose name matches `name`
    (case-insensitive, partial match on the process/executable name)."""
    name = name.strip().lower().removesuffix(".exe")
    if not name:
        return "No application name given."

    matched = []
    for proc in psutil.process_iter(["pid", "name"]):
        proc_name = (proc.info.get("name") or "").lower().removesuffix(".exe")
        if name in proc_name:
            matched.append(proc)

    if not matched:
        return f"No running process matching '{name}' found."

    for proc in matched:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning("Could not terminate %s: %s", proc, exc)

    gone, alive = psutil.wait_procs(matched, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return f"Closed {len(matched)} process(es) matching '{name}'."


def list_running_apps(limit: int = 30) -> str:
    """Returns a short list of currently visible/running application
    process names, deduplicated."""
    names = set()
    for proc in psutil.process_iter(["name"]):
        n = proc.info.get("name")
        if n:
            names.add(n)
    sample = sorted(names)[:limit]
    return ", ".join(sample) if sample else "No processes found."

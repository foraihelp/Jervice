"""File tools: search, list, open, read, and tidy a folder.

Searches are scoped to the user's profile directory by default to keep them
fast. Organizing files only ever works inside the user's own profile, never
deletes anything, never overwrites (a name clash gets a "(1)" suffix), and can
be undone.
"""

from __future__ import annotations

import ctypes
import fnmatch
import json
import logging
import os
import shutil
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Optional

from jarvis.config import PROJECT_ROOT

logger = logging.getLogger("jarvis.tools.files")

MAX_RESULTS = 15
MAX_DIRS_WALKED = 20000  # safety cap so a huge tree can't hang the assistant
MAX_LISTED = 40
MAX_ORGANIZE_FILES = 5000
_RECENT_SECONDS = 120  # a file touched this recently may still be downloading or open

UNDO_LOG = PROJECT_ROOT / "data" / "last_organize.json"

CATEGORIES: dict[str, set[str]] = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".md"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".heic", ".tiff", ".ico"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"},
    "Installers": {".exe", ".msi", ".msix", ".appx"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
}
_SKIP_NAMES = {"desktop.ini", "thumbs.db"}
_PARTIAL_EXTENSIONS = {".crdownload", ".part", ".tmp", ".partial", ".download"}

_KNOWN_FOLDER_GUIDS = {
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
}
_ALIASES = {
    "download": "downloads", "docs": "documents", "document": "documents", "picture": "pictures",
    "photos": "pictures", "video": "videos", "movies": "videos",
}


def _known_folder(name: str) -> Optional[Path]:
    """Where Windows actually keeps Downloads, Documents, and so on. These can
    be relocated (OneDrive often moves Documents and Desktop), so guessing
    <home>/Downloads is not reliable."""

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    try:
        g = uuid.UUID(_KNOWN_FOLDER_GUIDS[name])
        guid = GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))
        ptr = ctypes.c_void_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) != 0:  # type: ignore[attr-defined]
            return None
        try:
            return Path(ctypes.wstring_at(ptr.value))
        finally:
            ctypes.windll.ole32.CoTaskMemFree(ptr)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        logger.debug("Known-folder lookup failed for %s", name, exc_info=True)
        return None


def resolve_folder(path: str) -> Path:
    """A real, existing folder from what the user or model said: a well-known
    name ("downloads", "my documents") or a path. Raises ValueError with a
    message fit to read aloud if there isn't one."""
    raw = (path or "").strip().strip('"')
    if not raw:
        raise ValueError("Which folder? Give a folder name like Downloads, or a path.")
    key = raw.lower()
    for prefix in ("my ", "the "):
        key = key.removeprefix(prefix)
    key = key.removesuffix(" folder").strip()
    key = _ALIASES.get(key, key)
    if key in _KNOWN_FOLDER_GUIDS:
        folder = _known_folder(key) or (Path.home() / key.capitalize())
    else:
        folder = Path(os.path.expandvars(raw)).expanduser()
    if not folder.is_dir():
        raise ValueError(f"I couldn't find a folder at '{folder}'.")
    return folder.resolve()


def _category(path: Path) -> str:
    ext = path.suffix.lower()
    for name, extensions in CATEGORIES.items():
        if ext in extensions:
            return name
    return "Other"


def _size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes} B"


def search_files(query: str, root: str = "") -> str:
    query = query.strip().lower()
    if not query:
        return "No search term given. To see what's in a folder, use list_folder."

    try:
        start = resolve_folder(root) if root else Path.home()
    except ValueError as exc:
        return str(exc)

    wildcard = any(c in query for c in "*?")
    matches: list[str] = []
    walked = 0
    for dirpath, dirnames, filenames in os.walk(start):
        walked += 1
        if walked > MAX_DIRS_WALKED:
            break
        # Skip noisy/system directories.
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in
            {"node_modules", "$recycle.bin", "windows", "appdata"}
        ]
        for fname in filenames:
            hit = fnmatch.fnmatch(fname.lower(), query) if wildcard else query in fname.lower()
            if hit:
                matches.append(str(Path(dirpath) / fname))
                if len(matches) >= MAX_RESULTS:
                    return "\n".join(matches)

    return "\n".join(matches) if matches else f"No files matching '{query}' found under {start}."


def list_folder(path: str, limit: int = MAX_LISTED) -> str:
    """Summarizes a folder: counts by type, then the most recently changed files."""
    try:
        folder = resolve_folder(path)
        entries = list(os.scandir(folder))
    except (ValueError, OSError) as exc:
        return str(exc)

    files = [e for e in entries if e.is_file(follow_symlinks=False)]
    folders = [e for e in entries if e.is_dir(follow_symlinks=False)]
    counts: dict[str, int] = {}
    for e in files:
        counts[_category(Path(e.name))] = counts.get(_category(Path(e.name)), 0) + 1

    limit = max(1, min(int(limit or MAX_LISTED), 100))
    recent = sorted(files, key=lambda e: e.stat().st_mtime, reverse=True)[:limit]
    lines = [
        f"{folder}: {len(files)} files, {len(folders)} folders.",
        "By type: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none") + ".",
    ]
    if folders:
        lines.append("Folders: " + ", ".join(e.name for e in folders[:15]) + (" ..." if len(folders) > 15 else ""))
    lines.append(f"Most recent {len(recent)} files:")
    for e in recent:
        st = e.stat()
        lines.append(f"{e.name} ({_size(st.st_size)}, {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d})")
    return "\n".join(lines)


def _free_name(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while (directory / f"{stem} ({n}){suffix}").exists():
        n += 1
    return directory / f"{stem} ({n}){suffix}"


def organize_folder(path: str, keep_originals: bool = False, dry_run: bool = True) -> str:
    """Sorts the loose files in a folder into sub-folders by type (Documents, Images,
    Videos, Audio, Installers, Archives, Other). With dry_run it only reports what
    it would do. Never deletes, never overwrites, and only works inside the user's
    own profile."""
    try:
        folder = resolve_folder(path)
    except ValueError as exc:
        return str(exc)
    home = Path.home().resolve()
    if folder == home or home not in folder.parents:
        return "I only organize folders inside your user profile, like Downloads, Documents or Desktop."

    now = time.time()
    plan: list[tuple[Path, str]] = []
    skipped_recent = 0
    with os.scandir(folder) as it:
        for e in it:
            p = Path(e.path)
            if not e.is_file(follow_symlinks=False) or e.name.startswith(".") or e.name.lower() in _SKIP_NAMES:
                continue
            if p.suffix.lower() in _PARTIAL_EXTENSIONS:
                continue
            if now - e.stat().st_mtime < _RECENT_SECONDS:
                skipped_recent += 1
                continue
            plan.append((p, _category(p)))
            if len(plan) >= MAX_ORGANIZE_FILES:
                break

    if not plan:
        return f"There are no loose files to organize in {folder}."

    per_category: dict[str, int] = {}
    for _, cat in plan:
        per_category[cat] = per_category.get(cat, 0) + 1
    summary = ", ".join(f"{cat} {n}" for cat, n in sorted(per_category.items()))
    verb = "copy" if keep_originals else "move"
    note = f" ({skipped_recent} very recent files left alone.)" if skipped_recent else ""

    if dry_run:
        return (f"Preview only, nothing has changed yet. I would {verb} {len(plan)} files in {folder} into "
                f"sub-folders: {summary}.{note} Ask the user to confirm, then call organize_folder again "
                "with dry_run set to false.")

    done: list[dict[str, str]] = []
    failures: list[str] = []
    for src, cat in plan:
        dest_dir = folder / cat
        try:
            dest_dir.mkdir(exist_ok=True)
            dest = _free_name(dest_dir, src.name)
            if keep_originals:
                shutil.copy2(src, dest)
            else:
                shutil.move(str(src), str(dest))
            done.append({"src": str(src), "dst": str(dest)})
        except OSError as exc:
            failures.append(f"{src.name} ({exc.strerror or exc})")

    if done:
        UNDO_LOG.parent.mkdir(parents=True, exist_ok=True)
        UNDO_LOG.write_text(json.dumps({"copied": keep_originals, "items": done}, indent=1), encoding="utf-8")

    result = f"{'Copied' if keep_originals else 'Moved'} {len(done)} files in {folder} into sub-folders: {summary}.{note}"
    if failures:
        result += f" {len(failures)} could not be handled: " + "; ".join(failures[:5]) + "."
    return result + " You can undo this with undo_organize."


def undo_organize() -> str:
    """Puts back the files moved by the last organize_folder (or removes the copies it made)."""
    try:
        record = json.loads(UNDO_LOG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "There is nothing to undo."

    restored, problems = 0, []
    for item in record["items"]:
        src, dst = Path(item["src"]), Path(item["dst"])
        try:
            if not dst.exists():
                problems.append(f"{dst.name} is no longer there")
            elif record["copied"]:
                dst.unlink()
                restored += 1
            elif src.exists():
                problems.append(f"{src.name} already exists in the original place")
            else:
                shutil.move(str(dst), str(src))
                restored += 1
        except OSError as exc:
            problems.append(f"{dst.name} ({exc.strerror or exc})")

    for d in {Path(i["dst"]).parent for i in record["items"]}:
        try:
            d.rmdir()  # only succeeds if the folder is now empty
        except OSError:
            pass
    if not problems:
        UNDO_LOG.unlink(missing_ok=True)
    what = "Removed the copies of" if record["copied"] else "Put back"
    return f"{what} {restored} files." + (f" {len(problems)} problems: " + "; ".join(problems[:5]) + "." if problems else "")


def open_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"File '{path}' does not exist."
    try:
        os.startfile(str(p))  # type: ignore[attr-defined]  (Windows-only)
        return f"Opened {p}."
    except Exception as exc:
        logger.warning("Failed to open file %s: %s", p, exc)
        return f"I couldn't open '{path}': {exc}"


def read_text_file(path: str, max_chars: int = 4000) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"File '{path}' does not exist."
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        return text
    except Exception as exc:
        logger.warning("Failed to read file %s: %s", p, exc)
        return f"I couldn't read '{path}': {exc}"

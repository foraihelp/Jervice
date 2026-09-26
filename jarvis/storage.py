"""Where Jarvis keeps what belongs to the user: one data folder they can see, change and rename.

    <data folder>\\
        conversations\\history.jsonl   what was said in the window (shown again on the next launch)
        memory.json                   facts Jarvis was asked to remember, and its recent context
        reminders.json                timers and reminders
        Documents\\                    letters, notes and Word files Jarvis creates
        Screenshots\\
        last_organize.json            so a folder tidy-up can be undone

The folder defaults to Documents\\Jarvis. Settings can point it somewhere else or rename it. A change is
recorded in config.yaml and carried out at the next start, before anything is open: Windows will not
rename or move a folder that has files in use, and this way it never has to.

Not kept here: config.yaml and .env (settings and the API key) and the log stay under AppData, so the
app can always start and find them, and so a synced or removable data folder can't hold a secret key.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from jarvis.config import INSTALL_DIR, PROJECT_ROOT, update_config_value

logger = logging.getLogger("jarvis.storage")

DEFAULT_FOLDER_NAME = "Jarvis"
LEGACY_DIR = PROJECT_ROOT / "data"          # where earlier versions kept everything
_ITEMS = ("memory.json", "reminders.json", "last_organize.json", "conversations", "Documents", "Screenshots")
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

_active: Optional[Path] = None
startup_notice = ""                          # something the user should be told about, once, after start
_write_config: Callable[[str, str, Any], None] = update_config_value


# ------------------------------------------------------------------- locations


def default_dir() -> Path:
    """Documents\\Jarvis, wherever Windows really keeps Documents (OneDrive often moves it)."""
    from jarvis.tools import files

    docs = files._known_folder("documents") or (Path.home() / "Documents")
    return docs / DEFAULT_FOLDER_NAME


def data_dir() -> Path:
    """The folder in use right now."""
    return _active or LEGACY_DIR


def memory_file() -> Path:
    return data_dir() / "memory.json"


def reminders_file() -> Path:
    return data_dir() / "reminders.json"


def undo_file() -> Path:
    return data_dir() / "last_organize.json"


def history_file() -> Path:
    return data_dir() / "conversations" / "history.jsonl"


def documents_dir() -> Path:
    path = data_dir() / "Documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshots_dir() -> Path:
    path = data_dir() / "Screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- validating


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _inside(child: Path, parent: Path) -> bool:
    c, p = os.path.normcase(str(child.resolve())), os.path.normcase(str(parent.resolve()))
    return c != p and c.startswith(p.rstrip("\\") + "\\")


def _ensure_writable(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ".jarvis-write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


def validate_target(path: str) -> Path:
    """A folder Jarvis can safely use for its data, or ValueError with a message fit to show."""
    raw = (path or "").strip().strip('"')
    if not raw:
        raise ValueError("Choose a folder.")
    target = Path(os.path.expandvars(raw)).expanduser()
    if not target.is_absolute():
        raise ValueError("Give the full path of the folder, for example D:\\Jarvis Data.")
    target = target.resolve()
    if target == Path(target.anchor):
        raise ValueError("Choose a folder, not a whole drive.")
    for protected in (INSTALL_DIR, Path(os.environ.get("ProgramFiles", "C:\\Program Files")),
                      Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
                      Path(os.environ.get("SystemRoot", "C:\\Windows"))):
        if _same(target, protected) or _inside(target, protected):
            raise ValueError("That folder is part of Windows or a program install and can't be written to safely. Choose one of your own.")
    if _active is not None and _inside(target, _active):
        raise ValueError("The new folder can't be inside the current one.")
    # Prove it can be written to, then leave no trace: the folder is only really created (or moved
    # into) at the next start.
    created: list[Path] = []
    probe_dir = target
    while not probe_dir.exists() and probe_dir.parent != probe_dir:
        created.append(probe_dir)
        probe_dir = probe_dir.parent
    try:
        _ensure_writable(target)
    except OSError as exc:
        raise ValueError(f"I can't write to that folder: {exc.strerror or exc}") from exc
    finally:
        for folder in created:          # deepest first
            try:
                folder.rmdir()
            except OSError:
                pass
    return target


def valid_folder_name(name: str) -> str:
    name = (name or "").strip().strip(". ")
    if not name:
        raise ValueError("Type a name for the folder.")
    if _INVALID_NAME_CHARS.search(name):
        raise ValueError("A folder name cannot contain any of these characters:  < > : \" / \\ | ? *")
    if name.lower() in _RESERVED:
        raise ValueError(f"'{name}' is a reserved Windows name. Pick another.")
    return name


# ------------------------------------------------------------------ changing it


def request_folder(path: str) -> dict[str, Any]:
    """Records the folder to use from the next start. Nothing moves yet."""
    target = validate_target(path)
    _write_config("storage", "data_dir", str(target))
    return {"path": str(target), "needs_restart": _active is None or not _same(target, _active)}


def request_rename(new_name: str) -> dict[str, Any]:
    name = valid_folder_name(new_name)
    current = data_dir()
    target = current.parent / name
    if _same(target, current):
        return {"path": str(current), "needs_restart": False}
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"There is already a folder named '{name}' next to it, and it isn't empty.")
    return request_folder(str(target))


# ----------------------------------------------------------- moving at startup


def _move_tree(src: Path, dst: Path) -> None:
    """Moves everything in `src` into `dst`, merging with what is already there. A name that
    is taken keeps both files rather than overwriting."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if item.is_dir() and target.is_dir():
            _move_tree(item, target)
            continue
        if target.exists():
            stem, suffix, n = target.stem, target.suffix, 1
            while (dst / f"{stem} (moved {n}){suffix}").exists():
                n += 1
            target = dst / f"{stem} (moved {n}){suffix}"
        shutil.move(str(item), str(target))
    try:
        src.rmdir()
    except OSError:
        pass


def _relocate(src: Path, dst: Path) -> None:
    if _same(src, dst):
        return
    if _inside(dst, src) or _inside(src, dst):
        raise ValueError("One folder is inside the other.")
    if not src.exists():
        return
    if dst.exists() and not any(dst.iterdir()):
        dst.rmdir()                  # an empty target: replace it, so the fast rename below works
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)      # instant when both are on the same drive
            return
        except OSError:
            pass                     # another drive, or a file is in use: fall back to moving file by file
    _move_tree(src, dst)


def _adopt_legacy(dst: Path) -> None:
    """First run of this feature: copy what earlier versions saved into the new folder (a copy, so the
    old files stay where they were as a backup)."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in _ITEMS:
        old, new = LEGACY_DIR / name, dst / name
        if not old.exists() or new.exists():
            continue
        try:
            if old.is_dir():
                shutil.copytree(old, new)
            else:
                shutil.copy2(old, new)
        except OSError:
            logger.warning("Could not copy %s to %s", old, new, exc_info=True)


def initialize(config) -> Path:
    """Decides which folder is in use and carries out any pending move or rename. Runs once at
    startup, before anything opens a file in it. Never raises: if the wanted folder can't be used,
    Jarvis carries on with the previous one and `startup_notice` says why."""
    global _active, startup_notice
    desired = Path(config.storage_data_dir) if config.storage_data_dir else default_dir()
    recorded = Path(config.storage_active_dir) if config.storage_active_dir else None
    try:
        if recorded is None:
            _ensure_writable(desired)
            _adopt_legacy(desired)
            if not _same(desired, LEGACY_DIR):
                startup_notice = f"Your Jarvis data (conversations, reminders, memory, created documents) is now kept in {desired}. You can change or rename this folder in Settings."
        elif not _same(recorded, desired):
            _relocate(recorded, desired)
            _ensure_writable(desired)
            startup_notice = f"Your Jarvis data was moved to {desired}."
        else:
            _ensure_writable(desired)
        if recorded is None or not _same(recorded, desired):
            _write_config("storage", "active_dir", str(desired))
        _active = desired
    except Exception as exc:  # noqa: BLE001 - never stop Jarvis over a folder problem
        logger.exception("Could not use the data folder %s", desired)
        fallback = recorded if recorded is not None and recorded.exists() else LEGACY_DIR
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _active = fallback
        startup_notice = f"I couldn't use the data folder {desired} ({exc}). I'm still using {fallback}."
    return _active


def describe() -> dict[str, Any]:
    """What Settings shows about the folder in use."""
    folder = data_dir()
    size, count = 0, 0
    if folder.exists():
        for root, _, names in os.walk(folder):
            for n in names:
                try:
                    size += (Path(root) / n).stat().st_size
                    count += 1
                except OSError:
                    pass
    return {"path": str(folder), "exists": folder.exists(), "size_bytes": size, "file_count": count,
            "default_path": str(default_dir()), "log_path": str(PROJECT_ROOT / "data")}

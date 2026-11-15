"""File-backed folder list for the Knowledge dashboard.

The store travels with the vault: ``<vault>/.hermes/knowledge_folders.json`` with
``{"folders": [...], "hidden": [...]}``.  A legacy bare JSON array is accepted
on read (``{"folders": [...]}`` behaviour).  The runtime/dashboard never reads
this file directly — it goes through the HTTP contract — so the exact location
is an implementation detail of this service.

Writes are atomic (``bridge.atomic_io.atomic_write_text``) and the path is
resolved through ``resolve_inside`` so a crafted vault path cannot escape.
"""
from __future__ import annotations

import json
from pathlib import Path

from bridge.atomic_io import atomic_write_text, resolve_inside

# Learner-centric topics shared by the WeChat routing skill, the dashboard and
# the Golden Demo dataset.  Old vaults that still contain captures in the
# previous defaults ("AI 产品", "数据 Agent", "待读", "临时") keep showing those
# folders because ``folder_response`` appends any folder that still has items.
DEFAULT_FOLDERS = (
    "Inbox",
    "AI 产品面试",
    "Agent 与大模型",
    "行业与产品拆解",
    "读书与随笔",
)
STORE_RELATIVE_PATH = ".hermes/knowledge_folders.json"
MAX_FOLDER_NAME_LENGTH = 80


def normalize_folder(value: object) -> str:
    """Return a trimmed folder name, defaulting blank values to ``Inbox``."""
    folder = str(value or "").strip()
    return folder or "Inbox"


def validate_folder_name(value: object) -> str:
    """Return a usable folder name or raise ``ValueError`` for bad input."""
    folder = str(value or "").strip()
    if not folder:
        raise ValueError("folder name is required")
    if len(folder) > MAX_FOLDER_NAME_LENGTH:
        raise ValueError("folder name is too long")
    return folder


def folder_store_path(vault_path: str | Path) -> Path:
    return resolve_inside(Path(vault_path).expanduser(), STORE_RELATIVE_PATH)


def _read_store(vault_path: str | Path) -> dict[str, list[str]]:
    path = folder_store_path(vault_path)
    if not path.is_file():
        return {"folders": [], "hidden": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"folders": [], "hidden": []}
    if not isinstance(data, dict):
        # Legacy format: a bare list of folder names.
        data = {"folders": data}
    return {
        "folders": _clean_names(data.get("folders")),
        "hidden": _clean_names(data.get("hidden")),
    }


def _clean_names(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    cleaned: list[str] = []
    for value in values:
        folder = normalize_folder(value)
        if folder not in cleaned:
            cleaned.append(folder)
    return cleaned


def read_folder_names(vault_path: str | Path) -> list[str]:
    """Return explicitly stored folders (without the built-in defaults)."""
    return _read_store(vault_path)["folders"]


def read_hidden_folders(vault_path: str | Path) -> list[str]:
    return _read_store(vault_path)["hidden"]


def visible_folders(vault_path: str | Path) -> list[str]:
    """Return defaults + stored folders, minus hidden ones (order preserved)."""
    store = _read_store(vault_path)
    hidden = set(store["hidden"])
    folders: list[str] = []
    for folder in (*DEFAULT_FOLDERS, *store["folders"]):
        if folder not in hidden and folder not in folders:
            folders.append(folder)
    return folders or ["Inbox"]


def write_folder_store(
    vault_path: str | Path,
    folders: list[str],
    hidden: list[str] | None = None,
) -> None:
    path = folder_store_path(vault_path)
    payload = {
        "folders": _clean_names(folders),
        "hidden": _clean_names(hidden or []),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def save_folder(vault_path: str | Path, folder: str) -> list[str]:
    """Add one folder to the store (un-hiding it if needed)."""
    name = normalize_folder(folder)
    store = _read_store(vault_path)
    folders = store["folders"]
    if name not in folders:
        folders.append(name)
    hidden = [item for item in store["hidden"] if item != name]
    write_folder_store(vault_path, folders, hidden)
    return folders


def rename_stored_folder(vault_path: str | Path, old_folder: str, new_folder: str) -> None:
    """Rename one stored folder; hide a renamed built-in default."""
    old_name = normalize_folder(old_folder)
    new_name = normalize_folder(new_folder)
    store = _read_store(vault_path)
    folders = [new_name if item == old_name else item for item in store["folders"]]
    if new_name not in folders:
        folders.append(new_name)
    folders = [item for item in folders if item != old_name]
    hidden = [item for item in store["hidden"] if item != new_name]
    if old_name in DEFAULT_FOLDERS and old_name != new_name and old_name not in hidden:
        hidden.append(old_name)
    write_folder_store(vault_path, folders, hidden)

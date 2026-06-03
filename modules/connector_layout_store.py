"""
Persisted layout for Connectors tab: root-level folders and connector ordering.

Stored at database path ``connectors_layout`` (separate from connector definitions).
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional, Set, Tuple

from . import database_manager
from .notification_engine import nav_actions_main_tab, notify_critical

logger = logging.getLogger(__name__)

LAYOUT_PATH = "connectors_layout"
LAYOUT_VERSION = 1


def default_layout() -> Dict[str, Any]:
    return {"version": LAYOUT_VERSION, "root_items": [], "folders": {}}


def load_layout() -> Dict[str, Any]:
    raw = database_manager.get_data(LAYOUT_PATH)
    if not isinstance(raw, dict) or not raw:
        return default_layout()
    layout = default_layout()
    if raw.get("version") == LAYOUT_VERSION:
        layout["root_items"] = list(raw.get("root_items") or [])
        folders = raw.get("folders") or {}
        if isinstance(folders, dict):
            layout["folders"] = {
                str(k): {
                    "name": str(v.get("name") or "Folder"),
                    "connector_ids": [
                        str(x) for x in (v.get("connector_ids") or []) if x
                    ],
                }
                for k, v in folders.items()
                if isinstance(v, dict)
            }
    return layout


def save_layout(layout: Dict[str, Any]) -> bool:
    try:
        payload = {
            "version": LAYOUT_VERSION,
            "root_items": list(layout.get("root_items") or []),
            "folders": {
                k: {
                    "name": str(v.get("name") or "Folder"),
                    "connector_ids": list(v.get("connector_ids") or []),
                }
                for k, v in (layout.get("folders") or {}).items()
                if isinstance(v, dict)
            },
        }
        return database_manager.set_data(LAYOUT_PATH, payload)
    except Exception as e:
        logger.error(f"Error saving connectors layout: {e}", exc_info=True)
        notify_critical(
            "Could not save Connectors tab layout.",
            dedupe_key="connectors:layout_save",
            actions=nav_actions_main_tab("Connectors"),
        )
        return False


def _normalize_root_item(item: Any) -> Optional[Tuple[str, str]]:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    iid = item.get("id")
    if kind not in ("connector", "folder") or not iid:
        return None
    return str(kind), str(iid)


def reconcile_layout(
    layout: Dict[str, Any], existing_ids: Set[str]
) -> Dict[str, Any]:
    """Return a new layout dict synced with connector ids present in the manager."""
    out = deepcopy(layout)
    if out.get("version") != LAYOUT_VERSION:
        out = default_layout()

    folders: Dict[str, Any] = dict(out.get("folders") or {})
    # Filter folder member lists to known ids only (dedupe within folder)
    for fid, spec in list(folders.items()):
        if not isinstance(spec, dict):
            del folders[fid]
            continue
        seen: Set[str] = set()
        new_ids: List[str] = []
        for cid in spec.get("connector_ids") or []:
            s = str(cid)
            if s in existing_ids and s not in seen:
                new_ids.append(s)
                seen.add(s)
        spec["name"] = str(spec.get("name") or "Folder")
        spec["connector_ids"] = new_ids
        if not new_ids:
            del folders[fid]

    root_items_raw = list(out.get("root_items") or [])
    root_items: List[Dict[str, str]] = []
    occupied: Set[str] = set()

    for item in root_items_raw:
        parsed = _normalize_root_item(item)
        if not parsed:
            continue
        kind, iid = parsed
        if kind == "connector":
            if iid not in existing_ids or iid in occupied:
                continue
            root_items.append({"kind": "connector", "id": iid})
            occupied.add(iid)
        else:  # folder
            if iid not in folders:
                continue
            members = folders[iid].get("connector_ids") or []
            if not members:
                del folders[iid]
                continue
            # drop members already placed elsewhere (shouldn't happen)
            filtered: List[str] = []
            for m in members:
                if m in existing_ids and m not in occupied:
                    filtered.append(m)
                    occupied.add(m)
            if not filtered:
                del folders[iid]
                continue
            folders[iid]["connector_ids"] = filtered
            root_items.append({"kind": "folder", "id": iid})

    folder_ids_on_root = {it["id"] for it in root_items if it["kind"] == "folder"}
    for fid in list(folders.keys()):
        if fid not in folder_ids_on_root:
            del folders[fid]

    missing = sorted(existing_ids - occupied)
    for cid in missing:
        root_items.append({"kind": "connector", "id": cid})

    out["folders"] = folders
    out["root_items"] = root_items
    out["version"] = LAYOUT_VERSION
    return out


def find_root_slot_index(layout: Dict[str, Any], connector_id: str) -> int:
    """Index in root_items of the folder or connector row that contains this connector."""
    root_items = layout.get("root_items") or []
    folders = layout.get("folders") or {}
    for idx, item in enumerate(root_items):
        if item.get("kind") == "connector" and item.get("id") == connector_id:
            return idx
        if item.get("kind") == "folder":
            fid = item.get("id")
            spec = folders.get(fid) or {}
            if connector_id in (spec.get("connector_ids") or []):
                return idx
    return len(root_items)


def remove_connector_from_layout(layout: Dict[str, Any], connector_id: str) -> None:
    """Remove connector from root_items and from any folder; drop empty folders from root."""
    root_items: List[Dict[str, str]] = list(layout.get("root_items") or [])
    folders: Dict[str, Any] = dict(layout.get("folders") or {})

    root_items = [
        it for it in root_items if not (it.get("kind") == "connector" and it.get("id") == connector_id)
    ]

    empty_folder_ids: List[str] = []
    for fid, spec in list(folders.items()):
        ids = [c for c in (spec.get("connector_ids") or []) if c != connector_id]
        if not ids:
            empty_folder_ids.append(fid)
            del folders[fid]
        else:
            folders[fid] = {**spec, "connector_ids": ids}

    layout["folders"] = folders
    layout["root_items"] = [
        it
        for it in root_items
        if not (it.get("kind") == "folder" and it.get("id") in empty_folder_ids)
    ]


def merge_into_new_folder(
    layout: Dict[str, Any], dragged_id: str, target_id: str, new_name: str = "New folder"
) -> Tuple[Dict[str, Any], str]:
    """
    Create a new folder with [target_id, dragged_id] at the root slot that held target.
    Returns (mutated layout copy, new_folder_id).
    """
    if dragged_id == target_id:
        return layout, ""

    layout = deepcopy(layout)
    insert_at = find_root_slot_index(layout, target_id)

    remove_connector_from_layout(layout, dragged_id)
    remove_connector_from_layout(layout, target_id)

    new_id = str(uuid.uuid4())
    layout.setdefault("folders", {})[new_id] = {
        "name": new_name,
        "connector_ids": [target_id, dragged_id],
    }

    root_items = list(layout.get("root_items") or [])
    # insert_at may be past end after removals; clamp
    insert_at = min(insert_at, len(root_items))
    root_items.insert(insert_at, {"kind": "folder", "id": new_id})
    layout["root_items"] = root_items
    return layout, new_id


def move_connector_to_folder(
    layout: Dict[str, Any], connector_id: str, folder_id: str
) -> Dict[str, Any]:
    layout = deepcopy(layout)
    if folder_id not in (layout.get("folders") or {}):
        return layout
    if not any(
        it.get("kind") == "folder" and it.get("id") == folder_id
        for it in (layout.get("root_items") or [])
    ):
        return layout

    remove_connector_from_layout(layout, connector_id)

    folders = layout["folders"]
    members: List[str] = list(folders[folder_id].get("connector_ids") or [])
    if connector_id not in members:
        members.append(connector_id)
    folders[folder_id]["connector_ids"] = members
    return layout


def move_connector_to_root(layout: Dict[str, Any], connector_id: str) -> Dict[str, Any]:
    """Remove from folder if present; ensure a root connector entry exists."""
    layout = deepcopy(layout)
    remove_connector_from_layout(layout, connector_id)
    root_items = list(layout.get("root_items") or [])
    root_items.append({"kind": "connector", "id": connector_id})
    layout["root_items"] = root_items
    return layout


def rename_folder(layout: Dict[str, Any], folder_id: str, name: str) -> Dict[str, Any]:
    layout = deepcopy(layout)
    folders = layout.get("folders") or {}
    if folder_id in folders:
        folders[folder_id]["name"] = name.strip() or "Folder"
    layout["folders"] = folders
    return layout


def delete_folder_keep_connectors(layout: Dict[str, Any], folder_id: str) -> Dict[str, Any]:
    """Remove folder from root and release members as root-level connectors (preserve order)."""
    layout = deepcopy(layout)
    folders = dict(layout.get("folders") or {})
    spec = folders.pop(folder_id, None)
    members: List[str] = list((spec or {}).get("connector_ids") or [])

    root_items = list(layout.get("root_items") or [])
    insert_at = len(root_items)
    for i, it in enumerate(root_items):
        if it.get("kind") == "folder" and it.get("id") == folder_id:
            insert_at = i
            break
    root_items = [
        it
        for it in root_items
        if not (it.get("kind") == "folder" and it.get("id") == folder_id)
    ]
    for offset, cid in enumerate(members):
        root_items.insert(insert_at + offset, {"kind": "connector", "id": cid})

    layout["root_items"] = root_items
    layout["folders"] = folders
    return layout


def list_folder_connector_ids(layout: Dict[str, Any], folder_id: str) -> List[str]:
    spec = (layout.get("folders") or {}).get(folder_id) or {}
    return list(spec.get("connector_ids") or [])


def delete_folder_record_only(
    layout: Dict[str, Any], folder_id: str
) -> Tuple[Dict[str, Any], List[str]]:
    """Remove folder from layout without placing members on root. Returns (layout, member_ids)."""
    layout = deepcopy(layout)
    folders = dict(layout.get("folders") or {})
    spec = folders.pop(folder_id, None) or {}
    members: List[str] = list(spec.get("connector_ids") or [])
    layout["folders"] = folders
    layout["root_items"] = [
        it
        for it in (layout.get("root_items") or [])
        if not (it.get("kind") == "folder" and it.get("id") == folder_id)
    ]
    return layout, members

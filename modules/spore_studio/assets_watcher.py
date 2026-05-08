#!/usr/bin/env python3
"""
mtime-poll watcher for ``assets/{template_name}/``.

The Spore Studio editor needs to know when the user adds, removes, or
modifies asset files outside the editor (drag-drop into the folder, copy
from somewhere else, etc.) so the asset browser can refresh. We use a
simple mtime poll — running templates only have a handful of assets and
this avoids pulling in a new dependency like ``watchdog``.

Public surface:

* :func:`start_watcher` — idempotently start the global background poller.
* :func:`request_snapshot` — return the current asset tree for one template
  (also used by the HTTP endpoint directly).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from ..path_utils import get_assets_path, ensure_directory_exists

logger = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 1.5
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_known: Dict[str, Dict[str, float]] = {}


_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}


def _classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _FONT_EXTS:
        return "font"
    return "other"


def _scan_template(template_name: str) -> Dict[str, Any]:
    """Walk the assets folder for one template and return a flat file list."""
    template_assets = os.path.join(get_assets_path(), template_name)
    files: List[Dict[str, Any]] = []
    if os.path.isdir(template_assets):
        for root, _, file_names in os.walk(template_assets):
            for name in file_names:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, template_assets).replace(os.sep, "/")
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                files.append(
                    {
                        "name": name,
                        "rel_path": rel,
                        "url": f"/assets/{template_name}/{rel}",
                        "kind": _classify(name),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
    files.sort(key=lambda entry: entry["rel_path"].lower())
    return {
        "template": template_name,
        "root": template_assets,
        "files": files,
        "scanned_at": time.time(),
    }


def request_snapshot(template_name: str) -> Dict[str, Any]:
    """
    Return the current asset tree for ``template_name``.

    Always reads from disk; do NOT cache here because the HTTP endpoint
    that calls this is the user's explicit "refresh" path.
    """
    if template_name:
        ensure_directory_exists(os.path.join(get_assets_path(), template_name))
    return _scan_template(template_name)


def _broadcast_changes(template_name: str, snapshot: Dict[str, Any]) -> None:
    """Push the new snapshot to any connected editor over Socket.IO."""
    try:
        from .. import web_engine as web_engine_module
    except Exception:
        return
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if inst is None:
        return
    sio = getattr(inst, "socketio", None)
    if sio is None:
        return
    try:
        sio.emit(
            "spore_studio_assets_changed",
            {"template": template_name, "snapshot": snapshot},
        )
    except Exception as e:
        logger.debug("Failed to emit spore_studio_assets_changed: %s", e)


def _watched_templates() -> List[str]:
    """List all templates with an existing asset folder."""
    base = get_assets_path()
    if not os.path.isdir(base):
        return []
    templates: List[str] = []
    for entry in os.listdir(base):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        templates.append(entry)
    return templates


def _poll_once() -> None:
    for template_name in _watched_templates():
        snapshot = _scan_template(template_name)
        signature: Dict[str, float] = {
            f["rel_path"]: f["mtime"] for f in snapshot["files"]
        }
        with _lock:
            previous = _known.get(template_name)
            _known[template_name] = signature
        if previous is None:
            continue
        if previous != signature:
            _broadcast_changes(template_name, snapshot)


def _watcher_loop() -> None:
    logger.debug("Spore Studio assets watcher loop starting")
    with _lock:
        for template_name in _watched_templates():
            snapshot = _scan_template(template_name)
            _known[template_name] = {
                f["rel_path"]: f["mtime"] for f in snapshot["files"]
            }
    while not _stop_event.is_set():
        try:
            _poll_once()
        except Exception as e:
            logger.warning("Spore Studio assets watcher poll failed: %s", e)
        if _stop_event.wait(timeout=_POLL_INTERVAL_SECONDS):
            return


def start_watcher() -> None:
    """Start the watcher thread (idempotent)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_watcher_loop, daemon=True, name="spore-studio-assets-watcher"
        )
        _thread.start()
    logger.info("Spore Studio assets watcher started")


def stop_watcher() -> None:
    """Stop the watcher thread (used during application shutdown)."""
    _stop_event.set()

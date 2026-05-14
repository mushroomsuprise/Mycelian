#!/usr/bin/env python3
"""
mtime-poll watcher for ``assets/{template_name}/``.

The Spore Studio editor needs to know when the user adds, removes, or
modifies asset files outside the editor (drag-drop into the folder, copy
from somewhere else, etc.) so the asset browser can refresh. We use a
simple mtime poll — running templates only have a handful of assets and
this avoids pulling in a new dependency like ``watchdog``.

The poller runs as a Flask-SocketIO **background task** (see
:func:`ensure_background_poller`) so ``socketio.emit`` runs on the same
async model as the web engine (gevent), not a plain ``threading.Thread``.

Public surface:

* :func:`ensure_background_poller` — schedule the global poller (idempotent;
  call from a Socket.IO handler after the server is running).
* :func:`request_snapshot` — return the current asset tree for one template
  (also used by the HTTP endpoint directly).
* :func:`stop_watcher` — request shutdown of the background poller.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List

from ..path_utils import get_assets_path, ensure_directory_exists

logger = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 1.5
_lock = threading.Lock()
_poller_lock = threading.Lock()
_poller_started = False
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
        logger.warning("Failed to emit spore_studio_assets_changed: %s", e)


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


def _seed_baseline() -> None:
    with _lock:
        for template_name in _watched_templates():
            snapshot = _scan_template(template_name)
            _known[template_name] = {
                f["rel_path"]: f["mtime"] for f in snapshot["files"]
            }


def _background_poll_loop(socketio: Any) -> None:
    global _poller_started
    try:
        logger.debug("Spore Studio assets watcher loop starting")
        _seed_baseline()
        while not _stop_event.is_set():
            try:
                _poll_once()
            except Exception as e:
                logger.warning("Spore Studio assets watcher poll failed: %s", e)
            if _stop_event.is_set():
                break
            try:
                socketio.sleep(_POLL_INTERVAL_SECONDS)
            except Exception:
                break
    finally:
        with _poller_lock:
            _poller_started = False


def ensure_background_poller(socketio: Any) -> None:
    """
    Schedule the mtime poller once (idempotent).

    Must be called from the Socket.IO stack after ``socketio.run`` is
    active — typically the first ``connect`` handler.
    """
    global _poller_started
    with _poller_lock:
        if _poller_started:
            return
        _stop_event.clear()
        _poller_started = True
    try:
        socketio.start_background_task(_background_poll_loop, socketio)
    except Exception as e:
        with _poller_lock:
            _poller_started = False
        logger.warning("Could not schedule Spore Studio assets poller: %s", e)
        return
    logger.info("Spore Studio assets watcher started (Socket.IO background task)")


def start_watcher() -> None:
    """
    Deprecated: the poller is scheduled via :func:`ensure_background_poller`
    on the first websocket ``connect``. Kept for compatibility with older
    call sites (no-op).
    """
    logger.debug(
        "start_watcher() is deprecated; use ensure_background_poller from connect"
    )


def stop_watcher() -> None:
    """Signal the background poller to stop (used during application shutdown)."""
    _stop_event.set()

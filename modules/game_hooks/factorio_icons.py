# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Resolve Factorio item/fluid icons from the local game install."""

from __future__ import annotations

import logging
import os
import re
import time
from io import BytesIO
from typing import Dict, Optional, Tuple

from .factorio_hook import find_factorio_pid, parse_read_data_path, process_exe_path, resolve_data_dir

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_PACKS = ("base", "space-age", "quality", "elevated-rails", "core")
_ICON_SUBDIRS = (
    os.path.join("graphics", "icons"),
    os.path.join("graphics", "icons", "fluid"),
    os.path.join("graphics", "entity"),
)
_INDEX_TTL_SEC = 60.0

_index_cache: Dict[str, str] = {}
_index_built_at = 0.0
_index_root: Optional[str] = None
_png_cache: Dict[str, Tuple[float, bytes]] = {}


def resolve_game_data_root(exe_path: Optional[str] = None) -> Optional[str]:
    """Return Factorio's read-only ``data/`` directory (base, space-age, ...)."""
    if not exe_path:
        exe_path = process_exe_path(find_factorio_pid())
    candidates: list[str] = []
    if exe_path:
        exe_dir = os.path.dirname(os.path.abspath(exe_path))
        candidates.extend(
            [
                os.path.join(exe_dir, "..", "..", "data"),
                os.path.join(exe_dir, "..", "data"),
                os.path.join(exe_dir, "data"),
                os.path.join(exe_dir, "..", "..", "..", "data"),
            ]
        )
        if exe_dir.replace("\\", "/").endswith("Contents/MacOS"):
            candidates.insert(0, os.path.join(exe_dir, "..", "data"))
    data_dir = resolve_data_dir(exe_path=exe_path)
    if data_dir:
        log_path = os.path.join(data_dir, "factorio-current.log")
        if os.path.isfile(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    parsed = parse_read_data_path(fh.read(8192))
                if parsed:
                    candidates.insert(0, parsed)
            except OSError:
                pass
        candidates.append(os.path.join(data_dir, "data"))
    home = os.path.expanduser("~")
    candidates.extend(
        [
            os.path.join(
                home,
                "Library",
                "Application Support",
                "Steam",
                "steamapps",
                "common",
                "Factorio",
                "factorio.app",
                "Contents",
                "data",
            ),
            os.path.join(
                home,
                ".steam",
                "steam",
                "steamapps",
                "common",
                "Factorio",
                "data",
            ),
        ]
    )
    seen: set[str] = set()
    for raw in candidates:
        path = os.path.abspath(raw)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isdir(os.path.join(path, "base")):
            return path
    return None


def _scan_index(root: str) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for pack in _PACKS:
        pack_dir = os.path.join(root, pack)
        if not os.path.isdir(pack_dir):
            continue
        for sub in _ICON_SUBDIRS:
            folder = os.path.join(pack_dir, sub)
            if not os.path.isdir(folder):
                continue
            try:
                for entry in os.listdir(folder):
                    if not entry.lower().endswith(".png"):
                        continue
                    stem = entry[:-4]
                    full = os.path.join(folder, entry)
                    # Prefer earlier packs (base before space-age) only if unset.
                    index.setdefault(stem, full)
                    index.setdefault(stem.replace("_", "-"), full)
            except OSError:
                continue
    return index


def icon_index(force: bool = False) -> Dict[str, str]:
    global _index_cache, _index_built_at, _index_root
    now = time.monotonic()
    root = resolve_game_data_root()
    stale = (
        force
        or not _index_cache
        or root != _index_root
        or (now - _index_built_at) > _INDEX_TTL_SEC
    )
    if stale:
        _index_root = root
        _index_built_at = now
        _index_cache = _scan_index(root) if root else {}
    return _index_cache


def icon_path_for(name: str) -> Optional[str]:
    if not name or not _SAFE_NAME.match(name):
        return None
    index = icon_index()
    path = index.get(name) or index.get(name.replace("_", "-"))
    if not path and not name.startswith("starmap-planet-"):
        alias = "starmap-planet-" + name.replace("_", "-")
        path = index.get(alias)
    if not path:
        return None
    root = _index_root or ""
    try:
        common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
    except ValueError:
        return None
    if os.path.abspath(root) != common:
        return None
    return path if os.path.isfile(path) else None


def cropped_png_bytes(name: str) -> Optional[bytes]:
    """Return the top mip of a Factorio icon PNG (square crop)."""
    path = icon_path_for(name)
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _png_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.load()
            width, height = im.size
            if height > width > 0:
                im = im.crop((0, 0, width, width))
            buf = BytesIO()
            im.save(buf, format="PNG")
            data = buf.getvalue()
    except Exception as e:
        logger.debug("factorio icon crop %s: %s", name, e)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
    _png_cache[path] = (mtime, data)
    if len(_png_cache) > 512:
        # Drop an arbitrary oldest-style entry; this is a bounded best-effort cache.
        _png_cache.pop(next(iter(_png_cache)))
    return data

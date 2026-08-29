#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Persistent UI settings for the Custom Sources / Spore Studio template previewer.

Stored beside other app data as ``template_preview_settings.json`` — separate
from ``config.json`` and template JSON files. Preview-only options (e.g. mute,
per-template resolution) do not alter saved template configs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from .path_utils import get_data_path

logger = logging.getLogger(__name__)

_FILENAME = "template_preview_settings.json"
_SAVE_LOCK = threading.Lock()

_DEFAULTS: Dict[str, Any] = {
    "enable_preview_sounds": True,
    "show_mock_toolbar": True,
    "show_inline_preview": True,
    "resolutions": {},
}

PREVIEW_WIDTH_MIN = 50
PREVIEW_WIDTH_MAX = 7680
PREVIEW_HEIGHT_MIN = 25
PREVIEW_HEIGHT_MAX = 4320


def settings_path() -> str:
    return get_data_path(_FILENAME)


def clamp_preview_resolution(width: Any, height: Any) -> Optional[Tuple[int, int]]:
    """Return clamped ``(width, height)`` or ``None`` if values are not usable."""
    try:
        w = int(float(width))
        h = int(float(height))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    w = max(PREVIEW_WIDTH_MIN, min(w, PREVIEW_WIDTH_MAX))
    h = max(PREVIEW_HEIGHT_MIN, min(h, PREVIEW_HEIGHT_MAX))
    return w, h


def load_template_preview_settings() -> Dict[str, Any]:
    path = settings_path()
    data = dict(_DEFAULTS)
    data["resolutions"] = {}
    if not os.path.isfile(path):
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data.update(raw)
    except Exception as e:
        logger.warning("Could not load template preview settings: %s", e)
    # Coerce known keys
    if "enable_preview_sounds" in data:
        data["enable_preview_sounds"] = bool(data["enable_preview_sounds"])
    if "show_mock_toolbar" in data:
        data["show_mock_toolbar"] = bool(data["show_mock_toolbar"])
    if "show_inline_preview" in data:
        data["show_inline_preview"] = bool(data["show_inline_preview"])
    resolutions = data.get("resolutions")
    if not isinstance(resolutions, dict):
        data["resolutions"] = {}
    else:
        cleaned: Dict[str, Dict[str, int]] = {}
        for key, entry in resolutions.items():
            if not isinstance(key, str) or not key.strip():
                continue
            if not isinstance(entry, dict):
                continue
            clamped = clamp_preview_resolution(entry.get("width"), entry.get("height"))
            if clamped is None:
                continue
            cleaned[key.strip()] = {"width": clamped[0], "height": clamped[1]}
        data["resolutions"] = cleaned
    return data


def save_template_preview_settings(updates: Dict[str, Any]) -> bool:
    with _SAVE_LOCK:
        current = load_template_preview_settings()
        if "resolutions" in updates and isinstance(updates.get("resolutions"), dict):
            # Shallow merge would wipe other templates; merge per-key instead.
            merged = dict(current.get("resolutions") or {})
            for key, entry in updates["resolutions"].items():
                if not isinstance(key, str) or not key.strip():
                    continue
                if entry is None:
                    merged.pop(key.strip(), None)
                    continue
                if not isinstance(entry, dict):
                    continue
                clamped = clamp_preview_resolution(
                    entry.get("width"), entry.get("height")
                )
                if clamped is None:
                    continue
                merged[key.strip()] = {"width": clamped[0], "height": clamped[1]}
            current["resolutions"] = merged
            updates = {k: v for k, v in updates.items() if k != "resolutions"}
        current.update(updates)
        path = settings_path()
        tmp_path = path + ".tmp"
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            return True
        except Exception as e:
            logger.error("Could not save template preview settings: %s", e)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return False


def get_template_preview_resolution(key: str) -> Optional[Tuple[int, int]]:
    """Return stored ``(width, height)`` for *key*, or ``None`` if unset."""
    if not key or not str(key).strip():
        return None
    data = load_template_preview_settings()
    entry = (data.get("resolutions") or {}).get(str(key).strip())
    if not isinstance(entry, dict):
        return None
    return clamp_preview_resolution(entry.get("width"), entry.get("height"))


def set_template_preview_resolution(key: str, width: Any, height: Any) -> bool:
    """Persist preview resolution for one template/config key (merge, don't wipe)."""
    if not key or not str(key).strip():
        return False
    clamped = clamp_preview_resolution(width, height)
    if clamped is None:
        return False
    return save_template_preview_settings(
        {"resolutions": {str(key).strip(): {"width": clamped[0], "height": clamped[1]}}}
    )


def clear_template_preview_resolution(key: str) -> bool:
    """Remove the stored resolution override for *key*."""
    if not key or not str(key).strip():
        return False
    return save_template_preview_settings({"resolutions": {str(key).strip(): None}})

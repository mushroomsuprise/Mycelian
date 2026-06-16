#!/usr/bin/env python3
"""
Persistent UI settings for the Custom Sources template previewer.

Stored beside other app data as ``template_preview_settings.json`` — separate
from ``config.json`` and template JSON files. Preview-only options (e.g. mute)
do not alter saved template configs.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from .path_utils import get_data_path

logger = logging.getLogger(__name__)

_FILENAME = "template_preview_settings.json"

_DEFAULTS: Dict[str, Any] = {
    "enable_preview_sounds": True,
    "show_mock_toolbar": True,
    "show_inline_preview": True,
}


def settings_path() -> str:
    return get_data_path(_FILENAME)


def load_template_preview_settings() -> Dict[str, Any]:
    path = settings_path()
    data = dict(_DEFAULTS)
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
    return data


def save_template_preview_settings(updates: Dict[str, Any]) -> bool:
    current = load_template_preview_settings()
    current.update(updates)
    path = settings_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        return True
    except Exception as e:
        logger.error("Could not save template preview settings: %s", e)
        return False

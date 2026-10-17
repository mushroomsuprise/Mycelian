#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Small persisted UI preferences for Spore Studio.

The desktop window does not keep ``localStorage`` between launches, so flags such as
"the tour was already offered" are stored in ``data/spore_studio_ui_prefs.json``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict

from ..path_utils import get_data_path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MAX_KEYS = 64
_MAX_VALUE_CHARS = 2000


def _prefs_path() -> str:
    return get_data_path(os.path.join("data", "spore_studio_ui_prefs.json"))


def load_prefs() -> Dict[str, Any]:
    try:
        with open(_prefs_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Could not read Spore Studio UI prefs: %s", exc)
        return {}


def update_prefs(changes: Any) -> Dict[str, Any]:
    """Merge ``changes`` (string keys, JSON-scalar/short values) and persist."""
    if not isinstance(changes, dict):
        return load_prefs()
    with _LOCK:
        prefs = load_prefs()
        for key, value in changes.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                continue
            if value is None:
                prefs.pop(key, None)
                continue
            try:
                if len(json.dumps(value)) > _MAX_VALUE_CHARS:
                    continue
            except (TypeError, ValueError):
                continue
            if key not in prefs and len(prefs) >= _MAX_KEYS:
                continue
            prefs[key] = value
        path = _prefs_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(prefs, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("Could not save Spore Studio UI prefs: %s", exc)
        return prefs

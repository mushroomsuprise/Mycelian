#!/usr/bin/env python3
"""
Round-trip a previously saved Spore Studio template back into the editor model.

Templates that were authored by Spore Studio always have a sibling
``.spore.json`` sidecar — that file IS the editor model and we trust it
verbatim. Legacy / hand-written templates do not have a sidecar; we fall
back to a minimal "advanced mode" model containing just the canvas size
plus an extracted ``advanced_js`` block so the user can still open them in
the editor and tweak the JS without losing the visual layout.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from .template_codegen import extract_user_js
from ..path_utils import get_template_path

logger = logging.getLogger(__name__)


def _spore_sidecar_path(template_name: str) -> str:
    return get_template_path(
        os.path.join("template_configs", f"{template_name}.spore.json")
    )


def load_sidecar(template_name: str) -> Optional[Dict[str, Any]]:
    """Return the ``.spore.json`` sidecar if present, else None."""
    path = _spore_sidecar_path(template_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Spore sidecar at %s could not be loaded: %s", path, e)
    return None


def save_sidecar(template_name: str, model: Dict[str, Any]) -> bool:
    """Write the editor sidecar atomically. Returns True on success."""
    path = _spore_sidecar_path(template_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(model, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        logger.error("Failed to save spore sidecar %s: %s", path, e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


def parse_existing(template_name: str) -> Dict[str, Any]:
    """
    Build a usable editor model for ``template_name``.

    Behaviour:

    * If a ``.spore.json`` sidecar exists, return it (it is authoritative).
    * Otherwise return a minimal "advanced mode" model that points at the
      raw HTML and asks the editor to disable the canvas.
    """
    sidecar = load_sidecar(template_name)
    if sidecar is not None:
        sidecar.setdefault("template_name", template_name)
        sidecar.setdefault("alert_system", sidecar.get("alert_system", "queue"))
        sidecar.setdefault("design", {"width": 800, "height": 200})
        sidecar.setdefault("elements", [])
        sidecar.setdefault("advanced_js", "")
        return sidecar

    html_path = get_template_path(f"{template_name}.html")
    advanced_js = ""
    if os.path.isfile(html_path):
        try:
            with open(html_path, "r", encoding="utf-8") as fh:
                advanced_js = extract_user_js(fh.read())
        except OSError as e:
            logger.warning("Could not read legacy template %s: %s", html_path, e)

    return {
        "template_name": template_name,
        "alert_system": "queue",
        "design": {"width": 800, "height": 200},
        "elements": [],
        "advanced_js": advanced_js,
        "legacy": True,
    }

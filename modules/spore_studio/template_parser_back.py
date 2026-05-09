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

import glob
import json
import logging
import os
from typing import Any, Dict, Optional

from .template_codegen import extract_user_js
from .template_reverse_parser import (
    design_size_from_config,
    reverse_parse_legacy,
)
from ..path_utils import get_template_path

logger = logging.getLogger(__name__)


# Sidecars used to live next to the public JSON in ``template_configs/`` —
# the dual ``.json`` extension caused them to leak into the Source Settings
# dropdown via ``TemplateConfigParser.get_config_files()``. They now live
# under the hidden ``_spore/`` folder (mirrors the existing
# ``_boilerplates/`` convention which is already filtered out by other
# template enumerators).
_LEGACY_SIDECAR_DIR = "template_configs"
_SIDECAR_DIR = "_spore"
_SIDECAR_SUFFIX = ".spore.json"


def _spore_sidecar_path(template_name: str) -> str:
    return get_template_path(
        os.path.join(_SIDECAR_DIR, f"{template_name}{_SIDECAR_SUFFIX}")
    )


def _legacy_sidecar_path(template_name: str) -> str:
    return get_template_path(
        os.path.join(_LEGACY_SIDECAR_DIR, f"{template_name}{_SIDECAR_SUFFIX}")
    )


def _migrate_legacy_sidecar(template_name: str) -> None:
    """
    If a sidecar still lives in the old ``template_configs/`` location,
    move it to the new ``_spore/`` directory. Idempotent: silently does
    nothing when the new path already exists or the legacy path is absent.
    """
    new_path = _spore_sidecar_path(template_name)
    old_path = _legacy_sidecar_path(template_name)
    if os.path.isfile(new_path) or not os.path.isfile(old_path):
        return
    try:
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(old_path, new_path)
        logger.info(
            "Spore sidecar migrated: %s -> %s", old_path, new_path
        )
    except OSError as e:
        logger.warning(
            "Could not migrate spore sidecar %s -> %s: %s",
            old_path, new_path, e,
        )


def migrate_all_sidecars() -> int:
    """
    Move every ``*.spore.json`` left in the old ``template_configs/``
    folder into the new ``_spore/`` folder. Returns the count of files
    migrated. Safe to call repeatedly; idempotent when run on an
    already-migrated tree.
    """
    legacy_dir = get_template_path(_LEGACY_SIDECAR_DIR)
    if not os.path.isdir(legacy_dir):
        return 0
    pattern = os.path.join(legacy_dir, f"*{_SIDECAR_SUFFIX}")
    migrated = 0
    for old_path in glob.glob(pattern):
        stem = os.path.basename(old_path)[:-len(_SIDECAR_SUFFIX)]
        if not stem:
            continue
        before = os.path.isfile(_spore_sidecar_path(stem))
        _migrate_legacy_sidecar(stem)
        if not before and os.path.isfile(_spore_sidecar_path(stem)):
            migrated += 1
    return migrated


def load_sidecar(template_name: str) -> Optional[Dict[str, Any]]:
    """Return the ``.spore.json`` sidecar if present, else None."""
    _migrate_legacy_sidecar(template_name)
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


_LEGACY_TYPE_MAP = {
    # JSON-config field types → editor element types. Anything not listed
    # collapses to "text" for the editor since the JSON config widget is
    # what actually renders the value in Source Settings.
    "text": "text",
    "textarea": "text",
    "number": "text",
    "select": "text",
    "checkbox": "text",
    "color": "text",
    "switch": "text",
    "toggle": "text",
}


def _synthesize_legacy_elements(template_name: str) -> list:
    """
    Build a list of synthetic editor elements from the public JSON config.

    Walks ``template_configs/{name}.json``'s ``elements`` array. Each
    ``{"type":"separator","label":...}`` row resets the active category;
    every other entry that has an ``id`` becomes one synthetic element
    with ``position``/``size`` set to ``None`` so the editor knows not to
    render it on the canvas. The original JSON entry is kept under
    ``legacy_field`` so the save pipeline can write user edits back to
    the public JSON config without losing per-field metadata
    (description, options, min/max, ...).
    """
    # Late import: TemplateConfigParser belongs to a higher layer and a
    # top-level import would create a circular dependency at package init.
    from ..template_config_parser import TemplateConfigParser

    parser = TemplateConfigParser()
    config_path = parser.get_config_path(template_name)
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Legacy config %s could not be parsed: %s", config_path, e)
        return []

    raw_elements = config.get("elements")
    if not isinstance(raw_elements, list):
        return []

    out: list = []
    current_category = "Settings"
    for entry in raw_elements:
        if not isinstance(entry, dict):
            continue
        entry_type = str(entry.get("type") or "").lower()
        if entry_type == "separator":
            label = str(entry.get("label") or "").strip()
            if label:
                current_category = label
            continue
        eid = entry.get("id")
        if not eid:
            continue
        out.append({
            "id": str(eid),
            "type": _LEGACY_TYPE_MAP.get(entry_type, "text"),
            "category": current_category,
            "props": {"value": entry.get("value")},
            "legacy_field": entry,
            "position": None,
            "size": None,
            "bindings": [],
        })
    return out


def parse_existing(template_name: str) -> Dict[str, Any]:
    """
    Build a usable editor model for ``template_name``.

    Resolution order:

    1. If a ``.spore.json`` sidecar exists, return it verbatim.
    2. Else try the HTML reverse parser. When the legacy template has
       absolutely-positioned descendants with ``id`` attributes the
       parser produces synthetic elements with real ``position`` /
       ``size`` and a ``legacy_bindings`` map back to the JSON-config
       field IDs that drive each property. The model is flagged
       ``legacy=True`` so the editor renders the elements on the canvas
       (because positions are non-null) but the save pipeline still
       skips HTML codegen — only JSON-config field values are written
       back, never the hand-authored HTML.
    3. Fall back to the JSON-only synthesizer
       (:func:`_synthesize_legacy_elements`) — produces position-less
       Outline entries, same as before.
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

    positioned: Optional[list] = None
    try:
        positioned = reverse_parse_legacy(template_name)
    except Exception as e:  # pragma: no cover - parser is best-effort
        logger.warning(
            "Reverse parser failed for %s: %s", template_name, e,
        )
        positioned = None

    if positioned:
        design_w, design_h = design_size_from_config(template_name)
        return {
            "template_name": template_name,
            "alert_system": "queue",
            "design": {"width": design_w, "height": design_h},
            "elements": positioned,
            "advanced_js": advanced_js,
            "legacy": True,
            "legacy_source": "html_parsed",
        }

    return {
        "template_name": template_name,
        "alert_system": "queue",
        "design": {"width": 800, "height": 200},
        "elements": _synthesize_legacy_elements(template_name),
        "advanced_js": advanced_js,
        "legacy": True,
        "legacy_source": "json_config",
    }

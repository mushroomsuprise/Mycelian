#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Orchestrate Spore Studio template lifecycle: Create, Save, Delete.

Preview overrides, asset listings, and event registry are read-only.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
from typing import Any, Dict, Optional, Tuple

from . import template_codegen, template_parser_back
from ..path_utils import (
    ensure_directory_exists,
    get_assets_path,
    get_template_path,
)
from ..template_config_parser import TemplateConfigParser

logger = logging.getLogger(__name__)

# Public JSON fields always rewritten from the editor model on save (not merged
# from prior Source Settings values).
_CODEGEN_OWNED_CONFIG_VALUE_IDS = frozenset({"DesignWidth", "DesignHeight"})


def _is_codegen_owned_config_value(el_id: str) -> bool:
    """
    Config field ids whose ``value`` must follow the Spore model on save.

    Counter/data-display ``*_format`` fields are authored in Spore Studio;
    preserving stale Source Settings values breaks runtime tokens like
    ``{min}`` and ``{max}`` in OBS.
    """
    if el_id in _CODEGEN_OWNED_CONFIG_VALUE_IDS:
        return True
    return str(el_id).endswith("_format")


# First-party overlays: excluded from Spore Studio's picker and non-deletable.
SPORE_STUDIO_PROTECTED_TEMPLATES = frozenset({"activity_feed", "source_controls"})


# Names that would conflict with built-in Flask routes or our own helpers.
_RESERVED_TEMPLATE_NAMES = {
    "static",
    "assets",
    "api",
    "debug",
    "_spore_studio_editor",
}


def _clone(value: Any) -> Any:
    """Deep-clone a JSON-compatible value via roundtrip."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value


def _merge_element_arrays(new_arr: list, old_arr: list) -> list:
    """
    Vendored copy of ``merge_template_configs.merge_element_arrays``.

    The standalone installer script is not bundled with the frozen exe,
    so we keep a small private copy here. Behavior matches the upstream
    implementation: new structure wins, user-tuned ``value`` fields are
    transferred from old by ``id``, and id-less rows (separators) are
    taken from new verbatim.
    """
    if not isinstance(new_arr, list) or not isinstance(old_arr, list):
        return _clone(new_arr)

    new_has_ids = any(isinstance(el, dict) and "id" in el for el in new_arr)
    old_has_ids = any(isinstance(el, dict) and "id" in el for el in old_arr)
    if not (new_has_ids and old_has_ids):
        return _clone(new_arr)

    old_by_id: Dict[str, Dict[str, Any]] = {}
    for el in old_arr:
        if isinstance(el, dict) and "id" in el:
            key = str(el["id"])
            old_by_id.setdefault(key, el)

    result: list = []
    for new_el in new_arr:
        if isinstance(new_el, dict) and "id" in new_el:
            merged = _clone(new_el)
            old_el = old_by_id.get(str(new_el["id"]))
            if old_el is not None:
                el_id = str(new_el["id"])
                if (
                    "value" in old_el
                    and "value" in merged
                    and not _is_codegen_owned_config_value(el_id)
                ):
                    merged["value"] = _clone(old_el["value"])
                if "elements" in merged and "elements" in old_el:
                    merged["elements"] = _merge_element_arrays(
                        merged["elements"], old_el["elements"]
                    )
            result.append(merged)
        else:
            result.append(_clone(new_el))
    return result


def _merge_config(new_obj: Any, old_obj: Any) -> Any:
    """
    Vendored copy of ``merge_template_configs.merge_config``.

    The new config is the authoritative structure. User-customized ``value``
    fields and nested element arrays are transferred from ``old_obj`` where
    matching keys/ids are found.
    """
    if not isinstance(new_obj, dict) or not isinstance(old_obj, dict):
        return _clone(new_obj)

    result = _clone(new_obj)
    for key in list(result.keys()):
        if key not in old_obj:
            continue
        new_val = result[key]
        old_val = old_obj[key]
        if key == "value":
            result[key] = _clone(old_val)
        elif isinstance(new_val, list) and isinstance(old_val, list):
            result[key] = _merge_element_arrays(new_val, old_val)
        elif isinstance(new_val, dict) and isinstance(old_val, dict):
            result[key] = _merge_config(new_val, old_val)
    return result


class SporeStudioError(Exception):
    """Raised for user-facing save / create failures."""


def _validate_name(name: str) -> str:
    """
    Normalize and validate a template name.

    Allowed characters: letters, digits, underscore, hyphen. Names must not
    start with an underscore (those are reserved for boilerplate folders).
    """
    if not isinstance(name, str):
        raise SporeStudioError("Template name must be a string.")
    cleaned = name.strip()
    if not cleaned:
        raise SporeStudioError("Template name cannot be empty.")
    if not all(c.isalnum() or c in "_-" for c in cleaned):
        raise SporeStudioError(
            "Template name may only contain letters, numbers, '_' and '-'."
        )
    if cleaned.startswith("_"):
        raise SporeStudioError("Template name cannot start with '_'.")
    if cleaned.lower() in _RESERVED_TEMPLATE_NAMES:
        raise SporeStudioError(f"'{cleaned}' is reserved.")
    return cleaned


def _atomic_write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, path)


def _atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=4, ensure_ascii=False)
    os.replace(tmp_path, path)


def _load_existing_html(template_name: str) -> Optional[str]:
    path = get_template_path(f"{template_name}.html")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        logger.warning("Could not read existing template %s: %s", path, e)
        return None


def compile_preview_draft(model: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Compile an in-memory editor model to (html, json_config) for live preview.

    Does not write to disk. Raises SporeStudioError on invalid model or legacy
    templates (no codegen path).
    """
    if not isinstance(model, dict):
        raise SporeStudioError("Editor model must be a JSON object.")
    if model.get("legacy"):
        raise SporeStudioError("Live draft preview is only for Spore templates.")
    template_name = _validate_name(model.get("template_name") or "")
    existing = _load_existing_html(template_name)
    html_text, json_config = template_codegen.compile_model(
        model, existing_html=existing
    )
    return html_text, json_config


def ensure_template_assets_folder(template_name: str) -> str:
    """
    Make sure ``assets/{template_name}/`` exists and return the absolute path.

    Uses :func:`get_assets_path` so behavior matches the rest of the
    codebase regardless of whether we are running from source or frozen.
    """
    assets_dir = get_assets_path()
    ensure_directory_exists(assets_dir)
    template_dir = os.path.join(assets_dir, template_name)
    ensure_directory_exists(template_dir)
    return template_dir


def _refresh_web_engine_routes() -> None:
    """Ask the running web engine to register routes for any new template."""
    try:
        from .. import web_engine as web_engine_module
    except Exception as e:
        logger.debug("web_engine import skipped during route refresh: %s", e)
        return
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if inst is None:
        return
    try:
        inst.register_routes()
    except Exception as e:
        logger.warning("Failed to refresh web engine routes: %s", e)


def create_template(
    name: str,
    *,
    alert_system: str = "queue",
    copy_from: Optional[str] = None,
    design_width: int = 800,
    design_height: int = 200,
) -> Dict[str, Any]:
    """
    Stamp a new Spore Studio template (HTML + JSON + sidecar + asset folder).

    Args:
        name: The desired template name. Must be unique within
            ``templates/``.
        alert_system: ``queue`` or ``instant``. Selects the boilerplate.
        copy_from: Optional name of an existing Spore Studio template to
            clone. When set, the new template's editor model is a deep
            copy of ``copy_from``'s sidecar with the name updated.
        design_width: Initial canvas width in pixels.
        design_height: Initial canvas height in pixels.

    Returns:
        The newly-created editor model.

    Raises:
        SporeStudioError: when validation fails or the template already exists.
    """
    template_name = _validate_name(name)
    if alert_system not in ("queue", "instant"):
        raise SporeStudioError(
            f"alert_system must be 'queue' or 'instant', got '{alert_system}'."
        )

    html_path = get_template_path(f"{template_name}.html")
    if os.path.exists(html_path):
        raise SporeStudioError(f"Template '{template_name}' already exists.")

    if copy_from:
        copy_from = _validate_name(copy_from)
        source_model = template_parser_back.load_sidecar(copy_from)
        if not source_model:
            raise SporeStudioError(
                f"Cannot copy from '{copy_from}': it is not a Spore Studio template."
            )
        model = copy.deepcopy(source_model)
        model["template_name"] = template_name
        model.pop("legacy", None)
    else:
        model = {
            "template_name": template_name,
            "alert_system": alert_system,
            "design": {
                "width": int(design_width or 800),
                "height": int(design_height or 200),
            },
            "duration_seconds": 5,
            "queued": False,
            "elements": [],
            "advanced_js": "",
            "streamdeck_options": {"description": "", "actions": {}},
            "dynamic_controls": {"elements": []},
        }

    ensure_template_assets_folder(template_name)
    save_template(model)
    return model


def _resolve_model_path(el: Dict[str, Any], dotted: str) -> Any:
    """
    Walk a dotted path inside a model element (e.g. ``"props.font_size"``).

    Returns ``None`` when any segment is missing — callers treat that as
    "no edit to apply" and leave the JSON config field untouched.
    """
    cur: Any = el
    for segment in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(segment)
    return cur


def _resolved_legacy_binding_value(
    el: Dict[str, Any],
    model_path: str,
    design: Dict[str, Any],
) -> Any:
    """
    Compute the value to write back to the JSON config for one binding.

    For position/size, the editor model always stores the rendered
    top-left coordinates. When the original template anchored the
    element to ``bottom`` / ``right`` we have to invert the value
    against the design canvas size before writing it back, otherwise
    the next render would jump.
    """
    anchor = el.get("legacy_anchor") or {}
    raw = _resolve_model_path(el, model_path)
    if raw is None:
        return None

    if model_path == "position.y" and anchor.get("y") == "bottom":
        height = _resolve_model_path(el, "size.h")
        try:
            return int(
                round(
                    float(design.get("height") or 0) - float(raw) - float(height or 0)
                )
            )
        except (TypeError, ValueError):
            return raw
    if model_path == "position.x" and anchor.get("x") == "right":
        width = _resolve_model_path(el, "size.w")
        try:
            return int(
                round(float(design.get("width") or 0) - float(raw) - float(width or 0))
            )
        except (TypeError, ValueError):
            return raw
    return raw


def _save_legacy_template(model: Dict[str, Any], template_name: str) -> Dict[str, Any]:
    """
    Persist value edits for a legacy template without touching its HTML.

    Legacy templates are hand-authored HTML files with no
    ``.spore.json`` sidecar; their structure is loaded into the editor
    in one of two ways:

    * **JSON-only** (``legacy_source == "json_config"``): one synthetic
      element per id'd JSON config field. Edits arrive on
      ``el.props.value`` and write back to ``entry.value`` by element
      ``id``.
    * **HTML-parsed** (``legacy_source == "html_parsed"``): synthetic
      elements with a ``legacy_bindings`` map. Each map entry is
      ``"position.x" -> "TitleLeft"`` etc. — we resolve the dotted
      path on the model and write the value to the matching JSON
      config field, applying inverse anchoring for bottom/right
      positioned elements so the JSON value stays semantically correct.

    On save we must NOT regenerate the HTML (that would clobber
    hand-authored JS / animations) and we must NOT write a sidecar
    (the template would silently become a Spore Studio template on
    the next load). Only the public JSON config is touched.
    """
    parser = TemplateConfigParser()
    json_path = parser.get_config_path(template_name)
    if not os.path.isfile(json_path):
        raise SporeStudioError(
            f"Legacy template '{template_name}' has no JSON config to update."
        )

    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SporeStudioError(f"Could not read legacy config {json_path}: {e}") from e

    design = model.get("design") or {}
    edited_values: Dict[str, Any] = {}
    for el in model.get("elements") or []:
        if not isinstance(el, dict):
            continue
        eid = el.get("id")
        props = el.get("props") or {}
        if eid and "value" in props:
            edited_values[str(eid)] = props["value"]

        bindings = el.get("legacy_bindings") or {}
        if isinstance(bindings, dict):
            for model_path, field_id in bindings.items():
                if not field_id:
                    continue
                value = _resolved_legacy_binding_value(el, model_path, design)
                if value is None:
                    continue
                edited_values[str(field_id)] = value

    raw_elements = config.get("elements")
    if isinstance(raw_elements, list):
        for entry in raw_elements:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("id")
            if eid and str(eid) in edited_values:
                entry["value"] = edited_values[str(eid)]

    _atomic_write_json(json_path, config)
    _refresh_web_engine_routes()
    return model


def save_template(model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save an editor model to disk.

    For Spore Studio (non-legacy) templates this writes:

    * ``templates/{name}.html``
    * ``templates/template_configs/{name}.json``
    * ``templates/_spore/{name}.spore.json``
    * ``assets/{name}/`` (created if missing)

    For legacy templates (``model["legacy"]`` is truthy) only the public
    JSON config is updated — see :func:`_save_legacy_template`.

    The public JSON config is always regenerated from the editor model on
    save. Prior Source Settings values are not merged back in, so OBS and
    the in-app settings tab stay aligned with Spore Studio.

    Returns:
        The model as written (with name/alert_system normalized).
    """
    if not isinstance(model, dict):
        raise SporeStudioError("Editor model must be a JSON object.")

    template_name = _validate_name(model.get("template_name") or "")
    alert_system = model.get("alert_system") or "queue"
    if alert_system not in ("queue", "instant"):
        alert_system = "queue"

    model = dict(model)
    model["template_name"] = template_name
    model["alert_system"] = alert_system

    if not model.get("legacy"):
        from .timing import effective_duration_seconds

        model["duration_seconds"] = effective_duration_seconds(model)

    if model.get("legacy"):
        return _save_legacy_template(model, template_name)

    existing_html = _load_existing_html(template_name)
    html_text, json_config = template_codegen.compile_model(
        model, existing_html=existing_html
    )

    parser = TemplateConfigParser()
    json_path = parser.get_config_path(template_name)

    html_path = get_template_path(f"{template_name}.html")
    _atomic_write(html_path, html_text)
    _atomic_write_json(json_path, json_config)
    template_parser_back.save_sidecar(template_name, model)
    ensure_template_assets_folder(template_name)

    _refresh_web_engine_routes()
    return model


def delete_template(name: str) -> None:
    """
    Delete a Spore Studio template's HTML, public JSON config, sidecar, and assets.

    Refuses protected or non-Spore templates.
    """
    template_name = _validate_name(name)
    if template_name.lower() in SPORE_STUDIO_PROTECTED_TEMPLATES:
        raise SporeStudioError(
            f"Template '{template_name}' is protected and cannot be deleted."
        )
    if template_parser_back.load_sidecar(template_name) is None:
        raise SporeStudioError(
            f"'{template_name}' is not a Spore Studio template (no sidecar)."
        )

    html_path = get_template_path(f"{template_name}.html")
    parser = TemplateConfigParser()
    json_path = parser.get_config_path(template_name)

    template_parser_back.remove_sidecar(template_name)

    for path in (html_path, json_path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            raise SporeStudioError(f"Could not remove {path}: {e}") from e

    assets_dir = os.path.join(get_assets_path(), template_name)
    if os.path.isdir(assets_dir):
        try:
            shutil.rmtree(assets_dir)
        except OSError as e:
            logger.warning("Could not remove assets directory %s: %s", assets_dir, e)

    _refresh_web_engine_routes()


def list_spore_templates() -> Tuple[list, list]:
    """
    Enumerate templates by Spore Studio authorship.

    Returns:
        ``(spore_names, legacy_names)`` — each a sorted list of stems.
    """
    template_dir = get_template_path()
    if not os.path.isdir(template_dir):
        return [], []
    spore: list = []
    legacy: list = []
    for entry in sorted(os.listdir(template_dir)):
        if not entry.endswith(".html"):
            continue
        stem = entry[:-5]
        if stem.startswith("_"):
            continue
        if stem.lower() in SPORE_STUDIO_PROTECTED_TEMPLATES:
            continue
        if template_parser_back.load_sidecar(stem) is not None:
            spore.append(stem)
        else:
            legacy.append(stem)
    return spore, legacy

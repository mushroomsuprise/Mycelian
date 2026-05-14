#!/usr/bin/env python3
"""
Compile a Spore Studio editor model into the (HTML, JSON) pair Mycelian
already understands.

The editor model is the source of truth while editing and is persisted as
``templates/_spore/{name}.spore.json``. From it we deterministically
derive:

- ``templates/{name}.html`` — Jinja-rendered overlay with a stable
  ``SPORE_STUDIO:auto-*`` block for generated bindings, a ``SPORE_STUDIO:user-*``
  block for hand-edited code that must survive future saves, and a small
  ``SPORE_STUDIO:dom-*`` block holding the absolute-positioned element tree.
- ``templates/template_configs/{name}.json`` — the flat ``elements`` array
  consumed by the renderer (separators per element ``category``, fields per
  element ``props`` plus the canvas size and template title).

The output is idempotent: running ``compile_model`` twice on an unchanged
model produces byte-identical files (modulo the user-block, which we only
read, never overwrite).
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .behavior_blocks import compile_bindings
from ..path_utils import get_template_path

logger = logging.getLogger(__name__)


_AUTO_BEGIN = "// SPORE_STUDIO:auto-begin"
_AUTO_END = "// SPORE_STUDIO:auto-end"
_USER_BEGIN = "// SPORE_STUDIO:user-begin"
_USER_END = "// SPORE_STUDIO:user-end"
_DOM_BEGIN = "<!-- SPORE_STUDIO:dom-begin -->"
_DOM_END = "<!-- SPORE_STUDIO:dom-end -->"
_STYLES_BEGIN = "<!-- SPORE_STUDIO:styles-begin -->"
_STYLES_END = "<!-- SPORE_STUDIO:styles-end -->"


def _replace_block(source: str, begin: str, end: str, replacement: str) -> str:
    """
    Replace the block between two markers (both kept) with ``replacement``.

    If the markers are missing the source is returned unchanged. Lines around
    the markers (including their indentation) are preserved.
    """
    pattern = re.compile(
        re.escape(begin) + r"[\s\S]*?" + re.escape(end),
        re.MULTILINE,
    )
    if not pattern.search(source):
        return source
    return pattern.sub(begin + "\n" + replacement.rstrip("\n") + "\n" + end, source)


def _extract_block(source: str, begin: str, end: str) -> str:
    """Return the text between two markers (markers excluded), or '' if absent."""
    pattern = re.compile(
        re.escape(begin) + r"\n?([\s\S]*?)\n?" + re.escape(end),
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return ""
    return m.group(1)


def _slugify_id(value: str) -> str:
    """Reduce a string to characters legal in HTML ids (and JS variable names)."""
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or ""))
    return value.strip("_") or "el"


def _css_kv(props: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Collapse the editor-model ``props`` block into a list of CSS declarations.

    Keys are interpreted lazily: pixel values use the ``_px`` suffix, colors
    and font properties pass through as raw strings. Unknown keys are emitted
    verbatim under their kebab-cased name. Returning a list of tuples (rather
    than a dict) keeps the order deterministic for snapshot stability.
    """
    out: List[Tuple[str, str]] = []
    if not isinstance(props, dict):
        return out

    pixel_keys = {
        "font_size", "border_radius", "border_width", "padding", "margin",
        "letter_spacing", "line_height_px",
    }
    raw_keys = {
        "color", "background", "background_color", "border_color",
        "font_family", "font_weight", "text_align", "opacity", "z_index",
    }

    for key, value in props.items():
        if value in (None, ""):
            continue
        css_key = key.replace("_", "-")
        if key in pixel_keys:
            out.append((css_key, f"{value}px"))
        elif key in raw_keys:
            out.append((css_key, str(value)))
        elif key == "text":
            continue
        elif key == "src":
            continue
        else:
            out.append((css_key, str(value)))
    return out


_SHOW_BIND_ACTIONS = frozenset({"show", "show_for"})


def _binding_has_show_action(binding: Dict[str, Any]) -> bool:
    """True if the primary or any chained step uses ``show`` or ``show_for``."""
    if not isinstance(binding, dict):
        return False
    if binding.get("action") in _SHOW_BIND_ACTIONS:
        return True
    for row in binding.get("chain") or []:
        if isinstance(row, dict) and row.get("action") in _SHOW_BIND_ACTIONS:
            return True
    return False


def _element_start_hidden(element: Dict[str, Any]) -> bool:
    """
    Whether the element should be emitted with ``data-spore-hidden="true"``.

    If ``start_hidden`` is set on the element model, that explicit bool wins.
    Otherwise (auto): hidden when the element has any binding whose primary
    or chained action is ``show`` or ``show_for``.
    """
    if "start_hidden" in element:
        return bool(element.get("start_hidden"))
    for binding in element.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        if _binding_has_show_action(binding):
            return True
    return False


def _element_style(element: Dict[str, Any]) -> str:
    """Compose the inline ``style`` attribute for a single absolute-positioned element."""
    pos = element.get("position") or {}
    size = element.get("size") or {}
    x = pos.get("x", 0)
    y = pos.get("y", 0)
    w = size.get("w")
    h = size.get("h")
    declarations: List[Tuple[str, str]] = [
        ("left", f"{x}px"),
        ("top", f"{y}px"),
    ]
    if w is not None:
        declarations.append(("width", f"{w}px"))
    if h is not None:
        declarations.append(("height", f"{h}px"))
    declarations.extend(_css_kv(element.get("props") or {}))
    return "; ".join(f"{k}: {v}" for k, v in declarations)


def _render_element(element: Dict[str, Any]) -> str:
    """
    Render one editor element as the HTML snippet inserted into the DOM block.

    Note: the snippet uses the renderer's existing ``{{ Identifier }}`` Jinja
    convention for the element's text/src so that values can be overridden via
    the JSON config without regenerating HTML — for example, ``ContainerWidth``
    in alerts.json drives the rendered element directly.
    """
    eid = _slugify_id(element.get("id"))
    etype = (element.get("type") or "container").lower()
    props = element.get("props") or {}
    style = _element_style(element)
    classes = "spore-element"
    hidden_attr = (
        ' data-spore-hidden="true"' if _element_start_hidden(element) else ""
    )

    if etype == "text":
        text_var = element.get("text_var") or eid + "Text"
        text_default = props.get("text", "")
        return (
            f'<div id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'data-spore-type="text">'
            f"{{{{ {text_var}|default({json.dumps(text_default)})|safe }}}}"
            f"</div>"
        )

    if etype == "image":
        src_var = element.get("src_var") or eid + "Src"
        src_default = props.get("src", "")
        return (
            f'<img id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'data-spore-type="image" '
            f'src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}" '
            f'alt="" />'
        )

    if etype == "video":
        src_var = element.get("src_var") or eid + "Src"
        src_default = props.get("src", "")
        autoplay = "autoplay " if props.get("autoplay") else ""
        loop = "loop " if props.get("loop") else ""
        muted = "muted " if props.get("muted", True) else ""
        return (
            f'<video id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'data-spore-type="video" '
            f"{autoplay}{loop}{muted}playsinline>"
            f'<source src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}">'
            f"</video>"
        )

    if etype == "audio":
        src_var = element.get("src_var") or eid + "Src"
        src_default = props.get("src", "")
        return (
            f'<audio id="{html.escape(eid)}" class="{classes}"{hidden_attr} '
            f'data-spore-type="audio" preload="auto">'
            f'<source src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}">'
            f"</audio>"
        )

    return (
        f'<div id="{html.escape(eid)}" class="{classes}" '
        f'style="{html.escape(style, quote=True)}"{hidden_attr} '
        f'data-spore-type="container"></div>'
    )


def _boilerplate_path(alert_system: str) -> str:
    name = "queue_alert" if alert_system == "queue" else "instant_alert"
    return os.path.join(get_template_path("_boilerplates"), f"{name}.html")


def _load_boilerplate(alert_system: str) -> str:
    path = _boilerplate_path(alert_system)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _expose_field(element: Dict[str, Any], field_key: str) -> bool:
    """When False, the field is inlined in HTML only (omitted from template JSON)."""
    mm = element.get("source_settings_expose")
    if not isinstance(mm, dict):
        return True
    if field_key not in mm:
        return True
    return bool(mm[field_key])


def _sanitize_streamdeck_options(raw: Any) -> Dict[str, Any]:
    """
    Normalize editor ``streamdeck_options`` for ``template_configs/*.json``.

    Matches the shape used by templates like ``counter.json``: ``description``
    plus ``actions`` (mapping of action id to name/description/event/default_data).
    """
    out: Dict[str, Any] = {"description": "", "actions": {}}
    if not isinstance(raw, dict):
        return out
    desc = raw.get("description")
    if desc is not None:
        out["description"] = str(desc)
    actions_in = raw.get("actions")
    if not isinstance(actions_in, dict):
        return out
    actions_out: Dict[str, Any] = {}
    for action_id, spec in actions_in.items():
        aid = _slugify_id(str(action_id))
        if not aid or aid == "el":
            continue
        if not isinstance(spec, dict):
            continue
        event_name = str(spec.get("event") or "").strip()
        if not event_name:
            continue
        default_data = spec.get("default_data")
        if default_data is None:
            dd: Dict[str, Any] = {}
        elif isinstance(default_data, dict):
            dd = dict(default_data)
        else:
            dd = {}
        actions_out[aid] = {
            "name": str(spec.get("name") or aid),
            "description": str(spec.get("description") or ""),
            "event": event_name,
            "default_data": dd,
        }
    out["actions"] = actions_out
    return out


def _merge_streamdeck_binding_args_into_actions(
    elements: Any, streamdeck_options: Dict[str, Any]
) -> None:
    """
    Copy binding ``args`` from Stream-Deck-triggered rows into each action's
    ``default_data`` so HTTP / plugin presses send the same payload shape as
    the editor (filters, ``streamdeck_template_action``, ``from_payload``, etc.).
    Merges the primary binding ``args`` and each ``chain`` step's ``args`` (later
    keys override earlier ones). Mutates ``streamdeck_options`` in place
    (post-:func:`_sanitize_streamdeck_options`).
    """
    actions = streamdeck_options.get("actions")
    if not isinstance(actions, dict) or not actions:
        return
    if not isinstance(elements, list):
        return
    for element in elements:
        if not isinstance(element, dict):
            continue
        for binding in element.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            if binding.get("trigger") != "streamdeck":
                continue
            aid_raw = binding.get("streamdeck_action")
            if not aid_raw:
                continue
            aid = _slugify_id(str(aid_raw))
            if aid not in actions or not isinstance(actions[aid], dict):
                continue
            merged_chunks: Dict[str, Any] = {}
            args = binding.get("args")
            if isinstance(args, dict):
                merged_chunks.update(args)
            for row in binding.get("chain") or []:
                if not isinstance(row, dict):
                    continue
                ca = row.get("args")
                if isinstance(ca, dict):
                    merged_chunks.update(ca)
            if not merged_chunks:
                continue
            spec = actions[aid]
            dd = spec.get("default_data")
            if not isinstance(dd, dict):
                dd = {}
            merged = dict(dd)
            merged.update(merged_chunks)
            spec["default_data"] = merged


def _derived_json_config(model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the public ``templates/template_configs/{name}.json`` from the model.

    We emit:

    1. A ``Canvas`` separator with ``DesignWidth`` / ``DesignHeight`` /
       ``TemplateTitle`` so the existing Source Settings tab can edit them.
    2. One separator per distinct element ``category`` followed by the
       text/src/numeric properties exposed by that element. Element ids are
       suffixed by their property name to avoid collisions.

    The separator labels match the editor model's category names verbatim,
    which is the convention ``customsources.group_config_elements`` uses.
    """
    template_name = model.get("template_name") or "untitled"
    design = model.get("design") or {}
    width = int(design.get("width") or 800)
    height = int(design.get("height") or 200)

    elements_out: List[Dict[str, Any]] = [
        {"type": "separator", "label": "Canvas"},
        {
            "type": "number",
            "id": "DesignWidth",
            "label": "Design Width",
            "value": width,
            "min": 320,
            "max": 7680,
            "description": "Logical canvas width in pixels.",
        },
        {
            "type": "number",
            "id": "DesignHeight",
            "label": "Design Height",
            "value": height,
            "min": 240,
            "max": 4320,
            "description": "Logical canvas height in pixels.",
        },
        {
            "type": "text",
            "id": "TemplateTitle",
            "label": "Title",
            "value": str(model.get("title") or template_name),
            "description": "Browser title for this overlay.",
        },
    ]

    seen_categories: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for element in model.get("elements") or []:
        if not isinstance(element, dict):
            continue
        category = (element.get("category") or "Elements").strip() or "Elements"
        if category not in seen_categories:
            seen_categories.append(category)
        grouped.setdefault(category, []).append(element)

    for category in seen_categories:
        elements_out.append({"type": "separator", "label": category})
        for element in grouped[category]:
            eid = _slugify_id(element.get("id"))
            etype = (element.get("type") or "container").lower()
            props = element.get("props") or {}

            if etype == "text":
                text_var = element.get("text_var") or eid + "Text"
                if _expose_field(element, "text"):
                    elements_out.append(
                        {
                            "type": "text",
                            "id": text_var,
                            "label": f"{element.get('id', eid)} text",
                            "value": str(props.get("text", "")),
                            "description":
                                f"Text shown by element '{element.get('id', eid)}'.",
                        }
                    )
            elif etype in ("image", "video", "audio"):
                src_var = element.get("src_var") or eid + "Src"
                if _expose_field(element, "src"):
                    elements_out.append(
                        {
                            "type": "text",
                            "id": src_var,
                            "label": f"{element.get('id', eid)} source URL",
                            "value": str(props.get("src", "")),
                            "description":
                                f"Source URL or path for element "
                                f"'{element.get('id', eid)}'.",
                        }
                    )

            if etype == "video":
                vdefaults = {"autoplay": True, "loop": False, "muted": True}
                for bkey, blabel in (
                    ("autoplay", "autoplay"),
                    ("loop", "loop"),
                    ("muted", "muted"),
                ):
                    if not _expose_field(element, bkey):
                        continue
                    raw = props.get(bkey, vdefaults[bkey])
                    elements_out.append(
                        {
                            "type": "checkbox",
                            "id": f"{eid}_{bkey}",
                            "label": f"{element.get('id', eid)} {blabel}",
                            "value": bool(raw),
                            "description": f"{blabel.title()} for "
                            f"'{element.get('id', eid)}'.",
                        }
                    )

            for key in ("color", "background_color", "font_family"):
                if props.get(key) in (None, ""):
                    continue
                if not _expose_field(element, key):
                    continue
                elements_out.append(
                    {
                        "type": "color" if "color" in key else "text",
                        "id": f"{eid}_{key}",
                        "label": f"{element.get('id', eid)} {key.replace('_', ' ')}",
                        "value": str(props[key]),
                        "description":
                            f"{key.replace('_', ' ').title()} for "
                            f"'{element.get('id', eid)}'.",
                    }
                )

            for key in ("font_weight", "text_align"):
                if props.get(key) in (None, ""):
                    continue
                if not _expose_field(element, key):
                    continue
                elements_out.append(
                    {
                        "type": "text",
                        "id": f"{eid}_{key}",
                        "label": f"{element.get('id', eid)} {key.replace('_', ' ')}",
                        "value": str(props[key]),
                        "description":
                            f"{key.replace('_', ' ').title()} for "
                            f"'{element.get('id', eid)}'.",
                    }
                )

            for key in ("font_size", "border_radius", "border_width"):
                if props.get(key) in (None, ""):
                    continue
                if not _expose_field(element, key):
                    continue
                try:
                    value = int(props[key])
                except (TypeError, ValueError):
                    continue
                elements_out.append(
                    {
                        "type": "number",
                        "id": f"{eid}_{key}",
                        "label": f"{element.get('id', eid)} {key.replace('_', ' ')}",
                        "value": value,
                        "min": 0,
                        "max": 4096,
                        "description":
                            f"{key.replace('_', ' ').title()} for "
                            f"'{element.get('id', eid)}' (pixels).",
                    }
                )

            if props.get("opacity") not in (None, "") and _expose_field(
                element, "opacity"
            ):
                try:
                    opv = float(props["opacity"])
                except (TypeError, ValueError):
                    opv = None
                if opv is not None:
                    elements_out.append(
                        {
                            "type": "number",
                            "id": f"{eid}_opacity",
                            "label": f"{element.get('id', eid)} opacity",
                            "value": opv,
                            "min": 0,
                            "max": 1,
                            "step": 0.05,
                            "description": f"Opacity for '{element.get('id', eid)}'.",
                        }
                    )

            if props.get("border_color") not in (None, "") and _expose_field(
                element, "border_color"
            ):
                elements_out.append(
                    {
                        "type": "color",
                        "id": f"{eid}_border_color",
                        "label": f"{element.get('id', eid)} border color",
                        "value": str(props["border_color"]),
                        "description": (
                            f"Border color for '{element.get('id', eid)}'."
                        ),
                    }
                )

    sdo = _sanitize_streamdeck_options(model.get("streamdeck_options"))
    _merge_streamdeck_binding_args_into_actions(model.get("elements") or [], sdo)
    base: Dict[str, Any] = {
        "template_name": template_name,
        "spore_studio": True,
        "alert_system": str(model.get("alert_system") or "queue"),
        "elements": elements_out,
        "streamdeck_options": sdo,
    }
    return base


def compile_model(
    model: Dict[str, Any],
    *,
    existing_html: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Compile an editor model into ``(html_text, json_config)``.

    Args:
        model: The editor sidecar dict (see ``modules/spore_studio/__init__.py``).
        existing_html: Current contents of ``templates/{name}.html`` if any.
            When provided the user-block (everything between
            ``SPORE_STUDIO:user-begin`` / ``-end``) is preserved verbatim,
            and any structural deltas the user made outside the auto/dom
            blocks are kept. When None we start from the boilerplate matching
            ``model["alert_system"]``.

    Returns:
        Tuple of HTML string and JSON config dict, both ready to write.
    """
    alert_system = str(model.get("alert_system") or "queue")
    base_html = existing_html if existing_html else _load_boilerplate(alert_system)

    template_name = model.get("template_name") or "untitled"
    base_html = base_html.replace("__TEMPLATE_NAME__", template_name)

    elements = model.get("elements") or []
    dom_html = "\n".join(
        "        " + _render_element(el) for el in elements if isinstance(el, dict)
    )

    bindings = compile_bindings(elements)
    advanced_js = str(model.get("advanced_js") or "").rstrip()

    out_html = _replace_block(base_html, _DOM_BEGIN, _DOM_END, dom_html)

    auto_replacement = bindings["js"]
    out_html = _replace_block(out_html, _AUTO_BEGIN, _AUTO_END, auto_replacement)

    if existing_html:
        existing_user = _extract_block(existing_html, _USER_BEGIN, _USER_END).strip()
        if existing_user and not advanced_js:
            advanced_js = existing_user
    if advanced_js:
        out_html = _replace_block(
            out_html, _USER_BEGIN, _USER_END, advanced_js
        )

    if bindings["css"]:
        styles = "<style id=\"spore-studio-anim\">\n" + bindings["css"] + "\n</style>"
        out_html = _replace_block(out_html, _STYLES_BEGIN, _STYLES_END, styles)
    else:
        out_html = _replace_block(out_html, _STYLES_BEGIN, _STYLES_END, "")

    json_config = _derived_json_config(model)
    return out_html, json_config


def extract_user_js(html_text: str) -> str:
    """Public helper exposing the user-block extraction for round-tripping."""
    return _extract_block(html_text, _USER_BEGIN, _USER_END).strip()

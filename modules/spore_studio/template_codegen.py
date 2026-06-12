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
from .fonts_registry import (
    exposed_element_font_family,
    font_css_family_name,
    is_font_filename,
    resolve_font_filename,
)
from .spore_data_codegen import (
    DESIGN_CANVAS_MIN_PX,
    compile_spore_data_features,
    counter_format_config_id,
    counter_image_default_src_id,
    counter_image_range_max_id,
    counter_image_range_min_id,
    counter_image_range_src_id,
    inject_data_runtime_block,
)
from .timing import effective_duration_seconds
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
    match = pattern.search(source)
    if not match:
        return source
    body = begin + "\n" + replacement.rstrip("\n") + "\n" + end
    return source[: match.start()] + body + source[match.end() :]


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


_NON_STYLE_PROP_KEYS = frozenset(
    {
        "text",
        "src",
        "autoplay",
        "loop",
        "muted",
        "visual_kind",
        "audio_src",
        "volume",
        "fade_in_ms",
        "fade_out_ms",
        "audio_volume",
        "audio_fade_in_ms",
        "audio_fade_out_ms",
    }
)


def _register_font_lookup(lookup: Dict[str, str], raw: str, family: str) -> None:
    """Alias raw prop values and resolved filenames to a CSS ``font-family`` name."""
    lookup[raw] = family
    resolved = resolve_font_filename(raw)
    if resolved:
        lookup[resolved] = family
        stem = os.path.splitext(resolved)[0]
        if stem:
            lookup[stem] = family


def _compile_spore_font_css(
    elements: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, str]]:
    """
    Emit ``@font-face`` rules and a lookup table for inline ``font-family`` values.

    Exposed fonts use a per-element family plus a Jinja variable for the file path.
    """
    lines: List[str] = []
    lookup: Dict[str, str] = {}
    seen_static: Dict[str, str] = {}

    for el in elements or []:
        if not isinstance(el, dict):
            continue
        props = el.get("props") or {}
        raw = str(props.get("font_family") or "").strip()
        if not raw:
            continue
        eid = _slugify_id(el.get("id"))

        if _expose_field(el, "font_family"):
            fam = exposed_element_font_family(eid)
            default_fn = resolve_font_filename(raw) or raw
            jvar = f"{eid}_font_family"
            jinja_src = "{{ " + jvar + "|default(" + json.dumps(default_fn) + ") }}"
            lines.append(
                f'@font-face {{ font-family: "{fam}"; '
                f'src: url("/assets/default_assets/fonts/{jinja_src}"); }}'
            )
            _register_font_lookup(lookup, raw, fam)
            continue

        filename = resolve_font_filename(raw)
        if not filename or filename in seen_static:
            if filename and filename in seen_static:
                _register_font_lookup(lookup, raw, seen_static[filename])
            continue
        fam = font_css_family_name(filename)
        seen_static[filename] = fam
        esc_name = html.escape(filename, quote=True)
        lines.append(
            f'@font-face {{ font-family: "{fam}"; '
            f'src: url("/assets/default_assets/fonts/{esc_name}"); }}'
        )
        _register_font_lookup(lookup, raw, fam)

    return "\n".join(lines), lookup


def _css_kv(
    props: Dict[str, Any],
    *,
    font_map: Optional[Dict[str, str]] = None,
    element: Optional[Dict[str, Any]] = None,
) -> List[Tuple[str, str]]:
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
        if key in _NON_STYLE_PROP_KEYS:
            continue
        if value in (None, ""):
            continue
        if key == "vertical_align":
            va = str(value).strip().lower()
            if va in ("center", "bottom"):
                out.append(("display", "flex"))
                out.append(("flex-direction", "column"))
                jc = "center" if va == "center" else "flex-end"
                out.append(("justify-content", jc))
            continue
        css_key = key.replace("_", "-")
        if key in pixel_keys:
            out.append((css_key, f"{value}px"))
        elif key == "font_family":
            raw_ff = str(value).strip()
            fam = (font_map or {}).get(raw_ff)
            if not fam and font_map:
                resolved = resolve_font_filename(raw_ff)
                if resolved:
                    fam = font_map.get(resolved)
                if not fam:
                    stem = os.path.splitext(raw_ff)[0]
                    fam = font_map.get(stem)
            if fam:
                out.append(("font-family", fam))
            elif raw_ff:
                out.append(("font-family", raw_ff))
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


def _placement_base(
    parent_w: float,
    parent_h: float,
    child_w: float,
    child_h: float,
    anchor_h: str,
    anchor_v: str,
) -> Tuple[float, float]:
    """Pixel offset of child top-left for anchor presets inside a container."""
    ah = str(anchor_h or "left").strip().lower()
    av = str(anchor_v or "top").strip().lower()
    if ah == "center":
        bx = (parent_w - child_w) / 2.0
    elif ah == "right":
        bx = parent_w - child_w
    else:
        bx = 0.0
    if av == "center":
        by = (parent_h - child_h) / 2.0
    elif av == "bottom":
        by = parent_h - child_h
    else:
        by = 0.0
    return bx, by


def _resolve_child_position(
    child: Dict[str, Any],
    parent: Optional[Dict[str, Any]],
) -> Tuple[int, int]:
    """Resolve left/top for a nested element (anchor + offset or legacy position)."""
    if parent is None:
        pos = child.get("position") or {}
        return int(pos.get("x", 0) or 0), int(pos.get("y", 0) or 0)
    placement = child.get("placement")
    if isinstance(placement, dict):
        psize = parent.get("size") or {}
        csize = child.get("size") or {}
        pw = float(psize.get("w") or 0)
        ph = float(psize.get("h") or 0)
        cw = float(csize.get("w") or 0)
        ch = float(csize.get("h") or 0)
        ah = str(placement.get("anchor_h") or "left")
        av = str(placement.get("anchor_v") or "top")
        bx, by = _placement_base(pw, ph, cw, ch, ah, av)
        try:
            ox = int(placement.get("offset_x", 0) or 0)
        except (TypeError, ValueError):
            ox = 0
        try:
            oy = int(placement.get("offset_y", 0) or 0)
        except (TypeError, ValueError):
            oy = 0
        return int(bx) + ox, int(by) + oy
    pos = child.get("position") or {}
    return int(pos.get("x", 0) or 0), int(pos.get("y", 0) or 0)


def _element_style(
    element: Dict[str, Any],
    *,
    parent: Optional[Dict[str, Any]] = None,
    font_map: Optional[Dict[str, str]] = None,
) -> str:
    """Compose the inline ``style`` attribute for a single absolute-positioned element."""
    x, y = _resolve_child_position(element, parent)
    size = element.get("size") or {}
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
    declarations.extend(
        _css_kv(element.get("props") or {}, font_map=font_map, element=element)
    )
    return "; ".join(f"{k}: {v}" for k, v in declarations)


def _fmt_audio_data_attrs(vol: Any, fade_in_ms: Any, fade_out_ms: Any) -> str:
    """HTML data attributes consumed by boilerplate audio helpers."""
    try:
        if vol is None or vol == "":
            vol_s = "1"
        else:
            v = float(vol)
            v = max(0.0, min(1.0, v))
            vol_s = str(v)
    except (TypeError, ValueError):
        vol_s = "1"
    try:
        fin_i = max(0, int(float(fade_in_ms or 0)))
    except (TypeError, ValueError):
        fin_i = 0
    try:
        fout_i = max(0, int(float(fade_out_ms or 0)))
    except (TypeError, ValueError):
        fout_i = 0
    fin_s = str(fin_i)
    fout_s = str(fout_i)
    return (
        f'data-spore-audio-volume="{html.escape(vol_s, quote=True)}" '
        f'data-spore-audio-fade-in="{html.escape(fin_s, quote=True)}" '
        f'data-spore-audio-fade-out="{html.escape(fout_s, quote=True)}"'
    )


def _fmt_anim_data_attrs(element: Dict[str, Any]) -> str:
    """HTML data attributes for sporeShow / sporeHide entrance and exit animations."""
    anims = element.get("animations")
    if not isinstance(anims, dict):
        anims = {}
    anim_in = str(anims.get("anim_in") or "none").strip()
    anim_out = str(anims.get("anim_out") or "none").strip()
    if anim_in not in (
        "none", "fade", "slideIn", "slideOut", "scaleIn", "scaleOut",
    ):
        anim_in = "none"
    if anim_out not in (
        "none", "fade", "slideIn", "slideOut", "scaleIn", "scaleOut",
    ):
        anim_out = "none"
    try:
        in_ms = max(0, int(float(anims.get("anim_in_ms", 300))))
    except (TypeError, ValueError):
        in_ms = 300
    try:
        out_ms = max(0, int(float(anims.get("anim_out_ms", 300))))
    except (TypeError, ValueError):
        out_ms = 300
    try:
        delay_ms = max(0, int(float(anims.get("anim_delay_ms", 0))))
    except (TypeError, ValueError):
        delay_ms = 0
    easing = str(anims.get("anim_easing") or "ease-out").strip() or "ease-out"
    return (
        f'data-spore-anim-in="{html.escape(anim_in, quote=True)}" '
        f'data-spore-anim-out="{html.escape(anim_out, quote=True)}" '
        f'data-spore-anim-in-ms="{in_ms}" '
        f'data-spore-anim-out-ms="{out_ms}" '
        f'data-spore-anim-delay-ms="{delay_ms}" '
        f'data-spore-anim-easing="{html.escape(easing, quote=True)}"'
    )


def _render_element(
    element: Dict[str, Any],
    *,
    children_inner_markup: str = "",
    parent: Optional[Dict[str, Any]] = None,
    font_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Render one editor element as the HTML snippet inserted into the DOM block.

    For ``container`` types, optional ``children_inner_markup`` is inserted
    verbatim before the closing ``</div>`` (typically leading with a newline
    when nesting children).

    Note: the snippet uses the renderer's existing ``{{ Identifier }}`` Jinja
    convention for the element's text/src so that values can be overridden via
    the JSON config without regenerating HTML — for example, ``ContainerWidth``
    in alerts.json drives the rendered element directly.
    """
    eid = _slugify_id(element.get("id"))
    etype = (element.get("type") or "container").lower()
    props = element.get("props") or {}
    style = _element_style(element, parent=parent, font_map=font_map)
    classes = "spore-element"
    hidden_attr = (
        ' data-spore-hidden="true"' if _element_start_hidden(element) else ""
    )
    anim_attrs = _fmt_anim_data_attrs(element)
    text_mode = str(element.get("text_mode") or "static").strip().lower()
    if text_mode not in ("static", "counter", "data_display"):
        text_mode = "static"
    mode_attr = f' data-spore-text-mode="{html.escape(text_mode, quote=True)}"'

    if etype == "text":
        text_var = element.get("text_var") or eid + "Text"
        text_default = props.get("text", "")
        jinja_var = text_var
        if text_mode == "counter":
            cfg = element.get("counter") if isinstance(element.get("counter"), dict) else {}
            fmt = str(cfg.get("format") or "{value}")
            try:
                init = cfg.get("initial_value", 0)
            except (TypeError, ValueError):
                init = 0
            text_default = fmt.replace("{value}", str(init))
            jinja_var = counter_format_config_id(eid)
        elif text_mode == "data_display":
            cfg = element.get("data_display") if isinstance(element.get("data_display"), dict) else {}
            text_default = str(
                cfg.get("default_text") if cfg.get("default_text") is not None else "—"
            )
            jinja_var = counter_format_config_id(eid)
        return (
            f'<div id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'{anim_attrs} '
            f'data-spore-type="text"{mode_attr}>'
            f"{{{{ {jinja_var}|default({json.dumps(text_default)})|safe }}}}"
            f"</div>"
        )

    if etype == "image":
        src_var = element.get("src_var") or eid + "Src"
        src_mode = str(element.get("src_mode") or "static").strip().lower()
        counter_attrs = ""
        jinja_src_var = src_var
        if src_mode == "from_counter":
            cs = (
                element.get("counter_src")
                if isinstance(element.get("counter_src"), dict)
                else {}
            )
            src_default = str(cs.get("default_src") or props.get("src") or "")
            cid = _slugify_id(str(cs.get("counter_id") or ""))
            jinja_src_var = counter_image_default_src_id(eid)
            counter_attrs = (
                f' data-spore-src-mode="counter" '
                f'data-spore-counter-id="{html.escape(cid, quote=True)}"'
            )
        else:
            src_default = props.get("src", "")
        return (
            f'<img id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'{anim_attrs} '
            f'data-spore-type="image"{counter_attrs} '
            f'src="{{{{ {jinja_src_var}|default({json.dumps(src_default)}) }}}}" '
            f'alt="" />'
        )

    if etype == "video":
        src_var = element.get("src_var") or eid + "Src"
        src_default = props.get("src", "")
        visual_kind = str(props.get("visual_kind") or "video").strip().lower()
        if visual_kind != "image":
            visual_kind = "video"
        autoplay = "autoplay " if props.get("autoplay") else ""
        loop = "loop " if props.get("loop") else ""
        muted = "muted " if props.get("muted", True) else ""

        audio_src_default = props.get("audio_src") or ""
        audio_src_var = element.get("audio_src_var") or eid + "AudioSrc"
        audio_attrs = _fmt_audio_data_attrs(
            props.get("audio_volume", 1),
            props.get("audio_fade_in_ms", 0),
            props.get("audio_fade_out_ms", 0),
        )
        if visual_kind == "image":
            inner = (
                f'<img class="spore-video-visual" '
                f'src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}" '
                f'alt="" />'
            )
        else:
            inner = (
                f'<video class="spore-video-visual" '
                f'data-spore-type="video" '
                f"{autoplay}{loop}{muted}playsinline>"
                f'<source src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}">'
                f"</video>"
            )
        nested_audio = (
            f'<audio id="{html.escape(eid)}_sporeAudio" '
            f'class="spore-element-nested-audio" preload="auto" {audio_attrs}>'
            f'<source src="{{{{ {audio_src_var}|default({json.dumps(audio_src_default)}) }}}}">'
            f"</audio>"
        )
        return (
            f'<div id="{html.escape(eid)}" class="{classes}" '
            f'style="{html.escape(style, quote=True)}"{hidden_attr} '
            f'{anim_attrs} '
            f'data-spore-type="video" '
            f'data-spore-visual-kind="{html.escape(visual_kind, quote=True)}">'
            f"{inner}{nested_audio}"
            f"</div>"
        )

    if etype == "audio":
        src_var = element.get("src_var") or eid + "Src"
        src_default = props.get("src", "")
        audio_attrs = _fmt_audio_data_attrs(
            props.get("volume", 1),
            props.get("fade_in_ms", 0),
            props.get("fade_out_ms", 0),
        )
        return (
            f'<audio id="{html.escape(eid)}" class="{classes}"{hidden_attr} '
            f'{anim_attrs} '
            f'data-spore-type="audio" preload="auto" {audio_attrs}>'
            f'<source src="{{{{ {src_var}|default({json.dumps(src_default)}) }}}}">'
            f"</audio>"
        )

    return (
        f'<div id="{html.escape(eid)}" class="{classes}" '
        f'style="{html.escape(style, quote=True)}"{hidden_attr} '
        f'{anim_attrs} '
        f'data-spore-type="container">{children_inner_markup}</div>'
    )


_DOM_ROW_INDENT_BASE = "        "


def _partition_elements_for_dom(
    elements: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Split canonical ``elements`` into DOM roots and ordered child buckets.

    Mirrors the editor-side rules: unknown / non-container parents behave as if
    the element had no ``parent_id`` (roots).
    """
    usable: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for raw in elements:
        if not isinstance(raw, dict):
            continue
        eid_raw = raw.get("id")
        if eid_raw is None or eid_raw == "":
            continue
        ks = str(eid_raw)
        usable.append(raw)
        by_id[ks] = raw

    roots: List[Dict[str, Any]] = []
    kids: Dict[str, List[Dict[str, Any]]] = {}

    for el in usable:
        pid = el.get("parent_id")
        if pid is None or str(pid).strip() == "":
            roots.append(el)
            continue
        pk = str(pid)
        parent = by_id.get(pk)
        if parent is None:
            roots.append(el)
            continue
        if str(parent.get("type") or "container").lower() != "container":
            roots.append(el)
            continue
        kids.setdefault(pk, []).append(el)

    return roots, kids


def _emit_dom_recursive(
    el: Dict[str, Any],
    children_map: Dict[str, List[Dict[str, Any]]],
    rel_indent: str,
    *,
    parent: Optional[Dict[str, Any]] = None,
    font_map: Optional[Dict[str, str]] = None,
) -> str:
    """Return fully-indented subtree markup beginning with ``_DOM_ROW_INDENT_BASE``."""
    etype = str(el.get("type") or "container").lower()
    eid_raw = el.get("id")
    cid_key = "" if eid_raw in (None, "") else str(eid_raw)

    if etype != "container":
        return (
            _DOM_ROW_INDENT_BASE
            + rel_indent
            + _render_element(el, parent=parent, font_map=font_map)
        )

    nested = children_map.get(cid_key, [])
    if not nested:
        return (
            _DOM_ROW_INDENT_BASE
            + rel_indent
            + _render_element(
                el, children_inner_markup="", parent=parent, font_map=font_map
            )
        )

    child_indent = rel_indent + "    "
    child_chunks = [
        _emit_dom_recursive(
            ch, children_map, child_indent, parent=el, font_map=font_map
        )
        for ch in nested
    ]
    inner_markup = "\n" + "\n".join(child_chunks)
    return (
        _DOM_ROW_INDENT_BASE
        + rel_indent
        + _render_element(
            el, children_inner_markup=inner_markup, parent=parent, font_map=font_map
        )
    )


def _render_dom_tree(
    elements: Any,
    *,
    font_map: Optional[Dict[str, str]] = None,
) -> str:
    if not isinstance(elements, list):
        return ""
    dicts_only = [e for e in elements if isinstance(e, dict)]
    if not dicts_only:
        return ""
    roots, cmap = _partition_elements_for_dom(dicts_only)
    return "\n".join(
        _emit_dom_recursive(r, cmap, "", font_map=font_map) for r in roots
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
            "min": DESIGN_CANVAS_MIN_PX,
            "max": 7680,
            "description": "Logical canvas width in pixels.",
        },
        {
            "type": "number",
            "id": "DesignHeight",
            "label": "Design Height",
            "value": height,
            "min": DESIGN_CANVAS_MIN_PX,
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
        {
            "type": "number",
            "id": "Duration",
            "label": "Duration",
            "value": effective_duration_seconds(model),
            "min": 0.1,
            "max": 600,
            "step": 0.1,
            "description": (
                "Display/hold duration in seconds for alert queue integration "
                "(point-reward templates matching this name)."
            ),
        },
        {
            "type": "checkbox",
            "id": "Queued",
            "label": "Queued",
            "value": bool(model.get("queued")),
            "description": (
                "When enabled, point redemptions whose reward title matches "
                "this template name can hold the main alert queue."
            ),
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
                text_mode = str(element.get("text_mode") or "static").strip().lower()
                if text_mode == "counter" and _expose_field(element, "text"):
                    cfg = (
                        element.get("counter")
                        if isinstance(element.get("counter"), dict)
                        else {}
                    )
                    fmt_id = counter_format_config_id(eid)
                    elements_out.append(
                        {
                            "type": "text",
                            "id": fmt_id,
                            "label": f"{element.get('id', eid)} format",
                            "value": str(cfg.get("format") or "{value}"),
                            "description": (
                                f"Display format for counter '{element.get('id', eid)}'. "
                                "Use {{value}} for the numeric counter."
                            ),
                        }
                    )
                elif text_mode == "data_display" and _expose_field(element, "text"):
                    cfg = (
                        element.get("data_display")
                        if isinstance(element.get("data_display"), dict)
                        else {}
                    )
                    fmt_id = counter_format_config_id(eid)
                    elements_out.append(
                        {
                            "type": "text",
                            "id": fmt_id,
                            "label": f"{element.get('id', eid)} format",
                            "value": str(cfg.get("format") or "{value}"),
                            "description": (
                                f"Display format for '{element.get('id', eid)}'. "
                                "Use {{value}} for the resolved data value."
                            ),
                        }
                    )
                elif text_mode == "static" and _expose_field(element, "text"):
                    text_var = element.get("text_var") or eid + "Text"
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
                src_mode = str(element.get("src_mode") or "static").strip().lower()
                if (
                    etype == "image"
                    and src_mode == "from_counter"
                    and _expose_field(element, "src")
                ):
                    cs = (
                        element.get("counter_src")
                        if isinstance(element.get("counter_src"), dict)
                        else {}
                    )
                    elements_out.append(
                        {
                            "type": "text",
                            "id": counter_image_default_src_id(eid),
                            "label": f"{element.get('id', eid)} default image",
                            "value": str(
                                cs.get("default_src")
                                or props.get("src")
                                or ""
                            ),
                            "description": (
                                f"Image URL when no counter range matches "
                                f"'{element.get('id', eid)}'."
                            ),
                        }
                    )
                    for ri, row in enumerate(cs.get("ranges") or []):
                        if not isinstance(row, dict):
                            continue
                        try:
                            lo = int(float(row.get("min", 0)))
                        except (TypeError, ValueError):
                            lo = 0
                        try:
                            hi = int(float(row.get("max", lo)))
                        except (TypeError, ValueError):
                            hi = lo
                        src_val = str(row.get("src") or "")
                        elements_out.append(
                            {
                                "type": "number",
                                "id": counter_image_range_min_id(eid, ri),
                                "label": (
                                    f"{element.get('id', eid)} range {ri + 1} min"
                                ),
                                "value": lo,
                                "min": -9999999999,
                                "max": 9999999999,
                                "description": "Counter value range minimum (inclusive).",
                            }
                        )
                        elements_out.append(
                            {
                                "type": "number",
                                "id": counter_image_range_max_id(eid, ri),
                                "label": (
                                    f"{element.get('id', eid)} range {ri + 1} max"
                                ),
                                "value": hi,
                                "min": -9999999999,
                                "max": 9999999999,
                                "description": "Counter value range maximum (inclusive).",
                            }
                        )
                        elements_out.append(
                            {
                                "type": "text",
                                "id": counter_image_range_src_id(eid, ri),
                                "label": (
                                    f"{element.get('id', eid)} range {ri + 1} image"
                                ),
                                "value": src_val,
                                "description": (
                                    f"Image URL when counter is between min and max "
                                    f"for '{element.get('id', eid)}'."
                                ),
                            }
                        )
                elif _expose_field(element, "src"):
                    src_var = element.get("src_var") or eid + "Src"
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

            if etype == "audio":
                try:
                    vol_a = float(props.get("volume", 1))
                    vol_a = max(0.0, min(1.0, vol_a))
                except (TypeError, ValueError):
                    vol_a = 1.0
                if _expose_field(element, "volume"):
                    elements_out.append(
                        {
                            "type": "number",
                            "id": f"{eid}_volume",
                            "label": f"{element.get('id', eid)} volume",
                            "value": vol_a,
                            "min": 0,
                            "max": 1,
                            "step": 0.05,
                            "description": (
                                f"Playback volume (0–1) for '{element.get('id', eid)}'."
                            ),
                        }
                    )
                for fade_key, fade_label in (
                    ("fade_in_ms", "fade-in (ms)"),
                    ("fade_out_ms", "fade-out (ms)"),
                ):
                    if not _expose_field(element, fade_key):
                        continue
                    try:
                        fval = int(float(props.get(fade_key, 0)))
                    except (TypeError, ValueError):
                        fval = 0
                    fval = max(0, fval)
                    elements_out.append(
                        {
                            "type": "number",
                            "id": f"{eid}_{fade_key}",
                            "label": f"{element.get('id', eid)} {fade_label}",
                            "value": fval,
                            "min": 0,
                            "max": 120000,
                            "description": (
                                f"{fade_label.title()} for '{element.get('id', eid)}' "
                                f"(0 = disabled)."
                            ),
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
                vk = str(props.get("visual_kind") or "video").strip().lower()
                if vk not in ("video", "image"):
                    vk = "video"
                if _expose_field(element, "visual_kind"):
                    elements_out.append(
                        {
                            "type": "text",
                            "id": f"{eid}_visual_kind",
                            "label": f"{element.get('id', eid)} visual kind",
                            "value": vk,
                            "description": (
                                f"visual_kind for '{element.get('id', eid)}': "
                                f"'video' or 'image' (GIF/still)."
                            ),
                        }
                    )
                audio_src_var_v = element.get("audio_src_var") or eid + "AudioSrc"
                if _expose_field(element, "audio_src"):
                    elements_out.append(
                        {
                            "type": "text",
                            "id": audio_src_var_v,
                            "label": f"{element.get('id', eid)} audio URL",
                            "value": str(props.get("audio_src", "")),
                            "description": (
                                f"Optional separate audio track for "
                                f"'{element.get('id', eid)}'."
                            ),
                        }
                    )
                try:
                    avol = float(props.get("audio_volume", 1))
                    avol = max(0.0, min(1.0, avol))
                except (TypeError, ValueError):
                    avol = 1.0
                if _expose_field(element, "audio_volume"):
                    elements_out.append(
                        {
                            "type": "number",
                            "id": f"{eid}_audio_volume",
                            "label": f"{element.get('id', eid)} audio volume",
                            "value": avol,
                            "min": 0,
                            "max": 1,
                            "step": 0.05,
                            "description": (
                                f"Nested audio volume for '{element.get('id', eid)}'."
                            ),
                        }
                    )
                for fade_key, vf_label in (
                    ("audio_fade_in_ms", "audio fade-in (ms)"),
                    ("audio_fade_out_ms", "audio fade-out (ms)"),
                ):
                    if not _expose_field(element, fade_key):
                        continue
                    try:
                        fval_v = int(float(props.get(fade_key, 0)))
                    except (TypeError, ValueError):
                        fval_v = 0
                    fval_v = max(0, fval_v)
                    elements_out.append(
                        {
                            "type": "number",
                            "id": f"{eid}_{fade_key}",
                            "label": f"{element.get('id', eid)} {vf_label}",
                            "value": fval_v,
                            "min": 0,
                            "max": 120000,
                            "description": (
                                f"{vf_label.title()} for nested audio "
                                f"(0 = disabled)."
                            ),
                        }
                    )

            for key in ("color", "background_color", "font_family"):
                if props.get(key) in (None, ""):
                    continue
                if not _expose_field(element, key):
                    continue
                export_val = str(props[key])
                if key == "font_family":
                    export_val = resolve_font_filename(export_val) or export_val
                elements_out.append(
                    {
                        "type": "color" if "color" in key else "text",
                        "id": f"{eid}_{key}",
                        "label": f"{element.get('id', eid)} {key.replace('_', ' ')}",
                        "value": export_val,
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
    from .preview_mocks import derive_preview_mocks

    base: Dict[str, Any] = {
        "template_name": template_name,
        "spore_studio": True,
        "alert_system": str(model.get("alert_system") or "queue"),
        "elements": elements_out,
        "streamdeck_options": sdo,
        "preview_mocks": derive_preview_mocks(model),
    }
    dc = model.get("dynamic_controls")
    if isinstance(dc, dict) and isinstance(dc.get("elements"), list) and dc["elements"]:
        base["dynamic_controls"] = _clone_dynamic_controls(dc)
    return base


def _clone_dynamic_controls(dc: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize dynamic_controls for public template JSON."""
    out: Dict[str, Any] = {"elements": []}
    for row in dc.get("elements") or []:
        if not isinstance(row, dict):
            continue
        cleaned = dict(row)
        ctype = str(cleaned.get("type") or "button").strip()
        cleaned["type"] = ctype
        if not cleaned.get("id"):
            continue
        if ctype == "counter_control":
            cleaned["action"] = str(cleaned.get("action") or "counter_adjust").strip()
            try:
                step = int(cleaned.get("step", 1))
            except (TypeError, ValueError):
                step = 1
            cleaned["step"] = max(1, step)
            for obsolete in ("operation", "button_text", "delta"):
                cleaned.pop(obsolete, None)
        elif not cleaned.get("action"):
            cleaned["action"] = _slugify_id(str(cleaned.get("id")))
        out["elements"].append(cleaned)
    return out


def _normalize_model_font_families(model: Dict[str, Any]) -> None:
    """Resolve font labels (e.g. Renogare) to filenames in element props."""
    for el in model.get("elements") or []:
        if not isinstance(el, dict):
            continue
        props = el.get("props")
        if not isinstance(props, dict):
            continue
        raw = str(props.get("font_family") or "").strip()
        if not raw:
            continue
        resolved = resolve_font_filename(raw)
        if resolved:
            props["font_family"] = resolved


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

    _normalize_model_font_families(model)
    elements = model.get("elements") or []
    font_css, font_map = _compile_spore_font_css(elements)
    dom_html = _render_dom_tree(elements, font_map=font_map)

    bindings = compile_bindings(elements)
    advanced_js = str(model.get("advanced_js") or "").rstrip()

    base_html = inject_data_runtime_block(base_html)

    out_html = _replace_block(base_html, _DOM_BEGIN, _DOM_END, dom_html)

    data_js = compile_spore_data_features(model)
    auto_parts = [bindings["js"]]
    if data_js.strip():
        auto_parts.append(data_js)
    auto_replacement = "\n\n".join(auto_parts)
    out_html = _replace_block(out_html, _AUTO_BEGIN, _AUTO_END, auto_replacement)

    if existing_html:
        existing_user = _extract_block(existing_html, _USER_BEGIN, _USER_END).strip()
        if existing_user and not advanced_js:
            advanced_js = existing_user
    if advanced_js:
        out_html = _replace_block(
            out_html, _USER_BEGIN, _USER_END, advanced_js
        )

    style_parts: List[str] = []
    if font_css.strip():
        style_parts.append(font_css.strip())
    if bindings["css"].strip():
        style_parts.append(bindings["css"].strip())
    if style_parts:
        styles = (
            '<style id="spore-studio-styles">\n'
            + "\n\n".join(style_parts)
            + "\n</style>"
        )
        out_html = _replace_block(out_html, _STYLES_BEGIN, _STYLES_END, styles)
    else:
        out_html = _replace_block(out_html, _STYLES_BEGIN, _STYLES_END, "")

    json_config = _derived_json_config(model)
    return out_html, json_config


def extract_user_js(html_text: str) -> str:
    """Public helper exposing the user-block extraction for round-tripping."""
    return _extract_block(html_text, _USER_BEGIN, _USER_END).strip()

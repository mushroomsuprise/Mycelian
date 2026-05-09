#!/usr/bin/env python3
"""
Best-effort reverse parser for legacy hand-authored Mycelian templates.

Scans ``templates/{name}.html`` for absolutely-positioned descendants
that carry an ``id`` attribute, resolves their CSS positioning against
the matching ``template_configs/{name}.json`` defaults (Jinja vars),
and emits synthetic editor elements with real ``position`` / ``size``
plus a ``legacy_bindings`` map back to the JSON-config field IDs that
drive each property. The Spore Studio editor renders these on the
canvas like normal elements; the save pipeline writes any user edits
back into the JSON config without touching the hand-authored HTML.

Templates without absolutely-positioned id'd descendants (chat,
activity_feed, ff7, ...) return ``None`` so the caller can fall back
to the legacy outline-only behaviour in
:func:`template_parser_back._synthesize_legacy_elements`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Set, Tuple

from ..path_utils import get_template_path

logger = logging.getLogger(__name__)


# Regex for a Jinja substitution: ``{{ Var }}`` or ``{{Var|filter('arg')}}``.
# We only care about the leading identifier inside the braces.
_JINJA_VAR_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*\|[^}]+)?\s*\}\}"
)

# Pixel value matcher for ``123px`` / ``-5.5px`` / bare numbers.
_PIXEL_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*px$")

# Tag → editor element type. Anything else falls through to text/container
# heuristics in :func:`_classify_element`.
_TAG_TO_TYPE = {
    "img": "image",
    "video": "video",
    "audio": "audio",
}


# ---------------------------------------------------------------------------
# CSS extraction helpers
# ---------------------------------------------------------------------------


def _strip_comments(css: str) -> str:
    """Drop ``/* ... */`` comments. Keeps newlines for source-stable parsing."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _split_top_rules(css: str) -> List[Tuple[str, str]]:
    """
    Yield ``(selector_text, declaration_text)`` for each top-level CSS
    rule. At-rules (``@media``, ``@keyframes``, ``@font-face`` ...) are
    skipped wholesale — we don't want to drag responsive overrides or
    keyframe steps into the visual reverse parse.
    """
    rules: List[Tuple[str, str]] = []
    i = 0
    n = len(css)
    while i < n:
        c = css[i]
        if c.isspace():
            i += 1
            continue
        if c == "@":
            depth = 0
            saw_brace = False
            while i < n:
                ch = css[i]
                if ch == "{":
                    depth += 1
                    saw_brace = True
                elif ch == "}":
                    depth -= 1
                    if saw_brace and depth == 0:
                        i += 1
                        break
                elif ch == ";" and not saw_brace:
                    i += 1
                    break
                i += 1
            continue
        sel_start = i
        while i < n and css[i] != "{":
            i += 1
        if i >= n:
            break
        selector = css[sel_start:i].strip()
        i += 1
        depth = 1
        decl_start = i
        while i < n and depth > 0:
            ch = css[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        declarations = css[decl_start:i].strip()
        if i < n:
            i += 1
        if selector and declarations:
            rules.append((selector, declarations))
    return rules


def _parse_declarations(text: str) -> List[Tuple[str, str]]:
    """Split ``a:b; c:d`` into ``[('a','b'), ('c','d')]``."""
    out: List[Tuple[str, str]] = []
    for raw in text.split(";"):
        decl = raw.strip()
        if not decl or ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop and value:
            out.append((prop, value))
    return out


# Match a single simple selector atom (id, class, or tag) at the rightmost
# end of a compound selector chain.
_SIMPLE_ATOM_RE = re.compile(r"([#.]?[A-Za-z_][\w\-]*)")


def _selector_atoms(
    selector: str,
) -> Tuple[Optional[str], Set[str], Optional[str]]:
    """
    Return ``(id, class_set, tag)`` for the *rightmost* compound
    selector inside ``selector``. Combinators (`` ``, ``>``, ``+``,
    ``~``) collapse to just the rightmost atom for matching, which is
    a deliberate simplification — we only need to find which leaf
    element a rule targets, not enforce full CSS3 specificity.
    """
    selector = re.sub(
        r"::?[A-Za-z_][\w\-]*(?:\([^)]*\))?", "", selector
    )
    selector = re.sub(r"\[[^\]]*\]", "", selector)
    parts = re.split(r"\s*[>+~]\s*|\s+", selector.strip())
    if not parts:
        return (None, set(), None)
    last = parts[-1]
    eid: Optional[str] = None
    cls: Set[str] = set()
    tag: Optional[str] = None
    for atom in _SIMPLE_ATOM_RE.findall(last):
        if atom.startswith("#"):
            eid = atom[1:]
        elif atom.startswith("."):
            cls.add(atom[1:])
        else:
            tag = atom.lower()
    return (eid, cls, tag)


# ---------------------------------------------------------------------------
# JSON-config helpers
# ---------------------------------------------------------------------------


def _build_vars_map(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``{field_id: default_value}`` for every id'd, non-separator field."""
    out: Dict[str, Any] = {}
    elements = config.get("elements")
    if not isinstance(elements, list):
        return out
    for entry in elements:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").lower() == "separator":
            continue
        eid = entry.get("id")
        if eid:
            out[str(eid)] = entry.get("value")
    return out


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Resolved-declaration record + matching
# ---------------------------------------------------------------------------


class _Decl:
    """A single CSS declaration after Jinja substitution."""

    __slots__ = ("prop", "value", "var_id")

    def __init__(self, prop: str, value: str, var_id: Optional[str]):
        self.prop = prop
        self.value = value
        self.var_id = var_id


def _resolve_decl(
    raw_value: str, vars_map: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Substitute Jinja vars in ``raw_value`` against ``vars_map``.

    Returns ``(resolved_value, first_var_id)``. ``first_var_id`` is the
    leading Jinja identifier — used to bind the declaration back to a
    JSON-config field on save. A ``None`` value means a referenced
    variable had no resolvable default; in that case the caller should
    drop the declaration because rendering would be lossy.
    """
    matches = list(_JINJA_VAR_RE.finditer(raw_value))
    if not matches:
        return raw_value, None
    var_id = matches[0].group(1)
    resolved = raw_value
    for m in matches:
        name = m.group(1)
        if name not in vars_map or vars_map[name] in (None, ""):
            return None, var_id
        resolved = resolved.replace(m.group(0), str(vars_map[name]))
    return resolved.strip(), var_id


def _select_rules_for(
    rules: List[Tuple[str, List[_Decl]]],
    eid: Optional[str],
    classes: Set[str],
    tag: Optional[str],
) -> List[List[_Decl]]:
    """Return every rule whose rightmost atom matches the element."""
    matched: List[List[_Decl]] = []
    for selector_text, decls in rules:
        for sub in selector_text.split(","):
            sel_id, sel_cls, sel_tag = _selector_atoms(sub)
            if sel_id is None and not sel_cls and not sel_tag:
                continue
            if sel_id and sel_id != eid:
                continue
            if sel_cls and not sel_cls.issubset(classes):
                continue
            if sel_tag and tag and sel_tag != tag:
                continue
            matched.append(decls)
            break
    return matched


def _decls_to_dict(decl_lists: List[List[_Decl]]) -> Dict[str, _Decl]:
    """Flatten cascading rule lists into a final ``{prop: _Decl}`` dict."""
    out: Dict[str, _Decl] = {}
    for decls in decl_lists:
        for d in decls:
            out[d.prop] = d
    return out


def _decl_pixel_value(decl: Optional[_Decl]) -> Optional[int]:
    if decl is None or decl.value is None:
        return None
    text = decl.value.strip()
    m = _PIXEL_RE.match(text)
    if m:
        try:
            return int(round(float(m.group(1))))
        except (TypeError, ValueError):
            return None
    try:
        return int(round(float(text)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Body walker — captures ``id``, classes, tag, text-presence per element.
# ---------------------------------------------------------------------------


class _BodyParser(HTMLParser):
    """
    Collect ``(id, classes, tag, has_text)`` for every tag with an
    ``id`` attribute inside ``<body>``. Script and style tags are
    skipped so embedded JS/CSS literals don't pollute the element set.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.found: List[Dict[str, Any]] = []
        self._open: List[Dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag_l = tag.lower()
        if tag_l == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        rec: Optional[Dict[str, Any]] = None
        eid = (attrs_d.get("id") or "").strip()
        if eid:
            rec = {
                "id": eid,
                "classes": set(attrs_d.get("class", "").split()),
                "tag": tag_l,
                "has_text": False,
                "style_attr": (attrs_d.get("style") or "").strip(),
            }
            self.found.append(rec)
        self._open.append({"tag": tag_l, "rec": rec})

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l == "body":
            self.in_body = False
            return
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i]["tag"] == tag_l:
                del self._open[i:]
                break

    def handle_data(self, data: str) -> None:
        if not self.in_body or not self._open:
            return
        top = self._open[-1]
        if top.get("tag") in ("script", "style"):
            return
        if not data or not data.strip():
            return
        rec = top.get("rec")
        if rec is not None:
            rec["has_text"] = True


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _read_template_html(template_name: str) -> Optional[str]:
    path = get_template_path(f"{template_name}.html")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        logger.warning("Reverse parser cannot read %s: %s", path, e)
        return None


def _read_template_json(template_name: str) -> Optional[Dict[str, Any]]:
    # Late import: ``template_config_parser`` belongs to a higher layer
    # and a top-level import would create a circular dependency at
    # ``modules/spore_studio/__init__.py`` import time.
    from ..template_config_parser import TemplateConfigParser

    parser = TemplateConfigParser()
    config_path = parser.get_config_path(template_name)
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        logger.warning("Reverse parser cannot read %s: %s", config_path, e)
        return None


def _extract_style_blocks(html: str) -> str:
    """Concatenate every ``<style>...</style>`` body in source order."""
    chunks: List[str] = []
    for m in re.finditer(
        r"<style[^>]*>([\s\S]*?)</style>", html, flags=re.IGNORECASE,
    ):
        chunks.append(m.group(1))
    return "\n".join(chunks)


def _resolve_rules(
    css: str, vars_map: Dict[str, Any]
) -> List[Tuple[str, List[_Decl]]]:
    """Build ``(selector_text, [_Decl])`` for every top-level rule in ``css``."""
    out: List[Tuple[str, List[_Decl]]] = []
    for selector, decl_text in _split_top_rules(_strip_comments(css)):
        decls: List[_Decl] = []
        for prop, raw_value in _parse_declarations(decl_text):
            resolved, var_id = _resolve_decl(raw_value, vars_map)
            if resolved is None:
                continue
            decls.append(_Decl(prop, resolved, var_id))
        if decls:
            out.append((selector, decls))
    return out


def _is_positioned(decls: Dict[str, _Decl]) -> bool:
    pos = decls.get("position")
    if not pos:
        return False
    return pos.value.strip().lower() in ("absolute", "fixed")


def _decls_to_inline(style_attr: str) -> Dict[str, _Decl]:
    """Parse an inline ``style="..."`` attribute (no Jinja substitution)."""
    out: Dict[str, _Decl] = {}
    for prop, value in _parse_declarations(style_attr):
        out[prop] = _Decl(prop, value, None)
    return out


def _design_size(config: Dict[str, Any]) -> Tuple[int, int]:
    """Best-effort design canvas size from JSON-config keys."""
    vars_map = _build_vars_map(config)
    width = (
        _coerce_int(vars_map.get("DesignWidth"))
        or _coerce_int(vars_map.get("Width"))
        or _coerce_int(vars_map.get("StreamWidth"))
        or 1920
    )
    height = (
        _coerce_int(vars_map.get("DesignHeight"))
        or _coerce_int(vars_map.get("Height"))
        or _coerce_int(vars_map.get("StreamHeight"))
        or 1080
    )
    return width, height


def _classify_element(
    rec: Dict[str, Any], decls: Dict[str, _Decl]
) -> str:
    """text / image / video / audio / container."""
    tag = rec.get("tag")
    if tag in _TAG_TO_TYPE:
        return _TAG_TO_TYPE[tag]
    if rec.get("has_text") and "font-size" in decls:
        return "text"
    return "container"


def _build_element(
    rec: Dict[str, Any],
    decls: Dict[str, _Decl],
    design_w: int,
    design_h: int,
) -> Optional[Dict[str, Any]]:
    """Translate a positioned id'd DOM record into a synthetic editor element."""
    width = _decl_pixel_value(decls.get("width"))
    height = _decl_pixel_value(decls.get("height"))
    top = _decl_pixel_value(decls.get("top"))
    left = _decl_pixel_value(decls.get("left"))
    bottom = _decl_pixel_value(decls.get("bottom"))
    right = _decl_pixel_value(decls.get("right"))

    if all(
        v is None
        for v in (width, height, top, left, bottom, right)
    ):
        return None

    # Fallbacks so the canvas at least shows a draggable rectangle when
    # a dimension is missing (commonly because the legacy template uses
    # 100% / inheritance for that axis).
    width = width if width is not None else 200
    height = height if height is not None else 80

    if left is not None:
        x = left
    elif right is not None:
        x = max(0, design_w - right - width)
    else:
        x = 0
    if top is not None:
        y = top
    elif bottom is not None:
        y = max(0, design_h - bottom - height)
    else:
        y = 0

    bindings: Dict[str, str] = {}
    if left is not None and decls["left"].var_id:
        bindings["position.x"] = decls["left"].var_id
    elif right is not None and decls.get("right") and decls["right"].var_id:
        bindings["position.x"] = decls["right"].var_id
    if top is not None and decls["top"].var_id:
        bindings["position.y"] = decls["top"].var_id
    elif bottom is not None and decls.get("bottom") and decls["bottom"].var_id:
        bindings["position.y"] = decls["bottom"].var_id
    if "width" in decls and decls["width"].var_id:
        bindings["size.w"] = decls["width"].var_id
    if "height" in decls and decls["height"].var_id:
        bindings["size.h"] = decls["height"].var_id

    anchor = {
        "x": "right" if (left is None and right is not None) else "left",
        "y": "bottom" if (top is None and bottom is not None) else "top",
    }

    props: Dict[str, Any] = {}
    if "color" in decls and decls["color"].value:
        props["color"] = decls["color"].value
        if decls["color"].var_id:
            bindings["props.color"] = decls["color"].var_id
    bg = decls.get("background-color") or decls.get("background")
    if bg and bg.value:
        props["background_color"] = bg.value
        if bg.var_id:
            bindings["props.background_color"] = bg.var_id
    fs = _decl_pixel_value(decls.get("font-size"))
    if fs is not None:
        props["font_size"] = fs
        if "font-size" in decls and decls["font-size"].var_id:
            bindings["props.font_size"] = decls["font-size"].var_id
    br = _decl_pixel_value(decls.get("border-radius"))
    if br is not None:
        props["border_radius"] = br
        if (
            "border-radius" in decls
            and decls["border-radius"].var_id
        ):
            bindings["props.border_radius"] = decls["border-radius"].var_id

    element: Dict[str, Any] = {
        "id": rec["id"],
        "type": _classify_element(rec, decls),
        "category": "Layout",
        "position": {"x": int(x), "y": int(y)},
        "size": {"w": int(width), "h": int(height)},
        "props": props,
        "bindings": [],
        "legacy_bindings": bindings,
        "legacy_anchor": anchor,
    }
    return element


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reverse_parse_legacy(
    template_name: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    Build a list of synthetic editor elements with real position / size.

    Returns ``None`` when the template isn't amenable to reverse
    parsing (no positioned id'd elements found). In that case callers
    should fall back to
    :func:`template_parser_back._synthesize_legacy_elements`.
    """
    html_text = _read_template_html(template_name)
    if not html_text:
        return None

    config = _read_template_json(template_name) or {}
    vars_map = _build_vars_map(config)

    css = _extract_style_blocks(html_text)
    rules = _resolve_rules(css, vars_map)

    body_parser = _BodyParser()
    try:
        body_parser.feed(html_text)
    except Exception as e:  # pragma: no cover - HTMLParser is permissive
        logger.warning(
            "Reverse parser HTML parse failed for %s: %s",
            template_name, e,
        )
        return None

    if not body_parser.found:
        return None

    design_w, design_h = _design_size(config)

    elements: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for rec in body_parser.found:
        eid = rec["id"]
        if not eid or eid in seen_ids:
            continue
        matched = _select_rules_for(
            rules, eid, rec["classes"], rec["tag"]
        )
        decls = _decls_to_dict(matched)
        # Inline style attributes win over class/id rules but offer no
        # Jinja binding (no var_id) — same as a literal CSS author.
        decls.update(_decls_to_inline(rec.get("style_attr") or ""))
        if not _is_positioned(decls):
            continue
        element = _build_element(rec, decls, design_w, design_h)
        if element is None:
            continue
        elements.append(element)
        seen_ids.add(eid)

    if not elements:
        return None
    return elements


def design_size_from_config(template_name: str) -> Tuple[int, int]:
    """Helper exposing the resolved design canvas size to callers."""
    config = _read_template_json(template_name) or {}
    return _design_size(config)

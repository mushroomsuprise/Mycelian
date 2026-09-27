#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Overlay containers: several templates on one browser-source route.

A container does not merge HTML. The shell page loads each member template
in its own iframe. Locked (priority) slots keep a designated maximum size;
measured unused space inside that box is given to flexible slots.

The solver is pure. ``container_layout.js`` mirrors ``solve_layout`` so the
editor and the live page stay in step. Keep the two implementations aligned.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ..path_utils import get_template_path

logger = logging.getLogger(__name__)

CONTAINER_VERSION = 1

_RESERVED_ROUTES = frozenset(
    {
        "static",
        "assets",
        "api",
        "debug",
        "_spore_studio_editor",
        "socket.io",
    }
)

_ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_MIN_CANVAS = 50
_MAX_CANVAS_W = 7680
_MAX_CANVAS_H = 4320
_MAX_SPACING = 200
_MAX_PADDING = 400
_MAX_NAME = 80


class ContainerError(Exception):
    """User-facing container create / save / load failure."""


def containers_dir() -> str:
    return os.path.join(get_template_path(), "_containers")


def container_path(route: str) -> str:
    return os.path.join(containers_dir(), f"{route}.json")


def template_html_path(stem: str) -> str:
    return os.path.join(get_template_path(), f"{stem}.html")


def template_html_exists(stem: str) -> bool:
    if not stem or "/" in stem or "\\" in stem or ".." in stem:
        return False
    return os.path.isfile(template_html_path(stem))


def suggest_route(name: str) -> str:
    """Turn a display name into a URL segment."""
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_-]", "", text)
    text = text.strip("_-")
    if not text or text in _RESERVED_ROUTES:
        text = "container"
    return text[:48]


def validate_route_syntax(route: str) -> str:
    if not isinstance(route, str):
        raise ContainerError("Route must be a string.")
    cleaned = route.strip()
    if not cleaned:
        raise ContainerError("Route cannot be empty.")
    if not _ROUTE_RE.match(cleaned):
        raise ContainerError(
            "Route may only contain letters, numbers, '_' and '-', "
            "and cannot start with '_' or '-'."
        )
    if cleaned.lower() in _RESERVED_ROUTES:
        raise ContainerError(f"'{cleaned}' is reserved.")
    return cleaned


def container_route_exists(route: str) -> bool:
    try:
        cleaned = validate_route_syntax(route)
    except ContainerError:
        return False
    return os.path.isfile(container_path(cleaned))


def _atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, path)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ContainerError("Expected a boolean.")


def _as_int(value: Any, default: int, lo: int, hi: int, label: str) -> int:
    if value is None:
        number = default
    else:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError) as exc:
            raise ContainerError(f"{label} must be a number.") from exc
    if number < lo or number > hi:
        raise ContainerError(f"{label} must be between {lo} and {hi}.")
    return number


def _optional_int(value: Any, lo: int, hi: int, label: str) -> Optional[int]:
    if value is None or value == "":
        return None
    return _as_int(value, 0, lo, hi, label)


def _padding(raw: Any) -> Dict[str, int]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ContainerError("Padding must be an object.")
    return {
        "t": _as_int(raw.get("t"), 0, 0, _MAX_PADDING, "Padding top"),
        "r": _as_int(raw.get("r"), 0, 0, _MAX_PADDING, "Padding right"),
        "b": _as_int(raw.get("b"), 0, 0, _MAX_PADDING, "Padding bottom"),
        "l": _as_int(raw.get("l"), 0, 0, _MAX_PADDING, "Padding left"),
    }


def _normalize_link(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ContainerError("Size link must be an object.")
    link_id = str(raw.get("id") or "").strip()
    if not link_id or "/" in link_id or len(link_id) > 64:
        raise ContainerError("Size link id is invalid.")
    axis = raw.get("axis") or "vertical"
    if axis not in ("vertical", "horizontal"):
        raise ContainerError("Size link axis must be vertical or horizontal.")
    role = raw.get("role") or "flexible"
    if role not in ("locked", "flexible"):
        raise ContainerError("Size link role must be locked or flexible.")
    return {
        "id": link_id,
        "axis": axis,
        "role": role,
        "min_px": _as_int(raw.get("min_px"), 0, 0, _MAX_CANVAS_W, "Minimum px"),
        "min_pct": _as_int(raw.get("min_pct"), 0, 0, 100, "Minimum percent"),
        "max_px": _optional_int(raw.get("max_px"), 0, _MAX_CANVAS_W, "Maximum px"),
        "max_pct": _optional_int(raw.get("max_pct"), 0, 100, "Maximum percent"),
    }


def _normalize_slot(raw: Any, index: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContainerError("Each template slot must be an object.")
    slot_id = str(raw.get("id") or f"slot{index + 1}").strip()
    if not slot_id or "/" in slot_id or "\\" in slot_id or len(slot_id) > 64:
        raise ContainerError("Slot id is invalid.")
    template = str(raw.get("template") or "").strip()
    if not template or "/" in template or "\\" in template or ".." in template:
        raise ContainerError("Slot template name is invalid.")
    fit = raw.get("fit") or "native"
    if fit not in ("native", "scale"):
        raise ContainerError("Fit must be native or scale.")
    audio = raw.get("audio") if isinstance(raw.get("audio"), dict) else {}
    design_w = raw.get("design_width")
    design_h = raw.get("design_height")
    return {
        "id": slot_id,
        "template": template,
        "x": _as_int(raw.get("x"), 0, -_MAX_CANVAS_W, _MAX_CANVAS_W * 2, "X"),
        "y": _as_int(raw.get("y"), 0, -_MAX_CANVAS_H, _MAX_CANVAS_H * 2, "Y"),
        "w": _as_int(raw.get("w"), 640, 1, _MAX_CANVAS_W, "Width"),
        "h": _as_int(raw.get("h"), 360, 1, _MAX_CANVAS_H, "Height"),
        "z": _as_int(raw.get("z"), index + 1, 0, 999, "Z-order"),
        "opacity": _as_int(raw.get("opacity"), 100, 0, 100, "Opacity"),
        "visible": _as_bool(raw.get("visible"), True),
        "locked_position": _as_bool(raw.get("locked_position"), False),
        "pointer_events": _as_bool(raw.get("pointer_events"), True),
        "padding": _padding(raw.get("padding")),
        "fit": fit,
        "design_width": _optional_int(
            design_w, 1, _MAX_CANVAS_W, "Design width"
        ),
        "design_height": _optional_int(
            design_h, 1, _MAX_CANVAS_H, "Design height"
        ),
        "audio": {
            "muted": bool(audio.get("muted", False)),
            "volume": _as_int(audio.get("volume"), 100, 0, 100, "Volume"),
        },
        "link": _normalize_link(raw.get("link")),
    }


def normalize_container(raw: Any) -> Dict[str, Any]:
    """Return a container dict with defaults filled in.

    Raises ContainerError when the shape is invalid. Does not check whether
    the route collides with a file on disk.
    """
    if not isinstance(raw, dict):
        raise ContainerError("Container must be a JSON object.")
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > _MAX_NAME:
        raise ContainerError(f"Name must be 1–{_MAX_NAME} characters.")
    route = validate_route_syntax(str(raw.get("route") or ""))
    background = raw.get("background") or "transparent"
    if not isinstance(background, str):
        raise ContainerError("Background must be a string.")
    background = background.strip()
    if background.lower() == "transparent":
        background = "transparent"
    elif not _HEX_COLOR_RE.match(background):
        raise ContainerError("Background must be transparent or a hex color.")
    slots_raw = raw.get("slots") if raw.get("slots") is not None else []
    if not isinstance(slots_raw, list):
        raise ContainerError("Slots must be a list.")
    slots = [_normalize_slot(item, i) for i, item in enumerate(slots_raw)]
    ids = [slot["id"] for slot in slots]
    if len(ids) != len(set(ids)):
        raise ContainerError("Slot ids must be unique.")
    axes: Dict[str, str] = {}
    for slot in slots:
        link = slot.get("link")
        if not link:
            continue
        prev = axes.get(link["id"])
        if prev and prev != link["axis"]:
            raise ContainerError(
                f"Size link '{link['id']}' mixes vertical and horizontal."
            )
        axes[link["id"]] = link["axis"]
    return {
        "version": CONTAINER_VERSION,
        "name": name,
        "route": route,
        "width": _as_int(raw.get("width"), 1920, _MIN_CANVAS, _MAX_CANVAS_W, "Width"),
        "height": _as_int(
            raw.get("height"), 1080, _MIN_CANVAS, _MAX_CANVAS_H, "Height"
        ),
        "background": background,
        "spacing": _as_int(raw.get("spacing"), 0, 0, _MAX_SPACING, "Spacing"),
        "snap": _as_int(raw.get("snap"), 10, 0, 64, "Snap"),
        "enabled": _as_bool(raw.get("enabled"), True),
        "slots": slots,
    }


def load_container(route: str) -> Optional[Dict[str, Any]]:
    try:
        cleaned = validate_route_syntax(route)
    except ContainerError:
        return None
    path = container_path(cleaned)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read container %s: %s", path, exc)
        return None
    try:
        return normalize_container(raw)
    except ContainerError as exc:
        logger.warning("Container %s is invalid: %s", path, exc)
        return None


def list_container_models() -> List[Dict[str, Any]]:
    folder = containers_dir()
    if not os.path.isdir(folder):
        return []
    models: List[Dict[str, Any]] = []
    for entry in sorted(os.listdir(folder)):
        if not entry.endswith(".json"):
            continue
        model = load_container(entry[:-5])
        if model is not None:
            models.append(model)
    models.sort(key=lambda item: item["name"].lower())
    return models


def list_enabled_routes() -> List[str]:
    return [model["route"] for model in list_container_models() if model.get("enabled")]


def container_source_record(model: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """About-page card for one enabled container."""
    templates = [slot["template"] for slot in model.get("slots") or []]
    count = len(templates)
    noun = "template" if count == 1 else "templates"
    listed = ", ".join(templates) if templates else "none"
    base = (base_url or "").rstrip("/")
    return {
        "name": model["route"],
        "title": model["name"],
        "url": f"{base}/{model['route']}",
        "type": "container",
        "description": f"Container combining {count} {noun}: {listed}",
        "template_count": count,
        "templates": templates,
    }


def container_summary(model: Dict[str, Any]) -> Dict[str, Any]:
    templates = [slot["template"] for slot in model.get("slots") or []]
    return {
        "name": model["name"],
        "route": model["route"],
        "enabled": bool(model.get("enabled")),
        "width": model["width"],
        "height": model["height"],
        "template_count": len(templates),
        "templates": templates,
    }


def list_containers() -> List[Dict[str, Any]]:
    return [container_summary(model) for model in list_container_models()]


def _assert_route_free(route: str) -> None:
    if template_html_exists(route):
        raise ContainerError(f"'{route}' is already a template.")
    if container_route_exists(route):
        raise ContainerError(f"'{route}' is already a container.")


def save_container(
    raw: Dict[str, Any], *, previous_route: Optional[str] = None
) -> Dict[str, Any]:
    """Write a container. ``previous_route`` renames when it differs."""
    model = normalize_container(raw)
    route = model["route"]
    prev = (previous_route or "").strip() or None
    if prev:
        try:
            prev = validate_route_syntax(prev)
        except ContainerError as exc:
            raise ContainerError(f"Previous route: {exc}") from exc
    if template_html_exists(route):
        raise ContainerError(f"'{route}' is already a template.")
    if container_route_exists(route) and prev != route:
        raise ContainerError(f"'{route}' is already a container.")
    for slot in model["slots"]:
        if container_route_exists(slot["template"]):
            raise ContainerError(
                f"'{slot['template']}' is a container and cannot be nested."
            )
    _atomic_write_json(container_path(route), model)
    if prev and prev != route:
        old_path = container_path(prev)
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError as exc:
                logger.warning("Could not remove old container %s: %s", old_path, exc)
    return model


def create_container(
    name: str,
    route: Optional[str] = None,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Dict[str, Any]:
    chosen = validate_route_syntax(route or suggest_route(name))
    _assert_route_free(chosen)
    model = normalize_container(
        {
            "name": name,
            "route": chosen,
            "width": width,
            "height": height,
            "background": "transparent",
            "spacing": 0,
            "snap": 10,
            "enabled": True,
            "slots": [],
        }
    )
    return save_container(model)


def duplicate_container(route: str, name: str, new_route: str) -> Dict[str, Any]:
    source = load_container(route)
    if source is None:
        raise ContainerError(f"Container '{route}' was not found.")
    chosen = validate_route_syntax(new_route)
    _assert_route_free(chosen)
    source["name"] = name
    source["route"] = chosen
    return save_container(source)


def delete_container(route: str) -> None:
    cleaned = validate_route_syntax(route)
    path = container_path(cleaned)
    if not os.path.isfile(path):
        raise ContainerError(f"Container '{cleaned}' was not found.")
    try:
        os.remove(path)
    except OSError as exc:
        raise ContainerError(f"Could not delete container: {exc}") from exc


def list_member_templates() -> Dict[str, List[str]]:
    """Every HTML template a container may embed, including protected ones."""
    from .save_pipeline import (
        SPORE_STUDIO_PROTECTED_TEMPLATES,
        list_spore_templates,
    )

    spore, legacy = list_spore_templates()
    builtin: List[str] = []
    folder = get_template_path()
    if os.path.isdir(folder):
        for entry in sorted(os.listdir(folder)):
            if not entry.endswith(".html"):
                continue
            stem = entry[:-5]
            if stem.startswith("_"):
                continue
            if stem in SPORE_STUDIO_PROTECTED_TEMPLATES:
                builtin.append(stem)
    return {
        "spore": spore,
        "legacy": legacy,
        "builtin": builtin,
    }


def _eff_min(link: Dict[str, Any], span: int) -> int:
    pct = int(round(span * (link.get("min_pct") or 0) / 100.0))
    return max(int(link.get("min_px") or 0), pct)


def _eff_max(link: Dict[str, Any], span: int) -> Optional[int]:
    caps: List[int] = []
    if link.get("max_px") is not None:
        caps.append(int(link["max_px"]))
    if link.get("max_pct") is not None:
        caps.append(int(round(span * link["max_pct"] / 100.0)))
    if not caps:
        return None
    return min(caps)


def _axis_pads(slot: Dict[str, Any], axis: str) -> Tuple[int, int]:
    pad = slot["padding"]
    if axis == "vertical":
        return int(pad["t"]), int(pad["b"])
    return int(pad["l"]), int(pad["r"])


def _designated(slot: Dict[str, Any], axis: str) -> int:
    return int(slot["h"] if axis == "vertical" else slot["w"])


def _measured_content(
    slot: Dict[str, Any], axis: str, used: Optional[Dict[str, Any]]
) -> Optional[float]:
    """Content size along ``axis``, or None when the slot fills its box."""
    if slot.get("fit") == "scale":
        return None
    if not used:
        return None
    entry = used.get(slot["id"])
    if not isinstance(entry, dict):
        return None
    if entry.get("explicit"):
        key = "h" if axis == "vertical" else "w"
        try:
            return float(entry[key])
        except (KeyError, TypeError, ValueError):
            return None
    # Only locked native slots report a scanned used size. Missing means fill.
    key = "h" if axis == "vertical" else "w"
    if key not in entry or entry[key] is None:
        return None
    try:
        return float(entry[key])
    except (TypeError, ValueError):
        return None


def _outer_from_content(slot: Dict[str, Any], axis: str, content: float) -> int:
    before, after = _axis_pads(slot, axis)
    return int(round(content)) + before + after


def _locked_outer(
    slot: Dict[str, Any],
    axis: str,
    span: int,
    used: Optional[Dict[str, Any]],
    warnings: List[Dict[str, str]],
) -> int:
    link = slot["link"]
    designated = _designated(slot, axis)
    before, after = _axis_pads(slot, axis)
    content_cap = max(0, designated - before - after)
    measured = _measured_content(slot, axis, used)
    if measured is None:
        content = content_cap
    else:
        content = min(content_cap, max(0.0, measured))
    outer = _outer_from_content(slot, axis, content)
    lo = _eff_min(link, span)
    hi = designated
    cap = _eff_max(link, span)
    if cap is not None:
        hi = min(hi, cap)
    if lo > hi:
        warnings.append(
            {
                "code": "min_above_designated",
                "link": link["id"],
                "message": (
                    "A locked template's minimum is larger than its designated "
                    "size. The designated size is kept."
                ),
            }
        )
        return hi
    return max(lo, min(hi, outer))


def _split_int(total: int, weights: List[Tuple[str, int]]) -> Dict[str, int]:
    if not weights:
        return {}
    if total <= 0:
        return {key: 0 for key, _weight in weights}
    weight_sum = sum(max(1, weight) for _key, weight in weights)
    floors: List[Tuple[str, int]] = []
    remainders: List[Tuple[float, str]] = []
    used = 0
    for key, weight in weights:
        exact = total * max(1, weight) / weight_sum
        base = int(exact)
        floors.append((key, base))
        remainders.append((exact - base, key))
        used += base
    leftover = total - used
    remainders.sort(key=lambda item: (-item[0], item[1]))
    out = {key: base for key, base in floors}
    for index in range(leftover):
        out[remainders[index % len(remainders)][1]] += 1
    return out


def _distribute_flexible(
    remaining: int,
    items: List[Dict[str, Any]],
) -> Tuple[Dict[str, int], bool]:
    """Split ``remaining`` px across flexible slots. Returns sizes, overflow."""
    if not items:
        return {}, False
    mins = {item["id"]: max(0, int(item["min"])) for item in items}
    if sum(mins.values()) > remaining:
        return mins, True
    maxes = {
        item["id"]: (None if item["max"] is None else int(item["max"]))
        for item in items
    }
    weights = {item["id"]: max(1, int(item["weight"])) for item in items}
    assigned: Dict[str, int] = {}
    active = [item["id"] for item in items]
    budget = remaining
    for _guard in range(24):
        if not active:
            break
        shares = _split_int(budget, [(slot_id, weights[slot_id]) for slot_id in active])
        frozen: List[str] = []
        still: List[str] = []
        for slot_id in active:
            share = shares[slot_id]
            lo = mins[slot_id]
            hi = maxes[slot_id]
            if share < lo:
                assigned[slot_id] = lo
                frozen.append(slot_id)
            elif hi is not None and share > hi:
                assigned[slot_id] = hi
                frozen.append(slot_id)
            else:
                still.append(slot_id)
        if not frozen:
            assigned.update(shares)
            break
        for slot_id in frozen:
            budget -= assigned[slot_id]
        active = still
        if budget < 0:
            return assigned, True
    return assigned, False


def _content_box(slot: Dict[str, Any], outer_w: int, outer_h: int) -> Dict[str, int]:
    pad = slot["padding"]
    width = max(0, outer_w - int(pad["l"]) - int(pad["r"]))
    height = max(0, outer_h - int(pad["t"]) - int(pad["b"]))
    return {
        "x": int(pad["l"]),
        "y": int(pad["t"]),
        "w": width,
        "h": height,
    }


def _iframe_box(
    slot: Dict[str, Any],
    axis: Optional[str],
    content: Dict[str, int],
) -> Dict[str, int]:
    """Iframe viewport inside the slot.

    Locked native iframes stay at the designated content size on the link
    axis so percentage layouts do not collapse while the slot clips to the
    measured content.
    """
    pad = slot["padding"]
    role = (slot.get("link") or {}).get("role")
    fit = slot.get("fit") or "native"
    if fit == "scale":
        design_w = int(slot.get("design_width") or slot["w"])
        design_h = int(slot.get("design_height") or slot["h"])
        return {
            "w": max(1, design_w),
            "h": max(1, design_h),
            "left": int(pad["l"]),
            "top": int(pad["t"]),
        }
    iframe_w = content["w"]
    iframe_h = content["h"]
    if role == "locked" and axis and fit == "native":
        before_v, after_v = int(pad["t"]), int(pad["b"])
        before_h, after_h = int(pad["l"]), int(pad["r"])
        if axis == "vertical":
            iframe_h = max(0, int(slot["h"]) - before_v - after_v)
        else:
            iframe_w = max(0, int(slot["w"]) - before_h - after_h)
    return {
        "w": iframe_w,
        "h": iframe_h,
        "left": content["x"],
        "top": content["y"],
    }


def solve_layout(
    container: Dict[str, Any], used: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Resolve slot rectangles.

    ``used`` maps slot id to ``{"w", "h"}`` content pixels measured inside
    the locked iframe. Omit a slot (or pass None) when it fills its
    designated box. Padding is inside each slot. ``spacing`` is the gap
    between linked slot rectangles and is removed from the span before
    flexible slots are sized, along with each locked slot's padding.
    """
    model = normalize_container(container)
    warnings: List[Dict[str, str]] = []
    spacing = int(model["spacing"])
    solved: Dict[str, Dict[str, int]] = {}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for slot in model["slots"]:
        link = slot.get("link")
        if not link:
            solved[slot["id"]] = {
                "x": int(slot["x"]),
                "y": int(slot["y"]),
                "w": int(slot["w"]),
                "h": int(slot["h"]),
            }
            continue
        groups.setdefault(link["id"], []).append(slot)

    for link_id, members in groups.items():
        axis = members[0]["link"]["axis"]
        span = int(model["height"] if axis == "vertical" else model["width"])
        members = sorted(
            members,
            key=lambda slot: (
                int(slot["y"] if axis == "vertical" else slot["x"]),
                slot["id"],
            ),
        )
        locked_sizes: Dict[str, int] = {}
        flexible: List[Dict[str, Any]] = []
        for slot in members:
            if slot["link"]["role"] == "locked":
                locked_sizes[slot["id"]] = _locked_outer(
                    slot, axis, span, used, warnings
                )
            else:
                designated = _designated(slot, axis)
                flexible.append(
                    {
                        "id": slot["id"],
                        "weight": designated,
                        "min": _eff_min(slot["link"], span),
                        "max": _eff_max(slot["link"], span),
                    }
                )
        gap_total = spacing * max(0, len(members) - 1)
        remaining = span - gap_total - sum(locked_sizes.values())
        flex_sizes, overflow = _distribute_flexible(remaining, flexible)
        if overflow or remaining < 0:
            warnings.append(
                {
                    "code": "min_overflow",
                    "link": link_id,
                    "message": (
                        "Minimum sizes are larger than the canvas, so this "
                        "link overflows."
                    ),
                }
            )
        cursor = 0
        for index, slot in enumerate(members):
            size = locked_sizes.get(slot["id"], flex_sizes.get(slot["id"], 0))
            if axis == "vertical":
                solved[slot["id"]] = {
                    "x": int(slot["x"]),
                    "y": cursor,
                    "w": int(slot["w"]),
                    "h": int(size),
                }
            else:
                solved[slot["id"]] = {
                    "x": cursor,
                    "y": int(slot["y"]),
                    "w": int(size),
                    "h": int(slot["h"]),
                }
            cursor += int(size)
            if index < len(members) - 1:
                cursor += spacing

    resolved_slots: List[Dict[str, Any]] = []
    for slot in model["slots"]:
        rect = solved[slot["id"]]
        content = _content_box(slot, rect["w"], rect["h"])
        axis = (slot.get("link") or {}).get("axis")
        iframe = _iframe_box(slot, axis, content)
        resolved_slots.append(
            {
                "id": slot["id"],
                "template": slot["template"],
                "visible": slot["visible"],
                "x": rect["x"],
                "y": rect["y"],
                "w": rect["w"],
                "h": rect["h"],
                "z": slot["z"],
                "opacity": slot["opacity"],
                "pointer_events": slot["pointer_events"],
                "fit": slot["fit"],
                "design_width": slot.get("design_width"),
                "design_height": slot.get("design_height"),
                "audio": slot["audio"],
                "padding": slot["padding"],
                "role": (slot.get("link") or {}).get("role"),
                "axis": axis,
                "link_id": (slot.get("link") or {}).get("id"),
                "designated_w": int(slot["w"]),
                "designated_h": int(slot["h"]),
                "content": content,
                "iframe": iframe,
            }
        )
    # Stable warning order, drop duplicate codes per link.
    seen = set()
    unique_warnings = []
    for warning in warnings:
        key = (warning["code"], warning.get("link"))
        if key in seen:
            continue
        seen.add(key)
        unique_warnings.append(warning)
    return {
        "width": int(model["width"]),
        "height": int(model["height"]),
        "spacing": spacing,
        "background": model["background"],
        "warnings": unique_warnings,
        "slots": resolved_slots,
    }


def embed_src(template: str, audio: Dict[str, Any]) -> str:
    """Same-origin URL for a member template inside a container."""
    volume = 0 if audio.get("muted") else int(audio.get("volume", 100))
    muted = "1" if audio.get("muted") or volume <= 0 else "0"
    return (
        f"/{quote(template, safe='')}"
        f"?mycelian_embed=1&mycelian_volume={volume}&mycelian_muted={muted}"
    )


def embed_audio_script(*, volume: float, muted: bool) -> str:
    """Script that mutes or attenuates media inside an embedded template."""
    level = 0.0 if muted else max(0.0, min(100.0, float(volume))) / 100.0
    return (
        '<script id="mycelian-embed-audio">(function(){'
        "if(window.__mycelianEmbedAudio)return;"
        "window.__mycelianEmbedAudio=true;"
        f"var volume={level:.4f};"
        "var muted=volume<=0;"
        "function apply(el){try{el.muted=muted;if(!muted)el.volume=volume;}catch(e){}}"
        "function scan(root){if(!root||!root.querySelectorAll)return;"
        "var nodes=root.querySelectorAll('audio,video');"
        "for(var i=0;i<nodes.length;i++)apply(nodes[i]);}"
        "var play=HTMLMediaElement.prototype.play;"
        "HTMLMediaElement.prototype.play=function(){apply(this);return play.apply(this,arguments);};"
        "function patch(Base,name){if(!Base)return;"
        "var Wrapped=function(){var ctx=new Base();"
        "try{var gain=ctx.createGain();gain.gain.value=volume;"
        "var dest=ctx.destination;var connect=AudioNode.prototype.connect;"
        "AudioNode.prototype.connect=function(target){"
        "if(target===dest){var node=connect.call(this,gain);"
        "if(!gain.__mycelianWired){connect.call(gain,dest);gain.__mycelianWired=true;}"
        "return node;}"
        "return connect.apply(this,arguments);};}catch(e){}"
        "return ctx;};"
        "Wrapped.prototype=Base.prototype;window[name]=Wrapped;}"
        "patch(window.AudioContext,'AudioContext');"
        "patch(window.webkitAudioContext,'webkitAudioContext');"
        "function arm(){scan(document);"
        "if(!document.documentElement)return;"
        "var obs=new MutationObserver(function(){scan(document);});"
        "obs.observe(document.documentElement,{childList:true,subtree:true});}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',arm);}"
        "else{arm();}"
        "})();</script>"
    )


def inject_embed_audio(html: str, *, volume: float, muted: bool) -> str:
    if "mycelian-embed-audio" in html:
        return html
    script = embed_audio_script(volume=volume, muted=muted)
    if "<head>" in html:
        return html.replace("<head>", "<head>" + script, 1)
    return script + html


_SHELL_CSS = """
html, body { margin: 0; padding: 0; overflow: hidden; background: transparent; }
.mc-slot { position: absolute; overflow: hidden; box-sizing: border-box; }
.mc-clip { position: absolute; overflow: hidden; }
.mc-slot iframe { border: 0; position: absolute; background: transparent; }
.mc-missing {
  position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; color: #fff; font: 14px/1.3 sans-serif;
  background: rgba(0,0,0,0.35); text-align: center; padding: 8px;
}
"""


def render_shell(container: Dict[str, Any]) -> str:
    """HTML document that iframes member templates. Does not inline them."""
    model = normalize_container(container)
    layout = solve_layout(model)
    title = html.escape(model["name"])
    background = model["background"]
    bg_css = "transparent" if background == "transparent" else background
    payload = {
        "container": model,
        "layout": layout,
    }
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    slot_html: List[str] = []
    for slot in layout["slots"]:
        if not slot.get("visible", True):
            continue
        style = (
            f"left:{slot['x']}px;top:{slot['y']}px;"
            f"width:{slot['w']}px;height:{slot['h']}px;"
            f"z-index:{int(slot['z'])};opacity:{int(slot['opacity']) / 100:.3f};"
        )
        if not slot.get("pointer_events", True):
            style += "pointer-events:none;"
        missing = not template_html_exists(slot["template"])
        if missing:
            label = html.escape(slot["template"])
            inner = f'<div class="mc-missing">Missing template: {label}</div>'
        else:
            src = html.escape(embed_src(slot["template"], slot["audio"]), quote=True)
            clip = slot["content"]
            frame = slot["iframe"]
            inner = (
                f'<div class="mc-clip" style="left:{clip["x"]}px;top:{clip["y"]}px;'
                f'width:{clip["w"]}px;height:{clip["h"]}px">'
                f'<iframe data-slot="{html.escape(slot["id"], quote=True)}" '
                f'src="{src}" allow="autoplay" '
                f'style="left:{frame["left"] - clip["x"]}px;top:{frame["top"] - clip["y"]}px;'
                f'width:{frame["w"]}px;height:{frame["h"]}px"></iframe></div>'
            )
        slot_html.append(
            f'<div class="mc-slot" data-slot="{html.escape(slot["id"], quote=True)}" '
            f'style="{style}">{inner}</div>'
        )
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{title}</title>",
        f"<style>{_SHELL_CSS}",
        "html, body {",
        f"width:{int(model['width'])}px;height:{int(model['height'])}px;",
        f"background:{bg_css};",
        "}",
        "</style>",
        "</head>",
        "<body>",
        f'<div id="mc-container">{"".join(slot_html)}</div>',
        f'<script id="mc-container-data" type="application/json">{data}</script>',
        '<script src="/assets/default_assets/spore_studio/container_layout.js"></script>',
        '<script src="/assets/default_assets/spore_studio/container_shell.js"></script>',
        "</body></html>",
    ]
    return "".join(parts)

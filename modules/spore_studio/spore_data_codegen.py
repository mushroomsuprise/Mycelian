#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Compile Spore Studio counters, data displays, and dynamic control listeners.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .data_source_registry import ALERT_PAYLOAD_KEYS, CHAT_PAYLOAD_KEYS
from .behavior_blocks import _js_string, _js_value
from .data_feed_registry import (
    effective_feeds,
    normalize_filters,
    runtime_output_table,
)


_DATA_RUNTIME_BEGIN = "// SPORE_STUDIO:data-runtime-begin"
_DATA_RUNTIME_END = "// SPORE_STUDIO:data-runtime-end"


def load_data_runtime_js() -> str:
    """Read shared runtime helpers from the boilerplate folder."""
    from ..path_utils import get_template_path

    path = os.path.join(get_template_path("_boilerplates"), "spore_data_runtime.js")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def inject_data_runtime_block(html: str) -> str:
    """Ensure boilerplate contains the shared data-runtime script block."""
    runtime = load_data_runtime_js()
    if not runtime:
        return html
    pattern = re.compile(
        re.escape(_DATA_RUNTIME_BEGIN) + r"[\s\S]*?" + re.escape(_DATA_RUNTIME_END),
        re.MULTILINE,
    )
    replacement = _DATA_RUNTIME_BEGIN + "\n" + runtime + "\n" + _DATA_RUNTIME_END
    match = pattern.search(html)
    if match:
        return html[: match.start()] + replacement + html[match.end() :]
    # Insert before auto-begin if markers missing (legacy boilerplate).
    auto = "// SPORE_STUDIO:auto-begin"
    if auto in html:
        return html.replace(auto, replacement + "\n\n        " + auto, 1)
    return html


def _slugify_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or ""))
    return value.strip("_") or "el"


DESIGN_CANVAS_MIN_PX = 50


def counter_format_config_id(element_id: str) -> str:
    """Public JSON / Jinja id for a counter or data-display format string."""
    return f"{_slugify_id(element_id)}_format"


def counter_image_default_src_id(element_id: str) -> str:
    return f"{_slugify_id(element_id)}_counter_default_src"


def counter_image_range_min_id(element_id: str, index: int) -> str:
    return f"{_slugify_id(element_id)}_range_{index}_min"


def counter_image_range_max_id(element_id: str, index: int) -> str:
    return f"{_slugify_id(element_id)}_range_{index}_max"


def counter_image_range_src_id(element_id: str, index: int) -> str:
    return f"{_slugify_id(element_id)}_range_{index}_src"


def progress_bar_max_config_id(element_id: str) -> str:
    return f"{_slugify_id(element_id)}_max"


def progress_bar_max_kind_config_id(element_id: str) -> str:
    return f"{_slugify_id(element_id)}_max_kind"


def timer_config_ids(element_id: str) -> Dict[str, str]:
    """Public JSON / Jinja ids and socket suffixes for one timer element."""
    slug = _slugify_id(element_id)
    return {
        "mode": f"{slug}_mode",
        "start_seconds": f"{slug}_start_seconds",
        "limit_seconds": f"{slug}_limit_seconds",
        "auto_start": f"{slug}_auto_start",
        "format": counter_format_config_id(slug),
    }


def timer_control_event(element_id: str, operation: str) -> str:
    """Socket suffix for a timer start, pause, or reset event."""
    op = str(operation or "start").strip().lower()
    if op not in ("start", "pause", "reset"):
        op = "start"
    return f"{_slugify_id(element_id)}_{op}"


def _nonneg_int(value: Any, default: int) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def timer_mode(props: Dict[str, Any] | None) -> str:
    mode = str((props or {}).get("mode") or "count_down").strip().lower()
    if mode not in ("count_up", "count_down"):
        return "count_down"
    return mode


def timer_start_seconds(props: Dict[str, Any] | None) -> int:
    """Countdown start. Older models store this as ``duration_seconds``."""
    props = props or {}
    if props.get("start_seconds") is not None:
        return _nonneg_int(props.get("start_seconds"), 300)
    return _nonneg_int(props.get("duration_seconds", 300), 300)


def timer_limit_seconds(props: Dict[str, Any] | None) -> int:
    """Count-up stop point. ``0`` means the timer does not stop."""
    props = props or {}
    if props.get("limit_seconds") is None:
        return 0
    return _nonneg_int(props.get("limit_seconds"), 0)


def timer_format(props: Dict[str, Any] | None) -> str:
    fmt = str((props or {}).get("format") or "{mm}:{ss}").strip()
    return fmt or "{mm}:{ss}"


def build_timer_connector_actions(
    elements: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Connector Template Control entries for each timer: start, pause, reset."""
    actions: Dict[str, Any] = {}
    for el in _collect_timers(elements):
        raw = str(el.get("id") or "timer")
        label = raw or "timer"
        for op, title in (("start", "Start"), ("pause", "Pause"), ("reset", "Reset")):
            aid = timer_control_event(raw, op)
            actions[aid] = {
                "action_name": f"{title} {label}",
                "trigger": aid,
                "elements": [],
            }
    return actions


def _jinja_tojson_default(var_name: str, default: Any) -> str:
    """Jinja expression embedded in generated JS (resolved at HTML render)."""
    return f"{{{{ {var_name}|default({json.dumps(default)})|tojson }}}}"


def _is_counter_element(el: Dict[str, Any]) -> bool:
    etype = str(el.get("type") or "").lower()
    if etype == "counter":
        return isinstance(el.get("counter"), dict)
    if etype in ("text", "marquee") and (el.get("text_mode") or "static") == "counter":
        return isinstance(el.get("counter"), dict)
    return False


def _is_display_element(el: Dict[str, Any]) -> bool:
    etype = str(el.get("type") or "").lower()
    if etype == "data_display":
        return isinstance(el.get("data_display"), dict) or True
    if etype in ("text", "marquee") and (el.get("text_mode") or "static") == "data_display":
        return isinstance(el.get("data_display"), dict)
    return False


def normalize_progressive_goals(goals: Any) -> List[float]:
    """Keep goal order, raise any goal that sits below the one before it, drop duplicates."""
    cleaned: List[float] = []
    for raw in goals or []:
        if isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if cleaned and number < cleaned[-1]:
            number = cleaned[-1]
        if cleaned and number == cleaned[-1]:
            continue
        cleaned.append(int(number) if number == int(number) else number)
    return cleaned


def progressive_goal_state(
    value: Any,
    goals: Any,
    min_bound: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Active goal and clamps for a progressive counter.

    The active goal is the first goal strictly above ``value``, or the last
    goal once that value has been reached. The floor is the previous goal
    (or ``min_bound`` before the first goal). The ceiling is the last goal.
    """
    cleaned = normalize_progressive_goals(goals)
    if not cleaned:
        return None
    try:
        current = float(value)
    except (TypeError, ValueError):
        current = 0.0
    if isinstance(value, bool):
        current = 0.0
    floor: Optional[float] = None
    if min_bound not in (None, ""):
        try:
            floor = float(min_bound)
        except (TypeError, ValueError):
            floor = None
    if floor is not None and current < floor:
        current = floor
    ceiling = cleaned[-1]
    if current > ceiling:
        current = ceiling
    active_index = len(cleaned) - 1
    for index, goal in enumerate(cleaned):
        if goal > current:
            active_index = index
            break
    if active_index > 0:
        floor = cleaned[active_index - 1]
    active = cleaned[active_index]

    def _num(number: Optional[float]) -> Optional[float]:
        if number is None:
            return None
        return int(number) if number == int(number) else number

    return {
        "value": _num(current),
        "goals": cleaned,
        "floor": _num(floor),
        "ceiling": _num(ceiling),
        "active_goal": _num(active),
        "active_index": active_index,
    }


def _counter_cfg_by_id(
    elements: List[Dict[str, Any]], counter_id: str
) -> Optional[Dict[str, Any]]:
    want = _slugify_id(counter_id)
    if not want:
        return None
    for el, cfg in _collect_counters(elements):
        cid = _slugify_id(str(cfg.get("counter_id") or el.get("id") or ""))
        if cid == want:
            return cfg
    return None


_TRANSFORM_PROPERTIES = frozenset(
    {"scale", "translate_x", "translate_y", "rotate", "skew_x", "skew_y"}
)


def _normalize_transform_rule(raw: Any) -> Optional[Dict[str, Any]]:
    """One continuous map from a data-panel value onto a CSS transform property."""
    if not isinstance(raw, dict):
        return None
    prop = str(raw.get("property") or "scale").strip()
    if prop not in _TRANSFORM_PROPERTIES:
        return None
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}

    def _num(key: str, default: float) -> float:
        try:
            if raw.get(key) in (None, ""):
                return default
            number = float(raw.get(key))
        except (TypeError, ValueError):
            return default
        return int(number) if number == int(number) else number

    origin = str(raw.get("origin") or "center").strip() or "center"
    return {
        "id": str(raw.get("id") or ""),
        "enabled": raw.get("enabled") is not False,
        "property": prop,
        "source": source,
        "in_min": _num("in_min", 0),
        "in_max": _num("in_max", 100),
        "out_min": _num("out_min", 0),
        "out_max": _num("out_max", 1),
        "origin": origin,
    }


def _compile_transform_table(model: Dict[str, Any], elements: List[Dict[str, Any]]) -> str:
    canvas: List[Dict[str, Any]] = []
    for raw in model.get("canvas_transforms") or []:
        rule = _normalize_transform_rule(raw)
        if rule:
            canvas.append(rule)
    by_element: Dict[str, List[Dict[str, Any]]] = {}
    for el in elements or []:
        if not isinstance(el, dict) or not el.get("id"):
            continue
        rules: List[Dict[str, Any]] = []
        for raw in el.get("transforms") or []:
            rule = _normalize_transform_rule(raw)
            if rule:
                rules.append(rule)
        if rules:
            by_element[str(el.get("id"))] = rules
    if not canvas and not by_element:
        return ""
    table = {"canvas": canvas, "elements": by_element}
    return (
        "window.__sporeTransforms = " + _js_value(table) + ";\n"
        "if (typeof sporeInitTransforms === 'function') { sporeInitTransforms(); }"
    )


def _collect_counters(elements: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict) or not _is_counter_element(el):
            continue
        cfg = el.get("counter")
        if not isinstance(cfg, dict):
            cfg = {}
            el["counter"] = cfg
        out.append((el, cfg))
    return out


def _collect_counter_images(
    elements: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "image":
            continue
        if str(el.get("src_mode") or "static").strip().lower() != "from_counter":
            continue
        cs = el.get("counter_src")
        if not isinstance(cs, dict):
            continue
        out.append((el, cs))
    return out


def _collect_progress_bars(
    elements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "progress_bar":
            continue
        ps = el.get("progress_source")
        if not isinstance(ps, dict):
            continue
        out.append(el)
    return out


def _collect_clocks(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "clock":
            continue
        out.append(el)
    return out


def _collect_timers(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "timer":
            continue
        out.append(el)
    return out


def _collect_displays(elements: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict) or not _is_display_element(el):
            continue
        cfg = el.get("data_display")
        if not isinstance(cfg, dict):
            cfg = {}
            el["data_display"] = cfg
        out.append((el, cfg))
    return out


def _counter_image_transition_meta(el: Dict[str, Any]) -> Dict[str, Any] | None:
    """Normalize counter range image transition settings for runtime JS."""
    tr = el.get("counter_image_transition")
    if not isinstance(tr, dict) or not tr.get("enabled"):
        return None
    atype = str(tr.get("type") or "fade").strip().lower()
    allowed = ("slide", "fade", "crossfade", "bounce", "roll")
    if atype not in allowed:
        atype = "fade"
    try:
        dur = max(0, int(float(tr.get("duration_ms", 400))))
    except (TypeError, ValueError):
        dur = 400
    return {
        "enabled": True,
        "type": atype,
        "duration_ms": dur,
        "easing": str(tr.get("easing") or "ease-out"),
    }


def _value_animation_meta(el: Dict[str, Any]) -> Dict[str, Any] | None:
    """Normalize value-change animation settings for runtime JS."""
    va = el.get("value_animation")
    if not isinstance(va, dict) or not va.get("enabled"):
        return None
    try:
        dur = max(0, int(float(va.get("duration_ms", 500))))
    except (TypeError, ValueError):
        dur = 500
    atype = str(va.get("type") or "fade-in").strip()
    if atype not in ("tick_up", "fade-in", "slide-in", "bounce"):
        atype = "fade-in"
    return {
        "enabled": True,
        "type": atype,
        "duration_ms": dur,
        "easing": str(va.get("easing") or "ease-out"),
        "pulse": bool(va.get("pulse")),
    }


def _counter_ids_unique(elements: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map counter_id -> element id."""
    mapping: Dict[str, str] = {}
    for el, cfg in _collect_counters(elements):
        cid = _slugify_id(str(cfg.get("counter_id") or el.get("id") or "counter"))
        mapping[cid] = str(el.get("id") or "")
    return mapping


def compile_spore_data_features(model: Dict[str, Any]) -> str:
    """
    Return JS appended inside the auto block: counter init, rules, displays,
    dynamic control listeners, payload key maps.
    """
    template_name = str(model.get("template_name") or "untitled")
    elements = model.get("elements") or []
    lines: List[str] = []

    lines.append("window.__sporeAlertPayloadKeys = " + _js_value(ALERT_PAYLOAD_KEYS) + ";")
    lines.append("window.__sporeChatPayloadKeys = " + _js_value(CHAT_PAYLOAD_KEYS) + ";")
    lines.append("window.__sporeTemplateName = " + _js_string(template_name) + ";")

    counter_images = _collect_counter_images(elements)
    if counter_images:
        lines.append("window.__sporeCounterImages = window.__sporeCounterImages || [];")
        for el, cs in counter_images:
            eid_slug = _slugify_id(str(el.get("id") or ""))
            eid = str(el.get("id") or "")
            cid = _slugify_id(str(cs.get("counter_id") or ""))
            default_default = str(
                cs.get("default_src")
                or (el.get("props") or {}).get("src")
                or ""
            )
            default_var = counter_image_default_src_id(eid_slug)
            range_parts: List[str] = []
            range_index = 0
            for row in cs.get("ranges") or []:
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
                src = str(row.get("src") or "").strip()
                if not src:
                    continue
                src_var = counter_image_range_src_id(eid_slug, range_index)
                range_parts.append(
                    "{"
                    f'"min": {lo}, "max": {hi}, '
                    f'"src": {_jinja_tojson_default(src_var, src)}'
                    "}"
                )
                range_index += 1
            ranges_js = ", ".join(range_parts)
            tr_meta = _counter_image_transition_meta(el)
            tr_js = (
                f', "range_transition": {_js_value(tr_meta)}'
                if tr_meta
                else ""
            )
            lines.append(
                "window.__sporeCounterImages.push({"
                f'"elementId": {_js_string(eid)}, '
                f'"counter_id": {_js_string(cid)}, '
                f'"default_src": {_jinja_tojson_default(default_var, default_default)}, '
                f'"ranges": [{ranges_js}]'
                f"{tr_js}"
                "});"
            )

    progress_bars = _collect_progress_bars(elements)
    if progress_bars:
        lines.append("window.__sporeProgressBars = window.__sporeProgressBars || [];")
        for el in progress_bars:
            eid_slug = _slugify_id(str(el.get("id") or ""))
            eid = str(el.get("id") or "")
            ps = el.get("progress_source") if isinstance(el.get("progress_source"), dict) else {}
            props = el.get("props") if isinstance(el.get("props"), dict) else {}
            cid = _slugify_id(str(ps.get("counter_id") or ""))
            max_kind = str(ps.get("max_kind") or "fixed").strip().lower()
            if max_kind not in ("fixed", "counter"):
                max_kind = "fixed"
            max_counter_id = _slugify_id(str(ps.get("max_counter_id") or ""))
            try:
                max_default = int(float(ps.get("max", 100)))
            except (TypeError, ValueError):
                max_default = 100
            max_var = progress_bar_max_config_id(eid_slug)
            try:
                threshold = int(float(props.get("near_goal_threshold", 90)))
            except (TypeError, ValueError):
                threshold = 90
            threshold = max(0, min(100, threshold))
            effect = str(props.get("near_goal_effect") or "none").strip().lower()
            if effect not in ("none", "pulse", "shimmer", "scroll"):
                effect = "pulse" if props.get("near_goal_pulse") else "none"
            effect_var = f"{eid_slug}_near_goal_effect"
            follow_goals = False
            if max_kind != "counter":
                bound = _counter_cfg_by_id(elements, cid)
                if (
                    isinstance(bound, dict)
                    and bound.get("progressive")
                    and normalize_progressive_goals(bound.get("goals"))
                ):
                    follow_goals = True
            lines.append(
                "window.__sporeProgressBars.push({"
                f'"elementId": {_js_string(eid)}, '
                f'"counter_id": {_js_string(cid)}, '
                f'"max_kind": {_js_string(max_kind)}, '
                f'"max": {_jinja_tojson_default(max_var, max_default)}, '
                f'"max_counter_id": {_js_string(max_counter_id)}, '
                f'"follow_counter_goals": {str(follow_goals).lower()}, '
                f'"near_goal_threshold": {threshold}, '
                f'"near_goal_effect": {_jinja_tojson_default(effect_var, effect)}'
                "});"
            )
        if not _collect_counters(elements):
            lines.append(
                "if (typeof sporeUpdateAllProgressBars === 'function') { "
                "sporeUpdateAllProgressBars(); }"
            )

    # Counter metadata registration
    for el, cfg in _collect_counters(elements):
        eid = str(el.get("id") or "")
        cid = _slugify_id(str(cfg.get("counter_id") or eid or "counter"))
        db_path = str(cfg.get("database_path") or f"{template_name}/counters").strip()
        db_key = str(cfg.get("database_key") or cid).strip()
        fmt_default = str(cfg.get("format") or "{value}")
        fmt_var = counter_format_config_id(_slugify_id(eid))
        goals = normalize_progressive_goals(cfg.get("goals"))
        progressive = bool(cfg.get("progressive")) and bool(goals)
        meta: Dict[str, Any] = {
            "elementId": eid,
            "counter_id": cid,
            "initial_value": cfg.get("initial_value", 0),
            "min": cfg.get("min"),
            "max": None if progressive else cfg.get("max"),
            "persist": bool(cfg.get("persist")),
            "database_path": db_path,
            "database_key": db_key,
        }
        if progressive:
            meta["progressive"] = True
            meta["goals"] = goals
        va_meta = _value_animation_meta(el)
        if va_meta:
            meta["value_animation"] = va_meta
        lines.append(
            f"window.__sporeCounterMeta[{_js_string(cid)}] = Object.assign("
            f"{_js_value(meta)}, "
            f'{{"format": {_jinja_tojson_default(fmt_var, fmt_default)}}});'
        )

    if _collect_counters(elements):
        cid_list = [
            _slugify_id(str(cfg.get("counter_id") or el.get("id") or "counter"))
            for el, cfg in _collect_counters(elements)
        ]
        lines.append("(function () {")
        lines.append("    var __ids = " + _js_value(cid_list) + ";")
        lines.append("    var __i = 0;")
        lines.append("    function __next() {")
        lines.append("        if (__i >= __ids.length) {")
        lines.append(
            "            if (typeof sporeUpdateAllProgressBars === 'function') { "
            "sporeUpdateAllProgressBars(); }"
        )
        lines.append("            return;")
        lines.append("        }")
        lines.append("        var __c = __ids[__i++];")
        lines.append("        sporeCounterHydrate(__c, __next);")
        lines.append("    }")
        lines.append("    __next();")
        lines.append("})();")

    # Counter rules
    for el, cfg in _collect_counters(elements):
        cid = _slugify_id(str(cfg.get("counter_id") or el.get("id") or "counter"))
        for rule in cfg.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            trigger = str(rule.get("trigger") or "event")
            op = str(rule.get("operation") or "increment")
            delta = rule.get("delta") if isinstance(rule.get("delta"), dict) else {"kind": "fixed", "value": 1}
            delta_js = _js_value(delta)
            if trigger == "event":
                ev = str(rule.get("event") or "")
                if not ev:
                    continue
                filt = rule.get("filter") if isinstance(rule.get("filter"), dict) else {}
                # Blank filter rows mean "any value" and must not be compiled.
                filt = {k: v for k, v in filt.items() if v not in ("", None)}
                shared = (
                    normalize_filters(rule.get("filters"))
                    if isinstance(rule.get("filters"), dict)
                    else None
                )
                shared_js = _js_value(shared) if shared else "null"
                lines.append(
                    f"(function () {{\n"
                    f"    var __ev = {_js_string(ev)};\n"
                    f"    var __cid = {_js_string(cid)};\n"
                    f"    var __op = {_js_string(op)};\n"
                    f"    var __delta = {delta_js};\n"
                    f"    var __filter = {_js_value(filt)};\n"
                    f"    var __shared = {shared_js};\n"
                    f"    var __fstate = {{}};\n"
                    f"    if (typeof socket !== 'undefined' && socket && socket.on) {{\n"
                    f"        socket.on(__ev, function (data) {{\n"
                    f"            if (!sporeMatchesFilter(data || {{}}, __filter)) {{ return; }}\n"
                    f"            if (__shared && typeof sporeFeedFilterPasses === 'function' && "
                    f"!sporeFeedFilterPasses(__shared, data || {{}}, __fstate)) {{ return; }}\n"
                    f"            window.__sporeLastPayload.event = data || {{}};\n"
                    f"            sporeCounterAdjust(__cid, __op, __delta, data);\n"
                    f"        }});\n"
                    f"    }}\n"
                    f"}})();"
                )
            elif trigger == "streamdeck":
                sd = str(rule.get("streamdeck_action") or "")
                if not sd:
                    continue
                lines.append(
                    f"(function () {{\n"
                    f"    if (typeof socket === 'undefined' || !socket || !socket.on) {{ return; }}\n"
                    f"    socket.on('streamdeck_template_action', function (data) {{\n"
                    f"        if (!data || data.actionName !== {_js_string(sd)}) {{ return; }}\n"
                    f"        sporeCounterAdjust({_js_string(cid)}, {_js_string(op)}, {delta_js}, data.data || data);\n"
                    f"    }});\n"
                    f"}})();"
                )

    # Unified data feeds (every block). Computed first so data displays know
    # whether a feed now drives them.
    feeds_by_element: Dict[str, List[Dict[str, Any]]] = {}
    for fel in elements:
        if isinstance(fel, dict) and fel.get("id"):
            feeds_by_element[str(fel["id"])] = effective_feeds(fel)

    def _display_has_feed(element_id: str) -> bool:
        return any(
            f.get("enabled") and (f.get("target") or {}).get("id") == "set_text"
            for f in feeds_by_element.get(element_id, [])
        )

    # Data displays
    display_events: Set[str] = set()
    for idx, (el, cfg) in enumerate(_collect_displays(elements)):
        eid = str(el.get("id") or "")
        src = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
        driven_by_feed = _display_has_feed(eid)
        spec = {
            "elementId": eid,
            "source": src,
            "default_text": str(cfg.get("default_text") if cfg.get("default_text") is not None else "—"),
        }
        if driven_by_feed:
            spec["feed"] = True
        va_disp = _value_animation_meta(el)
        if va_disp:
            spec["value_animation"] = va_disp
        fmt_default = str(cfg.get("format") or "{value}")
        fmt_var = counter_format_config_id(_slugify_id(eid))
        lines.append(
            f"window.__sporeDataDisplays.push(Object.assign("
            f"{_js_value(spec)}, "
            f'{{"format": {_jinja_tojson_default(fmt_var, fmt_default)}}}));'
        )
        if driven_by_feed:
            continue
        for ev in cfg.get("refresh_on") or []:
            if ev:
                display_events.add(str(ev))

    if display_events:
        for ev in sorted(display_events):
            lines.append(
                f"(function () {{\n"
                f"    var __ev = {_js_string(ev)};\n"
                f"    if (typeof socket !== 'undefined' && socket && socket.on) {{\n"
                f"        socket.on(__ev, function (data) {{\n"
                f"            window.__sporeLastPayload.event = data || {{}};\n"
                f"            sporeRefreshAllDataDisplays();\n"
                f"        }});\n"
                f"    }}\n"
                f"}})();"
            )

    poll_ms = 0
    for _el, cfg in _collect_displays(elements):
        if _display_has_feed(str(_el.get("id") or "")):
            continue
        try:
            poll_ms = max(poll_ms, int(cfg.get("poll_interval_ms") or 0))
        except (TypeError, ValueError):
            pass
    if poll_ms > 0:
        lines.append(
            f"setInterval(function () {{ sporeLoadStatsCache(); sporeRefreshAllDataDisplays(); }}, {poll_ms});"
        )

    lines.append("if (typeof socket !== 'undefined' && socket && socket.on) {")
    lines.append("    socket.on('connect', function () {")
    lines.append("        sporeLoadStatsCache();")
    lines.append("        sporeRefreshAllDataDisplays();")
    lines.append("    });")
    lines.append("    socket.on('twitch-api-response', function (data) {")
    lines.append("        if (data && data.requestId) {")
    lines.append("            window.__sporeTwitchCache[data.requestId] = data; }")
    lines.append("        sporeRefreshAllDataDisplays();")
    lines.append("    });")
    lines.append("}")

    # Dynamic controls: listen for template_action events
    dc = model.get("dynamic_controls")
    emitted_control_events: set[str] = set()
    if isinstance(dc, dict):
        for ctrl in dc.get("elements") or []:
            if not isinstance(ctrl, dict):
                continue
            action = str(ctrl.get("action") or "").strip()
            if not action:
                continue
            if str(ctrl.get("type") or "") == "timer_control":
                continue
            handler_action = action
            if ctrl.get("type") == "counter_control":
                handler_action = "counter_adjust"
            payload: Dict[str, Any] = {}
            if handler_action == "counter_adjust":
                if ctrl.get("type") == "counter_control":
                    payload = {
                        "target_counter_id": ctrl.get("target_counter_id") or "",
                    }
                else:
                    payload = {
                        "target_counter_id": ctrl.get("target_counter_id") or "",
                        "operation": ctrl.get("operation") or "increment",
                        "delta": ctrl.get("delta") if isinstance(ctrl.get("delta"), dict) else {"kind": "fixed", "value": 1},
                    }
            elif ctrl.get("type") in ("text_input", "number_input", "slider"):
                payload = {"value": "{{value}}"}
            elif isinstance(ctrl.get("data"), dict):
                payload = dict(ctrl["data"])
            ev_name = f"{template_name}_{action}"
            if ev_name in emitted_control_events:
                continue
            emitted_control_events.add(ev_name)
            lines.append(
                f"(function () {{\n"
                f"    var __tn = {_js_string(template_name)};\n"
                f"    var __act = {_js_string(handler_action)};\n"
                f"    var __base = {_js_value(payload)};\n"
                f"    if (typeof socket !== 'undefined' && socket && socket.on) {{\n"
                f"        socket.on({_js_string(ev_name)}, function (data) {{\n"
                f"            var __d = {{}};\n"
                f"            for (var k in __base) {{ if (__base.hasOwnProperty(k)) __d[k] = __base[k]; }}\n"
                f"            if (data && typeof data === 'object') {{\n"
                f"                for (var k2 in data) {{ if (data.hasOwnProperty(k2)) __d[k2] = data[k2]; }}\n"
                f"            }}\n"
                f"            sporeDispatchControlAction(__act, __d, __tn);\n"
                f"        }});\n"
                f"    }}\n"
                f"}})();"
            )

    clocks = _collect_clocks(elements)
    if clocks:
        lines.append("window.__sporeClocks = window.__sporeClocks || [];")
        for el in clocks:
            eid_slug = _slugify_id(str(el.get("id") or ""))
            eid = str(el.get("id") or "")
            props = el.get("props") if isinstance(el.get("props"), dict) else {}
            fmt_default = str(props.get("format") or "HH:mm:ss")
            fmt_var = counter_format_config_id(eid_slug)
            tz = str(props.get("timezone") or "local").strip().lower()
            if tz not in ("local", "utc", "offset"):
                tz = "local"
            try:
                off = int(float(props.get("timezone_offset_minutes", 0)))
            except (TypeError, ValueError):
                off = 0
            lines.append(
                "window.__sporeClocks.push({"
                f'"elementId": {_js_string(eid)}, '
                f'"format": {_jinja_tojson_default(fmt_var, fmt_default)}, '
                f'"timezone": {_js_string(tz)}, '
                f'"timezone_offset_minutes": {off}'
                "});"
            )

    timers = _collect_timers(elements)
    if timers:
        lines.append("window.__sporeTimers = window.__sporeTimers || {};")
        for el in timers:
            eid = str(el.get("id") or "")
            props = el.get("props") if isinstance(el.get("props"), dict) else {}
            mode = timer_mode(props)
            start = timer_start_seconds(props)
            limit = timer_limit_seconds(props)
            fmt_default = timer_format(props)
            ids = timer_config_ids(eid)
            auto = bool(props.get("auto_start", True))
            lines.append(
                f"window.__sporeTimers[{_js_string(eid)}] = {{"
                f'"elementId": {_js_string(eid)}, '
                f'"mode": {_jinja_tojson_default(ids["mode"], mode)}, '
                f'"start_seconds": {_jinja_tojson_default(ids["start_seconds"], start)}, '
                f'"limit_seconds": {_jinja_tojson_default(ids["limit_seconds"], limit)}, '
                f'"duration_seconds": {_jinja_tojson_default(ids["start_seconds"], start)}, '
                f'"auto_start": {_jinja_tojson_default(ids["auto_start"], auto)}, '
                f'"format": {_jinja_tojson_default(ids["format"], fmt_default)}, '
                f'"running": false, "elapsed": 0, "started_at": null'
                "};"
            )
            slug_start = f"{template_name}_{timer_control_event(eid, 'start')}"
            slug_pause = f"{template_name}_{timer_control_event(eid, 'pause')}"
            slug_reset = f"{template_name}_{timer_control_event(eid, 'reset')}"
            lines.append(
                "(function () {\n"
                f"    var __id = {_js_string(eid)};\n"
                "    function __bind(ev, fn) {\n"
                "        if (typeof socket !== 'undefined' && socket && socket.on) {\n"
                "            socket.on(ev, fn);\n"
                "        }\n"
                "    }\n"
                f"    __bind({_js_string(slug_start)}, function () {{\n"
                "        if (typeof sporeTimerStart === 'function') { sporeTimerStart(__id); }\n"
                "    });\n"
                f"    __bind({_js_string(slug_pause)}, function () {{\n"
                "        if (typeof sporeTimerPause === 'function') { sporeTimerPause(__id); }\n"
                "    });\n"
                f"    __bind({_js_string(slug_reset)}, function () {{\n"
                "        if (typeof sporeTimerReset === 'function') { sporeTimerReset(__id); }\n"
                "    });\n"
                "})();"
            )

    if clocks:
        lines.append("if (typeof sporeInitClocks === 'function') { sporeInitClocks(); }")
    if timers:
        lines.append("if (typeof sporeInitTimers === 'function') { sporeInitTimers(); }")

    lines.append("if (typeof sporePatchSetText === 'function') { sporePatchSetText(); }")

    feed_rows: List[Dict[str, Any]] = []
    for el in elements:
        if not isinstance(el, dict) or not el.get("id"):
            continue
        eid = str(el["id"])
        etype = str(el.get("type") or "").lower()
        for fidx, feed in enumerate(feeds_by_element.get(eid, [])):
            if not feed.get("enabled"):
                continue
            row = {
                "elementId": eid,
                "elementType": etype,
                "id": feed["id"],
                "enabled": True,
                "source": feed["source"],
                "filters": feed["filters"],
                "mapping": dict(feed["mapping"]),
                "timing": feed["timing"],
                "target": feed["target"],
            }
            # A list's row template stays editable from Source Settings.
            if (
                etype == "list"
                and fidx == 0
                and (feed["target"] or {}).get("id") == "list_add"
                and row["mapping"].get("template")
            ):
                row["mapping"]["template"] = "__SPORE_ROW_TEMPLATE__"
            feed_rows.append(row)
    feeds_js = _js_value(feed_rows)
    for row in feed_rows:
        if row["mapping"].get("template") == "__SPORE_ROW_TEMPLATE__":
            original = next(
                f["mapping"]["template"]
                for f in feeds_by_element[row["elementId"]]
                if f["id"] == row["id"]
            )
            var = f"{_slugify_id(row['elementId'])}_row_template"
            feeds_js = feeds_js.replace(
                '"__SPORE_ROW_TEMPLATE__"',
                _jinja_tojson_default(var, original),
                1,
            )
    lines.append("window.__sporeFeedsCompiled = true;")
    lines.append("window.__sporeFeeds = " + feeds_js + ";")
    lines.append(
        "window.__sporeOutputs = "
        + _js_value(runtime_output_table(elements))
        + ";"
    )

    lines.append("if (typeof sporeInitWidgets === 'function') { sporeInitWidgets(); }")
    transform_js = _compile_transform_table(model, elements)
    if transform_js:
        lines.append(transform_js)

    return "\n".join(lines)


def compile_spore_data_css() -> str:
    return ""

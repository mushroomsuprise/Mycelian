#!/usr/bin/env python3
"""
Compile Spore Studio counters, data displays, and dynamic control listeners.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set, Tuple

from .data_source_registry import ALERT_PAYLOAD_KEYS, CHAT_PAYLOAD_KEYS
from .behavior_blocks import _js_string, _js_value


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


def _collect_counters(elements: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "text":
            continue
        if (el.get("text_mode") or "static") != "counter":
            continue
        cfg = el.get("counter")
        if not isinstance(cfg, dict):
            continue
        out.append((el, cfg))
    return out


def _collect_displays(elements: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict) or (el.get("type") or "").lower() != "text":
            continue
        if (el.get("text_mode") or "static") != "data_display":
            continue
        cfg = el.get("data_display")
        if not isinstance(cfg, dict):
            continue
        out.append((el, cfg))
    return out


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

    # Counter metadata registration
    for el, cfg in _collect_counters(elements):
        eid = str(el.get("id") or "")
        cid = _slugify_id(str(cfg.get("counter_id") or eid or "counter"))
        db_path = str(cfg.get("database_path") or f"{template_name}/counters").strip()
        db_key = str(cfg.get("database_key") or cid).strip()
        meta: Dict[str, Any] = {
            "elementId": eid,
            "counter_id": cid,
            "initial_value": cfg.get("initial_value", 0),
            "min": cfg.get("min"),
            "max": cfg.get("max"),
            "format": str(cfg.get("format") or "{value}"),
            "persist": bool(cfg.get("persist")),
            "database_path": db_path,
            "database_key": db_key,
        }
        lines.append(
            f"window.__sporeCounterMeta[{_js_string(cid)}] = {_js_value(meta)};"
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
        lines.append("        if (__i >= __ids.length) { return; }")
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
                lines.append(
                    f"(function () {{\n"
                    f"    var __ev = {_js_string(ev)};\n"
                    f"    var __cid = {_js_string(cid)};\n"
                    f"    var __op = {_js_string(op)};\n"
                    f"    var __delta = {delta_js};\n"
                    f"    var __filter = {_js_value(filt)};\n"
                    f"    if (typeof socket !== 'undefined' && socket && socket.on) {{\n"
                    f"        socket.on(__ev, function (data) {{\n"
                    f"            if (!sporeMatchesFilter(data || {{}}, __filter)) {{ return; }}\n"
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

    # Data displays
    display_events: Set[str] = set()
    for idx, (el, cfg) in enumerate(_collect_displays(elements)):
        eid = str(el.get("id") or "")
        src = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
        spec = {
            "elementId": eid,
            "source": src,
            "format": str(cfg.get("format") or "{value}"),
            "default_text": str(cfg.get("default_text") if cfg.get("default_text") is not None else "—"),
        }
        lines.append(f"window.__sporeDataDisplays.push({_js_value(spec)});")
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
    if isinstance(dc, dict):
        for ctrl in dc.get("elements") or []:
            if not isinstance(ctrl, dict):
                continue
            action = str(ctrl.get("action") or "").strip()
            if not action:
                continue
            handler_action = action
            if ctrl.get("type") == "counter_control":
                handler_action = "counter_adjust"
            payload: Dict[str, Any] = {}
            if handler_action == "counter_adjust":
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

    return "\n".join(lines)


def compile_spore_data_css() -> str:
    return ""

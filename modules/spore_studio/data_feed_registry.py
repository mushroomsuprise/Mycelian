#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Unified Spore Studio "Data feed" registry.

Every block in Spore Studio can receive data through the same feed model:

    source  ->  filters  ->  mapping / formatting  ->  timing  ->  target

This module is the single place that describes the pieces of that pipeline so
the editor, codegen, and tests share one definition:

* filter operators and text formatters offered to every block
* the *targets* a feed can send its result to (the only per-block difference)
* the *outputs* each block can publish for other blocks and templates
* normalisation of ``data_feed`` models
* migration of the legacy per-widget wiring (list ``source``, wheel
  ``source``, stat card ``stat_source``, text ``data_display``)
* the merged catalog served to the editor
"""

from __future__ import annotations

import json
import os
import copy
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Filters and formatting (shared by every block)
# ---------------------------------------------------------------------------

FILTER_OPERATORS: List[Dict[str, Any]] = [
    {"id": "eq", "label": "equals", "needs_value": True},
    {"id": "ne", "label": "does not equal", "needs_value": True},
    {"id": "contains", "label": "contains", "needs_value": True},
    {"id": "not_contains", "label": "does not contain", "needs_value": True},
    {"id": "starts_with", "label": "starts with", "needs_value": True},
    {"id": "ends_with", "label": "ends with", "needs_value": True},
    {"id": "regex", "label": "matches regex", "needs_value": True},
    {"id": "gt", "label": "is greater than", "needs_value": True},
    {"id": "gte", "label": "is at least", "needs_value": True},
    {"id": "lt", "label": "is less than", "needs_value": True},
    {"id": "lte", "label": "is at most", "needs_value": True},
    {"id": "between", "label": "is between (min,max)", "needs_value": True},
    {"id": "in_list", "label": "is one of (comma list)", "needs_value": True},
    {"id": "not_in_list", "label": "is not one of (comma list)", "needs_value": True},
    {"id": "empty", "label": "is empty", "needs_value": False},
    {"id": "not_empty", "label": "is not empty", "needs_value": False},
]

FILTER_OPERATOR_IDS = frozenset(row["id"] for row in FILTER_OPERATORS)

# ``{field|modifier|modifier:arg}`` modifiers understood by the runtime.
FORMATTERS: List[Dict[str, Any]] = [
    {"id": "number", "label": "Number with separators", "example": "{amount|number}"},
    {"id": "compact", "label": "Compact number (1.2K)", "example": "{total|compact}"},
    {"id": "int", "label": "Whole number", "example": "{amount|int}"},
    {"id": "decimals", "label": "Fixed decimals", "arg": "digits", "example": "{amount|decimals:2}"},
    {"id": "percent", "label": "Percent", "example": "{ratio|percent}"},
    {"id": "duration", "label": "Seconds as m:ss", "example": "{duration|duration}"},
    {"id": "ordinal", "label": "Ordinal (1st, 2nd)", "example": "{index|ordinal}"},
    {"id": "upper", "label": "UPPERCASE", "example": "{username|upper}"},
    {"id": "lower", "label": "lowercase", "example": "{username|lower}"},
    {"id": "title", "label": "Title Case", "example": "{username|title}"},
    {"id": "trim", "label": "Trim spaces", "example": "{message|trim}"},
    {"id": "truncate", "label": "Truncate", "arg": "length", "example": "{message|truncate:40}"},
    {"id": "default", "label": "Fallback text", "arg": "text", "example": "{username|default:Anonymous}"},
]

SAMPLE_PAYLOAD_FALLBACKS: Dict[str, Any] = {
    "string": "sample",
    "number": 42,
    "boolean": True,
    "array": ["one", "two"],
}

# ---------------------------------------------------------------------------
# Targets: the only thing that differs from block to block
# ---------------------------------------------------------------------------


def _opt(
    key: str,
    label: str,
    type_: str = "text",
    *,
    default: Any = "",
    options: Optional[List[Any]] = None,
    tooltip: str = "",
    min_: Optional[float] = None,
    max_: Optional[float] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "key": key,
        "label": label,
        "type": type_,
        "default": default,
        "tooltip": tooltip,
    }
    if options is not None:
        row["options"] = list(options)
    if min_ is not None:
        row["min"] = min_
    if max_ is not None:
        row["max"] = max_
    return row


def _target(
    tid: str,
    label: str,
    description: str = "",
    *,
    options: Optional[List[Dict[str, Any]]] = None,
    accepts: str = "item",
) -> Dict[str, Any]:
    """``accepts`` is ``item`` (uses mapped data) or ``trigger`` (just fires)."""
    return {
        "id": tid,
        "label": label,
        "description": description,
        "accepts": accepts,
        "options": options or [],
    }


_STYLE_PROPERTIES = [
    "background_color",
    "color",
    "border_color",
    "opacity",
    "font_size",
]

# Targets every block offers in addition to its own.
GENERIC_TARGETS: List[Dict[str, Any]] = [
    _target("show", "Show this block", accepts="trigger"),
    _target("hide", "Hide this block", accepts="trigger"),
    _target("toggle", "Toggle visibility", accepts="trigger"),
    _target(
        "show_for",
        "Show for a few seconds",
        "Shows the block, then hides it again.",
        options=[
            _opt("seconds", "Seconds", "number", default=5, min_=0.1, max_=600),
        ],
        accepts="trigger",
    ),
    _target(
        "flash_class",
        "Play a CSS animation class",
        "Adds a class briefly. Try sporeShake or sporePop.",
        options=[
            _opt("class_name", "Class name", "text", default="sporePop"),
            _opt("duration_ms", "Hold (ms)", "number", default=420, min_=0, max_=60000),
        ],
        accepts="trigger",
    ),
    _target(
        "set_style",
        "Change a style property",
        "Applies the mapped value (or a fixed value) to one style property.",
        options=[
            _opt("property", "Property", "select", default="background_color", options=_STYLE_PROPERTIES),
            _opt("fixed", "Fixed value", "text", default="", tooltip="Leave blank to use the mapped value."),
        ],
    ),
]

BLOCK_TARGETS: Dict[str, List[Dict[str, Any]]] = {
    "list": [
        _target(
            "list_add",
            "Add a row",
            "Adds one row for every matching item. Placement, row limit, lifetime and animations are set in the List section.",
        ),
        _target("list_clear", "Clear the list", accepts="trigger"),
    ],
    "wheel": [
        _target(
            "wheel_spin",
            "Spin the wheel",
            "Spins when an item arrives. Optionally fill the slices from a list in the data and land on a chosen label.",
            options=[
                _opt(
                    "segments_field", "Fill slices from field", "text", default="",
                    tooltip="Payload field holding a list of names (for example pool_entries). Blank keeps the current slices.",
                ),
                _opt(
                    "land_field", "Land on field", "text", default="",
                    tooltip="Payload field naming the winning label (a list uses its first entry). Blank spins to a random slice.",
                ),
            ],
        ),
        _target(
            "wheel_add_segment",
            "Add a slice",
            "Adds a slice labeled with the mapped text.",
            options=[_opt("color", "Slice color", "color", default="")],
        ),
    ],
    "stat_card": [
        _target("set_value", "Set the card value", "Shows the mapped value using the card's Value format."),
    ],
    "progress_bar": [
        _target("set_value", "Set the current value", "Overrides the bar's current value with the mapped number."),
        _target("set_max", "Set the goal (max)", "Overrides the bar's goal with the mapped number."),
    ],
    "text": [
        _target("set_text", "Replace this text", "Writes the mapped text into this block."),
    ],
    "marquee": [
        _target("set_text", "Replace this scrolling text", "Writes the mapped text into this block."),
    ],
    "counter": [
        _target("counter_increment", "Add to this counter", "Adds the mapped number. Uses 1 when no number is mapped."),
        _target("counter_decrement", "Subtract from this counter", "Subtracts the mapped number. Uses 1 when no number is mapped."),
        _target("counter_set", "Set this counter", "Sets the counter to the mapped number."),
        _target("counter_reset", "Reset this counter", "Returns the counter to its initial value.", accepts="trigger"),
    ],
    "data_display": [
        _target("set_text", "Show this value", "Writes the mapped text into this display."),
    ],
    "timer": [
        _target("timer_start", "Start the timer", accepts="trigger"),
        _target("timer_pause", "Pause the timer", accepts="trigger"),
        _target("timer_reset", "Reset the timer", accepts="trigger"),
        _target(
            "timer_set",
            "Set the timer",
            "Sets the shown time from the mapped number (seconds).",
        ),
    ],
    "image": [
        _target("set_src", "Set the image", "Uses the mapped text as the image URL or asset file."),
    ],
    "video": [
        _target("set_src", "Set the video / GIF", "Uses the mapped text as the media URL or asset file."),
        _target(
            "play_media",
            "Play the audio",
            accepts="trigger",
            options=[_opt("volume", "Volume override (0-1)", "number", default="", min_=0, max_=1)],
        ),
    ],
    "audio": [
        _target("set_src", "Set the audio file", "Uses the mapped text as the audio URL or asset file."),
        _target(
            "play_media",
            "Play the audio",
            accepts="trigger",
            options=[_opt("volume", "Volume override (0-1)", "number", default="", min_=0, max_=1)],
        ),
    ],
    "qr": [
        _target("set_qr_text", "Set the QR text", "Encodes the mapped text."),
    ],
    "embed": [
        _target("set_embed_url", "Set the page URL", "Loads the mapped text as the page URL."),
    ],
    "poll": [
        _target("repaint", "Paint from the data", "Repaints the poll from a poll-shaped payload."),
    ],
    "now_playing": [
        _target("repaint", "Paint from the data", "Repaints from a now-playing-shaped payload."),
    ],
}

# Which target each block uses when a feed does not name one.
DEFAULT_TARGET: Dict[str, str] = {
    "list": "list_add",
    "wheel": "wheel_spin",
    "stat_card": "set_value",
    "progress_bar": "set_value",
    "text": "set_text",
    "marquee": "set_text",
    "counter": "counter_increment",
    "data_display": "set_text",
    "timer": "timer_start",
    "image": "set_src",
    "video": "set_src",
    "audio": "play_media",
    "qr": "set_qr_text",
    "embed": "set_embed_url",
    "poll": "repaint",
    "now_playing": "repaint",
}

# Sources a block starts with when a feed is added from scratch.
DEFAULT_SOURCE_EVENTS: Dict[str, List[str]] = {
    "poll": ["poll_state", "poll_ended"],
    "now_playing": ["spotify_data_update"],
}

BLOCK_TYPES = (
    "audio", "clock", "container", "counter", "data_display", "divider", "embed",
    "gradient", "image", "list", "marquee", "now_playing", "poll", "progress_bar",
    "qr", "shape", "stat_card", "text", "timer", "video", "wheel",
)


def targets_for(block_type: str) -> List[Dict[str, Any]]:
    """Block targets followed by the generic ones every block has."""
    own = [copy.deepcopy(t) for t in BLOCK_TARGETS.get(block_type, [])]
    ids = {t["id"] for t in own}
    return own + [copy.deepcopy(t) for t in GENERIC_TARGETS if t["id"] not in ids]


def target_ids_for(block_type: str) -> List[str]:
    return [t["id"] for t in targets_for(block_type)]


# ---------------------------------------------------------------------------
# Outputs each block publishes
# ---------------------------------------------------------------------------


def _out(
    oid: str,
    event: str,
    label: str,
    description: str,
    payload: List[Dict[str, str]],
    *,
    match_key: str = "elementId",
    default_enabled: bool = True,
    only_when: str = "",
) -> Dict[str, Any]:
    return {
        "id": oid,
        "event": event,
        "label": label,
        "description": description,
        "payload": payload,
        "match_key": match_key,
        "default_enabled": default_enabled,
        "only_when": only_when,
    }


def _p(key: str, label: str, type_: str = "string") -> Dict[str, str]:
    return {"key": key, "label": label, "type": type_}


_TEMPLATE_FIELD = _p("template", "Template name")
_ELEMENT_FIELD = _p("elementId", "Block id")

BLOCK_OUTPUTS: Dict[str, List[Dict[str, Any]]] = {
    "wheel": [
        _out(
            "result", "wheel_result", "result",
            "Winning slice when the wheel stops.",
            [_TEMPLATE_FIELD, _ELEMENT_FIELD, _p("index", "Slice index", "number"), _p("label", "Winning label")],
        ),
    ],
    "poll": [
        _out(
            "result", "poll_result", "result",
            "Winners when the poll ends.",
            [
                _TEMPLATE_FIELD, _ELEMENT_FIELD,
                _p("winners", "Winners", "array"),
                _p("total_votes", "Total votes", "number"),
                _p("options", "Options", "array"),
            ],
        ),
    ],
    "timer": [
        _out(
            "finished", "timer_finished", "finished",
            "The timer reached its limit or countdown (not fired on pause).",
            [_TEMPLATE_FIELD, _ELEMENT_FIELD, _p("mode", "Mode"), _p("elapsed", "Elapsed seconds", "number")],
        ),
    ],
    "counter": [
        _out(
            "changed", "counter_changed", "counter changed",
            "The counter value changed.",
            [
                _TEMPLATE_FIELD, _ELEMENT_FIELD,
                _p("counterId", "Counter id"),
                _p("value", "Value", "number"),
                _p("delta", "Change", "number"),
            ],
        ),
    ],
    "progress_bar": [
        _out(
            "goal", "goal_reached", "goal reached",
            "The bar's value reached its goal.",
            [
                _TEMPLATE_FIELD, _ELEMENT_FIELD,
                _p("counterId", "Counter id"),
                _p("value", "Value", "number"),
                _p("max", "Goal", "number"),
            ],
        ),
    ],
    "list": [
        _out(
            "row_added", "list_row_added", "row added",
            "A row was added to the list.",
            [_TEMPLATE_FIELD, _ELEMENT_FIELD, _p("text", "Row text")],
            default_enabled=False,
        ),
        _out(
            "cleared", "list_cleared", "cleared",
            "The list was cleared.",
            [_TEMPLATE_FIELD, _ELEMENT_FIELD],
            default_enabled=False,
        ),
    ],
}


def _output_applies(spec: Dict[str, Any], element: Dict[str, Any]) -> bool:
    only = spec.get("only_when")
    if only == "counter":
        etype = str(element.get("type") or "").lower()
        return etype == "counter" or (element.get("text_mode") or "static") == "counter"
    return True


def outputs_for_block(block_type: str) -> List[Dict[str, Any]]:
    return [copy.deepcopy(o) for o in BLOCK_OUTPUTS.get(block_type, [])]


def output_settings(element: Dict[str, Any], output_id: str) -> Dict[str, Any]:
    """Per-element ``{enabled, label}`` override for one output (may be empty)."""
    raw = element.get("output_settings")
    if isinstance(raw, dict) and isinstance(raw.get(output_id), dict):
        return raw[output_id]
    return {}


def output_enabled(element: Dict[str, Any], spec: Dict[str, Any]) -> bool:
    cfg = output_settings(element, str(spec.get("id") or ""))
    if "enabled" in cfg:
        return bool(cfg.get("enabled"))
    return bool(spec.get("default_enabled", True))


def build_widget_outputs(elements: List[Dict[str, Any]], template_name: str) -> Dict[str, Any]:
    """
    Public-JSON ``outputs`` entries generated from the widgets in a model.

    One entry per widget instance per enabled output, keyed
    ``<elementId>_<outputId>``. Every entry carries ``generated: true`` so a
    save can replace stale generated entries without touching hand-written ones.
    """
    out: Dict[str, Any] = {}
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        eid = str(element.get("id") or "")
        etype = str(element.get("type") or "").lower()
        if not eid:
            continue
        for spec in BLOCK_OUTPUTS.get(etype, []):
            if not _output_applies(spec, element) or not output_enabled(element, spec):
                continue
            cfg = output_settings(element, spec["id"])
            label = str(cfg.get("label") or "").strip() or f"{eid} {spec['label']}"
            entry: Dict[str, Any] = {
                "event": spec["event"],
                "label": label,
                "description": spec["description"],
                "element_id": eid,
                "element_type": etype,
                "payload": [dict(p) for p in spec["payload"]],
                "generated": True,
            }
            match_key = spec.get("match_key")
            if match_key:
                entry["match"] = {match_key: eid}
            out[f"{eid}_{spec['id']}"] = entry
    return out


def runtime_output_table(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact table the runtime uses to decide which outputs to emit."""
    rows: List[Dict[str, Any]] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        eid = str(element.get("id") or "")
        etype = str(element.get("type") or "").lower()
        if not eid:
            continue
        for spec in BLOCK_OUTPUTS.get(etype, []):
            if not _output_applies(spec, element):
                continue
            rows.append(
                {
                    "element_id": eid,
                    "event": spec["event"],
                    "enabled": output_enabled(element, spec),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Feed model: defaults, normalisation, migration
# ---------------------------------------------------------------------------

_ALERT_DEFAULT_EVENTS = ["instant_alert", "next_alert"]


def default_feed(block_type: str = "", feed_id: str = "feed1") -> Dict[str, Any]:
    events = DEFAULT_SOURCE_EVENTS.get(block_type) or list(_ALERT_DEFAULT_EVENTS)
    return {
        "id": feed_id,
        "enabled": True,
        "label": "",
        "source": {
            "kind": "event",
            "events": list(events),
            "template_output": {"template": "", "event": "", "match": {}},
            "value": {"kind": "data_source", "source": ""},
            "refresh_ms": 0,
            "refresh_on": [],
        },
        "filters": {
            "alert_type": "any",
            "tier": "any",
            "rules": [],
            "match": "all",
            "users_allow": "",
            "users_block": "",
            "cooldown_ms": 0,
            "max_per_minute": 0,
            "dedupe_ms": 0,
            "skip_first": 0,
        },
        "mapping": {
            "text_field": "",
            "value_field": "",
            "template": "",
            "split_field": "",
            "prefix": "",
            "suffix": "",
            "decimals": "",
            "fallback": "",
        },
        "timing": {"delay_ms": 0},
        "target": {"id": DEFAULT_TARGET.get(block_type, "show"), "options": {}},
    }


def _num(value: Any, default: float = 0, *, minimum: Optional[float] = None) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    if minimum is not None and n < minimum:
        return minimum
    return n


def _int(value: Any, default: int = 0, *, minimum: int = 0) -> int:
    return int(_num(value, default, minimum=minimum))


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_filter_rules(rules: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(rules, list):
        return out
    for row in rules:
        if not isinstance(row, dict):
            continue
        field = _str(row.get("field")).strip()
        op = _str(row.get("op")).strip() or "eq"
        if not field or op not in FILTER_OPERATOR_IDS:
            continue
        out.append({"field": field, "op": op, "value": _str(row.get("value"))})
    return out


def normalize_filters(raw: Any) -> Dict[str, Any]:
    base = default_feed()["filters"]
    src = raw if isinstance(raw, dict) else {}
    match = _str(src.get("match")).lower()
    return {
        "alert_type": _str(src.get("alert_type") or base["alert_type"]).strip().lower() or "any",
        "tier": _str(src.get("tier") or base["tier"]).strip() or "any",
        "rules": normalize_filter_rules(src.get("rules")),
        "match": match if match in ("all", "any") else "all",
        "users_allow": _str(src.get("users_allow")).strip(),
        "users_block": _str(src.get("users_block")).strip(),
        "cooldown_ms": _int(src.get("cooldown_ms")),
        "max_per_minute": _int(src.get("max_per_minute")),
        "dedupe_ms": _int(src.get("dedupe_ms")),
        "skip_first": _int(src.get("skip_first")),
    }


def normalize_feed(raw: Any, block_type: str = "", index: int = 0) -> Dict[str, Any]:
    """Return a complete, validated feed dict (unknown keys dropped)."""
    base = default_feed(block_type, f"feed{index + 1}")
    src = raw if isinstance(raw, dict) else {}

    feed_id = _str(src.get("id")).strip() or base["id"]
    source_in = src.get("source") if isinstance(src.get("source"), dict) else {}
    kind = _str(source_in.get("kind")).strip().lower()
    if kind not in ("event", "template_output", "value"):
        kind = "event"

    events = source_in.get("events")
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, list):
        events = base["source"]["events"] if kind == "event" and "events" not in source_in else []
    events = [e for e in (_str(e).strip() for e in events) if e]

    tout_in = source_in.get("template_output") if isinstance(source_in.get("template_output"), dict) else {}
    match_in = tout_in.get("match") if isinstance(tout_in.get("match"), dict) else {}
    value_in = source_in.get("value") if isinstance(source_in.get("value"), dict) else {}
    value_spec = copy.deepcopy(value_in) if value_in else copy.deepcopy(base["source"]["value"])
    value_spec.setdefault("kind", "data_source")
    value_spec.setdefault("source", "")

    refresh_on = source_in.get("refresh_on")
    if not isinstance(refresh_on, list):
        refresh_on = []
    refresh_on = [e for e in (_str(e).strip() for e in refresh_on) if e]

    mapping_in = src.get("mapping") if isinstance(src.get("mapping"), dict) else {}
    decimals = mapping_in.get("decimals")
    if decimals in (None, ""):
        decimals_out: Any = ""
    else:
        decimals_out = _int(decimals, 0, minimum=0)

    timing_in = src.get("timing") if isinstance(src.get("timing"), dict) else {}
    target_in = src.get("target") if isinstance(src.get("target"), dict) else {}
    valid_targets = target_ids_for(block_type) if block_type else []
    target_id = _str(target_in.get("id")).strip()
    if not target_id or (valid_targets and target_id not in valid_targets):
        target_id = DEFAULT_TARGET.get(block_type, "show")
    options = target_in.get("options") if isinstance(target_in.get("options"), dict) else {}

    return {
        "id": feed_id,
        "enabled": bool(src.get("enabled", True)),
        "label": _str(src.get("label")).strip(),
        "source": {
            "kind": kind,
            "events": events,
            "template_output": {
                "template": _str(tout_in.get("template")).strip(),
                "event": _str(tout_in.get("event")).strip(),
                "match": {str(k): v for k, v in match_in.items()},
            },
            "value": value_spec,
            "refresh_ms": _int(source_in.get("refresh_ms")),
            "refresh_on": refresh_on,
        },
        "filters": normalize_filters(src.get("filters")),
        "mapping": {
            "text_field": _str(mapping_in.get("text_field")).strip(),
            "value_field": _str(mapping_in.get("value_field")).strip(),
            "template": _str(mapping_in.get("template")),
            "split_field": _str(mapping_in.get("split_field")).strip(),
            "prefix": _str(mapping_in.get("prefix")),
            "suffix": _str(mapping_in.get("suffix")),
            "decimals": decimals_out,
            "fallback": _str(mapping_in.get("fallback")),
        },
        "timing": {"delay_ms": _int(timing_in.get("delay_ms"))},
        "target": {"id": target_id, "options": copy.deepcopy(options)},
    }


def migrate_legacy_feeds(element: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build ``data_feed`` rows from the pre-unification per-widget wiring.

    Returns an empty list when the element has nothing legacy to convert.
    """
    if not isinstance(element, dict):
        return []
    etype = str(element.get("type") or "").lower()
    props = element.get("props") if isinstance(element.get("props"), dict) else {}

    if etype == "list":
        source = _str(props.get("source") or "alerts")
        if source == "manual":
            return []
        feed = default_feed("list")
        mapping = feed["mapping"]
        mapping["text_field"] = _str(props.get("text_field"))
        mapping["value_field"] = _str(props.get("value_field"))
        mapping["template"] = _str(props.get("row_template") or "{username}")
        if source == "giveaway":
            feed["source"]["events"] = ["giveaway_entry", "giveaway_winner"]
            mapping["split_field"] = "winners"
        elif source == "payload":
            ev = _str(props.get("source_event")).strip()
            if not ev:
                return []
            feed["source"]["events"] = [ev]
        else:
            feed["source"]["events"] = list(_ALERT_DEFAULT_EVENTS)
            at = _str(props.get("alert_type") or "any").strip().lower()
            feed["filters"]["alert_type"] = at or "any"
        feed["target"] = {"id": "list_add", "options": {}}
        return [normalize_feed(feed, "list")]

    if etype == "wheel":
        if _str(props.get("source") or "static") != "giveaway":
            return []
        feed = default_feed("wheel")
        feed["source"]["events"] = ["giveaway_winner"]
        feed["target"] = {
            "id": "wheel_spin",
            "options": {"segments_field": "pool_entries", "land_field": "winners"},
        }
        return [normalize_feed(feed, "wheel")]

    if etype == "stat_card":
        spec = element.get("stat_source") if isinstance(element.get("stat_source"), dict) else None
        if not spec:
            return []
        feed = default_feed("stat_card")
        feed["source"] = {
            "kind": "value",
            "events": [],
            "value": copy.deepcopy(spec),
            "refresh_ms": 1000,
            "refresh_on": list(_ALERT_DEFAULT_EVENTS),
        }
        feed["target"] = {"id": "set_value", "options": {}}
        return [normalize_feed(feed, "stat_card")]

    if etype in ("text", "marquee") and (element.get("text_mode") or "static") == "data_display":
        cfg = element.get("data_display") if isinstance(element.get("data_display"), dict) else None
        if not cfg:
            return []
        spec = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
        feed = default_feed(etype)
        feed["source"] = {
            "kind": "value",
            "events": [],
            "value": copy.deepcopy(spec) or {"kind": "data_source", "source": ""},
            "refresh_ms": _int(cfg.get("poll_interval_ms")),
            "refresh_on": [e for e in (cfg.get("refresh_on") or []) if e],
        }
        feed["target"] = {"id": "set_text", "options": {}}
        return [normalize_feed(feed, etype)]

    if etype in ("poll", "now_playing"):
        return [normalize_feed(default_feed(etype), etype)]

    return []


_COUNTER_OP_TARGET = {
    "increment": "counter_increment",
    "decrement": "counter_decrement",
    "set": "counter_set",
    "reset": "counter_reset",
}


def _rule_to_feed(rule: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """One old counter rule, expressed as a Data feed. None when it cannot fire."""
    trigger = str(rule.get("trigger") or "event").strip().lower()
    op = str(rule.get("operation") or "increment").strip().lower()
    filters = normalize_filters(rule.get("filters") if isinstance(rule.get("filters"), dict) else {})
    legacy = rule.get("filter") if isinstance(rule.get("filter"), dict) else {}
    if legacy.get("alert_type"):
        filters["alert_type"] = str(legacy["alert_type"]).strip().lower() or "any"
    if legacy.get("tier") not in (None, ""):
        filters["tier"] = str(legacy["tier"])
    for key, value in legacy.items():
        if key in ("alert_type", "tier") or value in ("", None):
            continue
        filters["rules"].append({"field": str(key), "op": "eq", "value": str(value)})

    delta = rule.get("delta") if isinstance(rule.get("delta"), dict) else {"kind": "fixed", "value": 1}
    alert_filter = str(delta.get("alert_type_filter") or "").strip().lower()
    if alert_filter and alert_filter != "any":
        filters["alert_type"] = alert_filter
    tier_filter = str(delta.get("tier_filter") or "").strip()
    if tier_filter and tier_filter != "any":
        filters["tier"] = tier_filter

    events: List[str] = []
    if trigger == "streamdeck":
        action = str(rule.get("streamdeck_action") or "").strip()
        if not action:
            return None
        events = ["streamdeck_template_action"]
        filters["rules"].append({"field": "actionName", "op": "eq", "value": action})
    else:
        event = str(rule.get("event") or "").strip()
        if not event:
            return None
        events = [event]

    feed = default_feed("counter", f"rule{index + 1}")
    feed["enabled"] = True
    feed["source"]["kind"] = "event"
    feed["source"]["events"] = events
    feed["filters"] = filters
    feed["target"] = {
        "id": _COUNTER_OP_TARGET.get(op, "counter_increment"),
        "options": {"delta": copy.deepcopy(delta)},
    }
    return normalize_feed(feed, "counter", index)


def migrate_counter_rules(model: Dict[str, Any]) -> bool:
    """
    Turn ``counter.rules`` into Data feeds and clear the rule list.

    Saving after this keeps the same events, filters, and deltas without a
    separate rules editor.
    """
    if not isinstance(model, dict) or model.get("legacy"):
        return False
    changed = False
    for el in model.get("elements") or []:
        if not isinstance(el, dict):
            continue
        counter = el.get("counter")
        if not isinstance(counter, dict):
            continue
        rules = counter.get("rules")
        if not isinstance(rules, list) or not rules:
            continue
        etype = str(el.get("type") or "").lower()
        mode = str(el.get("text_mode") or "static").strip().lower()
        if etype != "counter" and not (
            etype in ("text", "marquee") and mode == "counter"
        ):
            continue
        feeds = [f for f in (el.get("data_feed") or []) if isinstance(f, dict)]
        used = {str(f.get("id") or "") for f in feeds}
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            feed = _rule_to_feed(rule, len(feeds))
            if feed is None:
                continue
            n = 1
            base = str(feed["id"])
            while feed["id"] in used:
                n += 1
                feed["id"] = f"{base}_{n}"
            used.add(feed["id"])
            feeds.append(feed)
        el["data_feed"] = feeds
        counter["rules"] = []
        changed = True
    if _migrate_display_sources(model):
        changed = True
    return changed


def _migrate_display_sources(model: Dict[str, Any]) -> bool:
    """A data display that still names a source, but has no feed, gets one."""
    changed = False
    for el in model.get("elements") or []:
        if not isinstance(el, dict):
            continue
        etype = str(el.get("type") or "").lower()
        mode = str(el.get("text_mode") or "static").strip().lower()
        if etype != "data_display" and not (
            etype in ("text", "marquee") and mode == "data_display"
        ):
            continue
        cfg = el.get("data_display") if isinstance(el.get("data_display"), dict) else None
        spec = cfg.get("source") if isinstance(cfg, dict) and isinstance(cfg.get("source"), dict) else None
        if not spec or not str(spec.get("source") or "").strip():
            continue
        feeds = [f for f in (el.get("data_feed") or []) if isinstance(f, dict)]
        if any((f.get("target") or {}).get("id") == "set_text" for f in feeds):
            continue
        block = "data_display" if etype == "data_display" else etype
        feed = default_feed(block, "display1")
        feed["source"] = {
            "kind": "value",
            "events": [],
            "template_output": {"template": "", "event": "", "match": {}},
            "value": copy.deepcopy(spec),
            "refresh_ms": _int(cfg.get("poll_interval_ms")) if isinstance(cfg, dict) else 0,
            "refresh_on": [e for e in ((cfg or {}).get("refresh_on") or []) if e] or list(_ALERT_DEFAULT_EVENTS),
        }
        feed["target"] = {"id": "set_text", "options": {}}
        feeds.append(normalize_feed(feed, block, len(feeds)))
        el["data_feed"] = feeds
        changed = True
    return changed


def effective_feeds(element: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    The normalised feeds for ``element``.

    An explicit ``data_feed`` list always wins (even when empty). Elements that
    predate the unified model fall back to a migration of their legacy props.
    """
    if not isinstance(element, dict):
        return []
    etype = str(element.get("type") or "").lower()
    if "data_feed" in element and isinstance(element.get("data_feed"), list):
        return [normalize_feed(f, etype, i) for i, f in enumerate(element["data_feed"])]
    return migrate_legacy_feeds(element)


def templates_needing_widget_resave() -> List[Dict[str, str]]:
    """
    Sidecars that still store a counter or data display as a text mode.

    These have not been saved since Counter and Data Display became their own
    widgets, so the generated overlay is still the old shape.
    """
    import glob

    from ..path_utils import get_template_path

    folder = os.path.join(get_template_path(), "_spore")
    found: List[Dict[str, str]] = []
    try:
        paths = sorted(glob.glob(os.path.join(folder, "*.spore.json")))
    except OSError:
        return found
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                model = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(model, dict):
            continue
        name = str(model.get("template_name") or os.path.basename(path).replace(".spore.json", ""))
        for el in model.get("elements") or []:
            if not isinstance(el, dict):
                continue
            mode = str(el.get("text_mode") or "").strip().lower()
            etype = str(el.get("type") or "").lower()
            counter = el.get("counter") if isinstance(el.get("counter"), dict) else {}
            has_rules = bool(counter.get("rules"))
            if etype in ("text", "marquee") and mode in ("counter", "data_display"):
                found.append({"template": name, "mode": mode})
                break
            if has_rules and (etype == "counter" or mode == "counter"):
                found.append({"template": name, "mode": "counter"})
                break
    return found


def migrate_legacy_element_types(model: Dict[str, Any]) -> bool:
    """
    Turn Text/Marquee counter and data-display modes into their own widgets.

    A scrolling marquee keeps scrolling via ``props.scroll``. ``text_mode`` is
    removed so a later save no longer looks like the old widget.
    """
    if not isinstance(model, dict) or model.get("legacy"):
        return False
    changed = False
    for el in model.get("elements") or []:
        if not isinstance(el, dict):
            continue
        etype = str(el.get("type") or "").lower()
        mode = str(el.get("text_mode") or "static").strip().lower()
        if etype not in ("text", "marquee") or mode not in ("counter", "data_display"):
            continue
        props = el.get("props") if isinstance(el.get("props"), dict) else {}
        el["props"] = props
        if etype == "marquee":
            props["scroll"] = True
            props.setdefault("speed", 8)
            props.setdefault("direction", "left")
            props.setdefault("gap", 48)
        el["type"] = "counter" if mode == "counter" else "data_display"
        el.pop("text_mode", None)
        changed = True
    return changed


def migrate_model_feeds(model: Dict[str, Any]) -> bool:
    """
    Give every non-legacy element an explicit ``data_feed`` list.

    Returns True when anything changed so callers can mark the model dirty.
    """
    if not isinstance(model, dict) or model.get("legacy"):
        return False
    changed = False
    for el in model.get("elements") or []:
        if not isinstance(el, dict) or "data_feed" in el:
            continue
        el["data_feed"] = migrate_legacy_feeds(el)
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def get_data_catalog() -> Dict[str, Any]:
    """Everything the editor's Data feed section needs, merged in one payload."""
    from . import data_source_registry, event_registry, template_outputs

    sources = data_source_registry.get_data_source_registry()
    events = event_registry.get_event_registry()
    outputs = template_outputs.list_template_outputs()
    return {
        "sources": sources.get("sources", []),
        "source_categories": sources.get("categories", []),
        "alert_types": sources.get("alert_types", []),
        "alert_field_types": dict(data_source_registry.ALERT_FIELD_TYPES),
        "session_stat_keys": sources.get("session_stat_keys", []),
        "events": events.get("events", []),
        "actions": events.get("actions", []),
        "template_outputs": outputs,
        "operators": [dict(o) for o in FILTER_OPERATORS],
        "formatters": [dict(f) for f in FORMATTERS],
        "targets": {bt: targets_for(bt) for bt in BLOCK_TYPES},
        "default_targets": dict(DEFAULT_TARGET),
        "default_source_events": {k: list(v) for k, v in DEFAULT_SOURCE_EVENTS.items()},
        "block_outputs": {bt: outputs_for_block(bt) for bt in BLOCK_OUTPUTS},
        "default_feed": {bt: default_feed(bt) for bt in BLOCK_TYPES},
    }

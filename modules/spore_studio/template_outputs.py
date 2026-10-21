#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Dynamic discovery of the "outputs" every template can publish.

An *output* is a Socket.IO event a template emits when something happens in it
(for example the roulette wheel's ``roulette_result``). Other templates, and
Spore Studio data feeds, can listen to those outputs.

Discovery is hybrid:

* every ``templates/*.html`` is scanned for ``socket.emit('<name>'`` calls, so a
  template needs no extra work to be listed;
* a template can add an ``outputs`` block to ``template_configs/<name>.json``
  to give an emitted event a label, a description and payload fields. Spore
  Studio writes this block automatically for its widgets.

Results are cached per file mtime so the scan costs nothing once warm.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EMIT_RE = re.compile(r"""\bsocket\.emit\(\s*(['"])([A-Za-z0-9_\-]+)\1""")
_EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_RUNTIME_BLOCK_RE = re.compile(
    r"// SPORE_STUDIO:data-runtime-begin[\s\S]*?// SPORE_STUDIO:data-runtime-end"
)

# Events the shared widget runtime emits for every template that includes it.
# They belong to a template only when that template declares the output (or
# its sidecar still has the widget). Scanning the injected runtime otherwise
# advertises, for example, wheel_result on the poll template.
GENERATED_WIDGET_EVENTS = frozenset(
    {
        "wheel_result",
        "poll_result",
        "timer_finished",
        "counter_changed",
        "goal_reached",
        "list_row_added",
        "list_cleared",
    }
)

# Events that are control-plane plumbing, not results a user would listen to.
_DENY_EXACT = frozenset(
    {
        "alert_playing",
        "alert_progress",
        "alert_complete",
        "pause_alerts",
        "resume_alerts",
        "toggle_alerts",
        "skip_alert",
        "clear_alert_queue",
        "refresh-alerts",
        "twitch-api-request",
        "template_log",
        "overlay_hello",
        "pause-loaded",
        "chat_banner_request",
        "source_controls_relay",
        "submit_template_stat",
        "game_hook_command",
        "connect",
        "disconnect",
        "response",
        "debug_psn_data",
        "meme-refresh",
        "ttimer-refresh",
        "bitbar-refresh",
        "template_output",
    }
)
_DENY_PREFIXES = (
    "get_",
    "get-",
    "set_",
    "set-",
    "update_",
    "streamdeck_",
    "mycelian_",
    "activity_feed_",
    "spotify_oauth",
)
_DENY_SUFFIXES = ("_request", "_request_state", "-request")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "outputs": []}


def is_relayable_event(name: Any) -> bool:
    """True when ``name`` is a safe, non-plumbing event name."""
    if not isinstance(name, str) or not _EVENT_NAME_RE.match(name):
        return False
    if name in _DENY_EXACT:
        return False
    if name.startswith(_DENY_PREFIXES) or name.endswith(_DENY_SUFFIXES):
        return False
    return True


def scan_html_emits(html_text: str) -> List[str]:
    """Event names emitted with a literal ``socket.emit('name', ...)``.

    The injected data-runtime block is stripped first. It ships with every
    Spore template and would otherwise look like that template emits
    ``wheel_result`` even when it has no wheel.
    """
    text = _RUNTIME_BLOCK_RE.sub("", html_text or "")
    seen: List[str] = []
    for match in _EMIT_RE.finditer(text):
        name = match.group(2)
        if is_relayable_event(name) and name not in seen:
            seen.append(name)
    return seen


def _templates_dir() -> str:
    from ..path_utils import get_template_path

    return get_template_path()


def _config_path(template_name: str) -> str:
    return os.path.join(_templates_dir(), "template_configs", f"{template_name}.json")


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _humanize(name: str) -> str:
    return re.sub(r"[_\-]+", " ", name).strip().capitalize()


def _normalize_payload(rows: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, str):
            row = {"key": row}
        if not isinstance(row, dict) or not row.get("key"):
            continue
        out.append(
            {
                "key": str(row["key"]),
                "label": str(row.get("label") or row["key"]),
                "type": str(row.get("type") or "string"),
            }
        )
    return out


def declared_outputs(template_name: str, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Entries from a config's ``outputs`` block (hand-written or generated)."""
    cfg = config if config is not None else _read_json(_config_path(template_name))
    block = cfg.get("outputs")
    rows: List[Dict[str, Any]] = []
    if isinstance(block, dict):
        items = list(block.items())
    elif isinstance(block, list):
        items = [(str(r.get("id") or r.get("event") or ""), r) for r in block if isinstance(r, dict)]
    else:
        items = []
    for oid, row in items:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event") or "").strip()
        if not event or not _EVENT_NAME_RE.match(event):
            continue
        match = row.get("match") if isinstance(row.get("match"), dict) else {}
        rows.append(
            {
                "id": f"{template_name}:{oid or event}",
                "template": template_name,
                "output_id": str(oid or event),
                "event": event,
                "label": str(row.get("label") or _humanize(event)),
                "description": str(row.get("description") or ""),
                "payload": _normalize_payload(row.get("payload")),
                "match": dict(match),
                "element_id": str(row.get("element_id") or ""),
                "element_type": str(row.get("element_type") or ""),
                "generated": bool(row.get("generated")),
                "origin": "declared",
            }
        )
    return rows


def _row_from_built(template_name: str, oid: str, row: Dict[str, Any], origin: str) -> Dict[str, Any]:
    match = row.get("match") if isinstance(row.get("match"), dict) else {}
    return {
        "id": f"{template_name}:{oid}",
        "template": template_name,
        "output_id": oid,
        "event": str(row.get("event") or ""),
        "label": str(row.get("label") or _humanize(str(row.get("event") or oid))),
        "description": str(row.get("description") or ""),
        "payload": _normalize_payload(row.get("payload")),
        "match": dict(match),
        "element_id": str(row.get("element_id") or ""),
        "element_type": str(row.get("element_type") or ""),
        "generated": True,
        "origin": origin,
    }


def sidecar_widget_outputs(template_name: str) -> List[Dict[str, Any]]:
    """Outputs implied by widgets still stored on the Spore sidecar."""
    try:
        from .data_feed_registry import build_widget_outputs
        from .template_parser_back import load_sidecar
    except Exception:  # pragma: no cover - import cycles must not break discovery
        return []
    model = load_sidecar(template_name)
    if not isinstance(model, dict):
        return []
    built = build_widget_outputs(model.get("elements") or [], template_name)
    return [
        _row_from_built(template_name, oid, row, "sidecar")
        for oid, row in built.items()
        if isinstance(row, dict)
    ]


def _scan_templates() -> List[Dict[str, Any]]:
    tdir = _templates_dir()
    outputs: List[Dict[str, Any]] = []
    try:
        names = sorted(f for f in os.listdir(tdir) if f.lower().endswith(".html"))
    except OSError:
        return outputs
    for fname in names:
        template_name = fname[:-5]
        try:
            with open(os.path.join(tdir, fname), "r", encoding="utf-8") as fh:
                emitted = scan_html_emits(fh.read())
        except OSError:
            continue
        declared = declared_outputs(template_name)
        known_ids = {row["id"] for row in declared}
        known_events = {row["event"] for row in declared}
        outputs.extend(declared)
        for row in sidecar_widget_outputs(template_name):
            if row["id"] in known_ids or row["event"] in known_events:
                continue
            known_ids.add(row["id"])
            known_events.add(row["event"])
            outputs.append(row)
        for event in emitted:
            if event in known_events or event in GENERATED_WIDGET_EVENTS:
                continue
            outputs.append(
                {
                    "id": f"{template_name}:{event}",
                    "template": template_name,
                    "output_id": event,
                    "event": event,
                    "label": _humanize(event),
                    "description": f"Emitted by the {template_name} template.",
                    "payload": [],
                    "match": {},
                    "element_id": "",
                    "element_type": "",
                    "generated": False,
                    "origin": "scanned",
                }
            )
    return outputs


def _signature() -> Tuple[Any, ...]:
    tdir = _templates_dir()
    parts: List[Tuple[str, float]] = []
    for sub in ("", "template_configs"):
        d = os.path.join(tdir, sub) if sub else tdir
        try:
            for fname in os.listdir(d):
                if fname.lower().endswith((".html", ".json")):
                    full = os.path.join(d, fname)
                    try:
                        parts.append((f"{sub}/{fname}", os.path.getmtime(full)))
                    except OSError:
                        continue
        except OSError:
            continue
    parts.sort()
    return (tdir, tuple(parts))


def invalidate_cache() -> None:
    """Drop the scan cache so the next read sees a just-saved template."""
    with _lock:
        _cache["key"] = None
        _cache["outputs"] = []


def list_template_outputs(refresh: bool = False) -> List[Dict[str, Any]]:
    """Every discovered template output (cached by file mtimes)."""
    with _lock:
        try:
            key = _signature()
        except Exception:  # pragma: no cover - defensive
            key = None
        if refresh or key is None or _cache["key"] != key:
            try:
                _cache["outputs"] = _scan_templates()
            except Exception:
                logger.exception("Template output scan failed")
                _cache["outputs"] = []
            _cache["key"] = key
        return [dict(row) for row in _cache["outputs"]]


def known_output_events() -> Dict[str, List[str]]:
    """Map event name -> template names that publish it."""
    table: Dict[str, List[str]] = {}
    for row in list_template_outputs():
        owners = table.setdefault(row["event"], [])
        if row["template"] not in owners:
            owners.append(row["template"])
    return table


def merge_into_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Add template outputs to a list of registry events.

    Events are keyed by name in the binding picker, so several outputs that
    share a name (two wheels) collapse into one entry whose payload fields are
    the union. The full per-output list stays in the data catalog.
    """
    by_name = {ev.get("event"): ev for ev in events}
    for row in list_template_outputs():
        name = row["event"]
        existing = by_name.get(name)
        if existing is not None and existing.get("origin") != "template":
            continue  # a curated core event wins
        if existing is None:
            entry = {
                "event": name,
                "label": f"{row['label']} ({row['template']})",
                "alert_system": "either",
                "description": row["description"],
                "payload": [dict(p) for p in row["payload"]],
                "origin": "template",
                "templates": [row["template"]],
            }
            by_name[name] = entry
            events = list(events) + [entry]
        else:
            if row["template"] not in existing["templates"]:
                existing["templates"].append(row["template"])
            keys = {p["key"] for p in existing["payload"]}
            for p in row["payload"]:
                if p["key"] not in keys:
                    existing["payload"].append(dict(p))
    return events


def resolve_emitter(event: str, payload: Any) -> str:
    """Best guess at which template emitted ``event`` (payload wins)."""
    if isinstance(payload, dict):
        tpl = payload.get("template")
        if isinstance(tpl, str) and tpl.strip():
            return tpl.strip()
    owners = known_output_events().get(event) or []
    if len(owners) == 1:
        return owners[0]
    return ""


_SAMPLE_BY_TYPE = {"string": "sample", "number": 1, "boolean": True, "array": ["one", "two"]}


def sample_envelope(event: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``("template_output", envelope)`` carrying a sample payload, or None."""
    for row in list_template_outputs():
        if row["event"] != event:
            continue
        data: Dict[str, Any] = {}
        for field in row["payload"]:
            data[field["key"]] = _SAMPLE_BY_TYPE.get(field.get("type", "string"), "sample")
        for key, value in (row.get("match") or {}).items():
            data[key] = value
        data.setdefault("template", row["template"])
        return "template_output", {"template": row["template"], "event": event, "data": data}
    return None

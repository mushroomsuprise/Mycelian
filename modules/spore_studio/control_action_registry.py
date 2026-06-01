#!/usr/bin/env python3
"""
Curated control actions for Spore Studio template dynamic_controls.

Each action defines how Source Controls / in-template controls dispatch
at runtime (handler kind + optional parameter schema).
"""

from __future__ import annotations

from typing import Any, Dict, List


def _act(
    action: str,
    label: str,
    *,
    handler: str,
    description: str = "",
    params: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    return {
        "action": action,
        "label": label,
        "handler": handler,
        "description": description,
        "params": params or [],
    }


_CONTROL_ACTIONS: List[Dict[str, Any]] = [
    _act("pause_alerts", "Pause alerts", handler="global_socket"),
    _act("resume_alerts", "Resume alerts", handler="global_socket"),
    _act("toggle_alerts", "Toggle alerts pause", handler="global_socket"),
    _act("skip_alert", "Skip current alert", handler="global_socket"),
    _act("clear_alert_queue", "Clear alert queue", handler="global_socket"),
    _act("refresh_alerts", "Refresh alerts", handler="global_socket"),
    _act(
        "counter_adjust",
        "Adjust counter",
        handler="counter_adjust",
        description="Increment, decrement, set, or reset a template counter.",
        params=[
            {"key": "target_counter_id", "label": "Counter id", "type": "string"},
            {
                "key": "operation",
                "label": "Operation",
                "type": "select",
                "options": ["increment", "decrement", "set", "reset"],
            },
        ],
    ),
    _act(
        "element_show",
        "Show element",
        handler="element_binding",
        params=[{"key": "element_id", "label": "Element id", "type": "string"}],
    ),
    _act(
        "element_hide",
        "Hide element",
        handler="element_binding",
        params=[{"key": "element_id", "label": "Element id", "type": "string"}],
    ),
    _act(
        "element_toggle",
        "Toggle element",
        handler="element_binding",
        params=[{"key": "element_id", "label": "Element id", "type": "string"}],
    ),
    _act(
        "set_config_value",
        "Set Source Settings value",
        handler="set_config_value",
        params=[
            {"key": "field_id", "label": "Config field id", "type": "string"},
            {"key": "value_key", "label": "Payload value key", "type": "string",
             "default": "value"},
        ],
    ),
    _act(
        "twitch_api_request",
        "Twitch API request",
        handler="twitch_api",
        params=[
            {"key": "endpoint", "label": "Endpoint URL", "type": "string"},
            {"key": "method", "label": "HTTP method", "type": "select",
             "options": ["GET", "POST", "PATCH", "PUT", "DELETE"]},
        ],
    ),
    _act(
        "websocket_emit",
        "Emit custom WebSocket event",
        handler="websocket_emit",
        params=[
            {"key": "event_name", "label": "Event name", "type": "string"},
            {"key": "payload_json", "label": "Payload JSON", "type": "text"},
        ],
    ),
    _act(
        "streamdeck_forward",
        "Forward Stream Deck action",
        handler="streamdeck_forward",
        params=[
            {"key": "action_name", "label": "Action name", "type": "string"},
        ],
    ),
    _act(
        "custom_template_action",
        "Custom template socket event",
        handler="template_socket",
        description="Emits {template_name}_{action} with optional data JSON.",
        params=[
            {"key": "action", "label": "Action suffix", "type": "string"},
            {"key": "payload_json", "label": "Payload JSON", "type": "text"},
        ],
    ),
]

_CONTROL_TYPES: List[Dict[str, Any]] = [
    {"type": "button", "label": "Button"},
    {"type": "toggle", "label": "Toggle"},
    {"type": "text_input", "label": "Text input"},
    {"type": "number_input", "label": "Number input"},
    {"type": "slider", "label": "Slider"},
    {"type": "select", "label": "Select"},
    {"type": "counter_control", "label": "Counter control"},
]


def get_control_action_registry() -> Dict[str, Any]:
    return {
        "actions": [dict(a) for a in _CONTROL_ACTIONS],
        "control_types": [dict(t) for t in _CONTROL_TYPES],
    }


def get_control_action(action_name: str) -> Dict[str, Any]:
    for row in _CONTROL_ACTIONS:
        if row.get("action") == action_name:
            return dict(row)
    return {}

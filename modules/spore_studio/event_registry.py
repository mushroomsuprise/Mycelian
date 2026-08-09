#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Curated registry of websocket events that Spore Studio templates can bind to.

The "binding picker" in the editor needs a stable, human-readable list of
events, the payload fields each event exposes, and the actions a bound
element can perform when the event fires. We intentionally do NOT
auto-discover events from ``modules/web_engine.py`` at runtime: most of the
events listed there are internal plumbing (chat backfill, statistics
ingest, Stream Deck control plane, etc.) and would only confuse a user
who is trying to drag-drop an alert overlay.

Add a new entry here when an event is genuinely useful to expose to
overlay authors. The shape is:

::

    {
        "event": "next_alert",
        "label": "Alert (queue)",
        "alert_system": "queue",
        "description": "Fired by the alert processor for queued alerts...",
        "payload": [
            {"key": "alert_type", "label": "Alert type", "type": "string",
             "examples": ["follow", "sub", "raid"]},
            ...
        ],
    }

The "actions" list is shared across events (everything an overlay can do
in response is the same — show, hide, set text, animate, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List


_EVENTS: List[Dict[str, Any]] = [
    {
        "event": "next_alert",
        "label": "Alert (queue)",
        "alert_system": "queue",
        "description": (
            "Fired by the alert processor for queued alerts (follows, subs, "
            "raids, donations, etc.). The main ``alerts.html`` overlay must "
            "emit ``alert_complete`` with a ``queue_seq`` matching the payload "
            "when finished; other queue-aware overlays must echo the same "
            "``queue_seq`` if they are the sole completion source. Otherwise the "
            "queue stalls."
        ),
        "payload": [
            {"key": "queue_seq", "label": "Queue completion id", "type": "number",
             "examples": ["1", "2", "3"]},
            {"key": "alert_type", "label": "Alert type", "type": "string",
             "examples": ["follow", "sub", "resub", "giftsub", "bit",
                          "donation", "raid", "hype_train", "point"]},
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message", "type": "string"},
            {"key": "amount", "label": "Amount", "type": "number"},
            {"key": "currency", "label": "Currency", "type": "string"},
            {"key": "tier", "label": "Sub tier", "type": "string",
             "examples": ["1000", "2000", "3000"]},
            {"key": "cumulative_months", "label": "Cumulative months",
             "type": "number"},
            {"key": "raider_count", "label": "Raider count", "type": "number"},
            {"key": "amt_cheered", "label": "Bits cheered", "type": "number"},
            {"key": "gif_dir", "label": "GIF directory", "type": "string"},
            {"key": "gif_name", "label": "GIF file name", "type": "string"},
            {"key": "hold_queue_only", "label": "Main overlay holds timing only",
             "type": "boolean"},
            {"key": "duration", "label": "Hold duration (seconds)",
             "type": "number"},
        ],
    },
    {
        "event": "instant_alert",
        "label": "Alert (instant)",
        "alert_system": "instant",
        "description": (
            "Fired by the alert processor for non-blocking alerts that do "
            "not participate in the queue handshake. Use this for sub bars, "
            "counters, and anything that updates without holding the queue."
        ),
        "payload": [
            {"key": "alert_type", "label": "Alert type", "type": "string"},
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message", "type": "string"},
            {"key": "amount", "label": "Amount", "type": "number"},
            {"key": "tier", "label": "Sub tier", "type": "string"},
            {"key": "quantity", "label": "Quantity", "type": "number"},
            {"key": "gift_qty", "label": "Gift sub quantity", "type": "number"},
        ],
    },
    {
        "event": "refresh-alerts",
        "label": "Refresh alerts",
        "alert_system": "either",
        "description": (
            "Broadcast when alert settings change. Bind to this if your "
            "overlay needs to reset state or reload assets."
        ),
        "payload": [],
    },
    {
        "event": "alerts_paused",
        "label": "Alerts paused",
        "alert_system": "either",
        "description": "Sent when the user pauses the alert system.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "alerts_resumed",
        "label": "Alerts resumed",
        "alert_system": "either",
        "description": "Sent when the user resumes the alert system.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "pause_status_update",
        "label": "Pause status update",
        "alert_system": "either",
        "description": "Sent on every pause/resume toggle and at startup.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "new-message",
        "label": "Chat message (Twitch)",
        "alert_system": "either",
        "description": (
            "Fired for every Twitch chat message ingested by Mycelian. "
            "Carries the full message payload — username, message body, "
            "user color, badges, emote map, and message type."
        ),
        "payload": [
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message text", "type": "string"},
            {"key": "color", "label": "Username color", "type": "string"},
            {"key": "badges", "label": "Badges (csv)", "type": "string"},
            {"key": "emotes", "label": "Emotes (string)", "type": "string"},
            {"key": "message_type", "label": "Message type", "type": "string",
             "examples": ["text", "action", "highlight"]},
            {"key": "userid", "label": "User ID", "type": "string"},
            {"key": "timestamp", "label": "Timestamp", "type": "number"},
        ],
    },
    {
        "event": "chat_add_message",
        "label": "Chat message (connector)",
        "alert_system": "either",
        "description": (
            "Connector-driven chat message — fired by the chat "
            "template's 'Add message' connector action (handy for "
            "bot replies, Stream Deck shoutouts, etc.)."
        ),
        "payload": [
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message_text", "label": "Message text", "type": "string"},
            {"key": "is_moderator", "label": "Moderator", "type": "boolean"},
        ],
    },
    {
        "event": "message_moderation",
        "label": "Chat moderation event",
        "alert_system": "either",
        "description": (
            "Sent when a moderator deletes a chat message or times out "
            "a user; chat overlays use this to fade or strikethrough "
            "the affected messages."
        ),
        "payload": [
            {"key": "action", "label": "Action", "type": "string",
             "examples": ["delete", "timeout", "ban"]},
            {"key": "user_id", "label": "User ID", "type": "string"},
            {"key": "message_id", "label": "Message ID", "type": "string"},
        ],
    },
    {
        "event": "twitch-api-response",
        "label": "Twitch API call (response)",
        "alert_system": "either",
        "description": (
            "Configure the Helix URL and optional query/body JSON in the inspector. "
            "The overlay emits ``twitch-api-request`` after connect using a binding id "
            "as ``requestId`` (also applied to response filters automatically). "
            "Add filters for fields on the socket payload — e.g. ``success`` with value "
            "``true`` (JSON)."
        ),
        "payload": [],
    },
]


_ACTIONS: List[Dict[str, Any]] = [
    {
        "action": "show",
        "label": "Show element",
        "description": "Make the element visible.",
        "args": [],
    },
    {
        "action": "hide",
        "label": "Hide element",
        "description": "Hide the element (sets data-spore-hidden=true).",
        "args": [],
    },
    {
        "action": "toggle",
        "label": "Toggle visibility",
        "description": (
            "Show the element if it is hidden, or hide it if it is visible "
            "(toggles data-spore-hidden)."
        ),
        "args": [],
    },
    {
        "action": "show_for",
        "label": "Show for N seconds",
        "description": "Make the element visible, then hide it after N seconds.",
        "args": [
            {"key": "seconds", "label": "Seconds", "type": "number",
             "default": 5, "min": 0.1, "max": 600},
            {"key": "anim_in", "label": "Entrance animation", "type": "select",
             "options": ["none", "fade", "slideIn", "scaleIn"],
             "default": "fade"},
            {"key": "anim_out", "label": "Exit animation", "type": "select",
             "options": ["none", "fade", "slideOut", "scaleOut"],
             "default": "fade"},
        ],
    },
    {
        "action": "set_text",
        "label": "Set text content",
        "description": (
            "Write a value from the event payload (or a literal string) into "
            "the element's text."
        ),
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": "", "description":
             "Payload key whose value is used (e.g. 'username'). "
             "Leave blank to use a literal."},
            {"key": "literal", "label": "Literal text", "type": "string",
             "default": "", "description":
             "Used when 'Payload field' is blank."},
        ],
    },
    {
        "action": "set_image",
        "label": "Set image source",
        "description": "Update an <img> element's src attribute.",
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": ""},
            {"key": "literal", "label": "Literal URL", "type": "string",
             "default": ""},
        ],
    },
    {
        "action": "play_audio",
        "label": "Play audio element",
        "description": (
            "Start playback on this element's audio (or nested <audio> "
            "inside a video bundle). Honors Spore Studio volume / fade-in."
        ),
        "args": [
            {"key": "volume", "label": "Volume override (0–1)", "type": "number",
             "default": "", "min": 0, "max": 1, "step": 0.05,
             "description": "Blank = element default"},
            {"key": "fade_in_ms", "label": "Fade-in override (ms)", "type": "number",
             "default": "", "min": 0, "max": 120000,
             "description": "Blank = element default"},
        ],
    },
    {
        "action": "set_visual_src",
        "label": "Set visual source (GIF/video)",
        "description": (
            "Update nested <img> or <video> source URL for a bundled video "
            "element (GIF swaps, etc.). Payload or literal."
        ),
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": ""},
            {"key": "literal", "label": "Literal URL", "type": "string",
             "default": ""},
        ],
    },
    {
        "action": "randomize_position",
        "label": "Randomize position within bounds",
        "description": (
            "Set left/top to random integers in pixel bounds. Omit x_max/y_max "
            "(or pass -1) to align with canvas edge minus element width/height."
        ),
        "args": [
            {"key": "x_min", "label": "X min (px)", "type": "number",
             "default": 0},
            {"key": "x_max", "label": "X max (px, -1=canvas)", "type": "number",
             "default": -1},
            {"key": "y_min", "label": "Y min (px)", "type": "number",
             "default": 0},
            {"key": "y_max", "label": "Y max (px, -1=canvas)", "type": "number",
             "default": -1},
        ],
    },
    {
        "action": "set_transform",
        "label": "Set CSS transform",
        "description": "Replace transform with translate / rotate / scale (blank args skipped).",
        "args": [
            {"key": "translate_x", "label": "Translate X (px)", "type": "number"},
            {"key": "translate_y", "label": "Translate Y (px)", "type": "number"},
            {"key": "rotate_deg", "label": "Rotate (degrees)", "type": "number"},
            {"key": "scale", "label": "Scale (uniform)", "type": "number"},
        ],
    },
    {
        "action": "transform_jitter",
        "label": "Random transform jitter",
        "description": (
            "Random translate ±range, rotate ±rotate_range°, and/or "
            "uniform scale in [scale_min, scale_max]."
        ),
        "args": [
            {"key": "rotate_range", "label": "Rotate range (degrees)", "type": "number",
             "default": 0},
            {"key": "translate_range", "label": "Translate range (px)", "type": "number",
             "default": 0},
            {"key": "scale_min", "label": "Scale min", "type": "number"},
            {"key": "scale_max", "label": "Scale max", "type": "number"},
        ],
    },
    {
        "action": "flash_class",
        "label": "Play CSS class animation",
        "description": (
            "Add a class briefly (restart if re-triggered). Try sporeShake or sporePop."
        ),
        "args": [
            {"key": "class_name", "label": "Class name", "type": "string",
             "default": "sporeShake"},
            {"key": "duration_ms", "label": "Hold (ms)", "type": "number",
             "default": 420, "min": 0, "max": 60000},
        ],
    },
    {
        "action": "counter_adjust",
        "label": "Adjust counter",
        "description": (
            "Increment, decrement, set, or reset a Spore Studio counter on this "
            "template (text element in Counter mode)."
        ),
        "args": [
            {"key": "counter_id", "label": "Counter id", "type": "string",
             "default": "", "description": "counter_id from the text element."},
            {"key": "operation", "label": "Operation", "type": "select",
             "options": ["increment", "decrement", "set", "reset"],
             "default": "increment"},
            {"key": "delta_kind", "label": "Delta kind", "type": "select",
             "options": ["fixed", "random_int", "random_float", "data_source"],
             "default": "fixed"},
            {"key": "delta_value", "label": "Fixed delta / fallback", "type": "number",
             "default": 1},
            {"key": "delta_source", "label": "Data source id", "type": "string",
             "default": "", "description": "When delta kind is data_source."},
            {"key": "delta_min", "label": "Random min", "type": "number", "default": 1},
            {"key": "delta_max", "label": "Random max", "type": "number", "default": 5},
        ],
    },
    {
        "action": "timer_start",
        "label": "Start timer",
        "description": "Start or resume a timer element's count up/down.",
        "args": [],
    },
    {
        "action": "timer_pause",
        "label": "Pause timer",
        "description": "Pause a timer element.",
        "args": [],
    },
    {
        "action": "timer_reset",
        "label": "Reset timer",
        "description": "Reset a timer element to zero and apply auto-start setting.",
        "args": [],
    },
]
def get_event_registry() -> Dict[str, Any]:
    """
    Return the full event + action registry as a JSON-serializable dict.

    Returns:
        A dict with two top-level keys: ``events`` (list of event
        descriptors) and ``actions`` (list of action descriptors).
    """
    return {
        "events": [dict(event) for event in _EVENTS],
        "actions": [dict(action) for action in _ACTIONS],
    }


def get_event(event_name: str) -> Dict[str, Any]:
    """Return a single event descriptor by name, or an empty dict if unknown."""
    for ev in _EVENTS:
        if ev.get("event") == event_name:
            return dict(ev)
    return {}


def get_action(action_name: str) -> Dict[str, Any]:
    """Return a single action descriptor by name, or an empty dict if unknown."""
    for act in _ACTIONS:
        if act.get("action") == action_name:
            return dict(act)
    return {}

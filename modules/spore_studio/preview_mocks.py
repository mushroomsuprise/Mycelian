#!/usr/bin/env python3
"""
One-shot mock payload factory for Spore Studio's preview iframe.

The Spore Studio preview dialog renders a row of "Emit mock <event>"
buttons sourced from :mod:`event_registry`. Clicking a button POSTs to
``/api/spore-studio/preview/emit`` and the server calls
:func:`build_mock_payload` here to produce a single
``(socket_event, payload)`` tuple to push out over Socket.IO to the
iframe's sid.

The payloads deliberately reuse the demo-data pools defined inside
:mod:`web_engine` (``_DEMO_USERNAMES``, ``_DEMO_CHAT_MESSAGES``,
``_DEMO_ALERT_PRESETS``, etc.) so that the manual buttons render
identical-shape events to what the (now removed) auto-demo loop used
to emit. Imports are lazy so this module stays import-cycle-safe.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


_FALLBACK_POOLS: Dict[str, Any] = {
    "usernames": ("PreviewUser",),
    "chat": ("Hello chat o/", "GG!", "Pog moment!"),
    "colors": ("#1E90FF", "#FF4500", "#9ACD32"),
    "alerts": (
        {"alert_type": "follow"},
        {"alert_type": "sub", "tier": "1000"},
        {"alert_type": "raid", "raider_count": 12},
    ),
    "titles": ("Just Chatting",),
}


def _demo_pools() -> Dict[str, Any]:
    """
    Lazily pull the demo-data tables out of :mod:`web_engine`. Falls
    back to ``_FALLBACK_POOLS`` if the import fails (e.g. running
    inside a stripped-down test environment without ``eventlet``).
    """
    try:
        from .. import web_engine as _we
    except Exception as e:  # pragma: no cover - prod always has the deps
        logger.debug("web_engine import skipped for mock pools: %s", e)
        return _FALLBACK_POOLS
    return {
        "usernames": getattr(_we, "_DEMO_USERNAMES", _FALLBACK_POOLS["usernames"]),
        "chat": getattr(_we, "_DEMO_CHAT_MESSAGES", _FALLBACK_POOLS["chat"]),
        "colors": getattr(_we, "_DEMO_CHAT_COLORS", _FALLBACK_POOLS["colors"]),
        "alerts": getattr(_we, "_DEMO_ALERT_PRESETS", _FALLBACK_POOLS["alerts"]),
        "titles": getattr(_we, "_DEMO_GAME_TITLES", _FALLBACK_POOLS["titles"]),
    }


def _alert_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    preset = dict(random.choice(pools["alerts"]))
    preset["username"] = random.choice(pools["usernames"])
    preset.setdefault("duration", 5)
    preset["timestamp"] = time.time()
    return preset


def _chat_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": random.choice(pools["usernames"]),
        "message": random.choice(pools["chat"]),
        "color": random.choice(pools["colors"]),
        "badges": None,
        "emotes": "",
        "fragments": None,
        "message_type": "text",
        "userid": str(random.randint(10_000_000, 99_999_999)),
        "type": "chat",
        "id": f"mock-{int(time.time() * 1000)}",
        "twmsgid": f"mock-{int(time.time() * 1000)}",
        "timestamp": time.time(),
    }


def _connector_chat_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": random.choice(pools["usernames"]),
        "message_text": random.choice(pools["chat"]),
        "is_moderator": random.random() < 0.2,
    }


def _moderation_payload() -> Dict[str, Any]:
    return {
        "action": random.choice(("delete", "timeout", "ban")),
        "user_id": str(random.randint(10_000_000, 99_999_999)),
        "message_id": f"mock-msg-{int(time.time() * 1000)}",
    }


def _twitch_api_response_payload() -> Dict[str, Any]:
    return {
        "success": True,
        "requestId": f"mock-{int(time.time() * 1000)}",
        "data": {
            "data": [
                {"id": str(random.randint(10_000_000, 99_999_999)),
                 "login": random.choice(("previewuser", "mockstreamer")),
                 "display_name": "PreviewStreamer"},
            ],
        },
    }


# Maps registry event_name -> (socket_event_to_emit, payload_builder).
# Most events emit on the same channel name as the registry key; the
# pause/resume trio map onto a single "pause_status_update" channel
# because that's what the templates actually listen for at runtime.
_BUILDERS: Dict[str, Tuple[str, Any]] = {
    "next_alert": (
        "next_alert",
        lambda pools: _alert_payload(pools),
    ),
    "instant_alert": (
        "instant_alert",
        lambda pools: _alert_payload(pools),
    ),
    "refresh-alerts": (
        "refresh-alerts",
        lambda pools: {},
    ),
    "alerts_paused": (
        "alerts_paused",
        lambda pools: {"paused": True},
    ),
    "alerts_resumed": (
        "alerts_resumed",
        lambda pools: {"paused": False},
    ),
    "pause_status_update": (
        "pause_status_update",
        lambda pools: {"paused": random.random() < 0.5},
    ),
    "new-message": (
        "new-message",
        lambda pools: _chat_payload(pools),
    ),
    "chat_add_message": (
        "chat_add_message",
        lambda pools: _connector_chat_payload(pools),
    ),
    "message_moderation": (
        "message_moderation",
        lambda pools: _moderation_payload(),
    ),
    "twitch-api-response": (
        "twitch-api-response",
        lambda pools: _twitch_api_response_payload(),
    ),
}


def build_mock_payload(
    event_name: str,
) -> Optional[Tuple[str, Any]]:
    """
    Return ``(socket_event, payload)`` for a one-shot mock emit, or
    ``None`` when ``event_name`` has no registered builder. Logged at
    debug level so a missing builder is easy to diagnose without
    spamming production logs.
    """
    spec = _BUILDERS.get(event_name)
    if spec is None:
        logger.debug("No mock builder registered for event %r", event_name)
        return None
    socket_event, builder = spec
    try:
        payload = builder(_demo_pools())
    except Exception as e:  # pragma: no cover - builder bugs surface in dev
        logger.warning(
            "Mock builder for %s raised %s: %s",
            event_name, type(e).__name__, e,
        )
        return None
    return socket_event, payload


def supported_events() -> Tuple[str, ...]:
    """Tuple of event names that have a mock builder. Useful for tests."""
    return tuple(_BUILDERS.keys())

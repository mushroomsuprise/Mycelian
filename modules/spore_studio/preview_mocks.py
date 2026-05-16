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
    msg = random.choice(pools["chat"])
    uname = random.choice(pools["usernames"])
    return {
        "username": uname,
        "message_text": msg,
        "message": msg,
        "text": msg,
        "is_moderator": random.random() < 0.2,
    }


def _next_alert_queue_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    preset: Dict[str, Any] = {}
    for _ in range(10):
        preset = _alert_payload(pools)
        if preset.get("alert_type") != "follow":
            break
    try:
        from .. import web_engine as _we

        preset["queue_seq"] = _we.assign_next_alert_queue_seq()
    except Exception:
        preset.setdefault("queue_seq", 1)
    return preset


def _activity_feed_alert_payload(pools: Dict[str, Any]) -> Any:
    from ..uiwindows.activity_feed import iter_activity_feed_preview_payloads

    payloads = list(iter_activity_feed_preview_payloads())
    return random.choice(payloads) if payloads else {"message": "Preview activity"}


def _giveaway_keywords() -> Tuple[str, ...]:
    return ("!join", "!enter", "!giveaway", "!raffle")


def _giveaway_start_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {"keyword": random.choice(_giveaway_keywords())}


def _giveaway_entry_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": random.choice(pools["usernames"]),
        "pool_size": 1,
    }


def _giveaway_winner_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    names = list(pools["usernames"])
    if len(names) < 2:
        names = list(names) + ["PreviewUser", "MockViewer", "SampleChat"]
    k = min(5, len(names))
    users = random.sample(names, k=k)
    winner = random.choice(users)
    return {
        "winners": [winner],
        "pool_entries": list(users),
        "pool_size": len(users),
    }


def _streamer_info_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {"streamer_name": "PreviewStreamer", "user_id": "0"}


def _twitch_category_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {"current_category": random.choice(pools["titles"])}


def _bitbar_add_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {"amount": random.randint(50, 400)}


def _subbar_instant_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    preset_type = random.choice(("sub", "sub", "giftsub", "resub"))
    payload: Dict[str, Any] = {
        "username": random.choice(pools["usernames"]),
        "alert_type": preset_type,
        "tier": "1000",
        "is_replay": False,
    }
    if preset_type == "giftsub":
        payload["quantity"] = random.randint(1, 5)
    elif preset_type == "resub":
        payload["months"] = random.randint(2, 24)
    return payload


def _counter_tick_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    return {"message": str(random.randint(0, 99))}


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


# Maps toolbar / registry event_name -> (socket_event_to_emit, payload_builder).
_BUILDERS: Dict[str, Tuple[str, Any]] = {
    "next_alert": (
        "next_alert",
        lambda pools: _next_alert_queue_payload(pools),
    ),
    "instant_alert": (
        "instant_alert",
        lambda pools: _alert_payload(pools),
    ),
    "alerts_play_alert": (
        "alerts_play_alert",
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
    "pause_status_on": (
        "pause_status_update",
        lambda pools: {"paused": True},
    ),
    "pause_status_off": (
        "pause_status_update",
        lambda pools: {"paused": False},
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
    "activity_feed_alert": (
        "activity_feed_alert",
        lambda pools: _activity_feed_alert_payload(pools),
    ),
    "giveaway_start": (
        "giveaway_start",
        lambda pools: _giveaway_start_payload(pools),
    ),
    "giveaway_entry": (
        "giveaway_entry",
        lambda pools: _giveaway_entry_payload(pools),
    ),
    "giveaway_stop": (
        "giveaway_stop",
        lambda pools: {},
    ),
    "giveaway_winner": (
        "giveaway_winner",
        lambda pools: _giveaway_winner_payload(pools),
    ),
    "giveaway_clear": (
        "giveaway_clear",
        lambda pools: {},
    ),
    "streamer-info": (
        "streamer-info",
        lambda pools: _streamer_info_payload(pools),
    ),
    "twitch_data_update": (
        "twitch_data_update",
        lambda pools: _twitch_category_payload(pools),
    ),
    "bitbar_add_bits": (
        "bitbar_add_bits",
        lambda pools: _bitbar_add_payload(pools),
    ),
    "bitbar_reset": (
        "bitbar_reset",
        lambda pools: {},
    ),
    "subbar_instant_alert": (
        "instant_alert",
        lambda pools: _subbar_instant_payload(pools),
    ),
    "subbar_reset": (
        "subbar_reset",
        lambda pools: {},
    ),
    "counter_message": (
        "counter_message",
        lambda pools: _counter_tick_payload(pools),
    ),
    "roulette_spin": (
        "roulette_spin",
        lambda pools: {},
    ),
    "roulette_refresh": (
        "roulette_refresh",
        lambda pools: {},
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

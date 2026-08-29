#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
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
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_preview_queue_seq = 0
_preview_queue_seq_lock = threading.Lock()


def _assign_preview_alert_queue_seq() -> int:
    """Preview-only sequence; never touches live EXPECTED_ALERT_COMPLETE_SEQ."""
    global _preview_queue_seq
    with _preview_queue_seq_lock:
        _preview_queue_seq -= 1
        return _preview_queue_seq

# Canonical alert types for per-type preview toolbar buttons.
_PREVIEW_ALERT_TYPES: Tuple[Tuple[str, str], ...] = (
    ("follow", "Follow"),
    ("sub", "Sub"),
    ("resub", "Resub"),
    ("giftsub", "Gift sub"),
    ("bit", "Bits"),
    ("raid", "Raid"),
    ("donation", "Donation"),
    ("point", "Points"),
    ("streak", "Streak"),
    ("hype_train", "Hype train"),
)

_ALERT_SOCKET_EVENTS = frozenset({"next_alert", "instant_alert"})
_ACTIVITY_FEED_SOCKET_EVENTS = frozenset({"activity_feed_alert"})


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
    uname = random.choice(pools["usernames"])
    parent = random.choice(pools["usernames"])
    if random.random() < 0.2:
        message = "/me waves to chat"
    elif random.random() < 0.15:
        message = "/notacommand hello"
    else:
        message = random.choice(pools["chat"])
    payload: Dict[str, Any] = {
        "username": uname,
        "message": message,
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
    if random.random() < 0.25:
        payload["reply"] = {
            "parent_message_id": f"mock-parent-{int(time.time())}",
            "parent_message_body": f"@{parent} " + random.choice(pools["chat"]),
            "parent_user_name": parent,
            "parent_user_login": parent.lower(),
            "thread_message_id": f"mock-thread-{int(time.time())}",
        }
    return payload


def _chat_reply_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    """Twitch-style reply: parent context in header, @mention stripped from body."""
    uname = random.choice(pools["usernames"])
    parent_login = "utbsb"
    parent_display = "utbsb"
    msg_id = f"mock-{int(time.time() * 1000)}"
    return {
        "username": uname,
        "message": f"@{parent_login} oh yeah",
        "color": random.choice(pools["colors"]),
        "badges": None,
        "emotes": "",
        "fragments": None,
        "message_type": "text",
        "userid": str(random.randint(10_000_000, 99_999_999)),
        "type": "chat",
        "id": msg_id,
        "twmsgid": msg_id,
        "timestamp": time.time(),
        "reply": {
            "parent_message_id": f"mock-parent-{int(time.time())}",
            "parent_message_body": "ready for hades II solid?",
            "parent_user_name": parent_display,
            "parent_user_login": parent_login,
            "thread_message_id": f"mock-thread-{int(time.time())}",
        },
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


def _random_tier() -> str:
    return random.choice(("1000", "2000", "3000"))


def _typed_alert_fields(alert_type: str) -> Dict[str, Any]:
    """Type-specific fields for a mock alert payload (no username/timestamp)."""
    at = (alert_type or "follow").strip().lower()
    if at == "follow":
        return {}
    if at == "sub":
        return {"tier": _random_tier()}
    if at == "resub":
        return {
            "tier": _random_tier(),
            "cumulative_months": random.randint(2, 48),
            "message": random.choice(
                ("Thanks for sticking around!", "Month streak!", "Still here!")
            ),
        }
    if at == "giftsub":
        return {"tier": _random_tier(), "gift_qty": random.randint(1, 10)}
    if at == "bit":
        return {
            "amt_cheered": random.randint(1, 10_000),
            "message": random.choice(
                ("Cheer preview!", "PogChamp", "Take my bits!")
            ),
        }
    if at == "raid":
        return {"raider_count": random.randint(2, 200)}
    if at == "donation":
        return {
            "amount": round(random.uniform(1.0, 100.0), 2),
            "currency": random.choice(("USD", "EUR", "GBP")),
            "message": "Thanks for the support!",
        }
    if at == "point":
        return {
            "alert_name": random.choice(
                ("Hydrate", "Sound alert", "Highlight message")
            ),
            "message": "Channel point redemption preview",
        }
    if at == "streak":
        return {
            "streak_count": random.randint(2, 12),
            "channel_points_awarded": random.randint(0, 500),
        }
    if at == "hype_train":
        return {
            "level": random.randint(1, 5),
            "total": random.randint(10, 500),
        }
    return {}


def build_typed_alert_payload(
    alert_system: str,
    alert_type: str,
    pools: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a single alert mock with a fixed ``alert_type`` and realistic fields.
    """
    pools = pools if pools is not None else _demo_pools()
    at = (alert_type or "follow").strip().lower()
    payload: Dict[str, Any] = {
        "alert_type": at,
        "username": random.choice(pools["usernames"]),
        "duration": 5,
        "timestamp": time.time(),
    }
    payload.update(_typed_alert_fields(at))
    if str(alert_system or "queue").strip().lower() == "queue":
        payload["queue_seq"] = _assign_preview_alert_queue_seq()
    return payload


def _next_alert_queue_payload(pools: Dict[str, Any]) -> Dict[str, Any]:
    preset: Dict[str, Any] = {}
    for _ in range(10):
        preset = _alert_payload(pools)
        if preset.get("alert_type") != "follow":
            break
    preset["queue_seq"] = _assign_preview_alert_queue_seq()
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
    return {
        "streamer_name": "PreviewStreamer",
        "user_id": "0",
        "channel_points_icon": {
            "url_1x": "https://static-cdn.jtvnw.net/custom-reward-images/default-1.png",
            "url_2x": "https://static-cdn.jtvnw.net/custom-reward-images/default-2.png",
            "url_4x": "https://static-cdn.jtvnw.net/custom-reward-images/default-4.png",
        },
    }


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
    *,
    alert_type: Optional[str] = None,
) -> Optional[Tuple[str, Any]]:
    """
    Return ``(socket_event, payload)`` for a one-shot mock emit, or
    ``None`` when ``event_name`` has no registered builder. Logged at
    debug level so a missing builder is easy to diagnose without
    spamming production logs.

    When ``alert_type`` is set and ``event_name`` is ``next_alert`` or
    ``instant_alert``, emits a typed alert mock instead of a random preset.
    """
    pools = _demo_pools()
    ev = str(event_name or "").strip()
    if alert_type and ev in _ACTIVITY_FEED_SOCKET_EVENTS:
        try:
            from ..uiwindows.activity_feed import (
                build_typed_activity_feed_preview_payload,
            )

            body = build_typed_activity_feed_preview_payload(alert_type)
            return ev, body
        except Exception as e:  # pragma: no cover
            logger.warning(
                "Typed activity feed mock for %s/%s raised %s: %s",
                ev,
                alert_type,
                type(e).__name__,
                e,
            )
            return None
    if alert_type == "reply" and ev == "new-message":
        try:
            return ev, _chat_reply_payload(pools)
        except Exception as e:  # pragma: no cover
            logger.warning(
                "Reply chat mock raised %s: %s", type(e).__name__, e
            )
            return None
    if alert_type and ev in _ALERT_SOCKET_EVENTS:
        try:
            system = "queue" if ev == "next_alert" else "instant"
            body = build_typed_alert_payload(system, alert_type, pools)
            return ev, body
        except Exception as e:  # pragma: no cover
            logger.warning(
                "Typed alert mock for %s/%s raised %s: %s",
                ev, alert_type, type(e).__name__, e,
            )
            return None
    spec = _BUILDERS.get(ev)
    if spec is None:
        logger.debug("No mock builder registered for event %r", ev)
        return None
    socket_event, builder = spec
    try:
        payload = builder(pools)
    except Exception as e:  # pragma: no cover - builder bugs surface in dev
        logger.warning(
            "Mock builder for %s raised %s: %s",
            ev, type(e).__name__, e,
        )
        return None
    return socket_event, payload


def _collect_bound_events(model: Dict[str, Any]) -> List[str]:
    """Unique socket event names referenced by bindings, counters, refresh_on."""
    found: List[str] = []
    seen: set[str] = set()

    def add(ev: Any) -> None:
        if not ev or not isinstance(ev, str):
            return
        name = ev.strip()
        if not name or name in seen:
            return
        seen.add(name)
        found.append(name)

    for element in model.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for binding in element.get("bindings") or []:
            if isinstance(binding, dict):
                add(binding.get("event"))
        counter = element.get("counter")
        if isinstance(counter, dict):
            for rule in counter.get("rules") or []:
                if isinstance(rule, dict):
                    add(rule.get("event"))
        dd = element.get("data_display")
        if isinstance(dd, dict):
            for ev in dd.get("refresh_on") or []:
                add(ev)
    return found


def _registry_event_label(event_name: str) -> str:
    try:
        from . import event_registry as _er

        row = _er.get_event(event_name)
        if row.get("label"):
            return str(row["label"])
    except Exception:
        pass
    return event_name.replace("_", " ").replace("-", " ").title()


def derive_preview_mocks(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build the ``preview_mocks`` list persisted in ``template_configs/*.json``
    and shown in Spore Studio / Custom Sources preview toolbars.
    """
    if not isinstance(model, dict):
        return []
    alert_system = str(model.get("alert_system") or "queue").strip().lower()
    if alert_system not in ("queue", "instant"):
        alert_system = "queue"
    primary = "next_alert" if alert_system == "queue" else "instant_alert"
    generic_label = (
        "Random queue alert" if primary == "next_alert" else "Random instant alert"
    )

    actions: List[Dict[str, Any]] = [
        {"event": primary, "label": generic_label},
    ]
    for at_key, at_label in _PREVIEW_ALERT_TYPES:
        actions.append(
            {
                "event": primary,
                "label": at_label,
                "alert_type": at_key,
            }
        )

    bound = _collect_bound_events(model)
    for ev in bound:
        if ev in _ALERT_SOCKET_EVENTS:
            continue
        if ev == "subbar_instant_alert":
            continue
        actions.append(
            {
                "event": ev,
                "label": _registry_event_label(ev),
            }
        )
    return actions


def supported_events() -> Tuple[str, ...]:
    """Tuple of event names that have a mock builder. Useful for tests."""
    return tuple(_BUILDERS.keys())

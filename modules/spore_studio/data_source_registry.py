#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Curated data sources for Spore Studio counters and data displays.

Each source has a stable ``id`` (used in editor models and compiled JS),
a human label, value type hint, and category for the picker UI.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _src(
    sid: str,
    label: str,
    *,
    category: str,
    value_type: str = "number",
    description: str = "",
    delta_only: bool = False,
    display_only: bool = False,
) -> Dict[str, Any]:
    return {
        "id": sid,
        "label": label,
        "category": category,
        "value_type": value_type,
        "description": description,
        "delta_only": delta_only,
        "display_only": display_only,
    }


_DATA_SOURCES: List[Dict[str, Any]] = [
    # Delta-only
    _src("fixed", "Fixed value", category="Delta", value_type="number", delta_only=True),
    _src(
        "random_int",
        "Random integer",
        category="Delta",
        value_type="number",
        delta_only=True,
        description="Inclusive min/max.",
    ),
    _src(
        "random_float",
        "Random float",
        category="Delta",
        value_type="number",
        delta_only=True,
        description="Inclusive min/max with optional decimal places.",
    ),
    # Alert payload
    _src("alert.amount", "Alert amount", category="Alert payload"),
    _src("alert.quantity", "Alert quantity", category="Alert payload"),
    _src("alert.tier", "Sub tier", category="Alert payload", value_type="string"),
    _src("alert.amt_cheered", "Bits cheered", category="Alert payload"),
    _src("alert.cumulative_months", "Cumulative months", category="Alert payload"),
    _src("alert.raider_count", "Raider count", category="Alert payload"),
    _src("alert.username", "Username", category="Alert payload", value_type="string"),
    _src("alert.message", "Message", category="Alert payload", value_type="string"),
    _src("alert.alert_type", "Alert type", category="Alert payload", value_type="string"),
    _src("alert.currency", "Currency", category="Alert payload", value_type="string"),
    _src("alert.queue_seq", "Queue sequence", category="Alert payload"),
    # Chat
    _src("chat.username", "Chat username", category="Chat", value_type="string"),
    _src("chat.message", "Chat message", category="Chat", value_type="string"),
    _src("chat.message_length", "Chat message length", category="Chat"),
    _src("chat.userid", "Chat user ID", category="Chat", value_type="string"),
    _src("chat.badges", "Chat badges", category="Chat", value_type="string"),
    _src("chat.color", "Chat color", category="Chat", value_type="string"),
    _src("chat.message_text", "Connector message text", category="Chat", value_type="string"),
    # Alert system
    _src(
        "alerts.paused",
        "Alerts paused",
        category="Alert system",
        value_type="boolean",
    ),
    # Session / stats (via get_data statistics/session)
    _src("stats.total_gift_subs", "Total gift subs (session)", category="Session stats"),
    _src("stats.total_bits", "Total bits (session)", category="Session stats"),
    _src("stats.follows", "Follows (session)", category="Session stats"),
    _src("stats.subs", "Subs (session)", category="Session stats"),
    _src("stats.raids", "Raids (session)", category="Session stats"),
    _src("stats.cheers", "Cheers (session)", category="Session stats"),
    # Chatbot config mirrors
    _src("chatbot.gift_sub_quantity", "Gift sub quantity", category="Chatbot"),
    _src("chatbot.gift_sub_tier", "Gift sub tier", category="Chatbot"),
    _src("chatbot.raid_viewer_count", "Raid viewer count", category="Chatbot"),
    # Template / config (resolved at runtime)
    _src(
        "counter.{id}",
        "Another counter in this template",
        category="Template",
        description="Use counter.{counter_id} in the model.",
    ),
    _src(
        "config.{id}",
        "Source Settings field",
        category="Template",
        value_type="string",
        description="Reads exposed config value by field id.",
    ),
    _src(
        "runtime.{path}.{key}",
        "Runtime database field",
        category="Runtime database",
        value_type="string",
        description="User-defined path and key under database_manager.",
    ),
    _src(
        "twitch.{binding_id}.{path}",
        "Twitch API response field",
        category="Twitch API",
        value_type="string",
        description="Dot path into last twitch-api-response for a binding id.",
    ),
]

# Payload key mapping for alert.* sources
ALERT_PAYLOAD_KEYS: Dict[str, str] = {
    "alert.amount": "amount",
    "alert.quantity": "quantity",
    "alert.tier": "tier",
    "alert.amt_cheered": "amt_cheered",
    "alert.cumulative_months": "cumulative_months",
    "alert.raider_count": "raider_count",
    "alert.username": "username",
    "alert.message": "message",
    "alert.alert_type": "alert_type",
    "alert.currency": "currency",
    "alert.queue_seq": "queue_seq",
}

CHAT_PAYLOAD_KEYS: Dict[str, str] = {
    "chat.username": "username",
    "chat.message": "message",
    "chat.userid": "userid",
    "chat.badges": "badges",
    "chat.color": "color",
    "chat.message_text": "message_text",
}


def get_data_source_registry() -> Dict[str, Any]:
    """Return JSON-serializable registry for the Spore Studio editor."""
    categories: List[str] = []
    for row in _DATA_SOURCES:
        cat = row.get("category") or "Other"
        if cat not in categories:
            categories.append(cat)
    return {
        "sources": [dict(s) for s in _DATA_SOURCES],
        "categories": categories,
        "alert_payload_keys": dict(ALERT_PAYLOAD_KEYS),
        "chat_payload_keys": dict(CHAT_PAYLOAD_KEYS),
    }


def get_data_source(source_id: str) -> Dict[str, Any]:
    for row in _DATA_SOURCES:
        if row.get("id") == source_id:
            return dict(row)
    return {}

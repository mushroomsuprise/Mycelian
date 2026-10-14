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


# Alert types the counter-rule editor offers when a source reads an alert payload.
ALERT_TYPES: List[Dict[str, str]] = [
    {"id": "follow", "label": "Follow"},
    {"id": "sub", "label": "New sub"},
    {"id": "resub", "label": "Resub"},
    {"id": "giftsub", "label": "Gift sub"},
    {"id": "bit", "label": "Bits"},
    {"id": "donation", "label": "Donation"},
    {"id": "raid", "label": "Raid"},
    {"id": "hype_train", "label": "Hype train"},
    {"id": "point", "label": "Channel points"},
    {"id": "streak", "label": "Streak"},
]

# Which alert types actually carry each alert payload key. Keys that are absent
# are present on every alert type. The editor uses this so only relevant
# filters are offered for a given source or event.
ALERT_FIELD_TYPES: Dict[str, List[str]] = {
    "tier": ["sub", "resub", "giftsub"],
    "cumulative_months": ["resub"],
    "gift_qty": ["giftsub"],
    "recipient": ["giftsub"],
    "raider_count": ["raid"],
    "amt_cheered": ["bit"],
    "amount": ["donation"],
    "currency": ["donation"],
    "alert_name": ["point"],
    "streak_count": ["streak"],
    "channel_points_awarded": ["streak"],
    "level": ["hype_train"],
    "total": ["hype_train"],
}

# Keys returned by the statistics/session get_data snapshot.
SESSION_STAT_KEYS: List[Dict[str, str]] = [
    {"id": "total_gift_subs", "label": "Total gift subs"},
    {"id": "total_bits", "label": "Total bits"},
    {"id": "follows", "label": "Follows"},
    {"id": "subs", "label": "Subs"},
    {"id": "raids", "label": "Raids"},
    {"id": "cheers", "label": "Cheers"},
]


_DATA_SOURCES: List[Dict[str, Any]] = [
    # Alert payload. String fields are display-only so they are not offered as
    # counter deltas (a username cannot be added to a number).
    _src(
        "alert.amount",
        "Donation amount",
        category="Alert payload",
        description="Numeric amount on donation alerts.",
    ),
    _src(
        "alert.quantity",
        "Alert quantity",
        category="Alert payload",
        description="Generic quantity. Gift subs also expose alert.gift_qty.",
    ),
    _src(
        "alert.gift_qty",
        "Gift sub quantity",
        category="Alert payload",
        description="Number of subs gifted (giftsub alerts).",
    ),
    _src("alert.amt_cheered", "Bits cheered", category="Alert payload"),
    _src("alert.cumulative_months", "Cumulative months", category="Alert payload"),
    _src("alert.raider_count", "Raider count", category="Alert payload"),
    _src("alert.streak_count", "Watch streak", category="Alert payload"),
    _src(
        "alert.channel_points_awarded",
        "Channel points awarded",
        category="Alert payload",
        description="Points granted with a watch-streak alert.",
    ),
    _src(
        "alert.level",
        "Hype train level",
        category="Alert payload",
        description="Current hype train level.",
    ),
    _src(
        "alert.total",
        "Hype train total",
        category="Alert payload",
        description="Hype train progress total.",
    ),
    _src("alert.duration", "Alert duration (seconds)", category="Alert payload"),
    _src("alert.timestamp", "Alert timestamp", category="Alert payload"),
    _src("alert.queue_seq", "Queue sequence", category="Alert payload"),
    _src(
        "alert.tier",
        "Sub tier",
        category="Alert payload",
        value_type="string",
        display_only=True,
    ),
    _src(
        "alert.username",
        "Username",
        category="Alert payload",
        value_type="string",
        display_only=True,
    ),
    _src(
        "alert.message",
        "Message",
        category="Alert payload",
        value_type="string",
        display_only=True,
    ),
    _src(
        "alert.alert_type",
        "Alert type",
        category="Alert payload",
        value_type="string",
        display_only=True,
        description="follow, sub, resub, giftsub, bit, donation, raid, and so on.",
    ),
    _src(
        "alert.alert_name",
        "Point reward name",
        category="Alert payload",
        value_type="string",
        display_only=True,
        description="Channel point reward title.",
    ),
    _src(
        "alert.recipient",
        "Gift recipient",
        category="Alert payload",
        value_type="string",
        display_only=True,
    ),
    _src(
        "alert.currency",
        "Currency",
        category="Alert payload",
        value_type="string",
        display_only=True,
    ),
    # Subscriptions (counter deltas — tier and alert-type filters in the editor)
    _src(
        "sub.new_sub",
        "New sub (+1)",
        category="Subscriptions",
        value_type="number",
        delta_only=True,
        description=(
            "Returns 1 for a new subscription alert, else the fallback (usually 0). "
            "Optionally restrict to a sub tier and alert type."
        ),
    ),
    _src(
        "sub.resub",
        "Resub (+1)",
        category="Subscriptions",
        value_type="number",
        delta_only=True,
        description=(
            "Returns 1 for a resubscription alert, else the fallback (usually 0). "
            "Optionally restrict to a sub tier and alert type."
        ),
    ),
    _src(
        "sub.gift_sub",
        "Gift sub (+quantity)",
        category="Subscriptions",
        value_type="number",
        delta_only=True,
        description=(
            "Returns the gifted sub quantity for a giftsub alert, else the fallback "
            "(usually 0). Optionally restrict to a sub tier and alert type."
        ),
    ),
    # Chat (Twitch new-message). Connector text is a separate event, not a duplicate.
    _src(
        "chat.username",
        "Chat username",
        category="Chat",
        value_type="string",
        display_only=True,
    ),
    _src(
        "chat.message",
        "Chat message",
        category="Chat",
        value_type="string",
        display_only=True,
    ),
    _src("chat.message_length", "Chat message length", category="Chat"),
    _src(
        "chat.userid",
        "Chat user ID",
        category="Chat",
        value_type="string",
        display_only=True,
    ),
    _src(
        "chat.badges",
        "Chat badges",
        category="Chat",
        value_type="string",
        display_only=True,
    ),
    _src(
        "chat.color",
        "Chat color",
        category="Chat",
        value_type="string",
        display_only=True,
    ),
    _src(
        "chat.message_text",
        "Connector message text",
        category="Connector chat",
        value_type="string",
        display_only=True,
        description="message_text on chat_add_message (connector), not Twitch chat.",
    ),
    # Alert system
    _src(
        "alerts.paused",
        "Alerts paused",
        category="Alert system",
        value_type="boolean",
        display_only=True,
    ),
    # One session-stat source; the editor asks which key (statistics/session).
    _src(
        "stats",
        "Session statistic",
        category="Session stats",
        description=(
            "A key from the statistics/session snapshot. Pick the key in the "
            "field under this source."
        ),
    ),
    # Template / config. The editor collects the id, path, or binding — these
    # ids are not literal braces.
    _src(
        "counter",
        "Another counter in this template",
        category="Template",
        description="Value of another counter. Pick the counter in the next field.",
    ),
    _src(
        "config",
        "Source Settings field",
        category="Template",
        value_type="string",
        description="Reads an exposed config value. Pick the field id in the next field.",
    ),
    _src(
        "runtime",
        "Runtime database field",
        category="Runtime database",
        value_type="string",
        description="User-defined database path and key.",
    ),
    _src(
        "twitch",
        "Twitch API response field",
        category="Twitch API",
        value_type="string",
        description="Dot path into the last twitch-api-response for a binding.",
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
    "alert.gift_qty": "gift_qty",
    "alert.recipient": "recipient",
    "alert.alert_name": "alert_name",
    "alert.streak_count": "streak_count",
    "alert.channel_points_awarded": "channel_points_awarded",
    "alert.level": "level",
    "alert.total": "total",
    "alert.duration": "duration",
    "alert.timestamp": "timestamp",
}

CHAT_PAYLOAD_KEYS: Dict[str, str] = {
    "chat.username": "username",
    "chat.message": "message",
    "chat.userid": "userid",
    "chat.badges": "badges",
    "chat.color": "color",
    "chat.message_text": "message_text",
    "chat.message_length": "message",
}


def _annotated_sources() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for src in _DATA_SOURCES:
        row = dict(src)
        key = ALERT_PAYLOAD_KEYS.get(str(row.get("id") or ""))
        if key and key in ALERT_FIELD_TYPES:
            row["alert_types"] = list(ALERT_FIELD_TYPES[key])
        rows.append(row)
    return rows


def get_data_source_registry() -> Dict[str, Any]:
    """Return JSON-serializable registry for the Spore Studio editor."""
    categories: List[str] = []
    for row in _DATA_SOURCES:
        cat = row.get("category") or "Other"
        if cat not in categories:
            categories.append(cat)
    return {
        "sources": _annotated_sources(),
        "categories": categories,
        "alert_payload_keys": dict(ALERT_PAYLOAD_KEYS),
        "chat_payload_keys": dict(CHAT_PAYLOAD_KEYS),
        "alert_types": [dict(row) for row in ALERT_TYPES],
        "session_stat_keys": [dict(row) for row in SESSION_STAT_KEYS],
    }


def get_data_source(source_id: str) -> Dict[str, Any]:
    for row in _DATA_SOURCES:
        if row.get("id") == source_id:
            return dict(row)
    return {}

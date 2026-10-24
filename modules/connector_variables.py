# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Single source of truth for the connector editor: variables, condition fields,
operators, triggers, alert types, date formats and sample events.

Plain data plus small helpers; no UI imports. Every list here matches what the
runtime actually puts in an event (see ``connector_integration``, ``twitch``,
``youtube``, ``obs_service``, ``donation_alerts``, ``connector_manager``).

Variable dicts: ``{"key", "label", "example", "group"}`` plus optional
``"date": True`` (accepts a ``{key:%H:%M}`` style date format) and
``"pattern": True`` (the key contains a number the user may change, e.g.
``message.word.1``). ``example`` is the text the default ``{key}`` renders for
the sample event.
"""

from __future__ import annotations

import copy
import datetime as _dt
import secrets
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import quote

from .connector_core import TriggerCondition, TriggerType
from .connector_placeholders import clock_variables

# --------------------------------------------------------------------------
# Sample moment used for date-like examples (a fixed, readable time).
# --------------------------------------------------------------------------
_SAMPLE_UTC = _dt.datetime(2026, 3, 14, 19, 30, 0, tzinfo=_dt.timezone.utc)


def _v(key: str, label: str, example: Any, group: str = "Event", **extra: Any) -> Dict[str, Any]:
    out = {"key": key, "label": label, "example": example, "group": group}
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# Variables available to every trigger
# --------------------------------------------------------------------------
ALWAYS_VARIABLES: List[Dict[str, Any]] = [
    _v("connector_name", "Connector name", "My connector", "Connector"),
    _v(
        "trigger_count",
        "Times this connector has run (this run included)",
        "12",
        "Connector",
    ),
    _v("trigger_type", "Trigger type", "twitch_bits", "Connector"),
    _v("time", "Current time (24-hour)", "19:30", "Time", date=True),
    _v("date", "Today's date", "2026-03-14", "Time", date=True),
    _v("datetime", "Current date and time", "2026-03-14 19:30", "Time", date=True),
    _v("triggered_at", "When the connector ran", "1773516600", "Time", date=True),
    _v(
        "message_after_conditions",
        "Message with the matched condition text removed",
        "hello there",
        "Message helpers",
    ),
    _v(
        "message.word.1",
        "Word N of the message (change 1 to any number)",
        "!so",
        "Message helpers",
        pattern=True,
    ),
    _v(
        "message_after_word.1",
        "Word N of the message after conditions (change 1 to any number)",
        "hello",
        "Message helpers",
        pattern=True,
    ),
    _v(
        "message_after_from_word.2",
        "The message after conditions from word N to the end (change 2 to any number)",
        "there",
        "Message helpers",
        pattern=True,
    ),
]

# --------------------------------------------------------------------------
# Per-trigger event variables
# --------------------------------------------------------------------------
_USER = _v("username", "Username", "TestUser")
_USER_ID = _v("user_id", "User ID", "123456789")
_MESSAGE = _v("message", "Message", "Hello chat!")
_SUPER = [
    _USER,
    _v("amount", "Amount", "5"),
    _v("currency", "Currency", "USD"),
    _v("display_amount", "Amount as shown by YouTube", "$5.00"),
    _MESSAGE,
]
_OBS_OUTPUT = [
    _v("output_active", "Is active (true/false)", "true"),
    _v("output_state", "Output state", "OBS_WEBSOCKET_OUTPUT_STARTED"),
]

EVENT_VARIABLES: Dict[str, List[Dict[str, Any]]] = {
    "twitch_bits": [
        _USER,
        _v("amount", "Bits", "100"),
        _v("message", "Cheer message", "Take my bits!"),
        _USER_ID,
        _v("is_anonymous", "Anonymous cheer (true/false)", "false"),
        _v("bits_type", "Bits type (cheer, power_up)", "cheer"),
    ],
    "twitch_sub": [
        _USER,
        _v("tier", "Tier (1, 2 or 3; Prime is 1)", "1"),
        _v("message", "Sub message", "Happy to be here!"),
        _v("months", "Months (always 1 for a new sub)", "1"),
        _USER_ID,
        _v("is_gift", "Was gifted (true/false)", "false"),
    ],
    "twitch_resub": [
        _USER,
        _v("tier", "Tier (1, 2 or 3; Prime is 1)", "1"),
        _v("months", "Total months subscribed", "12"),
        _v("streak_months", "Months in a row (0 if not shared)", "6"),
        _v("message", "Resub message", "A whole year!"),
        _USER_ID,
    ],
    "twitch_giftsub": [
        _v("username", "Gifter", "GenerousViewer"),
        _v("gift_count", "Number of gifts", "5"),
        _v("tier", "Tier (1, 2 or 3)", "1"),
        _v("is_anonymous", "Anonymous gift (true/false)", "false"),
        _v("cumulative_total", "Gifter's all-time gifts (0 if hidden)", "25"),
        _v("user_id", "Gifter user ID", "123456789"),
    ],
    "twitch_follow": [
        _USER,
        _USER_ID,
        _v("followed_at", "Followed at", "2026-03-14 19:30:00+00:00", date=True),
    ],
    "twitch_raid": [
        _v("username", "Raider", "FriendlyStreamer"),
        _v("viewer_count", "Viewers", "42"),
        _v("from_user_id", "Raider user ID", "987654321"),
        _v("to_user_id", "Your user ID", "123456789"),
    ],
    "twitch_points": [
        _USER,
        _v("reward_name", "Reward name", "Hydrate!"),
        _v("reward_title", "Reward title (same as name)", "Hydrate!"),
        _v("reward_id", "Reward ID", "a1b2c3d4-0000-0000-0000-000000000000"),
        _v("reward_cost", "Reward cost", "500"),
        _v("user_input", "Viewer's text", "Drink some water"),
        _USER_ID,
    ],
    "twitch_chat_message": [
        _USER,
        _v("message", "Message", "!hello there"),
        _v("is_command", "Starts with ! (true/false)", "true"),
        _v("command", "Command word (without !)", "hello"),
        _USER_ID,
        _v("is_moderator", "Is a moderator (true/false)", "false"),
        _v("badges", "Badges (comma separated)", "subscriber,vip"),
        _v("emotes", "Emotes used (comma separated)", "Kappa"),
    ],
    "twitch_hype_train_start": [
        _v("level", "Level", "1"),
        _v("total", "Points so far", "1200"),
        _v("goal", "Points for next level", "2000"),
        _v("started_at", "Started at", "2026-03-14 19:30:00+00:00", "Stream", date=True),
        _v("expires_at", "Expires at", "2026-03-14 19:35:00+00:00", "Stream", date=True),
    ],
    "twitch_hype_train_end": [
        _v("level", "Level reached", "3"),
        _v("total", "Total points", "6500"),
        _v("started_at", "Started at", "2026-03-14 19:30:00+00:00", "Stream", date=True),
        _v("ended_at", "Ended at", "2026-03-14 19:42:00+00:00", "Stream", date=True),
        _v(
            "cooldown_ends_at",
            "Next train possible at",
            "2026-03-14 20:42:00+00:00",
            "Stream",
            date=True,
        ),
    ],
    "twitch_stream_online": [
        _v("stream_id", "Stream ID", "40000000000", "Stream"),
        _v("stream_type", "Stream type", "live", "Stream"),
        _v("started_at", "Started at", "2026-03-14 19:30:00+00:00", "Stream", date=True),
    ],
    "twitch_stream_offline": [],
    "youtube_chat_message": [_USER, _MESSAGE, _v("user_id", "Channel ID", "UCabc123")],
    "youtube_member": [
        _USER,
        _v("member_level", "Membership level", "Member"),
        _v("message", "Message", "Just joined!"),
    ],
    "youtube_member_milestone": [
        _USER,
        _v("months", "Months a member", "6"),
        _v("member_level", "Membership level", "Member"),
        _v("message", "Message", "Half a year!"),
    ],
    "youtube_gift_membership": [
        _v("username", "Gifter", "GenerousViewer"),
        _v("gift_count", "Number of gifts", "5"),
        _v("quantity", "Number of gifts (same as gift count)", "5"),
        _v("member_level", "Membership level", "Member"),
    ],
    "youtube_superchat": list(_SUPER),
    "youtube_supersticker": list(_SUPER),
    "donation": [
        _v("username", "Donor", "KindViewer"),
        _v("amount", "Amount (in your alert currency)", "5"),
        _v("currency", "Currency", "USD"),
        _v("formatted_amount", "Amount as text", "$5.00"),
        _v("display_amount", "Amount as text (same as formatted)", "$5.00"),
        _v("message", "Donation message", "Keep it up!"),
        _v("source", "Where it came from", "streamlabs"),
        _v("original_amount", "Amount as sent", "5"),
        _v("original_currency", "Currency as sent", "USD"),
    ],
    "timer": [
        _v("interval_seconds", "Timer interval (seconds)", "300"),
        _v("timer_id", "Timer ID", "test"),
    ],
    "schedule": [
        _v("specific_time", "Scheduled time (HH:MM)", "19:30"),
        _v("days_of_week", "Scheduled days (0=Mon .. 6=Sun)", "[0, 2, 4]"),
    ],
    "hotkey": [
        _v("combo", "Key combination", "ctrl+shift+f"),
        _v("key_code", "Main key", "f"),
        _v("modifiers", "Modifier keys", '["ctrl", "shift"]'),
    ],
    "streamdeck": [_v("connector_id", "Connector ID", "connector-id")],
    "webhook": [
        _v("payload", "Everything the webhook sent (JSON)", '{"username": "TestUser"}'),
        _v("username", "username (if the webhook sends it)", "TestUser"),
        _v("message", "message (if the webhook sends it)", "Hello from a webhook"),
        _v("amount", "amount (if the webhook sends it)", "5"),
    ],
    "obs_scene_changed": [
        _v("scene_name", "New scene", "Gameplay"),
        _v("previous_scene_name", "Previous scene", "Starting Soon"),
    ],
    "obs_stream_state": list(_OBS_OUTPUT),
    "obs_record_state": list(_OBS_OUTPUT),
    "obs_input_mute": [
        _v("input_name", "Audio source", "Mic/Aux"),
        _v("input_muted", "Is muted (true/false)", "true"),
    ],
    "any": [
        _v("event_type", "Event type", "twitch_chat_message"),
        _v("username", "Username (when the event has one)", "TestUser"),
        _v("message", "Message (when the event has one)", "Hello chat!"),
        _v("amount", "Amount (when the event has one)", "5"),
    ],
}

# --------------------------------------------------------------------------
# Condition fields
# --------------------------------------------------------------------------


def _f(key: str, label: str, ftype: str) -> Dict[str, str]:
    return {"key": key, "label": label, "type": ftype}


_SUPER_FIELDS = [
    _f("amount", "Amount", "number"),
    _f("currency", "Currency", "text"),
    _f("username", "Username", "text"),
    _f("message", "Message", "text"),
    _f("display_amount", "Amount as shown", "text"),
]
_OBS_OUTPUT_FIELDS = [
    _f("output_active", "Is active", "bool"),
    _f("output_state", "Output state", "text"),
]

CONDITION_FIELDS: Dict[str, List[Dict[str, str]]] = {
    "twitch_bits": [
        _f("amount", "Bits", "number"),
        _f("username", "Username", "text"),
        _f("message", "Message", "text"),
    ],
    "twitch_sub": [
        _f("tier", "Tier (1-3)", "number"),
        _f("username", "Username", "text"),
        _f("message", "Message", "text"),
        _f("is_gift", "Was gifted", "bool"),
    ],
    "twitch_resub": [
        _f("tier", "Tier (1-3)", "number"),
        _f("months", "Total months", "number"),
        _f("username", "Username", "text"),
        _f("message", "Message", "text"),
    ],
    "twitch_giftsub": [
        _f("username", "Gifter", "text"),
        _f("gift_count", "Number of gifts", "number"),
        _f("tier", "Tier (1-3)", "number"),
        _f("is_anonymous", "Anonymous", "bool"),
    ],
    "twitch_follow": [_f("username", "Username", "text")],
    "twitch_raid": [
        _f("username", "Raider", "text"),
        _f("viewer_count", "Viewers", "number"),
    ],
    "twitch_points": [
        _f("reward_name", "Reward name", "text"),
        _f("reward_id", "Reward ID", "text"),
        _f("reward_cost", "Reward cost", "number"),
        _f("username", "Username", "text"),
        _f("user_input", "Viewer's text", "text"),
    ],
    "twitch_chat_message": [
        _f("username", "Username", "text"),
        _f("message", "Message", "text"),
        _f("is_moderator", "Is a moderator", "bool"),
        _f("is_command", "Is a command", "bool"),
        _f("command", "Command word", "text"),
        _f("user_id", "User ID", "text"),
    ],
    "twitch_hype_train_start": [
        _f("level", "Level", "number"),
        _f("total", "Points so far", "number"),
        _f("goal", "Points for next level", "number"),
    ],
    "twitch_hype_train_end": [
        _f("level", "Level reached", "number"),
        _f("total", "Total points", "number"),
    ],
    "twitch_stream_online": [_f("stream_type", "Stream type", "text")],
    "twitch_stream_offline": [],
    "youtube_chat_message": [
        _f("username", "Username", "text"),
        _f("message", "Message", "text"),
        _f("user_id", "Channel ID", "text"),
    ],
    "youtube_member": [
        _f("username", "Username", "text"),
        _f("member_level", "Membership level", "text"),
        _f("message", "Message", "text"),
    ],
    "youtube_member_milestone": [
        _f("username", "Username", "text"),
        _f("months", "Months a member", "number"),
        _f("member_level", "Membership level", "text"),
        _f("message", "Message", "text"),
    ],
    "youtube_gift_membership": [
        _f("username", "Gifter", "text"),
        _f("gift_count", "Number of gifts", "number"),
        _f("member_level", "Membership level", "text"),
    ],
    "youtube_superchat": list(_SUPER_FIELDS),
    "youtube_supersticker": list(_SUPER_FIELDS),
    "donation": [
        _f("amount", "Amount", "number"),
        _f("currency", "Currency", "text"),
        _f("username", "Donor", "text"),
        _f("message", "Message", "text"),
        _f("source", "Source (streamlabs, streamelements, youtube)", "text"),
    ],
    "timer": [],
    "schedule": [],
    "hotkey": [],
    "streamdeck": [],
    "webhook": [
        _f("username", "username (if sent)", "text"),
        _f("message", "message (if sent)", "text"),
        _f("amount", "amount (if sent)", "number"),
    ],
    "obs_scene_changed": [
        _f("scene_name", "New scene", "text"),
        _f("previous_scene_name", "Previous scene", "text"),
    ],
    "obs_stream_state": list(_OBS_OUTPUT_FIELDS),
    "obs_record_state": list(_OBS_OUTPUT_FIELDS),
    "obs_input_mute": [
        _f("input_name", "Audio source", "text"),
        _f("input_muted", "Is muted", "bool"),
    ],
}


def _any_condition_fields() -> List[Dict[str, str]]:
    out = [_f("event_type", "Event type", "text")]
    seen = {"event_type"}
    for fields in CONDITION_FIELDS.values():
        for item in fields:
            if item["key"] not in seen:
                seen.add(item["key"])
                out.append(dict(item))
    return out


CONDITION_FIELDS["any"] = _any_condition_fields()

# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
OPERATORS: Dict[str, List[Tuple[str, str]]] = {
    "number": [
        ("equal", "is"),
        ("not_equal", "is not"),
        ("greater_than", "more than (>)"),
        ("greater_than_or_equal", "at least (≥)"),
        ("less_than", "less than (<)"),
        ("less_than_or_equal", "at most (≤)"),
    ],
    "text": [
        ("equal", "is"),
        ("not_equal", "is not"),
        ("contains", "contains"),
        ("not_contains", "does not contain"),
        ("begins_with", "starts with"),
        ("ends_with", "ends with"),
        ("starts_with", "first word is"),
        ("regex_match", "matches pattern"),
    ],
    "bool": [
        ("equal", "is"),
        ("not_equal", "is not"),
    ],
}

# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------
_HYPE_WARNING = "Twitch may no longer send Hype Train events, so this might never fire."


def _t(value, label, group, hint, fires=True, warning=None) -> Dict[str, Any]:
    return {
        "value": value,
        "label": label,
        "group": group,
        "hint": hint,
        "fires": fires,
        "warning": warning,
    }


TRIGGERS: List[Dict[str, Any]] = [
    _t("twitch_bits", "Bits cheered", "Twitch", "Someone cheers bits in your channel."),
    _t("twitch_sub", "New subscriber", "Twitch", "Someone subscribes for the first time."),
    _t("twitch_resub", "Resubscription", "Twitch", "A subscriber shares a resub message."),
    _t("twitch_giftsub", "Gifted subs", "Twitch", "Someone gifts one or more subs."),
    _t("twitch_follow", "New follower", "Twitch", "Someone follows your channel."),
    _t("twitch_raid", "Raid", "Twitch", "Another streamer raids your channel."),
    _t(
        "twitch_points",
        "Channel points reward",
        "Twitch",
        "A viewer redeems a channel points reward.",
    ),
    _t("twitch_chat_message", "Chat message", "Twitch", "Someone sends a chat message."),
    _t(
        "twitch_hype_train_start",
        "Hype Train started",
        "Twitch",
        "A Hype Train begins.",
        warning=_HYPE_WARNING,
    ),
    _t(
        "twitch_hype_train_end",
        "Hype Train ended",
        "Twitch",
        "A Hype Train ends.",
        warning=_HYPE_WARNING,
    ),
    _t("twitch_stream_online", "Stream went live", "Twitch", "Your Twitch stream starts."),
    _t("twitch_stream_offline", "Stream ended", "Twitch", "Your Twitch stream ends."),
    _t("youtube_chat_message", "Chat message", "YouTube", "Someone chats in your live stream."),
    _t("youtube_member", "New member", "YouTube", "Someone becomes a channel member."),
    _t(
        "youtube_member_milestone",
        "Membership milestone",
        "YouTube",
        "A member celebrates a membership milestone.",
    ),
    _t(
        "youtube_gift_membership",
        "Gifted memberships",
        "YouTube",
        "Someone gifts memberships.",
    ),
    _t("youtube_superchat", "Super Chat", "YouTube", "Someone sends a Super Chat."),
    _t("youtube_supersticker", "Super Sticker", "YouTube", "Someone sends a Super Sticker."),
    _t(
        "donation",
        "Tip / donation",
        "Tips",
        "A tip arrives through Streamlabs, StreamElements or a YouTube Super Chat.",
    ),
    _t("obs_scene_changed", "Scene changed", "OBS", "OBS switches to another scene."),
    _t("obs_stream_state", "Streaming started or stopped", "OBS", "OBS starts or stops streaming."),
    _t("obs_record_state", "Recording started or stopped", "OBS", "OBS starts or stops recording."),
    _t("obs_input_mute", "Audio source muted or unmuted", "OBS", "An OBS audio source is muted or unmuted."),
    _t("hotkey", "Hotkey", "Controls", "You press a key combination anywhere on this computer."),
    _t("streamdeck", "Stream Deck button", "Controls", "You press this connector's Stream Deck button."),
    _t(
        "webhook",
        "Webhook",
        "Controls",
        "Another app or script calls this connector's web address.",
    ),
    _t("timer", "Every few minutes", "Time", "Runs again and again on a fixed interval."),
    _t("schedule", "At a set time", "Time", "Runs at a time of day, on the days you choose."),
    _t("any", "Any event", "Other", "Runs for every event above; use conditions to narrow it."),
]

# --------------------------------------------------------------------------
# Alert types for the "Play alert" action
# --------------------------------------------------------------------------
ALERT_TYPES: List[Tuple[str, str]] = [
    ("bit", "Bits"),
    ("sub", "Subscription"),
    ("resub", "Resubscription"),
    ("giftsub", "Gifted subs"),
    ("streak", "Watch streak"),
    ("donation", "Donation"),
    ("raid", "Raid"),
    ("follow", "Follow"),
    ("point", "Channel points reward"),
]

# --------------------------------------------------------------------------
# Date format presets for {key:FORMAT}
# --------------------------------------------------------------------------
DATE_FORMATS: List[Dict[str, str]] = [
    {"format": "%H:%M", "label": "24-hour time"},
    {"format": "%-I:%M %p", "label": "12-hour time"},
    {"format": "%H:%M:%S", "label": "Time with seconds"},
    {"format": "%Y-%m-%d", "label": "ISO date"},
    {"format": "%b %-d", "label": "Short date"},
    {"format": "%A, %B %-d, %Y", "label": "Long date"},
    {"format": "%A", "label": "Day name"},
    {"format": "%B", "label": "Month name"},
    {"format": "%Y", "label": "Year"},
    {"format": "%b %-d, %-I:%M %p", "label": "Date and time"},
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _type_value(trigger_type: Any) -> str:
    return str(getattr(trigger_type, "value", trigger_type) or "")


def variables_for(trigger_type: Any) -> List[Dict[str, Any]]:
    """Event variables for *trigger_type* followed by the always-available ones."""
    return [*EVENT_VARIABLES.get(_type_value(trigger_type), []), *ALWAYS_VARIABLES]


def condition_field(trigger_type: Any, key: str) -> Optional[Dict[str, str]]:
    """The CONDITION_FIELDS entry for *key* on *trigger_type*, or None."""
    for item in CONDITION_FIELDS.get(_type_value(trigger_type), []):
        if item["key"] == key:
            return item
    return None


def trigger_info(trigger_type: Any) -> Optional[Dict[str, Any]]:
    value = _type_value(trigger_type)
    for item in TRIGGERS:
        if item["value"] == value:
            return item
    return None


# Typed values for the sample events (examples above are display text).
_SAMPLES: Dict[str, Dict[str, Any]] = {
    "twitch_bits": {
        "username": "TestUser",
        "amount": 100,
        "message": "Take my bits!",
        "user_id": "123456789",
        "is_anonymous": False,
        "bits_type": "cheer",
    },
    "twitch_sub": {
        "username": "TestUser",
        "tier": 1,
        "message": "Happy to be here!",
        "months": 1,
        "user_id": "123456789",
        "is_gift": False,
    },
    "twitch_resub": {
        "username": "TestUser",
        "tier": 1,
        "months": 12,
        "streak_months": 6,
        "message": "A whole year!",
        "user_id": "123456789",
    },
    "twitch_giftsub": {
        "username": "GenerousViewer",
        "gift_count": 5,
        "tier": 1,
        "is_anonymous": False,
        "cumulative_total": 25,
        "user_id": "123456789",
    },
    "twitch_follow": {
        "username": "TestUser",
        "user_id": "123456789",
        "followed_at": _SAMPLE_UTC,
    },
    "twitch_raid": {
        "username": "FriendlyStreamer",
        "viewer_count": 42,
        "from_user_id": "987654321",
        "to_user_id": "123456789",
    },
    "twitch_points": {
        "username": "TestUser",
        "reward_name": "Hydrate!",
        "reward_title": "Hydrate!",
        "reward_id": "a1b2c3d4-0000-0000-0000-000000000000",
        "reward_cost": 500,
        "user_input": "Drink some water",
        "user_id": "123456789",
    },
    "twitch_chat_message": {
        "username": "TestUser",
        "message": "!hello there",
        "is_command": True,
        "command": "hello",
        "user_id": "123456789",
        "is_moderator": False,
        "badges": "subscriber,vip",
        "emotes": "Kappa",
    },
    "twitch_hype_train_start": {
        "level": 1,
        "total": 1200,
        "goal": 2000,
        "started_at": _SAMPLE_UTC,
        "expires_at": _SAMPLE_UTC + _dt.timedelta(minutes=5),
    },
    "twitch_hype_train_end": {
        "level": 3,
        "total": 6500,
        "started_at": _SAMPLE_UTC,
        "ended_at": _SAMPLE_UTC + _dt.timedelta(minutes=12),
        "cooldown_ends_at": _SAMPLE_UTC + _dt.timedelta(minutes=72),
    },
    "twitch_stream_online": {
        "stream_id": "40000000000",
        "stream_type": "live",
        "started_at": _SAMPLE_UTC,
    },
    "twitch_stream_offline": {},
    "youtube_chat_message": {
        "username": "TestUser",
        "message": "Hello chat!",
        "user_id": "UCabc123",
    },
    "youtube_member": {
        "username": "TestUser",
        "member_level": "Member",
        "message": "Just joined!",
    },
    "youtube_member_milestone": {
        "username": "TestUser",
        "months": 6,
        "member_level": "Member",
        "message": "Half a year!",
    },
    "youtube_gift_membership": {
        "username": "GenerousViewer",
        "gift_count": 5,
        "quantity": 5,
        "member_level": "Member",
    },
    "youtube_superchat": {
        "username": "TestUser",
        "amount": 5.0,
        "currency": "USD",
        "display_amount": "$5.00",
        "message": "Hello chat!",
    },
    "youtube_supersticker": {
        "username": "TestUser",
        "amount": 5.0,
        "currency": "USD",
        "display_amount": "$5.00",
        "message": "",
    },
    "donation": {
        "username": "KindViewer",
        "amount": 5.0,
        "currency": "USD",
        "formatted_amount": "$5.00",
        "display_amount": "$5.00",
        "message": "Keep it up!",
        "source": "streamlabs",
        "original_amount": 5.0,
        "original_currency": "USD",
    },
    "timer": {"interval_seconds": 300, "timer_id": "test"},
    "schedule": {"specific_time": "19:30", "days_of_week": []},
    "hotkey": {"combo": "ctrl+shift+f", "key_code": "f", "modifiers": ["ctrl", "shift"]},
    "streamdeck": {"connector_id": ""},
    "webhook": {
        "username": "TestUser",
        "message": "Hello from a webhook",
        "amount": 5,
    },
    "obs_scene_changed": {"scene_name": "Gameplay", "previous_scene_name": "Starting Soon"},
    "obs_stream_state": {
        "output_active": True,
        "output_state": "OBS_WEBSOCKET_OUTPUT_STARTED",
    },
    "obs_record_state": {
        "output_active": True,
        "output_state": "OBS_WEBSOCKET_OUTPUT_STARTED",
    },
    "obs_input_mute": {"input_name": "Mic/Aux", "input_muted": True},
}


def _trigger_value(trigger: Any, key: str, default: Any = None) -> Any:
    if trigger is None:
        return default
    if isinstance(trigger, Mapping):
        return trigger.get(key, default)
    return getattr(trigger, key, default)


def sample_event(trigger_type: Any, trigger: Any = None) -> Dict[str, Any]:
    """Realistic event data for *trigger_type*, including ``event_type``.

    *trigger* (a trigger object or its config dict) tailors the sample so a test
    run actually fires: hotkey -> its own combination; streamdeck -> its
    ``connector_id``; timer -> its ``trigger_id`` / ``interval_seconds``;
    schedule -> its ``trigger_id`` / ``specific_time`` / ``days_of_week``.
    Conditions are not considered.
    """
    value = _type_value(trigger_type)
    if value == "any":
        data: Dict[str, Any] = {}
        for sample in _SAMPLES.values():
            for key, item in sample.items():
                data.setdefault(key, copy.deepcopy(item))
        data["event_type"] = "twitch_chat_message"
    else:
        data = copy.deepcopy(_SAMPLES.get(value, {}))
        data["event_type"] = value
    data.setdefault("timestamp", time.time())
    data.setdefault("source", "test")

    if value == "hotkey":
        from .hotkey_listener import normalize_hotkey_combo

        combo = ""
        if trigger is not None and hasattr(trigger, "combo") and callable(trigger.combo):
            combo = trigger.combo()
        if not combo:
            combo = normalize_hotkey_combo(
                _trigger_value(trigger, "key_combination", "") or ""
            )
        if combo:
            parts = combo.split("+")
            data.update({"combo": combo, "key_code": parts[-1], "modifiers": parts[:-1]})
    elif value == "streamdeck":
        connector_id = _trigger_value(trigger, "connector_id", "") or ""
        data["connector_id"] = connector_id
    elif value == "timer":
        data["timer_id"] = _trigger_value(trigger, "trigger_id", "") or "test"
        interval = _trigger_value(trigger, "interval_seconds", None)
        if interval not in (None, ""):
            try:
                data["interval_seconds"] = int(float(interval))
            except (TypeError, ValueError):
                pass
    elif value == "schedule":
        trigger_id = _trigger_value(trigger, "trigger_id", "") or ""
        if trigger_id:
            data["trigger_id"] = trigger_id
        specific = _trigger_value(trigger, "specific_time", "") or ""
        if specific:
            data["specific_time"] = specific
        days = _trigger_value(trigger, "days_of_week", None)
        if days:
            data["days_of_week"] = list(days)
    elif value == "webhook":
        payload = {k: v for k, v in data.items() if k in ("username", "message", "amount")}
        data["payload"] = payload
    return data


def example_context(
    trigger_type: Any,
    connector_name: str = "",
    trigger_count: int = 0,
    trigger: Any = None,
) -> Dict[str, Any]:
    """Placeholder context for the live preview: sample event + always variables.

    ``substitute_connector_placeholders(text, example_context(...))`` renders the
    preview. Date-like values are real datetimes / epoch numbers, so
    ``{time:%-I:%M %p}`` previews correctly.
    """
    from .connector_core import message_after_trigger_conditions

    event = sample_event(trigger_type, trigger)
    conditions = list(_trigger_value(trigger, "conditions", None) or [])
    conditions = [c for c in conditions if isinstance(c, TriggerCondition)]
    ctx: Dict[str, Any] = dict(event)
    ctx.update(
        {
            "trigger_type": _type_value(trigger_type),
            "connector_name": connector_name or "My connector",
            "trigger_count": int(trigger_count or 0) + 1,
            "triggered_at": time.time(),
            "message_after_conditions": message_after_trigger_conditions(
                str(event.get("message", "")), conditions
            ),
        }
    )
    for key, item in clock_variables().items():
        ctx.setdefault(key, item)
    return ctx


def _condition_parts(condition: Any) -> Tuple[str, str, Any, str]:
    if isinstance(condition, Mapping):
        op = condition.get("operator", "")
        return (
            str(condition.get("field", "")),
            str(getattr(op, "value", op) or ""),
            condition.get("value"),
            str(condition.get("join", "and") or "and"),
        )
    op = getattr(condition, "operator", "")
    return (
        str(getattr(condition, "field", "")),
        str(getattr(op, "value", op) or ""),
        getattr(condition, "value", None),
        str(getattr(condition, "join", "and") or "and"),
    )


_OPERATOR_LABELS: Dict[str, str] = {}
for _ops in OPERATORS.values():
    for _value, _label in _ops:
        _OPERATOR_LABELS.setdefault(_value, _label)


def describe_conditions(
    conditions: Iterable[Any],
    field_labels: Optional[Mapping[str, str]] = None,
) -> str:
    """Plain-English summary: "Runs when A and B, or when C".

    *conditions* are TriggerCondition objects or dicts with ``field`` /
    ``operator`` / ``value`` / ``join``. *field_labels* maps field keys to
    labels (e.g. ``{f["key"]: f["label"] for f in CONDITION_FIELDS[t]}``).
    No conditions -> "Runs every time".
    """
    labels = dict(field_labels or {})
    groups: List[List[str]] = []
    for index, condition in enumerate(conditions or []):
        field_key, op, value, join = _condition_parts(condition)
        if not field_key:
            continue
        label = labels.get(field_key, field_key)
        op_label = _OPERATOR_LABELS.get(op, op.replace("_", " "))
        if isinstance(value, bool):
            shown = "true" if value else "false"
        elif value is None or value == "":
            shown = '""'
        else:
            shown = str(value)
        text = f"{label} {op_label} {shown}"
        if not groups or (index > 0 and join.strip().lower() == "or"):
            groups.append([text])
        else:
            groups[-1].append(text)
    if not groups:
        return "Runs every time"
    return "Runs when " + ", or when ".join(" and ".join(g) for g in groups)


def generate_webhook_secret() -> str:
    """A fresh random webhook key (URL-safe)."""
    return secrets.token_urlsafe(24)


def webhook_url(connector_id: str, secret: str = "") -> str:
    """Local URL that fires *connector_id*'s webhook trigger.

    ``http://127.0.0.1:<port>/api/connectors/webhook/<id>?key=<secret>`` where
    the port is the running web engine's (default 5000). Send the key as the
    ``X-Mycelian-Key`` header instead of ``?key=`` to keep it out of logs.
    """
    port = 5000
    try:
        from . import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine is not None and getattr(engine, "port", None):
            port = int(engine.port)
    except Exception:
        pass
    url = f"http://127.0.0.1:{port}/api/connectors/webhook/{quote(str(connector_id), safe='')}"
    if secret:
        url += f"?key={quote(str(secret), safe='')}"
    return url


__all__ = [
    "ALERT_TYPES",
    "ALWAYS_VARIABLES",
    "CONDITION_FIELDS",
    "DATE_FORMATS",
    "EVENT_VARIABLES",
    "OPERATORS",
    "TRIGGERS",
    "TriggerType",
    "condition_field",
    "describe_conditions",
    "example_context",
    "generate_webhook_secret",
    "sample_event",
    "trigger_info",
    "variables_for",
    "webhook_url",
]

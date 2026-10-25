# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Variable / event catalog for the chatbot editor (no NiceGUI).

Variable entries are dicts::

    {"key", "label", "example", "group", "date": bool (optional),
     "pattern": bool (optional; key contains N / FIELD to replace)}

The editor inserts ``{key}`` (or ``{key:FORMAT}`` for date entries, see
:data:`DATE_FORMATS`). Previews use the real runtime substitution
(:func:`render_preview`) with sample data, example lookup values and the
stored custom variables, without side effects.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

from .chatbot_core import (
    ChatCommand,
    ChatEvent,
    CommandType,
    EventType,
    render_template,
)
from .connector_variables import DATE_FORMATS  # noqa: F401 - re-exported

PERMISSIONS = [
    ("everyone", "Everyone"),
    ("subscriber", "Subscribers"),
    ("vip", "VIPs"),
    ("moderator", "Mods"),
]


def _v(key: str, label: str, example: Any, group: str, **extra: Any) -> Dict[str, Any]:
    out = {"key": key, "label": label, "example": example, "group": group}
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# Variables for every reply
# --------------------------------------------------------------------------

_BASICS = [
    _v("username", "Name of the chatter / event user", "TestViewer", "Basics"),
    _v("user_login", "Login name (where available)", "testviewer", "Basics"),
    _v("time", "Current time", "19:30:45", "Basics", date=True),
    _v("date", "Today's date", "2026-03-14", "Basics", date=True),
    _v("timestamp", "Date and time with seconds", "2026-03-14 19:30:45", "Basics", date=True),
    _v("datetime", "Date and time", "2026-03-14 19:30", "Basics", date=True),
]

STATS_VARIABLES = [
    _v("stats.alerts.follow_alerts_played", "Follows", "120", "Statistics"),
    _v("stats.alerts.new_subs_played", "New subs", "34", "Statistics"),
    _v("stats.alerts.resubs_played", "Resubs", "56", "Statistics"),
    _v("stats.alerts.gift_subs_played", "Gift sub alerts", "12", "Statistics"),
    _v("stats.alerts.total_gift_subs", "Gifted subs in total", "80", "Statistics"),
    _v("stats.alerts.bit_alerts_played", "Cheers", "45", "Statistics"),
    _v("stats.alerts.total_bits", "Bits in total", "25000", "Statistics"),
    _v("stats.alerts.raids", "Raids", "9", "Statistics"),
    _v("stats.alerts.point_alerts_redeemed", "Channel point redemptions", "300", "Statistics"),
    _v("stats.alerts.total_channel_points_redeemed", "Channel points spent", "150000", "Statistics"),
    _v("stats.hype_trains.total_completions", "Hype trains completed", "4", "Statistics"),
    _v("stats.chatbot.commands_triggered", "Commands used", "999", "Statistics"),
    _v("stats.chatbot.events_triggered", "Events triggered", "250", "Statistics"),
    _v("stats.quotes.quotes_created", "Quotes saved", "42", "Statistics"),
    _v("stats.quotes.total_quotes_redeemed", "Quotes shown", "77", "Statistics"),
    _v("stats.giveaways.giveaways_completed", "Giveaways completed", "3", "Statistics"),
    _v("stats.polls.polls_completed", "Polls completed", "6", "Statistics"),
    _v("stats.polls.total_votes", "Poll votes", "180", "Statistics"),
    _v("stats.chat.twitch_messages_received", "Twitch chat messages", "5400", "Statistics"),
    _v("stats.chat.total_messages", "Chat messages (all)", "6100", "Statistics"),
]

QUOTE_VARIABLES = [
    _v("quote.random.text", "Random quote: text", "Never give up!", "Quotes"),
    _v("quote.random.author", "Random quote: author", "Streamer", "Quotes"),
    _v("quote.random.number", "Random quote: number", "7", "Quotes"),
    _v("quote.random.formatted", "Random quote: text and author", '"Never give up!" - Streamer', "Quotes"),
    _v("quote.N.text", "Quote number N: text", "Never give up!", "Quotes", pattern=True),
    _v("quote.N.author", "Quote number N: author", "Streamer", "Quotes", pattern=True),
]


def hype_level_variables(levels: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
    """``stats.hype_trains.level_completions.N`` for known levels (1-5 + seen)."""
    found = set(levels or [])
    if levels is None:
        try:
            from .statistics_manager import get_statistics_manager

            completions = get_statistics_manager().get_all_statistics()["hype_trains"]["level_completions"]
            found = {int(k) for k in completions}
        except Exception:
            found = set()
    return [
        _v(f"stats.hype_trains.level_completions.{level}", f"Hype trains ended at level {level}", "1", "Statistics")
        for level in sorted(found | {1, 2, 3, 4, 5})
    ]


def youtube_variables() -> List[Dict[str, Any]]:
    """Global ``youtube.*`` variables plus ``youtube.<Channel>_<field>`` per channel."""
    out = [
        _v("youtube.latest_video_title", "Latest video title", "My new video", "YouTube"),
        _v("youtube.latest_video_url", "Latest video link", "https://youtu.be/abc123", "YouTube"),
        _v("youtube.latest_video_channel", "Latest video channel", "MyChannel", "YouTube"),
        _v("youtube.latest_video_id", "Latest video id", "abc123", "YouTube"),
        _v("youtube.connection_status", "YouTube connection", "Connected", "YouTube"),
        _v("youtube.channel_count", "Tracked channels", "1", "YouTube"),
    ]
    try:
        from .dataobjects import state_manager

        data = state_manager.get_youtube_data()
        channels = getattr(data, "channels", None) or {}
        for channel in channels.values():
            title = str(channel.get("channel_title") or "").replace(" ", "").replace("-", "")
            if not title:
                continue
            for field, label in (
                ("latest_video_title", "latest video title"),
                ("latest_video_url", "latest video link"),
                ("channel_url", "channel link"),
            ):
                out.append(
                    _v(f"youtube.{title}_{field}", f"{channel.get('channel_title')}: {label}",
                       str(channel.get(field) or ""), "YouTube")
                )
    except Exception:
        pass
    return out


COMMON_VARIABLES: List[Dict[str, Any]] = [*_BASICS, *STATS_VARIABLES, *QUOTE_VARIABLES]


# --------------------------------------------------------------------------
# Lookups and custom variables
# --------------------------------------------------------------------------


def lookup_variables(lookup: Any) -> List[Dict[str, Any]]:
    """Variables a lookup adds (``lookup`` is the item's dict or an id)."""
    from .chatbot_lookups import lookup_variable_specs

    out = []
    for spec in lookup_variable_specs(lookup):
        entry = _v(spec["key"], spec["label"], spec["example"], "Lookup")
        if spec.get("date"):
            entry["date"] = True
        if spec.get("pattern"):
            entry["pattern"] = True
        out.append(entry)
    if lookup:
        out.append(_v("lookup_error", "Why the lookup failed (for fallback text)", "Couldn't find bob", "Lookup"))
    return out


def example_lookup_variables(lookup: Any) -> Dict[str, Any]:
    from .chatbot_lookups import example_variables

    return example_variables(lookup)


def custom_variables(context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Stored custom variables for the panel (example = current preview)."""
    from .chatbot_custom_variables import load_custom_definitions, preview_custom_variable

    ctx = context if context is not None else example_context("command")
    out = []
    for name, definition in sorted(load_custom_definitions().items()):
        try:
            example = preview_custom_variable(definition, ctx)
        except Exception as exc:
            example = f"Error: {exc}"
        label = definition.get("description") or name[len("custom_"):].replace("_", " ")
        entry = _v(name, label, example, "Custom")
        entry["kind"] = definition.get("kind")
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Command variables
# --------------------------------------------------------------------------


def command_variables(
    command_type: Any = "basic",
    argument_names: Optional[Iterable[str]] = None,
    lookup_id: Any = None,
) -> List[Dict[str, Any]]:
    """Variables for a command's reply (on top of COMMON_VARIABLES)."""
    ctype = getattr(command_type, "value", command_type) or "basic"
    out = [
        _v("command_message", "Everything after the command", "hello world", "Command"),
        _v("command_first_word", "First word after the command", "hello", "Command"),
        _v("command_last_word", "Last word", "world", "Command"),
        _v("command_word_N", "Word N after the command", "hello", "Command", pattern=True),
        _v("cooldown", "Cooldown in seconds (0 = none)", "30", "Command"),
        _v("usage_left", "Uses left (\"unlimited\" without a limit)", "4", "Command"),
    ]
    if ctype == CommandType.COUNTER.value:
        out.append(_v("count", "Counter value (after this use)", "12", "Command"))
    if ctype == CommandType.RESET.value:
        out.append(_v("target_command", "Counter that was reset", "deaths", "Command"))
        out.append(_v("target_count", "Its value before the reset", "12", "Command"))
    for name in argument_names or []:
        out.append(_v(str(name), f"Argument: {name}", "hello", "Arguments"))
    if lookup_id:
        out.extend(lookup_variables(lookup_id))
    return out


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

_HYPE_WARNING = "Twitch may no longer send Hype Train events, so this might never fire."
_TIERS = {0: "Any tier", 1: "Tier 1", 2: "Tier 2", 3: "Tier 3"}
_MATCH = {"exact": "Is exactly", "starts_with": "Starts with", "contains": "Contains"}


def _f(key, label, kind, unit="", options=None, default=None, help=""):  # noqa: A002
    return {"key": key, "label": label, "kind": kind, "unit": unit,
            "options": options, "default": default, "help": help}


_CHAT_FILTERS = [
    _f("chat_message_text", "Message text", "text", default="",
       help="Leave empty to answer every message. Not case-sensitive."),
    _f("chat_message_match_type", "Match", "match", options=dict(_MATCH), default="exact"),
]


def _e(value, label, group, hint, filters=None, warning=None):
    return {"value": value, "label": label, "group": group, "hint": hint,
            "warning": warning, "filters": filters or []}


EVENTS: List[Dict[str, Any]] = [
    _e("follow", "New follower", "Twitch", "Someone follows your channel."),
    _e("subscription", "New subscriber", "Twitch", "Someone subscribes for the first time.",
       [_f("sub_tier", "Tier", "select", options=dict(_TIERS), default=0)]),
    _e("resubscription", "Resubscription", "Twitch", "Someone shares a resub message.",
       [_f("resub_months", "At least", "number", unit="months", default=0, help="0 = any"),
        _f("resub_tier", "Tier", "select", options=dict(_TIERS), default=0)]),
    _e("gift_subscription", "Gifted subs", "Twitch", "Someone gifts subs.",
       [_f("gift_sub_quantity", "At least", "number", unit="gifts", default=0, help="0 = any"),
        _f("gift_sub_tier", "Tier", "select", options=dict(_TIERS), default=0)]),
    _e("bits", "Bits", "Twitch", "Someone cheers bits or uses a bits power-up.",
       [_f("bits_quantity", "At least", "number", unit="bits", default=0, help="0 = any")]),
    _e("raid", "Raid", "Twitch", "Another channel raids you.",
       [_f("raid_viewer_count", "At least", "number", unit="viewers", default=0, help="0 = any"),
        _f("raid_raider_name", "Only from", "text", default="", help="Channel name; empty = anyone")]),
    _e("channel_point_redemption", "Channel points", "Twitch", "Someone redeems a channel point reward.",
       [_f("channel_point_reward_name", "Reward", "text", default="",
           help="Reward name; empty = any reward. Not case-sensitive.")]),
    _e("chat_message", "Chat message", "Twitch", "A chat message matches your text.",
       list(_CHAT_FILTERS)),
    _e("hype_train_start", "Hype Train started", "Twitch", "A Hype Train begins.",
       warning=_HYPE_WARNING),
    _e("hype_train_progress", "Hype Train level", "Twitch", "A Hype Train makes progress.",
       [_f("hype_train_level", "At level", "number", unit="or higher", default=0, help="0 = any")],
       warning=_HYPE_WARNING),
    _e("hype_train_end", "Hype Train ended", "Twitch", "A Hype Train ends.",
       [_f("hype_train_end_level", "Reached level", "number", unit="or higher", default=0, help="0 = any")],
       warning=_HYPE_WARNING),
    _e("donation", "Tip / donation", "Tips", "A tip arrives from a connected donation service.",
       [_f("donation_amount", "At least", "number", unit="amount", default=0, help="0 = any")]),
    _e("youtube_chat_message", "YouTube chat message", "YouTube", "A YouTube live chat message matches your text.",
       list(_CHAT_FILTERS)),
    _e("youtube_membership", "New member", "YouTube", "Someone becomes a channel member."),
    _e("youtube_membership_milestone", "Member milestone", "YouTube", "A member shares a milestone."),
    _e("youtube_gift_membership", "Gifted memberships", "YouTube", "Someone gifts memberships."),
    _e("youtube_superchat", "Super Chat", "YouTube", "Someone sends a Super Chat."),
    _e("youtube_supersticker", "Super Sticker", "YouTube", "Someone sends a Super Sticker."),
    _e("interval", "Timer", "Time", "Posts again and again; the first post comes after one full interval.",
       [_f("interval", "Every", "duration", unit="seconds", default=600, help="Minimum 1 second")]),
    _e("specific_time", "At a time of day", "Time", "Posts once a day at this local time.",
       [_f("specific_time", "Time", "time", default="18:00", help="24-hour HH:MM")]),
]

EVENT_INDEX = {entry["value"]: entry for entry in EVENTS}

_U = _v("username", "Name", "TestViewer", "Event")
_UL = _v("user_login", "Login name", "testviewer", "Event")
_UID = _v("user_id", "User id", "12345678", "Event")
_MSG = _v("message", "Their message", "Love the stream!", "Event")


def _ev(*items):
    return [dict(i) for i in items]


EVENT_VARIABLES: Dict[str, List[Dict[str, Any]]] = {
    "follow": _ev(_U, _UL, _UID, _v("followed_at", "Followed at", "2026-03-14T19:30:00Z", "Event", date=True)),
    "subscription": _ev(_U, _UL, _UID, _v("tier", "Tier", "1", "Event"), _v("months", "Months", "1", "Event"), _MSG),
    "resubscription": _ev(
        _U, _UL, _UID, _v("tier", "Tier", "1", "Event"),
        _v("months", "Total months", "12", "Event"),
        _v("streak_months", "Streak months (0 if hidden)", "5", "Event"), _MSG,
    ),
    "gift_subscription": _ev(
        _v("username", "Gifter", "GenerousGifter", "Event"),
        _v("user_login", "Gifter login", "generousgifter", "Event"),
        _v("user_id", "Gifter id", "12345678", "Event"),
        _v("tier", "Tier", "1", "Event"),
        _v("amount", "Subs gifted now", "5", "Event"),
        _v("total_gifts", "Gifter's lifetime total (may be empty)", "50", "Event"),
        _v("recipient_name", "Recipient (single gifts, when known)", "LuckyViewer", "Event"),
        _v("is_anonymous", "Anonymous (true/false)", "false", "Event"),
    ),
    "bits": _ev(
        _U, _UL, _UID, _v("amount", "Bits", "500", "Event"), _MSG,
        _v("power_up_type", "Power-up (empty for cheers)", "Gigantify An Emote", "Event"),
    ),
    "raid": _ev(
        _v("username", "Raider", "FriendlyRaider", "Event"),
        _v("user_login", "Raider login", "friendlyraider", "Event"),
        _v("user_id", "Raider id", "87654321", "Event"),
        _v("viewer_count", "Raiders", "25", "Event"),
        _v("game", "Raider's game", "Celeste", "Event"),
    ),
    "channel_point_redemption": _ev(
        _U, _UL, _UID, _v("reward_name", "Reward", "Hydrate", "Event"),
        _v("amount", "Cost", "100", "Event"), _v("message", "Their text", "Drink water!", "Event"),
    ),
    "chat_message": _ev(_U, _UL, _UID, _v("message", "Message", "hello chat", "Event")),
    "hype_train_start": _ev(
        _v("username", "Conductor (may be 'A viewer')", "TrainLeader", "Event"),
        _v("level", "Level", "1", "Event"),
    ),
    "hype_train_progress": _ev(
        _v("level", "Level", "3", "Event"), _v("progress", "Progress", "75", "Event"),
        _v("goal", "Goal", "100", "Event"),
    ),
    "hype_train_end": _ev(_v("level", "Level reached", "3", "Event"), _v("amount", "Total contributions", "1000", "Event")),
    "donation": _ev(
        _v("username", "Donor", "KindDonor", "Event"), _v("amount", "Amount", "5", "Event"),
        _v("currency", "Currency", "USD", "Event"),
        _v("formatted_amount", "Amount with currency", "$5.00", "Event"),
        _MSG, _v("source", "Service", "streamelements", "Event"),
        _v("original_amount", "Original amount", "5", "Event"),
        _v("original_currency", "Original currency", "USD", "Event"),
    ),
    "youtube_chat_message": _ev(_U, _v("user_id", "Channel id", "UC123", "Event"), _v("message", "Message", "hello chat", "Event")),
    "youtube_membership": _ev(_U, _v("member_level", "Level", "Member", "Event"), _MSG),
    "youtube_membership_milestone": _ev(
        _U, _v("months", "Months", "6", "Event"), _v("member_level", "Level", "Member", "Event"), _MSG,
    ),
    "youtube_gift_membership": _ev(
        _v("username", "Gifter", "GenerousGifter", "Event"), _v("amount", "Memberships gifted", "5", "Event"),
        _v("member_level", "Level", "Member", "Event"),
    ),
    "youtube_superchat": _ev(
        _U, _v("amount", "Amount", "10", "Event"), _v("currency", "Currency", "USD", "Event"),
        _v("formatted_amount", "Amount with currency", "$10.00", "Event"), _MSG,
    ),
    "youtube_supersticker": _ev(
        _U, _v("amount", "Amount", "2", "Event"), _v("currency", "Currency", "USD", "Event"),
        _v("formatted_amount", "Amount with currency", "$2.00", "Event"), _MSG,
    ),
    "interval": [],
    "specific_time": [],
}


def event_variables(event_type: Any) -> List[Dict[str, Any]]:
    value = getattr(event_type, "value", event_type)
    return [dict(v) for v in EVENT_VARIABLES.get(str(value), [])]


# --------------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------------

_WORDS = ["hello", "world", "example", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _attr(item: Any, name: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _chat_sample(item: Any) -> str:
    text = str(_attr(item, "chat_message_text", "") or "").strip()
    if not text:
        return "Hello chat!"
    match_type = _attr(item, "chat_message_match_type", "exact")
    if match_type == "starts_with":
        return f"{text} everyone"
    if match_type == "contains":
        return f"well {text} indeed"
    return text


def sample_data(kind: str, event_type: Any = None, item: Any = None) -> Dict[str, Any]:
    """Realistic producer payload that passes the item's checks.

    Commands: sent by the broadcaster (permissions pass), with enough words
    for every argument. Events: amounts / names respect the item's filters.
    """
    base = {
        "username": "TestViewer",
        "user_login": "testviewer",
        "user_id": "12345678",
        "timestamp": time.time(),
        "source": "twitch",
    }
    if kind == "command":
        positions = [spec.get("position", 1) for spec in (_attr(item, "argument_mappings", {}) or {}).values()
                     if isinstance(spec, dict)]
        count = max([3, *positions])
        words = (_WORDS * 2)[:count]
        name = _attr(item, "command_name", "") or "command"
        return {
            **base,
            "userid": base["user_id"],
            "message": f"!{name} " + " ".join(words),
            "command_message": " ".join(words),
            "command_args": list(words),
            "badges": "broadcaster/1,subscriber/12",
        }

    value = str(getattr(event_type, "value", event_type) or getattr(_attr(item, "event_type"), "value", "") or "follow")

    def at_least(attr: str, default: float) -> float:
        wanted = _attr(item, attr, 0) or 0
        try:
            wanted = float(wanted)
        except (TypeError, ValueError):
            wanted = 0
        return max(default, wanted)

    def tier(attr: str) -> int:
        try:
            return int(_attr(item, attr, 0) or 0) or 1
        except (TypeError, ValueError):
            return 1

    if value == "follow":
        return {**base, "followed_at": "2026-03-14T19:30:00Z"}
    if value == "subscription":
        return {**base, "tier": tier("sub_tier"), "months": 1, "message": "Love the stream!"}
    if value == "resubscription":
        return {**base, "tier": tier("resub_tier"), "months": int(at_least("resub_months", 12)),
                "streak_months": 5, "message": "Another month!"}
    if value == "gift_subscription":
        qty = int(at_least("gift_sub_quantity", 5))
        return {**base, "username": "GenerousGifter", "user_login": "generousgifter",
                "tier": tier("gift_sub_tier"), "amount": qty, "quantity": qty, "total_gifts": 50,
                "recipient": "" if qty > 1 else "LuckyViewer",
                "recipient_name": "" if qty > 1 else "LuckyViewer", "is_anonymous": False}
    if value == "bits":
        return {**base, "amount": int(at_least("bits_quantity", 100)), "message": "Cheer100 great play!",
                "power_up_type": ""}
    if value == "donation":
        amount = at_least("donation_amount", 5.0)
        return {**base, "username": "KindDonor", "user_login": "kinddonor", "amount": amount,
                "currency": "USD", "formatted_amount": f"${amount:,.2f}", "message": "Keep it up!",
                "donation_message": "Keep it up!", "original_amount": amount,
                "original_currency": "USD", "source": "streamelements"}
    if value == "raid":
        raider = str(_attr(item, "raid_raider_name", "") or "").strip().lstrip("@") or "FriendlyRaider"
        return {**base, "username": raider, "user_login": raider.lower(), "user_id": "87654321",
                "raider_name": raider, "raider_login": raider.lower(), "raider_id": "87654321",
                "viewer_count": int(at_least("raid_viewer_count", 25)), "game": "Celeste"}
    if value == "channel_point_redemption":
        reward = str(_attr(item, "channel_point_reward_name", "") or "").strip() or "Hydrate"
        return {**base, "reward_title": reward, "reward_name": reward, "amount": 100,
                "message": "Drink water!"}
    if value == "hype_train_start":
        return {**base, "username": "TrainLeader", "user_login": "trainleader", "level": 1}
    if value == "hype_train_progress":
        return {**base, "username": "", "user_login": "", "user_id": "",
                "level": int(at_least("hype_train_level", 3)), "progress": 75, "goal": 100}
    if value == "hype_train_end":
        return {**base, "level": int(at_least("hype_train_end_level", 3)), "amount": 1000}
    if value in ("chat_message", "youtube_chat_message"):
        data = {**base, "message": _chat_sample(item), "badges": ""}
        if value == "youtube_chat_message":
            data.update({"source": "youtube", "user_id": "UC123", "userid": "UC123"})
        return data
    if value == "youtube_membership":
        return {**base, "source": "youtube", "tier": 1, "months": 1, "member_level": "Member",
                "message": "Happy to join!"}
    if value == "youtube_membership_milestone":
        return {**base, "source": "youtube", "tier": 1, "months": 6, "cumulative_months": 6,
                "member_level": "Member", "message": "Half a year!"}
    if value == "youtube_gift_membership":
        return {**base, "source": "youtube", "username": "GenerousGifter", "tier": 1, "amount": 5,
                "total_gifts": 5, "quantity": 5, "member_level": "Member"}
    if value in ("youtube_superchat", "youtube_supersticker"):
        amount = 10.0 if value == "youtube_superchat" else 2.0
        return {**base, "source": "youtube", "amount": amount, "currency": "USD",
                "formatted_amount": f"${amount:,.2f}", "display_amount": f"${amount:,.2f}",
                "message": "Great stream!", "donation_message": "Great stream!"}
    if value == "interval":
        return {"username": "System", "timestamp": time.time(), "source": "interval"}
    if value == "specific_time":
        return {"username": "System", "timestamp": time.time(), "source": "specific_time",
                "scheduled_time": _attr(item, "specific_time", "") or "18:00"}
    return dict(base)


def _as_item(kind: str, event_type: Any, item: Any) -> Any:
    if isinstance(item, (ChatCommand, ChatEvent)):
        return item
    data = dict(item or {}) if isinstance(item, dict) else {}
    if kind == "command":
        return ChatCommand(**data)
    if event_type is not None and "event_type" not in data:
        data["event_type"] = getattr(event_type, "value", event_type)
    return ChatEvent(**data)


def example_context(kind: str, event_type: Any = None, item: Any = None) -> Dict[str, Any]:
    """The context the runtime would build, with sample data and example lookups."""
    obj = _as_item(kind, event_type, item)
    if kind != "command" and event_type is None:
        event_type = obj.event_type
    data = sample_data(kind, event_type, obj)
    lookup_vars = example_lookup_variables(obj.lookup) if obj.lookup else None
    if kind == "command" and obj.command_type == CommandType.RESET:
        data["target_command"] = obj.reset_command or "deaths"
        data["target_count"] = 12
    return obj.build_context(data, lookup_vars)


def render_preview(text: str, kind: str, event_type: Any = None, item: Any = None) -> str:
    """*text* rendered by the real runtime with sample data (no side effects)."""
    return render_template(text, example_context(kind, event_type, item))


def preview_event_types() -> List[str]:
    """Every EventType value covered by EVENTS (catalog sanity helper)."""
    return [e.value for e in EventType if e.value in EVENT_INDEX]

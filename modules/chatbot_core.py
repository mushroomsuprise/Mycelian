#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
# Chatbot data model and runtime substitution.
#
# Template syntax (replies, fallbacks, argument defaults, custom variables)
# -------------------------------------------------------------------------
# * ``{key}`` -- a value from the item's context (chat / event data, command
#   variables, argument names, lookup variables). Dotted paths walk dicts.
# * ``{key:FORMAT}`` -- formatted like connectors: a FORMAT with a strftime
#   directive (``%d``, portable ``%-d`` ...) formats a date / epoch / ISO value,
#   anything else is a numeric format spec (``{amount:,}``, ``{amount:.2f}``).
# * ``{time}`` / ``{time.12.ampm}`` ... -- clock with dot filters
#   (:func:`format_time_variable`); ``{time:%H:%M}`` formats the clock.
# * ``{timestamp}`` (``YYYY-MM-DD HH:MM:SS``), ``{date}`` (``YYYY-MM-DD``),
#   ``{datetime}`` (``YYYY-MM-DD HH:MM``): local now; all accept a date FORMAT.
# * ``{stats.<path>}``, ``{youtube.<var>}``, ``{quote.random.text}`` /
#   ``{quote.<number>.<field>}``, ``{custom_<name>}``, ``{command_word_N}``.
#
# Substitution is one pass over the template: text inserted from a value (a
# viewer's message, a username, an API result) is never scanned again, so
# ``{stats.x}`` typed by a viewer stays literal. Unknown tokens stay as written.
# ``None`` values render as an empty string, booleans as ``true`` / ``false``.

import logging
import re
import sys
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from .connector_placeholders import (
    _ctx_lookup_found,
    _stringify,
    format_placeholder_value,
)

logger = logging.getLogger(__name__)


def _stat_child(obj: Any, part: str) -> Tuple[bool, Any]:
    if isinstance(obj, dict):
        if part in obj:
            return True, obj[part]
        if part.lstrip("-").isdigit() and int(part) in obj:
            return True, obj[int(part)]
    return False, None


def get_statistics_value(stat_name: str) -> str:
    """Get a statistics value by name from the StatisticsManager"""
    try:
        # Handle both 'stats.' prefixed and non-prefixed calls
        if stat_name.startswith("stats."):
            stat_name = stat_name[6:]  # Remove 'stats.' prefix

        from .statistics_manager import get_statistics_manager

        manager = get_statistics_manager()
        current: Any = manager.get_all_statistics()
        for part in stat_name.split("."):
            found, current = _stat_child(current, part)
            if not found:
                return "0"
        if current is None:
            return "0"
        if isinstance(current, float):
            return _stringify(round(current, 2))
        return _stringify(current)
    except Exception as e:
        logger.error(f"Error getting statistics value for '{stat_name}': {e}")
        return "0"


def get_youtube_value(youtube_var: str) -> str:
    """Get a YouTube value by variable name"""
    try:
        from .dataobjects import state_manager

        youtube_data = state_manager.get_youtube_data()
        if not youtube_data:
            return "YouTube data unavailable"

        # Check if this is a channel-specific variable (format: ChannelName_field)
        # Only treat as channel-specific if it doesn't match known global variable patterns
        global_vars = {
            "latest_video_url",
            "latest_video_title",
            "latest_video_id",
            "latest_video_channel",
            "connection_status",
            "channel_count",
        }

        if "_" in youtube_var and youtube_var not in global_vars:
            channel_name, field_name = youtube_var.split("_", 1)

            # Find the channel by name/title
            for channel_key, channel_data in youtube_data.channels.items():
                if channel_data.get("channel_title", "").lower().replace(
                    " ", ""
                ).replace("-", "") == channel_name.lower().replace(" ", "").replace(
                    "-", ""
                ):
                    # Found matching channel
                    field_mapping = {
                        "latest_video_url": channel_data.get(
                            "latest_video_url", "No video available"
                        ),
                        "latest_video_title": channel_data.get(
                            "latest_video_title", "No video available"
                        ),
                        "latest_video_id": channel_data.get(
                            "latest_video_id", "No video available"
                        ),
                        "channel_title": channel_data.get(
                            "channel_title", "Unknown channel"
                        ),
                        "channel_url": channel_data.get(
                            "channel_url", "No channel URL"
                        ),
                        "last_updated": channel_data.get(
                            "last_updated", "Never updated"
                        ),
                    }
                    return field_mapping.get(field_name, f"Unknown field: {field_name}")

            return f"Channel not found: {channel_name}"

        # Handle global variables
        global_vars = {
            "latest_video_url": youtube_data.latest_video_url or "No video available",
            "latest_video_title": youtube_data.latest_video_title
            or "No video available",
            "latest_video_id": youtube_data.latest_video_id or "No video available",
            "latest_video_channel": youtube_data.latest_video_channel
            or "Unknown channel",
            "connection_status": youtube_data.connection_status or "Disconnected",
            "channel_count": str(len(youtube_data.channels))
            if youtube_data.channels
            else "0",
        }

        return global_vars.get(youtube_var, f"Unknown YouTube variable: {youtube_var}")

    except Exception as e:
        logger.error(f"Error getting YouTube value for '{youtube_var}': {e}")
        return f"YouTube error: {str(e)}"


_QUOTE_FIELDS = ("text", "author", "quote_number", "number", "date_added", "added_by", "formatted")


def get_quote_value(spec: str) -> Optional[str]:
    """``random.text`` / ``5.author`` ... -> value; None for an unknown field.

    A missing quote gives an empty string.
    """
    which, _, field = str(spec or "").partition(".")
    if field not in _QUOTE_FIELDS:
        return None
    try:
        from .chatbot_manager import get_manager

        manager = get_manager()
        if which.isdigit():
            quote = manager.get_quote_by_number(int(which))
        elif which in ("random", "id", "any"):
            quote = manager.get_random_quote()
        else:
            return None
    except Exception as exc:
        logger.debug("Quote variable %s failed: %s", spec, exc)
        return ""
    if quote is None:
        return ""
    if field == "text":
        return quote.text
    if field == "author":
        return quote.author or ""
    if field in ("quote_number", "number"):
        return str(quote.quote_number)
    if field == "date_added":
        try:
            return datetime.fromtimestamp(float(quote.date_added)).strftime("%Y-%m-%d")
        except Exception:
            return ""
    if field == "added_by":
        return quote.added_by or ""
    return quote.format_quote()


TIME_VAR_PATTERN = re.compile(r"\{time(?:\.([^}]+))?\}", re.IGNORECASE)
_TIME_LAYOUT_PATTERN = re.compile(r"^(h{1,2}):mm(?::ss)?$", re.IGNORECASE)
COMMAND_WORD_PATTERN = re.compile(r"\{command_word_(\d+)\}")
PROCESSED_VARS_PATTERN = re.compile(r"\{([^}]+)\}")
YOUTUBE_VAR_PATTERN = re.compile(r"\{youtube\.([^}]+)\}")
STATS_VAR_PATTERN = re.compile(r"\{stats\.([^}]+)\}")
CUSTOM_VAR_PATTERN = re.compile(r"\{custom_([^}]+)\}")
_KNOWN_TIMEZONES = {
    "UTC": 0,
    "EST": -5,
    "CST": -6,
    "MST": -7,
    "PST": -8,
    "EDT": -4,
    "CDT": -5,
    "MDT": -6,
    "PDT": -7,
}


def format_time_variable(filters: str = "") -> str:
    """Format current time using order-independent dot filters.

    Bare ``{time}`` (empty filters) matches runtime default: 24-hour with seconds.

    Filters (any order, case-insensitive):
    - ``12`` / ``24`` — clock style
    - ``ampm`` / ``noampm`` — show or hide AM/PM
    - ``sec`` / ``nosec`` — include or omit seconds (ignored if a layout token is set)
    - ``tz`` — append timezone abbreviation
    - ``hh:mm``, ``h:mm``, ``hh:mm:ss``, ``h:mm:ss`` — explicit layout
    - ``UTC``, ``EST``, ``EDT``, ``CST``, ``CDT``, ``MST``, ``MDT``, ``PST``, ``PDT``
      — convert to that zone (label only when ``tz`` is also present)

    Examples:
    - ``""`` → ``19:30:45``
    - ``"12.ampm"`` → ``07:30:45 PM``
    - ``"hh:mm.12.ampm"`` → ``07:30 PM``
    - ``"UTC.24.tz"`` → ``00:30:45 UTC``
    """
    try:
        from datetime import timedelta, timezone

        hour_format = 24
        show_seconds = True
        show_ampm = False
        show_tz = False
        timezone_name = None
        layout = None

        if filters:
            for raw_token in filters.split("."):
                token = raw_token.strip()
                if not token:
                    continue
                token_lower = token.lower()

                if token_lower == "12":
                    hour_format = 12
                elif token_lower == "24":
                    hour_format = 24
                elif token_lower == "ampm":
                    show_ampm = True
                elif token_lower == "noampm":
                    show_ampm = False
                elif token_lower in ("sec", "seconds"):
                    show_seconds = True
                elif token_lower in ("nosec", "noseconds"):
                    show_seconds = False
                elif token_lower == "tz":
                    show_tz = True
                elif _TIME_LAYOUT_PATTERN.match(token_lower):
                    layout = token_lower
                else:
                    tz_candidate = token.upper()
                    if tz_candidate in _KNOWN_TIMEZONES:
                        timezone_name = tz_candidate

        now = datetime.now()

        if timezone_name:
            try:
                try:
                    import pytz

                    # Abbreviations like EST are not always valid pytz keys;
                    # map common US abbreviations to IANA zones when needed.
                    pytz_names = {
                        "UTC": "UTC",
                        "EST": "US/Eastern",
                        "EDT": "US/Eastern",
                        "CST": "US/Central",
                        "CDT": "US/Central",
                        "MST": "US/Mountain",
                        "MDT": "US/Mountain",
                        "PST": "US/Pacific",
                        "PDT": "US/Pacific",
                    }
                    if timezone_name == "UTC":
                        now = now.astimezone(pytz.UTC)
                    else:
                        tz = pytz.timezone(pytz_names.get(timezone_name, timezone_name))
                        now = now.astimezone(tz)
                except ImportError:
                    offset_hours = _KNOWN_TIMEZONES.get(timezone_name)
                    if offset_hours is not None:
                        tz = timezone(timedelta(hours=offset_hours))
                        now = now.astimezone(tz)
            except Exception:
                pass

        if layout:
            hour_token, rest = layout.split(":", 1)
            use_padded = len(hour_token) == 2
            include_seconds = rest == "mm:ss"
            if hour_format == 12:
                hour = now.hour % 12 or 12
            else:
                hour = now.hour
            # Build the hour by hand: %-H / %-I are not supported on Windows.
            hour_str = f"{hour:02d}" if use_padded else str(hour)
            time_str = hour_str + now.strftime(
                ":%M" + (":%S" if include_seconds else "")
            )
        else:
            if hour_format == 12:
                time_str = now.strftime("%I:%M:%S" if show_seconds else "%I:%M")
            else:
                time_str = now.strftime("%H:%M:%S" if show_seconds else "%H:%M")

        if show_ampm:
            time_str += now.strftime(" %p")

        if show_tz:
            label = timezone_name
            if not label:
                # Local abbreviation when no zone filter was given
                label = now.strftime("%Z") or "LOCAL"
            time_str += f" {label}"

        return time_str

    except Exception as e:
        logger.error(f"Error formatting time with filters '{filters}': {e}")
        return datetime.now().strftime("%H:%M:%S")


def replace_time_variables(text: str) -> str:
    """Replace ``{time}`` and ``{time.filter...}`` placeholders in text."""
    return TIME_VAR_PATTERN.sub(
        lambda m: format_time_variable(m.group(1) or ""),
        text,
    )


# --------------------------------------------------------------------------
# Template engine (single pass)
# --------------------------------------------------------------------------

TEMPLATE_TOKEN = re.compile(r"\{([A-Za-z0-9_][^{}]*)\}")
_TEMPLATE_KEY = re.compile(r"^[\w.\-]+$")
_COMMAND_WORD_KEY = re.compile(r"^command_word_(\d+)$")
_MAX_TEMPLATE_DEPTH = 10


class _TimestampValue(datetime):
    """``{timestamp}``: prints ``YYYY-MM-DD HH:MM:SS``."""

    def __str__(self) -> str:
        return self.strftime("%Y-%m-%d %H:%M:%S")


class _DateValue(datetime):
    """``{date}``: prints ``YYYY-MM-DD``."""

    def __str__(self) -> str:
        return self.strftime("%Y-%m-%d")


class _DateTimeValue(datetime):
    """``{datetime}``: prints ``YYYY-MM-DD HH:MM``."""

    def __str__(self) -> str:
        return self.strftime("%Y-%m-%d %H:%M")


class _ClockValue(datetime):
    """``{time:FMT}`` base value."""

    def __str__(self) -> str:
        return self.strftime("%H:%M:%S")


_CLOCK_CLASSES = {
    "timestamp": _TimestampValue,
    "date": _DateValue,
    "datetime": _DateTimeValue,
    "time": _ClockValue,
}


def _clock_value(key: str) -> datetime:
    now = datetime.now()
    cls = _CLOCK_CLASSES[key]
    return cls(now.year, now.month, now.day, now.hour, now.minute, now.second)


def lookup_template_value(key: str, context: Dict[str, Any], depth: int = 0) -> Tuple[bool, Any]:
    """(found, raw value) for a template key. See the module docstring."""
    lowered = key.lower()
    if lowered in _CLOCK_CLASSES:
        return True, _clock_value(lowered)
    if key.startswith("stats."):
        return True, get_statistics_value(key[6:])
    if key.startswith("youtube."):
        return True, get_youtube_value(key[8:])
    if key.startswith("quote."):
        value = get_quote_value(key[6:])
        return value is not None, value
    if key.startswith("custom_"):
        if depth > _MAX_TEMPLATE_DEPTH:
            return False, None
        from .chatbot_custom_variables import evaluate_custom_variable

        value = evaluate_custom_variable(key, context, _depth=depth + 1)
        return value is not None, value
    match = _COMMAND_WORD_KEY.match(key)
    if match and isinstance(context.get("command_args"), (list, tuple)):
        args = context["command_args"]
        index = int(match.group(1)) - 1
        return True, (str(args[index]) if 0 <= index < len(args) else "")
    if key in context:
        return True, context[key]
    if "." in key:
        return _ctx_lookup_found(context, key)
    return False, None


def resolve_template_token(inner: str, context: Dict[str, Any], depth: int = 0) -> Optional[str]:
    """Replacement text for the inside of one ``{...}``, or None (left as written)."""
    lowered = inner.lower()
    if lowered == "time" or lowered.startswith("time."):
        return format_time_variable(inner[5:] if len(inner) > 4 else "")
    key, _, fmt = inner.partition(":")
    if not _TEMPLATE_KEY.match(key):
        return None
    found, value = lookup_template_value(key, context, depth)
    if not found:
        return None
    if fmt:
        return format_placeholder_value(value, fmt)
    return _stringify(value)


def render_template(text: Any, context: Optional[Dict[str, Any]] = None, *, _depth: int = 0) -> str:
    """Substitute every ``{token}`` of *text* in one pass (see module docstring)."""
    if text is None:
        return ""
    text = str(text)
    if "{" not in text:
        return text
    ctx = context or {}

    def repl(match: re.Match) -> str:
        out = resolve_template_token(match.group(1), ctx, _depth)
        return match.group(0) if out is None else out

    return TEMPLATE_TOKEN.sub(repl, text)


def resolve_raw_template_value(text: str, context: Dict[str, Any], depth: int = 0) -> Any:
    """The raw value when *text* is exactly one ``{key}`` token, else rendered text.

    Keeps datetimes intact for date maths (custom "age" variables).
    """
    stripped = str(text or "").strip()
    match = TEMPLATE_TOKEN.fullmatch(stripped)
    if match and ":" not in match.group(1):
        found, value = lookup_template_value(match.group(1), context, depth)
        if found:
            return value
    return render_template(stripped, context, _depth=depth)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class CommandType(Enum):
    """Types of chat commands"""

    BASIC = "basic"
    COUNTER = "counter"
    RESET = "reset"


class EventType(Enum):
    """Types of automatic events"""

    FOLLOW = "follow"
    SUBSCRIPTION = "subscription"
    RESUBSCRIPTION = "resubscription"
    GIFT_SUBSCRIPTION = "gift_subscription"
    BITS = "bits"
    DONATION = "donation"
    RAID = "raid"
    HYPE_TRAIN_START = "hype_train_start"
    HYPE_TRAIN_END = "hype_train_end"
    HYPE_TRAIN_PROGRESS = "hype_train_progress"
    CHANNEL_POINT_REDEMPTION = "channel_point_redemption"
    INTERVAL = "interval"
    SPECIFIC_TIME = "specific_time"
    CHAT_MESSAGE = "chat_message"
    # YouTube live chat / monetization
    YOUTUBE_CHAT_MESSAGE = "youtube_chat_message"
    YOUTUBE_MEMBERSHIP = "youtube_membership"
    YOUTUBE_MEMBERSHIP_MILESTONE = "youtube_membership_milestone"
    YOUTUBE_GIFT_MEMBERSHIP = "youtube_gift_membership"
    YOUTUBE_SUPERCHAT = "youtube_superchat"
    YOUTUBE_SUPERSTICKER = "youtube_supersticker"


class ComparisonOperator(Enum):
    """Comparison operators for conditions"""

    EQUAL = "equal"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"


class VariableType(Enum):
    """Types of variables that can be used in responses"""

    USERNAME = "username"
    AMOUNT = "amount"
    COUNT = "count"
    TIMESTAMP = "timestamp"
    TIER = "tier"
    MONTHS = "months"
    MESSAGE = "message"
    VIEWER_COUNT = "viewer_count"
    LEVEL = "level"
    COOLDOWN = "cooldown"
    USAGE_LEFT = "usage_left"


# Bumped when stored items need a one-time migration (see from_dict).
SCHEMA_VERSION = 2

# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

PERMISSION_LEVELS = {
    "everyone": 0,
    "subscriber": 1,
    "vip": 2,
    "moderator": 3,
    "broadcaster": 4,
}
PERMISSION_CHOICES = ("everyone", "subscriber", "vip", "moderator")


def normalize_permission(value: Any, mod_only: bool = False) -> str:
    """A valid command permission; legacy ``mod_only`` maps to ``moderator``."""
    text = str(value or "").strip().lower()
    aliases = {"mod": "moderator", "mods": "moderator", "moderators": "moderator",
               "sub": "subscriber", "subs": "subscriber", "subscribers": "subscriber",
               "vips": "vip", "all": "everyone"}
    text = aliases.get(text, text)
    if text in PERMISSION_CHOICES:
        return text
    if text == "broadcaster":
        return "moderator"
    return "moderator" if mod_only else "everyone"


def badge_set_ids(badges: Any) -> set:
    """Badge set ids from ``"broadcaster/1,subscriber/12"`` (or a list); None safe."""
    ids: set = set()
    if not badges:
        return ids
    if isinstance(badges, str):
        items: List[Any] = badges.split(",")
    elif isinstance(badges, dict):
        items = list(badges.keys())
    elif isinstance(badges, (list, tuple, set)):
        items = list(badges)
    else:
        return ids
    for item in items:
        if isinstance(item, dict):
            set_id = item.get("set_id") or item.get("id") or ""
        elif isinstance(item, str):
            set_id = item.split("/", 1)[0]
        else:
            set_id = getattr(item, "set_id", "") or ""
        set_id = str(set_id).strip().lower()
        if set_id:
            ids.add(set_id)
    return ids


def _broadcaster_user_id() -> str:
    twitch = sys.modules.get("modules.twitch")
    api = getattr(twitch, "twitch_api", None) if twitch else None
    return str(getattr(api, "user_id", "") or "")


def user_permission_level(data: Dict[str, Any]) -> str:
    """Highest level of the chatter: broadcaster > moderator > vip > subscriber > everyone."""
    data = data or {}
    ids = badge_set_ids(data.get("badges"))
    user_id = str(data.get("user_id") or data.get("userid") or "")
    if (
        "broadcaster" in ids
        or data.get("is_broadcaster")
        or (user_id and user_id == _broadcaster_user_id())
    ):
        return "broadcaster"
    if ids & {"moderator", "lead_moderator"} or data.get("is_mod") or data.get("is_moderator"):
        return "moderator"
    if "vip" in ids or data.get("is_vip"):
        return "vip"
    if ids & {"subscriber", "founder"} or data.get("is_subscriber"):
        return "subscriber"
    return "everyone"


def permission_allows(required: str, data: Dict[str, Any]) -> bool:
    need = PERMISSION_LEVELS.get(normalize_permission(required), 0)
    return PERMISSION_LEVELS[user_permission_level(data)] >= need


# --------------------------------------------------------------------------
# Small normalizers
# --------------------------------------------------------------------------


def _normalize_reply_targets(
    raw, *, default: Optional[List[str]] = None, allow_empty: bool = False
) -> List[str]:
    """Normalize reply_targets to a list of 'twitch' and/or 'youtube'.

    ``allow_empty`` keeps an explicit empty list (Discord-only / banner items).
    """
    if default is None:
        default = ["twitch"]
    allowed = {"twitch", "youtube"}
    if raw is None:
        return list(default)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return list(default)
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip().lower()
        if s == "all":
            return ["twitch", "youtube"]
        if s in allowed and s not in out:
            out.append(s)
    if out:
        return out
    return [] if allow_empty else list(default)


def _normalize_output_mode(raw) -> str:
    """``banner`` sends the response to the chat overlay instead of chat."""
    mode = str(raw or "chat").strip().lower()
    return "banner" if mode == "banner" else "chat"


def _resolve_output(kwargs: Dict[str, Any]) -> Tuple[List[str], str, List[Any]]:
    """reply_targets / output_mode / discord_channels for an item.

    Missing reply_targets -> ``["twitch"]``. An empty list is kept only when
    the item still produces output (Discord channels or banner mode).
    """
    output_mode = _normalize_output_mode(kwargs.get("output_mode", "chat"))
    raw_discord = kwargs.get("discord_channels") or []
    discord_channels = list(raw_discord) if isinstance(raw_discord, list) else []
    targets = _normalize_reply_targets(kwargs.get("reply_targets"), allow_empty=True)
    if not targets and not discord_channels and output_mode != "banner":
        targets = ["twitch"]
    return targets, output_mode, discord_channels


def _to_number(value: Any, default: Union[int, float] = 0) -> Union[int, float]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return int(number) if number.is_integer() else number


def _to_int(value: Any, default: int = 0) -> int:
    number = _to_number(value, default)
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return default


def clean_command_word(value: Any) -> str:
    """``"!Hello "`` -> ``"hello"`` (first word, no prefix, lowercase)."""
    text = str(value or "").strip()
    if "ValueChangeEventArguments" in text:
        return ""
    text = text.lstrip("!").strip()
    if not text:
        return ""
    return text.split()[0].lower()


def clean_aliases(raw: Any, exclude: str = "") -> List[str]:
    """Clean alias list: no ``!``, lowercase, unique, junk entries dropped."""
    if raw is None:
        return []
    if isinstance(raw, str):
        if "ValueChangeEventArguments" in raw:
            return []
        items: List[Any] = re.split(r"[,\s]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []
    out: List[str] = []
    for item in items:
        word = clean_command_word(item)
        if word and word != exclude and word not in out:
            out.append(word)
    return out


def normalize_argument_mappings(raw: Any) -> Dict[str, Dict[str, Any]]:
    """``{name: {"position": int>=1, "default": str}}`` from int / dict values."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for name, spec in raw.items():
        key = str(name or "").strip().strip("{}").strip()
        if not key or not re.match(r"^[\w\-]+$", key):
            continue
        default = ""
        if isinstance(spec, dict):
            position = _to_int(spec.get("position"), 0)
            default = str(spec.get("default") or "")
        else:
            position = _to_int(spec, 0)
        if position < 1:
            continue
        out[key] = {"position": position, "default": default}
    return out


def serialize_argument_mappings(mappings: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compact stored form: int, or the dict form only when a default is set."""
    out: Dict[str, Any] = {}
    for name, spec in (mappings or {}).items():
        if spec.get("default"):
            out[name] = {"position": int(spec["position"]), "default": str(spec["default"])}
        else:
            out[name] = int(spec["position"])
    return out


def normalize_lookup(raw: Any) -> Optional[Dict[str, Any]]:
    """A lookup config dict with a non-empty ``id``, or None."""
    if not isinstance(raw, dict):
        return None
    lookup_id = str(raw.get("id") or "").strip().lower()
    if not lookup_id:
        return None
    out = {k: v for k, v in raw.items() if k != "id"}
    out = {"id": lookup_id, **out}
    return out


_SPECIFIC_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def normalize_specific_time(value: Any) -> Optional[str]:
    """``"9:05"`` -> ``"09:05"``; None when it is not a valid 24-hour HH:MM."""
    match = _SPECIFIC_TIME.match(str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


TWITCH_MESSAGE_LIMIT = 500
TWITCH_MAX_PARTS = 3


def split_chat_message(text: str, limit: int = TWITCH_MESSAGE_LIMIT, max_parts: int = TWITCH_MAX_PARTS) -> List[str]:
    """Split a reply into chat-sized parts at word boundaries.

    At most *max_parts* messages are produced; text that still does not fit is
    cut at a word boundary and the last part ends with ``…``.
    """
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        if len(parts) == max_parts - 1:
            cut = rest[: limit - 1]
            space = cut.rfind(" ")
            if space > limit // 2:
                cut = cut[:space]
            parts.append(cut.rstrip() + "…")
            break
        window = rest[: limit + 1]
        space = window.rfind(" ")
        if space <= 0:
            piece, rest = rest[:limit], rest[limit:]
        else:
            piece, rest = rest[:space], rest[space + 1 :]
        parts.append(piece.rstrip())
        rest = rest.lstrip()
    return parts


def _clean_event_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Copy of producer data with None text fields as "" and id aliases filled."""
    ctx = dict(data or {})
    for key in ("message", "badges", "username", "user_login", "user_id"):
        if key in ctx and ctx[key] is None:
            ctx[key] = ""
    if not ctx.get("user_id") and ctx.get("userid"):
        ctx["user_id"] = str(ctx["userid"])
    return ctx


def choose_lookup_outcome(lookup_vars: Optional[Dict[str, Any]], fallback_text: str) -> str:
    """``"normal"``, ``"fallback"`` or ``"silent"`` after a lookup.

    * success -> normal reply;
    * failure (or follow_age: not following) with fallback text -> fallback;
    * follow_age not following without fallback -> normal reply (its variables
      say "not following");
    * any other failure without fallback -> silent (nothing is posted).
    """
    if not lookup_vars:
        return "normal"
    if lookup_vars.get("lookup_ok"):
        return "normal"
    if str(fallback_text or "").strip():
        return "fallback"
    if lookup_vars.get("lookup_reason") == "not_following":
        return "normal"
    return "silent"


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------


class TriggerCondition:
    """Condition for triggering a command or event (no UI yet; round-trips)."""

    def __init__(self, field: str, operator: ComparisonOperator, value: Any):
        self.field = field
        self.operator = operator
        self.value = value

    def evaluate(self, data: Dict[str, Any]) -> bool:
        """True when ``data[field] <operator> value``; a missing field is False.

        EQUAL / ordering compare numerically when both sides are numbers
        (floats included); otherwise EQUAL is case-insensitive text and the
        ordering operators are False. CONTAINS / STARTS_WITH / ENDS_WITH are
        always case-insensitive text. ``self.value`` is never modified.
        """
        try:
            if not isinstance(data, dict) or self.field not in data:
                return False
            field_value = data.get(self.field)
            if field_value is None:
                return False
            from .chatbot_custom_variables import compare_values

            return compare_values(field_value, self.operator.value, self.value)
        except Exception as e:
            logger.debug(
                "Error evaluating condition %s %s %s: %s",
                self.field,
                getattr(self.operator, "value", self.operator),
                self.value,
                e,
            )
            return False

    def to_dict(self) -> Dict[str, Any]:
        value = self.value
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            value = str(value)
        return {
            "field": str(self.field),
            "operator": getattr(self.operator, "value", str(self.operator)),
            "value": value,
        }

    @classmethod
    def from_any(cls, raw: Any) -> Optional["TriggerCondition"]:
        if isinstance(raw, TriggerCondition):
            return raw
        if not isinstance(raw, dict) or not raw.get("field"):
            return None
        try:
            operator = ComparisonOperator(raw.get("operator"))
        except ValueError:
            return None
        return cls(field=str(raw["field"]), operator=operator, value=raw.get("value"))


def _parse_conditions(raw: Any) -> List[TriggerCondition]:
    out = []
    for item in raw or []:
        cond = TriggerCondition.from_any(item)
        if cond is not None:
            out.append(cond)
    return out


def _sanitize_value(value):
    """Make a value JSON serializable."""
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    if hasattr(value, "value"):
        return _sanitize_value(value.value)
    try:
        return str(value)
    except Exception:
        return ""


def _track_stat(kind: str, username: Any, name: str) -> None:
    try:
        from .statistics_manager import get_statistics_manager

        stats_manager = get_statistics_manager()
        if kind == "command":
            stats_manager.increment_commands_triggered(username=username, command_name=name)
        else:
            stats_manager.increment_events_triggered(username=username, event_name=name)
    except Exception as e:
        logger.error("Error tracking %s statistics: %s", kind, e)


def _run_lookup_for(item: Any, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Blocking lookup for *item* (call off the event loop / outside locks)."""
    if not getattr(item, "lookup", None):
        return None
    from .chatbot_lookups import run_lookup_blocking

    kind = "event" if isinstance(item, ChatEvent) else "command"
    return run_lookup_blocking(item.lookup, context, kind=kind)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


class ChatCommand:
    """A chat command (``!name``).

    Stored fields (``to_dict`` keys): see ``to_dict``. New in schema 2:
    ``permission`` ("everyone" | "subscriber" | "vip" | "moderator"),
    ``lookup`` (dict | None), ``lookup_fallback`` (str), ``schema_version``.
    ``mod_only`` is kept in sync with ``permission == "moderator"``.
    """

    def __init__(self, **kwargs):
        self.command_id = kwargs.get("command_id", "")
        self.name = kwargs.get("name", "")
        self.description = kwargs.get("description", "")
        self.command_name = clean_command_word(kwargs.get("command_name", ""))
        self.aliases = clean_aliases(kwargs.get("aliases", []), exclude=self.command_name)
        self.response_text = kwargs.get("response_text", "") or ""
        self.permission = normalize_permission(
            kwargs.get("permission"), mod_only=bool(kwargs.get("mod_only", False))
        )
        self.cooldown = max(0, _to_number(kwargs.get("cooldown", 0), 0))
        command_type = kwargs.get("command_type", CommandType.BASIC)
        if not isinstance(command_type, CommandType):
            try:
                command_type = CommandType(str(command_type))
            except ValueError:
                command_type = CommandType.BASIC
        self.command_type = command_type
        self.usage_limit = max(0, _to_int(kwargs.get("usage_limit", 0), 0))
        # Legacy repeat fields: still read, migrated into interval events by
        # the manager; nothing schedules repeating commands any more.
        self.repeating_enabled = bool(kwargs.get("repeating_enabled", False))
        self.repeat_count = kwargs.get("repeat_count", 1)
        self.repeat_interval = kwargs.get("repeat_interval", 0)
        # True: counter survives an app restart; False: reset to 0 on start.
        self.persistent_counter = bool(kwargs.get("persistent_counter", True))
        self.reset_command = clean_command_word(kwargs.get("reset_command", ""))
        self.enabled = bool(kwargs.get("enabled", True))

        # Legacy API fields (read for compatibility; migrated into ``lookup``).
        self.api_enabled = bool(kwargs.get("api_enabled", False))
        self.api_endpoint = kwargs.get("api_endpoint", "") or ""
        self.api_method = kwargs.get("api_method", "GET") or "GET"
        headers = kwargs.get("api_headers", {})
        if isinstance(headers, str):
            try:
                import json

                headers = json.loads(headers)
            except Exception:
                headers = {}
        self.api_headers = headers if isinstance(headers, dict) else {}
        self.api_body = kwargs.get("api_body", "") or ""
        self.api_response_format = kwargs.get("api_response_format", "") or ""
        self.api_parameters = kwargs.get("api_parameters", {}) or {}
        self.api_endpoint_select = kwargs.get("api_endpoint_select", "") or ""
        self.api_variable_processing = kwargs.get("api_variable_processing", []) or []

        self.lookup = normalize_lookup(kwargs.get("lookup"))
        if self.lookup is None and self.api_enabled and str(self.api_endpoint).strip():
            from .chatbot_lookups import migrate_legacy_api_fields

            self.lookup = migrate_legacy_api_fields(self)
            self.api_enabled = False
        self.lookup_fallback = str(kwargs.get("lookup_fallback") or "")

        # Runtime state
        self.counter_value = _to_int(kwargs.get("counter_value", 0), 0)
        self.usage_count = _to_int(kwargs.get("usage_count", 0), 0)
        self.last_used = _to_number(kwargs.get("last_used", 0), 0)
        self.trigger_count = _to_int(kwargs.get("trigger_count", 0), 0)
        self.last_triggered = _to_number(kwargs.get("last_triggered", 0), 0)

        self.conditions = _parse_conditions(kwargs.get("conditions", []))
        # {name: {"position": 1-based int, "default": template}}
        self.argument_mappings = normalize_argument_mappings(kwargs.get("argument_mappings", {}))
        self.reply_targets, self.output_mode, self.discord_channels = _resolve_output(kwargs)

    # ``mod_only`` mirrors the permission for old callers.
    @property
    def mod_only(self) -> bool:
        return self.permission == "moderator"

    @mod_only.setter
    def mod_only(self, value: bool) -> None:
        if value:
            self.permission = "moderator"
        elif self.permission == "moderator":
            self.permission = "everyone"

    def all_names(self) -> List[str]:
        return [n for n in [self.command_name, *self.aliases] if n]

    def should_trigger(self, data: Dict[str, Any]) -> bool:
        """Enabled and every condition passes."""
        if not self.enabled:
            return False
        return all(condition.evaluate(data) for condition in self.conditions)

    def can_use(self, data: Dict[str, Any]) -> tuple:
        """(allowed, reason): permission, cooldown and usage limit checks."""
        if not self.enabled:
            return False, "Command is disabled"
        if not permission_allows(self.permission, data):
            return False, f"Requires {self.permission}"
        current_time = time.time()
        if self.cooldown > 0 and current_time - self.last_used < self.cooldown:
            remaining = int(self.cooldown - (current_time - self.last_used))
            return False, f"Command is on cooldown ({remaining}s remaining)"
        if self.usage_limit > 0 and self.usage_count >= self.usage_limit:
            return False, "Command has reached its usage limit"
        return True, ""

    def begin_use(self, data: Dict[str, Any], *, track_stats: bool = True) -> None:
        """Consume one use: counters, cooldown start, counter increment, stats."""
        current_time = time.time()
        self.usage_count += 1
        self.trigger_count += 1
        self.last_used = current_time
        self.last_triggered = current_time
        if self.command_type == CommandType.COUNTER:
            self.counter_value += 1
        if track_stats:
            _track_stat("command", (data or {}).get("username"), self.command_name)

    def build_context(self, data: Dict[str, Any], lookup_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Variables for this command's reply.

        Precedence (later wins): lookup variables < chat data < command
        variables < argument names.
        """
        ctx: Dict[str, Any] = dict(lookup_vars or {})
        ctx.update(_clean_event_data(data))
        ctx.setdefault("username", "Unknown")
        ctx.setdefault("user_login", str(ctx.get("username") or "").lower())
        args = ctx.get("command_args")
        if not isinstance(args, (list, tuple)):
            message = str(ctx.get("message") or "").strip()
            words = message.split()
            args = words[1:] if words and words[0].startswith("!") else []
        args = [str(a) for a in args]
        ctx["command_args"] = args
        ctx.setdefault("command_message", " ".join(args))
        ctx["command_first_word"] = args[0] if args else ""
        ctx["command_last_word"] = args[-1] if args else ""
        ctx["chatter_id"] = str(ctx.get("user_id") or "")
        ctx["chatter_login"] = str(ctx.get("user_login") or "")
        ctx["command_name"] = self.command_name
        ctx["cooldown"] = _stringify(self.cooldown)
        if self.usage_limit > 0:
            ctx["usage_left"] = str(max(0, self.usage_limit - self.usage_count))
        else:
            ctx["usage_left"] = "unlimited"
        if self.command_type == CommandType.COUNTER:
            ctx["count"] = self.counter_value
        if self.command_type == CommandType.RESET:
            ctx.setdefault("target_command", "")
            ctx.setdefault("target_count", "")
        for name, spec in self.argument_mappings.items():
            index = spec["position"] - 1
            if 0 <= index < len(args):
                ctx[name] = args[index]
            else:
                default = spec.get("default") or ""
                ctx[name] = render_template(default, ctx) if default else ""
        return ctx

    def render_response(
        self,
        data: Dict[str, Any],
        lookup_vars: Optional[Dict[str, Any]] = None,
        *,
        use_fallback: bool = False,
    ) -> str:
        text = self.lookup_fallback if use_fallback else self.response_text
        return render_template(text, self.build_context(data, lookup_vars))

    def respond_after_lookup(self, data: Dict[str, Any], lookup_vars: Optional[Dict[str, Any]]) -> str:
        """Reply text after a lookup ran ("" when the outcome is silent)."""
        outcome = choose_lookup_outcome(lookup_vars, self.lookup_fallback)
        if outcome == "silent":
            logger.debug(
                "Command %s lookup failed (%s); staying silent",
                self.command_name,
                (lookup_vars or {}).get("lookup_error"),
            )
            return ""
        return self.render_response(data, lookup_vars, use_fallback=outcome == "fallback")

    def use_command(self, data: Dict[str, Any]) -> str:
        """Consume a use and return the reply (runs a lookup synchronously)."""
        self.begin_use(data)
        lookup_vars = _run_lookup_for(self, self.build_context(data))
        if lookup_vars is None:
            return self.render_response(data)
        return self.respond_after_lookup(data, lookup_vars)

    def reset_counter(self):
        """Reset the command counter"""
        self.counter_value = 0
        logger.info("Reset counter for command %s", self.command_name)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return _sanitize_value(
            {
                "command_id": self.command_id,
                "name": self.name,
                "description": self.description,
                "command_name": self.command_name,
                "aliases": list(self.aliases),
                "response_text": self.response_text,
                "permission": self.permission,
                "mod_only": self.mod_only,
                "cooldown": self.cooldown,
                "command_type": self.command_type.value,
                "usage_limit": self.usage_limit,
                "repeating_enabled": self.repeating_enabled,
                "repeat_count": self.repeat_count,
                "repeat_interval": self.repeat_interval,
                "persistent_counter": self.persistent_counter,
                "reset_command": self.reset_command,
                "enabled": self.enabled,
                "lookup": self.lookup,
                "lookup_fallback": self.lookup_fallback,
                # Legacy API fields (kept readable; ``lookup`` is authoritative)
                "api_enabled": self.api_enabled,
                "api_endpoint": self.api_endpoint,
                "api_method": self.api_method,
                "api_headers": self.api_headers,
                "api_body": self.api_body,
                "api_response_format": self.api_response_format,
                "api_parameters": self.api_parameters,
                "api_endpoint_select": self.api_endpoint_select,
                "api_variable_processing": self.api_variable_processing,
                # Runtime state
                "counter_value": self.counter_value,
                "usage_count": self.usage_count,
                "last_used": self.last_used,
                "trigger_count": self.trigger_count,
                "last_triggered": self.last_triggered,
                "conditions": [c.to_dict() for c in self.conditions],
                "argument_mappings": serialize_argument_mappings(self.argument_mappings),
                "reply_targets": list(self.reply_targets),
                "discord_channels": list(self.discord_channels or []),
                "output_mode": self.output_mode,
                "schema_version": SCHEMA_VERSION,
            }
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCommand":
        """Create from a stored dict (migrates schema-1 items)."""
        data = dict(data or {})
        if _to_int(data.get("schema_version"), 1) < 2:
            # persistent_counter used to do nothing (counters always persisted).
            data["persistent_counter"] = True
        return cls(**data)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

_CHAT_EVENT_TYPES = (EventType.CHAT_MESSAGE, EventType.YOUTUBE_CHAT_MESSAGE)
CHAT_MATCH_TYPES = ("exact", "starts_with", "contains")


def _number_or_none(value: Any) -> Optional[float]:
    from .chatbot_custom_variables import as_number

    return as_number(value)


def _at_least(data: Dict[str, Any], keys, minimum: float) -> bool:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            number = _number_or_none(data[key])
            return number is not None and number >= minimum
    return False


def _equals_number(data: Dict[str, Any], key: str, wanted: float) -> bool:
    number = _number_or_none(data.get(key))
    return number is not None and number == wanted


def chat_text_matches(message: Any, pattern: Any, match_type: str) -> bool:
    """Case-insensitive, trimmed chat matching. Empty pattern matches everything."""
    wanted = str(pattern or "").strip().casefold()
    if not wanted:
        return True
    text = str(message or "").strip().casefold()
    if match_type == "starts_with":
        return text.startswith(wanted)
    if match_type == "contains":
        return wanted in text
    return text == wanted


class ChatEvent:
    """An automatic response to a Twitch / YouTube / tip / time event.

    New in schema 2: ``lookup`` (dict | None; target "event_user" by default),
    ``lookup_fallback`` (str). ``interval`` (seconds) only matters for
    ``EventType.INTERVAL``; ``specific_time`` is "HH:MM" (24 h, local).
    """

    def __init__(self, **kwargs):
        self.event_id = kwargs.get("event_id", "")
        self.name = kwargs.get("name", "")
        self.description = kwargs.get("description", "")
        event_type = kwargs.get("event_type", EventType.FOLLOW)
        if not isinstance(event_type, EventType):
            try:
                event_type = EventType(str(event_type))
            except ValueError:
                event_type = EventType.FOLLOW
        self.event_type = event_type
        self.response_text = kwargs.get("response_text", "") or ""
        self.enabled = bool(kwargs.get("enabled", True))

        # Seconds between posts; only used by EventType.INTERVAL.
        self.interval = max(0, _to_number(kwargs.get("interval", 0), 0))

        # Runtime state
        self.trigger_count = _to_int(kwargs.get("trigger_count", 0), 0)
        self.last_triggered = _to_number(kwargs.get("last_triggered", 0), 0)

        self.conditions = _parse_conditions(kwargs.get("conditions", []))
        self.argument_mappings = normalize_argument_mappings(kwargs.get("argument_mappings", {}))
        self.reply_targets, self.output_mode, self.discord_channels = _resolve_output(kwargs)

        self.lookup = normalize_lookup(kwargs.get("lookup"))
        self.lookup_fallback = str(kwargs.get("lookup_fallback") or "")

        # Event-specific settings
        raw_time = kwargs.get("specific_time", "") or ""
        self.specific_time = normalize_specific_time(raw_time) or str(raw_time)
        self.chat_message_text = str(kwargs.get("chat_message_text", "") or "")
        match_type = str(kwargs.get("chat_message_match_type", "exact") or "exact")
        self.chat_message_match_type = match_type if match_type in CHAT_MATCH_TYPES else "exact"
        self.bits_quantity = _to_int(kwargs.get("bits_quantity", 0))  # 0 = any
        self.hype_train_level = _to_int(kwargs.get("hype_train_level", 0))
        self.hype_train_end_level = _to_int(kwargs.get("hype_train_end_level", 0))
        self.gift_sub_quantity = _to_int(kwargs.get("gift_sub_quantity", 0))
        self.gift_sub_tier = _to_int(kwargs.get("gift_sub_tier", 0))
        self.resub_months = _to_int(kwargs.get("resub_months", 0))
        self.resub_tier = _to_int(kwargs.get("resub_tier", 0))
        self.sub_tier = _to_int(kwargs.get("sub_tier", 0))
        self.donation_amount = float(_to_number(kwargs.get("donation_amount", 0.0), 0.0))
        self.raid_viewer_count = _to_int(kwargs.get("raid_viewer_count", 0))
        self.raid_raider_name = str(kwargs.get("raid_raider_name", "") or "")
        self.channel_point_reward_name = str(kwargs.get("channel_point_reward_name", "") or "")

    def repeats(self) -> bool:
        """True only for an enabled-or-not INTERVAL event with a positive interval."""
        return self.event_type == EventType.INTERVAL and self.interval > 0

    def should_trigger(self, data: Dict[str, Any]) -> bool:
        """Conditions plus the per-type filters (0 / empty filter = any)."""
        if not self.enabled:
            return False
        data = data or {}
        for condition in self.conditions:
            if not condition.evaluate(data):
                return False

        etype = self.event_type
        if etype == EventType.SPECIFIC_TIME:
            return normalize_specific_time(self.specific_time) is not None
        if etype in _CHAT_EVENT_TYPES:
            return chat_text_matches(
                data.get("message"), self.chat_message_text, self.chat_message_match_type
            )
        if etype == EventType.BITS:
            return self.bits_quantity <= 0 or _at_least(data, ("amount",), self.bits_quantity)
        if etype == EventType.HYPE_TRAIN_PROGRESS:
            return self.hype_train_level <= 0 or _at_least(data, ("level",), self.hype_train_level)
        if etype == EventType.HYPE_TRAIN_END:
            return self.hype_train_end_level <= 0 or _at_least(
                data, ("level",), self.hype_train_end_level
            )
        if etype == EventType.GIFT_SUBSCRIPTION:
            # Gifts in *this* event (total_gifts is the gifter's lifetime total).
            if self.gift_sub_quantity > 0 and not _at_least(
                data, ("quantity", "amount", "gift_qty", "total_gifts"), self.gift_sub_quantity
            ):
                return False
            if self.gift_sub_tier > 0 and not _equals_number(data, "tier", self.gift_sub_tier):
                return False
            return True
        if etype == EventType.RESUBSCRIPTION:
            if self.resub_months > 0 and not _at_least(
                data, ("months", "cumulative_months"), self.resub_months
            ):
                return False
            if self.resub_tier > 0 and not _equals_number(data, "tier", self.resub_tier):
                return False
            return True
        if etype == EventType.SUBSCRIPTION:
            return self.sub_tier <= 0 or _equals_number(data, "tier", self.sub_tier)
        if etype == EventType.DONATION:
            return self.donation_amount <= 0 or _at_least(data, ("amount",), self.donation_amount)
        if etype == EventType.RAID:
            if self.raid_viewer_count > 0 and not _at_least(
                data, ("viewer_count",), self.raid_viewer_count
            ):
                return False
            wanted = self.raid_raider_name.strip().lstrip("@").casefold()
            if wanted:
                names = {
                    str(data.get(k) or "").strip().lstrip("@").casefold()
                    for k in ("raider_name", "username", "user_login", "raider_login")
                }
                if wanted not in names:
                    return False
            return True
        if etype == EventType.CHANNEL_POINT_REDEMPTION:
            wanted = self.channel_point_reward_name.strip().casefold()
            if wanted:
                reward = str(data.get("reward_title") or data.get("reward_name") or "")
                if reward.strip().casefold() != wanted:
                    return False
            return True
        return True

    def begin_trigger(self, data: Dict[str, Any], *, track_stats: bool = True) -> None:
        current_time = time.time()
        self.trigger_count += 1
        self.last_triggered = current_time
        if track_stats:
            _track_stat("event", (data or {}).get("username"), self.name)

    def build_context(self, data: Dict[str, Any], lookup_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Variables for the reply: lookup variables < event data < argument names."""
        ctx: Dict[str, Any] = dict(lookup_vars or {})
        ctx.update(_clean_event_data(data))
        ctx.setdefault("username", "Unknown")
        ctx.setdefault("user_login", str(ctx.get("username") or "").lower())
        ctx["event_name"] = self.name
        args = ctx.get("command_args")
        if isinstance(args, (list, tuple)):
            args = [str(a) for a in args]
            ctx["command_args"] = args
            ctx.setdefault("command_message", " ".join(args))
            ctx["command_first_word"] = args[0] if args else ""
            ctx["command_last_word"] = args[-1] if args else ""
        else:
            args = []
        for name, spec in self.argument_mappings.items():
            index = spec["position"] - 1
            if 0 <= index < len(args):
                ctx[name] = args[index]
            else:
                default = spec.get("default") or ""
                ctx[name] = render_template(default, ctx) if default else ""
        return ctx

    def render_response(
        self,
        data: Dict[str, Any],
        lookup_vars: Optional[Dict[str, Any]] = None,
        *,
        use_fallback: bool = False,
    ) -> str:
        text = self.lookup_fallback if use_fallback else self.response_text
        return render_template(text, self.build_context(data, lookup_vars))

    def respond_after_lookup(self, data: Dict[str, Any], lookup_vars: Optional[Dict[str, Any]]) -> str:
        outcome = choose_lookup_outcome(lookup_vars, self.lookup_fallback)
        if outcome == "silent":
            logger.debug("Event %s lookup failed; staying silent", self.name)
            return ""
        return self.render_response(data, lookup_vars, use_fallback=outcome == "fallback")

    def trigger_event(self, data: Dict[str, Any]) -> str:
        """Count the trigger and return the reply (runs a lookup synchronously)."""
        self.begin_trigger(data)
        lookup_vars = _run_lookup_for(self, self.build_context(data))
        if lookup_vars is None:
            return self.render_response(data)
        return self.respond_after_lookup(data, lookup_vars)

    def set_interval_from_string(self, interval_str: str) -> bool:
        """Set interval from hh:mm:ss format string"""
        try:
            if not interval_str or interval_str.strip() == "":
                self.interval = 0
                return True

            parts = interval_str.strip().split(":")
            if len(parts) != 3:
                return False

            hours, minutes, seconds = map(int, parts)

            if hours < 0 or minutes < 0 or seconds < 0 or minutes > 59 or seconds > 59:
                return False

            total_seconds = hours * 3600 + minutes * 60 + seconds
            self.interval = total_seconds
            return True
        except (ValueError, AttributeError):
            return False

    def get_interval_string(self) -> str:
        """Get interval as hh:mm:ss format string"""
        if not self.interval:
            return ""
        total = int(self.interval)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return _sanitize_value(
            {
                "event_id": self.event_id,
                "name": self.name,
                "description": self.description,
                "event_type": self.event_type.value,
                "response_text": self.response_text,
                "enabled": self.enabled,
                "interval": self.interval,
                "trigger_count": self.trigger_count,
                "last_triggered": self.last_triggered,
                "conditions": [c.to_dict() for c in self.conditions],
                "lookup": self.lookup,
                "lookup_fallback": self.lookup_fallback,
                # Event-specific settings
                "specific_time": self.specific_time,
                "chat_message_text": self.chat_message_text,
                "chat_message_match_type": self.chat_message_match_type,
                "bits_quantity": self.bits_quantity,
                "hype_train_level": self.hype_train_level,
                "hype_train_end_level": self.hype_train_end_level,
                "gift_sub_quantity": self.gift_sub_quantity,
                "gift_sub_tier": self.gift_sub_tier,
                "resub_months": self.resub_months,
                "resub_tier": self.resub_tier,
                "sub_tier": self.sub_tier,
                "donation_amount": self.donation_amount,
                "raid_viewer_count": self.raid_viewer_count,
                "raid_raider_name": self.raid_raider_name,
                "channel_point_reward_name": self.channel_point_reward_name,
                "argument_mappings": serialize_argument_mappings(self.argument_mappings),
                "reply_targets": list(self.reply_targets),
                "discord_channels": list(self.discord_channels or []),
                "output_mode": self.output_mode,
                "schema_version": SCHEMA_VERSION,
            }
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatEvent":
        """Create from dictionary"""
        return cls(**dict(data or {}))


class Quote:
    """Represents a quote in the quote system"""

    def __init__(self, **kwargs):
        self.quote_id = kwargs.get("quote_id", "")
        self.quote_number = kwargs.get("quote_number", 0)
        self.text = kwargs.get("text", "")
        self.author = kwargs.get("author", "")
        self.date_added = kwargs.get("date_added", time.time())
        self.added_by = kwargs.get("added_by", "")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "quote_id": self.quote_id,
            "quote_number": self.quote_number,
            "text": self.text,
            "author": self.author,
            "date_added": self.date_added,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Quote":
        """Create from dictionary"""
        return cls(**data)

    def format_quote(self) -> str:
        """Format the quote for display"""
        formatted = f'"{self.text}"'
        if self.author:
            formatted += f" - {self.author}"
        return formatted

    def get_search_text(self) -> str:
        """Get text for searching (combines text and author)"""
        return f"{self.text} {self.author}".lower()


class Greeting:
    """Represents a user greeting in the greetings system"""

    def __init__(self, **kwargs):
        self.greeting_id = kwargs.get("greeting_id", "")
        self.user_id = kwargs.get("user_id", "")  # Primary key for tracking users
        self.username = kwargs.get("username", "")  # Current username (can change)
        self.greeting_text = kwargs.get("greeting_text", "")
        self.enabled = kwargs.get("enabled", True)
        self.greeted_flag = kwargs.get(
            "greeted_flag", False
        )  # Boolean flag for greeting status
        self.last_greeted = kwargs.get(
            "last_greeted", 0
        )  # Timestamp when last greeted (legacy)
        self.date_created = kwargs.get("date_created", time.time())
        self.last_username_update = kwargs.get("last_username_update", time.time())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "greeting_id": self.greeting_id,
            "user_id": self.user_id,
            "username": self.username,
            "greeting_text": self.greeting_text,
            "enabled": self.enabled,
            "greeted_flag": self.greeted_flag,
            "last_greeted": self.last_greeted,
            "date_created": self.date_created,
            "last_username_update": self.last_username_update,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Greeting":
        """Create from dictionary"""
        return cls(**data)

    def should_greet(self, current_time: float, cooldown_hours: int = 24) -> bool:
        """Check if user should be greeted based on time cooldown"""
        if not self.enabled:
            return False

        if self.last_greeted == 0:  # Never greeted before
            return True

        # Check if enough time has passed since last greeting
        hours_since_last_greet = (current_time - self.last_greeted) / 3600
        return hours_since_last_greet >= cooldown_hours

    def update_last_greeted(self, current_time: Union[float, None] = None):
        """Update the last greeted timestamp"""
        if current_time is None:
            current_time = time.time()
        self.last_greeted = current_time

    def update_username(self, new_username: str):
        """Update username and track when it was last updated"""
        if new_username != self.username:
            self.username = new_username
            self.last_username_update = time.time()

    def get_display_text(self) -> str:
        """Get formatted display text for the greeting"""
        return f"@{self.username}: {self.greeting_text}"

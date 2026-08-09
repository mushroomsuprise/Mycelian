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

import asyncio
import logging
import re
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def get_statistics_value(stat_name: str) -> str:
    """Get a statistics value by name from the StatisticsManager"""
    try:
        # Handle both 'stats.' prefixed and non-prefixed calls
        if stat_name.startswith("stats."):
            stat_name = stat_name[6:]  # Remove 'stats.' prefix

        from .statistics_manager import get_statistics_manager

        manager = get_statistics_manager()
        all_stats = manager.get_all_statistics()

        # Handle nested statistics (e.g., 'alerts.bit_alerts_played')
        if "." in stat_name:
            parts = stat_name.split(".")
            current_obj = all_stats
            try:
                for part in parts:
                    if isinstance(current_obj, dict):
                        current_obj = current_obj.get(part, 0)
                    else:
                        return "0"
                return str(current_obj) if current_obj is not None else "0"
            except:
                return "0"
        else:
            # Handle top-level statistics
            return str(all_stats.get(stat_name, "0"))
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


TIME_VAR_PATTERN = re.compile(r"\{time(?:\.([^}]+))?\}", re.IGNORECASE)
_TIME_LAYOUT_PATTERN = re.compile(r"^(h{1,2}):mm(?::ss)?$", re.IGNORECASE)
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
                hour_fmt = "%I" if use_padded else "%-I"
            else:
                hour_fmt = "%H" if use_padded else "%-H"
            # %-H / %-I are not supported on Windows; fall back to padded.
            try:
                time_str = now.strftime(
                    f"{hour_fmt}:%M" + (":%S" if include_seconds else "")
                )
            except ValueError:
                hour_fmt = "%I" if hour_format == 12 else "%H"
                time_str = now.strftime(
                    f"{hour_fmt}:%M" + (":%S" if include_seconds else "")
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


def _normalize_reply_targets(
    raw, *, default: Optional[List[str]] = None
) -> List[str]:
    """Normalize reply_targets to a list of 'twitch' and/or 'youtube'."""
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
    return out if out else list(default)


class TriggerCondition:
    """Condition for triggering a command or event"""

    def __init__(self, field: str, operator: ComparisonOperator, value: Any):
        self.field = field
        self.operator = operator
        self.value = value

    def evaluate(self, data: Dict[str, Any]) -> bool:
        """Evaluate the condition against provided data"""
        try:
            field_value = data.get(self.field)

            if field_value is None:
                return False

            # Type conversion for comparison
            if isinstance(field_value, str) and self.value.isdigit():
                field_value = int(field_value)
                self.value = int(self.value)
            elif (
                isinstance(field_value, int)
                and isinstance(self.value, str)
                and self.value.isdigit()
            ):
                self.value = int(self.value)

            # Perform comparison based on operator
            if self.operator == ComparisonOperator.EQUAL:
                return field_value == self.value
            elif self.operator == ComparisonOperator.GREATER_THAN_OR_EQUAL:
                return field_value >= self.value
            elif self.operator == ComparisonOperator.LESS_THAN_OR_EQUAL:
                return field_value <= self.value
            elif self.operator == ComparisonOperator.GREATER_THAN:
                return field_value > self.value
            elif self.operator == ComparisonOperator.LESS_THAN:
                return field_value < self.value
            elif self.operator == ComparisonOperator.CONTAINS:
                return str(self.value).lower() in str(field_value).lower()
            elif self.operator == ComparisonOperator.STARTS_WITH:
                return str(field_value).lower().startswith(str(self.value).lower())
            elif self.operator == ComparisonOperator.ENDS_WITH:
                return str(field_value).lower().endswith(str(self.value).lower())

        except Exception as e:
            logger.error(
                "Error evaluating condition %s %s %s: %s",
                self.field,
                self.operator.value,
                self.value,
                e,
            )
            return False

        return False


class ChatCommand:
    """Represents a chat command"""

    def __init__(self, **kwargs):
        self.command_id = kwargs.get("command_id", "")
        self.name = kwargs.get("name", "")
        self.description = kwargs.get("description", "")
        self.command_name = kwargs.get("command_name", "")
        self.aliases = kwargs.get("aliases", [])
        self.response_text = kwargs.get("response_text", "")
        self.mod_only = kwargs.get("mod_only", False)
        self.cooldown = kwargs.get("cooldown", 0)
        self.command_type = kwargs.get("command_type", CommandType.BASIC)
        self.usage_limit = kwargs.get("usage_limit", 0)
        self.repeating_enabled = kwargs.get("repeating_enabled", False)
        self.repeat_count = kwargs.get("repeat_count", 1)
        self.repeat_interval = kwargs.get("repeat_interval", 0)
        self.persistent_counter = kwargs.get("persistent_counter", False)
        self.reset_command = kwargs.get("reset_command", "")
        self.enabled = kwargs.get("enabled", True)

        # API Call functionality
        self.api_enabled = kwargs.get("api_enabled", False)
        self.api_endpoint = kwargs.get("api_endpoint", "")
        self.api_method = kwargs.get("api_method", "GET")
        self.api_headers = kwargs.get("api_headers", {})
        self.api_body = kwargs.get("api_body", "")
        self.api_response_format = kwargs.get("api_response_format", "")
        self.api_parameters = kwargs.get("api_parameters", {})
        self.api_endpoint_select = kwargs.get("api_endpoint_select", "")
        self.api_variable_processing = kwargs.get(
            "api_variable_processing", []
        )  # List of processing expressions

        # Runtime state
        self.counter_value = kwargs.get("counter_value", 0)
        self.usage_count = kwargs.get("usage_count", 0)
        self.last_used = kwargs.get("last_used", 0)
        self.trigger_count = kwargs.get("trigger_count", 0)
        self.last_triggered = kwargs.get("last_triggered", 0)

        # Conditions
        self.conditions = kwargs.get("conditions", [])

        # Argument mappings for custom variable names
        # Format: {"variable_name": position} where position is 1-based
        self.argument_mappings = kwargs.get("argument_mappings", {})
        # Where to send the response: "twitch", "youtube" (missing → twitch only)
        self.reply_targets = _normalize_reply_targets(
            kwargs.get("reply_targets"), default=["twitch"]
        )
        # Discord channel targets (independent of reply_targets):
        # [{guild_id, channel_id, guild_name?, channel_name?}, ...]
        raw_discord = kwargs.get("discord_channels") or []
        self.discord_channels = (
            list(raw_discord) if isinstance(raw_discord, list) else []
        )

    def should_trigger(self, data: Dict[str, Any]) -> bool:
        """Check if this command should trigger based on conditions"""
        if not self.enabled:
            return False

        # Check all conditions
        for condition in self.conditions:
            if not condition.evaluate(data):
                return False

        return True

    def can_use(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """Check if command can be used, returns (can_use, reason)"""
        if not self.enabled:
            return False, "Command is disabled"

        # Check cooldown
        current_time = time.time()
        if self.cooldown > 0 and current_time - self.last_used < self.cooldown:
            remaining = int(self.cooldown - (current_time - self.last_used))
            return False, f"Command is on cooldown ({remaining}s remaining)"

        # Check usage limit
        if self.usage_limit > 0 and self.usage_count >= self.usage_limit:
            return False, "Command has reached its usage limit"

        # Check mod only
        if self.mod_only:
            is_mod = (
                data.get("is_mod", False)
                or data.get("badges", "").find("moderator") >= 0
            )
            if not is_mod:
                return False, "Command is mod-only"

        return True, ""

    def use_command(self, data: Dict[str, Any]) -> str:
        """Use the command and return the processed response"""
        current_time = time.time()

        # Update counters
        self.usage_count += 1
        self.trigger_count += 1
        self.last_used = current_time
        self.last_triggered = current_time

        if self.command_type == CommandType.COUNTER:
            self.counter_value += 1

        # Track statistics
        try:
            from .statistics_manager import get_statistics_manager

            stats_manager = get_statistics_manager()
            username = data.get("username")
            stats_manager.increment_commands_triggered(
                username=username, command_name=self.command_name
            )
            logger.debug("Tracked command trigger for: %s", self.command_name)
        except Exception as e:
            logger.error("Error tracking command statistics: %s", e)

        # Handle API calls if enabled
        api_data = {}
        if self.api_enabled and self.api_endpoint:
            try:
                api_response = self._execute_api_call(data)
                logger.debug("API call completed for command %s", self.command_name)

                # Process API variables with custom expressions
                if self.api_variable_processing:
                    processed_vars = self._process_api_variables(api_response, data)
                    api_data = {**api_response, **processed_vars}
                else:
                    api_data = api_response

            except Exception as e:
                logger.error(
                    "Error executing API call for command %s: %s", self.command_name, e
                )
                # Continue with empty API data, command will still work with regular variables

        # Merge API data with regular data for variable processing
        combined_data = {**data, **api_data}

        # Process response with variables (including API response variables)
        response = self._process_variables(self.response_text, combined_data)
        return response

    def _process_variables(self, text: str, data: Dict[str, Any]) -> str:
        """Process variables in response text"""
        processed = text

        # Basic variables
        processed = processed.replace(
            "{username}", str(data.get("username", "Unknown"))
        )
        processed = processed.replace(
            "{timestamp}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        processed = processed.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
        # Resolve {time} / {time.filters} before the generic dotted-path walker
        processed = replace_time_variables(processed)

        # Command-specific variables
        if self.command_type == CommandType.COUNTER:
            processed = processed.replace("{count}", str(self.counter_value))

        if self.cooldown > 0:
            processed = processed.replace("{cooldown}", str(self.cooldown))

        if self.usage_limit > 0:
            uses_left = max(0, self.usage_limit - self.usage_count)
            processed = processed.replace("{usage_left}", str(uses_left))

        # Command message variables
        if "command_message" in data:
            processed = processed.replace(
                "{command_message}", str(data.get("command_message", ""))
            )
        if "command_args" in data:
            command_args = data.get("command_args", [])
            if command_args:
                processed = processed.replace(
                    "{command_first_word}", str(command_args[0])
                )
                processed = processed.replace(
                    "{command_last_word}", str(command_args[-1])
                )
            else:
                processed = processed.replace("{command_first_word}", "")
                processed = processed.replace("{command_last_word}", "")

            # Handle {command_word_N} variables
            import re

            word_pattern = re.compile(r"\{command_word_(\d+)\}")

            def replace_word_var(match):
                index = int(match.group(1)) - 1  # Convert to 0-based indexing
                if 0 <= index < len(command_args):
                    return str(command_args[index])
                return ""

            processed = word_pattern.sub(replace_word_var, processed)

        # Event-specific variables
        if "amount" in data:
            processed = processed.replace("{amount}", str(data.get("amount", "0")))
        if "tier" in data:
            processed = processed.replace("{tier}", str(data.get("tier", "1")))
        if "months" in data:
            processed = processed.replace("{months}", str(data.get("months", "1")))
        if "message" in data:
            processed = processed.replace("{message}", str(data.get("message", "")))
        if "viewer_count" in data:
            processed = processed.replace(
                "{viewer_count}", str(data.get("viewer_count", "0"))
            )
        if "level" in data:
            processed = processed.replace("{level}", str(data.get("level", "1")))

        # Handle custom argument mappings (e.g., {city} -> command_args[0])
        if self.argument_mappings and "command_args" in data:
            command_args = data.get("command_args", [])
            for var_name, position in self.argument_mappings.items():
                if isinstance(position, int) and position >= 1:
                    # Position is 1-based, convert to 0-based index
                    arg_index = position - 1
                    if 0 <= arg_index < len(command_args):
                        # Valid position, replace with argument value
                        processed = processed.replace(
                            f"{{{var_name}}}", str(command_args[arg_index])
                        )
                    else:
                        # Out of bounds, replace with empty string
                        processed = processed.replace(f"{{{var_name}}}", "")

        # Handle processed variables with dot notation (e.g., {account_age.days})
        import re

        processed_vars_pattern = re.compile(r"\{([^}]+)\}")

        def replace_processed_var(match):
            var_path = match.group(1)
            if "." in var_path:
                # Handle nested object access
                parts = var_path.split(".")
                current_obj = data
                try:
                    for part in parts:
                        if isinstance(current_obj, dict):
                            current_obj = current_obj.get(part, "")
                        else:
                            return match.group(0)  # Return original if can't access
                    return str(current_obj) if current_obj != "" else match.group(0)
                except:
                    return match.group(0)
            else:
                # Handle simple variable
                return str(data.get(var_path, match.group(0)))

        processed = processed_vars_pattern.sub(replace_processed_var, processed)

        # Handle YouTube variables (prefixed with 'youtube.')
        youtube_pattern = re.compile(r"\{youtube\.([^}]+)\}")

        def replace_youtube_var(match):
            youtube_var = match.group(1)
            return get_youtube_value(youtube_var)

        processed = youtube_pattern.sub(replace_youtube_var, processed)

        # Handle statistics variables (prefixed with 'stats.')
        stats_pattern = re.compile(r"\{stats\.([^}]+)\}")

        def replace_stats_var(match):
            stat_name = match.group(1)
            return get_statistics_value(stat_name)

        processed = stats_pattern.sub(replace_stats_var, processed)

        # Handle custom variables (prefixed with 'custom_')
        custom_pattern = re.compile(r"\{custom_([^}]+)\}")

        def replace_custom_var(match):
            custom_var_name = f"custom_{match.group(1)}"
            try:
                # Import here to avoid circular imports
                from modules.uiwindows.chatbot import \
                    evaluate_custom_variable_expression

                # Evaluate the custom variable with the current data context
                result = evaluate_custom_variable_expression(custom_var_name, data)
                if result is not None:
                    return str(result)
                else:
                    return f"[Custom:{custom_var_name}]"
            except Exception as e:
                logger.error(f"Error evaluating custom variable {custom_var_name}: {e}")
                return f"[Custom:{custom_var_name}]"

        processed = custom_pattern.sub(replace_custom_var, processed)

        return processed

    def _execute_api_call(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the API call and return extracted variables"""
        try:
            import asyncio
            import json

            # Process endpoint URL with variables
            endpoint = self._process_variables(self.api_endpoint, data)

            # Handle special trigger variables in the endpoint URL
            if "{trigger_username}" in endpoint:
                endpoint = endpoint.replace(
                    "{trigger_username}", data.get("username", "")
                )
            if "{trigger_user_id}" in endpoint:
                endpoint = endpoint.replace(
                    "{trigger_user_id}", data.get("user_id", "")
                )

            # Process headers with variables
            additional_headers = {}
            for key, value in self.api_headers.items():
                processed_key = self._process_variables(key, data)
                processed_value = self._process_variables(str(value), data)

                # Handle special trigger variables in headers
                if processed_value == "{trigger_username}":
                    processed_value = data.get("username", "")
                elif processed_value == "{trigger_user_id}":
                    processed_value = data.get("user_id", "")

                additional_headers[processed_key] = processed_value

            # Process body with variables (for POST/PUT requests)
            json_data = None
            if self.api_body and self.api_method.upper() in ["POST", "PUT", "PATCH"]:
                processed_body = self._process_variables(self.api_body, data)

                # Handle special trigger variables in the body
                if processed_body == "{trigger_username}":
                    processed_body = data.get("username", "")
                elif processed_body == "{trigger_user_id}":
                    processed_body = data.get("user_id", "")

                try:
                    json_data = json.loads(processed_body)
                except json.JSONDecodeError:
                    # If not valid JSON, we'll pass it as raw data to generic_api_call
                    json_data = processed_body

            # Make the API call using the existing generic_api_call method
            async def make_api_call():
                try:
                    # Import the twitch module to use its generic_api_call method
                    from . import twitch

                    if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                        raise Exception(
                            "Twitch API not connected - cannot make authenticated API calls"
                        )

                    # Prepare parameters for generic_api_call
                    params = {}
                    final_json_data = json_data

                    # Add API parameters from form data if available
                    if hasattr(self, "api_parameters") and self.api_parameters:
                        for key, value in self.api_parameters.items():
                            processed_key = self._process_variables(key, data)
                            processed_value = self._process_variables(str(value), data)

                            # Handle special trigger variables for the command user
                            if processed_value == "{trigger_username}":
                                processed_value = data.get("username", "")
                            elif processed_value == "{trigger_user_id}":
                                # For user_id, we might need to look it up if not available
                                processed_value = data.get("user_id", "")

                            params[processed_key] = processed_value

                    # For GET requests, extract query parameters from URL if present
                    final_endpoint = endpoint
                    if self.api_method.upper() == "GET" and "?" in endpoint:
                        base_url, query_string = endpoint.split("?", 1)
                        final_endpoint = base_url
                        # Parse query parameters
                        from urllib.parse import parse_qs

                        url_params = {
                            k: v[0] for k, v in parse_qs(query_string).items()
                        }
                        params.update(url_params)

                    # Note: The generic_api_call method already handles authentication headers
                    # We don't need to pass additional headers as they're handled by the method
                    # For now, we'll proceed with the basic implementation

                    # Make the API call using the authenticated session
                    response_data = await twitch.twitch_api.generic_api_call(
                        url=final_endpoint,
                        method=self.api_method.upper(),
                        params=params,
                        json_data=final_json_data,
                    )

                    # Add response metadata (generic_api_call doesn't return status/headers)
                    api_response = {
                        "status_code": 200,  # Assume success since generic_api_call raises on error
                        "headers": {},
                        "data": response_data,
                    }

                    return api_response

                except Exception as e:
                    logger.error("Error making API call to %s: %s", endpoint, e)
                    raise

            # Execute the async API call
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                api_response = loop.run_until_complete(make_api_call())
            finally:
                loop.close()

            # Extract variables from API response
            extracted_vars = self._extract_api_variables(api_response)

            return extracted_vars

        except Exception as e:
            logger.error(
                "Error executing API call for command %s: %s", self.command_name, e
            )
            raise

    def _extract_api_variables(self, api_response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract variables from API response based on response_format"""
        try:
            import json

            extracted_vars = {
                "api_status": api_response.get("status_code", 0),
                "api_success": api_response.get("status_code", 0) < 400,
            }

            if not self.api_response_format:
                # No specific format, return common API response variables
                response_data = api_response.get("data", {})
                if isinstance(response_data, dict):
                    for key, value in response_data.items():
                        if isinstance(value, (str, int, float, bool)):
                            extracted_vars[f"api_{key}"] = value
                return extracted_vars

            # Parse response format - support JSON path-like syntax
            format_specs = self.api_response_format.split(",")

            for spec in format_specs:
                spec = spec.strip()
                if "=" in spec:
                    # Format: variable_name=json_path or variable_name=static_value
                    var_name, path = spec.split("=", 1)
                    var_name = var_name.strip()
                    path = path.strip()

                    if path.startswith("{") and path.endswith("}"):
                        # JSON path extraction
                        json_path = path[1:-1]  # Remove { }
                        value = self._extract_json_path(
                            api_response.get("data", {}), json_path
                        )
                        if value is not None:
                            extracted_vars[var_name] = value
                    else:
                        # Static value or simple path
                        value = self._extract_json_path(
                            api_response.get("data", {}), path
                        )
                        if value is not None:
                            extracted_vars[var_name] = value
                        else:
                            # Treat as static value
                            extracted_vars[var_name] = path
                else:
                    # Simple format: json_path -> variable name derived from path
                    value = self._extract_json_path(api_response.get("data", {}), spec)
                    if value is not None:
                        var_name = (
                            spec.replace(".", "_").replace("[", "_").replace("]", "")
                        )
                        extracted_vars[f"api_{var_name}"] = value

            # Special handling for Twitch user API - calculate account age
            if "data.created_at" in self.api_response_format or "created_at" in str(
                api_response.get("data", {})
            ):
                try:
                    created_at = self._extract_json_path(
                        api_response.get("data", {}), "created_at"
                    )
                    if created_at:
                        age_data = self._calculate_account_age(created_at)
                        extracted_vars.update(age_data)
                except Exception as e:
                    logger.error(
                        "Error calculating account age from API response: %s", e
                    )

            return extracted_vars

        except Exception as e:
            logger.error("Error extracting API variables: %s", e)
            return {"api_error": str(e)}

    def _process_api_variables(
        self, api_response: Dict[str, Any], data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process API variables with custom expressions and calculations"""
        try:
            processed_vars = {}

            for expression in self.api_variable_processing:
                try:
                    result = self._evaluate_expression(expression, api_response, data)
                    if result is not None:
                        # Extract variable name from expression (format: variable_name=expression)
                        if "=" in expression:
                            var_name = expression.split("=")[0].strip()
                            processed_vars[var_name] = result
                        else:
                            # If no variable name specified, create one from the expression
                            var_name = (
                                expression.replace("{", "")
                                .replace("}", "")
                                .replace(".", "_")
                                .replace(" ", "_")
                            )
                            processed_vars[f"processed_{var_name}"] = result
                except Exception as e:
                    logger.error("Error processing expression '%s': %s", expression, e)
                    processed_vars[
                        f'error_{expression.split("=")[0].strip() if "=" in expression else "processing"}'
                    ] = str(e)

            return processed_vars

        except Exception as e:
            logger.error("Error processing API variables: %s", e)
            return {"processing_error": str(e)}

    def _evaluate_expression(
        self, expression: str, api_response: Dict[str, Any], data: Dict[str, Any]
    ) -> Any:
        """Evaluate a single expression for variable processing"""
        try:
            # Remove variable assignment part if present
            if "=" in expression:
                expression = expression.split("=", 1)[1].strip()

            # Replace variables in the expression
            processed_expression = self._process_variables(
                expression, {**data, **api_response.get("data", {})}
            )

            # Handle special functions
            if processed_expression.startswith(
                "date_diff_days("
            ) and processed_expression.endswith(")"):
                # Date difference in days: date_diff_days(date1, date2)
                args = processed_expression[15:-1].split(",")
                if len(args) == 2:
                    from datetime import datetime

                    try:
                        date1 = datetime.fromisoformat(
                            args[0].strip().replace("Z", "+00:00")
                        )
                        date2 = datetime.fromisoformat(
                            args[1].strip().replace("Z", "+00:00")
                        )
                        return abs((date1 - date2).days)
                    except:
                        return 0

            elif processed_expression.startswith(
                "date_to_age("
            ) and processed_expression.endswith(")"):
                # Convert date to age components: date_to_age(date_string)
                date_str = processed_expression[13:-1].strip()
                try:
                    from datetime import datetime

                    created_date = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )
                    current_time = datetime.now(created_date.tzinfo)

                    # Calculate the difference
                    age_delta = current_time - created_date
                    days = age_delta.days
                    years = days // 365
                    remaining_days = days % 365

                    return {
                        "days": str(days),
                        "years": str(years),
                        "remaining_days": str(remaining_days),
                        "datetime": created_date.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    }
                except Exception as e:
                    logger.error("Error calculating date age: %s", e)
                    return {"days": "0", "years": "0", "remaining_days": "0"}

            elif processed_expression.startswith(
                "math("
            ) and processed_expression.endswith(")"):
                # Basic math operations: math(expression)
                math_expr = processed_expression[5:-1].strip()
                try:
                    # Safe evaluation of math expressions
                    import builtins

                    allowed_names = {
                        k: v
                        for k, v in builtins.__dict__.items()
                        if k
                        in ("abs", "round", "min", "max", "sum", "len", "int", "float")
                    }
                    allowed_names.update({"__builtins__": {}})
                    return eval(math_expr, allowed_names, {})
                except:
                    return 0

            elif processed_expression.startswith(
                "compare("
            ) and processed_expression.endswith(")"):
                # Comparisons: compare(value1, operator, value2)
                compare_expr = processed_expression[8:-1].strip()
                try:
                    parts = [p.strip() for p in compare_expr.split(",")]
                    if len(parts) == 3:
                        val1, op, val2 = parts
                        val1 = float(val1) if val1.replace(".", "").isdigit() else val1
                        val2 = float(val2) if val2.replace(".", "").isdigit() else val2

                        if op == "==":
                            return val1 == val2
                        elif op == "!=":
                            return val1 != val2
                        elif op == ">":
                            return val1 > val2
                        elif op == "<":
                            return val1 < val2
                        elif op == ">=":
                            return val1 >= val2
                        elif op == "<=":
                            return val1 <= val2
                except:
                    pass

            # If no special function, return the processed expression as-is
            return processed_expression

        except Exception as e:
            logger.error("Error evaluating expression '%s': %s", expression, e)
            return None

    def _calculate_account_age(self, created_at: str) -> Dict[str, Any]:
        """Calculate account age from created_at timestamp"""
        try:
            import time
            from datetime import datetime

            # Parse the created_at timestamp (Twitch format: "2021-01-01T00:00:00Z")
            created_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            current_time = datetime.now(created_date.tzinfo)

            # Calculate the difference
            age_delta = current_time - created_date

            # Extract components
            days = age_delta.days
            years = days // 365
            remaining_days = days % 365

            return {
                "account_age_days": days,
                "account_age_years": years,
                "account_age_remaining_days": remaining_days,
                "account_created_datetime": created_date.strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
            }

        except Exception as e:
            logger.error("Error calculating account age: %s", e)
            return {
                "account_age_days": 0,
                "account_age_years": 0,
                "account_age_remaining_days": 0,
                "account_created_datetime": "Unknown",
            }

    def _extract_json_path(self, data: Any, path: str) -> Any:
        """Extract value from JSON data using dot notation path"""
        try:
            if not path:
                return data

            parts = path.split(".")
            current = data

            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit():
                    index = int(part)
                    if 0 <= index < len(current):
                        current = current[index]
                    else:
                        return None
                else:
                    return None

            return current

        except Exception as e:
            logger.error("Error extracting JSON path %s: %s", path, e)
            return None

    def reset_counter(self):
        """Reset the command counter"""
        self.counter_value = 0
        logger.info("Reset counter for command %s", self.command_name)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""

        # Helper function to sanitize values
        def sanitize_value(value):
            """Sanitize a value to ensure it's JSON serializable"""
            if value is None:
                return None
            if isinstance(value, (str, bool, int, float)):
                return value
            if isinstance(value, list):
                return [sanitize_value(item) for item in value]
            if isinstance(value, dict):
                return {k: sanitize_value(v) for k, v in value.items()}
            # Handle non-serializable objects
            if hasattr(value, "value"):
                return sanitize_value(value.value)
            # Convert to string as last resort
            try:
                return str(value)
            except Exception:
                return ""

        return {
            "command_id": sanitize_value(self.command_id),
            "name": sanitize_value(self.name),
            "description": sanitize_value(self.description),
            "command_name": sanitize_value(self.command_name),
            "aliases": sanitize_value(self.aliases),
            "response_text": sanitize_value(self.response_text),
            "mod_only": sanitize_value(self.mod_only),
            "cooldown": sanitize_value(self.cooldown),
            "command_type": sanitize_value(
                self.command_type.value
                if hasattr(self.command_type, "value")
                else str(self.command_type)
            ),
            "usage_limit": sanitize_value(self.usage_limit),
            "repeating_enabled": sanitize_value(self.repeating_enabled),
            "repeat_count": sanitize_value(self.repeat_count),
            "repeat_interval": sanitize_value(self.repeat_interval),
            "persistent_counter": sanitize_value(self.persistent_counter),
            "reset_command": sanitize_value(self.reset_command),
            "enabled": sanitize_value(self.enabled),
            # API Call fields
            "api_enabled": sanitize_value(self.api_enabled),
            "api_endpoint": sanitize_value(self.api_endpoint),
            "api_method": sanitize_value(self.api_method),
            "api_headers": sanitize_value(self.api_headers),
            "api_body": sanitize_value(self.api_body),
            "api_response_format": sanitize_value(self.api_response_format),
            "api_parameters": sanitize_value(self.api_parameters),
            "api_endpoint_select": sanitize_value(self.api_endpoint_select),
            "api_variable_processing": sanitize_value(self.api_variable_processing),
            # Runtime state
            "counter_value": sanitize_value(self.counter_value),
            "usage_count": sanitize_value(self.usage_count),
            "last_used": sanitize_value(self.last_used),
            "trigger_count": sanitize_value(self.trigger_count),
            "last_triggered": sanitize_value(self.last_triggered),
            "conditions": sanitize_value(
                [
                    {
                        "field": sanitize_value(c.field),
                        "operator": sanitize_value(
                            c.operator.value
                            if hasattr(c.operator, "value")
                            else str(c.operator)
                        ),
                        "value": sanitize_value(c.value),
                    }
                    for c in self.conditions
                ]
            ),
            "argument_mappings": sanitize_value(self.argument_mappings),
            "reply_targets": sanitize_value(list(self.reply_targets)),
            "discord_channels": sanitize_value(list(self.discord_channels or [])),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCommand":
        """Create from dictionary"""
        conditions = []
        for cond_data in data.get("conditions", []):
            conditions.append(
                TriggerCondition(
                    field=cond_data["field"],
                    operator=ComparisonOperator(cond_data["operator"]),
                    value=cond_data["value"],
                )
            )

        # Handle the command creation, excluding conditions which we'll set separately
        command_data = {k: v for k, v in data.items() if k != "conditions"}

        # Ensure command_type is properly converted to enum
        if "command_type" in command_data:
            if isinstance(command_data["command_type"], str):
                # Convert string to enum
                try:
                    command_data["command_type"] = CommandType(
                        command_data["command_type"]
                    )
                except ValueError:
                    # If invalid, default to BASIC
                    command_data["command_type"] = CommandType.BASIC
            elif not isinstance(command_data["command_type"], CommandType):
                # If it's something else, default to BASIC
                command_data["command_type"] = CommandType.BASIC

        # Ensure API headers is a dict, handle migration from older versions
        if "api_headers" in command_data:
            if isinstance(command_data["api_headers"], str):
                # If it's a string, try to parse as JSON, otherwise set as empty dict
                try:
                    import json

                    command_data["api_headers"] = json.loads(
                        command_data["api_headers"]
                    )
                except:
                    command_data["api_headers"] = {}
        else:
            command_data["api_headers"] = {}

        command = cls(**command_data)
        command.conditions = conditions
        return command


class ChatEvent:
    """Represents an automatic event response"""

    def __init__(self, **kwargs):
        self.event_id = kwargs.get("event_id", "")
        self.name = kwargs.get("name", "")
        self.description = kwargs.get("description", "")
        self.event_type = kwargs.get("event_type", EventType.FOLLOW)
        self.response_text = kwargs.get("response_text", "")
        self.enabled = kwargs.get("enabled", True)

        # Interval for repeating events (in seconds, 0 means no repeat)
        self.interval = kwargs.get("interval", 0)

        # Runtime state
        self.trigger_count = kwargs.get("trigger_count", 0)
        self.last_triggered = kwargs.get("last_triggered", 0)

        # Conditions
        self.conditions = kwargs.get("conditions", [])

        # Argument mappings for custom variable names (primarily for chat message events)
        # Format: {"variable_name": position} where position is 1-based
        self.argument_mappings = kwargs.get("argument_mappings", {})
        self.reply_targets = _normalize_reply_targets(
            kwargs.get("reply_targets"), default=["twitch"]
        )
        raw_discord = kwargs.get("discord_channels") or []
        self.discord_channels = (
            list(raw_discord) if isinstance(raw_discord, list) else []
        )

        # Event-specific settings
        self.specific_time = kwargs.get("specific_time", "")  # HH:MM format
        self.chat_message_text = kwargs.get("chat_message_text", "")
        self.chat_message_match_type = kwargs.get(
            "chat_message_match_type", "exact"
        )  # exact, starts_with, contains
        self.bits_quantity = kwargs.get("bits_quantity", 0)  # 0 = any
        self.hype_train_level = kwargs.get("hype_train_level", 0)  # 0 = any
        self.hype_train_end_level = kwargs.get("hype_train_end_level", 0)  # 0 = any
        self.gift_sub_quantity = kwargs.get("gift_sub_quantity", 0)  # 0 = any
        self.gift_sub_tier = kwargs.get("gift_sub_tier", 0)  # 0 = any
        self.resub_months = kwargs.get("resub_months", 0)  # 0 = any
        self.resub_tier = kwargs.get("resub_tier", 0)  # 0 = any
        self.sub_tier = kwargs.get("sub_tier", 0)  # 0 = any
        self.donation_amount = kwargs.get("donation_amount", 0.0)  # 0 = any
        self.raid_viewer_count = kwargs.get("raid_viewer_count", 0)  # 0 = any
        self.raid_raider_name = kwargs.get("raid_raider_name", "")  # empty = any
        self.channel_point_reward_name = kwargs.get(
            "channel_point_reward_name", ""
        )  # empty = any

    def should_trigger(self, data: Dict[str, Any]) -> bool:
        """Check if this event should trigger based on conditions and event-specific settings"""
        if not self.enabled:
            return False

        # Check all conditions
        for condition in self.conditions:
            if not condition.evaluate(data):
                return False

        # Check event-specific settings based on event type
        if self.event_type == EventType.SPECIFIC_TIME:
            # For SPECIFIC_TIME, this is handled by the scheduler, not here
            # But we can validate the time format if needed
            if not self.specific_time:
                return False
            # Time matching is done in the scheduler
            return True

        elif self.event_type == EventType.CHAT_MESSAGE:
            # Check message matching
            message = data.get("message", "")
            if not self.chat_message_text:
                return False

            if self.chat_message_match_type == "exact":
                return message == self.chat_message_text
            elif self.chat_message_match_type == "starts_with":
                return message.startswith(self.chat_message_text)
            elif self.chat_message_match_type == "contains":
                return self.chat_message_text in message
            return False

        elif self.event_type == EventType.BITS:
            # Check bits quantity if specified
            if self.bits_quantity > 0:
                amount = data.get("amount", 0)
                try:
                    amount = int(amount) if isinstance(amount, str) else amount
                    return amount >= self.bits_quantity
                except (ValueError, TypeError):
                    return False
            return True

        elif self.event_type == EventType.HYPE_TRAIN_PROGRESS:
            # Check hype train level if specified
            if self.hype_train_level > 0:
                level = data.get("level", 0)
                try:
                    level = int(level) if isinstance(level, str) else level
                    return level >= self.hype_train_level
                except (ValueError, TypeError):
                    return False
            return True

        elif self.event_type == EventType.HYPE_TRAIN_END:
            # Check hype train end level if specified
            if self.hype_train_end_level > 0:
                level = data.get("level", 0)
                try:
                    level = int(level) if isinstance(level, str) else level
                    return level >= self.hype_train_end_level
                except (ValueError, TypeError):
                    return False
            return True

        elif self.event_type == EventType.GIFT_SUBSCRIPTION:
            # Check gift sub quantity and tier if specified
            if self.gift_sub_quantity > 0:
                quantity = data.get("total_gifts", data.get("quantity", 0))
                try:
                    quantity = int(quantity) if isinstance(quantity, str) else quantity
                    if quantity < self.gift_sub_quantity:
                        return False
                except (ValueError, TypeError):
                    return False

            if self.gift_sub_tier > 0:
                tier = data.get("tier", 0)
                try:
                    tier = int(tier) if isinstance(tier, str) else tier
                    if tier != self.gift_sub_tier:
                        return False
                except (ValueError, TypeError):
                    return False

            return True

        elif self.event_type == EventType.RESUBSCRIPTION:
            # Check resub months and tier if specified
            if self.resub_months > 0:
                months = data.get("months", data.get("cumulative_months", 0))
                try:
                    months = int(months) if isinstance(months, str) else months
                    if months < self.resub_months:
                        return False
                except (ValueError, TypeError):
                    return False

            if self.resub_tier > 0:
                tier = data.get("tier", 0)
                try:
                    tier = int(tier) if isinstance(tier, str) else tier
                    if tier != self.resub_tier:
                        return False
                except (ValueError, TypeError):
                    return False

            return True

        elif self.event_type == EventType.SUBSCRIPTION:
            # Check sub tier if specified
            if self.sub_tier > 0:
                tier = data.get("tier", 0)
                try:
                    tier = int(tier) if isinstance(tier, str) else tier
                    if tier != self.sub_tier:
                        return False
                except (ValueError, TypeError):
                    return False
            return True

        elif self.event_type == EventType.DONATION:
            # Check donation amount if specified
            if self.donation_amount > 0:
                amount = data.get("amount", 0)
                try:
                    amount = float(amount) if isinstance(amount, str) else amount
                    return amount >= self.donation_amount
                except (ValueError, TypeError):
                    return False
            return True

        elif self.event_type == EventType.RAID:
            # Check raid viewer count and raider name if specified
            if self.raid_viewer_count > 0:
                viewer_count = data.get("viewer_count", 0)
                try:
                    viewer_count = (
                        int(viewer_count)
                        if isinstance(viewer_count, str)
                        else viewer_count
                    )
                    if viewer_count < self.raid_viewer_count:
                        return False
                except (ValueError, TypeError):
                    return False

            if self.raid_raider_name:
                raider_name = data.get("raider_name", data.get("username", ""))
                if raider_name != self.raid_raider_name:
                    return False

            return True

        elif self.event_type == EventType.CHANNEL_POINT_REDEMPTION:
            # Check channel point reward name if specified
            if self.channel_point_reward_name:
                reward_name = data.get("reward_title", data.get("reward_name", ""))
                if reward_name != self.channel_point_reward_name:
                    return False
            return True

        # For all other event types, no additional filtering
        return True

    def trigger_event(self, data: Dict[str, Any]) -> str:
        """Trigger the event and return the processed response"""
        current_time = time.time()

        # Update counters
        self.trigger_count += 1
        self.last_triggered = current_time

        # Track statistics
        try:
            from .statistics_manager import get_statistics_manager

            stats_manager = get_statistics_manager()
            username = data.get("username")
            stats_manager.increment_events_triggered(
                username=username, event_name=self.name
            )
            logger.debug(f"Tracked event trigger for: {self.name}")
        except Exception as e:
            logger.error(f"Error tracking event statistics: {e}")

        # Process response with variables
        response = self._process_variables(self.response_text, data)
        return response

    def _process_variables(self, text: str, data: Dict[str, Any]) -> str:
        """Process variables in response text"""
        processed = text

        # Basic variables
        processed = processed.replace(
            "{username}", str(data.get("username", "Unknown"))
        )
        processed = processed.replace(
            "{timestamp}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        processed = processed.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
        # Resolve {time} / {time.filters} before the generic dotted-path walker
        processed = replace_time_variables(processed)

        # Event-specific variables
        if "amount" in data:
            processed = processed.replace("{amount}", str(data.get("amount", "0")))
        if "tier" in data:
            processed = processed.replace("{tier}", str(data.get("tier", "1")))
        if "months" in data:
            processed = processed.replace("{months}", str(data.get("months", "1")))
        if "message" in data:
            processed = processed.replace("{message}", str(data.get("message", "")))
        if "viewer_count" in data:
            processed = processed.replace(
                "{viewer_count}", str(data.get("viewer_count", "0"))
            )
        if "level" in data:
            processed = processed.replace("{level}", str(data.get("level", "1")))

        # Command message variables (for chat message events)
        if "command_message" in data:
            processed = processed.replace(
                "{command_message}", str(data.get("command_message", ""))
            )
        if "command_args" in data:
            command_args = data.get("command_args", [])
            if command_args:
                processed = processed.replace(
                    "{command_first_word}", str(command_args[0])
                )
                processed = processed.replace(
                    "{command_last_word}", str(command_args[-1])
                )
            else:
                processed = processed.replace("{command_first_word}", "")
                processed = processed.replace("{command_last_word}", "")

            # Handle {command_word_N} variables
            import re

            word_pattern = re.compile(r"\{command_word_(\d+)\}")

            def replace_word_var(match):
                index = int(match.group(1)) - 1  # Convert to 0-based indexing
                if 0 <= index < len(command_args):
                    return str(command_args[index])
                return ""

            processed = word_pattern.sub(replace_word_var, processed)

        # Handle custom argument mappings (e.g., {city} -> command_args[0])
        if self.argument_mappings and "command_args" in data:
            command_args = data.get("command_args", [])
            for var_name, position in self.argument_mappings.items():
                if isinstance(position, int) and position >= 1:
                    # Position is 1-based, convert to 0-based index
                    arg_index = position - 1
                    if 0 <= arg_index < len(command_args):
                        # Valid position, replace with argument value
                        processed = processed.replace(
                            f"{{{var_name}}}", str(command_args[arg_index])
                        )
                    else:
                        # Out of bounds, replace with empty string
                        processed = processed.replace(f"{{{var_name}}}", "")

        # Handle processed variables with dot notation (e.g., {account_age.days})
        import re

        processed_vars_pattern = re.compile(r"\{([^}]+)\}")

        def replace_processed_var(match):
            var_path = match.group(1)
            if "." in var_path:
                # Handle nested object access
                parts = var_path.split(".")
                current_obj = data
                try:
                    for part in parts:
                        if isinstance(current_obj, dict):
                            current_obj = current_obj.get(part, "")
                        else:
                            return match.group(0)  # Return original if can't access
                    return str(current_obj) if current_obj != "" else match.group(0)
                except:
                    return match.group(0)
            else:
                # Handle simple variable
                return str(data.get(var_path, match.group(0)))

        processed = processed_vars_pattern.sub(replace_processed_var, processed)

        # Handle YouTube variables (prefixed with 'youtube.')
        youtube_pattern = re.compile(r"\{youtube\.([^}]+)\}")

        def replace_youtube_var(match):
            youtube_var = match.group(1)
            return get_youtube_value(youtube_var)

        processed = youtube_pattern.sub(replace_youtube_var, processed)

        # Handle statistics variables (prefixed with 'stats.')
        stats_pattern = re.compile(r"\{stats\.([^}]+)\}")

        def replace_stats_var(match):
            stat_name = match.group(1)
            return get_statistics_value(stat_name)

        processed = stats_pattern.sub(replace_stats_var, processed)

        # Handle custom variables (prefixed with 'custom_')
        custom_pattern = re.compile(r"\{custom_([^}]+)\}")

        def replace_custom_var(match):
            custom_var_name = f"custom_{match.group(1)}"
            try:
                # Import here to avoid circular imports
                from modules.uiwindows.chatbot import \
                    evaluate_custom_variable_expression

                # Evaluate the custom variable with the current data context
                result = evaluate_custom_variable_expression(custom_var_name, data)
                if result is not None:
                    return str(result)
                else:
                    return f"[Custom:{custom_var_name}]"
            except Exception as e:
                logger.error(f"Error evaluating custom variable {custom_var_name}: {e}")
                return f"[Custom:{custom_var_name}]"

        processed = custom_pattern.sub(replace_custom_var, processed)

        return processed

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
        if self.interval == 0:
            return ""

        hours = self.interval // 3600
        minutes = (self.interval % 3600) // 60
        seconds = self.interval % 60

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "description": self.description,
            "event_type": self.event_type.value,
            "response_text": self.response_text,
            "enabled": self.enabled,
            "interval": self.interval,
            "trigger_count": self.trigger_count,
            "last_triggered": self.last_triggered,
            "conditions": [
                {"field": c.field, "operator": c.operator.value, "value": c.value}
                for c in self.conditions
            ],
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
            "argument_mappings": self.argument_mappings,
            "reply_targets": list(self.reply_targets),
            "discord_channels": list(self.discord_channels or []),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatEvent":
        """Create from dictionary"""
        conditions = []
        for cond_data in data.get("conditions", []):
            conditions.append(
                TriggerCondition(
                    field=cond_data["field"],
                    operator=ComparisonOperator(cond_data["operator"]),
                    value=cond_data["value"],
                )
            )

        # Handle the event creation, excluding conditions which we'll set separately
        event_data = {k: v for k, v in data.items() if k != "conditions"}

        # Ensure event_type is properly converted to enum
        if "event_type" in event_data:
            if isinstance(event_data["event_type"], str):
                # Convert string to enum
                try:
                    event_data["event_type"] = EventType(event_data["event_type"])
                except ValueError:
                    # If invalid, default to FOLLOW
                    event_data["event_type"] = EventType.FOLLOW
            elif not isinstance(event_data["event_type"], EventType):
                # If it's something else, default to FOLLOW
                event_data["event_type"] = EventType.FOLLOW

        event = cls(**event_data)
        event.conditions = conditions
        return event


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

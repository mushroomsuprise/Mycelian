#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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

import html
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from nicegui import ui
from ..notification_engine import notify
from ..ui_buttons import outline_button, primary_button
from ..ui_form_controls import form_input, form_number, form_textarea

from ..help_system.contextual_help import set_chatbot_ui_references

# Import the chatbot modules
from ..chatbot_core import (
    ChatCommand,
    ChatEvent,
    CommandType,
    EventType,
    get_statistics_value,
    get_youtube_value,
)
from ..chatbot_manager import get_manager as get_chatbot_manager
from ..database_manager import get_data, set_data
from ..giveaway_manager import get_giveaway_manager
from ..statistics_manager import get_statistics_manager
from ..twitch import get_twitch_api
from ..twitch_api_reference import TwitchAPIReference

logger = logging.getLogger(__name__)

# Custom Variables Storage
CUSTOM_VARIABLES_PATH = "chatbot_custom_variables"


def save_custom_variables(custom_variables: dict) -> bool:
    """Save custom variables to persistent storage"""
    try:
        data_dict = {
            "data": custom_variables,
            "last_updated": time.time(),
            "version": "1.0",
        }
        success = set_data(CUSTOM_VARIABLES_PATH, data_dict)
        if success:
            logger.info(f"Saved {len(custom_variables)} custom variables to storage")
        else:
            logger.error("Failed to save custom variables to storage")
        return success
    except Exception as e:
        logger.error(f"Error saving custom variables: {e}", exc_info=True)
        return False


def load_custom_variables() -> dict:
    """Load custom variables from persistent storage"""
    try:
        stored_data = get_data(CUSTOM_VARIABLES_PATH)
        if stored_data and "data" in stored_data:
            custom_vars = stored_data["data"]
            logger.info(f"Loaded {len(custom_vars)} custom variables from storage")
            return custom_vars
        else:
            logger.info("No custom variables found in storage")
            return {}
    except Exception as e:
        logger.error(f"Error loading custom variables: {e}", exc_info=True)
        return {}


def add_custom_variable(name: str, expression: str) -> bool:
    """Add a new custom variable to storage"""
    try:
        custom_vars = load_custom_variables()
        clean_name = name.strip()
        if not clean_name.startswith("custom_"):
            clean_name = f"custom_{clean_name}"

        custom_vars[clean_name] = f"{{{clean_name}}} - Custom: {expression.strip()}"

        success = save_custom_variables(custom_vars)
        if success:
            logger.info(f"Added custom variable: {clean_name}")
        return success
    except Exception as e:
        logger.error(f"Error adding custom variable: {e}", exc_info=True)
        return False


def remove_custom_variable(name: str) -> bool:
    """Remove a custom variable from storage"""
    try:
        custom_vars = load_custom_variables()
        if name in custom_vars:
            del custom_vars[name]
            success = save_custom_variables(custom_vars)
            if success:
                logger.info(f"Removed custom variable: {name}")
            return success
        return True  # Variable didn't exist, consider it successful
    except Exception as e:
        logger.error(f"Error removing custom variable: {e}", exc_info=True)
        return False


def get_live_basic_variables() -> Dict[str, str]:
    """Get live basic variable values for preview"""
    try:
        twitch_api = get_twitch_api()
        username = "PreviewUser"
        if twitch_api and twitch_api.is_connected:
            if twitch_api.user and hasattr(twitch_api.user, "display_name"):
                username = twitch_api.user.display_name

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        date_str = now.strftime("%B %d, %Y")
        time_str = now.strftime("%I:%M %p")

        return {
            "username": username,
            "timestamp": timestamp,
            "datetime": timestamp,
            "date": date_str,
            "time": time_str,
            "source": "Twitch",
        }
    except Exception as e:
        logger.error(f"Error getting live basic variables: {e}")
        return {
            "username": "PreviewUser",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "date": datetime.now().strftime("%B %d, %Y"),
            "time": datetime.now().strftime("%I:%M %p"),
            "source": "Twitch",
        }


def evaluate_custom_variable_expression(
    var_name: str, context_data: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Evaluate a custom variable expression recursively"""
    try:
        custom_vars = load_custom_variables()
        if var_name not in custom_vars:
            return None

        var_desc = custom_vars[var_name]
        # Extract expression from description (format: "{custom_var_name} - Custom: expression")
        if " - Custom: " not in var_desc:
            return None

        expression = var_desc.split(" - Custom: ", 1)[1].strip()

        # Process nested variables in the expression first
        # Replace stats variables
        stats_pattern = re.compile(r"\{stats\.([^}]+)\}")
        processed_expr = stats_pattern.sub(
            lambda m: get_statistics_value(m.group(1)) or f"{{stats.{m.group(1)}}}",
            expression,
        )

        # Replace YouTube variables
        youtube_pattern = re.compile(r"\{youtube\.([^}]+)\}")
        processed_expr = youtube_pattern.sub(
            lambda m: get_youtube_value(m.group(1)) or f"{{youtube.{m.group(1)}}}",
            processed_expr,
        )

        # Replace other custom variables recursively
        custom_pattern = re.compile(r"\{custom_([^}]+)\}")
        max_depth = 10  # Prevent infinite recursion
        depth = 0

        while custom_pattern.search(processed_expr) and depth < max_depth:
            depth += 1
            processed_expr = custom_pattern.sub(
                lambda m: evaluate_custom_variable_expression(
                    f"custom_{m.group(1)}", context_data
                )
                or f"{{custom_{m.group(1)}}}",
                processed_expr,
            )

        # Replace basic variables
        basic_vars = get_live_basic_variables()
        for var_key, var_value in basic_vars.items():
            processed_expr = processed_expr.replace(f"{{{var_key}}}", str(var_value))

        # Handle advanced time formatting (e.g., {time:EST:12:show})
        time_pattern = re.compile(r"\{time(?::([^}]*))?\}")

        def replace_time_var(match):
            format_options = match.group(1) or ""
            from .chatbot_core import format_time_with_options

            return format_time_with_options(format_options)

        processed_expr = time_pattern.sub(replace_time_var, processed_expr)

        # Replace command-specific variables if context_data is available
        if context_data:
            # Command message variables
            if "command_message" in context_data:
                processed_expr = processed_expr.replace(
                    "{command_message}", str(context_data.get("command_message", ""))
                )

            if "command_args" in context_data:
                command_args = context_data.get("command_args", [])
                if command_args:
                    processed_expr = processed_expr.replace(
                        "{command_first_word}", str(command_args[0])
                    )
                    processed_expr = processed_expr.replace(
                        "{command_last_word}", str(command_args[-1])
                    )
                else:
                    processed_expr = processed_expr.replace("{command_first_word}", "")
                    processed_expr = processed_expr.replace("{command_last_word}", "")

                # Handle {command_word_N} variables
                word_pattern = re.compile(r"\{command_word_(\d+)\}")

                def replace_word_var(match):
                    index = int(match.group(1)) - 1  # Convert to 0-based indexing
                    if 0 <= index < len(command_args):
                        return str(command_args[index])
                    return ""

                processed_expr = word_pattern.sub(replace_word_var, processed_expr)

            # Argument mappings (custom variables like {city}, {temp}, etc.)
            if "argument_mappings" in context_data:
                argument_mappings = context_data.get("argument_mappings", {})
                command_args = context_data.get("command_args", [])
                for var_name, position in argument_mappings.items():
                    if isinstance(position, int) and position >= 1:
                        arg_index = position - 1
                        if 0 <= arg_index < len(command_args):
                            processed_expr = processed_expr.replace(
                                f"{{{var_name}}}", str(command_args[arg_index])
                            )
                        else:
                            processed_expr = processed_expr.replace(
                                f"{{{var_name}}}", ""
                            )

            # Other command-specific variables
            command_specific_vars = [
                "cooldown",
                "usage_left",
                "count",
                "command_name",
                "last_used",
            ]
            for var_name in command_specific_vars:
                if var_name in context_data:
                    processed_expr = processed_expr.replace(
                        f"{{{var_name}}}", str(context_data.get(var_name, ""))
                    )

        # Evaluate special functions
        # Handle date_to_age()
        if "date_to_age(" in processed_expr:
            date_to_age_pattern = re.compile(r"date_to_age\(([^)]+)\)")
            processed_expr = date_to_age_pattern.sub(
                lambda m: _evaluate_date_to_age(m.group(1)), processed_expr
            )

        # Handle math()
        if "math(" in processed_expr:
            math_pattern = re.compile(r"math\(([^)]+)\)")
            processed_expr = math_pattern.sub(
                lambda m: str(_evaluate_math(m.group(1))), processed_expr
            )

        # Handle compare()
        if "compare(" in processed_expr:
            compare_pattern = re.compile(r"compare\(([^)]+)\)")
            processed_expr = compare_pattern.sub(
                lambda m: str(_evaluate_compare(m.group(1))), processed_expr
            )

        return processed_expr

    except Exception as e:
        logger.error(
            f"Error evaluating custom variable '{var_name}': {e}", exc_info=True
        )
        return None


def _evaluate_date_to_age(date_str: str) -> str:
    """Evaluate date_to_age() function"""
    try:
        date_str = date_str.strip().strip('"').strip("'")
        created_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        current_time = datetime.now(created_date.tzinfo)
        age_delta = current_time - created_date
        days = age_delta.days
        years = days // 365
        remaining_days = days % 365
        return f"{years} years, {remaining_days} days"
    except Exception:
        return "0 days"


def _evaluate_math(math_expr: str) -> float:
    """Evaluate math() function"""
    try:
        import builtins

        # Handle both old format (direct expression) and new format (comma-separated)
        if "," in math_expr:
            # New format: value1,operator,value2
            parts = [p.strip() for p in math_expr.split(",")]
            if len(parts) == 3:
                val1, op, val2 = parts
                # Reconstruct as proper Python expression
                python_expr = f"{val1} {op} {val2}"
            else:
                # Fallback to original expression if not 3 parts
                python_expr = math_expr
        else:
            # Old format: direct expression
            python_expr = math_expr

        allowed_names = {
            k: v
            for k, v in builtins.__dict__.items()
            if k in ("abs", "round", "min", "max", "sum", "len", "int", "float")
        }
        allowed_names.update({"__builtins__": {}})
        return eval(python_expr, allowed_names, {})
    except Exception:
        return 0


def _evaluate_compare(compare_expr: str) -> bool:
    """Evaluate compare() function"""
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
        return False
    except Exception:
        return False


def validate_expression_variables(expression: str) -> list:
    """Validate that all variables in an expression exist. Returns list of invalid variables."""
    try:
        invalid_vars = []

        # Extract all variables from the expression (things in curly braces)
        var_pattern = re.compile(r"\{([^}]+)\}")
        variables = var_pattern.findall(expression)

        # Get available variable sets
        basic_vars = get_live_basic_variables()
        custom_vars = load_custom_variables()
        twitch_api = get_twitch_api()

        for var in variables:
            var_lower = var.lower()

            # Check if it's a valid variable type
            is_valid = False

            # 1. Basic variables
            if var in basic_vars:
                is_valid = True
            # 2. Custom variables (check both with and without custom_ prefix)
            elif var in custom_vars or f"custom_{var}" in custom_vars:
                is_valid = True
            # 3. Stats variables (stats.*)
            elif var.startswith("stats."):
                # We can't easily validate all possible stats, so assume they're valid
                is_valid = True
            # 4. YouTube variables (youtube.*)
            elif var.startswith("youtube."):
                # We can't easily validate all possible YouTube vars, so assume they're valid
                is_valid = True
            # 5. Time variables (time:*)
            elif var.startswith("time"):
                is_valid = True
            # 6. Command-specific variables
            elif var in [
                "command_message",
                "command_first_word",
                "command_last_word",
                "cooldown",
                "usage_left",
                "count",
                "command_name",
                "last_used",
            ]:
                is_valid = True
            # 7. Command word variables (command_word_N)
            elif var.startswith("command_word_"):
                try:
                    int(var.split("_")[-1])  # Check if last part is a number
                    is_valid = True
                except ValueError:
                    pass
            # 8. Argument mappings (these are user-defined, so assume valid)
            # We can't validate these easily without context

            if not is_valid:
                invalid_vars.append(var)

        return invalid_vars

    except Exception as e:
        logger.error(f"Error validating expression variables: {e}", exc_info=True)
        return []


def _wrap_variable_value(value: str) -> str:
    """Wrap a variable value in styled span"""
    escaped_value = html.escape(str(value))
    return f'<span style="color: var(--color-primary); font-style: italic;">{escaped_value}</span>'


def resolve_variables_for_preview(
    text: str,
    item_type: str,
    event_type: Optional[str] = None,
    command_type: Optional[str] = None,
    form_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve all variables in preview text with live data, wrapping replacements in styled spans"""
    if not text:
        return ""

    processed = text
    context_data = form_data or {}

    # Get live basic variables
    basic_vars = get_live_basic_variables()

    # 1. Replace basic variables
    for var_name, var_value in basic_vars.items():
        pattern = re.compile(re.escape(f"{{{var_name}}}"))
        processed = pattern.sub(lambda m: _wrap_variable_value(var_value), processed)

    # 2. Replace command/event-specific variables with sample data
    if item_type == "command":
        command_vars = {
            "command_name": context_data.get("command_name", "example"),
            "cooldown": str(context_data.get("cooldown", 0)),
            "usage_left": str(
                context_data.get("usage_limit", 0) - context_data.get("usage_count", 0)
            ),
            "last_used": basic_vars["timestamp"],
            # Command message variables
            "command_message": "hello world example",
            "command_first_word": "hello",
            "command_last_word": "example",
            "command_word_1": "hello",
            "command_word_2": "world",
            "command_word_3": "example",
        }
        if command_type == "counter":
            command_vars["count"] = str(context_data.get("counter_value", 0))
        for var_name, var_value in command_vars.items():
            pattern = re.compile(re.escape(f"{{{var_name}}}"))
            processed = pattern.sub(
                lambda m: _wrap_variable_value(var_value), processed
            )
    else:  # event
        event_vars = {
            "amount": "100",
            "message": "Thanks for the support!",
            "source": "Twitch",
        }
        if event_type == "follow":
            event_vars["follower_count"] = "1,234"
        elif event_type == "subscription":
            event_vars.update(
                {
                    "tier": "1",
                    "tier_name": "Tier 1",
                    "months": "3",
                    "streak": "2",
                    "is_gift": "false",
                }
            )
        elif event_type == "resubscription":
            event_vars.update(
                {
                    "tier": "2",
                    "tier_name": "Tier 2",
                    "months": "6",
                    "streak": "5",
                    "cumulative_months": "12",
                }
            )
        elif event_type == "gift_subscription":
            event_vars.update(
                {
                    "tier": "1",
                    "tier_name": "Tier 1",
                    "recipient_name": "Viewer123",
                    "recipient_display_name": "Viewer123",
                    "gifter_name": "GenerousGifter",
                    "total_gifts": "5",
                }
            )
        elif event_type == "bits":
            event_vars.update(
                {
                    "bits_amount": "500",
                    "bits_used": "100",
                    "power_up_type": "Lightning",
                    "is_anonymous": "false",
                }
            )
        elif event_type == "donation":
            event_vars.update(
                {
                    "currency": "USD",
                    "formatted_amount": "$25.00",
                    "donation_message": "Keep up the great work!",
                }
            )
        elif event_type == "raid":
            event_vars.update(
                {
                    "viewer_count": "25",
                    "raider_name": "FriendlyRaider",
                    "raid_duration": "30s",
                }
            )
        elif event_type in [
            "hype_train_start",
            "hype_train_progress",
            "hype_train_end",
        ]:
            event_vars.update(
                {
                    "level": "3",
                    "goal": "100",
                    "progress": "75",
                    "total_contributions": "87",
                    "conductor_name": "TrainLeader",
                }
            )
            if event_type == "hype_train_end":
                event_vars["top_contributors"] = "User1 (50), User2 (25), User3 (12)"
        elif event_type == "channel_point_redemption":
            event_vars.update(
                {
                    "reward_name": "Test Reward",
                    "reward_cost": "100",
                    "user_input": "Hello!",
                    "reward_id": "12345",
                }
            )

        for var_name, var_value in event_vars.items():
            pattern = re.compile(re.escape(f"{{{var_name}}}"))
            processed = pattern.sub(
                lambda m: _wrap_variable_value(var_value), processed
            )

    # 2.5. Handle greeting-specific variables (override username)
    if item_type == "greeting" and "username" in context_data:
        pattern = re.compile(re.escape("{username}"))
        processed = pattern.sub(
            lambda m: _wrap_variable_value(context_data["username"]), processed
        )

    # 3. Replace statistics variables
    stats_pattern = re.compile(r"\{stats\.([^}]+)\}")

    def replace_stats(match):
        stat_name = match.group(1)
        try:
            value = get_statistics_value(stat_name)
            if value and value != "0":
                return _wrap_variable_value(value)
            else:
                # Show variable name if unavailable
                return _wrap_variable_value(f"{{stats.{stat_name}}}")
        except Exception:
            return _wrap_variable_value(f"{{stats.{stat_name}}}")

    processed = stats_pattern.sub(replace_stats, processed)

    # 4. Replace YouTube variables
    youtube_pattern = re.compile(r"\{youtube\.([^}]+)\}")

    def replace_youtube(match):
        youtube_var = match.group(1)
        try:
            value = get_youtube_value(youtube_var)
            if (
                value
                and "unavailable" not in value.lower()
                and "error" not in value.lower()
            ):
                return _wrap_variable_value(value)
            else:
                return _wrap_variable_value(f"{{youtube.{youtube_var}}}")
        except Exception:
            return _wrap_variable_value(f"{{youtube.{youtube_var}}}")

    processed = youtube_pattern.sub(replace_youtube, processed)

    # 5. Replace custom variables
    custom_pattern = re.compile(r"\{custom_([^}]+)\}")

    def replace_custom(match):
        custom_var_name = f"custom_{match.group(1)}"
        try:
            value = evaluate_custom_variable_expression(custom_var_name, context_data)
            if value:
                return _wrap_variable_value(value)
            else:
                return _wrap_variable_value(f"{{{custom_var_name}}}")
        except Exception:
            return _wrap_variable_value(f"{{{custom_var_name}}}")

    processed = custom_pattern.sub(replace_custom, processed)

    # 6. Replace quote variables
    quote_pattern = re.compile(r"\{quote\.([^.]+)\.([^}]+)\}")

    def replace_quote(match):
        quote_id_str = match.group(1)
        quote_var = match.group(2)
        try:
            # Get the chatbot manager to fetch real quote data
            manager = get_chatbot_manager()
            quote = None

            # Check if quote_id_str is a number (specific quote) or "id" (random)
            if quote_id_str.isdigit():
                # Specific quote ID
                quote_number = int(quote_id_str)
                quote = manager.get_quote_by_number(quote_number)
            else:
                # Random quote (when user leaves "id" as placeholder)
                quote = manager.get_random_quote()

            if quote:
                # Use real quote data
                if quote_var == "text":
                    value = quote.text
                elif quote_var == "author":
                    value = quote.author or "Unknown"
                elif quote_var == "quote_number":
                    value = str(quote.quote_number)
                elif quote_var == "date_added":
                    # Format the timestamp
                    dt = datetime.fromtimestamp(quote.date_added)
                    value = dt.strftime("%Y-%m-%d %H:%M:%S")
                elif quote_var == "added_by":
                    value = quote.added_by or "Unknown"
                else:
                    value = f"{{quote.{quote_id_str}.{quote_var}}}"
            else:
                # No quote found - show appropriate message
                if quote_id_str.isdigit():
                    value = f"No quote found with number {quote_id_str}"
                else:
                    value = "No quotes available"

            return _wrap_variable_value(value)
        except Exception as e:
            logger.warning(f"Error fetching quote data for preview: {e}")
            return _wrap_variable_value(f"{{quote.{quote_id_str}.{quote_var}}}")

    processed = quote_pattern.sub(replace_quote, processed)

    # 7. Handle API variables (show variable name since API call isn't executed in preview)
    api_pattern = re.compile(r"\{api_([^}]+)\}")
    processed = api_pattern.sub(
        lambda m: _wrap_variable_value(f"{{api_{m.group(1)}}}"), processed
    )

    # 7. Handle processed variables with dot notation (e.g., {account_age.days})
    # These are typically from API variable processing, so show variable name
    dot_pattern = re.compile(r"\{([^}]+\.([^}]+))\}")
    processed = dot_pattern.sub(
        lambda m: _wrap_variable_value(f"{{{m.group(1)}}}"), processed
    )

    return processed


# Global references for UI state
# Container variables for each tab
commands_container = None
events_container = None
quotes_container = None
greetings_container = None
giveaways_container = None

# Global search term (shared across tabs)
search_term = ""
selected_chatbot_item = None
create_dialog = None
custom_variable_dialog = None  # Separate dialog for custom variable creation
edit_dialog = None
current_tab = (
    "commands"  # Track current tab: "commands", "events", "quotes", or "greetings"
)

# Tab button references for active state management
command_button = None
event_button = None
quotes_button = None
greetings_button = None

# Global API reference instance
api_reference = None


def get_auth_scope_mapping():
    """Map AuthScope enum values to their string representations for API reference."""
    try:
        from ..twitch import get_twitch_api

        twitch_api = get_twitch_api()
        if twitch_api and hasattr(twitch_api, "authscope"):
            # Map common AuthScope values to their string equivalents
            scope_mapping = {
                "CHANNEL_READ_REDEMPTIONS": "channel:read:redemptions",
                "CHANNEL_READ_SUBSCRIPTIONS": "channel:read:subscriptions",
                "CHANNEL_MANAGE_REDEMPTIONS": "channel:manage:redemptions",
                "BITS_READ": "bits:read",
                "CHANNEL_READ_HYPE_TRAIN": "channel:read:hype_train",
                "MODERATOR_READ_FOLLOWERS": "moderator:read:followers",
                "CHANNEL_MODERATE": "channel:moderate",
                "CHAT_EDIT": "chat:edit",
                "CHAT_READ": "chat:read",
                "USER_EDIT": "user:edit",
                "USER_BOT": "user:bot",
                "USER_READ_CHAT": "user:read:chat",
                "MODERATOR_MANAGE_ANNOUNCEMENTS": "moderator:manage:announcements",
                "MODERATOR_MANAGE_CHAT_MESSAGES": "moderator:manage:chat_messages",
                "MODERATOR_MANAGE_SHOUTOUTS": "moderator:manage:shoutouts",
                "MODERATOR_READ_MODERATORS": "moderator:read:moderators",
                "MODERATOR_READ_VIPS": "moderator:read:vips",
                "MODERATOR_MANAGE_BLOCKED_TERMS": "moderator:manage:blocked_terms",
                "MODERATOR_MANAGE_CHAT_SETTINGS": "moderator:manage:chat_settings",
                "MODERATOR_MANAGE_UNBAN_REQUESTS": "moderator:manage:unban_requests",
                "MODERATOR_MANAGE_BANNED_USERS": "moderator:manage:banned_users",
                "MODERATOR_MANAGE_WARNINGS": "moderator:manage:warnings",
            }

            # Convert auth scopes to strings
            available_scopes = []
            for scope in twitch_api.authscope:
                scope_name = str(scope).split(".")[-1]  # Get just the enum name
                if scope_name in scope_mapping:
                    available_scopes.append(scope_mapping[scope_name])

            return available_scopes
    except Exception as e:
        logger.error(f"Error getting auth scopes: {e}")
        return []

    return []


def get_available_api_endpoints():
    """Get API endpoints available within current auth scope."""
    global api_reference

    if api_reference is None:
        api_reference = TwitchAPIReference()

    available_scopes = get_auth_scope_mapping()
    available_endpoints = []

    # Get all endpoints and filter by available scopes
    all_endpoints = api_reference.get_all_endpoints()

    for category, endpoints in all_endpoints.items():
        for endpoint_name, endpoint in endpoints.items():
            # Check if endpoint is public (no authorization required) OR
            # if any of the endpoint's authorization scopes are available
            if not endpoint.authorization or any(
                scope in available_scopes for scope in endpoint.authorization
            ):
                available_endpoints.append(
                    {
                        "name": endpoint.name,
                        "endpoint": endpoint.endpoint,
                        "method": endpoint.method,
                        "description": endpoint.description,
                        "category": category,
                        "response_fields": endpoint.response_fields,
                        "required_params": endpoint.required_params,
                        "optional_params": endpoint.optional_params,
                        "full_name": f"{category}.{endpoint_name}",
                    }
                )

    return available_endpoints


def get_endpoint_response_variables(endpoint_full_name):
    """Get response variables for a specific endpoint."""
    global api_reference

    if api_reference is None:
        api_reference = TwitchAPIReference()

    try:
        category, endpoint_name = endpoint_full_name.split(".", 1)
        endpoint = api_reference.get_endpoint_by_name(category, endpoint_name)

        if endpoint:
            # Convert response fields to variable format
            variables = []
            for field in endpoint.response_fields:
                # Convert field names to variable format (e.g., "user_id" -> "{user_id}")
                variables.append(f"{{{field}}}")

            return variables
    except Exception as e:
        logger.error(f"Error getting response variables for {endpoint_full_name}: {e}")

    return []


# Add custom CSS for the chatbot UI
CUSTOM_CSS = """
.fade-in {
    animation: fadeIn 0.3s ease-in-out;
}

.scale-in {
    animation: scaleIn 0.2s ease-out;
}

.slide-in {
    animation: slideIn 0.3s ease-out;
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes scaleIn {
    from { transform: scale(0.95); opacity: 0; }
    to { transform: scale(1); opacity: 1; }
}

@keyframes slideIn {
    from { transform: translateY(-10px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

.chatbot-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
    border: 1px solid rgba(59, 130, 246, 0.2);
}

.chatbot-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
    border-color: rgba(59, 130, 246, 0.4);
}

.chatbot-card.command {
    background: var(--color-bg-surface);
    border: 1px solid rgba(16, 185, 129, 0.2);
}

.chatbot-card.command:hover {
    border-color: rgba(16, 185, 129, 0.4);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
}

.chatbot-card.event {
    background: var(--color-bg-surface);
    border: 1px solid rgba(245, 101, 101, 0.2);
}

.chatbot-card.event:hover {
    border-color: rgba(245, 101, 101, 0.4);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
}

.chatbot-card.disabled {
    opacity: 0.6;
    background: var(--color-bg-surface);
    border-color: var(--color-border-default);
}

.mod-only-badge {
    background: linear-gradient(135deg, rgba(245, 101, 101, 0.2), rgba(239, 68, 68, 0.3));
    border: 1px solid rgba(245, 101, 101, 0.4);
    color: var(--color-error);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.counter-badge {
    background: linear-gradient(135deg, rgba(251, 191, 36, 0.2), rgba(245, 158, 11, 0.3));
    border: 1px solid rgba(251, 191, 36, 0.4);
    color: var(--color-warning);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.repeating-badge {
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(124, 58, 237, 0.3));
    border: 1px solid rgba(139, 92, 246, 0.4);
    color: var(--color-primary);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.status-badge {
    padding: 4px 8px;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}

.status-enabled {
    background: rgba(16, 185, 129, 0.2);
    color: var(--color-success);
    border: 1px solid rgba(16, 185, 129, 0.3);
}

.status-disabled {
    background: rgba(239, 68, 68, 0.2);
    color: var(--color-error);
    border: 1px solid rgba(239, 68, 68, 0.3);
}

.form-section {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.form-section-title {
    color: var(--color-info);
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
    padding-bottom: 4px;
}

.control-button {
    transition: all 0.2s ease;
}

.control-button:hover {
    transform: translateY(-1px);
    opacity: 0.9;
}

.dialog-section {
    background: var(--color-bg-elevated);
    border: 1px solid var(--color-border-default);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
}



.variable-chip {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin: 2px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
}

.variable-chip:hover {
    background: rgba(59, 130, 246, 0.25);
    border-color: rgba(59, 130, 246, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.2);
}

.variable-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

.variable-help-text {
    background: rgba(245, 101, 101, 0.1);
    border: 1px solid rgba(245, 101, 101, 0.2);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 11px;
    color: var(--color-error);
    margin-top: 8px;
}

/* Tab styling */
.chatbot-tab {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.chatbot-tab.active {
    background: rgba(59, 130, 246, 0.2);
    border-color: rgba(59, 130, 246, 0.5);
    color: var(--color-info);
}

/* Search field styling - matching connectors theme */
.search-container {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    transition: none;
}

.search-container:focus-within {
    border: none;
    background: transparent;
}

.search-input .q-field__control::before {
    border-color: rgba(55, 65, 81, 0.4) !important;
}

.search-input .q-field__control::after {
    border-color: rgba(55, 55, 70, 0.8) !important;
}

.search-input .q-field__control {
    background: rgba(55, 55, 70, 0.9) !important;
    border-radius: 6px !important;
}

.search-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.search-input .q-field__label {
    color: rgba(255, 255, 255, 0.7) !important;
}

.search-input .q-field__control:hover {
    border-color: rgba(90, 90, 90, 0.6) !important;
}

.search-input .q-field__control:focus {
    border-color: #60a5fa !important;
    box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.2) !important;
}

.response-preview {
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 4px;
    padding: 8px;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    white-space: pre-wrap;
}

.counter-display {
    background: rgba(251, 191, 36, 0.1);
    border: 1px solid rgba(251, 191, 36, 0.3);
    border-radius: 4px;
    padding: 8px;
    text-align: center;
}

.usage-display {
    background: rgba(139, 92, 246, 0.1);
    border: 1px solid rgba(139, 92, 246, 0.3);
    border-radius: 4px;
    padding: 8px;
    text-align: center;
}

/* Variable Panel Styles */
.variable-panel {
    background: rgba(30, 30, 30, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 16px;
}

.variable-category {
    background: rgba(45, 45, 45, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 6px;
    padding: 8px;
    margin-bottom: 8px;
}

.variable-category:hover {
    background: rgba(55, 55, 55, 0.5);
    border-color: rgba(255, 255, 255, 0.1);
}

.variable-chip {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 4px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin: 2px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
    display: inline-block;
}

.variable-chip:hover {
    background: rgba(59, 130, 246, 0.25);
    border-color: rgba(59, 130, 246, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.2);
}

.variable-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

/* Category specific colors */
.variable-chip[data-category="basic"] {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.variable-chip[data-category="stats"] {
    background: rgba(245, 101, 101, 0.1);
    border-color: rgba(245, 101, 101, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="api"] {
    background: rgba(192, 132, 252, 0.1);
    border-color: rgba(192, 132, 252, 0.3);
    color: var(--color-primary);
}

.variable-chip[data-category="custom"] {
    background: rgba(139, 92, 246, 0.1);
    border-color: rgba(139, 92, 246, 0.3);
    color: var(--color-primary);
}

.variable-chip[data-category="command_event"] {
    background: rgba(34, 197, 94, 0.1);
    border-color: rgba(34, 197, 94, 0.3);
    color: var(--color-success);
}

.variable-chip[data-category="youtube"] {
    background: rgba(239, 68, 68, 0.1);
    border-color: rgba(239, 68, 68, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="quotes"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

.variable-chip[data-category="youtube"] {
    background: rgba(239, 68, 68, 0.1);
    border-color: rgba(239, 68, 68, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="command-event"] {
    background: rgba(16, 185, 129, 0.1);
    border-color: rgba(16, 185, 129, 0.3);
    color: var(--color-success);
}

.variable-chip[data-category="quotes"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

/* Expression chips */
.expression-chip {
    background: rgba(75, 85, 99, 0.1);
    border: 1px solid rgba(75, 85, 99, 0.3);
    color: var(--color-text-secondary);
    padding: 3px 6px;
    border-radius: 8px;
    font-size: 10px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
    display: inline-block;
    margin: 1px;
    max-width: 100%;
    word-wrap: break-word;
}

.expression-chip:hover {
    background: rgba(75, 85, 99, 0.25);
    border-color: rgba(75, 85, 99, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(75, 85, 99, 0.2);
}

.expression-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

/* Expression category colors */
.expression-chip[data-category="time_formatting"] {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.expression-chip[data-category="math_operations"] {
    background: rgba(34, 197, 94, 0.1);
    border-color: rgba(34, 197, 94, 0.3);
    color: var(--color-success);
}

.expression-chip[data-category="comparisons"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

.expression-chip[data-category="date_functions"] {
    background: rgba(192, 132, 252, 0.1);
    border-color: rgba(192, 132, 252, 0.3);
    color: var(--color-primary);
}
"""


def create_chatbot_tab():
    """Create the Chatbot tab UI"""
    global commands_container, events_container, quotes_container, greetings_container
    global giveaways_container

    # Add custom CSS to the page
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes(
        "content-section w-full h-full flex flex-col relative"
    ):
        # Tabs at the very top
        with ui.tabs().classes("w-full bg-theme-base rounded-lg p-1") as chatbot_tabs:
            commands_tab = ui.tab("Commands").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            events_tab = ui.tab("Events").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            quotes_tab = ui.tab("Quotes").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            greetings_tab = ui.tab("Greetings").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            giveaways_tab = ui.tab("Giveaways").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )

        set_chatbot_ui_references(chatbot_tabs)

        # Main content area with tab panels - each panel contains its own buttons and content
        with ui.tab_panels(chatbot_tabs, value=commands_tab).classes(
            "w-full flex-grow"
        ):
            # Commands Tab
            with ui.tab_panel(commands_tab).classes(
                "transition-all duration-300 w-full h-full flex flex-col"
            ):
                # Tab-specific header with buttons and search
                with ui.row().classes(
                    "w-full items-center justify-between p-4 pb-2 flex-none gap-2"
                ):
                    # Left side - tab-specific action buttons
                    with ui.row().classes("items-center gap-2"):
                        primary_button(
                            "New Command",
                            lambda: show_create_chatbot_dialog("command"),
                            icon="add",
                            extra_classes="px-4 py-2",
                        )

                        outline_button(
                            "Refresh",
                            lambda: refresh_tab_content("commands"),
                            icon="refresh",
                            extra_classes="px-3 py-2",
                        )

                    # Right side - Tab-specific search box
                    with ui.row().classes(
                        "items-center gap-2 flex-1 justify-end max-w-md"
                    ):
                        search_input_commands = (
                            form_input(
                                tooltip="Filter commands by name, alias, or description",
                                label="🔍 Search commands",
                                placeholder="Search by name, command, aliases, or description...",
                                value="",
                            )
                            .classes("search-input w-full")
                            .props("clearable")
                        )

                        # Update search on input change for commands tab
                        def on_commands_search_change(event):
                            global search_term
                            search_term = event.value or ""
                            refresh_tab_content("commands")

                        search_input_commands.on_value_change(on_commands_search_change)

                # Commands content area
                global commands_container
                with ui.scroll_area().classes("w-full flex-grow"):
                    commands_container = ui.element("div").classes("w-full p-4")

            # Events Tab
            with ui.tab_panel(events_tab).classes(
                "transition-all duration-300 w-full h-full flex flex-col"
            ):
                # Tab-specific header with buttons and search
                with ui.row().classes(
                    "w-full items-center justify-between p-4 pb-2 flex-none gap-2"
                ):
                    # Left side - tab-specific action buttons
                    with ui.row().classes("items-center gap-2"):
                        primary_button(
                            "New Event",
                            lambda: show_create_chatbot_dialog("event"),
                            icon="celebration",
                            extra_classes="px-4 py-2",
                        )

                        outline_button(
                            "Refresh",
                            lambda: refresh_tab_content("events"),
                            icon="refresh",
                            extra_classes="px-3 py-2",
                        )

                    # Right side - Tab-specific search box
                    with ui.row().classes(
                        "items-center gap-2 flex-1 justify-end max-w-md"
                    ):
                        search_input_events = (
                            form_input(
                                tooltip="Filter events by name or description",
                                label="🔍 Search events",
                                placeholder="Search by name, event type, or description...",
                                value="",
                            )
                            .classes("search-input w-full")
                            .props("clearable")
                        )

                        # Update search on input change for events tab
                        def on_events_search_change(event):
                            global search_term
                            search_term = event.value or ""
                            refresh_tab_content("events")

                        search_input_events.on_value_change(on_events_search_change)

                # Events content area
                global events_container
                with ui.scroll_area().classes("w-full flex-grow"):
                    events_container = ui.element("div").classes("w-full p-4")

            # Quotes Tab
            with ui.tab_panel(quotes_tab).classes(
                "transition-all duration-300 w-full h-full flex flex-col"
            ):
                # Tab-specific header with buttons and search
                with ui.row().classes(
                    "w-full items-center justify-between p-4 pb-2 flex-none gap-2"
                ):
                    # Left side - tab-specific action buttons
                    with ui.row().classes("items-center gap-2"):
                        primary_button(
                            "New Quote",
                            lambda: show_create_chatbot_dialog("quote"),
                            icon="format_quote",
                            extra_classes="px-4 py-2",
                        )

                        outline_button(
                            "Refresh",
                            lambda: refresh_tab_content("quotes"),
                            icon="refresh",
                            extra_classes="px-3 py-2",
                        )

                        outline_button(
                            "Settings",
                            show_quote_settings_dialog,
                            icon="settings",
                            extra_classes="px-3 py-2",
                        )

                    # Right side - Tab-specific search box
                    with ui.row().classes(
                        "items-center gap-2 flex-1 justify-end max-w-md"
                    ):
                        search_input_quotes = (
                            form_input(
                                tooltip="Filter quotes by text or author",
                                label="🔍 Search quotes",
                                placeholder="Search by text, author, or ID...",
                                value="",
                            )
                            .classes("search-input w-full")
                            .props("clearable")
                        )

                        # Update search on input change for quotes tab
                        def on_quotes_search_change(event):
                            global search_term
                            search_term = event.value or ""
                            refresh_tab_content("quotes")

                        search_input_quotes.on_value_change(on_quotes_search_change)

                # Quotes content area
                global quotes_container
                with ui.scroll_area().classes("w-full flex-grow"):
                    quotes_container = ui.element("div").classes("w-full p-4")

            # Greetings Tab
            with ui.tab_panel(greetings_tab).classes(
                "transition-all duration-300 w-full h-full flex flex-col"
            ):
                # Tab-specific header with buttons and search
                with ui.row().classes(
                    "w-full items-center justify-between p-4 pb-2 flex-none gap-2"
                ):
                    # Left side - tab-specific action buttons
                    with ui.row().classes("items-center gap-2"):
                        primary_button(
                            "New Greeting",
                            lambda: show_create_greeting_dialog(),
                            icon="waving_hand",
                            extra_classes="px-4 py-2",
                        )

                        outline_button(
                            "Refresh",
                            lambda: refresh_tab_content("greetings"),
                            icon="refresh",
                            extra_classes="px-3 py-2",
                        )

                        outline_button(
                            "Settings",
                            show_greeting_settings_dialog,
                            icon="settings",
                            extra_classes="px-3 py-2",
                        )

                    # Right side - Tab-specific search box
                    with ui.row().classes(
                        "items-center gap-2 flex-1 justify-end max-w-md"
                    ):
                        search_input_greetings = (
                            form_input(
                                tooltip="Filter greetings by name or message",
                                label="🔍 Search greetings",
                                placeholder="Search by username or greeting text...",
                                value="",
                            )
                            .classes("search-input w-full")
                            .props("clearable")
                        )

                        # Update search on input change for greetings tab
                        def on_greetings_search_change(event):
                            global search_term
                            search_term = event.value or ""
                            refresh_tab_content("greetings")

                        search_input_greetings.on_value_change(
                            on_greetings_search_change
                        )

                # Greetings content area
                global greetings_container
                with ui.scroll_area().classes("w-full flex-grow"):
                    greetings_container = ui.element("div").classes("w-full p-4")

            # Giveaways Tab
            with ui.tab_panel(giveaways_tab).classes(
                "transition-all duration-300 w-full h-full flex flex-col"
            ):
                with ui.row().classes(
                    "w-full items-center justify-between p-4 pb-2 flex-none gap-2"
                ):
                    with ui.row().classes("items-center gap-2"):
                        outline_button(
                            "Refresh",
                            lambda: refresh_tab_content("giveaways"),
                            icon="refresh",
                            extra_classes="px-3 py-2",
                        )

                with ui.scroll_area().classes("w-full flex-grow"):
                    giveaways_container = ui.element("div").classes("w-full p-4")

        # Load and display chatbot items for all tabs
        load_chatbot_items()

    # Load custom variables from persistent storage
    try:
        custom_vars = load_custom_variables()
        logger.info(
            f"Loaded {len(custom_vars)} custom variables on chatbot tab initialization"
        )
    except Exception as e:
        logger.error(f"Error loading custom variables on initialization: {e}")


def render_giveaways_tab(container_el) -> None:
    """Build the Giveaways sub-tab (settings, actions, stats + entrants panel)."""
    gm = get_giveaway_manager()
    cfg = gm.get_config()

    def reload_giveaways():
        refresh_tab_content("giveaways")

    def _giveaway_switch(text: str, tooltip: str, field: str, default=False):
        sw = ui.switch(
            text=text,
            value=bool(cfg.get(field, default)),
            on_change=lambda e, f=field: (
                gm.set_config_field(f, bool(e.value)),
                reload_giveaways(),
            ),
        ).classes("w-full")
        sw.tooltip(tooltip).classes("bg-theme-surface")
        return sw

    with container_el:
        with ui.column().classes("w-full gap-3 p-4"):
            active = gm.is_giveaway_active()
            ui.label("Giveaways").classes("text-xl font-medium")
            with ui.row().classes("items-center gap-4 flex-wrap"):
                ui.label(
                    "Active giveaway: Yes (chat entries on)"
                    if active
                    else "Active giveaway: No"
                ).classes("text-sm secondary-text")
                ui.label(f"Pool size: {gm.get_pool_size()}").classes(
                    "text-sm secondary-text"
                )
                ui.label(f"Winners per draw: {cfg.get('num_winners', 1)}").classes(
                    "text-sm muted-text"
                )

            err = gm.get_last_error()
            if err:
                ui.label(f"Last issue: {err}").classes("text-sm text-amber-600")

            with ui.row().classes("w-full gap-4 flex-wrap items-start"):
                keyword_in = form_input(
                    tooltip="Exact chat message viewers must send to enter (case-insensitive)",
                    label="Entry keyword",
                    value=cfg.get("keyword") or "",
                    placeholder="!enter",
                    classes="flex-1 min-w-[12rem]",
                )

                def save_keyword(_=None):
                    gm.set_config_field("keyword", (keyword_in.value or "").strip())
                    reload_giveaways()

                keyword_in.on("blur", save_keyword)

                num_in = form_number(
                    tooltip="How many winners are drawn each time you run Draw winners",
                    label="Winners per draw",
                    value=int(cfg.get("num_winners", 1)),
                    min=1,
                    max=100,
                    step=1,
                    classes="w-40",
                )

                def save_num(_=None):
                    try:
                        v = int(num_in.value)
                    except (TypeError, ValueError):
                        v = 1
                    gm.set_config_field("num_winners", v)
                    reload_giveaways()

                num_in.on("blur", save_num)

            with ui.row().classes("w-full gap-4 flex-wrap"):
                blocked_in = form_textarea(
                    tooltip="Usernames blocked from entering (one per line or comma-separated)",
                    label="Blocked usernames",
                    value="\n".join(cfg.get("blocked_usernames") or []),
                    rows=2,
                    classes="flex-1 min-w-[14rem]",
                )

                def save_blocked(_=None):
                    raw = blocked_in.value or ""
                    parts = re.split(r"[\n,]+", raw)
                    gm.set_config_field(
                        "blocked_usernames",
                        [p.strip().lower() for p in parts if p.strip()],
                    )
                    reload_giveaways()

                blocked_in.on("blur", save_blocked)

                win_msg = form_textarea(
                    tooltip="Chat message sent when winners are drawn. Use {winners} or {winner} for names.",
                    label="Winning announcement",
                    value=cfg.get("winning_message_template")
                    or "Congratulations {winners}!",
                    rows=2,
                    classes="flex-1 min-w-[14rem]",
                )

                def save_template(_=None):
                    gm.set_config_field(
                        "winning_message_template", (win_msg.value or "").strip()
                    )
                    reload_giveaways()

                win_msg.on("blur", save_template)

            with ui.row().classes("w-full gap-4 items-stretch min-h-[280px]"):
                with ui.column().classes(
                    "flex-[2] min-w-0 gap-0 rounded-lg border border-[var(--color-border)]"
                ):
                    with ui.row().classes(
                        "w-full items-center justify-between px-4 py-2 "
                        "bg-[var(--color-bg-elevated)] rounded-t-lg border-b "
                        "border-[var(--color-border)]"
                    ):
                        ui.label("Giveaway Entrants").classes("text-lg font-medium")
                        pool_count = gm.get_pool_size()
                        ui.badge(
                            str(pool_count),
                            color="primary" if pool_count else "grey",
                        ).props("rounded")

                    entrants_container = ui.column().classes("w-full flex-1 min-h-0")

                    def _build_entrants_list():
                        entrants_container.clear()
                        names = get_giveaway_manager().get_pool_entries()
                        with entrants_container:
                            with (
                                ui.scroll_area()
                                .classes("w-full")
                                .style(
                                    "min-height: 240px; "
                                    "background: var(--color-bg-sunken, #0d1117); "
                                    "border-bottom-left-radius: 0.5rem; "
                                    "border-bottom-right-radius: 0.5rem;"
                                )
                            ):
                                with ui.column().classes("w-full gap-0 p-2"):
                                    if not names:
                                        ui.label("No entries yet").classes(
                                            "text-sm muted-text italic py-4 self-center"
                                        )
                                    else:
                                        for name in names:
                                            ui.label(name).classes(
                                                "text-sm px-3 py-1.5 "
                                                "hover:bg-[var(--color-bg-overlay)] "
                                                "rounded transition-colors"
                                            ).style(
                                                "font-family: 'Segoe UI', 'Cascadia Code', "
                                                "monospace; user-select: none;"
                                            )

                    _build_entrants_list()
                    ui.timer(2.5, callback=_build_entrants_list)

                with ui.column().classes("flex-1 min-w-[14rem] gap-1"):
                    ui.label("Options").classes("text-sm font-medium mb-1")
                    with ui.grid(columns=2).classes("w-full gap-1"):
                        _giveaway_switch(
                            "No duplicate entries",
                            "One ticket per user in the entry pool",
                            "no_duplicate_entries",
                            True,
                        )
                        _giveaway_switch(
                            "Unique winners per draw",
                            "Same user cannot win more than one slot in a single draw",
                            "unique_winners_per_draw",
                            True,
                        )
                        _giveaway_switch(
                            "Remove winners from pool",
                            "Remove drawn winners from the pool after each draw",
                            "remove_winners_from_pool",
                            True,
                        )
                        _giveaway_switch(
                            "Exclude moderators",
                            "Moderators cannot enter the giveaway",
                            "exclude_mods",
                        )
                        _giveaway_switch(
                            "Exclude VIPs",
                            "VIP badge holders cannot enter the giveaway",
                            "exclude_vips",
                        )

            def do_start():
                ok, msg = get_giveaway_manager().start_giveaway()
                notify(
                    msg or "Accepting entries.",
                    type="positive" if ok else "negative",
                )
                reload_giveaways()

            def do_stop():
                get_giveaway_manager().stop_accepting()
                notify("Stopped accepting new entries.", type="info")
                reload_giveaways()

            def do_draw():
                ok, msg, _w = get_giveaway_manager().draw_winners()
                notify(msg, type="positive" if ok else "negative")
                reload_giveaways()

            def do_clear():
                get_giveaway_manager().clear_giveaway()
                notify("Giveaway cleared (pool and keyword).", type="info")
                reload_giveaways()

            with ui.row().classes("flex-wrap gap-2"):
                ui.button(
                    icon="play_arrow",
                    text="Start giveaway",
                    on_click=do_start,
                ).classes("control-button btn-success px-3 py-2")
                ui.button(
                    icon="stop",
                    text="Stop accepting",
                    on_click=do_stop,
                ).classes("control-button btn-cancel px-3 py-2")
                ui.button(
                    icon="casino",
                    text="Draw winners",
                    on_click=do_draw,
                ).classes("control-button btn-primary px-3 py-2")
                ui.button(
                    icon="delete_sweep",
                    text="Clear giveaway",
                    on_click=do_clear,
                ).classes("control-button btn-danger px-3 py-2")
                ui.button(
                    icon="refresh",
                    text="Refresh",
                    on_click=reload_giveaways,
                ).classes("control-button btn-secondary px-3 py-2")

            ui.label("Statistics").classes("text-lg font-medium")
            try:
                st = (
                    get_statistics_manager()
                    .get_all_statistics()
                    .get("giveaways", {})
                )
                done = int(st.get("giveaways_completed", 0) or 0)
                entries = int(st.get("total_entry_events", 0) or 0)
                avg = float(st.get("average_entries_per_giveaway", 0) or 0)
                ui.label(f"Giveaways completed: {done:,}").classes("text-sm")
                ui.label(f"Total giveaway entries: {entries:,}").classes("text-sm")
                ui.label(f"Average entries per giveaway: {avg:.2f}").classes(
                    "text-sm"
                )
                top = get_statistics_manager().get_top_users_by_statistic(
                    "giveaway_wins", 8
                )
                if top:
                    ui.label("Top giveaway wins").classes(
                        "text-sm font-medium mt-2"
                    )
                    for row in top:
                        ui.label(
                            f"{row.get('username', '?')}: {row.get('value', 0):,}"
                        ).classes("text-sm muted-text pl-2")
            except Exception as ex:
                ui.label(f"Stats unavailable: {ex}").classes("text-sm text-red-400")


def refresh_tab_content(tab_type: str):
    """Refresh the content of a specific tab"""
    global search_term

    # Get the appropriate container based on tab type
    container = get_container_for_tab(tab_type)
    if container is None:
        logger.error(f"Tab container not initialized for {tab_type}")
        return

    if tab_type == "giveaways":
        container.clear()
        try:
            render_giveaways_tab(container)
        except Exception as e:
            logger.error("Giveaways tab error: %s", e, exc_info=True)
            with container:
                ui.label(f"Error loading giveaways: {e}").classes("text-red-400")
        return

    # Clear existing content
    container.clear()

    try:
        manager = get_chatbot_manager()
        if tab_type == "commands":
            items = manager.get_all_commands()
        elif tab_type == "events":
            items = manager.get_all_events()
        elif tab_type == "quotes":
            items = manager.get_all_quotes()
        else:  # greetings
            # Only show custom greetings, not default greeting tracking records
            all_greetings = manager.get_all_greetings()
            default_greeting_text = get_default_greeting()
            items = {
                gid: greeting
                for gid, greeting in all_greetings.items()
                if greeting.greeting_text != default_greeting_text
            }

        # Apply search filtering based on tab type
        if search_term:
            filtered_items = {}
            search_lower = search_term.lower()

            if tab_type == "commands":
                for item_id, item in items.items():
                    # Search in name, command_name, aliases, and description
                    searchable_text = (
                        f"{item.name} {item.command_name} {item.description or ''}"
                    )
                    if hasattr(item, "aliases") and item.aliases:
                        searchable_text += " " + " ".join(item.aliases)
                    if search_lower in searchable_text.lower():
                        filtered_items[item_id] = item

            elif tab_type == "events":
                for item_id, item in items.items():
                    # Search in name, event type (formatted), and description
                    event_name = (
                        format_event_name(item.event_type)
                        if hasattr(item, "event_type")
                        else ""
                    )
                    searchable_text = (
                        f"{item.name} {event_name} {item.description or ''}"
                    )
                    if search_lower in searchable_text.lower():
                        filtered_items[item_id] = item

            elif tab_type == "quotes":
                for item_id, item in items.items():
                    # Search in quote text and author
                    author = item.author or ""
                    searchable_text = f"{item.text} {author}"
                    if search_lower in searchable_text.lower():
                        filtered_items[item_id] = item

            elif tab_type == "greetings":
                for item_id, item in items.items():
                    # Search in username
                    if search_lower in item.username.lower():
                        filtered_items[item_id] = item

            items = filtered_items

        with container:
            if not items:
                tab_name = tab_type
                with ui.column().classes(
                    "w-full h-full flex flex-col items-center justify-center gap-4 p-8"
                ):
                    ui.icon("chat_bubble_outline", size="4rem").classes("muted-text")
                    if search_term:
                        ui.label(f"No {tab_name} found for '{search_term}'").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            "Try a different search term or clear the search"
                        ).classes("text-sm muted-text fade-in")
                    else:
                        ui.label(f"No {tab_name} created yet").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            f"Create your first {tab_name[:-1]} to automate your chat"
                        ).classes("text-sm muted-text fade-in")

                    if tab_type == "commands":
                        button_text = "Create First Command"
                    elif tab_type == "events":
                        button_text = "Create First Event"
                    elif tab_type == "quotes":
                        button_text = "Create First Quote"
                    else:
                        button_text = "Create First Greeting"
                    ui.button(
                        icon="add",
                        text=button_text,
                        on_click=lambda: show_create_chatbot_dialog(
                            tab_type[:-1]
                        ),  # Remove 's'
                    ).classes("control-button btn-secondary px-6 py-3 mt-4")
            else:
                # Display items in a grid
                with ui.element("div").classes(
                    "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                ):
                    for item_id, item in items.items():
                        if tab_type == "commands":
                            create_command_card(item_id, item)
                        elif tab_type == "events":
                            create_event_card(item_id, item)
                        elif tab_type == "quotes":
                            create_quote_card(item_id, item)
                        else:  # greetings
                            create_greeting_card(item_id, item)

    except Exception as e:
        logger.error(f"Error loading chatbot items: {e}", exc_info=True)
        with container:
            ui.label(f"Error loading chatbot items: {str(e)}").classes("text-red-400")


def get_container_for_tab(tab_type: str):
    """Get the container element for a specific tab"""
    global commands_container, events_container, quotes_container, greetings_container
    global giveaways_container

    if tab_type == "commands":
        return commands_container
    elif tab_type == "events":
        return events_container
    elif tab_type == "quotes":
        return quotes_container
    elif tab_type == "greetings":
        return greetings_container
    elif tab_type == "giveaways":
        return giveaways_container
    else:
        return None


def load_chatbot_items():
    """Load and display chatbot items for all tabs initially"""

    # Load commands tab
    container = get_container_for_tab("commands")
    if container:
        container.clear()
        try:
            manager = get_chatbot_manager()
            items = manager.get_all_commands()
            with container:
                if not items:
                    tab_name = "commands"
                    with ui.column().classes(
                        "w-full h-full flex flex-col items-center justify-center gap-4 p-8"
                    ):
                        ui.icon("chat_bubble_outline", size="4rem").classes(
                            "muted-text"
                        )
                        ui.label(f"No {tab_name} created yet").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            f"Create your first {tab_name[:-1]} to automate your chat"
                        ).classes("text-sm muted-text fade-in")
                        ui.button(
                            icon="add",
                            text="Create First Command",
                            on_click=lambda: show_create_chatbot_dialog("command"),
                        ).classes("control-button btn-secondary px-6 py-3 mt-4")
                else:
                    # Display items in a grid
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                    ):
                        for item_id, item in items.items():
                            create_command_card(item_id, item)
        except Exception as e:
            logger.error(f"Error loading commands: {str(e)}", exc_info=True)
            with container:
                ui.label("Error loading commands").classes("text-red-400")

    # Load events tab
    container = get_container_for_tab("events")
    if container:
        container.clear()
        try:
            manager = get_chatbot_manager()
            items = manager.get_all_events()
            with container:
                if not items:
                    tab_name = "events"
                    with ui.column().classes(
                        "w-full h-full flex flex-col items-center justify-center gap-4 p-8"
                    ):
                        ui.icon("chat_bubble_outline", size="4rem").classes(
                            "muted-text"
                        )
                        ui.label(f"No {tab_name} created yet").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            f"Create your first {tab_name[:-1]} to automate your chat"
                        ).classes("text-sm muted-text fade-in")
                        ui.button(
                            icon="add",
                            text="Create First Event",
                            on_click=lambda: show_create_chatbot_dialog("event"),
                        ).classes("control-button btn-secondary px-6 py-3 mt-4")
                else:
                    # Display items in a grid
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                    ):
                        for item_id, item in items.items():
                            create_event_card(item_id, item)
        except Exception as e:
            logger.error(f"Error loading events: {str(e)}", exc_info=True)
            with container:
                ui.label("Error loading events").classes("text-red-400")

    # Load quotes tab
    container = get_container_for_tab("quotes")
    if container:
        container.clear()
        try:
            manager = get_chatbot_manager()
            items = manager.get_all_quotes()
            with container:
                if not items:
                    tab_name = "quotes"
                    with ui.column().classes(
                        "w-full h-full flex flex-col items-center justify-center gap-4 p-8"
                    ):
                        ui.icon("chat_bubble_outline", size="4rem").classes(
                            "muted-text"
                        )
                        ui.label(f"No {tab_name} created yet").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            f"Create your first {tab_name[:-1]} to automate your chat"
                        ).classes("text-sm muted-text fade-in")
                        ui.button(
                            icon="add",
                            text="Create First Quote",
                            on_click=lambda: show_create_chatbot_dialog("quote"),
                        ).classes("control-button btn-secondary px-6 py-3 mt-4")
                else:
                    # Display items in a grid
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                    ):
                        for item_id, item in items.items():
                            create_quote_card(item_id, item)
        except Exception as e:
            logger.error(f"Error loading quotes: {str(e)}", exc_info=True)
            with container:
                ui.label("Error loading quotes").classes("text-red-400")

    # Load greetings tab
    container = get_container_for_tab("greetings")
    if container:
        container.clear()
        try:
            manager = get_chatbot_manager()
            all_greetings = manager.get_all_greetings()
            default_greeting_text = get_default_greeting()
            items = {
                gid: greeting
                for gid, greeting in all_greetings.items()
                if greeting.greeting_text != default_greeting_text
            }
            with container:
                if not items:
                    tab_name = "greetings"
                    with ui.column().classes(
                        "w-full h-full flex flex-col items-center justify-center gap-4 p-8"
                    ):
                        ui.icon("chat_bubble_outline", size="4rem").classes(
                            "muted-text"
                        )
                        ui.label(f"No {tab_name} created yet").classes(
                            "text-lg secondary-text fade-in"
                        )
                        ui.label(
                            f"Create your first {tab_name[:-1]} to automate your chat"
                        ).classes("text-sm muted-text fade-in")
                        ui.button(
                            icon="add",
                            text="Create First Greeting",
                            on_click=lambda: show_create_greeting_dialog(),
                        ).classes("control-button btn-secondary px-6 py-3 mt-4")
                else:
                    # Display items in a grid
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                    ):
                        for item_id, item in items.items():
                            create_greeting_card(item_id, item)
        except Exception as e:
            logger.error(f"Error loading greetings: {str(e)}", exc_info=True)
            with container:
                ui.label("Error loading greetings").classes("text-red-400")

    # Giveaways tab
    container = get_container_for_tab("giveaways")
    if container:
        container.clear()
        try:
            render_giveaways_tab(container)
        except Exception as e:
            logger.error("Error loading giveaways: %s", e, exc_info=True)
            with container:
                ui.label(f"Error loading giveaways: {e}").classes("text-red-400")


def create_command_card(command_id: str, command: ChatCommand):
    """Create a card display for a command"""
    card_classes = "chatbot-card command p-4 rounded-lg"
    if not command.enabled:
        card_classes += " disabled"

    with ui.element("div").classes(card_classes):
        # Header row with name and status
        with ui.row().classes("w-full items-center justify-between mb-3"):
            with ui.column().classes("gap-1 flex-grow"):
                ui.label(command.name).classes("text-base font-semibold")
                # Command name and aliases underneath
                command_text = f"!{command.command_name}"
                if hasattr(command, "aliases") and command.aliases:
                    aliases_text = ", ".join([f"!{alias}" for alias in command.aliases])
                    command_text += f" (Aliases: {aliases_text})"
                ui.label(command_text).classes("text-xs text-blue-300")
                if command.description:
                    ui.label(command.description).classes("text-xs secondary-text")

            # Status badge and toggle
            with ui.column().classes("items-end gap-1"):
                status_classes = (
                    "status-badge status-enabled"
                    if command.enabled
                    else "status-badge status-disabled"
                )
                status_text = "Enabled" if command.enabled else "Disabled"
                ui.label(status_text).classes(status_classes)

                # Toggle switch
                ui.switch(
                    value=command.enabled,
                    on_change=lambda e, cid=command_id: toggle_chatbot_item(
                        cid, e.value
                    ),
                ).classes("scale-75")

        # Badges row (mod-only, counter, repeating)
        with ui.row().classes("w-full items-center gap-2 mb-3"):
            if command.mod_only:
                ui.label("Mod Only").classes("mod-only-badge")

            if command.command_type == CommandType.COUNTER:
                ui.label("Counter").classes("counter-badge")

            if command.repeating_enabled:
                repeat_text = (
                    f"Every {command.repeat_interval}s"
                    if command.repeat_interval
                    else f"{command.repeat_count}x"
                )
                ui.label(f"Repeating: {repeat_text}").classes("repeating-badge")

            if command.usage_limit > 0:
                ui.label(f"Limit: {command.usage_limit}").classes("usage-display")

        # Response preview
        with ui.column().classes("w-full mb-3"):
            ui.label("Response:").classes("text-xs secondary-text mb-1")
            # Ensure response_text is a string, not an event object
            response_text = command.response_text
            if not isinstance(response_text, str):
                response_text = str(response_text) if response_text else ""
            # Resolve variables with live data
            resolved_text = resolve_variables_for_preview(
                response_text,
                "command",
                None,
                command.command_type.value
                if hasattr(command.command_type, "value")
                else "basic",
                {
                    "command_name": command.command_name,
                    "cooldown": command.cooldown,
                    "usage_limit": command.usage_limit,
                    "usage_count": command.usage_count,
                    "counter_value": getattr(command, "counter_value", 0),
                },
            )
            # Truncate if needed
            if len(response_text) > 100:
                # Truncate the resolved HTML while preserving tags
                truncated_html = resolved_text[:200] + "..."
            else:
                truncated_html = resolved_text
            ui.html(truncated_html).classes("text-xs secondary-text")

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-gray-700"
        ):
            if command.command_type == CommandType.COUNTER:
                ui.label(f"Count: {command.counter_value}")
            else:
                ui.label(f"Used: {command.usage_count}x")

            if command.last_used > 0:
                import datetime

                last_used = datetime.datetime.fromtimestamp(command.last_used)
                ui.label(f"Last: {last_used.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Never used")

        # Action buttons
        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.button(
                icon="edit",
                text="Edit",
                on_click=lambda cid=command_id: show_edit_chatbot_dialog(
                    cid, "command"
                ),
            ).classes("control-button btn-secondary text-xs px-3 py-1 flex-grow")

            if command.command_type == CommandType.COUNTER:
                ui.button(
                    icon="replay",
                    text="Reset",
                    on_click=lambda cid=command_id: reset_command_counter(cid),
                ).classes("control-button btn-warning text-xs px-3 py-1 flex-grow")

            ui.button(
                icon="play_arrow",
                text="Test",
                on_click=lambda cid=command_id: test_chatbot_item(cid, "command"),
            ).classes("control-button btn-success text-xs px-3 py-1 flex-grow")

            ui.button(
                icon="delete",
                text="Delete",
                on_click=lambda cid=command_id: delete_chatbot_item(cid, "command"),
            ).classes("control-button btn-danger text-xs px-3 py-1 flex-grow")


def create_event_card(event_id: str, event: ChatEvent):
    """Create a card display for an event"""
    card_classes = "chatbot-card event p-4 rounded-lg"
    if not event.enabled:
        card_classes += " disabled"

    with ui.element("div").classes(card_classes):
        # Header row with name and status
        with ui.row().classes("w-full items-center justify-between mb-3"):
            with ui.column().classes("gap-1 flex-grow"):
                ui.label(event.name).classes("text-base font-semibold")
                # Event type underneath with interval for interval events
                event_type_text = format_event_name(event.event_type)
                if event.event_type == EventType.INTERVAL and event.interval > 0:
                    interval_text = format_interval_human_readable(event.interval)
                    if interval_text:
                        event_type_text += f" ({interval_text})"
                ui.label(event_type_text).classes("text-xs text-red-300")
                if event.description:
                    ui.label(event.description).classes("text-xs secondary-text")

            # Status badge and toggle
            with ui.column().classes("items-end gap-1"):
                status_classes = (
                    "status-badge status-enabled"
                    if event.enabled
                    else "status-badge status-disabled"
                )
                status_text = "Enabled" if event.enabled else "Disabled"
                ui.label(status_text).classes(status_classes)

                # Toggle switch
                ui.switch(
                    value=event.enabled,
                    on_change=lambda e, eid=event_id: toggle_chatbot_item(eid, e.value),
                ).classes("scale-75")

        # Response preview
        with ui.column().classes("w-full mb-3"):
            ui.label("Response:").classes("text-xs secondary-text mb-1")
            response_text = event.response_text
            # Resolve variables with live data
            event_type_value = (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            )
            resolved_text = resolve_variables_for_preview(
                response_text, "event", event_type_value, None, {}
            )
            # Truncate if needed
            if len(response_text) > 100:
                # Truncate the resolved HTML while preserving tags
                truncated_html = resolved_text[:200] + "..."
            else:
                truncated_html = resolved_text
            ui.html(truncated_html).classes("text-xs secondary-text")

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-gray-700"
        ):
            ui.label(f"Triggered: {event.trigger_count}x")
            if event.last_triggered > 0:
                import datetime

                last_triggered = datetime.datetime.fromtimestamp(event.last_triggered)
                ui.label(f"Last: {last_triggered.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Never triggered")

        # Action buttons
        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.button(
                icon="edit",
                text="Edit",
                on_click=lambda eid=event_id: show_edit_chatbot_dialog(eid, "event"),
            ).classes("control-button btn-secondary text-xs px-3 py-1 flex-grow")

            ui.button(
                icon="play_arrow",
                text="Test",
                on_click=lambda eid=event_id: test_chatbot_item(eid, "event"),
            ).classes("control-button btn-warning text-xs px-3 py-1 flex-grow")

            ui.button(
                icon="delete",
                text="Delete",
                on_click=lambda eid=event_id: delete_chatbot_item(eid, "event"),
            ).classes("control-button btn-danger text-xs px-3 py-1 flex-grow")


def create_quote_card(quote_id: str, quote):
    """Create a card display for a quote"""
    card_classes = "chatbot-card quote p-4 rounded-lg"

    with ui.element("div").classes(card_classes):
        # Header row with quote number and actions
        with ui.row().classes("w-full items-center justify-between mb-3"):
            with ui.column().classes("gap-1 flex-grow"):
                ui.label(f"Quote #{quote.quote_number}").classes(
                    "text-base font-semibold"
                )
                if quote.author:
                    ui.label(f"by {quote.author}").classes("text-xs secondary-text")
                else:
                    ui.label("Anonymous").classes("text-xs muted-text italic")

            # Action buttons
            with ui.column().classes("items-end gap-2"):
                ui.button(
                    icon="edit",
                    text="Edit",
                    on_click=lambda qid=quote_id: show_edit_quote_dialog(qid),
                ).classes("control-button btn-secondary text-xs px-3 py-1")

                ui.button(
                    icon="delete",
                    text="Delete",
                    on_click=lambda qid=quote_id: delete_quote_item(qid),
                ).classes("control-button btn-danger text-xs px-3 py-1")

        # Quote text
        with ui.column().classes("w-full mb-3"):
            ui.label("Quote:").classes("text-xs secondary-text mb-1")
            quote_preview = (
                quote.text[:150] + "..." if len(quote.text) > 150 else quote.text
            )
            ui.label(f'"{quote_preview}"').classes(
                "text-sm secondary-text italic p-3 bg-theme-surface rounded border-l-4 border-theme-primary"
            )

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-gray-700"
        ):
            if quote.added_by:
                ui.label(f"Added by: {quote.added_by}")
            else:
                ui.label("Added by: Unknown")

            if quote.date_added > 0:
                import datetime

                added_date = datetime.datetime.fromtimestamp(quote.date_added)
                ui.label(f"Added: {added_date.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Date unknown")


def create_greeting_card(greeting_id: str, greeting):
    """Create a card display for a greeting"""
    card_classes = "chatbot-card greeting p-4 rounded-lg"

    with ui.element("div").classes(card_classes):
        # Header row with username and status
        with ui.row().classes("w-full items-center justify-between mb-3"):
            with ui.column().classes("gap-1 flex-grow"):
                ui.label(f"@{greeting.username}").classes("text-base font-semibold")
                if greeting.user_id:
                    ui.label(f"ID: {greeting.user_id}").classes("text-xs text-cyan-300")

            # Status badge and toggle
            with ui.column().classes("items-end gap-1"):
                status_classes = (
                    "status-badge status-enabled"
                    if greeting.enabled
                    else "status-badge status-disabled"
                )
                status_text = "Enabled" if greeting.enabled else "Disabled"
                ui.label(status_text).classes(status_classes)

                # Toggle switch
                ui.switch(
                    value=greeting.enabled,
                    on_change=lambda e, gid=greeting_id: toggle_greeting(gid, e.value),
                ).classes("scale-75")

        # Greeting text
        with ui.column().classes("w-full mb-3"):
            ui.label("Greeting:").classes("text-xs secondary-text mb-1")
            greeting_text = greeting.greeting_text
            greeting_username = greeting.username
            resolved_text = f"{greeting_text}".replace(
                "{username}", _wrap_variable_value("@" + greeting_username)
            )
            # Truncate if needed
            if len(greeting_text) > 100:
                # Truncate the resolved HTML while preserving tags
                truncated_html = resolved_text[:200] + "..."
            else:
                truncated_html = resolved_text
            ui.html(truncated_html).classes(
                "text-sm secondary-text p-3 bg-theme-surface rounded border-l-4 border-cyan-500"
            )

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-gray-700"
        ):
            if greeting.last_greeted > 0:
                import datetime

                last_greeted = datetime.datetime.fromtimestamp(greeting.last_greeted)
                ui.label(f"Last greeted: {last_greeted.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Never greeted")

            # Determine if this is a custom greeting or a default greeting used for tracking
            default_greeting_text = get_default_greeting()
            is_custom_greeting = greeting.greeting_text != default_greeting_text
            greeting_type = "Custom" if is_custom_greeting else "Default (Tracking)"
            ui.label(f"Type: {greeting_type}")

        # Action buttons
        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.button(
                icon="edit",
                text="Edit",
                on_click=lambda gid=greeting_id: show_edit_greeting_dialog(gid),
            ).classes("control-button btn-secondary text-xs px-3 py-1 flex-grow")

            ui.button(
                icon="delete",
                text="Delete",
                on_click=lambda gid=greeting_id: delete_greeting(gid),
            ).classes("control-button btn-danger text-xs px-3 py-1 flex-grow")


def format_interval_human_readable(seconds: int) -> str:
    """Format interval seconds into human-readable format like '1 hr 30 min', '30 min', '45 sec'"""
    if seconds <= 0:
        return ""

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours} hr")
    if minutes > 0:
        parts.append(f"{minutes} min")
    if secs > 0 and hours == 0:  # Only show seconds if no hours
        parts.append(f"{secs} sec")

    return " ".join(parts)


def format_event_name(event_type: EventType) -> str:
    """Format event type for display"""
    name_mapping = {
        EventType.FOLLOW: "New Follower",
        EventType.SUBSCRIPTION: "New Subscription",
        EventType.RESUBSCRIPTION: "Resubscription",
        EventType.GIFT_SUBSCRIPTION: "Gift Subscription",
        EventType.BITS: "Bits Donation",
        EventType.DONATION: "Donation",
        EventType.RAID: "Raid",
        EventType.HYPE_TRAIN_START: "Hype Train Start",
        EventType.HYPE_TRAIN_END: "Hype Train End",
        EventType.HYPE_TRAIN_PROGRESS: "Hype Train Progress",
        EventType.CHANNEL_POINT_REDEMPTION: "Channel Point Redemption",
        EventType.INTERVAL: "Interval",
        EventType.SPECIFIC_TIME: "Specific Time",
        EventType.CHAT_MESSAGE: "Chat Message",
    }
    return name_mapping.get(event_type, event_type.value.replace("_", " ").title())


def show_create_chatbot_dialog(item_type: str):
    """Show the create chatbot item dialog"""
    if item_type == "quote":
        show_create_quote_dialog()
    elif item_type == "greeting":
        show_create_greeting_dialog()
    else:
        show_chatbot_dialog(item_type=item_type)


def show_create_quote_dialog():
    """Show the create quote dialog"""
    global create_dialog

    if create_dialog:
        create_dialog.close()
        create_dialog = None

    # Create the dialog
    create_dialog = ui.dialog().props("persistent")

    with create_dialog:
        with ui.card().classes("w-[500px]"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Add New Quote").classes(
                        "text-lg font-semibold text-theme-primary"
                    )
                    ui.button(icon="close", on_click=create_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-4 gap-4"):
                    # Quote text input
                    quote_text = ui.textarea(
                        label="Quote Text",
                        placeholder="Enter the quote text...",
                        value="",
                    ).classes("w-full")

                    # Author input
                    author_input = form_input(
        tooltip="Author (optional)",
                        label="Author (optional)",
                        placeholder="Who said this quote?",
                        value="",
                    ).classes("w-full")

                    # Form buttons
                    with ui.row().classes("w-full items-center justify-end gap-2 mt-4"):
                        ui.button(text="Cancel", on_click=create_dialog.close).props(
                            "flat"
                        ).classes("secondary-text")

                        ui.button(
                            icon="save",
                            text="Add Quote",
                            on_click=lambda: save_new_quote(
                                quote_text.value, author_input.value
                            ),
                        ).classes("control-button btn-primary px-4 py-2")

    create_dialog.open()


def save_new_quote(text: str, author: str):
    """Save a new quote"""
    try:
        if not text.strip():
            notify("Quote text cannot be empty", type="negative")
            return

        manager = get_chatbot_manager()
        success, error, quote_number = manager.add_quote(
            text.strip(), author.strip(), "UI"
        )

        if success:
            notify(f"Quote #{quote_number} added successfully!", type="positive")
            if create_dialog:
                create_dialog.close()
            refresh_chatbot_items()
        else:
            notify(f"Error adding quote: {error}", type="negative")

    except Exception as e:
        logger.error(f"Error saving quote: {e}", exc_info=True)
        notify(f"Error saving quote: {str(e)}", type="negative")


def toggle_quote_system(enabled: bool):
    """Toggle the quote system enabled/disabled"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_quotes_enabled(enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Quote system {status}", type="positive")
        else:
            notify("Failed to toggle quote system", type="negative")

    except Exception as e:
        logger.error(f"Error toggling quote system: {e}", exc_info=True)
        notify(f"Error toggling quote system: {str(e)}", type="negative")


def toggle_greetings_system(enabled: bool):
    """Toggle the greetings system enabled/disabled"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_greetings_enabled(enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Greetings system {status}", type="positive")
        else:
            notify("Failed to toggle greetings system", type="negative")

    except Exception as e:
        logger.error(f"Error toggling greetings system: {e}", exc_info=True)
        notify(f"Error toggling greetings system: {str(e)}", type="negative")


def show_chatbot_dialog(item_id: Optional[str] = None, item_type: Optional[str] = None):
    """Show the create/edit chatbot dialog"""
    global create_dialog

    if create_dialog:
        create_dialog.close()
        create_dialog = None

    # Create the dialog with 80% window size for more space
    create_dialog = ui.dialog().props("persistent maximized")

    with create_dialog:
        with ui.card().classes("w-[80vw] h-[80vh] overflow-hidden"):
            with ui.column().classes("w-full h-full"):
                # Dialog header
                is_edit = item_id is not None
                title = (
                    f"Edit {item_type.title()}"
                    if is_edit and item_type
                    else f"Create New {item_type.title()}"
                    if item_type
                    else "Create New Item"
                )

                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label(title).classes("text-lg font-semibold text-blue-400")
                    ui.button(icon="close", on_click=create_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.scroll_area().classes("flex-grow p-4"):
                    create_chatbot_form(item_id, item_type)

    create_dialog.open()


def update_api_endpoint_info(
    endpoint_full_name,
    info_container,
    parameters_container,
    variables_container,
    form_data,
):
    """Update API endpoint information and response variables when endpoint is selected."""
    if not endpoint_full_name:
        info_container.clear()
        variables_container.clear()
        return

    # Clear existing content
    info_container.clear()
    parameters_container.clear()
    variables_container.clear()

    try:
        # Get endpoint information
        available_endpoints = get_available_api_endpoints()
        selected_endpoint = None

        for endpoint in available_endpoints:
            if endpoint["full_name"] == endpoint_full_name:
                selected_endpoint = endpoint
                break

        if selected_endpoint:
            # Display endpoint information
            with info_container:
                with ui.element("div").classes(
                    "bg-theme-surface rounded p-3 border border-gray-600"
                ):
                    ui.label("Endpoint Information:").classes(
                        "text-sm font-medium text-yellow-400 mb-2"
                    )
                    ui.label(f"Name: {selected_endpoint['name']}").classes(
                        "text-xs secondary-text"
                    )
                    ui.label(f"Method: {selected_endpoint['method']}").classes(
                        "text-xs secondary-text"
                    )
                    ui.label(f"Endpoint: {selected_endpoint['endpoint']}").classes(
                        "text-xs secondary-text"
                    )
                    ui.label(
                        f"Description: {selected_endpoint['description']}"
                    ).classes("text-xs secondary-text")

            # Show required parameters
            if selected_endpoint["required_params"]:
                with info_container:
                    ui.label("Required Parameters:").classes(
                        "text-xs font-medium text-orange-400 mt-2 mb-1"
                    )
                    for param_name, param_type in selected_endpoint[
                        "required_params"
                    ].items():
                        ui.label(f"• {param_name}: {param_type}").classes(
                            "text-xs secondary-text"
                        )

            # Show optional parameters
            if selected_endpoint["optional_params"]:
                with info_container:
                    ui.label("Optional Parameters:").classes(
                        "text-xs font-medium text-blue-400 mt-2 mb-1"
                    )
                    for param_name, param_type in selected_endpoint[
                        "optional_params"
                    ].items():
                        ui.label(f"• {param_name}: {param_type}").classes(
                            "text-xs secondary-text"
                        )

            # Display parameter inputs
            with parameters_container:
                ui.label("API Parameters:").classes(
                    "text-sm font-medium text-theme-primary mb-2"
                )

                if selected_endpoint:
                    # Get the endpoint object with full details
                    category, endpoint_name = endpoint_full_name.split(".", 1)
                    endpoint_obj = api_reference.get_endpoint_by_name(
                        category, endpoint_name
                    )

                    if endpoint_obj:
                        # Create parameter inputs for required parameters
                        if endpoint_obj.required_params:
                            ui.label("Required Parameters").classes(
                                "text-xs font-medium text-red-400 mb-2"
                            )

                            for (
                                param_name,
                                param_type,
                            ) in endpoint_obj.required_params.items():
                                # Create input field for required parameter
                                with ui.row().classes("items-center gap-2 mb-2"):
                                    ui.label(f"{param_name}*:").classes(
                                        "text-xs secondary-text min-w-20"
                                    )

                                    # Get auto-fill value if available
                                    auto_fill_value = ""
                                    if (
                                        endpoint_obj.auto_fill_params
                                        and param_name in endpoint_obj.auto_fill_params
                                    ):
                                        auto_fill_key = endpoint_obj.auto_fill_params[
                                            param_name
                                        ]
                                        auto_fill_value = get_auto_fill_value(
                                            auto_fill_key
                                        )

                                    input_field = form_input(
                                        tooltip=f"API parameter: {param_name}",
                                        label="",
                                        placeholder=f"Enter {param_type}",
                                        value=auto_fill_value,
                                    ).classes("flex-1 text-xs")

                                    # Store parameter value in form data
                                    def update_param_value(
                                        param=param_name, field=input_field
                                    ):
                                        current_params = form_data.get(
                                            "api_parameters", {}
                                        )
                                        current_params[param] = field.value
                                        form_data["api_parameters"] = current_params

                                    input_field.on_value_change(
                                        lambda e,
                                        p=param_name,
                                        f=input_field: update_param_value(p, f)
                                    )

                                    # Auto-fill if value is available
                                    if auto_fill_value:
                                        update_param_value(param_name, input_field)

                        # Create parameter inputs for optional parameters
                        if endpoint_obj.optional_params:
                            ui.label("Optional Parameters").classes(
                                "text-xs font-medium text-blue-400 mb-2 mt-4"
                            )

                            for (
                                param_name,
                                param_type,
                            ) in endpoint_obj.optional_params.items():
                                # Create input field for optional parameter
                                with ui.row().classes("items-center gap-2 mb-2"):
                                    ui.label(f"{param_name}:").classes(
                                        "text-xs secondary-text min-w-20"
                                    )

                                    # Get auto-fill value if available
                                    auto_fill_value = ""
                                    if (
                                        endpoint_obj.auto_fill_params
                                        and param_name in endpoint_obj.auto_fill_params
                                    ):
                                        auto_fill_key = endpoint_obj.auto_fill_params[
                                            param_name
                                        ]
                                        auto_fill_value = get_auto_fill_value(
                                            auto_fill_key
                                        )

                                    input_field = form_input(
                                        tooltip=f"Optional API parameter: {param_name}",
                                        label="",
                                        placeholder=f"Enter {param_type} (optional)",
                                        value=auto_fill_value,
                                    ).classes("flex-1 text-xs")

                                    # Store parameter value in form data
                                    def update_optional_param_value(
                                        param=param_name, field=input_field
                                    ):
                                        current_params = form_data.get(
                                            "api_parameters", {}
                                        )
                                        current_params[param] = field.value
                                        form_data["api_parameters"] = current_params

                                    input_field.on_value_change(
                                        lambda e,
                                        p=param_name,
                                        f=input_field: update_optional_param_value(p, f)
                                    )

                                    # Auto-fill if value is available
                                    if auto_fill_value:
                                        update_optional_param_value(
                                            param_name, input_field
                                        )
                else:
                    ui.label("No parameters available for this endpoint").classes(
                        "text-xs muted-text italic"
                    )

            # Display response variables
            with variables_container:
                ui.label("API Response Variables").classes(
                    "text-sm font-medium text-theme-primary mb-2"
                )

                # Get response variables for this endpoint
                response_variables = get_endpoint_response_variables(endpoint_full_name)
                with ui.element("div").classes("variable-help-text"):
                    if response_variables:
                        ui.label(
                            "💡 API response data can be used as variables. Available variables for this endpoint can be selected from the 'API Response' category in the Variables panel on the right."
                        ).classes("text-xs text-theme-primary-light italic")
                        ui.label(
                            "Example: {data.user_id} will be replaced with the user_id from the API response."
                        ).classes("text-xs secondary-text")
                    else:
                        ui.label(
                            "No response variables available for this endpoint"
                        ).classes("text-xs muted-text italic")

    except Exception as e:
        logger.error(f"Error updating API endpoint info: {e}")
        with info_container:
            ui.label(f"Error loading endpoint information: {str(e)}").classes(
                "text-red-400 text-sm"
            )


def get_auto_fill_value(
    auto_fill_key: str, trigger_data: Optional[Dict[str, Any]] = None
) -> str:
    """Get auto-fill value for a parameter based on the current user/session data or trigger data."""
    try:
        from ..twitch import get_twitch_api

        if auto_fill_key == "user_id":
            twitch_api = get_twitch_api()
            if twitch_api and twitch_api.user_id:
                return twitch_api.user_id
        elif auto_fill_key == "username":
            twitch_api = get_twitch_api()
            if (
                twitch_api
                and twitch_api.user
                and hasattr(twitch_api.user, "display_name")
            ):
                return twitch_api.user.display_name
        elif auto_fill_key == "login":
            twitch_api = get_twitch_api()
            if twitch_api and twitch_api.user and hasattr(twitch_api.user, "login"):
                return twitch_api.user.login

        # Handle trigger variables (from the person using the command)
        elif auto_fill_key == "trigger_username":
            if trigger_data and "username" in trigger_data:
                return trigger_data["username"]
            return "[Command user's username]"
        elif auto_fill_key == "trigger_user_id":
            if trigger_data and "user_id" in trigger_data:
                return trigger_data["user_id"]
            return "[Command user's ID]"

        # Add more auto-fill options as needed
        logger.debug(
            f"Auto-fill key '{auto_fill_key}' not recognized or value not available"
        )
        return ""

    except Exception as e:
        logger.error(f"Error getting auto-fill value for '{auto_fill_key}': {e}")
        return ""


def insert_api_variable_into_textarea(variable: str, textarea_element=None):
    """Insert API variable into the response textarea"""
    try:
        if textarea_element is None:
            # Try to find the response input field
            notify(f"Variable: {variable} - Click to copy", type="info", timeout=3)
            return

        # Get current value of the textarea
        current_value = textarea_element.value or ""

        # Insert the variable at cursor position or at the end
        new_value = current_value + variable + " "

        # Update the textarea value
        textarea_element.set_value(new_value)

        # Show a brief notification
        notify(f"Inserted {variable}", type="positive", timeout=1)

    except Exception as e:
        logger.error(f"Error inserting API variable into textarea: {e}")
        notify(f"Error inserting variable: {str(e)}", type="negative")


def create_api_section(form_data: dict, response_input=None) -> ui.element:
    """Create a generic API Call configuration section that can be used by both commands and events"""
    with ui.expansion("API Integration", icon="api").classes(
        "w-full mb-4 hint-info rounded-lg transition-colors"
    ) as api_expansion:
        with api_expansion.add_slot("default"):
            with ui.element("div").classes("form-section p-4 w-full"):
                ui.label("API Call Configuration").classes("form-section-title mb-4")

                with ui.grid(columns=1).classes("gap-4 w-full"):
                    # API enabled toggle
                    api_enabled_switch = ui.switch(
                        text="Enable API Call",
                        value=bool(form_data.get("api_enabled", False)),
                        on_change=lambda e: [
                            form_data.update({"api_enabled": e.value}),
                            toggle_api_section(e.value, api_config_container),
                        ],
                    ).classes("w-full")

                    # API configuration container
                    api_config_container = ui.element("div").classes("w-full space-y-4")

                    # API endpoint selection
                    with ui.element("div").classes("w-full"):
                        ui.label("Available API Endpoints").classes(
                            "text-sm font-medium text-blue-400 mb-2"
                        )

                        # Get available endpoints
                        available_endpoints = get_available_api_endpoints()
                        endpoint_options = {"": "Select an endpoint..."}
                        endpoint_options.update(
                            {
                                endpoint[
                                    "full_name"
                                ]: f"{endpoint['name']} ({endpoint['method']} {endpoint['endpoint']})"
                                for endpoint in available_endpoints
                            }
                        )

                    # Container for API endpoint information
                    api_info_container = ui.element("div").classes("w-full mt-3")

                    # Container for API parameters
                    api_parameters_container = ui.element("div").classes("w-full mt-3")

                    # Container for API response variables
                    api_variables_container = ui.element("div").classes("w-full mt-3")

                    # If we have an existing selected endpoint, populate the containers
                    if form_data.get("api_endpoint_select"):
                        update_api_endpoint_info(
                            form_data["api_endpoint_select"],
                            api_info_container,
                            api_parameters_container,
                            api_variables_container,
                            form_data,
                        )
                        # Update API Response category in variables panel (if callback exists)
                        if "_update_api_response_category" in form_data:
                            form_data["_update_api_response_category"]()

                    # Continue with API endpoint selection
                    with ui.element("div").classes("w-full"):
                        api_endpoint_select = ui.select(
                            options=endpoint_options,
                            label="Select API Endpoint",
                            value=form_data.get("api_endpoint_select", ""),
                            on_change=lambda value: [
                                form_data.update({"api_endpoint_select": value.value}),
                                update_api_endpoint_info(
                                    value.value if value.value else None,
                                    api_info_container,
                                    api_parameters_container,
                                    api_variables_container,
                                    form_data,
                                ),
                                # Update API Response category in variables panel
                                form_data.get(
                                    "_update_api_response_category", lambda: None
                                )(),
                            ],
                        ).classes("w-full")

                        # If we have an existing API endpoint, try to find the matching endpoint
                        if form_data.get("api_endpoint") and not form_data.get(
                            "api_endpoint_select"
                        ):
                            existing_endpoint = form_data.get("api_endpoint", "")
                            existing_method = form_data.get("api_method", "GET")

                            # Try to find matching endpoint in available endpoints
                            for endpoint in available_endpoints:
                                if (
                                    endpoint["endpoint"] == existing_endpoint
                                    and endpoint["method"] == existing_method
                                ):
                                    form_data["api_endpoint_select"] = endpoint[
                                        "full_name"
                                    ]
                                    api_endpoint_select.set_value(endpoint["full_name"])
                                    # Update the info display
                                    update_api_endpoint_info(
                                        endpoint["full_name"],
                                        api_info_container,
                                        api_parameters_container,
                                        api_variables_container,
                                        form_data,
                                    )
                                    # Update API Response category in variables panel (if callback exists)
                                    if "_update_api_response_category" in form_data:
                                        form_data["_update_api_response_category"]()
                                    break

                    with api_config_container:
                        # Auto-populate endpoint and method from selection
                        selected_endpoint = form_data.get("api_endpoint_select", "")
                        if selected_endpoint:
                            # Get the selected endpoint details
                            available_endpoints = get_available_api_endpoints()
                            endpoint_info = None
                            for endpoint in available_endpoints:
                                if endpoint["full_name"] == selected_endpoint:
                                    endpoint_info = endpoint
                                    break

                            if endpoint_info:
                                # Auto-populate the form data with endpoint info
                                form_data["api_endpoint"] = endpoint_info["endpoint"]
                                form_data["api_method"] = endpoint_info["method"]

                # Variable Processing Section (always visible)
                processing_container = create_variable_processing_section(form_data)

                # Function to toggle API config visibility
                def toggle_api_section(enabled, container):
                    if enabled:
                        container.style("display: block")
                    else:
                        container.style("display: none")

                # Initialize visibility
                toggle_api_section(
                    form_data.get("api_enabled", False), api_config_container
                )

    return api_expansion


def create_variable_processing_section(form_data: dict) -> ui.element:
    """Create the variable processing section for API responses"""
    processing_container = ui.column().classes("space-y-2")

    def update_processing():
        expressions = []
        for row in processing_container.elements:
            if hasattr(row, "elements") and len(row.elements) >= 1:
                input_elem = row.elements[0]
                if hasattr(input_elem, "value") and input_elem.value.strip():
                    expressions.append(input_elem.value.strip())
        form_data["api_variable_processing"] = expressions

    # Variable Processing Section (always visible)
    with ui.column().classes("w-full"):
        ui.label("API Variable Processing").classes(
            "text-sm font-medium text-green-400 mb-2"
        )

        def add_processing_expression(expression=""):
            with ui.row().classes("items-center gap-2 w-full"):
                expression_input = form_input(
        tooltip="Processing Expression",
                    label="Processing Expression",
                    placeholder="account_age=date_to_age({data.created_at})",
                    value=expression,
                ).classes("flex-grow text-xs w-full")

                ui.button(
                    icon="delete",
                    on_click=lambda: remove_processing_expression(expression_input),
                ).classes("control-button btn-danger text-xs px-2 py-1 flex-shrink-0")

                expression_input.on_value_change(lambda e: update_processing())

                if expression:
                    update_processing()

        def remove_processing_expression(input_elem):
            # Get current expressions from form data
            current_expressions = form_data.get("api_variable_processing", [])

            # Remove the expression that corresponds to the clicked delete button
            if hasattr(input_elem, "value"):
                expression_to_remove = input_elem.value.strip()
                if expression_to_remove in current_expressions:
                    current_expressions.remove(expression_to_remove)

            # Update form data
            form_data["api_variable_processing"] = current_expressions

            # Clear and rebuild the container
            processing_container.clear()

            # Re-add the remaining expressions
            for expr in current_expressions:
                add_processing_expression(expr)

        # Load existing expressions
        existing_expressions = form_data.get("api_variable_processing", [])
        if isinstance(existing_expressions, list):
            for expr in existing_expressions:
                add_processing_expression(expr)

    return processing_container


def validate_interval_format(value: str) -> bool:
    """Validate hh:mm:ss format for interval input"""
    if not value:
        return True

    parts = value.split(":")
    if len(parts) != 3:
        return False

    try:
        hours, minutes, seconds = map(int, parts)
        return hours >= 0 and 0 <= minutes <= 59 and 0 <= seconds <= 59
    except ValueError:
        return False


def create_chatbot_form(item_id: Optional[str] = None, item_type: Optional[str] = None):
    """Create the chatbot form"""
    # Load existing item data if editing
    existing_item = None
    if item_id:
        try:
            manager = get_chatbot_manager()
            if item_type == "command":
                existing_item = manager.get_command(item_id)
            elif item_type == "event":
                existing_item = manager.get_event(item_id)
        except Exception as e:
            logger.error(f"Error loading chatbot item for edit: {e}")
            notify("Error loading item data", type="negative")
            return

    # Form state - populate with existing data if editing
    form_data = {
        "item_id": item_id,
        "item_type": item_type,
        "name": existing_item.name if existing_item else "",
        "description": existing_item.description if existing_item else "",
        "response_text": existing_item.response_text if existing_item else "",
        "enabled": existing_item.enabled if existing_item else True,
    }

    # Command-specific fields
    if item_type == "command":
        form_data.update(
            {
                "command_name": existing_item.command_name if existing_item else "",
                "aliases": getattr(existing_item, "aliases", [])
                if existing_item
                else [],
                "mod_only": existing_item.mod_only if existing_item else False,
                "cooldown": existing_item.cooldown if existing_item else 0,
                "command_type": existing_item.command_type.value
                if existing_item
                else "basic",
                "usage_limit": existing_item.usage_limit if existing_item else 0,
                "repeating_enabled": existing_item.repeating_enabled
                if existing_item
                else False,
                "repeat_count": existing_item.repeat_count if existing_item else 1,
                "repeat_interval": existing_item.repeat_interval
                if existing_item
                else 0,
                "persistent_counter": existing_item.persistent_counter
                if existing_item
                else False,
                "reset_command": existing_item.reset_command if existing_item else "",
                # API Call fields
                "api_enabled": getattr(existing_item, "api_enabled", False)
                if existing_item
                else False,
                "api_endpoint": getattr(existing_item, "api_endpoint", "")
                if existing_item
                else "",
                "api_method": getattr(existing_item, "api_method", "GET")
                if existing_item
                else "GET",
                "api_headers": getattr(existing_item, "api_headers", {})
                if existing_item
                else {},
                "api_body": getattr(existing_item, "api_body", "")
                if existing_item
                else "",
                "api_response_format": getattr(existing_item, "api_response_format", "")
                if existing_item
                else "",
                "api_parameters": getattr(existing_item, "api_parameters", {})
                if existing_item
                else {},
                "api_endpoint_select": getattr(existing_item, "api_endpoint_select", "")
                if existing_item
                else "",
                "api_variable_processing": getattr(
                    existing_item, "api_variable_processing", []
                )
                if existing_item
                else [],
                "argument_mappings": getattr(existing_item, "argument_mappings", {})
                if existing_item
                else {},
            }
        )
    else:  # Event-specific fields
        form_data.update(
            {
                "event_type": existing_item.event_type.value
                if existing_item and hasattr(existing_item, "event_type")
                else None,
                "interval": existing_item.get_interval_string()
                if existing_item and hasattr(existing_item, "get_interval_string")
                else "",
                # Event-specific settings
                "specific_time": getattr(existing_item, "specific_time", "")
                if existing_item
                else "",
                "chat_message_text": getattr(existing_item, "chat_message_text", "")
                if existing_item
                else "",
                "chat_message_match_type": getattr(
                    existing_item, "chat_message_match_type", "exact"
                )
                if existing_item
                else "exact",
                "bits_quantity": getattr(existing_item, "bits_quantity", 0)
                if existing_item
                else 0,
                "hype_train_level": getattr(existing_item, "hype_train_level", 0)
                if existing_item
                else 0,
                "hype_train_end_level": getattr(
                    existing_item, "hype_train_end_level", 0
                )
                if existing_item
                else 0,
                "gift_sub_quantity": getattr(existing_item, "gift_sub_quantity", 0)
                if existing_item
                else 0,
                "gift_sub_tier": getattr(existing_item, "gift_sub_tier", 0)
                if existing_item
                else 0,
                "resub_months": getattr(existing_item, "resub_months", 0)
                if existing_item
                else 0,
                "resub_tier": getattr(existing_item, "resub_tier", 0)
                if existing_item
                else 0,
                "sub_tier": getattr(existing_item, "sub_tier", 0)
                if existing_item
                else 0,
                "donation_amount": getattr(existing_item, "donation_amount", 0.0)
                if existing_item
                else 0.0,
                "raid_viewer_count": getattr(existing_item, "raid_viewer_count", 0)
                if existing_item
                else 0,
                "raid_raider_name": getattr(existing_item, "raid_raider_name", "")
                if existing_item
                else "",
                "channel_point_reward_name": getattr(
                    existing_item, "channel_point_reward_name", ""
                )
                if existing_item
                else "",
                # API-related fields for events
                "api_enabled": getattr(existing_item, "api_enabled", False)
                if existing_item
                else False,
                "api_endpoint": getattr(existing_item, "api_endpoint", "")
                if existing_item
                else "",
                "api_method": getattr(existing_item, "api_method", "GET")
                if existing_item
                else "GET",
                "api_headers": getattr(existing_item, "api_headers", {})
                if existing_item
                else {},
                "api_body": getattr(existing_item, "api_body", "")
                if existing_item
                else "",
                "api_response_format": getattr(existing_item, "api_response_format", "")
                if existing_item
                else "",
                "api_parameters": getattr(existing_item, "api_parameters", {})
                if existing_item
                else {},
                "api_endpoint_select": getattr(existing_item, "api_endpoint_select", "")
                if existing_item
                else "",
                "api_variable_processing": getattr(
                    existing_item, "api_variable_processing", []
                )
                if existing_item
                else [],
            }
        )

    # Main layout
    with ui.element("div").classes("w-full"):
        # Declare event settings container and function variables (will be defined later for events)
        event_settings_container = None
        update_event_settings = None
        # Reference to variables update function (will be set later)
        variables_update_func = None

        # Define update function early so it's accessible in event_select's on_change
        if item_type == "event":

            def update_event_settings():
                """Update event settings section based on selected event type"""
                if event_settings_container is None:
                    return
                event_settings_container.clear()
                event_type = form_data.get("event_type", "")
                events_with_settings = [
                    "specific_time",
                    "chat_message",
                    "bits",
                    "hype_train_progress",
                    "hype_train_end",
                    "gift_subscription",
                    "resubscription",
                    "subscription",
                    "donation",
                    "raid",
                    "channel_point_redemption",
                    "interval",
                ]

                if event_type in events_with_settings:
                    with event_settings_container:
                        with ui.element("div").classes(
                            "form-section mb-4 border border-gray-600 rounded-lg bg-theme-surface/30 p-4"
                        ):
                            ui.label("Event Settings").classes(
                                "form-section-title mb-4"
                            )

                            with ui.grid(columns=1).classes("gap-4 w-full"):
                                update_event_settings_fields(
                                    event_type, form_data, existing_item
                                )
                else:
                    # Clear container if event type doesn't need settings
                    event_settings_container.clear()

        # Basic Information Section
        with ui.element("div").classes("form-section mb-4"):
            ui.label("Basic Information").classes("form-section-title")

            with ui.grid(columns=2).classes("gap-4 w-full"):
                if item_type == "command":
                    form_input(
        tooltip="Command Name (without !)",
                        label="Command Name (without !)",
                        placeholder="e.g., hello, uptime, lurk",
                        value=form_data.get("command_name", ""),
                        on_change=lambda value: form_data.update(
                            {"command_name": value}
                        ),
                    ).classes("w-full")

                    aliases = form_data.get("aliases", [])
                    aliases_text = (
                        ", ".join(aliases) if isinstance(aliases, list) else ""
                    )
                    form_input(
                        tooltip="Comma-separated command aliases without !",
                        label="Aliases (comma-separated, without !)",
                        placeholder="e.g., hi, hey, sup",
                        value=aliases_text,
                        on_change=lambda value: form_data.update(
                            {
                                "aliases": [
                                    alias.strip()
                                    for alias in str(value).split(",")
                                    if alias.strip()
                                ]
                            }
                        ),
                    ).classes("w-full")
                else:
                    # Event type selection
                    event_options = {
                        "follow": "New Follower",
                        "subscription": "New Subscription",
                        "resubscription": "Resubscription",
                        "gift_subscription": "Gift Subscription",
                        "bits": "Bits Donation",
                        "donation": "Donation",
                        "raid": "Raid",
                        "hype_train_start": "Hype Train Start",
                        "hype_train_end": "Hype Train End",
                        "hype_train_progress": "Hype Train Progress",
                        "channel_point_redemption": "Channel Point Redemption",
                        "interval": "Interval",
                        "specific_time": "Specific Time",
                        "chat_message": "Chat Message",
                    }

                    event_select = ui.select(
                        options=event_options,
                        label="Event Type",
                        value=form_data.get("event_type", ""),
                        on_change=lambda e: [
                            form_data.update({"event_type": e.value}),
                            update_event_settings(),
                            update_custom_variables_display(form_data),
                            update_examples(),
                        ],
                    ).classes("w-full")

                form_input(
        tooltip="Name",
                    label="Name",
                    placeholder="e.g., Welcome Message, Uptime Command",
                    value=form_data.get("name", ""),
                    on_change=lambda value: form_data.update({"name": value}),
                ).classes("w-full")

                form_input(
        tooltip="Description (optional)",
                    label="Description (optional)",
                    placeholder="What does this do?",
                    value=form_data.get("description", ""),
                    on_change=lambda value: form_data.update({"description": value}),
                ).classes("w-full")

                ui.switch(
                    text="Enabled",
                    value=form_data.get("enabled", True),
                    on_change=lambda value: form_data.update({"enabled": value}),
                ).classes("w-full")

        # Event-specific options - Event Settings Section (separate location, after Basic Information)
        if item_type == "event":
            # Create container for event settings that will be updated dynamically
            # This is placed here so it appears after Basic Information in the UI
            event_settings_container = ui.element("div").classes("w-full mb-4")

            # Initialize event settings if event type is already set (for editing existing events)
            initial_event_type = form_data.get("event_type", "")
            if initial_event_type and update_event_settings:
                update_event_settings()

        # Command-specific options
        if item_type == "command":
            # Command Settings Section (Collapsible)
            with ui.expansion("Settings", icon="settings").classes(
                "w-full mb-4 border border-gray-600 rounded-lg bg-theme-surface/30 hover:bg-theme-surface/50 transition-colors"
            ) as command_expansion:
                with command_expansion.add_slot("default"):
                    with ui.element("div").classes("form-section p-4 w-full"):
                        ui.label("Command Settings").classes("form-section-title mb-4")

                        with ui.grid(columns=3).classes("gap-4 w-full"):
                            ui.switch(
                                text="Mod Only",
                                value=form_data.get("mod_only", False),
                                on_change=lambda e: form_data.update(
                                    {"mod_only": e.value}
                                ),
                            ).classes("w-full")

                            form_input(
        tooltip="Cooldown (seconds)",
                                label="Cooldown (seconds)",
                                placeholder="0",
                                value=str(form_data.get("cooldown", 0)),
                                on_change=lambda value: form_data.update(
                                    {
                                        "cooldown": int(value)
                                        if str(value).isdigit()
                                        else 0
                                    }
                                ),
                            ).classes("w-full")

                            form_input(
        tooltip="Usage Limit (0 = unlimited)",
                                label="Usage Limit (0 = unlimited)",
                                placeholder="0",
                                value=str(form_data.get("usage_limit", 0)),
                                on_change=lambda value: form_data.update(
                                    {
                                        "usage_limit": int(value)
                                        if str(value).isdigit()
                                        else 0
                                    }
                                ),
                            ).classes("w-full")

                        # Command Type Section
                        with ui.element("div").classes("mt-4"):
                            ui.label("Command Type").classes(
                                "text-sm font-medium secondary-text mb-2"
                            )

                            command_type_options = {
                                "basic": "Basic Command",
                                "counter": "Counter Command",
                                "reset": "Reset Command",
                            }

                            ui.select(
                                options=command_type_options,
                                label="Command Type",
                                value=form_data.get("command_type", "basic"),
                                on_change=lambda e: [
                                    form_data.update({"command_type": e.value}),
                                    update_command_type_options(
                                        e.value, form_data, command_type_container
                                    ),
                                ],
                            ).classes("w-full")

                            # Dynamic options based on command type
                            command_type_container = ui.element("div").classes(
                                "w-full mt-2"
                            )

                            # Initialize with current command type
                            current_command_type = (
                                form_data.get("command_type", "basic") or "basic"
                            )
                            update_command_type_options(
                                current_command_type, form_data, command_type_container
                            )

                        # Argument Mappings Section
                        with ui.element("div").classes(
                            "mt-6 pt-4 border-t border-gray-600"
                        ):
                            ui.label("Argument Mappings").classes(
                                "text-sm font-medium secondary-text mb-3"
                            )

                            # Container for argument mappings
                            argument_mappings_container = ui.element("div").classes(
                                "space-y-2"
                            )

                            # Function to update argument mappings display
                            def update_argument_mappings():
                                argument_mappings_container.clear()
                                mappings = form_data.get("argument_mappings", {})

                                if not mappings:
                                    with argument_mappings_container:
                                        ui.label(
                                            "No argument mappings defined"
                                        ).classes("text-xs muted-text italic")
                                else:
                                    with argument_mappings_container:
                                        for var_name, position in mappings.items():
                                            with ui.row().classes(
                                                "items-center gap-2 p-2 bg-theme-surface rounded"
                                            ):
                                                ui.label(f"{{{var_name}}}").classes(
                                                    "text-xs font-mono text-blue-300 min-w-20"
                                                )
                                                ui.label("←").classes(
                                                    "text-xs secondary-text"
                                                )
                                                ui.label(
                                                    f"Argument {position}"
                                                ).classes("text-xs text-green-300")
                                                ui.button(
                                                    icon="delete",
                                                    on_click=lambda e, vn=var_name: [
                                                        form_data.get(
                                                            "argument_mappings", {}
                                                        ).pop(vn, None),
                                                        update_argument_mappings(),
                                                    ],
                                                ).props("flat round").classes(
                                                    "text-red-400 hover:text-red-300"
                                                )

                                # Update the arguments variables category
                                if "_update_arguments_category" in form_data:
                                    form_data["_update_arguments_category"]()

                            # Initial display
                            update_argument_mappings()

                            # Add new mapping controls
                            with ui.row().classes("items-end gap-2 mt-3"):
                                new_var_input = form_input(
        tooltip="Variable Name",
                                    label="Variable Name",
                                    placeholder="e.g., city",
                                    value="",
                                ).classes("flex-1")

                                # Create position options (1-10 should be sufficient for most commands)
                                position_options = {
                                    str(i): f"Position {i}" for i in range(1, 11)
                                }

                                new_pos_input = ui.select(
                                    options=position_options,
                                    label="Argument Position",
                                    value="1",
                                ).classes("w-32")

                                ui.button(
                                    "Add Mapping",
                                    icon="add",
                                    on_click=lambda: [
                                        form_data.setdefault("argument_mappings", {}),
                                        new_var_input.value
                                        and new_pos_input.value
                                        and not any(
                                            var_name == new_var_input.value.strip()
                                            for var_name in form_data[
                                                "argument_mappings"
                                            ].keys()
                                        )
                                        and form_data["argument_mappings"].update(
                                            {
                                                new_var_input.value.strip(): int(
                                                    new_pos_input.value
                                                )
                                            }
                                        ),
                                        new_var_input.set_value(""),
                                        new_pos_input.set_value("1"),
                                        update_argument_mappings(),
                                    ],
                                ).classes("btn-success text-xs px-3 py-2")

        # Event-specific options - Event Settings section is now handled dynamically above

        # API Call Configuration Section (Generic) - Separate section before Response
        if item_type in ["command", "event"]:
            create_api_section(form_data, None)

            # Response Section - Two Column Layout
        with ui.element("div").classes("form-section mb-4"):
            ui.label("Response").classes("form-section-title")

            # Create two-column layout using flexbox
            with ui.element("div").classes("flex w-full gap-6"):
                # Left column - Response text area (takes remaining space)
                with ui.element("div").classes("flex-1 min-w-0"):
                    # Response preview
                    with ui.column().classes("w-full"):
                        ui.label("Preview:").classes(
                            "text-sm font-medium secondary-text mb-2"
                        )
                        response_preview_container = ui.element("div").classes(
                            "response-preview"
                        )

                    # Response text area
                    response_input = ui.textarea(
                        label="Response Text",
                        placeholder="Enter your response message...",
                        value=form_data.get("response_text", ""),
                    ).classes("w-full mb-3")

                    # Update preview when response changes
                    def update_preview():
                        # Get value directly from textarea element to avoid object issues
                        try:
                            preview_text = response_input.value or ""
                        except Exception:
                            # Fallback to form_data if textarea access fails
                            preview_text = form_data.get("response_text", "")

                        # Ensure preview_text is a string, not an event object or UI element
                        if not isinstance(preview_text, str):
                            # Try to extract value from UI element or event object
                            if hasattr(preview_text, "value"):
                                preview_text = preview_text.value or ""
                            else:
                                preview_text = ""

                        # Ensure it's still a string after extraction
                        if not isinstance(preview_text, str):
                            preview_text = ""

                        # Resolve variables with live data
                        item_type = form_data.get("item_type", "")
                        event_type = form_data.get("event_type")
                        command_type = form_data.get("command_type", "basic")
                        resolved_text = resolve_variables_for_preview(
                            preview_text, item_type, event_type, command_type, form_data
                        )

                        # Update preview with HTML content
                        try:
                            # Clear and rebuild the HTML element
                            response_preview_container.clear()
                            with response_preview_container:
                                ui.html(resolved_text if resolved_text else "").classes(
                                    "response-preview"
                                )
                        except Exception as e:
                            logger.error(f"Error updating preview: {e}", exc_info=True)

                    # Set up change handler
                    response_input.on_value_change(
                        lambda e: [
                            form_data.update({"response_text": e.value}),
                            update_preview(),
                        ]
                    )

                    # Initial preview
                    update_preview()

                    # Add examples section for the current event type
                    examples_container = ui.element("div").classes("w-full mt-3")
                    with examples_container:
                        ui.label("📝 Examples:").classes(
                            "text-xs font-medium text-theme-primary mb-2"
                        )

                        def update_variables_display():
                            """Update variables display when event type changes"""
                            # Variables are updated dynamically through category display functions
                            # This is a placeholder for compatibility
                            pass

                        def update_examples():
                            examples_container.clear()
                            with examples_container:
                                ui.label("📝 Examples:").classes(
                                    "text-xs font-medium text-theme-primary mb-2"
                                )

                                current_event_type = form_data.get("event_type")
                                # Always get examples - the function handles the logic internally
                                examples = get_variable_examples(
                                    item_type, current_event_type
                                )

                                for example_title, example_text in examples.items():
                                    with ui.element("div").classes(
                                        "mb-2 p-2 bg-theme-base rounded border border-theme-default"
                                    ):
                                        ui.label(example_title).classes(
                                            "text-xs font-medium text-yellow-400 mb-1"
                                        )
                                        ui.label(example_text).classes(
                                            "text-xs secondary-text font-mono"
                                        )

                        update_examples()

                # Right column - Variables panel (fixed width)
                with ui.element("div").classes("w-96 flex-shrink-0"):
                    # Variables panel header
                    with ui.row().classes("items-center justify-between mb-3"):
                        ui.label("Variables").classes(
                            "text-sm font-medium secondary-text"
                        )
                        ui.button(
                            icon="add",
                            text="Custom Variable",
                            on_click=lambda: show_custom_variable_dialog(
                                form_data,
                                lambda: update_custom_variables_display(form_data),
                            ),
                        ).classes("control-button btn-primary text-xs px-2 py-1")

                    # Variables list container with scroll
                    with ui.element("div").classes("max-h-96 overflow-y-auto"):
                        variables_container = ui.column().classes("w-full space-y-2")

                        # Initialize category expansion states
                        if "category_expansion_states" not in form_data:
                            form_data["category_expansion_states"] = {}

                        # Define categories with their data
                        categories_data = [
                            {
                                "name": "Basic",
                                "color_class": "text-blue-400",
                                "prefixes": [
                                    "username",
                                    "timestamp",
                                    "datetime",
                                    "date",
                                    "time",
                                    "source",
                                ],
                                "is_dynamic": False,
                            },
                            {
                                "name": "Arguments",
                                "color_class": "text-cyan-400",
                                "prefixes": [],
                                "is_dynamic": True,
                            },
                            {
                                "name": "Command/Event",
                                "color_class": "text-green-400",
                                "prefixes": [],
                                "is_dynamic": False,
                            },
                            {
                                "name": "Statistics",
                                "color_class": "text-orange-400",
                                "prefixes": ["stats."],
                                "is_dynamic": False,
                            },
                            {
                                "name": "YouTube",
                                "color_class": "text-red-400",
                                "prefixes": ["youtube."],
                                "is_dynamic": True,
                            },
                            {
                                "name": "Quotes",
                                "color_class": "text-yellow-400",
                                "prefixes": ["quote."],
                                "is_dynamic": False,
                            },
                            {
                                "name": "API Response",
                                "color_class": "text-theme-primary",
                                "prefixes": ["api_"],
                                "is_dynamic": False,
                            },
                            {
                                "name": "Custom",
                                "color_class": "text-indigo-400",
                                "prefixes": ["custom_"],
                                "is_dynamic": False,
                            },
                        ]

                        # Create category containers and headers
                        category_containers = {}
                        category_headers = {}
                        category_icons = {}

                        def update_api_response_category():
                            """Update the API Response category specifically"""
                            selected_endpoint = form_data.get("api_endpoint_select", "")
                            if not selected_endpoint:
                                return

                            if "API Response" in category_containers:
                                # Find the API Response category data
                                api_response_data = None
                                for cat_data in categories_data:
                                    if cat_data["name"] == "API Response":
                                        api_response_data = cat_data
                                        break

                                if api_response_data:
                                    # Always expand the category so variables are visible
                                    form_data["category_expansion_states"][
                                        "API Response"
                                    ] = True

                                    # Update the header to show expanded state
                                    if "API Response" in category_headers:
                                        header_row = category_headers["API Response"]
                                        header_row.clear()
                                        color_class = api_response_data["color_class"]
                                        with header_row:
                                            icon_element = ui.icon(
                                                "expand_less", size="sm"
                                            ).classes(f"text-{color_class}")
                                            category_icons["API Response"] = (
                                                icon_element
                                            )
                                            ui.label("API Response Variables").classes(
                                                f"text-xs font-medium {color_class}"
                                            )

                                    # Update the category content
                                    update_category_display(
                                        "API Response", api_response_data
                                    )

                        def update_arguments_category():
                            """Update the Arguments category specifically"""
                            if "Arguments" in category_containers:
                                # Find the Arguments category data
                                arguments_data = None
                                for cat_data in categories_data:
                                    if cat_data["name"] == "Arguments":
                                        arguments_data = cat_data
                                        break

                                if arguments_data:
                                    # Always expand the category so variables are visible when mappings exist
                                    argument_mappings = form_data.get(
                                        "argument_mappings", {}
                                    )
                                    if argument_mappings:
                                        form_data["category_expansion_states"][
                                            "Arguments"
                                        ] = True

                                        # Update the header to show expanded state
                                        if "Arguments" in category_headers:
                                            header_row = category_headers["Arguments"]
                                            header_row.clear()
                                            color_class = arguments_data["color_class"]
                                            with header_row:
                                                icon_element = ui.icon(
                                                    "expand_less", size="sm"
                                                ).classes(f"text-{color_class}")
                                                category_icons["Arguments"] = (
                                                    icon_element
                                                )
                                                ui.label("Arguments").classes(
                                                    f"text-xs font-medium {color_class}"
                                                )

                                    # Update the category content
                                    update_category_display("Arguments", arguments_data)

                        # Store reference to update functions in form_data
                        form_data["_update_api_response_category"] = (
                            update_api_response_category
                        )
                        form_data["_update_arguments_category"] = (
                            update_arguments_category
                        )

                        def update_category_display(category_name, category_data):
                            """Update only a specific category's content"""
                            container = category_containers[category_name]

                            # Get current context data
                            current_event_type = form_data.get("event_type")
                            current_command_type = form_data.get(
                                "command_type", "basic"
                            )
                            api_enabled = form_data.get("api_enabled", False)
                            selected_api_endpoint = form_data.get(
                                "api_endpoint_select", ""
                            )

                            available_variables = get_available_variables(
                                item_type,
                                current_event_type,
                                current_command_type,
                                api_enabled,
                            )

                            # Get dynamic API variables if endpoint is selected
                            api_response_vars = {}
                            if selected_api_endpoint:
                                api_response_vars = get_api_response_variables(
                                    selected_api_endpoint
                                )

                            # Get custom variables from persistent storage
                            custom_vars = load_custom_variables()
                            form_data["custom_variables"] = custom_vars

                            # Get variables for this category
                            category_vars = get_category_variables(
                                category_name,
                                category_data,
                                available_variables,
                                api_response_vars,
                                custom_vars,
                            )

                            # Clear and rebuild only this category's content
                            container.clear()

                            if category_vars and form_data[
                                "category_expansion_states"
                            ].get(category_name, False):
                                with container:
                                    with ui.column().classes("ml-4 space-y-1"):
                                        for var_name, var_desc in category_vars.items():
                                            # Determine category for styling based on actual category
                                            if category_name == "Basic":
                                                chip_category = "basic"
                                            elif category_name == "Command/Event":
                                                chip_category = "command-event"
                                            elif category_name == "Statistics":
                                                chip_category = "stats"
                                            elif category_name == "YouTube":
                                                chip_category = "youtube"
                                            elif category_name == "Quotes":
                                                chip_category = "quotes"
                                            elif category_name == "API Response":
                                                chip_category = "api"
                                            elif category_name == "Custom":
                                                chip_category = "custom"
                                            else:
                                                chip_category = "basic"

                                            if category_name == "Custom":
                                                # Custom variables get the variable label and vertically stacked edit/delete buttons
                                                with ui.row().classes(
                                                    "items-center gap-2"
                                                ):
                                                    # Variable label (clickable)
                                                    ui.label(var_desc).classes(
                                                        f"variable-chip flex-1"
                                                    ).props(
                                                        f'data-category="{chip_category}"'
                                                    ).on(
                                                        "click",
                                                        lambda var=f"{{{var_name}}}",
                                                        desc=var_desc: insert_variable_into_textarea(
                                                            var,
                                                            desc,
                                                            response_input,
                                                        ),
                                                    )
                                                    # Vertically stacked edit and delete buttons
                                                    with ui.column().classes("gap-1"):
                                                        ui.button(
                                                            icon="edit",
                                                            text="",
                                                            on_click=lambda name=var_name: edit_custom_variable(
                                                                name,
                                                                form_data,
                                                                lambda: update_category_display(
                                                                    category_name,
                                                                    category_data,
                                                                ),
                                                            ),
                                                        ).classes(
                                                            "control-button btn-secondary text-xs p-1"
                                                        ).props("dense small")
                                                        ui.button(
                                                            icon="delete",
                                                            text="",
                                                            on_click=lambda name=var_name: delete_custom_variable(
                                                                name,
                                                                lambda: update_category_display(
                                                                    category_name,
                                                                    category_data,
                                                                ),
                                                                form_data,
                                                            ),
                                                        ).classes(
                                                            "control-button btn-danger text-xs p-1"
                                                        ).props("dense small")
                                            else:
                                                # Regular variables just get the chip
                                                ui.label(var_desc).classes(
                                                    f"variable-chip"
                                                ).props(
                                                    f'data-category="{chip_category}"'
                                                ).on(
                                                    "click",
                                                    lambda var=f"{{{var_name}}}",
                                                    desc=var_desc: insert_variable_into_textarea(
                                                        var, desc, response_input
                                                    ),
                                                )

                        def get_category_variables(
                            category_name,
                            category_data,
                            available_variables,
                            api_response_vars,
                            custom_vars,
                        ):
                            """Get variables for a specific category - mutually exclusive filtering"""
                            prefixes = category_data["prefixes"]
                            is_dynamic = category_data["is_dynamic"]

                            # Define exclusion sets for mutually exclusive filtering
                            basic_var_names = {
                                "username",
                                "timestamp",
                                "datetime",
                                "date",
                                "time",
                                "source",
                            }

                            if category_name == "Basic":
                                # Only exact matches for basic variables
                                result = {
                                    k: v
                                    for k, v in available_variables.items()
                                    if k in basic_var_names
                                }
                            elif category_name == "Arguments":
                                # Argument mappings from form data
                                argument_mappings = form_data.get(
                                    "argument_mappings", {}
                                )
                                result = {
                                    var_name: f"Argument {position} from command"
                                    for var_name, position in argument_mappings.items()
                                }
                            elif category_name == "Command/Event":
                                # Command/Event specific variables (exclude all other categories)
                                result = {
                                    k: v
                                    for k, v in available_variables.items()
                                    if (
                                        k not in basic_var_names
                                        and not k.startswith("stats.")
                                        and not k.startswith("youtube.")
                                        and not k.startswith("quote.")
                                        and not k.startswith("custom_")
                                        and not k.startswith("api_")
                                        and "_latest_video_" not in k
                                        and "_channel_" not in k
                                        and "_last_updated" not in k
                                    )
                                }
                            elif category_name == "Statistics":
                                # Only variables starting with stats.
                                result = {
                                    k: v
                                    for k, v in available_variables.items()
                                    if k.startswith("stats.")
                                }
                            elif category_name == "YouTube":
                                # YouTube variables: youtube.* prefix OR channel-specific patterns
                                result = {
                                    k: v
                                    for k, v in available_variables.items()
                                    if (
                                        k.startswith("youtube.")
                                        or "_latest_video_" in k
                                        or "_channel_" in k
                                        or "_last_updated" in k
                                    )
                                }
                            elif category_name == "API Response":
                                result = api_response_vars.copy()
                            elif category_name == "Quotes":
                                # Only variables starting with quote.
                                result = {
                                    k: v
                                    for k, v in available_variables.items()
                                    if k.startswith("quote.")
                                }
                            elif category_name == "Custom":
                                result = custom_vars.copy()
                            else:
                                result = {}

                            # Sort variables alphabetically by key
                            return dict(sorted(result.items()))

                        def toggle_category(category_name, category_data):
                            """Toggle a category's expansion state and update only that category"""
                            form_data["category_expansion_states"][
                                category_name
                            ] = not form_data["category_expansion_states"].get(
                                category_name, False
                            )

                            # Recreate the header row with updated icon
                            header_row = category_headers[category_name]
                            header_row.clear()

                            color_class = category_data["color_class"]
                            with header_row:
                                icon_name = (
                                    "expand_less"
                                    if form_data["category_expansion_states"][
                                        category_name
                                    ]
                                    else "expand_more"
                                )
                                icon_element = ui.icon(icon_name, size="sm").classes(
                                    f"text-{color_class}"
                                )
                                category_icons[category_name] = icon_element
                                ui.label(f"{category_name} Variables").classes(
                                    f"text-xs font-medium {color_class}"
                                )

                            # Update the category content
                            update_category_display(category_name, category_data)

                        # Initialize categories
                        with variables_container:
                            for category_data in categories_data:
                                category_name = category_data["name"]
                                color_class = category_data["color_class"]

                                # Initialize expansion state if not set
                                if (
                                    category_name
                                    not in form_data["category_expansion_states"]
                                ):
                                    form_data["category_expansion_states"][
                                        category_name
                                    ] = False

                                # Get variables for this category to check if it should be displayed
                                current_event_type = form_data.get("event_type")
                                current_command_type = form_data.get(
                                    "command_type", "basic"
                                )
                                api_enabled = form_data.get("api_enabled", False)
                                selected_api_endpoint = form_data.get(
                                    "api_endpoint_select", ""
                                )

                                available_variables = get_available_variables(
                                    item_type,
                                    current_event_type,
                                    current_command_type,
                                    api_enabled,
                                )

                                api_response_vars = {}
                                if selected_api_endpoint:
                                    api_response_vars = get_api_response_variables(
                                        selected_api_endpoint
                                    )

                                custom_vars = load_custom_variables()
                                form_data["custom_variables"] = custom_vars

                                category_vars = get_category_variables(
                                    category_name,
                                    category_data,
                                    available_variables,
                                    api_response_vars,
                                    custom_vars,
                                )

                                # Always create API Response and Arguments category containers, even if empty
                                # This allows them to be populated when endpoints are selected or mappings are added
                                if category_vars or category_name in [
                                    "API Response",
                                    "Arguments",
                                ]:
                                    # Category header with toggle
                                    header_row = ui.row().classes(
                                        "items-center cursor-pointer hover-theme-surface rounded px-2 py-1"
                                    )
                                    category_headers[category_name] = header_row

                                    with header_row:
                                        icon_name = (
                                            "expand_less"
                                            if form_data["category_expansion_states"][
                                                category_name
                                            ]
                                            else "expand_more"
                                        )
                                        icon_element = ui.icon(
                                            icon_name, size="sm"
                                        ).classes(f"text-{color_class}")
                                        category_icons[category_name] = icon_element
                                        ui.label(f"{category_name} Variables").classes(
                                            f"text-xs font-medium {color_class}"
                                        )

                                    # Set click handler for the entire row
                                    header_row.on(
                                        "click",
                                        lambda cat_name=category_name,
                                        cat_data=category_data: toggle_category(
                                            cat_name, cat_data
                                        ),
                                    )

                                    # Container for this category's variables
                                    category_container = ui.element("div").classes(
                                        "category-variables-container"
                                    )
                                    category_containers[category_name] = (
                                        category_container
                                    )

                                    # Initialize with current state
                                    update_category_display(
                                        category_name, category_data
                                    )

                            # After all categories are initialized, update API Response category if endpoint is selected
                            selected_endpoint = form_data.get("api_endpoint_select", "")
                            if selected_endpoint:
                                api_response_data = next(
                                    (
                                        cat
                                        for cat in categories_data
                                        if cat["name"] == "API Response"
                                    ),
                                    None,
                                )
                                if (
                                    api_response_data
                                    and "API Response" in category_containers
                                ):
                                    update_category_display(
                                        "API Response", api_response_data
                                    )

                            # If callback exists and there's a selected endpoint, trigger update
                            # This handles the case where endpoint was selected before variables panel was created
                            if (
                                "_update_api_response_category" in form_data
                                and selected_endpoint
                            ):
                                form_data["_update_api_response_category"]()

        # Form buttons
        with ui.row().classes(
            "w-full items-center justify-end gap-2 mt-6 pt-4 border-t border-gray-700"
        ):
            ui.button(
                text="Cancel",
                on_click=lambda: create_dialog.close() if create_dialog else None,
            ).props("flat").classes("secondary-text")

            is_edit = item_id is not None
            button_text = (
                f"Update {item_type.title()}"
                if is_edit and item_type
                else f"Create {item_type.title()}"
                if item_type
                else "Create Item"
            )
            ui.button(
                icon="save",
                text=button_text,
                on_click=lambda: save_chatbot_item(form_data),
            ).classes("control-button btn-secondary px-6 py-2")


def update_event_settings_fields(event_type: str, form_data: dict, existing_item=None):
    """Update event settings fields based on event type"""
    # SPECIFIC_TIME: Time input (HH:MM format) - required
    if event_type == "specific_time":
        time_input = form_input(
        tooltip="Time (HH:MM) *",
            label="Time (HH:MM) *",
            placeholder="e.g., 14:30 for 2:30 PM",
            value=form_data.get(
                "specific_time", existing_item.specific_time if existing_item else ""
            ),
            validation={
                "HH:MM format required": lambda value: (
                    bool(value)
                    and ":" in value
                    and len(value.split(":")) == 2
                    and all(part.isdigit() for part in value.split(":"))
                    and 0 <= int(value.split(":")[0]) < 24
                    and 0 <= int(value.split(":")[1]) < 60
                ),
            },
            on_change=lambda value: form_data.update({"specific_time": value}),
        ).classes("w-full")
        time_input.props("hint='Required: Daily recurring time in 24-hour format'")

    # CHAT_MESSAGE: Message text + match type dropdown - required
    elif event_type == "chat_message":
        message_input = form_input(
        tooltip="Message Text *",
            label="Message Text *",
            placeholder="Enter the message text to match",
            value=form_data.get(
                "chat_message_text",
                existing_item.chat_message_text if existing_item else "",
            ),
            validation={"Message text is required": lambda value: bool(value)},
            on_change=lambda value: form_data.update({"chat_message_text": value}),
        ).classes("w-full")

        match_type_options = {
            "exact": "Exact Match",
            "starts_with": "Starts With",
            "contains": "Contains",
        }
        match_type_select = ui.select(
            match_type_options,
            label="Match Type *",
            value=form_data.get(
                "chat_message_match_type",
                existing_item.chat_message_match_type if existing_item else "exact",
            ),
            on_change=lambda e: form_data.update({"chat_message_match_type": e.value}),
        ).classes("w-full")

    # BITS: Quantity input (0 or empty = any)
    elif event_type == "bits":
        bits_input = ui.number(
            label="Bits Quantity",
            placeholder="0 or empty for any amount",
            value=form_data.get(
                "bits_quantity", existing_item.bits_quantity if existing_item else 0
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"bits_quantity": int(value) if value else 0}
            ),
        ).classes("w-full")
        bits_input.props("hint='Leave 0 or empty to trigger for any amount'")

    # HYPE_TRAIN_PROGRESS: Level input (0 or empty = any)
    elif event_type == "hype_train_progress":
        level_input = ui.number(
            label="Hype Train Level",
            placeholder="0 or empty for any level",
            value=form_data.get(
                "hype_train_level",
                existing_item.hype_train_level if existing_item else 0,
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"hype_train_level": int(value) if value else 0}
            ),
        ).classes("w-full")
        level_input.props("hint='Leave 0 or empty to trigger for any level'")

    # HYPE_TRAIN_END: Level input (0 or empty = any)
    elif event_type == "hype_train_end":
        level_input = ui.number(
            label="Hype Train End Level",
            placeholder="0 or empty for any level",
            value=form_data.get(
                "hype_train_end_level",
                existing_item.hype_train_end_level if existing_item else 0,
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"hype_train_end_level": int(value) if value else 0}
            ),
        ).classes("w-full")
        level_input.props("hint='Leave 0 or empty to trigger for any level'")

    # GIFT_SUBSCRIPTION: Quantity + Tier (0 or empty = any)
    elif event_type == "gift_subscription":
        gift_quantity_input = ui.number(
            label="Gift Sub Quantity",
            placeholder="0 or empty for any quantity",
            value=form_data.get(
                "gift_sub_quantity",
                existing_item.gift_sub_quantity if existing_item else 0,
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"gift_sub_quantity": int(value) if value else 0}
            ),
        ).classes("w-full")

        tier_options = {"0": "Any", "1": "Tier 1", "2": "Tier 2", "3": "Tier 3"}
        tier_value = str(
            form_data.get(
                "gift_sub_tier", existing_item.gift_sub_tier if existing_item else 0
            )
        )
        tier_select = ui.select(
            tier_options,
            label="Gift Sub Tier",
            value=tier_value,
            on_change=lambda e: form_data.update(
                {"gift_sub_tier": int(e.value) if e.value else 0}
            ),
        ).classes("w-full")

    # RESUBSCRIPTION: Months + Tier (0 or empty = any)
    elif event_type == "resubscription":
        months_input = ui.number(
            label="Resub Months",
            placeholder="0 or empty for any months",
            value=form_data.get(
                "resub_months", existing_item.resub_months if existing_item else 0
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"resub_months": int(value) if value else 0}
            ),
        ).classes("w-full")

        tier_options = {"0": "Any", "1": "Tier 1", "2": "Tier 2", "3": "Tier 3"}
        tier_value = str(
            form_data.get(
                "resub_tier", existing_item.resub_tier if existing_item else 0
            )
        )
        tier_select = ui.select(
            tier_options,
            label="Resub Tier",
            value=tier_value,
            on_change=lambda e: form_data.update(
                {"resub_tier": int(e.value) if e.value else 0}
            ),
        ).classes("w-full")

    # SUBSCRIPTION: Tier dropdown (0 or empty = any)
    elif event_type == "subscription":
        tier_options = {"0": "Any", "1": "Tier 1", "2": "Tier 2", "3": "Tier 3"}
        tier_value = str(
            form_data.get("sub_tier", existing_item.sub_tier if existing_item else 0)
        )
        tier_select = ui.select(
            tier_options,
            label="Sub Tier",
            value=tier_value,
            on_change=lambda e: form_data.update(
                {"sub_tier": int(e.value) if e.value else 0}
            ),
        ).classes("w-full")

    # DONATION: Amount input (0 or empty = any)
    elif event_type == "donation":
        donation_input = ui.number(
            label="Donation Amount",
            placeholder="0 or empty for any amount",
            value=form_data.get(
                "donation_amount",
                existing_item.donation_amount if existing_item else 0.0,
            ),
            min=0.0,
            step=0.01,
            on_change=lambda value: form_data.update(
                {"donation_amount": float(value) if value else 0.0}
            ),
        ).classes("w-full")
        donation_input.props("hint='Leave 0 or empty to trigger for any amount'")

    # RAID: Viewer count + Raider name (0/empty = any)
    elif event_type == "raid":
        viewer_count_input = ui.number(
            label="Raider Viewer Count",
            placeholder="0 or empty for any quantity",
            value=form_data.get(
                "raid_viewer_count",
                existing_item.raid_viewer_count if existing_item else 0,
            ),
            min=0,
            on_change=lambda value: form_data.update(
                {"raid_viewer_count": int(value) if value else 0}
            ),
        ).classes("w-full")

        raider_name_input = form_input(
        tooltip="Raider Name",
            label="Raider Name",
            placeholder="Leave empty for any raider",
            value=form_data.get(
                "raid_raider_name",
                existing_item.raid_raider_name if existing_item else "",
            ),
            on_change=lambda value: form_data.update({"raid_raider_name": value}),
        ).classes("w-full")

    # CHANNEL_POINT_REDEMPTION: Reward name input (empty = any)
    elif event_type == "channel_point_redemption":
        reward_name_input = form_input(
        tooltip="Point Reward Name",
            label="Point Reward Name",
            placeholder="Leave empty for any reward",
            value=form_data.get(
                "channel_point_reward_name",
                existing_item.channel_point_reward_name if existing_item else "",
            ),
            on_change=lambda value: form_data.update(
                {"channel_point_reward_name": value}
            ),
        ).classes("w-full")
        reward_name_input.props("hint='Leave empty to trigger for any point reward'")

    # INTERVAL: Interval input (hh:mm:ss) - required
    elif event_type == "interval":
        interval_input = form_input(
        tooltip="Interval (hh:mm:ss) *",
            label="Interval (hh:mm:ss) *",
            placeholder="e.g., 01:30:00 for 1.5 hours (required)",
            value=form_data.get(
                "interval", existing_item.get_interval_string() if existing_item else ""
            ),
            validation={
                "hh:mm:ss format required": lambda value: (
                    validate_interval_format(value) if value else False
                ),
                "Interval is required for Interval events": lambda value: bool(value),
            },
            on_change=lambda value: form_data.update({"interval": value}),
        ).classes("w-full")
        interval_input.props("hint='Required: Time between automatic triggers'")
        with ui.element("div").classes("variable-help-text"):
            ui.label(
                "💡 Interval events trigger automatically on a schedule. The interval is required and determines how often the event fires."
            ).classes("text-xs")


def update_command_type_options(command_type: str, form_data: dict, container=None):
    """Update command type specific options"""
    print(f"DEBUG: update_command_type_options called with type: {command_type}")
    if container is None:
        print(f"DEBUG: Container is None, returning")
        return

    container.clear()
    print(f"DEBUG: Container cleared, processing command type: {command_type}")

    with container:
        if command_type == "counter":
            with ui.grid(columns=2).classes("gap-4 w-full"):
                ui.switch(
                    text="Persistent Counter",
                    value=form_data.get("persistent_counter", False),
                    on_change=lambda e: form_data.update(
                        {"persistent_counter": e.value}
                    ),
                ).classes("w-full")

                ui.label("Counter will persist between app restarts").classes(
                    "text-xs muted-text"
                )

        elif command_type == "reset":
            print(
                f"DEBUG: Processing reset command type - exact value: '{command_type}'"
            )
            # Get available counter commands
            counter_commands = get_counter_commands()
            print(f"DEBUG: Counter commands available: {bool(counter_commands)}")
            print(f"DEBUG: Counter commands dict: {counter_commands}")

            if counter_commands:
                print(
                    f"DEBUG: Creating select dropdown with {len(counter_commands)} options"
                )
                ui.select(
                    options=counter_commands,
                    label="Command to Reset",
                    value=form_data.get("reset_command", ""),
                    on_change=lambda value: form_data.update({"reset_command": value}),
                ).classes("w-full")

                ui.label(
                    "Select the counter command that this reset command will target"
                ).classes("text-xs muted-text")
            else:
                print(f"DEBUG: Creating input field (no counter commands found)")
                form_input(
        tooltip="Command to Reset",
                    label="Command to Reset",
                    placeholder="e.g., deathcount, tipcounter",
                    value=form_data.get("reset_command", ""),
                    on_change=lambda value: form_data.update({"reset_command": value}),
                ).classes("w-full")

                ui.label(
                    "No counter commands found. Enter the command name (without !) that this reset command will target"
                ).classes("text-xs muted-text")


def get_counter_commands() -> Dict[str, str]:
    """Get available counter commands for reset functionality"""
    try:
        manager = get_chatbot_manager()
        commands = manager.get_all_commands()

        print(f"DEBUG: Found {len(commands)} total commands")

        counter_commands = {}
        for command_id, command in commands.items():
            cmd_type = getattr(command, "command_type", "unknown")
            cmd_name = getattr(command, "command_name", "unknown")
            print(f"DEBUG: Command {command_id}: type={cmd_type}, name={cmd_name}")

            # Check if it's a counter command
            if hasattr(command, "command_type"):
                if command.command_type == CommandType.COUNTER:
                    counter_commands[command.command_name] = (
                        f"!{command.command_name} (Count: {getattr(command, 'counter_value', 0)})"
                    )
                    print(f"DEBUG: Added counter command: {command.command_name}")
                elif str(command.command_type) == "counter":  # Fallback check
                    counter_commands[command.command_name] = (
                        f"!{command.command_name} (Count: {getattr(command, 'counter_value', 0)})"
                    )
                    print(
                        f"DEBUG: Added counter command (fallback): {command.command_name}"
                    )

        print(
            f"DEBUG: Found {len(counter_commands)} counter commands: {list(counter_commands.keys())}"
        )
        return counter_commands
    except Exception as e:
        logger.error(f"Error getting counter commands: {e}", exc_info=True)
        print(f"DEBUG: Exception in get_counter_commands: {e}")
        return {}


def get_api_response_variables(endpoint_full_name: str) -> Dict[str, str]:
    """Get API response variables for a specific endpoint"""
    if not endpoint_full_name:
        return {}

    try:
        # Get response variables for this endpoint
        response_variables = get_endpoint_response_variables(endpoint_full_name)
        api_vars = {}

        for var in response_variables:
            # Remove braces and create variable description
            var_name = var.strip("{}")
            api_vars[var_name] = f"{var} - API response variable"

        return api_vars
    except Exception as e:
        logger.error(f"Error getting API response variables: {e}")
        return {}


def update_custom_variables_display(form_data: dict):
    """Update the variables display in the custom variable dialog"""
    # This function will refresh all category displays
    # Since we don't have direct access to the containers here,
    # we'll rely on the category update functions stored in form_data
    # No category-specific update functions needed for custom variables dialog


def show_custom_variable_dialog(
    form_data: dict, update_callback, edit_mode: bool = False, edit_var_name: str = ""
):
    """Show dialog for creating/editing custom variables"""
    global custom_variable_dialog

    if custom_variable_dialog:
        custom_variable_dialog.close()
        custom_variable_dialog = None

    # Create the dialog
    custom_variable_dialog = ui.dialog().props("persistent")

    with (
        custom_variable_dialog,
        ui.card().style(
            "width: 1400px; max-width: none; max-height: 90vh; overflow-y: auto"
        ),
    ):
        with ui.card().classes("w-full"):
            with ui.column().classes("w-full h-auto"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    dialog_title = (
                        "Edit Custom Variable"
                        if edit_mode
                        else "Create Custom Variable"
                    )
                    ui.label(dialog_title).classes(
                        "text-lg font-semibold text-indigo-400"
                    )
                    with ui.row().classes("items-center gap-2"):
                        ui.button(
                            icon="help_outline",
                            on_click=show_custom_variable_help_dialog,
                        ).props("flat round").classes(
                            "text-blue-400 hover:text-blue-300"
                        ).tooltip("Help - View all available modifiers and functions")
                        ui.button(
                            icon="close", on_click=custom_variable_dialog.close
                        ).props("flat round").classes("secondary-text")

                # Dialog content - Grid layout with fixed heights
                with ui.grid(columns="1fr 320px 400px").classes("gap-4 p-4 h-[500px]"):
                    # Left column - Variable creation form
                    with ui.column().classes("space-y-4 h-full overflow-hidden"):
                        # Load existing values if in edit mode
                        initial_name = ""
                        initial_expression = ""

                        if edit_mode and edit_var_name:
                            custom_vars = load_custom_variables()
                            if edit_var_name in custom_vars:
                                var_desc = custom_vars[edit_var_name]
                                # Extract expression from description (format: "{custom_var_name} - Custom: expression")
                                if " - Custom: " in var_desc:
                                    initial_expression = var_desc.split(" - Custom: ")[
                                        1
                                    ]
                                    initial_name = edit_var_name.replace("custom_", "")

                        # Variable name input
                        var_name_input = form_input(
        tooltip="Variable Name",
                            label="Variable Name",
                            placeholder="e.g., user_level, account_age_days",
                            value=initial_name,
                        ).classes("w-full")

                        # Expression input - takes remaining space
                        expression_input = ui.textarea(
                            label="Expression",
                            placeholder="e.g., {time:EST:12:show}, math({stats.alerts.bit_alerts_played} / 10), date_to_age({data.created_at})",
                            value=initial_expression,
                        ).classes("w-full flex-1 min-h-[120px]")

                        # Help text in a fixed height scrollable container
                        with ui.element("div").classes(
                            "h-40 overflow-y-auto bg-theme-surface/50 p-3 rounded border border-gray-600"
                        ):
                            ui.label("💡 Expression Examples:").classes(
                                "text-xs font-medium text-indigo-400 mb-2 block"
                            )
                            ui.label(
                                "• math({stats.alerts.bit_alerts_played} / 10) → Calculate level"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• date_to_age({data.created_at}).years → Account age in years"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• compare({stats.chatbot.commands_triggered}, >, 100) → Check if high usage"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• {stats.alerts.bit_alerts_played} + {stats.alerts.resubs_played} → Combine values"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• {time:EST:12:show} → 02:30 PM EST (12hr with AM/PM)"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• {time:UTC:24} → 19:30 UTC (24hr format)"
                            ).classes("text-xs secondary-text block mb-1")
                            ui.label(
                                "• {time:PST:12:hide} → 11:30 PST (12hr without AM/PM)"
                            ).classes("text-xs secondary-text block mb-1")

                        # Form buttons - fixed at bottom with no flex issues
                        with ui.row().classes(
                            "w-full items-center justify-end gap-2 pt-3 border-t border-gray-600"
                        ):
                            ui.button(
                                text="Cancel", on_click=custom_variable_dialog.close
                            ).props("flat").classes("secondary-text")

                            button_text = (
                                "Update Variable" if edit_mode else "Create Variable"
                            )
                            button_icon = "save" if not edit_mode else "edit"

                            ui.button(
                                icon=button_icon,
                                text=button_text,
                                on_click=lambda: create_custom_variable(
                                    var_name_input.value,
                                    expression_input.value,
                                    form_data,
                                    update_callback,
                                    edit_mode=edit_mode,
                                    edit_var_name=edit_var_name,
                                ),
                            ).classes("control-button btn-primary px-4 py-2")

                    # Right column - Variables panel (matching command dialog style)
                    with ui.element("div").classes("w-96 flex-shrink-0"):
                        # Variables panel header
                        with ui.row().classes("items-center justify-between mb-3"):
                            ui.label("Variables").classes(
                                "text-sm font-medium secondary-text"
                            )

                        # Variables list container with scroll (matching command dialog)
                        with ui.element("div").classes("max-h-96 overflow-y-auto"):
                            variables_container = ui.column().classes(
                                "w-full space-y-2"
                            )

                            # Initialize category expansion states
                            if "category_expansion_states" not in form_data:
                                form_data["category_expansion_states"] = {}

                            # Define categories with their data (matching command dialog)
                            categories_data = [
                                {
                                    "name": "Basic",
                                    "color_class": "text-blue-400",
                                    "prefixes": [
                                        "username",
                                        "timestamp",
                                        "datetime",
                                        "date",
                                        "time",
                                        "source",
                                    ],
                                    "is_dynamic": False,
                                },
                                {
                                    "name": "Command/Event",
                                    "color_class": "text-green-400",
                                    "prefixes": [],
                                    "is_dynamic": False,
                                },
                                {
                                    "name": "Statistics",
                                    "color_class": "text-orange-400",
                                    "prefixes": ["stats."],
                                    "is_dynamic": False,
                                },
                                {
                                    "name": "YouTube",
                                    "color_class": "text-red-400",
                                    "prefixes": ["youtube."],
                                    "is_dynamic": True,
                                },
                                {
                                    "name": "Quotes",
                                    "color_class": "text-yellow-400",
                                    "prefixes": ["quote."],
                                    "is_dynamic": False,
                                },
                                {
                                    "name": "Custom",
                                    "color_class": "text-indigo-400",
                                    "prefixes": ["custom_"],
                                    "is_dynamic": False,
                                },
                            ]

                            # Create category containers and headers
                            category_containers = {}
                            category_headers = {}
                            category_icons = {}

                            def update_category_display(category_name, category_data):
                                """Update only a specific category's content"""
                                container = category_containers[category_name]

                                # Get current context data - for custom variables, show all possible variables
                                # We'll use "command" as default since it has the most variables
                                item_type = form_data.get(
                                    "item_type", "command"
                                )  # Default to command for full variable list
                                current_event_type = form_data.get("event_type")
                                current_command_type = form_data.get(
                                    "command_type", "basic"
                                )
                                api_enabled = form_data.get("api_enabled", False)
                                selected_api_endpoint = form_data.get(
                                    "api_endpoint_select", ""
                                )

                                available_variables = get_available_variables(
                                    item_type,
                                    current_event_type,
                                    current_command_type,
                                    api_enabled,
                                )

                                # Get dynamic API variables if endpoint is selected
                                api_response_vars = {}
                                if selected_api_endpoint:
                                    api_response_vars = get_api_response_variables(
                                        selected_api_endpoint
                                    )

                                # Get custom variables from persistent storage
                                custom_vars = load_custom_variables()
                                form_data["custom_variables"] = custom_vars

                                # Get variables for this category
                                category_vars = get_category_variables(
                                    category_name,
                                    category_data,
                                    available_variables,
                                    api_response_vars,
                                    custom_vars,
                                )

                                # Update the category content
                                container.clear()

                                with container:
                                    if category_vars:
                                        # Map category names to data-category CSS values
                                        category_data_map = {
                                            "Basic": "basic",
                                            "Command/Event": "command_event",
                                            "Statistics": "stats",
                                            "YouTube": "youtube",
                                            "Quotes": "quotes",
                                            "Custom": "custom",
                                        }
                                        data_category = category_data_map.get(
                                            category_name, "basic"
                                        )

                                        for var_name, var_desc in category_vars.items():
                                            ui.label(var_desc).classes(
                                                "variable-chip"
                                            ).props(
                                                f'data-category="{data_category}"'
                                            ).on(
                                                "click",
                                                lambda var=f"{{{var_name}}}",
                                                input_elem=expression_input: insert_variable_into_textarea(
                                                    var, var, input_elem
                                                ),
                                            )
                                    else:
                                        ui.label("No variables available").classes(
                                            "text-xs muted-text italic"
                                        )

                            def get_category_variables(
                                category_name,
                                category_data,
                                available_variables,
                                api_response_vars,
                                custom_vars,
                            ):
                                """Get variables for a specific category"""
                                category_vars = {}

                                if category_name == "Basic":
                                    # Basic variables
                                    for (
                                        var_key,
                                        var_desc,
                                    ) in available_variables.items():
                                        if var_key in category_data["prefixes"]:
                                            category_vars[var_key] = var_desc

                                elif category_name == "Command/Event":
                                    # Command/Event specific variables
                                    for (
                                        var_key,
                                        var_desc,
                                    ) in available_variables.items():
                                        # Include command-specific and event-specific variables
                                        if any(
                                            var_key.startswith(prefix)
                                            for prefix in [
                                                "command_",
                                                "amount",
                                                "tier",
                                                "months",
                                                "message",
                                                "viewer_count",
                                                "level",
                                                "cooldown",
                                                "usage_left",
                                                "count",
                                            ]
                                        ):
                                            category_vars[var_key] = var_desc

                                elif category_name == "Statistics":
                                    # Statistics variables
                                    for (
                                        var_key,
                                        var_desc,
                                    ) in available_variables.items():
                                        if any(
                                            var_key.startswith(prefix)
                                            for prefix in category_data["prefixes"]
                                        ):
                                            category_vars[var_key] = var_desc

                                elif category_name == "YouTube":
                                    # YouTube variables
                                    for (
                                        var_key,
                                        var_desc,
                                    ) in available_variables.items():
                                        if any(
                                            var_key.startswith(prefix)
                                            for prefix in category_data["prefixes"]
                                        ):
                                            category_vars[var_key] = var_desc

                                elif category_name == "Quotes":
                                    # Quote variables
                                    for (
                                        var_key,
                                        var_desc,
                                    ) in available_variables.items():
                                        if any(
                                            var_key.startswith(prefix)
                                            for prefix in category_data["prefixes"]
                                        ):
                                            category_vars[var_key] = var_desc

                                elif category_name == "Custom":
                                    # Custom variables
                                    for var_name, var_desc in custom_vars.items():
                                        # Extract just the variable name from the description
                                        if " - Custom: " in var_desc:
                                            clean_name = var_name.replace("custom_", "")
                                            category_vars[clean_name] = (
                                                f"{{{clean_name}}} - Custom Variable"
                                            )

                                return category_vars

                            def toggle_category(category_name, category_data):
                                """Toggle category expansion"""
                                current_state = form_data[
                                    "category_expansion_states"
                                ].get(category_name, False)
                                form_data["category_expansion_states"][
                                    category_name
                                ] = not current_state

                                # Update header icon and content
                                header_row = category_headers[category_name]
                                container = category_containers[category_name]
                                color_class = category_data["color_class"]

                                header_row.clear()
                                with header_row:
                                    icon_name = (
                                        "expand_less"
                                        if not current_state
                                        else "expand_more"
                                    )
                                    icon_element = ui.icon(
                                        icon_name, size="sm"
                                    ).classes(f"text-{color_class}")
                                    category_icons[category_name] = icon_element
                                    ui.label(
                                        f"{category_name} Variables"
                                        if category_name != "Arguments"
                                        else category_name
                                    ).classes(f"text-xs font-medium {color_class}")

                                # Update content visibility
                                if not current_state:
                                    update_category_display(
                                        category_name, category_data
                                    )
                                else:
                                    container.clear()

                            # Create category headers and containers
                            for category_data in categories_data:
                                category_name = category_data["name"]

                                # Create header row
                                header_row = ui.row().classes(
                                    "items-center cursor-pointer hover-theme-surface rounded px-2 py-1"
                                )
                                category_headers[category_name] = header_row

                                # Set initial expansion state
                                is_expanded = form_data[
                                    "category_expansion_states"
                                ].get(category_name, False)

                                with header_row:
                                    icon_name = (
                                        "expand_less" if is_expanded else "expand_more"
                                    )
                                    icon_element = ui.icon(
                                        icon_name, size="sm"
                                    ).classes(f"text-{category_data['color_class']}")
                                    category_icons[category_name] = icon_element

                                    label_text = (
                                        f"{category_name} Variables"
                                        if category_name != "Arguments"
                                        else category_name
                                    )
                                    ui.label(label_text).classes(
                                        f"text-xs font-medium {category_data['color_class']}"
                                    )

                                # Make header clickable
                                header_row.on(
                                    "click",
                                    lambda cat_name=category_name,
                                    cat_data=category_data: toggle_category(
                                        cat_name, cat_data
                                    ),
                                )

                                # Create container for category content
                                container = ui.column().classes("ml-4 space-y-1")
                                category_containers[category_name] = container

                                # Initialize category content if expanded
                                if is_expanded:
                                    update_category_display(
                                        category_name, category_data
                                    )

                            # No dynamic category updates needed for custom variables dialog

                    # Third column - Expressions/Modifiers panel
                    with ui.element("div").classes("w-full flex-shrink-0"):
                        # Expressions panel header
                        with ui.row().classes("items-center justify-between mb-3"):
                            ui.label("Expressions & Modifiers").classes(
                                "text-sm font-medium secondary-text"
                            )

                        # Expressions list container with scroll
                        with ui.element("div").classes("max-h-96 overflow-y-auto"):
                            expressions_container = ui.column().classes(
                                "w-full space-y-3"
                            )

                            # Initialize expression category expansion states
                            if "expression_expansion_states" not in form_data:
                                form_data["expression_expansion_states"] = {}

                            # Define expression categories with their data
                            expression_categories = [
                                {
                                    "name": "Time Formatting",
                                    "color_class": "text-blue-400",
                                    "expressions": [
                                        (
                                            "{time:timezone:hour_format:ampm_display}",
                                            "Time formatting - replace timezone, hour_format, ampm_display",
                                        ),
                                        ("{time:UTC:24}", "UTC 24-hour time"),
                                        (
                                            "{time:EST:12:show}",
                                            "EST 12-hour with AM/PM",
                                        ),
                                        (
                                            "{time:PST:12:hide}",
                                            "PST 12-hour without AM/PM",
                                        ),
                                        ("{time}", "Local time (24-hour)"),
                                    ],
                                },
                                {
                                    "name": "Math Operations",
                                    "color_class": "text-green-400",
                                    "expressions": [
                                        (
                                            "math(expression)",
                                            "Math calculation - replace with math expression",
                                        ),
                                        ("math(value1 + value2)", "Addition"),
                                        ("math(value1 - value2)", "Subtraction"),
                                        ("math(value1 * value2)", "Multiplication"),
                                        ("math(value1 / value2)", "Division"),
                                        ("math(round(value))", "Rounding"),
                                        ("math(max(value1, value2))", "Maximum value"),
                                        ("math(min(value1, value2))", "Minimum value"),
                                        ("math(abs(value))", "Absolute value"),
                                        ("math(int(value))", "Convert to integer"),
                                        ("math(float(value))", "Convert to float"),
                                        (
                                            "math(value ** power)",
                                            "Power/Exponentiation",
                                        ),
                                        ("math(value % divisor)", "Modulo/Remainder"),
                                    ],
                                },
                                {
                                    "name": "Comparisons",
                                    "color_class": "text-yellow-400",
                                    "expressions": [
                                        (
                                            "compare(value1, operator, value2)",
                                            "Comparison - replace operator (>, <, ==, !=, >=, <=)",
                                        ),
                                        ("compare(value1, >, value2)", "Greater than"),
                                        ("compare(value1, <, value2)", "Less than"),
                                        ("compare(value1, ==, value2)", "Equal to"),
                                        ("compare(value1, !=, value2)", "Not equal to"),
                                        (
                                            "compare(value1, >=, value2)",
                                            "Greater than or equal",
                                        ),
                                        (
                                            "compare(value1, <=, value2)",
                                            "Less than or equal",
                                        ),
                                    ],
                                },
                                {
                                    "name": "Date Functions",
                                    "color_class": "text-theme-primary",
                                    "expressions": [
                                        (
                                            "date_to_age(date_string)",
                                            "Calculate age from date - returns object with .years, .days, etc.",
                                        ),
                                        (
                                            "date_to_age(date_string).years",
                                            "Account age in years",
                                        ),
                                        (
                                            "date_to_age(date_string).days",
                                            "Account age in days",
                                        ),
                                        (
                                            "date_to_age(date_string).remaining_days",
                                            "Days after years",
                                        ),
                                        (
                                            "date_to_age(date_string).datetime",
                                            "Formatted creation date",
                                        ),
                                        (
                                            "date_diff_days(date1, date2)",
                                            "Days between two dates",
                                        ),
                                    ],
                                },
                            ]

                            # Create expression category containers and headers
                            expression_containers = {}
                            expression_headers = {}
                            expression_icons = {}

                            def toggle_expression_category(
                                category_name, category_data
                            ):
                                """Toggle expression category expansion"""
                                current_state = form_data[
                                    "expression_expansion_states"
                                ].get(category_name, False)
                                form_data["expression_expansion_states"][
                                    category_name
                                ] = not current_state

                                # Update header icon and content
                                header_row = expression_headers[category_name]
                                container = expression_containers[category_name]
                                color_class = category_data["color_class"]

                                header_row.clear()
                                with header_row:
                                    icon_name = (
                                        "expand_less"
                                        if not current_state
                                        else "expand_more"
                                    )
                                    icon_element = ui.icon(
                                        icon_name, size="sm"
                                    ).classes(f"text-{color_class}")
                                    expression_icons[category_name] = icon_element
                                    ui.label(f"{category_name}").classes(
                                        f"text-xs font-medium {color_class}"
                                    )

                                # Update content visibility
                                if not current_state:
                                    update_expression_category_display(
                                        category_name, category_data
                                    )
                                else:
                                    container.clear()

                            def update_expression_category_display(
                                category_name, category_data
                            ):
                                """Update expression category content"""
                                container = expression_containers[category_name]
                                color_class = category_data["color_class"]

                                container.clear()

                                with container:
                                    for expr_template, expr_desc in category_data[
                                        "expressions"
                                    ]:
                                        # Create template for insertion (remove example values)
                                        if category_name == "Time Formatting":
                                            if "{time:" in expr_template:
                                                insert_text = "{time:timezone:hour_format:ampm_display}"
                                            else:
                                                insert_text = "{time}"
                                        elif category_name == "Math Operations":
                                            if (
                                                "math(" in expr_template
                                                and "expression" in expr_template
                                            ):
                                                insert_text = "math(expression)"
                                            else:
                                                # For specific functions, keep the template
                                                insert_text = expr_template
                                        elif category_name == "Comparisons":
                                            if (
                                                "compare(" in expr_template
                                                and "operator" in expr_template
                                            ):
                                                insert_text = (
                                                    "compare(value1, operator, value2)"
                                                )
                                            else:
                                                # For specific operators, keep the template
                                                insert_text = expr_template
                                        elif category_name == "Date Functions":
                                            if (
                                                "date_to_age(date_string)"
                                                in expr_template
                                            ):
                                                insert_text = "date_to_age(date_string)"
                                            elif "date_diff_days(" in expr_template:
                                                insert_text = (
                                                    "date_diff_days(date1, date2)"
                                                )
                                            else:
                                                # For property access, keep as is
                                                insert_text = expr_template
                                        else:
                                            insert_text = expr_template

                                        # Display both expression and description
                                        display_text = f"{expr_template} - {expr_desc}"

                                        ui.label(display_text).classes(
                                            "expression-chip cursor-pointer"
                                        ).props(
                                            f'data-category="{category_name.lower().replace(" ", "_")}"'
                                        ).on(
                                            "click",
                                            lambda expr=insert_text,
                                            input_elem=expression_input: insert_variable_into_textarea(
                                                expr, expr, input_elem
                                            ),
                                        )

                            # Create category headers and containers
                            for category_data in expression_categories:
                                category_name = category_data["name"]
                                color_class = category_data["color_class"]

                                # Create header row
                                header_row = ui.row().classes(
                                    "items-center cursor-pointer hover-theme-surface rounded px-2 py-1"
                                )
                                expression_headers[category_name] = header_row

                                # Set initial expansion state
                                is_expanded = form_data[
                                    "expression_expansion_states"
                                ].get(category_name, False)

                                with header_row:
                                    icon_name = (
                                        "expand_less" if is_expanded else "expand_more"
                                    )
                                    icon_element = ui.icon(
                                        icon_name, size="sm"
                                    ).classes(f"text-{color_class}")
                                    expression_icons[category_name] = icon_element
                                    ui.label(f"{category_name}").classes(
                                        f"text-xs font-medium {color_class}"
                                    )

                                # Make header clickable
                                header_row.on(
                                    "click",
                                    lambda cat_name=category_name,
                                    cat_data=category_data: toggle_expression_category(
                                        cat_name, cat_data
                                    ),
                                )

                                # Create container for category content
                                container = ui.column().classes("ml-4 space-y-1")
                                expression_containers[category_name] = container

                                # Initialize category content if expanded
                                if is_expanded:
                                    update_expression_category_display(
                                        category_name, category_data
                                    )

    custom_variable_dialog.open()


def create_custom_variable(
    var_name: str,
    expression: str,
    form_data: dict,
    update_callback,
    edit_mode: bool = False,
    edit_var_name: str = "",
):
    """Create or update a custom variable from expression"""
    try:
        if not var_name.strip():
            notify("Variable name is required", type="negative")
            return

        if not expression.strip():
            notify("Expression is required", type="negative")
            return

        # Validate that all variables in the expression exist
        invalid_vars = validate_expression_variables(expression.strip())
        if invalid_vars:
            notify(
                f"Expression contains invalid variables: {', '.join(invalid_vars)}. "
                "Please check variable names and ensure they exist.",
                type="negative",
            )
            return

        # Clean variable name
        clean_name = var_name.strip()
        if not clean_name.startswith("custom_"):
            clean_name = f"custom_{clean_name}"

        if edit_mode and edit_var_name:
            # In edit mode, delete the old variable first if name changed
            if edit_var_name != clean_name:
                remove_custom_variable(edit_var_name)

        # Save to persistent storage
        success = add_custom_variable(clean_name, expression.strip())

        if success:
            # Update form data for immediate UI update
            if "custom_variables" not in form_data:
                form_data["custom_variables"] = {}

            form_data["custom_variables"][clean_name] = (
                f"{{{clean_name}}} - Custom: {expression.strip()}"
            )

            action_text = "updated" if edit_mode else "created"
            notify(
                f"Custom variable '{clean_name}' {action_text} successfully!",
                type="positive",
            )

            # Close dialog and refresh variables
            if custom_variable_dialog:
                custom_variable_dialog.close()

            # Call update callback
            update_callback()
        else:
            notify("Failed to save custom variable", type="negative")

    except Exception as e:
        logger.error(f"Error creating/updating custom variable: {e}", exc_info=True)
        notify(f"Error creating/updating custom variable: {str(e)}", type="negative")


def edit_custom_variable(var_name: str, form_data: dict, update_callback):
    """Edit a custom variable by opening the dialog with existing data"""
    try:
        # Check if variable exists
        custom_vars = load_custom_variables()
        if var_name not in custom_vars:
            notify(f"Custom variable '{var_name}' not found", type="negative")
            return

        # Open dialog in edit mode
        show_custom_variable_dialog(
            form_data, update_callback, edit_mode=True, edit_var_name=var_name
        )

    except Exception as e:
        logger.error(
            f"Error opening edit dialog for custom variable: {e}", exc_info=True
        )
        notify(f"Error opening edit dialog: {str(e)}", type="negative")


def delete_custom_variable(var_name: str, update_callback, form_data: dict = None):
    """Delete a custom variable from storage and refresh UI"""
    try:
        # Remove from persistent storage
        success = remove_custom_variable(var_name)

        if success:
            # Also remove from form_data if it exists there
            if (
                form_data
                and "custom_variables" in form_data
                and var_name in form_data["custom_variables"]
            ):
                del form_data["custom_variables"][var_name]

            notify(
                f"Custom variable '{var_name}' deleted successfully!", type="positive"
            )

            # Refresh the variables display
            update_callback()
        else:
            notify("Failed to delete custom variable", type="negative")

    except Exception as e:
        logger.error(f"Error deleting custom variable: {e}", exc_info=True)
        notify(f"Error deleting custom variable: {str(e)}", type="negative")


def get_available_variables(
    item_type: str,
    event_type: Optional[str] = None,
    command_type: Optional[str] = None,
    api_enabled: bool = False,
) -> Dict[str, str]:
    """Get available variables for the item type"""
    base_variables = {
        "username": "{username} - User who triggered",
        "timestamp": "{timestamp} - Current time",
        "datetime": "{datetime} - Full date and time",
        "date": "{date} - Current date",
        "time": "{time} - Current time",
    }

    # Quote variables available for all item types
    quote_variables = {
        "quote.id.text": "{quote.id.text} - Quote text (replace 'id' with quote number, or leave as 'id' for random quote)",
        "quote.id.author": "{quote.id.author} - Quote author (replace 'id' with quote number, or leave as 'id' for random quote)",
        "quote.id.quote_number": "{quote.id.quote_number} - Quote number (replace 'id' with quote number, or leave as 'id' for random quote)",
        "quote.id.date_added": "{quote.id.date_added} - When quote was added (replace 'id' with quote number, or leave as 'id' for random quote)",
        "quote.id.added_by": "{quote.id.added_by} - Who added the quote (replace 'id' with quote number, or leave as 'id' for random quote)",
    }

    # YouTube variables available for all item types
    youtube_variables = {
        "youtube.latest_video_url": "{youtube.latest_video_url} - Global latest YouTube video URL across all channels",
        "youtube.latest_video_title": "{youtube.latest_video_title} - Global latest YouTube video title across all channels",
        "youtube.latest_video_id": "{youtube.latest_video_id} - Global latest YouTube video ID across all channels",
        "youtube.latest_video_channel": "{youtube.latest_video_channel} - Channel name of the global latest video",
        "youtube.connection_status": "{youtube.connection_status} - YouTube connection status",
        "youtube.channel_count": "{youtube.channel_count} - Number of configured YouTube channels",
    }

    # Add channel-specific variables dynamically
    try:
        from ..dataobjects import state_manager

        youtube_data = state_manager.get_youtube_data()
        if youtube_data and youtube_data.channels:
            for channel_key, channel_data in youtube_data.channels.items():
                # Only create variables if we have a valid channel_title
                # Skip if channel_title is missing, empty, or same as channel_key (channel ID)
                channel_title = channel_data.get("channel_title", "")
                if not channel_title or channel_title == channel_key:
                    # Skip channels without a proper title or where title equals the ID
                    continue

                # Clean channel title for variable name (remove spaces, special chars)
                clean_channel_name = (
                    channel_title.lower()
                    .replace(" ", "")
                    .replace("-", "")
                    .replace("_", "")
                )

                youtube_variables.update(
                    {
                        f"{clean_channel_name}_latest_video_url": f"{{{clean_channel_name}_latest_video_url}} - Latest video URL from {channel_title}",
                        f"{clean_channel_name}_latest_video_title": f"{{{clean_channel_name}_latest_video_title}} - Latest video title from {channel_title}",
                        f"{clean_channel_name}_latest_video_id": f"{{{clean_channel_name}_latest_video_id}} - Latest video ID from {channel_title}",
                        f"{clean_channel_name}_channel_title": f"{{{clean_channel_name}_channel_title}} - Channel title: {channel_title}",
                        f"{clean_channel_name}_channel_url": f"{{{clean_channel_name}_channel_url}} - Channel URL for {channel_title}",
                        f"{clean_channel_name}_last_updated": f"{{{clean_channel_name}_last_updated}} - Last updated time for {channel_title}",
                    }
                )
    except Exception as e:
        logger.warning(f"Could not load YouTube channel variables: {e}")

    # Statistics variables available for all item types
    statistics_variables = {
        "stats.alerts.bit_alerts_played": "{stats.alerts.bit_alerts_played} - Total bit alerts played",
        "stats.alerts.total_bits": "{stats.alerts.total_bits} - Total bits given",
        "stats.alerts.resubs_played": "{stats.alerts.resubs_played} - Total resub alerts played",
        "stats.alerts.new_subs_played": "{stats.alerts.new_subs_played} - Total new sub alerts played",
        "stats.alerts.gift_subs_played": "{stats.alerts.gift_subs_played} - Total gift sub alerts played",
        "stats.alerts.total_gift_subs": "{stats.alerts.total_gift_subs} - Total gift subs given",
        "stats.alerts.follow_alerts_played": "{stats.alerts.follow_alerts_played} - Total follow alerts played",
        "stats.alerts.raids": "{stats.alerts.raids} - Total raids",
        "stats.alerts.point_alerts_redeemed": "{stats.alerts.point_alerts_redeemed} - Total channel point redemptions",
        "stats.hype_trains.total_completions": "{stats.hype_trains.total_completions} - Total hype trains completed",
        "stats.connectors.connectors_created": "{stats.connectors.connectors_created} - Total connectors created",
        "stats.connectors.connectors_triggered": "{stats.connectors.connectors_triggered} - Total connectors triggered",
        "stats.connectors.total_triggers": "{stats.connectors.total_triggers} - Total connector trigger events",
        "stats.chatbot.commands_created": "{stats.chatbot.commands_created} - Total commands created",
        "stats.chatbot.commands_triggered": "{stats.chatbot.commands_triggered} - Total commands triggered",
        "stats.chatbot.events_created": "{stats.chatbot.events_created} - Total events created",
        "stats.chatbot.events_triggered": "{stats.chatbot.events_triggered} - Total events triggered",
        "stats.chatbot.total_interactions": "{stats.chatbot.total_interactions} - Total chatbot interactions",
        "stats.quotes.quotes_created": "{stats.quotes.quotes_created} - Total quotes created",
        "stats.quotes.total_quotes_redeemed": "{stats.quotes.total_quotes_redeemed} - Total quotes redeemed",
        "stats.chat.twitch_messages_received": "{stats.chat.twitch_messages_received} - Total Twitch messages received",
        "stats.chat.total_messages": "{stats.chat.total_messages} - Total messages processed",
        "stats.session.session_duration": "{stats.session.session_duration} - Current session duration (seconds)",
    }

    # Add hype train level completion variables
    for level in get_statistics_manager().data.hype_trains.level_completions:
        statistics_variables.update(
            {
                f"stats.hype_trains.level_{level}_completions": f"{{stats.hype_trains.level_{level}_completions}} - Level {level} hype trains completed",
            }
        )
    statistics_variables.update(
        {
            "stats.hype_trains.total_completions": f"{{stats.hype_trains.total_completions}} - Total hype trains completed",
        }
    )

    if item_type == "command":
        # Base command variables available to all command types
        command_variables = {
            "cooldown": "{cooldown} - Current cooldown (seconds)",
            "usage_left": "{usage_left} - Uses remaining",
            "last_used": "{last_used} - Last used timestamp",
            "command_name": "{command_name} - The command name",
            # Command message variables
            "command_message": "{command_message} - Full message after command name",
            "command_first_word": "{command_first_word} - First word of command message",
            "command_last_word": "{command_last_word} - Last word of command message",
            "command_word_1": "{command_word_1} - First word of command message",
            "command_word_2": "{command_word_2} - Second word of command message",
            "command_word_3": "{command_word_3} - Third word of command message",
        }

        # Counter-specific variables - only for counter commands
        if command_type == "counter":
            command_variables.update(
                {
                    "count": "{count} - Command usage count",
                }
            )

        # Reset-specific variables - only for reset commands
        if command_type == "reset":
            command_variables.update(
                {
                    "target_command": "{target_command} - Target command being reset",
                    "target_count": "{target_count} - Target command's current count",
                }
            )

        return {
            **base_variables,
            **command_variables,
            **quote_variables,
            **statistics_variables,
            **youtube_variables,
        }
    else:  # Event variables - comprehensive list based on event type
        # Default event variables (available for all events)
        event_variables = {
            "amount": "{amount} - Bits/donation amount",
            "message": "{message} - User's message",
            "source": "{source} - Event source (twitch)",
        }

        # Event-specific variables
        if event_type == "follow":
            event_variables.update(
                {
                    "follower_count": "{follower_count} - Total followers",
                }
            )
        elif event_type == "subscription":
            event_variables.update(
                {
                    "tier": "{tier} - Subscription tier (1, 2, 3)",
                    "tier_name": "{tier_name} - Subscription tier name",
                    "months": "{months} - Total months subscribed",
                    "streak": "{streak} - Current streak months",
                    "is_gift": "{is_gift} - Whether this was a gift",
                }
            )
        elif event_type == "resubscription":
            event_variables.update(
                {
                    "tier": "{tier} - Subscription tier (1, 2, 3)",
                    "tier_name": "{tier_name} - Subscription tier name",
                    "months": "{months} - Total months subscribed",
                    "streak": "{streak} - Current streak months",
                    "cumulative_months": "{cumulative_months} - Total cumulative months",
                }
            )
        elif event_type == "gift_subscription":
            event_variables.update(
                {
                    "tier": "{tier} - Gift subscription tier",
                    "tier_name": "{tier_name} - Gift subscription tier name",
                    "recipient_name": "{recipient_name} - Gift recipient username",
                    "recipient_display_name": "{recipient_display_name} - Gift recipient display name",
                    "gifter_name": "{gifter_name} - Gifter username",
                    "total_gifts": "{total_gifts} - Total gifts given",
                }
            )
        elif event_type == "bits":
            event_variables.update(
                {
                    "bits_amount": "{bits_amount} - Total bits cheered",
                    "bits_used": "{bits_used} - Bits used in power-up",
                    "power_up_type": "{power_up_type} - Power-up type",
                    "is_anonymous": "{is_anonymous} - Whether cheer was anonymous",
                }
            )
        elif event_type == "donation":
            event_variables.update(
                {
                    "currency": "{currency} - Donation currency (USD, EUR, etc.)",
                    "formatted_amount": "{formatted_amount} - Formatted amount with currency",
                    "donation_message": "{donation_message} - Donation message",
                }
            )
        elif event_type == "raid":
            event_variables.update(
                {
                    "viewer_count": "{viewer_count} - Number of raiders",
                    "raider_name": "{raider_name} - Raider username",
                    "raid_duration": "{raid_duration} - Raid duration",
                }
            )
        elif event_type == "hype_train_start":
            event_variables.update(
                {
                    "level": "{level} - Hype train level",
                    "goal": "{goal} - Level goal",
                    "progress": "{progress} - Current progress",
                    "conductor_name": "{conductor_name} - Train conductor",
                }
            )
        elif event_type == "hype_train_progress":
            event_variables.update(
                {
                    "level": "{level} - Current hype train level",
                    "progress": "{progress} - Current progress",
                    "goal": "{goal} - Current level goal",
                    "total_contributions": "{total_contributions} - Total contributions",
                }
            )
        elif event_type == "hype_train_end":
            event_variables.update(
                {
                    "level": "{level} - Final hype train level",
                    "total_contributions": "{total_contributions} - Total contributions",
                    "top_contributors": "{top_contributors} - Top contributors list",
                    "conductor_name": "{conductor_name} - Train conductor",
                }
            )
        elif event_type == "channel_point_redemption":
            event_variables.update(
                {
                    "reward_name": "{reward_name} - Channel point reward name",
                    "reward_cost": "{reward_cost} - Points cost",
                    "user_input": "{user_input} - User input text",
                    "reward_id": "{reward_id} - Reward ID",
                }
            )
        elif event_type == "specific_time":
            event_variables.update(
                {
                    "time": "{time} - The specific time that triggered this event",
                }
            )
        elif event_type == "chat_message":
            event_variables.update(
                {
                    "message": "{message} - The chat message that triggered this event",
                }
            )

        # Combine all variables for events
        combined_variables = {
            **base_variables,
            **event_variables,
            **quote_variables,
            **statistics_variables,
        }

        # Add API response variables if API is enabled
        if api_enabled:
            api_variables = {
                "api_status": "{api_status} - HTTP status code",
                "api_success": "{api_success} - Whether API call was successful",
                "api_error": "{api_error} - Error message if API call failed",
            }

            # Add processed variables from variable processing
            processing_vars = {
                "account_age.days": "{account_age.days} - Account age in days",
                "account_age.years": "{account_age.years} - Account age in years",
                "account_age.remaining_days": "{account_age.remaining_days} - Remaining days after years",
                "account_age.datetime": "{account_age.datetime} - Formatted account creation date",
            }
            api_variables.update(processing_vars)

            combined_variables.update(api_variables)

        # Add YouTube variables to events
        combined_variables.update(youtube_variables)

        return combined_variables


def insert_variable(variable: str):
    """Insert variable into response text (legacy function)"""
    # This is kept for backward compatibility
    notify(f"Click to insert: {variable}", type="info")


def insert_variable_into_textarea(variable: str, description: str, textarea_element):
    """Insert variable into the response textarea"""
    try:
        if textarea_element is None:
            logger.error("Textarea element is None, cannot insert variable")
            return

        # Get current value of the textarea
        current_value = textarea_element.value or ""

        # Insert the variable at cursor position or at the end
        # For now, we'll append to the end with a space
        new_value = current_value + variable + " "

        # Update the textarea value
        textarea_element.set_value(new_value)

        # Show a brief notification
        notify(f"Inserted {variable}", type="positive", timeout=1)

    except Exception as e:
        logger.error(f"Error inserting variable into textarea: {e}")
        notify(f"Error inserting variable: {str(e)}", type="negative")


def get_variable_examples(
    item_type: str, event_type: Optional[str] = None
) -> Dict[str, str]:
    """Get example usage for different event/command types"""
    if item_type == "command":
        return {
            "Basic Command": "!hello → Hello {username}! Welcome to the stream!",
            "Counter Command": "!deathcount → This is death #{count} today. RIP!",
            "Reset Command": "!resetdeaths → Resetting death counter for {username}",
            "With Variables": "!uptime → Stream has been live for {timestamp}",
        }
    else:
        # Event-specific examples
        examples = {}

        if event_type == "follow":
            examples.update(
                {
                    "Welcome Message": "Thanks for following {username}! Welcome to the community! 🎉",
                    "Follower Goal": "New follower! We're now at {follower_count} followers! Thank you {username}!",
                    "Personal Welcome": "Hey {username}, thanks for the follow! Make yourself at home! 🏠",
                }
            )
        elif event_type == "subscription":
            examples.update(
                {
                    "New Sub": "Welcome {username} as a new {tier_name} subscriber! Thank you! ⭐",
                    "With Message": "{username} subscribed: {message} 🎊",
                    "Tier Specific": "Thank you {username} for the {tier_name} sub! You get special perks! 🎁",
                }
            )
        elif event_type == "resubscription":
            examples.update(
                {
                    "Resub Welcome": "Welcome back {username}! Thanks for {months} months of support! 👑",
                    "Long Time Supporter": "{username} has been supporting for {cumulative_months} months! Legend! 🏆",
                    "Resub Streak": "{username} is on a {streak} month resub streak! 🔥",
                }
            )
        elif event_type == "gift_subscription":
            examples.update(
                {
                    "Gift Received": "{recipient_name} just got a {tier_name} gift from {gifter_name}! 🎁",
                    "Multiple Gifts": "{gifter_name} gifted {total_gifts} subs! Thank you so much! 🙏",
                    "Anonymous Gift": "Someone just gifted a {tier_name} sub to {recipient_name}! Thank you! 😊",
                }
            )
        elif event_type == "bits":
            examples.update(
                {
                    "Bits Cheer": "Wow {username}! Thanks for the {amount} bits! 💎",
                    "Power-up": "{username} used {power_up_type} with {bits_amount} bits! 🚀",
                    "Bits Goal": "{username} brought us to {amount} bits! Thank you! 🎯",
                }
            )
        elif event_type == "raid":
            examples.update(
                {
                    "Raid Welcome": "Welcome raiders from {raider_name}! Thanks for bringing {viewer_count} viewers! 👋",
                    "Raid Thanks": "Thank you {raider_name} for the raid of {viewer_count}! Welcome everyone! 🎉",
                    "Raid Shoutout": "Shoutout to {raider_name} for bringing {viewer_count} awesome viewers! 📣",
                }
            )
        elif event_type == "hype_train_start":
            examples.update(
                {
                    "Train Start": "🚂 Hype train started by {conductor_name}! Let's reach level {level}! 🚂",
                    "Train Goal": "Hype train level {level} goal: {goal} contributions! Led by {conductor_name} 🎯",
                    "Community Event": "Community hype train activated! {conductor_name} is leading us! 🎊",
                }
            )
        elif event_type == "hype_train_progress":
            examples.update(
                {
                    "Progress Update": "Hype train level {level}: {progress}/{goal} - Keep it up! 🚂",
                    "Level Complete": "Level {level} complete! Moving to level {level}! 🎉",
                    "Final Push": "Almost there! {progress} more to reach level {level}! 💪",
                }
            )
        elif event_type == "hype_train_end":
            examples.update(
                {
                    "Train Complete": "Hype train completed at level {level}! {total_contributions} contributions! 🎉",
                    "Top Contributors": "Top contributors: {top_contributors} - Thank you! 🏆",
                    "Train Summary": "Amazing train led by {conductor_name} reached level {level}! 🎊",
                }
            )
        elif event_type == "donation":
            examples.update(
                {
                    "Donation Thanks": "Thank you {username} for the ${amount} donation! 💝",
                    "Donation with Message": "{username}: {donation_message} - Thanks for the ${formatted_amount}! 🙏",
                    "Goal Progress": "{username} brought us closer to our goal with ${amount}! 🎯",
                }
            )
        elif event_type == "interval":
            examples.update(
                {
                    "Periodic Reminder": "Don't forget to follow! We're at {follower_count} followers! 🎯",
                    "Time-based Message": "Current time: {timestamp} - Thanks for watching! ⏰",
                    "Stats Update": "Stream stats: {stats.chatbot.commands_triggered} commands used today! 📊",
                }
            )
        else:
            # Generic examples for unspecified event types
            examples.update(
                {
                    "Generic Welcome": "Thanks {username} for the {source} event! Welcome! 🎉",
                    "Simple Thanks": "Thank you {username}! Appreciate the support! 💕",
                    "With Timestamp": "Event from {username} at {timestamp} - Thank you! 📅",
                }
            )

        return examples


def show_custom_variable_help_dialog():
    """Show comprehensive help dialog for custom variables"""
    help_dialog = ui.dialog().props("persistent")

    with (
        help_dialog,
        ui.card().style(
            "width: 1200px; max-width: none; max-height: 80vh; overflow-y: auto"
        ),
    ):
        with ui.card().classes("w-full"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Custom Variables Help Guide").classes(
                        "text-xl font-semibold text-indigo-400"
                    )
                    ui.button(icon="close", on_click=help_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-6 space-y-6"):
                    ui.label(
                        "Custom variables allow you to create dynamic expressions that combine variables, functions, and calculations."
                    ).classes("text-sm secondary-text mb-4")

                    # Time Formatting Section
                    with ui.card().classes("hint-info"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("🕐 Time Formatting").classes(
                                "text-lg font-semibold text-blue-400"
                            )
                            ui.label(
                                "Format: {time:timezone:hour_format:ampm_display}"
                            ).classes("text-sm font-medium text-blue-300")

                            # Timezones
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Timezones:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                timezones = [
                                    "UTC - Coordinated Universal Time",
                                    "EST - Eastern Standard Time (-5)",
                                    "CST - Central Standard Time (-6)",
                                    "MST - Mountain Standard Time (-7)",
                                    "PST - Pacific Standard Time (-8)",
                                    "EDT - Eastern Daylight Time (-4)",
                                    "CDT - Central Daylight Time (-5)",
                                    "MDT - Mountain Daylight Time (-6)",
                                    "PDT - Pacific Daylight Time (-7)",
                                ]
                                for tz in timezones:
                                    ui.label(f"• {tz}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Hour formats
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Hour Formats:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                formats = [
                                    "12 - 12-hour format (1-12)",
                                    "24 - 24-hour format (0-23)",
                                ]
                                for fmt in formats:
                                    ui.label(f"• {fmt}").classes(
                                        "text-xs secondary-text"
                                    )

                            # AM/PM display
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("AM/PM Display:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                displays = [
                                    "show - Display AM/PM",
                                    "hide - Hide AM/PM (12hr format only)",
                                ]
                                for disp in displays:
                                    ui.label(f"• {disp}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "{time:EST:12:show} → 02:30 PM EST",
                                    "{time:UTC:24} → 19:30 UTC",
                                    "{time:PST:12:hide} → 11:30 PST",
                                    "{time} → 19:30 (local time, 24hr)",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-green-400 font-mono"
                                    )

                    # Math Operations Section
                    with ui.card().classes("hint-success"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("🔢 Math Operations").classes(
                                "text-lg font-semibold text-green-400"
                            )
                            ui.label("Format: math(expression)").classes(
                                "text-sm font-medium text-green-300"
                            )

                            # Available functions
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Available Functions:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                functions = [
                                    "abs(x) - Absolute value",
                                    "round(x) - Round to nearest integer",
                                    "min(x, y, ...) - Minimum value",
                                    "max(x, y, ...) - Maximum value",
                                    "sum(list) - Sum of values",
                                    "len(list) - Length of list",
                                    "int(x) - Convert to integer",
                                    "float(x) - Convert to float",
                                ]
                                for func in functions:
                                    ui.label(f"• {func}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Operators
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Operators:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                operators = [
                                    "+ - Addition",
                                    "- - Subtraction",
                                    "* - Multiplication",
                                    "/ - Division",
                                    "** - Power",
                                    "% - Modulo",
                                ]
                                for op in operators:
                                    ui.label(f"• {op}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "math({stats.alerts.bit_alerts_played} / 10) → Bits divided by 10",
                                    "math(round({stats.chatbot.commands_triggered} * 1.5)) → Rounded calculation",
                                    "math(max({stats.alerts.resubs_played}, {stats.alerts.new_subs_played})) → Maximum of two values",
                                    "math(abs({stats.alerts.total_bits} - 1000)) → Absolute difference",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-green-400 font-mono"
                                    )

                    # Comparison Operations Section
                    with ui.card().classes(
                        "border border-yellow-500/30 bg-yellow-500/5"
                    ):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("⚖️ Comparison Operations").classes(
                                "text-lg font-semibold text-yellow-400"
                            )
                            ui.label(
                                "Format: compare(value1, operator, value2)"
                            ).classes("text-sm font-medium text-yellow-300")

                            # Operators
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Operators:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                operators = [
                                    "== - Equal to",
                                    "!= - Not equal to",
                                    "> - Greater than",
                                    "< - Less than",
                                    ">= - Greater than or equal",
                                    "<= - Less than or equal",
                                ]
                                for op in operators:
                                    ui.label(f"• {op}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "compare({stats.chatbot.commands_triggered}, >, 100) → True if > 100 commands",
                                    "compare({stats.alerts.bit_alerts_played}, ==, 0) → True if no bit alerts",
                                    "compare({stats.alerts.total_bits}, >=, 1000) → True if 1000+ bits",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-yellow-400 font-mono"
                                    )

                    # Date Functions Section
                    with ui.card().classes(
                        "border border-[var(--color-border-accent)] bg-[var(--color-primary-light)]"
                    ):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("📅 Date Functions").classes(
                                "text-lg font-semibold text-theme-primary"
                            )

                            # date_to_age function
                            with ui.column().classes("ml-4 space-y-2"):
                                ui.label("date_to_age(date_string)").classes(
                                    "text-sm font-medium text-theme-primary-light"
                                )
                                ui.label("Returns age components from a date:").classes(
                                    "text-xs secondary-text"
                                )
                                with ui.column().classes("ml-4 space-y-1"):
                                    ui.label("• days - Total days").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• years - Total years").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label(
                                        "• remaining_days - Days after years"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• datetime - Formatted date string"
                                    ).classes("text-xs secondary-text")

                            # date_diff_days function
                            with ui.column().classes("ml-4 space-y-2"):
                                ui.label("date_diff_days(date1, date2)").classes(
                                    "text-sm font-medium text-theme-primary-light"
                                )
                                ui.label(
                                    "Returns absolute difference in days between two dates"
                                ).classes("text-xs secondary-text")

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "date_to_age({data.created_at}).years → Account age in years",
                                    "date_to_age({data.created_at}).days → Account age in days",
                                    "date_diff_days({data.created_at}, {timestamp}) → Days since creation",
                                    "{account_age.days} - Direct access to calculated age",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-theme-primary font-mono"
                                    )

                    # Statistics Variables Section
                    with ui.card().classes("hint-error"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("📊 Statistics Variables").classes(
                                "text-lg font-semibold text-red-400"
                            )
                            ui.label("Format: {stats.category.subcategory}").classes(
                                "text-sm font-medium text-red-300"
                            )

                            # Categories
                            with ui.column().classes("ml-4 space-y-2"):
                                ui.label("Categories:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                categories = [
                                    "alerts - Alert/playback statistics",
                                    "chatbot - Command and event usage",
                                    "chat - Message processing stats",
                                    "connectors - Connector trigger counts",
                                    "hype_trains - Hype train completions",
                                    "quotes - Quote system usage",
                                    "session - Current session data",
                                ]
                                for cat in categories:
                                    ui.label(f"• {cat}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "{stats.alerts.bit_alerts_played} → Total bit alerts played",
                                    "{stats.chatbot.commands_triggered} → Commands used today",
                                    "{stats.hype_trains.total_completions} → Total hype trains",
                                    "{stats.chat.twitch_messages_received} → Messages received",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-red-400 font-mono"
                                    )

                    # YouTube Variables Section
                    with ui.card().classes("border border-pink-500/30 bg-pink-500/5"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("📺 YouTube Variables").classes(
                                "text-lg font-semibold text-pink-400"
                            )
                            ui.label("Format: {youtube.variable_name}").classes(
                                "text-sm font-medium text-pink-300"
                            )

                            # Global variables
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Global Variables:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                globals_vars = [
                                    "latest_video_url - Latest video URL",
                                    "latest_video_title - Latest video title",
                                    "latest_video_id - Latest video ID",
                                    "latest_video_channel - Latest video channel",
                                    "connection_status - Connection status",
                                    "channel_count - Number of channels",
                                ]
                                for var in globals_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Channel-specific variables
                            with ui.column().classes("ml-4 space-y-1 mt-2"):
                                ui.label(
                                    "Channel-Specific (replace 'ChannelName' with actual channel):"
                                ).classes("text-sm font-medium secondary-text")
                                channel_vars = [
                                    "{ChannelName}_latest_video_url - Channel's latest video URL",
                                    "{ChannelName}_latest_video_title - Channel's latest video title",
                                    "{ChannelName}_latest_video_id - Channel's latest video ID",
                                    "{ChannelName}_channel_title - Channel display name",
                                    "{ChannelName}_channel_url - Channel URL",
                                    "{ChannelName}_last_updated - Last update time",
                                ]
                                for var in channel_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                    # Quote Variables Section
                    with ui.card().classes("hint-info"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("💬 Quote Variables").classes(
                                "text-lg font-semibold text-teal-400"
                            )
                            ui.label(
                                "Format: {quote.id.field} (use 'id' for random quote or quote number)"
                            ).classes("text-sm font-medium text-teal-300")

                            # Fields
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Fields:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                fields = [
                                    "text - The quote text",
                                    "author - Quote author",
                                    "quote_number - Quote number",
                                    "date_added - When quote was added",
                                    "added_by - Who added the quote",
                                ]
                                for field in fields:
                                    ui.label(f"• {field}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Examples
                            with ui.column().classes("ml-4 space-y-1 mt-3"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "{quote.1.text} → Text of quote #1",
                                    "{quote.id.author} → Random quote author",
                                    "{quote.5.date_added} → When quote #5 was added",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-teal-400 font-mono"
                                    )

                    # Command & Event Variables Section
                    with ui.card().classes("hint-warning"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("🎯 Command & Event Variables").classes(
                                "text-lg font-semibold text-orange-400"
                            )

                            # Basic variables
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Basic Variables:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                basic_vars = [
                                    "{username} - User who triggered",
                                    "{timestamp} - Current timestamp",
                                    "{date} - Current date",
                                    "{time} - Current time",
                                ]
                                for var in basic_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Command-specific
                            with ui.column().classes("ml-4 space-y-1 mt-2"):
                                ui.label("Command Variables:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                cmd_vars = [
                                    "{command_message} - Full message after command",
                                    "{command_first_word} - First word",
                                    "{command_last_word} - Last word",
                                    "{command_word_N} - Nth word (1-based)",
                                    "{count} - Usage counter",
                                    "{cooldown} - Cooldown seconds",
                                    "{usage_left} - Uses remaining",
                                ]
                                for var in cmd_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Event-specific
                            with ui.column().classes("ml-4 space-y-1 mt-2"):
                                ui.label("Event Variables:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                event_vars = [
                                    "{amount} - Bits/donation amount",
                                    "{tier} - Subscription tier",
                                    "{months} - Subscription months",
                                    "{message} - Chat message",
                                    "{viewer_count} - Raid viewers",
                                    "{level} - Hype train level",
                                ]
                                for var in event_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                    # API Variables Section
                    with ui.card().classes("hint-info"):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("🔗 API Variables").classes(
                                "text-lg font-semibold text-cyan-400"
                            )

                            # API response variables
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("API Response:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                api_vars = [
                                    "{api_status} - HTTP status code",
                                    "{api_success} - Success boolean",
                                    "{api_error} - Error message",
                                ]
                                for var in api_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                            # Processed variables
                            with ui.column().classes("ml-4 space-y-1 mt-2"):
                                ui.label("Processed Variables:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                proc_vars = [
                                    "{account_age.days} - Account age in days",
                                    "{account_age.years} - Account age in years",
                                    "{account_age.remaining_days} - Days after years",
                                    "{account_age.datetime} - Formatted datetime",
                                ]
                                for var in proc_vars:
                                    ui.label(f"• {var}").classes(
                                        "text-xs secondary-text"
                                    )

                    # Nested Access Section
                    with ui.card().classes(
                        "border border-[var(--color-border-accent)] bg-[var(--color-primary-light)]"
                    ):
                        with ui.column().classes("p-4 space-y-3"):
                            ui.label("🔍 Nested Object Access").classes(
                                "text-lg font-semibold text-indigo-400"
                            )
                            ui.label(
                                "Access nested properties using dot notation"
                            ).classes("text-sm font-medium text-indigo-300")

                            # Examples
                            with ui.column().classes("ml-4 space-y-1"):
                                ui.label("Examples:").classes(
                                    "text-sm font-medium secondary-text"
                                )
                                examples = [
                                    "{data.user.display_name} - Access nested user data",
                                    "{api_response.data.items[0].name} - API response array access",
                                    "{custom_var.subfield} - Custom variable subfield",
                                ]
                                for ex in examples:
                                    ui.label(f"• {ex}").classes(
                                        "text-xs text-indigo-400 font-mono"
                                    )

                # Dialog footer
                with ui.row().classes(
                    "w-full justify-end p-4 border-t border-gray-700"
                ):
                    ui.button(text="Close", on_click=help_dialog.close).classes(
                        "btn-cancel px-4 py-2"
                    )

    help_dialog.open()


def sanitize_form_data(form_data: dict) -> dict:
    """Sanitize form_data to remove non-serializable objects like NiceGUI Input or event objects"""
    sanitized = {}

    for key, value in form_data.items():
        # Handle None values
        if value is None:
            sanitized[key] = None
        # Handle strings - ensure they're actually strings
        elif isinstance(value, str):
            sanitized[key] = value
        # Handle booleans and numbers
        elif isinstance(value, (bool, int, float)):
            sanitized[key] = value
        # Handle lists
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_form_data({"item": item})["item"]
                if isinstance(item, dict)
                else (
                    str(item)
                    if not isinstance(item, (str, bool, int, float, type(None)))
                    else item
                )
                for item in value
            ]
        # Handle dictionaries
        elif isinstance(value, dict):
            sanitized[key] = sanitize_form_data(value)
        # Handle non-serializable objects (NiceGUI Input, event objects, etc.)
        else:
            # Check if it's a NiceGUI UI element or event object
            # Try to get .value attribute (NiceGUI Input/Textarea objects and event objects)
            if hasattr(value, "value"):
                val = value.value
                # Recursively sanitize the value in case it's also non-serializable
                if isinstance(val, (str, bool, int, float, type(None))):
                    sanitized[key] = val
                elif isinstance(val, dict):
                    sanitized[key] = sanitize_form_data(val)
                elif isinstance(val, list):
                    sanitized[key] = [
                        sanitize_form_data({"item": item})["item"]
                        if isinstance(item, dict)
                        else (
                            str(item)
                            if not isinstance(item, (str, bool, int, float, type(None)))
                            else item
                        )
                        for item in val
                    ]
                else:
                    sanitized[key] = str(val) if val is not None else ""
            # Check for common event object attributes
            elif hasattr(value, "__class__") and "Event" in str(type(value)):
                # It's an event object, try to extract meaningful data
                if hasattr(value, "args") and value.args:
                    sanitized[key] = (
                        sanitize_form_data({"item": value.args[0]})["item"]
                        if value.args
                        else ""
                    )
                else:
                    sanitized[key] = ""
            # Try to convert to string as fallback
            else:
                try:
                    sanitized[key] = str(value)
                except Exception:
                    sanitized[key] = ""

    return sanitized


def save_chatbot_item(form_data: dict):
    """Save the chatbot item (create new or update existing)"""
    try:
        # Sanitize form_data to remove non-serializable objects
        form_data = sanitize_form_data(form_data)

        # Validate form data
        if not form_data.get("name"):
            notify("Name is required", type="negative")
            return

        if not form_data.get("response_text"):
            notify("Response text is required", type="negative")
            return

        if form_data["item_type"] == "command":
            if not form_data.get("command_name"):
                notify("Command name is required", type="negative")
                return

            # Check for reserved command names
            reserved_commands = ["quote", "add_quote"]
            command_name_lower = form_data.get("command_name", "").lower().strip()
            if command_name_lower in reserved_commands:
                notify(
                    f"Command name '{command_name_lower}' is reserved and cannot be used",
                    type="negative",
                )
                return

            # Check aliases for reserved names
            aliases = form_data.get("aliases", [])
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_lower = alias.lower().strip()
                    if alias_lower in reserved_commands:
                        notify(
                            f"Alias '{alias_lower}' is reserved and cannot be used",
                            type="negative",
                        )
                        return
        else:
            if not form_data.get("event_type"):
                notify("Event type is required", type="negative")
                return

        # Create the item
        if form_data["item_type"] == "command":
            save_chatbot_command(form_data)
        else:
            save_chatbot_event(form_data)

    except Exception as e:
        logger.error(f"Error saving chatbot item: {e}", exc_info=True)
        notify(f"Error saving item: {str(e)}", type="negative")


def save_chatbot_command(form_data: dict):
    """Save a chatbot command"""
    try:
        is_edit = form_data.get("item_id") is not None

        # Create command object
        command_id = form_data.get("item_id") or str(uuid.uuid4())

        # Get existing command for edits to preserve runtime state
        existing_command = None
        if is_edit:
            from modules.chatbot_manager import get_manager

            manager = get_manager()
            existing_command = manager.get_command(command_id)

        command = ChatCommand(
            command_id=command_id,
            name=form_data["name"],
            description=form_data.get("description", ""),
            command_name=form_data["command_name"],
            aliases=form_data.get("aliases", []),
            response_text=(
                form_data["response_text"]
                if isinstance(form_data.get("response_text"), str)
                else str(form_data.get("response_text", ""))
            ),
            mod_only=form_data.get("mod_only", False),
            cooldown=form_data.get("cooldown", 0),
            command_type=CommandType(form_data.get("command_type", "basic")),
            usage_limit=form_data.get("usage_limit", 0),
            repeating_enabled=form_data.get("repeating_enabled", False),
            repeat_count=form_data.get("repeat_count", 1),
            repeat_interval=form_data.get("repeat_interval", 0),
            persistent_counter=form_data.get("persistent_counter", False),
            reset_command=form_data.get("reset_command", ""),
            enabled=form_data.get("enabled", True),
            # API-related fields
            api_enabled=form_data.get("api_enabled", False),
            api_endpoint=form_data.get("api_endpoint", ""),
            api_method=form_data.get("api_method", "GET"),
            api_headers=form_data.get("api_headers", {}),
            api_body=form_data.get("api_body", ""),
            api_response_format=form_data.get("api_response_format", ""),
            api_parameters=form_data.get("api_parameters", {}),
            api_endpoint_select=form_data.get("api_endpoint_select", ""),
            api_variable_processing=form_data.get("api_variable_processing", []),
            argument_mappings=form_data.get("argument_mappings", {}),
            # Preserve runtime state from existing command
            trigger_count=getattr(existing_command, "trigger_count", 0)
            if existing_command
            else 0,
            usage_count=getattr(existing_command, "usage_count", 0)
            if existing_command
            else 0,
            counter_value=getattr(existing_command, "counter_value", 0)
            if existing_command
            else 0,
            last_used=getattr(existing_command, "last_used", 0)
            if existing_command
            else 0,
            last_triggered=getattr(existing_command, "last_triggered", 0)
            if existing_command
            else 0,
        )

        # Save command
        manager = get_chatbot_manager()
        if is_edit:
            success = manager.update_command(command_id, command)
            action = "updated"
        else:
            success = manager.add_command(command)
            action = "created"

        if success:
            notify(f"Command '{command.name}' {action} successfully", type="positive")
            close_dialog_and_refresh()
        else:
            notify(f"Failed to {action} command", type="negative")

    except Exception as e:
        logger.error(f"Error saving command: {e}", exc_info=True)
        notify(f"Error saving command: {str(e)}", type="negative")


def save_chatbot_event(form_data: dict):
    """Save a chatbot event"""
    try:
        is_edit = form_data.get("item_id") is not None

        # Create event object
        event_id = form_data.get("item_id") or str(uuid.uuid4())

        # Get existing event for edits to preserve runtime state
        existing_event = None
        if is_edit:
            from modules.chatbot_manager import get_manager

            manager = get_manager()
            existing_event = manager.get_event(event_id)

        event_type_value = form_data.get("event_type", "follow")

        # Handle interval conversion from hh:mm:ss to seconds
        interval_str = form_data.get("interval", "")
        interval_seconds = 0
        if interval_str:
            temp_event = (
                ChatEvent()
            )  # Create temporary instance to use conversion method
            if not temp_event.set_interval_from_string(interval_str):
                notify(
                    "Invalid interval format. Please use hh:mm:ss format (e.g., 01:30:00)",
                    type="negative",
                )
                return
            interval_seconds = temp_event.interval

        # Validate Interval events require an interval
        if event_type_value == "interval" and interval_seconds <= 0:
            notify(
                "Interval events require an interval to be set. Please specify a time interval (hh:mm:ss).",
                type="negative",
            )
            return

        # Validate Specific Time events require a time
        if event_type_value == "specific_time" and not form_data.get("specific_time"):
            notify(
                "Specific Time events require a time to be set. Please specify a time in HH:MM format.",
                type="negative",
            )
            return

        event = ChatEvent(
            event_id=event_id,
            name=form_data.get("name", ""),
            description=form_data.get("description", ""),
            event_type=EventType(event_type_value)
            if event_type_value
            else EventType.FOLLOW,
            response_text=(
                form_data.get("response_text", "")
                if isinstance(form_data.get("response_text"), str)
                else str(form_data.get("response_text", ""))
            ),
            enabled=form_data.get("enabled", True),
            interval=interval_seconds,
            # Preserve runtime state from existing event
            trigger_count=getattr(existing_event, "trigger_count", 0)
            if existing_event
            else 0,
            last_triggered=getattr(existing_event, "last_triggered", 0)
            if existing_event
            else 0,
            # Event-specific settings
            specific_time=form_data.get("specific_time", ""),
            chat_message_text=form_data.get("chat_message_text", ""),
            chat_message_match_type=form_data.get("chat_message_match_type", "exact"),
            bits_quantity=int(form_data.get("bits_quantity", 0))
            if form_data.get("bits_quantity")
            else 0,
            hype_train_level=int(form_data.get("hype_train_level", 0))
            if form_data.get("hype_train_level")
            else 0,
            hype_train_end_level=int(form_data.get("hype_train_end_level", 0))
            if form_data.get("hype_train_end_level")
            else 0,
            gift_sub_quantity=int(form_data.get("gift_sub_quantity", 0))
            if form_data.get("gift_sub_quantity")
            else 0,
            gift_sub_tier=int(form_data.get("gift_sub_tier", 0))
            if form_data.get("gift_sub_tier")
            else 0,
            resub_months=int(form_data.get("resub_months", 0))
            if form_data.get("resub_months")
            else 0,
            resub_tier=int(form_data.get("resub_tier", 0))
            if form_data.get("resub_tier")
            else 0,
            sub_tier=int(form_data.get("sub_tier", 0))
            if form_data.get("sub_tier")
            else 0,
            donation_amount=float(form_data.get("donation_amount", 0.0))
            if form_data.get("donation_amount")
            else 0.0,
            raid_viewer_count=int(form_data.get("raid_viewer_count", 0))
            if form_data.get("raid_viewer_count")
            else 0,
            raid_raider_name=form_data.get("raid_raider_name", ""),
            channel_point_reward_name=form_data.get("channel_point_reward_name", ""),
            # API-related fields
            api_enabled=form_data.get("api_enabled", False),
            api_endpoint=form_data.get("api_endpoint", ""),
            api_method=form_data.get("api_method", "GET"),
            api_headers=form_data.get("api_headers", {}),
            api_body=form_data.get("api_body", ""),
            api_response_format=form_data.get("api_response_format", ""),
            api_parameters=form_data.get("api_parameters", {}),
            api_endpoint_select=form_data.get("api_endpoint_select", ""),
            api_variable_processing=form_data.get("api_variable_processing", []),
        )

        # Save event
        manager = get_chatbot_manager()
        if is_edit:
            success = manager.update_event(event_id, event)
            action = "updated"
        else:
            success = manager.add_event(event)
            action = "created"

        if success:
            notify(f"Event '{event.name}' {action} successfully", type="positive")
            close_dialog_and_refresh()
        else:
            notify(f"Failed to {action} event", type="negative")

    except Exception as e:
        logger.error(f"Error saving event: {e}", exc_info=True)
        notify(f"Error saving event: {str(e)}", type="negative")


def close_dialog_and_refresh():
    """Close the create dialog and refresh the items list"""
    global create_dialog
    try:
        if create_dialog:
            create_dialog.close()
        refresh_chatbot_items()
    except Exception as e:
        logger.error(f"Error closing dialog and refreshing: {e}")


def show_edit_chatbot_dialog(item_id: str, item_type: str):
    """Show the edit chatbot item dialog"""
    show_chatbot_dialog(item_id, item_type)


def show_edit_quote_dialog(quote_id: str):
    """Show the edit quote dialog"""
    show_chatbot_dialog(quote_id, "quote")


def test_chatbot_item(item_id: str, item_type: str):
    """Test a chatbot item with sample data"""
    try:
        manager = get_chatbot_manager()

        # Create sample test data based on type
        if item_type == "command":
            test_data = {
                "username": "TestUser",
                "message": f"!{item_type}",
                "timestamp": time.time(),
            }
        else:
            test_data = create_test_data_for_event(item_type)

        # Test the item using a thread to avoid event loop conflicts with NiceGUI
        try:
            import threading

            result_container = {}

            def run_async_test():
                """Run the async test in a separate thread with its own event loop"""
                try:
                    import asyncio as async_module

                    # Create a new event loop for this thread
                    new_loop = async_module.new_event_loop()
                    async_module.set_event_loop(new_loop)

                    # Run the async test
                    async def async_test():
                        if item_type == "command":
                            return await manager.test_command(item_id, test_data)
                        else:
                            return await manager.test_event(item_id, test_data)

                    result = new_loop.run_until_complete(async_test())
                    result_container["result"] = result
                    new_loop.close()
                except Exception as e:
                    logger.error(f"Error in async test thread: {e}", exc_info=True)
                    result_container["error"] = str(e)

            # Run the test in a separate thread
            test_thread = threading.Thread(target=run_async_test)
            test_thread.daemon = True
            test_thread.start()
            test_thread.join()

            # Check for errors
            if "error" in result_container:
                notify(f"Test error: {result_container['error']}", type="negative")
                return

            result = result_container.get("result", {})

            if result.get("success"):
                notify(
                    f"Test successful: {result.get('message', 'Item triggered')}",
                    type="positive",
                )
            else:
                notify(
                    f"Test failed: {result.get('error', 'Unknown error')}",
                    type="negative",
                )
        except Exception as test_error:
            logger.error(f"Error during test execution: {test_error}", exc_info=True)
            notify(f"Test error: {str(test_error)}", type="negative")

    except Exception as e:
        logger.error(f"Error testing chatbot item: {e}", exc_info=True)
        notify(f"Error testing item: {str(e)}", type="negative")


def create_test_data_for_event(event_type: str) -> Dict[str, Any]:
    """Create sample test data for an event type"""
    from datetime import datetime

    base_data = {"timestamp": time.time(), "username": "TestUser", "source": "test"}

    test_data_map = {
        "follow": base_data,
        "subscription": {**base_data, "tier": 1, "message": "Test sub!"},
        "resubscription": {**base_data, "tier": 2, "months": 5, "message": "Resub!"},
        "gift_subscription": {**base_data, "tier": 1, "recipient": "RecipientUser"},
        "bits": {**base_data, "amount": 100, "message": "Test bits!"},
        "donation": {
            **base_data,
            "amount": 5.0,
            "currency": "USD",
            "message": "Test donation!",
        },
        "raid": {**base_data, "viewer_count": 50, "raider_name": "TestRaider"},
        "hype_train_start": {**base_data, "level": 1},
        "hype_train_end": {**base_data, "level": 5, "total": 1000},
        "hype_train_progress": {**base_data, "level": 3, "progress": 75},
        "channel_point_redemption": {
            **base_data,
            "reward": "Test Reward",
            "cost": 100,
            "reward_name": "Test Reward",
            "reward_title": "Test Reward",
        },
        "interval": base_data,
        "specific_time": {**base_data, "time": datetime.now().strftime("%H:%M")},
        "chat_message": {**base_data, "message": "Hello! This is a test message"},
    }

    return test_data_map.get(event_type, base_data)


def toggle_chatbot_item(item_id: str, enabled: bool):
    """Toggle a chatbot item's enabled state"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_item(item_id, enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Item {status}", type="positive")
            refresh_chatbot_items()
        else:
            notify("Failed to toggle item", type="negative")
    except Exception as e:
        logger.error(f"Error toggling chatbot item: {e}", exc_info=True)
        notify(f"Error toggling item: {str(e)}", type="negative")


def reset_command_counter(command_id: str):
    """Reset a command's counter"""
    try:
        manager = get_chatbot_manager()
        success = manager.reset_command_counter(command_id)

        if success:
            notify("Command counter reset", type="positive")
            refresh_chatbot_items()
        else:
            notify("Failed to reset counter", type="negative")
    except Exception as e:
        logger.error(f"Error resetting command counter: {e}", exc_info=True)
        notify(f"Error resetting counter: {str(e)}", type="negative")


def delete_chatbot_item(item_id: str, item_type: str):
    """Delete a chatbot item with confirmation"""

    def confirm_delete():
        try:
            manager = get_chatbot_manager()
            if item_type == "command":
                success = manager.remove_command(item_id)
            elif item_type == "event":
                success = manager.remove_event(item_id)
            else:  # quote
                success = manager.delete_quote(item_id)

            if success:
                notify(f"{item_type.title()} deleted", type="positive")
                refresh_chatbot_items()
            else:
                notify(f"Failed to delete {item_type}", type="negative")
        except Exception as e:
            logger.error(f"Error deleting chatbot item: {e}", exc_info=True)
            notify(f"Error deleting item: {str(e)}", type="negative")

    # Show confirmation dialog
    with ui.dialog().props("persistent") as dialog:
        with ui.card():
            ui.label("Confirm Delete").classes("text-lg font-semibold mb-2")
            ui.label(
                f"Are you sure you want to delete this {item_type}? This action cannot be undone."
            ).classes("mb-4")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete",
                    on_click=lambda: [
                        confirm_delete(),
                        dialog.close(),
                    ],
                ).classes("btn-danger")

    dialog.open()


def delete_quote_item(quote_id: str):
    """Delete a quote item with confirmation"""
    delete_chatbot_item(quote_id, "quote")


def refresh_chatbot_items():
    """Refresh all chatbot tabs"""
    refresh_tab_content("commands")
    refresh_tab_content("events")
    refresh_tab_content("quotes")
    refresh_tab_content("greetings")
    refresh_tab_content("giveaways")


# Greeting-specific functions
def show_create_greeting_dialog():
    """Show the create greeting dialog"""
    global create_dialog

    if create_dialog:
        create_dialog.close()
        create_dialog = None

    # Create the dialog
    create_dialog = ui.dialog().props("persistent")

    with create_dialog:
        with ui.card().classes("w-[500px]"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Add New Greeting").classes(
                        "text-lg font-semibold text-cyan-400"
                    )
                    ui.button(icon="close", on_click=create_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-4 gap-4"):
                    # Username input
                    username_input = form_input(
        tooltip="Username (required)",
                        label="Username (required)",
                        placeholder="e.g., mycostreamer",
                        value="",
                    ).classes("w-full")

                    # Greeting text input
                    greeting_text_input = ui.textarea(
                        label="Greeting Text",
                        placeholder="Enter custom greeting message...",
                        value="",
                    ).classes("w-full")

                    # Enabled toggle
                    enabled_toggle = ui.switch(text="Enabled", value=True).classes(
                        "w-full"
                    )

                    # Form buttons
                    with ui.row().classes("w-full items-center justify-end gap-2 mt-4"):
                        ui.button(text="Cancel", on_click=create_dialog.close).props(
                            "flat"
                        ).classes("secondary-text")

                        ui.button(
                            icon="save",
                            text="Add Greeting",
                            on_click=lambda: handle_greeting_save(
                                username_input.value,
                                greeting_text_input.value,
                                enabled_toggle.value,
                            ),
                        ).classes("control-button btn-secondary px-4 py-2")

    create_dialog.open()


def handle_greeting_save(username: str, greeting_text: str, enabled: bool):
    """Handle greeting save synchronously"""
    try:
        # Validate inputs first
        if not username.strip():
            notify("Username is required", type="negative")
            return

        if not greeting_text.strip():
            notify("Greeting text is required", type="negative")
            return

        # Show loading state
        notify("Looking up Twitch user...", type="info")

        # Make the API call synchronously
        result = save_new_greeting_sync(username, greeting_text, enabled)

        if result and result.get("success"):
            notify(
                f"Greeting for @{result['username']} added successfully!",
                type="positive",
            )
            if create_dialog:
                create_dialog.close()
            refresh_chatbot_items()
        elif result:
            notify(f"Error: {result.get('error', 'Unknown error')}", type="negative")
        else:
            notify("Error: Failed to save greeting", type="negative")

    except Exception as e:
        logger.error(f"Error saving greeting: {e}", exc_info=True)
        notify(f"Error: {str(e)}", type="negative")


def save_new_greeting_sync(username: str, greeting_text: str, enabled: bool) -> dict:
    """Save a new greeting by looking up the user from Twitch API"""
    try:
        # Use asyncio.run to handle the async call
        try:
            # Try to get the current event loop
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, we need to handle this differently
                # Let's use a different approach - create a new event loop
                import concurrent.futures
                import threading

                result_container = {}

                def run_async():
                    try:
                        import asyncio as async_module

                        new_loop = async_module.new_event_loop()
                        async_module.set_event_loop(new_loop)

                        # Create a new task and run it
                        async def async_lookup():
                            return await lookup_twitch_user(username.strip())

                        result = new_loop.run_until_complete(async_lookup())
                        result_container["result"] = result
                        new_loop.close()
                    except Exception as e:
                        result_container["error"] = str(e)

                thread = threading.Thread(target=run_async)
                thread.start()
                thread.join()

                if "error" in result_container:
                    return {"success": False, "error": result_container["error"]}

                user_id, display_name = result_container["result"]
            else:
                # Loop is not running, we can use asyncio.run
                import asyncio

                user_id, display_name = asyncio.run(
                    lookup_twitch_user(username.strip())
                )

        except Exception as e:
            return {"success": False, "error": f"Failed to lookup user: {str(e)}"}

        # Use the display name from Twitch API if it's different
        actual_username = display_name if display_name else username.strip()

        # Save the greeting
        manager = get_chatbot_manager()
        success, error, greeting_id = manager.add_greeting(
            user_id, actual_username, greeting_text.strip(), enabled
        )

        if success:
            return {"success": True, "username": actual_username}
        else:
            return {"success": False, "error": error}

    except Exception as e:
        logger.error(f"Error saving greeting: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def show_edit_greeting_dialog(greeting_id: str):
    """Show the edit greeting dialog"""
    global edit_dialog

    if edit_dialog:
        edit_dialog.close()
        edit_dialog = None

    # Get existing greeting
    manager = get_chatbot_manager()
    greeting = manager.get_greeting(greeting_id)

    if not greeting:
        notify("Greeting not found", type="negative")
        return

    # Create the dialog
    edit_dialog = ui.dialog().props("persistent")

    with edit_dialog:
        with ui.card().classes("w-[500px]"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Edit Greeting").classes(
                        "text-lg font-semibold text-cyan-400"
                    )
                    ui.button(icon="close", on_click=lambda: close_edit_dialog()).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-4 gap-4"):
                    # User ID (read-only)
                    ui.input(label="User ID", value=greeting.user_id).classes(
                        "w-full"
                    ).props("readonly")

                    # Username (read-only)
                    ui.input(label="Username", value=greeting.username).classes(
                        "w-full"
                    ).props("readonly")

                    # Greeting text input
                    greeting_text_input = ui.textarea(
                        label="Greeting Text",
                        placeholder="Enter custom greeting message...",
                        value=greeting.greeting_text,
                    ).classes("w-full")

                    # Enabled toggle
                    enabled_toggle = ui.switch(
                        text="Enabled", value=greeting.enabled
                    ).classes("w-full")

                    # Form buttons
                    with ui.row().classes("w-full items-center justify-end gap-2 mt-4"):
                        ui.button(text="Cancel", on_click=edit_dialog.close).props(
                            "flat"
                        ).classes("secondary-text")

                        ui.button(
                            icon="save",
                            text="Update Greeting",
                            on_click=lambda: update_greeting(
                                greeting_id,
                                greeting_text_input.value,
                                enabled_toggle.value,
                            ),
                        ).classes("control-button btn-secondary px-4 py-2")

    edit_dialog.open()


def update_greeting(greeting_id: str, greeting_text: str, enabled: bool):
    """Update an existing greeting"""
    try:
        manager = get_chatbot_manager()
        success = manager.update_greeting(greeting_id, greeting_text, enabled)

        if success:
            notify("Greeting updated successfully!", type="positive")
            close_edit_dialog()
            refresh_chatbot_items()
        else:
            notify("Failed to update greeting", type="negative")

    except Exception as e:
        logger.error(f"Error updating greeting: {e}", exc_info=True)
        notify(f"Error updating greeting: {str(e)}", type="negative")


def toggle_greeting(greeting_id: str, enabled: bool):
    """Toggle a greeting's enabled state"""
    try:
        manager = get_chatbot_manager()
        success = manager.update_greeting(greeting_id, enabled=enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Greeting {status}", type="positive")
            refresh_chatbot_items()
        else:
            notify("Failed to toggle greeting", type="negative")
    except Exception as e:
        logger.error(f"Error toggling greeting: {e}", exc_info=True)
        notify(f"Error toggling greeting: {str(e)}", type="negative")


def delete_greeting(greeting_id: str):
    """Delete a greeting with confirmation"""

    def confirm_delete():
        try:
            manager = get_chatbot_manager()
            success = manager.remove_greeting(greeting_id)

            if success:
                notify("Greeting deleted", type="positive")
                refresh_chatbot_items()
            else:
                notify("Failed to delete greeting", type="negative")
        except Exception as e:
            logger.error(f"Error deleting greeting: {e}", exc_info=True)
            notify(f"Error deleting greeting: {str(e)}", type="negative")

    # Show confirmation dialog
    with ui.dialog().props("persistent") as dialog:
        with ui.card():
            ui.label("Confirm Delete").classes("text-lg font-semibold mb-2")
            ui.label(
                "Are you sure you want to delete this greeting? This action cannot be undone."
            ).classes("mb-4")

            with ui.row().classes("w-full items-center justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete",
                    on_click=lambda: [
                        confirm_delete(),
                        dialog.close(),
                    ],
                ).classes("btn-danger")

    dialog.open()


async def lookup_twitch_user(username: str) -> tuple[str, str]:
    """Lookup Twitch user by username and return (user_id, display_name) or raise exception"""
    try:
        from ..twitch import get_twitch_api

        twitch_api = get_twitch_api()
        if not twitch_api or not twitch_api.is_connected:
            raise Exception(
                "Twitch API not connected. Please authenticate with Twitch first."
            )

        # Use the generic API call method to lookup user by login name
        url = "https://api.twitch.tv/helix/users"
        params = {"login": username.lower()}

        response_data = await twitch_api.generic_api_call(url, "GET", params=params)

        if "data" in response_data and len(response_data["data"]) > 0:
            user_data = response_data["data"][0]
            return user_data["id"], user_data["display_name"]
        else:
            raise Exception(f"Twitch user '{username}' not found")

    except Exception as e:
        logger.error(f"Error looking up Twitch user '{username}': {e}")
        raise


def get_default_greeting():
    """Get the default greeting text"""
    try:
        manager = get_chatbot_manager()
        return manager.get_default_greeting()
    except Exception as e:
        logger.error(f"Error getting default greeting: {e}")
        return "@{username} welcome to the stream!"


def close_edit_dialog():
    """Close the edit dialog and reset the global variable"""
    global edit_dialog
    if edit_dialog:
        edit_dialog.close()
        edit_dialog = None


def show_greeting_settings_dialog():
    """Show the greeting settings dialog"""
    # Get current settings
    manager = get_chatbot_manager()
    default_greeting = manager.get_default_greeting()
    cooldown_hours = manager.get_greeting_cooldown()
    reset_interval_hours = manager.get_greeting_reset_interval()
    default_greeting_enabled = manager.get_default_greeting_enabled()
    custom_greeting_enabled = manager.get_custom_greeting_enabled()

    # Create the dialog with local variable
    with ui.dialog().props("persistent") as greeting_settings_dialog:
        with ui.card().classes("w-[600px]"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Greeting Settings").classes(
                        "text-lg font-semibold text-cyan-400"
                    )
                    ui.button(
                        icon="close", on_click=greeting_settings_dialog.close
                    ).props("flat round").classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-4 gap-4"):
                    # Greeting toggles
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Greeting Types").classes("text-sm font-medium")
                        with ui.row().classes("gap-4"):
                            # Default greeting toggle
                            with ui.column().classes("items-center gap-1"):

                                def handle_default_toggle(e):
                                    toggle_default_greeting(e.value)

                                ui.switch(
                                    value=default_greeting_enabled,
                                    on_change=handle_default_toggle,
                                ).classes("scale-75")
                                ui.label("Default").classes("text-xs text-cyan-300")

                            # Custom greeting toggle
                            with ui.column().classes("items-center gap-1"):

                                def handle_custom_toggle(e):
                                    toggle_custom_greeting(e.value)

                                ui.switch(
                                    value=custom_greeting_enabled,
                                    on_change=handle_custom_toggle,
                                ).classes("scale-75")
                                ui.label("Custom").classes("text-xs text-cyan-300")

                    # Default greeting text
                    default_greeting_input = ui.textarea(
                        label="Default Greeting Text",
                        placeholder="Enter default greeting for users without custom greetings...",
                        value=default_greeting,
                    ).classes("w-full")

                    # Reset interval hours (new system)
                    reset_interval_input = form_input(
        tooltip="Greeting Reset Interval (hours)",
                        label="Greeting Reset Interval (hours)",
                        placeholder="24",
                        value=str(reset_interval_hours),
                    ).classes("w-full")

                    # Help text
                    with ui.element("div").classes("variable-help-text"):
                        ui.label("💡 Variables you can use in greetings:").classes(
                            "text-xs font-medium text-cyan-400 mb-1"
                        )
                        ui.label("• {username} - The user's display name").classes(
                            "text-xs secondary-text"
                        )
                        ui.label("• {user_id} - The user's unique ID").classes(
                            "text-xs secondary-text"
                        )

                    # Form buttons
                    with ui.row().classes("w-full items-center justify-end gap-2 mt-4"):
                        ui.button(
                            text="Cancel", on_click=greeting_settings_dialog.close
                        ).props("flat").classes("secondary-text")

                        ui.button(
                            icon="save",
                            text="Save Settings",
                            on_click=lambda: save_greeting_settings_and_close(
                                default_greeting_input.value,
                                reset_interval_input.value,
                                greeting_settings_dialog,
                            ),
                        ).classes("control-button btn-secondary px-4 py-2")

    greeting_settings_dialog.open()


def toggle_default_greeting(enabled: bool):
    """Toggle default greetings on/off"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_default_greeting_enabled(enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Default greetings {status}", type="positive")
        else:
            notify("Failed to toggle default greetings", type="negative")

    except Exception as e:
        logger.error(f"Error toggling default greetings: {e}", exc_info=True)
        notify(f"Error toggling default greetings: {str(e)}", type="negative")


def toggle_custom_greeting(enabled: bool):
    """Toggle custom greetings on/off"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_custom_greeting_enabled(enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Custom greetings {status}", type="positive")
        else:
            notify("Failed to toggle custom greetings", type="negative")

    except Exception as e:
        logger.error(f"Error toggling custom greetings: {e}", exc_info=True)
        notify(f"Error toggling custom greetings: {str(e)}", type="negative")


def save_greeting_settings_and_close(
    default_greeting: str, reset_interval_str: str, dialog
):
    """Save greeting settings and close the dialog"""
    try:
        if not default_greeting.strip():
            notify("Default greeting text is required", type="negative")
            return

        # Parse reset interval hours
        try:
            reset_interval_hours = (
                int(reset_interval_str)
                if reset_interval_str and reset_interval_str.strip()
                else 24
            )
            if reset_interval_hours < 1:
                reset_interval_hours = 24
        except ValueError:
            reset_interval_hours = 24

        manager = get_chatbot_manager()
        success1 = manager.update_default_greeting(default_greeting.strip())
        success2 = manager.set_greeting_reset_interval(reset_interval_hours)

        if success1 and success2:
            notify("Greeting settings updated successfully!", type="positive")
            dialog.close()
            refresh_chatbot_items()
        else:
            notify("Failed to update greeting settings", type="negative")

    except Exception as e:
        logger.error(f"Error saving greeting settings: {e}", exc_info=True)
        notify(f"Error saving greeting settings: {str(e)}", type="negative")


def show_quote_settings_dialog():
    """Show the quote settings dialog"""
    global edit_dialog

    if edit_dialog:
        edit_dialog.close()
        edit_dialog = None

    # Get current settings
    manager = get_chatbot_manager()
    quotes_enabled = manager.get_quotes_enabled()
    quote_cooldown = manager.get_quote_cooldown()

    # Create the dialog
    edit_dialog = ui.dialog().props("persistent")

    with edit_dialog:
        with ui.card().classes("w-[500px]"):
            with ui.column().classes("w-full"):
                # Dialog header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Quote Settings").classes(
                        "text-lg font-semibold text-cyan-400"
                    )
                    ui.button(icon="close", on_click=lambda: close_edit_dialog()).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.column().classes("p-4 gap-4"):
                    # Quotes enabled toggle
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Enable Quote System").classes("text-sm font-medium")
                        ui.switch(
                            value=quotes_enabled,
                            on_change=lambda e: toggle_quote_system(e.value),
                        ).classes("scale-75")

                    # Quote cooldown input
                    quote_cooldown_input = form_input(
        tooltip="Global Quote Cooldown (seconds)",
                        label="Global Quote Cooldown (seconds)",
                        placeholder="30",
                        value=str(quote_cooldown),
                    ).classes("w-full")

                    # Help text
                    with ui.element("div").classes("variable-help-text"):
                        ui.label("💡 Quote Cooldown").classes(
                            "text-xs font-medium text-cyan-400 mb-1"
                        )
                        ui.label("• Set to 0 to disable cooldown").classes(
                            "text-xs secondary-text"
                        )
                        ui.label("• Applies to all quote commands (!quote)").classes(
                            "text-xs secondary-text"
                        )

                    # Form buttons
                    with ui.row().classes("w-full items-center justify-end gap-2 mt-4"):
                        ui.button(text="Cancel", on_click=edit_dialog.close).props(
                            "flat"
                        ).classes("secondary-text")

                        ui.button(
                            icon="save",
                            text="Save Settings",
                            on_click=lambda: save_quote_settings(
                                quote_cooldown_input.value,
                            ),
                        ).classes("control-button btn-secondary px-4 py-2")

    edit_dialog.open()


def save_quote_settings(quote_cooldown_str: str):
    """Save quote settings"""
    try:
        # Parse quote cooldown seconds
        try:
            quote_cooldown_seconds = (
                int(quote_cooldown_str)
                if quote_cooldown_str and quote_cooldown_str.strip()
                else 30
            )
            if quote_cooldown_seconds < 0:
                quote_cooldown_seconds = 0
        except ValueError:
            quote_cooldown_seconds = 30

        manager = get_chatbot_manager()
        success = manager.set_quote_cooldown(quote_cooldown_seconds)

        if success:
            notify("Quote settings updated successfully!", type="positive")
            close_edit_dialog()
            refresh_chatbot_items()
        else:
            notify("Failed to update quote settings", type="negative")

    except Exception as e:
        logger.error(f"Error saving quote settings: {e}", exc_info=True)
        notify(f"Error saving quote settings: {str(e)}", type="negative")

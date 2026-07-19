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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class ComparisonOperator(Enum):
    """Comparison operators for trigger conditions"""

    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"  # First word (exact token match)
    BEGINS_WITH = "begins_with"  # Prefix match (legacy starts_with behavior)
    ENDS_WITH = "ends_with"
    REGEX_MATCH = "regex_match"


class TriggerType(Enum):
    """Types of triggers available in the connector system"""

    # Twitch Event Triggers
    TWITCH_BITS = "twitch_bits"
    TWITCH_SUB = "twitch_sub"
    TWITCH_RESUB = "twitch_resub"
    TWITCH_GIFTSUB = "twitch_giftsub"
    TWITCH_FOLLOW = "twitch_follow"
    TWITCH_RAID = "twitch_raid"
    TWITCH_POINTS = "twitch_points"
    TWITCH_CHAT_MESSAGE = "twitch_chat_message"
    TWITCH_HYPE_TRAIN_START = "twitch_hype_train_start"
    TWITCH_HYPE_TRAIN_END = "twitch_hype_train_end"

    # Donation / tip triggers (e.g. connector webhooks)
    DONATION = "donation"

    # Timer/Schedule Triggers
    TIMER = "timer"
    SCHEDULE = "schedule"

    # Manual/Custom Triggers
    HOTKEY = "hotkey"
    STREAMDECK = "streamdeck"
    WEBHOOK = "webhook"

    # OBS Studio (WebSocket) triggers
    OBS_SCENE_CHANGED = "obs_scene_changed"
    OBS_STREAM_STATE = "obs_stream_state"
    OBS_RECORD_STATE = "obs_record_state"
    OBS_INPUT_MUTE = "obs_input_mute"

    # Matches any connector-processed event type
    ANY = "any"


class ActionType(Enum):
    """Types of actions available in the connector system"""

    # Template Control Actions
    TEMPLATE_CONTROL = "template_control"

    # WebSocket Actions
    WEBSOCKET_EMIT = "websocket_emit"

    # Alert System Actions
    TRIGGER_ALERT = "trigger_alert"

    # Chat Actions
    SEND_CHAT_MESSAGE = "send_chat_message"
    SEND_ANNOUNCEMENT = "send_announcement"

    # Greeting Actions
    ADD_GREETING = "add_greeting"
    UPDATE_GREETING = "update_greeting"
    SEND_GREETING = "send_greeting"

    # API Actions
    API_CALL = "api_call"

    # File/System Actions
    WRITE_FILE = "write_file"
    EXECUTE_COMMAND = "execute_command"

    # Input Actions
    KEY_PRESS = "key_press"
    AUDIO_CONTROL = "audio_control"

    # Game memory (crowd control)
    GAME_HOOK = "game_hook"

    # OBS Studio WebSocket controls
    OBS_CONTROL = "obs_control"


@dataclass
class TriggerCondition:
    """A single condition that must be met for a trigger to fire"""

    field: str  # The field to check (e.g., "amount", "username", "message")
    operator: ComparisonOperator
    value: Any  # The value to compare against
    case_sensitive: bool = True

    def evaluate(self, event_data: Dict[str, Any]) -> bool:
        """Evaluate this condition against event data"""
        try:
            field_value = self._get_field_value(event_data, self.field)
            return self._compare_values(field_value, self.value, self.operator)
        except Exception as e:
            logger.error(
                f"Error evaluating condition {self.field} {self.operator.value} {self.value}: {e}"
            )
            return False

    def _get_field_value(self, data: Dict[str, Any], field_path: str) -> Any:
        """Get value from nested data using dot notation (e.g., 'user.name')"""
        keys = field_path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def _compare_values(
        self, field_value: Any, expected_value: Any, operator: ComparisonOperator
    ) -> bool:
        """Compare two values using the specified operator"""
        # Handle None values
        if field_value is None:
            return operator == ComparisonOperator.EQUAL and expected_value is None

        # Convert to appropriate types for comparison
        if operator in [
            ComparisonOperator.GREATER_THAN,
            ComparisonOperator.GREATER_THAN_OR_EQUAL,
            ComparisonOperator.LESS_THAN,
            ComparisonOperator.LESS_THAN_OR_EQUAL,
        ]:
            try:
                field_value = float(field_value) if field_value is not None else 0
                expected_value = float(expected_value)
            except (ValueError, TypeError):
                return False

        # String operations
        if operator in [
            ComparisonOperator.CONTAINS,
            ComparisonOperator.NOT_CONTAINS,
            ComparisonOperator.STARTS_WITH,
            ComparisonOperator.BEGINS_WITH,
            ComparisonOperator.ENDS_WITH,
            ComparisonOperator.REGEX_MATCH,
        ]:
            field_value = str(field_value) if field_value is not None else ""
            expected_value = str(expected_value)

            if not self.case_sensitive:
                field_value = field_value.lower()
                expected_value = expected_value.lower()

        def _looks_bool_token(x):
            return isinstance(x, str) and str(x).strip().lower() in (
                "true",
                "false",
                "0",
                "1",
                "yes",
                "no",
                "on",
                "off",
            )

        def _truthy_plain(x):
            if isinstance(x, bool):
                return x
            return str(x).strip().lower() in ("true", "1", "yes", "on")

        # Perform comparison
        if operator == ComparisonOperator.EQUAL:
            fv, ev = field_value, expected_value
            if isinstance(fv, bool) or isinstance(ev, bool) or (
                _looks_bool_token(fv) or _looks_bool_token(ev)
            ):
                try:
                    return bool(_truthy_plain(fv) == _truthy_plain(ev))
                except Exception:
                    pass
            return fv == ev
        elif operator == ComparisonOperator.NOT_EQUAL:
            fv, ev = field_value, expected_value
            if isinstance(fv, bool) or isinstance(ev, bool) or (
                _looks_bool_token(fv) or _looks_bool_token(ev)
            ):
                try:
                    return bool(_truthy_plain(fv) != _truthy_plain(ev))
                except Exception:
                    pass
            return fv != ev
        elif operator == ComparisonOperator.GREATER_THAN:
            return field_value > expected_value
        elif operator == ComparisonOperator.GREATER_THAN_OR_EQUAL:
            return field_value >= expected_value
        elif operator == ComparisonOperator.LESS_THAN:
            return field_value < expected_value
        elif operator == ComparisonOperator.LESS_THAN_OR_EQUAL:
            return field_value <= expected_value
        elif operator == ComparisonOperator.CONTAINS:
            return expected_value in field_value
        elif operator == ComparisonOperator.NOT_CONTAINS:
            return expected_value not in field_value
        elif operator == ComparisonOperator.STARTS_WITH:
            # Exact match on the first whitespace-delimited token
            tokens = field_value.split(None, 1)
            return bool(tokens) and tokens[0] == expected_value
        elif operator == ComparisonOperator.BEGINS_WITH:
            return field_value.startswith(expected_value)
        elif operator == ComparisonOperator.ENDS_WITH:
            return field_value.endswith(expected_value)
        elif operator == ComparisonOperator.REGEX_MATCH:
            import re

            try:
                return bool(re.search(expected_value, field_value))
            except re.error:
                logger.error(f"Invalid regex pattern: {expected_value}")
                return False

        return False


def message_after_trigger_conditions(
    raw_message: str, conditions: List[TriggerCondition]
) -> str:
    """Remove matched condition literals from ``message`` for use in action placeholders.

    Mirrors case-sensitivity rules in :meth:`TriggerCondition._compare_values` for
    ``contains`` / ``starts_with`` / ``begins_with`` / ``ends_with`` on field
    ``message`` only.
    """
    msg = "" if raw_message is None else str(raw_message)
    for cond in conditions:
        if cond.field != "message":
            continue
        op = cond.operator
        if op not in (
            ComparisonOperator.CONTAINS,
            ComparisonOperator.STARTS_WITH,
            ComparisonOperator.BEGINS_WITH,
            ComparisonOperator.ENDS_WITH,
        ):
            continue
        expected = cond.value
        if expected is None:
            continue
        exp = str(expected)
        if not exp:
            continue
        case_sensitive = cond.case_sensitive
        if op == ComparisonOperator.CONTAINS:
            if case_sensitive:
                if exp in msg:
                    msg = msg.replace(exp, "", 1)
            else:
                low, sub = msg.lower(), exp.lower()
                i = low.find(sub)
                if i >= 0:
                    msg = (msg[:i] + msg[i + len(exp) :]).strip()
        elif op == ComparisonOperator.STARTS_WITH:
            # First-word match: strip the first token when it equals the expected value
            parts = msg.split(None, 1)
            if parts:
                check_tok = parts[0] if case_sensitive else parts[0].lower()
                check_e = exp if case_sensitive else exp.lower()
                if check_tok == check_e:
                    msg = parts[1] if len(parts) > 1 else ""
        elif op == ComparisonOperator.BEGINS_WITH:
            check_m = msg if case_sensitive else msg.lower()
            check_e = exp if case_sensitive else exp.lower()
            if check_m.startswith(check_e):
                msg = msg[len(exp) :]
        elif op == ComparisonOperator.ENDS_WITH:
            check_m = msg if case_sensitive else msg.lower()
            check_e = exp if case_sensitive else exp.lower()
            if check_m.endswith(check_e):
                msg = msg[: len(msg) - len(exp)]
        msg = msg.strip()
    return msg


@dataclass
class BaseTrigger(ABC):
    """Base class for all trigger types"""

    trigger_id: str
    trigger_type: TriggerType
    name: str
    description: str = ""
    enabled: bool = True
    conditions: List[TriggerCondition] = field(default_factory=list)
    cooldown_seconds: int = 0  # Minimum time between trigger activations
    last_triggered: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_trigger(self) -> bool:
        """Check if trigger is enabled and not on cooldown"""
        if not self.enabled:
            return False

        if self.cooldown_seconds > 0:
            time_since_last = time.time() - self.last_triggered
            if time_since_last < self.cooldown_seconds:
                return False

        return True

    def evaluate_conditions(self, event_data: Dict[str, Any]) -> bool:
        """Evaluate all conditions for this trigger"""
        if not self.conditions:
            return True  # No conditions means always trigger

        # All conditions must be true (AND logic)
        return all(condition.evaluate(event_data) for condition in self.conditions)

    @abstractmethod
    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Determine if this trigger should fire based on event data"""
        pass

    def trigger(self, event_data: Dict[str, Any]) -> bool:
        """Attempt to trigger this trigger"""
        if not self.can_trigger():
            return False

        if not self.should_trigger(event_data):
            return False

        self.last_triggered = time.time()
        logger.info(f"Trigger '{self.name}' ({self.trigger_id}) fired")
        return True


@dataclass
class BaseAction(ABC):
    """Base class for all action types"""

    action_id: str
    action_type: ActionType
    name: str
    description: str = ""
    enabled: bool = True
    delay_seconds: float = 0
    parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute this action"""
        pass

    def validate_parameters(self) -> bool:
        """Validate that required parameters are present and valid"""
        return True


@dataclass
class Connector:
    """A connector that links a trigger to one or more actions"""

    connector_id: str
    name: str
    description: str = ""
    enabled: bool = True
    trigger: BaseTrigger = None
    actions: List[BaseAction] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_triggered: float = 0
    trigger_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def _game_hook_prerequisites_unmet(self) -> bool:
        """If any enabled game-hook action's hook is off or not attached, skip the whole connector."""
        game_ids: set = set()
        for action in self.actions:
            if not action.enabled:
                continue
            if action.action_type != ActionType.GAME_HOOK:
                continue
            gid = str(getattr(action, "game_id", "ff7") or "ff7").strip() or "ff7"
            game_ids.add(gid)
        if not game_ids:
            return False
        from .game_hooks_service import game_hooks_service

        for gid in sorted(game_ids):
            if not game_hooks_service.is_game_hook_ready(gid):
                logger.debug(
                    "Connector '%s' skipped: game hook %r not ready",
                    self.name,
                    gid,
                )
                return True
        return False

    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """Process an event and potentially trigger actions"""
        if not self.enabled or not self.trigger:
            return False

        if self._game_hook_prerequisites_unmet():
            return False

        # Check if trigger should fire
        trigger_result = self.trigger.trigger(event_data)
        if not trigger_result:
            return False

        # Update connector stats
        self.last_triggered = time.time()
        self.trigger_count += 1

        # Execute all actions; action_results (1-based slot keys) is filled by actions
        # that produce outputs, e.g. game hook query_inventory, for {actionN.field} placeholders.
        success_count = 0
        msg_after = message_after_trigger_conditions(
            str(event_data.get("message", "")), self.trigger.conditions
        )
        action_results: Dict[str, Dict[str, Any]] = {}
        enabled_slots = [
            (idx + 1, act)
            for idx, act in enumerate(self.actions)
            if act.enabled
        ]
        for seq_index, (action_index, action) in enumerate(enabled_slots):
            if seq_index > 0:
                delay = float(getattr(action, "delay_seconds", 0) or 0)
                if delay > 0:
                    await asyncio.sleep(delay)
            try:
                success = await action.execute(
                    trigger_data={
                        "trigger_id": self.trigger.trigger_id,
                        "trigger_type": self.trigger.trigger_type.value,
                        "connector_id": self.connector_id,
                        "triggered_at": self.last_triggered,
                        "message_after_conditions": msg_after,
                        "action_index": action_index,
                        "action_results": action_results,
                    },
                    event_data=event_data,
                )
                if success:
                    success_count += 1
            except Exception as e:
                logger.error(
                    f"Error executing action '{action.name}' in connector '{self.name}': {e}",
                    exc_info=True,
                )

        logger.info(
            f"Connector '{self.name}' executed {success_count}/{len(self.actions)} actions"
        )
        return success_count > 0


class EventData:
    """Standardized event data structure for the connector system"""

    @staticmethod
    def from_twitch_bits(
        bits_amount: int, username: str, message: str = "", **kwargs
    ) -> Dict[str, Any]:
        """Create event data from Twitch bits event"""
        return {
            "event_type": "twitch_bits",
            "amount": bits_amount,
            "username": username,
            "message": message,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_sub(
        tier: int, username: str, message: str = "", months: int = 1, **kwargs
    ) -> Dict[str, Any]:
        """Create event data from Twitch subscription event"""
        return {
            "event_type": "twitch_sub",
            "tier": tier,
            "username": username,
            "message": message,
            "months": months,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_follow(username: str, **kwargs) -> Dict[str, Any]:
        """Create event data from Twitch follow event"""
        return {
            "event_type": "twitch_follow",
            "username": username,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_raid(username: str, viewer_count: int, **kwargs) -> Dict[str, Any]:
        """Create event data from Twitch raid event"""
        return {
            "event_type": "twitch_raid",
            "username": username,
            "viewer_count": viewer_count,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_chat(
        username: str,
        message: str,
        is_command: bool = False,
        command: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """Create event data from Twitch chat message"""
        return {
            "event_type": "twitch_chat_message",
            "username": username,
            "message": message,
            "is_command": is_command,
            "command": command,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_donation(
        amount: float, username: str, message: str = "", currency: str = "USD", **kwargs
    ) -> Dict[str, Any]:
        """Create event data from donation event"""
        return {
            "event_type": "donation",
            "amount": amount,
            "username": username,
            "message": message,
            "currency": currency,
            "timestamp": time.time(),
            "source": kwargs.get("source", "donation"),
            **kwargs,
        }

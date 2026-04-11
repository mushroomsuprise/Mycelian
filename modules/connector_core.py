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
    STARTS_WITH = "starts_with"
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

    # Donation Triggers (StreamLabs)
    DONATION = "donation"

    # Timer/Schedule Triggers
    TIMER = "timer"
    SCHEDULE = "schedule"

    # Manual/Custom Triggers
    HOTKEY = "hotkey"
    WEBHOOK = "webhook"


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
            ComparisonOperator.ENDS_WITH,
            ComparisonOperator.REGEX_MATCH,
        ]:
            field_value = str(field_value) if field_value is not None else ""
            expected_value = str(expected_value)

            if not self.case_sensitive:
                field_value = field_value.lower()
                expected_value = expected_value.lower()

        # Perform comparison
        if operator == ComparisonOperator.EQUAL:
            return field_value == expected_value
        elif operator == ComparisonOperator.NOT_EQUAL:
            return field_value != expected_value
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

    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """Process an event and potentially trigger actions"""
        if not self.enabled or not self.trigger:
            return False

        # Check if trigger should fire
        trigger_result = self.trigger.trigger(event_data)
        if not trigger_result:
            return False

        # Update connector stats
        self.last_triggered = time.time()
        self.trigger_count += 1

        # Execute all actions
        success_count = 0
        for action in self.actions:
            if action.enabled:
                try:
                    success = await action.execute(
                        trigger_data={
                            "trigger_id": self.trigger.trigger_id,
                            "trigger_type": self.trigger.trigger_type.value,
                            "connector_id": self.connector_id,
                            "triggered_at": self.last_triggered,
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

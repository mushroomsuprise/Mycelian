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
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from .connector_core import BaseTrigger, TriggerType

logger = logging.getLogger(__name__)


@dataclass
class TwitchBitsTrigger(BaseTrigger):
    """Trigger for Twitch bits/cheers events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_BITS

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this bits trigger should fire"""
        if event_data.get("event_type") != "twitch_bits":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchSubTrigger(BaseTrigger):
    """Trigger for Twitch subscription events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_SUB

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this subscription trigger should fire"""
        if event_data.get("event_type") != "twitch_sub":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchResubTrigger(BaseTrigger):
    """Trigger for Twitch resubscription events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_RESUB

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this resubscription trigger should fire"""
        if event_data.get("event_type") != "twitch_resub":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchGiftSubTrigger(BaseTrigger):
    """Trigger for Twitch gift subscription events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_GIFTSUB

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this gift subscription trigger should fire"""
        if event_data.get("event_type") != "twitch_giftsub":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchFollowTrigger(BaseTrigger):
    """Trigger for Twitch follow events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_FOLLOW

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this follow trigger should fire"""
        if event_data.get("event_type") != "twitch_follow":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchRaidTrigger(BaseTrigger):
    """Trigger for Twitch raid events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_RAID

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this raid trigger should fire"""
        if event_data.get("event_type") != "twitch_raid":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPointsTrigger(BaseTrigger):
    """Trigger for Twitch channel points redemption events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_POINTS

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this points trigger should fire"""
        if event_data.get("event_type") != "twitch_points":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchChatMessageTrigger(BaseTrigger):
    """Trigger for Twitch chat message events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_CHAT_MESSAGE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this chat message trigger should fire"""
        if event_data.get("event_type") != "twitch_chat_message":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchHypeTrainStartTrigger(BaseTrigger):
    """Trigger for Twitch hype train start events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_HYPE_TRAIN_START

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this hype train start trigger should fire"""
        if event_data.get("event_type") != "twitch_hype_train_start":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TwitchHypeTrainEndTrigger(BaseTrigger):
    """Trigger for Twitch hype train end events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_HYPE_TRAIN_END

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this hype train end trigger should fire"""
        if event_data.get("event_type") != "twitch_hype_train_end":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class DonationTrigger(BaseTrigger):
    """Trigger for donation events from the connector system"""

    def __post_init__(self):
        self.trigger_type = TriggerType.DONATION

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this donation trigger should fire"""
        if event_data.get("event_type") != "donation":
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class TimerTrigger(BaseTrigger):
    """Trigger for timer-based events"""

    interval_seconds: int = 60  # How often to trigger (in seconds)

    def __post_init__(self):
        self.trigger_type = TriggerType.TIMER

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this timer trigger should fire"""
        if event_data.get("event_type") != "timer":
            return False

        # Check if enough time has passed since last trigger
        import time

        current_time = time.time()
        if self.last_triggered == 0:
            # First time triggering
            return True

        time_since_last = current_time - self.last_triggered
        if time_since_last >= self.interval_seconds:
            return self.evaluate_conditions(event_data)

        return False


@dataclass
class ScheduleTrigger(BaseTrigger):
    """Trigger for scheduled events (cron-like)"""

    schedule_pattern: str = (
        ""  # Cron-like pattern (e.g., "0 */15 * * *" for every 15 minutes)
    )
    timezone: str = "UTC"

    def __post_init__(self):
        self.trigger_type = TriggerType.SCHEDULE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this scheduled trigger should fire"""
        if event_data.get("event_type") != "schedule":
            return False

        # This would require a cron library implementation
        # For now, just evaluate conditions
        return self.evaluate_conditions(event_data)


@dataclass
class HotkeyTrigger(BaseTrigger):
    """Trigger for hotkey/key press activation"""

    key_combination: str = ""  # e.g., "ctrl+shift+f", "f5", "alt+tab"
    modifiers: List[str] = (
        None  # List of modifier keys: ["ctrl", "alt", "shift", "cmd"]
    )
    key_code: str = ""  # The main key (e.g., "f", "enter", "space")
    is_global: bool = (
        True  # Whether the hotkey works globally or only when app is focused
    )

    def __post_init__(self):
        self.trigger_type = TriggerType.HOTKEY
        if self.modifiers is None:
            self.modifiers = []

        # Parse key_combination if provided
        if self.key_combination and not self.key_code:
            self._parse_key_combination()

    def _parse_key_combination(self):
        """Parse key combination string into components"""
        parts = [p.strip().lower() for p in self.key_combination.split("+")]

        # Identify modifiers and main key
        modifier_keys = {"ctrl", "control", "alt", "shift", "cmd", "super", "win"}
        modifiers = []
        key_code = ""

        for part in parts:
            if part in modifier_keys:
                modifiers.append(part)
            else:
                key_code = part

        self.modifiers = modifiers
        self.key_code = key_code

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this hotkey trigger should fire"""
        if event_data.get("event_type") != "hotkey":
            return False

        # Check if the pressed key matches our configuration
        pressed_key = event_data.get("key_code", "").lower()
        pressed_modifiers = set(event_data.get("modifiers", []))

        # Compare key codes
        if pressed_key != self.key_code.lower():
            return False

        # Compare modifiers
        expected_modifiers = set(self.modifiers)
        if pressed_modifiers != expected_modifiers:
            return False

        # Check global vs app focus if specified
        if "is_global" in event_data and event_data["is_global"] != self.is_global:
            return False

        return self.evaluate_conditions(event_data)


@dataclass
class StreamdeckTrigger(BaseTrigger):
    """Trigger for Stream Deck button activation via the Mycelian plugin."""

    connector_id: str = ""

    def __post_init__(self):
        self.trigger_type = TriggerType.STREAMDECK

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "streamdeck":
            return False
        if not self.connector_id:
            return False
        if event_data.get("connector_id") != self.connector_id:
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class WebhookTrigger(BaseTrigger):
    """Trigger for webhook events"""

    webhook_url: str = ""
    secret_key: str = ""  # For webhook validation

    def __post_init__(self):
        self.trigger_type = TriggerType.WEBHOOK

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this webhook trigger should fire"""
        if event_data.get("event_type") != "webhook":
            return False

        # Could add webhook signature validation here
        return self.evaluate_conditions(event_data)


@dataclass
class ObsSceneChangedTrigger(BaseTrigger):
    """Fires when OBS program scene changes (WebSocket)."""

    def __post_init__(self):
        self.trigger_type = TriggerType.OBS_SCENE_CHANGED

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "obs_scene_changed":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class ObsStreamStateTrigger(BaseTrigger):
    """Fires when OBS streaming output starts/stops or state string changes."""

    def __post_init__(self):
        self.trigger_type = TriggerType.OBS_STREAM_STATE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "obs_stream_state":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class ObsRecordStateTrigger(BaseTrigger):
    """Fires when OBS recording output starts/stops or state string changes."""

    def __post_init__(self):
        self.trigger_type = TriggerType.OBS_RECORD_STATE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "obs_record_state":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class ObsInputMuteTrigger(BaseTrigger):
    """Fires when an OBS audio input mute state toggles."""

    def __post_init__(self):
        self.trigger_type = TriggerType.OBS_INPUT_MUTE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "obs_input_mute":
            return False
        return self.evaluate_conditions(event_data)


CONNECTOR_EVENT_TYPES = frozenset(
    {
        "twitch_bits",
        "twitch_sub",
        "twitch_resub",
        "twitch_giftsub",
        "twitch_follow",
        "twitch_raid",
        "twitch_points",
        "twitch_chat_message",
        "twitch_hype_train_start",
        "twitch_hype_train_end",
        "donation",
        "timer",
        "schedule",
        "hotkey",
        "streamdeck",
        "webhook",
        "obs_scene_changed",
        "obs_stream_state",
        "obs_record_state",
        "obs_input_mute",
    }
)


@dataclass
class AnyTrigger(BaseTrigger):
    """Fires on any connector-processed event type."""

    def __post_init__(self):
        self.trigger_type = TriggerType.ANY

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") not in CONNECTOR_EVENT_TYPES:
            return False
        return self.evaluate_conditions(event_data)


# Factory function for creating triggers
def create_trigger(
    trigger_type: TriggerType, trigger_id: str, name: str, **kwargs
) -> BaseTrigger:
    """Factory function to create trigger instances"""
    trigger_classes = {
        TriggerType.TWITCH_BITS: TwitchBitsTrigger,
        TriggerType.TWITCH_SUB: TwitchSubTrigger,
        TriggerType.TWITCH_RESUB: TwitchResubTrigger,
        TriggerType.TWITCH_GIFTSUB: TwitchGiftSubTrigger,
        TriggerType.TWITCH_FOLLOW: TwitchFollowTrigger,
        TriggerType.TWITCH_RAID: TwitchRaidTrigger,
        TriggerType.TWITCH_POINTS: TwitchPointsTrigger,
        TriggerType.TWITCH_CHAT_MESSAGE: TwitchChatMessageTrigger,
        TriggerType.TWITCH_HYPE_TRAIN_START: TwitchHypeTrainStartTrigger,
        TriggerType.TWITCH_HYPE_TRAIN_END: TwitchHypeTrainEndTrigger,
        TriggerType.DONATION: DonationTrigger,
        TriggerType.TIMER: TimerTrigger,
        TriggerType.SCHEDULE: ScheduleTrigger,
        TriggerType.HOTKEY: HotkeyTrigger,
        TriggerType.STREAMDECK: StreamdeckTrigger,
        TriggerType.WEBHOOK: WebhookTrigger,
        TriggerType.OBS_SCENE_CHANGED: ObsSceneChangedTrigger,
        TriggerType.OBS_STREAM_STATE: ObsStreamStateTrigger,
        TriggerType.OBS_RECORD_STATE: ObsRecordStateTrigger,
        TriggerType.OBS_INPUT_MUTE: ObsInputMuteTrigger,
        TriggerType.ANY: AnyTrigger,
    }

    trigger_class = trigger_classes.get(trigger_type)
    if not trigger_class:
        raise ValueError(f"Unknown trigger type: {trigger_type}")

    # Remove trigger_type from kwargs to avoid duplicate parameter
    filtered_kwargs = {k: v for k, v in kwargs.items() if k != "trigger_type"}

    return trigger_class(
        trigger_id=trigger_id, name=name, trigger_type=trigger_type, **filtered_kwargs
    )

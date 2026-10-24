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

import logging
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

from .connector_core import BaseTrigger, TriggerType
from .hotkey_listener import normalize_hotkey_combo

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
class YouTubeChatMessageTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_CHAT_MESSAGE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_chat_message":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class YouTubeMemberTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_MEMBER

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_member":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class YouTubeMemberMilestoneTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_MEMBER_MILESTONE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_member_milestone":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class YouTubeGiftMembershipTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_GIFT_MEMBERSHIP

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_gift_membership":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class YouTubeSuperChatTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_SUPERCHAT

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_superchat":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class YouTubeSuperStickerTrigger(BaseTrigger):
    def __post_init__(self):
        self.trigger_type = TriggerType.YOUTUBE_SUPERSTICKER

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "youtube_supersticker":
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
class TwitchStreamOnlineTrigger(BaseTrigger):
    """Trigger for Twitch stream.online events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_STREAM_ONLINE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_stream_online":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchStreamOfflineTrigger(BaseTrigger):
    """Trigger for Twitch stream.offline events"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_STREAM_OFFLINE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_stream_offline":
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
    """Trigger for timer-based events.

    The connector scheduler (``ConnectorManager._connector_scheduler_loop``) owns
    the clock: it sends one ``timer`` event per elapsed interval, addressed to
    this connector only (``target_connector_id``), carrying ``timer_id`` (this
    trigger's id) and ``interval_seconds`` (this trigger's interval). The first
    event comes one interval after the connector is enabled / the app starts.
    """

    interval_seconds: int = 60  # How often to trigger (in seconds)

    def __post_init__(self):
        self.trigger_type = TriggerType.TIMER
        try:
            self.interval_seconds = max(1, int(float(self.interval_seconds)))
        except (TypeError, ValueError):
            self.interval_seconds = 60

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this timer trigger should fire"""
        if event_data.get("event_type") != "timer":
            return False
        tid = event_data.get("timer_id")
        if tid and self.trigger_id and tid not in (self.trigger_id, "test"):
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class ScheduleTrigger(BaseTrigger):
    """Trigger for scheduled events (cron-like)"""

    schedule_pattern: str = (
        ""  # Legacy: "M H * * *" daily cron (only minute/hour are read)
    )
    timezone: str = "UTC"  # Legacy, unused: schedules run in local time
    specific_time: str = ""  # Daily "HH:MM" (24-hour, local time)
    # 0=Mon .. 6=Sun; empty = every day
    days_of_week: List[int] = field(default_factory=list)

    def __post_init__(self):
        self.trigger_type = TriggerType.SCHEDULE
        self.days_of_week = normalize_days_of_week(self.days_of_week)

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this scheduled trigger should fire"""
        if event_data.get("event_type") != "schedule":
            return False

        tid = event_data.get("trigger_id")
        if tid and self.trigger_id and tid != self.trigger_id:
            return False

        return self.evaluate_conditions(event_data)


_DAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def normalize_days_of_week(days: Any) -> List[int]:
    """Sorted unique weekday numbers (0=Mon..6=Sun) from ints, numeric text or names."""
    if days is None or days == "":
        return []
    if isinstance(days, (str, int)):
        days = [days]
    out = set()
    try:
        items = list(days)
    except TypeError:
        return []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            value = int(item)
        else:
            text = str(item).strip().lower()
            if text in _DAY_NAMES:
                value = _DAY_NAMES[text]
            else:
                try:
                    value = int(text)
                except ValueError:
                    continue
        if 0 <= value <= 6:
            out.add(value)
    return sorted(out)


def schedule_is_due(trigger: Any, now: Any) -> bool:
    """True when *now* (a local ``datetime``) is the trigger's scheduled minute and day."""
    hhmm = parse_schedule_hhmm(trigger)
    if hhmm is None:
        return False
    hour, minute = hhmm
    if now.hour != hour or now.minute != minute:
        return False
    days = normalize_days_of_week(getattr(trigger, "days_of_week", None))
    return not days or now.weekday() in days


def parse_schedule_hhmm(trigger: Any) -> Optional[Tuple[int, int]]:
    """Parse daily (hour, minute) from schedule_pattern or specific_time.

    Accepts ``HH:MM`` or a 5-field cron ``M H * * *``. Returns None if unparseable.
    """
    specific = str(getattr(trigger, "specific_time", "") or "").strip()
    pattern = str(getattr(trigger, "schedule_pattern", "") or "").strip()
    for text in (specific, pattern):
        if not text:
            continue
        parts = text.split()
        if len(parts) >= 5:
            try:
                minute = int(parts[0])
                hour = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
            except ValueError:
                pass
        if ":" in text:
            try:
                hour_s, rest = text.strip().split(":", 1)
                hour = int(hour_s)
                minute = int(rest.split()[0])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
            except ValueError:
                pass
    return None


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
        """Parse key combination string into components (normalized)."""
        parts = normalize_hotkey_combo(self.key_combination).split("+")
        parts = [p for p in parts if p]
        if not parts:
            self.modifiers, self.key_code = [], ""
            return
        self.modifiers = parts[:-1]
        self.key_code = parts[-1]

    def combo(self) -> str:
        """Normalized combination this trigger listens for."""
        if self.key_combination:
            return normalize_hotkey_combo(self.key_combination)
        return normalize_hotkey_combo(
            "+".join([*(self.modifiers or []), self.key_code or ""])
        )

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Check if this hotkey trigger should fire.

        Both sides go through :func:`normalize_hotkey_combo`, so ``"Ctrl + Shift + F"``
        matches a press of ``ctrl+shift+f``. ``is_global`` is kept for stored data
        but not compared (hotkeys are always global).
        """
        if event_data.get("event_type") != "hotkey":
            return False

        pressed = event_data.get("combo")
        if not pressed:
            pressed = "+".join(
                [*(event_data.get("modifiers") or []), str(event_data.get("key_code") or "")]
            )
        expected = self.combo()
        if not expected or normalize_hotkey_combo(pressed) != expected:
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

    webhook_url: str = ""  # Legacy, unused (see connector_variables.webhook_url)
    secret_key: str = ""  # Required by POST /api/connectors/webhook/<connector_id>

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
        "twitch_stream_online",
        "twitch_stream_offline",
        "youtube_chat_message",
        "youtube_member",
        "youtube_member_milestone",
        "youtube_gift_membership",
        "youtube_superchat",
        "youtube_supersticker",
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
        # Internal clock ticks and events addressed to one connector (timer,
        # schedule, webhook) are not "any event"; the manager also routes
        # addressed events only to their target.
        if event_data.get("internal_tick"):
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
        TriggerType.TWITCH_STREAM_ONLINE: TwitchStreamOnlineTrigger,
        TriggerType.TWITCH_STREAM_OFFLINE: TwitchStreamOfflineTrigger,
        TriggerType.YOUTUBE_CHAT_MESSAGE: YouTubeChatMessageTrigger,
        TriggerType.YOUTUBE_MEMBER: YouTubeMemberTrigger,
        TriggerType.YOUTUBE_MEMBER_MILESTONE: YouTubeMemberMilestoneTrigger,
        TriggerType.YOUTUBE_GIFT_MEMBERSHIP: YouTubeGiftMembershipTrigger,
        TriggerType.YOUTUBE_SUPERCHAT: YouTubeSuperChatTrigger,
        TriggerType.YOUTUBE_SUPERSTICKER: YouTubeSuperStickerTrigger,
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

    # Drop keys the trigger dataclass does not define (old/foreign stored data)
    allowed = {f.name for f in fields(trigger_class)}
    unexpected = set(filtered_kwargs) - allowed
    if unexpected:
        logger.debug(
            "Ignoring unexpected parameters for %s: %s",
            trigger_type.value,
            sorted(unexpected),
        )
    safe_kwargs = {k: v for k, v in filtered_kwargs.items() if k in allowed}

    return trigger_class(
        trigger_id=trigger_id, name=name, trigger_type=trigger_type, **safe_kwargs
    )

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
class TwitchHypeTrainProgressTrigger(BaseTrigger):
    """Trigger for Hype Train progress (every contribution)"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_HYPE_TRAIN_PROGRESS

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_hype_train_progress":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPollBeginTrigger(BaseTrigger):
    """Trigger for a Twitch poll starting"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_POLL_BEGIN

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_poll_begin":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPollEndTrigger(BaseTrigger):
    """Trigger for a Twitch poll ending"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_POLL_END

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_poll_end":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPredictionBeginTrigger(BaseTrigger):
    """Trigger for a Twitch prediction starting"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_PREDICTION_BEGIN

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_prediction_begin":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPredictionLockTrigger(BaseTrigger):
    """Trigger for a Twitch prediction locking (bets closed)"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_PREDICTION_LOCK

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_prediction_lock":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchPredictionEndTrigger(BaseTrigger):
    """Trigger for a Twitch prediction resolving or being canceled"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_PREDICTION_END

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_prediction_end":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchAdBreakBeginTrigger(BaseTrigger):
    """Trigger for an ad break starting"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_AD_BREAK_BEGIN

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_ad_break_begin":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchChannelUpdateTrigger(BaseTrigger):
    """Trigger for the stream title or category changing"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_CHANNEL_UPDATE

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_channel_update":
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class TwitchShoutoutReceivedTrigger(BaseTrigger):
    """Trigger for another channel shouting you out"""

    def __post_init__(self):
        self.trigger_type = TriggerType.TWITCH_SHOUTOUT_RECEIVED

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "twitch_shoutout_received":
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



def _platform_matches(wanted: str, event_data: Dict[str, Any]) -> bool:
    wanted = str(wanted or "").strip().lower()
    return wanted in ("", "any") or str(event_data.get("platform") or "").lower() == wanted


@dataclass
class GameUnlockTrigger(BaseTrigger):
    """A trophy or achievement is unlocked (Games Tracker)."""

    platform: str = ""  # "" any, "psn" or "steam"
    grade: str = ""  # "" any, bronze / silver / gold / platinum, or "achievement" (Steam)

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_UNLOCK

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "game_unlock":
            return False
        if not _platform_matches(self.platform, event_data):
            return False
        grade = str(self.grade or "").strip().lower()
        if grade not in ("", "any") and str(event_data.get("unlock_grade") or "").lower() != grade:
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class GameCompletedTrigger(BaseTrigger):
    """A game is completed: platinum (PSN) or every achievement (Steam)."""

    platform: str = ""

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_COMPLETED

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "game_completed":
            return False
        if not _platform_matches(self.platform, event_data):
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class GameStartedTrigger(BaseTrigger):
    """A game shows up in the library for the first time."""

    platform: str = ""

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_STARTED

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "game_started":
            return False
        if not _platform_matches(self.platform, event_data):
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class GameMilestoneTrigger(BaseTrigger):
    """A game's completion crosses one of the tracker's milestones (25 / 50 / 75%)."""

    platform: str = ""
    milestone: int = 0  # 0 = any milestone

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_MILESTONE
        try:
            self.milestone = max(0, int(float(self.milestone or 0)))
        except (TypeError, ValueError):
            self.milestone = 0

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        if event_data.get("event_type") != "game_milestone":
            return False
        if not _platform_matches(self.platform, event_data):
            return False
        if self.milestone:
            try:
                if int(float(event_data.get("milestone") or 0)) != self.milestone:
                    return False
            except (TypeError, ValueError):
                return False
        return self.evaluate_conditions(event_data)


@dataclass
class _GameGoalTrigger(BaseTrigger):
    platform: str = ""  # "" any, "psn" or "steam"
    game: str = ""  # part of the game's name; "" any
    goal: str = ""  # part of the goal's name; "" any
    goal_kind: str = ""  # "" any, "manual", "auto" or a goal kind (complete / count / percent / unlock)

    _EVENT = ""

    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        from .chatbot_core import game_goal_matches

        if event_data.get("event_type") != self._EVENT:
            return False
        if not _platform_matches(self.platform, event_data):
            return False
        if not game_goal_matches(event_data, self.game, self.goal_kind or "any", self.goal):
            return False
        return self.evaluate_conditions(event_data)


@dataclass
class GameGoalProgressTrigger(_GameGoalTrigger):
    """A game goal moves: a manual count changes, or progress counts toward an automatic goal."""

    _EVENT = "game_goal_progress"

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_GOAL_PROGRESS


@dataclass
class GameGoalCompletedTrigger(_GameGoalTrigger):
    """A game goal is done."""

    _EVENT = "game_goal_completed"

    def __post_init__(self):
        self.trigger_type = TriggerType.GAME_GOAL_COMPLETED


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
        "twitch_hype_train_progress",
        "twitch_poll_begin",
        "twitch_poll_end",
        "twitch_prediction_begin",
        "twitch_prediction_lock",
        "twitch_prediction_end",
        "twitch_ad_break_begin",
        "twitch_channel_update",
        "twitch_shoutout_received",
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
        "game_unlock",
        "game_completed",
        "game_started",
        "game_milestone",
        "game_goal_progress",
        "game_goal_completed",
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
        TriggerType.TWITCH_HYPE_TRAIN_PROGRESS: TwitchHypeTrainProgressTrigger,
        TriggerType.TWITCH_POLL_BEGIN: TwitchPollBeginTrigger,
        TriggerType.TWITCH_POLL_END: TwitchPollEndTrigger,
        TriggerType.TWITCH_PREDICTION_BEGIN: TwitchPredictionBeginTrigger,
        TriggerType.TWITCH_PREDICTION_LOCK: TwitchPredictionLockTrigger,
        TriggerType.TWITCH_PREDICTION_END: TwitchPredictionEndTrigger,
        TriggerType.TWITCH_AD_BREAK_BEGIN: TwitchAdBreakBeginTrigger,
        TriggerType.TWITCH_CHANNEL_UPDATE: TwitchChannelUpdateTrigger,
        TriggerType.TWITCH_SHOUTOUT_RECEIVED: TwitchShoutoutReceivedTrigger,
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
        TriggerType.GAME_UNLOCK: GameUnlockTrigger,
        TriggerType.GAME_COMPLETED: GameCompletedTrigger,
        TriggerType.GAME_STARTED: GameStartedTrigger,
        TriggerType.GAME_MILESTONE: GameMilestoneTrigger,
        TriggerType.GAME_GOAL_PROGRESS: GameGoalProgressTrigger,
        TriggerType.GAME_GOAL_COMPLETED: GameGoalCompletedTrigger,
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

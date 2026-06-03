#!/usr/bin/env python3
"""
MIT License — same as repository root.

Runtime patch for twitchAPI EventSub: ChannelChatNotificationData omits Twitch's
``watch_streak`` payload field, so TwitchObject drops it during deserialization.

https://dev.twitch.tv/docs/eventsub/eventsub-reference/#channel-chat-notification-event
"""

import logging
from typing import Optional

from twitchAPI.object.base import TwitchObject
from twitchAPI.object.eventsub import ChannelChatNotificationData

logger = logging.getLogger(__name__)

_PATCH_ATTR = "__mycelian_watch_streak_field_patched__"


class WatchStreakNoticeData(TwitchObject):
    """Subset of Twitch ``watch_streak`` chat-notification notice payload."""

    streak_count: int
    channel_points_awarded: int


def ensure_channel_chat_notification_watch_streak_patch() -> None:
    """Add ``watch_streak`` typing to twitchAPI once; no-op if already present upstream."""
    if getattr(ChannelChatNotificationData, _PATCH_ATTR, False):
        return

    existing = getattr(ChannelChatNotificationData, "__annotations__", None) or {}
    if isinstance(existing, dict) and "watch_streak" in existing:
        setattr(ChannelChatNotificationData, _PATCH_ATTR, True)
        logger.debug(
            "ChannelChatNotificationData.watch_streak already modeled upstream; "
            "skipping Mycelian EventSub patch"
        )
        return

    merged = dict(existing)
    merged["watch_streak"] = Optional[WatchStreakNoticeData]
    ChannelChatNotificationData.__annotations__ = merged
    setattr(ChannelChatNotificationData, _PATCH_ATTR, True)
    logger.debug(
        "Patched ChannelChatNotificationData.watch_streak for EventSub deserialization"
    )

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
MIT License — same as repository root.

Runtime patches for twitchAPI EventSub gaps:

1. ChannelChatNotificationData omits Twitch's ``watch_streak`` payload field, so
   TwitchObject drops it during deserialization.
2. Hype Train EventSub v1 was withdrawn by Twitch (2026-01-15); twitchAPI 4.5.0
   on PyPI still subscribes with version ``1``. Patch listen methods to use ``2``
   and ensure v2 payload fields are annotated for deserialization.

https://dev.twitch.tv/docs/eventsub/eventsub-reference/#channel-chat-notification-event
https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelhype_trainbegin
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, List, Optional

from twitchAPI.eventsub.base import EventSubBase
from twitchAPI.object.base import TwitchObject
from twitchAPI.object.eventsub import (
    ChannelChatNotificationData,
    HypeTrainData,
    HypeTrainEndData,
    HypeTrainEndEvent,
    HypeTrainEvent,
)

logger = logging.getLogger(__name__)

_WATCH_STREAK_PATCH_ATTR = "__mycelian_watch_streak_field_patched__"
_HYPE_TRAIN_V2_PATCH_ATTR = "__mycelian_hype_train_v2_patched__"


class WatchStreakNoticeData(TwitchObject):
    """Subset of Twitch ``watch_streak`` chat-notification notice payload."""

    streak_count: int
    channel_points_awarded: int


class SharedTrainParticipants(TwitchObject):
    """Broadcaster participating in a shared Hype Train (EventSub v2)."""

    broadcaster_user_id: str
    broadcaster_user_login: str
    broadcaster_user_name: str


def ensure_channel_chat_notification_watch_streak_patch() -> None:
    """Force ``watch_streak`` onto twitchAPI as ``WatchStreakNoticeData``.

    Always overwrites a dict/Any/missing annotation so TwitchObject builds a
    real nested object instead of dropping the field or leaving a raw dict.
    """
    existing = getattr(ChannelChatNotificationData, "__annotations__", None) or {}
    if not isinstance(existing, dict):
        existing = {}
    desired = Optional[WatchStreakNoticeData]
    if (
        getattr(ChannelChatNotificationData, _WATCH_STREAK_PATCH_ATTR, False)
        and existing.get("watch_streak") is desired
    ):
        return

    merged = dict(existing)
    merged["watch_streak"] = desired
    ChannelChatNotificationData.__annotations__ = merged
    setattr(ChannelChatNotificationData, _WATCH_STREAK_PATCH_ATTR, True)
    logger.debug(
        "Patched ChannelChatNotificationData.watch_streak for EventSub deserialization"
    )


def _merge_annotations(cls: type, extra: dict) -> None:
    existing = getattr(cls, "__annotations__", None) or {}
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    for key, value in extra.items():
        if key not in merged:
            merged[key] = value
    cls.__annotations__ = merged


def ensure_hype_train_v2_patch() -> None:
    """Subscribe to Hype Train EventSub v2 and accept v2 payload fields.

    No-op when upstream already uses version ``2`` (e.g. a post-4.5.0 release).
    """
    if getattr(EventSubBase, _HYPE_TRAIN_V2_PATCH_ATTR, False):
        return

    # Detect upstream that already migrated to v2 (post-PyPI 4.5.0).
    already_v2 = False
    try:
        import inspect

        src = inspect.getsource(EventSubBase.listen_hype_train_begin)
        already_v2 = "'2'" in src or '"2"' in src
    except (OSError, TypeError):
        already_v2 = False

    _merge_annotations(
        HypeTrainData,
        {
            "type": Optional[str],
            "is_shared_train": Optional[bool],
            "all_time_high_level": Optional[int],
            "all_time_high_total": Optional[int],
            "shared_train_participants": Optional[List[SharedTrainParticipants]],
        },
    )
    _merge_annotations(
        HypeTrainEndData,
        {
            "type": Optional[str],
            "is_shared_train": Optional[bool],
            "shared_train_participants": Optional[List[SharedTrainParticipants]],
        },
    )

    if already_v2:
        setattr(EventSubBase, _HYPE_TRAIN_V2_PATCH_ATTR, True)
        logger.debug(
            "twitchAPI Hype Train listeners already use EventSub v2; "
            "only ensured payload field annotations"
        )
        return

    async def listen_hype_train_begin(
        self,
        broadcaster_user_id: str,
        callback: Callable[[HypeTrainEvent], Awaitable[None]],
    ) -> str:
        return await self._subscribe(
            "channel.hype_train.begin",
            "2",
            {"broadcaster_user_id": broadcaster_user_id},
            callback,
            HypeTrainEvent,
        )

    async def listen_hype_train_progress(
        self,
        broadcaster_user_id: str,
        callback: Callable[[HypeTrainEvent], Awaitable[None]],
    ) -> str:
        return await self._subscribe(
            "channel.hype_train.progress",
            "2",
            {"broadcaster_user_id": broadcaster_user_id},
            callback,
            HypeTrainEvent,
        )

    async def listen_hype_train_end(
        self,
        broadcaster_user_id: str,
        callback: Callable[[HypeTrainEndEvent], Awaitable[None]],
    ) -> str:
        return await self._subscribe(
            "channel.hype_train.end",
            "2",
            {"broadcaster_user_id": broadcaster_user_id},
            callback,
            HypeTrainEndEvent,
        )

    EventSubBase.listen_hype_train_begin = listen_hype_train_begin  # type: ignore[method-assign]
    EventSubBase.listen_hype_train_progress = listen_hype_train_progress  # type: ignore[method-assign]
    EventSubBase.listen_hype_train_end = listen_hype_train_end  # type: ignore[method-assign]
    setattr(EventSubBase, _HYPE_TRAIN_V2_PATCH_ATTR, True)
    logger.info(
        "Patched twitchAPI Hype Train EventSub subscriptions to version 2 "
        "(Twitch withdrew v1)"
    )

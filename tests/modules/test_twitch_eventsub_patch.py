# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_eventsub_patch.py (runtime twitchAPI EventSub patches)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from typing import Optional  # noqa: E402

import pytest  # noqa: E402
from twitchAPI.eventsub.base import EventSubBase  # noqa: E402
from twitchAPI.object.eventsub import (  # noqa: E402
    ChannelChatNotificationData,
    ChatMessageFragment,
    HypeTrainData,
    HypeTrainEndData,
    HypeTrainEndEvent,
    HypeTrainEvent,
)

from modules import twitch_eventsub_patch as tp  # noqa: E402


@pytest.fixture
def saved(monkeypatch):
    """Snapshot every class attribute the patches touch so monkeypatch restores them."""
    classes = (ChannelChatNotificationData, ChatMessageFragment, HypeTrainData, HypeTrainEndData)
    originals = {cls: dict(getattr(cls, "__annotations__", {}) or {}) for cls in classes}
    for attr in (tp._WATCH_STREAK_PATCH_ATTR, tp._MODIVERSARY_PATCH_ATTR):
        monkeypatch.setattr(ChannelChatNotificationData, attr, getattr(ChannelChatNotificationData, attr, False),
                            raising=False)
    monkeypatch.setattr(ChatMessageFragment, tp._CHAT_GIF_PATCH_ATTR,
                        getattr(ChatMessageFragment, tp._CHAT_GIF_PATCH_ATTR, False), raising=False)
    for name in ("listen_hype_train_begin", "listen_hype_train_progress", "listen_hype_train_end"):
        monkeypatch.setattr(EventSubBase, name, getattr(EventSubBase, name))
    monkeypatch.setattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR,
                        getattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR, False), raising=False)
    yield monkeypatch
    for cls, ann in originals.items():
        cls.__annotations__ = ann


@pytest.mark.parametrize(
    "fn, cls, attr, field, desired",
    [
        (tp.ensure_channel_chat_notification_modiversary_patch, ChannelChatNotificationData,
         tp._MODIVERSARY_PATCH_ATTR, "modiversary", Optional[tp.ModiversaryNoticeData]),
        (tp.ensure_channel_chat_notification_watch_streak_patch, ChannelChatNotificationData,
         tp._WATCH_STREAK_PATCH_ATTR, "watch_streak", Optional[tp.WatchStreakNoticeData]),
        (tp.ensure_channel_chat_message_gif_patch, ChatMessageFragment,
         tp._CHAT_GIF_PATCH_ATTR, "gif", Optional[tp.ChatMessageFragmentGifMetadata]),
    ],
)
def test_field_patches_apply_and_are_idempotent(saved, fn, cls, attr, field, desired):
    saved.setattr(cls, attr, False)
    cls.__annotations__ = {"other": int, field: dict}
    fn()
    assert cls.__annotations__[field] == desired
    assert cls.__annotations__["other"] is int
    assert getattr(cls, attr) is True
    before = cls.__annotations__
    fn()  # already patched: early return keeps the same dict object
    assert cls.__annotations__ is before


@pytest.mark.parametrize(
    "fn, cls, attr, field",
    [
        (tp.ensure_channel_chat_notification_modiversary_patch, ChannelChatNotificationData,
         tp._MODIVERSARY_PATCH_ATTR, "modiversary"),
        (tp.ensure_channel_chat_notification_watch_streak_patch, ChannelChatNotificationData,
         tp._WATCH_STREAK_PATCH_ATTR, "watch_streak"),
        (tp.ensure_channel_chat_message_gif_patch, ChatMessageFragment, tp._CHAT_GIF_PATCH_ATTR, "gif"),
    ],
)
def test_field_patches_repatch_when_flag_set_but_annotation_lost(saved, fn, cls, attr, field):
    saved.setattr(cls, attr, True)
    cls.__annotations__ = {}
    fn()
    assert field in cls.__annotations__


def test_merge_annotations_keeps_existing_and_handles_non_dict(saved):
    class Dummy:
        pass

    Dummy.__annotations__ = {"a": int}
    tp._merge_annotations(Dummy, {"a": str, "b": float})
    assert Dummy.__annotations__ == {"a": int, "b": float}

    class Weird:
        pass

    Weird.__annotations__ = ["not", "a", "dict"]  # type: ignore[assignment]
    tp._merge_annotations(Weird, {"x": int})
    assert Weird.__annotations__ == {"x": int}


def test_hype_train_patch_noop_when_already_patched(saved):
    saved.setattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR, True)
    sentinel = object()
    saved.setattr(EventSubBase, "listen_hype_train_begin", sentinel)
    tp.ensure_hype_train_v2_patch()
    assert EventSubBase.listen_hype_train_begin is sentinel


def test_hype_train_patch_skips_listeners_when_upstream_is_v2(saved):
    saved.setattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR, False)

    async def listen_hype_train_begin(self, broadcaster_user_id, callback):
        return await self._subscribe("channel.hype_train.begin", "2", {}, callback, None)

    saved.setattr(EventSubBase, "listen_hype_train_begin", listen_hype_train_begin)
    tp.ensure_hype_train_v2_patch()
    assert EventSubBase.listen_hype_train_begin is listen_hype_train_begin
    assert getattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR) is True
    assert "shared_train_participants" in HypeTrainData.__annotations__
    assert "is_shared_train" in HypeTrainEndData.__annotations__


def test_hype_train_patch_when_source_unavailable(saved):
    saved.setattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR, False)
    saved.setattr(EventSubBase, "listen_hype_train_begin", len)  # builtin: getsource raises TypeError
    tp.ensure_hype_train_v2_patch()
    assert EventSubBase.listen_hype_train_begin is not len
    assert getattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR) is True


def test_patched_listeners_subscribe_with_version_two(saved):
    saved.setattr(EventSubBase, tp._HYPE_TRAIN_V2_PATCH_ATTR, False)

    async def old(self, broadcaster_user_id, callback):  # v1 upstream
        return await self._subscribe("channel.hype_train.begin", "1", {}, callback, None)

    saved.setattr(EventSubBase, "listen_hype_train_begin", old)
    tp.ensure_hype_train_v2_patch()

    calls = []

    class Fake:
        async def _subscribe(self, *args):
            calls.append(args)
            return "sub-id"

    cb = object()
    fake = Fake()
    for name in ("listen_hype_train_begin", "listen_hype_train_progress", "listen_hype_train_end"):
        assert asyncio.run(getattr(EventSubBase, name)(fake, "b1", cb)) == "sub-id"
    assert [c[0] for c in calls] == [
        "channel.hype_train.begin", "channel.hype_train.progress", "channel.hype_train.end"]
    assert all(c[1] == "2" and c[2] == {"broadcaster_user_id": "b1"} and c[3] is cb for c in calls)
    assert calls[0][4] is HypeTrainEvent and calls[1][4] is HypeTrainEvent and calls[2][4] is HypeTrainEndEvent


def test_notice_data_objects_deserialize():
    ws = tp.WatchStreakNoticeData(streak_count=5, channel_points_awarded=350)
    assert ws.streak_count == 5 and ws.channel_points_awarded == 350
    assert tp.ModiversaryNoticeData(months=3).months == 3
    gif = tp.ChatMessageFragmentGifMetadata(url="u", id="i")
    assert gif.url == "u" and gif.id == "i"


@pytest.mark.parametrize(
    "fn, cls, field",
    [
        (tp.ensure_channel_chat_notification_modiversary_patch, ChannelChatNotificationData, "modiversary"),
        (tp.ensure_channel_chat_notification_watch_streak_patch, ChannelChatNotificationData, "watch_streak"),
        (tp.ensure_channel_chat_message_gif_patch, ChatMessageFragment, "gif"),
    ],
)
def test_field_patches_replace_non_dict_annotations(saved, fn, cls, field):
    cls.__annotations__ = ["broken"]  # type: ignore[assignment]
    fn()
    assert list(cls.__annotations__) == [field]

#!/usr/bin/env python3
"""Tests for single gift-sub recipient correlation on Twitch alerts."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules import alertutils
from modules import twitch as twitch_module
from modules.uiwindows.activity_feed import build_activity_feed_alert_payload


def _make_giftsub_alert(qty: int = 1, username: str = "CoolGifter") -> alertutils.AlertObj:
    alert = alertutils.AlertObj()
    alert.alert_type = "giftsub"
    alert.username = username
    alert.gift_qty = qty
    alert.tier = 1
    alert.alert_id = f"TestGift{qty}"
    alert.timestamp = 1.0
    alert.recipient = ""
    return alert


class GiftSubRecipientCorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        twitch_module._clear_pending_single_gifts_for_tests()

    def tearDown(self) -> None:
        twitch_module._clear_pending_single_gifts_for_tests()

    def test_recipient_before_gift_attaches_immediately(self) -> None:
        emitted = []

        def _capture(alert):
            emitted.append(alert)

        with patch.object(
            twitch_module, "_emit_giftsub_alert_and_instant", side_effect=_capture
        ):
            attached = twitch_module.note_gift_recipient(
                "ViewerName", gifter_login="coolgifter", gifter_id="111"
            )
            self.assertFalse(attached)

            alert = _make_giftsub_alert()
            found = twitch_module.begin_single_gift_wait(
                alert, gifter_login="coolgifter", gifter_id="111"
            )
            self.assertTrue(found)
            self.assertEqual(alert.recipient, "ViewerName")
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0].recipient, "ViewerName")

    def test_gift_before_recipient_attaches_and_emits(self) -> None:
        emitted = []

        def _capture(alert):
            emitted.append(alert)

        async def _run() -> None:
            with patch.object(
                twitch_module, "_emit_giftsub_alert_and_instant", side_effect=_capture
            ):
                with patch.object(
                    twitch_module, "_SINGLE_GIFT_RECIPIENT_WAIT_SECONDS", 0.05
                ):
                    alert = _make_giftsub_alert()
                    waiting = twitch_module.begin_single_gift_wait(
                        alert, gifter_login="coolgifter", gifter_id="111"
                    )
                    self.assertFalse(waiting)
                    self.assertEqual(len(emitted), 0)

                    attached = twitch_module.note_gift_recipient(
                        "ViewerName", gifter_login="coolgifter", gifter_id="111"
                    )
                    self.assertTrue(attached)
                    # Let cancelled wait task settle.
                    await asyncio.sleep(0.01)

                    self.assertEqual(alert.recipient, "ViewerName")
                    self.assertEqual(len(emitted), 1)
                    self.assertEqual(emitted[0].recipient, "ViewerName")

        asyncio.run(_run())

    def test_timeout_emits_without_recipient(self) -> None:
        emitted = []

        def _capture(alert):
            emitted.append(alert)

        async def _run() -> None:
            with patch.object(
                twitch_module, "_emit_giftsub_alert_and_instant", side_effect=_capture
            ):
                with patch.object(
                    twitch_module, "_SINGLE_GIFT_RECIPIENT_WAIT_SECONDS", 0.05
                ):
                    alert = _make_giftsub_alert()
                    waiting = twitch_module.begin_single_gift_wait(
                        alert, gifter_login="coolgifter", gifter_id="111"
                    )
                    self.assertFalse(waiting)
                    await asyncio.sleep(0.12)
                    self.assertEqual(len(emitted), 1)
                    self.assertEqual(emitted[0].recipient, "")

        asyncio.run(_run())

    def test_multi_gift_emits_immediately_via_on_sub_gift(self) -> None:
        emitted = []

        def _capture(alert):
            emitted.append(alert)

        event = SimpleNamespace(
            user_name="CoolGifter",
            user_login="coolgifter",
            user_id="111",
            is_anonymous=False,
            tier="1000",
            total=5,
        )
        data = SimpleNamespace(event=event)
        api = SimpleNamespace()
        # Bind unbound method
        api.on_sub_gift = twitch_module.Twitch_API.on_sub_gift.__get__(
            api, twitch_module.Twitch_API
        )
        api._note_event_received = MagicMock()

        async def _run() -> None:
            with patch.object(
                twitch_module, "_emit_giftsub_alert_and_instant", side_effect=_capture
            ), patch.object(
                twitch_module, "begin_single_gift_wait"
            ) as begin_wait, patch.object(
                twitch_module.alertutils, "fetch_giftsub_alert", return_value=None
            ), patch.object(
                twitch_module, "get_chatbot_manager"
            ) as get_bot, patch.object(
                twitch_module.statistics_manager, "get_statistics_manager"
            ) as get_stats:
                bot = MagicMock()
                bot.process_event.return_value = None
                get_bot.return_value = bot
                stats = MagicMock()
                get_stats.return_value = stats

                await api.on_sub_gift(data)
                begin_wait.assert_not_called()
                self.assertEqual(len(emitted), 1)
                self.assertEqual(emitted[0].gift_qty, 5)
                self.assertEqual(emitted[0].recipient, "")

        asyncio.run(_run())

    def test_single_gift_on_sub_gift_does_not_add_feed_until_emit(self) -> None:
        event = SimpleNamespace(
            user_name="CoolGifter",
            user_login="coolgifter",
            user_id="111",
            is_anonymous=False,
            tier="1000",
            total=1,
        )
        data = SimpleNamespace(event=event)
        api = SimpleNamespace()
        api.on_sub_gift = twitch_module.Twitch_API.on_sub_gift.__get__(
            api, twitch_module.Twitch_API
        )
        api._note_event_received = MagicMock()

        async def _run() -> None:
            with patch.object(
                twitch_module, "_add_twitch_alert_to_feed"
            ) as add_feed, patch.object(
                twitch_module, "_emit_giftsub_alert_and_instant"
            ) as emit, patch.object(
                twitch_module, "begin_single_gift_wait", return_value=False
            ) as begin_wait, patch.object(
                twitch_module.alertutils, "fetch_giftsub_alert", return_value=None
            ), patch.object(
                twitch_module, "get_chatbot_manager"
            ) as get_bot, patch.object(
                twitch_module.statistics_manager, "get_statistics_manager"
            ) as get_stats:
                bot = MagicMock()
                bot.process_event.return_value = None
                get_bot.return_value = bot
                stats = MagicMock()
                get_stats.return_value = stats

                await api.on_sub_gift(data)
                begin_wait.assert_called_once()
                emit.assert_not_called()
                add_feed.assert_not_called()

        asyncio.run(_run())

    def test_emit_giftsub_passes_gift_qty_and_recipient_to_feed(self) -> None:
        alert = _make_giftsub_alert(qty=1)
        alert.recipient = "ViewerName"
        alert.tier = 2

        with patch.object(twitch_module, "_queue_twitch_alert") as queue, patch.object(
            twitch_module, "_add_twitch_alert_to_feed"
        ) as add_feed, patch.object(
            twitch_module, "_send_twitch_instant_alert"
        ) as send_instant:
            twitch_module._emit_giftsub_alert_and_instant(alert)

            queue.assert_called_once_with(alert)
            add_feed.assert_called_once()
            kwargs = add_feed.call_args.kwargs
            self.assertEqual(kwargs.get("gift_qty"), 1)
            self.assertEqual(kwargs.get("recipient"), "ViewerName")
            self.assertEqual(kwargs.get("tier"), 2)
            self.assertIn("ViewerName", kwargs.get("message", ""))
            send_instant.assert_called_once()
            instant = send_instant.call_args[0][0]
            self.assertEqual(instant["gift_qty"], 1)
            self.assertEqual(instant["recipient"], "ViewerName")

    def test_emit_multi_giftsub_passes_gift_qty_to_feed(self) -> None:
        alert = _make_giftsub_alert(qty=5)
        alert.tier = 1

        with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(
            twitch_module, "_add_twitch_alert_to_feed"
        ) as add_feed, patch.object(twitch_module, "_send_twitch_instant_alert"):
            twitch_module._emit_giftsub_alert_and_instant(alert)

            kwargs = add_feed.call_args.kwargs
            self.assertEqual(kwargs.get("gift_qty"), 5)
            self.assertIsNone(kwargs.get("recipient"))
            self.assertIn("5 Tier 1 subs", kwargs.get("message", ""))

    def test_subscribe_is_gift_notes_recipient(self) -> None:
        emitted = []

        def _capture(alert):
            emitted.append(alert)

        async def _run() -> None:
            with patch.object(
                twitch_module, "_emit_giftsub_alert_and_instant", side_effect=_capture
            ):
                with patch.object(
                    twitch_module, "_SINGLE_GIFT_RECIPIENT_WAIT_SECONDS", 0.05
                ):
                    alert = _make_giftsub_alert()
                    twitch_module.begin_single_gift_wait(
                        alert, gifter_login="coolgifter", gifter_id="111"
                    )
                    # channel.subscribe gift path has no gifter; empty keys still match.
                    twitch_module.note_gift_recipient("ViewerFromSubscribe")
                    await asyncio.sleep(0.01)
                    self.assertEqual(alert.recipient, "ViewerFromSubscribe")
                    self.assertEqual(len(emitted), 1)

        asyncio.run(_run())


class ActivityFeedGiftSubPayloadTests(unittest.TestCase):
    def test_build_payload_includes_gift_qty_and_recipient(self) -> None:
        with patch(
            "modules.uiwindows.activity_feed.alertutils.resolve_chat_alert_media",
            return_value=None,
        ):
            payload = build_activity_feed_alert_payload(
                "Giftsub",
                "CoolGifter gifted a Tier 1 sub to Viewer!",
                "giftsub",
                tier=1,
                gift_qty=1,
                recipient="Viewer",
            )
        self.assertEqual(payload.get("gift_qty"), 1)
        self.assertEqual(payload.get("recipient"), "Viewer")
        self.assertEqual(payload.get("tier"), 1)

    def test_build_payload_includes_multi_gift_qty(self) -> None:
        with patch(
            "modules.uiwindows.activity_feed.alertutils.resolve_chat_alert_media",
            return_value=None,
        ):
            payload = build_activity_feed_alert_payload(
                "Giftsub",
                "CoolGifter gifted 5 Tier 2 subs!",
                "giftsub",
                tier=2,
                gift_qty=5,
            )
        self.assertEqual(payload.get("gift_qty"), 5)
        self.assertNotIn("recipient", payload)


if __name__ == "__main__":
    unittest.main()

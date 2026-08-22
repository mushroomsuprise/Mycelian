#!/usr/bin/env python3
"""Tests for single gift-sub recipient correlation on Twitch alerts."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules import alertutils
from modules import twitch as twitch_module


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
            ) as get_stats, patch.object(
                twitch_module, "_add_twitch_alert_to_feed"
            ):
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


if __name__ == "__main__":
    unittest.main()

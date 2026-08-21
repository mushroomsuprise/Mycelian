#!/usr/bin/env python3
"""Tests for Twitch ever-subscribed registry and new-sub filtering."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules import twitch as twitch_module
from modules.twitch_subscriber_registry import SubscriberRegistry


class SubscriberRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "subs.db")
        self.registry = SubscriberRegistry(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_unknown_user_is_not_known(self) -> None:
        self.assertFalse(self.registry.is_known(user_id="1", user_login="alice"))

    def test_record_by_id_and_login(self) -> None:
        self.registry.record(user_id="42", user_login="Alice", source="test")
        self.assertTrue(self.registry.is_known(user_id="42"))
        self.assertTrue(self.registry.is_known(user_login="alice"))
        self.assertTrue(self.registry.is_known(user_login="ALICE"))

    def test_login_only_seed_persists(self) -> None:
        self.registry.record(user_login="bob", source="statistics_seed")
        reloaded = SubscriberRegistry(db_path=self.db_path)
        self.assertTrue(reloaded.is_known(user_login="bob"))

    def test_session_ready_defaults_false(self) -> None:
        self.assertFalse(self.registry.is_session_ready())
        self.registry.set_session_ready(True)
        self.assertTrue(self.registry.is_session_ready())
        self.registry.reset_session_ready()
        self.assertFalse(self.registry.is_session_ready())

    def test_new_sub_alert_dedup(self) -> None:
        self.assertFalse(self.registry.was_new_sub_alerted_recently("99"))
        self.registry.mark_new_sub_alerted("99")
        self.assertTrue(self.registry.was_new_sub_alerted_recently("99"))

    def test_cancel_pending_task(self) -> None:
        async def _run() -> None:
            task = asyncio.create_task(asyncio.sleep(60))
            self.registry.store_pending_task("7", task)
            self.assertTrue(self.registry.cancel_pending("7"))
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())

        asyncio.run(_run())

    def test_seed_from_statistics_reads_sub_and_resub_only(self) -> None:
        import sqlite3

        stats_dir = os.path.join(self._tmpdir.name, "data")
        os.makedirs(stats_dir, exist_ok=True)
        stats_db = os.path.join(stats_dir, "statistics.db")
        conn = sqlite3.connect(stats_db)
        conn.execute(
            """
            CREATE TABLE user_events (
                username TEXT,
                event_type TEXT,
                amount REAL,
                alert_name TEXT,
                timestamp REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO user_events VALUES (?, ?, 0, '', 1)",
            [
                ("SeedSub", "sub"),
                ("SeedResub", "resub"),
                ("SeedGifter", "giftsub"),
                ("SeedBits", "bit"),
            ],
        )
        conn.commit()
        conn.close()

        with patch(
            "modules.path_utils.get_data_path",
            side_effect=lambda p: os.path.join(self._tmpdir.name, p),
        ):
            count = self.registry.seed_from_statistics()

        self.assertGreaterEqual(count, 2)
        self.assertTrue(self.registry.is_known(user_login="seedsub"))
        self.assertTrue(self.registry.is_known(user_login="seedresub"))
        self.assertFalse(self.registry.is_known(user_login="seedgifter"))


class SubscriberBadgeHelperTests(unittest.TestCase):
    def test_months_prefers_eventsub_info_over_id(self) -> None:
        # EventSub docs: info = months subscribed; id = badge version
        badge = SimpleNamespace(set_id="subscriber", id="12", info="16")
        self.assertEqual(twitch_module._subscriber_months_from_badges([badge]), 16)
        self.assertEqual(twitch_module._parse_badge_months(badge), 16)

    def test_months_falls_back_to_id_when_info_empty(self) -> None:
        badge = SimpleNamespace(set_id="subscriber", id="8", info="")
        self.assertEqual(twitch_module._subscriber_months_from_badges([badge]), 8)

    def test_months_helper_ignores_month_one(self) -> None:
        badge = SimpleNamespace(set_id="subscriber", id="1", info="1")
        self.assertIsNone(twitch_module._subscriber_months_from_badges([badge]))

    def test_ignores_non_subscriber_badges(self) -> None:
        badge = SimpleNamespace(set_id="moderator", id="1", info="")
        self.assertIsNone(twitch_module._subscriber_months_from_badges([badge]))


class ChatBadgeHarvestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "subs.db")
        self.registry = SubscriberRegistry(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_records_multi_month_subscriber_badge(self) -> None:
        badges = [
            SimpleNamespace(set_id="moderator", id="1", info=""),
            SimpleNamespace(set_id="subscriber", id="12", info="16"),
        ]
        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ):
            twitch_module._harvest_chat_badges_into_subscriber_registry(
                badges, user_id="99", user_login="Veteran"
            )
        self.assertTrue(self.registry.is_known(user_id="99"))
        self.assertTrue(self.registry.is_known(user_login="veteran"))

    def test_records_founder_badge(self) -> None:
        badges = [SimpleNamespace(set_id="founder", id="0", info="24")]
        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ):
            twitch_module._harvest_chat_badges_into_subscriber_registry(
                badges, user_id="5", user_login="FounderUser"
            )
        self.assertTrue(self.registry.is_known(user_id="5"))

    def test_month_one_recorded_when_no_pending_debounce(self) -> None:
        badges = [SimpleNamespace(set_id="subscriber", id="0", info="1")]
        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ):
            twitch_module._harvest_chat_badges_into_subscriber_registry(
                badges, user_id="11", user_login="Newish"
            )
        self.assertTrue(self.registry.is_known(user_id="11"))

    def test_month_one_skipped_during_pending_new_sub_debounce(self) -> None:
        pending = MagicMock()
        pending.done.return_value = False
        self.registry.store_pending_task("11", pending)
        badges = [SimpleNamespace(set_id="subscriber", id="0", info="1")]
        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ):
            twitch_module._harvest_chat_badges_into_subscriber_registry(
                badges, user_id="11", user_login="BrandNew"
            )
        self.assertFalse(self.registry.is_known(user_id="11"))
        self.registry.cancel_pending("11")
        pending.cancel.assert_called()

    def test_multi_month_cancels_pending_debounce(self) -> None:
        pending = MagicMock()
        pending.done.return_value = False
        self.registry.store_pending_task("22", pending)
        badges = [SimpleNamespace(set_id="subscriber", id="3", info="3")]
        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ):
            twitch_module._harvest_chat_badges_into_subscriber_registry(
                badges, user_id="22", user_login="Returning"
            )
        self.assertTrue(self.registry.is_known(user_id="22"))
        self.assertFalse(self.registry.has_pending("22"))
        pending.cancel.assert_called()


class NewSubGateLogicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "subs.db")
        self.registry = SubscriberRegistry(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_event(
        self,
        *,
        user_id="10",
        login="newbie",
        name="Newbie",
        is_gift=False,
        tier="1000",
    ):
        event = SimpleNamespace(
            user_id=user_id,
            user_login=login,
            user_name=name,
            is_gift=is_gift,
            tier=tier,
            message=None,
        )
        return SimpleNamespace(event=event)

    async def test_suppress_when_helix_not_ready(self) -> None:
        api = MagicMock()
        api._note_event_received = MagicMock()
        data = self._make_event()
        self.registry.set_session_ready(False)

        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ), patch.object(twitch_module, "_record_known_subscriber") as record_mock:
            await twitch_module.Twitch_API.on_new_sub(api, data)

        record_mock.assert_called()
        self.assertFalse(bool(self.registry._pending_tasks))

    async def test_suppress_known_user(self) -> None:
        api = MagicMock()
        api._note_event_received = MagicMock()
        self.registry.set_session_ready(True)
        self.registry.record(user_id="10", user_login="newbie", source="prior")
        data = self._make_event()

        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ), patch.object(twitch_module, "_record_known_subscriber"):
            await twitch_module.Twitch_API.on_new_sub(api, data)

        self.assertFalse(bool(self.registry._pending_tasks))

    async def test_gift_recipient_recorded_without_pending(self) -> None:
        api = MagicMock()
        api._note_event_received = MagicMock()
        self.registry.set_session_ready(True)
        data = self._make_event(is_gift=True)

        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ), patch.object(twitch_module, "_record_known_subscriber") as record_mock:
            await twitch_module.Twitch_API.on_new_sub(api, data)

        record_mock.assert_called()
        self.assertFalse(bool(self.registry._pending_tasks))

    async def test_unknown_user_schedules_debounce(self) -> None:
        api = MagicMock()
        api._note_event_received = MagicMock()
        api._emit_verified_new_sub = AsyncMock()
        self.registry.set_session_ready(True)
        data = self._make_event(user_id="55", login="fresh", name="Fresh")

        with patch(
            "modules.twitch_subscriber_registry.get_subscriber_registry",
            return_value=self.registry,
        ), patch.object(twitch_module, "_NEW_SUB_DEBOUNCE_SECONDS", 0.05):
            await twitch_module.Twitch_API.on_new_sub(api, data)
            self.assertTrue(bool(self.registry._pending_tasks))
            await asyncio.sleep(0.2)
            api._emit_verified_new_sub.assert_awaited()


if __name__ == "__main__":
    unittest.main()

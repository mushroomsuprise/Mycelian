#!/usr/bin/env python3
"""Statistics lookups stay correct without keeping every chatter in memory."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.statistics_manager as stats_module


class StatisticsPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

        def fake_path(relative: str) -> str:
            return os.path.join(self._tmpdir.name, relative)

        self._patches = [
            patch.object(stats_module, "get_data_path", side_effect=fake_path),
            patch.object(stats_module, "get_data", return_value=None),
        ]
        for item in self._patches:
            item.start()
        self.manager = stats_module.StatisticsManager()

    def tearDown(self) -> None:
        self.manager._username_backfill_stop.set()
        self.manager._close_statistics_db()
        for item in self._patches:
            item.stop()
        self._tmpdir.cleanup()

    def test_user_stats_round_trip_without_resident_map(self) -> None:
        self.manager._spill_raw_user_map(
            "chat.user_stats",
            {
                "ViewerOne": {
                    "twitch_messages_received": 4,
                    "total_messages": 4,
                    "first_seen": 1.0,
                    "last_seen": 2.0,
                }
            },
        )
        self.assertEqual(self.manager.data.chat.user_stats, {})
        stats = self.manager.get_user_statistics("viewerone")
        self.assertEqual(stats["chat"]["total_messages"], 4)
        self.assertIn("ViewerOne", self.manager.get_all_tracked_usernames())

    def test_new_events_store_lowercase_username(self) -> None:
        self.manager._record_event("ViewerOne", "chat_message", 1.0, "")
        self.manager._flush_pending_events()
        events = self.manager.get_user_events("VIEWERONE", limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["username"], "ViewerOne")
        self.assertEqual(events[0]["username_lower"], "viewerone")
        self.manager._backfill_username_lower_loop()
        again = self.manager.get_user_events("viewerone", limit=5)
        self.assertEqual(again[0]["event_type"], "chat_message")

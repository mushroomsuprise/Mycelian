#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot saves, greeting flags and repeat threads (DBX-04, DBX-12, TH-11, TH-12, RT-12)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from modules import chatbot_manager as cm
from modules.chatbot_core import ChatCommand, ChatEvent, EventType

_ALL_PATHS = [
    "BotData/Commands",
    "BotData/Events",
    "BotData/Quotes",
    "BotData/Greetings",
    "BotData/Settings",
]


class _DB:
    def __init__(self):
        self.store: dict = {}
        self.writes: list = []
        self.reads: list = []
        self.fail_paths: set = set()
        self.lock = threading.Lock()

    def get_data(self, path, request_etag=False):
        with self.lock:
            self.reads.append(path)
            return json.loads(json.dumps(self.store.get(path, {})))

    def get_data_checked(self, path):
        return self.get_data(path), True

    def set_data(self, path, data):
        with self.lock:
            self.writes.append(path)
            if path in self.fail_paths:
                return False
            self.store[path] = json.loads(json.dumps(data))
            return True


_REAL_FLAG_MANAGER = cm.GreetingFlagManager


class _ManagerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = _DB()
        flag_path = os.path.join(self.tmp.name, "greeting_flags.json")
        self._patches = [
            patch("modules.database_manager.get_data", self.db.get_data),
            patch("modules.database_manager.get_data_checked", self.db.get_data_checked),
            patch("modules.database_manager.set_data", self.db.set_data),
            patch("modules.statistics_manager.get_statistics_manager", MagicMock()),
            patch.object(cm, "GreetingFlagManager", lambda: _REAL_FLAG_MANAGER(flag_path)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        # Finish queued background saves while the fake database is still patched in.
        for manager in getattr(self, "_made", []):
            manager.flush_pending_saves()
            manager.cleanup()
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _manager(self) -> cm.ChatbotManager:
        manager = cm.ChatbotManager()
        self._made = getattr(self, "_made", []) + [manager]
        manager._save_data_now()
        self.db.writes.clear()
        return manager


class ChatbotSaveTests(_ManagerCase):
    def test_load_reads_commands_once(self):
        self.db.store["BotData/Commands"] = [
            ChatCommand(command_id="c1", command_name="hi", response_text="hey").to_dict()
        ]
        manager = self._manager()
        self.assertEqual(self.db.reads.count("BotData/Commands"), 1)
        self.assertIn("c1", manager.commands)

    def test_command_use_writes_only_the_changed_blob(self):
        manager = self._manager()
        manager.add_command(ChatCommand(command_id="c1", command_name="hi", response_text="hey"))
        manager.flush_pending_saves()
        self.assertEqual(self.db.writes, ["BotData/Commands"])

        self.db.writes.clear()
        result = manager.process_chat_message({"message": "!hi", "username": "viewer"})
        self.assertEqual(result[0], "hey")
        manager.flush_pending_saves()
        self.assertEqual(self.db.writes, ["BotData/Commands"])

        # Stored content is exactly what a full save would write.
        for path, data in manager._build_save_payloads():
            self.assertEqual(self.db.store[path], json.loads(json.dumps(data)))

        self.db.writes.clear()
        manager._save_data_now()
        self.assertEqual(self.db.writes, [])

    def test_failed_write_is_retried_and_backend_switch_rewrites_all(self):
        manager = self._manager()
        self.db.writes.clear()
        self.db.fail_paths = {"BotData/Events"}
        manager.add_event(ChatEvent(event_id="e1", name="Follow", event_type=EventType.FOLLOW, response_text="ty"))
        manager.flush_pending_saves()
        self.assertEqual(self.db.writes, ["BotData/Events"])

        self.db.fail_paths = set()
        self.db.writes.clear()
        manager._save_data_now()
        self.assertEqual(self.db.writes, ["BotData/Events"])

        self.db.writes.clear()
        with patch.object(cm, "_database_backend_token", return_value=("other",)):
            manager._save_data_now()
        self.assertEqual(self.db.writes, _ALL_PATHS)

    def test_save_while_other_threads_mutate(self):
        manager = self._manager()

        def churn():
            for i in range(300):
                manager.add_quote(f"quote {i}", "a", "b")

        worker = threading.Thread(target=churn)
        with patch.object(cm, "notify_critical") as notify:
            worker.start()
            while worker.is_alive():
                manager._save_data_now()
            worker.join(5)
            manager.flush_pending_saves()
            notify.assert_not_called()
        self.assertEqual(len(self.db.store["BotData/Quotes"]), 300)


class GreetingFlagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "flags.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reset_drops_entries_without_changing_who_is_greeted(self):
        flags = cm.GreetingFlagManager(self.path)
        flags.mark_user_greeted("1")
        flags.mark_user_greeted("2")
        self.assertFalse(flags.should_greet_user("1"))
        flags.reset_all_flags()
        self.assertEqual(flags.greeted_flags, {})
        self.assertTrue(flags.should_greet_user("1"))
        self.assertTrue(flags.should_greet_user("3"))

    def test_load_drops_false_entries(self):
        with open(self.path, "w") as f:
            json.dump({"greeted_flags": {"1": True, "2": False}, "last_reset_time": 5}, f)
        flags = cm.GreetingFlagManager(self.path)
        self.assertEqual(flags.greeted_flags, {"1": True})
        self.assertFalse(flags.should_greet_user("1"))
        self.assertTrue(flags.should_greet_user("2"))

    def test_save_snapshot_survives_concurrent_marks(self):
        flags = cm.GreetingFlagManager(self.path)

        def mark():
            for i in range(30000):
                flags.mark_user_greeted(str(i))

        worker = threading.Thread(target=mark)
        with patch.object(cm.logger, "error") as log_error:
            worker.start()
            while worker.is_alive():
                flags._save_flags()
            worker.join(5)
            log_error.assert_not_called()
        flags.force_save()
        with open(self.path) as f:
            stored = json.load(f)
        self.assertEqual(stored["greeted_flags"], flags.greeted_flags)
        self.assertEqual(stored["last_reset_time"], flags.last_reset_time)


class RepeatingEventTests(_ManagerCase):
    def test_quick_toggle_does_not_double_post(self):
        posts: list = []
        lock = threading.Lock()

        def deliver(*_args, **_kwargs):
            with lock:
                posts.append(time.monotonic())
            return True

        manager = self._manager()
        with (
            patch("modules.chatbot.deliver_chatbot_output", deliver),
            patch("modules.chatbot.is_chatbot_connected", return_value=True),
        ):
            event = ChatEvent(
                event_id="e1",
                name="Tick",
                event_type=EventType.INTERVAL,
                interval=0.4,
                response_text="tick",
            )
            manager.add_event(event)
            time.sleep(0.15)
            manager.toggle_item("e1", False)
            manager.toggle_item("e1", True)
            restarted = time.monotonic()
            time.sleep(1.0)
            manager.toggle_item("e1", False)
            time.sleep(0.5)
        after = [t for t in posts if t >= restarted - 0.01]
        # One post on restart, then one per 0.4 s: 3 within the second.
        self.assertGreaterEqual(len(after), 2)
        self.assertLessEqual(len(after), 3)
        gaps = [b - a for a, b in zip(after, after[1:])]
        for gap in gaps:
            self.assertGreater(gap, 0.3)


if __name__ == "__main__":
    unittest.main()

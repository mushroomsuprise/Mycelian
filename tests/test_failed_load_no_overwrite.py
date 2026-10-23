#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""A failed read never lets a manager overwrite good stored data with defaults.

Chatbot, polls, giveaway, connectors and the connectors layout load with
get_data_checked. A path whose load failed is re-read before it is written:
still failing means no write; readable again means the stored copy is the base
and only later in-memory changes go on top. Normal loads behave as before.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules import chatbot_manager as cm
from modules import connector_layout_store as layout_store
from modules.chatbot_core import ChatCommand, Quote
from modules.giveaway_manager import GiveawayManager
from modules.poll_manager import PollManager


class _FlakyDB:
    """In-memory backend whose reads can fail per path."""

    def __init__(self, store=None):
        self.store = copy.deepcopy(store or {})
        self.failing: set = set()
        self.writes: list = []
        self.lock = threading.Lock()

    def get_data(self, path, request_etag=False):
        data, _ok = self.get_data_checked(path)
        return data

    def get_data_checked(self, path):
        with self.lock:
            if path in self.failing:
                return {}, False
            return copy.deepcopy(self.store.get(path, {})), True

    def set_data(self, path, data):
        with self.lock:
            self.writes.append(path)
            self.store[path] = json.loads(json.dumps(data))
            return True

    def patches(self):
        return [
            patch("modules.database_manager.get_data", self.get_data),
            patch("modules.database_manager.get_data_checked", self.get_data_checked),
            patch("modules.database_manager.set_data", self.set_data),
        ]


class _Patched(unittest.TestCase):
    def start(self, db: _FlakyDB, extra=()):
        self._patches = [*db.patches(), *extra]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(getattr(self, "_patches", [])):
            p.stop()


_GOOD_COMMANDS = [ChatCommand(command_id="old", command_name="old", response_text="kept").to_dict()]
_GOOD_SETTINGS = {"default_greeting_text": "hi {username} from storage", "quotes_enabled": True}
_GOOD_QUOTES = [Quote(quote_id="q1", text="stored", quote_number=1).to_dict()]

_REAL_FLAGS = cm.GreetingFlagManager


class ChatbotFailedLoadTests(_Patched):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = _FlakyDB({
            "BotData/Commands": _GOOD_COMMANDS,
            "BotData/Settings": _GOOD_SETTINGS,
            "BotData/Quotes": _GOOD_QUOTES,
        })
        flag_path = os.path.join(self.tmp.name, "flags.json")
        self.start(self.db, [
            patch("modules.statistics_manager.get_statistics_manager", MagicMock()),
            patch.object(cm, "GreetingFlagManager", lambda: _REAL_FLAGS(flag_path)),
        ])

    def tearDown(self):
        super().tearDown()
        self.tmp.cleanup()

    def test_normal_load_is_unchanged(self):
        manager = cm.ChatbotManager()
        self.assertEqual(manager._unreadable_paths, {})
        self.assertIn("old", manager.commands)
        self.assertEqual(manager.default_greeting_text, "hi {username} from storage")
        manager._save_data_now()
        self.assertEqual(
            sorted(self.db.writes),
            sorted(["BotData/Commands", "BotData/Events", "BotData/Quotes", "BotData/Greetings", "BotData/Settings"]),
        )
        self.assertEqual(self.db.store["BotData/Commands"], _GOOD_COMMANDS)

    def test_failed_path_is_not_overwritten_then_merged_when_readable(self):
        self.db.failing = {"BotData/Commands", "BotData/Settings", "BotData/Quotes"}
        manager = cm.ChatbotManager()
        self.assertEqual(manager.commands, {})
        manager.add_command(ChatCommand(command_id="new", command_name="new", response_text="added"))
        manager.update_default_greeting("@{username} edited")
        manager.add_quote("fresh", "a", "b")
        manager.flush_pending_saves()
        self.assertEqual(self.db.store["BotData/Commands"], _GOOD_COMMANDS)
        self.assertEqual(self.db.store["BotData/Settings"], _GOOD_SETTINGS)
        self.assertEqual(self.db.store["BotData/Quotes"], _GOOD_QUOTES)

        self.db.failing = set()
        manager._save_data_now()
        ids = [c["command_id"] for c in self.db.store["BotData/Commands"]]
        self.assertEqual(ids, ["old", "new"])
        self.assertIn("old", manager.commands)
        settings = self.db.store["BotData/Settings"]
        self.assertEqual(settings["default_greeting_text"], "@{username} edited")
        self.assertTrue(settings["quotes_enabled"])
        numbers = sorted(q["quote_number"] for q in self.db.store["BotData/Quotes"])
        self.assertEqual(numbers, [1, 2])
        self.assertEqual(manager._unreadable_paths, {})


class PollFailedLoadTests(_Patched):
    STORED = {
        "templates": {
            "poll": {"config": {"categories": [{"id": "a", "name": "Yes", "keyword": "#yes"}]}},
            "other": {"config": {"categories": [{"id": "b", "name": "No", "keyword": "#no"}]}},
        }
    }

    def setUp(self):
        self.db = _FlakyDB({"BotData/Polls": self.STORED})
        self.start(self.db, [
            patch("modules.poll_manager._register_shutdown_flush", lambda *_a: None),
            patch.object(PollManager, "_emit", lambda *_a, **_k: None),
        ])

    def test_normal_load_is_unchanged(self):
        manager = PollManager()
        self.assertFalse(manager._load_failed)
        self.assertEqual(manager.get_config("poll")["categories"][0]["name"], "Yes")

    def test_no_write_while_unreadable_then_stored_templates_restored(self):
        self.db.failing = {"BotData/Polls"}
        manager = PollManager()
        manager.ensure_template("poll")
        manager.ensure_template("mine")
        manager.update_config("mine", {"duration_seconds": 30})
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.store["BotData/Polls"], self.STORED)

        self.db.failing = set()
        manager.update_config("mine", {"duration_seconds": 45})
        stored = self.db.store["BotData/Polls"]["templates"]
        self.assertEqual(sorted(stored), ["mine", "other", "poll"])
        self.assertEqual(stored["poll"]["config"]["categories"][0]["name"], "Yes")
        self.assertEqual(stored["mine"]["config"]["duration_seconds"], 45)
        self.assertEqual(manager.get_config("poll")["categories"][0]["name"], "Yes")


class GiveawayFailedLoadTests(_Patched):
    STORED = {"keyword": "!join", "num_winners": 2}

    def test_normal_load_is_unchanged(self):
        db = _FlakyDB({"BotData/Giveaway": self.STORED})
        self.start(db)
        manager = GiveawayManager()
        self.assertEqual(manager.get_config()["keyword"], "!join")
        manager.set_config_field("num_winners", 3)
        self.assertEqual(db.store["BotData/Giveaway"], manager.get_config())

    def test_no_write_while_unreadable_then_merged(self):
        db = _FlakyDB({"BotData/Giveaway": self.STORED})
        db.failing = {"BotData/Giveaway"}
        self.start(db)
        manager = GiveawayManager()
        manager.set_config_field("num_winners", 5)
        self.assertEqual(db.writes, [])
        self.assertEqual(db.store["BotData/Giveaway"], self.STORED)

        db.failing = set()
        manager.flush()
        stored = db.store["BotData/Giveaway"]
        self.assertEqual(stored["keyword"], "!join")
        self.assertEqual(stored["num_winners"], 5)
        self.assertEqual(manager.get_config(), stored)


class ConnectorFailedLoadTests(_Patched):
    def _manager(self):
        from modules.connector_manager import ConnectorManager

        manager = ConnectorManager()
        manager._serialize_connector = lambda c: {"connector_id": c.connector_id, "name": c.name}
        manager._deserialize_connector = lambda d: SimpleNamespace(
            connector_id=d["connector_id"], name=d["name"], enabled=False
        )
        return manager

    def test_no_overwrite_while_unreadable_then_merged(self):
        db = _FlakyDB({"connectors": {"c1": {"connector_id": "c1", "name": "stored"}}})
        db.failing = {"connectors"}
        self.start(db)
        manager = self._manager()
        manager.load_connectors()
        self.assertEqual(manager.connectors, {})
        manager.connectors["c2"] = SimpleNamespace(connector_id="c2", name="new", enabled=False)
        manager.save_connectors()
        self.assertEqual(db.writes, [])
        self.assertEqual(list(db.store["connectors"]), ["c1"])

        db.failing = set()
        manager.flush_blocked_save()
        self.assertEqual(list(db.store["connectors"]), ["c1", "c2"])
        self.assertEqual(sorted(manager.connectors), ["c1", "c2"])

    def test_normal_load_and_save_are_unchanged(self):
        db = _FlakyDB({"connectors": {"c1": {"connector_id": "c1", "name": "stored"}}})
        self.start(db)
        manager = self._manager()
        manager.load_connectors()
        del manager.connectors["c1"]
        manager.save_connectors()
        self.assertEqual(db.store["connectors"], {})


class LayoutFailedLoadTests(_Patched):
    STORED = {
        "version": 1,
        "root_items": [{"kind": "folder", "id": "f1"}, {"kind": "connector", "id": "c3"}],
        "folders": {"f1": {"name": "Scenes", "connector_ids": ["c1", "c2"]}},
    }

    def tearDown(self):
        super().tearDown()
        layout_store._layout_read_failed = False

    def test_normal_round_trip_is_unchanged(self):
        db = _FlakyDB({"connectors_layout": self.STORED})
        self.start(db)
        layout = layout_store.load_layout()
        self.assertTrue(layout_store.save_layout(layout))
        self.assertEqual(db.store["connectors_layout"], self.STORED)

    def test_folders_survive_a_failed_read(self):
        db = _FlakyDB({"connectors_layout": self.STORED})
        db.failing = {"connectors_layout"}
        self.start(db)
        existing = {"c1", "c2", "c3", "c4"}
        layout = layout_store.reconcile_layout(layout_store.load_layout(), existing)
        self.assertFalse(layout_store.save_layout(layout))
        self.assertEqual(db.writes, [])

        db.failing = set()
        layout, new_id = layout_store.merge_into_new_folder(layout, "c4", "c2", "Mine")
        self.assertTrue(layout_store.save_layout(layout))
        saved = db.store["connectors_layout"]
        self.assertEqual(saved["folders"]["f1"]["connector_ids"], ["c1"])
        self.assertEqual(saved["folders"][new_id]["connector_ids"], ["c2", "c4"])
        root_ids = [item["id"] for item in saved["root_items"]]
        self.assertEqual(root_ids[:2], ["f1", "c3"])
        self.assertIn(new_id, root_ids)
        self.assertNotIn("c4", root_ids)


if __name__ == "__main__":
    unittest.main()

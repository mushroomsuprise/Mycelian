#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Poll votes and giveaway config writes stay off the caller's thread (DBX-05, TH-06)."""

from __future__ import annotations

import copy
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from modules import giveaway_manager as giveaway_module
from modules import poll_manager as poll_module
from modules.giveaway_manager import GiveawayManager
from modules.poll_manager import PollManager

_STORED = {
    "templates": {
        "poll": {
            "config": {
                "categories": [
                    {"id": "a", "name": "Yes", "keyword": "#yes"},
                    {"id": "b", "name": "No", "keyword": "#no"},
                ],
                "allow_vote_switch": True,
            }
        }
    }
}


class _FakeDB:
    """Records every set_data payload; optional delay or gate per write."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.gate: threading.Event | None = None
        self.in_write = threading.Event()
        self.writes: list = []
        self._lock = threading.Lock()

    def get_data(self, path, request_etag=False):
        return copy.deepcopy(_STORED) if path == poll_module.DATA_PATH else {}

    def set_data(self, path, payload):
        self.in_write.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.writes.append((path, copy.deepcopy(payload)))
        return True

    def last(self, path):
        with self._lock:
            for written_path, payload in reversed(self.writes):
                if written_path == path:
                    return payload
        return None


class _LegacyPollManager(PollManager):
    """Pre-change behavior: every accepted vote writes synchronously."""

    def _schedule_vote_persist_locked(self) -> None:
        self._persist()


def _votes_script():
    msgs = []
    for i in range(60):
        msgs.append({"userid": str(i % 25), "username": f"u{i % 25}", "message": "#yes" if i % 3 else "#no"})
    return msgs


class PollWriteBehindTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        self._patches = [
            patch("modules.database_manager.get_data", self.db.get_data),
            patch("modules.database_manager.get_data_checked", lambda path: (self.db.get_data(path), True)),
            patch("modules.database_manager.set_data", self.db.set_data),
            patch.object(poll_module, "_register_shutdown_flush", lambda *_a: None),
            patch.object(PollManager, "_emit", lambda *_a, **_k: None),
            patch.object(PollManager, "_record_stats", lambda *_a, **_k: None),
            patch.object(PollManager, "_announce", lambda *_a, **_k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _run(self, cls, *, stop: bool):
        manager = cls()
        manager._now = lambda: 1000.0
        manager.start_poll("poll")
        for msg in _votes_script():
            manager.try_register_vote(msg)
        if stop:
            manager.stop_poll("poll")
        manager.flush()
        return manager

    def test_final_stored_state_matches_synchronous_writes(self):
        self._run(_LegacyPollManager, stop=False)
        legacy_open = self.db.last(poll_module.DATA_PATH)
        self.db.writes.clear()
        self._run(PollManager, stop=False)
        self.assertEqual(self.db.last(poll_module.DATA_PATH), legacy_open)

        self.db.writes.clear()
        self._run(_LegacyPollManager, stop=True)
        legacy_ended = self.db.last(poll_module.DATA_PATH)
        self.db.writes.clear()
        self._run(PollManager, stop=True)
        self.assertEqual(self.db.last(poll_module.DATA_PATH), legacy_ended)

    def test_votes_do_not_wait_for_the_database(self):
        manager = PollManager()
        manager._now = lambda: 1000.0
        manager.start_poll("poll")
        self.db.delay = 0.2
        started = time.monotonic()
        for msg in _votes_script():
            self.assertIsInstance(manager.try_register_vote(msg), bool)
        self.assertLess(time.monotonic() - started, 0.5)
        manager.flush()
        stored = self.db.last(poll_module.DATA_PATH)
        with manager._lock:
            _seq, expected = manager._build_payload_locked()
        self.assertEqual(stored, expected)
        self.assertEqual(sum(stored["templates"]["poll"]["runtime"]["votes"].values()), 25)

    def test_overlay_state_is_not_blocked_by_an_inflight_vote_write(self):
        manager = PollManager()
        manager._now = lambda: 1000.0
        manager.start_poll("poll")
        self.db.gate = threading.Event()
        self.db.in_write.clear()
        manager.try_register_vote({"userid": "1", "username": "a", "message": "#yes"})
        self.assertTrue(self.db.in_write.wait(2))
        started = time.monotonic()
        state = manager.get_overlay_state("poll")
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertEqual(sum(row["votes"] for row in state["options"]), 1)
        self.db.gate.set()
        manager.flush()

    def test_stop_poll_leaves_db_current_and_flush_drains(self):
        manager = PollManager()
        manager._now = lambda: 1000.0
        manager.start_poll("poll")
        with patch.object(poll_module, "_VOTE_WRITE_COALESCE_SECONDS", 30):
            manager.try_register_vote({"userid": "1", "username": "a", "message": "#no"})
            manager.flush()
            runtime = self.db.last(poll_module.DATA_PATH)["templates"]["poll"]["runtime"]
            self.assertEqual(runtime["votes"], {"a": 0, "b": 1})
            manager.try_register_vote({"userid": "2", "username": "b", "message": "#yes"})
            manager.stop_poll("poll")
        stored = self.db.last(poll_module.DATA_PATH)["templates"]["poll"]
        self.assertNotIn("runtime", stored)
        self.assertEqual([c["votes"] for c in stored["config"]["categories"]], [1, 1])

    def test_older_payload_never_lands_after_newer(self):
        manager = PollManager()
        with manager._lock:
            old = manager._build_payload_locked()
            new = manager._build_payload_locked()
        manager._write_payload(*new)
        manager._write_payload(*old)
        self.assertEqual(len(self.db.writes), 1)


class GiveawayConfigWriteTests(unittest.TestCase):
    def test_entries_are_not_blocked_by_a_config_write(self):
        db = _FakeDB()
        db.gate = threading.Event()
        with (
            patch("modules.database_manager.get_data", lambda *_a, **_k: {}),
            patch("modules.database_manager.get_data_checked", lambda *_a, **_k: ({}, True)),
            patch("modules.database_manager.set_data", db.set_data),
            patch.object(GiveawayManager, "_emit_event", lambda *_a, **_k: None),
            patch("modules.statistics_manager.get_statistics_manager", MagicMock()),
        ):
            manager = GiveawayManager()
            manager.set_config_field("keyword", "!join")
            db.gate.set()
            ok, _ = manager.start_giveaway()
            self.assertTrue(ok)
            db.gate = threading.Event()
            db.in_write.clear()
            writer = threading.Thread(
                target=manager.update_config, args=({"num_winners": 3},)
            )
            writer.start()
            self.assertTrue(db.in_write.wait(2))
            started = time.monotonic()
            entered = manager.try_register_entry(
                {"message": "!join", "username": "viewer", "userid": "9"}
            )
            self.assertLess(time.monotonic() - started, 0.05)
            self.assertTrue(entered)
            db.gate.set()
            writer.join(2)
            self.assertEqual(db.last(giveaway_module.DATA_PATH), manager.get_config())
            self.assertEqual(db.last(giveaway_module.DATA_PATH)["num_winners"], 3)


if __name__ == "__main__":
    unittest.main()

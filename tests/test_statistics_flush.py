#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stats event batches flush off the producer thread; unchanged user rows are not rewritten."""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.statistics_manager as stats_module  # noqa: E402


class _ManagerFixture:
    def __init__(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = self.tmpdir.name

        def fake_path(relative: str) -> str:
            return os.path.join(tmp, relative)

        self._patches = [
            patch.object(stats_module, "get_data_path", side_effect=fake_path),
            patch.object(stats_module, "get_data", return_value=None),
            patch.object(stats_module, "set_data", return_value=True),
        ]
        for item in self._patches:
            item.start()
        self.manager = stats_module.StatisticsManager()
        self.db_path = self.manager._stats_db_path

    def rows(self, sql: str):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def close(self) -> None:
        self.manager._username_backfill_stop.set()
        self.manager._close_statistics_db()
        for item in self._patches:
            item.stop()
        self.tmpdir.cleanup()


class EventBatchFlushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ManagerFixture()
        self.manager = self.fx.manager

    def tearDown(self) -> None:
        self.fx.close()

    def test_full_batch_is_written_by_the_flusher_not_the_producer(self) -> None:
        writers: list[str] = []
        done = threading.Event()
        original = self.manager._flush_event_rows

        def spy(rows):
            writers.append(threading.current_thread().name)
            original(rows)
            done.set()

        with patch.object(self.manager, "_flush_event_rows", side_effect=spy):
            started = time.monotonic()
            with self.manager._data_lock:  # like increment_twitch_messages
                for i in range(50):
                    self.manager._record_event(f"user{i}", "chat_message", 1.0, "")
                self.assertEqual(writers, [], "producer must not write under _data_lock")
            self.assertTrue(done.wait(2.0))
            elapsed = time.monotonic() - started
        self.assertEqual(writers, ["StatsEventFlush"])
        # Batch-full skips the 1 s coalescing wait.
        self.assertLess(elapsed, 0.8)
        count = self.fx.rows("SELECT COUNT(*) FROM user_events")[0][0]
        self.assertEqual(count, 50)

    def test_ten_thousand_events_are_stored_identically_and_in_order(self) -> None:
        expected = []
        clock = [1_700_000_000.0]

        def fake_time():
            clock[0] += 0.001
            return clock[0]

        with patch.object(stats_module.time, "time", side_effect=fake_time):
            for i in range(10_000):
                user = f"Viewer{i % 37}"
                kind = ("chat_message", "bit", "follow")[i % 3]
                amount = float(i % 11)
                self.manager._record_event(user, kind, amount, f"a{i % 5}")
                expected.append((user, kind, amount, f"a{i % 5}", round(clock[0], 6)))
        self.manager._flush_pending_events()
        got = [
            (u, k, a, n, round(t, 6))
            for (u, k, a, n, t) in self.fx.rows(
                "SELECT username, event_type, amount, alert_name, timestamp "
                "FROM user_events ORDER BY id"
            )
        ]
        self.assertEqual(got, expected)

    def test_without_a_flusher_thread_full_batches_flush_inline(self) -> None:
        self.manager._event_batch_stop.set()
        self.manager._event_flush_now.set()
        self.manager._event_flush_wake.set()
        self.manager._event_flush_thread.join(2.0)
        self.assertFalse(self.manager._event_flush_thread.is_alive())
        for i in range(50):
            self.manager._record_event("solo", "chat_message", 1.0, "")
        self.assertEqual(self.manager._event_batch, [])
        self.assertEqual(self.fx.rows("SELECT COUNT(*) FROM user_events")[0][0], 50)


def _lifetime(fx: _ManagerFixture):
    return fx.rows(
        "SELECT username_lower, category, username, data_json FROM user_lifetime "
        "ORDER BY category, username_lower"
    )


class UnchangedUserRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ManagerFixture()
        self.manager = self.fx.manager

    def tearDown(self) -> None:
        self.fx.close()

    def _count_upserts(self):
        calls = []
        original = self.manager._get_stats_db_connection

        def counting(*args, **kwargs):
            conn = original(*args, **kwargs)
            if conn is None:
                return conn

            class _Proxy:
                def __getattr__(self, name):
                    return getattr(conn, name)

                def executemany(self, sql, rows):
                    rows = list(rows)
                    if "user_lifetime" in sql:
                        calls.append(len(rows))
                    return conn.executemany(sql, rows)

            return _Proxy()

        return calls, counting

    def test_second_spill_of_the_same_map_writes_nothing(self) -> None:
        users = {"Alpha": {"wins": 1}, "Beta": {"wins": 2}}
        calls, counting = self._count_upserts()
        with patch.object(self.manager, "_get_stats_db_connection", side_effect=counting), \
                patch.object(self.manager, "_return_stats_db_connection",
                             side_effect=lambda conn: conn and conn.close()):
            self.manager._spill_raw_user_map("giveaways.user_wins", users)
            self.manager._spill_raw_user_map("giveaways.user_wins", users)
            users["Beta"] = {"wins": 3}
            self.manager._spill_raw_user_map("giveaways.user_wins", users)
            self.manager._spill_raw_user_map("giveaways.user_wins", {"BETA": {"wins": 3}})
        # first: both; second: none; third: only Beta; fourth: display-name change.
        self.assertEqual(calls, [2, 1, 1])
        self.assertEqual(
            _lifetime(self.fx),
            [
                ("alpha", "giveaways.user_wins", "Alpha", '{"wins": 1}'),
                ("beta", "giveaways.user_wins", "BETA", '{"wins": 3}'),
            ],
        )

    def test_case_colliding_names_in_one_map_keep_last_wins(self) -> None:
        self.manager._spill_raw_user_map("chat.user_stats", {"Foo": {"total_messages": 1}})
        self.manager._spill_raw_user_map(
            "chat.user_stats", {"Foo": {"total_messages": 2}, "foo": {"total_messages": 1}}
        )
        self.assertEqual(
            _lifetime(self.fx),
            [("foo", "chat.user_stats", "foo", '{"total_messages": 1}')],
        )

    def test_randomized_saves_match_always_rewriting(self) -> None:
        reference = _ManagerFixture()
        try:
            rng = random.Random(1234)
            clock = [1_700_000_000.0]
            names = [f"Chatter{i}" for i in range(40)] + ["chatter3", "CHATTER7"]
            with patch.object(stats_module.time, "time", side_effect=lambda: clock[0]):
                for step in range(30):
                    for _ in range(rng.randint(0, 25)):
                        clock[0] += 1.0
                        name = rng.choice(names)
                        kind = rng.randrange(4)
                        for m in (self.manager, reference.manager):
                            if kind == 0:
                                m.increment_resubs(name, "resub")
                            elif kind == 1:
                                m.record_giveaway_winner(name)
                            else:
                                m.increment_twitch_messages(name)
                    if step == 17:
                        for m in (self.manager, reference.manager):
                            m.reset_statistics("chat")
                    reference.manager._spilled_user_digests.clear()  # old: rewrite all
                    self.manager._save_to_database()
                    reference.manager._save_to_database()
                    self.assertEqual(_lifetime(self.fx), _lifetime(reference), step)
            self.assertTrue(_lifetime(self.fx))
        finally:
            reference.close()

    def test_close_rewrites_every_resident_user(self) -> None:
        self.manager.increment_twitch_messages("Gamma")
        self.manager._save_to_database()
        stored = _lifetime(self.fx)
        conn = sqlite3.connect(self.fx.db_path)
        conn.execute("UPDATE user_lifetime SET data_json = '{}'")
        conn.commit()
        conn.close()
        self.manager.save_on_close()
        self.assertEqual(_lifetime(self.fx), stored)


if __name__ == "__main__":
    unittest.main()

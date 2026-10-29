#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Subscriber registry writes leave the EventSub loop and keep the same rows (DBX-06, RT-09)."""

from __future__ import annotations

import asyncio
import itertools
import os
import random
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from modules.twitch_subscriber_registry import SubscriberRegistry


def _script(seed: int = 7):
    rng = random.Random(seed)
    ops = []
    for _ in range(600):
        uid = rng.choice([None, *[str(n) for n in range(30)]])
        login = rng.choice([None, *[f"User{n}" for n in range(30)]])
        source = rng.choice(["", "chat.badge.subscriber", "helix_snapshot"])
        months = rng.choice([None, 1, 3, 12])
        if rng.random() < 0.1:
            batch = [(str(rng.randrange(40)), f"user{rng.randrange(40)}", source, months) for _ in range(5)]
            ops.append(("many", batch))
        else:
            ops.append(("one", (uid, login, source, months)))
    return ops


def _apply(registry: SubscriberRegistry, ops) -> None:
    for kind, args in ops:
        if kind == "many":
            registry.record_many(args)
        else:
            uid, login, source, months = args
            registry.record(user_id=uid, user_login=login, source=source, cumulative_months=months)


def _dump(path: str):
    conn = sqlite3.connect(path)
    try:
        subs = conn.execute("SELECT * FROM known_subscribers ORDER BY user_id").fetchall()
        logins = conn.execute("SELECT * FROM known_subscriber_logins ORDER BY user_login").fetchall()
        return subs, logins
    finally:
        conn.close()


class SubscriberRegistryWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _fresh(self, name: str) -> SubscriberRegistry:
        return SubscriberRegistry(db_path=os.path.join(self.tmp.name, name))

    def test_loop_path_stores_the_same_rows_as_direct_writes(self):
        ops = _script()

        def registry_clock():
            # The registry's own clock: patching time.time itself would also hand values
            # to every other thread, and the two runs would no longer match.
            ticks = itertools.count(1000)
            fake = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("__")})
            fake.time = lambda: float(next(ticks))
            return patch("modules.twitch_subscriber_registry.time", fake)

        direct = self._fresh("direct.db")
        with registry_clock():
            _apply(direct, ops)
        direct.flush()

        queued = self._fresh("queued.db")

        async def on_loop():
            _apply(queued, ops)

        with registry_clock():
            asyncio.run(on_loop())
        queued.flush()

        self.assertEqual(_dump(os.path.join(self.tmp.name, "queued.db")), _dump(os.path.join(self.tmp.name, "direct.db")))
        self.assertTrue(_dump(os.path.join(self.tmp.name, "direct.db"))[0])

    def test_record_on_a_loop_does_not_wait_for_sqlite(self):
        registry = self._fresh("slow.db")
        original = registry._execute_write
        gate = threading.Event()

        def slow(entry):
            gate.wait(2)
            original(entry)

        registry._execute_write = slow

        async def on_loop():
            started = time.monotonic()
            for n in range(20):
                registry.record(user_id=str(n), user_login=f"u{n}", source="chat")
            return time.monotonic() - started

        elapsed = asyncio.run(on_loop())
        self.assertLess(elapsed, 0.2)
        self.assertTrue(registry.is_known(user_id="19"))
        gate.set()
        registry.flush()
        subs, _ = _dump(os.path.join(self.tmp.name, "slow.db"))
        self.assertEqual(len(subs), 20)

    def test_helix_sync_waiters_do_not_hold_executor_threads(self):
        registry = self._fresh("helix.db")
        calls = []

        async def fake_fetch():
            calls.append(1)
            await asyncio.sleep(0.2)
            return True

        registry._fetch_helix_subscribers = fake_fetch

        def executor_threads():
            # The loop's default executor names its threads asyncio_N. Counting every
            # thread would also catch unrelated threads still winding down from other tests.
            return {t for t in threading.enumerate() if t.name.startswith("asyncio_")}

        async def main():
            before = executor_threads()
            results = await asyncio.gather(*(registry.sync_from_helix() for _ in range(20)))
            return results, len(executor_threads() - before)

        results, extra_threads = asyncio.run(main())
        self.assertTrue(all(results))
        self.assertEqual(len(calls), 20)
        self.assertLessEqual(extra_threads, 0)
        self.assertFalse(registry._sync_lock.locked())


if __name__ == "__main__":
    unittest.main()

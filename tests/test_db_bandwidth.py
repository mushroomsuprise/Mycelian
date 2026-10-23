"""Fewer remote reads/writes with identical results (Area B: DB bandwidth).

A fake remote backend (RTDB-like nested tree) counts every call so each test
can show what is no longer read or written and that results stay the same.
"""

from __future__ import annotations

import asyncio
import copy
import random
import threading
import time
import unittest
from collections import Counter
from unittest.mock import patch

from modules import alertutils
from modules import database_manager as dbm
from modules.database_manager import DatabaseConfig, DatabaseManager, DatabaseReadError

STARTUP_PATHS = [
    "Alerts/BitAlerts",
    "Alerts/SubAlerts",
    "Alerts/StreakAlerts",
    "Alerts/FollowAlerts",
    "Alerts/DonationAlerts",
    "Alerts/GiftsubAlerts",
    "Alerts/GiftsubRangeAlerts",
    "Alerts/RaidAlerts",
    "Alerts/RaidRangeAlerts",
    "Alerts/PointAlerts",
]


class FakeRemote:
    """Nested-tree store with call counters. Child writes land inside parents."""

    def __init__(self, tree: dict, *, kind: str = "firebase"):
        self.tree = copy.deepcopy(tree)
        self.kind = kind
        self.reads: Counter = Counter()
        self.writes: list = []
        self.storage_fetches = 0
        self.fail_reads: set = set()
        self.read_delay = 0.0
        self.write_delay = 0.0
        self.write_gate: threading.Event | None = None
        self._lock = threading.Lock()

    def _node(self, path: str, create: bool = False):
        node = self.tree
        parts = [p for p in path.strip("/").split("/") if p]
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                if not create:
                    return None, None
                node[part] = {}
            node = node[part]
        return node, parts[-1] if parts else None

    def _get(self, path: str):
        parent, leaf = self._node(path)
        if parent is None or not isinstance(parent, dict):
            return None
        return copy.deepcopy(parent.get(leaf))

    # DatabaseInterface surface used by DatabaseManager
    def get_data(self, path: str, request_etag: bool = False):
        with self._lock:
            self.reads[path] += 1
        if self.read_delay:
            time.sleep(self.read_delay)
        if path in self.fail_reads:
            raise DatabaseReadError(f"read failed: {path}")
        value = self._get(path) or {}
        return {"data": value, "etag": None} if request_etag else value

    def set_data(self, path: str, data) -> bool:
        if self.write_gate is not None:
            self.write_gate.wait(5)
        if self.write_delay:
            time.sleep(self.write_delay)
        with self._lock:
            self.writes.append(("set", path, copy.deepcopy(data)))
            parent, leaf = self._node(path, create=True)
            parent[leaf] = copy.deepcopy(data)
        return True

    def update_data(self, path: str, data) -> bool:
        return self.set_data(path, {**(self._get(path) or {}), **data})

    def delete_data(self, path: str) -> bool:
        with self._lock:
            self.writes.append(("delete", path, None))
            parent, leaf = self._node(path)
            if isinstance(parent, dict):
                parent.pop(leaf, None)
        return True

    def fetch_alert_storage(self, max_alerts=None):
        with self._lock:
            self.storage_fetches += 1
        if self.read_delay:
            time.sleep(self.read_delay)
        return self._get("Alerts/AlertStorage") or {}

    def list_alert_storage_meta(self):
        return [
            (k, float(v.get("timestamp") or 0))
            for k, v in (self._get("Alerts/AlertStorage") or {}).items()
        ]

    def delete_paths(self, paths) -> bool:
        for path in paths:
            self.delete_data(path)
        return True

    async def get_multiple_data_checked_async(self, paths):
        output, failed = {}, set()
        for path in paths:
            try:
                output[path] = self.get_data(path)
            except Exception:
                output[path] = {}
                failed.add(path)
        return output, failed

    def get_connection_status(self):
        return {}

    def test_connection(self) -> bool:
        return True


def _alert(name: str, ts: float) -> dict:
    return {"username": name, "timestamp": ts, "alert_type": "follow"}


def _base_tree() -> dict:
    storage = {f"a{i:03d}": _alert(f"user{i}", 1000.0 + (i % 37)) for i in range(120)}
    return {
        "Alerts": {
            "BitAlerts": {"bits100": {"alert_name": "Hundred", "duration": 5}},
            "SubAlerts": {"subs1": {"alert_name": "Sub", "duration": 4}},
            "FollowAlerts": {"follow1": {"alert_name": "Follow"}},
            "RaidAlerts": {},
            "BitRangeAlerts": {},
            "SubRangeAlerts": {"subs1-5": {"alert_name": "Range"}},
            "AlertStorage": storage,
        }
    }


class _Harness(unittest.TestCase):
    """Swap in a DatabaseManager backed by FakeRemote for each test."""

    kind = "firebase"

    def setUp(self) -> None:
        self.remote = FakeRemote(_base_tree(), kind=self.kind)
        self.dm = DatabaseManager()
        self.dm._config = DatabaseConfig(database_type=self.kind)
        self.dm._database = self.remote
        self.dm._initialized = True
        self._patch = patch.object(dbm, "database_manager", self.dm)
        self._patch.start()
        dbm._startup_failed_paths.clear()
        self.addCleanup(self._patch.stop)
        self.addCleanup(dbm._startup_failed_paths.clear)

    def manager(self) -> alertutils.AlertStateManager:
        mgr = alertutils.AlertStateManager()
        mgr._startup_trim_done = True
        return mgr

    def phase1(self, mgr) -> dict:
        all_data, failed = asyncio.run(
            self.dm.get_multiple_data_checked_async(STARTUP_PATHS)
        )
        dbm._startup_failed_paths.update(failed)
        mgr.initialize_with_data(all_data)
        return all_data

    def expected_state(self) -> dict:
        tree = self.remote.tree["Alerts"]
        mgr = alertutils.AlertStateManager()
        out = {}
        for key, path in mgr._alert_paths.items():
            if key != "alert_storage":
                out[key] = copy.deepcopy(tree.get(path.split("/", 1)[1]) or {})
        return out


class StartupReloadTests(_Harness):
    """SU-03 / DBX-01: overlay startup reads only what the preload skipped."""

    def test_overlay_init_reads_only_missing_paths_and_no_storage(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        before = Counter(self.remote.reads)
        mgr.load_missing_alert_paths()
        extra = self.remote.reads - before
        preloaded = set(STARTUP_PATHS)
        self.assertFalse(preloaded & set(extra), extra)
        self.assertEqual(set(extra), {
            "Alerts/BitRangeAlerts",
            "Alerts/SubRangeAlerts",
            "Alerts/DonationRangeAlerts",
            "Alerts/StreakRangeAlerts",
        })
        self.assertEqual(self.remote.storage_fetches, 0)
        # Same in-memory state the old full reload produced.
        self.assertEqual(mgr._alert_state, self.expected_state())
        self.assertIn("subs1-5", alertutils.SubRangeAlerts)

    def test_stored_alerts_download_once_for_every_reader(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.load_missing_alert_paths()
        full = self.remote.tree["Alerts"]["AlertStorage"]

        everything = mgr.get_all_alerts()
        self.assertEqual(everything["alert_storage"], full)
        for page in (1, 2, 3):
            mgr.get_stored_alerts_paginated(page=page, limit=25)
            mgr.get_limited_stored_alerts_paginated(page=page, limit=25, max_total_alerts=50)
        self.assertEqual(mgr.get_stored_alerts(), full)
        self.assertEqual(self.remote.storage_fetches, 1)

    def test_concurrent_first_readers_share_one_download(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        self.remote.read_delay = 0.2
        results = []

        def reader():
            results.append(mgr.get_limited_stored_alerts_from_firebase(250))

        threads = [threading.Thread(target=reader) for _ in range(6)]
        mgr.prefetch_alert_storage()
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(self.remote.storage_fetches, 1)
        self.assertEqual(len(results), 6)
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(len(results[0]), 120)

    def test_firebase_trim_reuses_the_storage_download(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        with patch.object(mgr, "_set_trim_progress"), patch(
            "modules.notification_engine.start_alert_trim_footer_poll", create=True
        ):
            deleted = mgr.trim_stored_alerts(mode="quantity", keep_count=100, keep_days=30)
        self.assertEqual(deleted, 20)
        self.assertEqual(self.remote.storage_fetches, 1)
        # The newest 100 by timestamp (stable order) remain, in memory and remote.
        storage = _base_tree()["Alerts"]["AlertStorage"]
        rows = sorted(
            ((k, v["timestamp"]) for k, v in storage.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        kept = {k for k, _ in rows[:100]}
        self.assertEqual(set(self.remote.tree["Alerts"]["AlertStorage"]), kept)
        self.assertEqual(set(mgr._alert_storage), kept)
        mgr.get_all_alerts()
        self.assertEqual(self.remote.storage_fetches, 1)


class EmptyCollectionTests(_Harness):
    """DBX-02: an empty collection is read once, not on every alert."""

    def test_empty_range_collection_is_read_once(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        first = mgr.get_alerts_by_type("bits")
        for _ in range(50):
            self.assertEqual(mgr.get_alerts_by_type("bits"), first)
        self.assertEqual(self.remote.reads["Alerts/BitRangeAlerts"], 1)
        self.assertEqual(first, {"bits100": {"alert_name": "Hundred", "duration": 5}})

    def test_failed_read_is_retried_like_before(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        self.remote.fail_reads.add("Alerts/BitRangeAlerts")
        for _ in range(3):
            mgr.get_alerts_by_type("bits")
        self.assertEqual(self.remote.reads["Alerts/BitRangeAlerts"], 3)
        self.remote.fail_reads.clear()
        mgr.get_alerts_by_type("bits")
        mgr.get_alerts_by_type("bits")
        self.assertEqual(self.remote.reads["Alerts/BitRangeAlerts"], 4)

    def test_write_from_outside_the_manager_is_picked_up(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.get_alerts_by_type("bits")
        # e.g. the overlay socket set_data handler or the DB explorer
        dbm.set_data("Alerts/BitRangeAlerts", {"bits1-99": {"alert_name": "R"}})
        result = mgr.get_alerts_by_type("bits")
        self.assertIn("bits1-99", result)
        self.assertEqual(self.remote.reads["Alerts/BitRangeAlerts"], 2)

    def test_backend_reinitialize_forgets_loaded_collections(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.get_alerts_by_type("bits")
        self.dm._notify_write("", False)
        self.dm._notify_write("", True)
        mgr.get_alerts_by_type("bits")
        self.assertEqual(self.remote.reads["Alerts/BitRangeAlerts"], 2)


class AlertWriteTests(_Harness):
    """WE-25 / TH-06 / DBX-14: writes after the lock, in order."""

    def test_readers_do_not_wait_for_a_slow_save(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.load_missing_alert_paths()
        self.remote.write_delay = 1.0
        saver = threading.Thread(
            target=mgr.save_alert, args=("bits", "bits500", {"alert_name": "Five"})
        )
        saver.start()
        time.sleep(0.1)
        t0 = time.monotonic()
        result = mgr.get_alerts_by_type("bits")
        elapsed = time.monotonic() - t0
        saver.join(5)
        self.assertLess(elapsed, 0.3)
        self.assertIn("bits500", result)
        self.assertEqual(
            self.remote.tree["Alerts"]["BitAlerts"]["bits500"]["alert_name"], "Five"
        )

    def test_concurrent_saves_keep_every_alert(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        self.remote.write_delay = 0.02
        names = [f"bits{n}" for n in range(200, 220)]
        threads = [
            threading.Thread(target=mgr.save_alert, args=("bits", n, {"alert_name": n}))
            for n in names
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        stored = self.remote.tree["Alerts"]["BitAlerts"]
        self.assertEqual(stored, mgr._alert_state["bits"])
        self.assertTrue(set(names) <= set(stored))

    def test_scripted_saves_and_deletes_end_in_the_same_store(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        rng = random.Random(5)
        model = copy.deepcopy(mgr._alert_state["subs"])
        for step in range(40):
            alert_id = f"subs{rng.randint(1, 8)}"
            if rng.random() < 0.6:
                data = {"alert_name": f"s{step}"}
                self.assertTrue(mgr.save_alert("subs", alert_id, data))
                model[alert_id] = dict(data, display_name=mgr.get_display_name("subs", alert_id, data))
            else:
                self.assertTrue(mgr.delete_alert("subs", alert_id))
                model.pop(alert_id, None)
        self.assertEqual(self.remote.tree["Alerts"]["SubAlerts"], model)
        self.assertEqual(mgr._alert_state["subs"], model)

    def test_firebase_save_skips_the_redundant_child_write(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.save_alert("bits", "bits300", {"alert_name": "Three"})
        paths = [path for _kind, path, _ in self.remote.writes]
        self.assertEqual(paths, ["Alerts/BitAlerts"])
        mgr.delete_alert("bits", "bits300")
        self.assertEqual([p for _k, p, _ in self.remote.writes][1:], ["Alerts/BitAlerts"])
        self.assertNotIn("bits300", self.remote.tree["Alerts"]["BitAlerts"])

    def test_flush_waits_for_queued_writes(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        gate = threading.Event()
        self.remote.write_gate = gate
        saver = threading.Thread(
            target=mgr.save_alert, args=("bits", "bits900", {"alert_name": "N"})
        )
        saver.start()
        time.sleep(0.1)
        self.assertFalse(mgr.flush_config_writes(timeout=0.2))
        gate.set()
        self.assertTrue(mgr.flush_config_writes(timeout=5))
        saver.join(5)
        self.assertIn("bits900", self.remote.tree["Alerts"]["BitAlerts"])


class SqlAlertWriteTests(AlertWriteTests):
    kind = "sql"

    def test_firebase_save_skips_the_redundant_child_write(self) -> None:
        mgr = self.manager()
        self.phase1(mgr)
        mgr.save_alert("bits", "bits300", {"alert_name": "Three"})
        paths = [path for _kind, path, _ in self.remote.writes]
        # SQL/Mongo keep the separate child row, as before.
        self.assertEqual(paths, ["Alerts/BitAlerts/bits300", "Alerts/BitAlerts"])


class FailedLoadTests(_Harness):
    """A failed read must never let a save replace the stored collection."""

    def test_save_after_failed_startup_read_merges_with_stored(self) -> None:
        self.remote.fail_reads.add("Alerts/BitAlerts")
        mgr = self.manager()
        self.phase1(mgr)
        self.assertEqual(mgr._alert_state["bits"], {})
        # Still unreadable: no write at all.
        self.assertFalse(mgr.save_alert("bits", "bits50", {"alert_name": "Fifty"}))
        self.assertEqual(self.remote.writes, [])
        self.assertEqual(
            set(self.remote.tree["Alerts"]["BitAlerts"]), {"bits100"}
        )
        # Readable again: the stored alert is kept next to the new one.
        self.remote.fail_reads.clear()
        self.assertTrue(mgr.save_alert("bits", "bits50", {"alert_name": "Fifty"}))
        self.assertEqual(
            set(self.remote.tree["Alerts"]["BitAlerts"]), {"bits100", "bits50"}
        )

    def test_firebase_backend_raises_instead_of_returning_empty(self) -> None:
        from modules.database_manager import FirebaseDatabase

        class _Ref:
            def child(self, _p):
                return self

            def get(self, *a, **k):
                raise OSError("network down")

        db = FirebaseDatabase(DatabaseConfig(database_type="firebase"))
        db._initialized = True
        db._root_ref = _Ref()
        db._connection_tested = True
        with self.assertRaises(DatabaseReadError):
            db.get_data("AppSettings")
        self.dm._database = db
        self.assertEqual(self.dm.get_data("AppSettings"), {})
        self.assertEqual(self.dm.get_data_checked("AppSettings"), ({}, False))


class PagingEquivalenceTests(unittest.TestCase):
    """DBX-08 / WE-32: same pages, only the page is copied."""

    @staticmethod
    def _old_page(storage, page, limit):
        alerts = []
        for alert_id, data in storage.items():
            item = copy.deepcopy(data)
            item["alert_id"] = alert_id
            alerts.append(item)
        alerts.sort(key=lambda i: alertutils.safe_alert_timestamp(i.get("timestamp", 0)), reverse=True)
        start = (page - 1) * limit
        return alerts[start : start + limit], len(alerts)

    def test_random_storage_pages_match(self) -> None:
        mgr = alertutils.AlertStateManager()
        rng = random.Random(42)
        for _ in range(30):
            storage = {}
            for i in range(rng.randint(0, 120)):
                ts = rng.choice([0, None, "", "now", "bad", rng.randint(0, 20), 5.5])
                entry = {"username": f"u{i}", "n": [i]}
                if ts is not None or rng.random() < 0.5:
                    entry["timestamp"] = ts
                storage[f"id{rng.randint(0, 999)}_{i}"] = entry
            with patch("modules.alertutils.time.time", return_value=50.0):
                limited = mgr._limit_stored_alerts_dict(storage, 60)
                for limit in (1, 7, 25):
                    for page in range(1, 5):
                        got = mgr._page_stored_alerts_memory(storage, page, limit)
                        want, total = self._old_page(storage, page, limit)
                        self.assertEqual(got["alerts"], want)
                        self.assertEqual(got["total_count"], total)
                        with patch.object(
                            mgr,
                            "get_limited_stored_alerts_from_firebase",
                            return_value=limited,
                        ):
                            lim = mgr.get_limited_stored_alerts_paginated(page, limit, 60)
                        want2, total2 = self._old_page(limited, page, limit)
                        self.assertEqual(lim["alerts"], want2)
                        self.assertEqual(lim["total_count"], total2)

    def test_page_copies_do_not_alias_storage(self) -> None:
        mgr = alertutils.AlertStateManager()
        storage = {"a": {"timestamp": 2, "tags": ["x"]}, "b": {"timestamp": 1}}
        page = mgr._page_stored_alerts_memory(storage, 1, 1)
        page["alerts"][0]["tags"].append("y")
        self.assertEqual(storage["a"]["tags"], ["x"])
        self.assertNotIn("alert_id", storage["a"])


class FirebasePreloadTests(unittest.TestCase):
    """SU-04 / SU-05 / DBX-11: one probe, 8-wide fan-out, no sleeps."""

    def _db(self, *, fail=(), delay=0.05, probe_error=None):
        from modules.database_manager import FirebaseDatabase

        state = {"inflight": 0, "max": 0, "probes": 0, "lock": threading.Lock()}

        class _Ref:
            def __init__(self, path=""):
                self.path = path

            def child(self, path):
                return _Ref(path)

            def get(self, shallow=False, etag=False):
                if shallow:
                    with state["lock"]:
                        state["probes"] += 1
                    time.sleep(delay)
                    if probe_error:
                        raise probe_error
                    return None
                with state["lock"]:
                    state["inflight"] += 1
                    state["max"] = max(state["max"], state["inflight"])
                time.sleep(delay)
                with state["lock"]:
                    state["inflight"] -= 1
                if self.path in fail:
                    raise OSError("boom")
                return {"path": self.path}

        db = FirebaseDatabase(DatabaseConfig(database_type="firebase"))
        db._initialized = True
        db._root_ref = _Ref()
        return db, state

    def test_one_probe_bounded_concurrency_same_output(self) -> None:
        paths = [f"P{i}" for i in range(21)]
        db, state = self._db(fail={"P3"})
        t0 = time.monotonic()
        output, failed = asyncio.run(db.get_multiple_data_checked_async(paths))
        elapsed = time.monotonic() - t0
        self.assertEqual(state["probes"], 1)
        self.assertLessEqual(state["max"], 8)
        self.assertEqual(list(output), paths)
        for path in paths:
            self.assertEqual(output[path], {} if path == "P3" else {"path": path})
        self.assertEqual(failed, {"P3"})
        self.assertLess(elapsed, 0.05 * 7)  # old: 7 batches + 0.3 s of sleeps
        plain = asyncio.run(db.get_multiple_data_async(paths))
        self.assertEqual(plain, output)

    def test_failed_probe_gives_empty_results(self) -> None:
        paths = [f"P{i}" for i in range(16)]
        db, state = self._db(probe_error=OSError("offline"))
        output, failed = asyncio.run(db.get_multiple_data_checked_async(paths))
        self.assertEqual(output, {p: {} for p in paths})
        self.assertEqual(failed, set(paths))
        # Concurrent callers shared probes: at most one per wave of 8.
        self.assertLessEqual(state["probes"], 2)


class StartupPreloadShareTests(_Harness):
    """SU-06 / DBX-13: the settings migration reuses the preload's copy."""

    def test_migration_reads_preloaded_database_settings(self) -> None:
        self.remote.tree["DatabaseSettings"] = {"database_type": "sql"}
        with patch.object(dbm, "_startup_preload_done", threading.Event()), patch.object(
            dbm, "_startup_shared_data", {}
        ):
            asyncio.run(dbm.load_all_initial_data())
            reads_after_preload = self.remote.reads["DatabaseSettings"]
            from modules import database_init

            with patch("modules.config_manager.config_manager.update_database_config", return_value=True):
                self.assertTrue(database_init._migrate_old_database_settings_if_needed({}))
            self.assertEqual(self.remote.reads["DatabaseSettings"], reads_after_preload)
            copy_a = dbm.wait_for_startup_preload("DatabaseSettings", 0)
            copy_a["database_type"] = "changed"
            self.assertEqual(
                dbm.wait_for_startup_preload("DatabaseSettings", 0),
                {"database_type": "sql"},
            )


class StateManagerTests(_Harness):
    """Settings blobs: no overwrite after a failed load; DBX-09."""

    def _state_manager(self, failed=()):
        from modules import dataobjects

        self.remote.tree["AppSettings"] = {"activity_feed_limit": 42, "activity_feed_max_pages": 7}
        self.remote.tree["SpotifyData"] = {"track_name": "Song", "is_playing": False}
        for path in failed:
            self.remote.fail_reads.add(path)
        sm = dataobjects.StateManager()
        paths = list(sm._paths.values())
        all_data, failed_paths = asyncio.run(self.dm.get_multiple_data_checked_async(paths))
        dbm._startup_failed_paths.update(failed_paths)
        with patch.object(dataobjects, "_apply_database_connection_settings"):
            sm.initialize_with_data(all_data)
        return sm

    def test_failed_load_never_overwrites_stored_settings(self) -> None:
        sm = self._state_manager(failed=("AppSettings",))
        sm.update_app_setting("activity_feed_limit", 10)
        self.assertFalse(sm.save_changes())
        self.assertFalse([w for w in self.remote.writes if w[1] == "AppSettings"])
        self.remote.fail_reads.clear()
        self.assertTrue(sm.save_changes())
        stored = self.remote.tree["AppSettings"]
        self.assertEqual(stored["activity_feed_limit"], 10)
        self.assertEqual(stored["activity_feed_max_pages"], 7)

    def test_discord_merged_write_skips_on_failed_read(self) -> None:
        from modules import dataobjects

        self.remote.fail_reads.add("DiscordData")
        self.assertFalse(dataobjects._write_discord_data_merged({"enabled": True}))
        self.assertFalse([w for w in self.remote.writes if w[1] == "DiscordData"])

    def test_unchanged_spotify_field_is_not_dirty(self) -> None:
        sm = self._state_manager()
        sm.update_spotify_field("track_name", "Song")
        self.assertFalse(sm.field_has_changes("spotify_data.track_name"))
        sm.update_spotify_field("track_name", "Other")
        self.assertTrue(sm.field_has_changes("spotify_data.track_name"))
        self.assertTrue(sm.save_changes())
        self.assertEqual(self.remote.tree["SpotifyData"]["track_name"], "Other")

    def test_unchanged_spotify_field_stays_dirty_after_failed_write(self) -> None:
        sm = self._state_manager()
        sm.update_spotify_field("track_name", "Other")
        with patch.object(self.remote, "set_data", return_value=False):
            sm.save_changes()
        sm.update_spotify_field("track_name", "Other")
        self.assertTrue(sm.field_has_changes("spotify_data.track_name"))


class StorageWrapperTests(_Harness):
    """DBX-18 / WE-24 / TH-15: stored-alert backend calls use the wrapper."""

    def test_storage_calls_go_through_the_timeout_wrapper(self) -> None:
        seen = []
        original = self.dm._run_db_op_with_timeout

        def spy(op_name, fn, default, **kwargs):
            seen.append(op_name)
            return original(op_name, fn, default, **kwargs)

        with patch.object(self.dm, "_run_db_op_with_timeout", side_effect=spy):
            self.assertEqual(len(self.dm.fetch_alert_storage()), 120)
            self.assertEqual(len(self.dm.list_alert_storage_meta()), 120)
        self.assertEqual(seen, ["AlertStorage", "AlertStorageMeta"])

    def test_backend_errors_still_reach_the_caller(self) -> None:
        with patch.object(self.remote, "fetch_alert_storage", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                self.dm.fetch_alert_storage()


if __name__ == "__main__":
    unittest.main()

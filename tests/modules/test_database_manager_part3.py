# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/database_manager.py, part 3: DatabaseManager, notifications, migration,
startup preload and the module-level convenience functions.

Backends are in-memory fakes or fresh SQLite files under tmp_path; the global manager is
only ever replaced via monkeypatch.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import database_manager as dbm
from modules.database_manager import DatabaseConfig, DatabaseReadError


class FakeBackend:
    def __init__(self, init_ok=True, last_error=""):
        self.init_ok = init_ok
        self.last_error = last_error
        self.snapshot_error = ""
        self.data = {}
        self.calls = []
        self.write_result = True
        self.raise_on = {}

    def _maybe_raise(self, op):
        if op in self.raise_on:
            raise self.raise_on[op]

    def initialize(self):
        self.calls.append("initialize")
        return self.init_ok

    def get_data(self, path, request_etag=False):
        self.calls.append(("get", path))
        self._maybe_raise("get")
        data = self.data.get(path, {})
        return {"data": data, "etag": "e"} if request_etag else data

    def set_data(self, path, data):
        self.calls.append(("set", path))
        self._maybe_raise("set")
        if self.write_result:
            self.data[path] = data
        return self.write_result

    def update_data(self, path, data):
        self.calls.append(("update", path))
        self._maybe_raise("update")
        if self.write_result:
            self.data.setdefault(path, {}).update(data)
        return self.write_result

    def delete_data(self, path):
        self.calls.append(("delete", path))
        self._maybe_raise("delete")
        self.data.pop(path, None)
        return self.write_result

    def get_connection_status(self):
        return {"status": "Connected", "is_connected": True}

    def test_connection(self):
        return True

    async def get_multiple_data_async(self, paths):
        return {p: self.data.get(p, {}) for p in paths}

    def get_all_paths(self):
        self._maybe_raise("paths")
        return sorted(self.data)

    def get_snapshot(self):
        self._maybe_raise("snapshot")
        return dict(self.data)


class AlertBackend(FakeBackend):
    def fetch_alert_storage(self, max_alerts=None):
        self._maybe_raise("alerts")
        return {"a": {"max": max_alerts}}

    def list_alert_storage_meta(self):
        return [("a", 1.0)]

    def query_alert_storage_page(self, page, limit):
        return {"alerts": [], "total_count": page * limit}

    def get_alert_storage_limited(self, max_alerts):
        return {"limited": max_alerts}

    def delete_paths(self, paths):
        self.calls.append(("delete_paths", list(paths)))
        for p in paths:
            self.data.pop(p, None)
        return True

    async def get_multiple_data_checked_async(self, paths):
        return {p: self.data.get(p, {}) for p in paths}, {"bad"} & set(paths)


@pytest.fixture(autouse=True)
def _reset_tls():
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0
    yield
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(dbm, "notify", lambda *a, **k: calls.append(("notify", a, k)))
    monkeypatch.setattr(dbm, "notify_critical", lambda *a, **k: calls.append(("critical", a, k)))
    return calls


def _manager(monkeypatch, backend, db_type="sql"):
    # A class (so isinstance checks against it still work) whose constructor returns the fake.
    factory = type("BackendFactory", (), {"__new__": lambda cls, cfg: backend})
    for name in ("SQLDatabase", "FirebaseDatabase", "MongoDatabase"):
        monkeypatch.setattr(dbm, name, factory)
    m = dbm.DatabaseManager()
    m._config = DatabaseConfig(database_type=db_type)
    return m


@pytest.fixture
def backend():
    return AlertBackend()


@pytest.fixture
def mgr(monkeypatch, backend, notes):
    m = _manager(monkeypatch, backend)
    assert m.initialize()
    return m


@pytest.fixture
def remote(monkeypatch, backend, notes):
    m = _manager(monkeypatch, backend, "firebase")
    assert m.initialize()
    return m


# ======================================================================= listeners

class TestListeners:
    def test_function_and_exception(self, mgr):
        seen = []
        mgr.add_write_listener(lambda p, d: seen.append((p, d)))
        mgr.add_write_listener(lambda p, d: 1 / 0)
        mgr._notify_write("/A/B", False)
        assert seen == [("A/B", False)]
        mgr._notify_write(None, True)
        assert seen[-1] == ("", True)

    def test_bound_method_is_weak_and_pruned(self, mgr):
        class L:
            def __init__(self):
                self.seen = []

            def cb(self, p, d):
                self.seen.append(p)

        obj = L()
        mgr.add_write_listener(obj.cb)
        mgr._notify_write("x", False)
        assert obj.seen == ["x"]
        del obj
        gc.collect()
        mgr._notify_write("y", False)
        assert mgr._write_listeners == []

    def test_no_listeners_noop(self, mgr):
        mgr._notify_write("x", False)


# ======================================================================= initialize

class TestInitialize:
    def test_success_notifies_listeners(self, monkeypatch, backend, notes):
        m = _manager(monkeypatch, backend)
        seen = []
        m.add_write_listener(lambda p, d: seen.append((p, d)))
        m.last_init_error = "old"
        assert m.initialize(DatabaseConfig(database_type="sql")) is True
        assert seen == [("", False), ("", True)]
        assert m._initialized and m.last_init_error == "" and notes == []

    @pytest.mark.parametrize("db_type", ["sql", "firebase", "mongodb"])
    def test_backend_types(self, monkeypatch, db_type, notes):
        made = []
        for name, t in (("SQLDatabase", "sql"), ("FirebaseDatabase", "firebase"), ("MongoDatabase", "mongodb")):
            monkeypatch.setattr(dbm, name, lambda cfg, t=t: made.append(t) or FakeBackend())
        m = dbm.DatabaseManager()
        assert m.initialize(DatabaseConfig(database_type=db_type))
        assert made == [db_type]

    def test_unknown_type(self, notes):
        m = dbm.DatabaseManager()
        assert m.initialize(DatabaseConfig(database_type="oracle")) is False
        assert "unknown database type 'oracle'" in m.last_init_error
        assert notes[0][0] == "critical" and notes[0][2]["dedupe_key"] == "db:down"

    def test_failure_notifies_once_then_reconnect(self, monkeypatch, notes):
        backend = FakeBackend(init_ok=False, last_error="refused")
        m = _manager(monkeypatch, backend, "firebase")
        assert m.initialize() is False
        assert m.last_init_error == "refused"
        assert m.initialize() is False
        crit = [n for n in notes if n[0] == "critical"]
        assert len(crit) == 1
        assert crit[0][2]["title"] == "Can't connect to the Firebase database"
        assert crit[0][2]["details"] == "refused"
        backend.init_ok = True
        assert m.initialize() is True
        ok = [n for n in notes if n[0] == "notify"]
        assert ok[0][2]["dedupe_key"] == "db:reconnected" and ok[0][2]["type"] == "success"
        assert m._down_notified is False

    def test_failure_without_error_text(self, monkeypatch, notes):
        backend = FakeBackend(init_ok=False)
        backend.last_error = None
        m = _manager(monkeypatch, backend)
        assert m.initialize() is False
        assert m.last_init_error == ""
        assert notes[0][2]["details"] is None

    def test_quiet_failure_and_quiet_reconnect(self, monkeypatch, notes):
        backend = FakeBackend(init_ok=False)
        m = _manager(monkeypatch, backend)
        with dbm.quiet_notifications():
            assert m.initialize() is False
        assert notes == []
        m._down_notified = True
        backend.init_ok = True
        with dbm.quiet_notifications():
            assert m.initialize() is True
        assert notes == [] and m._down_notified is False

    def test_listener_done_even_on_exception(self, monkeypatch, notes):
        m = dbm.DatabaseManager()
        seen = []
        m.add_write_listener(lambda p, d: seen.append(d))
        monkeypatch.setattr(m, "_initialize", lambda c: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(RuntimeError):
            m.initialize()
        assert seen == [False, True]


class TestConfig:
    def test_get_config(self, mgr):
        assert mgr.get_config() is mgr._config

    def test_update_config_fields(self, mgr, caplog):
        caplog.set_level("DEBUG", logger=dbm.logger.name)
        assert mgr.update_config(firebase_database_url=" https://x.firebaseio.com ",
                                 mongodb_connection_string="mongodb://u:pw@h", bogus=1) is True
        assert mgr._config.firebase_database_url == "https://x.firebaseio.com/"
        assert "pw@h" not in caplog.text and "***" in caplog.text
        assert "Unknown config key: bogus" in caplog.text

    def test_update_config_non_string_url(self, mgr):
        assert mgr.update_config(firebase_database_url=None) is True
        assert mgr._config.firebase_database_url is None

    def test_update_config_same_type_no_reinit(self, mgr, backend):
        n = backend.calls.count("initialize")
        assert mgr.update_config(database_type="sql") is True
        assert backend.calls.count("initialize") == n

    def test_update_config_type_change_reinitializes(self, mgr, backend):
        n = backend.calls.count("initialize")
        assert mgr.update_config(database_type="firebase") is True
        assert backend.calls.count("initialize") == n + 1
        assert mgr._config.database_type == "firebase"


# ======================================================================= reads

class TestReads:
    def test_get_data(self, mgr, backend):
        backend.data["A"] = {"x": 1}
        assert mgr.get_data("A") == {"x": 1}
        assert mgr.get_data("A", request_etag=True) == {"data": {"x": 1}, "etag": "e"}

    def test_get_data_lazy_init(self, monkeypatch, backend, notes):
        m = _manager(monkeypatch, backend)
        backend.data["A"] = {"x": 1}
        assert m.get_data("A") == {"x": 1}
        assert m._initialized

    def test_get_data_no_database(self, monkeypatch, notes):
        m = dbm.DatabaseManager()
        m._config = DatabaseConfig(database_type="nope")
        assert m.get_data("A") == {}
        assert m.get_data("A", request_etag=True) == {"data": {}, "etag": None}
        assert m.get_data_checked("A") == ({}, False)
        assert m.get_all_paths() == []
        assert m.get_snapshot() == {}

    def test_get_data_backend_error_returns_default(self, mgr, backend):
        backend.raise_on["get"] = DatabaseReadError("x")
        assert mgr.get_data("A") == {}
        assert mgr.get_data("A", request_etag=True) == {"data": {}, "etag": None}

    def test_get_data_checked(self, mgr, backend):
        backend.data["A"] = {"x": 1}
        assert mgr.get_data_checked("A") == ({"x": 1}, True)
        backend.raise_on["get"] = DatabaseReadError("x")
        assert mgr.get_data_checked("A") == ({}, False)

    def test_get_data_checked_lazy_init(self, monkeypatch, backend, notes):
        m = _manager(monkeypatch, backend)
        assert m.get_data_checked("A") == ({}, True)

    def test_paths_and_snapshot(self, mgr, backend):
        backend.data = {"b": {}, "a": {"x": 1}}
        assert mgr.get_all_paths() == ["a", "b"]
        assert mgr.get_snapshot() == {"b": {}, "a": {"x": 1}}
        backend.raise_on["paths"] = RuntimeError()
        backend.raise_on["snapshot"] = RuntimeError()
        assert mgr.get_all_paths() == []
        assert mgr.get_snapshot() == {}

    def test_paths_snapshot_lazy_init(self, monkeypatch, backend, notes):
        m = _manager(monkeypatch, backend)
        assert m.get_all_paths() == []
        m2 = _manager(monkeypatch, backend)
        assert m2.get_snapshot() == {}
        assert m._initialized and m2._initialized


# ======================================================================= writes

class TestWrites:
    @pytest.mark.parametrize("op", ["set_data", "update_data", "delete_data"])
    def test_success_records_ok(self, mgr, backend, op, notes):
        seen = []
        mgr.add_write_listener(lambda p, d: seen.append((p, d)))
        args = ("A",) if op == "delete_data" else ("A", {"x": 1})
        assert getattr(mgr, op)(*args) is True
        assert dbm.last_write_problem() == ("ok", "")
        assert seen == [("A", False), ("A", True)]
        assert notes == []

    @pytest.mark.parametrize("op", ["set_data", "update_data", "delete_data"])
    def test_backend_false_reports_failure(self, mgr, backend, op, notes):
        backend.write_result = False
        args = ("A",) if op == "delete_data" else ("A", {"x": 1})
        assert getattr(mgr, op)(*args) is False
        assert dbm.last_write_problem() == ("failed", "")
        n = notes[-1]
        assert n[0] == "notify" and n[2]["dedupe_key"] == "db:write_failed"
        assert "the database rejected the write" in n[2]["details"]
        assert n[2]["title"] == "Couldn't save to the local database"

    @pytest.mark.parametrize("op", ["set_data", "update_data", "delete_data"])
    def test_backend_exception_reports_reason(self, mgr, backend, op, notes):
        backend.raise_on[op.split("_")[0]] = RuntimeError("disk full")
        args = ("A",) if op == "delete_data" else ("A", {"x": 1})
        assert getattr(mgr, op)(*args) is False
        assert dbm.last_write_problem() == ("failed", "disk full")
        assert "disk full" in notes[-1][2]["details"]

    @pytest.mark.parametrize("op", ["set_data", "update_data", "delete_data"])
    def test_no_database_reports_down(self, op, notes):
        m = dbm.DatabaseManager()
        m._config = DatabaseConfig(database_type="nope")
        args = ("A",) if op == "delete_data" else ("A", {})
        assert getattr(m, op)(*args) is False
        assert dbm.last_write_problem() == ("down", "")
        assert [n[2]["dedupe_key"] for n in notes] == ["db:down"]

    def test_quiet_write_failure_not_reported(self, mgr, backend, notes):
        backend.write_result = False
        with dbm.quiet_notifications():
            assert mgr.set_data("A", {}) is False
        assert notes == []
        assert dbm.last_write_problem() == ("failed", "")


class TestReportWriteFailure:
    def test_ok_and_superseded_not_reported(self, mgr, notes):
        for status in ("ok", "superseded"):
            dbm._write_tls.last_write = (status, "")
            mgr._report_write_failure("Write", "A")
        assert notes == []

    def test_unknown_status_treated_as_failed(self, mgr, notes):
        dbm._write_tls.last_write = None
        mgr._report_write_failure("Write", "")
        assert notes[-1][2]["dedupe_key"] == "db:write_failed"
        assert "(root)" in notes[-1][2]["details"]

    def test_not_initialized_reports_down(self, mgr, notes):
        mgr._initialized = False
        dbm._write_tls.last_write = ("failed", "x")
        mgr._report_write_failure("Write", "A")
        assert notes[-1][2]["dedupe_key"] == "db:down"

    def test_timeout_warning(self, mgr, notes):
        dbm._write_tls.last_write = ("timeout", "no answer after 30 s")
        mgr._report_write_failure("Update", "A/B")
        kw = notes[-1][2]
        assert kw["type"] == "warning" and kw["dedupe_key"] == "db:write_timeout"
        assert kw["details"] == "Update A/B: no answer after 30 s"
        assert "responding slowly" in kw["title"]

    def test_popup_gap(self, mgr, notes, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(dbm.time, "monotonic", lambda: clock["t"])
        dbm._write_tls.last_write = ("failed", "x")
        mgr._report_write_failure("Write", "A")
        assert notes[-1][2]["skip_toast"] is False
        clock["t"] += 10
        mgr._report_write_failure("Write", "A")
        assert notes[-1][2]["skip_toast"] is True
        clock["t"] += dbm._WRITE_FAILURE_POPUP_GAP_SEC + 1
        mgr._report_write_failure("Write", "A")
        assert notes[-1][2]["skip_toast"] is False


# ======================================================================= timeout wrapper

class _ThreadingProxy:
    """Stands in for the ``threading`` module inside database_manager only."""

    def __init__(self, thread_cls):
        self.Thread = thread_cls

    def __getattr__(self, name):
        return getattr(threading, name)


class HangingThread:
    """Never runs its target until the test calls run_later()."""

    last = None

    def __init__(self, target=None, name=None, daemon=None):
        self.target = target
        self.name = name
        HangingThread.last = self

    def start(self):
        pass

    def join(self, timeout=None):
        self.joined = timeout

    def is_alive(self):
        return True

    def run_later(self):
        self.target()


class TestRunWithTimeout:
    def test_remote_read_runs_on_worker_thread(self, remote, backend):
        backend.data["A"] = {"x": 1}
        names = []
        real_get = backend.get_data

        def get(path, request_etag=False):
            names.append(threading.current_thread().name)
            return real_get(path, request_etag)

        backend.get_data = get
        assert remote.get_data("A") == {"x": 1}
        assert names == ["MycelianDBGet"]

    def test_remote_write_ok_and_failure(self, remote, backend, notes):
        assert remote.set_data("A", {"x": 1}) is True
        assert dbm.last_write_problem() == ("ok", "")
        backend.raise_on["set"] = RuntimeError("denied")
        assert remote.set_data("A", {"x": 1}) is False
        assert dbm.last_write_problem() == ("failed", "denied")
        assert notes[-1][2]["title"] == "Couldn't save to the Firebase database"

    def test_timeout_on_write_invalidates_generation(self, remote, backend, monkeypatch, notes):
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))
        seen = []
        remote.add_write_listener(lambda p, d: seen.append((p, d)))
        remote._config.connection_timeout = 1
        assert remote.set_data("A", {"x": 1}) is False
        assert HangingThread.last.joined == 5  # floor of 5 seconds
        assert dbm.last_write_problem() == ("timeout", "no answer after 5 s")
        assert notes[-1][2]["dedupe_key"] == "db:write_timeout"
        assert remote._write_generation["A"] == 2
        assert seen == [("A", False)]
        # The late worker sees it was superseded and does not write.
        HangingThread.last.run_later()
        assert "A" not in backend.data
        assert seen == [("A", False), ("A", True)]

    def test_timeout_on_read_returns_default(self, remote, monkeypatch):
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))
        assert remote.get_data("A", request_etag=True) == {"data": {}, "etag": None}
        assert remote.get_data_checked("A") == ({}, False)
        assert dbm.last_write_problem() is None

    def test_timeout_after_newer_write_keeps_generation(self, remote, monkeypatch, notes):
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))

        class BumpOnJoin(HangingThread):
            def join(self, timeout=None):
                remote._write_generation["A"] = 99

        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(BumpOnJoin))
        assert remote.set_data("A", {}) is False
        assert remote._write_generation["A"] == 99

    def test_superseded_during_write(self, mgr, backend, notes):
        real_set = backend.set_data

        def set_and_bump(path, data):
            mgr._write_generation[path] += 1
            return real_set(path, data)

        backend.set_data = set_and_bump
        assert mgr.set_data("A", {"x": 1}) is False
        assert dbm.last_write_problem() == ("superseded", "")
        assert notes == []

    def test_path_lock_is_reused(self, mgr):
        assert mgr._path_write_lock("A") is mgr._path_write_lock("A")
        assert mgr._path_write_lock("A") is not mgr._path_write_lock("B")

    def test_zero_timeout_config_uses_default(self, remote, monkeypatch):
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))
        remote._config.connection_timeout = 0
        remote.get_data("A")
        assert HangingThread.last.joined == 30

    @pytest.mark.parametrize("have_gevent", [True, False])
    def test_overlay_hub_polls_instead_of_join(self, remote, backend, monkeypatch, have_gevent):
        monkeypatch.setattr(dbm, "_caller_is_overlay_hub", lambda: True)
        sleeps = []
        if have_gevent:
            monkeypatch.setitem(sys.modules, "gevent", types.SimpleNamespace(sleep=lambda s: sleeps.append(s)))
        else:
            monkeypatch.setitem(sys.modules, "gevent", None)
            monkeypatch.setattr(dbm.time, "sleep", lambda s: sleeps.append(s))

        class PollThread(HangingThread):
            polls = 0

            def is_alive(self):
                PollThread.polls += 1
                if PollThread.polls > 2:
                    if not getattr(self, "ran", False):
                        self.ran = True
                        self.target()
                    return False
                return True

        PollThread.polls = 0
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(PollThread))
        backend.data["A"] = {"v": 1}
        assert remote.get_data("A") == {"v": 1}
        assert sleeps == [0.01, 0.01]

    def test_overlay_hub_deadline(self, remote, monkeypatch):
        monkeypatch.setattr(dbm, "_caller_is_overlay_hub", lambda: True)
        monkeypatch.setitem(sys.modules, "gevent", types.SimpleNamespace(sleep=lambda s: None))
        clock = {"t": 0.0}

        def mono():
            clock["t"] += 3.0
            return clock["t"]

        monkeypatch.setattr(dbm.time, "monotonic", mono)
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))
        assert remote.get_data("A") == {}


# ======================================================================= alert storage

class TestAlertStorageOps:
    def test_wrappers(self, mgr):
        assert mgr.fetch_alert_storage(3) == {"a": {"max": 3}}
        assert mgr.list_alert_storage_meta() == [("a", 1.0)]
        assert mgr.query_alert_storage_page(2, 5) == {"alerts": [], "total_count": 10}
        assert mgr.get_alert_storage_limited(4) == {"limited": 4}

    def test_backend_error_reaches_caller(self, mgr, backend):
        backend.raise_on["alerts"] = ValueError("bad")
        with pytest.raises(ValueError, match="bad"):
            mgr.fetch_alert_storage()

    def test_not_initialized_returns_none(self, monkeypatch, backend):
        m = _manager(monkeypatch, backend)
        assert m._alert_backend() is None
        assert m.fetch_alert_storage() is None

    def test_backend_without_method(self, monkeypatch, notes):
        b = FakeBackend()
        m = _manager(monkeypatch, b)
        m.initialize()
        assert m.list_alert_storage_meta() is None
        b.data["Alerts/AlertStorage"] = {"x": {}}
        assert m.get_alert_storage_limited(5) == {"x": {}}

    def test_timeout_returns_none(self, remote, monkeypatch):
        monkeypatch.setattr(dbm, "threading", _ThreadingProxy(HangingThread))
        assert remote.fetch_alert_storage() is None
        assert remote.get_alert_storage_limited(5) == {}

    def test_limited_lazy_init(self, monkeypatch, backend, notes):
        m = _manager(monkeypatch, backend)
        assert m.get_alert_storage_limited(2) == {"limited": 2}


class TestDeletePaths:
    def test_empty(self, mgr):
        assert mgr.delete_paths([]) is True

    def test_batch_backend(self, mgr, backend):
        backend.data = {"a": {}, "b": {}}
        seen = []
        mgr.add_write_listener(lambda p, d: seen.append((p, d)))
        assert mgr.delete_paths(["a", "b"]) is True
        assert backend.data == {}
        assert seen == [("a", False), ("b", False), ("a", True), ("b", True)]

    def test_fallback_per_path(self, monkeypatch, notes):
        b = FakeBackend()
        b.data = {"a": {}, "b": {}}
        m = _manager(monkeypatch, b)
        m.initialize()
        assert m.delete_paths(["a", "b"]) is True
        assert b.data == {}
        b.write_result = False
        assert m.delete_paths(["a"]) is False


# ======================================================================= status / misc

class TestStatusMisc:
    def test_status_and_test(self, mgr):
        assert mgr.get_connection_status()["status"] == "Connected"
        assert mgr.test_connection() is True
        m = dbm.DatabaseManager()
        s = m.get_connection_status()
        assert s["status"] == "Not Initialized" and s["database_type"] == "sql"
        assert m.test_connection() is False

    def test_available_databases(self, monkeypatch):
        m = dbm.DatabaseManager()
        monkeypatch.setattr(dbm, "_ensure_firebase", lambda: True)
        monkeypatch.setattr(dbm, "_ensure_pymongo", lambda: False)
        assert m.get_available_databases() == ["sql", "firebase"]
        monkeypatch.setattr(dbm, "_ensure_firebase", lambda: False)
        monkeypatch.setattr(dbm, "_ensure_pymongo", lambda: True)
        assert m.get_available_databases() == ["sql", "mongodb"]

    def test_multiple_async(self, mgr, backend):
        backend.data = {"a": {"v": 1}}
        assert asyncio.run(mgr.get_multiple_data_async(["a", "b"])) == {"a": {"v": 1}, "b": {}}
        assert asyncio.run(mgr.get_multiple_data_checked_async(["a", "bad"])) == (
            {"a": {"v": 1}, "bad": {}}, {"bad"})

    def test_multiple_async_without_checked(self, monkeypatch, notes):
        b = FakeBackend()
        b.data = {"a": 1}
        m = _manager(monkeypatch, b)
        assert asyncio.run(m.get_multiple_data_checked_async(["a"])) == ({"a": 1}, set())

    def test_multiple_async_no_database(self, notes):
        m = dbm.DatabaseManager()
        m._config = DatabaseConfig(database_type="nope")
        assert asyncio.run(m.get_multiple_data_async(["a"])) == {"a": {}}
        assert asyncio.run(m.get_multiple_data_checked_async(["a"])) == ({"a": {}}, {"a"})


class TestSnapshotChecked:
    def test_ok(self, mgr, backend):
        backend.data = {"A": {"x": 1}}
        backend.snapshot_error = "stale"
        assert mgr.get_snapshot_checked() == ({"A": {"x": 1}}, "")

    def test_empty_ok(self, mgr):
        assert mgr.get_snapshot_checked() == ({}, "")

    def test_snapshot_error(self, mgr, backend):
        def snap():
            backend.snapshot_error = "Invalid JSON"
            return {}

        backend.get_snapshot = snap
        assert mgr.get_snapshot_checked() == ({}, "Invalid JSON")

    def test_failure(self, mgr, backend):
        backend.raise_on["snapshot"] = RuntimeError()
        snap, err = mgr.get_snapshot_checked()
        assert snap == {} and "didn't answer in time" in err

    def test_init_fails_quietly(self, monkeypatch, notes):
        b = FakeBackend(init_ok=False, last_error="nope")
        m = _manager(monkeypatch, b)
        assert m.get_snapshot_checked() == ({}, "nope")
        assert notes == []
        b.last_error = ""
        assert m.get_snapshot_checked() == ({}, "The database isn't connected.")


class TestPathsFromSnapshot:
    def test_empty(self, mgr):
        assert mgr.get_paths_from_snapshot({}) == []

    def test_non_firebase_uses_all_paths(self, mgr, backend):
        backend.data = {"z": {}, "a": {}}
        assert mgr.get_paths_from_snapshot({"anything": 1}) == ["a", "z"]

    def test_firebase_extracts(self):
        fb = dbm.FirebaseDatabase(DatabaseConfig())
        m = dbm.DatabaseManager()
        m._database = fb
        assert m.get_paths_from_snapshot({"B": {"x": 1}, "A": {"C": {"y": 1}}}) == ["A/C", "B"]


# ======================================================================= migration

def _sqlcfg(path):
    return DatabaseConfig(database_type="sql", sql_database_path=str(path))


def _close_sql(db):
    for c in db._connection_pool:
        c.close()
    if db._connection:
        db._connection.close()


class TestMigration:
    def test_sql_to_sql(self, tmp_path):
        src = dbm.SQLDatabase(_sqlcfg(tmp_path / "src.db"))
        src.set_data("A", {"x": 1})
        src.set_data("Alerts/AlertStorage/1", {"timestamp": 5})
        src.set_data("DatabaseSettings", {"database_type": "sql", "keep": True})
        _close_sql(src)
        m = dbm.DatabaseManager()
        seen = []
        m.add_write_listener(lambda p, d: seen.append((p, d)))
        assert m.migrate_data(_sqlcfg(tmp_path / "src.db"), _sqlcfg(tmp_path / "dst.db")) is True
        assert seen == [("", False), ("", True)]
        assert m.last_migration_result == {"ok": True, "total": 3, "migrated": 3, "failed": 0, "failed_paths": []}
        dst = dbm.SQLDatabase(_sqlcfg(tmp_path / "dst.db"))
        assert dst.get_data("A") == {"x": 1}
        assert dst.get_data("Alerts/AlertStorage/1") == {"timestamp": 5}
        assert dst.get_data("DatabaseSettings") == {"database_type": "sql", "keep": True}
        _close_sql(dst)

    @pytest.mark.parametrize("which", ["source", "target"])
    def test_unknown_types(self, tmp_path, which):
        good = _sqlcfg(tmp_path / "x.db")
        bad = DatabaseConfig(database_type="nope")
        m = dbm.DatabaseManager()
        args = (bad, good) if which == "source" else (good, bad)
        assert m.migrate_data(*args) is False
        assert f"Unknown {which} database type 'nope'" in m.last_migration_result["error"]

    @pytest.mark.parametrize("which", ["source", "target"])
    def test_init_failures(self, monkeypatch, which):
        ok, bad = FakeBackend(), FakeBackend(init_ok=False, last_error="")
        order = [bad, ok] if which == "source" else [ok, bad]
        monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: order.pop(0))
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(), DatabaseConfig()) is False
        assert m.last_migration_result["stage"] == which
        assert m.last_migration_result["error"] == f"Couldn't open the {which} database."

    def test_init_failure_with_reason(self, monkeypatch):
        monkeypatch.setattr(dbm, "FirebaseDatabase", lambda cfg: FakeBackend(init_ok=False, last_error="key"))
        monkeypatch.setattr(dbm, "MongoDatabase", lambda cfg: FakeBackend())
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(database_type="firebase"), DatabaseConfig(database_type="mongodb")) is False
        assert m.last_migration_result["error"] == "key"

    def test_partial_failure_and_migration_paths(self, monkeypatch):
        src = FakeBackend()
        src.data = {"A": {"a": 1}, "B": {"b": 1}, "C": {}}
        src.get_migration_paths = lambda: ["A", "B", "C"]
        real_get = src.get_data
        src.get_data = lambda p, request_etag=False: (_ for _ in ()).throw(DatabaseReadError("rd")) if p == "C" \
            else real_get(p)
        dst = FakeBackend()
        real_set = dst.set_data
        dst.set_data = lambda p, d: False if p == "B" else real_set(p, d)
        monkeypatch.setattr(dbm, "MongoDatabase", lambda cfg: src)
        monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: dst)
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(database_type="mongodb"), DatabaseConfig()) is False
        r = m.last_migration_result
        assert r["ok"] is False and r["total"] == 3 and r["migrated"] == 1 and r["failed"] == 2
        assert r["failed_paths"] == ["B", "C (rd)"]
        assert "DatabaseSettings" not in dst.data

    def test_persist_settings_error_ignored(self, monkeypatch):
        src, dst = FakeBackend(), FakeBackend()
        src.data = {"A": {"a": 1}}
        dst_get = dst.get_data
        dst.get_data = lambda p, request_etag=False: (_ for _ in ()).throw(RuntimeError("x")) \
            if p == "DatabaseSettings" else dst_get(p)
        seq = [src, dst]
        monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: seq.pop(0))
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(), DatabaseConfig()) is True

    def test_settings_non_dict_left_alone(self, monkeypatch):
        src, dst = FakeBackend(), FakeBackend()
        dst.data = {"DatabaseSettings": [1]}
        seq = [src, dst]
        monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: seq.pop(0))
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(), DatabaseConfig(database_type="sql")) is True
        assert dst.data["DatabaseSettings"] == [1]

    def test_outer_exception(self, monkeypatch):
        monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: (_ for _ in ()).throw(RuntimeError("ctor")))
        m = dbm.DatabaseManager()
        assert m.migrate_data(DatabaseConfig(), DatabaseConfig()) is False
        assert m.last_migration_result == {"ok": False, "error": "ctor"}


# ======================================================================= module-level

@pytest.fixture
def global_mgr(monkeypatch, backend, notes):
    m = _manager(monkeypatch, backend)
    monkeypatch.setattr(dbm, "database_manager", m)
    return m


class TestModuleFunctions:
    def test_convenience_delegates(self, global_mgr, backend):
        assert dbm.initialize_database(DatabaseConfig()) is True
        assert dbm.set_data("A", {"x": 1}) is True
        assert dbm.get_data("A") == {"x": 1}
        assert dbm.update_data("A", {"y": 2}) is True
        assert dbm.get_data_checked("A") == ({"x": 1, "y": 2}, True)
        assert asyncio.run(dbm.get_multiple_data_async(["A"])) == {"A": {"x": 1, "y": 2}}
        assert dbm.get_all_paths() == ["A"]
        assert dbm.get_snapshot() == {"A": {"x": 1, "y": 2}}
        assert dbm.delete_data("A") is True
        assert dbm.get_connection_status()["status"] == "Connected"
        assert dbm.test_connection() is True

    @pytest.mark.parametrize("db_type,expected", [("sql", False), ("firebase", True), ("mongodb", True)])
    def test_needs_remote_monitor(self, global_mgr, db_type, expected):
        global_mgr._config.database_type = db_type
        assert dbm.database_needs_remote_monitor() is expected

    def test_needs_remote_monitor_error(self, global_mgr, monkeypatch):
        monkeypatch.setattr(global_mgr, "get_config", lambda: (_ for _ in ()).throw(RuntimeError()))
        assert dbm.database_needs_remote_monitor() is False


class TestAutoReconnect:
    @pytest.fixture
    def internet(self, monkeypatch):
        from modules import connection_monitor

        state = {"up": True}
        monkeypatch.setattr(connection_monitor, "is_internet_available", lambda *a, **k: state["up"])
        return state

    def test_config_error(self, global_mgr, monkeypatch):
        monkeypatch.setattr(global_mgr, "get_config", lambda: (_ for _ in ()).throw(RuntimeError()))
        assert dbm.attempt_auto_reconnect() is False

    def test_sql_always_ok(self, global_mgr):
        assert dbm.attempt_auto_reconnect() is True

    def test_offline(self, global_mgr, internet):
        global_mgr._config.database_type = "firebase"
        internet["up"] = False
        assert dbm.attempt_auto_reconnect() is False

    def test_monitor_error(self, global_mgr, monkeypatch):
        from modules import connection_monitor

        global_mgr._config.database_type = "firebase"
        monkeypatch.setattr(connection_monitor, "is_internet_available",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        assert dbm.attempt_auto_reconnect() is False

    def test_connection_fine(self, global_mgr, internet, backend):
        global_mgr._config.database_type = "firebase"
        global_mgr.initialize()
        n = backend.calls.count("initialize")
        assert dbm.attempt_auto_reconnect() is True
        assert backend.calls.count("initialize") == n

    def test_reinitializes(self, global_mgr, internet, backend, monkeypatch):
        global_mgr._config.database_type = "mongodb"
        global_mgr.initialize()
        monkeypatch.setattr(backend, "test_connection", lambda: False)
        n = backend.calls.count("initialize")
        assert dbm.attempt_auto_reconnect() is True
        assert backend.calls.count("initialize") == n + 1
        backend.init_ok = False
        assert dbm.attempt_auto_reconnect() is False


class TestStartupPreload:
    @pytest.fixture
    def preload(self, monkeypatch):
        monkeypatch.setattr(dbm, "_startup_preload_done", threading.Event())
        monkeypatch.setattr(dbm, "_startup_failed_paths", set())
        monkeypatch.setattr(dbm, "_startup_shared_data", {})

    def test_wait_times_out(self, preload):
        assert dbm.wait_for_startup_preload("DatabaseSettings", 0.0) is None

    def test_load_all_initial_data(self, preload, global_mgr, backend, monkeypatch):
        backend.data = {"TwitchData": {"t": 1}, "DatabaseSettings": {"database_type": "sql"}}

        async def checked(paths):
            return {p: backend.data.get(p, {}) for p in paths}, {"OBSData"}

        monkeypatch.setattr(global_mgr, "get_multiple_data_checked_async", checked)
        out = asyncio.run(dbm.load_all_initial_data())
        assert out["TwitchData"] == {"t": 1}
        assert out["Alerts/AlertQueue"] == {}
        assert len(out) == 21
        assert dbm.startup_read_failed("OBSData") and not dbm.startup_read_failed("TwitchData")
        copy1 = dbm.wait_for_startup_preload("DatabaseSettings", 0)
        assert copy1 == {"database_type": "sql"}
        copy1["database_type"] = "changed"
        assert dbm.wait_for_startup_preload("DatabaseSettings", 0) == {"database_type": "sql"}
        assert dbm.wait_for_startup_preload("TwitchData", 0) is None  # not kept

    def test_failed_shared_path_not_returned(self, preload, global_mgr, monkeypatch):
        async def checked(paths):
            return {p: {} for p in paths}, {"DatabaseSettings"}

        monkeypatch.setattr(global_mgr, "get_multiple_data_checked_async", checked)
        asyncio.run(dbm.load_all_initial_data())
        assert dbm.wait_for_startup_preload("DatabaseSettings", 0) is None

    def test_no_failures(self, preload, global_mgr, monkeypatch):
        async def checked(paths):
            return {}, set()

        monkeypatch.setattr(global_mgr, "get_multiple_data_checked_async", checked)
        out = asyncio.run(dbm.load_all_initial_data())
        assert all(v == {} for v in out.values())
        assert dbm.wait_for_startup_preload("DatabaseSettings", 0) is None


def test_migration_into_firebase_target(monkeypatch):
    src, dst = FakeBackend(), FakeBackend()
    src.data = {"A": {"a": 1}}
    monkeypatch.setattr(dbm, "SQLDatabase", lambda cfg: src)
    monkeypatch.setattr(dbm, "FirebaseDatabase", lambda cfg: dst)
    m = dbm.DatabaseManager()
    assert m.migrate_data(DatabaseConfig(), DatabaseConfig(database_type="firebase")) is True
    assert dst.data["A"] == {"a": 1}
    assert dst.data["DatabaseSettings"] == {"database_type": "firebase"}

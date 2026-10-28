# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/database_manager.py, part 1: module helpers and the SQLite backend.

Every test opens its own SQLite file under tmp_path; the session's global database is not used.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import database_manager as dbm
from modules.database_manager import DatabaseConfig, DatabaseReadError, SQLDatabase


@pytest.fixture
def sql(tmp_path):
    db = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / "t.db")))
    assert db.initialize()
    yield db
    _close(db)


def _close(db):
    for conn in list(db._connection_pool):
        conn.close()
    db._connection_pool.clear()
    if db._connection is not None:
        db._connection.close()


def _raw(db, sql, params=()):
    conn = sqlite3.connect(db.db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _put_raw(db, path, data_json):
    _raw(db, "INSERT OR REPLACE INTO app_data (streamer_name, data_path, data_json) VALUES (?, ?, ?)",
         (db.streamer_name, path, data_json))


class BrokenConn:
    """Connection whose cursor raises on execute."""

    def __init__(self, exc=sqlite3.OperationalError("boom"), rollback_exc=None):
        self.exc = exc
        self.rollback_exc = rollback_exc
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self

    def execute(self, *a, **k):
        raise self.exc

    def rollback(self):
        self.rolled_back = True
        if self.rollback_exc:
            raise self.rollback_exc

    def commit(self):
        pass

    def close(self):
        self.closed = True


# ======================================================================= module helpers

class TestUrlHelpers:
    @pytest.mark.parametrize("raw,expected", [
        ("", ""), (None, ""), ("  https://a.firebaseio.com  ", "https://a.firebaseio.com/"),
        ("https://a.firebaseio.com/", "https://a.firebaseio.com/"),
    ])
    def test_normalize(self, raw, expected):
        assert dbm.normalize_firebase_database_url(raw) == expected

    @pytest.mark.parametrize("url,ok", [
        ("https://x.firebaseio.com/", True),
        ("https://x-default-rtdb.europe-west1.firebasedatabase.app", True),
        ("HTTPS://X.FIREBASEIO.COM", True),
        ("http://x.firebaseio.com/", False),
        ("https://example.com/", False),
        ("https:///nohost", False),
        ("", False),
        (None, False),
    ])
    def test_is_valid(self, url, ok):
        assert dbm.is_valid_firebase_rtdb_url(url) is ok

    def test_is_valid_urlparse_error(self, monkeypatch):
        monkeypatch.setattr(dbm, "urlparse", lambda u: (_ for _ in ()).throw(ValueError()))
        assert dbm.is_valid_firebase_rtdb_url("https://x.firebaseio.com") is False


def test_strip_db_path():
    assert dbm._strip_db_path("/a/b") == "a/b"
    assert dbm._strip_db_path("a/b") == "a/b"


def test_alert_prefix_query():
    q = dbm._alert_storage_prefix_query()["data_path"]
    assert q["$gte"] == "Alerts/AlertStorage/" and q["$lt"] == "Alerts/AlertStorage0"


@pytest.mark.parametrize("value,expected", [
    (None, 0.0), ("", 0.0), ("12.5", 12.5), (3, 3.0), ("abc", 0.0), ([1], 0.0),
])
def test_coerce_alert_timestamp(value, expected):
    assert dbm._coerce_alert_timestamp(value) == expected


def test_coerce_alert_timestamp_now(monkeypatch):
    monkeypatch.setattr(dbm.time, "time", lambda: 999.0)
    assert dbm._coerce_alert_timestamp("now") == 999.0


def test_sorted_alert_records_newest_first_and_no_mutation():
    storage = {"a": {"timestamp": 1}, "b": {"timestamp": "5"}, "c": "not a dict", 7: {"x": 1}}
    out = dbm._sorted_alert_records(storage)
    assert [r["alert_id"] for r in out] == ["b", "a", "7"]
    assert "alert_id" not in storage["a"]


def test_db_label():
    assert dbm._db_label("sql") == "local"
    assert dbm._db_label("FIREBASE") == "Firebase"
    assert dbm._db_label("mongodb") == "MongoDB"
    assert dbm._db_label(None) == "local"
    assert dbm._db_label("weird") == "weird"


def test_database_label_uses_global_config(monkeypatch):
    m = dbm.DatabaseManager()
    m._config = DatabaseConfig(database_type="firebase")
    monkeypatch.setattr(dbm, "database_manager", m)
    assert dbm.database_label() == "Firebase"
    assert dbm.database_label("mongodb") == "MongoDB"


@pytest.mark.parametrize("result,expected", [
    ({"status": "ok", "value": True}, ("ok", "")),
    ({"status": "ok", "value": False}, ("failed", "")),
    ({"status": None, "value": None, "reason": None}, ("failed", "")),
    ({"status": "superseded", "value": False}, ("superseded", "")),
    ({"status": "failed", "value": False, "reason": "x"}, ("failed", "x")),
])
def test_write_outcome(result, expected):
    assert dbm._write_outcome(result) == expected


def test_quiet_notifications_nesting():
    assert not dbm._quiet()
    with dbm.quiet_notifications():
        assert dbm._quiet()
        with dbm.quiet_notifications():
            assert dbm._quiet()
        assert dbm._quiet()
    assert not dbm._quiet()


def test_quiet_notifications_restores_on_error():
    with pytest.raises(RuntimeError):
        with dbm.quiet_notifications():
            raise RuntimeError()
    assert not dbm._quiet()


def test_last_write_problem_roundtrip():
    dbm._write_tls.last_write = ("failed", "why")
    assert dbm.last_write_problem() == ("failed", "why")
    dbm.clear_last_write_problem()
    assert dbm.last_write_problem() is None


class TestCallerIsOverlayHub:
    def _install(self, monkeypatch, web_engine):
        import modules

        monkeypatch.setattr(modules, "web_engine", web_engine, raising=False)
        monkeypatch.setitem(sys.modules, "modules.web_engine", web_engine)

    def test_no_instance(self, monkeypatch):
        self._install(monkeypatch, types.SimpleNamespace(web_engine_instance=None))
        assert dbm._caller_is_overlay_hub() is False

    def test_matching_thread(self, monkeypatch):
        import threading

        inst = types.SimpleNamespace(_server_thread_ident=threading.get_ident())
        self._install(monkeypatch, types.SimpleNamespace(web_engine_instance=inst))
        assert dbm._caller_is_overlay_hub() is True

    def test_other_thread_or_no_ident(self, monkeypatch):
        inst = types.SimpleNamespace(_server_thread_ident=None)
        self._install(monkeypatch, types.SimpleNamespace(web_engine_instance=inst))
        assert dbm._caller_is_overlay_hub() is False
        inst._server_thread_ident = -1
        assert dbm._caller_is_overlay_hub() is False

    def test_exception(self, monkeypatch):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError()

        self._install(monkeypatch, Boom())
        assert dbm._caller_is_overlay_hub() is False


class TestLazyImports:
    def test_ensure_firebase_success_and_cache(self, monkeypatch):
        fake = types.ModuleType("firebase_admin")
        fake.db = types.SimpleNamespace(name="db")
        monkeypatch.setitem(sys.modules, "firebase_admin", fake)
        monkeypatch.setitem(sys.modules, "firebase_admin.db", fake.db)
        for name, val in [("_firebase_checked", False), ("FIREBASE_AVAILABLE", False),
                          ("firebase_admin", None), ("firebase_db", None)]:
            monkeypatch.setattr(dbm, name, val)
        assert dbm._ensure_firebase() is True
        assert dbm.firebase_admin is fake and dbm.firebase_db is fake.db
        monkeypatch.setitem(sys.modules, "firebase_admin", None)
        assert dbm._ensure_firebase() is True  # cached

    def test_ensure_firebase_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "firebase_admin", None)
        monkeypatch.setattr(dbm, "_firebase_checked", False)
        monkeypatch.setattr(dbm, "FIREBASE_AVAILABLE", True)
        assert dbm._ensure_firebase() is False

    def test_ensure_pymongo_success(self, monkeypatch):
        fake = types.ModuleType("pymongo")
        fake.MongoClient = object
        monkeypatch.setitem(sys.modules, "pymongo", fake)
        for name, val in [("_mongo_checked", False), ("MONGODB_AVAILABLE", False),
                          ("pymongo", None), ("MongoClient", None)]:
            monkeypatch.setattr(dbm, name, val)
        assert dbm._ensure_pymongo() is True
        assert dbm.MongoClient is object
        assert dbm._ensure_pymongo() is True

    def test_ensure_pymongo_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pymongo", None)
        monkeypatch.setattr(dbm, "_mongo_checked", False)
        monkeypatch.setattr(dbm, "MONGODB_AVAILABLE", True)
        assert dbm._ensure_pymongo() is False


def test_database_config_defaults():
    c = DatabaseConfig()
    assert c.database_type == "sql" and c.streamer_name == "mycelian"
    assert c.connection_timeout == 30 and c.retry_attempts == 3


def test_interface_is_abstract():
    with pytest.raises(TypeError):
        dbm.DatabaseInterface()

    class Minimal(dbm.DatabaseInterface):
        def initialize(self): return super().initialize()
        def get_data(self, path, request_etag=False): return super().get_data(path, request_etag)
        def set_data(self, path, data): return super().set_data(path, data)
        def update_data(self, path, data): return super().update_data(path, data)
        def delete_data(self, path): return super().delete_data(path)
        def get_connection_status(self): return super().get_connection_status()
        def test_connection(self): return super().test_connection()
        async def get_multiple_data_async(self, paths): return await super().get_multiple_data_async(paths)
        def get_all_paths(self): return super().get_all_paths()
        def get_snapshot(self): return super().get_snapshot()

    m = Minimal()
    assert m.initialize() is None and m.get_data("x") is None and m.set_data("x", {}) is None
    assert m.update_data("x", {}) is None and m.delete_data("x") is None
    assert m.get_connection_status() is None and m.test_connection() is None
    assert asyncio.run(m.get_multiple_data_async([])) is None
    assert m.get_all_paths() is None and m.get_snapshot() is None
    assert m.last_error == "" and m.snapshot_error == ""


# ======================================================================= SQLDatabase

class TestSQLInit:
    def test_relative_path_resolves_in_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
        db = SQLDatabase(DatabaseConfig(sql_database_path="rel.db"))
        assert db.db_path == os.path.join(str(tmp_path), "rel.db")

    def test_absolute_path_used_as_is(self, tmp_path):
        p = str(tmp_path / "abs.db")
        db = SQLDatabase(DatabaseConfig(sql_database_path=p, streamer_name="s"))
        assert db.db_path == p and db.streamer_name == "s"
        assert db._pool_size == 3 and db._initialized is False

    def test_initialize_creates_dir_tables_index_trigger(self, tmp_path):
        p = tmp_path / "nested" / "dir" / "x.db"
        db = SQLDatabase(DatabaseConfig(sql_database_path=str(p)))
        assert db.initialize() is True
        assert p.exists() and db._initialized
        names = {r[0] for r in db._connection.execute("SELECT name FROM sqlite_master")}
        assert {"app_data", "idx_alert_storage_ts", "update_timestamp"} <= names
        mode = db._connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        _close(db)

    def test_initialize_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = SQLDatabase(DatabaseConfig(sql_database_path=""))
        db.db_path = "bare.db"
        assert db.initialize() is True
        assert (tmp_path / "bare.db").exists()
        _close(db)

    def test_initialize_failure_sets_last_error(self, tmp_path):
        d = tmp_path / "isdir.db"
        d.mkdir()
        db = SQLDatabase(DatabaseConfig(sql_database_path=str(d)))
        assert db.initialize() is False
        assert db.last_error.startswith("Couldn't open")
        assert not db._initialized

    def test_create_tables_index_error_is_tolerated(self, tmp_path):
        db = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / "x.db")))
        real = sqlite3.connect(db.db_path)

        class Wrap:
            def __init__(self):
                self.statements = []

            def cursor(self):
                return self

            def execute(self, statement, *a):
                self.statements.append(statement)
                if "idx_alert_storage_ts" in statement:
                    raise sqlite3.OperationalError("no json1")
                return real.execute(statement, *a)

            def commit(self):
                real.commit()

        db._connection = Wrap()
        db._create_tables()
        assert any("update_timestamp" in s for s in db._connection.statements)
        real.close()

    def test_configure_connection_swallows_errors(self):
        SQLDatabase._configure_app_sqlite_connection(BrokenConn())


class TestSQLPool:
    def test_pool_reuse_and_overflow(self, sql):
        conns = [sql._get_connection() for _ in range(5)]
        assert len({id(c) for c in conns}) == 5
        for c in conns:
            sql._return_connection(c)
        assert len(sql._connection_pool) == 3
        with pytest.raises(sqlite3.ProgrammingError):
            conns[-1].execute("SELECT 1")  # closed (pool was full)
        top = sql._connection_pool[-1]
        assert sql._get_connection() is top
        sql._return_connection(top)


class TestSQLCrud:
    def test_get_missing(self, sql):
        assert sql.get_data("nope") == {}
        assert sql.get_data("nope", request_etag=True) == {"data": {}, "etag": None}

    def test_set_get_with_slash_and_etag(self, sql):
        assert sql.set_data("/A/B", {"x": 1}) is True
        assert sql.get_data("A/B") == {"x": 1}
        res = sql.get_data("/A/B", request_etag=True)
        assert res["data"] == {"x": 1} and res["etag"]

    def test_set_overwrites_and_keeps_created_at(self, sql):
        sql.set_data("p", {"v": 1})
        created = sql._connection.execute("SELECT created_at FROM app_data WHERE data_path='p'").fetchone()[0]
        sql.set_data("p", {"v": 2})
        rows = sql._connection.execute("SELECT created_at, data_json FROM app_data WHERE data_path='p'").fetchall()
        assert len(rows) == 1 and rows[0][0] == created and json.loads(rows[0][1]) == {"v": 2}

    def test_set_non_json_values_use_str(self, sql):
        from datetime import datetime

        sql.set_data("d", {"when": datetime(2020, 1, 1)})
        assert sql.get_data("d") == {"when": "2020-01-01 00:00:00"}

    def test_lazy_initialize(self, tmp_path):
        db = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / "lazy.db")))
        assert db.set_data("k", {"a": 1})
        assert db._initialized
        _close(db)
        for method, args in [("get_data", ("k",)), ("update_data", ("k", {})), ("delete_data", ("k",)),
                             ("get_all_paths", ()), ("get_snapshot", ()), ("fetch_alert_storage", ()),
                             ("list_alert_storage_meta", ()), ("query_alert_storage_page", (1, 1)),
                             ("delete_paths", (["k"],)), ("_get_multiple_data_checked", (["k"],))]:
            fresh = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / f"{method}.db")))
            getattr(fresh, method)(*args)
            assert fresh._initialized, method
            _close(fresh)

    def test_get_corrupt_json_raises_read_error(self, sql):
        _put_raw(sql, "bad", "{nope")
        with pytest.raises(DatabaseReadError):
            sql.get_data("bad")

    def test_set_failure_returns_false(self, sql, monkeypatch):
        monkeypatch.setattr(sql, "_get_connection", lambda: BrokenConn())
        assert sql.set_data("x", {"a": 1}) is False

    def test_get_connection_failure_raises(self, sql, monkeypatch):
        monkeypatch.setattr(sql, "_get_connection", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
        with pytest.raises(DatabaseReadError):
            sql.get_data("x")
        assert sql.update_data("x", {}) is False
        assert sql.delete_data("x") is False
        assert sql.get_all_paths() == []
        assert sql.delete_paths(["x"]) is False

    def test_update_creates_merges_and_replaces_empty(self, sql):
        assert sql.update_data("/u", {"a": 1}) is True
        assert sql.update_data("u", {"b": 2}) is True
        assert sql.get_data("u") == {"a": 1, "b": 2}
        sql.set_data("e", {})
        assert sql.update_data("e", {"c": 3}) is True
        assert sql.get_data("e") == {"c": 3}

    def test_update_corrupt_existing_returns_false(self, sql):
        _put_raw(sql, "bad", "{nope")
        assert sql.update_data("bad", {"a": 1}) is False
        assert sql._connection.execute("SELECT data_json FROM app_data WHERE data_path='bad'").fetchone()[0] == "{nope"

    def test_update_non_dict_existing_returns_false(self, sql):
        sql.set_data("lst", [1, 2])
        assert sql.update_data("lst", {"a": 1}) is False

    def test_delete(self, sql):
        sql.set_data("x", {"a": 1})
        assert sql.delete_data("/x") is True
        assert sql.get_data("x") == {}
        assert sql.delete_data("missing") is True

    def test_delete_execute_error(self, sql, monkeypatch):
        monkeypatch.setattr(sql, "_get_connection", lambda: BrokenConn())
        assert sql.delete_data("x") is False
        assert sql.update_data("x", {}) is False


class TestSQLStatus:
    def test_connected(self, sql):
        s = sql.get_connection_status()
        assert s["status"] == "Connected" and s["is_connected"] and s["database_type"] == "SQLite"
        assert s["database_path"] == sql.db_path and s["streamer_name"] == "mycelian"

    def test_disconnected(self, tmp_path):
        db = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / "x.db")))
        s = db.get_connection_status()
        assert s["status"] == "Disconnected" and s["is_connected"] is False

    def test_error(self, sql):
        sql._connection.close()
        s = sql.get_connection_status()
        assert s["status"].startswith("Error:") and s["is_connected"] is False

    def test_test_connection(self, sql, tmp_path, monkeypatch):
        assert sql.test_connection() is True
        fresh = SQLDatabase(DatabaseConfig(sql_database_path=str(tmp_path / "f.db")))
        assert fresh.test_connection() is True and fresh._initialized
        _close(fresh)
        monkeypatch.setattr(sql, "_get_connection", lambda: BrokenConn())
        assert sql.test_connection() is False


class TestSQLMulti:
    def test_empty(self, sql):
        assert sql._get_multiple_data_checked([]) == ({}, set())

    def test_mixed_and_chunked(self, sql, monkeypatch):
        monkeypatch.setattr(dbm, "_SQL_IN_CHUNK", 2)
        for i in range(5):
            sql.set_data(f"p{i}", {"i": i})
        _put_raw(sql, "bad", "{x")
        paths = ["p0", "/p1", "p2", "p3", "p4", "missing", "bad", "p0"]
        out, failed = sql._get_multiple_data_checked(paths)
        assert out["p0"] == {"i": 0} and out["/p1"] == {"i": 1} and out["p4"] == {"i": 4}
        assert out["missing"] == {} and out["bad"] == {}
        assert failed == {"bad"}
        assert sql._get_multiple_data(["p2"]) == {"p2": {"i": 2}}

    def test_error_marks_all_failed(self, sql, monkeypatch):
        monkeypatch.setattr(sql, "_get_connection", lambda: BrokenConn())
        out, failed = sql._get_multiple_data_checked(["a", "b"])
        assert out == {"a": {}, "b": {}} and failed == {"a", "b"}

    def test_async_variants(self, sql):
        sql.set_data("a", {"v": 1})

        async def go():
            return (await sql.get_multiple_data_async(["a", "b"]),
                    await sql.get_multiple_data_checked_async(["a"]))

        plain, checked = asyncio.run(go())
        assert plain == {"a": {"v": 1}, "b": {}}
        assert checked == ({"a": {"v": 1}}, set())


class TestSQLPathsSnapshot:
    def test_all_paths_sorted_and_streamer_scoped(self, sql):
        sql.set_data("b", {"x": 1})
        sql.set_data("a/c", {"x": 1})
        _raw(sql, "INSERT INTO app_data (streamer_name, data_path, data_json) VALUES ('other','z','{}')")
        assert sql.get_all_paths() == ["a/c", "b"]

    def test_snapshot_nested_including_empty(self, sql):
        sql.set_data("A/B/C", {"v": 1})
        sql.set_data("A/D", {})
        sql.set_data("Top", {"t": 2})
        assert sql.get_snapshot() == {"A": {"B": {"C": {"v": 1}}, "D": {}}, "Top": {"t": 2}}

    def test_snapshot_corrupt_sets_error(self, sql):
        _put_raw(sql, "bad", "{x")
        assert sql.get_snapshot() == {}
        assert "Invalid JSON at bad" in sql.snapshot_error

    def test_set_nested_value(self, sql):
        d = {"a": {"x": 1}}
        sql._set_nested_value(d, "a/b/c", 5)
        sql._set_nested_value(d, "z", 1)
        assert d == {"a": {"x": 1, "b": {"c": 5}}, "z": 1}


class TestSQLAlertStorage:
    @pytest.fixture
    def alerts(self, sql):
        sql.set_data("Alerts/AlertStorage", {"parent": True})
        for i, ts in enumerate([10, 30, None, "20"]):
            data = {"n": i}
            if ts is not None:
                data["timestamp"] = ts
            sql.set_data(f"Alerts/AlertStorage/a{i}", data)
        sql.set_data("Alerts/Other/x", {"timestamp": 99})
        return sql

    def test_fetch_all(self, alerts):
        out = alerts.fetch_alert_storage()
        assert set(out) == {"a0", "a1", "a2", "a3"}
        assert out["a1"] == {"n": 1, "timestamp": 30}

    def test_fetch_limited_newest(self, alerts):
        assert set(alerts.fetch_alert_storage(max_alerts=2)) == {"a1", "a3"}
        assert set(alerts.fetch_alert_storage(max_alerts=0)) == {"a0", "a1", "a2", "a3"}

    def test_fetch_skips_corrupt(self, alerts):
        # JSON5 is accepted by SQLite's json functions (and the index) but not by json.loads.
        _put_raw(alerts, "Alerts/AlertStorage/bad", "{a:1}")
        assert "bad" not in alerts.fetch_alert_storage()

    def test_meta_newest_first(self, alerts):
        assert alerts.list_alert_storage_meta() == [("a1", 30.0), ("a3", 20.0), ("a0", 10.0), ("a2", 0.0)]

    def test_page(self, alerts):
        _put_raw(alerts, "Alerts/AlertStorage/bad", "{a:1}")
        alerts.set_data("Alerts/AlertStorage/lst", [1, 2])
        p1 = alerts.query_alert_storage_page(1, 2)
        assert p1["total_count"] == 6
        assert [a["alert_id"] for a in p1["alerts"]] == ["a1", "a3"]
        rest = alerts.query_alert_storage_page(2, 2)["alerts"] + alerts.query_alert_storage_page(3, 2)["alerts"]
        ids = [a["alert_id"] for a in rest]
        assert ids[0] == "a0"
        assert "bad" not in ids and set(ids) == {"a0", "a2", "lst"}  # JSON5 row skipped
        assert next(a for a in rest if a["alert_id"] == "lst") == {"alert_id": "lst"}

    def test_page_clamps_bad_input(self, alerts):
        out = alerts.query_alert_storage_page(0, 0)
        assert len(out["alerts"]) == 1 and out["alerts"][0]["alert_id"] == "a1"
        out = alerts.query_alert_storage_page(None, None)
        assert len(out["alerts"]) == 1


class TestSQLDeletePaths:
    def test_empty_is_true(self, sql):
        assert sql.delete_paths([]) is True

    def test_chunked(self, sql, monkeypatch):
        monkeypatch.setattr(dbm, "_SQL_IN_CHUNK", 2)
        for i in range(5):
            sql.set_data(f"x{i}", {"i": i})
        assert sql.delete_paths(["x0", "/x1", "x2", "x3"]) is True
        assert sql.get_all_paths() == ["x4"]

    def test_error_rolls_back(self, sql, monkeypatch):
        conn = BrokenConn()
        monkeypatch.setattr(sql, "_get_connection", lambda: conn)
        monkeypatch.setattr(sql, "_return_connection", lambda c: None)
        assert sql.delete_paths(["a"]) is False
        assert conn.rolled_back

    def test_rollback_error_swallowed(self, sql, monkeypatch):
        conn = BrokenConn(rollback_exc=RuntimeError("rb"))
        monkeypatch.setattr(sql, "_get_connection", lambda: conn)
        monkeypatch.setattr(sql, "_return_connection", lambda c: None)
        assert sql.delete_paths(["a"]) is False

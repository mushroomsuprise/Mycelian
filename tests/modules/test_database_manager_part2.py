# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/database_manager.py, part 2: Firebase and MongoDB backends.

firebase_admin and pymongo are replaced with in-memory fakes; nothing touches the network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import database_manager as dbm
from modules.database_manager import DatabaseConfig, DatabaseReadError, FirebaseDatabase, MongoDatabase


# ======================================================================= Firebase fakes

class FakeStore:
    def __init__(self, tree=None):
        self.tree = tree or {}
        self.calls = []
        self.fail = {}  # op -> exception (op: get, set, update, delete, query, probe, child:<key>)


class FakeQuery:
    def __init__(self, ref, key):
        self.ref, self.key, self.n = ref, key, None

    def limit_to_last(self, n):
        self.n = n
        return self

    def get(self):
        store = self.ref.store
        store.calls.append(("query", self.ref.path, self.key, self.n))
        if "query" in store.fail:
            raise store.fail["query"]
        node = self.ref._node()
        if not isinstance(node, dict):
            return node
        items = sorted(node.items(), key=lambda kv: dbm._coerce_alert_timestamp(
            kv[1].get(self.key) if isinstance(kv[1], dict) else None))
        return dict(items[-self.n:])


class FakeRef:
    def __init__(self, store, path):
        self.store = store
        self.path = path.strip("/")

    def _parts(self):
        return [p for p in self.path.split("/") if p]

    def _node(self):
        cur = self.store.tree
        for p in self._parts():
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return copy.deepcopy(cur)

    def _parent_for_write(self):
        cur = self.store.tree
        parts = self._parts()
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        return cur, parts[-1]

    def child(self, path):
        return FakeRef(self.store, f"{self.path}/{path.strip('/')}")

    def get(self, etag=False, shallow=False):
        self.store.calls.append(("get", self.path, etag, shallow))
        key = "probe" if self.path.endswith(dbm._FIREBASE_PROBE_CHILD) and shallow else "get"
        if key in self.store.fail:
            raise self.store.fail[key]
        last = self._parts()[-1] if self._parts() else ""
        if f"child:{last}" in self.store.fail and not shallow:
            raise self.store.fail[f"child:{last}"]
        if f"shallow:{last}" in self.store.fail and shallow:
            raise self.store.fail[f"shallow:{last}"]
        node = self._node()
        if shallow and isinstance(node, dict):
            node = {k: True for k in node}
        if etag:
            return node, "etag-1"
        return node

    def set(self, data):
        self.store.calls.append(("set", self.path, data))
        if "set" in self.store.fail:
            raise self.store.fail["set"]
        parent, key = self._parent_for_write()
        parent[key] = copy.deepcopy(data)

    def update(self, data):
        self.store.calls.append(("update", self.path, data))
        if "update" in self.store.fail:
            raise self.store.fail["update"]
        for k, v in data.items():
            ref = self.child(k) if self.path else FakeRef(self.store, k)
            parent, last = ref._parent_for_write()
            if v is None:
                parent.pop(last, None)
            else:
                parent[last] = copy.deepcopy(v)

    def delete(self):
        self.store.calls.append(("delete", self.path))
        if "delete" in self.store.fail:
            raise self.store.fail["delete"]
        parent, key = self._parent_for_write()
        parent.pop(key, None)

    def order_by_child(self, key):
        return FakeQuery(self, key)


class FakeAdmin(types.ModuleType):
    def __init__(self):
        super().__init__("firebase_admin")
        self.apps = {}
        self.init_calls = []
        self.init_errors = []
        self.get_app_errors = []
        self.credentials = types.SimpleNamespace(Certificate=lambda p: ("cert", p))

    def get_app(self, name="[DEFAULT]"):
        if self.get_app_errors:
            raise self.get_app_errors.pop(0)
        if name in self.apps:
            return self.apps[name]
        raise ValueError("no app")

    def initialize_app(self, cred, options, name="[DEFAULT]"):
        self.init_calls.append((cred, options, name))
        if self.init_errors:
            raise self.init_errors.pop(0)
        app = types.SimpleNamespace(name=name)
        self.apps[name] = app
        return app


class FakeFbDb:
    def __init__(self, store):
        self.store = store
        self.refs = []
        self.error = None

    def reference(self, path, app=None):
        if self.error:
            raise self.error
        self.refs.append((path, app))
        return FakeRef(self.store, path)


KEY = {"type": "service_account", "project_id": "p", "private_key_id": "k",
       "private_key": "pk", "client_email": "e@x"}


@pytest.fixture
def fb_env(monkeypatch, tmp_path):
    store = FakeStore()
    admin = FakeAdmin()
    fdb = FakeFbDb(store)
    monkeypatch.setattr(dbm, "_firebase_checked", True)
    monkeypatch.setattr(dbm, "FIREBASE_AVAILABLE", True)
    monkeypatch.setattr(dbm, "firebase_admin", admin)
    monkeypatch.setattr(dbm, "firebase_db", fdb)
    key = tmp_path / "key.json"
    key.write_text(json.dumps(KEY))
    cfg = DatabaseConfig(database_type="firebase", firebase_service_account_path=str(key),
                         firebase_database_url="https://x.firebaseio.com", connection_timeout=12)
    return types.SimpleNamespace(store=store, admin=admin, fdb=fdb, key=key, cfg=cfg)


@pytest.fixture
def fb(fb_env):
    db = FirebaseDatabase(fb_env.cfg)
    assert db.initialize()
    fb_env.db = db
    return fb_env


def _tree(env):
    return env.store.tree.setdefault("mycelian", {})


# ======================================================================= Firebase: init

class TestFirebaseInit:
    def test_constructor(self, fb_env):
        db = FirebaseDatabase(fb_env.cfg)
        assert db.database_url == "https://x.firebaseio.com/"
        assert db._http_timeout() == 12
        db.config.connection_timeout = 0
        assert db._http_timeout() == 30
        db.config.connection_timeout = 1
        assert db._http_timeout() == 5

    def test_success_creates_app(self, fb_env):
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is True
        cred, options, name = fb_env.admin.init_calls[0]
        assert cred == ("cert", str(fb_env.key))
        assert options == {"databaseURL": "https://x.firebaseio.com/", "httpTimeout": 12}
        assert fb_env.fdb.refs[0][0] == "/mycelian"
        assert db._initialized and not db._connection_tested
        assert fb_env.store.calls == []  # connection test deferred

    def test_reuses_existing_app(self, fb_env):
        fb_env.admin.apps["[DEFAULT]"] = "existing"
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is True
        assert db._app == "existing" and fb_env.admin.init_calls == []

    def test_already_exists_then_get_app(self, fb_env):
        fb_env.admin.get_app_errors = [ValueError("none")]
        fb_env.admin.apps["[DEFAULT]"] = "raced"
        fb_env.admin.init_errors = [ValueError("The default Firebase app already exists.")]
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is True
        assert db._app == "raced"

    def test_already_exists_unique_name(self, fb_env):
        fb_env.admin.init_errors = [ValueError("app already exists")]
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is True
        assert fb_env.admin.init_calls[-1][2].startswith("mycelian_")

    def test_other_value_error_fails(self, fb_env):
        fb_env.admin.init_errors = [ValueError("bad cert")]
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "Firebase refused the connection: bad cert" == db.last_error

    def test_reference_error_fails(self, fb_env):
        fb_env.fdb.error = RuntimeError("ref")
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False and not db._initialized

    def test_sdk_missing(self, fb_env, monkeypatch):
        monkeypatch.setattr(dbm, "FIREBASE_AVAILABLE", False)
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "firebase-admin" in db.last_error

    @pytest.mark.parametrize("field,msg", [
        ("firebase_service_account_path", "No Firebase service account key file"),
        ("firebase_database_url", "No Firebase database URL"),
    ])
    def test_missing_config(self, fb_env, field, msg):
        setattr(fb_env.cfg, field, "")
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert msg in db.last_error

    def test_key_file_missing(self, fb_env, tmp_path):
        fb_env.cfg.firebase_service_account_path = str(tmp_path / "nope.json")
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "wasn't found" in db.last_error

    def test_key_missing_fields(self, fb_env):
        fb_env.key.write_text(json.dumps({"type": "x"}))
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "project_id" in db.last_error and "client_email" in db.last_error

    def test_key_invalid_json(self, fb_env):
        fb_env.key.write_text("{")
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "isn't valid JSON" in db.last_error

    def test_invalid_url(self, fb_env):
        fb_env.cfg.firebase_database_url = "https://example.com"
        db = FirebaseDatabase(fb_env.cfg)
        assert db.initialize() is False
        assert "must look like" in db.last_error


# ======================================================================= Firebase: probe

class TestFirebaseProbe:
    def test_probe_requires_root(self, fb_env):
        db = FirebaseDatabase(fb_env.cfg)
        with pytest.raises(RuntimeError):
            db._probe_connection()

    def test_probe_is_shallow_small_child(self, fb):
        fb.db._probe_connection()
        assert fb.store.calls == [("get", "mycelian/DatabaseSettings", False, True)]

    def test_ensure_tested_once(self, fb):
        fb.db._ensure_connection_tested()
        fb.db._ensure_connection_tested()
        assert len(fb.store.calls) == 1 and fb.db._connection_tested
        assert fb.db._probe_flight is None

    def test_ensure_failure_raises_and_resets(self, fb):
        fb.store.fail["probe"] = RuntimeError("offline")
        with pytest.raises(RuntimeError, match="offline"):
            fb.db._ensure_connection_tested()
        assert not fb.db._connection_tested and fb.db._probe_flight is None
        del fb.store.fail["probe"]
        fb.db._ensure_connection_tested()
        assert fb.db._connection_tested

    def test_follower_shares_success(self, fb):
        ev = threading.Event()
        ev.set()
        fb.db._probe_flight = {"done": ev, "error": None}
        fb.db._ensure_connection_tested()
        assert fb.store.calls == []

    def test_follower_shares_error(self, fb):
        ev = threading.Event()
        ev.set()
        fb.db._probe_flight = {"done": ev, "error": RuntimeError("shared")}
        with pytest.raises(RuntimeError, match="shared"):
            fb.db._ensure_connection_tested()

    def test_tested_while_waiting_for_lock(self, fb):
        db = fb.db

        class Lock:
            def __enter__(self):
                db._connection_tested = True

            def __exit__(self, *a):
                return False

        db._probe_lock = Lock()
        db._ensure_connection_tested()
        assert fb.store.calls == []

    def test_concurrent_callers_share_one_probe(self, fb):
        gate = threading.Event()
        started = threading.Event()
        calls = []

        def slow_probe():
            calls.append(1)
            started.set()
            gate.wait(2)

        fb.db._probe_connection = slow_probe
        t1 = threading.Thread(target=fb.db._ensure_connection_tested)
        t1.start()
        assert started.wait(2)
        t2 = threading.Thread(target=fb.db._ensure_connection_tested)
        t2.start()
        gate.set()
        t1.join(2)
        t2.join(2)
        assert calls == [1] and fb.db._connection_tested


# ======================================================================= Firebase: CRUD

class TestFirebaseCrud:
    def test_get_data(self, fb):
        _tree(fb)["A"] = {"B": {"x": 1}}
        assert fb.db.get_data("/A/B") == {"x": 1}
        assert fb.db.get_data("A/missing") == {}
        assert fb.db.get_data("A/B", request_etag=True) == {"data": {"x": 1}, "etag": "etag-1"}
        assert fb.db.get_data("nope", request_etag=True) == {"data": {}, "etag": "etag-1"}

    def test_get_data_debug_logging(self, fb, caplog):
        caplog.set_level(logging.DEBUG, logger=dbm.logger.name)
        _tree(fb)["A"] = {"x": 1}
        assert fb.db.get_data("A") == {"x": 1}
        assert "approx_bytes" in caplog.text

    def test_get_data_debug_logging_failure_ignored(self, fb, caplog, monkeypatch):
        caplog.set_level(logging.DEBUG, logger=dbm.logger.name)
        _tree(fb)["A"] = {"x": 1}
        monkeypatch.setattr(dbm.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(TypeError("no")))
        assert fb.db.get_data("A") == {"x": 1}

    def test_get_data_lazy_init_failure(self, fb_env):
        fb_env.cfg.firebase_database_url = ""
        db = FirebaseDatabase(fb_env.cfg)
        with pytest.raises(DatabaseReadError, match="not initialized"):
            db.get_data("x")

    def test_get_data_lazy_init_success(self, fb_env):
        db = FirebaseDatabase(fb_env.cfg)
        assert db.get_data("x") == {}
        assert db._initialized

    def test_get_data_error(self, fb):
        fb.store.fail["get"] = RuntimeError("timeout")
        with pytest.raises(DatabaseReadError, match="timeout"):
            fb.db.get_data("A")

    def test_set_update_delete(self, fb):
        assert fb.db.set_data("/A/B", {"x": 1}) is True
        assert _tree(fb)["A"]["B"] == {"x": 1}
        assert fb.db.update_data("/A/B", {"y": 2}) is True
        assert _tree(fb)["A"]["B"] == {"x": 1, "y": 2}
        assert fb.db.delete_data("/A/B") is True
        assert "B" not in _tree(fb)["A"]

    @pytest.mark.parametrize("op", ["set", "update", "delete"])
    def test_write_errors(self, fb, op):
        fb.store.fail[op] = RuntimeError("denied")
        if op == "delete":
            assert fb.db.delete_data("A") is False
        else:
            assert getattr(fb.db, f"{op}_data")("A", {"x": 1}) is False

    @pytest.mark.parametrize("op", ["set_data", "update_data", "delete_data"])
    def test_write_not_initialized(self, fb_env, op):
        fb_env.cfg.firebase_database_url = ""
        db = FirebaseDatabase(fb_env.cfg)
        args = ("A",) if op == "delete_data" else ("A", {})
        assert getattr(db, op)(*args) is False


# ======================================================================= Firebase: status

class TestFirebaseStatus:
    def test_connected(self, fb):
        s = fb.db.get_connection_status()
        assert s["status"] == "Connected" and s["is_connected"] and s["config_valid"]

    def test_probe_error(self, fb):
        fb.store.fail["probe"] = RuntimeError("offline")
        s = fb.db.get_connection_status()
        assert s["status"] == "Error: offline" and s["error_details"] == "offline"
        assert s["config_valid"] is False

    def test_not_initialized_no_issues(self, fb_env):
        s = FirebaseDatabase(fb_env.cfg).get_connection_status()
        assert s["status"] == "Not Initialized" and s["config_valid"] is True and s["config_issues"] == []

    def test_not_initialized_all_issues(self, fb_env, monkeypatch):
        monkeypatch.setattr(dbm, "FIREBASE_AVAILABLE", False)
        fb_env.cfg.firebase_service_account_path = ""
        fb_env.cfg.firebase_database_url = ""
        s = FirebaseDatabase(fb_env.cfg).get_connection_status()
        assert s["config_issues"] == ["Firebase SDK not available", "Service account path not configured",
                                      "Database URL not configured"]
        assert s["status"].startswith("Configuration issues:")

    def test_not_initialized_missing_key_bad_url(self, fb_env, tmp_path):
        fb_env.cfg.firebase_service_account_path = str(tmp_path / "missing.json")
        fb_env.cfg.firebase_database_url = "https://example.com"
        s = FirebaseDatabase(fb_env.cfg).get_connection_status()
        assert any("Service account key not found" in i for i in s["config_issues"])
        assert "Invalid database URL format" in s["config_issues"]

    def test_test_connection(self, fb, fb_env):
        assert fb.db.test_connection() is True
        fb.store.fail["probe"] = RuntimeError("x")
        assert fb.db.test_connection() is False
        fresh = FirebaseDatabase(fb_env.cfg)
        assert fresh.test_connection() is True and fresh._initialized


# ======================================================================= Firebase: multi

class TestFirebaseMulti:
    def test_empty(self, fb):
        assert asyncio.run(fb.db.get_multiple_data_checked_async([])) == ({}, set())

    def test_mixed(self, fb, monkeypatch):
        _tree(fb).update({"A": {"x": 1}, "C": {"z": 3}})
        real = fb.db.get_data

        def get(path, request_etag=False):
            if path == "B":
                raise DatabaseReadError("bad")
            return real(path)

        monkeypatch.setattr(fb.db, "get_data", get)
        out, failed = asyncio.run(fb.db.get_multiple_data_checked_async(["A", "B", "C", "D"]))
        assert out == {"A": {"x": 1}, "B": {}, "C": {"z": 3}, "D": {}}
        assert failed == {"B"}
        assert asyncio.run(fb.db.get_multiple_data_async(["A"])) == {"A": {"x": 1}}


# ======================================================================= Firebase: paths/snapshot

class TestFirebasePaths:
    def test_all_paths(self, fb):
        assert fb.db.get_all_paths() == []
        _tree(fb).update({"b": {"x": 1}, "a": 1})
        assert fb.db.get_all_paths() == ["a", "b"]

    def test_all_paths_non_dict_and_error(self, fb, monkeypatch):
        fb.store.tree["mycelian"] = "scalar"
        monkeypatch.setattr(FakeRef, "get", lambda self, etag=False, shallow=False: "scalar")
        assert fb.db.get_all_paths() == []
        monkeypatch.setattr(FakeRef, "get", lambda self, etag=False, shallow=False: (_ for _ in ()).throw(RuntimeError()))
        assert fb.db.get_all_paths() == []

    def test_all_paths_not_initialized(self, fb_env):
        fb_env.cfg.firebase_database_url = ""
        db = FirebaseDatabase(fb_env.cfg)
        assert db.get_all_paths() == []
        assert db.get_migration_paths() == []
        assert db.get_snapshot() == {}

    def test_migration_paths(self, fb):
        _tree(fb).update({
            "Alerts": {"BitAlerts": {"a": 1}, "AlertStorage": {"id1": {"t": 1}}},
            "AppSettings": {"theme": "dark", "nested": {"x": 1}},
            "Empty": {},
            "Scalar": 5,
            "Broken": {"x": {"y": 1}},
        })
        fb.store.fail["child:Broken"] = RuntimeError("read")
        assert fb.db.get_migration_paths() == [
            "Alerts/AlertStorage", "Alerts/BitAlerts", "AppSettings", "Broken", "Empty", "Scalar",
        ]

    def test_migration_paths_empty_top(self, fb):
        assert fb.db.get_migration_paths() == []

    def test_migration_paths_error_falls_back(self, fb, monkeypatch):
        _tree(fb)["X"] = {"a": 1}
        calls = {"n": 0}
        real_get = FakeRef.get

        def get(self, etag=False, shallow=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first shallow read fails")
            return real_get(self, etag, shallow)

        monkeypatch.setattr(FakeRef, "get", get)
        assert fb.db.get_migration_paths() == ["X"]

    def test_extract_paths(self, fb):
        paths = []
        fb.db._extract_paths({"A": {"x": 1, "B": {"y": 2}}, "C": {"D": {"E": {"z": 1}}}, "top": 1}, "", paths)
        assert sorted(paths) == ["A", "A/B", "C/D/E"]
        paths = []
        fb.db._extract_paths("not a dict", "x", paths)
        assert paths == []

    def test_extract_paths_data_node_listed_before_leaves(self, fb):
        # A node with data values is appended up front, so the per-leaf append
        # (database_manager.py ~1451) never adds it a second time.
        paths = ["P"]
        fb.db._extract_paths({"a": 1, "b": 2}, "P", paths)
        assert paths == ["P", "P"]

    def test_snapshot(self, fb):
        assert fb.db.get_snapshot() == {}
        _tree(fb)["A"] = {"x": 1}
        assert fb.db.get_snapshot() == {"A": {"x": 1}}

    def test_snapshot_error(self, fb):
        fb.store.fail["get"] = RuntimeError("too big")
        assert fb.db.get_snapshot() == {}
        assert fb.db.snapshot_error == "too big"


# ======================================================================= Firebase: alert storage

def _alerts(env, n=5):
    storage = {f"a{i}": {"timestamp": i * 10, "n": i} for i in range(n)}
    storage["junk"] = "not a dict"
    _tree(env).setdefault("Alerts", {})["AlertStorage"] = storage
    return storage


class TestFirebaseAlerts:
    def test_limited(self, fb):
        _alerts(fb)
        out = fb.db.get_alert_storage_limited(2)
        assert set(out) == {"a3", "a4"}
        assert fb.db.get_alert_storage_limited(0) == {}

    def test_limited_empty_falls_back_to_full_read(self, fb, monkeypatch):
        monkeypatch.setattr(FakeQuery, "get", lambda self: {})
        _alerts(fb, 2)
        assert set(fb.db.get_alert_storage_limited(5)) == {"a0", "a1", "junk"}

    def test_limited_query_error_falls_back(self, fb):
        _alerts(fb, 2)
        fb.store.fail["query"] = RuntimeError("index not defined")
        assert set(fb.db.get_alert_storage_limited(1)) == {"a0", "a1", "junk"}

    def test_limited_not_initialized(self, fb_env):
        fb_env.cfg.firebase_database_url = ""
        assert FirebaseDatabase(fb_env.cfg).get_alert_storage_limited(5) == {}

    def test_fetch(self, fb):
        _alerts(fb, 3)
        assert set(fb.db.fetch_alert_storage()) == {"a0", "a1", "a2", "junk"}
        assert set(fb.db.fetch_alert_storage(max_alerts=1)) == {"a2"}

    def test_fetch_non_dict(self, fb, monkeypatch):
        monkeypatch.setattr(fb.db, "get_data", lambda p: [1, 2])
        assert fb.db.fetch_alert_storage() == {}

    def test_meta(self, fb):
        _alerts(fb, 3)
        assert fb.db.list_alert_storage_meta() == [("a2", 20.0), ("a1", 10.0), ("a0", 0.0)]

    def test_alert_storage_ref(self, fb, fb_env):
        assert fb.db._alert_storage_ref().path == "mycelian/Alerts/AlertStorage"
        fb_env.cfg.firebase_database_url = ""
        assert FirebaseDatabase(fb_env.cfg)._alert_storage_ref() is None

    def test_alert_count(self, fb, monkeypatch):
        _alerts(fb, 3)
        ref = fb.db._alert_storage_ref()
        assert fb.db._firebase_alert_count(ref) == 4
        fb.store.fail["shallow:AlertStorage"] = TypeError("old sdk")
        assert fb.db._firebase_alert_count(ref) == 4
        fb.store.fail["shallow:AlertStorage"] = RuntimeError("other")
        assert fb.db._firebase_alert_count(ref) == 4
        monkeypatch.setattr(fb.db, "get_data", lambda p: [1])
        assert fb.db._firebase_alert_count(ref) == 0

    def test_alert_count_shallow_non_dict(self, fb):
        ref = fb.db._alert_storage_ref()
        assert fb.db._firebase_alert_count(ref) == 0

    def test_page(self, fb):
        _alerts(fb, 5)
        p1 = fb.db.query_alert_storage_page(1, 2)
        assert p1["total_count"] == 6
        assert [a["alert_id"] for a in p1["alerts"]] == ["a4", "a3"]
        assert fb.store.calls[-1][-1] == 2
        p2 = fb.db.query_alert_storage_page(2, 2)
        assert [a["alert_id"] for a in p2["alerts"]] == ["a2", "a1"]
        assert fb.store.calls[-1][-1] == 4

    def test_page_clamps(self, fb):
        _alerts(fb, 2)
        assert len(fb.db.query_alert_storage_page(0, None)["alerts"]) == 1

    def test_page_query_error_and_non_dict(self, fb, monkeypatch):
        _alerts(fb, 3)
        fb.store.fail["query"] = RuntimeError("no index")
        assert [a["alert_id"] for a in fb.db.query_alert_storage_page(1, 2)["alerts"]] == ["a2", "a1"]
        del fb.store.fail["query"]
        monkeypatch.setattr(FakeQuery, "get", lambda self: None)
        assert fb.db.query_alert_storage_page(1, 2)["alerts"] == []

    def test_page_not_initialized(self, fb_env):
        fb_env.cfg.firebase_database_url = ""
        assert FirebaseDatabase(fb_env.cfg).query_alert_storage_page(1, 5) == {"alerts": [], "total_count": 0}

    def test_delete_paths(self, fb):
        assert fb.db.delete_paths([]) is True
        tree = _tree(fb)
        for i in range(150):
            tree[f"p{i}"] = {"v": i}
        assert fb.db.delete_paths([f"/p{i}" for i in range(149)]) is True
        assert list(tree) == ["p149"]
        updates = [c for c in fb.store.calls if c[0] == "update"]
        assert len(updates) == 2 and len(updates[0][2]) == 100

    def test_delete_paths_errors(self, fb, fb_env):
        fb.store.fail["update"] = RuntimeError("x")
        assert fb.db.delete_paths(["a"]) is False
        fb_env.cfg.firebase_database_url = ""
        assert FirebaseDatabase(fb_env.cfg).delete_paths(["a"]) is False


# ======================================================================= Mongo fakes

def _match(doc, query):
    for field, cond in (query or {}).items():
        val = doc.get(field)
        if isinstance(cond, dict):
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$gte" in cond and not (val is not None and val >= cond["$gte"]):
                return False
            if "$lt" in cond and not (val is not None and val < cond["$lt"]):
                return False
        elif val != cond:
            return False
    return True


class FakeCursor(list):
    def sort(self, spec):
        field, direction = spec[0]
        assert field == "data.timestamp"
        return FakeCursor(sorted(self, key=lambda d: dbm._coerce_alert_timestamp(
            (d.get("data") or {}).get("timestamp") if isinstance(d.get("data"), dict) else None),
            reverse=direction < 0))

    def skip(self, n):
        return FakeCursor(self[n:])

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.docs = {}
        self.indexes = []
        self.fail = {}
        self.calls = []

    def _check(self, op):
        self.calls.append(op)
        if op in self.fail:
            raise self.fail[op]

    def create_index(self, spec, **kw):
        self._check("create_index:" + ("named" if "name" in kw else "plain"))
        self.indexes.append((spec, kw))

    def find_one(self, query):
        self._check("find_one")
        for d in self.docs.values():
            if _match(d, query):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        self._check("find")
        return FakeCursor(copy.deepcopy(d) for d in self.docs.values() if _match(d, query))

    def count_documents(self, query):
        self._check("count")
        return sum(1 for d in self.docs.values() if _match(d, query))

    def replace_one(self, query, doc, upsert=False):
        self._check("replace_one")
        self.docs[query["data_path"]] = copy.deepcopy(doc)

    def delete_one(self, query):
        self._check("delete_one")
        existed = self.docs.pop(query["data_path"], None)
        return types.SimpleNamespace(deleted_count=1 if existed else 0)

    def delete_many(self, query):
        self._check("delete_many")
        for k in [k for k, d in self.docs.items() if _match(d, query)]:
            del self.docs[k]

    def put(self, path, data):
        self.docs[path] = {"data_path": path, "data": data}


class FakeMongoDb(dict):
    def __missing__(self, name):
        self[name] = FakeCollection(name)
        return self[name]


class FakeClient:
    instances = []

    def __init__(self, conn, serverSelectionTimeoutMS=None):
        self.conn = conn
        self.timeout = serverSelectionTimeoutMS
        self.ping_error = FakeClient.next_ping_error
        self.dbs = {}
        self.admin = types.SimpleNamespace(command=self._command)
        FakeClient.instances.append(self)

    next_ping_error = None

    def _command(self, name):
        assert name == "ping"
        if self.ping_error:
            raise self.ping_error
        return {"ok": 1}

    def __getitem__(self, name):
        return self.dbs.setdefault(name, FakeMongoDb())


@pytest.fixture
def mongo_env(monkeypatch):
    FakeClient.instances = []
    FakeClient.next_ping_error = None
    monkeypatch.setattr(dbm, "_mongo_checked", True)
    monkeypatch.setattr(dbm, "MONGODB_AVAILABLE", True)
    monkeypatch.setattr(dbm, "MongoClient", FakeClient)
    return DatabaseConfig(database_type="mongodb", mongodb_connection_string="mongodb://h/",
                          mongodb_database_name="dbn", connection_timeout=7)


@pytest.fixture
def mg(mongo_env):
    db = MongoDatabase(mongo_env)
    assert db.initialize()
    return db


class TestMongoInit:
    def test_success(self, mg):
        client = FakeClient.instances[0]
        assert client.conn == "mongodb://h/" and client.timeout == 7000
        assert mg._collection.name == "mycelian_data"
        assert mg._collection.indexes[0] == ([("data_path", 1)], {"unique": True})
        assert mg._collection.indexes[1][1] == {"name": "idx_data_timestamp"}

    def test_index_error_tolerated(self, mongo_env, monkeypatch):
        orig = FakeCollection.__init__

        def init(self, name):
            orig(self, name)
            self.fail["create_index:named"] = RuntimeError("no perms")

        monkeypatch.setattr(FakeCollection, "__init__", init)
        db = MongoDatabase(mongo_env)
        assert db.initialize() is True

    def test_driver_missing(self, mongo_env, monkeypatch):
        monkeypatch.setattr(dbm, "MONGODB_AVAILABLE", False)
        db = MongoDatabase(mongo_env)
        assert db.initialize() is False and "pymongo" in db.last_error

    def test_ping_fails(self, mongo_env):
        FakeClient.next_ping_error = RuntimeError("timeout")
        db = MongoDatabase(mongo_env)
        assert db.initialize() is False
        assert db.last_error == "Couldn't reach the MongoDB server: timeout"


@pytest.fixture
def mg_down(mongo_env):
    FakeClient.next_ping_error = RuntimeError("down")
    return MongoDatabase(mongo_env)


class TestMongoCrud:
    def test_get(self, mg):
        mg._collection.docs["A"] = {"data_path": "A", "data": {"x": 1}, "etag": "e"}
        mg._collection.docs["N"] = {"data_path": "N"}
        assert mg.get_data("/A") == {"x": 1}
        assert mg.get_data("A", request_etag=True) == {"data": {"x": 1}, "etag": "e"}
        assert mg.get_data("N") == {}
        assert mg.get_data("Z") == {}
        assert mg.get_data("Z", request_etag=True) == {"data": {}, "etag": None}

    def test_get_errors(self, mg, mg_down):
        with pytest.raises(DatabaseReadError, match="not initialized"):
            mg_down.get_data("A")
        mg._collection.fail["find_one"] = RuntimeError("net")
        with pytest.raises(DatabaseReadError, match="net"):
            mg.get_data("A")

    def test_lazy_init(self, mongo_env):
        db = MongoDatabase(mongo_env)
        assert db.set_data("A", {"x": 1}) is True and db._initialized

    def test_set(self, mg):
        assert mg.set_data("/A", {"x": 1}) is True
        doc = mg._collection.docs["A"]
        assert doc["data"] == {"x": 1} and doc["etag"] and doc["updated_at"]

    def test_set_errors(self, mg, mg_down):
        assert mg_down.set_data("A", {}) is False
        mg._collection.fail["replace_one"] = RuntimeError("x")
        assert mg.set_data("A", {}) is False

    def test_update(self, mg):
        assert mg.update_data("A", {"a": 1}) is True
        assert mg.update_data("A", {"b": 2}) is True
        assert mg.get_data("A") == {"a": 1, "b": 2}

    def test_update_lazy_init(self, mongo_env):
        db = MongoDatabase(mongo_env)
        assert db.update_data("A", {"a": 1}) is True and db._initialized

    def test_update_read_error(self, mg):
        mg._collection.fail["find_one"] = RuntimeError("x")
        assert mg.update_data("A", {"a": 1}) is False
        assert "A" not in mg._collection.docs

    def test_update_other_error(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "get_data", lambda p: [1])
        assert mg.update_data("A", {"a": 1}) is False

    def test_delete(self, mg, mg_down):
        mg.set_data("A", {"x": 1})
        assert mg.delete_data("/A") is True
        assert mg.delete_data("A") is True  # nothing to delete still ok
        assert mg_down.delete_data("A") is False
        mg._collection.fail["delete_one"] = RuntimeError("x")
        assert mg.delete_data("A") is False


class TestMongoStatus:
    def test_status(self, mg, mongo_env):
        s = mg.get_connection_status()
        assert s["status"] == "Connected" and s["database_name"] == "dbn"
        mg._client.ping_error = RuntimeError("lost")
        s = mg.get_connection_status()
        assert s["status"] == "Error: lost" and not s["is_connected"]
        s = MongoDatabase(mongo_env).get_connection_status()
        assert s["status"] == "Not Initialized"

    def test_test_connection(self, mg, mongo_env):
        assert mg.test_connection() is True
        mg._client.ping_error = RuntimeError("lost")
        assert mg.test_connection() is False
        fresh = MongoDatabase(mongo_env)
        assert fresh.test_connection() is True


class TestMongoMulti:
    def test_checked(self, mg, monkeypatch):
        monkeypatch.setattr(dbm, "_SQL_IN_CHUNK", 2)
        for p in ("a", "b", "c"):
            mg._collection.put(p, {"p": p})
        mg._collection.docs["n"] = {"data_path": "n", "data": None}
        out, failed = mg._get_multiple_data_checked(["/a", "b", "c", "n", "zz"])
        assert out == {"/a": {"p": "a"}, "b": {"p": "b"}, "c": {"p": "c"}, "n": {}, "zz": {}}
        assert failed == set()
        assert mg._get_multiple_data(["a"]) == {"a": {"p": "a"}}

    def test_empty_and_failures(self, mg, mg_down):
        assert mg._get_multiple_data_checked([]) == ({}, set())
        assert mg_down._get_multiple_data_checked(["a"]) == ({"a": {}}, {"a"})
        mg._collection.fail["find"] = RuntimeError("x")
        assert mg._get_multiple_data_checked(["a", "b"]) == ({"a": {}, "b": {}}, {"a", "b"})

    def test_lazy_init(self, mongo_env):
        db = MongoDatabase(mongo_env)
        db._get_multiple_data_checked(["a"])
        assert db._initialized

    def test_async(self, mg):
        mg._collection.put("a", {"v": 1})

        async def go():
            return (await mg.get_multiple_data_async(["a"]), await mg.get_multiple_data_checked_async(["a"]))

        assert asyncio.run(go()) == ({"a": {"v": 1}}, ({"a": {"v": 1}}, set()))


class TestMongoPathsSnapshot:
    def test_paths(self, mg, mg_down):
        mg._collection.put("b", {})
        mg._collection.put("a/x", {})
        mg._collection.docs["weird"] = {"no_path": True}
        assert mg.get_all_paths() == ["a/x", "b"]
        assert mg_down.get_all_paths() == []
        mg._collection.fail["find"] = RuntimeError("x")
        assert mg.get_all_paths() == []

    def test_snapshot(self, mg, mg_down):
        mg._collection.put("A/B", {"x": 1})
        mg._collection.put("A/C", {"y": 2})
        mg._collection.docs[""] = {"data_path": "", "data": {"skip": 1}}
        mg._collection.docs["D"] = {"data_path": "D"}
        assert mg.get_snapshot() == {"A": {"B": {"x": 1}, "C": {"y": 2}}, "D": {}}
        assert mg_down.get_snapshot() == {}
        mg._collection.fail["find"] = RuntimeError("big")
        assert mg.get_snapshot() == {} and mg.snapshot_error == "big"

    def test_collection_map(self, mg):
        assert mg._collection_map(None) == {}
        assert mg._collection_map({"data": [1]}) == {}
        assert mg._collection_map({"data": {"a": {"t": 1}, "b": 2, 3: {"x": 1}}}) == {"a": {"t": 1}, "3": {"x": 1}}


class TestMongoAlerts:
    @pytest.fixture
    def children(self, mg):
        for i in range(4):
            mg._collection.put(f"Alerts/AlertStorage/c{i}", {"timestamp": i * 10, "n": i})
        mg._collection.docs["Alerts/AlertStorage/bad"] = {"data_path": "Alerts/AlertStorage/bad", "data": "x"}
        mg._collection.put("Alerts/Other", {"timestamp": 999})
        return mg

    def test_fetch_children(self, children):
        assert set(children.fetch_alert_storage()) == {"c0", "c1", "c2", "c3"}
        assert set(children.fetch_alert_storage(max_alerts=2)) == {"c3", "c2"}

    def test_fetch_blob_merges_children(self, children):
        children._collection.put("Alerts/AlertStorage", {"b1": {"timestamp": 100}, "b2": {"timestamp": 5}, "meta": 1})
        out = children.fetch_alert_storage()
        assert set(out) == {"b1", "b2", "c0", "c1", "c2", "c3"}
        limited = children.fetch_alert_storage(max_alerts=2)
        assert limited == {"b1": {"timestamp": 100}, "c3": {"timestamp": 30, "n": 3}}
        assert len(children.fetch_alert_storage(max_alerts=50)) == 6

    def test_meta(self, children):
        children._collection.put("Alerts/AlertStorage", {"b1": {"timestamp": 15}})
        assert children.list_alert_storage_meta() == [
            ("c3", 30.0), ("c2", 20.0), ("b1", 15.0), ("c1", 10.0), ("c0", 0.0), ("bad", 0.0),
        ]

    def test_page_children(self, children):
        p = children.query_alert_storage_page(1, 2)
        assert p["total_count"] == 5
        assert [a["alert_id"] for a in p["alerts"]] == ["c3", "c2"]
        p = children.query_alert_storage_page(3, 2)
        assert p["alerts"][0]["alert_id"] in ("c0", "bad")
        p = children.query_alert_storage_page(0, 0)
        assert len(p["alerts"]) == 1

    def test_page_bad_data_is_empty(self, mg):
        mg._collection.docs["Alerts/AlertStorage/x"] = {"data_path": "Alerts/AlertStorage/x", "data": 5}
        assert mg.query_alert_storage_page(1, 5)["alerts"] == [{"alert_id": "x"}]

    def test_page_blob(self, children):
        children._collection.put("Alerts/AlertStorage", {"b1": {"timestamp": 100}})
        p = children.query_alert_storage_page(1, 2)
        assert p["total_count"] == 5
        assert [a["alert_id"] for a in p["alerts"]] == ["b1", "c3"]

    def test_not_initialized(self, mg_down):
        assert mg_down.fetch_alert_storage() == {}
        assert mg_down.list_alert_storage_meta() == []
        assert mg_down.query_alert_storage_page(1, 1) == {"alerts": [], "total_count": 0}
        assert mg_down.delete_paths(["a"]) is False

    def test_delete_paths(self, mg, monkeypatch):
        monkeypatch.setattr(dbm, "_SQL_IN_CHUNK", 2)
        assert mg.delete_paths([]) is True
        for p in "abcd":
            mg._collection.put(p, {})
        assert mg.delete_paths(["/a", "b", "c"]) is True
        assert list(mg._collection.docs) == ["d"]
        assert mg._collection.calls.count("delete_many") == 2
        mg._collection.fail["delete_many"] = RuntimeError("x")
        assert mg.delete_paths(["d"]) is False

    def test_iter_children(self, children):
        docs = list(children._iter_alert_child_docs(max_alerts=1))
        assert [d["data_path"] for d in docs] == ["Alerts/AlertStorage/c3"]
        assert len(list(children._iter_alert_child_docs())) == 5

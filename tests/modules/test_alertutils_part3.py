# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alertutils.py, part 3: stored-alert ordering and pagination, display names,
the individual-record migration and the module-level trim wrappers."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _alertutils_fakes as fakes  # noqa: E402
from modules import alertutils as au  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    db, mgr = fakes.install(monkeypatch)
    yield db, mgr
    fakes.restore_collections()


@pytest.fixture
def uninit(env, monkeypatch):
    _, mgr = env
    mgr._initialized = False
    calls = []

    def fake_init():
        calls.append(1)
        mgr._initialized = True

    monkeypatch.setattr(mgr, "initialize", fake_init)
    return calls


def _fill(mgr, n=5, loaded=True):
    mgr._alert_storage = {f"a{i}": {"timestamp": 100 + i, "username": f"u{i}"} for i in range(n)}
    mgr._alert_storage_loaded = loaded
    mgr._note_storage_changed()


# ======================================================================= ordering

def test_delete_alert_initializes(env, uninit):
    _, mgr = env
    assert mgr.delete_alert("follows", "f1") is True
    assert uninit == [1]


def test_ordered_ids_cached_until_change(env):
    _, mgr = env
    _fill(mgr, 3)
    mgr._alert_storage["bad"] = "not a dict"
    mgr._note_storage_changed()
    first = mgr._ordered_storage_ids_locked()
    assert first == ["a2", "a1", "a0", "bad"]
    assert mgr._ordered_storage_ids_locked() is first
    mgr._alert_storage["a9"] = {"timestamp": 999}
    mgr._note_storage_changed()
    assert mgr._ordered_storage_ids_locked()[0] == "a9"


@pytest.mark.parametrize("page,limit,expected", [
    (2, 10, (2, 10)), ("3", "5", (3, 5)), (0, 0, (1, 1)), (-2, 5000, (1, 1000)),
    ("x", None, (1, 25)), (None, "y", (1, 25)),
])
def test_coerce_page_args(page, limit, expected):
    assert au.AlertStateManager._coerce_page_args(page, limit) == expected


@pytest.mark.parametrize("page,total,limit,expected", [
    (1, 0, 10, 1), (5, 0, 10, 1), (3, 25, 10, 3), (9, 25, 10, 3), (0, 25, 10, 1),
])
def test_clamp_page(page, total, limit, expected):
    assert au.AlertStateManager._clamp_page(page, total, limit) == expected


def test_page_result_shape(env):
    _, mgr = env
    assert mgr._stored_alerts_page_result([], 0, 1, 25) == {
        "alerts": [], "total_count": 0, "page": 1, "limit": 25, "total_pages": 1,
        "has_next": False, "has_prev": False}
    r = mgr._stored_alerts_page_result([1], 30, 2, 10)
    assert r["total_pages"] == 3 and r["has_next"] and r["has_prev"]


def test_page_from_ordered_ids_handles_non_dict(env):
    _, mgr = env
    mgr._alert_storage = {"x": "junk", "y": {"timestamp": 1}}
    r = mgr._page_from_ordered_ids_locked(["x", "y"], 1, 10)
    assert r["alerts"] == [{"alert_id": "x"}, {"timestamp": 1, "alert_id": "y"}]


# ======================================================================= since / pages

def test_get_stored_alerts_since(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {f"a{i}": {"timestamp": 100 + i} for i in range(5)}
    db.database_manager.storage_result["junk"] = "x"
    out = mgr.get_stored_alerts_since(102)
    assert uninit == [1]
    assert [a["alert_id"] for a in out] == ["a4", "a3", "a2"]
    out[0]["timestamp"] = 0
    assert mgr._alert_storage["a4"]["timestamp"] == 104
    assert [a["alert_id"] for a in mgr.get_stored_alerts_since(0, limit=2)] == ["a4", "a3"]
    assert len(mgr.get_stored_alerts_since("bad")) == 5


def test_page_stored_alerts_memory_dicts(env):
    _, mgr = env
    storage = {f"a{i}": {"timestamp": i} for i in range(5)}
    r = mgr._page_stored_alerts_memory(storage, 2, 2)
    assert [a["alert_id"] for a in r["alerts"]] == ["a2", "a1"]
    assert r["total_count"] == 5 and r["total_pages"] == 3
    r = mgr._page_stored_alerts_memory(storage, 99, 2)
    assert r["page"] == 3 and [a["alert_id"] for a in r["alerts"]] == ["a0"]


def test_page_stored_alerts_memory_dict_subclass(env):
    _, mgr = env

    class Rec(dict):
        pass

    storage = {"a": {"timestamp": 1}, "b": Rec(timestamp=5)}  # dict subclasses use the fast path
    r = mgr._page_stored_alerts_memory(storage, 1, 10)
    assert [a["alert_id"] for a in r["alerts"]] == ["b", "a"]


def test_page_stored_alerts_memory_slow_path(env):
    _, mgr = env

    class NotDict:
        def __init__(self, ts):
            self.ts = ts

        def __deepcopy__(self, memo):
            return {"timestamp": self.ts}

    storage = {"x": NotDict(5), "y": {"timestamp": 9}, "z": NotDict(1)}
    r = mgr._page_stored_alerts_memory(storage, 1, 2)
    assert [a["alert_id"] for a in r["alerts"]] == ["y", "x"]
    assert r["total_count"] == 3 and r["has_next"]


def test_get_stored_alerts(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {"a": {"timestamp": 1}}
    out = mgr.get_stored_alerts()
    assert out == {"a": {"timestamp": 1}} and uninit == [1]
    out["a"]["timestamp"] = 2
    assert mgr.get_stored_alerts() == {"a": {"timestamp": 1}}  # loaded path returns a copy
    assert db.database_manager.storage_calls == [None]


def test_get_stored_alerts_paginated(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {f"a{i}": {"timestamp": i} for i in range(30)}
    r = mgr.get_stored_alerts_paginated(page=2, limit=10)
    assert uninit == [1]
    assert r["page"] == 2 and r["total_count"] == 30 and r["total_pages"] == 3
    assert r["alerts"][0]["alert_id"] == "a19"
    r = mgr.get_stored_alerts_paginated(page="bad", limit="bad")
    assert r["page"] == 1 and r["limit"] == 25


def test_get_stored_alerts_paginated_error(env, monkeypatch):
    _, mgr = env
    _fill(mgr)
    monkeypatch.setattr(mgr, "_ordered_storage_ids_locked", lambda: (_ for _ in ()).throw(RuntimeError()))
    r = mgr.get_stored_alerts_paginated(page=3, limit=7)
    assert r == {"alerts": [], "total_count": 0, "page": 3, "limit": 7, "total_pages": 1,
                 "has_next": False, "has_prev": False}


def test_get_recent_stored_alerts(env, uninit, monkeypatch):
    _, mgr = env
    _fill(mgr, 4)
    assert [a["alert_id"] for a in mgr.get_recent_stored_alerts(limit=2)] == ["a3", "a2"]
    monkeypatch.setattr(mgr, "get_stored_alerts_paginated", lambda **kw: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.get_recent_stored_alerts() == []


def test_get_stored_alert_by_id(env, uninit):
    db, mgr = env
    mgr._alert_storage = {"mem": {"username": "m"}}
    got = mgr.get_stored_alert_by_id("mem")
    assert got == {"username": "m"} and got is not mgr._alert_storage["mem"]
    assert uninit == [1]
    db.store["Alerts/AlertStorage/db1"] = {"username": "d"}
    assert mgr.get_stored_alert_by_id("db1") == {"username": "d"}
    assert mgr._alert_storage["db1"] == {"username": "d"}  # cached while storage is partial
    assert mgr.get_stored_alert_by_id("missing") is None
    db.store["Alerts/AlertStorage/empty"] = {}
    assert mgr.get_stored_alert_by_id("empty") is None


def test_get_stored_alert_by_id_loaded_not_cached_and_error(env):
    db, mgr = env
    mgr._alert_storage_loaded = True
    db.store["Alerts/AlertStorage/x"] = {"a": 1}
    assert mgr.get_stored_alert_by_id("x") == {"a": 1}
    assert "x" not in mgr._alert_storage
    db.read_error.add("Alerts/AlertStorage/y")
    assert mgr.get_stored_alert_by_id("y") is None


def test_limit_stored_alerts_dict(env):
    _, mgr = env
    assert mgr._limit_stored_alerts_dict({}, 5) == {}
    d = {"a": {"timestamp": 1}, "b": {"timestamp": 3}, "c": {}}
    assert list(mgr._limit_stored_alerts_dict(d, 2)) == ["b", "a"]


def test_get_limited_stored_alerts_from_firebase(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {"a": {"timestamp": 1}, "b": {"timestamp": 2}}
    assert list(mgr.get_limited_stored_alerts_from_firebase(1)) == ["b"]
    assert uninit == [1]


def test_get_limited_stored_alerts_empty_and_error(env, monkeypatch):
    _, mgr = env
    mgr._alert_storage_loaded = True
    assert mgr.get_limited_stored_alerts_from_firebase(5) == {}
    mgr._alert_storage = {"a": {}}
    monkeypatch.setattr(mgr, "_limit_stored_alerts_dict", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.get_limited_stored_alerts_from_firebase(5) == {}


def test_get_limited_stored_alerts_paginated(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {f"a{i}": {"timestamp": i} for i in range(20)}
    r = mgr.get_limited_stored_alerts_paginated(page=2, limit=5, max_total_alerts=12)
    assert uninit == [1]
    assert r["total_count"] == 12 and r["total_pages"] == 3
    assert [a["alert_id"] for a in r["alerts"]] == ["a14", "a13", "a12", "a11", "a10"]
    r = mgr.get_limited_stored_alerts_paginated(max_total_alerts="bad")
    assert r["total_count"] == 20


def test_get_limited_stored_alerts_paginated_error(env, monkeypatch):
    _, mgr = env
    _fill(mgr)
    monkeypatch.setattr(mgr, "_ordered_storage_ids_locked", lambda: (_ for _ in ()).throw(RuntimeError()))
    r = mgr.get_limited_stored_alerts_paginated(page=2, limit=4)
    assert r["alerts"] == [] and r["page"] == 2 and r["limit"] == 4


# ======================================================================= display names

@pytest.mark.parametrize("alert_type,alert_id,data,expected", [
    ("points", "rw1", {"title": "Hydrate"}, "Hydrate"),
    ("points", "rw1", None, "Point Reward rw1"),
    ("points", "rw1", {"title": ""}, "Point Reward rw1"),
    ("follows", "follow1", None, "Follow"),
    ("subs", "subs_fallback", None, "Resub Fallback"),
    ("subs", "subs2-6", None, "2-6 Months"),
    ("bits", "bits100-500", None, "100-500 Bits"),
    ("giftsubs", "giftsubs5-10", None, "5-10 Giftsubs"),
    ("donations", "donations5-10", None, "$5-$10 Donation"),
    ("raids", "raids1-9", None, "1-9 Raiders"),
    ("streaks", "streaks3-5", None, "3-5 Streak"),
    ("subs", "subsa-b", None, "Subs a-b"),
    ("subs", "subs1", None, "1 Month"),
    ("subs", "12", None, "12 Months"),
    ("bits", "bits100", None, "100 Bits"),
    ("giftsubs", "giftsubs5", None, "5 Giftsubs"),
    ("donations", "donations5", None, "$5 Donation"),
    ("raids", "raids10", None, "10 Raiders"),
    ("streaks", "streaks1", None, "1 Streak"),
    ("streaks", "streaks4", None, "4 Streaks"),
    ("bits", "bitsX", {"alert_name": "Custom"}, "Custom"),
    ("bits", "bitsX", None, "Bits bitsX"),
    ("other", "7", None, "Other 7"),
])
def test_get_display_name(env, alert_type, alert_id, data, expected):
    _, mgr = env
    assert mgr.get_display_name(alert_type, alert_id, data) == expected


def test_get_display_name_range_of_unknown_type_falls_through(env):
    _, mgr = env
    # Unknown types with a parseable range return nothing from the range branch; the int()
    # parse then fails and the generic fallback is used.
    assert mgr.get_display_name("other", "1-2") == "Other 1-2"


def test_get_display_name_error(env):
    _, mgr = env
    assert mgr.get_display_name("bits", None) == "Bits None"


# ======================================================================= migration

def _sql_backend(db, rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_data (data_path TEXT, data_json TEXT)")
    conn.executemany("INSERT INTO app_data VALUES (?, ?)", rows)
    db.database_manager._database = SimpleNamespace(_connection=conn)
    return conn


def test_migrate_without_sql_backend(env):
    db, mgr = env
    mgr._alert_state["bits"] = {"bits1": {"alert_name": "one"}}
    assert mgr.migrate_individual_alerts_to_collections() is True
    assert db.writes == []
    assert au.BitAlerts["bits1"].alert_name == "one"


def test_migrate_consolidates_individual_records(env):
    db, mgr = env
    conn = _sql_backend(db, [
        ("Alerts/BitAlerts", json.dumps({"bits1": {}})),
        ("Alerts/BitAlerts/bits100", json.dumps({"alert_name": "hundred"})),
        ("Alerts/BitAlerts/bad", "{not json"),
        ("Alerts/SubAlerts/subs3", json.dumps({"alert_name": "three"})),
    ])
    db.store["Alerts/BitAlerts"] = {"bits1": {"alert_name": "one"}}
    db.write_fail.add("Alerts/SubAlerts")
    try:
        assert mgr.migrate_individual_alerts_to_collections() is True
    finally:
        conn.close()
    assert db.store["Alerts/BitAlerts"] == {"bits1": {"alert_name": "one"}, "bits100": {"alert_name": "hundred"}}
    assert mgr._alert_state["bits"]["bits100"] == {"alert_name": "hundred"}
    assert db.deletes == ["Alerts/BitAlerts/bits100"]
    assert mgr._alert_state["subs"] == {}  # save failed: state untouched
    assert au.BitAlerts["bits100"].alert_name == "hundred"


def test_migrate_query_error_is_logged(env):
    db, mgr = env

    class BadConn:
        def cursor(self):
            raise sqlite3.OperationalError("locked")

    db.database_manager._database = SimpleNamespace(_connection=BadConn())
    assert mgr.migrate_individual_alerts_to_collections() is True
    assert db.writes == []


def test_migrate_skips_unknown_paths_and_handles_error(env, monkeypatch):
    db, mgr = env
    del mgr._alert_paths["points"]
    assert mgr.migrate_individual_alerts_to_collections() is True
    monkeypatch.setattr(mgr, "_update_global_collections", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.migrate_individual_alerts_to_collections() is False


# ======================================================================= module wrappers

def test_module_trim_wrappers(env, monkeypatch):
    _, mgr = env
    calls = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: calls.append("schedule"))
    monkeypatch.setattr(mgr, "cancel_alert_storage_trim", lambda: calls.append("cancel"))
    mgr._set_trim_progress(active=True, phase="calculating")
    assert au.get_alert_storage_trim_progress()["phase"] == "calculating"
    au.schedule_auto_trim_stored_alerts()
    au.cancel_alert_storage_trim()
    assert calls == ["schedule", "cancel"]

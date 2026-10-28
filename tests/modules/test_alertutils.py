# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alertutils.py, part 1: helpers, config read tracking, the ordered config
writer, initialization and resub fallback settings.

The database is an in-memory fake (see _alertutils_fakes.py) and manager threads run inline.
"""

from __future__ import annotations

import asyncio
import sys
import threading
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


# ======================================================================= module helpers

def test_new_alert_id_unique_and_counter(monkeypatch):
    monkeypatch.setattr(au, "_ALERT_ID_LAST_MS", 0)
    monkeypatch.setattr(au, "_ALERT_ID_COUNTER", 0)
    monkeypatch.setattr(au.time, "time", lambda: 1.0)
    assert au.new_alert_id() == "Alert1000000"
    assert au.new_alert_id("X") == "X1000001"


def test_new_alert_id_counter_rollover(monkeypatch):
    monkeypatch.setattr(au, "_ALERT_ID_LAST_MS", 1000)
    monkeypatch.setattr(au, "_ALERT_ID_COUNTER", 999)
    monkeypatch.setattr(au.time, "time", lambda: 1.0)
    assert au.new_alert_id("R") == "R1001000"


def test_new_alert_id_clock_moves_forward(monkeypatch):
    monkeypatch.setattr(au, "_ALERT_ID_LAST_MS", 500)
    monkeypatch.setattr(au, "_ALERT_ID_COUNTER", 7)
    monkeypatch.setattr(au.time, "time", lambda: 2.0)
    assert au.new_alert_id("A") == "A2000000"


@pytest.mark.parametrize("value,expected", [
    (None, 0.0), ("", 0.0), ("12.5", 12.5), (3, 3.0), ("bad", 0.0), ([], 0.0),
])
def test_safe_alert_timestamp(value, expected):
    assert au.safe_alert_timestamp(value) == expected


def test_safe_alert_timestamp_now_and_default(monkeypatch):
    monkeypatch.setattr(au.time, "time", lambda: 99.0)
    assert au.safe_alert_timestamp("now") == 99.0
    assert au.safe_alert_timestamp(None, default=5.0) == 5.0
    assert au.safe_alert_timestamp("x", default=7.0) == 7.0


@pytest.mark.parametrize("progress,expected", [
    (None, None),
    ({}, None),
    ({"active": False, "phase": "deleting"}, None),
    ({"active": True, "phase": "calculating"}, ("Calculating", "info")),
    ({"active": True, "phase": "deleting", "to_delete": 5, "deleted": 0}, ("5 to delete", "warning")),
    ({"active": True, "phase": "deleting", "to_delete": 5, "deleted": 2}, ("2 of 5", "warning")),
    ({"active": True, "phase": "deleting", "to_delete": "x", "deleted": "y"}, ("0 to delete", "warning")),
    ({"active": True, "phase": "deleting", "to_delete": -3, "deleted": None}, ("0 to delete", "warning")),
    ({"active": True, "phase": "idle"}, None),
    ({"active": True}, None),
])
def test_format_trim_progress_badge(progress, expected):
    assert au.format_trim_progress_badge(progress) == expected


def test_alert_settings_defaults():
    s = au.AlertSettings()
    assert s.global_delay == 3
    assert "hype_train_end" in au.AlertSettings.alert_types
    assert au.AlertSettings.FALLBACK_ALERT_ID == "subs_fallback"
    assert au.AlertSettings.default_streak_alert == "streaks1"


@pytest.mark.parametrize("tab,expected", [("bits", "bit"), ("follows", "follow"), ("points", "point"),
                                          ("donations", "donation"), ("other", "other")])
def test_ui_tab_to_runtime_alert_type(tab, expected):
    assert au.ui_tab_to_runtime_alert_type(tab) == expected


def test_notify_history_store_failed(monkeypatch):
    from modules import notification_engine

    seen = []
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: seen.append((a, k)))
    au._notify_history_store_failed("A1", "boom")
    au._notify_history_store_failed("A2")
    assert seen[0][1]["details"] == "alert_id=A1\nboom"
    assert seen[1][1]["details"] == "alert_id=A2"
    assert seen[0][1]["dedupe_key"] == "activity:store_failed"


def test_notify_history_store_failed_swallows(monkeypatch):
    from modules import notification_engine

    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    au._notify_history_store_failed("A1")


# ======================================================================= construction

def test_init_registers_write_listener(env):
    db, mgr = env
    assert db.database_manager.listeners == [mgr._on_database_write]
    assert mgr._alert_paths["alert_storage"] == "Alerts/AlertStorage"
    assert set(mgr._alert_state) == set(mgr._config_state_keys())
    assert mgr.get_trim_progress() == {"active": False, "phase": "idle", "to_delete": 0, "deleted": 0}


def test_init_tolerates_listener_failure(monkeypatch):
    db = fakes.FakeDB()

    def boom(cb):
        raise RuntimeError("no hooks")

    db.database_manager.add_write_listener = boom
    monkeypatch.setattr(au, "database_manager", db)
    mgr = au.AlertStateManager()
    assert mgr._initialized is False


# ======================================================================= config read tracking

def test_config_keys_for_path(env):
    _, mgr = env
    assert mgr._config_keys_for_path("") == mgr._config_state_keys()
    assert mgr._config_keys_for_path("Settings/x") == []
    assert mgr._config_keys_for_path("AlertsX") == []
    assert set(mgr._config_keys_for_path("Alerts")) == set(mgr._config_state_keys())
    assert mgr._config_keys_for_path("Alerts/BitAlerts") == ["bits"]
    assert mgr._config_keys_for_path("Alerts/BitAlerts/bits100") == ["bits"]
    assert mgr._config_keys_for_path("Alerts/AlertStorage/x") == []


def test_on_database_write_external_drops_loaded_keys(env):
    _, mgr = env
    mgr._loaded_state_keys = {"bits", "subs"}
    mgr._on_database_write("Other/path", False)
    assert mgr._config_write_epoch == 0
    mgr._on_database_write("Alerts/BitAlerts", False)
    assert mgr._config_writes_inflight == 1 and mgr._config_write_epoch == 1
    assert mgr._loaded_state_keys == {"subs"}
    assert mgr._config_read_token() is None
    mgr._on_database_write("Alerts/BitAlerts", True)
    mgr._on_database_write("Alerts/BitAlerts", True)  # never below zero
    assert mgr._config_writes_inflight == 0
    assert mgr._config_read_token() == 3


def test_on_database_write_own_write_keeps_loaded(env):
    _, mgr = env
    mgr._loaded_state_keys = {"bits"}
    mgr._own_write_paths["Alerts/BitAlerts"] = 1
    mgr._on_database_write("Alerts/BitAlerts", False)
    assert mgr._loaded_state_keys == {"bits"}


def test_note_config_read(env):
    _, mgr = env
    mgr._note_config_read("bits", False, 0)
    assert "bits" in mgr._unreadable_state_keys and "bits" not in mgr._loaded_state_keys
    mgr._note_config_read("bits", True, None)  # no token: readable, but not marked loaded
    assert "bits" not in mgr._unreadable_state_keys and "bits" not in mgr._loaded_state_keys
    mgr._note_config_read("bits", True, 5)  # stale token
    assert "bits" not in mgr._loaded_state_keys
    mgr._note_config_read("bits", True, mgr._config_write_epoch)
    assert "bits" in mgr._loaded_state_keys
    mgr._config_writes_inflight = 1
    mgr._note_config_read("subs", True, mgr._config_write_epoch)
    assert "subs" not in mgr._loaded_state_keys


def test_read_config_paths_serial_and_parallel(env):
    db, mgr = env
    db.store["Alerts/BitAlerts"] = {"bits1": {"duration": 1}}
    db.read_fail.add("Alerts/SubAlerts")
    out = mgr._read_config_paths(["bits", "subs"])
    assert out["bits"] == ({"bits1": {"duration": 1}}, True, 0)
    assert out["subs"] == ({}, False, 0)
    db.database_manager.database_type = "firebase"
    out = mgr._read_config_paths(["bits", "subs", "follows"], parallel=True)
    assert out["bits"][1] is True and out["follows"] == ({}, True, 0)


def test_lazy_load_config_keys(env):
    db, mgr = env
    db.store["Alerts/BitRangeAlerts"] = {"bits1-10": {"duration": 2}}
    mgr._alert_state["bits"] = {"bits5": {"duration": 1}}
    assert mgr._lazy_load_config_keys(["bits", "bit_ranges", "alert_storage", "nope"]) is True
    assert mgr._alert_state["bit_ranges"] == {"bits1-10": {"duration": 2}}
    assert "bits1-10" in au.BitRangeAlerts
    # already loaded -> nothing to do
    assert mgr._lazy_load_config_keys(["bit_ranges"]) is False
    # empty and already read -> not re-read
    assert mgr._lazy_load_config_keys(["sub_ranges"]) is False  # read, still empty
    assert "sub_ranges" in mgr._loaded_state_keys
    assert mgr._lazy_load_config_keys(["sub_ranges"]) is False


def test_lazy_load_skips_filled_meanwhile(env, monkeypatch):
    db, mgr = env
    db.store["Alerts/SubAlerts"] = {"subs1": {}}

    real = mgr._read_config_paths

    def read_and_fill(keys, **kw):
        out = real(keys, **kw)
        mgr._alert_state["subs"] = {"subs9": {}}
        return out

    monkeypatch.setattr(mgr, "_read_config_paths", read_and_fill)
    assert mgr._lazy_load_config_keys(["subs"]) is False
    assert mgr._alert_state["subs"] == {"subs9": {}}


def test_load_missing_alert_paths_initializes_first(env, monkeypatch):
    _, mgr = env
    mgr._initialized = False
    calls = []
    monkeypatch.setattr(mgr, "initialize", lambda: calls.append(1))
    mgr.load_missing_alert_paths()
    assert calls == [1]


def test_load_missing_alert_paths(env):
    db, mgr = env
    db.store["Alerts/RaidRangeAlerts"] = {"raids1-5": {"duration": 3}}
    db.read_fail.add("Alerts/SubAlerts")
    mgr._alert_state["subs"] = {"subs1": {}}  # keep on failed read
    mgr._loaded_state_keys = {"bits"}
    mgr.load_missing_alert_paths()
    assert mgr._alert_state["raid_ranges"] == {"raids1-5": {"duration": 3}}
    assert mgr._alert_state["subs"] == {"subs1": {}}
    assert "subs" not in mgr._unreadable_state_keys  # failed read with data in memory isn't noted
    assert "raid_ranges" in mgr._loaded_state_keys
    assert "raids1-5" in au.RaidRangeAlerts


def test_load_missing_alert_paths_failed_read_empty_is_unreadable(env):
    db, mgr = env
    db.read_fail.add("Alerts/FollowAlerts")
    mgr.load_missing_alert_paths()
    assert "follows" in mgr._unreadable_state_keys


def test_load_missing_alert_paths_nothing_missing(env, monkeypatch):
    _, mgr = env
    mgr._loaded_state_keys = set(mgr._config_state_keys())
    monkeypatch.setattr(mgr, "_read_config_paths", lambda *a, **k: pytest.fail("should not read"))
    mgr.load_missing_alert_paths()


def test_recover_unreadable_collection(env):
    db, mgr = env
    assert mgr._recover_unreadable_collection("bits") is True  # readable already
    mgr._unreadable_state_keys.add("bits")
    db.read_fail.add("Alerts/BitAlerts")
    assert mgr._recover_unreadable_collection("bits") is False
    db.read_fail.clear()
    db.store["Alerts/BitAlerts"] = {"bits1": {"a": 1}, "bits2": {"a": 2}}
    mgr._alert_state["bits"] = {"bits2": {"a": 22}}
    assert mgr._recover_unreadable_collection("bits") is True
    assert mgr._alert_state["bits"] == {"bits1": {"a": 1}, "bits2": {"a": 22}}
    assert "bits" not in mgr._unreadable_state_keys


def test_recover_unreadable_non_dict_data(env):
    db, mgr = env
    mgr._unreadable_state_keys.add("subs")
    db.store["Alerts/SubAlerts"] = ["weird"]
    mgr._alert_state["subs"] = {"subs1": {}}
    assert mgr._recover_unreadable_collection("subs") is True
    assert mgr._alert_state["subs"] == {"subs1": {}}


# ======================================================================= ordered config writer

def test_queue_and_wait_writes(env):
    db, mgr = env
    with mgr._lock:
        waiter = mgr._queue_config_writes([("set", "Alerts/BitAlerts", {"a": 1}),
                                           ("delete", "Alerts/BitAlerts/x", None)], "bits")
    assert mgr._wait_config_writes(waiter) is None
    assert db.store["Alerts/BitAlerts"] == {"a": 1}
    assert db.deletes == ["Alerts/BitAlerts/x"]
    assert db.flushes == ["alert_config_writes"]
    assert mgr._own_write_paths == {}
    assert mgr._pending_writes == 0


def test_writer_quiet_mode(env):
    db, mgr = env
    waiter = mgr._queue_config_writes([("set", "Alerts/SubAlerts", {})], "subs", quiet=True)
    mgr._wait_config_writes(waiter)
    assert db.quiet_used == 1
    waiter = mgr._queue_config_writes([("set", "Alerts/SubAlerts", {})], "subs")
    mgr._wait_config_writes(waiter)
    assert db.quiet_used == 1


def test_writer_quiet_without_helper(env):
    db, mgr = env
    db.quiet_notifications = None
    waiter = mgr._queue_config_writes([("set", "Alerts/SubAlerts", {})], "subs", quiet=True)
    assert mgr._wait_config_writes(waiter) is None


def test_writer_failed_write_reports_reason(env):
    db, mgr = env
    db.write_fail.add("Alerts/BitAlerts")
    db.problem = ("sql", "disk full", None)
    mgr._loaded_state_keys.add("bits")
    waiter = mgr._queue_config_writes([("set", "Alerts/BitAlerts", {}), ("set", "Alerts/BitAlerts", {})], "bits")
    err = mgr._wait_config_writes(waiter)
    assert isinstance(err, au._ConfigWriteFailed)
    assert str(err) == "Database write failed: Alerts/BitAlerts (sql: disk full)"
    assert "bits" not in mgr._loaded_state_keys


@pytest.mark.parametrize("problem,error,expected", [
    (None, False, "Alerts/BitAlerts"),
    ((), False, "Alerts/BitAlerts"),
    (None, True, "Alerts/BitAlerts"),
])
def test_writer_failed_write_without_reason(env, problem, error, expected):
    db, mgr = env
    db.write_fail.add("Alerts/BitAlerts")
    db.problem = problem
    db.problem_error = error
    waiter = mgr._queue_config_writes([("set", "Alerts/BitAlerts", {})], "bits")
    mgr._wait_config_writes(waiter)
    assert waiter["failed"] == expected


def test_writer_exception_stops_remaining_ops(env):
    db, mgr = env
    db.write_error.add("Alerts/BitAlerts")
    mgr._own_write_paths["Alerts/BitAlerts"] = 1  # another in-flight write of the same path
    waiter = mgr._queue_config_writes([("set", "Alerts/BitAlerts", {}), ("set", "Alerts/SubAlerts", {})], "bits")
    err = mgr._wait_config_writes(waiter)
    assert isinstance(err, RuntimeError)
    assert "Alerts/SubAlerts" not in db.store
    assert mgr._own_write_paths == {"Alerts/BitAlerts": 1}


def test_writer_loop_outer_exception(env):
    _, mgr = env
    waiter = mgr._queue_config_writes([("bad-op",)], "bits")  # unpacking fails inside the try
    err = mgr._wait_config_writes(waiter)
    assert isinstance(err, ValueError)
    assert waiter["done"].is_set()


def test_queue_writes_register_failure_is_ignored(env, monkeypatch):
    from modules import shutdown

    _, mgr = env
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    waiter = mgr._queue_config_writes([("set", "Alerts/SubAlerts", {})], "subs")
    assert mgr._wait_config_writes(waiter) is None


def test_queue_writes_reuses_live_thread(env, monkeypatch):
    _, mgr = env
    started = []

    class Alive:
        def is_alive(self):
            return True

    mgr._writer_thread = Alive()
    monkeypatch.setattr(au.threading, "Thread", lambda **k: started.append(k))
    waiter = mgr._queue_config_writes([("set", "x", {})], "subs")
    assert started == [] and mgr._pending_writes == 1 and not waiter["done"].is_set()
    mgr._write_queue.get_nowait()
    mgr._pending_writes = 0


def test_real_writer_thread_runs(monkeypatch):
    """One run with a real thread: the writer is a daemon that waits for more work."""
    db = fakes.FakeDB()
    monkeypatch.setattr(au, "database_manager", db)
    from modules import shutdown

    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda *a: None)
    mgr = au.AlertStateManager()
    waiter = mgr._queue_config_writes([("set", "Alerts/FollowAlerts", {"f": 1})], "follows")
    assert mgr._wait_config_writes(waiter) is None
    assert mgr.flush_config_writes(timeout=1.0) is True
    assert db.store["Alerts/FollowAlerts"] == {"f": 1}
    thread = mgr._writer_thread
    assert thread.daemon is True and thread.is_alive()
    # Stop the endless writer loop: unpacking the None sentinel raises and ends the thread.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    mgr._write_queue.put(None)
    thread.join(2)
    assert not thread.is_alive()


def test_wait_event_plain_and_hub(env, monkeypatch):
    db, mgr = env
    ev = threading.Event()
    ev.set()
    au.AlertStateManager._wait_event(ev)
    db.hub = True
    ev2 = threading.Event()
    ticks = []

    def fake_sleep(s):
        ticks.append(s)
        ev2.set()

    import gevent

    monkeypatch.setattr(gevent, "sleep", fake_sleep)
    au.AlertStateManager._wait_event(ev2)
    assert ticks == [0.01]


def test_wait_event_hub_without_gevent(env, monkeypatch):
    db, mgr = env
    db.hub = True
    monkeypatch.setitem(sys.modules, "gevent", None)  # import fails
    ev = threading.Event()
    monkeypatch.setattr(au.time, "sleep", lambda s: ev.set())
    au.AlertStateManager._wait_event(ev)
    assert ev.is_set()


def test_flush_config_writes(env, monkeypatch):
    _, mgr = env
    assert mgr.flush_config_writes() is True
    mgr._pending_writes = 2
    assert mgr.flush_config_writes(timeout=0) is False
    now = [0.0]
    monkeypatch.setattr(au.time, "monotonic", lambda: now[0])

    def wait(remaining):
        mgr._pending_writes = 0

    monkeypatch.setattr(mgr, "_writes_idle", SimpleNamespace(wait=wait))
    assert mgr.flush_config_writes(timeout=1) is True


def test_is_sql_and_firebase_backend(env):
    db, mgr = env
    assert mgr._is_sql_backend() is True and mgr._is_firebase_backend() is False
    db.database_manager.database_type = "firebase"
    assert mgr._is_sql_backend() is False and mgr._is_firebase_backend() is True
    db.database_manager.config_error = True
    assert mgr._is_sql_backend() is False and mgr._is_firebase_backend() is False


# ======================================================================= initialization

def test_initialize_loads_everything(env, monkeypatch):
    db, mgr = env
    mgr._initialized = False
    mgr._startup_trim_done = False
    db.store["Alerts/BitAlerts"] = {"bits100": {"alert_name": "Hundred", "bogus": 1}}
    db.store["Alerts/FollowAlerts"] = {"follow1": {"duration": 4}}
    db.database_manager.storage_result = {"S1": {"timestamp": 1}}
    trims = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: trims.append(1))
    migrated = []
    monkeypatch.setattr(mgr, "migrate_individual_alerts_to_collections", lambda: migrated.append(1))
    mgr.initialize()
    assert mgr._initialized is True
    assert mgr._alert_state["bits"]["bits100"]["alert_name"] == "Hundred"
    assert au.BitAlerts["bits100"].alert_name == "Hundred"
    assert au.FollowAlerts["follow1"].duration == 4
    assert mgr._alert_storage == {"S1": {"timestamp": 1}} and mgr._alert_storage_loaded
    assert trims == [1] and migrated == [1]
    mgr.initialize()  # no-op
    assert migrated == [1]


def test_initialize_migration_failure_is_tolerated(env, monkeypatch):
    _, mgr = env
    mgr._initialized = False
    monkeypatch.setattr(mgr, "migrate_individual_alerts_to_collections",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    mgr.initialize()
    assert mgr._initialized is True


def test_load_alerts_error_is_logged(env, monkeypatch, caplog):
    db, mgr = env
    db.read_error.add("Alerts/SubAlerts")
    with caplog.at_level("ERROR", logger=au.logger.name):
        mgr._load_alerts_from_firebase()
    assert "Error loading alerts from Firebase" in caplog.text


def test_initialize_async(env, monkeypatch):
    db, mgr = env
    mgr._initialized = False
    mgr._startup_trim_done = False
    db.store["Alerts/SubAlerts"] = {"subs1": {"alert_name": "One"}}
    trims = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: trims.append(1))
    asyncio.run(mgr.initialize_async())
    assert mgr._initialized and au.SubAlerts["subs1"].alert_name == "One"
    assert mgr._alert_storage_loaded is False
    assert trims == [1]
    asyncio.run(mgr.initialize_async())  # no-op
    assert trims == [1]


def test_load_alerts_async_error(env, caplog):
    db, mgr = env
    db.multi_error = True
    with caplog.at_level("ERROR", logger=au.logger.name):
        asyncio.run(mgr._load_alerts_from_firebase_async())
    assert "Error loading alerts from Firebase" in caplog.text


def test_initialize_with_data(env, monkeypatch):
    db, mgr = env
    mgr._initialized = False
    mgr._alert_storage = {"old": {}}
    db.startup_failed.add("Alerts/SubAlerts")
    data = {"Alerts/BitAlerts": {"bits1": {"alert_name": "b"}}, "Alerts/SubAlerts": {},
            "Alerts/AlertStorage": {"x": {}}}
    mgr.initialize_with_data(data)
    assert mgr._initialized
    assert mgr._alert_state["bits"] == {"bits1": {"alert_name": "b"}}
    assert mgr._alert_storage == {} and mgr._alert_storage_loaded is False
    assert "bits" in mgr._loaded_state_keys
    assert "subs" in mgr._unreadable_state_keys
    assert "follows" not in mgr._loaded_state_keys  # not in the preload
    mgr.initialize_with_data({"Alerts/BitAlerts": {}})  # no-op once initialized
    assert mgr._alert_state["bits"] == {"bits1": {"alert_name": "b"}}


def test_run_startup_auto_trim_once(env, monkeypatch):
    _, mgr = env
    mgr._startup_trim_done = False
    calls = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: calls.append(1))
    mgr._run_startup_auto_trim()
    mgr._run_startup_auto_trim()
    assert calls == [1]


def test_run_startup_auto_trim_error(env, monkeypatch):
    _, mgr = env
    mgr._startup_trim_done = False
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: (_ for _ in ()).throw(RuntimeError()))
    mgr._run_startup_auto_trim()
    assert mgr._startup_trim_done is True


# ======================================================================= resub fallback

def test_resub_fallback_get(env):
    db, mgr = env
    path = "Alerts/Settings/resub_fallback_enabled"
    db.read_fail.add(path)
    assert mgr.get_resub_fallback_enabled() is False
    assert mgr._resub_fallback_enabled is None  # not cached
    db.read_fail.clear()
    db.store[path] = {"enabled": 1}
    assert mgr.get_resub_fallback_enabled() is True
    db.store[path] = {"enabled": False}
    assert mgr.get_resub_fallback_enabled() is True  # cached


@pytest.mark.parametrize("stored", [None, {"other": 1}, "yes"])
def test_resub_fallback_get_missing_value(env, stored):
    db, mgr = env
    db.store["Alerts/Settings/resub_fallback_enabled"] = stored
    assert mgr.get_resub_fallback_enabled() is False
    assert mgr._resub_fallback_enabled is False


def test_resub_fallback_get_error(env):
    db, mgr = env
    db.read_error.add("Alerts/Settings/resub_fallback_enabled")
    assert mgr.get_resub_fallback_enabled() is False
    assert mgr._resub_fallback_enabled is False


def test_resub_fallback_set(env):
    db, mgr = env
    path = "Alerts/Settings/resub_fallback_enabled"
    assert mgr.set_resub_fallback_enabled(True) is True
    assert db.store[path] == {"enabled": True} and mgr._resub_fallback_enabled is True
    db.write_fail.add(path)
    assert mgr.set_resub_fallback_enabled(False) is False
    assert mgr._resub_fallback_enabled is True
    db.write_fail.clear()
    db.write_error.add(path)
    assert mgr.set_resub_fallback_enabled(False) is False


# ======================================================================= storage loading

def test_invalidate_storage_cache(env):
    _, mgr = env
    mgr._alert_storage = {"a": {}}
    mgr._alert_storage_loaded = True
    mgr._invalidate_alert_storage_cache()
    assert mgr._alert_storage == {} and mgr._alert_storage_loaded is False


def test_ensure_storage_loaded_merges_in_memory_records(env):
    db, mgr = env
    db.database_manager.storage_result = {"A": {"timestamp": 1}, "B": {"timestamp": 2}}
    mgr._alert_storage = {"B": {"timestamp": 99}}
    mgr._ensure_alert_storage_loaded()
    assert mgr._alert_storage == {"A": {"timestamp": 1}, "B": {"timestamp": 99}}
    assert mgr._alert_storage_loaded and mgr._storage_load_event is None
    mgr._ensure_alert_storage_loaded()  # already loaded: no second fetch
    assert db.database_manager.storage_calls == [None]


def test_ensure_storage_loaded_non_dict_fetch(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "_fetch_alert_storage_from_database", lambda max_alerts=None: ["x"])
    mgr._ensure_alert_storage_loaded()
    assert mgr._alert_storage == {} and mgr._alert_storage_loaded


def test_ensure_storage_loaded_with_max_alerts_no_event(env, monkeypatch):
    _, mgr = env
    seen = []
    monkeypatch.setattr(mgr, "_fetch_alert_storage_from_database",
                        lambda max_alerts=None: seen.append(max_alerts) or {"a": {}})
    mgr._ensure_alert_storage_loaded(max_alerts=5)
    assert seen == [5] and mgr._storage_load_event is None


def test_ensure_storage_loaded_follower_waits(env, monkeypatch):
    _, mgr = env
    ev = threading.Event()
    mgr._storage_load_event = ev
    waited = []
    monkeypatch.setattr(mgr, "_wait_event", lambda e: waited.append(e))
    mgr._ensure_alert_storage_loaded()
    assert waited == [ev] and mgr._alert_storage_loaded is False


def test_ensure_storage_loaded_loaded_meanwhile(env, monkeypatch):
    _, mgr = env

    def fetch(max_alerts=None):
        mgr._alert_storage = {"keep": {}}
        mgr._alert_storage_loaded = True
        return {"fetched": {}}

    monkeypatch.setattr(mgr, "_fetch_alert_storage_from_database", fetch)
    mgr._ensure_alert_storage_loaded()
    assert mgr._alert_storage == {"keep": {}}
    assert mgr._storage_load_event is None


def test_prefetch_alert_storage(env):
    db, mgr = env
    db.database_manager.storage_result = {"A": {}}
    mgr.prefetch_alert_storage()
    assert "AlertStoragePrefetch" in fakes.SyncThread.started
    assert mgr._alert_storage == {"A": {}}
    fakes.SyncThread.started.clear()
    mgr.prefetch_alert_storage()  # loaded: nothing to start
    assert fakes.SyncThread.started == []


def test_prefetch_alert_storage_error_swallowed(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "_ensure_alert_storage_loaded", lambda: (_ for _ in ()).throw(RuntimeError()))
    mgr.prefetch_alert_storage()


def test_fetch_storage_backend_dict(env):
    db, mgr = env
    db.database_manager.storage_result = {"a": {}}
    assert mgr._fetch_alert_storage_from_database(10) == {"a": {}}
    assert db.database_manager.storage_calls == [10]


def test_fetch_storage_firebase_limited(env):
    db, mgr = env
    db.database_manager.database_type = "firebase"
    db.limited = {"x": {}}
    assert mgr._fetch_alert_storage_from_database(3) == {"x": {}}
    assert db.limited_calls == [3]
    db.limited = None
    assert mgr._fetch_alert_storage_from_database(3) == {}


def test_fetch_storage_collection_fallback(env):
    db, mgr = env
    assert mgr._fetch_alert_storage_from_database() == {}
    db.store["Alerts/AlertStorage"] = {"c": {}}
    assert mgr._fetch_alert_storage_from_database() == {"c": {}}
    assert mgr._load_individual_alert_storage() == {"c": {}}


def test_fetch_storage_error(env):
    db, mgr = env
    db.database_manager.storage_result = RuntimeError("down")
    assert mgr._fetch_alert_storage_from_database() == {}


# ======================================================================= globals and keys

def test_filter_alert_obj_fields(env):
    _, mgr = env
    assert mgr._filter_alert_obj_fields({"duration": 1, "junk": 2, "gif_dir": "d"}) == {"duration": 1, "gif_dir": "d"}


def test_update_global_collections_all_types(env):
    _, mgr = env
    for key in mgr._config_state_keys():
        mgr._alert_state[key] = {f"{key}-id": {"alert_name": key, "unknown": True}}
    mgr._update_global_collections()
    mapping = {
        "bits": au.BitAlerts, "bit_ranges": au.BitRangeAlerts, "subs": au.SubAlerts,
        "sub_ranges": au.SubRangeAlerts, "giftsubs": au.GiftsubAlerts, "giftsub_ranges": au.GiftsubRangeAlerts,
        "donations": au.DonationAlerts, "donation_ranges": au.DonationRangeAlerts, "raids": au.RaidAlerts,
        "raid_ranges": au.RaidRangeAlerts, "points": au.PointAlerts, "follows": au.FollowAlerts,
        "streaks": au.StreakAlerts, "streak_ranges": au.StreakRangeAlerts,
    }
    for key, coll in mapping.items():
        assert list(coll) == [f"{key}-id"]
        assert coll[f"{key}-id"].alert_name == key
    mgr._alert_state["bits"] = {}
    mgr._update_global_collections()
    assert au.BitAlerts == {}


@pytest.mark.parametrize("alert_type,alert_id,expected", [
    ("bits", "bits100", ("bits", "Alerts/BitAlerts/bits100", False)),
    ("bits", "bits1-99", ("bit_ranges", "Alerts/BitRangeAlerts/bits1-99", True)),
    ("subs", "subs2-5", ("sub_ranges", "Alerts/SubRangeAlerts/subs2-5", True)),
    ("giftsubs", "g1-5", ("giftsub_ranges", "Alerts/GiftsubRangeAlerts/g1-5", True)),
    ("donations", "d1-5", ("donation_ranges", "Alerts/DonationRangeAlerts/d1-5", True)),
    ("raids", "r1-5", ("raid_ranges", "Alerts/RaidRangeAlerts/r1-5", True)),
    ("streaks", "s1-5", ("streak_ranges", "Alerts/StreakRangeAlerts/s1-5", True)),
    ("points", "abc-def", ("points", "Alerts/PointAlerts/abc-def", False)),
    ("follows", None, ("follows", "Alerts/FollowAlerts", None)),
])
def test_get_state_and_firebase_keys(env, alert_type, alert_id, expected):
    _, mgr = env
    state_key, path, is_range = mgr._get_state_and_firebase_keys(alert_type, alert_id)
    assert (state_key, path) == expected[:2]
    assert bool(is_range) == bool(expected[2])


def test_get_state_and_firebase_keys_invalid(env):
    _, mgr = env
    assert mgr._get_state_and_firebase_keys("nope", "x")[:2] == (None, None)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alertutils.py, part 2: AlertStateManager reads, saves, deletes, reloads,
sync, by-id helpers, completed-alert storage and stored-alert trimming."""

from __future__ import annotations

import sys
import time
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
    """Manager not initialized yet; initialize() is recorded instead of loading."""
    _, mgr = env
    mgr._initialized = False
    calls = []

    def fake_init():
        calls.append(1)
        mgr._initialized = True

    monkeypatch.setattr(mgr, "initialize", fake_init)
    return calls


# ======================================================================= reads

def test_get_alerts_for_type(env, uninit):
    _, mgr = env
    mgr._alert_state["bits"] = {"bits1": {"a": 1}}
    mgr._alert_state["bit_ranges"] = {"bits2-5": {"a": 2}}
    assert mgr.get_alerts_for_type("bits") == {"bits1": {"a": 1}, "bits2-5": {"a": 2}}
    assert uninit == [1]


def test_get_alerts_for_type_error(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "get_alerts_by_type", lambda t: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.get_alerts_for_type("bits") == {}


def test_get_alert_by_id(env, uninit):
    _, mgr = env
    mgr._alert_state["sub_ranges"] = {"subs2-5": {"x": 1}}
    assert mgr.get_alert_by_id("subs", "subs2-5") == {"x": 1}
    assert mgr.get_alert_by_id("subs", "subs9") is None
    assert mgr.get_alert_by_id("nope", "x") is None
    assert uninit == [1]


def test_get_alert_by_id_error(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "_get_state_and_firebase_keys", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.get_alert_by_id("subs", "x") is None


@pytest.mark.parametrize("include_ranges", [True, False])
def test_get_alerts_by_type_ranges(env, uninit, include_ranges):
    _, mgr = env
    for t, r in [("bits", "bit_ranges"), ("subs", "sub_ranges"), ("giftsubs", "giftsub_ranges"),
                 ("donations", "donation_ranges"), ("raids", "raid_ranges"), ("streaks", "streak_ranges")]:
        mgr._alert_state[t] = {f"{t}1": {}}
        mgr._alert_state[r] = {f"{t}1-2": {}}
        out = mgr.get_alerts_by_type(t, include_ranges=include_ranges)
        assert (f"{t}1-2" in out) is include_ranges and f"{t}1" in out
    mgr._alert_state["follows"] = {"follow1": {}}
    mgr._alert_state["points"] = {"rw": {}}
    assert mgr.get_alerts_by_type("follows") == {"follow1": {}}
    assert mgr.get_alerts_by_type("points") == {"rw": {}}
    assert mgr.get_alerts_by_type("unknown") == {}


def test_get_alerts_by_type_returns_copies_and_lazy_loads(env):
    db, mgr = env
    db.store["Alerts/RaidRangeAlerts"] = {"raids5-10": {"duration": 1}}
    out = mgr.get_alerts_by_type("raids")
    assert out == {"raids5-10": {"duration": 1}}
    out["raids5-10"]["duration"] = 99
    assert mgr._alert_state["raid_ranges"]["raids5-10"]["duration"] == 1


def test_get_alerts_by_type_error(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "_lazy_load_config_keys", lambda keys: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.get_alerts_by_type("bits") == {}


def test_ensure_alert_paths_loaded(env):
    db, mgr = env
    db.store["Alerts/GiftsubAlerts"] = {"giftsubs5": {"alert_name": "five"}}
    mgr.ensure_alert_paths_loaded(("giftsubs",))
    assert au.GiftsubAlerts["giftsubs5"].alert_name == "five"


# ======================================================================= save / delete

def test_save_alert_sql_writes_child_and_collection(env, uninit):
    db, mgr = env
    data = {"duration": 5, "gif_name": "a.gif"}
    assert mgr.save_alert("bits", "bits100", data) is True
    assert uninit == [1]
    assert data == {"duration": 5, "gif_name": "a.gif"}  # caller's dict untouched
    stored = mgr._alert_state["bits"]["bits100"]
    assert stored["display_name"] == "100 Bits"
    assert [w[0] for w in db.writes] == ["Alerts/BitAlerts/bits100", "Alerts/BitAlerts"]
    assert db.store["Alerts/BitAlerts"] == {"bits100": stored}
    assert au.BitAlerts["bits100"].gif_name == "a.gif"
    assert mgr.last_write_error == ""


def test_save_alert_firebase_skips_child_write(env):
    db, mgr = env
    db.database_manager.database_type = "firebase"
    assert mgr.save_alert("follows", "follow1", {"duration": 1}) is True
    assert [w[0] for w in db.writes] == ["Alerts/FollowAlerts"]


def test_save_alert_without_collection_update(env):
    db, mgr = env
    db.database_manager.database_type = "firebase"
    assert mgr.save_alert("subs", "subs1", {}, update_collection=False, quiet_db_notices=True) is True
    assert [w[0] for w in db.writes] == ["Alerts/SubAlerts/subs1"]
    assert db.quiet_used == 1
    assert "subs1" not in au.SubAlerts  # globals not refreshed for batch saves


def test_save_alert_unknown_type(env):
    _, mgr = env
    assert mgr.save_alert("nope", "x", {}) is False
    assert mgr.last_write_error == "unknown alert type 'nope'"


def test_save_alert_unreadable(env):
    db, mgr = env
    mgr._unreadable_state_keys.add("bits")
    db.read_fail.add("Alerts/BitAlerts")
    assert mgr.save_alert("bits", "bits1", {}) is False
    assert mgr.last_write_error == "unreadable"
    assert db.writes == []


def test_save_alert_write_failed(env):
    db, mgr = env
    db.write_fail.add("Alerts/BitAlerts/bits1")
    assert mgr.save_alert("bits", "bits1", {}) is False
    assert mgr.last_write_error.startswith("Database write failed: Alerts/BitAlerts/bits1")


def test_save_alert_exception(env):
    db, mgr = env
    db.write_error.add("Alerts/BitAlerts/bits1")
    assert mgr.save_alert("bits", "bits1", {}) is False
    assert mgr.last_write_error == "RuntimeError: write error Alerts/BitAlerts/bits1"


def test_update_alert_is_save_alias(env):
    _, mgr = env
    assert mgr.update_alert("raids", "raids10", {"duration": 2}) is True
    assert mgr._alert_state["raids"]["raids10"]["display_name"] == "10 Raiders"


def test_delete_alert_sql(env, uninit):
    db, mgr = env
    mgr.save_alert("bits", "bits1", {})
    mgr.save_alert("bits", "bits2", {})
    db.writes.clear()
    assert mgr.delete_alert("bits", "bits1") is True
    assert uninit == [1]
    assert "bits1" not in mgr._alert_state["bits"]
    assert db.deletes == ["Alerts/BitAlerts/bits1"]
    assert db.store["Alerts/BitAlerts"].keys() == {"bits2"}
    assert "bits1" not in au.BitAlerts


def test_delete_alert_firebase_missing_id(env):
    db, mgr = env
    db.database_manager.database_type = "firebase"
    assert mgr.delete_alert("follows", "ghost", quiet_db_notices=True) is True
    assert db.deletes == [] and [w[0] for w in db.writes] == ["Alerts/FollowAlerts"]


def test_delete_alert_failures(env):
    db, mgr = env
    assert mgr.delete_alert("nope", "x") is False
    assert mgr.last_write_error == "unknown alert type 'nope'"
    mgr._unreadable_state_keys.add("subs")
    db.read_fail.add("Alerts/SubAlerts")
    assert mgr.delete_alert("subs", "subs1") is False
    assert mgr.last_write_error == "unreadable"
    db.write_fail.add("Alerts/RaidAlerts")
    assert mgr.delete_alert("raids", "raids1") is False
    assert "Database write failed" in mgr.last_write_error
    db.delete_error.add("Alerts/PointAlerts/p1")
    assert mgr.delete_alert("points", "p1") is False
    assert mgr.last_write_error == "RuntimeError: delete error Alerts/PointAlerts/p1"


@pytest.mark.parametrize("alert_type,expected", [
    ("bits", "bit1"), ("subs", "sub1"), ("follows", "follow1"), ("points", "point1"), ("raids", "raid1"),
    ("giftsubs", "giftsub1"), ("donations", "donation1"), ("streaks", "streaks1"), ("nope", None),
])
def test_get_default_alert_id(env, alert_type, expected):
    _, mgr = env
    assert mgr.get_default_alert_id(alert_type) == expected


def test_get_default_alert_id_error(env, monkeypatch):
    _, mgr = env
    monkeypatch.delattr(au.AlertSettings, "default_bit_alert")
    assert mgr.get_default_alert_id("bits") is None


def test_get_all_alerts(env, uninit):
    db, mgr = env
    db.database_manager.storage_result = {"S": {"timestamp": 1}}
    mgr._alert_state["follows"] = {"f": {}}
    out = mgr.get_all_alerts()
    assert out["follows"] == {"f": {}} and out["alert_storage"] == {"S": {"timestamp": 1}}
    out["follows"]["g"] = {}
    assert "g" not in mgr._alert_state["follows"]


# ======================================================================= reloads / sync

def test_reload_from_firebase(env):
    db, mgr = env
    mgr._alert_storage = {"old": {}}
    mgr._alert_storage_loaded = True
    db.store["Alerts/SubAlerts"] = {"subs3": {}}
    db.database_manager.storage_result = {"new": {}}
    mgr.reload_from_firebase()
    assert mgr._alert_state["subs"] == {"subs3": {}}
    assert mgr._alert_storage == {"new": {}}


def test_reload_alert_type(env):
    db, mgr = env
    db.store["Alerts/StreakAlerts"] = {"streaks3": {"alert_name": "three"}}
    db.read_fail.add("Alerts/StreakRangeAlerts")
    mgr._alert_state["streak_ranges"] = {"streaks1-2": {}}
    mgr.reload_alert_type_from_firebase("streaks")
    assert mgr._alert_state["streaks"] == {"streaks3": {"alert_name": "three"}}
    assert mgr._alert_state["streak_ranges"] == {"streaks1-2": {}}  # failed read keeps memory
    assert au.StreakAlerts["streaks3"].alert_name == "three"


@pytest.mark.parametrize("alert_type", ["bits", "subs", "giftsubs", "donations", "raids", "streaks",
                                        "follows", "points"])
def test_reload_alert_type_each(env, alert_type):
    db, mgr = env
    mgr.reload_alert_type_from_firebase(alert_type, include_ranges=False)
    state_key, _, _ = mgr._get_state_and_firebase_keys(alert_type)
    assert state_key in mgr._loaded_state_keys


def test_reload_alert_type_failed_read_empty(env):
    db, mgr = env
    db.read_fail.add("Alerts/FollowAlerts")
    mgr.reload_alert_type_from_firebase("follows")
    assert mgr._alert_state["follows"] == {}
    assert "follows" in mgr._unreadable_state_keys


def test_reload_alert_type_invalid(env):
    db, mgr = env
    mgr.reload_alert_type_from_firebase("bogus")
    assert db.writes == []


def test_reload_alert_type_skips_unknown_state_keys(env):
    _, mgr = env
    del mgr._alert_paths["bit_ranges"]
    mgr.reload_alert_type_from_firebase("bits")
    assert "bits" in mgr._loaded_state_keys


def test_sync_to_firebase(env):
    db, mgr = env
    mgr._alert_state["bits"] = {"bits1": {}}
    db.database_manager.storage_result = {"S": {}}
    mgr._changes_pending = True
    assert mgr.sync_to_firebase() is True
    assert db.store["Alerts/BitAlerts"] == {"bits1": {}}
    assert db.store["Alerts/AlertStorage"] == {"S": {}}
    assert len(db.writes) == len(mgr._alert_paths)
    assert mgr._changes_pending is False


def test_sync_to_firebase_unreadable_and_failures(env):
    db, mgr = env
    mgr._unreadable_state_keys.add("subs")
    db.read_fail.add("Alerts/SubAlerts")
    assert mgr.sync_to_firebase() is False
    assert db.writes == []
    db.read_fail.clear()
    db.write_fail.add("Alerts/FollowAlerts")
    assert mgr.sync_to_firebase() is False


def test_update_alert_collections(env, uninit):
    db, mgr = env
    mgr._alert_state["raids"] = {"raids1": {"alert_name": "r"}}
    assert mgr.update_alert_collections("raids") is True
    assert uninit == [1]
    assert db.store["Alerts/RaidAlerts"] == {"raids1": {"alert_name": "r"}}
    assert au.RaidAlerts["raids1"].alert_name == "r"


def test_update_alert_collections_failures(env):
    db, mgr = env
    assert mgr.update_alert_collections("nope") is False
    mgr._unreadable_state_keys.add("bits")
    db.read_fail.add("Alerts/BitAlerts")
    assert mgr.update_alert_collections("bits") is False
    db.write_fail.add("Alerts/SubAlerts")
    assert mgr.update_alert_collections("subs") is False


# ======================================================================= by-id helpers

@pytest.mark.parametrize("alert_id,expected", [
    ("bits100", ("bits", "100")),
    ("giftsubs5", ("giftsubs", "5")),
    ("donations1-5", ("donations", "1-5")),
    ("follows", ("follows", "follows")),
    ("subs", ("subs", "subs")),
    ("streaks", ("streaks", "streaks")),
    ("raids", ("raids", "")),
    ("reward-uuid", ("points", "reward-uuid")),
])
def test_parse_alert_id(env, alert_id, expected):
    _, mgr = env
    assert mgr._parse_alert_id(alert_id) == expected


def test_parse_alert_id_error(env):
    _, mgr = env
    assert mgr._parse_alert_id(None) == (None, None)


def test_save_alert_by_id(env, uninit):
    db, mgr = env
    mgr._alert_state["bits"] = {"100": {"duration": 3}}
    assert mgr.save_alert_by_id("bits100") is True
    assert uninit == [1]
    assert db.store["Alerts/BitAlerts"]["100"]["duration"] == 3
    assert mgr.save_alert_by_id("bits999") is False  # not in state


def test_save_alert_by_id_unparseable_and_error(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(mgr, "_parse_alert_id", lambda a: (None, None))
    assert mgr.save_alert_by_id("x") is False
    monkeypatch.setattr(mgr, "_parse_alert_id", lambda a: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.save_alert_by_id("x") is False


def test_update_alert_data(env, uninit, monkeypatch):
    db, mgr = env
    assert mgr.update_alert_data("subs3", {"duration": 1}) is True
    assert uninit == [1]
    assert mgr._alert_state["subs"]["3"]["duration"] == 1
    db.write_fail.add("Alerts/SubAlerts/4")
    assert mgr.update_alert_data("subs4", {}) is False
    monkeypatch.setattr(mgr, "_parse_alert_id", lambda a: (None, None))
    assert mgr.update_alert_data("x", {}) is False
    monkeypatch.setattr(mgr, "_parse_alert_id", lambda a: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.update_alert_data("x", {}) is False


# ======================================================================= completed alert storage

def test_store_completed_alert(env, uninit, monkeypatch):
    db, mgr = env
    trims = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: trims.append(1))
    version = mgr._storage_version
    assert mgr.store_completed_alert("A1", {"username": "Ann", "alert_type": "follow", "timestamp": 5}) is True
    assert uninit == [1]
    rec = db.store["Alerts/AlertStorage/A1"]
    assert rec["username"] == "Ann" and rec["timestamp"] == 5.0 and rec["currency"] == "USD"
    assert mgr._alert_storage["A1"] == rec
    assert mgr._storage_version == version + 1
    assert trims == [1]


def test_store_completed_alert_missing_username_and_trim_error(env, monkeypatch):
    db, mgr = env
    monkeypatch.setattr(mgr, "_normalize_alert_data_for_storage", lambda d: dict(d))
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.store_completed_alert("A2", {"alert_type": "x"}) is True


def test_store_completed_alert_normalize_error(env, monkeypatch):
    db, mgr = env
    monkeypatch.setattr(mgr, "_normalize_alert_data_for_storage", lambda d: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.store_completed_alert("A3", {}) is False
    assert db.writes == []


def test_store_completed_alert_write_failed(env):
    db, mgr = env
    db.write_fail.add("Alerts/AlertStorage/A4")
    assert mgr.store_completed_alert("A4", {"username": "u"}) is False
    assert "A4" in mgr._alert_storage  # memory keeps it for this session


def test_store_completed_alert_write_exception_notifies(env, monkeypatch):
    db, mgr = env
    seen = []
    monkeypatch.setattr(au, "_notify_history_store_failed", lambda aid, reason: seen.append((aid, reason)))
    db.write_error.add("Alerts/AlertStorage/A5")
    assert mgr.store_completed_alert("A5", {"username": "u"}) is False
    assert seen == [("A5", "RuntimeError: write error Alerts/AlertStorage/A5")]


def test_normalize_alert_data_for_storage(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(au.time, "time", lambda: 1234.0)
    out = mgr._normalize_alert_data_for_storage({
        "username": None, "message": None, "currency": None, "tier": "2", "volume": "loud",
        "duration": None, "played": 1, "anonymous": "", "timestamp": 0, "extra": "kept",
    })
    assert out["username"] == "" and out["message"] == "" and out["currency"] == "USD"
    assert out["emotes"] is None and out["gif_dir"] == "" and out["twitch_reward_id"] == ""
    assert out["tier"] == 2 and out["volume"] == 0 and out["duration"] == 0.0
    assert out["played"] is True and out["anonymous"] is False
    assert out["timestamp"] == 1234.0
    assert out["extra"] == "kept"
    assert out["alert_type"] == ""


def test_normalize_keeps_valid_timestamp(env):
    _, mgr = env
    assert mgr._normalize_alert_data_for_storage({"timestamp": "55.5"})["timestamp"] == 55.5


def test_normalize_error_returns_copy(env, monkeypatch):
    _, mgr = env
    monkeypatch.setattr(au, "AlertObj", lambda: (_ for _ in ()).throw(RuntimeError()))
    data = {"a": [1]}
    out = mgr._normalize_alert_data_for_storage(data)
    assert out == data and out is not data and out["a"] is not data["a"]


# ======================================================================= trim settings & scheduling

@pytest.fixture
def app_settings(monkeypatch):
    from modules import dataobjects

    holder = {"settings": SimpleNamespace(alert_storage_auto_trim=True, alert_storage_trim_mode="Quantity",
                                          alert_storage_keep_count=10, alert_storage_keep_days=2)}

    class FakeState:
        def get_app_settings(self):
            s = holder["settings"]
            if isinstance(s, Exception):
                raise s
            return s

    monkeypatch.setattr(dataobjects, "state_manager", FakeState())
    return holder


def test_read_trim_settings(env, app_settings):
    _, mgr = env
    assert mgr._read_alert_storage_trim_settings() == {"enabled": True, "mode": "quantity",
                                                       "keep_count": 10, "keep_days": 2}
    app_settings["settings"] = SimpleNamespace(alert_storage_trim_mode="weird", alert_storage_keep_count=0,
                                               alert_storage_keep_days=-5)
    assert mgr._read_alert_storage_trim_settings() == {"enabled": False, "mode": "both",
                                                       "keep_count": 500, "keep_days": 1}
    app_settings["settings"] = RuntimeError("no settings")
    assert mgr._read_alert_storage_trim_settings() == {"enabled": False, "mode": "both",
                                                       "keep_count": 500, "keep_days": 30}


def test_trim_progress_helpers(env):
    _, mgr = env
    mgr._set_trim_progress(active=True, phase="deleting")
    p = mgr.get_trim_progress()
    assert p["active"] and p["phase"] == "deleting"
    p["phase"] = "x"
    assert mgr.get_trim_progress()["phase"] == "deleting"
    mgr._trim_pending = True
    mgr.cancel_alert_storage_trim()
    assert mgr._trim_cancel.is_set() and mgr._trim_pending is False
    assert mgr.get_trim_progress()["active"] is False


def test_schedule_skips_during_shutdown(env, monkeypatch, app_settings):
    from modules import shutdown

    _, mgr = env
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: True)
    mgr.schedule_auto_trim_stored_alerts()
    assert fakes.SyncThread.started == []


def test_schedule_shutdown_check_error_and_disabled(env, monkeypatch, app_settings):
    from modules import shutdown

    _, mgr = env
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: (_ for _ in ()).throw(RuntimeError()))
    app_settings["settings"] = SimpleNamespace(alert_storage_auto_trim=False)
    mgr.schedule_auto_trim_stored_alerts()
    assert fakes.SyncThread.started == []


def test_schedule_marks_pending_when_running(env, app_settings):
    _, mgr = env
    mgr._trim_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.schedule_auto_trim_stored_alerts()
    assert mgr._trim_pending is True
    assert fakes.SyncThread.started == []


def test_schedule_runs_worker(env, monkeypatch, app_settings):
    db, mgr = env
    runs = []
    monkeypatch.setattr(mgr, "maybe_auto_trim_stored_alerts", lambda: runs.append(1) or 3)
    mgr._trim_cancel.set()
    mgr.schedule_auto_trim_stored_alerts()
    assert fakes.SyncThread.started == ["AlertStorageTrim"]
    assert runs == [1]  # cancel cleared before start
    assert db.polls == [1]


def test_schedule_footer_poll_error_ignored(env, monkeypatch, app_settings):
    from modules import notification_engine

    _, mgr = env
    monkeypatch.setattr(mgr, "maybe_auto_trim_stored_alerts", lambda: 0)
    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    mgr.schedule_auto_trim_stored_alerts()
    assert fakes.SyncThread.started == ["AlertStorageTrim"]


def test_trim_worker_loop_pending_reruns(env, monkeypatch):
    _, mgr = env
    runs = []

    def trim():
        runs.append(1)
        if len(runs) == 1:
            mgr._trim_pending = True
            raise RuntimeError("first pass fails")
        return 0

    monkeypatch.setattr(mgr, "maybe_auto_trim_stored_alerts", trim)
    mgr._set_trim_progress(active=True)
    mgr._trim_worker_loop()
    assert runs == [1, 1]
    assert mgr.get_trim_progress()["active"] is False


def test_trim_worker_loop_cancelled(env, monkeypatch):
    _, mgr = env
    mgr._trim_cancel.set()
    monkeypatch.setattr(mgr, "maybe_auto_trim_stored_alerts", lambda: pytest.fail("should not run"))
    mgr._trim_worker_loop()


def test_trim_worker_loop_restarts_when_pending_at_exit(env, monkeypatch):
    import threading

    _, mgr = env
    mgr._trim_thread = threading.current_thread()
    restarts = []
    monkeypatch.setattr(mgr, "schedule_auto_trim_stored_alerts", lambda: restarts.append(1))

    def trim():
        # cancel then un-cancel so the loop breaks with work still pending
        mgr._trim_cancel.set()
        return 0

    monkeypatch.setattr(mgr, "maybe_auto_trim_stored_alerts", trim)

    class Lock:
        n = 0

        def __enter__(self):
            Lock.n += 1
            if Lock.n == 2:  # the finally block
                mgr._trim_cancel.clear()
                mgr._trim_pending = True

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mgr, "_trim_lock", Lock())
    mgr._trim_worker_loop()
    assert restarts == [1]
    assert mgr._trim_thread is None


def test_maybe_auto_trim(env, monkeypatch, app_settings):
    _, mgr = env
    seen = []
    monkeypatch.setattr(mgr, "trim_stored_alerts", lambda **kw: seen.append(kw) or 4)
    assert mgr.maybe_auto_trim_stored_alerts() == 4
    assert seen == [{"mode": "quantity", "keep_count": 10, "keep_days": 2}]
    app_settings["settings"] = SimpleNamespace(alert_storage_auto_trim=False)
    assert mgr.maybe_auto_trim_stored_alerts() == 0


# ======================================================================= trim_stored_alerts

def _storage(now):
    return {
        "new": {"timestamp": now},
        "mid": {"timestamp": now - 86400 * 2},
        "old": {"timestamp": now - 86400 * 40},
        "unknown": {"timestamp": 0},
        "none": None,
    }


def test_trim_memory_quantity_without_batch(env):
    db, mgr = env
    now = time.time()
    mgr._alert_storage = _storage(now)
    mgr._alert_storage_loaded = True
    deleted = mgr.trim_stored_alerts(mode="quantity", keep_count=2, keep_days=30)
    assert deleted == 3
    assert set(mgr._alert_storage) == {"new", "mid"}
    assert sorted(db.deletes) == ["Alerts/AlertStorage/none", "Alerts/AlertStorage/old",
                                  "Alerts/AlertStorage/unknown"]
    assert mgr.get_trim_progress()["active"] is False
    assert db.polls == [1]


def test_trim_bad_mode_and_counts(env):
    db, mgr = env
    now = time.time()
    mgr._alert_storage = _storage(now)
    mgr._alert_storage_loaded = True
    # mode "weird" -> both; keep_count None -> 1; keep_days 0 -> 1
    deleted = mgr.trim_stored_alerts(mode="weird", keep_count=None, keep_days=0)
    assert deleted == 4 and set(mgr._alert_storage) == {"new"}


def test_trim_nothing_to_delete(env):
    db, mgr = env
    mgr._alert_storage = {"a": {"timestamp": time.time()}}
    mgr._alert_storage_loaded = True
    assert mgr.trim_stored_alerts(mode="time", keep_days=1) == 0
    assert db.deletes == []


def test_trim_cancelled_before_start(env):
    _, mgr = env
    mgr._trim_cancel.set()
    assert mgr.trim_stored_alerts() == 0
    assert mgr.get_trim_progress()["active"] is False


def test_trim_footer_poll_error_ignored(env, monkeypatch):
    from modules import notification_engine

    _, mgr = env
    mgr._alert_storage_loaded = True
    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    assert mgr.trim_stored_alerts() == 0


def test_trim_firebase_loads_storage_once(env):
    db, mgr = env
    now = time.time()
    db.database_manager.database_type = "firebase"
    db.database_manager.storage_result = {"a": {"timestamp": now}, "b": {"timestamp": now - 1}, "x": "bad"}
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 1
    assert db.deletes == ["Alerts/AlertStorage/b"]
    assert db.database_manager.storage_calls == [None]
    assert "a" in mgr._alert_storage


def test_trim_sql_meta_and_batch_delete(env):
    db, mgr = env
    now = time.time()
    backend = db.database_manager
    backend.meta = [("a", now), ("b", now - 10), ("c", now - 20)]
    backend._initialized = True
    backend._database = SimpleNamespace(delete_paths=lambda p: True)
    mgr._alert_storage = {"c": {"timestamp": now - 20}}  # partial memory copy
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 2
    assert sorted(backend.delete_paths_calls[0]) == ["Alerts/AlertStorage/b", "Alerts/AlertStorage/c"]
    assert mgr._alert_storage == {}


def test_trim_batch_delete_failure_and_error(env):
    db, mgr = env
    now = time.time()
    backend = db.database_manager
    backend.meta = [("a", now), ("b", now - 10)]
    backend._initialized = True
    backend._database = SimpleNamespace(delete_paths=lambda p: True)
    backend.delete_paths_result = False
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 0
    backend.delete_paths_error = True
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 0


def test_trim_batch_chunks_and_cancel(env, monkeypatch):
    db, mgr = env
    now = time.time()
    backend = db.database_manager
    backend.meta = [(f"id{i}", now - i) for i in range(251)]
    backend._initialized = True
    backend._database = SimpleNamespace(delete_paths=lambda p: True)
    real = backend.delete_paths

    def delete_then_cancel(paths):
        out = real(paths)
        if len(backend.delete_paths_calls) == 2:
            mgr._trim_cancel.set()
        return out

    backend.delete_paths = delete_then_cancel
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 200
    assert [len(c) for c in backend.delete_paths_calls] == [100, 100]


def test_trim_meta_failure_falls_back_to_full_load(env):
    db, mgr = env
    now = time.time()
    db.database_manager.meta_error = True
    db.database_manager.storage_result = {"a": {"timestamp": now}, "b": {"timestamp": now - 5}}
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 1
    assert db.deletes == ["Alerts/AlertStorage/b"]


def test_trim_meta_none_falls_back(env):
    db, mgr = env
    db.database_manager.meta = None
    db.database_manager.storage_result = {"a": None}
    assert mgr.trim_stored_alerts(mode="time") == 0


def test_trim_single_delete_errors_keep_records(env):
    db, mgr = env
    now = time.time()
    mgr._alert_storage = {f"id{i}": {"timestamp": now - i} for i in range(3)}
    mgr._alert_storage_loaded = True
    db.delete_error.update({"Alerts/AlertStorage/id1", "Alerts/AlertStorage/id2"})
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 0
    assert set(mgr._alert_storage) == {"id0", "id1", "id2"}


def test_trim_single_delete_cancel_mid_way(env):
    db, mgr = env
    now = time.time()
    mgr._alert_storage = {f"id{i}": {"timestamp": now - i} for i in range(4)}
    mgr._alert_storage_loaded = True
    real = db.delete_data

    def delete(path):
        real(path)
        mgr._trim_cancel.set()
        return True

    db.delete_data = delete
    assert mgr.trim_stored_alerts(mode="quantity", keep_count=1) == 1
    assert len(mgr._alert_storage) == 3 and "id0" in mgr._alert_storage
    assert mgr.get_trim_progress()["active"] is False

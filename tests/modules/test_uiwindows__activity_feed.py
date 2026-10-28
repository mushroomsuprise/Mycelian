# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/activity_feed.py (part 1): live list, shared view manager, feed
settings, replay/skip, payload builders and previews, stored alerts, history, By person grouping
and aggregated text. The UI host, slots, watchdog and actions are in ..._part2.py.
The database, overlay server, alert queue and notifications are all fakes."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import alertutils, database_manager, dataobjects, web_engine  # noqa: E402
from modules.uiwindows import activity_feed as feed  # noqa: E402
import time as _time
import types as _types


def _local_clock(module, monkeypatch, **fakes):
    """Give ``module`` its own copy of ``time`` with some functions replaced.

    Patching the real time module would hand the fake clock to every thread in the process.
    """
    clock = _types.SimpleNamespace(**{k: getattr(_time, k) for k in dir(_time) if not k.startswith("__")})
    for name, fn in fakes.items():
        setattr(clock, name, fn)
    monkeypatch.setattr(module, "time", clock)


_ORIGINALS = {name: getattr(feed, name) for name in ("_emit_overlay_event", "_run_on_ui_loop", "notify", "show_sub_duration")}

_GLOBALS = (
    "_live_seq", "_legacy_hours_cache", "_history_payload", "_person_payload", "_history_task_running",
    "_history_rerun", "_condensed_update_scheduled", "_condensed_rebuild_running", "_condensed_rebuild_rerun",
    "_host", "_slot_cache", "_slot_rev", "_live_push_scheduled", "_queue_poll_timer", "_watchdog_timer",
    "_last_queue_sig", "_last_meta", "_meta_tick", "_health_failures", "_registered_event_clients",
    "_assets_shared", "_hooks_registered_client_ids",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fresh module state, a view manager that never touches the database, no overlay."""
    import copy

    for name in _GLOBALS:
        monkeypatch.setattr(feed, name, copy.copy(getattr(feed, name)))
    monkeypatch.setattr(feed, "activity_feed_state", feed.ActivityFeedState())
    vm = feed.ActivityFeedViewManager()
    vm._loaded = True
    saves = []
    monkeypatch.setattr(vm, "save", lambda: saves.append(dict(vm._view)) or True)
    monkeypatch.setattr(feed, "view_manager", vm)
    monkeypatch.setattr(feed, "alert_event_handler", feed.AlertEventHandler())
    emitted = []
    monkeypatch.setattr(feed, "_emit_overlay_event", lambda event, payload, to=None: emitted.append((event, payload, to)) or True)
    ui_calls = []
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: ui_calls.append(fn))
    notes = []
    monkeypatch.setattr(feed, "notify", lambda msg, **k: notes.append((msg, k)))
    monkeypatch.setattr(alertutils, "resolve_chat_alert_media", lambda alert, preview_placeholder=False: None)
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: True)
    return SimpleNamespace(vm=vm, saves=saves, emitted=emitted, ui_calls=ui_calls, notes=notes)


# ----------------------------------------------------------------- small helpers

def test_stale_client_and_element_alive():
    assert feed._is_stale_client_error(RuntimeError("The client this element belongs to has been deleted"))
    assert not feed._is_stale_client_error(RuntimeError("other"))
    assert feed._element_alive(None) is False
    assert feed._element_alive(SimpleNamespace(is_deleted=True)) is False
    assert feed._element_alive(SimpleNamespace(is_deleted=False, client=object())) is True

    class _Stale:
        is_deleted = False

        @property
        def client(self):
            raise RuntimeError("client deleted")

    class _Other(_Stale):
        @property
        def client(self):
            raise RuntimeError("boom")

    class _Weird(_Stale):
        @property
        def client(self):
            raise ValueError("x")

    assert feed._element_alive(_Stale()) is False
    with pytest.raises(RuntimeError):
        feed._element_alive(_Other())
    assert feed._element_alive(_Weird()) is False


def test_next_seq_and_trim(monkeypatch):
    assert feed._next_seq() == feed._live_seq
    monkeypatch.setattr(feed, "MAX_LIVE_ALERTS", 2)
    deleted = []

    class _El:
        is_deleted = False
        client = object()

        def __init__(self, fail=False):
            self.fail = fail

        def delete(self):
            if self.fail:
                raise RuntimeError("x")
            deleted.append(self)

    a, b = _El(), _El(fail=True)
    state = feed.activity_feed_state
    state.live_alerts = [{"n": 1}, {"n": 2}, {"n": 3, "element": a}, {"n": 4, "element": b}, "junk"]
    feed._trim_live_alerts()
    assert [x["n"] for x in state.live_alerts] == [1, 2] and deleted == [a]
    feed._trim_live_alerts()  # under the cap
    assert len(feed.live_alerts_snapshot()) == 2


def test_row_for():
    assert feed._row_for({"row": {"kind": "bits", "x": 1}}) == {"kind": "bits", "x": 1}
    assert feed._row_for({"type": "follow", "username": "A", "row": {}})["kind"] == "follow"
    assert feed._row_for(None)["kind"] == "alert"


def test_alert_event_handler(monkeypatch, isolated):
    pushes, rebuilds = [], []
    monkeypatch.setattr(feed, "_schedule_live_push", lambda: pushes.append(1))
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: rebuilds.append(reason))
    h = feed.AlertEventHandler()
    h.process_alert_immediately("not a dict")
    h.process_alert_immediately({"type": "follow", "username": "A"})
    entry = feed.activity_feed_state.live_alerts[0]
    assert entry["is_restored"] is False and entry["row"]["kind"] == "follow" and pushes == [1] and rebuilds == []
    isolated.vm._view["condensed"] = True
    h.process_alert_immediately({"type": "follow", "row": {"kind": "follow"}})
    assert rebuilds == ["new_alert"]
    h.force_timestamp_update()
    h.shutdown()
    h.process_alert_immediately({"type": "follow"})
    assert len(feed.activity_feed_state.live_alerts) == 2
    monkeypatch.setattr(feed, "_trim_live_alerts", lambda: 1 / 0)
    feed.AlertEventHandler().process_alert_immediately({"type": "follow"})  # logged, no raise


@pytest.mark.parametrize("delta, text", [(10, "just now"), (120, "2m ago"), (7200, "2h ago"), (200000, "2d ago")])
def test_format_timestamp(delta, text):
    assert feed.format_timestamp(time.time() - delta) == text


def test_format_timestamp_special():
    assert feed.format_timestamp("now") == "just now"
    assert feed.format_timestamp("garbage") == "just now"


# ----------------------------------------------------------------- filters / view normalisation

def test_filters_and_view_normalisation():
    assert feed.normalize_filter_state("x") == feed.default_filter_state()
    f = feed.normalize_filter_state({"bits": "false", "zzz": True, "subs": 0})
    assert f["bits"] is False and f["subs"] is False and "zzz" not in f
    assert feed._coerce_bool("maybe", True) is True and feed._coerce_bool(2, False) is True
    rules = feed.apply_filter_rules({"all": True, "bits": False, "subs": True}, "all")
    assert rules == {"all": True, "bits": True, "subs": True}
    assert feed.apply_filter_rules({"all": False, "bits": True, "subs": True}, "bits")["all"] is True
    assert feed.apply_filter_rules({"all": True, "bits": False, "subs": True}, "bits")["all"] is False
    assert feed.apply_filter_rules({"all": False}, None) == {"all": False}
    v = feed.normalize_view({"tab": "weird", "page": "x", "condensed": "yes", "search": "  a  b "})
    assert v["tab"] == "current" and v["page"] == 1 and v["condensed"] is True and v["search"] == "a b"
    assert feed.normalize_view({"page": -4})["page"] == 1
    assert feed.normalize_view(None)["tab"] == "current"


def test_max_history_pages(monkeypatch):
    sm = SimpleNamespace(_initialized=False)
    monkeypatch.setattr(dataobjects, "state_manager", sm)
    assert feed._max_history_pages() == 50
    sm._initialized = True
    sm.get_app_settings = lambda: SimpleNamespace(activity_feed_max_pages=0)
    assert feed._max_history_pages() == 1
    sm.get_app_settings = lambda: SimpleNamespace(activity_feed_max_pages=7)
    assert feed._max_history_pages() == 7
    sm.get_app_settings = lambda: 1 / 0
    assert feed._max_history_pages() == 50


def test_json_ready_and_serialize():
    data = {"a": 1, "element": object(), "nested": {"b": [1, object(), (2, 3)], "obj": object()}, 5: None}
    assert feed._json_ready(data) == {"a": 1, "nested": {"b": [1, [2, 3]]}, "5": None}
    assert feed.serialize_live_alert(object()) == {}
    assert feed.serialize_live_alerts([{"x": 1}, "junk"]) == [{"x": 1}]


# ----------------------------------------------------------------- view manager

class _DB:
    def __init__(self, data=None, ok=True, fail_get=False):
        self.data, self.ok, self.fail_get = data, ok, fail_get
        self.sets = []
        self.checked = 0

    def get_data(self, path):
        if self.fail_get:
            raise RuntimeError("db down")
        return self.data if self.ok else {}

    def get_data_checked(self, path):
        self.checked += 1
        return (self.data if self.ok else {}), self.ok

    def set_data(self, path, payload):
        self.sets.append((path, payload))
        return True


@pytest.fixture
def db(monkeypatch):
    fake = _DB()
    for name in ("get_data", "get_data_checked", "set_data"):
        monkeypatch.setattr(database_manager, name, getattr(fake, name))
    return fake


def fresh_vm():
    return feed.ActivityFeedViewManager()


def test_view_load_saved_document(db):
    db.data = {"tab": "previous", "page": 3, "filters": {"bits": False}, "search": "kept?"}
    vm = fresh_vm()
    snap = vm.load()
    assert snap["tab"] == "previous" and snap["page"] == 3 and snap["filters"]["bits"] is False
    assert snap["search"] == "" and snap["mode"] == "history"
    assert feed.activity_feed_state.current_tab == "previous"
    assert vm.load()["page"] == 3  # second load returns the cached view


@pytest.mark.parametrize("setup", ["empty_ok", "read_failed", "not_dict", "raises"])
def test_view_load_failures_keep_defaults(db, setup):
    if setup == "empty_ok":
        db.data = None
    elif setup == "read_failed":
        db.ok = False
    elif setup == "not_dict":
        db.data = ["x"]
    else:
        db.fail_get = True
    vm = fresh_vm()
    snap = vm.load()
    assert snap["tab"] == "current" and snap["page"] == 1
    assert vm._read_failed is (setup == "read_failed")


def test_view_save_and_merge_after_failed_read(db):
    vm = fresh_vm()
    db.ok = False
    vm.load()
    vm.update({"tab": "previous"})
    assert vm._unsaved_fields == {"tab", "page"} or "tab" in vm._unsaved_fields
    assert db.sets == []  # still unreadable: nothing saved over it
    db.ok = True
    db.data = {"filters": {"bits": False}, "condensed": False, "tab": "current", "page": 1}
    vm.update({"page": 2})
    assert db.sets[-1][1]["tab"] == "previous" and db.sets[-1][1]["filters"]["bits"] is False
    assert vm._read_failed is False


def test_view_save_errors(db, monkeypatch):
    vm = fresh_vm()
    vm._loaded = True
    vm._read_failed = True
    monkeypatch.setattr(vm, "_merge_onto_stored_view", lambda: 1 / 0)
    assert vm.save() is False

    def boom(path, payload):
        raise RuntimeError("x")

    monkeypatch.setattr(database_manager, "set_data", boom)
    vm._read_failed = False
    assert vm.save() is False


def test_view_update_merges_patches(isolated, monkeypatch):
    vm = isolated.vm
    assert vm.update(None)["revision"] == 0
    assert vm.update({"mode": "person"})["mode"] == "person"
    snap = vm.update({"mode": "history"})
    assert snap["tab"] == "previous" and snap["page"] == 1 and snap["revision"] == 2
    assert isolated.emitted[-1][0] == "activity_feed_view" and len(isolated.ui_calls) == 2
    vm.update({"condensed": "no"})
    vm.update({"tab": "nope"})
    vm.update({"page": "x"})
    monkeypatch.setattr(feed, "_max_history_pages", lambda: 3)
    assert vm.update({"page": 10})["page"] == 3
    assert vm.update({"search": " hi  there "})["search"] == "hi there"
    snap = vm.update({"filters": {"bits": False, "unknown": True}})
    assert snap["filters"]["bits"] is False and snap["filters"]["all"] is False
    snap = vm.update({"filters": {"bits": True, "subs": False}})
    assert snap["filters"]["subs"] is False
    assert vm._merge_patch("junk") == set()
    snap = vm.update({"search": "q"}, apply_desktop=False, broadcast=False)
    assert snap["search"] == "q"


def test_view_update_save_failure_notifies(isolated, monkeypatch):
    monkeypatch.setattr(isolated.vm, "save", lambda: False)
    isolated.vm.update({"tab": "previous"})
    assert isolated.notes[-1][1]["dedupe_key"] == "activity_feed:view_save_failed"


def test_view_update_restored_fields_repaint(isolated):
    isolated.vm._restored_fields = {"filters"}
    isolated.vm.update({"tab": "previous"})
    assert isolated.vm._restored_fields == set()


def test_apply_snapshot(isolated, monkeypatch):
    vm = isolated.vm
    assert vm.apply_snapshot("x") is False
    assert vm.apply_snapshot({"revision": "bad"}) is False
    assert vm.apply_snapshot({"revision": 0}) is False
    assert vm.apply_snapshot({"revision": 5, "tab": "previous", "page": 2}) is True
    assert vm.snapshot()["revision"] == 5 and feed.activity_feed_state.current_page == 2
    vm._read_failed = True
    monkeypatch.setattr(vm, "save", lambda: False)
    assert vm.apply_snapshot({"revision": 6}) is True
    assert vm._unsaved_fields == set(vm._SAVED_FIELDS)
    assert isolated.notes


def test_set_page_quietly(isolated):
    vm = isolated.vm
    assert vm.set_page_quietly(1) is False
    assert vm.set_page_quietly(0) is False
    assert vm.set_page_quietly(4) is True and vm.snapshot()["page"] == 4 and isolated.saves


def test_broadcast_view(isolated):
    feed.activity_feed_state.live_alerts = [{"type": "follow", "element": object()}]
    feed.broadcast_activity_feed_view({"revision": 2, "page": None, "condensed": True}, to="sid", include_live_alerts=True)
    event, payload, to = isolated.emitted[-1]
    assert event == "activity_feed_view" and to == "sid" and payload["page"] == 1 and payload["mode"] == "person"
    assert payload["live_alerts"] == [{"type": "follow"}]


_ORIGINALS = {name: getattr(feed, name) for name in ("_emit_overlay_event", "_run_on_ui_loop", "notify", "show_sub_duration")}


def test_emit_overlay_event(monkeypatch):
    emit = _ORIGINALS["_emit_overlay_event"]  # the autouse fixture replaces the module attribute
    sent = []
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    assert emit("e", {}) is False
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, p, to=None: sent.append((e, to)) or 1))
    assert emit("e", {"x": 1}, to="s") is True and sent == [("e", "s")]
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=lambda *a, **k: 1 / 0))
    assert emit("e", {}) is False


def test_notify_view_not_saved(monkeypatch, isolated):
    feed._notify_view_not_saved()
    msg, kw = isolated.notes[-1]
    assert kw["title"] == "Couldn't save your Activity Feed filters" and kw["type"] == "warning"


# ----------------------------------------------------------------- feed settings

def test_legacy_person_hours_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(feed, "_load_condensed_historical_hours", lambda: calls.append(1) or 6)
    assert feed._legacy_person_hours() == 6 and feed._legacy_person_hours() == 6 and calls == [1]
    monkeypatch.setattr(feed, "_legacy_hours_cache", None)
    monkeypatch.setattr(feed, "_load_condensed_historical_hours", lambda: 1 / 0)
    assert feed._legacy_person_hours() == 12


def test_current_feed_settings(monkeypatch):
    monkeypatch.setattr(feed, "_legacy_person_hours", lambda: 6)
    sm = SimpleNamespace(_initialized=True, get_app_settings=lambda: SimpleNamespace(
        activity_feed_layout="cards", activity_feed_limit="x", activity_feed_max_pages=3))
    monkeypatch.setattr(dataobjects, "state_manager", sm)
    s = feed.current_feed_settings()
    assert s["layout"] == "cards" and s["person_hours"] == 6
    assert (s["page_limit"], s["max_pages"]) == (20, 5)
    s = feed.current_feed_settings(SimpleNamespace(activity_feed_limit=10, activity_feed_max_pages=0, activity_feed_person_hours=24))
    assert (s["page_limit"], s["max_pages"], s["person_hours"]) == (10, 5, 24)
    sm._initialized = False
    assert feed.current_feed_settings()["layout"] == "rows"
    monkeypatch.setattr(dataobjects, "state_manager", None)
    assert feed.current_feed_settings()["layout"] == "rows"


def test_show_sub_duration(monkeypatch):
    original = _ORIGINALS["show_sub_duration"]
    assert original(SimpleNamespace(activity_feed_show_sub_duration=False)) is False
    sm = SimpleNamespace(_initialized=True, get_app_settings=lambda: SimpleNamespace(activity_feed_show_sub_duration=True))
    monkeypatch.setattr(dataobjects, "state_manager", sm)
    assert original() is True
    sm.get_app_settings = lambda: 1 / 0
    assert original() is True


def test_broadcast_and_apply_feed_settings(monkeypatch, isolated):
    slots, rebuilds = [], []
    monkeypatch.setattr(feed, "current_feed_settings", lambda app_settings=None: {"layout": "rows"})
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: slots.append(name))
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: rebuilds.append(reason))
    assert feed.broadcast_feed_settings(to="x") == {"layout": "rows"}
    assert isolated.emitted[-1] == ("activity_feed_settings", {"settings": {"layout": "rows"}}, "x")
    feed.apply_feed_settings_changed({"person_hours"})
    feed.apply_feed_settings_changed({"sub_duration"})
    feed.apply_feed_settings_changed({"layout"})
    feed.apply_feed_settings_changed()
    assert slots == ["settings"] * 4 and rebuilds == ["person_hours", "sub_duration"]


def test_set_feed_setting(monkeypatch):
    applied = []
    monkeypatch.setattr(feed, "apply_feed_settings_changed", lambda keys: applied.append(keys))
    sm = SimpleNamespace(updates=[], update_app_setting=lambda f, v: sm.updates.append((f, v)) or True,
                         save_changes=lambda: True)
    monkeypatch.setattr(dataobjects, "state_manager", sm)
    with pytest.raises(ValueError):
        feed.set_feed_setting("nope", 1)
    assert feed.set_feed_setting("layout", "CARDS") is True
    assert sm.updates == [("activity_feed_layout", "cards")] and applied == [{"layout"}]
    sm.update_app_setting = lambda f, v: False
    assert feed.set_feed_setting("layout", "rows") is False
    sm.update_app_setting = lambda f, v: 1 / 0
    assert feed.set_feed_setting("layout", "rows") is False


# ----------------------------------------------------------------- _run_on_ui_loop

def test_run_on_ui_loop_variants(monkeypatch):
    run = _ORIGINALS["_run_on_ui_loop"]
    scheduled = []
    monkeypatch.setattr(feed, "run_on_ui_loop", lambda fn: scheduled.append(fn))
    calls = []

    class _Client:
        is_deleted = False
        has_socket_connection = True

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *a):
            return False

    client = _Client()
    monkeypatch.setattr(feed, "_host", SimpleNamespace(host=SimpleNamespace(is_deleted=False, client=client)))
    run(lambda: calls.append("fn"))
    scheduled.pop()()
    assert calls == ["enter", "fn"]

    # no host: first connected client
    from nicegui import Client

    monkeypatch.setattr(feed, "_host", None)
    monkeypatch.setattr(Client, "instances", {"a": SimpleNamespace(is_deleted=True), "b": client})
    calls.clear()
    run(lambda: calls.append("fn"))
    scheduled.pop()()
    assert calls == ["enter", "fn"]

    # no client at all: runs bare; stale-client errors are swallowed, others logged
    monkeypatch.setattr(Client, "instances", {})
    run(lambda: calls.append("bare"))
    scheduled.pop()()
    assert calls[-1] == "bare"

    def stale():
        raise RuntimeError("client has been deleted")

    run(stale)
    scheduled.pop()()
    run(lambda: 1 / 0)
    scheduled.pop()()


def test_run_on_ui_loop_host_client_errors(monkeypatch):
    run = _ORIGINALS["_run_on_ui_loop"]
    scheduled = []
    monkeypatch.setattr(feed, "run_on_ui_loop", lambda fn: scheduled.append(fn))

    class _Host:
        is_deleted = False
        _n = 0

        @property
        def client(self):
            _Host._n += 1
            if _Host._n > 1:
                raise ValueError("gone")
            return object()

    monkeypatch.setattr(feed, "_host", SimpleNamespace(host=_Host()))
    from nicegui import Client

    class _BadInstances:
        def values(self):
            raise RuntimeError("x")

    monkeypatch.setattr(Client, "instances", _BadInstances())
    done = []
    run(lambda: done.append(1))
    scheduled.pop()()
    assert done == [1]


# ----------------------------------------------------------------- replay / skip

def test_alert_kind():
    assert feed._alert_kind({"type": "hype_train"}) == "hype train"
    assert feed._alert_kind(None) == "alert"


@pytest.mark.parametrize(
    "result, paused, engine, title",
    [
        ({"ok": False, "reason": "missing_id"}, False, None, "This alert can't be replayed"),
        ({"ok": False, "reason": "not_found"}, False, None, "This alert can't be replayed"),
        ({"ok": False}, False, None, "Couldn't replay this alert"),
        ({"ok": True, "reason": "queued"}, True, None, "Alerts are paused"),
        ({"ok": True}, False, object(), "Replaying the Bits alert"),
        ({"ok": True}, False, None, None),
    ],
)
def test_replay_alert(monkeypatch, isolated, result, paused, engine, title):
    monkeypatch.setattr(alertutils, "replay_alert", lambda alert_id: result)
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", paused)
    monkeypatch.setattr(web_engine, "web_engine_instance", engine)
    assert feed.replay_alert({"alert_id": "a", "type": "Bits"}) is result
    if title is None:
        assert isolated.notes == []
    else:
        assert isolated.notes[-1][1]["title"] == title
    if result.get("reason") == "not_found":
        assert isolated.notes[-1][1]["actions"]


def test_replay_alert_error(monkeypatch, isolated):
    monkeypatch.setattr(alertutils, "replay_alert", lambda alert_id: 1 / 0)
    assert feed.replay_alert(None) == {"ok": False, "reason": "error", "new_alert_id": None}
    assert isolated.notes[-1][1]["title"] == "Couldn't replay this alert"


@pytest.mark.parametrize(
    "data, result, title",
    [
        ({"type": "current"}, {"ok": True}, "Skipped the current alert"),
        ({"alert_id": "a", "type": "Bits"}, {"ok": True}, "Skipped the Bits alert"),
        ({"alert_id": "a"}, {"ok": True, "reason": "removed_from_queue"}, "Removed the alert from the queue"),
        ({"alert_id": "a", "type": "Raid"}, {"ok": True, "reason": "removed_from_queue"}, "Removed the Raid alert from the queue"),
        ({"alert_id": "a"}, {"ok": False, "reason": "not_playing"}, "That alert isn't playing"),
        (None, {"ok": False, "reason": "other"}, None),
    ],
)
def test_skip_alert(monkeypatch, isolated, data, result, title):
    seen = []
    monkeypatch.setattr(web_engine, "skip_current_alert", lambda alert_id, source="": seen.append(alert_id) or result)
    assert feed.skip_alert(data) is result
    assert seen == [None if (data or {}).get("type") == "current" or not data else data.get("alert_id")]
    if title:
        assert isolated.notes[-1][1]["title"] == title
    else:
        assert isolated.notes == []


def test_skip_alert_error(monkeypatch, isolated):
    monkeypatch.setattr(web_engine, "skip_current_alert", lambda *a, **k: 1 / 0)
    assert feed.skip_alert({})["reason"] == "error"
    assert isolated.notes[-1][1]["title"] == "Couldn't skip the alert"


@pytest.mark.parametrize("msg, user", [("Bob just followed!", "Bob"), ("Ann cheered 5", "Ann"), ("  ", "Unknown"), ("Solo", "Unknown")])
def test_extract_username(msg, user):
    assert feed.extract_username_from_message(msg) == user


def test_extract_username_error():
    assert feed.extract_username_from_message(None) == "Unknown"


def test_message_formatters():
    assert feed.format_watch_streak_message("", "x") == "Someone has watched for 0 consecutive streams!"
    assert feed.format_watch_streak_message("A", 3, include_username=False) == "Watched for 3 consecutive streams!"
    assert feed.format_modiversary_message("M", 1) == "M has been a moderator for 1 month!"
    assert feed.format_modiversary_message("", "x") == "Someone has been a moderator for 0 months!"
    assert feed.format_raid_activity_message("R", 1200) == "R raided with 1,200 viewers!"
    assert feed.format_raid_activity_message(None, "x", "Hades") == "Someone raided with 0 viewers, they were last playing Hades."
    assert feed._raid_category_suffix(None) == "!"


# ----------------------------------------------------------------- payload builders

class _Store:
    def __init__(self, data=None, exc=None):
        self.data, self.exc = data, exc

    def initialize(self):
        if self.exc:
            raise self.exc

    def get_stored_alert_by_id(self, alert_id):
        return self.data


def test_build_payload_basic(monkeypatch):
    p = feed.build_activity_feed_alert_payload(
        "Bits", "Bob cheered 100 bits!", "bits", timestamp="1700000000", tier=1, point_cost="1,000", level="x",
        gift_qty=None, recipient="  ", streak_count=None, raider_count="25", game_name="Hades", hype_train_type=" Golden ",
        amt_cheered=100, weird_extra="ignored", donation_amount=[1], duration_months="3")
    assert p["timestamp"] == 1700000000.0 and p["username"] == "Bob" and p["point_cost"] == 1000
    assert "level" not in p and p["raider_count"] == 25 and p["game_name"] == "Hades" and p["hype_train_type"] == "golden"
    assert p["amt_cheered"] == 100 and "weird_extra" not in p and "donation_amount" not in p and "recipient" not in p
    assert p["duration_months"] == 3 and p["row"]["kind"] == "bits"


def test_build_payload_with_stored_alert(monkeypatch):
    stored = {"username": "Stored", "streak_count": "7", "point_cost": "bad", "raider_count": 3, "game_name": "G",
              "tier": 2, "recipient": "R", "source": "twitch"}
    monkeypatch.setattr(alertutils, "alert_state_manager", _Store(stored))
    monkeypatch.setattr(alertutils, "resolve_chat_alert_media", lambda a, preview_placeholder=False: {"gif": "x.gif"})
    p = feed.build_activity_feed_alert_payload("Streak", "watched", "streak", alert_id="id1", recipient="Given")
    assert p["stored_alert_data"] is stored and p["username"] == "Stored" and p["streak_count"] == 7
    assert "point_cost" not in p and p["raider_count"] == 3 and p["game_name"] == "G" and p["tier"] == 2
    assert p["recipient"] == "Given" and p["chat_media"] == {"gif": "x.gif"} and p["alert_id"] == "id1"


def test_build_payload_stored_missing_or_error(monkeypatch):
    monkeypatch.setattr(alertutils, "alert_state_manager", _Store(None))
    p = feed.build_activity_feed_alert_payload("Follow", "Amy just followed!", alert_id="x", username="Given")
    assert "stored_alert_data" not in p and p["username"] == "Given"
    monkeypatch.setattr(alertutils, "alert_state_manager", _Store(exc=RuntimeError("db")))
    p = feed.build_activity_feed_alert_payload("Follow", "solo", alert_id="x")
    assert "username" not in p
    monkeypatch.setattr(alertutils, "alert_state_manager", _Store(["not", "dict"]))
    p = feed.build_activity_feed_alert_payload("Follow", "Amy just followed!", alert_id="x")
    assert p["stored_alert_data"] == ["not", "dict"] and p["username"] == "Amy"


@pytest.mark.parametrize(
    "kind, row_kind",
    [("follow", "follow"), ("sub", "sub"), ("resub", "resub"), ("bits", "bits"), ("bit", "bits"), ("giftsub", "giftsub"),
     ("raid", "raid"), ("donation", "donation"), ("points", "points"), ("streak", "streak"), ("hypetrain", "hype"),
     ("unknown", "follow"), (None, "follow")],
)
def test_typed_preview_payload(monkeypatch, kind, row_kind):
    p = feed.build_typed_activity_feed_preview_payload(kind)
    assert p["row"]["kind"] == row_kind
    assert p["username"] == p["stored_alert_data"]["username"]
    assert "chat_media" not in p


def test_typed_preview_payload_gift_variants_and_media(monkeypatch):
    import random

    monkeypatch.setattr(alertutils, "resolve_chat_alert_media", lambda a, preview_placeholder=False: {"video": "v.webm"})
    monkeypatch.setattr(random, "randint", lambda a, b: 1)
    p = feed.build_typed_activity_feed_preview_payload("giftsub")
    assert p["stored_alert_data"]["recipient"] and "to " in p["message"]
    assert p["chat_media"] == {"video": "v.webm"} and p["stored_alert_data"]["video"] == "v.webm"
    monkeypatch.setattr(random, "randint", lambda a, b: 5)
    p = feed.build_typed_activity_feed_preview_payload("giftsub")
    assert "recipient" not in p["stored_alert_data"] and "5 Tier" in p["message"]


@pytest.mark.parametrize("qty", [1, 7])
def test_iter_preview_payloads(monkeypatch, qty):
    import random

    monkeypatch.setattr(random, "randint", lambda a, b: qty if (a, b) == (1, 25) else a)
    payloads = list(feed.iter_activity_feed_preview_payloads())
    kinds = [p["row"]["kind"] for p in payloads]
    assert kinds == ["follow", "sub", "resub", "bits", "raid", "giftsub", "points", "streak", "hype", "hype"]
    gift = payloads[5]
    assert ("recipient" in gift) is (qty == 1)
    assert payloads[-1]["row"]["what"].startswith("Hype Train ended") or "ended" in payloads[-1]["message"]


def test_add_alert_to_feed(monkeypatch):
    seen, sent = [], []
    monkeypatch.setattr(feed.alert_event_handler, "process_alert_immediately", lambda a: seen.append(a))
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(activity_feed_alert=lambda a: sent.append(a)))
    feed.add_alert_to_feed("Follow", "Amy just followed!", "follow", raider_count=None)
    assert seen[0]["seq"] == seen[0]["row"]["seq"] == feed._live_seq
    assert sent[0]["type"] == "Follow" and "row" in sent[0]
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(activity_feed_alert=lambda a: 1 / 0))
    feed.add_alert_to_feed("Follow", "x")  # error logged
    monkeypatch.setattr(feed, "build_activity_feed_alert_payload", lambda *a, **k: {"row": None})
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    feed.add_alert_to_feed("Follow", "x")
    assert seen[-1]["seq"] == feed._live_seq


def test_update_alert_in_feed(monkeypatch, isolated):
    pushes, rebuilds = [], []
    monkeypatch.setattr(feed, "_schedule_live_push", lambda: pushes.append(1))
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: rebuilds.append(reason))
    assert feed.update_alert_in_feed(None, x=1) is False
    assert feed.update_alert_in_feed("a", x=None) is False
    assert feed.update_alert_in_feed("missing", duration_months=3) is False
    feed.activity_feed_state.live_alerts = [
        {"alert_id": "a", "type": "Giftsub", "badge_type": "giftsub", "username": "G", "stored_alert_data": {"tier": 1}},
        "junk",
    ]
    isolated.vm._view["condensed"] = True
    assert feed.update_alert_in_feed("a", duration_months=3, message="new") is True
    entry = feed.activity_feed_state.live_alerts[0]
    assert entry["stored_alert_data"] == {"tier": 1, "duration_months": 3} and entry["message"] == "new"
    assert entry["row"]["months"] == 3 and pushes == [1] and rebuilds == ["alert_updated"]
    assert isolated.emitted[-1][1]["live_alerts"]
    monkeypatch.setattr(feed, "broadcast_activity_feed_view", lambda *a, **k: 1 / 0)
    feed.activity_feed_state.live_alerts.append({"alert_id": "b"})
    assert feed.update_alert_in_feed("b", x=1) is True


def test_processor_lifecycle(monkeypatch):
    stopped, pushed = [], []
    monkeypatch.setattr(feed, "_stop_timers", lambda: stopped.append(1))
    monkeypatch.setattr(feed, "push_all_slots", lambda: pushed.append(1))
    feed.start_alert_processor()
    feed.stop_alert_processor()
    feed.force_ui_refresh()
    assert feed.alert_event_handler._should_stop and stopped == [1] and pushed == [1]


# ----------------------------------------------------------------- stored alerts

def conv(**stored):
    stored.setdefault("username", "U")
    stored.setdefault("timestamp", 1700000000)
    return feed.convert_stored_alert_to_feed_format(stored)


def test_convert_stored_variants(monkeypatch):
    assert feed.convert_stored_alert_to_feed_format("x") is None
    assert conv(alert_type="sub", source="youtube", member_level="Gold")["message"] == "U became a channel member (Gold)!"
    assert conv(alert_type="sub", source="youtube")["message"] == "U became a channel member!"
    r = conv(alert_type="sub", resub_month=6)
    assert r["type"] == "Resub" and r["badge_type"] == "resub"
    r = conv(alert_type="resub", months="x", total_months=4, source="youtube", member_level="L")
    assert r["message"] == "U reached a member milestone (4 months, L)!" and r["type"] == "Member Milestone"
    assert conv(alert_type="resub", source="youtube")["message"] == "U reached a member milestone (1 months)!"
    r = conv(alert_type="giftsub", source="youtube", gift_qty=3)
    assert r["message"] == "U gifted 3 memberships!" and r["badge_type"] == "gift_membership"
    r = conv(alert_type="giftsub", gift_qty=1, recipient=" R ", duration_months=3)
    assert r["recipient"] == "R" and r["gift_qty"] == 1 and r["duration_months"] == 3
    r = conv(alert_type="donation", source="youtube", display_amount="¥500")
    assert r["message"] == "U sent a Super Chat (¥500)!"
    r = conv(alert_type="donation", source="youtube", supersticker=True, donation_amount=2, currency="EUR")
    assert r["message"] == "U sent a Super Sticker (€2.00)!" and r["badge_type"] == "supersticker"
    assert conv(alert_type="donation", donation_text="5 bucks")["message"] == "U donated 5 bucks!"
    assert conv(alert_type="modiversary", months=2)["message"] == "U has been a moderator for 2 months!"
    r = conv(alert_type="hype_train_end", username="", level=3)
    assert r["message"] == "Hype Train ended at Level 3!" and r["username"] == "Unknown User"
    assert conv(alert_type="hype_train_start")["message"] == "Hype Train Level 1!"
    assert conv(alert_type="custom_thing")["message"] == "U triggered Custom Thing!"
    r = conv(alert_type="raid", raider_count="1,000", game_name="Hades")
    assert r["raider_count"] == 1000 and r["game_name"] == "Hades"
    assert conv(alert_type="raid")["raider_count"] == 0
    assert conv(alert_type=None, username="")["username"] == "Unknown User"


def test_convert_stored_basic_kinds():
    assert conv(alert_type="bit", amt_cheered="1,500")["message"] == "U cheered 1,500 bits!"
    assert conv(alert_type="sub", tier=2)["message"] == "U subscribed (Tier 2)!"
    assert conv(alert_type="resub", resub_month=7)["message"] == "U resubscribed for 7 months!"
    assert conv(alert_type="follow")["message"] == "U just followed!"
    r = conv(alert_type="point")
    assert r["message"] == "U redeemed 'Unknown Reward'!" and r["badge_type"] == "points"
    assert conv(alert_type="streak", streak_count=4)["message"] == "U has watched for 4 consecutive streams!"
    assert conv(alert_type="donation", donation_amount=5)["message"] == "U donated $5.00!"


def test_current_feed_settings_state_error(monkeypatch):
    class _SM:
        @property
        def _initialized(self):
            raise RuntimeError("x")

    monkeypatch.setattr(dataobjects, "state_manager", _SM())
    monkeypatch.setattr(feed, "_legacy_person_hours", lambda: None)
    assert feed.current_feed_settings()["person_hours"] == 12


def test_convert_stored_error(monkeypatch):
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: 1 / 0)
    assert feed.convert_stored_alert_to_feed_format({"alert_type": "sub"}) is None


class _Pager:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def initialize(self):
        pass

    def get_limited_stored_alerts_paginated(self, page, limit, max_total_alerts):
        self.calls.append((page, limit, max_total_alerts))
        return self.pages(page)


@pytest.fixture
def settings(monkeypatch):
    s = SimpleNamespace(activity_feed_limit=2, activity_feed_max_pages=3)
    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=lambda: s, _initialized=True))
    return s


def _stored(n, base=1700000000):
    return {"alert_id": f"a{n}", "alert_type": "follow", "username": f"U{n}", "timestamp": base + n}


def test_load_restored_alerts(monkeypatch, settings):
    pager = _Pager(lambda page: {"alerts": [_stored(1), "junk", _stored(2)], "total_pages": 2, "total_count": 3})
    monkeypatch.setattr(alertutils, "alert_state_manager", pager)
    alerts, info = feed.load_restored_alerts("x")
    assert info == {"page": 1, "total_pages": 2, "total_count": 3, "has_prev": False, "has_next": True, "limit": 2}
    assert [a["row"]["seq"] for a in alerts] == [3, 1]
    assert pager.calls == [(1, 2, 6)]
    # asked for page 3 but only 2 exist: reloads the last page
    alerts, info = feed.load_restored_alerts(3)
    assert info["page"] == 2 and pager.calls[-2:] == [(3, 2, 6), (2, 2, 6)]
    assert feed.activity_feed_state.total_pages == 2
    # seq <= 0 is left out
    pager.pages = lambda page: {"alerts": [_stored(1)], "total_pages": 1, "total_count": 0}
    alerts, _ = feed.load_restored_alerts(1)
    assert alerts[0]["row"]["seq"] is None


def test_load_restored_alerts_error(monkeypatch, settings, isolated):
    monkeypatch.setattr(alertutils, "alert_state_manager", _Pager(lambda page: 1 / 0))
    alerts, info = feed.load_restored_alerts(1)
    assert alerts == [] and info["error"]
    assert isolated.notes[-1][1]["dedupe_key"] == "activity_feed:history_failed"


def test_time_window_loaders(monkeypatch):
    now = time.time()

    class _Since:
        def initialize(self):
            pass

        def get_stored_alerts_since(self, cutoff, limit):
            return [_stored(1, now - 10), "junk", _stored(2, now - 5000), {"alert_type": "follow", "username": "N", "timestamp": now}]

    monkeypatch.setattr(alertutils, "alert_state_manager", _Since())
    alerts, n = feed.load_restored_alerts_for_time_window(now - 100)
    assert n == 2 and alerts[0]["username"] == "N"

    class _Fb:
        def initialize(self):
            pass

        def get_limited_stored_alerts_from_firebase(self, limit):
            return {"x": {"alert_type": "follow", "username": "F", "timestamp": now}, "y": "bad"}

    monkeypatch.setattr(alertutils, "alert_state_manager", _Fb())
    alerts, n = feed.load_restored_alerts_for_time_window(now - 100)
    assert n == 1 and alerts[0]["alert_id"] == "x"

    class _Broken:
        def initialize(self):
            raise RuntimeError("db")

    monkeypatch.setattr(alertutils, "alert_state_manager", _Broken())
    assert feed.load_restored_alerts_for_time_window(0) == ([], 0)


def test_alert_timestamp_for_cutoff(monkeypatch):
    monkeypatch.setattr(feed.time, "time", lambda: 50.0)
    assert feed._alert_timestamp_for_cutoff({"created_at": "", "timestamp": "now"}) == 50.0
    assert feed._alert_timestamp_for_cutoff({"created_at": "bad", "timestamp": "7"}) == 7.0
    assert feed._alert_timestamp_for_cutoff({}) == 0.0


def test_parse_hours_and_normalize_username():
    assert feed.parse_condensed_historical_hours("6.5") == 6
    assert feed.parse_condensed_historical_hours(0) == 12 and feed.parse_condensed_historical_hours("x", 3) == 3
    assert feed._normalize_condensed_alert_username({"message": "Ann followed"})["username"] == "Ann"
    assert "username" not in feed._normalize_condensed_alert_username({"message": ""})


def test_collect_alerts_for_condensed_view(monkeypatch):
    now = time.time()
    stored = [{"alert_id": "a", "message": "S1 x", "timestamp": now - 5}, {"message": "S2 x", "timestamp": now - 6}]
    monkeypatch.setattr(feed, "load_restored_alerts_for_time_window", lambda cutoff: (stored, 2))
    feed.activity_feed_state.live_alerts = [
        {"alert_id": "a", "username": "Live", "timestamp": now - 1},
        {"message": "L2 x", "timestamp": now - 2},
        {"username": "Old", "timestamp": now - 99999},
    ]
    alerts, hist, live = feed.collect_alerts_for_condensed_view(now - 100)
    assert (hist, live) == (2, 2)
    assert [a.get("username") for a in alerts] == ["Live", "L2", "S2"]


def test_load_condensed_historical_hours(monkeypatch):
    from modules import template_config_parser as tcp

    class _P:
        cfg = None

        def load_config(self, name):
            return _P.cfg

    monkeypatch.setattr(tcp, "TemplateConfigParser", _P)
    _P.cfg = {"elements": [{"type": "section", "id": "condense_historical_hours"},
                           {"id": "other"}, {"id": "condense_historical_hours", "value": "24"}]}
    assert feed._load_condensed_historical_hours() == 24
    _P.cfg = None
    assert feed._load_condensed_historical_hours() == 12

    class _Bad:
        def load_config(self, name):
            raise RuntimeError("x")

    monkeypatch.setattr(tcp, "TemplateConfigParser", _Bad)
    assert feed._load_condensed_historical_hours() == 12


def test_person_hours(monkeypatch):
    monkeypatch.setattr(feed, "current_feed_settings", lambda app_settings=None: {"person_hours": 48})
    assert feed._person_hours() == 48


# ----------------------------------------------------------------- condensed grouping

TYPES = feed.ActivityFeedState().alert_type_to_filter
ALL = feed.default_filter_state()


def acc(alerts, filters=None, search="", show_duration=True):
    groups = {}
    results = [feed._accumulate_condensed_alert(a, groups, filters or ALL, TYPES, search, show_duration=show_duration)
               for a in alerts]
    return groups, results


def test_accumulate_exclusions():
    groups, res = acc([{"type": "Hype Train"}, {"type": "x", "badge_type": "hype_train"}])
    assert res == ["excluded", "excluded"]
    filters = dict(ALL, all=False, bits=False)
    _, res = acc([{"type": "Bits", "username": "A"}, {"type": "Follow", "username": "A"}], filters)
    assert res == ["excluded", "ok"]
    _, res = acc([{"type": "Follow", "username": "A"}], search="zzz")
    assert res == ["excluded"]
    _, res = acc([{"type": "Follow", "message": ""}])
    assert res == ["unknown"]


def test_accumulate_search_from_state():
    feed.activity_feed_state.search_text = "ann"
    groups = {}
    assert feed._accumulate_condensed_alert({"type": "Follow", "username": "Ann"}, groups, ALL, TYPES) == "ok"
    assert feed._accumulate_condensed_alert({"type": "Follow", "username": "Bob"}, groups, ALL, TYPES) == "excluded"


def test_accumulate_subs_and_gifts():
    groups, _ = acc([
        {"type": "Sub", "username": "A", "tier": 2, "duration_months": 3},
        {"type": "Resub", "username": "A", "tier": 1, "stored_alert_data": {"resub_month": 5}},
        {"type": "Resub", "username": "A", "tier": 1, "message": "A resubscribed for 8 months"},
        {"type": "Resub", "username": "A", "tier": 1, "message": "A resubscribed"},
        {"type": "Giftsub", "username": "A", "tier": 1, "stored_alert_data": {"gift_qty": 5}},
        {"type": "Giftsub", "username": "A", "tier": 1, "gift_qty": "1,000"},
        {"type": "Giftsub", "username": "A", "tier": 1, "message": "A gifted 3 subs"},
        {"type": "Giftsub", "username": "A", "tier": 3, "message": "A gifted"},
    ])
    g = groups["A"]
    assert "sub_tier2_dur3" in g
    assert g["resub_tier1_multi_month"]["months"] == 8 and g["resub_tier1_multi_month"]["count"] == 2
    assert g["resub_tier1_1month"]["months"] == 1
    assert g["giftsub_tier1_1month"]["total_amount"] == 1008
    assert g["giftsub_tier3_1month"]["total_amount"] == 0


def test_accumulate_youtube_points_and_amounts():
    groups, _ = acc([
        {"type": "Membership", "username": "Y"},
        {"type": "Member Milestone", "username": "Y"},
        {"type": "gift membership", "username": "Y", "gift_qty": 2},
        {"type": "Super Chat", "username": "Y", "badge_type": "superchat", "donation_amount": 5},
        {"type": "super sticker", "username": "Y", "message": "Y donated $1,000.50"},
        {"type": "Points", "username": "Y", "stored_alert_data": {"alert_name": "Hydrate"}},
        {"type": "Points", "username": "Y", "message": "Y redeemed 'Dance'!"},
        {"type": "x", "badge_type": "points", "username": "Y", "message": "nothing"},
        {"type": "Bits", "username": "Y", "stored_alert_data": {"amt_cheered": "1,500"}},
        {"type": "bits", "username": "Y", "message": "Y cheered 100 bits"},
        {"type": "Donation", "username": "Y", "stored_alert_data": {"donation_amount": 2.5}},
        {"type": "Donation", "username": "Y", "message": "Y donated $3"},
        {"type": "Donation", "username": "Y", "message": "Y said hi"},
    ])
    g = groups["Y"]
    assert {"membership", "member_milestone", "gift_membership", "superchat", "supersticker"} <= set(g)
    assert g["gift_membership"]["total_amount"] == 2
    assert g["superchat"]["total_amount"] == 5.0 and g["supersticker"]["total_amount"] == 1000.5
    assert g["points:Hydrate"]["reward_name"] == "Hydrate" and "points:Dance" in g and "points:Unknown Reward" in g
    assert g["bits"]["total_amount"] == 1600 and g["donation"]["total_amount"] == 5.5


def test_accumulate_raid_streak_and_errors():
    groups, _ = acc([
        {"type": "Raid", "username": "R", "raider_count": 10, "game_name": "A"},
        {"type": "Raid", "username": "R", "stored_alert_data": {"raider_count": "200", "game_name": "B"}},
        {"type": "Raid", "username": "R", "message": "R raided with 50 viewers"},
        {"type": "Raid", "username": "R", "message": "R raided"},
        {"type": "Streak", "username": "R", "streak_count": 3},
        {"type": "Streak", "username": "R", "stored_alert_data": {"streak_count": 9}},
        {"type": "Streak", "username": "R", "message": "R watched 4 consecutive streams"},
        {"type": "Streak", "username": "R", "message": "no number"},
    ])
    assert groups["R"]["raid"]["total_amount"] == 200 and groups["R"]["raid"]["game_name"] == "B"
    assert groups["R"]["streak"]["total_amount"] == 9 and groups["R"]["streak"]["count"] == 4


def test_group_alerts_for_condensed(monkeypatch):
    feed.activity_feed_state.filter_state = dict(ALL)
    alerts = ["junk", {"type": "Follow", "username": "A"}, {"type": "Hype Train"}, {"type": "Follow", "message": ""},
              {"type": "Bits", "username": "B", "stored_alert_data": {"amt_cheered": "abc"}}]
    groups, excluded, unknown = feed._group_alerts_for_condensed(alerts)
    assert set(groups) == {"A", "B"} and excluded == 3 and unknown == 1


def test_person_kind_and_line():
    cases = [
        ("sub_tier1", {}, "sub"), ("resub_tier2_1month", {}, "resub"), ("giftsub_tier1_x", {}, "giftsub"),
        ("member_milestone", {}, "milestone"), ("gift_membership", {}, "giftmember"), ("membership", {}, "member"),
        ("superchat", {}, "superchat"), ("supersticker", {}, "supersticker"), ("points:x", {}, "points"),
        ("whatever", {"original_type": "point"}, "points"), ("bit", {}, "bits"), ("modiversary", {}, "modiversary"),
        ("zzz", {}, "alert"),
    ]
    for key, data, kind in cases:
        assert feed._person_kind(key, data) == kind


@pytest.mark.parametrize(
    "key, data, text, extra",
    [
        ("sub_tier2_dur3", {"tier": 2, "duration_months": 3}, "Subscribed (Tier 2) · 3 months in advance", {"tier": 2}),
        ("resub_tier1_multi_month", {"months": 6}, "Resubscribed for 6 months (Tier 1)", {"max": 6}),
        ("resub_tier1_1month", {}, "Resubscribed (Tier 1)", {}),
        ("giftsub_tier1", {"total_amount": 5, "count": 2, "duration_months": 3}, "Gifted 5 Tier 1 subs · 3 months each", {"count": 1, "amount": 5.0}),
        ("giftsub_tier1", {"total_amount": 0, "count": 1}, "Gifted 1 Tier 1 sub", {"count": 1}),
        ("follow", {}, "Followed", {}),
        ("bits", {"total_amount": 1500}, "Cheered 1,500 bits", {"amount": 1500.0}),
        ("donation", {"total_amount": 5}, "Tipped $5.00", {}),
        ("superchat", {"total_amount": 2}, "Sent a Super Chat ($2.00)", {}),
        ("supersticker", {}, "Sent a Super Sticker", {}),
        ("membership", {}, "Became a member", {}),
        ("member_milestone", {}, "Reached a member milestone", {}),
        ("gift_membership", {"count": 2}, "Gifted 2 memberships", {"count": 2}),
        ("raid", {"total_amount": 1200, "game_name": "Hades"}, "Raided with 1,200 viewers · Hades", {"max": 1200}),
        ("streak", {"total_amount": 4}, "Watched 4 streams in a row", {"max": 4}),
        ("streak", {}, "Watch streak", {}),
        ("points:Hydrate", {"reward_name": ""}, "Redeemed “Hydrate”", {}),
        ("points:", {}, "Redeemed “a reward”", {}),
        ("modiversary", {}, "Modiversary", {}),
    ],
)
def test_person_line(key, data, text, extra):
    line = feed.person_line(key, data)
    assert line["text"] == text
    for k, v in extra.items():
        assert line[k] == v


def test_serialize_condensed_groups(monkeypatch):
    user_alerts = {
        "A": {"follow": {"count": 2, "total_amount": 0, "tier": 1, "months": 0, "last_ts": 5}},
        "Empty": {},
        "Bad": {"follow": {"count": 1}},  # missing keys -> aggregated text falls back, still listed
    }
    groups = feed.serialize_condensed_groups(user_alerts)
    assert groups[0]["username"] == "A" and groups[0]["events"] == 2 and groups[0]["last_ts"] == 5.0
    assert groups[0]["lines"] == ["Followed 2 times!"] and groups[0]["totals"][0]["text"] == "Follow"
    assert [g["username"] for g in groups] == ["A", "Bad"] and groups[1]["last_ts"] is None
    monkeypatch.setattr(feed, "person_line", lambda k, d: 1 / 0)
    assert feed.serialize_condensed_groups({"A": {"follow": {}}}) == []
    monkeypatch.setattr(feed, "MAX_CONDENSED_GROUPS", 1)
    monkeypatch.setattr(feed, "person_line", lambda k, d: {"kind": "follow"})
    assert len(feed.serialize_condensed_groups({"A": {"follow": {"count": 1, "total_amount": 0, "tier": 1, "months": 0}},
                                                "B": {"follow": {"count": 1, "total_amount": 0, "tier": 1, "months": 0}}})) == 1


def test_condensed_view_model_and_truncation(monkeypatch):
    assert feed._condensed_truncation(5) is None
    monkeypatch.setattr(feed, "MAX_CONDENSED_GROUPS", 2)
    assert feed._condensed_truncation(3) == "Showing first 2 of 3 people"
    empty = feed._condensed_view_model({"condense_historical_hours": 6})
    assert empty["empty"] == "No alerts to condense in the last 6 hours" and empty["groups"] == []
    vm = feed._condensed_view_model({"groups": [1, 2, 3], "alerts_to_process": [1], "live_in_window": 1, "user_total": 3})
    assert vm["header"] == "Showing 1 alerts from past 12 hours" and vm["groups"] == [1, 2] and vm["truncation"]
    vm = feed._condensed_view_model({"user_alerts": {"A": {"follow": {"count": 1, "total_amount": 0, "tier": 1, "months": 0}}},
                                     "alert_count": 1})
    assert vm["header"] is None and len(vm["groups"]) == 1


def test_build_condensed_overlay_payload(monkeypatch):
    now = time.time()
    monkeypatch.setattr(feed, "_person_hours", lambda: 6)
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view",
                        lambda cutoff: ([{"type": "Follow", "username": "A", "timestamp": now}], 1, 0))
    feed.activity_feed_state.search_text = ""
    p = feed.build_condensed_overlay_payload()
    assert p["hours"] == 6 and p["user_count"] == 1 and p["groups"][0]["username"] == "A" and p["search"] == ""
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view", lambda cutoff: 1 / 0)
    p = feed.build_condensed_overlay_payload(hours=3, filter_state=ALL, search="x")
    assert p["groups"] == [] and p["hours"] == 3 and p["search"] == "x"


def test_condensed_payload_from_build_and_broadcast(isolated):
    p = feed.condensed_payload_from_build({"groups": [1], "condense_historical_hours": 4, "search": None})
    assert p["user_count"] == 1 and p["hours"] == 4 and p["search"] == ""
    feed._broadcast_condensed_groups({"groups": []}, to="s")
    assert isolated.emitted[-1][0] == "condensed_view_alerts" and isolated.emitted[-1][2] == "s"


def test_collect_condensed_build_data(monkeypatch):
    monkeypatch.setattr(feed, "_person_hours", lambda: 2)
    ticks = iter([0.0, 5.0])
    _local_clock(feed, monkeypatch, monotonic=lambda: next(ticks))
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view", lambda cutoff: ([{"type": "Follow", "username": "A"}], 1, 1))
    d = feed._collect_condensed_build_data()
    assert d["condense_historical_hours"] == 2 and d["user_total"] == 1 and d["groups"]
    ticks2 = iter([0.0, 0.1])
    _local_clock(feed, monkeypatch, monotonic=lambda: next(ticks2))
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view", lambda cutoff: 1 / 0)
    assert feed._collect_condensed_build_data()["alert_count"] == 0


@pytest.mark.parametrize(
    "key, data, text",
    [
        ("sub_tier1", {"count": 2}, "Subscribed 2 times (Tier 1)!"),
        ("sub_tier1", {"count": 1}, "Subscribed (Tier 1)!"),
        ("resub_tier1_1month", {"count": 2}, "Resubscribed 2 times for 1 month (Tier 1)!"),
        ("resub_tier1_1month", {"count": 1}, "Resubscribed for 1 month (Tier 1)!"),
        ("resub_tier1_multi_month", {"count": 2, "months": 6}, "Resubscribed 2 times for 6 months (Tier 1)!"),
        ("resub_tier1_multi_month", {"count": 1, "months": 6}, "Resubscribed for 6 months (Tier 1)!"),
        ("giftsub_tier1_1month_dur3", {"total_amount": 1500, "duration_months": 3}, "Gifted 1,500 Tier 1 subs (3 months)!"),
        ("giftsub_tier1_1month_dur3", {"total_amount": "x", "duration_months": 3}, "Gifted x Tier 1 subs (3 months)!"),
        ("giftsub_tier1_1month", {"total_amount": 2}, "Gifted 2 Tier 1 subs (1 month)!"),
        ("giftsub_tier1_1month", {"total_amount": "x"}, "Gifted x Tier 1 subs (1 month)!"),
        ("giftsub_tier1_multi_month", {"total_amount": 2, "months": 3}, "Gifted 2 Tier 1 subs (3 months)!"),
        ("giftsub_tier1_multi_month", {"total_amount": "y", "months": 3}, "Gifted y Tier 1 subs (3 months)!"),
        ("follow", {"count": 3}, "Followed 3 times!"),
        ("follow", {}, "Followed!"),
        ("bits", {"total_amount": 1000}, "Gave 1,000 bits!"),
        ("bits", {"total_amount": "z"}, "Gave z bits!"),
        ("donation", {"total_amount": 5.0}, "Donated $5!"),
        ("donation", {"total_amount": 5.5}, "Donated $5.50!"),
        ("membership", {"count": 2}, "Joined as member 2 times!"),
        ("member", {}, "Became a channel member!"),
        ("member_milestone", {"count": 2}, "Reached member milestones 2 times!"),
        ("member milestone", {}, "Reached a member milestone!"),
        ("gift_membership", {"total_amount": 3}, "Gifted 3 memberships!"),
        ("gift membership", {"total_amount": "q", "count": 2}, "Gifted q memberships!"),
        ("superchat", {"total_amount": 5.0}, "Sent Super Chat (5)!"),
        ("super chat", {"total_amount": 5.25}, "Sent Super Chat (5.25)!"),
        ("supersticker", {"total_amount": 2.0}, "Sent Super Sticker (2)!"),
        ("super sticker", {"total_amount": 2.5}, "Sent Super Sticker (2.50)!"),
        ("raid", {"total_amount": 1200, "count": 2, "game_name": "Hades"}, "Raided 2 times (largest: 1,200 viewers), they were last playing Hades."),
        ("raid", {"total_amount": "w"}, "Raided with w viewers!"),
        ("streak", {"total_amount": 0}, "Watch streak!"),
        ("streak", {"total_amount": 4}, "Watched for 4 consecutive streams!"),
        ("points:Hydrate", {"count": 2}, "Redeemed 'Hydrate' 2 times!"),
        ("x", {"original_type": "point"}, "Redeemed 'Unknown Reward'!"),
        ("custom_kind", {"count": 2}, "Custom Kind 2 times!"),
        ("custom_kind", {}, "Custom Kind!"),
    ],
)
def test_create_aggregated_alert_text(key, data, text):
    base = {"count": 1, "total_amount": 0, "tier": 1, "months": 0}
    base.update(data)
    assert feed.create_aggregated_alert_text(key, base) == text


def test_create_aggregated_alert_text_error():
    assert feed.create_aggregated_alert_text("follow", {}) == "Follow!"


# ----------------------------------------------------------------- history / person flows

def test_emit_stored_alerts_payload(isolated):
    feed._emit_stored_alerts_payload([{"type": "follow", "element": object()}], {"page": 2, "error": "x"}, to="s")
    event, payload, to = isolated.emitted[-1]
    assert event == "stored_alerts_paginated" and payload["success"] is False and payload["error"] == "x"
    assert payload["alerts"] == [{"type": "follow"}] and payload["page"] == 2 and to == "s"


def test_emit_stored_alerts_page(monkeypatch, isolated):
    monkeypatch.setattr(feed, "load_restored_alerts", lambda page: ([], {"page": page}))
    feed.emit_stored_alerts_page(3, to="x")
    assert isolated.emitted[-1][1]["page"] == 3
    monkeypatch.setattr(feed, "load_restored_alerts", lambda page: 1 / 0)
    feed.emit_stored_alerts_page(1)


def test_apply_history_page(monkeypatch, isolated):
    slots = []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: slots.append((name, value)))
    feed._apply_history_page([{"type": "follow", "username": "A"}], {"page": 1, "total_pages": 2, "has_next": True})
    assert feed._history_payload["rows"][0]["kind"] == "follow" and feed._history_payload["loading"] is False
    assert [s[0] for s in slots] == ["history"]
    isolated.vm._view["page"] = 5
    feed._apply_history_page([], {"page": 2})
    assert isolated.vm.snapshot()["page"] == 2 and [s[0] for s in slots][-2:] == ["history", "view"]


def test_refresh_history_inline_and_async(monkeypatch, isolated):
    applied = []
    monkeypatch.setattr(feed, "load_restored_alerts", lambda page: ([], {"page": page}))
    monkeypatch.setattr(feed, "_apply_history_page", lambda a, i: applied.append(i))

    def no_loop(coro, name=""):
        coro.close()
        raise RuntimeError("no loop")

    monkeypatch.setattr(feed.background_tasks, "create", no_loop)
    feed.refresh_restored_alerts()
    assert applied == [{"page": 1}]
    monkeypatch.setattr(feed, "_history_task_running", True)
    feed.refresh_restored_alerts()
    assert feed._history_rerun is True
    monkeypatch.setattr(feed, "_history_task_running", False)

    async def io_bound(fn, *a):
        return fn(*a)

    monkeypatch.setattr(feed.run, "io_bound", io_bound)
    isolated.vm._view["tab"] = "previous"
    runs = {"n": 0}

    def load(page):
        runs["n"] += 1
        if runs["n"] == 1:
            feed._history_rerun = True  # a change arrived while loading
        return [], {"page": page}

    monkeypatch.setattr(feed, "load_restored_alerts", load)
    asyncio.run(feed._refresh_history_async())
    assert runs["n"] == 2 and feed._history_task_running is False
    monkeypatch.setattr(feed, "load_restored_alerts", lambda page: 1 / 0)
    asyncio.run(feed._refresh_history_async())
    assert feed._history_task_running is False


def test_schedule_condensed_view_update(monkeypatch, isolated):
    scheduled, rebuilds = [], []
    monkeypatch.setattr(feed, "app_schedule", lambda delay, fn, once=False: scheduled.append(fn))
    monkeypatch.setattr(feed, "_schedule_condensed_rebuild", lambda reason: rebuilds.append(reason))
    feed.schedule_condensed_view_update("x")  # not in person mode
    assert scheduled == []
    isolated.vm._view["condensed"] = True
    feed.schedule_condensed_view_update("a")
    feed.schedule_condensed_view_update("b")  # coalesced
    assert len(scheduled) == 1
    scheduled[0]()
    assert rebuilds == ["a"] and feed._condensed_update_scheduled is False

    def boom(*a, **k):
        raise RuntimeError("no app")

    monkeypatch.setattr(feed, "app_schedule", boom)
    feed.schedule_condensed_view_update("c")
    assert feed._condensed_update_scheduled is False


def test_schedule_condensed_rebuild(monkeypatch):
    created = []

    def create(coro, name=""):
        created.append(name)
        coro.close()

    monkeypatch.setattr(feed.background_tasks, "create", create)
    feed._schedule_condensed_rebuild("r")
    assert created == ["activity_feed_condensed_rebuild"]
    monkeypatch.setattr(feed, "_condensed_rebuild_running", True)
    feed._schedule_condensed_rebuild("r")
    assert feed._condensed_rebuild_rerun is True and len(created) == 1
    monkeypatch.setattr(feed, "_condensed_rebuild_running", False)

    def boom(coro, name=""):
        coro.close()
        raise RuntimeError("x")

    monkeypatch.setattr(feed.background_tasks, "create", boom)
    feed._schedule_condensed_rebuild("r")


def test_update_condensed_view_async(monkeypatch, isolated):
    slots = []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: slots.append((name, value)))

    async def io_bound(fn, *a):
        return fn(*a)

    monkeypatch.setattr(feed.run, "io_bound", io_bound)
    asyncio.run(feed._update_condensed_view_async("x"))  # not person mode: returns
    assert slots == []
    isolated.vm._view["condensed"] = True
    n = {"i": 0}

    def build():
        n["i"] += 1
        if n["i"] == 1:
            feed._condensed_rebuild_rerun = True
        return {"groups": [{"username": "A"}]}

    monkeypatch.setattr(feed, "_collect_condensed_build_data", build)
    asyncio.run(feed._update_condensed_view_async("x"))
    assert n["i"] == 2 and slots[-1][0] == "person" and feed._person_payload["groups"] == [{"username": "A"}]
    assert isolated.emitted[-1][0] == "condensed_view_alerts"
    monkeypatch.setattr(feed, "_collect_condensed_build_data", lambda: 1 / 0)
    asyncio.run(feed._update_condensed_view_async("x"))
    assert slots[-1] == ("person", {"success": False, "error": "division by zero", "groups": []})
    assert feed._condensed_rebuild_running is False


def test_update_and_refresh_condensed_view(monkeypatch, isolated):
    rebuilds = []
    monkeypatch.setattr(feed, "_schedule_condensed_rebuild", lambda reason: rebuilds.append(reason))
    assert feed.update_condensed_view() is True and rebuilds == []
    isolated.vm._view["condensed"] = True
    feed.refresh_condensed_view()
    assert rebuilds == ["update_condensed_view"]


@pytest.mark.parametrize("mode", ["live", "history", "person"])
def test_publish_activity_feed_view(monkeypatch, isolated, mode):
    isolated.vm._view.update(feed.mode_patch(mode))
    pages = []
    monkeypatch.setattr(feed, "current_feed_settings", lambda app_settings=None: {"layout": "rows"})
    monkeypatch.setattr(feed, "emit_stored_alerts_page", lambda page, to=None: pages.append((page, to)))
    monkeypatch.setattr(feed, "build_condensed_overlay_payload", lambda filter_state=None, search=None: {"groups": []})
    snap = feed.publish_activity_feed_view(to="sid")
    events = [e for e, _, _ in isolated.emitted]
    assert events[:2] == ["activity_feed_settings", "activity_feed_view"] and snap["mode"] == mode
    if mode == "history":
        assert pages == [(1, "sid")]
    if mode == "person":
        assert events[-1] == "condensed_view_alerts"


def test_apply_view_to_desktop(monkeypatch, isolated):
    slots, refreshed, rebuilds = [], [], []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: slots.append(name))
    monkeypatch.setattr(feed, "refresh_restored_alerts", lambda: refreshed.append(1))
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: rebuilds.append(reason))
    feed._apply_view_to_desktop({"filters"})
    assert slots == ["view"]
    isolated.vm._view.update(feed.mode_patch("history"))
    feed._apply_view_to_desktop({"page"})
    feed._apply_view_to_desktop({"search"})
    assert slots[1:] == ["view", "history", "view"] and refreshed == [1]
    isolated.vm._view.update(feed.mode_patch("person"))
    feed._apply_view_to_desktop({"condensed"})
    feed._apply_view_to_desktop({"search"})
    feed._apply_view_to_desktop({"page"})
    assert rebuilds == ["view_change", "view_change"] and slots.count("person") == 1


def test_checkbox_tab_and_page_helpers(monkeypatch, isolated):
    feed.on_checkbox_change("bits", 0)
    assert isolated.vm.snapshot()["filters"]["bits"] is False
    feed.switch_to_tab("nope")
    feed.switch_to_tab("previous")
    assert isolated.vm.snapshot()["tab"] == "previous"
    feed.switch_to_tab("current")
    feed.activity_feed_state.total_pages = 4
    monkeypatch.setattr(feed, "_max_history_pages", lambda: 3)
    feed.go_to_page("x")
    feed.go_to_page(9)
    assert isolated.vm.snapshot()["page"] == 3
    feed.go_to_page(-1)
    assert isolated.vm.snapshot()["page"] == 1

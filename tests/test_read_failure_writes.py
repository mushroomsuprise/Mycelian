"""Read-then-write paths never save defaults over data whose read failed.

Each case runs twice against a fake backend: a healthy read (the write must be
exactly what it was before), and a failed read (no blind overwrite; merged or
skipped instead).
"""

from __future__ import annotations

import copy

import pytest

from modules import database_manager as dbm


class FakeBackend:
    def __init__(self, store=None):
        self.store = copy.deepcopy(store or {})
        self.fail_reads = False
        self.writes = []

    def get_data_checked(self, path):
        if self.fail_reads:
            return {}, False
        return copy.deepcopy(self.store.get(path, {})), True

    def get_data(self, path):
        return self.get_data_checked(path)[0]

    def set_data(self, path, data):
        self.writes.append((path, copy.deepcopy(data)))
        self.store[path] = copy.deepcopy(data)
        return True


@pytest.fixture()
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(dbm.database_manager, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(dbm.database_manager, "get_data", fake.get_data)
    monkeypatch.setattr(dbm.database_manager, "set_data", fake.set_data)
    monkeypatch.setattr(dbm, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(dbm, "get_data", fake.get_data)
    monkeypatch.setattr(dbm, "set_data", fake.set_data)
    return fake


# --- chatbot custom variables ---------------------------------------------------


def _chatbot(monkeypatch, backend):
    # Custom variable storage lives in chatbot_custom_variables (reads go through database_manager).
    from modules import chatbot_custom_variables as chatbot

    monkeypatch.setattr(chatbot.time, "time", lambda: 1000.0)
    chatbot.invalidate_custom_variable_cache()
    return chatbot


def test_custom_variables_success_path_unchanged(monkeypatch, backend):
    chatbot = _chatbot(monkeypatch, backend)
    backend.store[chatbot.CUSTOM_VARIABLES_PATH] = {"data": {"custom_a": "A"}}
    assert chatbot.add_custom_variable("b", "1+1")
    assert backend.writes[-1] == (
        chatbot.CUSTOM_VARIABLES_PATH,
        {
            "data": {"custom_a": "A", "custom_b": "{custom_b} - Custom: 1+1"},
            "last_updated": 1000.0,
            "version": "1.0",
        },
    )
    assert chatbot.remove_custom_variable("custom_a")
    assert backend.writes[-1][1]["data"] == {"custom_b": "{custom_b} - Custom: 1+1"}


def test_custom_variables_failed_read_does_not_overwrite(monkeypatch, backend):
    chatbot = _chatbot(monkeypatch, backend)
    backend.store[chatbot.CUSTOM_VARIABLES_PATH] = {"data": {"custom_a": "A"}}
    backend.fail_reads = True
    assert chatbot.add_custom_variable("b", "1") is False
    assert chatbot.remove_custom_variable("custom_a") is False
    assert backend.writes == []
    assert backend.store[chatbot.CUSTOM_VARIABLES_PATH] == {"data": {"custom_a": "A"}}


# --- FF7 boss log and connector overrides ---------------------------------------


def _ff7_hook():
    from modules.game_hooks import ff7_hook
    from modules.game_hooks.ff7_boss_tracker import Ff7BossTracker

    hook = object.__new__(ff7_hook.Ff7GameHook)
    hook._boss_tracker = Ff7BossTracker(frozenset({"boss"}), frozenset())
    hook._pending_override_rows = {}
    hook._enqueue_write = None
    return ff7_hook, hook


def test_boss_log_success_path_unchanged(backend):
    ff7, hook = _ff7_hook()
    backend.store[ff7._FF7_BOSS_LOG_PATH] = {"names": ["Old Boss"], "last": "Old Boss"}
    hook._load_boss_log()
    hook._boss_tracker.restore(["New Boss", "Old Boss"], "New Boss")
    hook._persist_boss_log()
    assert backend.writes == [
        (ff7._FF7_BOSS_LOG_PATH, {"names": ["New Boss", "Old Boss"], "last": "New Boss", "count": 2})
    ]


def test_boss_log_failed_load_merges_before_saving(backend):
    ff7, hook = _ff7_hook()
    backend.store[ff7._FF7_BOSS_LOG_PATH] = {"names": ["B2", "B1"], "last": "B2"}
    backend.fail_reads = True
    hook._load_boss_log()
    hook._boss_tracker.restore(["Fresh"], "Fresh")  # a defeat this session
    hook._persist_boss_log()
    assert backend.writes == []  # still unreadable: nothing written over the log
    backend.fail_reads = False
    hook._persist_boss_log()
    assert backend.writes[-1][1]["names"] == ["Fresh", "B2", "B1"]
    assert backend.writes[-1][1]["last"] == "Fresh"
    assert hook._boss_tracker.bosses_dict()["names"] == ["Fresh", "B2", "B1"]


def test_boss_log_clear_still_clears_when_unread(backend):
    ff7, hook = _ff7_hook()
    backend.store[ff7._FF7_BOSS_LOG_PATH] = {"names": ["B1"], "last": "B1"}
    backend.fail_reads = True
    hook._load_boss_log()
    backend.fail_reads = False
    hook.handle_command("clear_bosses", {})
    assert backend.writes[-1][1]["names"] == []


def test_connector_override_failed_read_kept_pending(backend):
    ff7, hook = _ff7_hook()
    op = sorted(ff7.FF7_CONNECTOR_PERSIST_ALLOWLIST)[0]
    stored_row = {"key": "stored", "op": op, "kwargs": {}, "expires_at_ms": None}
    backend.store[ff7._FF7_CONNECTOR_OVERRIDES_PATH] = {"version": 1, "entries": [stored_row]}

    backend.fail_reads = True
    hook._persist_connector_override(op, {"value": 2})
    assert backend.writes == []
    assert len(hook._pending_override_rows) == 1

    backend.fail_reads = False
    hook._persist_connector_override(op, {"value": 3})
    entries = backend.writes[-1][1]["entries"]
    assert entries[0] == stored_row  # stored entries kept
    assert hook._pending_override_rows == {}
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys))


def test_connector_override_success_path_unchanged(backend):
    ff7, hook = _ff7_hook()
    op = sorted(ff7.FF7_CONNECTOR_PERSIST_ALLOWLIST)[0]
    hook._persist_connector_override(op, {"value": 1})
    data = backend.writes[-1][1]
    assert set(data) == {"entries"}  # an empty stored doc gets only entries, as before
    assert [e["op"] for e in data["entries"]] == [op]


# --- activity feed view ----------------------------------------------------------


def _view_manager():
    from modules.uiwindows import activity_feed

    return activity_feed, activity_feed.ActivityFeedViewManager()


def test_feed_view_success_path_unchanged(backend):
    af, vm = _view_manager()
    backend.store[af.ACTIVITY_FEED_VIEW_PATH] = {"condensed": True, "tab": "current", "page": 3}
    vm.load()
    vm.update({"page": 4}, apply_desktop=False, broadcast=False)
    payload = backend.writes[-1][1]
    assert payload["condensed"] is True and payload["page"] == 4


def test_feed_view_failed_load_merges_stored_fields(backend):
    af, vm = _view_manager()
    backend.store[af.ACTIVITY_FEED_VIEW_PATH] = {"condensed": True, "tab": "current", "page": 3}
    backend.fail_reads = True
    vm.load()
    vm.update({"page": 5}, apply_desktop=False, broadcast=False)
    assert backend.writes == []  # unreadable: defaults are not saved over the view
    backend.fail_reads = False
    vm.update({"page": 6}, apply_desktop=False, broadcast=False)
    payload = backend.writes[-1][1]
    assert payload["condensed"] is True  # stored field kept
    assert payload["page"] == 6  # this session's change on top


# --- PSN game cache --------------------------------------------------------------


def test_psn_cache_read_reports_failure(monkeypatch, backend):
    from modules import psnapi

    monkeypatch.setattr(psnapi, "database_manager", backend)
    path = f"{psnapi.PSN_GAME_DATA_PATH}/NPWR1_00"
    backend.store[path] = {"np_communication_id": "NPWR1_00", "trophy_groups": [1]}
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR1_00") == (backend.store[path], True)
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR9_00") == (None, True)
    backend.fail_reads = True
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR1_00") == (None, False)
    assert psnapi.get_psn_game_cache_by_comm_id("NPWR1_00") is None
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", {"a": 1}) is False
    assert backend.writes == []

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""A failed database write is never reported as saved, and is reported once.

- StateManager.save_changes returns False when a write fails, keeps the changes
  pending, and shows one notification (the database layer stays quiet for it).
- DatabaseManager reports other callers' failed writes itself (deduped, popup
  throttled) and says "can't connect" once per outage, not on every retry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from modules import database_manager as dbm


class _FailingBackend:
    last_error = ""
    snapshot_error = ""

    def __init__(self, ok: bool = False):
        self.ok = ok
        self.writes = []

    def set_data(self, path, data):
        self.writes.append(path)
        return self.ok


@pytest.fixture()
def state_manager():
    from modules import dataobjects

    sm = dataobjects.StateManager()
    with patch.object(dataobjects, "_apply_database_connection_settings"):
        sm.initialize_with_data({})
    return sm


def test_failed_write_is_not_reported_as_saved(monkeypatch, state_manager):
    from modules import dataobjects

    def failing_set_data(path, data):
        dbm._write_tls.last_write = ("timeout", "no answer after 30 s")
        return False

    monkeypatch.setattr(dbm, "set_data", failing_set_data)
    monkeypatch.setattr(dataobjects, "_database_is_up", lambda: True)
    state_manager.update_app_setting("activity_feed_limit", 11)
    with patch.object(dataobjects, "notify") as ui_notify:
        assert state_manager.save_changes() is False
    # Still pending, so the next Save retries it.
    assert state_manager.field_has_changes("app_settings.activity_feed_limit")
    assert ui_notify.call_count == 1
    kwargs = ui_notify.call_args.kwargs
    assert kwargs["source"] == "database"
    assert kwargs["dedupe_key"] == "state:save_failed"
    assert "app settings" in kwargs["title"]
    assert "didn't respond in time" in ui_notify.call_args.args[0]
    assert kwargs["actions"][0]["settings_subtab"] == "Database"


def test_successful_write_clears_pending_changes(monkeypatch, state_manager):
    from modules import dataobjects

    monkeypatch.setattr(dbm, "set_data", lambda path, data: True)
    state_manager.update_app_setting("activity_feed_limit", 12)
    with patch.object(dataobjects, "notify") as ui_notify:
        assert state_manager.save_changes() is True
    assert not state_manager.field_has_changes("app_settings.activity_feed_limit")
    ui_notify.assert_not_called()


def test_manager_reports_write_failure_once_and_respects_quiet():
    dm = dbm.DatabaseManager()
    dm._config = dbm.DatabaseConfig(database_type="sql", sql_database_path=":memory:")
    dm._initialized = True
    dm._database = _FailingBackend(ok=False)
    with patch.object(dbm, "notify") as db_notify:
        assert dm.set_data("Some/Path", {}) is False
        assert dm.set_data("Some/Path", {}) is False
        with dbm.quiet_notifications():
            assert dm.set_data("Some/Path", {}) is False
    assert db_notify.call_count == 2
    first, second = db_notify.call_args_list
    assert first.kwargs["dedupe_key"] == "db:write_failed"
    assert first.kwargs["source"] == "database"
    assert first.kwargs["skip_toast"] is False
    # The repeat lands in the same entry without popping up again.
    assert second.kwargs["skip_toast"] is True


def test_connect_failure_notifies_once_per_outage(tmp_path):
    dm = dbm.DatabaseManager()
    dm._config = dbm.DatabaseConfig(database_type="nonsense")
    with patch.object(dbm, "notify_critical") as critical, patch.object(dbm, "notify") as info:
        assert dm.initialize() is False
        assert dm.initialize() is False
        assert critical.call_count == 1
        assert critical.call_args.kwargs["dedupe_key"] == "db:down"
        assert "unknown database type" in critical.call_args.kwargs["details"]
        ok = dm.initialize(dbm.DatabaseConfig(database_type="sql", sql_database_path=str(tmp_path / "t.db")))
        assert ok is True
        assert info.call_count == 1
        assert info.call_args.kwargs["title"].startswith("Reconnected")

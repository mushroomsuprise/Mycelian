"""Alert notifications: honest save results and no toast storms from background loops."""

from __future__ import annotations

from unittest.mock import patch

from modules import alert_processor, alertutils


def _manager() -> alertutils.AlertStateManager:
    manager = alertutils.AlertStateManager()
    manager._initialized = True
    return manager


def test_save_alert_reports_a_failed_database_write():
    manager = _manager()
    with (
        patch.object(manager, "_is_firebase_backend", return_value=False),
        patch("modules.alertutils.database_manager.set_data", return_value=False),
    ):
        assert manager.save_alert("bits", "bits100", {"alert_name": "x"}, quiet_db_notices=True) is False
    assert manager.last_write_error.startswith("Database write failed: Alerts/")

    with (
        patch.object(manager, "_is_firebase_backend", return_value=False),
        patch("modules.alertutils.database_manager.set_data", return_value=True),
    ):
        assert manager.save_alert("bits", "bits100", {"alert_name": "x"}) is True
    assert manager.last_write_error == ""


def test_delete_alert_reports_a_failed_database_write():
    manager = _manager()
    with (
        patch.object(manager, "_is_firebase_backend", return_value=False),
        patch("modules.alertutils.database_manager.delete_data", return_value=False),
        patch("modules.alertutils.database_manager.set_data", return_value=True),
    ):
        assert manager.delete_alert("bits", "bits100") is False
    assert manager.last_write_error.startswith("Database write failed: Alerts/")


def test_resub_fallback_switch_reports_a_failed_write():
    manager = _manager()
    with patch("modules.alertutils.database_manager.set_data", return_value=False):
        assert manager.set_resub_fallback_enabled(True) is False


def test_unsaved_activity_history_warns_once_with_a_stable_key():
    manager = _manager()
    manager._alert_storage_loaded = True
    with (
        patch("modules.alertutils.database_manager.set_data", side_effect=OSError("disk full")),
        patch("modules.notification_engine.notify") as notify,
    ):
        assert manager.store_completed_alert("a1", {"alert_type": "follow", "username": "u"}) is False
    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    assert kwargs["source"] == "activity_feed"
    assert kwargs["dedupe_key"] == "activity:store_failed"


def test_quiet_alert_writes_silence_the_database_notice():
    """The Alerts tab reports its own save failure, so the database layer stays quiet."""
    import pytest

    from modules import database_manager as dbm

    if not hasattr(dbm, "_quiet"):
        pytest.skip("database layer has no quiet_notifications()")
    manager = _manager()
    seen = []

    def _set(path, data):
        seen.append(dbm._quiet())
        return True

    with (
        patch.object(manager, "_is_firebase_backend", return_value=False),
        patch("modules.alertutils.database_manager.set_data", side_effect=_set),
    ):
        assert manager.save_alert("bits", "bits5", {"alert_name": "x"}, quiet_db_notices=True)
        assert manager.save_alert("bits", "bits6", {"alert_name": "y"})
    assert seen == [True, True, False, False]


def test_queue_errors_notify_once_per_streak_then_escalate_once():
    alert_processor._queue_error_state.update(streak=0, last_notified=None, escalated=False)
    with (
        patch.object(alert_processor, "notify") as notify,
        patch.object(alert_processor, "notify_critical") as critical,
    ):
        for _ in range(alert_processor._QUEUE_ERROR_ESCALATE_AFTER + 10):
            alert_processor._note_queue_error(RuntimeError("boom"))
        assert notify.call_count == 1
        assert critical.call_count == 1
        # A recovery followed by a new fault inside the cooldown stays quiet.
        alert_processor._note_queue_ok()
        alert_processor._note_queue_error(RuntimeError("boom"))
        assert notify.call_count == 1
    alert_processor._queue_error_state.update(streak=0, last_notified=None, escalated=False)

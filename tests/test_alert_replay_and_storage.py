#!/usr/bin/env python3
"""Tests for alert replay repoll, stored-alert trim, and feed card skip-on-error."""

from __future__ import annotations

import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alertutils
from modules.uiwindows import activity_feed as feed


class AppSettingsTrimDefaultsTests(unittest.TestCase):
    def test_auto_trim_defaults_off(self) -> None:
        from modules.dataobjects import AppSettings

        settings = AppSettings()
        self.assertFalse(settings.alert_storage_auto_trim)
        self.assertEqual(settings.alert_storage_trim_mode, "both")
        self.assertEqual(settings.alert_storage_keep_count, 500)
        self.assertEqual(settings.alert_storage_keep_days, 30)


class ReplayAlertTests(unittest.TestCase):
    def test_replay_uses_current_config_and_stored_event(self) -> None:
        stored = {
            "alert_type": "bit",
            "username": "CheerUser",
            "amt_cheered": 500,
            "message": "pog",
            "duration": 3.0,
            "gif_dir": "/old",
            "gif_name": "old.gif",
            "volume": 10,
        }
        config = alertutils.AlertObj()
        config.alert_type = "bit"
        config.duration = 8.0
        config.gif_dir = "/new"
        config.gif_name = "new.gif"
        config.volume = 80
        config.fade_in = 250
        config.username = "ConfigPlaceholder"

        with patch.object(alertutils, "fetch_bits_alert", return_value=config):
            replay = alertutils.build_replay_alert(stored)

        self.assertTrue(replay.is_replay)
        self.assertFalse(replay.played)
        self.assertTrue(replay.stackable)
        self.assertTrue(str(replay.alert_id).startswith("Replay"))
        self.assertEqual(replay.username, "CheerUser")
        self.assertEqual(replay.amt_cheered, 500)
        self.assertEqual(replay.message, "pog")
        self.assertEqual(replay.duration, 8.0)
        self.assertEqual(replay.gif_dir, "/new")
        self.assertEqual(replay.gif_name, "new.gif")
        self.assertEqual(replay.volume, 80)
        self.assertEqual(replay.fade_in, 250)

    def test_replay_falls_back_to_snapshot_when_config_missing(self) -> None:
        stored = {
            "alert_type": "follow",
            "username": "OldFollower",
            "duration": 4.0,
            "gif_dir": "/stored",
            "gif_name": "follow.gif",
        }
        with patch.object(alertutils, "fetch_follow_alert", return_value=None):
            replay = alertutils.build_replay_alert(stored)

        self.assertTrue(replay.is_replay)
        self.assertEqual(replay.username, "OldFollower")
        self.assertEqual(replay.gif_dir, "/stored")
        self.assertEqual(replay.gif_name, "follow.gif")
        self.assertEqual(replay.duration, 4.0)


class AlertStorageTrimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = alertutils.AlertStateManager()
        now = time.time()
        self.manager._alert_storage = {
            "new": {"timestamp": now, "username": "a"},
            "mid": {"timestamp": now - 86400, "username": "b"},
            "old": {"timestamp": now - 40 * 86400, "username": "c"},
        }
        self.manager._alert_storage_loaded = True

    def test_trim_by_quantity_keeps_newest(self) -> None:
        deleted_paths = []

        def _delete(path: str) -> bool:
            deleted_paths.append(path)
            return True

        with (
            patch.object(
                self.manager, "_ensure_alert_storage_loaded", return_value=None
            ),
            patch("modules.alertutils.database_manager.delete_data", side_effect=_delete),
        ):
            deleted = self.manager.trim_stored_alerts(
                mode="quantity", keep_count=2, keep_days=30
            )

        self.assertEqual(deleted, 1)
        self.assertEqual(deleted_paths, ["Alerts/AlertStorage/old"])
        self.assertNotIn("old", self.manager._alert_storage)
        self.assertIn("new", self.manager._alert_storage)
        self.assertIn("mid", self.manager._alert_storage)

    def test_trim_by_time_deletes_old_rows(self) -> None:
        deleted_ids = []

        def _delete(path: str) -> bool:
            deleted_ids.append(path.rsplit("/", 1)[-1])
            return True

        with (
            patch.object(
                self.manager, "_ensure_alert_storage_loaded", return_value=None
            ),
            patch("modules.alertutils.database_manager.delete_data", side_effect=_delete),
        ):
            deleted = self.manager.trim_stored_alerts(
                mode="time", keep_count=500, keep_days=30
            )

        self.assertEqual(deleted, 1)
        self.assertEqual(deleted_ids, ["old"])

    def test_trim_both_applies_count_and_age(self) -> None:
        deleted_ids = set()

        def _delete(path: str) -> bool:
            deleted_ids.add(path.rsplit("/", 1)[-1])
            return True

        with (
            patch.object(
                self.manager, "_ensure_alert_storage_loaded", return_value=None
            ),
            patch("modules.alertutils.database_manager.delete_data", side_effect=_delete),
        ):
            deleted = self.manager.trim_stored_alerts(
                mode="both", keep_count=1, keep_days=30
            )

        self.assertEqual(deleted, 2)
        self.assertEqual(deleted_ids, {"mid", "old"})
        self.assertEqual(list(self.manager._alert_storage.keys()), ["new"])


class FeedCardSkipTests(unittest.TestCase):
    def test_create_alert_element_skips_non_stale_errors(self) -> None:
        class _DummyContainer:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        previous = feed.activity_feed_state.current_alerts_container
        feed.activity_feed_state.current_alerts_container = _DummyContainer()
        try:
            with (
                patch.object(feed, "_element_alive", return_value=True),
                patch.object(feed, "_is_stale_client_error", return_value=False),
                patch.object(feed.logger, "error"),
                patch.object(feed.ui, "element", side_effect=RuntimeError("bad card")),
            ):
                result = feed.create_alert_element(
                    {
                        "alert_id": "poison",
                        "type": "Bits",
                        "message": "hi",
                        "timestamp": 1,
                    }
                )
            self.assertTrue(result)
        finally:
            feed.activity_feed_state.current_alerts_container = previous


if __name__ == "__main__":
    unittest.main()

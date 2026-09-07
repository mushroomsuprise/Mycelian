#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for activity-feed DOM probe skip / recover behaviour."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import activity_feed as feed  # noqa: E402


class FeedOnScreenTests(unittest.TestCase):
    def test_other_main_tab_is_off_screen(self) -> None:
        with (
            patch("modules.tray_controller.is_minimized", return_value=False),
            patch(
                "modules.help_system.contextual_help.get_current_tab_context",
                return_value=("Settings", None),
            ),
        ):
            self.assertFalse(feed._feed_ui_is_on_screen())

    def test_tray_minimized_is_off_screen(self) -> None:
        with patch("modules.tray_controller.is_minimized", return_value=True):
            self.assertFalse(feed._feed_ui_is_on_screen())

    def test_activity_feed_tab_with_client_is_on_screen(self) -> None:
        with (
            patch("modules.tray_controller.is_minimized", return_value=False),
            patch(
                "modules.help_system.contextual_help.get_current_tab_context",
                return_value=("Activity Feed", None),
            ),
            patch.object(feed, "_get_feed_client", return_value=object()),
        ):
            self.assertTrue(feed._feed_ui_is_on_screen())


class DomProbeResultTests(unittest.TestCase):
    def setUp(self) -> None:
        feed._dom_desync_failures = 0
        feed._last_dom_desync_warn_at = 0.0
        self._alerts = feed.activity_feed_state.live_alerts
        self._condense = feed.activity_feed_state.condense_list
        self._tab = feed.activity_feed_state.current_tab
        feed.activity_feed_state.live_alerts = [{"element": object()}]
        feed.activity_feed_state.condense_list = False
        feed.activity_feed_state.current_tab = "current"

    def tearDown(self) -> None:
        feed.activity_feed_state.live_alerts = self._alerts
        feed.activity_feed_state.condense_list = self._condense
        feed.activity_feed_state.current_tab = self._tab
        feed._dom_desync_failures = 0

    def test_offscreen_skips_recover(self) -> None:
        with patch.object(feed, "recover_activity_feed_panel") as recover:
            action = feed._apply_dom_probe_result(
                "regular_ok:rebuild",
                {"ok": False, "reason": "offscreen", "children": 0},
            )
        self.assertEqual(action, "skip_offscreen")
        recover.assert_not_called()
        self.assertEqual(feed._dom_desync_failures, 0)

    def test_no_visible_surface_with_python_children_skips(self) -> None:
        with (
            patch.object(feed, "_python_feed_has_children", return_value=True),
            patch.object(feed, "recover_activity_feed_panel") as recover,
        ):
            action = feed._apply_dom_probe_result(
                "regular_ok:rebuild",
                {"ok": False, "reason": "no_visible_surface", "children": 0},
            )
        self.assertEqual(action, "skip_offscreen")
        recover.assert_not_called()
        self.assertEqual(feed._dom_desync_failures, 0)

    def test_elements_missing_with_python_children_skips(self) -> None:
        with (
            patch.object(feed, "_python_feed_has_children", return_value=True),
            patch.object(feed, "recover_activity_feed_panel") as recover,
        ):
            action = feed._apply_dom_probe_result(
                "regular_ok:rebuild",
                {"ok": False, "reason": "elements_missing", "children": 0},
            )
        self.assertEqual(action, "skip_offscreen")
        recover.assert_not_called()

    def test_empty_visible_surface_recovers(self) -> None:
        with (
            patch.object(feed, "_python_feed_has_children", return_value=False),
            patch.object(feed, "recover_activity_feed_panel", return_value=True) as recover,
        ):
            action = feed._apply_dom_probe_result(
                "regular_ok:rebuild",
                {"ok": False, "reason": "empty", "children": 0},
            )
        self.assertEqual(action, "recover")
        recover.assert_called_once()
        self.assertEqual(feed._dom_desync_failures, 1)

    def test_healthy_probe_resets_failures(self) -> None:
        feed._dom_desync_failures = 2
        action = feed._apply_dom_probe_result(
            "after_recover",
            {"ok": True, "reason": "ok", "children": 4},
        )
        self.assertEqual(action, "ok")
        self.assertEqual(feed._dom_desync_failures, 0)


if __name__ == "__main__":
    unittest.main()

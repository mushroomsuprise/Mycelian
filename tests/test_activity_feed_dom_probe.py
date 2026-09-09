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

    def test_toolbar_missing_disables_condense_and_reloads(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_fallback_to_regular_feed") as fallback,
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            action = feed._apply_dom_probe_result(
                "condensed_ok:rebuild",
                {"ok": False, "reason": "toolbar_missing", "children": 0},
            )
        self.assertEqual(action, "reload")
        fallback.assert_called_once_with("dom_toolbar_missing", disable_condense=True)
        reload.assert_called_once_with("feed_toolbar_missing")

    def test_abandon_condensed_disables_and_reloads(self) -> None:
        with (
            patch.object(feed, "_fallback_to_regular_feed") as fallback,
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            feed._abandon_condensed_view("condensed_build_failed:test")
        fallback.assert_called_once_with(
            "condensed_build_failed:test", disable_condense=True
        )
        reload.assert_called_once_with("condensed_abandoned")


class FeedExpectsContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._alerts = feed.activity_feed_state.live_alerts
        self._condense = feed.activity_feed_state.condense_list
        self._tab = feed.activity_feed_state.current_tab
        feed.activity_feed_state.live_alerts = []
        feed.activity_feed_state.condense_list = False
        feed.activity_feed_state.current_tab = "current"

    def tearDown(self) -> None:
        feed.activity_feed_state.live_alerts = self._alerts
        feed.activity_feed_state.condense_list = self._condense
        feed.activity_feed_state.current_tab = self._tab

    def test_regular_feed_without_live_alerts_does_not_expect_content(self) -> None:
        self.assertFalse(feed._feed_expects_visible_content())

    def test_condensed_mode_expects_content_without_live_alerts(self) -> None:
        feed.activity_feed_state.condense_list = True
        self.assertTrue(feed._feed_expects_visible_content())


class CondensedViewSafetyTests(unittest.TestCase):
    def test_grouping_skips_poison_alert(self) -> None:
        alerts = [
            {"type": "follow", "username": "alice", "message": "alice followed"},
            {
                "type": None,
                "username": "bob",
                "message": "bob cheered 1 bits",
            },
            {"type": "follow", "username": "carol", "message": "carol followed"},
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertIn("alice", user_alerts)
        self.assertIn("carol", user_alerts)
        self.assertNotIn("bob", user_alerts)
        self.assertGreaterEqual(excluded, 1)
        self.assertEqual(unknown, 0)

    def test_grouping_skips_non_dict_rows(self) -> None:
        alerts = [
            {"type": "follow", "username": "alice", "message": "alice followed"},
            "not-an-alert",
            None,
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(list(user_alerts), ["alice"])
        self.assertEqual(excluded, 2)
        self.assertEqual(unknown, 0)

    def test_condensed_html_escapes_user_content(self) -> None:
        markup = feed._build_condensed_html(
            {
                "groups": [
                    {
                        "username": "<script>x</script>",
                        "lines": ["<img src=x onerror=alert(1)>"],
                    }
                ],
                "alert_count": 1,
                "historical_count": 1,
                "live_in_window": 0,
                "condense_historical_hours": 12,
            }
        )
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("&lt;img", markup)

    def test_serialize_caps_groups(self) -> None:
        user_alerts = {
            name: {
                "follow": {
                    "count": 1,
                    "total_amount": 0,
                    "tier": 1,
                    "months": 0,
                    "original_type": "follow",
                }
            }
            for name in ("a", "b", "c")
        }
        with patch.object(feed, "MAX_CONDENSED_GROUPS", 2):
            groups = feed.serialize_condensed_groups(user_alerts)
        self.assertEqual(len(groups), 2)
        self.assertEqual([g["username"] for g in groups], ["a", "b"])

    def test_delete_replaced_children_refuses_foreign_slot(self) -> None:
        class _Slot:
            def __init__(self, parent):
                self.parent = parent
                self.children = []

        class _El:
            def __init__(self):
                self.deleted = False
                self.default_slot = _Slot(self)

            def delete(self):
                self.deleted = True

        container = _El()
        keep = _El()
        sibling = _El()
        container.default_slot.children = [keep, sibling]
        feed._delete_replaced_condensed_children(container, keep)
        self.assertTrue(sibling.deleted)
        self.assertFalse(keep.deleted)

        other = _El()
        container.default_slot.parent = object()
        container.default_slot.children = [keep, other]
        feed._delete_replaced_condensed_children(container, keep)
        self.assertFalse(other.deleted)
        self.assertFalse(keep.deleted)


if __name__ == "__main__":
    unittest.main()

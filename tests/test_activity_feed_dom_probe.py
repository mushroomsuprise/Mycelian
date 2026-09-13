#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for activity-feed DOM probe skip / recover behaviour."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
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

    def test_elements_missing_offscreen_skips(self) -> None:
        with (
            patch.object(feed, "_python_feed_has_children", return_value=True),
            patch.object(feed, "_feed_ui_is_on_screen", return_value=False),
            patch.object(feed, "recover_activity_feed_panel") as recover,
            patch.object(feed, "_abandon_condensed_view") as abandon,
        ):
            action = feed._apply_dom_probe_result(
                "regular_ok:rebuild",
                {"ok": False, "reason": "elements_missing", "children": 0},
            )
        self.assertEqual(action, "skip_offscreen")
        recover.assert_not_called()
        abandon.assert_not_called()

    def test_elements_missing_on_screen_abandons_condensed(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_python_feed_has_children", return_value=True),
            patch.object(feed, "_feed_ui_is_on_screen", return_value=True),
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "recover_activity_feed_panel", return_value=True),
        ):
            action = feed._apply_dom_probe_result(
                "after_condensed_render:test",
                {"ok": False, "reason": "elements_missing", "children": 0},
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once_with("dom_elements_missing")

    def test_panel_missing_abandons_condensed(self) -> None:
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "recover_activity_feed_panel", return_value=True),
        ):
            action = feed._apply_dom_probe_result(
                "after_condensed_render:test",
                {"ok": False, "reason": "panel_missing", "children": 0},
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once_with("dom_panel_missing")

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

    def test_toolbar_missing_disables_condense_without_reload(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "recover_activity_feed_panel", return_value=True),
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            action = feed._apply_dom_probe_result(
                "condensed_ok:rebuild",
                {"ok": False, "reason": "toolbar_missing", "children": 0},
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once_with("dom_toolbar_missing")
        reload.assert_not_called()

    def test_occluded_condensed_fails_open(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            action = feed._apply_dom_probe_result(
                "condensed_ok:rebuild",
                {"ok": False, "reason": "occluded", "children": 4},
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once_with("dom_occluded")
        reload.assert_not_called()

    def test_unmeasurable_condensed_surface_fails_open(self) -> None:
        """A revealed surface with no measurable area is the blank screen."""
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_feed_ui_is_on_screen", return_value=True),
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            action = feed._apply_dom_probe_result(
                "periodic_watchdog",
                {
                    "ok": False,
                    "reason": "offscreen",
                    "condensed": True,
                    "children": 13,
                    "point": None,
                },
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once_with("dom_condensed_offscreen")
        reload.assert_not_called()

    def test_offscreen_regular_feed_still_skips_recovery(self) -> None:
        feed.activity_feed_state.condense_list = False
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_escalate_page_reload") as reload,
        ):
            action = feed._apply_dom_probe_result(
                "periodic_watchdog",
                {"ok": False, "reason": "offscreen", "condensed": False, "children": 0},
            )
        self.assertEqual(action, "skip_offscreen")
        abandon.assert_not_called()
        reload.assert_not_called()

    def test_condensed_surface_offscreen_in_tray_is_benign(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_feed_ui_is_on_screen", return_value=False),
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_schedule_condensed_rebuild"),
        ):
            action = feed._apply_dom_probe_result(
                "periodic_watchdog",
                {"ok": False, "reason": "offscreen", "condensed": True, "children": 13},
            )
        self.assertEqual(action, "skip_offscreen")
        abandon.assert_not_called()

    def test_zero_area_regular_feed_logs_without_acting(self) -> None:
        """Diagnostic only: escalating an unpainted panel risks reload loops."""
        feed.activity_feed_state.condense_list = False
        feed._last_unpainted_warn_at = 0.0
        with (
            patch.object(feed, "_feed_ui_is_on_screen", return_value=True),
            patch.object(feed, "_log_condensed_ancestry") as ancestry,
            patch.object(feed, "_escalate_page_reload") as reload,
            patch.object(feed, "_abandon_condensed_view") as abandon,
        ):
            action = feed._apply_dom_probe_result(
                "periodic_watchdog",
                {
                    "ok": True,
                    "reason": "ok",
                    "condensed": False,
                    "children": 7,
                    "zeroArea": True,
                    "chain": [{"tag": "DIV", "cls": "q-tab-panel", "disp": "none"}],
                },
            )
        self.assertEqual(action, "ok")
        ancestry.assert_called_once()
        reload.assert_not_called()
        abandon.assert_not_called()

    def test_probe_js_measures_regular_feed_area(self) -> None:
        js = feed._FEED_DOM_PROBE_JS
        self.assertIn("zeroArea", js)
        self.assertIn("getBoundingClientRect", js)

    def test_pending_reveal_schedules_rebuild(self) -> None:
        """Condense on but never revealed (deferred commit) must retry."""
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_schedule_condensed_rebuild") as rebuild,
            patch.object(feed, "_abandon_condensed_view") as abandon,
        ):
            action = feed._apply_dom_probe_result(
                "periodic_watchdog",
                {"ok": True, "reason": "ok", "condensed": False, "children": 5},
            )
        self.assertEqual(action, "pending_reveal")
        rebuild.assert_called_once()
        abandon.assert_not_called()

    def test_abandon_condensed_fails_open_without_reload(self) -> None:
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_fallback_to_regular_feed") as fallback,
            patch.object(feed, "_escalate_page_reload") as reload,
            patch.object(feed, "_set_condensed_unavailable_notice") as notice,
        ):
            feed._abandon_condensed_view("condensed_build_failed:test")
        fallback.assert_called_once_with(
            "condensed_build_failed:test", disable_condense=True
        )
        reload.assert_not_called()
        notice.assert_called_once_with(True)

    def test_probe_js_sees_condensed_mode(self) -> None:
        js = feed._FEED_DOM_PROBE_JS
        self.assertIn("data-view", js)
        self.assertIn("activity-feed-condensed-root", js)
        self.assertIn("elementFromPoint", js)
        # Probe and commit must agree on what "painted" means.
        self.assertIn(feed._CONDENSED_HITTEST_JS, js)
        self.assertIn(feed._CONDENSED_HITTEST_JS, feed._CONDENSED_COMMIT_JS)

    def test_commit_js_hit_tests_and_reverts_on_failure(self) -> None:
        js = feed._CONDENSED_COMMIT_JS
        self.assertIn("elementFromPoint", js)
        self.assertIn("setAttribute('data-view'", js)
        self.assertIn("removeAttribute('data-view')", js)
        self.assertIn("activity-feed-condensed-root", js)
        set_idx = js.find("setAttribute('data-view'")
        hit_idx = js.find("var test = _afHitTest(root)")
        revert_idx = js.rfind("removeAttribute('data-view')")
        self.assertGreater(hit_idx, set_idx)
        self.assertGreater(revert_idx, hit_idx)
        self.assertNotIn("innerHTML", js)

    def test_hittest_clips_sample_point_to_viewport(self) -> None:
        """A list taller than the viewport must not read as occluded."""
        js = feed._CONDENSED_HITTEST_JS
        self.assertIn("window.innerHeight", js)
        self.assertIn("document.documentElement.clientHeight", js)
        # No usable point is reported as offscreen, never occluded.
        self.assertIn("verdict: 'offscreen'", js)

    def test_hittest_tries_several_candidate_points(self) -> None:
        """One degenerate box must not condemn the whole surface."""
        js = feed._CONDENSED_HITTEST_JS
        self.assertIn("_afSamplePoints", js)
        # Children, the root, and the scroll port are all candidates.
        self.assertIn("root.children[i].getBoundingClientRect()", js)
        self.assertIn("_afIntersect(root.getBoundingClientRect(), clip)", js)
        self.assertIn("_afIntersect(clip, null)", js)
        self.assertIn("for (var i = 0; i < pts.length; i++)", js)

    def test_hittest_reports_metrics_for_diagnosis(self) -> None:
        js = feed._CONDENSED_HITTEST_JS
        for field in ("vw:", "vh:", "root:", "clip:", "child0:", "points"):
            self.assertIn(field, js)

    def test_commit_reveals_only_when_verified(self) -> None:
        """Only a confirmed paint may hide the regular feed."""
        js = feed._CONDENSED_COMMIT_JS
        self.assertIn("test.verdict !== 'ok'", js)
        self.assertIn("ok: test.verdict === 'ok'", js)

    def test_hittest_reports_ancestry_when_unmeasurable(self) -> None:
        js = feed._CONDENSED_HITTEST_JS
        self.assertIn("_afAncestry", js)
        self.assertIn("chain: _afAncestry(root)", js)

    def test_hittest_unswipes_quasar_panel(self) -> None:
        js = feed._CONDENSED_HITTEST_JS
        self.assertIn("_afUnswipe", js)
        self.assertIn("setProperty('transform', 'none', 'important')", js)
        css = feed._CONDENSED_COMMIT_JS
        self.assertIn("_afUnswipe", css)

    def test_condensed_css_pins_swiped_tab_panel(self) -> None:
        src = Path(feed.__file__).read_text(encoding="utf-8")
        self.assertIn(
            ".mycelian-main-tab-shell:has(.activity-feed-surfaces[data-view=\"condensed\"])",
            src,
        )
        self.assertIn("flex-direction: column !important;", src)
        self.assertIn("flex-wrap: nowrap !important;", src)
        self.assertIn("_afReseatFeed", feed._CONDENSED_HITTEST_JS)
        self.assertIn("flex-wrap', 'nowrap'", feed._CONDENSED_HITTEST_JS)
        styles = Path(ROOT / "modules" / "ui_styles.py").read_text(encoding="utf-8")
        self.assertIn("flex-direction: column !important;", styles)
        self.assertIn("flex-wrap: nowrap !important;", styles)

    def test_hittest_geometry_fallback_when_elementfrompoint_is_null(self) -> None:
        """On-screen area plus children is a paint, even with no hit sample."""
        js = feed._CONDENSED_HITTEST_JS
        self.assertIn("via = 'geometry'", js)
        self.assertIn("reason: 'geometry'", js)
        self.assertIn("_afHasLayoutBox", js)
        self.assertIn("root.children.length > 0", js)

    def test_geometry_commit_keeps_condensed_view(self) -> None:
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_set_condensed_unavailable_notice") as notice,
            patch.object(feed, "schedule_feed_integrity_check"),
        ):
            action = feed._handle_condensed_commit_result(
                "update_condensed_view",
                {"ok": True, "reason": "geometry", "children": 13},
            )
        self.assertEqual(action, "ok")
        abandon.assert_not_called()
        notice.assert_called_once_with(False)

    def test_metrics_are_logged_on_failure(self) -> None:
        with self.assertLogs(feed.logger, level="WARNING") as logs:
            feed._log_condensed_ancestry(
                "test",
                {
                    "metrics": {
                        "vw": 1920,
                        "vh": 1205,
                        "root": {"l": 29, "t": 172, "w": 1792, "h": 1153},
                        "clip": {"l": 16, "t": 163, "w": 1818, "h": 985},
                        "child0": None,
                        "kids": 13,
                        "points": 0,
                    }
                },
            )
        joined = "\n".join(logs.output)
        self.assertIn("viewport=1920x1205", joined)
        self.assertIn("candidate_points=0", joined)

    def test_unmeasurable_commit_fails_open(self) -> None:
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "schedule_feed_integrity_check"),
        ):
            action = feed._handle_condensed_commit_result(
                "update_condensed_view",
                {"ok": False, "reason": "offscreen", "children": 13, "point": None},
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once()

    def test_commit_deferred_while_feed_offscreen(self) -> None:
        with patch.object(feed, "_feed_ui_is_on_screen", return_value=False):
            result = asyncio.run(feed._commit_condensed_view_async())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "deferred_offscreen")

    def test_deferred_commit_keeps_toggle_and_feed(self) -> None:
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_set_condensed_unavailable_notice") as notice,
        ):
            action = feed._handle_condensed_commit_result(
                "apply_alert_on_ui",
                {"ok": False, "reason": "deferred_offscreen", "children": 0},
            )
        self.assertEqual(action, "defer")
        abandon.assert_not_called()
        notice.assert_not_called()

    def test_hide_js_clears_data_view(self) -> None:
        js = feed._CONDENSED_HIDE_JS
        self.assertIn("removeAttribute('data-view')", js)
        self.assertNotIn("<script>", js)


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


class CondensedViewContractTests(unittest.TestCase):
    """Pin condensed grouping / copy / overlay behavior across the render rewrite."""

    def setUp(self) -> None:
        self._alerts = feed.activity_feed_state.live_alerts
        self._condense = feed.activity_feed_state.condense_list
        self._tab = feed.activity_feed_state.current_tab
        self._filters = dict(feed.activity_feed_state.filter_state)
        self._toggle = feed.activity_feed_state.condense_toggle
        feed.activity_feed_state.live_alerts = []
        feed.activity_feed_state.condense_list = False
        feed.activity_feed_state.current_tab = "current"

    def tearDown(self) -> None:
        feed.activity_feed_state.live_alerts = self._alerts
        feed.activity_feed_state.condense_list = self._condense
        feed.activity_feed_state.current_tab = self._tab
        feed.activity_feed_state.filter_state = self._filters
        feed.activity_feed_state.condense_toggle = self._toggle

    def test_empty_state_wording(self) -> None:
        model = feed._condensed_view_model(
            {
                "groups": [],
                "condense_historical_hours": 12,
                "alert_count": 0,
                "historical_count": 0,
                "live_in_window": 0,
            }
        )
        self.assertEqual(model["empty"], "No alerts to condense in the last 12 hours")
        self.assertIsNone(model["header"])
        self.assertEqual(model["groups"], [])

    def test_header_wording_when_historical_or_live_present(self) -> None:
        model = feed._condensed_view_model(
            {
                "groups": [{"username": "alice", "lines": ["Followed!"]}],
                "alert_count": 6,
                "historical_count": 4,
                "live_in_window": 2,
                "condense_historical_hours": 12,
            }
        )
        self.assertIsNone(model["empty"])
        self.assertEqual(model["header"], "Showing 6 alerts from past 12 hours")
        self.assertEqual(model["groups"][0]["username"], "alice")
        self.assertEqual(model["groups"][0]["lines"], ["Followed!"])

    def test_collect_build_data_uses_stored_alerts_when_live_empty(self) -> None:
        """Restart with no live alerts still groups stored Previous Alerts."""
        stored = [
            {
                "alert_id": "h1",
                "username": "alice",
                "type": "Follow",
                "created_at": 1_700_000_000.0,
                "message": "alice followed",
            }
        ]
        feed.activity_feed_state.live_alerts = []
        with (
            patch.object(feed, "_load_condensed_historical_hours", return_value=12),
            patch.object(
                feed, "load_restored_alerts_for_time_window", return_value=(stored, 1)
            ),
        ):
            data = feed._collect_condensed_build_data()
        self.assertEqual(data["historical_count"], 1)
        self.assertEqual(data["live_in_window"], 0)
        self.assertEqual(data["alert_count"], 1)
        self.assertTrue(data["groups"])
        model = feed._condensed_view_model(data)
        self.assertEqual(model["header"], "Showing 1 alerts from past 12 hours")
        self.assertEqual(model["groups"][0]["username"], "alice")

    def test_no_header_when_counts_are_zero(self) -> None:
        model = feed._condensed_view_model(
            {
                "groups": [{"username": "alice", "lines": ["Followed!"]}],
                "alert_count": 1,
                "historical_count": 0,
                "live_in_window": 0,
                "condense_historical_hours": 8,
            }
        )
        self.assertIsNone(model["header"])

    def test_hours_coercion(self) -> None:
        self.assertEqual(feed.parse_condensed_historical_hours("12"), 12)
        self.assertEqual(feed.parse_condensed_historical_hours(0), 12)
        self.assertEqual(feed.parse_condensed_historical_hours(-3), 12)
        self.assertEqual(feed.parse_condensed_historical_hours("nope", default=8), 8)
        self.assertEqual(feed.parse_condensed_historical_hours(3.9), 3)

    def test_hype_train_excluded(self) -> None:
        alerts = [
            {"type": "Follow", "username": "alice", "message": "alice followed"},
            {
                "type": "Hype Train",
                "badge_type": "hype_train",
                "username": "bob",
                "message": "Hype Train Level 2!",
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertIn("alice", user_alerts)
        self.assertNotIn("bob", user_alerts)
        self.assertGreaterEqual(excluded, 1)
        self.assertEqual(unknown, 0)

    def test_unknown_usernames_dropped(self) -> None:
        alerts = [
            {"type": "Follow", "username": "", "message": ""},
            {"type": "Follow", "username": "alice", "message": "alice followed"},
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(list(user_alerts), ["alice"])
        self.assertEqual(unknown, 1)
        self.assertEqual(excluded, 0)

    def test_filters_exclude_types(self) -> None:
        alerts = [
            {"type": "Follow", "username": "alice", "message": "alice followed"},
            {
                "type": "Bits",
                "username": "bob",
                "message": "bob cheered 10 bits!",
                "stored_alert_data": {"amt_cheered": 10},
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": False, "follows": True, "bits": False}
        )
        self.assertIn("alice", user_alerts)
        self.assertNotIn("bob", user_alerts)
        self.assertEqual(excluded, 1)
        self.assertEqual(unknown, 0)

    def test_bits_and_donations_sum(self) -> None:
        alerts = [
            {
                "type": "Bits",
                "username": "alice",
                "message": "alice cheered 100 bits!",
                "stored_alert_data": {"amt_cheered": 100},
            },
            {
                "type": "Bits",
                "username": "alice",
                "message": "alice cheered 50 bits!",
                "stored_alert_data": {"amt_cheered": 50},
            },
            {
                "type": "Donation",
                "username": "alice",
                "message": "alice donated $1.50",
                "stored_alert_data": {"donation_amount": 1.5},
            },
            {
                "type": "Donation",
                "username": "alice",
                "message": "alice donated $2",
                "stored_alert_data": {"donation_amount": 2},
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(excluded, 0)
        self.assertEqual(unknown, 0)
        self.assertEqual(user_alerts["alice"]["bits"]["total_amount"], 150)
        self.assertEqual(user_alerts["alice"]["donation"]["total_amount"], 3.5)
        groups = feed.serialize_condensed_groups(user_alerts)
        self.assertEqual(groups[0]["username"], "alice")
        self.assertIn("Gave 150 bits!", groups[0]["lines"])
        self.assertIn("Donated $3.50!", groups[0]["lines"])

    def test_raids_and_streaks_take_maximum(self) -> None:
        alerts = [
            {
                "type": "Raid",
                "username": "alice",
                "message": "alice raided with 10 viewers",
                "stored_alert_data": {"raider_count": 10, "game_name": "Old Game"},
            },
            {
                "type": "Raid",
                "username": "alice",
                "message": "alice raided with 50 viewers",
                "stored_alert_data": {"raider_count": 50, "game_name": "New Game"},
            },
            {
                "type": "Streak",
                "username": "alice",
                "message": "alice has watched for 2 consecutive streams",
                "streak_count": 2,
            },
            {
                "type": "Streak",
                "username": "alice",
                "message": "alice has watched for 9 consecutive streams",
                "streak_count": 9,
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(excluded, 0)
        self.assertEqual(unknown, 0)
        self.assertEqual(user_alerts["alice"]["raid"]["total_amount"], 50)
        self.assertEqual(user_alerts["alice"]["raid"]["game_name"], "New Game")
        self.assertEqual(user_alerts["alice"]["streak"]["total_amount"], 9)

    def test_points_grouped_by_reward_name(self) -> None:
        alerts = [
            {
                "type": "Points",
                "badge_type": "point",
                "username": "alice",
                "message": "alice redeemed 'God Gamer'!",
                "stored_alert_data": {"alert_name": "God Gamer"},
            },
            {
                "type": "Points",
                "badge_type": "point",
                "username": "alice",
                "message": "alice redeemed 'God Gamer'!",
                "stored_alert_data": {"alert_name": "God Gamer"},
            },
            {
                "type": "Points",
                "badge_type": "point",
                "username": "alice",
                "message": "alice redeemed 'Hydrate'!",
                "stored_alert_data": {"alert_name": "Hydrate"},
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(excluded, 0)
        self.assertEqual(unknown, 0)
        groups = feed.serialize_condensed_groups(user_alerts)
        lines = groups[0]["lines"]
        self.assertIn("Redeemed 'God Gamer' 2 times!", lines)
        self.assertIn("Redeemed 'Hydrate'!", lines)

    def test_subs_keyed_by_tier_and_duration(self) -> None:
        alerts = [
            {"type": "Sub", "username": "alice", "tier": 1, "message": "alice subscribed"},
            {
                "type": "Resub",
                "username": "alice",
                "tier": 2,
                "message": "alice has resubscribed for 1 months!",
                "stored_alert_data": {"resub_month": 1},
            },
            {
                "type": "Resub",
                "username": "alice",
                "tier": 3,
                "message": "alice has resubscribed for 6 months!",
                "stored_alert_data": {"resub_month": 6},
            },
            {
                "type": "Giftsub",
                "username": "alice",
                "tier": 1,
                "message": "alice gifted 2 subs",
                "stored_alert_data": {"gift_qty": 2, "resub_month": 1},
            },
        ]
        user_alerts, excluded, unknown = feed._group_alerts_for_condensed(
            alerts, filter_state={"all": True}
        )
        self.assertEqual(excluded, 0)
        self.assertEqual(unknown, 0)
        self.assertIn("sub_tier1", user_alerts["alice"])
        self.assertIn("resub_tier2_1month", user_alerts["alice"])
        self.assertIn("resub_tier3_multi_month", user_alerts["alice"])
        self.assertIn("giftsub_tier1_1month", user_alerts["alice"])
        groups = feed.serialize_condensed_groups(user_alerts)
        lines = groups[0]["lines"]
        self.assertIn("Subscribed (Tier 1)!", lines)
        self.assertIn("Resubscribed for 1 month (Tier 2)!", lines)
        self.assertIn("Resubscribed for 6 months (Tier 3)!", lines)
        self.assertIn("Gifted 2 Tier 1 subs (1 month)!", lines)

    def test_collect_dedupes_by_alert_id_prefers_live(self) -> None:
        stored = [
            {
                "alert_id": "1",
                "username": "alice",
                "type": "Follow",
                "created_at": 1.0,
            }
        ]
        live = [
            {
                "alert_id": "1",
                "username": "alice",
                "type": "Follow",
                "created_at": 9.0,
            }
        ]
        feed.activity_feed_state.live_alerts = live
        with patch.object(
            feed, "load_restored_alerts_for_time_window", return_value=(stored, 1)
        ):
            alerts, historical_count, live_in_window = feed.collect_alerts_for_condensed_view(
                0.0
            )
        self.assertEqual(historical_count, 1)
        self.assertEqual(live_in_window, 1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["created_at"], 9.0)

    def test_collect_sorts_newest_first_and_drops_old_live(self) -> None:
        stored = [
            {"alert_id": "mid", "username": "carol", "type": "Follow", "created_at": 20.0},
        ]
        feed.activity_feed_state.live_alerts = [
            {"alert_id": "fresh", "username": "alice", "type": "Follow", "created_at": 50.0},
            {"alert_id": "stale", "username": "zed", "type": "Follow", "created_at": 1.0},
        ]
        with patch.object(
            feed, "load_restored_alerts_for_time_window", return_value=(stored, 1)
        ):
            alerts, historical_count, live_in_window = feed.collect_alerts_for_condensed_view(
                10.0
            )
        self.assertEqual(historical_count, 1)
        self.assertEqual(live_in_window, 1)
        self.assertEqual([a["alert_id"] for a in alerts], ["fresh", "mid"])

    def test_overlay_payload_shape(self) -> None:
        sample = [
            {"type": "Follow", "username": "alice", "message": "alice followed", "alert_id": "1"}
        ]
        with patch.object(
            feed, "collect_alerts_for_condensed_view", return_value=(sample, 1, 0)
        ):
            payload = feed.build_condensed_overlay_payload(hours="12")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["hours"], 12)
        self.assertEqual(payload["alert_count"], 1)
        self.assertEqual(payload["historical_count"], 1)
        self.assertEqual(payload["live_in_window"], 0)
        self.assertEqual(payload["user_count"], 1)
        self.assertEqual(payload["groups"][0]["username"], "alice")
        self.assertEqual(payload["groups"][0]["lines"], ["Followed!"])

    def test_filter_change_schedules_condensed_rebuild(self) -> None:
        feed.activity_feed_state.condense_list = True
        feed.activity_feed_state.current_tab = "current"
        with (
            patch.object(feed, "update_alert_visibility"),
            patch.object(feed, "schedule_condensed_view_update") as sched,
        ):
            feed.on_checkbox_change("follows", False)
        sched.assert_called_once_with("filter_change")

    def test_filter_change_skips_rebuild_when_condense_off(self) -> None:
        feed.activity_feed_state.condense_list = False
        with (
            patch.object(feed, "update_alert_visibility"),
            patch.object(feed, "schedule_condensed_view_update") as sched,
        ):
            feed.on_checkbox_change("follows", False)
        sched.assert_not_called()

    def test_notice_text_is_not_session_only(self) -> None:
        feed.activity_feed_state.live_alerts = []
        text = feed._condensed_unavailable_text()
        self.assertEqual(text, "Condensed view unavailable - showing full feed")
        self.assertNotIn("this session", text)
        self.assertNotIn("Previous Alerts", text)
        feed.activity_feed_state.live_alerts = [{"type": "Follow"}]
        self.assertEqual(
            feed._condensed_unavailable_text(),
            "Condensed view unavailable - showing full feed",
        )

    def test_toggle_hidden_on_previous_tab(self) -> None:
        class _Toggle:
            def __init__(self) -> None:
                self._classes = []
                self.is_deleted = False
                self.client = object()

            def classes(self, add=None, remove=None):
                if add:
                    self._classes.append(add)
                if remove and remove in self._classes:
                    self._classes.remove(remove)

        toggle = _Toggle()
        feed.activity_feed_state.condense_toggle = toggle
        feed.activity_feed_state.current_tab = "previous"
        feed.activity_feed_state.condense_list = False
        with (
            patch.object(feed, "_ensure_regular_feed_populated"),
            patch.object(feed, "_hide_condensed_view"),
            patch.object(feed, "schedule_feed_integrity_check"),
        ):
            self.assertTrue(feed.update_condensed_view())
        self.assertIn("hidden", toggle._classes)

    def test_toggle_shown_on_current_tab(self) -> None:
        class _Toggle:
            def __init__(self) -> None:
                self._classes = ["hidden"]
                self.is_deleted = False
                self.client = object()

            def classes(self, add=None, remove=None):
                if add:
                    self._classes.append(add)
                if remove and remove in self._classes:
                    self._classes.remove(remove)

        toggle = _Toggle()
        feed.activity_feed_state.condense_toggle = toggle
        feed.activity_feed_state.current_tab = "current"
        feed.activity_feed_state.condense_list = False
        with (
            patch.object(feed, "_ensure_regular_feed_populated"),
            patch.object(feed, "_hide_condensed_view"),
            patch.object(feed, "schedule_feed_integrity_check"),
        ):
            self.assertTrue(feed.update_condensed_view())
        self.assertNotIn("hidden", toggle._classes)

    def test_condense_on_schedules_rebuild_not_hide(self) -> None:
        class _Toggle:
            def __init__(self) -> None:
                self._classes = []
                self.is_deleted = False
                self.client = object()

            def classes(self, add=None, remove=None):
                return None

        feed.activity_feed_state.condense_toggle = _Toggle()
        feed.activity_feed_state.current_tab = "current"
        feed.activity_feed_state.condense_list = True
        with (
            patch.object(feed, "_schedule_condensed_rebuild") as sched,
            patch.object(feed, "_hide_condensed_view") as hide,
        ):
            self.assertTrue(feed.update_condensed_view())
        sched.assert_called_once_with("update_condensed_view")
        hide.assert_not_called()

    def test_failed_commit_fails_open(self) -> None:
        with (
            patch.object(feed, "_abandon_condensed_view") as abandon,
            patch.object(feed, "_set_condensed_unavailable_notice") as notice,
        ):
            action = feed._handle_condensed_commit_result(
                "update_condensed_view",
                {
                    "ok": False,
                    "reason": "occluded",
                    "children": 4,
                    "hit": {"tag": "DIV", "id": "", "cls": "q-tab-panel"},
                },
            )
        self.assertEqual(action, "abandon")
        abandon.assert_called_once()
        notice.assert_not_called()

    def test_successful_commit_clears_notice(self) -> None:
        with (
            patch.object(feed, "_set_condensed_unavailable_notice") as notice,
            patch.object(feed, "schedule_feed_integrity_check"),
        ):
            action = feed._handle_condensed_commit_result(
                "update_condensed_view",
                {"ok": True, "reason": "ok", "children": 4},
            )
        self.assertEqual(action, "ok")
        notice.assert_called_once_with(False)


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

    def test_condensed_content_uses_live_root_children(self) -> None:
        class _Slot:
            def __init__(self, children):
                self.children = children

        class _Root:
            def __init__(self, children):
                self.is_deleted = False
                self.client = object()
                self.default_slot = _Slot(children)

        previous_root = feed.activity_feed_state.condensed_root
        previous_container = feed.activity_feed_state.condensed_container
        try:
            feed.activity_feed_state.condensed_root = _Root([object()])
            self.assertTrue(feed._condensed_view_has_content())
            feed.activity_feed_state.condensed_root = _Root([])
            self.assertFalse(feed._condensed_view_has_content())
        finally:
            feed.activity_feed_state.condensed_root = previous_root
            feed.activity_feed_state.condensed_container = previous_container

    def test_view_model_keeps_raw_user_text(self) -> None:
        model = feed._condensed_view_model(
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
        self.assertEqual(model["groups"][0]["username"], "<script>x</script>")
        self.assertEqual(model["groups"][0]["lines"][0], "<img src=x onerror=alert(1)>")

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


_HITTEST_HARNESS_JS = r"""
const fs = require('fs');
const helper = fs.readFileSync(process.argv[2], 'utf8');

function rect(left, top, width, height) {
  return { left, top, width, height, right: left + width, bottom: top + height };
}
function makeEl(tag, cls, r, children) {
  const el = {
    tagName: tag, id: '', className: cls, _rect: r,
    children: children || [],
    getBoundingClientRect() { return this._rect; },
    contains(o) {
      if (o === this) return true;
      for (const c of this.children) if (c.contains(o)) return true;
      return false;
    },
    closest(sel) { return (this._closest || {})[sel] || null; },
  };
  return el;
}

function run(name, opts) {
  const kids = [];
  let y = opts.rootTop;
  for (const h of (opts.childHeights || [18])) {
    kids.push(makeEl('DIV', 'user-group', rect(opts.rootLeft, y, opts.rootWidth, h)));
    y += h;
  }
  const root = makeEl(
    'DIV', 'activity-feed-condensed-root',
    rect(opts.rootLeft, opts.rootTop, opts.rootWidth, opts.rootHeight),
    kids
  );
  const con = makeEl('DIV', 'activity-feed-condensed', opts.clip, [root]);
  root._closest = { '.activity-feed-condensed': con };
  const body = makeEl('BODY', '', rect(0, 0, opts.vw, opts.vh), [con]);
  const htmlEl = makeEl('HTML', '', rect(0, 0, opts.vw, opts.vh), [body]);
  const win = { innerWidth: opts.vw, innerHeight: opts.vh };
  const doc = {
    body, documentElement: htmlEl,
    elementFromPoint(x, y2) {
      if (opts.efpNull) return null;
      if (x < 0 || y2 < 0 || x >= opts.vw || y2 >= opts.vh) return null;
      if (opts.overlay) return opts.overlay;
      const inside = (e) => {
        const r = e.getBoundingClientRect();
        return x >= r.left && x <= r.right && y2 >= r.top && y2 <= r.bottom;
      };
      for (const k of kids) if (inside(k)) return k;
      if (inside(root)) return root;
      if (inside(con)) return con;
      return body;
    },
  };
  const gcs = () => ({
    display: 'block', visibility: 'visible', position: 'static',
    overflow: 'visible', opacity: '1',
  });
  const api = new Function(
    'window', 'document', 'Math', 'getComputedStyle',
    helper + '\n return {hit: _afHitTest};'
  )(win, doc, Math, gcs);
  const res = api.hit(root);
  return { name, verdict: res.verdict, reason: res.reason || res.verdict };
}

const overlay = makeEl('DIV', 'modal-backdrop', rect(0, 0, 1920, 1205));
const results = [
  run('logged', {
    vw: 1920, vh: 1205, rootLeft: 29, rootTop: 172, rootWidth: 1792, rootHeight: 1153,
    clip: rect(16, 163, 1818, 985), childHeights: [18, 80, 80], efpNull: true,
  }),
  run('collapsed', {
    vw: 1920, vh: 1205, rootLeft: 29, rootTop: 172, rootWidth: 1792, rootHeight: 1153,
    clip: rect(16, 163, 1818, 0), childHeights: [18], efpNull: true,
  }),
  run('occluded', {
    vw: 1920, vh: 1205, rootLeft: 29, rootTop: 172, rootWidth: 1792, rootHeight: 1153,
    clip: rect(16, 163, 1818, 985), childHeights: [18, 80], overlay,
  }),
  run('shifted', {
    vw: 1920, vh: 1173, rootLeft: 1944, rootTop: 172, rootWidth: 1792, rootHeight: 1311,
    clip: rect(1935, 163, 1810, 1345), childHeights: [15, 80, 80], efpNull: true,
  }),
];
process.stdout.write(JSON.stringify(results));
"""


class CondensedHitTestJsTests(unittest.TestCase):
    def test_logged_geometry_is_ok_when_elementfrompoint_returns_null(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper.js"
            harness = Path(tmp) / "harness.js"
            helper.write_text(feed._CONDENSED_HITTEST_JS, encoding="utf-8")
            harness.write_text(_HITTEST_HARNESS_JS, encoding="utf-8")
            completed = subprocess.run(
                [node, str(harness), str(helper)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = {row["name"]: row for row in json.loads(completed.stdout)}
        self.assertEqual(results["logged"]["verdict"], "ok")
        self.assertEqual(results["logged"]["reason"], "geometry")
        self.assertEqual(results["shifted"]["verdict"], "offscreen")
        self.assertEqual(results["collapsed"]["verdict"], "offscreen")
        self.assertEqual(results["occluded"]["verdict"], "occluded")


if __name__ == "__main__":
    unittest.main()

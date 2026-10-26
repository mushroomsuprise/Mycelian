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






class CondensedViewContractTests(unittest.TestCase):
    """Pin condensed grouping / copy / overlay behavior across the render rewrite."""

    def setUp(self) -> None:
        self._alerts = feed.activity_feed_state.live_alerts
        self._condense = feed.activity_feed_state.condense_list
        self._tab = feed.activity_feed_state.current_tab
        self._filters = dict(feed.activity_feed_state.filter_state)
        self._manager_view = feed.normalize_view(feed.view_manager.snapshot())
        self._manager_revision = feed.view_manager.revision
        self._manager_loaded = feed.view_manager._loaded
        feed.view_manager._loaded = True
        feed.view_manager.revision = 0
        feed.view_manager._view = feed.normalize_view({})
        feed.view_manager._sync_state()
        feed.activity_feed_state.live_alerts = []

    def tearDown(self) -> None:
        feed.view_manager._view = self._manager_view
        feed.view_manager.revision = self._manager_revision
        feed.view_manager._loaded = self._manager_loaded
        feed.activity_feed_state.live_alerts = self._alerts
        feed.activity_feed_state.condense_list = self._condense
        feed.activity_feed_state.current_tab = self._tab
        feed.activity_feed_state.filter_state.clear()
        feed.activity_feed_state.filter_state.update(self._filters)

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







class ActivityFeedViewManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        state = feed.activity_feed_state
        self._filters = dict(state.filter_state)
        self._condense = state.condense_list
        self._tab = state.current_tab
        self._page = state.current_page

    def tearDown(self) -> None:
        state = feed.activity_feed_state
        state.filter_state.clear()
        state.filter_state.update(self._filters)
        state.condense_list = self._condense
        state.current_tab = self._tab
        state.current_page = self._page

    def test_normalize_defaults_dropped_keys_invalid_tab_and_page(self) -> None:
        view = feed.normalize_view(
            {
                "tab": "sideways",
                "page": -4,
                "condensed": "false",
                "filters": {"follows": 0, "bits": "yes", "not_a_filter": False},
            }
        )
        self.assertEqual(view["tab"], "current")
        self.assertEqual(view["page"], 1)
        self.assertFalse(view["condensed"])
        self.assertFalse(view["filters"]["follows"])
        self.assertTrue(view["filters"]["bits"])
        self.assertTrue(view["filters"]["streaks"])
        self.assertNotIn("not_a_filter", view["filters"])
        self.assertEqual(set(view["filters"]), set(feed.FILTER_KEYS))

    def test_load_failure_falls_back_to_defaults(self) -> None:
        manager = feed.ActivityFeedViewManager()
        with patch(
            "modules.database_manager.get_data", side_effect=RuntimeError("db down")
        ):
            snapshot = manager.load()
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(snapshot["tab"], "current")
        self.assertFalse(snapshot["condensed"])
        self.assertEqual(snapshot["page"], 1)
        self.assertTrue(all(snapshot["filters"].values()))

    def test_save_writes_activity_feed_view(self) -> None:
        manager = feed.ActivityFeedViewManager()
        manager._loaded = True
        manager._view = feed.normalize_view(
            {
                "condensed": True,
                "tab": "previous",
                "page": "3",
                "filters": {"raids": False},
            }
        )
        with patch("modules.database_manager.set_data", return_value=True) as set_data:
            self.assertTrue(manager.save())
        self.assertEqual(set_data.call_args.args[0], "ActivityFeedView")
        payload = set_data.call_args.args[1]
        self.assertTrue(payload["condensed"])
        self.assertEqual(payload["tab"], "previous")
        self.assertEqual(payload["page"], 3)
        self.assertFalse(payload["filters"]["raids"])
        self.assertNotIn("revision", payload)

    def test_view_patches_save_and_broadcast_once(self) -> None:
        for patch_body in (
            {"condensed": True},
            {"tab": "previous"},
            {"page": 4},
        ):
            manager = feed.ActivityFeedViewManager()
            manager._loaded = True
            manager._view = feed.normalize_view({})
            with (
                patch.object(manager, "save", return_value=True) as save,
                patch.object(feed, "broadcast_activity_feed_view") as broadcast,
                patch.object(feed, "_apply_view_to_desktop"),
            ):
                before = manager.revision
                manager.update(patch_body)
            self.assertEqual(manager.revision, before + 1)
            save.assert_called_once()
            broadcast.assert_called_once()

    def test_same_revision_does_not_save(self) -> None:
        manager = feed.ActivityFeedViewManager()
        manager._loaded = True
        manager.revision = 4
        snapshot = manager.snapshot()
        with patch.object(manager, "save", return_value=True) as save:
            self.assertFalse(manager.apply_snapshot(snapshot))
        save.assert_not_called()
        self.assertEqual(manager.revision, 4)


if __name__ == "__main__":
    unittest.main()

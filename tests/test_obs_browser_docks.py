#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ExtraBrowserDocks parsing and Mycelian-only dock matching."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.obs_browser_docks import (  # noqa: E402
    is_dock_boot_file_url,
    list_mycelian_extra_browser_docks,
    mycelian_dock_route_from_url,
    parse_extra_browser_docks,
    pending_docks_missing_routes,
    replace_extra_browser_docks_value,
    retarget_mycelian_docks_to_boot,
    unescape_obs_ini_value,
    wake_mycelian_browser_docks,
)


SAMPLE_INI = """
[BasicWindow]
PreviewEnabled=true
ExtraBrowserDocks=[{"title": "Activity Feed", "url": "http://127.0.0.1:5000/activity_feed", "uuid": "aaa"}, {"title": "Source Controls", "url": "http://127.0.0.1:5000/source_controls", "uuid": "bbb"}, {"title": "StreamElements", "url": "https://streamelements.com/overlay/abc", "uuid": "ccc"}]
AlwaysOnTop=false
"""

ESCAPED_INI = (
    "[BasicWindow]\n"
    "ExtraBrowserDocks=[{\\n\"title\": \"Activity Feed\",\\n\"url\": "
    "\"http://127.0.0.1:5000/activity_feed\",\\n\"uuid\": \"aaa\"\\n}]\n"
)


class ParseExtraBrowserDocksTests(unittest.TestCase):
    def test_parses_user_style_json_line(self) -> None:
        docks = parse_extra_browser_docks(SAMPLE_INI)
        self.assertEqual(len(docks), 3)
        self.assertEqual(docks[0]["title"], "Activity Feed")
        self.assertEqual(
            docks[0]["url"], "http://127.0.0.1:5000/activity_feed"
        )
        self.assertEqual(docks[1]["title"], "Source Controls")

    def test_unescapes_obs_ini_newlines(self) -> None:
        self.assertIn("\n", unescape_obs_ini_value("a\\nb"))
        docks = parse_extra_browser_docks(ESCAPED_INI)
        self.assertEqual(len(docks), 1)
        self.assertEqual(docks[0]["title"], "Activity Feed")


class MycelianDockFilterTests(unittest.TestCase):
    def test_keeps_only_registered_local_routes(self) -> None:
        docks = list_mycelian_extra_browser_docks(
            5000,
            ["activity_feed", "source_controls", "alerts"],
            ini_text=SAMPLE_INI,
        )
        routes = {row["route"] for row in docks}
        titles = {row["title"] for row in docks}
        self.assertEqual(routes, {"activity_feed", "source_controls"})
        self.assertEqual(titles, {"Activity Feed", "Source Controls"})

    def test_wrong_port_is_ignored(self) -> None:
        docks = list_mycelian_extra_browser_docks(
            5001,
            ["activity_feed", "source_controls"],
            ini_text=SAMPLE_INI,
        )
        self.assertEqual(docks, [])

    def test_empty_routes_match_nothing(self) -> None:
        docks = list_mycelian_extra_browser_docks(
            5000, [], ini_text=SAMPLE_INI
        )
        self.assertEqual(docks, [])


class PendingDockTests(unittest.TestCase):
    def test_skips_routes_that_already_said_hello(self) -> None:
        docks = [
            {"title": "Activity Feed", "route": "activity_feed"},
            {"title": "Source Controls", "route": "source_controls"},
        ]
        pending = pending_docks_missing_routes(docks, ["activity_feed"])
        self.assertEqual([row["route"] for row in pending], ["source_controls"])


class DockBootUrlTests(unittest.TestCase):
    def test_file_boot_url_matches_route(self) -> None:
        url = (
            "file:///tmp/obs_dock_boot.html?port=5000&route=activity_feed"
            "#5000/activity_feed"
        )
        self.assertTrue(is_dock_boot_file_url(url))
        self.assertEqual(
            mycelian_dock_route_from_url(
                url, 5000, ["activity_feed", "source_controls"]
            ),
            "activity_feed",
        )
        self.assertIsNone(
            mycelian_dock_route_from_url(url, 5001, ["activity_feed"])
        )

    def test_hash_only_boot_url_matches_route(self) -> None:
        url = "file:///tmp/obs_dock_boot.html#5000/activity_feed"
        self.assertTrue(is_dock_boot_file_url(url))
        self.assertEqual(
            mycelian_dock_route_from_url(url, 5000, ["activity_feed"]),
            "activity_feed",
        )

    def test_retarget_only_mycelian_http_docks(self) -> None:
        docks = parse_extra_browser_docks(SAMPLE_INI)
        with patch(
            "modules.obs_browser_docks.dock_boot_file_url",
            side_effect=lambda port, route: (
                f"file:///tmp/obs_dock_boot.html?port={port}&route={route}"
            ),
        ):
            updated = retarget_mycelian_docks_to_boot(
                docks, 5000, ["activity_feed", "source_controls"]
            )
        urls = {row["title"]: row["url"] for row in updated}
        self.assertTrue(is_dock_boot_file_url(urls["Activity Feed"]))
        self.assertIn("route=activity_feed", urls["Activity Feed"])
        self.assertTrue(is_dock_boot_file_url(urls["Source Controls"]))
        self.assertEqual(
            urls["StreamElements"],
            "https://streamelements.com/overlay/abc",
        )

    def test_replace_preserves_other_ini_keys(self) -> None:
        docks = [
            {
                "title": "Activity Feed",
                "url": "file:///tmp/obs_dock_boot.html?port=5000&route=activity_feed",
                "uuid": "aaa",
            }
        ]
        rewritten = replace_extra_browser_docks_value(SAMPLE_INI, docks)
        self.assertIn("PreviewEnabled=true", rewritten)
        self.assertIn("AlwaysOnTop=false", rewritten)
        self.assertIn("obs_dock_boot.html", rewritten)
        self.assertNotIn("streamelements.com", rewritten)


class DockWakeNotifyTests(unittest.TestCase):
    def test_wake_asks_to_restart_obs_instead_of_reloading(self) -> None:
        pending = [
            {
                "title": "Activity Feed",
                "url": "http://127.0.0.1:5000/activity_feed",
                "route": "activity_feed",
            }
        ]
        with patch(
            "modules.obs_browser_docks._notify_restart_obs_for_docks"
        ) as notify:
            reloaded = wake_mycelian_browser_docks(pending)
        self.assertEqual(reloaded, [])
        notify.assert_called_once()
        self.assertEqual(notify.call_args[0][0], ["Activity Feed"])

    def test_wake_skips_boot_file_docks(self) -> None:
        pending = [
            {
                "title": "Activity Feed",
                "url": "file:///tmp/obs_dock_boot.html?port=5000&route=activity_feed",
                "route": "activity_feed",
            }
        ]
        with patch(
            "modules.obs_browser_docks._notify_restart_obs_for_docks"
        ) as notify:
            reloaded = wake_mycelian_browser_docks(pending)
        self.assertEqual(reloaded, [])
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

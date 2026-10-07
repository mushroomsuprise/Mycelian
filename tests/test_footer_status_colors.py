#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Footer badge labels and colors for each service status."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.notification_engine import (  # noqa: E402
    footer_status_display,
    footer_status_tier,
)


class FooterStatusColorTests(unittest.TestCase):
    def assert_status(
        self, service: str, raw: str, display: str, tier: str
    ) -> None:
        self.assertEqual(footer_status_display(service, raw), display, raw)
        self.assertEqual(footer_status_tier(service, raw), tier, raw)

    def test_healthy_states_are_green(self) -> None:
        self.assert_status("internet", "Online", "Online", "success")
        self.assert_status("twitch", "Connected", "Connected", "success")
        self.assert_status(
            "youtube", "Connected (3 channels)", "Connected", "success"
        )
        self.assert_status("webengine", "Running", "Running", "success")

    def test_in_progress_states_are_blue(self) -> None:
        self.assert_status("obs", "Connecting", "Connecting", "info")
        self.assert_status("obs", "Disconnecting", "Disconnecting", "info")
        self.assert_status("webengine", "Starting", "Starting", "info")
        self.assert_status("webengine", "Restarting", "Restarting", "info")
        self.assert_status("discord", "Reconnecting", "Reconnecting", "info")
        self.assert_status("internet", "Checking", "Checking", "info")
        self.assert_status("twitch", "Checking Internet", "Checking", "info")
        self.assert_status("spotify", "Token Refresh", "Connecting", "info")

    def test_inactive_states_are_gray(self) -> None:
        self.assert_status("obs", "Disconnected", "Disconnected", "muted")
        self.assert_status("twitch", "Not Configured", "Idle", "muted")
        self.assert_status("twitch", "Not Initialized", "Idle", "muted")
        self.assert_status("psn", "Not Connected", "Idle", "muted")
        self.assert_status(
            "spotify", "Authorization Required", "Idle", "muted"
        )
        self.assert_status("spotify", "Awaiting Authorization", "Idle", "muted")
        self.assert_status("spotify", "Opening Browser...", "Idle", "muted")
        self.assert_status(
            "twitch", "Configured but Not Authenticated", "Idle", "muted"
        )
        self.assert_status("youtube", "API Key Required", "Idle", "muted")
        self.assert_status("youtube", "Channel URLs Required", "Idle", "muted")
        self.assert_status("twitch", "", "Unknown", "muted")

    def test_degraded_states_are_amber(self) -> None:
        self.assert_status(
            "twitch", "Degraded (no recent events)", "Degraded", "warning"
        )
        self.assert_status(
            "twitch", "Authenticated but Disconnected", "Degraded", "warning"
        )
        self.assert_status(
            "youtube", "Partial (1/2 channels)", "Partial", "warning"
        )
        self.assert_status("webengine", "Stopped", "Stopped", "warning")
        self.assert_status("webengine", "Stalled", "Stalled", "warning")
        self.assert_status("webengine", "Overloaded", "Overloaded", "warning")
        self.assert_status("psn", "Offline", "Offline", "warning")

    def test_failures_are_red(self) -> None:
        self.assert_status("internet", "Offline", "Offline", "error")
        self.assert_status("twitch", "No Internet", "No Internet", "error")
        self.assert_status(
            "spotify", "Service Unreachable", "Unreachable", "error"
        )
        self.assert_status("webengine", "Crashed", "Crashed", "error")
        self.assert_status("webengine", "Frozen", "Frozen", "error")
        self.assert_status("streamelements", "Token expired", "Expired", "error")
        self.assert_status(
            "spotify", "Token Refresh Failed", "Expired", "error"
        )
        self.assert_status("spotify", "Token Refresh Error", "Expired", "error")
        self.assert_status(
            "streamlabs", "Authentication failed", "Auth Failed", "error"
        )
        self.assert_status("discord", "Auth Failed", "Auth Failed", "error")
        self.assert_status(
            "spotify", "Authorization Error: denied", "Auth Failed", "error"
        )
        self.assert_status(
            "spotify", "Authorization Timeout", "Auth Failed", "error"
        )
        self.assert_status(
            "youtube", "All Channels Failed", "Error", "error"
        )


if __name__ == "__main__":
    unittest.main()

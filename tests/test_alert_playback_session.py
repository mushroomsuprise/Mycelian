#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the alert playback handshake session."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.alert_playback_session import (
    ABSOLUTE_CAP_SEC,
    HEARTBEAT_SILENCE_SEC,
    NO_ACK_GRACE_SEC,
    REGISTRATION_WINDOW_SEC,
    AlertPlaybackSession,
    abort_session,
    begin_session,
    holder_route_allowed,
    queue_holder_routes_for_alert,
    reset_session_for_tests,
)
from modules.alertutils import AlertObj
from modules.spore_studio.overlay_recovery_inject import inject_overlay_recovery


class HolderRouteTests(unittest.TestCase):
    def test_exact_and_normalized_match(self) -> None:
        holders = ["alerts", "boo game"]
        self.assertTrue(holder_route_allowed("alerts", holders))
        self.assertTrue(holder_route_allowed("boo game", holders))
        self.assertTrue(holder_route_allowed("BooGame", holders))
        self.assertTrue(holder_route_allowed("boo%20game", holders))
        self.assertTrue(holder_route_allowed("/boo%20game", holders))
        self.assertFalse(holder_route_allowed("bitbar", holders))
        self.assertFalse(holder_route_allowed("combobar", holders))


class QueueHolderRoutesTests(unittest.TestCase):
    def test_normal_alert_is_alerts_only(self) -> None:
        alert = AlertObj()
        alert.alert_type = "sub"
        self.assertEqual(queue_holder_routes_for_alert(alert), ["alerts"])

    def test_hold_queue_only_includes_matched_stem(self) -> None:
        alert = AlertObj()
        alert.alert_type = "point"
        alert.alert_name = "Boo Game"
        alert.hold_queue_only = True
        with patch(
            "modules.template_config_parser.find_matching_template_routes_for_reward_title",
            return_value=["boo game", "Boo Game"],
        ):
            self.assertEqual(
                queue_holder_routes_for_alert(alert),
                ["alerts", "boo game", "Boo Game"],
            )


class AlertPlaybackSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_for_tests()
        self.t0 = 1000.0

    def tearDown(self) -> None:
        reset_session_for_tests()

    def _session(self, holders=("alerts",), duration=5.0) -> AlertPlaybackSession:
        return begin_session(
            7,
            holders,
            duration,
            delay_between_sec=0.5,
            now=self.t0,
        )

    def test_spectator_playing_and_complete_ignored(self) -> None:
        session = self._session(("alerts", "boo game"))
        self.assertFalse(
            session.note_playing("sid-bar", "bitbar", now=self.t0)
        )
        accepted, finished = session.note_complete(
            "sid-bar", "combobar", now=self.t0 + 0.1
        )
        self.assertFalse(accepted)
        self.assertFalse(finished)
        self.assertFalse(session.finished)
        self.assertEqual(session.required_sids, set())

    def test_preview_ignored(self) -> None:
        session = self._session()
        self.assertFalse(
            session.note_playing("sid-p", "alerts", is_preview=True, now=self.t0)
        )
        accepted, finished = session.note_complete(
            "sid-p", "alerts", is_preview=True, now=self.t0
        )
        self.assertFalse(accepted)
        self.assertFalse(finished)

    def test_two_holders_wait_for_slower(self) -> None:
        session = self._session(("alerts", "boo game"))
        session.note_playing("sid-alerts", "alerts", now=self.t0)
        session.note_playing("sid-boo", "boo game", now=self.t0)
        session.note_progress("sid-boo", now=self.t0 + 5)
        accepted, finished = session.note_complete(
            "sid-alerts", "alerts", now=self.t0 + 5
        )
        self.assertTrue(accepted)
        self.assertFalse(finished)
        accepted, finished = session.note_complete(
            "sid-boo", "boo game", now=self.t0 + 8
        )
        self.assertTrue(accepted)
        self.assertTrue(finished)
        self.assertEqual(session.finish_reason, "all_holders_done")

    def test_complete_without_playing_from_holder(self) -> None:
        session = self._session()
        accepted, finished = session.note_complete(
            "sid-alerts", "alerts", now=self.t0
        )
        self.assertTrue(accepted)
        self.assertTrue(finished)

    def test_dedicated_match_only(self) -> None:
        session = self._session(("alerts", "boo game"))
        self.assertFalse(session.note_playing("sid-other", "roulette", now=self.t0))
        self.assertTrue(session.note_playing("sid-boo", "boo game", now=self.t0))

    def test_disconnect_counts_as_done(self) -> None:
        session = self._session()
        session.note_playing("sid-a", "alerts", now=self.t0)
        self.assertTrue(session.note_disconnect("sid-a", now=self.t0 + 0.2))
        self.assertTrue(session.finished)

    def test_silent_sid_dropped(self) -> None:
        session = self._session()
        session.note_playing("sid-a", "alerts", now=self.t0)
        self.assertFalse(session.tick(now=self.t0 + HEARTBEAT_SILENCE_SEC - 0.1))
        self.assertTrue(session.tick(now=self.t0 + HEARTBEAT_SILENCE_SEC + 0.05))
        self.assertEqual(session.finish_reason, "all_holders_done")

    def test_progress_keeps_sid_alive(self) -> None:
        session = self._session()
        session.note_playing("sid-a", "alerts", now=self.t0)
        later = self.t0 + HEARTBEAT_SILENCE_SEC + 1
        session.note_progress("sid-a", now=later)
        self.assertFalse(session.tick(now=later + 0.1))
        self.assertFalse(session.finished)

    def test_no_ack_fallback(self) -> None:
        session = self._session(duration=2.0)
        fallback_at = self.t0 + 2.0 + 0.5 + NO_ACK_GRACE_SEC
        self.assertFalse(
            session.tick(now=self.t0 + REGISTRATION_WINDOW_SEC + 0.01)
        )
        self.assertFalse(session.tick(now=fallback_at - 0.05))
        self.assertTrue(session.tick(now=fallback_at + 0.01))
        self.assertEqual(session.finish_reason, "no_ack_fallback")

    def test_wrong_seq_does_not_use_global_session(self) -> None:
        first = self._session()
        first.note_playing("sid-a", "alerts", now=self.t0)
        second = begin_session(
            8, ["alerts"], 5.0, now=self.t0 + 1, delay_between_sec=0.5
        )
        self.assertTrue(first.finished)
        self.assertEqual(first.finish_reason, "superseded")
        accepted, finished = second.note_complete(
            "sid-a", "alerts", now=self.t0 + 1.1
        )
        self.assertTrue(accepted)
        self.assertTrue(finished)

    def test_skip_aborts(self) -> None:
        self._session()
        self.assertTrue(abort_session("skip"))
        session = self._session()
        session.force_finish("skip")
        self.assertEqual(session.finish_reason, "skip")

    def test_absolute_timeout(self) -> None:
        session = self._session()
        session.note_playing("sid-a", "alerts", now=self.t0)
        # Keep heartbeating so silence drop does not fire.
        now = self.t0 + ABSOLUTE_CAP_SEC
        session.note_progress("sid-a", now=now - 0.5)
        self.assertTrue(session.tick(now=now))
        self.assertEqual(session.finish_reason, "absolute_timeout")

    def test_shutdown_dedicated_alerts_fallback_still_holds(self) -> None:
        session = self._session(("alerts", "boo game"))
        session.note_playing("sid-alerts", "alerts", now=self.t0)
        live = self.t0 + HEARTBEAT_SILENCE_SEC + 0.05
        session.note_progress("sid-alerts", now=live)
        self.assertFalse(session.tick(now=live))
        self.assertIn("sid-alerts", session.required_sids)
        accepted, finished = session.note_complete(
            "sid-alerts", "alerts", now=live + 1
        )
        self.assertTrue(accepted)
        self.assertTrue(finished)


class OverlayInjectTests(unittest.TestCase):
    def test_injects_alert_queue_helper_after_recovery(self) -> None:
        html = (
            "<html><head>"
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>'
            "</head></html>"
        )
        out = inject_overlay_recovery(html)
        self.assertIn("overlay_recovery.js", out)
        self.assertIn("alert_queue.js", out)
        self.assertLess(out.index("overlay_recovery.js"), out.index("alert_queue.js"))

    def test_injects_alert_queue_when_recovery_already_present(self) -> None:
        html = (
            "<html><head>"
            '<script src="/assets/default_assets/overlay_recovery.js"></script>\n'
            '<script src="/assets/default_assets/template_logger.js"></script>'
            "</head></html>"
        )
        out = inject_overlay_recovery(html)
        self.assertIn("alert_queue.js", out)
        self.assertLess(out.index("overlay_recovery.js"), out.index("alert_queue.js"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for EventSub reconnecting health and keepalive log filter."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.log_filters import KeepaliveMissedFilter  # noqa: E402
from modules.twitch import Twitch_API, attempt_auto_reconnect  # noqa: E402


class EventSubReconnectingHealthTests(unittest.TestCase):
    def _api(self, *, reconnecting: bool, session=object(), ready: bool = True) -> Twitch_API:
        api = object.__new__(Twitch_API)
        api.eventsub = SimpleNamespace(
            _is_reconnecting=reconnecting,
            active_session=session,
        )
        api.is_connected = True
        api._eventsub_subscriptions_ready = ready
        return api

    def test_reconnecting_counts_as_live(self) -> None:
        api = self._api(reconnecting=True, session=None, ready=False)
        self.assertTrue(api._eventsub_is_reconnecting())
        self.assertTrue(api.is_eventsub_live())

    def test_idle_without_session_is_not_live(self) -> None:
        api = self._api(reconnecting=False, session=None, ready=False)
        self.assertFalse(api._eventsub_is_reconnecting())
        self.assertFalse(api.is_eventsub_live())

    def test_auto_reconnect_skipped_while_library_reconnecting(self) -> None:
        api = self._api(reconnecting=True)
        api.reconnect = MagicMock()
        with (
            patch("modules.twitch.twitch_has_tokens_configured", return_value=True),
            patch("modules.twitch.twitch_api", api),
        ):
            self.assertFalse(attempt_auto_reconnect())
        api.reconnect.assert_not_called()


class KeepaliveMissedFilterTests(unittest.TestCase):
    def test_first_warning_passes_then_debug(self) -> None:
        filt = KeepaliveMissedFilter()
        first = logging.LogRecord(
            "twitchAPI.eventsub.websocket",
            logging.WARNING,
            __file__,
            1,
            "keepalive missed, connection lost. reconnecting...",
            (),
            None,
        )
        self.assertTrue(filt.filter(first))
        self.assertEqual(first.levelno, logging.WARNING)

        second = logging.LogRecord(
            "twitchAPI.eventsub.websocket",
            logging.WARNING,
            __file__,
            1,
            "keepalive missed, connection lost. reconnecting...",
            (),
            None,
        )
        self.assertTrue(filt.filter(second))
        self.assertEqual(second.levelno, logging.DEBUG)

    def test_unrelated_warnings_pass(self) -> None:
        filt = KeepaliveMissedFilter()
        record = logging.LogRecord(
            "twitchAPI.eventsub.websocket",
            logging.WARNING,
            __file__,
            1,
            "connection attempt failed, retry in 1 seconds...",
            (),
            None,
        )
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()

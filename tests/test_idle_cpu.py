#!/usr/bin/env python3
"""Idle waits block until work arrives, and a queued wake is not missed."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.alert_processor import (  # noqa: E402
    _ALERT_WAKE,
    wait_for_alert_queue,
    wake_alert_queue,
)
from modules.web_engine import WebEngine  # noqa: E402


class EmitWakeTests(unittest.TestCase):
    def test_pipe_wakes_the_wait(self) -> None:
        engine = object.__new__(WebEngine)
        engine._open_emit_wake_pipe()
        self.addCleanup(engine._close_emit_wake_pipe)
        started = time.monotonic()

        def kick() -> None:
            time.sleep(0.05)
            engine._signal_emit_wake()

        threading.Thread(target=kick, daemon=True).start()
        woke = engine._wait_for_emit_wake(1.0)
        self.assertTrue(woke)
        self.assertLess(time.monotonic() - started, 0.5)


class AlertQueueWaitTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ALERT_WAKE.set()
        _ALERT_WAKE.clear()

    def test_idle_wait_returns_when_woken(self) -> None:
        _ALERT_WAKE.clear()
        started = time.monotonic()

        def kick() -> None:
            time.sleep(0.05)
            wake_alert_queue()

        threading.Thread(target=kick, daemon=True).start()
        wait_for_alert_queue(playing=False, paused=False)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_playing_wait_uses_a_short_timeout(self) -> None:
        with patch.object(_ALERT_WAKE, "wait", return_value=True) as waited:
            with patch.object(_ALERT_WAKE, "clear"):
                wait_for_alert_queue(playing=True, paused=False)
        waited.assert_called_once_with(timeout=0.2)

    def test_paused_wait_uses_half_second(self) -> None:
        with patch.object(_ALERT_WAKE, "wait", return_value=True) as waited:
            with patch.object(_ALERT_WAKE, "clear"):
                wait_for_alert_queue(playing=False, paused=True)
        waited.assert_called_once_with(timeout=0.5)


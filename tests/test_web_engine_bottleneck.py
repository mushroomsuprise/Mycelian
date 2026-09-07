#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for overlay bottleneck evaluation and Overloaded hysteresis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.web_engine import WebEngine, evaluate_bottleneck  # noqa: E402


def _metrics(**overrides):
    base = {
        "heartbeat_stale_sec": 0.0,
        "hub_tick_drift_ms": 0.0,
        "http_p95_ms": 15.0,
        "slow_requests_recent": 0,
        "template_control_oldest_age_ms": 0.0,
        "template_control_queue_depth": 0,
        "twitch_api_oldest_age_ms": 0.0,
        "twitch_api_queue_depth": 0,
        "template_control_drops_60s": 0,
        "twitch_api_drops_60s": 0,
        "template_control_worker_alive": True,
        "net_connects_30s": 0,
        "socket_emit_errors_60s": 0,
    }
    base.update(overrides)
    return base


class EvaluateBottleneckTests(unittest.TestCase):
    def test_short_helix_wait_is_not_bottlenecked(self) -> None:
        result = evaluate_bottleneck(
            _metrics(twitch_api_oldest_age_ms=3000.0, twitch_api_queue_depth=2)
        )
        self.assertFalse(result["bottlenecked"])
        self.assertEqual(result["hard_reasons"], [])

    def test_deep_old_helix_queue_is_hard_trip(self) -> None:
        result = evaluate_bottleneck(
            _metrics(twitch_api_oldest_age_ms=16000.0, twitch_api_queue_depth=10)
        )
        self.assertTrue(result["bottlenecked"])
        self.assertTrue(
            any("twitch_api_oldest_age_ms" in r for r in result["hard_reasons"])
        )

    def test_hub_drift_is_hard_trip(self) -> None:
        result = evaluate_bottleneck(_metrics(hub_tick_drift_ms=4000.0))
        self.assertTrue(result["bottlenecked"])
        self.assertTrue(any("hub_tick_drift_ms" in r for r in result["hard_reasons"]))

    def test_http_p95_is_hard_trip(self) -> None:
        result = evaluate_bottleneck(_metrics(http_p95_ms=800.0))
        self.assertTrue(result["bottlenecked"])


class OverloadHysteresisTests(unittest.TestCase):
    def _engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng._BN_OVERLOAD_HOLD_SEC = 15.0
        eng._BN_OVERLOAD_CLEAR_SEC = 10.0
        eng._bottleneck_since_mono = None
        eng._clear_since_mono = None
        eng._held_overloaded = False
        return eng

    def test_blip_under_hold_does_not_latch(self) -> None:
        eng = self._engine()
        self.assertFalse(eng._apply_overload_hysteresis(True, now_mono=100.0))
        self.assertFalse(eng._apply_overload_hysteresis(True, now_mono=114.0))
        self.assertFalse(eng._apply_overload_hysteresis(False, now_mono=116.0))

    def test_sustained_bottleneck_latches_after_hold(self) -> None:
        eng = self._engine()
        self.assertFalse(eng._apply_overload_hysteresis(True, now_mono=100.0))
        self.assertTrue(eng._apply_overload_hysteresis(True, now_mono=115.0))

    def test_clear_window_holds_then_releases(self) -> None:
        eng = self._engine()
        eng._apply_overload_hysteresis(True, now_mono=100.0)
        self.assertTrue(eng._apply_overload_hysteresis(True, now_mono=115.0))
        self.assertTrue(eng._apply_overload_hysteresis(False, now_mono=116.0))
        self.assertTrue(eng._apply_overload_hysteresis(False, now_mono=125.0))
        self.assertFalse(eng._apply_overload_hysteresis(False, now_mono=126.0))

    def test_return_during_clear_keeps_latched(self) -> None:
        eng = self._engine()
        eng._apply_overload_hysteresis(True, now_mono=100.0)
        self.assertTrue(eng._apply_overload_hysteresis(True, now_mono=115.0))
        self.assertTrue(eng._apply_overload_hysteresis(False, now_mono=116.0))
        self.assertTrue(eng._apply_overload_hysteresis(True, now_mono=118.0))


if __name__ == "__main__":
    unittest.main()

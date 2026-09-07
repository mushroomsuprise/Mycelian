#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for overlay startup sync (client watchdog) gates and OBS filtering."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import obs_service as obs_mod  # noqa: E402
from modules.obs_service import ObsServiceImpl  # noqa: E402
from modules.web_engine import (  # noqa: E402
    WebEngine,
    _claim_obs_browser_refresh_this_process,
    _claim_obs_dock_reload_this_process,
    _obs_browser_refresh_already_done,
    _obs_dock_reload_already_done,
    _reset_obs_browser_refresh_gate_for_tests,
    _reset_obs_dock_reload_gate_for_tests,
)


class FakeReqClient:
    def __init__(self, rows, urls):
        self.rows = rows
        self.urls = urls
        self.pressed = []

    def get_input_list(self, kind=None):
        if kind == "browser_source":
            return {
                "inputs": [
                    row
                    for row in self.rows
                    if str(row.get("input_kind") or "").startswith("browser_source")
                ]
            }
        return {"inputs": list(self.rows)}

    def get_input_settings(self, name):
        return {"inputSettings": {"url": self.urls.get(name, "")}}

    def press_input_properties_button(self, input_name, prop_name):
        self.pressed.append((input_name, prop_name))


class ObsBrowserRefreshGateTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_obs_browser_refresh_gate_for_tests()

    def tearDown(self) -> None:
        _reset_obs_browser_refresh_gate_for_tests()

    def test_claim_only_succeeds_once(self) -> None:
        self.assertFalse(_obs_browser_refresh_already_done())
        self.assertTrue(_claim_obs_browser_refresh_this_process())
        self.assertTrue(_obs_browser_refresh_already_done())
        self.assertFalse(_claim_obs_browser_refresh_this_process())


class OverlayClientCountTests(unittest.TestCase):
    def _engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng._socket_connected_lock = threading.Lock()
        eng._socket_connected_count = 0
        eng._preview_sessions_lock = threading.Lock()
        eng._preview_iframe_tokens = {}
        return eng

    def test_preview_sids_are_not_overlay_clients(self) -> None:
        eng = self._engine()
        eng._socket_connected_count = 3
        eng._preview_iframe_tokens = {"sid-a": "tok", "sid-b": "tok"}
        self.assertEqual(eng._overlay_socket_connected_count(), 1)

    def test_all_preview_counts_as_zero_overlay_clients(self) -> None:
        eng = self._engine()
        eng._socket_connected_count = 2
        eng._preview_iframe_tokens = {"sid-a": "tok", "sid-b": "tok"}
        self.assertEqual(eng._overlay_socket_connected_count(), 0)


class StartupObsEnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_obs_browser_refresh_gate_for_tests()

    def tearDown(self) -> None:
        _reset_obs_browser_refresh_gate_for_tests()

    def _engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng.port = 5000
        eng._registered_template_routes = {"alerts", "chat"}
        return eng

    def test_skips_when_obs_disconnected(self) -> None:
        eng = self._engine()
        mock_obs = MagicMock()
        mock_obs.is_connected.return_value = False
        with patch("modules.obs_service.obs_service", mock_obs):
            self.assertFalse(eng._maybe_enqueue_startup_obs_refresh())
        mock_obs.enqueue_refresh_mycelian_browser_sources.assert_not_called()
        self.assertFalse(_obs_browser_refresh_already_done())

    def test_skips_when_no_template_routes(self) -> None:
        eng = self._engine()
        eng._registered_template_routes = set()
        self.assertFalse(eng._maybe_enqueue_startup_obs_refresh())
        self.assertFalse(_obs_browser_refresh_already_done())

    def test_second_enqueue_is_noop(self) -> None:
        eng = self._engine()
        mock_obs = MagicMock()
        mock_obs.is_connected.return_value = True
        with patch("modules.obs_service.obs_service", mock_obs):
            self.assertTrue(eng._maybe_enqueue_startup_obs_refresh())
            self.assertTrue(eng._maybe_enqueue_startup_obs_refresh())
        self.assertEqual(
            mock_obs.enqueue_refresh_mycelian_browser_sources.call_count, 1
        )
        mock_obs.enqueue_refresh_mycelian_browser_sources.assert_called_once_with(
            5000, ["alerts", "chat"]
        )


class StartupOverlayRecoveryTests(unittest.TestCase):
    def test_recovery_emits_once(self) -> None:
        eng = object.__new__(WebEngine)
        eng._startup_overlay_recovery_sent = False
        eng.broadcast_overlay_recovery = MagicMock(return_value=True)
        eng._emit_startup_overlay_recovery_once()
        eng._emit_startup_overlay_recovery_once()
        eng.broadcast_overlay_recovery.assert_called_once_with("web_engine", "startup")


class MycelianBrowserRefreshOpTests(unittest.TestCase):
    def test_refreshes_only_mycelian_urls(self) -> None:
        svc = ObsServiceImpl()
        client = FakeReqClient(
            rows=[
                {"input_name": "Alerts", "input_kind": "browser_source"},
                {"input_name": "StreamElements", "input_kind": "browser_source"},
                {"input_name": "YouTube", "input_kind": "browser_source"},
                {"input_name": "Mic", "input_kind": "wasapi_input_capture"},
            ],
            urls={
                "Alerts": "http://127.0.0.1:5000/alerts",
                "StreamElements": "https://streamelements.com/overlay/abc",
                "YouTube": "https://www.youtube.com/embed/xyz",
            },
        )
        svc._req_client = client
        with patch.object(obs_mod, "_BROWSER_REFRESH_STAGGER_SEC", 0):
            payload = svc._refresh_mycelian_browser_sources_locked(
                5000, ["alerts", "chat"]
            )
        self.assertEqual(payload["refreshed"], ["Alerts"])
        skipped_names = {row["source_name"] for row in payload["skipped"]}
        self.assertIn("StreamElements", skipped_names)
        self.assertIn("YouTube", skipped_names)
        self.assertNotIn("Mic", skipped_names)
        self.assertEqual(client.pressed, [("Alerts", "refreshnocache")])

    def test_empty_routes_press_nothing(self) -> None:
        svc = ObsServiceImpl()
        client = FakeReqClient(
            rows=[{"input_name": "Alerts", "input_kind": "browser_source"}],
            urls={"Alerts": "http://127.0.0.1:5000/alerts"},
        )
        svc._req_client = client
        payload = svc._refresh_mycelian_browser_sources_locked(5000, [])
        self.assertEqual(payload["refreshed"], [])
        self.assertEqual(client.pressed, [])

    def test_enqueue_fails_fast_when_disconnected(self) -> None:
        svc = ObsServiceImpl()
        fut = svc.enqueue_refresh_mycelian_browser_sources(5000, ["alerts"])
        ok, msg, _payload = fut.result(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("not connected", msg.lower())


class ObsDockReloadGateTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_obs_dock_reload_gate_for_tests()

    def tearDown(self) -> None:
        _reset_obs_dock_reload_gate_for_tests()

    def test_claim_only_succeeds_once(self) -> None:
        self.assertFalse(_obs_dock_reload_already_done())
        self.assertTrue(_claim_obs_dock_reload_this_process())
        self.assertTrue(_obs_dock_reload_already_done())
        self.assertFalse(_claim_obs_dock_reload_this_process())


class OverlayHelloRouteTests(unittest.TestCase):
    def _engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng._socket_connected_lock = threading.Lock()
        eng._preview_sessions_lock = threading.Lock()
        eng._preview_iframe_tokens = {}
        eng._overlay_hello_routes = {}
        return eng

    def test_hello_records_template_route(self) -> None:
        eng = self._engine()
        eng._register_overlay_hello("sid-1", "/activity_feed")
        self.assertEqual(eng._overlay_connected_routes(), {"activity_feed"})
        eng._unregister_overlay_hello("sid-1")
        self.assertEqual(eng._overlay_connected_routes(), set())

    def test_preview_sid_hello_is_ignored(self) -> None:
        eng = self._engine()
        eng._preview_iframe_tokens = {"sid-preview": "tok"}
        eng._register_overlay_hello("sid-preview", "/activity_feed")
        self.assertEqual(eng._overlay_connected_routes(), set())


class StartupLoopDockWakeTests(unittest.TestCase):
    def test_connected_overlay_clients_still_wake_docks(self) -> None:
        eng = object.__new__(WebEngine)
        eng.is_running = True
        eng.socketio = MagicMock()
        eng.socketio.sleep = lambda _s: None
        eng._STARTUP_CLIENT_SYNC_SETTLE_SEC = 0
        eng._STARTUP_CLIENT_SYNC_OBS_WAIT_SEC = 30
        eng._STARTUP_CLIENT_SYNC_POLL_SEC = 0
        eng._overlay_socket_connected_count = lambda: 2
        eng._emit_startup_overlay_recovery_once = MagicMock()
        eng._maybe_enqueue_startup_obs_refresh = MagicMock(return_value=True)
        eng._maybe_enqueue_startup_dock_wake = MagicMock(return_value=True)
        eng._startup_client_sync_loop()
        eng._emit_startup_overlay_recovery_once.assert_called()
        eng._maybe_enqueue_startup_obs_refresh.assert_not_called()
        eng._maybe_enqueue_startup_dock_wake.assert_called()


class StartupDockWakeEnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_obs_dock_reload_gate_for_tests()

    def tearDown(self) -> None:
        _reset_obs_dock_reload_gate_for_tests()

    def _engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng.port = 5000
        eng._registered_template_routes = {"activity_feed", "source_controls"}
        eng._socket_connected_lock = threading.Lock()
        eng._overlay_hello_routes = {}
        eng._dock_wake_grace_done = False
        return eng

    def test_skips_when_no_mycelian_docks(self) -> None:
        eng = self._engine()
        with patch(
            "modules.obs_browser_docks.list_mycelian_extra_browser_docks",
            return_value=[],
        ), patch("modules.obs_browser_docks.ensure_dock_boot_html"), patch(
            "modules.obs_browser_docks.set_dock_boot_context"
        ):
            self.assertTrue(eng._maybe_enqueue_startup_dock_wake())
        self.assertFalse(_obs_dock_reload_already_done())

    def test_waits_for_obs_then_wakes_once(self) -> None:
        eng = self._engine()
        docks = [
            {
                "title": "Activity Feed",
                "url": "http://127.0.0.1:5000/activity_feed",
                "uuid": "aaa",
                "route": "activity_feed",
            }
        ]
        with patch(
            "modules.obs_browser_docks.list_mycelian_extra_browser_docks",
            return_value=docks,
        ), patch(
            "modules.obs_browser_docks.obs_appears_running",
            return_value=True,
        ), patch(
            "modules.obs_browser_docks.ensure_dock_boot_html",
        ), patch(
            "modules.obs_browser_docks.set_dock_boot_context",
        ), patch(
            "modules.obs_browser_docks.wake_mycelian_browser_docks",
        ) as wake, patch(
            "threading.Thread"
        ) as thread_cls:
            self.assertFalse(eng._maybe_enqueue_startup_dock_wake())
            self.assertFalse(_obs_dock_reload_already_done())
            self.assertTrue(eng._maybe_enqueue_startup_dock_wake())
            self.assertTrue(_obs_dock_reload_already_done())
            self.assertTrue(eng._maybe_enqueue_startup_dock_wake())
            thread_cls.assert_called_once()
            wake.assert_not_called()

    def test_skips_wake_when_dock_route_already_helloed(self) -> None:
        eng = self._engine()
        eng._overlay_hello_routes = {"sid-1": "activity_feed"}
        docks = [
            {
                "title": "Activity Feed",
                "url": "http://127.0.0.1:5000/activity_feed",
                "uuid": "aaa",
                "route": "activity_feed",
            }
        ]
        with patch(
            "modules.obs_browser_docks.list_mycelian_extra_browser_docks",
            return_value=docks,
        ), patch("modules.obs_browser_docks.ensure_dock_boot_html"), patch(
            "modules.obs_browser_docks.set_dock_boot_context"
        ), patch(
            "modules.obs_browser_docks.obs_appears_running",
            return_value=True,
        ), patch("threading.Thread") as thread_cls:
            self.assertTrue(eng._maybe_enqueue_startup_dock_wake())
        thread_cls.assert_not_called()
        self.assertFalse(_obs_dock_reload_already_done())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for overlay port conflict helpers (foreign PID / errno 48)."""

from __future__ import annotations

import errno
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.web_engine import (  # noqa: E402
    WebEngine,
    _cmdline_looks_like_mycelian,
    _is_addr_in_use,
)


class AddrInUseTests(unittest.TestCase):
    def test_errno_eaddrinuse(self) -> None:
        self.assertTrue(_is_addr_in_use(OSError(errno.EADDRINUSE, "Address already in use")))

    def test_macos_errno_48_text(self) -> None:
        self.assertTrue(
            _is_addr_in_use(OSError(48, "Address already in use: ('0.0.0.0', 5000)"))
        )
        self.assertTrue(
            _is_addr_in_use(Exception("[Errno 48] Address already in use: ('0.0.0.0', 5000)"))
        )

    def test_windows_10048(self) -> None:
        self.assertTrue(_is_addr_in_use(Exception("WinError 10048")))
        exc = OSError("bind failed")
        exc.winerror = 10048  # type: ignore[attr-defined]
        self.assertTrue(_is_addr_in_use(exc))

    def test_unrelated_error(self) -> None:
        self.assertFalse(_is_addr_in_use(OSError(errno.ECONNREFUSED, "refused")))
        self.assertFalse(_is_addr_in_use(RuntimeError("boom")))


class CmdlineMycelianTests(unittest.TestCase):
    def test_controlce_denied(self) -> None:
        self.assertFalse(
            _cmdline_looks_like_mycelian(
                "ControlCe /System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter"
            )
        )

    def test_airplay_denied(self) -> None:
        self.assertFalse(_cmdline_looks_like_mycelian("/usr/libexec/AirPlayXPCHelper"))

    def test_mycelian_allowed(self) -> None:
        self.assertTrue(
            _cmdline_looks_like_mycelian(
                "python /Users/me/Documents/Python Apps/Mycelian/main.py"
            )
        )

    def test_empty_denied(self) -> None:
        self.assertFalse(_cmdline_looks_like_mycelian(""))


class OwnOverlayPidTests(unittest.TestCase):
    def _engine(self) -> WebEngine:
        # Avoid full Flask/SocketIO init — construct a bare instance shell.
        eng = object.__new__(WebEngine)
        eng.port = 5000
        eng.host = "0.0.0.0"
        return eng

    def test_current_pid_is_own(self) -> None:
        eng = self._engine()
        with patch.object(eng, "_probe_health_endpoint", return_value=False):
            self.assertTrue(eng._is_own_overlay_pid(os.getpid()))

    def test_foreign_pid_without_health_denied(self) -> None:
        eng = self._engine()
        with (
            patch.object(eng, "_probe_health_endpoint", return_value=False),
            patch(
                "modules.web_engine._process_cmdline",
                return_value="ControlCe ... ControlCenter",
            ),
        ):
            self.assertFalse(eng._is_own_overlay_pid(89098))

    def test_health_probe_alone_does_not_allow_terminate(self) -> None:
        # /api/health may be answered by another, healthy Mycelian instance; the
        # end-user log showed a hung instance killing its healthy replacement.
        eng = self._engine()
        with (
            patch.object(eng, "_probe_health_endpoint", return_value=True),
            patch("modules.web_engine._process_cmdline", return_value=""),
        ):
            self.assertFalse(eng._is_own_overlay_pid(12345))

    def test_restart_never_kills_other_mycelian(self) -> None:
        eng = self._engine()
        eng._log_port_holder_hint = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(eng, "_extract_listening_pid", return_value=4242),
            patch(
                "modules.web_engine._process_cmdline",
                return_value='"Mycelian.exe","4242","Console","1","300,000 K"',
            ),
            patch("modules.web_engine.subprocess.run") as run,
            patch("os.kill") as kill,
        ):
            self.assertFalse(eng._try_terminate_stale_port_holder())
            run.assert_not_called()
            kill.assert_not_called()

    def test_startup_terminates_hung_leftover_mycelian(self) -> None:
        eng = self._engine()
        eng._try_terminate_stale_port_holder = MagicMock(return_value=True)  # type: ignore[method-assign]
        open_states = iter([True, False])
        with (
            patch.object(eng, "_port_is_open", side_effect=lambda: next(open_states, False)),
            patch.object(eng, "_probe_health_endpoint", return_value=False),
            patch.object(eng, "_extract_listening_pid", return_value=4242),
            patch(
                "modules.web_engine._process_cmdline",
                return_value='"Mycelian.exe","4242","Console","1","300,000 K"',
            ),
            patch("modules.web_engine.time.sleep"),
        ):
            eng.prepare_port_for_startup()
        eng._try_terminate_stale_port_holder.assert_called_once_with(
            allow_other_process=True
        )

    def test_startup_leaves_foreign_holder_alone(self) -> None:
        eng = self._engine()
        eng._try_terminate_stale_port_holder = MagicMock()  # type: ignore[method-assign]
        eng._log_port_holder_hint = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(eng, "_port_is_open", return_value=True),
            patch.object(eng, "_probe_health_endpoint", return_value=False),
            patch.object(eng, "_extract_listening_pid", return_value=777),
            patch("modules.web_engine._process_cmdline", return_value="ControlCe"),
        ):
            eng.prepare_port_for_startup()
        eng._try_terminate_stale_port_holder.assert_not_called()

    def test_try_terminate_skips_foreign(self) -> None:
        eng = self._engine()
        eng._close_listener = MagicMock()  # type: ignore[method-assign]
        eng._join_known_server_threads = MagicMock()  # type: ignore[method-assign]
        eng._log_port_holder_hint = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(eng, "_extract_listening_pid", return_value=89098),
            patch.object(eng, "_is_own_overlay_pid", return_value=False),
            patch("os.kill") as kill,
        ):
            self.assertFalse(eng._try_terminate_stale_port_holder())
            kill.assert_not_called()

    def test_prepare_port_for_restart_skips_kill_when_not_mycelian(self) -> None:
        eng = self._engine()
        eng._aggressive_stop = MagicMock()  # type: ignore[method-assign]
        eng._try_terminate_stale_port_holder = MagicMock(return_value=False)  # type: ignore[method-assign]
        eng._log_port_holder_hint = MagicMock()  # type: ignore[method-assign]
        eng._wait_for_port_free = MagicMock(return_value=False)  # type: ignore[method-assign]
        with (
            patch.object(eng, "_port_is_open", return_value=True),
            patch.object(eng, "_probe_health_endpoint", return_value=False),
        ):
            eng._prepare_port_for_restart()
        eng._aggressive_stop.assert_not_called()
        eng._try_terminate_stale_port_holder.assert_not_called()



class RestartRecoveryTests(unittest.TestCase):
    def _engine(self) -> WebEngine:
        import threading

        eng = object.__new__(WebEngine)
        eng.port = 5000
        eng.host = "0.0.0.0"
        eng.server_thread = None
        eng._known_server_threads = []
        eng._abandoned_server_threads = []
        eng._socket_connected_lock = threading.Lock()
        eng._socket_connected_count = 0
        eng._overlay_hello_routes = {}
        eng._restart_lock = threading.Lock()
        eng._restart_attempts = 1
        eng._restart_giveup_notified = False
        eng._last_run_error = None
        return eng

    def test_frozen_thread_joined_once_then_abandoned(self) -> None:
        eng = self._engine()
        stuck = MagicMock()
        stuck.is_alive.return_value = True
        stuck.name = "WebEngine"
        eng._known_server_threads = [stuck]
        eng._join_known_server_threads(0.01)
        eng._join_known_server_threads(0.01)
        stuck.join.assert_called_once()
        self.assertEqual(eng._known_server_threads, [])
        self.assertEqual(eng._abandoned_server_threads, [stuck])

    def test_client_tracking_reset(self) -> None:
        eng = self._engine()
        eng._socket_connected_count = 88
        eng._overlay_hello_routes = {"sid": "/alerts"}
        eng._reset_client_tracking()
        self.assertEqual(eng._socket_connected_count, 0)
        self.assertEqual(eng._overlay_hello_routes, {})

    def test_own_frozen_listener_gives_up_instead_of_foreign_wait(self) -> None:
        eng = self._engine()
        eng._try_terminate_stale_port_holder = MagicMock(return_value=False)  # type: ignore[method-assign]
        eng._notify_restart_giveup = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(eng, "_port_is_open", return_value=True),
            patch.object(eng, "_extract_listening_pid", return_value=os.getpid()),
        ):
            self.assertIs(eng._release_own_listener_or_give_up(), False)
        eng._notify_restart_giveup.assert_called_once()
        self.assertEqual(eng._restart_attempts, WebEngine._MAX_RESTART_ATTEMPTS)

    def test_other_holder_is_not_treated_as_own(self) -> None:
        eng = self._engine()
        with (
            patch.object(eng, "_port_is_open", return_value=True),
            patch.object(eng, "_extract_listening_pid", return_value=os.getpid() + 1),
        ):
            self.assertIsNone(eng._release_own_listener_or_give_up())


if __name__ == "__main__":
    unittest.main()

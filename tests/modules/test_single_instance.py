#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Single-instance guard: focus a live copy, replace a hung one, never kill a busy one."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import single_instance as si  # noqa: E402


def _fake_running_copy(reply: str) -> socket.socket:
    """A listener that answers every focus request with *reply*."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(1024)
                    conn.sendall((reply + "\n").encode())
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return srv


@unittest.skipIf(sys.platform == "win32", "flock-based tests; Windows uses a named mutex")
class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"MYCELIAN_DATA_DIR": self._tmp.name})
        self._env.start()
        si.release()
        self._holder = None
        self._servers: list[socket.socket] = []

    def tearDown(self) -> None:
        si.release()
        if self._holder is not None:
            self._holder.close()
        for srv in self._servers:
            srv.close()
        self._env.stop()
        self._tmp.cleanup()

    def _hold_lock_as_other_copy(self, reply: str | None, pid: int = 4242) -> None:
        import fcntl

        data = Path(self._tmp.name) / "data"
        data.mkdir(parents=True, exist_ok=True)
        self._holder = open(data / "instance.lock", "a+")
        fcntl.flock(self._holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        port = 0
        if reply is not None:
            srv = _fake_running_copy(reply)
            self._servers.append(srv)
            port = srv.getsockname()[1]
        (data / "instance.json").write_text(
            json.dumps({"pid": pid, "port": port, "token": "t"}), encoding="utf-8"
        )

    def _release_other_copy(self, *_args) -> None:
        self._holder.close()
        self._holder = None

    def test_first_launch_takes_lock_and_publishes_listener(self) -> None:
        self.assertTrue(si.ensure_single_instance())
        info = si.read_instance_info()
        self.assertEqual(info["pid"], os.getpid())
        self.assertGreater(info["port"], 0)

    def test_second_launch_exits_when_running_copy_answers(self) -> None:
        self._hold_lock_as_other_copy("ok")
        with patch.object(si, "_terminate") as terminate:
            self.assertFalse(si.ensure_single_instance())
        terminate.assert_not_called()

    def test_second_launch_exits_while_running_copy_is_starting(self) -> None:
        self._hold_lock_as_other_copy("starting")
        with patch.object(si, "_terminate") as terminate:
            self.assertFalse(si.ensure_single_instance())
        terminate.assert_not_called()

    def test_hung_copy_is_replaced_after_confirm_window(self) -> None:
        self._hold_lock_as_other_copy("busy")
        with (
            patch.object(si, "HUNG_CONFIRM_SEC", 0.2),
            patch.object(si, "_pid_alive", return_value=True),
            patch.object(si, "_pid_is_mycelian", return_value=True),
            patch.object(si, "_terminate", side_effect=self._release_other_copy) as terminate,
        ):
            self.assertTrue(si.ensure_single_instance())
        terminate.assert_called_once_with(4242)
        self.assertEqual(si.read_instance_info()["pid"], os.getpid())

    def test_unidentified_holder_is_never_killed(self) -> None:
        self._hold_lock_as_other_copy(None)
        with (
            patch.object(si, "HUNG_CONFIRM_SEC", 0.2),
            patch.object(si, "TAKEOVER_WAIT_SEC", 0.2),
            patch.object(si, "_pid_alive", return_value=True),
            patch.object(si, "_pid_is_mycelian", return_value=False),
            patch.object(si, "_terminate") as terminate,
        ):
            # Starts anyway rather than leaving the user with nothing.
            self.assertTrue(si.ensure_single_instance())
        terminate.assert_not_called()

    def test_copy_that_exits_during_wait_is_not_killed(self) -> None:
        self._hold_lock_as_other_copy("busy")
        timer = threading.Timer(0.3, self._release_other_copy)
        timer.start()
        try:
            with (
                patch.object(si, "HUNG_CONFIRM_SEC", 5.0),
                patch.object(si, "_terminate") as terminate,
            ):
                self.assertTrue(si.ensure_single_instance())
            terminate.assert_not_called()
        finally:
            timer.cancel()

    def test_listener_rejects_wrong_token_and_reports_state(self) -> None:
        self.assertTrue(si.ensure_single_instance())
        info = si.read_instance_info()
        with patch.object(si, "_ui_state", return_value="busy"):
            self.assertEqual(si.request_focus(dict(info, token="wrong"), timeout=1.0), "timeout")
            self.assertEqual(si.request_focus(info, timeout=2.0), "busy")
        handled = threading.Event()
        si.set_focus_handler(handled.set)
        try:
            with (
                patch.object(si, "_ui_state", return_value="ok"),
                patch.object(si, "_window_process_alive", return_value=True),
            ):
                self.assertEqual(si.request_focus(info, timeout=2.0), "ok")
            self.assertTrue(handled.wait(2.0))
            with (
                patch.object(si, "_ui_state", return_value="ok"),
                patch.object(si, "_window_process_alive", return_value=False),
            ):
                self.assertEqual(si.request_focus(info, timeout=2.0), "nowindow")
        finally:
            si.set_focus_handler(None)


if __name__ == "__main__":
    unittest.main()

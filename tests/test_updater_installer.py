#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the update helper launch vs shutdown child reaping."""

from __future__ import annotations

import multiprocessing
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import shutdown, update_sync, updater  # noqa: E402


SLEEP_SNIPPET = "import time; time.sleep(60)"


def _tree_pids(root_pid: int) -> set[int]:
    pids = {root_pid}
    try:
        import psutil

        for child in psutil.Process(root_pid).children(recursive=True):
            pids.add(int(child.pid))
    except Exception:
        pass
    return pids


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", SLEEP_SNIPPET],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _psutil_can_list_children() -> bool:
    try:
        import psutil

        list(psutil.Process().children(recursive=True))
        return True
    except Exception:
        return False


class _FakeProc:
    def __init__(self, pid: int, child_pids: list[int] | None = None) -> None:
        self.pid = pid
        self._child_pids = list(child_pids or [])
        self.running = True
        self.kill_calls = 0

    def children(self, recursive: bool = True):
        return [_FakeProc(pid) for pid in self._child_pids]

    def is_running(self) -> bool:
        return self.running

    def kill(self) -> None:
        self.kill_calls += 1
        self.running = False


class UpdaterInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        shutdown._protected_child_pids.clear()

    def tearDown(self) -> None:
        shutdown._protected_child_pids.clear()

    def test_windows_helper_creationflags_include_breakaway(self) -> None:
        flags = update_sync._windows_creationflags(breakaway=True)
        self.assertTrue(flags & update_sync._CREATE_NEW_PROCESS_GROUP)
        self.assertEqual(flags & update_sync._DETACHED_PROCESS, 0)
        self.assertTrue(flags & update_sync._CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(flags & update_sync._CREATE_NO_WINDOW)
        without = update_sync._windows_creationflags(breakaway=False)
        self.assertEqual(without & update_sync._CREATE_BREAKAWAY_FROM_JOB, 0)
        self.assertEqual(without & update_sync._DETACHED_PROCESS, 0)

    def test_windows_helper_passes_breakaway_flags(self) -> None:
        captured: dict = {}
        fake = mock.Mock()
        fake.pid = 99

        def fake_popen(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "launch.log"
            with mock.patch.object(update_sync.sys, "platform", "win32"):
                with mock.patch.object(update_sync.subprocess, "Popen", fake_popen):
                    proc = update_sync._popen_hidden(
                        ["powershell.exe", "-File", "apply.ps1"], log_path
                    )
        self.assertIs(proc, fake)
        flags = captured["kwargs"]["creationflags"]
        self.assertEqual(flags & update_sync._DETACHED_PROCESS, 0)
        self.assertTrue(flags & update_sync._CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(flags & update_sync._CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(captured["args"][0][0], "powershell.exe")
        env = captured["kwargs"]["env"]
        for var in ("_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"):
            self.assertNotIn(var, env)

    def test_windows_helper_retries_without_breakaway(self) -> None:
        flags_seen: list[int] = []
        fake = mock.Mock()
        fake.pid = 77

        def fake_popen(*args, **kwargs):
            flags_seen.append(kwargs.get("creationflags", 0))
            if len(flags_seen) == 1:
                raise OSError("job does not allow breakaway")
            return fake

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "launch.log"
            with mock.patch.object(update_sync.sys, "platform", "win32"):
                with mock.patch.object(update_sync.subprocess, "Popen", fake_popen):
                    proc = update_sync._popen_hidden(
                        ["powershell.exe", "-File", "apply.ps1"], log_path
                    )
        self.assertIs(proc, fake)
        self.assertEqual(len(flags_seen), 2)
        self.assertTrue(flags_seen[0] & update_sync._CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(flags_seen[1] & update_sync._CREATE_BREAKAWAY_FROM_JOB, 0)
        self.assertEqual(flags_seen[1] & update_sync._DETACHED_PROCESS, 0)

    def test_finish_update_protects_helper_then_exits(self) -> None:
        exits: list[bool] = []
        with mock.patch.object(
            updater, "_force_application_exit", lambda: exits.append(True)
        ):
            updater.finish_update_and_exit(4242)

        self.assertIn(4242, shutdown._protected_child_pids)
        self.assertEqual(exits, [True])

    def test_finish_update_does_not_exit_without_a_helper(self) -> None:
        exits: list[bool] = []
        with mock.patch.object(
            updater, "_force_application_exit", lambda: exits.append(True)
        ):
            with self.assertRaises(update_sync.UpdateSyncError):
                updater.finish_update_and_exit(0)

        self.assertEqual(exits, [])
        self.assertNotIn(0, shutdown._protected_child_pids)

    def test_launch_helper_refuses_a_dead_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            script = staging / "apply.ps1"
            script.write_text("noop", encoding="utf-8")
            with mock.patch.object(update_sync, "_write_helper_script", lambda _staging: script):
                with mock.patch.object(update_sync, "_popen_hidden", lambda *args, **kwargs: mock.Mock(pid=1)):
                    with mock.patch.object(update_sync, "_wait_for_helper_pid", lambda *args, **kwargs: 0):
                        with self.assertRaises(update_sync.UpdateSyncError):
                            update_sync.launch_apply_helper(staging, elevate=False)

    def test_reap_skips_protected_child_and_kills_unprotected(self) -> None:
        protected = _FakeProc(100, child_pids=[101])
        descendant = _FakeProc(101)
        victim = _FakeProc(200)
        procs = {100: protected, 101: descendant, 200: victim}

        def fake_process(pid, *args, **kwargs):
            proc = procs.get(int(pid))
            if proc is None:
                raise Exception(f"no fake process {pid}")
            return proc

        import psutil

        with mock.patch.object(multiprocessing, "active_children", lambda: []):
            with mock.patch.object(
                shutdown, "_iter_child_pids", lambda: [100, 101, 200]
            ):
                with mock.patch.object(psutil, "Process", fake_process):
                    shutdown.protect_child_process_trees([100])
                    shutdown.reap_child_process_trees()

        self.assertEqual(protected.kill_calls, 0)
        self.assertEqual(descendant.kill_calls, 0)
        self.assertEqual(victim.kill_calls, 1)

    def test_reap_spares_descendants_of_protected_child(self) -> None:
        parent = _FakeProc(50, child_pids=[51, 52])
        child_a = _FakeProc(51)
        child_b = _FakeProc(52)
        victim = _FakeProc(60)
        procs = {50: parent, 51: child_a, 52: child_b, 60: victim}

        def fake_process(pid, *args, **kwargs):
            proc = procs.get(int(pid))
            if proc is None:
                raise Exception(f"no fake process {pid}")
            return proc

        import psutil

        with mock.patch.object(multiprocessing, "active_children", lambda: []):
            with mock.patch.object(
                shutdown, "_iter_child_pids", lambda: [50, 51, 52, 60]
            ):
                with mock.patch.object(psutil, "Process", fake_process):
                    shutdown.protect_child_process_trees([50])
                    shutdown.reap_child_process_trees()

        self.assertEqual(parent.kill_calls, 0)
        self.assertEqual(child_a.kill_calls, 0)
        self.assertEqual(child_b.kill_calls, 0)
        self.assertEqual(victim.kill_calls, 1)

    @unittest.skipUnless(
        _psutil_can_list_children(),
        "psutil cannot inspect child processes in this environment",
    )
    def test_reap_skips_protected_child_and_kills_unprotected_live(self) -> None:
        protected = _spawn_sleeper()
        victim = _spawn_sleeper()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if protected.poll() is None and victim.poll() is None:
                    break
                time.sleep(0.05)
            self.assertIsNone(protected.poll())
            self.assertIsNone(victim.poll())

            ours = _tree_pids(protected.pid) | _tree_pids(victim.pid)
            original_iter = shutdown._iter_child_pids

            with mock.patch.object(
                shutdown,
                "_iter_child_pids",
                lambda: [pid for pid in original_iter() if pid in ours],
            ), mock.patch.object(multiprocessing, "active_children", lambda: []):
                shutdown.protect_child_process_trees([protected.pid])
                shutdown.reap_child_process_trees()

            self.assertIsNone(protected.poll())
            try:
                victim.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.fail("unprotected child was not reaped")
            self.assertIsNotNone(victim.poll())
        finally:
            _stop(protected)
            _stop(victim)

    @unittest.skipUnless(
        _psutil_can_list_children(),
        "psutil cannot inspect child processes in this environment",
    )
    def test_reap_spares_descendants_of_protected_child_live(self) -> None:
        nested = (
            "import subprocess, sys, time;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            "time.sleep(60)"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", nested],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            descendant_pids: set[int] = set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                descendant_pids = _tree_pids(parent.pid) - {parent.pid}
                if descendant_pids:
                    break
                time.sleep(0.05)
            self.assertIsNone(parent.poll())
            self.assertTrue(descendant_pids)

            ours = _tree_pids(parent.pid)
            original_iter = shutdown._iter_child_pids
            with mock.patch.object(
                shutdown,
                "_iter_child_pids",
                lambda: [pid for pid in original_iter() if pid in ours],
            ), mock.patch.object(multiprocessing, "active_children", lambda: []):
                shutdown.protect_child_process_trees([parent.pid])
                shutdown.reap_child_process_trees()

            self.assertIsNone(parent.poll())
            still = _tree_pids(parent.pid)
            for pid in descendant_pids:
                self.assertIn(pid, still)
        finally:
            try:
                import psutil

                for child in psutil.Process(parent.pid).children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
            except Exception:
                pass
            _stop(parent)


if __name__ == "__main__":
    unittest.main()

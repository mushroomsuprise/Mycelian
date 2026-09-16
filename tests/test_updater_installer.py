#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for updater installer launch vs shutdown child reaping."""

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

from modules import shutdown, updater  # noqa: E402


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
        updater._installer_helper_proc = None

    def tearDown(self) -> None:
        shutdown._protected_child_pids.clear()
        updater._installer_helper_proc = None

    def test_windows_installer_creationflags_include_breakaway(self) -> None:
        flags = updater._windows_installer_creationflags()
        self.assertTrue(flags & updater._CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(flags & updater._DETACHED_PROCESS)
        self.assertTrue(flags & updater._CREATE_BREAKAWAY_FROM_JOB)
        without = updater._windows_installer_creationflags(breakaway=False)
        self.assertEqual(without & updater._CREATE_BREAKAWAY_FROM_JOB, 0)
        self.assertTrue(without & updater._DETACHED_PROCESS)

    def test_windows_helper_passes_breakaway_flags(self) -> None:
        captured: dict = {}
        fake = mock.Mock()
        fake.pid = 99
        fake.poll.return_value = None

        def fake_popen(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(updater.subprocess, "Popen", fake_popen):
                with mock.patch.object(tempfile, "gettempdir", lambda: tmp):
                    proc = updater._run_installer_windows_detached(
                        str(Path(tmp) / "Setup.exe")
                    )
        self.assertIs(proc, fake)
        flags = captured["kwargs"]["creationflags"]
        self.assertTrue(flags & updater._DETACHED_PROCESS)
        self.assertTrue(flags & updater._CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(flags & updater._CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(captured["args"][0][0], "wscript.exe")
        env = captured["kwargs"]["env"]
        for var in ("_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"):
            self.assertNotIn(var, env)

    def test_windows_helper_retries_without_breakaway(self) -> None:
        flags_seen: list[int] = []
        fake = mock.Mock()
        fake.pid = 77
        fake.poll.return_value = None

        def fake_popen(*args, **kwargs):
            flags_seen.append(kwargs.get("creationflags", 0))
            if len(flags_seen) == 1:
                raise OSError("job does not allow breakaway")
            return fake

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(updater.subprocess, "Popen", fake_popen):
                with mock.patch.object(tempfile, "gettempdir", lambda: tmp):
                    proc = updater._run_installer_windows_detached(
                        str(Path(tmp) / "Setup.exe")
                    )
        self.assertIs(proc, fake)
        self.assertEqual(len(flags_seen), 2)
        self.assertTrue(flags_seen[0] & updater._CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(flags_seen[1] & updater._CREATE_BREAKAWAY_FROM_JOB, 0)
        self.assertTrue(flags_seen[1] & updater._DETACHED_PROCESS)

    def test_run_installer_and_exit_protects_helper_then_exits(self) -> None:
        fake = mock.Mock()
        fake.pid = 4242
        fake.poll.return_value = None
        exits: list[bool] = []
        patches = [
            mock.patch.object(updater, name, lambda path, _fake=fake: _fake)
            for name in (
                "_run_installer_windows_detached",
                "_run_installer_macos_detached",
                "_run_installer_linux_detached",
            )
        ]
        patches.append(
            mock.patch.object(
                updater, "_force_application_exit", lambda: exits.append(True)
            )
        )
        with patches[0], patches[1], patches[2], patches[3]:
            updater.run_installer_and_exit("/tmp/Setup.exe")

        self.assertIn(4242, shutdown._protected_child_pids)
        self.assertEqual(exits, [True])
        self.assertIs(updater._installer_helper_proc, fake)

    def test_run_installer_and_exit_does_not_exit_if_helper_died(self) -> None:
        fake = mock.Mock()
        fake.pid = 9
        fake.poll.return_value = 1
        fake.returncode = 1
        exits: list[bool] = []
        patches = [
            mock.patch.object(updater, name, lambda path, _fake=fake: _fake)
            for name in (
                "_run_installer_windows_detached",
                "_run_installer_macos_detached",
                "_run_installer_linux_detached",
            )
        ]
        patches.append(
            mock.patch.object(
                updater, "_force_application_exit", lambda: exits.append(True)
            )
        )
        with patches[0], patches[1], patches[2], patches[3]:
            updater.run_installer_and_exit("/tmp/Setup.exe")

        self.assertEqual(exits, [])
        self.assertNotIn(9, shutdown._protected_child_pids)

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

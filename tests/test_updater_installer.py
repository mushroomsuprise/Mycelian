#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the update helper launch vs shutdown child reaping."""

from __future__ import annotations

import contextlib
import multiprocessing
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
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


def _windows_startupinfo():
    """The Windows-only STARTUPINFO pieces, so the win32 path can run on any OS."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0, wShowWindow=0), create=True))
    stack.enter_context(mock.patch.object(subprocess, "STARTF_USESHOWWINDOW", 1, create=True))
    stack.enter_context(mock.patch.object(subprocess, "SW_HIDE", 0, create=True))
    return stack


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
            with mock.patch.object(update_sync.sys, "platform", "win32"), _windows_startupinfo():
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
        for var in update_sync._PYINSTALLER_ENV_VARS:
            self.assertNotIn(var, env)
        self.assertEqual(env.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")

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
            with mock.patch.object(update_sync.sys, "platform", "win32"), _windows_startupinfo():
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


class ReleaseSnapshotTests(unittest.TestCase):
    """File updates download the release tag's commit, not a moving branch."""

    SHA = "d04504e39802519b6da55d052c3792210d3475e6"

    def test_release_file_url_uses_commit_sha(self) -> None:
        url = update_sync.release_file_url(
            "mushroomsuprise",
            "mycelian",
            self.SHA,
            "packaged/manifest.json",
        )
        self.assertEqual(
            url,
            "https://raw.githubusercontent.com/mushroomsuprise/mycelian/"
            f"{self.SHA}/packaged/manifest.json",
        )
        self.assertNotIn("/main/", url)
        self.assertNotIn("/v1.13.3/", url)

    def test_release_file_url_rejects_branch_and_empty_ref(self) -> None:
        for ref in ("", "main", "v1.13.3", self.SHA[:12]):
            with self.assertRaises(update_sync.UpdateSyncError):
                update_sync.release_file_url("owner", "repo", ref, "packaged/manifest.json")

    def test_missing_or_non_sha_lookup_has_no_download_ref(self) -> None:
        self.assertIsNone(updater.release_download_ref(None))
        self.assertIsNone(updater.release_download_ref({}))
        self.assertIsNone(updater.release_download_ref({"sha": "main"}))
        self.assertIsNone(updater.release_download_ref({"sha": ""}))
        self.assertIsNone(updater.release_download_ref({"sha": self.SHA[:12]}))
        # A branch name on the release record is not a snapshot, even if it
        # sits next to a real commit field that is not the commits API sha.
        self.assertIsNone(
            updater.release_download_ref({"target_commitish": "main", "sha": "main"})
        )
        self.assertIsNone(updater.release_download_ref({"target_commitish": self.SHA}))
        self.assertEqual(
            updater.release_download_ref({"sha": self.SHA, "target_commitish": "main"}),
            self.SHA,
        )
        self.assertEqual(
            updater.release_download_ref({"sha": self.SHA.upper()}),
            self.SHA,
        )


if __name__ == "__main__":
    unittest.main()

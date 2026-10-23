#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Startup guards: frozen spawn children, splash thread hops, profiler milestones."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import app_startup  # noqa: E402
from modules import startup_profiler  # noqa: E402


class SpawnChildDetectionTests(unittest.TestCase):
    def test_app_launch_is_not_a_child(self) -> None:
        self.assertFalse(app_startup.is_spawn_child(["main.py"]))
        self.assertFalse(app_startup.is_spawn_child(["Mycelian.exe"]))
        self.assertFalse(
            app_startup.is_spawn_child(["Mycelian.exe", "--mycelian-npsso-capture"])
        )

    def test_frozen_spawn_worker_argv_is_a_child(self) -> None:
        # What PyInstaller's freeze_support() diverts: name is still MainProcess.
        argv = [
            "Mycelian.exe",
            "--multiprocessing-fork",
            "parent_pid=100",
            "pipe_handle=200",
        ]
        self.assertTrue(app_startup.is_spawn_child(argv))

    def test_frozen_resource_tracker_argv_is_a_child(self) -> None:
        argv = [
            "Mycelian",
            "-B",
            "-s",
            "-c",
            "from multiprocessing.resource_tracker import main;main(7)",
        ]
        self.assertTrue(app_startup.is_spawn_child(argv))

    def test_renamed_dev_child_is_a_child(self) -> None:
        fake = mock.Mock()
        fake.name = "SpawnProcess-1"
        with mock.patch("multiprocessing.current_process", return_value=fake):
            self.assertTrue(app_startup.is_spawn_child(["main.py"]))


_PROBE = textwrap.dedent(
    """
    import atexit, runpy, signal, sys
    sys.path.insert(0, {root!r})
    sys.argv = {argv!r}
    ns = runpy.run_path({main!r}, run_name="__mp_main__")
    print("SIGTERM_HANDLED", signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL)
    count = atexit._ncallbacks()
    atexit.unregister(ns["emergency_stats_save"])
    print("EMERGENCY_ATEXIT", atexit._ncallbacks() < count)
    """
)


class MainTopLevelRegistrationTests(unittest.TestCase):
    """Run main.py's module top level (not its __main__ block) from a temp copy."""

    def _run_top_level(self, argv: list[str]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            # A copy keeps get_data_path("logs") inside the temp dir.
            main_copy = Path(tmp) / "main.py"
            shutil.copyfile(ROOT / "main.py", main_copy)
            code = _PROBE.format(root=str(ROOT), argv=argv, main=str(main_copy))
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_main_process_registers_signal_and_atexit(self) -> None:
        out = self._run_top_level(["main.py"])
        self.assertIn("SIGTERM_HANDLED True", out)
        self.assertIn("EMERGENCY_ATEXIT True", out)

    def test_frozen_style_child_skips_signal_and_atexit(self) -> None:
        out = self._run_top_level(
            ["Mycelian", "--multiprocessing-fork", "tracker_fd=5", "pipe_handle=6"]
        )
        self.assertIn("SIGTERM_HANDLED False", out)
        self.assertIn("EMERGENCY_ATEXIT False", out)


class SplashLoopHopTests(unittest.TestCase):
    def setUp(self) -> None:
        from modules import mainuiwindow

        self.mw = mainuiwindow
        self._saved = (
            mainuiwindow._splash_dialog,
            mainuiwindow._splash_progress,
            mainuiwindow._splash_status,
            mainuiwindow._splash_close_requested,
        )

    def tearDown(self) -> None:
        (
            self.mw._splash_dialog,
            self.mw._splash_progress,
            self.mw._splash_status,
            self.mw._splash_close_requested,
        ) = self._saved

    def test_update_and_close_run_on_the_ui_loop_thread(self) -> None:
        from nicegui import core

        loop = asyncio.new_event_loop()
        runner = threading.Thread(target=loop.run_forever, daemon=True)
        runner.start()
        seen_threads: list[str] = []

        dialog = mock.Mock()
        dialog.close.side_effect = lambda: seen_threads.append(
            threading.current_thread().name
        )
        progress = mock.Mock()
        progress.set_value.side_effect = lambda _v: seen_threads.append(
            threading.current_thread().name
        )
        status = mock.Mock()
        self.mw._splash_dialog = dialog
        self.mw._splash_progress = progress
        self.mw._splash_status = status
        self.mw._splash_close_requested = False

        saved_loop = core.loop
        core.loop = loop
        try:
            worker = threading.Thread(
                target=lambda: (
                    self.mw.update_splash_progress(0.5, "Twitch ready"),
                    self.mw.close_splash_screen(),
                ),
                name="DeferredInit",
            )
            worker.start()
            worker.join()
            # The close request is visible immediately, before the loop runs it.
            self.assertTrue(self.mw._splash_close_requested)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        finally:
            core.loop = saved_loop
            loop.call_soon_threadsafe(loop.stop)
            runner.join(5)
            loop.close()

        progress.set_value.assert_called_once_with(0.5)
        self.assertEqual(status.text, "Twitch ready")
        dialog.close.assert_called_once_with()
        self.assertIsNone(self.mw._splash_dialog)
        self.assertEqual(seen_threads, [runner.name, runner.name])

    def test_without_a_loop_calls_run_inline(self) -> None:
        dialog = mock.Mock()
        self.mw._splash_dialog = dialog
        self.mw._splash_progress = None
        self.mw._splash_status = None
        self.mw._splash_close_requested = False
        self.mw.update_splash_progress(0.1, "Core components loaded")
        self.mw.close_splash_screen()
        dialog.close.assert_called_once_with()
        self.assertTrue(self.mw._splash_close_requested)


class StartupMilestoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._enabled = startup_profiler.is_startup_profiling_enabled()
        startup_profiler._milestones_seen.clear()

    def tearDown(self) -> None:
        startup_profiler.configure_startup_profiling(self._enabled)
        startup_profiler._milestones_seen.clear()

    def test_milestone_is_silent_when_profiling_is_off(self) -> None:
        startup_profiler.configure_startup_profiling(False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            startup_profiler.mark_startup_milestone("first build_root_ui done")
            startup_profiler.mark_process_start()
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(startup_profiler._milestones_seen, set())

    def test_milestone_prints_once_when_profiling(self) -> None:
        startup_profiler.configure_startup_profiling(True)
        buf = io.StringIO()
        with (
            mock.patch.object(startup_profiler, "_is_profiling_owner", return_value=True),
            redirect_stdout(buf),
        ):
            startup_profiler.mark_startup_milestone("native window shown")
            startup_profiler.mark_startup_milestone("native window shown")
        lines = [line for line in buf.getvalue().splitlines() if "milestone" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("native window shown", lines[0])


if __name__ == "__main__":
    unittest.main()

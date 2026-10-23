"""RT-07: OBS dock-boot watcher reuses the live OBS process instead of rescanning."""

from __future__ import annotations

import subprocess
import sys

import psutil

from modules import obs_browser_docks as docks


def test_obs_process_check_reuses_live_process(monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        proc = psutil.Process(child.pid)
        child_name = proc.name()
        proc.info = {"name": child_name}
        scans = {"n": 0}

        def fake_iter(attrs=None):
            scans["n"] += 1
            return iter([proc] if child.poll() is None else [])

        monkeypatch.setattr(docks, "_cached_obs_proc", None)
        monkeypatch.setattr(docks, "_is_obs_main_process_name", lambda n: n == child_name)
        monkeypatch.setattr(psutil, "process_iter", fake_iter)

        assert [docks.obs_process_is_running() for _ in range(5)] == [True] * 5
        assert scans["n"] == 1
        # Quit is seen on the very next check, as with a full scan.
        child.kill()
        child.wait()
        assert docks.obs_process_is_running() is False
        assert scans["n"] == 2
        assert docks.obs_process_is_running() is False
        assert scans["n"] == 3  # not running: full scan every call, as before
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.shutdown (teardown coordinator, child reaping, native window exit).

Nothing here may kill a real process or exit the test run: psutil, multiprocessing children,
subprocess, os._exit and the watchdog timer are all faked for every test.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import modules
from modules import shutdown as sh

ME = os.getpid()


# --------------------------------------------------------------------------- fake psutil

class FakePsutilError(Exception):
    pass


class FakeNoSuchProcess(FakePsutilError):
    pass


class FakePsProc:
    def __init__(self, pid, ppid=None, children=(), running=True, kill_error=None, children_error=None,
                 running_error=None):
        self.pid = pid
        self._ppid = ppid
        self._children = list(children)
        self.running = running
        self.kill_error = kill_error
        self.children_error = children_error
        self.running_error = running_error
        self.killed = 0

    def children(self, recursive=False):
        if self.children_error:
            raise self.children_error
        return list(self._children)

    def is_running(self):
        if self.running_error:
            raise self.running_error
        return self.running

    def kill(self):
        self.killed += 1
        if self.kill_error:
            raise self.kill_error
        self.running = False

    def ppid(self):
        if isinstance(self._ppid, Exception):
            raise self._ppid
        return self._ppid


class FakePsutil(ModuleType):
    def __init__(self):
        super().__init__("psutil")
        self.Error = FakePsutilError
        self.NoSuchProcess = FakeNoSuchProcess
        self.procs = {}
        self.process_error = None

    def Process(self, pid=None):
        if self.process_error:
            raise self.process_error
        if pid not in self.procs:
            raise FakeNoSuchProcess(pid)
        return self.procs[pid]


@pytest.fixture(autouse=True)
def psutil_fake(monkeypatch):
    fake = FakePsutil()
    fake.procs[ME] = FakePsProc(ME)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return fake


# --------------------------------------------------------------------------- safety + state reset

class Exited(BaseException):
    pass


@pytest.fixture(autouse=True)
def _safety(monkeypatch):
    monkeypatch.setattr(sh, "_shutdown_in_progress", False)
    monkeypatch.setattr(sh, "_native_close_registered", False)
    monkeypatch.setattr(sh, "_native_close_exit_started", False)
    monkeypatch.setattr(sh, "_protected_child_pids", set())
    monkeypatch.setattr(sh, "_pending_flushes", [])
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise Exited(code)

    monkeypatch.setattr(sh.os, "_exit", fake_exit)
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [])

    def no_run(*a, **k):
        raise AssertionError("tests must not run real subprocesses")

    monkeypatch.setattr(sh.subprocess, "run", no_run)
    monkeypatch.setattr(sh.time, "sleep", lambda s: None)
    return exits


def fake_module(monkeypatch, dotted, **attrs):
    """Install a fake ``modules.<dotted>`` reachable by both import forms shutdown uses."""
    m = ModuleType(f"modules.{dotted}")
    for k, v in attrs.items():
        setattr(m, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{dotted}", m)
    if "." not in dotted:
        monkeypatch.setattr(modules, dotted, m, raising=False)
    return m


def install_nicegui(monkeypatch, *, main_window=None, event_manager=None):
    ng = ModuleType("nicegui")
    ng.app = SimpleNamespace(native=SimpleNamespace(main_window=main_window))
    em = ModuleType("nicegui.native.event_manager")
    em.event_manager = event_manager
    monkeypatch.setitem(sys.modules, "nicegui", ng)
    monkeypatch.setitem(sys.modules, "nicegui.native", ModuleType("nicegui.native"))
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", em)
    return ng


# --------------------------------------------------------------------------- flushes / flags

def test_register_flush_replaces_by_name_and_runs_in_order():
    calls = []
    sh.register_shutdown_flush("a", lambda: calls.append("a1"))
    sh.register_shutdown_flush("b", lambda: calls.append("b"))
    sh.register_shutdown_flush("a", lambda: calls.append("a2"))
    assert [n for n, _ in sh._pending_flushes] == ["b", "a"]
    sh._run_registered_flushes()
    assert calls == ["b", "a2"]


def test_flush_steps_use_named_run_step(monkeypatch):
    seen = []
    monkeypatch.setattr(sh, "_run_step", lambda name, func, timeout=4.0: seen.append((name, timeout)))
    sh.register_shutdown_flush("stats", lambda: None)
    sh._run_registered_flushes()
    assert seen == [("flush:stats", 5.0)]


def test_mark_shutdown_in_progress_once():
    assert sh.is_shutdown_in_progress() is False
    assert sh.mark_shutdown_in_progress() is True
    assert sh.is_shutdown_in_progress() is True
    assert sh.mark_shutdown_in_progress() is False


# --------------------------------------------------------------------------- watchdog

class FakeTimer:
    instances = []

    def __init__(self, seconds, fn):
        self.seconds, self.fn = seconds, fn
        self.daemon = False
        self.started = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def timers(monkeypatch):
    FakeTimer.instances = []
    monkeypatch.setattr(sh.threading, "Timer", FakeTimer)
    return FakeTimer


@pytest.mark.parametrize("raw, seconds", [(None, 20.0), ("", 20.0), ("  ", 20.0), ("3.5", 3.5)])
def test_watchdog_armed(monkeypatch, timers, raw, seconds, caplog):
    if raw is None:
        monkeypatch.delenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", raising=False)
    else:
        monkeypatch.setenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", raw)
    with caplog.at_level(logging.WARNING, logger=sh.logger.name):
        sh._start_shutdown_watchdog()
    (t,) = timers.instances
    assert t.seconds == seconds and t.daemon is True and t.started is True
    assert "watchdog armed" in caplog.text


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_watchdog_disabled(monkeypatch, timers, raw):
    monkeypatch.setenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", raw)
    sh._start_shutdown_watchdog()
    assert timers.instances == []


def test_watchdog_invalid_value(monkeypatch, timers, caplog):
    monkeypatch.setenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", "soon")
    with caplog.at_level(logging.WARNING, logger=sh.logger.name):
        sh._start_shutdown_watchdog()
    assert timers.instances == []
    assert "Invalid MYCELIAN_SHUTDOWN_WATCHDOG_SEC='soon'" in caplog.text


def test_watchdog_fire_reaps_and_exits(monkeypatch, timers, _safety, caplog):
    monkeypatch.setenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", "1")
    reaped = []
    monkeypatch.setattr(sh, "reap_child_process_trees", lambda: reaped.append(1))
    sh._start_shutdown_watchdog()
    with caplog.at_level(logging.ERROR, logger=sh.logger.name), pytest.raises(Exited):
        timers.instances[0].fn()
    assert reaped == [1] and _safety == [0]
    assert "Shutdown watchdog fired after 1.0s; threads=" in caplog.text


def test_watchdog_fire_survives_errors(monkeypatch, timers, _safety, caplog):
    monkeypatch.setenv("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", "2")

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(sh.threading, "enumerate", boom)
    monkeypatch.setattr(sh, "reap_child_process_trees", boom)
    sh._start_shutdown_watchdog()
    with caplog.at_level(logging.ERROR, logger=sh.logger.name), pytest.raises(Exited):
        timers.instances[0].fn()
    assert _safety == [0]
    assert "Shutdown watchdog fired after 2.0s" in caplog.text


# --------------------------------------------------------------------------- step runners

def test_run_step_success(caplog):
    calls = []
    with caplog.at_level(logging.INFO, logger=sh.logger.name):
        sh._run_step("ok", lambda: calls.append(threading.current_thread().name))
    assert calls == ["shutdown-ok"]
    assert "Shutdown: ok (" in caplog.text


def test_run_step_error_logged(caplog):
    def boom():
        raise ValueError("bad")

    with caplog.at_level(logging.ERROR, logger=sh.logger.name):
        sh._run_step("bad", boom)
    assert "Shutdown failed: bad" in caplog.text


def test_run_step_timeout(caplog):
    release = threading.Event()
    try:
        with caplog.at_level(logging.WARNING, logger=sh.logger.name):
            sh._run_step("slow", lambda: release.wait(2), timeout=0.01)
        assert "Shutdown timed out: slow (0.0s)" in caplog.text
    finally:
        release.set()


def test_run_parallel_empty_and_normal(caplog):
    sh._run_parallel([], timeout=1)
    calls = []

    def boom():
        raise RuntimeError("ignored")

    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._run_parallel([("a", lambda: calls.append("a")), ("b", boom), ("c", lambda: calls.append("c"))], 1.0)
    assert sorted(calls) == ["a", "c"]
    assert "Shutdown (parallel): a, b, c" in caplog.text
    assert "Shutdown b: ignored" in caplog.text
    assert "Shutdown (parallel) done" in caplog.text


def test_run_parallel_timeout(caplog):
    release = threading.Event()
    try:
        with caplog.at_level(logging.WARNING, logger=sh.logger.name):
            sh._run_parallel([("stuck", lambda: release.wait(2))], timeout=0.01)
        assert "Shutdown timed out: stuck (batch 0.0s)" in caplog.text
    finally:
        release.set()


# --------------------------------------------------------------------------- service stops

def test_pause_alerts_and_activity_feed(monkeypatch):
    calls = []
    fake_module(monkeypatch, "web_engine", set_alerts_paused=lambda flag: calls.append(("paused", flag)))
    fake_module(monkeypatch, "uiwindows.activity_feed", stop_alert_processor=lambda: calls.append("stop"))
    fake_module(monkeypatch, "alertutils", cancel_alert_storage_trim=lambda: calls.append("trim"))
    sh._pause_alerts_and_activity_feed()
    assert calls == ["trim", ("paused", True), "stop"]


def test_pause_alerts_survives_helper_errors(monkeypatch, caplog):
    calls = []

    def boom(*a):
        raise RuntimeError("down")

    fake_module(monkeypatch, "web_engine", set_alerts_paused=boom)
    fake_module(monkeypatch, "uiwindows.activity_feed", stop_alert_processor=lambda: calls.append("stop"))
    fake_module(monkeypatch, "alertutils", cancel_alert_storage_trim=boom)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._pause_alerts_and_activity_feed()
    assert calls == ["stop"]
    assert "Alert storage trim cancel: down" in caplog.text


def test_simple_service_stops(monkeypatch):
    calls = []
    fake_module(monkeypatch, "alert_processor", cleanup=lambda: calls.append("alerts"))
    fake_module(monkeypatch, "obs_service", obs_service=SimpleNamespace(stop=lambda: calls.append("obs")))
    fake_module(monkeypatch, "youtube", stop_youtube_service=lambda join_timeout: calls.append(("yt", join_timeout)))
    fake_module(monkeypatch, "spotify", stop_spotify_service=lambda join_timeout: calls.append(("sp", join_timeout)))
    fake_module(monkeypatch, "discord_service", disconnect=lambda: calls.append("discord"))
    fake_module(monkeypatch, "psn_service",
                stop_psn_data_updater_thread=lambda join_timeout: calls.append(("psn", join_timeout)))
    fake_module(monkeypatch, "connection_monitor", stop=lambda: calls.append("connmon"))
    fake_module(monkeypatch, "statistics_manager", shutdown_statistics=lambda: calls.append("stats"))
    fake_module(monkeypatch, "service_manager",
                get_service_manager=lambda: SimpleNamespace(shutdown=lambda: calls.append("deferred")))
    sh._stop_alert_processor()
    sh._stop_obs()
    sh._stop_youtube()
    sh._stop_spotify()
    sh._stop_discord()
    sh._stop_psn()
    sh._stop_connection_monitor()
    sh._save_statistics()
    sh._stop_deferred_services()
    j = sh._SHUTDOWN_JOIN_TIMEOUT
    assert calls == ["alerts", "obs", ("yt", j), ("sp", j), "discord", ("psn", j), "connmon", "stats", "deferred"]


def test_deferred_services_error_logged(monkeypatch, caplog):
    def boom():
        raise RuntimeError("not started")

    fake_module(monkeypatch, "service_manager", get_service_manager=boom)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._stop_deferred_services()
    assert "Deferred service manager stop: not started" in caplog.text


class FakeApi:
    def __init__(self, calls, prefix):
        self.calls, self.prefix = calls, prefix

    def __getattr__(self, name):
        return lambda: self.calls.append(f"{self.prefix}.{name}")


@pytest.mark.parametrize("has_api", [True, False])
def test_stop_twitch_and_chatbot(monkeypatch, has_api):
    calls = []
    fake_module(monkeypatch, "twitch", get_twitch_api=lambda: FakeApi(calls, "tw") if has_api else None)
    fake_module(monkeypatch, "chatbot", get_chatbot_api=lambda: FakeApi(calls, "cb") if has_api else None)
    fake_module(monkeypatch, "twitch_oauth", stop_active_oauth=lambda: calls.append("oauth"))
    sh._stop_twitch()
    sh._stop_chatbot()
    if has_api:
        assert calls == ["tw.cancel_oauth", "tw.stop_connection", "oauth",
                         "cb.cancel_oauth", "cb.stop_health_check", "oauth"]
    else:
        assert calls == ["oauth", "oauth"]


class FakeLoop:
    def __init__(self, running=True, soon_error=None):
        self.running = running
        self.soon_error = soon_error
        self.soon = []

    def is_running(self):
        return self.running

    def stop(self):
        self.running = False

    def call_soon_threadsafe(self, fn):
        if self.soon_error:
            raise self.soon_error
        self.soon.append(fn)


class FakeFuture:
    def __init__(self, error=None):
        self.error = error
        self.timeouts = []

    def result(self, timeout):
        self.timeouts.append(timeout)
        if self.error:
            raise self.error


def connector_setup(monkeypatch, loop, thread=None, hotkey_error=None, future=None):
    calls = []
    manager = SimpleNamespace(connector_loop=loop, connector_thread=thread, is_running=True)

    async def stop():
        calls.append("manager.stop")

    manager.stop = stop
    fake_module(monkeypatch, "connector_manager", get_manager=lambda: manager)

    def hotkey_cleanup():
        if hotkey_error:
            raise hotkey_error
        calls.append("hotkeys")

    fake_module(monkeypatch, "hotkey_listener", cleanup=hotkey_cleanup)
    fut = future or FakeFuture()

    def run_threadsafe(coro, lp):
        calls.append(("run_coroutine_threadsafe", lp))
        coro.close()
        return fut

    import asyncio

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_threadsafe)
    return manager, calls, fut


class FakeThread:
    def __init__(self, alive=True):
        self.alive = alive
        self.joins = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)


def test_stop_connector_system_running_loop(monkeypatch):
    loop = FakeLoop()
    th = FakeThread()
    manager, calls, fut = connector_setup(monkeypatch, loop, th)
    sh._stop_connector_system()
    assert calls == [("run_coroutine_threadsafe", loop), "hotkeys"]
    assert fut.timeouts == [2.0]
    assert loop.soon == [loop.stop]
    assert manager.is_running is False
    assert th.joins == [1.0]


def test_stop_connector_system_errors_logged(monkeypatch, caplog):
    loop = FakeLoop(soon_error=RuntimeError("closed loop"))
    manager, calls, fut = connector_setup(monkeypatch, loop, FakeThread(alive=False),
                                          hotkey_error=RuntimeError("no hooks"),
                                          future=FakeFuture(error=TimeoutError("slow")))
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._stop_connector_system()
    assert "Connector manager async stop: slow" in caplog.text
    assert "Connector loop stop schedule: closed loop" in caplog.text
    assert "Hotkey listener cleanup: no hooks" in caplog.text
    assert manager.is_running is False


@pytest.mark.parametrize("loop", [None, FakeLoop(running=False)])
def test_stop_connector_system_idle_loop(monkeypatch, loop):
    manager, calls, _ = connector_setup(monkeypatch, loop, None)
    sh._stop_connector_system()
    assert calls == ["hotkeys"]
    assert manager.is_running is False


def test_stop_connector_system_real_async_stop(monkeypatch):
    """The coroutine handed to the loop really awaits manager.stop()."""
    import asyncio

    calls = []
    manager = SimpleNamespace(connector_loop=FakeLoop(), connector_thread=None, is_running=True)

    async def stop():
        calls.append("stopped")

    manager.stop = stop
    fake_module(monkeypatch, "connector_manager", get_manager=lambda: manager)
    fake_module(monkeypatch, "hotkey_listener", cleanup=lambda: None)

    def run_now(coro, loop):
        asyncio.run(coro)
        return FakeFuture()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_now)
    sh._stop_connector_system()
    assert calls == ["stopped"]


# --------------------------------------------------------------------------- pid helpers

class FakeChild:
    def __init__(self, pid, name="child", alive=True, exits_on_join=True, error=None):
        self.pid, self.name = pid, name
        self.alive = alive
        self.exits_on_join = exits_on_join
        self.error = error
        self.calls = []

    def is_alive(self):
        if self.error:
            raise self.error
        return self.alive

    def terminate(self):
        self.calls.append("terminate")

    def join(self, timeout=None):
        self.calls.append(("join", timeout))
        if self.exits_on_join:
            self.alive = False

    def kill(self):
        self.calls.append("kill")
        self.alive = False


def test_iter_child_pids_merges_and_dedupes(monkeypatch, psutil_fake):
    monkeypatch.setattr(multiprocessing, "active_children",
                        lambda: [FakeChild(10), FakeChild(None), FakeChild(-1), FakeChild("x"), FakeChild(ME)])
    psutil_fake.procs[ME]._children = [
        FakePsProc(10), FakePsProc(11), FakePsProc(12, running=False),
        FakePsProc(13, running_error=RuntimeError("gone")), FakePsProc(11),
    ]
    assert sh._iter_child_pids() == [10, 11]


def test_iter_child_pids_survives_errors(monkeypatch, psutil_fake):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(multiprocessing, "active_children", boom)
    psutil_fake.process_error = RuntimeError("no access")
    assert sh._iter_child_pids() == []


def test_windows_hidden_subprocess_kwargs(monkeypatch):
    monkeypatch.setattr(sh.sys, "platform", "darwin")
    assert sh._windows_hidden_subprocess_kwargs() == {}
    monkeypatch.setattr(sh.sys, "platform", "win32")

    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(sh.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(sh.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(sh.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(sh.subprocess, "SW_HIDE", 0, raising=False)
    kw = sh._windows_hidden_subprocess_kwargs()
    assert kw["creationflags"] == 0x08000000
    assert kw["startupinfo"].dwFlags == 1 and kw["startupinfo"].wShowWindow == 0
    monkeypatch.delattr(sh.subprocess, "STARTUPINFO")
    monkeypatch.delattr(sh.subprocess, "CREATE_NO_WINDOW")
    assert sh._windows_hidden_subprocess_kwargs() == {"creationflags": 0}


def test_pid_is_running(monkeypatch, psutil_fake):
    psutil_fake.procs[5] = FakePsProc(5)
    psutil_fake.procs[6] = FakePsProc(6, running=False)
    assert sh._pid_is_running(5) is True
    assert sh._pid_is_running(6) is False
    assert sh._pid_is_running(7) is False  # NoSuchProcess
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert sh._pid_is_running(5) is True  # can't tell: assume running


def test_leftover_tree_roots(monkeypatch, psutil_fake):
    assert sh._leftover_tree_roots([]) == []
    psutil_fake.procs.update({
        1: FakePsProc(1, ppid=ME), 2: FakePsProc(2, ppid=1), 3: FakePsProc(3, ppid=2),
        4: FakePsProc(4, ppid=RuntimeError("gone")),
    })
    assert sh._leftover_tree_roots([1, 2, 3, 4, 99]) == [1, 4, 99]
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert sh._leftover_tree_roots([2, 3]) == [2, 3]


def test_kill_tree_psutil_trivial_cases(monkeypatch, psutil_fake):
    assert sh._kill_process_tree_psutil(0) is True
    assert sh._kill_process_tree_psutil(ME) is True
    assert sh._kill_process_tree_psutil(404) is True  # already gone
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert sh._kill_process_tree_psutil(5) is False


def test_kill_tree_psutil_kills_children_then_root(psutil_fake):
    order = []
    c1, c2 = FakePsProc(21), FakePsProc(22, kill_error=FakeNoSuchProcess())
    same = FakePsProc(20)
    me = FakePsProc(ME)
    for p in (c1, c2, same, me):
        orig = p.kill
        p.kill = (lambda o=orig, pp=p: (order.append(pp.pid), o())[1])
    root = FakePsProc(20, children=[c1, c2, same, me])
    root_kill = root.kill
    root.kill = lambda: (order.append("root"), root_kill())[1]
    psutil_fake.procs[20] = root
    assert sh._kill_process_tree_psutil(20) is True
    assert order == [22, 21, "root"]  # reversed children, skipping root and self


def test_kill_tree_psutil_children_error_and_root_gone(psutil_fake):
    root = FakePsProc(30, children_error=FakePsutilError("denied"), kill_error=FakeNoSuchProcess(),
                      running_error=FakeNoSuchProcess())
    psutil_fake.procs[30] = root
    assert sh._kill_process_tree_psutil(30) is True


def test_kill_tree_psutil_child_kill_failure(psutil_fake):
    bad = FakePsProc(41, kill_error=PermissionError("denied"))
    psutil_fake.procs[40] = FakePsProc(40, children=[bad])
    assert sh._kill_process_tree_psutil(40) is False


def test_kill_tree_psutil_root_kill_failure(psutil_fake):
    psutil_fake.procs[50] = FakePsProc(50, kill_error=PermissionError("denied"))
    assert sh._kill_process_tree_psutil(50) is False


@pytest.mark.parametrize(
    "running_error, still_running, expected",
    [(None, False, True), (None, True, False), (FakeNoSuchProcess(), None, True), (RuntimeError(), None, False)],
)
def test_kill_tree_psutil_final_check(psutil_fake, running_error, still_running, expected):
    proc = FakePsProc(60)
    psutil_fake.procs[60] = proc
    orig_kill = proc.kill

    def kill():
        orig_kill()
        proc.running = still_running
        proc.running_error = running_error

    proc.kill = kill
    assert sh._kill_process_tree_psutil(60) is expected


class RunResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_taskkill_skipped_off_windows_and_for_self(monkeypatch):
    monkeypatch.setattr(sh.sys, "platform", "darwin")
    sh._taskkill_process_tree(5)  # subprocess.run would raise
    monkeypatch.setattr(sh.sys, "platform", "win32")
    sh._taskkill_process_tree(0)
    sh._taskkill_process_tree(ME)


@pytest.mark.parametrize(
    "result, text",
    [(RunResult(0), "Killed process tree PID 77"),
     (RunResult(128, stderr=" not found "), "taskkill PID 77 rc=128: not found"),
     (RunResult(1, stdout="out"), "taskkill PID 77 rc=1: out"),
     (RunResult(1, stdout=None, stderr=None), "taskkill PID 77 rc=1: ")],
)
def test_taskkill_on_windows(monkeypatch, caplog, result, text):
    calls = []
    monkeypatch.setattr(sh.sys, "platform", "win32")
    monkeypatch.setattr(sh, "_windows_hidden_subprocess_kwargs", lambda: {"creationflags": 8})
    monkeypatch.setattr(sh.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or result)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._taskkill_process_tree(77)
    cmd, kw = calls[0]
    assert cmd == ["taskkill", "/PID", "77", "/T", "/F"]
    assert kw["creationflags"] == 8 and kw["check"] is False and kw["timeout"] == 5.0
    assert text in caplog.text


def test_taskkill_error_logged(monkeypatch, caplog):
    monkeypatch.setattr(sh.sys, "platform", "win32")
    monkeypatch.setattr(sh, "_windows_hidden_subprocess_kwargs", lambda: {})

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("taskkill", 5)

    monkeypatch.setattr(sh.subprocess, "run", boom)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._taskkill_process_tree(78)
    assert "taskkill PID 78:" in caplog.text


# --------------------------------------------------------------------------- protection

def test_protect_child_process_trees(caplog):
    with caplog.at_level(logging.INFO, logger=sh.logger.name):
        sh.protect_child_process_trees([5, 5, 0, -3, "7", 6])
    assert sh._protected_child_pids == {5, 6}
    assert "Protecting child process trees from reaping: [5, 6]" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=sh.logger.name):
        sh.protect_child_process_trees([5])
    assert "Protecting" not in caplog.text


def test_expanded_protected_pids(monkeypatch, psutil_fake):
    assert sh._expanded_protected_pids() == set()
    sh.protect_child_process_trees([5, 8, 9])

    class BadPid:
        @property
        def pid(self):
            raise RuntimeError("x")

    psutil_fake.procs[5] = FakePsProc(5, children=[FakePsProc(6), BadPid()])
    psutil_fake.procs[8] = FakePsProc(8, children_error=RuntimeError("denied"))
    # 9 missing -> NoSuchProcess swallowed
    assert sh._expanded_protected_pids() == {5, 6, 8, 9}
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert sh._expanded_protected_pids() == {5, 8, 9}


def test_tree_contains_protected(psutil_fake):
    assert sh._tree_contains_protected("5", {5}) is False
    assert sh._tree_contains_protected(0, {0}) is False
    assert sh._tree_contains_protected(5, {5}) is True
    psutil_fake.procs[10] = FakePsProc(10, children=[FakePsProc(11), FakePsProc(12)])
    assert sh._tree_contains_protected(10, {12}) is True
    assert sh._tree_contains_protected(10, {99}) is False
    assert sh._tree_contains_protected(404, {99}) is False


# --------------------------------------------------------------------------- _kill_process_tree

def test_kill_process_tree_guards(monkeypatch):
    monkeypatch.setattr(sh, "_kill_process_tree_psutil", lambda pid: pytest.fail("must not kill"))
    monkeypatch.setattr(sh.sys, "platform", "darwin")
    sh._kill_process_tree(5)
    monkeypatch.setattr(sh.sys, "platform", "win32")
    sh._kill_process_tree(0)
    sh._kill_process_tree(ME)


def test_kill_process_tree_skips_protected(monkeypatch, caplog):
    monkeypatch.setattr(sh.sys, "platform", "win32")
    monkeypatch.setattr(sh, "_kill_process_tree_psutil", lambda pid: pytest.fail("protected"))
    sh.protect_child_process_trees([5])
    with caplog.at_level(logging.INFO, logger=sh.logger.name):
        sh._kill_process_tree(5)
    assert "Skipping kill of protected process tree pid=5" in caplog.text


@pytest.mark.parametrize("was_running, psutil_ok, expect_log, expect_taskkill",
                         [(True, True, True, False), (False, True, False, False), (True, False, False, True)])
def test_kill_process_tree_paths(monkeypatch, caplog, was_running, psutil_ok, expect_log, expect_taskkill):
    monkeypatch.setattr(sh.sys, "platform", "win32")
    taskkills = []
    monkeypatch.setattr(sh, "_pid_is_running", lambda pid: was_running)
    monkeypatch.setattr(sh, "_kill_process_tree_psutil", lambda pid: psutil_ok)
    monkeypatch.setattr(sh, "_taskkill_process_tree", lambda pid: taskkills.append(pid))
    with caplog.at_level(logging.WARNING, logger=sh.logger.name):
        sh._kill_process_tree(9)
    assert ("Killed process tree PID 9" in caplog.text) is expect_log
    assert taskkills == ([9] if expect_taskkill else [])


# --------------------------------------------------------------------------- reap_child_process_trees

def test_reap_posix_terminates_and_kills(monkeypatch, psutil_fake, caplog):
    monkeypatch.setattr(sh.sys, "platform", "darwin")
    polite = FakeChild(101, "polite")
    stubborn = FakeChild(102, "stubborn", exits_on_join=False)
    dead = FakeChild(103, "dead", alive=False)
    broken = FakeChild(104, "broken", error=RuntimeError("boom"))
    protected = FakeChild(105, "updater")
    sh.protect_child_process_trees([105])
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [polite, stubborn, dead, broken, protected])
    leftover_ok = FakePsProc(201)
    leftover_dead = FakePsProc(202, running=False)
    leftover_err = FakePsProc(203, kill_error=RuntimeError("denied"))
    psutil_fake.procs.update({201: leftover_ok, 202: leftover_dead, 203: leftover_err})
    monkeypatch.setattr(sh, "_iter_child_pids", lambda: [201, 202, 203, 105])
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.reap_child_process_trees()
    assert polite.calls == ["terminate", ("join", 1.0)]
    assert stubborn.calls == ["terminate", ("join", 1.0), "kill", ("join", 0.5)]
    assert dead.calls == []
    assert protected.calls == []
    assert "Could not terminate child broken: boom" in caplog.text
    assert "Skipping reap of protected spawn child updater (pid=105)" in caplog.text
    assert "Skipping reap of protected leftover pid=105" in caplog.text
    assert leftover_ok.killed == 1 and leftover_dead.killed == 0 and leftover_err.killed == 1


def test_reap_posix_nothing_left(monkeypatch):
    monkeypatch.setattr(sh.sys, "platform", "linux")
    monkeypatch.setattr(sh, "_iter_child_pids", lambda: [])
    sh.reap_child_process_trees()


def test_reap_posix_leftovers_without_psutil(monkeypatch):
    monkeypatch.setattr(sh.sys, "platform", "linux")
    monkeypatch.setattr(sh, "_iter_child_pids", lambda: [300])
    monkeypatch.setattr(sh, "_expanded_protected_pids", lambda: set())
    monkeypatch.setitem(sys.modules, "psutil", None)
    sh.reap_child_process_trees()  # ImportError swallowed


def test_reap_windows(monkeypatch, caplog):
    monkeypatch.setattr(sh.sys, "platform", "win32")
    children = [FakeChild(11, "webview"), FakeChild(12, "updater"), FakeChild(None, "nopid")]
    monkeypatch.setattr(multiprocessing, "active_children", lambda: children)
    monkeypatch.setattr(sh, "_expanded_protected_pids", lambda: {12, 31})
    monkeypatch.setattr(sh, "_tree_contains_protected", lambda pid, prot: pid in prot)
    monkeypatch.setattr(sh, "_iter_child_pids", lambda: [11, 30, 31, 32, 33])
    monkeypatch.setattr(sh, "_pid_is_running", lambda pid: pid != 33)
    monkeypatch.setattr(sh, "_leftover_tree_roots", lambda pids: [p for p in pids if p != 32])
    killed = []
    monkeypatch.setattr(sh, "_kill_process_tree", lambda pid: killed.append(pid))
    with caplog.at_level(logging.INFO, logger=sh.logger.name):
        sh.reap_child_process_trees()
    assert killed == [11, 30]
    assert "Terminating surviving child process webview (pid=11)" in caplog.text
    assert "Skipping reap of protected spawn child updater (pid=12)" in caplog.text
    assert "Skipping reap of protected leftover tree pid=31" in caplog.text
    assert all(c.calls == [] for c in children)  # no terminate() on Windows


# --------------------------------------------------------------------------- native window

class FakeMainWindow:
    def __init__(self, destroy_error=None):
        self.calls = []
        self.destroy_error = destroy_error

    def destroy(self):
        self.calls.append("destroy")
        if self.destroy_error:
            raise self.destroy_error

    def signal_server_shutdown(self):
        self.calls.append("signal")


def test_close_native_window_destroys_waits_and_reaps(monkeypatch):
    calls = []
    win = FakeMainWindow()
    install_nicegui(monkeypatch, main_window=win)
    monkeypatch.setattr(sh, "_cleanup_pywebview_windows", lambda: calls.append("cleanup"))
    monkeypatch.setattr(sh, "reap_child_process_trees", lambda: calls.append("reap"))
    rounds = [[object()], []]
    monkeypatch.setattr(multiprocessing, "active_children", lambda: rounds.pop(0) if rounds else [])
    sh._close_native_window()
    assert calls == ["cleanup", "reap"]
    assert win.calls == ["destroy"]
    assert rounds == []


def test_close_native_window_times_out_and_logs_destroy_error(monkeypatch, caplog):
    install_nicegui(monkeypatch, main_window=FakeMainWindow(destroy_error=RuntimeError("gone")))
    monkeypatch.setattr(sh, "_cleanup_pywebview_windows", lambda: None)
    reaped = []
    monkeypatch.setattr(sh, "reap_child_process_trees", lambda: reaped.append(1))
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [object()])
    t = {"now": 0.0}

    def mono():
        t["now"] += 1.0
        return t["now"]

    monkeypatch.setattr(sh.time, "monotonic", mono)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._close_native_window()
    assert reaped == [1]
    assert "Native window destroy: gone" in caplog.text


def test_close_native_window_without_window(monkeypatch):
    install_nicegui(monkeypatch, main_window=None)
    monkeypatch.setattr(sh, "_cleanup_pywebview_windows", lambda: None)
    monkeypatch.setattr(sh, "reap_child_process_trees", lambda: None)
    sh._close_native_window()


def test_cleanup_pywebview_without_webview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)
    sh._cleanup_pywebview_windows()
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[]))
    sh._cleanup_pywebview_windows()


def test_cleanup_pywebview_windows_destroys_and_logs(monkeypatch, caplog):
    import platform

    class Win:
        def __init__(self, error=None):
            self.error = error
            self.destroyed = False

        def destroy(self):
            self.destroyed = True
            if self.error:
                raise self.error

    ok = Win()
    webview2 = Win(AttributeError("'NoneType' has no BrowserProcessId"))
    other_attr = Win(AttributeError("weird"))
    generic = Win(RuntimeError("crash"))
    windows = [None, object(), ok, webview2, other_attr, generic]
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=windows))
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._cleanup_pywebview_windows()
    assert ok.destroyed and webview2.destroyed and generic.destroyed
    assert "WebView2 cleanup handled" in caplog.text
    assert "WebView cleanup attribute error: weird" in caplog.text
    assert "Error destroying WebView window: crash" in caplog.text


def test_cleanup_pywebview_attribute_error_off_windows(monkeypatch, caplog):
    import platform

    class Win:
        def destroy(self):
            raise AttributeError("NoneType")

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[Win()]))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with caplog.at_level(logging.ERROR, logger=sh.logger.name):
        sh._cleanup_pywebview_windows()
    assert "WebView cleanup attribute error: NoneType" in caplog.text


class FakeShm:
    instances = []
    missing = False

    def __init__(self, name, create):
        if FakeShm.missing:
            raise FileNotFoundError(name)
        self.name, self.create = name, create
        self.calls = []
        FakeShm.instances.append(self)

    def close(self):
        self.calls.append("close")

    def unlink(self):
        self.calls.append("unlink")


def test_cleanup_shared_memory(monkeypatch, caplog):
    from multiprocessing import shared_memory

    FakeShm.instances, FakeShm.missing = [], False
    monkeypatch.setattr(shared_memory, "SharedMemory", FakeShm)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.cleanup_shared_memory()
    (shm,) = FakeShm.instances
    assert shm.name == "status_flags" and shm.create is False and shm.calls == ["close", "unlink"]
    assert "status_flags cleaned up" in caplog.text
    FakeShm.missing = True
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.cleanup_shared_memory()
    assert "status_flags not found" in caplog.text

    def boom(name, create):
        raise PermissionError("denied")

    monkeypatch.setattr(shared_memory, "SharedMemory", boom)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.cleanup_shared_memory()
    assert "Shared memory cleanup: denied" in caplog.text


def test_signal_native_shutdown(monkeypatch, caplog):
    win = FakeMainWindow()
    install_nicegui(monkeypatch, main_window=win)
    sh._signal_native_shutdown()
    assert win.calls == ["signal"]
    install_nicegui(monkeypatch, main_window=SimpleNamespace())  # no method
    sh._signal_native_shutdown()

    class Bad:
        def signal_server_shutdown(self):
            raise RuntimeError("pipe closed")

    install_nicegui(monkeypatch, main_window=Bad())
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._signal_native_shutdown()
    assert "Native signal_server_shutdown: pipe closed" in caplog.text


def test_hard_exit_after_native_close(monkeypatch, _safety):
    calls = []
    fake_module(monkeypatch, "updater", _force_application_exit=lambda: calls.append("force"))
    sh._hard_exit_after_native_close()
    assert calls == ["force"] and _safety == []

    def boom():
        raise RuntimeError("cannot")

    fake_module(monkeypatch, "updater", _force_application_exit=boom)
    with pytest.raises(Exited):
        sh._hard_exit_after_native_close()
    assert _safety == [1]


class RecThread:
    created = []

    def __init__(self, target=None, name=None, daemon=None, args=()):
        self.target, self.name, self.daemon = target, name, daemon
        self.started = False
        RecThread.created.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def rec_threads(monkeypatch):
    RecThread.created = []
    monkeypatch.setattr(sh.threading, "Thread", RecThread)
    return RecThread


def test_start_native_close_hard_exit_once(rec_threads):
    sh._start_native_close_hard_exit()
    sh._start_native_close_hard_exit()
    (t,) = rec_threads.created
    assert t.target is sh._hard_exit_after_native_close and t.daemon and t.started
    assert t.name == "mycelian-window-closed-exit"


def test_request_native_window_shutdown(monkeypatch, rec_threads):
    calls = []
    monkeypatch.setattr(sh, "_signal_native_shutdown", lambda: calls.append("signal"))
    sh.request_native_window_shutdown()
    assert calls == ["signal"] and len(rec_threads.created) == 1


def test_register_native_window_close_handler(monkeypatch, rec_threads):
    handlers = {}
    em = SimpleNamespace(on=lambda name, fn: handlers.__setitem__(name, fn))
    install_nicegui(monkeypatch, event_manager=em)
    sh.register_native_window_close_handler()
    assert sh._native_close_registered is True and set(handlers) == {"closed"}
    sh.register_native_window_close_handler()  # idempotent
    calls = []
    monkeypatch.setattr(sh, "_signal_native_shutdown", lambda: calls.append("signal"))
    handlers["closed"](None)
    assert calls == ["signal"] and len(rec_threads.created) == 1


def test_register_native_window_close_handler_unavailable(monkeypatch, caplog):
    install_nicegui(monkeypatch)
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", None)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.register_native_window_close_handler()
    assert sh._native_close_registered is False
    assert "event_manager not available" in caplog.text


def test_stop_ui_health_monitor(monkeypatch, caplog):
    calls = []
    fake_module(monkeypatch, "ui_health_monitor", stop_ui_health_monitor=lambda: calls.append(1))
    sh._stop_ui_health_monitor()
    assert calls == [1]
    fake_module(monkeypatch, "ui_health_monitor")  # missing function
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._stop_ui_health_monitor()
    assert "UI health monitor stop:" in caplog.text


def test_stop_tray(monkeypatch, caplog):
    calls = []
    fake_module(monkeypatch, "tray_controller", allow_window_close=lambda: calls.append("allow"),
                shutdown=lambda: calls.append("shutdown"))
    sh._stop_tray()
    assert calls == ["allow", "shutdown"]

    def boom():
        raise RuntimeError("tray gone")

    fake_module(monkeypatch, "tray_controller", allow_window_close=boom, shutdown=lambda: None)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh._stop_tray()
    assert "Tray stop: tray gone" in caplog.text


# --------------------------------------------------------------------------- shutdown_application

@pytest.fixture
def orchestration(monkeypatch):
    calls = []
    monkeypatch.setattr(sh, "_start_shutdown_watchdog", lambda: calls.append("watchdog"))
    monkeypatch.setattr(sh, "_run_step", lambda name, func, timeout=4.0: calls.append((name, func, timeout)))
    monkeypatch.setattr(sh, "_run_parallel", lambda steps, timeout: calls.append(("parallel", [n for n, _ in steps],
                                                                                  timeout)))
    monkeypatch.setattr(sh, "_run_registered_flushes", lambda: calls.append("flushes"))
    exits = []
    fake_module(monkeypatch, "updater", _force_application_exit=lambda: exits.append(1))
    return SimpleNamespace(calls=calls, exits=exits)


def test_shutdown_application_order(orchestration):
    sh.shutdown_application(reason="test")
    names = [c[0] if isinstance(c, tuple) else c for c in orchestration.calls]
    assert names == ["watchdog", "ui_health_monitor", "tray", "pywebview", "pause_alerts", "connection_monitor",
                     "alert_processor", "connectors", "obs", "parallel", "deferred_services", "flushes",
                     "statistics"]
    steps = {c[0]: c for c in orchestration.calls if isinstance(c, tuple)}
    assert steps["pywebview"][1] is sh._close_native_window and steps["pywebview"][2] == 5.0
    assert steps["statistics"][1] is sh._save_statistics and steps["statistics"][2] == 8.0
    assert steps["parallel"][1] == ["youtube", "spotify", "discord", "psn", "twitch", "chatbot"]
    assert steps["parallel"][2] == sh._SHUTDOWN_INTEGRATION_PARALLEL_TIMEOUT
    assert orchestration.exits == []
    assert sh.is_shutdown_in_progress() is True


def test_shutdown_application_idempotent(orchestration, caplog):
    sh.shutdown_application(reason="first")
    n = len(orchestration.calls)
    with caplog.at_level(logging.DEBUG, logger=sh.logger.name):
        sh.shutdown_application(reason="second", force=True)
    assert len(orchestration.calls) == n
    assert orchestration.exits == []
    assert "Shutdown already in progress (reason=second)" in caplog.text


def test_shutdown_application_force_exits(orchestration):
    sh.shutdown_application(reason="signal", force=True)
    assert orchestration.exits == [1]


def test_shutdown_application_logs_finish_even_on_error(monkeypatch, orchestration, caplog):
    def boom(name, func, timeout=4.0):
        raise RuntimeError("step machinery broke")

    monkeypatch.setattr(sh, "_run_step", boom)
    with caplog.at_level(logging.INFO, logger=sh.logger.name), pytest.raises(RuntimeError):
        sh.shutdown_application(reason="x", force=True)
    assert "Application shutdown finished (reason=x)" in caplog.text
    assert orchestration.exits == []

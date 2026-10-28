# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.service_manager`` (deferred service start-up). No real service or thread runs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import contextlib

import pytest

from modules import service_manager as sm


class FakeThread:
    """Records threads instead of starting them; tests run targets by hand."""

    created: list = []

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.target, self.daemon, self.name = target, daemon, name
        self.started = False
        self.alive = False
        self.joined = []
        FakeThread.created.append(self)

    def start(self):
        self.started = True

    def run(self):
        self.target()

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


@pytest.fixture
def env(monkeypatch):
    FakeThread.created = []
    monkeypatch.setattr(sm.threading, "Thread", FakeThread)
    sleeps = []
    monkeypatch.setattr(sm.time, "sleep", lambda s: sleeps.append(s))
    timers = []

    @contextlib.contextmanager
    def fake_timer(name):
        timers.append(name)
        yield

    monkeypatch.setattr(sm, "StartupTimer", fake_timer)
    summaries = []
    monkeypatch.setattr(sm, "log_startup_summary", lambda **kw: summaries.append(kw))
    notices = []
    monkeypatch.setattr(sm, "notify_critical", lambda msg, **kw: notices.append((msg, kw)))
    return {"sleeps": sleeps, "timers": timers, "summaries": summaries, "notices": notices}


def run_thread(name):
    for t in FakeThread.created:
        if t.name == name and t.started and not getattr(t, "_ran", False):
            t._ran = True
            t.run()
            return t
    raise AssertionError(f"no started thread {name}")


# --------------------------------------------------------------------------- _notify_service_gave_up

@pytest.mark.parametrize(
    "name,title,source,actions",
    [
        ("twitch", "Twitch couldn't start", "twitch",
         [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "Twitch"}]),
        ("connectors", "Connectors couldn't start", "connectors", [{"kind": "navigate", "main_tab": "Connectors"}]),
        ("connection_monitor", "The internet connection check couldn't start", "app", None),
        ("weird_service", "Weird service couldn't start", "app", None),
    ],
)
def test_notify_service_gave_up(env, name, title, source, actions):
    sm._notify_service_gave_up(name, ValueError("bad"))
    ((msg, kw),) = env["notices"]
    assert "stays off until Mycelian restarts" in msg
    assert kw["title"] == title and kw["source"] == source and kw["actions"] == actions
    assert kw["details"] == f"{name}: ValueError: bad"
    assert kw["dedupe_key"] == f"deferred_init:{name}"
    assert kw["dedupe_cooldown_sec"] == 3600.0
    assert kw["hint"]


# --------------------------------------------------------------------------- register / status

def test_register_and_is_initialized_and_status():
    m = sm.DeferredServiceManager()
    f = lambda: None
    m.register("a", f, priority=3, background=True)
    m.register("b", f)
    assert m.services["a"] == {"init": f, "priority": 3, "background": True}
    assert m.services["b"]["priority"] == 5 and m.services["b"]["background"] is False
    assert m.is_initialized("a") is False and m.is_initialized("missing") is False
    m.initialized["a"] = True
    assert m.is_initialized("a") is True
    assert m.get_status() == {
        "a": {"priority": 3, "initialized": True, "thread_alive": False},
        "b": {"priority": 5, "initialized": False, "thread_alive": False},
    }
    m._thread = FakeThread()
    m._thread.alive = True
    assert m.get_status()["a"]["thread_alive"] is True


# --------------------------------------------------------------------------- start_deferred_init

def test_start_refused_when_shutting_down(env):
    m = sm.DeferredServiceManager()
    m._shutdown = True
    m.start_deferred_init()
    assert FakeThread.created == []


def test_init_runs_in_priority_order_with_progress(env):
    order, progress = [], []
    m = sm.DeferredServiceManager(progress_callback=lambda p, msg: progress.append((round(p, 3), msg)))
    m.register("late", lambda: order.append("late"), priority=9)
    m.register("early", lambda: order.append("early"), priority=1)
    m.start_deferred_init(delay_seconds=0.5)
    t = FakeThread.created[0]
    assert t.name == "DeferredInit" and t.daemon is True and t.started
    run_thread("DeferredInit")
    assert env["sleeps"] == [0.5]
    assert order == ["early", "late"]
    assert m.initialized == {"late": True, "early": True}
    assert env["timers"] == ["deferred:early", "deferred:late"]
    assert progress == [(0.5, "Loading early..."), (0.9, "Loading late..."), (1.0, "Ready!")]
    assert env["summaries"] == [{"title": "DEFERRED STARTUP TIMING SUMMARY", "prefix": "deferred:"}]


def test_init_without_progress_callback(env):
    m = sm.DeferredServiceManager()
    m.register("a", lambda: None)
    m.register("bg", lambda: None, background=True)
    m.start_deferred_init(0)
    run_thread("DeferredInit")
    assert m.initialized["a"] is True
    assert env["summaries"]


def test_already_initialized_services_are_skipped(env):
    calls = []
    m = sm.DeferredServiceManager()
    m.register("a", lambda: calls.append("a"))
    m.initialized["a"] = True
    m.start_deferred_init(0)
    run_thread("DeferredInit")
    assert calls == []


def test_shutdown_during_delay_cancels(env, monkeypatch):
    m = sm.DeferredServiceManager()
    m.register("a", lambda: pytest.fail("should not init"))
    m.start_deferred_init(0)
    monkeypatch.setattr(sm.time, "sleep", lambda s: setattr(m, "_shutdown", True))
    run_thread("DeferredInit")
    assert env["summaries"] == []


def test_shutdown_mid_loop_interrupts(env):
    calls = []
    m = sm.DeferredServiceManager()

    def first():
        calls.append("first")
        m._shutdown = True

    m.register("first", first, priority=1)
    m.register("second", lambda: calls.append("second"), priority=2)
    m.start_deferred_init(0)
    run_thread("DeferredInit")
    assert calls == ["first"]
    assert env["summaries"] == []  # no "completed" summary after shutdown


def test_background_service_started_on_side_thread(env):
    progress, calls = [], []
    m = sm.DeferredServiceManager(progress_callback=lambda p, msg: progress.append(msg))
    m.register("discord", lambda: calls.append("discord"), background=True)
    m.start_deferred_init(0)
    run_thread("DeferredInit")
    assert calls == []  # not run inline
    assert progress == ["Loading discord...", "Ready!"]
    assert m.initialized["discord"] is False
    run_thread("DeferredInit-discord")
    assert calls == ["discord"] and m.initialized["discord"] is True
    assert "deferred:discord" in env["timers"]


def test_background_service_failure_schedules_retry(env, monkeypatch):
    m = sm.DeferredServiceManager()
    m.register("discord", lambda: (_ for _ in ()).throw(RuntimeError("x")), background=True)
    seen = []
    monkeypatch.setattr(m, "_retry_failed_services", lambda names: seen.append(names))
    m._start_background_service("discord", m.services["discord"])
    run_thread("DeferredInit-discord")
    assert seen == [["discord"]] and m.initialized["discord"] is False


def test_failed_service_continues_and_retries(env, monkeypatch):
    calls = []
    m = sm.DeferredServiceManager()

    def bad():
        calls.append("bad")
        raise RuntimeError("db busy")

    m.register("bad", bad, priority=1)
    m.register("good", lambda: calls.append("good"), priority=2)
    retried = []
    monkeypatch.setattr(m, "_retry_failed_services", lambda names: retried.append(list(names)))
    m.start_deferred_init(0)
    run_thread("DeferredInit")
    assert calls == ["bad", "good"]
    assert retried == [["bad"]]
    assert m.initialized == {"bad": False, "good": True}


# --------------------------------------------------------------------------- _retry_failed_services

def test_retry_recovers_on_second_attempt(env):
    attempts = []
    m = sm.DeferredServiceManager()

    def flaky():
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("busy")

    m.register("flaky", flaky)
    m._retry_failed_services(["flaky"])
    assert m._retry_thread.name == "DeferredInitRetry"
    run_thread("DeferredInitRetry")
    assert m.initialized["flaky"] is True
    assert env["sleeps"] == [10.0, 20.0]
    assert env["notices"] == []


def test_retry_gives_up_after_three_attempts_and_notifies(env):
    m = sm.DeferredServiceManager()
    m.register("obs", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    m._retry_failed_services(["obs"])
    run_thread("DeferredInitRetry")
    assert env["sleeps"] == [10.0, 20.0, 40.0]
    ((msg, kw),) = env["notices"]
    assert kw["title"] == "OBS couldn't start" and "ConnectionError: refused" in kw["details"]


def test_retry_skips_services_initialized_meanwhile(env):
    m = sm.DeferredServiceManager()
    m.register("a", lambda: pytest.fail("already up"))
    m.initialized["a"] = True
    m._retry_failed_services(["a"])
    run_thread("DeferredInitRetry")
    assert env["sleeps"] == [10.0]


def test_retry_stops_when_shutdown_during_sleep(env, monkeypatch):
    m = sm.DeferredServiceManager()
    m.register("a", lambda: pytest.fail("no retry after shutdown"))
    monkeypatch.setattr(sm.time, "sleep", lambda s: setattr(m, "_shutdown", True))
    m._retry_failed_services(["a"])
    run_thread("DeferredInitRetry")


def test_retry_not_run_when_already_shut_down(env):
    m = sm.DeferredServiceManager()
    m.register("a", lambda: pytest.fail("no"))
    m._shutdown = True
    m._retry_failed_services(["a"])
    run_thread("DeferredInitRetry")
    assert env["sleeps"] == []


# --------------------------------------------------------------------------- shutdown

def test_shutdown_without_threads():
    m = sm.DeferredServiceManager()
    m.shutdown()
    assert m._shutdown is True


@pytest.mark.parametrize("stays_alive", [False, True])
def test_shutdown_joins_live_threads(stays_alive):
    m = sm.DeferredServiceManager()
    main, retry = FakeThread(), FakeThread()
    main.alive = retry.alive = True
    if not stays_alive:
        main.join = lambda timeout=None: setattr(main, "alive", False) or main.joined.append(timeout)
        retry.join = lambda timeout=None: setattr(retry, "alive", False) or retry.joined.append(timeout)
    m._thread, m._retry_thread = main, retry
    m.shutdown()
    assert main.joined == [1.0] and retry.joined == [1.0]


def test_shutdown_skips_dead_threads():
    m = sm.DeferredServiceManager()
    m._thread, m._retry_thread = FakeThread(), FakeThread()
    m.shutdown()
    assert m._thread.joined == [] and m._retry_thread.joined == []


# --------------------------------------------------------------------------- singleton

def test_get_service_manager_singleton_and_callback(monkeypatch):
    monkeypatch.setattr(sm._service_manager, "_progress_callback", sm._service_manager._progress_callback)
    assert sm.get_service_manager() is sm._service_manager
    cb = lambda p, m: None
    assert sm.get_service_manager(cb) is sm._service_manager
    assert sm._service_manager._progress_callback is cb
    sm.get_service_manager()
    assert sm._service_manager._progress_callback is cb

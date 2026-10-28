# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.ui_health_monitor`` (black-screen / dead-socket watchdog)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio

import pytest

from modules import ui_health_monitor as hm


class FakeClient:
    def __init__(self, socket=True, result=True, exc=None, deleted=False):
        self.has_socket_connection = socket
        self.result, self.exc = result, exc
        self.is_deleted = deleted
        self.js = []

    async def run_javascript(self, code, timeout=None):
        self.js.append((code, timeout))
        if self.exc:
            raise self.exc
        return self.result


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(monkeypatch):
    for name, value in {
        "_MONITOR_STARTED": False, "_PING_SCRIPT_INJECTED": False, "_SUSPENDED": False,
        "_health_client": None, "_armed_mono": 0.0, "_consecutive_ping_failures": 0,
        "_last_reload_mono": 0.0, "_reload_count": 0,
    }.items():
        monkeypatch.setattr(hm, name, value)
    clock = Clock()
    monkeypatch.setattr(hm.time, "monotonic", clock)
    tasks = []
    monkeypatch.setattr(hm.background_tasks, "create", lambda coro, name=None: tasks.append((name, coro)))
    shutdown = {"on": False}
    monkeypatch.setattr(hm, "is_shutdown_in_progress", lambda: shutdown["on"])
    scheduled = []
    monkeypatch.setattr(hm, "app_schedule", lambda *a, **k: scheduled.append((a, k)))
    from nicegui import Client

    monkeypatch.setattr(Client, "instances", {})
    yield {"clock": clock, "tasks": tasks, "shutdown": shutdown, "scheduled": scheduled}
    for _name, coro in tasks:
        coro.close()


def run_tasks(env):
    while env["tasks"]:
        _name, coro = env["tasks"].pop(0)
        asyncio.run(coro)


def armed(env, monkeypatch, client=None, ago=500.0):
    monkeypatch.setattr(hm, "_health_client", client)
    monkeypatch.setattr(hm, "_armed_mono", env["clock"].t - ago)


# --------------------------------------------------------------------------- helpers

def test_inject_script_once(env, monkeypatch):
    from nicegui import ui

    calls = []
    monkeypatch.setattr(ui, "add_head_html", lambda html, shared=False: calls.append((html, shared)))
    hm._inject_client_watchdog_script()
    hm._inject_client_watchdog_script()
    assert calls == [(hm._CLIENT_WATCHDOG_SCRIPT, True)]
    assert hm._PING_SCRIPT_INJECTED is True


def test_get_health_client(env, monkeypatch):
    assert hm._get_health_client() is None
    c = FakeClient()
    monkeypatch.setattr(hm, "_health_client", c)
    assert hm._get_health_client() is c
    c.is_deleted = True
    assert hm._get_health_client() is None

    class Broken:
        @property
        def is_deleted(self):
            raise RuntimeError("x")

    monkeypatch.setattr(hm, "_health_client", Broken())
    assert hm._get_health_client() is None


def test_any_client_has_socket(env, monkeypatch):
    from nicegui import Client

    assert hm._any_client_has_socket() is False
    monkeypatch.setattr(hm, "_health_client", FakeClient(socket=True))
    assert hm._any_client_has_socket() is True
    monkeypatch.setattr(hm, "_health_client", FakeClient(socket=False))
    monkeypatch.setattr(Client, "instances", {"a": FakeClient(socket=False), "b": FakeClient(socket=True)})
    assert hm._any_client_has_socket() is True
    monkeypatch.setattr(Client, "instances", None)  # .values() blows up -> swallowed
    assert hm._any_client_has_socket() is False


def test_in_startup_grace(env, monkeypatch):
    assert hm._in_startup_grace() is True  # not armed
    monkeypatch.setattr(hm, "_armed_mono", env["clock"].t - 10)
    assert hm._in_startup_grace() is True
    monkeypatch.setattr(hm, "_armed_mono", env["clock"].t - hm._STARTUP_GRACE_SEC)
    assert hm._in_startup_grace() is False


def test_set_monitor_suspended(env, monkeypatch):
    monkeypatch.setattr(hm, "_consecutive_ping_failures", 2)
    monkeypatch.setattr(hm, "_reload_count", 4)
    hm.set_monitor_suspended(False)  # no change -> nothing reset
    assert hm._consecutive_ping_failures == 2
    hm.set_monitor_suspended(True)
    assert hm._SUSPENDED is True and hm._consecutive_ping_failures == 0 and hm._reload_count == 4
    hm.set_monitor_suspended(False)
    assert hm._SUSPENDED is False and hm._armed_mono == env["clock"].t and hm._reload_count == 0


@pytest.mark.parametrize(
    "msg,expected",
    [("The slot stack is empty", True), ("slot cannot be determined", True), ("in a background task", True),
     ("Client has been deleted", True), ("client gone", False), ("timeout", False)],
)
def test_is_not_ready_error(msg, expected):
    assert hm._is_not_ready_error(RuntimeError(msg)) is expected


# --------------------------------------------------------------------------- _trigger_reload

def test_trigger_reload_happy_path(env, monkeypatch):
    c = FakeClient()
    armed(env, monkeypatch, c)
    hm._trigger_reload("why")
    assert hm._reload_count == 1 and hm._last_reload_mono == env["clock"].t
    assert [n for n, _ in env["tasks"]] == ["ui_health_reload"]
    run_tasks(env)
    assert c.js == [("window.location.reload()", 5.0)]


def test_trigger_reload_js_failure_logged(env, monkeypatch, caplog):
    c = FakeClient(exc=RuntimeError("gone"))
    armed(env, monkeypatch, c)
    hm._trigger_reload("why")
    run_tasks(env)
    assert "reload failed" in caplog.text


@pytest.mark.parametrize("case", ["shutdown", "suspended", "cooldown", "cap", "no_client", "no_socket"])
def test_trigger_reload_guards(env, monkeypatch, case):
    c = FakeClient()
    armed(env, monkeypatch, c)
    if case == "shutdown":
        env["shutdown"]["on"] = True
    elif case == "suspended":
        monkeypatch.setattr(hm, "_SUSPENDED", True)
    elif case == "cooldown":
        monkeypatch.setattr(hm, "_last_reload_mono", env["clock"].t - 5)
    elif case == "cap":
        monkeypatch.setattr(hm, "_reload_count", hm._MAX_RELOADS_PER_SESSION)
    elif case == "no_client":
        monkeypatch.setattr(hm, "_health_client", None)
    else:
        c.has_socket_connection = False
    before = hm._reload_count
    hm._trigger_reload("why")
    assert env["tasks"] == [] and hm._reload_count == before


# --------------------------------------------------------------------------- _on_ping_check_result

def test_ping_result_ignored_in_grace_or_shutdown(env, monkeypatch):
    hm._on_ping_check_result(False)  # not armed => grace
    assert hm._consecutive_ping_failures == 0
    armed(env, monkeypatch, FakeClient())
    env["shutdown"]["on"] = True
    hm._on_ping_check_result(False)
    assert hm._consecutive_ping_failures == 0


def test_ping_ok_resets_failures(env, monkeypatch):
    armed(env, monkeypatch, FakeClient())
    monkeypatch.setattr(hm, "_consecutive_ping_failures", 2)
    hm._on_ping_check_result(True)
    assert hm._consecutive_ping_failures == 0


def test_ping_false_reloads_after_three(env, monkeypatch):
    armed(env, monkeypatch, FakeClient())
    reasons = []
    monkeypatch.setattr(hm, "_trigger_reload", reasons.append)
    hm._on_ping_check_result(False)
    hm._on_ping_check_result(False)
    assert reasons == []
    hm._on_ping_check_result(False)
    assert reasons == ["ping_or_socket_dead"] and hm._consecutive_ping_failures == 3


def test_dead_socket_counts_as_failure(env, monkeypatch, caplog):
    armed(env, monkeypatch, FakeClient(socket=False))
    reasons = []
    monkeypatch.setattr(hm, "_trigger_reload", reasons.append)
    for _ in range(3):
        hm._on_ping_check_result(True)
    assert reasons == ["ping_or_socket_dead"]
    assert "socket_dead" in caplog.text


def test_missing_shell_reloads_after_two(env, monkeypatch, caplog):
    armed(env, monkeypatch, FakeClient())
    reasons = []
    monkeypatch.setattr(hm, "_trigger_reload", reasons.append)
    hm._on_ping_check_result(None)
    assert reasons == []
    hm._on_ping_check_result(None)
    assert reasons == ["full_black"] and "full_black" in caplog.text


# --------------------------------------------------------------------------- health checks

def test_async_check_skips_in_grace_or_without_socket(env, monkeypatch):
    c = FakeClient()
    asyncio.run(hm._run_health_check_async(c))  # grace
    armed(env, monkeypatch, c)
    c.has_socket_connection = False
    asyncio.run(hm._run_health_check_async(c))
    assert c.js == []


def test_async_check_feeds_result(env, monkeypatch):
    c = FakeClient(result=None)
    armed(env, monkeypatch, c)
    seen = []
    monkeypatch.setattr(hm, "_on_ping_check_result", seen.append)
    asyncio.run(hm._run_health_check_async(c))
    assert seen == [None] and c.js == [(hm._DOM_CHECK_JS, 5.0)]


@pytest.mark.parametrize("msg,expected", [("slot stack", []), ("timeout", [False])])
def test_async_check_errors(env, monkeypatch, msg, expected):
    c = FakeClient(exc=RuntimeError(msg))
    armed(env, monkeypatch, c)
    seen = []
    monkeypatch.setattr(hm, "_on_ping_check_result", seen.append)
    asyncio.run(hm._run_health_check_async(c))
    assert seen == expected


def test_run_health_check_schedules_async(env, monkeypatch):
    c = FakeClient()
    armed(env, monkeypatch, c)
    hm._run_health_check()
    assert [n for n, _ in env["tasks"]] == ["ui_health_check"]


@pytest.mark.parametrize("case", ["suspended", "grace", "shutdown", "no_client", "no_socket"])
def test_run_health_check_skips(env, monkeypatch, case):
    c = FakeClient()
    armed(env, monkeypatch, c)
    if case == "suspended":
        monkeypatch.setattr(hm, "_SUSPENDED", True)
    elif case == "grace":
        monkeypatch.setattr(hm, "_armed_mono", 0.0)
    elif case == "shutdown":
        env["shutdown"]["on"] = True
    elif case == "no_client":
        monkeypatch.setattr(hm, "_health_client", None)
    else:
        c.has_socket_connection = False
    hm._run_health_check()
    assert env["tasks"] == []


# --------------------------------------------------------------------------- arm / start / stop

def test_arm_starts_once_and_rebinds(env, monkeypatch):
    injected = []
    monkeypatch.setattr(hm, "_inject_client_watchdog_script", lambda: injected.append(1))
    monkeypatch.setattr(hm, "_reload_count", 3)
    c1, c2 = FakeClient(), FakeClient()
    hm.arm_ui_health_monitor(c1)
    assert hm._health_client is c1 and hm._armed_mono == env["clock"].t and hm._reload_count == 0
    assert hm._MONITOR_STARTED is True and injected == [1]
    assert env["scheduled"] == [((hm._PING_INTERVAL_SEC, hm._run_health_check), {"active": True})]
    env["clock"].t += 5
    hm.arm_ui_health_monitor(c2)
    assert hm._health_client is c2 and hm._armed_mono == env["clock"].t
    assert injected == [1] and len(env["scheduled"]) == 1


def test_start_alias_uses_context_client(env, monkeypatch):
    from nicegui import context

    seen = []
    monkeypatch.setattr(hm, "arm_ui_health_monitor", seen.append)
    sentinel = object()
    monkeypatch.setattr(type(context), "client", property(lambda self: sentinel), raising=False)
    hm.start_ui_health_monitor()
    assert seen == [sentinel]


def test_start_alias_swallows_errors(env, monkeypatch):
    def boom(_c):
        raise RuntimeError("no slot")

    monkeypatch.setattr(hm, "arm_ui_health_monitor", boom)
    hm.start_ui_health_monitor()  # must not raise


def test_stop_resets_state(env, monkeypatch):
    monkeypatch.setattr(hm, "_MONITOR_STARTED", True)
    monkeypatch.setattr(hm, "_consecutive_ping_failures", 2)
    monkeypatch.setattr(hm, "_health_client", FakeClient())
    monkeypatch.setattr(hm, "_armed_mono", 5.0)
    monkeypatch.setattr(hm, "_SUSPENDED", True)
    hm.stop_ui_health_monitor()
    assert (hm._MONITOR_STARTED, hm._consecutive_ping_failures, hm._health_client, hm._armed_mono, hm._SUSPENDED) == (
        False, 0, None, 0.0, False)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks_service.py (hook workers, coordinator loop, commands, UI snapshots)."""

from __future__ import annotations

import concurrent.futures
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import game_hooks_service as ghs  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeHook:
    def __init__(self, hook_id="ff7", running=True):
        self.hook_id = hook_id
        self.running = running
        self.enqueue = None
        self.closed = 0
        self.commands = []
        self.ops = []
        self.ticks = 0
        self.tick_result = {"attached": True}
        self.tick_error = None
        self.op_error = None
        self.close_error = None
        self.timed = []
        self.reloaded = 0

    def set_write_enqueue(self, enqueue):
        self.enqueue = enqueue

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error

    def handle_command(self, action, data):
        self.commands.append((action, data))

    def execute_operation(self, op, kwargs):
        if self.op_error:
            raise self.op_error
        self.ops.append((op, kwargs))
        return True, None, {"k": "v"}

    def drain_timed_jobs(self, enqueue_write):
        for op, kw in self.timed:
            enqueue_write(op, kw)
        self.timed = []

    def tick(self):
        self.ticks += 1
        if self.tick_error:
            raise self.tick_error
        return self.tick_result

    def idle_snapshot(self, *, disabled=False):
        return {"hook": self.hook_id, "attached": False, "disabled": disabled}

    def is_process_running(self):
        return self.running

    def on_config_reloaded(self):
        self.reloaded += 1


class OneShotEvent:
    """Stop event that lets a loop run ``n`` iterations."""

    def __init__(self, n=1):
        self.n = n
        self.waits = []
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.n -= 1
        return self.n <= 0


class FakeThread:
    def __init__(self, alive=True):
        self.alive = alive
        self.joined = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


@pytest.fixture(autouse=True)
def fast_interval(monkeypatch):
    monkeypatch.setattr(ghs, "_INTERVAL", 0.001)


@pytest.fixture
def svc(monkeypatch):
    s = ghs.GameHooksServiceImpl()
    hooks = {"ff7": FakeHook("ff7"), "factorio": FakeHook("factorio")}
    enabled = {"ff7": True, "factorio": False}
    monkeypatch.setattr(ghs, "create_hook", lambda hid: hooks.get(hid))
    monkeypatch.setattr(ghs, "registered_hook_ids", lambda: list(hooks))
    monkeypatch.setattr(ghs, "is_hook_enabled", lambda hid: enabled.get(hid, False))
    emitted = []
    s._emit = lambda payload: emitted.append(payload)
    yield types.SimpleNamespace(s=s, hooks=hooks, enabled=enabled, emitted=emitted)
    s.stop()


# --------------------------------------------------------------------------- _HookWorker


def test_worker_init_wires_enqueue():
    h = FakeHook()
    w = ghs._HookWorker(h)
    assert w.hook_id == "ff7" and callable(h.enqueue)
    h.enqueue("op", {"a": 1})
    fut, op, kw = w._write_queue.get_nowait()
    assert op == "op" and kw == {"a": 1} and not fut.done()


def test_worker_enqueue_and_drain_ops():
    h = FakeHook()
    w = ghs._HookWorker(h)
    args = {"x": 1}
    fut = w.enqueue("add_gil", args)
    cmd = w.enqueue("__command__", {"action": "clear", "data": None})
    cmd2 = w.enqueue("__command__", {})
    w._drain_writes()
    assert fut.result(0) == (True, None, {"k": "v"})
    assert h.ops == [("add_gil", {"x": 1})] and h.ops[0][1] is not args
    assert cmd.result(0) == (True, None, None) and cmd2.result(0) == (True, None, None)
    assert h.commands == [("clear", {}), ("", {})]


def test_worker_drain_op_exception():
    h = FakeHook()
    h.op_error = RuntimeError("bad write")
    w = ghs._HookWorker(h)
    fut = w.enqueue("x", {})
    w._drain_writes()
    assert fut.result(0) == (False, "bad write", None)


def test_worker_queue_full(monkeypatch):
    monkeypatch.setattr(ghs, "_WRITE_QUEUE_MAXSIZE", 1)
    w = ghs._HookWorker(FakeHook())
    w.enqueue("a", {})
    fut = w.enqueue("b", {})
    assert fut.result(0) == (False, "write queue full", None)
    done = concurrent.futures.Future()
    done.set_result("already")
    w._enqueue_or_drop(done, "c", {})
    assert done.result(0) == "already"


def test_worker_snapshots():
    w = ghs._HookWorker(FakeHook())
    assert w.peek_snapshot() is None and w.get_snapshot() is None
    snap = {"a": 1}
    w._last_snapshot = snap
    assert w.peek_snapshot() is snap
    got = w.get_snapshot()
    assert got == snap and got is not snap


def test_worker_loop_one_iteration():
    h = FakeHook()
    h.timed = [("restore", {"v": 1})]
    w = ghs._HookWorker(h)
    pre = w.enqueue("op1", {})
    w._stop = OneShotEvent(1)
    w._loop()
    assert pre.result(0)[0] is True
    assert w.peek_snapshot() == {"attached": True}
    # timed job was queued for the next drain
    fut, op, kw = w._write_queue.get_nowait()
    assert (op, kw) == ("restore", {"v": 1})
    assert 0.0 <= w._stop.waits[0] <= ghs._INTERVAL


def test_worker_loop_tick_error_uses_idle_snapshot():
    h = FakeHook()
    h.tick_error = ValueError("read fail")
    w = ghs._HookWorker(h)
    w._stop = OneShotEvent(2)
    w._loop()
    assert h.ticks == 2
    assert w.peek_snapshot() == {"hook": "ff7", "attached": False, "disabled": False, "error": "read fail"}


def test_worker_loop_exits_when_already_stopped():
    h = FakeHook()
    w = ghs._HookWorker(h)
    ev = OneShotEvent(5)
    ev.set()
    w._stop = ev
    w._loop()
    assert h.ticks == 0


def test_worker_start_stop_real_thread():
    h = FakeHook()
    w = ghs._HookWorker(h)
    w.start()
    try:
        first = w._thread
        assert w.is_alive()
        w.start()  # already running: no new thread
        assert w._thread is first
    finally:
        assert w.stop(timeout=2.0) is True
    assert not w.is_alive() and w._thread is None and h.closed == 1
    assert h.ticks >= 1


def test_worker_stop_thread_stuck():
    h = FakeHook()
    w = ghs._HookWorker(h)
    w._thread = FakeThread(alive=True)
    assert w.stop(timeout=0.01) is False
    assert h.closed == 0 and w._thread is not None


def test_worker_stop_without_thread_and_close_error():
    h = FakeHook()
    h.close_error = RuntimeError("x")
    w = ghs._HookWorker(h)
    assert w.stop() is True
    assert h.closed == 1
    assert w.is_alive() is False


# --------------------------------------------------------------------------- service basics


def test_get_hook_caches_and_normalizes(svc):
    s = svc.s
    assert s._get_hook(" FF7 ") is svc.hooks["ff7"]
    assert s._hooks == {"ff7": svc.hooks["ff7"]}
    assert s._get_hook(None) is None and s._get_hook("nope") is None
    assert "nope" not in s._hooks


def test_enqueue_write_no_worker(svc):
    fut = svc.s.enqueue_game_hook_write(" FF7 ", "add_gil", None)
    assert fut.result(0) == (False, "No active hook for 'ff7'", None)


def test_enqueue_write_live_worker(svc):
    w = ghs._HookWorker(svc.hooks["ff7"])
    w._thread = FakeThread(alive=True)
    svc.s._workers["ff7"] = w
    fut = svc.s.enqueue_game_hook_write("ff7", "add_gil", {"amount": 5})
    _, op, kw = w._write_queue.get_nowait()
    assert (op, kw) == ("add_gil", {"amount": 5}) and not fut.done()
    w._thread = FakeThread(alive=False)
    assert svc.s.enqueue_game_hook_write("ff7", "x").result(0)[0] is False


def test_reload_hook_config(svc):
    svc.s.reload_hook_config("ff7")
    svc.s.reload_ff7_boss_match_sets()
    svc.s.reload_hook_config("unknown")
    assert svc.hooks["ff7"].reloaded == 2


def test_clear_ff7_bosses_paths(svc, monkeypatch):
    svc.s.clear_ff7_bosses()
    assert svc.hooks["ff7"].commands == [("clear_bosses", {})]
    w = ghs._HookWorker(svc.hooks["ff7"])
    w._thread = FakeThread(alive=True)
    svc.s._workers["ff7"] = w
    svc.s.clear_ff7_bosses()
    _, op, kw = w._write_queue.get_nowait()
    assert (op, kw) == ("__command__", {"action": "clear_bosses", "data": {}})
    svc.s._workers.clear()
    monkeypatch.setattr(ghs, "create_hook", lambda hid: None)
    svc.s._hooks.clear()
    svc.s.clear_ff7_bosses()  # no hook: nothing happens


def test_stop_worker(svc):
    svc.s._stop_worker("ff7")  # none: no-op
    w = ghs._HookWorker(svc.hooks["ff7"])
    w._thread = FakeThread(alive=True)
    svc.s._workers["ff7"] = w
    svc.s._stop_worker("ff7")
    assert "ff7" in svc.s._workers  # stuck thread kept
    w._thread = FakeThread(alive=False)
    svc.s._stop_worker("ff7")
    assert "ff7" not in svc.s._workers


def test_ensure_worker(svc, monkeypatch):
    started = []
    monkeypatch.setattr(ghs._HookWorker, "start", lambda self: started.append(self))
    hook = svc.hooks["ff7"]
    w1 = svc.s._ensure_worker(hook)
    assert started == [w1] and svc.s._workers["ff7"] is w1
    w1._thread = FakeThread(alive=True)
    assert svc.s._ensure_worker(hook) is w1
    w1._thread = FakeThread(alive=False)
    w2 = svc.s._ensure_worker(hook)
    assert w2 is not w1 and started == [w1, w2] and hook.closed == 1


# --------------------------------------------------------------------------- emit


def test_emit_uses_web_engine(monkeypatch):
    from modules import web_engine

    sent = []
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        types.SimpleNamespace(safe_emit=lambda ev, p: sent.append((ev, p))), raising=False)
    s = ghs.GameHooksServiceImpl()
    s._emit({"v": 1})
    assert sent == [("game_hook_payload", {"v": 1})]
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    s._emit({"v": 2})
    assert len(sent) == 1


def test_emit_swallows_errors(monkeypatch, caplog):
    from modules import web_engine

    def boom(ev, p):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(web_engine, "web_engine_instance", types.SimpleNamespace(safe_emit=boom), raising=False)
    ghs.GameHooksServiceImpl()._emit({})
    assert "emit failed" in caplog.text


def test_emit_last_payload(svc, monkeypatch):
    svc.s.emit_last_payload()
    assert svc.emitted == []
    svc.s._ui_snapshots = {"ff7": {"a": 1}, "factorio": None}
    monkeypatch.setattr(ghs.time, "time", lambda: 12.5)
    svc.s.emit_last_payload()
    assert svc.emitted == [{"v": 1, "ts": 12500, "hooks": {"ff7": {"a": 1}}}]


# --------------------------------------------------------------------------- coordinator


def test_coordinator_states(svc, monkeypatch):
    s = svc.s
    started = []
    monkeypatch.setattr(ghs._HookWorker, "start", lambda self: started.append(self))
    svc.hooks["broken"] = None
    s._stop = OneShotEvent(1)
    s._coordinator_loop()
    payload = svc.emitted[-1]
    assert payload["v"] == 1 and isinstance(payload["ts"], int)
    # ff7 enabled + running -> worker started, no snapshot yet -> idle (not disabled)
    assert payload["hooks"]["ff7"] == {"hook": "ff7", "attached": False, "disabled": False}
    # factorio disabled -> disabled idle snapshot
    assert payload["hooks"]["factorio"]["disabled"] is True
    assert "broken" not in payload["hooks"]
    assert len(started) == 1
    assert s._ui_snapshots["factorio"]["disabled"] is True
    # loop end stops workers
    assert s._workers == {} or all(not w.is_alive() for w in s._workers.values())


def test_coordinator_uses_worker_snapshot_and_not_running(svc, monkeypatch):
    s = svc.s
    w = ghs._HookWorker(svc.hooks["ff7"])
    w._thread = FakeThread(alive=True)
    w._last_snapshot = {"attached": True, "battle": True}
    s._workers["ff7"] = w
    monkeypatch.setattr(s, "_stop_worker", lambda hid: None)  # keep the fake worker
    svc.enabled["factorio"] = True
    svc.hooks["factorio"].running = False
    s._stop = OneShotEvent(1)
    s._coordinator_loop()
    hooks = svc.emitted[-1]["hooks"]
    assert hooks["ff7"] is w._last_snapshot
    assert hooks["factorio"] == {"hook": "factorio", "attached": False, "disabled": False}


def test_coordinator_unchanged_reuses_last_hooks(svc, monkeypatch):
    s = svc.s
    svc.enabled["ff7"] = False
    s._stop = OneShotEvent(3)
    s._coordinator_loop()
    assert len(svc.emitted) == 3
    assert svc.emitted[1]["hooks"] is svc.emitted[2]["hooks"] is s._last_hooks
    assert s._emit_count == 3


def test_coordinator_debug_log_cadence(svc, caplog):
    import logging

    s = svc.s
    s._emit_count = 19
    svc.hooks.pop("factorio")
    svc.hooks["ff7"] = FakeHook("ff7")
    svc.enabled["ff7"] = False
    s._stop = OneShotEvent(1)
    with caplog.at_level(logging.DEBUG, logger=ghs.__name__):
        s._coordinator_loop()
    assert "[GameHooks] emit #20" in caplog.text


def test_coordinator_non_dict_snapshot(svc, monkeypatch):
    s = svc.s
    svc.hooks.pop("factorio")
    svc.hooks["ff7"].idle_snapshot = lambda disabled=False: "weird"
    svc.enabled["ff7"] = False
    s._stop = OneShotEvent(1)
    s._coordinator_loop()
    assert s._ui_snapshots["ff7"] == "weird"
    assert s.get_hook_ui_snapshot("ff7") == {"service_running": False, "ff7": None}


def test_start_and_stop_real_thread(svc, monkeypatch):
    s = svc.s
    refreshed = []
    monkeypatch.setattr(ghs, "refresh_hook_enabled_cache", lambda: refreshed.append(1))
    svc.enabled["ff7"] = False
    s.start()
    try:
        first = s._thread
        assert first.is_alive()
        s.start()
        assert s._thread is first and refreshed == [1]
    finally:
        s.stop()
    assert s._thread is None and not first.is_alive()


def test_start_refresh_error_still_starts(svc, monkeypatch):
    def boom():
        raise RuntimeError("db")

    monkeypatch.setattr(ghs, "refresh_hook_enabled_cache", boom)
    svc.enabled["ff7"] = False
    svc.s.start()
    assert svc.s._thread.is_alive()
    svc.s.stop()


def test_stop_stops_workers(svc):
    w = ghs._HookWorker(svc.hooks["ff7"])
    svc.s._workers["ff7"] = w
    svc.s.stop()
    assert svc.s._workers == {} and svc.hooks["ff7"].closed == 1


# --------------------------------------------------------------------------- UI snapshot helpers


def test_ui_snapshot_and_ready(svc):
    s = svc.s
    assert s.get_ff7_ui_snapshot() == {"service_running": False, "ff7": None}
    s._ui_snapshots["ff7"] = {"attached": True}
    assert not s.is_game_hook_ready("ff7")  # service not running
    s._thread = FakeThread(alive=True)
    snap = s.get_hook_ui_snapshot(" FF7 ")
    assert snap == {"service_running": True, "ff7": {"attached": True}}
    assert snap["ff7"] is not s._ui_snapshots["ff7"]
    assert s.is_game_hook_ready("ff7") is True
    s._ui_snapshots["ff7"] = {"attached": True, "disabled": True}
    assert s.is_game_hook_ready("ff7") is False
    s._ui_snapshots["ff7"] = {"attached": False}
    assert s.is_game_hook_ready("ff7") is False
    s._ui_snapshots["ff7"] = None
    assert s.is_game_hook_ready("ff7") is False
    assert s.is_game_hook_ready("factorio") is False  # disabled in settings
    s._thread = None


# --------------------------------------------------------------------------- handle_game_hook_command


@pytest.fixture
def global_svc(monkeypatch, svc):
    monkeypatch.setattr(ghs, "game_hooks_service", svc.s)
    return svc


@pytest.mark.parametrize("data", [None, "x", {}, {"hook": "ff7"}, {"action": "go"}, {"hook": " ", "action": "go"}])
def test_command_ignored(global_svc, data):
    ghs.handle_game_hook_command(data)
    assert global_svc.hooks["ff7"].commands == [] and global_svc.emitted == []


def test_command_resync(global_svc):
    global_svc.s._ui_snapshots["ff7"] = {"a": 1}
    ghs.handle_game_hook_command({"hook": "FF7", "action": " resync "})
    assert global_svc.emitted[-1]["hooks"] == {"ff7": {"a": 1}}


def test_command_to_live_worker(global_svc):
    w = ghs._HookWorker(global_svc.hooks["ff7"])
    w._thread = FakeThread(alive=True)
    global_svc.s._workers["ff7"] = w
    data = {"hook": "ff7", "action": "clear_bosses"}
    ghs.handle_game_hook_command(data)
    _, op, kw = w._write_queue.get_nowait()
    assert op == "__command__" and kw == {"action": "clear_bosses", "data": data}
    global_svc.s._workers.clear()


def test_command_direct_to_hook_and_unknown(global_svc):
    data = {"hook": "ff7", "action": "clear_bosses"}
    ghs.handle_game_hook_command(data)
    assert global_svc.hooks["ff7"].commands == [("clear_bosses", data)]
    ghs.handle_game_hook_command({"hook": "nope", "action": "x"})


def test_module_singleton():
    assert isinstance(ghs.game_hooks_service, ghs.GameHooksServiceImpl)
    assert isinstance(threading.Event(), type(ghs.GameHooksServiceImpl()._stop))

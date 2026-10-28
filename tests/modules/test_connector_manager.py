# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_manager: helpers, start / supervised thread, event pipeline, load / save / recover,
CRUD, hotkey sync, serialization, test runs, scheduler and module functions (all faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import contextlib
import threading
import types
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import pytest

import modules
from modules import connector_manager as cm
from modules.connector_actions import create_action
from modules.connector_core import (
    ActionType,
    BaseAction,
    ComparisonOperator as Op,
    Connector,
    TriggerCondition,
    TriggerType,
)
from modules.connector_triggers import create_trigger


def run(coro):
    return asyncio.run(coro)


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


class FakeHotkeys:
    def __init__(self):
        self.calls = []

    def register_hotkey(self, cid, combo, is_global=True):
        self.calls.append(("register", cid, combo, is_global))

    def unregister_hotkey(self, cid):
        self.calls.append(("unregister", cid))


class FakeDB:
    def __init__(self):
        self.stored = {}
        self.read_ok = True
        self.write_ok = True
        self.problem = None
        self.writes = []
        self.raise_on_write = None

    def get_data_checked(self, key):
        assert key == "connectors"
        return (dict(self.stored) if self.read_ok else {}), self.read_ok

    def set_data(self, key, value):
        if self.raise_on_write:
            raise self.raise_on_write
        self.writes.append(value)
        if self.write_ok:
            self.stored = dict(value)
        return self.write_ok

    @contextlib.contextmanager
    def quiet_notifications(self):
        yield

    def last_write_problem(self):
        return self.problem


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    for name in ("get_data_checked", "set_data", "quiet_notifications", "last_write_problem"):
        monkeypatch.setattr(cm.database_manager, name, getattr(db, name))
    notes = []
    monkeypatch.setattr(cm, "notify", lambda msg, **kw: notes.append(("notify", msg, kw)))
    monkeypatch.setattr(cm, "notify_critical", lambda msg, **kw: notes.append(("critical", msg, kw)))
    monkeypatch.setattr(cm, "nav_actions_main_tab", lambda tab: [("nav", tab)])
    stats = SimpleNamespace(calls=[])
    stats.increment_connector_triggers = lambda n, **kw: stats.calls.append((n, kw))
    monkeypatch.setattr(cm, "get_statistics_manager", lambda: stats)
    hotkeys = FakeHotkeys()
    monkeypatch.setattr(cm, "get_listener", lambda: hotkeys)
    manager = cm.ConnectorManager()
    return SimpleNamespace(db=db, notes=notes, stats=stats, hotkeys=hotkeys, m=manager, mp=monkeypatch)


def chat_action(i="a1", message="hi"):
    return create_action(ActionType.SEND_CHAT_MESSAGE, i, "Say", message=message)


def make_connector(cid="c1", trigger_type=TriggerType.TWITCH_BITS, enabled=True, actions=None, **trig_kw):
    trigger = create_trigger(trigger_type, f"t-{cid}", "T", **trig_kw)
    return Connector(connector_id=cid, name=f"Conn {cid}", enabled=enabled, trigger=trigger,
                     actions=actions if actions is not None else [chat_action()])


def hotkey_connector(cid="h1", enabled=True, combo="ctrl+f1"):
    return make_connector(cid, TriggerType.HOTKEY, enabled=enabled, key_combination=combo)


# --------------------------------------------------------------------------- module helpers


@pytest.mark.parametrize(
    "ttype,field,value,expected",
    [
        ("twitch_sub", "tier", "2000", "2"),
        ("twitch_resub", "tier", 3000, 3),
        ("any", "tier", 1000.0, 1),
        ("twitch_sub", "tier", 2, 2),
        ("twitch_sub", "tier", True, True),
        ("twitch_sub", "tier", "abc", "abc"),
        ("twitch_sub", "tier", None, None),
        ("twitch_bits", "tier", "2000", "2000"),
        ("twitch_sub", "amount", "2000", "2000"),
    ],
)
def test_migrate_condition_value(ttype, field, value, expected):
    assert cm._migrate_condition_value(ttype, field, value) == expected


def test_normalize_join():
    assert cm._normalize_join(" OR ") == "or"
    assert cm._normalize_join(None) == "and" and cm._normalize_join("xor") == "and"


def test_connector_listens_for():
    assert not cm._connector_listens_for(SimpleNamespace(), "twitch_bits")
    bits = make_connector()
    assert cm._connector_listens_for(bits, "twitch_bits")
    assert not cm._connector_listens_for(bits, "twitch_sub")
    assert cm._connector_listens_for(make_connector(trigger_type=TriggerType.ANY), "whatever")
    assert cm._connector_listens_for(SimpleNamespace(trigger=object()), "x")  # unknown class runs


# --------------------------------------------------------------------------- start / thread


def test_start_loads_once(env):
    calls = []
    env.mp.setattr(env.m, "load_connectors", lambda: calls.append(1))
    env.m.start()
    env.m.start()
    assert env.m.is_running and calls == [1] and env.m.processing_task is None


class FakeTask:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


class FakeLoop:
    def __init__(self, run_error=None, close_error=None):
        self.run_error = run_error
        self.close_error = close_error
        self.tasks = []
        self.closed = False

    def create_task(self, coro):
        coro.close()
        task = FakeTask()
        self.tasks.append(task)
        return task

    def run_forever(self):
        if self.run_error:
            raise self.run_error

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error

    def is_running(self):
        return True


def test_reset_event_pipeline_cancels_old_tasks(env):
    old_p, old_s = FakeTask(), FakeTask()
    env.m.processing_task, env.m._scheduler_task = old_p, old_s
    env.m._loop_mismatch_errors = 5
    loop = FakeLoop()
    env.m._reset_event_pipeline(loop)
    assert old_p.cancelled and old_s.cancelled
    assert isinstance(env.m.event_queue, asyncio.Queue)
    assert env.m.processing_task is loop.tasks[0] and env.m._scheduler_task is loop.tasks[1]
    assert env.m._loop_mismatch_errors == 0
    done_p = FakeTask(done=True)
    env.m.processing_task, env.m._scheduler_task = done_p, None
    env.m._reset_event_pipeline(loop)
    assert not done_p.cancelled


class SyncThread:
    started = []

    def __init__(self, target=None, daemon=None, name=None):
        self.target, self.daemon, self.name = target, daemon, name

    def start(self):
        SyncThread.started.append(self)
        self.target()


def _thread_env(env, loops, shutdown_seq, obs_error=False):
    obs_calls = []

    def set_loop(loop):
        if obs_error:
            raise RuntimeError("obs down")
        obs_calls.append(loop)

    fake_module(env.mp, "obs_service", obs_service=SimpleNamespace(set_connector_loop=set_loop))
    seq = list(shutdown_seq)
    fake_module(env.mp, "shutdown", is_shutdown_in_progress=lambda: seq.pop(0) if seq else True,
                register_shutdown_flush=lambda *a: None)
    loop_iter = iter(loops)
    env.mp.setattr(cm.asyncio, "new_event_loop", lambda: next(loop_iter))
    env.mp.setattr(cm.asyncio, "set_event_loop", lambda loop: None)
    sleeps = []
    env.mp.setattr(cm.time, "sleep", lambda s: sleeps.append(s))
    SyncThread.started = []
    env.mp.setattr(threading, "Thread", SyncThread)
    return obs_calls, sleeps


def test_connector_thread_clean_exit(env):
    loop = FakeLoop()
    obs_calls, sleeps = _thread_env(env, [loop], [False, True])
    env.m.start_connector_thread()
    t = SyncThread.started[0]
    assert t.name == "ConnectorProcessor" and t.daemon is True
    assert obs_calls == [loop, None] and loop.closed and sleeps == []
    assert all(task.cancelled for task in loop.tasks)
    assert env.m.connector_loop is None and env.m.event_queue is None and env.m.processing_task is None
    assert not any(n[0] == "critical" for n in env.notes)


def test_connector_thread_restarts_with_backoff(env):
    loops = [FakeLoop(run_error=RuntimeError("crash"), close_error=OSError("close")),
             FakeLoop(run_error=RuntimeError("again")), FakeLoop()]
    # while-check, after-run check per iteration
    _thread_env(env, loops, [False, False, False, False, False, True], obs_error=True)
    env.m.start_connector_thread()
    crit = [n for n in env.notes if n[0] == "critical"]
    assert len(crit) == 2 and crit[0][2]["dedupe_key"] == "connector:thread_restart"
    assert crit[0][2]["actions"] == [("nav", "Connectors")]


def test_connector_thread_backoff_doubles_and_caps(env):
    loops = [FakeLoop(run_error=RuntimeError(str(i))) for i in range(7)]
    seq = []
    for _ in range(7):
        seq += [False, False]
    seq.append(True)
    _, sleeps = _thread_env(env, loops, seq)
    # a crash during run still counts as a healthy start (backoff reset before run_forever)
    env.mp.setattr(env.m, "_reset_event_pipeline", lambda loop: (_ for _ in ()).throw(RuntimeError("no pipe")))
    env.m.start_connector_thread()
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_ensure_processing_task(env):
    m = env.m
    calls = []
    env.mp.setattr(m, "_reset_event_pipeline", lambda loop: calls.append(loop))
    m.ensure_processing_task()  # not running
    m.is_running = True
    m.ensure_processing_task()  # no loop
    loop = FakeLoop()
    loop.is_running = lambda: False
    m.connector_loop = loop
    m.ensure_processing_task()
    loop.is_running = lambda: True
    m.processing_task, m.event_queue = FakeTask(), object()
    m.ensure_processing_task()  # healthy
    assert calls == []
    m.processing_task = FakeTask(done=True)
    m.ensure_processing_task()
    assert calls == [loop]

    def boom(loop):
        raise RuntimeError("x")

    env.mp.setattr(m, "_reset_event_pipeline", boom)
    m.ensure_processing_task()


def test_schedule_event_on_connector_loop(env):
    m = env.m
    assert not m._schedule_event_on_connector_loop({})
    scheduled = []
    loop = SimpleNamespace(is_running=lambda: True, call_soon_threadsafe=lambda fn: scheduled.append(fn))
    m.connector_loop = loop
    assert not m._schedule_event_on_connector_loop({})  # no queue
    queue = asyncio.Queue()
    m.event_queue = queue
    assert m._schedule_event_on_connector_loop({"e": 1})
    scheduled[0]()
    assert queue.get_nowait() == {"e": 1}
    m.event_queue = None
    scheduled[0]()  # queue gone: no-op


def test_process_event_and_add_event_on_and_off_loop(env):
    m = env.m
    assert run(m.process_event({})) is False
    run(m.add_event({"event_type": "x"}))  # not running: dropped
    m.is_running = True
    env.mp.setattr(m, "ensure_processing_task", lambda: None)

    async def on_loop():
        m.connector_loop = asyncio.get_running_loop()
        m.event_queue = None
        assert await m.process_event({}) is False
        await m.add_event({})
        m.event_queue = asyncio.Queue()
        assert await m.process_event({"n": 1}) is True
        await m.add_event({"n": 2})
        return [m.event_queue.get_nowait(), m.event_queue.get_nowait()]

    assert run(on_loop()) == [{"n": 1}, {"n": 2}]
    m.connector_loop = None
    sent = []
    env.mp.setattr(m, "_schedule_event_on_connector_loop", lambda e: sent.append(e) or bool(e))
    assert run(m.process_event({"off": 1})) is True
    run(m.add_event({"event_type": "y"}))
    run(m.add_event({}))
    assert sent == [{"off": 1}, {"event_type": "y"}, {}]


def test_stop_cancels_tasks_and_audio(env):
    m = env.m
    run(m.stop())  # not running: no-op
    from modules.connector_actions import AudioControlAction

    calls = []
    env.mp.setattr(AudioControlAction, "cancel_all_restoration_tasks", classmethod(lambda c: calls.append("cancel")))
    env.mp.setattr(AudioControlAction, "cleanup_audio_change_registry", classmethod(lambda c: calls.append("clean")))

    async def go():
        async def forever():
            await asyncio.Event().wait()

        m.is_running = True
        m.processing_task = asyncio.create_task(forever())
        m._scheduler_task = asyncio.create_task(forever())
        m.event_queue = asyncio.Queue()
        await asyncio.sleep(0)
        await m.stop()

    run(go())
    assert not m.is_running and m.processing_task is None and m._scheduler_task is None
    assert m.event_queue is None and calls == ["cancel", "clean"]

    def boom(c):
        raise RuntimeError("x")

    env.mp.setattr(AudioControlAction, "cancel_all_restoration_tasks", classmethod(boom))
    m.is_running = True
    run(m.stop())


# --------------------------------------------------------------------------- _process_events


def test_process_events_queue_missing(env):
    env.m.is_running = True
    env.m.event_queue = None
    run(env.m._process_events())


def test_process_events_script(env):
    m = env.m
    m.is_running = True
    m.event_queue = asyncio.Queue()
    processed, rebuilt = [], []

    async def single(ev):
        processed.append(ev)

    env.mp.setattr(m, "_process_single_event", single)
    state = {"rebuild_fail": False}

    def rebuild(loop):
        rebuilt.append(loop)
        if state["rebuild_fail"]:
            raise RuntimeError("rebuild")

    env.mp.setattr(m, "_reset_event_pipeline", rebuild)
    mismatch = RuntimeError("Queue is bound to a different event loop")
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)

    script = [
        asyncio.TimeoutError(),
        {"event_type": "a"},
        mismatch,
        RuntimeError("other runtime"),
        ValueError("bad"),
        "set49",
        mismatch,
        "fail_rebuild",
        mismatch,
        asyncio.CancelledError(),
    ]
    clock = {"t": 0.0}
    env.mp.setattr(cm.time, "monotonic", lambda: clock["t"])

    async def fake_wait_for(aw, timeout):
        aw.close()
        while True:
            item = script.pop(0)
            if item == "set49":
                m._loop_mismatch_errors = 48
                continue
            if item == "fail_rebuild":
                state["rebuild_fail"] = True
                clock["t"] = 100.0
                continue
            break
        if isinstance(item, BaseException):
            raise item
        return item

    async def go():
        env.mp.setattr(cm.asyncio, "wait_for", fake_wait_for)
        env.mp.setattr(cm.asyncio, "sleep", fake_sleep)
        await m._process_events()

    run(go())
    assert processed == [{"event_type": "a"}]
    assert m.stats["events_processed"] == 1 and m.stats["errors"] == 2
    # rebuilt on the first mismatch and once the 30 s window passed; slowed down from 50 on
    assert len(rebuilt) == 2 and sleeps == [1.0]


def test_process_single_event_routes_and_counts(env):
    m = env.m
    hits = []

    @dataclass
    class Probe(BaseAction):
        async def execute(self, trigger_data, event_data):
            hits.append(trigger_data["connector_id"])
            return True

    def probe(i):
        return Probe(action_id=i, action_type=ActionType.API_CALL, name=i)

    m.connectors = {
        "bits": make_connector("bits", actions=[probe("p1")]),
        "off": make_connector("off", enabled=False, actions=[probe("p2")]),
        "sub": make_connector("sub", TriggerType.TWITCH_SUB, actions=[probe("p3")]),
        "any": make_connector("any", TriggerType.ANY, actions=[probe("p4")]),
    }
    run(m._process_single_event({"event_type": "twitch_bits", "username": "Bob"}))
    assert hits == ["bits", "any"] and m.stats["triggers_fired"] == 2
    assert env.stats.calls == [(1, {"username": "Bob", "connector_name": "Conn bits"}),
                               (1, {"username": "Bob", "connector_name": "Conn any"})]
    hits.clear()
    run(m._process_single_event({"event_type": "twitch_bits", "target_connector_id": "any"}))
    assert hits == ["any"]


def test_process_single_event_errors(env):
    m = env.m

    class Boom:
        enabled = True
        name = "boom"
        trigger = SimpleNamespace()

        async def process_event(self, ev):
            raise RuntimeError("x")

    m.connectors = {"b": Boom()}
    run(m._process_single_event({"event_type": "x"}))
    assert m.stats["errors"] == 1

    class Ok(Boom):
        async def process_event(self, ev):
            return True

    m.connectors = {"o": Ok()}

    def broken():
        raise RuntimeError("stats")

    env.mp.setattr(cm, "get_statistics_manager", broken)
    run(m._process_single_event({"event_type": "x"}))
    assert m.stats["triggers_fired"] == 1


# --------------------------------------------------------------------------- load / save / recover


def stored(manager, *connectors):
    return {c.connector_id: manager._serialize_connector(c) for c in connectors}


def test_load_connectors_registers_hotkeys_and_reports_skipped(env):
    m = env.m
    data = stored(m, hotkey_connector("h1"), hotkey_connector("h2", enabled=False), make_connector("c1"))
    data["bad1"] = "not a dict"
    for i in range(4):
        data[f"bad{i + 2}"] = {"name": f"Broken {i}"}
    env.db.stored = data
    real = m._deserialize_connector

    def deserialize(d):
        if not isinstance(d, dict) or "connector_id" not in d:
            raise KeyError("connector_id")
        return real(d)

    env.mp.setattr(m, "_deserialize_connector", deserialize)
    m.load_connectors()
    assert set(m.connectors) == {"h1", "h2", "c1"}
    assert env.hotkeys.calls == [("register", "h1", "ctrl+f1", True)]
    note = [n for n in env.notes if n[0] == "notify"][0]
    assert "and 2 more" in note[1] and "“bad1”" in note[1] and note[2]["dedupe_key"] == "connector:load_skipped"
    assert "bad5: KeyError" in note[2]["details"]


def test_load_connectors_none_result_and_failed_read(env):
    m = env.m
    env.db.stored = {"x": {"connector_id": "x"}}
    env.mp.setattr(m, "_deserialize_connector", lambda d: None)
    m.load_connectors()
    assert m.connectors == {} and not m._load_failed
    env.db.read_ok = False
    m.load_connectors()
    assert m._load_failed


def test_load_connectors_unexpected_error_is_critical(env):
    def boom(key):
        raise RuntimeError("db exploded")

    env.mp.setattr(cm.database_manager, "get_data_checked", boom)
    env.m.load_connectors()
    crit = [n for n in env.notes if n[0] == "critical"]
    assert crit and crit[0][2]["dedupe_key"] == "connector:load_failed"


def test_recover_after_failed_load_merges(env):
    m = env.m
    other = FakeDB()
    disk_a = make_connector("a")
    disk_hk = hotkey_connector("hk")
    disk_off = hotkey_connector("hk_off", enabled=False)
    env.db.stored = stored(m, disk_a, disk_hk, disk_off)
    env.db.stored["broken"] = {"connector_id": "broken"}
    env.db.stored["none"] = {"connector_id": "none", "name": "n"}
    local_a = make_connector("a")
    local_a.name = "local edit"
    m.connectors = {"a": local_a, "new": make_connector("new")}
    m._load_failed = True
    real = m._deserialize_connector

    def deserialize(d):
        if d["connector_id"] == "broken":
            raise ValueError("bad")
        if d["connector_id"] == "none":
            return None
        return real(d)

    env.mp.setattr(m, "_deserialize_connector", deserialize)
    assert m._recover_after_failed_load() is True
    assert m.connectors["a"] is local_a and set(m.connectors) == {"a", "hk", "hk_off", "new"}
    assert env.hotkeys.calls == [("register", "hk", "ctrl+f1", True)]
    assert not m._load_failed
    # Second call: already recovered
    assert m._recover_after_failed_load() is True
    env.db.read_ok = False
    assert m._recover_after_failed_load() is False


def test_save_blocked_until_readable_then_flushed(env):
    m = env.m
    m._load_failed = True
    env.db.read_ok = False
    m.connectors = {"c1": make_connector("c1")}
    assert m.save_connectors() is False and m._save_blocked and not m.last_save_ok
    assert env.db.writes == []
    assert [n for n in env.notes if n[0] == "notify"][0][2]["dedupe_key"] == "connector:save_blocked"
    env.db.read_ok = True
    m.flush_blocked_save()
    assert not m._save_blocked and m.last_save_ok and "c1" in env.db.stored
    env.db.writes.clear()
    m.flush_blocked_save()  # nothing blocked
    assert env.db.writes == []


def test_save_write_false_and_problem_reason(env):
    m = env.m
    m.connectors = {"c1": make_connector("c1")}
    env.db.write_ok = False
    env.db.problem = ("disk full", None, "path")
    assert m.save_connectors() is False
    crit = [n for n in env.notes if n[0] == "critical"][-1]
    assert crit[2]["details"] == "Database write returned no success for 'connectors' (disk full: path)"
    env.db.problem = None
    assert m.save_connectors() is False
    assert [n for n in env.notes if n[0] == "critical"][-1][2]["details"].endswith("'connectors'")


def test_save_serialize_error_skips_connector_and_exception(env):
    m = env.m
    m.connectors = {"good": make_connector("good"), "bad": SimpleNamespace()}
    assert m.save_connectors() is True and set(env.db.stored) == {"good"}
    env.db.raise_on_write = OSError("io")
    assert m.save_connectors() is False
    assert "OSError: io" in [n for n in env.notes if n[0] == "critical"][-1][2]["details"]


# --------------------------------------------------------------------------- CRUD


def test_add_update_remove_connector(env):
    m = env.m
    hk = hotkey_connector("h1")
    assert m.add_connector(hk)
    assert not m.add_connector(hk)  # duplicate
    assert env.hotkeys.calls == [("register", "h1", "ctrl+f1", True)]
    assert "h1" in env.db.stored
    assert not m.add_connector(make_connector("x", actions=[]))  # invalid
    assert not m.add_connector(make_connector("off", enabled=False, actions=[]))
    assert m.add_connector(make_connector("off2", enabled=False))
    assert m.get_connector("h1") is hk and set(m.get_all_connectors()) == {"h1", "off2"}
    assert m.get_connectors_by_trigger_type(TriggerType.HOTKEY) == [hk]

    replacement = hotkey_connector("h1", combo="ctrl+f2")
    assert m.update_connector("h1", replacement)
    assert env.hotkeys.calls[-2:] == [("unregister", "h1"), ("register", "h1", "ctrl+f2", True)]
    assert not m.update_connector("missing", replacement)
    assert not m.update_connector("h1", make_connector("h1", actions=[]))
    disabled = hotkey_connector("h1", enabled=False)
    assert m.update_connector("h1", disabled)
    assert env.hotkeys.calls[-1] == ("unregister", "h1")

    assert m.remove_connector("h1") and "h1" not in env.db.stored
    assert not m.remove_connector("h1")


def test_update_connector_keep_stats(env):
    m = env.m
    old = make_connector("c1")
    old.created_at, old.last_triggered, old.trigger_count = 1.0, 2.0, 7
    old.trigger.last_triggered = 3.0
    m.connectors = {"c1": old}
    new = make_connector("c1")
    assert m.update_connector("c1", new, keep_stats=True)
    assert (new.created_at, new.last_triggered, new.trigger_count, new.trigger.last_triggered) == (1.0, 2.0, 7, 3.0)
    other_type = make_connector("c1", TriggerType.TWITCH_SUB)
    assert m.update_connector("c1", other_type, keep_stats=True)
    assert other_type.trigger.last_triggered == 0 and other_type.trigger_count == 7
    no_old_trigger = make_connector("c1")
    m.connectors["c1"].trigger = None
    assert m.update_connector("c1", no_old_trigger, keep_stats=True)


def test_crud_exceptions(env):
    m = env.m

    class BadLock:
        def __enter__(self):
            raise RuntimeError("lock")

        def __exit__(self, *a):
            return False

    m.connectors = {"c1": make_connector("c1")}
    m._lock = BadLock()
    assert not m.add_connector(make_connector("c2"))
    assert not m.update_connector("c1", make_connector("c1"))
    assert not m.remove_connector("c1")
    assert not m.toggle_connector("c1")
    assert not m.set_connector_enabled("c1", False)
    assert m.set_connectors_enabled(["c1"], False) == 0


def test_toggle_and_set_enabled(env):
    m = env.m
    hk = hotkey_connector("h1")
    m.connectors = {"h1": hk, "c1": make_connector("c1"), "c2": make_connector("c2")}
    assert not m.toggle_connector("missing")
    assert m.toggle_connector("h1") and not hk.enabled
    assert env.hotkeys.calls[-1] == ("unregister", "h1")
    assert m.toggle_connector("h1") and hk.enabled
    assert env.hotkeys.calls[-1] == ("register", "h1", "ctrl+f1", True)
    assert not m.set_connector_enabled("missing", True)
    writes = len(env.db.writes)
    assert m.set_connector_enabled("c1", True) and len(env.db.writes) == writes  # unchanged
    assert m.set_connector_enabled("c1", False) and not m.connectors["c1"].enabled
    assert m.set_connectors_enabled(["c1", "c2", "missing", "h1"], False) == 2
    assert m.set_connectors_enabled(["c1"], False) == 0


def test_get_statistics(env, monkeypatch):
    m = env.m
    m.connectors = {"a": make_connector("a"), "b": make_connector("b", enabled=False)}
    m.stats["started_at"] = 10.0
    monkeypatch.setattr(cm.time, "time", lambda: 25.0)
    s = m.get_statistics()
    assert s["uptime_seconds"] == 15.0 and s["connector_count"] == 2 and s["enabled_connectors"] == 1


def test_validate_connector(env):
    v = env.m._validate_connector
    assert not v(Connector(connector_id="c", name="n"))
    assert not v(make_connector(actions=[]))
    assert not v(Connector(connector_id="c", name="n", trigger=object(), actions=[chat_action()]))
    assert not v(make_connector(actions=[object()]))
    assert not v(make_connector(actions=[chat_action(message="  ")]))
    assert v(make_connector())


# --------------------------------------------------------------------------- serialization


def test_round_trip_keeps_fields(env):
    m = env.m
    cond = TriggerCondition(field="amount", operator=Op.GREATER_THAN, value=5, case_sensitive=False, join="OR")
    conn = make_connector("c1", conditions=[cond], cooldown_seconds=3, metadata={"k": 1})
    conn.metadata = {"m": 2}
    conn.trigger.last_triggered = 9.0
    data = m.connector_to_dict(conn)
    assert data["trigger"]["conditions"][0] == {"field": "amount", "operator": "greater_than", "value": 5,
                                                "case_sensitive": False, "join": "or"}
    back = m.connector_from_dict(data)
    assert back.trigger.last_triggered == 9.0 and back.trigger.cooldown_seconds == 3
    assert back.trigger.conditions[0].join == "or" and back.metadata == {"m": 2}
    assert back.actions[0].message == "hi"
    no_trigger = Connector(connector_id="x", name="x")
    assert m._serialize_connector(no_trigger)["trigger"] is None
    assert m._deserialize_connector(m._serialize_connector(no_trigger)).trigger is None


def test_deserialize_migrates_tier_and_defaults(env):
    m = env.m
    trig = {"trigger_id": "t", "trigger_type": "twitch_sub", "name": "T",
            "conditions": [{"field": "tier", "operator": "equal", "value": "2000"}]}
    t = m._deserialize_trigger(trig)
    assert t.conditions[0].value == "2" and t.conditions[0].case_sensitive is True
    assert t.conditions[0].join == "and" and t.last_triggered == 0
    assert m._deserialize_trigger({"trigger_type": "nope"}) is None
    assert m._deserialize_action({"action_type": "nope"}) is None
    assert m._deserialize_connector({"name": "missing id"}) is None
    conn = m._deserialize_connector({"connector_id": "c", "name": "n", "actions": [{"action_type": "nope"}]})
    assert conn.actions == [] and conn.enabled is True and conn.trigger_count == 0


# --------------------------------------------------------------------------- hotkey helpers


def test_register_hotkey_variants(env):
    m = env.m
    m._register_connector_hotkey(make_connector())  # not a hotkey
    m._register_connector_hotkey(Connector(connector_id="n", name="n"))
    blank = hotkey_connector("b", combo="")
    m._register_connector_hotkey(blank)
    assert env.hotkeys.calls == []
    legacy = SimpleNamespace(connector_id="l", trigger=SimpleNamespace(
        trigger_type=TriggerType.HOTKEY, key_combination="f9", is_global=False))
    m._register_connector_hotkey(legacy)
    assert env.hotkeys.calls == [("register", "l", "f9", False)]

    def boom(*a):
        raise RuntimeError("x")

    env.hotkeys.register_hotkey = boom
    env.hotkeys.unregister_hotkey = boom
    m._register_connector_hotkey(hotkey_connector())
    m._unregister_connector_hotkey(hotkey_connector())
    m._unregister_connector_hotkey(make_connector())
    m._sync_hotkey_for_connector_enabled(make_connector())


# --------------------------------------------------------------------------- test_connector


def test_test_connector_errors(env):
    m = env.m
    assert run(m.test_connector("missing")) == {"success": False, "error": "Connector not found"}
    m.connectors["nt"] = Connector(connector_id="nt", name="n")
    assert run(m.test_connector("nt"))["error"] == "Connector has no trigger"
    m.connectors["c1"] = make_connector("c1")
    assert run(m.test_connector("c1", {}))["error"] == "Connector loop not running"

    class Broken:
        def is_running(self):
            raise RuntimeError("loop gone")

    m.connector_loop = Broken()
    assert run(m.test_connector("c1", {})) == {"success": False, "error": "loop gone"}


def test_test_connector_outside_any_loop(env):
    m = env.m
    m.connectors["c1"] = make_connector("c1")
    coro = m.test_connector("c1", {})
    with pytest.raises(StopIteration) as stop:
        coro.send(None)
    assert stop.value.value == {"success": False, "error": "Connector loop not running"}


def test_test_connector_on_loop_uses_sample(env):
    m = env.m
    seen = []

    @dataclass
    class Probe(BaseAction):
        async def execute(self, trigger_data, event_data):
            seen.append(event_data)
            return False

    conn = make_connector("c1", actions=[Probe(action_id="p", action_type=ActionType.API_CALL, name="Probe")])
    m.connectors["c1"] = conn

    async def go():
        m.connector_loop = asyncio.get_running_loop()
        return await m.test_connector("c1")

    result = run(go())
    assert result["success"] and result["triggered"] is False and result["trigger_type"] == "twitch_bits"
    assert result["failures"][0]["label"] == "Probe" and result["action_count"] == 1
    assert seen[0]["event_type"] == "twitch_bits"


def test_test_connector_cross_thread(env):
    m = env.m
    m.connectors["c1"] = make_connector("c1", actions=[])
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        m.connector_loop = loop
        result = run(m.test_connector("c1", {"event_type": "twitch_bits"}))
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
    assert result["success"] and result["triggered"] is False and result["connector_name"] == "Conn c1"


def test_test_connector_without_trigger_reports_none(env):
    m = env.m
    conn = make_connector("c1", actions=[])

    async def go():
        m.connector_loop = asyncio.get_running_loop()
        conn.trigger = None
        return await m.test_connector("c1", {"event_type": "x"})

    m.connectors["c1"] = conn
    assert run(go())["trigger_type"] is None


# --------------------------------------------------------------------------- scheduler


def test_scheduler_due_events(env):
    f = cm.ConnectorManager._scheduler_due_events
    timer = make_connector("tm", TriggerType.TIMER, interval_seconds=10)
    sched = make_connector("sc", TriggerType.SCHEDULE, specific_time="10:30", days_of_week=[0])
    off = make_connector("off", TriggerType.TIMER, enabled=False)
    no_trig = Connector(connector_id="nt", name="n")
    bits = make_connector("bits")
    broken_timer = make_connector("bt", TriggerType.TIMER)
    broken_timer.trigger.interval_seconds = "x"
    conns = [("tm", timer), ("sc", sched), ("off", off), ("nt", no_trig), ("bits", bits), ("bt", broken_timer)]
    last, timers = {}, {"stale": (5, 0)}
    now = datetime(2026, 10, 26, 10, 30)
    events = f(conns, now, 100.0, last, timers)
    assert [e["event_type"] for e in events] == ["schedule"]
    assert events[0]["target_connector_id"] == "sc" and events[0]["days_of_week"] == [0]
    assert timers == {"tm": (10, 110.0), "bt": (60, 160.0)}
    assert f(conns, now, 105.0, last, timers) == []  # schedule already fired this minute
    events = f(conns, datetime(2026, 10, 26, 10, 31), 110.0, last, timers)
    assert [e["target_connector_id"] for e in events] == ["tm"] and events[0]["interval_seconds"] == 10
    timer.trigger.interval_seconds = 20
    assert f(conns, now, 111.0, last, timers) == [] and timers["tm"] == (20, 131.0)


def test_connector_scheduler_loop(env):
    m = env.m
    added = []
    ticks = {"n": 0}

    async def fake_sleep(s):
        ticks["n"] += 1
        if ticks["n"] == 1:
            return  # not running: skipped
        if ticks["n"] == 2:
            m.is_running = True
            return
        if ticks["n"] == 3:
            env.mp.setattr(cm.ConnectorManager, "_scheduler_due_events",
                           staticmethod(lambda *a: (_ for _ in ()).throw(RuntimeError("tick"))))
            return
        raise asyncio.CancelledError

    async def add(ev):
        added.append(ev)

    env.mp.setattr(cm.ConnectorManager, "_scheduler_due_events", staticmethod(lambda *a: [{"e": 1}]))
    env.mp.setattr(m, "add_event", add)

    async def go():
        env.mp.setattr(cm.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await m._connector_scheduler_loop()

    run(go())
    assert added == [{"e": 1}]


def test_scheduler_loop_reraises_cancel_inside_tick(env):
    m = env.m
    m.is_running = True

    async def fake_sleep(s):
        return None

    async def add(ev):
        raise asyncio.CancelledError

    env.mp.setattr(cm.ConnectorManager, "_scheduler_due_events", staticmethod(lambda *a: [{"e": 1}]))
    env.mp.setattr(m, "add_event", add)

    async def go():
        env.mp.setattr(cm.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await m._connector_scheduler_loop()

    run(go())


# --------------------------------------------------------------------------- module functions


def test_initialize_and_cleanup(monkeypatch):
    calls = []
    fake_mgr = SimpleNamespace(start=lambda: calls.append("start"), stop=None)

    async def stop():
        calls.append("stop")

    fake_mgr.stop = stop
    import modules.hotkey_listener as hl

    monkeypatch.setattr(cm, "connector_manager", fake_mgr)
    monkeypatch.setattr(hl, "initialize", lambda: calls.append("hk_init"))
    monkeypatch.setattr(hl, "cleanup", lambda: calls.append("hk_cleanup"))
    cm.initialize()
    run(cm.cleanup())
    assert calls == ["hk_init", "start", "stop", "hk_cleanup"]
    assert cm.get_manager() is fake_mgr
    fake_mgr.connector_loop = "LOOP"
    assert cm.get_connector_loop() == "LOOP"


def test_initialize_and_cleanup_errors(monkeypatch):
    notes = []
    monkeypatch.setattr(cm, "notify_critical", lambda msg, **kw: notes.append(kw))
    monkeypatch.setattr(cm, "nav_actions_main_tab", lambda t: [])
    import modules.hotkey_listener as hl

    def boom():
        raise RuntimeError("no hook")

    monkeypatch.setattr(hl, "initialize", boom)
    cm.initialize()
    assert notes[0]["dedupe_key"] == "connector:init_failed"

    async def bad_stop():
        raise RuntimeError("x")

    monkeypatch.setattr(cm, "connector_manager", SimpleNamespace(stop=bad_stop))
    run(cm.cleanup())


def test_register_connector_flush(monkeypatch):
    seen = []
    fake_module(monkeypatch, "shutdown", register_shutdown_flush=lambda name, fn: seen.append((name, fn)))
    cm._register_connector_flush()
    assert seen[0][0] == "connectors"
    monkeypatch.setitem(sys.modules, "modules.shutdown", None)
    cm._register_connector_flush()  # ImportError: no-op

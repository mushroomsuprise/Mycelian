# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/web_engine.py (part 4): cross-thread emit queue and wake pipe,
the template-control emit worker, persisted control changes, overlay HTML cache,
source-control relays, template output relay, broadcast helpers, Stream Deck
Socket.IO handlers, run/start/stop and the source URL listing.

No real server is started: ``socketio.run``, threads and sleeps are faked, and
templates live under tmp_path.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import web_engine  # noqa: E402
from modules import template_config_parser as tcp_mod  # noqa: E402
from modules.spore_studio import assets_watcher  # noqa: E402
from modules.spore_studio import containers as containers_mod  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402

WE = web_engine.WebEngine
ORIG_ENSURE_EMIT_WORKER = WE.ensure_template_control_emit_worker


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    adir = tmp_path / "assets"
    adir.mkdir()
    monkeypatch.setattr(web_engine, "get_template_path", lambda rel="": str(tdir / rel) if rel else str(tdir))
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(adir / rel) if rel else str(adir))
    return SimpleNamespace(templates=tdir, assets=adir, configs=tdir / "template_configs")


@pytest.fixture
def engine(dirs, monkeypatch):
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "web_engine_instance", "web_engine_running",
                 "EXPECTED_ALERT_COMPLETE_SEQ"):
        monkeypatch.setattr(web_engine, name, getattr(web_engine, name))
    for meth in ("ensure_web_engine_heartbeat", "ensure_template_control_emit_worker", "_refresh_cached_listener"):
        monkeypatch.setattr(WE, meth, lambda self: None)
    for fn in ("_notify_alert_processor", "_sync_pause_button_state", "_sync_mute_button_state"):
        monkeypatch.setattr(web_engine, fn, lambda: None)
    monkeypatch.setattr(web_engine, "broadcast_alert_queue_state", lambda force=False: None)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", lambda sio: None)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: [])
    monkeypatch.setattr(tcp_mod, "get_shared_parser", lambda: TemplateConfigParser(config_dir=str(dirs.configs)))
    # Source Controls UI hook (NiceGUI); record instead.
    import modules.uiwindows as uiw

    dirty = []
    monkeypatch.setattr(uiw, "sourcecontrols", SimpleNamespace(mark_source_controls_dirty=dirty.append),
                        raising=False)
    (dirs.templates / "alpha.html").write_text("<html><body>{{ x }}</body></html>", encoding="utf-8")
    eng = WE(template_dir=str(dirs.templates), port=5993)
    eng.template_config_parser = TemplateConfigParser(config_dir=str(dirs.configs))
    eng.dirty = dirty
    eng.bg_tasks = []
    monkeypatch.setattr(eng.socketio, "start_background_task", lambda fn, *a, **k: eng.bg_tasks.append(fn))
    yield eng
    eng._close_emit_wake_pipe()


@pytest.fixture
def sio(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert c.is_connected()
    c.get_received()
    yield c
    if c.is_connected():
        c.disconnect()


@pytest.fixture
def sio2(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    c.get_received()
    yield c
    if c.is_connected():
        c.disconnect()


def received(client, name):
    return [e["args"][0] if e["args"] else None for e in client.get_received() if e["name"] == name]


def events(client):
    return [(e["name"], e["args"][0] if e["args"] else None) for e in client.get_received()]


def call(client, event, *args):
    return client.emit(event, *args, callback=True)


def _boom(*a, **k):
    raise RuntimeError("boom")


@pytest.fixture
def safe(engine, monkeypatch):
    out = []
    monkeypatch.setattr(engine, "safe_emit", lambda event, data=None, to=None: out.append((event, data, to)) or True)
    return out


def _write_config(dirs, name, config):
    dirs.configs.mkdir(exist_ok=True)
    (dirs.configs / f"{name}.json").write_text(json.dumps(config), encoding="utf-8")


# ------------------------------------------------------------------ safe_emit


def test_safe_emit_rejects_empty_event(engine):
    assert engine.safe_emit("") is False
    assert engine._safe_emit_queue.qsize() == 0


def test_safe_emit_direct_on_server_thread(engine, sio):
    engine._server_thread_ident = threading.get_ident()
    assert engine.safe_emit("ping") is True
    assert engine.safe_emit("data", {"a": 1}) is True
    assert events(sio) == [("ping", None), ("data", {"a": 1})]
    assert engine._safe_emit_queue.qsize() == 0


def test_safe_emit_direct_failure(engine, monkeypatch):
    engine._server_thread_ident = threading.get_ident()
    monkeypatch.setattr(engine.socketio, "emit", _boom)
    assert engine.safe_emit("x", {}) is False
    assert len(engine._socket_emit_error_times) == 1


def test_safe_emit_queues_from_other_threads(engine, sio):
    engine._server_thread_ident = None
    assert engine.safe_emit("chat", {"m": 1}) is True
    assert engine.safe_emit("next_alert", {"a": 1}) is True  # priority event
    assert engine._safe_emit_priority_queue.qsize() == 1 and engine._safe_emit_queue.qsize() == 1
    # Latest-wins events keep one slot per (event, to).
    engine.safe_emit("game_hook_payload", {"v": 1})
    engine.safe_emit("game_hook_payload", {"v": 2})
    assert engine._safe_emit_queue.qsize() == 2
    assert engine._drain_safe_emit_queue() == 3
    assert events(sio) == [("next_alert", {"a": 1}), ("chat", {"m": 1}), ("game_hook_payload", {"v": 2})]


def test_safe_emit_drops_oldest_without_live_drain(engine, monkeypatch):
    engine._safe_emit_queue.maxsize = 1
    logged = []
    monkeypatch.setattr(engine, "_log_emit_queue_full", lambda ev, q=None: logged.append(ev))
    engine.safe_emit("a", 1)
    engine.safe_emit("b", 2)
    assert logged == ["a"]
    assert engine._safe_emit_queue.get_nowait()[1] == "b"


def test_safe_emit_drain_live(engine, monkeypatch):
    assert engine._safe_emit_drain_live() is False
    engine.is_running = True
    engine._template_control_worker_alive = True
    engine._emit_drain_last_tick = time.monotonic()
    assert engine._safe_emit_drain_live() is True
    engine._emit_drain_last_tick = time.monotonic() - web_engine._SAFE_EMIT_DRAIN_STALL_SEC - 1
    assert engine._safe_emit_drain_live() is False


def test_safe_emit_full_mode(engine, monkeypatch):
    monkeypatch.setattr(engine, "_safe_emit_drain_live", lambda: False)
    assert engine._safe_emit_full_mode() == web_engine._EMIT_FULL_DROP_OLDEST
    monkeypatch.setattr(engine, "_safe_emit_drain_live", lambda: True)
    assert engine._safe_emit_full_mode() == web_engine._EMIT_FULL_WAIT

    async def in_loop():
        return engine._safe_emit_full_mode()

    assert asyncio.run(in_loop()) == web_engine._EMIT_FULL_OVERFLOW


def test_log_emit_queue_full_rate_limited(engine, monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(web_engine.time, "monotonic", lambda: now[0])
    with caplog.at_level("WARNING", logger="modules.web_engine"):
        engine._log_emit_queue_full("a")
        engine._log_emit_queue_full("b", engine._safe_emit_priority_queue)
        engine._log_emit_queue_full("c")
        assert engine._emit_queue_full_suppressed == 2
        now[0] += WE._EMIT_QUEUE_FULL_LOG_INTERVAL_SEC + 1
        engine._log_emit_queue_full("d")
    msgs = [r.getMessage() for r in caplog.records if "safe_emit queue full" in r.getMessage()]
    assert len(msgs) == 2
    assert "dropping oldest a" in msgs[0]
    assert "2 more dropped" in msgs[1]
    assert engine._emit_queue_full_suppressed == 0


# ------------------------------------------------------------------ _drain_safe_emit_queue


def test_drain_stops_for_superseded_generation(engine, sio):
    engine.safe_emit("x", 1)
    engine._run_generation = 5
    assert engine._drain_safe_emit_queue(gen=4) == 0
    assert engine._drain_safe_emit_queue(gen=5) == 1
    assert received(sio, "x") == [1]


def test_drain_handles_lag_none_data_and_errors(engine, sio, monkeypatch):
    engine._safe_emit_queue.put((0, "no_lag", None, None), on_full=lambda: "overflow")
    engine._safe_emit_queue.put((time.monotonic(), "lagged", {"a": 1}, None), on_full=lambda: "overflow")
    engine._emit_delivery_lags_ms.clear()
    assert engine._drain_safe_emit_queue() == 2
    assert events(sio) == [("no_lag", None), ("lagged", {"a": 1})]
    assert len(engine._emit_delivery_lags_ms) == 1
    engine.safe_emit("bad", 1)
    monkeypatch.setattr(engine.socketio, "emit", _boom)
    assert engine._drain_safe_emit_queue() == 0
    assert len(engine._socket_emit_error_times) == 1


# ------------------------------------------------------------------ wake pipe


def test_wake_pipe_roundtrip(engine):
    assert engine._emit_wake_read is not None
    engine._signal_emit_wake()
    engine._drain_emit_wake_pipe()
    with pytest.raises(BlockingIOError):
        engine._emit_wake_read.recv(1)


def test_wake_pipe_unavailable(engine, monkeypatch):
    engine._close_emit_wake_pipe()
    monkeypatch.setattr(web_engine.socket, "socketpair", lambda: (_ for _ in ()).throw(OSError("no fds")))
    engine._open_emit_wake_pipe()
    assert engine._emit_wake_read is None and engine._emit_wake_write is None
    engine._signal_emit_wake()
    engine._drain_emit_wake_pipe()
    slept = []
    monkeypatch.setattr(engine.socketio, "sleep", slept.append)
    assert engine._wait_for_emit_wake(0.5) is False
    assert slept == [0.5]


def test_close_wake_pipe_ignores_close_errors(engine):
    class Sock:
        def close(self):
            raise OSError("bad fd")

    engine._close_emit_wake_pipe()
    engine._emit_wake_read = Sock()
    engine._emit_wake_write = None
    engine._close_emit_wake_pipe()
    assert engine._emit_wake_read is None


def test_signal_and_drain_swallow_socket_errors(engine):
    class Sock:
        def send(self, b):
            raise BlockingIOError()

        def recv(self, n):
            raise OSError("closed")

        def close(self):
            pass

    engine._close_emit_wake_pipe()
    engine._emit_wake_write = Sock()
    engine._emit_wake_read = Sock()
    engine._signal_emit_wake()
    engine._drain_emit_wake_pipe()


def test_wait_for_emit_wake(engine, monkeypatch):
    import gevent.select as gselect

    slept = []
    monkeypatch.setattr(engine.socketio, "sleep", slept.append)
    monkeypatch.setattr(gselect, "select", lambda r, w, x, t: ([], [], []))
    assert engine._wait_for_emit_wake(0.01) is False
    drained = []
    monkeypatch.setattr(engine, "_drain_emit_wake_pipe", lambda: drained.append(1))
    monkeypatch.setattr(gselect, "select", lambda r, w, x, t: (r, [], []))
    assert engine._wait_for_emit_wake(0.01) is True and drained == [1]
    monkeypatch.setattr(gselect, "select", _boom)
    assert engine._wait_for_emit_wake(0.2) is False
    assert engine._wait_for_emit_wake(0.3) is False
    assert slept == [0.2, 0.3] and engine._emit_wake_fallback_logged is True
    import gevent

    monkeypatch.delattr(gevent, "select")
    monkeypatch.setitem(sys.modules, "gevent.select", None)
    assert engine._wait_for_emit_wake(0.4) is False
    assert slept[-1] == 0.4


# ------------------------------------------------------------------ emit worker


def test_ensure_template_control_emit_worker(engine, monkeypatch):
    ORIG_ENSURE_EMIT_WORKER(engine)
    ORIG_ENSURE_EMIT_WORKER(engine)  # already started
    assert engine.bg_tasks == [engine._template_control_emit_loop]
    engine._template_control_emit_worker_started = False
    monkeypatch.setattr(engine.socketio, "start_background_task", _boom)
    ORIG_ENSURE_EMIT_WORKER(engine)
    assert engine._template_control_emit_worker_started is False


def _drive_loop(engine, monkeypatch, iterations, *, sleep=None, wait=None):
    """Run the emit loop for a fixed number of passes."""
    engine.is_running = True
    engine._run_generation = 1
    left = [iterations]

    def current(gen):
        if left[0] <= 0:
            return False
        left[0] -= 1
        return True

    monkeypatch.setattr(engine, "_run_is_current", current)
    monkeypatch.setattr(engine, "_hub_run_generation", lambda: 1)
    monkeypatch.setattr(engine.socketio, "sleep", sleep or (lambda s=0: None))
    monkeypatch.setattr(engine, "_wait_for_emit_wake", wait or (lambda t: False))
    engine._template_control_emit_loop()


def test_emit_loop_skips_when_not_current(engine, monkeypatch):
    engine.is_running = False
    engine._template_control_emit_loop()
    assert engine._template_control_worker_alive is False


def test_emit_loop_drains_and_coalesces(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "_bottleneck_print", lambda msg: None)
    engine.safe_emit("queued", {"q": 1})
    q = engine._template_control_queue
    now = time.monotonic()
    for item in [(now, "tpl_text", {"text": "a"}), (now, "tpl_text", {"text": "ab"}),
                 (now, "tpl_counter_increment", {"action": "increment"}), (0, "tpl_text", {"text": "abc"})]:
        q.put_nowait(item)
        engine._tc_enqueue_times.append(now)
    restarts = []
    monkeypatch.setattr(WE, "ensure_template_control_emit_worker", lambda self: restarts.append(1))
    _drive_loop(engine, monkeypatch, 2)
    got = events(sio)
    assert got[0] == ("queued", {"q": 1})
    assert [e for e in got[1:]] == [("tpl_text", {"text": "ab"}), ("tpl_counter_increment", {"action": "increment"}),
                                    ("tpl_text", {"text": "abc"})]
    assert len(engine._tc_enqueue_times) == 0
    # Loop ended while still "running": the worker restarts itself.
    assert restarts == [1]
    assert engine._template_control_worker_alive is False


def test_emit_loop_broadcast_error_and_idle_wait(engine, monkeypatch):
    monkeypatch.setattr(web_engine, "_bottleneck_print", lambda msg: None)
    engine._template_control_queue.put_nowait((time.monotonic(), "x", {}))
    monkeypatch.setattr(engine, "_broadcast_template_control_event", _boom)
    waits = []
    engine.is_running = False  # no restart at the end
    # Checks: start, pass 1 (busy), pass 2 (idle), stop.
    _drive_loop(engine, monkeypatch, 3, wait=lambda t: waits.append(t) or False)
    assert waits == [1.0]  # second pass was idle


def test_emit_loop_sleep_errors(engine, monkeypatch):
    calls = []

    def bad_wait(t):
        raise RuntimeError("hub")

    def sleep(s=0):
        calls.append(s)
        if s == 0.1:
            raise RuntimeError("still broken")

    _drive_loop(engine, monkeypatch, 3, wait=bad_wait, sleep=sleep)
    assert calls == [0.1]


def test_emit_loop_sleep_error_after_supersede(engine, monkeypatch):
    def bad_wait(t):
        engine._run_generation = 99
        raise RuntimeError("hub")

    engine.is_running = True
    engine._run_generation = 1
    monkeypatch.setattr(engine, "_hub_run_generation", lambda: 1)
    monkeypatch.setattr(engine, "_wait_for_emit_wake", bad_wait)
    engine._template_control_emit_loop()
    # A superseded run's worker leaves the new run's flags alone.
    assert engine._template_control_worker_alive is True


def test_emit_loop_sleep_error_recovers(engine, monkeypatch):
    state = {"n": 0}

    def bad_wait(t):
        state["n"] += 1
        raise RuntimeError("hub")

    # Checks: start, pass 1, recovery check, pass 2, recovery check fails -> exit.
    _drive_loop(engine, monkeypatch, 4, wait=bad_wait)
    assert state["n"] == 2


def test_emit_loop_restart_failure(engine, monkeypatch):
    monkeypatch.setattr(WE, "ensure_template_control_emit_worker", _boom)
    _drive_loop(engine, monkeypatch, 1)
    assert engine._template_control_emit_worker_started is False


# ------------------------------------------------------------------ emit_template_control_event


def test_emit_template_control_event(engine, monkeypatch):
    persisted = []
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: persisted.append(a))
    monkeypatch.setattr(web_engine, "_bottleneck_print", lambda msg: None)
    engine.emit_template_control_event("", "go")
    engine.emit_template_control_event("tpl", "")
    assert persisted == []
    engine.emit_template_control_event("tpl", "go", {"a": 1})
    engine.emit_template_control_event("tpl", "go", "junk")
    engine.emit_template_control_event("tpl", "go")
    items = [engine._template_control_queue.get_nowait() for _ in range(3)]
    assert [(i[1], i[2]) for i in items] == [("tpl_go", {"a": 1}), ("tpl_go", {}), ("tpl_go", {})]
    assert persisted == [("tpl", "go", {"a": 1}), ("tpl", "go", {}), ("tpl", "go", {})]


def test_emit_template_control_event_queue_full(engine, monkeypatch):
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: None)
    monkeypatch.setattr(web_engine, "_bottleneck_print", lambda msg: None)
    engine._template_control_queue = queue.Queue(maxsize=1)
    engine.emit_template_control_event("tpl", "a")
    engine.emit_template_control_event("tpl", "b")
    assert engine._template_control_queue.qsize() == 1
    assert len(engine._tc_drop_times) == 1
    assert len(engine._tc_enqueue_times) == 1


# ------------------------------------------------------------------ persist_template_control_change


PERSIST_CFG = {
    "elements": [{"id": "Count", "value": 1}],
    "dynamic_controls": {"elements": [
        {"type": "number_input", "action": "set_count", "value": 1,
         "persist": {"target_element_id": "Count", "value_type": "int_nonneg"}},
        {"type": "button", "action": "plain"},
        {"type": "counter_control", "action_increment": "inc",
         "persist": {"target_element_id": "Count", "value_key": "n", "sync_dynamic_value": False}},
        {"type": "text_input", "action": "no_target", "persist": {"value_key": "text"}},
        {"type": "text_input", "action": "wrong_target", "persist": {"target_element_id": "Missing"}},
        {"type": "slider", "action": "bad_value", "persist": {"target_element_id": "Count"}},
    ]},
}


@pytest.fixture
def persisted(engine, dirs, monkeypatch, safe):
    _write_config(dirs, "tpl", PERSIST_CFG)
    notices = []
    monkeypatch.setattr(WE, "_notify_control_persist_failed", staticmethod(lambda t, d: notices.append((t, d))))
    return SimpleNamespace(notices=notices, safe=safe, load=lambda: json.loads((dirs.configs / "tpl.json").read_text()))


def test_persist_writes_target_and_dynamic_value(engine, persisted):
    engine.persist_template_control_change("tpl", "set_count", {"value": "-4"})
    cfg = persisted.load()
    assert cfg["elements"][0]["value"] == 0
    assert cfg["dynamic_controls"]["elements"][0]["value"] == 0
    (event, payload, _to), = persisted.safe
    assert event == "source_controls_state_update"
    assert payload["reason"] == "persisted_control_change" and payload["template_names"] == ["tpl"]
    assert engine.dirty and engine.dirty[0]["action"] == "set_count"


def test_persist_without_sync_dynamic_value(engine, persisted):
    engine.persist_template_control_change("tpl", "inc", {"n": 9})
    cfg = persisted.load()
    assert cfg["elements"][0]["value"] == 9
    assert "value" not in cfg["dynamic_controls"]["elements"][2]


@pytest.mark.parametrize("action, data", [
    ("unknown", {"value": 1}),       # no matching control
    ("no_target", {"text": "x"}),    # persist without a target
    ("set_count", {}),               # value key missing
    ("set_count", {"value": None}),  # null value
    ("wrong_target", {"value": 1}),  # target element missing
])
def test_persist_noops(engine, persisted, action, data):
    engine.persist_template_control_change("tpl", action, data)
    assert persisted.load()["elements"][0]["value"] == 1
    assert persisted.safe == [] and persisted.notices == []


def test_persist_guards_and_absence_cache(engine, persisted, monkeypatch):
    engine.persist_template_control_change("", "x", {})
    engine.persist_template_control_change("tpl", "", None)
    engine.persist_template_control_change("tpl", "plain", None)
    assert engine._persist_absence_cache[("tpl", "plain")][1] is False
    loads = []
    real = engine.template_config_parser.load_config
    monkeypatch.setattr(engine.template_config_parser, "load_config", lambda *a, **k: loads.append(a) or real(*a, **k))
    engine.persist_template_control_change("tpl", "plain", {"value": 1})
    assert loads == []  # cached absence: no reload


def test_persist_second_lookup_without_persist(engine, persisted, monkeypatch):
    # The deep copy differs from the cached tree (defensive re-check).
    calls = {"n": 0}
    real = WE._dynamic_control_element_for_action

    def lookup(config, action):
        calls["n"] += 1
        el = real(config, action)
        return el if calls["n"] == 1 else {"action": action}

    monkeypatch.setattr(WE, "_dynamic_control_element_for_action", staticmethod(lookup))
    engine.persist_template_control_change("tpl", "set_count", {"value": 3})
    assert persisted.load()["elements"][0]["value"] == 1


def test_persist_missing_config_file(engine, safe, tmp_path):
    engine.persist_template_control_change("ghost", "go", {"value": 1})
    assert safe == []


def test_persist_save_failure_and_errors(engine, persisted, monkeypatch):
    monkeypatch.setattr(engine.template_config_parser, "save_config", lambda n, c: False)
    engine.persist_template_control_change("tpl", "set_count", {"value": 2})
    assert persisted.notices == [("tpl", "save_config returned False (see the log for the file error)")]
    engine.persist_template_control_change("tpl", "bad_value", {"value": "abc"})  # int("abc") fails
    assert persisted.notices[-1] == ("tpl", "ValueError: could not convert string to float: 'abc'")


def test_notify_control_persist_failed(monkeypatch):
    from modules import notification_engine

    calls = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: calls.append(kw))
    monkeypatch.setattr(notification_engine, "nav_actions_main_tab", lambda tab: [tab])
    WE._notify_control_persist_failed("tpl", "disk full")
    assert calls[0]["details"] == "tpl: disk full" and calls[0]["actions"] == ["Source Controls"]
    assert calls[0]["dedupe_key"] == "source_controls:persist:tpl"
    monkeypatch.setattr(notification_engine, "notify", _boom)
    WE._notify_control_persist_failed("tpl", "x")


@pytest.mark.parametrize("raw, vt, out", [
    ("x", "any", "x"), ([1], "json", [1]), ("-3", "int_nonneg", 0), ("2.7", "uint", 2), ("0", "int_min1", 1),
    ("5", "positive_int", 5), ("-2.5", "int", -2), ("1.5", "float", 1.5), (True, "bool", True),
    (" Yes ", "boolean", True), ("off", "bool", False), (0, "bool", False), (3, "str", "3"), (3, None, "3"),
    (4, "mystery", "4"),
])
def test_coerce_persisted_control_value(raw, vt, out):
    assert WE._coerce_persisted_control_value(raw, vt) == out


def test_dynamic_control_element_for_action():
    cfg = {"dynamic_controls": {"elements": ["junk", {"action": "a"}, {"action_decrement": "d"}, {"action_reset": "r"}]}}
    assert WE._dynamic_control_element_for_action(cfg, "a") == {"action": "a"}
    assert WE._dynamic_control_element_for_action(cfg, "d") == {"action_decrement": "d"}
    assert WE._dynamic_control_element_for_action(cfg, "r") == {"action_reset": "r"}
    assert WE._dynamic_control_element_for_action(cfg, "zz") is None
    assert WE._dynamic_control_element_for_action({}, "a") is None


# ------------------------------------------------------------------ overlay HTML cache


def test_overlay_html_cache(engine, dirs, monkeypatch):
    key = engine._overlay_html_cache_key("alpha")
    assert key[0] > 0 and key[1] == 0.0
    assert engine._overlay_html_cache_key("missing")[:2] == (0.0, 0.0)
    assert engine._cached_overlay_html("alpha") is None
    engine._store_overlay_html("alpha", "<html>")
    assert engine._cached_overlay_html("alpha") == "<html>"
    assert engine._cached_overlay_html("alpha", key) == "<html>"
    assert engine._cached_overlay_html("alpha", ("other",)) is None
    for i in range(30):
        engine._store_overlay_html(f"t{i}", "x", key=(i,))
    assert len(engine._overlay_html_cache) == 24
    engine._overlay_html_cache = None
    assert engine._cached_overlay_html("alpha") is None
    engine._store_overlay_html("alpha", "x")


def test_overlay_html_cache_key_bad_config_name(engine, monkeypatch):
    monkeypatch.setattr(engine.template_config_parser, "get_config_path", lambda n: "/nonexistent/x.json")
    assert engine._overlay_html_cache_key("alpha")[1] == 0.0


# ------------------------------------------------------------------ runtime path index


RUNTIME_CFG = {"elements": [], "dynamic_controls": {"elements": [
    "junk", {"action": "a"}, {"action": "b", "persist": "x"},
    {"action": "c", "persist": {"runtime_database_path": "Tpl/state"}},
    {"action": "d", "persist": {"runtime_database_path": "Other/state"}},
]}}


def test_runtime_path_index_and_lookup(engine, dirs):
    _write_config(dirs, "tpl", RUNTIME_CFG)
    _write_config(dirs, "tpl2", {"elements": [], "dynamic_controls": {"elements": [
        {"action": "x", "persist": {"runtime_database_path": "Tpl/state"}}]}})
    _write_config(dirs, "plain", {"elements": []})
    index = engine._runtime_path_index()
    assert sorted(index["Tpl/state"]) == ["tpl", "tpl2"]
    assert "Other/state" not in index  # only the first persisted path per template
    assert engine._runtime_path_index() is index  # cached per generation
    assert sorted(engine._source_control_templates_for_runtime_path("Tpl/state")) == ["tpl", "tpl2"]
    assert engine._source_control_templates_for_runtime_path("") == []
    assert engine._source_control_templates_for_runtime_path(None) == []


def test_runtime_path_lookup_error(engine, monkeypatch):
    monkeypatch.setattr(engine, "_runtime_path_index", _boom)
    assert engine._source_control_templates_for_runtime_path("x") == []


def test_emit_source_controls_state_update(engine, safe, monkeypatch):
    engine._emit_source_controls_state_update(reason="r")
    (event, payload, _), = safe
    assert payload["template_names"] == [] and payload["data"] == {} and payload["reason"] == "r"
    assert engine.dirty == [payload]
    import modules.uiwindows as uiw

    monkeypatch.setattr(uiw, "sourcecontrols", SimpleNamespace(mark_source_controls_dirty=_boom))
    engine._emit_source_controls_state_update(reason="again")  # UI hook failure is logged


def test_broadcast_runtime_path_update(engine, safe, monkeypatch):
    monkeypatch.setattr(engine, "_source_control_templates_for_runtime_path", lambda p: [])
    engine._broadcast_source_control_runtime_path_update("p", {})
    assert safe == []
    monkeypatch.setattr(engine, "_source_control_templates_for_runtime_path", lambda p: ["tpl"])
    engine._dynamic_controls_cache = ("k", 0, b"")
    engine._broadcast_source_control_runtime_path_update("p", "junk")
    assert engine._dynamic_controls_cache is None
    payload = safe[0][1]
    assert payload["reason"] == "runtime_db_update" and payload["db_path"] == "p" and payload["data"] == {}


# ------------------------------------------------------------------ source_controls_relay


def test_source_controls_relay(engine, sio, sio2, safe):
    engine._registered_dynamic_handlers = {"tpl_go"}
    out = call(sio, "source_controls_relay", {"event_name": "tpl_go", "target_template": "tpl",
                                              "event_data": {"v": 1}})
    assert out == {"success": True, "event_name": "tpl_go", "target_template": "tpl"}
    assert received(sio2, "tpl_go") == [{"v": 1}]
    assert safe[0][1]["action"] == "go" and safe[0][1]["event_name"] == "tpl_go"
    assert call(sio, "source_controls_relay", {"event_name": "tpl_other"}) == {"success": False,
                                                                              "error": "Unknown event_name"}


def test_source_controls_relay_validation(engine, sio, safe, monkeypatch):
    engine.__dict__.pop("_registered_dynamic_handlers", None)
    assert call(sio, "source_controls_relay", "junk") == {"success": False, "error": "Invalid data format"}
    assert call(sio, "source_controls_relay", {}) == {"success": False, "error": "Missing event_name"}
    assert call(sio, "source_controls_relay", {"event_name": "bad/name"}) == {"success": False,
                                                                              "error": "Invalid event_name"}
    assert call(sio, "source_controls_relay", {"event_name": 5}) == {"success": False, "error": "Invalid event_name"}
    out = call(sio, "source_controls_relay", {"event_name": "plain", "target_template": "", "event_data": [1]})
    assert out["success"] is True
    assert safe[-1][1]["action"] is None and safe[-1][1]["template_names"] == [] and safe[-1][1]["data"] == {}
    monkeypatch.setattr(engine, "_emit_source_controls_state_update", _boom)
    assert call(sio, "source_controls_relay", {"event_name": "plain"}) == {"success": False, "error": "boom"}
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    assert call(sio, "source_controls_relay", {"event_name": "plain"}) == {"success": False, "error": "Forbidden"}


def test_relay_registration_errors_are_logged(engine, monkeypatch):
    monkeypatch.setattr(engine.socketio, "on", lambda name: _boom)
    engine.register_standalone_template_relay_handlers()
    engine.register_template_output_relay()


# ------------------------------------------------------------------ template output relay


@pytest.fixture
def outputs(monkeypatch):
    from modules.spore_studio import template_outputs as to

    monkeypatch.setattr(to, "known_output_events", lambda: {"roulette_result": ["roulette"]})
    monkeypatch.setattr(to, "resolve_emitter", lambda ev, payload: "roulette")
    return to


def test_template_output_relay(engine, sio, sio2, outputs):
    sio.emit("roulette_result", {"winner": "A"})
    assert events(sio) == [("template_output", {"template": "roulette", "event": "roulette_result",
                                                "data": {"winner": "A"}})]
    assert [e[0] for e in events(sio2)] == ["template_output", "roulette_result"]
    sio.emit("roulette_result", "B")
    assert received(sio2, "template_output")[0]["data"] == {"value": "B"}
    sio.emit("roulette_result")
    assert received(sio2, "template_output")[0]["data"] == {}


def test_template_output_relay_filters(engine, sio, sio2, outputs, monkeypatch):
    sio.emit("unknown_result", {"a": 1})
    sio.emit("roulette_result", {"big": "x" * 70000})
    assert events(sio2) == []
    monkeypatch.setattr(outputs, "is_relayable_event", lambda ev: False)
    sio.emit("roulette_result", {"a": 1})
    assert events(sio2) == []


def test_template_output_relay_unserialisable_and_errors(engine, sio, sio2, outputs, monkeypatch):
    real_dumps = json.dumps
    monkeypatch.setattr(web_engine.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(TypeError("nope")))
    sio.emit("roulette_result", {"a": 1})
    monkeypatch.setattr(web_engine.json, "dumps", real_dumps)
    monkeypatch.setattr(outputs, "known_output_events", _boom)
    sio.emit("roulette_result", {"a": 1})
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    monkeypatch.setattr(outputs, "known_output_events", lambda: {"roulette_result": ["r"]})
    sio.emit("roulette_result", {"a": 1})
    assert events(sio2) == []


# ------------------------------------------------------------------ route info / resync


def test_get_registered_template_routes(engine, dirs):
    (dirs.templates / "beta.html").write_text("x")
    info = engine.get_registered_template_routes()
    assert info["template_routes"] == ["alpha"] and info["total_routes"] == 1
    assert [r["rule"] for r in info["flask_rules"]] == ["/alpha"]
    assert info["template_files_on_disk"] == ["alpha", "beta"]
    assert info["discrepancies"] == {"registered_but_missing_file": [], "file_exists_but_not_registered": ["beta"]}
    engine.template_dir = str(dirs.templates / "gone")
    assert engine.get_registered_template_routes()["discrepancies"]["registered_but_missing_file"] == ["alpha"]
    engine._registered_template_routes = None
    err = engine.get_registered_template_routes()
    assert err["total_routes"] == 0 and "error" in err


def test_force_route_resync(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_rebuild_url_map", lambda: calls.append(set(engine._registered_template_routes)))
    assert engine.force_route_resync() is True
    assert calls == [set()]
    monkeypatch.setattr(engine, "_rebuild_url_map", _boom)
    assert engine.force_route_resync() is False


# ------------------------------------------------------------------ broadcast helpers


@pytest.mark.parametrize("method, event", [
    ("next_alert", "next_alert"), ("activity_feed_alert", "activity_feed_alert"), ("instant_alert", "instant_alert"),
    ("new_message", "new-message"), ("message_moderation", "message_moderation"), ("state", "state"),
    ("remove_messages", "remove-messages"),
])
def test_broadcast_helpers(engine, safe, monkeypatch, method, event):
    monkeypatch.setattr(web_engine.logger, "isEnabledFor", lambda level: True)
    assert getattr(engine, method)({"x": 1}) is True
    assert safe == [(event, {"x": 1}, None)]


def test_more_broadcast_helpers(engine, safe):
    assert engine.broadcast_overlay_recovery() is True
    assert engine.broadcast_theme_update("--a", "Dark", "dark") is True
    assert engine.clear_chat() is True
    assert safe == [("overlay-recovery", {"service": "twitch", "reason": "reconnected"}, None),
                    ("theme_update", {"css": "--a", "theme_name": "Dark", "theme_type": "dark"}, None),
                    ("clear-chat", None, None)]


def test_broadcast_helpers_report_failure(engine, monkeypatch):
    monkeypatch.setattr(engine, "safe_emit", lambda *a, **k: False)
    assert engine.next_alert({}) is False
    assert engine.broadcast_overlay_recovery("psn", "x") is False
    assert engine.broadcast_theme_update("", "", "") is False
    assert engine.clear_chat() is False


def test_module_broadcast_overlay_recovery(engine, safe, monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    assert web_engine.broadcast_overlay_recovery() is False
    monkeypatch.setattr(web_engine, "web_engine_instance", engine)
    assert web_engine.broadcast_overlay_recovery("youtube", "restart") is True
    assert safe == [("overlay-recovery", {"service": "youtube", "reason": "restart"}, None)]


@pytest.mark.parametrize("paused, first", [(True, "alerts_paused"), (False, "alerts_resumed")])
def test_pause_status_update(engine, safe, monkeypatch, paused, first):
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", paused)
    assert engine.pause_status_update() is True
    assert [e for e, _, _ in safe] == [first, "pause_status_update", "pause_alerts"]


def test_pause_and_mute_status_update_errors(engine, monkeypatch):
    monkeypatch.setattr(engine, "safe_emit", _boom)
    assert engine.pause_status_update() is False
    assert engine.mute_status_update() is False


def test_mute_status_update(engine, safe, monkeypatch):
    synced = []
    monkeypatch.setattr(web_engine, "_sync_mute_button_state", lambda: synced.append(1))
    monkeypatch.setattr(web_engine, "ALERTS_MUTED", True)
    assert engine.mute_status_update() is True
    assert safe == [("mute_status_update", {"muted": True}, None)] and synced == [1]


def test_hype_train_status_update(engine, safe, monkeypatch):
    assert engine.hype_train_status_update(True, {"level": 3}) is True
    assert engine.hype_train_status_update(False) is True
    assert safe[0][1]["active"] is True and safe[0][1]["level"] == 3
    assert safe[1][1]["active"] is False and "level" not in safe[1][1]
    monkeypatch.setattr(engine, "safe_emit", _boom)
    assert engine.hype_train_status_update(True) is False


# ------------------------------------------------------------------ Stream Deck Socket.IO handlers


def test_streamdeck_toggle_alerts(engine, sio, sio2, monkeypatch):
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", False)
    out = call(sio, "streamdeck_toggle_alerts")
    assert out == {"success": True, "paused": True, "message": "Alerts paused"}
    assert [e[0] for e in events(sio2)] == ["alerts_paused", "pause_status_update"]
    sio.get_received()
    sio.emit("streamdeck_toggle_alerts", {"requestId": "r"})
    got = events(sio)
    assert got[0] == ("alerts_resumed", {"paused": False})
    assert ("response", {"success": True, "paused": False, "message": "Alerts resumed", "requestId": "r"}) in got
    assert ("connection_status_update", {"connected": True}) in got


def test_streamdeck_toggle_alerts_error(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "set_alerts_paused", _boom)
    assert call(sio, "streamdeck_toggle_alerts") == {"success": False, "error": "boom",
                                                      "message": "Failed to toggle alerts"}
    sio.emit("streamdeck_toggle_alerts", {"requestId": 1})
    assert received(sio, "response") == [{"success": False, "error": "boom", "message": "Failed to toggle alerts",
                                          "requestId": 1}]


def test_streamdeck_get_pause_status(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    assert call(sio, "streamdeck_get_pause_status") == {"success": True, "paused": True,
                                                         "message": "Alerts are currently paused"}
    sio.emit("streamdeck_get_pause_status", {"requestId": 2})
    assert received(sio, "response")[0]["requestId"] == 2


class BadBool:
    def __bool__(self):
        raise RuntimeError("boom")


@pytest.mark.parametrize("event, error_shape", [
    ("streamdeck_get_pause_status", {"success": False, "error": "boom", "message": "Failed to get pause status"}),
    ("streamdeck_check_connection", {"success": False, "connected": False, "error": "boom",
                                     "message": "Server error occurred"}),
])
def test_streamdeck_status_handler_errors(engine, sio, monkeypatch, event, error_shape):
    # Evaluating the paused flag/timestamp fails: the handler answers with an error.
    if event == "streamdeck_get_pause_status":
        monkeypatch.setattr(web_engine, "ALERTS_PAUSED", BadBool())
    else:
        monkeypatch.setattr(web_engine, "datetime", SimpleNamespace(now=_boom))
    assert call(sio, event) == error_shape
    sio.emit(event, {"requestId": "e"})
    assert received(sio, "response") == [dict(error_shape, requestId="e")]


def test_streamdeck_check_connection(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_running", True)
    out = call(sio, "streamdeck_check_connection")
    assert out["success"] is True and out["server_running"] is True and out["alerts_paused"] is False
    sio.emit("streamdeck_check_connection", {"requestId": 3})
    assert received(sio, "response")[0]["requestId"] == 3


SD_CONFIGS = {
    "sd": {"elements": [], "streamdeck_options": {"description": "SD tpl", "actions": {
        "go": {"name": "Go", "description": "d", "event": "sd_fire", "default_data": {"a": 1}},
        "bare": {},
        "junk": "x",
    }}},
    "dyn": {"elements": [], "dynamic_controls": {"elements": [
        {"type": "counter_control", "action": "inc_increment", "label": "Inc"},
        {"type": "number_input", "action": "num", "value": 5},
        {"type": "number_input", "action": "num_novalue"},
        {"type": "text_input", "action": "txt", "value": "hi"},
        {"type": "button", "action": "btn"},
        {"type": "slider", "action": "sl"},
        {"type": "toggle"},
        "junk",
    ]}},
    "opts_no_actions": {"elements": [], "streamdeck_options": {}},
}


class DictParser:
    """Returns stored configs as-is (dynamic_controls included)."""

    def __init__(self, configs):
        self.configs = configs

    def get_config_files(self):
        return list(self.configs)

    def load_config(self, name, **kw):
        return self.configs[name]


def test_streamdeck_get_template_actions(engine, sio, monkeypatch):
    engine.template_config_parser = DictParser(SD_CONFIGS)
    monkeypatch.setattr(web_engine.logger, "isEnabledFor", lambda level: True)
    out = call(sio, "streamdeck_get_template_actions")
    assert out["success"] is True and out["templates_count"] == 3
    by_key = {a["action_key"]: a for a in out["actions"]}
    assert by_key["go"] == {"template_name": "sd", "action_key": "go", "action_name": "Go", "description": "d",
                            "event": "sd_fire", "default_data": {"a": 1}, "template_description": "SD tpl",
                            "category": "streamdeck_action"}
    assert by_key["bare"]["event"] == "sd_bare" and by_key["bare"]["action_name"] == "bare"
    assert "junk" not in by_key
    assert by_key["dyn_inc_increment"]["default_data"] == {"action": "increment"}
    assert by_key["dyn_inc_increment"]["action_name"] == "Inc"
    assert by_key["dyn_num"]["default_data"] == {"value": 5}
    assert by_key["dyn_num_novalue"]["default_data"] == {}
    assert by_key["dyn_txt"]["default_data"] == {"text": "hi"}
    assert by_key["dyn_btn"]["default_data"] == {} and by_key["dyn_sl"]["category"] == "dynamic_control"
    assert out["count"] == len(out["actions"]) == 8
    sio.emit("streamdeck_get_template_actions", {"requestId": "q"})
    got = events(sio)
    assert [e[0] for e in got] == ["streamdeck_template_actions_response", "response"]
    assert got[1][1]["requestId"] == "q"


def test_streamdeck_get_template_actions_from_disk(engine, sio, dirs):
    for name, cfg in SD_CONFIGS.items():
        _write_config(dirs, name, cfg)
    keys = {a["action_key"] for a in call(sio, "streamdeck_get_template_actions")["actions"]}
    assert {"go", "bare"} <= keys


@pytest.mark.xfail(strict=True, reason="BUG: streamdeck_get_template_actions loads configs without "
                                       "dynamic_controls, so dynamic-control actions are never listed")
def test_streamdeck_get_template_actions_lists_dynamic_controls_from_disk(engine, sio, dirs):
    _write_config(dirs, "dyn", SD_CONFIGS["dyn"])
    keys = {a["action_key"] for a in call(sio, "streamdeck_get_template_actions")["actions"]}
    assert "dyn_btn" in keys


def test_streamdeck_get_template_actions_errors(engine, sio, monkeypatch):
    class Parser:
        def get_config_files(self):
            return ["broken"]

        def load_config(self, *a, **k):
            raise RuntimeError("bad json")

    engine.template_config_parser = Parser()
    assert call(sio, "streamdeck_get_template_actions")["count"] == 0  # per-template error skipped
    engine.template_config_parser = SimpleNamespace(get_config_files=_boom)
    err = call(sio, "streamdeck_get_template_actions")
    assert err == {"success": False, "error": "boom", "actions": [], "count": 0,
                   "message": "Failed to load template actions"}
    sio.emit("streamdeck_get_template_actions", {"requestId": 4})
    assert received(sio, "response") == [dict(err, requestId=4)]


def test_streamdeck_get_template_configs(engine, sio, dirs, monkeypatch):
    for name, cfg in SD_CONFIGS.items():
        _write_config(dirs, name, cfg)
    _write_config(dirs, "plain", {"elements": []})
    out = call(sio, "streamdeck_get_template_configs")
    assert out["success"] is True and sorted(out["data"]) == ["dyn", "opts_no_actions", "sd"]
    sio.emit("streamdeck_get_template_configs", {"requestId": 5})
    assert received(sio, "response")[0]["count"] == 3
    real_load = engine.template_config_parser.load_config
    monkeypatch.setattr(engine.template_config_parser, "load_config",
                        lambda name, **kw: _boom() if name == "dyn" else real_load(name, **kw))
    assert "dyn" not in call(sio, "streamdeck_get_template_configs")["data"]


def test_streamdeck_get_template_configs_error(engine, sio):
    engine.template_config_parser = SimpleNamespace(get_streamdeck_configs=_boom)
    err = call(sio, "streamdeck_get_template_configs")
    assert err == {"success": False, "error": "boom", "data": {}, "count": 0}
    sio.emit("streamdeck_get_template_configs", {"requestId": 6})
    assert received(sio, "response") == [dict(err, requestId=6)]


def test_response_event_is_accepted(engine, sio):
    assert not call(sio, "response", {"ok": True})  # logged only, no reply
    assert events(sio) == []


# ------------------------------------------------------------------ run / start / stop


@pytest.fixture
def runner(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_maybe_enable_gevent_blocking_monitor", lambda: calls.append("monitor"))
    monkeypatch.setattr(engine, "ensure_startup_client_sync", lambda: calls.append("sync"))
    monkeypatch.setattr(engine, "request_restart", lambda reason: calls.append(("restart", reason)))
    monkeypatch.setattr(engine, "_schedule_foreign_port_wait", lambda: calls.append("foreign_wait"))
    monkeypatch.setattr(engine, "_log_port_holder_hint", lambda: calls.append("hint"))
    monkeypatch.setattr(engine, "_probe_health_endpoint", lambda: False)
    state = SimpleNamespace(calls=calls, error=None, during=None)

    def fake_run(app, **kw):
        calls.append(("run", kw["host"], kw["port"], kw["debug"], kw["use_reloader"]))
        assert engine.is_running and web_engine.web_engine_running
        assert engine._server_thread_ident == threading.get_ident()
        if state.during:
            state.during()
        if state.error:
            raise state.error

    monkeypatch.setattr(engine.socketio, "run", fake_run)
    return state


def test_run_clean_exit(engine, runner):
    engine._heartbeat_task_started = True
    engine._template_control_emit_worker_started = True
    engine.run()
    assert runner.calls == ["monitor", "sync", ("run", "0.0.0.0", 5993, False, False)]
    assert engine._heartbeat_task_started is False and engine._template_control_emit_worker_started is False
    assert engine.is_running is False and web_engine.web_engine_running is False
    assert engine._server_thread_ident is None and engine._last_run_error is None
    assert engine._run_thread_generations == {}


def test_run_second_generation_rearms_poller(engine, runner, monkeypatch):
    monkeypatch.setattr(assets_watcher, "_poller_started", True)
    engine.run()
    assert assets_watcher._poller_started is True  # first run leaves it
    engine.run()
    assert assets_watcher._poller_started is False


def test_run_rearm_failure_and_worker_arm_failure(engine, runner, monkeypatch):
    engine.run()
    monkeypatch.setattr(assets_watcher, "_poller_lock", None)  # "with None" fails
    monkeypatch.setattr(engine, "ensure_startup_client_sync", _boom)
    engine.run()
    assert ("run", "0.0.0.0", 5993, False, False) in runner.calls


def test_run_generic_error_requests_restart(engine, runner):
    runner.error = RuntimeError("hub died")
    engine.run()
    assert ("restart", "server exited with error: hub died") in runner.calls
    assert engine._last_run_error == "hub died" and engine.is_running is False


def test_run_generic_error_restart_failure(engine, runner, monkeypatch):
    runner.error = RuntimeError("x")
    monkeypatch.setattr(engine, "request_restart", _boom)
    engine.run()
    assert engine._last_run_error == "x"


@pytest.mark.parametrize("health, expected", [(True, "restart"), (False, "foreign_wait")])
def test_run_port_in_use(engine, runner, monkeypatch, health, expected):
    runner.error = OSError(errno.EADDRINUSE, "Address already in use")
    monkeypatch.setattr(engine, "_probe_health_endpoint", lambda: health)
    engine.run()
    kinds = [c if isinstance(c, str) else c[0] for c in runner.calls]
    assert "hint" in kinds and expected in kinds


@pytest.mark.parametrize("health", [True, False])
def test_run_port_in_use_followup_failures(engine, runner, monkeypatch, health):
    runner.error = OSError(errno.EADDRINUSE, "Address already in use")
    monkeypatch.setattr(engine, "_probe_health_endpoint", lambda: health)
    monkeypatch.setattr(engine, "request_restart", _boom)
    monkeypatch.setattr(engine, "_schedule_foreign_port_wait", _boom)
    engine.run()
    assert engine.is_running is False


def test_run_superseded_by_newer_run(engine, runner):
    def bump():
        engine._run_generation += 1

    runner.during = bump
    runner.error = RuntimeError("late failure")
    engine.run()
    # The newer run owns the state: nothing is reset or restarted.
    assert engine.is_running is True and engine._last_run_error is None
    assert not any(isinstance(c, tuple) and c[0] == "restart" for c in runner.calls)
    engine.is_running = False


class FakeThread:
    instances = []

    def __init__(self, target=None, name=None, daemon=None):
        self.target, self.name, self.daemon = target, name, daemon
        self.started = False
        self.alive = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if self.target:
            self.target()
        self.alive = False


def test_start(engine, monkeypatch):
    FakeThread.instances = []
    monkeypatch.setattr(web_engine.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_engine.time, "sleep", lambda s: None)
    engine.is_running = True
    engine.start()
    (thread,) = FakeThread.instances
    assert thread.started and thread.daemon and thread.target == engine.run
    assert web_engine.web_engine_running is True and web_engine.web_engine_instance is engine
    engine.start()  # already running: no second thread
    assert len(FakeThread.instances) == 1
    assert engine.is_alive() is True
    thread.alive = False
    assert engine.is_alive() is False
    engine.server_thread = None
    assert engine.is_alive() is False
    engine.is_running = False


@pytest.fixture
def stopper(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_stop_twitch_api_worker", lambda: calls.append("twitch"))
    monkeypatch.setattr(engine, "_close_listener", lambda: calls.append("close"))
    monkeypatch.setattr(engine.socketio, "stop", lambda: calls.append("sio_stop"))
    state = SimpleNamespace(calls=calls, port_open=False, alive=False)
    monkeypatch.setattr(engine, "_port_is_open", lambda: state.port_open)
    monkeypatch.setattr(engine, "_thread_alive", lambda: state.alive)
    FakeThread.instances = []
    monkeypatch.setattr(web_engine.threading, "Thread", FakeThread)
    return state


def test_stop_when_already_stopped(engine, stopper):
    engine.stop()
    assert stopper.calls == ["twitch"]


def test_stop_running_server(engine, stopper, monkeypatch):
    engine.is_running = True
    monkeypatch.setattr(web_engine, "web_engine_running", True)
    engine.socketio.wsgi_server = object()
    engine.stop()
    assert stopper.calls == ["twitch", "sio_stop", "close", "close"]
    assert engine.is_running is False and web_engine.web_engine_running is False
    assert FakeThread.instances[0].name == "WebEngineStop"


def test_stop_zombie_without_wsgi_server(engine, stopper):
    stopper.port_open = True
    engine.socketio.wsgi_server = None
    engine.stop()
    assert "sio_stop" not in stopper.calls and stopper.calls.count("close") == 2


@pytest.mark.parametrize("exc", [AttributeError("no server"), RuntimeError("bad")])
def test_stop_socketio_errors(engine, stopper, monkeypatch, exc):
    stopper.alive = True

    def bad_stop():
        raise exc

    monkeypatch.setattr(engine.socketio, "stop", bad_stop)
    engine.stop()
    assert engine.is_running is False


def test_stop_thread_creation_failure(engine, stopper, monkeypatch):
    engine.is_running = True
    monkeypatch.setattr(web_engine.threading, "Thread", _boom)
    engine.stop()
    assert engine.is_running is False and "close" in stopper.calls


def test_stop_without_flask_context_helper(engine, stopper, monkeypatch):
    import flask

    engine.is_running = True
    engine.socketio.wsgi_server = object()
    monkeypatch.delattr(flask, "has_request_context")
    engine.stop()
    assert "sio_stop" in stopper.calls


# ------------------------------------------------------------------ control counts / source URLs


def test_get_template_control_count(engine, dirs, monkeypatch):
    _write_config(dirs, "dyn", {"elements": [], "dynamic_controls": {"elements": [{}, {}]}})
    _write_config(dirs, "dynbad", {"elements": [], "dynamic_controls": {"elements": "x"}})
    _write_config(dirs, "els", {"elements": [
        {"type": "button", "action": "a"}, {"type": "slider"}, {"type": "counter_control", "action_reset": "r"},
        {"type": "label", "action": "x"}, "junk"]})
    _write_config(dirs, "elsbad", {"elements": "x"})
    assert engine._get_template_control_count("dyn") == 2
    assert engine._get_template_control_count("dynbad") == 0
    assert engine._get_template_control_count("els") == 2
    assert engine._get_template_control_count("elsbad") == 0
    monkeypatch.setattr(engine.template_config_parser, "load_config", lambda *a, **k: "junk")
    assert engine._get_template_control_count("x") == 0
    monkeypatch.setattr(engine.template_config_parser, "load_config", _boom)
    assert engine._get_template_control_count("x") == 0
    engine.template_config_parser = None
    assert engine._get_template_control_count("x") == 0


def test_get_available_source_urls(engine, dirs, monkeypatch):
    engine._registered_template_routes = {"alpha", "hidden", "ctl"}
    engine._registered_dynamic_handlers = {"ctl_go", "hidden_go", "gone_go", "nounderscore"}
    hidden = {"hidden"}
    monkeypatch.setattr(engine.template_config_parser, "is_config_hidden", lambda n: n in hidden)
    monkeypatch.setattr(engine, "_get_template_control_count", lambda n: 4)
    monkeypatch.setattr(containers_mod, "list_container_models",
                        lambda: [{"route": "box", "enabled": True}, {"route": "off", "enabled": False}])
    monkeypatch.setattr(containers_mod, "container_source_record",
                        lambda model, base: {"name": model["route"], "url": f"{base}/{model['route']}"})
    urls = engine.get_available_source_urls()
    by_name = {u["name"]: u for u in urls}
    assert sorted(by_name) == ["alpha", "box", "ctl"]
    assert by_name["alpha"] == {"name": "alpha", "url": "http://127.0.0.1:5993/alpha", "type": "template",
                                "description": "HTML template: alpha.html"}
    assert by_name["ctl"]["type"] == "template_with_controls" and by_name["ctl"]["control_count"] == 4
    assert by_name["box"]["url"] == "http://127.0.0.1:5993/box"


def test_get_available_source_urls_without_parser_or_handlers(engine, monkeypatch):
    engine.template_config_parser = None
    engine.__dict__.pop("_registered_dynamic_handlers", None)
    engine.host = "192.168.1.5"
    monkeypatch.setattr(containers_mod, "list_container_models", _boom)
    assert engine.get_available_source_urls() == [{"name": "alpha", "url": "http://192.168.1.5:5993/alpha",
                                                   "type": "template", "description": "HTML template: alpha.html"}]
    engine._registered_dynamic_handlers = {"alpha_go"}
    monkeypatch.setattr(engine, "_get_template_control_count", lambda n: 1)
    assert engine.get_available_source_urls()[0]["has_dynamic_controls"] is True


def test_get_available_source_urls_error(engine):
    engine._registered_template_routes = None
    assert engine.get_available_source_urls() == []

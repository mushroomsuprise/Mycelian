# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""WebEngine lifecycle methods (modules/web_engine.py, before ``register_routes``):
port probes, hub helpers, heartbeat / startup-sync / watchdog / supervisor loops,
listener cleanup, stale port holder handling, foreign-port wait and auto-restart.

Loops run synchronously against a fake clock (``web_engine.time``); subprocess,
socket, urlopen, OBS and notifications are faked. No real ports or processes.
"""

from __future__ import annotations

import os
import sys
import threading
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine as we  # noqa: E402


class Clock:
    def __init__(self, t=50_000.0):
        self.t = t
        self.sleeps = []
        self.on_sleep = None

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


@pytest.fixture(scope="module", autouse=True)
def _collect_engines():
    """Each WebEngine is a large reference cycle (routes close over self); free them
    when this file is done so a later collection pause doesn't land in other tests."""
    yield
    import gc

    gc.collect()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "web_engine_instance", we.web_engine_instance)
    tdir = tmp_path / "templates"
    tdir.mkdir()
    eng = we.WebEngine(template_dir=str(tdir), port=5996)
    eng._maintenance_hooks_last_run = float("inf")
    return eng


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(we, "time", c)
    return c


@pytest.fixture
def shutdown(monkeypatch):
    import modules.shutdown as sd

    state = types.SimpleNamespace(value=False, calls=0, after=None)

    def fake():
        state.calls += 1
        if state.after is not None and state.calls > state.after:
            return True
        return state.value

    monkeypatch.setattr(sd, "is_shutdown_in_progress", fake)
    return state


class FakeThread:
    started = []

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args, self.name, self.daemon = target, args, name, daemon
        self.alive = False

    def start(self):
        FakeThread.started.append(self)
        self.alive = True

    def is_alive(self):
        return self.alive


@pytest.fixture
def fake_threads(monkeypatch):
    """Swap threading inside web_engine for one whose Thread never runs."""
    FakeThread.started = []
    ns = types.SimpleNamespace(**{k: getattr(threading, k) for k in ("Lock", "RLock", "get_ident", "Condition")})
    ns.Thread = FakeThread
    monkeypatch.setattr(we, "threading", ns)
    return FakeThread.started


def _run(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


# --------------------------------------------------------------------------- probes


def test_port_is_open(engine, monkeypatch):
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def create_connection(addr, timeout):
        calls.append((addr, timeout))
        return Conn()

    monkeypatch.setattr(we, "socket", types.SimpleNamespace(create_connection=create_connection))
    assert engine._port_is_open() is True
    assert calls == [(("127.0.0.1", 5996), 0.5)]

    def refuse(addr, timeout):
        raise ConnectionRefusedError()

    monkeypatch.setattr(we, "socket", types.SimpleNamespace(create_connection=refuse))
    assert engine._port_is_open() is False


def test_probe_health_endpoint(engine, monkeypatch):
    seen = []

    class Resp:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(we, "urlopen", lambda req, timeout: seen.append(req) or Resp(200))
    assert engine._probe_health_endpoint() is True
    assert seen[0].full_url == "http://127.0.0.1:5996/api/health"
    assert seen[0].get_header("User-agent") == "Mycelian-WebEngine-Probe"
    monkeypatch.setattr(we, "urlopen", lambda req, timeout: Resp(503))
    assert engine._probe_health_endpoint() is False
    monkeypatch.setattr(we, "urlopen", mock.Mock(side_effect=OSError("refused")))
    assert engine._probe_health_endpoint() is False


@pytest.fixture
def warnings_log(monkeypatch):
    out = []
    monkeypatch.setattr(we.logger, "warning", lambda msg, *a, **k: out.append(msg % a if a else msg))
    return out


def test_log_port_holder_hint_windows(engine, monkeypatch, warnings_log):
    monkeypatch.setattr(we.sys, "platform", "win32")
    netstat = "  TCP 0.0.0.0:5996  0.0.0.0:0  LISTENING  4242\n  TCP 1.1.1.1:80 x ESTABLISHED 1\n"
    run = mock.Mock(return_value=_run(netstat))
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    engine._log_port_holder_hint()
    assert run.call_args[0][0] == ["netstat", "-ano", "-p", "TCP"]
    assert warnings_log == ["Port 5996 may be held by: TCP 0.0.0.0:5996  0.0.0.0:0  LISTENING  4242"]
    engine.host = "10.0.0.1"
    warnings_log.clear()
    engine._log_port_holder_hint()  # needle "10.0.0.1:5996" not present
    assert warnings_log == []
    run.side_effect = OSError("no netstat")
    engine._log_port_holder_hint()


def test_log_port_holder_hint_mac_and_linux(engine, monkeypatch, warnings_log):
    monkeypatch.setattr(we.sys, "platform", "darwin")
    lsof = "COMMAND PID USER\n\nControlCe 512 me 9u IPv4 TCP *:5996 (LISTEN)\n"
    run = mock.Mock(return_value=_run(lsof))
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    engine._log_port_holder_hint()
    assert run.call_args[0][0] == ["lsof", "-nP", "-iTCP:5996", "-sTCP:LISTEN"]
    assert warnings_log == ["Port 5996 may be held by: ControlCe 512 me 9u IPv4 TCP *:5996 (LISTEN)"]
    run.side_effect = OSError
    engine._log_port_holder_hint()
    monkeypatch.setattr(we.sys, "platform", "linux")
    run.reset_mock()
    engine._log_port_holder_hint()
    run.assert_not_called()


# --------------------------------------------------------------------------- hub helpers


def test_run_off_hub_plain_call_off_hub(engine):
    engine._server_thread_ident = -1
    assert engine._run_off_hub(lambda a, b: a + b, 2, 3) == 5


def test_run_off_hub_uses_threadpool_on_hub(engine):
    engine._server_thread_ident = threading.get_ident()
    pool = mock.Mock()
    pool.apply.return_value = "pooled"
    with mock.patch("gevent.get_hub", return_value=types.SimpleNamespace(threadpool=pool)):
        assert engine._run_off_hub(max, 1, 9) == "pooled"
    pool.apply.assert_called_once_with(max, (1, 9))
    with mock.patch("gevent.get_hub", side_effect=RuntimeError("no hub")):
        assert engine._run_off_hub(max, 1, 9) == 9


def test_hub_alert_state_guard_off_hub_and_import_failure(engine):
    engine._server_thread_ident = -1
    import contextlib

    assert isinstance(engine._hub_alert_state_guard(), contextlib.nullcontext)
    engine._server_thread_ident = threading.get_ident()
    with mock.patch.dict(sys.modules, {"gevent.lock": None}):
        assert isinstance(engine._hub_alert_state_guard(), contextlib.nullcontext)


def test_hub_alert_state_guard_lock_per_generation(engine):
    engine._server_thread_ident = threading.get_ident()
    engine._run_generation = 1
    a = engine._hub_alert_state_guard()
    assert engine._hub_alert_state_guard() is a
    engine._run_generation = 2
    b = engine._hub_alert_state_guard()
    assert b is not a and list(engine._hub_alert_state_locks) == [2]


def test_hub_alert_state_serialized_wraps_handler(engine):
    engine._server_thread_ident = -1

    def handler(x, y=0):
        """doc"""
        return x + y

    wrapped = engine._hub_alert_state_serialized(handler)
    assert wrapped(1, y=2) == 3 and wrapped.__name__ == "handler" and wrapped.__doc__ == "doc"


def test_hub_run_generation_and_run_is_current(engine):
    engine._run_generation = 7
    assert engine._hub_run_generation() == 7
    engine._run_thread_generations = {threading.get_ident(): 3}
    assert engine._hub_run_generation() == 3
    engine._run_thread_generations = {-5: 3}
    assert engine._hub_run_generation() == 7
    engine.is_running = False
    assert engine._run_is_current(7) is False
    engine.is_running = True
    assert engine._run_is_current(7) is True and engine._run_is_current(6) is False


# --------------------------------------------------------------------------- heartbeat / startup sync arming


def test_ensure_web_engine_heartbeat(engine, fake_threads):
    engine.socketio.start_background_task = mock.Mock()
    engine.ensure_web_engine_heartbeat()
    engine.socketio.start_background_task.assert_called_once_with(engine._web_engine_heartbeat_loop)
    assert engine._last_gevent_heartbeat is not None
    assert [t.name for t in fake_threads] == ["MycelianWebEngineWatchdog"]
    engine.ensure_web_engine_heartbeat()  # already started
    assert engine.socketio.start_background_task.call_count == 1
    # Restarted heartbeat keeps a live watchdog
    engine._heartbeat_task_started = False
    engine.ensure_web_engine_heartbeat()
    assert len(fake_threads) == 1


def test_ensure_web_engine_heartbeat_start_failure(engine, fake_threads):
    engine.socketio.start_background_task = mock.Mock(side_effect=RuntimeError("no hub"))
    engine.ensure_web_engine_heartbeat()
    assert engine._heartbeat_task_started is False and fake_threads == []


def test_ensure_startup_client_sync(engine):
    engine.socketio.start_background_task = mock.Mock()
    engine.ensure_startup_client_sync()
    engine.ensure_startup_client_sync()
    engine.socketio.start_background_task.assert_called_once_with(engine._startup_client_sync_loop)
    engine._startup_client_sync_started = False
    engine.socketio.start_background_task.side_effect = RuntimeError
    engine.ensure_startup_client_sync()
    assert engine._startup_client_sync_started is False


def test_emit_startup_overlay_recovery_once(engine):
    engine.broadcast_overlay_recovery = mock.Mock()
    engine._emit_startup_overlay_recovery_once()
    engine._emit_startup_overlay_recovery_once()
    engine.broadcast_overlay_recovery.assert_called_once_with("web_engine", "startup")
    engine._startup_overlay_recovery_sent = False
    engine.broadcast_overlay_recovery.side_effect = RuntimeError
    engine._emit_startup_overlay_recovery_once()
    assert engine._startup_overlay_recovery_sent is True


# --------------------------------------------------------------------------- startup OBS refresh


@pytest.fixture
def gates(monkeypatch):
    monkeypatch.setattr(we, "_obs_browser_refresh_done_this_process", False)
    monkeypatch.setattr(we, "_obs_dock_reload_done_this_process", False)


@pytest.fixture
def obs(monkeypatch):
    import modules.obs_service as obs_mod

    svc = types.SimpleNamespace(is_enabled=lambda: True, is_connected=lambda: True,
                                enqueue_refresh_mycelian_browser_sources=mock.Mock())
    monkeypatch.setattr(obs_mod, "obs_service", svc)
    return svc


def test_startup_obs_refresh_branches(engine, gates, obs, monkeypatch):
    engine.overlay_source_routes = lambda: []
    assert engine._maybe_enqueue_startup_obs_refresh() is False
    engine.overlay_source_routes = lambda: ["alerts"]
    obs.is_enabled = lambda: False
    assert engine._maybe_enqueue_startup_obs_refresh() is True
    obs.is_enabled = lambda: True
    obs.is_connected = lambda: False
    assert engine._maybe_enqueue_startup_obs_refresh() is False
    obs.is_connected = lambda: True
    assert engine._maybe_enqueue_startup_obs_refresh() is True
    obs.enqueue_refresh_mycelian_browser_sources.assert_called_once_with(5996, ["alerts"])
    assert we._obs_browser_refresh_already_done() is True
    assert engine._maybe_enqueue_startup_obs_refresh() is True  # gate spent
    assert obs.enqueue_refresh_mycelian_browser_sources.call_count == 1


def test_startup_obs_refresh_lost_claim_and_enqueue_error(engine, gates, obs, monkeypatch):
    engine.overlay_source_routes = lambda: ["alerts"]
    monkeypatch.setattr(we, "_claim_obs_browser_refresh_this_process", lambda: False)
    assert engine._maybe_enqueue_startup_obs_refresh() is True
    obs.enqueue_refresh_mycelian_browser_sources.assert_not_called()
    monkeypatch.undo()


def test_startup_obs_refresh_enqueue_error(engine, gates, obs):
    engine.overlay_source_routes = lambda: ["alerts"]
    obs.enqueue_refresh_mycelian_browser_sources.side_effect = RuntimeError("ws closed")
    assert engine._maybe_enqueue_startup_obs_refresh() is True


def test_startup_obs_refresh_import_failure(engine, gates):
    engine.overlay_source_routes = lambda: ["alerts"]
    with mock.patch.dict(sys.modules, {"modules.obs_service": None}):
        assert engine._maybe_enqueue_startup_obs_refresh() is False


# --------------------------------------------------------------------------- startup dock wake


@pytest.fixture
def docks(monkeypatch):
    import modules.obs_browser_docks as d

    st = types.SimpleNamespace(
        docks=[{"title": "Feed", "route": "activity_feed", "url": "http://127.0.0.1:5996/activity_feed"}],
        running=True, pending=None, calls=[],
    )
    monkeypatch.setattr(d, "ensure_dock_boot_html", lambda: st.calls.append("boot"))
    monkeypatch.setattr(d, "set_dock_boot_context", lambda port, routes: st.calls.append(("ctx", port)))
    monkeypatch.setattr(d, "list_mycelian_extra_browser_docks", lambda port, routes: st.docks)
    monkeypatch.setattr(d, "migrate_extra_browser_docks_to_boot",
                        lambda port, routes: st.calls.append("migrate"))
    monkeypatch.setattr(d, "obs_appears_running", lambda: st.running)
    monkeypatch.setattr(d, "pending_docks_missing_routes",
                        lambda docks, connected: st.pending if st.pending is not None else docks)
    monkeypatch.setattr(d, "is_dock_boot_file_url", lambda url: str(url).startswith("file:"))
    monkeypatch.setattr(d, "wake_mycelian_browser_docks", lambda rows: None)
    st.mod = d
    return st


def test_dock_wake_gate_and_routes(engine, gates, docks, monkeypatch):
    engine.overlay_source_routes = lambda: []
    assert engine._maybe_enqueue_startup_dock_wake() is False
    monkeypatch.setattr(we, "_obs_dock_reload_done_this_process", True)
    assert engine._maybe_enqueue_startup_dock_wake() is True


def test_dock_wake_helpers_unavailable(engine, gates):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    with mock.patch.dict(sys.modules, {"modules.obs_browser_docks": None}):
        assert engine._maybe_enqueue_startup_dock_wake() is True


def test_dock_wake_boot_file_error_and_list_error(engine, gates, docks, monkeypatch):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    monkeypatch.setattr(docks.mod, "ensure_dock_boot_html", mock.Mock(side_effect=OSError("ro")))
    monkeypatch.setattr(docks.mod, "list_mycelian_extra_browser_docks", mock.Mock(side_effect=OSError("cfg")))
    assert engine._maybe_enqueue_startup_dock_wake() is True


def test_dock_wake_no_docks(engine, gates, docks):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    docks.docks = []
    assert engine._maybe_enqueue_startup_dock_wake() is True
    assert docks.calls == ["boot", ("ctx", 5996)]


def test_dock_wake_obs_not_running_migrates(engine, gates, docks, monkeypatch):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    docks.running = False
    assert engine._maybe_enqueue_startup_dock_wake() is False
    assert "migrate" in docks.calls
    monkeypatch.setattr(docks.mod, "migrate_extra_browser_docks_to_boot", mock.Mock(side_effect=OSError))
    assert engine._maybe_enqueue_startup_dock_wake() is False


def test_dock_wake_already_connected_or_on_boot_page(engine, gates, docks):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    docks.pending = []
    assert engine._maybe_enqueue_startup_dock_wake() is True
    docks.pending = [{"title": "Feed", "url": "file:///boot.html"}]
    assert engine._maybe_enqueue_startup_dock_wake() is True
    assert we._obs_dock_reload_already_done() is False


def test_dock_wake_grace_then_wakes_once(engine, gates, docks, fake_threads):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    engine._dock_wake_grace_done = False
    assert engine._maybe_enqueue_startup_dock_wake() is False  # one grace poll
    assert engine._maybe_enqueue_startup_dock_wake() is True
    assert [t.name for t in fake_threads] == ["MycelianOBSDockWake"]
    assert fake_threads[0].args == (docks.docks,)
    # Gate now spent
    assert engine._maybe_enqueue_startup_dock_wake() is True
    assert len(fake_threads) == 1


def test_dock_wake_lost_claim_and_thread_failure(engine, gates, docks, monkeypatch):
    engine.overlay_source_routes = lambda: ["activity_feed"]
    engine._dock_wake_grace_done = True
    monkeypatch.setattr(we, "_claim_obs_dock_reload_this_process", lambda: False)
    assert engine._maybe_enqueue_startup_dock_wake() is True
    monkeypatch.setattr(we, "_claim_obs_dock_reload_this_process", lambda: True)

    class Boom(FakeThread):
        def start(self):
            raise RuntimeError("can't start new thread")

    ns = types.SimpleNamespace(Thread=Boom, Lock=threading.Lock, get_ident=threading.get_ident)
    monkeypatch.setattr(we, "threading", ns)
    assert engine._maybe_enqueue_startup_dock_wake() is True


# --------------------------------------------------------------------------- startup sync loop


def _sync_engine(engine, clock):
    engine.is_running = True
    engine._run_generation = 1
    engine.socketio.sleep = clock.sleep
    engine._emit_startup_overlay_recovery_once = mock.Mock()


def test_startup_sync_loop_completes_when_both_attempted(engine, clock, gates):
    _sync_engine(engine, clock)
    engine._overlay_socket_connected_count = lambda: 2
    obs = iter([False, True])
    engine._maybe_enqueue_startup_obs_refresh = lambda: next(obs)
    engine._maybe_enqueue_startup_dock_wake = mock.Mock(return_value=True)
    engine._startup_client_sync_loop()
    assert clock.sleeps == [engine._STARTUP_CLIENT_SYNC_SETTLE_SEC, engine._STARTUP_CLIENT_SYNC_POLL_SEC]
    assert engine._emit_startup_overlay_recovery_once.call_count == 2
    assert engine._maybe_enqueue_startup_dock_wake.call_count == 1


def test_startup_sync_loop_gives_up_at_deadline(engine, clock):
    _sync_engine(engine, clock)
    engine._overlay_socket_connected_count = lambda: 0
    engine._maybe_enqueue_startup_obs_refresh = lambda: False
    engine._maybe_enqueue_startup_dock_wake = lambda: False
    engine._startup_client_sync_loop()
    assert sum(clock.sleeps) >= engine._STARTUP_CLIENT_SYNC_SETTLE_SEC + engine._STARTUP_CLIENT_SYNC_OBS_WAIT_SEC
    engine._emit_startup_overlay_recovery_once.assert_not_called()


def test_startup_sync_loop_exits_on_sleep_error_or_stale_run(engine, clock):
    _sync_engine(engine, clock)
    engine.socketio.sleep = mock.Mock(side_effect=RuntimeError)
    engine._maybe_enqueue_startup_obs_refresh = mock.Mock()
    engine._startup_client_sync_loop()
    engine._maybe_enqueue_startup_obs_refresh.assert_not_called()

    engine.socketio.sleep = clock.sleep
    engine.is_running = False
    engine._startup_client_sync_loop()
    engine._maybe_enqueue_startup_obs_refresh.assert_not_called()

    # Poll sleep fails mid-loop
    engine.is_running = True
    sleeps = iter([None])

    def settle_then_fail(s):
        next(sleeps)

    engine.socketio.sleep = mock.Mock(side_effect=settle_then_fail)
    engine._overlay_socket_connected_count = lambda: 0
    engine._maybe_enqueue_startup_obs_refresh = mock.Mock(return_value=False)
    engine._maybe_enqueue_startup_dock_wake = mock.Mock(return_value=False)
    engine._startup_client_sync_loop()
    assert engine._maybe_enqueue_startup_obs_refresh.call_count == 1


# --------------------------------------------------------------------------- heartbeat loop / tick


def test_heartbeat_loop_ticks_until_run_ends(engine, clock):
    engine.is_running = True
    engine._run_generation = 4
    engine._heartbeat_task_started = True
    ticks = []

    def tick():
        ticks.append(1)
        if len(ticks) == 2:
            raise RuntimeError("bad tick")
        if len(ticks) == 3:
            engine.is_running = False

    engine._web_engine_heartbeat_tick = tick
    engine.socketio.sleep = clock.sleep
    engine._web_engine_heartbeat_loop()
    assert len(ticks) == 3
    assert engine._last_gevent_heartbeat is not None  # set by the failed tick
    assert engine._heartbeat_task_started is False


def test_heartbeat_loop_stops_on_sleep_error_and_stale_generation(engine, clock):
    engine.is_running = True
    engine._run_generation = 1
    engine._heartbeat_task_started = True
    engine.socketio.sleep = mock.Mock(side_effect=RuntimeError)
    engine._web_engine_heartbeat_loop()
    assert engine._heartbeat_task_started is False

    # The run ends while sleeping; an older generation must not clear the flag
    engine._heartbeat_task_started = True

    def sleep_and_restart(s):
        engine._run_generation = 2

    engine.socketio.sleep = sleep_and_restart
    engine._run_thread_generations = {threading.get_ident(): 1}
    engine._web_engine_heartbeat_tick = mock.Mock()
    engine._web_engine_heartbeat_loop()
    engine._web_engine_heartbeat_tick.assert_not_called()
    assert engine._heartbeat_task_started is True


def test_heartbeat_tick_records_drift_and_logs(engine, clock, monkeypatch):
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    engine._get_bottleneck_diagnostics = lambda: {"bottlenecked": False, "metrics": {"http_p95_ms": 5}}
    engine._maybe_log_bottleneck_calibration_watch = mock.Mock()
    engine._web_engine_heartbeat_tick()
    assert engine._last_heartbeat_mono == clock.t and engine._last_gevent_heartbeat == clock.t
    assert "drift=0ms" in printed[-1] and printed[-1].endswith("-> running")
    engine._maybe_log_bottleneck_calibration_watch.assert_called_once()
    clock.t += 12.5
    engine._get_bottleneck_diagnostics = lambda: {"bottlenecked": True, "reasons": ["x"], "metrics": None}
    engine._web_engine_heartbeat_tick()
    assert "drift=2500ms" in printed[-1] and printed[-1].endswith("-> overloaded")


# --------------------------------------------------------------------------- gevent blocking monitor


def test_gevent_monitor_disabled_without_env(engine, monkeypatch):
    monkeypatch.delenv("MYCELIAN_GEVENT_MONITOR", raising=False)
    fake = types.SimpleNamespace(config=types.SimpleNamespace())
    with mock.patch.dict(sys.modules, {"gevent": fake}):
        engine._maybe_enable_gevent_blocking_monitor()
    assert vars(fake.config) == {}


def test_gevent_monitor_subscribes_once_and_rate_limits(engine, monkeypatch, clock, warnings_log):
    monkeypatch.setenv("MYCELIAN_GEVENT_MONITOR", "1")
    monkeypatch.setattr(we.WebEngine, "_gevent_monitor_subscribed", False, raising=False)

    class Blocked:
        def __init__(self, t, info):
            self.blocking_time = t
            self.info = info

    events = types.SimpleNamespace(EventLoopBlocked=Blocked, subscribers=[])
    fake = types.SimpleNamespace(config=types.SimpleNamespace(), events=events)
    with mock.patch.dict(sys.modules, {"gevent": fake, "gevent.events": events}):
        engine._maybe_enable_gevent_blocking_monitor()
        engine._maybe_enable_gevent_blocking_monitor()  # already subscribed
    assert fake.config.monitor_thread is True and fake.config.max_blocking_time == 1.0
    assert fake.config.print_blocking_reports is False
    assert len(events.subscribers) == 1
    sub = events.subscribers[0]
    sub("not blocked")
    sub(Blocked(1.5, ["frame a", "frame b"]))
    sub(Blocked(2.0, None))  # within 30s of the last log
    assert warnings_log == ["WebEngine gevent hub blocked > 1.5s:\nframe a\nframe b"]
    clock.t += 31
    sub(Blocked(2.0, None))
    assert warnings_log[-1] == "WebEngine gevent hub blocked > 2.0s:\n"


def test_gevent_monitor_unavailable(engine, monkeypatch):
    monkeypatch.setenv("MYCELIAN_GEVENT_MONITOR", "1")
    with mock.patch.dict(sys.modules, {"gevent": None}):
        engine._maybe_enable_gevent_blocking_monitor()  # logged at debug, no raise


# --------------------------------------------------------------------------- watchdog


def test_watchdog_loop_dumps_once_per_stall(engine, clock, warnings_log):
    engine.is_running = True
    engine._last_gevent_heartbeat = None
    dumps = []
    engine._dump_all_thread_stacks = dumps.append
    plan = {
        1: lambda: None,                                                   # no heartbeat yet
        2: lambda: setattr(engine, "_last_gevent_heartbeat", clock.t - 50),  # stalled
        3: lambda: None,                                                   # still stalled: no 2nd dump
        4: lambda: setattr(engine, "_last_gevent_heartbeat", clock.t),     # recovered
        5: lambda: setattr(engine, "_last_gevent_heartbeat", clock.t),     # healthy, no log
        6: lambda: setattr(engine, "is_running", False),
    }
    clock.on_sleep = lambda n: plan[n]()
    engine._watchdog_loop()
    assert len(dumps) == 1 and dumps[0].startswith("WebEngine gevent hub unresponsive for")
    assert warnings_log.count("WebEngine watchdog: gevent hub recovered after stall") == 1
    assert clock.sleeps == [15.0] * 6


def test_watchdog_loop_not_running(engine, clock):
    engine.is_running = False
    engine._watchdog_loop()
    assert clock.sleeps == []


def test_dump_all_thread_stacks(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(we, "get_data_path", lambda sub: str(tmp_path / sub))
    errors = []
    monkeypatch.setattr(we.logger, "error", lambda msg, *a: errors.append(a))
    engine._dump_all_thread_stacks("hub stuck")
    files = list((tmp_path / "logs").glob("freeze_dump_*.txt"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("Thread stack dump (hub stuck) at") and "File" in text
    monkeypatch.setattr(we, "get_data_path", mock.Mock(side_effect=OSError("ro")))
    engine._dump_all_thread_stacks("again")
    assert errors[-1][0] == "again"


# --------------------------------------------------------------------------- supervisor


def test_start_supervisor(engine, fake_threads):
    engine.start_supervisor()
    assert [t.name for t in fake_threads] == ["WebEngineSupervisor"]
    assert fake_threads[0].target == engine._supervisor_loop
    engine.start_supervisor()  # alive -> no second thread
    assert len(fake_threads) == 1


def _supervise(engine, clock, shutdown, iterations, setup):
    shutdown.after = iterations
    restarts = []
    engine.request_restart = restarts.append
    clock.on_sleep = lambda n: setup(n)
    engine._supervisor_loop()
    return restarts


def test_supervisor_restarts_zombie_dead_and_frozen(engine, clock, shutdown):
    def setup(n):
        if n == 1:  # after initial grace: zombie
            engine.server_thread = types.SimpleNamespace(is_alive=lambda: True)
            engine.is_running = False
        elif n == 2:  # thread died
            engine.server_thread = types.SimpleNamespace(is_alive=lambda: False)
        elif n == 3:  # running but heartbeat frozen
            engine.server_thread = types.SimpleNamespace(is_alive=lambda: True)
            engine.is_running = True
            engine._restart_in_progress = True
            engine._last_gevent_heartbeat = clock.t - 100

    restarts = _supervise(engine, clock, shutdown, 3, setup)
    assert restarts[0].endswith("(zombie)")
    assert restarts[1] == "WebEngine thread is no longer running"
    assert restarts[2].startswith("WebEngine gevent hub stopped responding")


def test_supervisor_probe_failures_and_grace(engine, clock, shutdown):
    probes = iter([True, False, False, False])
    engine._probe_health_endpoint = lambda: next(probes)
    engine.server_thread = types.SimpleNamespace(is_alive=lambda: True)
    engine.is_running = True
    engine._last_gevent_heartbeat = None

    def setup(n):
        if n == 1:
            engine._server_started_at = clock.t - 100

    restarts = _supervise(engine, clock, shutdown, 4, setup)
    assert restarts == ["WebEngine /api/health unresponsive (2 consecutive probe failures)"]

    # In the startup grace window the probe is skipped
    engine._server_started_at = clock.t + 1000
    engine._probe_health_endpoint = mock.Mock()
    shutdown.calls = 0
    _supervise(engine, clock, shutdown, 1, lambda n: None)
    engine._probe_health_endpoint.assert_not_called()


def test_supervisor_foreign_wait_and_errors(engine, clock, shutdown):
    engine._foreign_port_wait_active = True
    engine._thread_alive = mock.Mock()

    def setup(n):
        if n == 2:
            engine._foreign_port_wait_active = False
            engine._thread_alive = mock.Mock(side_effect=RuntimeError("bad"))

    _supervise(engine, clock, shutdown, 3, setup)
    assert engine._thread_alive.call_count == 2  # raised twice, loop kept going


def test_supervisor_returns_immediately_on_shutdown(engine, clock, shutdown):
    shutdown.value = True
    engine._thread_alive = mock.Mock()
    engine._supervisor_loop()
    engine._thread_alive.assert_not_called()
    assert clock.sleeps == [engine._SUPERVISOR_POLL_SEC]


# --------------------------------------------------------------------------- listener handling


def test_extract_listener_from_wsgi(engine):
    assert engine._extract_listener_from_wsgi(None) is None
    assert engine._extract_listener_from_wsgi(types.SimpleNamespace(socket="S", server="V")) == "S"
    assert engine._extract_listener_from_wsgi(types.SimpleNamespace(socket=None, server="V")) == "V"
    assert engine._extract_listener_from_wsgi(object()) is None


class _BadSocketIO:
    def __getattr__(self, name):
        raise RuntimeError("socketio broken")

    def stop(self):
        raise RuntimeError("stop")


def test_refresh_cached_listener(engine):
    engine.socketio = types.SimpleNamespace(wsgi_server=types.SimpleNamespace(socket="L"))
    engine._refresh_cached_listener()
    assert engine._cached_listener == "L"
    engine.socketio = types.SimpleNamespace()
    engine._refresh_cached_listener()
    assert engine._cached_listener == "L"
    engine.socketio = _BadSocketIO()
    engine._refresh_cached_listener()  # swallowed


def test_close_listener(engine):
    cached, live = mock.Mock(), mock.Mock()
    engine._cached_listener = cached
    engine.socketio = types.SimpleNamespace(wsgi_server=types.SimpleNamespace(socket=live))
    engine._close_listener()
    cached.close.assert_called_once() and live.close.assert_called_once()
    assert engine._cached_listener is None
    # Same listener cached and live is closed once; objects without close are skipped
    engine._cached_listener = live
    engine._close_listener()
    assert live.close.call_count == 2
    engine._cached_listener = object()
    engine.socketio = _BadSocketIO()
    engine._close_listener()
    assert engine._cached_listener is not None  # nothing closed
    bad = mock.Mock()
    bad.close.side_effect = OSError
    engine._cached_listener = bad
    engine._close_listener()
    assert engine._cached_listener is bad


class _Thread:
    def __init__(self, name, alive_after_join=False, alive=True):
        self.name = name
        self.alive = alive
        self.alive_after_join = alive_after_join
        self.joined = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)
        self.alive = self.alive_after_join


def test_prune_and_join_known_server_threads(engine, warnings_log):
    a, b, dead = _Thread("a"), _Thread("b", alive_after_join=True), _Thread("dead", alive=False)
    engine._known_server_threads = [a, None, dead]
    engine.server_thread = b
    engine._join_known_server_threads(2.0)
    assert a.joined == [2.0] and b.joined == [2.0] and dead.joined == []
    assert warnings_log == ["WebEngine thread b did not exit within 2s"]
    assert engine._known_server_threads == []


def test_wait_for_port_free(engine, clock):
    states = iter([True, True, False])
    engine._port_is_open = lambda: next(states)
    assert engine._wait_for_port_free(timeout=5) is True
    assert clock.sleeps == [0.25, 0.25]
    engine._port_is_open = lambda: True
    assert engine._wait_for_port_free(timeout=1) is False


def test_is_healthy(engine):
    engine._probe_health_endpoint = lambda: True
    assert engine._is_healthy() is False
    engine.is_running = True
    engine.server_thread = types.SimpleNamespace(is_alive=lambda: True)
    assert engine._is_healthy() is True


def test_force_stop_and_aggressive_stop(engine):
    calls = []
    engine.stop = lambda: calls.append("stop")
    engine._close_listener = lambda: calls.append("close")
    engine.socketio = types.SimpleNamespace(stop=lambda: calls.append("sio"))
    engine._join_known_server_threads = lambda t: calls.append(("join", t))
    engine._port_is_open = lambda: False
    engine._log_port_holder_hint = lambda: calls.append("hint")
    engine.force_stop(join_timeout=1.0)
    assert calls == ["stop", "close", "sio", ("join", 1.0)]
    calls.clear()
    engine.stop = mock.Mock(side_effect=RuntimeError)
    engine.socketio = _BadSocketIO()
    engine._port_is_open = lambda: True
    engine._aggressive_stop()
    assert calls == ["close", ("join", engine._RESTART_THREAD_JOIN_SEC), "hint"]


# --------------------------------------------------------------------------- stale port holder


def test_extract_listening_pid(engine, monkeypatch, warnings_log):
    run = mock.Mock()
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    monkeypatch.setattr(we.sys, "platform", "win32")
    run.return_value = _run("TCP 0.0.0.0:80 x LISTENING 1\nTCP 0.0.0.0:5996 0.0.0.0:0 LISTENING 777\n")
    assert engine._extract_listening_pid() == 777
    engine.host = "127.0.0.1"
    run.return_value = _run("TCP 127.0.0.1:5996 x LISTENING 778\n")
    assert engine._extract_listening_pid() == 778
    run.return_value = _run("nothing\n")
    assert engine._extract_listening_pid() is None
    monkeypatch.setattr(we.sys, "platform", "darwin")
    run.return_value = _run("\n4321\n")
    assert engine._extract_listening_pid() == 4321
    assert run.call_args[0][0] == ["lsof", "-nP", "-t", "-iTCP:5996", "-sTCP:LISTEN"]
    run.return_value = _run("")
    assert engine._extract_listening_pid() is None
    run.return_value = _run("notapid\n")
    assert engine._extract_listening_pid() is None
    assert "holder PID" in warnings_log[-1]
    monkeypatch.setattr(we.sys, "platform", "linux")
    assert engine._extract_listening_pid() is None


def test_is_own_overlay_pid(engine, monkeypatch):
    engine._probe_health_endpoint = lambda: False
    assert engine._is_own_overlay_pid(os.getpid()) is True
    monkeypatch.setattr(we, "_process_cmdline", lambda pid: "/Applications/Mycelian.app/x")
    assert engine._is_own_overlay_pid(1) is True
    monkeypatch.setattr(we, "_process_cmdline", lambda pid: "ControlCenter")
    assert engine._is_own_overlay_pid(1) is False
    engine._probe_health_endpoint = lambda: True
    assert engine._is_own_overlay_pid(1) is True


def test_try_terminate_unknown_and_own_pid(engine):
    engine._extract_listening_pid = lambda: None
    assert engine._try_terminate_stale_port_holder() is False
    engine._extract_listening_pid = lambda: os.getpid()
    calls = []
    engine._close_listener = lambda: calls.append("close")
    engine._join_known_server_threads = lambda join_timeout: calls.append(join_timeout)
    engine._port_is_open = lambda: False
    assert engine._try_terminate_stale_port_holder() is True
    assert calls == ["close", 5.0]
    engine._port_is_open = lambda: True
    assert engine._try_terminate_stale_port_holder() is False


def test_try_terminate_foreign_is_left_alone(engine):
    engine._extract_listening_pid = lambda: 999999
    engine._is_own_overlay_pid = lambda pid: False
    engine._log_port_holder_hint = mock.Mock()
    assert engine._try_terminate_stale_port_holder() is False
    engine._log_port_holder_hint.assert_called_once()


def test_try_terminate_posix_sends_sigterm(engine, monkeypatch):
    import signal

    engine._extract_listening_pid = lambda: 999999
    engine._is_own_overlay_pid = lambda pid: True
    monkeypatch.setattr(we.sys, "platform", "darwin")
    kill = mock.Mock()
    monkeypatch.setattr(we.os, "kill", kill)
    assert engine._try_terminate_stale_port_holder() is True
    kill.assert_called_once_with(999999, signal.SIGTERM)
    kill.side_effect = ProcessLookupError
    assert engine._try_terminate_stale_port_holder() is False


def test_try_terminate_windows_taskkill(engine, monkeypatch):
    engine._extract_listening_pid = lambda: 4242
    engine._is_own_overlay_pid = lambda pid: True
    monkeypatch.setattr(we.sys, "platform", "win32")

    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    run = mock.Mock(return_value=_run(returncode=0))
    sp = types.SimpleNamespace(run=run, CREATE_NO_WINDOW=0x08000000, STARTUPINFO=StartupInfo,
                               STARTF_USESHOWWINDOW=1, SW_HIDE=0)
    monkeypatch.setattr(we, "subprocess", sp)
    assert engine._try_terminate_stale_port_holder() is True
    args, kw = run.call_args
    assert args[0] == ["taskkill", "/PID", "4242", "/F"]
    assert kw["creationflags"] == 0x08000000 and kw["startupinfo"].dwFlags == 1
    run.return_value = _run(returncode=128, stderr="Access denied")
    assert engine._try_terminate_stale_port_holder() is False
    # No STARTUPINFO on this platform build
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    run.return_value = _run(returncode=0)
    assert engine._try_terminate_stale_port_holder() is True
    assert "startupinfo" not in run.call_args[1] and run.call_args[1]["creationflags"] == 0


# --------------------------------------------------------------------------- port conflict / prepare


@pytest.mark.parametrize("platform, hint_part", [("darwin", "AirPlay Receiver"), ("win32", "port 5996")])
def test_notify_port_conflict(engine, monkeypatch, platform, hint_part):
    import modules.notification_engine as ne

    crit = mock.Mock()
    monkeypatch.setattr(ne, "notify_critical", crit)
    monkeypatch.setattr(we.sys, "platform", platform)
    engine._notify_port_conflict("ctx")
    kw = crit.call_args[1]
    assert hint_part in kw["hint"] and kw["dedupe_key"] == "web_engine:port_conflict"
    assert kw["details"] == "Overlay server could not bind 0.0.0.0:5996 (ctx)"
    crit.side_effect = RuntimeError
    engine._notify_port_conflict("ctx")


def test_prepare_port_for_startup_free_and_foreign(engine):
    engine._port_is_open = lambda: False
    engine._probe_health_endpoint = mock.Mock()
    engine.prepare_port_for_startup()
    engine._probe_health_endpoint.assert_not_called()
    engine._port_is_open = lambda: True
    engine._probe_health_endpoint = lambda: False
    engine._log_port_holder_hint = mock.Mock()
    engine._prepare_port_for_restart = mock.Mock()
    engine.prepare_port_for_startup()
    engine._log_port_holder_hint.assert_called_once()
    engine._prepare_port_for_restart.assert_not_called()


def test_prepare_port_for_startup_releases_own_listener(engine, clock):
    engine._probe_health_endpoint = lambda: True
    engine._prepare_port_for_restart = mock.Mock()
    engine._try_terminate_stale_port_holder = mock.Mock()
    states = iter([True, True, True, False])
    engine._port_is_open = lambda: next(states)
    engine.prepare_port_for_startup()
    engine._try_terminate_stale_port_holder.assert_called_once()
    assert clock.sleeps == [0.5]

    # Freed by the restart prep alone
    states = iter([True, False, False])
    engine._port_is_open = lambda: next(states)
    engine._try_terminate_stale_port_holder.reset_mock()
    engine.prepare_port_for_startup()
    engine._try_terminate_stale_port_holder.assert_not_called()

    # Still held at the end -> hint + error
    engine._port_is_open = lambda: True
    engine._log_port_holder_hint = mock.Mock()
    engine.prepare_port_for_startup()
    engine._log_port_holder_hint.assert_called_once()


def test_prepare_port_for_restart(engine):
    engine._port_is_open = lambda: False
    engine._probe_health_endpoint = mock.Mock()
    engine._prepare_port_for_restart()
    engine._probe_health_endpoint.assert_not_called()

    calls = []
    engine._port_is_open = lambda: True
    engine._probe_health_endpoint = lambda: True
    engine._aggressive_stop = lambda: calls.append("stop")
    engine._try_terminate_stale_port_holder = lambda: calls.append("kill")
    engine._wait_for_port_free = lambda timeout: calls.append(("wait", timeout))
    engine._prepare_port_for_restart()
    assert calls == ["stop", "kill", ("wait", 8.0)]

    calls.clear()
    states = iter([True, False])
    engine._port_is_open = lambda: next(states)
    engine._prepare_port_for_restart()
    assert calls == ["stop", ("wait", 8.0)]

    engine._port_is_open = lambda: True
    engine._probe_health_endpoint = lambda: False
    engine._log_port_holder_hint = mock.Mock()
    calls.clear()
    engine._prepare_port_for_restart()
    assert calls == [] and engine._log_port_holder_hint.call_count == 1


# --------------------------------------------------------------------------- foreign-port wait


def test_schedule_foreign_port_wait(engine, shutdown, fake_threads):
    engine._notify_port_conflict = mock.Mock()
    shutdown.value = True
    engine._schedule_foreign_port_wait()
    assert fake_threads == []
    shutdown.value = False
    engine._schedule_foreign_port_wait()
    assert [t.name for t in fake_threads] == ["WebEnginePortWait"]
    engine._notify_port_conflict.assert_called_once_with("foreign-holder")
    engine._schedule_foreign_port_wait()  # already waiting
    assert len(fake_threads) == 1


def test_foreign_port_wait_returns_when_healthy(engine, clock, shutdown):
    engine._foreign_port_wait_active = True
    engine._is_healthy = lambda: True
    engine._foreign_port_wait_loop()
    assert engine._foreign_port_wait_active is False


def test_foreign_port_wait_binds_after_release(engine, clock, shutdown):
    engine._foreign_port_wait_active = True
    engine._restart_attempts = 3
    engine._restart_giveup_notified = True
    engine._last_run_error = "port held"
    ports = iter([True, False])
    engine._port_is_open = lambda: next(ports)
    health = iter([False, False, False, True])
    engine._is_healthy = lambda: next(health)
    engine._start_server_thread = mock.Mock()
    engine._foreign_port_wait_loop()
    engine._start_server_thread.assert_called_once()
    assert engine._restart_attempts == 0 and engine._restart_giveup_notified is False
    assert engine._last_run_error is None and engine._foreign_port_wait_active is False
    assert clock.sleeps == [we._FOREIGN_PORT_WAIT_POLL_SEC, 1.0, 1.0]


def test_foreign_port_wait_bind_error_and_unconfirmed(engine, clock, shutdown):
    # 2 loop checks + 15 recovery checks, then the next loop check sees shutdown
    shutdown.after = 17
    engine._is_healthy = lambda: False
    engine._port_is_open = lambda: False
    starts = iter([RuntimeError("bind"), None])

    def start():
        err = next(starts)
        if err:
            raise err

    engine._start_server_thread = start
    engine._foreign_port_wait_loop()
    # bind error -> poll sleep; second bind -> 15 x 1s recovery, then poll sleep
    assert clock.sleeps[0] == we._FOREIGN_PORT_WAIT_POLL_SEC
    assert clock.sleeps.count(1.0) == int(we._FOREIGN_PORT_RECOVERY_SEC)
    assert clock.sleeps[-1] == we._FOREIGN_PORT_WAIT_POLL_SEC


def test_foreign_port_wait_shutdown_during_recovery(engine, clock, shutdown):
    shutdown.after = 1
    engine._is_healthy = lambda: False
    engine._port_is_open = lambda: False
    engine._start_server_thread = mock.Mock()
    engine._foreign_port_wait_loop()
    assert clock.sleeps == []


# --------------------------------------------------------------------------- _do_restart


@pytest.fixture
def restart(engine, clock):
    st = types.SimpleNamespace(calls=[], ports=[False], probes=[True], healthy=[True])

    def nxt(lst):
        return lst.pop(0) if len(lst) > 1 else lst[0]

    engine._aggressive_stop = lambda: st.calls.append("stop")
    engine._prepare_port_for_restart = lambda: st.calls.append("prep")
    engine._port_is_open = lambda: nxt(st.ports)
    engine._probe_health_endpoint = lambda: nxt(st.probes)
    engine._is_healthy = lambda: nxt(st.healthy)
    engine._start_server_thread = lambda: st.calls.append("start")
    engine._schedule_foreign_port_wait = lambda: st.calls.append("wait")
    engine._log_port_holder_hint = lambda: st.calls.append("hint")
    engine._notify_port_conflict = lambda ctx: st.calls.append(("conflict", ctx))
    engine._notify_restart_giveup = lambda: st.calls.append("giveup")
    return st


def test_do_restart_success_resets_state(engine, clock, restart):
    engine._heartbeat_task_started = True
    engine._startup_client_sync_started = True
    engine._startup_overlay_recovery_sent = True
    engine._template_control_emit_worker_started = True
    engine._last_gevent_heartbeat = 1.0
    engine._server_started_at = 1.0
    engine._restart_attempts = 2
    engine._last_run_error = "old"
    engine._do_restart("frozen", 2)
    assert restart.calls == ["stop", "prep", "start"]
    assert engine._heartbeat_task_started is False and engine._startup_client_sync_started is False
    assert engine._startup_overlay_recovery_sent is False
    assert engine._template_control_emit_worker_started is False
    assert engine._last_gevent_heartbeat is None and engine._server_started_at is None
    assert engine._restart_attempts == 0 and engine._last_run_error is None
    assert engine._restart_in_progress is False


def test_do_restart_foreign_holder_refunds_attempt(engine, clock, restart):
    restart.ports = [True]
    restart.probes = [False]
    engine._restart_attempts = 1
    engine._do_restart("x", 1)
    assert restart.calls == ["stop", "prep", "wait"]
    assert engine._restart_attempts == 0 and "foreign process" in engine._last_run_error


def test_do_restart_backoff_then_foreign(engine, clock, restart):
    # open+own -> backoff + prep, then open + foreign
    restart.ports = [True, True, True]
    restart.probes = [True, False]
    engine._restart_attempts = 4
    engine._do_restart("x", 3)
    assert clock.sleeps == [4.0]
    assert restart.calls == ["stop", "prep", "prep", "wait"]
    assert engine._restart_attempts == 3


def test_do_restart_port_still_in_use_skips_bind_and_gives_up(engine, clock, restart):
    restart.ports = [True]
    restart.probes = [True]
    restart.healthy = [False]
    engine._restart_attempts = engine._MAX_RESTART_ATTEMPTS
    engine._do_restart("x", 9)
    assert "start" not in restart.calls
    assert engine._last_run_error == "port 5996 still in use"
    assert ("conflict", "auto-restart") in restart.calls and "giveup" in restart.calls
    assert engine._restart_giveup_notified is True
    assert clock.sleeps[0] == 8.0  # backoff capped


def test_do_restart_bind_error_addr_in_use(engine, clock, restart):
    engine._start_server_thread = mock.Mock(side_effect=OSError(48, "Address already in use"))
    engine._restart_attempts = 2
    engine._do_restart("x", 1)
    assert restart.calls[-1] == "wait"
    assert engine._restart_attempts == 1 and "Address already in use" in engine._last_run_error


def test_do_restart_bind_error_other_then_not_recovered(engine, clock, restart):
    engine._start_server_thread = mock.Mock(side_effect=RuntimeError("thread limit"))
    restart.healthy = [False]
    engine._restart_attempts = 1
    engine._do_restart("x", 1)
    assert engine._last_run_error == "thread limit"
    assert "giveup" not in restart.calls and "wait" not in restart.calls
    assert clock.sleeps.count(1.0) == 15


def test_do_restart_not_recovered_with_foreign_holder(engine, clock, restart):
    restart.ports = [False, False, False, False, True]
    restart.probes = [False]
    restart.healthy = [False]
    engine._restart_attempts = 2
    engine._do_restart("x", 1)
    assert restart.calls[-2:] == ["hint", "wait"]
    assert engine._restart_attempts == 1


# --------------------------------------------------------------------------- request_restart / start / give up


def test_request_restart_gates(engine, clock, shutdown, fake_threads):
    engine._notify_restart_giveup = mock.Mock()
    shutdown.value = True
    engine.request_restart("x")
    assert fake_threads == []
    shutdown.value = False
    engine._restart_in_progress = True
    engine.request_restart("x")
    assert fake_threads == []
    engine._restart_in_progress = False
    engine.request_restart("hub frozen")
    assert len(fake_threads) == 1 and fake_threads[0].args == ("hub frozen", 1)
    assert fake_threads[0].name == "WebEngineRestart"
    engine.request_restart("again")  # cooldown
    assert len(fake_threads) == 1
    clock.t += engine._RESTART_COOLDOWN_SEC + 1
    engine._restart_attempts = engine._MAX_RESTART_ATTEMPTS
    engine.request_restart("x")
    engine.request_restart("x")
    engine._notify_restart_giveup.assert_called_once()
    assert len(fake_threads) == 1


def test_start_server_thread(engine, fake_threads):
    engine._port_is_open = lambda: True
    with pytest.raises(OSError, match="port 0.0.0.0:5996 is in use"):
        engine._start_server_thread()
    engine._port_is_open = lambda: False
    old = _Thread("old", alive=False)
    engine._known_server_threads = [old]
    engine._start_server_thread()
    t = fake_threads[0]
    assert t.name == "WebEngine" and t.target == engine.run and t.daemon is True
    assert engine.server_thread is t and engine._known_server_threads == [t]
    assert we.web_engine_instance is engine


def test_notify_restart_giveup(engine, monkeypatch):
    import modules.notification_engine as ne

    notify = mock.Mock()
    monkeypatch.setattr(ne, "notify", notify)
    engine._last_run_error = None
    engine._notify_restart_giveup()
    kw = notify.call_args[1]
    assert kw["details"] is None and kw["dedupe_key"] == "svc:webengine:problem" and kw["type"] == "error"
    engine._last_run_error = "bind"
    engine._notify_restart_giveup()
    assert notify.call_args[1]["details"] == "Last error: bind"
    notify.side_effect = RuntimeError
    engine._notify_restart_giveup()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/obs_service.py, part 1: helpers, public API, worker loop, connect/disconnect.

obsws-python clients are fakes; no socket is ever opened. Part 2 covers the snapshot,
browser-source operations and the connector-operation dispatcher.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import concurrent.futures  # noqa: E402
import logging  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import pytest  # noqa: E402

from modules import dataobjects, notification_engine, obs_browser_docks  # noqa: E402
from modules import obs_service as obs  # noqa: E402


@pytest.fixture(autouse=True)
def _no_dock_migrate(monkeypatch):
    calls: List[int] = []
    monkeypatch.setattr(obs_browser_docks, "schedule_dock_boot_migrate_if_obs_exits", lambda: calls.append(1))
    return calls


@pytest.fixture
def notes(monkeypatch):
    out: List[Dict[str, Any]] = []
    monkeypatch.setattr(notification_engine, "notify", lambda message, **kw: out.append({"message": message, **kw}))
    return out


@pytest.fixture
def obs_settings(monkeypatch):
    data = SimpleNamespace(enabled=True, host="localhost", port=4455, password="pw")
    fake_state = SimpleNamespace(get_obs_data=lambda: data)
    monkeypatch.setattr(dataobjects, "state_manager", fake_state)
    return data


@pytest.fixture
def fast_ticks(monkeypatch):
    monkeypatch.setattr(obs, "_IDLE_TICK_SEC", 0.001)
    monkeypatch.setattr(obs, "_IDLE_HEALTH_INTERVAL_SEC", 0.0)


def done_future(value=None, exc=None):
    fut: concurrent.futures.Future = concurrent.futures.Future()
    if exc is not None:
        fut.set_exception(exc)
    else:
        fut.set_result(value)
    return fut


class Rec:
    """Minimal fake ReqClient: records calls, returns configured values."""

    def __init__(self, **returns):
        self.calls: List[tuple] = []
        self.returns = returns

    def _call(self, name, *args, **kw):
        self.calls.append((name, args, kw))
        val = self.returns.get(name)
        if isinstance(val, BaseException):
            raise val
        if callable(val):
            return val(*args, **kw)
        return val

    def get_stream_status(self):
        return self._call("get_stream_status") or {"outputActive": False}

    def get_record_status(self):
        return self._call("get_record_status") or {"outputActive": False}

    def disconnect(self):
        return self._call("disconnect")


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "raw,expected",
    [(True, True), (False, False), ("1", True), (" YES ", True), ("on", True), ("true", True),
     ("0", False), ("no", False), (1, True), (0, False), (None, False)],
)
def test_truthy(raw, expected):
    assert obs._truthy(raw) is expected


@pytest.mark.parametrize(
    "val,expected",
    [(None, None), ("", None), (True, True), (3, 3), (2.5, 2.5), ("1.5", 1.5), (" 7 ", 7), ("abc", "abc")],
)
def test_coerce_numeric(val, expected):
    assert obs._coerce_numeric(val) == expected


def test_coerce_numeric_bad_float_text_returned():
    assert obs._coerce_numeric("1.2.3") == "1.2.3"


def test_attr_mapping_and_object():
    assert obs._attr({"sceneName": "A"}, "scene_name", "sceneName") == "A"
    assert obs._attr({"x": 1}, "scene_name") is None
    assert obs._attr(SimpleNamespace(scene_name="B"), "scene_name", "sceneName") == "B"
    assert obs._attr(SimpleNamespace(sceneName="C"), "scene_name", "sceneName") == "C"
    assert obs._attr(SimpleNamespace(), "scene_name") is None
    # str is not treated as a mapping
    assert obs._attr("text", "upper") is not None


def test_unique_obs_input_name():
    assert obs.unique_obs_input_name("Alerts", None) == "Alerts"
    assert obs.unique_obs_input_name("Alerts", ["Alerts", "Alerts 2", "", "  "]) == "Alerts 3"
    assert obs.unique_obs_input_name("  ", []) == "Mycelian"
    assert obs.unique_obs_input_name(None, ["Mycelian"]) == "Mycelian 2"


def test_unique_obs_input_name_exhausted():
    taken = ["X"] + [f"X {n}" for n in range(2, 10001)]
    with pytest.raises(RuntimeError, match='source named "X"'):
        obs.unique_obs_input_name("X", taken)


def test_adjusted_obs_input_name():
    assert obs.adjusted_obs_input_name("A", []) == ("A", "")
    assert obs.adjusted_obs_input_name("", ["Mycelian"]) == ("Mycelian 2", "")
    name, why = obs.adjusted_obs_input_name("A", ["A"])
    assert name == "A 2" and '"A"' in why and '"A 2"' in why


def test_browser_source_input_settings_defaults():
    s = obs.browser_source_input_settings(None, "800", 600.0, fps_custom=True)
    assert s == {
        "url": "", "width": 800, "height": 600, "css": "", "reroute_audio": False,
        "fps_custom": True, "shutdown": True, "restart_when_active": False, "fps": 60,
    }
    s2 = obs.browser_source_input_settings("u", 1, 1, fps_custom=True, fps="30")
    assert s2["fps"] == 30


def test_gather_transform_overrides():
    out = obs._gather_transform_overrides(
        {"position_x": "10", "scale_x": "1.5", "rotation": "", "crop_left": None,
         "bounds_type": "OBS_BOUNDS_NONE", "unknown": 5, "width": 100}
    )
    assert out == {"positionX": 10, "scaleX": 1.5, "boundsType": "OBS_BOUNDS_NONE", "width": 100}


class OBSSDKError(Exception):
    pass


class WebSocketTimeoutException(Exception):
    pass


class WebSocketBadStatusException(Exception):
    pass


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ConnectionRefusedError(), True),
        (BrokenPipeError(), True),
        (ConnectionResetError(), True),
        (TimeoutError(), True),
        (OBSSDKError("x"), True),
        (RuntimeError("authentication enabled but no password provided"), True),
        (WebSocketTimeoutException(), True),
        (WebSocketBadStatusException(), True),
        (ValueError("weird"), False),
    ],
)
def test_is_transient_connect_error(exc, expected):
    assert obs._is_transient_connect_error(exc) is expected


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("authentication enabled but no password provided", "missing"),
        ("Authentication Enabled", "missing"),
        ("failed to identify client with the server", "wrong"),
        ("connection refused", None),
    ],
)
def test_obs_auth_problem(msg, expected):
    assert obs._obs_auth_problem(Exception(msg)) == expected


# --------------------------------------------------------------------------- simple public API


def test_initial_state_and_status_lines():
    svc = obs.ObsServiceImpl()
    assert svc.get_connection_phase() == "disconnected"
    assert svc.get_status_line() == "Disconnected"
    assert svc.is_connected() is False
    assert svc.auth_failed() is False
    assert svc.connection_details() == (False, "", "")
    svc._set_phase("connecting")
    assert svc.get_status_line() == "Connecting"
    svc._set_phase("disconnecting")
    assert svc.get_status_line() == "Disconnecting"
    svc._set_phase("connected")
    assert svc.get_status_line() == "Connected"
    assert svc.is_connected() is False  # no client yet
    svc._req_client = object()
    svc._obs_version = "30.1"
    svc._websocket_version = "5.4"
    assert svc.get_status_line() == "30.1"
    assert svc.is_connected() is True
    assert svc.connection_details() == (True, "30.1", "5.4")
    svc._auth_notified_sig = ("h", 1, 2)
    assert svc.auth_failed() is True


def test_set_connector_loop():
    svc = obs.ObsServiceImpl()
    loop = object()
    svc.set_connector_loop(loop)
    assert svc._connector_loop is loop
    svc.set_connector_loop(None)
    assert svc._connector_loop is None


def test_is_enabled_reads_settings(obs_settings):
    svc = obs.ObsServiceImpl()
    assert svc.is_enabled() is True
    obs_settings.enabled = False
    assert svc.is_enabled() is False


def test_load_enabled_settings_error_is_false(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_obs_data=boom))
    assert obs.ObsServiceImpl()._load_enabled_settings() is False


def test_connection_kwargs_modes(obs_settings):
    svc = obs.ObsServiceImpl()
    assert svc._connection_kwargs("connect") == {"host": "localhost", "port": 4455, "password": "pw", "timeout": obs._CONNECT_TIMEOUT_SEC}
    assert svc._connection_kwargs("health")["timeout"] == obs._HEALTH_TIMEOUT_SEC
    assert svc._connection_kwargs()["timeout"] == obs._RPC_TIMEOUT_SEC
    obs_settings.host = "   "
    obs_settings.port = None
    obs_settings.password = None
    kw = svc._connection_kwargs()
    assert kw["host"] == "localhost" and kw["port"] == 4455 and kw["password"] == ""
    obs_settings.host = " 10.0.0.5 "
    obs_settings.port = "4460"
    kw = svc._connection_kwargs()
    assert kw["host"] == "10.0.0.5" and kw["port"] == 4460


def test_enqueue_obs_request_and_full_queue():
    svc = obs.ObsServiceImpl()
    fut = svc.enqueue_obs_request("start_stream", {"a": 1})
    item = svc._req_queue.get_nowait()
    assert item[0] is fut and item[1] == "start_stream" and item[2] == {"a": 1}
    svc.enqueue_obs_request("stop_stream")
    assert svc._req_queue.get_nowait()[2] == {}
    svc._req_queue = queue.Queue(maxsize=1)
    svc._req_queue.put_nowait((None, "x", {}))
    full = svc.enqueue_obs_request("start_stream")
    assert full.result(0) == (False, "OBS request queue is full", None)


def test_apply_settings_sets_wakeup_and_queues(caplog):
    svc = obs.ObsServiceImpl()
    svc.apply_settings()
    assert svc._reconnect_wakeup.is_set()
    assert svc._req_queue.get_nowait() == (None, "__reconnect__", {})
    svc._req_queue = queue.Queue(maxsize=1)
    svc._req_queue.put_nowait((None, "x", {}))
    with caplog.at_level(logging.WARNING, logger=obs.__name__):
        svc.apply_settings()
    assert "queue full" in caplog.text


def test_enqueue_refresh_snapshot():
    svc = obs.ObsServiceImpl()
    fut = svc.enqueue_refresh_snapshot()
    f, op, kw = svc._req_queue.get_nowait()
    assert f is fut and op == "__refresh_snapshot__" and kw == {}


@pytest.mark.parametrize(
    "fut,expected",
    [
        (done_future((True, None)), (True, "")),
        (done_future((False, "boom")), (False, "boom")),
        (done_future((False, "")), (False, "snapshot failed")),
        (done_future((False,)), (False, "snapshot failed")),
        (done_future("weird"), (False, "snapshot failed")),
        (done_future(exc=TimeoutError("late")), (False, "late")),
    ],
)
def test_refresh_snapshot_blocking(fut, expected):
    svc = obs.ObsServiceImpl()
    svc.enqueue_refresh_snapshot = lambda: fut
    assert svc.refresh_snapshot_blocking(0.01) == expected


def test_get_connector_snapshot_copies_and_filters():
    svc = obs.ObsServiceImpl()
    svc._snapshot.update(
        scene_names=["A"], input_names=["Mic"],
        sources_by_scene={"A": {"cam": "cam"}, "B": "junk"},
        stream_output_active=1, stream_output_state=None,
        record_output_active=0, record_output_state="REC", current_program_scene_name=None,
    )
    snap = svc.get_connector_snapshot()
    assert snap == {
        "scene_names": ["A"], "input_names": ["Mic"], "sources_by_scene": {"A": {"cam": "cam"}},
        "stream_output_active": True, "stream_output_state": "", "record_output_active": False,
        "record_output_state": "REC", "current_program_scene_name": "",
    }
    snap["scene_names"].append("X")
    snap["sources_by_scene"]["A"]["y"] = "y"
    assert svc._snapshot["scene_names"] == ["A"]
    assert svc._snapshot["sources_by_scene"]["A"] == {"cam": "cam"}
    svc._snapshot["sources_by_scene"] = None
    assert svc.get_connector_snapshot()["sources_by_scene"] == {}


def test_should_abort_connect():
    svc = obs.ObsServiceImpl()
    assert svc._should_abort_connect() is False
    svc._reconnect_wakeup.set()
    assert svc._should_abort_connect() is True
    svc._reconnect_wakeup.clear()
    svc._stop.set()
    assert svc._should_abort_connect() is True


def test_connect_executor_lifecycle():
    svc = obs.ObsServiceImpl()
    svc._replace_connect_executor()  # nothing to replace
    pool = svc._get_connect_executor()
    assert isinstance(pool, concurrent.futures.ThreadPoolExecutor)
    assert svc._get_connect_executor() is pool
    svc._replace_connect_executor()
    assert svc._connect_executor is None
    assert pool._shutdown is True


# --------------------------------------------------------------------------- start / stop


def test_start_and_stop_worker_thread():
    svc = obs.ObsServiceImpl()
    entered = threading.Event()

    def fake_main():
        entered.set()
        svc._stop.wait(2)

    svc._thread_main = fake_main
    svc.start()
    assert entered.wait(1)
    th = svc._thread
    assert th.name == "ObsWebSocket" and th.daemon
    svc.start()  # alive: same thread
    assert svc._thread is th
    pool = svc._get_connect_executor()
    svc._set_phase("connected")
    svc.stop()
    assert svc._thread is None
    assert not th.is_alive()
    assert svc._connect_executor is None and pool._shutdown
    assert svc.get_connection_phase() == "disconnected"
    assert svc._reconnect_wakeup.is_set()
    assert svc._req_queue.get_nowait() == (None, "__shutdown__", {})


def test_stop_without_thread_and_full_queue():
    svc = obs.ObsServiceImpl()
    svc._req_queue = queue.Queue(maxsize=1)
    svc._req_queue.put_nowait((None, "x", {}))
    svc.stop()
    assert svc._stop.is_set()
    assert svc.get_connection_phase() == "disconnected"


def test_stop_keeps_thread_reference_if_join_times_out():
    svc = obs.ObsServiceImpl()
    joined = []
    svc._thread = SimpleNamespace(join=lambda timeout: joined.append(timeout), is_alive=lambda: True)
    svc.stop()
    assert joined == [8.0]
    assert svc._thread is not None


def test_start_obs_service_quiets_loggers(monkeypatch):
    started = []
    monkeypatch.setattr(obs.obs_service, "start", lambda: started.append(1))
    names = ["obsws_python.baseclient.ObsClient", "obsws_python.reqs.ReqClient"]
    old = [logging.getLogger(n).level for n in names]
    try:
        obs.start_obs_service()
        assert started == [1]
        assert all(logging.getLogger(n).level == logging.CRITICAL for n in names)
    finally:
        for n, lvl in zip(names, old):
            logging.getLogger(n).setLevel(lvl)


# --------------------------------------------------------------------------- _thread_main


def _main_harness(enabled_seq, connect_effects, idle_results):
    """A service whose collaborators are scripted; returns (svc, log)."""
    svc = obs.ObsServiceImpl()
    log: List[Any] = []
    enabled = list(enabled_seq)
    connects = list(connect_effects)
    idles = list(idle_results)
    svc._disconnect_clients = lambda: log.append("disconnect")
    svc._load_enabled_settings = lambda: enabled.pop(0) if enabled else True

    def connect():
        eff = connects.pop(0) if connects else None
        log.append("connect")
        if isinstance(eff, BaseException):
            raise eff
        svc._req_client = eff

    svc._connect_blocking = connect

    def idle(wait):
        log.append(("idle", wait))
        res = idles.pop(0) if idles else "shutdown"
        return res

    svc._disconnect_idle_wait_or_drain_queue = idle

    def run_iter():
        log.append("run")
        svc._stop.set()

    svc._run_loop_iteration = run_iter
    return svc, log


def test_thread_main_disabled_waits_then_shuts_down():
    svc, log = _main_harness([False, False], [], ["done", "shutdown"])
    svc._retry_after_fail_sec = 30.0
    svc._thread_main()
    assert log == ["disconnect", ("idle", obs._DISABLED_POLL_INTERVAL_SEC), "disconnect",
                   ("idle", obs._DISABLED_POLL_INTERVAL_SEC)]
    assert svc._retry_after_fail_sec == obs._CONNECT_ATTEMPT_FIRST_WAIT_SEC


def test_thread_main_connect_failure_backoff():
    svc, log = _main_harness([True] * 4, [None, None, None, None], ["done", "wake", "done", "shutdown"])
    svc._thread_main()
    idles = [w for x in log if isinstance(x, tuple) for w in [x[1]]]
    assert idles == [5.0, 6.75, 6.75, 9.113]
    assert svc._retry_after_fail_sec == 9.113


def test_thread_main_backoff_caps():
    svc, log = _main_harness([True, True], [None, None], ["done", "shutdown"])
    svc._retry_after_fail_sec = 40.0
    svc._thread_main()
    assert svc._retry_after_fail_sec == obs._CONNECT_ATTEMPT_MAX_WAIT_SEC


def test_thread_main_connected_runs_loop_and_resets():
    client = object()
    svc, log = _main_harness([True], [client], [])
    svc._retry_after_fail_sec = 20.0
    svc._idle_health_failures = 3
    svc._last_idle_health_mono = 99.0
    svc._thread_main()
    assert log == ["disconnect", "connect", "run"]
    assert svc._retry_after_fail_sec == obs._CONNECT_ATTEMPT_FIRST_WAIT_SEC
    assert svc._idle_health_failures == 0 and svc._last_idle_health_mono == 0.0


def test_thread_main_error_cooldown(caplog):
    svc, log = _main_harness([True, True], [RuntimeError("kaboom"), RuntimeError("again")], ["done", "shutdown"])
    with caplog.at_level(logging.ERROR, logger=obs.__name__):
        svc._thread_main()
    assert ("idle", obs._THREAD_ERROR_COOLDOWN_SEC) in log
    assert log.count("connect") == 2
    assert "kaboom" in caplog.text


def test_thread_main_exits_immediately_when_stopped():
    svc, log = _main_harness([], [], [])
    svc._stop.set()
    svc._thread_main()
    assert log == []


# --------------------------------------------------------------------------- idle drain


def test_idle_drain_answers_every_queued_op():
    svc = obs.ObsServiceImpl()
    futs = {}
    for op in ["__refresh_snapshot__", "__browser_source_size__", "__refresh_mycelian_browser_sources__",
               "__create_browser_source__", "__scene_browser_urls__", "start_stream"]:
        futs[op] = svc.enqueue_obs_request(op)
        svc._req_queue.put_nowait((None, op, {}))  # no-future variants
    svc._req_queue.put_nowait((None, "__shutdown__", {}))
    assert svc._disconnect_idle_wait_or_drain_queue(5.0) == "shutdown"
    assert futs["__refresh_snapshot__"].result(0) == (False, "Not connected")
    for op in ["__browser_source_size__", "__refresh_mycelian_browser_sources__", "__create_browser_source__",
               "__scene_browser_urls__", "start_stream"]:
        assert futs[op].result(0) == (False, "OBS is not connected", None)


def test_idle_drain_reconnect_item_wakes():
    svc = obs.ObsServiceImpl()
    svc._retry_after_fail_sec = 30
    svc._req_queue.put_nowait((None, "__reconnect__", {}))
    svc._reconnect_wakeup.clear()
    assert svc._disconnect_idle_wait_or_drain_queue(5.0) == "wake"
    assert svc._retry_after_fail_sec == obs._CONNECT_ATTEMPT_FIRST_WAIT_SEC


def test_idle_drain_wakeup_flag():
    svc = obs.ObsServiceImpl()
    svc._retry_after_fail_sec = 30
    svc._reconnect_wakeup.set()
    assert svc._disconnect_idle_wait_or_drain_queue(5.0) == "wake"
    assert not svc._reconnect_wakeup.is_set()
    assert svc._retry_after_fail_sec == obs._CONNECT_ATTEMPT_FIRST_WAIT_SEC


def test_idle_drain_times_out_done():
    svc = obs.ObsServiceImpl()
    assert svc._disconnect_idle_wait_or_drain_queue(0.03) == "done"
    assert svc._disconnect_idle_wait_or_drain_queue(-1) == "done"


def test_idle_drain_stopped_is_shutdown():
    svc = obs.ObsServiceImpl()
    svc._stop.set()
    assert svc._disconnect_idle_wait_or_drain_queue(5.0) == "shutdown"


# --------------------------------------------------------------------------- run loop


def _loop_service(client=None):
    svc = obs.ObsServiceImpl()
    svc._req_client = client
    svc.disconnects = 0

    def disc():
        svc.disconnects += 1
        svc._req_client = None

    svc._disconnect_clients = disc
    svc._apply_ws_timeout = lambda cl, t: None
    return svc


def test_run_loop_shutdown_disconnects():
    svc = _loop_service(Rec())
    svc._req_queue.put_nowait((None, "__shutdown__", {}))
    svc._run_loop_iteration()
    assert svc.disconnects == 1


def test_run_loop_reconnect_returns_without_disconnect():
    svc = _loop_service(Rec())
    svc._req_queue.put_nowait((None, "__reconnect__", {}))
    svc._run_loop_iteration()
    assert svc.disconnects == 0


def test_run_loop_stop_flag_exits():
    svc = _loop_service(Rec())
    svc._stop.set()
    svc._run_loop_iteration()
    assert svc.disconnects == 1


def test_run_loop_special_ops_with_client():
    svc = _loop_service(Rec())
    snaps = []
    svc._refresh_snapshot_locked = lambda: snaps.append(1)
    svc._lookup_browser_source_size_locked = lambda route, port: {"route": route, "port": port}
    svc._refresh_mycelian_browser_sources_locked = lambda port, routes: {"port": port, "routes": routes}
    svc._create_browser_source_locked = lambda kw: {"kw": kw}
    svc._scene_browser_urls_locked = lambda: {"urls": 1}
    svc._dispatch_connector_operation = lambda op, kw: snaps.append((op, kw))
    f1 = svc.enqueue_obs_request("__refresh_snapshot__")
    f2 = svc.enqueue_obs_request("__browser_source_size__", {"route": None, "port": 5})
    f3 = svc.enqueue_obs_request("__refresh_mycelian_browser_sources__", {"port": 1, "template_routes": None})
    f4 = svc.enqueue_obs_request("__create_browser_source__", {"a": 1})
    f5 = svc.enqueue_obs_request("__scene_browser_urls__")
    f6 = svc.enqueue_obs_request("toggle_record", {"x": 1})
    f7 = svc.enqueue_obs_request("nope")
    for op in ["__refresh_snapshot__", "__browser_source_size__", "__refresh_mycelian_browser_sources__",
               "__create_browser_source__", "__scene_browser_urls__", "start_stream"]:
        svc._req_queue.put_nowait((None, op, {}))
    svc._req_queue.put_nowait((None, "__shutdown__", {}))
    svc._run_loop_iteration()
    assert f1.result(0) == (True, None)
    assert f2.result(0) == (True, None, {"route": "", "port": 5})
    assert f3.result(0) == (True, None, {"port": 1, "routes": []})
    assert f4.result(0) == (True, None, {"kw": {"a": 1}})
    assert f5.result(0) == (True, None, {"urls": 1})
    assert f6.result(0) == (True, None, None)
    ok, msg, _ = f7.result(0)
    assert ok is False and "Unknown OBS connector operation 'nope'" in msg
    assert snaps.count(1) == 2
    assert ("toggle_record", {"x": 1}) in snaps


def test_run_loop_special_ops_errors():
    svc = _loop_service(Rec())

    def boom(*a, **k):
        raise RuntimeError("bad")

    svc._lookup_browser_source_size_locked = boom
    svc._refresh_mycelian_browser_sources_locked = boom
    svc._create_browser_source_locked = boom
    svc._scene_browser_urls_locked = boom
    svc._dispatch_connector_operation = boom
    ops = ["__browser_source_size__", "__refresh_mycelian_browser_sources__", "__create_browser_source__",
           "__scene_browser_urls__", "start_stream"]
    futs = [svc.enqueue_obs_request(op) for op in ops]
    for op in ops:
        svc._req_queue.put_nowait((None, op, {}))
    svc._req_queue.put_nowait((None, "__shutdown__", {}))
    svc._run_loop_iteration()
    assert [f.result(0) for f in futs] == [(False, "bad", None)] * 5


def test_run_loop_special_ops_without_client():
    svc = _loop_service(None)
    ops = ["__refresh_snapshot__", "__browser_source_size__", "__refresh_mycelian_browser_sources__",
           "__create_browser_source__", "__scene_browser_urls__"]
    futs = [svc.enqueue_obs_request(op) for op in ops]
    for op in ops:
        svc._req_queue.put_nowait((None, op, {}))
    svc._req_queue.put_nowait((None, "__shutdown__", {}))
    svc._run_loop_iteration()
    assert futs[0].result(0) == (False, "Not connected")
    assert [f.result(0) for f in futs[1:]] == [(False, "Not connected", None)] * 4


def test_run_loop_idle_wakeup_returns(fast_ticks):
    svc = _loop_service(Rec())
    svc._reconnect_wakeup.set()
    svc._run_loop_iteration()
    assert not svc._reconnect_wakeup.is_set()
    assert svc.disconnects == 0


def test_run_loop_idle_health_failures_drop_socket(fast_ticks):
    client = Rec(get_stream_status=ConnectionError("dead"))
    svc = _loop_service(client)
    svc._run_loop_iteration()
    assert svc.disconnects == 1
    assert svc._idle_health_failures == obs._IDLE_HEALTH_DROP_AFTER_FAILS
    assert [c[0] for c in client.calls].count("get_stream_status") == obs._IDLE_HEALTH_DROP_AFTER_FAILS


def test_run_loop_idle_health_success_resets_and_event_worker_death(fast_ticks):
    client = Rec(get_stream_status={"outputActive": True, "outputState": "STARTED"})
    svc = _loop_service(client)
    svc._idle_health_failures = 2
    ticks = {"n": 0}

    def is_alive():
        ticks["n"] += 1
        return ticks["n"] < 3

    svc._ev_client = SimpleNamespace(worker=SimpleNamespace(is_alive=is_alive))
    svc._run_loop_iteration()
    assert svc.disconnects == 1
    assert svc._idle_health_failures == 0
    assert svc._snapshot["stream_output_active"] is True
    assert svc._snapshot["stream_output_state"] == "STARTED"


def test_run_loop_idle_respects_health_interval(monkeypatch):
    monkeypatch.setattr(obs, "_IDLE_TICK_SEC", 0.001)
    monkeypatch.setattr(obs, "_IDLE_HEALTH_INTERVAL_SEC", 1000.0)
    client = Rec()
    svc = _loop_service(client)
    svc._last_idle_health_mono = obs.time.monotonic()
    svc._ev_client = SimpleNamespace(worker=None)
    timer = threading.Timer(0.05, svc._reconnect_wakeup.set)
    timer.start()
    svc._run_loop_iteration()
    timer.join()
    assert client.calls == []


# --------------------------------------------------------------------------- connect pieces


class FakeVersion:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_establish_req_session_snake_and_camel(monkeypatch):
    made = []

    class FakeReq:
        def __init__(self, **kw):
            made.append(kw)

        def get_version(self):
            return resp

    import obsws_python

    monkeypatch.setattr(obsws_python, "ReqClient", FakeReq)
    resp = FakeVersion(obs_version="30.2", obs_web_socket_version="5.5")
    rc, plug, ws = obs.ObsServiceImpl._establish_req_session({"host": "h", "port": 1})
    assert isinstance(rc, FakeReq) and (plug, ws) == ("30.2", "5.5")
    assert made == [{"host": "h", "port": 1}]
    resp = FakeVersion(obsVersion="29", obsWebSocketVersion="5.0")
    assert obs.ObsServiceImpl._establish_req_session({})[1:] == ("29", "5.0")
    resp = FakeVersion(obs_version=None)
    assert obs.ObsServiceImpl._establish_req_session({})[1:] == ("", "")


class SyncPool:
    """Executor stand-in: runs the callable at submit time."""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append((fn, args))
        if self.exc is not None:
            return done_future(exc=self.exc)
        if self.result is not None:
            return done_future(self.result)
        try:
            return done_future(fn(*args))
        except Exception as e:  # pragma: no cover - surfaced through the future
            return done_future(exc=e)

    def shutdown(self, wait=False, cancel_futures=False):
        self.shut = True


def test_run_connect_with_timeout_success():
    svc = obs.ObsServiceImpl()
    pool = SyncPool()
    svc._connect_executor = pool
    svc._establish_req_session = lambda kw: ("rc", "30", "5")
    assert svc._run_connect_with_timeout({"host": "h"}) == ("rc", "30", "5")
    assert pool.submitted[0][1] == ({"host": "h"},)


def test_run_connect_with_timeout_timeout_replaces_pool():
    svc = obs.ObsServiceImpl()
    pool = SyncPool(exc=concurrent.futures.TimeoutError())
    svc._connect_executor = pool
    assert svc._run_connect_with_timeout({}) is None
    assert svc._connect_executor is None and pool.shut


def test_run_connect_with_timeout_auth_failure_notifies(notes):
    svc = obs.ObsServiceImpl()
    svc._connect_executor = SyncPool(exc=RuntimeError("failed to identify client"))
    assert svc._run_connect_with_timeout({"host": "h", "port": 4455, "password": "x"}) is None
    assert svc.auth_failed() is True
    assert notes[-1]["title"] == "OBS rejected the WebSocket password"


@pytest.mark.parametrize("exc", [ConnectionRefusedError("off"), ValueError("other")])
def test_run_connect_with_timeout_other_failures(notes, exc):
    svc = obs.ObsServiceImpl()
    svc._connect_executor = SyncPool(exc=exc)
    assert svc._run_connect_with_timeout({}) is None
    assert notes == [] and svc.auth_failed() is False


def test_notify_auth_failed_once_per_signature(notes):
    svc = obs.ObsServiceImpl()
    kw = {"host": "h", "port": 4455, "password": ""}
    svc._notify_auth_failed("missing", kw, RuntimeError("no password provided"))
    svc._notify_auth_failed("missing", kw, RuntimeError("again"))
    assert len(notes) == 1
    n = notes[0]
    assert n["title"] == "OBS needs a WebSocket password"
    assert n["type"] == "error" and n["source"] == "obs"
    assert n["dedupe_key"] == "obs:auth_failed"
    assert n["details"] == "h:4455: RuntimeError: no password provided"
    assert n["actions"] == notification_engine.nav_actions_settings("OBS")
    svc._notify_auth_failed("wrong", dict(kw, password="new"), RuntimeError("x"))
    assert len(notes) == 2 and notes[1]["title"] == "OBS rejected the WebSocket password"


def test_notify_auth_failed_swallows_notify_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ui")

    monkeypatch.setattr(notification_engine, "notify", boom)
    svc = obs.ObsServiceImpl()
    svc._notify_auth_failed("wrong", {}, RuntimeError("x"))
    assert svc._auth_notified_sig == ("", 0, hash(""))


def _patch_event_client(monkeypatch, factory):
    import obsws_python

    monkeypatch.setattr(obsws_python, "EventClient", factory)


def test_connect_event_client_success(monkeypatch):
    made = []

    def factory(**kw):
        made.append(kw)
        return SimpleNamespace(callback=None)

    _patch_event_client(monkeypatch, factory)
    svc = obs.ObsServiceImpl()
    svc._connect_executor = SyncPool()
    registered = []
    svc._register_callbacks = lambda: registered.append(svc._ev_client)
    svc._connect_event_client({"host": "h", "timeout": 99})
    from obsws_python.subs import Subs

    assert made == [{"host": "h", "timeout": obs._CONNECT_TIMEOUT_SEC, "subs": Subs.LOW_VOLUME}]
    assert svc._ev_client is not None and registered == [svc._ev_client]


def test_connect_event_client_aborted_closes(monkeypatch):
    ev = SimpleNamespace(callback=None)
    _patch_event_client(monkeypatch, lambda **kw: ev)
    svc = obs.ObsServiceImpl()
    svc._connect_executor = SyncPool()
    svc._reconnect_wakeup.set()
    closed = []
    svc._disconnect_event_client_bounded = lambda e: closed.append(e)
    svc._connect_event_client({})
    assert closed == [ev] and svc._ev_client is None


def test_connect_event_client_timeout(monkeypatch, notes):
    svc = obs.ObsServiceImpl()
    pool = SyncPool(exc=concurrent.futures.TimeoutError())
    svc._connect_executor = pool
    svc._connect_event_client({})
    assert svc._connect_executor is None
    assert notes[-1]["details"] == "Timed out opening the OBS event connection."
    assert notes[-1]["dedupe_key"] == "obs:event_client"
    assert notes[-1]["actions"] == notification_engine.nav_actions_main_tab("Connectors")


def test_connect_event_client_error(monkeypatch, notes):
    svc = obs.ObsServiceImpl()
    svc._connect_executor = SyncPool(exc=OSError("refused"))
    svc._connect_event_client({})
    assert notes[-1]["details"] == "OSError: refused"
    assert notes[-1]["type"] == "warning"


def test_notify_events_unavailable_swallows(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(notification_engine, "notify", boom)
    obs.ObsServiceImpl._notify_events_unavailable("d")


def _connect_harness(obs_settings, session):
    svc = obs.ObsServiceImpl()
    log: List[Any] = []
    svc._disconnect_clients = lambda: log.append("disconnect")
    svc._run_connect_with_timeout = lambda kw: (log.append(("connect", kw["timeout"])), session)[1]
    svc._apply_ws_timeout = lambda cl, t: log.append(("timeout", t))
    svc._update_output_flags_only = lambda use_health_timeout=False: log.append(("flags", use_health_timeout))
    svc._connect_event_client = lambda kw: log.append("events")
    return svc, log


def test_connect_blocking_aborts_early(obs_settings):
    svc, log = _connect_harness(obs_settings, None)
    svc._stop.set()
    svc._connect_blocking()
    assert log == [] and svc.get_connection_phase() == "disconnected"


def test_connect_blocking_aborts_after_disconnect(obs_settings):
    svc, log = _connect_harness(obs_settings, None)
    svc._disconnect_clients = lambda: (log.append("disconnect"), svc._reconnect_wakeup.set())
    svc._connect_blocking()
    assert log == ["disconnect"]
    assert svc.get_connection_phase() == "connecting"


def test_connect_blocking_no_session(obs_settings):
    svc, log = _connect_harness(obs_settings, None)
    svc._connect_blocking()
    assert log == ["disconnect", ("connect", obs._CONNECT_TIMEOUT_SEC), "disconnect"]
    assert svc._last_connect_attempt > 0
    assert svc._req_client is None


def test_connect_blocking_aborted_after_session(obs_settings):
    rc = Rec(disconnect=RuntimeError("already closed"))
    svc, log = _connect_harness(obs_settings, (rc, "30", "5"))

    def connect(kw):
        svc._reconnect_wakeup.set()
        return (rc, "30", "5")

    svc._run_connect_with_timeout = connect
    svc._connect_blocking()
    assert rc.calls[0][0] == "disconnect"
    assert svc._req_client is None
    assert log == ["disconnect", "disconnect"]


def test_connect_blocking_success(obs_settings):
    rc = Rec()
    svc, log = _connect_harness(obs_settings, (rc, "30.1", "5.4"))
    svc._auth_notified_sig = ("h", 1, 1)
    svc._connect_blocking()
    assert svc._req_client is rc
    assert svc.get_connection_phase() == "connected"
    assert svc._obs_version == "30.1" and svc._websocket_version == "5.4"
    assert svc._auth_notified_sig is None
    assert ("timeout", obs._RPC_TIMEOUT_SEC) in log
    assert ("flags", True) in log
    assert log[-1] == "events"
    assert svc._req_queue.get_nowait() == (None, "__refresh_snapshot__", {})


def test_connect_blocking_success_tolerates_flag_error_full_queue_and_abort(obs_settings):
    rc = Rec()
    svc, log = _connect_harness(obs_settings, (rc, "", ""))

    def flags(use_health_timeout=False):
        svc._reconnect_wakeup.set()  # abort before the event client
        raise RuntimeError("flaky")

    svc._update_output_flags_only = flags
    svc._req_queue = queue.Queue(maxsize=1)
    svc._req_queue.put_nowait((None, "x", {}))
    svc._connect_blocking()
    assert svc._req_client is rc
    assert "events" not in log


# --------------------------------------------------------------------------- disconnect


class _Worker:
    def __init__(self, alive=True):
        self.alive = alive
        self.joined = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


def test_disconnect_event_client_bounded_full():
    cleared = []
    ws_closed = []
    worker = _Worker()
    ev = SimpleNamespace(
        callback=SimpleNamespace(clear=lambda: cleared.append(1)),
        base_client=SimpleNamespace(ws=SimpleNamespace(close=lambda: ws_closed.append(1))),
        worker=worker,
    )
    obs.ObsServiceImpl()._disconnect_event_client_bounded(ev)
    assert cleared == [1] and ws_closed == [1]
    assert worker.joined == [obs._EVENT_DISCONNECT_JOIN_TIMEOUT_SEC]


def test_disconnect_event_client_bounded_errors_swallowed():
    def boom():
        raise RuntimeError("x")

    worker = _Worker(alive=False)
    ev = SimpleNamespace(callback=SimpleNamespace(clear=boom), base_client=SimpleNamespace(ws=SimpleNamespace(close=boom)),
                         worker=worker)
    obs.ObsServiceImpl()._disconnect_event_client_bounded(ev)
    assert worker.joined == []

    class BadCallback:
        @property
        def callback(self):
            raise RuntimeError("bad attr")

    obs.ObsServiceImpl()._disconnect_event_client_bounded(BadCallback())
    obs.ObsServiceImpl()._disconnect_event_client_bounded(SimpleNamespace(base_client=None))
    obs.ObsServiceImpl()._disconnect_event_client_bounded(SimpleNamespace(base_client=SimpleNamespace()))


def test_disconnect_clients_closes_both(_no_dock_migrate):
    svc = obs.ObsServiceImpl()
    rc = Rec()
    ev = object()
    svc._req_client, svc._ev_client = rc, ev
    seen = []
    svc._disconnect_event_client_bounded = lambda e: seen.append(e)
    svc._set_phase("connected")
    svc._disconnect_clients()
    assert seen == [ev] and rc.calls[0][0] == "disconnect"
    assert svc._req_client is None and svc._ev_client is None
    assert svc.get_connection_phase() == "disconnected"
    assert _no_dock_migrate == [1]


def test_disconnect_clients_swallows_errors(monkeypatch):
    svc = obs.ObsServiceImpl()
    svc._req_client = Rec(disconnect=RuntimeError("x"))
    svc._ev_client = object()

    def boom(_e):
        raise RuntimeError("y")

    svc._disconnect_event_client_bounded = boom

    def dock_boom():
        raise RuntimeError("z")

    monkeypatch.setattr(obs_browser_docks, "schedule_dock_boot_migrate_if_obs_exits", dock_boom)
    svc._disconnect_clients()
    assert svc.get_connection_phase() == "disconnected"


def test_apply_ws_timeout():
    svc = obs.ObsServiceImpl()
    svc._apply_ws_timeout(SimpleNamespace(), 3.0)  # no base: nothing
    base = SimpleNamespace(ws=None, timeout=0)
    svc._apply_ws_timeout(SimpleNamespace(base_client=base), 3.0)
    assert base.timeout == 3.0
    got = []
    base2 = SimpleNamespace(ws=SimpleNamespace(settimeout=lambda t: got.append(t)), timeout=0)
    svc._apply_ws_timeout(SimpleNamespace(base_client=base2), 2.0)
    assert got == [2.0] and base2.timeout == 2.0

    def boom(_t):
        raise OSError("closed")

    base3 = SimpleNamespace(ws=SimpleNamespace(settimeout=boom), timeout=0)
    svc._apply_ws_timeout(SimpleNamespace(base_client=base3), 1.0)
    assert base3.timeout == 1.0


# --------------------------------------------------------------------------- events


def test_enqueue_connector_event_needs_running_loop(monkeypatch):
    called = []
    monkeypatch.setattr(obs.asyncio, "run_coroutine_threadsafe", lambda *a: called.append(a))
    svc = obs.ObsServiceImpl()
    svc._enqueue_connector_event({"x": 1})
    svc._connector_loop = SimpleNamespace(is_running=lambda: False)
    svc._enqueue_connector_event({"x": 1})
    assert called == []


def test_enqueue_connector_event_dispatches(monkeypatch):
    from modules import connector_integration

    added = []
    manager = SimpleNamespace(add_event=lambda ev: ("coro", ev))
    monkeypatch.setattr(connector_integration, "get_integration", lambda: SimpleNamespace(manager=manager))
    monkeypatch.setattr(obs.asyncio, "run_coroutine_threadsafe", lambda coro, loop: added.append((coro, loop)))
    svc = obs.ObsServiceImpl()
    loop = SimpleNamespace(is_running=lambda: True)
    svc._connector_loop = loop
    svc._enqueue_connector_event({"event_type": "x"})
    assert added == [(("coro", {"event_type": "x"}), loop)]


def test_enqueue_connector_event_swallows_errors(monkeypatch):
    from modules import connector_integration

    def boom():
        raise RuntimeError("not ready")

    monkeypatch.setattr(connector_integration, "get_integration", boom)
    svc = obs.ObsServiceImpl()
    svc._connector_loop = SimpleNamespace(is_running=lambda: True)
    svc._enqueue_connector_event({})


def test_register_callbacks_without_callback():
    svc = obs.ObsServiceImpl()
    svc._ev_client = SimpleNamespace()
    svc._register_callbacks()
    svc._ev_client = None
    svc._register_callbacks()


def test_register_callbacks_handlers(monkeypatch):
    monkeypatch.setattr(obs.time, "time", lambda: 123.0)
    registered = []
    svc = obs.ObsServiceImpl()
    svc._ev_client = SimpleNamespace(callback=SimpleNamespace(register=lambda fns: registered.extend(fns)))
    events = []
    svc._enqueue_connector_event = lambda e: events.append(e)
    svc._register_callbacks()
    names = [f.__name__ for f in registered]
    assert names == ["on_current_program_scene_changed", "on_stream_state_changed",
                     "on_record_state_changed", "on_input_mute_state_changed"]
    scene, stream, record, mute = registered

    scene(SimpleNamespace(scene_name="Main", previous_scene_name="Intro"))
    scene({"sceneName": None})
    stream({"outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"})
    assert svc._snapshot["stream_output_active"] is True
    stream(SimpleNamespace(output_active=False, output_state=None, state="stopped"))
    assert svc._snapshot["stream_output_state"] == "stopped"
    record({"outputActive": 1, "state": "rec"})
    assert svc._snapshot["record_output_active"] is True and svc._snapshot["record_output_state"] == "rec"
    record({})
    assert svc._snapshot["record_output_active"] is False and svc._snapshot["record_output_state"] == ""
    mute({"inputName": "Mic", "inputMuted": True})
    mute(SimpleNamespace(input_name=None, input_muted=None))

    assert events[0] == {"event_type": "obs_scene_changed", "source": "obs", "timestamp": 123.0,
                         "scene_name": "Main", "previous_scene_name": "Intro", "username": ""}
    assert events[1]["scene_name"] == "" and events[1]["previous_scene_name"] == ""
    assert events[2] == {"event_type": "obs_stream_state", "source": "obs", "timestamp": 123.0,
                         "output_active": True, "output_state": "OBS_WEBSOCKET_OUTPUT_STARTED", "username": ""}
    assert events[3]["output_state"] == "stopped"
    assert events[4]["event_type"] == "obs_record_state" and events[4]["output_active"] is True
    assert events[6] == {"event_type": "obs_input_mute", "source": "obs", "timestamp": 123.0,
                         "input_name": "Mic", "input_muted": True, "username": ""}
    assert events[7]["input_name"] == "" and events[7]["input_muted"] is False

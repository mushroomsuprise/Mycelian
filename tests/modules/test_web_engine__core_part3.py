# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""WebEngine methods (modules/web_engine.py, before ``register_routes``): config caches,
assets-watch registry, Twitch API worker, socket/overlay client tracking, bottleneck
metrics, overload hysteresis and get_health.

Time is driven by a fake clock swapped in for ``web_engine.time``; nothing binds a port.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import types
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine as we  # noqa: E402


class Clock:
    """Stand-in for the ``time`` module inside web_engine."""

    def __init__(self, t=10_000.0):
        self.t = t
        self.sleeps = []

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture(scope="module", autouse=True)
def _collect_engines():
    """Each WebEngine is a large reference cycle (routes close over self); free them
    when this file is done so a later collection pause doesn't land in other tests."""
    yield
    import gc

    gc.collect()


@pytest.fixture
def engine(tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    eng = we.WebEngine(template_dir=str(tdir), port=5997)
    eng._maintenance_hooks_last_run = float("inf")
    return eng


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(we, "time", c)
    return c


class FakeParser:
    def __init__(self, configs=None, raise_for=(), hidden=(), paths=None):
        self.configs = configs or {}
        self.raise_for = set(raise_for)
        self.hidden = set(hidden)
        self.paths = paths or {}

    def get_config_files(self):
        return list(self.configs)

    def load_config(self, name, **kw):
        if name in self.raise_for:
            raise RuntimeError("bad json")
        return json.loads(json.dumps(self.configs[name]))

    def get_config_path(self, name):
        if name not in self.paths:
            raise ValueError("no path")
        return self.paths[name]

    def is_config_hidden(self, name):
        return name in self.hidden


# --------------------------------------------------------------------------- all-template-configs cache


def test_build_all_template_configs_merges_previews_and_skips_bad(engine):
    engine.template_config_parser = FakeParser({"a": {"x": 1}, "b": {}, "c": {}}, raise_for={"b"})
    merged = []
    engine._merge_preview_session_into_config = lambda tok, n, cfg: merged.append((tok, n)) or cfg.update(m=1)
    out = engine._build_all_template_configs("tok")
    assert out == {"a": {"x": 1, "m": 1}, "c": {"m": 1}}
    assert merged == [("tok", "a"), ("tok", "c")]


def test_yield_sleep_prefers_socketio_and_falls_back(engine, clock):
    engine.socketio.sleep = mock.Mock()
    engine._yield_sleep(0.05)
    engine.socketio.sleep.assert_called_once_with(0.05)
    assert clock.sleeps == []
    engine.socketio.sleep.side_effect = RuntimeError("no hub")
    engine._yield_sleep(0.05)
    assert clock.sleeps == [0.05]


def test_cache_key_and_preview_rev(engine):
    assert engine._template_configs_cache_key("") == ""
    assert engine._template_configs_cache_key(None) == ""
    engine._preview_sessions_rev = 3
    assert engine._template_configs_cache_key("tok") == "tok#3"
    engine._bump_preview_sessions_rev()
    assert engine._preview_sessions_rev == 4
    del engine._preview_sessions_rev
    engine._bump_preview_sessions_rev()
    assert engine._preview_sessions_rev == 1


def test_template_configs_fingerprint(engine, tmp_path):
    f = tmp_path / "a.json"
    f.write_text("{}")
    engine.template_config_parser = FakeParser({"a": {}, "b": {}, "c": {}},
                                               paths={"a": str(f), "c": str(tmp_path / "gone.json")})
    fp = engine._template_configs_fingerprint()
    assert fp == (("a", os.stat(f).st_mtime), ("b", 0.0), ("c", 0.0))


def _set_all_cache(engine, key="", expires=None, payload=b"P", gen=None, fp=(), verified=None, now=10_000.0):
    engine._all_template_configs_cache = (
        key, now + 100 if expires is None else expires, payload,
        engine._template_config_generation if gen is None else gen, fp,
        now if verified is None else verified,
    )


def test_peek_all_cache_misses(engine, clock):
    assert engine._peek_all_template_configs_cache("") is None
    _set_all_cache(engine, key="other")
    assert engine._peek_all_template_configs_cache("") is None
    _set_all_cache(engine, expires=clock.t - 1)
    assert engine._peek_all_template_configs_cache("") is None
    _set_all_cache(engine, gen=-1)
    assert engine._peek_all_template_configs_cache("") is None


def test_peek_all_cache_fresh_and_reverified(engine, clock):
    _set_all_cache(engine)
    assert engine._peek_all_template_configs_cache("") == b"P"
    # Past the verify window: unchanged fingerprint renews verified_at
    engine._template_configs_fingerprint = lambda: ("fp",)
    _set_all_cache(engine, fp=("fp",), verified=clock.t - 10)
    assert engine._peek_all_template_configs_cache("") == b"P"
    assert engine._all_template_configs_cache[5] == clock.t
    # Changed fingerprint -> miss
    _set_all_cache(engine, fp=("old",), verified=clock.t - 10)
    assert engine._peek_all_template_configs_cache("") is None
    # Fingerprint failure -> miss
    engine._template_configs_fingerprint = mock.Mock(side_effect=OSError)
    _set_all_cache(engine, fp=("fp",), verified=clock.t - 10)
    assert engine._peek_all_template_configs_cache("") is None


def test_peek_all_cache_does_not_overwrite_newer_entry(engine, clock):
    _set_all_cache(engine, fp=("fp",), verified=clock.t - 10)

    def fp_and_replace():
        engine._all_template_configs_cache = ("", clock.t + 5, b"NEW", engine._template_config_generation, (), clock.t)
        return ("fp",)

    engine._template_configs_fingerprint = fp_and_replace
    assert engine._peek_all_template_configs_cache("") == b"P"
    assert engine._all_template_configs_cache[2] == b"NEW"


def test_get_all_cached_builds_then_hits(engine, clock):
    engine._build_all_template_configs = mock.Mock(return_value={"a": {"v": 1}})
    engine._template_configs_fingerprint = lambda: ("fp",)
    payload, hit = engine._get_all_template_configs_cached("")
    assert (json.loads(payload), hit) == ({"a": {"v": 1}}, False)
    assert engine._all_template_configs_building is False
    key, expires, body, gen, fp, verified = engine._all_template_configs_cache
    assert expires == clock.t + engine._ALL_TEMPLATE_CONFIGS_CACHE_TTL and fp == ("fp",)
    assert engine._get_all_template_configs_cached("") == (payload, True)
    engine._build_all_template_configs.assert_called_once_with(None)


def test_get_all_cached_fingerprint_failure_and_invalidate_race(engine, clock):
    engine._template_configs_fingerprint = mock.Mock(side_effect=OSError)

    def build(tok):
        engine._template_config_generation += 1  # invalidated mid-build
        return {}

    engine._build_all_template_configs = build
    payload, hit = engine._get_all_template_configs_cached("tok")
    assert payload == b"{}" and hit is False
    assert engine._all_template_configs_cache is None


def test_get_all_cached_waits_for_inflight_builder(engine, clock):
    engine._all_template_configs_building = True
    peeks = iter([None, None, b"BUILT"])
    engine._peek_all_template_configs_cache = lambda tok: next(peeks)
    engine._yield_sleep = clock.sleep
    assert engine._get_all_template_configs_cached("") == (b"BUILT", True)
    assert clock.sleeps == [engine._SINGLEFLIGHT_POLL_SEC] * 2


def test_get_all_cached_duplicate_build_after_deadline(engine, clock):
    engine._all_template_configs_building = True
    engine._peek_all_template_configs_cache = lambda tok: None
    engine._yield_sleep = clock.sleep
    engine._build_all_template_configs = lambda tok: {"dup": 1}
    engine._template_configs_fingerprint = lambda: ()
    payload, hit = engine._get_all_template_configs_cached("")
    assert json.loads(payload) == {"dup": 1} and hit is False
    assert sum(clock.sleeps) >= engine._SINGLEFLIGHT_WAIT_SEC
    assert engine._all_template_configs_building is False


# --------------------------------------------------------------------------- queue metadata cache


def test_queue_metadata_cache(engine, clock, monkeypatch):
    import modules.template_config_parser as tcp

    build = mock.Mock(return_value={"alerts": {"duration": 5}})
    monkeypatch.setattr(tcp, "build_template_queue_metadata", build)
    payload, hit = engine._get_template_queue_metadata_cached()
    assert json.loads(payload) == {"alerts": {"duration": 5}} and hit is False
    assert engine._get_template_queue_metadata_cached() == (payload, True)
    clock.t += engine._TEMPLATE_QUEUE_METADATA_CACHE_TTL + 1
    assert engine._get_template_queue_metadata_cached()[1] is False
    assert build.call_count == 2
    engine.invalidate_template_queue_metadata_cache()
    assert engine._template_queue_metadata_cache is None


def test_queue_metadata_waits_then_hits_or_duplicates(engine, clock, monkeypatch):
    import modules.template_config_parser as tcp

    monkeypatch.setattr(tcp, "build_template_queue_metadata", lambda: {"m": 1})
    engine._template_queue_metadata_building = True

    def sleep_and_fill(s):
        clock.sleep(s)
        engine._template_queue_metadata_cache = (clock.t + 30, b"FILLED")

    engine._yield_sleep = sleep_and_fill
    assert engine._get_template_queue_metadata_cached() == (b"FILLED", True)

    engine._template_queue_metadata_cache = None
    engine._template_queue_metadata_building = True
    engine._yield_sleep = clock.sleep
    payload, hit = engine._get_template_queue_metadata_cached()
    assert json.loads(payload) == {"m": 1} and hit is False
    assert engine._template_queue_metadata_building is False


# --------------------------------------------------------------------------- dynamic controls cache


def test_build_dynamic_controls_configs(engine, monkeypatch):
    configs = {
        "dock": {"dynamic_controls": {"elements": [{"id": 1}]}},
        "hidden": {"dynamic_controls": {"elements": [{"id": 2}]}},
        "empty": {"dynamic_controls": {"elements": []}},
        "none": {"other": 1},
        "bad": {},
    }
    engine.template_config_parser = FakeParser(configs, raise_for={"bad"}, hidden={"hidden"})
    monkeypatch.setattr(we, "resolve_dynamic_control_values_from_elements",
                        lambda cfg: dict(cfg["dynamic_controls"]))
    merged = []
    engine._merge_preview_session_into_config = lambda tok, n, dc: merged.append(n)
    out = engine._build_dynamic_controls_configs("t")
    assert out == {"dock": {"elements": [{"id": 1}]}, "hidden": {"elements": [{"id": 2}], "hidden": True}}
    assert merged == ["dock", "hidden"]


def test_dynamic_controls_cache_paths(engine, clock):
    engine._build_dynamic_controls_configs = mock.Mock(return_value={"d": {}})
    assert engine._peek_dynamic_controls_cache("") is None
    payload, hit = engine._get_dynamic_controls_configs_cached("")
    assert payload == b'{"d":{}}' and hit is False
    assert engine._get_dynamic_controls_configs_cached("") == (payload, True)
    assert engine._peek_dynamic_controls_cache("tok") is None  # other key
    clock.t += engine._DYNAMIC_CONTROLS_CACHE_TTL + 0.1
    assert engine._peek_dynamic_controls_cache("") is None


def test_dynamic_controls_invalidate_race_not_cached(engine, clock):
    def build(tok):
        engine._template_config_generation += 1
        return {}

    engine._build_dynamic_controls_configs = build
    engine._get_dynamic_controls_configs_cached("")
    assert engine._dynamic_controls_cache is None


def test_dynamic_controls_wait_hit_and_deadline(engine, clock):
    engine._dynamic_controls_building = True
    peeks = iter([None, b"X"])
    engine._peek_dynamic_controls_cache = lambda tok: next(peeks)
    engine._yield_sleep = clock.sleep
    assert engine._get_dynamic_controls_configs_cached("") == (b"X", True)

    engine._peek_dynamic_controls_cache = lambda tok: None
    engine._dynamic_controls_building = True
    engine._build_dynamic_controls_configs = lambda tok: {"late": 1}
    payload, hit = engine._get_dynamic_controls_configs_cached("")
    assert json.loads(payload) == {"late": 1} and hit is False
    assert engine._dynamic_controls_building is False


def test_get_source_controls_configs(engine, clock):
    engine._build_dynamic_controls_configs = mock.Mock(return_value={"live": 1})
    assert engine.get_source_controls_configs() == {"live": 1}
    engine._build_dynamic_controls_configs.assert_called_once_with(None)
    engine._dynamic_controls_cache = ("", clock.t + 3, b'{"cached":1}')
    assert engine.get_source_controls_configs() == {"cached": 1}


def test_invalidate_all_template_configs_cache(engine):
    engine._all_template_configs_cache = ("k",)
    engine._dynamic_controls_cache = ("k",)
    engine._template_queue_metadata_cache = (1, b"")
    engine._persist_absence_cache = {("a",): ()}
    engine._runtime_path_index_cache = {"x": 1}
    engine._overlay_html_cache = {"a": ()}
    gen = engine._template_config_generation
    engine.invalidate_all_template_configs_cache()
    assert engine._all_template_configs_cache is None and engine._dynamic_controls_cache is None
    assert engine._template_queue_metadata_cache is None
    assert engine._template_config_generation == gen + 1
    assert engine._persist_absence_cache == {} and engine._runtime_path_index_cache is None
    assert engine._overlay_html_cache == {}
    engine._overlay_html_cache = None
    del engine._template_config_generation
    engine.invalidate_all_template_configs_cache()
    assert engine._template_config_generation == 1


def test_broadcast_template_config_updated(engine):
    engine.safe_emit = mock.Mock()
    engine.broadcast_template_config_updated("")
    engine.safe_emit.assert_not_called()
    engine.broadcast_template_config_updated("alerts")
    engine.safe_emit.assert_called_once_with("template_config_updated", {"template": "alerts"})


def test_assets_watch_templates(engine):
    engine.register_assets_watch_template(" b ")
    engine.register_assets_watch_template("a")
    engine.register_assets_watch_template("")
    engine.register_assets_watch_template(None)
    assert engine.get_assets_watch_templates() == ["a", "b"]
    engine.unregister_assets_watch_template("b")
    engine.unregister_assets_watch_template("")
    engine.unregister_assets_watch_template("missing")
    assert engine.get_assets_watch_templates() == ["a"]


# --------------------------------------------------------------------------- Twitch API worker


def test_stop_twitch_worker_only_when_started(engine):
    engine._twitch_api_queue = mock.Mock()
    engine._stop_twitch_api_worker()
    engine._twitch_api_queue.put.assert_not_called()
    engine._twitch_api_worker_started = True
    engine._stop_twitch_api_worker()
    engine._twitch_api_queue.put.assert_called_once_with(None)
    engine._twitch_api_queue.put.side_effect = RuntimeError("closed")
    engine._stop_twitch_api_worker()  # logged, not raised


def test_twitch_worker_runs_jobs_and_stops(engine):
    ran = []

    async def job(n):
        ran.append(n)

    async def bad():
        raise RuntimeError("helix 500")

    engine.ensure_twitch_api_worker()
    engine.ensure_twitch_api_worker()  # already started: no second thread
    workers = [t for t in threading.enumerate() if t.name == "MycelianTwitchApiWorker"]
    assert len(workers) >= 1
    engine._submit_twitch_api_coro(job(1))
    engine._submit_twitch_api_coro(bad())
    engine._submit_twitch_api_coro(job(2))
    engine._stop_twitch_api_worker()
    for t in workers:
        t.join(timeout=3)
    assert ran == [1, 2]
    assert engine._twitch_api_worker_started is False
    assert len(engine._tw_enqueue_times) == 0


def test_submit_twitch_coro_drops_when_full(engine, monkeypatch):
    engine.ensure_twitch_api_worker = lambda: None
    engine._twitch_api_queue = queue.Queue(maxsize=1)
    engine._twitch_api_queue.put_nowait("busy")
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    engine._submit_twitch_api_coro("job")
    assert len(engine._tw_drop_times) == 1
    assert printed == ["tw_drop depth=1"]


def test_twitch_worker_loop_swallows_loop_teardown_errors(engine, monkeypatch):
    class Loop:
        def __init__(self):
            self.ran = []

        def run_until_complete(self, coro):
            if coro == "shutdown":
                raise RuntimeError("teardown")
            self.ran.append(coro)

        def shutdown_asyncgens(self):
            return "shutdown"

        def close(self):
            raise RuntimeError("close")

    loop = Loop()
    monkeypatch.setattr(we, "asyncio", types.SimpleNamespace(new_event_loop=lambda: loop,
                                                             set_event_loop=lambda l: None))
    engine._twitch_api_worker_started = True
    engine._twitch_api_queue.put("job")
    engine._twitch_api_queue.put(None)
    engine._twitch_api_worker_loop()
    assert loop.ran == ["job"]
    assert engine._twitch_api_worker_started is False


# --------------------------------------------------------------------------- socket / overlay tracking


def test_log_socket_lifecycle_rate_limits(engine, clock, monkeypatch):
    infos = []
    monkeypatch.setattr(we.logger, "info", lambda msg, *a: infos.append(msg % a))
    engine._log_socket_lifecycle("connected", "s1", 1)
    clock.t += 1
    engine._log_socket_lifecycle("connected", "s2", 2)
    engine._log_socket_lifecycle("disconnected", "s2", 1)
    assert engine._socket_lifecycle_log_suppressed == 2
    clock.t += 10
    engine._log_socket_lifecycle("connected", "s3", 2)
    assert infos[0] == "Socket.IO client connected sid=s1 (connected=1)"
    assert infos[1] == "Socket.IO client connected sid=s3 (connected=2) (2 more in the last 11s)"
    assert engine._socket_lifecycle_log_suppressed == 0


def test_socket_connect_disconnect_counts(engine, monkeypatch):
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    assert engine._socket_client_connected() == 1
    assert engine._socket_client_connected() == 2
    assert engine._socket_client_disconnected() == 1
    assert engine._socket_client_disconnected() == 0
    assert engine._socket_client_disconnected() == 0
    assert engine._get_socket_connected_count() == 0
    assert len(engine._socket_connect_times) == 2 and len(engine._socket_disconnect_times) == 3
    assert printed[0].startswith("connect connected=1")


def test_overlay_counts_exclude_preview_iframes(engine):
    engine._socket_connected_count = 3
    engine._preview_iframe_tokens = {"s1": "t"}
    assert engine._preview_iframe_sid_count() == 1
    assert engine._overlay_socket_connected_count() == 2
    engine._preview_iframe_tokens = {"a": 1, "b": 2, "c": 3, "d": 4}
    assert engine._overlay_socket_connected_count() == 0


def test_overlay_connected_routes(engine):
    engine._overlay_hello_routes = {"s1": "alerts", "s2": "", "s3": "alerts", "s4": "chat"}
    assert engine._overlay_connected_routes() == {"alerts", "chat"}
    engine._overlay_hello_routes = None
    assert engine._overlay_connected_routes() == set()


@pytest.fixture
def route_from_path(monkeypatch):
    import modules.obs_browser_source_match as m

    fn = lambda p: p.strip("/").split("?")[0] if p.startswith("/") else None  # noqa: E731
    monkeypatch.setattr(m, "overlay_template_route_from_path", fn)
    return m


def test_register_overlay_hello(engine, route_from_path):
    engine._preview_iframe_has_sid = lambda sid: sid == "preview"
    engine._register_overlay_hello("", "/alerts")
    engine._register_overlay_hello("preview", "/alerts")
    engine._register_overlay_hello("s1", "not-a-path")
    engine._register_overlay_hello("s1", None)
    assert engine._overlay_hello_routes == {}
    engine._register_overlay_hello("s1", "/alerts?x=1")
    assert engine._overlay_hello_routes == {"s1": "alerts"}
    # Preview lookup failure does not block registration; missing dict is created
    engine._preview_iframe_has_sid = mock.Mock(side_effect=RuntimeError)
    engine._overlay_hello_routes = None
    engine._register_overlay_hello("s2", "/chat")
    assert engine._overlay_hello_routes == {"s2": "chat"}


def test_register_overlay_hello_import_failure(engine):
    engine._preview_iframe_has_sid = lambda sid: False
    with mock.patch.dict(sys.modules, {"modules.obs_browser_source_match": None}):
        engine._register_overlay_hello("s1", "/alerts")
    assert engine._overlay_hello_routes == {}


def test_unregister_and_lookup_overlay_hello(engine):
    engine._overlay_hello_routes = {"s1": "alerts"}
    assert engine._overlay_hello_route_for_sid("s1") == "alerts"
    assert engine._overlay_hello_route_for_sid("") is None
    engine._unregister_overlay_hello("")
    engine._unregister_overlay_hello("s1")
    assert engine._overlay_hello_routes == {}
    engine._overlay_hello_routes = None
    engine._unregister_overlay_hello("s1")
    assert engine._overlay_hello_route_for_sid("s1") is None


def test_handshake_route_for_sid(engine, route_from_path):
    engine._overlay_hello_routes = {"s1": "alerts"}
    assert engine._handshake_route_for_sid("s1", {"route": "/x"}) == "alerts"
    assert engine._handshake_route_for_sid("s2", "junk") is None
    assert engine._handshake_route_for_sid("s2", {}) is None
    assert engine._handshake_route_for_sid("s2", {"route": "/chat"}) == "chat"
    assert engine._handshake_route_for_sid("s2", {"path": " raw/route "}) == "raw/route"
    assert engine._handshake_route_for_sid("s2", {"path": "  /"}) is None
    with mock.patch.dict(sys.modules, {"modules.obs_browser_source_match": None}):
        assert engine._handshake_route_for_sid("s2", {"route": "/chat"}) == "chat"


# --------------------------------------------------------------------------- metrics


def test_http_latency_window_and_stats(engine, clock):
    assert engine._http_latency_stats() == {"http_p50_ms": 0.0, "http_p95_ms": 0.0, "http_max_ms": 0.0}
    engine._record_http_latency(100.0, "/a")
    clock.t += 61
    for ms in (10.0, 20.0, 30.0):
        engine._record_http_latency(ms, "/b")
    assert len(engine._http_latency_samples) == 3  # old sample pruned
    assert engine._http_latency_stats() == {"http_p50_ms": 20.0, "http_p95_ms": 20.0, "http_max_ms": 30.0}


def test_queue_age_emit_p95_and_net_connects(engine, clock):
    assert engine._queue_oldest_age_ms(deque()) == 0.0
    assert engine._queue_oldest_age_ms(deque([clock.t - 2])) == 2000.0
    assert engine._queue_oldest_age_ms(deque([clock.t + 5])) == 0.0
    assert engine._emit_delivery_p95_ms() == 0.0
    engine._emit_delivery_lags_ms.extend([5.0, 50.0, 10.0])
    assert engine._emit_delivery_p95_ms() == 10.0
    engine._socket_connect_times.extend([clock.t - 40, clock.t - 1, clock.t])
    engine._socket_disconnect_times.extend([clock.t - 50, clock.t - 2])
    assert engine._net_connects_30s() == 1
    assert list(engine._socket_connect_times) == [clock.t - 1, clock.t]


def test_record_slow_request_and_count(engine, clock, monkeypatch):
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    engine._record_slow_request("x", 0.75)
    engine._record_slow_request()
    assert printed == ["slow_request source=x elapsed=0.750s recent=1",
                       "slow_request source=unknown recent=2"]
    clock.t += engine._OVERLOAD_SLOW_REQUEST_WINDOW_SEC + 1
    assert engine._slow_request_count_recent() == 0


def test_collect_bottleneck_metrics(engine, clock):
    engine._socket_connected_count = 4
    engine._template_control_queue = mock.Mock(qsize=mock.Mock(side_effect=NotImplementedError))
    engine._twitch_api_queue = mock.Mock(qsize=mock.Mock(side_effect=NotImplementedError))
    engine._last_heartbeat_mono = clock.t - 13.0
    engine._last_gevent_heartbeat = clock.t - 5.0
    engine._tc_drop_times.append(clock.t)
    engine._tw_drop_times.extend([clock.t - 100, clock.t])
    engine._socket_emit_error_times.append(clock.t)
    engine._template_control_worker_alive = True
    m = engine._collect_bottleneck_metrics()
    assert m["hub_tick_drift_ms"] == pytest.approx(3000.0)
    assert m["heartbeat_stale_sec"] == 5.0
    assert (m["template_control_queue_depth"], m["twitch_api_queue_depth"]) == (0, 0)
    assert (m["template_control_drops_60s"], m["twitch_api_drops_60s"], m["socket_emit_errors_60s"]) == (1, 1, 1)
    assert m["connected_clients"] == 4 and m["template_control_worker_alive"] is True
    engine._last_heartbeat_mono = None
    engine._last_gevent_heartbeat = None
    m = engine._collect_bottleneck_metrics()
    assert m["hub_tick_drift_ms"] == 0.0 and m["heartbeat_stale_sec"] == 0.0


def test_calibration_watch_logs_near_threshold(engine, monkeypatch):
    infos = []
    monkeypatch.setattr(we.logger, "info", lambda msg, *a: infos.append(a[0]))
    engine._maybe_log_bottleneck_calibration_watch({
        "hub_tick_drift_ms": 1500, "http_p95_ms": 10, "template_control_queue_depth": 20,
        "twitch_api_queue_depth": 4,
    })
    assert infos == ["hub_tick_drift_ms", "template_control_queue_depth", "twitch_api_queue_depth"]


def test_overload_hysteresis(engine):
    assert engine._apply_overload_hysteresis(True, 0.0) is False
    assert engine._apply_overload_hysteresis(True, 10.0) is False
    assert engine._apply_overload_hysteresis(True, 15.0) is True
    assert engine._apply_overload_hysteresis(True, 16.0) is True  # already held
    assert engine._apply_overload_hysteresis(False, 20.0) is True  # clear window starts
    assert engine._apply_overload_hysteresis(False, 29.0) is True
    assert engine._apply_overload_hysteresis(False, 30.0) is False
    assert engine._apply_overload_hysteresis(False, 31.0) is False
    assert engine._clear_since_mono is None
    assert isinstance(engine._apply_overload_hysteresis(False), bool)


def test_bottleneck_and_overload_diagnostics(engine, monkeypatch):
    engine._collect_bottleneck_metrics = lambda: {"http_p95_ms": 999}
    engine._held_overloaded = True
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    d = engine._get_bottleneck_diagnostics()
    assert d["bottlenecked"] is True and d["raw_bottlenecked"] is True
    assert d["hard_reasons"] == ["http_p95_ms=999"] and d["metrics"] == {"http_p95_ms": 999}
    assert printed[0].startswith("eval bottlenecked=True raw=True")
    o = engine._get_overload_diagnostics()
    assert o == {"overloaded": True, "reasons": ["http_p95_ms=999"], "metrics": {"http_p95_ms": 999}}
    assert engine._is_overloaded() is True


def test_health_state_transition_logging(engine, monkeypatch):
    warnings = []
    monkeypatch.setattr(we.logger, "warning", lambda msg, *a: warnings.append(msg % a))
    engine._maybe_log_health_state_transition("Running", {})
    engine._maybe_log_health_state_transition("Running", {})
    assert warnings == [] and engine._last_reported_health_state == "Running"
    engine._maybe_log_health_state_transition(we.WEBENGINE_STATE_OVERLOADED, {
        "reasons": ["a", "b"], "metrics": {"connected_clients": 3}})
    assert "Overloaded — a; b; clients: 3; template_control_queue: ?" in warnings[-1]
    assert engine._last_overload_health_log_at > 0
    engine._maybe_log_health_state_transition("Stalled", {})
    assert warnings[-1] == "WebEngine health: recovered from Overloaded (now Stalled)"
    engine._maybe_log_health_state_transition(we.WEBENGINE_STATE_OVERLOADED, {})
    assert "Overloaded — unknown" in warnings[-1]


def test_heartbeat_thread_and_host_helpers(engine, clock):
    assert engine._heartbeat_stale_seconds() is None and engine._is_heartbeat_stale() is False
    engine._last_gevent_heartbeat = clock.t - 61
    assert engine._heartbeat_stale_seconds() == 61 and engine._is_heartbeat_stale() is True
    assert engine._thread_alive() is False and engine._is_zombie_thread() is False
    engine.server_thread = types.SimpleNamespace(is_alive=lambda: True)
    assert engine._thread_alive() is True and engine._is_zombie_thread() is True
    engine.is_running = True
    assert engine._is_zombie_thread() is False
    assert engine._local_connect_host() == "127.0.0.1"
    engine.host = ""
    assert engine._local_connect_host() == "127.0.0.1"
    engine.host = "192.168.1.4"
    assert engine._local_connect_host() == "192.168.1.4"


# --------------------------------------------------------------------------- get_health


@pytest.fixture
def health(engine, clock, monkeypatch):
    import modules.shutdown as shutdown

    state = types.SimpleNamespace(shutdown=False, overloaded=False, clients=0, reasons=[])
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: state.shutdown)
    engine._get_bottleneck_diagnostics = lambda: {
        "bottlenecked": state.overloaded, "reasons": state.reasons,
        "metrics": {"connected_clients": state.clients},
    }
    engine._maybe_log_health_state_transition = mock.Mock()
    return state


def _alive(engine, alive=True):
    engine.server_thread = types.SimpleNamespace(is_alive=lambda: alive)


def test_get_health_running_with_detail(engine, health, clock):
    engine.is_running = True
    _alive(engine)
    health.clients = 2
    h = engine.get_health()
    assert h["state"] == we.WEBENGINE_STATE_RUNNING and h["detail"] == "2 clients"
    assert h["connected_clients"] == 2 and h["thread_alive"] is True
    engine._maybe_log_health_state_transition.assert_called_once()


def test_get_health_overloaded_stalled(engine, health, clock):
    engine.is_running = True
    _alive(engine)
    health.overloaded = True
    health.reasons = ["http_p95_ms=900"]
    h = engine.get_health()
    assert h["state"] == we.WEBENGINE_STATE_OVERLOADED and "http_p95_ms=900" in h["detail"]
    assert h["overload"] is True and h["bottleneck_reasons"] == ["http_p95_ms=900"]
    engine._last_gevent_heartbeat = clock.t - 120
    h = engine.get_health()
    assert h["state"] == we.WEBENGINE_STATE_STALLED and "heartbeat 120s stale" in h["detail"]


def test_get_health_stopped_restarting_crashed(engine, health, clock):
    health.shutdown = True
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_STOPPED
    health.shutdown = False
    engine._restart_in_progress = True
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_RESTARTING
    engine._restart_in_progress = False
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_CRASHED  # no thread, not running
    _alive(engine)
    engine.is_running = True
    engine._restart_attempts = engine._MAX_RESTART_ATTEMPTS
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_CRASHED  # gave up
    engine._restart_attempts = 0
    engine._restart_giveup_notified = False
    engine.is_running = False
    engine._last_run_error = "bind failed: " + "x" * 200
    h = engine.get_health()
    assert h["state"] == we.WEBENGINE_STATE_CRASHED
    assert h["detail"] == ("bind failed: " + "x" * 200)[:80]


def test_get_health_starting_and_grace(engine, health, clock, monkeypatch):
    # Zombie detection would win while the thread is alive and not running, so
    # look at the starting branches with it turned off.
    engine._is_zombie_thread = lambda: False
    _alive(engine)
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_STARTING
    engine._server_started_at = clock.t - 5
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_STARTING
    engine._server_started_at = clock.t - 60
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_CRASHED
    engine._last_gevent_heartbeat = clock.t
    assert engine.get_health()["state"] == we.WEBENGINE_STATE_STOPPED


def test_all_template_configs_rate_limit(engine, clock):
    for _ in range(engine._ALL_TEMPLATE_CONFIGS_RATE_LIMIT):
        assert engine._all_template_configs_rate_limited("1.2.3.4") is False
    assert engine._all_template_configs_rate_limited("1.2.3.4") is True
    assert engine._all_template_configs_rate_limited("5.6.7.8") is False
    clock.t += engine._ALL_TEMPLATE_CONFIGS_RATE_WINDOW_SEC + 0.1
    assert engine._all_template_configs_rate_limited("1.2.3.4") is False

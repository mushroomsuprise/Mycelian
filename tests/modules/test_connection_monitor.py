# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.connection_monitor`` (internet / service probes and auto-reconnect). No real network."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import types
from types import SimpleNamespace

import pytest
import requests

import modules
from modules import connection_monitor as cm


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeThread:
    created: list = []

    def __init__(self, target=None, name=None, daemon=None, **kw):
        self.target, self.name, self.daemon = target, name, daemon
        self.started = False
        self.alive = False
        self.joined = []
        FakeThread.created.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


@pytest.fixture
def cmenv(monkeypatch):
    FakeThread.created = []
    clock = Clock()
    monkeypatch.setattr(cm.time, "monotonic", clock)
    monkeypatch.setattr(cm.threading, "Thread", FakeThread)
    shutdown = {"on": False}
    monkeypatch.setattr(cm, "is_shutdown_in_progress", lambda: shutdown["on"])
    for name, value in {
        "_external_fail_streak": 0, "_internet_online": True, "_internet_status_label": "Online",
        "_internet_settled_label": "Online", "_last_external_probe_mono": 0.0,
        "_last_external_result_online": True, "_service_probe_cache": {}, "_last_reconnect_mono": {},
        "_monitor_thread": None, "_bg_refresh_pending": [], "_bg_refresh_thread": None,
        "_bg_refresh_active": None, "_bg_refresh_listeners": [],
    }.items():
        monkeypatch.setattr(cm, name, value)
    probes = {"up": True, "urls": []}

    def fake_http(url):
        probes["urls"].append(url)
        return probes["up"]

    monkeypatch.setattr(cm, "_http_probe", fake_http)
    return SimpleNamespace(clock=clock, shutdown=shutdown, probes=probes)


# --------------------------------------------------------------------------- _on_ui_loop_thread

def test_on_ui_loop_thread_without_nicegui_core(monkeypatch):
    monkeypatch.delitem(sys.modules, "nicegui.core", raising=False)
    assert cm._on_ui_loop_thread() is False


def test_on_ui_loop_thread_no_loop(monkeypatch):
    monkeypatch.setitem(sys.modules, "nicegui.core", SimpleNamespace(loop=None))
    assert cm._on_ui_loop_thread() is False


def test_on_ui_loop_thread_outside_running_loop(monkeypatch):
    monkeypatch.setitem(sys.modules, "nicegui.core", SimpleNamespace(loop=object()))
    assert cm._on_ui_loop_thread() is False


def test_on_ui_loop_thread_inside_matching_loop(monkeypatch):
    async def probe():
        monkeypatch.setitem(sys.modules, "nicegui.core", SimpleNamespace(loop=asyncio.get_running_loop()))
        inside = cm._on_ui_loop_thread()
        monkeypatch.setitem(sys.modules, "nicegui.core", SimpleNamespace(loop=object()))
        other = cm._on_ui_loop_thread()
        return inside, other

    assert asyncio.run(probe()) == (True, False)


# --------------------------------------------------------------------------- background refresh

def test_add_listener_dedupes(cmenv):
    cb = lambda: None
    cm.add_background_refresh_listener(cb)
    cm.add_background_refresh_listener(cb)
    assert cm._bg_refresh_listeners == [cb]


def test_request_background_refresh_queues_and_starts_once(cmenv):
    cm.request_background_refresh("internet")
    cm.request_background_refresh("internet")
    cm.request_background_refresh("twitch")
    assert cm._bg_refresh_pending == ["internet", "twitch"]
    assert len(FakeThread.created) == 1
    t = FakeThread.created[0]
    assert t.started and t.name == "ConnectivityRefresh" and t.daemon is True
    assert t.target is cm._background_refresh_worker
    assert cm.is_background_refresh_pending("twitch")
    assert not cm.is_background_refresh_pending("spotify")


def test_request_skips_active_key_and_shutdown(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_bg_refresh_active", "twitch")
    monkeypatch.setattr(cm, "_bg_refresh_thread", object())
    cm.request_background_refresh("twitch")
    assert cm._bg_refresh_pending == []
    assert cm.is_background_refresh_pending("twitch")
    cmenv.shutdown["on"] = True
    cm.request_background_refresh("spotify")
    assert cm._bg_refresh_pending == []


def test_worker_runs_queue_and_calls_listeners(cmenv, monkeypatch):
    seen = []
    monkeypatch.setattr(cm, "_update_internet_state", lambda: seen.append("internet"))

    def reach(key, blocking):
        seen.append((key, blocking))
        if key == "bad":
            raise RuntimeError("probe died")

    monkeypatch.setattr(cm, "is_service_reachable", reach)
    cm._bg_refresh_pending.extend(["internet", "bad", "twitch"])
    monkeypatch.setattr(cm, "_bg_refresh_thread", object())

    def bad_listener():
        raise RuntimeError("x")

    cm.add_background_refresh_listener(bad_listener)
    cm.add_background_refresh_listener(lambda: seen.append("listener"))
    cm._background_refresh_worker()
    assert seen == ["internet", ("bad", True), ("twitch", True), "listener"]
    assert cm._bg_refresh_thread is None and cm._bg_refresh_active is None


def test_worker_drops_queue_on_shutdown(cmenv, monkeypatch):
    cm._bg_refresh_pending.extend(["internet"])
    cmenv.shutdown["on"] = True
    monkeypatch.setattr(cm, "_update_internet_state", lambda: pytest.fail("no probes during shutdown"))
    cm._background_refresh_worker()
    assert cm._bg_refresh_pending == []


# --------------------------------------------------------------------------- probes

def test_internet_cache_stale(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert not cm._internet_cache_stale(cmenv.clock.t + cm._INTERNET_ONLINE_CACHE_SEC - 1)
    assert cm._internet_cache_stale(cmenv.clock.t + cm._INTERNET_ONLINE_CACHE_SEC)
    monkeypatch.setattr(cm, "_internet_online", False)
    assert cm._internet_cache_stale(cmenv.clock.t + cm._INTERNET_OFFLINE_CACHE_SEC)


class Resp:
    def __init__(self, code):
        self.status_code = code
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.parametrize("code,expected", [(204, True), (404, True), (500, False)])
def test_http_probe_head(monkeypatch, code, expected):
    calls = []
    monkeypatch.setattr(cm.requests, "head", lambda url, **kw: calls.append((url, kw)) or Resp(code))
    assert cm._http_probe("https://x") is expected
    assert calls == [("https://x", {"timeout": cm._PROBE_TIMEOUT_SEC, "allow_redirects": True})]


@pytest.mark.parametrize("code,expected", [(200, True), (503, False)])
def test_http_probe_falls_back_to_get(monkeypatch, code, expected):
    def head(url, **kw):
        raise requests.ConnectionError("no head")

    resp = Resp(code)
    got = []
    monkeypatch.setattr(cm.requests, "head", head)
    monkeypatch.setattr(cm.requests, "get", lambda url, **kw: got.append(kw) or resp)
    assert cm._http_probe("https://x") is expected
    assert resp.closed and got[0]["stream"] is True


def test_http_probe_both_fail(monkeypatch):
    def fail(url, **kw):
        raise requests.Timeout("t")

    monkeypatch.setattr(cm.requests, "head", fail)
    monkeypatch.setattr(cm.requests, "get", fail)
    assert cm._http_probe("https://x") is False


def test_tcp_probe(monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []
    monkeypatch.setattr(cm.socket, "create_connection", lambda addr, timeout: seen.append((addr, timeout)) or Conn())
    assert cm._tcp_probe("db", 27017) is True
    assert seen == [(("db", 27017), cm._PROBE_TIMEOUT_SEC)]

    def refuse(addr, timeout):
        raise OSError("refused")

    monkeypatch.setattr(cm.socket, "create_connection", refuse)
    assert cm._tcp_probe("db", 1) is False


def test_external_probe_round(cmenv):
    assert cm._run_external_probe_round() is True
    assert cmenv.probes["urls"] == cm._EXTERNAL_PROBE_URLS[:1]
    cmenv.probes["up"] = False
    cmenv.probes["urls"].clear()
    assert cm._run_external_probe_round() is False
    assert cmenv.probes["urls"] == cm._EXTERNAL_PROBE_URLS


# --------------------------------------------------------------------------- _update_internet_state

def test_update_uses_cache_when_fresh(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert cm._update_internet_state() is True
    assert cmenv.probes["urls"] == []


def test_update_streak_to_offline_and_recovery(cmenv, monkeypatch):
    broadcasts = []
    from modules import web_engine

    monkeypatch.setattr(web_engine, "broadcast_overlay_recovery", lambda *a: broadcasts.append(a))
    cm._service_probe_cache["twitch"] = (True, 0.0)
    cmenv.probes["up"] = False
    for i in range(cm._EXTERNAL_OFFLINE_STREAK_REQUIRED - 1):
        assert cm._update_internet_state(force=True) is True
        assert cm._internet_status_label == "Checking" and cm._internet_settled_label == "Checking"
    assert cm._update_internet_state(force=True) is False
    assert cm._internet_status_label == "Offline" and cm._service_probe_cache == {}
    assert cm._last_external_result_online is False
    cmenv.probes["up"] = True
    assert cm._update_internet_state(force=True) is True
    assert cm._external_fail_streak == 0 and cm._internet_settled_label == "Online"
    assert broadcasts == [("internet", "restored")]
    # Already online: no second broadcast
    cm._update_internet_state(force=True)
    assert len(broadcasts) == 1


def test_update_recovery_broadcast_failure_is_swallowed(cmenv, monkeypatch):
    from modules import web_engine

    def boom(*a):
        raise RuntimeError("no server")

    monkeypatch.setattr(web_engine, "broadcast_overlay_recovery", boom)
    monkeypatch.setattr(cm, "_internet_online", False)
    assert cm._update_internet_state(force=True) is True


# --------------------------------------------------------------------------- is_internet_available / status

def test_is_internet_available_shutdown(cmenv):
    cmenv.shutdown["on"] = True
    assert cm.is_internet_available() is False


def test_is_internet_available_blocking(cmenv, monkeypatch):
    seen = []
    monkeypatch.setattr(cm, "_update_internet_state", lambda force=False: seen.append(force) or False)
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: False)
    assert cm.is_internet_available(force=True) is False
    assert seen == [True]


def test_is_internet_available_nonblocking_queues_when_stale(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: True)
    queued = []
    monkeypatch.setattr(cm, "request_background_refresh", queued.append)
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert cm.is_internet_available() is True
    assert queued == []
    assert cm.is_internet_available(force=True) is True
    assert queued == ["internet"]
    monkeypatch.setattr(cm, "_last_external_probe_mono", 0.0)
    cm.is_internet_available(blocking=False)
    assert queued == ["internet", "internet"]


def test_get_internet_status(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_internet_status_label", "Checking")
    monkeypatch.setattr(cm, "_internet_settled_label", "Offline")
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: True)
    assert cm.get_internet_status() == "Offline"
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: False)
    assert cm.get_internet_status() == "Checking"


def test_probe_service_url_uses_http_probe(cmenv):
    assert cm._probe_service_url("https://svc") is True
    assert cmenv.probes["urls"] == ["https://svc"]


# --------------------------------------------------------------------------- database probe

def _db_config(monkeypatch, **cfg):
    """Point the live DatabaseManager instance's config at ``cfg``."""
    from modules import database_manager

    monkeypatch.setattr(database_manager.database_manager, "get_config", lambda: SimpleNamespace(**cfg))


def test_probe_database_uses_the_real_manager_config(cmenv, monkeypatch):
    from modules import database_manager as dbm

    cfg = SimpleNamespace(database_type="firebase", firebase_database_url="https://x.firebaseio.com")
    monkeypatch.setattr(dbm.database_manager, "get_config", lambda: cfg)
    assert cm._probe_database() is True


def test_probe_database_sql(cmenv, monkeypatch):
    _db_config(monkeypatch, database_type="sql")
    assert cm._probe_database() is True


@pytest.mark.parametrize("url,expected_url", [
    ("https://x.firebaseio.com/", "https://x.firebaseio.com/.json?shallow=true"),
    ("https://x.firebaseio.com/root.json", "https://x.firebaseio.com/root.json?shallow=true"),
])
def test_probe_database_firebase(cmenv, monkeypatch, url, expected_url):
    _db_config(monkeypatch, database_type="firebase", firebase_database_url=url)
    assert cm._probe_database() is True
    assert cmenv.probes["urls"] == [expected_url]


def test_probe_database_firebase_without_url(cmenv, monkeypatch):
    _db_config(monkeypatch, database_type="firebase", firebase_database_url="  ")
    assert cm._probe_database() is False


@pytest.mark.parametrize("conn,addr", [
    ("mongodb://db.example:28000/x", ("db.example", 28000)),
    (None, ("localhost", 27017)),
    ("mongodb:///nohost", ("localhost", 27017)),
])
def test_probe_database_mongodb(cmenv, monkeypatch, conn, addr):
    _db_config(monkeypatch, database_type="mongodb", mongodb_connection_string=conn)
    seen = []
    monkeypatch.setattr(cm, "_tcp_probe", lambda h, p: seen.append((h, p)) or True)
    assert cm._probe_database() is True
    assert seen == [addr]


def test_probe_database_unknown_type_and_error(cmenv, monkeypatch):
    _db_config(monkeypatch, database_type="other")
    assert cm._probe_database() is False
    from modules import database_manager

    def boom():
        raise RuntimeError("cfg")

    monkeypatch.setattr(database_manager, "get_config", boom, raising=False)
    assert cm._probe_database() is False


# --------------------------------------------------------------------------- service reachability

def test_peek_service_reachable(cmenv, monkeypatch):
    queued = []
    monkeypatch.setattr(cm, "request_background_refresh", queued.append)
    assert cm.peek_service_reachable("twitch") is None and queued == ["twitch"]
    cm._service_probe_cache["twitch"] = (False, cmenv.clock.t)
    assert cm.peek_service_reachable("twitch") is False and queued == ["twitch"]
    cm._service_probe_cache["twitch"] = (True, cmenv.clock.t - cm._SERVICE_PROBE_CACHE_SEC)
    assert cm.peek_service_reachable("twitch") is True and queued == ["twitch", "twitch"]
    monkeypatch.setattr(cm, "_internet_online", False)
    assert cm.peek_service_reachable("twitch") is False
    cmenv.shutdown["on"] = True
    assert cm.peek_service_reachable("twitch") is False


def test_is_service_reachable_database_delegates(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_probe_database", lambda: "db-result")
    assert cm.is_service_reachable("database") == "db-result"


def test_is_service_reachable_nonblocking(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: True)
    queued = []
    monkeypatch.setattr(cm, "request_background_refresh", queued.append)
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert cm.is_service_reachable("twitch") is True  # no result yet -> optimistic
    cm._service_probe_cache["twitch"] = (False, cmenv.clock.t)
    assert cm.is_service_reachable("twitch") is False
    assert cm.is_service_reachable("twitch", force=True) is False
    assert queued.count("twitch") == 2
    monkeypatch.setattr(cm, "_internet_online", False)
    assert cm.is_service_reachable("twitch") is False


def test_is_service_reachable_blocking_probe_and_cache(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: False)
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert cm.is_service_reachable("spotify") is True
    assert cmenv.probes["urls"] == [cm._SERVICE_PROBE_URLS["spotify"]]
    cmenv.probes["up"] = False
    assert cm.is_service_reachable("spotify") is True  # cached
    assert cm.is_service_reachable("spotify", force=True) is False
    assert cm._service_probe_cache["spotify"] == (False, cmenv.clock.t)
    cmenv.clock.t += cm._SERVICE_PROBE_CACHE_SEC + 1
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    cmenv.probes["up"] = True
    # internet probe would now also be fresh; stale service cache re-probes
    assert cm.is_service_reachable("spotify") is True


def test_is_service_reachable_without_url_is_true(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_last_external_probe_mono", cmenv.clock.t)
    assert cm.is_service_reachable("discord", blocking=True) is True
    assert cmenv.probes["urls"] == []


def test_is_service_reachable_blocking_offline(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "is_internet_available", lambda blocking=None: False)
    assert cm.is_service_reachable("twitch", blocking=True) is False


# --------------------------------------------------------------------------- reconnect helpers

def test_auto_reconnect_enabled(monkeypatch):
    from modules import dataobjects

    monkeypatch.setattr(dataobjects, "state_manager",
                        SimpleNamespace(get_app_settings=lambda: SimpleNamespace(auto_reconnect=False)))
    assert cm._auto_reconnect_enabled() is False
    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=lambda: None))
    assert cm._auto_reconnect_enabled() is True


def test_reconnect_cooldown(cmenv):
    assert cm._reconnect_cooldown_elapsed("twitch") is True
    cm._mark_reconnect_attempt("twitch")
    assert cm._last_reconnect_mono["twitch"] == cmenv.clock.t
    assert cm._reconnect_cooldown_elapsed("twitch") is False
    cmenv.clock.t += cm._RECONNECT_COOLDOWN_SEC
    assert cm._reconnect_cooldown_elapsed("twitch") is True


# --------------------------------------------------------------------------- registry

def _fake_service_modules(monkeypatch):
    calls = []

    def mod(name, **attrs):
        m = types.ModuleType(f"modules.{name}")
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, f"modules.{name}", m)
        monkeypatch.setattr(modules, name, m, raising=False)
        return m

    def rec(tag, value=True):
        return lambda *a: calls.append(tag) or value

    mod("twitch", twitch_has_tokens_configured=rec("twitch.cfg"), attempt_auto_reconnect=rec("twitch.rc"))
    mod("spotify", spotify_configured_for_monitor=rec("spotify.cfg"), attempt_auto_reconnect=rec("spotify.rc"))
    mod("youtube", youtube_configured_for_monitor=rec("youtube.cfg"), attempt_auto_reconnect=rec("youtube.rc"))
    mod("psn_service", psn_configured_for_monitor=rec("psn.cfg"), attempt_auto_reconnect=rec("psn.rc"))
    mod("discord_service", discord_configured_for_monitor=rec("discord.cfg"), attempt_auto_reconnect=rec("discord.rc"))
    mod("chatbot", chatbot_has_dedicated_credentials=rec("chatbot.cfg"), attempt_auto_reconnect=rec("chatbot.rc"))
    mod("database_manager", database_needs_remote_monitor=rec("db.cfg"), attempt_auto_reconnect=rec("db.rc"))
    mod("connection_status_tracker", is_remote_service_disconnected=lambda key: calls.append(f"disc:{key}") or True)
    return calls


def test_build_registry_wires_every_service(monkeypatch):
    calls = _fake_service_modules(monkeypatch)
    reg = cm._build_registry()
    assert [e.key for e in reg] == ["twitch", "spotify", "youtube", "psn", "discord", "chatbot", "database"]
    for e in reg:
        assert e.is_configured() and e.is_disconnected() and e.attempt_reconnect()
    for tag in ("twitch", "spotify", "youtube", "psn", "discord", "chatbot", "db"):
        assert f"{tag}.cfg" in calls and f"{tag}.rc" in calls
    assert "disc:database" in calls and "disc:twitch" in calls


# --------------------------------------------------------------------------- monitor loop

class StopAfter:
    """Event stand-in: lets the loop body run ``n`` times."""

    def __init__(self, n=1):
        self.n = n
        self.flag = False
        self.waits = []

    def is_set(self):
        return self.flag

    def wait(self, timeout):
        self.waits.append(timeout)
        self.n -= 1
        if self.n <= 0:
            self.flag = True

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False


def _entry(key, configured=True, disconnected=True, log=None, fail=False):
    def reconnect():
        log.append(key)
        if fail:
            raise RuntimeError("reconnect blew up")
        return True

    return cm._ServiceEntry(key, lambda: configured, lambda: disconnected, reconnect)


def test_monitor_loop_reconnects_eligible_services(cmenv, monkeypatch):
    log = []
    reg = [
        _entry("ok", log=log),
        _entry("unconfigured", configured=False, log=log),
        _entry("connected", disconnected=False, log=log),
        _entry("cooling", log=log),
        _entry("unreachable", log=log),
        _entry("boom", log=log, fail=True),
    ]
    monkeypatch.setattr(cm, "_build_registry", lambda: reg)
    stop = StopAfter(1)
    monkeypatch.setattr(cm, "_monitor_stop", stop)
    monkeypatch.setattr(cm, "is_internet_available", lambda: True)
    monkeypatch.setattr(cm, "_auto_reconnect_enabled", lambda: True)
    cm._last_reconnect_mono["cooling"] = cmenv.clock.t
    monkeypatch.setattr(cm, "is_service_reachable", lambda key: key != "unreachable")
    cm._monitor_loop()
    assert log == ["ok", "boom"]
    assert cm._last_reconnect_mono["ok"] == cmenv.clock.t
    assert stop.waits == [cm._MONITOR_INTERVAL_SEC]


def test_monitor_loop_skips_when_disabled_or_offline(cmenv, monkeypatch):
    log = []
    monkeypatch.setattr(cm, "_build_registry", lambda: [_entry("x", log=log)])
    monkeypatch.setattr(cm, "is_internet_available", lambda: None)
    monkeypatch.setattr(cm, "_monitor_stop", StopAfter(2))
    states = iter([(False, True), (True, False)])

    def enabled():
        e, online = next(states)
        cm._internet_online = online
        return e

    monkeypatch.setattr(cm, "_auto_reconnect_enabled", enabled)
    cm._monitor_loop()
    assert log == []


def test_monitor_loop_breaks_on_shutdown(cmenv, monkeypatch):
    monkeypatch.setattr(cm, "_build_registry", lambda: [])
    stop = StopAfter(5)
    monkeypatch.setattr(cm, "_monitor_stop", stop)
    cmenv.shutdown["on"] = True
    cm._monitor_loop()
    assert stop.waits == []


def test_monitor_loop_inner_shutdown_stops_iteration(cmenv, monkeypatch):
    log = []

    def first_reconnect():
        log.append("a")
        cmenv.shutdown["on"] = True

    reg = [cm._ServiceEntry("a", lambda: True, lambda: True, first_reconnect), _entry("b", log=log)]
    monkeypatch.setattr(cm, "_build_registry", lambda: reg)
    monkeypatch.setattr(cm, "_monitor_stop", StopAfter(1))
    monkeypatch.setattr(cm, "is_internet_available", lambda: True)
    monkeypatch.setattr(cm, "_auto_reconnect_enabled", lambda: True)
    monkeypatch.setattr(cm, "is_service_reachable", lambda key: True)
    cm._monitor_loop()
    assert log == ["a"]


def test_monitor_loop_logs_errors_and_continues(cmenv, monkeypatch, caplog):
    monkeypatch.setattr(cm, "_build_registry", lambda: [])
    stop = StopAfter(2)
    monkeypatch.setattr(cm, "_monitor_stop", stop)

    def boom():
        raise RuntimeError("probe crash")

    monkeypatch.setattr(cm, "is_internet_available", boom)
    cm._monitor_loop()
    assert len(stop.waits) == 2
    assert "Connection monitor loop error" in caplog.text


# --------------------------------------------------------------------------- start / stop

def test_start_creates_thread_once(cmenv, monkeypatch):
    stop = StopAfter()
    stop.flag = True
    monkeypatch.setattr(cm, "_monitor_stop", stop)
    cm.start()
    assert stop.flag is False
    (t,) = FakeThread.created
    assert t.started and t.name == "ConnectionMonitor" and t.target is cm._monitor_loop
    t.alive = True
    cm.start()
    assert len(FakeThread.created) == 1
    t.alive = False
    cm.start()
    assert len(FakeThread.created) == 2


def test_stop_sets_event_and_joins(cmenv, monkeypatch):
    stop = StopAfter()
    monkeypatch.setattr(cm, "_monitor_stop", stop)
    cm.stop()  # no thread
    assert stop.flag is True
    t = FakeThread()
    t.alive = True
    monkeypatch.setattr(cm, "_monitor_thread", t)
    cm.stop()
    assert t.joined == [5.0]
    t2 = FakeThread()
    monkeypatch.setattr(cm, "_monitor_thread", t2)
    cm.stop()
    assert t2.joined == []

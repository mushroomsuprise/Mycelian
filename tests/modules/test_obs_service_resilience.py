# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""OBS connection resilience: address clean-up, error classification, waking when OBS starts,
closing sessions that connect too late, and resyncing after a request timeout."""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import obs_service as obs  # noqa: E402


@pytest.mark.parametrize("host,port,expected", [
    ("localhost", 4455, ("localhost", 4455)),
    ("  ws://192.168.1.20:4460/  ", 4455, ("192.168.1.20", 4460)),
    ("wss://studio.local", "4470", ("studio.local", 4470)),
    ("[::1]:4456", 4455, ("::1", 4456)),
    ("::1", 4455, ("::1", 4455)),  # bare IPv6: no port to split off
    ("", None, ("localhost", 4455)),
    ("host:notaport", 99999, ("host:notaport", 4455)),
])
def test_normalize_obs_address(host, port, expected):
    assert obs.normalize_obs_address(host, port) == expected


def test_this_computer_includes_its_own_addresses(monkeypatch):
    monkeypatch.setattr(obs, "_local_addresses", lambda: frozenset({"192.168.1.20"}))
    assert obs.obs_host_is_this_computer("localhost")
    assert obs.obs_host_is_this_computer("ws://192.168.1.20:4455")
    assert not obs.obs_host_is_this_computer("192.168.1.99")


class WebSocketTimeoutException(Exception):
    pass


class WebSocketAddressException(Exception):
    pass


class WebSocketBadStatusException(Exception):
    pass


class OBSSDKError(Exception):
    pass


@pytest.mark.parametrize("exc,kind", [
    (OBSSDKError("authentication enabled but no password provided"), "auth_missing"),
    (OBSSDKError("failed to identify client with the server, please check connection settings"), "auth_wrong"),
    (None, "timeout"),
    (WebSocketTimeoutException("timed out"), "timeout"),
    (ConnectionRefusedError(61, "Connection refused"), "refused"),
    (WebSocketAddressException("[Errno 8] nodename nor servname provided"), "host"),
    (WebSocketBadStatusException("Handshake status 404 Not Found"), "not_websocket"),
    (ConnectionResetError(54, "reset"), "closed"),
    (OSError(65, "No route to host"), "unreachable"),
    (RuntimeError("something odd"), "other"),
])
def test_classify_connect_error(exc, kind, monkeypatch):
    monkeypatch.setattr(obs, "_local_addresses", lambda: frozenset())
    err = obs.classify_connect_error(exc, "localhost", 4455)
    assert err["kind"] == kind
    assert err["title"] and err["hint"] and err["details"]


def test_refused_on_another_computer_mentions_that_computer(monkeypatch):
    monkeypatch.setattr(obs, "_local_addresses", lambda: frozenset())
    err = obs.classify_connect_error(ConnectionRefusedError("refused"), "10.0.0.5", 4455)
    assert "10.0.0.5:4455" in err["title"] and "firewall" in err["hint"]


def test_failed_connect_is_kept_and_logged_once(monkeypatch, caplog):
    import logging

    svc = obs.ObsServiceImpl()
    kw = {"host": "localhost", "port": 4455, "password": "", "timeout": 1}
    monkeypatch.setattr(svc, "_establish_req_session", lambda kw: (_ for _ in ()).throw(ConnectionRefusedError("refused")))
    with caplog.at_level(logging.WARNING, logger=obs.__name__):
        assert svc._run_connect_with_timeout(kw) is None
        assert svc._run_connect_with_timeout(kw) is None
    assert svc.last_connect_error()["kind"] == "refused"
    assert caplog.text.count("OBS connect failed") == 1  # then DEBUG only
    svc.apply_settings()
    assert svc.last_connect_error() is None  # new settings: the old reason no longer applies
    svc._replace_connect_executor()


def test_a_late_session_is_closed():
    closed = []
    fut: concurrent.futures.Future = concurrent.futures.Future()
    fut.set_result((SimpleNamespace(disconnect=lambda: closed.append(1)), "30", "5"))
    obs._close_late_session(fut)
    assert closed == [1]
    failed: concurrent.futures.Future = concurrent.futures.Future()
    failed.set_exception(RuntimeError("x"))
    obs._close_late_session(failed)  # nothing to close


def test_wakes_up_when_obs_starts(monkeypatch):
    svc = obs.ObsServiceImpl()
    running = iter([False, True])
    monkeypatch.setattr(svc, "_obs_just_started", lambda: next(running, False) and True)
    monkeypatch.setattr(obs, "_OBS_PROCESS_CHECK_SEC", 0.01)
    assert svc._disconnect_idle_wait_or_drain_queue(5.0) == "wake"


def test_obs_just_started_only_on_the_transition(monkeypatch):
    from modules import dataobjects, obs_browser_docks

    svc = obs.ObsServiceImpl()
    monkeypatch.setattr(dataobjects.state_manager, "get_obs_data", lambda: SimpleNamespace(host="localhost"))
    states = iter([False, True, True])
    monkeypatch.setattr(obs_browser_docks, "obs_process_is_running", lambda: next(states))
    assert svc._obs_just_started() is False  # first look: OBS closed
    assert svc._obs_just_started() is True  # it opened
    assert svc._obs_just_started() is False  # still open: nothing new
    monkeypatch.setattr(dataobjects.state_manager, "get_obs_data", lambda: SimpleNamespace(host="10.9.9.9"))
    monkeypatch.setattr(obs, "_local_addresses", lambda: frozenset())
    assert svc._obs_just_started() is False  # OBS on another computer: no process to watch


def test_retry_quickly_right_after_obs_starts(monkeypatch):
    svc = obs.ObsServiceImpl()
    svc._obs_started_at = obs.time.monotonic()
    monkeypatch.setattr(svc, "_obs_just_started", lambda: False)
    start = obs.time.monotonic()
    assert svc._disconnect_idle_wait_or_drain_queue(45.0) == "done"
    assert obs.time.monotonic() - start < obs._OBS_STARTING_RETRY_SEC + 1


def test_a_request_timeout_forces_a_fresh_session():
    svc = obs.ObsServiceImpl()
    svc._note_request_error(WebSocketTimeoutException("timed out"))
    assert svc._request_timed_out
    svc2 = obs.ObsServiceImpl()
    svc2._note_request_error(ValueError("bad scene"))
    assert not svc2._request_timed_out


def test_connection_kwargs_clean_the_address_and_password(monkeypatch):
    from modules import dataobjects

    monkeypatch.setattr(dataobjects.state_manager, "get_obs_data",
                        lambda: SimpleNamespace(host=" ws://127.0.0.1:4460 ", port=4455, password="  pw \n"))
    kw = obs.ObsServiceImpl()._connection_kwargs("connect")
    assert (kw["host"], kw["port"], kw["password"]) == ("127.0.0.1", 4460, "pw")


def test_no_proxy_covers_localhost_when_a_proxy_is_set(monkeypatch):
    for key in ("no_proxy", "NO_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    obs._ensure_no_proxy_for_localhost()
    import os

    assert "no_proxy" not in os.environ  # no proxy: nothing to change
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    monkeypatch.setenv("no_proxy", "example.com")
    obs._ensure_no_proxy_for_localhost()
    assert os.environ["no_proxy"] == "example.com,localhost,127.0.0.1,::1"

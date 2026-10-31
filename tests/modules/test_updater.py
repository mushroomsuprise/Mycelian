# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.updater module-level functions: GitHub release lookup, version checks,
the forced exit path and speed formatting.

Never downloads or exits: aiohttp is faked and os._exit raises a BaseException the tests catch.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiohttp
import pytest

from modules import updater as up
from modules import update_sync

SHA = "b" * 40
LATEST_URL = f"{up.GITHUB_API_BASE}/repos/{up.GITHUB_OWNER}/{up.GITHUB_REPO}/releases/latest"


def commit_url(tag):
    return f"{up.GITHUB_API_BASE}/repos/{up.GITHUB_OWNER}/{up.GITHUB_REPO}/commits/{tag}"


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", json_error=None):
        self.status = status
        self.payload = payload
        self._text = text
        self.json_error = json_error
        self.read_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type="application/json"):
        if self.json_error:
            raise self.json_error
        return self.payload

    async def text(self):
        return self._text

    async def read(self):
        self.read_called = True
        return b""


class FakeSession:
    def __init__(self, ns):
        self.ns = ns

    async def __aenter__(self):
        if self.ns.enter_error:
            raise self.ns.enter_error
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.ns.requested.append(url)
        route = self.ns.routes.get(url)
        if isinstance(route, BaseException):
            raise route
        if route is None:
            return FakeResponse(404)
        return route


@pytest.fixture
def github(monkeypatch):
    ns = SimpleNamespace(routes={}, requested=[], enter_error=None, timeouts=[])

    def client_session(timeout=None):
        ns.timeouts.append(timeout)
        return FakeSession(ns)

    monkeypatch.setattr(up.aiohttp, "ClientSession", client_session)
    monkeypatch.setattr(up, "_github_release_cache", None)
    monkeypatch.setattr(up, "_last_fetch_error", None)
    return ns


def release(github, tag="v2.0.0", body="notes", html="https://gh/r", commit=SHA):
    github.routes[LATEST_URL] = FakeResponse(200, {"tag_name": tag, "body": body, "html_url": html})
    if commit is not None:
        github.routes[commit_url(tag)] = FakeResponse(200, {"sha": commit})


def fetch(force=False):
    return asyncio.run(up.fetch_latest_update_info_from_github(force_refresh=force))


# --------------------------------------------------------------------------- small helpers

def test_last_update_check_error(monkeypatch):
    monkeypatch.setattr(up, "_last_fetch_error", ("m", "d"))
    assert up.last_update_check_error() == ("m", "d")


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(up, "notify", lambda message, **kw: calls.append((message, kw)))
    monkeypatch.setattr(up, "nav_actions_settings", lambda tab: [{"nav": tab}])
    return calls


def test_notify_check_not_ready(notes):
    up._notify_check_not_ready()
    (msg, kw), = notes
    assert msg == "Mycelian is still starting up." and kw["type"] == "warning" and kw["source"] == "updates"


def test_notify_update_failed(notes):
    up._notify_update_failed("boom", "trace")
    (msg, kw), = notes
    assert msg == "boom" and kw["details"] == "trace" and kw["type"] == "error"
    assert kw["dedupe_key"] == "updates:apply_failed" and kw["actions"] == [{"nav": "About"}]


@pytest.mark.parametrize("payload, expected", [({"sha": SHA}, SHA), ({"sha": "main"}, None), ({}, None),
                                               ([SHA], None), (None, None), ({"sha": SHA.upper()}, SHA)])
def test_release_download_ref(payload, expected):
    assert up.release_download_ref(payload) == expected


@pytest.mark.parametrize("current, new, expected", [("1.0.0", "1.0.1", True), ("1.0.1", "1.0.0", False),
                                                    ("1.0.0", "1.0.0", False), ("1.0", "1.0.1b1", True),
                                                    ("1.0.0", "not-a-version", False)])
def test_compare_versions(current, new, expected, caplog):
    assert up._compare_versions(current, new) is expected


@pytest.mark.parametrize("bps, text", [(0, "0 B/s"), (-5, "0 B/s"), (512, "512 B/s"), (2048, "2 KB/s"),
                                       (5 * 1024 * 1024, "5.00 MB/s"), (3 * 1024 ** 3, "3.00 GB/s"),
                                       (2 * 1024 ** 4, "2048.00 GB/s"), ("fast", "N/A")])
def test_format_speed(bps, text):
    assert up._format_speed(bps) == text


# --------------------------------------------------------------------------- commit resolution

def run_commit(github, tag):
    async def go():
        async with up.aiohttp.ClientSession() as s:
            return await up._fetch_release_commit_sha(s, tag)

    return asyncio.run(go())


def test_fetch_release_commit_sha(github, caplog):
    github.routes[commit_url("v1%2Fx")] = FakeResponse(200, {"sha": SHA})
    assert run_commit(github, " v1/x ") == SHA
    assert run_commit(github, "   ") is None
    resp = FakeResponse(422)
    github.routes[commit_url("bad")] = resp
    with caplog.at_level(logging.WARNING, logger=up.logger.name):
        assert run_commit(github, "bad") is None
    assert resp.read_called and "did not resolve to a commit (422)" in caplog.text
    github.routes[commit_url("nojson")] = FakeResponse(200, json_error=ValueError("x"))
    assert run_commit(github, "nojson") is None
    github.routes[commit_url("ct")] = FakeResponse(200, json_error=aiohttp.ContentTypeError(SimpleNamespace(real_url="u"), ()))
    assert run_commit(github, "ct") is None
    github.routes[commit_url("nosha")] = FakeResponse(200, {"sha": "main"})
    with caplog.at_level(logging.WARNING, logger=up.logger.name):
        assert run_commit(github, "nosha") is None
    assert "did not resolve to a commit SHA" in caplog.text


# --------------------------------------------------------------------------- fetch_latest_update_info_from_github

def test_fetch_success_and_cache(github):
    release(github)
    info = fetch()
    assert info == {"latest_version": "2.0.0", "tag_name": "v2.0.0", "release_notes": "notes",
                    "release_url": "https://gh/r", "published_at": "", "commit_sha": SHA}
    assert up.last_update_check_error() is None
    assert github.timeouts[0].total == 15.0
    info["latest_version"] = "mutated"
    n = len(github.requested)
    again = fetch()
    assert again["latest_version"] == "2.0.0"  # deep copy from cache
    assert len(github.requested) == n  # served from cache
    fetch(force=True)
    assert len(github.requested) == n + 2


def test_fetch_cache_expires(github, monkeypatch):
    release(github)
    t = {"now": 1000.0}
    monkeypatch.setattr(up.time, "monotonic", lambda: t["now"])
    fetch()
    n = len(github.requested)
    t["now"] += up._GITHUB_RELEASE_CACHE_TTL_SEC + 1
    fetch()
    assert len(github.requested) == n + 2


def test_fetch_cached_none(github, monkeypatch):
    monkeypatch.setattr(up, "_github_release_cache", (up.time.monotonic(), None))
    assert fetch() is None
    assert github.requested == []


def test_fetch_tag_without_version(github):
    github.routes[LATEST_URL] = FakeResponse(200, {"tag_name": "v", "body": ""})
    assert fetch() is None
    assert up.last_update_check_error()[0] == "The latest release on GitHub has no version number."
    assert up._github_release_cache[1] is None


def test_fetch_404(github):
    assert fetch() is None
    msg, details = up.last_update_check_error()
    assert msg == "GitHub didn't return a release for Mycelian." and details.endswith("HTTP 404")


@pytest.mark.parametrize("status, message", [(403, "GitHub is limiting requests right now."),
                                             (429, "GitHub is limiting requests right now."),
                                             (500, "GitHub returned an error (HTTP 500).")])
def test_fetch_http_errors(github, status, message):
    github.routes[LATEST_URL] = FakeResponse(status, text="x" * 400)
    assert fetch() is None
    msg, details = up.last_update_check_error()
    assert msg == message
    assert details.endswith("x" * 300) and "x" * 301 not in details


def test_fetch_tag_without_commit(github):
    release(github, commit=None)
    github.routes[commit_url("v2.0.0")] = FakeResponse(404)
    assert fetch() is None
    msg, details = up.last_update_check_error()
    assert "2.0.0 is on GitHub but can't be installed automatically" in msg
    assert details == "Release tag v2.0.0 did not resolve to a commit."


@pytest.mark.parametrize(
    "error, message",
    [
        (asyncio.TimeoutError(), "GitHub didn't answer in time."),
        (aiohttp.ServerTimeoutError(), "GitHub didn't answer in time."),
        (aiohttp.ClientConnectionError("dns"), "Mycelian couldn't reach GitHub."),
        (RuntimeError("weird"), "Something went wrong reading the release from GitHub."),
    ],
)
def test_fetch_exceptions_without_cache(github, error, message):
    github.routes[LATEST_URL] = error
    assert fetch() is None
    assert up.last_update_check_error()[0] == message
    assert up._github_release_cache[1] is None


def test_fetch_timeout_returns_cached_fallback(github, monkeypatch):
    cached = {"latest_version": "1.5", "commit_sha": SHA}
    monkeypatch.setattr(up, "_github_release_cache", (0.0, cached))  # stale
    monkeypatch.setattr(up.time, "monotonic", lambda: 10_000.0)
    github.routes[LATEST_URL] = asyncio.TimeoutError()
    result = fetch()
    assert result == cached and result is not cached
    assert up._github_release_cache == (0.0, cached)  # stale fallback kept, not overwritten


def test_fetch_failure_keeps_previous_good_cache(github, monkeypatch):
    cached = {"latest_version": "1.5", "commit_sha": SHA}
    monkeypatch.setattr(up, "_github_release_cache", (0.0, cached))
    monkeypatch.setattr(up.time, "monotonic", lambda: 10_000.0)
    assert fetch() is None  # 404
    assert up._github_release_cache == (0.0, cached)


def test_fetch_cancelled_returns_cached_fallback(github, monkeypatch):
    cached = {"latest_version": "1.5", "commit_sha": SHA}
    monkeypatch.setattr(up, "_github_release_cache", (0.0, cached))
    monkeypatch.setattr(up.time, "monotonic", lambda: 10_000.0)
    github.routes[LATEST_URL] = asyncio.CancelledError()
    assert fetch() == cached


def test_fetch_real_task_cancel_propagates(github, monkeypatch):
    cached = {"latest_version": "1.5", "commit_sha": SHA}
    monkeypatch.setattr(up, "_github_release_cache", (0.0, cached))
    monkeypatch.setattr(up.time, "monotonic", lambda: 10_000.0)
    github.routes[LATEST_URL] = asyncio.CancelledError()

    async def run():
        asyncio.current_task().cancel()  # the task itself is being cancelled (e.g. shutdown)
        return await up.fetch_latest_update_info_from_github()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


# --------------------------------------------------------------------------- check_for_updates

def check(monkeypatch, info, current="1.0.0"):
    seen = {}

    async def fake_fetch(*, force_refresh=False):
        seen["force"] = force_refresh
        return info

    monkeypatch.setattr(up, "fetch_latest_update_info_from_github", fake_fetch)
    return asyncio.run(up.check_for_updates(current, force_refresh=True)), seen


def test_check_for_updates_newer(monkeypatch):
    info = {"latest_version": "2.0.0", "commit_sha": SHA}
    result, seen = check(monkeypatch, info)
    assert result is info and seen == {"force": True}


@pytest.mark.parametrize("info", [None, {}, {"latest_version": "2.0.0"}, {"latest_version": "2.0.0", "commit_sha": "main"},
                                  {"latest_version": "1.0.0", "commit_sha": SHA},
                                  {"latest_version": "", "commit_sha": SHA}])
def test_check_for_updates_nothing_to_offer(monkeypatch, info):
    assert check(monkeypatch, info)[0] is None


# --------------------------------------------------------------------------- exit paths

class Exited(BaseException):
    pass


@pytest.fixture
def exits(monkeypatch):
    codes = []

    def fake_exit(code):
        codes.append(code)
        raise Exited(code)

    monkeypatch.setattr(up.os, "_exit", fake_exit)
    return codes


@pytest.fixture
def fake_shutdown(monkeypatch):
    ns = SimpleNamespace(reaps=0, protected=[], reap_error=None)

    def reap():
        ns.reaps += 1
        if ns.reap_error:
            raise ns.reap_error

    mod = ModuleType("modules.shutdown")
    mod.reap_child_process_trees = reap
    mod.protect_child_process_trees = lambda pids: ns.protected.extend(pids)
    monkeypatch.setitem(sys.modules, "modules.shutdown", mod)
    return ns


@pytest.mark.parametrize("pid", [0, -1, None, "12"])
def test_finish_update_requires_helper(monkeypatch, pid):
    monkeypatch.setattr(up, "_force_application_exit", lambda: pytest.fail("must not exit"))
    with pytest.raises(update_sync.UpdateSyncError, match="did not start"):
        up.finish_update_and_exit(pid)


def test_finish_update_protects_then_exits(monkeypatch, fake_shutdown):
    calls = []
    monkeypatch.setattr(up, "_force_application_exit", lambda: calls.append("exit"))
    up.finish_update_and_exit(321)
    assert fake_shutdown.protected == [321] and calls == ["exit"]


class FakeNativeWindow:
    def __init__(self):
        self.destroyed = 0

    def destroy(self):
        self.destroyed += 1


def install_exit_env(monkeypatch, *, main_window=None, has_shutdown=True, windows=(), loop=None,
                     shutdown_error=None):
    calls = []
    ng = ModuleType("nicegui")
    app = SimpleNamespace(native=SimpleNamespace(main_window=main_window))
    if has_shutdown:
        def shutdown():
            calls.append("app.shutdown")
            if shutdown_error:
                raise shutdown_error
        app.shutdown = shutdown
    ng.app = app
    monkeypatch.setitem(sys.modules, "nicegui", ng)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=list(windows)))
    if loop is not None:
        monkeypatch.setattr(up.asyncio, "get_event_loop", lambda: loop)
    import time as time_mod

    monkeypatch.setattr(time_mod, "sleep", lambda s: calls.append(("sleep", s)))
    return calls


class FakeLoop:
    def __init__(self, running):
        self.running = running
        self.scheduled = []
        self.stopped = False

    def is_running(self):
        return self.running

    def call_soon_threadsafe(self, fn):
        self.scheduled.append(fn)

    def stop(self):
        self.stopped = True


def test_force_exit_full_cleanup(monkeypatch, exits, fake_shutdown):
    win = FakeNativeWindow()
    pw1, pw2 = FakeNativeWindow(), object()
    loop = FakeLoop(running=True)
    calls = install_exit_env(monkeypatch, main_window=win, windows=[pw1, pw2], loop=loop)
    with pytest.raises(Exited):
        up._force_application_exit()
    assert exits == [0]
    assert fake_shutdown.reaps == 2
    assert win.destroyed == 1 and pw1.destroyed == 1
    assert len(loop.scheduled) == 1
    loop.scheduled[0]()
    assert loop.stopped
    assert calls == [("sleep", 0.5), "app.shutdown"]


def test_force_exit_tolerates_failures(monkeypatch, exits, fake_shutdown, caplog):
    fake_shutdown.reap_error = RuntimeError("reap failed")

    class BadWindow:
        def destroy(self):
            raise RuntimeError("gone")

    def no_loop():
        raise RuntimeError("no current event loop")

    install_exit_env(monkeypatch, main_window=BadWindow(), windows=[BadWindow()],
                     shutdown_error=RuntimeError("already down"))
    monkeypatch.setattr(up.asyncio, "get_event_loop", no_loop)
    with caplog.at_level(logging.DEBUG, logger=up.logger.name), pytest.raises(Exited):
        up._force_application_exit()
    assert exits == [0]
    for text in ("Could not reap child processes", "Could not close NiceGUI window",
                 "Could not close pywebview windows", "Could not shutdown NiceGUI app"):
        assert text in caplog.text


def test_force_exit_idle_loop_and_no_windows(monkeypatch, exits, fake_shutdown):
    calls = install_exit_env(monkeypatch, main_window=None, has_shutdown=False, loop=FakeLoop(running=False))
    with pytest.raises(Exited):
        up._force_application_exit()
    assert calls == [] and exits == [0]


def test_force_exit_unexpected_error_falls_back_to_exit_1(monkeypatch, exits, fake_shutdown, caplog):
    install_exit_env(monkeypatch)

    def broken_loop():
        raise ValueError("loop policy broken")

    monkeypatch.setattr(up.asyncio, "get_event_loop", broken_loop)
    with caplog.at_level(logging.ERROR, logger=up.logger.name), pytest.raises(Exited):
        up._force_application_exit()
    assert exits == [1]
    assert fake_shutdown.reaps == 2
    assert "Error during application exit: loop policy broken" in caplog.text


def test_force_exit_fallback_reap_error(monkeypatch, exits, fake_shutdown):
    install_exit_env(monkeypatch)
    monkeypatch.setattr(up.asyncio, "get_event_loop", lambda: (_ for _ in ()).throw(ValueError("x")))
    fake_shutdown.reap_error = RuntimeError("still failing")
    with pytest.raises(Exited):
        up._force_application_exit()
    assert exits == [1]


# --------------------------------------------------------------------------- connected client

def install_clients(monkeypatch, clients):
    ng = ModuleType("nicegui")
    ng.Client = SimpleNamespace(instances={i: c for i, c in enumerate(clients)})
    monkeypatch.setitem(sys.modules, "nicegui", ng)


def test_connected_client(monkeypatch):
    deleted = SimpleNamespace(is_deleted=True, has_socket_connection=True)
    offline = SimpleNamespace(has_socket_connection=False)
    live = SimpleNamespace(is_deleted=False, has_socket_connection=True)
    install_clients(monkeypatch, [deleted, offline, live])
    assert up._connected_client() is live
    install_clients(monkeypatch, [deleted, offline])
    assert up._connected_client() is None


def test_connected_client_error(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "nicegui", None)
    with caplog.at_level(logging.DEBUG, logger=up.logger.name):
        assert up._connected_client() is None
    assert "could not resolve a connected client" in caplog.text


# --------------------------------------------------------------------------- release history / downgrade

def releases_url(page):
    return f"{up.GITHUB_API_BASE}/repos/{up.GITHUB_OWNER}/{up.GITHUB_REPO}/releases?per_page=100&page={page}"


def test_fetch_release_history_filters_and_sorts(github):
    github.routes[releases_url(1)] = FakeResponse(200, [
        {"tag_name": "v1.14.0", "body": "first", "html_url": "u14", "published_at": "2026-10-01T00:00:00Z"},
        {"tag_name": "v1.16.0", "body": "newest", "html_url": "u16"},
        {"tag_name": "v1.15.0-beta", "prerelease": True},
        {"tag_name": "v1.15.1", "draft": True},
        {"tag_name": "v1.13.9"},  # older than the first release with this updater
        {"tag_name": "nightly"},
        {"tag_name": "1.15.0"},
        "junk",
    ])
    history = asyncio.run(up.fetch_release_history())
    assert [r["latest_version"] for r in history] == ["1.16.0", "1.15.0", "1.14.0"]
    assert history[2] == {"latest_version": "1.14.0", "tag_name": "v1.14.0", "release_notes": "first",
                          "release_url": "u14", "published_at": "2026-10-01T00:00:00Z"}
    assert github.requested == [releases_url(1)]  # a short page is the last one


def test_fetch_release_history_pages_and_errors(github):
    github.routes[releases_url(1)] = FakeResponse(200, [{"tag_name": f"v1.14.{i}"} for i in range(100)])
    github.routes[releases_url(2)] = FakeResponse(200, [])
    assert len(asyncio.run(up.fetch_release_history())) == 100
    assert github.requested == [releases_url(1), releases_url(2)]
    github.routes[releases_url(1)] = FakeResponse(403, text="rate limited")
    with pytest.raises(RuntimeError, match="HTTP 403"):
        asyncio.run(up.fetch_release_history())


def test_downgrade_choices():
    history = [{"latest_version": v} for v in ("1.16.0", "1.15.0", "1.14.0")]
    assert [r["latest_version"] for r in up.downgrade_choices("1.15.0", history)] == ["1.14.0"]
    assert up.downgrade_choices("1.14.0", history) == []
    assert up.downgrade_choices("not a version", history) == []


def test_resolve_release_commit(github):
    github.routes[commit_url("v1.14.0")] = FakeResponse(200, {"sha": SHA})
    assert asyncio.run(up.resolve_release_commit("v1.14.0")) == SHA
    assert asyncio.run(up.resolve_release_commit("v9")) is None


@pytest.mark.parametrize("outcome, kind", [((True, ""), "success"), ((False, "copy failed"), "error"), (None, None)])
def test_report_last_apply_result(monkeypatch, notes, outcome, kind):
    monkeypatch.setattr(update_sync, "take_update_result", lambda: outcome)
    up.UpdateManager().report_last_apply_result()
    if kind is None:
        assert notes == []
        return
    (msg, kw), = notes
    assert kw["type"] == kind
    if kind == "error":
        assert "previous version was restored" in msg and kw["details"] == "copy failed"

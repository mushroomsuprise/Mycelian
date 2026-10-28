# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 4: poll / stream events, shoutouts, the health-check
and subscriber-registry retry loops, revocation handling, EventSub stop and reconnect."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import threading  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import (  # noqa: E402
    connection_monitor,
    dataobjects,
    discord_service,
    notification_engine,
    shutdown,
    twitch as tw,
    twitch_polls,
    twitch_subscriber_registry,
)


def run(coro):
    return asyncio.run(coro)


FUTURE = datetime.now() + timedelta(hours=3)


class FakeThread:
    created: List["FakeThread"] = []

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.target, self.daemon, self.name = target, daemon, name
        self.started = False
        FakeThread.created.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        pass


@pytest.fixture
def fake_threading(monkeypatch):
    FakeThread.created = []
    ns = SimpleNamespace(Thread=FakeThread, Event=threading.Event, Lock=threading.Lock,
                         current_thread=threading.current_thread)
    monkeypatch.setattr(tw, "threading", ns)
    return FakeThread


@pytest.fixture
def env(monkeypatch):
    flags = SimpleNamespace(shutdown=False, internet=True, reachable=True, auto_reconnect=True)
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: flags.shutdown)
    monkeypatch.setattr(connection_monitor, "is_internet_available", lambda *a, **k: flags.internet)
    monkeypatch.setattr(connection_monitor, "is_service_reachable", lambda *a, **k: flags.reachable)
    state = MagicMock()
    state.get_app_settings.side_effect = lambda: SimpleNamespace(auto_reconnect=flags.auto_reconnect)
    state.save_changes.return_value = True
    monkeypatch.setattr(dataobjects, "state_manager", state)
    flags.state = state
    return flags


@pytest.fixture
def api(env):
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42")
    a.user_id = "42"
    a.auth_token = "tok"
    return a


# --------------------------------------------------------------------------- polls


def test_subscribe_poll_events_ok(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: {"channel:manage:polls"})
    es = MagicMock()
    for name in ("listen_channel_poll_begin", "listen_channel_poll_progress", "listen_channel_poll_end"):
        setattr(es, name, AsyncMock())
    run(api._subscribe_poll_events(es))
    es.listen_channel_poll_begin.assert_awaited_once_with("42", api.on_poll_begin)
    es.listen_channel_poll_end.assert_awaited_once_with("42", api.on_poll_end)


def test_subscribe_poll_events_validates_then_unknown(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: None)
    validate = AsyncMock()
    monkeypatch.setattr(tw, "validate_access_token", validate)
    es = MagicMock()
    run(api._subscribe_poll_events(es))
    validate.assert_awaited_once_with("tok")
    es.listen_channel_poll_begin.assert_not_called()


def test_subscribe_poll_events_missing_scope(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: {"chat:read"})
    notify = MagicMock()
    monkeypatch.setattr(twitch_polls, "notify_scope_missing", notify)
    es = MagicMock()
    run(api._subscribe_poll_events(es))
    notify.assert_called_once()
    es.listen_channel_poll_begin.assert_not_called()


def test_subscribe_poll_events_error_is_logged(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: {"channel:manage:polls"})
    es = MagicMock()
    es.listen_channel_poll_begin = AsyncMock(side_effect=RuntimeError("409"))
    run(api._subscribe_poll_events(es))  # no raise


@pytest.mark.parametrize("method, handler", [("on_poll_begin", "handle_begin"), ("on_poll_progress", "handle_progress"),
                                             ("on_poll_end", "handle_end")])
def test_poll_handlers_forward(api, monkeypatch, method, handler):
    seen = []
    monkeypatch.setattr(twitch_polls, handler, lambda ev: seen.append(ev))
    run(getattr(api, method)(SimpleNamespace(event="EV")))
    assert seen == ["EV"] and api.last_event_time is not None


# --------------------------------------------------------------------------- stream online / offline


def test_stream_online_offline(api, monkeypatch):
    live, off = MagicMock(), MagicMock()
    monkeypatch.setattr(discord_service, "notify_platform_live", live)
    monkeypatch.setattr(discord_service, "notify_platform_offline", off)
    run(api.on_stream_online(SimpleNamespace(event=SimpleNamespace(id="1", type="live"))))
    live.assert_called_once_with("twitch")
    run(api.on_stream_offline(SimpleNamespace(event=None)))
    off.assert_called_once_with("twitch")


def test_stream_online_offline_errors(api, monkeypatch):
    monkeypatch.setattr(discord_service, "notify_platform_live", MagicMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(discord_service, "notify_platform_offline", MagicMock(side_effect=RuntimeError("x")))

    class Bad:
        @property
        def event(self):
            raise RuntimeError("no event")

    run(api.on_stream_online(Bad()))
    run(api.on_stream_offline(Bad()))


# --------------------------------------------------------------------------- shoutout


def test_send_shoutout(api, monkeypatch):
    api.generic_api_call = AsyncMock(return_value={})
    assert run(api.send_shoutout("99", "raider")) is True
    url, method = api.generic_api_call.await_args.args
    assert url.endswith("/chat/shoutouts") and method == "POST"
    assert api.generic_api_call.await_args.kwargs["params"] == {
        "from_broadcaster_id": "42", "to_broadcaster_id": "99", "moderator_id": "42"}


def test_send_shoutout_missing_ids(api):
    api.generic_api_call = AsyncMock()
    assert run(api.send_shoutout("")) is False
    api.user_id = ""
    assert run(api.send_shoutout("99")) is False
    api.generic_api_call.assert_not_awaited()


def test_send_shoutout_failure_notifies(api, monkeypatch):
    api.generic_api_call = AsyncMock(side_effect=RuntimeError("429"))
    notify = MagicMock()
    monkeypatch.setattr(notification_engine, "notify", notify)
    assert run(api.send_shoutout("99")) is False
    assert notify.call_args.kwargs["title"] == "Couldn't send a Twitch shoutout to the raider"
    notify.side_effect = RuntimeError("ui")
    assert run(api.send_shoutout("99", "bob")) is False


# --------------------------------------------------------------------------- health check thread


def test_start_and_stop_health_check(api, fake_threading):
    api.start_health_check()
    t = api.health_check_thread
    assert t.started and t.daemon and t.target == api._health_check_loop
    api.start_health_check()  # already alive: no new thread
    assert len(fake_threading.created) == 1
    api.stop_health_check()
    assert api._health_stop.is_set()


def test_stop_health_check_thread_still_alive(api):
    api.health_check_thread = MagicMock()
    api.health_check_thread.is_alive.return_value = True
    api.stop_health_check()
    api.health_check_thread.join.assert_called_once_with(timeout=2.0)


def test_subscriber_registry_retry_start_stop(api, fake_threading):
    old = MagicMock()
    old.is_alive.return_value = True
    api._sub_registry_retry_thread = old
    api._start_subscriber_registry_retry()
    old.join.assert_called_once_with(timeout=0.2)
    t = api._sub_registry_retry_thread
    assert t.name == "TwitchSubRegistryRetry" and t.started
    api._stop_subscriber_registry_retry()
    assert api._sub_registry_retry_stop.is_set()


class _WaitSeq:
    """Event stand-in whose wait() returns the queued answers (then True)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.timeouts: List[Any] = []

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        return self.answers.pop(0) if self.answers else True

    def set(self):
        pass

    def is_set(self):
        return False


@pytest.fixture
def registry(monkeypatch):
    reg = MagicMock()
    reg.is_session_ready.return_value = False
    reg.is_helix_forbidden.return_value = False
    reg.sync_from_helix = AsyncMock(return_value=False)
    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", lambda: reg)
    return reg


def test_registry_retry_loop_retries_with_backoff_until_ok(api, registry):
    registry.sync_from_helix = AsyncMock(side_effect=[False, RuntimeError("net"), True])
    api._sub_registry_retry_stop = _WaitSeq([False, False, False])
    api._subscriber_registry_retry_loop()
    assert api._sub_registry_retry_stop.timeouts == [5, 15, 30]
    assert registry.sync_from_helix.await_count == 3


def test_registry_retry_loop_stops_when_forbidden_after_sync(api, registry):
    registry.is_helix_forbidden.side_effect = [False, True]
    api._sub_registry_retry_stop = _WaitSeq([False])
    api._subscriber_registry_retry_loop()
    assert registry.sync_from_helix.await_count == 1


def test_registry_retry_loop_early_exits(api, registry, env):
    registry.is_session_ready.return_value = True
    api._sub_registry_retry_stop = _WaitSeq([False])
    api._subscriber_registry_retry_loop()
    registry.sync_from_helix.assert_not_called()
    env.shutdown = True
    api._sub_registry_retry_stop = _WaitSeq([False])
    api._subscriber_registry_retry_loop()
    api._sub_registry_retry_stop = _WaitSeq([True])  # stopped before first try
    api._subscriber_registry_retry_loop()


def test_eventsub_live_and_reconnecting(api):
    assert api._eventsub_is_reconnecting() is False and api.is_eventsub_live() is False
    api.eventsub = SimpleNamespace(_is_reconnecting=False, active_session=object())
    api.is_connected = True
    assert api.is_eventsub_live() is False
    api._eventsub_subscriptions_ready = True
    assert api.is_eventsub_live() is True


def test_cancel_oauth(api, monkeypatch):
    stop = MagicMock()
    monkeypatch.setattr(tw, "stop_active_oauth", stop)
    api.cancel_oauth()
    stop.assert_not_called()
    api.authenticator = object()
    api.cancel_oauth()
    stop.assert_called_once() and api.authenticator is None


# --------------------------------------------------------------------------- health check loop


@pytest.fixture
def loop_api(api, monkeypatch):
    api._health_stop = _WaitSeq([])
    api._schedule_eventsub_rebuild = MagicMock()
    api.reconnect = MagicMock()
    api._sync_tokens_to_state_manager = MagicMock()
    monkeypatch.setattr(tw, "twitch_connected", False)
    return api


def test_health_loop_stops_on_shutdown(loop_api, env):
    env.shutdown = True
    loop_api._health_check_loop()
    assert loop_api._health_stop.timeouts == []


def test_health_loop_healthy_without_events(loop_api):
    loop_api.eventsub = SimpleNamespace(active_session=object())
    loop_api._eventsub_subscriptions_ready = True
    loop_api._health_check_loop()
    assert loop_api.is_connected is True and tw.twitch_connected is True
    assert loop_api.last_health_check is not None
    assert loop_api._health_stop.timeouts == [60]


def test_health_loop_staleness_triggers_rebuild(loop_api):
    loop_api.eventsub = SimpleNamespace(active_session=object())
    loop_api._eventsub_subscriptions_ready = True
    loop_api.last_event_time = datetime.now() - timedelta(hours=2)
    loop_api._health_check_loop()
    loop_api._schedule_eventsub_rebuild.assert_called_once_with("event staleness")
    assert (datetime.now() - loop_api.last_event_time).total_seconds() < 5


def test_health_loop_recent_event_no_rebuild(loop_api):
    loop_api.eventsub = SimpleNamespace(active_session=object())
    loop_api._eventsub_subscriptions_ready = True
    loop_api.last_event_time = datetime.now()
    loop_api._health_check_loop()
    loop_api._schedule_eventsub_rebuild.assert_not_called()


def test_health_loop_reconnecting_and_dead(loop_api):
    loop_api.is_connected = True
    loop_api.eventsub = SimpleNamespace(_is_reconnecting=True, active_session=None)
    loop_api._health_check_loop()
    assert loop_api.is_connected is True
    loop_api.eventsub = None
    loop_api._health_stop = _WaitSeq([])
    loop_api._health_check_loop()
    assert loop_api.is_connected is False and tw.twitch_connected is False


def _expired(api):
    api.auth_token, api.refresh_token = "old", "ref"
    api.token_expiry = datetime.now() - timedelta(minutes=1)


def test_health_loop_refresh_changes_token_reconnects(loop_api):
    _expired(loop_api)

    async def refresh():
        loop_api.auth_token = "new"
        return True

    loop_api.refresh_auth_token = refresh
    loop_api._health_check_loop()
    loop_api._sync_tokens_to_state_manager.assert_called_once()
    loop_api.reconnect.assert_called_once()


def test_health_loop_refresh_same_token_no_reconnect(loop_api):
    _expired(loop_api)
    loop_api.refresh_auth_token = AsyncMock(return_value=True)
    loop_api._health_check_loop()
    loop_api.reconnect.assert_not_called()


def test_health_loop_refresh_fails_past_expiry_marks_disconnected(loop_api):
    _expired(loop_api)
    loop_api.is_connected = True
    loop_api.refresh_auth_token = AsyncMock(return_value=False)
    loop_api._health_check_loop()
    assert loop_api.is_connected is False


def test_health_loop_refresh_fails_inside_buffer_stays_connected(loop_api):
    loop_api.auth_token, loop_api.refresh_token = "old", "ref"
    loop_api.token_expiry = datetime.now() + timedelta(minutes=2)
    loop_api.eventsub = SimpleNamespace(active_session=object())
    loop_api._eventsub_subscriptions_ready = True
    loop_api.refresh_auth_token = AsyncMock(return_value=False)
    loop_api._health_check_loop()
    assert loop_api.is_connected is True


def test_health_loop_refresh_raises(loop_api):
    _expired(loop_api)
    loop_api.refresh_auth_token = AsyncMock(side_effect=RuntimeError("x"))
    loop_api._health_check_loop()  # logged


def test_health_loop_outer_error_waits_and_retries(loop_api, monkeypatch):
    monkeypatch.setattr(loop_api, "_eventsub_is_reconnecting", MagicMock(side_effect=RuntimeError("bug")))
    loop_api._health_stop = _WaitSeq([False])  # first wait(10) -> retry, then True from the 60s wait? errors again
    loop_api._health_check_loop()
    assert loop_api._health_stop.timeouts == [10, 10]
    assert loop_api.is_connected is False


def test_sync_tokens_to_state_manager(api, env):
    api.token_expiry = FUTURE
    api._sync_tokens_to_state_manager()
    assert env.state.set_twitch_data.call_args.args[0]["token_expiry"] == FUTURE.isoformat()
    env.state.save_changes.return_value = False
    api.token_expiry = None
    api._sync_tokens_to_state_manager()
    assert env.state.set_twitch_data.call_args.args[0]["token_expiry"] == ""
    env.state.set_twitch_data.side_effect = RuntimeError("db")
    api._sync_tokens_to_state_manager()  # logged


# --------------------------------------------------------------------------- revocation / settings


@pytest.fixture
def rev_api(api):
    api.reconnect = MagicMock()
    api._sync_tokens_to_state_manager = MagicMock()
    api.refresh_auth_token = AsyncMock(return_value=True)
    api.refresh_token = "ref"
    return api


def test_revocation_refreshes_and_reconnects(rev_api):
    run(rev_api._on_eventsub_revocation({"sub": 1}))
    rev_api._sync_tokens_to_state_manager.assert_called_once()
    rev_api.reconnect.assert_called_once()
    run(rev_api._on_eventsub_revocation({"sub": 2}))  # debounced
    rev_api.reconnect.assert_called_once()


def test_revocation_shutdown_and_disabled(rev_api, env):
    env.shutdown = True
    run(rev_api._on_eventsub_revocation({}))
    rev_api.refresh_auth_token.assert_not_awaited()
    env.shutdown = False
    env.auto_reconnect = False
    rev_api.refresh_auth_token = AsyncMock(side_effect=RuntimeError("x"))
    run(rev_api._on_eventsub_revocation({}))
    rev_api.reconnect.assert_not_called()


def test_revocation_without_refresh_token(rev_api):
    rev_api.refresh_token = ""
    rev_api.refresh_auth_token = AsyncMock(return_value=False)
    run(rev_api._on_eventsub_revocation({}))
    rev_api.refresh_auth_token.assert_not_awaited()
    rev_api.reconnect.assert_called_once()


def test_auto_reconnect_and_raid_settings(api, env):
    assert api._auto_reconnect_enabled() is True
    env.auto_reconnect = False
    assert api._auto_reconnect_enabled() is False
    env.state.get_app_settings.side_effect = lambda: SimpleNamespace(auto_raid_helix_shoutout=False,
                                                                     auto_raid_chat_shoutout=False)
    assert api._raid_shoutout_enabled("helix") is False
    assert api._raid_shoutout_enabled("chat") is False
    assert api._raid_shoutout_enabled("other") is True
    env.state.get_app_settings.side_effect = RuntimeError("x")
    assert api._auto_reconnect_enabled() is True
    assert api._raid_shoutout_enabled("helix") is True


def test_is_on_eventsub_socket_loop(api):
    assert api._is_on_eventsub_socket_loop() is False
    api.eventsub = SimpleNamespace(_socket_loop=None)
    assert api._is_on_eventsub_socket_loop() is False
    api.eventsub = SimpleNamespace(_socket_loop=object())
    assert api._is_on_eventsub_socket_loop() is False  # no running loop

    async def go():
        api.eventsub = SimpleNamespace(_socket_loop=asyncio.get_running_loop())
        return api._is_on_eventsub_socket_loop()

    assert run(go()) is True


# --------------------------------------------------------------------------- stop eventsub


def test_stop_eventsub_none_and_on_socket_loop(api, monkeypatch):
    api._stop_eventsub_safely()
    es = MagicMock()
    api.eventsub = es
    monkeypatch.setattr(api, "_is_on_eventsub_socket_loop", lambda: True)
    api._stop_eventsub_safely()
    assert api.eventsub is es


@pytest.mark.parametrize(
    "exc",
    [None, TimeoutError(), AttributeError("NoneType has no close"), AttributeError("other"),
     RuntimeError("Event loop is closed"), RuntimeError("weird"), ValueError("x")],
)
def test_stop_eventsub_handles_errors(api, monkeypatch, exc):
    seen = []

    def fake_run(coro, timeout):
        seen.append(timeout)
        coro.close()
        if exc is not None:
            raise exc

    monkeypatch.setattr(tw, "_run_coro_in_thread", fake_run)

    class ES:
        async def stop(self):
            return None

    api.eventsub = ES()
    api._stop_eventsub_safely()
    assert seen == [tw._EVENTSUB_STOP_TIMEOUT] and api.eventsub is None


def test_stop_eventsub_keeps_replacement(api, monkeypatch):
    replacement = object()

    def fake_run(coro, timeout):
        coro.close()
        api.eventsub = replacement

    monkeypatch.setattr(tw, "_run_coro_in_thread", fake_run)

    class ES:
        async def stop(self):
            return None

    api.eventsub = ES()
    api._stop_eventsub_safely()
    assert api.eventsub is replacement


def test_note_event_received(api):
    api._note_event_received()
    assert isinstance(api.last_event_time, datetime)


# --------------------------------------------------------------------------- rebuild / reconnect


def test_schedule_rebuild_gates(api, env, monkeypatch):
    api.reconnect = MagicMock()
    env.shutdown = True
    api._schedule_eventsub_rebuild("r")
    env.shutdown = False
    env.auto_reconnect = False
    api._schedule_eventsub_rebuild("r")
    env.auto_reconnect = True
    env.reachable = False
    api._schedule_eventsub_rebuild("r")
    api.reconnect.assert_not_called()
    env.reachable = True
    api._schedule_eventsub_rebuild("r")
    api._schedule_eventsub_rebuild("r")  # debounced
    api.reconnect.assert_called_once()


def test_schedule_rebuild_connectivity_check_error(api, monkeypatch):
    api.reconnect = MagicMock()
    monkeypatch.setattr(connection_monitor, "is_internet_available", MagicMock(side_effect=RuntimeError("x")))
    api._schedule_eventsub_rebuild("r")
    api.reconnect.assert_called_once()


def test_reconnect_skips_on_shutdown_and_offline(api, env, fake_threading):
    env.shutdown = True
    api.reconnect()
    env.shutdown = False
    env.internet = False
    api.reconnect()
    assert fake_threading.created == [] and api._reconnect_in_progress is False


def test_reconnect_hops_off_socket_loop(api, monkeypatch, fake_threading):
    monkeypatch.setattr(api, "_is_on_eventsub_socket_loop", lambda: True)
    api.reconnect()
    t = fake_threading.created[0]
    assert t.name == "TwitchEventSubReconnect" and t.target == api.reconnect


def test_reconnect_runs_initialize_in_thread(api, monkeypatch, fake_threading):
    api.eventsub = MagicMock()
    stop = MagicMock()
    monkeypatch.setattr(api, "_stop_eventsub_safely", stop)
    init = AsyncMock()
    monkeypatch.setattr(api, "intialize_twitch_api", init)
    monkeypatch.setattr(tw, "twitch_connected", True)
    api.is_connected = True
    api.reconnect()
    stop.assert_called_once()
    assert api.is_connected is False and tw.twitch_connected is False and api._connection_epoch == 1
    assert api._reconnect_in_progress is True
    api.reconnect()  # duplicate skipped
    assert len(fake_threading.created) == 1
    fake_threading.created[0].target()
    init.assert_awaited_once()
    assert api._reconnect_in_progress is False


def test_reconnect_connectivity_error_still_reconnects(api, monkeypatch, fake_threading):
    monkeypatch.setattr(connection_monitor, "is_internet_available", MagicMock(side_effect=RuntimeError("x")))
    api.reconnect()
    assert len(fake_threading.created) == 1


def test_reconnect_shutdown_during_stop(api, env, monkeypatch, fake_threading):
    api.eventsub = MagicMock()

    def stop():
        env.shutdown = True

    calls = {"n": 0}

    def shutdown_flag():
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", shutdown_flag)
    monkeypatch.setattr(api, "_stop_eventsub_safely", stop)
    api.reconnect()
    assert fake_threading.created == [] and api._reconnect_in_progress is False


def test_reconnect_thread_start_error(api, monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no threads")

    monkeypatch.setattr(tw, "threading", SimpleNamespace(Thread=Boom))
    api.reconnect()
    assert api._reconnect_in_progress is False

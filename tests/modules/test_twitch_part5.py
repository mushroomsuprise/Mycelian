# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 5: EventSub initialization, generic Helix calls,
stop / OAuth reconnect and the token / connection status reports (all faked, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import aiohttp  # noqa: E402
import pytest  # noqa: E402

import modules  # noqa: E402
from modules import (  # noqa: E402
    api_credentials_manager,
    connection_monitor,
    shutdown,
    twitch as tw,
    twitch_moderators,
    twitch_subscriber_registry,
)


def run(coro):
    return asyncio.run(coro)


FUTURE = datetime.now() + timedelta(hours=3)


@pytest.fixture
def flags(monkeypatch):
    f = SimpleNamespace(shutdown=False)
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: f.shutdown)
    monkeypatch.setattr(tw, "twitch_connected", False)
    monkeypatch.setattr(tw, "notify_twitch_connect_failed", MagicMock())
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials", lambda: {})
    return f


# --------------------------------------------------------------------------- intialize_twitch_api


class FakeEventSub:
    def __init__(self, twitch, revocation_handler=None):
        self.twitch = twitch
        self.revocation_handler = revocation_handler
        self.started = False
        self.calls: List[str] = []
        self.fail: dict = {}

    def start(self):
        self.started = True

    def __getattr__(self, name):
        if name.startswith("listen_"):
            async def listen(*args, **kw):
                self.calls.append(name)
                if name in self.fail:
                    raise self.fail[name]
                return "id"

            return listen
        raise AttributeError(name)


@pytest.fixture
def init_env(flags, monkeypatch):
    api = tw.Twitch_API()
    api.user = SimpleNamespace(id="42", display_name="S")
    api.twitch = MagicMock()
    created: List[FakeEventSub] = []

    def make_es(twitch, **kw):
        es = FakeEventSub(twitch, **kw)
        created.append(es)
        return es

    monkeypatch.setattr(tw, "EventSubWebsocket", make_es)
    monkeypatch.setattr(api, "stage_twitch_api", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "_ensure_user_authenticated", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "_subscribe_poll_events", AsyncMock())
    monkeypatch.setattr(api, "_seed_current_category", AsyncMock())
    monkeypatch.setattr(api, "start_health_check", MagicMock())
    monkeypatch.setattr(api, "_start_subscriber_registry_retry", MagicMock())
    monkeypatch.setattr(api, "_stop_eventsub_safely", MagicMock(side_effect=lambda: setattr(api, "eventsub", None)))
    reg = MagicMock()
    reg.sync_from_helix = AsyncMock(return_value=True)
    reg.is_helix_forbidden.return_value = False
    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", lambda: reg)
    mods = MagicMock()
    mods.refresh = AsyncMock()
    monkeypatch.setattr(twitch_moderators, "get_moderator_cache", lambda: mods)
    monkeypatch.setattr(tw, "is_twitch_api_ready", lambda: True)
    web = SimpleNamespace(broadcast_overlay_recovery=MagicMock())
    monkeypatch.setitem(sys.modules, "modules.web_engine", web)
    monkeypatch.setattr(modules, "web_engine", web, raising=False)
    monkeypatch.setattr(tw.asyncio, "sleep", AsyncMock())
    return SimpleNamespace(api=api, created=created, reg=reg, mods=mods, web=web, flags=flags)


def test_initialize_success(init_env):
    api = init_env.api
    assert run(api.intialize_twitch_api()) is True
    es = init_env.created[0]
    assert es.started and es.revocation_handler == api._on_eventsub_revocation
    assert "listen_channel_chat_message" in es.calls and "listen_stream_offline" in es.calls
    assert "listen_hype_train_end" in es.calls
    assert api.is_connected and tw.twitch_connected and api._eventsub_subscriptions_ready
    init_env.reg.reset_session_ready.assert_called_once()
    init_env.reg.seed_from_statistics.assert_called_once()
    init_env.mods.refresh.assert_awaited_once_with(force=True)
    init_env.web.broadcast_overlay_recovery.assert_called_once_with("twitch", "reconnected")
    api.start_health_check.assert_called_once()
    api._seed_current_category.assert_awaited_once()
    api._start_subscriber_registry_retry.assert_not_called()


def test_initialize_shutdown_and_staging_failures(init_env):
    api = init_env.api
    init_env.flags.shutdown = True
    assert run(api.intialize_twitch_api()) is False
    init_env.flags.shutdown = False
    api.stage_twitch_api = AsyncMock(return_value=False)
    assert run(api.intialize_twitch_api()) is False
    assert api.is_connected is False


def test_initialize_epoch_changed_after_staging(init_env):
    api = init_env.api

    async def stage():
        api._connection_epoch += 1
        return True

    api.stage_twitch_api = stage
    assert run(api.intialize_twitch_api()) is False
    assert init_env.created == []


def test_initialize_user_auth_not_verified(init_env):
    api = init_env.api
    api._ensure_user_authenticated = AsyncMock(return_value=False)
    assert run(api.intialize_twitch_api()) is False


def test_initialize_old_twitchapi_without_revocation_kwarg(init_env, monkeypatch):
    api = init_env.api

    class OldES(FakeEventSub):
        def __init__(self, twitch, **kw):
            if kw:
                raise TypeError("unexpected kwarg")
            super().__init__(twitch)

    monkeypatch.setattr(tw, "EventSubWebsocket", OldES)
    assert run(api.intialize_twitch_api()) is True
    assert api.eventsub.revocation_handler == api._on_eventsub_revocation


def test_initialize_old_twitchapi_handler_cannot_be_set(init_env, monkeypatch):
    api = init_env.api

    class OldES(FakeEventSub):
        def __init__(self, twitch, **kw):
            if kw:
                raise TypeError("unexpected kwarg")
            object.__setattr__(self, "calls", [])
            object.__setattr__(self, "fail", {})
            object.__setattr__(self, "started", False)

        def __setattr__(self, name, value):
            raise AttributeError("read only")

        def start(self):
            object.__setattr__(self, "started", True)

    monkeypatch.setattr(tw, "EventSubWebsocket", OldES)
    assert run(api.intialize_twitch_api()) is True


def test_initialize_registry_reset_error_and_epoch_change_after_start(init_env, monkeypatch):
    api = init_env.api
    calls = {"n": 0}

    def get_reg():
        calls["n"] += 1
        if calls["n"] == 1:
            api._connection_epoch += 1  # a reconnect happened while starting
            raise RuntimeError("reg")
        return init_env.reg

    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", get_reg)
    assert run(api.intialize_twitch_api()) is False
    api._stop_eventsub_safely.assert_called_once()


@pytest.mark.parametrize(
    "topic",
    ["listen_channel_chat_message", "listen_channel_chat_notification", "listen_channel_moderate",
     "listen_channel_update", "listen_channel_follow_v2", "listen_channel_subscription_message",
     "listen_channel_subscribe", "listen_channel_cheer"],
)
def test_initialize_required_subscription_failure(init_env, monkeypatch, topic):
    api = init_env.api
    orig = tw.EventSubWebsocket

    def make(twitch, **kw):
        es = orig(twitch, **kw)
        es.fail[topic] = RuntimeError("409 conflict")
        return es

    monkeypatch.setattr(tw, "EventSubWebsocket", make)
    epoch = api._connection_epoch
    assert run(api.intialize_twitch_api()) is False
    assert api._connection_epoch == epoch + 1
    tw.notify_twitch_connect_failed.assert_called_once()
    assert tw.notify_twitch_connect_failed.call_args.args[0] == "eventsub"


@pytest.mark.parametrize("topic", ["listen_channel_subscription_end", "listen_hype_train_begin"])
def test_initialize_optional_subscription_failure(init_env, monkeypatch, topic):
    api = init_env.api
    orig = tw.EventSubWebsocket

    def make(twitch, **kw):
        es = orig(twitch, **kw)
        es.fail[topic] = RuntimeError("gone")
        return es

    monkeypatch.setattr(tw, "EventSubWebsocket", make)
    assert run(api.intialize_twitch_api()) is True


def test_initialize_subscription_failure_after_replacement(init_env, monkeypatch):
    """If another reconnect already replaced the websocket, the failing one isn't stopped."""
    api = init_env.api
    orig = tw.EventSubWebsocket

    def make(twitch, **kw):
        es = orig(twitch, **kw)

        async def listen(*a):
            api.eventsub = object()
            raise RuntimeError("x")

        es.listen_channel_chat_message = listen
        return es

    monkeypatch.setattr(tw, "EventSubWebsocket", make)
    assert run(api.intialize_twitch_api()) is False
    api._stop_eventsub_safely.assert_not_called()


def test_initialize_shutdown_after_subscribing(init_env):
    api = init_env.api

    async def poll(es):
        init_env.flags.shutdown = True

    api._subscribe_poll_events = poll
    assert run(api.intialize_twitch_api()) is False
    api._stop_eventsub_safely.assert_called_once()
    assert api.is_connected is False


def test_initialize_post_connect_fallbacks(init_env, monkeypatch):
    api = init_env.api
    monkeypatch.setattr(tw, "is_twitch_api_ready", lambda: False)
    init_env.web.broadcast_overlay_recovery.side_effect = RuntimeError("x")
    init_env.reg.seed_from_statistics.side_effect = RuntimeError("seed")
    init_env.reg.sync_from_helix = AsyncMock(return_value=False)
    assert run(api.intialize_twitch_api()) is True
    init_env.mods.refresh.assert_not_awaited()
    assert tw.asyncio.sleep.await_count == 20
    api._start_subscriber_registry_retry.assert_called_once()


def test_initialize_registry_forbidden_and_errors(init_env, monkeypatch):
    api = init_env.api
    init_env.reg.sync_from_helix = AsyncMock(return_value=False)
    init_env.reg.is_helix_forbidden.return_value = True
    init_env.mods.refresh = AsyncMock(side_effect=RuntimeError("mods"))
    assert run(api.intialize_twitch_api()) is True
    api._start_subscriber_registry_retry.assert_not_called()
    init_env.reg.sync_from_helix = AsyncMock(side_effect=RuntimeError("helix"))
    assert run(api.intialize_twitch_api()) is True
    api._start_subscriber_registry_retry.assert_called_once()


def test_initialize_unexpected_error(init_env):
    api = init_env.api
    api.stage_twitch_api = AsyncMock(side_effect=RuntimeError("boom"))
    assert run(api.intialize_twitch_api()) is False
    api._stop_eventsub_safely.assert_called_once()


# --------------------------------------------------------------------------- generic_api_call


class FakeResponse:
    def __init__(self, status=200, data=None, content_type="application/json"):
        self.status = status
        self._data = {} if data is None else data
        self.content_type = content_type

    async def json(self):
        return self._data

    async def text(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: List[Any] = []

    def request(self, method, url, params=None, json=None, headers=None):
        self.requests.append(SimpleNamespace(method=method, url=url, params=params, json=json,
                                             headers=dict(headers)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def helix(flags, monkeypatch):
    api = tw.Twitch_API()
    api.client_id, api.client_secret = "cid", "sec"
    api.auth_token, api.refresh_token = "tok", "ref"
    api.token_expiry = FUTURE
    monkeypatch.setattr(api, "sync_client_credentials", lambda: None)
    monkeypatch.setattr(api, "_schedule_eventsub_rebuild", MagicMock())
    monkeypatch.setattr(tw, "_helix_session_usable", lambda s: True)
    return api


def _with_session(api, *responses):
    session = FakeSession(responses)
    api.twitch = SimpleNamespace(_Twitch__session=session)
    return session


def test_generic_get_json(helix):
    session = _with_session(helix, FakeResponse(200, {"data": [1]}))
    out = run(helix.generic_api_call("https://api.twitch.tv/helix/x", "get", params={"a": 1},
                                     headers={"X-Extra": 1, "Client-ID": "evil", "authorization": "x"}))
    assert out == {"data": [1]}
    req = session.requests[0]
    assert req.method == "GET" and req.params == {"a": 1}
    assert req.headers == {"X-Extra": "1", "Client-ID": "cid", "Authorization": "Bearer tok"}


def test_generic_post_non_json_response(helix):
    session = _with_session(helix, FakeResponse(204, "", content_type="text/plain"))
    out = run(helix.generic_api_call("u", "POST", json_data={"b": 2}))
    assert out == {"status": 204, "data": ""}
    assert session.requests[0].headers["Content-Type"] == "application/json"


def test_generic_skips_anonymous_user_lookup(helix):
    with pytest.raises(Exception, match="User lookup skipped"):
        run(helix.generic_api_call("https://api.twitch.tv/helix/users", params={"login": "anonymous"}))


def test_generic_loads_auth_when_missing(helix, monkeypatch):
    helix.auth_token = ""
    monkeypatch.setattr(helix, "load_auth_data", MagicMock(return_value=False))
    with pytest.raises(Exception, match="Authentication required - no valid tokens"):
        run(helix.generic_api_call("u"))

    def load():
        helix.auth_token = "loaded"
        return True

    monkeypatch.setattr(helix, "load_auth_data", load)
    session = _with_session(helix, FakeResponse(200, {"ok": 1}))
    assert run(helix.generic_api_call("u")) == {"ok": 1}
    assert session.requests[0].headers["Authorization"] == "Bearer loaded"


def test_generic_expired_token_refresh(helix, monkeypatch):
    helix.token_expiry = None
    helix.client_secret = ""
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials", lambda: {"client_secret": "S"})
    monkeypatch.setattr(helix, "refresh_auth_token", AsyncMock(return_value=False))
    with pytest.raises(Exception, match="token refresh failed"):
        run(helix.generic_api_call("u"))
    helix.refresh_auth_token = AsyncMock(return_value=True)
    _with_session(helix, FakeResponse(200, {"ok": 1}))
    assert run(helix.generic_api_call("u")) == {"ok": 1}


def test_generic_expired_without_secret_still_calls(helix):
    helix.token_expiry = None
    helix.client_secret = ""
    _with_session(helix, FakeResponse(200, {"ok": 1}))
    assert run(helix.generic_api_call("u")) == {"ok": 1}


def test_generic_token_missing_after_refresh(helix, monkeypatch):
    helix.token_expiry = None

    async def refresh():
        helix.auth_token = ""
        return True

    helix.refresh_auth_token = refresh
    with pytest.raises(Exception, match="Authentication token missing"):
        run(helix.generic_api_call("u"))


def test_generic_403(helix):
    _with_session(helix, FakeResponse(403, {"message": "no"}))
    with pytest.raises(tw.TwitchPermissionError):
        run(helix.generic_api_call("u"))


def test_generic_500(helix):
    _with_session(helix, FakeResponse(500, "oops", content_type="text/plain"))
    with pytest.raises(Exception, match="Twitch API Error 500"):
        run(helix.generic_api_call("u"))


@pytest.mark.parametrize(
    "retry, expected",
    [
        (FakeResponse(200, {"ok": 2}), {"ok": 2}),
        (FakeResponse(204, "", content_type="text/plain"), {"status": 204, "data": ""}),
    ],
)
def test_generic_401_refresh_and_retry_success(helix, monkeypatch, retry, expected):
    async def refresh():
        helix.auth_token = "new"
        return True

    helix.refresh_auth_token = refresh
    apply = AsyncMock(side_effect=RuntimeError("auth update"))
    monkeypatch.setattr(tw, "apply_user_authentication", apply)
    session = _with_session(helix, FakeResponse(401, {}), retry)
    assert run(helix.generic_api_call("u")) == expected
    assert session.requests[1].headers["Authorization"] == "Bearer new"
    helix._schedule_eventsub_rebuild.assert_called_once()
    apply.assert_awaited_once()


def test_generic_401_retry_403_and_error(helix, monkeypatch):
    helix.refresh_auth_token = AsyncMock(return_value=True)
    monkeypatch.setattr(tw, "apply_user_authentication", AsyncMock())
    _with_session(helix, FakeResponse(401, {}), FakeResponse(403, {}))
    with pytest.raises(tw.TwitchPermissionError):
        run(helix.generic_api_call("u"))
    _with_session(helix, FakeResponse(401, {}), FakeResponse(500, {}))
    with pytest.raises(Exception, match="Authentication failed - Twitch API Error 500"):
        run(helix.generic_api_call("u"))


def test_generic_401_refresh_fails(helix):
    helix.refresh_auth_token = AsyncMock(return_value=False)
    _with_session(helix, FakeResponse(401, {}))
    with pytest.raises(Exception, match="token refresh unsuccessful"):
        run(helix.generic_api_call("u"))


def test_generic_uses_ephemeral_session_when_unusable(helix, monkeypatch):
    monkeypatch.setattr(tw, "_helix_session_usable", lambda s: False)
    session = FakeSession([FakeResponse(200, {"e": 1})])

    @asynccontextmanager
    async def ephemeral():
        yield session

    monkeypatch.setattr(tw, "_ephemeral_client_session", ephemeral)
    helix.twitch = SimpleNamespace(_Twitch__session=object())
    assert run(helix.generic_api_call("u")) == {"e": 1}
    helix.twitch = None
    session.responses.append(FakeResponse(200, {"e": 2}))
    assert run(helix.generic_api_call("u")) == {"e": 2}


def test_generic_session_not_ready_when_no_token_after_checks(helix, monkeypatch):
    """With a library client but no auth token at the session stage, the call is deferred."""
    monkeypatch.setattr(tw, "_helix_session_usable", lambda s: False)
    helix.twitch = SimpleNamespace()
    real_checks = {"n": 0}

    class FlakyToken(str):
        def __bool__(self):
            real_checks["n"] += 1
            return real_checks["n"] < 4

    helix.auth_token = FlakyToken("tok")
    with pytest.raises(tw.TwitchSessionNotReadyError):
        run(helix.generic_api_call("u"))


def test_generic_library_client_absent_and_no_token_uses_ephemeral(helix, monkeypatch):
    monkeypatch.setattr(tw, "_helix_session_usable", lambda s: False)
    helix.twitch = None
    session = FakeSession([FakeResponse(200, {"z": 1})])

    @asynccontextmanager
    async def ephemeral():
        yield session

    monkeypatch.setattr(tw, "_ephemeral_client_session", ephemeral)
    checks = {"n": 0}

    class FlakyToken(str):
        def __bool__(self):
            checks["n"] += 1
            return checks["n"] < 4

    helix.auth_token = FlakyToken("tok")
    assert run(helix.generic_api_call("u")) == {"z": 1}


@pytest.mark.parametrize("online", [True, False])
def test_generic_client_error(helix, monkeypatch, online):
    monkeypatch.setattr(connection_monitor, "is_internet_available", lambda *a, **k: online)
    _with_session(helix, aiohttp.ClientError("reset"))
    with pytest.raises(Exception, match="Network error during API call"):
        run(helix.generic_api_call("u"))


def test_generic_client_error_connectivity_check_fails(helix, monkeypatch):
    monkeypatch.setattr(connection_monitor, "is_internet_available", MagicMock(side_effect=RuntimeError("x")))
    _with_session(helix, aiohttp.ClientError("reset"))
    with pytest.raises(Exception, match="Network error"):
        run(helix.generic_api_call("u"))


def test_generic_other_error_reraised(helix):
    _with_session(helix, ValueError("bad"))
    with pytest.raises(ValueError):
        run(helix.generic_api_call("u"))


# --------------------------------------------------------------------------- stop / reconnect with oauth


def test_stop_connection(flags, monkeypatch):
    api = tw.Twitch_API()
    api.eventsub = MagicMock()
    api.twitch = SimpleNamespace(_Twitch__session=object())
    monkeypatch.setattr(api, "_stop_eventsub_safely", MagicMock())
    monkeypatch.setattr(api, "stop_health_check", MagicMock())
    api.is_connected = True
    assert api.stop_connection() is True
    api._stop_eventsub_safely.assert_called_once()
    assert api.eventsub is None and api.is_connected is False and api._connection_epoch == 1
    api.stop_health_check.side_effect = RuntimeError("x")
    assert api.stop_connection() is False


def test_reconnect_with_oauth(flags, monkeypatch):
    api = tw.Twitch_API()
    api.auth_token = "old"
    monkeypatch.setattr(api, "stop_connection", MagicMock())
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials",
                        lambda: {"client_id": "C", "client_secret": "S"})
    monkeypatch.setattr(api, "authenticate_with_oauth", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "intialize_twitch_api", AsyncMock(return_value=True))
    assert run(api.reconnect_with_oauth()) is True
    assert (api.client_id, api.client_secret, api.auth_token) == ("C", "S", "")
    api.intialize_twitch_api = AsyncMock(return_value=False)
    assert run(api.reconnect_with_oauth()) is False
    api.authenticate_with_oauth = AsyncMock(return_value=False)
    assert run(api.reconnect_with_oauth()) is False
    tw.notify_twitch_connect_failed.assert_called_with("signin_failed")
    api.authenticate_with_oauth = AsyncMock(side_effect=RuntimeError("x"))
    assert run(api.reconnect_with_oauth()) is False
    assert tw.notify_twitch_connect_failed.call_args.args == ("signin_failed", "RuntimeError: x")


def test_reconnect_with_oauth_keeps_old_creds_when_store_empty(flags, monkeypatch):
    api = tw.Twitch_API()
    api.client_id, api.client_secret = "c", "s"
    monkeypatch.setattr(api, "stop_connection", MagicMock())
    monkeypatch.setattr(api, "authenticate_with_oauth", AsyncMock(return_value=False))
    run(api.reconnect_with_oauth())
    assert (api.client_id, api.client_secret) == ("c", "s")


# --------------------------------------------------------------------------- status


def test_get_token_status(flags):
    api = tw.Twitch_API()
    s = api.get_token_status()
    assert s["has_auth_token"] is False and s["token_expiry"] is None and s["expires_in_minutes"] is None
    api.auth_token = "t"
    api.token_expiry = datetime.now() + timedelta(minutes=90)
    api._last_refresh_attempt = datetime(2026, 1, 1)
    s = api.get_token_status()
    assert 88 <= s["expires_in_minutes"] <= 90 and s["last_refresh_attempt"] == "2026-01-01T00:00:00"
    api.token_expiry = "bad"  # isoformat missing -> error dict
    assert "error" in api.get_token_status()


def test_get_connection_status_variants(flags):
    api = tw.Twitch_API()
    assert api.get_connection_status()["status"] == "Not Configured"
    api.client_id, api.client_secret = "c", "s"
    assert api.get_connection_status()["status"] == "Configured but Not Authenticated"
    api.auth_token, api.refresh_token = "t", "r"
    st = api.get_connection_status()
    assert st["status"] == "Authenticated but Disconnected" and st["user_name"] == "Unknown"
    assert st["token_expires_countdown"] == "Unknown"
    api.user = SimpleNamespace(display_name="Me", profile_image_url="https://p")
    api.eventsub = SimpleNamespace(_is_reconnecting=False, active_session=object())
    api.is_connected = True
    api._eventsub_subscriptions_ready = True
    st = api.get_connection_status()
    assert st["status"] == "Connected" and st["degraded"] is False and st["last_update"] == "Unknown"
    assert st["avatar_url"] == "https://p"
    api.last_health_check = datetime(2026, 1, 2, 3, 4, 5)
    api.last_event_time = datetime.now() - timedelta(hours=2)
    st = api.get_connection_status()
    assert st["status"] == "Degraded (no recent events)" and st["last_update"] == "2026-01-02 03:04:05"


def test_get_connection_status_live_without_user(flags):
    api = tw.Twitch_API()
    api.eventsub = SimpleNamespace(_is_reconnecting=True)
    st = api.get_connection_status()
    assert st["user_name"] == "Unknown" and st["avatar_url"] == ""

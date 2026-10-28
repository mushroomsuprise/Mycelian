# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 6: channel points currency icon and custom rewards,
sync coroutine helpers, module initialization, notices and the module-level status helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import (  # noqa: E402
    api_credentials_manager as acm,
    connection_monitor,
    connection_status_tracker,
    dataobjects,
    notification_engine,
    twitch as tw,
    twitch_oauth,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(tw, "_CHANNEL_POINTS_ICON_CACHE", {})
    monkeypatch.setattr(tw, "_channel_points_unlocked", None)
    monkeypatch.setattr(tw, "_channel_points_fetch_lock", threading.Lock())
    monkeypatch.setattr(tw, "twitch_api", None)


def _api(**kw):
    a = MagicMock()
    a.is_connected = True
    a.user_id = "42"
    a.generic_api_call = AsyncMock(return_value={"data": []})
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# --------------------------------------------------------------------------- icon parsing


def test_normalize_points_image():
    assert tw._normalize_points_image(None) is None
    assert tw._normalize_points_image("x") is None
    assert tw._normalize_points_image({}) is None
    assert tw._normalize_points_image({"other": 1}) is None
    assert tw._normalize_points_image({"url_2x": "b"}) == {"url_1x": "b", "url_2x": "b", "url_4x": "b"}
    assert tw._normalize_points_image({"url": "a", "url4x": "d"}) == {"url_1x": "a", "url_2x": "a", "url_4x": "d"}


def test_icon_from_settings_and_gql():
    assert tw._icon_from_channel_points_settings(None) is None
    assert tw._icon_from_channel_points_settings({"image": None, "defaultImage": {"url": "d"}})["url_1x"] == "d"
    assert tw._icon_from_channel_points_gql_response({}) is None
    assert tw._icon_from_channel_points_gql_response([1]) is None
    assert tw._icon_from_channel_points_gql_response([{"data": {"channel": "bad"}}]) is None
    payload = [{"errors": ["e"], "data": {"channel": {"communityPointsSettings": {"image": {"url": "c"}}}}}]
    assert tw._icon_from_channel_points_gql_response(payload)["url_1x"] == "c"


def test_clear_channel_points_icon_cache():
    tw._CHANNEL_POINTS_ICON_CACHE.update({"1": (0, {}), "2": (0, {})})
    tw.clear_channel_points_icon_cache(1)
    assert list(tw._CHANNEL_POINTS_ICON_CACHE) == ["2"]
    tw.clear_channel_points_icon_cache()
    assert tw._CHANNEL_POINTS_ICON_CACHE == {}


def test_helix_fallback_icon(monkeypatch):
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", AsyncMock(return_value=(None, "error")))
    assert run(tw._helix_fallback_channel_points_icon()) is None
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async",
                        AsyncMock(return_value=([{"default_image": None}, {"default_image": {"url_1x": "r"}}], "ok")))
    assert run(tw._helix_fallback_channel_points_icon())["url_1x"] == "r"
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async",
                        AsyncMock(return_value=([{"default_image": None}], "ok")))
    assert run(tw._helix_fallback_channel_points_icon()) is None


# --------------------------------------------------------------------------- icon fetch


class _Resp:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload

    async def json(self):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _gql(monkeypatch, status=200, payload=None, error=None, calls=None):
    class Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None, headers=None, timeout=None):
            if calls is not None:
                calls.append((url, json, headers))
            if error:
                raise error
            return _Resp(status, payload)

    monkeypatch.setattr(tw.aiohttp, "ClientSession", Session)


GQL_OK = [{"data": {"channel": {"communityPointsSettings": {"image": {"url": "u1"}}}}}]


def test_fetch_icon_guards(monkeypatch):
    assert run(tw.fetch_channel_points_currency_icon_async("")) is None
    monkeypatch.setattr(tw, "_channel_points_unlocked", False)
    assert run(tw.fetch_channel_points_currency_icon_async("1")) is None


def test_fetch_icon_gql_then_cached(monkeypatch):
    calls = []
    _gql(monkeypatch, 200, GQL_OK, calls=calls)
    icon = run(tw.fetch_channel_points_currency_icon_async("7"))
    assert icon["url_1x"] == "u1"
    url, payload, headers = calls[0]
    assert url == "https://gql.twitch.tv/gql" and payload[0]["variables"] == {"channelID": "7"}
    assert headers["Client-Id"] == tw._TWITCH_GQL_CLIENT_ID and "Authorization" not in headers
    assert run(tw.fetch_channel_points_currency_icon_async("7")) == icon
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["http", "error"])
def test_fetch_icon_falls_back_to_helix(monkeypatch, kind):
    if kind == "http":
        _gql(monkeypatch, 500, None)
    else:
        _gql(monkeypatch, error=RuntimeError("dns"))
    monkeypatch.setattr(tw, "_helix_fallback_channel_points_icon", AsyncMock(return_value=None))
    assert run(tw.fetch_channel_points_currency_icon_async("7")) is None
    assert tw._CHANNEL_POINTS_ICON_CACHE == {}


def test_fetch_icon_sync_wrapper(monkeypatch):
    monkeypatch.setattr(tw, "fetch_channel_points_currency_icon_async", AsyncMock(return_value={"url_1x": "x"}))
    assert tw.fetch_channel_points_currency_icon("1") == {"url_1x": "x"}
    monkeypatch.setattr(tw, "_run_twitch_coro_sync", MagicMock(side_effect=RuntimeError("x")))
    assert tw.fetch_channel_points_currency_icon("1") is None


@pytest.mark.parametrize(
    "exc, expected",
    [(tw.TwitchPermissionError("x"), True), (RuntimeError("must be Partner or Affiliate"), True),
     (RuntimeError("403 Forbidden"), True), (RuntimeError("500"), False)],
)
def test_is_channel_points_forbidden(exc, expected):
    assert tw._is_channel_points_forbidden(exc) is expected


# --------------------------------------------------------------------------- coroutine helpers


def test_run_coro_in_thread():
    async def ok():
        return threading.current_thread().name

    assert tw._run_coro_in_thread(ok()) == "TwitchCoroSync"

    async def bad():
        raise ValueError("x")

    with pytest.raises(ValueError):
        tw._run_coro_in_thread(bad())


def test_run_twitch_coro_sync_with_and_without_loop():
    async def val():
        return 5

    assert tw._run_twitch_coro_sync(val()) == 5

    async def outer():
        return tw._run_twitch_coro_sync(val())

    assert run(outer()) == 5


# --------------------------------------------------------------------------- rewards


def test_fetch_rewards_async_states(monkeypatch):
    monkeypatch.setattr(tw, "_channel_points_unlocked", False)
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "not_unlocked")
    monkeypatch.setattr(tw, "_channel_points_unlocked", None)
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "not_connected")
    api = _api(generic_api_call=AsyncMock(return_value={"data": [{"id": "r"}]}))
    monkeypatch.setattr(tw, "twitch_api", api)
    assert run(tw._fetch_channel_point_rewards_async()) == ([{"id": "r"}], "ok")
    assert tw._channel_points_unlocked is True
    assert "broadcaster_id=42" in api.generic_api_call.await_args.args[0]
    api.generic_api_call = AsyncMock(return_value={})
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "error")
    api.generic_api_call = AsyncMock(side_effect=RuntimeError("500"))
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "error")
    api.generic_api_call = AsyncMock(side_effect=tw.TwitchPermissionError("403"))
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "not_unlocked")
    assert tw._channel_points_unlocked is False
    assert not tw._channel_points_fetch_lock.locked()


def test_fetch_rewards_async_waits_for_lock_and_rechecks(monkeypatch):
    monkeypatch.setattr(tw, "twitch_api", _api())
    tw._channel_points_fetch_lock.acquire()
    real_sleep = asyncio.sleep

    async def fake_sleep(_d):
        tw._channel_points_unlocked = False  # decided by the other caller
        tw._channel_points_fetch_lock.release()
        await real_sleep(0)

    monkeypatch.setattr(tw.asyncio, "sleep", fake_sleep)
    assert run(tw._fetch_channel_point_rewards_async()) == (None, "not_unlocked")


def test_get_point_rewards_async(monkeypatch):
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", AsyncMock(return_value=([1], "ok")))
    assert run(tw.get_point_rewards_async()) == [1]
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", AsyncMock(return_value=(None, "error")))
    assert run(tw.get_point_rewards_async()) is None


def test_fetch_channel_point_rewards_sync(monkeypatch):
    assert tw.fetch_channel_point_rewards() == {"rewards": None, "status": "not_connected"}
    monkeypatch.setattr(tw, "twitch_api", _api())
    monkeypatch.setattr(tw, "_channel_points_unlocked", False)
    assert tw.fetch_channel_point_rewards()["status"] == "not_unlocked"
    monkeypatch.setattr(tw, "_channel_points_unlocked", None)
    for result, expected in [((None, "ok"), {"rewards": [], "status": "ok"}),
                             (([1], "ok"), {"rewards": [1], "status": "ok"}),
                             ((None, "error"), {"rewards": None, "status": "error"})]:
        monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", AsyncMock(return_value=result))
        assert tw.fetch_channel_point_rewards() == expected
    assert tw._channel_points_unlocked is True
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", AsyncMock(return_value=(None, "not_unlocked")))
    assert tw.fetch_channel_point_rewards()["status"] == "not_unlocked"
    assert tw._channel_points_unlocked is False
    monkeypatch.setattr(tw, "_channel_points_unlocked", None)
    monkeypatch.setattr(tw, "_run_twitch_coro_sync", MagicMock(side_effect=RuntimeError("x")))
    coro_fn = AsyncMock()
    monkeypatch.setattr(tw, "_fetch_channel_point_rewards_async", coro_fn)
    assert tw.fetch_channel_point_rewards() == {"rewards": None, "status": "error"}


def test_get_point_rewards(monkeypatch):
    monkeypatch.setattr(tw, "fetch_channel_point_rewards", lambda: {"status": "ok", "rewards": [1]})
    assert tw.get_point_rewards() == [1]
    monkeypatch.setattr(tw, "fetch_channel_point_rewards", lambda: {"status": "error", "rewards": None})
    assert tw.get_point_rewards() is None


def test_get_point_reward_by_id(monkeypatch):
    assert tw.get_point_reward_by_id("r1") is None  # not connected
    api = _api(generic_api_call=AsyncMock(return_value={"data": [{"id": "r1"}]}))
    monkeypatch.setattr(tw, "twitch_api", api)
    for bad in ("", "new", "loading", "no_channel_points"):
        assert tw.get_point_reward_by_id(bad) is None
    assert tw.get_point_reward_by_id("r1") == {"id": "r1"}
    assert api.generic_api_call.await_args.args[0].endswith("&id=r1")
    api.generic_api_call = AsyncMock(return_value={"data": []})
    assert tw.get_point_reward_by_id("r1") is None
    api.generic_api_call = AsyncMock(side_effect=RuntimeError("x"))
    assert tw.get_point_reward_by_id("r1") is None


def test_create_point_reward(monkeypatch):
    assert tw.create_point_reward({}) is None
    api = _api(generic_api_call=AsyncMock(return_value={"data": [{"id": "new"}]}))
    monkeypatch.setattr(tw, "twitch_api", api)
    assert tw.create_point_reward({"title": "T", "cost": "50"}) == {"id": "new"}
    url, method = api.generic_api_call.await_args.args
    assert method == "POST" and api.generic_api_call.await_args.kwargs["json_data"]["cost"] == 50
    api.generic_api_call = AsyncMock(return_value={"data": []})
    assert tw.create_point_reward({}) is None
    api.generic_api_call = AsyncMock(side_effect=RuntimeError("x"))
    assert tw.create_point_reward({}) is None


@pytest.mark.parametrize("fn, args, method", [(tw.update_point_reward, ("r", {"title": "x"}), "PATCH"),
                                              (tw.delete_point_reward, ("r",), "DELETE")])
def test_update_and_delete_point_reward(monkeypatch, fn, args, method):
    assert fn(*args) is False
    api = _api(generic_api_call=AsyncMock(return_value={}))
    monkeypatch.setattr(tw, "twitch_api", api)
    assert fn(*args) is True
    assert api.generic_api_call.await_args.args[1] == method
    api.generic_api_call = AsyncMock(return_value=None)
    assert fn(*args) is False
    api.generic_api_call = AsyncMock(side_effect=RuntimeError("x"))
    assert fn(*args) is False


def test_convert_ui_to_api_format():
    minimal = tw._convert_ui_to_api_format({})
    assert minimal == {
        "title": "", "cost": 100, "is_enabled": True, "is_user_input_required": False,
        "should_redemptions_skip_request_queue": True, "max_per_stream_setting": {"is_enabled": False},
        "max_per_user_per_stream_setting": {"is_enabled": False}, "global_cooldown_setting": {"is_enabled": False}}
    full = tw._convert_ui_to_api_format({
        "title": "T", "cost": 5, "is_user_input_required": True, "prompt": "say",
        "max_per_stream": {"is_enabled": True, "max_per_stream": 3},
        "max_per_user_per_stream": {"is_enabled": True},
        "global_cooldown": {"is_enabled": True, "global_cooldown_seconds": 30},
    })
    assert full["prompt"] == "say"
    assert full["max_per_stream_setting"] == {"is_enabled": True, "max_per_stream": 3}
    assert full["max_per_user_per_stream_setting"] == {"is_enabled": True, "max_per_user_per_stream": 1}
    assert full["global_cooldown_setting"] == {"is_enabled": True, "global_cooldown_seconds": 30}


# --------------------------------------------------------------------------- initialize


class _FakeThread:
    created: List["_FakeThread"] = []

    def __init__(self, target=None, name=None, daemon=None, **kw):
        self.target, self.name = target, name
        _FakeThread.created.append(self)

    def start(self):
        pass


def test_initialize_main_process_once(monkeypatch):
    _FakeThread.created = []
    monkeypatch.setattr(tw, "_initialized", False)
    monkeypatch.setattr(tw, "threading", SimpleNamespace(Thread=_FakeThread, Lock=threading.Lock,
                                                         Event=threading.Event))
    monkeypatch.setattr(tw.multiprocessing, "current_process", lambda: SimpleNamespace(name="MainProcess", pid=1))
    tw.initialize()
    assert isinstance(tw.twitch_api, tw.Twitch_API) and tw._initialized is True
    assert _FakeThread.created[0].name == "TwitchInit"
    first = tw.twitch_api
    tw.initialize()
    assert tw.twitch_api is first and len(_FakeThread.created) == 1


def test_initialize_skips_child_process(monkeypatch):
    monkeypatch.setattr(tw, "_initialized", False)
    monkeypatch.setattr(tw.multiprocessing, "current_process", lambda: SimpleNamespace(name="Child", pid=2))
    tw.initialize()
    assert tw.twitch_api is None and tw._initialized is False


def test_run_twitch_init_sets_staging_complete(monkeypatch):
    ev = threading.Event()
    monkeypatch.setattr(tw, "_staging_complete", ev)
    api = MagicMock()
    api.intialize_twitch_api = AsyncMock(side_effect=RuntimeError("x"))
    monkeypatch.setattr(tw, "twitch_api", api)
    with pytest.raises(RuntimeError):
        tw._run_twitch_init()
    assert ev.is_set() and tw.wait_for_staging_complete(timeout=0) is True


def test_get_twitch_api_and_ready(monkeypatch):
    assert tw.get_twitch_api() is None and tw.is_twitch_api_ready() is False
    api = SimpleNamespace(is_connected=False, twitch=None)
    monkeypatch.setattr(tw, "twitch_api", api)
    assert tw.get_twitch_api() is api and tw.is_twitch_api_ready() is False
    api.is_connected, api.twitch = True, SimpleNamespace()
    assert tw.is_twitch_api_ready() is False
    api.twitch = SimpleNamespace(_Twitch__session=object())
    assert tw.is_twitch_api_ready() is True


def test_can_proxy_helix_api_requests(monkeypatch):
    assert tw.can_proxy_helix_api_requests() is False
    api = MagicMock(auth_token="", client_id="c")

    def load():
        api.auth_token = "t"

    api.load_auth_data.side_effect = load
    monkeypatch.setattr(tw, "twitch_api", api)
    assert tw.can_proxy_helix_api_requests() is True
    api.sync_client_credentials.side_effect = RuntimeError("x")
    assert tw.can_proxy_helix_api_requests() is False


# --------------------------------------------------------------------------- notices


@pytest.fixture
def notes(monkeypatch):
    calls: List[Any] = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: calls.append({"message": msg, **kw}))
    return calls


@pytest.mark.parametrize("reason, key", [("signin_needed", "twitch:signin_prompt"),
                                         ("client_id_changed", "twitch:signin_prompt"),
                                         ("signin_failed", "twitch:signin_failed"),
                                         ("eventsub", "twitch:eventsub_failed"),
                                         ("unknown", "twitch:signin_prompt")])
def test_notify_twitch_connect_failed(notes, reason, key):
    tw.notify_twitch_connect_failed(reason, "d")
    assert notes[-1]["dedupe_key"] == key and notes[-1]["details"] == "d" and notes[-1]["source"] == "twitch"


def test_notify_twitch_connect_failed_error(monkeypatch):
    monkeypatch.setattr(notification_engine, "notify", MagicMock(side_effect=RuntimeError("x")))
    tw.notify_twitch_connect_failed()


def test_notify_client_credentials(notes, monkeypatch):
    tw._notify_twitch_client_credentials("missing")
    assert notes[-1]["dedupe_key"] == "twitch:no_client_secret" and notes[-1]["type"] == "warning"
    tw._notify_twitch_client_credentials("rejected", "")
    assert notes[-1]["dedupe_key"] == "twitch:bad_client_credentials" and notes[-1]["details"] is None
    monkeypatch.setattr(notification_engine, "notify", MagicMock(side_effect=RuntimeError("x")))
    tw._notify_twitch_client_credentials("missing")


# --------------------------------------------------------------------------- OAuth trigger


def test_trigger_oauth_without_api():
    assert tw.trigger_oauth_reconnection() is False


@pytest.mark.parametrize("result, expected", [(True, True), (False, False)])
def test_trigger_oauth_waits_for_thread(monkeypatch, result, expected):
    api = SimpleNamespace(authenticator=None, reconnect_with_oauth=AsyncMock(return_value=result))
    monkeypatch.setattr(tw, "twitch_api", api)
    monkeypatch.setattr(twitch_oauth, "is_oauth_in_progress", lambda: False)
    assert tw.trigger_oauth_reconnection() is expected


def test_trigger_oauth_thread_error(monkeypatch):
    api = SimpleNamespace(authenticator=None, reconnect_with_oauth=AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(tw, "twitch_api", api)
    monkeypatch.setattr(twitch_oauth, "is_oauth_in_progress", lambda: False)
    assert tw.trigger_oauth_reconnection() is False


def test_trigger_oauth_returns_when_flow_starts(monkeypatch):
    gate = threading.Event()

    async def slow():
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 2)
        return True

    api = SimpleNamespace(authenticator="AUTH", reconnect_with_oauth=slow)
    monkeypatch.setattr(tw, "twitch_api", api)
    try:
        assert tw.trigger_oauth_reconnection() is True
    finally:
        gate.set()


def test_trigger_oauth_deadline_passes(monkeypatch):
    gate = threading.Event()

    async def slow():
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 2)
        return False

    api = SimpleNamespace(authenticator=None, reconnect_with_oauth=slow)
    monkeypatch.setattr(tw, "twitch_api", api)
    monkeypatch.setattr(twitch_oauth, "is_oauth_in_progress", lambda: False)
    ticks = iter([0.0, 0.0, 100.0])
    # Only twitch's clock: patching time.monotonic itself would also feed other threads.
    monkeypatch.setattr(tw, "time", SimpleNamespace(monotonic=lambda: next(ticks, 100.0)))
    try:
        assert tw.trigger_oauth_reconnection() is True
    finally:
        gate.set()


def test_trigger_oauth_outer_error(monkeypatch):
    monkeypatch.setattr(tw, "twitch_api", SimpleNamespace())
    monkeypatch.setattr(tw, "threading", SimpleNamespace(Event=MagicMock(side_effect=RuntimeError("x"))))
    assert tw.trigger_oauth_reconnection() is False


# --------------------------------------------------------------------------- module status helpers


def test_get_twitch_connection_status(monkeypatch):
    monkeypatch.setattr(connection_status_tracker, "apply_connectivity_overlay_to_info",
                        lambda svc, info: dict(info, svc=svc))
    assert tw.get_twitch_connection_status()["status"] == "Not Initialized"
    monkeypatch.setattr(tw, "twitch_api", SimpleNamespace(get_connection_status=lambda: {"status": "Connected"}))
    assert tw.get_twitch_connection_status() == {"status": "Connected", "svc": "twitch"}
    monkeypatch.setattr(tw, "twitch_api", SimpleNamespace(get_connection_status=MagicMock(side_effect=RuntimeError())))
    assert tw.get_twitch_connection_status()["status"] == "Error"


@pytest.fixture
def tokens(monkeypatch):
    t = SimpleNamespace(creds={"client_id": "c", "client_secret": "s"},
                        data=SimpleNamespace(auth_token="a", refresh_token="r"))
    monkeypatch.setattr(acm.api_credentials_manager, "get_twitch_credentials", lambda: t.creds)
    state = MagicMock()
    state.get_twitch_data.side_effect = lambda: t.data
    monkeypatch.setattr(dataobjects, "state_manager", state)
    return t


def test_twitch_has_tokens_configured(tokens):
    assert tw.twitch_has_tokens_configured() is True
    tokens.data = SimpleNamespace(auth_token="a", refresh_token=" ")
    assert tw.twitch_has_tokens_configured() is False
    tokens.data = None
    assert tw.twitch_has_tokens_configured() is False
    tokens.creds = {"client_id": "c"}
    assert tw.twitch_has_tokens_configured() is False
    tokens.creds = None  # .get on None -> error -> False
    assert tw.twitch_has_tokens_configured() is False


def test_is_twitch_disconnected_for_monitor(tokens, monkeypatch):
    assert tw.is_twitch_disconnected_for_monitor() is True  # no api
    api = MagicMock()
    api.is_eventsub_live.return_value = False
    monkeypatch.setattr(tw, "twitch_api", api)
    assert tw.is_twitch_disconnected_for_monitor() is True
    api.is_eventsub_live.return_value = True
    api.is_connected = True
    assert tw.is_twitch_disconnected_for_monitor() is False
    tokens.data = None
    assert tw.is_twitch_disconnected_for_monitor() is False


def test_attempt_auto_reconnect(tokens, monkeypatch):
    assert tw.attempt_auto_reconnect() is False  # no api
    api = MagicMock()
    api._eventsub_is_reconnecting.return_value = True
    monkeypatch.setattr(tw, "twitch_api", api)
    assert tw.attempt_auto_reconnect() is False  # library already reconnecting
    api._eventsub_is_reconnecting.return_value = False
    monkeypatch.setattr(connection_monitor, "is_internet_available", lambda *a, **k: True)
    monkeypatch.setattr(connection_monitor, "is_service_reachable", lambda *a, **k: False)
    assert tw.attempt_auto_reconnect() is False
    monkeypatch.setattr(connection_monitor, "is_service_reachable", lambda *a, **k: True)
    assert tw.attempt_auto_reconnect() is True
    api.reconnect.assert_called_once()
    monkeypatch.setattr(connection_monitor, "is_internet_available", MagicMock(side_effect=RuntimeError()))
    assert tw.attempt_auto_reconnect() is False
    tokens.data = None
    assert tw.attempt_auto_reconnect() is False


def test_get_twitch_token_status(monkeypatch):
    assert tw.get_twitch_token_status()["status"] == "Not Initialized"
    monkeypatch.setattr(tw, "twitch_api", SimpleNamespace(get_token_status=lambda: {"ok": 1}))
    assert tw.get_twitch_token_status() == {"ok": 1}
    monkeypatch.setattr(tw, "twitch_api", SimpleNamespace(get_token_status=MagicMock(side_effect=RuntimeError("e"))))
    assert tw.get_twitch_token_status() == {"status": "Error", "error": "e"}

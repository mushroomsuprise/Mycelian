# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/discord_service.py with a fake discord.py module (the gateway is never contacted)."""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules  # noqa: E402
from modules import discord_service as ds  # noqa: E402
from modules.dataobjects import DiscordData  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self, data=None):
        self.data = data if data is not None else DiscordData()
        self.fields = []
        self.saves = 0
        self.save_ok = True
        self.youtube = SimpleNamespace(latest_video_title="", oauth_channel_id="", oauth_channel_title="",
                                       live_chat_status="")
        self.app_settings = SimpleNamespace(streamer_name="")
        self._initialized = True
        self.initialize_calls = 0
        self.fail = set()

    def get_discord_data(self):
        if "discord" in self.fail:
            raise RuntimeError("no discord data")
        return self.data

    def update_discord_field(self, name, value):
        if "update" in self.fail:
            raise RuntimeError("update fail")
        self.fields.append((name, value))
        setattr(self.data, name, value)

    def save_changes(self):
        self.saves += 1
        return self.save_ok

    def get_youtube_data(self):
        if "youtube" in self.fail:
            raise RuntimeError("yt")
        return self.youtube

    def get_app_settings(self):
        if "app" in self.fail:
            raise RuntimeError("app")
        return self.app_settings

    def initialize(self):
        self.initialize_calls += 1
        self._initialized = True


@pytest.fixture
def state(monkeypatch):
    st = FakeState()
    monkeypatch.setattr(ds, "state_manager", st)
    return st


class FakeAvatar:
    def __init__(self, url="https://cdn/avatar.png", fail=False):
        self.url = url
        self.fail = fail
        self.sizes = []

    def replace(self, size):
        if self.fail:
            raise RuntimeError("no avatar")
        self.sizes.append(size)
        return self


class FakeUser:
    def __init__(self, name="Bot#0001", uid=42, avatar=None):
        self.name = name
        self.id = uid
        self.display_avatar = avatar or FakeAvatar()

    def __str__(self):
        return self.name


class FakeChannel:
    def __init__(self, cid, name="general", fail=None):
        self.id = cid
        self.name = name
        self.fail = fail
        self.sent = []

    async def send(self, message):
        if self.fail:
            raise self.fail
        self.sent.append(message)


class FakeGuild:
    def __init__(self, gid, name, channels=()):
        self.id = gid
        self.name = name
        self.text_channels = list(channels)


class FakeClient:
    """Minimal discord.Client: start() fires on_ready (or a scripted failure) and waits for close()."""

    script = "ready"  # class-level knob set per test: ready | login_fail | crash | return
    instances: list = []

    def __init__(self, intents=None):
        self.intents = intents
        self.user = None
        self.application_id = 777
        self.guilds = []
        self.channels = {}
        self.fetchable = {}
        self.events = {}
        self._closed = False
        self._close_event = None
        FakeClient.instances.append(self)

    def event(self, fn):
        self.events[fn.__name__] = fn
        return fn

    async def start(self, token):
        self.token = token
        self._close_event = asyncio.Event()
        if FakeClient.script in ("login_fail", "crash"):
            await asyncio.sleep(0.05)  # a real login round-trip; the caller is already waiting
        if FakeClient.script == "login_fail":
            raise FakeDiscord.LoginFailure("bad token")
        if FakeClient.script == "crash":
            raise RuntimeError("gateway crash")
        if FakeClient.script == "return":
            return
        self.user = FakeUser()
        await self.events["on_ready"]()
        await self._close_event.wait()

    async def close(self):
        self._closed = True
        if self._close_event is not None:
            self._close_event.set()

    def is_closed(self):
        return self._closed

    def get_guild(self, gid):
        for g in self.guilds:
            if g.id == gid:
                return g
        return None

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        r = self.fetchable.get(cid)
        if isinstance(r, BaseException):
            raise r
        if r is None:
            raise LookupError("unknown channel")
        return r


class FakeDiscord(types.ModuleType):
    class LoginFailure(Exception):
        pass

    class Intents:
        @staticmethod
        def default():
            return "default-intents"

    Client = FakeClient


@pytest.fixture
def fake_discord(monkeypatch):
    mod = FakeDiscord("discord")
    monkeypatch.setitem(sys.modules, "discord", mod)
    FakeClient.script = "ready"
    FakeClient.instances = []
    monkeypatch.setattr(ds, "_READY_TIMEOUT_SEC", 10.0)  # only an upper bound; ready normally takes ms
    return mod


@pytest.fixture
def svc(state):
    s = ds.DiscordService()
    yield s
    # never leave a bot thread running
    s._stop_requested = True
    loop = s._loop
    if loop is not None and loop.is_running():
        client = s._client
        if client is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(2)
            except Exception:
                pass
    t = s._thread
    if t is not None and t.is_alive():
        t.join(3)


@pytest.fixture
def running(svc):
    """Give ``svc`` a real event loop on a helper thread plus a fake ready client (for _run_coro paths)."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    client = FakeClient()
    client.user = FakeUser()
    svc._loop = loop
    svc._client = client
    svc._ready.set()
    svc._status = "Connected"
    yield SimpleNamespace(svc=svc, loop=loop, client=client)
    svc._loop = None
    svc._client = None
    loop.call_soon_threadsafe(loop.stop)
    t.join(2)
    loop.close()


# --------------------------------------------------------------------------- normalize targets


def test_normalize_channel_targets():
    assert ds._normalize_channel_targets(None) == []
    assert ds._normalize_channel_targets("x") == []
    raw = [
        {"guild_id": 1, "channel_id": " 2 ", "guild_name": "G", "channel_name": "", "extra": 1},
        {"guild_id": "", "channel_id": "3"},
        {"guild_id": "4"},
        "nope",
        {"guild_id": "5", "channel_id": "6", "channel_name": "news"},
    ]
    assert ds._normalize_channel_targets(raw) == [
        {"guild_id": "1", "channel_id": "2", "guild_name": "G"},
        {"guild_id": "5", "channel_id": "6", "channel_name": "news"},
    ]
    assert ds._normalize_channel_targets(({"guild_id": "1", "channel_id": "2"},)) == [
        {"guild_id": "1", "channel_id": "2"}]


# --------------------------------------------------------------------------- message formatting / urls


def test_format_message_with_values():
    assert ds._format_go_live_message("{platform}: {title} {url}", platform="twitch", title="T", url="U") == \
        "Twitch: T U"


def test_format_message_youtube_fills_title_and_url(state):
    state.youtube.latest_video_title = "My Stream"
    state.youtube.oauth_channel_id = "UC1"
    out = ds._format_go_live_message("{platform}|{title}|{url}", platform="youtube")
    assert out == "YouTube|My Stream|https://www.youtube.com/channel/UC1/live"


def test_format_message_youtube_title_lookup_failure(state):
    state.fail.add("youtube")
    assert ds._format_go_live_message("{title}|{url}", platform="youtube") == "|"


def test_format_message_twitch_fills_url(monkeypatch, state):
    monkeypatch.setattr(ds, "_twitch_stream_url", lambda: "https://twitch.tv/me")
    assert ds._format_go_live_message("{url}", platform="twitch") == "https://twitch.tv/me"


def test_format_message_other_platform_no_url_lookup(state):
    assert ds._format_go_live_message("{platform} {url}", platform="kick") == "YouTube "


@pytest.mark.parametrize("url,expected", [("http://x", "Bad {oops} (Twitch: http://x)"), ("", "Bad {oops} (Twitch)")])
def test_format_message_bad_template_falls_back(monkeypatch, state, url, expected):
    monkeypatch.setattr(ds, "_twitch_stream_url", lambda: "")
    assert ds._format_go_live_message("Bad {oops}", platform="twitch", url=url) == expected


@pytest.fixture
def fake_twitch(monkeypatch):
    mod = types.ModuleType("modules.twitch")
    mod.api = None

    def get_twitch_api():
        if isinstance(mod.api, BaseException):
            raise mod.api
        return mod.api

    mod.get_twitch_api = get_twitch_api
    monkeypatch.setitem(sys.modules, "modules.twitch", mod)
    monkeypatch.setattr(modules, "twitch", mod, raising=False)
    return mod


def test_twitch_stream_url_from_api(fake_twitch, state):
    fake_twitch.api = SimpleNamespace(user=SimpleNamespace(login="streamer"))
    assert ds._twitch_stream_url() == "https://twitch.tv/streamer"


def test_twitch_stream_url_from_settings(fake_twitch, state):
    fake_twitch.api = SimpleNamespace(user=None)
    state.app_settings.streamer_name = " fallback "
    assert ds._twitch_stream_url() == "https://twitch.tv/fallback"


def test_twitch_stream_url_errors(fake_twitch, state):
    fake_twitch.api = RuntimeError("api")
    state.fail.add("app")
    assert ds._twitch_stream_url() == ""


def test_twitch_stream_url_nothing(fake_twitch, state):
    assert ds._twitch_stream_url() == ""


def test_youtube_live_url_variants(state):
    assert ds._youtube_live_url() == ""
    state.youtube.oauth_channel_title = "Chan"
    assert ds._youtube_live_url() == "https://www.youtube.com/"
    state.youtube.oauth_channel_id = "UC9"
    assert ds._youtube_live_url() == "https://www.youtube.com/channel/UC9/live"
    state.fail.add("youtube")
    assert ds._youtube_live_url() == ""


# --------------------------------------------------------------------------- helix probe


class HelixAPI:
    def __init__(self, items, connected=True, user_id="u1", bridge=True):
        self.is_connected = connected
        self.user_id = user_id
        self.items = items
        self.calls = []
        if bridge:
            self.run_coroutine = self._run

    async def generic_api_call(self, url, method, params=None):
        self.calls.append((url, method, params))
        if isinstance(self.items, BaseException):
            raise self.items
        return {"data": self.items}

    def _run(self, coro, timeout):
        self.timeout = timeout
        return asyncio.run(coro)


def test_helix_no_api(fake_twitch):
    assert ds._helix_twitch_is_live() is False
    fake_twitch.api = HelixAPI([1], connected=False)
    assert ds._helix_twitch_is_live() is False
    fake_twitch.api = HelixAPI([1], user_id="")
    assert ds._helix_twitch_is_live() is False


def test_helix_via_bridge(fake_twitch):
    api = HelixAPI([{"id": "s"}])
    fake_twitch.api = api
    assert ds._helix_twitch_is_live() is True
    assert api.calls == [("https://api.twitch.tv/helix/streams", "GET", {"user_id": "u1"})]
    assert api.timeout == 8
    api.items = []
    assert ds._helix_twitch_is_live() is False


def test_helix_via_worker_thread(fake_twitch):
    fake_twitch.api = HelixAPI([{"id": "s"}], bridge=False)
    assert ds._helix_twitch_is_live() is True
    fake_twitch.api = HelixAPI(RuntimeError("http"), bridge=False)
    assert ds._helix_twitch_is_live() is False


def test_helix_exception(fake_twitch):
    fake_twitch.api = RuntimeError("boom")
    assert ds._helix_twitch_is_live() is False


# --------------------------------------------------------------------------- go-live announcer


@pytest.fixture
def golive(monkeypatch, state):
    sent = []
    result = {"ok": True}

    def send(message, channels):
        sent.append((message, channels))
        return result["ok"]

    monkeypatch.setattr(ds, "send_to_channels", send)
    helix = {"live": False, "calls": 0}

    def helix_probe():
        helix["calls"] += 1
        return helix["live"]

    monkeypatch.setattr(ds, "_helix_twitch_is_live", helix_probe)
    failed = []
    monkeypatch.setattr(ds, "_notify_go_live_failed", lambda p: failed.append(p))
    state.data.go_live_enabled = True
    state.data.go_live_channels = [{"guild_id": "1", "channel_id": "2"}]
    state.data.go_live_message = "Live on {platform} {url}"
    return SimpleNamespace(sent=sent, result=result, helix=helix, failed=failed, state=state)


def test_golive_unknown_platform(golive):
    a = ds.GoLiveAnnouncer()
    assert a.on_platform_live("kick") is False
    assert a.on_platform_live(None) is False
    assert golive.sent == []


def test_golive_twitch_sends_once_per_session(golive):
    a = ds.GoLiveAnnouncer()
    assert a.on_platform_live(" Twitch ", url="https://twitch.tv/x") is True
    assert golive.sent == [("Live on Twitch https://twitch.tv/x", [{"guild_id": "1", "channel_id": "2"}])]
    assert a.twitch_live and a.announcement_sent
    assert golive.helix["calls"] == 0
    # youtube after twitch: already sent, no helix probe needed
    assert a.on_platform_live("youtube", url="u") is False
    assert golive.helix["calls"] == 0 and a.youtube_live
    assert len(golive.sent) == 1


def test_golive_youtube_checks_helix(golive):
    golive.helix["live"] = True
    a = ds.GoLiveAnnouncer()
    assert a.on_platform_live("youtube", title="t", url="u") is True
    assert golive.helix["calls"] == 1 and a.twitch_live and a.youtube_live


def test_golive_disabled_or_no_channels(golive):
    golive.state.data.go_live_enabled = False
    a = ds.GoLiveAnnouncer()
    assert a.on_platform_live("twitch", url="u") is False
    golive.state.data.go_live_enabled = True
    golive.state.data.go_live_channels = []
    assert a.on_platform_live("twitch", url="u") is False
    assert golive.sent == [] and a.announcement_sent is False


def test_golive_state_error(golive):
    golive.state.fail.add("discord")
    assert ds.GoLiveAnnouncer().on_platform_live("twitch") is False


def test_golive_default_template(golive):
    golive.state.data.go_live_message = ""
    ds.GoLiveAnnouncer().on_platform_live("twitch", url="https://t")
    assert golive.sent[0][0] == "Stream is live on Twitch!"


def test_golive_send_failure_allows_retry_and_notifies(golive):
    golive.result["ok"] = False
    a = ds.GoLiveAnnouncer()
    assert a.on_platform_live("twitch", url="u") is False
    assert a.announcement_sent is False and golive.failed == ["twitch"]
    golive.result["ok"] = True
    assert a.on_platform_live("twitch", url="u") is True


def test_golive_offline_resets_session(golive):
    a = ds.GoLiveAnnouncer()
    a.on_platform_live("twitch", url="u")
    a.youtube_live = True
    a.on_platform_offline("Twitch")
    assert a.announcement_sent is True  # youtube still live
    a.on_platform_offline("youtube")
    assert a.announcement_sent is False and not a.twitch_live and not a.youtube_live
    a.announcement_sent = True
    a.on_platform_offline(None)  # unknown platform, nothing live -> reset
    assert a.announcement_sent is False


def test_golive_reset_session(golive):
    a = ds.GoLiveAnnouncer()
    a.twitch_live = a.youtube_live = a.announcement_sent = True
    a.reset_session()
    assert (a.twitch_live, a.youtube_live, a.announcement_sent) == (False, False, False)


# --------------------------------------------------------------------------- notify failed


@pytest.fixture
def notices(monkeypatch):
    from modules import notification_engine

    sent = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: sent.append((msg, kw)))
    return sent


def test_notify_go_live_failed_not_connected(monkeypatch, notices, state):
    monkeypatch.setattr(ds, "discord_service", SimpleNamespace(last_send_errors=["bot not connected"]))
    ds._notify_go_live_failed("twitch")
    msg, kw = notices[0]
    assert msg == "Your Twitch go-live announcement wasn't posted to Discord."
    assert "isn't connected" in kw["hint"] and kw["details"] == "bot not connected"
    assert kw["dedupe_key"] == "discord:golive_failed" and kw["source"] == "discord"


def test_notify_go_live_failed_permission_hint(monkeypatch, notices, state):
    monkeypatch.setattr(ds, "discord_service", SimpleNamespace(last_send_errors=[]))
    ds._notify_go_live_failed("kick")
    msg, kw = notices[0]
    assert "kick go-live" in msg and "Send Messages" in kw["hint"] and kw["details"] is None
    monkeypatch.setattr(ds, "discord_service", SimpleNamespace(last_send_errors=["1: x", "2: y"]))
    ds._notify_go_live_failed("youtube")
    assert notices[1][1]["details"] == "1: x\n2: y" and "YouTube" in notices[1][0]


def test_notify_go_live_failed_swallows_errors(monkeypatch, state):
    from modules import notification_engine

    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(ds, "discord_service", SimpleNamespace())
    ds._notify_go_live_failed("twitch")


# --------------------------------------------------------------------------- status / invite


def test_status_disconnected_defaults(svc, state):
    st = svc.get_status()
    assert st == {"status": "Disconnected", "is_connected": False, "bot_username": "", "bot_user_id": "",
                  "bot_avatar_url": "", "application_id": "", "invite_url": ""}
    assert svc.is_connected() is False


def test_status_connected_with_avatar_and_invite(svc, state):
    c = FakeClient()
    c.user = FakeUser()
    svc._client = c
    svc._ready.set()
    svc._status = "Connected"
    st = svc.get_status()
    assert st["is_connected"] is True and st["bot_username"] == "Bot#0001" and st["bot_user_id"] == "42"
    assert st["bot_avatar_url"] == "https://cdn/avatar.png" and c.user.display_avatar.sizes == [128]
    assert st["application_id"] == "777"
    assert st["invite_url"].startswith("https://discord.com/api/oauth2/authorize?client_id=777&")
    assert "permissions=84992" in st["invite_url"] and "scope=bot" in st["invite_url"]
    svc._stop_requested = True
    assert svc.get_status()["is_connected"] is False


def test_status_avatar_error_and_no_app_id(svc, state):
    c = FakeClient()
    c.user = FakeUser(avatar=FakeAvatar(fail=True))
    c.application_id = None
    svc._client = c
    st = svc.get_status()
    assert st["bot_avatar_url"] == "" and st["application_id"] == ""


def test_invite_url_from_state_and_failures(svc, state):
    assert svc.get_invite_url() == ""
    state.data.application_id = " 123 "
    assert svc.get_invite_url(permissions=8) == \
        "https://discord.com/api/oauth2/authorize?client_id=123&permissions=8&scope=bot"
    state.fail.add("discord")
    assert svc.get_invite_url() == ""


# --------------------------------------------------------------------------- _run_coro / async helpers


def test_run_coro_without_loop_closes_coroutine(svc):
    async def coro():
        return 1

    c = coro()
    assert svc._run_coro(c, default="d") == "d"
    assert c.cr_frame is None  # closed


def test_run_coro_close_error_ignored(svc):
    class Bad:
        def close(self):
            raise RuntimeError("x")

    assert svc._run_coro(Bad(), default=5) == 5


def test_run_coro_runs_on_loop(running):
    async def coro():
        return 99

    assert running.svc._run_coro(coro()) == 99

    async def bad():
        raise RuntimeError("inside")

    assert running.svc._run_coro(bad(), default="fallback") == "fallback"


def test_list_guilds_and_channels(running):
    c = running.client
    g1 = FakeGuild(1, "zeta", [FakeChannel(11, "b-chan"), FakeChannel(10, "A-chan")])
    g2 = FakeGuild(2, "Alpha")
    g3 = FakeGuild(3, None)
    c.guilds = [g1, g2, g3]
    assert running.svc.list_guilds() == [{"id": "3", "name": None}, {"id": "2", "name": "Alpha"},
                                         {"id": "1", "name": "zeta"}]
    assert running.svc.list_text_channels("1") == [
        {"id": "10", "name": "A-chan", "guild_id": "1", "guild_name": "zeta"},
        {"id": "11", "name": "b-chan", "guild_id": "1", "guild_name": "zeta"},
    ]
    assert running.svc.list_text_channels("abc") == []
    assert running.svc.list_text_channels(None) == []
    assert running.svc.list_text_channels("99") == []


def test_list_helpers_without_client(svc):
    assert asyncio.run(svc._list_guilds_async()) == []
    assert asyncio.run(svc._list_text_channels_async("1")) == []
    assert svc.list_guilds() == [] and svc.list_text_channels("1") == []


def test_send_to_channels_guards(svc):
    assert svc.send_to_channels("  ", [{"guild_id": "1", "channel_id": "2"}]) is False
    assert svc.send_to_channels("hi", []) is False
    assert svc.send_to_channels("hi", [{"guild_id": "1", "channel_id": "2"}]) is False
    assert svc.last_send_errors == ["bot not connected"]


def test_send_to_channels_mixed_results(running):
    c = running.client
    good = FakeChannel(2)
    c.channels[2] = good
    c.fetchable[3] = FakeChannel(3)
    c.fetchable[4] = PermissionError("forbidden")
    c.channels[5] = SimpleNamespace(id=5)  # not sendable
    c.channels[6] = FakeChannel(6, fail=RuntimeError("rate limited"))
    targets = [{"guild_id": "g", "channel_id": cid} for cid in ("2", "x1", "3", "4", "5", "6")]
    assert running.svc.send_to_channels(" hello ", targets) is True
    assert good.sent == ["hello"] and c.fetchable[3].sent == ["hello"]
    errs = running.svc.last_send_errors
    assert errs[0] == "'x1': not a channel id"
    assert errs[1].startswith("4: can't open channel (PermissionError")
    assert errs[2] == "5: not a text channel"
    assert errs[3] == "6: RuntimeError: rate limited"


def test_send_to_channels_all_fail(running):
    running.client.channels[2] = FakeChannel(2, fail=RuntimeError("no"))
    assert running.svc.send_to_channels("m", [{"guild_id": "g", "channel_id": "2"}]) is False


def test_send_async_without_client(svc):
    assert asyncio.run(svc._send_to_channels_async("m", [{"channel_id": "1"}])) is False
    assert svc.last_send_errors == ["bot not connected"]


def test_send_async_invalid_none_channel_id(running):
    async def go():
        return await running.svc._send_to_channels_async("m", [{"channel_id": None}])

    assert asyncio.run(go()) is False
    assert running.svc.last_send_errors == ["'': not a channel id"]


# --------------------------------------------------------------------------- persist / status helpers


def test_persist_status_fields(svc, state):
    svc._status = "Connected"
    svc._persist_status_fields(token=" tok ")
    assert state.fields[0] == ("bot_token", "tok")
    assert [f for f, _ in state.fields[1:]] == ["connection_status", "bot_user_id", "bot_username",
                                                "application_id"]
    assert state.saves == 1
    state.fields.clear()
    state.save_ok = False
    svc._persist_status_fields(token="  ")
    assert state.fields[0][0] == "connection_status"
    state.fail.add("update")
    svc._persist_status_fields()  # swallowed


# --------------------------------------------------------------------------- connect / thread lifecycle


def test_connect_empty_token(svc):
    svc._status = "Error"
    assert svc.connect_with_token("  ") is False
    assert svc._status == "Disconnected"


def test_connect_full_lifecycle(fake_discord, svc, state):
    assert svc.connect_with_token(" tok ") is True
    assert svc.is_connected()
    client = FakeClient.instances[-1]
    assert client.token == "tok" and client.intents == "default-intents"
    assert set(client.events) == {"on_ready", "on_disconnect", "on_resumed"}
    assert svc._thread.name == "MycelianDiscordBot" and svc._thread.daemon
    assert ("bot_token", "tok") in state.fields
    assert ("connection_status", "Connected") in state.fields
    # reconnect with same token while connected -> no new client
    assert svc.connect_with_token("tok") is True
    assert svc.connect_with_token("tok", wait_for_ready=False) is True
    assert len(FakeClient.instances) == 1
    # gateway events
    loop = svc._loop
    asyncio.run_coroutine_threadsafe(client.events["on_disconnect"](), loop).result(2)
    assert svc._status == "Reconnecting"
    asyncio.run_coroutine_threadsafe(client.events["on_resumed"](), loop).result(2)
    assert svc._status == "Connected"
    # module wrapper uses the singleton; ours is separate
    thread = svc._thread
    svc.disconnect(join_timeout=2)
    assert not thread.is_alive()
    assert client.is_closed()
    assert svc._client is None and svc._loop is None and svc._status == "Disconnected"
    assert ("connection_status", "Disconnected") in state.fields


def test_on_disconnect_while_stopping_keeps_status(fake_discord, svc):
    assert svc.connect_with_token("tok")
    client = FakeClient.instances[-1]
    svc._stop_requested = True
    asyncio.run_coroutine_threadsafe(client.events["on_disconnect"](), svc._loop).result(2)
    assert svc._status == "Connected"
    svc._stop_requested = False


def test_connect_new_token_replaces_client(fake_discord, svc, state):
    assert svc.connect_with_token("one")
    first = FakeClient.instances[-1]
    assert svc.connect_with_token("two")
    assert first.is_closed() and FakeClient.instances[-1].token == "two"


def test_connect_without_waiting(fake_discord, svc):
    assert svc.connect_with_token("tok", wait_for_ready=False) is True
    assert svc._ready.wait(2)


@pytest.mark.parametrize("script,status", [("login_fail", "Auth Failed"), ("crash", "Error")])
def test_connect_failure_final_status(fake_discord, svc, state, script, status):
    FakeClient.script = script
    svc.connect_with_token("tok")
    if svc._thread is not None:
        svc._thread.join(2)
    assert svc._status == status
    assert ("connection_status", status) in state.fields
    assert svc._client is None and not svc._ready.is_set()


class GatedState(FakeState):
    """Holds the bot thread's status write until the caller thread has checked the result.

    This pins the ordering the app normally gets anyway: connect_with_token wakes on ready and checks
    is_connected() before the bot thread's cleanup clears the client."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()

    def update_discord_field(self, name, value):
        if threading.current_thread().name == "MycelianDiscordBot":
            self.gate.wait(5.0)
        elif name == "bot_token":  # the caller's "connected" persist (not the pre-connect disconnect)
            self.gate.set()
        super().update_discord_field(name, value)


@pytest.mark.xfail(strict=True, reason="BUG: connect_with_token returns True for a rejected token/crash: "
                                       "is_connected() ignores the Auth Failed/Error status while _client is set")
@pytest.mark.parametrize("script", ["login_fail", "crash"])
def test_connect_failure_returns_false(fake_discord, monkeypatch, script):
    st = GatedState()
    monkeypatch.setattr(ds, "state_manager", st)
    s = ds.DiscordService()
    FakeClient.script = script
    try:
        assert s.connect_with_token("bad-token") is False
    finally:
        st.gate.set()
        if s._thread is not None:
            s._thread.join(2)


def test_connect_start_returns_without_ready(fake_discord, svc, state, monkeypatch):
    """start() returning cleanly never sets ready: the wait times out and status becomes Error."""
    FakeClient.script = "return"
    monkeypatch.setattr(ds, "_READY_TIMEOUT_SEC", 0.2)
    assert svc.connect_with_token("tok") is False
    assert svc._status == "Error"


def test_connect_ready_but_status_disconnected_reports_error(fake_discord, svc, state, monkeypatch):
    """Ready fires but the status read back says Disconnected -> the failure is recorded as Error."""
    monkeypatch.setattr(svc, "get_status", lambda: {"status": "Disconnected", "is_connected": False})
    assert svc.connect_with_token("tok") is False
    assert svc._status == "Error"


def test_connect_same_token_waits_then_reconnects_if_not_ready(fake_discord, svc, monkeypatch):
    monkeypatch.setattr(ds, "_READY_TIMEOUT_SEC", 0.05)

    class AliveThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            pass

    svc._thread = AliveThread()
    svc._token = "tok"
    svc._status = "Connecting"
    monkeypatch.setattr(ds, "_READY_TIMEOUT_SEC", 10.0)
    svc._ready.clear()

    real_wait = svc._ready.wait
    waits = []

    def wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 1:
            return False  # first wait (existing session) times out
        return real_wait(timeout)

    svc._ready.wait = wait
    assert svc.connect_with_token("tok") is True
    assert len(waits) == 2


def test_thread_main_finally_handles_close_and_task_errors(fake_discord, svc, monkeypatch):
    """Run _thread_main on this thread with a client whose close() raises and that leaves a pending task."""

    class Leaky(FakeClient):
        async def start(self, token):
            asyncio.get_running_loop().create_task(asyncio.sleep(10))
            raise RuntimeError("crash")

        async def close(self):
            raise RuntimeError("close failed")

    fake_discord.Client = Leaky
    svc._token = "tok"
    try:
        svc._thread_main()
    finally:
        asyncio.set_event_loop(None)
    assert svc._status == "Error" and svc._loop is None and svc._client is None
    assert not svc._ready.is_set()


def test_thread_main_finally_all_tasks_error(fake_discord, svc, monkeypatch):
    FakeClient.script = "return"
    real_all_tasks = asyncio.all_tasks
    monkeypatch.setattr(ds.asyncio, "all_tasks", lambda loop=None: (_ for _ in ()).throw(RuntimeError("x")))
    svc._token = "tok"
    try:
        svc._thread_main()
    finally:
        asyncio.set_event_loop(None)
    assert svc._status == "Disconnected"
    assert real_all_tasks is not None


def test_disconnect_close_errors_swallowed(svc, state):
    class Loop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, fn):
            raise RuntimeError("closed")

    class Client:
        def close(self):
            raise RuntimeError("no close")

    svc._loop = Loop()
    svc._client = Client()
    svc._token = "tok"
    state.data.bot_token = ""
    svc.disconnect(join_timeout=0.01)
    assert ("bot_token", "tok") in state.fields  # session token restored
    assert ("connection_status", "Disconnected") in state.fields and state.saves == 1
    assert svc._loop is None


def test_disconnect_persist_failure_swallowed(svc, state):
    state.fail.add("discord")
    svc.disconnect()
    assert svc._status == "Disconnected"


def test_disconnect_keeps_existing_stored_token(svc, state):
    state.data.bot_token = "stored"
    svc._token = "session"
    svc.disconnect()
    assert ("bot_token", "session") not in state.fields


# --------------------------------------------------------------------------- start service / auto reconnect


def test_start_service_no_token(svc, state, monkeypatch):
    from modules import database_manager as dbm_mod

    monkeypatch.setattr(dbm_mod, "get_data", lambda path: {}, raising=False)
    state._initialized = False
    calls = []
    monkeypatch.setattr(svc, "connect_with_token", lambda *a, **k: calls.append(a))
    svc.start_discord_service()
    assert state.initialize_calls == 1 and calls == []


def test_start_service_db_fallback_token(svc, state, monkeypatch):
    fake_dbm = types.ModuleType("modules.database_manager")
    fake_dbm.get_data = lambda path: {"bot_token": "enc"} if path == "DiscordData" else None
    monkeypatch.setitem(sys.modules, "modules.database_manager", fake_dbm)
    monkeypatch.setattr(modules, "database_manager", fake_dbm)
    from modules import encryption_utils

    monkeypatch.setattr(encryption_utils, "ensure_decrypted", lambda raw: " plain ")
    calls = []
    monkeypatch.setattr(svc, "connect_with_token", lambda tok, wait_for_ready=True: calls.append((tok, wait_for_ready)) or True)
    state.youtube.live_chat_status = "Live"
    svc.start_discord_service()
    assert ("bot_token", "plain") in state.fields
    assert calls == [("plain", False)]
    assert svc.go_live.youtube_live is True


def test_start_service_db_fallback_errors(svc, state, monkeypatch):
    fake_dbm = types.ModuleType("modules.database_manager")
    fake_dbm.get_data = lambda path: (_ for _ in ()).throw(RuntimeError("db"))
    monkeypatch.setitem(sys.modules, "modules.database_manager", fake_dbm)
    monkeypatch.setattr(modules, "database_manager", fake_dbm)
    svc.start_discord_service()  # logs and returns


def test_start_service_db_fallback_decrypts_to_empty(svc, state, monkeypatch):
    fake_dbm = types.ModuleType("modules.database_manager")
    fake_dbm.get_data = lambda path: {"bot_token": "enc"}
    monkeypatch.setitem(sys.modules, "modules.database_manager", fake_dbm)
    monkeypatch.setattr(modules, "database_manager", fake_dbm)
    from modules import encryption_utils

    monkeypatch.setattr(encryption_utils, "ensure_decrypted", lambda raw: "  ")
    svc.start_discord_service()
    assert state.fields == []


def test_start_service_connect_variants(svc, state, monkeypatch):
    state.data.bot_token = "tok"
    monkeypatch.setattr(svc, "connect_with_token", lambda tok, wait_for_ready=True: False)
    svc.start_discord_service()
    assert svc.go_live.youtube_live is False
    monkeypatch.setattr(svc, "connect_with_token", lambda tok, wait_for_ready=True: True)
    state.fail.add("youtube")
    svc.start_discord_service()  # youtube lookup error swallowed
    assert svc.go_live.youtube_live is False
    state.fail = {"discord"}
    svc.start_discord_service()  # outer error logged


def test_attempt_auto_reconnect(svc, state, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "connect_with_token", lambda tok: calls.append(tok) or True)
    assert svc.attempt_auto_reconnect() is False
    state.data.bot_token = " t "
    assert svc.attempt_auto_reconnect() is True and calls == ["t"]
    state.fail.add("discord")
    assert svc.attempt_auto_reconnect() is False
    monkeypatch.setattr(svc, "is_connected", lambda: True)
    assert svc.attempt_auto_reconnect() is True


# --------------------------------------------------------------------------- module wrappers


class Recorder:
    def __init__(self):
        self.calls = []
        self.go_live = SimpleNamespace(
            on_platform_live=lambda p, title="", url="": self.calls.append(("live", p, title, url)) or True,
            on_platform_offline=lambda p: self.calls.append(("offline", p)),
        )

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
            return f"ret-{name}"

        return f


def test_module_wrappers_delegate(monkeypatch, state):
    r = Recorder()
    monkeypatch.setattr(ds, "discord_service", r)
    ds.start_discord_service()
    assert ds.get_discord_status() == "ret-get_status"
    assert ds.connect_with_token("t", wait_for_ready=False) == "ret-connect_with_token"
    ds.disconnect()
    assert ds.list_guilds() == "ret-list_guilds"
    assert ds.list_text_channels("g") == "ret-list_text_channels"
    assert ds.send_to_channels("m", []) == "ret-send_to_channels"
    assert ds.get_invite_url() == "ret-get_invite_url"
    assert ds.attempt_auto_reconnect() == "ret-attempt_auto_reconnect"
    assert ds.notify_platform_live("twitch", title="T", url="U") is True
    ds.notify_platform_offline("twitch")
    names = [c[0] for c in r.calls]
    assert names == ["start_discord_service", "get_status", "connect_with_token", "disconnect", "list_guilds",
                     "list_text_channels", "send_to_channels", "get_invite_url", "attempt_auto_reconnect",
                     "live", "offline"]
    assert r.calls[2] == ("connect_with_token", ("t",), {"wait_for_ready": False})
    assert r.calls[9] == ("live", "twitch", "T", "U")


def test_discord_configured_for_monitor(state):
    assert ds.discord_configured_for_monitor() is False
    state.data.bot_token = " x "
    assert ds.discord_configured_for_monitor() is True
    state.fail.add("discord")
    assert ds.discord_configured_for_monitor() is False

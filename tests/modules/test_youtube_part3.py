# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/youtube.py, part 3: the OAuth live chat poller (discovery, polling, routing),
chat/membership/gift/Super Chat emitters, sending chat, and the module-level service functions.

All collaborators (alerts, connectors, chatbot, polls, web engine, Discord, HTTP) are faked.
"""

from __future__ import annotations

import copy
import hashlib
import http.client as http_client
import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import youtube as yt
from modules.dataobjects import YouTubeData


# --------------------------------------------------------------------------- fakes


class FakeState:
    """Mirrors StateManager's YouTube API: get returns a deep copy, update writes through."""

    def __init__(self, data=None, fail_get=False):
        self.data = data if data is not None else YouTubeData()
        self.fail_get = fail_get
        self.updates = []
        self.saves = 0
        self.set_calls = []
        self.initialized = 0

    def initialize(self):
        self.initialized += 1

    def get_youtube_data(self):
        if self.fail_get:
            raise RuntimeError("db down")
        return copy.deepcopy(self.data)

    def update_youtube_field(self, field, value):
        self.updates.append((field, value))
        setattr(self.data, field, copy.deepcopy(value))
        return True

    def save_changes(self):
        self.saves += 1

    def set_youtube_data(self, d):
        self.set_calls.append(d)
        return True


class Resp:
    def __init__(self, status=200, payload=None, text="", json_error=None):
        self.status_code = status
        self._payload = payload
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeHTTP:
    """Routes requests.get/post to responses keyed by URL suffix (or a FIFO list)."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def _pick(self, url):
        for suffix, value in self.routes.items():
            if url.endswith(suffix):
                if isinstance(value, list):
                    value = value.pop(0) if len(value) > 1 else value[0]
                if isinstance(value, BaseException):
                    raise value
                return value
        raise AssertionError(f"unexpected URL {url}")

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._pick(url)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._pick(url)


class FakeClock:
    def __init__(self, now=10_000.0):
        self.now = now
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def strftime(self, fmt):
        return "2026-01-01 00:00:00"


@pytest.fixture
def state(monkeypatch):
    st = FakeState()
    monkeypatch.setattr(yt, "state_manager", st)
    return st


@pytest.fixture
def creds(monkeypatch):
    from modules import api_credentials_manager as acm

    box = {"value": {}}

    def get():
        v = box["value"]
        if isinstance(v, BaseException):
            raise v
        return v

    monkeypatch.setattr(acm.api_credentials_manager, "get_youtube_credentials", get)
    return box


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(yt, "time", c)
    return c


@pytest.fixture
def http(monkeypatch):
    def install(routes=None):
        f = FakeHTTP(routes)
        monkeypatch.setattr(yt.requests, "get", f.get)
        monkeypatch.setattr(yt.requests, "post", f.post)
        return f

    return install


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(yt.webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


@pytest.fixture
def notices(monkeypatch):
    from modules import notification_engine as ne

    got = []
    monkeypatch.setattr(ne, "notify", lambda *a, **k: got.append((a, k)))
    return got


@pytest.fixture
def client(state, creds, clock):
    c = yt.YouTubeClient()
    state.updates.clear()
    state.saves = 0
    return c


CID = "UC" + "a" * 22  # a valid 24-char channel id


import threading

from modules import alertutils


def _live_ready(client):
    d = client.youtube_data
    d.live_chat_enabled = True
    d.refresh_token = "RT"
    d.oauth_client_id = "id"
    d.oauth_client_secret = "sec"


# --------------------------------------------------------------------------- live chat loop


def test_should_run_live_chat(client):
    assert client.should_run_live_chat() is False
    _live_ready(client)
    assert client.should_run_live_chat() is True
    client.youtube_data.live_chat_enabled = False
    assert client.should_run_live_chat() is False


def test_start_live_chat_not_ready(client):
    client.youtube_data.live_chat_status = "Live"
    client.start_live_chat_monitoring()
    assert client.youtube_data.live_chat_status == "Not authorized"
    assert client._live_running is False

    # tokens present but disabled: status untouched
    _live_ready(client)
    client.youtube_data.live_chat_enabled = False
    client.youtube_data.live_chat_status = "Offline"
    client.start_live_chat_monitoring()
    assert client.youtube_data.live_chat_status == "Offline"


def test_start_live_chat_already_running(client, monkeypatch):
    client._live_running = True
    monkeypatch.setattr(client, "should_run_live_chat", lambda: pytest.fail("should return early"))
    client.start_live_chat_monitoring()


class _LoopDriver:
    """Feeds a scripted sequence of actions into the live loop via _live_sleep."""

    def __init__(self, client, max_sleeps):
        self.client = client
        self.sleeps = []
        self.max = max_sleeps

    def __call__(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) >= self.max:
            self.client._live_running = False


@pytest.fixture
def discord(monkeypatch):
    from modules import discord_service

    calls = []
    monkeypatch.setattr(discord_service, "notify_platform_live", lambda p: calls.append(("live", p)))
    monkeypatch.setattr(discord_service, "notify_platform_offline", lambda p: calls.append(("offline", p)))
    return calls


def test_live_loop_discovers_chat_and_polls(client, monkeypatch, discord):
    _live_ready(client)
    client.youtube_data.live_chat_status = "Offline"
    driver = _LoopDriver(client, 3)
    monkeypatch.setattr(client, "_live_sleep", driver)
    monkeypatch.setattr(client, "_discover_active_live_chat_id", lambda: "CHAT1")
    polls = iter([2500, None, 200])
    monkeypatch.setattr(client, "_poll_live_chat_once", lambda: next(polls))
    client.start_live_chat_monitoring()
    assert client._live_chat_id == "CHAT1"
    assert client.youtube_data.live_chat_status == "Live"
    assert driver.sleeps == [2.5, yt._LIVE_CHAT_ERROR_BACKOFF_SEC, 1.0]
    assert discord == [("live", "youtube")]


def test_live_loop_already_live_no_discord(client, monkeypatch, discord):
    _live_ready(client)
    client.youtube_data.live_chat_status = "Live"
    monkeypatch.setattr(client, "_live_sleep", _LoopDriver(client, 1))
    monkeypatch.setattr(client, "_discover_active_live_chat_id", lambda: "CHAT1")
    monkeypatch.setattr(client, "_poll_live_chat_once", lambda: 1000)
    client.start_live_chat_monitoring()
    assert discord == []


def test_live_loop_offline_and_not_authorized(client, monkeypatch):
    _live_ready(client)
    driver = _LoopDriver(client, 2)
    monkeypatch.setattr(client, "_live_sleep", driver)
    monkeypatch.setattr(client, "_discover_active_live_chat_id", lambda: None)
    runs = iter([True, True, False])
    monkeypatch.setattr(client, "should_run_live_chat", lambda: next(runs))
    client.start_live_chat_monitoring()
    assert driver.sleeps == [yt._LIVE_OFFLINE_BACKOFF_SEC] * 2
    assert client.youtube_data.live_chat_status == "Not authorized"


def test_live_loop_quota_wait(client, clock, monkeypatch):
    _live_ready(client)
    long_wait = _LoopDriver(client, 1)
    monkeypatch.setattr(client, "_live_sleep", long_wait)
    client._quota_exceeded_until = clock.now + 3600
    client.start_live_chat_monitoring()
    short_wait = _LoopDriver(client, 1)
    monkeypatch.setattr(client, "_live_sleep", short_wait)
    client._quota_exceeded_until = clock.now + 1
    client.start_live_chat_monitoring()
    assert long_wait.sleeps == [60.0] and short_wait.sleeps == [5.0]


def test_live_loop_chat_ended_while_live_notifies_discord(client, monkeypatch, discord):
    _live_ready(client)
    client._live_chat_id = "CHAT1"
    client.youtube_data.live_chat_status = "Live"
    driver = _LoopDriver(client, 1)
    monkeypatch.setattr(client, "_live_sleep", driver)
    monkeypatch.setattr(client, "_poll_live_chat_once", lambda: yt._LIVE_CHAT_ENDED)
    client.start_live_chat_monitoring()
    assert client._live_chat_id is None and client._live_bootstrap_done is False
    assert client.youtube_data.live_chat_status == "Offline"
    assert discord == [("offline", "youtube")]
    assert driver.sleeps == [yt._LIVE_OFFLINE_BACKOFF_SEC]


def test_live_loop_real_chat_end_notifies_discord(client, monkeypatch, discord):
    _live_ready(client)
    client._live_chat_id = "CHAT1"
    client.youtube_data.live_chat_status = "Live"
    monkeypatch.setattr(client, "_live_sleep", _LoopDriver(client, 1))
    monkeypatch.setattr(client, "_oauth_get", lambda path, params: {"__live_chat_ended__": True})
    client.start_live_chat_monitoring()
    assert ("offline", "youtube") in discord
    assert client.youtube_data.live_chat_status == "Offline"


def test_live_loop_hooks_and_errors_are_contained(client, monkeypatch):
    from modules import discord_service

    _live_ready(client)
    boom = lambda p: (_ for _ in ()).throw(RuntimeError("discord down"))
    monkeypatch.setattr(discord_service, "notify_platform_live", boom)
    monkeypatch.setattr(discord_service, "notify_platform_offline", boom)
    driver = _LoopDriver(client, 2)
    monkeypatch.setattr(client, "_live_sleep", driver)
    monkeypatch.setattr(client, "_discover_active_live_chat_id", lambda: "CHAT")
    seq = iter([yt._LIVE_CHAT_ENDED, "raise"])

    def poll():
        v = next(seq)
        if v == "raise":
            raise RuntimeError("poll exploded")
        return v

    monkeypatch.setattr(client, "_poll_live_chat_once", poll)
    client.youtube_data.live_chat_status = "Offline"
    client.start_live_chat_monitoring()
    assert client.youtube_data.live_chat_status == "Error"
    assert driver.sleeps == [yt._LIVE_OFFLINE_BACKOFF_SEC] * 2


def test_stop_live_chat_monitoring(client):
    client._live_running = True
    client.stop_live_chat_monitoring()
    assert client._live_running is False


# --------------------------------------------------------------------------- discovery / polling


def test_discover_active_live_chat_id(client, monkeypatch):
    responses = iter([None, {"__live_chat_ended__": True}, {"items": None},
                      {"items": [{"snippet": None}, {"snippet": {"liveChatId": "C1"}}]},
                      {"items": [{"snippet": {}}]}])
    seen = []
    monkeypatch.setattr(client, "_oauth_get", lambda path, params: seen.append((path, params)) or next(responses))
    assert client._discover_active_live_chat_id() is None
    assert client._discover_active_live_chat_id() is None
    assert client._discover_active_live_chat_id() is None
    assert client._discover_active_live_chat_id() == "C1"
    assert client._discover_active_live_chat_id() is None
    assert seen[0] == ("liveBroadcasts", {"part": "snippet", "mine": "true", "broadcastStatus": "active",
                                          "broadcastType": "all"})


def test_poll_without_chat_id(client):
    assert client._poll_live_chat_once() is None


def _msg(mid, mtype="textMessageEvent", **snip):
    return {"id": mid, "snippet": {"type": mtype, **snip}, "authorDetails": {"displayName": "U"}}


def test_poll_bootstrap_then_routes_new_messages(client, monkeypatch):
    client._live_chat_id = "CHAT"
    routed = []
    monkeypatch.setattr(client, "_route_live_chat_message", lambda item: routed.append(item["id"]))
    pages = iter([
        {"items": [_msg("a"), _msg("b"), {"id": ""}], "nextPageToken": "P1", "pollingIntervalMillis": 3000},
        {"items": [_msg("b"), _msg("c")], "pollingIntervalMillis": "bad"},
        {"items": [_msg("d")]},
    ])
    params_seen = []
    monkeypatch.setattr(client, "_oauth_get", lambda path, params: params_seen.append(dict(params)) or next(pages))
    assert client._poll_live_chat_once() == 3000
    assert routed == [] and client._live_bootstrap_done is True and client._live_page_token == "P1"
    assert client._poll_live_chat_once() == 5000
    assert routed == ["c"]
    assert client._poll_live_chat_once() == 5000
    assert routed == ["c", "d"]
    assert "pageToken" not in params_seen[0] and params_seen[1]["pageToken"] == "P1"
    assert params_seen[0] == {"part": "snippet,authorDetails", "liveChatId": "CHAT", "maxResults": 200}


def test_poll_route_error_is_contained(client, monkeypatch):
    client._live_chat_id = "CHAT"
    client._live_bootstrap_done = True
    monkeypatch.setattr(client, "_route_live_chat_message", lambda item: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(client, "_oauth_get", lambda p, q: {"items": [_msg("z")], "pollingIntervalMillis": 100})
    assert client._poll_live_chat_once() == 100
    assert "z" in client._seen_message_ids


def test_poll_ended_paths(client, monkeypatch):
    client._live_chat_id = "CHAT"
    client._live_bootstrap_done = True
    monkeypatch.setattr(client, "_route_live_chat_message", lambda item: None)
    responses = iter([None, {"__live_chat_ended__": True}, {"items": [_msg("e", "chatEndedEvent")]}])
    monkeypatch.setattr(client, "_oauth_get", lambda p, q: next(responses))
    client.youtube_data.live_chat_status = "Live"
    assert client._poll_live_chat_once() is None
    # The loop owns the "Offline" change (it needs the Live status to fire the offline hook).
    assert client._poll_live_chat_once() is yt._LIVE_CHAT_ENDED
    assert client.youtube_data.live_chat_status == "Live"
    assert client._poll_live_chat_once() is yt._LIVE_CHAT_ENDED
    assert client.youtube_data.live_chat_status == "Live"


def test_remember_message_id_caps_memory(client, monkeypatch):
    monkeypatch.setattr(yt, "_SEEN_MESSAGE_IDS_MAX", 3)
    for mid in ["a", "b", "a", "c", "d"]:
        client._remember_message_id(mid)
    assert client._seen_message_id_order == ["b", "c", "d"]
    assert client._seen_message_ids == {"b", "c", "d"}


# --------------------------------------------------------------------------- routing


@pytest.mark.parametrize("mtype, target", [
    ("textMessageEvent", "_emit_youtube_chat_message"),
    ("newSponsorEvent", "_emit_membership_sub_alert"),
    ("memberMilestoneChatEvent", "_emit_membership_resub_alert"),
    ("membershipGiftingEvent", "_emit_membership_gift_alert"),
    ("superChatEvent", "_emit_superchat_donation_alert"),
    ("superStickerEvent", "_emit_superchat_donation_alert"),
    ("fanFundingEvent", "_emit_superchat_donation_alert"),
])
def test_route_live_chat_message(client, monkeypatch, mtype, target):
    calls = []
    for name in ("_emit_youtube_chat_message", "_emit_membership_sub_alert", "_emit_membership_resub_alert",
                 "_emit_membership_gift_alert", "_emit_superchat_donation_alert"):
        monkeypatch.setattr(client, name, lambda *a, _n=name, **k: calls.append((_n, a, k)))
    item = {"id": 77, "snippet": {"type": mtype, "authorChannelId": "UCsnip"},
            "authorDetails": {"displayName": "Viewer", "channelId": "UCauthor"}}
    client._route_live_chat_message(item)
    assert len(calls) == 1 and calls[0][0] == target
    name, args, kw = calls[0]
    assert args[0] == item if name == "_emit_youtube_chat_message" else args[0] == "Viewer"
    if name == "_emit_youtube_chat_message":
        assert args[1:] == ("Viewer", "UCauthor")
    if name == "_emit_superchat_donation_alert":
        assert args[2] == mtype and kw == {"event_id": "77"}


def test_route_defaults_and_unknown_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_emit_youtube_chat_message", lambda *a: calls.append(a))
    client._route_live_chat_message({"snippet": {"type": "textMessageEvent", "authorChannelId": "UCs"}})
    assert calls[0][1:] == ("YouTube User", "UCs")
    client._route_live_chat_message({"snippet": None, "authorDetails": None})
    client._route_live_chat_message({"snippet": {"type": "pollEvent"}})
    assert len(calls) == 1


# --------------------------------------------------------------------------- collaborators


def test_alerts_enabled(client):
    assert client._alerts_enabled() is True
    client.youtube_data.alerts_enabled = False
    assert client._alerts_enabled() is False


def test_schedule_connector_event(client, monkeypatch):
    from modules import connector_manager

    scheduled = []
    mgr = SimpleNamespace(is_running=True, _schedule_event_on_connector_loop=scheduled.append)
    monkeypatch.setattr(connector_manager, "get_manager", lambda: mgr)
    client._schedule_connector_event({"e": 1})
    mgr.is_running = False
    client._schedule_connector_event({"e": 2})
    monkeypatch.setattr(connector_manager, "get_manager", lambda: None)
    client._schedule_connector_event({"e": 3})
    monkeypatch.setattr(connector_manager, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    client._schedule_connector_event({"e": 4})
    assert scheduled == [{"e": 1}]


def test_process_chatbot_event(client, monkeypatch):
    from modules import chatbot, chatbot_manager

    seen = []
    monkeypatch.setattr(chatbot_manager, "get_manager",
                        lambda: SimpleNamespace(process_event=lambda t, d: ("result", t, d)))
    monkeypatch.setattr(chatbot, "dispatch_process_event_result",
                        lambda r, default_targets=None: seen.append((r, default_targets)))
    client._process_chatbot_event("evt", {"x": 1})
    assert seen == [(("result", "evt", {"x": 1}), ["youtube"])]
    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    client._process_chatbot_event("evt", {})  # logged


@pytest.fixture
def alert_sinks(monkeypatch):
    from modules import alert_processor
    from modules.uiwindows import activity_feed

    sinks = {"stored": [], "queued": [], "feed": [], "instant": []}
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda aid, d: sinks["stored"].append((aid, d)))
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: sinks["queued"].append(a))
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda **kw: sinks["feed"].append(kw))
    monkeypatch.setattr(alertutils, "new_alert_id", lambda prefix="Alert": "AID")
    return sinks


def test_enqueue_alert(client, alert_sinks):
    a = alertutils.AlertObj()
    a.alert_id = "X1"
    a.username = "u"
    client._enqueue_alert(a)
    (aid, stored), = alert_sinks["stored"]
    assert aid == "X1" and stored["source"] == "youtube" and stored["username"] == "u"
    assert alert_sinks["queued"] == [a]
    client.youtube_data.alerts_enabled = False
    client._enqueue_alert(a)
    assert len(alert_sinks["queued"]) == 1


# --------------------------------------------------------------------------- sending chat


def test_send_live_chat_message(client, http, monkeypatch):
    assert client.send_live_chat_message("   ") is False
    assert client.send_live_chat_message("hi") is False  # no chat id
    client._live_chat_id = "CHAT"
    monkeypatch.setattr(client, "ensure_valid_access_token", lambda: False)
    assert client.send_live_chat_message("hi") is False

    monkeypatch.setattr(client, "ensure_valid_access_token", lambda: True)
    client.youtube_data.access_token = "TOK"
    f = http({"/liveChat/messages": [Resp(200), Resp(201), Resp(403, text="banned"), RuntimeError("x")]})
    assert client.send_live_chat_message("  hello  ") is True
    _, url, kw = f.calls[0]
    assert url == f"{yt.YOUTUBE_API_BASE}/liveChat/messages"
    assert kw["json"] == {"snippet": {"liveChatId": "CHAT", "type": "textMessageEvent",
                                      "textMessageDetails": {"messageText": "hello"}}}
    assert kw["headers"] == {"Authorization": "Bearer TOK", "Content-Type": "application/json"}
    assert kw["params"] == {"part": "snippet"} and kw["timeout"] == 15
    assert client.send_live_chat_message("x") is True
    assert client.send_live_chat_message("x") is False
    assert client.send_live_chat_message("x") is False


# --------------------------------------------------------------------------- chat message emit


@pytest.fixture
def collab(client, monkeypatch):
    rec = {"connector": [], "chatbot": []}
    monkeypatch.setattr(client, "_schedule_connector_event", lambda e: rec["connector"].append(e))
    monkeypatch.setattr(client, "_process_chatbot_event", lambda t, d: rec["chatbot"].append((t, d)))
    return rec


@pytest.fixture
def polls(monkeypatch):
    from modules import poll_manager

    calls = []
    pm = SimpleNamespace(try_moderator_start=lambda m: calls.append(("start", m)),
                         try_register_vote=lambda m: calls.append(("vote", m)))
    monkeypatch.setattr(poll_manager, "get_poll_manager", lambda: pm)
    return calls


def test_emit_chat_message_full(client, collab, polls, monkeypatch, clock):
    from modules import web_engine
    from modules.chatbot_core import EventType

    monkeypatch.setattr(yt, "_is_youtube_chat_overlay_enabled", lambda: True)
    shown = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(new_message=shown.append), raising=False)
    item = {"id": "M1", "snippet": {"textMessageDetails": {"messageText": "hello"}},
            "authorDetails": {"isChatModerator": True, "isChatOwner": False}}
    client._emit_youtube_chat_message(item, "Viewer", "UCv")
    ev = collab["connector"][0]
    assert ev["event_type"] == "youtube_chat_message" and ev["message"] == "hello" and ev["user_id"] == "UCv"
    t, d = collab["chatbot"][0]
    assert t == EventType.YOUTUBE_CHAT_MESSAGE and d["userid"] == "UCv" and d["source"] == "youtube"
    assert polls[0][0] == "start" and polls[0][1]["is_moderator"] is True and polls[0][1]["is_chat_owner"] is False
    assert polls[1][0] == "vote"
    msg = shown[0]
    assert msg["id"] == "M1" and msg["twmsgid"] == "M1" and msg["platform"] == "youtube" and msg["message"] == "hello"


def test_emit_chat_message_fallbacks(client, collab, polls, monkeypatch, clock):
    from modules import web_engine, poll_manager

    monkeypatch.setattr(yt, "_is_youtube_chat_overlay_enabled", lambda: True)
    shown = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(new_message=shown.append), raising=False)
    monkeypatch.setattr(poll_manager, "get_poll_manager", lambda: (_ for _ in ()).throw(RuntimeError("polls")))
    client._emit_youtube_chat_message({"snippet": {"displayMessage": "fallback"}}, "V", "")
    assert shown[0]["message"] == "fallback"
    assert shown[0]["id"] == f"yt-{int(clock.now * 1000)}" and shown[0]["twmsgid"] == ""

    # empty message: nothing at all
    client._emit_youtube_chat_message({"snippet": {}}, "V", "")
    assert len(collab["connector"]) == 1


def test_emit_chat_message_overlay_disabled_or_missing(client, collab, polls, monkeypatch):
    from modules import web_engine

    shown = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(new_message=shown.append), raising=False)
    monkeypatch.setattr(yt, "_is_youtube_chat_overlay_enabled", lambda: False)
    client._emit_youtube_chat_message({"snippet": {"displayMessage": "x"}}, "V", "")
    assert shown == [] and len(collab["chatbot"]) == 1

    monkeypatch.setattr(yt, "_is_youtube_chat_overlay_enabled", lambda: True)
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    client._emit_youtube_chat_message({"snippet": {"displayMessage": "x"}}, "V", "")
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(new_message=lambda m: (_ for _ in ()).throw(RuntimeError("x"))), raising=False)
    client._emit_youtube_chat_message({"snippet": {"displayMessage": "x"}}, "V", "")
    assert shown == []


# --------------------------------------------------------------------------- membership alerts


@pytest.fixture
def instant(client, monkeypatch):
    sent = []
    monkeypatch.setattr(client, "_send_instant_alert", sent.append)
    return sent


class _SlottedAlert:
    """An alert object that refuses a member_level attribute."""

    def __setattr__(self, k, v):
        if k == "member_level":
            raise AttributeError("read-only")
        object.__setattr__(self, k, v)


def test_membership_sub_alert(client, collab, alert_sinks, instant, monkeypatch, clock):
    from modules.chatbot_core import EventType

    template = alertutils.AlertObj()
    template.alert_name = "Sub template"
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: template)
    client._emit_membership_sub_alert("Fan", {"newSponsorDetails": {"memberLevelName": "Gold"},
                                              "displayMessage": "Welcome!"})
    assert collab["connector"][0]["event_type"] == "youtube_member"
    assert collab["connector"][0]["member_level"] == "Gold"
    t, d = collab["chatbot"][0]
    assert t == EventType.YOUTUBE_MEMBERSHIP and d["member_level"] == "Gold" and d["months"] == 1
    a = alert_sinks["queued"][0]
    assert a is template and (a.username, a.alert_type, a.tier, a.alert_id) == ("Fan", "sub", 1, "AID")
    assert a.member_level == "Gold" and a.timestamp == clock.now
    assert instant[0]["type"] == "sub" and instant[0]["member_level"] == "Gold"
    feed = alert_sinks["feed"][0]
    assert feed["message"] == "Fan became a channel member (Gold)!" and feed["badge_type"] == "membership"
    assert feed["timestamp"] == str(int(clock.now))


def test_membership_sub_alert_defaults_and_disabled(client, collab, alert_sinks, instant, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: None)
    client._emit_membership_sub_alert("Fan", {})
    assert isinstance(alert_sinks["queued"][0], alertutils.AlertObj)
    assert alert_sinks["feed"][0]["message"] == "Fan became a channel member!"

    client.youtube_data.alerts_enabled = False
    client._emit_membership_sub_alert("Fan", {})
    assert len(instant) == 1 and len(alert_sinks["feed"]) == 1 and len(collab["chatbot"]) == 2


def test_membership_alert_with_readonly_member_level(client, collab, alert_sinks, instant, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: _SlottedAlert())
    client._emit_membership_sub_alert("Fan", {"newSponsorDetails": {"memberLevelName": "Gold"}})
    assert len(alert_sinks["queued"]) == 1


@pytest.mark.parametrize("month, kind, feed_type, badge", [
    (1, "sub", "Membership", "membership"), (None, "sub", "Membership", "membership"),
    ("abc", "sub", "Membership", "membership"), (-4, "sub", "Membership", "membership"),
    (12, "resub", "Member Milestone", "member_milestone"),
])
def test_membership_resub_alert(client, collab, alert_sinks, instant, monkeypatch, month, kind, feed_type, badge):
    from modules.chatbot_core import EventType

    fetched = []
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: fetched.append(("sub", m)) or None)
    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: fetched.append(("resub", m)) or None)
    client._emit_membership_resub_alert("Fan", {"memberMilestoneChatDetails": {
        "memberMonth": month, "memberLevelName": "Gold", "userComment": "still here"}})
    months = 12 if kind == "resub" else 1
    assert fetched == [(kind, months)]
    ev = collab["connector"][0]
    assert ev["event_type"] == "youtube_member_milestone" and ev["months"] == months
    t, d = collab["chatbot"][0]
    assert t == EventType.YOUTUBE_MEMBERSHIP_MILESTONE and d["cumulative_months"] == months
    a = alert_sinks["queued"][0]
    assert a.alert_type == kind and a.resub_month == months and a.message == "still here"
    assert instant[0]["type"] == kind
    assert ("cumulative_months" in instant[0]) is (kind == "resub")
    feed = alert_sinks["feed"][0]
    assert feed["alert_type"] == feed_type and feed["badge_type"] == badge
    if kind == "resub":
        assert feed["message"] == "Fan continued membership for 12 months (Gold)!"
    else:
        assert feed["message"] == "Fan became a channel member (Gold)!"


def test_membership_resub_alert_no_level_and_disabled(client, collab, alert_sinks, instant, monkeypatch):
    tmpl = alertutils.AlertObj()
    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: tmpl)
    client._emit_membership_resub_alert("Fan", {"memberMilestoneChatDetails": {"memberMonth": 3},
                                                "displayMessage": "display"})
    assert alert_sinks["queued"][0] is tmpl and tmpl.message == "display"
    assert alert_sinks["feed"][0]["message"] == "Fan continued membership for 3 months!"

    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: _SlottedAlert())
    client.youtube_data.alerts_enabled = False
    client._emit_membership_resub_alert("Fan", {"memberMilestoneChatDetails": {"memberMonth": 5}})
    assert len(instant) == 1


@pytest.mark.parametrize("count, qty, plural", [(5, 5, "s"), (None, 1, ""), ("x", 1, ""), (0, 1, "")])
def test_membership_gift_alert(client, collab, alert_sinks, instant, monkeypatch, count, qty, plural):
    from modules.chatbot_core import EventType

    fetched = []
    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda q: fetched.append(q) or None)
    client._emit_membership_gift_alert("Gifter", {"membershipGiftingDetails": {
        "giftMembershipsCount": count, "giftMembershipsLevelName": "Gold"}, "displayMessage": "gifted!"})
    assert fetched == [qty]
    ev = collab["connector"][0]
    assert ev["event_type"] == "youtube_gift_membership" and ev["gift_count"] == qty
    t, d = collab["chatbot"][0]
    assert t == EventType.YOUTUBE_GIFT_MEMBERSHIP and d["quantity"] == qty and d["total_gifts"] == qty
    a = alert_sinks["queued"][0]
    assert a.alert_type == "giftsub" and a.gift_qty == qty and a.message == "gifted!"
    assert instant[0]["gift_qty"] == qty
    assert alert_sinks["feed"][0]["message"] == f"Gifter gifted {qty} membership{plural} (Gold)!"


def test_membership_gift_alert_template_no_level_and_disabled(client, collab, alert_sinks, instant, monkeypatch):
    tmpl = alertutils.AlertObj()
    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda q: tmpl)
    client._emit_membership_gift_alert("G", {"membershipGiftingDetails": {"giftMembershipsCount": 2}})
    assert alert_sinks["queued"][0] is tmpl
    assert alert_sinks["feed"][0]["message"] == "G gifted 2 memberships!"

    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda q: _SlottedAlert())
    client.youtube_data.alerts_enabled = False
    client._emit_membership_gift_alert("G", {})
    assert len(instant) == 1


# --------------------------------------------------------------------------- Super Chats


@pytest.fixture
def donations(monkeypatch):
    from modules import donation_alerts

    calls = []
    monkeypatch.setattr(donation_alerts, "emit_donation_alert", lambda **kw: calls.append(kw))
    return calls


def test_superchat(client, collab, donations):
    from modules.chatbot_core import EventType

    client._emit_superchat_donation_alert("Donor", {"superChatDetails": {
        "amountMicros": "5000000", "currency": "EUR", "amountDisplayString": "€5.00", "userComment": "gg"}},
        "superChatEvent", event_id="E1")
    ev = collab["connector"][0]
    assert ev["event_type"] == "youtube_superchat" and ev["amount"] == 5.0 and ev["display_amount"] == "€5.00"
    t, d = collab["chatbot"][0]
    assert t == EventType.YOUTUBE_SUPERCHAT and d["formatted_amount"] == "€5.00" and d["donation_message"] == "gg"
    assert donations == [{"username": "Donor", "amount": 5.0, "currency": "EUR", "message": "gg",
                          "source": "youtube", "event_id": "E1", "enqueue": True, "is_supersticker": False}]


def test_supersticker_alt_text_and_defaults(client, collab, donations):
    from modules.chatbot_core import EventType

    client.youtube_data.alerts_enabled = False
    client._emit_superchat_donation_alert("Donor", {"superStickerDetails": {
        "amountMicros": 2500000, "superStickerMetadata": {"altText": "dancing cat"}}}, "superStickerEvent")
    ev = collab["connector"][0]
    assert ev["event_type"] == "youtube_supersticker" and ev["currency"] == "USD" and ev["display_amount"] == "2.50"
    assert collab["chatbot"][0][0] == EventType.YOUTUBE_SUPERSTICKER
    assert donations[0]["message"] == "dancing cat" and donations[0]["is_supersticker"] is True
    assert donations[0]["enqueue"] is False and donations[0]["event_id"] == ""


def test_supersticker_without_meta_and_fan_funding(client, collab, donations):
    client._emit_superchat_donation_alert("D", {"superStickerDetails": {}, "displayMessage": "sticker!"},
                                          "superStickerEvent")
    assert donations[0]["message"] == "sticker!" and donations[0]["amount"] == 0.0
    client._emit_superchat_donation_alert("D", {"fanFundingEventDetails": {"amountMicros": "oops",
                                                                            "userComment": "thanks"}},
                                          "fanFundingEvent")
    assert donations[1]["amount"] == 0.0 and donations[1]["message"] == "thanks"
    assert collab["connector"][1]["display_amount"] == "0.00"


def test_send_instant_alert(client, monkeypatch):
    from modules import web_engine

    sent = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(instant_alert=sent.append), raising=False)
    client._send_instant_alert({"a": 1})
    assert sent == [{"a": 1}]
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    client._send_instant_alert({"a": 2})
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(instant_alert=lambda d: (_ for _ in ()).throw(RuntimeError("x"))), raising=False)
    client._send_instant_alert({"a": 3})
    assert sent == [{"a": 1}]


# --------------------------------------------------------------------------- module-level functions


class FakeYT:
    def __init__(self, **data):
        self.youtube_data = YouTubeData(**data)
        self.is_connected = False
        self.calls = []
        self.auth_result = True
        self.quota = False
        self.done = threading.Event()
        self.live_done = threading.Event()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def rec(*a, **k):
            self.calls.append((name,) + a)
            return {"authenticate": self.auth_result, "start_oauth_flow_with_server": True,
                    "send_live_chat_message": True, "has_oauth_tokens": False}.get(name)

        return rec

    def is_quota_blocked(self):
        return self.quota

    def start_monitoring(self):
        self.calls.append(("start_monitoring",))
        self.done.set()

    def start_live_chat_monitoring(self):
        self.calls.append(("start_live_chat_monitoring",))
        self.live_done.set()

    def update_video_data(self):
        self.calls.append(("update_video_data",))
        self.youtube_data.latest_video_url = "https://youtu.be/new"


@pytest.fixture
def mod(monkeypatch):
    monkeypatch.setattr(yt, "youtube_client", None)
    monkeypatch.setattr(yt, "youtube_thread", None)
    monkeypatch.setattr(yt, "youtube_live_thread", None)
    monkeypatch.setattr(yt, "is_running", False)

    def set_client(c):
        monkeypatch.setattr(yt, "youtube_client", c)
        return c

    return set_client


def test_initialize_youtube(mod, monkeypatch):
    made = []
    monkeypatch.setattr(yt, "YouTubeClient", lambda: made.append(1) or FakeYT())
    c = yt.initialize_youtube()
    assert yt.initialize_youtube() is c and made == [1] and yt.get_youtube_client() is c


def test_initialize_youtube_error(mod, monkeypatch):
    monkeypatch.setattr(yt, "YouTubeClient", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert yt.initialize_youtube() is None


@pytest.mark.parametrize("data, expected", [
    ({}, False), ({"api_key": "k"}, False), ({"channel_urls": "u"}, False),
    ({"api_key": "k", "channel_urls": "u"}, True),
])
def test_should_auto_initialize(mod, data, expected):
    assert yt.should_auto_initialize() is False
    mod(FakeYT(**data))
    assert yt.should_auto_initialize() is expected


def test_should_auto_initialize_error(mod):
    mod(SimpleNamespace())
    assert yt.should_auto_initialize() is False


def test_start_youtube_service_runs_both_loops(mod, monkeypatch):
    c = FakeYT(api_key="k", channel_urls="u")
    monkeypatch.setattr(yt, "initialize_youtube", lambda: c)
    assert yt.start_youtube_service() is True
    try:
        assert c.done.wait(2) and c.live_done.wait(2)
        assert yt.is_running is True and ("authenticate",) in c.calls
    finally:
        yt.youtube_thread.join(2)
        yt.youtube_live_thread.join(2)


def test_start_youtube_service_without_credentials(mod, monkeypatch):
    c = FakeYT()
    monkeypatch.setattr(yt, "initialize_youtube", lambda: c)
    assert yt.start_youtube_service() is True
    yt.youtube_thread.join(2)
    yt.youtube_live_thread.join(2)
    assert ("authenticate",) not in c.calls


class _T:
    def __init__(self, alive):
        self.alive = alive
        self.joined = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


def test_start_youtube_service_guards(mod, monkeypatch):
    monkeypatch.setattr(yt, "initialize_youtube", lambda: None)
    assert yt.start_youtube_service() is False  # no client

    monkeypatch.setattr(yt, "is_running", True)
    assert yt.start_youtube_service() is False
    monkeypatch.setattr(yt, "is_running", False)
    monkeypatch.setattr(yt, "youtube_thread", _T(True))
    assert yt.start_youtube_service() is False
    monkeypatch.setattr(yt, "youtube_thread", _T(False))
    monkeypatch.setattr(yt, "youtube_live_thread", _T(True))
    assert yt.start_youtube_service() is False

    monkeypatch.setattr(yt, "youtube_live_thread", None)
    monkeypatch.setattr(yt, "initialize_youtube", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert yt.start_youtube_service() is False


def test_stop_youtube_service(mod, monkeypatch):
    yt.stop_youtube_service()  # not running
    c = mod(FakeYT())
    t1, t2 = _T(True), _T(True)
    monkeypatch.setattr(yt, "youtube_thread", t1)
    monkeypatch.setattr(yt, "youtube_live_thread", t2)
    monkeypatch.setattr(yt, "is_running", True)
    yt.stop_youtube_service(join_timeout=0.5)
    assert ("stop_monitoring",) in c.calls and ("stop_live_chat_monitoring",) in c.calls
    assert yt.is_running is False and t1.joined == [0.5] and t2.joined == [0.5]


def test_stop_youtube_service_no_client_and_error(mod, monkeypatch):
    monkeypatch.setattr(yt, "is_running", True)
    yt.stop_youtube_service()
    assert yt.is_running is False

    mod(SimpleNamespace(stop_monitoring=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
    monkeypatch.setattr(yt, "is_running", True)
    yt.stop_youtube_service()
    assert yt.is_running is True


def test_get_youtube_status(mod, monkeypatch):
    from modules import connection_status_tracker as cst

    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: None)
    assert yt.get_youtube_status() == {"status": "Not Initialized", "is_connected": False, "latest_video": "N/A"}

    c = mod(FakeYT(connection_status="Connected (1 channels)"))
    c.has_oauth_tokens = lambda: True
    s = yt.get_youtube_status()
    assert s == {"status": "Connected (1 channels)", "is_connected": False, "latest_video": "No video found",
                 "channel_count": 0, "latest_video_channel": "N/A", "live_chat_status": "Not authorized",
                 "oauth_connected": True, "oauth_channel_title": "", "live_chat_enabled": True}
    c.is_connected = True
    c.youtube_data.channels = {"a": {}, "b": {}}
    c.youtube_data.latest_video_title = "Vid"
    c.youtube_data.latest_video_channel = "Chan"
    c.youtube_data.live_chat_status = "Live"
    c.youtube_data.oauth_channel_title = "Me"
    s = yt.get_youtube_status()
    assert (s["channel_count"], s["latest_video"], s["latest_video_channel"], s["live_chat_status"],
            s["oauth_channel_title"], s["is_connected"]) == (2, "Vid", "Chan", "Live", "Me", True)

    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "No internet" if key == "youtube" else None)
    s = yt.get_youtube_status()
    assert s["status"] == "No internet" and s["is_connected"] is False


def test_oauth_wrappers(mod, monkeypatch):
    monkeypatch.setattr(yt, "initialize_youtube", lambda: None)
    assert yt.trigger_youtube_oauth_flow() is False
    yt.disconnect_youtube_oauth()
    assert yt.send_youtube_chat_message("hi") is False
    yt.restart_youtube_live_chat_if_needed()

    c = FakeYT()
    monkeypatch.setattr(yt, "initialize_youtube", lambda: mod(c))
    assert yt.trigger_youtube_oauth_flow() is True
    assert [x[0] for x in c.calls] == ["load_youtube_data", "_sync_oauth_client_credentials",
                                       "start_oauth_flow_with_server"]
    c.calls.clear()
    yt.disconnect_youtube_oauth()
    assert [x[0] for x in c.calls] == ["load_youtube_data", "disconnect_oauth"]
    c.calls.clear()
    yt.restart_youtube_live_chat_if_needed()
    assert c.calls == [("load_youtube_data",)]
    assert yt.send_youtube_chat_message("hello") is True
    assert c.calls[-1] == ("send_live_chat_message", "hello")


def test_disconnect_oauth_wrapper_init_returns_none(mod, monkeypatch):
    monkeypatch.setattr(yt, "initialize_youtube", lambda: None)
    assert yt.disconnect_youtube_oauth() is None


def test_update_youtube_settings(mod, monkeypatch):
    monkeypatch.setattr(yt, "initialize_youtube", lambda: None)
    yt.update_youtube_settings(api_key="k")  # no client: nothing happens

    c = FakeYT()
    monkeypatch.setattr(yt, "initialize_youtube", lambda: mod(c))

    def update_field(field, value):
        c.calls.append(("update_field", field, value))
        setattr(c.youtube_data, field, value)

    c.update_field = update_field
    yt.update_youtube_settings(api_key="k", channel_url="u")
    assert c.calls == [("update_field", "api_key", "k"), ("update_field", "channel_urls", "u"),
                       ("update_field", "channels", {}), ("authenticate",)]
    c.calls.clear()
    c.is_connected = True
    yt.update_youtube_settings()
    assert c.calls == []
    c.is_connected = False
    c.youtube_data.api_key = ""
    yt.update_youtube_settings()
    assert c.calls == []


def test_get_latest_video_url(mod):
    assert yt.get_latest_video_url() is None
    c = mod(FakeYT(latest_video_url="https://youtu.be/x"))
    assert yt.get_latest_video_url() == "https://youtu.be/x"
    c.youtube_data.latest_video_url = ""
    assert yt.get_latest_video_url() is None
    c.is_connected = True
    assert yt.get_latest_video_url() == "https://youtu.be/new"


def test_trigger_authentication_and_test_connection(mod, monkeypatch):
    monkeypatch.setattr(yt, "initialize_youtube", lambda: None)
    assert yt.trigger_authentication() is False
    c = FakeYT()
    monkeypatch.setattr(yt, "initialize_youtube", lambda: mod(c))
    assert yt.trigger_authentication() is True
    c.auth_result = False
    assert yt.test_connection() is False


def test_youtube_configured_for_monitor(monkeypatch):
    from modules import connection_status_tracker as cst

    monkeypatch.setattr(cst, "youtube_configured", lambda: True)
    assert yt.youtube_configured_for_monitor() is True


def test_attempt_auto_reconnect(mod, monkeypatch):
    from modules import connection_monitor as cm

    net = {"internet": True, "service": True}
    monkeypatch.setattr(cm, "is_internet_available", lambda: net["internet"])
    monkeypatch.setattr(cm, "is_service_reachable", lambda name: net["service"] and name == "youtube")
    configured = {"v": True}
    monkeypatch.setattr(yt, "youtube_configured_for_monitor", lambda: configured["v"])

    assert yt.attempt_auto_reconnect() is False  # no client
    c = mod(FakeYT(api_key="k"))
    assert yt.attempt_auto_reconnect() is True
    configured["v"] = False
    assert yt.attempt_auto_reconnect() is False
    configured["v"] = True
    c.quota = True
    assert yt.attempt_auto_reconnect() is False
    c.quota = False
    c.youtube_data.api_key = " "
    assert yt.attempt_auto_reconnect() is False
    c.youtube_data.api_key = "k"
    net["internet"] = False
    assert yt.attempt_auto_reconnect() is False
    net["internet"] = True
    net["service"] = False
    assert yt.attempt_auto_reconnect() is False
    monkeypatch.setattr(cm, "is_internet_available", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert yt.attempt_auto_reconnect() is False
    assert c.calls.count(("authenticate",)) == 1

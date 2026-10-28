# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/youtube.py, part 2: authenticate, the latest-video update cycle, the monitor
loop, websocket payloads, and Google OAuth (URL, browser flow, token exchange/refresh, authenticated GET).

HTTP, time and state are faked; no browser opens and nothing reaches Google.
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


def quota_403():
    return Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}, text="quota")


URL1 = f"https://www.youtube.com/channel/{CID}"
CID2 = "UC" + "b" * 22
URL2 = f"https://www.youtube.com/channel/{CID2}"


def _channels_resp(title="My Channel", uploads="UU1"):
    content = {"relatedPlaylists": {"uploads": uploads}} if uploads else {}
    return Resp(200, {"items": [{"snippet": {"title": title}, "contentDetails": content}]})


def _video(vid, title="Video", published="2026-01-01T00:00:00Z", thumb="http://t", key="resourceId"):
    snip = {"title": title, "publishedAt": published, "thumbnails": {"default": {"url": thumb}}}
    if key == "resourceId":
        snip["resourceId"] = {"videoId": vid}
    elif key == "videoId":
        snip["videoId"] = vid
    return snip


# --------------------------------------------------------------------------- authenticate


def test_authenticate_requires_key_and_urls(client):
    assert client.authenticate() is False
    assert client.youtube_data.connection_status == "API Key Required"
    client.youtube_data.api_key = "KEY"
    assert client.authenticate() is False
    assert client.youtube_data.connection_status == "Channel URLs Required"
    assert client.is_connected is False


def test_authenticate_fetches_and_caches_channel(client, http):
    client.youtube_data.api_key = "KEY"
    client.youtube_data.channel_urls = URL1
    http({"/channels": _channels_resp()})
    assert client.authenticate() is True
    assert client.is_connected is True
    assert client.youtube_data.connection_status == "Connected (1 channels)"
    ch = client.youtube_data.channels[CID]
    assert ch["channel_title"] == "My Channel" and ch["channel_id"] == CID
    assert ch["channel_url"] == URL1 and ch["uploads_playlist_id"] == "UU1"


def test_authenticate_without_uploads_playlist(client, http):
    client.youtube_data.api_key = "KEY"
    client.youtube_data.channel_urls = URL1
    http({"/channels": _channels_resp(uploads=None)})
    assert client.authenticate() is True
    assert "uploads_playlist_id" not in client.youtube_data.channels[CID]


def test_authenticate_cached_channel_paths(client, http):
    client.youtube_data.api_key = "KEY"
    client.youtube_data.channel_urls = f"{URL1}|{URL2}"
    client.youtube_data.channels = {
        CID: {"uploads_playlist_id": "UU1", "channel_title": "Cached One"},
        CID2: {"uploads_playlist_id": "UU2"},
    }
    # first channel: refresh fails -> cached data used; second: refresh succeeds
    http({"/channels": [Resp(500, text="x"), _channels_resp("Fresh Two", "UU2")]})
    assert client.authenticate() is True
    assert client.youtube_data.connection_status == "Connected (2 channels)"
    assert client.youtube_data.channels[CID2]["channel_title"] == "Fresh Two"


def test_authenticate_partial_and_cached_without_playlist(client, http, monkeypatch):
    client.youtube_data.api_key = "KEY"
    client.youtube_data.channel_urls = f"{URL1}|{URL2}|https://example.com/nothing|https://www.youtube.com/@boom"
    client.youtube_data.channels = {CID: {"channel_title": "had data"}}  # cached but no playlist id

    real_extract = client.extract_channel_id_from_url

    def extract(url):
        if url.endswith("@boom"):
            raise RuntimeError("explode")
        return real_extract(url)

    monkeypatch.setattr(client, "extract_channel_id_from_url", extract)
    http({"/channels": Resp(500, text="x")})
    assert client.authenticate() is True  # URL1 counts via cached data, URL2 fails
    assert client.youtube_data.connection_status == "Partial (1/4 channels)"


def test_authenticate_all_fail(client, http):
    client.youtube_data.api_key = "KEY"
    client.youtube_data.channel_urls = URL1
    http({"/channels": Resp(500, text="x")})
    assert client.authenticate() is False
    assert client.youtube_data.connection_status == "All Channels Failed"
    assert client.is_connected is False


def test_authenticate_unexpected_error(client, monkeypatch):
    client.youtube_data.api_key = "KEY"
    monkeypatch.setattr(client, "parse_channel_urls", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert client.authenticate() is False
    assert client.youtube_data.connection_status == "Error: bad"


# --------------------------------------------------------------------------- update_video_data


def _connected(client, state, urls=URL1, channels=None):
    for target in (client.youtube_data, state.data):
        target.api_key = "KEY"
        target.channel_urls = urls
        target.channels = copy.deepcopy(channels or {})
    client.is_connected = True


def test_update_video_data_picks_newest_across_channels(client, state, http):
    _connected(client, state, f"{URL1}|{URL2}", {
        CID: {"channel_url": URL1, "channel_id": CID, "channel_title": "One", "uploads_playlist_id": "UU1"},
        CID2: {"channel_url": URL2, "channel_id": CID2, "channel_title": "Two", "uploads_playlist_id": "UU2"},
    })
    f = http({"/playlistItems": [
        Resp(200, {"items": [{"snippet": _video("old", "Older", "2026-01-01T00:00:00Z")}]}),
        Resp(200, {"items": [{"snippet": _video("new", "Newer", "2026-02-01T00:00:00Z", key="videoId")}]}),
    ]})
    client.update_video_data()
    d = client.youtube_data
    assert (d.latest_video_id, d.latest_video_title, d.latest_video_channel) == ("new", "Newer", "Two")
    assert d.latest_video_url == "https://www.youtube.com/watch?v=new"
    assert d.latest_video_thumbnail == "http://t" and d.last_updated == "2026-01-01 00:00:00"
    assert d.channels[CID]["latest_video_id"] == "old" and d.channels[CID]["uploads_playlist_id"] == "UU1"
    persisted = [f for f, _ in state.updates]
    assert persisted == list(yt._YOUTUBE_VIDEO_PERSIST_FIELDS)
    assert state.data.latest_video_id == "new"
    assert all(c[1].endswith("/playlistItems") for c in f.calls)


def test_update_video_data_unchanged_latest_not_persisted(client, state, http):
    _connected(client, state, URL1, {CID: {"channel_title": "One", "uploads_playlist_id": "UU1"}})
    state.data.latest_video_id = "same"
    http({"/playlistItems": Resp(200, {"items": [{"snippet": _video("same")}]})})
    client.update_video_data()
    assert state.updates == []


def test_update_video_data_caches_new_playlist_id(client, state, http):
    _connected(client, state, URL1, {})
    http({"/channels": _channels_resp("One", "UU7"),
          "/playlistItems": Resp(200, {"items": [{"snippet": _video("v1")}]})})
    client.update_video_data()
    ch = client.youtube_data.channels[CID]
    assert ch["uploads_playlist_id"] == "UU7" and ch["latest_video_id"] == "v1"
    assert client.youtube_data.latest_video_channel == "Unknown Channel"


def test_update_video_data_playlist_lookup_variants(client, state, monkeypatch):
    _connected(client, state, URL1, {})
    monkeypatch.setattr(client, "get_latest_videos", lambda cid, uploads_playlist_id=None: [_video("v1")])
    infos = iter([None, {"relatedPlaylists": {}}])
    monkeypatch.setattr(client, "get_channel_info", lambda cid: next(infos))
    client.update_video_data()
    assert "uploads_playlist_id" not in client.youtube_data.channels[CID]
    client.youtube_data.latest_video_id = ""
    client.update_video_data()
    assert "uploads_playlist_id" not in client.youtube_data.channels[CID]


def test_update_video_data_respects_playlist_filter(client, state, monkeypatch):
    _connected(client, state, URL1, {CID: {"channel_title": "One", "uploads_playlist_id": "UU1"}})
    monkeypatch.setattr(client, "get_latest_videos", lambda cid, uploads_playlist_id=None: [
        _video("short1"), {"title": "no id"}, _video("keep", "Keeper")])
    monkeypatch.setattr(client, "_get_excluded_video_ids", lambda cid: {"short1"})
    client.update_video_data()
    assert client.youtube_data.latest_video_id == "keep"

    monkeypatch.setattr(client, "_get_excluded_video_ids", lambda cid: {"short1", "keep"})
    client.youtube_data.latest_video_id = ""
    state.data.latest_video_id = ""
    client.update_video_data()
    assert client.youtube_data.latest_video_id == ""  # everything excluded


def test_update_video_data_skips_bad_channels(client, state, monkeypatch, caplog):
    _connected(client, state, f"https://example.com/x|{URL1}|{URL2}", {})
    monkeypatch.setattr(client, "get_latest_videos",
                        lambda cid, uploads_playlist_id=None: [] if cid == CID else (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level("WARNING", logger=yt.logger.name):
        client.update_video_data()
    assert any("any channels" in r.getMessage() for r in caplog.records)
    assert client.youtube_data.latest_video_id == ""


def test_update_video_data_guards(client, state, clock, monkeypatch):
    client._quota_exceeded_until = clock.now + 10
    monkeypatch.setattr(client, "authenticate", lambda: pytest.fail("no auth while quota blocked"))
    client.update_video_data()

    client._quota_exceeded_until = 0
    monkeypatch.setattr(client, "authenticate", lambda: False)
    loaded = []
    monkeypatch.setattr(client, "load_youtube_data", lambda: loaded.append(1))
    client.update_video_data()
    assert loaded == []

    client.is_connected = True
    client.youtube_data.channel_urls = ""
    client.update_video_data()  # no URLs after reload
    assert loaded == [1]


def test_update_video_data_slow_and_error(client, state, clock, monkeypatch, caplog):
    _connected(client, state, URL1, {CID: {"uploads_playlist_id": "UU1"}})

    def slow(cid, uploads_playlist_id=None):
        clock.now += 11
        return []

    monkeypatch.setattr(client, "get_latest_videos", slow)
    with caplog.at_level("WARNING", logger=yt.logger.name):
        client.update_video_data()
    assert any("Slow YouTube API" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(client, "load_youtube_data", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    client.update_video_data()  # logged


@pytest.mark.xfail(strict=True, reason="BUG: update_video_data reloads a fresh copy of YouTubeData after "
                                       "authenticate(), discarding the channel titles/URLs it just cached "
                                       "(never persisted), so the latest video's channel shows 'Unknown Channel'")
def test_update_video_data_keeps_channel_title_from_authenticate(client, state, http):
    for target in (client.youtube_data, state.data):
        target.api_key = "KEY"
        target.channel_urls = URL1
    http({"/channels": _channels_resp("My Channel", "UU1"),
          "/playlistItems": Resp(200, {"items": [{"snippet": _video("v1")}]})})
    assert client.authenticate() is True
    client.update_video_data()
    assert client.youtube_data.latest_video_channel == "My Channel"


def test_persist_video_fields_ignores_errors(client, state, monkeypatch):
    calls = []

    def flaky(field, value):
        calls.append(field)
        if field == "channels":
            raise RuntimeError("db")

    monkeypatch.setattr(state, "update_youtube_field", flaky)
    client._persist_video_fields()
    assert calls == list(yt._YOUTUBE_VIDEO_PERSIST_FIELDS)


# --------------------------------------------------------------------------- monitor loop / websocket


def test_start_monitoring_connected(client, clock, monkeypatch):
    client.is_connected = True
    seen = []
    monkeypatch.setattr(client, "update_video_data", lambda: seen.append("update"))
    monkeypatch.setattr(client, "send_websocket_data", lambda: seen.append("ws"))

    def sleep(s):
        seen.append(("sleep", s))
        client.running = False

    monkeypatch.setattr(client, "_interruptible_sleep", sleep)
    client.start_monitoring()
    assert seen == ["update", "ws", ("sleep", 1800.0)]


def test_start_monitoring_not_connected_paths(client, clock, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "update_video_data", lambda: seen.append("update"))
    monkeypatch.setattr(client, "_interruptible_sleep", lambda s: setattr(client, "running", False))
    client.youtube_data.api_key = "  "
    monkeypatch.setattr(client, "authenticate", lambda: pytest.fail("no key -> no auth"))
    client.start_monitoring()
    assert seen == []

    client.youtube_data.api_key = "KEY"
    results = iter([False, True])
    monkeypatch.setattr(client, "authenticate", lambda: next(results))
    client.start_monitoring()
    assert seen == []
    client.start_monitoring()
    assert seen == ["update"]


def test_start_monitoring_overrun_and_error(client, clock, monkeypatch):
    client.is_connected = True
    client.update_interval = 1.0
    sleeps = []
    n = {"i": 0}

    def update():
        n["i"] += 1
        if n["i"] == 1:
            clock.now += 5  # overruns the interval
        elif n["i"] == 2:
            raise RuntimeError("api broke")
        else:
            client.running = False
            clock.now += 5

    monkeypatch.setattr(client, "update_video_data", update)
    monkeypatch.setattr(client, "send_websocket_data", lambda: None)
    monkeypatch.setattr(client, "_interruptible_sleep", lambda s: sleeps.append(s))
    client.start_monitoring()
    assert sleeps == [30]


def test_send_websocket_data(client, monkeypatch):
    from modules import web_engine

    emitted = []
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(safe_emit=lambda ev, d: emitted.append((ev, d))), raising=False)
    client.youtube_data.api_key = "SECRETKEY"
    client.youtube_data.refresh_token = "SECRETRT"
    client.youtube_data.latest_video_title = "Vid"
    client.send_websocket_data()
    ev, d = emitted[0]
    assert ev == "youtube_data_update" and d["latest_video_title"] == "Vid"
    assert "SECRETKEY" not in repr(d) and "SECRETRT" not in repr(d)
    assert "api_key" not in d and "access_token" not in d

    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    client.send_websocket_data()
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(safe_emit=lambda *a: (_ for _ in ()).throw(RuntimeError("x"))), raising=False)
    client.send_websocket_data()
    assert len(emitted) == 1


def test_stop_and_disconnect(client):
    client.running = True
    client._live_running = True
    client.is_connected = True
    client.disconnect()
    assert (client.running, client._live_running, client.is_connected) == (False, False, False)
    assert client.youtube_data.connection_status == "Disconnected"


def test_sleep_helpers(client, clock):
    client.running = True
    client._interruptible_sleep(1.2)
    assert clock.sleeps == [0.5, 0.5, pytest.approx(0.2)]
    clock.sleeps.clear()
    client.running = False
    client._interruptible_sleep(5)
    assert clock.sleeps == []

    client._live_running = True
    client._live_sleep(0)  # at least 0.1s
    assert clock.sleeps == [pytest.approx(0.1)]
    clock.sleeps.clear()
    client._live_running = False
    client._live_sleep(60)
    assert clock.sleeps == []


# --------------------------------------------------------------------------- OAuth URL / flow


def test_oauth_credential_and_token_flags(client):
    assert client.has_oauth_credentials() is False and client.has_oauth_tokens() is False
    client.youtube_data.oauth_client_id = "id"
    client.youtube_data.oauth_client_secret = "  "
    assert client.has_oauth_credentials() is False
    client.youtube_data.oauth_client_secret = "s"
    client.youtube_data.refresh_token = "r"
    assert client.has_oauth_credentials() is True and client.has_oauth_tokens() is True


def test_build_oauth_url(client):
    assert client.build_oauth_url() == ""
    client.youtube_data.oauth_client_id = " my id&x "
    url = client.build_oauth_url()
    assert url.startswith(yt.YOUTUBE_OAUTH_AUTH_URL + "?client_id=my%20id%26x&")
    assert "redirect_uri=http%3A//127.0.0.1%3A9974" in url
    assert "scope=https%3A//www.googleapis.com/auth/youtube.force-ssl" in url
    assert "access_type=offline" in url and "prompt=consent" in url and "response_type=code" in url


class _FakeServer:
    def __init__(self, start_ok=True, result=None):
        self.start_ok = start_ok
        self.result = result if result is not None else {"success": True, "code": "CODE"}
        self.stopped = 0

    def start(self):
        return self.start_ok

    def stop(self):
        self.stopped += 1

    def wait_for_callback(self, timeout=300):
        return self.result


def _oauth_ready(client):
    client.youtube_data.oauth_client_id = "id"
    client.youtube_data.oauth_client_secret = "sec"


def test_oauth_flow_success(client, monkeypatch, _no_browser):
    _oauth_ready(client)
    srv = _FakeServer()
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: srv)
    codes = []
    monkeypatch.setattr(client, "complete_oauth_flow", lambda code: codes.append(code) or True)
    assert client.start_oauth_flow_with_server() is True
    assert codes == ["CODE"] and srv.stopped == 1
    assert _no_browser == [client.build_oauth_url()]
    assert client.youtube_data.live_chat_status == "Opening browser..."


def test_oauth_flow_failures(client, monkeypatch, _no_browser):
    assert client.start_oauth_flow_with_server() is False
    assert client.youtube_data.live_chat_status == "OAuth credentials required"

    _oauth_ready(client)
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: _FakeServer(start_ok=False))
    assert client.start_oauth_flow_with_server() is False
    assert client.youtube_data.live_chat_status == "OAuth server error"

    srv = _FakeServer()
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: srv)
    monkeypatch.setattr(client, "build_oauth_url", lambda: "")
    assert client.start_oauth_flow_with_server() is False
    assert srv.stopped == 1 and _no_browser == []


def test_oauth_flow_exchange_failure(client, monkeypatch):
    _oauth_ready(client)
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: _FakeServer())
    monkeypatch.setattr(client, "complete_oauth_flow", lambda code: False)
    assert client.start_oauth_flow_with_server() is False
    assert client.youtube_data.live_chat_status == "Token exchange failed"


@pytest.mark.parametrize("result, status", [
    ({"success": False, "error": "timeout"}, "Authorization timeout"),
    ({"success": False, "error": "access_denied"}, "Authorization error: access_denied"),
    ({"success": False}, "Authorization error: Unknown error"),
])
def test_oauth_flow_callback_errors(client, monkeypatch, result, status):
    _oauth_ready(client)
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: _FakeServer(result=result))
    assert client.start_oauth_flow_with_server() is False
    assert client.youtube_data.live_chat_status == status


def test_oauth_flow_exception(client, monkeypatch):
    _oauth_ready(client)
    monkeypatch.setattr(yt, "YouTubeOAuthCallbackServer", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert client.start_oauth_flow_with_server() is False
    assert client.youtube_data.live_chat_status == "OAuth error"


def test_complete_oauth_flow_success(client, state, http, monkeypatch):
    from datetime import datetime, timezone

    _oauth_ready(client)
    f = http({"/token": Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})})
    monkeypatch.setattr(client, "_fetch_oauth_channel_identity", lambda: True)
    assert client.complete_oauth_flow("CODE") is True
    _, url, kw = f.calls[0]
    assert url == yt.YOUTUBE_OAUTH_TOKEN_URL and kw["timeout"] == 15
    assert kw["data"] == {"grant_type": "authorization_code", "code": "CODE",
                          "redirect_uri": yt.YOUTUBE_OAUTH_REDIRECT_URI, "client_id": "id", "client_secret": "sec"}
    d = client.youtube_data
    assert d.access_token == "AT" and d.refresh_token == "RT" and d.live_chat_status == "Offline"
    expiry = datetime.fromisoformat(d.token_expiry)
    remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
    assert 3500 < remaining <= 3540
    assert state.saves == 1


def test_complete_oauth_flow_keeps_old_refresh_token_and_unknown_channel(client, http, monkeypatch):
    from datetime import datetime, timezone

    _oauth_ready(client)
    client.youtube_data.refresh_token = "OLD"
    http({"/token": Resp(200, {"access_token": "AT", "expires_in": 30})})
    monkeypatch.setattr(client, "_fetch_oauth_channel_identity", lambda: False)
    assert client.complete_oauth_flow("C") is True
    assert client.youtube_data.refresh_token == "OLD"
    assert client.youtube_data.live_chat_status == "Authorized (channel unknown)"
    remaining = (datetime.fromisoformat(client.youtube_data.token_expiry) - datetime.now(timezone.utc)).total_seconds()
    assert 55 < remaining <= 60  # floor of 60 seconds


def test_complete_oauth_flow_no_refresh_token_at_all(client, http, monkeypatch, state):
    http({"/token": Resp(200, {"access_token": "AT"})})
    monkeypatch.setattr(client, "_fetch_oauth_channel_identity", lambda: True)
    assert client.complete_oauth_flow("C") is True
    assert "refresh_token" not in [f for f, _ in state.updates]


def test_complete_oauth_flow_failures(client, http):
    http({"/token": [Resp(400, text="bad code"), Resp(200, {"expires_in": "soon"})]})
    assert client.complete_oauth_flow("C") is False
    assert client.complete_oauth_flow("C") is False  # int("soon") -> error path


def test_disconnect_oauth(client, state):
    d = client.youtube_data
    d.access_token, d.refresh_token, d.oauth_channel_id = "a", "r", "UCx"
    client._live_chat_id, client._live_page_token, client._live_bootstrap_done = "chat", "tok", True
    client._remember_message_id("m1")
    client.disconnect_oauth()
    assert (d.access_token, d.refresh_token, d.token_expiry, d.oauth_channel_id, d.oauth_channel_title) == ("",) * 5
    assert d.live_chat_status == "Not authorized"
    assert client._live_chat_id is None and client._live_page_token is None
    assert client._live_bootstrap_done is False
    assert client._seen_message_ids == set() and client._seen_message_id_order == []
    assert state.saves == 1


@pytest.mark.parametrize("raw, ok", [
    ("", False), ("   ", False), (None, False), ("garbage", False),
    ("2026-05-01T12:00:00Z", True), ("2026-05-01T12:00:00+02:00", True), ("2026-05-01T12:00:00", True),
])
def test_parse_token_expiry(client, raw, ok):
    client.youtube_data.token_expiry = raw
    dt = client._parse_token_expiry()
    assert (dt is not None) is ok
    if ok:
        assert dt.tzinfo is not None


# --------------------------------------------------------------------------- token refresh


def _future(seconds):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def test_ensure_valid_access_token(client, monkeypatch):
    refreshed = []
    monkeypatch.setattr(client, "_refresh_access_token", lambda: refreshed.append(1) or True)
    assert client.ensure_valid_access_token() is False  # no refresh token
    client.youtube_data.refresh_token = "r"
    client.youtube_data.access_token = "a"
    client.youtube_data.token_expiry = _future(600)
    assert client.ensure_valid_access_token() is True and refreshed == []
    client.youtube_data.token_expiry = _future(-10)
    assert client.ensure_valid_access_token() is True and refreshed == [1]
    client.youtube_data.access_token = ""
    client.youtube_data.token_expiry = _future(600)
    assert client.ensure_valid_access_token() is True and refreshed == [1, 1]


def test_refresh_access_token_locked(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_refresh_access_token", lambda: calls.append(1) or True)
    client.youtube_data.access_token = "newer"
    assert client._refresh_access_token_locked("stale") is True and calls == []
    client.youtube_data.access_token = "stale"
    assert client._refresh_access_token_locked("stale") is True and calls == [1]
    client.youtube_data.access_token = ""
    assert client._refresh_access_token_locked("stale") is True and calls == [1, 1]
    assert not client._refresh_lock.locked()


def test_refresh_access_token_success(client, state, http):
    _oauth_ready(client)
    client.youtube_data.refresh_token = " RT "
    f = http({"/token": [Resp(200, {"access_token": "A1", "expires_in": 120}),
                         Resp(200, {"access_token": "A2", "refresh_token": "RT2"})]})
    assert client._refresh_access_token() is True
    assert f.calls[0][2]["data"] == {"grant_type": "refresh_token", "refresh_token": "RT", "client_id": "id",
                                     "client_secret": "sec"}
    assert client.youtube_data.access_token == "A1" and client.youtube_data.refresh_token == " RT "
    assert client._refresh_access_token() is True
    assert client.youtube_data.refresh_token == "RT2" and state.saves == 2


@pytest.mark.parametrize("status, notified", [(400, True), (401, True), (503, False)])
def test_refresh_access_token_http_failure(client, http, notices, status, notified):
    client.youtube_data.refresh_token = "RT"
    http({"/token": Resp(status, text="invalid_grant")})
    assert client._refresh_access_token() is False
    assert client.youtube_data.live_chat_status == "Token refresh failed"
    assert bool(notices) is notified
    if notified:
        assert notices[0][1]["details"] == f"Token refresh: HTTP {status}: invalid_grant"


def test_refresh_access_token_no_token_and_error(client, http):
    assert client._refresh_access_token() is False
    client.youtube_data.refresh_token = "RT"
    http({"/token": ConnectionError("down")})
    assert client._refresh_access_token() is False


# --------------------------------------------------------------------------- authenticated GET


@pytest.fixture
def authed(client, monkeypatch):
    client.youtube_data.access_token = "TOK"
    monkeypatch.setattr(client, "ensure_valid_access_token", lambda: True)
    return client


def test_oauth_headers(client):
    client.youtube_data.access_token = "abc"
    assert client._oauth_headers() == {"Authorization": "Bearer abc"}


def test_oauth_get_success(authed, http):
    f = http({"/channels": Resp(200, {"items": []})})
    assert authed._oauth_get("/channels", {"mine": "true"}) == {"items": []}
    _, url, kw = f.calls[0]
    assert url == f"{yt.YOUTUBE_API_BASE}/channels"
    assert kw == {"headers": {"Authorization": "Bearer TOK"}, "params": {"mine": "true"}, "timeout": 15}


def test_oauth_get_guards(client, clock, monkeypatch, http):
    f = http({})
    client._quota_exceeded_until = clock.now + 1
    assert client._oauth_get("x", {}) is None
    client._quota_exceeded_until = 0
    monkeypatch.setattr(client, "ensure_valid_access_token", lambda: False)
    assert client._oauth_get("x", {}) is None
    assert f.calls == []


def test_oauth_get_401_refresh_and_retry(authed, http, monkeypatch):
    stale = []

    def refresh(token):
        stale.append(token)
        authed.youtube_data.access_token = "NEW"
        return True

    monkeypatch.setattr(authed, "_refresh_access_token_locked", refresh)
    f = http({"/x": [Resp(401), Resp(200, {"ok": 1})]})
    assert authed._oauth_get("x", {}) == {"ok": 1}
    assert stale == ["TOK"]
    assert f.calls[1][2]["headers"] == {"Authorization": "Bearer NEW"}


def test_oauth_get_401_refresh_fails(authed, http, monkeypatch):
    monkeypatch.setattr(authed, "_refresh_access_token_locked", lambda t: False)
    http({"/x": Resp(401)})
    assert authed._oauth_get("x", {}) is None


@pytest.mark.parametrize("reason", ["quotaExceeded", "dailyLimitExceeded"])
def test_oauth_get_403_quota(authed, http, clock, notices, reason):
    http({"/x": Resp(403, {"error": {"errors": [{"reason": reason}, "junk"]}})})
    assert authed._oauth_get("x", {}) is None
    assert authed._quota_exceeded_until == clock.now + 3600
    assert authed.youtube_data.live_chat_status == "Quota exceeded"
    assert len(notices) == 1


@pytest.mark.parametrize("reason", ["liveChatEnded", "liveChatDisabled"])
def test_oauth_get_403_chat_ended(authed, http, reason):
    http({"/x": Resp(403, {"error": {"errors": [{"reason": reason}]}})})
    assert authed._oauth_get("x", {}) == {"__live_chat_ended__": True}


def test_oauth_get_403_other_and_unparseable(authed, http):
    http({"/x": [Resp(403, {"error": {"errors": [{"reason": "forbidden"}]}}, text="nope"),
                 Resp(403, json_error=ValueError("html"), text="<html>")]})
    assert authed._oauth_get("x", {}) is None
    assert authed._oauth_get("x", {}) is None


def test_oauth_get_other_status_and_exception(authed, http):
    http({"/x": [Resp(500, text="err"), Resp(200, json_error=ValueError("bad json"))]})
    assert authed._oauth_get("x", {}) is None
    assert authed._oauth_get("x", {}) is None


def test_fetch_oauth_channel_identity(client, monkeypatch):
    responses = iter([
        None, {"items": []},
        {"items": [{"id": "UCme", "snippet": {"title": "Me"}}]},
        {"items": [{"id": "", "snippet": None}]},
    ])
    seen = []

    def get(path, params):
        seen.append((path, params))
        return next(responses)

    monkeypatch.setattr(client, "_oauth_get", get)
    assert client._fetch_oauth_channel_identity() is False
    assert client._fetch_oauth_channel_identity() is False
    assert client._fetch_oauth_channel_identity() is True
    assert client.youtube_data.oauth_channel_id == "UCme" and client.youtube_data.oauth_channel_title == "Me"
    assert client._fetch_oauth_channel_identity() is False
    assert client.youtube_data.oauth_channel_id == "UCme"
    assert seen[0] == ("channels", {"part": "snippet", "mine": "true"})

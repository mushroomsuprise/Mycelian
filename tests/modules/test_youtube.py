# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/youtube.py, part 1: notices, the OAuth callback server, and the
YouTubeClient's data/state helpers and Data API calls (channels, videos, playlists, quota).

HTTP goes through a fake ``requests.get``/``requests.post``; state through a fake state
manager that, like the real one, hands out copies. Nothing reaches Google.
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


# --------------------------------------------------------------------------- notices


def test_notify_quota(notices):
    from modules import notification_engine as ne

    yt._notify_youtube_quota()
    (args, kw), = notices
    assert kw["dedupe_key"] == "youtube:quota" and kw["type"] == "warning" and kw["source"] == "youtube"
    assert kw["actions"] == ne.nav_actions_settings("YouTube")
    assert "quota" in kw["title"].lower()


def test_notify_signin_expired(notices):
    yt._notify_youtube_signin_expired("HTTP 400: invalid_grant")
    (args, kw), = notices
    assert kw["dedupe_key"] == "youtube:auth" and kw["details"] == "HTTP 400: invalid_grant"


def test_notices_swallow_errors(monkeypatch):
    from modules import notification_engine as ne

    def boom(*a, **k):
        raise RuntimeError("toast broke")

    monkeypatch.setattr(ne, "notify", boom)
    yt._notify_youtube_quota()
    yt._notify_youtube_signin_expired("x")


# --------------------------------------------------------------------------- OAuth callback handler / server


class _Handler(yt._YouTubeOAuthCallbackHandler):
    def __init__(self, path, server, wfile=None):
        self.path = path
        self.server = server
        self.wfile = wfile or io.BytesIO()
        self.sent = []

    def send_response(self, code, message=None):
        self.sent.append(("status", code))

    def send_header(self, k, v):
        self.sent.append(("header", k, v))

    def end_headers(self):
        self.sent.append(("end",))


def _tcp():
    srv = object.__new__(yt._YouTubeOAuthTCPServer)
    srv.auth_code = None
    srv.auth_error = None
    return srv


def test_handler_code_error_and_noop():
    srv = _tcp()
    h = _Handler("/?code=c0de&scope=x", srv)
    h.do_GET()
    assert srv.auth_code == "c0de" and ("status", 200) in h.sent
    assert b"Authorization Successful" in h.wfile.getvalue()

    h = _Handler("/?error=access_denied", srv)
    h.do_GET()
    assert srv.auth_error == "access_denied" and ("status", 400) in h.sent
    assert b"access_denied" in h.wfile.getvalue()

    h = _Handler("/", srv)
    h.do_GET()
    assert h.sent == []
    assert h.log_message("%s", 1) is None


def test_handler_with_other_server_type():
    plain = SimpleNamespace()
    _Handler("/?code=x", plain).do_GET()
    _Handler("/?error=y", plain).do_GET()
    assert vars(plain) == {}


def test_handler_failure_sends_500():
    class Bad:
        def write(self, b):
            raise OSError("pipe")

    h = _Handler("/?error=x", _tcp(), wfile=Bad())
    h.do_GET()
    assert h.sent[-2:] == [("status", 500), ("end",)]


def test_oauth_server_real_roundtrip_on_port_0():
    server = yt.YouTubeOAuthCallbackServer(port=0)
    assert server.start() is True
    try:
        port = server.server.server_address[1]
        conn = http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/?error=denied")
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
        conn.close()
        assert server.wait_for_callback(timeout=1) == {"success": False, "error": "denied"}
    finally:
        server.stop()
    assert not server.server_thread.is_alive()


def test_oauth_server_start_failure_and_stop_paths(monkeypatch):
    def boom(*a, **k):
        raise OSError("in use")

    monkeypatch.setattr(yt, "_YouTubeOAuthTCPServer", boom)
    s = yt.YouTubeOAuthCallbackServer()
    assert s.port == yt.YOUTUBE_OAUTH_PORT
    assert s.start() is False
    s.stop()  # nothing to stop

    joined = []
    s.server_thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout: joined.append(timeout))
    s.stop()
    assert joined == [2]

    s.server = SimpleNamespace(shutdown=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    s.stop()  # logged


def test_wait_for_callback_code_and_timeout(clock):
    s = yt.YouTubeOAuthCallbackServer()
    s.server = _tcp()
    s.server.auth_code = "abc"
    assert s.wait_for_callback(timeout=5) == {"success": True, "code": "abc"}
    s.server = None
    assert s.wait_for_callback(timeout=1) == {"success": False, "error": "timeout"}
    assert clock.sleeps == [0.5, 0.5]


# --------------------------------------------------------------------------- chat overlay flag


def test_chat_overlay_enabled_reads_template(monkeypatch):
    from modules import template_config_parser as tcp

    seen = {}

    class Parser:
        def load_config(self, name, include_dynamic_controls=True):
            seen["args"] = (name, include_dynamic_controls)
            return {"cfg": True}

    monkeypatch.setattr(tcp, "TemplateConfigParser", Parser)
    monkeypatch.setattr(tcp, "_config_element_value", lambda cfg, el: el == "EnableYouTubeChat" and cfg["cfg"])
    assert yt._is_youtube_chat_overlay_enabled() is True
    assert seen["args"] == ("chat", False)

    monkeypatch.setattr(tcp, "_config_element_value", lambda cfg, el: None)
    assert yt._is_youtube_chat_overlay_enabled() is False


def test_chat_overlay_enabled_error(monkeypatch):
    from modules import template_config_parser as tcp

    class Parser:
        def load_config(self, *a, **k):
            raise OSError("missing")

    monkeypatch.setattr(tcp, "TemplateConfigParser", Parser)
    assert yt._is_youtube_chat_overlay_enabled() is False


# --------------------------------------------------------------------------- client: state


def test_client_init_defaults_and_loads(state, creds, clock):
    state.data = YouTubeData(api_key="k", channel_urls="https://youtube.com/@a")
    c = yt.YouTubeClient()
    assert state.initialized == 1
    assert c.youtube_data.api_key == "k" and c.youtube_data is not state.data
    assert c.update_interval == 1800.0 and c.is_connected is False
    assert c._live_chat_id is None and c._seen_message_ids == set()


def test_client_init_state_error(monkeypatch, creds, clock):
    monkeypatch.setattr(yt, "state_manager", FakeState(fail_get=True))
    c = yt.YouTubeClient()
    assert c.youtube_data == YouTubeData()


def test_is_quota_blocked(client, clock):
    assert client.is_quota_blocked() is False
    client._quota_exceeded_until = clock.now + 1
    assert client.is_quota_blocked() is True


def test_sync_oauth_client_credentials(client, state, creds):
    assert client._sync_oauth_client_credentials() is False
    creds["value"] = {"client_id": " cid ", "client_secret": " sec "}
    assert client._sync_oauth_client_credentials(persist=True) is True
    assert state.saves == 1
    assert client.youtube_data.oauth_client_id == "cid" and client.youtube_data.oauth_client_secret == "sec"
    assert client._sync_oauth_client_credentials(persist=True) is True  # unchanged -> no save
    assert state.saves == 1
    creds["value"] = {"client_id": None, "client_secret": "sec2"}
    assert client._sync_oauth_client_credentials(persist=False) is True
    assert state.saves == 1 and client.youtube_data.oauth_client_secret == "sec2"
    creds["value"] = {"client_id": "", "client_secret": ""}
    assert client._sync_oauth_client_credentials() is True  # keeps existing
    creds["value"] = RuntimeError("vault")
    assert client._sync_oauth_client_credentials() is True
    client.youtube_data.oauth_client_secret = ""
    assert client._sync_oauth_client_credentials() is False


@pytest.mark.parametrize("raw, urls", [
    ("", []), (" a | | b |c ", ["a", "b", "c"]), ("only", ["only"]),
])
def test_parse_channel_urls(client, raw, urls):
    client.youtube_data.channel_urls = raw
    assert client.parse_channel_urls() == urls


def test_get_channel_key(client, monkeypatch):
    assert client.get_channel_key(f"https://youtube.com/channel/{CID}") == CID
    monkeypatch.setattr(client, "extract_channel_id_from_url", lambda u: None)
    url = "https://example.com/x"
    assert client.get_channel_key(url) == hashlib.sha256(url.encode()).hexdigest()


def test_save_youtube_data(client, state, monkeypatch):
    client.youtube_data.api_key = "k"
    client.save_youtube_data()
    saved = state.set_calls[0]
    assert saved["api_key"] == "k" and set(saved) == set(YouTubeData.__dataclass_fields__)

    monkeypatch.setattr(state, "set_youtube_data", lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    client.save_youtube_data()  # logged


def test_update_field(client, state, monkeypatch):
    client.update_field("connection_status", "Hi")
    assert client.youtube_data.connection_status == "Hi" and state.updates == [("connection_status", "Hi")]
    client.update_field("nope", 1)
    assert len(state.updates) == 1
    monkeypatch.setattr(state, "update_youtube_field", lambda f, v: (_ for _ in ()).throw(RuntimeError("db")))
    client.update_field("api_key", "z")
    assert client.youtube_data.api_key == "z"


# --------------------------------------------------------------------------- channel id extraction / resolution


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/channel/{CID}",
    f"  https://youtube.com/channel/{CID}?si=xyz  ",
    f"https://m.youtube.com/channel/{CID}/videos",
])
def test_extract_channel_id_direct(client, url, http):
    f = http({})
    assert client.extract_channel_id_from_url(url) == CID
    assert f.calls == []


@pytest.mark.parametrize("url, ident", [
    ("https://www.youtube.com/@SomeHandle", "SomeHandle"),
    ("https://www.youtube.com/c/CustomName/featured", "CustomName"),
    ("https://www.youtube.com/user/OldUser", "OldUser"),
    ("https://www.youtube.com/channel/UCshort", "UCshort"),
])
def test_extract_channel_id_resolves_other_forms(client, monkeypatch, url, ident):
    seen = []
    monkeypatch.setattr(client, "_resolve_channel_identifier", lambda i: seen.append(i) or "UCresolved")
    assert client.extract_channel_id_from_url(url) == "UCresolved"
    assert seen == [ident]


def test_extract_channel_id_uses_cache(client, monkeypatch):
    url = "https://www.youtube.com/@Cached"
    client.youtube_data.channels = {
        "junk": "not a dict",
        "k1": {"channel_url": url, "channel_id": ""},
        "k2": {"channel_url": url, "channel_id": "UCcached"},
    }
    monkeypatch.setattr(client, "_resolve_channel_identifier", lambda i: pytest.fail("should use cache"))
    assert client.extract_channel_id_from_url(url) == "UCcached"


def test_extract_channel_id_unrecognised_and_error(client):
    assert client.extract_channel_id_from_url("https://example.com/watch?v=1") is None
    assert client.extract_channel_id_from_url(None) is None  # .strip() on None -> error path


def test_resolve_identifier_success(client, http):
    client.youtube_data.api_key = "KEY"
    f = http({"/search": Resp(200, {"items": [{"snippet": {"channelId": "UCfound"}}]})})
    assert client._resolve_channel_identifier("handle") == "UCfound"
    _, url, kw = f.calls[0]
    assert url == "https://www.googleapis.com/youtube/v3/search"
    assert kw["params"] == {"part": "snippet", "q": "handle", "type": "channel", "key": "KEY", "maxResults": 1}
    assert kw["timeout"] == 10


def test_resolve_identifier_guards_and_failures(client, http, clock, notices):
    f = http({"/search": [Resp(200, {"items": []}), Resp(500, text="err"), quota_403(),
                          ConnectionError("down")]})
    assert client._resolve_channel_identifier("x") is None  # no API key
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now + 5
    assert client._resolve_channel_identifier("x") is None
    assert f.calls == []
    client._quota_exceeded_until = 0
    assert client._resolve_channel_identifier("x") is None  # no items
    assert client._resolve_channel_identifier("x") is None  # 500
    assert client._resolve_channel_identifier("x") is None  # quota
    assert client.is_quota_blocked()
    client._quota_exceeded_until = 0
    assert client._resolve_channel_identifier("x") is None  # exception


# --------------------------------------------------------------------------- channel info / videos


def test_get_channel_info_merges_snippet_and_content(client, http, clock):
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now - 1
    f = http({"/channels": Resp(200, {"items": [{"snippet": {"title": "T"},
                                                  "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})})
    info = client.get_channel_info(CID)
    assert info == {"title": "T", "relatedPlaylists": {"uploads": "UU1"}}
    assert client._quota_exceeded_until == 0.0
    assert f.calls[0][2]["params"] == {"part": "snippet,contentDetails", "id": CID, "key": "KEY"}


def test_get_channel_info_failures(client, http, clock, notices):
    http({"/channels": [Resp(200, {"items": []}), Resp(404, text="nf"), quota_403(), ValueError("x")]})
    assert client.get_channel_info(CID) is None  # no key
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now + 10
    assert client.get_channel_info(CID) is None  # quota-blocked
    client._quota_exceeded_until = 0
    assert client.get_channel_info(CID) is None
    assert client.get_channel_info(CID) is None
    assert client.get_channel_info(CID) is None
    assert client.youtube_data.connection_status == "Quota Exceeded (retry in 1 hour)"
    client._quota_exceeded_until = 0
    assert client.get_channel_info(CID) is None


def test_get_latest_videos_with_cached_playlist(client, http, clock):
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now - 1
    f = http({"/playlistItems": Resp(200, {"items": [{"snippet": {"title": "v1"}}, {"snippet": {"title": "v2"}}]})})
    vids = client.get_latest_videos(CID, uploads_playlist_id="UU1", max_results=3)
    assert vids == [{"title": "v1"}, {"title": "v2"}]
    assert f.calls[0][2]["params"] == {"part": "snippet", "playlistId": "UU1", "key": "KEY", "maxResults": 3,
                                       "order": "date"}
    assert client._quota_exceeded_until == 0.0


def test_get_latest_videos_looks_up_playlist(client, http, monkeypatch):
    client.youtube_data.api_key = "KEY"
    monkeypatch.setattr(client, "get_channel_info", lambda cid: {"relatedPlaylists": {"uploads": "UU9"}})
    f = http({"/playlistItems": Resp(200, {"items": [{"snippet": {"title": "v"}}]})})
    assert client.get_latest_videos(CID) == [{"title": "v"}]
    assert f.calls[0][2]["params"]["playlistId"] == "UU9"

    monkeypatch.setattr(client, "get_channel_info", lambda cid: None)
    assert client.get_latest_videos(CID) == []
    monkeypatch.setattr(client, "get_channel_info", lambda cid: {"title": "no playlists"})
    assert client.get_latest_videos(CID) == []


def test_get_latest_videos_failures(client, http, clock, notices):
    http({"/playlistItems": [Resp(200, {"items": []}), Resp(500, text="x"), quota_403(), RuntimeError("x")]})
    assert client.get_latest_videos(CID, "UU") == []  # no key
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now + 1
    assert client.get_latest_videos(CID, "UU") == []
    client._quota_exceeded_until = 0
    assert client.get_latest_videos(CID, "UU") == []  # empty
    assert client.get_latest_videos(CID, "UU") == []  # 500
    assert client.get_latest_videos(CID, "UU") == []  # quota
    client._quota_exceeded_until = 0
    assert client.get_latest_videos(CID, "UU") == []  # exception


def test_handle_quota_error(client, clock, notices):
    assert client._handle_quota_error(Resp(500)) is False
    assert client._handle_quota_error(Resp(403, {"error": {"errors": [{"reason": "forbidden"}]}})) is False
    assert client._handle_quota_error(Resp(403, json_error=ValueError("html"))) is False
    assert client._handle_quota_error(Resp(403, {})) is False
    assert notices == []
    assert client._handle_quota_error(quota_403()) is True
    assert client._quota_exceeded_until == clock.now + 3600
    assert client.youtube_data.connection_status == "Quota Exceeded (retry in 1 hour)"
    assert len(notices) == 1


# --------------------------------------------------------------------------- playlists / exclusion


def test_get_channel_playlists(client, http, clock, notices):
    assert client.get_channel_playlists(CID) == []  # no key
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now + 1
    assert client.get_channel_playlists(CID) == []
    client._quota_exceeded_until = clock.now - 1
    f = http({"/playlists": [
        Resp(200, {"items": [{"id": "PL1", "snippet": {"title": "Shorts"}}, {"id": "PL2", "snippet": {"title": "VODs"}}]}),
        Resp(200, {}), Resp(500, text="x"), quota_403(), Resp(200, {"items": [{"id": "PL3"}]}),
    ]})
    assert client.get_channel_playlists(CID) == [{"playlist_id": "PL1", "title": "Shorts"},
                                                 {"playlist_id": "PL2", "title": "VODs"}]
    assert f.calls[0][2]["params"] == {"part": "snippet", "channelId": CID, "key": "KEY", "maxResults": 50}
    assert client._quota_exceeded_until == 0.0
    assert client.get_channel_playlists(CID) == []
    assert client.get_channel_playlists(CID) == []
    assert client.get_channel_playlists(CID) == []  # quota
    client._quota_exceeded_until = 0
    assert client.get_channel_playlists(CID) == []  # KeyError -> logged


def test_get_playlist_video_ids(client, http, clock, notices):
    assert client.get_playlist_video_ids("PL") == set()
    client.youtube_data.api_key = "KEY"
    client._quota_exceeded_until = clock.now + 1
    assert client.get_playlist_video_ids("PL") == set()
    client._quota_exceeded_until = clock.now - 1
    f = http({"/playlistItems": [
        Resp(200, {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {}}, {"snippet": {}},
                             {"contentDetails": {"videoId": "b"}}]}),
        Resp(500, text="x"), quota_403(), Resp(200, json_error=ValueError("bad")),
    ]})
    assert client.get_playlist_video_ids("PL") == {"a", "b"}
    assert f.calls[0][2]["params"] == {"part": "contentDetails", "playlistId": "PL", "key": "KEY", "maxResults": 50}
    assert client._quota_exceeded_until == 0.0
    assert client.get_playlist_video_ids("PL") == set()
    assert client.get_playlist_video_ids("PL") == set()
    client._quota_exceeded_until = 0
    assert client.get_playlist_video_ids("PL") == set()


def test_excluded_video_ids(client, monkeypatch):
    assert client._get_excluded_video_ids(CID) == set()  # no filter

    client.youtube_data.playlist_filter = ["shorts", "Clips"]
    calls = {"pl": 0}

    def playlists(cid):
        calls["pl"] += 1
        return [{"playlist_id": "PL1", "title": "Shorts"}, {"playlist_id": "PL2", "title": "CLIPS"},
                {"playlist_id": "PL3", "title": "Streams"}]

    monkeypatch.setattr(client, "get_channel_playlists", playlists)
    monkeypatch.setattr(client, "get_playlist_video_ids", lambda pid: {"PL1": {"a", "b"}, "PL2": {"b", "c"}}[pid])
    assert client._get_excluded_video_ids(CID) == {"a", "b", "c"}
    assert client._get_excluded_video_ids(CID) == {"a", "b", "c"}
    assert calls["pl"] == 1  # cached for the cycle


def test_excluded_video_ids_no_playlists_or_no_match(client, monkeypatch):
    client.youtube_data.playlist_filter = ["Shorts"]
    monkeypatch.setattr(client, "get_channel_playlists", lambda cid: [])
    assert client._get_excluded_video_ids("c1") == set()
    assert client._excluded_video_ids_cache["c1"] == set()
    monkeypatch.setattr(client, "get_channel_playlists", lambda cid: [{"playlist_id": "P", "title": "Other"}])
    assert client._get_excluded_video_ids("c2") == set()
    assert client._excluded_video_ids_cache["c2"] == set()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spotify.py, part 1: the OAuth callback server/handler and SpotifyClient.

HTTP goes through a fake ``requests.request``/``requests.post``, time through a fake clock,
state through a fake state manager; no browser is opened and nothing reaches Spotify.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import http.client
import io

import pytest

from modules import spotify as sp
from modules.dataobjects import SpotifyData


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self, data=None, fail_get=False):
        self.data = data if data is not None else SpotifyData()
        self.fail_get = fail_get
        self.updates = []
        self.saves = 0
        self.set_calls = []
        self.initialized = 0

    def initialize(self):
        self.initialized += 1

    def get_spotify_data(self):
        if self.fail_get:
            raise RuntimeError("db down")
        return self.data

    def update_spotify_field(self, field, value):
        self.updates.append((field, value))
        return True

    def save_changes(self):
        self.saves += 1

    def set_spotify_data(self, d):
        self.set_calls.append(d)
        return True


class FakeClock:
    """Replaces the ``time`` module inside modules.spotify."""

    def __init__(self, now=1000.0, mono=500.0):
        self.now = now
        self.mono = mono
        self.sleeps = []
        self.on_sleep = None

    def time(self):
        return self.now

    def monotonic(self):
        return self.mono

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s
        self.mono += s
        if self.on_sleep:
            self.on_sleep(s)


class Resp:
    def __init__(self, status=200, payload=None, headers=None, text="", json_error=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeHTTP:
    """Queue of responses for requests.request / requests.post."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def post(self, url, **kw):
        return self("POST", url, **kw)


@pytest.fixture
def state(monkeypatch):
    st = FakeState()
    monkeypatch.setattr(sp, "state_manager", st)
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

    monkeypatch.setattr(acm.api_credentials_manager, "get_spotify_credentials", get)
    return box


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(sp, "time", c)
    return c


@pytest.fixture
def http_fake(monkeypatch):
    def install(*responses):
        f = FakeHTTP(*responses)
        monkeypatch.setattr(sp.requests, "request", f)
        monkeypatch.setattr(sp.requests, "post", f.post)
        return f

    return install


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(sp.webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


@pytest.fixture
def client(state, creds, clock):
    c = sp.SpotifyClient()
    state.updates.clear()
    state.saves = 0
    return c


def _configure(c, **fields):
    for k, v in fields.items():
        setattr(c.spotify_data, k, v)


def _playback(name="Song", artist="Artist", progress=30000, duration=120000, playing=True, images=True,
              album=True, track_id="t1"):
    item = {"name": name, "artists": [{"name": artist}], "duration_ms": duration, "id": track_id}
    if album:
        item["album"] = {"name": "Album", "images": [{"url": "http://img"}] if images else []}
    return {"item": item, "progress_ms": progress, "is_playing": playing}


# --------------------------------------------------------------------------- OAuth callback handler


class _Handler(sp.OAuthCallbackHandler):
    def __init__(self, path, server, wfile=None):  # no socket handling
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


def _custom_server():
    srv = object.__new__(sp.CustomTCPServer)
    srv.auth_code = None
    srv.auth_error = None
    return srv


def test_handler_stores_code_and_returns_success_page():
    srv = _custom_server()
    h = _Handler("/?code=abc123&state=x", srv)
    h.do_GET()
    assert srv.auth_code == "abc123"
    assert ("status", 200) in h.sent and ("header", "Content-type", "text/html") in h.sent
    assert b"Authorization Successful" in h.wfile.getvalue()


def test_handler_stores_error_and_returns_error_page():
    srv = _custom_server()
    h = _Handler("/?error=access_denied", srv)
    h.do_GET()
    assert srv.auth_error == "access_denied" and srv.auth_code is None
    assert ("status", 400) in h.sent
    body = h.wfile.getvalue()
    assert b"Authorization Failed" in body and b"access_denied" in body


def test_handler_with_plain_server_object_still_responds():
    plain = SimpleNamespace()
    h = _Handler("/?code=zzz", plain)
    h.do_GET()
    assert not hasattr(plain, "auth_code")
    assert ("status", 200) in h.sent
    h2 = _Handler("/?error=nope", plain)
    h2.do_GET()
    assert ("status", 400) in h2.sent


def test_handler_without_params_sends_nothing():
    h = _Handler("/favicon.ico", _custom_server())
    h.do_GET()
    assert h.sent == [] and h.wfile.getvalue() == b""


def test_handler_write_failure_sends_500():
    class BadFile:
        def write(self, b):
            raise OSError("broken pipe")

    h = _Handler("/?code=abc", _custom_server(), wfile=BadFile())
    h.do_GET()
    assert h.sent[-2:] == [("status", 500), ("end",)]


def test_handler_log_message_is_silent():
    h = _Handler("/", _custom_server())
    assert h.log_message("%s", "x") is None


def test_callback_server_real_roundtrip_on_ephemeral_port():
    server = sp.OAuthCallbackServer(port=0)
    assert server.start() is True
    try:
        port = server.server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/?code=live-code")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        conn.close()
        assert server.server.auth_code == "live-code"
        assert server.wait_for_callback(timeout=1) == {"success": True, "code": "live-code"}
    finally:
        server.stop()
    assert not server.server_thread.is_alive()


def test_callback_server_start_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("address in use")

    monkeypatch.setattr(sp, "CustomTCPServer", boom)
    server = sp.OAuthCallbackServer(port=0)
    assert server.start() is False
    assert server.server is None


def test_callback_server_stop_without_start_and_with_errors():
    server = sp.OAuthCallbackServer()
    assert server.port == 9973
    server.stop()  # nothing started: no error

    class BadServer:
        def shutdown(self):
            raise RuntimeError("cannot shutdown")

    server.server = BadServer()
    server.stop()  # error is logged, not raised


def test_wait_for_callback_error_and_timeout(clock):
    server = sp.OAuthCallbackServer()
    srv = _custom_server()
    srv.auth_error = "access_denied"
    server.server = srv
    assert server.wait_for_callback(timeout=5) == {"success": False, "error": "access_denied"}

    server.server = None
    assert server.wait_for_callback(timeout=1.2) == {"success": False, "error": "timeout"}
    assert clock.sleeps == [0.5, 0.5, 0.5]


def test_wait_for_callback_picks_up_code_after_polling(clock):
    server = sp.OAuthCallbackServer()
    server.server = _custom_server()

    def arrive(_s):
        server.server.auth_code = "late"

    clock.on_sleep = arrive
    assert server.wait_for_callback(timeout=10) == {"success": True, "code": "late"}
    assert clock.sleeps == [0.5]


# --------------------------------------------------------------------------- client construction / data


def test_client_init_loads_state_and_syncs_credentials(state, creds, clock):
    state.data = SpotifyData(client_id="id", client_secret="sec", access_token="tok", token_expiry=5.0)
    creds["value"] = {"client_id": " newid ", "client_secret": ""}
    c = sp.SpotifyClient()
    assert state.initialized == 1
    assert c.spotify_data is state.data
    assert c.spotify_data.client_id == "newid"
    assert ("client_id", "newid") in state.updates
    assert state.saves == 1  # persisted because it changed
    assert c.is_authenticated is False and c.update_interval == 5.0


def test_client_init_without_credentials_logs_debug(state, creds, clock):
    c = sp.SpotifyClient()
    assert c.spotify_data.client_id == "" and state.saves == 0


def test_client_init_state_failure_uses_empty_data(monkeypatch, creds, clock):
    st = FakeState(fail_get=True)
    monkeypatch.setattr(sp, "state_manager", st)
    c = sp.SpotifyClient()
    assert isinstance(c.spotify_data, SpotifyData)
    assert c.spotify_data.client_id == ""


def test_reload_and_sync(client, state, creds):
    calls = []
    client._sync_client_credentials = lambda persist=False: calls.append(persist) or True
    client.reload_and_sync(persist=True)
    client.reload_and_sync(persist=False)
    assert calls == [True, True, True]  # load always syncs; persist adds one more
    assert state.initialized == 3


def test_sync_client_credentials_variants(client, state, creds):
    creds["value"] = {}
    assert client._sync_client_credentials() is False
    _configure(client, client_id="a", client_secret="b")
    assert client._sync_client_credentials() is True

    creds["value"] = {"client_id": "a", "client_secret": "b"}  # unchanged
    assert client._sync_client_credentials(persist=True) is True
    assert state.saves == 0

    creds["value"] = {"client_id": "a2", "client_secret": "b2"}
    assert client._sync_client_credentials(persist=False) is True
    assert state.saves == 0
    assert client.spotify_data.client_secret == "b2"

    creds["value"] = {"client_id": None, "client_secret": "b3"}
    assert client._sync_client_credentials(persist=True) is True
    assert state.saves == 1 and client.spotify_data.client_secret == "b3"

    creds["value"] = RuntimeError("vault locked")
    assert client._sync_client_credentials() is True
    _configure(client, client_secret="")
    assert client._sync_client_credentials() is False


def test_has_auth_tokens(client):
    assert client._has_auth_tokens() is False
    _configure(client, access_token="  ")
    assert client._has_auth_tokens() is False
    _configure(client, refresh_token="r")
    assert client._has_auth_tokens() is True
    _configure(client, refresh_token=None, access_token="a")
    assert client._has_auth_tokens() is True


def test_cooldown_and_backoff_flags(client, clock):
    assert not client.is_in_auth_cooldown() and not client.is_in_api_backoff()
    client._auth_failure_until = clock.mono + 1
    client._api_backoff_until = clock.mono + 1
    assert client.is_in_auth_cooldown() and client.is_in_api_backoff()


@pytest.mark.parametrize("status, skip", [
    ("Token Refresh Failed", True), ("Authorization Required", True), ("Not Configured", True),
    ("Token Refresh Error", True), ("Connected", False), ("", False), (None, False),
])
def test_should_skip_playback_poll_by_status(client, status, skip):
    client.spotify_data.connection_status = status
    assert client.should_skip_playback_poll() is skip
    client.is_authenticated = True
    assert client.should_skip_playback_poll() is False


def test_should_skip_playback_poll_during_backoff(client, clock):
    client.is_authenticated = True
    client._api_backoff_until = clock.mono + 10
    assert client.should_skip_playback_poll() is True
    client._api_backoff_until = 0
    client._auth_failure_until = clock.mono + 10
    assert client.should_skip_playback_poll() is True


def test_access_token_needs_refresh(client, clock):
    assert client._access_token_needs_refresh() is True  # no token
    _configure(client, access_token="tok", token_expiry=None)
    assert client._access_token_needs_refresh() is True
    _configure(client, token_expiry=clock.now + 100)
    assert client._access_token_needs_refresh() is False
    _configure(client, token_expiry=clock.now + 29)
    assert client._access_token_needs_refresh() is True


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("abc", None), ([1], None), ("12.5", 12.5), (7, 7.0),
])
def test_token_expiry_epoch(raw, expected):
    assert sp.SpotifyClient._token_expiry_epoch(raw) == expected


def test_clear_and_register_auth_failure(client, state, clock, monkeypatch):
    from modules import notification_engine as ne

    notices = []
    monkeypatch.setattr(ne, "notify", lambda *a, **k: notices.append((a, k)))
    client.is_authenticated = True
    client._register_auth_failure("invalid_grant")
    assert client._auth_failure_until == clock.mono + sp._AUTH_FAILURE_COOLDOWN_SEC
    assert client.is_authenticated is False
    assert client.spotify_data.connection_status == "Authorization Required"
    assert client._auth_recovery_logged is True
    assert notices == []  # only invalid_client is surfaced

    client._clear_auth_failure_state()
    assert (client._auth_failure_until, client._last_auth_error_code, client._auth_recovery_logged) == (0.0, "", False)

    client._register_auth_failure("invalid_client")
    assert len(notices) == 1
    kw = notices[0][1]
    assert kw["dedupe_key"] == "spotify:bad_credentials" and kw["source"] == "spotify"
    assert kw["actions"] == ne.nav_actions_settings("Spotify")

    client._register_auth_failure("invalid_client")  # already logged: no second notice
    assert len(notices) == 1


def test_register_auth_failure_notice_error_is_swallowed(client, monkeypatch):
    from modules import notification_engine as ne

    def boom(*a, **k):
        raise RuntimeError("toast failed")

    monkeypatch.setattr(ne, "notify", boom)
    client._register_auth_failure("invalid_client")
    assert client._last_auth_error_code == "invalid_client"


@pytest.mark.parametrize("header, wait", [
    (None, 30.0), ("12", 12.0), ("soon", 30.0), ("0", 1.0), ("99999", 3600.0),
])
def test_apply_rate_limit_backoff(client, clock, header, wait):
    headers = {} if header is None else {"Retry-After": header}
    client._apply_rate_limit_backoff(Resp(429, headers=headers))
    assert client._api_backoff_until == clock.mono + wait


def test_parse_oauth_error(client):
    assert client._parse_oauth_error(Resp(400, {"error": "invalid_grant"})) == "invalid_grant"
    assert client._parse_oauth_error(Resp(400, {"error": None})) == ""
    assert client._parse_oauth_error(Resp(400, json_error=ValueError("not json"))) == ""
    assert client._parse_oauth_error(Resp(400, payload=["list"])) == ""  # AttributeError


def test_api_request_success_and_429(client, clock, http_fake):
    f = http_fake(Resp(200), Resp(429, headers={"Retry-After": "5"}))
    r = client._api_request("get", "https://x", headers={"h": "1"}, data={"d": "2"})
    assert r.status_code == 200
    assert f.calls[0] == ("GET", "https://x", {"headers": {"h": "1"}, "data": {"d": "2"}, "timeout": 10})
    r = client._api_request("post", "https://y")
    assert r.status_code == 429 and client._api_backoff_until == clock.mono + 5


def test_api_request_skips_during_backoff_and_cooldown(client, clock, http_fake):
    f = http_fake(Resp(200))
    client._api_backoff_until = clock.mono + 1
    assert client._api_request("GET", "u") is None
    client._api_backoff_until = 0
    client._auth_failure_until = clock.mono + 1
    assert client._api_request("GET", "u") is None
    assert f.calls == []
    assert client._api_request("GET", "u", allow_during_auth_cooldown=True).status_code == 200


def test_api_request_exception_returns_none(client, http_fake):
    http_fake(ConnectionError("offline"))
    assert client._api_request("GET", "u") is None


def test_monitor_sleep_seconds(client, clock):
    client.update_interval_playing = 2.0
    client.update_interval_idle = 7.0
    assert client._get_monitor_sleep_seconds() == 7.0
    client._last_playback_state = "playing"
    assert client._get_monitor_sleep_seconds() == 2.0
    client._api_backoff_until = clock.mono + 40
    assert client._get_monitor_sleep_seconds() == 40
    client._auth_failure_until = clock.mono + 90
    assert client._get_monitor_sleep_seconds() == 90


def test_interruptible_sleep(client, clock):
    client.running = True
    client._interruptible_sleep(1.2)
    assert clock.sleeps == [0.5, 0.5, pytest.approx(0.2)]

    clock.sleeps.clear()
    client.running = False
    client._interruptible_sleep(10)
    assert clock.sleeps == []

    client.running = True
    client._interruptible_sleep(-3)
    assert clock.sleeps == []

    def stop(_s):
        client.running = False

    clock.on_sleep = stop
    client._interruptible_sleep(100)
    assert clock.sleeps == [0.5]


def test_save_spotify_data(client, state, monkeypatch):
    client.spotify_data.track_name = "X"
    client.save_spotify_data()
    assert state.set_calls == [asdict(client.spotify_data)]

    def boom(d):
        raise RuntimeError("write failed")

    monkeypatch.setattr(state, "set_spotify_data", boom)
    client.save_spotify_data()  # logged, not raised


def test_update_field(client, state, monkeypatch):
    client.update_field("track_name", "Hello")
    assert client.spotify_data.track_name == "Hello"
    assert state.updates == [("track_name", "Hello")]

    client.update_field("not_a_field", 1)
    assert not hasattr(client.spotify_data, "not_a_field")
    assert len(state.updates) == 1

    def boom(f, v):
        raise RuntimeError("db")

    monkeypatch.setattr(state, "update_spotify_field", boom)
    client.update_field("album_name", "A")  # error logged
    assert client.spotify_data.album_name == "A"


# --------------------------------------------------------------------------- token refresh


def _ready(c, clock, *, access="", expiry=None):
    _configure(c, client_id="id", client_secret="sec", refresh_token="rt", access_token=access, token_expiry=expiry)


def test_refresh_token_success_updates_everything(client, state, clock, http_fake):
    _ready(client, clock)
    client._auth_failure_until = 1.0
    f = http_fake(Resp(200, {"access_token": "new", "refresh_token": "rt2", "expires_in": 600}))
    assert client.refresh_token() is True
    method, url, kw = f.calls[0]
    assert method == "POST" and url == "https://accounts.spotify.com/api/token"
    assert kw["data"] == {"grant_type": "refresh_token", "refresh_token": "rt", "client_id": "id",
                          "client_secret": "sec"}
    d = client.spotify_data
    assert (d.access_token, d.refresh_token, d.token_expiry) == ("new", "rt2", clock.now + 600 - 30)
    assert d.connection_status == "Connected" and client.is_authenticated is True
    assert client._auth_failure_until == 0.0
    assert state.saves == 1


def test_refresh_token_default_expiry_and_keeps_refresh_token(client, clock, http_fake):
    _ready(client, clock)
    http_fake(Resp(200, {"access_token": "new"}))
    assert client.refresh_token(force=True) is True
    assert client.spotify_data.refresh_token == "rt"
    assert client.spotify_data.token_expiry == clock.now + 3600 - 30


def test_refresh_token_skips_when_still_valid(client, clock, http_fake):
    _ready(client, clock, access="tok", expiry=clock.now + 1000)
    f = http_fake()
    assert client.refresh_token() is True
    assert f.calls == []


def test_refresh_token_missing_pieces(client, clock, http_fake):
    f = http_fake()
    assert client.refresh_token() is False  # no refresh token
    _configure(client, refresh_token="rt")
    assert client.refresh_token() is False  # no client id/secret
    assert f.calls == []


def test_refresh_token_in_cooldown_returns_current_token(client, clock, http_fake):
    _ready(client, clock, access="tok")
    client._auth_failure_until = clock.mono + 100
    f = http_fake()
    assert client.refresh_token() is True
    _configure(client, access_token="")
    assert client.refresh_token() is False
    assert f.calls == []


def test_refresh_token_in_backoff(client, clock, http_fake):
    _ready(client, clock, access="tok", expiry=None)
    client._api_backoff_until = clock.mono + 100
    f = http_fake()
    assert client.refresh_token() is True
    assert f.calls == []


def test_refresh_token_request_failed_returns_token_presence(client, clock, http_fake):
    _ready(client, clock, access="")
    http_fake(OSError("offline"))
    assert client.refresh_token(force=True) is False


@pytest.mark.parametrize("err", ["invalid_client", "invalid_grant"])
def test_refresh_token_oauth_rejection_registers_failure(client, clock, http_fake, err, monkeypatch):
    from modules import notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda *a, **k: None)
    _ready(client, clock)
    http_fake(Resp(400, {"error": err}))
    assert client.refresh_token(force=True) is False
    assert client._last_auth_error_code == err
    assert client.spotify_data.connection_status == "Authorization Required"
    assert client.is_in_auth_cooldown()


def test_refresh_token_other_failure_marks_refresh_failed(client, clock, http_fake):
    _ready(client, clock)
    client.is_authenticated = True
    http_fake(Resp(500, {"error": "server_error"}, text="oops"))
    assert client.refresh_token(force=True) is False
    assert client.is_authenticated is False
    assert client.spotify_data.connection_status == "Token Refresh Failed"


def test_refresh_token_bad_payload_marks_error(client, clock, http_fake):
    _ready(client, clock)
    http_fake(Resp(200, {"no_access": True}))  # KeyError
    assert client.refresh_token(force=True) is False
    assert client.spotify_data.connection_status == "Token Refresh Error"


def test_refresh_token_waits_for_inflight_refresh(client, clock):
    _ready(client, clock, access="tok", expiry=clock.now + 1000)
    results = []
    client._refresh_lock.acquire()
    t = threading.Thread(target=lambda: results.append(client.refresh_token()))
    t.start()
    t.join(0.05)
    client._refresh_lock.release()
    t.join(2)
    assert results == [True]

    # forced: only reports whether a token exists
    client._refresh_lock.acquire()
    _configure(client, access_token=" ")
    t = threading.Thread(target=lambda: results.append(client.refresh_token(force=True)))
    t.start()
    t.join(0.05)
    client._refresh_lock.release()
    t.join(2)
    assert results == [True, False]

    # not forced, token present but expired -> falls through to token-presence check
    client._refresh_lock.acquire()
    _configure(client, access_token="tok", token_expiry=clock.now)
    t = threading.Thread(target=lambda: results.append(client.refresh_token()))
    t.start()
    t.join(0.05)
    client._refresh_lock.release()
    t.join(2)
    assert results == [True, False, True]
    assert not client._refresh_lock.locked()


# --------------------------------------------------------------------------- playback


def _authed(c, clock):
    _ready(c, clock, access="tok", expiry=clock.now + 1000)


def test_get_current_playback_200(client, clock, http_fake):
    _authed(client, clock)
    f = http_fake(Resp(200, {"is_playing": True}))
    assert client.get_current_playback() == {"is_playing": True}
    assert client._last_api_success is True
    method, url, kw = f.calls[0]
    assert url == "https://api.spotify.com/v1/me/player"
    assert kw["headers"] == {"Authorization": "Bearer tok"}


def test_get_current_playback_204(client, clock, http_fake):
    _authed(client, clock)
    http_fake(Resp(204))
    assert client.get_current_playback() is None
    assert client._last_api_success is True


def test_get_current_playback_skips_in_backoff_or_cooldown(client, clock, http_fake):
    _authed(client, clock)
    f = http_fake()
    client._api_backoff_until = clock.mono + 5
    assert client.get_current_playback() is None
    client._api_backoff_until = 0
    client._auth_failure_until = clock.mono + 5
    assert client.get_current_playback() is None
    assert f.calls == [] and client._last_api_success is False


def test_get_current_playback_without_access_token(client, clock, http_fake):
    f = http_fake()
    assert client.get_current_playback() is None  # no refresh token either
    _ready(client, clock)
    client.refresh_token = lambda force=False: False
    assert client.get_current_playback() is None
    assert f.calls == []


def test_get_current_playback_refreshes_missing_token_first(client, clock, http_fake):
    _ready(client, clock)
    f = http_fake(Resp(200, {"access_token": "fresh", "expires_in": 3600}), Resp(200, {"ok": 1}))
    assert client.get_current_playback() == {"ok": 1}
    assert f.calls[1][2]["headers"]["Authorization"] == "Bearer fresh"


def test_get_current_playback_refresh_check_fails(client, clock):
    _authed(client, clock)
    client.refresh_token = lambda force=False: False
    assert client.get_current_playback() is None


def test_get_current_playback_request_none_and_429(client, clock, http_fake):
    _authed(client, clock)
    http_fake(OSError("down"))
    assert client.get_current_playback() is None
    http_fake(Resp(429, headers={"Retry-After": "3"}))
    assert client.get_current_playback() is None
    assert client.is_in_api_backoff()


@pytest.mark.parametrize("code", [401, 403])
def test_get_current_playback_rejected_token_retries_once(client, clock, http_fake, code):
    _authed(client, clock)
    f = http_fake(Resp(code), Resp(200, {"access_token": "t2"}), Resp(200, {"after": "retry"}))
    assert client.get_current_playback() == {"after": "retry"}
    assert [c[1] for c in f.calls] == ["https://api.spotify.com/v1/me/player",
                                       "https://accounts.spotify.com/api/token",
                                       "https://api.spotify.com/v1/me/player"]


def test_get_current_playback_rejected_token_refresh_fails(client, clock, http_fake):
    _authed(client, clock)
    client.is_authenticated = True
    http_fake(Resp(401), Resp(500, text="bad"))
    assert client.get_current_playback() is None
    assert client.is_authenticated is False
    assert client.spotify_data.connection_status == "Authorization Required"


def test_get_current_playback_rejected_twice_stops(client, clock, http_fake):
    _authed(client, clock)
    f = http_fake(Resp(401), Resp(200, {"access_token": "t2"}), Resp(401))
    assert client.get_current_playback() is None
    assert len(f.calls) == 3  # no third attempt
    assert client.spotify_data.connection_status == "Authorization Required"


def test_get_current_playback_502_retries(client, clock, http_fake):
    _authed(client, clock)
    f = http_fake(Resp(502), Resp(200, {"ok": True}))
    assert client.get_current_playback() == {"ok": True}
    assert len(f.calls) == 2

    f = http_fake(Resp(502), Resp(502))
    assert client.get_current_playback() is None
    assert len(f.calls) == 2


def test_get_current_playback_502_refresh_fails(client, clock, http_fake):
    _authed(client, clock)
    http_fake(Resp(502))
    seq = iter([True, False])
    client.refresh_token = lambda force=False: next(seq)
    assert client.get_current_playback() is None


def test_get_current_playback_unexpected_status_and_exception(client, clock, http_fake):
    _authed(client, clock)
    http_fake(Resp(418, text="teapot"))
    assert client.get_current_playback() is None
    assert client._last_api_success is False
    http_fake(Resp(200, json_error=ValueError("bad json")))
    assert client.get_current_playback() is None


# --------------------------------------------------------------------------- authenticate


def test_authenticate_success(client, clock, http_fake):
    _authed(client, clock)
    http_fake(Resp(204))
    assert client.authenticate() is True
    assert client.is_authenticated is True
    assert client.spotify_data.connection_status == "Connected"


def test_authenticate_in_cooldown(client, clock):
    client._auth_failure_until = clock.mono + 10
    assert client.authenticate() is False


def test_authenticate_not_configured(client, clock):
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Not Configured"


def test_authenticate_refreshes_expired_token(client, clock, http_fake):
    _ready(client, clock, access="old", expiry=clock.now - 1)
    http_fake(Resp(200, {"access_token": "new"}), Resp(200, {"is_playing": False}))
    assert client.authenticate() is True
    assert client.spotify_data.access_token == "new"


def test_authenticate_refresh_fails(client, clock, http_fake):
    _ready(client, clock)
    http_fake(Resp(500, text="x"))
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Authorization Required"


def test_authenticate_refresh_fails_into_cooldown(client, clock, http_fake, monkeypatch):
    from modules import notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda *a, **k: None)
    _ready(client, clock)
    http_fake(Resp(400, {"error": "invalid_grant"}))
    assert client.authenticate() is False
    assert client.is_in_auth_cooldown()


def test_authenticate_without_any_token(client, clock):
    _configure(client, client_id="id", client_secret="sec")
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Authorization Required"


def test_authenticate_cooldown_after_refresh(client, clock):
    _authed(client, clock)
    calls = iter([False, True])
    client.is_in_auth_cooldown = lambda: next(calls)
    assert client.authenticate() is False


def test_authenticate_playback_failure(client, clock, http_fake):
    _authed(client, clock)
    http_fake(Resp(418))
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Authorization Required"


def test_authenticate_playback_failure_in_cooldown_keeps_status(client, clock):
    _authed(client, clock)
    client.spotify_data.connection_status = "Custom"

    def fail_playback():
        client._auth_failure_until = clock.mono + 50
        client._last_api_success = False

    client.get_current_playback = fail_playback
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Custom"


def test_authenticate_exception(client, clock):
    _authed(client, clock)

    def boom():
        raise RuntimeError("bad")

    client.get_current_playback = boom
    assert client.authenticate() is False
    assert client.spotify_data.connection_status == "Authentication Error"


# --------------------------------------------------------------------------- formatting / playback data


@pytest.mark.parametrize("ms, text", [
    (0, "0:00"), (None, "0:00"), (5000, "0:05"), (65000, "1:05"), ("125000", "2:05"), (59999, "0:59"),
])
def test_millis_to_timestamp(client, ms, text):
    assert client.millis_to_timestamp(ms) == text


@pytest.mark.xfail(strict=True, reason="BUG: millis_to_timestamp drops hours (minutes % 60), so a 75-minute "
                                       "podcast/mix shows as 15:00")
def test_millis_to_timestamp_over_an_hour(client):
    assert client.millis_to_timestamp(75 * 60 * 1000) in ("75:00", "1:15:00")


def test_set_empty_track_data(client):
    client.spotify_data.track_name = "Song"
    client.spotify_data.is_playing = True
    client.set_empty_track_data()
    d = client.spotify_data
    assert (d.track_name, d.artist_name, d.album_name, d.album_image_url) == ("Nothing playing", "Nothing playing", "", "")
    assert (d.current_tracktime, d.track_length, d.is_playing, d.progress_percentage) == ("0:00", "0:00", False, 0.0)


def test_update_playback_data_full(client):
    client.get_current_playback = lambda: _playback()
    client.update_playback_data()
    d = client.spotify_data
    assert (d.track_name, d.artist_name, d.album_name, d.album_image_url) == ("Song", "Artist", "Album", "http://img")
    assert (d.current_tracktime, d.track_length) == ("0:30", "2:00")
    assert d.current_tracktime_seconds == 30 and d.track_length_seconds == 120
    assert d.progress_percentage == 25 and d.is_playing is True
    assert client.last_track_id == "t1" and client._last_playback_state == "playing"

    client.get_current_playback = lambda: _playback(playing=False, images=False, duration=0, progress=0)
    client.update_playback_data()
    assert client.spotify_data.album_image_url == "" and client.spotify_data.progress_percentage == 0
    assert client._last_playback_state == "idle"


def test_update_playback_data_no_album(client):
    client.get_current_playback = lambda: _playback(album=False, track_id="t9")
    client.update_playback_data()
    assert client.spotify_data.album_name == "" and client.spotify_data.album_image_url == ""
    assert client.last_track_id == "t9"


def test_update_playback_data_none(client):
    client.spotify_data.track_name = "Old"
    client._last_api_success = False
    client.get_current_playback = lambda: None
    client.update_playback_data()
    assert client.spotify_data.track_name == "Old"  # transient failure keeps data

    def idle():
        client._last_api_success = True
        return None

    client.get_current_playback = idle
    client.update_playback_data()
    assert client.spotify_data.track_name == "Nothing playing"
    assert client._last_playback_state == "idle"


def test_update_playback_data_malformed_and_error(client):
    client.spotify_data.track_name = "Old"
    client.get_current_playback = lambda: {"item": {"name": "x", "artists": []}}
    client.update_playback_data()
    assert client.spotify_data.track_name == "Nothing playing"

    client.spotify_data.track_name = "Old"

    def boom():
        raise RuntimeError("x")

    client.get_current_playback = boom
    client.update_playback_data()
    assert client.spotify_data.track_name == "Nothing playing"


# --------------------------------------------------------------------------- monitoring loop


def test_start_monitoring_polls_and_marks_connected(client, clock, monkeypatch):
    _authed(client, clock)
    sent = []
    loops = {"n": 0}

    def update():
        client._last_api_success = True

    def ws():
        sent.append(client.is_authenticated)

    def sleep(seconds):
        loops["n"] += 1
        assert seconds == pytest.approx(5.0)
        client.running = False

    client.update_playback_data = update
    client.send_websocket_data = ws
    client._interruptible_sleep = sleep
    client.start_monitoring()
    assert loops["n"] == 1 and sent == [True]
    assert client.spotify_data.connection_status == "Connected"


def test_start_monitoring_skips_poll_and_handles_no_tokens(client, clock):
    _authed(client, clock)
    client.spotify_data.connection_status = "Not Configured"
    polled, sent = [], []
    client.update_playback_data = lambda: polled.append(1)
    client.send_websocket_data = lambda: sent.append(1)
    client._interruptible_sleep = lambda s: setattr(client, "running", False)
    client.start_monitoring()
    assert polled == [] and sent == [1]

    _configure(client, access_token="", refresh_token="")
    client.spotify_data.track_name = "Old"
    client.start_monitoring()
    assert client.spotify_data.track_name == "Nothing playing"
    assert sent == [1]


def test_start_monitoring_already_authenticated_and_failed_poll(client, clock):
    _authed(client, clock)
    client.is_authenticated = True
    client.spotify_data.connection_status = "Something"
    client.update_playback_data = lambda: setattr(client, "_last_api_success", True)
    client.send_websocket_data = lambda: None
    client._interruptible_sleep = lambda s: setattr(client, "running", False)
    client.start_monitoring()
    assert client.spotify_data.connection_status == "Something"  # not rewritten

    client.is_authenticated = False
    client.spotify_data.connection_status = "Connected"
    client.update_playback_data = lambda: setattr(client, "_last_api_success", False)
    client.start_monitoring()
    assert client.is_authenticated is False


def test_start_monitoring_slow_iteration_and_errors(client, clock):
    _authed(client, clock)
    client.is_authenticated = True
    sleeps = []

    def slow_update():
        clock.now += 100  # longer than the interval
        client._last_api_success = True

    state = {"n": 0}

    def ws():
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("socket gone")
        if state["n"] == 3:
            client.running = False

    def sleep(s):
        sleeps.append(s)

    client.update_playback_data = slow_update
    client.send_websocket_data = ws
    client._interruptible_sleep = sleep
    client.start_monitoring()
    assert sleeps == [5]  # first loop: no sleep (slow); second: error -> sleep 5; third: stop (slow)


def test_send_websocket_data(client, monkeypatch):
    from modules import web_engine

    emitted = []
    inst = SimpleNamespace(safe_emit=lambda ev, payload: emitted.append((ev, payload)))
    monkeypatch.setattr(web_engine, "web_engine_instance", inst, raising=False)
    client.spotify_data.access_token = "SECRET"
    client.spotify_data.track_name = "Song"
    client.send_websocket_data()
    ev, payload = emitted[0]
    assert ev == "spotify_data_update" and payload["track_name"] == "Song"
    assert "SECRET" not in repr(payload)

    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    client.send_websocket_data()
    assert len(emitted) == 1

    def boom(ev, payload):
        raise RuntimeError("emit failed")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=boom), raising=False)
    client.send_websocket_data()  # swallowed


def test_stop_and_disconnect(client):
    client.running = True
    client.is_authenticated = True
    client.disconnect()
    assert client.running is False and client.is_authenticated is False
    assert client.spotify_data.connection_status == "Disconnected"


# --------------------------------------------------------------------------- OAuth flow


def test_start_oauth_flow(client):
    assert client.start_oauth_flow() == ""
    _configure(client, client_id="cid", client_secret="sec")
    url = client.start_oauth_flow()
    assert url.startswith("https://accounts.spotify.com/authorize?client_id=cid&")
    assert "redirect_uri=http://127.0.0.1:9973" in url and "show_dialog=true" in url
    assert client.spotify_data.connection_status == "Awaiting Authorization"


def test_start_oauth_flow_exception(client, monkeypatch):
    _configure(client, client_id="cid", client_secret="sec")

    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(client, "update_field", boom)
    assert client.start_oauth_flow() == ""


class _FakeOAuthServer:
    instances = []

    def __init__(self, start_ok=True, result=None):
        self.start_ok = start_ok
        self.result = result or {"success": True, "code": "the-code"}
        self.stopped = 0
        _FakeOAuthServer.instances.append(self)

    def start(self):
        return self.start_ok

    def stop(self):
        self.stopped += 1

    def wait_for_callback(self, timeout=300):
        return self.result


def _patch_server(monkeypatch, **kw):
    _FakeOAuthServer.instances = []
    monkeypatch.setattr(sp, "OAuthCallbackServer", lambda: _FakeOAuthServer(**kw))


def test_oauth_with_server_success(client, monkeypatch, _no_browser):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch)
    codes = []
    client.complete_oauth_flow = lambda code: codes.append(code) or True
    assert client.start_oauth_flow_with_server() is True
    assert codes == ["the-code"]
    assert len(_no_browser) == 1 and _no_browser[0].startswith("https://accounts.spotify.com/authorize")
    assert _FakeOAuthServer.instances[0].stopped == 1


def test_oauth_with_server_completion_fails(client, monkeypatch):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch)
    client.complete_oauth_flow = lambda code: False
    assert client.start_oauth_flow_with_server() is False


def test_oauth_with_server_not_configured(client, monkeypatch, _no_browser):
    _patch_server(monkeypatch)
    assert client.start_oauth_flow_with_server() is False
    assert _FakeOAuthServer.instances == [] and _no_browser == []


def test_oauth_with_server_start_fails(client, monkeypatch):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch, start_ok=False)
    assert client.start_oauth_flow_with_server() is False
    assert client.spotify_data.connection_status == "Server Error"


def test_oauth_with_server_no_url(client, monkeypatch, _no_browser):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch)
    client.start_oauth_flow = lambda: ""
    assert client.start_oauth_flow_with_server() is False
    assert _FakeOAuthServer.instances[0].stopped == 1 and _no_browser == []


@pytest.mark.parametrize("error, status", [
    ("timeout", "Authorization Timeout"), ("access_denied", "Authorization Error: access_denied"),
])
def test_oauth_with_server_callback_errors(client, monkeypatch, error, status):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch, result={"success": False, "error": error})
    assert client.start_oauth_flow_with_server() is False
    assert client.spotify_data.connection_status == status


def test_oauth_with_server_unknown_error(client, monkeypatch):
    _configure(client, client_id="cid", client_secret="sec")
    _patch_server(monkeypatch, result={"success": False})
    assert client.start_oauth_flow_with_server() is False
    assert client.spotify_data.connection_status == "Authorization Error: Unknown error"


def test_oauth_with_server_exception(client, monkeypatch):
    _configure(client, client_id="cid", client_secret="sec")

    def boom():
        raise RuntimeError("no server class")

    monkeypatch.setattr(sp, "OAuthCallbackServer", boom)
    assert client.start_oauth_flow_with_server() is False
    assert client.spotify_data.connection_status == "OAuth Error"


def test_complete_oauth_flow_success(client, state, clock, http_fake):
    _configure(client, client_id="cid", client_secret="sec")
    client._auth_failure_until = 99
    f = http_fake(Resp(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 100}))
    assert client.complete_oauth_flow("CODE") is True
    _, url, kw = f.calls[0]
    assert url == "https://accounts.spotify.com/api/token"
    assert kw["data"]["code"] == "CODE" and kw["data"]["redirect_uri"] == sp.SPOTIFY_OAUTH_REDIRECT_URI
    assert kw["timeout"] == 10
    d = client.spotify_data
    assert (d.access_token, d.refresh_token, d.token_expiry) == ("at", "rt", clock.now + 70)
    assert client.is_authenticated and d.connection_status == "Connected"
    assert client._auth_failure_until == 0.0 and state.saves == 1


def test_complete_oauth_flow_default_expiry(client, clock, http_fake):
    http_fake(Resp(200, {"access_token": "at", "refresh_token": "rt"}))
    assert client.complete_oauth_flow("C") is True
    assert client.spotify_data.token_expiry == clock.now + 3570


def test_complete_oauth_flow_rejections(client, clock, http_fake, monkeypatch):
    from modules import notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda *a, **k: None)
    http_fake(Resp(400, {"error": "invalid_grant"}, text="bad code"))
    assert client.complete_oauth_flow("C") is False
    assert client._last_auth_error_code == "invalid_grant"
    assert client.spotify_data.connection_status == "OAuth Failed"

    client._clear_auth_failure_state()
    http_fake(Resp(500, {"error": "invalid_grant"}))
    assert client.complete_oauth_flow("C") is False
    assert client._last_auth_error_code == ""


def test_complete_oauth_flow_exception(client, http_fake):
    http_fake(OSError("offline"))
    assert client.complete_oauth_flow("C") is False
    assert client.spotify_data.connection_status == "OAuth Error"

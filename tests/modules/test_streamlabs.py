# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/streamlabs.py (OAuth, Engine.IO 3 socket, donations poll, dedupe).

HTTP (``requests``), the websocket client, the OAuth callback server and the browser are
all faked; nothing binds port 9975 or reaches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import io  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import webbrowser  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Callable, List  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402

from modules import api_credentials_manager, donation_alerts  # noqa: E402
from modules import streamlabs as sl  # noqa: E402


# --------------------------------------------------------------------------- fakes


class Env:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            streamlabs_access_token="",
            streamlabs_refresh_token="",
            streamlabs_connection_status="",
            streamlabs_last_error="",
        )
        self.creds = {"client_id": "", "client_secret": ""}
        self.statuses: List[tuple] = []
        self.updates: List[dict] = []
        self.cleared: List[str] = []
        self.alerts: List[dict] = []

    def get_settings(self):
        return self.settings

    def set_status(self, service, status, error=None):
        self.statuses.append((service, status, error))
        self.settings.streamlabs_connection_status = status
        if error is not None:
            self.settings.streamlabs_last_error = error

    def update_settings(self, **kw):
        self.updates.append(kw)
        for k, v in kw.items():
            setattr(self.settings, k, v)

    def clear_secret(self, name):
        self.cleared.append(name)


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(sl, "get_settings", e.get_settings)
    monkeypatch.setattr(sl, "set_status", e.set_status)
    monkeypatch.setattr(sl, "update_settings", e.update_settings)
    monkeypatch.setattr(sl, "clear_secret", e.clear_secret)
    monkeypatch.setattr(api_credentials_manager, "get_streamlabs_credentials", lambda: dict(e.creds))
    monkeypatch.setattr(donation_alerts, "emit_donation_alert", lambda **kw: e.alerts.append(kw))
    monkeypatch.setattr(sl, "_client", None)
    return e


class FakeEvent:
    def __init__(self, waits=None) -> None:
        self.flag = False
        self.waits = list(waits or [])
        self.wait_args: List[float] = []

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, t=None):
        self.wait_args.append(t)
        return self.waits.pop(0) if self.waits else True


class FakeApp:
    script: List[Callable[["FakeApp"], None]] = []
    instances: List["FakeApp"] = []

    def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.sent: List[str] = []
        self.closed = 0
        self.fail_send = False
        self.fail_close = False
        self.run_kwargs = None
        FakeApp.instances.append(self)

    def send(self, payload):
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append(payload)

    def close(self):
        self.closed += 1
        if self.fail_close:
            raise OSError("close failed")

    def run_forever(self, **kw):
        self.run_kwargs = kw
        for step in list(FakeApp.script):
            step(self)

    def msg(self, raw):
        self.on_message(self, raw)


@pytest.fixture
def fake_ws(monkeypatch):
    FakeApp.script = []
    FakeApp.instances = []
    monkeypatch.setattr(sl.websocket, "WebSocketApp", FakeApp)
    return FakeApp


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._payload


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "event,expected",
    [
        (None, False),
        ({"type": "bits"}, False),
        ({"type": "donation"}, True),
        ({"type": "donation", "for": ""}, True),
        ({"type": "donation", "for": "streamlabs"}, True),
        ({"type": "donation", "for": "twitch_account"}, False),
    ],
)
def test_is_streamlabs_donation_event(event, expected):
    assert sl.is_streamlabs_donation_event(event) is expected


@pytest.mark.parametrize(
    "packet,expected",
    [
        (None, None),
        ("2", None),
        ("42{bad", None),
        ('42["event"]', None),
        ('42["other", {}]', None),
        ('42["event", "str"]', None),
        ('42["event", {"type": "donation"}]', {"type": "donation"}),
    ],
)
def test_parse_engineio_event(packet, expected):
    assert sl.parse_engineio_event(packet) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(True, 0.0), (None, 0.0), (3, 3.0), (2.5, 2.5), ("1,234.50", 1234.5), ("  ", 0.0), ("abc", 0.0)],
)
def test_as_amount(value, expected):
    assert sl._as_amount(value) == expected


def test_numeric_id():
    assert sl._numeric_id("123") == 123
    assert sl._numeric_id(" 7 ") == 7
    assert sl._numeric_id("abc") == 0
    assert sl._numeric_id(None) == 0
    assert sl._numeric_id("-5") == 0


def test_numeric_id_unicode_digit_valueerror():
    # "²".isdigit() is True but int("²") raises ValueError.
    assert sl._numeric_id("²") == 0


def test_donation_fingerprint():
    fp = sl.donation_fingerprint(" Ada ", 3.456, "", "m" * 200)
    assert fp == ("ada", 3.46, "USD", "m" * 120)
    assert sl.donation_fingerprint(None, 1, "eur", None) == ("", 1.0, "EUR", "")


def test_normalize_streamlabs_donation_full():
    d = sl.normalize_streamlabs_donation(
        {"name": " Ada ", "message": "hi", "currency": "eur", "amount": "5",
         "donation_id": "99", "id": "x", "_id": "  ", "created_at": "1700"}
    )
    assert d["username"] == "Ada"
    assert d["currency"] == "EUR"
    assert d["amount"] == 5.0
    assert d["ids"] == ["99", "x"]
    assert d["event_id"] == "99"
    assert d["numeric_id"] == 99
    assert d["created_at"] == 1700
    assert d["fingerprint"] == ("ada", 5.0, "EUR", "hi")


def test_normalize_streamlabs_donation_defaults():
    d = sl.normalize_streamlabs_donation({"from": "", "currency": " ", "id": "12"})
    assert d["username"] == "Anonymous"
    assert d["currency"] == "USD"
    assert d["message"] == ""
    assert d["ids"] == ["12"] and d["numeric_id"] == 12
    bare = sl.normalize_streamlabs_donation({"from": "Fr"})
    assert bare["username"] == "Fr" and bare["ids"] == [] and bare["event_id"] == "" and bare["numeric_id"] == 0


def test_iter_streamlabs_donations():
    assert sl.iter_streamlabs_donations({"type": "follow"}) == []
    assert sl.iter_streamlabs_donations({"type": "donation", "message": "nope"}) == []
    single = sl.iter_streamlabs_donations({"type": "donation", "message": {"name": "A", "id": "1"}})
    assert [d["event_id"] for d in single] == ["1"]
    multi = sl.iter_streamlabs_donations(
        {"type": "donation", "event_id": "ev", "message": [{"name": "A"}, "skip", {"name": "B", "_id": "b"}]}
    )
    assert [d["event_id"] for d in multi] == ["ev:0", "b"]
    assert multi[0]["ids"] == ["ev:0"]
    no_event = sl.iter_streamlabs_donations({"type": "donation", "message": [{"name": "A"}]})
    assert no_event[0]["ids"] == []
    assert sl.iter_streamlabs_donations({"type": "donation"}) == []


# --------------------------------------------------------------------------- deduper


def _don(ids=(), fp=("a", 1.0, "USD", ""), numeric=0, created=0):
    return {"ids": list(ids), "fingerprint": fp, "numeric_id": numeric, "created_at": created}


def test_deduper_live_ids_and_fingerprint_window():
    d = sl.DonationDeduper(window_sec=10)
    assert d.accept_live(_don(ids=["1"]), now=0) is True
    assert d.accept_live(_don(ids=["1"], fp=("b", 2.0, "USD", "")), now=1) is False
    # same fingerprint, new id, inside window
    assert d.accept_live(_don(ids=["2"]), now=5) is False
    # outside the window
    assert d.accept_live(_don(ids=["3"]), now=20) is True
    # default "now" uses monotonic
    assert d.accept_live(_don(ids=["4"], fp=("z", 9.0, "USD", ""))) is True


def test_deduper_remember_skips_empty_and_tracks_high_water():
    d = sl.DonationDeduper()
    d._remember({"ids": ["", "x"], "fingerprint": None, "numeric_id": 5}, 0)
    assert d._ids == {"x"} and d._fingerprints == {} and d._high_water == 5
    d._remember({"ids": None, "numeric_id": 3}, 0)
    assert d._high_water == 5


def test_deduper_poll_batch_primes_then_filters(monkeypatch):
    monkeypatch.setattr(sl.time, "time", lambda: 1000.0)
    d = sl.DonationDeduper(window_sec=10)
    first = [_don(ids=["10"], fp=("p", 1.0, "USD", ""), numeric=10)]
    assert d.accept_poll_batch(first, now=0) == []
    assert d._primed and d._primed_at == 1000.0 and d._high_water == 10
    batch = [
        _don(ids=["10"], fp=("p", 1.0, "USD", ""), numeric=10),  # seen id
        _don(ids=["9"], fp=("old", 1.0, "USD", ""), numeric=9),  # below high water
        _don(ids=["s"], fp=("stale", 1.0, "USD", ""), created=900),  # created before priming
        _don(ids=["n"], fp=("new", 1.0, "USD", ""), numeric=11, created=999),
        _don(ids=["m"], fp=("new2", 1.0, "USD", "")),
    ]
    fresh = d.accept_poll_batch(batch, now=100)
    assert [x["ids"][0] for x in fresh] == ["n", "m"]
    assert {"9", "s"} <= d._ids
    assert d.accept_poll_batch([_don(ids=["q"], fp=("q", 1.0, "USD", ""))]) != []


# --------------------------------------------------------------------------- OAuth handler


class _HandlerHarness(sl._OAuthHandler):
    def __init__(self, path):
        self.path = path
        self.server = SimpleNamespace(auth_code=None, auth_state="", auth_error=None)
        self.wfile = io.BytesIO()
        self.sent = []
        self.headers_out = []

    def send_response(self, code, message=None):
        self.sent.append(code)

    def send_header(self, k, v):
        self.headers_out.append((k, v))

    def end_headers(self):
        self.headers_out.append(("END", ""))


def test_oauth_handler_success():
    h = _HandlerHarness("/?code=abc&state=st")
    h.do_GET()
    assert h.server.auth_code == "abc" and h.server.auth_state == "st"
    assert h.sent == [200]
    body = h.wfile.getvalue()
    assert b"Streamlabs is connected" in body
    assert ("Content-Length", str(len(body))) in h.headers_out
    assert ("Content-Type", "text/html; charset=utf-8") in h.headers_out


def test_oauth_handler_code_without_state():
    h = _HandlerHarness("/?code=abc")
    h.do_GET()
    assert h.server.auth_state == ""


def test_oauth_handler_error_and_default_error():
    h = _HandlerHarness("/?error=access_denied")
    h.do_GET()
    assert h.server.auth_error == "access_denied" and h.sent == [400]
    assert b"not completed" in h.wfile.getvalue()
    h2 = _HandlerHarness("/favicon.ico")
    h2.do_GET()
    assert h2.server.auth_error == "authorization_failed"


def test_oauth_handler_log_message_is_silent():
    h = _HandlerHarness("/")
    assert h.log_message("%s", "x") is None


def test_callback_server_binds_loopback(monkeypatch):
    seen = {}

    def fake_init(self, address, handler, bind_and_activate=True):
        seen["address"] = address
        seen["handler"] = handler

    monkeypatch.setattr(sl.TCPServer, "__init__", fake_init)
    server = sl._CallbackServer()
    assert seen == {"address": ("127.0.0.1", 9975), "handler": sl._OAuthHandler}
    assert server.auth_code is None and server.auth_state == "" and server.auth_error is None
    assert sl._CallbackServer.allow_reuse_address is True


# --------------------------------------------------------------------------- client basics


def test_status_and_last_error(env):
    c = sl.StreamlabsClient()
    assert c.status() == "Disconnected" and c.last_error() == ""
    env.settings.streamlabs_connection_status = "Connected"
    env.settings.streamlabs_last_error = "e"
    assert c.status() == "Connected" and c.last_error() == "e"


def test_start_without_token(env):
    c = sl.StreamlabsClient()
    c.start()
    assert env.statuses == [("streamlabs", "Disconnected", None)]
    assert c._thread is None


def test_start_launches_socket_and_poll_threads(env):
    env.settings.streamlabs_access_token = "acc"
    c = sl.StreamlabsClient()
    release = threading.Event()
    ran = []
    c._socket_loop = lambda: (ran.append("socket"), release.wait(2))
    c._poll_loop = lambda: (ran.append("poll"), release.wait(2))
    old = c._deduper
    try:
        c.start()
        assert c._thread.name == "StreamlabsSocket" and c._poll_thread.name == "StreamlabsPoll"
        assert c._deduper is not old
        first = c._thread
        c.start()  # alive: no-op
        assert c._thread is first
    finally:
        release.set()
        c._thread.join(2)
        c._poll_thread.join(2)
    assert sorted(ran) == ["poll", "socket"]


def test_stop_closes_and_joins(env):
    c = sl.StreamlabsClient()
    ws = FakeApp("u")
    ws.fail_close = True
    c._ws = ws
    release = threading.Event()
    t = threading.Thread(target=release.wait, args=(2,), daemon=True)
    t.start()
    c._thread = t
    threading.Timer(0.02, release.set).start()
    c.stop()
    assert not t.is_alive()
    assert c._stop.is_set() and ws.closed == 1
    assert env.statuses[-1] == ("streamlabs", "Disconnected", None)


def test_stop_from_worker_thread_does_not_self_join(env):
    c = sl.StreamlabsClient()
    out = []

    def inner():
        c._poll_thread = threading.current_thread()
        c.stop()
        out.append(1)

    t = threading.Thread(target=inner, daemon=True)
    t.start()
    t.join(2)
    assert out == [1]


def test_disconnect_clears_tokens(env):
    c = sl.StreamlabsClient()
    c.disconnect()
    assert env.cleared == ["streamlabs_access_token", "streamlabs_refresh_token"]
    assert env.statuses[-1] == ("streamlabs", "Disconnected", "")


def test_begin_oauth_is_exclusive(env):
    c = sl.StreamlabsClient()
    c._oauth = lambda cid, sec: f"ran {cid} {sec}"
    assert c.begin_oauth("id", "sec") == "ran id sec"
    assert c._oauth_lock.acquire(blocking=False)
    try:
        assert c.begin_oauth("id", "sec") == "Authorization is already in progress."
    finally:
        c._oauth_lock.release()


# --------------------------------------------------------------------------- OAuth flow


class FakeServer:
    """Replaces _CallbackServer; ``result`` is applied when serve_forever runs."""

    result: dict = {}
    instances: List["FakeServer"] = []
    raise_on_init: Any = None

    def __init__(self):
        if FakeServer.raise_on_init:
            raise FakeServer.raise_on_init
        self.auth_code = None
        self.auth_state = ""
        self.auth_error = None
        self.shut = False
        self.closed = False
        FakeServer.instances.append(self)

    def serve_forever(self):
        pass

    def shutdown(self):
        self.shut = True

    def server_close(self):
        self.closed = True


@pytest.fixture
def oauth(env, monkeypatch):
    FakeServer.result = {}
    FakeServer.instances = []
    FakeServer.raise_on_init = None
    monkeypatch.setattr(sl, "_CallbackServer", FakeServer)
    monkeypatch.setattr(sl.secrets, "token_urlsafe", lambda n: "STATE")
    opened = []

    def fake_open(url):
        opened.append(url)
        srv = FakeServer.instances[-1]
        for k, v in FakeServer.result.items():
            setattr(srv, k, v)

    monkeypatch.setattr(webbrowser, "open", fake_open)
    sleeps = []
    monkeypatch.setattr(sl.time, "sleep", lambda s: sleeps.append(s))
    return SimpleNamespace(opened=opened, sleeps=sleeps)


def test_oauth_port_in_use(env, oauth):
    FakeServer.raise_on_init = OSError("in use")
    c = sl.StreamlabsClient()
    assert c._oauth("id", "sec") == "OAuth callback port 9975 is in use."
    assert env.statuses[-1] == ("streamlabs", "Error", "OAuth callback port 9975 is in use.")
    assert oauth.opened == []


def test_oauth_success_exchanges_code(env, oauth):
    FakeServer.result = {"auth_code": "CODE", "auth_state": "STATE"}
    c = sl.StreamlabsClient()
    seen = []
    c._exchange_code = lambda cid, sec, code: seen.append((cid, sec, code)) or "access"
    assert c._oauth("cid", "sec") == ""
    assert seen == [("cid", "sec", "CODE")]
    url = oauth.opened[0]
    assert url.startswith(sl.AUTHORIZE_URL + "?")
    assert "client_id=cid" in url and "state=STATE" in url and "response_type=code" in url
    assert "scope=donations.read+socket.token" in url
    srv = FakeServer.instances[0]
    assert srv.shut and srv.closed
    assert ("streamlabs", "Connecting", None) in env.statuses
    assert oauth.sleeps == []


def test_oauth_error_from_callback(env, oauth):
    FakeServer.result = {"auth_error": "access_denied"}
    c = sl.StreamlabsClient()
    assert c._oauth("cid", "sec") == "access_denied"
    assert env.statuses[-1] == ("streamlabs", "Authentication failed", "access_denied")


def test_oauth_state_mismatch(env, oauth):
    FakeServer.result = {"auth_code": "CODE", "auth_state": "OTHER"}
    c = sl.StreamlabsClient()
    assert c._oauth("cid", "sec") == "Authorization was not completed."
    assert env.statuses[-1][1] == "Authentication failed"


def test_oauth_times_out(env, oauth, monkeypatch):
    clock = [0.0, 0.0, 100.0]
    monkeypatch.setattr(sl.time, "time", lambda: clock.pop(0) if clock else 200.0)
    c = sl.StreamlabsClient()
    assert c._oauth("cid", "sec") == "Authorization was not completed."
    assert oauth.sleeps == [0.2, 0.2]
    assert FakeServer.instances[0].shut and FakeServer.instances[0].closed


def test_oauth_exchange_failure_messages(env, oauth):
    FakeServer.result = {"auth_code": "CODE", "auth_state": "STATE"}
    c = sl.StreamlabsClient()
    c._exchange_code = lambda *a: ""
    env.settings.streamlabs_last_error = "Streamlabs rejected"
    assert c._oauth("cid", "sec") == "Streamlabs rejected"
    env.settings.streamlabs_last_error = ""
    assert c._oauth("cid", "sec") == "Token exchange failed."


def test_exchange_code_success_restarts(env, monkeypatch):
    posted = {}

    def fake_post(url, data=None, timeout=None):
        posted.update(url=url, data=data, timeout=timeout)
        return FakeResponse(200, {"access_token": "A", "refresh_token": "R"})

    monkeypatch.setattr(sl.requests, "post", fake_post)
    c = sl.StreamlabsClient()
    calls = []
    c.stop = lambda: calls.append("stop")
    c.start = lambda: calls.append("start")
    assert c._exchange_code("cid", "sec", "code") == "A"
    assert posted["url"] == sl.TOKEN_URL and posted["timeout"] == 30
    assert posted["data"]["grant_type"] == "authorization_code" and posted["data"]["code"] == "code"
    assert posted["data"]["redirect_uri"] == sl.STREAMLABS_OAUTH_REDIRECT_URI
    assert env.updates == [{"streamlabs_access_token": "A", "streamlabs_refresh_token": "R"}]
    assert calls == ["stop", "start"]


def test_exchange_code_failures(env, monkeypatch):
    c = sl.StreamlabsClient()

    def boom(*a, **k):
        raise requests.ConnectionError("x")

    monkeypatch.setattr(sl.requests, "post", boom)
    assert c._exchange_code("a", "b", "c") == ""
    assert env.statuses[-1] == ("streamlabs", "Error", "Could not reach Streamlabs.")
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: FakeResponse(400))
    assert c._exchange_code("a", "b", "c") == ""
    assert env.statuses[-1][2] == "Streamlabs rejected the authorization code."
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: FakeResponse(200, {}))
    assert c._exchange_code("a", "b", "c") == ""
    assert env.statuses[-1][2] == "Streamlabs did not return a token."
    assert env.updates == []


# --------------------------------------------------------------------------- token refresh + API


def test_refresh_access_token_missing_inputs(env, monkeypatch):
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: pytest.fail("no post"))
    c = sl.StreamlabsClient()
    assert c._refresh_access_token() == ""
    assert env.statuses[-1] == ("streamlabs", "Token expired", "Reconnect Streamlabs.")
    env.settings.streamlabs_refresh_token = "r"
    env.creds = {"client_id": "id", "client_secret": ""}
    assert c._refresh_access_token() == ""


def test_refresh_access_token_paths(env, monkeypatch):
    env.settings.streamlabs_refresh_token = "old"
    env.creds = {"client_id": "id", "client_secret": "sec"}
    c = sl.StreamlabsClient()

    def boom(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(sl.requests, "post", boom)
    assert c._refresh_access_token() == ""
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: FakeResponse(401))
    assert c._refresh_access_token() == ""
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: FakeResponse(200, {"access_token": ""}))
    assert c._refresh_access_token() == ""
    assert all(s[1] == "Token expired" for s in env.statuses)
    posted = {}

    def ok(url, data=None, timeout=None):
        posted.update(data)
        return FakeResponse(200, {"access_token": "new"})

    monkeypatch.setattr(sl.requests, "post", ok)
    assert c._refresh_access_token() == "new"
    assert posted["grant_type"] == "refresh_token" and posted["refresh_token"] == "old"
    assert env.updates[-1] == {"streamlabs_access_token": "new", "streamlabs_refresh_token": "old"}
    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: FakeResponse(200, {"access_token": "n2", "refresh_token": "r2"}))
    assert c._refresh_access_token() == "n2"
    assert env.updates[-1] == {"streamlabs_access_token": "n2", "streamlabs_refresh_token": "r2"}


def test_authorized_get(env, monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.update(url=url, headers=headers, params=params, timeout=timeout)
        return FakeResponse(200, {})

    monkeypatch.setattr(sl.requests, "get", fake_get)
    c = sl.StreamlabsClient()
    assert c._authorized_get("u", "tok", {"a": 1}).status_code == 200
    assert seen["headers"]["Authorization"] == "Bearer tok" and seen["params"] == {"a": 1} and seen["timeout"] == 20

    def boom(*a, **k):
        raise requests.ConnectionError("x")

    monkeypatch.setattr(sl.requests, "get", boom)
    assert c._authorized_get("u", "tok", None) is None


def test_api_get_paths(env):
    c = sl.StreamlabsClient()
    assert c._api_get("u") is None  # no token
    env.settings.streamlabs_access_token = "tok"
    responses = []
    c._authorized_get = lambda url, access, params: responses.pop(0)

    responses[:] = [None]
    assert c._api_get("u") is None
    responses[:] = [FakeResponse(500)]
    assert c._api_get("u") is None
    responses[:] = [FakeResponse(200, bad_json=True)]
    assert c._api_get("u") is None
    responses[:] = [FakeResponse(200, ["list"])]
    assert c._api_get("u") is None
    responses[:] = [FakeResponse(200, {"ok": 1})]
    assert c._api_get("u") == {"ok": 1}


def test_api_get_refreshes_on_401(env):
    env.settings.streamlabs_access_token = "tok"
    c = sl.StreamlabsClient()
    used = []
    responses = [FakeResponse(401), FakeResponse(200, {"ok": 2})]

    def fake_get(url, access, params):
        used.append(access)
        return responses.pop(0)

    c._authorized_get = fake_get
    c._refresh_access_token = lambda: "new"
    assert c._api_get("u", {"p": 1}) == {"ok": 2}
    assert used == ["tok", "new"]
    c._refresh_access_token = lambda: ""
    responses[:] = [FakeResponse(401)]
    assert c._api_get("u") is None


def test_socket_token(env):
    c = sl.StreamlabsClient()
    c._api_get = lambda url, params=None: None
    assert c._socket_token() == ""
    c._api_get = lambda url, params=None: {"socket_token": "st"}
    assert c._socket_token() == "st"
    c._api_get = lambda url, params=None: {"x": 1}
    assert c._socket_token() == ""


# --------------------------------------------------------------------------- socket loop


def _client(waits=None):
    c = sl.StreamlabsClient()
    c._stop = FakeEvent(waits)
    return c


def test_socket_loop_no_token_backoff(env):
    c = _client(waits=[False] * 6 + [True])
    c._socket_token = lambda: ""
    c._socket_loop()
    assert c._stop.wait_args == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert ("streamlabs", "Error", "Could not get a Streamlabs socket token.") in env.statuses


def test_socket_loop_no_token_keeps_auth_status(env):
    c = _client()

    def tok():
        env.settings.streamlabs_connection_status = "Token expired"
        return ""

    c._socket_token = tok
    c._socket_loop()
    assert not any(s[1] == "Error" for s in env.statuses)


def test_socket_loop_stops_after_token(env):
    c = _client()

    def tok():
        c._stop.flag = True
        return "t"

    c._socket_token = tok
    c._connect_socket = lambda t: pytest.fail("no connect")
    c._socket_loop()
    assert c._stop.wait_args == []


def test_socket_loop_connects_and_reconnects(env):
    c = _client(waits=[False, True])
    c._socket_token = lambda: "t"
    seen = []
    c._connect_socket = lambda t: seen.append(t)
    c._socket_loop()
    assert seen == ["t", "t"]
    assert c._stop.wait_args == [2.0, 2.0]  # delay resets to 2 after each good token
    assert ("streamlabs", "Connecting", "Reconnecting to Streamlabs.") in env.statuses


def test_socket_loop_returns_when_stopped_during_connect(env):
    c = _client()
    c._socket_token = lambda: "t"
    c._connect_socket = lambda t: setattr(c._stop, "flag", True)
    c._socket_loop()
    assert c._stop.wait_args == []


def _patch_thread(monkeypatch, thread_cls):
    """Swap only streamlabs' view of threading.Thread (not the global module)."""
    ns = SimpleNamespace(
        Thread=thread_cls,
        Event=threading.Event,
        Lock=threading.Lock,
        current_thread=threading.current_thread,
    )
    monkeypatch.setattr(sl, "threading", ns)


class _InstantThread:
    """Runs nothing; records that a ping worker was started."""

    started: List["_InstantThread"] = []

    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        _InstantThread.started.append(self)


def test_connect_socket_engineio_flow(env, fake_ws, monkeypatch):
    _InstantThread.started = []
    _patch_thread(monkeypatch, _InstantThread)
    c = sl.StreamlabsClient()
    donation_event = {"type": "donation", "message": [{"name": "Ada", "amount": "5", "id": "1", "currency": "usd"}]}
    fake_ws.script = [
        lambda app: app.on_open(app),
        lambda app: app.on_message(app, None),
        lambda app: app.msg(""),
        lambda app: app.msg('0{"pingInterval": 1000}'),  # clamps to 5 s
        lambda app: app.msg("0{bad"),  # second open: interval falls back, no new ping thread
        lambda app: app.msg("2"),
        lambda app: app.msg('42["other", {}]'),
        lambda app: app.msg("42" + json.dumps(["event", donation_event])),
        lambda app: app.msg("42" + json.dumps(["event", donation_event])),  # dedupe
        lambda app: app.msg("3"),
        lambda app: app.on_error(app, "oops"),
        lambda app: app.on_close(app, 1000, ""),
        lambda app: app.on_close(app, None, ""),
    ]
    c._connect_socket("to/k")
    (app,) = fake_ws.instances
    assert app.url == sl.SOCKET_URL + "?EIO=3&transport=websocket&token=to%2Fk"
    assert app.run_kwargs == {"ping_interval": 0, "ping_timeout": None}
    assert app.sent == ["40", "40", "3"]
    assert len(_InstantThread.started) == 1
    assert _InstantThread.started[0].args == (5.0,)
    assert env.statuses.count(("streamlabs", "Connected", "")) == 2
    assert env.alerts == [
        {"username": "Ada", "amount": 5.0, "currency": "USD", "message": "", "source": "streamlabs", "event_id": "1"}
    ]
    assert c._ws is None


def test_connect_socket_ping_loop_sends_and_stops(env, fake_ws, monkeypatch):
    c = sl.StreamlabsClient()
    holder = {}
    real_thread = threading.Thread

    class CapturingThread(real_thread):
        def __init__(self, target=None, args=(), daemon=None, **kw):
            holder["target"], holder["args"] = target, args
            super().__init__(target=lambda: None, daemon=True)

    _patch_thread(monkeypatch, CapturingThread)

    def opened(app):
        app.on_open(app)
        app.msg('0{"pingInterval": 25000}')

    fake_ws.script = [opened]
    c._connect_socket("t")
    assert holder["args"] == (25.0,)
    # After run_forever the ping stop event is set: the loop exits without sending.
    holder["target"](0.001)


def test_connect_socket_ping_loop_sends_until_closed(env, fake_ws, monkeypatch):
    c = sl.StreamlabsClient()
    holder = {}

    class CapturingThread:
        def __init__(self, target=None, args=(), daemon=None, **kw):
            holder["target"] = target

        def start(self):
            pass

    _patch_thread(monkeypatch, CapturingThread)

    def opened(app):
        app.on_open(app)
        app.msg("0{}")
        # Drive the ping loop: it sends "2" each interval until on_close sets the stop.
        sends_before = list(app.sent)
        t = threading.Thread(target=holder["target"], args=(0.005,), daemon=True)
        t.start()
        for _ in range(200):
            if app.sent.count("2") >= 2:
                break
            threading.Event().wait(0.005)
        app.on_close(app, None, None)
        t.join(1)
        assert not t.is_alive()
        assert app.sent.count("2") >= 2 and sends_before == ["40"]

    fake_ws.script = [opened]
    c._connect_socket("t")


def test_connect_socket_send_failures_and_missing_ws(env, fake_ws, monkeypatch):
    _patch_thread(monkeypatch, _InstantThread)
    c = sl.StreamlabsClient()

    def failing(app):
        app.fail_send = True
        app.msg("2")
        c._ws = None
        app.msg("2")

    fake_ws.script = [failing]
    c._connect_socket("t")
    assert fake_ws.instances[0].sent == []


def test_handle_socket_event_and_emit(env):
    c = sl.StreamlabsClient()
    c._handle_socket_event({"type": "bits", "message": [{"name": "x"}]})
    assert env.alerts == []
    c._handle_socket_event({"type": "donation", "message": [{"name": "A", "id": "1", "amount": 1}]})
    c._handle_socket_event({"type": "donation", "message": [{"name": "A", "id": "1", "amount": 1}]})
    assert len(env.alerts) == 1 and env.alerts[0]["source"] == "streamlabs"


# --------------------------------------------------------------------------- poll loop


def test_poll_loop_runs_until_stop(env):
    c = _client(waits=[False, True])
    polls = []
    c._poll_once = lambda: polls.append(1)
    c._poll_loop()
    assert polls == [1, 1]
    assert c._stop.wait_args == [sl.POLL_SEC, sl.POLL_SEC]


def test_poll_once(env):
    c = sl.StreamlabsClient()
    requested = []
    payloads = [None, {"data": "x"}, {"data": [{"name": "Old", "donation_id": "1"}]},
                {"data": [{"name": "Old", "donation_id": "1"}, "junk", {"name": "New", "donation_id": "2"}]}]

    def fake_api(url, params=None):
        requested.append((url, params))
        return payloads.pop(0)

    c._api_get = fake_api
    c._poll_once()  # None
    c._poll_once()  # not a list
    c._poll_once()  # primes, no alerts
    assert env.alerts == []
    c._poll_once()
    assert [a["username"] for a in env.alerts] == ["New"]
    assert requested[0] == (sl.DONATIONS_URL, {"limit": 25})


# --------------------------------------------------------------------------- module API


def test_get_client_and_status(env):
    a = sl.get_streamlabs_client()
    assert a is sl.get_streamlabs_client()
    env.settings.streamlabs_connection_status = "Connected"
    assert sl.get_streamlabs_status() == {"status": "Connected", "error": "", "is_valid": True}
    env.settings.streamlabs_connection_status = "Error"
    env.settings.streamlabs_last_error = "x"
    assert sl.get_streamlabs_status() == {"status": "Error", "error": "x", "is_valid": False}


def test_streamlabs_configured(env):
    assert sl.streamlabs_configured() is False
    env.settings.streamlabs_access_token = "tok"
    assert sl.streamlabs_configured() is True
    env.settings.streamlabs_access_token = ""
    env.creds = {"client_id": " id "}
    assert sl.streamlabs_configured() is True
    env.creds = {"client_id": None}
    assert sl.streamlabs_configured() is False


def test_start_streamlabs_service(env, monkeypatch):
    starts = []
    monkeypatch.setattr(sl.StreamlabsClient, "start", lambda self: starts.append(1))
    env.creds = {"client_id": "id"}
    sl.start_streamlabs_service()  # configured but no token
    assert starts == []
    env.settings.streamlabs_access_token = "tok"
    sl.start_streamlabs_service()
    assert starts == [1]


def test_stop_and_disconnect_streamlabs(env, monkeypatch):
    sl.stop_streamlabs_service()  # no client: nothing
    assert env.cleared == []
    client = sl.get_streamlabs_client()
    seen = []
    monkeypatch.setattr(client, "disconnect", lambda: seen.append("d"))
    sl.disconnect_streamlabs()
    assert seen == ["d"]

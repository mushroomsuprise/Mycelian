# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/streamelements.py (Astro + realtime sockets, JWT helpers, tip gating).

Sockets and HTTP are faked: ``websocket.WebSocketApp`` is replaced by a scripted fake and
``requests.get`` by a stub, so nothing touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import base64  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Callable, List  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402

from modules import donation_alerts, notification_engine  # noqa: E402
from modules import streamelements as se  # noqa: E402


def make_jwt(payload: Any) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"hdr.{body}.sig"


# --------------------------------------------------------------------------- fakes


class Env:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            streamelements_jwt="",
            streamelements_channel_id="",
            streamelements_connection_status="",
            streamelements_last_error="",
            alert_currency="",
        )
        self.statuses: List[tuple] = []
        self.updates: List[dict] = []
        self.cleared: List[str] = []
        self.notes: List[dict] = []
        self.alerts: List[dict] = []

    def get_settings(self):
        return self.settings

    def set_status(self, service, status, error=None):
        self.statuses.append((service, status, error))
        self.settings.streamelements_connection_status = status

    def update_settings(self, **kw):
        self.updates.append(kw)
        for k, v in kw.items():
            setattr(self.settings, k, v)

    def clear_secret(self, name):
        self.cleared.append(name)


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(se, "get_settings", e.get_settings)
    monkeypatch.setattr(se, "set_status", e.set_status)
    monkeypatch.setattr(se, "update_settings", e.update_settings)
    monkeypatch.setattr(se, "clear_secret", e.clear_secret)
    monkeypatch.setattr(notification_engine, "notify", lambda message, **kw: e.notes.append({"message": message, **kw}))
    monkeypatch.setattr(donation_alerts, "emit_donation_alert", lambda **kw: e.alerts.append(kw))
    monkeypatch.setattr(se, "_client", None)
    return e


class FakeEvent:
    """Stands in for ``threading.Event`` with scripted ``wait`` results."""

    def __init__(self, waits=None, set_after_calls=None) -> None:
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
    """Stands in for ``websocket.WebSocketApp``; ``script`` runs inside run_forever."""

    script: List[Callable[["FakeApp"], None]] = []
    instances: List["FakeApp"] = []

    def __init__(self, url, on_message=None, on_error=None, on_close=None, on_open=None):
        self.url = url
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.on_open = on_open
        self.sent: List[str] = []
        self.closed = 0
        self.run_kwargs = None
        self.fail_send = False
        self.fail_close = False
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
    monkeypatch.setattr(se.websocket, "WebSocketApp", FakeApp)
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


# --------------------------------------------------------------------------- JWT helpers


def test_jwt_payload_variants():
    assert se.jwt_payload("") == {}
    assert se.jwt_payload(None) == {}
    assert se.jwt_payload("onlyone") == {}
    assert se.jwt_payload("a.!!!notb64.c") == {}
    assert se.jwt_payload("a." + base64.urlsafe_b64encode(b"not json").decode() + ".c") == {}
    assert se.jwt_payload(make_jwt([1, 2])) == {}
    assert se.jwt_payload(make_jwt({"channel": "abc"})) == {"channel": "abc"}


def test_jwt_is_expired(monkeypatch):
    monkeypatch.setattr(se.time, "time", lambda: 1000.0)
    assert se.jwt_is_expired(make_jwt({})) is False
    assert se.jwt_is_expired(make_jwt({"exp": 999})) is True
    assert se.jwt_is_expired(make_jwt({"exp": 1000})) is True
    assert se.jwt_is_expired(make_jwt({"exp": 1001})) is False
    assert se.jwt_is_expired(make_jwt({"exp": "soon"})) is False
    assert se.jwt_is_expired(make_jwt({"exp": [1]})) is False


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({}, ""),
        ({"channel": "  abc "}, "abc"),
        ({"channel": "   ", "channelId": "def"}, "def"),
        ({"channel": {"_id": "x1"}}, "x1"),
        ({"channel_id": {"id": 42}}, "42"),
        ({"channel": {"name": "n"}}, ""),
        ({"channel": 5}, ""),
    ],
)
def test_channel_id_from_jwt(payload, expected):
    assert se.channel_id_from_jwt(make_jwt(payload) if payload else "bad") == expected


def test_notify_channel_lookup_failed(env, monkeypatch):
    se._notify_channel_lookup_failed("HTTP 404")
    (note,) = env.notes
    assert note["source"] == "streamelements"
    assert note["details"] == "HTTP 404"
    assert note["dedupe_key"] == "streamelements:channel_lookup"
    assert note["actions"] == notification_engine.nav_actions_settings("StreamElements")

    def boom(*_a, **_k):
        raise RuntimeError("no ui")

    monkeypatch.setattr(notification_engine, "notify", boom)
    se._notify_channel_lookup_failed("x")  # swallowed


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "packet,expected",
    [
        (None, None),
        (42, None),
        ("40", None),
        ("42not json", None),
        ('42{"a":1}', None),
        ("42[]", None),
        ("42[1, 2]", None),
        ('42["authenticated"]', ("authenticated", None)),
        ('42["event", {"x": 1}]', ("event", {"x": 1})),
    ],
)
def test_parse_socketio_message(packet, expected):
    assert se.parse_socketio_message(packet) == expected


def test_tip_amount():
    assert se._tip_amount(None) == 0.0
    assert se._tip_amount("2.5") == 2.5
    assert se._tip_amount("abc") == 0.0
    assert se._tip_amount([1]) == 0.0


def test_normalize_realtime_tip_ignores_non_tips():
    assert se.normalize_realtime_tip(None) is None
    assert se.normalize_realtime_tip({"listener": "follower-latest"}) is None
    assert se.normalize_realtime_tip({"type": "cheer"}) is None


def test_normalize_realtime_tip_overlay_emulate_event_fields():
    tip = se.normalize_realtime_tip(
        {
            "listener": "tip-latest",
            "event": {"name": " Ada ", "amount": "5", "currency": "eur", "message": "hi", "_id": "e1"},
        }
    )
    assert tip == {"username": "Ada", "amount": 5.0, "currency": "EUR", "message": "hi", "event_id": "e1"}


def test_normalize_realtime_tip_overlay_nested_data():
    tip = se.normalize_realtime_tip(
        {
            "listener": "tip-latest",
            "_id": "outer",
            "event": {"data": {"displayName": "Bob", "message": "yo", "currency": "usd", "amount": 3}},
        }
    )
    assert tip == {"username": "Bob", "amount": 3.0, "currency": "USD", "message": "yo", "event_id": "outer"}


def test_normalize_realtime_tip_overlay_defaults():
    tip = se.normalize_realtime_tip({"listener": "tip-latest", "event": "notadict"})
    assert tip == {"username": "Anonymous", "amount": 0.0, "currency": "", "message": "", "event_id": ""}
    blank = se.normalize_realtime_tip({"listener": "tip-latest", "event": {"name": "   "}})
    assert blank["username"] == "Anonymous"


def test_normalize_realtime_tip_activity():
    tip = se.normalize_realtime_tip(
        {"type": "tip", "activityId": "act", "currency": "gbp",
         "data": {"username": "Cy", "amount": "7.25", "message": "m"}}
    )
    assert tip == {"username": "Cy", "amount": 7.25, "currency": "GBP", "message": "m", "event_id": "act"}
    bare = se.normalize_realtime_tip({"type": "tip", "data": None, "name": "N", "amount": 1})
    assert bare["username"] == "N" and bare["amount"] == 1.0 and bare["event_id"] == ""
    anon = se.normalize_realtime_tip({"type": "tip"})
    assert anon["username"] == "Anonymous"


@pytest.mark.parametrize(
    "data,expected",
    [
        (None, "drop"),
        ({"approved": "rejected"}, "drop"),
        ({"status": "failed"}, "drop"),
        ({"approved": "Pending"}, "hold"),
        ({"approved": "allowed", "status": "success"}, "alert"),
        ({"status": ""}, "alert"),
        ({}, "alert"),
    ],
)
def test_classify_tip(data, expected):
    assert se.classify_tip(data) == expected


def test_normalize_streamelements_tip():
    full = se.normalize_streamelements_tip(
        {"_id": "t1", "donation": {"user": {"username": " Dee "}, "message": "hey", "amount": "4", "currency": "cad"}}
    )
    assert full == {"username": "Dee", "amount": 4.0, "currency": "CAD", "message": "hey", "event_id": "t1"}
    bad = se.normalize_streamelements_tip({"transactionId": "tx", "donation": {"user": "x", "amount": "lots", "currency": " "}})
    assert bad == {"username": "Anonymous", "amount": 0.0, "currency": "USD", "message": "", "event_id": "tx"}
    empty = se.normalize_streamelements_tip({"donation": "nope"})
    assert empty["currency"] == "USD" and empty["event_id"] == ""


def test_tip_gate_flow():
    gate = se.TipGate()
    assert gate.consider("x") is None
    pending = {"_id": "p", "approved": "pending", "donation": {"amount": 1}}
    assert gate.consider(pending) is None
    assert "p" in gate._pending
    allowed = {"_id": "p", "approved": "allowed", "donation": {"amount": 1, "user": {"username": "U"}}}
    out = gate.consider(allowed)
    assert out["username"] == "U" and "p" not in gate._pending
    assert gate.consider(allowed) is None  # once only
    gate.consider({"_id": "q", "approved": "pending"})
    assert gate.consider({"_id": "q", "approved": "rejected"}) is None
    assert "q" not in gate._pending
    # no ids: never remembered
    assert gate.consider({"approved": "pending"}) is None
    assert gate.consider({"status": "failed"}) is None
    assert gate.consider({"donation": {}}) is not None
    assert gate.consider({"donation": {}}) is not None


# --------------------------------------------------------------------------- client lifecycle


def test_status_and_last_error_defaults(env):
    c = se.StreamElementsClient()
    assert c.status() == "Disconnected"
    assert c.last_error() == ""
    env.settings.streamelements_connection_status = "Connected"
    env.settings.streamelements_last_error = "oops"
    assert c.status() == "Connected" and c.last_error() == "oops"


def test_start_without_jwt_sets_disconnected(env):
    c = se.StreamElementsClient()
    c.start()
    assert env.statuses == [("streamelements", "Disconnected", None)]
    assert c._thread is None


def test_start_with_expired_jwt(env, monkeypatch):
    env.settings.streamelements_jwt = make_jwt({"exp": 1})
    c = se.StreamElementsClient()
    c.start()
    assert env.statuses[-1][1] == "Token expired"
    assert c._thread is None and c._realtime_thread is None


def test_start_launches_both_workers_then_noop_when_alive(env):
    env.settings.streamelements_jwt = " tok "
    c = se.StreamElementsClient()
    release = threading.Event()
    ran = []
    c._run = lambda: (ran.append("astro"), release.wait(2))
    c._realtime_loop = lambda: (ran.append("rt"), release.wait(2))
    c._realtime_authenticated = True
    old_gate = c._gate
    try:
        c.start()
        assert c._thread.name == "StreamElementsAstro" and c._thread.daemon
        assert c._realtime_thread.name == "StreamElementsRealtime"
        assert c._realtime_authenticated is False
        assert c._gate is not old_gate
        first = (c._thread, c._realtime_thread)
        c.start()  # both alive: nothing new
        assert (c._thread, c._realtime_thread) == first
    finally:
        release.set()
        c._thread.join(2)
        c._realtime_thread.join(2)
    assert sorted(ran) == ["astro", "rt"]


def test_start_restarts_only_dead_worker(env):
    env.settings.streamelements_jwt = "tok"
    c = se.StreamElementsClient()
    release = threading.Event()
    alive = threading.Thread(target=release.wait, args=(2,), daemon=True)
    alive.start()
    c._thread = alive
    started = []
    c._run = lambda: started.append("astro")
    c._realtime_loop = lambda: started.append("rt")
    try:
        c.start()
        c._realtime_thread.join(2)
        assert c._thread is alive
        assert started == ["rt"]
    finally:
        release.set()
        alive.join(2)


def test_stop_closes_sockets_and_joins(env):
    c = se.StreamElementsClient()
    good = FakeApp("u")
    bad = FakeApp("u")
    bad.fail_close = True
    c._ws, c._realtime_ws = good, bad
    release = threading.Event()
    worker = threading.Thread(target=release.wait, args=(2,), daemon=True)
    worker.start()
    c._thread = worker
    release.set()
    c.stop()
    assert c._stop.is_set()
    assert good.closed == 1 and bad.closed == 1
    assert not worker.is_alive()


def test_stop_skips_current_thread_join(env):
    c = se.StreamElementsClient()
    out = []

    def inner():
        c._thread = threading.current_thread()
        c.stop()
        out.append("done")

    t = threading.Thread(target=inner, daemon=True)
    t.start()
    t.join(2)
    assert out == ["done"]


def test_note_failure_while_realtime_authenticated(env):
    c = se.StreamElementsClient()
    c._realtime_authenticated = True
    c._note_failure("Error", "astro hiccup")
    assert env.statuses == []
    c._note_failure("Token expired", "x")
    assert env.statuses == [("streamelements", "Token expired", "x")]
    c._realtime_authenticated = False
    c._note_failure("Error", "boom")
    assert env.statuses[-1] == ("streamelements", "Error", "boom")
    c._note_connected()
    assert env.statuses[-1] == ("streamelements", "Connected", "")


def test_reject_jwt_expired_stops(env):
    c = se.StreamElementsClient()
    c._realtime_authenticated = True
    c._reject_jwt(make_jwt({"exp": 1}))
    assert env.statuses[-1][1] == "Token expired"
    assert c._stop.is_set() and c._realtime_authenticated is False


def test_reject_jwt_not_expired(env):
    c = se.StreamElementsClient()
    c._reject_jwt("tok")
    assert env.statuses[-1] == ("streamelements", "Authentication failed", "StreamElements rejected the JWT.")
    assert not c._stop.is_set()


def test_disconnect_clears_everything(env):
    c = se.StreamElementsClient()
    c.disconnect()
    assert c._stop.is_set()
    assert env.cleared == ["streamelements_jwt"]
    assert env.updates == [{"streamelements_channel_id": ""}]
    assert env.statuses[-1] == ("streamelements", "Disconnected", "")


# --------------------------------------------------------------------------- channel id


def test_resolve_channel_id_from_jwt(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: pytest.fail("no HTTP expected"))
    c = se.StreamElementsClient()
    jwt = make_jwt({"channel": "chan1"})
    assert c._resolve_channel_id(jwt) == "chan1"
    assert env.updates == [{"streamelements_channel_id": "chan1"}]
    assert c._resolve_channel_id(jwt) == "chan1"  # stored matches: no new update
    assert len(env.updates) == 1


def test_resolve_channel_id_uses_stored(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: pytest.fail("no HTTP expected"))
    env.settings.streamelements_channel_id = " stored "
    assert se.StreamElementsClient()._resolve_channel_id("tok") == "stored"


def test_resolve_channel_id_request_error(env, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(se.requests, "get", boom)
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert env.notes == []


def test_resolve_channel_id_http_lookup(env, monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse(200, {"_id": " cid "})

    monkeypatch.setattr(se.requests, "get", fake_get)
    assert se.StreamElementsClient()._resolve_channel_id("tok") == "cid"
    assert seen["url"] == se.CHANNELS_ME
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["timeout"] == 20
    assert env.updates == [{"streamelements_channel_id": "cid"}]


@pytest.mark.parametrize("status", [401, 403])
def test_resolve_channel_id_unauthorized_rejects(env, monkeypatch, status):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(status))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert env.statuses[-1][1] == "Authentication failed"


def test_resolve_channel_id_server_error_is_quiet(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(503))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert env.notes == [] and env.statuses == []


def test_resolve_channel_id_client_error_notifies(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(404))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert env.notes[-1]["details"].endswith("HTTP 404")


def test_resolve_channel_id_bad_json(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(200, bad_json=True))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert env.notes == []


@pytest.mark.parametrize("payload", [{}, ["x"], {"id": ""}])
def test_resolve_channel_id_no_id_notifies(env, monkeypatch, payload):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == ""
    assert "no channel id" in env.notes[-1]["details"]
    assert env.updates == []


def test_resolve_channel_id_uses_id_key(env, monkeypatch):
    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResponse(200, {"id": 77}))
    assert se.StreamElementsClient()._resolve_channel_id("tok") == "77"


# --------------------------------------------------------------------------- Astro loop


def _client_with_event(waits=None):
    c = se.StreamElementsClient()
    c._stop = FakeEvent(waits)
    return c


def test_run_no_jwt(env):
    c = _client_with_event()
    c._run()
    assert env.statuses == [("streamelements", "Disconnected", None)]


def test_run_retries_when_channel_missing(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event(waits=[False, False, True])
    c._resolve_channel_id = lambda jwt: ""
    c._connect = lambda *a: pytest.fail("should not connect")
    c._run()
    assert c._stop.wait_args == [2.0, 4.0, 8.0]
    assert ("streamelements", "Error", "Could not read the channel id from the JWT.") in env.statuses


def test_run_delay_caps_at_sixty(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event(waits=[False] * 7 + [True])
    c._resolve_channel_id = lambda jwt: ""
    c._run()
    assert c._stop.wait_args[-1] == 60.0


def test_run_stops_after_resolve_when_stop_set(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event()

    def resolve(jwt):
        c._stop.flag = True
        return "chan"

    c._resolve_channel_id = resolve
    c._connect = lambda *a: pytest.fail("should not connect")
    c._run()
    assert c._stop.wait_args == []


@pytest.mark.parametrize("status", ["Authentication failed", "Token expired"])
def test_run_gives_up_on_auth_failure(env, status):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event()

    def resolve(jwt):
        env.settings.streamelements_connection_status = status
        return ""

    c._resolve_channel_id = resolve
    c._run()
    assert c._stop.wait_args == []


def test_run_connects_and_reconnects_with_backoff(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event(waits=[False, True])
    c._resolve_channel_id = lambda jwt: "chan"
    connects = []
    c._connect = lambda jwt, ch: connects.append((jwt, ch))
    c._run()
    assert connects == [("tok", "chan"), ("tok", "chan")]
    assert c._stop.wait_args == [2.0, 4.0]
    assert ("streamelements", "Connecting", "Reconnecting to StreamElements.") in env.statuses


def test_run_returns_when_stopped_during_connect(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event()
    c._resolve_channel_id = lambda jwt: "chan"
    c._connect = lambda jwt, ch: setattr(c._stop, "flag", True)
    c._run()
    assert c._stop.wait_args == []


# --------------------------------------------------------------------------- Astro socket


def _welcome(app):
    app.msg(json.dumps({"type": "welcome"}))


def test_connect_subscribes_and_handles_messages(env, fake_ws):
    env.settings.streamelements_jwt = "tok"
    c = se.StreamElementsClient()
    tip = {"_id": "t1", "donation": {"user": {"username": "Ada"}, "amount": 3, "currency": "usd", "message": "hi"}}
    fake_ws.script = [
        lambda app: app.msg("not json"),
        lambda app: app.msg("[1, 2]"),
        _welcome,
        lambda app: app.msg(json.dumps({"type": "response", "data": {"topic": "channel.tips"}})),
        lambda app: app.msg(json.dumps({"type": "response", "data": {"topic": "channel.tips.moderation"}})),
        lambda app: app.msg(json.dumps({"type": "message", "topic": "channel.tips", "data": tip})),
        lambda app: app.msg(json.dumps({"type": "reconnect", "data": {"reconnect_token": "rt/1"}})),
        lambda app: app.on_error(app, "err"),
        lambda app: app.on_close(app, 1000, "bye"),
        lambda app: app.on_close(app, None, None),
    ]
    c._connect("tok", "chan")
    (app,) = fake_ws.instances
    assert app.url == se.ASTRO_URL
    assert app.run_kwargs == {"ping_interval": 20, "ping_timeout": 10}
    subs = [json.loads(s) for s in app.sent]
    assert [s["data"]["topic"] for s in subs] == list(se.TIP_TOPICS)
    assert all(s["type"] == "subscribe" and s["data"]["room"] == "chan" and s["data"]["token"] == "tok" for s in subs)
    assert ("streamelements", "Connected", "") in env.statuses
    assert env.alerts == [
        {"username": "Ada", "amount": 3.0, "currency": "USD", "message": "hi", "source": "streamelements", "event_id": "t1"}
    ]
    assert c._reconnect_token == "rt/1"
    assert app.closed == 1
    assert c._ws is None


def test_connect_with_reconnect_token_skips_subscribe(env, fake_ws):
    c = se.StreamElementsClient()
    c._reconnect_token = "a b/c"
    fake_ws.script = [
        _welcome,
        lambda app: app.msg(json.dumps({"type": "reconnect", "data": "x"})),
    ]
    c._connect("tok", "chan")
    (app,) = fake_ws.instances
    assert app.url == se.ASTRO_URL + "?reconnect_token=a%20b%2Fc"
    assert app.sent == []
    assert env.statuses == [("streamelements", "Connected", "")]
    assert c._reconnect_token == ""


def test_connect_send_and_close_errors_are_swallowed(env, fake_ws):
    c = se.StreamElementsClient()

    def failing(app):
        app.fail_send = True
        app.fail_close = True

    fake_ws.script = [
        failing,
        _welcome,
        lambda app: app.msg(json.dumps({"type": "reconnect", "data": {}})),
    ]
    c._connect("tok", "chan")
    assert fake_ws.instances[0].closed == 1


def test_connect_send_noop_when_ws_cleared(env, fake_ws):
    c = se.StreamElementsClient()
    fake_ws.script = [lambda app: setattr(c, "_ws", None), _welcome]
    c._connect("tok", "chan")
    assert fake_ws.instances[0].sent == []


def test_handle_response_errors(env):
    c = se.StreamElementsClient()
    env.settings.streamelements_jwt = "tok"
    subscribed = {"tips": False, "moderation": False}
    c._handle_response({"error": "err_bad", "data": {"message": "Bad room"}}, subscribed)
    assert env.statuses[-1] == ("streamelements", "Error", "Bad room")
    c._handle_response({"error": "err_other"}, subscribed)
    assert env.statuses[-1] == ("streamelements", "Error", "err_other")
    c._handle_response({"error": "err_unauthorized"}, subscribed)
    assert env.statuses[-1][1] == "Authentication failed"
    c._handle_response({"data": {"topic": "other"}}, subscribed)
    assert subscribed == {"tips": False, "moderation": False}


def test_handle_tip_message_filters(env):
    c = se.StreamElementsClient()
    c._handle_tip_message({"topic": "channel.follows", "data": {"donation": {}}})
    c._handle_tip_message({"topic": "channel.tips", "data": {"approved": "pending", "_id": "x"}})
    assert env.alerts == []
    c._handle_tip_message({"topic": "channel.tips.moderation", "data": {"_id": "y", "donation": {"amount": 2}}})
    assert len(env.alerts) == 1


def test_emit_normalized_fallbacks(env, monkeypatch):
    c = se.StreamElementsClient()
    monkeypatch.setattr(se.time, "time_ns", lambda: 123)
    env.settings.alert_currency = "EUR"
    c._emit_normalized({"username": "u", "amount": 1.0, "currency": "", "message": "", "event_id": ""})
    assert env.alerts[-1]["currency"] == "EUR"
    assert env.alerts[-1]["event_id"] == "streamelements-123"
    env.settings.alert_currency = ""
    c._emit_normalized({"username": "u", "amount": 1.0, "currency": "", "message": "", "event_id": "e"})
    assert env.alerts[-1]["currency"] == "USD" and env.alerts[-1]["event_id"] == "e"


# --------------------------------------------------------------------------- realtime loop + socket


def test_realtime_loop_no_jwt(env):
    c = _client_with_event()
    c._connect_realtime = lambda jwt: pytest.fail("no connect")
    c._realtime_loop()


def test_realtime_loop_backoff_until_stop(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event(waits=[False, False, False, False, False, True])
    calls = []
    c._connect_realtime = lambda jwt: calls.append(jwt)
    c._realtime_loop()
    assert len(calls) == 6
    assert c._stop.wait_args == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_realtime_loop_stops_when_stop_set(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event()
    c._connect_realtime = lambda jwt: setattr(c._stop, "flag", True)
    c._realtime_loop()
    assert c._stop.wait_args == []


def test_realtime_loop_stops_on_auth_failure(env):
    env.settings.streamelements_jwt = "tok"
    c = _client_with_event()
    c._connect_realtime = lambda jwt: setattr(env.settings, "streamelements_connection_status", "Token expired")
    c._realtime_loop()
    assert c._stop.wait_args == []


def test_connect_realtime_handshake_auth_and_tips(env, fake_ws):
    c = se.StreamElementsClient()
    event_tip = {"type": "tip", "data": {"username": "Zed", "amount": 9, "currency": "usd", "_id": "rt1"}}
    test_tip = {"listener": "tip-latest", "event": {"name": "Emu", "amount": 1}}
    fake_ws.script = [
        lambda app: app.on_message(app, None),
        lambda app: app.msg(""),
        lambda app: app.msg('0{"sid":"s","pingInterval":25000}'),
        lambda app: app.msg("40"),
        lambda app: app.msg('40{"sid":"x"}'),  # second 40: already authenticated
        lambda app: app.msg("2"),
        lambda app: app.msg("garbage"),
        lambda app: app.msg('42["authenticated", {"channelId": " c9 "}]'),
        lambda app: app.msg('42["something", {}]'),
        lambda app: app.msg('42["event", {"type": "follow"}]'),
        lambda app: app.msg("42" + json.dumps(["event", event_tip])),
        lambda app: app.msg("42" + json.dumps(["event:test", test_tip])),
    ]
    c._connect_realtime("tok")
    (app,) = fake_ws.instances
    assert app.url == se.REALTIME_URL
    assert app.run_kwargs["ping_interval"] == 0 and app.run_kwargs["ping_timeout"] is None
    assert app.run_kwargs["sslopt"]["cert_reqs"] == se.ssl.CERT_REQUIRED
    assert app.run_kwargs["sslopt"]["ca_certs"] == se.certifi.where()
    assert app.sent[0] == "40"
    assert json.loads(app.sent[1][2:]) == ["authenticate", {"method": "jwt", "token": "tok"}]
    assert app.sent[2] == "3"
    assert len(app.sent) == 3
    assert c._realtime_authenticated is True
    assert env.updates == [{"streamelements_channel_id": "c9"}]
    assert ("streamelements", "Connected", "") in env.statuses
    assert [a["username"] for a in env.alerts] == ["Zed", "Emu"]
    assert env.alerts[1]["currency"] == "USD"
    assert c._realtime_ws is None


def test_connect_realtime_authenticated_without_channel(env, fake_ws):
    c = se.StreamElementsClient()
    fake_ws.script = [
        lambda app: app.msg('42["authenticated", "ok"]'),
        lambda app: app.msg('42["authenticated", {"channel_id": ""}]'),
    ]
    c._connect_realtime("tok")
    assert env.updates == []
    assert c._realtime_authenticated is True


def test_connect_realtime_unauthorized_closes(env, fake_ws):
    c = se.StreamElementsClient()
    c._realtime_authenticated = True

    def unauthorized(app):
        app.fail_close = True
        app.msg('42["unauthorized", {}]')

    fake_ws.script = [unauthorized]
    c._connect_realtime("tok")
    assert c._realtime_authenticated is False
    assert env.statuses[-1][1] == "Authentication failed"
    assert fake_ws.instances[0].closed == 1


def test_connect_realtime_errors_close_and_send_failures(env, fake_ws):
    c = se.StreamElementsClient()
    c._realtime_authenticated = True

    def send_fails(app):
        app.fail_send = True
        app.msg("2")

    fake_ws.script = [
        send_fails,
        lambda app: app.on_error(app, "Connection closed opcode=8"),
        lambda app: app.on_error(app, "other"),
        lambda app: setattr(c, "_realtime_ws", None),
        lambda app: app.msg("2"),  # _send with no socket: no-op
        lambda app: app.on_close(app, 1006, ""),
        lambda app: app.on_close(app, None, ""),
    ]
    c._connect_realtime("tok")
    assert c._realtime_authenticated is False
    assert fake_ws.instances[0].sent == []


# --------------------------------------------------------------------------- module API


def test_get_client_singleton(env):
    a = se.get_streamelements_client()
    assert a is se.get_streamelements_client()
    assert isinstance(a, se.StreamElementsClient)


def test_get_status(env):
    env.settings.streamelements_connection_status = "Connected"
    env.settings.streamelements_last_error = "e"
    assert se.get_streamelements_status() == {"status": "Connected", "error": "e", "is_valid": True}
    env.settings.streamelements_connection_status = "Error"
    assert se.get_streamelements_status()["is_valid"] is False


def test_configured_and_start_service(env, monkeypatch):
    starts = []
    monkeypatch.setattr(se.StreamElementsClient, "start", lambda self: starts.append(self))
    env.settings.streamelements_jwt = "  "
    assert se.streamelements_configured() is False
    se.start_streamelements_service()
    assert starts == []
    env.settings.streamelements_jwt = "tok"
    assert se.streamelements_configured() is True
    se.start_streamelements_service()
    assert len(starts) == 1


def test_connect_streamelements(env, monkeypatch):
    calls = []
    monkeypatch.setattr(se.StreamElementsClient, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(se.StreamElementsClient, "stop", lambda self: calls.append("stop"))
    assert se.connect_streamelements("  ") == "Paste a StreamElements JWT first."
    assert se.connect_streamelements(None) == "Paste a StreamElements JWT first."
    assert calls == []
    assert se.connect_streamelements(" jwt ") == ""
    assert env.updates == [{"streamelements_jwt": "jwt", "streamelements_channel_id": ""}]
    assert calls == ["stop", "start"]


def test_disconnect_streamelements_without_client(env):
    se.disconnect_streamelements()
    assert env.cleared == ["streamelements_jwt"]
    assert env.updates == [{"streamelements_channel_id": ""}]
    assert env.statuses == [("streamelements", "Disconnected", "")]


def test_disconnect_streamelements_with_client(env, monkeypatch):
    client = se.get_streamelements_client()
    seen = []
    monkeypatch.setattr(client, "disconnect", lambda: seen.append("d"))
    se.disconnect_streamelements()
    assert seen == ["d"]
    assert env.cleared == []

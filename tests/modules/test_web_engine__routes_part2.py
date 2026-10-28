# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/web_engine.py Socket.IO handlers (part 2).

Database get/set/update, alert state, Twitch API proxy, PSN, pause/mute, giveaway
and poll replay, Spotify, Twitch data, Activity Feed handlers, skip, queue state,
route info / resync and the chat banner relay. Every collaborator is faked where
web_engine looks it up; templates live under tmp_path.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import dataobjects, web_engine  # noqa: E402
from modules.spore_studio import assets_watcher  # noqa: E402
from modules.spore_studio import containers as containers_mod  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402

WE = web_engine.WebEngine


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    adir = tmp_path / "assets"
    adir.mkdir()
    monkeypatch.setattr(web_engine, "get_template_path", lambda rel="": str(tdir / rel) if rel else str(tdir))
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(adir / rel) if rel else str(adir))
    return SimpleNamespace(templates=tdir, assets=adir, configs=tdir / "template_configs")


@pytest.fixture
def engine(dirs, monkeypatch):
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "web_engine_instance", "web_engine_running",
                 "EXPECTED_ALERT_COMPLETE_SEQ"):
        monkeypatch.setattr(web_engine, name, getattr(web_engine, name))
    for meth in ("ensure_web_engine_heartbeat", "ensure_template_control_emit_worker", "_refresh_cached_listener"):
        monkeypatch.setattr(WE, meth, lambda self: None)
    # Side effects of pause/mute changes that reach the UI loop and alert processor.
    for fn in ("_notify_alert_processor", "_sync_pause_button_state", "_sync_mute_button_state"):
        monkeypatch.setattr(web_engine, fn, lambda: None)
    monkeypatch.setattr(web_engine, "broadcast_alert_queue_state", lambda force=False: None)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", lambda sio: None)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: [])
    (dirs.templates / "alpha.html").write_text("<html><body>a</body></html>", encoding="utf-8")
    eng = WE(template_dir=str(dirs.templates), port=5995)
    eng.template_config_parser = TemplateConfigParser(config_dir=str(dirs.configs))
    eng.bg_tasks = []
    monkeypatch.setattr(eng.socketio, "start_background_task", lambda fn, *a, **k: eng.bg_tasks.append(fn))
    yield eng
    eng._close_emit_wake_pipe()


@pytest.fixture
def sio(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert c.is_connected()
    c.get_received()
    yield c
    if c.is_connected():
        c.disconnect()


@pytest.fixture
def sio2(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    c.get_received()
    yield c
    if c.is_connected():
        c.disconnect()


def _sid(engine, client):
    return engine.socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")


def received(client, name):
    return [e["args"][0] if e["args"] else None for e in client.get_received() if e["name"] == name]


def flush(engine):
    return engine._drain_safe_emit_queue()


def call(client, event, *args):
    """Emit and return the handler's acknowledgement value."""
    return client.emit(event, *args, callback=True)


@pytest.fixture
def remote(monkeypatch):
    """Make the current Socket.IO client look non-local for mutating events."""
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)


def _boom(*a, **k):
    raise RuntimeError("boom")


# ------------------------------------------------------------------ set_data / update_data


@pytest.fixture
def db(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(web_engine.database_manager, "set_data", lambda p, d: calls.append(("set", p, d)))
    monkeypatch.setattr(web_engine.database_manager, "update_data", lambda p, d: calls.append(("update", p, d)))
    monkeypatch.setattr(engine, "_broadcast_source_control_runtime_path_update",
                        lambda p, d: calls.append(("broadcast", p, d)))
    return calls


@pytest.mark.parametrize("event, kind", [("set_data", "set"), ("update_data", "update")])
def test_write_data(engine, sio, db, event, kind):
    assert call(sio, event, {"path": "a/b", "data": {"x": 1}}) is True
    assert db == [(kind, "a/b", {"x": 1}), ("broadcast", "a/b", {"x": 1})]


@pytest.mark.parametrize("event", ["set_data", "update_data"])
@pytest.mark.parametrize("payload", ["junk", {"path": "a"}, {"data": 1}])
def test_write_data_rejects_bad_payload(engine, sio, db, event, payload):
    assert call(sio, event, payload) is False
    assert db == []


@pytest.mark.parametrize("event", ["set_data", "update_data"])
def test_write_data_forbidden_and_errors(engine, sio, db, monkeypatch, event):
    monkeypatch.setattr(web_engine.database_manager, "set_data", _boom)
    monkeypatch.setattr(web_engine.database_manager, "update_data", _boom)
    assert call(sio, event, {"path": "a", "data": 1}) is False
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    assert call(sio, event, {"path": "a", "data": 1}) is False


# ------------------------------------------------------------------ get_data


def test_get_data_invalid(engine, sio):
    assert call(sio, "get_data", {"request_id": 4}) is False
    assert received(sio, "get_data") == [{"error": "Invalid data format", "request_id": 4}]
    assert call(sio, "get_data", "junk") is False
    assert received(sio, "get_data") == [{"error": "Invalid data format"}]


def test_get_data_shapes(engine, sio, monkeypatch):
    results = {"plain": 5, "dict": {"data": {"v": 1}, "etag": "e"}, "noetag": {"v": 2}}
    seen = []

    def get_data(path, etag):
        seen.append((path, etag))
        return results[path]

    monkeypatch.setattr(web_engine.database_manager, "get_data", get_data)
    assert call(sio, "get_data", {"path": "plain"}) == {"path": "plain", "data": 5}
    assert received(sio, "get_data") == [{"path": "plain", "data": 5}]
    out = call(sio, "get_data", {"path": "dict", "request_etag": True, "request_id": "r"})
    assert out == {"data": {"v": 1}, "etag": "e", "path": "dict"}
    assert received(sio, "get_data") == [{"data": {"v": 1}, "etag": "e", "path": "dict", "request_id": "r"}]
    assert call(sio, "get_data", {"path": "noetag"}) == {"v": 2, "path": "noetag"}
    assert results["noetag"] == {"v": 2}  # copied, not mutated
    assert seen == [("plain", False), ("dict", True), ("noetag", False)]


def test_get_data_error(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine.database_manager, "get_data", _boom)
    assert call(sio, "get_data", {"path": "x", "request_id": 9}) is False
    assert received(sio, "get_data") == [{"error": "boom", "request_id": 9}]
    assert call(sio, "get_data", {"path": "x"}) is False
    assert received(sio, "get_data") == [{"error": "boom"}]


def test_get_data_statistics_session(engine, sio, monkeypatch):
    alerts = SimpleNamespace(total_gift_subs=3, total_bits=None, follow_alerts_played=2, new_subs_played=1,
                             resubs_played=4, raids=0, bit_alerts_played=7)
    monkeypatch.setattr(web_engine.statistics_manager, "get_statistics_manager",
                        lambda: SimpleNamespace(data=SimpleNamespace(alerts=alerts)))
    doc = {"total_gift_subs": 3, "total_bits": 0, "follows": 2, "subs": 5, "raids": 0, "cheers": 7}
    assert call(sio, "get_data", {"path": "statistics/session"}) == {"data": doc, "path": "statistics/session"}
    # Snapshot failure falls through to the database.
    monkeypatch.setattr(web_engine.statistics_manager, "get_statistics_manager", _boom)
    monkeypatch.setattr(web_engine.database_manager, "get_data", lambda p, e: {"db": True})
    assert call(sio, "get_data", {"path": "statistics/session"}) == {"db": True, "path": "statistics/session"}


# ------------------------------------------------------------------ alert state manager handlers


class FakeAlertState:
    def __init__(self):
        self.calls = []
        self.update_ok = True
        self.save_ok = True

    def get_alerts_by_type(self, alert_type, include_ranges=True):
        self.calls.append(("by_type", alert_type, include_ranges))
        return {alert_type: {"x": 1}}

    def get_all_alerts(self):
        self.calls.append(("all",))
        return {"all": 1}

    def update_alert_data(self, alert_id, alert_data):
        self.calls.append(("update", alert_id, alert_data))
        return self.update_ok

    def save_alert_by_id(self, alert_id):
        self.calls.append(("save", alert_id))
        return self.save_ok

    def get_stored_alerts(self):
        return {"a1": {}}


@pytest.fixture
def alert_state(monkeypatch):
    fake = FakeAlertState()
    inits = []
    monkeypatch.setattr(web_engine.alertutils, "initialize_alert_state", lambda: inits.append(1))
    monkeypatch.setattr(web_engine.alertutils, "alert_state_manager", fake)
    fake.inits = inits
    return fake


def test_get_alert_state(engine, sio, alert_state):
    assert call(sio, "get_alert_state") == {"all": 1}
    assert received(sio, "alert_state_response") == [{"all": 1}]
    assert call(sio, "get_alert_state", {"alert_type": "bits", "include_ranges": False}) == {"bits": {"x": 1}}
    assert alert_state.calls == [("all",), ("by_type", "bits", False)]
    assert len(alert_state.inits) == 2


def test_get_alert_state_error(engine, sio, alert_state, monkeypatch):
    monkeypatch.setattr(alert_state, "get_all_alerts", _boom)
    assert call(sio, "get_alert_state") == {"error": "boom"}
    assert received(sio, "alert_state_response") == [{"error": "boom"}]


def test_update_alert_data(engine, sio, alert_state):
    assert call(sio, "update_alert_data", {"alert_id": "bits100", "alert_data": {"a": 1}}) == {"success": True}
    alert_state.update_ok = False
    assert call(sio, "update_alert_data", {"alert_id": "b", "alert_data": {}}) == {
        "success": False, "error": "Failed to update alert data"}
    assert call(sio, "update_alert_data", {"alert_id": "b"})["error"].startswith("Invalid data format")
    alert_state.update_alert_data = _boom
    assert call(sio, "update_alert_data", {"alert_id": "b", "alert_data": {}}) == {"success": False, "error": "boom"}


def test_save_alert_by_id(engine, sio, alert_state):
    assert call(sio, "save_alert_by_id", {"alert_id": "subs1"}) == {"success": True}
    alert_state.save_ok = False
    assert call(sio, "save_alert_by_id", {"alert_id": "subs1"}) == {"success": False, "error": "Failed to save alert"}
    assert call(sio, "save_alert_by_id", "junk")["error"] == "Invalid data format: alert_id required"
    alert_state.save_alert_by_id = _boom
    assert call(sio, "save_alert_by_id", {"alert_id": "x"}) == {"success": False, "error": "boom"}


def test_alert_writes_forbidden(engine, sio, alert_state, remote):
    assert call(sio, "update_alert_data", {"alert_id": "b", "alert_data": {}}) == {"success": False, "error": "Forbidden"}
    assert call(sio, "save_alert_by_id", {"alert_id": "b"}) == {"success": False, "error": "Forbidden"}
    assert alert_state.calls == []


def test_get_stored_alerts(engine, sio, alert_state):
    assert call(sio, "get_stored_alerts") == {"a1": {}}
    alert_state.get_stored_alerts = _boom
    assert call(sio, "get_stored_alerts") == {}


# ------------------------------------------------------------------ twitch_api_proxy


class FakeTwitchApi:
    def __init__(self, refresh=False, fail=False):
        self.auth_token = "old"
        self.client_id = "cid"
        self.client_secret = "sec"
        self.refresh_token = "rt"
        self.user_id = "u1"
        self.token_expiry = None
        self._refresh, self._fail = refresh, fail
        self.calls = []

    async def generic_api_call(self, url, method, params, json_data):
        self.calls.append((url, method, params, json_data))
        if self._fail:
            raise RuntimeError("api down")
        if self._refresh:
            self.auth_token = "new-token"
        return {"data": [1]}


class FakeState:
    def __init__(self, twitch=None, save_ok=True):
        self.twitch = twitch
        self.saved = []
        self.save_ok = save_ok
        self.psn = None
        self.spotify = None

    def get_twitch_data(self):
        return self.twitch

    def set_twitch_data(self, data):
        self.saved.append(data)

    def save_changes(self):
        return self.save_ok

    def get_live_psn_data(self):
        return self.psn

    def get_spotify_data(self):
        return self.spotify


@pytest.fixture
def proxy(engine, monkeypatch):
    coros = []
    monkeypatch.setattr(engine, "_submit_twitch_api_coro", coros.append)
    state = FakeState()
    monkeypatch.setattr(dataobjects, "state_manager", state)
    api = FakeTwitchApi()
    monkeypatch.setattr(web_engine, "twitch", SimpleNamespace(twitch_api=api))

    def run():
        for coro in coros:
            asyncio.run(coro)
        coros.clear()
        flush(engine)

    return SimpleNamespace(coros=coros, state=state, api=api, run=run)


HELIX = "https://api.twitch.tv/helix/users"


def _proxy_responses(sio):
    return received(sio, "twitch_api_proxy_response")


def test_twitch_proxy_rejects_bad_input(engine, sio, proxy):
    sio.emit("twitch_api_proxy", {"method": "GET"})
    sio.emit("twitch_api_proxy", {"url": "https://evil.example/x"})
    flush(engine)
    assert _proxy_responses(sio) == [
        {"error": "Invalid data format: 'url' is required.", "success": False},
        {"error": "url must be https://api.twitch.tv/…", "success": False},
    ]
    assert proxy.coros == []


def test_twitch_proxy_not_initialised(engine, sio, proxy, monkeypatch):
    monkeypatch.setattr(web_engine, "twitch", SimpleNamespace(twitch_api=None))
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    assert _proxy_responses(sio) == [{"error": "Twitch API not initialized.", "success": False}]


@pytest.mark.parametrize("twitch_data", [None, dataobjects.TwitchData(auth_token="", client_id="c"),
                                         dataobjects.TwitchData(auth_token="t", client_id="")])
def test_twitch_proxy_needs_auth(engine, sio, proxy, twitch_data):
    proxy.state.twitch = twitch_data
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    assert _proxy_responses(sio) == [{"error": "Twitch authentication required.", "success": False}]


def test_twitch_proxy_syncs_tokens_and_calls(engine, sio, proxy):
    proxy.state.twitch = dataobjects.TwitchData(auth_token="tok", client_id="cid2", client_secret="s2",
                                                refresh_token="r2", token_expiry="2030-01-01T00:00:00")
    sio.emit("twitch_api_proxy", {"url": HELIX, "method": "post", "params": {"a": 1}, "json_data": {"b": 2}})
    proxy.run()
    assert proxy.api.calls == [(HELIX, "POST", {"a": 1}, {"b": 2})]
    assert (proxy.api.auth_token, proxy.api.client_id, proxy.api.client_secret, proxy.api.refresh_token) == (
        "tok", "cid2", "s2", "r2")
    assert proxy.api.token_expiry.year == 2030
    assert _proxy_responses(sio) == [{"success": True, "data": {"data": [1]}}]
    assert proxy.state.saved == []


def test_twitch_proxy_bad_expiry(engine, sio, proxy):
    proxy.state.twitch = dataobjects.TwitchData(auth_token="tok", client_id="cid", token_expiry="not-a-date")
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    assert proxy.api.token_expiry is None
    assert _proxy_responses(sio)[0]["success"] is True


@pytest.mark.parametrize("save_ok", [True, False])
def test_twitch_proxy_persists_refreshed_tokens(engine, sio, proxy, save_ok):
    import datetime as dt

    proxy.state.twitch = dataobjects.TwitchData(auth_token="old", client_id="cid", client_secret="sec",
                                                refresh_token="rt", current_category="Games")
    proxy.state.save_ok = save_ok
    proxy.api._refresh = True
    proxy.api.token_expiry = dt.datetime(2031, 1, 1)
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    (saved,) = proxy.state.saved
    assert saved["auth_token"] == "new-token" and saved["current_category"] == "Games"
    assert saved["token_expiry"] == "2031-01-01T00:00:00"
    assert _proxy_responses(sio) == [{"success": True, "data": {"data": [1]}}]


def test_twitch_proxy_refresh_sync_failure(engine, sio, proxy):
    proxy.state.twitch = dataobjects.TwitchData(auth_token="old", client_id="cid", client_secret="sec",
                                                refresh_token="rt")
    proxy.api._refresh = True
    proxy.state.set_twitch_data = _boom
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    assert _proxy_responses(sio) == [{"success": True, "data": {"data": [1]}}]


def test_twitch_proxy_call_failure(engine, sio, proxy):
    proxy.state.twitch = dataobjects.TwitchData(auth_token="old", client_id="cid", client_secret="sec",
                                                refresh_token="rt")
    proxy.api._fail = True
    sio.emit("twitch_api_proxy", {"url": HELIX})
    proxy.run()
    assert _proxy_responses(sio) == [{"error": "api down", "success": False}]


def test_twitch_proxy_submit_failure(engine, sio, proxy, monkeypatch):
    def submit(coro):
        coro.close()
        raise RuntimeError("queue gone")

    monkeypatch.setattr(engine, "_submit_twitch_api_coro", submit)
    sio.emit("twitch_api_proxy", {"url": HELIX})
    flush(engine)
    assert _proxy_responses(sio) == [{"error": "queue gone", "success": False}]


# ------------------------------------------------------------------ PSN


def test_get_psn_data(engine, sio, monkeypatch):
    from modules import psn_service

    state = FakeState()
    monkeypatch.setattr(web_engine, "state_manager", state)
    monkeypatch.setattr(psn_service, "_public_psn_payload", lambda d: {"game": getattr(d, "current_game_name", None)})
    assert call(sio, "get_psn_data") is True
    assert received(sio, "psn_data_update") == [{"game": None}]  # default PSNData
    state.psn = SimpleNamespace(current_game_name="Astro Bot")
    assert call(sio, "get_psn_data", {"x": 1}) is True
    assert received(sio, "psn_data_update") == [{"game": "Astro Bot"}]


def test_get_psn_data_debug_logging_and_error(engine, sio, monkeypatch):
    from modules import psn_service

    state = FakeState()
    state.psn = SimpleNamespace(current_game_name="G")
    monkeypatch.setattr(web_engine, "state_manager", state)
    monkeypatch.setattr(psn_service, "_public_psn_payload", lambda d: {"current_game_name": "G"})
    monkeypatch.setattr(web_engine.logger, "isEnabledFor", lambda level: True)
    assert call(sio, "get_psn_data") is True
    state.psn = None
    assert call(sio, "get_psn_data") is True
    sio.get_received()
    state.get_live_psn_data = _boom
    assert call(sio, "get_psn_data") is False
    assert received(sio, "psn_data_error") == [{"error": "boom"}]


# ------------------------------------------------------------------ pause / mute status


def test_pause_loaded_and_get_pause_status(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    sio.emit("pause-loaded")
    assert received(sio, "pause_status_update") == [{"paused": True}]
    assert call(sio, "get_pause_status") == {"paused": True}
    assert received(sio, "pause_status_update") == [{"paused": True}]


@pytest.mark.parametrize("paused, event", [(True, "alerts_paused"), (False, "alerts_resumed")])
def test_set_pause_status_broadcasts(engine, sio, sio2, paused, event):
    assert call(sio, "set_pause_status", {"paused": int(paused)}) == {"success": True, "paused": paused}
    assert web_engine.ALERTS_PAUSED is paused
    names = [e["name"] for e in sio2.get_received()]
    assert names == [event, "pause_status_update"]


def test_set_pause_status_invalid_forbidden_error(engine, sio, monkeypatch):
    assert call(sio, "set_pause_status", {"x": 1})["error"] == "Invalid data format: paused boolean required"
    monkeypatch.setattr(web_engine, "set_alerts_paused", _boom)
    assert call(sio, "set_pause_status", {"paused": True}) == {"success": False, "error": "boom"}
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    assert call(sio, "set_pause_status", {"paused": True}) == {"success": False, "error": "forbidden"}


def test_mute_status(engine, sio, sio2, monkeypatch):
    synced, broadcasts = [], []
    monkeypatch.setattr(web_engine, "_sync_mute_button_state", lambda: synced.append(1))
    monkeypatch.setattr(web_engine, "broadcast_alert_queue_state", lambda force=False: broadcasts.append(1))
    monkeypatch.setattr(web_engine, "ALERTS_MUTED", False)
    assert call(sio, "get_mute_status") == {"muted": False}
    assert received(sio, "mute_status_update") == [{"muted": False}]
    assert call(sio, "set_mute_status", {"muted": 1}) == {"success": True, "muted": True}
    assert web_engine.ALERTS_MUTED is True
    assert received(sio2, "mute_status_update") == [{"muted": True}]
    assert synced == [1] and broadcasts == [1]
    assert call(sio, "set_mute_status", {})["error"] == "Invalid data format: muted boolean required"
    monkeypatch.setattr(web_engine, "_sync_mute_button_state", _boom)
    assert call(sio, "set_mute_status", {"muted": False}) == {"success": False, "error": "boom"}
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    assert call(sio, "set_mute_status", {"muted": False}) == {"success": False, "error": "forbidden"}


# ------------------------------------------------------------------ giveaway / poll replay


def test_giveaway_and_poll_state(engine, sio, monkeypatch):
    from modules import giveaway_manager, poll_manager

    templates = []
    monkeypatch.setattr(giveaway_manager, "get_giveaway_manager",
                        lambda: SimpleNamespace(get_overlay_state=lambda: {"open": True}))
    monkeypatch.setattr(poll_manager, "get_poll_manager",
                        lambda: SimpleNamespace(get_overlay_state=lambda t: templates.append(t) or {"poll": t}))
    assert call(sio, "giveaway_request_state") == {"open": True}
    assert call(sio, "poll_request_state", {"template": " vote "}) == {"poll": "vote"}
    assert call(sio, "poll_request_state", "junk") == {"poll": ""}
    flush(engine)
    events = sio.get_received()
    assert [(e["name"], e["args"][0]) for e in events] == [
        ("giveaway_sync", {"open": True}), ("poll_sync", {"poll": "vote"}), ("poll_sync", {"poll": ""})]
    monkeypatch.setattr(giveaway_manager, "get_giveaway_manager", _boom)
    monkeypatch.setattr(poll_manager, "get_poll_manager", _boom)
    assert call(sio, "giveaway_request_state") == {}
    assert call(sio, "poll_request_state", {}) == {}


# ------------------------------------------------------------------ Spotify


@pytest.fixture
def spotify(monkeypatch):
    from modules import spotify as sp

    state = FakeState()
    monkeypatch.setattr(dataobjects, "state_manager", state)
    monkeypatch.setattr(sp, "public_spotify_payload", lambda d: {"track": getattr(d, "track_name", None)})
    return SimpleNamespace(mod=sp, state=state)


def test_get_spotify_data(engine, sio, spotify):
    assert call(sio, "get_spotify_data") is True
    assert received(sio, "spotify_data_update") == [{"track": None}]
    spotify.state.spotify = SimpleNamespace(track_name="Song")
    assert call(sio, "get_spotify_data") is True
    assert received(sio, "spotify_data_update") == [{"track": "Song"}]
    spotify.state.get_spotify_data = _boom
    assert call(sio, "get_spotify_data") is False
    assert received(sio, "spotify_data_error") == [{"error": "boom"}]


def test_spotify_oauth_start(engine, sio, spotify, monkeypatch):
    monkeypatch.setattr(spotify.mod, "trigger_oauth_flow", lambda: "https://accounts.spotify.com/x")
    assert call(sio, "spotify_oauth_start") == {"success": True, "url": "https://accounts.spotify.com/x"}
    assert received(sio, "spotify_oauth_url") == [{"url": "https://accounts.spotify.com/x"}]
    monkeypatch.setattr(spotify.mod, "trigger_oauth_flow", lambda: "")
    assert call(sio, "spotify_oauth_start") == {"success": False, "error": "Failed to generate OAuth URL"}
    assert received(sio, "spotify_oauth_error") == [{"error": "Failed to generate OAuth URL"}]
    monkeypatch.setattr(spotify.mod, "trigger_oauth_flow", _boom)
    assert call(sio, "spotify_oauth_start") == {"success": False, "error": "boom"}
    assert received(sio, "spotify_oauth_error") == [{"error": "boom"}]


def test_spotify_oauth_complete(engine, sio, spotify, monkeypatch):
    codes = []
    monkeypatch.setattr(spotify.mod, "complete_oauth_with_code", lambda c: codes.append(c) or True)
    assert call(sio, "spotify_oauth_complete", {"code": "abc"}) == {"success": True}
    assert received(sio, "spotify_oauth_success") == [{"message": "Successfully connected to Spotify"}]
    assert codes == ["abc"]
    assert call(sio, "spotify_oauth_complete", {}) == {"success": False, "error": "Invalid data format: code required"}
    assert received(sio, "spotify_oauth_error") == [{"error": "Invalid data format: code required"}]
    monkeypatch.setattr(spotify.mod, "complete_oauth_with_code", lambda c: False)
    assert call(sio, "spotify_oauth_complete", {"code": "x"}) == {"success": False,
                                                                 "error": "Failed to complete OAuth flow"}
    monkeypatch.setattr(spotify.mod, "complete_oauth_with_code", _boom)
    assert call(sio, "spotify_oauth_complete", {"code": "x"}) == {"success": False, "error": "boom"}
    assert received(sio, "spotify_oauth_error")[-1] == {"error": "boom"}


def test_update_spotify_settings(engine, sio, spotify, monkeypatch):
    seen = []
    monkeypatch.setattr(spotify.mod, "update_spotify_settings", lambda **kw: seen.append(kw))
    assert call(sio, "update_spotify_settings", {"client_id": "i", "client_secret": "s"}) == {"success": True}
    assert call(sio, "update_spotify_settings", {}) == {"success": True}
    assert seen == [{"client_id": "i", "client_secret": "s"}, {"client_id": "", "client_secret": ""}]
    assert call(sio, "update_spotify_settings", "junk")["error"] == "Invalid data format: dictionary required"
    monkeypatch.setattr(spotify.mod, "update_spotify_settings", _boom)
    assert call(sio, "update_spotify_settings", {}) == {"success": False, "error": "boom"}


def test_get_spotify_status(engine, sio, spotify, monkeypatch):
    monkeypatch.setattr(spotify.mod, "get_spotify_status", lambda: {"status": "connected"})
    assert call(sio, "get_spotify_status") == {"status": "connected"}
    assert received(sio, "spotify_status_update") == [{"status": "connected"}]
    monkeypatch.setattr(spotify.mod, "get_spotify_status", _boom)
    assert call(sio, "get_spotify_status") == {"success": False, "error": "boom"}
    assert received(sio, "spotify_status_error") == [{"error": "boom"}]


# ------------------------------------------------------------------ Twitch data


def test_get_twitch_data_strips_secrets(engine, sio, monkeypatch):
    state = FakeState(twitch=dataobjects.TwitchData(client_id="cid", auth_token="t", refresh_token="r",
                                                    client_secret="s", current_category="Art"))
    monkeypatch.setattr(dataobjects, "state_manager", state)
    assert call(sio, "get_twitch_data") is True
    (payload,) = received(sio, "twitch_data_update")
    assert payload["client_id"] == "cid" and payload["current_category"] == "Art"
    assert payload["auth_token"] == payload["refresh_token"] == payload["client_secret"] == ""
    state.twitch = None
    assert call(sio, "get_twitch_data") is True
    assert received(sio, "twitch_data_update")[0]["client_id"] == ""
    state.get_twitch_data = _boom
    assert call(sio, "get_twitch_data") is False
    assert received(sio, "twitch_data_error") == [{"error": "boom"}]


# ------------------------------------------------------------------ Activity Feed handlers


@pytest.fixture
def feed(monkeypatch):
    mod = types.ModuleType("modules.uiwindows.activity_feed")
    mod.calls = []
    mod._emit_stored_alerts_payload = lambda alerts, info, to=None: mod.calls.append(("emit", alerts, info, to))
    mod.load_restored_alerts = lambda page: (mod.calls.append(("load", page)) or
                                             (["a"], {"page": page, "total_pages": 3, "total_count": 30}))
    mod.parse_condensed_historical_hours = lambda raw: int(raw)
    mod.normalize_filter_state = lambda f: dict(f, normalized=True)
    mod.view_manager = SimpleNamespace(load=lambda: {"filters": {"saved": True}},
                                       update=lambda d: dict(d, updated=True))
    mod.build_condensed_overlay_payload = lambda hours, filter_state, search: {
        "hours": hours if hours is not None else 6, "filters": filter_state, "search": search, "groups": []}
    mod.publish_activity_feed_view = lambda to=None: {"view": "list", "to": bool(to)}
    mod.broadcast_feed_settings = lambda to=None: {"font": 14}
    mod.open_feed_settings = lambda: mod.calls.append(("open",))
    monkeypatch.setitem(sys.modules, "modules.uiwindows.activity_feed", mod)
    return mod


def test_stored_alerts_paginated(engine, sio, feed):
    sid = _sid(engine, sio)
    assert call(sio, "get_stored_alerts_paginated", {"page": "2", "limit": 99}) == {
        "success": True, "page": 2, "total_pages": 3, "total_count": 30}
    assert feed.calls == [("load", 2), ("emit", ["a"], {"page": 2, "total_pages": 3, "total_count": 30}, sid)]
    feed.calls.clear()
    call(sio, "get_stored_alerts_paginated", {"page": "x"})
    call(sio, "get_stored_alerts_paginated", {"page": -4})
    assert [c[1] for c in feed.calls if c[0] == "load"] == [1, 1]
    assert call(sio, "get_stored_alerts_paginated", "junk")["error"] == "Invalid data format: dictionary required"
    feed.load_restored_alerts = lambda page: ([], {"error": "db"})
    assert call(sio, "get_stored_alerts_paginated", {}) == {"success": False, "page": 1, "total_pages": 1,
                                                            "total_count": 0}


def test_stored_alerts_paginated_error(engine, sio, feed):
    feed.load_restored_alerts = _boom
    out = call(sio, "get_stored_alerts_paginated", {})
    assert out["success"] is False and out["error"] == "boom" and out["alerts"] == []
    assert received(sio, "stored_alerts_paginated") == [out]


def test_condensed_view_alerts(engine, sio, feed):
    out = call(sio, "get_condensed_view_alerts", {"hours": "4", "filters": {"bits": True}, "search": 12})
    assert out == {"hours": 4, "filters": {"bits": True, "normalized": True}, "search": "12", "groups": []}
    assert received(sio, "condensed_view_alerts") == [out]
    out = call(sio, "get_condensed_view_alerts", {"hours": ""})
    assert out["hours"] == 6 and out["filters"] == {"saved": True} and out["search"] is None
    bad = call(sio, "get_condensed_view_alerts", "junk")
    assert bad == {"success": False, "error": "Invalid data format: dictionary required", "groups": [],
                   "historical_count": 0, "hours": 12}


def test_condensed_view_alerts_error(engine, sio, feed):
    feed.build_condensed_overlay_payload = _boom
    out = call(sio, "get_condensed_view_alerts", {"hours": "3"})
    assert out == {"success": False, "error": "boom", "groups": [], "historical_count": 0, "hours": 3}
    assert received(sio, "condensed_view_alerts") == [out]


def test_activity_feed_view_and_settings(engine, sio, feed):
    assert call(sio, "get_activity_feed_view") == {"success": True, "view": {"view": "list", "to": True}}
    assert call(sio, "set_activity_feed_view", {"mode": "compact"}) == {
        "success": True, "view": {"mode": "compact", "updated": True}}
    assert call(sio, "set_activity_feed_view", "junk")["error"] == "Invalid data format: dictionary required"
    assert call(sio, "get_activity_feed_settings") == {"success": True, "settings": {"font": 14}}
    assert call(sio, "activity_feed_open_settings") == {"success": True}
    assert feed.calls == [("open",)]


def test_activity_feed_handler_errors(engine, sio, feed):
    feed.publish_activity_feed_view = _boom
    feed.view_manager = SimpleNamespace(update=_boom)
    feed.broadcast_feed_settings = _boom
    feed.open_feed_settings = _boom
    assert call(sio, "get_activity_feed_view") == {"success": False, "error": "boom"}
    assert received(sio, "activity_feed_view") == [{"success": False, "error": "boom"}]
    assert call(sio, "set_activity_feed_view", {}) == {"success": False, "error": "boom"}
    assert call(sio, "get_activity_feed_settings") == {"success": False, "error": "boom"}
    assert call(sio, "activity_feed_open_settings") == {"success": False, "error": "boom"}


def test_activity_feed_writes_forbidden(engine, sio, feed, remote):
    assert call(sio, "set_activity_feed_view", {}) == {"success": False, "error": "forbidden"}
    assert call(sio, "activity_feed_open_settings") == {"success": False, "error": "forbidden"}
    assert call(sio, "activity_feed_replay_alert", {"alert_id": "a"})["reason"] == "forbidden"
    assert call(sio, "skip_alert") == {"ok": False, "success": False, "reason": "forbidden"}
    assert call(sio, "activity_feed_skip_alert") == {"ok": False, "success": False, "reason": "forbidden"}
    assert call(sio, "force_route_resync") == {"success": False, "error": "Forbidden"}
    assert feed.calls == []


def test_activity_feed_replay_alert(engine, sio, monkeypatch):
    seen = []
    monkeypatch.setattr(web_engine.alertutils, "replay_alert",
                        lambda aid: seen.append(aid) or {"ok": True, "reason": "replayed", "new_alert_id": "n"})
    assert call(sio, "activity_feed_replay_alert", {"alert_id": "a1"}) == {
        "ok": True, "reason": "replayed", "new_alert_id": "n", "success": True}
    monkeypatch.setattr(web_engine.alertutils, "replay_alert", lambda aid: {"ok": False, "reason": "missing"})
    assert call(sio, "activity_feed_replay_alert", "junk") == {"ok": False, "reason": "missing", "success": False,
                                                               "error": "missing"}
    monkeypatch.setattr(web_engine.alertutils, "replay_alert", lambda aid: {"ok": False, "reason": ""})
    assert call(sio, "activity_feed_replay_alert", {})["error"] == "error"
    monkeypatch.setattr(web_engine.alertutils, "replay_alert", _boom)
    assert call(sio, "activity_feed_replay_alert", {"alert_id": "a"}) == {
        "ok": False, "reason": "error", "new_alert_id": None, "success": False, "error": "error"}
    assert seen == ["a1"]


@pytest.mark.parametrize("event, source", [("skip_alert", "skip_alert"), ("activity_feed_skip_alert", "activity_feed")])
def test_skip_handlers(engine, sio, monkeypatch, event, source):
    seen = []
    monkeypatch.setattr(web_engine, "skip_alert_from_payload",
                        lambda data, source="": seen.append((data, source)) or {"ok": True})
    assert call(sio, event, {"alert_id": "x"}) == {"ok": True}
    assert seen == [({"alert_id": "x"}, source)]
    monkeypatch.setattr(web_engine, "skip_alert_from_payload", _boom)
    assert call(sio, event) == {"ok": False, "success": False, "reason": "error"}


# ------------------------------------------------------------------ alert queue / started


def test_get_alert_queue_state(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: {"queue": [], "revision": 3})
    assert call(sio, "get_alert_queue_state") == {"queue": [], "revision": 3}
    assert received(sio, web_engine.ALERT_QUEUE_STATE_EVENT) == [{"queue": [], "revision": 3}]
    real = engine.socketio.emit
    monkeypatch.setattr(engine.socketio, "emit",
                        lambda ev, *a, **k: _boom() if ev == web_engine.ALERT_QUEUE_STATE_EVENT else real(ev, *a, **k))
    assert call(sio, "get_alert_queue_state") == {"queue": [], "revision": 3}


def test_alert_started(engine, sio, monkeypatch):
    from modules import alert_processor

    seen = []
    monkeypatch.setattr(alert_processor, "note_current_alert_duration", lambda seq, ms: seen.append((seq, ms)))
    for payload in ("junk", {"queue_seq": "x"}, {"queue_seq": -1}, {}):
        sio.emit("alert_started", payload)
    sio.emit("alert_started", {"queue_seq": "4", "duration_ms": 1500})
    assert seen == [(4, 1500)]
    engine._register_preview_iframe_sid("tok", _sid(engine, sio))
    sio.emit("alert_started", {"queue_seq": 5, "duration_ms": 1})
    assert seen == [(4, 1500)]


# ------------------------------------------------------------------ route info / resync


def test_get_route_info(engine, sio, monkeypatch):
    info = call(sio, "get_route_info")
    assert info["template_routes"] == ["alpha"] and info["total_routes"] == 1
    assert received(sio, "route_info_update") == [info]
    monkeypatch.setattr(engine, "get_registered_template_routes", _boom)
    assert call(sio, "get_route_info") == {"success": False, "error": "boom"}
    assert received(sio, "route_info_error") == [{"success": False, "error": "boom"}]


def test_force_route_resync(engine, sio, sio2, monkeypatch):
    monkeypatch.setattr(engine, "force_route_resync", lambda: True)
    out = call(sio, "force_route_resync")
    assert out["success"] is True and out["message"] == "Route resynchronization completed"
    (done,) = received(sio2, "route_resync_completed")
    assert done["manual_resync"] is True and done["route_info"]["template_routes"] == ["alpha"]
    monkeypatch.setattr(engine, "force_route_resync", lambda: False)
    assert call(sio, "force_route_resync") == {"success": False, "error": "Route resynchronization failed"}
    monkeypatch.setattr(engine, "force_route_resync", _boom)
    assert call(sio, "force_route_resync") == {"success": False, "error": "boom"}


# ------------------------------------------------------------------ chat banner relay


def test_chat_banner_request(engine, sio, monkeypatch):
    from modules import chat_banner

    seen = []
    monkeypatch.setattr(chat_banner, "emit_chat_banner", lambda payload=None: seen.append(payload))
    sio.emit("chat_banner_request", {"text": "hi"})
    sio.emit("chat_banner_request", {"text": "x", "source": "bot"})
    sio.emit("chat_banner_request", "junk")
    assert seen == [{"text": "hi", "source": "template"}, {"text": "x", "source": "bot"}, {"source": "template"}]

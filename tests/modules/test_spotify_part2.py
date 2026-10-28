# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spotify.py, part 2: module-level service functions and helpers.

Module globals (spotify_client, threads, is_running) are swapped with monkeypatch and the
client is a fake, so no monitor thread talks to Spotify.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import spotify as sp
from modules.dataobjects import SpotifyData


class FakeClient:
    def __init__(self, **data):
        self.spotify_data = SpotifyData(**data)
        self.is_authenticated = False
        self.cooldown = False
        self.backoff = False
        self.auth_results = []
        self.calls = []
        self.monitor_event = threading.Event()

    def is_in_auth_cooldown(self):
        return self.cooldown() if callable(self.cooldown) else self.cooldown

    def is_in_api_backoff(self):
        return self.backoff

    def reload_and_sync(self, persist=True):
        self.calls.append(("reload", persist))

    def authenticate(self):
        self.calls.append(("authenticate",))
        return self.auth_results.pop(0) if self.auth_results else False

    def update_field(self, field, value):
        self.calls.append(("update_field", field, value))
        setattr(self.spotify_data, field, value)

    def _sync_client_credentials(self, persist=False):
        self.calls.append(("sync", persist))
        return True

    def start_oauth_flow(self):
        self.calls.append(("start_oauth_flow",))
        return "https://auth"

    def start_oauth_flow_with_server(self):
        self.calls.append(("start_oauth_flow_with_server",))
        return True

    def complete_oauth_flow(self, code):
        self.calls.append(("complete", code))
        return code == "good"

    def start_monitoring(self):
        self.calls.append(("start_monitoring",))
        self.monitor_event.set()

    def stop_monitoring(self):
        self.calls.append(("stop_monitoring",))


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(sp, "spotify_client", None)
    monkeypatch.setattr(sp, "spotify_thread", None)
    monkeypatch.setattr(sp, "is_running", False)
    monkeypatch.setattr(sp, "_spotify_start_thread", None)
    monkeypatch.setattr(sp.webbrowser, "open", lambda *a, **k: pytest.fail("browser opened"))
    yield


def _set_client(monkeypatch, client):
    monkeypatch.setattr(sp, "spotify_client", client)
    return client


# --------------------------------------------------------------------------- small pieces


def test_public_payload_none():
    p = sp.public_spotify_payload(None)
    assert p["connection_status"] == "Disconnected" and p["track_name"] == "" and p["is_playing"] is False
    assert set(p) == {"track_name", "artist_name", "album_name", "album_image_url", "current_tracktime",
                      "track_length", "current_tracktime_seconds", "track_length_seconds", "is_playing",
                      "progress_percentage", "connection_status"}


def test_public_payload_hides_secrets_and_defaults_blanks():
    data = SpotifyData(client_id="cid", client_secret="SECRET", access_token="AT", refresh_token="RT",
                       track_name="Song", is_playing=1, progress_percentage=12.5)
    p = sp.public_spotify_payload(data)
    assert p["track_name"] == "Song" and p["is_playing"] is True and p["progress_percentage"] == 12.5
    for secret in ("SECRET", "AT", "RT", "cid"):
        assert secret not in p.values()

    blank = SimpleNamespace(track_name=None, current_tracktime="", connection_status="")
    p = sp.public_spotify_payload(blank)
    assert p["track_name"] == "" and p["current_tracktime"] == "0:00"
    assert p["connection_status"] == "Disconnected" and p["track_length_seconds"] == 0


def test_callback_server_stop_joins_live_thread():
    server = sp.OAuthCallbackServer()
    joined = []
    server.server_thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout: joined.append(timeout))
    server.stop()
    assert joined == [2]


def test_initialize_spotify_creates_once(monkeypatch):
    made = []

    class Fake:
        def __init__(self):
            made.append(self)

    monkeypatch.setattr(sp, "SpotifyClient", Fake)
    first = sp.initialize_spotify()
    assert sp.initialize_spotify() is first and len(made) == 1
    assert sp.get_spotify_client() is first


def test_initialize_spotify_error(monkeypatch):
    def boom():
        raise RuntimeError("bad")

    monkeypatch.setattr(sp, "SpotifyClient", boom)
    assert sp.initialize_spotify() is None
    assert sp.spotify_client is None


@pytest.mark.parametrize("data, expected", [
    ({}, False),
    ({"client_id": "a", "client_secret": "b"}, True),
    ({"client_id": "a"}, False),
    ({"refresh_token": "r"}, True),
    ({"access_token": "t"}, True),
])
def test_should_auto_initialize(monkeypatch, data, expected):
    _set_client(monkeypatch, FakeClient(**data))
    assert sp.should_auto_initialize() is expected


def test_should_auto_initialize_without_client_and_error(monkeypatch):
    assert sp.should_auto_initialize() is False
    _set_client(monkeypatch, SimpleNamespace())  # no spotify_data -> error path
    assert sp.should_auto_initialize() is False


# --------------------------------------------------------------------------- startup auth retry


def test_authenticate_with_retry_succeeds_after_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: sleeps.append(s))
    c = FakeClient()
    c.auth_results = [False, True]
    assert sp._authenticate_with_retry(c) is True
    assert sleeps == [1.0]
    assert c.calls == [("reload", True), ("authenticate",), ("reload", True), ("authenticate",)]


def test_authenticate_with_retry_all_fail(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: sleeps.append(s))
    c = FakeClient()
    assert sp._authenticate_with_retry(c, delays=(0.0, 0.5, 2.0)) is False
    assert sleeps == [0.5, 2.0]
    assert c.calls.count(("authenticate",)) == 3


def test_authenticate_with_retry_cooldown_paths(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: pytest.fail("should not sleep"))
    c = FakeClient()
    c.cooldown = True
    assert sp._authenticate_with_retry(c) is False
    assert c.calls == []

    # cooldown begins after the first failure
    c = FakeClient()
    seq = iter([False, False, True])
    c.cooldown = lambda: next(seq)
    assert sp._authenticate_with_retry(c) is False
    assert c.calls == [("reload", True), ("authenticate",)]

    # cooldown appears just before a later attempt
    c = FakeClient()
    seq = iter([False, False, False, True])
    c.cooldown = lambda: next(seq)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    assert sp._authenticate_with_retry(c) is False
    assert c.calls.count(("authenticate",)) == 1


# --------------------------------------------------------------------------- service lifecycle


def test_start_service_impl_starts_monitor(monkeypatch):
    c = FakeClient(client_id="a", client_secret="b")
    monkeypatch.setattr(sp, "initialize_spotify", lambda: _set_client(monkeypatch, c))
    retried = []
    monkeypatch.setattr(sp, "_authenticate_with_retry", lambda client: retried.append(client) or True)
    sp._start_spotify_service_impl()
    try:
        assert c.monitor_event.wait(2)
        assert retried == [c]
        assert sp.is_running is True
        assert sp.spotify_thread.name == "SpotifyMonitor" and sp.spotify_thread.daemon
    finally:
        sp.spotify_thread.join(2)


def test_start_service_impl_without_credentials_skips_auth(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(sp, "initialize_spotify", lambda: c)
    monkeypatch.setattr(sp, "_authenticate_with_retry", lambda client: pytest.fail("no auth expected"))
    sp._start_spotify_service_impl()
    sp.spotify_thread.join(2)
    assert c.monitor_event.is_set()


def test_start_service_impl_no_client(monkeypatch):
    monkeypatch.setattr(sp, "initialize_spotify", lambda: None)
    sp._start_spotify_service_impl()
    assert sp.is_running is False and sp.spotify_thread is None


def test_start_service_impl_error_resets_running(monkeypatch):
    c = FakeClient(client_id="a", client_secret="b")
    monkeypatch.setattr(sp, "initialize_spotify", lambda: _set_client(monkeypatch, c))

    def boom(client):
        raise RuntimeError("auth exploded")

    monkeypatch.setattr(sp, "_authenticate_with_retry", boom)
    sp._start_spotify_service_impl()
    assert sp.is_running is False and sp.spotify_thread is None


class _Thread:
    def __init__(self, alive):
        self.alive = alive
        self.joined = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


def test_start_spotify_service_spawns_thread(monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(sp, "_start_spotify_service_impl", ran.set)
    monkeypatch.setattr(sp, "is_running", True)
    assert sp.start_spotify_service() is True
    assert ran.wait(2)
    sp._spotify_start_thread.join(2)
    assert sp._spotify_start_thread.name == "SpotifyServiceStart"
    assert sp.is_running is False  # stale flag reset before the new start


def test_start_spotify_service_already_running_or_starting(monkeypatch):
    monkeypatch.setattr(sp, "_start_spotify_service_impl", lambda: pytest.fail("should not start"))
    monkeypatch.setattr(sp, "spotify_thread", _Thread(True))
    assert sp.start_spotify_service() is False

    monkeypatch.setattr(sp, "spotify_thread", _Thread(False))
    monkeypatch.setattr(sp, "_spotify_start_thread", _Thread(True))
    assert sp.start_spotify_service() is True


def test_stop_spotify_service(monkeypatch):
    sp.stop_spotify_service()  # not running: just a warning

    c = _set_client(monkeypatch, FakeClient())
    t = _Thread(True)
    monkeypatch.setattr(sp, "spotify_thread", t)
    monkeypatch.setattr(sp, "is_running", True)
    sp.stop_spotify_service(join_timeout=0.25)
    assert ("stop_monitoring",) in c.calls
    assert sp.is_running is False and t.joined == [0.25]


def test_stop_spotify_service_without_client_or_thread(monkeypatch):
    monkeypatch.setattr(sp, "is_running", True)
    monkeypatch.setattr(sp, "spotify_thread", _Thread(False))
    sp.stop_spotify_service()
    assert sp.is_running is False


def test_stop_spotify_service_error(monkeypatch):
    bad = SimpleNamespace(stop_monitoring=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    _set_client(monkeypatch, bad)
    monkeypatch.setattr(sp, "is_running", True)
    sp.stop_spotify_service()  # logged
    assert sp.is_running is True  # failed before the flag was cleared


# --------------------------------------------------------------------------- status / triggers


def test_get_spotify_status(monkeypatch):
    from modules import connection_status_tracker as cst

    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: None)
    s = sp.get_spotify_status()
    assert s == {"status": "Not Initialized", "is_authenticated": False, "current_track": "N/A"}

    c = _set_client(monkeypatch, FakeClient(connection_status="Connected", artist_name="A", track_name="T"))
    c.is_authenticated = True
    assert sp.get_spotify_status() == {"status": "Connected", "is_authenticated": True, "current_track": "A - T"}

    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "Offline" if key == "spotify" else None)
    assert sp.get_spotify_status() == {"status": "Offline", "is_authenticated": False, "current_track": "A - T"}


def test_trigger_oauth_flow(monkeypatch):
    monkeypatch.setattr(sp, "initialize_spotify", lambda: None)
    assert sp.trigger_oauth_flow() == ""
    assert sp.trigger_automatic_oauth_flow() is False
    assert sp.trigger_authentication() is False

    c = FakeClient()
    monkeypatch.setattr(sp, "initialize_spotify", lambda: _set_client(monkeypatch, c))
    assert sp.trigger_oauth_flow() == "https://auth"
    assert sp.trigger_automatic_oauth_flow() is True
    c.auth_results = [True]
    assert sp.trigger_authentication() is True
    assert [x[0] for x in c.calls] == ["start_oauth_flow", "start_oauth_flow_with_server", "authenticate"]


def test_complete_oauth_with_code(monkeypatch):
    assert sp.complete_oauth_with_code("good") is False
    c = _set_client(monkeypatch, FakeClient())
    assert sp.complete_oauth_with_code("good") is True
    assert sp.complete_oauth_with_code("bad") is False
    assert ("complete", "bad") in c.calls


def test_update_spotify_settings(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(sp, "initialize_spotify", lambda: _set_client(monkeypatch, c))
    c.auth_results = [True]
    sp.update_spotify_settings(client_id="id", client_secret="sec", market_country="US")
    assert c.spotify_data.client_id == "id" and c.spotify_data.market_country == "US"
    assert ("sync", True) in c.calls and ("authenticate",) in c.calls

    c.calls.clear()
    c.is_authenticated = True
    sp.update_spotify_settings(market_country="DE")
    assert c.calls == [("update_field", "market_country", "DE"), ("sync", True)]

    c.calls.clear()
    c.is_authenticated = False
    c.spotify_data = SpotifyData()
    sp.update_spotify_settings()
    assert c.calls == [("sync", True)]  # nothing configured -> no authenticate


def test_update_spotify_settings_no_client(monkeypatch):
    monkeypatch.setattr(sp, "initialize_spotify", lambda: None)
    assert sp.update_spotify_settings(client_id="x") is None


def test_test_and_reconnect(monkeypatch, caplog):
    assert sp.test_and_reconnect() is False
    c = _set_client(monkeypatch, FakeClient())
    c.auth_results = [True, False]
    monkeypatch.setattr(sp, "diagnose_token_storage", lambda: {
        "current_tokens_found": False, "potential_locations": ["SQLite (old.db)"]})
    with caplog.at_level("INFO", logger=sp.logger.name):
        assert sp.test_and_reconnect() is True
    assert any("SQLite (old.db)" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(sp, "diagnose_token_storage", lambda: {
        "current_tokens_found": True, "potential_locations": []})
    assert sp.test_and_reconnect() is False


# --------------------------------------------------------------------------- diagnose_token_storage


class _DiagState:
    def __init__(self, db_type="sql", data=None, settings_error=False, data_error=False):
        self.db_type = db_type
        self.data = data
        self.settings_error = settings_error
        self.data_error = data_error

    def get_database_settings(self):
        if self.settings_error:
            raise RuntimeError("no settings")
        return SimpleNamespace(database_type=self.db_type) if self.db_type else None

    def get_spotify_data(self):
        if self.data_error:
            raise RuntimeError("db gone")
        return self.data


def _diag_state(monkeypatch, st):
    from modules import dataobjects

    monkeypatch.setattr(dataobjects, "state_manager", st)


def test_diagnose_token_storage_finds_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ServiceAccountKey.json").write_text("{}")
    (tmp_path / "old.db").write_text("")
    (tmp_path / "notes.txt").write_text("")
    _diag_state(monkeypatch, _DiagState("firebase", SpotifyData(refresh_token="r")))
    d = sp.diagnose_token_storage()
    assert d == {
        "current_database_type": "firebase",
        "current_tokens_found": True,
        "potential_locations": ["Firebase (ServiceAccountKey.json found)", "SQLite (old.db)"],
    }


def test_diagnose_token_storage_no_tokens_no_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _diag_state(monkeypatch, _DiagState(None, SpotifyData()))
    d = sp.diagnose_token_storage()
    assert d == {"current_database_type": "Unknown", "current_tokens_found": False, "potential_locations": []}

    _diag_state(monkeypatch, _DiagState(settings_error=True, data=None))
    assert sp.diagnose_token_storage()["current_database_type"] == "Unknown"


def test_diagnose_token_storage_listdir_error(monkeypatch, tmp_path):
    import os

    monkeypatch.chdir(tmp_path)
    _diag_state(monkeypatch, _DiagState("sql", SpotifyData(access_token="a")))

    def boom(path):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "listdir", boom)
    d = sp.diagnose_token_storage()
    assert d["current_tokens_found"] is True and d["potential_locations"] == []


def test_diagnose_token_storage_state_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _diag_state(monkeypatch, _DiagState("sql", data_error=True))
    d = sp.diagnose_token_storage()
    assert d == {"current_database_type": "sql", "current_tokens_found": False, "potential_locations": []}


# --------------------------------------------------------------------------- reconnect helpers


def test_spotify_configured_for_monitor(monkeypatch):
    from modules import connection_status_tracker as cst

    monkeypatch.setattr(cst, "spotify_configured", lambda: True)
    assert sp.spotify_configured_for_monitor() is True
    monkeypatch.setattr(cst, "spotify_configured", lambda: False)
    assert sp.spotify_configured_for_monitor() is False


def test_spotify_has_stored_tokens(monkeypatch):
    assert sp.spotify_has_stored_tokens() is False
    c = _set_client(monkeypatch, FakeClient(access_token="  ", refresh_token=""))
    assert sp.spotify_has_stored_tokens() is False
    c.spotify_data.refresh_token = "r"
    assert sp.spotify_has_stored_tokens() is True


@pytest.fixture
def net(monkeypatch):
    from modules import connection_monitor as cm

    state = {"internet": True, "service": True, "error": None}

    def internet():
        if state["error"]:
            raise state["error"]
        return state["internet"]

    monkeypatch.setattr(cm, "is_internet_available", internet)
    monkeypatch.setattr(cm, "is_service_reachable", lambda name: state["service"] if name == "spotify" else None)
    return state


@pytest.fixture
def recovery(monkeypatch):
    from modules import web_engine

    calls = []
    monkeypatch.setattr(web_engine, "broadcast_overlay_recovery", lambda *a: calls.append(a), raising=False)
    return calls


def test_attempt_auto_reconnect_success(monkeypatch, net, recovery):
    c = _set_client(monkeypatch, FakeClient(refresh_token="r"))
    c.auth_results = [True]
    assert sp.attempt_auto_reconnect() is True
    assert recovery == [("spotify", "reconnected")]


def test_attempt_auto_reconnect_auth_fails(monkeypatch, net, recovery):
    _set_client(monkeypatch, FakeClient(refresh_token="r"))
    assert sp.attempt_auto_reconnect() is False
    assert recovery == []


def test_attempt_auto_reconnect_guards(monkeypatch, net, recovery):
    assert sp.attempt_auto_reconnect() is False  # no client
    c = _set_client(monkeypatch, FakeClient(refresh_token="r"))
    c.cooldown = True
    assert sp.attempt_auto_reconnect() is False
    c.cooldown = False
    c.backoff = True
    assert sp.attempt_auto_reconnect() is False
    c.backoff = False
    c.spotify_data.refresh_token = ""
    assert sp.attempt_auto_reconnect() is False
    c.spotify_data.refresh_token = "r"
    net["internet"] = False
    assert sp.attempt_auto_reconnect() is False
    net["internet"] = True
    net["service"] = False
    assert sp.attempt_auto_reconnect() is False
    net["service"] = True
    net["error"] = RuntimeError("monitor broken")
    assert sp.attempt_auto_reconnect() is False
    assert ("authenticate",) not in c.calls


def test_attempt_auto_reconnect_broadcast_error_ignored(monkeypatch, net):
    from modules import web_engine

    def boom(*a):
        raise RuntimeError("socket down")

    monkeypatch.setattr(web_engine, "broadcast_overlay_recovery", boom, raising=False)
    c = _set_client(monkeypatch, FakeClient(access_token="a"))
    c.auth_results = [True]
    assert sp.attempt_auto_reconnect() is True

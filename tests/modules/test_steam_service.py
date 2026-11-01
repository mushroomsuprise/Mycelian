# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/steam_service.py with a fake SteamClient and state manager."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import steam_service as svc  # noqa: E402
from modules.steam_data import SteamData  # noqa: E402
from modules.steamapi import SteamAPIError, SteamConnectionError, SteamKeyError  # noqa: E402


class FakeState:
    def __init__(self, key="k" * 32, profile="me"):
        self.settings = SimpleNamespace(api_key=key, steam_profile=profile)
        self.live = None

    def get_steam_settings_data(self):
        return self.settings

    def get_live_steam_data(self):
        return self.live

    def set_live_steam_data(self, data):
        self.live = data
        return True


class FakeClient:
    instances = []

    def __init__(self, key="", profile=""):
        self.api_key, self.profile = key, profile
        self.connected = False
        self.steam_data = SteamData()
        self.connect_result = True
        self.summary_result = True
        self.summary_error = None
        self.achievements = []
        self.achievement_error = None
        self.level_error = None
        self.calls = []
        FakeClient.instances.append(self)

    def connect(self):
        self.calls.append("connect")
        self.connected = self.connect_result
        self.steam_data.connection_status = "Connected" if self.connected else "Invalid API Key"
        return self.connected

    def is_connected(self):
        return self.connected

    def update_credentials(self, key, profile):
        self.calls.append(("creds", key, profile))
        self.api_key, self.profile = key, profile
        self.connected = False

    def refresh_summary(self):
        self.calls.append("summary")
        if self.summary_error:
            raise self.summary_error
        return self.summary_result

    def refresh_level(self):
        self.calls.append("level")
        if self.level_error:
            raise self.level_error

    def refresh_achievements(self):
        self.calls.append("achievements")
        if self.achievement_error:
            raise self.achievement_error
        return list(self.achievements)


@pytest.fixture
def env(monkeypatch):
    state = FakeState()
    emitted = []
    FakeClient.instances = []
    monkeypatch.setattr(svc, "state_manager", state)
    monkeypatch.setattr(svc, "SteamClient", FakeClient)
    monkeypatch.setattr(svc, "_emit", lambda event, payload: emitted.append((event, payload)))
    monkeypatch.setattr(svc, "steam_client_instance", None)
    monkeypatch.setattr(svc, "steam_update_thread", None)
    started = []
    monkeypatch.setattr(svc, "start_steam_data_updater_thread", lambda: started.append(1))
    svc.stop_steam_thread_event.clear()
    yield SimpleNamespace(state=state, emitted=emitted, started=started)
    svc.stop_steam_thread_event.clear()


def test_init_without_a_key_publishes_not_connected(env):
    env.state.settings.api_key = ""
    svc._initialize_steam_module_impl()
    assert svc.steam_client_instance is None and env.state.live.connection_status == "Not Connected"


def test_init_connects_broadcasts_and_starts_polling(env):
    svc._initialize_steam_module_impl()
    client = svc.steam_client_instance
    assert client.calls == ["connect"] and env.started == [1]
    assert env.emitted[-1][0] == "steam_data_update" and env.emitted[-1][1]["connection_status"] == "Connected"
    assert env.state.live is client.steam_data


def test_initialize_runs_in_a_thread(env, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(svc, "_initialize_steam_module_impl", ran.set)
    svc.initialize_steam_module()
    assert ran.wait(2)


def test_poll_once_cadence_and_unlock_events(env, monkeypatch):
    client = FakeClient("k", "me")
    client.connected = True
    clock = [1000.0]
    monkeypatch.setattr(svc.time, "time", lambda: clock[0])

    def summary():
        client.calls.append("summary")
        client.steam_data.current_game_appid = 620
        return True

    client.refresh_summary = summary
    client.achievements = [{"name": "First"}]
    state = {}
    svc._poll_once(client, state)
    assert client.calls == ["summary", "level", "achievements"]
    assert env.emitted == [("steam_achievement_unlocked", {"name": "First"})]
    client.calls.clear()
    clock[0] += 5
    svc._poll_once(client, state)
    assert client.calls == ["summary"]  # level and achievements not due yet
    clock[0] += svc.ACHIEVEMENTS_SEC
    svc._poll_once(client, state)
    assert client.calls[-1] == "achievements"


def test_poll_once_errors(env):
    client = FakeClient("k", "me")
    client.connected = True
    client.summary_result = False
    svc._poll_once(client, {})
    assert client.connected is False and client.steam_data.connection_status == "Profile Not Found"

    client = FakeClient("k", "me")
    client.steam_data.current_game_appid = 1
    client.level_error = SteamAPIError("no level")
    client.achievement_error = SteamAPIError("no stats")
    svc._poll_once(client, {"achievements_at": 0})  # both swallowed
    client.achievement_error = SteamKeyError("bad key")
    with pytest.raises(SteamKeyError):
        svc._poll_once(client, {})


def run_loop_once(monkeypatch):
    monkeypatch.setattr(svc.stop_steam_thread_event, "wait", lambda _t: svc.stop_steam_thread_event.set())
    svc.steam_data_update_loop()
    svc.stop_steam_thread_event.clear()


def test_loop_polls_reconnects_and_handles_errors(env, monkeypatch):
    client = FakeClient("k", "me")
    svc.steam_client_instance = client
    client.connected = True
    run_loop_once(monkeypatch)
    assert "summary" in client.calls and env.emitted[-1][0] == "steam_data_update"

    client.summary_error = SteamKeyError("refused")
    run_loop_once(monkeypatch)
    assert client.connected is False and client.steam_data.connection_status == "Invalid API Key"
    client.calls.clear()
    run_loop_once(monkeypatch)
    assert client.calls == []  # needs new settings: no reconnect attempt

    client.steam_data.connection_status = "Disconnected"
    reconnects = []
    monkeypatch.setattr(svc, "attempt_auto_reconnect", lambda: reconnects.append(1) or False)
    run_loop_once(monkeypatch)
    assert reconnects == [1]

    client.connected = True
    client.summary_error = SteamConnectionError("down")
    run_loop_once(monkeypatch)
    assert client.steam_data.connection_status == "Disconnected" and client.connected is False

    client.connected = True
    client.summary_error = RuntimeError("weird")
    run_loop_once(monkeypatch)  # logged, not raised

    svc.steam_client_instance = None
    svc.steam_data_update_loop()  # nothing to poll: returns at once


def test_thread_start_and_stop(monkeypatch):
    monkeypatch.setattr(svc, "steam_update_thread", None)
    monkeypatch.setattr(svc, "steam_client_instance", None)
    svc.start_steam_data_updater_thread()
    assert svc.steam_update_thread is None
    gate = threading.Event()
    monkeypatch.setattr(svc, "steam_data_update_loop", lambda: gate.wait(2))
    monkeypatch.setattr(svc, "steam_client_instance", FakeClient("k", "me"))
    svc.start_steam_data_updater_thread()
    first = svc.steam_update_thread
    svc.start_steam_data_updater_thread()
    assert svc.steam_update_thread is first and first.is_alive()
    gate.set()
    svc.stop_steam_data_updater_thread(join_timeout=2)
    assert svc.steam_update_thread is None
    svc.stop_steam_data_updater_thread()

    stuck = threading.Event()
    monkeypatch.setattr(svc, "steam_data_update_loop", lambda: stuck.wait(5))
    svc.start_steam_data_updater_thread()
    svc.stop_steam_data_updater_thread(join_timeout=0.01)
    assert svc.steam_update_thread is not None  # still running: kept
    stuck.set()
    svc.steam_update_thread.join(2)
    svc.stop_steam_thread_event.clear()


def test_settings_change(env, monkeypatch):
    stops = []
    monkeypatch.setattr(svc, "stop_steam_data_updater_thread", lambda **k: stops.append(1))
    svc.handle_steam_settings_change()  # no client yet: creates one
    client = svc.steam_client_instance
    assert client.calls == ["connect"] and env.started == [1]
    svc.handle_steam_settings_change()  # unchanged and connected: just keep polling
    assert client.calls == ["connect"] and env.started == [1, 1]
    env.state.settings.steam_profile = "other"
    svc.handle_steam_settings_change()
    assert client.calls[-2:] == [("creds", "k" * 32, "other"), "connect"] and svc.steam_client_instance is client
    env.state.settings.api_key = ""
    svc.handle_steam_settings_change()
    assert svc.steam_client_instance is None and env.state.live.connection_status == "Not Connected"
    assert env.emitted[-1][1]["connection_status"] == "Not Connected"


def test_status_label_and_display(env, monkeypatch):
    import modules.connection_status_tracker as cst

    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: None)
    env.state.settings.api_key = ""
    assert svc.steam_configured() is False and svc.get_steam_status_label() == "Not Connected"
    assert svc.get_steam_status_display() == ("Not Connected", "", "text-theme-error")
    env.state.settings.api_key = "k"
    assert svc.get_steam_status_label() == "Disconnected"
    env.state.live = SteamData(connection_status="Offline", persona_name="Mushy")
    assert svc.get_steam_status_display() == ("Offline", "Mushy", "text-theme-warning")
    env.state.live = SteamData(connection_status="Profile Not Set")
    assert svc.get_steam_status_display() == ("Profile Not Set", "", "text-theme-warning")
    env.state.live = SteamData(connection_status="Offline", persona_name="Mushy")
    client = FakeClient("k", "me")
    client.steam_data.connection_status = "Connected"
    svc.steam_client_instance = client
    assert svc.get_steam_status_display() == ("Connected", "Mushy", "text-theme-success")
    client.steam_data.connection_status = ""
    assert svc.get_steam_status_label() == "Disconnected"
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "No Internet")
    assert svc.get_steam_status_display() == ("No Internet", "", "text-theme-error")
    env.state.get_steam_settings_data = lambda: (_ for _ in ()).throw(RuntimeError())
    assert svc.steam_configured() is False and svc.steam_configured_for_monitor() is False


def test_monitor_hooks(env, monkeypatch):
    import modules.connection_monitor as cm

    assert svc.get_steam_client() is None and svc.is_steam_api_disconnected() is False
    assert svc.attempt_auto_reconnect() is False
    client = FakeClient("k", "me")
    svc.steam_client_instance = client
    assert svc.is_steam_api_disconnected() is True
    monkeypatch.setattr(cm, "is_internet_available", lambda: False)
    monkeypatch.setattr(cm, "is_service_reachable", lambda key: True)
    assert svc.attempt_auto_reconnect() is False
    monkeypatch.setattr(cm, "is_internet_available", lambda: True)
    assert svc.attempt_auto_reconnect() is True and env.started == [1]
    client.connect_result = False
    assert svc.attempt_auto_reconnect() is False
    monkeypatch.setattr(cm, "is_internet_available", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert svc.attempt_auto_reconnect() is False


def test_emit_uses_the_web_engine(monkeypatch):
    import modules.web_engine as we

    sent = []
    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, p: sent.append(e)))
    svc._emit("steam_data_update", {})
    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, p: 1 / 0))
    svc._emit("x", {})  # logged, not raised
    monkeypatch.setattr(we, "web_engine_instance", None)
    svc._emit("y", {})
    assert sent == ["steam_data_update"]


def test_update_and_broadcast_without_a_client(env):
    svc._update_and_broadcast()
    assert env.emitted == []

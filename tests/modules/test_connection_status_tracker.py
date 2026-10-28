# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.connection_status_tracker`` (live integration status labels). Every service is faked."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types
from types import SimpleNamespace

import pytest

import modules
from modules import connection_monitor as cm
from modules import connection_status_tracker as cst


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


def fake_state(monkeypatch, **getters):
    sm = SimpleNamespace(**{k: (lambda v=v: v) for k, v in getters.items()})
    fake_module(monkeypatch, "dataobjects", state_manager=sm)
    return sm


def boom(*a, **k):
    raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(cst, "_last_probe_mono", {})
    monkeypatch.setattr(cst, "_ui_last_overlay", {})
    monkeypatch.setattr(cst, "_obs_process_cache", [0.0, False])


# --------------------------------------------------------------------------- *_configured

@pytest.mark.parametrize("creds,expected", [
    ({"client_id": "a", "client_secret": "b"}, True),
    ({"client_id": " ", "client_secret": "b"}, False),
    ({"client_id": "a", "client_secret": None}, False),
    ({}, False),
])
@pytest.mark.parametrize("which", ["twitch", "spotify"])
def test_twitch_spotify_configured(monkeypatch, creds, expected, which):
    mgr = SimpleNamespace(get_twitch_credentials=lambda: creds, get_spotify_credentials=lambda: creds)
    fake_module(monkeypatch, "api_credentials_manager", api_credentials_manager=mgr)
    fn = cst.twitch_configured if which == "twitch" else cst.spotify_configured
    assert fn() is expected


@pytest.mark.parametrize("which", ["twitch", "spotify"])
def test_credentials_error_means_unconfigured(monkeypatch, which):
    mgr = SimpleNamespace(get_twitch_credentials=boom, get_spotify_credentials=boom)
    fake_module(monkeypatch, "api_credentials_manager", api_credentials_manager=mgr)
    fn = cst.twitch_configured if which == "twitch" else cst.spotify_configured
    assert fn() is False


@pytest.mark.parametrize("data,expected", [(None, False), (SimpleNamespace(api_key=" "), False),
                                           (SimpleNamespace(api_key="k"), True), (SimpleNamespace(), False)])
def test_youtube_configured(monkeypatch, data, expected):
    fake_state(monkeypatch, get_youtube_data=data)
    assert cst.youtube_configured() is expected


@pytest.mark.parametrize("live,settings,expected", [
    (None, None, False),
    (None, SimpleNamespace(npsso_code=" abc "), True),
    (SimpleNamespace(npsso_code="xyz"), SimpleNamespace(npsso_code=""), True),
    (SimpleNamespace(npsso_code=None), SimpleNamespace(npsso_code=None), False),
])
def test_psn_configured(monkeypatch, live, settings, expected):
    fake_state(monkeypatch, get_live_psn_data=live, get_psn_settings_data=settings)
    assert cst.psn_configured() is expected


@pytest.mark.parametrize("data,expected", [(None, False), (SimpleNamespace(), True),
                                           (SimpleNamespace(enabled=False), False)])
def test_obs_configured(monkeypatch, data, expected):
    fake_state(monkeypatch, get_obs_data=data)
    assert cst.obs_configured() is expected


@pytest.mark.parametrize("data,expected", [(None, False), (SimpleNamespace(bot_token="t"), True),
                                           (SimpleNamespace(bot_token=None), False)])
def test_discord_configured(monkeypatch, data, expected):
    fake_state(monkeypatch, get_discord_data=data)
    assert cst.discord_configured() is expected


@pytest.mark.parametrize("fn", ["youtube_configured", "psn_configured", "obs_configured", "discord_configured",
                                "_obs_host_is_local"])
def test_state_errors_mean_false(monkeypatch, fn):
    sm = SimpleNamespace(get_youtube_data=boom, get_live_psn_data=boom, get_psn_settings_data=boom,
                         get_obs_data=boom, get_discord_data=boom)
    fake_module(monkeypatch, "dataobjects", state_manager=sm)
    assert getattr(cst, fn)() is False


@pytest.mark.parametrize("name", ["streamlabs", "streamelements"])
def test_donation_services_configured(monkeypatch, name):
    fake_module(monkeypatch, name, **{f"{name}_configured": lambda: True})
    assert getattr(cst, f"{name}_configured")() is True
    fake_module(monkeypatch, name, **{f"{name}_configured": boom})
    assert getattr(cst, f"{name}_configured")() is False


@pytest.mark.parametrize("key", ["twitch", "spotify", "youtube", "psn", "obs", "discord", "streamlabs", "streamelements"])
def test_service_configured_dispatch(monkeypatch, key):
    fn = {"twitch": "twitch_configured", "spotify": "spotify_configured"}.get(key, f"{key}_configured")
    monkeypatch.setattr(cst, fn, lambda: "sentinel")
    assert cst.service_configured(key) == "sentinel"


def test_service_configured_webengine_and_unknown():
    assert cst.service_configured("webengine") is True
    assert cst.service_configured("nope") is False


def test_internal_configured_twitch_always_true(monkeypatch):
    monkeypatch.setattr(cst, "service_configured", lambda key: False)
    assert cst._configured("twitch") is True
    assert cst._configured("spotify") is False


# --------------------------------------------------------------------------- OBS local / process

@pytest.mark.parametrize("host,expected", [("localhost", True), ("", True), ("127.0.0.1", True), (" LOCALHOST ", True),
                                           ("192.168.1.5", False), (None, True)])
def test_obs_host_is_local(monkeypatch, host, expected):
    fake_state(monkeypatch, get_obs_data=SimpleNamespace(host=host))
    assert cst._obs_host_is_local() is expected


def test_local_obs_running_is_cached(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(cst.time, "monotonic", lambda: t[0])
    calls = []
    fake_module(monkeypatch, "obs_browser_docks", obs_process_is_running=lambda: calls.append(1) or False)
    assert cst._local_obs_running() is False
    assert cst._local_obs_running() is False
    assert calls == [1]
    t[0] += cst._OBS_PROCESS_TTL_SEC
    fake_module(monkeypatch, "obs_browser_docks", obs_process_is_running=boom)
    assert cst._local_obs_running() is True  # unknown -> assume running
    assert cst._obs_process_cache == [t[0], True]


@pytest.mark.parametrize("local,running,expected", [(False, True, "Disconnected"), (True, True, "Can't connect"),
                                                     (True, False, "Not running")])
def test_obs_disconnected_status(monkeypatch, local, running, expected):
    monkeypatch.setattr(cst, "_obs_host_is_local", lambda: local)
    monkeypatch.setattr(cst, "_local_obs_running", lambda: running)
    assert cst.obs_disconnected_status() == expected


# --------------------------------------------------------------------------- probe scheduling

def test_should_probe_rate_limits(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(cst.time, "monotonic", lambda: t[0])
    assert cst._should_probe("spotify") is True
    assert cst._should_probe("spotify") is False
    assert cst._should_probe("spotify", force=True) is True
    t[0] += cst._PROBE_INTERVAL_SEC["spotify"]
    assert cst._should_probe("spotify") is True
    assert cst._should_probe("unlisted") is True
    t[0] += 9.9
    assert cst._should_probe("unlisted") is False


def test_probe_configured_services(monkeypatch):
    seen = []
    monkeypatch.setattr(cst, "_configured", lambda key: key in ("twitch", "spotify", "webengine", "obs"))
    monkeypatch.setattr(cst, "_should_probe", lambda key, force=False: key != "obs")

    def status(key):
        seen.append(key)
        if key == "webengine":
            raise RuntimeError("x")
        return "ok"

    monkeypatch.setattr(cst, "get_connection_status", status)
    cst.probe_configured_services(force=True)
    assert seen == ["internet", "twitch", "spotify", "webengine"]


def test_probe_configured_services_skips_internet_when_rate_limited(monkeypatch):
    seen = []
    monkeypatch.setattr(cst, "_configured", lambda key: False)
    monkeypatch.setattr(cst, "_should_probe", lambda key, force=False: False)
    monkeypatch.setattr(cst, "get_connection_status", seen.append)
    cst.probe_configured_services()
    assert seen == []


# --------------------------------------------------------------------------- overlays

class FakeCM:
    def __init__(self, monkeypatch, inet="Online", reachable=True, pending=False, ui_loop=False):
        self.inet, self.reachable, self.pending = inet, reachable, pending
        self.queued, self.calls = [], []
        monkeypatch.setattr(cm, "is_internet_available", lambda *a, **k: self.calls.append("avail") or True)
        monkeypatch.setattr(cm, "is_background_refresh_pending", lambda key: self.pending)
        monkeypatch.setattr(cm, "request_background_refresh", self.queued.append)
        monkeypatch.setattr(cm, "get_internet_status", lambda: self.inet)
        monkeypatch.setattr(cm, "peek_service_reachable", lambda key: self.reachable)
        monkeypatch.setattr(cm, "is_service_reachable", lambda key: self.reachable)
        monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: ui_loop)


@pytest.mark.parametrize("inet,reachable,expected", [
    ("Offline", True, "No Internet"), ("Checking", True, "Checking Internet"),
    ("Online", True, None), ("Online", False, "Service Unreachable"),
])
def test_ui_loop_overlay(monkeypatch, inet, reachable, expected):
    f = FakeCM(monkeypatch, inet=inet, reachable=reachable)
    assert cst._ui_loop_connectivity_overlay("twitch") == expected
    assert cst._ui_last_overlay["twitch"] == expected
    assert f.queued == []


def test_ui_loop_overlay_keeps_last_while_pending(monkeypatch):
    f = FakeCM(monkeypatch, reachable=None, pending=True)
    cst._ui_last_overlay["psn"] = "Service Unreachable"
    assert cst._ui_loop_connectivity_overlay("psn") == "Service Unreachable"
    assert f.queued == ["psn"]
    assert cst._ui_loop_connectivity_overlay("spotify") is None


def test_get_connectivity_overlay_non_remote():
    assert cst.get_connectivity_overlay("obs") is None


def test_get_connectivity_overlay_on_ui_loop_delegates(monkeypatch):
    FakeCM(monkeypatch, ui_loop=True)
    monkeypatch.setattr(cst, "_ui_loop_connectivity_overlay", lambda key: f"ui:{key}")
    assert cst.get_connectivity_overlay("twitch") == "ui:twitch"


@pytest.mark.parametrize("inet,reachable,expected", [
    ("Offline", True, "No Internet"), ("Checking", True, "Checking Internet"),
    ("Online", False, "Service Unreachable"), ("Online", True, None),
])
def test_get_connectivity_overlay_blocking(monkeypatch, inet, reachable, expected):
    f = FakeCM(monkeypatch, inet=inet, reachable=reachable)
    assert cst.get_connectivity_overlay("spotify") == expected
    assert f.calls == ["avail"]


def test_get_connectivity_overlay_errors_mean_none(monkeypatch):
    FakeCM(monkeypatch)
    monkeypatch.setattr(cm, "get_internet_status", boom)
    assert cst.get_connectivity_overlay("youtube") is None


def test_apply_connectivity_overlay(monkeypatch):
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: None)
    assert cst.apply_connectivity_overlay("twitch", "Connected") == "Connected"
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "No Internet")
    assert cst.apply_connectivity_overlay("twitch", "Connected") == "No Internet"


def test_apply_connectivity_overlay_to_info(monkeypatch):
    info = {"status": "Connected", "is_valid": True, "x": 1}
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: None)
    assert cst.apply_connectivity_overlay_to_info("twitch", info) is info
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "No Internet")
    out = cst.apply_connectivity_overlay_to_info("twitch", info)
    assert out == {"status": "No Internet", "is_valid": False, "x": 1} and info["status"] == "Connected"
    out = cst.apply_connectivity_overlay_to_info("twitch", {"state": "x"}, status_field="state", valid_field=None)
    assert out == {"state": "No Internet"}
    out = cst.apply_connectivity_overlay_to_info("twitch", {"status": "x"})
    assert "is_valid" not in out


# --------------------------------------------------------------------------- get_connection_status

@pytest.fixture
def no_overlay(monkeypatch):
    monkeypatch.setattr(cst, "apply_connectivity_overlay", lambda key, status: status)


def test_status_internet(monkeypatch):
    f = FakeCM(monkeypatch, inet="Checking")
    assert cst.get_connection_status("internet") == "Checking"
    assert f.calls == ["avail"]


@pytest.mark.parametrize("status,expected", [(" Connected ", "Connected"), (None, "Unknown")])
def test_status_twitch(monkeypatch, no_overlay, status, expected):
    fake_module(monkeypatch, "twitch", get_twitch_connection_status=lambda: {"status": status})
    assert cst.get_connection_status("twitch") == expected


@pytest.mark.parametrize("live,stored,expected", [
    ("Connected", None, "Connected"),
    ("Not Initialized", SimpleNamespace(connection_status="Token Expired"), "Token Expired"),
    ("", SimpleNamespace(connection_status=""), "Unknown"),
    (None, None, "Unknown"),
])
def test_status_spotify(monkeypatch, no_overlay, live, stored, expected):
    fake_module(monkeypatch, "spotify", get_spotify_status=lambda: {"status": live})
    fake_state(monkeypatch, get_spotify_data=stored)
    assert cst.get_connection_status("spotify") == expected


@pytest.mark.parametrize("live,stored,expected", [
    ("Live", None, "Live"),
    ("Not Initialized", SimpleNamespace(connection_status="Quota"), "Quota"),
    ("", SimpleNamespace(connection_status=None), "Unknown"),
    ("", None, "Unknown"),
])
def test_status_youtube(monkeypatch, no_overlay, live, stored, expected):
    fake_module(monkeypatch, "youtube", get_youtube_status=lambda: {"status": live})
    fake_state(monkeypatch, get_youtube_data=stored)
    assert cst.get_connection_status("youtube") == expected


def test_status_psn(monkeypatch, no_overlay):
    fake_module(monkeypatch, "psn_service", get_psn_status_label=lambda: "Online")
    assert cst.get_connection_status("psn") == "Online"
    fake_module(monkeypatch, "psn_service", get_psn_status_label=lambda: None)
    assert cst.get_connection_status("psn") == "Unknown"


@pytest.mark.parametrize("phase,auth,expected", [
    ("connected", False, "Connected"), ("connecting", False, "Connecting"),
    ("disconnecting", False, "Disconnecting"), ("idle", True, "Auth Failed"), ("idle", False, "Not running"),
])
def test_status_obs(monkeypatch, no_overlay, phase, auth, expected):
    svc = SimpleNamespace(get_connection_phase=lambda: phase, auth_failed=lambda: auth)
    fake_module(monkeypatch, "obs_service", obs_service=svc)
    monkeypatch.setattr(cst, "obs_disconnected_status", lambda: "Not running")
    assert cst.get_connection_status("obs") == expected


@pytest.mark.parametrize("live,stored,expected", [
    ("Connected", None, "Connected"),
    ("", SimpleNamespace(connection_status="Bad token"), "Bad token"),
    ("", SimpleNamespace(connection_status=""), "Unknown"),
    (None, None, "Unknown"),
])
def test_status_discord(monkeypatch, no_overlay, live, stored, expected):
    fake_module(monkeypatch, "discord_service", get_discord_status=lambda: {"status": live})
    fake_state(monkeypatch, get_discord_data=stored)
    assert cst.get_connection_status("discord") == expected


@pytest.mark.parametrize("name", ["streamlabs", "streamelements"])
def test_status_donation_services(monkeypatch, no_overlay, name):
    fake_module(monkeypatch, name, **{f"get_{name}_status": lambda: {"status": "Connected"}})
    assert cst.get_connection_status(name) == "Connected"
    fake_module(monkeypatch, name, **{f"get_{name}_status": lambda: {}})
    assert cst.get_connection_status(name) == "Disconnected"


def test_status_webengine(monkeypatch, no_overlay):
    fake_module(monkeypatch, "web_engine", get_webengine_health=lambda: {"state": "running"})
    assert cst.get_connection_status("webengine") == "running"
    fake_module(monkeypatch, "web_engine", get_webengine_health=lambda: {})
    assert cst.get_connection_status("webengine") == "Unknown"


def test_status_unknown_key(no_overlay):
    assert cst.get_connection_status("zzz") == "Unknown"


def test_status_applies_overlay(monkeypatch):
    fake_module(monkeypatch, "twitch", get_twitch_connection_status=lambda: {"status": "Connected"})
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: "No Internet")
    assert cst.get_connection_status("twitch") == "No Internet"


# --------------------------------------------------------------------------- is_remote_service_disconnected

def test_disconnected_twitch(monkeypatch):
    fake_module(monkeypatch, "twitch", is_twitch_disconnected_for_monitor=lambda: True)
    assert cst.is_remote_service_disconnected("twitch") is True


@pytest.mark.parametrize("client,tokens,expected", [
    (None, True, False),
    (SimpleNamespace(spotify_data=SimpleNamespace(connection_status="Disconnected"), is_authenticated=True), False, False),
    (SimpleNamespace(spotify_data=SimpleNamespace(connection_status="Authorization Required"), is_authenticated=False), True, False),
    (SimpleNamespace(spotify_data=SimpleNamespace(connection_status="Token Refresh Failed"), is_authenticated=True), True, True),
    (SimpleNamespace(spotify_data=SimpleNamespace(connection_status=None), is_authenticated=False), True, True),
    (SimpleNamespace(spotify_data=SimpleNamespace(connection_status="Connected"), is_authenticated=True), True, False),
])
def test_disconnected_spotify(monkeypatch, client, tokens, expected):
    fake_module(monkeypatch, "spotify", get_spotify_client=lambda: client, spotify_has_stored_tokens=lambda: tokens)
    assert cst.is_remote_service_disconnected("spotify") is expected


@pytest.mark.parametrize("client,configured,expected", [
    (None, True, False),
    (SimpleNamespace(is_quota_blocked=lambda: False, is_connected=False), False, False),
    (SimpleNamespace(is_quota_blocked=lambda: True, is_connected=False), True, False),
    (SimpleNamespace(is_quota_blocked=lambda: False, is_connected=False), True, True),
    (SimpleNamespace(is_quota_blocked=lambda: False, is_connected=True), True, False),
])
def test_disconnected_youtube(monkeypatch, client, configured, expected):
    fake_module(monkeypatch, "youtube", get_youtube_client=lambda: client)
    monkeypatch.setattr(cst, "youtube_configured", lambda: configured)
    assert cst.is_remote_service_disconnected("youtube") is expected


def test_disconnected_psn(monkeypatch):
    fake_module(monkeypatch, "psn_service", is_psn_api_disconnected=lambda: True)
    assert cst.is_remote_service_disconnected("psn") is True


@pytest.mark.parametrize("configured,status,connected,expected", [
    (False, "", False, False),
    (True, "Reconnecting", False, False),
    (True, "Error", False, True),
    (True, "", True, False),
])
def test_disconnected_discord(monkeypatch, configured, status, connected, expected):
    svc = SimpleNamespace(get_status=lambda: {"status": status}, is_connected=lambda: connected)
    fake_module(monkeypatch, "discord_service", discord_service=svc)
    monkeypatch.setattr(cst, "discord_configured", lambda: configured)
    assert cst.is_remote_service_disconnected("discord") is expected


@pytest.mark.parametrize("creds,api,expected", [
    (False, SimpleNamespace(using_fallback=False, is_connected=False), False),
    (True, None, False),
    (True, SimpleNamespace(using_fallback=True, is_connected=False), False),
    (True, SimpleNamespace(), False),
    (True, SimpleNamespace(using_fallback=False, is_connected=False), True),
    (True, SimpleNamespace(using_fallback=False, is_connected=True), False),
])
def test_disconnected_chatbot(monkeypatch, creds, api, expected):
    fake_module(monkeypatch, "chatbot", chatbot_has_dedicated_credentials=lambda: creds, get_chatbot_api=lambda: api)
    assert cst.is_remote_service_disconnected("chatbot") is expected


@pytest.mark.parametrize("remote,ok,expected", [(False, False, False), (True, True, False), (True, False, True)])
def test_disconnected_database(monkeypatch, remote, ok, expected):
    fake_module(monkeypatch, "database_manager", database_needs_remote_monitor=lambda: remote, test_connection=lambda: ok)
    assert cst.is_remote_service_disconnected("database") is expected


def test_disconnected_unknown_key():
    assert cst.is_remote_service_disconnected("obs") is False


def test_constants():
    assert cst.REMOTE_SERVICE_KEYS == {"twitch", "spotify", "youtube", "psn"}
    assert set(cst.SERVICE_KEYS) <= set(cst._PROBE_INTERVAL_SEC) | {"obs"}

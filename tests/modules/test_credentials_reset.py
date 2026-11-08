# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Clearing one integration's saved credentials: what's blanked, what's stopped, what's kept."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import credentials_reset as cr  # noqa: E402


class FakeState:
    def __init__(self):
        self.cleared = []
        self.ok = True

    def clear_fields(self, key, fields):
        self.cleared.append((key, dict(fields)))
        return self.ok


@pytest.fixture
def env(monkeypatch):
    from modules import api_credentials_manager, dataobjects

    state = FakeState()
    monkeypatch.setattr(dataobjects, "state_manager", state)
    api = []
    monkeypatch.setattr(api_credentials_manager, "clear_service_credentials", lambda s: api.append(s) or True)
    calls = []
    return SimpleNamespace(state=state, api=api, calls=calls, mp=monkeypatch)


def test_every_service_has_a_clearer_and_words():
    assert set(cr.SERVICES) == set(cr._CLEARERS)
    for info in cr.SERVICES.values():
        assert info.label and info.what and info.kept
    with pytest.raises(ValueError):
        cr.clear("myspace")


def test_twitch_stops_the_connection_and_blanks_everything(env):
    from modules import twitch

    live = SimpleNamespace(stop_connection=lambda: env.calls.append("stop"), client_id="c", client_secret="s",
                           auth_token="a", refresh_token="r", user_id="u", token_expiry=1, twitch=object())
    env.mp.setattr(twitch, "get_twitch_api", lambda: live)
    ok, errors = cr.clear("twitch")
    assert ok and errors == []
    assert env.calls == ["stop"] and live.auth_token == "" and live.client_secret == "" and live.twitch is None
    key, fields = env.state.cleared[0]
    assert key == "twitch_data" and fields["auth_token"] == fields["refresh_token"] == fields["client_secret"] == ""
    assert env.api == ["twitch"]


def test_chatbot(env):
    from modules import chatbot

    live = SimpleNamespace(stop_health_check=lambda: None, client_id="c", client_secret="s", auth_token="a",
                           refresh_token="r", user_id="u", token_expiry=1, twitch=object(), user=object(), is_connected=True)
    env.mp.setattr(chatbot, "get_chatbot_api", lambda: live)
    assert cr.clear("chatbot")[0]
    assert live.is_connected is False and live.auth_token == ""
    assert env.state.cleared[0][0] == "chatbot_data" and env.api == ["chatbot"]


def test_obs_keeps_host_and_port(env):
    from modules import obs_service

    env.mp.setattr(obs_service.obs_service, "apply_settings", lambda: env.calls.append("reconnect"))
    assert cr.clear("obs")[0]
    assert env.state.cleared == [("obs_data", {"password": ""})] and env.calls == ["reconnect"]


@pytest.mark.parametrize("service,module,fn,key,field", [
    ("psn", "psn_service", "handle_psn_settings_change", "psn_settings_data", "npsso_code"),
    ("steam", "steam_service", "handle_steam_settings_change", "steam_settings_data", "api_key"),
])
def test_psn_and_steam_reapply_settings(env, service, module, fn, key, field):
    import importlib

    mod = importlib.import_module(f"modules.{module}")
    env.mp.setattr(mod, fn, lambda: env.calls.append(fn))
    assert cr.clear(service)[0]
    assert env.state.cleared == [(key, {field: ""})]
    assert env.calls == [fn]  # empty credential: the service stops and clears its live copy


def test_spotify_blanks_the_running_client(env):
    from modules import spotify

    data = SimpleNamespace(client_id="c", client_secret="s", access_token="a", refresh_token="r", token_expiry=1.0,
                           connection_status="Connected")
    client = SimpleNamespace(spotify_data=data, is_authenticated=True)
    env.mp.setattr(spotify, "stop_spotify_service", lambda: env.calls.append("stop"))
    env.mp.setattr(spotify, "get_spotify_client", lambda: client)
    assert cr.clear("spotify")[0]
    assert data.access_token == "" and data.connection_status == "Not Configured" and client.is_authenticated is False
    assert env.state.cleared[0][0] == "spotify_data" and env.api == ["spotify"]


def test_youtube(env):
    from modules import youtube

    data = SimpleNamespace(api_key="k", oauth_client_id="i", oauth_client_secret="s", access_token="a",
                           refresh_token="r", token_expiry="t", oauth_channel_id="c", oauth_channel_title="n")
    env.mp.setattr(youtube, "stop_youtube_service", lambda: env.calls.append("stop"))
    env.mp.setattr(youtube, "get_youtube_client", lambda: SimpleNamespace(youtube_data=data))
    assert cr.clear("youtube")[0]
    assert data.api_key == "" and data.refresh_token == "" and data.oauth_channel_title == ""
    key, fields = env.state.cleared[0]
    assert key == "youtube_data" and fields["oauth_client_secret"] == "" and "channel_urls" not in fields
    assert env.api == ["youtube"]


def test_streamlabs_and_streamelements(env):
    from modules import donation_settings, streamelements, streamlabs

    secrets = []
    env.mp.setattr(donation_settings, "clear_secret", lambda f: secrets.append(f) or True)
    env.mp.setattr(streamlabs, "disconnect_streamlabs", lambda: env.calls.append("sl"))
    env.mp.setattr(streamelements, "disconnect_streamelements", lambda: env.calls.append("se"))
    assert cr.clear("streamlabs")[0] and cr.clear("streamelements")[0]
    assert env.calls == ["sl", "se"]
    assert secrets == ["streamlabs_access_token", "streamlabs_refresh_token", "streamelements_jwt"]
    assert env.api == ["streamlabs"]


def test_discord_token_isnt_saved_back_on_disconnect(env):
    from modules import discord_service

    seen = []
    svc = SimpleNamespace(_token="BOT")
    svc.disconnect = lambda: seen.append(svc._token)
    env.mp.setattr(discord_service, "discord_service", svc)
    assert cr.clear("discord")[0]
    assert seen == [""]  # disconnect() saves its session copy back when the stored one is blank
    assert env.state.cleared[0] == ("discord_data", {"bot_token": "", "bot_user_id": "", "bot_username": "",
                                                     "application_id": "", "connection_status": "Not Configured"})


def test_a_failed_step_is_reported_and_the_rest_still_runs(env):
    from modules import obs_service

    env.state.ok = False
    env.mp.setattr(obs_service.obs_service, "apply_settings", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    ok, errors = cr.clear("obs")
    assert ok is False and errors == ["saved OBS password", "reconnect OBS without the password"]


def test_clear_and_notify(env, monkeypatch):
    from modules import notification_engine, obs_service

    notes = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: notes.append({"message": msg, **kw}))
    monkeypatch.setattr(obs_service.obs_service, "apply_settings", lambda: None)
    assert cr.clear_and_notify("obs") is True
    assert notes[-1]["title"] == "OBS credentials cleared" and "The host and port stayed" in notes[-1]["message"]
    env.state.ok = False
    assert cr.clear_and_notify("obs") is False
    assert notes[-1]["type"] == "error" and "saved OBS password" in notes[-1]["message"]


def test_api_credentials_clear(tmp_path, monkeypatch):
    from modules import api_credentials_manager as acm

    m = acm.APICredentialsManager(config_path=str(tmp_path / "api_credentials.json"))
    monkeypatch.setattr(m, "_save_credentials", lambda: True)
    m.initialize()
    m.update_streamlabs_credentials("cid", "secret")
    m.update_streamlabs_credentials("cid", "")  # blank secret: kept (the form guard)
    assert m.get_streamlabs_credentials()["client_secret"] == "secret"
    assert m.clear_service_credentials("streamlabs") is True
    assert m.get_streamlabs_credentials() == {"client_id": "", "client_secret": ""}
    with pytest.raises(ValueError):
        m.clear_service_credentials("nope")

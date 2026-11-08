# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Clear the saved sign-in for one integration ("Clear saved credentials" in its Settings tab).

Each ``clear(service)`` stops the live connection first (so it can't save its copy back),
blanks the in-memory copies, then blanks what's stored: the settings blob in the database
(through ``StateManager.clear_fields``, which unlike the normal setters does blank secrets)
and, where the service has one, its client ID and secret in ``api_credentials.json``.
Non-secret settings (which channel, which profile, the OBS host and port…) are kept.

``SERVICES`` lists what each one clears, for the confirmation dialog.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    what: str  # shown in the confirmation: what gets cleared
    kept: str  # what stays


SERVICES: Dict[str, Service] = {
    "twitch": Service("twitch", "Twitch", "the client ID, client secret and sign-in tokens of your Twitch account",
                      "your other Twitch settings"),
    "chatbot": Service("chatbot", "chatbot account", "the chatbot account's client ID, client secret and sign-in tokens",
                       "your commands and other chatbot settings"),
    "obs": Service("obs", "OBS", "the OBS WebSocket password", "the host and port"),
    "psn": Service("psn", "PlayStation", "the NPSSO code", "the PSN username to track and the Games Tracker library"),
    "steam": Service("steam", "Steam", "the Steam Web API key", "the profile to track and the Games Tracker library"),
    "spotify": Service("spotify", "Spotify", "the client ID, client secret and sign-in tokens", "your other Spotify settings"),
    "youtube": Service("youtube", "YouTube", "the API key, the OAuth client ID and secret, and the sign-in tokens",
                       "the channels you follow"),
    "streamlabs": Service("streamlabs", "Streamlabs", "the client ID, client secret and sign-in tokens",
                          "your alert settings"),
    "streamelements": Service("streamelements", "StreamElements", "the JWT token and the channel it belongs to",
                              "your alert settings"),
    "discord": Service("discord", "Discord", "the bot token", "your channel and go-live settings"),
}


def _step(errors: List[str], label: str, fn: Callable[[], object]) -> None:
    try:
        result = fn()
        if result is False:
            errors.append(label)
    except Exception as e:
        logger.warning("Clearing credentials: %s failed: %s", label, e, exc_info=True)
        errors.append(label)


def _state():
    from .dataobjects import state_manager

    return state_manager


def _api_creds(service: str) -> bool:
    from .api_credentials_manager import clear_service_credentials

    return clear_service_credentials(service)


# ----- per service -----


def _clear_twitch(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import twitch

        api = twitch.get_twitch_api()
        if api is None:
            return
        api.stop_connection()
        for name in ("client_id", "client_secret", "auth_token", "refresh_token", "user_id"):
            setattr(api, name, "")
        api.token_expiry = None
        api.twitch = None

    _step(errors, "stop the Twitch connection", stop_live)
    _step(errors, "saved Twitch tokens", lambda: _state().clear_fields(
        "twitch_data", {"client_id": "", "client_secret": "", "auth_token": "", "refresh_token": "",
                        "user_id": "", "token_expiry": ""}))
    _step(errors, "Twitch client ID and secret", lambda: _api_creds("twitch"))


def _clear_chatbot(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import chatbot

        api = chatbot.get_chatbot_api()
        if api is None:
            return
        try:
            api.stop_health_check()
        except Exception:
            pass
        for name in ("client_id", "client_secret", "auth_token", "refresh_token", "user_id"):
            setattr(api, name, "")
        api.token_expiry = None
        api.twitch = None
        api.user = None
        api.is_connected = False

    _step(errors, "stop the chatbot connection", stop_live)
    _step(errors, "saved chatbot tokens", lambda: _state().clear_fields(
        "chatbot_data", {"auth_token": "", "refresh_token": "", "user_id": "", "token_expiry": ""}))
    _step(errors, "chatbot client ID and secret", lambda: _api_creds("chatbot"))


def _clear_obs(errors: List[str]) -> None:
    _step(errors, "saved OBS password", lambda: _state().clear_fields("obs_data", {"password": ""}))

    def reconnect() -> None:
        from .obs_service import obs_service

        obs_service.apply_settings()

    _step(errors, "reconnect OBS without the password", reconnect)


def _clear_psn(errors: List[str]) -> None:
    _step(errors, "saved NPSSO code", lambda: _state().clear_fields("psn_settings_data", {"npsso_code": ""}))

    def stop_live() -> None:
        from . import psn_service

        psn_service.handle_psn_settings_change()  # an empty NPSSO stops PSN and clears the live copy

    _step(errors, "stop the PlayStation connection", stop_live)


def _clear_steam(errors: List[str]) -> None:
    _step(errors, "saved Steam API key", lambda: _state().clear_fields("steam_settings_data", {"api_key": ""}))

    def stop_live() -> None:
        from . import steam_service

        steam_service.handle_steam_settings_change()  # an empty key stops Steam

    _step(errors, "stop the Steam connection", stop_live)


def _clear_spotify(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import spotify

        spotify.stop_spotify_service()
        client = spotify.get_spotify_client()
        if client is not None:
            data = client.spotify_data
            for name in ("client_id", "client_secret", "access_token", "refresh_token"):
                setattr(data, name, "")
            data.token_expiry = None
            data.connection_status = "Not Configured"
            client.is_authenticated = False

    _step(errors, "stop Spotify", stop_live)
    _step(errors, "saved Spotify tokens", lambda: _state().clear_fields(
        "spotify_data", {"client_id": "", "client_secret": "", "access_token": "", "refresh_token": "",
                         "token_expiry": None, "connection_status": "Not Configured"}))
    _step(errors, "Spotify client ID and secret", lambda: _api_creds("spotify"))


def _clear_youtube(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import youtube

        youtube.stop_youtube_service()
        client = youtube.get_youtube_client()
        if client is not None:
            data = client.youtube_data
            for name in ("api_key", "oauth_client_id", "oauth_client_secret", "access_token", "refresh_token",
                         "token_expiry", "oauth_channel_id", "oauth_channel_title"):
                if hasattr(data, name):
                    setattr(data, name, "")

    _step(errors, "stop YouTube", stop_live)
    fields = {"api_key": "", "oauth_client_id": "", "oauth_client_secret": "", "access_token": "",
              "refresh_token": "", "token_expiry": "", "oauth_channel_id": "", "oauth_channel_title": "",
              "connection_status": "Not Configured"}
    _step(errors, "saved YouTube keys and tokens", lambda: _state().clear_fields("youtube_data", fields))
    _step(errors, "YouTube OAuth client ID and secret", lambda: _api_creds("youtube"))


def _clear_streamlabs(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import streamlabs

        streamlabs.disconnect_streamlabs()  # also clears the access and refresh tokens

    _step(errors, "disconnect Streamlabs", stop_live)

    def tokens() -> None:
        from .donation_settings import clear_secret

        clear_secret("streamlabs_access_token")
        clear_secret("streamlabs_refresh_token")

    _step(errors, "saved Streamlabs tokens", tokens)
    _step(errors, "Streamlabs client ID and secret", lambda: _api_creds("streamlabs"))


def _clear_streamelements(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import streamelements

        streamelements.disconnect_streamelements()  # stops it and clears the JWT and channel

    _step(errors, "disconnect StreamElements", stop_live)

    def token() -> None:
        from .donation_settings import clear_secret

        clear_secret("streamelements_jwt")

    _step(errors, "saved StreamElements token", token)


def _clear_discord(errors: List[str]) -> None:
    def stop_live() -> None:
        from . import discord_service

        svc = discord_service.discord_service
        svc._token = ""  # disconnect() would otherwise save its session copy back
        svc.disconnect()
        svc._token = ""

    _step(errors, "disconnect the Discord bot", stop_live)
    _step(errors, "saved Discord bot token", lambda: _state().clear_fields(
        "discord_data", {"bot_token": "", "bot_user_id": "", "bot_username": "", "application_id": "",
                         "connection_status": "Not Configured"}))


_CLEARERS: Dict[str, Callable[[List[str]], None]] = {
    "twitch": _clear_twitch,
    "chatbot": _clear_chatbot,
    "obs": _clear_obs,
    "psn": _clear_psn,
    "steam": _clear_steam,
    "spotify": _clear_spotify,
    "youtube": _clear_youtube,
    "streamlabs": _clear_streamlabs,
    "streamelements": _clear_streamelements,
    "discord": _clear_discord,
}


def clear(service: str) -> Tuple[bool, List[str]]:
    """Clear one integration's saved credentials. Returns (everything cleared, the steps that failed)."""
    if service not in _CLEARERS:
        raise ValueError(f"Unknown integration: {service}")
    errors: List[str] = []
    _CLEARERS[service](errors)
    if errors:
        logger.warning("Cleared %s credentials with problems: %s", service, ", ".join(errors))
    else:
        logger.info("Cleared saved %s credentials", service)
    return not errors, errors


def clear_and_notify(service: str) -> bool:
    """``clear`` plus one notice saying how it went. Blocking: call off the UI thread."""
    from .notification_engine import notify

    info = SERVICES[service]
    ok, errors = clear(service)
    if ok:
        notify(f"Cleared {info.what}. {info.kept[0].upper() + info.kept[1:]} stayed.", type="success",
               source="app", title=f"{info.label} credentials cleared")
    else:
        notify("Some of it couldn't be cleared: " + ", ".join(errors) + ".", type="error", source="app",
               title=f"Couldn't fully clear the {info.label} credentials",
               hint="Check the database connection in Settings → Database, then try again. Details are in the log.")
    return ok

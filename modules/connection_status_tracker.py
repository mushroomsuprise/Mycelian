"""
Live integration connection status — probed on a schedule, independent of Settings tab UI.

Called from the global status poll so footer badges and notifications reflect real
connection state without opening each settings sub-tab.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SERVICE_KEYS = ("twitch", "spotify", "youtube", "psn", "obs")


def twitch_configured() -> bool:
    try:
        from .api_credentials_manager import api_credentials_manager

        c = api_credentials_manager.get_twitch_credentials()
        return bool(
            (c.get("client_id") or "").strip()
            and (c.get("client_secret") or "").strip()
        )
    except Exception:
        return False


def spotify_configured() -> bool:
    try:
        from .api_credentials_manager import api_credentials_manager

        c = api_credentials_manager.get_spotify_credentials()
        return bool(
            (c.get("client_id") or "").strip()
            and (c.get("client_secret") or "").strip()
        )
    except Exception:
        return False


def youtube_configured() -> bool:
    try:
        from .dataobjects import state_manager

        y = state_manager.get_youtube_data()
        if not y:
            return False
        return bool((getattr(y, "api_key", "") or "").strip())
    except Exception:
        return False


def psn_configured() -> bool:
    """NPSSO present in settings or live snapshot (PSN tab semantics)."""
    try:
        from .dataobjects import state_manager

        live = state_manager.get_live_psn_data()
        settings = state_manager.get_psn_settings_data()
        token_in_settings = (
            (settings.npsso_code or "").strip() if settings else ""
        )
        token_in_live = ""
        if live and getattr(live, "npsso_code", None):
            token_in_live = str(live.npsso_code or "").strip()
        return bool(token_in_settings or token_in_live)
    except Exception:
        return False


def obs_configured() -> bool:
    try:
        from .dataobjects import state_manager

        o = state_manager.get_obs_data()
        if not o:
            return False
        return bool(getattr(o, "enabled", True))
    except Exception:
        return False


def service_configured(key: str) -> bool:
    if key == "twitch":
        return twitch_configured()
    if key == "spotify":
        return spotify_configured()
    if key == "youtube":
        return youtube_configured()
    if key == "psn":
        return psn_configured()
    if key == "obs":
        return obs_configured()
    return False

# Minimum seconds between heavier probes per service (main poll is ~2s).
_PROBE_INTERVAL_SEC: Dict[str, float] = {
    "twitch": 5.0,
    "spotify": 15.0,
    "youtube": 30.0,
    "psn": 12.0,
}

_last_probe_mono: Dict[str, float] = {}
_last_spotify_auth_mono: float = 0.0
_SPOTIFY_AUTH_INTERVAL_SEC = 10.0


def _configured(key: str) -> bool:
    if key == "twitch":
        return True
    return service_configured(key)


def _should_probe(key: str, *, force: bool = False) -> bool:
    if force:
        return True
    interval = _PROBE_INTERVAL_SEC.get(key, 10.0)
    now = time.monotonic()
    last = _last_probe_mono.get(key, 0.0)
    if now - last < interval:
        return False
    _last_probe_mono[key] = now
    return True


def probe_configured_services(*, force: bool = False) -> None:
    """Ensure background workers are running and refresh live status where needed."""
    for key in SERVICE_KEYS:
        if key != "twitch" and not _configured(key):
            continue
        if not _should_probe(key, force=force):
            continue
        try:
            if key == "spotify":
                _probe_spotify()
            elif key == "youtube":
                _probe_youtube()
            elif key == "psn":
                _probe_psn()
            # Twitch / OBS: status reads are already live from their service workers.
        except Exception:
            logger.debug("connection status probe failed for %s", key, exc_info=True)


def _probe_spotify() -> None:
    from . import spotify

    if not spotify.spotify_client:
        spotify.initialize_spotify()
    if not spotify.is_running:
        spotify.start_spotify_service()

    client = spotify.get_spotify_client()
    if not client:
        return

    global _last_spotify_auth_mono
    now = time.monotonic()
    data = client.spotify_data
    has_tokens = bool(
        (getattr(data, "refresh_token", "") or "").strip()
        or (getattr(data, "access_token", "") or "").strip()
    )
    if (
        has_tokens
        and not client.is_authenticated
        and now - _last_spotify_auth_mono >= _SPOTIFY_AUTH_INTERVAL_SEC
    ):
        _last_spotify_auth_mono = now
        has_creds = bool(
            (getattr(data, "client_id", "") or "").strip()
            and (getattr(data, "client_secret", "") or "").strip()
        )
        client.reload_and_sync(persist=True)
        ok = client.authenticate()
        logger.info(
            "Spotify background auth retry: success=%s creds=%s tokens=%s status=%s",
            ok,
            has_creds or bool(
                client.spotify_data.client_id and client.spotify_data.client_secret
            ),
            has_tokens,
            client.spotify_data.connection_status,
        )


def _probe_youtube() -> None:
    from . import youtube

    if not youtube.youtube_client:
        youtube.initialize_youtube()
    if not youtube.is_running:
        youtube.start_youtube_service()


def _probe_psn() -> None:
    from . import psn_service

    if not psn_service.psn_client_instance:
        psn_service.initialize_psn_module()
    else:
        psn_service.start_psn_data_updater_thread()


def get_connection_status(key: str) -> str:
    """Return the current connection status label for an integration."""
    if key == "twitch":
        from . import twitch

        info = twitch.get_twitch_connection_status()
        st = info.get("status")
        return str(st).strip() if st is not None else "Unknown"

    if key == "spotify":
        from . import spotify

        st = spotify.get_spotify_status()
        status = str(st.get("status", "") or "").strip()
        if status and status != "Not Initialized":
            return status
        from .dataobjects import state_manager

        s = state_manager.get_spotify_data()
        if s:
            return (getattr(s, "connection_status", "") or "Unknown").strip()
        return "Unknown"

    if key == "youtube":
        from . import youtube

        st = youtube.get_youtube_status()
        status = str(st.get("status", "") or "").strip()
        if status and status != "Not Initialized":
            return status
        from .dataobjects import state_manager

        y = state_manager.get_youtube_data()
        if not y:
            return "Unknown"
        return (getattr(y, "connection_status", "") or "Unknown").strip()

    if key == "psn":
        from .dataobjects import state_manager

        live = state_manager.get_live_psn_data()
        settings = state_manager.get_psn_settings_data()
        token_in_settings = (
            (settings.npsso_code or "").strip() if settings else ""
        )
        token_in_live = ""
        if live and getattr(live, "npsso_code", None):
            token_in_live = str(live.npsso_code or "").strip()
        if not (token_in_settings or token_in_live):
            return "Not Connected"
        if live and getattr(live, "is_online", False):
            return "Connected"
        return "Configured but Offline"

    if key == "obs":
        from .obs_service import obs_service

        return "Connected" if obs_service.is_connected() else "Disconnected"

    return "Unknown"

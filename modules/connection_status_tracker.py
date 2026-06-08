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

SERVICE_KEYS = ("twitch", "spotify", "youtube", "psn", "obs", "webengine")


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
    if key == "webengine":
        # The overlay server always runs (OBS sources / Stream Deck / alerts).
        return True
    return False

# Minimum seconds between heavier probes per service (main poll is ~2s).
_PROBE_INTERVAL_SEC: Dict[str, float] = {
    "twitch": 5.0,
    "spotify": 15.0,
    "youtube": 30.0,
    "psn": 12.0,
    "webengine": 5.0,
}

# A WebEngine heartbeat older than this means the gevent hub is stalled.
_WEBENGINE_FREEZE_SEC = 60.0

_last_probe_mono: Dict[str, float] = {}


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
    """Touch configured integrations on a schedule (rate-limited).

    Service startup is owned by ``DeferredServiceManager`` in ``main.py``.
    Footer and notifications read live state via :func:`get_connection_status` only.
    """
    for key in SERVICE_KEYS:
        if key != "twitch" and not _configured(key):
            continue
        if not _should_probe(key, force=force):
            continue
        try:
            get_connection_status(key)
        except Exception:
            logger.debug("connection status read failed for %s", key, exc_info=True)


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

        phase = obs_service.get_connection_phase()
        if phase == "connected":
            return "Connected"
        if phase == "connecting":
            return "Connecting"
        if phase == "disconnecting":
            return "Disconnecting"
        return "Disconnected"

    if key == "webengine":
        from . import web_engine

        if not getattr(web_engine, "web_engine_running", False):
            return "Stopped"
        inst = getattr(web_engine, "web_engine_instance", None)
        last = getattr(inst, "_last_gevent_heartbeat", None) if inst else None
        if last is not None and (time.time() - last) > _WEBENGINE_FREEZE_SEC:
            return "Frozen"
        return "Connected"

    return "Unknown"

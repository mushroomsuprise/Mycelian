# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
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

SERVICE_KEYS = ("twitch", "spotify", "youtube", "psn", "obs", "discord", "webengine")


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


def discord_configured() -> bool:
    try:
        from .dataobjects import state_manager

        d = state_manager.get_discord_data()
        if not d:
            return False
        return bool((getattr(d, "bot_token", "") or "").strip())
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
    if key == "discord":
        return discord_configured()
    if key == "webengine":
        # The overlay server always runs (OBS sources / Stream Deck / alerts).
        return True
    return False

# Minimum seconds between heavier probes per service (main poll is ~2s).
_PROBE_INTERVAL_SEC: Dict[str, float] = {
    "internet": 5.0,
    "twitch": 5.0,
    "spotify": 15.0,
    "youtube": 30.0,
    "psn": 12.0,
    "discord": 15.0,
    "webengine": 5.0,
}

# Remote integrations whose footer/settings status reflects connectivity probes.
REMOTE_SERVICE_KEYS = frozenset({"twitch", "spotify", "youtube", "psn"})

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
    for key in ("internet",) + SERVICE_KEYS:
        if key == "internet":
            if not _should_probe(key, force=force):
                continue
        elif key != "twitch" and not _configured(key):
            continue
        elif not _should_probe(key, force=force):
            continue
        try:
            get_connection_status(key)
        except Exception:
            logger.debug("connection status read failed for %s", key, exc_info=True)


def get_connectivity_overlay(key: str) -> Optional[str]:
    """Return a connectivity-based status override for remote services, if any."""
    if key not in REMOTE_SERVICE_KEYS:
        return None
    try:
        from .connection_monitor import (
            get_internet_status,
            is_internet_available,
            is_service_reachable,
        )

        is_internet_available()
        inet = get_internet_status()
        if inet == "Offline":
            return "No Internet"
        if inet == "Checking":
            return "Checking Internet"
        if not is_service_reachable(key):
            return "Service Unreachable"
    except Exception:
        logger.debug("connectivity overlay failed for %s", key, exc_info=True)
    return None


def apply_connectivity_overlay(key: str, status: str) -> str:
    """Apply connectivity overlay to a status string when appropriate."""
    overlay = get_connectivity_overlay(key)
    return overlay if overlay else status


def apply_connectivity_overlay_to_info(
    key: str,
    info: Dict[str, object],
    *,
    status_field: str = "status",
    valid_field: Optional[str] = "is_valid",
) -> Dict[str, object]:
    """Apply connectivity overlay to a status dict (settings tabs)."""
    overlay = get_connectivity_overlay(key)
    if not overlay:
        return info
    out = dict(info)
    out[status_field] = overlay
    if valid_field and valid_field in out:
        out[valid_field] = False
    return out


def get_connection_status(key: str) -> str:
    """Return the current connection status label for an integration."""
    if key == "internet":
        from .connection_monitor import get_internet_status, is_internet_available

        is_internet_available()
        return get_internet_status()

    base_status: Optional[str] = None

    if key == "twitch":
        from . import twitch

        info = twitch.get_twitch_connection_status()
        st = info.get("status")
        base_status = str(st).strip() if st is not None else "Unknown"

    elif key == "spotify":
        from . import spotify

        st = spotify.get_spotify_status()
        status = str(st.get("status", "") or "").strip()
        if status and status != "Not Initialized":
            base_status = status
        else:
            from .dataobjects import state_manager

            s = state_manager.get_spotify_data()
            if s:
                base_status = (getattr(s, "connection_status", "") or "Unknown").strip()
            else:
                base_status = "Unknown"

    elif key == "youtube":
        from . import youtube

        st = youtube.get_youtube_status()
        status = str(st.get("status", "") or "").strip()
        if status and status != "Not Initialized":
            base_status = status
        else:
            from .dataobjects import state_manager

            y = state_manager.get_youtube_data()
            if not y:
                base_status = "Unknown"
            else:
                base_status = (getattr(y, "connection_status", "") or "Unknown").strip()

    elif key == "psn":
        from . import psn_service

        base_status = psn_service.get_psn_status_label()

    elif key == "obs":
        from .obs_service import obs_service

        phase = obs_service.get_connection_phase()
        if phase == "connected":
            base_status = "Connected"
        elif phase == "connecting":
            base_status = "Connecting"
        elif phase == "disconnecting":
            base_status = "Disconnecting"
        else:
            base_status = "Disconnected"

    elif key == "discord":
        from . import discord_service

        st = discord_service.get_discord_status()
        status = str(st.get("status", "") or "").strip()
        if status:
            base_status = status
        else:
            from .dataobjects import state_manager

            d = state_manager.get_discord_data()
            base_status = (
                (getattr(d, "connection_status", "") or "Unknown").strip()
                if d
                else "Unknown"
            )

    elif key == "webengine":
        from .web_engine import get_webengine_health

        health = get_webengine_health()
        base_status = str(health.get("state") or "Unknown")

    else:
        base_status = "Unknown"

    if base_status is None:
        base_status = "Unknown"
    return apply_connectivity_overlay(key, base_status)


# Status labels that warrant auto-reconnect for Spotify (not auth/OAuth in-progress).
_SPOTIFY_RECONNECTABLE = frozenset(
    {
        "Disconnected",
        "Token Refresh Failed",
        "Token Refresh Error",
    }
)

_SPOTIFY_SKIP_RECONNECT = frozenset(
    {
        "Authorization Required",
        "Not Configured",
        "Awaiting Authorization",
        "Opening Browser...",
        "Not Initialized",
    }
)


def is_remote_service_disconnected(key: str) -> bool:
    """True when a remote service should be considered disconnected for auto-reconnect."""
    if key == "twitch":
        from . import twitch

        return twitch.is_twitch_disconnected_for_monitor()

    if key == "spotify":
        from . import spotify

        client = spotify.get_spotify_client()
        if client is None:
            return False
        if not spotify.spotify_has_stored_tokens():
            return False
        status = (client.spotify_data.connection_status or "").strip()
        if status in _SPOTIFY_SKIP_RECONNECT:
            return False
        if status in _SPOTIFY_RECONNECTABLE:
            return True
        return not client.is_authenticated

    if key == "youtube":
        from . import youtube

        client = youtube.get_youtube_client()
        if client is None:
            return False
        if not youtube_configured():
            return False
        if client.is_quota_blocked():
            return False
        return not client.is_connected

    if key == "psn":
        from . import psn_service

        return psn_service.is_psn_api_disconnected()

    if key == "discord":
        from . import discord_service

        if not discord_configured():
            return False
        return not discord_service.discord_service.is_connected()

    if key == "chatbot":
        from . import chatbot

        if not chatbot.chatbot_has_dedicated_credentials():
            return False
        api = chatbot.get_chatbot_api()
        if api is None or getattr(api, "using_fallback", True):
            return False
        return not getattr(api, "is_connected", False)

    if key == "database":
        from . import database_manager

        if not database_manager.database_needs_remote_monitor():
            return False
        return not database_manager.test_connection()

    return False

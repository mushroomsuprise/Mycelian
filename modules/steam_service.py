# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Steam service: one ``SteamClient`` for the saved settings, a background thread that polls the
profile, current game and achievements, and Socket.IO broadcasts for overlays:

* ``steam_data_update``: the full public state (``steamapi.public_payload``).
* ``steam_achievement_unlocked``: one achievement dict per new unlock.

Budget: Steam allows 100,000 Web API calls a day; this uses a few thousand.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .dataobjects import state_manager
from .steam_data import SteamData
from .steamapi import (
    SteamAPIError,
    SteamClient,
    SteamConnectionError,
    SteamKeyError,
    public_payload,
    redact_key,
)

logger = logging.getLogger(__name__)

steam_client_instance: Optional[SteamClient] = None
steam_update_thread: Optional[threading.Thread] = None
stop_steam_thread_event = threading.Event()
_steam_init_lock = threading.Lock()

SUMMARY_SEC = 15  # Profile and current game
ACHIEVEMENTS_SEC = 30  # Current game's achievements (and right away when the game changes)
LEVEL_SEC = 3600  # Steam level
RETRY_SEC = 60  # Reconnect attempts while disconnected


def _emit(event: str, payload: dict) -> None:
    try:
        from . import web_engine

        instance = getattr(web_engine, "web_engine_instance", None)
        if instance:
            instance.safe_emit(event, payload)
    except Exception as e:
        logger.debug("Steam broadcast of %s failed: %s", event, e)


def _update_and_broadcast() -> None:
    client = steam_client_instance
    if client is None:
        return
    state_manager.set_live_steam_data(client.steam_data)
    _emit("steam_data_update", public_payload(client.steam_data))


def _initialize_steam_module_impl() -> None:
    global steam_client_instance
    with _steam_init_lock:
        settings = state_manager.get_steam_settings_data()
        if not (settings.api_key or "").strip():
            logger.info("Steam not configured (no Web API key)")
            state_manager.set_live_steam_data(SteamData(connection_status="Not Connected"))
            return
        steam_client_instance = SteamClient(settings.api_key, settings.steam_profile)
        steam_client_instance.connect()
        _update_and_broadcast()
        start_steam_data_updater_thread()


def initialize_steam_module() -> None:
    """Connect to Steam in the background and start polling (called once at startup)."""
    threading.Thread(target=_initialize_steam_module_impl, daemon=True, name="SteamInit").start()


def _poll_once(client: SteamClient, state: dict) -> None:
    """One update cycle. ``state`` keeps the timers between cycles."""
    now = time.time()
    previous_game = client.steam_data.current_game_appid
    if not client.refresh_summary():
        client.connected = False
        client.steam_data.connection_status = "Profile Not Found"
        return
    if "level_at" not in state or now - state["level_at"] >= LEVEL_SEC:
        try:
            client.refresh_level()
        except SteamAPIError as e:
            logger.debug("Steam level unavailable: %s", e)
        state["level_at"] = now
    game = client.steam_data.current_game_appid
    due = "achievements_at" not in state or now - state["achievements_at"] >= ACHIEVEMENTS_SEC
    if game and (game != previous_game or due):
        try:
            for achievement in client.refresh_achievements():
                _emit("steam_achievement_unlocked", achievement)
        except SteamKeyError:
            raise
        except SteamAPIError as e:
            logger.debug("Steam achievements unavailable for %s: %s", game, e)
        state["achievements_at"] = now


def steam_data_update_loop() -> None:
    state: dict = {}
    logger.info("Steam data updater started")
    while not stop_steam_thread_event.is_set():
        client = steam_client_instance
        if client is None or not client.api_key:
            break
        wait = SUMMARY_SEC
        try:
            if not client.is_connected():
                if client.steam_data.connection_status in ("Invalid API Key", "Profile Not Found", "Profile Not Set"):
                    wait = RETRY_SEC * 5  # needs new settings; check rarely
                elif not attempt_auto_reconnect():
                    wait = RETRY_SEC
            else:
                _poll_once(client, state)
        except SteamKeyError as e:
            logger.warning("Steam refused the API key: %s", e)
            client.connected = False
            client.steam_data.connection_status = "Invalid API Key"
        except SteamConnectionError as e:
            logger.warning("Steam unreachable: %s", e)
            client.connected = False
            client.steam_data.connection_status = "Disconnected"
            wait = RETRY_SEC
        except Exception as e:
            logger.warning("Steam update failed: %s", redact_key(str(e), client.api_key))
        _update_and_broadcast()
        stop_steam_thread_event.wait(wait)
    logger.info("Steam data updater stopped")


def start_steam_data_updater_thread() -> None:
    global steam_update_thread
    if steam_client_instance is None or not steam_client_instance.api_key:
        return
    if steam_update_thread is not None and steam_update_thread.is_alive():
        return
    stop_steam_thread_event.clear()
    steam_update_thread = threading.Thread(target=steam_data_update_loop, daemon=True, name="SteamUpdateThread")
    steam_update_thread.start()


def stop_steam_data_updater_thread(*, join_timeout: float = 5.0) -> None:
    global steam_update_thread
    if steam_update_thread and steam_update_thread.is_alive():
        stop_steam_thread_event.set()
        steam_update_thread.join(timeout=join_timeout)
        if steam_update_thread.is_alive():
            logger.warning("Steam updater did not stop within %.1fs", join_timeout)
            return
    steam_update_thread = None


def handle_steam_settings_change() -> None:
    """Apply saved Steam settings: reconnect with the new key or profile (call after save)."""
    global steam_client_instance
    settings = state_manager.get_steam_settings_data()
    key = (settings.api_key or "").strip()
    profile = (settings.steam_profile or "").strip()
    client = steam_client_instance
    if client is not None and client.api_key == key and client.profile == profile:
        if client.is_connected():
            start_steam_data_updater_thread()
            return
    stop_steam_data_updater_thread()
    if not key:
        steam_client_instance = None
        state_manager.set_live_steam_data(SteamData(connection_status="Not Connected"))
        _emit("steam_data_update", public_payload(SteamData(connection_status="Not Connected")))
        return
    if client is None:
        steam_client_instance = client = SteamClient(key, profile)
    else:
        client.update_credentials(key, profile)
    client.connect()
    _update_and_broadcast()
    start_steam_data_updater_thread()


# ----- status (footer, settings, connection monitor) -----


def get_steam_client() -> Optional[SteamClient]:
    return steam_client_instance


def steam_configured() -> bool:
    try:
        return bool((state_manager.get_steam_settings_data().api_key or "").strip())
    except Exception:
        return False


def get_steam_status_label() -> str:
    """Connected, Offline, Not Connected, Invalid API Key, Profile Not Found or Disconnected."""
    if not steam_configured():
        return "Not Connected"
    client = steam_client_instance
    if client is not None:
        return client.steam_data.connection_status or "Disconnected"
    live = state_manager.get_live_steam_data()
    return (getattr(live, "connection_status", "") or "Disconnected") if live else "Disconnected"


def get_steam_status_display() -> tuple[str, str, str]:
    """(status text, profile name, theme color class) for the Steam settings tab."""
    from .connection_status_tracker import get_connectivity_overlay

    overlay = get_connectivity_overlay("steam")
    if overlay:
        return overlay, "", "text-theme-error"
    status = get_steam_status_label()
    live = state_manager.get_live_steam_data()
    name = str(getattr(live, "persona_name", "") or "") if live else ""
    if status == "Connected":
        return "Connected", name, "text-theme-success"
    if status in ("Offline", "Profile Not Set"):
        return status, name, "text-theme-warning"
    return status, name, "text-theme-error"


def steam_configured_for_monitor() -> bool:
    return steam_configured()


def is_steam_api_disconnected() -> bool:
    client = steam_client_instance
    return bool(client and client.api_key and not client.is_connected())


def attempt_auto_reconnect() -> bool:
    client = steam_client_instance
    if client is None or not client.api_key:
        return False
    try:
        from .connection_monitor import is_internet_available, is_service_reachable

        if not is_internet_available() or not is_service_reachable("steam"):
            return False
    except Exception:
        return False
    ok = bool(client.connect())
    if ok:
        start_steam_data_updater_thread()
    return ok

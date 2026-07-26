# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Central connection monitor: Tier 1 external internet probes, Tier 2 per-service
reachability, and coordinated auto-reconnect for remote integrations.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests

from .shutdown import is_shutdown_in_progress

logger = logging.getLogger(__name__)

# --- Tier 1: external internet probes ---
_EXTERNAL_PROBE_URLS: List[str] = [
    "https://connectivitycheck.gstatic.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
]
_EXTERNAL_OFFLINE_STREAK_REQUIRED = 3
_PROBE_TIMEOUT_SEC = 3.0
_INTERNET_ONLINE_CACHE_SEC = 15.0
_INTERNET_OFFLINE_CACHE_SEC = 10.0

# --- Tier 2: per-service probe cache ---
_SERVICE_PROBE_CACHE_SEC = 30.0

# --- Monitor loop ---
_MONITOR_INTERVAL_SEC = 25.0
_RECONNECT_COOLDOWN_SEC = 60.0

_external_fail_streak = 0
_internet_online = True
_internet_status_label = "Online"
_last_external_probe_mono = 0.0
_last_external_result_online = True
_connectivity_lock = threading.Lock()

_service_probe_cache: Dict[str, tuple[bool, float]] = {}
_last_reconnect_mono: Dict[str, float] = {}

_monitor_thread: Optional[threading.Thread] = None
_monitor_stop = threading.Event()


def _http_probe(url: str) -> bool:
    """Return True if the host responds (any HTTP status counts as reachable)."""
    try:
        resp = requests.head(
            url,
            timeout=_PROBE_TIMEOUT_SEC,
            allow_redirects=True,
        )
        return resp.status_code < 500
    except requests.RequestException:
        try:
            resp = requests.get(
                url,
                timeout=_PROBE_TIMEOUT_SEC,
                allow_redirects=True,
                stream=True,
            )
            resp.close()
            return resp.status_code < 500
        except requests.RequestException:
            return False


def _tcp_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SEC):
            return True
    except OSError:
        return False


def _run_external_probe_round() -> bool:
    """Probe all external sources. Round succeeds if any source responds."""
    for url in _EXTERNAL_PROBE_URLS:
        if _http_probe(url):
            return True
    return False


def _update_internet_state(*, force: bool = False) -> bool:
    """Run Tier 1 probes and update streak / online flag. Returns internet online."""
    global _external_fail_streak, _internet_online, _internet_status_label
    global _last_external_probe_mono, _last_external_result_online

    now = time.monotonic()
    with _connectivity_lock:
        if not force:
            cache_ttl = (
                _INTERNET_ONLINE_CACHE_SEC
                if _internet_online
                else _INTERNET_OFFLINE_CACHE_SEC
            )
            if now - _last_external_probe_mono < cache_ttl:
                return _internet_online

        _internet_status_label = "Checking"
        round_ok = _run_external_probe_round()
        _last_external_probe_mono = now
        _last_external_result_online = round_ok

        if round_ok:
            became_online = not _internet_online
            _external_fail_streak = 0
            _internet_online = True
            _internet_status_label = "Online"
            if became_online:
                logger.info("Internet connectivity restored")
                try:
                    from . import web_engine

                    web_engine.broadcast_overlay_recovery("internet", "restored")
                except Exception:
                    logger.debug(
                        "overlay recovery broadcast failed after internet restore",
                        exc_info=True,
                    )
        else:
            _external_fail_streak += 1
            if _external_fail_streak >= _EXTERNAL_OFFLINE_STREAK_REQUIRED:
                _internet_online = False
                _internet_status_label = "Offline"
                _service_probe_cache.clear()
                logger.info(
                    "Internet flagged offline after %d consecutive all-fail external probe rounds",
                    _external_fail_streak,
                )
            else:
                # Streak building — keep previous online state until threshold hit
                _internet_status_label = "Checking"
                logger.debug(
                    "External probe round failed (%d/%d toward offline)",
                    _external_fail_streak,
                    _EXTERNAL_OFFLINE_STREAK_REQUIRED,
                )

        return _internet_online


def is_internet_available(*, force: bool = False) -> bool:
    """Tier 1: True when external connectivity probes report online."""
    if is_shutdown_in_progress():
        return False
    return _update_internet_state(force=force)


def get_internet_status() -> str:
    """Human-readable Tier 1 status: Online / Offline / Checking."""
    return _internet_status_label


def _probe_service_url(url: str) -> bool:
    return _http_probe(url)


def _probe_database() -> bool:
    try:
        from . import database_manager

        cfg = database_manager.get_config()
        if cfg.database_type == "sql":
            return True
        if cfg.database_type == "firebase":
            db_url = (cfg.firebase_database_url or "").strip()
            if not db_url:
                return False
            if not db_url.endswith(".json"):
                db_url = db_url.rstrip("/") + "/.json"
            return _http_probe(db_url + "?shallow=true")
        if cfg.database_type == "mongodb":
            conn = (cfg.mongodb_connection_string or "mongodb://localhost:27017/").strip()
            parsed = urlparse(conn)
            host = parsed.hostname or "localhost"
            port = parsed.port or 27017
            return _tcp_probe(host, port)
    except Exception:
        logger.debug("database service probe failed", exc_info=True)
    return False


_SERVICE_PROBE_URLS: Dict[str, str] = {
    "twitch": "https://api.twitch.tv/helix/users",
    "spotify": "https://api.spotify.com/v1/me",
    "youtube": "https://www.googleapis.com/youtube/v3/",
    "psn": "https://ca.account.sony.com/",
    "chatbot": "https://api.twitch.tv/helix/users",
}


def is_service_reachable(key: str, *, force: bool = False) -> bool:
    """Tier 2: True when the service host responds (only meaningful if internet is online)."""
    if key == "database":
        return _probe_database()

    if not is_internet_available():
        return False

    now = time.monotonic()
    with _connectivity_lock:
        if not force:
            cached = _service_probe_cache.get(key)
            if cached is not None:
                result, ts = cached
                if now - ts < _SERVICE_PROBE_CACHE_SEC:
                    return result

    url = _SERVICE_PROBE_URLS.get(key)
    if not url:
        result = True
    else:
        result = _probe_service_url(url)

    with _connectivity_lock:
        _service_probe_cache[key] = (result, now)
    if not result:
        logger.debug("Tier 2 probe failed for service %s", key)
    return result


def _auto_reconnect_enabled() -> bool:
    try:
        from .dataobjects import state_manager

        return bool(state_manager.get_app_settings().auto_reconnect)
    except Exception:
        return True


def _reconnect_cooldown_elapsed(key: str) -> bool:
    now = time.monotonic()
    last = _last_reconnect_mono.get(key, 0.0)
    return now - last >= _RECONNECT_COOLDOWN_SEC


def _mark_reconnect_attempt(key: str) -> None:
    _last_reconnect_mono[key] = time.monotonic()


@dataclass
class _ServiceEntry:
    key: str
    is_configured: Callable[[], bool]
    is_disconnected: Callable[[], bool]
    attempt_reconnect: Callable[[], bool]


def _build_registry() -> List[_ServiceEntry]:
    from . import chatbot, discord_service, spotify, twitch, youtube
    from . import database_manager
    from . import psn_service
    from .connection_status_tracker import is_remote_service_disconnected

    entries: List[_ServiceEntry] = [
        _ServiceEntry(
            key="twitch",
            is_configured=lambda: twitch.twitch_has_tokens_configured(),
            is_disconnected=lambda: is_remote_service_disconnected("twitch"),
            attempt_reconnect=lambda: twitch.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="spotify",
            is_configured=spotify.spotify_configured_for_monitor,
            is_disconnected=lambda: is_remote_service_disconnected("spotify"),
            attempt_reconnect=lambda: spotify.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="youtube",
            is_configured=youtube.youtube_configured_for_monitor,
            is_disconnected=lambda: is_remote_service_disconnected("youtube"),
            attempt_reconnect=lambda: youtube.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="psn",
            is_configured=psn_service.psn_configured_for_monitor,
            is_disconnected=lambda: is_remote_service_disconnected("psn"),
            attempt_reconnect=lambda: psn_service.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="discord",
            is_configured=discord_service.discord_configured_for_monitor,
            is_disconnected=lambda: is_remote_service_disconnected("discord"),
            attempt_reconnect=lambda: discord_service.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="chatbot",
            is_configured=chatbot.chatbot_has_dedicated_credentials,
            is_disconnected=lambda: is_remote_service_disconnected("chatbot"),
            attempt_reconnect=lambda: chatbot.attempt_auto_reconnect(),
        ),
        _ServiceEntry(
            key="database",
            is_configured=database_manager.database_needs_remote_monitor,
            is_disconnected=lambda: is_remote_service_disconnected("database"),
            attempt_reconnect=lambda: database_manager.attempt_auto_reconnect(),
        ),
    ]
    return entries


def _monitor_loop() -> None:
    logger.info("Connection monitor started")
    registry = _build_registry()
    while not _monitor_stop.is_set():
        try:
            if is_shutdown_in_progress():
                break

            is_internet_available()

            if _auto_reconnect_enabled() and _internet_online:
                for entry in registry:
                    if _monitor_stop.is_set() or is_shutdown_in_progress():
                        break
                    key = entry.key
                    try:
                        if not entry.is_configured():
                            continue
                        if not entry.is_disconnected():
                            continue
                        if not _reconnect_cooldown_elapsed(key):
                            continue
                        if not is_service_reachable(key):
                            continue
                        logger.info(
                            "Connection monitor attempting auto-reconnect for %s",
                            key,
                        )
                        _mark_reconnect_attempt(key)
                        entry.attempt_reconnect()
                    except Exception:
                        logger.debug(
                            "monitor reconnect failed for %s", key, exc_info=True
                        )
        except Exception:
            logger.error("Connection monitor loop error", exc_info=True)

        _monitor_stop.wait(_MONITOR_INTERVAL_SEC)

    logger.info("Connection monitor stopped")


def start() -> None:
    """Start the background connection monitor thread."""
    global _monitor_thread
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        name="ConnectionMonitor",
        daemon=True,
    )
    _monitor_thread.start()
    logger.debug("Connection monitor thread scheduled")


def stop() -> None:
    """Signal the connection monitor to stop."""
    _monitor_stop.set()
    thread = _monitor_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)

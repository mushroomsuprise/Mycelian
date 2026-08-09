#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from .dataobjects import PSNSettingsData  # PSNSettingsData for type hint
from .dataobjects import state_manager

# Assuming these modules are in paths accessible by your main script
from .psnapi import (  # PSNData for default object
    PSNClient,
    PSNData,
    PSNGameMismatch,
    find_best_fuzzy_game_name_match,
    normalize_game_name_key,
)

logger = logging.getLogger(__name__)  # Ensure logger is configured in your app


# --- Global PSN Module Variables ---
psn_client_instance: Optional[PSNClient] = None
psn_update_thread: Optional[threading.Thread] = None
stop_psn_thread_event = threading.Event()

# Updater cadence — budgeted against PSNAWP's default 1 HTTP request / 3s limiter
SLEEP_PRESENCE = 6  # Presence poll interval (also used when idle / no game)
TROPHY_TITLE_REFRESH_SEC = 18  # trophy_titles_for_title while same game
OVERALL_SUMMARY_SEC = 120  # Account-wide trophy_summary
# Skip trophy_groups_summary API when cached group structure is fresher than this
TROPHY_GROUPS_CACHE_TTL_SEC = 24 * 60 * 60

# Back-compat aliases (older code / mental model)
SLEEP_NO_GAME = SLEEP_PRESENCE
SLEEP_CACHE_HIT = SLEEP_PRESENCE
SLEEP_CACHE_MISS = SLEEP_PRESENCE


def _trophy_groups_cache_is_fresh(cached_game: dict | None) -> bool:
    """True when cached trophy_groups exist and trophy_groups_updated_at is within TTL."""
    if not cached_game:
        return False
    groups = cached_game.get("trophy_groups") or []
    if not groups:
        # Previously confirmed base-only (no multi-group) still counts as seeded
        if cached_game.get("has_trophy_groups") is not False:
            return False
    ts_raw = cached_game.get("trophy_groups_updated_at")
    if not ts_raw:
        return False
    try:
        updated_at = datetime.fromisoformat(str(ts_raw))
    except (TypeError, ValueError):
        return False
    age_sec = (datetime.now() - updated_at).total_seconds()
    return age_sec < TROPHY_GROUPS_CACHE_TTL_SEC

# Notification throttling for game mismatches
MISMATCH_NOTIFICATION_COOLDOWN = 300  # 5 minutes between notifications for same game
_mismatch_notification_times: dict[
    str, float
] = {}  # Track last notification time per game

_NPSSO_EXPIRED_NOTIFY_COOLDOWN_SEC = 45 * 60
_npsso_expired_last_notified: float = 0.0


def notify_psn_npsso_expired() -> None:
    """Notify the user once per cooldown that their NPSSO token needs updating."""
    global _npsso_expired_last_notified
    now = time.time()
    if now - _npsso_expired_last_notified < _NPSSO_EXPIRED_NOTIFY_COOLDOWN_SEC:
        return
    _npsso_expired_last_notified = now
    try:
        from .notification_engine import notify

        notify(
            "PSN NPSSO token expired — open Settings → PSN and click Connect to update.",
            type="warning",
            timeout=10000,
        )
    except Exception as e:
        logger.debug("PSN NPSSO expiry notification failed: %s", e)


# --- Initialization and Management Functions ---

_psn_init_lock = threading.Lock()


def _finalize_psn_client_state(
    current_psn_username: Optional[str],
) -> None:
    """Persist live PSN data and start the updater thread for an existing client."""
    global psn_client_instance
    if not psn_client_instance:
        return

    if psn_client_instance.authenticated:
        logger.info(
            "PSNClient connected successfully as %s.",
            psn_client_instance.psn_data.online_id,
        )
        logger.info("Account ID: %s", psn_client_instance.psn_data.account_id)
        if current_psn_username:
            logger.info(
                "PSNClient configured to track PSN username: %s",
                current_psn_username,
            )
    else:
        logger.error(
            "Failed to connect PSNClient with the stored NPSSO code during initialization."
        )
        logger.error(
            "PSNClient authenticated status: %s",
            psn_client_instance.authenticated,
        )

    state_manager.set_live_psn_data(psn_client_instance.psn_data)
    if psn_client_instance.authenticated:
        start_psn_data_updater_thread()
    else:
        logger.info("PSN updater thread not started until client connects successfully")


def _initialize_psn_module_impl() -> None:
    """Blocking PSN connect and updater setup (runs off the deferred init thread)."""
    global psn_client_instance

    with _psn_init_lock:
        if not hasattr(state_manager, "get_psn_settings_data"):
            logger.error(
                "State manager is not properly initialized or missing PSN settings method"
            )
            return

        try:
            psn_settings: Optional[PSNSettingsData] = (
                state_manager.get_psn_settings_data()
            )
            current_npsso_code = psn_settings.npsso_code if psn_settings else None
            current_psn_username = psn_settings.psn_username if psn_settings else None

            if psn_client_instance is not None:
                logger.info(
                    "PSN module already initialized; ensuring connection and updater thread"
                )
                if not psn_client_instance.authenticated and current_npsso_code:
                    logger.info("Retrying PSNClient connect on existing instance...")
                    psn_client_instance.connect()
                _finalize_psn_client_state(current_psn_username)
                return

            logger.info("=== PSN SERVICE INITIALIZATION STARTING ===")
            logger.info(f"Retrieved PSN settings from state manager: {psn_settings}")
            logger.info(f"NPSSO code present: {bool(current_npsso_code)}")
            logger.info(f"PSN username: {current_psn_username}")

            if not current_npsso_code:
                logger.info(
                    "No NPSSO code found in settings. PSNClient not started by PSN Service."
                )
                empty_psn_data = PSNData()
                empty_psn_data.connection_status = "Not Connected"
                state_manager.set_live_psn_data(empty_psn_data)
                logger.info("=== PSN SERVICE INITIALIZATION COMPLETE ===")
                return

            logger.info(
                "NPSSO code found in settings. Initializing PSNClient for PSN Service."
            )
            psn_client_instance = PSNClient(
                npsso_code=current_npsso_code, psn_username=current_psn_username
            )
            logger.info("Attempting to connect PSNClient...")
            psn_client_instance.connect()
            _finalize_psn_client_state(current_psn_username)

        except Exception as e:
            logger.exception(f"Exception during PSN service initialization: {e}")

        logger.info("=== PSN SERVICE INITIALIZATION COMPLETE ===")


def initialize_psn_module() -> None:
    """Start PSN initialization in the background so deferred startup is not blocked."""
    threading.Thread(
        target=_initialize_psn_module_impl,
        daemon=True,
        name="PSNInit",
    ).start()


def psn_data_update_loop():
    """Periodically fetches data from PSN if the client is initialized.

    Cadence (budgeted against PSNAWP ~1 req / 3s):
    - Presence every SLEEP_PRESENCE
    - Title trophies (trophy_titles_for_title) every TROPHY_TITLE_REFRESH_SEC, or on game change
    - Group trophies (trophy_groups_summary) only when cache groups are missing/stale (>24h)
    - Overall trophy summary every OVERALL_SUMMARY_SEC
    """
    global psn_client_instance
    if not psn_client_instance:
        logger.error(
            "PSNClient not initialized at start of PSN update loop. Thread exiting."
        )
        return

    logger.info("=== PSN DATA UPDATER THREAD STARTED ===")
    update_count = 0
    last_overall_summary_at = 0.0
    last_title_trophy_at = 0.0
    tracked_np_title_id: str | None = None
    force_title_refresh = True

    while not stop_psn_thread_event.is_set():
        sleep_time = SLEEP_PRESENCE

        try:
            update_count += 1
            now = time.time()
            logger.debug(f"PSN update loop iteration #{update_count}")

            if not psn_client_instance.psn_data.npsso_code:
                logger.debug(
                    "PSNClient has no NPSSO code configured in its data. Update loop pausing activity."
                )
                state_manager.set_live_psn_data(psn_client_instance.psn_data)
                stop_psn_thread_event.wait(SLEEP_PRESENCE)
                continue

            if not psn_client_instance.is_connected():
                if getattr(psn_client_instance, "is_auth_expired", lambda: False)():
                    logger.debug(
                        "Skipping PSN reconnect in update loop — NPSSO token expired"
                    )
                    state_manager.set_live_psn_data(psn_client_instance.psn_data)
                    stop_psn_thread_event.wait(SLEEP_PRESENCE)
                    continue
                logger.info(
                    "PSNClient not connected. Attempting to connect in update loop..."
                )
                try:
                    from .connection_monitor import (
                        is_internet_available,
                        is_service_reachable,
                    )

                    if not is_internet_available() or not is_service_reachable("psn"):
                        logger.debug(
                            "Skipping PSN reconnect in update loop — connectivity check failed"
                        )
                        state_manager.set_live_psn_data(psn_client_instance.psn_data)
                        stop_psn_thread_event.wait(SLEEP_PRESENCE)
                        continue
                except Exception:
                    logger.debug(
                        "Connectivity check unavailable for PSN update loop",
                        exc_info=True,
                    )

                connection_result = psn_client_instance.connect()
                logger.info(f"Reconnection attempt result: {connection_result}")

                if not connection_result:
                    logger.warning(
                        "PSNClient remains disconnected after connection attempt in loop."
                    )
                    state_manager.set_live_psn_data(psn_client_instance.psn_data)
                    stop_psn_thread_event.wait(SLEEP_PRESENCE)
                    continue

            logger.debug("PSNClient is connected. Fetching updates...")

            # --- Presence (every cycle) ---
            presence_result = psn_client_instance.get_presence()
            if not presence_result:
                logger.debug("No presence data available")
                tracked_np_title_id = None
                force_title_refresh = True
                _clear_game_data_if_needed()
                _update_and_broadcast()
                stop_psn_thread_event.wait(SLEEP_PRESENCE)
                continue

            logger.info(f"User online status: {psn_client_instance.psn_data.is_online}")
            logger.info(
                f"Current game: {psn_client_instance.psn_data.current_game_name}"
            )

            # --- Overall trophy summary (slow cadence) ---
            if now - last_overall_summary_at >= OVERALL_SUMMARY_SEC:
                logger.debug("Fetching overall trophy summary...")
                trophy_summary_result = (
                    psn_client_instance.get_overall_trophy_summary()
                )
                last_overall_summary_at = time.time()
                if trophy_summary_result:
                    logger.info(
                        f"Trophy counts: {psn_client_instance.psn_data.trophy_counts}"
                    )
                else:
                    logger.warning("Failed to fetch trophy summary")
                    if getattr(
                        psn_client_instance, "is_auth_expired", lambda: False
                    )():
                        state_manager.set_live_psn_data(psn_client_instance.psn_data)
                        stop_psn_thread_event.wait(SLEEP_PRESENCE)
                        continue

            current_game_presence = psn_client_instance.psn_data.presence
            basic_presence = (
                current_game_presence.get("basicPresence", current_game_presence)
                if current_game_presence
                else {}
            )
            game_info_list = basic_presence.get("gameTitleInfoList", [])

            if not game_info_list:
                logger.debug(
                    "User not currently playing a game or no game info available"
                )
                tracked_np_title_id = None
                force_title_refresh = True
                _clear_game_data_if_needed()
                _update_and_broadcast()
                logger.debug(
                    f"PSN update loop iteration #{update_count} complete, no game - waiting {SLEEP_PRESENCE}s..."
                )
                stop_psn_thread_event.wait(SLEEP_PRESENCE)
                continue

            game_info = game_info_list[0]
            np_title_id = game_info.get("npTitleId")
            platform = game_info.get("format")
            presence_game_name = game_info.get("titleName")

            logger.info(f"Currently playing: {presence_game_name}")
            logger.debug(f"Game info - npTitleId: {np_title_id}, format: {platform}")

            if not np_title_id or not platform:
                logger.warning(
                    f"Missing npTitleId or format for game {presence_game_name}"
                )
                _update_and_broadcast()
                stop_psn_thread_event.wait(SLEEP_PRESENCE)
                continue

            if np_title_id != tracked_np_title_id:
                logger.info(
                    "PSN game changed: %r -> %r", tracked_np_title_id, np_title_id
                )
                tracked_np_title_id = np_title_id
                force_title_refresh = True

            # Resolve np_communication_id from cache when available
            cached_game = psn_client_instance.find_game_by_np_title_id(np_title_id)
            if not cached_game:
                cached_game = psn_client_instance.find_game_by_name(
                    presence_game_name, platform=platform
                )
            np_communication_id = (
                cached_game.get("np_communication_id") if cached_game else None
            )
            if np_communication_id:
                psn_client_instance.psn_data.current_game_np_comm_id = (
                    np_communication_id
                )
                psn_client_instance.psn_data.current_game_mismatch = None

            # --- Title-level trophies (hot path; 1 request) ---
            need_title = force_title_refresh or (
                now - last_title_trophy_at >= TROPHY_TITLE_REFRESH_SEC
            )
            title_result = None
            if need_title:
                logger.debug(
                    "Fetching title trophy progress for %s (%s)",
                    presence_game_name,
                    np_title_id,
                )
                title_result = psn_client_instance.get_title_trophy_progress(np_title_id)
                last_title_trophy_at = time.time()
                force_title_refresh = False

                if title_result:
                    np_communication_id = (
                        title_result.get("np_communication_id") or np_communication_id
                    )
                    psn_client_instance.psn_data.current_game_mismatch = None
                    # Persist / refresh mapping for this title without wiping trophy_groups
                    if np_communication_id:
                        mapping_updates = {
                            "np_title_id": np_title_id,
                            "presence_name": presence_game_name,
                            "trophy_name": title_result.get("title_name")
                            or presence_game_name,
                            "cover_art_url": title_result.get("icon_url")
                            or (
                                cached_game.get("cover_art_url")
                                if cached_game
                                else None
                            ),
                            "platform": platform,
                            "has_trophy_groups": title_result.get(
                                "has_trophy_groups", False
                            ),
                        }
                        existing = psn_client_instance.get_cached_game_data(
                            np_communication_id
                        )
                        if existing:
                            # Keep richer group flag once trophy_groups have been seeded
                            if existing.get("trophy_groups") or existing.get(
                                "trophy_groups_updated_at"
                            ):
                                mapping_updates.pop("has_trophy_groups", None)
                            psn_client_instance.update_game_cache_entry(
                                np_communication_id, mapping_updates
                            )
                            cached_game = (
                                psn_client_instance.get_cached_game_data(
                                    np_communication_id
                                )
                                or existing
                            )
                        else:
                            store_entry = {
                                "np_communication_id": np_communication_id,
                                **mapping_updates,
                                "trophy_groups": [],
                                "last_updated": datetime.now().isoformat(),
                            }
                            psn_client_instance.store_game_data(store_entry)
                            cached_game = store_entry
                    logger.info(
                        "Current game trophy progress: %s%%",
                        psn_client_instance.psn_data.current_game_progress,
                    )
                else:
                    logger.warning(
                        "Failed to fetch title trophies for %s", presence_game_name
                    )
                    if getattr(
                        psn_client_instance, "is_auth_expired", lambda: False
                    )():
                        state_manager.set_live_psn_data(psn_client_instance.psn_data)
                        stop_psn_thread_event.wait(SLEEP_PRESENCE)
                        continue

                    # Cache miss fallback: bulk list only if targeted lookup failed
                    if not np_communication_id:
                        logger.info(
                            "Cache MISS / targeted title lookup failed for '%s' — trying bulk fallback",
                            presence_game_name,
                        )
                        np_communication_id = _resolve_game_via_bulk_fallback(
                            presence_game_name, np_title_id, platform
                        )
                        if np_communication_id:
                            cached_game = psn_client_instance.get_cached_game_data(
                                np_communication_id
                            )

            # --- Group trophies: only when cache structure missing or older than 24h ---
            if np_communication_id and (
                cached_game is None
                or cached_game.get("np_communication_id") != np_communication_id
            ):
                cached_game = psn_client_instance.get_cached_game_data(
                    np_communication_id
                )

            should_refresh_groups = bool(np_communication_id) and (
                not _trophy_groups_cache_is_fresh(cached_game)
            )
            if should_refresh_groups:
                logger.debug(
                    "Fetching group trophy progress for %s (%s)",
                    presence_game_name,
                    np_communication_id,
                )
                groups_result = psn_client_instance.get_game_trophies(
                    np_communication_id=np_communication_id, platform=platform
                )
                if groups_result:
                    groups_list = groups_result.get("trophy_groups") or []
                    groups_updated_at = datetime.now().isoformat()
                    cache_updates = {
                        "trophy_groups": groups_list,
                        "has_trophy_groups": len(groups_list) > 1,
                        "trophy_groups_updated_at": groups_updated_at,
                    }
                    if psn_client_instance.get_cached_game_data(np_communication_id):
                        psn_client_instance.update_game_cache_entry(
                            np_communication_id, cache_updates
                        )
                    else:
                        psn_client_instance.store_game_data(
                            {
                                "np_communication_id": np_communication_id,
                                "np_title_id": np_title_id,
                                "presence_name": presence_game_name,
                                "trophy_name": presence_game_name,
                                "platform": platform,
                                "trophy_groups": groups_list,
                                "has_trophy_groups": len(groups_list) > 1,
                                "trophy_groups_updated_at": groups_updated_at,
                                "last_updated": groups_updated_at,
                            }
                        )
                    cached_game = psn_client_instance.get_cached_game_data(
                        np_communication_id
                    )
                    logger.info(
                        "Group trophy progress: %s%% trophies=%s (cached %s group(s))",
                        psn_client_instance.psn_data.current_game_progress,
                        psn_client_instance.psn_data.current_game_trophies,
                        len(groups_list),
                    )
                else:
                    logger.warning(
                        "Failed to fetch group trophies for %s", presence_game_name
                    )
                    if getattr(
                        psn_client_instance, "is_auth_expired", lambda: False
                    )():
                        state_manager.set_live_psn_data(psn_client_instance.psn_data)
                        stop_psn_thread_event.wait(SLEEP_PRESENCE)
                        continue
            elif np_communication_id:
                logger.debug(
                    "Skipping group trophy API for %s — cache fresh (%s group(s))",
                    presence_game_name,
                    len((cached_game or {}).get("trophy_groups") or []),
                )

            if not np_communication_id and not title_result:
                # Still unresolved after targeted + optional bulk
                if need_title:
                    mismatch = PSNGameMismatch(
                        presence_name=presence_game_name,
                        np_title_id=np_title_id,
                        platform=platform,
                        detected_at=datetime.now().isoformat(),
                        notified=False,
                    )
                    psn_client_instance.psn_data.current_game_mismatch = mismatch
                    logger.info(
                        f"Created mismatch record for game: {presence_game_name}"
                    )

            _update_and_broadcast()
            logger.debug(
                "PSN update loop iteration #%s complete, waiting %ss...",
                update_count,
                sleep_time,
            )

        except Exception as e:
            logger.exception(
                f"Error during PSN data update loop iteration #{update_count}: {e}"
            )
            if psn_client_instance:
                state_manager.set_live_psn_data(psn_client_instance.psn_data)
            sleep_time = SLEEP_PRESENCE

        stop_psn_thread_event.wait(sleep_time)

    logger.info("=== PSN DATA UPDATER THREAD FINISHED ===")


def _resolve_game_via_bulk_fallback(
    presence_game_name: str, np_title_id: str, platform: str
) -> str | None:
    """Rare path: bulk trophy_titles list + name match, then store the current game only."""
    global psn_client_instance
    if not psn_client_instance:
        return None

    all_games_result = psn_client_instance.get_all_games()
    bulk_fetch_failed = all_games_result is None
    all_games = all_games_result if all_games_result is not None else {}
    np_communication_id = None

    presence_key = normalize_game_name_key(presence_game_name)
    if all_games:
        for game_id, game_data in all_games.items():
            game_name = game_data.get("name") or ""
            if game_name and (
                game_name.lower() == presence_game_name.lower()
                or normalize_game_name_key(game_name) == presence_key
            ):
                np_communication_id = game_id
                logger.info(
                    "Found matching game in all_games: %s -> %s",
                    game_name,
                    np_communication_id,
                )
                break

        if not np_communication_id:
            # Inject id onto candidates so fuzzy helper can return a usable dict
            fuzzy_candidates = [
                {**game_data, "np_communication_id": game_id}
                for game_id, game_data in all_games.items()
            ]
            fuzzy_hit = find_best_fuzzy_game_name_match(
                presence_game_name,
                fuzzy_candidates,
                platform=platform,
                name_fields=("name",),
            )
            if fuzzy_hit:
                np_communication_id = fuzzy_hit.get("np_communication_id")
                logger.info(
                    "Fuzzy matched game in all_games: %r -> %r (%s)",
                    presence_game_name,
                    fuzzy_hit.get("name"),
                    np_communication_id,
                )

    if not np_communication_id and np_title_id:
        resolved = psn_client_instance.resolve_np_communication_id_from_np_title_id(
            np_title_id
        )
        if resolved:
            np_communication_id = resolved

    if not np_communication_id:
        logger.warning(
            "Could not resolve game for cache: presence_name=%r np_title_id=%r "
            "all_games_empty=%s bulk_fetch_failed=%s",
            presence_game_name,
            np_title_id,
            not bool(all_games),
            bulk_fetch_failed,
        )
        return None

    game_meta = all_games.get(np_communication_id, {}) if all_games else {}
    single_entry = {
        "np_communication_id": np_communication_id,
        "np_title_id": np_title_id,
        "presence_name": presence_game_name,
        "trophy_name": game_meta.get("name") or presence_game_name,
        "cover_art_url": game_meta.get("icon_url"),
        "platform": game_meta.get("platform") or platform,
        "trophy_groups": [],
        "last_updated": datetime.now().isoformat(),
    }
    psn_client_instance.store_game_data(single_entry)
    psn_client_instance.psn_data.current_game_np_comm_id = np_communication_id
    psn_client_instance.psn_data.current_game_mismatch = None
    logger.info(
        "Stored current game to database cache (bulk/title-id fallback): %s",
        np_communication_id,
    )
    return np_communication_id



def _clear_game_data_if_needed():
    """Helper to clear game data when no game is being played."""
    global psn_client_instance
    if psn_client_instance and (
        psn_client_instance.psn_data.current_game_name is not None
        or psn_client_instance.psn_data.current_game_art_url is not None
    ):
        logger.info("Clearing game data - no game currently being played")
        psn_client_instance.psn_data.current_game_name = None
        psn_client_instance.psn_data.current_game_art_url = None
        psn_client_instance.psn_data.current_game_np_comm_id = None
        psn_client_instance.psn_data.current_game_trophies = {}
        psn_client_instance.psn_data.current_game_trophies_all = {}
        psn_client_instance.psn_data.current_game_progress = None
        psn_client_instance.psn_data.current_game_mismatch = (
            None  # Clear mismatch when offline/no game
        )


def _update_and_broadcast():
    """Helper to update state manager and broadcast to WebSocket clients."""
    global psn_client_instance, _mismatch_notification_times
    if not psn_client_instance:
        return

    # Update StateManager with the latest client data
    logger.debug("Updating state manager with latest PSN data...")
    state_manager.set_live_psn_data(psn_client_instance.psn_data)
    logger.debug(
        f"Updated state manager - Online: {psn_client_instance.psn_data.is_online}, Game: {psn_client_instance.psn_data.current_game_name}"
    )

    # Broadcast PSN data updates to WebSocket clients for real-time updates
    try:
        from . import web_engine

        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
        ):
            # Convert PSN data to dict for JSON serialization
            import dataclasses

            psn_data_dict = dataclasses.asdict(psn_client_instance.psn_data)

            # Emit the updated data to all connected clients
            web_engine.web_engine_instance.safe_emit("psn_data_update", psn_data_dict)
            logger.debug(
                f"Broadcasted PSN data update to WebSocket clients: {psn_client_instance.psn_data.current_game_name}"
            )

            # Handle mismatch notification with throttling
            mismatch = psn_client_instance.psn_data.current_game_mismatch
            if mismatch and not mismatch.notified:
                current_time = time.time()
                last_notified = _mismatch_notification_times.get(
                    mismatch.presence_name, 0
                )

                if current_time - last_notified >= MISMATCH_NOTIFICATION_COOLDOWN:
                    # Emit mismatch notification event
                    mismatch_data = dataclasses.asdict(mismatch)
                    web_engine.web_engine_instance.safe_emit(
                        "psn_game_mismatch", mismatch_data
                    )
                    logger.info(
                        f"Broadcasted mismatch notification for game: {mismatch.presence_name}"
                    )

                    # Update tracking
                    _mismatch_notification_times[mismatch.presence_name] = current_time
                    mismatch.notified = True
                else:
                    logger.debug(
                        f"Skipping mismatch notification for {mismatch.presence_name} - cooldown active"
                    )
        else:
            logger.debug(
                "Web engine instance not available for broadcasting PSN updates"
            )
    except Exception as e:
        logger.debug(
            f"Error broadcasting PSN data update to WebSocket clients: {str(e)}"
        )


def start_psn_data_updater_thread():
    """Starts the PSN data updater thread if not already running and client is somewhat configured."""
    global psn_update_thread, psn_client_instance

    logger.info("=== STARTING PSN DATA UPDATER THREAD ===")

    if not psn_client_instance:
        logger.warning(
            "Cannot start PSN update thread (PSN Service): PSNClient not initialized."
        )
        return

    if not psn_client_instance.authenticated:
        logger.debug("Not starting PSN update thread: PSNClient is not connected yet.")
        return

    # Only start if there's an NPSSO code, otherwise the loop doesn't do much.
    if not psn_client_instance.psn_data.npsso_code:
        logger.debug("Not starting PSN update thread: No NPSSO code in PSNClient data.")
        return

    if psn_update_thread is None or not psn_update_thread.is_alive():
        logger.info("Starting PSN data updater thread (PSN Service)...")
        stop_psn_thread_event.clear()
        psn_update_thread = threading.Thread(
            target=psn_data_update_loop, daemon=True, name="PSNUpdateThread"
        )
        psn_update_thread.start()
        logger.info(
            f"PSN update thread started successfully. Thread alive: {psn_update_thread.is_alive()}"
        )
    else:
        logger.info("PSN data updater thread (PSN Service) already running.")


def stop_psn_data_updater_thread(*, join_timeout: float = 5.0) -> None:
    """Stops the PSN data updater thread."""
    global psn_update_thread
    logger.info("=== STOPPING PSN DATA UPDATER THREAD ===")

    if psn_update_thread and psn_update_thread.is_alive():
        logger.info("Attempting to stop PSN data updater thread (PSN Service)...")
        stop_psn_thread_event.set()
        psn_update_thread.join(timeout=join_timeout)
        if psn_update_thread.is_alive():
            logger.warning(
                "PSN data updater thread (PSN Service) did not stop within %.1fs.",
                join_timeout,
            )
        else:
            logger.info("PSN data updater thread (PSN Service) stopped successfully.")
        psn_update_thread = None
    else:
        logger.info(
            "PSN data updater thread (PSN Service) not running or already stopped."
        )


def handle_psn_settings_change():
    """Handles changes to PSN settings (NPSSO code and PSN username).
    Updates the PSNClient and restarts the update thread if necessary.
    This should be called after settings are saved.
    """
    global psn_client_instance
    logger.info("=== HANDLING PSN SETTINGS CHANGE ===")

    try:
        psn_settings = state_manager.get_psn_settings_data()
        new_npsso_code = psn_settings.npsso_code if psn_settings else None
        new_psn_username = psn_settings.psn_username if psn_settings else None

        logger.info(f"New NPSSO code present: {bool(new_npsso_code)}")
        logger.info(f"New PSN username: {new_psn_username}")

        settings_changed_or_client_needs_init = False
        if psn_client_instance:
            # Compare with the settings stored within the client
            old_npsso = psn_client_instance.psn_data.npsso_code
            old_username = psn_client_instance.psn_username
            logger.info(
                f"Comparing settings - Old NPSSO present: {bool(old_npsso)}, Old username: {old_username}"
            )

            if old_npsso != new_npsso_code or old_username != new_psn_username:
                settings_changed_or_client_needs_init = True
                logger.info("Settings have changed")
        elif new_npsso_code:
            settings_changed_or_client_needs_init = True
            logger.info("No existing client but new NPSSO code provided")

        if not settings_changed_or_client_needs_init:
            logger.info(
                "PSN settings have not changed or no new code for uninitialized client. Verifying thread status."
            )
            # If client exists, has code, but thread isn't running, start it.
            if (
                psn_client_instance
                and psn_client_instance.psn_data.npsso_code
                and (psn_update_thread is None or not psn_update_thread.is_alive())
            ):
                logger.info(
                    "PSN update thread was not running for configured client, starting it now."
                )
                start_psn_data_updater_thread()
            return

        logger.info(
            f"PSN settings change detected/required for PSN Service. New code: {'Present' if new_npsso_code else 'Absent'}, Username: {new_psn_username or 'None'}"
        )

        logger.info("Stopping existing PSN update thread...")
        stop_psn_data_updater_thread()

        if new_npsso_code:
            if psn_client_instance:
                logger.info("Updating existing PSNClient with new credentials...")
                psn_client_instance.update_credentials(
                    new_npsso_code, new_psn_username or ""
                )
            else:
                logger.info("Creating new PSNClient instance...")
                psn_client_instance = PSNClient(
                    npsso_code=new_npsso_code, psn_username=new_psn_username
                )

            logger.info(
                "Attempting to connect with new/updated PSN settings in PSN Service..."
            )
            connection_result = psn_client_instance.connect()
            logger.info(f"Connection result: {connection_result}")

            if psn_client_instance.is_connected():
                logger.info(
                    f"PSNClient (PSN Service) connected successfully as {psn_client_instance.psn_data.online_id}."
                )
                if new_psn_username:
                    logger.info(
                        f"PSNClient configured to track PSN username: {new_psn_username}"
                    )
            else:
                logger.error(
                    "Failed to connect PSNClient (PSN Service) with the new/updated settings."
                )

            logger.info("Storing updated PSN data in state manager...")
            state_manager.set_live_psn_data(psn_client_instance.psn_data)

            # Broadcast PSN data updates to WebSocket clients for immediate updates
            try:
                from . import web_engine

                if (
                    hasattr(web_engine, "web_engine_instance")
                    and web_engine.web_engine_instance
                ):
                    # Convert PSN data to dict for JSON serialization
                    import dataclasses

                    psn_data_dict = dataclasses.asdict(psn_client_instance.psn_data)

                    # Emit the updated data to all connected clients
                    web_engine.web_engine_instance.safe_emit(
                        "psn_data_update", psn_data_dict
                    )
                    logger.debug(
                        f"Broadcasted PSN settings change to WebSocket clients: {psn_client_instance.psn_data.current_game_name}"
                    )
                else:
                    logger.debug(
                        "Web engine instance not available for broadcasting PSN settings change"
                    )
            except Exception as e:
                logger.debug(
                    f"Error broadcasting PSN settings change to WebSocket clients: {str(e)}"
                )

            logger.info("Starting PSN update thread with new settings...")
            start_psn_data_updater_thread()
        else:
            logger.info(
                "NPSSO code removed. PSNClient (PSN Service) will be deactivated."
            )
            if psn_client_instance:
                psn_client_instance.update_credentials("", "")
                state_manager.set_live_psn_data(
                    psn_client_instance.psn_data
                )  # Store the now-deactivated client's state

                # Broadcast empty PSN data to WebSocket clients
                try:
                    from . import web_engine

                    if (
                        hasattr(web_engine, "web_engine_instance")
                        and web_engine.web_engine_instance
                    ):
                        # Convert PSN data to dict for JSON serialization
                        import dataclasses

                        psn_data_dict = dataclasses.asdict(psn_client_instance.psn_data)

                        # Emit the empty data to all connected clients
                        web_engine.web_engine_instance.safe_emit(
                            "psn_data_update", psn_data_dict
                        )
                        logger.debug(
                            "Broadcasted PSN deactivation to WebSocket clients"
                        )
                    else:
                        logger.debug(
                            "Web engine instance not available for broadcasting PSN deactivation"
                        )
                except Exception as e:
                    logger.debug(
                        f"Error broadcasting PSN deactivation to WebSocket clients: {str(e)}"
                    )
            else:
                # If no client instance, ensure live data reflects no configuration
                empty_psn_data = PSNData()
                state_manager.set_live_psn_data(empty_psn_data)

                # Broadcast empty PSN data to WebSocket clients
                try:
                    from . import web_engine

                    if (
                        hasattr(web_engine, "web_engine_instance")
                        and web_engine.web_engine_instance
                    ):
                        # Convert empty PSN data to dict for JSON serialization
                        import dataclasses

                        psn_data_dict = dataclasses.asdict(empty_psn_data)

                        # Emit the empty data to all connected clients
                        web_engine.web_engine_instance.safe_emit(
                            "psn_data_update", psn_data_dict
                        )
                        logger.debug("Broadcasted empty PSN data to WebSocket clients")
                    else:
                        logger.debug(
                            "Web engine instance not available for broadcasting empty PSN data"
                        )
                except Exception as e:
                    logger.debug(
                        f"Error broadcasting empty PSN data to WebSocket clients: {str(e)}"
                    )

    except Exception as e:
        logger.exception(f"Exception while handling PSN settings change: {e}")

    logger.info("=== PSN SETTINGS CHANGE HANDLING COMPLETE ===")


def get_psn_client() -> Optional[PSNClient]:
    """Return the global PSN client instance, if initialized."""
    return psn_client_instance


def get_psn_status_label() -> str:
    """Return the canonical PSN connection status label for footer and settings."""
    from .connection_status_tracker import psn_configured

    if not psn_configured():
        return "Not Connected"

    if psn_client_instance and (psn_client_instance.psn_data.npsso_code or "").strip():
        status = (
            getattr(psn_client_instance.psn_data, "connection_status", "") or ""
        ).strip()
        if status and status != "Disconnected":
            return status

    live = state_manager.get_live_psn_data()
    if live:
        status = (getattr(live, "connection_status", "") or "").strip()
        if status and status != "Disconnected":
            return status
        if getattr(live, "is_online", False):
            return "Connected"

    if psn_client_instance and not psn_client_instance.is_connected():
        return "Token Expired"

    return "Offline"


def get_psn_status_display() -> tuple[str, str, str]:
    """Return (status_text, user_text, theme_color_class) for PSN settings UI."""
    from .connection_status_tracker import get_connectivity_overlay

    overlay = get_connectivity_overlay("psn")
    if overlay:
        return overlay, "N/A", "text-theme-error"

    psn_settings = state_manager.get_psn_settings_data()
    target_username = psn_settings.psn_username if psn_settings else None
    live_psn_data = state_manager.get_live_psn_data()
    status = get_psn_status_label()

    if status == "Not Connected":
        return "Not Connected", "N/A", "text-theme-error"

    if status in ("Token Expired", "Authentication Failed"):
        return status, "N/A", "text-theme-error"

    online_id = ""
    if live_psn_data and live_psn_data.online_id:
        online_id = str(live_psn_data.online_id)
    elif psn_client_instance and psn_client_instance.psn_data.online_id:
        online_id = str(psn_client_instance.psn_data.online_id)

    if status == "Connected":
        if target_username:
            return (
                f"Connected - Tracking {target_username}",
                f"{target_username} (target)",
                "text-theme-success",
            )
        return (
            f"Connected as {online_id}" if online_id else "Connected",
            f"{online_id} (own account)" if online_id else "Unknown",
            "text-theme-success",
        )

    if status == "Offline":
        if target_username:
            return (
                f"Offline - Tracking {target_username}",
                f"{target_username} (target)",
                "text-theme-warning",
            )
        return (
            "Offline",
            f"{online_id} (own account)" if online_id else "Unknown",
            "text-theme-warning",
        )

    return status, "N/A", "text-theme-warning"


def psn_configured_for_monitor() -> bool:
    from .connection_status_tracker import psn_configured

    return psn_configured()


def is_psn_api_disconnected() -> bool:
    if not psn_client_instance:
        return False
    if not (psn_client_instance.psn_data.npsso_code or "").strip():
        return False
    return not psn_client_instance.is_connected()


def attempt_auto_reconnect() -> bool:
    if not psn_client_instance:
        return False
    if not (psn_client_instance.psn_data.npsso_code or "").strip():
        return False
    try:
        from .connection_monitor import (
            is_internet_available,
            is_service_reachable,
        )

        if not is_internet_available() or not is_service_reachable("psn"):
            return False
    except Exception:
        return False
    return bool(psn_client_instance.connect())

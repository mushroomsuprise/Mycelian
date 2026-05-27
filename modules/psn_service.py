#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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
from .psnapi import PSNClient, PSNData, PSNGameMismatch  # PSNData for default object

logger = logging.getLogger(__name__)  # Ensure logger is configured in your app


def _normalize_psn_game_title(name: str) -> str:
    """Casefold, strip common trademark symbols, collapse whitespace for title matching."""
    if not name:
        return ""
    s = name.casefold()
    for ch in ("\u2122", "\u00ae"):
        s = s.replace(ch, "")
    return " ".join(s.split())


# --- Global PSN Module Variables ---
psn_client_instance: Optional[PSNClient] = None
psn_update_thread: Optional[threading.Thread] = None
stop_psn_thread_event = threading.Event()

# Sleep times for the update loop
SLEEP_NO_GAME = 20  # Sleep time when no game is being played
SLEEP_CACHE_HIT = 10  # Sleep time when game data was found in cache
SLEEP_CACHE_MISS = 15  # Sleep time when game data had to be fetched from API

# Notification throttling for game mismatches
MISMATCH_NOTIFICATION_COOLDOWN = 300  # 5 minutes between notifications for same game
_mismatch_notification_times: dict[
    str, float
] = {}  # Track last notification time per game

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
        logger.info(
            "PSN updater thread not started until client connects successfully"
        )


def initialize_psn_module():
    """Initialize the PSNClient based on stored settings and starts the update thread.
    Should be called after state_manager is initialized.
    Idempotent: concurrent or repeated calls reuse one client and one connect attempt.
    """
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
            current_psn_username = (
                psn_settings.psn_username if psn_settings else None
            )

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
                state_manager.set_live_psn_data(PSNData())
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


def psn_data_update_loop():
    """Periodically fetches data from PSN if the client is initialized.
    Updates the StateManager with the new PSNData.

    Flow:
    1. Query presence data to get current game
    2. If no game, sleep longer and restart
    3. Check database for cached game info
    4. If cached, use it; if not, fetch from API and store
    5. Fetch user trophy progress (always fresh)
    6. Update state and broadcast
    """
    global psn_client_instance
    if not psn_client_instance:
        logger.error(
            "PSNClient not initialized at start of PSN update loop. Thread exiting."
        )
        return

    logger.info("=== PSN DATA UPDATER THREAD STARTED ===")
    update_count = 0

    while not stop_psn_thread_event.is_set():
        sleep_time = SLEEP_NO_GAME  # Default to longer sleep

        try:
            update_count += 1
            logger.debug(f"PSN update loop iteration #{update_count}")

            # Check if client is configured with a code from its own data
            if not psn_client_instance.psn_data.npsso_code:
                logger.debug(
                    "PSNClient has no NPSSO code configured in its data. Update loop pausing activity."
                )
                state_manager.set_live_psn_data(psn_client_instance.psn_data)
                stop_psn_thread_event.wait(SLEEP_NO_GAME)
                continue

            logger.debug("PSNClient has NPSSO code configured")

            # Ensure we're connected
            if not psn_client_instance.is_connected():
                logger.info(
                    "PSNClient not connected. Attempting to connect in update loop..."
                )
                connection_result = psn_client_instance.connect()
                logger.info(f"Reconnection attempt result: {connection_result}")

                if not connection_result:
                    logger.warning(
                        "PSNClient remains disconnected after connection attempt in loop."
                    )
                    state_manager.set_live_psn_data(psn_client_instance.psn_data)
                    stop_psn_thread_event.wait(SLEEP_NO_GAME)
                    continue

            logger.debug("PSNClient is connected. Fetching updates...")

            # Step 1: Fetch presence data
            logger.debug("Fetching PSN presence data...")
            presence_result = psn_client_instance.get_presence()

            if not presence_result:
                logger.debug("No presence data available")
                _clear_game_data_if_needed()
                _update_and_broadcast()
                stop_psn_thread_event.wait(SLEEP_NO_GAME)
                continue

            logger.debug(
                f"Presence data keys: {list(presence_result.keys()) if isinstance(presence_result, dict) else 'Not a dict'}"
            )
            logger.info(f"User online status: {psn_client_instance.psn_data.is_online}")
            logger.info(
                f"Current game: {psn_client_instance.psn_data.current_game_name}"
            )

            # Fetch overall trophy summary (always)
            logger.debug("Fetching overall trophy summary...")
            trophy_summary_result = psn_client_instance.get_overall_trophy_summary()
            if trophy_summary_result:
                logger.info(
                    f"Trophy counts: {psn_client_instance.psn_data.trophy_counts}"
                )
            else:
                logger.exception("Failed to fetch trophy summary")

            # Step 1a/1b: Check if playing a game
            current_game_presence = psn_client_instance.psn_data.presence
            basic_presence = (
                current_game_presence.get("basicPresence", current_game_presence)
                if current_game_presence
                else {}
            )
            game_info_list = basic_presence.get("gameTitleInfoList", [])

            if not game_info_list or len(game_info_list) == 0:
                # No game being played - clear data and sleep longer
                logger.debug(
                    "User not currently playing a game or no game info available"
                )
                _clear_game_data_if_needed()
                _update_and_broadcast()
                logger.debug(
                    f"PSN update loop iteration #{update_count} complete, no game - waiting {SLEEP_NO_GAME} seconds..."
                )
                stop_psn_thread_event.wait(SLEEP_NO_GAME)
                continue

            # Game is being played
            game_info = game_info_list[0]
            np_title_id = game_info.get("npTitleId")  # This is from presence data
            platform = game_info.get("format")
            presence_game_name = game_info.get("titleName")

            logger.info(f"Currently playing: {presence_game_name}")
            logger.debug(f"Game info - npTitleId: {np_title_id}, format: {platform}")

            if not np_title_id or not platform:
                logger.exception(
                    f"Missing npTitleId or format for game {presence_game_name}"
                )
                _update_and_broadcast()
                stop_psn_thread_event.wait(SLEEP_CACHE_MISS)
                continue

            # Step 2: Check database for cached game info
            cached_game = None
            np_communication_id = None
            cache_hit = False

            # First try to find by np_title_id
            cached_game = psn_client_instance.find_game_by_np_title_id(np_title_id)

            if not cached_game:
                # Try to find by game name
                cached_game = psn_client_instance.find_game_by_name(presence_game_name)

            if cached_game:
                # Step 2a: Cache hit - use cached data
                cache_hit = True
                np_communication_id = cached_game.get("np_communication_id")
                logger.info(
                    f"Cache HIT for game '{presence_game_name}' -> np_communication_id: {np_communication_id}"
                )

                # Set current game data from cache
                psn_client_instance.psn_data.current_game_np_comm_id = (
                    np_communication_id
                )
                # Clear any existing mismatch since we found a match
                psn_client_instance.psn_data.current_game_mismatch = None
                sleep_time = SLEEP_CACHE_HIT
            else:
                # Step 2b: Cache miss - need to fetch from API
                logger.info(
                    f"Cache MISS for game '{presence_game_name}' - fetching from API..."
                )

                all_games_result = psn_client_instance.get_all_games()
                bulk_fetch_failed = all_games_result is None
                all_games = all_games_result if all_games_result is not None else {}
                np_communication_id = None

                presence_key = _normalize_psn_game_title(presence_game_name)
                if all_games:
                    for game_id, game_data in all_games.items():
                        game_name = game_data.get("name") or ""
                        if game_name and (
                            game_name.lower() == presence_game_name.lower()
                            or _normalize_psn_game_title(game_name) == presence_key
                        ):
                            np_communication_id = game_id
                            logger.info(
                                f"Found matching game in all_games: {game_name} -> {np_communication_id}"
                            )
                            break

                if not np_communication_id and np_title_id:
                    resolved = (
                        psn_client_instance.resolve_np_communication_id_from_np_title_id(
                            np_title_id
                        )
                    )
                    if resolved:
                        np_communication_id = resolved

                if np_communication_id:
                    trophy_groups = psn_client_instance.get_game_trophy_groups(
                        np_communication_id, platform
                    )
                    if all_games:
                        for game_id, game_data in all_games.items():
                            game_cache_entry = {
                                "np_communication_id": game_id,
                                "np_title_id": np_title_id
                                if game_id == np_communication_id
                                else None,
                                "presence_name": presence_game_name
                                if game_id == np_communication_id
                                else None,
                                "trophy_name": game_data.get("name"),
                                "cover_art_url": game_data.get("icon_url"),
                                "platform": game_data.get("platform"),
                                "trophy_groups": [],
                                "last_updated": datetime.now().isoformat(),
                            }
                            if game_id == np_communication_id and trophy_groups:
                                game_cache_entry["trophy_groups"] = trophy_groups
                                game_cache_entry["np_title_id"] = np_title_id
                                game_cache_entry["presence_name"] = presence_game_name
                            psn_client_instance.store_game_data(game_cache_entry)
                        logger.info(
                            f"Stored {len(all_games)} games to database cache"
                        )
                    else:
                        single_entry = {
                            "np_communication_id": np_communication_id,
                            "np_title_id": np_title_id,
                            "presence_name": presence_game_name,
                            "trophy_name": presence_game_name,
                            "cover_art_url": None,
                            "platform": platform,
                            "trophy_groups": trophy_groups or [],
                            "last_updated": datetime.now().isoformat(),
                        }
                        psn_client_instance.store_game_data(single_entry)
                        logger.info(
                            "Stored single game to database cache "
                            "(np_title_id resolution; bulk trophy list empty)"
                        )
                    psn_client_instance.psn_data.current_game_np_comm_id = (
                        np_communication_id
                    )
                    psn_client_instance.psn_data.current_game_mismatch = None
                else:
                    logger.warning(
                        f"Could not resolve game for cache: presence_name={presence_game_name!r} "
                        f"np_title_id={np_title_id!r} all_games_empty={not bool(all_games)} "
                        f"bulk_fetch_failed={bulk_fetch_failed}"
                    )
                    if bulk_fetch_failed:
                        logger.exception(
                            "Failed to fetch bulk trophy list from API and "
                            "np_title_id resolution did not succeed"
                        )
                    elif not all_games:
                        logger.warning(
                            "Bulk trophy list empty; np_title_id resolution did not yield a game"
                        )
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

                sleep_time = SLEEP_CACHE_MISS

            # Step 3: Fetch user trophy progress (always fresh)
            if np_communication_id and platform:
                logger.debug(
                    f"Fetching trophies for current game: {presence_game_name} ({np_communication_id}, {platform})"
                )
                game_trophy_result = psn_client_instance.get_game_trophies(
                    np_communication_id=np_communication_id, platform=platform
                )
                if game_trophy_result:
                    logger.info(
                        f"Current game trophy progress: {psn_client_instance.psn_data.current_game_progress}%"
                    )
                    logger.info(
                        f"Current game trophies: {psn_client_instance.psn_data.current_game_trophies}"
                    )
                else:
                    logger.exception(
                        f"Failed to fetch trophies for game {presence_game_name}"
                    )
            elif np_title_id and platform:
                # Fallback to using np_title_id if we couldn't find np_communication_id
                logger.debug(
                    f"Fetching trophies using np_title_id fallback: {presence_game_name} ({np_title_id}, {platform})"
                )
                game_trophy_result = psn_client_instance.get_game_trophies(
                    np_communication_id=np_title_id, platform=platform
                )
                if game_trophy_result:
                    logger.info(
                        f"Current game trophy progress: {psn_client_instance.psn_data.current_game_progress}%"
                    )
                    logger.info(
                        f"Current game trophies: {psn_client_instance.psn_data.current_game_trophies}"
                    )
                else:
                    logger.exception(
                        f"Failed to fetch trophies for game {presence_game_name}"
                    )

            # Update and broadcast
            _update_and_broadcast()

            # Log sleep time based on cache status
            cache_status = "cache hit" if cache_hit else "cache miss"
            logger.debug(
                f"PSN update loop iteration #{update_count} complete ({cache_status}), waiting {sleep_time} seconds..."
            )

        except Exception as e:
            logger.exception(
                f"Error during PSN data update loop iteration #{update_count}: {e}"
            )
            if psn_client_instance:
                state_manager.set_live_psn_data(psn_client_instance.psn_data)
            sleep_time = SLEEP_CACHE_MISS  # Use normal sleep on error

        # Step 5: Sleep based on whether we had a cache hit or not
        stop_psn_thread_event.wait(sleep_time)

    logger.info("=== PSN DATA UPDATER THREAD FINISHED ===")


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
            web_engine.web_engine_instance.socketio.emit(
                "psn_data_update", psn_data_dict
            )
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
                    web_engine.web_engine_instance.socketio.emit(
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
        logger.debug(
            "Not starting PSN update thread: PSNClient is not connected yet."
        )
        return

    # Only start if there's an NPSSO code, otherwise the loop doesn't do much.
    if not psn_client_instance.psn_data.npsso_code:
        logger.debug(
            "Not starting PSN update thread: No NPSSO code in PSNClient data."
        )
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


def stop_psn_data_updater_thread():
    """Stops the PSN data updater thread."""
    global psn_update_thread
    logger.info("=== STOPPING PSN DATA UPDATER THREAD ===")

    if psn_update_thread and psn_update_thread.is_alive():
        logger.info("Attempting to stop PSN data updater thread (PSN Service)...")
        stop_psn_thread_event.set()
        psn_update_thread.join(timeout=5.0)
        if psn_update_thread.is_alive():
            logger.exception(
                "PSN data updater thread (PSN Service) did not stop in the allocated time."
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
                    web_engine.web_engine_instance.socketio.emit(
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
                        web_engine.web_engine_instance.socketio.emit(
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
                        web_engine.web_engine_instance.socketio.emit(
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

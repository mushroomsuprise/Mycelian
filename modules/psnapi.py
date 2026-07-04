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
from dataclasses import dataclass, field
from datetime import datetime

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPAuthenticationError,
    PSNAWPBadRequestError,
    PSNAWPForbiddenError,
    PSNAWPNotFoundError,
)
try:
    from psnawp_api.core.psnawp_exceptions import (
        PSNAWPServerError,
        PSNAWPUnauthorizedError,
    )
except ImportError:

    class PSNAWPServerError(Exception):  # type: ignore[no-redef]
        pass

    class PSNAWPUnauthorizedError(Exception):  # type: ignore[no-redef]
        pass
from psnawp_api.models.trophies import PlatformType
from psnawp_api.models.trophies.trophy_titles import TrophyTitleIterator
from psnawp_api.psnawp import PSNAWP

from .database_manager import database_manager

logger = logging.getLogger(__name__)

# Database path for PSN game data cache
PSN_GAME_DATA_PATH = "PSNGameData/games"


def _is_psn_auth_expired_error(exc: BaseException) -> bool:
    if isinstance(exc, PSNAWPAuthenticationError):
        return True
    if isinstance(exc, PSNAWPUnauthorizedError):
        return True
    msg = str(exc).lower()
    return "expired token" in msg or "expired access token" in msg


def _is_psn_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (RequestsConnectionError, OSError, TimeoutError)):
        return True
    if isinstance(exc, PSNAWPServerError):
        return True
    msg = str(exc).lower()
    return "service unavailable" in msg


def presence_format_to_platform_type(platform: str | None) -> PlatformType:
    """Convert PSN presence ``format`` (e.g. ``PS5``) to psnawp ``PlatformType`` for trophy APIs."""
    if platform is None or not str(platform).strip():
        return PlatformType.UNKNOWN
    s = str(platform).strip().upper().replace(" ", "")
    if s in ("PSVITA", "PS_VITA", "VITA"):
        return PlatformType.PS_VITA
    if s in ("PSPC", "PC"):
        return PlatformType.PSPC
    return PlatformType(s)


def load_all_psn_game_cache_docs_from_db() -> list[dict]:
    """
    All cached game documents, regardless of database backend.

    Firebase RTDB returns a subtree for ``PSNGameData/games``; SQLite and Mongo
    store one document per child path. This merges both so enumeration works
    everywhere without duplicate entries (path-based rows win for the same
    np_communication_id).
    """
    by_id: dict[str, dict] = {}
    try:
        parent = database_manager.get_data(PSN_GAME_DATA_PATH)
        if isinstance(parent, dict) and parent:
            for _key, val in parent.items():
                if not isinstance(val, dict):
                    continue
                # Prefer field on document; else RTDB often uses np_comm_id as the child key
                cid = val.get("np_communication_id") or (
                    str(_key) if isinstance(_key, str) and _key else None
                )
                if not cid:
                    continue
                if not val.get("np_communication_id"):
                    val = {**val, "np_communication_id": cid}
                by_id[str(cid)] = val

        # Firebase: one get_data on PSNGameData/games is the full subtree. Do not call
        # get_all_paths() — the Firebase implementation downloads the entire database
        # to list paths, which can freeze the UI and block the settings tab.
        if database_manager.get_config().database_type == "firebase":
            games_list = list(by_id.values())
        else:
            # SQLite and Mongo: one document per child path; merge in path rows (wins
            # over parent-shaped duplicates).
            prefix = f"{PSN_GAME_DATA_PATH}/"
            for p in database_manager.get_all_paths():
                if p.startswith("/"):
                    p = p[1:]
                if not p.startswith(prefix) or p == PSN_GAME_DATA_PATH.rstrip("/"):
                    continue
                rel = p[len(prefix) :]
                if "/" in rel:
                    continue
                data = database_manager.get_data(p)
                if not isinstance(data, dict):
                    continue
                cid = data.get("np_communication_id") or rel
                if not cid:
                    continue
                if not data.get("np_communication_id"):
                    data = {**data, "np_communication_id": cid}
                by_id[str(cid)] = data

            games_list = list(by_id.values())
        games_list.sort(key=lambda x: (x.get("trophy_name") or "").lower())
        logger.debug(f"Loaded {len(games_list)} PSN game cache document(s) from database")
        return games_list
    except Exception as e:
        logger.error(f"Error loading PSN game cache from database: {e}")
        return []


def get_psn_game_cache_by_comm_id(np_communication_id: str) -> dict | None:
    """Read one cached game document from the database (any backend)."""
    if not np_communication_id:
        return None
    try:
        path = f"{PSN_GAME_DATA_PATH}/{np_communication_id}"
        data = database_manager.get_data(path)
        if data and isinstance(data, dict) and data.get("np_communication_id"):
            logger.debug(f"Found cached game data for {np_communication_id}")
            return data
        logger.debug(f"No cached game data found for {np_communication_id}")
        return None
    except Exception as e:
        logger.error(
            f"Error retrieving cached game data for {np_communication_id}: {e}"
        )
        return None


def update_psn_game_cache_in_db(np_communication_id: str, updates: dict) -> bool:
    """Update fields in one cached game document. Does not require a PSNClient instance."""
    try:
        if not np_communication_id:
            logger.error("Cannot update game cache: missing np_communication_id")
            return False
        existing_data = get_psn_game_cache_by_comm_id(np_communication_id)
        if not existing_data:
            logger.error(
                f"Cannot update game cache: game {np_communication_id} not found"
            )
            return False
        for key, value in updates.items():
            existing_data[key] = value
        existing_data["last_updated"] = datetime.now().isoformat()
        path = f"{PSN_GAME_DATA_PATH}/{np_communication_id}"
        result = database_manager.set_data(path, existing_data)
        if result:
            logger.info(
                f"Updated game cache for {np_communication_id}: {list(updates.keys())}"
            )
        else:
            logger.error(f"Failed to update game cache for {np_communication_id}")
        return result
    except Exception as e:
        logger.error(f"Error updating game cache for {np_communication_id}: {e}")
        return False


def delete_psn_game_cache_in_db(np_communication_id: str) -> bool:
    """Delete one cached game path. See PSNClient.delete_game_cache_entry."""
    if not np_communication_id:
        logger.error("Cannot delete game cache: missing np_communication_id")
        return False
    try:
        path = f"{PSN_GAME_DATA_PATH}/{np_communication_id}"
        if database_manager.delete_data(path):
            logger.info(f"Deleted game cache entry {np_communication_id}")
            return True
        logger.error(f"Failed to delete game cache for {np_communication_id}")
        return False
    except Exception as e:
        logger.error(f"Error deleting game cache for {np_communication_id}: {e}")
        return False


@dataclass
class PSNGameMismatch:
    """Data class for tracking PSN game name mismatches between presence and trophy APIs."""

    presence_name: str  # Name from presence/social API
    np_title_id: str  # ID from presence data
    platform: str  # PS4, PS5, etc.
    detected_at: str  # ISO timestamp when mismatch was detected
    notified: bool = False  # Whether user has been notified about this mismatch


@dataclass
class PSNData:
    """Dataclass to store PSN information."""

    current_game_name: str | None = None
    current_game_art_url: str | None = None
    current_game_np_comm_id: str | None = (
        None  # np_communication_id for the current game
    )
    trophy_counts: dict[str, int] = field(
        default_factory=dict
    )  # e.g., {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    current_game_trophies: dict[str, int] = field(
        default_factory=dict
    )  # Trophies for the current game (base / selected trophy group)
    current_game_trophies_all: dict = field(
        default_factory=dict
    )  # All groups combined: {"earned": {...}, "defined": {...}} (base + DLC)
    current_game_progress: int | None = None
    all_games_data: dict[str, dict] = field(
        default_factory=dict
    )  # e.g., {"game_id": {"name": "Game Name", "icon_url": "...", ...}}
    npsso_code: str | None = None
    online_id: str | None = None
    account_id: str | None = None
    is_online: bool = False
    connection_status: str = "Disconnected"
    presence: dict = field(default_factory=dict)
    current_game_mismatch: PSNGameMismatch | None = None  # Active mismatch state


class PSNClient:
    """Handles connection and data fetching from the PlayStation Network API."""

    def __init__(self, npsso_code: str | None = None, psn_username: str | None = None):
        self.npsso_code: str | None = npsso_code
        self.psn_username: str | None = (
            psn_username  # Target PSN username for API calls
        )
        self.api: PSNAWP | None = None
        self.user_online_id: str | None = None
        self.account_id: str | None = None
        self.authenticated: bool = False
        self._auth_expired: bool = False
        self._auth_expired_warned: bool = False
        self.psn_data: PSNData = PSNData()
        if npsso_code:
            self.psn_data.npsso_code = npsso_code

    def update_npsso_code(self, npsso_code: str):
        """Updates the NPSSO code and resets authentication status."""
        self.npsso_code = npsso_code
        self.psn_data.npsso_code = npsso_code
        self.api = None
        self.authenticated = False
        self._auth_expired = False
        self._auth_expired_warned = False
        self.psn_data.connection_status = (
            "Not Connected" if not (npsso_code or "").strip() else "Disconnected"
        )
        logger.info("NPSSO code updated. Re-authentication will be attempted.")

    def update_psn_username(self, psn_username: str):
        """Updates the target PSN username for API calls."""
        self.psn_username = psn_username
        logger.info(f"PSN username updated to: {psn_username}")

    def update_credentials(self, npsso_code: str, psn_username: str):
        """Updates both NPSSO code and PSN username and resets authentication status."""
        self.npsso_code = npsso_code
        self.psn_username = psn_username
        self.psn_data.npsso_code = npsso_code
        self.api = None
        self.authenticated = False
        self._auth_expired = False
        self._auth_expired_warned = False
        self.psn_data.connection_status = (
            "Not Connected" if not (npsso_code or "").strip() else "Disconnected"
        )
        logger.info(
            f"Credentials updated. NPSSO code and PSN username ({psn_username}) set. Re-authentication will be attempted."
        )

    # --- Database Cache Helper Methods ---

    def get_cached_game_data(self, np_communication_id: str) -> dict | None:
        """
        Retrieve cached game data from the database by np_communication_id.

        Args:
            np_communication_id: The game's np_communication_id (e.g., "NPWR12345_00")

        Returns:
            Game data dict if found, None otherwise
        """
        return get_psn_game_cache_by_comm_id(np_communication_id)

    def store_game_data(self, game_data: dict) -> bool:
        """
        Store game data to the database.

        Args:
            game_data: Dict containing game info with at least np_communication_id

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            np_communication_id = game_data.get("np_communication_id")
            if not np_communication_id:
                logger.error("Cannot store game data: missing np_communication_id")
                return False

            # Add/update timestamp
            game_data["last_updated"] = datetime.now().isoformat()

            path = f"{PSN_GAME_DATA_PATH}/{np_communication_id}"
            result = database_manager.set_data(path, game_data)
            if result:
                logger.info(
                    f"Stored game data for {np_communication_id} ({game_data.get('presence_name', 'Unknown')})"
                )
            else:
                logger.error(f"Failed to store game data for {np_communication_id}")
            return result
        except Exception as e:
            logger.error(f"Error storing game data: {e}")
            return False

    def find_game_by_np_title_id(self, np_title_id: str) -> dict | None:
        """
        Find cached game data by np_title_id (from presence data).
        Searches through all cached games to find a match.

        Args:
            np_title_id: The game's npTitleId from presence data (e.g., "CUSA12345_00")

        Returns:
            Game data dict if found, None otherwise
        """
        try:
            if not np_title_id:
                return None
            for game_data in load_all_psn_game_cache_docs_from_db():
                if game_data.get("np_title_id") == np_title_id:
                    logger.debug(
                        f"Found cached game by np_title_id {np_title_id}: "
                        f"{game_data.get('presence_name', 'unknown')}"
                    )
                    return game_data
            logger.debug(f"No cached game found with np_title_id: {np_title_id}")
            return None
        except Exception as e:
            logger.error(f"Error finding game by np_title_id {np_title_id}: {e}")
            return None

    def find_game_by_name(self, game_name: str) -> dict | None:
        """
        Find cached game data by game name (presence_name or trophy_name).
        Uses case-insensitive matching.

        Args:
            game_name: The game name to search for

        Returns:
            Game data dict if found, None otherwise
        """
        try:
            if not game_name:
                return None
            game_name_lower = game_name.lower()
            for game_data in load_all_psn_game_cache_docs_from_db():
                presence_name = (game_data.get("presence_name") or "").lower()
                trophy_name = (game_data.get("trophy_name") or "").lower()
                if (
                    presence_name == game_name_lower
                    or trophy_name == game_name_lower
                ):
                    logger.debug(
                        f"Found cached game by name '{game_name}': "
                        f"{game_data.get('np_communication_id', '')}"
                    )
                    return game_data
            logger.debug(f"No cached game found with name: {game_name}")
            return None
        except Exception as e:
            logger.error(f"Error finding game by name {game_name}: {e}")
            return None

    def get_all_cached_games(self) -> list[dict]:
        """
        Returns a list of all cached games with their names and IDs for UI display.

        Returns:
            List of game data dicts, sorted by trophy_name
        """
        return load_all_psn_game_cache_docs_from_db()

    def update_game_cache_entry(self, np_communication_id: str, updates: dict) -> bool:
        """
        Updates specific fields in a cached game entry.

        Args:
            np_communication_id: The game's np_communication_id
            updates: Dict of fields to update (e.g., {"presence_name": "New Name"})

        Returns:
            True if updated successfully, False otherwise
        """
        return update_psn_game_cache_in_db(np_communication_id, updates)

    def delete_game_cache_entry(self, np_communication_id: str) -> bool:
        """Remove one cached game document. Next presence cycle may re-fetch and store it."""
        return delete_psn_game_cache_in_db(np_communication_id)

    def connect(self) -> bool:
        """
        Connects to the PSN API using the provided NPSSO code.
        Fetches basic user information upon successful connection.
        """
        if not self.npsso_code:
            logger.error("NPSSO code is not set. Cannot connect to PSN.")
            self.authenticated = False
            self.psn_data.connection_status = "Not Connected"
            return False

        if self._auth_expired:
            logger.debug(
                "Skipping PSN connect — NPSSO token expired; update credentials to retry"
            )
            return False

        if self.authenticated and self.api:
            logger.info("Already authenticated with PSN.")
            return True

        try:
            logger.info("Attempting to connect to PSN API...")
            logger.debug(
                f"NPSSO code length: {len(self.npsso_code) if self.npsso_code else 0}"
            )

            self.api = PSNAWP(self.npsso_code)
            logger.debug("PSNAWP instance created successfully")

            client_me = (
                self.api.me()
            )  # Gets the authenticated user's account_id, online_id.
            logger.debug(f"Retrieved client.me() data: {type(client_me)}")

            self.account_id = client_me.account_id
            self.user_online_id = client_me.online_id

            logger.info(
                f"Retrieved user info - Account ID: {self.account_id}, Online ID: {self.user_online_id}"
            )

            self.psn_data.online_id = self.user_online_id
            self.psn_data.account_id = self.account_id

            self.authenticated = True
            self.psn_data.connection_status = "Connected"
            logger.info(
                f"Successfully connected to PSN as user: {self.user_online_id} (Account ID: {self.account_id})"
            )
            return True
        except PSNAWPAuthenticationError as e:
            self._on_psn_auth_expired("connect", e)
        except PSNAWPForbiddenError as e:
            logger.error(
                f"PSN API Forbidden: Check permissions or IP restrictions. Details: {e}"
            )
            self.authenticated = False
            self.psn_data.connection_status = "Authentication Failed"
        except PSNAWPNotFoundError as e:
            logger.error(
                f"PSN API Not Found: Endpoint or resource not found. Details: {e}"
            )
            self.authenticated = False
            self.psn_data.connection_status = "Authentication Failed"
        except PSNAWPBadRequestError as e:
            logger.error(
                f"PSN API Bad Request: The request was malformed. Details: {e}"
            )
            self.authenticated = False
            self.psn_data.connection_status = "Authentication Failed"
        except Exception as e:
            logger.exception(f"An unexpected error occurred during PSN connection: {e}")
            self.authenticated = False
            self.psn_data.connection_status = "Authentication Failed"

        return False

    def is_connected(self) -> bool:
        """Checks if the client is authenticated."""
        return self.authenticated

    def _sync_presence_connection_status(self) -> None:
        """Reflect authenticated API session vs PlayStation presence in connection_status."""
        if not self.authenticated:
            return
        self.psn_data.connection_status = (
            "Connected" if self.psn_data.is_online else "Offline"
        )

    def _on_psn_auth_expired(self, context: str, exc: BaseException) -> None:
        self.authenticated = False
        self.api = None
        self.psn_data.connection_status = "Token Expired"
        self._auth_expired = True
        if not self._auth_expired_warned:
            self._auth_expired_warned = True
            logger.warning("%s: NPSSO token expired or invalid: %s", context, exc)
        else:
            logger.debug("%s: NPSSO token still expired or invalid: %s", context, exc)
        try:
            from .psn_service import notify_psn_npsso_expired

            notify_psn_npsso_expired()
        except Exception as notify_err:
            logger.debug("PSN NPSSO expiry notification failed: %s", notify_err)

    def is_auth_expired(self) -> bool:
        """True when the stored NPSSO token is known expired until user updates it."""
        return self._auth_expired

    def get_presence(self, _allow_retry: bool = True) -> dict | None:
        """Fetches the current presence status of the user or specified PSN username."""
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get presence, not connected.")
            if not self.connect():  # Try to reconnect
                return None

        # Use specified PSN username if available, otherwise use authenticated user's online_id
        target_online_id = (
            self.psn_username if self.psn_username else self.user_online_id
        )

        if not target_online_id:
            logger.warning(
                "Cannot get presence, no target online_id or psn_username set."
            )
            return None

        logger.debug(f"Fetching presence for user: {target_online_id}")

        try:
            # The psnawp.user() method expects online_id as an argument
            user = self.api.user(online_id=target_online_id)
            logger.debug(f"Created user object for {target_online_id}")

            presence_info = user.get_presence()
            logger.debug(f"Raw presence info received: {type(presence_info)}")
            logger.debug(
                f"Presence info keys: {list(presence_info.keys()) if isinstance(presence_info, dict) else 'Not a dict'}"
            )

            self.psn_data.presence = presence_info

            # Handle nested presence structure - check for basicPresence first
            basic_presence = None
            if isinstance(presence_info, dict):
                if "basicPresence" in presence_info:
                    basic_presence = presence_info["basicPresence"]
                    logger.debug(f"Found basicPresence data: {basic_presence}")
                else:
                    # Fallback to root level for backwards compatibility
                    basic_presence = presence_info
                    logger.debug("Using root level presence data")

            availability = None
            if basic_presence and isinstance(basic_presence, dict):
                availability = basic_presence.get("availability")
                logger.debug(f"Presence availability: {availability}")

                # Check multiple availability indicators
                if availability in ["available", "availableToPlay"]:
                    self.psn_data.is_online = True
                    logger.info(
                        f"User {target_online_id} is online (availability: {availability})"
                    )
                elif (
                    basic_presence.get("primaryPlatformInfo", {}).get("onlineStatus")
                    == "online"
                ):
                    # Secondary check for online status
                    self.psn_data.is_online = True
                    logger.info(
                        f"User {target_online_id} is online (via primaryPlatformInfo)"
                    )
                else:
                    self.psn_data.is_online = False
                    logger.info(
                        f"User {target_online_id} is offline or unavailable (availability: {availability})"
                    )

                # Parse game information
                game_title_info = basic_presence.get("gameTitleInfoList")
                if (
                    game_title_info
                    and isinstance(game_title_info, list)
                    and len(game_title_info) > 0
                ):
                    logger.debug(f"Game title info list length: {len(game_title_info)}")
                    game_info = game_title_info[0]
                    logger.debug(
                        f"First game info keys: {list(game_info.keys()) if isinstance(game_info, dict) else 'Not a dict'}"
                    )

                    self.psn_data.current_game_name = game_info.get("titleName")
                    # Try different possible icon URL keys
                    self.psn_data.current_game_art_url = (
                        game_info.get("npTitleIconUrl")
                        or game_info.get("conceptIconUrl")
                        or game_info.get("titleIconUrl")
                    )

                    logger.info(f"Current game: {self.psn_data.current_game_name}")
                    logger.debug(f"Game art URL: {self.psn_data.current_game_art_url}")
                    logger.debug(f"Game npTitleId: {game_info.get('npTitleId')}")
                    logger.debug(f"Game format: {game_info.get('format')}")
                else:
                    # Clear game data when no game is detected
                    self.psn_data.current_game_name = None
                    self.psn_data.current_game_art_url = None
                    # Also clear game-specific trophy data
                    self.psn_data.current_game_trophies = {}
                    self.psn_data.current_game_trophies_all = {}
                    self.psn_data.current_game_progress = None
                    logger.debug("No game currently being played - cleared game data")
            else:
                # No basic presence data found
                self.psn_data.is_online = False
                self.psn_data.current_game_name = None
                self.psn_data.current_game_art_url = None
                # Also clear game-specific trophy data
                self.psn_data.current_game_trophies = {}
                self.psn_data.current_game_trophies_all = {}
                self.psn_data.current_game_progress = None
                logger.warning(
                    "No valid presence data structure found - cleared game data"
                )

            logger.debug(f"Successfully fetched presence for {target_online_id}")
            self._sync_presence_connection_status()
            return presence_info
        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            logger.error(
                f"API error while fetching presence for {target_online_id}: {e}"
            )
        except (RequestsConnectionError, OSError) as e:
            logger.warning(
                "Network error fetching presence for %s: %s",
                target_online_id,
                e,
            )
            self.authenticated = False
            self.api = None
            if _allow_retry and self.connect():
                return self.get_presence(_allow_retry=False)
        except Exception as e:
            if _is_psn_auth_expired_error(e):
                self._on_psn_auth_expired(f"get_presence({target_online_id})", e)
            elif _allow_retry and _is_psn_connection_error(e):
                logger.warning(
                    "Connection error fetching presence for %s, retrying: %s",
                    target_online_id,
                    e,
                )
                self.authenticated = False
                self.api = None
                if self.connect():
                    return self.get_presence(_allow_retry=False)
            else:
                logger.warning(
                    "Unexpected error fetching presence for %s: %s",
                    target_online_id,
                    e,
                )
                self.authenticated = False
        return None

    def get_trophy_target_account_id(self, _allow_retry: bool = True) -> str | None:
        """Account ID used for trophy APIs (tracked PSN user or authenticating user)."""
        if not self.is_connected() or not self.api:
            logger.warning("Cannot resolve trophy account, not connected.")
            if not self.connect():
                return None
        target_account_id = self.account_id
        if self.psn_username:
            try:
                logger.debug(
                    f"Getting account_id for PSN username: {self.psn_username}"
                )
                target_user = self.api.user(online_id=self.psn_username)
                target_account_id = target_user.account_id
                logger.debug(
                    f"Using PSN username {self.psn_username} with account_id {target_account_id}"
                )
            except Exception as e:
                if _is_psn_auth_expired_error(e):
                    self._on_psn_auth_expired(
                        f"get_trophy_target_account_id({self.psn_username})", e
                    )
                elif _allow_retry and _is_psn_connection_error(e):
                    logger.warning(
                        "Connection error resolving PSN account_id for %s, retrying: %s",
                        self.psn_username,
                        e,
                    )
                    self.authenticated = False
                    self.api = None
                    if self.connect():
                        return self.get_trophy_target_account_id(_allow_retry=False)
                else:
                    logger.warning(
                        "Failed to get account_id for PSN username %s: %s",
                        self.psn_username,
                        e,
                    )
                return None
        if not target_account_id:
            logger.warning("No trophy target account_id available.")
            return None
        return target_account_id

    def resolve_np_communication_id_from_np_title_id(
        self, np_title_id: str
    ) -> str | None:
        """
        Map presence ``npTitleId`` to ``npCommunicationId`` using the trophy-by-title-id
        endpoint (works when the bulk trophy title list is empty or names do not match).
        """
        if not np_title_id or not self.api:
            return None
        if not self.is_connected():
            if not self.connect():
                return None
        account_id = self.get_trophy_target_account_id()
        if not account_id:
            return None
        try:
            comm_id = TrophyTitleIterator.get_np_communication_id(
                self.api.authenticator, np_title_id, account_id
            )
            if comm_id:
                logger.info(
                    f"Resolved np_communication_id from np_title_id {np_title_id!r}: {comm_id}"
                )
            return comm_id or None
        except (PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            logger.warning(
                f"np_title_id lookup failed for {np_title_id!r} (account={account_id}): {e}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error resolving np_title_id {np_title_id!r}: {e}",
                exc_info=True,
            )
            return None

    def get_all_games(self) -> dict | None:
        """
        Fetches all game titles and their trophy summaries for the user or specified PSN username.
        Handles pagination to retrieve more than 800 games if needed.
        """
        target_account_id = self.get_trophy_target_account_id()
        if not target_account_id:
            return None

        logger.debug(f"Fetching all games for account_id: {target_account_id}")

        try:
            # The API requires account_id for trophy operations
            user = self.api.user(account_id=target_account_id)  # type: ignore
            logger.debug("Created user object for trophy titles fetch")

            games_data = {}
            offset = 0
            page_size = 800
            total_fetched = 0

            # Pagination loop - continue until we get fewer results than the limit
            while True:
                logger.debug(
                    f"Fetching trophy titles with offset={offset}, page_size={page_size}"
                )
                titles_response = user.trophy_titles(
                    limit=None, offset=offset, page_size=page_size
                )  # type: ignore
                logger.debug(f"Trophy titles response type: {type(titles_response)}")

                # Check if the PaginationIterator has any items
                if (
                    hasattr(titles_response, "_total_item_count")
                    and titles_response._total_item_count == 0
                ):
                    logger.warning(
                        "No trophy titles available from API (empty response)"
                    )
                    logger.debug(
                        f"PaginationIterator details: {titles_response.__dict__}"
                    )
                    logger.info(
                        f"Check if NPSSO code is valid and user {target_account_id} has trophy data accessible"
                    )
                    break

                if not titles_response:
                    logger.warning("No response from trophy_titles API")
                    break

                # Convert TrophyTitleIterator to list of TrophyTitle objects
                try:
                    trophy_titles = list(titles_response)
                    logger.debug(
                        f"Successfully converted TrophyTitleIterator to list with {len(trophy_titles)} items"
                    )
                except Exception as e:
                    logger.error(f"Could not convert TrophyTitleIterator to list: {e}")
                    logger.debug(f"TrophyTitleIterator object: {titles_response}")
                    if hasattr(titles_response, "__dict__"):
                        logger.debug(
                            f"TrophyTitleIterator attributes: {titles_response.__dict__}"
                        )
                    break

                if not trophy_titles:
                    logger.debug("No more trophy titles found")
                    break

                batch_count = len(trophy_titles)
                logger.info(f"Fetched {batch_count} trophy titles (offset: {offset})")

                # Log first 5 titles for debugging on first batch
                if offset == 0:
                    for i, title in enumerate(trophy_titles[:5]):
                        logger.debug(
                            f"Title {i}: {getattr(title, 'title_name', 'Unknown')} - {getattr(title, 'np_communication_id', 'Unknown')}"
                        )

                # Process TrophyTitle objects
                for title in trophy_titles:
                    game_id = getattr(title, "np_communication_id", None)
                    if game_id:
                        earned = getattr(title, "earned_trophies", None)
                        defined = getattr(title, "defined_trophies", None)
                        games_data[game_id] = {
                            "name": getattr(title, "title_name", None),
                            "platform": getattr(title, "title_platform", None),
                            "icon_url": getattr(title, "title_icon_url", None),
                            "earned_trophies": {
                                "bronze": getattr(earned, "bronze", 0) if earned else 0,
                                "silver": getattr(earned, "silver", 0) if earned else 0,
                                "gold": getattr(earned, "gold", 0) if earned else 0,
                                "platinum": getattr(earned, "platinum", 0)
                                if earned
                                else 0,
                            }
                            if earned
                            else {},
                            "defined_trophies": {
                                "bronze": getattr(defined, "bronze", 0)
                                if defined
                                else 0,
                                "silver": getattr(defined, "silver", 0)
                                if defined
                                else 0,
                                "gold": getattr(defined, "gold", 0) if defined else 0,
                                "platinum": getattr(defined, "platinum", 0)
                                if defined
                                else 0,
                            }
                            if defined
                            else {},
                            "progress": getattr(title, "progress", None),
                        }

                total_fetched += batch_count

                # Check if we need to fetch more
                if batch_count < page_size:
                    logger.debug(
                        f"Received {batch_count} titles (less than page_size {page_size}), pagination complete"
                    )
                    break

                # Move to next page
                offset += page_size
                logger.debug(f"Fetched {page_size} titles, checking for more...")

            self.psn_data.all_games_data = games_data
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.info(
                f"Successfully fetched {len(games_data)} games for {target_desc} (total API calls: {(offset // page_size) + 1})"
            )
            return games_data

        except PSNAWPForbiddenError as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(f"PSN API access forbidden for {target_desc}: {e}")
            logger.info(
                "This may indicate authentication issues or the target user has private trophy data"
            )
        except PSNAWPNotFoundError as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(f"PSN API endpoint not found for {target_desc}: {e}")
            logger.info(
                "The trophy API endpoint may have changed or be temporarily unavailable"
            )
        except PSNAWPBadRequestError as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(f"PSN API bad request for {target_desc}: {e}")
            logger.info("Check that the account_id and authentication are valid")
        except Exception as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.exception(
                f"Unexpected error fetching all games for {target_desc}: {e}"
            )
        return None

    # Placeholder for fetching overall trophy summary
    def get_overall_trophy_summary(self, _allow_retry: bool = True) -> dict | None:
        """Fetches the overall trophy summary for the user or specified PSN username."""
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get trophy summary, not connected.")
            if not self.connect():  # Try to reconnect
                return None

        # Use specified PSN username if available, otherwise use authenticated user's account_id
        target_account_id = self.account_id

        if self.psn_username:
            # If we have a specific PSN username, we need to get their account_id
            try:
                logger.debug(
                    f"Getting account_id for PSN username: {self.psn_username}"
                )
                target_user = self.api.user(online_id=self.psn_username)
                target_account_id = target_user.account_id
                logger.debug(
                    f"Using PSN username {self.psn_username} for trophy summary with account_id {target_account_id}"
                )
            except Exception as e:
                if _is_psn_auth_expired_error(e):
                    self._on_psn_auth_expired(
                        f"get_overall_trophy_summary account lookup({self.psn_username})",
                        e,
                    )
                elif _allow_retry and _is_psn_connection_error(e):
                    logger.warning(
                        "Connection error resolving account_id for %s, retrying: %s",
                        self.psn_username,
                        e,
                    )
                    self.authenticated = False
                    self.api = None
                    if self.connect():
                        return self.get_overall_trophy_summary(_allow_retry=False)
                else:
                    logger.warning(
                        "Failed to get account_id for PSN username %s: %s",
                        self.psn_username,
                        e,
                    )
                return None

        if not target_account_id:
            logger.warning("Cannot get trophy summary, no target account_id available.")
            return None

        logger.debug(f"Fetching trophy summary for account_id: {target_account_id}")

        try:
            user = self.api.user(account_id=target_account_id)  # type: ignore
            logger.debug("Created user object for trophy summary fetch")

            summary = user.trophy_summary()  # type: ignore
            logger.debug(f"Trophy summary response type: {type(summary)}")
            logger.debug(
                f"Trophy summary attributes: {dir(summary) if summary else 'None'}"
            )

            # Handle TrophySummary object - it has attributes, not dictionary keys
            if summary:
                try:
                    # Try different possible attribute names for trophy counts
                    if hasattr(summary, "earned_trophies"):
                        earned_trophies = summary.earned_trophies
                        logger.debug(
                            f"Found earned_trophies attribute: {type(earned_trophies)}"
                        )

                        if isinstance(earned_trophies, dict):
                            self.psn_data.trophy_counts = earned_trophies
                            logger.info(f"Trophy counts from dict: {earned_trophies}")
                        else:
                            # Convert object to dict if needed
                            trophy_counts = {
                                "bronze": getattr(earned_trophies, "bronze", 0),
                                "silver": getattr(earned_trophies, "silver", 0),
                                "gold": getattr(earned_trophies, "gold", 0),
                                "platinum": getattr(earned_trophies, "platinum", 0),
                            }
                            self.psn_data.trophy_counts = trophy_counts
                            logger.info(
                                f"Trophy counts from attributes: {trophy_counts}"
                            )

                    elif hasattr(summary, "earnedTrophies"):
                        self.psn_data.trophy_counts = summary.earnedTrophies
                        logger.info(
                            f"Trophy counts from earnedTrophies: {summary.earnedTrophies}"
                        )
                    else:
                        # Try to extract trophy counts directly from summary object
                        trophy_counts = {
                            "bronze": getattr(summary, "bronze", 0),
                            "silver": getattr(summary, "silver", 0),
                            "gold": getattr(summary, "gold", 0),
                            "platinum": getattr(summary, "platinum", 0),
                        }
                        self.psn_data.trophy_counts = trophy_counts
                        logger.info(
                            f"Trophy counts extracted directly: {trophy_counts}"
                        )

                except Exception as e:
                    logger.warning(f"Could not extract trophy counts from summary: {e}")
                    logger.debug(
                        f"Summary object details: {vars(summary) if hasattr(summary, '__dict__') else 'No __dict__'}"
                    )
                    self.psn_data.trophy_counts = {}
            else:
                logger.warning("Trophy summary returned None")
                self.psn_data.trophy_counts = {}

            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.info(
                f"Successfully fetched overall trophy summary for {target_desc}"
            )
            return summary  # type: ignore
        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(
                f"API error while fetching trophy summary for {target_desc}: {e}"
            )
        except Exception as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            if _is_psn_auth_expired_error(e):
                self._on_psn_auth_expired(
                    f"get_overall_trophy_summary({target_desc})", e
                )
            elif _allow_retry and _is_psn_connection_error(e):
                logger.warning(
                    "Connection error fetching trophy summary for %s, retrying: %s",
                    target_desc,
                    e,
                )
                self.authenticated = False
                self.api = None
                if self.connect():
                    return self.get_overall_trophy_summary(_allow_retry=False)
            else:
                logger.warning(
                    "Unexpected error fetching trophy summary for %s: %s",
                    target_desc,
                    e,
                )
                self.authenticated = False
        return None

    def get_game_trophy_groups(
        self, np_communication_id: str, platform: str
    ) -> list | None:
        """
        Fetches all trophy groups (base game + DLCs) for a specific game with defined trophy counts.
        This is used to populate the cached game data structure.

        Args:
            np_communication_id: The game's np_communication_id
            platform: The platform (PS4, PS5, etc.)

        Returns:
            List of trophy group dicts, or None on error
        """
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get game trophy groups, not connected.")
            if not self.connect():
                return None

        # Use specified PSN username if available, otherwise use authenticated user's account_id
        target_account_id = self.account_id

        if self.psn_username:
            try:
                logger.debug(
                    f"Getting account_id for PSN username: {self.psn_username}"
                )
                target_user = self.api.user(online_id=self.psn_username)
                target_account_id = target_user.account_id
            except Exception as e:
                logger.error(
                    f"Failed to get account_id for PSN username {self.psn_username}: {e}"
                )
                return None

        if not target_account_id:
            logger.warning(
                "Cannot get game trophy groups, no target account_id available."
            )
            return None

        platform_type = presence_format_to_platform_type(platform)
        logger.debug(
            f"Fetching trophy groups for game: {np_communication_id} on {platform} "
            f"({platform_type})"
        )

        try:
            user = self.api.user(account_id=target_account_id)  # type: ignore

            # Fetch trophy groups summary for the game
            trophy_groups_summary = user.trophy_groups_summary(  # type: ignore
                np_communication_id=np_communication_id,
                platform=platform_type,
                include_progress=True,
            )

            if not trophy_groups_summary:
                logger.warning(
                    f"No trophy groups summary returned for {np_communication_id}"
                )
                return None

            trophy_groups_list = []

            # Extract trophy groups from the response
            trophy_groups = []
            if hasattr(trophy_groups_summary, "trophy_groups"):
                trophy_groups = trophy_groups_summary.trophy_groups
            elif (
                isinstance(trophy_groups_summary, dict)
                and "trophyGroups" in trophy_groups_summary
            ):
                trophy_groups = trophy_groups_summary["trophyGroups"]

            logger.debug(
                f"Found {len(trophy_groups)} trophy groups for {np_communication_id}"
            )

            for group in trophy_groups:
                # Handle both object and dict responses
                if isinstance(group, dict):
                    group_id = group.get("trophyGroupId", "")
                    group_name = group.get("trophyGroupName", "Unknown")
                    defined = group.get("definedTrophies", {})

                    trophy_groups_list.append(
                        {
                            "trophy_group_id": group_id,
                            "group_name": group_name,
                            "is_base_game": group_id == "default",
                            "defined_trophies": {
                                "bronze": defined.get("bronze", 0),
                                "silver": defined.get("silver", 0),
                                "gold": defined.get("gold", 0),
                                "platinum": defined.get("platinum", 0),
                            },
                        }
                    )
                else:
                    # Object-based response
                    group_id = getattr(group, "trophy_group_id", "") or ""
                    group_name = (
                        getattr(group, "trophy_group_name", "Unknown") or "Unknown"
                    )
                    defined = getattr(group, "defined_trophies", None)

                    defined_counts = {
                        "bronze": 0,
                        "silver": 0,
                        "gold": 0,
                        "platinum": 0,
                    }
                    if defined:
                        if hasattr(defined, "bronze"):
                            defined_counts = {
                                "bronze": getattr(defined, "bronze", 0),
                                "silver": getattr(defined, "silver", 0),
                                "gold": getattr(defined, "gold", 0),
                                "platinum": getattr(defined, "platinum", 0),
                            }
                        elif isinstance(defined, dict):
                            defined_counts = {
                                "bronze": defined.get("bronze", 0),
                                "silver": defined.get("silver", 0),
                                "gold": defined.get("gold", 0),
                                "platinum": defined.get("platinum", 0),
                            }

                    trophy_groups_list.append(
                        {
                            "trophy_group_id": group_id,
                            "group_name": group_name,
                            "is_base_game": group_id == "default",
                            "defined_trophies": defined_counts,
                        }
                    )

            logger.info(
                f"Successfully fetched {len(trophy_groups_list)} trophy groups for {np_communication_id}"
            )
            return trophy_groups_list

        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            logger.error(
                f"API error while fetching trophy groups for {np_communication_id}: {e}"
            )
        except Exception as e:
            logger.exception(
                f"Unexpected error fetching trophy groups for {np_communication_id}: {e}"
            )
        return None

    def get_game_trophies(
        self, np_communication_id: str, platform: str, trophy_group_id: str = "default"
    ) -> dict | None:
        """
        Fetches trophies for a specific game and optionally a specific trophy group.
        Updates current_game_trophies and current_game_progress in psn_data.
        """
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get game trophies, not connected.")
            if not self.connect():  # Try to reconnect
                return None

        # Use specified PSN username if available, otherwise use authenticated user's account_id
        target_account_id = self.account_id

        if self.psn_username:
            # If we have a specific PSN username, we need to get their account_id
            try:
                logger.debug(
                    f"Getting account_id for PSN username: {self.psn_username}"
                )
                target_user = self.api.user(online_id=self.psn_username)
                target_account_id = target_user.account_id
                logger.debug(
                    f"Using PSN username {self.psn_username} for game trophies with account_id {target_account_id}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to get account_id for PSN username {self.psn_username}: {e}"
                )
                return None

        if not target_account_id:
            logger.warning("Cannot get game trophies, no target account_id available.")
            return None

        logger.debug(
            f"Fetching game trophies for: {np_communication_id} on {platform} (group: {trophy_group_id})"
        )

        try:
            user = self.api.user(account_id=target_account_id)  # type: ignore
            logger.debug("Created user object for game trophy fetch")

            # Get user's trophy titles to find the correct npCommunicationId
            logger.debug(
                "Fetching user trophy titles to find correct npCommunicationId..."
            )
            trophy_titles = user.trophy_titles(limit=None, offset=0, page_size=800)
            logger.debug(f"Trophy titles response type: {type(trophy_titles)}")

            correct_np_comm_id = np_communication_id  # Default to original
            # Full-title aggregates from trophy_titles (base + DLC); used only for np_comm_id resolution and fallback.
            title_aggregate: dict | None = None
            np_service_name = "trophy2"  # Default to newer service

            # Iterate through trophy titles to find matching game
            try:
                for title in trophy_titles:
                    logger.debug(
                        f"Checking trophy title: {getattr(title, 'title_name', 'Unknown')} - npCommunicationId: {getattr(title, 'np_communication_id', 'Unknown')}"
                    )

                    # Look for a matching game name or np_communication_id
                    title_name = getattr(title, "title_name", "") or ""
                    title_np_comm_id = getattr(title, "np_communication_id", "") or ""

                    # Check if this matches our current game by name comparison
                    current_game_name = self.psn_data.current_game_name
                    if current_game_name and (
                        title_name.lower() == current_game_name.lower()
                        or title_np_comm_id == np_communication_id
                    ):
                        logger.info(
                            f"Found matching game in trophy titles: {title_name} -> {title_np_comm_id}"
                        )
                        correct_np_comm_id = title_np_comm_id
                        np_service_name = (
                            getattr(title, "np_service_name", "trophy2") or "trophy2"
                        )

                        # Extract current progress data if available
                        progress = getattr(title, "progress", None)
                        earned_trophies = getattr(title, "earned_trophies", None)
                        defined_trophies = getattr(title, "defined_trophies", None)

                        if (
                            progress is not None
                            and earned_trophies
                            and defined_trophies
                        ):
                            title_aggregate = {
                                "progress_percentage": progress,
                                "earned_trophies": {
                                    "bronze": getattr(earned_trophies, "bronze", 0),
                                    "silver": getattr(earned_trophies, "silver", 0),
                                    "gold": getattr(earned_trophies, "gold", 0),
                                    "platinum": getattr(earned_trophies, "platinum", 0),
                                },
                                "defined_trophies": {
                                    "bronze": getattr(defined_trophies, "bronze", 0),
                                    "silver": getattr(defined_trophies, "silver", 0),
                                    "gold": getattr(defined_trophies, "gold", 0),
                                    "platinum": getattr(
                                        defined_trophies, "platinum", 0
                                    ),
                                },
                            }
                            logger.info(
                                f"Found existing progress data: {progress}% complete"
                            )
                        break

            except Exception as e:
                if _is_psn_auth_expired_error(e):
                    self._on_psn_auth_expired("trophy title iteration", e)
                elif _is_psn_connection_error(e):
                    logger.warning(
                        "Connection error iterating trophy titles, retrying next cycle: %s",
                        e,
                    )
                    self.authenticated = False
                    self.api = None
                else:
                    logger.warning("Error iterating through trophy titles: %s", e)

            logger.debug(f"Final npCommunicationId to use: {correct_np_comm_id}")
            logger.debug(f"Using npServiceName: {np_service_name}")

            if title_aggregate:
                logger.info(
                    "Title-level trophy aggregate available (full title, may include DLC); "
                    "fetching per-group summary for base vs total split."
                )

            # Fetch specific game's defined and earned trophies using trophy_groups_summary
            platform_type = presence_format_to_platform_type(platform)
            logger.debug(
                f"Fetching trophy groups summary for game with ID: {correct_np_comm_id} "
                f"on {platform} ({platform_type})"
            )
            trophy_groups_summary = user.trophy_groups_summary(  # type: ignore
                np_communication_id=correct_np_comm_id,
                platform=platform_type,
                include_progress=True,  # This gets earned trophy data as well
            )
            logger.debug(
                f"Trophy groups summary response type: {type(trophy_groups_summary)}"
            )
            logger.debug(
                f"Trophy groups summary attributes: {dir(trophy_groups_summary) if trophy_groups_summary else 'None'}"
            )

            defined_counts = {
                "bronze": 0,
                "silver": 0,
                "gold": 0,
                "platinum": 0,
                "total": 0,
            }
            earned_counts = {
                "bronze": 0,
                "silver": 0,
                "gold": 0,
                "platinum": 0,
                "total": 0,
            }

            if trophy_groups_summary:
                # The trophy_groups_summary returns a list of trophy groups
                # We need to iterate through them and sum up the trophy counts
                try:
                    trophy_groups = getattr(
                        trophy_groups_summary, "trophy_groups", None
                    ) or []
                    logger.debug(f"Found {len(trophy_groups)} trophy groups")

                    all_defined_counts = {
                        "bronze": 0,
                        "silver": 0,
                        "gold": 0,
                        "platinum": 0,
                        "total": 0,
                    }
                    all_earned_counts = {
                        "bronze": 0,
                        "silver": 0,
                        "gold": 0,
                        "platinum": 0,
                        "total": 0,
                    }
                    for agroup in trophy_groups:
                        if hasattr(agroup, "defined_trophies"):
                            adt = agroup.defined_trophies
                            if hasattr(adt, "bronze"):
                                all_defined_counts["bronze"] += int(
                                    getattr(adt, "bronze", 0) or 0
                                )
                                all_defined_counts["silver"] += int(
                                    getattr(adt, "silver", 0) or 0
                                )
                                all_defined_counts["gold"] += int(
                                    getattr(adt, "gold", 0) or 0
                                )
                                all_defined_counts["platinum"] += int(
                                    getattr(adt, "platinum", 0) or 0
                                )
                        if hasattr(agroup, "earned_trophies"):
                            aet = agroup.earned_trophies
                            if hasattr(aet, "bronze"):
                                all_earned_counts["bronze"] += int(
                                    getattr(aet, "bronze", 0) or 0
                                )
                                all_earned_counts["silver"] += int(
                                    getattr(aet, "silver", 0) or 0
                                )
                                all_earned_counts["gold"] += int(
                                    getattr(aet, "gold", 0) or 0
                                )
                                all_earned_counts["platinum"] += int(
                                    getattr(aet, "platinum", 0) or 0
                                )
                    all_defined_counts["total"] = (
                        all_defined_counts["bronze"]
                        + all_defined_counts["silver"]
                        + all_defined_counts["gold"]
                        + all_defined_counts["platinum"]
                    )
                    all_earned_counts["total"] = (
                        all_earned_counts["bronze"]
                        + all_earned_counts["silver"]
                        + all_earned_counts["gold"]
                        + all_earned_counts["platinum"]
                    )
                    self.psn_data.current_game_trophies_all = {
                        "defined": all_defined_counts,
                        "earned": all_earned_counts,
                    }

                    for group in trophy_groups:
                        # Check if this is the group we want (usually "default" for base game)
                        group_id = getattr(group, "trophy_group_id", "")
                        if group_id == trophy_group_id:
                            logger.debug(f"Processing trophy group: {group_id}")

                            # Get defined trophies for this group
                            if hasattr(group, "defined_trophies"):
                                defined_trophies = group.defined_trophies
                                if hasattr(defined_trophies, "bronze"):
                                    defined_counts["bronze"] = getattr(
                                        defined_trophies, "bronze", 0
                                    )
                                    defined_counts["silver"] = getattr(
                                        defined_trophies, "silver", 0
                                    )
                                    defined_counts["gold"] = getattr(
                                        defined_trophies, "gold", 0
                                    )
                                    defined_counts["platinum"] = getattr(
                                        defined_trophies, "platinum", 0
                                    )
                                    defined_counts["total"] = (
                                        defined_counts["bronze"]
                                        + defined_counts["silver"]
                                        + defined_counts["gold"]
                                        + defined_counts["platinum"]
                                    )
                                    logger.info(
                                        f"Defined trophy counts: {defined_counts}"
                                    )

                            # Get earned trophies for this group
                            if hasattr(group, "earned_trophies"):
                                earned_trophies = group.earned_trophies
                                if hasattr(earned_trophies, "bronze"):
                                    earned_counts["bronze"] = getattr(
                                        earned_trophies, "bronze", 0
                                    )
                                    earned_counts["silver"] = getattr(
                                        earned_trophies, "silver", 0
                                    )
                                    earned_counts["gold"] = getattr(
                                        earned_trophies, "gold", 0
                                    )
                                    earned_counts["platinum"] = getattr(
                                        earned_trophies, "platinum", 0
                                    )
                                    earned_counts["total"] = (
                                        earned_counts["bronze"]
                                        + earned_counts["silver"]
                                        + earned_counts["gold"]
                                        + earned_counts["platinum"]
                                    )
                                    logger.info(
                                        f"Earned trophy counts: {earned_counts}"
                                    )

                            # If we have progress directly available, log it (do not clobber title_aggregate)
                            if hasattr(group, "progress"):
                                group_progress = getattr(group, "progress", 0)
                                logger.debug(
                                    f"Progress from trophy group: {group_progress}%"
                                )

                            break  # Found our group, no need to continue

                    if defined_counts["total"] == 0 and earned_counts["total"] == 0:
                        logger.warning(
                            f"No trophy data found for group '{trophy_group_id}' in trophy groups summary"
                        )
                        # Try to get data from any available group
                        if trophy_groups:
                            first_group = trophy_groups[0]
                            logger.debug(
                                f"Trying first available group: {getattr(first_group, 'trophy_group_id', 'unknown')}"
                            )

                            if hasattr(first_group, "defined_trophies"):
                                defined_trophies = first_group.defined_trophies
                                if hasattr(defined_trophies, "bronze"):
                                    defined_counts["bronze"] = getattr(
                                        defined_trophies, "bronze", 0
                                    )
                                    defined_counts["silver"] = getattr(
                                        defined_trophies, "silver", 0
                                    )
                                    defined_counts["gold"] = getattr(
                                        defined_trophies, "gold", 0
                                    )
                                    defined_counts["platinum"] = getattr(
                                        defined_trophies, "platinum", 0
                                    )
                                    defined_counts["total"] = (
                                        defined_counts["bronze"]
                                        + defined_counts["silver"]
                                        + defined_counts["gold"]
                                        + defined_counts["platinum"]
                                    )

                            if hasattr(first_group, "earned_trophies"):
                                earned_trophies = first_group.earned_trophies
                                if hasattr(earned_trophies, "bronze"):
                                    earned_counts["bronze"] = getattr(
                                        earned_trophies, "bronze", 0
                                    )
                                    earned_counts["silver"] = getattr(
                                        earned_trophies, "silver", 0
                                    )
                                    earned_counts["gold"] = getattr(
                                        earned_trophies, "gold", 0
                                    )
                                    earned_counts["platinum"] = getattr(
                                        earned_trophies, "platinum", 0
                                    )
                                    earned_counts["total"] = (
                                        earned_counts["bronze"]
                                        + earned_counts["silver"]
                                        + earned_counts["gold"]
                                        + earned_counts["platinum"]
                                    )

                except Exception as e:
                    logger.warning(f"Error processing trophy groups summary: {e}")
                    logger.debug(
                        f"Trophy groups summary object: {vars(trophy_groups_summary) if hasattr(trophy_groups_summary, '__dict__') else 'No __dict__'}"
                    )
                    self.psn_data.current_game_trophies_all = {}
            else:
                logger.warning("No trophy groups summary returned")
                self.psn_data.current_game_trophies_all = {}

            self.psn_data.current_game_trophies = {
                "defined": defined_counts,
                "earned": earned_counts,
            }

            # Degraded mode: groups summary did not yield this game's group counts
            if (
                defined_counts["total"] == 0
                and earned_counts["total"] == 0
                and title_aggregate
            ):
                logger.warning(
                    "No per-group trophy data from API; falling back to title-level "
                    "aggregates (counts may include DLC in both fields)."
                )
                d = dict(title_aggregate["defined_trophies"])
                e = dict(title_aggregate["earned_trophies"])
                self.psn_data.current_game_trophies = {"defined": d, "earned": e}
                self.psn_data.current_game_trophies_all = {
                    "defined": dict(d),
                    "earned": dict(e),
                }
                self.psn_data.current_game_progress = title_aggregate[
                    "progress_percentage"
                ]
            elif defined_counts["total"] > 0:
                progress = round(
                    (earned_counts["total"] / defined_counts["total"]) * 100
                )
                self.psn_data.current_game_progress = progress
                logger.info(
                    f"Game progress calculated: {progress}% ({earned_counts['total']}/{defined_counts['total']})"
                )
            else:
                if title_aggregate is not None:
                    self.psn_data.current_game_progress = title_aggregate[
                        "progress_percentage"
                    ]
                    logger.info(
                        f"Using progress from trophy titles: {self.psn_data.current_game_progress}%"
                    )
                else:
                    self.psn_data.current_game_progress = 0
                    logger.warning("No defined trophies found, setting progress to 0%")

            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.info(
                f"Successfully fetched trophies for game {correct_np_comm_id} for {target_desc}"
            )
            return {"trophy_groups_summary": trophy_groups_summary}

        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(
                f"API error while fetching game trophies for {np_communication_id} for {target_desc}: {e}"
            )
        except Exception as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.exception(
                f"Unexpected error fetching game trophies for {np_communication_id} for {target_desc}: {e}"
            )
        return None


if __name__ == "__main__":
    # This is for basic testing, replace 'YOUR_NPSSO_CODE_HERE' with a real code.
    # Do not commit real NPSSO codes.
    logging.basicConfig(level=logging.DEBUG)
    npsso = "YOUR_NPSSO_CODE_HERE"

    if npsso == "YOUR_NPSSO_CODE_HERE":
        print(
            "Please replace 'YOUR_NPSSO_CODE_HERE' with your actual NPSSO code to test."
        )
    else:
        client = PSNClient(npsso_code=npsso)
        if client.connect():
            print(f"Connected as: {client.user_online_id}")

            presence = client.get_presence()
            if presence:
                print("\\nPresence:")
                # print(f"  Status: {presence.get('availability')}")
                # if presence.get("gameTitleInfoList"):
                #     game_info = presence["gameTitleInfoList"][0]
                #     print(f"  Playing: {game_info.get('titleName')}")
                #     print(f"  Platform: {game_info.get('format')}")
                #     print(f"  Icon: {game_info.get('npTitleIconUrl') or game_info.get('conceptIconUrl')}")
                # else:
                #     print("  Not currently in a game.")
                print(client.psn_data)

            summary = client.get_overall_trophy_summary()
            if summary:
                print("\\nOverall Trophy Summary:")
                # print(f"  Level: {summary.get('trophyLevel')}")
                # print(f"  Progress: {summary.get('progress')}%")
                # print(f"  Earned: {summary.get('earnedTrophies')}")
                print(client.psn_data.trophy_counts)

            all_games = client.get_all_games()
            if all_games:
                print(f"\\nFetched {len(all_games)} games.")
                # for game_id, data in list(all_games.items())[:2]: # Print details of first 2 games
                #     print(f"  Game: {data['name']} ({data['platform']}) - Progress: {data['progress']}%")
                #     print(f"    Icon: {data['icon_url']}")
                #     print(f"    NPCommID: {game_id}")
                # print(client.psn_data.all_games_data)

            # Example: Fetch trophies for the current game if one is being played
            if (
                client.psn_data.current_game_name
                and client.psn_data.presence
                and client.psn_data.presence.get("gameTitleInfoList")
            ):
                current_game_info = client.psn_data.presence["gameTitleInfoList"][0]
                np_comm_id = current_game_info.get(
                    "npTitleId"
                )  # This is often the npCommunicationId for trophies
                platform = current_game_info.get("format")

                if np_comm_id and platform:
                    print(
                        f"\\nFetching trophies for current game: {client.psn_data.current_game_name} ({np_comm_id}, {platform})"
                    )
                    game_trophies = client.get_game_trophies(
                        np_communication_id=np_comm_id, platform=platform
                    )
                    if game_trophies:
                        # print("  Defined Trophies:", game_trophies.get("defined"))
                        # print("  Earned Trophies:", game_trophies.get("earned"))
                        print(client.psn_data.current_game_trophies)
                        print(
                            f"Game Progress: {client.psn_data.current_game_progress}%"
                        )
        else:
            print("Failed to connect to PSN.")
            print("Failed to connect to PSN.")

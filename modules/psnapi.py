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

import difflib
import logging
import re
import threading
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

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
from .psn_data import PSNData, PSNGameMismatch

logger = logging.getLogger(__name__)

# Database path for PSN game data cache
PSN_GAME_DATA_PATH = "PSNGameData/games"

# Fuzzy name match (conservative; only after exact/normalized miss)
FUZZY_MATCH_THRESHOLD = 0.92
FUZZY_AMBIGUITY_GAP = 0.03
FUZZY_MIN_QUERY_LEN = 4

_APOSTROPHE_CHARS = (
    "\u2019",  # ’
    "\u2018",  # ‘
    "\u02bb",  # ʻ
    "\u02bc",  # ʼ
    "\u0060",  # `
    "\u00b4",  # ´
)
_DASH_CHARS = (
    "\u2013",  # –
    "\u2014",  # —
    "\u2212",  # −
)
_TROPHY_SUFFIX_RE = re.compile(
    r"\s+(trophies|trophy\s+set|trophy\s+collection)(\s+for\b.*)?$",
    re.IGNORECASE,
)

# Process-local index for fast np_title_id / name lookups (avoids full DB scans)
_game_cache_index_lock = threading.Lock()
_game_cache_by_title_id: dict[str, dict] = {}
_game_cache_by_name: dict[str, dict] = {}
_game_cache_by_comm_id: dict[str, dict] = {}
_game_cache_index_loaded = False


def _normalize_game_name_key(name: str | None) -> str:
    """Casefold + unicode fold for presence/trophy title matching.

    Handles curly apostrophes, trademark symbols, trophy-list suffixes, and
    punctuation noise so near-identical PSN names compare equal.
    """
    if not name:
        return ""
    # Strip trademark glyphs before NFKC — NFKC maps ™→"tm", ®→"r", which
    # would leave noisy letters in the key if removed afterward.
    s = str(name)
    for ch in ("\u2122", "\u00ae", "\u00a9"):  # ™ ® ©
        s = s.replace(ch, "")
    s = unicodedata.normalize("NFKC", s).casefold()
    for ch in _APOSTROPHE_CHARS:
        s = s.replace(ch, "'")
    for ch in _DASH_CHARS:
        s = s.replace(ch, " ")
    s = _TROPHY_SUFFIX_RE.sub("", s)
    cleaned: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("'", "+", "&", " "):
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


# Public alias for callers outside this module (e.g. psn_service)
normalize_game_name_key = _normalize_game_name_key


def _normalize_platform_token(platform: Any) -> str:
    if platform is None:
        return ""
    return str(platform).strip().upper().replace(" ", "").replace("_", "")


def _platforms_compatible(candidate_platform: Any, wanted: str | None) -> bool:
    if not wanted:
        return True
    a = _normalize_platform_token(candidate_platform)
    b = _normalize_platform_token(wanted)
    if not a or not b:
        return True
    return a == b or a in b or b in a


def find_best_fuzzy_game_name_match(
    query: str,
    candidates: Iterable[dict],
    *,
    platform: str | None = None,
    name_fields: Sequence[str] = ("presence_name", "trophy_name", "name"),
) -> dict | None:
    """Return the unique best fuzzy name match, or None if none / ambiguous.

    Uses SequenceMatcher ratio on normalized titles. Accepts only when the best
    score is >= FUZZY_MATCH_THRESHOLD and clearly ahead of the runner-up.
    When ``platform`` is set, prefers platform-compatible candidates first.
    """
    query_key = _normalize_game_name_key(query)
    if len(query_key) < FUZZY_MIN_QUERY_LEN:
        return None

    scored: list[tuple[float, dict]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        best_for_candidate = 0.0
        for field_name in name_fields:
            raw = candidate.get(field_name)
            if not raw:
                continue
            cand_key = _normalize_game_name_key(str(raw))
            if not cand_key:
                continue
            if cand_key == query_key:
                best_for_candidate = 1.0
                break
            ratio = difflib.SequenceMatcher(None, query_key, cand_key).ratio()
            if ratio > best_for_candidate:
                best_for_candidate = ratio
        if best_for_candidate > 0:
            scored.append((best_for_candidate, candidate))

    if not scored:
        return None

    def _pick_unique(
        pool: list[tuple[float, dict]],
    ) -> tuple[dict, float] | None:
        if not pool:
            return None
        pool.sort(key=lambda item: item[0], reverse=True)
        best_score, best_cand = pool[0]
        if best_score < FUZZY_MATCH_THRESHOLD:
            return None
        if len(pool) > 1 and (best_score - pool[1][0]) < FUZZY_AMBIGUITY_GAP:
            return None
        return best_cand, best_score

    picked: tuple[dict, float] | None = None
    if platform:
        platform_pool = [
            (score, cand)
            for score, cand in scored
            if _platforms_compatible(cand.get("platform"), platform)
        ]
        picked = _pick_unique(platform_pool)
    if picked is None:
        picked = _pick_unique(scored)
    if picked is None:
        return None

    best_cand, best_score = picked
    matched_label = (
        best_cand.get("presence_name")
        or best_cand.get("trophy_name")
        or best_cand.get("name")
        or ""
    )
    logger.info(
        "PSN fuzzy name match: %r -> %r (score=%.3f, platform=%s)",
        query,
        matched_label,
        best_score,
        platform or "",
    )
    return best_cand


def _invalidate_game_cache_index() -> None:
    global _game_cache_index_loaded
    with _game_cache_index_lock:
        _game_cache_by_title_id.clear()
        _game_cache_by_name.clear()
        _game_cache_by_comm_id.clear()
        _game_cache_index_loaded = False


def _index_game_cache_doc(game_data: dict) -> None:
    """Upsert one game document into the in-memory lookup index (caller holds lock)."""
    if not isinstance(game_data, dict):
        return
    np_comm_id = game_data.get("np_communication_id")
    if np_comm_id:
        _game_cache_by_comm_id[str(np_comm_id)] = game_data
    np_title_id = game_data.get("np_title_id")
    if np_title_id:
        _game_cache_by_title_id[str(np_title_id)] = game_data
    for key in (
        _normalize_game_name_key(game_data.get("presence_name")),
        _normalize_game_name_key(game_data.get("trophy_name")),
        (game_data.get("presence_name") or "").lower(),
        (game_data.get("trophy_name") or "").lower(),
    ):
        if key:
            _game_cache_by_name[key] = game_data


def _ensure_game_cache_index() -> None:
    global _game_cache_index_loaded
    with _game_cache_index_lock:
        if _game_cache_index_loaded:
            return
        docs = load_all_psn_game_cache_docs_from_db()
        _game_cache_by_title_id.clear()
        _game_cache_by_name.clear()
        _game_cache_by_comm_id.clear()
        for game_data in docs:
            _index_game_cache_doc(game_data)
        _game_cache_index_loaded = True
        logger.debug(
            "Built PSN game cache index: %d title_id(s), %d name key(s), %d doc(s)",
            len(_game_cache_by_title_id),
            len(_game_cache_by_name),
            len(_game_cache_by_comm_id),
        )


def _trophy_set_to_counts(trophy_set: Any) -> dict[str, int]:
    """Normalize TrophySet / dict into bronze/silver/gold/platinum/total counts."""
    counts = {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0, "total": 0}
    if trophy_set is None:
        return counts
    if isinstance(trophy_set, dict):
        counts["bronze"] = int(trophy_set.get("bronze", 0) or 0)
        counts["silver"] = int(trophy_set.get("silver", 0) or 0)
        counts["gold"] = int(trophy_set.get("gold", 0) or 0)
        counts["platinum"] = int(trophy_set.get("platinum", 0) or 0)
    else:
        counts["bronze"] = int(getattr(trophy_set, "bronze", 0) or 0)
        counts["silver"] = int(getattr(trophy_set, "silver", 0) or 0)
        counts["gold"] = int(getattr(trophy_set, "gold", 0) or 0)
        counts["platinum"] = int(getattr(trophy_set, "platinum", 0) or 0)
    counts["total"] = (
        counts["bronze"] + counts["silver"] + counts["gold"] + counts["platinum"]
    )
    return counts


def _serialize_trophy_groups_for_cache(trophy_groups: Any) -> list[dict]:
    """Serialize trophy group objects/dicts into the DB cache shape (defined counts only)."""
    trophy_groups_list: list[dict] = []
    if not trophy_groups:
        return trophy_groups_list

    for group in trophy_groups:
        if isinstance(group, dict):
            group_id = (
                group.get("trophyGroupId") or group.get("trophy_group_id") or ""
            )
            group_name = (
                group.get("trophyGroupName")
                or group.get("trophy_group_name")
                or "Unknown"
            )
            defined = (
                group.get("definedTrophies") or group.get("defined_trophies") or {}
            )
            defined_counts = _trophy_set_to_counts(defined)
        else:
            group_id = getattr(group, "trophy_group_id", "") or ""
            group_name = getattr(group, "trophy_group_name", "Unknown") or "Unknown"
            defined_counts = _trophy_set_to_counts(
                getattr(group, "defined_trophies", None)
            )

        trophy_groups_list.append(
            {
                "trophy_group_id": group_id,
                "group_name": group_name,
                "is_base_game": group_id == "default",
                "defined_trophies": {
                    "bronze": defined_counts["bronze"],
                    "silver": defined_counts["silver"],
                    "gold": defined_counts["gold"],
                    "platinum": defined_counts["platinum"],
                },
            }
        )
    return trophy_groups_list


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
            with _game_cache_index_lock:
                if _game_cache_index_loaded:
                    _index_game_cache_doc(existing_data)
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
            _invalidate_game_cache_index()
            logger.info(f"Deleted game cache entry {np_communication_id}")
            return True
        logger.error(f"Failed to delete game cache for {np_communication_id}")
        return False
    except Exception as e:
        logger.error(f"Error deleting game cache for {np_communication_id}: {e}")
        return False


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
        self._cached_target_account_id: str | None = None
        self._cached_target_username_key: str | None = None
        self.psn_data: PSNData = PSNData()
        if npsso_code:
            self.psn_data.npsso_code = npsso_code

    def _clear_target_account_cache(self) -> None:
        self._cached_target_account_id = None
        self._cached_target_username_key = None

    def update_npsso_code(self, npsso_code: str):
        """Updates the NPSSO code and resets authentication status."""
        self.npsso_code = npsso_code
        self.psn_data.npsso_code = npsso_code
        self.api = None
        self.authenticated = False
        self._auth_expired = False
        self._auth_expired_warned = False
        self._clear_target_account_cache()
        self.psn_data.connection_status = (
            "Not Connected" if not (npsso_code or "").strip() else "Disconnected"
        )
        logger.info("NPSSO code updated. Re-authentication will be attempted.")

    def update_psn_username(self, psn_username: str):
        """Updates the target PSN username for API calls."""
        self.psn_username = psn_username
        self._clear_target_account_cache()
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
        self._clear_target_account_cache()
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
                with _game_cache_index_lock:
                    if _game_cache_index_loaded:
                        _index_game_cache_doc(game_data)
                    else:
                        # Index not built yet; next lookup will load from DB
                        pass
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
        Uses the in-memory index (built lazily from the DB cache).

        Args:
            np_title_id: The game's npTitleId from presence data (e.g., "CUSA12345_00")

        Returns:
            Game data dict if found, None otherwise
        """
        try:
            if not np_title_id:
                return None
            _ensure_game_cache_index()
            with _game_cache_index_lock:
                game_data = _game_cache_by_title_id.get(str(np_title_id))
            if game_data:
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

    def find_game_by_name(
        self, game_name: str, platform: str | None = None
    ) -> dict | None:
        """
        Find cached game data by game name (presence_name or trophy_name).

        Cascade: exact lower / normalized index keys, then conservative fuzzy
        scan of cached docs (unique winner above threshold only).

        Args:
            game_name: The game name to search for
            platform: Optional presence platform (PS4/PS5) to prefer on fuzzy ties

        Returns:
            Game data dict if found, None otherwise
        """
        try:
            if not game_name:
                return None
            _ensure_game_cache_index()
            keys = (
                game_name.lower(),
                _normalize_game_name_key(game_name),
            )
            with _game_cache_index_lock:
                for key in keys:
                    if not key:
                        continue
                    game_data = _game_cache_by_name.get(key)
                    if game_data:
                        logger.debug(
                            f"Found cached game by name '{game_name}': "
                            f"{game_data.get('np_communication_id', '')}"
                        )
                        return game_data
                cache_docs = list(_game_cache_by_comm_id.values())

            fuzzy = find_best_fuzzy_game_name_match(
                game_name, cache_docs, platform=platform
            )
            if fuzzy:
                return fuzzy

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
        self._clear_target_account_cache()
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
        username_key = self.psn_username or ""
        if (
            self._cached_target_account_id
            and self._cached_target_username_key == username_key
        ):
            return self._cached_target_account_id

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
                    self._clear_target_account_cache()
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
        self._cached_target_account_id = target_account_id
        self._cached_target_username_key = username_key
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

    def get_overall_trophy_summary(self, _allow_retry: bool = True) -> dict | None:
        """Fetches the overall trophy summary for the user or specified PSN username."""
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get trophy summary, not connected.")
            if not self.connect():  # Try to reconnect
                return None

        target_account_id = self.get_trophy_target_account_id(_allow_retry=_allow_retry)
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

        target_account_id = self.get_trophy_target_account_id()
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

    def get_title_trophy_progress(self, np_title_id: str) -> dict | None:
        """
        Fetch title-level trophy progress for one presence ``npTitleId`` via
        ``trophy_titles_for_title`` (single HTTP request). Updates psn_data progress
        and trophy counts; returns summary including ``np_communication_id``.
        """
        if not np_title_id:
            return None
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get title trophy progress, not connected.")
            if not self.connect():
                return None

        target_account_id = self.get_trophy_target_account_id()
        if not target_account_id:
            logger.warning(
                "Cannot get title trophy progress, no target account_id available."
            )
            return None

        logger.debug(
            "Fetching title trophy progress for np_title_id=%s account=%s",
            np_title_id,
            target_account_id,
        )
        try:
            user = self.api.user(account_id=target_account_id)  # type: ignore
            titles = list(user.trophy_titles_for_title(title_ids=[np_title_id]))
            if not titles:
                logger.warning(
                    "trophy_titles_for_title returned no titles for %s", np_title_id
                )
                return None

            title = titles[0]
            np_communication_id = getattr(title, "np_communication_id", None)
            earned = _trophy_set_to_counts(getattr(title, "earned_trophies", None))
            defined = _trophy_set_to_counts(getattr(title, "defined_trophies", None))
            progress = getattr(title, "progress", None)
            if progress is None and defined["total"] > 0:
                progress = round((earned["total"] / defined["total"]) * 100)
            elif progress is None:
                progress = 0

            self.psn_data.current_game_np_comm_id = np_communication_id
            self.psn_data.current_game_progress = int(progress) if progress is not None else 0
            # Title-level aggregates (may include DLC). Used until groups refresh splits base/all.
            self.psn_data.current_game_trophies = {
                "defined": dict(defined),
                "earned": dict(earned),
            }
            self.psn_data.current_game_trophies_all = {
                "defined": dict(defined),
                "earned": dict(earned),
            }

            result = {
                "np_title_id": np_title_id,
                "np_communication_id": np_communication_id,
                "title_name": getattr(title, "title_name", None),
                "icon_url": getattr(title, "title_icon_url", None),
                "progress": self.psn_data.current_game_progress,
                "earned_trophies": earned,
                "defined_trophies": defined,
                "has_trophy_groups": bool(getattr(title, "has_trophy_groups", False)),
                "platform": getattr(title, "title_platform", None),
            }
            logger.info(
                "Title trophy progress for %s: %s%% (np_communication_id=%s)",
                np_title_id,
                self.psn_data.current_game_progress,
                np_communication_id,
            )
            return result
        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            logger.error(
                "API error fetching title trophy progress for %s: %s", np_title_id, e
            )
        except Exception as e:
            if _is_psn_auth_expired_error(e):
                self._on_psn_auth_expired(
                    f"get_title_trophy_progress({np_title_id})", e
                )
            elif _is_psn_connection_error(e):
                logger.warning(
                    "Connection error fetching title trophy progress for %s: %s",
                    np_title_id,
                    e,
                )
                self.authenticated = False
                self.api = None
                self._clear_target_account_cache()
            else:
                logger.exception(
                    "Unexpected error fetching title trophy progress for %s: %s",
                    np_title_id,
                    e,
                )
        return None

    def get_game_trophies(
        self, np_communication_id: str, platform: str, trophy_group_id: str = "default"
    ) -> dict | None:
        """
        Fetches per-group trophy progress via ``trophy_groups_summary`` (base vs DLC).
        Prefer ``get_title_trophy_progress`` for the hot path; call this on a slower cadence.
        """
        if not self.is_connected() or not self.api:
            logger.warning("Cannot get game trophies, not connected.")
            if not self.connect():
                return None

        target_account_id = self.get_trophy_target_account_id()
        if not target_account_id:
            logger.warning("Cannot get game trophies, no target account_id available.")
            return None

        logger.debug(
            "Fetching game trophy groups for: %s on %s (group: %s)",
            np_communication_id,
            platform,
            trophy_group_id,
        )

        try:
            user = self.api.user(account_id=target_account_id)  # type: ignore
            platform_type = presence_format_to_platform_type(platform)
            trophy_groups_summary = user.trophy_groups_summary(  # type: ignore
                np_communication_id=np_communication_id,
                platform=platform_type,
                include_progress=True,
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

            if not trophy_groups_summary:
                logger.warning("No trophy groups summary returned")
                self.psn_data.current_game_trophies_all = {}
                self.psn_data.current_game_trophies = {
                    "defined": defined_counts,
                    "earned": earned_counts,
                }
                return None

            trophy_groups = getattr(trophy_groups_summary, "trophy_groups", None) or []
            logger.debug("Found %d trophy groups", len(trophy_groups))
            cached_trophy_groups = _serialize_trophy_groups_for_cache(trophy_groups)

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

            def _add_set(dest: dict[str, int], src: Any) -> None:
                counts = _trophy_set_to_counts(src)
                for key in ("bronze", "silver", "gold", "platinum"):
                    dest[key] += counts[key]

            for agroup in trophy_groups:
                _add_set(
                    all_defined_counts, getattr(agroup, "defined_trophies", None)
                )
                _add_set(all_earned_counts, getattr(agroup, "earned_trophies", None))

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

            selected_group = None
            for group in trophy_groups:
                if getattr(group, "trophy_group_id", "") == trophy_group_id:
                    selected_group = group
                    break
            if selected_group is None and trophy_groups:
                selected_group = trophy_groups[0]
                logger.debug(
                    "Group %r not found; using first group %r",
                    trophy_group_id,
                    getattr(selected_group, "trophy_group_id", "unknown"),
                )

            if selected_group is not None:
                defined_counts = _trophy_set_to_counts(
                    getattr(selected_group, "defined_trophies", None)
                )
                earned_counts = _trophy_set_to_counts(
                    getattr(selected_group, "earned_trophies", None)
                )

            self.psn_data.current_game_trophies = {
                "defined": defined_counts,
                "earned": earned_counts,
            }

            if defined_counts["total"] > 0:
                progress = round(
                    (earned_counts["total"] / defined_counts["total"]) * 100
                )
                self.psn_data.current_game_progress = progress
                logger.info(
                    "Game progress calculated: %s%% (%s/%s)",
                    progress,
                    earned_counts["total"],
                    defined_counts["total"],
                )
            elif all_defined_counts["total"] > 0:
                progress = round(
                    (all_earned_counts["total"] / all_defined_counts["total"]) * 100
                )
                self.psn_data.current_game_progress = progress
            else:
                self.psn_data.current_game_progress = 0
                logger.warning("No defined trophies found, setting progress to 0%")

            self.psn_data.current_game_np_comm_id = np_communication_id
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.info(
                "Successfully fetched group trophies for game %s for %s",
                np_communication_id,
                target_desc,
            )
            return {
                "trophy_groups_summary": trophy_groups_summary,
                "trophy_groups": cached_trophy_groups,
            }

        except (PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError) as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            logger.error(
                "API error while fetching game trophies for %s for %s: %s",
                np_communication_id,
                target_desc,
                e,
            )
        except Exception as e:
            target_desc = (
                self.psn_username if self.psn_username else "authenticated user"
            )
            if _is_psn_auth_expired_error(e):
                self._on_psn_auth_expired(
                    f"get_game_trophies({np_communication_id}, {target_desc})",
                    e,
                )
            elif _is_psn_connection_error(e):
                logger.warning(
                    "Connection error fetching game trophies for %s for %s: %s",
                    np_communication_id,
                    target_desc,
                    e,
                )
                self.authenticated = False
                self.api = None
                self._clear_target_account_cache()
            else:
                logger.exception(
                    "Unexpected error fetching game trophies for %s for %s: %s",
                    np_communication_id,
                    target_desc,
                    e,
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

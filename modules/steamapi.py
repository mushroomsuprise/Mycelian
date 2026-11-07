# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Steam Web API client: the tracked profile, the game it is playing and that game's
achievements. Endpoints go through ``python-steam-api`` (``steam_web_api``); its HTTP client is
replaced so every request has a timeout and errors never carry the API key (Steam puts the key
in the query string, and requests' error messages quote the URL).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import requests
from steam_web_api.apps import Apps
from steam_web_api.client import Client
from steam_web_api.constants import API_BASE_URL
from steam_web_api.users import Users
from steam_web_api.utils import buildUrlWithParams

from . import api_usage
from .steam_data import SteamData

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = (5, 15)  # connect, read (seconds)
STEAM_CDN = "https://cdn.akamai.steamstatic.com/steam/apps"
ACHIEVEMENT_ICON_BASE = "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps"
PERSONA_STATES = {
    0: "Offline",
    1: "Online",
    2: "Busy",
    3: "Away",
    4: "Snooze",
    5: "Looking to trade",
    6: "Looking to play",
}
RECENT_UNLOCKS_MAX = 10
# Kept in the Games Tracker catalog (game_tracker.db), so they survive restarts.
SCHEMA_TTL_SEC = 30 * 86400.0  # re-read sooner when the player's list has a different size
GLOBAL_PERCENT_TTL_SEC = 7 * 86400.0
FAILED_PERCENT_RETRY_SEC = 6 * 3600.0  # memory only: a failed percentage read

_STEAMID64_RE = re.compile(r"^7656119\d{10}$")
_PROFILE_URL_RE = re.compile(r"steamcommunity\.com/(profiles|id)/([^/?#\s]+)", re.IGNORECASE)
_VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")


class SteamAPIError(Exception):
    """A Steam Web API call failed. ``status`` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class SteamKeyError(SteamAPIError):
    """Steam refused the API key."""


class SteamConnectionError(SteamAPIError):
    """Steam could not be reached."""


class SteamRateLimited(SteamAPIError):
    """Steam answered 429 (or we are still waiting out an earlier 429); nothing was sent."""


# One pause shared by every Steam client: a 429 holds back the poller and the library sync alike.
rate_limit = api_usage.Backoff(base=60.0, cap=900.0)


def redact_key(text: str, key: str) -> str:
    """Hide the API key wherever it appears (URLs in error messages)."""
    text = str(text or "")
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", text)


class _TimeoutClient(Client):
    """``steam_web_api`` client with timeouts, a pooled session and key-free errors."""

    def __init__(self, key: str = ""):
        super().__init__(key)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def request(self, method: str, url: str, data: Any = None, params: dict | None = None, headers: dict | None = None):
        if rate_limit.active():
            raise SteamRateLimited(f"Steam rate limit: waiting {rate_limit.remaining():.0f} s", 429)
        request_url = buildUrlWithParams(API_BASE_URL + url, self.key, params or {})
        endpoint = api_usage.endpoint_name(url)
        try:
            result = self._session.request(method, request_url, headers=headers or None, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            api_usage.record("Steam", endpoint, None)
            raise SteamConnectionError(redact_key(f"{type(e).__name__}: {e}", self.key)) from None
        api_usage.record("Steam", endpoint, result.status_code)
        try:
            body = result.json()
        except ValueError:
            body = None
        if result.status_code == 429:
            wait = rate_limit.hit(result.headers.get("Retry-After"))
            logger.warning("Steam rate limit (429) on %s; pausing Steam requests for %.0f s", endpoint, wait)
            raise SteamRateLimited(f"429 Too Many Requests: paused {wait:.0f} s", 429, body)
        if result.status_code < 400:
            rate_limit.ok()
        if result.status_code >= 400:
            message = f"{result.status_code} {result.reason}"
            error = body.get("playerstats", {}).get("error") if isinstance(body, dict) else None
            if error:
                raise SteamAPIError(f"{message}: {error}", result.status_code, body)
            if result.status_code in (401, 403) or "verify your key" in (result.text or "").lower():
                raise SteamKeyError(f"{message}: the Steam Web API key was refused", result.status_code, body)
            raise SteamAPIError(message, result.status_code, body)
        if body is None:
            raise SteamAPIError(f"{result.status_code}: not a JSON response", result.status_code)
        return body


def parse_profile(text: str | None) -> tuple[str, str] | None:
    """("id", SteamID64) or ("vanity", custom URL name) from a profile URL, ID or name."""
    text = str(text or "").strip()
    if not text:
        return None
    match = _PROFILE_URL_RE.search(text)
    if match:
        kind, value = match.group(1).lower(), match.group(2)
        if kind == "profiles":
            return ("id", value) if _STEAMID64_RE.match(value) else None
        return ("vanity", value)
    if _STEAMID64_RE.match(text):
        return ("id", text)
    if _VANITY_RE.match(text):
        return ("vanity", text)
    return None


def game_art(appid: int | None) -> tuple[str | None, str | None]:
    """(store header URL, library capsule URL) for a Steam app."""
    if not appid:
        return None, None
    return f"{STEAM_CDN}/{appid}/header.jpg", f"{STEAM_CDN}/{appid}/library_600x900.jpg"


def _icon_url(appid: int, icon: str | None) -> str | None:
    icon = str(icon or "").strip()
    if not icon:
        return None
    if icon.startswith("https://"):
        return icon
    if icon.startswith("http://"):
        return "https://" + icon[len("http://"):]
    return f"{ACHIEVEMENT_ICON_BASE}/{appid}/{icon}.jpg"


def _catalog(kind: str, appid: int) -> tuple[dict, float] | None:
    """(data, fetched_at) from the Games Tracker catalog, or None (never raises)."""
    try:
        from .game_tracker import catalog

        return catalog.steam_schema(appid) if kind == "steam_schema" else catalog.steam_percents(appid)
    except Exception:
        logger.debug("Steam catalog read failed for %s %s", kind, appid, exc_info=True)
        return None


def _catalog_save(fn: str, appid: int, data: dict) -> None:
    try:
        from .game_tracker import catalog

        getattr(catalog, fn)(appid, data)
    except Exception:
        logger.debug("Steam catalog write failed for %s", appid, exc_info=True)


def _percent(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


class SteamClient:
    """Fetches the tracked profile, its current game and that game's achievements."""

    def __init__(self, api_key: str | None = None, profile: str | None = None, steam_id: str | None = None):
        self.api_key = (api_key or "").strip()
        self.profile = (profile or "").strip()
        self.steam_id = (steam_id or "").strip() or None
        self.connected = False
        self.steam_data = SteamData(connection_status="Not Connected" if not self.api_key else "Disconnected")
        self._lock = threading.RLock()
        self._client: _TimeoutClient | None = None
        self._users: Users | None = None
        self._apps: Apps | None = None
        self._resolved_for: str | None = self.profile if self.steam_id else None
        # appid -> (schema achievements by api name, fetched at)
        self._schemas: dict[int, tuple[dict[str, dict], float]] = {}
        # appid -> ({api name: percent}, fetched at)
        self._global_percents: dict[int, tuple[dict[str, float], float]] = {}
        # appid -> api names already unlocked (the first load sets the baseline)
        self._known_unlocked: dict[int, set[str]] = {}
        # appid -> the player's achievement count the schema was last re-read for
        self._schema_rechecked: dict[int, int] = {}

    # ----- setup -----

    def _api(self) -> tuple[Users, Apps, _TimeoutClient]:
        if self._client is None:
            self._client = _TimeoutClient(self.api_key)
            self._users = Users(self._client)
            self._apps = Apps(self._client)
        return self._users, self._apps, self._client  # type: ignore[return-value]

    def update_credentials(self, api_key: str, profile: str) -> None:
        with self._lock:
            key_changed = (api_key or "").strip() != self.api_key
            profile_changed = (profile or "").strip() != self.profile
            self.api_key = (api_key or "").strip()
            self.profile = (profile or "").strip()
            if key_changed:
                self._client = self._users = self._apps = None
            if profile_changed:
                self.steam_id = None
                self._resolved_for = None
                self._known_unlocked.clear()
            self.connected = False
            self.steam_data = SteamData(connection_status="Not Connected" if not self.api_key else "Disconnected")

    def is_connected(self) -> bool:
        return self.connected

    # ----- profile -----

    def resolve_steam_id(self) -> str | None:
        """SteamID64 for the configured profile (cached until the profile changes)."""
        if self.steam_id and self._resolved_for == self.profile:
            return self.steam_id
        parsed = parse_profile(self.profile)
        if parsed is None:
            return None
        kind, value = parsed
        if kind == "id":
            steam_id = value
        else:
            users, _apps, _client = self._api()
            response = users.get_steamid(value) or {}
            steam_id = str(response.get("steamid") or "") if response.get("success") == 1 else ""
        self.steam_id = steam_id or None
        self._resolved_for = self.profile
        return self.steam_id

    def connect(self) -> bool:
        """Check the key and profile, and load the profile summary."""
        with self._lock:
            data = self.steam_data
            if not self.api_key:
                self.connected = False
                data.connection_status = "Not Connected"
                return False
            if not self.profile:
                self.connected = False
                data.connection_status = "Profile Not Set"
                return False
            try:
                if not self.resolve_steam_id():
                    self.connected = False
                    data.connection_status = "Profile Not Found"
                    return False
                if not self.refresh_summary():
                    self.connected = False
                    data.connection_status = "Profile Not Found"
                    return False
                self.connected = True
                return True
            except SteamKeyError as e:
                logger.warning("Steam refused the API key: %s", e)
                data.connection_status = "Invalid API Key"
            except SteamConnectionError as e:
                logger.warning("Steam unreachable: %s", e)
                data.connection_status = "Disconnected"
            except Exception as e:
                logger.warning("Steam connect failed: %s", redact_key(str(e), self.api_key))
                data.connection_status = "Disconnected"
            self.connected = False
            return False

    def refresh_summary(self) -> bool:
        """Update persona, avatar and the current game. False when the profile is unknown."""
        users, _apps, _client = self._api()
        player = (users.get_user_details(self.steam_id) or {}).get("player")
        if not player:
            return False
        data = self.steam_data
        data.steam_id = str(player.get("steamid") or self.steam_id)
        data.persona_name = player.get("personaname") or data.persona_name
        data.profile_url = player.get("profileurl") or data.profile_url
        avatar = str(player.get("avatarfull") or player.get("avatarmedium") or "")
        data.avatar_url = ("https://" + avatar[7:]) if avatar.startswith("http://") else (avatar or None)
        state = int(player.get("personastate") or 0)
        data.persona_state = PERSONA_STATES.get(state, "Online")
        data.is_online = state != 0
        data.connection_status = "Connected" if data.is_online else "Offline"

        appid_raw = player.get("gameid")
        appid = int(appid_raw) if str(appid_raw or "").isdigit() else None
        if appid != data.current_game_appid:
            data.current_game_appid = appid
            data.current_game_art_url, data.current_game_capsule_url = game_art(appid)
            data.has_achievements = False
            data.achievements_private = False
            data.achievement_counts = {}
            data.current_game_progress = None
            data.current_game_achievements = []
        data.current_game_name = (player.get("gameextrainfo") or None) if appid else None
        return True

    def refresh_level(self) -> None:
        users, _apps, _client = self._api()
        response = users.get_user_steam_level(self.steam_id) or {}  # already the "response" object
        level = response.get("player_level")
        self.steam_data.steam_level = int(level) if level is not None else None

    # ----- achievements -----

    def _schema(self, appid: int, *, refresh: bool = False) -> dict[str, dict]:
        """Achievement definitions by API name: memory, then the catalog, then Steam (one request)."""
        now = time.time()
        cached = self._schemas.get(appid)
        if not refresh and cached and now - cached[1] < SCHEMA_TTL_SEC:
            return cached[0]
        if not refresh:
            stored = _catalog("steam_schema", appid)
            if stored is not None and now - stored[1] < SCHEMA_TTL_SEC:
                self._schemas[appid] = stored
                return stored[0]
        _users, _apps, client = self._api()
        body = client.request("get", "/ISteamUserStats/GetSchemaForGame/v2/", params={"appid": appid, "l": "english"})
        game = body.get("game") or {}
        if appid == self.steam_data.current_game_appid and not self.steam_data.current_game_name and game.get("gameName"):
            self.steam_data.current_game_name = game["gameName"]
        entries = ((game.get("availableGameStats") or {}).get("achievements")) or []
        schema = {str(a.get("name")): a for a in entries if a.get("name")}
        self._schemas[appid] = (schema, time.time())
        _catalog_save("save_steam_schema", appid, schema)
        return schema

    def _percents(self, appid: int) -> dict[str, float]:
        now = time.time()
        cached = self._global_percents.get(appid)
        if cached and now - cached[1] < (GLOBAL_PERCENT_TTL_SEC if cached[0] else FAILED_PERCENT_RETRY_SEC):
            return cached[0]
        stored = _catalog("steam_percents", appid)
        if stored is not None and now - stored[1] < GLOBAL_PERCENT_TTL_SEC:
            self._global_percents[appid] = stored
            return stored[0]
        _users, _apps, client = self._api()
        try:
            body = client.request("get", "/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/", params={"gameid": appid})
            entries = (body.get("achievementpercentages") or {}).get("achievements") or []
            percents = {str(a.get("name")): p for a in entries if (p := _percent(a.get("percent"))) is not None}
            _catalog_save("save_steam_percents", appid, percents)
        except SteamRateLimited:
            raise  # not cached: try again once the pause is over
        except SteamAPIError as e:
            logger.debug("Steam global achievement percentages unavailable for %s: %s", appid, e)
            percents = {}
        self._global_percents[appid] = (percents, time.time())
        return percents

    def refresh_achievements(self) -> list[dict[str, Any]]:
        """Load the current game's achievements. Returns achievements unlocked since the last call."""
        data = self.steam_data
        appid = data.current_game_appid
        if not appid:
            return []
        schema = self._schema(appid)
        if not schema:
            data.has_achievements = False
            data.achievement_counts = {}
            data.current_game_progress = None
            data.current_game_achievements = []
            return []
        _users, apps, _client = self._api()
        try:
            body = apps.get_user_achievements(self.steam_id, appid, language="english")
        except SteamAPIError as e:
            if e.status in (400, 403) and "not public" in str(e).lower():
                data.achievements_private = True
                data.has_achievements = True
                data.achievement_counts = {"unlocked": 0, "total": len(schema)}
                data.current_game_progress = None
                data.current_game_achievements = []
                return []
            raise
        data.achievements_private = False
        player = {str(a.get("apiname")): a for a in ((body.get("playerstats") or {}).get("achievements") or [])}
        if player and len(player) != len(schema) and self._schema_rechecked.get(appid) != len(player):
            # The game gained (or lost) achievements since the schema was read: read it again, once.
            self._schema_rechecked[appid] = len(player)
            schema = self._schema(appid, refresh=True) or schema
        percents = self._percents(appid)

        merged: list[dict[str, Any]] = []
        for api_name, meta in schema.items():
            mine = player.get(api_name) or {}
            achieved = bool(mine.get("achieved"))
            merged.append({
                "api_name": api_name,
                "name": meta.get("displayName") or api_name,
                "description": meta.get("description") or "",
                "icon_url": _icon_url(appid, meta.get("icon")),
                "icon_gray_url": _icon_url(appid, meta.get("icongray")),
                "achieved": achieved,
                "unlock_time": int(mine.get("unlocktime") or 0) if achieved else 0,
                "hidden": bool(meta.get("hidden")),
                "global_percent": percents.get(api_name),
            })
        # Unlocked newest first, then locked from most to least common.
        merged.sort(key=lambda a: (not a["achieved"], -a["unlock_time"], -(a["global_percent"] or 0)))
        unlocked = [a for a in merged if a["achieved"]]
        data.has_achievements = True
        data.current_game_achievements = merged
        data.achievement_counts = {"unlocked": len(unlocked), "total": len(merged)}
        data.current_game_progress = round(len(unlocked) * 100 / len(merged)) if merged else None

        names = {a["api_name"] for a in unlocked}
        known = self._known_unlocked.get(appid)
        self._known_unlocked[appid] = names
        if known is None:
            return []  # first look at this game: nothing is "new"
        new = sorted((a for a in unlocked if a["api_name"] not in known), key=lambda a: a["unlock_time"])
        for achievement in new:
            data.recent_unlocks.insert(0, dict(achievement, game_name=data.current_game_name, appid=appid))
        del data.recent_unlocks[RECENT_UNLOCKS_MAX:]
        if new:
            data.last_unlock = data.recent_unlocks[0]
        return new

    # ----- library (Games Tracker) -----

    def get_owned_games(self) -> list[dict[str, Any]]:
        """Every game on the profile: appid, name, playtime (minutes), last played (epoch) and icon hash."""
        steam_id = self.resolve_steam_id()
        if not steam_id:
            return []
        _users, _apps, client = self._api()
        body = client.request(
            "get",
            "/IPlayerService/GetOwnedGames/v1/",
            params={"steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1},
        )
        games = (body.get("response") or {}).get("games") or []
        out: list[dict[str, Any]] = []
        for g in games:
            try:
                appid = int(g.get("appid"))
            except (TypeError, ValueError):
                continue
            out.append({
                "appid": appid,
                "name": str(g.get("name") or ""),
                "playtime_min": int(g.get("playtime_forever") or 0),
                "last_played": int(g.get("rtime_last_played") or 0),
                "icon_hash": str(g.get("img_icon_url") or ""),
            })
        return out

    def get_game_achievements(self, appid: int) -> dict[str, Any]:
        """One game's achievements for the tracked profile, without touching the live current-game state.

        ``{"has_achievements": bool, "private": bool, "total": int, "unlocked": {api name: {...}}}``.
        Games without stats come back with ``has_achievements`` False.
        """
        steam_id = self.resolve_steam_id()
        if not steam_id:
            return {"has_achievements": False, "private": False, "total": 0, "unlocked": {}}
        _users, _apps, client = self._api()
        try:
            body = client.request(
                "get",
                "/ISteamUserStats/GetPlayerAchievements/v1/",
                params={"appid": int(appid), "steamid": steam_id, "l": "english"},
            )
        except SteamAPIError as e:
            text = str(e).lower()
            if e.status in (400, 403) and "not public" in text:
                return {"has_achievements": True, "private": True, "total": 0, "unlocked": {}}
            if e.status in (400, 403, 500) and ("no stats" in text or "not found" in text or e.status == 500):
                return {"has_achievements": False, "private": False, "total": 0, "unlocked": {}}
            raise
        stats = body.get("playerstats") or {}
        entries = stats.get("achievements") or []
        unlocked: dict[str, dict[str, Any]] = {}
        for a in entries:
            if not a.get("achieved"):
                continue
            api_name = str(a.get("apiname") or "")
            if not api_name:
                continue
            unlocked[api_name] = {
                "name": a.get("name") or api_name,
                "description": a.get("description") or "",
                "unlock_time": int(a.get("unlocktime") or 0),
            }
        return {"has_achievements": bool(entries), "private": False, "total": len(entries), "unlocked": unlocked}

    def achievement_list(self, appid: int, earned: Any = None) -> list[dict[str, Any]]:
        """Every achievement of a game in schema order, for picking a Games Tracker goal:
        ``id`` (API name), ``name``, ``description``, ``icon``, ``grade`` ("achievement"), ``earned``.

        Pass ``earned`` (API names, e.g. the tracker's stored unlocks) to skip the player request."""
        appid = int(appid)
        schema = self._schema(appid)
        if not schema:
            return []
        if earned is not None:
            earned = set(earned)
        else:
            try:
                earned = set(self.get_game_achievements(appid).get("unlocked") or {})
            except SteamAPIError as e:
                logger.debug("Steam achievements for %s unavailable: %s", appid, e)
                earned = set()
        return [
            {
                "id": api_name,
                "name": meta.get("displayName") or api_name,
                "description": meta.get("description") or "",
                "icon": _icon_url(appid, meta.get("icon")),
                "grade": "achievement",
                "earned": api_name in earned,
            }
            for api_name, meta in schema.items()
        ]

    def global_percents(self, appid: int) -> dict[str, float]:
        """{api name: percent of players} for an app (cached)."""
        return self._percents(int(appid))


def public_payload(data: SteamData) -> dict[str, Any]:
    """JSON-safe Steam state for overlays (holds no secrets)."""
    from dataclasses import asdict

    payload = asdict(data)
    json.dumps(payload)  # fail here, not in the emitter
    return payload

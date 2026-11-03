# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Steam library sync for the Games Tracker.

Only games with playtime are tracked. The first sweep reads every played game's
achievements, one request at a time (``REQUEST_GAP_SEC`` apart); later sweeps re-read only
games whose playtime or last-played time moved. ``observe_game`` takes the achievement list
``steam_service`` already loaded for the game being played, so unlocks show up within a poll.
All of it runs on the tracker's Steam worker thread.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .deltas import diff
from .models import STEAM, UNLOCK, GameEvent, TrackedGame, TrackerSettings
from . import covers
from .store import GameTrackerStore

logger = logging.getLogger(__name__)

REQUEST_GAP_SEC = 1.0  # between per-game achievement requests
BATCH_SAVE = 20


STORE_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


def store_header_url(appid: Any) -> Optional[str]:
    """The store's own header image URL for an app (newer games keep art at hashed addresses). No API key needed."""
    import requests

    r = requests.get(STORE_DETAILS_URL, params={"appids": str(appid), "filters": "basic"}, timeout=15)
    r.raise_for_status()
    entry = (r.json() or {}).get(str(appid)) or {}
    url = str(((entry.get("data") or {}) if entry.get("success") else {}).get("header_image") or "")
    return url if url.startswith("https://") else None


def cover_url(appid: Any) -> str:
    from ..steamapi import game_art

    header, _capsule = game_art(int(appid))
    return header or ""


def _merge(old: Optional[TrackedGame], new: TrackedGame) -> TrackedGame:
    now = time.time()
    if old is not None:
        new.title_override = old.title_override
        new.hidden = old.hidden
        new.first_seen = old.first_seen or now
        new.title = new.title or old.title
        new.last_played = max(new.last_played, old.last_played)
        new.playtime_min = max(new.playtime_min, old.playtime_min)
        new.extra = {**old.extra, **new.extra}
    else:
        new.first_seen = now
    return new


def _with_achievements(game: TrackedGame, ach: Dict[str, Any]) -> TrackedGame:
    game.has_achievements = bool(ach.get("has_achievements"))
    game.total_achievements = int(ach.get("total") or 0)
    unlocked = ach.get("unlocked") or {}
    # Oldest unlock first, so events come out in the order they happened.
    game.unlocked_ids = sorted(unlocked, key=lambda k: (unlocked[k].get("unlock_time") or 0, k))
    game.progress = round(len(game.unlocked_ids) * 100 / game.total_achievements) if game.total_achievements else 0
    game.extra["achievement_names"] = {k: v.get("name") or k for k, v in unlocked.items()}
    game.extra["achievement_details"] = {k: v.get("description") or "" for k, v in unlocked.items()}
    if ach.get("private"):
        game.extra["private"] = True
    else:
        game.extra.pop("private", None)
    return game


def _baseline(store: GameTrackerStore, account: str) -> bool:
    base = store.baseline(STEAM)
    if base is None:
        return False
    if base.get("account") != account:
        logger.info("Games Tracker: Steam profile changed; starting a new silent import")
        store.clear_platform(STEAM)
        store.delete_meta(f"baseline:{STEAM}")
        return False
    return True


def _needs_achievements(old: Optional[TrackedGame], owned: Dict[str, Any]) -> bool:
    if old is None or not old.baselined:
        return True
    if old.extra.get("private"):
        return True
    return int(owned["last_played"] or 0) > int(old.last_played or 0) or int(owned["playtime_min"]) > old.playtime_min


def sync_library(
    client,
    store: GameTrackerStore,
    settings: TrackerSettings,
    *,
    stop_event: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Optional[List[GameEvent]]:
    """Read the played games. Returns the changes (empty during the first import), or None when Steam was unavailable."""
    from ..steamapi import SteamAPIError, SteamConnectionError, SteamKeyError

    if client is None:
        return None
    try:
        steam_id = client.resolve_steam_id()
        owned = client.get_owned_games() if steam_id else None
    except (SteamKeyError, SteamConnectionError) as e:
        logger.warning("Games Tracker: Steam library unavailable: %s", e)
        return None
    except SteamAPIError as e:
        logger.warning("Games Tracker: Steam library request failed: %s", e)
        return None
    if not steam_id or owned is None:
        return None

    baseline_done = _baseline(store, steam_id)
    stored = store.games_by_id(STEAM)
    played = sorted((g for g in owned if g["playtime_min"] > 0), key=lambda g: g["last_played"], reverse=True)
    events: List[GameEvent] = []
    pending: List[TrackedGame] = []
    total = len(played)
    private = False
    for i, owned_game in enumerate(played, 1):
        if stop_event is not None and stop_event.is_set():
            break
        appid = str(owned_game["appid"])
        old = stored.get(appid)
        new = TrackedGame(
            platform=STEAM,
            game_id=appid,
            title=owned_game["name"],
            platform_label="Steam",
            cover_url=cover_url(appid),
            playtime_min=int(owned_game["playtime_min"]),
            last_played=float(owned_game["last_played"] or 0),
        )
        new = _merge(old, new)
        if not private and _needs_achievements(old, owned_game):
            try:
                ach = client.get_game_achievements(int(appid))
                private = bool(ach.get("private"))
                _with_achievements(new, ach)
                # A game first seen after the import is new play (every unlock counts); a game whose
                # earlier read failed (``old.baselined`` False) is read silently.
                events.extend(diff(old, new, baseline_done=baseline_done, milestones=settings.milestones))
                new.baselined = True
            except (SteamKeyError, SteamConnectionError):
                raise
            except SteamAPIError as e:
                logger.debug("Games Tracker: achievements unavailable for %s: %s", appid, e)
                _keep_achievements(old, new)
            if stop_event is not None:
                stop_event.wait(REQUEST_GAP_SEC)
            else:
                time.sleep(REQUEST_GAP_SEC)
        else:
            _keep_achievements(old, new)
        pending.append(new)
        if len(pending) >= BATCH_SAVE:
            store.upsert_games(pending)
            pending = []
        if progress:
            progress(i, total)
    store.upsert_games(pending)
    if not baseline_done and not (stop_event is not None and stop_event.is_set()):
        store.set_baseline(STEAM, steam_id)
    name_unlocks(client, events, store)
    return events


def _keep_achievements(old: Optional[TrackedGame], new: TrackedGame) -> None:
    """Carry the last read over; a game never read stays un-baselined so its first read is silent."""
    if old is None:
        new.baselined = False
        return
    new.unlocked_ids = list(old.unlocked_ids)
    new.total_achievements = old.total_achievements
    new.progress = old.progress
    new.has_achievements = old.has_achievements
    new.baselined = old.baselined


def observe_game(store: GameTrackerStore, settings: TrackerSettings, account: str, appid: Any, name: str, achievements: List[Dict[str, Any]]) -> List[GameEvent]:
    """The game ``steam_service`` just refreshed (its merged achievement list)."""
    base = store.baseline(STEAM)
    if base is None or base.get("account") != str(account):
        return []
    appid = str(appid)
    old = store.get_game(STEAM, appid)
    unlocked = {
        str(a.get("api_name")): {"name": a.get("name"), "description": a.get("description"), "unlock_time": a.get("unlock_time")}
        for a in achievements or []
        if a.get("achieved") and a.get("api_name")
    }
    new = TrackedGame(platform=STEAM, game_id=appid, title=name or (old.title if old else ""), platform_label="Steam",
                      cover_url=cover_url(appid), last_played=time.time(), playtime_min=old.playtime_min if old else 0)
    new = _merge(old, new)
    _with_achievements(new, {"has_achievements": bool(achievements), "total": len(achievements or []), "unlocked": unlocked})
    rarity = {str(a.get("api_name")): a.get("global_percent") for a in achievements or []}
    icons = {str(a.get("api_name")): a.get("icon_url") or "" for a in achievements or []}
    events = diff(old, new, baseline_done=True, milestones=settings.milestones)
    new.baselined = True
    store.upsert_games([new])
    _fill_names(events, new, rarity, icons)
    return events


def _fill_names(events: Iterable[GameEvent], game: TrackedGame, rarity: Dict[str, Any], icons: Dict[str, str]) -> None:
    names = game.extra.get("achievement_names") or {}
    details = game.extra.get("achievement_details") or {}
    for e in events:
        if e.kind != UNLOCK or e.platform != STEAM or e.game_id != game.game_id:
            continue
        api_name = e.unlock_name
        e.unlock_name = names.get(api_name) or api_name
        e.unlock_description = details.get(api_name) or ""
        r = rarity.get(api_name)
        e.unlock_rarity = float(r) if r is not None else None
        e.unlock_icon = icons.get(api_name) or ""


def name_unlocks(client, events: List[GameEvent], store: GameTrackerStore) -> None:
    """Display names and rarity for unlocks found by a library sweep."""
    by_game: Dict[str, List[GameEvent]] = {}
    for e in events:
        if e.kind == UNLOCK and e.platform == STEAM:
            by_game.setdefault(e.game_id, []).append(e)
    for appid, game_events in by_game.items():
        game = store.get_game(STEAM, appid)
        if game is None:
            continue
        try:
            rarity = client.global_percents(int(appid)) if client is not None else {}
        except Exception:
            rarity = {}
        _fill_names(game_events, game, rarity, {})


def cover_items(games: Iterable[TrackedGame]) -> List[Tuple[str, str, str]]:
    return [(STEAM, g.game_id, g.cover_url) for g in games if g.cover_url]


covers.set_fallback(STEAM, store_header_url)

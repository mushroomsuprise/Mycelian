# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""PlayStation library sync for the Games Tracker.

``sync_library`` reads every trophy list on the account (one paged request), keeps the
tracker store and the PSN game cache in step, and returns the changes since the last
read. ``observe_title`` does the same for the one game the presence loop just refreshed.
Both run on the tracker's PSN worker thread, never on the presence loop or the UI.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .deltas import diff
from .models import GRADES, PSN, UNLOCK, GameEvent, TrackedGame, TrackerSettings, grade_counts
from .store import GameTrackerStore

logger = logging.getLogger(__name__)

NPWR_RE = re.compile(r"^NPWR\d{5}_\d{2}$")
PAGE_SIZE = 800
MAX_NAMED_GAMES_PER_SYNC = 10  # per-game trophy lookups to name unlocks (rate budget)
_PLATFORM_ORDER = ("PS5", "PS4", "PS3", "PSVITA", "PSPC")


def platform_label(value: Any) -> str:
    """PS5 / PS4 / ... from psnawp's ``title_platform`` (a frozenset of PlatformType), a string or a list."""
    if value is None:
        return ""
    items = value if isinstance(value, (set, frozenset, list, tuple)) else [value]
    names = set()
    for item in items:
        name = getattr(item, "value", item)
        name = str(name or "").upper().replace(" ", "")
        if name and name != "UNKNOWN":
            names.add(name)
    for preferred in _PLATFORM_ORDER:
        if preferred in names:
            return "PS Vita" if preferred == "PSVITA" else "PC" if preferred == "PSPC" else preferred
    return sorted(names)[0] if names else ""


def _trophy_set(obj: Any) -> Dict[str, int]:
    if isinstance(obj, dict):
        return grade_counts(obj)
    return {g: int(getattr(obj, g, 0) or 0) for g in GRADES}


def _ts(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_titles(client) -> Optional[List[Dict[str, Any]]]:
    """Every trophy list on the tracked account, or None when PSN can't be read right now."""
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    account_id = client.get_trophy_target_account_id()
    if not account_id:
        return None
    try:
        user = client.api.user(account_id=account_id)
        titles: Dict[str, Dict[str, Any]] = {}
        # limit=None: psnawp pages through the whole list itself (PAGE_SIZE titles per request).
        for t in user.trophy_titles(limit=None, page_size=PAGE_SIZE):
            comm_id = getattr(t, "np_communication_id", None)
            if not comm_id:
                continue
            titles[str(comm_id)] = {
                "np_communication_id": str(comm_id),
                "name": getattr(t, "title_name", None) or "",
                "platform": platform_label(getattr(t, "title_platform", None)),
                "icon_url": getattr(t, "title_icon_url", None) or "",
                "earned": _trophy_set(getattr(t, "earned_trophies", None)),
                "defined": _trophy_set(getattr(t, "defined_trophies", None)),
                "progress": getattr(t, "progress", None),
                "last_updated": _ts(getattr(t, "last_updated_datetime", None)),
                "has_trophy_groups": bool(getattr(t, "has_trophy_groups", False)),
            }
        return list(titles.values())
    except Exception as e:
        logger.warning("Games Tracker: couldn't read the PlayStation trophy list: %s", e)
        return None


def title_to_game(title: Dict[str, Any]) -> TrackedGame:
    earned = grade_counts(title.get("earned") or title.get("earned_trophies"))
    defined = grade_counts(title.get("defined") or title.get("defined_trophies"))
    progress = title.get("progress")
    if progress is None:
        total = sum(defined.values())
        progress = round(sum(earned.values()) * 100 / total) if total else 0
    return TrackedGame(
        platform=PSN,
        game_id=str(title.get("np_communication_id") or ""),
        title=str(title.get("name") or title.get("title_name") or ""),
        platform_label=platform_label(title.get("platform")),
        cover_url=str(title.get("icon_url") or ""),
        earned=earned,
        defined=defined,
        progress=int(progress or 0),
        last_played=_ts(title.get("last_updated")),
        extra={"has_trophy_groups": bool(title.get("has_trophy_groups"))},
    )


def merge(old: Optional[TrackedGame], new: TrackedGame, cache_doc: Optional[dict] = None) -> TrackedGame:
    """The fresh read plus what the user and earlier reads know (titles, hidden, IDs, first seen)."""
    now = time.time()
    if old is not None:
        new.title_override = old.title_override
        new.hidden = old.hidden
        new.first_seen = old.first_seen or now
        new.np_title_id = new.np_title_id or old.np_title_id
        new.cover_url = new.cover_url or old.cover_url
        new.platform_label = new.platform_label or old.platform_label
        new.title = new.title or old.title
        new.last_played = max(new.last_played, old.last_played)
        new.extra = {**old.extra, **new.extra}
    else:
        new.first_seen = now
    if cache_doc:
        new.np_title_id = str(cache_doc.get("np_title_id") or new.np_title_id or "")
        presence = str(cache_doc.get("presence_name") or "").strip()
        if presence:
            new.extra["presence_name"] = presence
        if cache_doc.get("trophy_groups"):
            new.extra["trophy_groups"] = cache_doc.get("trophy_groups")
    new.baselined = True
    return new


def _changed(old: Optional[TrackedGame], new: TrackedGame) -> bool:
    if old is None:
        return True
    a, b = old.to_dict(), new.to_dict()
    for k in ("updated_at",):
        a.pop(k, None)
        b.pop(k, None)
    return a != b


def _cache_docs() -> Dict[str, dict]:
    try:
        from ..psnapi import load_all_psn_game_cache_docs_from_db

        return {str(d.get("np_communication_id")): d for d in load_all_psn_game_cache_docs_from_db() if d.get("np_communication_id")}
    except Exception as e:
        logger.debug("Games Tracker: PSN game cache unavailable: %s", e)
        return {}


def _ensure_cache_doc(game: TrackedGame, docs: Dict[str, dict]) -> None:
    """Every played title gets a PSN game cache entry (so presence can match it); existing ones keep their edits."""
    from ..psnapi import store_psn_game_cache_in_db, update_psn_game_cache_in_db

    doc = docs.get(game.game_id)
    if doc is None:
        doc = {
            "np_communication_id": game.game_id,
            "np_title_id": game.np_title_id or None,
            "presence_name": game.title,
            "trophy_name": game.title,
            "cover_art_url": game.cover_url or None,
            "platform": game.platform_label or None,
            "has_trophy_groups": bool(game.extra.get("has_trophy_groups")),
            "trophy_groups": [],
        }
        if store_psn_game_cache_in_db(doc):
            docs[game.game_id] = doc
        return
    fill = {}
    if not doc.get("trophy_name") and game.title:
        fill["trophy_name"] = game.title
    if not doc.get("cover_art_url") and game.cover_url:
        fill["cover_art_url"] = game.cover_url
    if fill:
        update_psn_game_cache_in_db(game.game_id, fill)
        doc.update(fill)


def _baseline(store: GameTrackerStore, account: str) -> bool:
    """True when the silent first import for this account is done; another account wipes the old one's games."""
    base = store.baseline(PSN)
    if base is None:
        return False
    if base.get("account") != account:
        logger.info("Games Tracker: PlayStation account changed; starting a new silent import")
        store.clear_platform(PSN)
        store.delete_meta(f"baseline:{PSN}")
        return False
    return True


def sync_library(
    client,
    store: GameTrackerStore,
    settings: TrackerSettings,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Optional[List[GameEvent]]:
    """Read the whole library. Returns the changes (empty on the first import), or None when PSN was unavailable."""
    titles = fetch_titles(client)
    if titles is None:
        return None
    account = str(client.get_trophy_target_account_id() or "")
    baseline_done = _baseline(store, account)
    stored = store.games_by_id(PSN)
    docs = _cache_docs()
    events: List[GameEvent] = []
    changed: List[TrackedGame] = []
    total = len(titles)
    for i, title in enumerate(titles, 1):
        new = title_to_game(title)
        if not new.game_id:
            continue
        old = stored.get(new.game_id)
        _ensure_cache_doc(new, docs)
        new = merge(old, new, docs.get(new.game_id))
        events.extend(diff(old, new, baseline_done=baseline_done, milestones=settings.milestones))
        if _changed(old, new):
            changed.append(new)
        if len(changed) >= 50:
            store.upsert_games(changed)
            changed = []
        if progress and (i % 25 == 0 or i == total):
            progress(i, total)
    store.upsert_games(changed)
    if not baseline_done and account:
        store.set_baseline(PSN, account)
    name_unlocks(client, events)
    return events


def observe_title(client, store: GameTrackerStore, settings: TrackerSettings, title_result: Dict[str, Any]) -> List[GameEvent]:
    """One game the presence loop just refreshed (``PSNClient.get_title_trophy_progress`` result)."""
    comm_id = str((title_result or {}).get("np_communication_id") or "")
    if not comm_id:
        return []
    account = str(client.get_trophy_target_account_id() or "") if client is not None else ""
    base = store.baseline(PSN)
    if base is None or base.get("account") != account:
        return []  # the library sync owns the first import
    new = title_to_game(title_result)
    new.np_title_id = str(title_result.get("np_title_id") or "")
    new.last_played = time.time()
    old = store.get_game(PSN, comm_id)
    docs = _cache_docs() if old is None else {}
    if old is None:
        _ensure_cache_doc(new, docs)
    else:
        try:
            from ..psnapi import get_psn_game_cache_by_comm_id

            doc = get_psn_game_cache_by_comm_id(comm_id)
            if doc:
                docs[comm_id] = doc
        except Exception:
            pass
    new = merge(old, new, docs.get(comm_id))
    events = diff(old, new, baseline_done=True, milestones=settings.milestones)
    if _changed(old, new):
        store.upsert_games([new])
    name_unlocks(client, events)
    return events


def name_unlocks(client, events: List[GameEvent]) -> None:
    """Fill in the trophy name, description, rarity and icon of unlock events (the newest earned of each grade)."""
    by_game: Dict[str, List[GameEvent]] = {}
    for e in events:
        if e.kind == UNLOCK and e.platform == PSN and not e.unlock_name:
            by_game.setdefault(e.game_id, []).append(e)
    if not by_game or client is None or not getattr(client, "api", None):
        return
    for comm_id in list(by_game)[:MAX_NAMED_GAMES_PER_SYNC]:
        trophies = _earned_trophies(client, comm_id, by_game[comm_id][0].platform_label)
        if not trophies:
            continue
        for grade in GRADES:
            wanted = [e for e in by_game[comm_id] if e.unlock_grade == grade]
            pool = [t for t in trophies if t["grade"] == grade]
            # Newest unlocks last, matching the oldest-first event order.
            for event, trophy in zip(wanted, list(reversed(pool[: len(wanted)]))):
                event.unlock_name = trophy["name"]
                event.unlock_description = trophy["description"]
                event.unlock_rarity = trophy["rarity"]
                event.unlock_icon = trophy["icon"]


def _earned_trophies(client, comm_id: str, label: str) -> List[Dict[str, Any]]:
    """Earned trophies of one list, newest first."""
    try:
        from ..psnapi import presence_format_to_platform_type

        account_id = client.get_trophy_target_account_id()
        if not account_id:
            return []
        user = client.api.user(account_id=account_id)
        platform = presence_format_to_platform_type((label or "PS5").replace(" ", "").replace("PC", "PSPC"))
        out = []
        for t in user.trophies(np_communication_id=comm_id, platform=platform, include_progress=True, trophy_group_id="all"):
            if not getattr(t, "earned", False):
                continue
            ttype = getattr(t, "trophy_type", None)
            grade = str(getattr(ttype, "value", ttype) or "").lower()
            rate = getattr(t, "trophy_earn_rate", None)
            out.append({
                "name": getattr(t, "trophy_name", None) or "",
                "description": getattr(t, "trophy_detail", None) or "",
                "icon": getattr(t, "trophy_icon_url", None) or "",
                "grade": grade,
                "rarity": float(rate) if rate is not None else None,
                "earned_at": _ts(getattr(t, "earned_date_time", None)),
            })
        out.sort(key=lambda t: t["earned_at"], reverse=True)
        return out
    except Exception as e:
        logger.debug("Games Tracker: couldn't name trophies for %s: %s", comm_id, e)
        return []


# ----- edits from the tab / API -----


def apply_edit(
    store: GameTrackerStore,
    game_id: str,
    *,
    title: Optional[str] = None,
    np_title_id: Optional[str] = None,
    np_communication_id: Optional[str] = None,
    hidden: Optional[bool] = None,
) -> Tuple[Optional[TrackedGame], str]:
    """Save drawer edits to the tracker and the PSN game cache. Returns (game, error message)."""
    from ..psnapi import (
        delete_psn_game_cache_in_db,
        get_psn_game_cache_by_comm_id,
        store_psn_game_cache_in_db,
        update_psn_game_cache_in_db,
    )

    game = store.get_game(PSN, game_id)
    if game is None:
        return None, "That game isn't in the tracker."
    if np_communication_id is not None:
        new_id = np_communication_id.strip().upper()
        if not NPWR_RE.match(new_id):
            return None, "An NPWR ID looks like NPWR12345_00."
        if new_id != game.game_id:
            if store.get_game(PSN, new_id) is not None:
                return None, f"{new_id} is already tracked as another game."
            doc = get_psn_game_cache_by_comm_id(game.game_id) or {}
            moved = {**doc, "np_communication_id": new_id}
            if store_psn_game_cache_in_db(moved) and doc:
                delete_psn_game_cache_in_db(game.game_id)
            game = store.rekey_game(PSN, game.game_id, new_id) or game
    cache_updates: Dict[str, Any] = {}
    if title is not None:
        game.title_override = title.strip()
        cache_updates["presence_name"] = game.title_override or game.extra.get("presence_name") or game.title
        if game.title_override:
            game.extra["presence_name"] = game.title_override
    if np_title_id is not None:
        game.np_title_id = np_title_id.strip().upper()
        cache_updates["np_title_id"] = game.np_title_id or None
    if hidden is not None:
        game.hidden = bool(hidden)
    if cache_updates and get_psn_game_cache_by_comm_id(game.game_id):
        update_psn_game_cache_in_db(game.game_id, cache_updates)
    store.upsert_games([game])
    return game, ""


def resolve_npwr(client, np_title_id: str) -> Optional[str]:
    """NPWR ID for an NP Title ID (one PSN request)."""
    if client is None or not np_title_id:
        return None
    try:
        return client.resolve_np_communication_id_from_np_title_id(np_title_id.strip().upper())
    except Exception as e:
        logger.debug("Games Tracker: NPWR lookup failed for %s: %s", np_title_id, e)
        return None


GROUPS_TTL_SEC = 600
_groups_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def group_progress(client, comm_id: str, label: str) -> Optional[List[Dict[str, Any]]]:
    """Per-group progress (base game and DLC) for the drawer: one request, cached ten minutes."""
    cached = _groups_cache.get(comm_id)
    if cached and time.time() - cached[0] < GROUPS_TTL_SEC:
        return cached[1]
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    try:
        from ..psnapi import presence_format_to_platform_type

        account_id = client.get_trophy_target_account_id()
        if not account_id:
            return None
        platform = presence_format_to_platform_type((label or "PS5").replace(" ", "").replace("PC", "PSPC"))
        summary = client.api.user(account_id=account_id).trophy_groups_summary(
            np_communication_id=comm_id, platform=platform, include_progress=True
        )
        groups = []
        for g in getattr(summary, "trophy_groups", None) or []:
            earned = sum(_trophy_set(getattr(g, "earned_trophies", None)).values())
            defined = sum(_trophy_set(getattr(g, "defined_trophies", None)).values())
            groups.append({
                "id": getattr(g, "trophy_group_id", "") or "",
                "name": getattr(g, "trophy_group_name", None) or ("Base game" if getattr(g, "trophy_group_id", "") == "default" else "DLC"),
                "earned": earned,
                "defined": defined,
                "progress": int(getattr(g, "progress", None) or (round(earned * 100 / defined) if defined else 0)),
            })
        _groups_cache[comm_id] = (time.time(), groups)
        return groups
    except Exception as e:
        logger.debug("Games Tracker: trophy groups unavailable for %s: %s", comm_id, e)
        return None


def remove_game(store: GameTrackerStore, game_id: str, *, from_cache: bool = True) -> bool:
    """Forget a game (it comes back on the next sync if it's still on the account)."""
    removed = store.delete_game(PSN, game_id)
    if from_cache:
        try:
            from ..psnapi import delete_psn_game_cache_in_db

            delete_psn_game_cache_in_db(game_id)
        except Exception as e:
            logger.debug("Games Tracker: cache delete failed for %s: %s", game_id, e)
    return removed


def cover_items(games: Iterable[TrackedGame]) -> List[Tuple[str, str, str]]:
    return [(PSN, g.game_id, g.cover_url) for g in games if g.cover_url]

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""PlayStation library sync for the Games Tracker.

``sync_library`` reads the account's trophy lists, keeps the tracker store and the PSN game
cache in step, and returns the changes since the last read. PSN lists titles most recently
updated first, so a routine sync reads one small page and stops after a run of unchanged
titles (usually one request); the whole list is read on the first import, on **Sync now**
and every ``FULL_SYNC_SEC``. ``observe_title`` does the same for the one game the presence
loop just refreshed. Both run on the tracker's PSN worker thread, never on the presence
loop or the UI.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import catalog
from .deltas import diff
from .models import GRADES, PSN, UNLOCK, GameEvent, TrackedGame, TrackerSettings, game_changed, grade_counts
from .store import GameTrackerStore

logger = logging.getLogger(__name__)

NPWR_RE = re.compile(r"^NPWR\d{5}_\d{2}$")
PAGE_SIZE = 800  # full listing
INCREMENTAL_PAGE_SIZE = 50  # routine sync: newest first, one page is usually enough
UNCHANGED_RUN_STOP = 5  # stop reading after this many unchanged titles in a row
FULL_SYNC_SEC = 6 * 3600.0
SYNC_META_KEY = "psn_sync"  # store meta: {"full_at": epoch of the last full listing}
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


def fetch_titles(
    client,
    *,
    page_size: int = PAGE_SIZE,
    stop: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """The tracked account's trophy lists, newest first, or None when PSN can't be read right now.

    Every list by default; with ``stop`` reading ends (no further page requested) as soon as it
    returns True for a title."""
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    account_id = client.get_trophy_target_account_id()
    if not account_id:
        return None
    try:
        user = client.user_for(account_id)
        titles: Dict[str, Dict[str, Any]] = {}
        # limit=None: psnawp pages through the list itself, ``page_size`` titles per request,
        # and only asks for the next page when it's needed.
        for t in user.trophy_titles(limit=None, page_size=page_size):
            comm_id = getattr(t, "np_communication_id", None)
            if not comm_id:
                continue
            titles[str(comm_id)] = entry = {
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
            if stop is not None and stop(entry):
                break
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
    return game_changed(old, new)


def _cache_docs() -> Dict[str, dict]:
    try:
        from ..psnapi import cached_game_docs_by_comm_id

        return cached_game_docs_by_comm_id()
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


def _unchanged(old: Optional[TrackedGame], title: Dict[str, Any]) -> bool:
    """True when a listed title matches what the store already has (nothing earned, nothing added)."""
    if old is None or not old.baselined:
        return False
    progress = title.get("progress")
    return (
        grade_counts(title.get("earned")) == grade_counts(old.earned)
        and grade_counts(title.get("defined")) == grade_counts(old.defined)
        and (progress is None or int(progress) == old.progress)
    )


def sync_library(
    client,
    store: GameTrackerStore,
    settings: TrackerSettings,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
    full: bool = False,
) -> Optional[List[GameEvent]]:
    """Read the library (the recently updated part, or all of it with ``full`` / when a full read is due).
    Returns the changes (empty on the first import), or None when PSN was unavailable."""
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    account = str(client.get_trophy_target_account_id() or "")
    if not account:
        return None
    baseline_done = _baseline(store, account)
    stored = store.games_by_id(PSN)
    meta = store.get_meta(SYNC_META_KEY, {}) or {}
    full = full or not baseline_done or time.time() - float(meta.get("full_at") or 0) >= FULL_SYNC_SEC
    if full:
        titles = fetch_titles(client)
    else:
        run = [0]

        def stop(title: Dict[str, Any]) -> bool:
            run[0] = run[0] + 1 if _unchanged(stored.get(title["np_communication_id"]), title) else 0
            return run[0] >= UNCHANGED_RUN_STOP

        titles = fetch_titles(client, page_size=INCREMENTAL_PAGE_SIZE, stop=stop)
    if titles is None:
        return None
    docs = _cache_docs()
    events: List[GameEvent] = []
    changed: List[TrackedGame] = []
    total = len(titles)
    for i, title in enumerate(titles, 1):
        comm_id = str(title.get("np_communication_id") or "")
        old = stored.get(comm_id)
        # Nothing new on a title the store and the PSN cache already have: skip the merge.
        skip = (
            old is not None
            and comm_id in docs
            and _unchanged(old, title)
            and float(title.get("last_updated") or 0) <= old.last_played
        )
        if comm_id and not skip:
            new = title_to_game(title)
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
    if full:
        store.set_meta(SYNC_META_KEY, {**meta, "full_at": time.time()})
    name_unlocks(client, events, store)
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
    docs = _cache_docs()  # the in-memory index: no DB read per observe
    if old is None:
        _ensure_cache_doc(new, docs)
    new = merge(old, new, docs.get(comm_id))
    events = diff(old, new, baseline_done=True, milestones=settings.milestones)
    # Nothing earned: the row (and everyone listening) is left alone; last_played catches up every few minutes.
    if game_changed(old, new, ignore_last_played=True):
        store.upsert_games([new])
    name_unlocks(client, events, store)
    return events


def name_unlocks(client, events: List[GameEvent], store: Optional[GameTrackerStore] = None) -> None:
    """Fill in the trophy name, description, rarity and icon of unlock events (the newest earned of each grade)."""
    by_game: Dict[str, List[GameEvent]] = {}
    for e in events:
        if e.kind == UNLOCK and e.platform == PSN and not e.unlock_name:
            by_game.setdefault(e.game_id, []).append(e)
    if not by_game or client is None or not getattr(client, "api", None):
        return
    for comm_id in list(by_game)[:MAX_NAMED_GAMES_PER_SYNC]:
        game = store.get_game(PSN, comm_id) if store is not None else None
        trophies = _earned_trophies(client, comm_id, by_game[comm_id][0].platform_label,
                                    defined=game.defined if game else None, store=store)
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


def _earned_trophies(client, comm_id: str, label: str, *, defined: Any = None,
                     store: Optional[GameTrackerStore] = None) -> List[Dict[str, Any]]:
    """Earned trophies of one list, newest first."""
    out = trophy_list(client, comm_id, label, earned_only=True, defined=defined, store=store) or []
    out.sort(key=lambda t: t["earned_at"], reverse=True)
    return out


def trophy_list(client, comm_id: str, label: str, *, earned_only: bool = False, defined: Any = None,
                store: Optional[GameTrackerStore] = None) -> Optional[List[Dict[str, Any]]]:
    """Every trophy of one list in game order (``id``, ``name``, ``description``, ``icon``, ``grade``, ``rarity``,
    ``earned``, ``earned_at``); only the earned ones with ``earned_only``. None when PSN can't be reached.

    One request per 200 trophies: the account's earned half. Names, details and icons come from
    the catalog (read from PSN once per list, again when ``defined`` shows new trophies)."""
    if client is None or not getattr(client, "api", None):
        return None
    earned = client.get_earned_trophies(comm_id, label)
    if earned is None:
        return None
    defs = catalog.psn_definitions(client, comm_id, label, defined, store=store)
    if defs is None:
        return None
    known = {d["id"] for d in defs}
    if any(tid not in known for tid in earned):
        # Trophies the cached list doesn't have (a DLC landed): read the definitions again.
        try:
            (store or catalog._store()).catalog_delete(catalog.PSN_DEFS, comm_id)
        except Exception:
            pass
        defs = catalog.psn_definitions(client, comm_id, label, None, store=store) or defs
    out = []
    for d in defs:
        e = earned.get(d["id"]) or {}
        is_earned = bool(e.get("earned"))
        if earned_only and not is_earned:
            continue
        out.append({
            "id": d["id"],
            "name": d.get("name") or "",
            "description": d.get("description") or "",
            "icon": d.get("icon") or "",
            "grade": d.get("grade") or "",
            "rarity": e.get("rarity"),
            "earned": is_earned,
            "earned_at": float(e.get("earned_at") or 0.0),
        })
    return out


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


def group_progress(client, comm_id: str, label: str, *, defined: Any = None,
                   store: Optional[GameTrackerStore] = None) -> Optional[List[Dict[str, Any]]]:
    """Per-group progress (base game and DLC) for the drawer: one request (the account's earned
    counts; group names and totals come from the catalog), cached ten minutes."""
    cached = _groups_cache.get(comm_id)
    if cached and time.time() - cached[0] < GROUPS_TTL_SEC:
        return cached[1]
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    earned = client.get_earned_trophy_groups(comm_id, label)
    if earned is None:
        return None
    defs = catalog.psn_groups(client, comm_id, label, defined, store=store)
    if defs is None:
        return None
    groups = []
    for g in defs:
        e = earned.get(g["id"]) or {}
        got = sum((e.get("earned") or {}).values())
        total = sum((g.get("defined") or {}).values())
        groups.append({
            "id": g["id"],
            "name": g.get("name") or ("Base game" if g["id"] == "default" else "DLC"),
            "earned": got,
            "defined": total,
            "progress": int(e.get("progress") or (round(got * 100 / total) if total else 0)),
        })
    _groups_cache[comm_id] = (time.time(), groups)
    return groups


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

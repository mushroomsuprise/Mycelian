# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Game data that rarely changes, kept in ``game_tracker.db`` so PSN / Steam are asked once.

* PSN trophy definitions (name, detail, icon, grade, group, hidden) per NPWR ID. Re-read when
  the game's defined trophy counts no longer match (a DLC added trophies) or after 90 days.
* PSN trophy group definitions (name, icon, defined counts), same rule.
* Steam achievement schemas per app ID: 30 days, or sooner when the player's list has a
  different number of achievements.
* Steam global achievement percentages: 7 days.

What the account has earned is never cached here: that's the one request left per read
(``PSNClient.get_earned_trophies`` / ``get_earned_trophy_groups``, GetPlayerAchievements).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .models import GRADES, grade_counts

logger = logging.getLogger(__name__)

PSN_DEFS = "psn_defs"
PSN_GROUPS = "psn_groups"
STEAM_SCHEMA = "steam_schema"
STEAM_PERCENTS = "steam_percents"

DAY = 86400.0
PSN_DEFS_TTL_SEC = 90 * DAY
STEAM_SCHEMA_TTL_SEC = 30 * DAY
STEAM_PERCENTS_TTL_SEC = 7 * DAY


def _store(store=None):
    if store is not None:
        return store
    from .store import get_store

    return get_store()


def _get(store, kind: str, key: Any) -> Optional[tuple]:
    try:
        return _store(store).catalog_get(kind, str(key))
    except Exception:
        logger.debug("Games Tracker catalog read failed (%s %s)", kind, key, exc_info=True)
        return None


def _put(store, kind: str, key: Any, data: Any) -> None:
    try:
        _store(store).catalog_put(kind, str(key), data)
    except Exception:
        logger.debug("Games Tracker catalog write failed (%s %s)", kind, key, exc_info=True)


def _sig(counts: Any) -> Dict[str, int]:
    """Grade counts from a dict or an object with bronze / silver / gold / platinum attributes."""
    if counts is None or isinstance(counts, dict):
        return grade_counts(counts or {})
    return {g: int(getattr(counts, g, 0) or 0) for g in GRADES}


def _matches(cached_sig: Any, defined: Any) -> bool:
    """True when the cached definitions still fit the game's defined counts (unknown counts fit)."""
    if defined is None:
        return True
    want = _sig(defined)
    if not any(want.values()):
        return True
    return _sig(cached_sig) == want


# ----- PSN -----


def _psn_user(client):
    if client is None or not getattr(client, "api", None) or not client.is_connected():
        return None
    account_id = client.get_trophy_target_account_id()
    return client.user_for(account_id) if account_id else None


def _fetch_psn_definitions(client, comm_id: str, label: str) -> Optional[List[Dict[str, Any]]]:
    from ..psnapi import label_to_platform_type

    try:
        user = _psn_user(client)
        if user is None:
            return None
        out = []
        for t in user.trophies(np_communication_id=comm_id, platform=label_to_platform_type(label),
                               include_progress=False, trophy_group_id="all"):
            ttype = getattr(t, "trophy_type", None)
            tid = getattr(t, "trophy_id", None)
            out.append({
                "id": str(tid if tid is not None else ""),
                "group": str(getattr(t, "trophy_group_id", None) or "default"),
                "grade": str(getattr(ttype, "value", ttype) or "").lower(),
                "name": getattr(t, "trophy_name", None) or "",
                "description": getattr(t, "trophy_detail", None) or "",
                "icon": getattr(t, "trophy_icon_url", None) or "",
                "hidden": bool(getattr(t, "trophy_hidden", False)),
            })
        return out
    except Exception as e:
        logger.debug("Games Tracker: trophy definitions unavailable for %s: %s", comm_id, e)
        return None


def psn_definitions(client, comm_id: str, label: str, defined: Any = None, *, store=None) -> Optional[List[Dict[str, Any]]]:
    """Every trophy of a list in game order (``id``, ``group``, ``grade``, ``name``, ``description``,
    ``icon``, ``hidden``). From the catalog when it still fits ``defined``; else one read from PSN.
    A stale entry is returned when PSN can't be read; None when there is nothing at all."""
    entry = _get(store, PSN_DEFS, comm_id)
    if entry is not None:
        data, at = entry
        if time.time() - at < PSN_DEFS_TTL_SEC and _matches(data.get("sig"), defined):
            return list(data.get("trophies") or [])
    fetched = _fetch_psn_definitions(client, comm_id, label)
    if not fetched:
        return list(entry[0].get("trophies") or []) if entry is not None else None
    sig = {g: sum(1 for t in fetched if t["grade"] == g) for g in GRADES}
    _put(store, PSN_DEFS, comm_id, {"sig": sig, "trophies": fetched})
    return fetched


def psn_groups(client, comm_id: str, label: str, defined: Any = None, *, store=None) -> Optional[List[Dict[str, Any]]]:
    """A list's trophy groups in order (``id``, ``name``, ``icon``, ``defined`` grade counts), cached like
    the definitions. One request when PSN has to be asked."""
    from ..psnapi import label_to_platform_type

    entry = _get(store, PSN_GROUPS, comm_id)
    if entry is not None:
        data, at = entry
        if time.time() - at < PSN_DEFS_TTL_SEC and _matches(data.get("sig"), defined):
            return list(data.get("groups") or [])
    try:
        user = _psn_user(client)
        summary = user.trophy_groups_summary(
            np_communication_id=comm_id, platform=label_to_platform_type(label), include_progress=False
        ) if user is not None else None
    except Exception as e:
        logger.debug("Games Tracker: trophy groups unavailable for %s: %s", comm_id, e)
        summary = None
    if summary is None:
        return list(entry[0].get("groups") or []) if entry is not None else None
    groups = []
    for g in getattr(summary, "trophy_groups", None) or []:
        gid = getattr(g, "trophy_group_id", "") or ""
        groups.append({
            "id": gid,
            "name": getattr(g, "trophy_group_name", None) or ("Base game" if gid == "default" else "DLC"),
            "icon": getattr(g, "trophy_group_icon_url", None) or "",
            "defined": _sig(getattr(g, "defined_trophies", None)),
        })
    sig = {k: sum(g["defined"][k] for g in groups) for k in GRADES}
    _put(store, PSN_GROUPS, comm_id, {"sig": sig, "groups": groups})
    return groups


# ----- Steam -----


def steam_schema(appid: Any, *, store=None) -> Optional[tuple]:
    """(schema by API name, fetched_at) or None; the caller decides whether it's fresh enough."""
    entry = _get(store, STEAM_SCHEMA, appid)
    if entry is None:
        return None
    data, at = entry
    return (data if isinstance(data, dict) else {}), at


def save_steam_schema(appid: Any, schema: Dict[str, Any], *, store=None) -> None:
    _put(store, STEAM_SCHEMA, appid, schema)


def steam_percents(appid: Any, *, store=None) -> Optional[tuple]:
    entry = _get(store, STEAM_PERCENTS, appid)
    if entry is None:
        return None
    data, at = entry
    return (data if isinstance(data, dict) else {}), at


def save_steam_percents(appid: Any, percents: Dict[str, float], *, store=None) -> None:
    _put(store, STEAM_PERCENTS, appid, percents)

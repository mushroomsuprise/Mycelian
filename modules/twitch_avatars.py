#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch profile pictures for viewer names (Activity Feed "By person" cards).

Lookups never block the caller: :func:`cached_avatars` returns what is already known, and
:func:`request_avatars` fetches the rest in the background (up to 100 logins per Helix
call) and calls ``on_update`` once new pictures arrived.

Results are saved to ``data/twitch_avatars.json`` so restarts don't look everyone up
again. A picture is refreshed after a few days (viewers change them); names Twitch
doesn't know are asked about again after a few hours.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

HELIX_USERS = "https://api.twitch.tv/helix/users"
BATCH_SIZE = 100  # Helix /users accepts up to 100 logins per call
FOUND_TTL_SEC = 3 * 24 * 3600
MISSING_TTL_SEC = 6 * 3600
MAX_SAVED = 5000  # most recently fetched viewers kept on disk
CACHE_FILE = os.path.join("data", "twitch_avatars.json")
_CALL_TIMEOUT_SEC = 15.0

# Twitch logins: 1-25 letters, digits or underscores. Display names that differ from the
# login (spaces, other scripts) can't be looked up by name and keep their initial.
_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")

_lock = threading.Lock()
_cache: Dict[str, Tuple[str, float]] = {}  # login -> (picture URL or "", fetched at)
_pending: set = set()
_loaded = False


def _cache_path() -> str:
    from .path_utils import get_data_path

    return get_data_path(CACHE_FILE)


def _load_locked() -> None:
    """Read the saved cache once (caller holds the lock)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.debug("Couldn't read the Twitch profile picture cache: %s", exc)
        return
    if not isinstance(raw, dict):
        return
    for login, entry in raw.items():
        try:
            url, fetched = str(entry[0] or ""), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if login_for(login) == login and (not url or url.startswith("https://")):
            _cache[login] = (url, fetched)


def _save() -> None:
    with _lock:
        newest = sorted(_cache.items(), key=lambda kv: kv[1][1], reverse=True)[:MAX_SAVED]
        payload = {login: [url, fetched] for login, (url, fetched) in newest}
    path = _cache_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as exc:
        logger.debug("Couldn't save the Twitch profile picture cache: %s", exc)


def login_for(name: str) -> Optional[str]:
    """The Twitch login a display name stands for, or None when it can't be one."""
    login = str(name or "").strip().lower()
    return login if _LOGIN_RE.match(login) else None


def _fresh(entry: Tuple[str, float], now: float) -> bool:
    url, fetched = entry
    return now - fetched < (FOUND_TTL_SEC if url else MISSING_TTL_SEC)


def cached_avatars(names: Iterable[str]) -> Dict[str, str]:
    """{name: picture URL} for the names whose picture is already known."""
    now = time.time()
    out: Dict[str, str] = {}
    with _lock:
        _load_locked()
        for name in names:
            login = login_for(name)
            entry = _cache.get(login) if login else None
            if entry and entry[0] and _fresh(entry, now):
                out[name] = entry[0]
    return out


def request_avatars(names: Iterable[str], on_update: Optional[Callable[[], None]] = None) -> bool:
    """Fetch pictures not known yet in a background thread. True when a fetch started."""
    now = time.time()
    with _lock:
        _load_locked()
        wanted: List[str] = []
        for name in names:
            login = login_for(name)
            if not login or login in _pending or login in wanted:
                continue
            entry = _cache.get(login)
            if entry and _fresh(entry, now):
                continue
            wanted.append(login)
        if not wanted:
            return False
        _pending.update(wanted)
    threading.Thread(
        target=_fetch_and_store, args=(wanted, on_update), name="TwitchAvatars", daemon=True
    ).start()
    return True


def _fetch_and_store(logins: List[str], on_update: Optional[Callable[[], None]]) -> None:
    found_any = stored = False
    try:
        for start in range(0, len(logins), BATCH_SIZE):
            batch = logins[start:start + BATCH_SIZE]
            pictures = _fetch_batch(batch)
            if pictures is None:
                break  # Twitch unavailable: ask again next time
            now = time.time()
            with _lock:
                for login in batch:
                    url = pictures.get(login, "")
                    _cache[login] = (url, now)
                    found_any = found_any or bool(url)
            stored = True
    except Exception as exc:
        logger.debug("Twitch profile picture lookup failed: %s", exc)
    finally:
        with _lock:
            _pending.difference_update(logins)
    if stored:
        _save()
    if found_any and on_update is not None:
        try:
            on_update()
        except Exception as exc:
            logger.debug("Twitch profile picture update callback failed: %s", exc)


def _fetch_batch(logins: List[str]) -> Optional[Dict[str, str]]:
    """{login: https picture URL} from Helix, or None when Twitch isn't reachable."""
    from . import twitch

    api = twitch.get_twitch_api()
    if api is None or not getattr(api, "is_connected", False):
        return None
    url = f"{HELIX_USERS}?{urlencode([('login', login) for login in logins])}"
    try:
        data = asyncio.run(asyncio.wait_for(api.generic_api_call(url, "GET"), _CALL_TIMEOUT_SEC))
    except Exception as exc:
        logger.debug("Twitch /users lookup failed: %s", exc)
        return None
    pictures: Dict[str, str] = {}
    for row in (data or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        login = str(row.get("login") or "").lower()
        picture = str(row.get("profile_image_url") or "")
        if login and picture.startswith("https://"):
            pictures[login] = picture
    return pictures


def _reset_for_tests() -> None:
    global _loaded
    with _lock:
        _cache.clear()
        _pending.clear()
        _loaded = False

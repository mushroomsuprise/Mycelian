"""Cached Twitch channel moderators list for connector conditions."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional, Set

logger = logging.getLogger(__name__)

_MODERATOR_CACHE_TTL_SECONDS = 1800
_MODERATOR_CACHE_FAILURE_TTL_SECONDS = 120
_scope_warned = False


class ModeratorCache:
    """In-memory cache of moderator user IDs for the connected broadcaster."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._moderator_ids: Set[str] = set()
        self._last_fetch: float = 0
        self._last_fetch_success: bool = False
        self._ttl = _MODERATOR_CACHE_TTL_SECONDS
        self._failure_ttl = _MODERATOR_CACHE_FAILURE_TTL_SECONDS
        self._refresh_lock = asyncio.Lock()
        self._refresh_inflight: Optional[asyncio.Task[bool]] = None

    def _effective_ttl(self) -> float:
        if self._last_fetch_success:
            return self._ttl
        return self._failure_ttl

    def _is_stale(self) -> bool:
        if self._last_fetch == 0:
            return True
        return (time.time() - self._last_fetch) > self._effective_ttl()

    async def ensure_fresh(self, force: bool = False) -> bool:
        """Refresh the cache when stale; coalesce concurrent callers into one fetch."""
        if not force and not self._is_stale():
            return True

        async with self._refresh_lock:
            if not force and not self._is_stale():
                return True

            if self._refresh_inflight is not None:
                try:
                    return await asyncio.shield(self._refresh_inflight)
                except Exception:
                    return False

            self._refresh_inflight = asyncio.create_task(self._fetch_moderators())
            try:
                return await self._refresh_inflight
            finally:
                self._refresh_inflight = None

    async def refresh(self, force: bool = False) -> bool:
        """Fetch moderators from Helix GET /moderation/moderators."""
        return await self.ensure_fresh(force=force)

    async def _fetch_moderators(self) -> bool:
        global _scope_warned

        from . import twitch

        api = twitch.twitch_api
        if not api or not api.user_id:
            return False

        broadcaster_id = str(api.user_id)
        try:
            ids: Set[str] = set()
            cursor: Optional[str] = None
            while True:
                params: dict = {"broadcaster_id": broadcaster_id, "first": 100}
                if cursor:
                    params["after"] = cursor
                url = "https://api.twitch.tv/helix/moderation/moderators"
                response = await api.generic_api_call(url, "GET", params=params)
                if not response:
                    break
                for row in response.get("data") or []:
                    uid = row.get("user_id")
                    if uid:
                        ids.add(str(uid))
                pagination = response.get("pagination") or {}
                cursor = pagination.get("cursor")
                if not cursor:
                    break

            with self._lock:
                self._moderator_ids = ids
                self._last_fetch = time.time()
                self._last_fetch_success = True
            logger.debug("Moderator cache refreshed: %d moderators", len(ids))
            return True
        except Exception as e:
            with self._lock:
                self._last_fetch = time.time()
                self._last_fetch_success = False
            err = str(e).lower()
            if "403" in err or "401" in err or "forbidden" in err:
                if not _scope_warned:
                    _scope_warned = True
                    logger.warning(
                        "Could not fetch moderators (check channel:manage:moderators scope): %s",
                        e,
                    )
            else:
                logger.error("Error refreshing moderator cache: %s", e, exc_info=True)
            return False

    def is_cached_moderator(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return False
        with self._lock:
            return str(user_id) in self._moderator_ids

    def ensure_fresh_sync(self) -> None:
        """Best-effort refresh from sync context (e.g. chat handler)."""
        if not self._is_stale():
            return
        if self._refresh_inflight is not None:
            return
        try:
            from . import twitch

            api = twitch.twitch_api
            if not api:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.ensure_fresh())
                return
            loop.create_task(self.ensure_fresh())
        except Exception as e:
            logger.debug("Moderator cache background refresh skipped: %s", e)


def badges_indicate_moderator(badges: Any) -> bool:
    """Return True if EventSub/chat badges include moderator."""
    if not badges:
        return False
    if isinstance(badges, str):
        return "moderator/" in badges
    for badge in badges:
        set_id = None
        if hasattr(badge, "set_id"):
            set_id = badge.set_id
        elif isinstance(badge, dict):
            set_id = badge.get("set_id")
        if set_id == "moderator":
            return True
    return False


def resolve_is_moderator(
    user_id: Optional[str],
    badges: Any = None,
    broadcaster_id: Optional[str] = None,
) -> bool:
    """Whether the chatter is a moderator (badge, broadcaster, or API cache)."""
    if broadcaster_id and user_id and str(user_id) == str(broadcaster_id):
        return True
    if badges_indicate_moderator(badges):
        return True
    _cache.ensure_fresh_sync()
    return _cache.is_cached_moderator(user_id)


async def resolve_is_moderator_async(
    user_id: Optional[str],
    badges: Any = None,
    broadcaster_id: Optional[str] = None,
) -> bool:
    """Async moderator resolution with coalesced cache refresh when needed."""
    if broadcaster_id and user_id and str(user_id) == str(broadcaster_id):
        return True
    if badges_indicate_moderator(badges):
        return True
    await _cache.ensure_fresh()
    return _cache.is_cached_moderator(user_id)


_cache = ModeratorCache()


def get_moderator_cache() -> ModeratorCache:
    return _cache

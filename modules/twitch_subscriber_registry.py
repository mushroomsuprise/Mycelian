# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Persistent registry of users who have ever subscribed to the channel.

Used to filter ``channel.subscribe`` EventSub events so renewals / re-buys after
a lapse do not fire a "new sub" alert. Prefer missing a true new sub over a
false positive.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from typing import Optional, Set

logger = logging.getLogger(__name__)

_NEW_SUB_ALERT_DEDUP_SECONDS = 60.0
_HELIX_PAGE_SIZE = 100
_HELIX_PAGE_DELAY_SECONDS = 0.05


class SubscriberRegistry:
    """Ever-subscribed user registry backed by SQLite + in-memory sets."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._user_ids: Set[str] = set()
        self._user_logins: Set[str] = set()
        self._session_helix_ready = False
        self._helix_snapshot_forbidden = False
        self._recent_new_sub_alerts: dict[str, float] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._sync_lock = threading.Lock()
        self._db_path = db_path or self._default_db_path()
        self._ensure_db()
        self._load_from_db()

    @staticmethod
    def _default_db_path() -> str:
        from .path_utils import get_data_path

        return get_data_path(os.path.join("data", "twitch_subscriber_registry.db"))

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_db(self) -> None:
        try:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS known_subscribers (
                        user_id TEXT PRIMARY KEY,
                        user_login TEXT,
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL,
                        source TEXT DEFAULT '',
                        cumulative_months INTEGER
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ks_login "
                    "ON known_subscribers(user_login)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS known_subscriber_logins (
                        user_login TEXT PRIMARY KEY,
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL,
                        source TEXT DEFAULT ''
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(
                "Failed to initialize subscriber registry DB: %s", e, exc_info=True
            )

    def _load_from_db(self) -> None:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT user_id, user_login FROM known_subscribers")
                ids: Set[str] = set()
                logins: Set[str] = set()
                for user_id, user_login in cur.fetchall():
                    if user_id:
                        ids.add(str(user_id))
                    if user_login:
                        logins.add(str(user_login).lower())
                cur.execute("SELECT user_login FROM known_subscriber_logins")
                for (user_login,) in cur.fetchall():
                    if user_login:
                        logins.add(str(user_login).lower())
                with self._lock:
                    self._user_ids = ids
                    self._user_logins = logins
                logger.info(
                    "Subscriber registry loaded: %d user_ids, %d logins",
                    len(ids),
                    len(logins),
                )
            finally:
                conn.close()
        except Exception as e:
            logger.error("Failed to load subscriber registry: %s", e, exc_info=True)

    def is_session_ready(self) -> bool:
        with self._lock:
            return self._session_helix_ready

    def is_helix_forbidden(self) -> bool:
        with self._lock:
            return self._helix_snapshot_forbidden

    def set_session_ready(self, ready: bool) -> None:
        with self._lock:
            self._session_helix_ready = bool(ready)

    def reset_session_ready(self) -> None:
        """Call on Twitch reconnect so alerts stay gated until Helix sync finishes."""
        with self._lock:
            self._session_helix_ready = False
            self._helix_snapshot_forbidden = False

    @staticmethod
    def _normalize_login(user_login: Optional[str]) -> Optional[str]:
        if not user_login:
            return None
        login = str(user_login).strip().lower()
        return login or None

    @staticmethod
    def _normalize_id(user_id: Optional[str]) -> Optional[str]:
        if user_id is None:
            return None
        uid = str(user_id).strip()
        return uid or None

    def is_known(
        self,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
    ) -> bool:
        """True if this user_id or login was ever recorded as a subscriber."""
        uid = self._normalize_id(user_id)
        login = self._normalize_login(user_login)
        with self._lock:
            if uid and uid in self._user_ids:
                return True
            if login and login in self._user_logins:
                return True
            return False

    def record(
        self,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
        *,
        source: str = "",
        cumulative_months: Optional[int] = None,
    ) -> None:
        """Remember a user as ever-subscribed. Never deletes on sub end."""
        uid = self._normalize_id(user_id)
        login = self._normalize_login(user_login)
        if not uid and not login:
            return

        now = time.time()
        with self._lock:
            if uid:
                self._user_ids.add(uid)
            if login:
                self._user_logins.add(login)

        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                if uid:
                    cur.execute(
                        "SELECT first_seen_at FROM known_subscribers WHERE user_id = ?",
                        (uid,),
                    )
                    row = cur.fetchone()
                    if row:
                        cur.execute(
                            """
                            UPDATE known_subscribers
                            SET user_login = COALESCE(?, user_login),
                                last_seen_at = ?,
                                source = CASE WHEN ? != '' THEN ? ELSE source END,
                                cumulative_months = COALESCE(?, cumulative_months)
                            WHERE user_id = ?
                            """,
                            (login, now, source, source, cumulative_months, uid),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO known_subscribers
                            (user_id, user_login, first_seen_at, last_seen_at,
                             source, cumulative_months)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (uid, login, now, now, source, cumulative_months),
                        )
                    if login:
                        cur.execute(
                            "DELETE FROM known_subscriber_logins WHERE user_login = ?",
                            (login,),
                        )
                elif login:
                    cur.execute(
                        "SELECT 1 FROM known_subscriber_logins WHERE user_login = ?",
                        (login,),
                    )
                    if cur.fetchone():
                        cur.execute(
                            """
                            UPDATE known_subscriber_logins
                            SET last_seen_at = ?,
                                source = CASE WHEN ? != '' THEN ? ELSE source END
                            WHERE user_login = ?
                            """,
                            (now, source, source, login),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO known_subscriber_logins
                            (user_login, first_seen_at, last_seen_at, source)
                            VALUES (?, ?, ?, ?)
                            """,
                            (login, now, now, source),
                        )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(
                "Failed to persist subscriber registry entry id=%s login=%s: %s",
                uid,
                login,
                e,
                exc_info=True,
            )

    def mark_new_sub_alerted(self, user_id: Optional[str]) -> None:
        uid = self._normalize_id(user_id)
        if not uid:
            return
        now = time.time()
        with self._lock:
            self._recent_new_sub_alerts[uid] = now
            # was_new_sub_alerted_recently only evicts the id it is asked about, so a
            # one-off new sub would otherwise sit here for the life of the process.
            expired = [
                key
                for key, ts in self._recent_new_sub_alerts.items()
                if (now - ts) > _NEW_SUB_ALERT_DEDUP_SECONDS
            ]
            for key in expired:
                self._recent_new_sub_alerts.pop(key, None)

    def was_new_sub_alerted_recently(self, user_id: Optional[str]) -> bool:
        uid = self._normalize_id(user_id)
        if not uid:
            return False
        now = time.time()
        with self._lock:
            ts = self._recent_new_sub_alerts.get(uid)
            if ts is None:
                return False
            if (now - ts) > _NEW_SUB_ALERT_DEDUP_SECONDS:
                self._recent_new_sub_alerts.pop(uid, None)
                return False
            return True

    def store_pending_task(self, user_id: str, task: asyncio.Task) -> None:
        uid = self._normalize_id(user_id)
        if not uid:
            return
        with self._lock:
            old = self._pending_tasks.pop(uid, None)
            self._pending_tasks[uid] = task
        if old and not old.done():
            old.cancel()

    def cancel_pending(self, user_id: Optional[str]) -> bool:
        """Cancel a pending new-sub debounce task. Returns True if cancelled."""
        uid = self._normalize_id(user_id)
        if not uid:
            return False
        with self._lock:
            task = self._pending_tasks.pop(uid, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def has_pending(self, user_id: Optional[str]) -> bool:
        """True if a new-sub debounce task is still outstanding for this user."""
        uid = self._normalize_id(user_id)
        if not uid:
            return False
        with self._lock:
            task = self._pending_tasks.get(uid)
            return task is not None and not task.done()

    def clear_pending(self, user_id: Optional[str]) -> None:
        uid = self._normalize_id(user_id)
        if not uid:
            return
        with self._lock:
            self._pending_tasks.pop(uid, None)

    def seed_from_statistics(self) -> int:
        """Seed known logins from statistics user_events (sub/resub only)."""
        try:
            from .path_utils import get_data_path

            db_path = get_data_path(os.path.join("data", "statistics.db"))
            if not os.path.isfile(db_path):
                legacy = get_data_path("statistics.db")
                if os.path.isfile(legacy):
                    db_path = legacy
                else:
                    return 0

            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='user_events'"
                )
                if not cur.fetchone():
                    return 0
                cur.execute(
                    """
                    SELECT DISTINCT username FROM user_events
                    WHERE event_type IN ('sub', 'resub')
                      AND username IS NOT NULL
                      AND trim(username) != ''
                    """
                )
                rows = cur.fetchall()
            finally:
                conn.close()

            count = 0
            for (username,) in rows:
                login = self._normalize_login(username)
                if not login:
                    continue
                already = self.is_known(user_login=login)
                self.record(user_login=login, source="statistics_seed")
                if not already:
                    count += 1
            if count:
                logger.info(
                    "Seeded subscriber registry with %d logins from statistics",
                    count,
                )
            return count
        except Exception as e:
            logger.error(
                "Failed to seed subscriber registry from statistics: %s",
                e,
                exc_info=True,
            )
            return 0

    async def sync_from_helix(self) -> bool:
        """Paginate Helix GET /subscriptions into the registry.

        Sets session ready only on success.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_lock.acquire)
        try:
            ok = await self._fetch_helix_subscribers()
            if ok:
                self.set_session_ready(True)
            return ok
        finally:
            try:
                self._sync_lock.release()
            except RuntimeError:
                pass

    async def _fetch_helix_subscribers(self) -> bool:
        from . import twitch
        from .twitch import TwitchPermissionError, TwitchSessionNotReadyError

        api = twitch.twitch_api
        if not api or not getattr(api, "user_id", None):
            return False

        broadcaster_id = str(api.user_id)
        try:
            cursor: Optional[str] = None
            total = 0
            while True:
                params: dict = {
                    "broadcaster_id": broadcaster_id,
                    "first": _HELIX_PAGE_SIZE,
                }
                if cursor:
                    params["after"] = cursor
                url = "https://api.twitch.tv/helix/subscriptions"
                response = await api.generic_api_call(url, "GET", params=params)
                if not response:
                    break
                for row in response.get("data") or []:
                    uid = row.get("user_id")
                    login = row.get("user_login") or row.get("user_name")
                    self.record(
                        user_id=str(uid) if uid else None,
                        user_login=login,
                        source="helix_snapshot",
                    )
                    total += 1
                pagination = response.get("pagination") or {}
                cursor = pagination.get("cursor")
                if not cursor:
                    break
                await asyncio.sleep(_HELIX_PAGE_DELAY_SECONDS)

            logger.info(
                "Helix subscriber snapshot complete: recorded %d current subscribers",
                total,
            )
            return True
        except TwitchSessionNotReadyError as e:
            logger.debug("Subscriber Helix sync deferred: %s", e)
            return False
        except TwitchPermissionError as e:
            with self._lock:
                self._helix_snapshot_forbidden = True
            logger.warning(
                "Helix subscriber snapshot unavailable: the channel must have "
                "Affiliate or Partner status."
            )
            logger.debug("Helix GET /subscriptions forbidden: %s", e)
            return False
        except Exception as e:
            err = str(e).lower()
            if "session not ready" in err:
                logger.debug("Subscriber Helix sync deferred: %s", e)
                return False
            if "partner or affiliate" in err or (
                "403" in err and "forbidden" in err
            ):
                with self._lock:
                    self._helix_snapshot_forbidden = True
                logger.warning(
                    "Helix subscriber snapshot unavailable: the channel must have "
                    "Affiliate or Partner status."
                )
                return False
            logger.error(
                "Error syncing subscribers from Helix: %s", e, exc_info=True
            )
            return False


_registry = SubscriberRegistry()


def get_subscriber_registry() -> SubscriberRegistry:
    return _registry

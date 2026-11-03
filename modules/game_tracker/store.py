# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Local SQLite store for the Games Tracker (data/game_tracker.db).

Holds every tracked game, the log of changes (for recent unlocks and period totals),
the tracker settings and the per-platform baseline (which account the silent first
import was for). PSN identity (presence name, NPWR ID, NP Title ID) stays in the PSN
game cache; ``psn_sync`` keeps the two in step.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .models import GameEvent, TrackedGame, TrackerSettings

logger = logging.getLogger(__name__)

DB_FILE = os.path.join("data", "game_tracker.db")
EVENT_LOG_MAX = 20000  # newest change events kept

_SETTINGS_KEY = "settings"
_BASELINE_KEY = "baseline:{platform}"


class GameTrackerStore:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or self._default_db_path()
        self._lock = threading.RLock()
        self._ensure_db()

    @staticmethod
    def _default_db_path() -> str:
        from ..path_utils import get_data_path

        return get_data_path(DB_FILE)

    @property
    def path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS games (
                        platform TEXT NOT NULL,
                        game_id TEXT NOT NULL,
                        data TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (platform, game_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        platform TEXT NOT NULL,
                        game_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        grade TEXT DEFAULT '',
                        data TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
                conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.commit()
            finally:
                conn.close()

    # ----- games -----

    def list_games(self, platform: Optional[str] = None) -> List[TrackedGame]:
        with self._lock:
            conn = self._connect()
            try:
                if platform:
                    rows = conn.execute("SELECT data FROM games WHERE platform = ?", (platform,)).fetchall()
                else:
                    rows = conn.execute("SELECT data FROM games").fetchall()
            finally:
                conn.close()
        out: List[TrackedGame] = []
        for (raw,) in rows:
            try:
                out.append(TrackedGame.from_dict(json.loads(raw)))
            except Exception as e:
                logger.debug("Games Tracker: skipped an unreadable game row: %s", e)
        return out

    def games_by_id(self, platform: str) -> Dict[str, TrackedGame]:
        return {g.game_id: g for g in self.list_games(platform)}

    def get_game(self, platform: str, game_id: str) -> Optional[TrackedGame]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data FROM games WHERE platform = ? AND game_id = ?", (platform, str(game_id))
                ).fetchone()
            finally:
                conn.close()
        return TrackedGame.from_dict(json.loads(row[0])) if row else None

    def upsert_games(self, games: Iterable[TrackedGame]) -> int:
        now = time.time()
        rows = []
        for g in games:
            g.updated_at = now
            rows.append((g.platform, str(g.game_id), json.dumps(g.to_dict(), separators=(",", ":")), now))
        if not rows:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT INTO games (platform, game_id, data, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(platform, game_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        return len(rows)

    def delete_game(self, platform: str, game_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM games WHERE platform = ? AND game_id = ?", (platform, str(game_id)))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def rekey_game(self, platform: str, old_id: str, new_id: str) -> Optional[TrackedGame]:
        """Move a game to a new ID (a corrected NPWR ID). Returns the moved game."""
        old_id, new_id = str(old_id), str(new_id)
        if old_id == new_id:
            return self.get_game(platform, old_id)
        with self._lock:
            game = self.get_game(platform, old_id)
            if game is None:
                return None
            game.game_id = new_id
            self.upsert_games([game])
            self.delete_game(platform, old_id)
            conn = self._connect()
            try:
                conn.execute("UPDATE events SET game_id = ? WHERE platform = ? AND game_id = ?", (new_id, platform, old_id))
                conn.commit()
            finally:
                conn.close()
            return game

    def clear_platform(self, platform: str) -> None:
        """Forget a platform's games and history (another account was connected)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM games WHERE platform = ?", (platform,))
                conn.execute("DELETE FROM events WHERE platform = ?", (platform,))
                conn.commit()
            finally:
                conn.close()

    # ----- change log -----

    def record_events(self, events: Iterable[GameEvent]) -> int:
        rows = [
            (e.ts, e.platform, str(e.game_id), e.kind, e.unlock_grade or "", json.dumps(e.to_dict(), separators=(",", ":")))
            for e in events
        ]
        if not rows:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany("INSERT INTO events (ts, platform, game_id, kind, grade, data) VALUES (?, ?, ?, ?, ?, ?)", rows)
                count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if count > EVENT_LOG_MAX:
                    conn.execute(
                        "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY ts ASC, id ASC LIMIT ?)",
                        (count - EVENT_LOG_MAX,),
                    )
                conn.commit()
            finally:
                conn.close()
        return len(rows)

    def recent_events(
        self,
        limit: int = 10,
        *,
        platform: Optional[str] = None,
        game_id: Optional[str] = None,
        kinds: Iterable[str] = ("unlock",),
    ) -> List[GameEvent]:
        kinds = tuple(kinds)
        sql = f"SELECT data FROM events WHERE kind IN ({','.join('?' * len(kinds))})"
        args: List[Any] = list(kinds)
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        if game_id:
            sql += " AND game_id = ?"
            args.append(str(game_id))
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        out = []
        for (raw,) in rows:
            try:
                out.append(GameEvent(**json.loads(raw)))
            except Exception:
                continue
        return out

    def event_counts(self, since: float = 0.0, platform: Optional[str] = None) -> Dict[str, int]:
        """Counts since a time: ``{kind: n}`` plus ``grade:<grade>`` for unlocks."""
        sql = "SELECT kind, grade, COUNT(*) FROM events WHERE ts >= ?"
        args: List[Any] = [float(since)]
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        sql += " GROUP BY kind, grade"
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        out: Dict[str, int] = {}
        for kind, grade, n in rows:
            out[kind] = out.get(kind, 0) + n
            if kind == "unlock" and grade:
                out[f"grade:{grade}"] = out.get(f"grade:{grade}", 0) + n
        return out

    # ----- meta: settings and baselines -----

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            finally:
                conn.close()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_meta(self, key: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
                conn.commit()
            finally:
                conn.close()

    def load_settings(self) -> TrackerSettings:
        return TrackerSettings.from_dict(self.get_meta(_SETTINGS_KEY, {}))

    def save_settings(self, settings: TrackerSettings) -> None:
        self.set_meta(_SETTINGS_KEY, TrackerSettings.from_dict(settings.to_dict()).to_dict())

    def baseline(self, platform: str) -> Optional[Dict[str, Any]]:
        value = self.get_meta(_BASELINE_KEY.format(platform=platform))
        return value if isinstance(value, dict) else None

    def set_baseline(self, platform: str, account: str) -> None:
        self.set_meta(_BASELINE_KEY.format(platform=platform), {"account": str(account), "at": time.time()})

    def reset_baseline(self, platform: str) -> None:
        """Treat current progress as the starting point: games are re-read silently on the next sync."""
        games = self.list_games(platform)
        for g in games:
            g.baselined = False
        self.upsert_games(games)


_store: Optional[GameTrackerStore] = None
_store_lock = threading.Lock()


def get_store() -> GameTrackerStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = GameTrackerStore()
        return _store


def set_store(store: Optional[GameTrackerStore]) -> None:
    """Swap the shared store (tests)."""
    global _store
    with _store_lock:
        _store = store

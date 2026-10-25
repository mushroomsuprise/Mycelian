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

import asyncio
import concurrent.futures
import copy
import logging
import sqlite3
import json
import os
import threading
import time
import weakref
from abc import ABC, abstractmethod
from contextlib import contextmanager
from urllib.parse import urlparse
from typing import Any, Dict, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

from .path_utils import get_data_path
from .notification_engine import nav_actions_settings, notify, notify_critical

# Third-party imports are loaded on first use (DB-02). Hiddenimports stay in build.py.
firebase_admin = None
firebase_db = None
FIREBASE_AVAILABLE = False
_firebase_checked = False

pymongo = None
MongoClient = None
MONGODB_AVAILABLE = False
_mongo_checked = False


def _ensure_firebase() -> bool:
    global firebase_admin, firebase_db, FIREBASE_AVAILABLE, _firebase_checked
    if _firebase_checked:
        return FIREBASE_AVAILABLE
    _firebase_checked = True
    try:
        import firebase_admin as _fa
        from firebase_admin import db as _fdb

        firebase_admin = _fa
        firebase_db = _fdb
        FIREBASE_AVAILABLE = True
    except ImportError:
        FIREBASE_AVAILABLE = False
    return FIREBASE_AVAILABLE


def _ensure_pymongo() -> bool:
    global pymongo, MongoClient, MONGODB_AVAILABLE, _mongo_checked
    if _mongo_checked:
        return MONGODB_AVAILABLE
    _mongo_checked = True
    try:
        import pymongo as _pm
        from pymongo import MongoClient as _MC

        pymongo = _pm
        MongoClient = _MC
        MONGODB_AVAILABLE = True
    except ImportError:
        MONGODB_AVAILABLE = False
    return MONGODB_AVAILABLE

logger = logging.getLogger(__name__)


def _caller_is_overlay_hub() -> bool:
    """True when the caller is the Flask-SocketIO gevent hub thread."""
    try:
        from . import web_engine

        inst = getattr(web_engine, "web_engine_instance", None)
        if inst is None:
            return False
        ident = getattr(inst, "_server_thread_ident", None)
        return ident is not None and threading.get_ident() == ident
    except Exception:
        return False


def normalize_firebase_database_url(url: str) -> str:
    """Strip whitespace and ensure trailing slash for Firebase Admin databaseURL."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.endswith("/"):
        return u + "/"
    return u


def is_valid_firebase_rtdb_url(url: str) -> bool:
    """Accept legacy *.firebaseio.com and regional *.firebasedatabase.app RTDB URLs."""
    u = (url or "").strip()
    if not u.lower().startswith("https://"):
        return False
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host.endswith(".firebaseio.com"):
        return True
    if host.endswith(".firebasedatabase.app"):
        return True
    return False


class DatabaseReadError(Exception):
    """Raised when a database read fails (distinct from a missing path)."""


# Firebase preload fan-out (firebase-admin's HTTP pool holds 10 connections).
_FIREBASE_PRELOAD_CONCURRENCY = 8
# Child read by the Firebase reachability probe. Shallow, so it costs a few
# bytes whether or not the node exists (the admin SDK bypasses rules).
_FIREBASE_PROBE_CHILD = "DatabaseSettings"


@dataclass
class DatabaseConfig:
    """Configuration settings for database connections"""

    database_type: str = "sql"  # Default to SQLite

    # SQLite settings
    sql_database_path: str = "mycelian.db"

    # Firebase settings
    firebase_service_account_path: str = ""
    firebase_database_url: str = ""

    # MongoDB settings
    mongodb_connection_string: str = ""
    mongodb_database_name: str = ""

    # Common settings
    streamer_name: str = "mycelian"
    connection_timeout: int = 30
    retry_attempts: int = 3


class DatabaseInterface(ABC):
    """Abstract base class for database implementations"""

    # Plain-language reason for the last failed initialize() (shown in notification details).
    last_error: str = ""
    # Set by get_snapshot() when the read failed (it still returns {} for old callers).
    snapshot_error: str = ""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the database connection"""
        pass

    @abstractmethod
    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from the database"""
        pass

    @abstractmethod
    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in the database"""
        pass

    @abstractmethod
    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in the database"""
        pass

    @abstractmethod
    def delete_data(self, path: str) -> bool:
        """Delete data from the database"""
        pass

    @abstractmethod
    def get_connection_status(self) -> Dict[str, Any]:
        """Get the current connection status"""
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """Test the database connection"""
        pass

    @abstractmethod
    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple paths asynchronously"""
        pass

    @abstractmethod
    def get_all_paths(self) -> List[str]:
        """Get all data paths stored in the database"""
        pass

    @abstractmethod
    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all database data as a nested dictionary"""
        pass


# Matches safe_alert_timestamp for numeric and missing timestamps.
_ALERT_TS_SQL = "COALESCE(CAST(json_extract(data_json, '$.timestamp') AS REAL), 0)"
_SQL_IN_CHUNK = 400


def _strip_db_path(path: str) -> str:
    if path.startswith("/"):
        return path[1:]
    return path


def _sorted_alert_records(storage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Newest-first alert dicts with alert_id set. Does not mutate inputs."""
    alerts = []
    for alert_id, alert_data in storage.items():
        if not isinstance(alert_data, dict):
            continue
        item = dict(alert_data)
        item["alert_id"] = str(alert_id)
        alerts.append(item)
    alerts.sort(
        key=lambda item: _coerce_alert_timestamp(item.get("timestamp")),
        reverse=True,
    )
    return alerts


def _alert_storage_prefix_query() -> Dict[str, Any]:
    """Mongo range for Alerts/AlertStorage/{id}, excluding the parent path."""
    return {
        "data_path": {
            "$gte": "Alerts/AlertStorage/",
            "$lt": "Alerts/AlertStorage0",
        }
    }


def _coerce_alert_timestamp(value: Any) -> float:
    """Unix seconds for alert sort. Matches modules.alertutils.safe_alert_timestamp."""
    if value is None or value == "":
        return 0.0
    if value == "now":
        return time.time()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class SQLDatabase(DatabaseInterface):
    """SQLite database implementation"""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        # Use path utils to get correct database path for exe
        if config.sql_database_path and not os.path.isabs(config.sql_database_path):
            self.db_path = get_data_path(config.sql_database_path)
        else:
            self.db_path = config.sql_database_path
        self.streamer_name = config.streamer_name
        self._connection = None
        self._lock = threading.RLock()
        self._initialized = False

        # Connection pooling
        self._connection_pool = []
        self._pool_size = 3
        self._pool_lock = threading.Lock()

    def initialize(self) -> bool:
        """Initialize SQLite database and create tables"""
        try:
            with self._lock:
                # Create database directory if it doesn't exist
                os.makedirs(
                    os.path.dirname(self.db_path)
                    if os.path.dirname(self.db_path)
                    else ".",
                    exist_ok=True,
                )

                # Connect to database
                self._connection = sqlite3.connect(
                    self.db_path, check_same_thread=False, timeout=30.0
                )
                self._connection.row_factory = sqlite3.Row
                self._configure_app_sqlite_connection(self._connection)

                # Create tables
                self._create_tables()
                self._initialized = True

                logger.info(f"SQLite database initialized at {self.db_path}")
                return True

        except Exception as e:
            logger.error(
                f"Failed to initialize SQLite database: {str(e)}", exc_info=True
            )
            self.last_error = f"Couldn't open {self.db_path}: {e}"
            return False

    def _create_tables(self):
        """Create necessary tables for the application"""
        cursor = self._connection.cursor()

        # Main data table - stores all application data in JSON format
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                streamer_name TEXT NOT NULL,
                data_path TEXT NOT NULL,
                data_json TEXT NOT NULL,
                etag TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(streamer_name, data_path)
            )
        """)

        # UNIQUE(streamer_name, data_path) already indexes that pair.
        cursor.execute("DROP INDEX IF EXISTS idx_streamer_path")

        # Sort stored alerts by the timestamp already inside the JSON document.
        # The index does not rewrite alert payloads.
        try:
            cursor.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_alert_storage_ts
                ON app_data(streamer_name, {_ALERT_TS_SQL})
                WHERE data_path LIKE 'Alerts/AlertStorage/%'
                  AND data_path != 'Alerts/AlertStorage'
                """
            )
        except sqlite3.OperationalError as index_error:
            logger.debug("Alert storage timestamp index unavailable: %s", index_error)

        # Trigger to update the updated_at timestamp
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_timestamp
            AFTER UPDATE ON app_data
            BEGIN
                UPDATE app_data SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END
        """)

        self._connection.commit()

    @staticmethod
    def _configure_app_sqlite_connection(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            # A few megabytes. Negative cache_size is KiB. Do not mmap the file.
            conn.execute("PRAGMA cache_size=-4096")
            conn.execute("PRAGMA temp_store=MEMORY")
        except Exception:
            pass

    def _get_connection(self):
        """Get connection from pool or create new one"""
        with self._pool_lock:
            if self._connection_pool:
                return self._connection_pool.pop()

        # Create new connection
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        self._configure_app_sqlite_connection(conn)
        return conn

    def _return_connection(self, conn):
        """Return connection to pool"""
        with self._pool_lock:
            if len(self._connection_pool) < self._pool_size:
                self._connection_pool.append(conn)
            else:
                conn.close()

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from SQLite database"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            cursor.execute(
                """
                SELECT data_json, etag FROM app_data
                WHERE streamer_name = ? AND data_path = ?
            """,
                (self.streamer_name, path),
            )

            row = cursor.fetchone()

            if row:
                data = json.loads(row["data_json"])
                if request_etag:
                    return {"data": data, "etag": row["etag"]}
                return data
            else:
                if request_etag:
                    return {"data": {}, "etag": None}
                return {}

        except Exception as e:
            logger.error(
                f"Error getting data from SQLite at {path}: {str(e)}", exc_info=True
            )
            raise DatabaseReadError(str(e)) from e
        finally:
            if conn:
                self._return_connection(conn)

    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in SQLite database"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            self._upsert_row(cursor, path, data)
            conn.commit()
            logger.debug(f"Successfully set data at SQLite path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error setting data in SQLite at {path}: {str(e)}", exc_info=True
            )
            return False
        finally:
            if conn:
                self._return_connection(conn)

    def _upsert_row(self, cursor, path: str, data: Dict[str, Any]) -> None:
        """Insert or update one path in place. Preserves created_at on update."""
        data_json = json.dumps(data, default=str)
        etag = str(hash(data_json))
        cursor.execute(
            """
            INSERT INTO app_data
                (streamer_name, data_path, data_json, etag, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(streamer_name, data_path) DO UPDATE SET
                data_json = excluded.data_json,
                etag = excluded.etag,
                updated_at = CURRENT_TIMESTAMP
            """,
            (self.streamer_name, path, data_json, etag),
        )

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in SQLite database"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            path = _strip_db_path(path)
            cursor.execute(
                """
                SELECT data_json FROM app_data
                WHERE streamer_name = ? AND data_path = ?
                """,
                (self.streamer_name, path),
            )
            row = cursor.fetchone()
            if row is None:
                updated_data = data
            else:
                try:
                    existing_data = json.loads(row["data_json"])
                except json.JSONDecodeError as decode_error:
                    raise DatabaseReadError(str(decode_error)) from decode_error
                if existing_data:
                    existing_data.update(data)
                    updated_data = existing_data
                else:
                    updated_data = data
            self._upsert_row(cursor, path, updated_data)
            conn.commit()
            return True

        except DatabaseReadError as e:
            logger.error(
                f"Skipping SQLite update at {path}; read failed: {e}", exc_info=True
            )
            return False
        except Exception as e:
            logger.error(
                f"Error updating data in SQLite at {path}: {str(e)}", exc_info=True
            )
            return False
        finally:
            if conn:
                self._return_connection(conn)

    def delete_data(self, path: str) -> bool:
        """Delete data from SQLite database"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            cursor.execute(
                """
                DELETE FROM app_data
                WHERE streamer_name = ? AND data_path = ?
            """,
                (self.streamer_name, path),
            )

            conn.commit()
            logger.debug(f"Successfully deleted data at SQLite path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error deleting data from SQLite at {path}: {str(e)}", exc_info=True
            )
            return False
        finally:
            if conn:
                self._return_connection(conn)

    def get_connection_status(self) -> Dict[str, Any]:
        """Get SQLite connection status"""
        try:
            if self._connection:
                # Test the connection
                cursor = self._connection.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()

                return {
                    "status": "Connected",
                    "database_type": "SQLite",
                    "database_path": self.db_path,
                    "streamer_name": self.streamer_name,
                    "is_connected": True,
                    "last_check": datetime.now().isoformat(),
                }
            else:
                return {
                    "status": "Disconnected",
                    "database_type": "SQLite",
                    "database_path": self.db_path,
                    "is_connected": False,
                    "last_check": datetime.now().isoformat(),
                }
        except Exception as e:
            return {
                "status": f"Error: {str(e)}",
                "database_type": "SQLite",
                "database_path": self.db_path,
                "is_connected": False,
                "last_check": datetime.now().isoformat(),
            }

    def test_connection(self) -> bool:
        """Test SQLite connection"""
        try:
            if not self._initialized:
                return self.initialize()

            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return True
            finally:
                self._return_connection(conn)
        except Exception as e:
            logger.error(f"SQLite connection test failed: {str(e)}")
            return False

    def _get_multiple_data(self, paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Load many paths in chunked IN queries. Missing paths are {}."""
        return self._get_multiple_data_checked(paths)[0]

    def _get_multiple_data_checked(self, paths: List[str]):
        """Like _get_multiple_data, plus the set of paths whose read failed."""
        output: Dict[str, Dict[str, Any]] = {path: {} for path in paths}
        failed: set = set()
        if not paths:
            return output, failed
        if not self._initialized:
            self.initialize()

        normalized: List[tuple] = []
        for original in paths:
            normalized.append((original, _strip_db_path(original)))

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            found: Dict[str, Any] = {}
            bad_norms: set = set()
            unique_norms = list(dict.fromkeys(norm for _, norm in normalized))
            for offset in range(0, len(unique_norms), _SQL_IN_CHUNK):
                chunk = unique_norms[offset : offset + _SQL_IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    SELECT data_path, data_json FROM app_data
                    WHERE streamer_name = ? AND data_path IN ({placeholders})
                    """,
                    (self.streamer_name, *chunk),
                )
                for row in cursor.fetchall():
                    try:
                        found[row["data_path"]] = json.loads(row["data_json"])
                    except json.JSONDecodeError as decode_error:
                        logger.error(
                            "Error parsing JSON for %s: %s",
                            row["data_path"],
                            decode_error,
                        )
                        found[row["data_path"]] = {}
                        bad_norms.add(row["data_path"])
            for original, norm in normalized:
                output[original] = found.get(norm) or {}
                if norm in bad_norms:
                    failed.add(original)
            return output, failed
        except Exception as e:
            logger.error(f"Error fetching multiple SQLite paths: {e}", exc_info=True)
            return output, set(paths)
        finally:
            if conn:
                self._return_connection(conn)

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple paths asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_multiple_data, paths)

    async def get_multiple_data_checked_async(self, paths: List[str]):
        """Returns (output, failed_paths); failed reads are {} in output."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_multiple_data_checked, paths)

    def get_all_paths(self) -> List[str]:
        """Get all data paths stored in SQLite database"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT data_path FROM app_data
                WHERE streamer_name = ?
                ORDER BY data_path
            """,
                (self.streamer_name,),
            )
            paths = [row[0] for row in cursor.fetchall()]
            return paths

        except Exception as e:
            logger.error(
                f"Error getting all paths from SQLite: {str(e)}", exc_info=True
            )
            return []
        finally:
            if conn:
                self._return_connection(conn)

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all database data as a nested dictionary"""
        if not self._initialized:
            self.initialize()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT data_path, data_json FROM app_data
                WHERE streamer_name = ?
                """,
                (self.streamer_name,),
            )
            snapshot: Dict[str, Any] = {}
            for row in cursor.fetchall():
                path = row["data_path"]
                try:
                    data = json.loads(row["data_json"])
                except json.JSONDecodeError as decode_error:
                    raise DatabaseReadError(
                        f"Invalid JSON at {path}: {decode_error}"
                    ) from decode_error
                # Include empty dicts so snapshot mirrors all rows.
                self._set_nested_value(snapshot, path, data)
            return snapshot

        except Exception as e:
            logger.error(f"Error getting snapshot from SQLite: {str(e)}", exc_info=True)
            self.snapshot_error = str(e)
            return {}
        finally:
            if conn:
                self._return_connection(conn)

    def _alert_storage_rows(
        self, cursor, *, max_alerts: Optional[int] = None, meta_only: bool = False
    ):
        columns = "data_path" if meta_only else "data_path, data_json"
        if meta_only:
            columns = f"data_path, {_ALERT_TS_SQL} AS alert_ts"
        sql = f"""
            SELECT {columns} FROM app_data
            WHERE streamer_name = ?
              AND data_path LIKE 'Alerts/AlertStorage/%'
              AND data_path != 'Alerts/AlertStorage'
        """
        params: list = [self.streamer_name]
        if max_alerts and max_alerts > 0:
            sql += f" ORDER BY {_ALERT_TS_SQL} DESC LIMIT ?"
            params.append(int(max_alerts))
        elif meta_only:
            sql += f" ORDER BY {_ALERT_TS_SQL} DESC"
        cursor.execute(sql, tuple(params))
        return cursor.fetchall()

    def fetch_alert_storage(self, max_alerts: Optional[int] = None) -> Dict[str, Any]:
        """Return alert_id -> alert JSON. Same documents, optional newest-N limit."""
        if not self._initialized:
            self.initialize()
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            rows = self._alert_storage_rows(cursor, max_alerts=max_alerts)
            storage: Dict[str, Any] = {}
            for row in rows:
                path = row["data_path"]
                alert_id = path.rsplit("/", 1)[-1]
                try:
                    storage[alert_id] = json.loads(row["data_json"])
                except json.JSONDecodeError as decode_error:
                    logger.error("Error parsing JSON for stored alert %s: %s", path, decode_error)
            return storage
        finally:
            if conn:
                self._return_connection(conn)

    def list_alert_storage_meta(self) -> List[tuple]:
        """Return (alert_id, timestamp) newest first, without the alert JSON."""
        if not self._initialized:
            self.initialize()
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            rows = self._alert_storage_rows(cursor, meta_only=True)
            meta = []
            for row in rows:
                alert_id = row["data_path"].rsplit("/", 1)[-1]
                try:
                    ts = float(row["alert_ts"] or 0)
                except (TypeError, ValueError):
                    ts = 0.0
                meta.append((alert_id, ts))
            return meta
        finally:
            if conn:
                self._return_connection(conn)

    def query_alert_storage_page(self, page: int, limit: int) -> Dict[str, Any]:
        """One page of stored alerts, newest first, plus the real total count."""
        if not self._initialized:
            self.initialize()
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 1))
        offset = (page - 1) * limit
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM app_data
                WHERE streamer_name = ?
                  AND data_path LIKE 'Alerts/AlertStorage/%'
                  AND data_path != 'Alerts/AlertStorage'
                """,
                (self.streamer_name,),
            )
            total_count = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                f"""
                SELECT data_path, data_json FROM app_data
                WHERE streamer_name = ?
                  AND data_path LIKE 'Alerts/AlertStorage/%'
                  AND data_path != 'Alerts/AlertStorage'
                ORDER BY {_ALERT_TS_SQL} DESC
                LIMIT ? OFFSET ?
                """,
                (self.streamer_name, limit, offset),
            )
            alerts = []
            for row in cursor.fetchall():
                alert_id = row["data_path"].rsplit("/", 1)[-1]
                try:
                    data = json.loads(row["data_json"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    data = {}
                item = dict(data)
                item["alert_id"] = alert_id
                alerts.append(item)
            return {"alerts": alerts, "total_count": total_count}
        finally:
            if conn:
                self._return_connection(conn)

    def delete_paths(self, paths: List[str]) -> bool:
        """Delete many paths in chunked transactions."""
        if not paths:
            return True
        if not self._initialized:
            self.initialize()
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            norms = [_strip_db_path(path) for path in paths]
            for offset in range(0, len(norms), _SQL_IN_CHUNK):
                chunk = norms[offset : offset + _SQL_IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    DELETE FROM app_data
                    WHERE streamer_name = ? AND data_path IN ({placeholders})
                    """,
                    (self.streamer_name, *chunk),
                )
            conn.commit()
            return True
        except Exception as e:
            logger.error("Error batch-deleting SQLite paths: %s", e, exc_info=True)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            if conn:
                self._return_connection(conn)

    def _set_nested_value(self, d: Dict[str, Any], path: str, value: Any) -> None:
        """Set a value in a nested dictionary using a path string"""
        keys = path.split("/")
        current = d
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value


class FirebaseDatabase(DatabaseInterface):
    """Firebase Realtime Database implementation"""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.service_account_path = config.firebase_service_account_path
        self.database_url = normalize_firebase_database_url(
            config.firebase_database_url
        )
        self.streamer_name = config.streamer_name
        self._app = None
        self._root_ref = None
        self._initialized = False
        self._connection_tested = False
        # One probe at a time; concurrent callers share its outcome (SU-04).
        self._probe_lock = threading.Lock()
        self._probe_flight: Optional[Dict[str, Any]] = None

    def _http_timeout(self) -> int:
        """Per-request HTTP timeout (seconds) for the firebase-admin DB client."""
        return max(5, int(getattr(self.config, "connection_timeout", 30) or 30))

    def initialize(self) -> bool:
        """Initialize Firebase connection"""
        if not _ensure_firebase():
            logger.error("Firebase SDK not available. Install firebase-admin package.")
            self.last_error = "The Firebase library (firebase-admin) isn't installed."
            return False

        # Validate required configuration
        if not self.service_account_path:
            logger.error("Firebase service account path is required but not configured")
            self.last_error = "No Firebase service account key file is set."
            return False

        if not self.database_url:
            logger.error("Firebase database URL is required but not configured")
            self.last_error = "No Firebase database URL is set."
            return False

        try:
            # Check if service account key file exists
            if not os.path.exists(self.service_account_path):
                logger.error(
                    f"Firebase service account key not found at {self.service_account_path}"
                )
                self.last_error = (
                    f"The service account key file wasn't found at {self.service_account_path}."
                )
                return False

            # Validate the service account key file
            try:
                import json

                with open(self.service_account_path, "r") as f:
                    key_data = json.load(f)
                    required_fields = [
                        "type",
                        "project_id",
                        "private_key_id",
                        "private_key",
                        "client_email",
                    ]
                    missing_fields = [
                        field for field in required_fields if field not in key_data
                    ]
                    if missing_fields:
                        logger.error(
                            f"Firebase service account key is missing required fields: {missing_fields}"
                        )
                        self.last_error = (
                            "The service account key file is missing: "
                            + ", ".join(missing_fields)
                        )
                        return False
            except Exception as e:
                logger.error(f"Invalid Firebase service account key file: {str(e)}")
                self.last_error = f"The service account key file isn't valid JSON: {e}"
                return False

            # Validate database URL format (firebaseio.com or regional firebasedatabase.app)
            if not is_valid_firebase_rtdb_url(self.database_url):
                logger.error(
                    "Invalid Firebase Realtime Database URL. Use https://…firebaseio.com/ "
                    "or https://…firebasedatabase.app/ (trailing slash optional)."
                )
                self.last_error = (
                    "The database URL must look like https://…firebaseio.com/ "
                    "or https://…firebasedatabase.app/."
                )
                return False

            # Check if Firebase app already exists
            try:
                self._app = firebase_admin.get_app()
                logger.debug("Using existing Firebase app")
            except ValueError:
                # App doesn't exist, create it
                try:
                    cred = firebase_admin.credentials.Certificate(
                        self.service_account_path
                    )
                    self._app = firebase_admin.initialize_app(
                        cred,
                        {
                            "databaseURL": self.database_url,
                            "httpTimeout": self._http_timeout(),
                        },
                    )
                    logger.debug("Firebase initialized successfully")
                except ValueError as e:
                    if "already exists" in str(e):
                        # Try to get the existing app
                        try:
                            self._app = firebase_admin.get_app()
                            logger.debug(
                                "Using existing Firebase app after initialization conflict"
                            )
                        except ValueError:
                            # Create with unique name
                            import uuid

                            app_name = f"mycelian_{uuid.uuid4().hex[:8]}"
                            self._app = firebase_admin.initialize_app(
                                cred,
                                {
                                    "databaseURL": self.database_url,
                                    "httpTimeout": self._http_timeout(),
                                },
                                name=app_name,
                            )
                            logger.debug(
                                f"Firebase initialized with unique name: {app_name}"
                            )
                    else:
                        raise e

            # Initialize the root reference
            self._root_ref = firebase_db.reference(
                f"/{self.streamer_name}", app=self._app
            )

            # Skip connection test during startup for faster initialization
            # We'll test connection on first actual database operation
            logger.info(
                f"Firebase database initialized (connection test deferred) at {self.database_url}"
            )

            self._initialized = True
            logger.info(
                f"Firebase database ready at {self.database_url} for streamer {self.streamer_name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {str(e)}", exc_info=True)
            self.last_error = f"Firebase refused the connection: {e}"
            return False

    def _probe_connection(self) -> None:
        """Cheap connectivity check — never download the full database tree."""
        if self._root_ref is None:
            raise RuntimeError("Firebase database not initialized")
        # .info/* lives at the DB root; _root_ref is /{streamer} and cannot use .info children.
        # A shallow read of one small child is an authenticated round trip like a
        # shallow read of the streamer node, without listing every top-level key.
        self._root_ref.child(_FIREBASE_PROBE_CHILD).get(shallow=True)

    def _ensure_connection_tested(self):
        """Ensure the Firebase connection has been tested before performing operations.

        Callers that arrive while a probe is running wait for it and share its
        result instead of each sending their own probe.
        """
        if self._connection_tested:
            return
        with self._probe_lock:
            if self._connection_tested:
                return
            flight = self._probe_flight
            leader = flight is None
            if leader:
                flight = {"done": threading.Event(), "error": None}
                self._probe_flight = flight
        if not leader:
            flight["done"].wait()
            if flight["error"] is not None:
                raise flight["error"]
            return
        try:
            self._probe_connection()
            logger.debug("Firebase connection test passed")
            self._connection_tested = True
        except Exception as test_error:
            flight["error"] = test_error
            logger.error(f"Firebase connection test failed: {str(test_error)}")
            raise test_error
        finally:
            with self._probe_lock:
                self._probe_flight = None
            flight["done"].set()

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from Firebase database.

        Raises DatabaseReadError when the read fails, like the SQLite and MongoDB
        backends, so a failed read is never mistaken for an empty node.
        """
        if not self._initialized:
            self.initialize()

        if self._root_ref is None:
            logger.error("Firebase database not initialized")
            raise DatabaseReadError("Firebase database not initialized")

        try:
            # Ensure connection is tested before first operation
            self._ensure_connection_tested()

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Getting data from Firebase path: {path}")
            response = self._root_ref.child(path).get(etag=request_etag)

            if request_etag:
                data = response[0]
                etag = response[1]
                payload = data or {}
            else:
                payload = response or {}

            if logger.isEnabledFor(logging.DEBUG) and payload:
                try:
                    approx_bytes = len(
                        json.dumps(payload, default=str, ensure_ascii=False)
                    )
                    logger.debug(
                        "Firebase download path=%s approx_bytes=%d",
                        path,
                        approx_bytes,
                    )
                except Exception:
                    pass

            if request_etag:
                return {"data": payload, "etag": etag}
            return payload

        except Exception as e:
            logger.error(
                f"Error getting data from Firebase at {path}: {str(e)}", exc_info=True
            )
            raise DatabaseReadError(str(e)) from e

    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in Firebase database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                logger.error("Firebase database not initialized")
                return False

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Setting data at Firebase path: {path}")
            self._root_ref.child(path).set(data)
            logger.debug(f"Successfully set data at Firebase path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error setting data in Firebase at {path}: {str(e)}", exc_info=True
            )
            return False

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in Firebase database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                logger.error("Firebase database not initialized")
                return False

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Updating data at Firebase path: {path}")
            self._root_ref.child(path).update(data)
            logger.debug(f"Successfully updated data at Firebase path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error updating data in Firebase at {path}: {str(e)}", exc_info=True
            )
            return False

    def delete_data(self, path: str) -> bool:
        """Delete data from Firebase database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                logger.error("Firebase database not initialized")
                return False

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Deleting data at Firebase path: {path}")
            self._root_ref.child(path).delete()
            logger.debug(f"Successfully deleted data at Firebase path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error deleting data from Firebase at {path}: {str(e)}", exc_info=True
            )
            return False

    def get_connection_status(self) -> Dict[str, Any]:
        """Get Firebase connection status"""
        try:
            if self._root_ref:
                self._probe_connection()

                return {
                    "status": "Connected",
                    "database_type": "Firebase",
                    "database_url": self.database_url,
                    "service_account_path": self.service_account_path,
                    "streamer_name": self.streamer_name,
                    "is_connected": True,
                    "last_check": datetime.now().isoformat(),
                    "config_valid": True,
                }
            else:
                # Check why initialization failed
                status_msg = "Not Initialized"
                config_issues = []

                if not _ensure_firebase():
                    config_issues.append("Firebase SDK not available")
                if not self.service_account_path:
                    config_issues.append("Service account path not configured")
                elif not os.path.exists(self.service_account_path):
                    config_issues.append(
                        f"Service account key not found: {self.service_account_path}"
                    )
                if not self.database_url:
                    config_issues.append("Database URL not configured")
                elif not is_valid_firebase_rtdb_url(self.database_url):
                    config_issues.append("Invalid database URL format")

                if config_issues:
                    status_msg = f"Configuration issues: {'; '.join(config_issues)}"

                return {
                    "status": status_msg,
                    "database_type": "Firebase",
                    "database_url": self.database_url,
                    "service_account_path": self.service_account_path,
                    "is_connected": False,
                    "last_check": datetime.now().isoformat(),
                    "config_valid": len(config_issues) == 0,
                    "config_issues": config_issues,
                }
        except Exception as e:
            return {
                "status": f"Error: {str(e)}",
                "database_type": "Firebase",
                "database_url": self.database_url,
                "service_account_path": self.service_account_path,
                "is_connected": False,
                "last_check": datetime.now().isoformat(),
                "config_valid": False,
                "error_details": str(e),
            }

    def test_connection(self) -> bool:
        """Test Firebase connection"""
        try:
            if not self._root_ref:
                return self.initialize()

            self._probe_connection()
            return True
        except Exception as e:
            logger.error(f"Firebase connection test failed: {str(e)}")
            return False

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple Firebase paths asynchronously"""
        output, _failed = await self.get_multiple_data_checked_async(paths)
        return output

    async def get_multiple_data_checked_async(self, paths: List[str]):
        """Read many paths with bounded concurrency. Returns (output, failed_paths).

        Failed reads are {} in ``output``, as before, and also listed in
        ``failed_paths`` so callers can tell them apart from empty nodes.
        """
        output: Dict[str, Dict[str, Any]] = {}
        failed: set = set()
        if not paths:
            return output, failed

        loop = asyncio.get_event_loop()
        # Within the HTTP pool size; the first wave shares one connection probe.
        gate = asyncio.Semaphore(_FIREBASE_PRELOAD_CONCURRENCY)

        async def get_single_path(path):
            async with gate:
                return await loop.run_in_executor(None, self.get_data, path)

        results = await asyncio.gather(
            *(get_single_path(path) for path in paths), return_exceptions=True
        )
        for path, result in zip(paths, results):
            if isinstance(result, BaseException):
                logger.error(f"Error fetching data from {path}: {str(result)}")
                output[path] = {}
                failed.add(path)
            else:
                output[path] = result or {}

        return output, failed

    def get_all_paths(self) -> List[str]:
        """Get all data paths from Firebase by traversing the tree"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                return []

            # Shallow list of top-level keys only (avoid full-tree download in 24/7).
            top = self._root_ref.get(shallow=True)
            if not top:
                return []
            if isinstance(top, dict):
                return sorted(str(k) for k in top.keys())
            return []

        except Exception as e:
            logger.error(
                f"Error getting all paths from Firebase: {str(e)}", exc_info=True
            )
            return []

    def get_migration_paths(self) -> List[str]:
        """Return app-level paths for migration (one extra level, not per-alert keys)."""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                return []
            top = self._root_ref.get(shallow=True)
            if not isinstance(top, dict) or not top:
                return []
            paths: List[str] = []
            for key in top.keys():
                key_s = str(key)
                try:
                    node = self._root_ref.child(key_s).get()
                except Exception as child_err:
                    logger.error(
                        "Error reading Firebase node %s for migration: %s",
                        key_s,
                        child_err,
                    )
                    paths.append(key_s)
                    continue
                if not isinstance(node, dict) or not node:
                    paths.append(key_s)
                    continue
                if all(isinstance(v, dict) for v in node.values()):
                    for child_key in node.keys():
                        paths.append(f"{key_s}/{child_key}")
                else:
                    paths.append(key_s)
            return sorted(paths)
        except Exception as e:
            logger.error(
                f"Error getting migration paths from Firebase: {str(e)}",
                exc_info=True,
            )
            return self.get_all_paths()

    def _extract_paths(self, data: Any, current_path: str, paths: List[str]) -> None:
        """Recursively extract all paths from nested data"""
        if isinstance(data, dict):
            # Check if this looks like a data node (has non-dict values)
            has_data_values = any(not isinstance(v, dict) for v in data.values())
            if has_data_values and current_path:
                paths.append(current_path)
            # Continue traversing
            for key, value in data.items():
                new_path = f"{current_path}/{key}" if current_path else key
                if isinstance(value, dict):
                    self._extract_paths(value, new_path, paths)
                elif current_path:
                    # Leaf node with a parent path
                    if current_path not in paths:
                        paths.append(current_path)

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all Firebase data"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                return {}

            # Firebase allows getting the entire tree at once
            snapshot = self._root_ref.get()
            return snapshot or {}

        except Exception as e:
            logger.error(
                f"Error getting snapshot from Firebase: {str(e)}", exc_info=True
            )
            self.snapshot_error = str(e)
            return {}

    def get_alert_storage_limited(self, max_alerts: int) -> Dict[str, Any]:
        """Fetch up to max_alerts completed alerts without downloading full AlertStorage."""
        if not self._initialized:
            self.initialize()
        if self._root_ref is None or max_alerts <= 0:
            return {}

        self._ensure_connection_tested()
        storage_ref = self._root_ref.child("Alerts/AlertStorage")
        try:
            result = (
                storage_ref.order_by_child("timestamp")
                .limit_to_last(int(max_alerts))
                .get()
            )
            if isinstance(result, dict) and result:
                logger.debug(
                    "Firebase AlertStorage limit_to_last fetched %d alert(s)",
                    len(result),
                )
                return result
        except Exception as e:
            logger.debug(
                "Firebase limit_to_last on AlertStorage unavailable, using full read: %s",
                e,
            )

        return self.get_data("Alerts/AlertStorage") or {}

    def _alert_storage_ref(self):
        if not self._initialized:
            self.initialize()
        if self._root_ref is None:
            return None
        return self._root_ref.child("Alerts/AlertStorage")

    def _firebase_alert_count(self, storage_ref) -> int:
        try:
            shallow = storage_ref.get(shallow=True)
            if isinstance(shallow, dict):
                return len(shallow)
        except TypeError:
            pass
        except Exception as e:
            logger.debug("Firebase shallow AlertStorage count unavailable: %s", e)
        data = self.get_data("Alerts/AlertStorage") or {}
        return len(data) if isinstance(data, dict) else 0

    def fetch_alert_storage(self, max_alerts: Optional[int] = None) -> Dict[str, Any]:
        if max_alerts and max_alerts > 0:
            return self.get_alert_storage_limited(int(max_alerts))
        data = self.get_data("Alerts/AlertStorage") or {}
        return data if isinstance(data, dict) else {}

    def list_alert_storage_meta(self) -> List[tuple]:
        data = self.fetch_alert_storage()
        rows = []
        for alert_id, alert_data in data.items():
            if not isinstance(alert_data, dict):
                continue
            rows.append((str(alert_id), _coerce_alert_timestamp(alert_data.get("timestamp"))))
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows

    def query_alert_storage_page(self, page: int, limit: int) -> Dict[str, Any]:
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 1))
        storage_ref = self._alert_storage_ref()
        if storage_ref is None:
            return {"alerts": [], "total_count": 0}
        total_count = self._firebase_alert_count(storage_ref)
        need = page * limit
        fetched: Dict[str, Any] = {}
        try:
            result = (
                storage_ref.order_by_child("timestamp")
                .limit_to_last(int(need))
                .get()
            )
            if isinstance(result, dict):
                fetched = result
        except Exception as e:
            logger.debug("Firebase alert page query fell back to full read: %s", e)
            fetched = self.fetch_alert_storage()
        alerts = _sorted_alert_records(fetched)
        start = (page - 1) * limit
        return {"alerts": alerts[start : start + limit], "total_count": total_count}

    def delete_paths(self, paths: List[str]) -> bool:
        if not paths:
            return True
        if not self._initialized:
            self.initialize()
        if self._root_ref is None:
            return False
        try:
            for offset in range(0, len(paths), 100):
                updates = {}
                for path in paths[offset : offset + 100]:
                    updates[_strip_db_path(path)] = None
                self._root_ref.update(updates)
            return True
        except Exception as e:
            logger.error("Error batch-deleting Firebase paths: %s", e, exc_info=True)
            return False


class MongoDatabase(DatabaseInterface):
    """MongoDB database implementation"""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.connection_string = config.mongodb_connection_string
        self.database_name = config.mongodb_database_name
        self.streamer_name = config.streamer_name
        self._client = None
        self._database = None
        self._collection = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize MongoDB connection"""
        if not _ensure_pymongo():
            logger.error("MongoDB driver not available. Install pymongo package.")
            self.last_error = "The MongoDB driver (pymongo) isn't installed."
            return False

        try:
            # Connect to MongoDB
            self._client = MongoClient(
                self.connection_string,
                serverSelectionTimeoutMS=self.config.connection_timeout * 1000,
            )

            # Test the connection
            self._client.admin.command("ping")

            # Get database and collection
            self._database = self._client[self.database_name]
            self._collection = self._database[f"{self.streamer_name}_data"]

            # Create indexes for better performance
            self._collection.create_index([("data_path", 1)], unique=True)
            try:
                self._collection.create_index(
                    [("data.timestamp", -1)],
                    name="idx_data_timestamp",
                )
            except Exception as index_error:
                logger.debug("MongoDB alert timestamp index unavailable: %s", index_error)

            self._initialized = True
            logger.info(
                f"Connected to MongoDB at {self.connection_string}, database: {self.database_name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB: {str(e)}", exc_info=True)
            self.last_error = f"Couldn't reach the MongoDB server: {e}"
            return False

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from MongoDB database"""
        if not self._initialized:
            self.initialize()

        if self._collection is None:
            logger.error("MongoDB database not initialized")
            raise DatabaseReadError("MongoDB database not initialized")

        try:
            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Getting data from MongoDB path: {path}")
            document = self._collection.find_one({"data_path": path})

            if document:
                data = document.get("data", {})
                if request_etag:
                    return {"data": data, "etag": document.get("etag")}
                return data
            else:
                if request_etag:
                    return {"data": {}, "etag": None}
                return {}

        except Exception as e:
            logger.error(
                f"Error getting data from MongoDB at {path}: {str(e)}", exc_info=True
            )
            raise DatabaseReadError(str(e)) from e

    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in MongoDB database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._collection is None:
                logger.error("MongoDB database not initialized")
                return False

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            # Create document
            etag = str(hash(json.dumps(data, sort_keys=True, default=str)))
            document = {
                "data_path": path,
                "data": data,
                "etag": etag,
                "updated_at": datetime.now(),
            }

            logger.debug(f"Setting data at MongoDB path: {path}")
            self._collection.replace_one({"data_path": path}, document, upsert=True)
            logger.debug(f"Successfully set data at MongoDB path: {path}")
            return True

        except Exception as e:
            logger.error(
                f"Error setting data in MongoDB at {path}: {str(e)}", exc_info=True
            )
            return False

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in MongoDB database"""
        if not self._initialized:
            self.initialize()

        try:
            existing_data = self.get_data(path)

            if existing_data:
                existing_data.update(data)
                updated_data = existing_data
            else:
                updated_data = data

            return self.set_data(path, updated_data)

        except DatabaseReadError as e:
            logger.error(
                f"Skipping MongoDB update at {path}; read failed: {e}", exc_info=True
            )
            return False
        except Exception as e:
            logger.error(
                f"Error updating data in MongoDB at {path}: {str(e)}", exc_info=True
            )
            return False

    def delete_data(self, path: str) -> bool:
        """Delete data from MongoDB database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._collection is None:
                logger.error("MongoDB database not initialized")
                return False

            # Remove leading slash if present
            if path.startswith("/"):
                path = path[1:]

            logger.debug(f"Deleting data at MongoDB path: {path}")
            result = self._collection.delete_one({"data_path": path})

            if result.deleted_count > 0:
                logger.debug(f"Successfully deleted data at MongoDB path: {path}")
                return True
            else:
                logger.warning(f"No document found to delete at MongoDB path: {path}")
                return True  # Consider it successful if nothing to delete

        except Exception as e:
            logger.error(
                f"Error deleting data from MongoDB at {path}: {str(e)}", exc_info=True
            )
            return False

    def get_connection_status(self) -> Dict[str, Any]:
        """Get MongoDB connection status"""
        try:
            if self._client:
                # Test the connection
                self._client.admin.command("ping")

                return {
                    "status": "Connected",
                    "database_type": "MongoDB",
                    "connection_string": self.connection_string,
                    "database_name": self.database_name,
                    "streamer_name": self.streamer_name,
                    "is_connected": True,
                    "last_check": datetime.now().isoformat(),
                }
            else:
                return {
                    "status": "Not Initialized",
                    "database_type": "MongoDB",
                    "connection_string": self.connection_string,
                    "is_connected": False,
                    "last_check": datetime.now().isoformat(),
                }
        except Exception as e:
            return {
                "status": f"Error: {str(e)}",
                "database_type": "MongoDB",
                "connection_string": self.connection_string,
                "is_connected": False,
                "last_check": datetime.now().isoformat(),
            }

    def test_connection(self) -> bool:
        """Test MongoDB connection"""
        try:
            if not self._client:
                return self.initialize()

            self._client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"MongoDB connection test failed: {str(e)}")
            return False

    def _get_multiple_data(self, paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Load many paths with one $in query per chunk. Missing paths are {}."""
        return self._get_multiple_data_checked(paths)[0]

    def _get_multiple_data_checked(self, paths: List[str]):
        """Like _get_multiple_data, plus the set of paths whose read failed."""
        output: Dict[str, Dict[str, Any]] = {path: {} for path in paths}
        if not paths:
            return output, set()
        if not self._initialized:
            self.initialize()
        if self._collection is None:
            return output, set(paths)
        try:
            normalized = [(original, _strip_db_path(original)) for original in paths]
            unique_norms = list(dict.fromkeys(norm for _, norm in normalized))
            found: Dict[str, Any] = {}
            for offset in range(0, len(unique_norms), _SQL_IN_CHUNK):
                chunk = unique_norms[offset : offset + _SQL_IN_CHUNK]
                for doc in self._collection.find({"data_path": {"$in": chunk}}):
                    found[doc.get("data_path", "")] = doc.get("data", {}) or {}
            for original, norm in normalized:
                output[original] = found.get(norm) or {}
            return output, set()
        except Exception as e:
            logger.error(f"Error fetching multiple MongoDB paths: {e}", exc_info=True)
            return output, set(paths)

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple MongoDB paths asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_multiple_data, paths)

    async def get_multiple_data_checked_async(self, paths: List[str]):
        """Returns (output, failed_paths); failed reads are {} in output."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_multiple_data_checked, paths)

    def get_all_paths(self) -> List[str]:
        """Get all data paths stored in MongoDB"""
        if not self._initialized:
            self.initialize()

        try:
            if self._collection is None:
                return []

            # Query all documents and get their paths
            paths = []
            for doc in self._collection.find({}, {"data_path": 1}):
                if "data_path" in doc:
                    paths.append(doc["data_path"])

            return sorted(paths)

        except Exception as e:
            logger.error(
                f"Error getting all paths from MongoDB: {str(e)}", exc_info=True
            )
            return []

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all MongoDB data as a nested dictionary"""
        if not self._initialized:
            self.initialize()

        try:
            if self._collection is None:
                return {}

            snapshot = {}
            for doc in self._collection.find({}):
                path = doc.get("data_path", "")
                if not path:
                    continue
                data = doc.get("data", {})
                self._set_nested_value(snapshot, path, data)

            return snapshot

        except Exception as e:
            logger.error(
                f"Error getting snapshot from MongoDB: {str(e)}", exc_info=True
            )
            self.snapshot_error = str(e)
            return {}

    def _set_nested_value(self, d: Dict[str, Any], path: str, value: Any) -> None:
        """Set a value in a nested dictionary using a path string"""
        keys = path.split("/")
        current = d
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

    def _collection_map(self, document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Treat a parent Alerts/AlertStorage document as id -> alert, if it is one."""
        if not document:
            return {}
        data = document.get("data")
        if not isinstance(data, dict):
            return {}
        blob = {}
        for key, value in data.items():
            if isinstance(value, dict):
                blob[str(key)] = value
        return blob

    def _iter_alert_child_docs(self, *, max_alerts: Optional[int] = None, projection=None):
        prefix = _alert_storage_prefix_query()
        cursor = self._collection.find(prefix, projection)
        if max_alerts and max_alerts > 0:
            cursor = cursor.sort([("data.timestamp", -1)]).limit(int(max_alerts))
        return cursor

    def fetch_alert_storage(self, max_alerts: Optional[int] = None) -> Dict[str, Any]:
        if not self._initialized:
            self.initialize()
        if self._collection is None:
            return {}
        exact = self._collection.find_one({"data_path": "Alerts/AlertStorage"})
        blob = self._collection_map(exact)
        if blob:
            storage = dict(blob)
            for doc in self._collection.find(_alert_storage_prefix_query()):
                path = str(doc.get("data_path") or "")
                data = doc.get("data")
                if isinstance(data, dict):
                    storage[path.rsplit("/", 1)[-1]] = data
            if max_alerts and max_alerts > 0 and len(storage) > max_alerts:
                ordered = _sorted_alert_records(storage)[: int(max_alerts)]
                return {
                    item["alert_id"]: {k: v for k, v in item.items() if k != "alert_id"}
                    for item in ordered
                }
            return storage
        storage = {}
        for doc in self._iter_alert_child_docs(max_alerts=max_alerts):
            data = doc.get("data")
            if not isinstance(data, dict):
                continue
            alert_id = str(doc.get("data_path") or "").rsplit("/", 1)[-1]
            storage[alert_id] = data
        return storage

    def list_alert_storage_meta(self) -> List[tuple]:
        if not self._initialized:
            self.initialize()
        if self._collection is None:
            return []
        rows = []
        exact = self._collection.find_one({"data_path": "Alerts/AlertStorage"})
        for alert_id, alert_data in self._collection_map(exact).items():
            rows.append(
                (alert_id, _coerce_alert_timestamp(alert_data.get("timestamp")))
            )
        for doc in self._collection.find(
            _alert_storage_prefix_query(),
            {"data_path": 1, "data.timestamp": 1},
        ):
            alert_id = str(doc.get("data_path") or "").rsplit("/", 1)[-1]
            timestamp = None
            data = doc.get("data")
            if isinstance(data, dict):
                timestamp = data.get("timestamp")
            rows.append((alert_id, _coerce_alert_timestamp(timestamp)))
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows

    def query_alert_storage_page(self, page: int, limit: int) -> Dict[str, Any]:
        if not self._initialized:
            self.initialize()
        if self._collection is None:
            return {"alerts": [], "total_count": 0}
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 1))
        exact = self._collection.find_one({"data_path": "Alerts/AlertStorage"})
        blob = self._collection_map(exact)
        prefix = _alert_storage_prefix_query()
        if blob:
            merged = self.fetch_alert_storage()
            alerts = _sorted_alert_records(merged)
            total_count = len(alerts)
            start = (page - 1) * limit
            return {"alerts": alerts[start : start + limit], "total_count": total_count}
        total_count = int(self._collection.count_documents(prefix))
        skip = (page - 1) * limit
        alerts = []
        cursor = (
            self._collection.find(prefix)
            .sort([("data.timestamp", -1)])
            .skip(skip)
            .limit(limit)
        )
        for doc in cursor:
            data = doc.get("data")
            if not isinstance(data, dict):
                data = {}
            item = dict(data)
            item["alert_id"] = str(doc.get("data_path") or "").rsplit("/", 1)[-1]
            alerts.append(item)
        alerts.sort(
            key=lambda item: _coerce_alert_timestamp(item.get("timestamp")),
            reverse=True,
        )
        return {"alerts": alerts, "total_count": total_count}

    def delete_paths(self, paths: List[str]) -> bool:
        if not paths:
            return True
        if not self._initialized:
            self.initialize()
        if self._collection is None:
            return False
        try:
            norms = [_strip_db_path(path) for path in paths]
            for offset in range(0, len(norms), _SQL_IN_CHUNK):
                chunk = norms[offset : offset + _SQL_IN_CHUNK]
                self._collection.delete_many({"data_path": {"$in": chunk}})
            return True
        except Exception as e:
            logger.error("Error batch-deleting MongoDB paths: %s", e, exc_info=True)
            return False


class DatabaseManager:
    """Main database manager that handles all database operations"""

    def __init__(self):
        self._config = DatabaseConfig()
        self._database: Optional[DatabaseInterface] = None
        self._lock = threading.RLock()
        self._initialized = False
        self._write_gen_lock = threading.Lock()
        self._write_generation: Dict[str, int] = {}
        self._path_write_locks: Dict[str, threading.Lock] = {}
        self._listener_lock = threading.Lock()
        self._write_listeners: List[Any] = []
        # One "can't connect" notification per outage; reset on a successful connect.
        self._down_notified = False
        self.last_init_error = ""
        self._last_failure_toast: Dict[str, float] = {}
        # Outcome of the last migrate_data() call, for the Database tab to report.
        self.last_migration_result: Dict[str, Any] = {}

    def add_write_listener(self, callback) -> None:
        """Call ``callback(path, done)`` around every write.

        ``done`` is False before the write starts and True once the backend call
        has returned (even when the caller already gave up on a timeout). The
        path "" means the whole database may have changed (backend switch or
        migration). Bound methods are held weakly. Callbacks must be quick and
        must not take locks that are held across database calls.
        """
        if hasattr(callback, "__self__"):
            ref = weakref.WeakMethod(callback)
        else:
            ref = lambda cb=callback: cb  # noqa: E731
        with self._listener_lock:
            self._write_listeners.append(ref)

    def _notify_write(self, path: str, done: bool) -> None:
        with self._listener_lock:
            refs = list(self._write_listeners)
        if not refs:
            return
        path = _strip_db_path(path or "")
        alive = 0
        for ref in refs:
            callback = ref()
            if callback is None:
                continue
            alive += 1
            try:
                callback(path, done)
            except Exception as e:
                logger.debug("Database write listener failed for %s: %s", path, e)
        if alive != len(refs):
            with self._listener_lock:
                self._write_listeners = [
                    ref for ref in self._write_listeners if ref() is not None
                ]

    def initialize(self, config: Optional[DatabaseConfig] = None) -> bool:
        """Initialize the database manager with the specified configuration.

        A failure notifies once per outage (callers that report it themselves wrap
        the call in :func:`quiet_notifications`); the next success after a reported
        outage says the connection is back.
        """
        self._notify_write("", False)
        try:
            ok = self._initialize(config)
        finally:
            self._notify_write("", True)
        if ok:
            self.last_init_error = ""
            if self._down_notified:
                self._down_notified = False
                if not _quiet():
                    notify(
                        "",
                        type="success",
                        source="database",
                        title=f"Reconnected to the {_db_label(self._config.database_type)} database",
                        dedupe_key="db:reconnected",
                    )
        elif not _quiet():
            self._notify_down()
        return ok

    def _notify_down(self) -> None:
        """Say once that the database can't be reached (until it connects again)."""
        if self._down_notified:
            return
        self._down_notified = True
        label = _db_label(self._config.database_type)
        notify_critical(
            "Changes won't be saved until the connection is back.",
            source="database",
            title=f"Can't connect to the {label} database",
            hint="Check the connection details in Settings → Database and press Test.",
            details=self.last_init_error or None,
            dedupe_key="db:down",
            dedupe_cooldown_sec=1800,
            actions=nav_actions_settings("Database"),
        )

    def _report_write_failure(self, op_name: str, path: str) -> None:
        """Notify (deduped, popup at most every few minutes) that a write didn't land.

        Skipped for writes superseded by a newer write to the same path, and for
        callers inside :func:`quiet_notifications` (they report with more context).
        """
        if _quiet():
            return
        status, reason = last_write_problem() or ("failed", "")
        if status in ("ok", "superseded"):
            return
        if status == "down" or not self._initialized:
            self._notify_down()
            return
        label = _db_label(self._config.database_type)
        target = path or "(root)"
        if status == "timeout":
            key = "db:write_timeout"
            kwargs = dict(
                type="warning",
                title=f"The {label} database is responding slowly",
                hint="Check your internet connection and the settings in Settings → Database.",
                details=f"{op_name} {target}: {reason}",
            )
            message = "A save took too long, so recent changes may not have been kept."
        else:
            key = "db:write_failed"
            kwargs = dict(
                type="error",
                title=f"Couldn't save to the {label} database",
                hint="Check the connection in Settings → Database, then try again.",
                details=f"{op_name} {target}: {reason or 'the database rejected the write (see the log)'}",
            )
            message = "Recent changes may not have been kept."
        now = time.monotonic()
        last = self._last_failure_toast.get(key, 0.0)
        quiet_popup = bool(last) and now - last < _WRITE_FAILURE_POPUP_GAP_SEC
        if not quiet_popup:
            self._last_failure_toast[key] = now
        notify(
            message,
            source="database",
            dedupe_key=key,
            dedupe_cooldown_sec=600,
            skip_toast=quiet_popup,
            actions=nav_actions_settings("Database"),
            **kwargs,
        )

    def _initialize(self, config: Optional[DatabaseConfig] = None) -> bool:
        with self._lock:
            if config:
                self._config = config

            # Create the appropriate database implementation
            if self._config.database_type == "sql":
                self._database = SQLDatabase(self._config)
            elif self._config.database_type == "firebase":
                self._database = FirebaseDatabase(self._config)
            elif self._config.database_type == "mongodb":
                self._database = MongoDatabase(self._config)
            else:
                logger.error(f"Unsupported database type: {self._config.database_type}")
                self.last_init_error = (
                    f"config.json has an unknown database type '{self._config.database_type}'. "
                    "Choose SQL, Firebase or MongoDB in Settings → Database."
                )
                return False

            # Initialize the database
            if self._database.initialize():
                self._initialized = True
                logger.info(
                    f"Database manager initialized with {self._config.database_type} database"
                )
                return True
            else:
                logger.error(
                    f"Failed to initialize {self._config.database_type} database"
                )
                self.last_init_error = str(getattr(self._database, "last_error", "") or "")
                return False

    def get_config(self) -> DatabaseConfig:
        """Get the current database configuration"""
        return self._config

    def update_config(self, **kwargs) -> bool:
        """Update database configuration and reinitialize if necessary"""
        with self._lock:
            # Log the update attempt (connection strings can hold a password)
            logger.info(
                "Updating database manager config with: %s",
                {
                    k: ("***" if k == "mongodb_connection_string" and v else v)
                    for k, v in kwargs.items()
                },
            )

            # Update configuration
            old_type = self._config.database_type
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    if key == "firebase_database_url" and isinstance(value, str):
                        value = normalize_firebase_database_url(value)
                    old_value = getattr(self._config, key)
                    setattr(self._config, key, value)
                    if key != "mongodb_connection_string":
                        logger.debug(f"Updated config {key}: {old_value} -> {value}")
                else:
                    logger.warning(f"Unknown config key: {key}")

            # Reinitialize only when the live backend type actually changes
            if "database_type" in kwargs and kwargs["database_type"] != old_type:
                logger.info(
                    f"Database type changed to {kwargs['database_type']}, reinitializing..."
                )
                self._initialized = False
                return self.initialize()

            return True

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from the database"""
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            if request_etag:
                return {"data": {}, "etag": None}
            return {}

        default = {"data": {}, "etag": None} if request_etag else {}
        return self._run_db_op_with_timeout(
            "Get",
            lambda: self._database.get_data(path, request_etag),
            default,
            path=path,
        )

    def get_data_checked(self, path: str):
        """Like get_data, plus whether the read succeeded: ``(data, ok)``.

        A failed or timed-out read returns ``({}, False)``; get_data returns
        ``{}`` for those too, which looks the same as an empty path. Callers
        that later write the whole path back use this to avoid replacing good
        remote data with defaults.
        """
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            return {}, False

        outcome = self._run_db_op_with_timeout(
            "Get",
            lambda: (self._database.get_data(path), True),
            None,
            path=path,
        )
        if outcome is None:
            return {}, False
        return outcome

    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in the database with a hard timeout.

        The write runs on a short-lived worker thread so a hung backend write
        (e.g. a stalled Firebase request on a flaky network) can never block the
        calling thread indefinitely. Combined with state writes no longer holding
        the global StateManager lock, this prevents a single slow write from
        freezing the web engine / Twitch threads.
        """
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            _write_tls.last_write = ("down", "")
            self._report_write_failure("Write", path)
            return False

        ok = bool(
            self._run_db_op_with_timeout(
                "Write",
                lambda: self._database.set_data(path, data),
                False,
                path=path,
                invalidate_on_timeout=True,
            )
        )
        if not ok:
            self._report_write_failure("Write", path)
        return ok

    def _path_write_lock(self, path: str) -> threading.Lock:
        with self._write_gen_lock:
            lock = self._path_write_locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._path_write_locks[path] = lock
            return lock

    def _run_db_op_with_timeout(
        self,
        op_name: str,
        fn,
        default,
        *,
        path: str = "",
        invalidate_on_timeout: bool = False,
    ):
        timeout = max(5, int(getattr(self._config, "connection_timeout", 30) or 30))
        result: Dict[str, Any] = {"value": default, "status": "ok", "reason": ""}
        my_gen = 0
        path_lock = None

        if invalidate_on_timeout:
            with self._write_gen_lock:
                self._write_generation[path] = self._write_generation.get(path, 0) + 1
                my_gen = self._write_generation[path]
            path_lock = self._path_write_lock(path)
            self._notify_write(path, False)

        def _worker() -> None:
            try:
                if path_lock is not None:
                    with path_lock:
                        with self._write_gen_lock:
                            if self._write_generation.get(path) != my_gen:
                                result["status"] = "superseded"
                                return
                        value = fn()
                        with self._write_gen_lock:
                            if self._write_generation.get(path) != my_gen:
                                result["status"] = "superseded"
                                return
                        result["value"] = value
                else:
                    result["value"] = fn()
            except Exception as e:
                logger.error(
                    "Database %s worker failed for %s: %s",
                    op_name.lower(),
                    path,
                    e,
                    exc_info=True,
                )
                result["value"] = default
                result["status"] = "failed"
                result["reason"] = str(e)
            finally:
                if invalidate_on_timeout:
                    self._notify_write(path, True)

        is_sql = str(getattr(self._config, "database_type", "sql") or "sql") == "sql"
        if is_sql:
            _worker()
            if invalidate_on_timeout:
                _write_tls.last_write = _write_outcome(result)
            return result["value"]

        worker = threading.Thread(
            target=_worker, name=f"MycelianDB{op_name}", daemon=True
        )
        worker.start()
        if _caller_is_overlay_hub():
            try:
                from gevent import sleep as _hub_sleep
            except ImportError:
                _hub_sleep = time.sleep
            deadline = time.monotonic() + float(timeout)
            while worker.is_alive() and time.monotonic() < deadline:
                _hub_sleep(0.01)
        else:
            worker.join(timeout)

        if worker.is_alive():
            if invalidate_on_timeout:
                with self._write_gen_lock:
                    if self._write_generation.get(path) == my_gen:
                        self._write_generation[path] = my_gen + 1
            logger.error(
                "Database %s to %s timed out after %ss; continuing without blocking",
                op_name.lower(),
                path or "(root)",
                timeout,
            )
            if invalidate_on_timeout:
                _write_tls.last_write = ("timeout", f"no answer after {timeout} s")
            return default

        if invalidate_on_timeout:
            _write_tls.last_write = _write_outcome(result)
        return result["value"]

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in the database"""
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            _write_tls.last_write = ("down", "")
            self._report_write_failure("Update", path)
            return False

        ok = bool(
            self._run_db_op_with_timeout(
                "Update",
                lambda: self._database.update_data(path, data),
                False,
                path=path,
                invalidate_on_timeout=True,
            )
        )
        if not ok:
            self._report_write_failure("Update", path)
        return ok

    def delete_data(self, path: str) -> bool:
        """Delete data from the database"""
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            _write_tls.last_write = ("down", "")
            self._report_write_failure("Delete", path)
            return False

        ok = bool(
            self._run_db_op_with_timeout(
                "Delete",
                lambda: self._database.delete_data(path),
                False,
                path=path,
                invalidate_on_timeout=True,
            )
        )
        if not ok:
            self._report_write_failure("Delete", path)
        return ok

    def _alert_backend(self):
        """Active backend once the app database is already open. Does not connect."""
        if not self._initialized or self._database is None:
            return None
        return self._database

    def _run_alert_storage_op(self, op_name: str, method_name: str, *args):
        """Run a stored-alert backend call under the usual timeout / hub-safe wait.

        Backend exceptions reach the caller unchanged, as when it was called
        directly. A timeout returns None, which callers treat as "unavailable".
        """
        backend = self._alert_backend()
        if backend is None or not hasattr(backend, method_name):
            return None
        method = getattr(backend, method_name)

        def _call():
            try:
                return True, method(*args)
            except Exception as e:
                return False, e

        outcome = self._run_db_op_with_timeout(
            op_name, _call, None, path="Alerts/AlertStorage"
        )
        if outcome is None:
            return None
        ok, value = outcome
        if not ok:
            raise value
        return value

    def fetch_alert_storage(self, max_alerts: Optional[int] = None):
        return self._run_alert_storage_op(
            "AlertStorage", "fetch_alert_storage", max_alerts
        )

    def list_alert_storage_meta(self):
        return self._run_alert_storage_op("AlertStorageMeta", "list_alert_storage_meta")

    def query_alert_storage_page(self, page: int, limit: int):
        return self._run_alert_storage_op(
            "AlertStoragePage", "query_alert_storage_page", page, limit
        )

    def delete_paths(self, paths: List[str]) -> bool:
        """Batch-delete paths on the active backend."""
        if not paths:
            return True
        backend = self._alert_backend()
        if backend is not None and hasattr(backend, "delete_paths"):
            for path in paths:
                self._notify_write(path, False)
            try:
                return bool(backend.delete_paths(paths))
            finally:
                for path in paths:
                    self._notify_write(path, True)
        ok = True
        for path in paths:
            if not self.delete_data(path):
                ok = False
        return ok

    def get_connection_status(self) -> Dict[str, Any]:
        """Get the current database connection status"""
        if self._database:
            return self._database.get_connection_status()
        else:
            return {
                "status": "Not Initialized",
                "database_type": self._config.database_type,
                "is_connected": False,
                "last_check": datetime.now().isoformat(),
            }

    def test_connection(self) -> bool:
        """Test the database connection"""
        if self._database:
            return self._database.test_connection()
        else:
            return False

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple paths asynchronously"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return await self._database.get_multiple_data_async(paths)
        else:
            logger.error("Database not initialized")
            return {path: {} for path in paths}

    async def get_multiple_data_checked_async(self, paths: List[str]):
        """Like get_multiple_data_async, plus the set of paths whose read failed."""
        if not self._initialized:
            self.initialize()

        if self._database and hasattr(self._database, "get_multiple_data_checked_async"):
            return await self._database.get_multiple_data_checked_async(paths)
        if self._database:
            return await self._database.get_multiple_data_async(paths), set()
        logger.error("Database not initialized")
        return {path: {} for path in paths}, set(paths)

    def get_available_databases(self) -> List[str]:
        """Get list of available database types"""
        available = ["sql"]  # SQLite is always available

        if _ensure_firebase():
            available.append("firebase")

        if _ensure_pymongo():
            available.append("mongodb")

        return available

    def get_all_paths(self) -> List[str]:
        """Get all data paths stored in the database"""
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            return []
        return list(
            self._run_db_op_with_timeout(
                "ListPaths",
                self._database.get_all_paths,
                [],
                path="*",
            )
            or []
        )

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all database data as a nested dictionary"""
        if not self._initialized:
            self.initialize()

        if not self._database:
            logger.error("Database not initialized")
            return {}
        return self._run_db_op_with_timeout(
            "Snapshot",
            self._database.get_snapshot,
            {},
            path="*",
        ) or {}

    def get_snapshot_checked(self) -> Tuple[Dict[str, Any], str]:
        """Like get_snapshot, plus why it failed: ``(snapshot, error)``.

        ``error`` is "" when the read worked, even if the database is empty.
        """
        if not self._initialized:
            with quiet_notifications():
                self.initialize()
        db = self._database
        if not db or not self._initialized:
            return {}, self.last_init_error or "The database isn't connected."
        db.snapshot_error = ""
        failed = object()
        out = self._run_db_op_with_timeout("Snapshot", db.get_snapshot, failed, path="*")
        if out is failed:
            return {}, "The database didn't answer in time, or the read failed (see the log)."
        if db.snapshot_error:
            return {}, db.snapshot_error
        return out or {}, ""

    def get_paths_from_snapshot(self, snapshot: Dict[str, Any]) -> List[str]:
        """Derive path list from an in-memory snapshot (avoids a second full-tree read)."""
        if not snapshot:
            return []
        if isinstance(self._database, FirebaseDatabase):
            paths: List[str] = []
            self._database._extract_paths(snapshot, "", paths)
            return sorted(paths)
        return self.get_all_paths()

    def get_alert_storage_limited(self, max_alerts: int) -> Dict[str, Any]:
        """Limited completed-alert fetch when the backend supports it."""
        if not self._initialized:
            self.initialize()
        if self._database and hasattr(self._database, "get_alert_storage_limited"):
            return (
                self._run_alert_storage_op(
                    "AlertStorage", "get_alert_storage_limited", max_alerts
                )
                or {}
            )
        return self.get_data("Alerts/AlertStorage") or {}

    def migrate_data(
        self, source_config: DatabaseConfig, target_config: DatabaseConfig
    ) -> bool:
        """Migrate data from one database to another"""
        # The target may be the live database; its writes bypass set_data.
        # The caller (Database tab) reports the outcome from last_migration_result.
        self.last_migration_result = {}
        self._notify_write("", False)
        try:
            return self._migrate_data(source_config, target_config)
        finally:
            self._notify_write("", True)

    def _migrate_data(
        self, source_config: DatabaseConfig, target_config: DatabaseConfig
    ) -> bool:
        try:
            # Create source and target database instances
            if source_config.database_type == "sql":
                source_db = SQLDatabase(source_config)
            elif source_config.database_type == "firebase":
                source_db = FirebaseDatabase(source_config)
            elif source_config.database_type == "mongodb":
                source_db = MongoDatabase(source_config)
            else:
                logger.error(
                    f"Unsupported source database type: {source_config.database_type}"
                )
                self.last_migration_result = {
                    "ok": False,
                    "error": f"Unknown source database type '{source_config.database_type}'.",
                }
                return False

            if target_config.database_type == "sql":
                target_db = SQLDatabase(target_config)
            elif target_config.database_type == "firebase":
                target_db = FirebaseDatabase(target_config)
            elif target_config.database_type == "mongodb":
                target_db = MongoDatabase(target_config)
            else:
                logger.error(
                    f"Unsupported target database type: {target_config.database_type}"
                )
                self.last_migration_result = {
                    "ok": False,
                    "error": f"Unknown target database type '{target_config.database_type}'.",
                }
                return False

            # Initialize both databases
            if not source_db.initialize():
                logger.error("Failed to initialize source database")
                self.last_migration_result = {
                    "ok": False,
                    "stage": "source",
                    "error": source_db.last_error or "Couldn't open the source database.",
                }
                return False

            if not target_db.initialize():
                logger.error("Failed to initialize target database")
                self.last_migration_result = {
                    "ok": False,
                    "stage": "target",
                    "error": target_db.last_error or "Couldn't open the target database.",
                }
                return False

            if hasattr(source_db, "get_migration_paths"):
                paths_to_migrate = source_db.get_migration_paths()
            else:
                paths_to_migrate = source_db.get_all_paths()
            logger.info(
                f"Migrating {len(paths_to_migrate)} paths from "
                f"{source_config.database_type} to {target_config.database_type}"
            )

            migrated_count = 0
            failed_count = 0
            failed_paths: List[str] = []
            for path in paths_to_migrate:
                try:
                    data = source_db.get_data(path)
                    if target_db.set_data(path, data):
                        migrated_count += 1
                        logger.debug(f"Migrated data from path: {path}")
                    else:
                        failed_count += 1
                        failed_paths.append(path)
                        logger.warning(f"Failed to migrate data from path: {path}")
                except Exception as e:
                    failed_count += 1
                    failed_paths.append(f"{path} ({e})")
                    logger.error(f"Error migrating path {path}: {str(e)}")
            self.last_migration_result = {
                "ok": failed_count == 0,
                "total": len(paths_to_migrate),
                "migrated": migrated_count,
                "failed": failed_count,
                "failed_paths": failed_paths,
            }

            logger.info(
                f"Migration completed. Wrote {migrated_count} paths, "
                f"{failed_count} failures ({source_config.database_type} -> "
                f"{target_config.database_type})"
            )
            if failed_count == 0:
                try:
                    settings = target_db.get_data("DatabaseSettings") or {}
                    if isinstance(settings, dict):
                        settings = dict(settings)
                        settings["database_type"] = target_config.database_type
                        target_db.set_data("DatabaseSettings", settings)
                except Exception as persist_err:
                    logger.warning(
                        "Could not persist target database_type after migration: %s",
                        persist_err,
                    )
            return failed_count == 0

        except Exception as e:
            logger.error(f"Error during data migration: {str(e)}", exc_info=True)
            self.last_migration_result = {"ok": False, "error": str(e)}
            return False


# --- Notification plumbing -------------------------------------------------
# Write outcome of the calling thread's last set/update/delete: (status, reason) with
# status "ok", "failed", "timeout", "superseded" or "down".
_write_tls = threading.local()
_WRITE_FAILURE_POPUP_GAP_SEC = 300.0
_DB_LABELS = {"sql": "local", "firebase": "Firebase", "mongodb": "MongoDB"}


def _db_label(database_type: Optional[str]) -> str:
    return _DB_LABELS.get(str(database_type or "").lower(), str(database_type or "local"))


def _write_outcome(result: Dict[str, Any]) -> Tuple[str, str]:
    """(status, reason) for a finished write; a backend that returned False failed."""
    status = result.get("status") or "ok"
    if status == "ok" and not result.get("value"):
        status = "failed"
    return status, str(result.get("reason") or "")


def _quiet() -> bool:
    return getattr(_write_tls, "quiet", 0) > 0


@contextmanager
def quiet_notifications():
    """Silence this thread's database notifications (connect and write failures).

    For callers that report the failure themselves with better context, such as
    StateManager.save_changes, startup, and the Database tab.
    """
    _write_tls.quiet = getattr(_write_tls, "quiet", 0) + 1
    try:
        yield
    finally:
        _write_tls.quiet -= 1


def last_write_problem() -> Optional[Tuple[str, str]]:
    """(status, reason) of this thread's last write, or None if unknown."""
    return getattr(_write_tls, "last_write", None)


def clear_last_write_problem() -> None:
    _write_tls.last_write = None


def database_label(database_type: Optional[str] = None) -> str:
    """User-facing name of a database type ("local", "Firebase", "MongoDB")."""
    if database_type is None:
        database_type = database_manager.get_config().database_type
    return _db_label(database_type)


# Global database manager instance
database_manager = DatabaseManager()


# Convenience functions for backward compatibility
def get_data(path: str, request_etag: bool = False) -> Dict[str, Any]:
    """Get data from the database"""
    return database_manager.get_data(path, request_etag)


def set_data(path: str, data: Dict[str, Any]) -> bool:
    """Set data in the database"""
    return database_manager.set_data(path, data)


def update_data(path: str, data: Dict[str, Any]) -> bool:
    """Update data in the database"""
    return database_manager.update_data(path, data)


def delete_data(path: str) -> bool:
    """Delete data from the database"""
    return database_manager.delete_data(path)


def get_data_checked(path: str):
    """Get data plus whether the read succeeded: ``(data, ok)``."""
    return database_manager.get_data_checked(path)


async def get_multiple_data_async(paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """Get data from multiple paths asynchronously"""
    return await database_manager.get_multiple_data_async(paths)


# Startup preload bookkeeping: which paths failed to read, plus copies of
# paths other startup code would otherwise read again (SU-06).
_STARTUP_SHARED_PATHS = ("DatabaseSettings",)
_startup_preload_done = threading.Event()
_startup_failed_paths: set = set()
_startup_shared_data: Dict[str, Any] = {}


def startup_read_failed(path: str) -> bool:
    """True when the startup preload could not read ``path``."""
    return path in _startup_failed_paths


def wait_for_startup_preload(path: str, timeout: float) -> Optional[Dict[str, Any]]:
    """Copy of ``path`` as read by the startup preload.

    Returns None when the preload has not finished within ``timeout`` seconds,
    when the read failed, or when the path was not kept.
    """
    if not _startup_preload_done.wait(timeout):
        return None
    if path in _startup_failed_paths or path not in _startup_shared_data:
        return None
    return copy.deepcopy(_startup_shared_data[path])


async def load_all_initial_data() -> Dict[str, Any]:
    """Load all startup data in parallel - optimized approach"""

    # Essential startup paths only (remove services that aren't auto-started)
    startup_paths = [
        "TwitchData",
        "AppSettings",
        "PSNSettings",
        "SpotifyData",
        "YouTubeData",  # Include for tab data loading
        "ChatbotData",  # Chatbot is auto-started
        "DatabaseSettings",
        "OBSData",
        "DiscordData",  # Discord bot auto-connect on startup
        "Statistics",
        # Core alert types (remove extended ranges that aren't needed immediately)
        "Alerts/BitAlerts",
        "Alerts/SubAlerts",
        "Alerts/StreakAlerts",
        "Alerts/FollowAlerts",
        "Alerts/DonationAlerts",
        "Alerts/GiftsubAlerts",
        "Alerts/GiftsubRangeAlerts",
        "Alerts/RaidAlerts",
        "Alerts/RaidRangeAlerts",
        "Alerts/PointAlerts",
        "Alerts/AlertQueue",
        # Skip: BotData/*, ViewerData, AlertStorage, other extended alert ranges
    ]

    logger.info(f"Loading {len(startup_paths)} essential startup data paths...")

    # Load all data in parallel (the original working approach)
    path_to_data, failed_paths = await database_manager.get_multiple_data_checked_async(
        startup_paths
    )
    _startup_failed_paths.clear()
    _startup_failed_paths.update(failed_paths)
    for path in _STARTUP_SHARED_PATHS:
        if path in path_to_data:
            _startup_shared_data[path] = copy.deepcopy(path_to_data[path])
    _startup_preload_done.set()
    if failed_paths:
        logger.warning(
            "Startup could not read %d path(s): %s",
            len(failed_paths),
            sorted(failed_paths),
        )

    # Convert from {path: data} to a flat dict with path keys
    # Include all paths, even if empty, so modules can initialize with defaults
    all_data = {}
    for path in startup_paths:
        data = path_to_data.get(path, {})
        all_data[path] = data  # Always include, even if empty

    loaded_count = len([p for p in startup_paths if path_to_data.get(p)])
    logger.info(
        f"Successfully loaded {loaded_count}/{len(startup_paths)} startup paths"
    )

    return all_data


def initialize_database(config: Optional[DatabaseConfig] = None) -> bool:
    """Initialize the database with the specified configuration"""
    return database_manager.initialize(config)


def get_connection_status() -> Dict[str, Any]:
    """Get the current database connection status"""
    return database_manager.get_connection_status()


def test_connection() -> bool:
    """Test the database connection"""
    return database_manager.test_connection()


def database_needs_remote_monitor() -> bool:
    """True when the active database backend is remote (not local SQLite)."""
    try:
        return database_manager.get_config().database_type in ("firebase", "mongodb")
    except Exception:
        return False


def attempt_auto_reconnect() -> bool:
    """Re-initialize remote database connection when probe fails."""
    try:
        cfg = database_manager.get_config()
    except Exception:
        return False
    if cfg.database_type == "sql":
        return True
    try:
        from .connection_monitor import is_internet_available

        if not is_internet_available():
            return False
    except Exception:
        return False
    if database_manager.test_connection():
        return True
    return bool(database_manager.initialize(cfg))


def get_all_paths() -> List[str]:
    """Get all data paths stored in the database"""
    return database_manager.get_all_paths()


def get_snapshot() -> Dict[str, Any]:
    """Get a complete snapshot of all database data as a nested dictionary"""
    return database_manager.get_snapshot()

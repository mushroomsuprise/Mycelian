#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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
import logging
import sqlite3
import json
import os
import threading
from abc import ABC, abstractmethod
from urllib.parse import urlparse
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime

from .path_utils import get_data_path

# Third-party imports (will be optional based on selected database)
try:
    import firebase_admin
    from firebase_admin import db as firebase_db

    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

try:
    import pymongo
    from pymongo import MongoClient

    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

logger = logging.getLogger(__name__)


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
                    self.db_path, check_same_thread=False
                )
                self._connection.row_factory = sqlite3.Row

                # Create tables
                self._create_tables()
                self._initialized = True

                logger.info(f"SQLite database initialized at {self.db_path}")
                return True

        except Exception as e:
            logger.error(
                f"Failed to initialize SQLite database: {str(e)}", exc_info=True
            )
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

        # Index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_streamer_path 
            ON app_data(streamer_name, data_path)
        """)

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

    def _get_connection(self):
        """Get connection from pool or create new one"""
        with self._pool_lock:
            if self._connection_pool:
                return self._connection_pool.pop()

        # Create new connection
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
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
            if request_etag:
                return {"data": {}, "etag": None}
            return {}
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

            data_json = json.dumps(data, default=str)
            etag = str(hash(data_json))

            cursor.execute(
                """
                INSERT OR REPLACE INTO app_data
                (streamer_name, data_path, data_json, etag, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (self.streamer_name, path, data_json, etag),
            )

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

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in SQLite database"""
        if not self._initialized:
            self.initialize()

        try:
            # Get existing data
            existing_data = self.get_data(path)

            # Merge with new data
            if existing_data:
                existing_data.update(data)
                updated_data = existing_data
            else:
                updated_data = data

            # Set the merged data
            return self.set_data(path, updated_data)

        except Exception as e:
            logger.error(
                f"Error updating data in SQLite at {path}: {str(e)}", exc_info=True
            )
            return False

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

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple paths asynchronously"""
        loop = asyncio.get_event_loop()

        async def get_single_path(path):
            return await loop.run_in_executor(None, self.get_data, path)

        tasks = [get_single_path(path) for path in paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for i, path in enumerate(paths):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(f"Error fetching data from {path}: {str(result)}")
                output[path] = {}
            else:
                output[path] = result or {}

        return output

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

        try:
            snapshot = {}
            paths = self.get_all_paths()

            for path in paths:
                data = self.get_data(path)
                # Include empty dicts so snapshot mirrors all rows (merged shape may
                # still disagree with per-path documents for the explorer).
                self._set_nested_value(snapshot, path, data)

            return snapshot

        except Exception as e:
            logger.error(f"Error getting snapshot from SQLite: {str(e)}", exc_info=True)
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

    def initialize(self) -> bool:
        """Initialize Firebase connection"""
        if not FIREBASE_AVAILABLE:
            logger.error("Firebase SDK not available. Install firebase-admin package.")
            return False

        # Validate required configuration
        if not self.service_account_path:
            logger.error("Firebase service account path is required but not configured")
            return False

        if not self.database_url:
            logger.error("Firebase database URL is required but not configured")
            return False

        try:
            # Check if service account key file exists
            if not os.path.exists(self.service_account_path):
                logger.error(
                    f"Firebase service account key not found at {self.service_account_path}"
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
                        return False
            except Exception as e:
                logger.error(f"Invalid Firebase service account key file: {str(e)}")
                return False

            # Validate database URL format (firebaseio.com or regional firebasedatabase.app)
            if not is_valid_firebase_rtdb_url(self.database_url):
                logger.error(
                    "Invalid Firebase Realtime Database URL. Use https://…firebaseio.com/ "
                    "or https://…firebasedatabase.app/ (trailing slash optional)."
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
                        cred, {"databaseURL": self.database_url}
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
                                cred, {"databaseURL": self.database_url}, name=app_name
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
            return False

    def _ensure_connection_tested(self):
        """Ensure the Firebase connection has been tested before performing operations"""
        if not self._connection_tested:
            try:
                test_ref = firebase_db.reference("/", app=self._app)
                test_ref.get()  # This will throw an exception if not connected
                logger.debug("Firebase connection test passed")
                self._connection_tested = True
            except Exception as test_error:
                logger.error(f"Firebase connection test failed: {str(test_error)}")
                raise test_error

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from Firebase database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                logger.error("Firebase database not initialized")
                if request_etag:
                    return {"data": {}, "etag": None}
                return {}

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
                return {"data": data or {}, "etag": etag}
            else:
                return response or {}

        except Exception as e:
            logger.error(
                f"Error getting data from Firebase at {path}: {str(e)}", exc_info=True
            )
            if request_etag:
                return {"data": {}, "etag": None}
            return {}

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
                # Test the connection with a simple read operation
                # Use a safe path that doesn't contain illegal characters
                test_ref = firebase_db.reference("/", app=self._app)
                test_ref.get()  # This will throw an exception if not connected

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

                if not FIREBASE_AVAILABLE:
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

            # Test by reading connection status
            result = self._root_ref.child(".info/connected").get()
            return bool(result)
        except Exception as e:
            logger.error(f"Firebase connection test failed: {str(e)}")
            return False

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple Firebase paths asynchronously in batches"""

        # Process in smaller batches to avoid overwhelming Firebase connection pool
        batch_size = 3  # Process 3 requests at a time
        output = {}

        for i in range(0, len(paths), batch_size):
            batch_paths = paths[i : i + batch_size]
            logger.debug(
                f"Processing Firebase batch {i//batch_size + 1}: {batch_paths}"
            )

            loop = asyncio.get_event_loop()

            async def get_single_path(path):
                return await loop.run_in_executor(None, self.get_data, path)

            tasks = [get_single_path(path) for path in batch_paths]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, path in enumerate(batch_paths):
                result = results[j]
                if isinstance(result, Exception):
                    logger.error(f"Error fetching data from {path}: {str(result)}")
                    output[path] = {}
                else:
                    output[path] = result or {}

            # Small delay between batches to give connection pool time to recover
            if i + batch_size < len(paths):
                await asyncio.sleep(0.05)  # 50ms delay between batches

        return output

    def get_all_paths(self) -> List[str]:
        """Get all data paths from Firebase by traversing the tree"""
        if not self._initialized:
            self.initialize()

        try:
            if self._root_ref is None:
                return []

            # Get all data at root level
            all_data = self._root_ref.get()
            if not all_data:
                return []

            # Extract all paths from the nested structure
            paths = []
            self._extract_paths(all_data, "", paths)
            return sorted(paths)

        except Exception as e:
            logger.error(
                f"Error getting all paths from Firebase: {str(e)}", exc_info=True
            )
            return []

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
            return {}


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
        if not MONGODB_AVAILABLE:
            logger.error("MongoDB driver not available. Install pymongo package.")
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

            self._initialized = True
            logger.info(
                f"Connected to MongoDB at {self.connection_string}, database: {self.database_name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB: {str(e)}", exc_info=True)
            return False

    def get_data(self, path: str, request_etag: bool = False) -> Dict[str, Any]:
        """Get data from MongoDB database"""
        if not self._initialized:
            self.initialize()

        try:
            if self._collection is None:
                logger.error("MongoDB database not initialized")
                if request_etag:
                    return {"data": {}, "etag": None}
                return {}

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
            if request_etag:
                return {"data": {}, "etag": None}
            return {}

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
            # Get existing data
            existing_data = self.get_data(path)

            # Merge with new data
            if existing_data:
                existing_data.update(data)
                updated_data = existing_data
            else:
                updated_data = data

            # Set the merged data
            return self.set_data(path, updated_data)

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

    async def get_multiple_data_async(
        self, paths: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Get data from multiple MongoDB paths asynchronously"""
        loop = asyncio.get_event_loop()

        async def get_single_path(path):
            return await loop.run_in_executor(None, self.get_data, path)

        tasks = [get_single_path(path) for path in paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for i, path in enumerate(paths):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(f"Error fetching data from {path}: {str(result)}")
                output[path] = {}
            else:
                output[path] = result or {}

        return output

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


class DatabaseManager:
    """Main database manager that handles all database operations"""

    def __init__(self):
        self._config = DatabaseConfig()
        self._database: Optional[DatabaseInterface] = None
        self._lock = threading.RLock()
        self._initialized = False

    def initialize(self, config: Optional[DatabaseConfig] = None) -> bool:
        """Initialize the database manager with the specified configuration"""
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
                return False

    def get_config(self) -> DatabaseConfig:
        """Get the current database configuration"""
        return self._config

    def update_config(self, **kwargs) -> bool:
        """Update database configuration and reinitialize if necessary"""
        with self._lock:
            # Log the update attempt
            logger.info(f"Updating database manager config with: {kwargs}")

            # Update configuration
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    if key == "firebase_database_url" and isinstance(value, str):
                        value = normalize_firebase_database_url(value)
                    old_value = getattr(self._config, key)
                    setattr(self._config, key, value)
                    logger.debug(f"Updated config {key}: {old_value} -> {value}")
                else:
                    logger.warning(f"Unknown config key: {key}")

            # Reinitialize if database type changed
            if "database_type" in kwargs:
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

        if self._database:
            return self._database.get_data(path, request_etag)
        else:
            logger.error("Database not initialized")
            if request_etag:
                return {"data": {}, "etag": None}
            return {}

    def set_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Set data in the database"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return self._database.set_data(path, data)
        else:
            logger.error("Database not initialized")
            return False

    def update_data(self, path: str, data: Dict[str, Any]) -> bool:
        """Update data in the database"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return self._database.update_data(path, data)
        else:
            logger.error("Database not initialized")
            return False

    def delete_data(self, path: str) -> bool:
        """Delete data from the database"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return self._database.delete_data(path)
        else:
            logger.error("Database not initialized")
            return False

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

    def get_available_databases(self) -> List[str]:
        """Get list of available database types"""
        available = ["sql"]  # SQLite is always available

        if FIREBASE_AVAILABLE:
            available.append("firebase")

        if MONGODB_AVAILABLE:
            available.append("mongodb")

        return available

    def get_all_paths(self) -> List[str]:
        """Get all data paths stored in the database"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return self._database.get_all_paths()
        else:
            logger.error("Database not initialized")
            return []

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a complete snapshot of all database data as a nested dictionary"""
        if not self._initialized:
            self.initialize()

        if self._database:
            return self._database.get_snapshot()
        else:
            logger.error("Database not initialized")
            return {}

    def migrate_data(
        self, source_config: DatabaseConfig, target_config: DatabaseConfig
    ) -> bool:
        """Migrate data from one database to another"""
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
                return False

            # Initialize both databases
            if not source_db.initialize():
                logger.error("Failed to initialize source database")
                return False

            if not target_db.initialize():
                logger.error("Failed to initialize target database")
                return False

            paths_to_migrate = source_db.get_all_paths()
            logger.info(
                f"Migrating {len(paths_to_migrate)} paths from "
                f"{source_config.database_type} to {target_config.database_type}"
            )

            migrated_count = 0
            failed_count = 0
            for path in paths_to_migrate:
                try:
                    data = source_db.get_data(path)
                    if target_db.set_data(path, data):
                        migrated_count += 1
                        logger.debug(f"Migrated data from path: {path}")
                    else:
                        failed_count += 1
                        logger.warning(f"Failed to migrate data from path: {path}")
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Error migrating path {path}: {str(e)}")

            logger.info(
                f"Migration completed. Wrote {migrated_count} paths, "
                f"{failed_count} failures ({source_config.database_type} -> "
                f"{target_config.database_type})"
            )
            return failed_count == 0

        except Exception as e:
            logger.error(f"Error during data migration: {str(e)}", exc_info=True)
            return False


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


async def get_multiple_data_async(paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """Get data from multiple paths asynchronously"""
    return await database_manager.get_multiple_data_async(paths)


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
        "Statistics",
        # Core alert types (remove extended ranges that aren't needed immediately)
        "Alerts/BitAlerts",
        "Alerts/SubAlerts",
        "Alerts/StreakAlerts",
        "Alerts/FollowAlerts",
        "Alerts/DonationAlerts",
        "Alerts/PointAlerts",
        "Alerts/AlertQueue",
        # Skip: BotData/*, ViewerData, AlertStorage, extended alert ranges
    ]

    logger.info(f"Loading {len(startup_paths)} essential startup data paths...")

    # Load all data in parallel (the original working approach)
    path_to_data = await get_multiple_data_async(startup_paths)

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


def get_all_paths() -> List[str]:
    """Get all data paths stored in the database"""
    return database_manager.get_all_paths()


def get_snapshot() -> Dict[str, Any]:
    """Get a complete snapshot of all database data as a nested dictionary"""
    return database_manager.get_snapshot()

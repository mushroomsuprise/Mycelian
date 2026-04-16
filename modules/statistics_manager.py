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
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .database_manager import get_data, set_data, update_data
from .path_utils import get_data_path

logger = logging.getLogger(__name__)


@dataclass
class IndividualAlertTypeStatistics:
    """Statistics for individual alert types"""

    alert_type: str = ""
    alert_name: str = ""
    trigger_count: int = 0
    last_triggered: float = field(default_factory=time.time)
    first_triggered: float = field(default_factory=time.time)


@dataclass
class UserAlertStatistics:
    """Per-user statistics for alert events"""

    bit_alerts_played: int = 0
    resubs_played: int = 0
    new_subs_played: int = 0
    gift_subs_played: int = 0
    follow_alerts_played: int = 0
    raids: int = 0
    point_alerts_redeemed: int = 0
    channel_points_redeemed: int = 0  # Total channel points redeemed by this user
    donations: int = 0
    total_alerts: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class AlertStatistics:
    """Statistics for alert events"""

    bit_alerts_played: int = 0
    total_bits: int = 0  # Total amount of bits given across all alerts
    resubs_played: int = 0
    new_subs_played: int = 0
    gift_subs_played: int = 0
    total_gift_subs: int = 0  # Total number of gift subs given across all alerts
    follow_alerts_played: int = 0
    raids: int = 0
    point_alerts_redeemed: int = 0
    total_channel_points_redeemed: int = (
        0  # Total channel points redeemed across all users
    )
    donations: int = 0
    # Per-user tracking
    user_stats: Dict[str, UserAlertStatistics] = field(default_factory=dict)
    # Individual alert type tracking
    alert_type_stats: Dict[str, IndividualAlertTypeStatistics] = field(
        default_factory=dict
    )


@dataclass
class HypeTrainStatistics:
    """Statistics for hype train completions by level"""

    level_completions: Dict[int, int] = field(
        default_factory=dict
    )  # Dynamic dictionary for unlimited levels
    total_completions: int = 0


@dataclass
class UserConnectorStatistics:
    """Per-user statistics for connector system"""

    connectors_triggered: int = 0
    total_triggers: int = 0
    connector_usage: Dict[str, int] = field(
        default_factory=dict
    )  # connector_name -> trigger_count
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class IndividualConnectorStatistics:
    """Statistics for individual connectors"""

    connector_name: str = ""
    trigger_count: int = 0
    last_triggered: float = field(default_factory=time.time)
    first_triggered: float = field(default_factory=time.time)


@dataclass
class ConnectorStatistics:
    """Statistics for connector system"""

    # connectors_created is not stored here since we get it dynamically from connector_manager
    connectors_triggered: int = 0
    total_triggers: int = 0  # Total number of trigger events across all connectors
    # Per-user tracking
    user_stats: Dict[str, UserConnectorStatistics] = field(default_factory=dict)
    # Per-user individual connector tracking
    user_connector_stats: Dict[str, UserConnectorStatistics] = field(
        default_factory=dict
    )
    # Individual connector tracking
    connector_stats: Dict[str, IndividualConnectorStatistics] = field(
        default_factory=dict
    )


@dataclass
class UserChatbotStatistics:
    """Per-user statistics for chatbot system"""

    commands_triggered: int = 0
    events_triggered: int = 0
    total_interactions: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class IndividualCommandStatistics:
    """Statistics for individual commands"""

    command_name: str = ""
    usage_count: int = 0
    last_used: float = field(default_factory=time.time)
    first_used: float = field(default_factory=time.time)


@dataclass
class IndividualEventStatistics:
    """Statistics for individual events"""

    event_name: str = ""
    trigger_count: int = 0
    last_triggered: float = field(default_factory=time.time)
    first_triggered: float = field(default_factory=time.time)


@dataclass
class UserCommandStatistics:
    """Per-user statistics for individual commands"""

    command_usage: Dict[str, int] = field(
        default_factory=dict
    )  # command_name -> usage_count
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class UserEventStatistics:
    """Per-user statistics for individual events"""

    event_usage: Dict[str, int] = field(
        default_factory=dict
    )  # event_name -> trigger_count
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class ChatbotStatistics:
    """Statistics for chatbot system"""

    # commands_created and events_created are not stored here since we get them dynamically
    commands_triggered: int = 0
    events_triggered: int = 0
    total_interactions: int = 0  # Total commands + events triggered
    # Per-user tracking
    user_stats: Dict[str, UserChatbotStatistics] = field(default_factory=dict)
    # Per-user individual item tracking
    user_command_stats: Dict[str, UserCommandStatistics] = field(default_factory=dict)
    user_event_stats: Dict[str, UserEventStatistics] = field(default_factory=dict)
    # Individual command and event tracking
    command_stats: Dict[str, IndividualCommandStatistics] = field(default_factory=dict)
    event_stats: Dict[str, IndividualEventStatistics] = field(default_factory=dict)


@dataclass
class UserQuoteStatistics:
    """Per-user statistics for quote system"""

    total_quotes_redeemed: int = 0
    individual_quote_usage: Dict[str, int] = field(
        default_factory=dict
    )  # quote_id -> usage_count
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class QuoteStatistics:
    """Statistics for quote system"""

    # quotes_created is not stored here since we get it dynamically
    total_quotes_redeemed: int = 0
    individual_quote_usage: Dict[str, int] = field(
        default_factory=dict
    )  # quote_id -> usage_count
    # Per-user tracking
    user_stats: Dict[str, UserQuoteStatistics] = field(default_factory=dict)


@dataclass
class GiveawayStatistics:
    """Statistics for chatbot giveaways"""

    giveaways_completed: int = 0
    total_entry_events: int = 0
    user_wins: Dict[str, int] = field(default_factory=dict)


@dataclass
class UserChatStatistics:
    """Per-user statistics for chat messages"""

    twitch_messages_received: int = 0
    total_messages: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class ChatStatistics:
    """Statistics for chat messages"""

    twitch_messages_received: int = 0
    total_messages: int = 0
    # Per-user tracking
    user_stats: Dict[str, UserChatStatistics] = field(default_factory=dict)


@dataclass
class TemplateStatistics:
    """Statistics for individual templates"""

    template_name: str = ""
    custom_stats: Dict[str, Any] = field(
        default_factory=dict
    )  # Custom stats submitted by templates
    last_updated: float = field(default_factory=time.time)


@dataclass
class SessionStatistics:
    """Statistics for the current session"""

    start_time: float = field(default_factory=time.time)
    last_save_time: float = field(default_factory=time.time)
    session_duration: float = 0.0


@dataclass
class StatisticsData:
    """Main statistics data container"""

    alerts: AlertStatistics = field(default_factory=AlertStatistics)
    hype_trains: HypeTrainStatistics = field(default_factory=HypeTrainStatistics)
    connectors: ConnectorStatistics = field(default_factory=ConnectorStatistics)
    chatbot: ChatbotStatistics = field(default_factory=ChatbotStatistics)
    quotes: QuoteStatistics = field(default_factory=QuoteStatistics)
    giveaways: GiveawayStatistics = field(default_factory=GiveawayStatistics)
    chat: ChatStatistics = field(default_factory=ChatStatistics)
    templates: Dict[str, TemplateStatistics] = field(
        default_factory=dict
    )  # template_name -> TemplateStatistics
    session: SessionStatistics = field(default_factory=SessionStatistics)


class StatisticsManager:
    """Comprehensive statistics manager for Mycelian"""

    _STATS_SYSTEM_USERNAME = "__system__"

    def __init__(self):
        self.data = StatisticsData()
        self.save_interval = 300  # Save every 5 minutes by default
        self.database_path = "statistics"
        self.save_task = None
        self.is_running = False
        self._needs_periodic_start = False  # Flag for deferred periodic saving
        self._periodic_thread = None  # Thread for periodic saving

        # Separate statistics database attributes
        self._stats_db_path: Optional[str] = None
        self._stats_db_lock = threading.RLock()
        self._stats_db_pool: List[sqlite3.Connection] = []
        self._stats_db_pool_size = 3
        self._stats_db_pool_lock = threading.Lock()
        self._stats_db_initialized = False

        # Initialize the separate statistics database
        self._init_statistics_db()

        # Load existing statistics from database
        self._load_from_database()

        logger.info("StatisticsManager initialized")

    # ---- Separate Statistics Database Methods ----

    def _init_statistics_db(self):
        """Initialize the separate statistics SQLite database.

        This database is managed solely by the StatisticsManager and stores
        per-user timestamped events and lifetime totals independently of
        the main application database_manager.
        """
        try:
            db_path = get_data_path(os.path.join("data", "statistics.db"))
            os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

            self._stats_db_path = db_path

            # Create initial connection and tables
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._configure_stats_sqlite_connection(conn)
            self._create_stats_tables(conn)
            self._migrate_legacy_root_statistics_db_if_needed(conn)
            conn.close()

            self._stats_db_initialized = True
            logger.info(f"Statistics database initialized at {db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize statistics database: {e}", exc_info=True)
            self._stats_db_initialized = False

    def _create_stats_tables(self, conn: sqlite3.Connection):
        """Create tables for the separate statistics database."""
        cursor = conn.cursor()

        # Per-user timestamped events table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount REAL DEFAULT 0,
                alert_name TEXT DEFAULT '',
                timestamp REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes for efficient date-range and per-user queries
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ue_username ON user_events(username)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ue_event_type ON user_events(event_type)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ue_timestamp ON user_events(timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ue_user_type_ts "
            "ON user_events(username, event_type, timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ue_type_ts "
            "ON user_events(event_type, timestamp)"
        )

        # Lifetime totals table (replaces database_manager storage)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lifetime_totals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL UNIQUE,
                data_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_name TEXT NOT NULL UNIQUE,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    def _migrate_legacy_root_statistics_db_if_needed(self, conn: sqlite3.Connection) -> None:
        """Copy ``user_events`` from legacy ``<project>/statistics.db`` if ``data/statistics.db`` is empty.

        Some setups only have ``statistics.db`` next to the project root while the app
        reads ``data/statistics.db``. This runs once (recorded in ``migrations``).
        """
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM migrations WHERE migration_name = ?",
                ("legacy_root_statistics_user_events",),
            )
            if cursor.fetchone():
                return
            cursor.execute("SELECT COUNT(*) FROM user_events")
            if int(cursor.fetchone()[0] or 0) > 0:
                return
        except Exception as e:
            logger.debug("Legacy statistics migration pre-check skipped: %s", e)
            return

        legacy_path = get_data_path("statistics.db")
        if not legacy_path or not os.path.isfile(legacy_path):
            return
        if os.path.abspath(legacy_path) == os.path.abspath(self._stats_db_path or ""):
            return

        n_legacy = 0
        try:
            leg = sqlite3.connect(legacy_path, check_same_thread=False)
            try:
                lc = leg.cursor()
                lc.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='user_events'"
                )
                if not lc.fetchone():
                    return
                lc.execute("SELECT COUNT(*) FROM user_events")
                n_legacy = int(lc.fetchone()[0] or 0)
            finally:
                leg.close()
        except Exception as e:
            logger.warning("Could not read legacy statistics DB %s: %s", legacy_path, e)
            return
        if n_legacy == 0:
            return

        try:
            conn.execute("ATTACH DATABASE ? AS legacy", (legacy_path,))
            cursor.execute(
                """
                INSERT INTO main.user_events (username, event_type, amount, alert_name, timestamp)
                SELECT username, event_type, amount, alert_name, timestamp
                FROM legacy.user_events
                """
            )
            conn.execute("DETACH DATABASE legacy")
            cursor.execute(
                "INSERT INTO migrations (migration_name) VALUES (?)",
                ("legacy_root_statistics_user_events",),
            )
            conn.commit()
            logger.info(
                "Imported %s user_events rows from legacy statistics DB %s",
                n_legacy,
                legacy_path,
            )
        except Exception as e:
            logger.warning(
                "Legacy user_events import from %s failed: %s",
                legacy_path,
                e,
                exc_info=True,
            )
            try:
                conn.execute("DETACH DATABASE legacy")
            except sqlite3.Error:
                pass
            try:
                conn.rollback()
            except Exception:
                pass

    @staticmethod
    def _configure_stats_sqlite_connection(conn: sqlite3.Connection) -> None:
        """Apply pragmas for concurrent writes (e.g. per-message chat events)."""
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:
            logger.debug("Could not apply statistics DB pragmas: %s", e)

    def _get_stats_db_connection(self) -> Optional[sqlite3.Connection]:
        """Get a connection from the statistics DB pool or create a new one."""
        if not self._stats_db_initialized or not self._stats_db_path:
            return None
        with self._stats_db_pool_lock:
            if self._stats_db_pool:
                return self._stats_db_pool.pop()
        conn = sqlite3.connect(self._stats_db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        self._configure_stats_sqlite_connection(conn)
        return conn

    def _return_stats_db_connection(self, conn: Optional[sqlite3.Connection]):
        """Return a connection to the statistics DB pool."""
        if conn is None:
            return
        with self._stats_db_pool_lock:
            if len(self._stats_db_pool) < self._stats_db_pool_size:
                self._stats_db_pool.append(conn)
            else:
                conn.close()

    def _close_statistics_db(self):
        """Close all connections in the statistics database pool."""
        with self._stats_db_pool_lock:
            for conn in self._stats_db_pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._stats_db_pool.clear()
        logger.info("Statistics database connections closed")

    def _record_event(
        self,
        username: str,
        event_type: str,
        amount: float = 0,
        alert_name: str = "",
    ):
        """Record a timestamped per-user event in the statistics database.

        Args:
            username: The username associated with the event.
            event_type: Type of event (bit, sub, resub, giftsub, donation, point_redeem, follow,
                raid, connector, chatbot_command, chatbot_event, quote_redeem, chat_message,
                giveaway_entry, giveaway_win, giveaway_draw_complete).
            amount: Numeric amount (bits, gift quantity, points, etc.).
            alert_name: Name of the alert that was triggered.
        """
        if not self._stats_db_initialized or not username:
            return
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, event_type, amount, alert_name, time.time()),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Error recording event: {e}")
        finally:
            self._return_stats_db_connection(conn)

    def _load_lifetime_totals_from_stats_db(self) -> Optional[Dict[str, Any]]:
        """Load lifetime totals from the statistics database.

        Returns:
            Data in the same ``{"data": {...}, "version": "1.0"}`` format used
            by the legacy database_manager path, or ``None`` if no data exists.
        """
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return None
            cursor = conn.cursor()
            cursor.execute("SELECT category, data_json FROM lifetime_totals")
            rows = cursor.fetchall()
            if not rows:
                return None
            data_dict: Dict[str, Any] = {}
            for row in rows:
                data_dict[row[0]] = json.loads(row[1])
            return {"data": data_dict, "version": "1.0"}
        except Exception as e:
            logger.error(f"Error loading from statistics database: {e}", exc_info=True)
            return None
        finally:
            self._return_stats_db_connection(conn)

    def _save_lifetime_totals_to_stats_db(self, data_dict: Dict[str, Any]):
        """Save lifetime totals to the statistics database.

        Args:
            data_dict: Dictionary keyed by category name with JSON-serialisable values.
        """
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return
            cursor = conn.cursor()
            for category, category_data in data_dict.items():
                cursor.execute(
                    "INSERT OR REPLACE INTO lifetime_totals (category, data_json, updated_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (category, json.dumps(category_data)),
                )
            conn.commit()
            logger.debug("Statistics saved to statistics database successfully")
        except Exception as e:
            logger.error(f"Error saving to statistics database: {e}", exc_info=True)
        finally:
            self._return_stats_db_connection(conn)

    def _migrate_from_database_manager(self) -> bool:
        """One-time migration of lifetime totals from database_manager to the statistics database.

        Returns:
            True if migration was performed, False otherwise.
        """
        if not self._stats_db_initialized:
            return False
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return False
            cursor = conn.cursor()

            # Check if migration already completed
            cursor.execute(
                "SELECT 1 FROM migrations WHERE migration_name = 'initial_migration'"
            )
            if cursor.fetchone():
                return False  # Already migrated

            # Load data from old database_manager
            old_data = get_data(self.database_path)
            if not old_data or "data" not in old_data:
                # No old data to migrate – mark as complete anyway
                cursor.execute(
                    "INSERT INTO migrations (migration_name) VALUES ('initial_migration')"
                )
                conn.commit()
                return False

            # Store each category in lifetime_totals
            data_dict = old_data["data"]
            for category, category_data in data_dict.items():
                cursor.execute(
                    "INSERT OR REPLACE INTO lifetime_totals (category, data_json) VALUES (?, ?)",
                    (category, json.dumps(category_data)),
                )

            # Mark migration as complete
            cursor.execute(
                "INSERT INTO migrations (migration_name) VALUES ('initial_migration')"
            )
            conn.commit()

            logger.info(
                "Successfully migrated statistics from database_manager to statistics database"
            )
            return True
        except Exception as e:
            logger.error(f"Error during statistics migration: {e}", exc_info=True)
            return False
        finally:
            self._return_stats_db_connection(conn)

    # ---- Per-User Event Query Methods ----

    def get_user_events(
        self,
        username: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query timestamped events for a user within a date range.

        Args:
            username: Username to query.
            start_time: Start of range (unix timestamp). None for no lower bound.
            end_time: End of range (unix timestamp). None for no upper bound.
            event_type: Filter by event type. None for all types.
            limit: Maximum number of events to return.

        Returns:
            List of event dictionaries ordered by timestamp descending.
        """
        if not self._stats_db_initialized:
            return []
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return []
            query = "SELECT * FROM user_events WHERE lower(username) = lower(?)"
            params: list = [username]
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(end_time)
            if event_type is not None:
                query += " AND event_type = ?"
                params.append(event_type)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error querying user events: {e}")
            return []
        finally:
            self._return_stats_db_connection(conn)

    def get_date_range_summary(
        self, start_time: float, end_time: float
    ) -> Dict[str, Any]:
        """Get aggregate stats across all users for a date range.

        Args:
            start_time: Start of range (unix timestamp).
            end_time: End of range (unix timestamp).

        Returns:
            Dictionary with per-event-type counts/totals and overall metrics.
        """
        if not self._stats_db_initialized:
            return {}
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return {}
            cursor = conn.cursor()
            summary: Dict[str, Any] = {}

            # Counts and totals grouped by event type
            cursor.execute(
                "SELECT event_type, COUNT(*) as count, SUM(amount) as total_amount "
                "FROM user_events WHERE timestamp >= ? AND timestamp <= ? "
                "GROUP BY event_type",
                (start_time, end_time),
            )
            for row in cursor.fetchall():
                summary[row[0]] = {"count": row[1], "total_amount": row[2] or 0}

            # Unique users
            cursor.execute(
                "SELECT COUNT(DISTINCT username) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ?",
                (start_time, end_time),
            )
            summary["unique_users"] = cursor.fetchone()[0]

            # Total events
            cursor.execute(
                "SELECT COUNT(*) FROM user_events WHERE timestamp >= ? AND timestamp <= ?",
                (start_time, end_time),
            )
            summary["total_events"] = cursor.fetchone()[0]
            return summary
        except Exception as e:
            logger.error(f"Error getting date range summary: {e}")
            return {}
        finally:
            self._return_stats_db_connection(conn)

    def get_top_users_in_range(
        self,
        start_time: float,
        end_time: float,
        event_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get top contributors in a date range.

        Args:
            start_time: Start of range (unix timestamp).
            end_time: End of range (unix timestamp).
            event_type: Filter by event type. None for all types.
            limit: Maximum number of users to return.

        Returns:
            List of dicts with username, event_count, and total_amount.
        """
        if not self._stats_db_initialized:
            return []
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return []
            cursor = conn.cursor()
            if event_type:
                cursor.execute(
                    "SELECT username, COUNT(*) as event_count, SUM(amount) as total_amount "
                    "FROM user_events "
                    "WHERE timestamp >= ? AND timestamp <= ? AND event_type = ? "
                    "GROUP BY username ORDER BY total_amount DESC, event_count DESC LIMIT ?",
                    (start_time, end_time, event_type, limit),
                )
            else:
                cursor.execute(
                    "SELECT username, COUNT(*) as event_count, SUM(amount) as total_amount "
                    "FROM user_events "
                    "WHERE timestamp >= ? AND timestamp <= ? "
                    "GROUP BY username ORDER BY event_count DESC LIMIT ?",
                    (start_time, end_time, limit),
                )
            return [
                {
                    "username": row[0],
                    "event_count": row[1],
                    "total_amount": row[2] or 0,
                }
                for row in cursor.fetchall()
            ]
        except Exception as e:
            logger.error(f"Error getting top users in range: {e}")
            return []
        finally:
            self._return_stats_db_connection(conn)

    def _compute_highlights_from_user_events(
        self, cursor: sqlite3.Cursor, start_time: float, end_time: float
    ) -> Dict[str, Any]:
        """Aggregate highlight metrics from ``user_events`` for ``[start_time, end_time]``."""
        print("[highlights] _compute_highlights_from_user_events start")
        highlights: Dict[str, Any] = {}
        ts_params = (start_time, end_time)
        not_system = "lower(username) != ?"
        sys_name = (self._STATS_SYSTEM_USERNAME.lower(),)

        def top_by_sum(event_type: str, limit: int = 5) -> List[Dict[str, Any]]:
            cursor.execute(
                "SELECT username, COALESCE(SUM(amount), 0) AS v FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = ? "
                "GROUP BY username ORDER BY v DESC LIMIT ?",
                (*ts_params, event_type, limit),
            )
            return [
                {
                    "username": (r[0] if r[0] is not None else "?"),
                    "total": int(float(r[1] or 0)),
                }
                for r in cursor.fetchall()
            ]

        def top_by_count(
            event_type: str, limit: int = 5, exclude_system: bool = False
        ) -> List[Dict[str, Any]]:
            q = (
                "SELECT username, COUNT(*) AS c FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = ? "
            )
            params: List[Any] = [start_time, end_time, event_type]
            if exclude_system:
                q += f"AND {not_system} "
                params.append(sys_name[0])
            q += " GROUP BY username ORDER BY c DESC LIMIT ?"
            params.append(limit)
            cursor.execute(q, tuple(params))
            return [
                {
                    "username": (r[0] if r[0] is not None else "?"),
                    "count": int(r[1] or 0),
                }
                for r in cursor.fetchall()
            ]

        highlights["top_bits"] = top_by_sum("bit", 5)
        highlights["top_bit_donor"] = (
            {
                "username": highlights["top_bits"][0].get("username") or "?",
                "total": int(highlights["top_bits"][0].get("total") or 0),
            }
            if highlights["top_bits"]
            else None
        )

        cursor.execute(
            "SELECT SUM(amount) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'bit'",
            ts_params,
        )
        row = cursor.fetchone()
        highlights["total_bits"] = int(row[0] or 0) if row else 0

        cursor.execute(
            "SELECT username, amount FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'bit' "
            "ORDER BY amount DESC LIMIT 1",
            ts_params,
        )
        row = cursor.fetchone()
        highlights["biggest_bit_donation"] = (
            {
                "username": row[0] if row[0] is not None else "?",
                "amount": int(row[1] or 0),
            }
            if row
            else None
        )

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'sub'",
            ts_params,
        )
        highlights["total_new_subs"] = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'resub'",
            ts_params,
        )
        highlights["total_resubs"] = int(cursor.fetchone()[0] or 0)
        highlights["total_subs"] = highlights["total_new_subs"] + highlights["total_resubs"]

        highlights["top_new_subs"] = top_by_count("sub", 5, exclude_system=False)
        highlights["top_resubs"] = top_by_count("resub", 5, exclude_system=False)

        cursor.execute(
            "SELECT SUM(amount) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giftsub'",
            ts_params,
        )
        row = cursor.fetchone()
        highlights["total_gift_subs"] = int(row[0] or 0) if row else 0
        highlights["top_gift_subs"] = top_by_sum("giftsub", 5)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'donation'",
            ts_params,
        )
        highlights["total_donations"] = int(cursor.fetchone()[0] or 0)
        highlights["top_donations"] = top_by_count("donation", 5, exclude_system=False)

        cursor.execute(
            "SELECT SUM(amount) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'point_redeem'",
            ts_params,
        )
        row = cursor.fetchone()
        highlights["total_channel_points"] = int(row[0] or 0) if row else 0
        highlights["top_channel_points"] = top_by_sum("point_redeem", 5)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'follow'",
            ts_params,
        )
        highlights["total_follows"] = int(cursor.fetchone()[0] or 0)
        highlights["top_follows"] = top_by_count("follow", 5, exclude_system=False)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'raid'",
            ts_params,
        )
        highlights["total_raids"] = int(cursor.fetchone()[0] or 0)
        highlights["top_raids"] = top_by_count("raid", 5, exclude_system=False)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'chat_message'",
            ts_params,
        )
        highlights["total_chat_messages"] = int(cursor.fetchone()[0] or 0)
        highlights["top_chat_messages"] = top_by_count(
            "chat_message", 5, exclude_system=True
        )

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_entry'",
            ts_params,
        )
        highlights["total_giveaway_entries"] = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_win'",
            ts_params,
        )
        highlights["total_giveaway_wins"] = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_draw_complete'",
            ts_params,
        )
        highlights["total_giveaway_rounds"] = int(cursor.fetchone()[0] or 0)
        highlights["top_giveaway_entries"] = top_by_count(
            "giveaway_entry", 5, exclude_system=True
        )
        highlights["top_giveaway_wins"] = top_by_count(
            "giveaway_win", 5, exclude_system=True
        )

        cursor.execute(
            "SELECT username, COUNT(*) as event_count FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'chat_message' "
            f"AND {not_system} "
            "GROUP BY username ORDER BY event_count DESC LIMIT 1",
            ts_params + sys_name,
        )
        row = cursor.fetchone()
        highlights["top_chatter"] = (
            {
                "username": row[0] if row[0] is not None else "?",
                "event_count": int(row[1] or 0),
            }
            if row
            else None
        )

        cursor.execute(
            "SELECT username, COUNT(*) as event_count FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? "
            f"AND {not_system} "
            "GROUP BY username ORDER BY event_count DESC LIMIT 1",
            ts_params + sys_name,
        )
        row = cursor.fetchone()
        highlights["most_active_user"] = (
            {
                "username": row[0] if row[0] is not None else "?",
                "event_count": int(row[1] or 0),
            }
            if row
            else None
        )

        cursor.execute(
            "SELECT COUNT(DISTINCT username) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? "
            f"AND {not_system}",
            ts_params + sys_name,
        )
        highlights["unique_users"] = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            "SELECT COUNT(*) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ?",
            ts_params,
        )
        highlights["total_events"] = int(cursor.fetchone()[0] or 0)

        print(
            "[highlights] _compute_highlights_from_user_events done total_events=",
            highlights["total_events"],
        )
        return highlights

    @staticmethod
    def _empty_highlights_template(total_events: int = 0) -> Dict[str, Any]:
        """Default highlight keys for PNG export when full aggregation cannot run."""
        return {
            "top_bits": [],
            "top_bit_donor": None,
            "total_bits": 0,
            "biggest_bit_donation": None,
            "total_new_subs": 0,
            "total_resubs": 0,
            "total_subs": 0,
            "top_new_subs": [],
            "top_resubs": [],
            "total_gift_subs": 0,
            "top_gift_subs": [],
            "total_donations": 0,
            "top_donations": [],
            "total_channel_points": 0,
            "top_channel_points": [],
            "total_follows": 0,
            "top_follows": [],
            "total_raids": 0,
            "top_raids": [],
            "total_chat_messages": 0,
            "top_chat_messages": [],
            "total_giveaway_entries": 0,
            "total_giveaway_wins": 0,
            "total_giveaway_rounds": 0,
            "top_giveaway_entries": [],
            "top_giveaway_wins": [],
            "top_chatter": None,
            "most_active_user": None,
            "unique_users": 0,
            "total_events": int(total_events or 0),
            "_fallback_partial": True,
        }

    @staticmethod
    def _highlights_top_by_total(
        pairs: List[Tuple[str, int]], limit: int = 5
    ) -> List[Dict[str, Any]]:
        pairs = [(str(u or "?"), int(v or 0)) for u, v in pairs if int(v or 0) > 0]
        pairs.sort(key=lambda x: -x[1])
        return [{"username": u, "total": v} for u, v in pairs[:limit]]

    @staticmethod
    def _highlights_top_by_count(
        pairs: List[Tuple[str, int]], limit: int = 5
    ) -> List[Dict[str, Any]]:
        pairs = [(str(u or "?"), int(v or 0)) for u, v in pairs if int(v or 0) > 0]
        pairs.sort(key=lambda x: -x[1])
        return [{"username": u, "count": v} for u, v in pairs[:limit]]

    def _compute_highlights_from_lifetime(self) -> Dict[str, Any]:
        """Build export-shaped highlights from in-memory lifetime aggregates (no date filter).

        Used when ``user_events`` has no rows for the export window but JSON-backed
        lifetime totals are available from ``self.data``.
        """
        sys_lower = self._STATS_SYSTEM_USERNAME.lower()
        alerts = self.data.alerts
        chat = self.data.chat
        connectors = self.data.connectors
        gb = self.data.giveaways
        cbot = self.data.chatbot

        ab_pairs: List[Tuple[str, int]] = []
        cp_pairs: List[Tuple[str, int]] = []
        ns_pairs: List[Tuple[str, int]] = []
        rs_pairs: List[Tuple[str, int]] = []
        gs_pairs: List[Tuple[str, int]] = []
        dn_pairs: List[Tuple[str, int]] = []
        fl_pairs: List[Tuple[str, int]] = []
        rd_pairs: List[Tuple[str, int]] = []
        for un, st in (alerts.user_stats or {}).items():
            if str(un).strip().lower() == sys_lower:
                continue
            ab_pairs.append((un, int(getattr(st, "bit_alerts_played", 0) or 0)))
            cp_pairs.append((un, int(getattr(st, "channel_points_redeemed", 0) or 0)))
            ns_pairs.append((un, int(getattr(st, "new_subs_played", 0) or 0)))
            rs_pairs.append((un, int(getattr(st, "resubs_played", 0) or 0)))
            gs_pairs.append((un, int(getattr(st, "gift_subs_played", 0) or 0)))
            dn_pairs.append((un, int(getattr(st, "donations", 0) or 0)))
            fl_pairs.append((un, int(getattr(st, "follow_alerts_played", 0) or 0)))
            rd_pairs.append((un, int(getattr(st, "raids", 0) or 0)))

        chat_pairs: List[Tuple[str, int]] = []
        for un, st in (chat.user_stats or {}).items():
            if str(un).strip().lower() == sys_lower:
                continue
            n = int(getattr(st, "total_messages", 0) or 0) or int(
                getattr(st, "twitch_messages_received", 0) or 0
            )
            chat_pairs.append((un, n))

        gw_win_pairs: List[Tuple[str, int]] = [
            (str(u), int(n or 0))
            for u, n in (gb.user_wins or {}).items()
            if str(u).strip().lower() != sys_lower and int(n or 0) > 0
        ]

        activity_by_user: Dict[str, int] = {}

        def _acc(name: str, delta: int) -> None:
            if not name or str(name).strip().lower() == sys_lower:
                return
            activity_by_user[name] = activity_by_user.get(name, 0) + int(delta or 0)

        for un, st in (alerts.user_stats or {}).items():
            _acc(un, int(getattr(st, "total_alerts", 0) or 0))
        for un, st in (chat.user_stats or {}).items():
            _acc(
                un,
                int(getattr(st, "total_messages", 0) or 0)
                or int(getattr(st, "twitch_messages_received", 0) or 0),
            )
        for un, st in (connectors.user_stats or {}).items():
            _acc(un, int(getattr(st, "total_triggers", 0) or 0))
        for un, st in (connectors.user_connector_stats or {}).items():
            _acc(un, int(getattr(st, "total_triggers", 0) or 0))
        for un, st in (cbot.user_stats or {}).items():
            _acc(un, int(getattr(st, "total_interactions", 0) or 0))

        most_active: Optional[Tuple[str, int]] = None
        for un, score in activity_by_user.items():
            if most_active is None or score > most_active[1]:
                most_active = (un, score)

        top_chatter_row: Optional[Tuple[str, int]] = None
        for un, n in chat_pairs:
            if n <= 0:
                continue
            if top_chatter_row is None or n > top_chatter_row[1]:
                top_chatter_row = (un, n)

        top_bits = self._highlights_top_by_total(ab_pairs, 5)
        top_channel_points = self._highlights_top_by_total(cp_pairs, 5)
        top_new_subs = self._highlights_top_by_count(ns_pairs, 5)
        top_resubs = self._highlights_top_by_count(rs_pairs, 5)
        top_gift_subs = self._highlights_top_by_total(gs_pairs, 5)
        top_donations = self._highlights_top_by_count(dn_pairs, 5)
        top_follows = self._highlights_top_by_count(fl_pairs, 5)
        top_raids = self._highlights_top_by_count(rd_pairs, 5)
        top_chat_messages = self._highlights_top_by_count(chat_pairs, 5)
        top_giveaway_wins = self._highlights_top_by_count(gw_win_pairs, 5)

        total_giveaway_wins = sum(int(v or 0) for v in (gb.user_wins or {}).values())

        uniq_names = self._in_memory_tracked_usernames()
        unique_users = len(
            {u for u in uniq_names if str(u).strip().lower() != sys_lower}
        )

        score = (
            int(chat.total_messages or 0)
            + int(alerts.bit_alerts_played or 0)
            + int(alerts.total_bits or 0)
            + int(alerts.total_channel_points_redeemed or 0)
            + int(alerts.new_subs_played or 0)
            + int(alerts.resubs_played or 0)
            + int(alerts.total_gift_subs or 0)
            + int(alerts.donations or 0)
            + int(alerts.follow_alerts_played or 0)
            + int(alerts.raids or 0)
            + int(connectors.total_triggers or 0)
            + int(gb.total_entry_events or 0)
            + int(cbot.total_interactions or 0)
            + int(gb.giveaways_completed or 0)
        )
        if score <= 0 and unique_users <= 0:
            return {}

        out: Dict[str, Any] = {
            "top_bits": top_bits,
            "top_bit_donor": (
                {
                    "username": top_bits[0].get("username") or "?",
                    "total": int(top_bits[0].get("total") or 0),
                }
                if top_bits
                else None
            ),
            "total_bits": int(alerts.total_bits or 0),
            "biggest_bit_donation": None,
            "total_new_subs": int(alerts.new_subs_played or 0),
            "total_resubs": int(alerts.resubs_played or 0),
            "total_subs": int(alerts.new_subs_played or 0) + int(alerts.resubs_played or 0),
            "top_new_subs": top_new_subs,
            "top_resubs": top_resubs,
            "total_gift_subs": int(alerts.total_gift_subs or 0),
            "top_gift_subs": top_gift_subs,
            "total_donations": int(alerts.donations or 0),
            "top_donations": top_donations,
            "total_channel_points": int(alerts.total_channel_points_redeemed or 0),
            "top_channel_points": top_channel_points,
            "total_follows": int(alerts.follow_alerts_played or 0),
            "top_follows": top_follows,
            "total_raids": int(alerts.raids or 0),
            "top_raids": top_raids,
            "total_chat_messages": int(chat.total_messages or 0),
            "top_chat_messages": top_chat_messages,
            "total_giveaway_entries": int(gb.total_entry_events or 0),
            "total_giveaway_wins": int(total_giveaway_wins),
            "total_giveaway_rounds": int(gb.giveaways_completed or 0),
            "top_giveaway_entries": [],
            "top_giveaway_wins": top_giveaway_wins,
            "top_chatter": (
                {
                    "username": top_chatter_row[0],
                    "event_count": int(top_chatter_row[1]),
                }
                if top_chatter_row
                else None
            ),
            "most_active_user": (
                {
                    "username": most_active[0],
                    "event_count": int(most_active[1]),
                }
                if most_active and most_active[1] > 0
                else None
            ),
            "unique_users": int(unique_users),
            "total_events": max(1, int(score)),
            "_fallback_partial": True,
            "_lifetime_fallback": True,
        }
        return out

    def _compute_highlights_fallback(
        self, cursor: sqlite3.Cursor, start_time: float, end_time: float
    ) -> Dict[str, Any]:
        """Best-effort aggregates when ``_compute_highlights_from_user_events`` fails.

        Fills ``total_events`` first, then runs simple SUM/COUNT queries in isolation
        so legacy or partially inconsistent rows do not drop the whole export.
        """
        ts = (start_time, end_time)
        out = self._empty_highlights_template(0)

        def _one_int(query: str, params: Tuple[Any, ...]) -> Optional[int]:
            try:
                cursor.execute(query, params)
                row = cursor.fetchone()
                if not row:
                    return 0
                return int(row[0] or 0)
            except Exception as ex:
                print(f"[highlights] fallback query failed: {query[:60]}... err={ex!r}")
                return None

        te = _one_int(
            "SELECT COUNT(*) FROM user_events WHERE timestamp >= ? AND timestamp <= ?",
            ts,
        )
        if te is None:
            print("[highlights] fallback could not read total_events; returning empty template")
            return out
        out["total_events"] = te
        if te == 0:
            return out

        mapping = [
            (
                "total_bits",
                "SELECT COALESCE(SUM(amount), 0) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'bit'",
            ),
            (
                "total_new_subs",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'sub'",
            ),
            (
                "total_resubs",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'resub'",
            ),
            (
                "total_gift_subs",
                "SELECT COALESCE(SUM(amount), 0) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giftsub'",
            ),
            (
                "total_donations",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'donation'",
            ),
            (
                "total_channel_points",
                "SELECT COALESCE(SUM(amount), 0) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'point_redeem'",
            ),
            (
                "total_follows",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'follow'",
            ),
            (
                "total_raids",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'raid'",
            ),
            (
                "total_chat_messages",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'chat_message'",
            ),
            (
                "total_giveaway_entries",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_entry'",
            ),
            (
                "total_giveaway_wins",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_win'",
            ),
            (
                "total_giveaway_rounds",
                "SELECT COUNT(*) FROM user_events "
                "WHERE timestamp >= ? AND timestamp <= ? AND event_type = 'giveaway_draw_complete'",
            ),
        ]
        for key, q in mapping:
            v = _one_int(q, ts)
            if v is not None:
                out[key] = v
        out["total_subs"] = int(out["total_new_subs"] or 0) + int(out["total_resubs"] or 0)
        print(
            "[highlights] fallback totals: events=",
            out["total_events"],
            "bits=",
            out["total_bits"],
            "subs(new/resub)=",
            out["total_new_subs"],
            "/",
            out["total_resubs"],
        )
        return out

    def _debug_print_user_events_db_layout(
        self, cursor: sqlite3.Cursor, start_time: float, end_time: float
    ) -> None:
        """Console diagnostics for SQLite layout vs export range (best-effort, non-fatal)."""
        print("[highlights debug] ---- user_events DB layout ----")
        print("[highlights debug] active_db=", self._stats_db_path)
        legacy_path = get_data_path("statistics.db")
        print("[highlights debug] legacy_root_db=", legacy_path)
        for label, path in (
            ("active", self._stats_db_path),
            ("legacy_root", legacy_path),
        ):
            if not path:
                print(f"[highlights debug] {label}: (no path)")
                continue
            try:
                exists = os.path.isfile(path)
                sz = os.path.getsize(path) if exists else None
                print(f"[highlights debug] {label} exists={exists} size_bytes={sz}")
            except OSError as e:
                print(f"[highlights debug] {label} path stat error:", repr(e))

        def _safe_ts_label(name: str, ts: Any) -> str:
            if ts is None:
                return f"{name}=None"
            try:
                f = float(ts)
                return f"{name}={f} iso={datetime.fromtimestamp(f).isoformat()}"
            except (TypeError, ValueError, OSError) as e:
                return f"{name}={ts!r} (no iso: {e!r})"

        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_events'"
            )
            if not cursor.fetchone():
                print("[highlights debug] no table user_events in this database file")
                print("[highlights debug] ---- end layout ----")
                return
        except sqlite3.Error as e:
            print("[highlights debug] sqlite_master check failed:", repr(e))
            print("[highlights debug] ---- end layout ----")
            return

        try:
            cursor.execute("PRAGMA table_info(user_events)")
            cols = cursor.fetchall()
            print("[highlights debug] PRAGMA table_info(user_events):", cols)
        except sqlite3.Error as e:
            print("[highlights debug] PRAGMA table_info failed:", repr(e))

        try:
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_events'"
            )
            row = cursor.fetchone()
            print("[highlights debug] CREATE sql:", row[0] if row else None)
        except sqlite3.Error as e:
            print("[highlights debug] sqlite_master sql fetch failed:", repr(e))

        total_rows = 0
        mn_ts: Any = None
        mx_ts: Any = None
        try:
            cursor.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM user_events")
            r = cursor.fetchone()
            if r:
                total_rows = int(r[0] or 0)
                mn_ts, mx_ts = r[1], r[2]
            print(
                "[highlights debug] total_rows=",
                total_rows,
                _safe_ts_label("min_timestamp", mn_ts),
                _safe_ts_label("max_timestamp", mx_ts),
            )
            if mx_ts is not None:
                try:
                    if float(mx_ts) > 1e12:
                        print(
                            "[highlights debug] hint: max_timestamp > 1e12 — "
                            "values may be milliseconds; export range uses Unix seconds."
                        )
                except (TypeError, ValueError):
                    pass
        except sqlite3.Error as e:
            print("[highlights debug] COUNT/MIN/MAX timestamp failed:", repr(e))

        print(
            "[highlights debug] export_range ",
            _safe_ts_label("start_time", start_time),
            _safe_ts_label("end_time", end_time),
        )

        try:
            cursor.execute(
                "SELECT COUNT(*) FROM user_events WHERE timestamp >= ? AND timestamp <= ?",
                (start_time, end_time),
            )
            in_range = int(cursor.fetchone()[0] or 0)
            print("[highlights debug] rows_in_export_range=", in_range)
        except sqlite3.Error as e:
            print("[highlights debug] rows_in_range query failed:", repr(e))

        try:
            cursor.execute(
                "SELECT event_type, COUNT(*) AS c FROM user_events "
                "GROUP BY event_type ORDER BY c DESC LIMIT 40"
            )
            et = cursor.fetchall()
            print("[highlights debug] event_type counts (top 40):", et)
        except sqlite3.Error as e:
            print("[highlights debug] event_type breakdown failed:", repr(e))

        try:
            cursor.execute(
                "SELECT * FROM user_events ORDER BY timestamp DESC LIMIT 5"
            )
            samples = cursor.fetchall()
            if not samples:
                cursor.execute(
                    "SELECT * FROM user_events ORDER BY timestamp ASC LIMIT 5"
                )
                samples = cursor.fetchall()
            print(
                "[highlights debug] sample rows (up to 5):",
                [tuple(r) for r in samples],
            )
        except sqlite3.Error as e:
            print("[highlights debug] sample rows failed:", repr(e))

        print("[highlights debug] ---- end layout ----")

    def get_date_range_highlights(
        self, start_time: float, end_time: float
    ) -> Dict[str, Any]:
        """Get highlight data for the export card.

        Args:
            start_time: Start of range (unix timestamp).
            end_time: End of range (unix timestamp).

        Returns:
            Dictionary of highlight metrics (top donors, totals, etc.).
        """
        print(
            "[highlights] get_date_range_highlights range:",
            start_time,
            end_time,
            "db_path=",
            self._stats_db_path,
            "initialized=",
            self._stats_db_initialized,
        )
        if not self._stats_db_initialized:
            print("[highlights] stats DB not initialized — returning {}")
            return {}
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                print("[highlights] no DB connection — returning {}")
                return {}
            cursor = conn.cursor()
            self._debug_print_user_events_db_layout(cursor, start_time, end_time)
            try:
                data = self._compute_highlights_from_user_events(
                    cursor, start_time, end_time
                )
                print(
                    "[highlights] full compute OK total_events=",
                    data.get("total_events"),
                )
                return data
            except Exception as e:
                print("[highlights] full compute FAILED:", repr(e))
                logger.error(
                    "Full highlights compute failed, using fallback: %s",
                    e,
                    exc_info=True,
                )
                fb = self._compute_highlights_fallback(cursor, start_time, end_time)
                print(
                    "[highlights] fallback result total_events=",
                    fb.get("total_events"),
                    "partial=",
                    fb.get("_fallback_partial"),
                )
                return fb
        except Exception as e:
            logger.error(
                "Error getting date range highlights: %s", e, exc_info=True
            )
            print("[highlights] get_date_range_highlights outer error:", repr(e))
            return {}
        finally:
            self._return_stats_db_connection(conn)

    def get_highlights_for_export(
        self, start_time: float, end_time: float
    ) -> Tuple[Dict[str, Any], str]:
        """Return ``(highlights, source)`` for the PNG export.

        ``source`` is ``\"event_log\"`` when SQLite has rows in the date range,
        ``\"lifetime\"`` when the event log window is empty but lifetime JSON-backed
        aggregates in ``self.data`` can populate the image, otherwise ``\"empty\"``.
        """
        sql = self.get_date_range_highlights(start_time, end_time)
        te = int(sql.get("total_events", 0) or 0)
        if te:
            src = "event_log_fallback" if sql.get("_fallback_partial") else "event_log"
            print(
                "[highlights] get_highlights_for_export: source=",
                src,
                "total_events=",
                te,
                "keys_sample=",
                list(sql.keys())[:8],
            )
            return sql, src
        life = self._compute_highlights_from_lifetime()
        lte = int(life.get("total_events", 0) or 0)
        if lte:
            print(
                "[highlights] get_highlights_for_export: source=lifetime total_events=",
                lte,
                "lifetime_partial=",
                life.get("_fallback_partial"),
                "keys_sample=",
                list(life.keys())[:10],
            )
            return life, "lifetime"
        print("[highlights] get_highlights_for_export: empty (no event log or lifetime data)")
        return {}, "empty"

    def get_event_log_summary(self) -> Dict[str, Any]:
        """Diagnostics for the SQLite event log: row count and timestamp span.

        Returns:
            Keys: ``total_rows``, ``min_timestamp``, ``max_timestamp`` (or None if empty).
        """
        out: Dict[str, Any] = {
            "total_rows": 0,
            "min_timestamp": None,
            "max_timestamp": None,
        }
        if not self._stats_db_initialized:
            return out
        conn = None
        try:
            conn = self._get_stats_db_connection()
            if conn is None:
                return out
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_events")
            out["total_rows"] = int(cursor.fetchone()[0] or 0)
            if out["total_rows"] == 0:
                return out
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM user_events")
            row = cursor.fetchone()
            if row and row[0] is not None:
                out["min_timestamp"] = float(row[0])
            if row and row[1] is not None:
                out["max_timestamp"] = float(row[1])
            return out
        except Exception as e:
            logger.error(f"Error reading event log summary: {e}")
            return out
        finally:
            self._return_stats_db_connection(conn)

    def _in_memory_tracked_usernames(self) -> set:
        """Usernames present in any per-user lifetime stats structure."""
        names: set = set()
        names.update(self.data.alerts.user_stats.keys())
        names.update(self.data.connectors.user_stats.keys())
        names.update(self.data.connectors.user_connector_stats.keys())
        names.update(self.data.chatbot.user_stats.keys())
        names.update(self.data.quotes.user_stats.keys())
        names.update(self.data.chat.user_stats.keys())
        names.update(self.data.giveaways.user_wins.keys())
        return names

    @staticmethod
    def _dict_key_case_insensitive(d: Dict[str, Any], username: str) -> Optional[str]:
        """Return the dict's actual key matching username case-insensitively, or None."""
        if not username or not str(username).strip():
            return None
        low = str(username).strip().lower()
        for k in d:
            if str(k).lower() == low:
                return k
        return None

    def get_all_tracked_usernames(self) -> List[str]:
        """Get all usernames with any tracked statistics (SQLite events and/or lifetime JSON).

        Returns:
            Sorted list of unique usernames.
        """
        usernames: set = self._in_memory_tracked_usernames()
        if self._stats_db_initialized:
            conn = None
            try:
                conn = self._get_stats_db_connection()
                if conn is not None:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT DISTINCT username FROM user_events ORDER BY username"
                    )
                    usernames.update(row[0] for row in cursor.fetchall())
            except Exception as e:
                logger.error(f"Error getting tracked usernames from SQL: {e}")
            finally:
                self._return_stats_db_connection(conn)
        return sorted(usernames)

    # ---- End Separate Statistics Database Methods ----

    def initialize_with_data(self, all_data: Dict[str, Any]):
        """Initialize the statistics manager with pre-loaded data

        Args:
            all_data: Dictionary mapping database paths to their data
        """
        try:
            stored_data = all_data.get("Statistics", {})

            if stored_data and "data" in stored_data:
                # Load each statistics category
                data_dict = stored_data["data"]

                if "alerts" in data_dict:
                    alerts_dict = data_dict["alerts"]
                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in alerts_dict:
                        user_stats_dict = alerts_dict.pop("user_stats")
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserAlertStatistics(**user_data)

                    # Handle individual alert type stats
                    alert_type_stats = {}
                    if "alert_type_stats" in alerts_dict:
                        alert_type_stats_dict = alerts_dict.pop("alert_type_stats")
                        for alert_type, alert_data in alert_type_stats_dict.items():
                            alert_type_stats[alert_type] = (
                                IndividualAlertTypeStatistics(**alert_data)
                            )

                    self.data.alerts = AlertStatistics(**alerts_dict)
                    self.data.alerts.user_stats = user_stats
                    self.data.alerts.alert_type_stats = alert_type_stats

                if "hype_trains" in data_dict:
                    hype_dict = data_dict["hype_trains"].copy()
                    logger.debug(f"Loading hype_trains data: {hype_dict}")
                    self.data.hype_trains = HypeTrainStatistics(**hype_dict)

                if "stream_sessions" in data_dict:
                    stream_sessions_dict = data_dict["stream_sessions"].copy()
                    self.data.stream_sessions = StreamSessionStatistics(
                        **stream_sessions_dict
                    )

                if "viewer_engagement" in data_dict:
                    viewer_engagement_dict = data_dict["viewer_engagement"].copy()
                    self.data.viewer_engagement = ViewerEngagementStatistics(
                        **viewer_engagement_dict
                    )

                if "performance" in data_dict:
                    performance_dict = data_dict["performance"].copy()
                    self.data.performance = PerformanceStatistics(**performance_dict)

                if "settings" in data_dict:
                    settings_dict = data_dict["settings"].copy()
                    self.data.settings = StatisticsSettings(**settings_dict)

                logger.info("Statistics loaded from pre-loaded data")
            else:
                logger.info(
                    "No statistics data found in pre-loaded data, using defaults"
                )

        except Exception as e:
            logger.error(
                f"Error loading statistics from pre-loaded data: {str(e)}",
                exc_info=True,
            )

    def _load_from_database(self):
        """Load statistics from database"""
        try:
            stored_data = None

            # Try loading from the new statistics database first
            if self._stats_db_initialized:
                stored_data = self._load_lifetime_totals_from_stats_db()

            # If no data in new DB, try migrating from old database_manager
            if not stored_data:
                old_data = get_data(self.database_path)
                if old_data and "data" in old_data:
                    stored_data = old_data
                    # Migrate to new database if available
                    if self._stats_db_initialized:
                        self._migrate_from_database_manager()

            if stored_data and "data" in stored_data:
                # Load each statistics category
                data_dict = stored_data["data"]

                if "alerts" in data_dict:
                    alerts_dict = data_dict["alerts"]
                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in alerts_dict:
                        user_stats_dict = alerts_dict.pop("user_stats")
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserAlertStatistics(**user_data)

                    # Handle individual alert type stats
                    alert_type_stats = {}
                    if "alert_type_stats" in alerts_dict:
                        alert_type_stats_dict = alerts_dict.pop("alert_type_stats")
                        for alert_type, alert_data in alert_type_stats_dict.items():
                            alert_type_stats[alert_type] = (
                                IndividualAlertTypeStatistics(**alert_data)
                            )

                    self.data.alerts = AlertStatistics(**alerts_dict)
                    self.data.alerts.user_stats = user_stats
                    self.data.alerts.alert_type_stats = alert_type_stats

                if "hype_trains" in data_dict:
                    hype_dict = data_dict["hype_trains"].copy()
                    logger.debug(f"Loading hype_trains data: {hype_dict}")

                    # Load level_completions dictionary
                    level_completions = {}
                    if "level_completions" in hype_dict:
                        level_completions_dict = hype_dict.pop("level_completions")
                        logger.debug(
                            f"Found level_completions in hype_dict: {level_completions_dict}"
                        )
                        if isinstance(level_completions_dict, dict):
                            # Convert string keys to integers (JSON serialization converts int keys to strings)
                            level_completions = {
                                int(k): v for k, v in level_completions_dict.items()
                            }
                        elif isinstance(level_completions_dict, list):
                            # Handle corrupted data - if it's a list, try to convert to dict with indices as keys
                            logger.warning(
                                f"level_completions is corrupted as list instead of dict: {level_completions_dict}. "
                                "Converting to dict with indices as keys."
                            )
                            level_completions = {
                                i: v
                                for i, v in enumerate(level_completions_dict)
                                if v is not None
                            }
                        elif level_completions_dict is not None:
                            logger.warning(
                                f"level_completions is not a dict or list: {type(level_completions_dict)} = {level_completions_dict}. "
                                "Initializing as empty dict."
                            )
                    else:
                        logger.debug(
                            "No level_completions found in hype_dict - initializing as empty dict"
                        )

                    hype_stats = HypeTrainStatistics(**hype_dict)
                    hype_stats.level_completions = level_completions

                    self.data.hype_trains = hype_stats
                    logger.info(
                        f"Hype train statistics loaded - level_completions: {level_completions}, total: {hype_stats.total_completions}"
                    )

                    # Check and repair data integrity after loading
                    integrity_issues = self.check_data_integrity()
                    if integrity_issues:
                        logger.warning(
                            f"Data integrity issues found: {integrity_issues}"
                        )
                        if self.repair_data_integrity():
                            logger.info("Data integrity automatically repaired")
                        else:
                            logger.error("Failed to repair data integrity issues")

                if "connectors" in data_dict:
                    # Only load connectors_triggered and total_triggers, not connectors_created
                    # since we get that dynamically from connector_manager
                    conn_dict = data_dict["connectors"]
                    self.data.connectors.connectors_triggered = conn_dict.get(
                        "connectors_triggered", 0
                    )
                    self.data.connectors.total_triggers = conn_dict.get(
                        "total_triggers", 0
                    )

                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in conn_dict:
                        user_stats_dict = conn_dict["user_stats"]
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserConnectorStatistics(**user_data)
                    self.data.connectors.user_stats = user_stats

                    # Handle per-user individual connector stats
                    user_connector_stats = {}
                    if "user_connector_stats" in conn_dict:
                        user_connector_stats_dict = conn_dict["user_connector_stats"]
                        for username, user_data in user_connector_stats_dict.items():
                            user_connector_stats[username] = UserConnectorStatistics(
                                **user_data
                            )
                    self.data.connectors.user_connector_stats = user_connector_stats

                    # Handle individual connector stats
                    connector_stats = {}
                    if "connector_stats" in conn_dict:
                        connector_stats_dict = conn_dict["connector_stats"]
                        for (
                            connector_name,
                            connector_data,
                        ) in connector_stats_dict.items():
                            connector_stats[connector_name] = (
                                IndividualConnectorStatistics(**connector_data)
                            )
                    self.data.connectors.connector_stats = connector_stats

                    logger.info(
                        f"Loaded connectors statistics from database: {self.data.connectors.connectors_triggered} triggered, {self.data.connectors.total_triggers} total triggers"
                    )

                if "chatbot" in data_dict:
                    bot_dict = data_dict["chatbot"]
                    # Only load triggered counts and total interactions, not created counts
                    self.data.chatbot.commands_triggered = bot_dict.get(
                        "commands_triggered", 0
                    )
                    self.data.chatbot.events_triggered = bot_dict.get(
                        "events_triggered", 0
                    )
                    self.data.chatbot.total_interactions = bot_dict.get(
                        "total_interactions", 0
                    )

                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in bot_dict:
                        user_stats_dict = bot_dict["user_stats"]
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserChatbotStatistics(**user_data)
                    self.data.chatbot.user_stats = user_stats

                    # Handle per-user individual command and event stats
                    user_command_stats = {}
                    user_event_stats = {}
                    if "user_command_stats" in bot_dict:
                        user_command_stats_dict = bot_dict["user_command_stats"]
                        for username, user_data in user_command_stats_dict.items():
                            user_command_stats[username] = UserCommandStatistics(
                                **user_data
                            )
                    if "user_event_stats" in bot_dict:
                        user_event_stats_dict = bot_dict["user_event_stats"]
                        for username, user_data in user_event_stats_dict.items():
                            user_event_stats[username] = UserEventStatistics(
                                **user_data
                            )
                    self.data.chatbot.user_command_stats = user_command_stats
                    self.data.chatbot.user_event_stats = user_event_stats

                    # Handle individual command and event stats
                    command_stats = {}
                    event_stats = {}
                    if "command_stats" in bot_dict:
                        command_stats_dict = bot_dict["command_stats"]
                        for command_name, command_data in command_stats_dict.items():
                            command_stats[command_name] = IndividualCommandStatistics(
                                **command_data
                            )
                    if "event_stats" in bot_dict:
                        event_stats_dict = bot_dict["event_stats"]
                        for event_name, event_data in event_stats_dict.items():
                            event_stats[event_name] = IndividualEventStatistics(
                                **event_data
                            )
                    self.data.chatbot.command_stats = command_stats
                    self.data.chatbot.event_stats = event_stats

                if "quotes" in data_dict:
                    quotes_dict = data_dict["quotes"]
                    # Only load redeemed count and usage, not created count
                    self.data.quotes.total_quotes_redeemed = quotes_dict.get(
                        "total_quotes_redeemed", 0
                    )
                    self.data.quotes.individual_quote_usage = quotes_dict.get(
                        "individual_quote_usage", {}
                    )

                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in quotes_dict:
                        user_stats_dict = quotes_dict["user_stats"]
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserQuoteStatistics(**user_data)
                    self.data.quotes.user_stats = user_stats

                if "giveaways" in data_dict:
                    gdict = data_dict["giveaways"]
                    self.data.giveaways.giveaways_completed = int(
                        gdict.get("giveaways_completed", 0) or 0
                    )
                    self.data.giveaways.total_entry_events = int(
                        gdict.get("total_entry_events", 0) or 0
                    )
                    uw = gdict.get("user_wins", {})
                    if isinstance(uw, dict):
                        self.data.giveaways.user_wins = {
                            str(k): int(v) for k, v in uw.items()
                        }
                    else:
                        self.data.giveaways.user_wins = {}

                if "chat" in data_dict:
                    chat_dict = data_dict["chat"]
                    # Handle user statistics separately
                    user_stats = {}
                    if "user_stats" in chat_dict:
                        user_stats_dict = chat_dict.pop("user_stats")
                        for username, user_data in user_stats_dict.items():
                            user_stats[username] = UserChatStatistics(**user_data)

                    self.data.chat = ChatStatistics(**chat_dict)
                    self.data.chat.user_stats = user_stats

                if "templates" in data_dict:
                    templates_dict = data_dict["templates"]
                    for template_name, template_data in templates_dict.items():
                        template_stats = TemplateStatistics(**template_data)
                        self.data.templates[template_name] = template_stats

                # Always update session start time to current session, not loaded value
                self.data.session.start_time = time.time()
                logger.info("Statistics loaded from database successfully")
            else:
                logger.info("No existing statistics found in database, starting fresh")
        except Exception as e:
            logger.error(f"Error loading statistics from database: {e}")

    def _save_to_database(self):
        """Save current statistics to database"""
        try:
            logger.debug("Starting statistics save to database")

            # Update session duration
            self.data.session.session_duration = (
                time.time() - self.data.session.start_time
            )
            self.data.session.last_save_time = time.time()

            # Convert to dictionary for storage
            data_dict = {
                "data": {
                    "alerts": {
                        "bit_alerts_played": self.data.alerts.bit_alerts_played,
                        "total_bits": self.data.alerts.total_bits,
                        "resubs_played": self.data.alerts.resubs_played,
                        "new_subs_played": self.data.alerts.new_subs_played,
                        "gift_subs_played": self.data.alerts.gift_subs_played,
                        "total_gift_subs": self.data.alerts.total_gift_subs,
                        "follow_alerts_played": self.data.alerts.follow_alerts_played,
                        "raids": self.data.alerts.raids,
                        "point_alerts_redeemed": self.data.alerts.point_alerts_redeemed,
                        "total_channel_points_redeemed": self.data.alerts.total_channel_points_redeemed,
                        "donations": self.data.alerts.donations,
                        "user_stats": {
                            username: {
                                "bit_alerts_played": stats.bit_alerts_played,
                                "resubs_played": stats.resubs_played,
                                "new_subs_played": stats.new_subs_played,
                                "gift_subs_played": stats.gift_subs_played,
                                "follow_alerts_played": stats.follow_alerts_played,
                                "raids": stats.raids,
                                "point_alerts_redeemed": stats.point_alerts_redeemed,
                                "channel_points_redeemed": stats.channel_points_redeemed,
                                "donations": stats.donations,
                                "total_alerts": stats.total_alerts,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.alerts.user_stats.items()
                        },
                        "alert_type_stats": {
                            alert_type: {
                                "alert_type": stats.alert_type,
                                "alert_name": stats.alert_name,
                                "trigger_count": stats.trigger_count,
                                "first_triggered": stats.first_triggered,
                                "last_triggered": stats.last_triggered,
                            }
                            for alert_type, stats in self.data.alerts.alert_type_stats.items()
                        },
                    },
                    "hype_trains": {
                        "level_completions": dict(
                            self.data.hype_trains.level_completions
                        )
                        if isinstance(self.data.hype_trains.level_completions, dict)
                        else {},
                        "total_completions": self.data.hype_trains.total_completions,
                    },
                    "connectors": {
                        # connectors_created is not stored since we get it dynamically from connector_manager
                        "connectors_triggered": self.data.connectors.connectors_triggered,
                        "total_triggers": self.data.connectors.total_triggers,
                        "user_stats": {
                            username: {
                                "connectors_triggered": stats.connectors_triggered,
                                "total_triggers": stats.total_triggers,
                                "connector_usage": stats.connector_usage,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.connectors.user_stats.items()
                        },
                        "user_connector_stats": {
                            username: {
                                "connectors_triggered": stats.connectors_triggered,
                                "total_triggers": stats.total_triggers,
                                "connector_usage": stats.connector_usage,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.connectors.user_connector_stats.items()
                        },
                        "connector_stats": {
                            connector_name: {
                                "connector_name": stats.connector_name,
                                "trigger_count": stats.trigger_count,
                                "first_triggered": stats.first_triggered,
                                "last_triggered": stats.last_triggered,
                            }
                            for connector_name, stats in self.data.connectors.connector_stats.items()
                        },
                    },
                    "chatbot": {
                        # commands_created and events_created are not stored since we get them dynamically
                        "commands_triggered": self.data.chatbot.commands_triggered,
                        "events_triggered": self.data.chatbot.events_triggered,
                        "total_interactions": self.data.chatbot.total_interactions,
                        "user_stats": {
                            username: {
                                "commands_triggered": stats.commands_triggered,
                                "events_triggered": stats.events_triggered,
                                "total_interactions": stats.total_interactions,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.chatbot.user_stats.items()
                        },
                        "user_command_stats": {
                            username: {
                                "command_usage": stats.command_usage,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.chatbot.user_command_stats.items()
                        },
                        "user_event_stats": {
                            username: {
                                "event_usage": stats.event_usage,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.chatbot.user_event_stats.items()
                        },
                        "command_stats": {
                            command_name: {
                                "command_name": stats.command_name,
                                "usage_count": stats.usage_count,
                                "first_used": stats.first_used,
                                "last_used": stats.last_used,
                            }
                            for command_name, stats in self.data.chatbot.command_stats.items()
                        },
                        "event_stats": {
                            event_name: {
                                "event_name": stats.event_name,
                                "trigger_count": stats.trigger_count,
                                "first_triggered": stats.first_triggered,
                                "last_triggered": stats.last_triggered,
                            }
                            for event_name, stats in self.data.chatbot.event_stats.items()
                        },
                    },
                    "quotes": {
                        # quotes_created is not stored since we get it dynamically
                        "total_quotes_redeemed": self.data.quotes.total_quotes_redeemed,
                        "individual_quote_usage": self.data.quotes.individual_quote_usage,
                        "user_stats": {
                            username: {
                                "total_quotes_redeemed": stats.total_quotes_redeemed,
                                "individual_quote_usage": stats.individual_quote_usage,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.quotes.user_stats.items()
                        },
                    },
                    "giveaways": {
                        "giveaways_completed": self.data.giveaways.giveaways_completed,
                        "total_entry_events": self.data.giveaways.total_entry_events,
                        "user_wins": dict(self.data.giveaways.user_wins),
                    },
                    "chat": {
                        "twitch_messages_received": self.data.chat.twitch_messages_received,
                        "total_messages": self.data.chat.total_messages,
                        "user_stats": {
                            username: {
                                "twitch_messages_received": stats.twitch_messages_received,
                                "total_messages": stats.total_messages,
                                "first_seen": stats.first_seen,
                                "last_seen": stats.last_seen,
                            }
                            for username, stats in self.data.chat.user_stats.items()
                        },
                    },
                    "templates": {
                        template_name: {
                            "template_name": stats.template_name,
                            "custom_stats": stats.custom_stats,
                            "last_updated": stats.last_updated,
                        }
                        for template_name, stats in self.data.templates.items()
                    },
                    "session": {
                        "start_time": self.data.session.start_time,
                        "last_save_time": self.data.session.last_save_time,
                        "session_duration": self.data.session.session_duration,
                    },
                },
                "last_updated": time.time(),
                "version": "1.0",
            }

            logger.debug(
                f"Saving hype_trains data: {data_dict.get('data', {}).get('hype_trains', 'NOT FOUND')}"
            )

            # Save to the separate statistics database if available
            if self._stats_db_initialized:
                self._save_lifetime_totals_to_stats_db(data_dict["data"])
            else:
                # Fallback to old database_manager
                success = set_data(self.database_path, data_dict)
                if success:
                    logger.debug("Statistics saved to database successfully")
                else:
                    logger.error("Failed to save statistics to database")

        except Exception as e:
            logger.error(f"Error saving statistics to database: {e}")

    def _track_user_alert(self, username: str, alert_type: str):
        """Helper method to track per-user alert statistics"""
        if username not in self.data.alerts.user_stats:
            self.data.alerts.user_stats[username] = UserAlertStatistics()

        user_stats = self.data.alerts.user_stats[username]
        current_time = time.time()

        # Update the specific alert type
        if hasattr(user_stats, alert_type):
            setattr(user_stats, alert_type, getattr(user_stats, alert_type) + 1)

        # Update total alerts and timestamps
        user_stats.total_alerts += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if user_stats.total_alerts == 1:
            user_stats.first_seen = current_time

    def _track_alert_type(self, alert_type: str, alert_name: str = ""):
        """Helper method to track individual alert type statistics"""
        if alert_type not in self.data.alerts.alert_type_stats:
            self.data.alerts.alert_type_stats[alert_type] = (
                IndividualAlertTypeStatistics(
                    alert_type=alert_type, alert_name=alert_name
                )
            )

        alert_stats = self.data.alerts.alert_type_stats[alert_type]
        current_time = time.time()

        # Update trigger count and timestamps
        alert_stats.trigger_count += 1
        alert_stats.last_triggered = current_time

        # Set first_triggered if this is the first time
        if alert_stats.trigger_count == 1:
            alert_stats.first_triggered = current_time

        # Update alert_name if provided and not set
        if alert_name and not alert_stats.alert_name:
            alert_stats.alert_name = alert_name

    # Alert Statistics Methods
    def increment_bit_alerts(
        self, bit_amount: int = 0, username: Optional[str] = None, alert_name: str = ""
    ):
        """Increment bit alerts played count and total bits amount"""
        self.data.alerts.bit_alerts_played += 1
        self.data.alerts.total_bits += bit_amount

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "bit_alerts_played")
            self._record_event(username, "bit", bit_amount, alert_name)

        # Track individual alert type
        self._track_alert_type("bit", alert_name)

        logger.info(
            f"Bit alerts incremented to: {self.data.alerts.bit_alerts_played}, total bits: {self.data.alerts.total_bits}"
        )

    def increment_resubs(self, username: Optional[str] = None, alert_name: str = ""):
        """Increment resubs played count"""
        self.data.alerts.resubs_played += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "resubs_played")
            self._record_event(username, "resub", 0, alert_name)

        # Track individual alert type
        self._track_alert_type("resub", alert_name)

        logger.debug(f"Resubs incremented to: {self.data.alerts.resubs_played}")

    def increment_new_subs(self, username: Optional[str] = None, alert_name: str = ""):
        """Increment new subs played count"""
        self.data.alerts.new_subs_played += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "new_subs_played")
            self._record_event(username, "sub", 0, alert_name)

        # Track individual alert type
        self._track_alert_type("sub", alert_name)

        logger.debug(f"New subs incremented to: {self.data.alerts.new_subs_played}")

    def increment_gift_subs(
        self,
        gift_quantity: int = 1,
        username: Optional[str] = None,
        alert_name: str = "",
    ):
        """Increment gift subs played count and total gift subs amount"""
        self.data.alerts.gift_subs_played += 1
        self.data.alerts.total_gift_subs += gift_quantity

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "gift_subs_played")
            self._record_event(username, "giftsub", gift_quantity, alert_name)

        # Track individual alert type
        self._track_alert_type("giftsub", alert_name)

        logger.debug(
            f"Gift subs incremented to: {self.data.alerts.gift_subs_played}, total gift subs: {self.data.alerts.total_gift_subs}"
        )

    def increment_follow_alerts(
        self, username: Optional[str] = None, alert_name: str = ""
    ):
        """Increment follow alerts played count"""
        self.data.alerts.follow_alerts_played += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "follow_alerts_played")
            self._record_event(username, "follow", 0, alert_name)

        # Track individual alert type
        self._track_alert_type("follow", alert_name)

        logger.info(
            f"Follow alerts incremented to: {self.data.alerts.follow_alerts_played}"
        )

    def increment_raids(self, username: Optional[str] = None, alert_name: str = ""):
        """Increment raids count"""
        self.data.alerts.raids += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "raids")
            self._record_event(username, "raid", 0, alert_name)

        # Track individual alert type
        self._track_alert_type("raid", alert_name)

        logger.info(f"Raids incremented to: {self.data.alerts.raids}")

    def increment_point_alerts(
        self,
        username: Optional[str] = None,
        alert_name: str = "",
        points_amount: int = 0,
    ):
        """Increment point alerts redeemed count"""
        self.data.alerts.point_alerts_redeemed += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "point_alerts_redeemed")
            try:
                pts = int(points_amount or 0)
            except (TypeError, ValueError):
                pts = 0
            self._record_event(username, "point_redeem", float(pts), alert_name)

        # Track individual alert type
        self._track_alert_type("point", alert_name)

        logger.info(
            f"Point alerts incremented to: {self.data.alerts.point_alerts_redeemed}"
        )

    def increment_channel_points_redeemed(
        self, points_amount: int, username: Optional[str] = None
    ):
        """Increment total channel points redeemed and track per-user statistics"""
        # Validate points_amount
        if points_amount is None or not isinstance(points_amount, (int, float)):
            logger.warning(
                f"Invalid points_amount received: {points_amount} (type: {type(points_amount)})"
            )
            return

        # Convert to int if it's a float
        points_amount = int(points_amount)

        if points_amount <= 0:
            logger.debug(
                f"Skipping increment with non-positive points_amount: {points_amount}"
            )
            return

        self.data.alerts.total_channel_points_redeemed += points_amount

        # Track per-user statistics if username provided
        if username:
            if username not in self.data.alerts.user_stats:
                self.data.alerts.user_stats[username] = UserAlertStatistics()
            self.data.alerts.user_stats[
                username
            ].channel_points_redeemed += points_amount
            self.data.alerts.user_stats[username].last_seen = time.time()

        logger.info(
            f"Channel points redeemed incremented by {points_amount} to: {self.data.alerts.total_channel_points_redeemed}"
        )

    def increment_donations(self, username: Optional[str] = None, alert_name: str = ""):
        """Increment donations count"""
        self.data.alerts.donations += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_alert(username, "donations")
            self._record_event(username, "donation", 0, alert_name)

        # Track individual alert type
        self._track_alert_type("donation", alert_name)

        logger.info(f"Donations incremented to: {self.data.alerts.donations}")

    # Hype Train Statistics Methods
    def increment_hype_train_completion(self, level: int):
        """Increment hype train completion for specific level (supports unlimited levels)"""
        # Ensure level is an integer
        try:
            level = int(level)
        except (ValueError, TypeError):
            logger.error(
                f"Invalid hype train level received: {level} (type: {type(level)})"
            )
            return

        # Ensure level_completions is always a dict (defensive programming)
        if not isinstance(self.data.hype_trains.level_completions, dict):
            logger.error(
                f"level_completions is not a dict: {type(self.data.hype_trains.level_completions)} = {self.data.hype_trains.level_completions}. "
                "Resetting to empty dict."
            )
            self.data.hype_trains.level_completions = {}

        # Use dictionary for dynamic levels (unlimited - no cap)
        self.data.hype_trains.level_completions[level] = (
            self.data.hype_trains.level_completions.get(level, 0) + 1
        )

        self.data.hype_trains.total_completions += 1
        logger.info(
            f"Hype train level {level} completion recorded. Current level_completions: {self.data.hype_trains.level_completions}. Total: {self.data.hype_trains.total_completions}"
        )

    # Connector Statistics Methods

    def increment_connectors_triggered(
        self, username: Optional[str] = None, connector_name: str = ""
    ):
        """Increment connectors triggered count"""
        self.data.connectors.connectors_triggered += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_connector(username, 1)

        # Track individual connector if name provided
        if connector_name:
            self._track_individual_connector(connector_name)

        if username:
            self._record_event(
                username, "connector", 1.0, connector_name or ""
            )

        logger.debug(
            f"Connectors triggered incremented to: {self.data.connectors.connectors_triggered}"
        )

    def increment_connector_triggers(
        self, count: int = 1, username: Optional[str] = None, connector_name: str = ""
    ):
        """Increment total connector triggers"""
        self.data.connectors.total_triggers += count

        # Track per-user statistics if username provided
        if username:
            self._track_user_connector(username, count)
            # Track per-user individual connector usage if both username and connector_name provided
            if connector_name:
                for _ in range(count):
                    self._track_user_connector(username, connector_name)

        # Track individual connector if name provided
        if connector_name:
            for _ in range(count):
                self._track_individual_connector(connector_name)

        if username and connector_name:
            self._record_event(username, "connector", float(count), connector_name)

        logger.debug(
            f"Total connector triggers incremented to: {self.data.connectors.total_triggers}"
        )

    def _track_user_chatbot(self, username: str, stat_type: str):
        """Helper method to track per-user chatbot statistics"""
        if username not in self.data.chatbot.user_stats:
            self.data.chatbot.user_stats[username] = UserChatbotStatistics()

        user_stats = self.data.chatbot.user_stats[username]
        current_time = time.time()

        # Update the specific statistic
        if hasattr(user_stats, stat_type):
            setattr(user_stats, stat_type, getattr(user_stats, stat_type) + 1)

        # Update total interactions and timestamps
        user_stats.total_interactions += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if user_stats.total_interactions == 1:
            user_stats.first_seen = current_time

    def _track_command(self, command_name: str):
        """Helper method to track individual command statistics"""
        if command_name not in self.data.chatbot.command_stats:
            self.data.chatbot.command_stats[command_name] = IndividualCommandStatistics(
                command_name=command_name
            )

        command_stats = self.data.chatbot.command_stats[command_name]
        current_time = time.time()

        # Update usage count and timestamps
        command_stats.usage_count += 1
        command_stats.last_used = current_time

        # Set first_used if this is the first time
        if command_stats.usage_count == 1:
            command_stats.first_used = current_time

    def _track_event(self, event_name: str):
        """Helper method to track individual event statistics"""
        if event_name not in self.data.chatbot.event_stats:
            self.data.chatbot.event_stats[event_name] = IndividualEventStatistics(
                event_name=event_name
            )

        event_stats = self.data.chatbot.event_stats[event_name]
        current_time = time.time()

        # Update trigger count and timestamps
        event_stats.trigger_count += 1
        event_stats.last_triggered = current_time

        # Set first_triggered if this is the first time
        if event_stats.trigger_count == 1:
            event_stats.first_triggered = current_time

    def _track_user_command(self, username: str, command_name: str):
        """Helper method to track per-user command usage"""
        if username not in self.data.chatbot.user_command_stats:
            self.data.chatbot.user_command_stats[username] = UserCommandStatistics()

        user_stats = self.data.chatbot.user_command_stats[username]
        current_time = time.time()

        # Update command usage
        if command_name not in user_stats.command_usage:
            user_stats.command_usage[command_name] = 0
        user_stats.command_usage[command_name] += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if len(user_stats.command_usage) == 1 and all(
            count == 1 for count in user_stats.command_usage.values()
        ):
            user_stats.first_seen = current_time

    def _track_user_event(self, username: str, event_name: str):
        """Helper method to track per-user event usage"""
        if username not in self.data.chatbot.user_event_stats:
            self.data.chatbot.user_event_stats[username] = UserEventStatistics()

        user_stats = self.data.chatbot.user_event_stats[username]
        current_time = time.time()

        # Update event usage
        if event_name not in user_stats.event_usage:
            user_stats.event_usage[event_name] = 0
        user_stats.event_usage[event_name] += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if len(user_stats.event_usage) == 1 and all(
            count == 1 for count in user_stats.event_usage.values()
        ):
            user_stats.first_seen = current_time

    def _track_user_connector(self, username: str, connector_name: str):
        """Helper method to track per-user connector usage"""
        if username not in self.data.connectors.user_connector_stats:
            self.data.connectors.user_connector_stats[username] = (
                UserConnectorStatistics()
            )

        user_stats = self.data.connectors.user_connector_stats[username]
        current_time = time.time()

        # Update connector usage
        if connector_name not in user_stats.connector_usage:
            user_stats.connector_usage[connector_name] = 0
        user_stats.connector_usage[connector_name] += 1
        user_stats.connectors_triggered += 1
        user_stats.total_triggers += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if user_stats.total_triggers == 1:
            user_stats.first_seen = current_time

    def _track_individual_connector(self, connector_name: str):
        """Helper method to track individual connector usage (global, not per-user)"""
        if connector_name not in self.data.connectors.connector_stats:
            self.data.connectors.connector_stats[connector_name] = (
                IndividualConnectorStatistics(
                    connector_name=connector_name,
                    trigger_count=0,
                    last_triggered=time.time(),
                    first_triggered=time.time(),
                )
            )

        connector_stat = self.data.connectors.connector_stats[connector_name]
        connector_stat.trigger_count += 1
        connector_stat.last_triggered = time.time()

        logger.debug(
            f"Tracked connector '{connector_name}' usage: {connector_stat.trigger_count}"
        )

    # Chatbot Statistics Methods

    def increment_commands_triggered(
        self, username: Optional[str] = None, command_name: str = ""
    ):
        """Increment chatbot commands triggered count"""
        self.data.chatbot.commands_triggered += 1
        self.data.chatbot.total_interactions += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_chatbot(username, "commands_triggered")
            # Track per-user individual command usage if both username and command_name provided
            if command_name:
                self._track_user_command(username, command_name)

        # Track individual command if name provided
        if command_name:
            self._track_command(command_name)

        if username:
            self._record_event(
                username, "chatbot_command", 1.0, command_name or ""
            )

        logger.debug(
            f"Commands triggered incremented to: {self.data.chatbot.commands_triggered}"
        )

    def increment_events_triggered(
        self, username: Optional[str] = None, event_name: str = ""
    ):
        """Increment chatbot events triggered count"""
        self.data.chatbot.events_triggered += 1
        self.data.chatbot.total_interactions += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_chatbot(username, "events_triggered")
            # Track per-user individual event usage if both username and event_name provided
            if event_name:
                self._track_user_event(username, event_name)

        # Track individual event if name provided
        if event_name:
            self._track_event(event_name)

        if username:
            self._record_event(username, "chatbot_event", 1.0, event_name or "")

        logger.debug(
            f"Events triggered incremented to: {self.data.chatbot.events_triggered}"
        )

    def _track_user_quote(self, username: str, quote_id: str):
        """Helper method to track per-user quote statistics"""
        if username not in self.data.quotes.user_stats:
            self.data.quotes.user_stats[username] = UserQuoteStatistics()

        user_stats = self.data.quotes.user_stats[username]
        current_time = time.time()

        # Update individual quote usage
        if quote_id not in user_stats.individual_quote_usage:
            user_stats.individual_quote_usage[quote_id] = 0
        user_stats.individual_quote_usage[quote_id] += 1

        # Update total quotes redeemed and timestamps
        user_stats.total_quotes_redeemed += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if user_stats.total_quotes_redeemed == 1:
            user_stats.first_seen = current_time

    # Quote Statistics Methods

    def increment_quote_redeemed(self, quote_id: str, username: Optional[str] = None):
        """Increment quote redeemed count for specific quote"""
        if quote_id not in self.data.quotes.individual_quote_usage:
            self.data.quotes.individual_quote_usage[quote_id] = 0
        self.data.quotes.individual_quote_usage[quote_id] += 1
        self.data.quotes.total_quotes_redeemed += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_quote(username, quote_id)
            self._record_event(username, "quote_redeem", 1.0, str(quote_id))

        logger.debug(
            f"Quote {quote_id} redeemed. Total: {self.data.quotes.total_quotes_redeemed}"
        )

    # Giveaway statistics

    def record_giveaway_entry(self, username: Optional[str] = None):
        """Count a successful giveaway pool entry (chat keyword match)."""
        self.data.giveaways.total_entry_events += 1
        if username:
            self._record_event(username, "giveaway_entry", 1.0, "")
        logger.debug(
            "Giveaway entry recorded. Total entries: %s",
            self.data.giveaways.total_entry_events,
        )

    def record_giveaway_winner(self, username: str):
        """Increment lifetime wins for a display name."""
        if not username:
            return
        d = self.data.giveaways.user_wins
        d[username] = d.get(username, 0) + 1
        self._record_event(username, "giveaway_win", 1.0, "")
        logger.debug("Giveaway win recorded for %s", username)

    def record_giveaway_round_complete(self):
        """Count one completed draw (Draw winners click)."""
        self.data.giveaways.giveaways_completed += 1
        self._record_event(
            self._STATS_SYSTEM_USERNAME,
            "giveaway_draw_complete",
            1.0,
            "",
        )
        logger.debug(
            "Giveaway round complete. Total: %s",
            self.data.giveaways.giveaways_completed,
        )

    def _track_user_chat(self, username: str):
        """Helper method to track per-user chat statistics"""
        if username not in self.data.chat.user_stats:
            self.data.chat.user_stats[username] = UserChatStatistics()

        user_stats = self.data.chat.user_stats[username]
        current_time = time.time()

        # Update chat statistics
        user_stats.twitch_messages_received += 1
        user_stats.total_messages += 1
        user_stats.last_seen = current_time

        # Set first_seen if this is the first time
        if user_stats.total_messages == 1:
            user_stats.first_seen = current_time

    # Chat Statistics Methods
    def increment_twitch_messages(self, username: Optional[str] = None):
        """Increment Twitch messages received count"""
        self.data.chat.twitch_messages_received += 1
        self.data.chat.total_messages += 1

        # Track per-user statistics if username provided
        if username:
            self._track_user_chat(username)
            self._record_event(username, "chat_message", 1.0, "")

        logger.info(
            f"Twitch messages incremented to: {self.data.chat.twitch_messages_received} (total: {self.data.chat.total_messages})"
        )

    # Template Statistics Methods
    def submit_template_stat(
        self,
        template_name: str,
        stat_name: str,
        stat_value: Any,
        increment: bool = False,
    ):
        """
        Submit a custom statistic from a template

        Args:
            template_name: Name of the template submitting the stat
            stat_name: Name of the statistic
            stat_value: Value of the statistic
            increment: If True, increment existing value by stat_value, otherwise set to stat_value
        """
        try:
            # Ensure template entry exists
            if template_name not in self.data.templates:
                self.data.templates[template_name] = TemplateStatistics(
                    template_name=template_name
                )

            template_stats = self.data.templates[template_name]

            # Handle different data types and increment logic
            if increment:
                # Increment existing value
                if stat_name in template_stats.custom_stats:
                    current_value = template_stats.custom_stats[stat_name]
                    if isinstance(current_value, (int, float)) and isinstance(
                        stat_value, (int, float)
                    ):
                        template_stats.custom_stats[stat_name] = (
                            current_value + stat_value
                        )
                    elif isinstance(current_value, list) and isinstance(
                        stat_value, list
                    ):
                        template_stats.custom_stats[stat_name] = (
                            current_value + stat_value
                        )
                    else:
                        # For non-numeric types, just set the new value
                        template_stats.custom_stats[stat_name] = stat_value
                else:
                    # First time setting this stat
                    template_stats.custom_stats[stat_name] = stat_value
            else:
                # Set the value directly
                template_stats.custom_stats[stat_name] = stat_value

            # Update last modified time
            template_stats.last_updated = time.time()

            logger.debug(
                f"Template '{template_name}' stat '{stat_name}' updated to: {template_stats.custom_stats[stat_name]}"
            )
            return True

        except Exception as e:
            logger.error(f"Error submitting template stat: {e}", exc_info=True)
            return False

    def get_template_stats(self, template_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get template statistics

        Args:
            template_name: Specific template name, or None for all templates

        Returns:
            Dictionary of template statistics
        """
        if template_name:
            if template_name in self.data.templates:
                template_stats = self.data.templates[template_name]
                return {
                    "template_name": template_stats.template_name,
                    "custom_stats": template_stats.custom_stats.copy(),
                    "last_updated": template_stats.last_updated,
                }
            else:
                return {}
        else:
            # Return all template stats
            result = {}
            for name, stats in self.data.templates.items():
                result[name] = {
                    "template_name": stats.template_name,
                    "custom_stats": stats.custom_stats.copy(),
                    "last_updated": stats.last_updated,
                }
            return result

    def increment_template_counter(
        self, template_name: str, counter_name: str, increment: int = 1
    ):
        """
        Convenience method to increment a template counter

        Args:
            template_name: Name of the template
            counter_name: Name of the counter to increment
            increment: Amount to increment by (default: 1)
        """
        return self.submit_template_stat(
            template_name, counter_name, increment, increment=True
        )

    def reset_template_stats(
        self, template_name: Optional[str] = None, stat_name: Optional[str] = None
    ):
        """
        Reset template statistics

        Args:
            template_name: Specific template name, or None for all templates
            stat_name: Specific stat name, or None for all stats in the template
        """
        if template_name:
            if template_name in self.data.templates:
                if stat_name:
                    if stat_name in self.data.templates[template_name].custom_stats:
                        del self.data.templates[template_name].custom_stats[stat_name]
                        logger.info(
                            f"Reset template '{template_name}' stat '{stat_name}'"
                        )
                else:
                    self.data.templates[template_name].custom_stats.clear()
                    logger.info(f"Reset all stats for template '{template_name}'")
        else:
            # Reset all template stats
            self.data.templates.clear()
            logger.info("Reset all template statistics")

    def _get_connector_count(self) -> int:
        """Get the current connector count from connector_manager"""
        try:
            from . import connector_manager

            # Get the global connector manager instance
            if hasattr(connector_manager, "connector_manager"):
                return len(connector_manager.connector_manager.connectors)
            else:
                # If connector_manager is not initialized, return 0
                return 0
        except Exception as e:
            logger.debug(f"Could not get connector count: {e}")
            return 0

    def _get_commands_count(self) -> int:
        """Get the current commands count from chatbot_manager"""
        try:
            from . import chatbot_manager

            # Get the global chatbot manager instance
            manager = chatbot_manager.get_manager()

            # Check if manager has been loaded with data
            if not hasattr(manager, "commands") or manager.commands is None:
                logger.debug(" Commands count: 0 (chatbot manager not initialized)")
                return 0

            commands = manager.get_all_commands()
            count = len(commands)
            logger.debug(f" Commands count: {count}")
            return count
        except Exception as e:
            logger.error(f"Could not get commands count: {e}", exc_info=True)
            return 0

    def _get_events_count(self) -> int:
        """Get the current events count from chatbot_manager"""
        try:
            from . import chatbot_manager

            # Get the global chatbot manager instance
            manager = chatbot_manager.get_manager()

            # Check if manager has been loaded with data
            if not hasattr(manager, "events") or manager.events is None:
                logger.debug(" Events count: 0 (chatbot manager not initialized)")
                return 0

            count = len(manager.get_all_events())
            logger.debug(f" Events count: {count}")
            return count
        except Exception as e:
            logger.error(f"Could not get events count: {e}", exc_info=True)
            return 0

    def _get_quotes_count(self) -> int:
        """Get the current quotes count from chatbot_manager"""
        try:
            from . import chatbot_manager

            # Get the global chatbot manager instance
            manager = chatbot_manager.get_manager()

            # Check if manager has been loaded with data
            if not hasattr(manager, "quotes") or manager.quotes is None:
                logger.debug(" Quotes count: 0 (chatbot manager not initialized)")
                return 0

            count = len(manager.get_all_quotes())
            logger.debug(f" Quotes count: {count}")
            return count
        except Exception as e:
            logger.error(f"Could not get quotes count: {e}", exc_info=True)
            return 0

    # General Statistics Methods
    def get_all_statistics(self) -> Dict[str, Any]:
        """Get all statistics as a dictionary"""
        commands_count = self._get_commands_count()
        events_count = self._get_events_count()
        quotes_count = self._get_quotes_count()

        logger.debug(
            f"get_all_statistics: commands={commands_count}, events={events_count}, quotes={quotes_count}"
        )

        return {
            "alerts": {
                "bit_alerts_played": self.data.alerts.bit_alerts_played,
                "total_bits": self.data.alerts.total_bits,
                "resubs_played": self.data.alerts.resubs_played,
                "new_subs_played": self.data.alerts.new_subs_played,
                "gift_subs_played": self.data.alerts.gift_subs_played,
                "total_gift_subs": self.data.alerts.total_gift_subs,
                "follow_alerts_played": self.data.alerts.follow_alerts_played,
                "raids": self.data.alerts.raids,
                "point_alerts_redeemed": self.data.alerts.point_alerts_redeemed,
                "total_channel_points_redeemed": self.data.alerts.total_channel_points_redeemed,
            },
            "hype_trains": {
                "level_completions": self.data.hype_trains.level_completions.copy(),
                "total_completions": self.data.hype_trains.total_completions,
            },
            "connectors": {
                # Get connector count dynamically from connector_manager
                "connectors_created": self._get_connector_count(),
                "connectors_triggered": self.data.connectors.connectors_triggered,
                "total_triggers": self.data.connectors.total_triggers,
            },
            "chatbot": {
                # Get counts dynamically from chatbot_manager
                "commands_created": commands_count,
                "commands_triggered": self.data.chatbot.commands_triggered,
                "events_created": events_count,
                "events_triggered": self.data.chatbot.events_triggered,
                "total_interactions": self.data.chatbot.total_interactions,
            },
            "quotes": {
                # Get count dynamically from chatbot_manager
                "quotes_created": quotes_count,
                "total_quotes_redeemed": self.data.quotes.total_quotes_redeemed,
                "individual_quote_usage": self.data.quotes.individual_quote_usage,
            },
            "giveaways": {
                "giveaways_completed": self.data.giveaways.giveaways_completed,
                "total_entry_events": self.data.giveaways.total_entry_events,
                "average_entries_per_giveaway": (
                    self.data.giveaways.total_entry_events
                    / self.data.giveaways.giveaways_completed
                    if self.data.giveaways.giveaways_completed
                    else 0.0
                ),
                "user_wins": dict(self.data.giveaways.user_wins),
            },
            "chat": {
                "twitch_messages_received": self.data.chat.twitch_messages_received,
                "total_messages": self.data.chat.total_messages,
            },
            "templates": self.get_template_stats(),
            "session": {
                "start_time": self.data.session.start_time,
                "last_save_time": self.data.session.last_save_time,
                "session_duration": time.time() - self.data.session.start_time,
            },
        }

    def get_statistics_summary(self) -> Dict[str, Any]:
        """Get a summary of key statistics"""
        commands_count = self._get_commands_count()
        events_count = self._get_events_count()
        quotes_count = self._get_quotes_count()

        return {
            "total_alerts": (
                self.data.alerts.bit_alerts_played
                + self.data.alerts.resubs_played
                + self.data.alerts.new_subs_played
                + self.data.alerts.gift_subs_played
                + self.data.alerts.follow_alerts_played
                + self.data.alerts.raids
                + self.data.alerts.point_alerts_redeemed
            ),
            "total_hype_trains": self.data.hype_trains.total_completions,
            "total_connectors": self._get_connector_count(),
            "total_connector_triggers": self.data.connectors.total_triggers,
            "total_chatbot_items": (commands_count + events_count),
            "total_chatbot_interactions": self.data.chatbot.total_interactions,
            "total_quotes": quotes_count,
            "total_quote_redeems": self.data.quotes.total_quotes_redeemed,
            "total_chat_messages": self.data.chat.total_messages,
            "session_duration_hours": (time.time() - self.data.session.start_time)
            / 3600,
        }

    def reset_statistics(self, category: Optional[str] = None):
        """Reset statistics - optionally for specific category only"""
        if category is None:
            # Reset all statistics
            self.data = StatisticsData()
            logger.info("All statistics reset")
        elif category == "alerts":
            self.data.alerts = AlertStatistics()
            logger.info("Alert statistics reset")
        elif category == "hype_trains":
            self.data.hype_trains = HypeTrainStatistics()
            logger.info("Hype train statistics reset")
        elif category == "connectors":
            self.data.connectors = ConnectorStatistics()
            logger.info("Connector statistics reset")
        elif category == "chatbot":
            self.data.chatbot = ChatbotStatistics()
            logger.info("Chatbot statistics reset")
        elif category == "quotes":
            self.data.quotes = QuoteStatistics()
            logger.info("Quote statistics reset")
        elif category == "giveaways":
            self.data.giveaways = GiveawayStatistics()
            logger.info("Giveaway statistics reset")
        elif category == "chat":
            self.data.chat = ChatStatistics()
            logger.info("Chat statistics reset")

        # Save the reset data
        self._save_to_database()

    # Periodic Saving Methods
    def _periodic_save_thread(self):
        """Background thread for periodic saving"""
        logger.debug("Periodic save thread started")

        while self.is_running:
            try:
                import time

                time.sleep(self.save_interval)
                if self.is_running:  # Check again in case we were stopped during sleep
                    logger.debug("Periodic save triggered")
                    self._save_to_database()
                    logger.debug("Periodic statistics save completed")
            except Exception as e:
                logger.error(f"Error in periodic save thread: {e}")

    def start_periodic_saving_safe(self):
        """Start the periodic saving using a background thread"""
        if self.is_running:
            logger.warning("Statistics manager periodic saving is already running")
            return

        self.is_running = True
        logger.info("Starting statistics manager periodic saving")

        # Start the periodic saving thread immediately
        try:
            import threading

            self._periodic_thread = threading.Thread(
                target=self._periodic_save_thread,
                daemon=True,
                name="StatisticsPeriodicSave",
            )
            self._periodic_thread.start()
            logger.info("Started periodic statistics saving thread")
            self._needs_periodic_start = False
        except Exception as e:
            logger.error(f"Failed to start periodic saving thread: {e}")
            self.is_running = False
            self._needs_periodic_start = True

    def ensure_periodic_saving(self):
        """Ensure the periodic saving thread is running"""
        if self._needs_periodic_start or (
            self.is_running and self._periodic_thread is None
        ):
            logger.info("Ensuring periodic saving is running...")
            self.start_periodic_saving_safe()

    def start_periodic_saving(self):
        """Start the periodic saving task (deprecated - use start_periodic_saving_safe)"""
        return self.start_periodic_saving_safe()

    def stop_periodic_saving(self):
        """Stop the periodic saving thread"""
        if self.is_running:
            self.is_running = False
            if self._periodic_thread and self._periodic_thread.is_alive():
                # Thread will stop on next iteration since self.is_running is now False
                logger.info("Stopping periodic statistics saving thread")
            self._periodic_thread = None
            logger.info("Stopped periodic statistics saving")

    def save_on_close(self):
        """Save statistics on application close"""
        try:
            logger.info(" Saving statistics on application close...")
            self._save_to_database()
            logger.info(" Statistics successfully saved on application close")
        except Exception as e:
            logger.error(f"Failed to save statistics on close: {str(e)}", exc_info=True)
            # Try one more time with a shorter timeout/context
            try:
                logger.info(" Attempting emergency save...")
                time.sleep(0.1)  # Brief pause
                self._save_to_database()
                logger.info(" Emergency statistics save succeeded")
            except Exception as emergency_error:
                logger.error(
                    f"Emergency statistics save also failed: {str(emergency_error)}",
                    exc_info=True,
                )
        finally:
            # Close statistics database connections
            self._close_statistics_db()

    def force_save(self):
        """Force an immediate save of statistics"""
        logger.info("Force save requested")
        self._save_to_database()
        return self.get_all_statistics()

    def get_user_statistics(self, username: str) -> Dict[str, Any]:
        """Get all statistics for a specific user"""
        display_name = str(username).strip() if username else ""
        user_stats = {
            "username": display_name,
            "alerts": {},
            "connectors": {},
            "chatbot": {},
            "quotes": {},
            "giveaways": {},
            "chat": {},
        }

        # Get user alert statistics
        k_alerts = self._dict_key_case_insensitive(self.data.alerts.user_stats, display_name)
        if k_alerts:
            user_stats["username"] = k_alerts
            alert_stats = self.data.alerts.user_stats[k_alerts]
            user_stats["alerts"] = {
                "bit_alerts_played": alert_stats.bit_alerts_played,
                "resubs_played": alert_stats.resubs_played,
                "new_subs_played": alert_stats.new_subs_played,
                "gift_subs_played": alert_stats.gift_subs_played,
                "follow_alerts_played": alert_stats.follow_alerts_played,
                "raids": alert_stats.raids,
                "point_alerts_redeemed": alert_stats.point_alerts_redeemed,
                "donations": alert_stats.donations,
                "total_alerts": alert_stats.total_alerts,
                "first_seen": alert_stats.first_seen,
                "last_seen": alert_stats.last_seen,
            }

        # Get user connector statistics (aggregate table or per-connector usage table)
        k_conn = self._dict_key_case_insensitive(self.data.connectors.user_stats, display_name)
        k_ucc = self._dict_key_case_insensitive(
            self.data.connectors.user_connector_stats, display_name
        )
        if k_conn:
            user_stats["username"] = k_conn
            conn_stats = self.data.connectors.user_stats[k_conn]
            user_stats["connectors"] = {
                "connectors_triggered": conn_stats.connectors_triggered,
                "total_triggers": conn_stats.total_triggers,
                "first_seen": conn_stats.first_seen,
                "last_seen": conn_stats.last_seen,
            }
        elif k_ucc:
            user_stats["username"] = k_ucc
            ucc = self.data.connectors.user_connector_stats[k_ucc]
            user_stats["connectors"] = {
                "connectors_triggered": ucc.connectors_triggered,
                "total_triggers": ucc.total_triggers,
                "first_seen": ucc.first_seen,
                "last_seen": ucc.last_seen,
            }

        # Get user chatbot statistics
        k_bot = self._dict_key_case_insensitive(self.data.chatbot.user_stats, display_name)
        if k_bot:
            user_stats["username"] = k_bot
            bot_stats = self.data.chatbot.user_stats[k_bot]
            user_stats["chatbot"] = {
                "commands_triggered": bot_stats.commands_triggered,
                "events_triggered": bot_stats.events_triggered,
                "total_interactions": bot_stats.total_interactions,
                "first_seen": bot_stats.first_seen,
                "last_seen": bot_stats.last_seen,
            }

        # Get user quote statistics
        k_quote = self._dict_key_case_insensitive(self.data.quotes.user_stats, display_name)
        if k_quote:
            user_stats["username"] = k_quote
            quote_stats = self.data.quotes.user_stats[k_quote]
            user_stats["quotes"] = {
                "total_quotes_redeemed": quote_stats.total_quotes_redeemed,
                "individual_quote_usage": quote_stats.individual_quote_usage.copy(),
                "first_seen": quote_stats.first_seen,
                "last_seen": quote_stats.last_seen,
            }

        k_gw = self._dict_key_case_insensitive(self.data.giveaways.user_wins, display_name)
        if k_gw:
            user_stats["username"] = k_gw
            user_stats["giveaways"] = {
                "giveaway_wins": self.data.giveaways.user_wins[k_gw],
            }

        # Get user chat statistics
        k_chat = self._dict_key_case_insensitive(self.data.chat.user_stats, display_name)
        if k_chat:
            user_stats["username"] = k_chat
            chat_stats = self.data.chat.user_stats[k_chat]
            user_stats["chat"] = {
                "twitch_messages_received": chat_stats.twitch_messages_received,
                "total_messages": chat_stats.total_messages,
                "first_seen": chat_stats.first_seen,
                "last_seen": chat_stats.last_seen,
            }

        return user_stats

    def get_top_users_by_statistic(
        self, stat_type: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top users ranked by a specific statistic

        Args:
            stat_type: Type of statistic (e.g., 'total_alerts', 'bits_donated', 'quotes_redeemed')
            limit: Maximum number of users to return

        Returns:
            List of dictionaries with username and stat value, sorted by value descending
        """
        users = []

        if stat_type == "total_alerts":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.total_alerts,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "bits_donated":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.bit_alerts_played,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "subs_gifted":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.gift_subs_played,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "donations":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.donations,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "points_redeemed":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.point_alerts_redeemed,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "quotes_redeemed":
            for username, stats in self.data.quotes.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.total_quotes_redeemed,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "chat_messages":
            for username, stats in self.data.chat.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.total_messages,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "connector_triggers":
            for username, stats in self.data.connectors.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.total_triggers,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "chatbot_interactions":
            for username, stats in self.data.chatbot.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "value": stats.total_interactions,
                        "first_seen": stats.first_seen,
                        "last_seen": stats.last_seen,
                    }
                )

        elif stat_type == "giveaway_wins":
            for username, wins in self.data.giveaways.user_wins.items():
                users.append(
                    {
                        "username": username,
                        "value": wins,
                        "first_seen": 0.0,
                        "last_seen": 0.0,
                    }
                )

        # Sort by value descending and return top limit
        users.sort(key=lambda x: x["value"], reverse=True)
        return users[:limit]

    def get_recent_users(self, stat_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most recent users for a specific statistic type

        Args:
            stat_type: Type of statistic (e.g., 'alerts', 'chat', 'connectors')
            limit: Maximum number of users to return

        Returns:
            List of dictionaries with username and last_seen timestamp, sorted by recency
        """
        users = []

        if stat_type == "alerts":
            for username, stats in self.data.alerts.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "last_seen": stats.last_seen,
                        "first_seen": stats.first_seen,
                    }
                )

        elif stat_type == "chat":
            for username, stats in self.data.chat.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "last_seen": stats.last_seen,
                        "first_seen": stats.first_seen,
                    }
                )

        elif stat_type == "connectors":
            for username, stats in self.data.connectors.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "last_seen": stats.last_seen,
                        "first_seen": stats.first_seen,
                    }
                )

        elif stat_type == "quotes":
            for username, stats in self.data.quotes.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "last_seen": stats.last_seen,
                        "first_seen": stats.first_seen,
                    }
                )

        elif stat_type == "chatbot":
            for username, stats in self.data.chatbot.user_stats.items():
                users.append(
                    {
                        "username": username,
                        "last_seen": stats.last_seen,
                        "first_seen": stats.first_seen,
                    }
                )

        # Sort by last_seen descending (most recent first)
        users.sort(key=lambda x: x["last_seen"], reverse=True)
        return users[:limit]

    def get_user_activity_summary(self, username: str) -> Dict[str, Any]:
        """Get a summary of user activity across all categories"""
        user_stats = self.get_user_statistics(username)

        summary = {
            "username": username,
            "total_activity_score": 0,
            "categories_active": 0,
            "most_recent_activity": 0,
            "first_seen": float("inf"),
            "last_seen": 0,
        }

        # Calculate activity score and find most recent activity
        for category, stats in user_stats.items():
            if category == "username":
                continue

            if isinstance(stats, dict) and stats:
                summary["categories_active"] += 1

                # Add to activity score based on different metrics
                if category == "alerts":
                    summary["total_activity_score"] += stats.get("total_alerts", 0) * 10
                elif category == "chat":
                    summary["total_activity_score"] += (
                        stats.get("total_messages", 0) * 1
                    )
                elif category == "quotes":
                    summary["total_activity_score"] += (
                        stats.get("total_quotes_redeemed", 0) * 5
                    )
                elif category == "connectors":
                    summary["total_activity_score"] += (
                        stats.get("total_triggers", 0) * 3
                    )
                elif category == "chatbot":
                    summary["total_activity_score"] += (
                        stats.get("total_interactions", 0) * 2
                    )

                # Track timestamps
                if "last_seen" in stats:
                    summary["most_recent_activity"] = max(
                        summary["most_recent_activity"], stats["last_seen"]
                    )
                if "first_seen" in stats:
                    summary["first_seen"] = min(
                        summary["first_seen"], stats["first_seen"]
                    )

        # Handle case where no activity found
        if summary["first_seen"] == float("inf"):
            summary["first_seen"] = 0

        return summary

    def get_top_commands(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top commands by usage count"""
        commands = []
        for command_name, stats in self.data.chatbot.command_stats.items():
            commands.append(
                {
                    "command_name": command_name,
                    "usage_count": stats.usage_count,
                    "first_used": stats.first_used,
                    "last_used": stats.last_used,
                }
            )

        commands.sort(key=lambda x: x["usage_count"], reverse=True)
        return commands[:limit]

    def get_top_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top events by trigger count"""
        events = []
        for event_name, stats in self.data.chatbot.event_stats.items():
            events.append(
                {
                    "event_name": event_name,
                    "trigger_count": stats.trigger_count,
                    "first_triggered": stats.first_triggered,
                    "last_triggered": stats.last_triggered,
                }
            )

        events.sort(key=lambda x: x["trigger_count"], reverse=True)
        return events[:limit]

    def get_top_connectors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top connectors by trigger count"""
        connectors = []
        for connector_name, stats in self.data.connectors.connector_stats.items():
            connectors.append(
                {
                    "connector_name": connector_name,
                    "trigger_count": stats.trigger_count,
                    "first_triggered": stats.first_triggered,
                    "last_triggered": stats.last_triggered,
                }
            )

        connectors.sort(key=lambda x: x["trigger_count"], reverse=True)
        return connectors[:limit]

    def get_top_alert_types(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top alert types by trigger count"""
        alert_types = []
        for alert_type, stats in self.data.alerts.alert_type_stats.items():
            alert_types.append(
                {
                    "alert_type": alert_type,
                    "alert_name": stats.alert_name,
                    "trigger_count": stats.trigger_count,
                    "first_triggered": stats.first_triggered,
                    "last_triggered": stats.last_triggered,
                }
            )

        alert_types.sort(key=lambda x: x["trigger_count"], reverse=True)
        return alert_types[:limit]

    def get_top_users_for_command(
        self, command_name: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top users who used a specific command the most"""
        users = []
        for username, stats in self.data.chatbot.user_command_stats.items():
            if command_name in stats.command_usage:
                users.append(
                    {
                        "username": username,
                        "usage_count": stats.command_usage[command_name],
                        "first_used": stats.first_seen,
                        "last_used": stats.last_seen,
                    }
                )

        users.sort(key=lambda x: x["usage_count"], reverse=True)
        return users[:limit]

    def get_top_users_for_event(
        self, event_name: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top users who triggered a specific event the most"""
        users = []
        for username, stats in self.data.chatbot.user_event_stats.items():
            if event_name in stats.event_usage:
                users.append(
                    {
                        "username": username,
                        "trigger_count": stats.event_usage[event_name],
                        "first_triggered": stats.first_seen,
                        "last_triggered": stats.last_seen,
                    }
                )

        users.sort(key=lambda x: x["trigger_count"], reverse=True)
        return users[:limit]

    def get_top_users_for_connector(
        self, connector_name: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top users who triggered a specific connector the most"""
        users = []
        for username, stats in self.data.connectors.user_connector_stats.items():
            if connector_name in stats.connector_usage:
                users.append(
                    {
                        "username": username,
                        "trigger_count": stats.connector_usage[connector_name],
                        "first_triggered": stats.first_seen,
                        "last_triggered": stats.last_seen,
                    }
                )

        users.sort(key=lambda x: x["trigger_count"], reverse=True)
        return users[:limit]

    def get_top_users_for_quote(
        self, quote_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top users who redeemed a specific quote the most"""
        users = []
        for username, stats in self.data.quotes.user_stats.items():
            if quote_id in stats.individual_quote_usage:
                users.append(
                    {
                        "username": username,
                        "usage_count": stats.individual_quote_usage[quote_id],
                        "first_used": stats.first_seen,
                        "last_used": stats.last_seen,
                    }
                )

        users.sort(key=lambda x: x["usage_count"], reverse=True)
        return users[:limit]

    def get_user_individual_usage(self, username: str) -> Dict[str, Any]:
        """Get detailed individual item usage for a specific user"""
        usage = {
            "username": username,
            "commands": {},
            "events": {},
            "connectors": {},
            "quotes": {},
        }

        # Get user's command usage
        if username in self.data.chatbot.user_command_stats:
            usage["commands"] = self.data.chatbot.user_command_stats[
                username
            ].command_usage.copy()

        # Get user's event usage
        if username in self.data.chatbot.user_event_stats:
            usage["events"] = self.data.chatbot.user_event_stats[
                username
            ].event_usage.copy()

        # Get user's connector usage
        if username in self.data.connectors.user_connector_stats:
            usage["connectors"] = self.data.connectors.user_connector_stats[
                username
            ].connector_usage.copy()

        # Get user's quote usage
        if username in self.data.quotes.user_stats:
            usage["quotes"] = self.data.quotes.user_stats[
                username
            ].individual_quote_usage.copy()

        return usage

    def get_saving_status(self):
        """Get the current status of periodic saving"""
        thread_alive = (
            self._periodic_thread is not None and self._periodic_thread.is_alive()
        )
        return {
            "is_running": self.is_running,
            "has_thread": thread_alive,
            "needs_start": self._needs_periodic_start,
            "save_interval": self.save_interval,
            "last_save_time": self.data.session.last_save_time
            if hasattr(self.data.session, "last_save_time")
            else None,
        }

    def check_data_integrity(self) -> List[str]:
        """Check data integrity and return list of issues found"""
        issues = []

        # Check hype train level_completions
        if not isinstance(self.data.hype_trains.level_completions, dict):
            issues.append(
                f"hype_trains.level_completions is {type(self.data.hype_trains.level_completions)} "
                f"instead of dict: {self.data.hype_trains.level_completions}"
            )
        else:
            # Check that all keys are integers and values are integers
            for k, v in self.data.hype_trains.level_completions.items():
                if not isinstance(k, int):
                    issues.append(
                        f"hype_trains.level_completions key {k} is {type(k)} instead of int"
                    )
                if not isinstance(v, int):
                    issues.append(
                        f"hype_trains.level_completions[{k}] value {v} is {type(v)} instead of int"
                    )

        return issues

    def repair_data_integrity(self) -> bool:
        """Attempt to repair data integrity issues. Returns True if repairs were made."""
        repairs_made = False

        # Fix hype train level_completions
        if not isinstance(self.data.hype_trains.level_completions, dict):
            logger.warning(
                f"Repairing hype_trains.level_completions: was {type(self.data.hype_trains.level_completions)}, "
                f"setting to empty dict"
            )
            self.data.hype_trains.level_completions = {}
            repairs_made = True

        # Clean up non-integer keys/values
        if isinstance(self.data.hype_trains.level_completions, dict):
            cleaned = {}
            for k, v in self.data.hype_trains.level_completions.items():
                try:
                    int_k = int(k)
                    int_v = int(v)
                    cleaned[int_k] = int_v
                except (ValueError, TypeError):
                    logger.warning(
                        f"Removing invalid hype_trains.level_completions entry: {k}={v}"
                    )
                    repairs_made = True

            if len(cleaned) != len(self.data.hype_trains.level_completions):
                self.data.hype_trains.level_completions = cleaned
                repairs_made = True

        if repairs_made:
            logger.info("Data integrity repairs completed")
            # Save the repaired data
            self.save_statistics()

        return repairs_made

    def set_save_interval(self, interval_seconds: int):
        """Set the periodic save interval"""
        self.save_interval = interval_seconds
        logger.info(f"Statistics save interval set to {interval_seconds} seconds")


# Global instance
_statistics_manager = None


def get_statistics_manager() -> StatisticsManager:
    """Get the global statistics manager instance"""
    global _statistics_manager
    if _statistics_manager is None:
        _statistics_manager = StatisticsManager()
    return _statistics_manager


def initialize_statistics():
    """Initialize the statistics manager"""
    manager = get_statistics_manager()
    # Don't start periodic saving here - it will be started when event loop is available
    return manager


def initialize_statistics_with_data(all_data: Dict[str, Any]):
    """Initialize the statistics manager with pre-loaded data"""
    manager = get_statistics_manager()
    manager.initialize_with_data(all_data)
    # Don't start periodic saving here - it will be started when event loop is available
    return manager


def start_statistics_saving():
    """Start periodic saving for the statistics manager (call after event loop is running)"""
    try:
        manager = get_statistics_manager()
        manager.start_periodic_saving_safe()
        logger.info("Statistics periodic saving started successfully")
        return manager
    except Exception as e:
        logger.error(f"Failed to start statistics saving: {e}")
        return None


def shutdown_statistics():
    """Shutdown the statistics manager"""
    global _statistics_manager
    print(" shutdown_statistics() called")

    if _statistics_manager:
        print(" Statistics manager found, shutting down...")
        try:
            _statistics_manager.stop_periodic_saving()
            _statistics_manager.save_on_close()
            _statistics_manager = None
            print(" Statistics manager shutdown complete")
        except Exception as e:
            print(f" Error during statistics manager shutdown: {str(e)}")
    else:
        print(" No statistics manager to shutdown")

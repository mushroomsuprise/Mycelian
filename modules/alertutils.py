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
import contextlib
import copy
import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union, ClassVar

from . import database_manager

logger = logging.getLogger(__name__)


_ALERT_ID_LOCK = threading.Lock()
_ALERT_ID_LAST_MS = 0
_ALERT_ID_COUNTER = 0


def new_alert_id(prefix: str = "Alert") -> str:
    """Return a unique alert id: prefix + millisecond timestamp + 3-digit counter.

    Two events in the same second (or millisecond) never share an id, so their
    stored records (Alerts/AlertStorage/<id>), feed rows and queued alerts stay
    distinct. Ids stay increasing within one process.
    """
    global _ALERT_ID_LAST_MS, _ALERT_ID_COUNTER
    with _ALERT_ID_LOCK:
        now_ms = int(time.time() * 1000)
        if now_ms <= _ALERT_ID_LAST_MS:
            now_ms = _ALERT_ID_LAST_MS
            _ALERT_ID_COUNTER += 1
            if _ALERT_ID_COUNTER > 999:
                now_ms += 1
                _ALERT_ID_COUNTER = 0
        else:
            _ALERT_ID_COUNTER = 0
        _ALERT_ID_LAST_MS = now_ms
        return f"{prefix}{now_ms}{_ALERT_ID_COUNTER:03d}"


def safe_alert_timestamp(value: Any, default: float = 0.0) -> float:
    """Coerce an alert timestamp to unix seconds. Never raises."""
    if value is None or value == "":
        return default
    if value == "now":
        return time.time()
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_trim_progress_badge(
    progress: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    """Return (badge_text, tier) while a trim is in progress, else None."""
    if not progress or not progress.get("active"):
        return None
    phase = str(progress.get("phase") or "")
    if phase == "calculating":
        return ("Calculating", "info")
    if phase == "deleting":
        try:
            to_delete = max(0, int(progress.get("to_delete") or 0))
        except (TypeError, ValueError):
            to_delete = 0
        try:
            deleted = max(0, int(progress.get("deleted") or 0))
        except (TypeError, ValueError):
            deleted = 0
        if deleted <= 0:
            return (f"{to_delete} to delete", "warning")
        return (f"{deleted} of {to_delete}", "warning")
    return None


@dataclass
class AlertSettings:
    global_delay: int = 3
    alert_types: ClassVar[Tuple[str, ...]] = (
        "sub",
        "bit",
        "follow",
        "point",
        "raid",
        "giftsub",
        "donation",
        "streak",
        "hype_train_start",
        "hype_train_progress",
        "hype_train_end",
    )
    default_bit_alert: str = "bit1"
    default_sub_alert: str = "sub1"
    default_follow_alert: str = "follow1"
    default_point_alert: str = "point1"
    default_raids_alert: str = "raid1"
    default_giftsub_alert: str = "giftsub1"
    default_donation_alert: str = "donation1"
    default_streak_alert: str = "streaks1"
    FALLBACK_ALERT_ID: str = "subs_fallback"


# Alerts tab uses plural keys; Twitch/overlay use singular runtime types.
UI_TAB_TO_RUNTIME_ALERT_TYPE = {
    "bits": "bit",
    "subs": "sub",
    "streaks": "streak",
    "giftsubs": "giftsub",
    "donations": "donation",
    "raids": "raid",
    "follows": "follow",
    "points": "point",
}


def ui_tab_to_runtime_alert_type(ui_tab: str) -> str:
    """Map Alerts settings tab id to runtime alert_type (bit, follow, …)."""
    return UI_TAB_TO_RUNTIME_ALERT_TYPE.get(ui_tab, ui_tab)


# Global alert collections
BitAlerts = {}
BitRangeAlerts = {}
SubAlerts = {}
SubRangeAlerts = {}
GiftsubAlerts = {}
GiftsubRangeAlerts = {}
DonationAlerts = {}
DonationRangeAlerts = {}
RaidAlerts = {}
RaidRangeAlerts = {}
PointAlerts = {}
FollowAlerts = {}
StreakAlerts = {}
StreakRangeAlerts = {}


class _ConfigWriteFailed(RuntimeError):
    """The database reported a queued alert-config write as failed."""


def _notify_history_store_failed(alert_id: str, reason: str = "") -> None:
    """An alert played but its Activity Feed entry wasn't saved (background, deduped)."""
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "Alerts still play, but new ones may be missing from the Activity Feed after a restart.",
            type="warning",
            source="activity_feed",
            title="Activity Feed history couldn't be saved",
            hint="Check your database connection in Settings → Database.",
            details=f"alert_id={alert_id}" + (f"\n{reason}" if reason else ""),
            actions=nav_actions_settings("Database"),
            dedupe_key="activity:store_failed",
            dedupe_cooldown_sec=1800.0,
        )
    except Exception as e:
        logger.debug("Could not report the Activity Feed save failure: %s", e)


# Alert state manager class to handle alert state synchronization
class AlertStateManager:
    """Manages the state of all alert configurations and provides methods to access and update them.

    This class serves as the central source of truth for all alert configurations,
    handling both the in-memory state and synchronization with Firebase.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._alert_state = {
            "bits": {},
            "bit_ranges": {},
            "subs": {},
            "sub_ranges": {},
            "giftsubs": {},
            "giftsub_ranges": {},
            "donations": {},
            "donation_ranges": {},
            "raids": {},
            "raid_ranges": {},
            "points": {},
            "follows": {},
            "streaks": {},
            "streak_ranges": {},
        }
        # Add alert storage for completed alerts
        self._alert_storage = {}
        self._alert_storage_loaded = False
        # Newest-first id index over _alert_storage, rebuilt only after it changes.
        self._storage_version = 0
        self._sorted_ids_cache: Optional[List[str]] = None
        self._sorted_ids_key: Any = None

        self._alert_paths = {
            "bits": "Alerts/BitAlerts",
            "bit_ranges": "Alerts/BitRangeAlerts",
            "subs": "Alerts/SubAlerts",
            "sub_ranges": "Alerts/SubRangeAlerts",
            "giftsubs": "Alerts/GiftsubAlerts",
            "giftsub_ranges": "Alerts/GiftsubRangeAlerts",
            "donations": "Alerts/DonationAlerts",
            "donation_ranges": "Alerts/DonationRangeAlerts",
            "raids": "Alerts/RaidAlerts",
            "raid_ranges": "Alerts/RaidRangeAlerts",
            "points": "Alerts/PointAlerts",
            "follows": "Alerts/FollowAlerts",
            "streaks": "Alerts/StreakAlerts",
            "streak_ranges": "Alerts/StreakRangeAlerts",
            "alert_storage": "Alerts/AlertStorage",
        }
        self._initialized = False
        self._changes_pending = False
        # Why the last save_alert / delete_alert returned False ("unreadable",
        # "write_failed" or exception text), so the Alerts tab can explain it.
        self.last_write_error: str = ""
        self._resub_fallback_enabled: Optional[bool] = None
        self._startup_trim_done = False
        self._trim_lock = threading.Lock()
        self._trim_cancel = threading.Event()
        self._trim_pending = False
        self._trim_thread: Optional[threading.Thread] = None
        self._trim_progress: Dict[str, Any] = {
            "active": False,
            "phase": "idle",
            "to_delete": 0,
            "deleted": 0,
        }
        # Config collections whose last database read succeeded, empty ones
        # included, so an empty collection is not re-read on every alert.
        # Writes from outside this manager drop the affected keys (see
        # _on_database_write), which brings back today's re-read for them.
        self._loaded_lock = threading.Lock()
        self._loaded_state_keys: set = set()
        # Collections whose read failed: never written back until a read works.
        self._unreadable_state_keys: set = set()
        self._config_write_epoch = 0
        self._config_writes_inflight = 0
        self._own_write_paths: Dict[str, int] = {}
        # One full AlertStorage download at a time; other callers wait for it.
        self._storage_load_event: Optional[threading.Event] = None
        # Config writes run in mutation order on one thread, after the alert
        # lock is released, so readers never wait on database I/O.
        self._write_queue: "queue.Queue" = queue.Queue()
        self._writer_lock = threading.Lock()
        self._writes_idle = threading.Condition(self._writer_lock)
        self._pending_writes = 0
        self._writer_thread: Optional[threading.Thread] = None
        try:
            database_manager.database_manager.add_write_listener(
                self._on_database_write
            )
        except Exception as e:
            logger.debug("Could not watch alert collection writes: %s", e)

    # ------------------------------------------------------------------
    # Config collection read tracking (DBX-02)
    # ------------------------------------------------------------------
    def _config_state_keys(self) -> List[str]:
        return [key for key in self._alert_paths if key != "alert_storage"]

    def _config_keys_for_path(self, path: str) -> List[str]:
        if path == "":
            return self._config_state_keys()
        if path != "Alerts" and not path.startswith("Alerts/"):
            return []
        keys = []
        for key, collection in self._alert_paths.items():
            if key == "alert_storage":
                continue
            if (
                path == collection
                or collection.startswith(path + "/")
                or path.startswith(collection + "/")
            ):
                keys.append(key)
        return keys

    def _on_database_write(self, path: str, done: bool) -> None:
        """database_manager write hook. Must stay quick and lock-light."""
        keys = self._config_keys_for_path(path)
        if not keys:
            return
        with self._loaded_lock:
            self._config_write_epoch += 1
            self._config_writes_inflight += -1 if done else 1
            if self._config_writes_inflight < 0:
                self._config_writes_inflight = 0
            if not self._own_write_paths.get(path):
                self._loaded_state_keys.difference_update(keys)

    def _config_read_token(self) -> Optional[int]:
        """Write epoch before a read; None while an alert collection write runs."""
        with self._loaded_lock:
            if self._config_writes_inflight:
                return None
            return self._config_write_epoch

    def _note_config_read(self, key: str, ok: bool, token: Optional[int]) -> None:
        with self._loaded_lock:
            if not ok:
                self._unreadable_state_keys.add(key)
                self._loaded_state_keys.discard(key)
                return
            self._unreadable_state_keys.discard(key)
            if (
                token is not None
                and not self._config_writes_inflight
                and token == self._config_write_epoch
            ):
                self._loaded_state_keys.add(key)

    def _read_config_paths(
        self, keys: List[str], *, parallel: bool = False
    ) -> Dict[str, Tuple[dict, bool, Optional[int]]]:
        """Read config collections (outside the alert lock): key -> (data, ok, token)."""

        def _one(key: str):
            token = self._config_read_token()
            data, ok = database_manager.get_data_checked(self._alert_paths[key])
            return key, (data or {}, bool(ok), token)

        if parallel and len(keys) > 1 and not self._is_sql_backend():
            with ThreadPoolExecutor(
                max_workers=min(8, len(keys)), thread_name_prefix="AlertPathLoad"
            ) as pool:
                return dict(pool.map(_one, keys))
        return dict(_one(key) for key in keys)

    def _lazy_load_config_keys(self, state_keys: List[str]) -> bool:
        """Load empty collections not yet read this session. True if state changed."""
        with self._lock:
            pending = [
                key
                for key in state_keys
                if key in self._alert_paths
                and key != "alert_storage"
                and not self._alert_state.get(key)
            ]
        if not pending:
            return False
        with self._loaded_lock:
            pending = [key for key in pending if key not in self._loaded_state_keys]
        if not pending:
            return False
        results = self._read_config_paths(pending)
        changed = False
        with self._lock:
            for key in pending:
                data, ok, token = results[key]
                if self._alert_state.get(key):
                    continue  # filled meanwhile by a save or another load
                if ok and data:
                    self._alert_state[key] = data
                    changed = True
                    logger.debug(
                        "Lazy-loaded %d alerts from %s",
                        len(data),
                        self._alert_paths[key],
                    )
                self._note_config_read(key, ok, token)
            if changed:
                self._update_global_collections()
        return changed

    def load_missing_alert_paths(self) -> None:
        """Read the alert collections the startup preload did not load.

        Overlay startup used to re-read every collection plus all stored
        alerts. Collections the preload read successfully are already current;
        only the others (range collections it skips, failed reads) are read.
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
                return
        with self._loaded_lock:
            missing = [
                key
                for key in self._config_state_keys()
                if key not in self._loaded_state_keys
            ]
        if not missing:
            return
        results = self._read_config_paths(missing, parallel=True)
        with self._lock:
            for key in missing:
                data, ok, token = results[key]
                if ok and data and not self._alert_state.get(key):
                    self._alert_state[key] = data
                    logger.debug(
                        f"Loaded {len(data)} alerts from {self._alert_paths[key]}"
                    )
                if ok or not self._alert_state.get(key):
                    self._note_config_read(key, ok, token)
            self._update_global_collections()

    def _recover_unreadable_collection(self, state_key: str) -> bool:
        """Before writing a whole collection whose load failed, read it again.

        Returns False (do not write) while it still cannot be read, so a
        failed load never replaces the stored collection with the in-memory
        one. On success the stored alerts are merged under the in-memory ones.
        """
        with self._loaded_lock:
            if state_key not in self._unreadable_state_keys:
                return True
        token = self._config_read_token()
        data, ok = database_manager.get_data_checked(self._alert_paths[state_key])
        if not ok:
            logger.error(
                "Not writing %s: it could not be read from the database",
                self._alert_paths[state_key],
            )
            return False
        with self._lock:
            merged = dict(data or {}) if isinstance(data, dict) else {}
            merged.update(self._alert_state.get(state_key) or {})
            self._alert_state[state_key] = merged
            self._update_global_collections()
        self._note_config_read(state_key, True, token)
        return True

    # ------------------------------------------------------------------
    # Ordered config writer (WE-25)
    # ------------------------------------------------------------------
    def _queue_config_writes(
        self, ops: List[tuple], state_key: str, quiet: bool = False
    ) -> dict:
        """Queue (kind, path, data) writes. Call with ``self._lock`` held, so
        queue order is mutation order. Returns a waiter for _wait_config_writes.

        ``quiet``: the caller reports a failed write itself, so the database
        layer's own "couldn't save" notice is skipped for these writes.
        """
        waiter = {"done": threading.Event(), "error": None, "quiet": quiet}
        with self._writer_lock:
            self._pending_writes += 1
            self._write_queue.put((ops, state_key, waiter))
            thread = self._writer_thread
            start = thread is None or not thread.is_alive()
            if start:
                thread = threading.Thread(
                    target=self._config_writer_loop,
                    name="AlertConfigWriter",
                    daemon=True,
                )
                self._writer_thread = thread
        if start:
            thread.start()
            try:
                from .shutdown import register_shutdown_flush

                register_shutdown_flush("alert_config_writes", self.flush_config_writes)
            except Exception as e:
                logger.debug("Could not register alert write flush: %s", e)
        return waiter

    def _config_writer_loop(self) -> None:
        while True:
            ops, state_key, waiter = self._write_queue.get()
            try:
                for kind, path, data in ops:
                    with self._loaded_lock:
                        self._own_write_paths[path] = (
                            self._own_write_paths.get(path, 0) + 1
                        )
                    quiet = getattr(database_manager, "quiet_notifications", None)
                    try:
                        with (
                            quiet()
                            if waiter.get("quiet") and quiet is not None
                            else contextlib.nullcontext()
                        ):
                            if kind == "delete":
                                ok = database_manager.delete_data(path)
                            else:
                                ok = database_manager.set_data(path, data)
                    except Exception as e:
                        waiter["error"] = e
                        ok = False
                    finally:
                        with self._loaded_lock:
                            left = self._own_write_paths.get(path, 1) - 1
                            if left > 0:
                                self._own_write_paths[path] = left
                            else:
                                self._own_write_paths.pop(path, None)
                    if not ok:
                        # The store may now differ from memory: re-read when empty.
                        with self._loaded_lock:
                            self._loaded_state_keys.discard(state_key)
                        if "failed" not in waiter:
                            problem = None
                            try:
                                problem = database_manager.last_write_problem()
                            except Exception:
                                pass
                            reason = ": ".join(str(p) for p in problem if p) if problem else ""
                            waiter["failed"] = f"{path} ({reason})" if reason else path
                    if waiter["error"] is not None:
                        break
            except Exception as e:
                waiter["error"] = e
            finally:
                waiter["done"].set()
                with self._writer_lock:
                    self._pending_writes -= 1
                    self._writes_idle.notify_all()

    def _wait_config_writes(self, waiter: dict) -> Optional[BaseException]:
        """Wait (hub-cooperatively on the overlay server) for queued writes.

        A write the database reported as failed (returned False) comes back as
        an error too, so callers never report a save that didn't happen.
        """
        done = waiter["done"]
        self._wait_event(done)
        if waiter["error"] is None and waiter.get("failed"):
            return _ConfigWriteFailed(f"Database write failed: {waiter['failed']}")
        return waiter["error"]

    @staticmethod
    def _wait_event(event: threading.Event) -> None:
        if database_manager._caller_is_overlay_hub():
            try:
                from gevent import sleep as _hub_sleep
            except ImportError:
                _hub_sleep = time.sleep
            while not event.is_set():
                _hub_sleep(0.01)
            return
        event.wait()

    def flush_config_writes(self, timeout: float = 4.5) -> bool:
        """Block until queued config writes finish (shutdown). True if idle."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._writer_lock:
            while self._pending_writes > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "Alert config writes still pending at shutdown: %d",
                        self._pending_writes,
                    )
                    return False
                self._writes_idle.wait(remaining)
        return True

    def _is_sql_backend(self) -> bool:
        try:
            config = database_manager.database_manager.get_config()
            return bool(config and config.database_type == "sql")
        except Exception:
            return False

    def initialize(self):
        """Initialize the alert state by loading all alerts from Firebase"""
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing alert state manager")
            self._load_alerts_from_firebase()

            # Run migration to fix any individual alert records
            try:
                self.migrate_individual_alerts_to_collections()
            except Exception as e:
                logger.warning(f"Alert migration failed, but continuing: {str(e)}")

            self._initialized = True
            logger.debug("Alert state manager initialized")
        self._run_startup_auto_trim()

    async def initialize_async(self):
        """Initialize the alert state by loading all alerts from Firebase asynchronously"""
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing alert state manager")
            await self._load_alerts_from_firebase_async()
            self._initialized = True
            logger.debug("Alert state manager initialized")
        self._run_startup_auto_trim()

    def initialize_with_data(self, all_data: Dict[str, Any]):
        """Initialize the alert state with pre-loaded data

        Args:
            all_data: Dictionary mapping database paths to their data
        """
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing alert state manager with pre-loaded data")

            # Clear existing state
            for key in self._alert_state:
                self._alert_state[key] = {}

            # Clear alert storage (lazy-loaded when activity feed requests history)
            self._alert_storage = {}
            self._alert_storage_loaded = False

            # Load alerts from pre-loaded data
            for state_key, firebase_path in self._alert_paths.items():
                if state_key == "alert_storage":
                    logger.debug(
                        "Skipped loading alert storage from pre-loaded data (lazy load)"
                    )
                else:
                    data = all_data.get(firebase_path, {})
                    self._alert_state[state_key] = data
                    logger.debug(f"Loaded {len(data)} alerts from {firebase_path}")
                    if firebase_path in all_data:
                        failed = database_manager.startup_read_failed(firebase_path)
                        self._note_config_read(
                            state_key, not failed, self._config_read_token()
                        )

            # Update the global alert collections
            self._update_global_collections()

            self._initialized = True
            logger.debug("Alert state manager initialized with pre-loaded data")
        self._run_startup_auto_trim()

    def get_resub_fallback_enabled(self) -> bool:
        """Check if the resub fallback alert is enabled.

        Returns:
            bool: True if the resub fallback alert is enabled, False otherwise.
        """
        if self._resub_fallback_enabled is not None:
            return self._resub_fallback_enabled

        try:
            data, ok = database_manager.get_data_checked(
                "Alerts/Settings/resub_fallback_enabled"
            )
            if not ok:
                # Not cached, so the next call reads it again.
                return False
            if isinstance(data, dict) and "enabled" in data:
                self._resub_fallback_enabled = bool(data["enabled"])
            else:
                self._resub_fallback_enabled = False
        except Exception as e:
            logger.error(
                f"Error reading resub fallback setting: {str(e)}", exc_info=True
            )
            self._resub_fallback_enabled = False

        return self._resub_fallback_enabled

    def set_resub_fallback_enabled(self, enabled: bool) -> bool:
        """Set the resub fallback alert enabled state.

        Args:
            enabled: Whether the resub fallback alert should be enabled.

        Returns:
            bool: True if the setting was saved successfully.
        """
        try:
            if not database_manager.set_data(
                "Alerts/Settings/resub_fallback_enabled", {"enabled": enabled}
            ):
                logger.error("Could not save the resub fallback setting")
                return False
            self._resub_fallback_enabled = enabled
            logger.debug(f"Resub fallback alert {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger.error(
                f"Error saving resub fallback setting: {str(e)}", exc_info=True
            )
            return False

    def _load_alerts_from_firebase(self):
        """Load all alerts from Firebase and update the state"""
        try:
            # Clear existing state
            for key in self._alert_state:
                self._alert_state[key] = {}

            # Clear alert storage
            self._alert_storage = {}
            self._alert_storage_loaded = False

            # Load alerts from Firebase
            for state_key, firebase_path in self._alert_paths.items():
                if state_key == "alert_storage":
                    self._alert_storage = self._fetch_alert_storage_from_database()
                    self._alert_storage_loaded = True
                    logger.debug(
                        f"Loaded {len(self._alert_storage)} stored alerts from individual records"
                    )
                else:
                    token = self._config_read_token()
                    data, ok = database_manager.get_data_checked(firebase_path)
                    data = data or {}
                    self._alert_state[state_key] = data
                    self._note_config_read(state_key, ok, token)
                    logger.debug(f"Loaded {len(data)} alerts from {firebase_path}")

            # Update the global alert collections
            self._update_global_collections()

            logger.debug("Loaded all alerts from Firebase")
        except Exception as e:
            logger.error(f"Error loading alerts from Firebase: {str(e)}", exc_info=True)

    def _invalidate_alert_storage_cache(self) -> None:
        """Drop cached completed alerts so the next read refetches from the database."""
        self._alert_storage_loaded = False
        self._alert_storage = {}

    def _is_firebase_backend(self) -> bool:
        try:
            config = database_manager.database_manager.get_config()
            return bool(config and config.database_type == "firebase")
        except Exception:
            return False

    def _ensure_alert_storage_loaded(self, max_alerts: Optional[int] = None) -> None:
        """Load completed alerts once per session (or after invalidation).

        Callers that arrive during the download wait for it instead of
        starting another one.
        """
        leader = True
        event = None
        with self._lock:
            if self._alert_storage_loaded:
                return
            if max_alerts is None:
                event = self._storage_load_event
                leader = event is None
                if leader:
                    event = threading.Event()
                    self._storage_load_event = event
        if not leader:
            self._wait_event(event)
            return
        try:
            fetched = self._fetch_alert_storage_from_database(max_alerts=max_alerts)
            if not isinstance(fetched, dict):
                fetched = {}
            with self._lock:
                if self._alert_storage_loaded:
                    return
                fetched.update(self._alert_storage)
                self._alert_storage = fetched
                self._alert_storage_loaded = True
        finally:
            if event is not None:
                with self._lock:
                    if self._storage_load_event is event:
                        self._storage_load_event = None
                event.set()

    def prefetch_alert_storage(self) -> None:
        """Load stored alerts on a background thread (once)."""
        with self._lock:
            if self._alert_storage_loaded or self._storage_load_event is not None:
                return

        def _run() -> None:
            try:
                self._ensure_alert_storage_loaded()
            except Exception as e:
                logger.debug("Stored alert prefetch failed: %s", e)

        threading.Thread(target=_run, name="AlertStoragePrefetch", daemon=True).start()

    def _fetch_alert_storage_from_database(
        self, max_alerts: Optional[int] = None
    ) -> dict:
        """Load completed alert records from the database.

        Returns:
            dict: Dictionary of alert_id -> alert_data
        """
        try:
            alert_storage: dict = {}
            fetched = database_manager.database_manager.fetch_alert_storage(max_alerts)
            if isinstance(fetched, dict):
                return fetched

            # Backend not open yet: keep the previous collection read.
            if self._is_firebase_backend() and max_alerts and max_alerts > 0:
                collection_data = database_manager.get_alert_storage_limited(
                    max_alerts
                )
                if collection_data:
                    alert_storage = collection_data
                    logger.debug(
                        "Loaded %d stored alerts via Firebase limit_to_last",
                        len(alert_storage),
                    )
            else:
                collection_data = database_manager.get_data("Alerts/AlertStorage") or {}
                if collection_data:
                    alert_storage = collection_data
                    logger.debug("Loaded alert storage from collection format")

            return alert_storage

        except Exception as e:
            logger.error(
                f"Error loading individual alert storage: {str(e)}", exc_info=True
            )
            return {}

    def _load_individual_alert_storage(self) -> dict:
        """Load individual alert storage records from the database (uncached)."""
        return self._fetch_alert_storage_from_database()

    async def _load_alerts_from_firebase_async(self):
        """Load all alerts from Firebase asynchronously and update the state"""
        try:
            # Clear existing state
            for key in self._alert_state:
                self._alert_state[key] = {}

            # Completed alerts are lazy-loaded to avoid downloading AlertStorage at init
            self._alert_storage = {}
            self._alert_storage_loaded = False

            paths = [
                p
                for k, p in self._alert_paths.items()
                if k != "alert_storage"
            ]

            logger.debug(f"Loading {len(paths)} alert paths in parallel")
            results = await database_manager.get_multiple_data_async(paths)

            for state_key, firebase_path in self._alert_paths.items():
                if state_key == "alert_storage":
                    continue
                data = results.get(firebase_path, {}) or {}
                self._alert_state[state_key] = data
                logger.debug(f"Loaded {len(data)} alerts from {firebase_path}")

            # Update the global alert collections
            self._update_global_collections()

            logger.debug("Loaded all alerts from Firebase")
        except Exception as e:
            logger.error(f"Error loading alerts from Firebase: {str(e)}", exc_info=True)

    def _filter_alert_obj_fields(self, alert_data: dict) -> dict:
        """Filter alert data to only include fields that are valid for AlertObj

        Args:
            alert_data (dict): The raw alert data dictionary

        Returns:
            dict: Filtered dictionary with only valid AlertObj fields
        """
        # Get all field names from the AlertObj dataclass
        from dataclasses import fields

        valid_fields = {field.name for field in fields(AlertObj)}

        # Filter the alert data to only include valid fields
        filtered_data = {
            key: value for key, value in alert_data.items() if key in valid_fields
        }

        return filtered_data

    def _update_global_collections(self):
        """Update the global alert collections from the current state"""
        global \
            BitAlerts, \
            BitRangeAlerts, \
            SubAlerts, \
            SubRangeAlerts, \
            GiftsubAlerts, \
            GiftsubRangeAlerts
        global \
            DonationAlerts, \
            DonationRangeAlerts, \
            RaidAlerts, \
            RaidRangeAlerts, \
            PointAlerts, \
            FollowAlerts, \
            StreakAlerts, \
            StreakRangeAlerts

        # Clear existing collections
        BitAlerts.clear()
        BitRangeAlerts.clear()
        SubAlerts.clear()
        SubRangeAlerts.clear()
        GiftsubAlerts.clear()
        GiftsubRangeAlerts.clear()
        DonationAlerts.clear()
        DonationRangeAlerts.clear()
        RaidAlerts.clear()
        RaidRangeAlerts.clear()
        PointAlerts.clear()
        FollowAlerts.clear()
        StreakAlerts.clear()
        StreakRangeAlerts.clear()

        # Update with new data, filtering out invalid fields
        for key, value in self._alert_state["bits"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            BitAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["bit_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            BitRangeAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["subs"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            SubAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["sub_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            SubRangeAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["giftsubs"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            GiftsubAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["giftsub_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            GiftsubRangeAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["donations"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            DonationAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["donation_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            DonationRangeAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["raids"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            RaidAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["raid_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            RaidRangeAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["points"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            PointAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["follows"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            FollowAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["streaks"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            StreakAlerts[key] = AlertObj(**filtered_value)

        for key, value in self._alert_state["streak_ranges"].items():
            filtered_value = self._filter_alert_obj_fields(value)
            StreakRangeAlerts[key] = AlertObj(**filtered_value)

        logger.debug("Updated global alert collections")

    def _get_state_and_firebase_keys(self, alert_type: str, alert_id: str = None):
        """Get the state key and Firebase path for an alert type and ID

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str, optional): The ID of the alert

        Returns:
            tuple: (state_key, firebase_path, is_range)
        """
        is_range = (
            alert_id
            and "-" in str(alert_id)
            and alert_type
            in ["bits", "subs", "giftsubs", "donations", "raids", "streaks"]
        )

        # Map alert type to state key
        type_map = {
            "bits": "bits" if not is_range else "bit_ranges",
            "subs": "subs" if not is_range else "sub_ranges",
            "giftsubs": "giftsubs" if not is_range else "giftsub_ranges",
            "donations": "donations" if not is_range else "donation_ranges",
            "raids": "raids" if not is_range else "raid_ranges",
            "streaks": "streaks" if not is_range else "streak_ranges",
            "follows": "follows",
            "points": "points",
        }

        if alert_type not in type_map:
            logger.error(f"Invalid alert type: {alert_type}")
            return None, None, is_range

        state_key = type_map[alert_type]
        firebase_path = self._alert_paths[state_key]

        if alert_id:
            firebase_path = f"{firebase_path}/{alert_id}"

        return state_key, firebase_path, is_range

    def get_alerts_for_type(self, alert_type: str) -> dict:
        """Get all alerts for a specific type from the state

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)

        Returns:
            dict: Dictionary of alerts for the specified type
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            return self.get_alerts_by_type(alert_type)
        except Exception as e:
            logger.error(
                f"Error getting alerts for type {alert_type}: {str(e)}",
                exc_info=True,
            )
            return {}

    def get_alert_by_id(self, alert_type: str, alert_id: str) -> dict:
        """Get a specific alert from the state

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert

        Returns:
            dict: The alert data or None if not found
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                state_key, _, _ = self._get_state_and_firebase_keys(
                    alert_type, alert_id
                )
                if not state_key:
                    return None

                return self._alert_state[state_key].get(alert_id)
            except Exception as e:
                logger.error(
                    f"Error getting alert {alert_id} for type {alert_type}: {str(e)}",
                    exc_info=True,
                )
                return None

    def save_alert(
        self,
        alert_type: str,
        alert_id: str,
        alert_data: dict,
        update_collection: bool = True,
        quiet_db_notices: bool = False,
    ) -> bool:
        """Save an alert to the state and sync with Firebase

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert
            alert_data (dict): The alert data to save
            update_collection (bool): Whether to update the collection after saving (default True)
            quiet_db_notices (bool): The caller reports a failure itself (see last_write_error)

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            state_key, firebase_path, _ = self._get_state_and_firebase_keys(
                alert_type, alert_id
            )
            self.last_write_error = ""
            if not state_key or not firebase_path:
                self.last_write_error = f"unknown alert type {alert_type!r}"
                return False
            if not self._recover_unreadable_collection(state_key):
                self.last_write_error = "unreadable"
                return False

            with self._lock:
                # Generate and store the display name
                alert_data_copy = copy.deepcopy(alert_data)
                alert_data_copy["display_name"] = self.get_display_name(
                    alert_type, alert_id, alert_data
                )

                # Update the state
                self._alert_state[state_key][alert_id] = alert_data_copy

                # Database writes run after the lock is released, in this order.
                # On Firebase the collection write already contains the alert,
                # so the separate child write is skipped (DBX-14).
                ops = []
                if not (update_collection and self._is_firebase_backend()):
                    ops.append(("set", firebase_path, copy.deepcopy(alert_data_copy)))

                # Update the collection with all alerts of this type (unless disabled for batch operations)
                if update_collection:
                    collection_path = self._alert_paths[state_key]
                    ops.append(
                        (
                            "set",
                            collection_path,
                            copy.deepcopy(self._alert_state[state_key]),
                        )
                    )

                    # Update the global collections
                    self._update_global_collections()

                waiter = self._queue_config_writes(ops, state_key, quiet=quiet_db_notices)

            error = self._wait_config_writes(waiter)
            if error is not None:
                raise error
            logger.debug(f"Saved alert {alert_id} for type {alert_type}")
            return True
        except Exception as e:
            logger.error(
                f"Error saving alert {alert_id} for type {alert_type}: {str(e)}",
                exc_info=True,
            )
            self.last_write_error = str(e) if isinstance(e, _ConfigWriteFailed) else f"{type(e).__name__}: {e}"
            return False

    def delete_alert(
        self, alert_type: str, alert_id: str, quiet_db_notices: bool = False
    ) -> bool:
        """Delete an alert from the state and sync with Firebase

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert
            quiet_db_notices (bool): The caller reports a failure itself (see last_write_error)

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            state_key, firebase_path, _ = self._get_state_and_firebase_keys(
                alert_type, alert_id
            )
            self.last_write_error = ""
            if not state_key or not firebase_path:
                self.last_write_error = f"unknown alert type {alert_type!r}"
                return False
            if not self._recover_unreadable_collection(state_key):
                self.last_write_error = "unreadable"
                return False

            with self._lock:
                # Remove from state if it exists
                if alert_id in self._alert_state[state_key]:
                    del self._alert_state[state_key][alert_id]

                # Delete from database - remove both individual alert and update the collection
                # (after the lock is released, in this order). On Firebase the
                # collection write already drops the child (DBX-14).
                ops = []
                if not self._is_firebase_backend():
                    ops.append(("delete", firebase_path, None))

                # Also update the collection with remaining alerts of this type
                collection_path = self._alert_paths[state_key]
                ops.append(
                    ("set", collection_path, copy.deepcopy(self._alert_state[state_key]))
                )

                # Update the global collections
                self._update_global_collections()

                waiter = self._queue_config_writes(ops, state_key, quiet=quiet_db_notices)

            error = self._wait_config_writes(waiter)
            if error is not None:
                raise error
            logger.debug(f"Deleted alert {alert_id} for type {alert_type}")
            return True
        except Exception as e:
            logger.error(
                f"Error deleting alert {alert_id} for type {alert_type}: {str(e)}",
                exc_info=True,
            )
            self.last_write_error = str(e) if isinstance(e, _ConfigWriteFailed) else f"{type(e).__name__}: {e}"
            return False

    def get_default_alert_id(self, alert_type: str) -> str:
        """Get the default alert ID for a specific type

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)

        Returns:
            str: The default alert ID for the specified type
        """
        try:
            # Map alert type to settings attribute
            attr_map = {
                "bits": "default_bit_alert",
                "subs": "default_sub_alert",
                "follows": "default_follow_alert",
                "points": "default_point_alert",
                "raids": "default_raids_alert",
                "giftsubs": "default_giftsub_alert",
                "donations": "default_donation_alert",
                "streaks": "default_streak_alert",
            }

            if alert_type not in attr_map:
                logger.error(f"Invalid alert type: {alert_type}")
                return None

            return getattr(AlertSettings, attr_map[alert_type])
        except Exception as e:
            logger.error(
                f"Error getting default alert ID for type {alert_type}: {str(e)}",
                exc_info=True,
            )
            return None

    def update_alert(self, alert_type: str, alert_id: str, alert_data: dict):
        """Update an alert in the alert state and sync with Firebase

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert
            alert_data (dict): The alert data
        """
        # This is now an alias for save_alert
        return self.save_alert(alert_type, alert_id, alert_data)

    def get_all_alerts(self):
        """Get all alerts from the state, including alert configurations and stored alerts

        Returns:
            dict: Dictionary with all alerts including 'alert_storage' key
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
        # Stored alerts load on first use (they are no longer fetched at startup).
        self._ensure_alert_storage_loaded()
        with self._lock:
            result = copy.deepcopy(self._alert_state)
            result["alert_storage"] = copy.deepcopy(self._alert_storage)
            return result

    def get_alerts_by_type(self, alert_type: str, include_ranges: bool = True):
        """Get alerts of a specific type from the state

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            include_ranges (bool): Whether to include range alerts

        Returns:
            dict: Dictionary with alerts of the specified type
        """
        try:
            with self._lock:
                if not self._initialized:
                    self.initialize()

            # Map alert type to state keys
            type_map = {
                "bits": ["bits", "bit_ranges"] if include_ranges else ["bits"],
                "subs": ["subs", "sub_ranges"] if include_ranges else ["subs"],
                "giftsubs": (
                    ["giftsubs", "giftsub_ranges"]
                    if include_ranges
                    else ["giftsubs"]
                ),
                "donations": (
                    ["donations", "donation_ranges"]
                    if include_ranges
                    else ["donations"]
                ),
                "raids": ["raids", "raid_ranges"] if include_ranges else ["raids"],
                "streaks": (
                    ["streaks", "streak_ranges"] if include_ranges else ["streaks"]
                ),
                "follows": ["follows"],
                "points": ["points"],
            }

            if alert_type not in type_map:
                logger.error(f"Invalid alert type: {alert_type}")
                return {}

            state_keys = type_map[alert_type]

            # Startup preload may omit range paths; load any empty collections
            # not read yet this session (outside the alert lock).
            self._lazy_load_config_keys(state_keys)

            # Combine alerts from all relevant state keys
            with self._lock:
                result = {}
                for key in state_keys:
                    result.update(copy.deepcopy(self._alert_state[key]))

            return result
        except Exception as e:
            logger.error(
                f"Error getting alerts by type from state: {str(e)}", exc_info=True
            )
            return {}

    def ensure_alert_paths_loaded(self, state_keys: List[str]) -> None:
        """Load specific alert collections from the database if they are empty."""
        self._lazy_load_config_keys(list(state_keys))
        with self._lock:
            self._update_global_collections()

    def reload_from_firebase(self):
        """Force a reload of all alerts from Firebase"""
        with self._lock:
            logger.debug("Reloading all alerts from Firebase")
            self._invalidate_alert_storage_cache()
            self._load_alerts_from_firebase()
            logger.debug("Reload completed")

    def reload_alert_type_from_firebase(
        self, alert_type: str, include_ranges: bool = True
    ) -> None:
        """Reload only the alert config paths for one settings tab (not AlertStorage)."""
        type_map = {
            "bits": ["bits", "bit_ranges"] if include_ranges else ["bits"],
            "subs": ["subs", "sub_ranges"] if include_ranges else ["subs"],
            "giftsubs": (
                ["giftsubs", "giftsub_ranges"] if include_ranges else ["giftsubs"]
            ),
            "donations": (
                ["donations", "donation_ranges"] if include_ranges else ["donations"]
            ),
            "raids": ["raids", "raid_ranges"] if include_ranges else ["raids"],
            "streaks": (
                ["streaks", "streak_ranges"] if include_ranges else ["streaks"]
            ),
            "follows": ["follows"],
            "points": ["points"],
        }
        state_keys = type_map.get(alert_type)
        if not state_keys:
            logger.error("reload_alert_type_from_firebase: invalid type %s", alert_type)
            return

        with self._lock:
            logger.debug(
                "Reloading alert paths for type %s: %s", alert_type, state_keys
            )
            for state_key in state_keys:
                if state_key not in self._alert_paths:
                    continue
                firebase_path = self._alert_paths[state_key]
                token = self._config_read_token()
                data, ok = database_manager.get_data_checked(firebase_path)
                if not ok and self._alert_state.get(state_key):
                    # Keep what is in memory rather than blanking it on a failed read.
                    continue
                self._alert_state[state_key] = data or {}
                self._note_config_read(state_key, ok, token)
            self._update_global_collections()

    def sync_to_firebase(self):
        """Force a sync of all alerts to Firebase"""
        try:
            # Never write collections that could not be read (or stored alerts
            # that are not loaded) over the database copy.
            for state_key in self._config_state_keys():
                if not self._recover_unreadable_collection(state_key):
                    return False
            self._ensure_alert_storage_loaded()

            with self._lock:
                logger.debug("Syncing all alerts to Firebase")

                ops = []
                for state_key, firebase_path in self._alert_paths.items():
                    if state_key == "alert_storage":
                        ops.append(
                            ("set", firebase_path, copy.deepcopy(self._alert_storage))
                        )
                    else:
                        ops.append(
                            (
                                "set",
                                firebase_path,
                                copy.deepcopy(self._alert_state[state_key]),
                            )
                        )
                waiter = self._queue_config_writes(ops, "")

            error = self._wait_config_writes(waiter)
            if error is not None:
                raise error
            logger.debug("Sync completed")
            self._changes_pending = False
            return True
        except Exception as e:
            logger.error(
                f"Error syncing alerts to Firebase: {str(e)}", exc_info=True
            )
            return False

    def update_alert_collections(self, alert_type: str) -> bool:
        """Update the alert collections for a specific alert type after batch operations

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            # Find the state key for this alert type
            state_key = None
            for key, path in self._alert_paths.items():
                if key.replace("_alerts", "") == alert_type:
                    state_key = key
                    break

            if not state_key:
                return False
            if not self._recover_unreadable_collection(state_key):
                return False

            with self._lock:
                # Update the collection with all alerts of this type
                collection_path = self._alert_paths[state_key]
                snapshot = copy.deepcopy(self._alert_state[state_key])

                # Update the global collections
                self._update_global_collections()

                waiter = self._queue_config_writes(
                    [("set", collection_path, snapshot)], state_key
                )

            error = self._wait_config_writes(waiter)
            if error is not None:
                raise error
            logger.debug(f"Updated collections for alert type {alert_type}")
            return True
        except Exception as e:
            logger.error(
                f"Error updating collections for {alert_type}: {str(e)}",
                exc_info=True,
            )
            return False

    def save_alert_by_id(self, alert_id: str) -> bool:
        """Save an alert by its ID to Firebase using the current data in state manager

        Args:
            alert_id (str): The ID of the alert to save (format: "bits100", "subs1", etc.)

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        # save_alert takes the lock itself and writes after releasing it.
        try:
            # Parse alert_id to determine alert type and ID
            alert_type, actual_alert_id = self._parse_alert_id(alert_id)
            if not alert_type:
                logger.error(f"Could not parse alert_id: {alert_id}")
                return False

            # Get the alert data from state
            alert_data = self.get_alert_by_id(alert_type, actual_alert_id)
            if not alert_data:
                logger.error(
                    f"Alert {actual_alert_id} not found for type {alert_type}"
                )
                return False

            # Save using existing save_alert method
            return self.save_alert(alert_type, actual_alert_id, alert_data)

        except Exception as e:
            logger.error(
                f"Error saving alert by ID {alert_id}: {str(e)}", exc_info=True
            )
            return False

    def update_alert_data(self, alert_id: str, alert_data: dict) -> bool:
        """Update alert data in the state manager and save to Firebase

        Args:
            alert_id (str): The ID of the alert to update (format: "bits100", "subs1", etc.)
            alert_data (dict): The alert data to update

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            # Parse alert_id to determine alert type and ID
            alert_type, actual_alert_id = self._parse_alert_id(alert_id)
            if not alert_type:
                logger.error(f"Could not parse alert_id: {alert_id}")
                return False

            # Save the alert data using existing save_alert method
            success = self.save_alert(alert_type, actual_alert_id, alert_data)

            if success:
                logger.debug(f"Successfully updated alert {alert_id}")
                return True
            else:
                logger.error(f"Failed to update alert {alert_id}")
                return False

        except Exception as e:
            logger.error(
                f"Error updating alert data for {alert_id}: {str(e)}", exc_info=True
            )
            return False

    def store_completed_alert(self, alert_id: str, alert_data: dict) -> bool:
        """Store a completed alert in the alert storage

        Args:
            alert_id (str): The ID of the completed alert
            alert_data (dict): The alert data to store (should be from AlertObj.__dict__)

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                complete_alert_data = self._normalize_alert_data_for_storage(alert_data)
                self._alert_storage[alert_id] = copy.deepcopy(complete_alert_data)
                self._note_storage_changed()
            except Exception as e:
                logger.error(
                    f"Error storing completed alert {alert_id}: {str(e)}", exc_info=True
                )
                return False

        try:
            if not database_manager.set_data(
                f"Alerts/AlertStorage/{alert_id}", complete_alert_data
            ):
                # (The database layer reports the failed write itself.)
                logger.error("Could not store completed alert %s", alert_id)
                return False

            logger.debug(
                f"Stored completed alert {alert_id} with data keys: {list(complete_alert_data.keys())}"
            )
            if "username" in complete_alert_data:
                logger.debug(
                    f"Stored alert {alert_id} - username: '{complete_alert_data['username']}'"
                )
            else:
                logger.warning(
                    f"Stored alert {alert_id} is missing username field!"
                )

            try:
                self.schedule_auto_trim_stored_alerts()
            except Exception as trim_err:
                logger.debug(
                    "Auto-trim after storing alert %s failed: %s", alert_id, trim_err
                )
            return True

        except Exception as e:
            logger.error(
                f"Error storing completed alert {alert_id}: {str(e)}", exc_info=True
            )
            _notify_history_store_failed(alert_id, f"{type(e).__name__}: {e}")
            return False

    def _read_alert_storage_trim_settings(self) -> Dict[str, Any]:
        """Load stored-alert retention settings from AppSettings."""
        defaults = {
            "enabled": False,
            "mode": "both",
            "keep_count": 500,
            "keep_days": 30,
        }
        try:
            from .dataobjects import state_manager

            settings = state_manager.get_app_settings()
            mode = str(
                getattr(settings, "alert_storage_trim_mode", "both") or "both"
            ).strip().lower()
            if mode not in ("quantity", "time", "both"):
                mode = "both"
            keep_count = int(getattr(settings, "alert_storage_keep_count", 500) or 500)
            keep_days = int(getattr(settings, "alert_storage_keep_days", 30) or 30)
            return {
                "enabled": bool(getattr(settings, "alert_storage_auto_trim", False)),
                "mode": mode,
                "keep_count": max(1, keep_count),
                "keep_days": max(1, keep_days),
            }
        except Exception as e:
            logger.debug("Could not read alert storage trim settings: %s", e)
            return defaults

    def _run_startup_auto_trim(self) -> None:
        if self._startup_trim_done:
            return
        self._startup_trim_done = True
        try:
            self.schedule_auto_trim_stored_alerts()
        except Exception as e:
            logger.debug("Startup stored-alert trim failed: %s", e)

    def _set_trim_progress(self, **fields: Any) -> None:
        with self._trim_lock:
            self._trim_progress.update(fields)

    def _reset_trim_progress(self) -> None:
        with self._trim_lock:
            self._trim_progress = {
                "active": False,
                "phase": "idle",
                "to_delete": 0,
                "deleted": 0,
            }

    def get_trim_progress(self) -> Dict[str, Any]:
        """Copy of the current background-trim progress (safe for other threads)."""
        with self._trim_lock:
            return dict(self._trim_progress)

    def cancel_alert_storage_trim(self) -> None:
        """Ask the trim worker to stop; hide progress immediately."""
        self._trim_cancel.set()
        with self._trim_lock:
            self._trim_pending = False
        self._reset_trim_progress()

    def schedule_auto_trim_stored_alerts(self) -> None:
        """Start a background trim if auto-trim is on. Never blocks the caller."""
        try:
            from .shutdown import is_shutdown_in_progress

            if is_shutdown_in_progress():
                return
        except Exception:
            pass
        settings = self._read_alert_storage_trim_settings()
        if not settings["enabled"]:
            return
        with self._trim_lock:
            thread = self._trim_thread
            if thread is not None and thread.is_alive():
                self._trim_pending = True
                return
            self._trim_cancel.clear()
            self._trim_pending = False
            thread = threading.Thread(
                target=self._trim_worker_loop,
                name="AlertStorageTrim",
                daemon=True,
            )
            self._trim_thread = thread
        thread.start()
        try:
            from .notification_engine import start_alert_trim_footer_poll

            start_alert_trim_footer_poll()
        except Exception:
            pass

    def _trim_worker_loop(self) -> None:
        try:
            while True:
                if self._trim_cancel.is_set():
                    break
                try:
                    deleted = self.maybe_auto_trim_stored_alerts()
                    if deleted:
                        logger.info(
                            "Auto-trimmed %d stored alert(s) in background", deleted
                        )
                except Exception as e:
                    logger.debug("Background stored-alert trim failed: %s", e)
                with self._trim_lock:
                    if self._trim_cancel.is_set() or not self._trim_pending:
                        self._trim_pending = False
                        break
                    self._trim_pending = False
        finally:
            restart = False
            with self._trim_lock:
                pending = self._trim_pending and not self._trim_cancel.is_set()
                self._trim_pending = False
                if threading.current_thread() is self._trim_thread:
                    self._trim_thread = None
                restart = pending
            self._reset_trim_progress()
            if restart:
                self.schedule_auto_trim_stored_alerts()

    def maybe_auto_trim_stored_alerts(self) -> int:
        """Trim stored alerts when auto-trim is enabled. Returns deleted count."""
        settings = self._read_alert_storage_trim_settings()
        if not settings["enabled"]:
            return 0
        return self.trim_stored_alerts(
            mode=settings["mode"],
            keep_count=settings["keep_count"],
            keep_days=settings["keep_days"],
        )

    def trim_stored_alerts(
        self,
        *,
        mode: str = "both",
        keep_count: int = 500,
        keep_days: int = 30,
    ) -> int:
        """Delete stored alerts by newest count and/or age. Returns number deleted."""
        mode = str(mode or "both").strip().lower()
        if mode not in ("quantity", "time", "both"):
            mode = "both"
        keep_count = max(1, int(keep_count or 1))
        keep_days = max(1, int(keep_days or 1))

        self._set_trim_progress(
            active=True, phase="calculating", to_delete=0, deleted=0
        )
        try:
            from .notification_engine import start_alert_trim_footer_poll

            start_alert_trim_footer_poll()
        except Exception:
            pass
        if self._trim_cancel.is_set():
            self._reset_trim_progress()
            return 0

        # A complete in-memory cache is authoritative (including tests).
        # Otherwise list timestamps from the backend so trim does not load every payload.
        if self._alert_storage_loaded:
            with self._lock:
                rows = [
                    (alert_id, safe_alert_timestamp((alert_data or {}).get("timestamp")))
                    for alert_id, alert_data in self._alert_storage.items()
                ]
        elif self._is_firebase_backend():
            # Firebase's meta listing downloads every stored alert anyway; load
            # them into memory once so later readers reuse the same download.
            self._ensure_alert_storage_loaded()
            with self._lock:
                rows = [
                    (
                        str(alert_id),
                        safe_alert_timestamp(alert_data.get("timestamp")),
                    )
                    for alert_id, alert_data in self._alert_storage.items()
                    if isinstance(alert_data, dict)
                ]
            rows.sort(key=lambda item: item[1], reverse=True)
        else:
            meta = None
            try:
                meta = database_manager.database_manager.list_alert_storage_meta()
            except Exception as meta_error:
                logger.debug("Alert storage meta list failed: %s", meta_error)
                meta = None
            if meta is None:
                self._ensure_alert_storage_loaded()
                with self._lock:
                    rows = [
                        (
                            alert_id,
                            safe_alert_timestamp((alert_data or {}).get("timestamp")),
                        )
                        for alert_id, alert_data in self._alert_storage.items()
                    ]
            else:
                rows = list(meta)

        rows.sort(key=lambda item: item[1], reverse=True)
        to_delete = set()
        if mode in ("time", "both"):
            cutoff = time.time() - (keep_days * 86400)
            for alert_id, timestamp in rows:
                # 0 = unknown time (old records): never age it out.
                if 0 < timestamp < cutoff:
                    to_delete.add(alert_id)
        if mode in ("quantity", "both"):
            for alert_id, _timestamp in rows[keep_count:]:
                to_delete.add(alert_id)

        if not to_delete or self._trim_cancel.is_set():
            self._reset_trim_progress()
            return 0

        self._set_trim_progress(
            active=True,
            phase="deleting",
            to_delete=len(to_delete),
            deleted=0,
        )

        deleted = 0
        deleted_ids = []
        backend = database_manager.database_manager
        can_batch = bool(
            getattr(backend, "_initialized", False)
            and getattr(backend, "_database", None) is not None
            and hasattr(getattr(backend, "_database", None), "delete_paths")
        )
        pending_ids = list(to_delete)
        if can_batch:
            chunk_size = 100
            for offset in range(0, len(pending_ids), chunk_size):
                if self._trim_cancel.is_set():
                    break
                chunk = pending_ids[offset : offset + chunk_size]
                paths = [f"Alerts/AlertStorage/{alert_id}" for alert_id in chunk]
                try:
                    if backend.delete_paths(paths):
                        deleted += len(chunk)
                        deleted_ids.extend(chunk)
                        self._set_trim_progress(deleted=deleted)
                except Exception as e:
                    logger.error(
                        "Error batch-deleting stored alerts during trim: %s",
                        e,
                        exc_info=True,
                    )
        else:
            for alert_id in pending_ids:
                if self._trim_cancel.is_set():
                    break
                try:
                    database_manager.delete_data(f"Alerts/AlertStorage/{alert_id}")
                    deleted += 1
                    deleted_ids.append(alert_id)
                    self._set_trim_progress(deleted=deleted)
                except Exception as e:
                    logger.error(
                        "Error deleting stored alert %s during trim: %s",
                        alert_id,
                        e,
                        exc_info=True,
                    )

        with self._lock:
            for alert_id in deleted_ids:
                self._alert_storage.pop(alert_id, None)
            self._note_storage_changed()

        if deleted and not self._trim_cancel.is_set():
            logger.info(
                "Trimmed %d stored alert(s) (mode=%s keep_count=%s keep_days=%s)",
                deleted,
                mode,
                keep_count,
                keep_days,
            )
        self._reset_trim_progress()
        return deleted

    def _normalize_alert_data_for_storage(self, alert_data: dict) -> dict:
        """Normalize alert data to ensure all AlertObj fields are present and properly formatted

        Args:
            alert_data (dict): Raw alert data (usually from AlertObj.__dict__)

        Returns:
            dict: Normalized alert data with all fields
        """
        try:
            # Create a new AlertObj to get all default field values
            default_alert = AlertObj()
            default_dict = default_alert.__dict__

            # Start with defaults and update with provided data
            normalized_data = copy.deepcopy(default_dict)
            normalized_data.update(alert_data)

            # Ensure critical fields are not None
            critical_fields = {
                "username": "",
                "alert_type": "",
                "alert_id": "",
                "alert_name": "",
                "message": "",
                "emotes": None,
                "fragments": None,
                "title": "",
                "single_audio_dir": "",
                "single_audio_name": "",
                "gif_dir": "",
                "gif_name": "",
                "randomized_dir": "",
                "randomized_extra_dir": "",
                "twitch_reward_id": "",
                "currency": "USD",
            }

            for field, default_value in critical_fields.items():
                if normalized_data.get(field) is None:
                    normalized_data[field] = default_value
                    logger.debug(
                        f"Normalized field '{field}' from None to '{default_value}' for alert storage"
                    )

            # A missing / zero timestamp would read as 1970 ("20000d ago") and be
            # trimmed at once in time mode: treat it as "now".
            if safe_alert_timestamp(normalized_data.get("timestamp"), 0.0) <= 0:
                normalized_data["timestamp"] = time.time()

            # Ensure numeric fields are properly typed
            numeric_fields = {
                "duration": float,
                "timestamp": float,
                "tier": int,
                "gift_qty": int,
                "resub_month": int,
                "months_prepaid": int,
                "amt_cheered": int,
                "point_cost": int,
                "raider_count": int,
                "donation_amount": float,
                "hype_train_level": int,
                "streak_count": int,
                "channel_points_awarded": int,
                "fade_in": int,
                "fade_out": int,
                "volume": int,
                "randomized_chance": int,
                "randomized_extra_chance": int,
            }

            for field, field_type in numeric_fields.items():
                if field in normalized_data:
                    try:
                        normalized_data[field] = field_type(normalized_data[field] or 0)
                    except (ValueError, TypeError):
                        normalized_data[field] = field_type(0)
                        logger.debug(
                            f"Normalized field '{field}' to 0 due to conversion error"
                        )

            # Ensure boolean fields are properly typed
            boolean_fields = [
                "deleted",
                "played",
                "stackable",
                "skip_alert",
                "anonymous",
                "audio_only",
                "randomized",
                "randomized_extra",
                "hype_train_in_progress",
            ]

            for field in boolean_fields:
                if field in normalized_data:
                    normalized_data[field] = bool(normalized_data[field])

            return normalized_data

        except Exception as e:
            logger.error(
                f"Error normalizing alert data for storage: {str(e)}", exc_info=True
            )
            # Return original data if normalization fails
            return copy.deepcopy(alert_data)

    def _note_storage_changed(self) -> None:
        """Invalidate the newest-first index (call with self._lock held)."""
        self._storage_version = getattr(self, "_storage_version", 0) + 1
        self._sorted_ids_cache = None

    def _ordered_storage_ids_locked(self) -> List[str]:
        """Stored alert ids newest first (call with self._lock held).

        Cached until _alert_storage changes (insert/trim/reload), so long streams
        with the full history in memory don't re-sort it for every feed rebuild.
        """
        storage = self._alert_storage
        key = (id(storage), len(storage), getattr(self, "_storage_version", 0))
        cached = getattr(self, "_sorted_ids_cache", None)
        if cached is not None and getattr(self, "_sorted_ids_key", None) == key:
            return cached

        def _ts(alert_id):
            data = storage.get(alert_id)
            return safe_alert_timestamp(
                data.get("timestamp", 0) if isinstance(data, dict) else 0
            )

        ordered = sorted(storage, key=_ts, reverse=True)
        self._sorted_ids_cache = ordered
        self._sorted_ids_key = key
        return ordered

    @staticmethod
    def _coerce_page_args(page: Any, limit: Any) -> Tuple[int, int]:
        """Integer page (>= 1) and limit (1..1000); bad values fall back to 1 / 25."""
        try:
            page_i = int(page)
        except (TypeError, ValueError):
            page_i = 1
        try:
            limit_i = int(limit)
        except (TypeError, ValueError):
            limit_i = 25
        return max(1, page_i), min(1000, max(1, limit_i))

    @staticmethod
    def _clamp_page(page: int, total_count: int, limit: int) -> int:
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
        return min(max(1, page), total_pages)

    def _page_from_ordered_ids_locked(
        self, ordered_ids: List[str], page: int, limit: int
    ) -> dict:
        page = self._clamp_page(page, len(ordered_ids), limit)
        start_index = (page - 1) * limit
        page_alerts = []
        for alert_id in ordered_ids[start_index : start_index + limit]:
            data = self._alert_storage.get(alert_id)
            alert_copy = copy.deepcopy(data) if isinstance(data, dict) else {}
            alert_copy["alert_id"] = alert_id
            page_alerts.append(alert_copy)
        return self._stored_alerts_page_result(
            page_alerts, len(ordered_ids), page, limit
        )

    def get_stored_alerts_since(
        self, cutoff_ts: float, limit: Optional[int] = None
    ) -> List[dict]:
        """Stored alerts with timestamp >= cutoff_ts, newest first, across all storage.

        Each item is a copy with "alert_id" set. ``limit`` caps the count (None = all).
        For time-window views (condensed feed) that must not stop at N pages.
        """
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.initialize()
        try:
            cutoff = float(cutoff_ts)
        except (TypeError, ValueError):
            cutoff = 0.0
        with self._lock:
            loaded = self._alert_storage_loaded
        if not loaded:
            self._ensure_alert_storage_loaded()
        out: List[dict] = []
        with self._lock:
            for alert_id in self._ordered_storage_ids_locked():
                data = self._alert_storage.get(alert_id)
                if not isinstance(data, dict):
                    continue
                if safe_alert_timestamp(data.get("timestamp", 0)) < cutoff:
                    break
                alert_copy = copy.deepcopy(data)
                alert_copy["alert_id"] = alert_id
                out.append(alert_copy)
                if limit is not None and len(out) >= int(limit):
                    break
        return out

    def _stored_alerts_page_result(
        self, alerts: list, total_count: int, page: int, limit: int
    ) -> dict:
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
        return {
            "alerts": alerts,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }

    def _page_stored_alerts_memory(self, storage: dict, page: int, limit: int) -> dict:
        page, limit = self._coerce_page_args(page, limit)
        page = self._clamp_page(page, len(storage), limit)
        start_index = (page - 1) * limit
        end_index = start_index + limit
        if all(isinstance(alert_data, dict) for alert_data in storage.values()):
            # Same stable sort as below, but only the page is copied.
            ordered_ids = sorted(
                storage,
                key=lambda alert_id: safe_alert_timestamp(
                    storage[alert_id].get("timestamp", 0)
                ),
                reverse=True,
            )
            page_alerts = []
            for alert_id in ordered_ids[start_index:end_index]:
                alert_copy = copy.deepcopy(storage[alert_id])
                alert_copy["alert_id"] = alert_id
                page_alerts.append(alert_copy)
            return self._stored_alerts_page_result(
                page_alerts, len(ordered_ids), page, limit
            )

        alerts_list = []
        for alert_id, alert_data in storage.items():
            alert_copy = copy.deepcopy(alert_data)
            alert_copy["alert_id"] = alert_id
            alerts_list.append(alert_copy)
        alerts_list.sort(
            key=lambda item: safe_alert_timestamp(item.get("timestamp", 0)),
            reverse=True,
        )
        total_count = len(alerts_list)
        start_index = (page - 1) * limit
        end_index = start_index + limit
        return self._stored_alerts_page_result(
            alerts_list[start_index:end_index], total_count, page, limit
        )

    def get_stored_alerts(self) -> dict:
        """Get all stored completed alerts

        Returns:
            dict: Dictionary of stored alerts
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            if self._alert_storage_loaded:
                return copy.deepcopy(self._alert_storage)
        self._ensure_alert_storage_loaded()
        with self._lock:
            return copy.deepcopy(self._alert_storage)

    def get_stored_alerts_paginated(self, page: int = 1, limit: int = 25) -> dict:
        """Get stored completed alerts with pagination

        Args:
            page (int): Page number (1-based)
            limit (int): Number of alerts per page

        Returns:
            dict: Dictionary containing:
                - alerts: List of alert data sorted by timestamp (newest first)
                - total_count: Total number of stored alerts
                - page: Current page number
                - limit: Items per page
                - total_pages: Total number of pages
                - has_next: Whether there's a next page
                - has_prev: Whether there's a previous page
        """
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.initialize()
        page, limit = self._coerce_page_args(page, limit)

        try:
            with self._lock:
                use_memory = self._alert_storage_loaded
            if not use_memory:
                # One shared full load, so every page comes from the same data.
                self._ensure_alert_storage_loaded()
            with self._lock:
                return self._page_from_ordered_ids_locked(
                    self._ordered_storage_ids_locked(), page, limit
                )

        except Exception as e:
            logger.error(
                f"Error getting paginated stored alerts: {str(e)}", exc_info=True
            )
            return {
                "alerts": [],
                "total_count": 0,
                "page": page,
                "limit": limit,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            }

    def get_recent_stored_alerts(self, limit: int = 25) -> list:
        """Get the most recent stored alerts

        Args:
            limit (int): Maximum number of alerts to return

        Returns:
            list: List of recent alerts sorted by timestamp (newest first)
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            # Get first page of stored alerts
            paginated_result = self.get_stored_alerts_paginated(page=1, limit=limit)
            return paginated_result.get("alerts", [])

        except Exception as e:
            logger.error(
                f"Error getting recent stored alerts: {str(e)}", exc_info=True
            )
            return []

    def get_stored_alert_by_id(self, alert_id: str) -> Optional[dict]:
        """Get a specific stored alert by its ID

        Args:
            alert_id (str): The ID of the stored alert to retrieve

        Returns:
            dict: The stored alert data or None if not found
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

        try:
            with self._lock:
                stored_alert = self._alert_storage.get(alert_id)
                if stored_alert is not None:
                    return copy.deepcopy(stored_alert)

            # Read outside the alert lock.
            single = database_manager.get_data(f"Alerts/AlertStorage/{alert_id}")
            if isinstance(single, dict) and single:
                with self._lock:
                    if not self._alert_storage_loaded:
                        self._alert_storage[alert_id] = single
                return copy.deepcopy(single)
            return None
        except Exception as e:
            logger.error(
                f"Error getting stored alert by ID {alert_id}: {str(e)}",
                exc_info=True,
            )
            return None

    def _limit_stored_alerts_dict(self, all_stored_alerts: dict, max_alerts: int) -> dict:
        """Return the newest max_alerts entries from a stored-alerts dict."""
        if not all_stored_alerts:
            return {}

        alerts_list = []
        for alert_id, alert_data in all_stored_alerts.items():
            timestamp = safe_alert_timestamp(alert_data.get("timestamp", 0))
            alerts_list.append((alert_id, alert_data, timestamp))

        alerts_list.sort(key=lambda x: x[2], reverse=True)
        limited_alerts = alerts_list[:max_alerts]

        return {alert_id: alert_data for alert_id, alert_data, _ in limited_alerts}

    def get_limited_stored_alerts_from_firebase(self, max_alerts: int) -> dict:
        """Get only the most recent X alerts directly from Firebase to reduce bandwidth

        Args:
            max_alerts (int): Maximum number of alerts to fetch from Firebase

        Returns:
            dict: Dictionary of the most recent stored alerts (limited to max_alerts)
        """
        if not self._initialized:
            self.initialize()

        need_fetch = False
        with self._lock:
            need_fetch = not self._alert_storage_loaded

        if need_fetch:
            # One shared full load, so every caller sees the same stored alerts.
            self._ensure_alert_storage_loaded()

        try:
            with self._lock:
                all_stored_alerts = dict(self._alert_storage)

            if not all_stored_alerts:
                logger.debug("No stored alerts found")
                return {}

            result = self._limit_stored_alerts_dict(all_stored_alerts, max_alerts)

            logger.debug(
                "Retrieved %d out of %d stored alerts from Firebase (limited to %d)",
                len(result),
                len(all_stored_alerts),
                max_alerts,
            )
            return result

        except Exception as e:
            logger.error(
                f"Error getting limited stored alerts from Firebase: {str(e)}",
                exc_info=True,
            )
            return {}

    def get_limited_stored_alerts_paginated(
        self, page: int = 1, limit: int = 25, max_total_alerts: int = 250
    ) -> dict:
        """Get stored completed alerts with pagination, limited to a maximum total number of alerts

        Args:
            page (int): Page number (1-based)
            limit (int): Number of alerts per page
            max_total_alerts (int): Maximum total number of alerts to consider

        Returns:
            dict: Dictionary containing:
                - alerts: List of alert data sorted by timestamp (newest first)
                - total_count: Total number of stored alerts (limited to max_total_alerts)
                - page: Current page number
                - limit: Items per page
                - total_pages: Total number of pages
                - has_next: Whether there's a next page
                - has_prev: Whether there's a previous page
        """
        page, limit = self._coerce_page_args(page, limit)
        try:
            max_total = max(0, int(max_total_alerts))
        except (TypeError, ValueError):
            max_total = 250
        try:
            if not self._initialized:
                self.initialize()
            with self._lock:
                loaded = self._alert_storage_loaded
            if not loaded:
                # One shared full load, so every caller sees the same stored alerts.
                self._ensure_alert_storage_loaded()
            with self._lock:
                ordered_ids = self._ordered_storage_ids_locked()[:max_total]
                return self._page_from_ordered_ids_locked(ordered_ids, page, limit)

        except Exception as e:
            logger.error(
                f"Error getting limited paginated stored alerts: {str(e)}",
                exc_info=True,
            )
            return {
                "alerts": [],
                "total_count": 0,
                "page": page,
                "limit": limit,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            }

    def get_display_name(
        self, alert_type: str, alert_id: str, alert_data: dict = None
    ) -> str:
        """Generate a consistent display name for an alert based on type and ID

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert
            alert_data (dict, optional): The alert data (used for point alerts)

        Returns:
            str: Formatted display name for the alert
        """
        try:
            # Handle point alerts - use the Twitch reward name
            if alert_type == "points":
                if alert_data and alert_data.get("title"):
                    return alert_data["title"]
                return f"Point Reward {alert_id}"

            # Handle follow alerts - always just "Follow"
            if alert_type == "follows":
                return "Follow"

            # Handle resub fallback alert
            if alert_id == AlertSettings.FALLBACK_ALERT_ID:
                return "Resub Fallback"

            # Parse numeric part from alert ID
            numeric_part = alert_id
            if alert_id.startswith(alert_type):
                numeric_part = alert_id[len(alert_type) :]

            # Handle range alerts (contain '-')
            if "-" in numeric_part:
                try:
                    min_val, max_val = numeric_part.split("-")
                    min_val = int(min_val)
                    max_val = int(max_val)

                    if alert_type == "subs":
                        return f"{min_val}-{max_val} Months"
                    elif alert_type == "bits":
                        return f"{min_val}-{max_val} Bits"
                    elif alert_type == "giftsubs":
                        return f"{min_val}-{max_val} Giftsubs"
                    elif alert_type == "donations":
                        return f"${min_val}-${max_val} Donation"
                    elif alert_type == "raids":
                        return f"{min_val}-{max_val} Raiders"
                    elif alert_type == "streaks":
                        return f"{min_val}-{max_val} Streak"
                except ValueError:
                    # Fallback if parsing fails
                    return f"{alert_type.title()} {numeric_part}"

            # Handle exact amount alerts
            try:
                amount = int(numeric_part)

                if alert_type == "subs":
                    return f"{amount} Month" if amount == 1 else f"{amount} Months"
                elif alert_type == "bits":
                    return f"{amount} Bits"
                elif alert_type == "giftsubs":
                    return f"{amount} Giftsubs"
                elif alert_type == "donations":
                    return f"${amount} Donation"
                elif alert_type == "raids":
                    return f"{amount} Raiders"
                elif alert_type == "streaks":
                    return f"{amount} Streak" if amount == 1 else f"{amount} Streaks"
            except ValueError:
                # Fallback if parsing fails
                pass

            # Final fallback - use alert_name from data or a generic name
            if alert_data and alert_data.get("alert_name"):
                return alert_data["alert_name"]

            return f"{alert_type.title()} {alert_id}"

        except Exception as e:
            logger.error(
                f"Error generating display name for {alert_type} alert {alert_id}: {str(e)}",
                exc_info=True,
            )
            return f"{alert_type.title()} {alert_id}"

    def _parse_alert_id(self, alert_id: str) -> tuple:
        """Parse an alert ID to determine alert type and actual ID

        Args:
            alert_id (str): The alert ID to parse (format: "bits100", "subs1", etc.)

        Returns:
            tuple: (alert_type, actual_alert_id) or (None, None) if parsing fails
        """
        try:
            # Special case for point alerts - they use the Twitch reward ID directly
            if not any(
                alert_id.startswith(prefix)
                for prefix in [
                    "bits",
                    "streaks",
                    "subs",
                    "giftsubs",
                    "donations",
                    "raids",
                    "follows",
                ]
            ):
                # Assume it's a point alert with Twitch reward ID
                return "points", alert_id

            # For other alert types, extract the type prefix
            for alert_type in [
                "giftsubs",
                "donations",
                "follows",
                "raids",
                "bits",
                "streaks",
                "subs",
            ]:
                if alert_id.startswith(alert_type):
                    actual_id = alert_id[len(alert_type) :]
                    # For follows and subs without numbers, use the full alert_id
                    if not actual_id and alert_type in ["follows", "subs", "streaks"]:
                        return alert_type, alert_id
                    return alert_type, actual_id

            return None, None

        except Exception as e:
            logger.error(f"Error parsing alert_id {alert_id}: {str(e)}", exc_info=True)
            return None, None

    def migrate_individual_alerts_to_collections(self) -> bool:
        """Migrate individual alert records to collection format

        This method finds all individual alert records in the database and
        consolidates them into their respective collections.

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            try:
                logger.info("Starting migration of individual alerts to collections")

                # Get all alert paths that might have individual records
                alert_types = [
                    "bits",
                    "bit_ranges",
                    "subs",
                    "sub_ranges",
                    "streaks",
                    "streak_ranges",
                    "giftsubs",
                    "giftsub_ranges",
                    "donations",
                    "donation_ranges",
                    "raids",
                    "raid_ranges",
                    "points",
                    "follows",
                ]

                migrated_count = 0

                for state_key in alert_types:
                    if state_key not in self._alert_paths:
                        continue

                    collection_path = self._alert_paths[state_key]
                    logger.debug(f"Processing collection: {collection_path}")

                    # Try to find individual alert records by checking common patterns
                    # This is a bit of a hack, but we need to find the individual records
                    individual_alerts = {}

                    # For SQL database, we can query for paths that start with the collection path
                    if hasattr(
                        database_manager.database_manager, "_database"
                    ) and hasattr(
                        database_manager.database_manager._database, "_connection"
                    ):
                        # SQL database - query for individual alert paths
                        try:
                            cursor = database_manager.database_manager._database._connection.cursor()
                            cursor.execute(
                                """
                                SELECT data_path, data_json FROM app_data 
                                WHERE data_path LIKE ? AND data_path != ?
                            """,
                                (f"{collection_path}/%", collection_path),
                            )

                            rows = cursor.fetchall()
                            for row in rows:
                                path = row["data_path"]
                                alert_id = path.split("/")[
                                    -1
                                ]  # Get the last part as alert ID
                                try:
                                    alert_data = json.loads(row["data_json"])
                                    individual_alerts[alert_id] = alert_data
                                    logger.debug(
                                        f"Found individual alert: {alert_id} at {path}"
                                    )
                                except json.JSONDecodeError as e:
                                    logger.error(f"Error parsing JSON for {path}: {e}")

                        except Exception as e:
                            logger.error(
                                f"Error querying individual alerts for {collection_path}: {e}"
                            )

                    # If we found individual alerts, consolidate them
                    if individual_alerts:
                        logger.info(
                            f"Found {len(individual_alerts)} individual alerts for {state_key}"
                        )

                        # Load existing collection data
                        existing_collection = (
                            database_manager.get_data(collection_path) or {}
                        )

                        # Merge individual alerts into collection
                        existing_collection.update(individual_alerts)

                        # Save the consolidated collection
                        if database_manager.set_data(
                            collection_path, existing_collection
                        ):
                            # Update our state
                            self._alert_state[state_key] = existing_collection
                            migrated_count += len(individual_alerts)
                            logger.info(
                                f"Migrated {len(individual_alerts)} alerts to {collection_path}"
                            )

                            # Clean up individual records
                            for alert_id in individual_alerts.keys():
                                individual_path = f"{collection_path}/{alert_id}"
                                database_manager.delete_data(individual_path)
                                logger.debug(
                                    f"Cleaned up individual record: {individual_path}"
                                )
                        else:
                            logger.error(
                                f"Failed to save consolidated collection for {state_key}"
                            )

                # Update global collections after migration
                self._update_global_collections()

                logger.info(
                    f"Migration completed. Migrated {migrated_count} individual alerts to collections"
                )
                return True

            except Exception as e:
                logger.error(f"Error during alert migration: {str(e)}", exc_info=True)
                return False


# Global instance of the alert state manager
alert_state_manager = AlertStateManager()


def get_alert_storage_trim_progress() -> Dict[str, Any]:
    return alert_state_manager.get_trim_progress()


def schedule_auto_trim_stored_alerts() -> None:
    alert_state_manager.schedule_auto_trim_stored_alerts()


def cancel_alert_storage_trim() -> None:
    alert_state_manager.cancel_alert_storage_trim()


# Alert helper functions/classes
@dataclass
class AlertObj:
    # General options:
    duration: float = 0.0
    alert_name: str = ""
    display_name: str = ""  # Formatted display name for UI
    alert_type: str = ""
    deleted: bool = False
    alert_id: str = ""
    played: bool = False
    stackable: bool = False
    timestamp: float = 0.0
    skip_alert: bool = False
    is_replay: bool = False
    is_test: bool = False
    # True: main alerts overlay only holds queue timing; dedicated browser sources show visuals
    hold_queue_only: bool = False

    # Twitch data:
    username: str = ""
    anonymous: bool = False
    message: str = None
    emotes: list = None
    fragments: list = None
    title: str = None

    # Sub/resub/giftsub options:
    tier: int = 0
    gift_qty: int = 0
    resub_month: int = 0
    months_prepaid: int = 0
    recipient: str = ""  # Gift recipient username (single gifts)

    # Bit options:
    amt_cheered: int = 0
    is_gigantified_emote: bool = False
    gigantified_emote_id: str = ""
    gigantified_emote_name: str = ""

    # Points options:
    twitch_reward_id: str = None
    point_cost: int = 0
    enable_alert: bool = False

    # Raid options:
    raider_count: int = 0
    game_name: str = None  # raider's Twitch category at raid time

    # Watch streak options:
    streak_count: int = 0
    channel_points_awarded: int = 0

    # Donation options:
    donation_amount: float = 0.0
    currency: str = "USD"
    original_amount: float = 0.0
    original_currency: str = ""
    donation_source: str = ""
    donation_text: str = ""

    # Hype train options:
    hype_train_level: int = 0
    hype_train_in_progress: bool = False

    # Audio options:
    fade_in: int = 0
    fade_out: int = 0
    volume: int = 0
    audio_only: bool = False  # If True, only play audio without any visual elements
    tts_enabled: bool = False
    tts_source: str = "alert_message"
    tts_custom_message: str = ""
    # Static audio file (fallback)
    single_audio_dir: str = None
    single_audio_name: str = None

    # GIF options:
    gif_dir: str = None
    gif_name: str = None

    # Audio randomization options:
    # Basic randomization - if True, use randomized audio based on chances
    randomized: bool = False
    # Folder containing randomized audio files
    randomized_dir: str = None
    # Chance (1-100) to play random audio instead of static file
    randomized_chance: int = 0
    # Extra rare randomization - if True, check for extra rare audio first
    randomized_extra: bool = False
    # Chance (1-100) to play extra rare audio (checked first)
    randomized_extra_chance: int = 0
    # Folder containing extra rare randomized audio files
    randomized_extra_dir: str = None


def _alert_obj_from_db(alert_data: dict) -> AlertObj:
    """Build AlertObj from a DB/UI dict, dropping unknown fields."""
    if not isinstance(alert_data, dict):
        alert_data = {}
    return AlertObj(**alert_state_manager._filter_alert_obj_fields(alert_data))


def fetch_bits_alert(quantity: int) -> Optional[AlertObj]:
    """
    Get bits alert data based on quantity.
    Checks for exact match in BitAlerts, otherwise falls back to range alerts.
    If no match is found, returns the default bit alert.

    Args:
        quantity: The number of bits

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all bits alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("bits", include_ranges=True)

    # Check for exact quantity match first
    exact_key = "bits" + str(quantity)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    # Check for range matches
    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                # Remove alert type prefix if present
                range_part = alert_id
                if alert_id.startswith("bits"):
                    range_part = alert_id[4:]  # Remove 'bits' prefix

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= quantity <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_bit_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_giftsub_alert(quantity: int) -> Optional[AlertObj]:
    """
    Get giftsub alert data based on quantity.
    Checks for exact match in GiftsubAlerts, otherwise falls back to range alerts.
    If no match is found, returns the default giftsub alert.

    Args:
        quantity: The number of gift subs

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all giftsub alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("giftsubs", include_ranges=True)
    if not alerts:
        alert_state_manager.ensure_alert_paths_loaded(
            ["giftsubs", "giftsub_ranges"]
        )
        alerts = alert_state_manager.get_alerts_by_type("giftsubs", include_ranges=True)

    # Check for exact quantity match first
    exact_key = "giftsubs" + str(quantity)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    # Check for range matches
    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                # Remove alert type prefix if present
                range_part = alert_id
                if alert_id.startswith("giftsubs"):
                    range_part = alert_id[8:]  # Remove 'giftsubs' prefix

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= quantity <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_giftsub_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return _alert_obj_from_db(next(iter(alerts.values())))

    logger.warning(
        "No giftsub alert configured for quantity %s, using default", quantity
    )
    fallback = AlertObj()
    fallback.alert_type = "giftsub"
    fallback.alert_name = "Default Giftsub"
    return fallback


def fetch_donation_alert(quantity: int) -> Optional[AlertObj]:
    """
    Get donation alert data based on quantity.
    Checks for exact match in DonationAlerts, otherwise falls back to range alerts.
    If no match is found, returns the default donation alert.

    Args:
        quantity: The donation amount

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all donation alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("donations", include_ranges=True)

    # Check for exact quantity match first
    exact_key = "donations" + str(quantity)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    # Check for range matches
    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                # Remove alert type prefix if present
                range_part = alert_id
                if alert_id.startswith("donations"):
                    range_part = alert_id[9:]  # Remove 'donations' prefix

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= quantity <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_donation_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_raid_alert(raider_count: int) -> Optional[AlertObj]:
    """
    Get raid alert data based on raider count.
    Checks for exact match in RaidAlerts, otherwise falls back to range alerts.
    If no match is found, returns the default raid alert.

    Args:
        raider_count: The number of raiders

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all raid alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("raids", include_ranges=True)

    # Check for exact count match first
    exact_key = "raids" + str(raider_count)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    # Check for range matches
    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                # Remove alert type prefix if present
                range_part = alert_id
                if alert_id.startswith("raids"):
                    range_part = alert_id[5:]  # Remove 'raids' prefix

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= raider_count <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_raids_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_streak_alert(streak_count: int) -> Optional[AlertObj]:
    """
    Get watch streak alert data based on consecutive broadcast streak count.
    Checks exact match and range alerts, then default streak alert.

    Args:
        streak_count: The watch streak count from Twitch

    Returns:
        AlertObj or None
    """
    alert_state_manager.initialize()

    alerts = alert_state_manager.get_alerts_by_type("streaks", include_ranges=True)

    exact_key = "streaks" + str(streak_count)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                range_part = alert_id
                if alert_id.startswith("streaks"):
                    range_part = alert_id[7:]

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= streak_count <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    default_alert_id = AlertSettings.default_streak_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_sub_alert(months: int) -> Optional[AlertObj]:
    """
    Get subscription alert data based on months.
    If no match is found, returns the default sub alert.

    Args:
        months: The subscription month count (fixed at 1 for this alert)

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all sub alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=True)

    # Check for exact month match first
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                range_part = alert_id
                if alert_id.startswith("subs"):
                    range_part = alert_id[4:]
                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= months <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_sub_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_follow_alert() -> Optional[AlertObj]:
    """
    Get follow alert data. Since there's only one follow alert, it simply
    returns the first available follow alert or the default follow alert.

    Returns:
        AlertObj
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all follow alerts
    alerts = alert_state_manager.get_alerts_by_type("follows", include_ranges=False)

    # Return default alert if available
    default_alert_id = AlertSettings.default_follow_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        # Return the first available follow alert
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_point_alert(twitch_reward_id: str) -> Optional[AlertObj]:
    """
    Get channel point redemption alert data based on reward ID.
    Returns only exact matches for the twitch_reward_id.

    Args:
        twitch_reward_id: The Twitch reward ID

    Returns:
        AlertObj if exact match found, None otherwise
    """
    # Use the AlertStateManager instead of global collections
    alert_state_manager.initialize()

    # Get all point alerts
    alerts = alert_state_manager.get_alerts_by_type("points", include_ranges=False)

    # Check for exact reward ID match
    if twitch_reward_id in alerts:
        return _alert_obj_from_db(alerts[twitch_reward_id])

    # Return None if no exact match found
    return None


def fetch_resub_alert(months: int) -> Optional[AlertObj]:
    """
    Get resubscription alert data based on months.

    Checks for an exact month match first. If none is found and the resub
    fallback alert is enabled, uses the fallback alert. Otherwise falls back
    to the default sub alert (sub1 / first available).

    Args:
        months: The cumulative subscription month count

    Returns:
        AlertObj or None
    """
    alert_state_manager.initialize()

    # Get all sub alerts (including ranges)
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=True)

    # 1. Check for exact month match
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])

    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                range_part = alert_id
                if alert_id.startswith("subs"):
                    range_part = alert_id[4:]
                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= months <= max_val:
                    return _alert_obj_from_db(alert_data)
            except ValueError:
                continue

    # 2. If no exact match, check if the fallback alert is enabled
    fallback_id = AlertSettings.FALLBACK_ALERT_ID
    if alert_state_manager.get_resub_fallback_enabled() and fallback_id in alerts:
        return _alert_obj_from_db(alerts[fallback_id])

    # 3. Fall back to default sub alert (existing behaviour)
    default_alert_id = AlertSettings.default_sub_alert
    if default_alert_id in alerts:
        return _alert_obj_from_db(alerts[default_alert_id])
    elif alerts:
        return _alert_obj_from_db(next(iter(alerts.values())))
    return None


def fetch_cheer_alert(quantity: int) -> AlertObj:
    """
    Alias for fetch_bits_alert.

    Args:
        quantity: The number of bits

    Returns:
        AlertObj
    """
    return fetch_bits_alert(quantity)


_REPLAY_EVENT_FIELDS = (
    "username",
    "anonymous",
    "message",
    "emotes",
    "fragments",
    "title",
    "tier",
    "gift_qty",
    "recipient",
    "resub_month",
    "months_prepaid",
    "amt_cheered",
    "twitch_reward_id",
    "point_cost",
    "raider_count",
    "game_name",
    "donation_amount",
    "currency",
    "original_amount",
    "original_currency",
    "donation_source",
    "donation_text",
    "hype_train_level",
    "hype_train_in_progress",
    "streak_count",
    "channel_points_awarded",
    "alert_type",
    "is_gigantified_emote",
    "gigantified_emote_id",
    "gigantified_emote_name",
)


def _replay_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _replay_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_alert_config_for_stored(stored: dict) -> Optional[AlertObj]:
    """Re-resolve current alert presentation config from a stored event snapshot."""
    if not isinstance(stored, dict):
        return None
    alert_type = str(stored.get("alert_type") or "").strip().lower()
    if alert_type in ("bit", "bits"):
        return fetch_bits_alert(_replay_int(stored.get("amt_cheered"), 0))
    if alert_type == "sub":
        months = _replay_int(stored.get("resub_month"), 1)
        return fetch_sub_alert(months if months > 0 else 1)
    if alert_type == "resub":
        months = _replay_int(stored.get("resub_month"), 1)
        return fetch_resub_alert(months if months > 0 else 1)
    if alert_type in ("giftsub", "giftsubs"):
        qty = _replay_int(stored.get("gift_qty"), 1)
        return fetch_giftsub_alert(qty if qty > 0 else 1)
    if alert_type in ("donation", "donations"):
        amount = _replay_float(stored.get("donation_amount"), 0.0)
        return fetch_donation_alert(amount)
    if alert_type in ("raid", "raids"):
        count = _replay_int(stored.get("raider_count"), 0)
        return fetch_raid_alert(count if count > 0 else 1)
    if alert_type in ("streak", "streaks", "watch_streak", "watchstreak"):
        count = _replay_int(stored.get("streak_count"), 0)
        return fetch_streak_alert(count if count > 0 else 1)
    if alert_type == "follow":
        return fetch_follow_alert()
    if alert_type in ("point", "points"):
        reward_id = stored.get("twitch_reward_id")
        if reward_id:
            return fetch_point_alert(str(reward_id))
        return None
    return None


_MISSING = object()

# Alert types that never enter the queue live (instant_alert only), so their
# replay is the instant mirror alone.
_REPLAY_INSTANT_ONLY_TYPES = frozenset(
    {"hype_train_start", "hype_train_progress", "hype_train_end", "hype_train"}
)

# Live instant_alert "type" names that differ from the stored alert_type.
_REPLAY_INSTANT_TYPE_NAMES = {
    "bit": "cheer",
    "bits": "cheer",
    "point": "points",
}

# Extra stored keys copied into the replay instant payload (live payloads carry them).
_REPLAY_INSTANT_EXTRA_FIELDS = (
    "alert_name",
    "source",
    "power_up_type",
    "conductor_name",
    "level",
    "total_contributions",
    "top_contributions",
    "member_level",
    "is_supersticker",
)


def _is_point_type(alert_type: Any) -> bool:
    return str(alert_type or "").strip().lower() in ("point", "points")


def _apply_point_dedicated_hold(replay: AlertObj) -> None:
    """Same dedicated-template queue hold the live point handler applies."""
    try:
        from .template_config_parser import match_point_reward_dedicated_template

        dedicated = match_point_reward_dedicated_template(replay.alert_name or "")
    except Exception as exc:
        logger.debug("Replay dedicated template lookup failed: %s", exc)
        return
    if not dedicated or not dedicated.get("queued"):
        return
    gif_ok = (
        replay.gif_dir
        and str(replay.gif_dir).strip()
        and replay.gif_name
        and str(replay.gif_name).strip()
    )
    audio_ok = replay.single_audio_dir and replay.single_audio_name
    if not gif_ok and not audio_ok and not replay.randomized:
        replay.hold_queue_only = True
        try:
            replay.duration = float(dedicated["duration_seconds"])
        except (KeyError, TypeError, ValueError):
            pass


def build_replay_alert(stored: dict, config: Any = _MISSING) -> AlertObj:
    """Build a replay AlertObj: current config presentation + stored event identity.

    The event content (username, message, amounts, reward title...) comes from the
    stored record; media, duration, TTS and other presentation come from the alert
    config as it is configured today. If no config matches, falls back to the stored
    snapshot (``replay_alert`` refuses that case with ``no_config`` instead).
    """
    if not isinstance(stored, dict):
        stored = {}

    if config is _MISSING:
        config = fetch_alert_config_for_stored(stored)
    if config is not None:
        replay = copy.deepcopy(config)
    else:
        replay = AlertObj(**alert_state_manager._filter_alert_obj_fields(stored))

    for field_name in _REPLAY_EVENT_FIELDS:
        if field_name in stored and stored[field_name] is not None:
            setattr(replay, field_name, stored[field_name])

    if stored.get("alert_type"):
        replay.alert_type = stored["alert_type"]

    if _is_point_type(replay.alert_type):
        # The live handler names point alerts after the reward title.
        if stored.get("alert_name"):
            replay.alert_name = stored["alert_name"]
        replay.hold_queue_only = False
        if config is not None:
            _apply_point_dedicated_hold(replay)

    replay.alert_id = new_alert_id("Replay")
    replay.timestamp = time.time()
    replay.played = False
    replay.deleted = False
    replay.skip_alert = False
    replay.is_replay = True
    replay.is_test = False
    replay.stackable = True
    return replay


def _replay_point_hold(stored: dict) -> Optional[AlertObj]:
    """Queue hold for an unconfigured point reward with a queued dedicated template."""
    title = str(stored.get("alert_name") or "")
    if not title:
        return None
    try:
        from .template_config_parser import match_point_reward_dedicated_template

        dedicated = match_point_reward_dedicated_template(title)
    except Exception as exc:
        logger.debug("Replay dedicated template lookup failed: %s", exc)
        return None
    if not dedicated or not dedicated.get("queued"):
        return None
    hold = AlertObj()
    for field_name in _REPLAY_EVENT_FIELDS:
        if field_name in stored and stored[field_name] is not None:
            setattr(hold, field_name, stored[field_name])
    hold.alert_type = "point"
    hold.alert_name = title
    try:
        hold.duration = float(dedicated["duration_seconds"])
    except (KeyError, TypeError, ValueError):
        hold.duration = 0.0
    hold.hold_queue_only = True
    hold.enable_alert = False
    hold.alert_id = new_alert_id("Replay")
    hold.timestamp = time.time()
    hold.is_replay = True
    hold.stackable = True
    return hold


def build_replay_instant_payload(
    stored: dict, alert_id: str, replay: Optional[AlertObj] = None
) -> Dict[str, Any]:
    """instant_alert-shaped payload for a replay (original content, is_replay=True)."""
    stored = stored if isinstance(stored, dict) else {}
    alert_type = str(stored.get("alert_type") or "").strip()
    payload: Dict[str, Any] = {
        "type": _REPLAY_INSTANT_TYPE_NAMES.get(alert_type.lower(), alert_type),
        "alert_type": alert_type,
    }
    for field_name in _REPLAY_EVENT_FIELDS + _REPLAY_INSTANT_EXTRA_FIELDS:
        value = stored.get(field_name)
        if value is not None:
            payload[field_name] = value
    if alert_type.lower().startswith("hype_train"):
        payload.setdefault("level", stored.get("hype_train_level"))
        if stored.get("username"):
            payload.setdefault("conductor_name", stored.get("username"))
    if replay is not None:
        for key in ("alert_name", "duration", "gif_dir", "gif_name", "single_audio_dir", "single_audio_name"):
            value = getattr(replay, key, None)
            if value not in (None, ""):
                payload.setdefault(key, value)
    payload["alert_id"] = alert_id
    payload["original_alert_id"] = str(stored.get("alert_id") or "")
    payload["timestamp"] = time.time()
    payload["is_replay"] = True
    payload["is_test"] = False
    return payload


# Socket event for the replay instant mirror. Separate from "instant_alert" so
# goal bars, counters, giveaways and other instant consumers never count a
# replay (old compiled templates ignore it; new Spore boilerplates play it
# through their visual bindings only).
REPLAY_INSTANT_EVENT = "instant_alert_replay"


def _emit_replay_instant(payload: Dict[str, Any]) -> bool:
    try:
        from . import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine is None:
            return False
        return bool(engine.safe_emit(REPLAY_INSTANT_EVENT, payload))
    except Exception as exc:
        logger.debug("Replay instant mirror failed: %s", exc)
        return False


def replay_alert(alert_id: Any) -> Dict[str, Any]:
    """Replay a stored alert by id.

    Keeps the original event content, re-fetches today's alert config/media for
    that event, and routes it like the live alert did (queue and/or instant
    mirror) with ``is_replay=True``. Never stores a record, adds a feed row,
    updates statistics/counters or fires connectors.

    Returns ``{"ok": bool, "reason": str, "new_alert_id": str | None}``.
    reason: "queued" | "instant_only" | "missing_id" | "not_found" | "no_config" |
    "error".
    """
    result: Dict[str, Any] = {"ok": False, "reason": "", "new_alert_id": None}
    aid = str(alert_id or "").strip()
    if not aid:
        result["reason"] = "missing_id"
        return result
    try:
        manager = alert_state_manager
        manager.initialize()
        stored = manager.get_stored_alert_by_id(aid)
    except Exception as exc:
        logger.error("Replay lookup failed for %s: %s", aid, exc, exc_info=True)
        result["reason"] = "error"
        return result
    if not isinstance(stored, dict) or not stored:
        result["reason"] = "not_found"
        return result
    stored = copy.deepcopy(stored)
    stored.setdefault("alert_id", aid)
    alert_type = str(stored.get("alert_type") or "").strip().lower()

    from . import alert_processor

    if alert_type in _REPLAY_INSTANT_ONLY_TYPES:
        new_id = new_alert_id("Replay")
        _emit_replay_instant(build_replay_instant_payload(stored, new_id))
        result.update(ok=True, reason="instant_only", new_alert_id=new_id)
        return result

    try:
        config = fetch_alert_config_for_stored(stored)
    except Exception as exc:
        logger.error("Replay config lookup failed for %s: %s", aid, exc, exc_info=True)
        result["reason"] = "error"
        return result

    if _is_point_type(alert_type):
        if config is None:
            # Live: unconfigured rewards hold the queue for a queued dedicated
            # template, otherwise they only send instant_alert.
            hold = _replay_point_hold(stored)
            if hold is not None:
                alert_processor.enqueue_alert(hold)
                result.update(ok=True, reason="queued", new_alert_id=hold.alert_id)
                return result
            new_id = new_alert_id("Replay")
            _emit_replay_instant(build_replay_instant_payload(stored, new_id))
            result.update(ok=True, reason="instant_only", new_alert_id=new_id)
            return result
        replay = build_replay_alert(stored, config)
        queued = bool(replay.enable_alert)
        if queued:
            alert_processor.enqueue_alert(replay)
        _emit_replay_instant(
            build_replay_instant_payload(stored, replay.alert_id, replay)
        )
        result.update(
            ok=True,
            reason="queued" if queued else "instant_only",
            new_alert_id=replay.alert_id,
        )
        return result

    if config is None:
        result["reason"] = "no_config"
        return result
    replay = build_replay_alert(stored, config)
    alert_processor.enqueue_alert(replay)
    _emit_replay_instant(build_replay_instant_payload(stored, replay.alert_id, replay))
    result.update(ok=True, reason="queued", new_alert_id=replay.alert_id)
    return result


_CHAT_ACTIVITY_ONLY_ALERT_TYPES = frozenset(
    {
        "point",
        "points",
        "hype_train",
        "hypetrain",
    }
)
_CHAT_MEDIA_RICH_ALERT_TYPES = frozenset(
    {
        "follow",
        "sub",
        "resub",
        "giftsub",
        "bit",
        "bits",
        "donation",
        "raid",
        "streak",
        "streaks",
        "watch_streak",
        "watchstreak",
    }
)
_CHAT_PREVIEW_PLACEHOLDER_DIR = "/assets/default_assets/images"
_CHAT_PREVIEW_PLACEHOLDER_GIFS = (
    "cheer100.gif",
    "cheer1000.gif",
    "cheer5000.gif",
    "cheer10000.gif",
    "cheer100000.gif",
)


def _normalize_chat_alert_type(alert_data: dict) -> str:
    for key in ("badge_type", "alert_type", "type"):
        raw = alert_data.get(key)
        if raw is None or raw == "":
            continue
        return str(raw).strip().lower().replace(" ", "")
    return ""


def _chat_alert_lookup(alert_data: dict, *keys: str) -> Any:
    stored = alert_data.get("stored_alert_data")
    if not isinstance(stored, dict):
        stored = {}
    for key in keys:
        if key in alert_data and alert_data[key] is not None:
            return alert_data[key]
        if key in stored and stored[key] is not None:
            return stored[key]
    return None


def _alert_obj_media(alert: Optional[AlertObj]) -> Optional[Dict[str, str]]:
    if alert is None:
        return None
    gif_dir = str(alert.gif_dir or "").strip()
    gif_name = str(alert.gif_name or "").strip()
    if gif_dir and gif_name:
        return {"gif_dir": gif_dir, "gif_name": gif_name}
    return None


def _media_from_alert_data_fields(alert_data: dict) -> Optional[Dict[str, str]]:
    chat_media = alert_data.get("chat_media")
    if isinstance(chat_media, dict):
        gif_dir = str(chat_media.get("gif_dir") or "").strip()
        gif_name = str(chat_media.get("gif_name") or "").strip()
        if gif_dir and gif_name:
            return {"gif_dir": gif_dir, "gif_name": gif_name}

    stored = alert_data.get("stored_alert_data")
    if isinstance(stored, dict):
        gif_dir = str(stored.get("gif_dir") or "").strip()
        gif_name = str(stored.get("gif_name") or "").strip()
        if gif_dir and gif_name:
            return {"gif_dir": gif_dir, "gif_name": gif_name}

    gif_dir = str(alert_data.get("gif_dir") or "").strip()
    gif_name = str(alert_data.get("gif_name") or "").strip()
    if gif_dir and gif_name:
        return {"gif_dir": gif_dir, "gif_name": gif_name}
    return None


def _preview_placeholder_media() -> Dict[str, str]:
    import random

    return {
        "gif_dir": _CHAT_PREVIEW_PLACEHOLDER_DIR,
        "gif_name": random.choice(_CHAT_PREVIEW_PLACEHOLDER_GIFS),
    }


def _find_range_amount_alert(
    alerts: dict,
    amount: float,
    *,
    prefix: str,
    prefix_strip_len: int,
) -> Optional[AlertObj]:
    """Return the alert whose ID range contains ``amount``, if any."""
    for alert_id, alert_data in alerts.items():
        if "-" not in alert_id:
            continue
        try:
            range_part = (
                alert_id[prefix_strip_len:]
                if alert_id.startswith(prefix)
                else alert_id
            )
            min_val, max_val = map(int, range_part.split("-"))
            if min_val <= amount <= max_val:
                return _alert_obj_from_db(alert_data)
        except (ValueError, TypeError):
            continue
    return None


def _fetch_sub_alert_exact(months: int) -> Optional[AlertObj]:
    """Exact subs{N} match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=False)
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return None


def _fetch_bits_alert_exact(quantity: int) -> Optional[AlertObj]:
    """Exact bits{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("bits", include_ranges=True)
    exact_key = "bits" + str(quantity)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return _find_range_amount_alert(
        alerts, quantity, prefix="bits", prefix_strip_len=4
    )


def _fetch_giftsub_alert_exact(quantity: int) -> Optional[AlertObj]:
    """Exact giftsubs{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("giftsubs", include_ranges=True)
    if not alerts:
        alert_state_manager.ensure_alert_paths_loaded(
            ["giftsubs", "giftsub_ranges"]
        )
        alerts = alert_state_manager.get_alerts_by_type("giftsubs", include_ranges=True)
    exact_key = "giftsubs" + str(quantity)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return _find_range_amount_alert(
        alerts, quantity, prefix="giftsubs", prefix_strip_len=8
    )


def _fetch_donation_alert_exact(amount: float) -> Optional[AlertObj]:
    """Exact donations amount or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("donations", include_ranges=True)
    exact_key = "donations" + str(int(amount) if amount == int(amount) else amount)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return _find_range_amount_alert(
        alerts, amount, prefix="donations", prefix_strip_len=9
    )


def _fetch_raid_alert_exact(raider_count: int) -> Optional[AlertObj]:
    """Exact raids{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("raids", include_ranges=True)
    exact_key = "raids" + str(raider_count)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return _find_range_amount_alert(
        alerts, raider_count, prefix="raids", prefix_strip_len=5
    )


def _fetch_streak_alert_exact(streak_count: int) -> Optional[AlertObj]:
    """Exact streaks{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("streaks", include_ranges=True)
    exact_key = "streaks" + str(streak_count)
    if exact_key in alerts:
        return _alert_obj_from_db(alerts[exact_key])
    return _find_range_amount_alert(
        alerts, streak_count, prefix="streaks", prefix_strip_len=7
    )


def _fetch_chat_alert_for_media(alert_type: str, alert_data: dict) -> Optional[AlertObj]:
    """
    Resolve alert config for chat media-rich blocks.

    Uses exact/range matches only for amount-based types so a configured
    ``bits1`` alert is not reused for unrelated bit amounts (e.g. 500 bits).
    """
    at = alert_type
    if at == "follow":
        return fetch_follow_alert()
    if at == "sub":
        return _fetch_sub_alert_exact(1)
    if at == "resub":
        months = _chat_alert_lookup(
            alert_data, "resub_month", "months", "total_months", "cumulative_months"
        )
        try:
            months_int = int(months) if months is not None else 2
        except (TypeError, ValueError):
            months_int = 2
        if months_int < 2:
            months_int = 2
        return _fetch_sub_alert_exact(months_int)
    if at == "giftsub":
        qty = _chat_alert_lookup(alert_data, "gift_qty", "quantity")
        try:
            qty_int = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty_int = 1
        return _fetch_giftsub_alert_exact(max(qty_int, 1))
    if at in ("bit", "bits"):
        amt = _chat_alert_lookup(alert_data, "amt_cheered", "bits", "amount")
        try:
            amt_int = int(amt) if amt is not None else 0
        except (TypeError, ValueError):
            amt_int = 0
        if amt_int < 1:
            return None
        return _fetch_bits_alert_exact(amt_int)
    if at == "donation":
        amount = _chat_alert_lookup(alert_data, "donation_amount", "amount")
        try:
            amount_val = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            amount_val = 0.0
        if amount_val <= 0:
            return None
        return _fetch_donation_alert_exact(max(1, int(round(amount_val))))
    if at == "raid":
        count = _chat_alert_lookup(alert_data, "raider_count", "viewers")
        try:
            count_int = int(count) if count is not None else 0
        except (TypeError, ValueError):
            count_int = 0
        if count_int < 1:
            return None
        return _fetch_raid_alert_exact(count_int)
    if at in ("streak", "streaks", "watch_streak", "watchstreak"):
        count = _chat_alert_lookup(alert_data, "streak_count")
        try:
            count_int = int(count) if count is not None else 0
        except (TypeError, ValueError):
            count_int = 0
        if count_int < 1:
            return None
        return _fetch_streak_alert_exact(count_int)
    return None


def resolve_chat_alert_media(
    alert_data: dict, *, preview_placeholder: bool = False
) -> Optional[Dict[str, str]]:
    """
    Resolve GIF/video paths for the chat template media-rich alert block.

    Only attaches media when an alert is explicitly configured for the event
    (exact or range match). Does not reuse fallback/default alerts or GIF paths
    stored on unrelated alert snapshots.
    """
    if not isinstance(alert_data, dict):
        return None

    alert_type = _normalize_chat_alert_type(alert_data)
    if not alert_type or alert_type in _CHAT_ACTIVITY_ONLY_ALERT_TYPES:
        return None

    if alert_type not in _CHAT_MEDIA_RICH_ALERT_TYPES:
        return None

    alert_obj = _fetch_chat_alert_for_media(alert_type, alert_data)
    resolved = _alert_obj_media(alert_obj)
    if resolved:
        return resolved

    if preview_placeholder:
        return _preview_placeholder_media()
    return None


# Initialize the alert state manager when this module is imported
def initialize_alert_state():
    """Initialize the global alert state manager"""
    global alert_state_manager
    if alert_state_manager is None:
        alert_state_manager = AlertStateManager()
    alert_state_manager.initialize()


def initialize_alert_state_with_data(all_data: Dict[str, Any]):
    """Initialize the global alert state manager with pre-loaded data"""
    global alert_state_manager
    if alert_state_manager is None:
        alert_state_manager = AlertStateManager()
    alert_state_manager.initialize_with_data(all_data)


async def initialize_alert_state_async():
    """Initialize the global alert state manager asynchronously"""
    global alert_state_manager
    if alert_state_manager is None:
        alert_state_manager = AlertStateManager()
    await alert_state_manager.initialize_async()

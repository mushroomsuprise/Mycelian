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
import copy
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from . import database_manager

logger = logging.getLogger(__name__)


@dataclass
class AlertSettings:
    global_delay: int = 3
    alert_types = [
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
    ]
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

# Global alert queue
ALERT_QUEUE = []


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
        self._resub_fallback_enabled: Optional[bool] = None

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

    async def initialize_async(self):
        """Initialize the alert state by loading all alerts from Firebase asynchronously"""
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing alert state manager")
            await self._load_alerts_from_firebase_async()
            self._initialized = True
            logger.debug("Alert state manager initialized")

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

            # Update the global alert collections
            self._update_global_collections()

            self._initialized = True
            logger.debug("Alert state manager initialized with pre-loaded data")

    def get_resub_fallback_enabled(self) -> bool:
        """Check if the resub fallback alert is enabled.

        Returns:
            bool: True if the resub fallback alert is enabled, False otherwise.
        """
        if self._resub_fallback_enabled is not None:
            return self._resub_fallback_enabled

        try:
            data = database_manager.get_data("Alerts/Settings/resub_fallback_enabled")
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
            database_manager.set_data(
                "Alerts/Settings/resub_fallback_enabled", {"enabled": enabled}
            )
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
                    data = database_manager.get_data(firebase_path) or {}
                    self._alert_state[state_key] = data
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
            config = database_manager.get_config()
            return bool(config and config.database_type == "firebase")
        except Exception:
            return False

    def _ensure_alert_storage_loaded(self, max_alerts: Optional[int] = None) -> None:
        """Load completed alerts once per session (or after invalidation)."""
        if self._alert_storage_loaded:
            return
        self._alert_storage = self._fetch_alert_storage_from_database(
            max_alerts=max_alerts
        )
        self._alert_storage_loaded = True

    def _fetch_alert_storage_from_database(
        self, max_alerts: Optional[int] = None
    ) -> dict:
        """Load completed alert records from the database.

        Returns:
            dict: Dictionary of alert_id -> alert_data
        """
        try:
            alert_storage: dict = {}

            # For SQL database, query for all paths that start with Alerts/AlertStorage/
            if hasattr(database_manager.database_manager, "_database") and hasattr(
                database_manager.database_manager._database, "_connection"
            ):
                try:
                    cursor = (
                        database_manager.database_manager._database._connection.cursor()
                    )
                    limit_clause = ""
                    params: list = ["Alerts/AlertStorage/%", "Alerts/AlertStorage"]
                    if max_alerts and max_alerts > 0:
                        limit_clause = (
                            " ORDER BY json_extract(data_json, '$.timestamp') DESC "
                            f"LIMIT {int(max_alerts)}"
                        )
                    cursor.execute(
                        f"""
                        SELECT data_path, data_json FROM app_data 
                        WHERE data_path LIKE ? AND data_path != ?
                        {limit_clause}
                    """,
                        tuple(params),
                    )

                    rows = cursor.fetchall()
                    for row in rows:
                        path = row["data_path"]
                        alert_id = path.split("/")[-1]
                        try:
                            alert_data = json.loads(row["data_json"])
                            alert_storage[alert_id] = alert_data
                        except json.JSONDecodeError as e:
                            logger.error(
                                f"Error parsing JSON for stored alert {path}: {e}"
                            )

                except Exception as e:
                    logger.error(f"Error querying stored alerts from SQL database: {e}")
            elif self._is_firebase_backend() and max_alerts and max_alerts > 0:
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
                    logger.debug(f"Loaded alert storage from collection format")

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
    ) -> bool:
        """Save an alert to the state and sync with Firebase

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert
            alert_data (dict): The alert data to save
            update_collection (bool): Whether to update the collection after saving (default True)

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
                if not state_key or not firebase_path:
                    return False

                # Generate and store the display name
                alert_data_copy = copy.deepcopy(alert_data)
                alert_data_copy["display_name"] = self.get_display_name(
                    alert_type, alert_id, alert_data
                )

                # Update the state
                self._alert_state[state_key][alert_id] = alert_data_copy

                # Sync with database - save individual alert
                database_manager.set_data(firebase_path, alert_data_copy)

                # Update the collection with all alerts of this type (unless disabled for batch operations)
                if update_collection:
                    collection_path = self._alert_paths[state_key]
                    database_manager.set_data(
                        collection_path, self._alert_state[state_key]
                    )

                    # Update the global collections
                    self._update_global_collections()

                logger.debug(f"Saved alert {alert_id} for type {alert_type}")
                return True
            except Exception as e:
                logger.error(
                    f"Error saving alert {alert_id} for type {alert_type}: {str(e)}",
                    exc_info=True,
                )
                return False

    def delete_alert(self, alert_type: str, alert_id: str) -> bool:
        """Delete an alert from the state and sync with Firebase

        Args:
            alert_type (str): The type of alert (bits, subs, etc.)
            alert_id (str): The ID of the alert

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
                if not state_key or not firebase_path:
                    return False

                # Remove from state if it exists
                if alert_id in self._alert_state[state_key]:
                    del self._alert_state[state_key][alert_id]

                # Delete from database - remove both individual alert and update the collection
                # Delete individual alert
                database_manager.delete_data(firebase_path)

                # Also update the collection with remaining alerts of this type
                collection_path = self._alert_paths[state_key]
                database_manager.set_data(collection_path, self._alert_state[state_key])

                # Update the global collections
                self._update_global_collections()

                logger.debug(f"Deleted alert {alert_id} for type {alert_type}")
                return True
            except Exception as e:
                logger.error(
                    f"Error deleting alert {alert_id} for type {alert_type}: {str(e)}",
                    exc_info=True,
                )
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
        with self._lock:
            try:
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

                # Startup preload may omit range paths; load any empty collections now.
                needs_collection_update = False
                for key in state_keys:
                    if key not in self._alert_paths:
                        continue
                    if self._alert_state.get(key):
                        continue
                    firebase_path = self._alert_paths[key]
                    data = database_manager.get_data(firebase_path) or {}
                    if data:
                        self._alert_state[key] = data
                        logger.debug(
                            "Lazy-loaded %d alerts from %s",
                            len(data),
                            firebase_path,
                        )
                        needs_collection_update = True
                if needs_collection_update:
                    self._update_global_collections()

                # Combine alerts from all relevant state keys
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
        with self._lock:
            for state_key in state_keys:
                if state_key not in self._alert_paths:
                    continue
                if self._alert_state.get(state_key):
                    continue
                firebase_path = self._alert_paths[state_key]
                data = database_manager.get_data(firebase_path) or {}
                if data:
                    self._alert_state[state_key] = data
                    logger.debug(
                        "Lazy-loaded %d alerts from %s",
                        len(data),
                        firebase_path,
                    )
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
                data = database_manager.get_data(firebase_path) or {}
                self._alert_state[state_key] = data
            self._update_global_collections()

    def sync_to_firebase(self):
        """Force a sync of all alerts to Firebase"""
        with self._lock:
            try:
                logger.debug("Syncing all alerts to Firebase")

                for state_key, firebase_path in self._alert_paths.items():
                    if state_key == "alert_storage":
                        database_manager.set_data(firebase_path, self._alert_storage)
                    else:
                        database_manager.set_data(
                            firebase_path, self._alert_state[state_key]
                        )

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

                # Update the collection with all alerts of this type
                collection_path = self._alert_paths[state_key]
                database_manager.set_data(collection_path, self._alert_state[state_key])

                # Update the global collections
                self._update_global_collections()

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
                # Ensure we have a complete copy with all AlertObj fields
                complete_alert_data = self._normalize_alert_data_for_storage(alert_data)

                # Store in memory
                self._alert_storage[alert_id] = copy.deepcopy(complete_alert_data)

                # Store in database
                database_manager.set_data(
                    f"Alerts/AlertStorage/{alert_id}", complete_alert_data
                )

                # Debug logging to verify what's being stored
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

                return True

            except Exception as e:
                logger.error(
                    f"Error storing completed alert {alert_id}: {str(e)}", exc_info=True
                )
                return False

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

    def get_stored_alerts(self) -> dict:
        """Get all stored completed alerts

        Returns:
            dict: Dictionary of stored alerts
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

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
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                self._ensure_alert_storage_loaded()

                # Convert stored alerts to list and sort by timestamp (newest first)
                alerts_list = []
                for alert_id, alert_data in self._alert_storage.items():
                    alert_copy = copy.deepcopy(alert_data)
                    alert_copy["alert_id"] = alert_id  # Ensure alert_id is included
                    alerts_list.append(alert_copy)

                # Sort by timestamp (newest first)
                alerts_list.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

                total_count = len(alerts_list)
                total_pages = (
                    (total_count + limit - 1) // limit if total_count > 0 else 1
                )

                # Calculate pagination boundaries
                start_index = (page - 1) * limit
                end_index = start_index + limit

                # Get the page slice
                page_alerts = alerts_list[start_index:end_index]

                return {
                    "alerts": page_alerts,
                    "total_count": total_count,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                }

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
                stored_alert = self._alert_storage.get(alert_id)
                if stored_alert is not None:
                    return copy.deepcopy(stored_alert)

                single = database_manager.get_data(
                    f"Alerts/AlertStorage/{alert_id}"
                )
                if isinstance(single, dict) and single:
                    self._alert_storage[alert_id] = single
                    if not self._alert_storage_loaded:
                        self._alert_storage_loaded = True
                    return copy.deepcopy(single)
                return None
            except Exception as e:
                logger.error(
                    f"Error getting stored alert by ID {alert_id}: {str(e)}",
                    exc_info=True,
                )
                return None

    def get_limited_stored_alerts_from_firebase(self, max_alerts: int) -> dict:
        """Get only the most recent X alerts directly from Firebase to reduce bandwidth

        Args:
            max_alerts (int): Maximum number of alerts to fetch from Firebase

        Returns:
            dict: Dictionary of the most recent stored alerts (limited to max_alerts)
        """
        with self._lock:
            try:
                self._ensure_alert_storage_loaded(max_alerts=max_alerts)
                all_stored_alerts = dict(self._alert_storage)

                if not all_stored_alerts:
                    logger.debug("No stored alerts found")
                    return {}

                # Convert to list of tuples (alert_id, alert_data) and sort by timestamp
                alerts_list = []
                for alert_id, alert_data in all_stored_alerts.items():
                    timestamp = alert_data.get("timestamp", 0)
                    alerts_list.append((alert_id, alert_data, timestamp))

                # Sort by timestamp (newest first)
                alerts_list.sort(key=lambda x: x[2], reverse=True)

                # Limit to max_alerts
                limited_alerts = alerts_list[:max_alerts]

                # Convert back to dictionary format
                result = {}
                for alert_id, alert_data, _ in limited_alerts:
                    result[alert_id] = alert_data

                logger.debug(
                    f"Retrieved {len(result)} out of {len(all_stored_alerts)} stored alerts from Firebase (limited to {max_alerts})"
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
        with self._lock:
            try:
                limited_stored_alerts = self.get_limited_stored_alerts_from_firebase(
                    max_total_alerts
                )

                # Convert to list and sort by timestamp (newest first)
                alerts_list = []
                for alert_id, alert_data in limited_stored_alerts.items():
                    alert_copy = copy.deepcopy(alert_data)
                    alert_copy["alert_id"] = alert_id  # Ensure alert_id is included
                    alerts_list.append(alert_copy)

                # Sort by timestamp (newest first)
                alerts_list.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

                total_count = len(alerts_list)
                total_pages = (
                    (total_count + limit - 1) // limit if total_count > 0 else 1
                )

                # Calculate pagination boundaries
                start_index = (page - 1) * limit
                end_index = start_index + limit

                # Get the page slice
                page_alerts = alerts_list[start_index:end_index]

                return {
                    "alerts": page_alerts,
                    "total_count": total_count,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                }

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

    # Bit options:
    amt_cheered: int = 0

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


def load_alerts():
    db_bit_alerts = database_manager.get_data("Alerts/BitAlerts") or {}
    db_bit_range_alerts = database_manager.get_data("Alerts/BitRangeAlerts") or {}
    db_sub_alerts = database_manager.get_data("Alerts/SubAlerts") or {}
    db_sub_range_alerts = database_manager.get_data("Alerts/SubRangeAlerts") or {}
    db_giftsub_alerts = database_manager.get_data("Alerts/GiftsubAlerts") or {}
    db_giftsub_range_alerts = (
        database_manager.get_data("Alerts/GiftsubRangeAlerts") or {}
    )
    db_donation_alerts = database_manager.get_data("Alerts/DonationAlerts") or {}
    db_donation_range_alerts = (
        database_manager.get_data("Alerts/DonationRangeAlerts") or {}
    )
    db_raid_alerts = database_manager.get_data("Alerts/RaidAlerts") or {}
    db_raid_range_alerts = database_manager.get_data("Alerts/RaidRangeAlerts") or {}
    db_point_alerts = database_manager.get_data("Alerts/PointAlerts") or {}
    db_follow_alerts = database_manager.get_data("Alerts/FollowAlerts") or {}
    db_streak_alerts = database_manager.get_data("Alerts/StreakAlerts") or {}
    db_streak_range_alerts = database_manager.get_data("Alerts/StreakRangeAlerts") or {}

    # Clear existing global collections first
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

    for key, value in db_bit_alerts.items():
        BitAlerts[key] = AlertObj(**value)
    for key, value in db_bit_range_alerts.items():
        BitRangeAlerts[key] = AlertObj(**value)
    for key, value in db_sub_alerts.items():
        SubAlerts[key] = AlertObj(**value)
    for key, value in db_sub_range_alerts.items():
        SubRangeAlerts[key] = AlertObj(**value)
    for key, value in db_giftsub_alerts.items():
        GiftsubAlerts[key] = AlertObj(**value)
    for key, value in db_giftsub_range_alerts.items():
        GiftsubRangeAlerts[key] = AlertObj(**value)
    for key, value in db_donation_alerts.items():
        DonationAlerts[key] = AlertObj(**value)
    for key, value in db_donation_range_alerts.items():
        DonationRangeAlerts[key] = AlertObj(**value)
    for key, value in db_raid_alerts.items():
        RaidAlerts[key] = AlertObj(**value)
    for key, value in db_raid_range_alerts.items():
        RaidRangeAlerts[key] = AlertObj(**value)
    for key, value in db_point_alerts.items():
        PointAlerts[key] = AlertObj(**value)
    for key, value in db_follow_alerts.items():
        FollowAlerts[key] = AlertObj(**value)
    for key, value in db_streak_alerts.items():
        StreakAlerts[key] = AlertObj(**value)
    for key, value in db_streak_range_alerts.items():
        StreakRangeAlerts[key] = AlertObj(**value)

    # Do not update alert state manager from here anymore
    # That would create an infinite loop


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
        return AlertObj(**alerts[exact_key])

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
                    return AlertObj(**alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_bit_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return AlertObj(**next(iter(alerts.values())))
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
        return AlertObj(**alerts[exact_key])

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
                    return AlertObj(**alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_giftsub_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return AlertObj(**next(iter(alerts.values())))

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
        return AlertObj(**alerts[exact_key])

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
                    return AlertObj(**alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_donation_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return AlertObj(**next(iter(alerts.values())))
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
        return AlertObj(**alerts[exact_key])

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
                    return AlertObj(**alert_data)
            except ValueError:
                continue

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_raids_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return AlertObj(**next(iter(alerts.values())))
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
        return AlertObj(**alerts[exact_key])

    for alert_id, alert_data in alerts.items():
        if "-" in alert_id:
            try:
                range_part = alert_id
                if alert_id.startswith("streaks"):
                    range_part = alert_id[7:]

                min_val, max_val = map(int, range_part.split("-"))
                if min_val <= streak_count <= max_val:
                    return AlertObj(**alert_data)
            except ValueError:
                continue

    default_alert_id = AlertSettings.default_streak_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        return AlertObj(**next(iter(alerts.values())))
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

    # Get all sub alerts
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=False)

    # Check for exact month match first
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])

    # Return default alert if no match found
    default_alert_id = AlertSettings.default_sub_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Fall back to first available alert if default not found
        return AlertObj(**next(iter(alerts.values())))
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
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        # Return the first available follow alert
        return AlertObj(**next(iter(alerts.values())))
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
        return AlertObj(**alerts[twitch_reward_id])

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

    # Get all sub alerts (exact only, no ranges)
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=False)

    # 1. Check for exact month match
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])

    # 2. If no exact match, check if the fallback alert is enabled
    fallback_id = AlertSettings.FALLBACK_ALERT_ID
    if alert_state_manager.get_resub_fallback_enabled() and fallback_id in alerts:
        return AlertObj(**alerts[fallback_id])

    # 3. Fall back to default sub alert (existing behaviour)
    default_alert_id = AlertSettings.default_sub_alert
    if default_alert_id in alerts:
        return AlertObj(**alerts[default_alert_id])
    elif alerts:
        return AlertObj(**next(iter(alerts.values())))
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


_CHAT_ACTIVITY_ONLY_ALERT_TYPES = frozenset(
    {"point", "points", "streak", "hype_train", "hypetrain"}
)
_CHAT_MEDIA_RICH_ALERT_TYPES = frozenset(
    {"follow", "sub", "resub", "giftsub", "bit", "bits", "donation", "raid"}
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
                return AlertObj(**alert_data)
        except (ValueError, TypeError):
            continue
    return None


def _fetch_sub_alert_exact(months: int) -> Optional[AlertObj]:
    """Exact subs{N} match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("subs", include_ranges=False)
    exact_key = "subs" + str(months)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])
    return None


def _fetch_bits_alert_exact(quantity: int) -> Optional[AlertObj]:
    """Exact bits{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("bits", include_ranges=True)
    exact_key = "bits" + str(quantity)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])
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
        return AlertObj(**alerts[exact_key])
    return _find_range_amount_alert(
        alerts, quantity, prefix="giftsubs", prefix_strip_len=8
    )


def _fetch_donation_alert_exact(amount: float) -> Optional[AlertObj]:
    """Exact donations amount or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("donations", include_ranges=True)
    exact_key = "donations" + str(int(amount) if amount == int(amount) else amount)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])
    return _find_range_amount_alert(
        alerts, amount, prefix="donations", prefix_strip_len=9
    )


def _fetch_raid_alert_exact(raider_count: int) -> Optional[AlertObj]:
    """Exact raids{N} or range match only — no default/fallback alerts."""
    alert_state_manager.initialize()
    alerts = alert_state_manager.get_alerts_by_type("raids", include_ranges=True)
    exact_key = "raids" + str(raider_count)
    if exact_key in alerts:
        return AlertObj(**alerts[exact_key])
    return _find_range_amount_alert(
        alerts, raider_count, prefix="raids", prefix_strip_len=5
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
        return _fetch_donation_alert_exact(amount_val)
    if at == "raid":
        count = _chat_alert_lookup(alert_data, "raider_count", "viewers")
        try:
            count_int = int(count) if count is not None else 0
        except (TypeError, ValueError):
            count_int = 0
        if count_int < 1:
            return None
        return _fetch_raid_alert_exact(count_int)
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

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

"""
Legacy Migration Module for Mycelian

This module handles migrating data from the old Firebase-only database format
to the new multi-database system. It supports:
- Migrating alert configurations (bits, subs, points, follows, raids, donations)
- Migrating alert logs/history
- Copying associated asset files (audio, gifs)
- Storing non-alert data in JSON files
"""

import copy
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .alertutils import AlertObj, alert_state_manager
from .path_utils import ensure_directory_exists, get_data_path

logger = logging.getLogger(__name__)

# Firebase imports (optional - only needed for migration)
try:
    import firebase_admin
    from firebase_admin import credentials
    from firebase_admin import db as firebase_db

    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("Firebase SDK not available for legacy migration")


@dataclass
class MigrationConfig:
    """Configuration for legacy database migration"""

    old_service_account_path: str = ""
    old_database_url: str = ""
    old_streamer_name: str = ""
    old_assets_folder: str = ""
    skip_disabled_alerts: bool = False  # Unchecked by default
    skip_existing_alerts: bool = False  # Unchecked by default
    migrate_alert_logs: bool = True
    migrate_other_data: bool = True
    copy_asset_files: bool = False  # Will be enabled/disabled based on assets folder


@dataclass
class MigrationResult:
    """Results of a migration operation"""

    total_items: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)
    migrated_items: List[Dict[str, Any]] = field(default_factory=list)
    type_counts: Dict[str, int] = field(default_factory=dict)
    copied_files: List[str] = field(default_factory=list)
    missing_files: List[Dict[str, str]] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def add_success(self, item_type: str, item_id: str, item_name: str = ""):
        """Record a successful migration"""
        self.successful += 1
        self.migrated_items.append(
            {"type": item_type, "id": item_id, "name": item_name}
        )
        if item_type not in self.type_counts:
            self.type_counts[item_type] = 0
        self.type_counts[item_type] += 1

    def add_error(self, item_type: str, item_id: str, error: str):
        """Record a migration error"""
        self.failed += 1
        self.errors.append({"type": item_type, "id": item_id, "error": error})

    def add_skip(self, item_type: str, item_id: str, reason: str):
        """Record a skipped item"""
        self.skipped += 1
        self.errors.append(
            {"type": item_type, "id": item_id, "error": f"Skipped: {reason}"}
        )

    def add_copied_file(self, file_path: str):
        """Record a successfully copied file"""
        self.copied_files.append(file_path)

    def add_missing_file(self, alert_id: str, file_path: str, file_type: str):
        """Record a missing file"""
        self.missing_files.append(
            {"alert_id": alert_id, "path": file_path, "type": file_type}
        )

    def get_duration(self) -> float:
        """Get the migration duration in seconds"""
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0

    def get_summary(self) -> str:
        """Get a summary of the migration results"""
        duration = self.get_duration()
        summary = f"Migration Complete ({duration:.1f}s):\n"
        summary += f"  Total items: {self.total_items}\n"
        summary += f"  Successful: {self.successful}\n"
        summary += f"  Failed: {self.failed}\n"
        summary += f"  Skipped: {self.skipped}\n"

        if self.type_counts:
            summary += "\nMigrated by type:\n"
            for item_type, count in sorted(self.type_counts.items()):
                summary += f"  {item_type}: {count}\n"

        if self.copied_files:
            summary += f"\nFiles copied: {len(self.copied_files)}\n"

        if self.missing_files:
            summary += f"\nMissing files: {len(self.missing_files)}\n"

        if self.errors:
            summary += f"\nErrors ({len(self.errors)}):\n"
            for error in self.errors[:10]:  # Show first 10 errors
                summary += f"  [{error['type']}] {error['id']}: {error['error']}\n"
            if len(self.errors) > 10:
                summary += f"  ... and {len(self.errors) - 10} more errors\n"

        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Convert results to dictionary for JSON export"""
        return {
            "total_items": self.total_items,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": self.get_duration(),
            "type_counts": self.type_counts,
            "copied_files_count": len(self.copied_files),
            "missing_files": self.missing_files,
            "errors": self.errors,
            "migrated_items": self.migrated_items,
        }


# Field mapping from old format to new AlertObj format
FIELD_MAPPING = {
    "alerttype": "alert_type",
    "alertname": "alert_name",
    "audiodirectory": "single_audio_dir",
    "audioname": "single_audio_name",
    "gifdirectory": "gif_dir",
    "gifname": "gif_name",
    "randomized": "randomized",
    "randomizedchance": "randomized_chance",
    "randomizeddirectory": "randomized_dir",
    "randomizedextra": "randomized_extra",
    "randomizedextrachance": "randomized_extra_chance",
    "randomizedextradirectory": "randomized_extra_dir",
    "twitchrewardid": "twitch_reward_id",
    "fadein": "fade_in",
    "fadeout": "fade_out",
    "volume": "volume",
    "duration": "duration",
    "disabled": "deleted",  # Map disabled to deleted
    "username": "username",
    "message": "message",
    "emotes": "emotes",
    "title": "title",
    "tier": "tier",
    "quantity": "amt_cheered",  # For bits
    "raidercount": "raider_count",
    "months": "resub_month",
}

# Alert type mapping from old keys to new alert state manager keys
ALERT_TYPE_MAPPING = {
    "bit_alerts": "bits",
    "sub_alerts": "subs",
    "point_alerts": "points",
    "follow_alerts": "follows",
    "raid_alerts": "raids",
    "donation_alerts": "donations",
}

# Old Firebase bucket -> ``static/<subdir>/`` when directory fields are "None" (same as new type folder)
LEGACY_BUCKET_STATIC_SUBDIR = dict(ALERT_TYPE_MAPPING)

# Mapping from old alerttype field values to new alert type keys
ALERTTYPE_FIELD_MAPPING = {
    "bits": "bits",
    "sub": "subs",
    "resub": "subs",
    "giftsub": "giftsubs",
    "points": "points",
    "follow": "follows",
    "raid": "raids",
    "donation": "donations",
}


def clean_none_value(value: Any) -> Any:
    """Convert string 'None' or 'N/A' to actual None"""
    if value == "None" or value == "N/A" or value == "none":
        return None
    return value


def convert_to_bool(value: Any) -> bool:
    """Convert various representations to boolean"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def convert_to_int(value: Any, default: int = 0) -> int:
    """Convert value to integer safely"""
    if value is None or value == "None" or value == "N/A":
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def convert_to_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float safely"""
    if value is None or value == "None" or value == "N/A":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def normalize_path(path: str) -> str:
    """Normalize path separators and clean up old path format"""
    if not path or path == "None":
        return ""

    # Replace backslashes with forward slashes
    path = path.replace("\\", "/")

    # Remove leading slash if present
    if path.startswith("/"):
        path = path[1:]

    # Convert old static paths to new assets/alerts paths
    if path.startswith("static/"):
        path = "assets/alerts/" + path[7:]

    return path


def _legacy_str_field(value: Any) -> str:
    """Strip legacy DB values; treat missing, blank, or literal 'None' as empty."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() == "none":
        return ""
    return s


def legacy_relative_subpath_under_assets_root(
    db_dir: str, old_assets_folder: str
) -> str:
    """
    Directory relative to old_assets_folder using forward slashes, no leading/trailing slashes.

    Legacy paths often repeat ``static`` (e.g. folder is ``.../app/static`` while DB has
    ``\\static\\bits\\``). When the assets root folder name is ``static``, strip one leading
    ``static/`` segment. Mixed ``/`` and ``\\`` are normalized.
    """
    if not db_dir or not str(db_dir).strip() or str(db_dir).strip().lower() == "none":
        return ""
    p = db_dir.replace("\\", "/").strip("/")
    root_base = os.path.basename(os.path.normpath(old_assets_folder)).lower()
    pl = p.lower()
    if pl.startswith("static/"):
        if root_base == "static":
            p = p[7:].strip("/")
        # else: keep ``static/...`` (assets root is project folder, not the static subdir)
    elif pl == "static":
        p = ""
    return p.strip("/")


def join_under_assets_root(root: str, rel_subpath: str, *tail: str) -> str:
    """Join assets root with a slash-separated relative path and optional extra path parts."""
    parts = [x for x in rel_subpath.replace("\\", "/").split("/") if x]
    return os.path.join(root, *parts, *tail)


def migration_dest_rel_under_assets_db_dir(db_dir: str) -> str:
    """
    Directory relative to ``get_data_path('assets')`` for migrated files.

    Legacy data used ``static/...``; new layout lives under ``assets/alerts/...``.
    ``normalize_path`` maps to ``assets/alerts/...`` which must not be joined again
    onto ``get_data_path('assets')`` (that would duplicate ``assets``).

    Returns a slash-separated path with no leading/trailing slashes, or "".
    """
    if not db_dir or str(db_dir).strip().lower() in ("", "none"):
        return ""
    p = db_dir.replace("\\", "/").strip("/")
    if p.startswith("/"):
        p = p[1:]
    pl = p.lower()
    if pl.startswith("static/"):
        rest = p[7:].strip("/")
        return f"alerts/{rest}" if rest else "alerts"
    if pl.startswith("assets/"):
        return p[7:].strip("/")
    return p


def transform_old_alert_to_new(
    old_alert: Dict[str, Any], alert_id: str, alert_type: str
) -> Dict[str, Any]:
    """
    Transform an alert from old format to new AlertObj format.

    Args:
        old_alert: The old alert data dictionary
        alert_id: The alert ID/key
        alert_type: The alert type (bits, subs, etc.)

    Returns:
        Dictionary with new AlertObj field names and values
    """
    new_alert = {}

    # Set the alert ID and type
    new_alert["alert_id"] = alert_id
    new_alert["alert_type"] = alert_type

    # Map fields from old to new format
    for old_field, new_field in FIELD_MAPPING.items():
        if old_field in old_alert:
            value = clean_none_value(old_alert[old_field])

            # Type conversions based on field
            if new_field in ("randomized", "randomized_extra", "deleted", "anonymous"):
                value = convert_to_bool(value)
            elif new_field in (
                "randomized_chance",
                "randomized_extra_chance",
                "fade_in",
                "fade_out",
                "volume",
                "tier",
                "amt_cheered",
                "raider_count",
                "resub_month",
                "point_cost",
                "gift_qty",
            ):
                value = convert_to_int(value)
            elif new_field in ("duration", "donation_amount"):
                value = convert_to_float(value)
            elif new_field in (
                "single_audio_dir",
                "gif_dir",
                "randomized_dir",
                "randomized_extra_dir",
            ):
                value = normalize_path(value) if value else None

            new_alert[new_field] = value

    # If GIF directory is not set but audio directory is, use the same directory for GIF
    # Check original gifdirectory field as both string and boolean
    original_gif_dir = old_alert.get("gifdirectory")
    gif_dir_is_none = (
        original_gif_dir is None
        or original_gif_dir == "None"
        or original_gif_dir == ""
        or str(original_gif_dir).lower() == "none"
    )

    if (
        gif_dir_is_none
        and "single_audio_dir" in new_alert
        and new_alert["single_audio_dir"]
    ):
        new_alert["gif_dir"] = new_alert["single_audio_dir"]

    # Generate display name
    display_name = generate_display_name(alert_type, alert_id, new_alert)
    new_alert["display_name"] = display_name
    new_alert["alert_name"] = display_name  # Also set alert_name for UI compatibility

    # Set timestamp to current time for new alerts
    new_alert["timestamp"] = time.time()

    return new_alert


def generate_mycelian_alert_id(alert_type: str, old_alert_id: str) -> str:
    """Generate a proper Mycelian alert ID from old Firebase data.

    Args:
        alert_type: The Mycelian alert type (bits, subs, etc.)
        old_alert_id: The alert ID from old Firebase data

    Returns:
        str: The Mycelian-formatted alert ID
    """
    # If the old ID already starts with the correct type, use it as-is
    if old_alert_id.startswith(alert_type):
        return old_alert_id

    # Try to extract numeric part from old ID
    numeric_part = old_alert_id

    # Remove old prefixes if present
    old_prefixes = ["bit", "sub", "donation", "raid", "follow"]
    for prefix in old_prefixes:
        if old_alert_id.lower().startswith(prefix):
            numeric_part = old_alert_id[len(prefix) :]
            break

    # Ensure we have a valid numeric part
    if not numeric_part:
        numeric_part = old_alert_id

    return f"{alert_type}{numeric_part}"


def generate_display_name(
    alert_type: str, alert_id: str, alert_data: Dict[str, Any]
) -> str:
    """Generate a display name for an alert based on type and ID.

    Args:
        alert_type: The new alert type (bits, subs, giftsubs, etc.)
        alert_id: The Mycelian alert ID (e.g., "bits101", "subs1-4")
        alert_data: The alert data dictionary
    """
    # This function now works with Mycelian alert_id format
    # It mimics the logic from alertutils.AlertStateManager.get_display_name

    # For point alerts, use the alert name or title
    if alert_type == "points":
        if alert_data.get("alert_name"):
            return alert_data["alert_name"]
        if alert_data.get("title"):
            return alert_data["title"]
        return f"Point Reward {alert_id}"

    # For follow alerts
    if alert_type == "follows":
        return "Follow"

    # Extract numeric part from alert ID by removing the type prefix
    numeric_part = alert_id
    if alert_id.startswith(alert_type):
        numeric_part = alert_id[len(alert_type) :]

    # Handle range alerts (contain '-')
    if "-" in numeric_part:
        try:
            min_val, max_val = numeric_part.split("-")
            min_val = int(min_val)
            max_val = int(max_val)

            if alert_type == "bits":
                return f"{min_val}-{max_val} Bits"
            elif alert_type == "subs":
                return f"{min_val}-{max_val} Months"
            elif alert_type == "giftsubs":
                return f"{min_val}-{max_val} Gift Subs"
            elif alert_type == "donations":
                return f"${min_val}-${max_val} Donation"
            elif alert_type == "raids":
                return f"{min_val}-{max_val} Raiders"
        except ValueError:
            pass

    # Handle exact amount alerts
    try:
        amount = int(numeric_part)

        if alert_type == "subs":
            return f"{amount} Month" if amount == 1 else f"{amount} Months"
        elif alert_type == "bits":
            return f"{amount} Bits"
        elif alert_type == "giftsubs":
            return f"{amount} Gift Sub" if amount == 1 else f"{amount} Gift Subs"
        elif alert_type == "donations":
            return f"${amount} Donation"
        elif alert_type == "raids":
            return f"{amount} Raider" if amount == 1 else f"{amount} Raiders"
    except ValueError:
        pass

    # Fallback - use alert name from data if available
    if alert_data.get("alert_name") and alert_data.get("alert_name") != "None":
        return alert_data["alert_name"]

    return f"{alert_type.title()} {alert_id}"


def determine_alert_category(alert_type: str, alert_id: str) -> Tuple[str, bool]:
    """
    Determine the alert category and whether it's a range alert.

    Args:
        alert_type: The type of alert (bits, subs, etc.)
        alert_id: The alert ID

    Returns:
        Tuple of (category_key, is_range)
    """
    # Check if it's a range alert by looking for hyphen in the numeric part
    is_range = False

    # Remove type prefix from ID
    numeric_part = alert_id
    prefixes = ["bit", "sub", "giftsub", "donation", "raid"]
    for prefix in prefixes:
        if alert_id.lower().startswith(prefix):
            numeric_part = alert_id[len(prefix) :]
            break

    # Check for range pattern (e.g., "1-99", "100-999")
    if "-" in numeric_part:
        try:
            parts = numeric_part.split("-")
            if len(parts) == 2:
                int(parts[0])
                int(parts[1])
                is_range = True
        except ValueError:
            pass

    return alert_type, is_range


def transform_alert_log_to_storage(
    log_id: str, log_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Transform an alert log entry to the new AlertStorage format.

    Args:
        log_id: The log entry ID
        log_data: The old log data (contains CurDate and DictVal)

    Returns:
        Dictionary in AlertStorage format
    """
    # Get the inner alert data from DictVal
    inner_data = log_data.get("DictVal", {})

    # Transform using the same field mapping
    new_entry = {}

    # Map fields
    for old_field, new_field in FIELD_MAPPING.items():
        if old_field in inner_data:
            value = clean_none_value(inner_data[old_field])

            # Type conversions
            if new_field in ("randomized", "randomized_extra", "deleted", "anonymous"):
                value = convert_to_bool(value)
            elif new_field in (
                "randomized_chance",
                "randomized_extra_chance",
                "fade_in",
                "fade_out",
                "volume",
                "tier",
                "amt_cheered",
                "raider_count",
                "resub_month",
            ):
                value = convert_to_int(value)
            elif new_field in ("duration", "donation_amount"):
                value = convert_to_float(value)
            elif new_field in (
                "single_audio_dir",
                "gif_dir",
                "randomized_dir",
                "randomized_extra_dir",
            ):
                value = normalize_path(value) if value else None

            new_entry[new_field] = value

    # Set alert ID
    new_entry["alert_id"] = inner_data.get("alert_id", log_id)

    # Convert timestamp from CurDate (epoch seconds)
    cur_date = log_data.get("CurDate", 0)
    if cur_date:
        new_entry["timestamp"] = float(cur_date)
    else:
        new_entry["timestamp"] = time.time()

    # Mark as played (historical alert)
    new_entry["played"] = True

    # Preserve additional fields from old format
    if "stream_id" in inner_data:
        new_entry["stream_id"] = inner_data["stream_id"]
    if "stream_time" in inner_data:
        new_entry["stream_time"] = inner_data["stream_time"]
    if "date" in inner_data:
        new_entry["original_date"] = inner_data["date"]

    return new_entry


class LegacyMigrator:
    """
    Handles migration from old Firebase database to new database system.
    """

    def __init__(self, config: MigrationConfig):
        self.config = config
        self._old_app = None
        self._old_ref = None
        self._raw_data: Dict[str, Any] = {}
        self._progress_callback: Optional[Callable[[str, float], None]] = None
        self._cancelled = False

    def cancel_migration(self):
        """Cancel the ongoing migration."""
        self._cancelled = True
        logger.info("Migration cancellation requested")

    def is_cancelled(self) -> bool:
        """Check if migration has been cancelled."""
        return self._cancelled

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        """Set a callback for progress updates: callback(message, progress_percent)"""
        self._progress_callback = callback

    def _report_progress(self, message: str, progress: float):
        """Report progress to callback if set"""
        logger.info(f"Migration progress: {progress:.1f}% - {message}")
        if self._progress_callback:
            try:
                self._progress_callback(message, progress)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

    def _report_progress_with_items(
        self, message: str, current_item: int, total_items: int
    ):
        """Report progress with item count for more accurate progress bars."""
        if total_items > 0:
            progress_percent = (current_item / total_items) * 100.0
        else:
            progress_percent = 0.0
        self._report_progress(message, progress_percent)

    def connect_to_old_database(self) -> Tuple[bool, str]:
        """
        Connect to the old Firebase database.

        Returns:
            Tuple of (success, error_message)
        """
        if not FIREBASE_AVAILABLE:
            return False, "Firebase SDK not available. Install firebase-admin package."

        if not self.config.old_service_account_path:
            return False, "Old service account path not configured"

        if not self.config.old_database_url:
            return False, "Old database URL not configured"

        if not os.path.exists(self.config.old_service_account_path):
            return (
                False,
                f"Service account key not found: {self.config.old_service_account_path}",
            )

        try:
            self._report_progress("Connecting to old database...", 0)

            # Validate service account key
            with open(self.config.old_service_account_path, "r") as f:
                key_data = json.load(f)
                required_fields = [
                    "type",
                    "project_id",
                    "private_key_id",
                    "private_key",
                    "client_email",
                ]
                missing = [f for f in required_fields if f not in key_data]
                if missing:
                    return False, f"Service account key missing fields: {missing}"

            # Create a unique app name for the old database connection
            app_name = f"legacy_migration_{int(time.time())}"

            # Initialize the old Firebase app
            cred = credentials.Certificate(self.config.old_service_account_path)
            self._old_app = firebase_admin.initialize_app(
                cred, {"databaseURL": self.config.old_database_url}, name=app_name
            )

            # Create reference to the user's data
            streamer = self.config.old_streamer_name or "mycelian"
            self._old_ref = firebase_db.reference(
                f"/{streamer}/ToolData", self._old_app
            )

            # Test the connection
            test_data = self._old_ref.get()
            if test_data is None:
                return False, f"No data found at /{streamer}/ToolData"

            self._report_progress("Connected to old database", 5)
            return True, ""

        except Exception as e:
            logger.error(f"Failed to connect to old database: {e}", exc_info=True)
            return False, f"Connection failed: {str(e)}"

    def fetch_all_data(self) -> Tuple[bool, str]:
        """
        Fetch all data from the old database.

        Returns:
            Tuple of (success, error_message)
        """
        if self._old_ref is None:
            return False, "Not connected to old database"

        try:
            self._report_progress("Fetching data from old database...", 10)
            self._raw_data = self._old_ref.get() or {}

            if not self._raw_data:
                return False, "No data retrieved from old database"

            # Log what was found
            keys = list(self._raw_data.keys())
            logger.info(f"Fetched data with keys: {keys}")
            self._report_progress(f"Fetched data: {len(keys)} top-level keys", 15)

            return True, ""

        except Exception as e:
            logger.error(f"Failed to fetch data: {e}", exc_info=True)
            return False, f"Fetch failed: {str(e)}"

    def count_total_items(self) -> int:
        """
        Count total items that will be migrated before starting the migration.

        Returns:
            int: Total number of items to migrate
        """
        if not self._raw_data:
            return 0

        total_count = 0
        alerts_data = self._raw_data.get("alerts", {})

        # Count alerts
        for old_type in ALERT_TYPE_MAPPING.keys():
            old_alerts = alerts_data.get(old_type, {})
            if old_alerts:
                # Apply filters for counting
                for alert_id, alert_data in old_alerts.items():
                    # Skip disabled alerts if configured
                    if self.config.skip_disabled_alerts and alert_data.get(
                        "disabled", False
                    ):
                        continue
                    total_count += 1

        # Count alert logs if enabled
        if self.config.migrate_alert_logs:
            alert_logs = alerts_data.get("alert_logs", {})
            total_count += len(alert_logs)

        # Count asset files if enabled
        if self.config.copy_asset_files and self.config.old_assets_folder:
            total_count += self._count_asset_files()

        return total_count

    def _count_asset_files(self) -> int:
        """Count asset files to be copied."""
        if not self.config.old_assets_folder or not os.path.exists(
            self.config.old_assets_folder
        ):
            return 0

        count = 0
        try:
            for root, dirs, files in os.walk(self.config.old_assets_folder):
                # Skip certain directories
                dirs[:] = [
                    d for d in dirs if d not in [".git", "__pycache__", "node_modules"]
                ]
                count += len(files)
        except Exception as e:
            logger.warning(f"Error counting asset files: {e}")

        return count

    def load_from_json_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Load migration data from a JSON file instead of Firebase.
        Useful for testing or offline migration.

        Args:
            file_path: Path to the JSON file

        Returns:
            Tuple of (success, error_message)
        """
        try:
            self._report_progress(f"Loading data from {file_path}...", 10)

            with open(file_path, "r", encoding="utf-8") as f:
                self._raw_data = json.load(f)

            if not self._raw_data:
                return False, "No data in JSON file"

            keys = list(self._raw_data.keys())
            logger.info(f"Loaded data with keys: {keys}")
            self._report_progress(f"Loaded data: {len(keys)} top-level keys", 15)

            return True, ""

        except Exception as e:
            logger.error(f"Failed to load JSON file: {e}", exc_info=True)
            return False, f"Load failed: {str(e)}"

    def migrate_alerts(self) -> MigrationResult:
        """
        Migrate alert configurations from old format to new.

        Returns:
            MigrationResult with details of the migration
        """
        result = MigrationResult()
        result.start_time = time.time()

        alerts_data = self._raw_data.get("alerts", {})
        if not alerts_data:
            result.errors.append(
                {"type": "general", "id": "", "error": "No alerts data found"}
            )
            result.end_time = time.time()
            return result

        # Initialize alert state manager
        alert_state_manager.initialize()

        # Count total items first (this is a rough estimate for progress)
        total_alert_items = sum(
            len(alerts_data.get(old_type, {})) for old_type in ALERT_TYPE_MAPPING.keys()
        )
        result.total_items = total_alert_items

        # Process each alert type
        total_types = len(ALERT_TYPE_MAPPING)
        current_item = 0
        for type_idx, (old_type, new_type) in enumerate(ALERT_TYPE_MAPPING.items()):
            if self.is_cancelled():
                self._report_progress("Migration cancelled by user", 0)
                break

            old_alerts = alerts_data.get(old_type, {})
            if not old_alerts:
                continue

            self._report_progress_with_items(
                f"Migrating {old_type}: {len(old_alerts)} alerts",
                current_item,
                total_alert_items,
            )

            # Track alerts saved for this type to update collection at the end
            alerts_saved_for_type = []

            for alert_id, alert_data in old_alerts.items():
                if self.is_cancelled():
                    self._report_progress("Migration cancelled by user", 0)
                    break

                current_item += 1

                try:
                    # Determine actual alert type from the alerttype field in old data
                    old_alerttype = alert_data.get("alerttype", "").lower()
                    actual_type = ALERTTYPE_FIELD_MAPPING.get(old_alerttype, new_type)

                    # Generate the proper Mycelian alert ID
                    mycelian_alert_id = generate_mycelian_alert_id(
                        actual_type, alert_id
                    )

                    # Skip disabled alerts if configured
                    if self.config.skip_disabled_alerts and alert_data.get(
                        "disabled", False
                    ):
                        result.add_skip(
                            actual_type, mycelian_alert_id, "Alert is disabled"
                        )
                        continue

                    # Check if alert already exists
                    if self.config.skip_existing_alerts:
                        existing = alert_state_manager.get_alert_by_id(
                            actual_type, mycelian_alert_id
                        )
                        if existing:
                            result.add_skip(
                                actual_type, mycelian_alert_id, "Alert already exists"
                            )
                            continue

                    # Transform to new format using the actual alert type and Mycelian alert ID
                    new_alert_data = transform_old_alert_to_new(
                        alert_data, mycelian_alert_id, actual_type
                    )

                    # Determine if it's a range alert and get proper ID
                    _, is_range = determine_alert_category(
                        actual_type, mycelian_alert_id
                    )

                    # Save the alert without updating collection (batch mode)
                    success = alert_state_manager.save_alert(
                        actual_type,
                        mycelian_alert_id,
                        new_alert_data,
                        update_collection=False,
                    )

                    if success:
                        alerts_saved_for_type.append(actual_type)
                        result.add_success(
                            actual_type,
                            mycelian_alert_id,
                            new_alert_data.get("display_name", ""),
                        )
                    else:
                        result.add_error(
                            actual_type, mycelian_alert_id, "Failed to save alert"
                        )

                except Exception as e:
                    logger.error(
                        f"Error migrating alert {alert_id}: {e}", exc_info=True
                    )
                    result.add_error(
                        actual_type if "actual_type" in locals() else new_type,
                        alert_id,
                        str(e),
                    )

            # Update collections for this alert type after all alerts are saved
            if alerts_saved_for_type:
                try:
                    # Get unique alert types saved for this batch
                    unique_types = set(alerts_saved_for_type)
                    for alert_type in unique_types:
                        alert_state_manager.update_alert_collections(alert_type)
                except Exception as e:
                    logger.error(
                        f"Error updating collections for {old_type}: {e}", exc_info=True
                    )

        result.end_time = time.time()
        self._report_progress(
            f"Alert migration complete: {result.successful} migrated", 60
        )
        return result

    def migrate_alert_logs(self) -> MigrationResult:
        """
        Migrate alert logs/history to AlertStorage.

        Returns:
            MigrationResult with details of the migration
        """
        result = MigrationResult()
        result.start_time = time.time()

        alerts_data = self._raw_data.get("alerts", {})
        alert_logs = alerts_data.get("alert_logs", {})

        if not alert_logs:
            result.errors.append(
                {"type": "general", "id": "", "error": "No alert logs found"}
            )
            result.end_time = time.time()
            return result

        # Initialize alert state manager
        alert_state_manager.initialize()

        total_logs = len(alert_logs)
        result.total_items = total_logs

        for idx, (log_id, log_data) in enumerate(alert_logs.items()):
            if self.is_cancelled():
                self._report_progress("Migration cancelled by user", 0)
                break

            self._report_progress_with_items(
                f"Migrating log {idx + 1}/{total_logs}...", idx, total_logs
            )

            try:
                # Transform to new format
                new_entry = transform_alert_log_to_storage(log_id, log_data)

                # Store in alert storage
                success = alert_state_manager.store_completed_alert(log_id, new_entry)

                if success:
                    alert_type = new_entry.get("alert_type", "unknown")
                    result.add_success(alert_type, log_id, "")
                else:
                    result.add_error("log", log_id, "Failed to store alert log")

            except Exception as e:
                logger.error(f"Error migrating alert log {log_id}: {e}", exc_info=True)
                result.add_error("log", log_id, str(e))

        result.end_time = time.time()
        self._report_progress(
            f"Alert log migration complete: {result.successful} migrated", 80
        )
        return result

    def migrate_other_data(self) -> Tuple[bool, List[str]]:
        """
        Migrate non-alert data to JSON files.

        Returns:
            Tuple of (success, list of saved files)
        """
        saved_files = []

        # Create migration legacy data directory
        legacy_dir = get_data_path("data/migration_legacy")
        ensure_directory_exists(legacy_dir)

        self._report_progress("Saving other data to JSON files...", 82)

        # Keys to export as JSON files
        export_keys = [
            "settings",
            "logs",
            "UserDetails",
            "CounterStatus",
            "StreamID",
            "func_buttons",
            "ConnectedMods",
        ]

        # Also export alert metadata
        alerts_data = self._raw_data.get("alerts", {})
        alert_meta_keys = [
            "AlertDelay",
            "AlertDisReload",
            "AlertQueue",
            "AlertQueueStatus",
            "AlertStatus",
            "RecentChatters",
            "RecentChattersStatus",
        ]

        for key in export_keys:
            if key in self._raw_data:
                try:
                    file_path = os.path.join(legacy_dir, f"{key}.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(self._raw_data[key], f, indent=2, default=str)
                    saved_files.append(file_path)
                    logger.info(f"Saved {key} to {file_path}")
                except Exception as e:
                    logger.error(f"Failed to save {key}: {e}")

        # Export alert metadata
        if alerts_data:
            alert_meta = {}
            for key in alert_meta_keys:
                if key in alerts_data:
                    alert_meta[key] = alerts_data[key]

            if alert_meta:
                try:
                    file_path = os.path.join(legacy_dir, "alert_metadata.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(alert_meta, f, indent=2, default=str)
                    saved_files.append(file_path)
                except Exception as e:
                    logger.error(f"Failed to save alert metadata: {e}")

        self._report_progress(f"Saved {len(saved_files)} JSON files", 85)
        return True, saved_files

    def copy_asset_files(self, result: MigrationResult) -> MigrationResult:
        """
        Copy asset files (audio, gifs) from old location to new.

        Args:
            result: MigrationResult to add file copy results to

        Returns:
            Updated MigrationResult
        """
        print(
            "[legacy_migration] copy_asset_files: enter, "
            f"old_assets_folder={self.config.old_assets_folder!r}"
        )
        if not self.config.old_assets_folder:
            print(
                "[legacy_migration] copy_asset_files: early exit — "
                "no old_assets_folder set"
            )
            self._report_progress(
                "Skipping file copy - no assets folder configured", 90
            )
            return result

        if not os.path.exists(self.config.old_assets_folder):
            print(
                "[legacy_migration] copy_asset_files: early exit — path missing: "
                f"{self.config.old_assets_folder!r}"
            )
            self._report_progress(
                f"Assets folder not found: {self.config.old_assets_folder}", 90
            )
            return result

        self._report_progress("Copying asset files...", 86)

        # Get all migrated alerts and collect their file paths
        alerts_data = self._raw_data.get("alerts", {})
        print(
            "[legacy_migration] copy_asset_files: _raw_data has 'alerts' key: "
            f"{'alerts' in self._raw_data}, "
            f"alerts top-level keys: {list(alerts_data.keys())!r}"
        )
        files_to_copy = []

        for old_type in ALERT_TYPE_MAPPING.keys():
            old_alerts = alerts_data.get(old_type, {})
            for alert_id, alert_data in old_alerts.items():
                # Collect audio files
                audio_dir = _legacy_str_field(alert_data.get("audiodirectory", ""))
                audio_name = _legacy_str_field(alert_data.get("audioname", ""))
                if audio_name:
                    files_to_copy.append(
                        {
                            "alert_id": alert_id,
                            "type": "audio",
                            "dir": audio_dir,
                            "name": audio_name,
                            "bucket": old_type,
                        }
                    )

                # Collect GIF files
                gif_dir = _legacy_str_field(alert_data.get("gifdirectory", ""))
                gif_name = _legacy_str_field(alert_data.get("gifname", ""))
                if gif_name:
                    files_to_copy.append(
                        {
                            "alert_id": alert_id,
                            "type": "gif",
                            "dir": gif_dir,
                            "name": gif_name,
                            "bucket": old_type,
                        }
                    )

                # Collect randomized audio directories
                rand_dir = _legacy_str_field(alert_data.get("randomizeddirectory", ""))
                if rand_dir:
                    files_to_copy.append(
                        {
                            "alert_id": alert_id,
                            "type": "randomized_audio",
                            "dir": rand_dir,
                            "name": "*",  # Copy all files in directory
                            "bucket": old_type,
                        }
                    )

        # Copy files
        assets_base = get_data_path("assets")
        ensure_directory_exists(assets_base)

        print(
            f"[legacy_migration] copy_asset_files: files_to_copy count={len(files_to_copy)}, "
            f"assets_base={assets_base!r}"
        )
        for i, sample in enumerate(files_to_copy[:5]):
            print(f"[legacy_migration] copy_asset_files:   [{i}] {sample!r}")
        if len(files_to_copy) > 5:
            print(
                f"[legacy_migration] copy_asset_files:   ... and {len(files_to_copy) - 5} more"
            )
        if not files_to_copy:
            print(
                "[legacy_migration] copy_asset_files: no file entries collected — "
                "check alert fields audiodirectory/audioname/gifdirectory/gifname/"
                "randomizeddirectory in loaded data"
            )

        total_files = len(files_to_copy)
        for idx, file_info in enumerate(files_to_copy):
            if total_files and idx % 10 == 0:
                progress = 86 + (idx / total_files * 9)
                self._report_progress(
                    f"Copying file {idx + 1}/{total_files}...", progress
                )

            bucket = file_info.get("bucket", "")
            default_sub = LEGACY_BUCKET_STATIC_SUBDIR.get(bucket, "")

            rel_sub = legacy_relative_subpath_under_assets_root(
                file_info["dir"], self.config.old_assets_folder
            )
            if not rel_sub and default_sub:
                rel_sub = default_sub
            file_name = file_info["name"]

            # Source: resolve under old assets root (handles static/ duplication and "None" dirs)
            source_path = join_under_assets_root(
                self.config.old_assets_folder, rel_sub, file_name
            )

            # Destination: under get_data_path("assets") — static/ -> alerts/, never duplicate assets/
            dir_clean = _legacy_str_field(file_info["dir"])
            if dir_clean:
                dest_rel = migration_dest_rel_under_assets_db_dir(file_info["dir"])
            else:
                dest_rel = f"alerts/{default_sub}" if default_sub else ""
            dest_parts = [x for x in dest_rel.replace("\\", "/").split("/") if x]
            dest_dir = (
                os.path.join(assets_base, *dest_parts) if dest_parts else assets_base
            )
            dest_path = os.path.join(dest_dir, file_name)

            # Handle directory copies
            if file_name == "*":
                source_dir = join_under_assets_root(
                    self.config.old_assets_folder, rel_sub
                )
                debug_this = idx < 8
                if debug_this:
                    print(
                        "[legacy_migration] copy_asset_files: dir wildcard "
                        f"alert_id={file_info['alert_id']!r} type={file_info['type']!r}\n"
                        f"  source_dir={source_dir!r} isdir={os.path.isdir(source_dir)}\n"
                        f"  dest_dir={dest_dir!r}"
                    )
                if os.path.isdir(source_dir):
                    try:
                        ensure_directory_exists(dest_dir)
                        listed = os.listdir(source_dir)
                        if debug_this:
                            print(
                                f"  listdir count={len(listed)} sample={listed[:5]!r}"
                            )
                        copied_here = 0
                        skipped_exists = 0
                        for f in listed:
                            src = os.path.join(source_dir, f)
                            dst = os.path.join(dest_dir, f)
                            if os.path.isfile(src) and not os.path.exists(dst):
                                shutil.copy2(src, dst)
                                result.add_copied_file(dst)
                                copied_here += 1
                                if copied_here <= 3 and debug_this:
                                    print(f"    copied {src!r} -> {dst!r}")
                            elif os.path.isfile(src) and os.path.exists(dst):
                                skipped_exists += 1
                        if debug_this:
                            print(
                                f"  dir summary: copied={copied_here}, "
                                f"skipped_already_exists={skipped_exists}"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to copy directory {source_dir}: {e}")
                        print(
                            f"[legacy_migration] copy_asset_files: EXCEPTION dir copy "
                            f"{source_dir!r}: {e!r}"
                        )
                        result.add_missing_file(
                            file_info["alert_id"], source_dir, file_info["type"]
                        )
                else:
                    print(
                        "[legacy_migration] copy_asset_files: randomized dir missing "
                        f"source_dir={source_dir!r}"
                    )
                continue

            # Copy individual file
            debug_file = idx < 12
            if debug_file:
                print(
                    "[legacy_migration] copy_asset_files: file "
                    f"idx={idx} type={file_info['type']!r} alert_id={file_info['alert_id']!r}\n"
                    f"  source_path={source_path!r} exists={os.path.exists(source_path)}\n"
                    f"  dest_path={dest_path!r} dest_exists={os.path.exists(dest_path)}"
                )
            if os.path.exists(source_path):
                try:
                    ensure_directory_exists(dest_dir)
                    if not os.path.exists(dest_path):
                        shutil.copy2(source_path, dest_path)
                        result.add_copied_file(dest_path)
                        if debug_file:
                            print(f"  -> shutil.copy2 ok -> {dest_path!r}")
                    elif debug_file:
                        print(
                            "  -> skip copy: destination already exists "
                            f"({dest_path!r})"
                        )
                except Exception as e:
                    logger.warning(f"Failed to copy {source_path}: {e}")
                    print(
                        f"[legacy_migration] copy_asset_files: EXCEPTION file copy "
                        f"{source_path!r}: {e!r}"
                    )
                    result.add_missing_file(
                        file_info["alert_id"], source_path, file_info["type"]
                    )
            else:
                if debug_file or idx < 20:
                    print(
                        "[legacy_migration] copy_asset_files: missing source "
                        f"{source_path!r} (type={file_info['type']!r})"
                    )
                result.add_missing_file(
                    file_info["alert_id"], source_path, file_info["type"]
                )

        self._report_progress(
            f"File copy complete: {len(result.copied_files)} copied, {len(result.missing_files)} missing",
            95,
        )
        print(
            "[legacy_migration] copy_asset_files: done — "
            f"copied={len(result.copied_files)}, missing={len(result.missing_files)}"
        )
        if result.copied_files:
            print(
                "[legacy_migration] copy_asset_files: copied sample: "
                f"{result.copied_files[:5]!r}"
            )
        return result

    def run_full_migration(self) -> MigrationResult:
        """
        Run the complete migration process.

        Returns:
            Combined MigrationResult
        """
        combined_result = MigrationResult()
        combined_result.start_time = time.time()

        self._report_progress("Starting migration...", 0)

        # Connect to old database (if not using JSON file)
        if not self._raw_data:
            if self.config.old_service_account_path:
                success, error = self.connect_to_old_database()
                if not success:
                    combined_result.add_error("connection", "", error)
                    combined_result.end_time = time.time()
                    return combined_result

                # Fetch data
                success, error = self.fetch_all_data()
                if not success:
                    combined_result.add_error("fetch", "", error)
                    combined_result.end_time = time.time()
                    return combined_result
            else:
                combined_result.add_error(
                    "config", "", "No data source configured (Firebase or JSON file)"
                )
                combined_result.end_time = time.time()
                return combined_result

        # Migrate alerts
        alert_result = self.migrate_alerts()
        combined_result = self._merge_results(combined_result, alert_result)

        # Migrate alert logs
        if self.config.migrate_alert_logs:
            log_result = self.migrate_alert_logs()
            combined_result = self._merge_results(combined_result, log_result)
            # Add log count to type counts
            if log_result.successful > 0:
                combined_result.type_counts["alert_logs"] = log_result.successful

        # Migrate other data
        if self.config.migrate_other_data:
            self.migrate_other_data()

        # Copy asset files
        print(
            "[legacy_migration] run_full_migration: copy_asset_files="
            f"{self.config.copy_asset_files!r}, "
            f"old_assets_folder={self.config.old_assets_folder!r}"
        )
        if self.config.copy_asset_files:
            combined_result = self.copy_asset_files(combined_result)
        else:
            print(
                "[legacy_migration] run_full_migration: skipping copy_asset_files "
                "(copy_asset_files is False)"
            )

        combined_result.end_time = time.time()
        self._report_progress("Migration complete!", 100)

        return combined_result

    def _merge_results(
        self, combined: MigrationResult, individual: MigrationResult
    ) -> MigrationResult:
        """Merge an individual migration result into the combined result."""
        combined.total_items += individual.total_items
        combined.successful += individual.successful
        combined.failed += individual.failed
        combined.skipped += individual.skipped
        combined.errors.extend(individual.errors)
        combined.migrated_items.extend(individual.migrated_items)
        # Merge type counts
        for key, value in individual.type_counts.items():
            combined.type_counts[key] = combined.type_counts.get(key, 0) + value
        # Merge missing files and copied files
        combined.missing_files.extend(individual.missing_files)
        combined.copied_files.extend(individual.copied_files)
        return combined

    def cleanup(self):
        """Clean up resources after migration"""
        if self._old_app:
            try:
                firebase_admin.delete_app(self._old_app)
            except Exception as e:
                logger.warning(f"Error cleaning up Firebase app: {e}")
            self._old_app = None
            self._old_ref = None
            self._old_ref = None

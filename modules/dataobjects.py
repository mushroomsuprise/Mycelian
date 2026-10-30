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

"""
Data objects and state management for Mycelian.

This module contains all the data classes and the StateManager that handles
the application's configuration and state.
"""

import asyncio
import copy
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from .encryption_utils import (
    EncryptionError,
    ensure_decrypted,
    ensure_encrypted,
    is_encrypted,
)
from .notification_engine import nav_actions_settings, notify, notify_critical

# How each settings blob is named in notifications ("Couldn't save your Twitch settings").
_BLOB_LABELS = {
    "twitch_data": "Twitch",
    "app_settings": "app",
    "psn_settings_data": "PSN",
    "spotify_data": "Spotify",
    "database_settings": "database",
    "youtube_data": "YouTube",
    "chatbot_data": "chatbot",
    "obs_data": "OBS",
    "discord_data": "Discord",
}
# A failed save already showing stays one entry; repeats within this gap don't pop up again.
_SAVE_FAILURE_POPUP_GAP_SEC = 60.0


def _blob_names(state_keys) -> str:
    names = sorted({_BLOB_LABELS.get(k, k) for k in state_keys})
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _database_is_up() -> bool:
    try:
        from . import database_manager

        return bool(database_manager.database_manager._initialized)
    except Exception:
        return False


def _database_label() -> str:
    try:
        from . import database_manager

        return database_manager.database_label()
    except Exception:
        return "configured"

# Import database_manager in functions that need it, not at the module level
# This avoids circular import issues

logger = logging.getLogger(__name__)

# Serialize DiscordData DB read-modify-writes so a blank concurrent save cannot
# overwrite a token that was just persisted on another thread.
_discord_db_write_lock = threading.Lock()
_SECRET_FIELDS = {
    "twitch_data": ("client_secret", "auth_token", "refresh_token"),
    "spotify_data": ("client_secret", "access_token", "refresh_token"),
    "youtube_data": ("api_key", "oauth_client_secret", "access_token", "refresh_token"),
    "chatbot_data": ("auth_token", "refresh_token"),
}


def _is_blank_secret(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not str(value).strip())


def _keep_existing_if_blank_secret(field: str, incoming: Any, existing: Any, secret_names) -> Any:
    if field in secret_names and _is_blank_secret(incoming) and existing:
        return existing
    return incoming


def _write_discord_data_merged(payload: Dict[str, Any]) -> bool:
    """Atomically merge and write DiscordData. Never blank an existing bot_token."""
    from . import database_manager

    path = "DiscordData"
    with _discord_db_write_lock:
        existing, ok = database_manager.get_data_checked(path)
        if not ok:
            # Merging onto a failed read would drop every stored field.
            logger.error("DiscordData write skipped: the stored copy could not be read")
            return False
        existing = existing or {}
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        merged.update(payload or {})

        incoming_token = str((payload or {}).get("bot_token") or "").strip()
        existing_token = str(existing.get("bot_token") or "").strip()
        if incoming_token:
            merged["bot_token"] = (
                incoming_token
                if is_encrypted(incoming_token)
                else ensure_encrypted(incoming_token)
            )
            logger.info(
                "DiscordData write: persisting bot_token (len=%s)",
                len(incoming_token),
            )
        elif existing_token:
            merged["bot_token"] = existing["bot_token"]
            logger.info(
                "DiscordData write: preserving existing bot_token (blank in payload)"
            )
        else:
            merged["bot_token"] = ""

        return bool(database_manager.set_data(path, merged))


@dataclass
class TwitchData:
    """
    Data class for storing Twitch API related data
    """

    client_id: str = ""
    client_secret: str = ""
    auth_token: str = ""
    refresh_token: str = ""
    user_id: str = ""
    token_expiry: str = ""  # ISO format datetime string
    current_category: str = ""  # Current Twitch category/game name
    twitch: object = None
    authenticator: object = None
    user: object = None
    eventsub: object = None
    log_level: str = "INFO"
    # When False, Twitch events skip ALERT_QUEUE / instant_alert / activity feed
    alerts_enabled: bool = True

    def __post_init__(self):
        """Log when a new TwitchData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class ChatbotData:
    """
    Data class for storing Chatbot API related data (separate from main Twitch account)
    """

    auth_token: str = ""
    refresh_token: str = ""
    user_id: str = ""
    token_expiry: str = ""  # ISO format datetime string
    user: object = None  # For user info

    def __post_init__(self):
        """Log when a new ChatbotData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class AppSettings:
    """
    Data class for storing application settings
    """

    streamer_name: str = "mycelian"
    streamer_id: str = ""
    version: str = "1.14.0"
    build_number: str = "28b2dc8d51adb332a85d7aca6b2bdeaac917694e"
    build_date: str = "October 29th 2026"
    alert_volume: float = 0.5
    auto_reconnect: bool = True
    auto_raid_helix_shoutout: bool = True
    auto_raid_chat_shoutout: bool = True
    current_theme: str = "dark"
    notifications_enabled: bool = True
    # Fine-grained notification preferences (see notify_settings.NotifyPrefs).
    notify_levels: dict = field(default_factory=lambda: {"error": True, "warning": True, "info": True, "success": True})
    notify_durations: dict = field(default_factory=lambda: {"error": 0, "warning": 8, "info": 4, "success": 3})
    notify_position: str = "top-right"
    notify_max_visible: int = 4
    notify_desktop_when_minimized: bool = True
    notify_record_muted: bool = True
    notify_muted: dict = field(default_factory=dict)  # source id -> list of muted levels
    status_footer_enabled: bool = True
    status_footer_minimal: bool = False
    # Badge looks (see notification_engine.FOOTER_FULL_STYLES / FOOTER_MINIMAL_STYLES)
    status_footer_full_style: str = "stacked"
    status_footer_minimal_style: str = "tiles"
    auto_update: bool = True
    update_check_interval_minutes: int = 30  # 5-120; read by updater.UpdateManager
    activity_feed_limit: int = 20
    activity_feed_max_pages: int = 5
    # Activity Feed look (tab and OBS dock; see uiwindows/activity_feed_model.FEED_SETTINGS)
    activity_feed_layout: str = "rows"  # rows | cards | ticker
    activity_feed_density: str = "comfortable"  # compact | comfortable | spacious
    activity_feed_color_mode: str = "icon"  # icon | bar | tint | none
    activity_feed_show_icons: bool = True
    activity_feed_tier_badge: str = "icon"  # icon | name | hidden
    activity_feed_platform_marks: bool = True
    activity_feed_viewer_messages: bool = True
    activity_feed_show_sub_duration: bool = True  # tag on multi-month subs and gifts ("6 months paid ahead")
    activity_feed_sub_duration_style: str = "split"  # split | chip | calendar | dots
    activity_feed_sub_duration_place: str = "inline"  # inline | right | below
    activity_feed_wrap_text: bool = False
    activity_feed_times: str = "relative"  # relative | clock | hidden
    activity_feed_earlier_after_minutes: int = 30  # 0 = never
    activity_feed_order_numbers: bool = True
    activity_feed_timeline_dots: bool = True
    activity_feed_highlight_new_minutes: int = 5  # 0 = off
    activity_feed_now_playing: bool = True
    activity_feed_alert_timer: str = "strip"  # strip | item | off
    activity_feed_up_next: int = 1  # 0 | 1 | 3 | 5
    activity_feed_text_size: str = "medium"  # small | medium | large
    activity_feed_person_hours: int = 0  # 0 = not chosen yet (uses the old dock setting, else 12)
    alert_storage_auto_trim: bool = False
    alert_storage_trim_mode: str = "both"
    alert_storage_keep_count: int = 500
    alert_storage_keep_days: int = 30
    start_maximized: bool = True
    ui_font_family: str = ""
    ui_scale_percent: int = 100
    minimize_to_tray: bool = False
    start_minimized: bool = False
    run_at_startup: bool = False
    last_main_tab: str = "Activity Feed"
    last_settings_tab: str = "Twitch"
    window_width: int = 0
    window_height: int = 0
    window_x: int = -1
    window_y: int = -1
    # Help window geometry. Position can be negative on multi-monitor setups, so a
    # separate flag marks whether one was saved.
    help_window_width: int = 0
    help_window_height: int = 0
    help_window_x: int = 0
    help_window_y: int = 0
    help_window_has_position: bool = False

    def __post_init__(self):
        """Log when a new AppSettings instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class PSNSettingsData:
    """
    Data class for storing PSN specific settings.
    """

    npsso_code: str = ""
    psn_username: str = ""  # PSN username/online_id for API calls
    # Add other PSN-specific UI settings here if needed later

    def __post_init__(self):
        """Log when a new PSNSettingsData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class SpotifyData:
    """
    Data class for storing Spotify API related data
    """

    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expiry: Optional[float] = None
    connection_status: str = "Disconnected"
    market_country: str = ""  # User-selected market country for API calls

    # Current playback data
    track_name: str = "Nothing playing"
    artist_name: str = "Nothing playing"
    album_name: str = ""
    album_image_url: str = ""
    current_tracktime: str = "0:00"
    track_length: str = "0:00"
    current_tracktime_seconds: float = 0.0
    track_length_seconds: float = 0.0
    is_playing: bool = False
    progress_percentage: float = 0.0

    def __post_init__(self):
        """Log when a new SpotifyData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class DatabaseSettings:
    """
    Data class for storing database configuration settings
    """

    database_type: str = "sql"  # "sql", "firebase", "mongodb"

    # SQL Configuration
    sql_database_path: str = "mycelian.db"

    # Firebase Configuration
    firebase_service_account_path: str = "ServiceAccountKey.json"
    firebase_database_url: str = ""

    # MongoDB Configuration
    mongodb_connection_string: str = "mongodb://localhost:27017/"
    mongodb_database_name: str = "mycelian"

    # Common Configuration
    connection_timeout: int = 30
    retry_attempts: int = 3

    def __post_init__(self):
        """Log when a new DatabaseSettings instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


def _apply_database_connection_settings(temp_db_settings: "DatabaseSettings") -> None:
    """Apply connection fields from DatabaseSettings without changing live database_type.

    ``config.json`` remains the source of truth for which backend is active.
    """
    from . import database_manager

    database_manager.database_manager.update_config(
        sql_database_path=temp_db_settings.sql_database_path,
        firebase_service_account_path=temp_db_settings.firebase_service_account_path,
        firebase_database_url=temp_db_settings.firebase_database_url,
        mongodb_connection_string=temp_db_settings.mongodb_connection_string,
        mongodb_database_name=temp_db_settings.mongodb_database_name,
        streamer_name="mycelian",
        connection_timeout=temp_db_settings.connection_timeout,
        retry_attempts=temp_db_settings.retry_attempts,
    )


@dataclass
class PSNData:
    current_game_name: str = ""
    current_game_art_url: str = ""
    trophy_counts: dict = field(default_factory=dict)  # Overall trophy counts
    current_game_trophies: dict = field(
        default_factory=dict
    )  # Current game earned trophies
    current_game_defined_trophies: dict = field(
        default_factory=dict
    )  # Current game defined trophies
    current_game_progress: int | None = None  # Current game completion percentage
    all_games_data: dict = field(default_factory=dict)  # All games trophy data
    npsso_code: str = ""
    online_id: str = ""
    account_id: str = ""
    is_online: bool = False
    connection_status: str = "Disconnected"
    presence: dict = field(default_factory=dict)


@dataclass
class YouTubeChannelData:
    """
    Data class for individual YouTube channel information
    """

    channel_url: str = ""
    channel_id: str = ""
    channel_title: str = ""
    latest_video_id: str = ""
    latest_video_title: str = ""
    latest_video_url: str = ""
    latest_video_published_at: str = ""
    latest_video_thumbnail: str = ""
    last_updated: str = ""


@dataclass
class YouTubeData:
    """
    Data class for storing YouTube API related data
    """

    api_key: str = ""
    # Support multiple channels separated by "|"
    channel_urls: str = ""
    # Dictionary to store individual channel data
    channels: dict = field(default_factory=dict)
    # Global latest video across all channels
    latest_video_id: str = ""
    latest_video_title: str = ""
    latest_video_url: str = ""
    latest_video_published_at: str = ""
    latest_video_thumbnail: str = ""
    latest_video_channel: str = ""  # Channel name/title of the global latest video
    playlist_filter: list = field(default_factory=list)
    connection_status: str = "Disconnected"
    last_updated: str = ""
    # Google OAuth for live chat / memberships / Super Chats
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expiry: str = ""  # ISO format datetime string
    oauth_channel_id: str = ""
    oauth_channel_title: str = ""
    # When True and OAuth tokens exist, run the live chat poller
    live_chat_enabled: bool = True
    live_chat_status: str = "Not authorized"  # Not authorized / Offline / Live / Error
    # When False, YouTube monetization events skip alert pipeline
    alerts_enabled: bool = True

    def __post_init__(self):
        """Log when a new YouTubeData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


@dataclass
class OBSData:
    """OBS WebSocket connection settings (persisted in app database)."""

    host: str = "localhost"
    port: int = 4455
    password: str = ""
    enabled: bool = True
    connection_status: str = "Disconnected"
    last_error: str = ""


@dataclass
class DiscordData:
    """Discord bot integration settings (persisted in app database)."""

    bot_token: str = ""
    connection_status: str = "Disconnected"
    bot_user_id: str = ""
    bot_username: str = ""
    application_id: str = ""
    # Automatic "Stream is live" announcement
    go_live_enabled: bool = False
    go_live_message: str = (
        "Stream is live on {platform}! {url}"
    )
    # [{guild_id, channel_id, guild_name?, channel_name?}, ...]
    go_live_channels: list = field(default_factory=list)

    def __post_init__(self):
        if self.go_live_channels is None:
            self.go_live_channels = []


class StateManager:
    """
    Manages application state including TwitchData and AppSettings.

    This class serves as the central source of truth for application settings and Twitch data,
    handling both the in-memory state and synchronization with Firebase.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._state = {
            "twitch_data": {},
            "app_settings": {},
            "psn_settings_data": {},  # Added for PSN
            "spotify_data": {},  # Added for Spotify
            "database_settings": {},  # Added for DatabaseSettings
            "youtube_data": {},  # Added for YouTube
            "chatbot_data": {},  # Added for Chatbot
            "obs_data": {},  # OBS WebSocket settings
            "discord_data": {},  # Discord bot integration
        }
        self._paths = {
            "twitch_data": "TwitchData",
            "app_settings": "AppSettings",
            "psn_settings_data": "PSNSettings",  # Firebase path for PSN settings
            "spotify_data": "SpotifyData",  # Firebase path for Spotify data
            "database_settings": "DatabaseSettings",  # Firebase path for DatabaseSettings
            "youtube_data": "YouTubeData",  # Firebase path for YouTube data
            "chatbot_data": "ChatbotData",  # Firebase path for Chatbot data
            "obs_data": "OBSData",
            "discord_data": "DiscordData",
        }
        self._initialized = False
        self._changes_pending = False
        self._changed_fields = set()
        # Blobs whose load failed: state_key -> state right after that load.
        # They are re-read before any write so defaults never replace stored data.
        self._unreadable_blobs: Dict[str, Dict[str, Any]] = {}
        # A failed SpotifyData write keeps unchanged playback fields dirty, so
        # the next save retries it as it did before (DBX-09).
        self._spotify_write_failed = False
        self._last_save_failure_toast = float("-inf")

        # Initialize with default data
        self._twitch_data = TwitchData()
        self._app_settings = AppSettings()
        self._psn_settings_data = (
            PSNSettingsData()
        )  # Initialize default PSN settings data
        self._live_psn_data: Optional[PSNData] = (
            PSNData()
        )  # For live PSN data from psnapi.PSNClient
        self._spotify_data = SpotifyData()  # Initialize default Spotify data
        self._database_settings = (
            DatabaseSettings()
        )  # Initialize default DatabaseSettings data
        self._youtube_data = YouTubeData()  # Initialize default YouTube data
        self._chatbot_data = ChatbotData()  # Initialize default Chatbot data
        self._obs_data = OBSData()
        self._discord_data = DiscordData()

    def initialize(self):
        """Initialize the state manager by loading data from Firebase"""
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing state manager")
            self._load_from_firebase()
            self._initialized = True
            logger.debug("State manager initialized")

    async def initialize_async(self):
        """Initialize the state manager by loading data from Firebase asynchronously"""
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing state manager")
            await self._load_from_firebase_async()
            self._initialized = True
            logger.debug("State manager initialized")

    def initialize_with_data(self, all_data: Dict[str, Any]):
        """Initialize the state manager with pre-loaded data

        Args:
            all_data: Dictionary mapping database paths to their data
        """
        if self._initialized:
            return

        with self._lock:
            logger.debug("Initializing state manager with pre-loaded data")

            # Load database settings first to configure database manager
            try:
                database_settings_data = all_data.get(
                    self._paths["database_settings"], {}
                )
                if not isinstance(database_settings_data, dict):
                    database_settings_data = {}  # corrupt blob: use defaults
                self._state["database_settings"] = database_settings_data

                # If we have database settings, update the database manager configuration
                if database_settings_data:
                    # Create a temporary DatabaseSettings object to get the configuration
                    temp_db_settings = DatabaseSettings(
                        **{
                            k: v
                            for k, v in database_settings_data.items()
                            if k in DatabaseSettings.__dataclass_fields__
                        }
                    )

                    _apply_database_connection_settings(temp_db_settings)
                    logger.debug(
                        "Updated database manager connection settings (type left to config.json)"
                    )

            except Exception as e:
                logger.debug(f"Could not load database settings, using defaults: {e}")

            # Load all data from the pre-loaded dictionary
            self._state["twitch_data"] = all_data.get(self._paths["twitch_data"], {})
            self._state["app_settings"] = all_data.get(self._paths["app_settings"], {})
            self._state["psn_settings_data"] = all_data.get(
                self._paths["psn_settings_data"], {}
            )
            self._state["spotify_data"] = all_data.get(self._paths["spotify_data"], {})
            self._state["youtube_data"] = all_data.get(self._paths["youtube_data"], {})
            self._state["chatbot_data"] = all_data.get(self._paths["chatbot_data"], {})
            self._state["obs_data"] = all_data.get(self._paths["obs_data"], {})
            self._state["discord_data"] = all_data.get(self._paths["discord_data"], {})

            # Update the local objects
            self._update_local_objects()

            from . import database_manager

            unreadable = []
            for state_key, path in self._paths.items():
                if path in all_data and database_manager.startup_read_failed(path):
                    self._mark_blob_unreadable(state_key)
                    unreadable.append(state_key)
            if unreadable:
                self._notify_unreadable(unreadable)

            self._initialized = True
            logger.debug("State manager initialized with pre-loaded data")

    def _mark_blob_unreadable(self, state_key: str) -> None:
        """Remember a blob whose load failed (call with ``_lock`` held, after
        ``_update_local_objects``)."""
        self._unreadable_blobs[state_key] = copy.deepcopy(
            self._state.get(state_key) or {}
        )
        logger.warning(
            "%s could not be read; it will be re-read before it is saved",
            self._paths.get(state_key, state_key),
        )

    def _notify_unreadable(self, state_keys) -> None:
        """Say once that some settings couldn't be read (so they show defaults and
        won't be written). Skipped when the whole database is down: that has its own
        notification."""
        if not state_keys or not _database_is_up():
            return
        notify(
            "Mycelian is showing defaults for them and won't overwrite your saved copy "
            "until it can read it.",
            type="warning",
            source="database",
            title=f"Couldn't read your {_blob_names(state_keys)} settings",
            hint="Check the connection in Settings → Database. Mycelian reads them again before the next save.",
            details="Unreadable: " + ", ".join(self._paths.get(k, k) for k in state_keys),
            dedupe_key="state:unreadable",
            dedupe_cooldown_sec=3600,
            actions=nav_actions_settings("Database"),
        )

    def _notify_load_failed(self, exc: Exception) -> None:
        if not _database_is_up():
            return  # the database layer already said it can't connect
        notify_critical(
            "Mycelian is using defaults for anything it couldn't read.",
            source="database",
            title="Couldn't load your settings",
            hint="Check the connection in Settings → Database, then restart Mycelian.",
            details=f"{type(exc).__name__}: {exc}",
            dedupe_key="state:load",
            actions=nav_actions_settings("Database"),
        )

    def _notify_save_failed(self, failed: Dict[str, Tuple[str, str]], blocked) -> None:
        """One notification for a save that didn't fully land (changes stay pending)."""
        keys = set(failed) | set(blocked)
        names = _blob_names(keys)
        label = _database_label()
        details = [
            f"{self._paths.get(k, k)}: {status}{' - ' + reason if reason else ''}"
            for k, (status, reason) in sorted(failed.items())
        ] + [f"{self._paths.get(k, k)}: not written (couldn't be read first)" for k in sorted(blocked)]
        if failed:
            statuses = {status for status, _ in failed.values()}
            if "down" in statuses or not _database_is_up():
                why = f"Mycelian can't reach the {label} database."
            elif "timeout" in statuses:
                why = f"The {label} database didn't respond in time."
            else:
                why = f"The {label} database didn't accept the change."
            title, level = f"Couldn't save your {names} settings", "error"
        else:
            why = "Mycelian couldn't read the saved copy first, so it won't overwrite it."
            title, level = f"Didn't save your {names} settings", "warning"
        import time

        now = time.monotonic()
        quiet_popup = now - self._last_save_failure_toast < _SAVE_FAILURE_POPUP_GAP_SEC
        if not quiet_popup:
            self._last_save_failure_toast = now
        notify(
            f"{why} Your changes are kept and will be saved the next time you press Save.",
            type=level,
            source="database",
            title=title,
            hint="Check the connection in Settings → Database, then press Save again.",
            details="\n".join(details),
            dedupe_key="state:save_failed",
            dedupe_cooldown_sec=600,
            skip_toast=quiet_popup,
            actions=nav_actions_settings("Database"),
        )

    def _read_blob_checked(self, state_key: str, failed: List[str]) -> Dict[str, Any]:
        from . import database_manager

        data, ok = database_manager.get_data_checked(self._paths[state_key])
        if not ok:
            failed.append(state_key)
        return data or {}

    def _recover_unreadable_blobs(self, state_keys) -> set:
        """Re-read blobs whose load failed, before writing them.

        On success the stored copy becomes the base and the fields changed in
        memory since the failed load are applied on top. Returns the keys that
        still cannot be read; callers must not write those.
        """
        from . import database_manager

        blocked = set()
        for state_key in state_keys:
            with self._lock:
                if state_key not in self._unreadable_blobs:
                    continue
            data, ok = database_manager.get_data_checked(self._paths[state_key])
            if not ok:
                logger.warning(
                    "Not saving %s: it could not be read from the database",
                    self._paths[state_key],
                )
                blocked.add(state_key)
                continue
            with self._lock:
                baseline = self._unreadable_blobs.pop(state_key, None)
                if baseline is None:
                    continue
                merged = dict(data) if isinstance(data, dict) else {}
                for key, value in (self._state.get(state_key) or {}).items():
                    if key not in baseline or baseline[key] != value:
                        merged[key] = value
                self._state[state_key] = merged
                self._update_local_objects()
                logger.info(
                    "Re-read %s after a failed load; local changes kept",
                    self._paths[state_key],
                )
        return blocked

    def _load_from_firebase(self):
        """Load all data from Firebase and update the state"""
        try:
            # First, try to load database settings to configure the database manager
            failed: List[str] = []
            try:
                database_settings_data = self._read_blob_checked(
                    "database_settings", failed
                )
                if not isinstance(database_settings_data, dict):
                    database_settings_data = {}  # corrupt blob: use defaults
                self._state["database_settings"] = database_settings_data

                # If we have database settings, update the database manager configuration
                if database_settings_data:
                    temp_db_settings = DatabaseSettings(
                        **{
                            k: v
                            for k, v in database_settings_data.items()
                            if k in DatabaseSettings.__dataclass_fields__
                        }
                    )

                    _apply_database_connection_settings(temp_db_settings)
                    logger.debug(
                        "Updated database manager connection settings (type left to config.json)"
                    )

            except Exception as e:
                logger.debug(f"Could not load database settings, using defaults: {e}")

            # Load Twitch data
            twitch_data = self._read_blob_checked("twitch_data", failed)
            self._state["twitch_data"] = twitch_data

            # Load app settings
            app_settings = self._read_blob_checked("app_settings", failed)
            self._state["app_settings"] = app_settings

            # Load PSN settings data
            psn_settings_data = self._read_blob_checked("psn_settings_data", failed)
            self._state["psn_settings_data"] = psn_settings_data

            # Load Spotify data
            spotify_data = self._read_blob_checked("spotify_data", failed)
            self._state["spotify_data"] = spotify_data

            # Load YouTube data
            youtube_data = self._read_blob_checked("youtube_data", failed)
            self._state["youtube_data"] = youtube_data

            # Load Chatbot data
            chatbot_data = self._read_blob_checked("chatbot_data", failed)
            self._state["chatbot_data"] = chatbot_data

            obs_data = self._read_blob_checked("obs_data", failed)
            self._state["obs_data"] = obs_data

            discord_data = self._read_blob_checked("discord_data", failed)
            self._state["discord_data"] = discord_data
            raw_tok = str((discord_data or {}).get("bot_token") or "")
            logger.info(
                "Loaded DiscordData from DB (bot_token_len=%s encrypted=%s)",
                len(raw_tok),
                bool(raw_tok),
            )

            # Update the local objects
            self._update_local_objects()
            for state_key in failed:
                self._mark_blob_unreadable(state_key)
            self._notify_unreadable(failed)
            logger.info(
                "Discord bot_token after decrypt (len=%s)",
                len((getattr(self._discord_data, "bot_token", "") or "").strip()),
            )
        except Exception as e:
            logger.error(f"Error loading data from database: {str(e)}", exc_info=True)
            self._notify_load_failed(e)

    async def _load_from_firebase_async(self):
        """Load all data from Firebase asynchronously and update the state"""
        try:
            # Import database_manager here to avoid circular imports
            from . import database_manager

            # Get all paths to load
            paths = list(self._paths.values())

            # Load all data in parallel
            logger.debug(f"Loading {len(paths)} state paths in parallel")
            results = await database_manager.get_multiple_data_async(paths)

            # Process results
            for state_key, firebase_path in self._paths.items():
                data = results.get(firebase_path, {}) or {}
                self._state[state_key] = data

            # If we have database settings, update the database manager configuration
            if self._state.get("database_settings"):
                try:
                    database_settings_data = self._state["database_settings"]
                    temp_db_settings = DatabaseSettings(
                        **{
                            k: v
                            for k, v in database_settings_data.items()
                            if k in DatabaseSettings.__dataclass_fields__
                        }
                    )

                    _apply_database_connection_settings(temp_db_settings)
                    logger.debug(
                        "Updated database manager connection settings (type left to config.json)"
                    )

                except Exception as e:
                    logger.debug(
                        f"Could not update database manager config from loaded settings: {e}"
                    )

            # Update the local objects
            self._update_local_objects()

            logger.debug("Loaded data from database")
        except Exception as e:
            logger.error(f"Error loading data from database: {str(e)}", exc_info=True)
            self._notify_load_failed(e)

    def _update_local_objects(self):
        """Update the local objects from the current state"""
        # Update Twitch data
        twitch_dict = self._state["twitch_data"]
        if twitch_dict:
            # Filter out any keys not in TwitchData
            valid_keys = [f.name for f in TwitchData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in twitch_dict.items() if k in valid_keys}

            # Decrypt sensitive fields
            if "client_id" in filtered_dict:
                filtered_dict["client_id"] = ensure_decrypted(
                    filtered_dict["client_id"]
                )
            if "client_secret" in filtered_dict:
                filtered_dict["client_secret"] = ensure_decrypted(
                    filtered_dict["client_secret"]
                )
            if "auth_token" in filtered_dict:
                filtered_dict["auth_token"] = ensure_decrypted(
                    filtered_dict["auth_token"]
                )
            if "refresh_token" in filtered_dict:
                filtered_dict["refresh_token"] = ensure_decrypted(
                    filtered_dict["refresh_token"]
                )

            # Create a new TwitchData object with the filtered data
            self._twitch_data = TwitchData(**filtered_dict)
        else:
            # Create default TwitchData if no data exists
            self._twitch_data = TwitchData()

        # Update the state dictionary to include all fields from the dataclass (excluding object fields)
        self._state["twitch_data"] = {
            field.name: getattr(self._twitch_data, field.name)
            for field in TwitchData.__dataclass_fields__.values()
            if not field.name.startswith("_")
            and field.name not in ["twitch", "authenticator", "user", "eventsub"]
        }

        # Update App settings
        settings_dict = self._state["app_settings"]
        if settings_dict:
            # Filter out any keys not in AppSettings and exclude version/build_date
            # which should always use hardcoded values
            valid_keys = [f.name for f in AppSettings.__dataclass_fields__.values()]
            filtered_dict = {
                k: v
                for k, v in settings_dict.items()
                if k in valid_keys and k not in ["version", "build_number", "build_date"]
            }

            # Create a new AppSettings object with the filtered data
            # version and build_date will use the hardcoded defaults
            self._app_settings = AppSettings(**filtered_dict)
        else:
            # Create default AppSettings if no data exists
            self._app_settings = AppSettings()

        # Update the state dictionary to include all fields from the dataclass
        # but exclude version and build_date from being stored in state for database sync
        self._state["app_settings"] = {
            field.name: getattr(self._app_settings, field.name)
            for field in AppSettings.__dataclass_fields__.values()
            if not field.name.startswith("_")
            and field.name not in ["version", "build_number", "build_date"]
        }

        # Update PSN Settings data
        psn_settings_dict = self._state["psn_settings_data"]
        if psn_settings_dict:
            valid_keys = [f.name for f in PSNSettingsData.__dataclass_fields__.values()]
            filtered_dict = {
                k: v for k, v in psn_settings_dict.items() if k in valid_keys
            }
            self._psn_settings_data = PSNSettingsData(**filtered_dict)
        else:
            self._psn_settings_data = PSNSettingsData()

        # Update the state dictionary to include all fields from the dataclass
        self._state["psn_settings_data"] = {
            field.name: getattr(self._psn_settings_data, field.name)
            for field in PSNSettingsData.__dataclass_fields__.values()
            if not field.name.startswith("_")
        }

        # Update Spotify data
        spotify_dict = self._state["spotify_data"]
        if spotify_dict:
            valid_keys = [f.name for f in SpotifyData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in spotify_dict.items() if k in valid_keys}

            # Decrypt sensitive fields
            if "client_id" in filtered_dict:
                filtered_dict["client_id"] = ensure_decrypted(
                    filtered_dict["client_id"]
                )
            if "client_secret" in filtered_dict:
                filtered_dict["client_secret"] = ensure_decrypted(
                    filtered_dict["client_secret"]
                )
            if "access_token" in filtered_dict:
                filtered_dict["access_token"] = ensure_decrypted(
                    filtered_dict["access_token"]
                )
            if "refresh_token" in filtered_dict:
                filtered_dict["refresh_token"] = ensure_decrypted(
                    filtered_dict["refresh_token"]
                )

            self._spotify_data = SpotifyData(**filtered_dict)
        else:
            self._spotify_data = SpotifyData()

        # Update the state dictionary to include all fields from the dataclass
        self._state["spotify_data"] = {
            field.name: getattr(self._spotify_data, field.name)
            for field in SpotifyData.__dataclass_fields__.values()
            if not field.name.startswith("_")
        }

        # Update YouTube data
        youtube_dict = self._state["youtube_data"]
        if youtube_dict:
            valid_keys = [f.name for f in YouTubeData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in youtube_dict.items() if k in valid_keys}

            # Handle migration from old single-channel format to new multi-channel format
            # Check if we have old channel_url but no channel_urls
            if "channel_url" in youtube_dict and not filtered_dict.get("channel_urls"):
                # Migrate old single channel URL to new format
                filtered_dict["channel_urls"] = youtube_dict["channel_url"]
                logger.info("Migrated single channel URL to multi-channel format")

            # Migrate playlist_filter from old comma-separated string to list
            pf = filtered_dict.get("playlist_filter")
            if isinstance(pf, str):
                filtered_dict["playlist_filter"] = (
                    [item.strip() for item in pf.split(",") if item.strip()]
                    if pf.strip()
                    else []
                )
                logger.info("Migrated playlist_filter from string to list format")

            # Decrypt sensitive fields
            if "api_key" in filtered_dict:
                filtered_dict["api_key"] = ensure_decrypted(filtered_dict["api_key"])
            for _yt_secret in (
                "oauth_client_id",
                "oauth_client_secret",
                "access_token",
                "refresh_token",
            ):
                if _yt_secret in filtered_dict and filtered_dict[_yt_secret]:
                    filtered_dict[_yt_secret] = ensure_decrypted(
                        filtered_dict[_yt_secret]
                    )

            self._youtube_data = YouTubeData(**filtered_dict)

            # Initialize channels dictionary if it's empty but we have channel URLs
            if not self._youtube_data.channels and self._youtube_data.channel_urls:
                self._youtube_data.channels = {}
                logger.debug("Initialized empty channels dictionary")
        else:
            self._youtube_data = YouTubeData()

        # Update the state dictionary to include all fields from the dataclass
        self._state["youtube_data"] = {
            field.name: getattr(self._youtube_data, field.name)
            for field in YouTubeData.__dataclass_fields__.values()
            if not field.name.startswith("_")
        }

        # Update OBS data
        obs_dict = self._state["obs_data"]
        if obs_dict:
            valid_keys = [f.name for f in OBSData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in obs_dict.items() if k in valid_keys}

            if "password" in filtered_dict:
                filtered_dict["password"] = ensure_decrypted(filtered_dict["password"])

            if "port" in filtered_dict and filtered_dict["port"] is not None:
                try:
                    filtered_dict["port"] = int(filtered_dict["port"])
                except (TypeError, ValueError):
                    filtered_dict["port"] = 4455

            self._obs_data = OBSData(**filtered_dict)
        else:
            self._obs_data = OBSData()

        self._state["obs_data"] = {
            field.name: getattr(self._obs_data, field.name)
            for field in OBSData.__dataclass_fields__.values()
        }

        # Update Discord data
        discord_dict = self._state.get("discord_data") or {}
        if discord_dict:
            valid_keys = [f.name for f in DiscordData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in discord_dict.items() if k in valid_keys}

            if "bot_token" in filtered_dict and filtered_dict["bot_token"]:
                filtered_dict["bot_token"] = ensure_decrypted(filtered_dict["bot_token"])

            channels = filtered_dict.get("go_live_channels")
            if channels is None:
                filtered_dict["go_live_channels"] = []
            elif not isinstance(channels, list):
                filtered_dict["go_live_channels"] = []

            self._discord_data = DiscordData(**filtered_dict)
        else:
            self._discord_data = DiscordData()

        self._state["discord_data"] = {
            field.name: getattr(self._discord_data, field.name)
            for field in DiscordData.__dataclass_fields__.values()
        }

        # Update Chatbot data
        chatbot_dict = self._state["chatbot_data"]
        if chatbot_dict:
            # Filter out any keys not in ChatbotData
            valid_keys = [f.name for f in ChatbotData.__dataclass_fields__.values()]
            filtered_dict = {k: v for k, v in chatbot_dict.items() if k in valid_keys}

            # Decrypt sensitive fields
            if "auth_token" in filtered_dict:
                filtered_dict["auth_token"] = ensure_decrypted(
                    filtered_dict["auth_token"]
                )
            if "refresh_token" in filtered_dict:
                filtered_dict["refresh_token"] = ensure_decrypted(
                    filtered_dict["refresh_token"]
                )

            # Create a new ChatbotData object with the filtered data
            self._chatbot_data = ChatbotData(**filtered_dict)
        else:
            # Create default ChatbotData if no data exists
            self._chatbot_data = ChatbotData()

        # Update the state dictionary to include all fields from the dataclass (excluding object fields)
        self._state["chatbot_data"] = {
            field.name: getattr(self._chatbot_data, field.name)
            for field in ChatbotData.__dataclass_fields__.values()
            if field.name != "user"  # Exclude user object from state
        }

        # Update Database settings
        database_settings_dict = self._state["database_settings"]
        if database_settings_dict:
            valid_keys = [
                f.name for f in DatabaseSettings.__dataclass_fields__.values()
            ]
            filtered_dict = {
                k: v for k, v in database_settings_dict.items() if k in valid_keys
            }
            self._database_settings = DatabaseSettings(**filtered_dict)
        else:
            self._database_settings = DatabaseSettings()

        # Update the state dictionary to include all fields from the dataclass
        self._state["database_settings"] = {
            field.name: getattr(self._database_settings, field.name)
            for field in DatabaseSettings.__dataclass_fields__.values()
            if not field.name.startswith("_")
        }

    def get_twitch_data(self) -> TwitchData:
        """Get the current Twitch data

        Returns:
            TwitchData: The current Twitch data
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            return copy.deepcopy(self._twitch_data)

    def get_app_settings(self) -> AppSettings:
        """Get the current application settings

        Returns:
            AppSettings: The current application settings
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            return copy.deepcopy(self._app_settings)

    def get_psn_settings_data(self) -> PSNSettingsData:
        """Get the current PSN settings data

        Returns:
            PSNSettingsData: The current PSN settings data
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._psn_settings_data)

    def get_spotify_data(self) -> SpotifyData:
        """Get the current Spotify data

        Returns:
            SpotifyData: The current Spotify data
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._spotify_data)

    def get_database_settings(self) -> DatabaseSettings:
        """Get the current Database settings

        Returns:
            DatabaseSettings: The current Database settings
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._database_settings)

    def get_live_psn_data(self) -> Optional[PSNData]:
        """Get the current live PSN data (fetched by PSNClient).

        Returns:
            Optional[PSNData]: A copy of the current live PSN data, or None if not set.
        """
        with self._lock:
            # No need to call self.initialize() here as this data is not from Firebase settings
            if self._live_psn_data is None:
                logger.debug("Live PSN data is None.")
                return None
            return copy.deepcopy(self._live_psn_data)

    def get_youtube_data(self) -> YouTubeData:
        """Get the current YouTube data

        Returns:
            YouTubeData: The current YouTube data
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._youtube_data)

    def get_chatbot_data(self) -> ChatbotData:
        """Get the current Chatbot data

        Returns:
            ChatbotData: The current Chatbot data
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._chatbot_data)

    def set_youtube_data(self, youtube_data: dict) -> bool:
        """Set YouTube data and sync with database

        Args:
            youtube_data (dict): The YouTube data to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                secrets = _SECRET_FIELDS["youtube_data"]
                existing_state = dict(self._state.get("youtube_data") or {})
                merged = copy.deepcopy(youtube_data)
                for key in secrets:
                    incoming = merged.get(key)
                    existing = existing_state.get(key)
                    merged[key] = _keep_existing_if_blank_secret(
                        key, incoming, existing, secrets
                    )
                self._state["youtube_data"] = merged

                # Update the local instance
                for key, value in merged.items():
                    if hasattr(self._youtube_data, key):
                        setattr(self._youtube_data, key, value)

                # Mark as changed for sync to database
                self._changed_fields.add("youtube_data")
                self._changes_pending = True

                logger.debug("YouTube data updated in state manager")
                return True
            except Exception as e:
                logger.error(f"Error updating YouTube data: {str(e)}", exc_info=True)
                return False

    def get_obs_data(self) -> OBSData:
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._obs_data)

    def set_obs_data(self, obs_data: dict) -> bool:
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                self._state["obs_data"] = copy.deepcopy(obs_data)

                for key, value in obs_data.items():
                    if hasattr(self._obs_data, key):
                        setattr(self._obs_data, key, value)

                self._changed_fields.add("obs_data")
                self._changes_pending = True

                try:
                    self._obs_data.port = int(getattr(self._obs_data, "port", 4455) or 4455)
                except (TypeError, ValueError):
                    self._obs_data.port = 4455

                self._state["obs_data"] = {
                    f.name: getattr(self._obs_data, f.name)
                    for f in OBSData.__dataclass_fields__.values()
                }

                return True
            except Exception as e:
                logger.error("Error updating OBS data: %s", e, exc_info=True)
                return False

    def get_discord_data(self) -> DiscordData:
        with self._lock:
            if not self._initialized:
                self.initialize()
            return copy.deepcopy(self._discord_data)

    def set_discord_data(self, discord_data: dict) -> bool:
        """Set Discord data and sync with database (immediate write like Spotify)."""
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                incoming = copy.deepcopy(discord_data) if discord_data else {}
                new_token = str(incoming.get("bot_token") or "").strip()
                existing_token = str(
                    getattr(self._discord_data, "bot_token", "") or ""
                ).strip()
                if not new_token and existing_token:
                    incoming["bot_token"] = existing_token

                for key, value in incoming.items():
                    if hasattr(self._discord_data, key):
                        self._state.setdefault("discord_data", {})[key] = value
                        setattr(self._discord_data, key, value)
                        self._changed_fields.add(f"discord_data.{key}")

                if self._discord_data.go_live_channels is None:
                    self._discord_data.go_live_channels = []
                    self._state["discord_data"]["go_live_channels"] = []

                self._state["discord_data"] = {
                    f.name: getattr(self._discord_data, f.name)
                    for f in DiscordData.__dataclass_fields__.values()
                }
                self._changed_fields.add("discord_data")
                self._changes_pending = True

                # Snapshot for immediate write (blank-token preserve in merged writer)
                payload = self._state["discord_data"].copy()
                path = self._paths["discord_data"]
            except Exception as e:
                logger.error("Error updating Discord data: %s", e, exc_info=True)
                return False

        if "discord_data" in self._unreadable_blobs:
            if self._recover_unreadable_blobs({"discord_data"}):
                return False
            # The merged state is written through the normal save path.
            return bool(self.save_changes())

        if not _write_discord_data_merged(payload):
            logger.error("DiscordData immediate write failed")
            return False

        with self._lock:
            self._changed_fields = {
                f for f in self._changed_fields if not f.startswith("discord_data")
            }
            self._changes_pending = len(self._changed_fields) > 0
        return True

    def persist_discord_bot_token(self, token: str) -> bool:
        """Thin wrapper: update bot_token field and flush via save_changes / merge write."""
        token = (token or "").strip()
        if not token:
            logger.warning("persist_discord_bot_token called with empty token")
            return False
        if not self.update_discord_field("bot_token", token):
            return False
        ok = bool(self.save_changes())
        if not ok:
            logger.warning("persist_discord_bot_token: save_changes failed")
        return ok

    def _persist_unlocked(self, path: str, data: dict, changed_prefix: str) -> bool:
        """Write a single payload to the database WITHOUT holding ``_lock``.

        Setters update in-memory state under the lock, then call this to perform
        the (potentially blocking / network) write with the lock released, so a
        slow database write cannot stall state getters running on the web engine
        gevent hub or the Twitch threads. On success the persisted changed-field
        entries are pruned under the lock.
        """
        if changed_prefix in self._unreadable_blobs:
            if self._recover_unreadable_blobs({changed_prefix}):
                return False
            # ``data`` was built before the stored copy was merged in.
            return bool(self.save_changes())

        try:
            from . import database_manager

            database_manager.clear_last_write_problem()
            ok = bool(database_manager.set_data(path, data))
            if not ok:
                # A newer write of the same blob (with these fields) replaced this one.
                problem = database_manager.last_write_problem()
                ok = bool(problem) and problem[0] == "superseded"
            if changed_prefix == "spotify_data":
                self._spotify_write_failed = not ok
        except Exception as e:
            logger.error(
                "Error writing %s to database: %s", changed_prefix, e, exc_info=True
            )
            return False

        if not ok:
            # The database layer reported it; the changes stay pending for the next save.
            with self._lock:
                self._changes_pending = True
            return False

        with self._lock:
            self._changed_fields = {
                f for f in self._changed_fields if not f.startswith(changed_prefix)
            }
        return True

    def set_chatbot_data(self, chatbot_data: dict) -> bool:
        """Set Chatbot data and sync with database

        Args:
            chatbot_data (dict): The Chatbot data to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state
                for key, value in chatbot_data.items():
                    if hasattr(self._chatbot_data, key):
                        existing = self._state["chatbot_data"].get(key)
                        value = _keep_existing_if_blank_secret(
                            key, value, existing, _SECRET_FIELDS["chatbot_data"]
                        )
                        self._state["chatbot_data"][key] = value
                        self._changed_fields.add(f"chatbot_data.{key}")

                # Create a copy of the state data for database storage with encrypted sensitive fields
                encrypted_data = self._state["chatbot_data"].copy()
                if "auth_token" in encrypted_data:
                    encrypted_data["auth_token"] = ensure_encrypted(
                        encrypted_data["auth_token"]
                    )
                if "refresh_token" in encrypted_data:
                    encrypted_data["refresh_token"] = ensure_encrypted(
                        encrypted_data["refresh_token"]
                    )

                # Update the local object
                self._update_local_objects()
                path = self._paths["chatbot_data"]
            except Exception as e:
                logger.error(f"Error setting Chatbot data: {str(e)}", exc_info=True)
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(path, encrypted_data, "chatbot_data"):
            return False
        logger.debug("Updated Chatbot data")
        return True

    def set_twitch_data(self, twitch_data: dict) -> bool:
        """Set Twitch data and sync with database

        Args:
            twitch_data (dict): The Twitch data to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state
                for key, value in twitch_data.items():
                    if hasattr(self._twitch_data, key):
                        existing = self._state["twitch_data"].get(key)
                        value = _keep_existing_if_blank_secret(
                            key, value, existing, _SECRET_FIELDS["twitch_data"]
                        )
                        self._state["twitch_data"][key] = value
                        self._changed_fields.add(f"twitch_data.{key}")

                # Create a copy of the state data for database storage with encrypted sensitive fields
                encrypted_data = self._state["twitch_data"].copy()
                if "client_id" in encrypted_data:
                    encrypted_data["client_id"] = ensure_encrypted(
                        encrypted_data["client_id"]
                    )
                if "client_secret" in encrypted_data:
                    encrypted_data["client_secret"] = ensure_encrypted(
                        encrypted_data["client_secret"]
                    )
                if encrypted_data.get("auth_token"):
                    encrypted_data["auth_token"] = ensure_encrypted(
                        encrypted_data["auth_token"]
                    )
                if encrypted_data.get("refresh_token"):
                    encrypted_data["refresh_token"] = ensure_encrypted(
                        encrypted_data["refresh_token"]
                    )

                # Update the local object
                self._update_local_objects()
                path = self._paths["twitch_data"]
            except Exception as e:
                logger.error(f"Error setting Twitch data: {str(e)}", exc_info=True)
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(path, encrypted_data, "twitch_data"):
            return False
        logger.debug("Updated Twitch data")
        return True

    def set_app_settings(self, app_settings: dict) -> bool:
        """Set application settings and sync with database

        Args:
            app_settings (dict): The application settings to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state, but exclude version and build_date
                for key, value in app_settings.items():
                    if hasattr(self._app_settings, key) and key not in [
                        "version",
                        "build_number",
                        "build_date",
                    ]:
                        self._state["app_settings"][key] = value
                        self._changed_fields.add(f"app_settings.{key}")
                    elif key in ["version", "build_number", "build_date"]:
                        logger.warning(
                            f"Skipping {key} update - this field uses hardcoded values from AppSettings dataclass"
                        )

                # Snapshot payload for the unlocked write
                app_settings_payload = self._state["app_settings"].copy()

                # Update the local object
                self._update_local_objects()
                path = self._paths["app_settings"]
            except Exception as e:
                logger.error(
                    f"Error setting application settings: {str(e)}", exc_info=True
                )
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(path, app_settings_payload, "app_settings"):
            return False
        logger.debug("Updated application settings")
        return True

    def set_psn_settings_data(self, psn_settings_data: dict) -> bool:
        """Set PSN settings data and sync with database

        Args:
            psn_settings_data (dict): The PSN settings data to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state
                for key, value in psn_settings_data.items():
                    if hasattr(self._psn_settings_data, key):
                        self._state["psn_settings_data"][key] = value
                        self._changed_fields.add(f"psn_settings_data.{key}")

                # Snapshot payload for the unlocked write
                psn_settings_payload = self._state["psn_settings_data"].copy()

                # Update the local object
                self._update_local_objects()
                path = self._paths["psn_settings_data"]
            except Exception as e:
                logger.error(
                    f"Error setting PSN settings data: {str(e)}", exc_info=True
                )
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(
            path, psn_settings_payload, "psn_settings_data"
        ):
            return False
        logger.debug("Updated PSN settings data")
        return True

    def set_spotify_data(self, spotify_data: dict) -> bool:
        """Set Spotify data and sync with database

        Args:
            spotify_data (dict): The Spotify data to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state
                for key, value in spotify_data.items():
                    if hasattr(self._spotify_data, key):
                        existing = self._state["spotify_data"].get(key)
                        value = _keep_existing_if_blank_secret(
                            key, value, existing, _SECRET_FIELDS["spotify_data"]
                        )
                        self._state["spotify_data"][key] = value
                        self._changed_fields.add(f"spotify_data.{key}")

                # Create a copy of the state data for database storage with encrypted sensitive fields
                encrypted_data = self._state["spotify_data"].copy()
                if "client_id" in encrypted_data:
                    encrypted_data["client_id"] = ensure_encrypted(
                        encrypted_data["client_id"]
                    )
                if "client_secret" in encrypted_data:
                    encrypted_data["client_secret"] = ensure_encrypted(
                        encrypted_data["client_secret"]
                    )
                if encrypted_data.get("access_token"):
                    encrypted_data["access_token"] = ensure_encrypted(
                        encrypted_data["access_token"]
                    )
                if encrypted_data.get("refresh_token"):
                    encrypted_data["refresh_token"] = ensure_encrypted(
                        encrypted_data["refresh_token"]
                    )

                # Update the local object
                self._update_local_objects()
                path = self._paths["spotify_data"]
            except Exception as e:
                logger.error(f"Error setting Spotify data: {str(e)}", exc_info=True)
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(path, encrypted_data, "spotify_data"):
            return False
        logger.debug("Updated Spotify data")
        return True

    def set_database_settings(self, database_settings: dict) -> bool:
        """Set Database settings and sync with database

        Args:
            database_settings (dict): The Database settings to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                # Update the state
                for key, value in database_settings.items():
                    if hasattr(self._database_settings, key):
                        self._state["database_settings"][key] = value
                        self._changed_fields.add(f"database_settings.{key}")

                # Snapshot payload for the unlocked write
                database_settings_payload = self._state["database_settings"].copy()

                # Update the local object
                self._update_local_objects()
                path = self._paths["database_settings"]
            except Exception as e:
                logger.error(
                    f"Error setting Database settings: {str(e)}", exc_info=True
                )
                return False

        # Perform the (potentially blocking) write without holding the lock.
        if not self._persist_unlocked(
            path, database_settings_payload, "database_settings"
        ):
            return False
        logger.debug("Updated Database settings")
        return True

    def set_live_psn_data(self, data: PSNData) -> bool:
        """Set the live PSN data in memory. This does not save to Firebase.

        Args:
            data (PSNData): The live PSN data object from PSNClient.

        Returns:
            bool: True if successful, False otherwise.
        """
        with self._lock:
            try:
                self._live_psn_data = copy.deepcopy(data)
                logger.debug(
                    f"Live PSN data updated in StateManager. Current game: {data.current_game_name}"
                )
                # Optionally, emit an event or notify listeners here if needed.
                return True
            except Exception as e:
                logger.error(f"Error setting live PSN data: {str(e)}", exc_info=True)
                return False

    def update_twitch_field(self, field: str, value: Any) -> bool:
        """Update a single field in the Twitch data

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._twitch_data, field):
                    logger.error(f"Invalid Twitch data field: {field}")
                    return False

                if field in _SECRET_FIELDS["twitch_data"]:
                    new_tok = str(value or "").strip()
                    old_tok = str(getattr(self._twitch_data, field, "") or "").strip()
                    if not new_tok and old_tok:
                        return True
                    value = new_tok

                # Get current value for correct type conversion
                current_value = getattr(self._twitch_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["twitch_data"][field] = value
                self._changed_fields.add(f"twitch_data.{field}")
                self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating Twitch data field {field}: {str(e)}", exc_info=True
                )
                return False

    def update_app_setting(self, field: str, value: Any) -> bool:
        """Update a single field in the application settings

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._app_settings, field):
                    logger.error(f"Invalid application settings field: {field}")
                    return False

                # Prevent updating version and build_date - these should always use hardcoded values
                if field in ["version", "build_number", "build_date"]:
                    logger.warning(
                        f"Cannot update {field} - this field uses hardcoded values from AppSettings dataclass"
                    )
                    return False

                # Get current value for correct type conversion
                current_value = getattr(self._app_settings, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed (skip comparison)
                self._state["app_settings"][field] = value
                self._changed_fields.add(f"app_settings.{field}")
                self._changes_pending = True

                # Log the update
                # Reduced debug logging to prevent log spam from frequent updates

                # Update the local object
                self._update_local_objects()

                return True
            except Exception as e:
                logger.error(
                    f"Error updating application setting {field}: {str(e)}",
                    exc_info=True,
                )
                return False

    def update_psn_setting(self, field: str, value: Any) -> bool:
        """Update a single field in the PSN settings data

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._psn_settings_data, field):
                    logger.error(f"Invalid PSN settings data field: {field}")
                    return False

                # Get current value for correct type conversion
                current_value = getattr(self._psn_settings_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["psn_settings_data"][field] = value
                self._changed_fields.add(f"psn_settings_data.{field}")
                self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating PSN setting {field}: {str(e)}", exc_info=True
                )
                return False

    def update_spotify_field(self, field: str, value: Any) -> bool:
        """Update a single field in the Spotify data

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._spotify_data, field):
                    logger.error(f"Invalid Spotify data field: {field}")
                    return False

                if field in _SECRET_FIELDS["spotify_data"]:
                    new_tok = str(value or "").strip()
                    old_tok = str(getattr(self._spotify_data, field, "") or "").strip()
                    if not new_tok and old_tok:
                        return True
                    value = new_tok

                # Get current value for correct type conversion
                current_value = getattr(self._spotify_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Mark as changed unless the value is already in state (the
                # playback poll re-sends unchanged fields every few seconds).
                state = self._state["spotify_data"]
                unchanged = (
                    not self._spotify_write_failed
                    and field in state
                    and type(state[field]) is type(value)
                    and state[field] == value
                )
                state[field] = value
                if not unchanged:
                    self._changed_fields.add(f"spotify_data.{field}")
                    self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating Spotify data field {field}: {str(e)}",
                    exc_info=True,
                )
                return False

    def update_youtube_field(self, field: str, value: Any) -> bool:
        """Update a single field in the YouTube data

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._youtube_data, field):
                    logger.error(f"Invalid YouTube data field: {field}")
                    return False

                if field in _SECRET_FIELDS["youtube_data"]:
                    new_tok = str(value or "").strip()
                    old_tok = str(getattr(self._youtube_data, field, "") or "").strip()
                    if not new_tok and old_tok:
                        return True
                    value = new_tok

                # Get current value for correct type conversion
                current_value = getattr(self._youtube_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["youtube_data"][field] = value
                self._changed_fields.add(f"youtube_data.{field}")
                self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating YouTube data field {field}: {str(e)}",
                    exc_info=True,
                )
                return False

    def update_obs_field(self, field: str, value: Any) -> bool:
        """Update one field on OBS integration settings."""

        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._obs_data, field):
                    logger.error("Invalid OBS data field: %s", field)
                    return False

                current_value = getattr(self._obs_data, field)

                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                self._state["obs_data"][field] = value
                self._changed_fields.add(f"obs_data.{field}")
                self._changes_pending = True

                self._update_local_objects()
                return True
            except Exception as e:
                logger.error("Error updating OBS field %s: %s", field, e, exc_info=True)
                return False

    def update_discord_field(self, field: str, value: Any) -> bool:
        """Update one field on Discord integration settings."""

        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._discord_data, field):
                    logger.error("Invalid Discord data field: %s", field)
                    return False

                # Never clear a stored bot token via an empty field update
                if field == "bot_token":
                    new_tok = str(value or "").strip()
                    old_tok = str(
                        getattr(self._discord_data, "bot_token", "") or ""
                    ).strip()
                    if not new_tok and old_tok:
                        return True
                    value = new_tok

                current_value = getattr(self._discord_data, field)

                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                setattr(self._discord_data, field, value)
                self._state["discord_data"][field] = value
                self._changed_fields.add(f"discord_data.{field}")
                self._changes_pending = True

                self._update_local_objects()
                return True
            except Exception as e:
                logger.error(
                    "Error updating Discord field %s: %s", field, e, exc_info=True
                )
                return False

    def update_chatbot_field(self, field: str, value: Any) -> bool:
        """Update a single field in the Chatbot data

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._chatbot_data, field):
                    logger.error(f"Invalid Chatbot data field: {field}")
                    return False

                if field in _SECRET_FIELDS["chatbot_data"]:
                    new_tok = str(value or "").strip()
                    old_tok = str(getattr(self._chatbot_data, field, "") or "").strip()
                    if not new_tok and old_tok:
                        return True
                    value = new_tok

                # Get current value for correct type conversion
                current_value = getattr(self._chatbot_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["chatbot_data"][field] = value
                self._changed_fields.add(f"chatbot_data.{field}")
                self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating Chatbot data field {field}: {str(e)}",
                    exc_info=True,
                )
                return False

    def update_database_setting(self, field: str, value: Any) -> bool:
        """Update a single field in the Database settings

        Args:
            field (str): The field to update
            value (Any): The new value

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                if not hasattr(self._database_settings, field):
                    logger.error(f"Invalid Database settings field: {field}")
                    return False

                # Get current value for correct type conversion
                current_value = getattr(self._database_settings, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["database_settings"][field] = value
                self._changed_fields.add(f"database_settings.{field}")
                self._changes_pending = True

                # Update the local object
                self._update_local_objects()

                # Reduced debug logging to prevent log spam from frequent updates
                return True
            except Exception as e:
                logger.error(
                    f"Error updating Database setting {field}: {str(e)}", exc_info=True
                )
                return False

    def save_changes(self) -> bool:
        """Save all pending changes to the database.

        The encrypted write payloads are assembled while holding ``_lock``, but
        the actual (potentially blocking / network) database writes are performed
        WITHOUT the lock held. This prevents a slow or stalled database write
        from blocking every state getter — those getters run on the web engine's
        gevent Socket.IO hub and on the Twitch threads, so a lock held across a
        slow Firebase write would otherwise freeze OBS sources, Stream Deck, and
        Twitch all at once.

        A False result has already been reported to the user (one deduped
        notification from here or from the encryption layer), and the unsaved
        fields stay pending for the next save, so callers must not add their own
        "couldn't save" notification.

        Returns:
            bool: True if successful, False otherwise
        """
        from . import database_manager

        writes = []  # list of (path, data) to persist outside the lock
        db_config_to_apply = None

        with self._lock:
            if not self._initialized:
                self.initialize()
            unreadable = {
                f.split(".", 1)[0] for f in self._changed_fields
            } & set(self._unreadable_blobs)
        # Blobs whose load failed are re-read first; ones still unreadable are
        # not written (their changes stay pending).
        blocked = self._recover_unreadable_blobs(unreadable) if unreadable else set()

        with self._lock:
            # Snapshot exactly the fields we are about to persist so we can clear
            # only those after a successful write — any changes that arrive while
            # the unlocked write is in flight are preserved for the next save.
            fields_being_saved = {
                f for f in self._changed_fields if f.split(".", 1)[0] not in blocked
            }
            if not fields_being_saved:
                if blocked:
                    self._notify_save_failed({}, blocked)
                    return False
                self._changes_pending = False
                return True

            def _changed(prefix: str) -> bool:
                return any(f.startswith(prefix) for f in fields_being_saved)

            try:
                logger.debug("Preparing changes to save to database")

                # Twitch data with encryption
                if _changed("twitch_data"):
                    encrypted_twitch_data = self._state["twitch_data"].copy()
                    if "client_id" in encrypted_twitch_data:
                        encrypted_twitch_data["client_id"] = ensure_encrypted(
                            encrypted_twitch_data["client_id"]
                        )
                    if "client_secret" in encrypted_twitch_data:
                        encrypted_twitch_data["client_secret"] = ensure_encrypted(
                            encrypted_twitch_data["client_secret"]
                        )
                    if encrypted_twitch_data.get("auth_token"):
                        encrypted_twitch_data["auth_token"] = ensure_encrypted(
                            encrypted_twitch_data["auth_token"]
                        )
                    if encrypted_twitch_data.get("refresh_token"):
                        encrypted_twitch_data["refresh_token"] = ensure_encrypted(
                            encrypted_twitch_data["refresh_token"]
                        )
                    writes.append(
                        (self._paths["twitch_data"], encrypted_twitch_data)
                    )

                # App settings
                if _changed("app_settings"):
                    writes.append(
                        (
                            self._paths["app_settings"],
                            self._state["app_settings"].copy(),
                        )
                    )

                # PSN settings data
                if _changed("psn_settings_data"):
                    writes.append(
                        (
                            self._paths["psn_settings_data"],
                            self._state["psn_settings_data"].copy(),
                        )
                    )

                # Spotify data with encryption
                if _changed("spotify_data"):
                    encrypted_spotify_data = self._state["spotify_data"].copy()
                    if "client_id" in encrypted_spotify_data:
                        encrypted_spotify_data["client_id"] = ensure_encrypted(
                            encrypted_spotify_data["client_id"]
                        )
                    if "client_secret" in encrypted_spotify_data:
                        encrypted_spotify_data["client_secret"] = ensure_encrypted(
                            encrypted_spotify_data["client_secret"]
                        )
                    if encrypted_spotify_data.get("access_token"):
                        encrypted_spotify_data["access_token"] = ensure_encrypted(
                            encrypted_spotify_data["access_token"]
                        )
                    if encrypted_spotify_data.get("refresh_token"):
                        encrypted_spotify_data["refresh_token"] = ensure_encrypted(
                            encrypted_spotify_data["refresh_token"]
                        )
                    writes.append(
                        (self._paths["spotify_data"], encrypted_spotify_data)
                    )

                # YouTube data with encryption
                if _changed("youtube_data"):
                    encrypted_youtube_data = self._state["youtube_data"].copy()
                    if "api_key" in encrypted_youtube_data:
                        encrypted_youtube_data["api_key"] = ensure_encrypted(
                            encrypted_youtube_data["api_key"]
                        )
                    for _yt_secret in (
                        "oauth_client_id",
                        "oauth_client_secret",
                        "access_token",
                        "refresh_token",
                    ):
                        if (
                            _yt_secret in encrypted_youtube_data
                            and encrypted_youtube_data[_yt_secret]
                        ):
                            encrypted_youtube_data[_yt_secret] = ensure_encrypted(
                                encrypted_youtube_data[_yt_secret]
                            )
                    writes.append(
                        (self._paths["youtube_data"], encrypted_youtube_data)
                    )

                # Chatbot data with encryption
                if _changed("chatbot_data"):
                    encrypted_chatbot_data = self._state["chatbot_data"].copy()
                    for _cb_secret in ("auth_token", "refresh_token"):
                        if _cb_secret in encrypted_chatbot_data:
                            encrypted_chatbot_data[_cb_secret] = ensure_encrypted(
                                encrypted_chatbot_data[_cb_secret]
                            )
                    writes.append(
                        (self._paths["chatbot_data"], encrypted_chatbot_data)
                    )

                # OBS WebSocket settings (encrypt password like YouTube API key)
                if _changed("obs_data"):
                    encrypted_obs = self._state["obs_data"].copy()
                    if "password" in encrypted_obs:
                        encrypted_obs["password"] = ensure_encrypted(
                            encrypted_obs["password"]
                        )
                    writes.append((self._paths["obs_data"], encrypted_obs))

                # Discord bot settings (encrypt bot token; never blank stored token)
                if _changed("discord_data"):
                    encrypted_discord = self._state["discord_data"].copy()
                    # Encryption + blank-token preserve happen inside the merged writer
                    writes.append((self._paths["discord_data"], encrypted_discord))

                # Database settings
                if _changed("database_settings"):
                    writes.append(
                        (
                            self._paths["database_settings"],
                            self._state["database_settings"].copy(),
                        )
                    )

                    try:
                        database_settings_data = self._state["database_settings"]
                        temp_db_settings = DatabaseSettings(
                            **{
                                k: v
                                for k, v in database_settings_data.items()
                                if k in DatabaseSettings.__dataclass_fields__
                            }
                        )
                        # Always use mycelian for database consistency
                        db_config_to_apply = database_manager.DatabaseConfig(
                            database_type=temp_db_settings.database_type,
                            sql_database_path=temp_db_settings.sql_database_path,
                            firebase_service_account_path=temp_db_settings.firebase_service_account_path,
                            firebase_database_url=temp_db_settings.firebase_database_url,
                            mongodb_connection_string=temp_db_settings.mongodb_connection_string,
                            mongodb_database_name=temp_db_settings.mongodb_database_name,
                            streamer_name="mycelian",
                            connection_timeout=temp_db_settings.connection_timeout,
                            retry_attempts=temp_db_settings.retry_attempts,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Could not prepare database manager config after save: {e}"
                        )
            except Exception as e:
                logger.error(
                    f"Error preparing changes to save: {str(e)}", exc_info=True
                )
                if not isinstance(e, EncryptionError):  # encryption_utils reported it
                    notify_critical(
                        "Nothing was written, and your changes are kept.",
                        source="app",
                        title="Couldn't prepare your changes for saving",
                        hint="Try again. If it keeps happening, copy the details and report it.",
                        details=f"{type(e).__name__}: {e}",
                        dedupe_key="state:save_failed",
                    )
                return False

        # --- Perform the (potentially blocking) writes WITHOUT holding _lock ---
        # The database layer stays quiet here: one notification below names what
        # wasn't saved, instead of a generic database error plus a tab error.
        path_to_key = {path: key for key, path in self._paths.items()}
        failed: Dict[str, Tuple[str, str]] = {}
        with database_manager.quiet_notifications():
            for path, data in writes:
                state_key = path_to_key.get(path, path)
                database_manager.clear_last_write_problem()
                problem = None
                try:
                    if path == self._paths["discord_data"]:
                        ok = _write_discord_data_merged(data)
                        if not ok and database_manager.last_write_problem() is None:
                            problem = ("failed", "the stored copy couldn't be read first")
                    else:
                        ok = bool(database_manager.set_data(path, data))
                except Exception as e:
                    logger.error(
                        f"Error saving {path} to database: {str(e)}", exc_info=True
                    )
                    ok, problem = False, ("failed", str(e))
                if path == self._paths["spotify_data"]:
                    self._spotify_write_failed = not ok
                if ok:
                    continue
                status, reason = problem or database_manager.last_write_problem() or ("failed", "")
                if status == "superseded":
                    # A newer write of the same blob (with these fields) replaced this one.
                    continue
                failed[state_key] = (status, reason)

        if db_config_to_apply is not None and "database_settings" not in failed:
            try:
                # config.json, not this blob, picks the live backend (as in
                # _apply_database_connection_settings), so only connection fields go.
                config_fields = dict(db_config_to_apply.__dict__)
                config_fields.pop("database_type", None)
                database_manager.database_manager.update_config(**config_fields)
                logger.debug(
                    "Updated database manager configuration after save: %s",
                    db_config_to_apply.database_type,
                )
            except Exception as e:
                logger.warning(
                    f"Could not update database manager config after save: {e}"
                )

        # Clear only the fields we persisted; preserve anything changed mid-write
        # and everything whose write failed (it is retried on the next save).
        with self._lock:
            self._changed_fields -= {
                f for f in fields_being_saved if f.split(".", 1)[0] not in failed
            }
            self._changes_pending = len(self._changed_fields) > 0

        if failed or blocked:
            logger.error("Changes not saved: %s (unreadable: %s)", failed, sorted(blocked))
            self._notify_save_failed(failed, blocked)
            return False

        logger.debug("Changes saved to database")
        return True

    def discard_changes(self):
        """Discard all pending changes and reload from Firebase"""
        with self._lock:
            logger.debug("Discarding all changes")
            self._load_from_firebase()
            self._changed_fields.clear()
            self._changes_pending = False
            logger.debug("Changes discarded")

    def has_changes(self) -> bool:
        """Check if there are pending changes

        Returns:
            bool: True if there are pending changes, False otherwise
        """
        has_pending = self._changes_pending
        has_changed_fields = len(self._changed_fields) > 0

        # These should normally be in sync, but log a warning if not
        if has_pending != has_changed_fields:
            logger.warning(
                f"Inconsistent change tracking state: _changes_pending={has_pending}, _changed_fields={self._changed_fields}"
            )
            # Prioritize the presence of changed fields over the flag
            self._changes_pending = has_changed_fields

        # Add more extensive logging to help diagnose issues
        logger.debug(
            f"has_changes() returning {has_pending}, changed fields: {self._changed_fields}"
        )

        return self._changes_pending

    def get_changed_fields(self) -> set:
        """Get the set of fields that have been changed

        Returns:
            set: The set of changed fields
        """
        return copy.deepcopy(self._changed_fields)

    def field_has_changes(self, field_path: str) -> bool:
        """Check if a specific field has been changed

        Args:
            field_path (str): The field path (e.g., 'twitch_data.client_id')

        Returns:
            bool: True if the field has been changed, False otherwise
        """
        return field_path in self._changed_fields

    def reload_from_firebase(self):
        """Force a reload of all data from Firebase"""
        with self._lock:
            logger.debug("Reloading all data from Firebase")
            self._load_from_firebase()
            self._changed_fields.clear()
            self._changes_pending = False
            logger.debug("Reload completed")


# Global instance of the state manager
state_manager = StateManager()


# Initialize the state manager when this module is imported
def initialize_state():
    """Initialize the state manager"""
    state_manager.initialize()


def initialize_with_data(all_data: Dict[str, Any]):
    """Initialize the state manager with pre-loaded data"""
    state_manager.initialize_with_data(all_data)


async def initialize_state_async():
    """Initialize the state manager asynchronously"""
    await state_manager.initialize_async()

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
from typing import Any, Dict, List, Optional, Union

from nicegui import ui

from .encryption_utils import ensure_decrypted, ensure_encrypted
from .notification_engine import nav_actions_settings, notify_critical
from .psnapi import PSNData

# Import database_manager in functions that need it, not at the module level
# This avoids circular import issues

logger = logging.getLogger(__name__)


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
    version: str = "1.9.1"
    build_date: str = "May 12th 2026"
    alert_volume: float = 0.5
    auto_reconnect: bool = True
    current_theme: str = "dark"
    notifications_enabled: bool = True
    auto_update: bool = True
    activity_feed_limit: int = 20
    activity_feed_max_pages: int = 5
    start_maximized: bool = True

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

    def __post_init__(self):
        """Log when a new YouTubeData instance is created"""
        # Removed debug logging to reduce log spam from frequent data access
        pass


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
        }
        self._paths = {
            "twitch_data": "TwitchData",
            "app_settings": "AppSettings",
            "psn_settings_data": "PSNSettings",  # Firebase path for PSN settings
            "spotify_data": "SpotifyData",  # Firebase path for Spotify data
            "database_settings": "DatabaseSettings",  # Firebase path for DatabaseSettings
            "youtube_data": "YouTubeData",  # Firebase path for YouTube data
            "chatbot_data": "ChatbotData",  # Firebase path for Chatbot data
        }
        self._initialized = False
        self._changes_pending = False
        self._changed_fields = set()

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

                    # Get streamer name from app settings or use default
                    streamer_name = (
                        "mycelian"  # Always use mycelian for database consistency
                    )

                    # Update database manager configuration
                    from . import database_manager

                    config = database_manager.DatabaseConfig(
                        database_type=temp_db_settings.database_type,
                        sql_database_path=temp_db_settings.sql_database_path,
                        firebase_service_account_path=temp_db_settings.firebase_service_account_path,
                        firebase_database_url=temp_db_settings.firebase_database_url,
                        mongodb_connection_string=temp_db_settings.mongodb_connection_string,
                        mongodb_database_name=temp_db_settings.mongodb_database_name,
                        streamer_name=streamer_name,
                        connection_timeout=temp_db_settings.connection_timeout,
                        retry_attempts=temp_db_settings.retry_attempts,
                    )

                    # Update the database manager with the new configuration
                    database_manager.update_config(**config.__dict__)
                    logger.debug(
                        f"Updated database manager configuration: {temp_db_settings.database_type}"
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

            # Update the local objects
            self._update_local_objects()

            self._initialized = True
            logger.debug("State manager initialized with pre-loaded data")

    def _load_from_firebase(self):
        """Load all data from Firebase and update the state"""
        try:
            # Import database_manager here to avoid circular imports
            from . import database_manager

            # First, try to load database settings to configure the database manager
            try:
                database_settings_data = (
                    database_manager.get_data(self._paths["database_settings"]) or {}
                )
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

                    # Get streamer name from app settings or use default
                    streamer_name = (
                        "mycelian"  # Always use mycelian for database consistency
                    )
                    # Removed dynamic streamer_name lookup to prevent database fragmentation

                    # Update database manager configuration
                    config = database_manager.DatabaseConfig(
                        database_type=temp_db_settings.database_type,
                        sql_database_path=temp_db_settings.sql_database_path,
                        firebase_service_account_path=temp_db_settings.firebase_service_account_path,
                        firebase_database_url=temp_db_settings.firebase_database_url,
                        mongodb_connection_string=temp_db_settings.mongodb_connection_string,
                        mongodb_database_name=temp_db_settings.mongodb_database_name,
                        streamer_name=streamer_name,
                        connection_timeout=temp_db_settings.connection_timeout,
                        retry_attempts=temp_db_settings.retry_attempts,
                    )

                    # Update the database manager with the new configuration
                    database_manager.update_config(**config.__dict__)
                    logger.debug(
                        f"Updated database manager configuration: {temp_db_settings.database_type}"
                    )

            except Exception as e:
                logger.debug(f"Could not load database settings, using defaults: {e}")

            # Load Twitch data
            twitch_data = database_manager.get_data(self._paths["twitch_data"]) or {}
            self._state["twitch_data"] = twitch_data

            # Load app settings
            app_settings = database_manager.get_data(self._paths["app_settings"]) or {}
            self._state["app_settings"] = app_settings

            # Load PSN settings data
            psn_settings_data = (
                database_manager.get_data(self._paths["psn_settings_data"]) or {}
            )
            self._state["psn_settings_data"] = psn_settings_data

            # Load Spotify data
            spotify_data = database_manager.get_data(self._paths["spotify_data"]) or {}
            self._state["spotify_data"] = spotify_data

            # Load YouTube data
            youtube_data = database_manager.get_data(self._paths["youtube_data"]) or {}
            self._state["youtube_data"] = youtube_data

            # Load Chatbot data
            chatbot_data = database_manager.get_data(self._paths["chatbot_data"]) or {}
            self._state["chatbot_data"] = chatbot_data

            # Update the local objects
            self._update_local_objects()

            logger.debug("Loaded data from database")
        except Exception as e:
            logger.error(f"Error loading data from database: {str(e)}", exc_info=True)
            notify_critical(
                "Could not load application data from the database.",
                dedupe_key="state:load_from_db",
                actions=nav_actions_settings("Database"),
            )

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

                    # Get streamer name from app settings or use default
                    streamer_name = (
                        "mycelian"  # Always use mycelian for database consistency
                    )
                    # Removed dynamic streamer_name lookup to prevent database fragmentation

                    # Update database manager configuration
                    config = database_manager.DatabaseConfig(
                        database_type=temp_db_settings.database_type,
                        sql_database_path=temp_db_settings.sql_database_path,
                        firebase_service_account_path=temp_db_settings.firebase_service_account_path,
                        firebase_database_url=temp_db_settings.firebase_database_url,
                        mongodb_connection_string=temp_db_settings.mongodb_connection_string,
                        mongodb_database_name=temp_db_settings.mongodb_database_name,
                        streamer_name=streamer_name,
                        connection_timeout=temp_db_settings.connection_timeout,
                        retry_attempts=temp_db_settings.retry_attempts,
                    )

                    # Update the database manager with the new configuration
                    database_manager.update_config(**config.__dict__)
                    logger.debug(
                        f"Updated database manager configuration: {temp_db_settings.database_type}"
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
            notify_critical(
                "Could not load application data from the database.",
                dedupe_key="state:load_from_db_async",
                actions=nav_actions_settings("Database"),
            )

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
                if k in valid_keys and k not in ["version", "build_date"]
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
            and field.name not in ["version", "build_date"]
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
                # Update the state
                self._state["youtube_data"] = copy.deepcopy(youtube_data)

                # Update the local instance
                for key, value in youtube_data.items():
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

                # Sync with database using encrypted data
                from . import database_manager

                database_manager.set_data(self._paths["chatbot_data"], encrypted_data)

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f for f in self._changed_fields if not f.startswith("chatbot_data")
                }
                logger.debug("Updated Chatbot data")
                return True
            except Exception as e:
                logger.error(f"Error setting Chatbot data: {str(e)}", exc_info=True)
                return False

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

                # Sync with database using encrypted data
                from . import database_manager

                database_manager.set_data(self._paths["twitch_data"], encrypted_data)

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f for f in self._changed_fields if not f.startswith("twitch_data")
                }
                logger.debug("Updated Twitch data")
                return True
            except Exception as e:
                logger.error(f"Error setting Twitch data: {str(e)}", exc_info=True)
                return False

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
                        "build_date",
                    ]:
                        self._state["app_settings"][key] = value
                        self._changed_fields.add(f"app_settings.{key}")
                    elif key in ["version", "build_date"]:
                        logger.warning(
                            f"Skipping {key} update - this field uses hardcoded values from AppSettings dataclass"
                        )

                # Sync with database
                from . import database_manager

                database_manager.set_data(
                    self._paths["app_settings"], self._state["app_settings"]
                )

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f for f in self._changed_fields if not f.startswith("app_settings")
                }
                logger.debug("Updated application settings")
                return True
            except Exception as e:
                logger.error(
                    f"Error setting application settings: {str(e)}", exc_info=True
                )
                return False

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

                # Sync with database
                from . import database_manager

                database_manager.set_data(
                    self._paths["psn_settings_data"], self._state["psn_settings_data"]
                )

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f
                    for f in self._changed_fields
                    if not f.startswith("psn_settings_data")
                }
                logger.debug("Updated PSN settings data")
                return True
            except Exception as e:
                logger.error(
                    f"Error setting PSN settings data: {str(e)}", exc_info=True
                )
                return False

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

                # Sync with database using encrypted data
                from . import database_manager

                database_manager.set_data(self._paths["spotify_data"], encrypted_data)

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f for f in self._changed_fields if not f.startswith("spotify_data")
                }
                logger.debug("Updated Spotify data")
                return True
            except Exception as e:
                logger.error(f"Error setting Spotify data: {str(e)}", exc_info=True)
                return False

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

                # Sync with database
                from . import database_manager

                database_manager.set_data(
                    self._paths["database_settings"], self._state["database_settings"]
                )

                # Update the local object
                self._update_local_objects()

                self._changed_fields = {
                    f
                    for f in self._changed_fields
                    if not f.startswith("database_settings")
                }
                logger.debug("Updated Database settings")
                return True
            except Exception as e:
                logger.error(
                    f"Error setting Database settings: {str(e)}", exc_info=True
                )
                return False

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
                if field in ["version", "build_date"]:
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

                # Get current value for correct type conversion
                current_value = getattr(self._spotify_data, field)

                # Ensure type matching
                if isinstance(current_value, bool) and not isinstance(value, bool):
                    value = bool(value)
                elif isinstance(current_value, int) and not isinstance(value, int):
                    value = int(value)
                elif isinstance(current_value, float) and not isinstance(value, float):
                    value = float(value)

                # Always update and mark as changed
                self._state["spotify_data"][field] = value
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
        """Save all pending changes to Firebase

        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            try:
                from . import database_manager

                logger.debug("Saving all changes to database")

                # Save Twitch data with encryption
                if any(f.startswith("twitch_data") for f in self._changed_fields):
                    encrypted_twitch_data = self._state["twitch_data"].copy()
                    if "client_id" in encrypted_twitch_data:
                        encrypted_twitch_data["client_id"] = ensure_encrypted(
                            encrypted_twitch_data["client_id"]
                        )
                    if "client_secret" in encrypted_twitch_data:
                        encrypted_twitch_data["client_secret"] = ensure_encrypted(
                            encrypted_twitch_data["client_secret"]
                        )
                    database_manager.set_data(
                        self._paths["twitch_data"], encrypted_twitch_data
                    )

                # Save app settings
                if any(f.startswith("app_settings") for f in self._changed_fields):
                    database_manager.set_data(
                        self._paths["app_settings"], self._state["app_settings"]
                    )

                # Save PSN settings data
                if any(f.startswith("psn_settings_data") for f in self._changed_fields):
                    database_manager.set_data(
                        self._paths["psn_settings_data"],
                        self._state["psn_settings_data"],
                    )

                # Save Spotify data with encryption
                if any(f.startswith("spotify_data") for f in self._changed_fields):
                    encrypted_spotify_data = self._state["spotify_data"].copy()
                    if "client_id" in encrypted_spotify_data:
                        encrypted_spotify_data["client_id"] = ensure_encrypted(
                            encrypted_spotify_data["client_id"]
                        )
                    if "client_secret" in encrypted_spotify_data:
                        encrypted_spotify_data["client_secret"] = ensure_encrypted(
                            encrypted_spotify_data["client_secret"]
                        )
                    database_manager.set_data(
                        self._paths["spotify_data"], encrypted_spotify_data
                    )

                # Save YouTube data with encryption
                if any(f.startswith("youtube_data") for f in self._changed_fields):
                    encrypted_youtube_data = self._state["youtube_data"].copy()
                    if "api_key" in encrypted_youtube_data:
                        encrypted_youtube_data["api_key"] = ensure_encrypted(
                            encrypted_youtube_data["api_key"]
                        )
                    database_manager.set_data(
                        self._paths["youtube_data"], encrypted_youtube_data
                    )

                # Save Database settings
                if any(f.startswith("database_settings") for f in self._changed_fields):
                    database_manager.set_data(
                        self._paths["database_settings"],
                        self._state["database_settings"],
                    )

                    # If database settings changed, update the database manager configuration
                    try:
                        database_settings_data = self._state["database_settings"]
                        temp_db_settings = DatabaseSettings(
                            **{
                                k: v
                                for k, v in database_settings_data.items()
                                if k in DatabaseSettings.__dataclass_fields__
                            }
                        )

                        # Get streamer name from app settings or use default
                        streamer_name = (
                            "mycelian"  # Always use mycelian for database consistency
                        )
                        # Removed dynamic streamer_name lookup to prevent database fragmentation

                        # Update database manager configuration
                        config = database_manager.DatabaseConfig(
                            database_type=temp_db_settings.database_type,
                            sql_database_path=temp_db_settings.sql_database_path,
                            firebase_service_account_path=temp_db_settings.firebase_service_account_path,
                            firebase_database_url=temp_db_settings.firebase_database_url,
                            mongodb_connection_string=temp_db_settings.mongodb_connection_string,
                            mongodb_database_name=temp_db_settings.mongodb_database_name,
                            streamer_name=streamer_name,
                            connection_timeout=temp_db_settings.connection_timeout,
                            retry_attempts=temp_db_settings.retry_attempts,
                        )

                        # Update the database manager with the new configuration
                        database_manager.update_config(**config.__dict__)
                        logger.debug(
                            f"Updated database manager configuration after save: {temp_db_settings.database_type}"
                        )

                    except Exception as e:
                        logger.warning(
                            f"Could not update database manager config after save: {e}"
                        )

                self._changed_fields.clear()
                self._changes_pending = False
                logger.debug("Changes saved to database")
                return True
            except Exception as e:
                logger.error(
                    f"Error saving changes to database: {str(e)}", exc_info=True
                )
                notify_critical(
                    "Could not save changes to the database. Your edits may be lost.",
                    dedupe_key="state:save_changes",
                    actions=nav_actions_settings("Database"),
                )
                return False

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

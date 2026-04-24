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

import logging
import os
from typing import Optional

from .api_credentials_manager import (
    get_encrypted_spotify_client_id,
    get_encrypted_spotify_client_secret,
    get_encrypted_twitch_client_id,
    get_encrypted_twitch_client_secret,
    initialize_api_credentials,
)
from .database_manager import DatabaseConfig, database_manager
from .dataobjects import DatabaseSettings, state_manager

logger = logging.getLogger(__name__)


def _create_default_data():
    """Create default data for new database installations"""
    try:
        logger.info("Creating default data for new installation...")

        # Only create data if it doesn't already exist

        # Check and create default app settings
        if not database_manager.get_data("AppSettings"):
            default_app_settings = {
                "streamer_name": "",
                "streamer_id": "",
                "version": "1.3.11",
                "build_date": "November 29th 2025",
                "alert_volume": 0.5,
                "auto_reconnect": True,
                "current_theme": "dark",
                "notifications_enabled": True,
                "auto_update": True,
                "activity_feed_limit": 25,
                "activity_feed_max_pages": 10,
            }
            database_manager.set_data("AppSettings", default_app_settings)
            logger.debug("Created default AppSettings")

        # Check and create default Twitch data with encrypted credentials
        existing_twitch_data = database_manager.get_data("TwitchData")
        if not existing_twitch_data:
            default_twitch_data = {
                "client_id": get_encrypted_twitch_client_id(),
                "client_secret": get_encrypted_twitch_client_secret(),
                "auth_token": "",
                "refresh_token": "",
                "user_id": "",
                "token_expiry": "",
                "current_category": "",
                "log_level": "INFO",
            }
            database_manager.set_data("TwitchData", default_twitch_data)
            logger.debug("Created default TwitchData with encrypted credentials")
        else:
            logger.debug("TwitchData already exists, preserving existing data")

        # Check and create default PSN settings data
        if not database_manager.get_data("PSNSettings"):
            default_psn_settings_data = {"npsso_code": "", "psn_username": ""}
            database_manager.set_data("PSNSettings", default_psn_settings_data)
            logger.debug("Created default PSNSettings")

        # Check and create default Spotify data with encrypted credentials
        if not database_manager.get_data("SpotifyData"):
            default_spotify_data = {
                "client_id": get_encrypted_spotify_client_id(),
                "client_secret": get_encrypted_spotify_client_secret(),
                "access_token": "",
                "refresh_token": "",
                "token_expiry": None,
                "connection_status": "Disconnected",
                "market_country": "",
                "track_name": "Nothing playing",
                "artist_name": "Nothing playing",
                "album_name": "",
                "album_image_url": "",
                "current_tracktime": "0:00",
                "track_length": "0:00",
                "current_tracktime_seconds": 0.0,
                "track_length_seconds": 0.0,
                "is_playing": False,
                "progress_percentage": 0.0,
            }
            database_manager.set_data("SpotifyData", default_spotify_data)
            logger.debug("Created default SpotifyData with encrypted credentials")

        # Create empty alert collections only if they don't exist
        alert_paths = [
            "Alerts/AlertQueue",
            "Alerts/AlertStorage",
            "Alerts/BitAlerts",
            "Alerts/BitRangeAlerts",
            "Alerts/SubAlerts",
            "Alerts/ResubAlerts",
            "Alerts/GiftsubAlerts",
            "Alerts/GiftsubRangeAlerts",
            "Alerts/FollowAlerts",
            "Alerts/RaidAlerts",
            "Alerts/RaidRangeAlerts",
            "Alerts/DonationAlerts",
            "Alerts/DonationRangeAlerts",
            "Alerts/PointAlerts",
        ]

        for alert_path in alert_paths:
            if not database_manager.get_data(alert_path):
                database_manager.set_data(alert_path, {})
                logger.debug(f"Created default {alert_path}")

        logger.info("Default data created successfully")

    except Exception as e:
        logger.error(f"Error creating default data: {str(e)}", exc_info=True)


def initialize_database_system() -> bool:
    """
    Initialize the database system using external configuration.

    This function:
    1. Initializes the config manager (reads from config.json)
    2. Gets the database type and configuration from the config file
    3. Initializes the database manager with the configuration
    4. Handles migration from old database-stored settings if needed

    Returns:
        bool: True if initialization was successful, False otherwise
    """
    try:
        logger.info("Initializing database system...")

        # Initialize config and API credentials managers in parallel for faster startup
        import asyncio

        from .api_credentials_manager import initialize_api_credentials
        from .config_manager import config_manager

        async def init_managers():
            # Run config and API credentials initialization concurrently
            config_task = asyncio.get_event_loop().run_in_executor(
                None, config_manager.initialize
            )
            api_task = asyncio.get_event_loop().run_in_executor(
                None, initialize_api_credentials
            )

            config_result, api_result = await asyncio.gather(config_task, api_task)
            return config_result, api_result

        # Use asyncio to parallelize initialization if event loop is available
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            config_success, api_success = loop.run_until_complete(init_managers())
            loop.close()
        except RuntimeError:
            # Fall back to sequential initialization if event loop is already running
            config_success = config_manager.initialize()
            api_success = initialize_api_credentials()

        if not config_success:
            logger.error("Failed to initialize configuration manager")
            return False

        if not api_success:
            logger.error("Failed to initialize API credentials manager")
            return False

        # Get database configuration from external config file
        config_data = config_manager.get_database_config()
        logger.info(
            f"Loaded database configuration - Type: {config_data.get('database_type', 'unknown')}"
        )

        # Create database configuration from external config
        config = DatabaseConfig(
            database_type=config_data.get("database_type", "sql"),
            sql_database_path=config_data.get("sql_database_path", "mycelian.db"),
            firebase_service_account_path=config_data.get(
                "firebase_service_account_path", "ServiceAccountKey.json"
            ),
            firebase_database_url=config_data.get(
                "firebase_database_url",
                "",
            ),
            mongodb_connection_string=config_data.get(
                "mongodb_connection_string", "mongodb://localhost:27017/"
            ),
            mongodb_database_name=config_data.get("mongodb_database_name", "mycelian"),
            streamer_name="mycelian",  # Always use mycelian for consistency
            connection_timeout=config_data.get("connection_timeout", 30),
            retry_attempts=config_data.get("retry_attempts", 3),
        )

        # Initialize the database manager with the external configuration
        if database_manager.initialize(config):
            logger.info(f"Database manager initialized with {config.database_type}")

            # Defer migration to background for faster startup
            import threading

            def migrate_background():
                try:
                    _migrate_old_database_settings_if_needed(config_data)
                except Exception as e:
                    logger.warning(f"Background migration failed: {e}")

            migration_thread = threading.Thread(target=migrate_background, daemon=True)
            migration_thread.start()

            return True
        else:
            logger.error(
                f"Failed to initialize database manager with {config.database_type}"
            )
            # Fall back to SQL if the configured database type fails
            if config.database_type != "sql":
                logger.info("Falling back to SQL database...")
                config.database_type = "sql"
                config_manager.set_database_type("sql")
                return database_manager.initialize(config)
            return False

    except Exception as e:
        logger.error(f"Error initializing database system: {str(e)}", exc_info=True)
        return False


def _migrate_old_database_settings_if_needed(config_data: dict) -> bool:
    """
    Migrate old database settings from the database to the external config file.
    This ensures backward compatibility with existing installations.
    """
    try:
        logger.info("Checking for old database settings to migrate...")

        # Try to load existing database settings from the database
        from .dataobjects import state_manager

        # Initialize state manager to read from the current database
        if not hasattr(state_manager, "_initialized") or not state_manager._initialized:
            state_manager.initialize()

        # Try to get database settings from the database
        try:
            db_settings = state_manager.get_database_settings()

            # Check if we have meaningful database settings in the database
            # that are different from defaults and should be migrated
            should_migrate = False
            changes_to_migrate = {}

            # Compare each field and see if it differs from current config
            if (
                db_settings.firebase_service_account_path
                and db_settings.firebase_service_account_path
                != config_data.get("firebase_service_account_path")
            ):
                changes_to_migrate["firebase_service_account_path"] = (
                    db_settings.firebase_service_account_path
                )
                should_migrate = True

            if (
                db_settings.firebase_database_url
                and db_settings.firebase_database_url
                != config_data.get("firebase_database_url")
            ):
                changes_to_migrate["firebase_database_url"] = (
                    db_settings.firebase_database_url
                )
                should_migrate = True

            if (
                db_settings.mongodb_connection_string
                and db_settings.mongodb_connection_string
                != config_data.get("mongodb_connection_string")
            ):
                changes_to_migrate["mongodb_connection_string"] = (
                    db_settings.mongodb_connection_string
                )
                should_migrate = True

            if (
                db_settings.mongodb_database_name
                and db_settings.mongodb_database_name
                != config_data.get("mongodb_database_name")
            ):
                changes_to_migrate["mongodb_database_name"] = (
                    db_settings.mongodb_database_name
                )
                should_migrate = True

            if (
                db_settings.sql_database_path
                and db_settings.sql_database_path
                != config_data.get("sql_database_path")
            ):
                changes_to_migrate["sql_database_path"] = db_settings.sql_database_path
                should_migrate = True

            if should_migrate:
                logger.info(
                    f"Migrating {len(changes_to_migrate)} database settings to external config"
                )
                from .config_manager import config_manager

                success = config_manager.update_database_config(**changes_to_migrate)
                if success:
                    logger.info(
                        "Successfully migrated old database settings to external config"
                    )
                else:
                    logger.warning("Failed to migrate old database settings")
                return success
            else:
                logger.debug("No database settings migration needed")
                return True

        except Exception as e:
            logger.debug(f"No old database settings found to migrate: {e}")
            return True

    except Exception as e:
        logger.warning(f"Error during database settings migration: {e}")
        return True  # Don't fail initialization due to migration issues


def migrate_from_firebase_only() -> bool:
    """
    Migrate from the old Firebase-only system to the new multi-database system.

    This function checks if there's existing Firebase data and migrates it to
    the new system while preserving all existing data.

    Returns:
        bool: True if migration was successful or not needed, False if failed
    """
    try:
        logger.info("Checking for Firebase-only system migration...")

        # Check if ServiceAccountKey.json exists (indicates old Firebase system)
        if not os.path.exists("ServiceAccountKey.json"):
            logger.debug("No ServiceAccountKey.json found, no migration needed")
            return True

        # Check if we already have database settings (indicates new system)
        try:
            if hasattr(state_manager, "_initialized") and state_manager._initialized:
                db_settings = state_manager.get_database_settings()
                if db_settings and db_settings.database_type:
                    logger.debug("Database settings already exist, no migration needed")
                    return True
        except:
            pass

        logger.info("Migrating from Firebase-only system...")

        # Initialize with Firebase first to access existing data
        firebase_config = DatabaseConfig(
            database_type="firebase",
            firebase_service_account_path="ServiceAccountKey.json",
            firebase_database_url="",
            streamer_name="mycelian",
        )

        if database_manager.initialize(firebase_config):
            logger.info("Successfully connected to existing Firebase data")

            # Create database settings entry in Firebase
            default_settings = DatabaseSettings(
                database_type="firebase",  # Keep using Firebase as default for existing users
                sql_database_path="mycelian.db",
                firebase_service_account_path="ServiceAccountKey.json",
                firebase_database_url="",
                mongodb_connection_string="mongodb://localhost:27017/",
                mongodb_database_name="mycelian",
                connection_timeout=30,
                retry_attempts=3,
            )

            # Save database settings to Firebase
            settings_dict = {
                "database_type": default_settings.database_type,
                "sql_database_path": default_settings.sql_database_path,
                "firebase_service_account_path": default_settings.firebase_service_account_path,
                "firebase_database_url": default_settings.firebase_database_url,
                "mongodb_connection_string": default_settings.mongodb_connection_string,
                "mongodb_database_name": default_settings.mongodb_database_name,
                "connection_timeout": default_settings.connection_timeout,
                "retry_attempts": default_settings.retry_attempts,
            }

            database_manager.set_data("DatabaseSettings", settings_dict)
            logger.info(
                "Migration completed: Added database settings to existing Firebase data"
            )

            return True
        else:
            logger.warning("Could not connect to existing Firebase data for migration")
            return False

    except Exception as e:
        logger.error(f"Error during Firebase migration: {str(e)}", exc_info=True)
        return False


def get_database_status() -> dict:
    """
    Get the current status of the database system.

    Returns:
        dict: Status information including connection status, database type, etc.
    """
    try:
        status = database_manager.get_connection_status()

        # Add additional information
        status["available_databases"] = database_manager.get_available_databases()
        status["firebase_key_exists"] = os.path.exists("ServiceAccountKey.json")

        # Check if database settings exist in config manager
        try:
            from .config_manager import config_manager

            if config_manager._initialized or config_manager.initialize():
                config_data = config_manager.get_database_config()
                status["settings_configured"] = bool(
                    config_data and config_data.get("database_type")
                )
                status["configured_database_type"] = (
                    config_data.get("database_type") if config_data else None
                )
            else:
                status["settings_configured"] = False
                status["configured_database_type"] = None
        except:
            status["settings_configured"] = False
            status["configured_database_type"] = None

        return status

    except Exception as e:
        logger.error(f"Error getting database status: {str(e)}", exc_info=True)
        return {
            "status": f"Error: {str(e)}",
            "is_connected": False,
            "available_databases": ["sql"],  # SQLite is always available
            "firebase_key_exists": os.path.exists("ServiceAccountKey.json"),
            "settings_configured": False,
            "configured_database_type": None,
        }


def ensure_database_initialized() -> bool:
    """
    Ensure the database system is properly initialized.

    This is the main function that should be called during application startup
    to ensure the database system is ready to use.

    Returns:
        bool: True if database system is ready, False otherwise
    """
    try:
        # Check if we need to re-initialize due to database type change
        from .config_manager import config_manager

        if database_manager._initialized:
            # Get current config from external config manager
            if not config_manager._initialized:
                config_manager.initialize()

            current_external_config = config_manager.get_database_config()
            current_db_config = database_manager.get_config()

            # Check if the external config database type differs from current database manager config
            if (
                current_external_config.get("database_type")
                != current_db_config.database_type
            ):
                logger.info(
                    f"Database type changed from {current_db_config.database_type} to {current_external_config.get('database_type')}, re-initializing..."
                )
                database_manager._initialized = False  # Force re-initialization
            else:
                logger.debug("Database system already initialized with correct type")
                return True

        # Initialize using the new config-based system
        logger.info("Initializing database system using external configuration...")
        return initialize_database_system()

    except Exception as e:
        logger.error(f"Error ensuring database initialization: {str(e)}", exc_info=True)
        return False

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

import logging
import os
from typing import Optional

from .api_credentials_manager import (
    initialize_api_credentials,
)
from .database_manager import (
    DatabaseConfig,
    database_label,
    database_manager,
    quiet_notifications,
)
from .notification_engine import nav_actions_settings, notify_critical

logger = logging.getLogger(__name__)

# Why startup failed, in plain language. main.py shows it in a native dialog
# before exiting, because no window exists yet to show a notification in.
last_startup_error: str = ""


def _set_startup_error(text: str) -> None:
    global last_startup_error
    last_startup_error = text

# The settings migration thread starts before the startup preload; it waits this
# long for the preload's DatabaseSettings before reading the path itself.
_MIGRATION_PRELOAD_WAIT_SEC = 120.0


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

        from .startup_profiler import StartupTimer

        # Use asyncio to parallelize initialization if event loop is available
        try:
            with StartupTimer("database_init.config_and_api_credentials"):
                config_success, api_success = asyncio.run(init_managers())
        except RuntimeError:
            # Fall back to sequential initialization if event loop is already running
            with StartupTimer("database_init.config_manager.initialize"):
                config_success = config_manager.initialize()
            with StartupTimer("database_init.initialize_api_credentials"):
                api_success = initialize_api_credentials()

        # These failures end startup (main.py exits), so they are reported through
        # last_startup_error rather than a notification nobody would see.
        if not config_success:
            logger.error("Failed to initialize configuration manager")
            _set_startup_error(
                "Mycelian couldn't read its settings file "
                f"({getattr(config_manager, 'config_path', 'config.json')}). "
                "Fix or delete the file, then start Mycelian again."
            )
            return False

        if not api_success:
            logger.error("Failed to initialize API credentials manager")
            from .api_credentials_manager import api_credentials_manager

            _set_startup_error(
                "Mycelian couldn't open its API credentials file "
                f"({getattr(api_credentials_manager, 'config_path', 'api_credentials.json')}). "
                "Check that the Mycelian data folder is writable."
            )
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

        # Initialize the database manager with the external configuration.
        # Failures are reported below with what Mycelian did about them.
        with StartupTimer("database_init.database_manager.initialize"), quiet_notifications():
            db_init_ok = database_manager.initialize(config)
        if db_init_ok:
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
            reason = database_manager.last_init_error
            # Fall back to SQL if the configured database type fails
            if config.database_type != "sql":
                failed_type = config.database_type
                failed_label = database_label(failed_type)
                logger.info("Falling back to SQL database...")
                config.database_type = "sql"
                with quiet_notifications():
                    fallback_ok = database_manager.initialize(config)
                if not fallback_ok:
                    _set_startup_error(
                        f"Mycelian couldn't connect to your {failed_label} database, and "
                        "its local database couldn't be opened either.\n\n"
                        f"{failed_label}: {reason or 'see the log'}\n"
                        f"Local: {database_manager.last_init_error or 'see the log'}"
                    )
                    return False
                # Saved so the next start doesn't wait on the broken connection again;
                # the notification tells the user how to switch back.
                config_manager.set_database_type("sql")
                notify_critical(
                    f"Mycelian started with the local database instead. Your {failed_label} "
                    "data is untouched; settings changed now are saved locally.",
                    source="database",
                    title=f"Couldn't connect to your {failed_label} database",
                    hint=(
                        "Fix the connection in Settings → Database, press Test, then switch "
                        f"back to {failed_label}."
                    ),
                    details=reason or None,
                    dedupe_key="db:startup_fallback",
                    actions=nav_actions_settings("Database"),
                )
                return True
            _set_startup_error(
                "Mycelian couldn't open its local database "
                f"({config.sql_database_path}).\n\n{reason or 'See the log for details.'}"
            )
            return False

    except Exception as e:
        logger.error(f"Error initializing database system: {str(e)}", exc_info=True)
        _set_startup_error(f"Mycelian's database system failed to start.\n\n{e}")
        return False


def _migrate_old_database_settings_if_needed(config_data: dict) -> bool:
    """
    Migrate old database settings from the database to the external config file.
    This ensures backward compatibility with existing installations.
    """
    try:
        logger.info("Checking for old database settings to migrate...")

        # Read DatabaseSettings with a single path fetch — avoid state_manager.initialize()
        # which downloads 7+ paths on every launch before initialize_with_data runs.
        from .dataobjects import DatabaseSettings
        from . import database_manager

        try:
            # Reuse the startup preload's copy (Phase 1 reads this path anyway);
            # read it here only when the preload is missing, failed, or empty.
            raw = database_manager.wait_for_startup_preload(
                "DatabaseSettings", _MIGRATION_PRELOAD_WAIT_SEC
            )
            if not raw:
                raw = database_manager.get_data("DatabaseSettings") or {}
            if not raw:
                logger.debug("DatabaseSettings empty; skipping old-settings migration")
                return True

            from .config_manager import config_manager

            if config_data.get("database_settings_migrated"):
                logger.debug("Database settings already migrated to config.json")
                return True

            valid_keys = [
                f.name for f in DatabaseSettings.__dataclass_fields__.values()
            ]
            filtered = {k: v for k, v in raw.items() if k in valid_keys}

            _PLACEHOLDER = {
                "mongodb_connection_string": "mongodb://localhost:27017/",
                "mongodb_database_name": "mycelian",
                "sql_database_path": "mycelian.db",
                "firebase_service_account_path": "ServiceAccountKey.json",
                "firebase_database_url": (
                    "https://your-project-default-rtdb.firebaseio.com/"
                ),
            }
            field_names = (
                "firebase_service_account_path",
                "firebase_database_url",
                "mongodb_connection_string",
                "mongodb_database_name",
                "sql_database_path",
            )
            changes_to_migrate = {}
            for field_name in field_names:
                db_val = filtered.get(field_name)
                if not db_val or db_val == _PLACEHOLDER.get(field_name):
                    continue
                cfg_val = config_data.get(field_name)
                if cfg_val and cfg_val != _PLACEHOLDER.get(field_name):
                    continue
                changes_to_migrate[field_name] = db_val

            if changes_to_migrate:
                logger.info(
                    f"Migrating {len(changes_to_migrate)} database settings to external config"
                )
                success = config_manager.update_database_config(**changes_to_migrate)
                if not success:
                    logger.warning("Failed to migrate old database settings")
                    return False

            config_manager.update_database_config(database_settings_migrated=True)
            return True

        except Exception as e:
            logger.debug(f"No old database settings found to migrate: {e}")
            return True

    except Exception as e:
        logger.warning(f"Error during database settings migration: {e}")
        return True  # Don't fail initialization due to migration issues


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
        # Startup exits on False; main.py shows this reason in a native dialog.
        _set_startup_error(f"Mycelian's database system failed to start.\n\n{e}")
        return False

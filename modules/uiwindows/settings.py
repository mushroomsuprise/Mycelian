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
import logging
import time
import webbrowser
from dataclasses import replace
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
from nicegui import ui
from ..notification_engine import notify
from ..ui_buttons import outline_button
from ..ui_timer import layout_schedule

from .. import dataobjects, psn_service, twitch
from ..api_credentials_manager import api_credentials_manager
from ..config_manager import config_manager
from ..database_manager import (
    DatabaseConfig,
    database_manager,
    is_valid_firebase_rtdb_url,
)
from ..dataobjects import YouTubeData, state_manager
from ..build_info import resolve_build_number
from ..log_parser import get_actionable_errors, get_log_dir
from ..path_utils import get_working_directory, reveal_in_file_manager
from ..startup_profiler import StartupTimer, log_startup_summary
from ..ui_settings_layout import settings_header, settings_section, settings_surface
from .service_brand_icons import service_tab_icon
from .tabs import (
    AppSettingsTab,
    DatabaseTab,
    GameHooksTab,
    ObsTab,
    PSNTab,
    SpotifyTab,
    StatisticsTab,
    ThemeTab,
    TwitchTab,
    YouTubeTab,
)

# from ..psnapi import PSNClient # No longer needed here for status display

logger = logging.getLogger(__name__)

# Native-path file browser dialogs (resizable; OS separators on Windows).
# self-start + mx-auto: keeps intrinsic width so horizontal resize works (not only height).
_FILE_BROWSER_CARD_CLASSES = (
    "mx-auto self-start min-w-[480px] min-h-[400px] w-[min(88vw,1100px)] h-[500px] "
    "!max-w-[min(96vw,1920px)] max-h-[90vh] resize overflow-auto p-4 flex flex-col"
)
_FILE_BROWSER_DIALOG_PROPS = "content-class=mycelian-wide-file-dialog"

# CSS for settings-specific styling - shared card/section rules live in ui_styles.py
CSS = """
.header-section {
    padding: 1rem;
    margin-bottom: 1rem;
    border-radius: 8px;
    background: var(--color-primary-light);
    border-left: 4px solid var(--color-primary);
}

.settings-header {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--color-text-primary);
    margin-bottom: 0.5rem;
}

.settings-description {
    font-size: 0.9rem;
    color: var(--color-text-secondary);
    margin-bottom: 1rem;
}

.secondary-text {
    font-size: 0.85rem;
    color: var(--color-text-muted);
}

.settings-card {
    transition: all 0.3s ease;
    background: var(--color-bg-surface);
}

.settings-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px var(--color-bg-overlay);
}

/* Flat cards inside Settings subtabs (no hover lift/glow) */
.mycelian-sub-tab-shell .q-card,
.mycelian-sub-tab-shell .content-section,
.mycelian-sub-tab-shell .settings-card,
.mycelian-sub-tab-shell .statistics-section,
.mycelian-sub-tab-shell .statistics-metric-card {
    box-shadow: none !important;
    filter: none !important;
}

.mycelian-sub-tab-shell .settings-card:hover,
.mycelian-sub-tab-shell .q-card:hover,
.mycelian-sub-tab-shell .content-section:hover,
.mycelian-sub-tab-shell .statistics-section:hover,
.mycelian-sub-tab-shell .statistics-metric-card:hover {
    transform: none !important;
    box-shadow: none !important;
    filter: none !important;
}

.button-row {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
}

.settings-group {
    margin-bottom: 1.5rem;
}

.divider {
    margin: 1.5rem 0;
    border-top: 1px solid var(--color-border-accent);
}

.tab-content {
    padding: 1rem;
    min-height: unset;
    width: 100%;
    max-width: 100%;
    background: var(--color-bg-elevated);
    box-sizing: border-box;
}

.settings-tab-surface {
    flex: 0 0 auto !important;
    width: 100% !important;
    max-width: 100% !important;
    height: auto !important;
    max-height: none !important;
    box-sizing: border-box;
}

.settings-status-band {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.25rem;
    align-items: center;
}

.settings-form-grid {
    width: 100%;
}

.settings-form-grid > * {
    min-width: 0;
}

.settings-section {
    margin-top: 0.25rem;
}

.settings-divider {
    margin: 0.75rem 0;
}

.settings-toolbar {
    gap: 0.5rem;
}
"""


class SettingsUI:
    """Settings UI component for managing application settings and Twitch data."""

    def __init__(self):
        self.ui_elements = {}
        self.last_saved_values = {}
        self.is_initialized = False
        self.twitch_data: Optional[dataobjects.TwitchData] = None
        self.app_settings: Optional[dataobjects.AppSettings] = None
        self.psn_settings_data: Optional[dataobjects.PSNSettingsData] = None
        self.spotify_data: Optional[dataobjects.SpotifyData] = None
        self.database_settings: Optional[dataobjects.DatabaseSettings] = None
        self.api_credentials: Optional[object] = None  # Will hold APICredentials

        # Active timers tracking to prevent duplicates and enable cleanup
        self.active_timers = {}
        self._active_timers = []  # List for storing timer references for cleanup

        self.oauth_in_progress = {
            "spotify": False,
            "twitch": False,
        }

        # Defer PSN initialization until the PSN tab is actually accessed
        self._psn_initialized = False

        # Modular per-tab components
        self._tabs_by_name = {}
        self._active_tab_name = "Twitch"

    def _load_database_settings_from_config(self) -> dataobjects.DatabaseSettings:
        """Load database settings from the external config manager"""
        try:
            # Initialize config manager if needed
            if not config_manager._initialized:
                config_manager.initialize()

            # Get config data
            config_data = config_manager.get_database_config()

            # Create DatabaseSettings object from config data
            db_settings = dataobjects.DatabaseSettings(
                database_type=config_data.get("database_type", "sql"),
                sql_database_path=config_data.get("sql_database_path", "mycelian.db"),
                firebase_service_account_path=config_data.get(
                    "firebase_service_account_path", "ServiceAccountKey.json"
                ),
                firebase_database_url=config_data.get(
                    "firebase_database_url",
                    "https://twitch-api-bot-default-rtdb.firebaseio.com/",
                ),
                mongodb_connection_string=config_data.get(
                    "mongodb_connection_string", "mongodb://localhost:27017/"
                ),
                mongodb_database_name=config_data.get(
                    "mongodb_database_name", "mycelian"
                ),
                connection_timeout=config_data.get("connection_timeout", 30),
                retry_attempts=config_data.get("retry_attempts", 3),
            )

            logger.debug(
                f"Loaded database settings from config: {db_settings.database_type}"
            )
            return db_settings

        except Exception as e:
            logger.error(f"Error loading database settings from config: {e}")
            # Return default settings on error
            return dataobjects.DatabaseSettings()

    # Removed the old initialize method - initialization is now done inline in build_ui()

    def build_ui(self):
        """Build the settings UI and return its main container."""
        logger.info("--- SettingsUI.build_ui() CALLED ---")

        # Initialize only the essential data, defer PSN initialization
        if not self.is_initialized:
            # Only initialize state manager if not already done
            if (
                not hasattr(state_manager, "_initialized")
                or not state_manager._initialized
            ):
                state_manager.initialize()

            # Load data quickly without expensive operations
            try:
                self.twitch_data = state_manager.get_twitch_data()
                self.app_settings = state_manager.get_app_settings()
                self.psn_settings_data = state_manager.get_psn_settings_data()
                self.spotify_data = state_manager.get_spotify_data()
                self.youtube_data = state_manager.get_youtube_data()

                # Load database settings from external config manager instead of database
                self.database_settings = self._load_database_settings_from_config()

                # Load API credentials
                self.api_credentials = api_credentials_manager.get_credentials()

                # Store initial values for comparison
                self.last_saved_values = {
                    "twitch_data": {
                        field: getattr(self.twitch_data, field)
                        for field in self.twitch_data.__dataclass_fields__
                    },
                    "app_settings": {
                        field: getattr(self.app_settings, field)
                        for field in self.app_settings.__dataclass_fields__
                    },
                    "psn_settings_data": {
                        field: getattr(self.psn_settings_data, field)
                        for field in self.psn_settings_data.__dataclass_fields__
                    },
                    "spotify_data": {
                        field: getattr(self.spotify_data, field)
                        for field in self.spotify_data.__dataclass_fields__
                    },
                    "youtube_data": {
                        field.name: getattr(self.youtube_data, field.name)
                        for field in YouTubeData.__dataclass_fields__.values()
                    },
                    # Database settings now handled by config manager
                    "database_settings": {},
                }

                self.is_initialized = True
                logger.debug("Settings UI data loaded successfully")

            except Exception as e:
                logger.error(f"Error loading settings data: {e}")
                # Set defaults if loading fails
                self.is_initialized = True

        # Add the custom CSS
        ui.add_head_html(f"<style>{CSS}</style>")

        # Create a scroll container for the entire content
        scroll_container = ui.scroll_area().classes("w-full h-full")
        with scroll_container:
            with ui.element("div").classes("mycelian-sub-tab-shell w-full"):
                with ui.tabs().classes("w-full settings-tabs mycelian-sub-tabs") as tabs:
                    ui.tab("App Settings", icon="tune")
                    ui.tab("Theme", icon="palette")
                    ui.tab("Twitch", icon="stream")
                    ui.tab("PSN", icon="sports_esports")
                    ui.tab("Spotify", icon="music_note")
                    ui.tab("YouTube", icon="video_library")
                    ui.tab("Database", icon="storage")
                    ui.tab("Statistics", icon="analytics")
                    ui.tab("About", icon="info")

                tabs.on("change", lambda _: self.update_all_fields_styling())

                with ui.tab_panels(tabs, value="App Settings").classes(
                    "w-full flex-1 min-h-0"
                ):
                    # App Settings Tab
                    with ui.tab_panel("App Settings").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("Application Settings").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.column().classes("w-full gap-4 settings-group"):
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Notifications:").classes("w-40")
                                    self.ui_elements[
                                        "app_settings.notifications_enabled"
                                    ] = ui.switch(
                                        value=self.app_settings.notifications_enabled
                                    ).on(
                                        "change",
                                        lambda e: self.on_field_change(
                                            "app_settings", "notifications_enabled", e
                                        ),
                                    )
                                    # Add q-switch class for better styling
                                    self.ui_elements[
                                        "app_settings.notifications_enabled"
                                    ].classes("q-switch")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Auto Update:").classes("w-40")
                                    self.ui_elements["app_settings.auto_update"] = (
                                        ui.switch(
                                            value=self.app_settings.auto_update
                                        ).on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "app_settings", "auto_update", e
                                            ),
                                        )
                                    )
                                    # Add q-switch class for better styling
                                    self.ui_elements[
                                        "app_settings.auto_update"
                                    ].classes("q-switch")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Update Check Interval:").classes("w-40")
                                    # Number input for minutes with min/max bounds
                                    self.ui_elements[
                                        "app_settings.update_check_interval_minutes"
                                    ] = (
                                        ui.number(
                                            value=getattr(
                                                self.app_settings,
                                                "update_check_interval_minutes",
                                                30,
                                            ),
                                            min=5,
                                            max=120,
                                            step=5,
                                        )
                                        .classes("w-28")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "app_settings",
                                                "update_check_interval_minutes",
                                                e,
                                            ),
                                        )
                                    )
                                    ui.label("minutes (5 - 120)").classes(
                                        "ml-2 secondary-text"
                                    )

                                ui.separator().classes("divider")

                                ui.label("Activity Feed Settings").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full items-center"):
                                    ui.label("History Limit:").classes("w-40")
                                    self.ui_elements[
                                        "app_settings.activity_feed_limit"
                                    ] = (
                                        ui.number(
                                            value=self.app_settings.activity_feed_limit,
                                            min=5,
                                            max=100,
                                            step=5,
                                        )
                                        .classes("w-24")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "app_settings", "activity_feed_limit", e
                                            ),
                                        )
                                    )
                                    ui.label("alerts per page").classes(
                                        "ml-2 secondary-text"
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Max Pages:").classes("w-40")
                                    self.ui_elements[
                                        "app_settings.activity_feed_max_pages"
                                    ] = (
                                        ui.number(
                                            value=self.app_settings.activity_feed_max_pages,
                                            min=1,
                                            max=50,
                                            step=1,
                                        )
                                        .classes("w-24")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "app_settings",
                                                "activity_feed_max_pages",
                                                e,
                                            ),
                                        )
                                    )
                                    ui.label("maximum pages to load").classes(
                                        "ml-2 secondary-text"
                                    )

                    # Theme Tab
                    with ui.tab_panel("Theme").classes("tab-content"):
                        from modules.uiwindows.tabs.theme_tab import ThemeTab

                        theme_tab = ThemeTab()
                        theme_tab.build(parent_container=ui.column())

                    # Twitch Tab
                    with ui.tab_panel("Twitch").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("Twitch").classes("text-xl font-bold mb-4")

                            with ui.column().classes("w-full gap-4"):
                                ui.label("Authentication Status").classes(
                                    "text-lg font-semibold"
                                )

                                # Live status display
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Status:").classes("w-40")
                                    self.ui_elements["twitch_status_label"] = ui.label(
                                        "Loading..."
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Connected User:").classes("w-40")
                                    self.ui_elements["twitch_user_label"] = ui.label(
                                        "N/A"
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Last Update:").classes("w-40")
                                    self.ui_elements["twitch_last_update_label"] = (
                                        ui.label("Never").classes("secondary-text")
                                    )

                                ui.separator().classes("divider")

                                # Connection controls
                                ui.label("Connection Controls").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full gap-2"):
                                    self.ui_elements["connect_twitch_button"] = (
                                        ui.button(
                                            "Connect to Twitch",
                                            on_click=self.handle_twitch_oauth_connection,
                                        ).props("icon=login color=primary")
                                    )

                                    self.ui_elements["refresh_status_button"] = (
                                        ui.button(
                                            "Refresh Status",
                                            on_click=self.refresh_twitch_status,
                                        ).props("icon=refresh outline")
                                    )

                                # Initial status update
                                # Defer this to when the tab is actually viewed
                                # self.refresh_twitch_status()

                                # Add a timer to refresh status when tab becomes visible
                                layout_schedule(
                                    0.1, lambda: self.refresh_twitch_status(), once=True
                                )

                                ui.separator().classes("divider")

                                # API Credentials section
                                ui.label("API Credentials").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "Default Twitch API credentials for the application. Only change if you have your own Twitch developer application."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client ID:").classes("w-40")
                                    self.ui_elements["twitch_api_client_id"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_twitch_credentials()[
                                                    "client_id"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            placeholder="Twitch API Client ID",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "twitch", "client_id", e
                                            ),
                                        )
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client Secret:").classes("w-40")
                                    self.ui_elements["twitch_api_client_secret"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_twitch_credentials()[
                                                    "client_secret"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            password=True,
                                            password_toggle_button=True,
                                            placeholder="Twitch API Client Secret",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "twitch", "client_secret", e
                                            ),
                                        )
                                    )

                                ui.separator().classes("divider")

                                # Chatbot API Credentials section
                                ui.label("Chatbot API Credentials").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "Optional Twitch API credentials for chatbot functionality. Use these only if you want the chatbot to operate from a different Twitch account than your main account."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client ID:").classes("w-40")
                                    self.ui_elements["chatbot_api_client_id"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_chatbot_credentials()[
                                                    "client_id"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            placeholder="Chatbot Twitch API Client ID",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "chatbot", "client_id", e
                                            ),
                                        )
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client Secret:").classes("w-40")
                                    self.ui_elements["chatbot_api_client_secret"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_chatbot_credentials()[
                                                    "client_secret"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            password=True,
                                            password_toggle_button=True,
                                            placeholder="Chatbot Twitch API Client Secret",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "chatbot", "client_secret", e
                                            ),
                                        )
                                    )

                    # PSN Tab (New)
                    with ui.tab_panel("PSN").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("PlayStation Network Integration").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.column().classes("w-full gap-4 settings-group"):
                                ui.label(
                                    "Configure your PSN credentials and target username for API access."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("NPSSO Code:").classes("w-40")
                                    self.ui_elements["psn_settings_data.npsso_code"] = (
                                        ui.input(
                                            value=(
                                                self.psn_settings_data.npsso_code
                                                if self.psn_settings_data
                                                else ""
                                            ),
                                            password=True,
                                            password_toggle_button=True,
                                            placeholder="Required for PSN API access",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "psn_settings_data", "npsso_code", e
                                            ),
                                        )
                                    )

                                ui.label(
                                    "Your NPSSO code from the PSN web login. Required for API access."
                                ).classes("secondary-text ml-40")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("PSN Username:").classes("w-40")
                                    self.ui_elements[
                                        "psn_settings_data.psn_username"
                                    ] = (
                                        ui.input(
                                            value=(
                                                self.psn_settings_data.psn_username
                                                if self.psn_settings_data
                                                else ""
                                            ),
                                            placeholder="Optional: Username to track (leave empty for your own)",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "psn_settings_data", "psn_username", e
                                            ),
                                        )
                                    )

                                ui.label(
                                    "Optional: Specify a PSN username to track. Leave empty to track your own account."
                                ).classes("secondary-text ml-40")

                                ui.separator().classes("divider")

                                ui.label("Connection Status").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Status:").classes("w-40")
                                    self.ui_elements["psn_status_label"] = ui.label(
                                        "Not Connected"
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Tracking:").classes("w-40")
                                    self.ui_elements["psn_user_label"] = ui.label(
                                        "N/A"
                                    ).classes("font-semibold")

                        # PSN status update is now completely deferred to avoid blocking UI creation
                        # layout_schedule(0.1, lambda: self.update_psn_status_display(), once=True)

                    # Spotify Tab
                    with ui.tab_panel("Spotify").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("Spotify Integration").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.column().classes("w-full gap-4 settings-group"):
                                ui.label("Connection Status").classes(
                                    "text-lg font-semibold"
                                )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Status:").classes("w-40")
                                    self.ui_elements["spotify_status_label"] = ui.label(
                                        "Loading..."
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Current Track:").classes("w-40")
                                    self.ui_elements["spotify_track_label"] = ui.label(
                                        "N/A"
                                    ).classes("font-semibold")

                                ui.separator().classes("divider")

                                # Connection controls
                                ui.label("Connection Controls").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full gap-2"):
                                    self.ui_elements["connect_spotify_button"] = (
                                        ui.button(
                                            "Connect to Spotify",
                                            on_click=self.handle_spotify_oauth_connection,
                                        ).props("icon=login color=primary")
                                    )

                                    self.ui_elements["test_spotify_button"] = ui.button(
                                        "Test Connection",
                                        on_click=self.test_spotify_connection,
                                    ).props("icon=wifi_tethering outline")

                                    self.ui_elements[
                                        "refresh_spotify_status_button"
                                    ] = ui.button(
                                        "Refresh Status",
                                        on_click=self.refresh_spotify_status,
                                    ).props("icon=refresh outline")

                                # Initial status update
                                # Defer this to when the tab is actually viewed
                                # self.refresh_spotify_status()

                                # Add a timer to refresh status when tab becomes visible - but delay it more to avoid spam
                                layout_schedule(
                                    2.0,
                                    lambda: self.refresh_spotify_status(),
                                    once=True,
                                )

                                ui.separator().classes("divider")

                                # Market/Region Settings
                                ui.label("Market/Region Settings").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "Select your country to ensure proper content availability. This affects which tracks can be played."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Market Country:").classes("w-40")

                                    # Define available markets (common Spotify markets) as a simple dictionary
                                    spotify_markets = {
                                        "": "Auto (from account)",
                                        "US": "United States",
                                        "GB": "United Kingdom",
                                        "CA": "Canada",
                                        "AU": "Australia",
                                        "DE": "Germany",
                                        "FR": "France",
                                        "ES": "Spain",
                                        "IT": "Italy",
                                        "NL": "Netherlands",
                                        "SE": "Sweden",
                                        "NO": "Norway",
                                        "DK": "Denmark",
                                        "FI": "Finland",
                                        "BR": "Brazil",
                                        "MX": "Mexico",
                                        "AR": "Argentina",
                                        "CL": "Chile",
                                        "CO": "Colombia",
                                        "JP": "Japan",
                                        "KR": "South Korea",
                                        "IN": "India",
                                        "PH": "Philippines",
                                        "TH": "Thailand",
                                        "MY": "Malaysia",
                                        "SG": "Singapore",
                                        "ID": "Indonesia",
                                        "VN": "Vietnam",
                                        "ZA": "South Africa",
                                        "EG": "Egypt",
                                        "MA": "Morocco",
                                        "TW": "Taiwan",
                                        "HK": "Hong Kong",
                                        "TR": "Turkey",
                                        "IL": "Israel",
                                        "AE": "United Arab Emirates",
                                        "SA": "Saudi Arabia",
                                        "RU": "Russia",
                                        "PL": "Poland",
                                        "CZ": "Czech Republic",
                                        "HU": "Hungary",
                                        "RO": "Romania",
                                        "GR": "Greece",
                                        "PT": "Portugal",
                                        "AT": "Austria",
                                        "CH": "Switzerland",
                                        "BE": "Belgium",
                                        "IE": "Ireland",
                                        "NZ": "New Zealand",
                                    }

                                    # Get the current market country value, defaulting to empty string
                                    # Handle case where market_country might not exist in older data
                                    current_market = (
                                        getattr(self.spotify_data, "market_country", "")
                                        if self.spotify_data
                                        else ""
                                    )

                                    # Ensure the value exists in the options, default to first option if not
                                    valid_values = list(spotify_markets.keys())
                                    if current_market not in valid_values:
                                        current_market = (
                                            ""  # Default to "Auto (from account)"
                                        )

                                    # Try to create the select without initial value first, then set it
                                    try:
                                        self.ui_elements[
                                            "spotify_data.market_country"
                                        ] = (
                                            ui.select(
                                                options=spotify_markets, with_input=True
                                            )
                                            .classes("flex-1")
                                            .on(
                                                "change",
                                                lambda e: self.on_field_change(
                                                    "spotify_data", "market_country", e
                                                ),
                                            )
                                        )

                                        # Set the value after creation if it's valid
                                        if current_market in valid_values:
                                            self.ui_elements[
                                                "spotify_data.market_country"
                                            ].value = current_market

                                    except Exception as e:
                                        print(
                                            f"Error creating Spotify market select: {e}"
                                        )
                                        # Fallback: create select without any initial value
                                        self.ui_elements[
                                            "spotify_data.market_country"
                                        ] = (
                                            ui.select(options=spotify_markets)
                                            .classes("flex-1")
                                            .on(
                                                "change",
                                                lambda e: self.on_field_change(
                                                    "spotify_data", "market_country", e
                                                ),
                                            )
                                        )

                                ui.separator().classes("divider")

                                # API Credentials section
                                ui.label("API Credentials").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "Default Spotify API credentials for the application. Only change if you have your own Spotify developer application."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client ID:").classes("w-40")
                                    self.ui_elements["spotify_api_client_id"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_spotify_credentials()[
                                                    "client_id"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            placeholder="Spotify API Client ID",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "spotify", "client_id", e
                                            ),
                                        )
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Client Secret:").classes("w-40")
                                    self.ui_elements["spotify_api_client_secret"] = (
                                        ui.input(
                                            value=(
                                                api_credentials_manager.get_spotify_credentials()[
                                                    "client_secret"
                                                ]
                                                if self.api_credentials
                                                else ""
                                            ),
                                            password=True,
                                            password_toggle_button=True,
                                            placeholder="Spotify API Client Secret",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_api_credential_change(
                                                "spotify", "client_secret", e
                                            ),
                                        )
                                    )

                    # YouTube Tab
                    with ui.tab_panel("YouTube").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("YouTube Integration").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.column().classes("w-full gap-4 settings-group"):
                                # Connection Status
                                ui.label("Connection Status").classes(
                                    "text-lg font-semibold"
                                )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Status:").classes("w-40")
                                    self.ui_elements["youtube_status_label"] = ui.label(
                                        "Loading..."
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Channel:").classes("w-40")
                                    self.ui_elements["youtube_channel_label"] = (
                                        ui.label("N/A").classes("font-semibold")
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Latest Video:").classes("w-40")
                                    self.ui_elements["youtube_video_label"] = ui.label(
                                        "N/A"
                                    ).classes("font-semibold")

                                ui.separator().classes("divider")

                                # Connection Controls
                                ui.label("Connection Controls").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full gap-2"):
                                    self.ui_elements["test_youtube_button"] = ui.button(
                                        "Test Connection",
                                        on_click=self.test_youtube_connection,
                                    ).props("icon=wifi_tethering outline")

                                    self.ui_elements[
                                        "refresh_youtube_status_button"
                                    ] = ui.button(
                                        "Refresh Status",
                                        on_click=self.refresh_youtube_status,
                                    ).props("icon=refresh outline")

                                # Initial status update with timer
                                layout_schedule(
                                    2.0,
                                    lambda: self.refresh_youtube_status(),
                                    once=True,
                                )

                                ui.separator().classes("divider")

                                # API Credentials section
                                ui.label("API Credentials").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "YouTube Data API v3 credentials. Get these from the Google Cloud Console."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("API Key:").classes("w-40")
                                    self.ui_elements["youtube_data.api_key"] = (
                                        ui.input(
                                            value=getattr(
                                                self.youtube_data, "api_key", ""
                                            ),
                                            password=True,
                                            password_toggle_button=True,
                                            placeholder="YouTube Data API v3 Key",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "youtube_data", "api_key", e
                                            ),
                                        )
                                    )

                                ui.separator().classes("divider")

                                # Channel Configuration
                                ui.label("Channel Configuration").classes(
                                    "text-lg font-semibold"
                                )
                                ui.label(
                                    "Configure which YouTube channels to monitor. Separate multiple channels with '|' symbol."
                                ).classes("settings-description")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Channel URLs:").classes("w-40")
                                    self.ui_elements["youtube_data.channel_urls"] = (
                                        ui.input(
                                            value=getattr(
                                                self.youtube_data, "channel_urls", ""
                                            ),
                                            placeholder="https://youtube.com/@Channel1|https://youtube.com/@Channel2|https://youtube.com/channel/UC...",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "youtube_data", "channel_urls", e
                                            ),
                                        )
                                    )

                                with ui.column().classes("w-full gap-1"):
                                    ui.label("Playlist Filter (Exclude):").classes(
                                        "w-40"
                                    )
                                    self._yt_playlist_chip_container = ui.row().classes(
                                        "w-full flex-wrap gap-1 items-center min-h-[32px]"
                                    )
                                    self._rebuild_yt_playlist_chips()
                                    self._yt_playlist_input = (
                                        ui.input(
                                            placeholder="Type playlist name, press Enter",
                                        )
                                        .classes("w-96")
                                        .on(
                                            "keydown.enter",
                                            self._on_yt_playlist_input_enter,
                                        )
                                    )

                                ui.label(
                                    "Videos belonging to playlists listed here will be excluded from automated messages. Type a playlist name and press Enter to add it."
                                ).classes("secondary-text mt-2")

                    # Database Tab
                    with ui.tab_panel("Database").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("Database Configuration").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.column().classes("w-full gap-4 settings-group"):
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Database Type:").classes("w-40")
                                    self.ui_elements[
                                        "database_settings.database_type"
                                    ] = (
                                        ui.select(
                                            value=self.database_settings.database_type,
                                            options=["sql", "firebase", "mongodb"],
                                            with_input=False,
                                        )
                                        .classes("w-48")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "database_settings", "database_type", e
                                            ),
                                        )
                                    )

                                logger.info(
                                    "Database type change event bound successfully"
                                )

                                # Set up value monitoring since NiceGUI select events don't fire reliably
                                db_element = self.ui_elements[
                                    "database_settings.database_type"
                                ]

                                def check_value_change():
                                    current_value = getattr(db_element, "value", None)
                                    if not hasattr(check_value_change, "last_value"):
                                        check_value_change.last_value = current_value
                                    elif current_value != check_value_change.last_value:
                                        # Value changed, trigger the handler
                                        fake_event = type(
                                            "Event", (), {"value": current_value}
                                        )()
                                        self._handle_database_type_change(fake_event)
                                        check_value_change.last_value = current_value

                                    # Schedule next check (prevents timer leak)
                                    layout_schedule(0.5, check_value_change, once=True)

                                # Start the initial check
                                check_value_change()

                                ui.separator().classes("divider")

                                # SQL Configuration
                                with ui.column().classes(
                                    "w-full gap-2"
                                ) as self.sql_config:
                                    ui.label("SQLite Configuration").classes(
                                        "text-lg font-semibold"
                                    )
                                    ui.label(
                                        "SQLite is a lightweight, file-based database that requires no setup."
                                    ).classes("settings-description")

                                    with ui.row().classes("w-full items-center"):
                                        ui.label("Database File:").classes("w-40")
                                        with ui.row().classes("flex-1 gap-2"):
                                            self.ui_elements[
                                                "database_settings.sql_database_path"
                                            ] = (
                                                ui.input(
                                                    value=self.database_settings.sql_database_path,
                                                    placeholder="mycelian.db",
                                                )
                                                .classes("flex-1")
                                                .on(
                                                    "change",
                                                    lambda e: self.on_field_change(
                                                        "database_settings",
                                                        "sql_database_path",
                                                        e,
                                                    ),
                                                )
                                            )
                                            ui.button(
                                                "Browse",
                                                on_click=self.browse_sql_database_file,
                                            ).props("icon=folder_open outline")

                                    ui.label(
                                        "Specify the path where the SQLite database file will be stored."
                                    ).classes("secondary-text ml-40")

                                # Firebase Configuration
                                with ui.column().classes(
                                    "w-full gap-2"
                                ) as self.firebase_config:
                                    ui.label("Firebase Configuration").classes(
                                        "text-lg font-semibold"
                                    )
                                    ui.label(
                                        "Firebase Realtime Database provides cloud-based data storage."
                                    ).classes("settings-description")

                                    # Configuration validation status
                                    with ui.row().classes("w-full items-center mb-2"):
                                        ui.label("Configuration Status:").classes(
                                            "w-40"
                                        )
                                        self.ui_elements["firebase_config_status"] = (
                                            ui.label(
                                                "Checking..."
                                            ).classes("font-semibold")
                                        )

                                    with ui.row().classes("w-full items-center"):
                                        ui.label("Service Account Key:").classes("w-40")
                                        with ui.row().classes("flex-1 gap-2"):
                                            self.ui_elements[
                                                "database_settings.firebase_service_account_path"
                                            ] = (
                                                ui.input(
                                                    value=self.database_settings.firebase_service_account_path,
                                                    placeholder="ServiceAccountKey.json",
                                                )
                                                .classes("flex-1")
                                                .on(
                                                    "change",
                                                    lambda e: self.on_firebase_config_change(
                                                        "firebase_service_account_path",
                                                        e,
                                                    ),
                                                )
                                            )
                                            ui.button(
                                                "Browse",
                                                on_click=self.browse_firebase_key_file,
                                            ).props("icon=folder_open outline")

                                    # Key file validation status
                                    with ui.row().classes("w-full items-center ml-40"):
                                        self.ui_elements["firebase_key_status"] = (
                                            ui.label("").classes("text-sm")
                                        )

                                    ui.label(
                                        "Path to your Firebase service account JSON key file."
                                    ).classes("secondary-text ml-40")

                                    with ui.row().classes("w-full items-center"):
                                        ui.label("Database URL:").classes("w-40")
                                        self.ui_elements[
                                            "database_settings.firebase_database_url"
                                        ] = (
                                            ui.input(
                                                value=self.database_settings.firebase_database_url,
                                                placeholder="database path",
                                            )
                                            .classes("flex-1")
                                            .on(
                                                "change",
                                                lambda e: self.on_firebase_config_change(
                                                    "firebase_database_url", e
                                                ),
                                            )
                                        )

                                    # URL validation status
                                    with ui.row().classes("w-full items-center ml-40"):
                                        self.ui_elements["firebase_url_status"] = (
                                            ui.label("").classes("text-sm")
                                        )

                                    ui.label(
                                        "Your Firebase Realtime Database URL from the Firebase console."
                                    ).classes("secondary-text ml-40")

                                    # Firebase requirements info
                                    with ui.card().classes("w-full p-3 hint-info mt-2"):
                                        ui.label("Firebase Requirements:").classes(
                                            "text-sm font-semibold text-blue-300"
                                        )
                                        ui.label(
                                            "• Service account key file (JSON format from Firebase Console)"
                                        ).classes("text-xs text-blue-200")
                                        ui.label(
                                            "• Database URL: https://…firebaseio.com/ or https://…firebasedatabase.app/"
                                        ).classes("text-xs text-blue-200")

                                # MongoDB Configuration
                                with ui.column().classes(
                                    "w-full gap-2"
                                ) as self.mongodb_config:
                                    ui.label("MongoDB Configuration").classes(
                                        "text-lg font-semibold"
                                    )
                                    ui.label(
                                        "MongoDB is a NoSQL database that can run locally or in the cloud."
                                    ).classes("settings-description")

                                    with ui.row().classes("w-full items-center"):
                                        ui.label("Connection String:").classes("w-40")
                                        self.ui_elements[
                                            "database_settings.mongodb_connection_string"
                                        ] = (
                                            ui.input(
                                                value=self.database_settings.mongodb_connection_string,
                                                placeholder="mongodb://localhost:27017/",
                                            )
                                            .classes("flex-1")
                                            .on(
                                                "change",
                                                lambda e: self.on_field_change(
                                                    "database_settings",
                                                    "mongodb_connection_string",
                                                    e,
                                                ),
                                            )
                                        )

                                    ui.label(
                                        "MongoDB connection string (e.g., mongodb://localhost:27017/ or mongodb+srv://...)."
                                    ).classes("secondary-text ml-40")

                                    with ui.row().classes("w-full items-center"):
                                        ui.label("Database Name:").classes("w-40")
                                        self.ui_elements[
                                            "database_settings.mongodb_database_name"
                                        ] = (
                                            ui.input(
                                                value=self.database_settings.mongodb_database_name,
                                                placeholder="mycelian",
                                            )
                                            .classes("w-48")
                                            .on(
                                                "change",
                                                lambda e: self.on_field_change(
                                                    "database_settings",
                                                    "mongodb_database_name",
                                                    e,
                                                ),
                                            )
                                        )

                                    ui.label(
                                        "Name of the MongoDB database to use for storing data."
                                    ).classes("secondary-text ml-40")

                                ui.separator().classes("divider")

                                # Common Configuration
                                ui.label("Common Settings").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Connection Timeout:").classes("w-40")
                                    self.ui_elements[
                                        "database_settings.connection_timeout"
                                    ] = (
                                        ui.number(
                                            value=self.database_settings.connection_timeout,
                                            min=5,
                                            max=300,
                                            step=5,
                                        )
                                        .classes("w-24")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "database_settings",
                                                "connection_timeout",
                                                e,
                                            ),
                                        )
                                    )
                                    ui.label("seconds").classes("ml-2 secondary-text")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Retry Attempts:").classes("w-40")
                                    self.ui_elements[
                                        "database_settings.retry_attempts"
                                    ] = (
                                        ui.number(
                                            value=self.database_settings.retry_attempts,
                                            min=1,
                                            max=10,
                                            step=1,
                                        )
                                        .classes("w-24")
                                        .on(
                                            "change",
                                            lambda e: self.on_field_change(
                                                "database_settings", "retry_attempts", e
                                            ),
                                        )
                                    )
                                    ui.label("attempts").classes("ml-2 secondary-text")

                                ui.separator().classes("divider")

                                # Connection Status
                                ui.label("Connection Status").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full items-center"):
                                    ui.label("Status:").classes("w-40")
                                    self.ui_elements["database_status_label"] = (
                                        ui.label("Loading...").classes("font-semibold")
                                    )

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Database Type:").classes("w-40")
                                    self.ui_elements["database_type_label"] = ui.label(
                                        "N/A"
                                    ).classes("font-semibold")

                                with ui.row().classes("w-full items-center"):
                                    ui.label("Last Check:").classes("w-40")
                                    self.ui_elements["database_last_check_label"] = (
                                        ui.label("Never").classes("secondary-text")
                                    )

                                ui.separator().classes("divider")

                                # Connection Controls
                                ui.label("Connection Controls").classes(
                                    "text-lg font-semibold"
                                )
                                with ui.row().classes("w-full gap-2"):
                                    self.ui_elements["test_database_button"] = (
                                        ui.button(
                                            "Test Connection",
                                            on_click=self.test_database_connection,
                                        ).props("icon=wifi_tethering outline")
                                    )

                                    self.ui_elements["migrate_database_button"] = (
                                        ui.button(
                                            "Migrate Data",
                                            on_click=self.show_migration_dialog,
                                        ).props("icon=sync_alt outline")
                                    )

                                    self.ui_elements[
                                        "refresh_database_status_button"
                                    ] = ui.button(
                                        "Refresh Status",
                                        on_click=self.refresh_database_status,
                                    ).props("icon=refresh outline")

                                # # DEBUG: Manual test button
                                # with ui.row().classes('w-full gap-2'):
                                #     ui.button(
                                #         'Test UI Toggle',
                                #         on_click=self.debug_toggle_visibility
                                #     ).props('icon=visibility color=warning outline')

                                # Update config visibility based on current selection
                                self.update_database_config_visibility()

                                # Initial status update
                                layout_schedule(
                                    0.1,
                                    lambda: self.refresh_database_status(),
                                    once=True,
                                )

                                # Also initialize Firebase configuration status if Firebase is selected
                                if self.database_settings.database_type == "firebase":
                                    logger.info(
                                        "Database type is Firebase, scheduling Firebase config status update"
                                    )
                                    layout_schedule(
                                        0.2,
                                        lambda: self.update_firebase_config_status(),
                                        once=True,
                                    )

                    # Statistics Tab
                    with ui.tab_panel("Statistics").classes("tab-content"):
                        self._build_statistics_dashboard()

                    # About Tab
                    with ui.tab_panel("About").classes("tab-content"):
                        with ui.card().classes("content-section w-full"):
                            ui.label("Application Information").classes(
                                "text-xl font-bold mb-4"
                            )

                            with ui.row().classes("w-full"):
                                ui.label(
                                    f"Version: {self.app_settings.version}"
                                ).classes("secondary-text")

                            with ui.row().classes("w-full"):
                                ui.label(
                                    f"Build Date: {self.app_settings.build_date}"
                                ).classes("secondary-text")

                            ui.separator().classes("divider")

                            # Update controls
                            ui.label("Update Management").classes(
                                "text-lg font-semibold"
                            )
                            with ui.row().classes("w-full gap-2 mt-2"):
                                ui.button(
                                    "Check for Updates",
                                    on_click=self.check_for_updates_manual,
                                ).props("icon=system_update color=primary")

                                outline_button(
                                    "View Changelog",
                                    self.show_changelog_modal,
                                    icon="history",
                                )

                        # Available Source URLs section
                        with ui.card().classes("content-section w-full mt-4"):
                            ui.label("Available Source URLs").classes(
                                "text-xl font-bold mb-4"
                            )
                            ui.label(
                                "Copy these URLs to use as Browser Sources in OBS or other streaming software."
                            ).classes("settings-description mb-4")

                            # Container for the URL list
                            self.ui_elements["source_urls_container"] = (
                                ui.column().classes("w-full gap-2")
                            )

                            # Refresh button
                            with ui.row().classes("w-full justify-end mt-4"):
                                ui.button(
                                    "Refresh URLs", on_click=self.refresh_source_urls
                                ).props("icon=refresh outline")

                            # Load source URLs initially
                            layout_schedule(0.1, lambda: self.refresh_source_urls(), once=True)

                # Button row for save/discard
                with ui.row().classes("button-row w-full mb-4"):
                    ui.button("Discard Changes", on_click=self.discard_changes).props(
                        'outline color="grey"'
                    )
                    ui.button("Save Changes", on_click=self.save_changes).props(
                        'color="primary"'
                    )
        return scroll_container  # RETURN THE BUILT UI CONTAINER

    def on_field_change(self, section: str, field: str, event):
        """Handle changes to field values"""
        # Safely get the value from the event object
        new_value = getattr(event, "value", None)
        if new_value is None and hasattr(event, "args") and event.args is not None:
            args = event.args
            if isinstance(args, (list, tuple)) and len(args) > 0:
                new_value = args[0]
            elif not isinstance(args, (list, tuple)):
                new_value = args
        if new_value is None:
            # If event doesn't have value, try to get it from the UI element directly
            field_key = f"{section}.{field}"
            if field_key in self.ui_elements:
                new_value = getattr(self.ui_elements[field_key], "value", None)

        logger.info(
            f"--- SettingsUI.on_field_change triggered for {section}.{field} with value {new_value} ---"
        )
        try:
            # Get the correct data object and update method from state_manager
            if section == "twitch_data":
                data_obj = self.twitch_data
                update_method = state_manager.update_twitch_field
            elif section == "app_settings":
                data_obj = self.app_settings
                update_method = state_manager.update_app_setting
            elif section == "psn_settings_data":
                data_obj = self.psn_settings_data
                update_method = state_manager.update_psn_setting
            elif section == "spotify_data":
                data_obj = self.spotify_data
                update_method = state_manager.update_spotify_field
            elif section == "youtube_data":
                data_obj = self.youtube_data
                update_method = state_manager.update_youtube_field
            elif section == "database_settings":
                data_obj = self.database_settings
                update_method = (
                    None  # Database settings handled separately via config manager
                )
            else:
                logger.error(f"Unknown section in on_field_change: {section}")
                return

            # Get the current field value for comparison
            old_value = getattr(data_obj, field)
            old_value_type = type(old_value).__name__
            new_value_type = type(new_value).__name__

            logger.debug(
                f"Comparing values for {section}.{field}: old={old_value} ({old_value_type}), new={new_value} ({new_value_type})"
            )

            # Check if type conversion is needed for proper comparison
            # This is especially important for boolean values
            if old_value_type != new_value_type:
                logger.debug(
                    f"Type mismatch for {section}.{field}: converting for comparison"
                )
                if isinstance(old_value, bool):
                    # Convert new_value to boolean if old_value is boolean
                    new_value = bool(new_value)
                elif isinstance(old_value, int):
                    # Convert new_value to int if old_value is int
                    new_value = int(new_value) if new_value is not None else 0
                elif isinstance(old_value, float):
                    # Convert new_value to float if old_value is float
                    new_value = float(new_value) if new_value is not None else 0.0
                elif isinstance(old_value, str):
                    # Convert new_value to string if old_value is string
                    new_value = str(new_value)

                logger.debug(
                    f"After conversion: new={new_value} ({type(new_value).__name__})"
                )

            # Force comparison for boolean values to handle edge cases
            explicit_compare = old_value != new_value
            logger.debug(
                f"Explicit comparison result for {section}.{field}: {explicit_compare}"
            )

            # Only update if the value has actually changed
            if explicit_compare:
                # Update the value in the local dataclass instance
                if hasattr(data_obj, field):
                    setattr(data_obj, field, new_value)
                    logger.debug(
                        f"Updated local UI model {section}.{field} to {new_value}"
                    )

                    # IMPORTANT: Update the StateManager to track the change
                    if update_method:
                        if update_method(field, new_value):
                            logger.debug(
                                f"Successfully updated StateManager {section}.{field} to {new_value}"
                            )
                        else:
                            logger.error(
                                f"Failed to update StateManager {section}.{field}"
                            )

                        # Log change status to help diagnose issues
                        field_path = f"{section}.{field}"
                        has_changes = state_manager.has_changes()
                        field_changed = state_manager.field_has_changes(field_path)
                        logger.info(
                            f"After update: StateManager.has_changes()={has_changes}, field_has_changes({field_path})={field_changed}"
                        )
                    elif section == "database_settings":
                        # Database settings are handled by config manager, not state manager
                        logger.debug(
                            f"Database setting {field} updated locally, will be saved via config manager"
                        )
                    else:
                        logger.error(
                            f"No update_method found for section {section} in StateManager."
                        )
                else:
                    logger.error(f"Field {field} not found in {section} data object.")
            else:
                logger.info(
                    f"No actual change detected for {section}.{field} (old={old_value}, new={new_value})"
                )
                # Show a small tooltip notification to inform the user that no actual change was made
                notify(
                    f"Value for {field.replace('_', ' ')} unchanged",
                    type="info",
                    position="bottom-right",
                    timeout=1500,
                )

            # Reschedule periodic update timer when relevant settings change
            if section == "app_settings" and field in [
                "auto_update",
                "update_check_interval_minutes",
            ]:
                try:
                    from .. import mainuiwindow

                    # Reschedule using current settings
                    mainuiwindow.reschedule_periodic_update_timer()
                    logger.info(
                        "Updater: Periodic update timer rescheduled due to settings change"
                    )
                except Exception as reschedule_error:
                    logger.error(
                        f"Error rescheduling periodic update timer: {reschedule_error}"
                    )

            # Special handling for database_type to update visibility
            if section == "database_settings" and field == "database_type":
                logger.info(f"Database type field changed to: {new_value}")
                # Note: UI visibility update is now handled in on_database_type_change_immediate
                # Don't update database manager config here - wait for save
                # notify(f"Database type changed to {new_value}. Save changes to apply.", type="info", timeout=3000)

            # Update field styling (this relies on state_manager.field_has_changes)
            field_key = f"{section}.{field}"
            logger.debug(f"Field changed: {field_key}, triggering style update.")
            self.update_field_styling(field_key)

        except Exception as e:
            logger.error(f"Error in on_field_change: {str(e)}", exc_info=True)

    def on_api_credential_change(self, service: str, field: str, event):
        """Handle API credential changes"""
        try:
            # Get the new value
            new_value = event.value if hasattr(event, "value") else event

            # Update the credentials via the API credentials manager
            if service == "twitch":
                if field == "client_id":
                    api_credentials_manager.update_twitch_credentials(
                        client_id=new_value
                    )
                elif field == "client_secret":
                    api_credentials_manager.update_twitch_credentials(
                        client_secret=new_value
                    )
            elif service == "spotify":
                if field == "client_id":
                    api_credentials_manager.update_spotify_credentials(
                        client_id=new_value
                    )
                elif field == "client_secret":
                    api_credentials_manager.update_spotify_credentials(
                        client_secret=new_value
                    )
            elif service == "chatbot":
                if field == "client_id":
                    api_credentials_manager.update_chatbot_credentials(
                        client_id=new_value
                    )
                elif field == "client_secret":
                    api_credentials_manager.update_chatbot_credentials(
                        client_secret=new_value
                    )

            # Track the change for UI styling
            field_key = f"{service}_api_{field}"
            # Create a simple element styling to indicate the change
            element = self.ui_elements.get(field_key)
            if element:
                element.style(
                    "border-left: 3px solid var(--color-primary); background: var(--color-primary-light); transition: all 0.2s ease"
                )

            logger.debug(f"API credential changed: {service}.{field} updated")
            notify(
                f"{service.title()} {field.replace('_', ' ').title()} updated",
                type="positive",
            )

        except Exception as e:
            logger.error(f"Error handling API credential change: {e}", exc_info=True)
            notify(f"Error updating {service} credentials", type="negative")

    def update_field_styling(self, field_key: str):
        """Update the styling of a field based on changes"""
        try:
            # Get the element from our dictionary
            element = self.ui_elements.get(field_key)
            if not element:
                return

            # Check if the field has changes
            if state_manager.field_has_changes(field_key):
                logger.debug(
                    f"Field {field_key} has changes - applying 'changed' styling"
                )
                # Apply styling for changed fields - adjust based on element type
                element_type = element.__class__.__name__.lower()

                if "switch" in element_type:
                    # For switches, add a more noticeable effect
                    element.style(
                        "box-shadow: 0 0 8px var(--color-primary); transform: scale(1.05); transition: all 0.2s ease"
                    )
                elif "input" in element_type:
                    # For input fields, add a more noticeable border and background
                    element.style(
                        "border-left: 3px solid var(--color-primary); background: var(--color-primary-light); transition: all 0.2s ease"
                    )
                else:
                    # Default styling for other element types
                    element.style(
                        "border-left: 3px solid var(--color-primary); transform: translateX(3px); transition: all 0.2s ease"
                    )

                # Add a dynamic notification when a field is first changed
                section, field = field_key.split(".")
                notify(
                    f"Changed: {field.replace('_', ' ')}",
                    type="positive",
                    position="bottom-right",
                    timeout=1000,
                    icon="edit",
                )
            else:
                logger.debug(
                    f"Field {field_key} has no changes - removing 'changed' styling"
                )
                # Remove styling for unchanged fields
                element.style(
                    "border-left: none; box-shadow: none; background: none; transform: none; transition: all 0.2s ease"
                )

        except Exception as e:
            logger.error(f"Error in update_field_styling: {str(e)}", exc_info=True)

    def update_all_fields_styling(self):
        """Update styling for all tracked fields"""
        for field_key in self.ui_elements:
            self.update_field_styling(field_key)

    def save_changes(self):
        """Save current settings by forcing a save of all values."""
        try:
            # Get all UI elements and force update their values in the state manager
            updated_fields = []

            # Update Twitch data fields
            if self.twitch_data:
                for field in self.twitch_data.__dataclass_fields__:
                    field_key = f"twitch_data.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        state_manager.update_twitch_field(field, value)
                        updated_fields.append(field_key)

            # Update App settings fields
            if self.app_settings:
                for field in self.app_settings.__dataclass_fields__:
                    field_key = f"app_settings.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        state_manager.update_app_setting(field, value)
                        updated_fields.append(field_key)

            # Update PSN settings fields
            if hasattr(self, "psn_settings_data") and self.psn_settings_data:
                for field in self.psn_settings_data.__dataclass_fields__:
                    field_key = f"psn_settings_data.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        state_manager.update_psn_setting(field, value)
                        updated_fields.append(field_key)

            # Update Spotify data fields
            if hasattr(self, "spotify_data") and self.spotify_data:
                for field in self.spotify_data.__dataclass_fields__:
                    field_key = f"spotify_data.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        state_manager.update_spotify_field(field, value)
                        updated_fields.append(field_key)

            # Update Database settings fields (save to config manager instead of state manager)
            if hasattr(self, "database_settings") and self.database_settings:
                database_type_changed = False
                old_database_type = self.database_settings.database_type
                config_updates = {}

                # Load original values from config manager for comparison
                original_config = config_manager.get_database_config()

                for field in self.database_settings.__dataclass_fields__:
                    field_key = f"database_settings.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        # Compare against original config values, not local object
                        old_value = original_config.get(
                            field, getattr(self.database_settings, field)
                        )

                        if old_value != value:
                            logger.info(
                                f"Updating database setting {field}: {old_value} -> {value}"
                            )
                            if field == "database_type":
                                database_type_changed = True

                            # Update local object
                            setattr(self.database_settings, field, value)
                            # Prepare for config manager update
                            config_updates[field] = value
                            updated_fields.append(field_key)

                # Save database settings to config manager
                if config_updates:
                    logger.info(
                        f"Saving {len(config_updates)} database settings to config manager"
                    )
                    config_success = config_manager.update_database_config(
                        **config_updates
                    )

                    if not config_success:
                        logger.error(
                            "Failed to save database settings to config manager"
                        )
                        notify("Failed to save database settings", type="negative")
                        return

                # If database type changed, log it specifically
                if database_type_changed:
                    new_database_type = self.ui_elements[
                        "database_settings.database_type"
                    ].value
                    logger.info(
                        f"Database type changed from {old_database_type} to {new_database_type}"
                    )
                    notify(
                        f"Database type changed to {new_database_type}",
                        type="info",
                        timeout=3000,
                    )

            # Force save of all values
            if state_manager.save_changes():
                # Update local last_saved_values for future reference
                if self.twitch_data:
                    self.last_saved_values["twitch_data"] = {
                        field: getattr(self.twitch_data, field)
                        for field in self.twitch_data.__dataclass_fields__
                    }
                if self.app_settings:
                    self.last_saved_values["app_settings"] = {
                        field: getattr(self.app_settings, field)
                        for field in self.app_settings.__dataclass_fields__
                    }
                if hasattr(self, "psn_settings_data") and self.psn_settings_data:
                    self.last_saved_values["psn_settings_data"] = {
                        field: getattr(self.psn_settings_data, field)
                        for field in self.psn_settings_data.__dataclass_fields__
                    }
                if hasattr(self, "spotify_data") and self.spotify_data:
                    self.last_saved_values["spotify_data"] = {
                        field: getattr(self.spotify_data, field)
                        for field in self.spotify_data.__dataclass_fields__
                    }
                if hasattr(self, "database_settings") and self.database_settings:
                    # Database settings now handled by config manager, no need to track in last_saved_values
                    pass

                # Update UI styling after save
                self.update_all_fields_styling()
                self.update_psn_status_display()  # Ensure PSN status is current
                psn_service.handle_psn_settings_change()  # Notify PSN service if settings changed

                # Update database manager configuration and UI visibility
                self.update_database_manager_config()
                self.update_database_config_visibility()

                # Refresh database status after potential type change
                layout_schedule(0.2, lambda: self.refresh_database_status(), once=True)

                notify("Settings saved successfully", type="positive", timeout=2000)
                logger.info(
                    f"Settings saved successfully. Updated fields: {updated_fields}"
                )
            else:
                notify("Error saving settings", type="negative")
                logger.error("StateManager.save_changes() returned False.")
        except Exception as e:
            notify(f"Error saving settings: {str(e)}", type="negative")
            logger.error(f"Error in SettingsUI.save_changes: {str(e)}", exc_info=True)

    def _save_twitch_settings_only(self):
        """Save only Twitch settings without triggering other notifications"""
        try:
            updated_fields = []

            # Update only Twitch data fields
            if hasattr(self, "twitch_data") and self.twitch_data:
                for field in self.twitch_data.__dataclass_fields__:
                    field_key = f"twitch_data.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        old_value = getattr(self.twitch_data, field)
                        if old_value != value:
                            state_manager.update_twitch_field(field, value)
                            updated_fields.append(field_key)

            # Save only if there are Twitch changes
            if updated_fields:
                if state_manager.save_changes():
                    logger.info(
                        f"Twitch settings saved successfully. Updated fields: {updated_fields}"
                    )
                else:
                    logger.error("Failed to save Twitch settings")
            else:
                logger.debug("No Twitch settings changes to save")

        except Exception as e:
            logger.error(f"Error saving Twitch settings: {str(e)}", exc_info=True)

    def discard_changes(self):
        """Discard all changes and reload from Firebase"""
        if state_manager.has_changes():
            state_manager.discard_changes()

            # Reload data
            self.twitch_data = state_manager.get_twitch_data()
            self.app_settings = state_manager.get_app_settings()
            if hasattr(self, "psn_settings_data"):
                self.psn_settings_data = state_manager.get_psn_settings_data()
            if hasattr(self, "spotify_data"):
                self.spotify_data = state_manager.get_spotify_data()
            if hasattr(self, "database_settings"):
                self.database_settings = self._load_database_settings_from_config()

            # Update UI elements with the reloaded values
            if self.twitch_data:
                for field_name in self.twitch_data.__dataclass_fields__:
                    field_key = f"twitch_data.{field_name}"
                    if field_key in self.ui_elements:
                        value = getattr(self.twitch_data, field_name)
                        self.ui_elements[field_key].value = value

            if self.app_settings:
                for field_name in self.app_settings.__dataclass_fields__:
                    field_key = f"app_settings.{field_name}"
                    if field_key in self.ui_elements:
                        value = getattr(self.app_settings, field_name)
                        self.ui_elements[field_key].value = value

            if hasattr(self, "psn_settings_data") and self.psn_settings_data:
                for field_name in self.psn_settings_data.__dataclass_fields__:
                    field_key = f"psn_settings_data.{field_name}"
                    if field_key in self.ui_elements:
                        value = getattr(self.psn_settings_data, field_name)
                        if (
                            "label"
                            in self.ui_elements[field_key].__class__.__name__.lower()
                        ):
                            self.ui_elements[field_key].set_text(value)
                        else:
                            self.ui_elements[field_key].value = value

            if hasattr(self, "spotify_data") and self.spotify_data:
                for field_name in self.spotify_data.__dataclass_fields__:
                    field_key = f"spotify_data.{field_name}"
                    if field_key in self.ui_elements:
                        value = getattr(self.spotify_data, field_name)
                        if (
                            "label"
                            in self.ui_elements[field_key].__class__.__name__.lower()
                        ):
                            self.ui_elements[field_key].set_text(value)
                        else:
                            self.ui_elements[field_key].value = value

            if hasattr(self, "database_settings") and self.database_settings:
                for field_name in self.database_settings.__dataclass_fields__:
                    field_key = f"database_settings.{field_name}"
                    if field_key in self.ui_elements:
                        value = getattr(self.database_settings, field_name)
                        if (
                            "label"
                            in self.ui_elements[field_key].__class__.__name__.lower()
                        ):
                            self.ui_elements[field_key].set_text(str(value))
                        else:
                            self.ui_elements[field_key].value = value

                logger.info(
                    f"Reset database UI elements. Database type: {self.database_settings.database_type}"
                )

            # Re-apply theme based on discarded (reloaded) settings
            from ..mainuiwindow import apply_theme

            theme_name = (
                self.app_settings.current_theme if self.app_settings else "dark"
            )
            apply_theme(theme_name)

            # Update field styling after discard
            self.update_all_fields_styling()
            self.update_psn_status_display()

            # Update database config visibility and manager
            if hasattr(self, "database_settings"):
                self.update_database_config_visibility()
                self.update_database_manager_config()

            notify("Changes discarded", type="info")
        else:
            notify("No changes to discard", type="info")

    def refresh_ui(self):
        """Refresh all UI elements with the latest data"""
        # Reload data from state manager
        if not hasattr(state_manager, "_initialized") or not state_manager._initialized:
            state_manager.initialize()

        self.twitch_data = state_manager.get_twitch_data()
        self.app_settings = state_manager.get_app_settings()
        self.psn_settings_data = state_manager.get_psn_settings_data()
        self.spotify_data = state_manager.get_spotify_data()
        self.database_settings = self._load_database_settings_from_config()

        # Update UI elements with the latest values
        if self.twitch_data:
            for field in self.twitch_data.__dataclass_fields__:
                field_key = f"twitch_data.{field}"
                if field_key in self.ui_elements:
                    value = getattr(self.twitch_data, field)
                    self.ui_elements[field_key].value = value

        if self.app_settings:
            for field in self.app_settings.__dataclass_fields__:
                field_key = f"app_settings.{field}"
                if field_key in self.ui_elements:
                    value = getattr(self.app_settings, field)
                    self.ui_elements[field_key].value = value

        if hasattr(self, "psn_settings_data") and self.psn_settings_data:
            for field_name in self.psn_settings_data.__dataclass_fields__:
                field_key = f"psn_settings_data.{field_name}"
                if field_key in self.ui_elements:
                    value = getattr(self.psn_settings_data, field_name)
                    if (
                        "label"
                        in self.ui_elements[field_key].__class__.__name__.lower()
                    ):
                        self.ui_elements[field_key].set_text(value)
                    else:
                        self.ui_elements[field_key].value = value

        if hasattr(self, "spotify_data") and self.spotify_data:
            for field_name in self.spotify_data.__dataclass_fields__:
                field_key = f"spotify_data.{field_name}"
                if field_key in self.ui_elements:
                    value = getattr(self.spotify_data, field_name)
                    if (
                        "label"
                        in self.ui_elements[field_key].__class__.__name__.lower()
                    ):
                        self.ui_elements[field_key].set_text(value)
                    else:
                        self.ui_elements[field_key].value = value

        if hasattr(self, "database_settings") and self.database_settings:
            for field_name in self.database_settings.__dataclass_fields__:
                field_key = f"database_settings.{field_name}"
                if field_key in self.ui_elements:
                    value = getattr(self.database_settings, field_name)
                    if (
                        "label"
                        in self.ui_elements[field_key].__class__.__name__.lower()
                    ):
                        self.ui_elements[field_key].set_text(value)
                    else:
                        self.ui_elements[field_key].value = value

            # Update database config visibility and manager
            self.update_database_config_visibility()
            self.update_database_manager_config()

        # Update field styling
        self.update_all_fields_styling()

        # Refresh all status displays
        self.refresh_twitch_status()
        if hasattr(self, "psn_settings_data") and self.psn_settings_data:
            self.update_psn_status_display()
        if hasattr(self, "spotify_data") and self.spotify_data:
            self.refresh_spotify_status()
        if hasattr(self, "database_settings") and self.database_settings:
            self.refresh_database_status()

    def handle_twitch_oauth_connection(self):
        """Handle the Connect to Twitch button click to trigger OAuth reconnection"""
        try:
            logger.info("User clicked Connect to Twitch button")

            # Check if client ID and secret are provided
            if not self.twitch_data.client_id or not self.twitch_data.client_secret:
                notify(
                    "Please enter your Twitch Client ID and Client Secret first!",
                    type="warning",
                )
                return

            # Update button state to show it's working
            if "connect_twitch_button" in self.ui_elements:
                self.ui_elements["connect_twitch_button"].set_text("Connecting...")
                self.ui_elements["connect_twitch_button"].disable()

            # Show a notification that the process is starting
            notify("Starting Twitch OAuth connection...", type="info")

            # Save only Twitch settings to avoid triggering database and other notifications
            self._save_twitch_settings_only()

            # Start the OAuth reconnection in a separate thread
            import threading

            # Create shared result holder for thread communication
            oauth_result = {"status": "connecting", "success": None, "error": None}

            def oauth_thread():
                try:
                    success = twitch.trigger_oauth_reconnection()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success

                except Exception as e:
                    logger.error(f"Error in OAuth thread: {str(e)}", exc_info=True)
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            # Start the thread
            oauth_worker = threading.Thread(target=oauth_thread)
            oauth_worker.daemon = True
            oauth_worker.start()

            # Create a timer to check the result from main thread
            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif oauth_result["status"] == "complete":
                    try:
                        if oauth_result["success"]:
                            notify(
                                "Successfully connected to Twitch!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info("Twitch OAuth connection successful")
                        else:
                            notify(
                                "Failed to connect to Twitch. Please check your credentials and try again.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.error("Twitch OAuth connection failed")

                        # Refresh the status display and reset button
                        self.refresh_twitch_status()
                        if "connect_twitch_button" in self.ui_elements:
                            self.ui_elements["connect_twitch_button"].set_text(
                                "Connect to Twitch"
                            )
                            self.ui_elements["connect_twitch_button"].enable()
                    except Exception as e:
                        logger.error(f"Error updating UI after Twitch OAuth: {str(e)}")
                    return False  # Stop checking
                elif oauth_result["status"] == "error":
                    try:
                        notify(
                            f"Error during Twitch connection: {oauth_result['error']}",
                            type="negative",
                        )
                        # Reset button state
                        if "connect_twitch_button" in self.ui_elements:
                            self.ui_elements["connect_twitch_button"].set_text(
                                "Connect to Twitch"
                            )
                            self.ui_elements["connect_twitch_button"].enable()
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    return False  # Stop checking
                return False  # Default stop

            # Start timer to check results with timeout protection
            check_count = {"count": 0}  # Use dict to maintain reference in closure
            max_checks = 150  # 30 seconds max (150 * 0.2s)

            def check_oauth_result_with_timeout():
                check_count["count"] += 1

                # Add timeout protection
                if check_count["count"] >= max_checks:
                    logger.warning("OAuth check timed out after 30 seconds")
                    notify(
                        "OAuth connection timed out. Please try again.", type="negative"
                    )
                    # Reset button state
                    if "connect_twitch_button" in self.ui_elements:
                        self.ui_elements["connect_twitch_button"].set_text(
                            "Connect to Twitch"
                        )
                        self.ui_elements["connect_twitch_button"].enable()
                    return False  # Stop timer

                # Call the original check function
                return check_oauth_result()

            oauth_timer = layout_schedule(0.2, check_oauth_result_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            logger.error(
                f"Error handling Twitch OAuth connection: {str(e)}", exc_info=True
            )
            notify(f"Error starting Twitch connection: {str(e)}", type="negative")
            # Reset button state
            if "connect_twitch_button" in self.ui_elements:
                self.ui_elements["connect_twitch_button"].set_text("Connect to Twitch")
                self.ui_elements["connect_twitch_button"].enable()

    def refresh_twitch_status(self):
        """Refresh the Twitch connection status display"""
        try:
            # Get current status from the Twitch module
            status_info = twitch.get_twitch_connection_status()

            # Update status label and color
            if "twitch_status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_valid = status_info.get("is_valid", False)

                self.ui_elements["twitch_status_label"].set_text(status_text)

                # Update color based on status
                if is_valid:
                    self.ui_elements["twitch_status_label"].classes(
                        replace="font-semibold text-green-500"
                    )
                else:
                    self.ui_elements["twitch_status_label"].classes(
                        replace="font-semibold text-red-500"
                    )

            # Update user label
            if "twitch_user_label" in self.ui_elements:
                user_name = status_info.get("user_name", "N/A")
                self.ui_elements["twitch_user_label"].set_text(user_name)

            # Update last update label
            if "twitch_last_update_label" in self.ui_elements:
                last_update = status_info.get("last_update", "Never")
                self.ui_elements["twitch_last_update_label"].set_text(last_update)

            logger.debug(f"Twitch status refreshed: {status_info}")

        except Exception as e:
            logger.error(f"Error refreshing Twitch status: {str(e)}", exc_info=True)
            # Set error status
            if "twitch_status_label" in self.ui_elements:
                self.ui_elements["twitch_status_label"].set_text("Status Error")
                self.ui_elements["twitch_status_label"].classes(
                    replace="font-semibold text-red-500"
                )

    def _ensure_psn_initialized(self):
        """Ensure PSN service is initialized (lazy initialization)"""
        if not self._psn_initialized:
            try:
                # Skip PSN initialization during startup to avoid blocking UI
                # psn_service.initialize_psn_module()
                self._psn_initialized = True
                logger.debug("PSN service initialization skipped to avoid blocking UI")
            except Exception as e:
                logger.warning(f"Failed to initialize PSN service: {str(e)}")

    def update_psn_status_display(self):
        """Update the PSN status display labels with current data"""
        try:
            # Initialize PSN service only when needed
            self._ensure_psn_initialized()

            from .. import psn_service

            status_text, user_text, _status_color = psn_service.get_psn_status_display()

            # Update UI elements if they exist
            if "psn_status_label" in self.ui_elements:
                self.ui_elements["psn_status_label"].set_text(status_text)
            if "psn_user_label" in self.ui_elements:
                self.ui_elements["psn_user_label"].set_text(user_text)

        except Exception as e:
            logger.error(f"Error updating PSN status display: {str(e)}")
            # Set error status if UI elements exist
            if "psn_status_label" in self.ui_elements:
                self.ui_elements["psn_status_label"].set_text("Error")
            if "psn_user_label" in self.ui_elements:
                self.ui_elements["psn_user_label"].set_text("N/A")

    def _cleanup_spotify_oauth(self):
        """Clean up after Spotify OAuth"""
        try:
            # Mark OAuth as no longer in progress
            self._spotify_oauth_in_progress = False

            # Reset button state
            if "connect_spotify_button" in self.ui_elements:
                self.ui_elements["connect_spotify_button"].set_text(
                    "Connect to Spotify"
                )
                self.ui_elements["connect_spotify_button"].enable()
        except Exception as e:
            logger.error(f"Error cleaning up Spotify OAuth: {str(e)}")

    def handle_spotify_oauth_connection(self):
        """Handle the Connect to Spotify button click to trigger OAuth connection"""
        try:
            logger.info("User clicked Connect to Spotify button")

            # Prevent duplicate OAuth attempts
            if (
                hasattr(self, "_spotify_oauth_in_progress")
                and self._spotify_oauth_in_progress
            ):
                logger.warning(
                    "Spotify OAuth already in progress, ignoring duplicate request"
                )
                notify("Spotify connection already in progress...", type="info")
                return

            # Check if client ID and secret are provided
            if not self.spotify_data.client_id or not self.spotify_data.client_secret:
                notify(
                    "Please enter your Spotify Client ID and Client Secret first!",
                    type="warning",
                )
                return

            # Mark OAuth as in progress
            self._spotify_oauth_in_progress = True

            # Update button state to show it's working
            if "connect_spotify_button" in self.ui_elements:
                self.ui_elements["connect_spotify_button"].set_text("Connecting...")
                self.ui_elements["connect_spotify_button"].disable()

            # Show a notification that the process is starting
            notify("Starting Spotify OAuth connection...", type="info")

            # Save only Spotify settings to avoid triggering database and other notifications
            self._save_spotify_settings_only()

            # Start the OAuth connection in a separate thread
            import threading

            # Create shared result holder for thread communication
            spotify_oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
            }

            def oauth_thread():
                try:
                    from .. import spotify

                    # Update Spotify settings with current values
                    spotify.update_spotify_settings(
                        client_id=self.spotify_data.client_id,
                        client_secret=self.spotify_data.client_secret,
                    )

                    # Use automatic OAuth flow
                    success = spotify.trigger_automatic_oauth_flow()
                    spotify_oauth_result["status"] = "complete"
                    spotify_oauth_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in Spotify OAuth thread: {str(e)}", exc_info=True
                    )
                    spotify_oauth_result["status"] = "error"
                    spotify_oauth_result["error"] = str(e)

            # Start the thread
            oauth_worker = threading.Thread(target=oauth_thread)
            oauth_worker.daemon = True
            oauth_worker.start()

            # Create a timer to check the result from main thread
            oauth_check_count = 0
            max_oauth_checks = 150  # 30 seconds at 0.2 second intervals

            def check_spotify_oauth_result():
                nonlocal oauth_check_count
                oauth_check_count += 1

                # Safety timeout for OAuth (30 seconds)
                if oauth_check_count >= max_oauth_checks:
                    logger.warning("Spotify OAuth check timed out")
                    self._cleanup_spotify_oauth()
                    notify("Spotify OAuth timed out", type="negative")
                    return False

                if spotify_oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif spotify_oauth_result["status"] == "complete":
                    try:
                        if spotify_oauth_result["success"]:
                            notify(
                                "Spotify authorization will open in your browser. Please authorize the application.",
                                type="info",
                                timeout=5000,
                            )
                            logger.info(
                                "Spotify automatic OAuth flow started successfully"
                            )
                        else:
                            notify(
                                "Failed to start Spotify authorization. Please check your credentials.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning(
                                "Failed to start Spotify automatic OAuth flow"
                            )

                        # Refresh the status display
                        self.refresh_spotify_status()

                    except Exception as e:
                        logger.error(f"Error updating UI after Spotify OAuth: {str(e)}")

                    self._cleanup_spotify_oauth()
                    return False  # Stop checking
                elif spotify_oauth_result["status"] == "error":
                    try:
                        notify(
                            f"Error during Spotify connection: {spotify_oauth_result['error']}",
                            type="negative",
                        )
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")

                    self._cleanup_spotify_oauth()
                    return False  # Stop checking

                return False  # Default stop

            # Start timer to check results with timeout protection
            spotify_check_count = {
                "count": 0
            }  # Use dict to maintain reference in closure
            spotify_max_checks = 150  # 30 seconds max (150 * 0.2s)

            def check_spotify_oauth_result_with_timeout():
                spotify_check_count["count"] += 1

                # Add timeout protection
                if spotify_check_count["count"] >= spotify_max_checks:
                    logger.warning("Spotify OAuth check timed out after 30 seconds")
                    notify(
                        "Spotify OAuth connection timed out. Please try again.",
                        type="negative",
                    )
                    self._cleanup_spotify_oauth()
                    return False  # Stop timer

                # Call the original check function
                return check_spotify_oauth_result()

            spotify_oauth_timer = layout_schedule(0.2, check_spotify_oauth_result_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(spotify_oauth_timer)

        except Exception as e:
            logger.error(
                f"Error handling Spotify OAuth connection: {str(e)}", exc_info=True
            )
            notify(f"Error starting Spotify connection: {str(e)}", type="negative")
            self._cleanup_spotify_oauth()

    def refresh_spotify_status(self):
        """Refresh the Spotify connection status display"""
        try:
            # Force reload the latest Spotify data from state manager
            self.spotify_data = state_manager.get_spotify_data()

            # Get current status from the Spotify module
            from .. import spotify

            status_info = spotify.get_spotify_status()

            # Also get the connection status directly from our data
            current_connection_status = (
                self.spotify_data.connection_status if self.spotify_data else "Unknown"
            )

            # Update market country field if it exists in UI
            if "spotify_data.market_country" in self.ui_elements:
                current_market = (
                    getattr(self.spotify_data, "market_country", "")
                    if self.spotify_data
                    else ""
                )
                self.ui_elements["spotify_data.market_country"].value = current_market

            # Update status label and color
            if "spotify_status_label" in self.ui_elements:
                # Use the most current status from either source
                status_text = current_connection_status or status_info.get(
                    "status", "Unknown"
                )
                is_authenticated = status_info.get("is_authenticated", False)

                self.ui_elements["spotify_status_label"].set_text(status_text)

                # Update color based on status
                if is_authenticated or status_text == "Connected":
                    self.ui_elements["spotify_status_label"].classes(
                        replace="font-semibold text-green-500"
                    )
                else:
                    self.ui_elements["spotify_status_label"].classes(
                        replace="font-semibold text-red-500"
                    )

            # Update current track label - Handle regional restrictions gracefully
            if "spotify_track_label" in self.ui_elements:
                current_track = status_info.get("current_track", "N/A")

                # If we get "Nothing playing - Nothing playing" due to regional restrictions,
                # show a more user-friendly message
                if current_track == "Nothing playing - Nothing playing":
                    if status_info.get("is_authenticated", False):
                        current_track = (
                            "Spotify connected (playback may be restricted by region)"
                        )
                    else:
                        current_track = "Not connected"

                self.ui_elements["spotify_track_label"].set_text(current_track)

            logger.debug(
                f"Spotify status refreshed - DB status: {current_connection_status}, Module status: {status_info.get('status', 'Unknown')}"
            )

        except Exception as e:
            logger.error(f"Error refreshing Spotify status: {str(e)}", exc_info=True)
            # Set error status
            if "spotify_status_label" in self.ui_elements:
                self.ui_elements["spotify_status_label"].set_text("Status Error")
                self.ui_elements["spotify_status_label"].classes(
                    replace="font-semibold text-red-500"
                )

    def _handle_database_type_change(self, event):
        """Handle database type changes with immediate UI updates"""
        try:
            # Extract the new database type from the event
            new_type = getattr(event, "value", None)
            if new_type is None and hasattr(event, "args") and event.args is not None:
                args = event.args
                if isinstance(args, (list, tuple)) and len(args) > 0:
                    new_type = args[0]
                elif not isinstance(args, (list, tuple)):
                    new_type = args
            if not new_type:
                db_key = "database_settings.database_type"
                if db_key in self.ui_elements:
                    new_type = self.ui_elements[db_key].value
            if not new_type:
                logger.warning(
                    "Could not extract value from database type change event"
                )
                return

            old_type = self.database_settings.database_type
            logger.info(f"Database type changed from {old_type} to {new_type}")

            # Show user notification
            notify(
                f"Database type changed to: {new_type}", type="positive", timeout=2000
            )

            # Ensure field handler sees the resolved value (NiceGUI select events vary)
            resolved_event = type(
                "DatabaseTypeEvent",
                (),
                {"value": new_type, "args": (new_type,)},
            )()
            self.on_field_change("database_settings", "database_type", resolved_event)

            new_type = self.database_settings.database_type

            # Update visibility of configuration sections
            if hasattr(self, "sql_config"):
                self.sql_config.visible = new_type == "sql"

            if hasattr(self, "firebase_config"):
                firebase_visible = new_type == "firebase"
                self.firebase_config.visible = firebase_visible
                if firebase_visible:
                    layout_schedule(
                        0.1, lambda: self.update_firebase_config_status(), once=True
                    )

            if hasattr(self, "mongodb_config"):
                self.mongodb_config.visible = new_type == "mongodb"

        except Exception as e:
            logger.error(
                f"Error in database type change handler: {str(e)}", exc_info=True
            )
            notify(f"Error changing database type: {str(e)}", type="negative")

    def debug_toggle_visibility(self):
        """Debug method to manually test visibility toggling"""
        try:
            notify("Testing visibility toggle...", type="info")
            logger.info("--- DEBUG: Manual visibility toggle test ---")

            # Get current database type from the dropdown
            current_dropdown_value = self.ui_elements[
                "database_settings.database_type"
            ].value
            logger.info(f"Current dropdown value: {current_dropdown_value}")

            # Check if config sections exist
            logger.info(f"sql_config exists: {hasattr(self, 'sql_config')}")
            logger.info(f"firebase_config exists: {hasattr(self, 'firebase_config')}")
            logger.info(f"mongodb_config exists: {hasattr(self, 'mongodb_config')}")

            # Toggle visibility based on dropdown value
            if hasattr(self, "sql_config"):
                sql_visible = current_dropdown_value == "sql"
                self.sql_config.visible = sql_visible
                notify(
                    f"Set SQL config visible: {sql_visible}", type="info", timeout=1000
                )
                logger.info(f"Set SQL config visible: {sql_visible}")

            if hasattr(self, "firebase_config"):
                firebase_visible = current_dropdown_value == "firebase"
                self.firebase_config.visible = firebase_visible
                notify(
                    f"Set Firebase config visible: {firebase_visible}",
                    type="info",
                    timeout=1000,
                )
                logger.info(f"Set Firebase config visible: {firebase_visible}")

            if hasattr(self, "mongodb_config"):
                mongodb_visible = current_dropdown_value == "mongodb"
                self.mongodb_config.visible = mongodb_visible
                notify(
                    f"Set MongoDB config visible: {mongodb_visible}",
                    type="info",
                    timeout=1000,
                )
                logger.info(f"Set MongoDB config visible: {mongodb_visible}")

        except Exception as e:
            logger.error(f"Error in debug_toggle_visibility: {str(e)}", exc_info=True)
            notify(f"Debug error: {str(e)}", type="negative")

    def test_database_connection(self):
        """Handle the Test Connection button click"""
        try:
            logger.info("User clicked Test Connection button")

            # Update button state to show it's working
            if "test_database_button" in self.ui_elements:
                self.ui_elements["test_database_button"].set_text("Testing...")
                self.ui_elements["test_database_button"].disable()

            # Show a notification that the process is starting
            notify("Testing database connection...", type="info")

            # Save current settings first to ensure the database manager has the latest configuration
            self.save_changes()

            # Test the connection in a separate thread
            import threading

            # Create shared result holder for thread communication
            db_test_result = {"status": "testing", "success": None, "error": None}

            def test_thread():
                try:
                    # Update database manager configuration
                    self.update_database_manager_config()

                    # Test the connection
                    success = database_manager.test_connection()
                    db_test_result["status"] = "complete"
                    db_test_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in database test thread: {str(e)}", exc_info=True
                    )
                    db_test_result["status"] = "error"
                    db_test_result["error"] = str(e)

            # Start the thread
            test_worker = threading.Thread(target=test_thread)
            test_worker.daemon = True
            test_worker.start()

            # Create a timer to check the result from main thread with timeout protection
            db_check_count = {"count": 0}  # Use dict to maintain reference in closure
            db_max_checks = 100  # 20 seconds max (100 * 0.2s)

            def check_db_test_result_with_timeout():
                db_check_count["count"] += 1

                # Add timeout protection
                if db_check_count["count"] >= db_max_checks:
                    logger.warning("Database test timed out after 20 seconds")
                    notify(
                        "Database test timed out. Please check your configuration.",
                        type="negative",
                    )
                    # Reset button state
                    if "test_database_button" in self.ui_elements:
                        self.ui_elements["test_database_button"].set_text(
                            "Test Connection"
                        )
                        self.ui_elements["test_database_button"].enable()
                    return False  # Stop timer

                # Call the original check function
                return check_db_test_result()

            def check_db_test_result():
                if db_test_result["status"] == "testing":
                    return True  # Continue checking
                elif db_test_result["status"] == "complete":
                    try:
                        if db_test_result["success"]:
                            notify(
                                "Database connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info("Database connection test successful")
                        else:
                            notify(
                                "Database connection failed. Please check your configuration.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.error("Database connection test failed")

                        # Refresh the status display and reset button
                        self.refresh_database_status()
                        if "test_database_button" in self.ui_elements:
                            self.ui_elements["test_database_button"].set_text(
                                "Test Connection"
                            )
                            self.ui_elements["test_database_button"].enable()
                    except Exception as e:
                        logger.error(f"Error updating UI after database test: {str(e)}")
                    return False  # Stop checking
                elif db_test_result["status"] == "error":
                    try:
                        notify(
                            f"Error during database test: {db_test_result['error']}",
                            type="negative",
                        )
                        # Reset button state
                        if "test_database_button" in self.ui_elements:
                            self.ui_elements["test_database_button"].set_text(
                                "Test Connection"
                            )
                            self.ui_elements["test_database_button"].enable()
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    return False  # Stop checking
                return False  # Default stop

            # Start timer to check results with timeout protection
            db_test_timer = layout_schedule(0.2, check_db_test_result_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(db_test_timer)

        except Exception as e:
            logger.error(f"Error handling database test: {str(e)}", exc_info=True)
            notify(f"Error starting database test: {str(e)}", type="negative")
            # Reset button state
            if "test_database_button" in self.ui_elements:
                self.ui_elements["test_database_button"].set_text("Test")
                self.ui_elements["test_database_button"].enable()

    def show_migration_dialog(self):
        """Handle the Show Migration Dialog button click"""
        try:
            logger.info("User clicked Show Migration Dialog button")

            # Create a dialog for migration options
            with ui.dialog() as migration_dialog, ui.card().classes("w-96"):
                ui.label("Database Migration").classes("text-xl font-bold mb-4")

                ui.label("Select source and target databases for migration:").classes(
                    "mb-4"
                )

                # Get available database types
                available_dbs = database_manager.get_available_databases()

                with ui.row().classes("w-full items-center mb-2"):
                    ui.label("From:").classes("w-20")
                    source_select = ui.select(
                        options=available_dbs,
                        value=available_dbs[0] if available_dbs else "sql",
                    ).classes("flex-1")

                with ui.row().classes("w-full items-center mb-4"):
                    ui.label("To:").classes("w-20")
                    target_select = ui.select(
                        options=available_dbs,
                        value=(
                            available_dbs[1]
                            if len(available_dbs) > 1
                            else available_dbs[0]
                        ),
                    ).classes("flex-1")

                ui.label(
                    "Warning: This will copy all data from the source to the target database. Existing data in the target may be overwritten."
                ).classes("text-orange-600 mb-4")

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=migration_dialog.close).props(
                        "outline"
                    )
                    ui.button(
                        "Start Migration",
                        on_click=lambda: self.start_migration(
                            source_select.value or "sql",
                            target_select.value or "sql",
                            migration_dialog,
                        ),
                    ).props("color=primary")

            migration_dialog.open()

        except Exception as e:
            logger.error(f"Error showing migration dialog: {str(e)}", exc_info=True)
            notify(f"Error showing migration dialog: {str(e)}", type="negative")

    def start_migration(self, source_type: str, target_type: str, dialog):
        """Start the database migration process"""
        try:
            if source_type == target_type:
                notify("Source and target databases cannot be the same", type="warning")
                return

            dialog.close()

            # Show progress notification
            notify("Starting database migration...", type="info")

            # Create a status tracking mechanism
            migration_status = {
                "completed": False,
                "success": False,
                "error": None,
                "notified": False,
            }

            # Start migration in a separate thread
            import threading

            def migration_thread():
                try:
                    logger.info(
                        f"Starting migration from {source_type} to {target_type}"
                    )

                    # Get current database configuration
                    current_config = database_manager.get_config()

                    # Prefer live manager config when source type matches the active database
                    if source_type == current_config.database_type:
                        source_config = replace(current_config)
                    elif source_type == "firebase":
                        source_config = DatabaseConfig(
                            database_type="firebase",
                            firebase_service_account_path=self.database_settings.firebase_service_account_path,
                            firebase_database_url=self.database_settings.firebase_database_url,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )
                    elif source_type == "sql":
                        source_config = DatabaseConfig(
                            database_type="sql",
                            sql_database_path=self.database_settings.sql_database_path,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )
                    else:  # mongodb
                        source_config = DatabaseConfig(
                            database_type="mongodb",
                            mongodb_connection_string=self.database_settings.mongodb_connection_string,
                            mongodb_database_name=self.database_settings.mongodb_database_name,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )

                    if target_type == "firebase":
                        target_config = DatabaseConfig(
                            database_type="firebase",
                            firebase_service_account_path=self.database_settings.firebase_service_account_path,
                            firebase_database_url=self.database_settings.firebase_database_url,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )
                    elif target_type == "sql":
                        target_config = DatabaseConfig(
                            database_type="sql",
                            sql_database_path=self.database_settings.sql_database_path,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )
                    else:  # mongodb
                        target_config = DatabaseConfig(
                            database_type="mongodb",
                            mongodb_connection_string=self.database_settings.mongodb_connection_string,
                            mongodb_database_name=self.database_settings.mongodb_database_name,
                            streamer_name="mycelian",  # Always use mycelian for database consistency
                        )

                    # Perform migration
                    success = database_manager.migrate_data(
                        source_config, target_config
                    )

                    if success:
                        prior_type = self.database_settings.database_type
                        snapshot_before_switch = replace(database_manager.get_config())
                        switch_cfg = replace(
                            DatabaseConfig(
                                **self.database_settings.__dict__,
                                streamer_name="mycelian",
                            ),
                            database_type=target_type,
                        )
                        if database_manager.update_config(**switch_cfg.__dict__):
                            self.database_settings.database_type = target_type
                            if not config_manager.update_database_config(
                                **self.database_settings.__dict__
                            ):
                                logger.error(
                                    "Migration succeeded but failed to persist config.json"
                                )
                            state_manager.update_database_setting(
                                "database_type", target_type
                            )
                            state_manager.save_changes()
                            self.update_database_config_visibility()
                            migration_status["success"] = True
                            migration_status["completed"] = True
                            logger.info(
                                f"Migration from {source_type} to {target_type} completed successfully"
                            )
                        else:
                            self.database_settings.database_type = prior_type
                            database_manager.initialize(snapshot_before_switch)
                            migration_status["success"] = False
                            migration_status["completed"] = True
                            migration_status["error"] = (
                                "Data was copied but the app could not switch to the "
                                "target database. Check credentials and logs."
                            )
                            logger.error(
                                "Migration copy succeeded but switching active DB failed"
                            )
                    else:
                        migration_status["success"] = False
                        migration_status["completed"] = True
                        migration_status["error"] = (
                            f"Migration from {source_type} to {target_type} failed"
                        )

                        logger.error(
                            f"Migration from {source_type} to {target_type} failed"
                        )

                except Exception as e:
                    logger.error(f"Error in migration thread: {str(e)}", exc_info=True)
                    migration_status["success"] = False
                    migration_status["completed"] = True
                    migration_status["error"] = f"Error during migration: {str(e)}"

            # Start the thread
            migration_worker = threading.Thread(target=migration_thread)
            migration_worker.daemon = True
            migration_worker.start()

            # Create a timer to check migration status and update UI
            migration_timer = None

            def check_migration_status():
                nonlocal migration_timer
                if migration_status["completed"] and not migration_status["notified"]:
                    migration_status["notified"] = True  # Mark as notified

                    if migration_status["success"]:
                        notify(
                            f"Migration from {source_type} to {target_type} completed successfully!",
                            type="positive",
                            timeout=5000,
                        )
                        self.refresh_database_status()
                    else:
                        error_msg = migration_status.get("error", "Unknown error")
                        notify(error_msg, type="negative", timeout=5000)

                    # Cancel the timer to stop checking
                    if migration_timer:
                        migration_timer.cancel()
                    return False  # Stop the timer
                elif migration_status["completed"]:
                    # Already notified, just stop the timer
                    if migration_timer:
                        migration_timer.cancel()
                    return False
                return True  # Continue checking

            # Start a timer to check status every 500ms with timeout protection
            migration_check_count = {
                "count": 0
            }  # Use dict to maintain reference in closure
            migration_max_checks = 120  # 60 seconds max (120 * 0.5s)

            def check_migration_status_with_timeout():
                migration_check_count["count"] += 1

                # Add timeout protection
                if migration_check_count["count"] >= migration_max_checks:
                    logger.warning("Migration check timed out after 60 seconds")
                    notify(
                        "Migration operation timed out. Please check the logs.",
                        type="negative",
                    )
                    dialog.close()
                    return False  # Stop timer

                # Call the original check function
                return check_migration_status()

            migration_timer = layout_schedule(0.5, check_migration_status_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(migration_timer)

        except Exception as e:
            logger.error(f"Error starting migration: {str(e)}", exc_info=True)
            notify(f"Error starting migration: {str(e)}", type="negative")

    def refresh_database_status(self):
        """Refresh the database connection status display"""
        try:
            # Get current status from the database manager
            status_info = database_manager.get_connection_status()

            # Update status label and color
            if "database_status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_connected = status_info.get("is_connected", False)

                self.ui_elements["database_status_label"].set_text(status_text)

                # Update color based on status
                if is_connected:
                    self.ui_elements["database_status_label"].classes(
                        replace="font-semibold text-green-500"
                    )
                else:
                    self.ui_elements["database_status_label"].classes(
                        replace="font-semibold text-red-500"
                    )

            # Update database type label
            if "database_type_label" in self.ui_elements:
                db_type = status_info.get("database_type", "N/A")
                self.ui_elements["database_type_label"].set_text(db_type)

            # Update last check label
            if "database_last_check_label" in self.ui_elements:
                last_check = status_info.get("last_check", "Never")
                # Format the timestamp if it's an ISO string
                try:
                    if last_check != "Never":
                        from datetime import datetime

                        dt = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
                        last_check = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass  # Keep original value if parsing fails

                self.ui_elements["database_last_check_label"].set_text(last_check)

            # Show specific Firebase configuration issues if present
            if (
                status_info.get("database_type") == "Firebase"
                and not status_info.get("is_connected", False)
                and "config_issues" in status_info
            ):
                config_issues = status_info["config_issues"]
                if config_issues:
                    issue_text = "Configuration Issues:\n" + "\n".join(
                        f"• {issue}" for issue in config_issues
                    )
                    notify(issue_text, type="warning", timeout=8000)

            logger.debug(f"Database status refreshed: {status_info}")

        except Exception as e:
            logger.error(f"Error refreshing database status: {str(e)}", exc_info=True)
            # Set error status
            if "database_status_label" in self.ui_elements:
                self.ui_elements["database_status_label"].set_text("Status Error")
                self.ui_elements["database_status_label"].classes(
                    replace="font-semibold text-red-500"
                )

    def update_database_config_visibility(self):
        """Update the visibility of database configuration elements"""
        try:
            if not hasattr(self, "database_settings") or not self.database_settings:
                logger.warning(
                    "No database_settings found in update_database_config_visibility"
                )
                return

            current_type = self.database_settings.database_type
            logger.info(f"Updating database config visibility for type: {current_type}")

            # Show/hide configuration sections based on selected database type
            if hasattr(self, "sql_config"):
                visible = current_type == "sql"
                logger.info(f"Setting SQL config visibility to: {visible}")
                self.sql_config.visible = visible
                logger.info(f"SQL config visibility set successfully: {visible}")
            else:
                logger.warning("sql_config attribute not found")

            if hasattr(self, "firebase_config"):
                visible = current_type == "firebase"
                logger.info(f"Setting Firebase config visibility to: {visible}")
                self.firebase_config.visible = visible
                logger.info(f"Firebase config visibility set successfully: {visible}")
                # Update Firebase status when it becomes visible
                if visible:
                    logger.info("Firebase config is now visible, updating status")
                    # Use a timer to ensure UI is ready
                    layout_schedule(
                        0.1, lambda: self.update_firebase_config_status(), once=True
                    )
            else:
                logger.warning("firebase_config attribute not found")

            if hasattr(self, "mongodb_config"):
                visible = current_type == "mongodb"
                logger.info(f"Setting MongoDB config visibility to: {visible}")
                self.mongodb_config.visible = visible
                logger.info(f"MongoDB config visibility set successfully: {visible}")
            else:
                logger.warning("mongodb_config attribute not found")

            logger.info(
                f"Successfully updated database config visibility for type: {current_type}"
            )

        except Exception as e:
            logger.error(
                f"Error updating database config visibility: {str(e)}", exc_info=True
            )

    def update_database_manager_config(self):
        """Update the database manager configuration based on current settings"""
        try:
            if not hasattr(self, "database_settings") or not self.database_settings:
                return

            # Create a new database configuration
            config = DatabaseConfig(
                database_type=self.database_settings.database_type,
                sql_database_path=self.database_settings.sql_database_path,
                firebase_service_account_path=self.database_settings.firebase_service_account_path,
                firebase_database_url=self.database_settings.firebase_database_url,
                mongodb_connection_string=self.database_settings.mongodb_connection_string,
                mongodb_database_name=self.database_settings.mongodb_database_name,
                streamer_name="mycelian",  # Always use mycelian for database consistency
                connection_timeout=self.database_settings.connection_timeout,
                retry_attempts=self.database_settings.retry_attempts,
            )

            # Update the database manager
            success = database_manager.update_config(**config.__dict__)

            if success:
                logger.debug(
                    f"Updated database manager configuration: {config.database_type}"
                )
                # Show success notification for database type changes
                if config.database_type != "sql":  # Only show for non-default databases
                    notify(
                        f"Switched to {config.database_type} database",
                        type="positive",
                        timeout=2000,
                    )
            else:
                logger.error(
                    f"Failed to update database manager configuration to {config.database_type}"
                )
                # Show error notification
                notify(
                    f"Failed to switch to {config.database_type} database. Check configuration and logs.",
                    type="negative",
                    timeout=5000,
                )

                # Refresh database status to show the error
                self.refresh_database_status()

        except Exception as e:
            logger.error(
                f"Error updating database manager config: {str(e)}", exc_info=True
            )
            notify(f"Error updating database configuration: {str(e)}", type="negative")

    def browse_sql_database_file(self):
        """Open file browser for SQLite database file selection"""
        try:
            # Create a NiceGUI-based file browser dialog
            self._show_database_file_browser_dialog("sql")
        except Exception as e:
            logger.error(f"Error in database file browser: {str(e)}", exc_info=True)
            notify(
                "Error opening file browser. Please enter the path manually.",
                type="negative",
            )

    @staticmethod
    def _format_source_url_title(name: str) -> str:
        return name.replace("_", " ").title()

    @staticmethod
    def _format_source_url_control_badge(url_info: dict) -> str:
        if url_info.get("type") == "template":
            count = 0
        else:
            count = int(url_info.get("control_count", 0))
        noun = "Control" if count == 1 else "Controls"
        return f"{count} {noun}"

    def refresh_source_urls(self):
        """Refresh the list of available source URLs"""
        urls = []  # Initialize urls variable at the start
        try:
            # Get the source URLs container
            container = self.ui_elements.get("source_urls_container")
            if not container:
                logger.warning("Source URLs container not found")
                return

            # Clear existing content
            container.clear()

            # Get available URLs from web engine
            from .. import alert_processor, web_engine

            # Check both locations for web engine instance
            engine_instance = None
            if web_engine.web_engine_instance:
                engine_instance = web_engine.web_engine_instance
            elif alert_processor.web_engine_instance:
                engine_instance = alert_processor.web_engine_instance

            if engine_instance:
                urls = engine_instance.get_available_source_urls()

                if urls:
                    display_urls = [
                        u
                        for u in urls
                        if u.get("type") != "system_info"
                        and not str(u.get("name", "")).startswith("_")
                    ]
                    with container:
                        with ui.element("div").classes("source-url-cards-grid"):
                            for url_info in display_urls:
                                description = url_info["description"]
                                with ui.card().classes(
                                    "w-full source-url-card settings-tab-card transition-colors"
                                ):
                                    with ui.column().classes("w-full gap-1"):
                                        with ui.row().classes(
                                            "items-center w-full source-url-card-header"
                                        ):
                                            with ui.element(
                                                "div"
                                            ).classes(
                                                "source-url-card-header-info"
                                            ).tooltip(description).classes(
                                                "bg-theme-surface"
                                            ):
                                                ui.label(
                                                    self._format_source_url_title(
                                                        url_info["name"]
                                                    )
                                                ).classes(
                                                    "text-sm font-semibold source-url-card-title"
                                                )
                                                ui.label(
                                                    self._format_source_url_control_badge(
                                                        url_info
                                                    )
                                                ).classes(
                                                    "source-url-card-badge text-xs"
                                                )

                                            copy_btn = ui.button(
                                                "",
                                                on_click=lambda _=None,
                                                url=url_info[
                                                    "url"
                                                ]: self._copy_url_to_clipboard(
                                                    url
                                                ),
                                            ).props(
                                                "size=sm icon=content_copy outline color=primary"
                                            ).classes("source-url-card-copy")
                                            copy_btn.tooltip(
                                                "Copy URL to clipboard"
                                            )

                                        url_input = ui.input(
                                            value=url_info["url"]
                                        ).classes("w-full font-mono text-xs")
                                        url_input.props("outlined dense readonly")
                                        url_input.tooltip(
                                            "Browser source URL for OBS"
                                        ).classes("bg-theme-surface")
                else:
                    with container:
                        ui.label(
                            "No source URLs available. Make sure the web engine is running."
                        ).classes("secondary-text text-center p-4")
            else:
                with container:
                    ui.label(
                        "Web engine not available. Please start the application properly."
                    ).classes("text-red-400 text-center p-4")

            logger.debug(f"Refreshed source URLs display with {len(urls)} URLs")

        except Exception as e:
            logger.error(f"Error refreshing source URLs: {str(e)}", exc_info=True)
            # Show error in the container if possible
            container = self.ui_elements.get("source_urls_container")
            if container:
                container.clear()
                with container:
                    ui.label(f"Error loading URLs: {str(e)}").classes(
                        "text-red-400 text-center p-4"
                    )

    @staticmethod
    def _truncate_log_error_message(message: str, max_len: int = 120) -> str:
        text = " ".join(message.split())
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def refresh_log_errors(self) -> None:
        """Refresh actionable error summary from mycelian.log."""
        count_label = self.ui_elements.get("log_error_count_label")
        list_container = self.ui_elements.get("log_errors_list_container")
        if not count_label or not list_container:
            return

        try:
            summary = get_actionable_errors()
            list_container.clear()

            if summary.total_count == 0:
                count_label.set_text("No actionable errors")
                count_label.classes(remove="text-amber-400 text-red-400")
                count_label.classes(add="secondary-text")
                return

            noun = "error" if summary.total_count == 1 else "errors"
            count_label.set_text(f"{summary.total_count} actionable {noun}")
            count_label.classes(remove="secondary-text")
            count_label.classes(add="text-amber-400")

            with list_container:
                for item in summary.unique_errors:
                    display = self._truncate_log_error_message(item.message)
                    if item.count > 1:
                        display = f"{display} (×{item.count})"
                    ui.label(display).classes("secondary-text text-sm break-words")

        except Exception as e:
            logger.error(f"Error refreshing log errors: {e}", exc_info=True)
            count_label.set_text("Unable to read log file")
            count_label.classes(remove="secondary-text text-amber-400")
            count_label.classes(add="text-red-400")
            list_container.clear()

    def open_logs_folder(self) -> None:
        """Open the folder containing mycelian.log in the native file manager."""
        try:
            reveal_in_file_manager(get_log_dir())
        except Exception as e:
            logger.error(f"Error opening logs folder: {e}", exc_info=True)
            notify("Failed to open logs folder.", type="negative")

    def _copy_url_to_clipboard(self, url: str):
        """Copy URL to clipboard and show notification"""
        try:
            # Use Python's native clipboard functionality for NiceGUI desktop app
            try:
                import pyperclip

                pyperclip.copy(url)
                notify(f"Copied: {url}", type="positive", timeout=2000)
                logger.debug(f"Copied URL to clipboard using pyperclip: {url}")
                return
            except ImportError:
                logger.debug("pyperclip not available, trying alternative methods")

            # Fallback to platform-specific clipboard methods
            import subprocess
            import sys

            if sys.platform == "darwin":  # macOS
                subprocess.run(["pbcopy"], input=url.encode(), check=True)
                notify(f"Copied: {url}", type="positive", timeout=2000)
                logger.debug(f"Copied URL to clipboard using pbcopy: {url}")
            elif sys.platform == "win32":  # Windows
                subprocess.run(["clip"], input=url.encode(), shell=True, check=True)
                notify(f"Copied: {url}", type="positive", timeout=2000)
                logger.debug(f"Copied URL to clipboard using clip: {url}")
            elif sys.platform.startswith("linux"):  # Linux
                # Try xclip first, then xsel as fallback
                try:
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=url.encode(),
                        check=True,
                    )
                    notify(f"Copied: {url}", type="positive", timeout=2000)
                    logger.debug(f"Copied URL to clipboard using xclip: {url}")
                except (subprocess.CalledProcessError, FileNotFoundError):
                    subprocess.run(
                        ["xsel", "--clipboard", "--input"],
                        input=url.encode(),
                        check=True,
                    )
                    notify(f"Copied: {url}", type="positive", timeout=2000)
                    logger.debug(f"Copied URL to clipboard using xsel: {url}")
            else:
                raise Exception(f"Unsupported platform: {sys.platform}")

        except Exception as e:
            logger.error(f"Error copying URL to clipboard: {str(e)}", exc_info=True)
            notify(
                "Failed to copy URL to clipboard. You can manually select and copy the URL from the text field.",
                type="warning",
                timeout=4000,
            )

    def check_for_updates_manual(self):
        """Manual update check triggers the centralized UpdateManager."""
        try:
            logger.info(
                "User clicked Check for Updates button (delegated to UpdateManager)"
            )
            from .. import updater

            updater.update_manager.trigger_manual_check()
        except Exception as e:
            logger.error(f"Error delegating manual update check: {e}", exc_info=True)
            notify("Failed to start update check.", type="negative")

    async def fetch_all_releases_from_github(self) -> List[Dict[str, str]]:
        """
        Fetch all releases from GitHub repository

        Returns:
            List of release dictionaries with version, date, and notes
        """
        # GitHub configuration (matching updater.py)
        GITHUB_OWNER = "mushroomsuprise"
        GITHUB_REPO = "mycelian"
        GITHUB_API_BASE = "https://api.github.com"

        api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        releases_data = await response.json()

                        releases = []
                        for release in releases_data:
                            # Skip draft releases
                            if release.get("draft", False):
                                continue

                            # Extract version from tag_name (remove 'v' prefix if present)
                            tag_name = release.get("tag_name", "")
                            version = tag_name.lstrip("v")

                            # Get release date
                            published_at = release.get("published_at", "")
                            release_date = ""
                            if published_at:
                                try:
                                    from datetime import datetime

                                    dt = datetime.fromisoformat(
                                        published_at.replace("Z", "+00:00")
                                    )
                                    release_date = dt.strftime("%B %d, %Y")
                                except:
                                    release_date = published_at.split("T")[
                                        0
                                    ]  # Fallback to date part

                            # Get release notes
                            release_notes = release.get(
                                "body", "No release notes available."
                            )

                            # Get release URL
                            release_url = release.get("html_url", "")

                            if version:
                                releases.append(
                                    {
                                        "version": version,
                                        "date": release_date,
                                        "notes": release_notes,
                                        "url": release_url,
                                        "prerelease": release.get("prerelease", False),
                                    }
                                )

                        logger.info(
                            f"Successfully fetched {len(releases)} releases from GitHub"
                        )
                        return releases

                    elif response.status == 404:
                        logger.warning(
                            f"GitHub repository not found or has no releases"
                        )
                        return []
                    else:
                        logger.error(
                            f"GitHub API request failed with status {response.status}"
                        )
                        return []

        except aiohttp.ClientError as e:
            logger.error(f"Network error while fetching releases from GitHub: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching releases from GitHub: {e}")
            return []

    def show_changelog_modal(self):
        """Show the changelog modal window"""
        try:
            logger.info("User clicked View Changelog button")

            # Prevent multiple concurrent requests
            if hasattr(self, "_changelog_loading") and self._changelog_loading:
                notify("Changelog is already loading...", type="info", timeout=2000)
                return

            self._changelog_loading = True

            # Show loading notification
            notify("Fetching changelog from GitHub...", type="info", timeout=2000)

            # Use a shared variable to communicate between threads
            changelog_result = {
                "status": "loading",
                "releases": [],
                "error": None,
                "processed": False,
            }

            # Start changelog fetch in a separate thread
            import threading

            def changelog_fetch_thread():
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    # Fetch releases
                    releases = loop.run_until_complete(
                        self.fetch_all_releases_from_github()
                    )

                    changelog_result["status"] = "complete"
                    changelog_result["releases"] = releases

                except Exception as e:
                    logger.error(f"Error in changelog fetch thread: {e}", exc_info=True)
                    changelog_result["status"] = "error"
                    changelog_result["error"] = str(e)
                finally:
                    try:
                        loop.close()
                    except:
                        pass

            # Start the fetch thread
            changelog_worker = threading.Thread(target=changelog_fetch_thread)
            changelog_worker.daemon = True
            changelog_worker.start()

            # Use a timer to check the result and show modal when ready
            def check_changelog_result():
                try:
                    # Prevent processing multiple times
                    if changelog_result.get("processed", False):
                        return False  # Stop checking - already processed

                    if changelog_result["status"] == "loading":
                        return True  # Continue checking
                    elif changelog_result["status"] == "complete":
                        changelog_result["processed"] = True  # Mark as processed
                        self._changelog_loading = False  # Reset loading flag

                        releases = changelog_result["releases"]
                        if releases:
                            self._create_changelog_modal(releases)
                        else:
                            notify(
                                "No releases found or repository not available yet.",
                                type="warning",
                                timeout=4000,
                            )
                        return False  # Stop checking
                    elif changelog_result["status"] == "error":
                        changelog_result["processed"] = True  # Mark as processed
                        self._changelog_loading = False  # Reset loading flag

                        notify(
                            f"Error fetching changelog: {changelog_result['error']}",
                            type="negative",
                        )
                        return False  # Stop checking

                    return False  # Default stop
                except Exception as e:
                    logger.error(f"Error in check_changelog_result: {e}")
                    self._changelog_loading = False  # Reset loading flag on error
                    return False  # Stop checking

            # Start timer to check results every 200ms (reduced frequency)
            layout_schedule(0.2, check_changelog_result)

        except Exception as e:
            logger.error(f"Error showing changelog modal: {e}", exc_info=True)
            self._changelog_loading = False  # Reset loading flag on error
            notify(f"Error displaying changelog: {str(e)}", type="negative")

    def _create_changelog_modal(self, releases: List[Dict[str, str]]):
        """Create and display the changelog modal with release data"""
        try:
            with ui.dialog().props("maximized") as changelog_dialog:
                with ui.card().classes("w-full max-w-4xl mx-auto my-8"):
                    # Header
                    with ui.row().classes(
                        "w-full items-center justify-between p-4 border-b"
                    ):
                        ui.label("Mycelian Changelog").classes("text-2xl font-bold")
                        ui.button(icon="close", on_click=changelog_dialog.close).props(
                            "flat round"
                        )

                    # Content area with scroll
                    with ui.scroll_area().classes("w-full").style("height: 70vh"):
                        with ui.column().classes("w-full gap-4 p-4"):
                            if not releases:
                                ui.label("No releases found.").classes(
                                    "text-center muted-text text-lg"
                                )
                            else:
                                for release in releases:
                                    # Release card
                                    with ui.card().classes("w-full"):
                                        # Release header
                                        with ui.row().classes(
                                            "w-full items-center justify-between p-4 border-b"
                                        ):
                                            with ui.column().classes("gap-1"):
                                                version_text = (
                                                    f"Version {release['version']}"
                                                )
                                                if release.get("prerelease", False):
                                                    version_text += " (Pre-release)"
                                                ui.label(version_text).classes(
                                                    "text-xl font-semibold"
                                                )
                                                if release.get("date"):
                                                    ui.label(
                                                        f"Released {release['date']}"
                                                    ).classes("text-sm muted-text")

                                            # Link to GitHub release
                                            if release.get("url"):

                                                def create_open_url_handler(url):
                                                    return lambda _: webbrowser.open(
                                                        url
                                                    )

                                                ui.button(
                                                    "View on GitHub",
                                                    on_click=create_open_url_handler(
                                                        release["url"]
                                                    ),
                                                ).props(
                                                    "outline size=sm icon=open_in_new"
                                                )

                                        # Release notes
                                        with ui.column().classes("w-full p-4"):
                                            notes = release.get(
                                                "notes", "No release notes available."
                                            )
                                            if notes.strip():
                                                # Format the markdown-like content for display
                                                formatted_notes = (
                                                    self._format_release_notes(notes)
                                                )
                                                ui.html(formatted_notes).classes(
                                                    "text-sm"
                                                )
                                            else:
                                                ui.label(
                                                    "No release notes available."
                                                ).classes("text-sm muted-text")

            # Open the dialog
            changelog_dialog.open()

        except Exception as e:
            logger.error(f"Error creating changelog modal: {e}", exc_info=True)
            notify("Error displaying changelog data", type="negative")

    def _format_release_notes(self, notes: str) -> str:
        """Format release notes for HTML display"""
        try:
            # Basic markdown-like formatting for GitHub release notes
            import html

            # Escape HTML first
            formatted = html.escape(notes)

            # Convert common markdown patterns to HTML
            # Headers (## becomes h3, ### becomes h4)
            import re

            formatted = re.sub(
                r"^### (.+)$", r"<h4>\1</h4>", formatted, flags=re.MULTILINE
            )
            formatted = re.sub(
                r"^## (.+)$", r"<h3>\1</h3>", formatted, flags=re.MULTILINE
            )
            formatted = re.sub(
                r"^# (.+)$", r"<h2>\1</h2>", formatted, flags=re.MULTILINE
            )

            # Bold text (**text** or __text__)
            formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", formatted)
            formatted = re.sub(r"__(.+?)__", r"<strong>\1</strong>", formatted)

            # Italic text (*text* or _text_)
            formatted = re.sub(r"\*(.+?)\*", r"<em>\1</em>", formatted)
            formatted = re.sub(r"_(.+?)_", r"<em>\1</em>", formatted)

            # Code blocks (```code```)
            formatted = re.sub(
                r"```(.+?)```", r"<code>\1</code>", formatted, flags=re.DOTALL
            )

            # Inline code (`code`)
            formatted = re.sub(r"`(.+?)`", r"<code>\1</code>", formatted)

            # Links ([text](url))
            formatted = re.sub(
                r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank">\1</a>', formatted
            )

            # Bullet points (- or *)
            lines = formatted.split("\n")
            in_list = False
            formatted_lines = []

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("- ") or stripped.startswith("* "):
                    if not in_list:
                        formatted_lines.append("<ul>")
                        in_list = True
                    formatted_lines.append(f"<li>{stripped[2:]}</li>")
                else:
                    if in_list:
                        formatted_lines.append("</ul>")
                        in_list = False
                    formatted_lines.append(line)

            if in_list:
                formatted_lines.append("</ul>")

            formatted = "\n".join(formatted_lines)

            # Convert line breaks to <br> tags
            formatted = formatted.replace("\n", "<br>")

            return formatted.strip()

        except Exception as e:
            logger.error(f"Error formatting release notes: {e}")
            return notes  # Return original notes if formatting fails

    def browse_firebase_key_file(self):
        """Open file browser for Firebase service account key file selection"""
        try:
            # Create a NiceGUI-based file browser dialog
            self._show_database_file_browser_dialog("firebase")
        except Exception as e:
            logger.error(f"Error in database file browser: {str(e)}", exc_info=True)
            notify(
                "Error opening file browser. Please enter the path manually.",
                type="negative",
            )

    def _show_database_file_browser_dialog(self, file_type):
        """Show a NiceGUI-based file browser dialog for database files"""
        import os
        from pathlib import Path

        # Determine dialog settings based on file type
        if file_type == "sql":
            title = "Select SQLite Database File"
            extension_filter = ".db"
            field_key = "database_settings.sql_database_path"
            default_filename = "mycelian.db"
        elif file_type == "firebase":
            title = "Select Firebase Service Account Key File"
            extension_filter = ".json"
            field_key = "database_settings.firebase_service_account_path"
            default_filename = "ServiceAccountKey.json"
        else:
            logger.error(f"Unknown file type for database browser: {file_type}")
            return

        # Create dialog state
        dialog_state = {
            "current_path": get_working_directory(),
            "selected_file": None,
            "path_input": None,
            "file_list": None,
            "file_type": file_type,
            "extension_filter": extension_filter,
            "field_key": field_key,
        }

        with (
            ui.dialog().props(_FILE_BROWSER_DIALOG_PROPS) as dialog,
            ui.card().classes(_FILE_BROWSER_CARD_CLASSES),
        ):
            ui.label(title).classes("text-lg font-bold mb-4 shrink-0")

            with ui.column().classes("w-full min-h-0 flex-1 gap-3"):
                # Current path display and manual entry
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label("Path:").classes("text-sm font-medium")
                    dialog_state["path_input"] = ui.input(
                        value=dialog_state["current_path"],
                        placeholder="Enter file path or navigate below...",
                    ).classes("flex-1")
                    ui.button(
                        "Go",
                        icon="folder",
                        on_click=lambda: self._navigate_to_database_path(dialog_state),
                    ).props("dense")

                # Quick access buttons
                with ui.row().classes("w-full gap-2 mb-2 shrink-0"):
                    ui.button(
                        "Home",
                        icon="home",
                        on_click=lambda: self._navigate_to_database_path(
                            dialog_state, Path.home()
                        ),
                    ).props("dense outline size=sm")

                    ui.button(
                        "Desktop",
                        icon="desktop_windows",
                        on_click=lambda: self._navigate_to_database_path(
                            dialog_state, Path.home() / "Desktop"
                        ),
                    ).props("dense outline size=sm")

                    ui.button(
                        "Downloads",
                        icon="download",
                        on_click=lambda: self._navigate_to_database_path(
                            dialog_state, Path.home() / "Downloads"
                        ),
                    ).props("dense outline size=sm")

                    ui.button(
                        "Documents",
                        icon="description",
                        on_click=lambda: self._navigate_to_database_path(
                            dialog_state, Path.home() / "Documents"
                        ),
                    ).props("dense outline size=sm")

                # File listing area
                with ui.scroll_area().classes(
                    "w-full min-h-0 flex-1 border border-theme-default rounded-lg p-2 bg-theme-base"
                ):
                    dialog_state["file_list"] = ui.column().classes("w-full gap-1")

                # Selected file display
                with ui.row().classes("w-full items-center shrink-0"):
                    ui.label("Selected:").classes("text-sm font-medium")
                    dialog_state["selected_label"] = ui.label("None").classes(
                        "text-sm secondary-text"
                    )

                # New file name input (for SQL databases)
                if file_type == "sql":
                    with ui.row().classes("w-full items-center shrink-0"):
                        ui.label("Or create new:").classes("text-sm font-medium")
                        dialog_state["new_filename_input"] = ui.input(
                            value=default_filename,
                            placeholder="Enter new database filename...",
                        ).classes("flex-1")

                # Dialog buttons
                with ui.row().classes("w-full justify-end gap-2 mt-4 shrink-0"):
                    ui.button("Cancel", on_click=dialog.close).classes("btn-cancel")

                    select_button = ui.button(
                        "Select File",
                        icon="check",
                        on_click=lambda: self._select_database_file_from_dialog(
                            dialog_state, dialog
                        ),
                    ).classes("btn-primary")
                    select_button.enabled = False
                    dialog_state["select_button"] = select_button

        # Initial file listing
        self._update_database_file_listing(dialog_state)

        dialog.open()

    def _navigate_to_database_path(self, dialog_state, path=None):
        """Navigate to a specific path in the database file browser"""
        try:
            from pathlib import Path

            if path is None:
                path = dialog_state["path_input"].value

            path = Path(path).expanduser().resolve()

            if path.exists():
                dialog_state["current_path"] = str(path)
                dialog_state["path_input"].value = str(path)
                dialog_state["selected_file"] = None
                dialog_state["selected_label"].set_text("None")
                dialog_state["select_button"].enabled = False

                self._update_database_file_listing(dialog_state)
            else:
                notify(f"Path does not exist: {path}", type="warning")

        except Exception as e:
            logger.error(f"Error navigating to database path: {str(e)}")
            notify(f"Error navigating to path: {str(e)}", type="negative")

    def _update_database_file_listing(self, dialog_state):
        """Update the file listing in the database browser"""
        try:
            from pathlib import Path

            current_path = Path(dialog_state["current_path"])
            extension_filter = dialog_state["extension_filter"]

            # Clear existing file list
            dialog_state["file_list"].clear()

            # Add parent directory option (if not at root)
            if current_path.parent != current_path:
                with dialog_state["file_list"]:
                    with ui.row().classes(
                        "w-full items-center gap-2 p-2 hover-theme-surface rounded cursor-pointer"
                    ):
                        ui.icon("arrow_upward").classes("text-blue-400")
                        parent_label = ui.label(".. (Parent Directory)").classes(
                            "text-blue-400"
                        )
                        parent_label.on(
                            "click",
                            lambda: self._navigate_to_database_path(
                                dialog_state, current_path.parent
                            ),
                        )

            # List directories and files
            try:
                items = sorted(
                    current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
                )

                for item in items:
                    if item.name.startswith("."):
                        continue  # Skip hidden files

                    with dialog_state["file_list"]:
                        with ui.row().classes(
                            "w-full items-center gap-2 p-2 hover-theme-surface rounded cursor-pointer"
                        ) as row:
                            if item.is_dir():
                                ui.icon("folder").classes("text-yellow-400")
                                dir_label = ui.label(item.name).classes("")
                                row.on(
                                    "click",
                                    lambda path=item: self._navigate_to_database_path(
                                        dialog_state, path
                                    ),
                                )
                            else:
                                # Show different icons for different file types
                                if item.suffix.lower() == extension_filter:
                                    if extension_filter == ".db":
                                        ui.icon("storage").classes("text-green-400")
                                    else:  # .json
                                        ui.icon("code").classes("text-green-400")
                                    file_label = ui.label(item.name).classes(
                                        "text-green-400"
                                    )
                                else:
                                    ui.icon("description").classes("secondary-text")
                                    file_label = ui.label(item.name).classes(
                                        "secondary-text"
                                    )

                                # Make files selectable if they match our filter
                                if item.suffix.lower() == extension_filter:
                                    row.on(
                                        "click",
                                        lambda path=item: self._select_database_file_in_dialog(
                                            dialog_state, path
                                        ),
                                    )

            except PermissionError:
                with dialog_state["file_list"]:
                    ui.label(
                        "Permission denied - cannot access this directory"
                    ).classes("text-red-400 p-2")

        except Exception as e:
            logger.error(f"Error updating database file listing: {str(e)}")
            with dialog_state["file_list"]:
                ui.label(f"Error loading directory: {str(e)}").classes(
                    "text-red-400 p-2"
                )

    def _select_database_file_in_dialog(self, dialog_state, file_path):
        """Select a file in the database dialog"""
        try:
            dialog_state["selected_file"] = str(file_path)
            dialog_state["selected_label"].set_text(file_path.name)
            dialog_state["select_button"].enabled = True

            # Update the path input to show the full file path
            dialog_state["path_input"].value = str(file_path)

        except Exception as e:
            logger.error(f"Error selecting database file: {str(e)}")

    def _select_database_file_from_dialog(self, dialog_state, dialog):
        """Handle the final file selection from the database dialog"""
        try:
            from pathlib import Path

            selected_path = None

            # Check for manual path entry first
            manual_path = dialog_state["path_input"].value
            if manual_path and Path(manual_path).exists():
                if Path(manual_path).is_file():
                    selected_path = manual_path
                elif Path(manual_path).is_dir() and dialog_state["file_type"] == "sql":
                    # For SQL, allow selecting directory + filename
                    if "new_filename_input" in dialog_state:
                        filename = dialog_state["new_filename_input"].value
                        if filename:
                            selected_path = str(Path(manual_path) / filename)

            # Fall back to selected file
            if not selected_path and dialog_state["selected_file"]:
                selected_path = dialog_state["selected_file"]

            # For SQL databases, allow creating new files
            if (
                not selected_path
                and dialog_state["file_type"] == "sql"
                and "new_filename_input" in dialog_state
            ):
                filename = dialog_state["new_filename_input"].value
                if filename:
                    current_dir = Path(dialog_state["current_path"])
                    selected_path = str(current_dir / filename)

            if not selected_path:
                notify("Please select a file or enter a path", type="warning")
                return

            # Validate file extension
            extension_filter = dialog_state["extension_filter"]
            if not selected_path.lower().endswith(extension_filter):
                notify(f"Please select a {extension_filter} file", type="warning")
                return

            # Update the main input field
            field_key = dialog_state["field_key"]
            if field_key in self.ui_elements:
                self.ui_elements[field_key].value = selected_path

                # Trigger the change event
                event_obj = type("Event", (), {"value": selected_path})()
                if dialog_state["file_type"] == "firebase":
                    self.on_firebase_config_change(
                        "firebase_service_account_path", event_obj
                    )
                else:
                    self.on_field_change(
                        "database_settings", field_key.split(".")[1], event_obj
                    )

                logger.info(
                    f"User selected database file ({dialog_state['file_type']}): {selected_path}"
                )
                notify(f"Selected file: {Path(selected_path).name}", type="positive")

                dialog.close()
            else:
                notify("Error updating file path", type="negative")

        except Exception as e:
            logger.error(f"Error in database file selection: {str(e)}")
            notify(f"Error selecting file: {str(e)}", type="negative")

    def test_spotify_connection(self):
        """Handle the Test Connection button click for Spotify"""
        try:
            logger.info("User clicked Test Connection button for Spotify")

            # Prevent duplicate test attempts
            if (
                hasattr(self, "_spotify_test_in_progress")
                and self._spotify_test_in_progress
            ):
                logger.warning(
                    "Spotify test already in progress, ignoring duplicate request"
                )
                notify("Spotify test already in progress...", type="info")
                return

            # Mark test as in progress
            self._spotify_test_in_progress = True

            # Update button state to show it's working
            if "test_spotify_button" in self.ui_elements:
                self.ui_elements["test_spotify_button"].set_text("Testing...")
                self.ui_elements["test_spotify_button"].disable()

            # Show a notification that the process is starting
            notify("Testing Spotify connection...", type="info")

            # Save only Spotify settings to avoid triggering database and other notifications
            self._save_spotify_settings_only()

            # Create a unique result holder for this test
            import uuid

            test_id = str(uuid.uuid4())
            test_result = {
                "status": "testing",
                "success": None,
                "error": None,
                "id": test_id,
            }

            # Test the connection in a separate thread
            import threading

            def test_thread():
                try:
                    from .. import spotify

                    # Test and reconnect if possible
                    success = spotify.test_and_reconnect()
                    test_result["status"] = "complete"
                    test_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in Spotify test thread: {str(e)}", exc_info=True
                    )
                    test_result["status"] = "error"
                    test_result["error"] = str(e)

            # Start the thread
            test_worker = threading.Thread(target=test_thread)
            test_worker.daemon = True
            test_worker.start()

            # Use a single-shot approach with delayed execution
            def handle_test_completion():
                try:
                    # Wait for thread to complete (up to 5 seconds)
                    test_worker.join(timeout=5.0)

                    if test_result["status"] == "complete":
                        if test_result["success"]:
                            notify(
                                "Spotify connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info(
                                "Spotify connection test completed successfully"
                            )
                        else:
                            notify(
                                "Failed to connect to Spotify. You may need to re-authorize.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning(
                                "Spotify connection test completed with failure"
                            )

                        # Refresh the status display
                        self.refresh_spotify_status()

                    elif test_result["status"] == "error":
                        notify(
                            f"Error during Spotify test: {test_result['error']}",
                            type="negative",
                        )
                    else:
                        # Test didn't complete in time
                        notify("Spotify test timed out", type="negative")
                        logger.warning("Spotify test timed out")

                except Exception as e:
                    logger.error(f"Error handling test completion: {str(e)}")
                    notify("Error during Spotify test", type="negative")
                finally:
                    self._cleanup_spotify_test()

            # Schedule completion check after 5 seconds
            layout_schedule(5.0, handle_test_completion, once=True)

        except Exception as e:
            logger.error(f"Error handling Spotify test: {str(e)}", exc_info=True)
            notify(f"Error starting Spotify test: {str(e)}", type="negative")
            self._cleanup_spotify_test()

    def _cleanup_spotify_test(self):
        """Clean up after Spotify test"""
        try:
            # Mark test as no longer in progress
            self._spotify_test_in_progress = False

            # Reset button state
            if "test_spotify_button" in self.ui_elements:
                self.ui_elements["test_spotify_button"].set_text("Test")
                self.ui_elements["test_spotify_button"].enable()
        except Exception as e:
            logger.error(f"Error cleaning up Spotify test: {str(e)}")

    def _on_yt_playlist_input_enter(self, _event=None) -> None:
        """Handle Enter key in the YouTube playlist filter input."""
        if not hasattr(self, "_yt_playlist_input") or not self._yt_playlist_input:
            return
        name = (self._yt_playlist_input.value or "").strip()
        if not name:
            return
        self._yt_playlist_input.value = ""
        self._add_yt_playlist_filter(name)

    def _add_yt_playlist_filter(self, name: str) -> None:
        """Add a playlist name to the YouTube exclusion list."""
        if not self.youtube_data:
            return
        current_list = list(self.youtube_data.playlist_filter or [])
        if name in current_list:
            notify(f"'{name}' is already in the filter list", type="warning")
            return
        current_list.append(name)
        self.youtube_data.playlist_filter = current_list
        state_manager.update_youtube_field("playlist_filter", current_list)
        self._create_yt_playlist_chip(name)

    def _remove_yt_playlist_filter(self, name: str) -> None:
        """Remove a playlist name from the YouTube exclusion list."""
        if not self.youtube_data:
            return
        current_list = list(self.youtube_data.playlist_filter or [])
        if name in current_list:
            current_list.remove(name)
            self.youtube_data.playlist_filter = current_list
            state_manager.update_youtube_field("playlist_filter", current_list)
        self._rebuild_yt_playlist_chips()

    def _create_yt_playlist_chip(self, name: str) -> None:
        """Create a single removable chip for the YouTube playlist filter."""
        if not hasattr(self, "_yt_playlist_chip_container"):
            return
        with self._yt_playlist_chip_container:
            with (
                ui.element("div")
                .classes(
                    "flex items-center gap-1 px-3 py-1 rounded-full"
                    " bg-blue-500/20 border border-blue-500/40"
                )
                .style("flex-shrink: 0; white-space: nowrap;")
            ):
                ui.label(name).classes("text-sm").style("white-space: nowrap;")
                ui.button(
                    icon="close",
                    on_click=lambda _e, n=name: self._remove_yt_playlist_filter(n),
                ).props("flat dense round size=xs")

    def _rebuild_yt_playlist_chips(self) -> None:
        """Clear and recreate all YouTube playlist filter chips."""
        if not hasattr(self, "_yt_playlist_chip_container"):
            return
        self._yt_playlist_chip_container.clear()
        current_list = (
            list(self.youtube_data.playlist_filter)
            if self.youtube_data and self.youtube_data.playlist_filter
            else []
        )
        for name in current_list:
            self._create_yt_playlist_chip(name)

    def refresh_youtube_status(self):
        """Refresh the YouTube connection status display"""
        try:
            # Force reload the latest YouTube data from state manager
            self.youtube_data = state_manager.get_youtube_data()

            # Get current status from the YouTube module
            from .. import youtube

            status_info = youtube.get_youtube_status()

            # Also get the connection status directly from our data
            current_connection_status = (
                self.youtube_data.connection_status if self.youtube_data else "Unknown"
            )

            # Update status label and color
            if "youtube_status_label" in self.ui_elements:
                # Use the most current status from either source
                status_text = current_connection_status or status_info.get(
                    "status", "Unknown"
                )
                is_connected = status_info.get("is_connected", False)

                self.ui_elements["youtube_status_label"].set_text(status_text)

                # Update color based on status
                if is_connected or status_text == "Connected":
                    self.ui_elements["youtube_status_label"].classes(
                        replace="font-semibold text-green-500"
                    )
                else:
                    self.ui_elements["youtube_status_label"].classes(
                        replace="font-semibold text-red-500"
                    )

            # Update channel label
            if "youtube_channel_label" in self.ui_elements:
                if self.youtube_data and self.youtube_data.channels:
                    channel_count = len(self.youtube_data.channels)
                    latest_channel = (
                        self.youtube_data.latest_video_channel or "Multiple channels"
                    )
                    channel_info = (
                        f"{channel_count} channels - Latest: {latest_channel}"
                    )
                else:
                    channel_info = "No channels configured"
                self.ui_elements["youtube_channel_label"].set_text(channel_info)

            # Update latest video label
            if "youtube_video_label" in self.ui_elements:
                latest_video = status_info.get("latest_video", "N/A")
                if latest_video and latest_video != "No video found":
                    # Truncate long titles for display
                    if len(latest_video) > 50:
                        latest_video = latest_video[:47] + "..."
                self.ui_elements["youtube_video_label"].set_text(latest_video)

            logger.debug(
                f"YouTube status refreshed - DB status: {current_connection_status}, Module status: {status_info.get('status', 'Unknown')}"
            )

        except Exception as e:
            logger.error(f"Error refreshing YouTube status: {str(e)}", exc_info=True)
            # Set error status
            if "youtube_status_label" in self.ui_elements:
                self.ui_elements["youtube_status_label"].set_text("Status Error")
                self.ui_elements["youtube_status_label"].classes(
                    replace="font-semibold text-red-500"
                )

    def test_youtube_connection(self):
        """Handle the Test Connection button click for YouTube"""
        try:
            logger.info("User clicked Test Connection button for YouTube")

            # Prevent duplicate test attempts
            if (
                hasattr(self, "_youtube_test_in_progress")
                and self._youtube_test_in_progress
            ):
                logger.warning(
                    "YouTube test already in progress, ignoring duplicate request"
                )
                notify("YouTube test already in progress...", type="info")
                return

            # Mark test as in progress
            self._youtube_test_in_progress = True

            # Update button state to show it's working
            if "test_youtube_button" in self.ui_elements:
                self.ui_elements["test_youtube_button"].set_text("Testing...")
                self.ui_elements["test_youtube_button"].disable()

            # Show a notification that the process is starting
            notify("Testing YouTube connection...", type="info")

            # Save only YouTube settings to avoid triggering database and other notifications
            self._save_youtube_settings_only()

            # Force the YouTube client to reload data from the database
            try:
                from .. import youtube

                if youtube.youtube_client:
                    # Reload data from database
                    youtube.youtube_client.load_youtube_data()
                    # Also update the local reference to the current YouTube data
                    self.youtube_data = youtube.youtube_client.youtube_data
                    logger.info("YouTube client data reloaded after settings save")
                else:
                    # If no client exists, initialize it
                    youtube.initialize_youtube()
                    if youtube.youtube_client:
                        self.youtube_data = youtube.youtube_client.youtube_data
                        logger.info("YouTube client initialized and data loaded")
            except Exception as e:
                logger.warning(f"Could not reload YouTube client data: {str(e)}")

            # Create a unique result holder for this test
            import uuid

            test_id = str(uuid.uuid4())
            test_result = {
                "status": "testing",
                "success": None,
                "error": None,
                "id": test_id,
            }

            # Test the connection in a separate thread
            import threading

            def test_thread():
                try:
                    from .. import youtube

                    # Test and authenticate
                    success = youtube.trigger_authentication()
                    test_result["status"] = "complete"
                    test_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in YouTube test thread: {str(e)}", exc_info=True
                    )
                    test_result["status"] = "error"
                    test_result["error"] = str(e)

            # Start the thread
            test_worker = threading.Thread(target=test_thread)
            test_worker.daemon = True
            test_worker.start()

            # Use a single-shot approach with delayed execution
            def handle_test_completion():
                try:
                    # Wait for thread to complete (up to 5 seconds)
                    test_worker.join(timeout=5.0)

                    if test_result["status"] == "complete":
                        if test_result["success"]:
                            notify(
                                "YouTube connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info(
                                "YouTube connection test completed successfully"
                            )
                        else:
                            notify(
                                "Failed to connect to YouTube. Check your API key and channel URL.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning(
                                "YouTube connection test completed with failure"
                            )

                        # Refresh the status display
                        self.refresh_youtube_status()

                    elif test_result["status"] == "error":
                        notify(
                            f"Error during YouTube test: {test_result['error']}",
                            type="negative",
                        )
                    else:
                        # Test didn't complete in time
                        notify("YouTube test timed out", type="negative")
                        logger.warning("YouTube test timed out")

                except Exception as e:
                    logger.error(f"Error handling test completion: {str(e)}")
                    notify("Error during YouTube test", type="negative")
                finally:
                    self._cleanup_youtube_test()

            # Schedule completion check after 5 seconds
            layout_schedule(5.0, handle_test_completion, once=True)

        except Exception as e:
            logger.error(f"Error handling YouTube test: {str(e)}", exc_info=True)
            notify(f"Error starting YouTube test: {str(e)}", type="negative")
            self._cleanup_youtube_test()

    def _cleanup_youtube_test(self):
        """Clean up after YouTube test"""
        try:
            # Mark test as no longer in progress
            self._youtube_test_in_progress = False

            # Reset button state
            if "test_youtube_button" in self.ui_elements:
                self.ui_elements["test_youtube_button"].set_text("Test")
                self.ui_elements["test_youtube_button"].enable()
        except Exception as e:
            logger.error(f"Error cleaning up YouTube test: {str(e)}")

    def _save_youtube_settings_only(self):
        """Save only YouTube settings to database"""
        try:
            logger.info("Saving YouTube settings to database")

            # Save YouTube data
            if self.youtube_data:
                youtube_dict = {
                    field.name: getattr(self.youtube_data, field.name)
                    for field in YouTubeData.__dataclass_fields__.values()
                }
                state_manager.set_youtube_data(youtube_dict)

            # Save changes to database
            state_manager.save_changes()
            logger.debug("YouTube settings saved successfully")

        except Exception as e:
            logger.error(f"Error saving YouTube settings: {str(e)}", exc_info=True)

    def on_firebase_config_change(self, field, event):
        """Handle changes to the Firebase configuration"""
        logger.info(
            f"--- SettingsUI.on_firebase_config_change triggered for {field} with value {event.value} ---"
        )
        self.on_field_change("database_settings", field, event)

        # Update Firebase configuration validation
        self.update_firebase_config_status()

    def update_firebase_config_status(self):
        """Update the Firebase configuration validation status"""
        try:
            import json
            import os

            logger.debug("Updating Firebase configuration status")

            # Check if we have the required UI elements
            if "firebase_config_status" not in self.ui_elements:
                logger.warning("firebase_config_status UI element not found")
                return

            # Get current values
            service_account_path = self.database_settings.firebase_service_account_path
            database_url = self.database_settings.firebase_database_url

            logger.debug(
                f"Checking Firebase config - Path: {service_account_path}, URL: {database_url}"
            )

            # Check service account key file
            key_status = ""
            key_valid = False
            if not service_account_path:
                key_status = "❌ Path not specified"
            elif not os.path.exists(service_account_path):
                key_status = f"❌ File not found: {service_account_path}"
            else:
                try:
                    with open(service_account_path, "r") as f:
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
                            key_status = (
                                f"❌ Missing fields: {', '.join(missing_fields)}"
                            )
                        else:
                            key_status = "✅ Valid service account key"
                            key_valid = True
                except Exception as e:
                    key_status = f"❌ Invalid JSON: {str(e)}"

            # Check database URL (firebaseio.com or regional firebasedatabase.app)
            url_status = ""
            url_valid = False
            if not database_url:
                url_status = "❌ URL not specified"
            elif not is_valid_firebase_rtdb_url(database_url):
                url_status = "❌ Invalid URL format"
            else:
                url_status = "✅ Valid URL format"
                url_valid = True

            # Update UI elements
            if "firebase_key_status" in self.ui_elements:
                self.ui_elements["firebase_key_status"].set_text(key_status)
                if key_valid:
                    self.ui_elements["firebase_key_status"].classes(
                        replace="text-sm text-green-400"
                    )
                else:
                    self.ui_elements["firebase_key_status"].classes(
                        replace="text-sm text-red-400"
                    )

            if "firebase_url_status" in self.ui_elements:
                self.ui_elements["firebase_url_status"].set_text(url_status)
                if url_valid:
                    self.ui_elements["firebase_url_status"].classes(
                        replace="text-sm text-green-400"
                    )
                else:
                    self.ui_elements["firebase_url_status"].classes(
                        replace="text-sm text-red-400"
                    )

            # Update overall status
            if "firebase_config_status" in self.ui_elements:
                if key_valid and url_valid:
                    self.ui_elements["firebase_config_status"].set_text(
                        "✅ Ready to connect"
                    )
                    self.ui_elements["firebase_config_status"].classes(
                        replace="font-semibold text-green-400"
                    )
                else:
                    self.ui_elements["firebase_config_status"].set_text(
                        "❌ Configuration incomplete"
                    )
                    self.ui_elements["firebase_config_status"].classes(
                        replace="font-semibold text-red-400"
                    )

        except Exception as e:
            logger.error(
                f"Error updating Firebase config status: {str(e)}", exc_info=True
            )
            if "firebase_config_status" in self.ui_elements:
                self.ui_elements["firebase_config_status"].set_text(
                    "❌ Status check error"
                )
                self.ui_elements["firebase_config_status"].classes(
                    replace="font-semibold text-red-400"
                )

    def cleanup_timers(self):
        """Clean up all active timers to prevent memory leaks and CPU usage"""
        try:
            cleaned_count = 0

            # Clean up timers from the list
            for timer in self._active_timers:
                try:
                    if hasattr(timer, "cancel"):
                        timer.cancel()
                        cleaned_count += 1
                    elif hasattr(timer, "stop"):
                        timer.stop()
                        cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Error cleaning up individual timer: {e}")

            self._active_timers.clear()

            # Also clean up any timers in the active_timers dict
            for timer_name, timer in self.active_timers.items():
                try:
                    if hasattr(timer, "cancel"):
                        timer.cancel()
                        cleaned_count += 1
                    elif hasattr(timer, "stop"):
                        timer.stop()
                        cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Error cleaning up timer {timer_name}: {e}")

            self.active_timers.clear()

            if cleaned_count > 0:
                logger.debug(f"Cleaned up {cleaned_count} active timers")

        except Exception as e:
            logger.error(f"Error during timer cleanup: {e}", exc_info=True)

    def _save_spotify_settings_only(self):
        """Save only Spotify settings to avoid triggering database and other notifications"""
        try:
            updated_fields = []

            # Update only Spotify data fields
            if hasattr(self, "spotify_data") and self.spotify_data:
                for field in self.spotify_data.__dataclass_fields__:
                    field_key = f"spotify_data.{field}"
                    if field_key in self.ui_elements and hasattr(
                        self.ui_elements[field_key], "value"
                    ):
                        value = self.ui_elements[field_key].value
                        old_value = getattr(self.spotify_data, field)
                        if old_value != value:
                            state_manager.update_spotify_field(field, value)
                            updated_fields.append(field_key)

            # Save only if there are Spotify changes
            if updated_fields:
                if state_manager.save_changes():
                    logger.info(
                        f"Spotify settings saved successfully. Updated fields: {updated_fields}"
                    )
                else:
                    logger.error("Failed to save Spotify settings")
            else:
                logger.debug("No Spotify settings changes to save")

        except Exception as e:
            logger.error(f"Error saving Spotify settings: {str(e)}", exc_info=True)

    def build_ui_v2(self):
        """Build the modular settings UI with per-tab components and per-tab Save/Discard.

        This implementation uses independent tab classes with local buffers and a
        tab-switch guard for unsaved changes. Statistics and About remain unchanged.
        """
        logger.info("--- SettingsUI.build_ui_v2() CALLED ---")

        # Ensure state is initialized for About info
        with StartupTimer("settings_state_init"):
            if (
                not hasattr(state_manager, "_initialized")
                or not state_manager._initialized
            ):
                state_manager.initialize()
            self.app_settings = state_manager.get_app_settings()

        # Prepare tab components
        with StartupTimer("settings_tab_objects"):
            # Time each tab object creation individually
            with StartupTimer("settings_tab_objects_app_settings"):
                app_settings_tab = AppSettingsTab()
            with StartupTimer("settings_tab_objects_theme"):
                theme_tab = ThemeTab()
            with StartupTimer("settings_tab_objects_twitch"):
                twitch_tab = TwitchTab()
            with StartupTimer("settings_tab_objects_psn"):
                psn_tab = PSNTab()
            with StartupTimer("settings_tab_objects_spotify"):
                spotify_tab = SpotifyTab()
            with StartupTimer("settings_tab_objects_youtube"):
                youtube_tab = YouTubeTab()
            with StartupTimer("settings_tab_objects_obs"):
                obs_tab = ObsTab()
            with StartupTimer("settings_tab_objects_game_hooks"):
                game_hooks_tab = GameHooksTab()
            with StartupTimer("settings_tab_objects_database"):
                database_tab = DatabaseTab()
            with StartupTimer("settings_tab_objects_statistics"):
                statistics_tab = StatisticsTab()

            self._tabs_by_name = {
                "App Settings": app_settings_tab,
                "Theme": theme_tab,
                "Twitch": twitch_tab,
                "PSN": psn_tab,
                "Spotify": spotify_tab,
                "YouTube": youtube_tab,
                "OBS": obs_tab,
                "Game Hooks": game_hooks_tab,
                "Database": database_tab,
                "Statistics": statistics_tab,
            }

        # Add the custom CSS
        with StartupTimer("settings_css"):
            ui.add_head_html(f"<style>{CSS}</style>")

        # Match Alerts/Chatbot layout: tab-surface padding outside the connected sub-tab shell
        with StartupTimer("settings_ui_structure"):
            with ui.element("div").classes(
                "tab-surface w-full flex-1 min-h-0 flex flex-col p-4"
            ):
                with ui.element("div").classes(
                    "mycelian-sub-tab-shell w-full flex-1 min-h-0 flex flex-col"
                ):
                    with ui.tabs().classes("w-full settings-tabs mycelian-sub-tabs") as tabs:
                        ui.tab("Twitch", icon=service_tab_icon("twitch"))
                        ui.tab("OBS", icon=service_tab_icon("obs"))
                        ui.tab("PSN", icon=service_tab_icon("psn"))
                        ui.tab("Spotify", icon=service_tab_icon("spotify"))
                        ui.tab("YouTube", icon=service_tab_icon("youtube"))
                        ui.tab("Game Hooks", icon="memory")
                        ui.tab("Database", icon="storage")
                        ui.tab("Statistics", icon="analytics")
                        ui.tab("Theme", icon="palette")
                        ui.tab("App Settings", icon="tune")
                        ui.tab("About", icon="info")

                    # Panels for each tab
                    with StartupTimer("settings_tab_panels"):
                        with StartupTimer("settings_create_tab_panels_container"):
                            tab_panels_container = ui.tab_panels(
                                tabs, value=self._active_tab_name
                            ).classes("w-full flex-1 min-h-0 grow").props(
                                "animated=false"
                            )

                            # Set references for help system context detection
                            from ..help_system.contextual_help import (
                                set_settings_ui_references,
                            )

                            set_settings_ui_references(tabs, tab_panels_container)

                        # Initialize lazy loading state
                        self._settings_loaded_tabs = set()

                        def load_tab_content(tab_name):
                            """Load content for a specific tab"""
                            from ..ui_tab_transitions import _tab_label

                            tab_name = _tab_label(tab_name)
                            if not tab_name or tab_name in self._settings_loaded_tabs:
                                return  # Already loaded or unknown

                            container = self._settings_tab_containers.get(tab_name)
                            if not container:
                                return

                            with StartupTimer(
                                f"settings_{tab_name.lower().replace(' ', '_')}_tab"
                            ):
                                try:
                                    # Safely remove loading elements before adding content
                                    try:
                                        if hasattr(container, "_loading_spinner"):
                                            container.remove(container._loading_spinner)
                                        if hasattr(container, "_loading_label"):
                                            container.remove(container._loading_label)
                                    except (ValueError, Exception):
                                        pass  # Already removed or not in children

                                    # Load actual content (tab .build() enters parent_container)
                                    if tab_name == "App Settings":
                                        self._tabs_by_name[
                                            "App Settings"
                                        ].build(container)
                                    elif tab_name == "Theme":
                                        self._tabs_by_name["Theme"].build(container)
                                    elif tab_name == "Twitch":
                                        self._tabs_by_name["Twitch"].build(container)
                                    elif tab_name == "PSN":
                                        self._tabs_by_name["PSN"].build(container)
                                    elif tab_name == "Spotify":
                                        self._tabs_by_name["Spotify"].build(container)
                                    elif tab_name == "YouTube":
                                        self._tabs_by_name["YouTube"].build(container)
                                    elif tab_name == "OBS":
                                        self._tabs_by_name["OBS"].build(container)
                                    elif tab_name == "Game Hooks":
                                        self._tabs_by_name["Game Hooks"].build(
                                            container
                                        )
                                    elif tab_name == "Database":
                                        self._tabs_by_name["Database"].build(
                                            container
                                        )
                                    elif tab_name == "Statistics":
                                        self._tabs_by_name["Statistics"].build(
                                            container
                                        )
                                    elif tab_name == "About":
                                        self._build_about_tab(container)

                                    self._settings_loaded_tabs.add(tab_name)
                                except Exception as e:
                                    logger.error(
                                        f"Error loading settings tab {tab_name}: {e}",
                                        exc_info=True,
                                    )
                                    with container:
                                        ui.label(
                                            f"Failed to load {tab_name}: {e}"
                                        ).classes("text-red-400 text-center p-4")

                        # Create tab panels with lazy loading
                        self._settings_tab_containers = {}

                        # Panel DOM order matches tab strip (for correct slide direction)
                        settings_panel_order = [
                            "Twitch",
                            "OBS",
                            "PSN",
                            "Spotify",
                            "YouTube",
                            "Game Hooks",
                            "Database",
                            "Statistics",
                            "Theme",
                            "App Settings",
                            "About",
                        ]

                        _TAB_PANEL_CLASSES = (
                            "tab-content w-full h-full min-h-0 flex flex-col"
                        )

                        with tab_panels_container:
                            for tab_name in settings_panel_order:
                                with ui.tab_panel(tab_name).classes(_TAB_PANEL_CLASSES):
                                    with ui.scroll_area().classes("w-full h-full grow"):
                                        container = ui.column().classes(
                                            "w-full gap-4"
                                        )
                                        self._settings_tab_containers[tab_name] = (
                                            container
                                        )
                                        _eager_tabs = {
                                            "App Settings",
                                            "Statistics",
                                            "About",
                                        }
                                        if tab_name in _eager_tabs:
                                            with StartupTimer(
                                                f"settings_{tab_name.lower().replace(' ', '_')}_tab"
                                            ):
                                                if tab_name == "About":
                                                    self._build_about_tab(container)
                                                else:
                                                    self._tabs_by_name[
                                                        tab_name
                                                    ].build(container)
                                            self._settings_loaded_tabs.add(tab_name)
                                        else:
                                            spinner = (
                                                ui.spinner("dots")
                                                .classes("mx-auto")
                                                .props("size=2rem")
                                            )
                                            label = ui.label("Loading...").classes(
                                                "text-center muted-text"
                                            )
                                            container._loading_spinner = spinner
                                            container._loading_label = label

                        from ..ui_tab_transitions import _tab_label

                        def _settings_tab_from_event(e) -> str:
                            val = getattr(e, "value", None)
                            if val is None:
                                args = getattr(e, "args", None)
                                if isinstance(args, dict) and "value" in args:
                                    val = args["value"]
                            return _tab_label(val)

                        def on_settings_tab_change(e):
                            new_tab = _settings_tab_from_event(e)
                            if (
                                new_tab in self._settings_tab_containers
                                and new_tab not in self._settings_loaded_tabs
                            ):
                                load_tab_content(new_tab)

                        tabs.on("change", on_settings_tab_change)

                        # Timer fallback when tabs.on("change") does not fire (native mode)
                        previous_settings_tab = _tab_label(tabs.value)

                        def check_settings_tab_changes():
                            nonlocal previous_settings_tab

                            current_tab = _tab_label(tabs.value)
                            if current_tab != previous_settings_tab:
                                if (
                                    current_tab in self._settings_tab_containers
                                    and current_tab not in self._settings_loaded_tabs
                                ):
                                    load_tab_content(current_tab)
                                self._active_tab_name = current_tab
                                previous_settings_tab = current_tab

                        # Check for tab changes every 200ms
                        layout_schedule(0.2, check_settings_tab_changes, active=True)

                        # Lazy-loaded default tab never fires a change event on first open
                        initial_settings_tab = (
                            _tab_label(tabs.value) or self._active_tab_name
                        )
                        if (
                            initial_settings_tab
                            and initial_settings_tab not in self._settings_loaded_tabs
                        ):
                            layout_schedule(
                                0.05,
                                lambda t=initial_settings_tab: load_tab_content(t),
                                once=True,
                            )

                # Unsaved-changes guard
                from ..ui_tab_transitions import _tab_label

                def on_tab_change(e):
                    new_name = _tab_label(getattr(e, "value", None))
                    prev_name = _tab_label(self._active_tab_name)
                    if not new_name or new_name == prev_name:
                        return
                    current_tab = self._tabs_by_name.get(prev_name)
                    if current_tab and getattr(current_tab, "dirty", False):
                        self._show_unsaved_changes_dialog(tabs, prev_name, new_name)
                        tabs.value = prev_name
                        return
                    if current_tab:
                        current_tab.on_exit()
                    next_tab = self._tabs_by_name.get(new_name)
                    if next_tab:
                        next_tab.on_enter()
                    self._active_tab_name = new_name

                # Monitor sub-tab changes using a timer since tabs.on("change") may not work in native mode
                previous_subtab = _tab_label(tabs.value)

                def check_subtab_changes():
                    nonlocal previous_subtab
                    current_subtab = _tab_label(tabs.value)
                    if current_subtab != previous_subtab:
                        mock_event = type("MockEvent", (), {"value": current_subtab})()
                        on_tab_change(mock_event)
                        previous_subtab = current_subtab

                layout_schedule(0.5, check_subtab_changes, active=True)  # Check every 500ms

        ui.run_javascript(
            "window.mycelianInitSubTabSeams && window.mycelianInitSubTabSeams()"
        )

        # Log timing summary
        # log_startup_summary()

    def _build_about_tab(self, container):
        """Build the About tab content"""
        with settings_surface(container):
            with settings_header("Mycelian"):
                ui.button(
                    "Check for Updates",
                    on_click=self.check_for_updates_manual,
                ).props("icon=system_update color=primary dense")
                outline_button(
                    "View on GitHub",
                    lambda: webbrowser.open(
                        "https://github.com/mushroomsuprise/mycelian"
                    ),
                    icon="code",
                    extra_classes="dense",
                )
                outline_button(
                    "View Changelog",
                    self.show_changelog_modal,
                    icon="history",
                    extra_classes="dense",
                )
            with ui.row().classes("gap-4 flex-wrap"):
                ui.label(f"Version {self.app_settings.version}").classes(
                    "secondary-text text-sm"
                )
                ui.label(
                    f"Build Number {resolve_build_number(getattr(self.app_settings, 'build_number', 'dev'))}"
                ).classes("secondary-text text-sm")
                ui.label(f"Built on {self.app_settings.build_date}").classes(
                    "secondary-text text-sm"
                )

        with settings_surface(container):
            with ui.row().classes("w-full items-start justify-between gap-3"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label("Application Logs").classes("text-base font-semibold")
                    ui.label(
                        "Actionable errors from the application log file, "
                        "excluding known noise such as expired tokens."
                    ).classes("secondary-text text-sm")
                with ui.row().classes("items-center gap-2 shrink-0"):
                    outline_button(
                        "Open Logs Folder",
                        self.open_logs_folder,
                        icon="folder_open",
                        extra_classes="dense",
                    )
                    ui.button("Refresh", on_click=self.refresh_log_errors).props(
                        "icon=refresh outline dense"
                    )
            self.ui_elements["log_error_count_label"] = ui.label(
                "No actionable errors"
            ).classes("secondary-text text-sm")
            self.ui_elements["log_errors_list_container"] = ui.column().classes(
                "w-full gap-1 mt-1"
            )
            layout_schedule(0.1, lambda: self.refresh_log_errors(), once=True)

        with settings_surface(container):
            with ui.row().classes("w-full items-start justify-between gap-3"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label("Available Source URLs").classes(
                        "text-base font-semibold"
                    )
                    ui.label(
                        "Copy these URLs to use as Browser Sources in OBS "
                        "or other streaming software."
                    ).classes("secondary-text text-sm")
                ui.button("Refresh URLs", on_click=self.refresh_source_urls).props(
                    "icon=refresh outline dense"
                )
            self.ui_elements["source_urls_container"] = ui.column().classes(
                "w-full gap-2 mt-2"
            )
            layout_schedule(0.1, lambda: self.refresh_source_urls(), once=True)

    def _show_unsaved_changes_dialog(
        self, tabs_component, prev_name: str, next_name: str
    ) -> None:
        """Prompt whether to discard or stay when leaving a dirty tab."""
        with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
            ui.label("Unsaved changes").classes("text-lg font-bold mb-2")
            ui.label(
                "You have unsaved changes on this tab. Do you want to discard them and switch tabs?"
            ).classes("secondary-text mb-4")

            def confirm_switch():
                try:
                    current_tab = self._tabs_by_name.get(prev_name)
                    if current_tab:
                        current_tab.discard()
                        current_tab.on_exit()
                    next_tab = self._tabs_by_name.get(next_name)
                    if next_tab:
                        next_tab.on_enter()
                    tabs_component.value = next_name
                    self._active_tab_name = next_name
                finally:
                    dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Stay", on_click=dialog.close).props("outline")
                ui.button("Discard and switch", on_click=confirm_switch).props(
                    "color=primary"
                )

            dialog.open()  # Explicitly open the dialog

    def has_unsaved_changes(self) -> bool:
        """Check if any settings tab has unsaved changes."""
        if not hasattr(self, "_tabs_by_name") or self._tabs_by_name is None:
            return False
        return any(
            getattr(tab, "dirty", False)
            for tab in self._tabs_by_name.values()
            if tab is not None
        )


# Create a singleton instance
settings_ui = SettingsUI()


def create_settings_tab():
    """Create and return the settings tab content"""
    # Use the modular per-tab UI build
    return settings_ui.build_ui_v2()

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from nicegui import ui

from ...ui_buttons import primary_button
from ...ui_settings_layout import (
    settings_action_row,
    settings_form_grid,
    settings_inner_panel,
    settings_surface,
)
from ...notification_engine import notify

from ... import dataobjects
from ...dataobjects import state_manager
from ...path_utils import get_data_path


class AppSettingsTab:
    name = "App Settings"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.AppSettings] = None
        self.ui_elements: Dict[str, Any] = {}

    # ----- Stream Deck Plugin helpers -----
    def _get_streamdeck_plugins_dir(self) -> Optional[Path]:
        """Get the OS-specific Stream Deck plugins directory."""
        import platform

        system = platform.system()

        if system == "Darwin":  # macOS
            plugins_dir = (
                Path.home()
                / "Library"
                / "Application Support"
                / "com.elgato.StreamDeck"
                / "Plugins"
            )
        elif system == "Windows":
            appdata = os.path.expandvars("%appdata%")
            plugins_dir = Path(appdata) / "Elgato" / "StreamDeck" / "Plugins"
        else:
            return None

        return plugins_dir if plugins_dir.exists() else None

    def _check_plugin_installed(self) -> bool:
        """Check if the Stream Deck plugin is currently installed."""
        plugins_dir = self._get_streamdeck_plugins_dir()
        if not plugins_dir:
            return False

        plugin_dir = plugins_dir / "com.mushroomsuprise.mycelian.sdPlugin"
        if not plugin_dir.exists():
            return False

        # Verify manifest.json exists
        manifest = plugin_dir / "manifest.json"
        if not manifest.exists():
            return False

        return True

    def _get_plugin_status_text(self) -> str:
        """Get the plugin installation status text."""
        return "Installed" if self._check_plugin_installed() else "Not Installed"

    def _install_streamdeck_plugin(self) -> None:
        """Install the Stream Deck plugin by copying files to the plugins directory."""
        try:
            # Get source directory
            source_dir = (
                Path(get_data_path("sd_plugin"))
                / "com.mushroomsuprise.mycelian.sdPlugin"
            )

            if not source_dir.exists():
                notify(
                    "Plugin source files not found. Please ensure sd_plugin directory exists.",
                    type="negative",
                )
                return

            # Get destination directory
            plugins_dir = self._get_streamdeck_plugins_dir()
            if not plugins_dir:
                notify(
                    "Stream Deck plugins directory not found. Please ensure Stream Deck is installed.",
                    type="negative",
                )
                return

            # Ensure plugins directory exists
            plugins_dir.mkdir(parents=True, exist_ok=True)

            destination_dir = plugins_dir / "com.mushroomsuprise.mycelian.sdPlugin"

            # Remove existing plugin if present
            if destination_dir.exists():
                shutil.rmtree(destination_dir)

            # Copy plugin files
            shutil.copytree(source_dir, destination_dir)

            # Verify installation
            if self._check_plugin_installed():
                notify(
                    "Plugin installed successfully! Please restart Stream Deck for changes to take effect.",
                    type="positive",
                    timeout=5000,
                )
                # Update status label
                if "plugin_status_label" in self.ui_elements:
                    self.ui_elements["plugin_status_label"].set_text(
                        self._get_plugin_status_text()
                    )
            else:
                notify(
                    "Plugin installation verification failed. Please try again.",
                    type="negative",
                )

        except PermissionError:
            notify(
                "Permission denied. Please ensure you have write access to the Stream Deck plugins directory.",
                type="negative",
            )
        except Exception as e:
            notify(f"Error installing plugin: {str(e)}", type="negative")

    # ----- lifecycle -----
    def on_enter(self) -> None:
        pass

    def on_exit(self) -> None:
        pass

    # ----- building -----
    def build(self, parent_container) -> None:
        self._load_from_state()

        with settings_surface(parent_container):
            ui.label("Application Settings").classes("text-lg font-bold")

            with ui.column().classes("w-full gap-3"):
                with settings_inner_panel():
                    ui.label("General").classes("text-base font-semibold")
                    with settings_form_grid(columns=2):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Notifications").classes("text-sm")
                            self.ui_elements["notifications_enabled"] = (
                                ui.switch(value=self.buffer.notifications_enabled)
                                .classes("q-switch")
                                .on_value_change(
                                    lambda e: self._set(
                                        "notifications_enabled", bool(e.value)
                                    )
                                )
                            )
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Auto update").classes("text-sm")
                            self.ui_elements["auto_update"] = (
                                ui.switch(value=self.buffer.auto_update)
                                .classes("q-switch")
                                .on_value_change(
                                    lambda e: self._set("auto_update", bool(e.value))
                                )
                            )
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Start maximized").classes("text-sm")
                            self.ui_elements["start_maximized"] = (
                                ui.switch(value=self.buffer.start_maximized)
                                .classes("q-switch")
                                .on_value_change(
                                    lambda e: self._set(
                                        "start_maximized", bool(e.value)
                                    )
                                )
                            )
                        ui.label("Applies on next launch").classes(
                            "secondary-text text-sm self-center"
                        )
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Update check").classes("text-sm shrink-0")
                            self.ui_elements["update_check_interval_minutes"] = (
                                ui.number(
                                    value=getattr(
                                        self.buffer,
                                        "update_check_interval_minutes",
                                        30,
                                    ),
                                    min=5,
                                    max=120,
                                    step=5,
                                )
                                .classes("w-24")
                                .on(
                                    "change",
                                    lambda e: self._set(
                                        "update_check_interval_minutes",
                                        int(
                                            getattr(
                                                e, "args", [getattr(e, "value", 30)]
                                            )[0]
                                            or 30
                                        ),
                                    ),
                                )
                            )
                            ui.label("minutes (5–120)").classes(
                                "secondary-text text-sm"
                            )

                with settings_inner_panel():
                    ui.label("Activity feed").classes("text-base font-semibold")
                    with settings_form_grid(columns=2):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("History limit").classes("text-sm shrink-0")
                            self.ui_elements["activity_feed_limit"] = (
                                ui.number(
                                    value=self.buffer.activity_feed_limit,
                                    min=5,
                                    max=100,
                                    step=5,
                                )
                                .classes("w-24")
                                .on(
                                    "change",
                                    lambda e: self._set(
                                        "activity_feed_limit",
                                        int(
                                            getattr(
                                                e, "args", [getattr(e, "value", 5)]
                                            )[0]
                                            or 5
                                        ),
                                    ),
                                )
                            )
                            ui.label("per page").classes("secondary-text text-sm")
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Max pages").classes("text-sm shrink-0")
                            self.ui_elements["activity_feed_max_pages"] = (
                                ui.number(
                                    value=self.buffer.activity_feed_max_pages,
                                    min=1,
                                    max=50,
                                    step=1,
                                )
                                .classes("w-24")
                                .on(
                                    "change",
                                    lambda e: self._set(
                                        "activity_feed_max_pages",
                                        int(
                                            getattr(
                                                e, "args", [getattr(e, "value", 1)]
                                            )[0]
                                            or 1
                                        ),
                                    ),
                                )
                            )
                            ui.label("to load").classes("secondary-text text-sm")

                with settings_inner_panel():
                    ui.label("Stream Deck plugin").classes("text-base font-semibold")
                    with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Status").classes("text-sm secondary-text")
                            self.ui_elements["plugin_status_label"] = ui.label(
                                self._get_plugin_status_text()
                            ).classes("font-semibold text-sm")
                        self.ui_elements["install_plugin_button"] = primary_button(
                            "Install Plugin",
                            self._install_streamdeck_plugin,
                        )

            settings_action_row(discard=self.discard, save=self.save)

    # ----- buffer helpers -----
    def _load_from_state(self) -> None:
        app_settings = state_manager.get_app_settings()
        # create a detached copy for buffering
        self.buffer = dataobjects.AppSettings(
            **{
                k: getattr(app_settings, k)
                for k in app_settings.__dataclass_fields__.keys()
            }
        )
        self.dirty = False

    def _set(self, field: str, value) -> None:
        current_value = getattr(self.buffer, field)
        if current_value != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    # ----- actions -----
    def save(self) -> None:
        if not self.buffer:
            return
        # persist buffered values to state_manager (skip hardcoded metadata fields)
        _skip_save = frozenset({"version", "build_date"})
        for field in self.buffer.__dataclass_fields__.keys():
            if field in _skip_save:
                continue
            state_manager.update_app_setting(field, getattr(self.buffer, field))
        if state_manager.save_changes():
            notify("Settings saved", type="positive")
            self.dirty = False
        else:
            notify("Error saving settings", type="negative")

    def discard(self) -> None:
        # reload from state and update UI controls
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key)
        self.dirty = False

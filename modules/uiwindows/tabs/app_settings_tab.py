from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from nicegui import ui

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
                ui.notify(
                    "Plugin source files not found. Please ensure sd_plugin directory exists.",
                    type="negative",
                )
                return

            # Get destination directory
            plugins_dir = self._get_streamdeck_plugins_dir()
            if not plugins_dir:
                ui.notify(
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
                ui.notify(
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
                ui.notify(
                    "Plugin installation verification failed. Please try again.",
                    type="negative",
                )

        except PermissionError:
            ui.notify(
                "Permission denied. Please ensure you have write access to the Stream Deck plugins directory.",
                type="negative",
            )
        except Exception as e:
            ui.notify(f"Error installing plugin: {str(e)}", type="negative")

    # ----- lifecycle -----
    def on_enter(self) -> None:
        pass

    def on_exit(self) -> None:
        pass

    # ----- building -----
    def build(self, parent_container) -> None:
        print("DEBUG: AppSettingsTab.build() called")
        self._load_from_state()

        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("Application Settings").classes("text-xl font-bold mb-4")

                with ui.column().classes("w-full gap-4"):
                    # Notifications
                    with ui.row().classes("w-full items-center"):
                        ui.label("Notifications:").classes("w-40")
                        self.ui_elements["notifications_enabled"] = (
                            ui.switch(value=self.buffer.notifications_enabled)
                            .classes("q-switch")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "notifications_enabled",
                                    bool(
                                        getattr(
                                            e, "args", [getattr(e, "value", False)]
                                        )[0]
                                    ),
                                ),
                            )
                        )

                    # Auto Update
                    with ui.row().classes("w-full items-center"):
                        ui.label("Auto Update:").classes("w-40")
                        self.ui_elements["auto_update"] = (
                            ui.switch(value=self.buffer.auto_update)
                            .classes("q-switch")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "auto_update",
                                    bool(
                                        getattr(
                                            e, "args", [getattr(e, "value", False)]
                                        )[0]
                                    ),
                                ),
                            )
                        )

                    # Update interval
                    with ui.row().classes("w-full items-center"):
                        ui.label("Update Check Interval:").classes("w-40")
                        self.ui_elements["update_check_interval_minutes"] = (
                            ui.number(
                                value=getattr(
                                    self.buffer, "update_check_interval_minutes", 30
                                ),
                                min=5,
                                max=120,
                                step=5,
                            )
                            .classes("w-28")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "update_check_interval_minutes",
                                    int(
                                        getattr(e, "args", [getattr(e, "value", 30)])[0]
                                        or 30
                                    ),
                                ),
                            )
                        )
                        ui.label("minutes (5 - 120)").classes("ml-2 secondary-text")

                    ui.separator().classes("divider")

                    ui.label("Activity Feed Settings").classes("text-lg font-semibold")
                    with ui.row().classes("w-full items-center"):
                        ui.label("History Limit:").classes("w-40")
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
                                        getattr(e, "args", [getattr(e, "value", 5)])[0]
                                        or 5
                                    ),
                                ),
                            )
                        )
                        ui.label("alerts per page").classes("ml-2 secondary-text")

                    with ui.row().classes("w-full items-center"):
                        ui.label("Max Pages:").classes("w-40")
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
                                        getattr(e, "args", [getattr(e, "value", 1)])[0]
                                        or 1
                                    ),
                                ),
                            )
                        )
                        ui.label("maximum pages to load").classes("ml-2 secondary-text")

                    ui.separator().classes("divider")

                    ui.label("Stream Deck Plugin").classes("text-lg font-semibold")

                    # Status indicator
                    with ui.row().classes("w-full items-center"):
                        ui.label("Plugin Status:").classes("w-40")
                        self.ui_elements["plugin_status_label"] = ui.label(
                            self._get_plugin_status_text()
                        ).classes("font-bold")

                    # Install button
                    with ui.row().classes("w-full items-center"):
                        ui.label("").classes("w-40")  # Spacer for alignment
                        self.ui_elements["install_plugin_button"] = ui.button(
                            "Install Plugin", on_click=self._install_streamdeck_plugin
                        ).props("color=primary")

                    ui.separator().classes("divider")

                    with ui.row().classes("justify-end gap-2 mt-3"):
                        ui.button("Discard", on_click=self.discard).props("outline")
                        ui.button("Save", on_click=self.save).props("color=primary")

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
        # persist buffered values to state_manager
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_app_setting(field, getattr(self.buffer, field))
        if state_manager.save_changes():
            from nicegui import ui as _ui

            _ui.notify("Settings saved", type="positive")
            self.dirty = False
        else:
            ui.notify("Error saving settings", type="negative")

    def discard(self) -> None:
        # reload from state and update UI controls
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key)
        self.dirty = False

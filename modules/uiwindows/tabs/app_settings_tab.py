from __future__ import annotations

import shutil
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
from ...streamdeck_plugin_utils import (
    PluginInstallState,
    get_bundled_plugin_dir,
    get_install_button_label,
    get_installed_plugin_dir,
    get_plugin_status,
    get_plugin_status_display,
    get_streamdeck_plugins_dir,
    maybe_notify_streamdeck_plugin_outdated,
)

from ... import dataobjects
from ...dataobjects import state_manager


class AppSettingsTab:
    name = "App Settings"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.AppSettings] = None
        self.ui_elements: Dict[str, Any] = {}

    def _apply_plugin_status_display(self, status) -> None:
        display = get_plugin_status_display(status)

        primary = self.ui_elements.get("plugin_status_primary")
        if primary is not None:
            primary.set_text(display.status_text)

        version_label = self.ui_elements.get("plugin_installed_version")
        if version_label is not None:
            if display.installed_version:
                version_label.set_text(f"Installed version: v{display.installed_version}")
                version_label.visible = True
            else:
                version_label.visible = False

        update_label = self.ui_elements.get("plugin_new_version")
        if update_label is not None:
            if display.new_version_available:
                update_label.set_text(
                    f"New version available: v{display.new_version_available}"
                )
                update_label.visible = True
            else:
                update_label.visible = False

    def _refresh_plugin_ui(self, *, notify_if_outdated: bool = False) -> None:
        status = get_plugin_status()
        if notify_if_outdated:
            maybe_notify_streamdeck_plugin_outdated()

        self._apply_plugin_status_display(status)

        button = self.ui_elements.get("install_plugin_button")
        if button is not None:
            button.set_text(get_install_button_label(status))

    def _install_streamdeck_plugin(self) -> None:
        """Install or reinstall the Stream Deck plugin from the bundled copy."""
        was_installed = get_plugin_status().is_installed
        try:
            source_dir = get_bundled_plugin_dir()
            if not source_dir.exists():
                notify(
                    "Plugin source files not found. Please ensure sd_plugin directory exists.",
                    type="negative",
                )
                return

            plugins_dir = get_streamdeck_plugins_dir()
            if not plugins_dir:
                notify(
                    "Stream Deck plugins directory not found. Please ensure Stream Deck is installed.",
                    type="negative",
                )
                return

            plugins_dir.mkdir(parents=True, exist_ok=True)
            destination_dir = get_installed_plugin_dir() or (
                plugins_dir / source_dir.name
            )

            if destination_dir.exists():
                shutil.rmtree(destination_dir)

            shutil.copytree(source_dir, destination_dir)

            status = get_plugin_status()
            if status.state in (
                PluginInstallState.UP_TO_DATE,
                PluginInstallState.UNKNOWN,
            ):
                verb = "reinstalled" if was_installed else "installed"
                notify(
                    f"Plugin {verb} successfully! Please restart Stream Deck for changes to take effect.",
                    type="positive",
                    timeout=5000,
                )
                self._refresh_plugin_ui()
            else:
                notify(
                    "Plugin installation verification failed. Please try again.",
                    type="negative",
                )
                self._refresh_plugin_ui()

        except PermissionError:
            notify(
                "Permission denied. Please ensure you have write access to the Stream Deck plugins directory.",
                type="negative",
            )
        except Exception as e:
            notify(f"Error installing plugin: {str(e)}", type="negative")

    # ----- lifecycle -----
    def on_enter(self) -> None:
        self._refresh_plugin_ui(notify_if_outdated=True)

    def on_exit(self) -> None:
        pass

    # ----- building -----
    def build(self, parent_container) -> None:
        self._load_from_state()
        initial_status = get_plugin_status()

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
                            ui.label("Show connection status footer").classes(
                                "text-sm"
                            )
                            self.ui_elements["status_footer_enabled"] = (
                                ui.switch(value=self.buffer.status_footer_enabled)
                                .classes("q-switch")
                                .on_value_change(
                                    lambda e: self._set(
                                        "status_footer_enabled", bool(e.value)
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
                    with ui.row().classes(
                        "w-full items-start justify-between gap-3 flex-wrap"
                    ):
                        with ui.column().classes("gap-1 min-w-0"):
                            with ui.row().classes("items-center gap-2 flex-wrap"):
                                ui.label("Status").classes("text-sm secondary-text")
                                self.ui_elements["plugin_status_primary"] = ui.label(
                                    get_plugin_status_display(initial_status).status_text
                                ).classes("font-semibold text-sm")
                            self.ui_elements["plugin_installed_version"] = ui.label(
                                ""
                            ).classes("text-sm secondary-text")
                            self.ui_elements["plugin_new_version"] = ui.label(
                                ""
                            ).classes("text-sm text-amber-400 font-medium")
                        self.ui_elements["install_plugin_button"] = primary_button(
                            get_install_button_label(initial_status),
                            self._install_streamdeck_plugin,
                        )
                    self._apply_plugin_status_display(initial_status)

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

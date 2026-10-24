# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import html
import logging
from contextlib import contextmanager
from typing import Dict, Any, Optional

from nicegui import ui

from ...font_utils import SYSTEM_DEFAULT_LABEL, get_available_font_families
from ...ui_buttons import outline_button, primary_button
from ...ui_font import apply_app_font, apply_ui_scale
from ...ui_form_controls import form_number, form_select
from .app_settings_styles import APP_SETTINGS_CSS
from ...notification_engine import notify, refresh_service_status_footer
from ...help_system.contextual_help import help_button
from ...streamdeck_plugin_utils import (
    PluginInstallError,
    PluginInstallState,
    get_install_button_label,
    get_plugin_status,
    get_plugin_status_display,
    install_bundled_plugin,
    maybe_notify_streamdeck_plugin_outdated,
)

from ... import dataobjects
from ...dataobjects import state_manager

logger = logging.getLogger(__name__)


class AppSettingsTab:
    name = "App Settings"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.AppSettings] = None
        self.ui_elements: Dict[str, Any] = {}

    def _apply_plugin_status_display(self, status) -> None:
        display = get_plugin_status_display(status)

        badge = self.ui_elements.get("plugin_badge")
        if badge is not None:
            if status.state == PluginInstallState.UP_TO_DATE:
                css, icon = "is-ok", "check_circle"
            elif status.state == PluginInstallState.OUTDATED:
                css, icon = "is-warn", "update"
            elif status.state == PluginInstallState.UNKNOWN:
                css, icon = "is-ok", "check_circle"
            else:
                css, icon = "is-muted", "extension_off"
            badge.set_content(
                f'<span class="aps-plugin-badge {css}"><span class="aps-icon">{icon}</span>'
                f"{html.escape(display.status_text)}</span>"
            )

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
            result = install_bundled_plugin()

            status = get_plugin_status()
            if status.state in (
                PluginInstallState.UP_TO_DATE,
                PluginInstallState.UNKNOWN,
            ):
                verb = (
                    "reinstalled"
                    if was_installed or result.replaced_existing
                    else "installed"
                )
                notify(
                    f"Plugin {verb} successfully! "
                    "Quit and reopen Stream Deck (or restart it) so the update loads.",
                    type="positive",
                    timeout=6000,
                )
                self._refresh_plugin_ui()
            else:
                notify(
                    "Plugin files were copied but version check failed. "
                    "Restart Stream Deck, then open App Settings to verify.",
                    type="warning",
                    timeout=6000,
                )
                self._refresh_plugin_ui()

        except PluginInstallError as e:
            notify(e.user_message, type="negative", timeout=8000)
        except Exception as e:
            notify(f"Error installing plugin: {e}", type="negative")

    # ----- lifecycle -----
    def on_enter(self) -> None:
        self._refresh_plugin_ui(notify_if_outdated=True)

    def on_exit(self) -> None:
        pass

    # ----- building -----
    def build(self, parent_container) -> None:
        from ...help_system.help_render import (
            inject_page_stylesheet,
            promote_color_importance,
            scope_selectors,
        )

        inject_page_stylesheet(
            "mycelian-app-settings",
            promote_color_importance(scope_selectors(APP_SETTINGS_CSS, ".aps")),
        )
        self._load_from_state()
        self._dims: Dict[str, Any] = {}
        self._autostart_ok = self._autostart_supported()
        initial_status = get_plugin_status()

        with parent_container:
            with ui.element("div").classes("aps"):
                with ui.element("div").classes("aps-grid"):
                    with ui.element("div").classes("aps-col"):
                        self._build_appearance()
                        self._build_notifications()
                        self._build_updates()
                    with ui.element("div").classes("aps-col"):
                        self._build_window()
                        self._build_storage()
                        self._build_streamdeck(initial_status)
                with ui.element("div").classes("aps-actions"):
                    self.ui_elements["_dirty_label"] = ui.html("", sanitize=False).classes("aps-dirty")
                    outline_button("Discard", self.discard)
                    primary_button("Save", self.save)
        self._apply_plugin_status_display(initial_status)
        self._refresh_derived()

    # Rows are "title + description" on the left and the control on the right.
    @staticmethod
    def _card(icon: str, title: str, subtitle: str):
        card = ui.element("div").classes("aps-card")
        with card:
            with ui.element("div").classes("aps-card-head"):
                ui.html(f'<div class="aps-card-icon"><span class="aps-icon">{icon}</span></div>', sanitize=False)
                with ui.column().classes("gap-0"):
                    ui.label(title).classes("aps-card-title")
                    ui.label(subtitle).classes("aps-card-sub")
        return card

    @contextmanager
    def _row(self, title: str, description: str = "", *, key: Optional[str] = None, tag: str = ""):
        row = ui.element("div").classes("aps-row")
        if key:
            self._dims[key] = row
        with row:
            with ui.element("div").classes("min-w-0"):
                with ui.element("div").classes("aps-row-title"):
                    ui.label(title)
                    if tag:
                        ui.label(tag).classes("aps-tag")
                if description:
                    ui.label(description).classes("aps-row-desc")
            with ui.element("div").classes("aps-control"):
                yield

    def _switch(self, field: str, setter=None) -> Any:
        setter = setter or self._set
        el = ui.switch(value=bool(getattr(self.buffer, field, False))).classes("q-switch")
        el.on_value_change(lambda e, f=field: setter(f, bool(e.value)))
        self.ui_elements[field] = el
        return el

    def _number(self, field: str, tooltip: str, *, lo: int, hi: int, step: int, default: int, unit: str) -> Any:
        el = form_number(
            tooltip=tooltip,
            value=getattr(self.buffer, field, default),
            min=lo,
            max=hi,
            step=step,
            classes="w-24",
            on_change=lambda e, f=field, d=default: self._set(f, int(e.value or d)),
        )
        self.ui_elements[field] = el
        ui.label(unit).classes("aps-unit")
        return el

    def _build_appearance(self) -> None:
        with self._card("palette", "Appearance", "How the Mycelian window looks"):
            with self._row("App font", "Used everywhere in this window. Overlays keep their own fonts."):
                self.ui_elements["ui_font_family"] = form_select(
                    tooltip="Font used across the Mycelian interface",
                    options=get_available_font_families(),
                    value=self.buffer.ui_font_family or SYSTEM_DEFAULT_LABEL,
                    classes="w-56",
                )
                self.ui_elements["ui_font_family"].on_value_change(
                    lambda e: self._set("ui_font_family", self._font_from_value(e.value))
                )
            with self._row("Interface scale", "Zooms the whole window. Changes show right away."):
                scale_options = {str(step): f"{step}%" for step in (90, 95, 100, 105, 110, 115, 120, 125)}
                current_scale = str(int(getattr(self.buffer, "ui_scale_percent", 100) or 100))
                if current_scale not in scale_options:
                    current_scale = "100"
                self.ui_elements["ui_scale_percent"] = form_select(
                    tooltip="Zoom the Mycelian window between 90% and 125%",
                    options=scale_options,
                    value=current_scale,
                    classes="w-28",
                )

                def on_scale_change(event) -> None:
                    percent = int(event.value or 100)
                    self._set("ui_scale_percent", percent)
                    apply_ui_scale(percent)

                self.ui_elements["ui_scale_percent"].on_value_change(on_scale_change)
            with ui.element("div").classes("aps-preview") as preview:
                ui.label("Mycelian streaming toolkit").classes("aps-preview-big")
                ui.label("Alerts, overlays and chat in one place").classes("aps-preview-small")
            self._font_preview = preview

    def _build_notifications(self) -> None:
        with self._card("notifications", "Notifications and status bar", "What Mycelian tells you while it runs"):
            with self._row("Notifications", "Pop-up messages in the corner of the window, such as \"Settings saved\"."):
                self._switch("notifications_enabled")
            with self._row("Connection status bar", "The bar along the bottom that shows each service's connection."):
                help_button(topic_id="status_footer", tooltip="Status footer colors", size="sm")
                self._switch("status_footer_enabled")
            with self._row("Minimal badges", "Only icons with a colored ring; hover a badge for its status.", key="status_footer_minimal"):
                self._switch("status_footer_minimal")

    def _build_window(self) -> None:
        with self._card("desktop_windows", "Window and startup", "How Mycelian opens and keeps running"):
            with self._row("Start maximized", "Open the window full size.", tag="Next launch"):
                self._switch("start_maximized")
            with self._row("Run at login", self._login_description(), key="run_at_startup"):
                startup_switch = self._switch("run_at_startup", self._set_tray_option)
                if not self._autostart_supported():
                    startup_switch.disable()
                    ui.tooltip("Only available in an installed build of Mycelian")
            with self._row("Minimize to tray", "Closing or minimizing the window keeps Mycelian running in the system tray."):
                self._switch("minimize_to_tray", self._set_tray_option)
            with self._row("Start minimized to tray", "Launch straight into the tray without showing the window."):
                self._switch("start_minimized", self._set_tray_option)
            self._tray_note = ui.html("", sanitize=False)

    def _login_description(self) -> str:
        if self._autostart_supported():
            return "Start Mycelian when you sign in to this computer."
        return "Only available in an installed build of Mycelian."

    def _build_updates(self) -> None:
        with self._card("system_update", "Updates", "Keeping Mycelian current"):
            with self._row("Check for updates automatically", "Look for a new version in the background and offer to install it."):
                self._switch("auto_update")
            with self._row("Check every", "How often the automatic check runs.", key="update_check_interval_minutes"):
                self._number(
                    "update_check_interval_minutes",
                    "How often Mycelian checks for application updates (minutes)",
                    lo=5, hi=120, step=5, default=30, unit="minutes",
                )

    def _build_storage(self) -> None:
        with self._card("inventory_2", "Activity feed and alert storage", "How much history is shown and kept"):
            with self._row("Items per page", "How many events the Activity Feed shows at a time."):
                self._number(
                    "activity_feed_limit", "Number of activity items shown per page",
                    lo=5, hi=100, step=5, default=5, unit="per page",
                )
            with self._row("Pages to load", "How many pages of history the feed can page back through."):
                self._number(
                    "activity_feed_max_pages", "Maximum pages of history kept in the activity feed",
                    lo=1, hi=50, step=1, default=1, unit="pages",
                )
            self._feed_note = ui.html("", sanitize=False)
            with self._row("Auto-trim stored alerts", "Delete old alerts from storage automatically after saving and at startup."):
                self._switch("alert_storage_auto_trim")
            with self._row("Trim by", "Which rule decides what gets deleted.", key="alert_storage_trim_mode"):
                self.ui_elements["alert_storage_trim_mode"] = form_select(
                    tooltip="Quantity keeps the newest N alerts. Time deletes alerts older than the retention window. Both applies both rules.",
                    options={"quantity": "Max number", "time": "Age", "both": "Number and age"},
                    value=self.buffer.alert_storage_trim_mode or "both",
                    classes="w-44",
                    on_change=lambda e: self._set("alert_storage_trim_mode", str(e.value or "both")),
                )
            with self._row("Keep the newest", "Used when trimming by number.", key="alert_storage_keep_count"):
                self._number(
                    "alert_storage_keep_count",
                    "Maximum number of past alerts to keep when quantity trim is enabled",
                    lo=10, hi=10000, step=10, default=10, unit="alerts",
                )
            with self._row("Delete alerts older than", "Used when trimming by age.", key="alert_storage_keep_days"):
                self._number(
                    "alert_storage_keep_days",
                    "Delete stored alerts older than this many days when age trim is enabled",
                    lo=1, hi=3650, step=1, default=1, unit="days",
                )
            self._trim_note = ui.html("", sanitize=False)

    def _build_streamdeck(self, initial_status) -> None:
        with self._card("grid_view", "Stream Deck plugin", "Control Mycelian from Elgato Stream Deck keys"):
            with ui.element("div").classes("aps-plugin"):
                self.ui_elements["plugin_badge"] = ui.html("", sanitize=False)
                with ui.element("div").classes("aps-plugin-meta grow"):
                    # Kept as labels: _apply_plugin_status_display updates these by key.
                    self.ui_elements["plugin_status_primary"] = ui.label(
                        get_plugin_status_display(initial_status).status_text
                    ).classes("hidden")
                    self.ui_elements["plugin_installed_version"] = ui.label("")
                    self.ui_elements["plugin_new_version"] = ui.label("").classes("is-new")
                self.ui_elements["install_plugin_button"] = primary_button(
                    get_install_button_label(initial_status),
                    self._install_streamdeck_plugin,
                    icon="download",
                )

    # ----- live summaries -----
    def _refresh_derived(self) -> None:
        """Dim rows that don't apply, and update the summary notes and preview."""
        if not self.buffer or not getattr(self, "_dims", None):
            return
        b = self.buffer
        trim_on = bool(b.alert_storage_auto_trim)
        mode = b.alert_storage_trim_mode or "both"
        dim = {
            "status_footer_minimal": not b.status_footer_enabled,
            "run_at_startup": not getattr(self, "_autostart_ok", True),
            "update_check_interval_minutes": not b.auto_update,
            "alert_storage_trim_mode": not trim_on,
            "alert_storage_keep_count": not trim_on or mode == "time",
            "alert_storage_keep_days": not trim_on or mode == "quantity",
        }
        for key, off in dim.items():
            row = self._dims.get(key)
            if row is not None:
                row.classes(add="is-dim") if off else row.classes(remove="is-dim")

        per_page = int(b.activity_feed_limit or 0)
        pages = int(b.activity_feed_max_pages or 0)
        self._set_note(self._feed_note, "view_list",
                       f"The feed can show up to <b>{per_page * pages:,}</b> events ({per_page} × {pages} pages).")

        if not trim_on:
            self._set_note(self._trim_note, "all_inclusive", "Stored alerts are <b>kept forever</b>. Turn on auto-trim to limit storage.")
        else:
            count, days = int(b.alert_storage_keep_count or 0), int(b.alert_storage_keep_days or 0)
            rules = {
                "quantity": f"keeps the newest <b>{count:,}</b> alerts",
                "time": f"deletes alerts older than <b>{days:,} day{'s' if days != 1 else ''}</b>",
                "both": f"keeps the newest <b>{count:,}</b> alerts and deletes any older than <b>{days:,} day{'s' if days != 1 else ''}</b>",
            }
            self._set_note(self._trim_note, "auto_delete", f"Auto-trim {rules.get(mode, rules['both'])}.")

        if b.minimize_to_tray or b.start_minimized:
            self._set_note(self._tray_note, "info",
                           "Mycelian keeps running in the tray: alerts, chat and overlays stay live while the window is closed.")
        else:
            self._set_note(self._tray_note, "", "")

        family = self._font_from_value(getattr(b, "ui_font_family", ""))
        if getattr(self, "_font_preview", None) is not None:
            self._font_preview.style(f"font-family: {self._css_font(family)}")

        label = self.ui_elements.get("_dirty_label")
        if label is not None:
            if self.dirty:
                label.set_content('<span class="aps-dot"></span>Unsaved changes')
                label.classes(add="is-dirty")
            else:
                label.set_content('<span class="aps-dot"></span>All changes saved')
                label.classes(remove="is-dirty")

    @staticmethod
    def _css_font(family: str) -> str:
        if not family:
            return "inherit"
        return "'" + family.replace("\\", "").replace("'", "") + "', sans-serif"

    @staticmethod
    def _set_note(element, icon: str, text: str) -> None:
        if element is None:
            return
        if not text:
            element.set_content("")
            return
        element.set_content(f'<div class="aps-note"><span class="aps-icon">{icon}</span><div>{text}</div></div>')

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

    @staticmethod
    def _font_from_value(raw) -> str:
        if raw is None or raw == "" or raw == SYSTEM_DEFAULT_LABEL:
            return ""
        return str(raw)

    def _sync_font_from_ui(self) -> None:
        el = self.ui_elements.get("ui_font_family")
        if el is not None:
            self.buffer.ui_font_family = self._font_from_value(el.value)

    def _set(self, field: str, value) -> None:
        current_value = getattr(self.buffer, field)
        if current_value != value:
            setattr(self.buffer, field, value)
            self.dirty = True
            self._refresh_derived()

    @staticmethod
    def _autostart_supported() -> bool:
        try:
            from ...autostart import is_supported

            return is_supported()
        except Exception:
            return False

    def _set_tray_option(self, field: str, value: bool) -> None:
        """Set a background-operation toggle and warn about metered database usage."""
        self._set(field, value)
        if not value:
            return
        try:
            from ...tray_controller import warn_background_database_usage

            warn_background_database_usage()
        except Exception:
            pass

    # ----- actions -----
    def save(self) -> None:
        if not self.buffer:
            return
        self._sync_font_from_ui()
        # persist buffered values to state_manager (skip hardcoded metadata fields)
        _skip_save = frozenset({"version", "build_number", "build_date"})
        for field in self.buffer.__dataclass_fields__.keys():
            if field in _skip_save:
                continue
            state_manager.update_app_setting(field, getattr(self.buffer, field))
        if state_manager.save_changes():
            apply_app_font(self.buffer.ui_font_family)
            apply_ui_scale(self.buffer.ui_scale_percent)
            self._apply_background_settings()
            self._apply_alert_storage_trim()
            try:
                refresh_service_status_footer()
            except Exception:
                logger.debug("status footer refresh after save failed", exc_info=True)
            notify("Settings saved", type="positive")
            self.dirty = False
            self._refresh_derived()
        else:
            notify("Error saving settings", type="negative")

    def _apply_background_settings(self) -> None:
        """Push the saved tray / autostart toggles to the OS and the tray process."""
        try:
            from ...autostart import set_enabled as set_autostart_enabled

            set_autostart_enabled(bool(self.buffer.run_at_startup))
        except Exception as e:
            notify(f"Could not update the login item: {e}", type="warning")

        try:
            from ...tray_controller import apply_settings

            apply_settings()
        except Exception:
            # Tray availability is reported by tray_controller itself; a failure here
            # must not block the rest of the save.
            pass

    def _apply_alert_storage_trim(self) -> None:
        """Kick off stored-alert retention in the background after save."""
        if not self.buffer or not self.buffer.alert_storage_auto_trim:
            return
        try:
            from ... import alertutils

            alertutils.alert_state_manager.initialize()
            alertutils.schedule_auto_trim_stored_alerts()
        except Exception:
            pass

    def discard(self) -> None:
        # reload from state and update UI controls
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                if key == "ui_font_family":
                    element.value = self.buffer.ui_font_family or SYSTEM_DEFAULT_LABEL
                elif key == "ui_scale_percent":
                    element.value = str(
                        int(getattr(self.buffer, "ui_scale_percent", 100) or 100)
                    )
                else:
                    element.value = getattr(self.buffer, key)
        self.dirty = False
        apply_ui_scale(int(getattr(self.buffer, "ui_scale_percent", 100) or 100))
        self._refresh_derived()

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
from ...notify_settings import DURATION_CHOICES, LEVEL_LABELS, LEVELS, POSITIONS, SOURCES, NotifyPrefs, duration_label
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
            versions = []
            if display.installed_version:
                versions.append(f"Installed: v{display.installed_version}")
            if display.new_version_available:
                versions.append(f"New version available: v{display.new_version_available}")
            title = html.escape(" · ".join(versions), quote=True)
            badge.set_content(
                f'<span class="aps-plugin-badge {css}" title="{title}"><span class="aps-icon">{icon}</span>'
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
                with ui.element("div").classes("aps-shell"):
                    self._build_header()
                    with ui.element("div").classes("aps-body"):
                        with ui.element("div").classes("aps-col"):
                            self._build_appearance()
                            self._build_notifications()
                            self._build_streamdeck(initial_status)
                        with ui.element("div").classes("aps-col"):
                            self._build_window()
                            self._build_updates()
                        with ui.element("div").classes("aps-col"):
                            self._build_storage()
                    self._notify_panel = ui.element("div").classes("aps-npanel")
        self._render_notify_panel()
        self._apply_plugin_status_display(initial_status)
        self._refresh_derived()

    def _build_header(self) -> None:
        with ui.element("div").classes("aps-head"):
            ui.html(
                '<span class="aps-title-icon"><span class="aps-icon">tune</span></span>'
                '<span><div class="aps-title-name">App Settings</div>'
                '<div class="aps-title-sub">How Mycelian looks, starts and stores history</div></span>',
                sanitize=False,
            ).classes("aps-title")
            self.ui_elements["_dirty_label"] = ui.html("", sanitize=False)
            ui.element("div").classes("aps-spacer")
            self.ui_elements["_discard_btn"] = outline_button("Discard", self.discard).props("dense").classes("px-3")
            primary_button("Save", self.save, icon="check").props("dense").classes("px-4")

    @staticmethod
    def _section(icon: str, title: str):
        sec = ui.element("div").classes("aps-sec")
        with sec:
            ui.html(f'<span class="aps-icon">{icon}</span>{html.escape(title)}', sanitize=False).classes("aps-sec-head")
        return sec

    @contextmanager
    def _row(self, title: str, description: str = "", *, key: Optional[str] = None, tag: str = "", help_topic: str = ""):
        """One line: label with hover help (or a help link) on the left, the control on the right."""
        row = ui.element("div").classes("aps-row")
        if key:
            self._dims[key] = row
        with row:
            with ui.element("div").classes("aps-label"):
                ui.label(title)
                if tag:
                    ui.label(tag).classes("aps-tag")
                if help_topic:
                    help_button(topic_id=help_topic, tooltip=description, size="sm")
                elif description:
                    ui.html('<span class="aps-icon">help_outline</span>', sanitize=False).classes("aps-help").tooltip(description)
            with ui.element("div").classes("aps-ctrl"):
                yield

    def _switch(self, field: str, setter=None) -> Any:
        setter = setter or self._set
        el = ui.switch(value=bool(getattr(self.buffer, field, False))).props("dense")
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
            classes="aps-num",
            on_change=lambda e, f=field, d=default: self._set(f, int(e.value or d)),
        )
        self.ui_elements[field] = el
        ui.label(unit).classes("aps-unit")
        return el

    def _build_appearance(self) -> None:
        with self._section("palette", "Appearance"):
            with self._row("App font", "Used everywhere in this window. Overlays keep their own fonts."):
                self.ui_elements["ui_font_family"] = form_select(
                    tooltip="Font used across the Mycelian interface",
                    options=get_available_font_families(),
                    value=self.buffer.ui_font_family or SYSTEM_DEFAULT_LABEL,
                    classes="w-44",
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
                    classes="w-24",
                )

                def on_scale_change(event) -> None:
                    percent = int(event.value or 100)
                    self._set("ui_scale_percent", percent)
                    apply_ui_scale(percent)

                self.ui_elements["ui_scale_percent"].on_value_change(on_scale_change)
            self._font_preview = ui.html("<b>Aa</b><span>Mycelian streaming toolkit</span>", sanitize=False).classes("aps-sample")

    def _build_notifications(self) -> None:
        with self._section("signal_cellular_alt", "Status bar"):
            with self._row(
                "Connection status bar",
                "The bar along the bottom that shows each service's connection. Click for what the colors mean.",
                help_topic="status_footer",
            ):
                self._switch("status_footer_enabled")
            with self._row("Minimal badges", "Only icons with a colored ring; hover a badge for its status.", key="status_footer_minimal"):
                self._switch("status_footer_minimal")

    # ----- notifications panel -----
    def _render_notify_panel(self) -> None:
        panel = getattr(self, "_notify_panel", None)
        if panel is None or getattr(panel, "is_deleted", False):
            return
        b = self.buffer
        prefs = NotifyPrefs.from_app_settings(b)
        panel.clear()
        with panel:
            with ui.element("div").classes("aps-nhead"):
                ui.html('<span class="aps-icon">notifications</span>Notifications', sanitize=False).classes("aps-sec-head")
                ui.element("div").classes("aps-spacer")
                outline_button("Send a test", self._send_test_notifications, icon="science").props("dense").classes("px-3").tooltip(
                    "Shows one of each kind with the saved settings")
            with ui.element("div").classes("aps-ngrid"):
                with ui.element("div"):
                    with self._row("Pop-up notifications", "Turn every pop-up off. The notification centre (the bell) still records them."):
                        self._switch("notifications_enabled")
                    with self._row("Where pop-ups appear", "Corner of the window pop-ups slide into."):
                        form_select(tooltip="Pop-up position", options=dict(POSITIONS), value=prefs.position, classes="w-40",
                                    on_change=lambda e: self._set("notify_position", str(e.value or "top-right")))
                    with self._row("Most on screen at once", "Older pop-ups close early when more arrive (errors stay longest)."):
                        form_select(tooltip="Maximum pop-ups shown together", options={str(n): str(n) for n in range(1, 9)},
                                    value=str(prefs.max_visible), classes="w-20",
                                    on_change=lambda e: self._set("notify_max_visible", int(e.value or 4)))
                with ui.element("div"):
                    with self._row("Desktop notifications when minimized",
                                   "Errors and warnings also show as system notifications while Mycelian is in the tray."):
                        self._switch("notify_desktop_when_minimized")
                    with self._row("Keep muted ones in the notification centre",
                                   "Notifications you silenced below are still listed under the bell, marked Muted."):
                        self._switch("notify_record_muted")
            ui.label("By level").classes("aps-nsub")
            with ui.element("div").classes("aps-levels"):
                for level in LEVELS:
                    on = prefs.levels[level]
                    with ui.element("div").classes(f"aps-level is-{level}" + ("" if on else " is-off")):
                        with ui.element("div").classes("aps-level-top"):
                            ui.element("span").classes("d")
                            ui.label(LEVEL_LABELS[level])
                            ui.element("div").classes("aps-spacer")
                            ui.switch(value=on, on_change=lambda e, lv=level: self._set_level(lv, bool(e.value))).props("dense").tooltip(
                                f"Show {LEVEL_LABELS[level].lower()} as pop-ups")
                        with ui.element("div").classes("aps-level-row"):
                            ui.label("Stays up")
                            choices = {str(int(s) if s == int(s) else s): duration_label(s) for s in DURATION_CHOICES}
                            current = prefs.durations[level]
                            key = str(int(current) if current == int(current) else current)
                            choices.setdefault(key, duration_label(current))
                            form_select(tooltip=f"How long {LEVEL_LABELS[level].lower()} stay on screen", options=choices, value=key,
                                        classes="w-36", on_change=lambda e, lv=level: self._set_duration(lv, e.value))
            ui.label("By feature: untick to silence one kind of notification for one feature").classes("aps-nsub")
            with ui.element("div").classes("aps-mx-wrap"):
                with ui.element("div").classes("aps-mx-cols"):
                    half = (len(SOURCES) + 1) // 2
                    for chunk in (SOURCES[:half], SOURCES[half:]):
                        self._feature_table(chunk, prefs)

    def _feature_table(self, sources, prefs: "NotifyPrefs") -> None:
        with ui.element("table").classes("aps-mx"):
            with ui.element("tr"):
                ui.html("<th>Feature</th>", sanitize=False).classes("contents")
                for lv in LEVELS:
                    th = ui.element("th")
                    with th:
                        ui.html(f'<span class="d" style="background:var(--color-notify-{lv})"></span>{LEVEL_LABELS[lv]}', sanitize=False).classes("contents")
                    th.on("click", lambda _e, level=lv: self._toggle_level_column(level))
                    th.tooltip(f"Tick or untick {LEVEL_LABELS[lv].lower()} for every feature")
            for sid, label, icon in sources:
                muted = prefs.muted.get(sid, [])
                with ui.element("tr").classes("is-muted" if len(muted) == len(LEVELS) else ""):
                    with ui.element("td"):
                        ui.html(f'<span class="mod"><span class="aps-icon">{icon}</span>{html.escape(label)}</span>'
                                + ('<span class="aps-mute-tag">Muted</span>' if len(muted) == len(LEVELS) else ""), sanitize=False)
                    for lv in LEVELS:
                        with ui.element("td"):
                            ui.checkbox(value=lv not in muted, on_change=lambda e, s=sid, level=lv: self._set_source_level(s, level, bool(e.value))
                                        ).props("dense size=sm")

    def _set_level(self, level: str, on: bool) -> None:
        levels = dict(NotifyPrefs.from_app_settings(self.buffer).levels)
        levels[level] = on
        self._set("notify_levels", levels)
        self._render_notify_panel()

    def _set_duration(self, level: str, raw) -> None:
        durations = dict(NotifyPrefs.from_app_settings(self.buffer).durations)
        try:
            durations[level] = float(raw)
        except (TypeError, ValueError):
            return
        self._set("notify_durations", durations)

    def _set_source_level(self, source: str, level: str, on: bool) -> None:
        muted = {k: list(v) for k, v in NotifyPrefs.from_app_settings(self.buffer).muted.items()}
        levels_off = set(muted.get(source, []))
        if on:
            levels_off.discard(level)
        else:
            levels_off.add(level)
        if levels_off:
            muted[source] = sorted(levels_off)
        else:
            muted.pop(source, None)
        self._set("notify_muted", muted)
        self._render_notify_panel()

    def _toggle_level_column(self, level: str) -> None:
        """Header click: if any feature has this level on, turn it off everywhere; else on everywhere."""
        muted = {k: set(v) for k, v in NotifyPrefs.from_app_settings(self.buffer).muted.items()}
        any_on = any(level not in muted.get(sid, set()) for sid, _l, _i in SOURCES)
        for sid, _label, _icon in SOURCES:
            levels_off = muted.setdefault(sid, set())
            if any_on:
                levels_off.add(level)
            else:
                levels_off.discard(level)
        self._set("notify_muted", {k: sorted(v) for k, v in muted.items() if v})
        self._render_notify_panel()

    def _send_test_notifications(self) -> None:
        if self.dirty:
            notify("Save first to test your new settings", type="info", source="app",
                   message="The test uses the saved notification settings.")
            return
        notify("Something went wrong", type="error", source="app", message="This is what an error looks like.",
               hint="Errors say how to fix the problem here.", actions=[{"kind": "navigate", "main_tab": "Settings",
                                                                          "settings_subtab": "App Settings", "label": "Open App Settings"}],
               dedupe_key="test:error")
        notify("Worth a look", type="warning", source="app", message="This is what a warning looks like.", dedupe_key="test:warning")
        notify("Just so you know", type="info", source="app", message="This is what info looks like.", dedupe_key="test:info")
        notify("All done", type="success", source="app", dedupe_key="test:success")

    def _build_window(self) -> None:
        with self._section("desktop_windows", "Window and startup"):
            with self._row("Start maximized", "Open the window full size.", tag="Next launch"):
                self._switch("start_maximized")
            with self._row("Run at login", self._login_description(), key="run_at_startup"):
                startup_switch = self._switch("run_at_startup", self._set_tray_option)
                if not self._autostart_ok:
                    startup_switch.disable()
            with self._row("Minimize to tray", "Closing or minimizing the window keeps Mycelian running in the system tray."):
                self._switch("minimize_to_tray", self._set_tray_option)
            with self._row("Start minimized to tray", "Launch straight into the tray without showing the window."):
                self._switch("start_minimized", self._set_tray_option)
            self._tray_note = ui.html("", sanitize=False).classes("aps-foot")

    def _login_description(self) -> str:
        if self._autostart_ok:
            return "Start Mycelian when you sign in to this computer."
        return "Only available in an installed build of Mycelian."

    def _build_updates(self) -> None:
        with self._section("system_update", "Updates"):
            with self._row("Check for updates automatically", "Look for a new version in the background and offer to install it."):
                self._switch("auto_update")
            with self._row("Check every", "How often the automatic check runs.", key="update_check_interval_minutes"):
                self._number(
                    "update_check_interval_minutes",
                    "How often Mycelian checks for application updates (minutes)",
                    lo=5, hi=120, step=5, default=30, unit="min",
                )

    def _build_storage(self) -> None:
        with self._section("inventory_2", "Activity feed and storage"):
            with self._row("Items per page", "How many events the Activity Feed shows at a time."):
                self._number(
                    "activity_feed_limit", "Number of activity items shown per page",
                    lo=5, hi=100, step=5, default=5, unit="items",
                )
            with self._row("Pages to load", "How many pages of history the feed can page back through."):
                self._number(
                    "activity_feed_max_pages", "Maximum pages of history kept in the activity feed",
                    lo=1, hi=50, step=1, default=1, unit="pages",
                )
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
            self._storage_note = ui.html("", sanitize=False).classes("aps-foot")

    def _build_streamdeck(self, initial_status) -> None:
        with self._section("grid_view", "Stream Deck plugin"):
            with ui.element("div").classes("aps-plugin"):
                self.ui_elements["plugin_badge"] = ui.html("", sanitize=False)
                # Kept as (hidden) labels: _apply_plugin_status_display updates these by key;
                # the badge's hover text shows the versions.
                self.ui_elements["plugin_status_primary"] = ui.label(
                    get_plugin_status_display(initial_status).status_text
                ).classes("hidden")
                self.ui_elements["plugin_installed_version"] = ui.label("").classes("hidden")
                self.ui_elements["plugin_new_version"] = ui.label("").classes("hidden")
                ui.element("div").classes("aps-spacer")
                self.ui_elements["install_plugin_button"] = primary_button(
                    get_install_button_label(initial_status),
                    self._install_streamdeck_plugin,
                    icon="download",
                ).props("dense").classes("px-3")

    # ----- live summaries -----
    def _refresh_derived(self) -> None:
        """Dim rows that don't apply, and update the summary lines, font sample and header."""
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
            if row is None:
                continue
            if off:
                row.classes(add="is-dim")
            else:
                row.classes(remove="is-dim")

        per_page = int(b.activity_feed_limit or 0)
        pages = int(b.activity_feed_max_pages or 0)
        feed = f"Feed shows up to <b>{per_page * pages:,}</b> events."
        if not trim_on:
            storage = "Stored alerts are <b>kept forever</b>."
        else:
            count, days = int(b.alert_storage_keep_count or 0), int(b.alert_storage_keep_days or 0)
            age = f"<b>{days:,} day{'s' if days != 1 else ''}</b>"
            storage = {
                "quantity": f"Keeps the newest <b>{count:,}</b> alerts.",
                "time": f"Deletes alerts older than {age}.",
            }.get(mode, f"Keeps the newest <b>{count:,}</b> alerts, none older than {age}.")
        self._set_note(getattr(self, "_storage_note", None), f"{feed} {storage}")

        tray = b.minimize_to_tray or b.start_minimized
        self._set_note(
            getattr(self, "_tray_note", None),
            "Alerts, chat and overlays stay live while the window is in the tray." if tray else "",
        )

        family = self._font_from_value(getattr(b, "ui_font_family", ""))
        if getattr(self, "_font_preview", None) is not None:
            self._font_preview.style(f"font-family: {self._css_font(family)}")

        label = self.ui_elements.get("_dirty_label")
        if label is not None:
            if self.dirty:
                label.set_content('<span class="aps-pill is-warn"><span class="aps-icon">edit</span>Unsaved changes</span>')
            else:
                label.set_content('<span class="aps-pill is-ok"><span class="aps-icon">check_circle</span>All changes saved</span>')
        discard = self.ui_elements.get("_discard_btn")
        if discard is not None:
            if self.dirty:
                discard.props(remove="disable")
            else:
                discard.props("disable")

    @staticmethod
    def _css_font(family: str) -> str:
        if not family:
            return "inherit"
        return "'" + family.replace("\\", "").replace("'", "") + "', sans-serif"

    @staticmethod
    def _set_note(element, text: str) -> None:
        if element is None:
            return
        element.set_content(f'<span class="aps-icon">info</span><span>{text}</span>' if text else "")

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
            self._push_notify_settings_to_docks()
            notify("Settings saved", type="positive", source="app")
            self.dirty = False
            self._refresh_derived()
        else:
            notify("Error saving settings", type="negative")

    @staticmethod
    def _push_notify_settings_to_docks() -> None:
        """OBS docks show their own pop-ups; send them the new level switches and durations."""
        try:
            from ... import web_engine

            engine = web_engine.web_engine_instance
            if engine is not None:
                engine.safe_emit("notify_settings", NotifyPrefs.from_app_settings(state_manager.get_app_settings()).for_client())
        except Exception:
            logger.debug("could not push notification settings to docks", exc_info=True)

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
        self._render_notify_panel()
        self._refresh_derived()

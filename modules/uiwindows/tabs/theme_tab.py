from __future__ import annotations

from typing import Dict, Any, Optional

from nicegui import ui

from ...dataobjects import state_manager
from ...theme_manager import (
    get_theme_manager,
    generate_preview_css_variables,
    ThemeColors,
)
from ...help_system.contextual_help import help_button
from ...notification_engine import notify
from .base import TabBase


# ---------------------------------------------------------------------------
#  Preview CSS - scoped overrides that beat global ui_styles.py selectors
# ---------------------------------------------------------------------------
THEME_PREVIEW_CSS = """
/* ============================================
   Theme Preview - Container Isolation
   ============================================ */

body .theme-preview-container,
body.body--dark .theme-preview-container,
body:not(.body--dark) .theme-preview-container {
    background: var(--preview-color-bg-base) !important;
    color: var(--preview-color-text-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
    font-size: 13px !important;
    line-height: 1.5 !important;
    box-sizing: border-box !important;
    width: 100% !important;
    border-radius: 6px !important;
    border: 1px solid var(--preview-color-border-default) !important;
    overflow: hidden !important;
}

/* --- Override ALL text elements inside preview --- */
body .theme-preview-container p,
body .theme-preview-container span,
body .theme-preview-container div,
body .theme-preview-container label,
body:not(.body--dark) .theme-preview-container p,
body:not(.body--dark) .theme-preview-container span,
body:not(.body--dark) .theme-preview-container div,
body:not(.body--dark) .theme-preview-container label,
body.body--dark .theme-preview-container p,
body.body--dark .theme-preview-container span,
body.body--dark .theme-preview-container div,
body.body--dark .theme-preview-container label {
    color: var(--preview-color-text-primary) !important;
}

/* ============================================
   Quasar Component Overrides (scoped)
   ============================================ */

/* Cards */
body .theme-preview-container .q-card,
body.body--dark .theme-preview-container .q-card,
body:not(.body--dark) .theme-preview-container .q-card {
    background: var(--preview-color-bg-surface) !important;
    color: var(--preview-color-text-primary) !important;
    border: 1px solid var(--preview-color-border-subtle) !important;
    box-shadow: none !important;
}

/* Input field controls */
body .theme-preview-container .q-field__control,
body:not(.body--dark) .theme-preview-container .q-field__control {
    background: var(--preview-color-bg-surface) !important;
    color: var(--preview-color-text-primary) !important;
}

body .theme-preview-container .q-field__native,
body .theme-preview-container .q-field__input,
body:not(.body--dark) .theme-preview-container .q-field__native,
body:not(.body--dark) .theme-preview-container .q-field__input {
    color: var(--preview-color-text-primary) !important;
}

body .theme-preview-container .q-field__label,
body:not(.body--dark) .theme-preview-container .q-field__label {
    color: var(--preview-color-text-muted) !important;
}

body .theme-preview-container .q-field__control::before {
    border-color: var(--preview-color-border-default) !important;
}

body .theme-preview-container .q-field__control::after {
    border-color: var(--preview-color-primary) !important;
}

/* Buttons - base */
body .theme-preview-container .q-btn,
body:not(.body--dark) .theme-preview-container .q-btn {
    color: var(--preview-color-text-primary) !important;
}

body .theme-preview-container .q-btn--flat,
body:not(.body--dark) .theme-preview-container .q-btn--flat {
    color: var(--preview-color-text-primary) !important;
    background: transparent !important;
}

/* Toggle / Switch */
body .theme-preview-container .q-toggle__inner--truthy .q-toggle__track,
body:not(.body--dark) .theme-preview-container .q-toggle__inner--truthy .q-toggle__track {
    background-color: var(--preview-color-primary) !important;
    opacity: 0.8 !important;
}

body .theme-preview-container .q-toggle__inner--falsy .q-toggle__track,
body:not(.body--dark) .theme-preview-container .q-toggle__inner--falsy .q-toggle__track {
    background-color: var(--preview-color-border-default) !important;
    opacity: 0.5 !important;
}

body .theme-preview-container .q-toggle__inner--truthy .q-toggle__thumb:after {
    background-color: var(--preview-color-primary) !important;
}

/* Checkbox */
body .theme-preview-container .q-checkbox__bg,
body:not(.body--dark) .theme-preview-container .q-checkbox__bg {
    border-color: var(--preview-color-border-default) !important;
}

body .theme-preview-container .q-checkbox__inner--truthy .q-checkbox__bg {
    background: var(--preview-color-primary) !important;
    border-color: var(--preview-color-primary) !important;
}

/* Select dropdown icon */
body .theme-preview-container .q-select__dropdown-icon,
body:not(.body--dark) .theme-preview-container .q-select__dropdown-icon {
    color: var(--preview-color-text-muted) !important;
}

/* Icons */
body .theme-preview-container .q-icon,
body:not(.body--dark) .theme-preview-container .q-icon {
    color: var(--preview-color-text-secondary) !important;
}

/* Separator */
body .theme-preview-container .q-separator {
    background: var(--preview-color-border-subtle) !important;
}

/* Spinner */
body .theme-preview-container .q-spinner {
    color: var(--preview-color-primary) !important;
}

/* ============================================
   Mock UI - Tab Bar
   ============================================ */

.mock-tab-bar {
    display: flex;
    gap: 0;
    background: var(--preview-color-bg-elevated);
    border-bottom: 1px solid var(--preview-color-border-default);
    padding: 0 6px;
    overflow-x: auto;
}

.mock-tab {
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 500;
    color: var(--preview-color-text-secondary) !important;
    border-bottom: 2px solid transparent;
    white-space: nowrap;
    cursor: default;
    user-select: none;
}

.mock-tab.active {
    color: var(--preview-color-primary) !important;
    border-bottom-color: var(--preview-color-primary);
}

.mock-sub-tab-bar {
    display: flex;
    gap: 0;
    background: var(--preview-color-bg-surface);
    border-bottom: 1px solid var(--preview-color-border-subtle);
    padding: 0 4px;
    overflow-x: auto;
}

.mock-sub-tab {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 5px 8px;
    font-size: 10px;
    font-weight: 500;
    color: var(--preview-color-text-secondary) !important;
    border-bottom: 2px solid transparent;
    white-space: nowrap;
    cursor: default;
    user-select: none;
}

.mock-sub-tab.active {
    color: var(--preview-color-primary) !important;
    border-bottom-color: var(--preview-color-primary);
}

.mock-sub-tab .icon {
    font-family: 'Material Icons';
    font-size: 13px;
    font-weight: normal;
    font-style: normal;
}

/* ============================================
   Mock UI - Content Area
   ============================================ */

.mock-content-area {
    background: var(--preview-color-bg-elevated);
    padding: 10px;
}

.mock-settings-card {
    background: var(--preview-color-bg-surface);
    border: 1px solid var(--preview-color-border-subtle);
    border-radius: 6px;
    padding: 10px;
}

.mock-header-section {
    background: var(--preview-color-primary-light);
    border-left: 3px solid var(--preview-color-primary);
    border-radius: 4px;
    padding: 8px 10px;
    margin-bottom: 8px;
}

.mock-connector-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 5px 0;
    border-bottom: 1px solid var(--preview-color-border-subtle);
}

.mock-connector-row:last-child {
    border-bottom: none;
}

.mock-status-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-right: 5px;
}

.mock-status-dot.connected {
    background: var(--preview-color-success);
}

.mock-status-dot.idle {
    background: var(--preview-color-warning);
}

.mock-status-dot.disconnected {
    background: var(--preview-color-text-muted);
}

.mock-nc-preview-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    flex-wrap: wrap;
}

.mock-nc-chip {
    font-size: 10px;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid var(--preview-color-border-subtle);
    background: var(--preview-color-bg-surface);
    color: var(--preview-color-text-primary);
    border-left-width: 3px;
}

.mock-nc-chip.success {
    border-left-color: var(--preview-color-notify-success);
}

.mock-nc-chip.warning {
    border-left-color: var(--preview-color-notify-warning);
}

.mock-nc-chip.error {
    border-left-color: var(--preview-color-notify-error);
}

.mock-nc-chip.info {
    border-left-color: var(--preview-color-notify-info);
}

/* ============================================
   Preview Button Styles
   ============================================ */

body .theme-preview-container .theme-button-primary,
body .theme-preview-container .q-btn.theme-button-primary {
    background: var(--preview-color-primary) !important;
    color: var(--preview-color-text-inverse) !important;
    border: none !important;
    border-radius: 4px !important;
}

body .theme-preview-container .theme-button-primary .q-btn__content {
    background: transparent !important;
    color: var(--preview-color-text-inverse) !important;
}

body .theme-preview-container .theme-button-secondary,
body .theme-preview-container .q-btn.theme-button-secondary {
    background: transparent !important;
    color: var(--preview-color-primary) !important;
    border: 1px solid var(--preview-color-border-accent) !important;
    border-radius: 4px !important;
}

body .theme-preview-container .theme-button-secondary .q-btn__content {
    background: transparent !important;
    color: var(--preview-color-primary) !important;
}

body .theme-preview-container .theme-button-success,
body .theme-preview-container .q-btn.theme-button-success {
    background: var(--preview-color-success) !important;
    color: var(--preview-color-text-inverse) !important;
    border: none !important;
}

body .theme-preview-container .theme-button-success .q-btn__content {
    background: transparent !important;
    color: var(--preview-color-text-inverse) !important;
}

body .theme-preview-container .theme-button-warning,
body .theme-preview-container .q-btn.theme-button-warning {
    background: var(--preview-color-warning) !important;
    color: var(--preview-color-text-inverse) !important;
    border: none !important;
}

body .theme-preview-container .theme-button-warning .q-btn__content {
    background: transparent !important;
    color: var(--preview-color-text-inverse) !important;
}

body .theme-preview-container .theme-button-error,
body .theme-preview-container .q-btn.theme-button-error {
    background: var(--preview-color-error) !important;
    color: var(--preview-color-text-inverse) !important;
    border: none !important;
}

body .theme-preview-container .theme-button-error .q-btn__content {
    background: transparent !important;
    color: var(--preview-color-text-inverse) !important;
}

/* ============================================
   Status Badges
   ============================================ */

.status-badge-preview {
    display: inline-flex;
    align-items: center;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

.status-badge-preview.success {
    background: rgba(76, 175, 80, 0.15);
    color: var(--preview-color-success) !important;
    border: 1px solid rgba(76, 175, 80, 0.25);
}

.status-badge-preview.warning {
    background: rgba(255, 152, 0, 0.15);
    color: var(--preview-color-warning) !important;
    border: 1px solid rgba(255, 152, 0, 0.25);
}

.status-badge-preview.error {
    background: rgba(244, 67, 54, 0.15);
    color: var(--preview-color-error) !important;
    border: 1px solid rgba(244, 67, 54, 0.25);
}

.status-badge-preview.info {
    background: rgba(33, 150, 243, 0.15);
    color: var(--preview-color-info) !important;
    border: 1px solid rgba(33, 150, 243, 0.25);
}

/* ============================================
   Typography Classes
   ============================================ */

body .theme-preview-container .typography-primary {
    color: var(--preview-color-text-primary) !important;
    font-weight: 600;
}

body .theme-preview-container .typography-secondary {
    color: var(--preview-color-text-secondary) !important;
    font-weight: 400;
}

body .theme-preview-container .typography-muted {
    color: var(--preview-color-text-muted) !important;
    font-weight: 400;
}

/* ============================================
   Color Swatch Classes (CSS-variable driven)
   ============================================ */

.preview-swatch { border-radius: 4px; }
.preview-swatch-primary { background: var(--preview-color-primary) !important; }
.preview-swatch-primary-light { background: var(--preview-color-primary-light) !important; }
.preview-swatch-success { background: var(--preview-color-success) !important; }
.preview-swatch-warning { background: var(--preview-color-warning) !important; }
.preview-swatch-error { background: var(--preview-color-error) !important; }
.preview-swatch-info { background: var(--preview-color-info) !important; }
.preview-swatch-notify-success { background: var(--preview-color-notify-success) !important; }
.preview-swatch-notify-warning { background: var(--preview-color-notify-warning) !important; }
.preview-swatch-notify-error { background: var(--preview-color-notify-error) !important; }
.preview-swatch-notify-info { background: var(--preview-color-notify-info) !important; }
.preview-swatch-bg-base { background: var(--preview-color-bg-base) !important; }
.preview-swatch-bg-elevated { background: var(--preview-color-bg-elevated) !important; }
.preview-swatch-bg-surface { background: var(--preview-color-bg-surface) !important; }
.preview-swatch-text-primary { background: var(--preview-color-text-primary) !important; }
.preview-swatch-text-secondary { background: var(--preview-color-text-secondary) !important; }
.preview-swatch-text-muted { background: var(--preview-color-text-muted) !important; }
.preview-swatch-border-default { background: var(--preview-color-border-default) !important; }
.preview-swatch-text-inverse { background: var(--preview-color-text-inverse) !important; }

/* ============================================
   Grid Helpers
   ============================================ */

.preview-grid-2col {
    display: grid;
    grid-template-columns: 3fr 2fr;
    gap: 8px;
}

.preview-color-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
    gap: 6px;
}
"""


class ThemeTab:
    name = "Theme"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[str] = None
        self.ui_elements: Dict[str, Any] = {}
        self.preview_container = None
        self.preview_elements = {}
        self._themes_loaded = False
        self._preview_css_injected: bool = False

    # ----- helper methods -----
    def _get_theme_manager(self):
        """Get theme manager instance with error handling"""
        try:
            return get_theme_manager()
        except Exception as e:
            from nicegui import ui

            notify(f"Error accessing theme manager: {str(e)}", type="negative")
            return None

    def _get_available_themes(self, theme_manager):
        """Get available themes with error handling"""
        try:
            return theme_manager.get_available_themes() if theme_manager else []
        except Exception as e:
            from nicegui import ui

            notify(f"Error loading themes: {str(e)}", type="negative")
            return []

    def _get_current_theme(self, theme_name=None):
        """Get theme object safely with error handling"""
        try:
            theme_manager = self._get_theme_manager()
            if not theme_manager:
                return None
            return theme_manager.get_theme_by_name(theme_name or self.buffer or "dark")
        except Exception as e:
            from nicegui import ui

            notify(f"Error loading theme: {str(e)}", type="negative")
            return None

    def _safe_get_theme_attribute(self, theme, attribute, default=""):
        """Safely get theme attribute with fallback"""
        try:
            if not theme:
                return default
            return getattr(theme, attribute, default)
        except Exception:
            return default

    def _update_theme_select_options(self, available_themes=None):
        """Update theme select dropdown options safely"""
        try:
            theme_manager = self._get_theme_manager()
            if not theme_manager:
                return False

            available_themes = available_themes or self._get_available_themes(
                theme_manager
            )

            if "theme_select" not in self.ui_elements:
                return False

            # Use simple string list for NiceGUI select (this works reliably)
            select_options = [name for name, display_name in available_themes]
            self.ui_elements["theme_select"].options = select_options

            # Safely set current value if valid
            available_values = select_options  # Already the names
            if self.buffer and self.buffer in available_values:
                self.ui_elements["theme_select"].value = self.buffer
            elif available_values:
                # Set to first available theme if current buffer is invalid
                self.ui_elements["theme_select"].value = available_values[0]

            return True
        except Exception as e:
            from nicegui import ui

            notify(f"Error updating theme options: {str(e)}", type="negative")
            return False

    # ----- lifecycle -----
    def on_enter(self) -> None:
        """Refresh themes list and apply preview when tab is entered"""
        self._load_from_state()
        self._refresh_theme_list()
        self._themes_loaded = True
        self._apply_preview()

    def on_exit(self) -> None:
        """Clean up when leaving tab"""
        pass

    # ------------------------------------------------------------------ #
    #  Build - theme selection controls                                    #
    # ------------------------------------------------------------------ #
    def build(self, parent_container) -> None:
        """Build theme selection UI with live preview"""
        self._load_from_state()

        # Inject static preview CSS once
        ui.add_head_html(f"<style>{THEME_PREVIEW_CSS}</style>")

        # Inject initial preview CSS variables so the first render has correct colors
        initial_theme = self._get_current_theme()
        if initial_theme:
            initial_vars = generate_preview_css_variables(initial_theme)
            ui.add_head_html(
                f'<style id="theme-preview-vars">{initial_vars}</style>'
            )

        with parent_container:
            with ui.element("div").classes("content-section w-full p-3"):
                # Header row
                with ui.row().classes("w-full justify-between items-center mb-2"):
                    ui.label("Theme").classes("text-lg font-bold")
                    help_button(tooltip="Theme selection help")

                # Theme selection row - compact
                with ui.card().classes("w-full p-3 mb-2"):
                    with ui.row().classes("w-full items-center gap-3"):
                        # Load themes and create select element
                        theme_manager = self._get_theme_manager()
                        available_themes = self._get_available_themes(theme_manager)
                        select_options = [
                            name for name, display_name in available_themes
                        ]

                        # Determine valid initial value
                        valid_value = None
                        if self.buffer and self.buffer in select_options:
                            valid_value = self.buffer
                        elif select_options:
                            valid_value = select_options[0]
                            self.buffer = valid_value

                        # Theme selector
                        self.ui_elements["theme_select"] = ui.select(
                            options=select_options,
                            value=valid_value,
                            on_change=lambda e: self._on_theme_change(e.value),
                        ).classes("flex-1").props("dense")

                        # Theme type badge (inline)
                        current_theme = self._get_current_theme()
                        theme_type = "Unknown"
                        display_name = "Unknown"
                        if current_theme:
                            if (
                                hasattr(current_theme, "theme_type")
                                and current_theme.theme_type
                            ):
                                theme_type = current_theme.theme_type.capitalize()
                            if (
                                hasattr(current_theme, "display_name")
                                and current_theme.display_name
                            ):
                                display_name = current_theme.display_name
                            elif (
                                hasattr(current_theme, "name")
                                and current_theme.name
                            ):
                                for name, disp in available_themes:
                                    if name == current_theme.name:
                                        display_name = disp
                                        break

                        self.ui_elements["theme_type_label"] = ui.label(
                            f"{theme_type} ({display_name})"
                        ).classes("text-sm font-medium")

                        # Action buttons
                        ui.button(
                            "Apply", on_click=self.save, icon="check"
                        ).props("color=primary dense").classes("px-3")

                        ui.button(
                            "Create",
                            on_click=lambda: self.open_theme_editor(),
                            icon="palette",
                        ).props("color=secondary dense").classes("px-3")

                        ui.button(
                            "Edit",
                            on_click=lambda: self.open_theme_editor(
                                theme_name=self.buffer
                            ),
                            icon="edit",
                        ).props("color=secondary outline dense").classes("px-3")

                # Preview container
                self.preview_container = ui.element("div").classes(
                    "w-full theme-preview-container"
                )

                # Build initial preview
                self._build_preview_ui(self.preview_container)

    # ------------------------------------------------------------------ #
    #  Mock UI Preview                                                     #
    # ------------------------------------------------------------------ #
    def _build_preview_ui(self, container):
        """Build a mock UI preview that mirrors the actual app layout"""
        if not self.buffer:
            return

        current_theme = self._get_current_theme()
        if not current_theme:
            return

        with container:
            # 1. Mock app tab bar
            self._build_mock_tab_bar()
            self._build_mock_notification_chips()

            # 2. Mock content area with settings sub-tabs and content
            with ui.element("div").classes("mock-content-area"):
                self._build_mock_sub_tabs()

                with ui.element("div").classes("preview-grid-2col").style(
                    "margin-top: 8px;"
                ):
                    # Left: settings card with form elements
                    self._build_mock_settings_card()
                    # Right: connection status card
                    self._build_mock_status_card()

                # Button showcase row
                self._build_mock_buttons()

            # 3. Color palette strip
            self._build_color_palette(current_theme)

    def _build_mock_tab_bar(self):
        """Build mock top-level tab bar matching the real app"""
        tabs = [
            "Activity Feed",
            "Alerts",
            "Source Settings",
            "Source Controls",
            "Connectors",
            "Chatbot",
            "Settings",
        ]
        with ui.element("div").classes("mock-tab-bar"):
            for tab_name in tabs:
                active = "active" if tab_name == "Settings" else ""
                with ui.element("div").classes(f"mock-tab {active}"):
                    ui.html(tab_name)
            with ui.element("div").classes("mock-tab"):
                ui.html("Bell")

    def _build_mock_notification_chips(self):
        """Preview notification toast accents (theme notify colors)."""
        with ui.element("div").classes("mock-nc-preview-row"):
            for cls, label in (
                ("success", "Saved"),
                ("info", "Info"),
                ("warning", "Warning"),
                ("error", "Error"),
            ):
                with ui.element("div").classes(f"mock-nc-chip {cls}"):
                    ui.html(label)

    def _build_mock_sub_tabs(self):
        """Build mock settings sub-tab bar with icons"""
        sub_tabs = [
            ("tune", "App Settings"),
            ("palette", "Theme"),
            ("stream", "Twitch"),
            ("sports_esports", "PSN"),
            ("music_note", "Spotify"),
            ("video_library", "YouTube"),
            ("memory", "Game Hooks"),
            ("storage", "Database"),
            ("analytics", "Statistics"),
            ("info", "About"),
        ]
        with ui.element("div").classes("mock-sub-tab-bar"):
            for icon_name, tab_label in sub_tabs:
                active = "active" if tab_label == "Theme" else ""
                with ui.element("div").classes(f"mock-sub-tab {active}"):
                    ui.html(f'<span class="icon">{icon_name}</span>{tab_label}')

    def _build_mock_settings_card(self):
        """Build mock settings card with form elements"""
        with ui.element("div").classes("mock-settings-card"):
            # Header section (mimics real app header-section)
            with ui.element("div").classes("mock-header-section"):
                ui.label("Application Settings").classes(
                    "text-sm font-semibold typography-primary"
                )
                ui.label("Configure your app preferences").classes(
                    "text-xs typography-secondary"
                ).style("margin-top: 2px;")

            # Form elements
            with ui.column().classes("gap-2 w-full"):
                ui.input(
                    label="Stream Title",
                    placeholder="Enter stream title...",
                ).classes("form-input-preview w-full").props("dense")

                ui.select(
                    options=["English", "Spanish", "French"],
                    label="Language",
                    value="English",
                ).classes("form-input-preview w-full").props("dense")

                with ui.row().classes("items-center gap-4"):
                    ui.switch("Auto-connect", value=True)
                    ui.checkbox("Enable notifications", value=True)

    def _build_mock_status_card(self):
        """Build mock connection status card"""
        with ui.element("div").classes("mock-settings-card"):
            ui.label("Connections").classes(
                "text-sm font-semibold typography-primary"
            ).style("margin-bottom: 6px;")

            connections = [
                ("Twitch", "connected", "Connected"),
                ("Spotify", "connected", "Connected"),
                ("StreamElements", "disconnected", "Disconnected"),
                ("OBS WebSocket", "connected", "Connected"),
                ("PSN", "idle", "Idle"),
            ]
            for service, status, label in connections:
                with ui.element("div").classes("mock-connector-row"):
                    ui.label(service).classes("text-xs typography-primary")
                    with ui.row().classes("items-center gap-0"):
                        ui.element("span").classes(f"mock-status-dot {status}")
                        badge_class = {
                            "connected": "success",
                            "disconnected": "error",
                            "idle": "warning",
                        }.get(status, "info")
                        with ui.element("span").classes(
                            f"status-badge-preview {badge_class}"
                        ):
                            ui.label(label).classes("text-xs")

    def _build_mock_buttons(self):
        """Build button variant showcase row"""
        with ui.element("div").style(
            "display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;"
        ):
            ui.button("Primary").classes("theme-button-primary").props("dense size=sm")
            ui.button("Secondary").classes("theme-button-secondary").props(
                "dense size=sm"
            )
            ui.button("Success").classes("theme-button-success").props("dense size=sm")
            ui.button("Warning").classes("theme-button-warning").props("dense size=sm")
            ui.button("Error").classes("theme-button-error").props("dense size=sm")

    def _build_color_palette(self, current_theme):
        """Build compact color palette strip"""
        with ui.element("div").style(
            "background: var(--preview-color-bg-elevated);"
            "padding: 8px 10px;"
            "border-top: 1px solid var(--preview-color-border-subtle);"
        ):
            ui.label("Color Palette").classes(
                "text-xs font-semibold typography-secondary"
            ).style("margin-bottom: 6px;")

            # All colors in a single compact grid
            swatches = [
                ("Primary", "preview-swatch-primary"),
                ("Success", "preview-swatch-success"),
                ("Warning", "preview-swatch-warning"),
                ("Error", "preview-swatch-error"),
                ("Info", "preview-swatch-info"),
                ("N-Success", "preview-swatch-notify-success"),
                ("N-Warn", "preview-swatch-notify-warning"),
                ("N-Err", "preview-swatch-notify-error"),
                ("N-Info", "preview-swatch-notify-info"),
                ("Base", "preview-swatch-bg-base"),
                ("Elevated", "preview-swatch-bg-elevated"),
                ("Surface", "preview-swatch-bg-surface"),
                ("Text", "preview-swatch-text-primary"),
                ("Secondary", "preview-swatch-text-secondary"),
                ("Muted", "preview-swatch-text-muted"),
                ("Border", "preview-swatch-border-default"),
            ]
            with ui.element("div").classes("preview-color-grid"):
                for label, swatch_class in swatches:
                    with ui.column().classes("items-center gap-0"):
                        ui.element("div").classes(
                            f"preview-swatch {swatch_class}"
                        ).style(
                            "width: 100%; height: 24px; border-radius: 3px;"
                            "border: 1px solid var(--preview-color-border-subtle);"
                        )
                        ui.label(label).classes("typography-muted").style(
                            "font-size: 9px; margin-top: 2px;"
                        )

    # ------------------------------------------------------------------ #
    #  Theme list & change handling                                        #
    # ------------------------------------------------------------------ #
    def _refresh_theme_list(self):
        """Refresh the theme dropdown with available themes"""
        theme_manager = self._get_theme_manager()
        available_themes = self._get_available_themes(theme_manager)

        if "theme_select" in self.ui_elements:
            # Use simple string list for NiceGUI select (this works reliably)
            select_options = [name for name, display_name in available_themes]
            self.ui_elements["theme_select"].options = select_options

            # Safely set value only if buffer is in available themes
            available_values = select_options  # Already the names
            if self.buffer and self.buffer in available_values:
                self.ui_elements["theme_select"].value = self.buffer
            elif available_values:
                # Set to first available theme if current buffer is invalid
                self.ui_elements["theme_select"].value = available_values[0]

    def _on_theme_change(self, new_value):
        """Handle theme selection change with enhanced live preview"""
        if new_value and new_value != self.buffer:
            self.buffer = new_value
            self.dirty = True

            # Update theme type label
            theme = self._get_current_theme(new_value)
            theme_type = "Unknown"
            display_name = "Unknown"

            if theme:
                if hasattr(theme, "theme_type") and theme.theme_type:
                    theme_type = theme.theme_type.capitalize()
                if hasattr(theme, "display_name") and theme.display_name:
                    display_name = theme.display_name
                elif hasattr(theme, "name") and theme.name:
                    # Find display name from available themes
                    theme_manager = self._get_theme_manager()
                    available_themes = self._get_available_themes(theme_manager)
                    for name, disp in available_themes:
                        if name == theme.name:
                            display_name = disp
                            break

            if "theme_type_label" in self.ui_elements:
                self.ui_elements[
                    "theme_type_label"
                ].text = f"{theme_type} ({display_name})"

            # Update live preview
            self._apply_preview()

    # ------------------------------------------------------------------ #
    #  Preview CSS injection                                               #
    # ------------------------------------------------------------------ #
    def _apply_preview(self):
        """Apply live preview by updating CSS variables and rebuilding the mock UI.

        Uses a single <style> element updated via JavaScript to avoid
        accumulating style tags.  The mock UI is then cleared and rebuilt
        so all elements pick up the new CSS variable values.
        """
        if not self.buffer or not self.preview_container:
            return

        theme = self._get_current_theme()
        if not theme:
            return

        # Generate preview CSS variables scoped to .theme-preview-container
        preview_css = generate_preview_css_variables(theme)

        # Escape for JS template literal
        escaped_css = preview_css.replace("\\", "\\\\").replace(
            "`", "\\`"
        ).replace("${", "\\${")

        # Update (or create) a single style element for preview variables
        ui.run_javascript(f"""
            (function() {{
                var s = document.getElementById('theme-preview-vars');
                if (!s) {{
                    s = document.createElement('style');
                    s.id = 'theme-preview-vars';
                    document.head.appendChild(s);
                }}
                s.textContent = `{escaped_css}`;
            }})();
        """)

        # Rebuild the preview UI so new elements use the updated variables
        self.preview_container.clear()
        self._build_preview_ui(self.preview_container)

    # ----- buffer helpers -----
    def _load_from_state(self) -> None:
        """Load current theme from state"""
        app_settings = state_manager.get_app_settings()
        self.buffer = getattr(app_settings, "current_theme", "dark")
        self.dirty = False

    # ----- actions -----
    def save(self) -> None:
        """Apply and persist selected theme"""
        if not self.buffer:
            notify("No theme selected to apply", type="warning")
            return

        try:
            # Update state manager
            state_manager.update_app_setting("current_theme", self.buffer)

            if not state_manager.save_changes():
                notify("Error saving theme setting", type="negative")
                return

            # Apply theme immediately without restart
            from ...mainuiwindow import apply_theme

            apply_theme(self.buffer)

            notify(
                f"Theme '{self.buffer}' applied successfully!",
                type="positive",
                timeout=3000,
            )
            self.dirty = False
        except Exception as e:
            notify(f"Error applying theme: {str(e)}", type="negative")

    def discard(self) -> None:
        """Revert to saved theme"""
        try:
            self._load_from_state()

            if "theme_select" in self.ui_elements:
                self.ui_elements["theme_select"].value = self.buffer

            self._apply_preview()
            self.dirty = False
        except Exception as e:
            notify(f"Error discarding changes: {str(e)}", type="negative")

    # ------------------------------------------------------------------ #
    #  Color field definitions used by the theme editor dialog             #
    # ------------------------------------------------------------------ #
    _COLOR_SECTIONS: list = [
        (
            "Primary Colors",
            [
                ("primary", "Primary"),
                ("primary_hover", "Primary Hover"),
                ("primary_light", "Primary Light"),
            ],
        ),
        (
            "Background Colors",
            [
                ("bg_base", "Background Base"),
                ("bg_elevated", "Background Elevated"),
                ("bg_surface", "Background Surface"),
                ("bg_overlay", "Background Overlay"),
            ],
        ),
        (
            "Text Colors",
            [
                ("text_primary", "Text Primary"),
                ("text_secondary", "Text Secondary"),
                ("text_muted", "Text Muted"),
                ("text_inverse", "Text Inverse"),
            ],
        ),
        (
            "Border Colors",
            [
                ("border_default", "Border Default"),
                ("border_subtle", "Border Subtle"),
                ("border_accent", "Border Accent"),
            ],
        ),
        (
            "Status Colors",
            [
                ("success", "Success"),
                ("warning", "Warning"),
                ("error", "Error"),
                ("info", "Info"),
            ],
        ),
        (
            "Notification Colors",
            [
                ("notify_success", "Notify Success"),
                ("notify_warning", "Notify Warning"),
                ("notify_error", "Notify Error"),
                ("notify_info", "Notify Info"),
            ],
        ),
        (
            "Interactive States",
            [
                ("hover_overlay", "Hover Overlay"),
                ("active_overlay", "Active Overlay"),
                ("focus_ring", "Focus Ring"),
            ],
        ),
    ]

    # ------------------------------------------------------------------ #
    #  Theme editor dialog (create & edit)                                 #
    # ------------------------------------------------------------------ #
    def open_theme_editor(self, theme_name: Optional[str] = None):
        """Open the theme editor dialog.

        Args:
            theme_name: If provided, opens in **edit** mode for that theme.
                        If ``None``, opens in **create** mode.
        """
        theme_manager = get_theme_manager()
        editing = theme_name is not None
        source_theme: Optional[Any] = None

        if editing:
            source_theme = theme_manager.get_theme_by_name(theme_name)
            if source_theme is None:
                notify(f"Theme '{theme_name}' not found", type="negative")
                return

        # Storage for editor UI element references
        editor_ui: Dict[str, Any] = {}

        # ----- helper: resolve initial colour for a field -----
        def _initial_color(field_name: str, src_theme) -> str:
            """Return the raw colour string for *field_name* from *src_theme*,
            falling back to dark-theme defaults."""
            val = getattr(src_theme, field_name, "") if src_theme else ""
            if val:
                return val
            return theme_manager.get_default_colors_for_type("dark").get(
                field_name, ""
            )

        # ----- build dialog -----
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl p-6"):
            title = f"Edit Theme: {source_theme.display_name}" if editing else "Create New Theme"
            ui.label(title).classes("text-2xl font-bold mb-4")

            # -- Metadata row --
            with ui.row().classes("w-full gap-4 mb-4 items-end"):
                with ui.column().classes("flex-1"):
                    ui.label("Theme Name *").classes("text-sm font-medium mb-1")
                    editor_ui["name"] = ui.input(
                        value=source_theme.name if editing else "",
                        placeholder="my_custom_theme",
                    ).props("outlined dense" + (" readonly" if editing else ""))

                with ui.column().classes("flex-1"):
                    ui.label("Display Name *").classes("text-sm font-medium mb-1")
                    editor_ui["display_name"] = ui.input(
                        value=source_theme.display_name if editing else "",
                        placeholder="My Custom Theme",
                    ).props("outlined dense")

                with ui.column().classes("w-40"):
                    ui.label("Type *").classes("text-sm font-medium mb-1")
                    editor_ui["theme_type"] = ui.radio(
                        ["dark", "light"],
                        value=(source_theme.theme_type if editing and source_theme.theme_type else "dark"),
                        on_change=lambda e: self._update_editor_colors(
                            e.value, editor_ui
                        ),
                    ).props("inline dense")

            # -- Base-theme selector (create mode only) --
            if not editing:
                with ui.row().classes("w-full gap-4 mb-4 items-end"):
                    with ui.column().classes("flex-1"):
                        ui.label("Clone colours from").classes(
                            "text-sm font-medium mb-1"
                        )
                        available = theme_manager.get_available_themes()
                        base_options = [name for name, _disp in available]
                        editor_ui["base_theme"] = ui.select(
                            options=base_options,
                            value=base_options[0] if base_options else None,
                            on_change=lambda e: self._populate_colors_from_theme(
                                e.value, editor_ui
                            ),
                        ).props("outlined dense").classes("flex-1")

                # Use base theme for initial colours if available
                base_name = base_options[0] if base_options else None
                source_theme = (
                    theme_manager.get_theme_by_name(base_name)
                    if base_name
                    else None
                )

            # -- Color sections --
            for section_label, fields in self._COLOR_SECTIONS:
                with ui.expansion(section_label, value=True).classes("w-full"):
                    with (
                        ui.element("div")
                        .style(
                            "display:grid; grid-template-columns:repeat(3, 1fr); gap:0.75rem;"
                        )
                        .classes("w-full py-2")
                    ):
                        for field_name, field_label in fields:
                            color_val = _initial_color(field_name, source_theme)
                            self._create_color_input(
                                editor_ui, field_name, field_label, color_val
                            )

            # -- Action buttons --
            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                save_label = "Save Changes" if editing else "Create Theme"
                ui.button(
                    save_label,
                    on_click=lambda: self._save_theme(
                        editor_ui, dialog, editing=editing
                    ),
                ).props("color=primary")

        dialog.open()

    # ------------------------------------------------------------------ #
    #  Editor helper: populate colours from a chosen base theme            #
    # ------------------------------------------------------------------ #
    def _populate_colors_from_theme(self, theme_name: str, editor_ui: dict):
        """Load all colour values from *theme_name* into the editor buttons."""
        theme_manager = get_theme_manager()
        theme = theme_manager.get_theme_by_name(theme_name)
        if not theme:
            return
        # Update the theme-type radio to match
        editor_ui["theme_type"].value = theme.theme_type or "dark"
        self._set_editor_colors_from_theme(theme, editor_ui)

    def _set_editor_colors_from_theme(self, theme, editor_ui: dict):
        """Push colour values from a ThemeColors object into editor buttons."""
        theme_manager = get_theme_manager()
        default_colors = theme_manager.get_default_colors_for_type(
            theme.theme_type or "dark"
        )
        for _section_label, fields in self._COLOR_SECTIONS:
            for field_name, _field_label in fields:
                raw = getattr(theme, field_name, "") or default_colors.get(
                    field_name, ""
                )
                self._set_color_button(editor_ui, field_name, raw)

    def _update_editor_colors(self, theme_type: str, editor_ui: dict):
        """When the user toggles dark/light, reset colours to that type's defaults."""
        theme_manager = get_theme_manager()
        defaults = theme_manager.get_default_colors_for_type(theme_type)
        for field_name, raw_value in defaults.items():
            self._set_color_button(editor_ui, field_name, raw_value)

    # ------------------------------------------------------------------ #
    #  Colour button helpers                                               #
    # ------------------------------------------------------------------ #
    def _get_contrast_border_color(self, hex_color: str) -> str:
        """Calculate a contrasting border color based on the background color."""
        if not hex_color or not hex_color.startswith("#"):
            return "#cccccc"
        try:
            hc = hex_color.lstrip("#")
            if len(hc) == 3:
                hc = "".join(c * 2 for c in hc)
            r, g, b = int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
            brightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            return "#666666" if brightness > 0.5 else "#cccccc"
        except Exception:
            return "#cccccc"

    def _button_style(self, hex_color: str) -> str:
        """Return the inline style string for a colour-swatch button."""
        border = self._get_contrast_border_color(hex_color)
        return (
            f"background-color: {hex_color}; min-width: 40px; width: 40px; "
            f"height: 40px; border-radius: 4px; border: 2px solid {border}; "
            "box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 0;"
        )

    def _create_color_input(
        self, editor_ui: dict, field_name: str, label: str, default_value: str = ""
    ):
        """Create a labelled colour-swatch button that opens a colour picker."""
        hex_default = self._convert_to_hex(default_value)

        # Store both hex and original-format values
        editor_ui[f"{field_name}_hex"] = hex_default
        editor_ui[f"{field_name}_rgb"] = default_value if default_value else hex_default

        with ui.row().classes("items-center gap-2"):
            color_button = (
                ui.button()
                .style(self._button_style(hex_default))
                .props("flat dense")
                .on(
                    "click",
                    lambda _, fn=field_name: self._show_color_picker_dialog(
                        editor_ui, fn
                    ),
                )
            )
            editor_ui[field_name] = color_button
            ui.label(label).classes("text-sm")

    def _convert_to_hex(self, color_str: str) -> str:
        """Convert RGB/RGBA color strings to hex format for NiceGUI color input.

        Handles rgb(), rgba(), hex, and shorthand hex formats.
        For rgba() values the alpha channel is discarded (hex has no alpha).
        """
        if not color_str:
            return "#000000"

        color_str = color_str.strip()

        # If already hex, return as-is (normalise short-hand)
        if color_str.startswith("#"):
            hex_clean = color_str.lstrip("#")
            if len(hex_clean) == 3:
                hex_clean = "".join(c * 2 for c in hex_clean)
            return f"#{hex_clean[:6]}"

        # Handle rgba() format first (more specific match before rgb)
        if color_str.startswith("rgba("):
            try:
                inner = color_str[5:].rstrip(")")
                parts = [p.strip() for p in inner.split(",")]
                r = int(float(parts[0]))
                g = int(float(parts[1]))
                b = int(float(parts[2]))
                # Clamp to 0-255
                r, g, b = (max(0, min(255, v)) for v in (r, g, b))
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return "#000000"

        # Handle rgb() format
        if color_str.startswith("rgb("):
            try:
                inner = color_str[4:].rstrip(")")
                parts = [p.strip() for p in inner.split(",")]
                r = int(float(parts[0]))
                g = int(float(parts[1]))
                b = int(float(parts[2]))
                r, g, b = (max(0, min(255, v)) for v in (r, g, b))
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return "#000000"

        # Fallback - bare hex without #
        stripped = color_str.lstrip("#")
        if len(stripped) == 6:
            try:
                int(stripped, 16)
                return f"#{stripped}"
            except ValueError:
                pass

        return "#000000"

    def _hex_to_rgb(self, hex_color: str) -> str:
        """Convert hex color to RGB string format."""
        if not hex_color or not hex_color.startswith("#"):
            return hex_color
        try:
            hc = hex_color.lstrip("#")
            if len(hc) == 3:
                hc = "".join(c * 2 for c in hc)
            r, g, b = int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
            return f"rgb({r}, {g}, {b})"
        except Exception:
            return hex_color

    def _set_color_button(
        self, editor_ui: dict, field_name: str, raw_value: str
    ):
        """Update a single colour-swatch button and its stored values."""
        hex_val = self._convert_to_hex(raw_value)
        editor_ui[f"{field_name}_hex"] = hex_val
        editor_ui[f"{field_name}_rgb"] = raw_value if raw_value else hex_val
        if field_name in editor_ui:
            editor_ui[field_name].style(self._button_style(hex_val))

    # ------------------------------------------------------------------ #
    #  Colour picker sub-dialog                                            #
    # ------------------------------------------------------------------ #
    def _show_color_picker_dialog(self, editor_ui: dict, field_name: str):
        """Show a dialog with NiceGUI's color picker for the specified field."""
        current_hex = editor_ui.get(f"{field_name}_hex", "#000000")
        display_name = field_name.replace("_", " ").title()

        with ui.dialog() as picker_dlg, ui.card().classes("p-4"):
            ui.label(f"Select {display_name} Color").classes(
                "text-lg font-semibold mb-4"
            )
            color_input = (
                ui.color_input(value=current_hex)
                .props("outlined")
                .classes("w-full")
            )

            with ui.row().classes("gap-2 mt-4 justify-end"):
                ui.button("Cancel", on_click=picker_dlg.close).props("flat")

                def _apply(
                    _evt=None,
                    _ci=color_input,
                    _fn=field_name,
                    _dlg=picker_dlg,
                ):
                    new_hex = _ci.value
                    if new_hex and new_hex.startswith("#"):
                        rgb_val = self._hex_to_rgb(new_hex)
                        editor_ui[f"{_fn}_hex"] = new_hex
                        editor_ui[f"{_fn}_rgb"] = rgb_val
                        editor_ui[_fn].style(self._button_style(new_hex))
                    _dlg.close()

                ui.button("Apply", on_click=_apply).props("color=primary")

        picker_dlg.open()

    # ------------------------------------------------------------------ #
    #  Save / update theme                                                 #
    # ------------------------------------------------------------------ #
    def _save_theme(self, editor_ui: dict, dialog, *, editing: bool = False):
        """Validate and save (create or update) the theme from the editor."""
        import re

        theme_manager = get_theme_manager()

        name = editor_ui["name"].value.strip()
        display_name = editor_ui["display_name"].value.strip()
        theme_type = editor_ui["theme_type"].value

        # --- validation ---
        if not name:
            notify("Theme name is required", type="negative")
            return
        if not display_name:
            notify("Display name is required", type="negative")
            return
        if not re.match(r"^[a-zA-Z0-9_]+$", name):
            notify(
                "Theme name can only contain letters, numbers, and underscores",
                type="negative",
            )
            return
        if not editing and theme_manager.theme_name_exists(name):
            notify(f"Theme '{name}' already exists", type="negative")
            return

        # --- build ThemeColors from editor state ---
        def _cv(fn: str) -> str:
            return editor_ui.get(f"{fn}_rgb", "")

        theme_obj = ThemeColors(
            name=name,
            display_name=display_name,
            theme_type=theme_type,
            primary=_cv("primary"),
            primary_hover=_cv("primary_hover"),
            primary_light=_cv("primary_light"),
            bg_base=_cv("bg_base"),
            bg_elevated=_cv("bg_elevated"),
            bg_surface=_cv("bg_surface"),
            bg_overlay=_cv("bg_overlay"),
            text_primary=_cv("text_primary"),
            text_secondary=_cv("text_secondary"),
            text_muted=_cv("text_muted"),
            text_inverse=_cv("text_inverse"),
            border_default=_cv("border_default"),
            border_subtle=_cv("border_subtle"),
            border_accent=_cv("border_accent"),
            success=_cv("success"),
            warning=_cv("warning"),
            error=_cv("error"),
            info=_cv("info"),
            notify_success=_cv("notify_success"),
            notify_warning=_cv("notify_warning"),
            notify_error=_cv("notify_error"),
            notify_info=_cv("notify_info"),
            hover_overlay=_cv("hover_overlay"),
            active_overlay=_cv("active_overlay"),
            focus_ring=_cv("focus_ring"),
        )

        # --- persist ---
        if editing:
            ok = theme_manager.update_custom_theme(theme_obj)
        else:
            ok = theme_manager.save_custom_theme(theme_obj)

        if ok:
            verb = "updated" if editing else "created"
            notify(
                f"Theme '{display_name}' {verb} successfully!", type="positive"
            )
            # Refresh the theme selector dropdown (use plain string list)
            available_themes = theme_manager.get_available_themes()
            if "theme_select" in self.ui_elements:
                select_options = [n for n, _d in available_themes]
                self.ui_elements["theme_select"].options = select_options
                self.ui_elements["theme_select"].update()
            # If editing the currently-previewed theme, refresh preview
            if editing and self.buffer == name:
                self._apply_preview()
            dialog.close()
        else:
            notify("Failed to save theme", type="negative")

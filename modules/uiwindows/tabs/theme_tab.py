from __future__ import annotations

import copy
import re
from typing import Dict, Any, Optional, Callable, List, Tuple

from nicegui import ui

from ...dataobjects import state_manager
from ...theme_manager import (
    PROTECTED_THEMES,
    get_theme_manager,
    generate_preview_css_variables,
    ThemeColors,
)
from ..service_brand_icons import SERVICE_BRAND_SVG
from ...notification_engine import notify
from ...ui_buttons import apply_flat_btn_props, outline_button, primary_button
from ...ui_form_controls import form_input, form_select
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

.mock-main-tab-shell {
    display: flex;
    flex-direction: column;
    width: 100%;
}

.mock-tab-bar-row {
    display: flex;
    align-items: flex-end;
    gap: 4px;
    background: transparent;
    padding: 0;
}

.mock-tab-bar {
    display: flex;
    gap: 2px;
    flex: 1;
    min-width: 0;
    align-items: flex-end;
    overflow-x: auto;
    background: transparent;
    border: none;
    padding: 0;
}

.mock-notification-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 32px;
    margin-bottom: 4px;
    border-radius: 50%;
    background: var(--preview-color-bg-surface);
    border: 1px solid var(--preview-color-border-subtle);
    color: var(--preview-color-text-secondary) !important;
    cursor: default;
    user-select: none;
}

.mock-notification-btn .icon {
    font-family: 'Material Icons';
    font-size: 16px;
    font-weight: normal;
    font-style: normal;
}

.mock-tab {
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 500;
    color: var(--preview-color-text-muted, var(--preview-color-text-secondary)) !important;
    border-radius: 8px 8px 0 0;
    border: none;
    border-bottom: 1px solid var(--preview-color-border-subtle);
    background: var(--preview-color-bg-surface);
    white-space: nowrap;
    cursor: default;
    user-select: none;
    position: relative;
    z-index: 1;
}

.mock-tab.active {
    background: var(--preview-color-bg-elevated);
    border-top: 1px solid var(--preview-color-border-accent);
    border-left: 1px solid var(--preview-color-border-accent);
    border-right: 1px solid var(--preview-color-border-accent);
    border-bottom: none;
    color: var(--preview-color-primary) !important;
    font-weight: 600;
    z-index: 6;
    margin-bottom: -1px;
}

.mock-sub-tab-shell {
    display: flex;
    flex-direction: column;
    width: 100%;
}

.mock-sub-tab-bar {
    display: flex;
    gap: 2px;
    align-items: flex-end;
    background: transparent;
    border-bottom: none;
    padding: 0;
    overflow-x: auto;
}

.mock-sub-tab {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 5px 10px;
    font-size: 10px;
    font-weight: 500;
    color: var(--preview-color-text-muted, var(--preview-color-text-secondary)) !important;
    border-radius: 6px 6px 0 0;
    border: none;
    border-bottom: 1px solid var(--preview-color-border-subtle);
    background: var(--preview-color-bg-surface);
    margin-bottom: 0;
    white-space: nowrap;
    cursor: default;
    user-select: none;
    position: relative;
    z-index: 1;
}

.mock-sub-tab.active {
    background: var(--preview-color-bg-elevated);
    border-top: 1px solid var(--preview-color-border-accent);
    border-left: 1px solid var(--preview-color-border-accent);
    border-right: 1px solid var(--preview-color-border-accent);
    border-bottom: none;
    color: var(--preview-color-primary) !important;
    font-weight: 600;
    z-index: 6;
    margin-bottom: -1px;
}

.mock-sub-tab-content {
    border: 1px solid var(--preview-color-border-accent);
    border-radius: 8px;
    background: var(--preview-color-bg-elevated);
    padding: 10px;
    position: relative;
    z-index: 2;
    margin-top: -1px;
    box-sizing: border-box;
}

/* Theme preview: mask top border under active mock tab (Theme is ~8th tab) */
.mock-sub-tab-shell .mock-sub-tab-content::before {
    content: '';
    position: absolute;
    top: 0;
    left: 42%;
    width: 14%;
    height: 2px;
    background: var(--preview-color-bg-elevated);
    z-index: 5;
    pointer-events: none;
}

.mock-sub-tab .icon {
    font-family: 'Material Icons';
    font-size: 13px;
    font-weight: normal;
    font-style: normal;
}

.mock-sub-tab .brand-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 1.25em;
    height: 1.25em;
}

.mock-sub-tab .brand-icon svg {
    width: 1.25em;
    height: 1.25em;
    display: block;
}

/* ============================================
   Mock UI - Content Area
   ============================================ */

.mock-content-area {
    border: 1px solid var(--preview-color-border-accent);
    border-radius: 10px;
    background: var(--preview-color-bg-elevated);
    padding: 10px;
    position: relative;
    z-index: 2;
    margin-top: -1px;
    box-sizing: border-box;
}

/* Theme preview: mask top border under active main tab (Settings is last) */
.mock-main-tab-shell > .mock-content-area::before {
    content: '';
    position: absolute;
    top: 0;
    left: 78%;
    width: 14%;
    height: 2px;
    background: var(--preview-color-bg-elevated);
    z-index: 5;
    pointer-events: none;
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

/* Neutralize Quasar semantic fills so preview vars win over applied --q-* */
body .theme-preview-container .q-btn.bg-primary,
body .theme-preview-container .q-btn.bg-positive,
body .theme-preview-container .q-btn.bg-negative,
body .theme-preview-container .q-btn.bg-warning,
body .theme-preview-container .q-btn.bg-info {
    background: unset !important;
}

body .theme-preview-container .q-btn .q-focus-helper {
    background: transparent !important;
}

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

.preview-swatch {
    border-radius: 4px;
    cursor: pointer;
}
.theme-preview-container .preview-swatch:hover,
.theme-editor-panel .preview-swatch:hover {
    outline: 2px solid var(--preview-color-primary);
    outline-offset: 1px;
}
.preview-swatch-primary { background: var(--preview-color-primary) !important; }
.preview-swatch-primary-hover { background: var(--preview-color-primary-hover) !important; }
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
.preview-swatch-bg-overlay { background: var(--preview-color-bg-overlay) !important; }
.preview-swatch-text-primary { background: var(--preview-color-text-primary) !important; }
.preview-swatch-text-secondary { background: var(--preview-color-text-secondary) !important; }
.preview-swatch-text-muted { background: var(--preview-color-text-muted) !important; }
.preview-swatch-text-inverse { background: var(--preview-color-text-inverse) !important; }
.preview-swatch-border-default { background: var(--preview-color-border-default) !important; }
.preview-swatch-border-subtle { background: var(--preview-color-border-subtle) !important; }
.preview-swatch-border-accent { background: var(--preview-color-border-accent) !important; }
.preview-swatch-hover-overlay { background: var(--preview-color-hover-overlay) !important; }
.preview-swatch-active-overlay { background: var(--preview-color-active-overlay) !important; }
.preview-swatch-focus-ring { background: var(--preview-color-focus-ring) !important; }

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
    grid-template-columns: repeat(auto-fill, minmax(64px, 1fr));
    gap: 6px;
}

/* Editor chrome below mock preview — uses live app theme, not preview vars */
.theme-editor-panel {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-default);
    border-radius: 4px;
    margin-top: 12px;
    padding: 12px 14px;
}

.theme-editor-panel .typography-secondary,
.theme-editor-panel .typography-muted {
    color: var(--color-text-secondary) !important;
}

.theme-editor-panel .editor-section-title {
    color: var(--color-text-primary);
    font-size: 0.875rem;
    font-weight: 600;
    margin-bottom: 8px;
}

.palette-toolbar {
    display: flex;
    gap: 6px;
    margin-top: 8px;
    justify-content: flex-end;
    flex-wrap: wrap;
}
"""

# Color field definitions for the palette editor
_COLOR_SECTIONS: List[Tuple[str, List[Tuple[str, str]]]] = [
    (
        "Primary Colors",
        [
            ("primary", "Primary"),
            ("primary_hover", "P-Hover"),
            ("primary_light", "P-Light"),
        ],
    ),
    (
        "Background Colors",
        [
            ("bg_base", "Base"),
            ("bg_elevated", "Elevated"),
            ("bg_surface", "Surface"),
            ("bg_overlay", "Overlay"),
        ],
    ),
    (
        "Text Colors",
        [
            ("text_primary", "Text"),
            ("text_secondary", "Secondary"),
            ("text_muted", "Muted"),
            ("text_inverse", "Inverse"),
        ],
    ),
    (
        "Border Colors",
        [
            ("border_default", "Border"),
            ("border_subtle", "B-Subtle"),
            ("border_accent", "B-Accent"),
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
            ("notify_success", "N-Success"),
            ("notify_warning", "N-Warn"),
            ("notify_error", "N-Err"),
            ("notify_info", "N-Info"),
        ],
    ),
    (
        "Interactive States",
        [
            ("hover_overlay", "Hover"),
            ("active_overlay", "Active"),
            ("focus_ring", "Focus"),
        ],
    ),
]

PALETTE_SWATCHES: List[Tuple[str, str, str]] = [
    (
        field_name,
        label,
        f"preview-swatch-{field_name.replace('_', '-')}",
    )
    for _section_label, fields in _COLOR_SECTIONS
    for field_name, label in fields
]

_PALETTE_COLOR_FIELDS: Tuple[str, ...] = tuple(
    field_name for field_name, _label, _css in PALETTE_SWATCHES
)

_MAX_PALETTE_HISTORY = 50


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
        self._working_theme: Optional[ThemeColors] = None
        self._palette_dirty: bool = False
        self.palette_container = None
        self._undo_stack: List[ThemeColors] = []
        self._redo_stack: List[ThemeColors] = []

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

    def _sync_working_theme(self) -> None:
        """Copy the selected theme into the in-memory working editor state."""
        theme = self._get_current_theme()
        if theme:
            self._working_theme = copy.deepcopy(theme)
        else:
            self._working_theme = None
        self._palette_dirty = False
        self._reset_palette_history()

    def _reset_palette_history(self) -> None:
        """Clear undo/redo stacks (e.g. after theme switch or discard)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_palette_toolbar_state()

    def _push_undo_snapshot(self) -> None:
        """Save current working theme on the undo stack before a palette edit."""
        if not self._working_theme:
            return
        self._undo_stack.append(copy.deepcopy(self._working_theme))
        if len(self._undo_stack) > _MAX_PALETTE_HISTORY:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _palette_matches_disk(self) -> bool:
        """True when working palette colors match the persisted theme file."""
        if not self._working_theme or not self.buffer:
            return True
        disk = self._get_current_theme(self.buffer)
        if not disk:
            return True
        for field in _PALETTE_COLOR_FIELDS:
            if getattr(self._working_theme, field, "") != getattr(disk, field, ""):
                return False
        return True

    def _refresh_dirty_from_palette(self) -> None:
        """Sync dirty flags from whether the palette differs from disk / app state."""
        self._palette_dirty = not self._palette_matches_disk()
        app_settings = state_manager.get_app_settings()
        saved_theme = getattr(app_settings, "current_theme", "dark")
        self.dirty = self._palette_dirty or self.buffer != saved_theme

    def _update_palette_toolbar_state(self) -> None:
        """Enable or disable undo, redo, and discard palette controls."""
        undo_btn = self.ui_elements.get("palette_undo_btn")
        redo_btn = self.ui_elements.get("palette_redo_btn")
        discard_btn = self.ui_elements.get("palette_discard_btn")
        if undo_btn is not None:
            if self._undo_stack:
                undo_btn.props(remove="disable")
            else:
                undo_btn.props("disable")
        if redo_btn is not None:
            if self._redo_stack:
                redo_btn.props(remove="disable")
            else:
                redo_btn.props("disable")
        if discard_btn is not None:
            if self._palette_dirty:
                discard_btn.props(remove="disable")
            else:
                discard_btn.props("disable")

    def _undo_palette(self) -> None:
        """Revert the last palette color change."""
        if not self._undo_stack or not self._working_theme:
            return
        self._redo_stack.append(copy.deepcopy(self._working_theme))
        self._working_theme = self._undo_stack.pop()
        self._refresh_dirty_from_palette()
        self._apply_preview()

    def _redo_palette(self) -> None:
        """Re-apply a palette change that was undone."""
        if not self._redo_stack:
            return
        if self._working_theme:
            self._undo_stack.append(copy.deepcopy(self._working_theme))
        self._working_theme = self._redo_stack.pop()
        self._refresh_dirty_from_palette()
        self._apply_preview()

    def _discard_palette_edits(self) -> None:
        """Reload palette colors from the selected theme on disk."""
        theme = self._get_current_theme(self.buffer)
        if theme:
            self._working_theme = copy.deepcopy(theme)
        self._reset_palette_history()
        self._refresh_dirty_from_palette()
        self._apply_preview()
        notify("Palette changes discarded", type="info")

    def _get_preview_theme(self) -> Optional[ThemeColors]:
        """Theme object used for mock-ui preview (working copy preferred)."""
        return self._working_theme or self._get_current_theme()

    def _mark_palette_dirty(self) -> None:
        self._refresh_dirty_from_palette()

    def _update_theme_type_label(self) -> None:
        """Refresh the theme type badge next to the combobox."""
        if "theme_type_label" not in self.ui_elements:
            return
        theme = self._get_preview_theme()
        theme_type = "Unknown"
        display_name = "Unknown"
        if theme:
            if hasattr(theme, "theme_type") and theme.theme_type:
                theme_type = theme.theme_type.capitalize()
            display_name = (
                theme.display_name
                or theme.name
                or self.buffer
                or "Unknown"
            )
        self.ui_elements["theme_type_label"].text = (
            f"{theme_type} ({display_name})"
        )

    def _build_theme_select_options(self, available_themes):
        """Build {internal_name: display_label} dict for the theme dropdown."""
        return {
            name: (display_name or name.replace("_", " ").title())
            for name, display_name in available_themes
        }

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

            select_options = self._build_theme_select_options(available_themes)
            self.ui_elements["theme_select"].options = select_options

            # Safely set current value if valid
            available_values = list(select_options)
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
        self._sync_working_theme()
        self._update_delete_button_state()
        self._update_theme_type_label()
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
            with ui.column().classes("w-full gap-3 settings-tab-content p-3"):
                # Theme selection row - compact
                with ui.card().classes("w-full p-3 mb-2"):
                    with ui.row().classes("w-full items-center gap-3"):
                        # Load themes and create select element
                        theme_manager = self._get_theme_manager()
                        available_themes = self._get_available_themes(theme_manager)
                        select_options = self._build_theme_select_options(
                            available_themes
                        )

                        # Determine valid initial value
                        valid_value = None
                        if self.buffer and self.buffer in select_options:
                            valid_value = self.buffer
                        elif select_options:
                            valid_value = next(iter(select_options))
                            self.buffer = valid_value

                        # Theme selector
                        self.ui_elements["theme_select"] = form_select(
                            tooltip="Color theme applied across the Mycelian interface",
                            options=select_options,
                            value=valid_value,
                            classes="flex-1",
                            on_change=lambda e: self._on_theme_change(e.value),
                        )

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
                            "Save as",
                            on_click=self._open_save_as_dialog,
                            icon="drive_file_rename_outline",
                        ).props("color=secondary dense").classes("px-3")

                        delete_btn = ui.button(
                            "Delete",
                            on_click=self._confirm_delete_theme,
                            icon="delete",
                        ).props("color=negative outline dense").classes("px-3")
                        if self.buffer in PROTECTED_THEMES:
                            delete_btn.props("disable")
                        self.ui_elements["delete_btn"] = delete_btn

                self._sync_working_theme()

                with ui.element("div").classes("w-full theme-preview-container"):
                    self.preview_container = ui.element("div").classes("w-full")
                    self._build_preview_ui(self.preview_container)

                self.palette_container = ui.element("div").classes(
                    "w-full theme-editor-panel"
                )
                self._build_palette_section()

    # ------------------------------------------------------------------ #
    #  Mock UI Preview                                                     #
    # ------------------------------------------------------------------ #
    def _build_preview_ui(self, container):
        """Build a mock UI preview that mirrors the actual app layout"""
        if not self.buffer:
            return

        current_theme = self._get_preview_theme()
        if not current_theme:
            return

        with container:
            with ui.element("div").classes("mock-main-tab-shell"):
                # 1. Mock app tab bar
                self._build_mock_tab_bar()

                # 2. Mock content area with settings sub-tabs and content
                with ui.element("div").classes("mock-content-area"):
                    self._build_mock_notification_chips()

                    with ui.element("div").classes("mock-sub-tab-shell"):
                        self._build_mock_sub_tabs()

                        with ui.element("div").classes("mock-sub-tab-content"):
                            with ui.element("div").classes("preview-grid-2col"):
                                # Left: settings card with form elements
                                self._build_mock_settings_card()
                                # Right: connection status card
                                self._build_mock_status_card()

                            # Button showcase row
                            self._build_mock_buttons()

    def _build_palette_section(self) -> None:
        """Build the color palette and editor toolbar below the mock preview."""
        if not self.palette_container:
            return
        current_theme = self._get_preview_theme()
        if not current_theme:
            return
        self.palette_container.clear()
        with self.palette_container:
            ui.label("Edit theme colors").classes("editor-section-title")
            ui.label(
                "Click a swatch to change a color. Changes preview above until you Apply."
            ).classes("text-xs typography-secondary mb-2")
            self._build_color_palette(current_theme)
            self._build_palette_toolbar()

    def _build_mock_tab_bar(self):
        """Build mock top-level tab bar matching the real app"""
        tabs = [
            "Activity Feed",
            "Alerts",
            "Source Settings",
            "Source Controls",
            "Connectors",
            "Chatbot",
            "Spore Studio",
            "Settings",
        ]
        with ui.element("div").classes("mock-tab-bar-row"):
            with ui.element("div").classes("mock-tab-bar"):
                for tab_name in tabs:
                    active = "active" if tab_name == "Settings" else ""
                    with ui.element("div").classes(f"mock-tab {active}"):
                        ui.html(tab_name)
            with ui.element("div").classes("mock-notification-btn"):
                ui.html('<span class="icon">notifications</span>')

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
        """Build mock settings sub-tab bar matching build_ui_v2 order and icons."""
        sub_tabs = [
            ("brand", "twitch", "Twitch"),
            ("brand", "obs", "OBS"),
            ("brand", "psn", "PSN"),
            ("brand", "spotify", "Spotify"),
            ("brand", "youtube", "YouTube"),
            ("material", "memory", "Game Hooks"),
            ("material", "storage", "Database"),
            ("material", "analytics", "Statistics"),
            ("material", "palette", "Theme"),
            ("material", "tune", "App Settings"),
            ("material", "info", "About"),
        ]
        with ui.element("div").classes("mock-sub-tab-bar"):
            for icon_kind, icon_key, tab_label in sub_tabs:
                active = "active" if tab_label == "Theme" else ""
                with ui.element("div").classes(f"mock-sub-tab {active}"):
                    if icon_kind == "brand":
                        svg = SERVICE_BRAND_SVG[icon_key]
                        ui.html(
                            f'<span class="brand-icon">{svg}</span>{tab_label}'
                        )
                    else:
                        ui.html(
                            f'<span class="icon">{icon_key}</span>{tab_label}'
                        )

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
            for label, cls in (
                ("Primary", "theme-button-primary"),
                ("Secondary", "theme-button-secondary"),
                ("Success", "theme-button-success"),
                ("Warning", "theme-button-warning"),
                ("Error", "theme-button-error"),
            ):
                btn = ui.button(label).classes(cls).props("dense size=sm")
                apply_flat_btn_props(btn, dense=True)

    def _build_color_palette(self, current_theme):
        """Build clickable color palette swatches."""
        ui.label("Color swatches").classes(
            "text-xs font-semibold typography-secondary"
        ).style("margin-bottom: 6px;")

        with ui.element("div").classes("preview-color-grid"):
            for field_name, label, swatch_class in PALETTE_SWATCHES:
                raw_color = getattr(current_theme, field_name, "") or ""
                hex_color = self._convert_to_hex(raw_color)
                border = self._get_contrast_border_color(hex_color)
                with ui.column().classes("items-center gap-0"):
                    swatch = ui.element("div").classes(
                        f"preview-swatch {swatch_class}"
                    ).style(
                        f"width: 100%; height: 24px; border-radius: 3px;"
                        f"background: {raw_color or hex_color};"
                        f"border: 1px solid {border};"
                    )
                    swatch.on(
                        "click",
                        lambda _, fn=field_name: self._open_palette_color_picker(fn),
                    )
                    ui.label(label).classes("typography-muted").style(
                        "font-size: 9px; margin-top: 2px;"
                    )

    def _build_palette_toolbar(self) -> None:
        """Undo, redo, and discard controls below the color palette."""
        with ui.row().classes("palette-toolbar w-full"):
            undo_btn = outline_button(
                "Undo",
                self._undo_palette,
                icon="undo",
                extra_classes="px-3 py-1",
            )
            redo_btn = outline_button(
                "Redo",
                self._redo_palette,
                icon="redo",
                extra_classes="px-3 py-1",
            )
            discard_btn = outline_button(
                "Discard",
                self._discard_palette_edits,
                icon="restore",
                extra_classes="px-3 py-1",
            )
            self.ui_elements["palette_undo_btn"] = undo_btn
            self.ui_elements["palette_redo_btn"] = redo_btn
            self.ui_elements["palette_discard_btn"] = discard_btn
        self._update_palette_toolbar_state()

    # ------------------------------------------------------------------ #
    #  Theme list & change handling                                        #
    # ------------------------------------------------------------------ #
    def _refresh_theme_list(self):
        """Refresh the theme dropdown with available themes"""
        self._refresh_theme_list_after_change(select_name=self.buffer)

    def _on_theme_change(self, new_value):
        """Handle theme selection change with enhanced live preview"""
        if new_value and new_value != self.buffer:
            self.buffer = new_value
            self._sync_working_theme()
            self._refresh_dirty_from_palette()
            self._update_delete_button_state()
            self._update_theme_type_label()
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

        theme = self._get_preview_theme()
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

        # Rebuild mock preview and refresh palette swatches
        self.preview_container.clear()
        self._build_preview_ui(self.preview_container)
        self._build_palette_section()

    # ----- buffer helpers -----
    def _load_from_state(self) -> None:
        """Load current theme from state"""
        app_settings = state_manager.get_app_settings()
        self.buffer = getattr(app_settings, "current_theme", "dark")
        self.dirty = False

    # ----- actions -----
    def save(self) -> None:
        """Apply and persist selected theme (and palette edits for custom themes)."""
        if not self.buffer:
            notify("No theme selected to apply", type="warning")
            return

        try:
            theme_manager = self._get_theme_manager()
            if (
                self._palette_dirty
                and self._working_theme
                and theme_manager
            ):
                if self.buffer in PROTECTED_THEMES:
                    notify(
                        "Built-in themes cannot be overwritten. Use Save as to "
                        "persist palette changes.",
                        type="warning",
                    )
                else:
                    self._working_theme.name = self.buffer
                    persisted = theme_manager.get_theme_by_name(self.buffer)
                    if persisted:
                        self._working_theme.display_name = persisted.display_name
                        self._working_theme.theme_type = persisted.theme_type
                    if not theme_manager.update_custom_theme(self._working_theme):
                        notify("Failed to save theme color changes", type="negative")
                        return
                    self._reset_palette_history()
                    self._refresh_dirty_from_palette()

            state_manager.update_app_setting("current_theme", self.buffer)

            if not state_manager.save_changes():
                notify("Error saving theme setting", type="negative")
                return

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

            self._sync_working_theme()
            self._update_delete_button_state()
            self._update_theme_type_label()
            self._apply_preview()
            self.dirty = False
        except Exception as e:
            notify(f"Error discarding changes: {str(e)}", type="negative")

    def _update_delete_button_state(self) -> None:
        """Enable or disable Delete based on whether the theme is protected."""
        delete_btn = self.ui_elements.get("delete_btn")
        if delete_btn is None:
            return
        if self.buffer in PROTECTED_THEMES:
            delete_btn.props("disable")
        else:
            delete_btn.props(remove="disable")

    def _refresh_theme_list_after_change(self, select_name: Optional[str] = None) -> None:
        """Refresh combobox options and optionally select a theme by name."""
        theme_manager = self._get_theme_manager()
        if not theme_manager:
            return
        available_themes = self._get_available_themes(theme_manager)
        select_options = self._build_theme_select_options(available_themes)
        if "theme_select" not in self.ui_elements:
            return
        self.ui_elements["theme_select"].options = select_options
        target = select_name or self.buffer
        if target and target in select_options:
            self.ui_elements["theme_select"].value = target
            self.buffer = target
        elif select_options:
            first = next(iter(select_options))
            self.ui_elements["theme_select"].value = first
            self.buffer = first
        self.ui_elements["theme_select"].update()
        self._update_delete_button_state()
        self._update_theme_type_label()

    # ------------------------------------------------------------------ #
    #  Save as / Delete                                                    #
    # ------------------------------------------------------------------ #
    def _open_save_as_dialog(self) -> None:
        """Save the current working theme configuration under a new name."""
        if not self._working_theme:
            notify("No theme loaded to save", type="warning")
            return

        dialog_ui: Dict[str, Any] = {}

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md p-6"):
            ui.label("Save Theme As").classes("text-xl font-bold mb-4")

            with ui.column().classes("w-full gap-3"):
                ui.label("Theme Name *").classes("text-sm font-medium")
                dialog_ui["name"] = form_input(
                    tooltip="Internal theme file name (no spaces)",
                    placeholder="my_custom_theme",
                )

                ui.label("Display Name *").classes("text-sm font-medium")
                dialog_ui["display_name"] = form_input(
                    tooltip="Friendly name shown in the theme picker",
                    placeholder="My Custom Theme",
                )

                ui.label("Type *").classes("text-sm font-medium")
                default_type = self._working_theme.theme_type or "dark"
                dialog_ui["theme_type"] = ui.radio(
                    ["dark", "light"], value=default_type
                ).props("inline dense")

            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Save",
                    on_click=lambda: self._execute_save_as(dialog_ui, dialog),
                ).props("color=primary")

        dialog.open()

    def _execute_save_as(self, dialog_ui: dict, dialog) -> None:
        """Validate and persist a new theme from the working copy."""
        theme_manager = self._get_theme_manager()
        if not theme_manager or not self._working_theme:
            return

        name = dialog_ui["name"].value.strip()
        display_name = dialog_ui["display_name"].value.strip()
        theme_type = dialog_ui["theme_type"].value

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
        if theme_manager.theme_name_exists(name):
            notify(f"Theme '{name}' already exists", type="negative")
            return

        theme_obj = copy.deepcopy(self._working_theme)
        theme_obj.name = name
        theme_obj.display_name = display_name
        theme_obj.theme_type = theme_type

        if not theme_manager.save_custom_theme(theme_obj):
            notify("Failed to save theme", type="negative")
            return

        notify(f"Theme '{display_name}' saved successfully!", type="positive")
        self.buffer = name
        self._working_theme = copy.deepcopy(theme_obj)
        self._reset_palette_history()
        self._refresh_dirty_from_palette()
        self.dirty = True
        self._refresh_theme_list_after_change(select_name=name)
        self._apply_preview()
        dialog.close()

    def _confirm_delete_theme(self) -> None:
        """Show confirmation before deleting the selected theme."""
        if not self.buffer:
            notify("No theme selected", type="warning")
            return
        if self.buffer in PROTECTED_THEMES:
            notify("Built-in themes cannot be deleted", type="warning")
            return

        theme_manager = self._get_theme_manager()
        display_name = self.buffer
        if theme_manager:
            display_name = theme_manager.get_theme_display_name(self.buffer)

        with ui.dialog() as confirm_dialog:
            with ui.card().classes("p-4"):
                with ui.column().classes("gap-3"):
                    ui.label("Delete Theme").classes("text-lg font-bold")
                    ui.label(
                        f'Delete "{display_name}" ({self.buffer})?'
                    ).classes("text-sm")
                    ui.label("This action cannot be undone.").classes(
                        "text-sm text-negative"
                    )
                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button(
                            "Cancel", on_click=confirm_dialog.close
                        ).props("flat")
                        ui.button(
                            "Delete",
                            on_click=lambda: self._execute_delete_theme(
                                confirm_dialog
                            ),
                        ).props("color=negative")

        confirm_dialog.open()

    def _execute_delete_theme(self, dialog) -> None:
        """Delete the selected theme and update UI state."""
        if not self.buffer or self.buffer in PROTECTED_THEMES:
            dialog.close()
            return

        deleted_name = self.buffer
        theme_manager = self._get_theme_manager()
        if not theme_manager:
            notify("Theme manager unavailable", type="negative")
            dialog.close()
            return

        if not theme_manager.delete_theme(deleted_name):
            notify(f"Failed to delete theme '{deleted_name}'", type="negative")
            dialog.close()
            return

        app_settings = state_manager.get_app_settings()
        saved_theme = getattr(app_settings, "current_theme", "dark")
        fallback = "dark"
        if saved_theme == deleted_name:
            state_manager.update_app_setting("current_theme", fallback)
            state_manager.save_changes()

        self.buffer = fallback
        self._sync_working_theme()
        self._refresh_theme_list_after_change(select_name=fallback)
        self._apply_preview()
        self.dirty = False
        self._palette_dirty = False
        notify(f"Theme '{deleted_name}' deleted", type="positive")
        dialog.close()

    # ------------------------------------------------------------------ #
    #  Palette color picker                                                #
    # ------------------------------------------------------------------ #
    def _open_palette_color_picker(self, field_name: str) -> None:
        """Open a color picker for a palette swatch field."""
        if not self._working_theme:
            return
        current = getattr(self._working_theme, field_name, "") or ""
        current_hex = self._convert_to_hex(current)
        display_name = field_name.replace("_", " ").title()

        def on_apply(rgb_value: str) -> None:
            current = getattr(self._working_theme, field_name, "") or ""
            if current == rgb_value:
                return
            self._push_undo_snapshot()
            setattr(self._working_theme, field_name, rgb_value)
            self._mark_palette_dirty()
            self._apply_preview()

        self._show_color_picker_dialog(
            display_name, current_hex, on_apply=on_apply
        )

    # ------------------------------------------------------------------ #
    #  Colour helpers                                                      #
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

    def _show_color_picker_dialog(
        self,
        display_name: str,
        current_hex: str,
        *,
        on_apply: Callable[[str], None],
    ) -> None:
        """Show a color picker dialog and invoke *on_apply* with the chosen RGB value."""
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

                def _apply_color(
                    _evt=None,
                    _ci=color_input,
                    _dlg=picker_dlg,
                    _callback=on_apply,
                ):
                    new_hex = _ci.value
                    if new_hex and new_hex.startswith("#"):
                        _callback(self._hex_to_rgb(new_hex))
                    _dlg.close()

                ui.button("Apply", on_click=_apply_color).props("color=primary")

        picker_dlg.open()

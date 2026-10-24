# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import html
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
from ...notification_engine import notify
from ...ui_buttons import destructive_button, outline_button, primary_button
from ...ui_form_controls import form_input, form_select
from .theme_mock import build_mock_html
from .theme_styles import THEME_CSS


# Color fields shown in the editor: (group, [(field, label, what it colors)])
_COLOR_SECTIONS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (
        "Primary",
        [
            ("primary", "Primary", "Buttons, switches, active tabs and links"),
            ("primary_hover", "Hover", "Primary buttons under the pointer"),
            ("primary_light", "Tint", "Soft highlight behind icons and selected items"),
        ],
    ),
    (
        "Backgrounds",
        [
            ("bg_base", "Base", "Window background"),
            ("bg_elevated", "Elevated", "Panels, tab content and the status bar"),
            ("bg_surface", "Surface", "Cards and inactive tabs"),
            ("bg_overlay", "Overlay", "Dimmed backdrop behind dialogs"),
        ],
    ),
    (
        "Text",
        [
            ("text_primary", "Primary", "Headings and main text"),
            ("text_secondary", "Secondary", "Body text and descriptions"),
            ("text_muted", "Muted", "Hints, units and timestamps"),
            ("text_inverse", "Inverse", "Text on some filled buttons and badges"),
        ],
    ),
    (
        "Borders",
        [
            ("border_default", "Default", "Inputs and outlines"),
            ("border_subtle", "Subtle", "Card edges and row dividers"),
            ("border_accent", "Accent", "Active tabs and outlined buttons"),
        ],
    ),
    (
        "Status",
        [
            ("success", "Success", "Connected, saved and positive values"),
            ("warning", "Warning", "Reconnecting and needs attention"),
            ("error", "Error", "Failures and destructive actions"),
            ("info", "Info", "Connecting and neutral notices"),
        ],
    ),
    (
        "Notifications",
        [
            ("notify_success", "Success", "Pop-up accent for successes"),
            ("notify_warning", "Warning", "Pop-up accent for warnings"),
            ("notify_error", "Error", "Pop-up accent for errors"),
            ("notify_info", "Info", "Pop-up accent for notices"),
        ],
    ),
    (
        "Interaction",
        [
            ("hover_overlay", "Hover", "Rows and items under the pointer"),
            ("active_overlay", "Pressed", "Selected or pressed items"),
            ("focus_ring", "Focus ring", "Glow around the focused input"),
        ],
    ),
]

_PALETTE_COLOR_FIELDS: Tuple[str, ...] = tuple(
    field_name for _group, fields in _COLOR_SECTIONS for field_name, _label, _desc in fields
)

_MAX_PALETTE_HISTORY = 50

_COLOR_RE = re.compile(
    r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)(%?)\s*)?\)", re.I
)


def _parse_color(value: str) -> Optional[Tuple[float, float, float, float]]:
    """Parse ``#rgb``, ``#rrggbb[aa]``, ``rgb()`` or ``rgba()`` into (r, g, b, alpha)."""
    value = (value or "").strip()
    if value.startswith("#"):
        hc = value[1:]
        if len(hc) in (3, 4):
            hc = "".join(c * 2 for c in hc)
        try:
            r, g, b = int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
            a = int(hc[6:8], 16) / 255 if len(hc) == 8 else 1.0
            return float(r), float(g), float(b), a
        except ValueError:
            return None
    m = _COLOR_RE.fullmatch(value)
    if not m:
        return None
    r, g, b = (max(0.0, min(255.0, float(m.group(i)))) for i in (1, 2, 3))
    a = 1.0
    if m.group(4) is not None:
        a = float(m.group(4)) / (100 if m.group(5) else 1)
    return r, g, b, max(0.0, min(1.0, a))


def _over(fg: Tuple[float, ...], bg: Tuple[float, ...]) -> Tuple[float, float, float, float]:
    """Composite ``fg`` (with alpha) over an opaque ``bg``."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)  # type: ignore[return-value]


def _luminance(c: Tuple[float, ...]) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast_ratio(fg: Tuple[float, ...], bg: Tuple[float, ...]) -> float:
    """WCAG contrast ratio between two opaque colors."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readability_checks(theme: ThemeColors) -> List[Tuple[str, str, str, float, str]]:
    """(label, fg css, bg css, ratio, rating) for the theme's key text/background pairs.

    Rating is ``ok`` (meets the target), ``low`` (readable but weak) or ``bad``.
    """
    dark = (theme.theme_type or "dark") != "light"
    canvas = (0.0, 0.0, 0.0, 1.0) if dark else (255.0, 255.0, 255.0, 1.0)

    def solid(value: str, under: Tuple[float, ...]) -> Optional[Tuple[float, ...]]:
        parsed = _parse_color(value)
        return _over(parsed, under) if parsed else None

    base = solid(theme.bg_base, canvas)
    surface = solid(theme.bg_surface, base) if base else None
    primary = solid(theme.primary, surface) if surface else None
    pairs = (
        ("Text", theme.text_primary, surface, theme.bg_surface, 4.5),
        ("Secondary", theme.text_secondary, surface, theme.bg_surface, 4.5),
        ("Hints", theme.text_muted, surface, theme.bg_surface, 3.0),
        # Filled buttons (Quasar color=primary) draw white text.
        ("Buttons", "#ffffff", primary, theme.primary, 4.5),
    )
    out = []
    for label, fg_css, bg, bg_css, target in pairs:
        fg = solid(fg_css, bg) if bg else None
        if not fg or not bg:
            continue
        ratio = contrast_ratio(fg, bg)
        rating = "ok" if ratio >= target else ("low" if ratio >= target - 1.5 else "bad")
        out.append((label, fg_css, bg_css, ratio, rating))
    return out


class ThemeTab:
    name = "Theme"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[str] = None
        self.ui_elements: Dict[str, Any] = {}
        self.preview_container = None
        self.preview_elements = {}
        self._themes_loaded = False
        self._working_theme: Optional[ThemeColors] = None
        self._palette_dirty: bool = False
        self.palette_container = None
        self._undo_stack: List[ThemeColors] = []
        self._redo_stack: List[ThemeColors] = []
        self._vars_el = None
        self._gallery = None
        self._chips: Dict[str, Any] = {}

    # ----- helper methods -----
    def _get_theme_manager(self):
        """Get theme manager instance with error handling"""
        try:
            return get_theme_manager()
        except Exception as e:
            notify(f"Error accessing theme manager: {str(e)}", type="negative")
            return None

    def _get_available_themes(self, theme_manager):
        """Get available themes with error handling"""
        try:
            return theme_manager.get_available_themes() if theme_manager else []
        except Exception as e:
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

    def _applied_theme_name(self) -> str:
        return getattr(state_manager.get_app_settings(), "current_theme", "dark")

    def _display_name(self, name: Optional[str]) -> str:
        theme_manager = self._get_theme_manager()
        if theme_manager and name:
            return theme_manager.get_theme_display_name(name)
        return name or "Unknown"

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
        self.dirty = self._palette_dirty or self.buffer != self._applied_theme_name()

    def _update_palette_toolbar_state(self) -> None:
        """Enable or disable undo, redo, and discard palette controls."""
        for key, enabled in (
            ("palette_undo_btn", bool(self._undo_stack)),
            ("palette_redo_btn", bool(self._redo_stack)),
            ("palette_discard_btn", self._palette_dirty),
        ):
            btn = self.ui_elements.get(key)
            if btn is None:
                continue
            if enabled:
                btn.props(remove="disable")
            else:
                btn.props("disable")

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

    def _build_theme_select_options(self, available_themes):
        """Build {internal_name: display_label} dict for the theme dropdown."""
        return {
            name: (display_name or name.replace("_", " ").title())
            for name, display_name in available_themes
        }

    # ----- lifecycle -----
    def on_enter(self) -> None:
        """Refresh themes list and apply preview when tab is entered"""
        self._load_from_state()
        self._refresh_theme_list()
        self._sync_working_theme()
        self._themes_loaded = True
        self._apply_preview()

    def on_exit(self) -> None:
        """Clean up when leaving tab"""
        pass

    # ------------------------------------------------------------------ #
    #  Build                                                               #
    # ------------------------------------------------------------------ #
    def build(self, parent_container) -> None:
        """Build theme picker, live mock preview and color editor."""
        from ...help_system.help_render import (
            inject_page_stylesheet,
            promote_color_importance,
            scope_selectors,
        )

        inject_page_stylesheet(
            "mycelian-theme-tab", promote_color_importance(scope_selectors(THEME_CSS, ".thm"))
        )
        self._load_from_state()

        theme_manager = self._get_theme_manager()
        available_themes = self._get_available_themes(theme_manager)
        select_options = self._build_theme_select_options(available_themes)
        if not (self.buffer and self.buffer in select_options) and select_options:
            self.buffer = next(iter(select_options))
        self._sync_working_theme()

        with parent_container:
            with ui.element("div").classes("thm"):
                self._build_picker_card(select_options, available_themes)
                self._build_preview_card()
                self._build_colors_card()
                with ui.element("div").classes("thm-actions"):
                    self.ui_elements["_status_label"] = ui.html("", sanitize=False).classes("thm-status")
                    outline_button("Discard", self.discard, icon="undo")
                    primary_button("Apply", self.save, icon="check")

        self._apply_preview()

    @staticmethod
    def _card_head(icon: str, title: str, subtitle: str):
        head = ui.element("div").classes("thm-card-head")
        with head:
            ui.html(f'<span class="thm-icon">{icon}</span>', sanitize=False).classes("thm-card-icon")
            with ui.element("div").classes("thm-card-text"):
                ui.label(title).classes("thm-card-title")
                ui.label(subtitle).classes("thm-card-sub")
        return head

    def _build_picker_card(self, select_options, available_themes) -> None:
        with ui.element("div").classes("thm-card"):
            self._card_head(
                "palette",
                "Theme",
                "Pick a theme to preview it below. Nothing changes until you click Apply.",
            )
            with ui.element("div").classes("thm-picker"):
                self.ui_elements["theme_select"] = form_select(
                    tooltip="Color theme applied across the Mycelian interface",
                    options=select_options,
                    value=self.buffer if self.buffer in select_options else None,
                    classes="w-72",
                    on_change=lambda e: self._on_theme_change(e.value),
                )
                self.ui_elements["theme_type_label"] = ui.html("", sanitize=False).classes(
                    "flex items-center gap-2"
                )
                ui.element("div").classes("thm-spacer")
                outline_button(
                    "Save as",
                    self._open_save_as_dialog,
                    icon="drive_file_rename_outline",
                )
                self.ui_elements["delete_btn"] = destructive_button(
                    "Delete", self._confirm_delete_theme, icon="delete"
                )
            self._gallery = ui.element("div").classes("thm-gallery")
            self._render_gallery(available_themes)

    def _render_gallery(self, available_themes=None) -> None:
        """One clickable thumbnail per theme; clicking selects it in the dropdown."""
        if self._gallery is None:
            return
        theme_manager = self._get_theme_manager()
        if available_themes is None:
            available_themes = self._get_available_themes(theme_manager)
        applied = self._applied_theme_name()
        self._gallery.clear()
        self._chips = {}
        with self._gallery:
            for name, display_name in available_themes:
                theme = theme_manager.get_theme_by_name(name) if theme_manager else None
                if not theme:
                    continue
                chip = ui.element("div").on("click", lambda _, n=name: self._select_theme(n))
                chip.classes("thm-chip")
                with chip:
                    ui.html(self._chip_html(theme, display_name, name == applied), sanitize=False).classes(
                        "contents"
                    )
                self._chips[name] = chip
        self._update_gallery_selection()

    @staticmethod
    def _chip_html(theme: ThemeColors, display_name: str, applied: bool) -> str:
        c = {k: html.escape(getattr(theme, k, "") or "", quote=True) for k in (
            "bg_base", "bg_surface", "bg_elevated", "text_primary", "text_muted", "primary", "border_subtle"
        )}
        mode = "light_mode" if theme.theme_type == "light" else "dark_mode"
        tick = '<span class="thm-icon thm-chip-applied" title="Current theme">check_circle</span>' if applied else ""
        return (
            f'<div class="thm-chip-thumb" style="background:{c["bg_base"]}">'
            f'<div class="thm-chip-side" style="background:{c["bg_surface"]}"></div>'
            f'<div class="thm-chip-main" style="background:{c["bg_elevated"]};border:1px solid {c["border_subtle"]}">'
            f'<div class="thm-chip-line" style="background:{c["text_primary"]};width:70%"></div>'
            f'<div class="thm-chip-line" style="background:{c["text_muted"]};width:50%"></div>'
            f'<div class="thm-chip-btn" style="background:{c["primary"]}"></div></div></div>'
            f'<div class="thm-chip-foot"><span class="thm-icon thm-chip-mode">{mode}</span>'
            f'<span class="thm-chip-name">{html.escape(display_name or theme.name)}</span>{tick}</div>'
        )

    def _update_gallery_selection(self) -> None:
        for name, chip in self._chips.items():
            if name == self.buffer:
                chip.classes(add="is-selected")
            else:
                chip.classes(remove="is-selected")

    def _select_theme(self, name: str) -> None:
        select = self.ui_elements.get("theme_select")
        if select is not None and select.value != name:
            select.value = name  # fires on_change → _on_theme_change

    def _build_preview_card(self) -> None:
        with ui.element("div").classes("thm-card"):
            with self._card_head("visibility", "Preview", "How Mycelian looks with the selected theme and color edits."):
                self.ui_elements["_preview_badge"] = ui.html("", sanitize=False)
            with ui.element("div").classes("thm-mock-wrap"):
                minimal = False
                try:
                    minimal = bool(getattr(state_manager.get_app_settings(), "status_footer_minimal", False))
                except Exception:
                    minimal = False
                with ui.element("div").classes("thm-mock theme-preview-container") as mock:
                    self._vars_el = ui.html("", sanitize=False).classes("hidden")
                    ui.html(build_mock_html(minimal), sanitize=False).classes("contents")
                self.preview_container = mock

    def _build_colors_card(self) -> None:
        with ui.element("div").classes("thm-card"):
            self._card_head(
                "format_color_fill",
                "Colors",
                "Click a color to change it. Edits show in the preview right away.",
            )
            self.ui_elements["_readability"] = ui.html("", sanitize=False).classes("thm-read")
            with ui.element("div").classes("thm-card-body").style("padding-bottom: 12px"):
                self.ui_elements["_palette_note"] = ui.html("", sanitize=False).classes("thm-note")
            self.palette_container = ui.element("div").classes("thm-groups")
            with ui.element("div").classes("thm-toolbar"):
                self.ui_elements["palette_undo_btn"] = outline_button("Undo", self._undo_palette, icon="undo")
                self.ui_elements["palette_redo_btn"] = outline_button("Redo", self._redo_palette, icon="redo")
                self.ui_elements["palette_discard_btn"] = outline_button(
                    "Reset colors", self._discard_palette_edits, icon="restore"
                )
            self._update_palette_toolbar_state()

    def _build_palette_section(self) -> None:
        """(Re)build the grouped color swatches from the working theme."""
        if self.palette_container is None:
            return
        theme = self._get_preview_theme()
        if not theme:
            return
        disk = self._get_current_theme(self.buffer)
        self.palette_container.clear()
        with self.palette_container:
            for group, fields in _COLOR_SECTIONS:
                with ui.element("div").classes("thm-group"):
                    ui.label(group).classes("thm-group-title")
                    with ui.element("div").classes("thm-swatches"):
                        for field_name, label, desc in fields:
                            self._build_swatch(theme, disk, group, field_name, label, desc)

    def _build_swatch(self, theme, disk, group, field_name, label, desc) -> None:
        raw = getattr(theme, field_name, "") or ""
        edited = disk is not None and raw != (getattr(disk, field_name, "") or "")
        # Register click before classes so NiceGUI 3.x does not treat it as a late listener.
        swatch = ui.element("div").on(
            "click", lambda _, fn=field_name, g=group, lb=label: self._open_palette_color_picker(fn, f"{g} · {lb}")
        )
        swatch.classes("thm-swatch is-edited" if edited else "thm-swatch")
        swatch.tooltip(desc + (" (edited)" if edited else ""))
        safe = html.escape(raw, quote=True)
        with swatch:
            ui.html(
                f'<span class="thm-swatch-chip"><span class="thm-swatch-fill" style="background:{safe}"></span></span>'
                f'<span class="thm-swatch-text"><span class="thm-swatch-label">{html.escape(label)}'
                f'<span class="thm-swatch-dot"></span></span>'
                f'<span class="thm-swatch-value">{html.escape(self._describe_color(raw))}</span></span>',
                sanitize=False,
            ).classes("contents")

    def _describe_color(self, raw: str) -> str:
        parsed = _parse_color(raw)
        if not parsed:
            return raw or "not set"
        hex_value = self._convert_to_hex(raw).upper()
        alpha = parsed[3]
        return hex_value if alpha >= 0.999 else f"{hex_value} · {round(alpha * 100)}%"

    # ------------------------------------------------------------------ #
    #  Derived view state                                                  #
    # ------------------------------------------------------------------ #
    def _refresh_view(self) -> None:
        """Tags, badges, notes and status text that follow the selection and edits."""
        theme = self._get_preview_theme()
        applied = self._applied_theme_name()
        name = self._display_name(self.buffer)
        safe_name = html.escape(name)
        protected = self.buffer in PROTECTED_THEMES
        is_current = self.buffer == applied and not self._palette_dirty

        type_el = self.ui_elements.get("theme_type_label")
        if type_el is not None and theme:
            light = theme.theme_type == "light"
            mode = (
                f'<span class="thm-tag"><span class="thm-icon">{"light_mode" if light else "dark_mode"}</span>'
                f'{"Light" if light else "Dark"}</span>'
            )
            origin = (
                '<span class="thm-tag"><span class="thm-icon">lock</span>Built-in</span>'
                if protected
                else '<span class="thm-tag is-accent"><span class="thm-icon">brush</span>Editable</span>'
            )
            type_el.set_content(mode + origin)

        badge = self.ui_elements.get("_preview_badge")
        if badge is not None:
            if is_current:
                badge.set_content(
                    '<span class="thm-state is-ok"><span class="thm-icon">check_circle</span>Current theme</span>'
                )
            else:
                badge.set_content(
                    '<span class="thm-state is-warn"><span class="thm-icon">visibility</span>Preview only</span>'
                )

        note = self.ui_elements.get("_palette_note")
        if note is not None:
            if protected:
                if self._palette_dirty:
                    note.classes(add="is-warn")
                else:
                    note.classes(remove="is-warn")
                note.set_content(
                    f'<span class="thm-icon">{"warning" if self._palette_dirty else "lock"}</span><span>'
                    f"<b>{safe_name}</b> is built in, so its colors can't be overwritten. "
                    "Edit freely, then use <b>Save as</b> to keep your colors as a new theme.</span>"
                )
            else:
                note.classes(remove="is-warn")
                note.set_content(
                    f'<span class="thm-icon">info</span><span>Color edits to <b>{safe_name}</b> are saved '
                    "to the theme when you click <b>Apply</b>.</span>"
                )

        read = self.ui_elements.get("_readability")
        if read is not None and theme:
            read.set_content(self._readability_html(theme))

        status = self.ui_elements.get("_status_label")
        if status is not None:
            if self._palette_dirty and protected:
                text = "Unsaved color changes. Use <b>Save as</b> to keep them."
            elif self._palette_dirty:
                text = f"Unsaved color changes to <b>{safe_name}</b>. Apply saves them."
            elif self.buffer != applied:
                text = f"Previewing <b>{safe_name}</b>. Click Apply to use it."
            else:
                text = f"<b>{safe_name}</b> is your current theme."
            status.set_content(f'<span class="thm-dot"></span><span>{text}</span>')
            if is_current:
                status.classes(remove="is-dirty")
            else:
                status.classes(add="is-dirty")

        self._update_gallery_selection()
        self._update_delete_button_state()
        self._update_palette_toolbar_state()

    @staticmethod
    def _readability_html(theme: ThemeColors) -> str:
        icons = {"ok": "check_circle", "low": "error_outline", "bad": "cancel"}
        words = {"ok": "Easy to read", "low": "Low contrast", "bad": "Hard to read"}
        parts = []
        for label, fg, bg, ratio, rating in readability_checks(theme):
            parts.append(
                f'<span class="thm-read-item is-{rating}" title="{words[rating]}">'
                f'<span class="thm-read-sample" style="color:{html.escape(fg, quote=True)};'
                f'background:{html.escape(bg, quote=True)}">Aa</span>{label}'
                f'<span class="thm-read-ratio">{ratio:.1f}:1</span>'
                f'<span class="thm-icon">{icons[rating]}</span></span>'
            )
        return "".join(parts)

    # ------------------------------------------------------------------ #
    #  Theme list & change handling                                        #
    # ------------------------------------------------------------------ #
    def _refresh_theme_list(self):
        """Refresh the theme dropdown with available themes"""
        self._refresh_theme_list_after_change(select_name=self.buffer)

    def _on_theme_change(self, new_value):
        """Handle theme selection change with live preview"""
        if new_value and new_value != self.buffer:
            self.buffer = new_value
            self._sync_working_theme()
            self._refresh_dirty_from_palette()
            self._apply_preview()

    def _apply_preview(self):
        """Point the mock at the working theme's colors and refresh the editor.

        The mock's markup never changes; only the ``--preview-color-*`` variables do.
        """
        if not self.buffer:
            return
        theme = self._get_preview_theme()
        if not theme:
            return
        if self._vars_el is not None:
            self._vars_el.set_content(f"<style>{generate_preview_css_variables(theme)}</style>")
        self._build_palette_section()
        self._refresh_view()

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
            self._render_gallery()
            self._build_palette_section()
            self._refresh_view()
        except Exception as e:
            notify(f"Error applying theme: {str(e)}", type="negative")

    def discard(self) -> None:
        """Revert to saved theme"""
        try:
            self._load_from_state()

            if "theme_select" in self.ui_elements:
                self.ui_elements["theme_select"].value = self.buffer

            self._sync_working_theme()
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
        self._render_gallery(available_themes)
        self._update_delete_button_state()

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
    def _open_palette_color_picker(self, field_name: str, title: Optional[str] = None) -> None:
        """Open a color picker for a palette swatch field."""
        if not self._working_theme:
            return
        current = getattr(self._working_theme, field_name, "") or ""
        current_hex = self._convert_to_hex(current)
        display_name = title or field_name.replace("_", " ").title()
        parsed = _parse_color(current)
        alpha = parsed[3] if parsed else 1.0

        def on_apply(rgb_value: str) -> None:
            if alpha < 0.999:
                # The picker has no alpha channel; keep the color's transparency.
                rgb_value = "rgba(" + rgb_value[4:-1] + f", {round(alpha, 3):g})"
            current = getattr(self._working_theme, field_name, "") or ""
            if current == rgb_value:
                return
            self._push_undo_snapshot()
            setattr(self._working_theme, field_name, rgb_value)
            self._mark_palette_dirty()
            self._apply_preview()

        note = f"Keeps its {round(alpha * 100)}% opacity." if alpha < 0.999 else ""
        self._show_color_picker_dialog(
            display_name, current_hex, on_apply=on_apply, note=note
        )

    # ------------------------------------------------------------------ #
    #  Colour helpers                                                      #
    # ------------------------------------------------------------------ #
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
        note: str = "",
    ) -> None:
        """Show a color picker dialog and invoke *on_apply* with the chosen RGB value."""
        with ui.dialog() as picker_dlg, ui.card().classes("p-4"):
            ui.label(display_name).classes("text-lg font-semibold mb-4")
            color_input = (
                ui.color_input(value=current_hex)
                .props("outlined")
                .classes("w-full")
            )
            if note:
                ui.label(note).classes("text-xs secondary-text mt-1")

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

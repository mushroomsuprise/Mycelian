# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import html
import logging
import re
from typing import Dict, Any, Optional, List, Tuple

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
from ...ui_color_control import open_color_picker
from ...ui_form_controls import form_input, form_select
from .theme_mock import build_mock_html
from .theme_styles import THEME_CSS, THEME_MENU_CSS

logger = logging.getLogger(__name__)


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

# Pop-up look, saved with the theme (empty = default).
_NOTIFY_STYLES: Tuple[Tuple[str, str], ...] = (
    ("accent", "Accent bar"), ("chip", "Icon chip"), ("minimal", "Minimal"), ("tinted", "Tinted"), ("glass", "Glass"),
)
_NOTIFY_LOOK_DEFAULTS: Dict[str, str] = {
    "notify_style": "accent", "notify_radius": "12", "notify_shadow": "soft", "notify_timer": "on", "notify_density": "comfortable",
}
_EDITABLE_FIELDS: Tuple[str, ...] = _PALETTE_COLOR_FIELDS + tuple(_NOTIFY_LOOK_DEFAULTS)

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
        self._option_dots_el = None
        self._select_dots = None
        self._focus_field: str = "primary"
        self._swatch_els: Dict[str, Any] = {}

    # ----- helper methods -----
    def _get_theme_manager(self):
        """Get theme manager instance with error handling"""
        try:
            return get_theme_manager()
        except Exception as e:
            logger.error("Theme manager unavailable: %s", e, exc_info=True)
            notify(
                "Mycelian is using the default theme.",
                type="error",
                source="theme",
                title="Themes couldn't be loaded",
                hint="Restart Mycelian. If it keeps happening, copy the details and report it.",
                details=f"{type(e).__name__}: {e}",
                dedupe_key="theme:manager",
                dedupe_cooldown_sec=3600,
            )
            return None

    def _get_available_themes(self, theme_manager):
        """Get available themes with error handling"""
        try:
            return theme_manager.get_available_themes() if theme_manager else []
        except Exception as e:
            logger.error("Could not list themes: %s", e, exc_info=True)
            notify(
                "",
                type="error",
                source="theme",
                title="Couldn't read the theme list",
                hint="Check that the themes folder is readable, then reopen Settings → Theme.",
                details=f"{type(e).__name__}: {e}",
                dedupe_key="theme:list",
                dedupe_cooldown_sec=600,
            )
            return []

    def _get_current_theme(self, theme_name=None):
        """Get theme object safely with error handling"""
        try:
            theme_manager = self._get_theme_manager()
            if not theme_manager:
                return None
            return theme_manager.get_theme_by_name(theme_name or self.buffer or "dark")
        except Exception as e:
            name = theme_name or self.buffer or "dark"
            logger.error("Could not load theme %s: %s", name, e, exc_info=True)
            notify(
                "",
                type="error",
                source="theme",
                title=f"Couldn't load the theme “{name}”",
                hint="Its file may be damaged. Pick another theme.",
                details=f"{type(e).__name__}: {e}",
                dedupe_key=f"theme:load:{name}",
                dedupe_cooldown_sec=600,
            )
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
        for field in _EDITABLE_FIELDS:
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
        """Build the header (picker + actions), live mock preview and colors panel."""
        from ...help_system.help_render import (
            inject_page_stylesheet,
            promote_color_importance,
            scope_selectors,
        )

        inject_page_stylesheet(
            "mycelian-theme-tab", promote_color_importance(scope_selectors(THEME_CSS, ".thm"))
        )
        inject_page_stylesheet("mycelian-theme-menu", THEME_MENU_CSS)
        self._load_from_state()

        theme_manager = self._get_theme_manager()
        available_themes = self._get_available_themes(theme_manager)
        select_options = self._build_theme_select_options(available_themes)
        if not (self.buffer and self.buffer in select_options) and select_options:
            self.buffer = next(iter(select_options))
        self._sync_working_theme()

        with parent_container:
            with ui.element("div").classes("thm"):
                with ui.element("div").classes("thm-shell"):
                    self._build_header(select_options)
                    with ui.element("div").classes("thm-body"):
                        self._build_stage()
                        self._build_side()
                # <style> blocks live in the page body so they also reach the dropdown menu.
                self._vars_el = ui.html("", sanitize=False).classes("hidden")
                self._option_dots_el = ui.html("", sanitize=False).classes("hidden")

        self._render_option_dots(available_themes)
        self._apply_preview()

    def _build_header(self, select_options) -> None:
        with ui.element("div").classes("thm-head"):
            ui.html(
                '<span class="thm-title-icon"><span class="thm-icon">palette</span></span>'
                '<span><div class="thm-title-name">Theme</div>'
                '<div class="thm-title-sub">Preview a theme before you apply it</div></span>',
                sanitize=False,
            ).classes("thm-title")
            select = form_select(
                tooltip="Color theme applied across the Mycelian interface",
                options=select_options,
                value=self.buffer if self.buffer in select_options else None,
                classes="thm-select",
                on_change=lambda e: self._on_theme_change(e.value),
            )
            with select.add_slot("prepend"):
                self._select_dots = ui.html("", sanitize=False)
            select.add_slot(
                "option",
                '<q-item v-bind="props.itemProps"><q-item-section side>'
                '<span class="thm-optdots" :data-thm="props.opt.label"><i></i><i></i><i></i></span>'
                "</q-item-section><q-item-section><q-item-label>{{ props.opt.label }}</q-item-label>"
                "</q-item-section></q-item>",
            )
            self.ui_elements["theme_select"] = select
            self.ui_elements["theme_type_label"] = ui.html("", sanitize=False).classes("thm-tags")
            self.ui_elements["_preview_badge"] = ui.html("", sanitize=False)
            ui.element("div").classes("thm-spacer")
            outline_button("Save as", self._open_save_as_dialog, icon="content_copy").props("dense").classes("px-3")
            delete_btn = destructive_button("", self._confirm_delete_theme, icon="delete").props("dense")
            delete_btn.classes("px-2").tooltip("Delete this theme")
            self.ui_elements["delete_btn"] = delete_btn
            ui.element("div").classes("thm-divider")
            self.ui_elements["_discard_btn"] = outline_button("Discard", self.discard).props("dense").classes("px-3")
            primary_button("Apply", self.save, icon="check").props("dense").classes("px-4")

    def _build_stage(self) -> None:
        from ...notification_engine import current_footer_style_classes

        minimal = False
        try:
            minimal = bool(getattr(state_manager.get_app_settings(), "status_footer_minimal", False))
        except Exception:
            minimal = False
        with ui.element("div").classes("thm-stage"):
            with ui.element("div").classes("thm-mock theme-preview-container") as mock:
                ui.html(build_mock_html(minimal, current_footer_style_classes()), sanitize=False).classes("contents")
            self.preview_container = mock

    def _build_side(self) -> None:
        """Colors and pop-up notification panels, side by side under the preview."""
        with ui.element("div").classes("thm-panels"):
            with ui.element("div").classes("thm-side"):
                with ui.element("div").classes("thm-side-head"):
                    ui.label("Colors")
                    ui.element("div").classes("thm-spacer")
                    for key, icon, tip, handler in (
                        ("palette_undo_btn", "undo", "Undo", self._undo_palette),
                        ("palette_redo_btn", "redo", "Redo", self._redo_palette),
                        ("palette_discard_btn", "restart_alt", "Reset colors", self._discard_palette_edits),
                    ):
                        btn = ui.button(icon=icon).props("flat round dense size=sm")
                        btn.on_click(handler)
                        btn.tooltip(tip)
                        self.ui_elements[key] = btn
                with ui.element("div").classes("thm-colors"):
                    self.palette_container = ui.element("div").classes("thm-groups")
                    with ui.element("div").classes("thm-colors-aside"):
                        self.ui_elements["_focus"] = ui.html("", sanitize=False).classes("thm-focus")
                        with ui.element("div").classes("thm-side-head is-sub"):
                            ui.label("Readability")
                        self.ui_elements["_readability"] = ui.html("", sanitize=False).classes("thm-reads")
            with ui.element("div").classes("thm-side"):
                with ui.element("div").classes("thm-side-head"):
                    ui.label("Pop-up notifications")
                self.notify_container = ui.element("div").classes("thm-notify")
        self._update_palette_toolbar_state()

    def _build_palette_section(self) -> None:
        """(Re)build the swatch rows from the working theme."""
        if self.palette_container is None:
            return
        theme = self._get_preview_theme()
        if not theme:
            return
        disk = self._get_current_theme(self.buffer)
        self.palette_container.clear()
        with self.palette_container:
            for group, fields in _COLOR_SECTIONS:
                with ui.element("div").classes("thm-grp"):
                    ui.label(group).classes("thm-grp-name")
                    with ui.element("div").classes("thm-grp-sw"):
                        for field_name, label, desc in fields:
                            self._build_swatch(theme, disk, group, field_name, label, desc)
        self._build_notify_section(theme)

    def _notify_look(self, theme: ThemeColors, field_name: str) -> str:
        return str(getattr(theme, field_name, "") or _NOTIFY_LOOK_DEFAULTS[field_name])

    def _set_notify_look(self, field_name: str, value: str) -> None:
        """Change one pop-up look setting on the working theme (undoable, like a color)."""
        if not self._working_theme:
            return
        if self._notify_look(self._working_theme, field_name) == value:
            return
        self._push_undo_snapshot()
        setattr(self._working_theme, field_name, value)
        self._refresh_dirty_from_palette()
        self._apply_preview()

    def _build_notify_section(self, theme: ThemeColors) -> None:
        box = getattr(self, "notify_container", None)
        if box is None or getattr(box, "is_deleted", False):
            return
        style = self._notify_look(theme, "notify_style")
        box.clear()
        with box:
            with ui.element("div").classes("thm-nstyles"):
                for value, label in _NOTIFY_STYLES:
                    chip = ui.element("div").classes("thm-nstyle" + (" is-on" if value == style else ""))
                    chip.on("click", lambda _e, v=value: self._set_notify_look("notify_style", v))
                    with chip:
                        ui.label(label)
            with ui.element("div").classes("thm-nrow"):
                ui.label("Corners")
                form_select(
                    tooltip="Corner radius of pop-ups",
                    options={str(r): f"{r} px" for r in (0, 4, 8, 12, 16, 20, 24)},
                    value=self._notify_look(theme, "notify_radius"),
                    classes="thm-nsel",
                    on_change=lambda e: self._set_notify_look("notify_radius", str(e.value or "12")),
                )
                ui.label("Shadow")
                form_select(
                    tooltip="Shadow under pop-ups",
                    options={"none": "None", "soft": "Soft", "strong": "Strong"},
                    value=self._notify_look(theme, "notify_shadow"),
                    classes="thm-nsel",
                    on_change=lambda e: self._set_notify_look("notify_shadow", str(e.value or "soft")),
                )
            with ui.element("div").classes("thm-nrow"):
                ui.label("Density")
                form_select(
                    tooltip="Comfortable leaves more room; compact fits more on screen",
                    options={"comfortable": "Comfortable", "compact": "Compact"},
                    value=self._notify_look(theme, "notify_density"),
                    classes="thm-nsel",
                    on_change=lambda e: self._set_notify_look("notify_density", str(e.value or "comfortable")),
                )
                ui.label("Timer bar")
                ui.switch(
                    value=self._notify_look(theme, "notify_timer") != "off",
                    on_change=lambda e: self._set_notify_look("notify_timer", "on" if e.value else "off"),
                ).props("dense").tooltip("A thin bar along the bottom shows how long the pop-up stays")
            ui.html(self._notify_sample_html(theme), sanitize=False).classes("thm-nsample")

    def _notify_sample_html(self, theme: ThemeColors) -> str:
        """A static pop-up drawn with the renderer's classes and this theme's colors."""
        def c(field_name: str, fallback: str = "") -> str:
            return html.escape(str(getattr(theme, field_name, "") or fallback), quote=True)

        try:
            radius = max(0, min(28, int(float(self._notify_look(theme, "notify_radius")))))
        except ValueError:
            radius = 12
        variables = (
            f"--notify-radius:{radius}px;--color-bg-surface:{c('bg_surface')};--color-bg-base:{c('bg_base')};"
            f"--color-border-subtle:{c('border_subtle')};--color-border-default:{c('border_default')};"
            f"--color-border-accent:{c('border_accent')};--color-text-primary:{c('text_primary')};"
            f"--color-text-secondary:{c('text_secondary')};--color-text-muted:{c('text_muted')};"
            f"--color-primary:{c('primary')};--color-primary-light:{c('primary_light')};"
            f"--color-notify-warning:{c('notify_warning', theme.warning)};--color-warning:{c('warning')};"
        )
        style = self._notify_look(theme, "notify_style")
        shadow = self._notify_look(theme, "notify_shadow")
        classes = f"mt is-warning st-{style}"
        if shadow != "soft":
            classes += f" sh-{shadow}"
        if self._notify_look(theme, "notify_density") == "compact":
            classes += " is-compact"
        bar = '<div class="mt-bar" style="width:55%"></div>' if self._notify_look(theme, "notify_timer") != "off" else ""
        return (
            f'<div class="{classes}" style="{variables}">'
            '<div class="mt-ico"><span class="mt-i">warning</span></div>'
            '<div class="mt-top"><span class="mt-lvl">Warning</span><span class="mt-dot"></span><span>Spore Studio</span></div>'
            '<span class="mt-x"><span class="mt-i">close</span></span>'
            '<div class="mt-title">Font missing in “giveaway”</div>'
            "<div class=\"mt-msg\">Renogare.ttf isn't in the fonts folder, so the overlay falls back to Arial.</div>"
            '<div class="mt-acts"><span class="mt-btn is-main"><span class="mt-i">auto_awesome</span>Open in Spore Studio</span></div>'
            f"{bar}</div>"
        )

    def _build_swatch(self, theme, disk, group, field_name, label, desc) -> None:
        raw = getattr(theme, field_name, "") or ""
        classes = ["thm-sw"]
        if disk is not None and raw != (getattr(disk, field_name, "") or ""):
            classes.append("is-edited")
        if field_name == self._focus_field:
            classes.append("is-active")
        # Register click before classes so NiceGUI 3.x does not treat it as a late listener.
        swatch = ui.element("div").on(
            "click", lambda _, fn=field_name, g=group, lb=label: self._on_swatch_click(fn, f"{g} · {lb}")
        )
        swatch.classes(" ".join(classes))
        swatch.tooltip(f"{group} · {label}")
        self._swatch_els[field_name] = swatch
        with swatch:
            ui.html(
                f'<span class="thm-sw-fill" style="background:{html.escape(raw, quote=True)}"></span>',
                sanitize=False,
            ).classes("contents")

    def _on_swatch_click(self, field_name: str, title: str) -> None:
        self._focus_field = field_name
        self._build_palette_section()
        self._refresh_focus()
        self._open_palette_color_picker(field_name, title)

    def _describe_color(self, raw: str) -> str:
        parsed = _parse_color(raw)
        if not parsed:
            return raw or "not set"
        hex_value = self._convert_to_hex(raw).upper()
        alpha = parsed[3]
        return hex_value if alpha >= 0.999 else f"{hex_value} · {round(alpha * 100)}%"

    def _render_option_dots(self, available_themes=None) -> None:
        """Per-theme dot colors for the dropdown's options (matched by label)."""
        if self._option_dots_el is None:
            return
        theme_manager = self._get_theme_manager()
        if not theme_manager:
            return
        if available_themes is None:
            available_themes = self._get_available_themes(theme_manager)
        rules = []
        for name, label in self._build_theme_select_options(available_themes).items():
            theme = theme_manager.get_theme_by_name(name)
            if not theme:
                continue
            key = label.replace("\\", "\\\\").replace('"', '\\"')
            for i, color in enumerate(self._dot_colors(theme), start=1):
                safe = re.sub(r"[^#\w\s(),.%-]", "", color)
                rules.append(f'.thm-optdots[data-thm="{key}"] i:nth-child({i}){{background:{safe}}}')
        self._option_dots_el.set_content(f"<style>{''.join(rules)}</style>")

    @staticmethod
    def _dot_colors(theme: ThemeColors) -> Tuple[str, str, str]:
        return (theme.bg_base or "", theme.bg_surface or "", theme.primary or "")

    # ------------------------------------------------------------------ #
    #  Derived view state                                                  #
    # ------------------------------------------------------------------ #
    def _refresh_focus(self) -> None:
        focus = self.ui_elements.get("_focus")
        theme = self._get_preview_theme()
        if focus is None or not theme:
            return
        label, desc, group = self._focus_field, "", ""
        for g, fields in _COLOR_SECTIONS:
            for field_name, field_label, field_desc in fields:
                if field_name == self._focus_field:
                    label, desc, group = field_label, field_desc, g
        raw = getattr(theme, self._focus_field, "") or ""
        disk = self._get_current_theme(self.buffer)
        edited = disk is not None and raw != (getattr(disk, self._focus_field, "") or "")
        dot = '<span class="thm-focus-dot" title="Edited"></span>' if edited else ""
        name = label if label == group else f"{group} · {label}"
        focus.set_content(
            f'<span class="thm-focus-chip"><span class="thm-sw-fill" style="background:{html.escape(raw, quote=True)}"></span></span>'
            f'<span><div class="thm-focus-name">{html.escape(name)}{dot}</div>'
            f'<div class="thm-focus-val">{html.escape(self._describe_color(raw))} · {html.escape(desc)}</div></span>'
        )

    def _refresh_view(self) -> None:
        """Tags, state pill, focus box, readability and button states."""
        theme = self._get_preview_theme()
        applied = self._applied_theme_name()
        protected = self.buffer in PROTECTED_THEMES
        is_current = self.buffer == applied and not self._palette_dirty

        if self._select_dots is not None and theme:
            dots = "".join(f'<i style="background:{html.escape(c, quote=True)}"></i>' for c in self._dot_colors(theme))
            self._select_dots.set_content(f'<span class="thm-dots">{dots}</span>')

        type_el = self.ui_elements.get("theme_type_label")
        if type_el is not None and theme:
            light = theme.theme_type == "light"
            tags = (
                f'<span class="thm-tag"><span class="thm-icon">{"light_mode" if light else "dark_mode"}</span>'
                f'{"Light" if light else "Dark"}</span>'
            )
            if protected:
                tags += (
                    '<span class="thm-tag" title="Built-in themes can\'t be changed or deleted. '
                    'Use Save as to keep edited colors."><span class="thm-icon">lock</span>Built-in</span>'
                )
            type_el.set_content(tags)

        badge = self.ui_elements.get("_preview_badge")
        if badge is not None:
            if is_current:
                badge.set_content(
                    '<span class="thm-pill is-ok"><span class="thm-icon">check_circle</span>Current theme</span>'
                )
            elif self._palette_dirty:
                tip = "Use Save as to keep these colors" if protected else "Apply saves these colors to the theme"
                badge.set_content(
                    f'<span class="thm-pill is-warn" title="{tip}"><span class="thm-icon">edit</span>Unsaved colors</span>'
                )
            else:
                badge.set_content(
                    '<span class="thm-pill is-warn" title="Click Apply to use this theme">'
                    '<span class="thm-icon">visibility</span>Previewing</span>'
                )

        discard = self.ui_elements.get("_discard_btn")
        if discard is not None:
            if is_current:
                discard.props("disable")
            else:
                discard.props(remove="disable")

        read = self.ui_elements.get("_readability")
        if read is not None and theme:
            read.set_content(self._readability_html(theme))

        self._refresh_focus()
        self._update_delete_button_state()
        self._update_palette_toolbar_state()

    @staticmethod
    def _readability_html(theme: ThemeColors) -> str:
        icons = {"ok": "check", "low": "priority_high", "bad": "close"}
        words = {"ok": "Easy to read", "low": "Low contrast", "bad": "Hard to read"}
        return "".join(
            f'<span class="thm-read is-{rating}" title="{words[rating]} ({ratio:.1f}:1)">'
            f'<span class="thm-icon">{icons[rating]}</span>{label}<b>{ratio:.1f}</b></span>'
            for label, _fg, _bg, ratio, rating in readability_checks(theme)
        )

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
            notify("", type="warning", source="theme", title="Pick a theme first")
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
                        "Nothing was applied.",
                        type="warning",
                        source="theme",
                        title="Built-in themes can't be changed",
                        hint="Use Save as to keep your colors as a new theme, or discard the color changes.",
                    )
                    return
                else:
                    self._working_theme.name = self.buffer
                    persisted = theme_manager.get_theme_by_name(self.buffer)
                    if persisted:
                        self._working_theme.display_name = persisted.display_name
                        self._working_theme.theme_type = persisted.theme_type
                    if not theme_manager.update_custom_theme(self._working_theme):
                        notify(
                            "",
                            type="error",
                            source="theme",
                            title=f"Couldn't save your color changes to “{self._display_name(self.buffer)}”",
                            hint="Check that the themes folder is writable, then press Save again.",
                        )
                        return
                    self._reset_palette_history()
                    self._refresh_dirty_from_palette()

            state_manager.update_app_setting("current_theme", self.buffer)

            if not state_manager.save_changes():
                return  # save_changes() already reported it (changes stay pending)

            from ...mainuiwindow import apply_theme

            # apply_theme returns False (and warns, source "theme") when the theme fails to load.
            if apply_theme(self.buffer) is False:
                return

            notify(
                "",
                type="success",
                source="theme",
                title=f"Theme “{self._display_name(self.buffer)}” applied",
            )
            self.dirty = False
            self._render_option_dots()
            self._build_palette_section()
            self._refresh_view()
        except Exception as e:
            logger.error("Applying theme %s failed: %s", self.buffer, e, exc_info=True)
            notify(
                "",
                type="error",
                source="theme",
                title="Couldn't apply the theme",
                hint="Try again or pick another theme. If it keeps happening, copy the details and report it.",
                details=f"{type(e).__name__}: {e}",
            )

    def _display_name(self, name: str) -> str:
        """The theme's display name (falls back to its internal name)."""
        try:
            manager = get_theme_manager()
            return str(manager.get_theme_display_name(name) or name)
        except Exception:
            return name

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
            logger.error("Discarding theme changes failed: %s", e, exc_info=True)
            notify(
                "",
                type="error",
                source="theme",
                title="Couldn't reset the theme editor",
                hint="Leave and reopen Settings → Theme.",
                details=f"{type(e).__name__}: {e}",
            )

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
        self._render_option_dots(available_themes)
        self._update_delete_button_state()

    # ------------------------------------------------------------------ #
    #  Save as / Delete                                                    #
    # ------------------------------------------------------------------ #
    def _open_save_as_dialog(self) -> None:
        """Save the current working theme configuration under a new name."""
        if not self._working_theme:
            notify("", type="warning", source="theme", title="Pick a theme to base the new one on")
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
            notify("", type="warning", source="theme", title="Enter a theme name")
            return
        if not display_name:
            notify("", type="warning", source="theme", title="Enter a display name")
            return
        if not re.match(r"^[a-zA-Z0-9_]+$", name):
            notify(
                "",
                type="warning",
                source="theme",
                title="Theme names can only use letters, numbers and underscores",
            )
            return
        if theme_manager.theme_name_exists(name):
            notify(
                "",
                type="warning",
                source="theme",
                title=f"A theme called “{name}” already exists",
                hint="Choose another name.",
            )
            return

        theme_obj = copy.deepcopy(self._working_theme)
        theme_obj.name = name
        theme_obj.display_name = display_name
        theme_obj.theme_type = theme_type

        if not theme_manager.save_custom_theme(theme_obj):
            notify(
                "",
                type="error",
                source="theme",
                title="Couldn't save the new theme",
                hint="Check that the themes folder is writable, then try again.",
            )
            return

        notify(
            "Press Save to use it.",
            type="success",
            source="theme",
            title=f"Theme “{display_name}” created",
        )
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
            notify("", type="warning", source="theme", title="Pick a theme first")
            return
        if self.buffer in PROTECTED_THEMES:
            # Delete is disabled for built-in themes; kept as a guard.
            notify("", type="warning", source="theme", title="Built-in themes can't be deleted")
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
            dialog.close()  # _get_theme_manager already reported it
            return

        deleted_display = self._display_name(deleted_name)
        if not theme_manager.delete_theme(deleted_name):
            notify(
                "",
                type="error",
                source="theme",
                title=f"Couldn't delete “{deleted_display}”",
                hint="Check that the themes folder is writable, then try again.",
            )
            dialog.close()
            return

        app_settings = state_manager.get_app_settings()
        saved_theme = getattr(app_settings, "current_theme", "dark")
        fallback = "dark"
        switched = False
        if saved_theme == deleted_name:
            state_manager.update_app_setting("current_theme", fallback)
            # A failed save is reported by save_changes (and retried on the next save).
            state_manager.save_changes()
            try:
                from ...mainuiwindow import apply_theme

                switched = apply_theme(fallback) is not False
            except Exception as e:
                logger.error("Could not switch to %s after delete: %s", fallback, e, exc_info=True)

        self.buffer = fallback
        self._sync_working_theme()
        self._refresh_theme_list_after_change(select_name=fallback)
        self._apply_preview()
        self.dirty = False
        self._palette_dirty = False
        notify(
            f"Switched to {self._display_name(fallback)}." if switched else "",
            type="success",
            source="theme",
            title=f"Deleted “{deleted_display}”",
        )
        dialog.close()

    # ------------------------------------------------------------------ #
    #  Palette color picker                                                #
    # ------------------------------------------------------------------ #
    def _open_palette_color_picker(self, field_name: str, title: Optional[str] = None) -> None:
        """Open the shared colour picker on a palette swatch; the mock preview follows live."""
        if not self._working_theme:
            return
        original = getattr(self._working_theme, field_name, "") or ""
        display_name = title or field_name.replace("_", " ").title()
        parts = display_name.split(" · ")
        if len(parts) == 2 and parts[0] == parts[1]:
            display_name = parts[0]
        parsed = _parse_color(original)
        alpha = parsed[3] if parsed else 1.0
        has_alpha = alpha < 0.999
        anchor = self._swatch_els.get(field_name)
        if anchor is None:
            return

        def live(value: str) -> None:
            if not self._working_theme:
                return
            setattr(self._working_theme, field_name, value)
            if self._vars_el is not None:
                self._vars_el.set_content(f"<style>{generate_preview_css_variables(self._working_theme)}</style>")
            self._refresh_focus()

        def restore() -> None:
            live(original)

        def done(value: str) -> None:
            if not self._working_theme:
                return
            setattr(self._working_theme, field_name, original)
            if not value or value == original:
                restore()
                return
            self._push_undo_snapshot()
            setattr(self._working_theme, field_name, value)
            self._mark_palette_dirty()
            self._apply_preview()

        note = f"Started at {round(alpha * 100)}% opacity" if has_alpha else "The preview follows as you pick"
        open_color_picker(
            anchor,
            original,
            on_input=live,
            on_done=done,
            on_cancel=restore,
            allow_alpha=has_alpha,
            preferred_format="rgb",
            title=display_name,
            note=note,
            fill_selector=".thm-sw-fill",
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

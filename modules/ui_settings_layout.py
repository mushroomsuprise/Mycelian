# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Shared layout helpers for Settings subtabs.

Uses theme semantic classes and structural Tailwind only — no hardcoded colors.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Generator, List, Optional, Tuple

from nicegui import ui

from .ui_buttons import outline_button, primary_button

# (label, handler, icon?, use_primary?)
ActionButton = Tuple[str, Callable[..., Any], Optional[str], bool]

THEME_CHIP_CLASSES = (
    "theme-chip flex items-center gap-1 px-3 py-1 rounded-full shrink-0"
)


@contextmanager
def settings_surface(parent: Any) -> Generator[None, None, None]:
    """Outer layout wrapper for settings subtabs (no card frame)."""
    with parent:
        with ui.column().classes("w-full gap-3 settings-tab-content"):
            yield


@contextmanager
def settings_inner_panel() -> Generator[None, None, None]:
    """Nested panel (e.g. Twitch account blocks)."""
    with ui.element("div").classes(
        "content-card settings-inner-panel w-full p-3 border border-theme-subtle rounded-lg"
    ):
        with ui.column().classes("w-full gap-2"):
            yield


@contextmanager
def settings_header(title: str) -> Generator[None, None, None]:
    """Title row; place action buttons inside this context."""
    with ui.row().classes("w-full justify-between items-center gap-2 shrink-0"):
        ui.label(title).classes("text-lg font-bold")
        with ui.row().classes("items-center gap-2 shrink-0"):
            yield


@contextmanager
def settings_section(
    title: str, *, subtitle: Optional[str] = None
) -> Generator[None, None, None]:
    """Compact section block."""
    with ui.column().classes("w-full gap-2 settings-section"):
        ui.label(title).classes("text-base font-semibold")
        if subtitle:
            ui.label(subtitle).classes("text-sm secondary-text")
        yield


@contextmanager
def settings_status_band() -> Generator[None, None, None]:
    """Horizontal status metrics row."""
    with ui.row().classes("settings-status-band w-full"):
        yield


def status_metric(label: str, *, extra_classes: str = "") -> ui.label:
    """Label + value column for use inside settings_status_band."""
    with ui.column().classes("gap-0"):
        ui.label(label).classes("text-xs secondary-text")
        return ui.label("…").classes(f"font-semibold text-sm {extra_classes}".strip())


@contextmanager
def settings_form_grid(*, columns: int = 2) -> Generator[None, None, None]:
    """Grid for labeled inputs."""
    with ui.grid(columns=columns).classes(
        "settings-form-grid w-full gap-x-4 gap-y-2"
    ):
        yield


@contextmanager
def settings_toolbar() -> Generator[None, None, None]:
    """Horizontal action button group."""
    with ui.row().classes(
        "settings-toolbar w-full flex-wrap gap-2 items-center"
    ):
        yield


def settings_divider() -> ui.separator:
    return ui.separator().classes("settings-divider divider")


def settings_footer(
    discard: Callable[..., Any],
    save: Callable[..., Any],
) -> None:
    """Save / Discard row (Discard then Save)."""
    settings_action_row(discard=discard, save=save)


def settings_action_row(
    *,
    discard: Callable[..., Any],
    save: Callable[..., Any],
    before_discard: Optional[List[ActionButton]] = None,
    after_save: Optional[List[ActionButton]] = None,
) -> None:
    """
    Bottom action row: optional leading buttons, Discard, Save, optional trailing.

    Each ActionButton is (label, handler, icon_or_none, use_primary).
    """
    with ui.row().classes("button-row w-full justify-end gap-2 mt-1 flex-wrap"):
        for label, handler, icon, use_primary in before_discard or []:
            if use_primary:
                primary_button(label, handler, icon=icon)
            else:
                outline_button(label, handler, icon=icon)
        outline_button("Discard", discard)
        primary_button("Save", save)
        for label, handler, icon, use_primary in after_save or []:
            if use_primary:
                primary_button(label, handler, icon=icon)
            else:
                outline_button(label, handler, icon=icon)


def theme_chip_row() -> ui.row:
    """Row wrapper for removable theme-aware chips."""
    return ui.row().classes("w-full flex-wrap gap-1 items-center min-h-[28px]")

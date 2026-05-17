"""
Shared NiceGUI button factories for consistent styling across Mycelian.

Convention:
  - primary_button  — connect, new, save, apply (solid primary)
  - outline_button  — refresh, test connection, cancel, secondary actions
  - destructive_button — delete, disconnect, dangerous actions
  - success_button  — explicit enable/confirm only (not tab branding)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from nicegui import ui


def _apply_btn_classes(btn: ui.button, extra_classes: str) -> ui.button:
    """Padding/hover helper — do not use legacy .control-button (breaks Quasar colors)."""
    btn.classes(f"mycelian-btn {extra_classes}".strip())
    return btn


def primary_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Solid primary action (connect, new, save)."""
    btn = ui.button(label, on_click=on_click, icon=icon)
    btn.props("color=primary unelevated no-caps")
    return _apply_btn_classes(btn, extra_classes)


def outline_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Outlined secondary action (refresh, test, cancel)."""
    btn = ui.button(label, on_click=on_click, icon=icon)
    btn.props("outline color=primary no-caps")
    return _apply_btn_classes(btn, extra_classes)


def destructive_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Destructive action (delete, remove)."""
    btn = ui.button(label, on_click=on_click, icon=icon)
    btn.props("color=negative outline no-caps")
    return _apply_btn_classes(btn, extra_classes)


def success_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Explicit positive confirm (enable, confirm) — not for generic 'new' actions."""
    btn = ui.button(label, on_click=on_click, icon=icon)
    btn.props("color=positive unelevated no-caps")
    return _apply_btn_classes(btn, extra_classes)

"""
Shared NiceGUI button factories for consistent styling across Mycelian.

Convention:
  - primary_button  — connect, new, save, apply (solid primary)
  - outline_button  — refresh, test connection, cancel, secondary actions
  - destructive_button — delete, disconnect, dangerous actions
  - success_button  — explicit enable/confirm only (not tab branding)
  - dock_control_button — OBS-dock neutral chrome (activity feed toolbar)
  - warning_button  — spin / caution actions (source controls)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from nicegui import ui

_FLAT_BTN_PROPS = "flat no-caps"
_FLAT_DENSE_BTN_PROPS = "flat no-caps dense"


def apply_flat_btn_props(btn: ui.button, *, dense: bool = False) -> ui.button:
    """Strip Quasar primary fill so theme .btn-* / .control-button CSS applies."""
    btn.props(_FLAT_DENSE_BTN_PROPS if dense else _FLAT_BTN_PROPS)
    return btn


def _apply_btn_classes(btn: ui.button, extra_classes: str) -> ui.button:
    """Padding/hover helper — do not use legacy .control-button (breaks Quasar colors)."""
    btn.classes(f"mycelian-btn {extra_classes}".strip())
    return btn


def _styled_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
    quasar_props: str,
) -> ui.button:
    """Apply Quasar props first, then bind click — props() can drop handlers set in the constructor."""
    btn = ui.button(label, icon=icon)
    btn.props(quasar_props)
    btn.on_click(on_click)
    return _apply_btn_classes(btn, extra_classes)


def primary_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Solid primary action (connect, new, save)."""
    return _styled_button(
        label,
        on_click,
        icon=icon,
        extra_classes=extra_classes,
        quasar_props="color=primary unelevated no-caps",
    )


def outline_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Outlined secondary action (refresh, test, cancel)."""
    return _styled_button(
        label,
        on_click,
        icon=icon,
        extra_classes=extra_classes,
        quasar_props="outline color=primary no-caps",
    )


def destructive_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Destructive action (delete, remove)."""
    return _styled_button(
        label,
        on_click,
        icon=icon,
        extra_classes=extra_classes,
        quasar_props="color=negative outline no-caps",
    )


def success_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Explicit positive confirm (enable, confirm) — not for generic 'new' actions."""
    return _styled_button(
        label,
        on_click,
        icon=icon,
        extra_classes=extra_classes,
        quasar_props="color=positive unelevated no-caps",
    )


def dock_control_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
    dense: bool = True,
) -> ui.button:
    """Neutral OBS-dock toolbar button (hover overlay + border via ui_styles)."""
    quasar_props = "flat no-caps dense" if dense else "flat no-caps"
    return _styled_button(
        label,
        on_click,
        icon=icon,
        extra_classes=f"control-button {extra_classes}".strip(),
        quasar_props=quasar_props,
    )


def warning_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
) -> ui.button:
    """Caution / spin actions — theme warning fill via .btn-warning CSS."""
    btn = ui.button(label, icon=icon)
    btn.props("flat no-caps")
    btn.on_click(on_click)
    return _apply_btn_classes(btn, f"btn-warning {extra_classes}".strip())


def themed_control_button(
    label: str,
    on_click: Callable[..., Any],
    *,
    icon: Optional[str] = None,
    extra_classes: str = "",
    dense: bool = False,
) -> ui.button:
    """Semantic .btn-* button with control-button chrome (chatbot, giveaways)."""
    btn = ui.button(label, icon=icon)
    apply_flat_btn_props(btn, dense=dense)
    btn.on_click(on_click)
    return _apply_btn_classes(btn, f"control-button {extra_classes}".strip())

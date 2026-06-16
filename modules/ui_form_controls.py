"""Shared NiceGUI form controls with tooltips and consistent styling."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Union

from nicegui import ui

_DEFAULT_CLASSES = "w-full"
_FIELD_PROPS = "outlined dense"


def _with_tooltip(element: Any, tooltip: str) -> Any:
    if tooltip:
        element.tooltip(tooltip).classes("bg-theme-surface")
    return element


def form_select(
    *,
    tooltip: str,
    options: Union[List, dict],
    label: Optional[str] = None,
    value: Any = None,
    classes: str = _DEFAULT_CLASSES,
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    el = ui.select(
        options=options,
        label=label,
        value=value,
        on_change=on_change,
        **kwargs,
    )
    el.classes(classes).props(_FIELD_PROPS)
    return _with_tooltip(el, tooltip)


def form_input(
    *,
    tooltip: str,
    label: Optional[str] = None,
    value: Any = None,
    placeholder: Optional[str] = None,
    classes: str = _DEFAULT_CLASSES,
    password: bool = False,
    password_toggle_button: bool = False,
    readonly: bool = False,
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    el = ui.input(
        label=label,
        value=value,
        placeholder=placeholder,
        password=password,
        password_toggle_button=password_toggle_button,
        on_change=on_change,
        **kwargs,
    )
    el.classes(classes).props(_FIELD_PROPS)
    if readonly:
        el.props("readonly")
    return _with_tooltip(el, tooltip)


def form_sensitive_input(
    *,
    tooltip: str,
    label: Optional[str] = None,
    value: Any = None,
    placeholder: Optional[str] = None,
    classes: str = _DEFAULT_CLASSES,
    password: bool = True,
    readonly: bool = False,
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    """Text input masked by default with a visibility toggle."""
    return form_input(
        tooltip=tooltip,
        label=label,
        value=value,
        placeholder=placeholder,
        classes=classes,
        password=password,
        password_toggle_button=True,
        readonly=readonly,
        on_change=on_change,
        **kwargs,
    )


def form_sensitive_number(
    *,
    tooltip: str,
    label: Optional[str] = None,
    value: Any = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    step: Optional[float] = None,
    classes: str = _DEFAULT_CLASSES,
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    """Numeric input masked by default with a visibility toggle."""
    if value is None:
        display = ""
    elif isinstance(value, float) and value == int(value):
        display = str(int(value))
    elif isinstance(value, int):
        display = str(value)
    else:
        display = str(value)
    el = form_sensitive_input(
        tooltip=tooltip,
        label=label,
        value=display,
        classes=classes,
        on_change=on_change,
        **kwargs,
    )
    if min is not None:
        el.props(f"min={min}")
    if max is not None:
        el.props(f"max={max}")
    if step is not None:
        el.props(f"step={step}")
    return el


def form_number(
    *,
    tooltip: str,
    label: Optional[str] = None,
    value: Any = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    step: Optional[float] = None,
    classes: str = "",
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    el = ui.number(
        label=label,
        value=value,
        min=min,
        max=max,
        step=step,
        on_change=on_change,
        **kwargs,
    )
    el.classes(classes or "w-24").props(_FIELD_PROPS)
    return _with_tooltip(el, tooltip)


def form_textarea(
    *,
    tooltip: str,
    label: Optional[str] = None,
    value: Any = None,
    placeholder: Optional[str] = None,
    classes: str = _DEFAULT_CLASSES,
    rows: int = 3,
    on_change: Optional[Callable] = None,
    **kwargs: Any,
) -> Any:
    el = ui.textarea(
        label=label,
        value=value,
        placeholder=placeholder,
        on_change=on_change,
        **kwargs,
    )
    el.classes(classes).props(f"{_FIELD_PROPS} rows={rows}")
    return _with_tooltip(el, tooltip)

"""Lifecycle-safe timer scheduling for NiceGUI 3.x.

NiceGUI 3 made ``ui.timer`` a strict child of whatever slot is active when it is
created. When that parent slot is cleared (lazy tab loads, dialog closes, tab
rebuilds) a still-sleeping timer wakes up, fails to resolve its parent slot, and
raises ``RuntimeError: The parent slot of the element has been deleted``.

``app.timer`` has no parent slot (it uses ``nullcontext`` internally), so it
survives container clears and is the right tool for callbacks that only touch
Python state, module singletons, or UI through stored element references,
``ui.run_javascript`` or the broadcast notify path. Use :func:`app_schedule`
for those (the overwhelming majority of cases).

Use :func:`layout_schedule` only when a callback must create new NiceGUI
elements inline at fire time; it anchors the timer to the long-lived client
layout slot and returns the ``Timer`` so callers can ``cancel()`` it.
"""

from __future__ import annotations

from typing import Any, Callable

from nicegui import app, context, ui


def app_schedule(
    interval: float,
    callback: Callable[..., Any],
    *,
    once: bool = False,
    active: bool = True,
) -> None:
    """Schedule a timer that is not bound to any UI slot.

    Survives tab clears and dialog closes. Use for callbacks that do not create
    new UI elements inline (the default choice).
    """
    app.timer(interval, callback, once=once, active=active)


def layout_schedule(
    interval: float,
    callback: Callable[..., Any],
    *,
    once: bool = False,
    active: bool = True,
) -> ui.timer:
    """Schedule a timer anchored to the client layout slot.

    Use only when the callback creates new NiceGUI elements inline at fire time.
    Returns the ``Timer`` so callers can ``cancel()`` it.
    """
    with context.client.layout:
        return ui.timer(interval, callback, once=once, active=active)

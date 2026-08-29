# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
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

import logging
from typing import Any, Callable

from nicegui import app, context, ui

logger = logging.getLogger(__name__)


def run_on_ui_loop(fn: Callable[[], Any]) -> None:
    """Marshal a callback onto NiceGUI's asyncio loop.

    Timers are backed by asyncio tasks, and task creation is not thread-safe, so any
    worker thread that wants to schedule one must hop onto the loop first. Falls back
    to calling inline when the loop is not up yet, which is safe because scheduling
    then defers to app startup.
    """
    try:
        from nicegui import core

        loop = getattr(core, "loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn)
            return
    except Exception:
        pass
    try:
        fn()
    except Exception as exc:
        logger.error("ui_timer: loop callback failed: %s", exc, exc_info=True)


def app_schedule(
    interval: float,
    callback: Callable[..., Any],
    *,
    once: bool = False,
    active: bool = True,
) -> Any:
    """Schedule a timer that is not bound to any UI slot.

    Survives tab clears, dialog closes, and having no connected client at all, so this
    is also the only safe choice for work that must continue while the app sits in the
    system tray. Returns the ``Timer`` so callers can ``deactivate()`` it.
    """
    return app.timer(interval, callback, once=once, active=active)


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

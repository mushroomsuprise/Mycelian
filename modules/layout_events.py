# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Page-level custom events, listened for before the page is first drawn.

NiceGUI's browser code deletes and re-renders any element that gains a new
event listener after it was first drawn (``update`` in ``nicegui.js``). When
that element is ``client.layout`` the whole UI is rebuilt: scroll positions,
open dialogs, input focus, iframes and the Activity Feed renderer are lost.

Tabs that are built later (Custom Sources, the colour picker, Games Tracker,
toasts) therefore never call ``client.layout.on`` themselves. ``install`` adds
one listener per known event while ``build_root_ui`` runs, and features plug
their handlers in with ``listen`` (per page) or ``set_default`` (app-wide).
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)

EVENT_COLOR_PICKER = "mycelian-color-picker"
EVENT_CS_SPLIT = "mycelian-cs-split"
EVENT_GT_DRAWER_WIDTH = "mycelian-gtr-drawer-width"
EVENT_FEED = "mycelian-afx"
EVENT_TOAST_ACTION = "mycelian-toast-action"
EVENT_WINDOW_GEOMETRY = "mycelian-window-geometry"

EVENTS = (
    EVENT_COLOR_PICKER,
    EVENT_CS_SPLIT,
    EVENT_GT_DRAWER_WIDTH,
    EVENT_FEED,
    EVENT_TOAST_ACTION,
    EVENT_WINDOW_GEOMETRY,
)

Handler = Callable[[Any], Any]

_installed: Set[Any] = set()
_handlers: Dict[Any, Dict[str, Handler]] = {}
_defaults: Dict[str, Handler] = {}
_last_activity = 0.0


def mark_activity() -> None:
    """Record that the user just did something (tab switch, drag, click in a page widget)."""
    global _last_activity
    _last_activity = time.monotonic()


def idle_seconds() -> float:
    """Seconds since the last recorded user activity (large when there was none)."""
    return time.monotonic() - _last_activity


def _key(client: Any) -> Any:
    return getattr(client, "id", None) or id(client)


def install(client: Any) -> bool:
    """Listen for every known page event on ``client``. Returns True the first time."""
    key = _key(client)
    if key in _installed:
        return False
    _installed.add(key)
    for event in EVENTS:
        client.layout.on(event, functools.partial(_dispatch, key, event))
    on_delete = getattr(client, "on_delete", None)
    if callable(on_delete):
        on_delete(lambda: forget(key))
    return True


def install_current() -> None:
    """``install`` for the page in the current NiceGUI context, if there is one."""
    try:
        from nicegui import context

        client = context.client
    except Exception:
        return
    install(client)


def listen(client: Any, event: str, handler: Handler) -> None:
    """Send ``event`` from ``client``'s page to ``handler`` (replacing an earlier one)."""
    if event not in EVENTS:
        raise ValueError(f"Unknown page event {event!r}: add it to layout_events.EVENTS")
    if install(client):
        logger.debug("Page events installed late for %s (first listener: %s)", _key(client), event)
    _handlers.setdefault(_key(client), {})[event] = handler


def set_default(event: str, handler: Handler) -> None:
    """Handle ``event`` on every page that has no page-specific handler."""
    if event not in EVENTS:
        raise ValueError(f"Unknown page event {event!r}: add it to layout_events.EVENTS")
    _defaults[event] = handler


def handler_for(client: Any, event: str) -> Optional[Handler]:
    return _handlers.get(_key(client), {}).get(event) or _defaults.get(event)


def forget(key: Any) -> None:
    _installed.discard(key)
    _handlers.pop(key, None)


def _dispatch(key: Any, event: str, e: Any) -> Any:
    mark_activity()
    handler = _handlers.get(key, {}).get(event) or _defaults.get(event)
    if handler is None:
        return None
    return handler(e)

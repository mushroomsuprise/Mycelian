# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Main tab: Games Tracker (the page itself lives in tabs/games_tracker_tab.py).

The main window builds it lazily and pauses its timers while another main tab is
showing. Its options are a Settings sub-tab (tabs/games_tracker_settings_tab.py).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from nicegui import ui

logger = logging.getLogger(__name__)

_tab: Optional[Any] = None  # the built GamesTrackerTab


def get_games_tracker_tab() -> Optional[Any]:
    """The built page, or None before the tab was first opened."""
    return _tab


def create_games_tracker_tab() -> None:
    """Build the Games Tracker main tab in the current container."""
    global _tab
    from .tabs.games_tracker_tab import GamesTrackerTab

    tab = GamesTrackerTab()
    with ui.element("div").classes("tab-surface w-full flex-1 min-h-0 flex flex-col p-4"):
        with ui.scroll_area().classes("w-full h-full grow"):
            container = ui.column().classes("w-full gap-4")
    tab.build(container)
    _tab = tab


def pause_games_tracker() -> None:
    """Stop the refresh timers while another main tab is showing."""
    tab = _tab
    if tab is not None:
        try:
            tab.on_exit()
        except Exception:
            logger.debug("Games Tracker on_exit failed", exc_info=True)


def resume_games_tracker() -> None:
    """Restart the timers and refresh at once when the tab shows again."""
    tab = _tab
    if tab is not None:
        try:
            tab.on_enter()
        except Exception:
            logger.debug("Games Tracker on_enter failed", exc_info=True)

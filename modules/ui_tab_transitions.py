# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Tab panel slide direction helpers for Quasar QTabPanels.

Set transition-prev / transition-next based on tab index in a canonical order
so slides move left when navigating to a tab farther right, and vice versa.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Union

# Canonical main tab order (matches mainuiwindow.py tab strip)
MAIN_TAB_ORDER: List[str] = [
    "Activity Feed",
    "Alerts",
    "Source Settings",
    "Source Controls",
    "Connectors",
    "Chatbot",
    "Spore Studio",
    "Settings",
]

# Canonical settings subtab order (matches build_ui_v2 tab strip)
SETTINGS_TAB_ORDER: List[str] = [
    "Twitch",
    "OBS",
    "PSN",
    "Spotify",
    "YouTube",
    "Game Hooks",
    "Database",
    "Statistics",
    "Theme",
    "App Settings",
    "About",
]


def _tab_label(tab: Any) -> str:
    """Resolve a tab object or string to a display name."""
    if tab is None:
        return ""
    if isinstance(tab, str):
        return tab
    for attr in ("text", "label", "name"):
        val = getattr(tab, attr, None)
        if val:
            return str(val)
    return str(tab)


def apply_tab_slide_direction(
    tab_panels: Any,
    prev_tab: Any,
    new_tab: Any,
    order: Sequence[str],
) -> None:
    """
    Configure Quasar tab panel transitions before switching tabs.

    Moving to a higher index (right in the bar): next panel slides in from right.
    Moving to a lower index: next panel slides in from left.
    """
    if tab_panels is None:
        return
    prev_name = _tab_label(prev_tab)
    new_name = _tab_label(new_tab)
    if not prev_name or not new_name or prev_name == new_name:
        return
    try:
        old_idx = order.index(prev_name)
        new_idx = order.index(new_name)
    except ValueError:
        return
    if new_idx > old_idx:
        tab_panels.props(
            "transition-prev=slide-right transition-next=slide-left"
        )
    else:
        tab_panels.props(
            "transition-prev=slide-left transition-next=slide-right"
        )


def tab_index(tab: Any, order: Sequence[str]) -> Optional[int]:
    """Return index of tab in order, or None if not found."""
    name = _tab_label(tab)
    try:
        return order.index(name)
    except ValueError:
        return None

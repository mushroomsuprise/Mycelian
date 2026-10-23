# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Is a Chatbot sub-tab on screen? Lets periodic UI refreshes skip hidden panels.

A panel counts as hidden only when the main tab strip (or the Chatbot sub-tab strip)
positively shows a different, known tab; anything unexpected counts as visible, so a
refresh is never wrongly skipped. Callbacks registered with
:func:`on_chatbot_subtab_shown` run as soon as their panel is shown again, so the
user sees the same up-to-date content they would have seen without the skip.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_MAIN_TAB_LABELS = frozenset(
    {
        "Activity Feed",
        "Alerts",
        "Source Settings",
        "Source Controls",
        "Connectors",
        "Chatbot",
        "Spore Studio",
        "Settings",
    }
)
_CHATBOT_SUB_TABS = frozenset(
    {"Commands", "Events", "Quotes", "Greetings", "Giveaways", "Polls"}
)

_show_callbacks: Dict[str, Callable[[], Any]] = {}
# Strips already hooked (kept by reference so a rebuilt strip is never mistaken for one).
_hooked_tabs: List[Any] = []


def _tab_strips() -> tuple:
    try:
        from ..help_system import contextual_help as ch

        return ch._main_tabs, ch._chatbot_tabs
    except Exception:
        return None, None


def _label(tabs: Any) -> str:
    if tabs is None:
        return ""
    try:
        from ..ui_tab_transitions import _tab_label

        return _tab_label(tabs.value)
    except Exception:
        return ""


def chatbot_subtab_hidden(sub_tab: str) -> bool:
    """True only when another known main tab or Chatbot sub-tab is showing."""
    main_tabs, chatbot_tabs = _tab_strips()
    _ensure_hooks(main_tabs, chatbot_tabs)
    main = _label(main_tabs)
    if main in _MAIN_TAB_LABELS and main != "Chatbot":
        return True
    current = _label(chatbot_tabs)
    return current in _CHATBOT_SUB_TABS and current != sub_tab


def on_chatbot_subtab_shown(sub_tab: str, callback: Callable[[], Any]) -> None:
    """Run ``callback`` whenever ``sub_tab`` becomes visible (replaces a previous one)."""
    _show_callbacks[sub_tab] = callback
    _ensure_hooks(*_tab_strips())


def _ensure_hooks(*strips: Any) -> None:
    for tabs in strips:
        if tabs is None or any(t is tabs for t in _hooked_tabs):
            continue
        try:
            tabs.on_value_change(_on_tab_change)
            _hooked_tabs.append(tabs)
        except Exception:
            logger.debug("tab visibility hook failed", exc_info=True)


def _on_tab_change(_e: Any = None) -> None:
    for sub_tab, callback in list(_show_callbacks.items()):
        if chatbot_subtab_hidden(sub_tab):
            continue
        try:
            callback()
        except Exception:
            logger.debug("tab shown callback failed for %s", sub_tab, exc_info=True)

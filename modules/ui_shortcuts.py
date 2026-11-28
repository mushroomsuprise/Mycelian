# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""App-wide keyboard shortcuts for the main window.

Ctrl+S (Windows / Linux) or Cmd+S (macOS) saves whatever is in front: an open
chatbot or connector editor first, otherwise the visible Alerts, Settings or
Source Settings form. Places that already handle the key themselves (the Database
explorer) call ``preventDefault`` and are left alone.

The browser side is one document listener (:data:`SHORTCUTS_HEAD_HTML`); it reports
through the page event ``layout_events.EVENT_SAVE_SHORTCUT`` so no element gains a
listener after the page is drawn.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from . import layout_events

logger = logging.getLogger(__name__)

SHORTCUTS_HEAD_HTML = """
<script id="mycelian-shortcuts-js">
(function () {
  if (window.__mycelianShortcuts) return;
  window.__mycelianShortcuts = true;
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.altKey || e.repeat) return;
    if (!(e.metaKey || e.ctrlKey)) return;
    if (e.key !== 's' && e.key !== 'S') return;
    e.preventDefault();
    if (typeof emitEvent === 'function') emitEvent('mycelian-save', {});
  });
})();
</script>
"""


def _open_editor() -> Optional[Any]:
    """The chatbot or connector editor dialog that is open, if any."""
    for module_name in ("chatbot", "connectors"):
        try:
            module = __import__(f"modules.uiwindows.{module_name}", fromlist=["_active_editor"])
        except Exception:
            continue
        editor = getattr(module, "_active_editor", None)
        dialog = getattr(editor, "dialog", None)
        if dialog is not None and not getattr(dialog, "is_deleted", False) and getattr(dialog, "value", False):
            return editor
    return None


def _current_main_tab() -> str:
    try:
        from .help_system.contextual_help import get_current_tab_context

        return get_current_tab_context()[0] or ""
    except Exception:
        return ""


def _save_alerts() -> bool:
    from .uiwindows.alertsettings import alerts_have_unsaved_changes, save_active_alert

    if not alerts_have_unsaved_changes():
        return False
    save_active_alert(lambda *_args: None)
    return True


def _save_settings() -> bool:
    from .uiwindows.settings import settings_ui

    name = getattr(settings_ui, "_active_tab_name", "") or ""
    if not name or not settings_ui._tab_is_dirty(name):
        return False
    settings_ui.save_tab(name)
    return True


def _save_source_settings() -> bool:
    from .uiwindows.customsources import save_open_source_settings, source_settings_is_dirty

    if not source_settings_is_dirty():
        return False
    save_open_source_settings()
    return True


_TAB_SAVERS: dict[str, Callable[[], bool]] = {
    "Alerts": _save_alerts,
    "Settings": _save_settings,
    "Source Settings": _save_source_settings,
}


def handle_save_shortcut(_event: Any = None) -> str:
    """Save what is in front. Returns what was saved ("" when nothing needed saving)."""
    editor = _open_editor()
    if editor is not None:
        try:
            editor.save()
            return "editor"
        except Exception:
            logger.error("Save shortcut: editor save failed", exc_info=True)
            return ""
    tab = _current_main_tab()
    saver = _TAB_SAVERS.get(tab)
    if saver is None:
        return ""
    try:
        return tab if saver() else ""
    except Exception:
        logger.error("Save shortcut: saving %s failed", tab, exc_info=True)
        return ""


def install() -> None:
    """Route the page's save shortcut here (app-wide, every page)."""
    layout_events.set_default(layout_events.EVENT_SAVE_SHORTCUT, handle_save_shortcut)

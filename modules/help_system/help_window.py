# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help window

Help opens in its own native window so the docs can stay open beside Mycelian.
The window is a second pywebview window owned by the webview process (see
``native_window_bridge.open_help_window``) showing the ``/help`` page served here.

- Opening help while the window is already open navigates it in place (history is
  kept) and brings it forward.
- Size and position are saved in AppSettings (``help_window_*``) and restored on
  the next open.
- Theme switches are pushed to the help window live.
- Without a native window (e.g. browser mode) callers fall back to the dialog.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

HELP_PATH = "/help"
HELP_WINDOW_TITLE = "Mycelian Help"
_THEME_STYLE_ID = "mycelian-help-theme"
_MIN_WIDTH, _MIN_HEIGHT = 720, 480

_registered = False
# client id -> (client, HelpBrowser). Normally zero or one entry.
_live: Dict[str, Tuple[Any, Any]] = {}


# ----- theme -----

def _current_theme() -> Tuple[Any, str]:
    from ..theme_manager import get_theme_manager

    manager = get_theme_manager()
    theme = manager.get_theme()
    theme_type = manager.get_theme_type(theme.name) if theme else "dark"
    return theme, theme_type


def _theme_css(theme) -> str:
    from ..mainuiwindow import _get_quasar_brand_css
    from ..theme_manager import generate_css_variables

    return generate_css_variables(theme) + _get_quasar_brand_css(theme)


def _rgb_to_hex(value: str) -> Optional[str]:
    """pywebview wants ``#RRGGBB``; themes store ``rgb(r, g, b)``."""
    value = (value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
    if not m:
        return None
    return "#" + "".join(f"{min(int(c), 255):02x}" for c in m.groups())


def push_theme() -> None:
    """Re-apply the active theme in every open help window (called after a theme switch)."""
    if not _live:
        return
    try:
        theme, theme_type = _current_theme()
        if theme is None:
            return
        css = json.dumps(_theme_css(theme))
    except Exception as e:
        logger.debug("help window: could not build theme update: %s", e)
        return
    is_dark = "true" if theme_type == "dark" else "false"
    js = f"""
    (function() {{
        var style = document.getElementById('{_THEME_STYLE_ID}');
        if (!style) {{
            style = document.createElement('style');
            style.id = '{_THEME_STYLE_ID}';
            document.head.appendChild(style);
        }}
        style.textContent = {css};
        if (window.Quasar && Quasar.Dark) Quasar.Dark.set({is_dark});
    }})();
    """
    for client, _browser in list(_live.values()):
        try:
            client.run_javascript(js)
        except Exception as e:
            logger.debug("help window: theme push failed: %s", e)


# ----- page -----

def _help_page(topic: str = "", category: str = "") -> None:
    from nicegui import context, ui

    from .help_browser import HelpBrowser

    ui.colors(primary="var(--color-primary)")
    try:
        from ..ui_font import get_app_font_css_block

        theme, theme_type = _current_theme()
        # The shared head carries the theme from app start; this page-level style sits
        # after it and holds the theme that is active now.
        ui.add_head_html(f'<style id="{_THEME_STYLE_ID}">{_theme_css(theme)}</style>')
        ui.add_head_html(f"<style>{get_app_font_css_block()}</style>")
        ui.dark_mode(theme_type == "dark")
    except Exception as e:
        logger.debug("help window: could not apply theme: %s", e)

    ui.add_head_html(
        "<style>"
        ".nicegui-content{padding:0!important;height:100vh;}"
        ".help-window-root{width:100vw;height:100vh;overflow:hidden;"
        "background:var(--color-bg-elevated);color:var(--color-text-primary);}"
        "</style>"
    )

    browser = HelpBrowser()
    browser.build_page(initial_topic=topic or None, category=category or None)

    client = context.client
    _live[client.id] = (client, browser)
    client.on_delete(lambda: _live.pop(client.id, None))


def _save_geometry(event) -> None:
    args = getattr(event, "args", None) or {}
    try:
        from ..dataobjects import state_manager

        width = int(args.get("width") or 0)
        height = int(args.get("height") or 0)
        updates = []
        if width >= _MIN_WIDTH and height >= _MIN_HEIGHT:
            updates += [("help_window_width", width), ("help_window_height", height)]
        if args.get("x") is not None and args.get("y") is not None:
            updates += [
                ("help_window_x", int(args["x"])),
                ("help_window_y", int(args["y"])),
                ("help_window_has_position", True),
            ]
        settings = state_manager.get_app_settings()
        changed = False
        for field, value in updates:
            if getattr(settings, field, None) != value:
                changed = state_manager.update_app_setting(field, value) or changed
        if changed:
            state_manager.save_changes()
    except Exception as e:
        logger.debug("help window: could not save geometry: %s", e)


def register_help_page() -> None:
    """Add the ``/help`` route and the geometry listener. Call before ``ui.run``."""
    global _registered
    if _registered:
        return
    from nicegui import ui

    ui.page(HELP_PATH, title=HELP_WINDOW_TITLE)(_help_page)
    try:
        from nicegui.native.event_manager import event_manager

        event_manager.on("mycelian_help_geometry", _save_geometry)
    except ImportError:
        logger.debug("help window: native event_manager unavailable")
    _registered = True


# ----- opening -----

def _live_browser() -> Optional[Tuple[Any, Any]]:
    for client_id, (client, browser) in list(_live.items()):
        if getattr(client, "has_socket_connection", True):
            return client, browser
        _live.pop(client_id, None)
    return None


def _native_available() -> bool:
    try:
        from nicegui import app

        return getattr(app.native, "main_window", None) is not None
    except Exception:
        return False


def _window_options() -> dict:
    options: dict = {"title": HELP_WINDOW_TITLE}
    try:
        from ..dataobjects import state_manager

        settings = state_manager.get_app_settings()
        width = int(getattr(settings, "help_window_width", 0) or 0)
        height = int(getattr(settings, "help_window_height", 0) or 0)
        if width >= _MIN_WIDTH and height >= _MIN_HEIGHT:
            options.update(width=width, height=height)
        if getattr(settings, "help_window_has_position", False):
            options.update(x=int(settings.help_window_x), y=int(settings.help_window_y))
    except Exception as e:
        logger.debug("help window: could not read saved geometry: %s", e)
    try:
        theme, _ = _current_theme()
        background = _rgb_to_hex(getattr(theme, "bg_elevated", "") if theme else "")
        if background:
            options["background_color"] = background
    except Exception:
        pass
    return options


def _help_url(topic_id: Optional[str], category: Optional[str]) -> Optional[str]:
    from ..tray_controller import get_window_url

    base = get_window_url()
    if not base:
        return None
    query = {k: v for k, v in (("topic", topic_id), ("category", category)) if v}
    url = base.rstrip("/") + HELP_PATH
    return f"{url}?{urlencode(query)}" if query else url


def open_help_window(topic_id: Optional[str] = None, category: Optional[str] = None) -> bool:
    """Show help in the help window. Returns False when no window can be used."""
    from ..tray_controller import push_window_method

    live = _live_browser()
    if live is not None:
        client, browser = live
        try:
            with client:
                if topic_id:
                    browser.navigate_to(topic_id)
                elif category:
                    browser._navigate_to_category(category)
            push_window_method("mycelian_focus_help_window")
            return True
        except Exception as e:
            logger.debug("help window: in-place navigation failed, reloading: %s", e)

    if not _native_available():
        return False
    url = _help_url(topic_id, category)
    if not url:
        return False
    return push_window_method("mycelian_open_help_window", url, _window_options())

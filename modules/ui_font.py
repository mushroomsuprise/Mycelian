# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Apply user-selected app font via injected CSS."""

from __future__ import annotations

import logging

from nicegui import ui

from .font_utils import resolve_font_css

logger = logging.getLogger(__name__)


def get_app_font_css_block(family: str | None = None) -> str:
    """Return a <style> body for #mycelian-font-override (no script tags)."""
    if family is None:
        try:
            from .dataobjects import state_manager

            family = (
                getattr(state_manager.get_app_settings(), "ui_font_family", "") or ""
            )
        except Exception:
            family = ""

    css_value = resolve_font_css(family)
    return f"""
:root {{
    --font-family-app: {css_value};
}}
body, .q-app, #app, .nicegui-content {{
    font-family: var(--font-family-app) !important;
}}
"""


def apply_app_font(family: str | None = None) -> None:
    """Update global font-family in the browser (requires an active client)."""
    css_block = get_app_font_css_block(family)
    escaped = css_block.replace("`", "\\`").replace("${", "\\${")
    js = f"""
(function() {{
    var style = document.getElementById('mycelian-font-override');
    if (!style) {{
        style = document.createElement('style');
        style.id = 'mycelian-font-override';
        document.head.appendChild(style);
    }}
    style.textContent = `{escaped}`;
}})();
"""
    try:
        from nicegui import context

        if context.client is None:
            logger.debug("apply_app_font skipped: no UI client yet")
            return
        ui.run_javascript(js)
    except Exception as exc:
        logger.debug("apply_app_font failed: %s", exc)

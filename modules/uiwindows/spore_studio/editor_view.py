#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
NiceGUI host for the Spore Studio iframe editor.

The tab renders one of two states:

* A placeholder card when the web engine isn't running yet (e.g. the user
  opened Spore Studio before the alert system started). A "Retry" button
  re-attempts the iframe load.
* A full-bleed iframe pointing at ``http://127.0.0.1:{port}/_spore_studio_editor``
  once the server is up. The iframe handles all editor interaction itself;
  this module never reaches into it.
"""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import Any, Dict

from nicegui import ui

from ...ui_timer import layout_schedule
from .. import customsources as _customsources_module  # noqa: F401  (style consistency)
from ... import web_engine as web_engine_module

logger = logging.getLogger(__name__)


_TAB_CSS = """
.spore-studio-host {
    background: var(--color-bg-base);
    color: var(--color-text-primary);
}
.spore-studio-host iframe {
    width: 100%;
    height: 100%;
    border: 0;
    background: var(--color-bg-base);
}
.spore-studio-empty {
    color: var(--color-text-secondary);
    text-align: center;
}
.spore-studio-toolbar {
    border-bottom: 1px solid var(--color-border-default);
    background: var(--color-bg-surface);
}
"""

_INJECTED_CSS = {"injected": False}


def _inject_css() -> None:
    if _INJECTED_CSS["injected"]:
        return
    ui.add_head_html(
        f"<style id='spore-studio-tab-css'>{_TAB_CSS}</style>", shared=True
    )
    # The editor lives in a cross-origin iframe. A click that prevents default
    # can leave keyboard focus on the main tab strip, so arrow keys move tabs
    # instead of the selected element. Forward those keys into the editor.
    ui.run_javascript(
        """
        if (!window.__mycelianSporeNudge) {
            window.__mycelianSporeNudge = true;
            document.addEventListener('keydown', function (ev) {
                if (!ev.key || ev.key.indexOf('Arrow') !== 0) { return; }
                var target = ev.target;
                if (!target || !target.closest) { return; }
                if (target.closest('input, textarea, select, [contenteditable="true"]')) {
                    return;
                }
                if (!target.closest('.q-tab, .q-tabs')) { return; }
                var host = document.querySelector('.spore-studio-host');
                if (!host || host.offsetParent === null) { return; }
                var iframe = host.querySelector('iframe');
                if (!iframe || !iframe.contentWindow) { return; }
                iframe.contentWindow.postMessage({
                    source: 'mycelian-host',
                    type: 'spore-nudge',
                    key: ev.key,
                    shiftKey: !!ev.shiftKey
                }, '*');
                ev.preventDefault();
                ev.stopPropagation();
            }, true);
        }
        """
    )
    _INJECTED_CSS["injected"] = True


def _editor_url() -> str:
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if inst is None or not getattr(inst, "is_running", False):
        return ""
    port = getattr(inst, "port", 5000) or 5000
    return f"http://127.0.0.1:{port}/_spore_studio_editor"


def _state() -> Dict[str, Any]:
    return {"iframe": None, "placeholder": None, "container": None}


def create_spore_studio_tab() -> None:
    """
    Build the Spore Studio tab body.

    The function is invoked lazily by ``mainuiwindow.create_ui_elements``
    via the same ``LazyTabPanel`` mechanism every other tab uses, so we
    can safely poll the web engine here without delaying app startup.
    """
    _inject_css()
    state = _state()

    with ui.element("div").classes(
        "spore-studio-host w-full h-full min-h-0 flex flex-col"
    ) as container:
        state["container"] = container

        with ui.row().classes(
            "spore-studio-toolbar w-full items-center justify-end gap-1 px-2 py-1 shrink-0"
        ):
            ui.button(
                "Reload editor",
                icon="refresh",
                on_click=lambda: _refresh_iframe(state),
            ).props("dense flat").tooltip(
                "Reload the editor after the overlay server restarts"
            )
            ui.button(
                "Open externally",
                icon="open_in_new",
                on_click=_open_externally,
            ).props("dense flat").tooltip(
                "Open the editor in your default browser"
            )

        body = ui.element("div").classes("w-full grow relative min-h-0")
        state["body"] = body

        with body:
            iframe = ui.element("iframe").classes("block w-full h-full border-0")
            state["iframe"] = iframe
            iframe.props("allow=clipboard-read;clipboard-write")

            placeholder = (
                ui.column()
                .classes(
                    "absolute inset-0 items-center justify-center " "spore-studio-empty"
                )
                .style("display:none;gap:8px;")
            )
            state["placeholder"] = placeholder
            with placeholder:
                ui.icon("hourglass_empty", size="2rem")
                ui.label("Waiting for the overlay server to start…").classes("text-sm")
                ui.label(
                    "Spore Studio runs in-browser inside an iframe served "
                    "by Mycelian's web engine. The server starts with the "
                    "alert system."
                ).classes("text-xs opacity-70")
                ui.button(
                    "Retry",
                    icon="refresh",
                    on_click=lambda: _refresh_iframe(state),
                ).props("dense").classes("mt-2")

    def _poll_editor(remaining: int = 40) -> None:
        url = _editor_url()
        _refresh_iframe(state)
        if url or remaining <= 0:
            return
        layout_schedule(1.5, lambda: _poll_editor(remaining - 1), once=True)

    layout_schedule(0.5, lambda: _poll_editor(), once=True)


def _refresh_iframe(state: Dict[str, Any]) -> None:
    iframe = state.get("iframe")
    placeholder = state.get("placeholder")
    if iframe is None or placeholder is None:
        return
    url = _editor_url()
    if not url:
        iframe.props("src=")
        iframe.style("display:none")
        placeholder.style("display:flex")
        return
    iframe.style("display:block")
    # Cache-bust so the iframe actually reloads when the URL is otherwise
    # unchanged — without this, calling props(src=...) with the same value
    # is a no-op and the "Reload editor" button silently does nothing.
    cache_busted = f"{url}?_cb={int(time.time() * 1000)}"
    iframe.props(f'src="{cache_busted}"')
    placeholder.style("display:none")


def _open_externally() -> None:
    url = _editor_url()
    if not url:
        from ...notification_engine import notify

        notify("Overlay server is not running yet.", type="warning")
        return
    # Server-side webbrowser.open is the pattern used elsewhere in the app
    # (settings, help_browser, spotify) and is far more reliable than
    # ui.run_javascript("window.open(...)") which is async-only and gets
    # blocked by browser popup heuristics when not in a click-stack.
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        logger.warning("Failed to open Spore Studio externally: %s", e)
        from ...notification_engine import notify

        notify(f"Could not open browser: {e}", type="negative")

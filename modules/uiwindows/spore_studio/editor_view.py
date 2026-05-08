#!/usr/bin/env python3
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
from typing import Any, Dict

from nicegui import ui

from .. import customsources as _customsources_module  # noqa: F401  (style consistency)
from ... import web_engine as web_engine_module

logger = logging.getLogger(__name__)


_TAB_CSS = """
.spore-studio-host {
    background: #0f1115;
    color: #e7e9ee;
}
.spore-studio-host iframe {
    width: 100%;
    height: 100%;
    border: 0;
    background: #0f1115;
}
.spore-studio-empty {
    color: #b8bec9;
    text-align: center;
}
"""

_INJECTED_CSS = {"injected": False}


def _inject_css() -> None:
    if _INJECTED_CSS["injected"]:
        return
    ui.add_head_html(f"<style id='spore-studio-tab-css'>{_TAB_CSS}</style>", shared=True)
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
        "spore-studio-host w-full h-full flex flex-col"
    ) as container:
        state["container"] = container

        with ui.row().classes(
            "w-full items-center justify-between px-3 py-1 border-b "
            "border-[var(--color-border-default)]"
        ):
            with ui.column().classes("gap-0"):
                ui.label("Spore Studio").classes("text-lg font-medium")
                ui.label(
                    "Visual editor for Mycelian HTML templates and their "
                    "JSON configurations."
                ).classes("text-xs opacity-70")

            with ui.row().classes("items-center gap-2"):
                refresh_btn = ui.button(
                    icon="refresh",
                    text="Reload editor",
                    on_click=lambda: _refresh_iframe(state),
                ).props("dense flat").classes("text-xs")
                refresh_btn.tooltip("Reload the editor inside the iframe")

                open_external_btn = ui.button(
                    icon="open_in_new",
                    text="Open externally",
                    on_click=lambda: _open_externally(),
                ).props("dense flat").classes("text-xs")
                open_external_btn.tooltip(
                    "Open the editor in your default browser"
                )

        body = ui.element("div").classes("w-full flex-grow relative")
        state["body"] = body

        with body:
            iframe = ui.element("iframe").classes(
                "block w-full h-full border-0"
            )
            state["iframe"] = iframe
            iframe.props("allow=clipboard-read;clipboard-write")

            placeholder = (
                ui.column()
                .classes(
                    "absolute inset-0 items-center justify-center "
                    "spore-studio-empty"
                )
                .style("display:none;gap:8px;")
            )
            state["placeholder"] = placeholder
            with placeholder:
                ui.icon("hourglass_empty", size="2rem")
                ui.label(
                    "Waiting for the overlay server to start…"
                ).classes("text-sm")
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

    ui.timer(0.5, lambda: _refresh_iframe(state), once=True)


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
    iframe.props(f'src="{url}"')
    placeholder.style("display:none")


def _open_externally() -> None:
    url = _editor_url()
    if not url:
        from ...notification_engine import notify

        notify("Overlay server is not running yet.", type="warning")
        return
    ui.run_javascript(f"window.open({url!r}, '_blank');")

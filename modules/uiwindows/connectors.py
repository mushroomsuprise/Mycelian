#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from nicegui import background_tasks, context, run, ui
from ..notification_engine import notify
from ..ui_buttons import outline_button, primary_button
from ..ui_timer import layout_schedule
from ..ui_form_controls import form_input, form_number, form_select


from .. import (
    connector_actions,
    connector_integration,
    connector_layout_store,
    connector_manager,
    connector_triggers,
)
from ..connector_core import (
    ActionType,
    ComparisonOperator,
    Connector,
    TriggerCondition,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Global references for UI state
connectors_container = None
selected_connector = None
create_dialog = None
edit_dialog = None
search_input = None
current_search = ""
connector_cards = {}  # Store connector cards by ID for search functionality
folder_cards = {}  # folder_id -> folder wrapper element for search
connector_parent_folder: Dict[str, Optional[str]] = (
    {}
)  # connector_id -> folder_id if inside a folder, else None
_client_drag_state: Dict[int, Dict[str, Optional[str]]] = {}
# Host for folder floating panels (survives connectors_container.clear())
_folder_dialog_host: Optional[Any] = None
_folder_floaters: Dict[str, Dict[str, Any]] = {}
folder_tile_title_labels: Dict[str, Any] = {}
# Quasar / NiceGUI dialogs use z-index ~6000+; keep floaters below so edit/create dialogs stack on top.
_FOLDER_FLOAT_Z_HOST = 5500
_FOLDER_FLOAT_Z_SHELL_BASE = 5510

# Add custom CSS for the connectors UI
CUSTOM_CSS = """
.fade-in {
    animation: fadeIn 0.3s ease-in-out;
}

.scale-in {
    animation: scaleIn 0.2s ease-out;
}

.slide-in {
    animation: slideIn 0.3s ease-out;
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes scaleIn {
    from { transform: scale(0.95); opacity: 0; }
    to { transform: scale(1); opacity: 1; }
}

@keyframes slideIn {
    from { transform: translateY(-10px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

.connector-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-accent);
}

.connector-card.connector-card-enabled {
    border-color: var(--color-success);
}

.connector-card.connector-card-disabled {
    border-color: var(--color-error);
}

.connector-card.connector-card-enabled:hover,
.connector-card.connector-card-disabled:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
}

.connector-card.connector-card-enabled:hover {
    border-color: var(--color-success);
}

.connector-card.connector-card-disabled:hover {
    border-color: var(--color-error);
}

.connector-card:not(.connector-card-enabled):not(.connector-card-disabled):hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
    border-color: var(--color-primary);
}

.trigger-badge {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(37, 99, 235, 0.3));
    border: 1px solid rgba(59, 130, 246, 0.4);
    color: var(--color-info);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.action-badge {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(5, 150, 105, 0.3));
    border: 1px solid rgba(16, 185, 129, 0.4);
    color: var(--color-success);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
    width: fit-content;
}

.condition-badge {
    background: linear-gradient(135deg, rgba(147, 51, 234, 0.2), rgba(126, 34, 206, 0.3));
    border: 1px solid rgba(147, 51, 234, 0.4);
    color: var(--color-primary);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
    width: fit-content;
}

.connector-flow {
    width: 100%;
}

.connector-flow-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--color-text-muted);
}

.connector-flow-arrow {
    color: var(--color-text-muted);
    align-self: center;
    margin: 2px 0;
    opacity: 0.7;
}

.form-section {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.form-section-title {
    color: var(--color-primary);
    font-weight: 600;
    font-size: 18px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--color-border-accent);
    padding-bottom: 4px;
}

.control-button {
    transition: all 0.2s ease;
}

.control-button:hover {
    transform: translateY(-1px);
    opacity: 0.9;
}

.dialog-section {
    background: var(--color-bg-elevated);
    border: 1px solid var(--color-border-default);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
}



/* Trigger Select Styling - Blue Theme */
.trigger-select .q-field__control::before {
    border-color: rgba(59, 130, 246, 0.4) !important;
}

.trigger-select .q-field__control::after {
    border-color: var(--color-info) !important;
}

.trigger-select .q-field__control {
    background: rgba(59, 130, 246, 0.1) !important;
    border-radius: 4px !important;
}

.trigger-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.trigger-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.trigger-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Action Select Styling - Green Theme */
.action-select .q-field__control::before {
    border-color: rgba(16, 185, 129, 0.4) !important;
}

.action-select .q-field__control::after {
    border-color: var(--color-success) !important;
}

.action-select .q-field__control {
    background: rgba(16, 185, 129, 0.1) !important;
    border-radius: 4px !important;
}

.action-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.action-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.action-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Condition Select Styling - Purple Theme */
.condition-select .q-field__control::before {
    border-color: rgba(147, 51, 234, 0.4) !important;
}

.condition-select .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.condition-select .q-field__control {
    background: rgba(147, 51, 234, 0.1) !important;
    border-radius: 4px !important;
}

.condition-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.condition-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.condition-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Input field styling for color themes */
.trigger-input .q-field__control::before {
    border-color: rgba(59, 130, 246, 0.4) !important;
}

.trigger-input .q-field__control::after {
    border-color: var(--color-info) !important;
}

.trigger-input .q-field__control {
    background: rgba(59, 130, 246, 0.1) !important;
    border-radius: 4px !important;
}

.trigger-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.trigger-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.action-input .q-field__control::before {
    border-color: rgba(16, 185, 129, 0.4) !important;
}

.action-input .q-field__control::after {
    border-color: var(--color-success) !important;
}

.action-input .q-field__control {
    background: rgba(16, 185, 129, 0.1) !important;
    border-radius: 4px !important;
}

.action-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.action-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.condition-input .q-field__control::before {
    border-color: rgba(147, 51, 234, 0.4) !important;
}

.condition-input .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.condition-input .q-field__control {
    background: rgba(147, 51, 234, 0.1) !important;
    border-radius: 4px !important;
}

.condition-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.condition-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

/* Container background styling */
.condition-container {
    background: linear-gradient(135deg, rgba(147, 51, 234, 0.1), rgba(147, 51, 234, 0.05)) !important;
    border: 1px solid rgba(147, 51, 234, 0.2) !important;
}

.condition-fields-row {
    flex-wrap: nowrap !important;
}

.condition-field-cell,
.condition-operator-cell {
    flex: 0 1 auto !important;
    width: auto !important;
    min-width: 7rem;
    max-width: 16rem;
}

.condition-value-cell {
    flex: 1 1 6rem !important;
    min-width: 5rem !important;
    max-width: 100%;
    width: auto !important;
}

.condition-value-cell .q-field {
    width: 100%;
}

.action-container {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(16, 185, 129, 0.05)) !important;
    border: 1px solid rgba(16, 185, 129, 0.2) !important;
}

/* Search input styling - theme aware */
.search-input .q-field__control::before {
    border-color: var(--color-border-default) !important;
}

.search-input .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.search-input .q-field__control {
    background: var(--color-bg-surface) !important;
    border-radius: 6px !important;
}

.search-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.search-input .q-field__label {
    color: var(--color-text-muted) !important;
}

.search-input .q-icon {
    color: var(--color-text-secondary) !important;
}

.search-input .q-field__control:hover {
    background: var(--color-hover-overlay) !important;
}

.connector-folder {
    border: 1px solid var(--color-border-accent);
    background: var(--color-bg-surface);
}

.connector-root-drop {
    border-color: var(--color-border-default);
    background: var(--color-bg-elevated);
}

.connector-folder-tile {
    cursor: default;
}

.connector-folder-open-zone {
    cursor: pointer;
    border-radius: 0.35rem;
    transition: background 0.15s ease;
    flex: 1;
    min-height: 3.5rem;
    max-height: 4.5rem;
    overflow: hidden;
}

.connector-folder-open-zone:hover {
    background: var(--color-hover-overlay);
}

.connector-folder-floating {
    box-shadow: 0 12px 40px var(--color-bg-overlay);
    background: var(--color-bg-surface);
    box-sizing: border-box;
}

.folder-float-handle {
    user-select: none;
    touch-action: none;
    -webkit-user-drag: none;
}

.connector-folder-preview-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(3.5rem, 1fr));
    gap: 0.35rem;
    width: 100%;
    overflow: hidden;
    max-height: 4.5rem;
}

.connector-folder-preview-tile {
    aspect-ratio: 1;
    min-height: 3.25rem;
    max-height: 4rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 0.25rem;
    border-radius: 0.35rem;
    border: 1px solid var(--color-border-accent);
    background: var(--color-bg-elevated);
    overflow: hidden;
}

.connector-folder-preview-tile-enabled {
    border-color: var(--color-success);
}

.connector-folder-preview-tile-disabled {
    border-color: var(--color-error);
}

.connector-folder-preview-tile-disabled .preview-name {
    color: var(--color-text-secondary);
    opacity: 0.75;
}

.connector-folder-preview-tile .preview-name {
    font-size: 0.65rem;
    line-height: 1.15;
    text-align: center;
    color: var(--color-text-primary);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
    width: 100%;
}
"""


def create_connectors_tab():
    """Create the Connectors tab UI"""
    global connectors_container, _folder_dialog_host

    # Add custom CSS to the page
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes("tab-surface w-full h-full flex flex-col p-4"):
        # Compact header section - single row layout
        with ui.column().classes("w-full gap-3 p-4 flex-none"):
            with ui.row().classes("w-full items-center gap-3"):
                global search_input
                with ui.element("div").classes("flex-1 min-w-48 slide-in"):
                    search_input = (
                        form_input(
                            tooltip="Filter connectors by name or description",
                            label="🔍 Search connectors",
                            placeholder="Search connectors...",
                            value="",
                            on_change=on_search_change,
                        )
                        .classes("bg-theme-base")
                        .props("clearable")
                    )

                with ui.row().classes("items-center gap-3 flex-nowrap shrink-0"):
                    primary_button(
                        "New Connector",
                        show_create_connector_dialog,
                        icon="add",
                        extra_classes="px-4 py-2 shrink-0",
                    )

                    outline_button(
                        "Refresh",
                        refresh_connectors,
                        icon="refresh",
                        extra_classes="px-3 py-2 shrink-0",
                    )

        # Main content area - flexible height
        with ui.element("div").classes("grow overflow-hidden relative"):
            with ui.scroll_area().classes("w-full h-full"):
                connectors_container = ui.element("div").classes("w-full p-4")

            _ensure_folder_dialog_host()

        # Load and display connectors
        load_connectors()

        # Initialize search visibility after loading
        update_search_visibility()


def _folder_header_drag_js(panel_dom_id: str) -> str:
    """Client-only drag for folder floaters; panel_dom_id is NiceGUI DOM id (e.g. c42)."""
    pid = json.dumps(panel_dom_id)
    return f"""(e) => {{
      if (e.button !== 0) return;
      if (e.target && e.target.closest && e.target.closest('button, .q-btn, [role="button"]')) return;
      const panel = document.getElementById({pid});
      if (!panel) return;
      const r = panel.getBoundingClientRect();
      const ox = e.clientX - r.left;
      const oy = e.clientY - r.top;
      panel.style.left = r.left + 'px';
      panel.style.top = r.top + 'px';
      panel.style.right = 'auto';
      panel.style.margin = '0';
      const move = (ev) => {{
        let nx = ev.clientX - ox;
        let ny = ev.clientY - oy;
        const w = panel.offsetWidth;
        const h = panel.offsetHeight;
        nx = Math.max(8, Math.min(nx, window.innerWidth - w - 8));
        ny = Math.max(8, Math.min(ny, window.innerHeight - h - 8));
        panel.style.left = nx + 'px';
        panel.style.top = ny + 'px';
        ev.preventDefault();
      }};
      const up = (ev) => {{
        window.removeEventListener('mousemove', move, true);
        window.removeEventListener('mouseup', up, true);
        if (ev) ev.preventDefault();
      }};
      window.addEventListener('mousemove', move, true);
      window.addEventListener('mouseup', up, true);
      e.preventDefault();
      e.stopPropagation();
    }}"""


def _ensure_folder_dialog_host() -> None:
    global _folder_dialog_host
    if _folder_dialog_host is not None and not _folder_dialog_host.is_deleted:
        return
    _folder_dialog_host = (
        ui.element("div")
        .classes("fixed top-0 left-0 w-0 h-0 overflow-visible")
        .style(f"z-index:{_FOLDER_FLOAT_Z_HOST}")
    )


def _detach_folder_member_cards(folder_id: str) -> None:
    layout = connector_layout_store.load_layout()
    spec = (layout.get("folders") or {}).get(folder_id) or {}
    for cid in spec.get("connector_ids") or []:
        connector_cards.pop(cid, None)
        connector_parent_folder.pop(cid, None)


def _close_folder_floater(folder_id: str) -> None:
    st = _folder_floaters.pop(folder_id, None)
    if not st:
        return
    _detach_folder_member_cards(folder_id)
    shell = st.get("shell")
    if shell is not None and not shell.is_deleted:
        try:
            shell.delete()
        except Exception:
            pass


def _close_all_folder_floaters() -> None:
    for fid in list(_folder_floaters.keys()):
        _close_folder_floater(fid)


def _open_folder_floating_window(folder_id: str) -> None:
    global _folder_floaters

    _ensure_folder_dialog_host()
    mgr = connector_manager.get_manager()
    connectors = mgr.get_all_connectors()
    existing_ids = set(connectors.keys())
    layout = connector_layout_store.reconcile_layout(
        connector_layout_store.load_layout(), existing_ids
    )
    spec = (layout.get("folders") or {}).get(folder_id)
    if not spec or not isinstance(spec, dict):
        notify("Folder not found", type="warning")
        return
    title = str(spec.get("name") or "Folder")
    member_ids = [cid for cid in spec.get("connector_ids") or [] if cid in connectors]
    fold_state = _folder_members_enabled_state(member_ids, connectors)

    existing = _folder_floaters.get(folder_id)
    if existing:
        sh = existing.get("shell")
        if sh is not None and not sh.is_deleted:
            notify("This folder is already open", type="info", timeout=1.5)
            return
        _folder_floaters.pop(folder_id, None)

    assert _folder_dialog_host is not None
    offset = len(_folder_floaters)
    z = _FOLDER_FLOAT_Z_SHELL_BASE + min(offset, 85)

    with _folder_dialog_host:
        shell = ui.element("div").classes(
            "connector-folder-floating pointer-events-auto rounded-lg flex flex-col "
            "border border-theme-subtle min-h-0"
        )
        shell.style(
            "position:fixed;"
            f"left:{min(40 + offset * 28, 280)}px;"
            f"top:{min(72 + offset * 24, 200)}px;"
            "width:min(92vw, 960px);"
            "min-width:320px;"
            "min-height:280px;"
            "max-width:96vw;"
            "max-height:90vh;"
            "resize:both;"
            "overflow:auto;"
            "box-sizing:border-box;"
            f"z-index:{z};"
        )
        with shell:
            head = ui.row().classes(
                "w-full items-center justify-between gap-2 flex-none "
                "border-b border-theme-default px-2 py-1 cursor-move"
            )
            head.on(
                "mousedown.capture",
                js_handler=_folder_header_drag_js(f"c{shell.id}"),
            )
            with head:
                drag_area = ui.element("div").classes(
                    "folder-float-handle flex flex-row items-center gap-2 grow min-w-0"
                )
                with drag_area:
                    ui.icon("folder", size="sm").classes("text-amber-400 shrink-0")
                    title_label = ui.label(title).classes(
                        "text-base font-semibold text-theme-primary truncate"
                    )
                with ui.row().classes("items-center gap-1 shrink-0"):
                    floater_enable_sw = ui.switch(
                        value=(fold_state == "all_on"),
                        on_change=lambda e, fid=folder_id: set_folder_connectors_enabled(
                            fid, bool(e.value)
                        ),
                    ).props("dense").classes("scale-90").tooltip(
                        "Enable or disable all connectors in this folder"
                    )
                    if fold_state == "mixed":
                        floater_enable_sw.props("indeterminate")
                    if not member_ids:
                        floater_enable_sw.disable()
                    ui.button(icon="close", on_click=lambda f=folder_id: _close_folder_floater(f)).props(
                        "flat dense round"
                    ).tooltip("Close")

            body_wrap = ui.element("div").classes("flex-1 min-h-0 p-3 flex flex-col")
            with body_wrap:
                with ui.scroll_area().classes("w-full flex-1").style(
                    "min-height: 0; flex: 1 1 auto;"
                ):
                    body_grid = ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 p-1"
                    )
                    body_grid.on("dragover.prevent", lambda _: None)
                    body_grid.on(
                        "drop.prevent", lambda _: _handle_drop_on_folder(folder_id)
                    )
                    with body_grid:
                        for cid in member_ids:
                            create_connector_card(cid, connectors[cid], folder_id)

    _folder_floaters[folder_id] = {
        "shell": shell,
        "body_grid": body_grid,
        "title_label": title_label,
        "is_open": True,
    }
    update_search_visibility()


def _update_folder_floater_title(folder_id: str, new_title: str) -> None:
    st = _folder_floaters.get(folder_id)
    if not st:
        return
    lbl = st.get("title_label")
    if lbl is not None and not lbl.is_deleted:
        lbl.text = new_title


def _drag_client_id() -> int:
    return context.client.id


def _set_drag_source(connector_id: str, from_folder_id: Optional[str]) -> None:
    _client_drag_state[_drag_client_id()] = {
        "connector_id": connector_id,
        "from_folder_id": from_folder_id,
    }


def _peek_drag_source() -> Optional[Dict[str, Optional[str]]]:
    return _client_drag_state.get(_drag_client_id())


def _pop_drag_source() -> Optional[Dict[str, Optional[str]]]:
    return _client_drag_state.pop(_drag_client_id(), None)


def _clear_drag_source() -> None:
    _client_drag_state.pop(_drag_client_id(), None)


def _connector_search_blob(connector: Connector) -> str:
    trig = (
        format_trigger_name(connector.trigger.trigger_type)
        if connector.trigger
        else ""
    )
    parts = [
        connector.name or "",
        connector.description or "",
        trig,
    ]
    parts.extend(
        get_action_display_name(action) for action in (connector.actions or [])
    )
    return " ".join(parts).lower()


def _handle_drop_on_card(target_id: str) -> None:
    st = _peek_drag_source()
    if not st:
        return
    src = st["connector_id"]
    if src == target_id:
        _pop_drag_source()
        return
    _pop_drag_source()
    mgr = connector_manager.get_manager()
    existing_ids = set(mgr.get_all_connectors().keys())
    layout = connector_layout_store.reconcile_layout(
        connector_layout_store.load_layout(), existing_ids
    )
    new_layout, new_fid = connector_layout_store.merge_into_new_folder(
        layout, src, target_id
    )
    if not new_fid:
        return
    connector_layout_store.save_layout(new_layout)
    load_connectors()
    update_search_visibility()
    _prompt_new_folder_name(new_fid)


def _handle_drop_on_folder(folder_id: str) -> None:
    st = _peek_drag_source()
    if not st:
        return
    src = st["connector_id"]
    if st.get("from_folder_id") == folder_id:
        _pop_drag_source()
        return
    _pop_drag_source()
    mgr = connector_manager.get_manager()
    existing_ids = set(mgr.get_all_connectors().keys())
    layout = connector_layout_store.reconcile_layout(
        connector_layout_store.load_layout(), existing_ids
    )
    new_layout = connector_layout_store.move_connector_to_folder(
        layout, src, folder_id
    )
    connector_layout_store.save_layout(new_layout)
    load_connectors()
    update_search_visibility()


def _handle_drop_on_root() -> None:
    st = _peek_drag_source()
    if not st:
        return
    if st.get("from_folder_id") is None:
        _pop_drag_source()
        return
    _pop_drag_source()
    src = st["connector_id"]
    mgr = connector_manager.get_manager()
    existing_ids = set(mgr.get_all_connectors().keys())
    layout = connector_layout_store.reconcile_layout(
        connector_layout_store.load_layout(), existing_ids
    )
    new_layout = connector_layout_store.move_connector_to_root(layout, src)
    connector_layout_store.save_layout(new_layout)
    load_connectors()
    update_search_visibility()


def _prompt_new_folder_name(folder_id: str) -> None:
    layout = connector_layout_store.load_layout()
    spec = (layout.get("folders") or {}).get(folder_id) or {}
    cur = str(spec.get("name") or "New folder")

    def save_name(name: str, dialog: ui.dialog) -> None:
        mgr = connector_manager.get_manager()
        existing_ids = set(mgr.get_all_connectors().keys())
        lay = connector_layout_store.reconcile_layout(
            connector_layout_store.load_layout(), existing_ids
        )
        new_lay = connector_layout_store.rename_folder(lay, folder_id, name)
        connector_layout_store.save_layout(new_lay)
        dialog.close()
        load_connectors()
        update_search_visibility()

    with ui.dialog() as dialog:
        with ui.card().classes("p-4 min-w-[20rem]"):
            ui.label("Name this folder").classes("text-lg font-semibold mb-2")
            inp = ui.input(value=cur).classes("w-full mb-3")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Save",
                    on_click=lambda: save_name((inp.value or "").strip() or "Folder", dialog),
                ).classes("btn-primary")
    dialog.open()


def show_rename_folder_dialog(folder_id: str) -> None:
    layout = connector_layout_store.load_layout()
    spec = (layout.get("folders") or {}).get(folder_id) or {}
    cur = str(spec.get("name") or "Folder")

    def save_name(name: str, dialog: ui.dialog) -> None:
        mgr = connector_manager.get_manager()
        existing_ids = set(mgr.get_all_connectors().keys())
        lay = connector_layout_store.reconcile_layout(
            connector_layout_store.load_layout(), existing_ids
        )
        new_lay = connector_layout_store.rename_folder(lay, folder_id, name)
        connector_layout_store.save_layout(new_lay)
        dialog.close()
        _update_folder_floater_title(folder_id, name)
        tl = folder_tile_title_labels.get(folder_id)
        if tl is not None and not tl.is_deleted:
            tl.text = name
        update_search_visibility()

    with ui.dialog() as dialog:
        with ui.card().classes("p-4 min-w-[20rem]"):
            ui.label("Rename folder").classes("text-lg font-semibold mb-2")
            inp = ui.input(value=cur).classes("w-full mb-3")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Save",
                    on_click=lambda: save_name((inp.value or "").strip() or "Folder", dialog),
                ).classes("btn-primary")
    dialog.open()


def show_delete_folder_dialog(folder_id: str) -> None:
    def keep_connectors(dialog: ui.dialog) -> None:
        _close_folder_floater(folder_id)
        mgr = connector_manager.get_manager()
        existing_ids = set(mgr.get_all_connectors().keys())
        lay = connector_layout_store.reconcile_layout(
            connector_layout_store.load_layout(), existing_ids
        )
        new_lay = connector_layout_store.delete_folder_keep_connectors(lay, folder_id)
        connector_layout_store.save_layout(new_lay)
        dialog.close()
        load_connectors()
        update_search_visibility()

    def delete_all(dialog: ui.dialog) -> None:
        _close_folder_floater(folder_id)
        mgr = connector_manager.get_manager()
        existing_ids = set(mgr.get_all_connectors().keys())
        lay = connector_layout_store.reconcile_layout(
            connector_layout_store.load_layout(), existing_ids
        )
        new_lay, members = connector_layout_store.delete_folder_record_only(
            lay, folder_id
        )
        connector_layout_store.save_layout(new_lay)
        dialog.close()
        for cid in members:
            mgr.remove_connector(cid)
        load_connectors()
        update_search_visibility()

    with ui.dialog().props("persistent") as dialog:
        with ui.card().classes("p-4 max-w-lg"):
            ui.label("Delete folder").classes("text-lg font-semibold mb-2")
            ui.label(
                "Remove the folder only (connectors return to the main grid), "
                "or delete the folder and all connectors inside it."
            ).classes("text-sm secondary-text mb-4")
            with ui.column().classes("w-full gap-2"):
                ui.button(
                    "Delete folder only — keep connectors",
                    on_click=lambda: keep_connectors(dialog),
                ).classes("control-button btn-secondary w-full")
                ui.button(
                    "Delete folder and all connectors inside",
                    on_click=lambda: delete_all(dialog),
                ).classes("control-button btn-danger w-full")
            with ui.row().classes("w-full justify-end mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()


def _folder_members_enabled_state(
    member_ids: List[str], connectors: Dict[str, Connector]
) -> str:
    """Return 'all_on', 'all_off', or 'mixed' for connectors in a folder."""
    if not member_ids:
        return "all_off"
    n = len(member_ids)
    enabled_n = sum(
        1 for cid in member_ids if connectors.get(cid) and connectors[cid].enabled
    )
    if enabled_n == 0:
        return "all_off"
    if enabled_n == n:
        return "all_on"
    return "mixed"


def set_folder_connectors_enabled(folder_id: str, enabled: bool) -> None:
    """Enable or disable every connector listed in the folder layout."""
    try:
        mgr = connector_manager.get_manager()
        connectors = mgr.get_all_connectors()
        existing_ids = set(connectors.keys())
        layout = connector_layout_store.reconcile_layout(
            connector_layout_store.load_layout(), existing_ids
        )
        member_ids = connector_layout_store.list_folder_connector_ids(layout, folder_id)
        spec = (layout.get("folders") or {}).get(folder_id) or {}
        title = str(spec.get("name") or "Folder")
        if not member_ids:
            notify("Folder is empty", type="warning", timeout=2000)
            return
        changed = mgr.set_connectors_enabled(member_ids, enabled)
        if changed:
            verb = "Enabled" if enabled else "Disabled"
            notify(f'{verb} {changed} connector(s) in "{title}"', type="positive")
        refresh_connectors()
    except Exception as e:
        logger.error(f"Error setting folder connectors enabled: {e}", exc_info=True)
        notify(f"Error updating folder connectors: {str(e)}", type="negative")


def load_connectors():
    """Load and display connectors"""
    global connectors_container, connector_cards, folder_cards, connector_parent_folder, folder_tile_title_labels

    if connectors_container is None:
        logger.error("Connectors container not initialized")
        return

    open_folder_ids = list(_folder_floaters.keys())
    _close_all_folder_floaters()
    connectors_container.clear()
    connector_cards.clear()
    folder_cards.clear()
    connector_parent_folder.clear()
    folder_tile_title_labels.clear()

    try:
        manager = connector_manager.get_manager()
        connectors = manager.get_all_connectors()
        existing_ids = set(connectors.keys())

        raw_layout = connector_layout_store.load_layout()
        layout = connector_layout_store.reconcile_layout(raw_layout, existing_ids)

        if json.dumps(layout, sort_keys=True, default=str) != json.dumps(
            raw_layout, sort_keys=True, default=str
        ):
            connector_layout_store.save_layout(layout)

        def render_folder(folder_id: str, folder_spec: dict) -> None:
            name = str(folder_spec.get("name") or "Folder")
            member_ids = [
                cid
                for cid in folder_spec.get("connector_ids") or []
                if cid in connectors
            ]
            count = len(member_ids)
            fold_state = _folder_members_enabled_state(member_ids, connectors)

            wrapper = ui.element("div").classes(
                "connector-folder connector-folder-tile connector-card p-4 rounded-lg fade-in "
                "flex flex-col gap-2 border border-theme-subtle bg-[var(--color-bg-surface)]"
            )
            wrapper.props(f'data-folder-id="{folder_id}"')
            folder_cards[folder_id] = wrapper
            wrapper.on("dragover.prevent", lambda _: None)
            wrapper.on("drop.prevent", lambda _: _handle_drop_on_folder(folder_id))

            with wrapper:
                with ui.row().classes(
                    "w-full items-center justify-between gap-2 flex-none"
                ):
                    with ui.row().classes("items-center gap-2 grow min-w-0"):
                        ui.icon("folder", size="28px").classes(
                            "text-amber-400 shrink-0"
                        )
                        title_lbl = ui.label(name).classes(
                            "text-base font-semibold text-theme-primary truncate"
                        )
                        folder_tile_title_labels[folder_id] = title_lbl
                        ui.label(
                            f"{count} connector{'s' if count != 1 else ''}"
                        ).classes(
                            "text-xs secondary-text whitespace-nowrap shrink-0"
                        )
                    with ui.row().classes("items-center gap-1 shrink-0"):
                        folder_enable_sw = ui.switch(
                            "Toggle all",
                            value=(fold_state == "all_on"),
                            on_change=lambda e, fid=folder_id: set_folder_connectors_enabled(
                                fid, bool(e.value)
                            ),
                        ).props("left-label dense").classes("scale-90")
                        if fold_state == "mixed":
                            folder_enable_sw.props("indeterminate")
                        if not member_ids:
                            folder_enable_sw.disable()
                        ui.button(
                            icon="edit",
                            on_click=lambda f=folder_id: show_rename_folder_dialog(f),
                        ).props("flat dense round").tooltip("Rename folder")
                        ui.button(
                            icon="delete",
                            on_click=lambda f=folder_id: show_delete_folder_dialog(f),
                        ).props("flat dense round").tooltip("Delete folder")

                open_zone = ui.element("div").classes(
                    "connector-folder-open-zone w-full grow min-w-0 p-1 -m-1"
                )
                open_zone.on(
                    "click",
                    lambda f=folder_id: _open_folder_floating_window(f),
                )
                open_zone.tooltip("Open folder")
                with open_zone:
                    if member_ids:
                        with ui.element("div").classes(
                            "connector-folder-preview-grid"
                        ):
                            for cid in member_ids:
                                c = connectors.get(cid)
                                if not c:
                                    continue
                                tile_cls = (
                                    "connector-folder-preview-tile "
                                    + (
                                        "connector-folder-preview-tile-enabled"
                                        if c.enabled
                                        else "connector-folder-preview-tile-disabled"
                                    )
                                )
                                icon_cls = "text-amber-300 shrink-0 mb-0.5"
                                if not c.enabled:
                                    icon_cls += " opacity-40"
                                with ui.element("div").classes(tile_cls):
                                    ui.icon("hub", size="18px").classes(icon_cls)
                                    nm = (c.name or "").strip() or "Untitled"
                                    ui.label(nm).classes("preview-name")

        with connectors_container:
            if not connectors:
                with ui.column().classes(
                    "w-full h-full flex flex-col items-center justify-center gap-4 p-8 empty-state"
                ):
                    ui.icon("link_off", size="4rem").classes("muted-text")
                    ui.label("No connectors created yet").classes(
                        "text-lg secondary-text fade-in"
                    )
                    ui.label(
                        "Create your first connector to automate your stream"
                    ).classes("text-sm muted-text fade-in")
                    ui.button(
                        icon="add",
                        text="Create First Connector",
                        on_click=show_create_connector_dialog,
                    ).classes(
                        "control-button btn-primary px-6 py-3 mt-4"
                    )
            else:
                with ui.element("div").classes("w-full flex flex-col gap-3"):
                    root_drop = ui.element("div").classes(
                        "connector-root-drop w-full min-h-[2.75rem] rounded-lg px-3 py-2 "
                        "flex items-center justify-center text-xs secondary-text"
                    )
                    root_drop.props('data-root-drop="1"')
                    root_drop.on("dragover.prevent", lambda _: None)
                    root_drop.on("drop.prevent", lambda _: _handle_drop_on_root())
                    with root_drop:
                        ui.label(
                            "Drop a connector here to move it to the main grid"
                        ).classes("text-center")

                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
                    ):
                        for item in layout.get("root_items") or []:
                            kind = item.get("kind")
                            iid = item.get("id")
                            if not kind or not iid:
                                continue
                            if kind == "connector":
                                if iid in connectors:
                                    create_connector_card(iid, connectors[iid], None)
                            elif kind == "folder":
                                spec = (layout.get("folders") or {}).get(iid)
                                if spec and isinstance(spec, dict):
                                    render_folder(iid, spec)

        folder_specs = layout.get("folders") or {}
        for fid in open_folder_ids:
            if fid in folder_specs:
                _open_folder_floating_window(fid)

    except Exception as e:
        logger.error(f"Error loading connectors: {e}", exc_info=True)
        with connectors_container:
            ui.label(f"Error loading connectors: {str(e)}").classes("text-red-400")


def create_connector_card(
    connector_id: str, connector: Connector, folder_id: Optional[str] = None
):
    """Create a card display for a connector"""
    global connector_parent_folder

    card_classes = "connector-card p-4 rounded-lg min-w-0"
    card_classes += (
        " connector-card-enabled"
        if connector.enabled
        else " connector-card-disabled"
    )

    # Create the card element and store reference for search functionality
    card_element = (
        ui.element("div")
        .classes(card_classes)
        .props(f'data-connector-id="{connector_id}"')
    )
    if folder_id:
        card_element.props(f'data-folder-parent="{folder_id}"')
    connector_cards[connector_id] = card_element
    connector_parent_folder[connector_id] = folder_id

    card_element.props("draggable")
    card_element.on(
        "dragstart",
        lambda _: _set_drag_source(connector_id, folder_id),
    )
    card_element.on("dragend", lambda _: _clear_drag_source())
    card_element.on("dragover.prevent", lambda _: None)
    card_element.on("drop.prevent", lambda _: _handle_drop_on_card(connector_id))

    with card_element:
        # Header row with name, controls, and actions
        with ui.row().classes("w-full items-center justify-between gap-2 mb-3"):
            with ui.column().classes("gap-1 grow min-w-0"):
                ui.label(connector.name).classes(
                    "text-base font-semibold text-theme-primary truncate"
                )
                if connector.description:
                    ui.label(connector.description).classes(
                        "text-xs secondary-text truncate"
                    )

            with ui.row().classes("items-center gap-1 shrink-0"):
                ui.switch(
                    value=connector.enabled,
                    on_change=lambda e, cid=connector_id: toggle_connector(
                        cid, e.value
                    ),
                ).props("dense").classes("scale-90").tooltip(
                    "Enable or disable this connector"
                )
                ui.button(
                    icon="edit",
                    on_click=lambda cid=connector_id: show_edit_connector_dialog(cid),
                ).props("flat dense round").tooltip("Edit connector")
                ui.button(
                    icon="play_arrow",
                    on_click=lambda cid=connector_id: test_connector(cid),
                ).props("flat dense round").tooltip("Test connector")
                ui.button(
                    icon="delete",
                    on_click=lambda cid=connector_id: delete_connector(cid),
                ).props("flat dense round").tooltip("Delete connector")

        # Trigger → Condition → Actions flow (vertical)
        if connector.trigger:
            conditions = connector.trigger.conditions or []
            has_actions = bool(connector.actions)
            has_filter = bool(conditions)

            with ui.column().classes("connector-flow gap-0 mb-3"):
                with ui.row().classes("items-center gap-2 flex-wrap w-full"):
                    ui.icon("flash_on", size="16px").classes("text-blue-400 shrink-0")
                    ui.label("Trigger").classes("connector-flow-label shrink-0")
                    ui.label(
                        format_trigger_name(connector.trigger.trigger_type)
                    ).classes("trigger-badge")

                if has_filter or has_actions:
                    ui.icon("arrow_downward", size="18px").classes("connector-flow-arrow")

                if has_filter:
                    with ui.row().classes("items-center gap-2 flex-wrap w-full"):
                        ui.icon("filter_list", size="16px").classes(
                            "text-theme-primary shrink-0"
                        )
                        ui.label("Condition").classes(
                            "connector-flow-label shrink-0"
                        )
                        for condition in conditions:
                            chip_text = _format_condition_for_card(
                                condition, connector.trigger.trigger_type
                            )
                            ui.label(chip_text).classes("condition-badge")

                    if has_actions:
                        ui.icon("arrow_downward", size="18px").classes(
                            "connector-flow-arrow"
                        )

                if has_actions:
                    with ui.row().classes("items-center gap-2 flex-wrap w-full"):
                        ui.icon("play_arrow", size="16px").classes(
                            "text-green-400 shrink-0"
                        )
                        ui.label("Actions").classes("connector-flow-label shrink-0")
                        for action in connector.actions:
                            action_display = get_action_display_name(action)
                            ui.label(action_display).classes("action-badge")

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-theme-subtle"
        ):
            ui.label(f"Triggered: {connector.trigger_count}x")
            if connector.last_triggered > 0:
                import datetime

                last_triggered = datetime.datetime.fromtimestamp(
                    connector.last_triggered
                )
                ui.label(f"Last: {last_triggered.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Never triggered")


def format_trigger_name(trigger_type: TriggerType) -> str:
    """Format trigger type for display"""
    name_mapping = {
        TriggerType.TWITCH_BITS: "Twitch Bits",
        TriggerType.TWITCH_SUB: "Twitch Sub",
        TriggerType.TWITCH_RESUB: "Twitch Resub",
        TriggerType.TWITCH_GIFTSUB: "Gift Sub",
        TriggerType.TWITCH_FOLLOW: "Twitch Follow",
        TriggerType.TWITCH_RAID: "Twitch Raid",
        TriggerType.TWITCH_POINTS: "Channel Points",
        TriggerType.TWITCH_CHAT_MESSAGE: "Chat Message",
        TriggerType.TWITCH_HYPE_TRAIN_START: "Hype Train Start",
        TriggerType.TWITCH_HYPE_TRAIN_END: "Hype Train End",
        TriggerType.TWITCH_STREAM_ONLINE: "Twitch Stream Online",
        TriggerType.TWITCH_STREAM_OFFLINE: "Twitch Stream Offline",
        TriggerType.YOUTUBE_CHAT_MESSAGE: "YouTube Chat Message",
        TriggerType.YOUTUBE_MEMBER: "YouTube Membership",
        TriggerType.YOUTUBE_MEMBER_MILESTONE: "YouTube Member Milestone",
        TriggerType.YOUTUBE_GIFT_MEMBERSHIP: "YouTube Gift Membership",
        TriggerType.YOUTUBE_SUPERCHAT: "YouTube Super Chat",
        TriggerType.YOUTUBE_SUPERSTICKER: "YouTube Super Sticker",
        TriggerType.DONATION: "Donation",
        TriggerType.TIMER: "Timer",
        TriggerType.SCHEDULE: "Schedule",
        TriggerType.HOTKEY: "Hotkey",
        TriggerType.STREAMDECK: "Stream Deck",
        TriggerType.WEBHOOK: "Webhook",
        TriggerType.OBS_SCENE_CHANGED: "OBS Scene",
        TriggerType.OBS_STREAM_STATE: "OBS Stream",
        TriggerType.OBS_RECORD_STATE: "OBS Record",
        TriggerType.OBS_INPUT_MUTE: "OBS Input Mute",
        TriggerType.ANY: "Any (all events)",
    }
    return name_mapping.get(trigger_type, trigger_type.value.replace("_", " ").title())


def get_action_display_name(action) -> str:
    """Get a descriptive display name for an action"""
    try:
        action_type = action.action_type.value

        # Return more descriptive names based on action type and configuration
        if action_type == "template_control":
            template_name = getattr(action, "template_name", "Unknown")
            control_action = getattr(action, "control_action", "Unknown")
            return (
                f"{template_name.title()} → {control_action.replace('_', ' ').title()}"
            )
        elif action_type == "websocket_emit":
            event_name = getattr(action, "event_name", "Custom Event")
            return f"WebSocket → {event_name}"
        elif action_type == "send_chat_message":
            targets = getattr(action, "reply_targets", None) or ["twitch"]
            if set(targets) >= {"twitch", "youtube"}:
                return "Send Chat Message → All"
            if targets == ["youtube"]:
                return "Send Chat Message → YouTube"
            return "Send Chat Message → Twitch"
        elif action_type == "send_announcement":
            color = getattr(action, "color", "primary") or "primary"
            return f"Send Announcement ({color})"
        elif action_type == "send_discord_message":
            channels = getattr(action, "channels", None) or []
            n = len(channels) if isinstance(channels, list) else 0
            if n == 1 and isinstance(channels[0], dict):
                name = channels[0].get("channel_name") or channels[0].get("channel_id")
                return f"Send Discord → #{name}"
            return f"Send Discord Message ({n} channels)"
        elif action_type == "trigger_alert":
            alert_type = getattr(action, "alert_type", "Alert")
            amount = getattr(action, "amount", "")
            if amount:
                return f"Trigger {alert_type.title()} ({amount})"
            return f"Trigger {alert_type.title()}"
        elif action_type == "api_call":
            method = getattr(action, "method", "GET")
            url = getattr(action, "url", "API")
            # Extract domain from URL for display
            if url.startswith("http"):
                try:
                    from urllib.parse import urlparse

                    domain = urlparse(url).netloc
                    return f"{method} → {domain}"
                except Exception:
                    return f"{method} → API Call"
            return f"{method} → API Call"
        elif action_type == "write_file":
            file_path = getattr(action, "file_path", "file")
            # Extract just the filename if it's a path
            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
            return f"Write to {file_name}"
        elif action_type == "execute_command":
            command = getattr(action, "command", "Command")
            # Show first part of command for display
            if len(command) > 20:
                return f"Execute: {command[:20]}..."
            return f"Execute: {command}"
        elif action_type == "key_press":
            input_type = getattr(action, "input_type", "key")
            key_sequence = getattr(action, "key_sequence", "")
            action_mode = getattr(action, "action_mode", "press")
            if input_type == "macro":
                return "Execute Macro"
            elif input_type == "mouse":
                return f"Mouse {action_mode}: {key_sequence}"
            else:
                return f"Key {action_mode}: {key_sequence}"
        elif action_type == "audio_control":
            control_type = getattr(action, "control_type", "system_volume")
            action_mode = getattr(action, "action_mode", "set")
            if control_type == "microphone":
                return f"Microphone: {action_mode}"
            elif control_type == "application_volume":
                app_name = getattr(action, "target_application", "App")
                return f"{app_name} Volume: {action_mode}"
            else:
                return f"System Volume: {action_mode}"
        elif action_type == "game_hook":
            gid = getattr(action, "game_id", "ff7")
            op = getattr(action, "operation", "")
            return f"Game {gid}: {op.replace('_', ' ')}"
        elif action_type == "obs_control":
            op = getattr(action, "operation", "")
            return f"OBS: {op.replace('_', ' ')}"
        else:
            return action_type.replace("_", " ").title()

    except Exception:
        return getattr(action, "name", "Unknown Action")


def show_create_connector_dialog():
    """Show the create connector dialog"""
    show_connector_dialog()


def show_connector_dialog(connector_id: str = None):
    """Show the create/edit connector dialog"""
    global create_dialog

    if create_dialog:
        create_dialog.close()  # Close existing dialog first
        create_dialog = None

    # Create the dialog with 75% window size
    create_dialog = ui.dialog().props("persistent maximized")

    with create_dialog:
        with ui.card().classes("w-[75vw] h-[75vh] overflow-hidden"):
            with ui.column().classes("w-full h-full"):
                # Dialog header
                is_edit = connector_id is not None
                title = "Edit Connector" if is_edit else "Create New Connector"

                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-theme-subtle"
                ):
                    ui.label(title).classes("text-xl font-semibold text-theme-primary")
                    ui.button(icon="close", on_click=create_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.scroll_area().classes("grow p-4"):
                    create_connector_form(connector_id)

    create_dialog.open()


def create_connector_form(connector_id: str = None):
    """Create the connector creation/edit form"""
    # Load existing connector data if editing
    existing_connector = None
    if connector_id:
        try:
            manager = connector_manager.get_manager()
            existing_connector = manager.get_connector(connector_id)
        except Exception as e:
            logger.error(f"Error loading connector for edit: {e}")
            notify("Error loading connector data", type="negative")
            return

    # Form state - populate with existing data if editing
    trigger_type_value = None
    if existing_connector and existing_connector.trigger:
        try:
            trigger_type_value = existing_connector.trigger.trigger_type.value
        except Exception as e:
            logger.error(f"Error accessing trigger type: {e}", exc_info=True)

    form_data = {
        "connector_id": connector_id,
        "name": existing_connector.name if existing_connector else "",
        "description": existing_connector.description if existing_connector else "",
        "trigger_type": trigger_type_value,
        "trigger_conditions": [],
        "trigger_config": {
            "key_combination": "",
            "is_global": True,
        },
        "actions": [],
    }

    # Populate existing trigger conditions
    if (
        existing_connector
        and existing_connector.trigger
        and existing_connector.trigger.conditions
    ):
        for condition in existing_connector.trigger.conditions:
            try:
                form_data["trigger_conditions"].append(
                    {
                        "field": condition.field,
                        "operator": condition.operator.value,
                        "value": condition.value,
                    }
                )
            except Exception as e:
                logger.error(
                    f"Error processing condition during edit: {e}", exc_info=True
                )
                # Continue with other conditions rather than failing completely

    # Populate existing trigger config for hotkey triggers
    if (
        existing_connector
        and existing_connector.trigger
        and existing_connector.trigger.trigger_type.value == "hotkey"
    ):
        form_data["trigger_config"] = {
            "key_combination": getattr(
                existing_connector.trigger, "key_combination", ""
            ),
            "is_global": getattr(existing_connector.trigger, "is_global", True),
        }

    if (
        existing_connector
        and existing_connector.trigger
        and existing_connector.trigger.trigger_type.value == "streamdeck"
    ):
        form_data["trigger_config"] = {
            "connector_id": getattr(
                existing_connector.trigger,
                "connector_id",
                existing_connector.connector_id,
            ),
        }

    # Populate existing actions
    if existing_connector and existing_connector.actions:
        for action in existing_connector.actions:
            try:
                action_data = {
                    "type": action.action_type.value,
                    "config": {},
                    "delay_seconds": float(
                        getattr(action, "delay_seconds", 0) or 0
                    ),
                }

                # Extract action configuration based on type
                if hasattr(action, "template_name"):
                    action_data["config"]["template_name"] = action.template_name
                if hasattr(action, "control_action"):
                    action_data["config"]["control_action"] = action.control_action

                # For template control actions, extract control_data back to individual fields
                if hasattr(action, "control_data") and action.control_data:
                    action_data["config"].update(action.control_data)

                if hasattr(action, "event_name"):
                    action_data["config"]["event_name"] = action.event_name
                if hasattr(action, "event_data"):
                    action_data["config"]["event_data"] = action.event_data
                if hasattr(action, "message"):
                    action_data["config"]["message"] = action.message
                if hasattr(action, "reply_targets"):
                    action_data["config"]["reply_targets"] = list(
                        getattr(action, "reply_targets", None) or ["twitch"]
                    )
                at = getattr(action, "action_type", None)
                at_val = getattr(at, "value", at)
                if hasattr(action, "channels") and at_val == "send_discord_message":
                    action_data["config"]["channels"] = list(
                        getattr(action, "channels", None) or []
                    )
                if hasattr(action, "color"):
                    action_data["config"]["color"] = action.color
                if hasattr(action, "file_path"):
                    action_data["config"]["file_path"] = action.file_path
                if hasattr(action, "content"):
                    action_data["config"]["content"] = action.content
                if hasattr(action, "append"):
                    action_data["config"]["append"] = action.append

                # Extract greeting action specific attributes
                if hasattr(action, "user_id"):
                    action_data["config"]["user_id"] = action.user_id
                if hasattr(action, "username"):
                    action_data["config"]["username"] = action.username
                if hasattr(action, "greeting_text"):
                    action_data["config"]["greeting_text"] = action.greeting_text
                if hasattr(action, "greeting_id"):
                    action_data["config"]["greeting_id"] = action.greeting_id
                if hasattr(action, "enabled"):
                    action_data["config"]["enabled"] = action.enabled
                if hasattr(action, "force_send"):
                    action_data["config"]["force_send"] = action.force_send

                if getattr(action, "action_type", None) == ActionType.GAME_HOOK:
                    action_data["config"]["game_id"] = getattr(
                        action, "game_id", "ff7"
                    )
                    action_data["config"]["operation"] = getattr(
                        action, "operation", ""
                    )
                    ha = getattr(action, "hook_arguments", None) or {}
                    for hk, hv in ha.items():
                        action_data["config"][f"arg_{hk}"] = str(hv)

                if getattr(action, "action_type", None) == ActionType.OBS_CONTROL:
                    action_data["config"]["operation"] = getattr(
                        action, "operation", ""
                    )
                    ha = getattr(action, "obs_arguments", None) or {}
                    for hk, hv in ha.items():
                        action_data["config"][f"arg_{hk}"] = str(hv)

                form_data["actions"].append(action_data)
            except Exception as e:
                logger.error(f"Error processing action during edit: {e}", exc_info=True)
                # Continue with other actions rather than failing completely

    # Main layout - use grid for better horizontal space usage
    with ui.element("div").classes("w-full"):
        # Top row - Basic information
        with ui.element("div").classes("form-section mb-4"):
            ui.label("Basic Information").classes("form-section-title")

            with ui.grid(columns=2).classes("gap-4 w-full"):
                form_input(
        tooltip="Connector Name",
                    label="Connector Name",
                    placeholder="e.g., High Bits Counter",
                    value=form_data["name"],
                    on_change=lambda e: form_data.update({"name": e.value}),
                ).classes("w-full")

                form_input(
        tooltip="Description (optional)",
                    label="Description (optional)",
                    placeholder="What does this connector do?",
                    value=form_data["description"],
                    on_change=lambda e: form_data.update({"description": e.value}),
                ).classes("w-full")

        # Main content - side by side layout
        with ui.grid(columns=2).classes("gap-6 w-full"):
            # Left column - Trigger configuration
            with ui.element("div").classes("w-full"):
                with ui.element("div").classes("form-section"):
                    ui.label("Trigger Configuration").classes("form-section-title")

                    trigger_options = {
                        "twitch_bits": "Twitch Bits",
                        "twitch_sub": "Twitch Subscription",
                        "twitch_resub": "Twitch Resubscription",
                        "twitch_giftsub": "Twitch Gift Sub",
                        "twitch_follow": "Twitch Follow",
                        "twitch_raid": "Twitch Raid",
                        "twitch_points": "Channel Points",
                        "twitch_chat_message": "Twitch Chat Message",
                        "twitch_stream_online": "Twitch Stream Online",
                        "twitch_stream_offline": "Twitch Stream Offline",
                        "youtube_chat_message": "YouTube Chat Message",
                        "youtube_member": "YouTube Membership",
                        "youtube_member_milestone": "YouTube Member Milestone",
                        "youtube_gift_membership": "YouTube Gift Membership",
                        "youtube_superchat": "YouTube Super Chat",
                        "youtube_supersticker": "YouTube Super Sticker",
                        "any": "Any (all events)",
                        "donation": "Donation",
                        "hotkey": "Hotkey Trigger",
                        "streamdeck": "Stream Deck",
                        "obs_scene_changed": "OBS — Program scene changed",
                        "obs_stream_state": "OBS — Stream status",
                        "obs_record_state": "OBS — Recording status",
                        "obs_input_mute": "OBS — Input mute changed",
                    }

                    form_select(
        tooltip="Trigger Type",
                        options=trigger_options,
                        label="Trigger Type",
                        value=form_data["trigger_type"],
                        on_change=lambda e: handle_trigger_type_change(
                            e.value,
                            form_data,
                            trigger_config_container,
                            conditions_container,
                        ),
                    ).classes("w-full mb-4 trigger-select")

                    # Trigger config container (for hotkey, timer, etc.)
                    trigger_config_container = ui.element("div").classes("w-full mb-4")

                    # Conditions container
                    conditions_container = ui.element("div").classes("w-full")

            # Right column - Actions configuration
            with ui.element("div").classes("w-full"):
                with ui.element("div").classes("form-section"):
                    ui.label("Actions Configuration").classes("form-section-title")

                    actions_container = ui.element("div").classes("w-full")

                    with ui.row().classes("w-full items-center gap-2 mt-4"):
                        ui.button(
                            icon="add",
                            text="Add Action",
                            on_click=lambda: add_action_to_form(
                                form_data, actions_container
                            ),
                        ).classes(
                            "control-button btn-success px-4 py-2"
                        )

        # Form buttons
        with ui.row().classes(
            "w-full items-center justify-end gap-2 mt-6 pt-4 border-t border-theme-subtle"
        ):
            ui.button(text="Cancel", on_click=create_dialog.close).props(
                "flat"
            ).classes("secondary-text")

            is_edit = connector_id is not None
            button_text = "Update Connector" if is_edit else "Create Connector"
            ui.button(
                icon="save",
                text=button_text,
                on_click=lambda: save_connector(form_data),
            ).classes(
                "control-button btn-primary px-6 py-2"
            )

        # Initialize existing data if editing
        if existing_connector:
            # Initialize trigger configuration if trigger type is already selected
            if form_data.get("trigger_type"):
                handle_trigger_type_change(
                    form_data["trigger_type"],
                    form_data,
                    trigger_config_container,
                    conditions_container,
                )

            # Render existing conditions into the conditions list (not the outer container)
            if form_data.get("trigger_conditions"):
                _rebuild_all_conditions(form_data)

            # Populate existing actions
            for i, action_data in enumerate(form_data["actions"]):
                add_action_to_form_with_data_and_index(
                    form_data, actions_container, action_data, i
                )


def handle_trigger_type_change(
    trigger_type: str, form_data: dict, trigger_config_container, conditions_container
):
    """Handle trigger type change and show relevant configuration options"""
    if not trigger_type:
        return

    form_data["trigger_type"] = trigger_type
    trigger_config_container.clear()
    conditions_container.clear()

    # Show trigger-specific configuration
    if trigger_type == "hotkey":
        with trigger_config_container:
            ui.label("Hotkey Configuration").classes(
                "text-sm font-medium secondary-text mb-2"
            )

            # Key combination input
            form_input(
        tooltip="Key Combination",
                label="Key Combination",
                placeholder="e.g., ctrl+shift+f, f12, alt+tab",
                value=form_data["trigger_config"]["key_combination"],
                on_change=lambda e: form_data["trigger_config"].update(
                    {"key_combination": e.value}
                ),
            ).classes("w-full mb-3").props(
                'hint="Use + to combine keys (ctrl+shift+f)"'
            )

            # Global hotkey checkbox
            ui.checkbox(
                text="Global hotkey (works when app is not focused)",
                value=form_data["trigger_config"]["is_global"],
                on_change=lambda e: form_data["trigger_config"].update(
                    {"is_global": e.value}
                ),
            ).classes("mb-3")

    elif trigger_type == "streamdeck":
        with trigger_config_container:
            ui.label("Stream Deck Configuration").classes(
                "text-sm font-medium secondary-text mb-2"
            )
            ui.label(
                "Assign this connector on a Stream Deck button using the "
                "Connector action in the Mycelian Stream Deck plugin."
            ).classes("text-xs muted-text mb-3")

    with conditions_container:
        ui.label("Conditions (optional)").classes(
            "text-base font-medium secondary-text mb-2"
        )
        ui.label("Add conditions to make the trigger more specific").classes(
            "text-xs muted-text mb-3"
        )
        _obs_hint = {
            TriggerType.OBS_SCENE_CHANGED.value: (
                "Scene changed events include scene_name and previous_scene_name. "
                'Example: only after switching into a scene named BRB — '
                "Field scene_name Equals BRB."
            ),
            TriggerType.OBS_STREAM_STATE.value: (
                "OBS sends output_active (streaming output running) and output_state strings such as "
                "OBS_WEBSOCKET_OUTPUT_STATE_STARTED / STOPPED / PAUSED (plus STARTING / STOPPING while "
                "transitioning)."
            ),
            TriggerType.OBS_RECORD_STATE.value: (
                'Only fire while recordings are actively running — Field output_active Equals true '
                "(you can type true or false). For a precise state match instead, Field "
                "output_state Equals OBS_WEBSOCKET_OUTPUT_STATE_STARTED (recording underway) "
                "or OBS_WEBSOCKET_OUTPUT_STATE_STOPPED (not recording)."
            ),
            TriggerType.OBS_INPUT_MUTE.value: (
                "Fires when an audio source mute toggles. Fields: input_name, input_muted. "
                "Example muted: Field input_muted Equals true."
            ),
        }
        if trigger_type in _obs_hint:
            ui.label(_obs_hint[trigger_type]).classes("text-xs muted-text mb-3")

        if trigger_type == TriggerType.ANY.value:
            ui.label(
                "Warning: This trigger fires on every connector event type (bits, chat, "
                "subs, OBS, hotkeys, and more). Use specific conditions to avoid unwanted "
                "activations."
            ).classes("text-xs text-amber-400 mb-3")

        conditions_list = ui.element("div").classes("w-full space-y-2")
        form_data["_conditions_list"] = conditions_list

        ui.button(
            icon="add",
            text="Add Condition",
            on_click=lambda: add_condition_to_trigger(
                trigger_type, form_data, conditions_list
            ),
        ).classes(
            "control-button btn-secondary text-sm px-3 py-1 mt-2"
        )

    _call_game_hook_hint_refresh(form_data)


# (operator_key, symbol, tooltip label)
_CONDITION_OPERATOR_META: Dict[str, tuple] = {
    "equal": ("=", "Equals"),
    "not_equal": ("≠", "Does not equal"),
    "greater_than_or_equal": ("≥", "Greater than or equal"),
    "less_than_or_equal": ("≤", "Less than or equal"),
    "greater_than": (">", "Greater than"),
    "less_than": ("<", "Less than"),
    "contains": ("∋", "Contains"),
    "starts_with": ("^", "First word"),
    "begins_with": ("^…", "Begins with"),
    "ends_with": ("$", "Ends with"),
}
_BOOL_CONDITION_OPERATOR_KEYS = ("equal", "not_equal")
_BOOL_CONDITION_VALUE_OPTIONS: Dict[str, str] = {"true": "True", "false": "False"}


def _format_condition_for_card(condition, trigger_type: TriggerType) -> str:
    """Human-readable condition text for connector card chips."""
    field_labels = get_available_fields_for_trigger(trigger_type.value)
    field_label = field_labels.get(condition.field, condition.field or "")
    op_key = (
        condition.operator.value
        if hasattr(condition.operator, "value")
        else str(condition.operator)
    )
    meta = _CONDITION_OPERATOR_META.get(op_key)
    op_symbol = meta[0] if meta else op_key.replace("_", " ")
    raw_value = condition.value
    if isinstance(raw_value, bool):
        value_text = "True" if raw_value else "False"
    else:
        s = str(raw_value).strip().lower()
        if s in ("true", "1", "yes", "on"):
            value_text = "True"
        elif s in ("false", "0", "no", "off"):
            value_text = "False"
        else:
            value_text = str(raw_value)
    return f"{field_label} {op_symbol} {value_text}"


def _is_boolean_condition_field(field: Optional[str]) -> bool:
    return field == "is_moderator"


def _condition_operator_keys_for_field(field: Optional[str]) -> tuple:
    if _is_boolean_condition_field(field):
        return _BOOL_CONDITION_OPERATOR_KEYS
    return tuple(_CONDITION_OPERATOR_META.keys())


def _condition_operator_tip(operator_key: Optional[str]) -> str:
    if not operator_key:
        return "Comparison operator"
    meta = _CONDITION_OPERATOR_META.get(str(operator_key))
    return meta[1] if meta else str(operator_key)


def _condition_operator_options_for_field(field: Optional[str]) -> Dict[str, str]:
    """Map operator keys to 'symbol|tooltip' labels for Quasar select slots."""
    keys = _condition_operator_keys_for_field(field)
    return {
        k: f"{_CONDITION_OPERATOR_META[k][0]}|{_CONDITION_OPERATOR_META[k][1]}"
        for k in keys
        if k in _CONDITION_OPERATOR_META
    }


def _condition_operator_select(
    *,
    field: Optional[str],
    value: Any,
    classes: str,
    on_change: Callable,
) -> Any:
    """Operator select: symbols in UI; full names in tooltips (control + dropdown)."""
    options = _condition_operator_options_for_field(field)
    if value and value not in options:
        sym, tip = _CONDITION_OPERATOR_META.get(str(value), (str(value), str(value)))
        options[str(value)] = f"{sym}|{tip}"

    def _sync_tip(e) -> None:
        on_change(e)
        select_el.tooltip(_condition_operator_tip(e.value)).classes("bg-theme-surface")

    select_el = ui.select(
        options=options,
        label="Operator",
        value=value,
        on_change=_sync_tip,
    )
    select_el.classes(classes).props("outlined dense")
    select_el.props(
        ":option-label=\"(opt) => String(opt.label || '').split('|')[0]\""
    )
    select_el.tooltip(_condition_operator_tip(value)).classes("bg-theme-surface")
    select_el.add_slot(
        "option",
        r"""
        <q-item v-bind="props.itemProps">
            <q-item-section>
                <span>{{ String(props.opt.label || '').split('|')[0] }}</span>
                <q-tooltip>{{ String(props.opt.label || '').split('|')[1] }}</q-tooltip>
            </q-item-section>
        </q-item>
        """,
    )
    select_el.add_slot(
        "selected-item",
        r"""
        <span class="q-ml-xs">{{ String(props.opt.label || '').split('|')[0] }}</span>
        """,
    )
    return select_el


def _condition_value_for_display(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return "true"
    if s in ("false", "0", "no", "off"):
        return "false"
    return value


def _rebuild_all_conditions(form_data: dict) -> None:
    if form_data.get("_rebuilding_conditions"):
        return
    conditions_list = form_data.get("_conditions_list")
    trigger_type = form_data.get("trigger_type")
    if conditions_list is None or not trigger_type:
        return
    snapshot = [
        {
            "field": c.get("field"),
            "operator": c.get("operator"),
            "value": c.get("value", ""),
        }
        for c in form_data.get("trigger_conditions", [])
    ]
    form_data["_rebuilding_conditions"] = True
    try:
        conditions_list.clear()
        form_data["trigger_conditions"] = snapshot
        for i, condition_data in enumerate(snapshot):
            add_condition_to_trigger_with_data_and_index(
                trigger_type, form_data, conditions_list, condition_data, i
            )
    finally:
        form_data["_rebuilding_conditions"] = False
    _call_game_hook_hint_refresh(form_data)


def _select_display_value(value: Any) -> Any:
    """NiceGUI select rejects empty string; use None for unset."""
    if value == "":
        return None
    return value


def _normalize_form_select_values(form_data: dict) -> None:
    """Coerce empty select values in form_data for rebuild and save."""
    for action in form_data.get("actions") or []:
        if action.get("type") == "":
            action["type"] = None
    for cond in form_data.get("trigger_conditions") or []:
        for key in ("field", "operator"):
            if cond.get(key) == "":
                cond[key] = None


def add_condition_to_trigger(trigger_type: str, form_data: dict, conditions_list):
    """Add a condition input to the trigger"""
    add_condition_to_trigger_with_data(trigger_type, form_data, conditions_list, None)


def add_condition_to_trigger_with_data(
    trigger_type: str, form_data: dict, conditions_list, condition_data: dict = None
):
    """Add a condition input to the trigger with optional existing data"""
    # For new conditions, calculate the next index
    condition_index = len(form_data.get("trigger_conditions", []))
    add_condition_to_trigger_with_data_and_index(
        trigger_type, form_data, conditions_list, condition_data, condition_index
    )


def add_condition_to_trigger_with_data_and_index(
    trigger_type: str,
    form_data: dict,
    conditions_list,
    condition_data: dict = None,
    condition_index: int = None,
):
    """Add a condition input to the trigger with optional existing data and explicit index"""
    # Get available fields for this trigger type
    available_fields = get_available_fields_for_trigger(trigger_type)

    # Use existing data or defaults
    initial_field = _select_display_value(
        condition_data.get("field") if condition_data else None
    )
    initial_operator = _select_display_value(
        condition_data.get("operator") if condition_data else None
    )
    initial_value = condition_data.get("value", "") if condition_data else ""
    initial_value = _condition_value_for_display(initial_value)
    is_bool_field = _is_boolean_condition_field(initial_field)
    if not initial_operator:
        initial_operator = "equal"
    if is_bool_field and initial_value is None:
        initial_value = "true"

    # If this is a new condition (no condition_data), add it to form_data
    if condition_data is None and not form_data.get("_rebuilding_conditions"):
        if "trigger_conditions" not in form_data:
            form_data["trigger_conditions"] = []
        if condition_index is None:
            condition_index = len(form_data["trigger_conditions"])
        if condition_index >= len(form_data["trigger_conditions"]):
            form_data["trigger_conditions"].append(
                {
                    "field": None,
                    "operator": "equal",
                    "value": initial_value,
                }
            )

    # If condition_index is still None, use the current position in the conditions list
    if condition_index is None:
        condition_index = len(form_data.get("trigger_conditions", [])) - 1

    with conditions_list:
        with ui.element("div").classes(
            "w-full p-3 condition-container rounded mb-2"
        ):
            with ui.row().classes("w-full items-center justify-between mb-2"):
                ui.label(f"Condition #{condition_index + 1}").classes(
                    "text-base font-medium"
                )
                ui.button(
                    icon="delete",
                    on_click=lambda idx=condition_index: remove_condition(
                        idx, form_data, conditions_list
                    ),
                ).props("flat round").classes("text-red-400")

            with ui.row().classes(
                "w-full items-end gap-2 condition-fields-row"
            ):
                form_select(
                    tooltip="Field to compare on this event",
                    options=available_fields,
                    label="Field",
                    value=initial_field,
                    on_change=lambda e, idx=condition_index: update_condition_field(
                        idx, e.value, form_data
                    ),
                ).classes("condition-select condition-field-cell")

                _condition_operator_select(
                    field=initial_field,
                    value=initial_operator,
                    classes="condition-select condition-operator-cell",
                    on_change=lambda e, idx=condition_index: update_condition_operator(
                        idx, e.value, form_data
                    ),
                )

                if is_bool_field:
                    form_select(
                        tooltip="Expected true or false",
                        options=_BOOL_CONDITION_VALUE_OPTIONS,
                        label="Value",
                        value=initial_value
                        if initial_value in _BOOL_CONDITION_VALUE_OPTIONS
                        else "true",
                        on_change=lambda e, idx=condition_index: update_condition_value(
                            idx, e.value, form_data
                        ),
                    ).classes("condition-select condition-value-cell")
                else:
                    form_input(
                        tooltip="Value to compare against",
                        label="Value",
                        value=initial_value if initial_value is not None else "",
                        on_change=lambda e, idx=condition_index: update_condition_value(
                            idx, e.value, form_data
                        ),
                    ).classes("condition-input condition-value-cell")

    _call_game_hook_hint_refresh(form_data)


def _trigger_field_mappings() -> Dict[str, Dict[str, str]]:
    """Per-trigger condition fields (internal; use get_available_fields_for_trigger)."""
    return {
        "twitch_bits": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
        },
        "twitch_sub": {
            "tier": "Tier",
            "username": "Username",
            "message": "Message",
        },
        "twitch_resub": {
            "tier": "Tier",
            "username": "Username",
            "message": "Message",
        },
        "twitch_giftsub": {
            "tier": "Tier",
            "gifter_username": "Gifter Username",
            "quantity": "Quantity",
            "username": "Recipient Username",
        },
        "twitch_raid": {
            "viewer_count": "Viewer Count",
            "username": "Username",
        },
        "twitch_chat_message": {
            "username": "Username",
            "message": "Message",
            "is_moderator": "Is Moderator",
        },
        "donation": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
        },
        "twitch_points": {
            "reward_name": "Reward Name",
            "reward_id": "Reward ID",
            "username": "Username",
            "user_input": "User Input",
        },
        "youtube_chat_message": {
            "username": "Username",
            "message": "Message",
            "user_id": "User ID",
        },
        "youtube_member": {
            "username": "Username",
            "member_level": "Member Level",
            "message": "Message",
        },
        "youtube_member_milestone": {
            "username": "Username",
            "months": "Months",
            "member_level": "Member Level",
            "message": "Message",
        },
        "youtube_gift_membership": {
            "username": "Username",
            "gift_count": "Gift Count",
            "quantity": "Quantity",
            "member_level": "Member Level",
        },
        "youtube_superchat": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
            "currency": "Currency",
            "display_amount": "Display Amount",
        },
        "youtube_supersticker": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
            "currency": "Currency",
            "display_amount": "Display Amount",
        },
        "obs_scene_changed": {
            "scene_name": "Scene name",
            "previous_scene_name": "Previous scene",
        },
        "obs_stream_state": {
            "output_active": "Streaming output active (true/false)",
            "output_state": "Stream state constant (OBS_WEBSOCKET_OUTPUT_STATE_*)",
        },
        "obs_record_state": {
            "output_active": "Recording active (true/false)",
            "output_state": "Record state constant (OBS_WEBSOCKET_OUTPUT_STATE_*)",
        },
        "obs_input_mute": {
            "input_name": "Input name",
            "input_muted": "Input muted",
        },
    }


def get_available_fields_for_trigger(trigger_type: str) -> Dict[str, str]:
    """Get available fields for a trigger type"""
    field_mappings = _trigger_field_mappings()
    if trigger_type == TriggerType.ANY.value:
        merged: Dict[str, str] = {}
        for fields in field_mappings.values():
            merged.update(fields)
        return dict(sorted(merged.items(), key=lambda item: item[1].lower()))
    return field_mappings.get(trigger_type, {"username": "Username"})


def _call_game_hook_hint_refresh(form_data: dict) -> None:
    cb = form_data.get("_game_hook_hint_refresh")
    if callable(cb):
        try:
            cb()
        except Exception as e:
            logger.debug("game hook hint refresh: %s", e)


def game_hook_placeholder_lines(
    form_data: dict,
    hint_tags: Optional[Sequence[str]] = None,
    action_index: Optional[int] = None,
) -> List[str]:
    """Placeholder tokens for Game Hook arg hints (single-brace, no spaces).

    ``hint_tags`` on catalog args are scoped so one field does not advertise
    unrelated hook paths (e.g. gil under enemy-only inputs). Use:

    - ``character`` — party slot names + ``{random_character}``
    - ``enemy`` — enemy slot names + ``{random_enemy}``
    - ``hooks_gil``, ``hooks_battle``, ``hooks_field`` — single hook paths
    - ``hooks_party`` / ``hooks_enemy`` — slot names only (no random_*)
    - ``gear`` — ``{random_weapon}`` / armor / accessory
    - ``damage`` — ``{random_damage.min.max}`` example (battle damage amounts)
    - ``random_range`` — same random-range token (non-damage amounts: gil, HP)
    - ``numeric`` — no extra hook lines (duration, etc.)
    - ``action_index`` (0-based) — for later slots, prior-step ``{actionN.*}`` tokens.
    """
    tags = frozenset(hint_tags or ())
    lines: List[str] = []

    tt = form_data.get("trigger_type")
    trigger_fields = get_available_fields_for_trigger(tt) if tt else {}
    for fid in trigger_fields.keys():
        lines.append(f"{{{fid}}}")

    for cond in form_data.get("trigger_conditions") or []:
        f = cond.get("field")
        if isinstance(f, str) and f.strip():
            tok = f"{{{f.strip()}}}"
            if tok not in lines:
                lines.append(tok)

    for core in (
        "message",
        "username",
        "event_type",
        "timestamp",
        "source",
        "trigger_id",
        "trigger_type",
        "connector_id",
    ):
        lines.append(f"{{{core}}}")

    # Scoped FF7 hook readouts (avoid one mega-block for every tag).
    if tags and ("character" in tags or "hooks_party" in tags):
        for i in range(3):
            lines.append(f"{{hooks.ff7.party.{i}.name}}")
    if tags and "character" in tags:
        lines.append("{random_character}")

    if tags and ("enemy" in tags or "hooks_enemy" in tags):
        for i in range(6):
            lines.append(f"{{hooks.ff7.enemies.{i}.name}}")
    if tags and ("enemy" in tags or "hooks_enemy" in tags):
        lines.append("{message_after_conditions}")
    if tags and "enemy" in tags:
        lines.append("{random_enemy}")

    if tags and "hooks_gil" in tags:
        lines.append("{hooks.ff7.gil}")

    if action_index is not None and action_index > 0:
        for j in range(1, action_index + 1):
            lines.append(f"{{action{j}.item_name}}")
            lines.append(f"{{action{j}.quantity}}")
            lines.append(f"{{action{j}.resolved_name}}")
            lines.append(f"{{action{j}.kind}}")
            lines.append(f"{{action{j}.error}}")

    if tags and "hooks_battle" in tags:
        lines.append("{hooks.ff7.battle}")

    if tags and "hooks_field" in tags:
        lines.append("{hooks.ff7.field_name}")

    if tags and ("damage" in tags or "random_range" in tags):
        lines.append("{random_damage.1.9999}")

    if tags and "gear" in tags:
        lines.extend(
            [
                "{random_weapon}",
                "{random_armor}",
                "{random_accessory}",
            ]
        )

    if "message" in trigger_fields or "message" in tags:
        lines.append("{message.word.1}")
        lines.append("{message.word.2}")
        lines.append("{message_after_word.1}")
        lines.append("{message_after_word.2}")
        lines.append("{message_after_from_word.3}")

    seen: set[str] = set()
    uniq: List[str] = []
    for x in lines:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def add_action_to_form(form_data: dict, actions_container):
    """Add an action to the form"""
    add_action_to_form_with_data(form_data, actions_container, None)


def add_action_to_form_with_data(
    form_data: dict, actions_container, action_data: dict = None
):
    """Add an action to the form with optional existing data"""
    # For new actions, calculate the next index
    action_index = len(form_data.get("actions", []))
    add_action_to_form_with_data_and_index(
        form_data, actions_container, action_data, action_index
    )


def add_action_to_form_with_data_and_index(
    form_data: dict,
    actions_container,
    action_data: dict = None,
    action_index: int = None,
):
    """Add an action to the form with optional existing data and explicit index"""
    # Get available actions
    available_actions = get_available_actions()

    # Use existing data or defaults
    initial_type = _select_display_value(
        action_data.get("type") if action_data else None
    )
    initial_config = action_data.get("config", {}) if action_data else {}

    # If this is a new action (no action_data), add it to form_data
    if action_data is None:
        if "actions" not in form_data:
            form_data["actions"] = []
        if action_index is None:
            action_index = len(form_data["actions"])
        form_data["actions"].append(
            {"type": None, "config": initial_config, "delay_seconds": 0}
        )

    # If action_index is still None, use the current position in the actions list
    if action_index is None:
        action_index = len(form_data.get("actions", [])) - 1

    initial_delay = 0.0
    if action_data is not None:
        initial_delay = float(action_data.get("delay_seconds", 0) or 0)
    elif action_index < len(form_data.get("actions", [])):
        initial_delay = float(
            form_data["actions"][action_index].get("delay_seconds", 0) or 0
        )

    with actions_container:
        with ui.element("div").classes("w-full p-3 action-container rounded mb-3"):
            with ui.row().classes("w-full items-center justify-between mb-2 gap-2"):
                ui.label(f"Action #{action_index + 1}").classes(
                    "text-base font-medium"
                )
                if action_index > 0:
                    with ui.row().classes("items-center gap-2 grow justify-end"):
                        form_number(
                            tooltip=f"Delay in seconds from Action #{action_index}",
                            label="Delay (s)",
                            value=initial_delay,
                            min=0,
                            step=1,
                            on_change=lambda e, idx=action_index: update_action_delay(
                                idx, e.value, form_data
                            ),
                        ).classes("w-28")
                        ui.label(
                            f"Delay in seconds from Action #{action_index}"
                        ).classes("text-xs muted-text")
                ui.button(
                    icon="delete",
                    on_click=lambda idx=action_index: remove_action(
                        idx, form_data, actions_container
                    ),
                ).props("flat round").classes("text-red-400")

            form_select(
        tooltip="Action Type",
                options=available_actions,
                label="Action Type",
                value=initial_type,
                on_change=lambda e, idx=action_index: handle_action_type_change(
                    idx, e.value, form_data, action_config_container
                ),
            ).classes("w-full mb-3 action-select")

            action_config_container = ui.element("div").classes("w-full")

    # If we have existing data, populate the configuration
    if action_data and initial_type:
        handle_action_type_change_with_data(
            action_index,
            initial_type,
            form_data,
            action_config_container,
            initial_config,
        )


def get_available_actions() -> Dict[str, str]:
    """Get available action types"""
    return {
        "template_control": "Template Control",
        "websocket_emit": "WebSocket Event",
        "trigger_alert": "Trigger Alert",
        "send_chat_message": "Send Chat Message",
        "send_announcement": "Send Announcement",
        "send_discord_message": "Send Discord Message",
        "add_greeting": "Add Greeting",
        "update_greeting": "Update Greeting",
        "send_greeting": "Send Greeting",
        "api_call": "API Call",
        "write_file": "Write to File",
        "execute_command": "Execute Command",
        "key_press": "Key Press / Mouse Click",
        "audio_control": "Audio Control",
        "game_hook": "Game Hook (memory write)",
        "obs_control": "OBS Studio (WebSocket)",
    }


def handle_action_type_change(
    action_index: int, action_type: str, form_data: dict, config_container
):
    """Handle action type change and show relevant configuration options"""
    handle_action_type_change_with_data(
        action_index, action_type, form_data, config_container, {}
    )


def handle_action_type_change_with_data(
    action_index: int,
    action_type: str,
    form_data: dict,
    config_container,
    initial_config: dict = None,
):
    """Handle action type change and show relevant configuration options with initial data"""
    if not action_type:
        return

    if initial_config is None:
        initial_config = {}

    form_data["actions"][action_index]["type"] = action_type
    # Only clear config if we don't have initial data
    if not initial_config:
        form_data["actions"][action_index]["config"] = {}
    else:
        form_data["actions"][action_index]["config"] = initial_config.copy()

    config_container.clear()

    with config_container:
        if action_type == "template_control":
            create_template_control_config(action_index, form_data, initial_config)
        elif action_type == "websocket_emit":
            create_websocket_emit_config(action_index, form_data, initial_config)
        elif action_type == "send_chat_message":
            create_chat_message_config(action_index, form_data, initial_config)
        elif action_type == "send_announcement":
            create_send_announcement_config(action_index, form_data, initial_config)
        elif action_type == "send_discord_message":
            create_discord_message_config(action_index, form_data, initial_config)
        elif action_type == "add_greeting":
            create_add_greeting_config(action_index, form_data, initial_config)
        elif action_type == "update_greeting":
            create_update_greeting_config(action_index, form_data, initial_config)
        elif action_type == "send_greeting":
            create_send_greeting_config(action_index, form_data, initial_config)
        elif action_type == "trigger_alert":
            create_trigger_alert_config(action_index, form_data, initial_config)
        elif action_type == "api_call":
            create_api_call_config(action_index, form_data, initial_config)
        elif action_type == "execute_command":
            create_execute_command_config(action_index, form_data, initial_config)
        elif action_type == "write_file":
            create_write_file_config(action_index, form_data, initial_config)
        elif action_type == "key_press":
            create_key_press_config(action_index, form_data, initial_config)
        elif action_type == "audio_control":
            create_audio_control_config(action_index, form_data, initial_config)
        elif action_type == "game_hook":
            create_game_hook_config(action_index, form_data, initial_config)
        elif action_type == "obs_control":
            create_obs_control_config(action_index, form_data, initial_config)


def create_game_hook_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Configure crowd-control style game memory writes (FF7 first)."""
    if initial_config is None:
        initial_config = {}

    from ..game_hooks.ff7_hook import (
        FF7_CONNECTOR_CATALOG,
        catalog_entry_is_public,
        ff7_game_speed_select_options,
    )

    speed_select_options = ff7_game_speed_select_options()
    op_options: Dict[str, str] = {}
    for c in FF7_CONNECTOR_CATALOG:
        if catalog_entry_is_public(c):
            op_options[c["id"]] = c["label"]
    cur_op = initial_config.get("operation", "add_gil")
    if cur_op not in op_options:
        spec_legacy = next(
            (c for c in FF7_CONNECTOR_CATALOG if c["id"] == cur_op), None
        )
        op_options[cur_op] = (
            (spec_legacy or {}).get("label", cur_op) + " (legacy)"
            if spec_legacy
            else cur_op + " (legacy)"
        )

    form_select(
        tooltip="Game",
        options={"ff7": "Final Fantasy VII (2013)"},
        label="Game",
        value=initial_config.get("game_id", "ff7"),
        on_change=lambda e: update_action_config(
            action_index, "game_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    _op_args_slot: List[Any] = [None]

    def _live_action_config() -> dict:
        try:
            return form_data["actions"][action_index].get("config") or {}
        except Exception:
            return initial_config

    def refresh_args(op_id: str) -> None:
        op_container = _op_args_slot[0]
        if op_container is None:
            return
        op_container.clear()
        spec = next((c for c in FF7_CONNECTOR_CATALOG if c["id"] == op_id), None)
        with op_container:
            cfg_now = _live_action_config()
            if not spec:
                ui.label(
                    f"Legacy or unknown operation \"{op_id}\" — edit raw arg_* keys below."
                ).classes("text-sm text-amber-400 mb-2")
                for k in sorted(cfg_now.keys()):
                    if not k.startswith("arg_"):
                        continue
                    form_input(
                        tooltip=k,
                        label=k,
                        value=str(cfg_now.get(k, "")),
                        on_change=lambda e, kk=k: update_action_config(
                            action_index, kk, e.value, form_data
                        ),
                    ).classes("w-full mb-2 action-input")
                return
            ui.label(spec.get("description", "")).classes("text-sm muted-text mb-2")
            for arg in spec.get("args", []):
                aname = arg["name"]
                key = f"arg_{aname}"
                val = str(cfg_now.get(key, initial_config.get(key, "")))

                if arg.get("control") == "select":
                    sel_opts = arg.get("options") or {}
                    if aname == "speed" and not sel_opts:
                        sel_opts = speed_select_options
                    if val and val not in sel_opts:
                        sel_opts = dict(sel_opts)
                        sel_opts[val] = val
                    _arg_label = arg.get("label", aname)
                    form_select(
                        tooltip=_arg_label,
                        options=sel_opts,
                        label=_arg_label,
                        value=val if val in sel_opts else next(iter(sel_opts.keys()), val),
                        on_change=lambda e, k=key: update_action_config(
                            action_index, k, e.value, form_data
                        ),
                    ).classes("w-full mb-1 action-select")
                else:
                    hint_lines = game_hook_placeholder_lines(
                        form_data, arg.get("hint_tags"), action_index
                    )
                    hint_text = "  ".join(hint_lines) if hint_lines else ""
                    _arg_label = arg.get("label", aname)
                    form_input(
                        tooltip=_arg_label,
                        label=_arg_label,
                        value=val,
                        on_change=lambda e, k=key: update_action_config(
                            action_index, k, e.value, form_data
                        ),
                    ).classes("w-full mb-1 action-input")
                    if hint_text:
                        ui.label(hint_text).classes(
                            "text-xs muted-text mb-2 wrap-break-word"
                        )

    def refresh_args_from_form() -> None:
        try:
            op = form_data["actions"][action_index]["config"].get(
                "operation", "add_gil"
            )
        except Exception:
            op = "add_gil"
        refresh_args(str(op))

    form_data["_game_hook_hint_refresh"] = refresh_args_from_form

    form_select(
        tooltip="Operation",
        options=op_options,
        label="Operation",
        value=cur_op if cur_op in op_options else next(iter(op_options.keys()), cur_op),
        on_change=lambda e: [
            update_action_config(action_index, "operation", e.value, form_data),
            refresh_args(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    _op_args_slot[0] = ui.element("div").classes("w-full mb-2")
    refresh_args(cur_op)

    ui.label(
        "Requires Game Hooks enabled and the game running on Windows. "
        "Battle-only actions fail safely when not in combat."
    ).classes("text-xs muted-text mt-2")


def create_obs_control_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Configure OBS Studio WebSocket actions (scenes, sources, stream, record, etc.)."""
    if initial_config is None:
        initial_config = {}

    from ..obs_connector_catalog import OBS_CONNECTOR_CATALOG
    from ..obs_service import obs_service

    def _live_cfg() -> dict:
        try:
            return form_data["actions"][action_index].get("config") or {}
        except Exception:
            return initial_config

    op_container = ui.element("div").classes("w-full")
    op_labels = {c["id"]: c["label"] for c in OBS_CONNECTOR_CATALOG}
    cur_op = str(initial_config.get("operation") or "set_program_scene")
    if cur_op not in op_labels:
        op_labels[cur_op] = f"{cur_op} (legacy)"

    def _snapshot_placeholder(dynamic: Optional[str]) -> str:
        """Explain empty OBS selects (dict keys are invisible to users if we used empty-string keys only)."""

        conn_ok, _, _ = obs_service.connection_details()
        if conn_ok:
            if dynamic == "scene_item":
                return (
                    "(Choose the Scene slot first above, load lists, "
                    "or tap Refresh OBS lists — scene items appear per scene.)"
                )
            return (
                "(Nothing loaded yet — lists refresh when this opens or when "
                "you tap Refresh OBS lists)"
            )
        return "(OBS disconnected — set up Settings → OBS, then reopen or Refresh)"

    def refresh_op_args() -> None:
        op_container.clear()
        cfg = _live_cfg()
        op_id = str(cfg.get("operation") or "set_program_scene")
        spec = next((x for x in OBS_CONNECTOR_CATALOG if x["id"] == op_id), None)
        snap = obs_service.get_connector_snapshot()
        scene_names = snap.get("scene_names") or []
        scene_opts = {n: n for n in scene_names}
        input_opts = {n: n for n in (snap.get("input_names") or [])}

        with op_container:
            if not spec:
                ui.label(f'Unknown OBS operation "{op_id}"').classes("text-negative")
                return
            desc = spec.get("description") or ""
            if desc:
                ui.label(desc).classes("text-sm muted-text mb-2")

            for arg in spec.get("args", []):
                aname = arg["name"]
                key = f"arg_{aname}"
                default_v = str(cfg.get(key, initial_config.get(key, "")))

                if arg.get("control") == "select":
                    opts: Dict[str, str] = dict(arg.get("options") or {})
                    dynamic = arg.get("dynamic")
                    if dynamic == "scene":
                        opts = {**scene_opts}
                    elif dynamic == "input":
                        opts = {**input_opts}
                    elif dynamic == "scene_item":
                        sag = arg.get("scene_arg") or "scene_name"
                        sn = str(cfg.get(f"arg_{sag}", "") or "").strip()
                        by = (snap.get("sources_by_scene") or {}).get(sn) or {}
                        opts = dict(by)
                    if default_v and default_v not in opts:
                        opts = {**opts, default_v: f"{default_v} (manual)"}
                    if not opts:
                        opts = {"": _snapshot_placeholder(dynamic)}

                    first_val = (
                        default_v
                        if default_v in opts
                        else next(iter(opts.keys()))
                    )

                    def _sel_change(e, k=key):
                        update_action_config(action_index, k, e.value, form_data)
                        refresh_op_args()

                    _arg_label = arg.get("label", aname)
                    form_select(
                        tooltip=_arg_label,
                        options=opts,
                        label=_arg_label,
                        value=first_val,
                        on_change=_sel_change,
                    ).classes("w-full mb-2 action-select")
                else:
                    _arg_label = arg.get("label", aname)
                    form_input(
                        tooltip=_arg_label,
                        label=_arg_label,
                        value=default_v,
                        on_change=lambda e, k=key: update_action_config(
                            action_index, k, e.value, form_data
                        ),
                    ).classes("w-full mb-2 action-input")

    def _refresh_remote_lists() -> None:
        async def _job() -> None:
            ok, err = await run.io_bound(obs_service.refresh_snapshot_blocking, 25.0)
            if not ok and err:
                notify(f"Could not refresh OBS lists: {err}", type="warning")
            else:
                notify("OBS lists updated", type="positive")
            refresh_op_args()

        layout_schedule(0, _job, once=True)

    ui.button("Refresh OBS lists", icon="refresh", on_click=_refresh_remote_lists).classes(
        "mb-2"
    )

    form_select(
        tooltip="OBS operation",
        options=op_labels,
        label="OBS operation",
        value=cur_op if cur_op in op_labels else next(iter(op_labels.keys())),
        on_change=lambda e: [
            update_action_config(action_index, "operation", e.value, form_data),
            refresh_op_args(),
        ],
    ).classes("w-full mb-2 action-select")

    refresh_op_args()

    async def _bootstrap_obs_lists_once() -> None:
        try:
            await run.io_bound(obs_service.refresh_snapshot_blocking, 25.0)
        except Exception as e:
            logger.debug("OBS action UI bootstrap snapshot: %s", e)
        refresh_op_args()

    layout_schedule(0, _bootstrap_obs_lists_once, once=True)

    ui.label(
        "Requires OBS Studio running with WebSocket enabled "
        "(default port 4455). Password is saved encrypted in Settings → OBS."
    ).classes("text-xs muted-text mt-2")


def create_key_press_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for key press action"""
    if initial_config is None:
        initial_config = {}

    # Input Type Selection
    form_select(
        tooltip="Input Type",
        options={"key": "Keyboard", "mouse": "Mouse", "macro": "Macro Sequence"},
        label="Input Type",
        value=initial_config.get("input_type", "key"),
        on_change=lambda e: [
            update_action_config(action_index, "input_type", e.value, form_data),
            refresh_key_press_ui(action_index, form_data, e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Dynamic container for input-specific options
    config_container = ui.element("div").classes("w-full")

    def refresh_key_press_ui(action_index: int, form_data: dict, input_type: str):
        """Refresh the UI based on input type selection"""
        config_container.clear()

        with config_container:
            if input_type == "key":
                create_keyboard_config(action_index, form_data, initial_config)
            elif input_type == "mouse":
                create_mouse_config(action_index, form_data, initial_config)
            elif input_type == "macro":
                create_macro_config(action_index, form_data, initial_config)

    # Initialize with current input type
    current_input_type = initial_config.get("input_type", "key")
    refresh_key_press_ui(action_index, form_data, current_input_type)


def create_keyboard_config(action_index: int, form_data: dict, initial_config: dict):
    """Create keyboard-specific configuration"""

    # Action Mode
    ui.select(
        options={
            "press": "Press & Release",
            "hold": "Press & Hold",
            "repeat": "Repeat",
        },
        label="Action Mode",
        value=initial_config.get("action_mode", "press"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_mode_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Key Sequence Input
    form_input(
        tooltip="Key Sequence",
        label="Key Sequence",
        placeholder="e.g., ctrl+c, alt+tab, up, arrow_down, shift+left, f1, enter",
        value=initial_config.get("key_sequence", ""),
        on_change=lambda e: update_action_config(
            action_index, "key_sequence", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Use '+' for combos (e.g., ctrl+c). Arrow keys: up, down, left, right, or arrow_up style. "
        "Use {{placeholder}} for dynamic values."
    ).classes("text-xs muted-text mb-2")

    # Mode-specific options container
    mode_options_container = ui.element("div").classes("w-full")

    def show_mode_options(mode: str):
        """Show options specific to the selected mode"""
        mode_options_container.clear()

        with mode_options_container:
            if mode == "hold":
                form_input(
        tooltip="Hold Duration (seconds)",
                    label="Hold Duration (seconds)",
                    placeholder="0.5",
                    value=str(initial_config.get("hold_duration", 0.5)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "hold_duration",
                        float(e.value) if e.value else 0.5,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
            elif mode == "repeat":
                with ui.row().classes("w-full gap-4"):
                    form_input(
        tooltip="Repeat Count",
                        label="Repeat Count",
                        placeholder="3",
                        value=str(initial_config.get("repeat_count", 1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_count",
                            int(e.value) if e.value.isdigit() else 1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")
                    form_input(
        tooltip="Interval (seconds)",
                        label="Interval (seconds)",
                        placeholder="0.1",
                        value=str(initial_config.get("repeat_interval", 0.1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_interval",
                            float(e.value) if e.value else 0.1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")

    # Initialize mode options
    current_mode = initial_config.get("action_mode", "press")
    show_mode_options(current_mode)


def create_mouse_config(action_index: int, form_data: dict, initial_config: dict):
    """Create mouse-specific configuration"""

    # Mouse Button Selection
    ui.select(
        options={
            "left_click": "Left Click",
            "right_click": "Right Click",
            "middle_click": "Middle Click",
        },
        label="Mouse Button",
        value=initial_config.get("key_sequence", "left_click"),
        on_change=lambda e: update_action_config(
            action_index, "key_sequence", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Action Mode
    ui.select(
        options={
            "press": "Single Click",
            "hold": "Press & Hold",
            "repeat": "Multiple Clicks",
        },
        label="Click Mode",
        value=initial_config.get("action_mode", "press"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_mouse_mode_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Mode-specific options container
    mouse_mode_options_container = ui.element("div").classes("w-full")

    def show_mouse_mode_options(mode: str):
        """Show options specific to the selected mouse mode"""
        mouse_mode_options_container.clear()

        with mouse_mode_options_container:
            if mode == "hold":
                form_input(
        tooltip="Hold Duration (seconds)",
                    label="Hold Duration (seconds)",
                    placeholder="0.5",
                    value=str(initial_config.get("hold_duration", 0.5)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "hold_duration",
                        float(e.value) if e.value else 0.5,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
            elif mode == "repeat":
                with ui.row().classes("w-full gap-4"):
                    form_input(
        tooltip="Click Count",
                        label="Click Count",
                        placeholder="3",
                        value=str(initial_config.get("repeat_count", 1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_count",
                            int(e.value) if e.value.isdigit() else 1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")
                    form_input(
        tooltip="Interval (seconds)",
                        label="Interval (seconds)",
                        placeholder="0.1",
                        value=str(initial_config.get("repeat_interval", 0.1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_interval",
                            float(e.value) if e.value else 0.1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")

    # Initialize mouse mode options
    current_mode = initial_config.get("action_mode", "press")
    show_mouse_mode_options(current_mode)


def create_macro_config(action_index: int, form_data: dict, initial_config: dict):
    """Create macro sequence configuration"""

    # Macro Description
    ui.label("Macro Sequence").classes("text-base font-medium secondary-text mb-2")
    ui.label("Define a sequence of keyboard and mouse actions with delays.").classes(
        "text-xs muted-text mb-2"
    )

    # Macro steps container
    macro_container = ui.element("div").classes("w-full")

    # Initialize macro steps from config
    try:
        import json

        macro_steps = json.loads(initial_config.get("macro_sequence", "[]"))
    except Exception:
        macro_steps = []

    if not macro_steps:
        macro_steps = [{"type": "key", "target": "", "delay": 0.1}]

    def update_macro_sequence():
        """Update the macro sequence in form data"""
        import json

        macro_json = json.dumps(macro_steps)
        update_action_config(action_index, "macro_sequence", macro_json, form_data)

    def render_macro_steps():
        """Render all macro steps"""
        macro_container.clear()

        with macro_container:
            for i, step in enumerate(macro_steps):
                with ui.card().classes("w-full mb-2 p-3"):
                    with ui.row().classes("w-full items-center gap-4"):
                        # Step number
                        ui.label(f"Step {i+1}").classes(
                            "text-sm font-medium text-theme-primary min-w-16"
                        )

                        # Step type
                        ui.select(
                            options={
                                "key": "Keyboard",
                                "mouse": "Mouse",
                                "delay": "Delay",
                            },
                            value=step.get("type", "key"),
                            on_change=lambda e, idx=i: update_step_type(idx, e.value),
                        ).classes("w-32")

                        # Step target/value
                        if step.get("type") == "delay":
                            ui.input(
                                placeholder="Seconds",
                                value=str(step.get("target", "1.0")),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")
                        elif step.get("type") == "mouse":
                            ui.select(
                                options={
                                    "left_click": "Left",
                                    "right_click": "Right",
                                    "middle_click": "Middle",
                                },
                                value=step.get("target", "left_click"),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")
                        else:  # keyboard
                            ui.input(
                                placeholder="e.g., ctrl+c, enter, space",
                                value=step.get("target", ""),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")

                        # Delay after step
                        if step.get("type") != "delay":
                            ui.input(
                                placeholder="Delay",
                                value=str(step.get("delay", 0.1)),
                                on_change=lambda e, idx=i: update_step_delay(
                                    idx, e.value
                                ),
                            ).classes("w-20")

                        # Remove step button
                        if len(macro_steps) > 1:
                            ui.button(
                                icon="delete", on_click=lambda idx=i: remove_step(idx)
                            ).props("flat dense").classes("text-red-400")

            # Add step button
            ui.button(icon="add", text="Add Step", on_click=add_step).classes(
                "w-full mt-2 btn-primary"
            )

    def update_step_type(idx: int, step_type: str):
        """Update step type"""
        if idx < len(macro_steps):
            macro_steps[idx]["type"] = step_type
            if step_type == "delay":
                macro_steps[idx]["target"] = "1.0"
            elif step_type == "mouse":
                macro_steps[idx]["target"] = "left_click"
            else:
                macro_steps[idx]["target"] = ""
            update_macro_sequence()
            render_macro_steps()

    def update_step_target(idx: int, target: str):
        """Update step target"""
        if idx < len(macro_steps):
            macro_steps[idx]["target"] = target
            update_macro_sequence()

    def update_step_delay(idx: int, delay: str):
        """Update step delay"""
        if idx < len(macro_steps):
            try:
                macro_steps[idx]["delay"] = float(delay) if delay else 0.1
            except ValueError:
                macro_steps[idx]["delay"] = 0.1
            update_macro_sequence()

    def add_step():
        """Add a new step"""
        macro_steps.append({"type": "key", "target": "", "delay": 0.1})
        update_macro_sequence()
        render_macro_steps()

    def remove_step(idx: int):
        """Remove a step"""
        if len(macro_steps) > 1 and idx < len(macro_steps):
            macro_steps.pop(idx)
            update_macro_sequence()
            render_macro_steps()

    # Initial render
    render_macro_steps()

    # Help text
    ui.label(
        "Use {{placeholder}} in keyboard steps for dynamic values from trigger events."
    ).classes("text-xs muted-text mt-2")


def create_audio_control_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for audio control action"""
    if initial_config is None:
        initial_config = {}

    # Permission Status Section
    permission_container = ui.element("div").classes("w-full mb-4")
    update_permission_status(permission_container)

    # Control Type Selection
    ui.select(
        options={
            "device": "Device",
            "application": "Application",
        },
        label="Control Type",
        value=initial_config.get("control_type", "device"),
        on_change=lambda e: [
            update_action_config(action_index, "control_type", e.value, form_data),
            refresh_audio_control_ui(action_index, form_data, e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Duration input (shown for all control types)
    form_input(
        tooltip="Duration (seconds)",
        label="Duration (seconds)",
        placeholder="0",
        value=str(initial_config.get("duration", 0.0)),
        on_change=lambda e: update_action_config(
            action_index, "duration", float(e.value) if e.value else 0.0, form_data
        ),
    ).classes("w-full mb-4 action-input")

    ui.label(
        "Duration in seconds (0 = permanent change, >0 = temporary change that auto-restores)"
    ).classes("text-xs muted-text mb-4")

    # Dynamic container for control-specific options
    config_container = ui.element("div").classes("w-full")

    def refresh_audio_control_ui(action_index: int, form_data: dict, control_type: str):
        """Refresh the UI based on control type selection"""
        config_container.clear()

        with config_container:
            if control_type == "device":
                create_device_config(action_index, form_data, initial_config)
            elif control_type == "application":
                create_application_config(action_index, form_data, initial_config)

    # Initialize with current control type
    current_control_type = initial_config.get("control_type", "device")
    refresh_audio_control_ui(action_index, form_data, current_control_type)


def update_permission_status(container):
    """Update the permission status display"""
    container.clear()

    with container:
        permission_info = check_audio_permissions()

        # Permission status indicator
        with ui.row().classes("w-full items-center gap-2 mb-2"):
            if permission_info["has_permissions"]:
                ui.icon("check_circle", color="green").classes("text-green-500")
                ui.label("Audio permissions: OK").classes("text-green-600 font-medium")
            else:
                ui.icon("warning", color="orange").classes("text-orange-500")
                ui.label("Audio permissions: Restricted").classes(
                    "text-orange-600 font-medium"
                )

        # Permission message
        ui.label(permission_info["message"]).classes("text-sm muted-text mb-2")

        # Request permissions button (if applicable)
        if not permission_info["has_permissions"] and permission_info.get(
            "can_request", False
        ):
            ui.button(
                "Request Permissions",
                icon="security",
                on_click=lambda: request_permissions_and_update(container),
            ).classes("btn-secondary text-sm px-3 py-1")

        # Additional instructions for Windows admin
        if permission_info.get("is_admin") is False:
            ui.label(
                "💡 Tip: Right-click the application and select 'Run as administrator' for full audio control"
            ).classes("text-xs text-blue-600 mt-2")


def request_permissions_and_update(container):
    """Request permissions and update the status display"""
    try:
        success = request_audio_permissions()
        if success:
            notify("Permission request initiated", type="positive")
        else:
            notify("Permission request failed or not supported", type="warning")

        # Refresh the permission status
        update_permission_status(container)
    except Exception as e:
        logger.error(f"Error requesting permissions: {e}")
        notify(f"Error requesting permissions: {e}", type="negative")


def create_system_volume_config(
    action_index: int, form_data: dict, initial_config: dict
):
    """Create system volume configuration"""

    # Action Mode
    ui.select(
        options={
            "set": "Set Volume",
            "set_random": "Set Random Volume",
            "increase": "Increase Volume",
            "decrease": "Decrease Volume",
            "mute": "Mute",
            "unmute": "Unmute",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_volume_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Volume options container
    volume_options_container = ui.element("div").classes("w-full")

    def show_volume_options(mode: str):
        """Show options specific to the selected mode"""
        volume_options_container.clear()

        with volume_options_container:
            if mode == "set":
                form_input(
        tooltip="Volume Level (%)",
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Volume level from 0-100%").classes("text-xs muted-text")
            elif mode in ["increase", "decrease"]:
                form_input(
        tooltip="Volume Step (%)",
                    label="Volume Step (%)",
                    placeholder="10",
                    value=str(initial_config.get("volume_step", 10.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_step",
                        float(e.value) if e.value else 10.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Amount to increase/decrease volume by").classes(
                    "text-xs muted-text"
                )

    # Initialize volume options
    current_mode = initial_config.get("action_mode", "set")
    show_volume_options(current_mode)


def create_microphone_config(action_index: int, form_data: dict, initial_config: dict):
    """Create microphone control configuration"""

    # Action Mode
    ui.select(
        options={
            "mute": "Mute Microphone",
            "unmute": "Unmute Microphone",
            "toggle_mute": "Toggle Mute",
            "set": "Set Microphone Level",
        },
        label="Action",
        value=initial_config.get("action_mode", "toggle_mute"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_microphone_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Microphone options container
    mic_options_container = ui.element("div").classes("w-full")

    def show_microphone_options(mode: str):
        """Show options specific to the selected microphone mode"""
        mic_options_container.clear()

        with mic_options_container:
            if mode == "set":
                form_input(
        tooltip="Microphone Level (%)",
                    label="Microphone Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Microphone input level from 0-100%").classes(
                    "text-xs muted-text"
                )

    # Initialize microphone options
    current_mode = initial_config.get("action_mode", "toggle_mute")
    show_microphone_options(current_mode)

    # Help text
    ui.label(
        "Microphone control may require elevated permissions on some systems."
    ).classes("text-xs text-yellow-500 mt-2")


def create_application_volume_config(
    action_index: int, form_data: dict, initial_config: dict
):
    """Create application volume configuration"""

    # Application Name Input
    form_input(
        tooltip="Application Name",
        label="Application Name",
        placeholder="e.g., discord.exe, spotify.exe, chrome.exe",
        value=initial_config.get("target_application", ""),
        on_change=lambda e: update_action_config(
            action_index, "target_application", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Enter the application process name (case-insensitive). Use {{placeholder}} for dynamic values."
    ).classes("text-xs muted-text mb-2")

    # Action Mode
    ui.select(
        options={
            "set": "Set Volume",
            "mute": "Mute Application",
            "unmute": "Unmute Application",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_app_volume_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # App volume options container
    app_volume_options_container = ui.element("div").classes("w-full")

    def show_app_volume_options(mode: str):
        """Show options specific to the selected app volume mode"""
        app_volume_options_container.clear()

        with app_volume_options_container:
            if mode == "set":
                form_input(
        tooltip="Volume Level (%)",
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Application volume level from 0-100%").classes(
                    "text-xs muted-text"
                )

    # Initialize app volume options
    current_mode = initial_config.get("action_mode", "set")
    show_app_volume_options(current_mode)

    # Platform notice
    # ui.label(
    #     "Application volume control is currently supported on Windows only."
    # ).classes("text-xs text-blue-500 mt-2")


def create_device_config(action_index: int, form_data: dict, initial_config: dict):
    """Create device-specific audio configuration"""
    if initial_config is None:
        initial_config = {}

    # Device Selection
    device_options = get_available_audio_devices()
    initial_device = initial_config.get("target_device", "")
    # Use default if initial value is empty or not in options
    if not initial_device or initial_device not in device_options:
        initial_device = (
            next(iter(device_options.keys())) if device_options else "default"
        )

    form_select(
        tooltip="Audio Device",
        options=device_options,
        label="Audio Device",
        value=initial_device,
        on_change=lambda e: update_action_config(
            action_index, "target_device", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Device type hint
    ui.label(
        "Select the audio device you want to control (speakers, microphone, etc.)"
    ).classes("text-xs muted-text mb-2")

    # Action Mode (reuse system volume config)
    create_system_volume_config(action_index, form_data, initial_config)


def create_application_config(action_index: int, form_data: dict, initial_config: dict):
    """Create application-specific audio configuration"""
    if initial_config is None:
        initial_config = {}

    # Application Selection
    app_options = get_available_applications()
    initial_app = initial_config.get("target_application", "")
    # Use default if initial value is empty or not in options
    if not initial_app or initial_app not in app_options:
        initial_app = next(iter(app_options.keys())) if app_options else "default"

    form_select(
        tooltip="Application",
        options=app_options,
        label="Application",
        value=initial_app,
        on_change=lambda e: update_action_config(
            action_index, "target_application", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Application hint
    ui.label("Select the application whose volume you want to control").classes(
        "text-xs muted-text mb-2"
    )

    # Action Mode for Applications
    ui.select(
        options={
            "set": "Set Volume",
            "mute": "Mute Application",
            "unmute": "Unmute Application",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_app_action_options(e.value, action_index, form_data, initial_config),
        ],
    ).classes("w-full mb-2 action-select")

    # App action options container
    app_action_options_container = ui.element("div").classes("w-full")

    def show_app_action_options(
        mode: str, action_index: int, form_data: dict, initial_config: dict
    ):
        """Show options specific to the selected app action mode"""
        app_action_options_container.clear()

        with app_action_options_container:
            if mode == "set":
                form_input(
        tooltip="Volume Level (%)",
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")

                ui.label(
                    "Set the volume level for the selected application (0-100)"
                ).classes("text-xs muted-text")

    # Initialize with current action mode
    current_mode = initial_config.get("action_mode", "set")
    show_app_action_options(current_mode, action_index, form_data, initial_config)


def get_available_audio_devices() -> Dict[str, str]:
    """Get available audio devices on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_audio_devices()
        elif system == "darwin":  # macOS
            return get_macos_audio_devices()
        elif system == "linux":
            return get_linux_audio_devices()
        else:
            return {"": "Default Device"}
    except Exception as e:
        logger.error(f"Error getting audio devices: {e}")
        return {"": "Default Device"}


def get_available_applications() -> Dict[str, str]:
    """Get available applications on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_applications()
        elif system == "darwin":  # macOS
            return get_macos_applications()
        elif system == "linux":
            return get_linux_applications()
        else:
            return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting applications: {e}")
        return {"default": "Default Application"}


def get_windows_audio_devices() -> Dict[str, str]:
    """Get Windows audio devices using pycaw with actual device names"""
    try:
        import warnings

        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import AudioDeviceState
        from pycaw.pycaw import AudioUtilities

        devices = {}
        seen_names = set()  # Track device names to avoid duplicates

        # Temporarily suppress pycaw warnings during device enumeration
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", module="pycaw.*", category=UserWarning)

            # Get all audio devices using the simpler AudioUtilities approach
            try:
                all_devices = AudioUtilities.GetAllDevices()

                for device in all_devices:
                    try:
                        # Only process enabled/active devices
                        if device.state != AudioDeviceState.Active:
                            continue

                        device_id = device.id

                        # Try to get friendly name, but don't fail if we can't
                        try:
                            friendly_name = device.FriendlyName
                            if not friendly_name:
                                continue
                        except Exception:
                            # Skip devices where we can't get the friendly name
                            continue

                        # Skip duplicates by name
                        if friendly_name in seen_names:
                            continue
                        seen_names.add(friendly_name)

                        # Determine if it's a playback or recording device based on name patterns
                        if any(
                            keyword in friendly_name.lower()
                            for keyword in [
                                "speaker",
                                "headphone",
                                "headset",
                                "hdmi",
                                "display",
                            ]
                        ):
                            devices[f"playback:{device_id}"] = f"[OUT] {friendly_name}"
                        elif any(
                            keyword in friendly_name.lower()
                            for keyword in ["microphone", "mic", "input", "line-in"]
                        ):
                            devices[f"recording:{device_id}"] = f"[MIC] {friendly_name}"

                    except Exception as e:
                        # Only log actual errors, not property access issues
                        if "COMError" not in str(e):
                            logger.warning(f"Error processing device: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error getting all devices: {e}")

        # If no devices found, provide fallback
        if not devices:
            devices = {"default": "Default Device"}

        return devices

    except ImportError:
        logger.warning("pycaw not available for Windows device enumeration")
        return {"default": "Default Device (pycaw not installed)"}
    except Exception as e:
        logger.error(f"Error getting Windows audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_macos_audio_devices() -> Dict[str, str]:
    """Get macOS audio devices with improved parsing"""
    try:
        import re
        import subprocess

        devices = {}

        # Use system_profiler to get audio devices
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            logger.warning("system_profiler failed, trying alternative method")
            return get_macos_audio_devices_fallback()

        lines = result.stdout.split("\n")
        current_section = None
        device_name = None
        device_type = None

        for line in lines:
            line = line.strip()

            # Check for section headers
            if "Audio Devices:" in line:
                current_section = "devices"
            elif "Output Devices:" in line:
                current_section = "output"
            elif "Input Devices:" in line:
                current_section = "input"
            elif line.startswith("Device Name:"):
                device_name = line.split(":", 1)[1].strip()
                device_type = "unknown"
            elif line.startswith("Default Output Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_output:{default_device}"] = (
                        f"[OUT] {default_device} (Default)"
                    )
            elif line.startswith("Default Input Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_input:{default_device}"] = (
                        f"[MIC] {default_device} (Default)"
                    )
            elif device_name and current_section:
                # Look for device type indicators
                if "Headphones" in device_name or "Headset" in device_name:
                    device_type = "headphones"
                elif "Speaker" in device_name or "Speakers" in device_name:
                    device_type = "speakers"
                elif "Microphone" in device_name or "Mic" in device_name:
                    device_type = "microphone"
                elif "Display" in device_name:
                    device_type = "display"

                # Add device with appropriate icon
                if device_type == "headphones":
                    devices[f"{current_section}:{device_name}"] = (
                        f"[HEAD] {device_name}"
                    )
                elif device_type == "microphone":
                    devices[f"{current_section}:{device_name}"] = f"[MIC] {device_name}"
                elif device_type == "speakers":
                    devices[f"{current_section}:{device_name}"] = f"[SPK] {device_name}"
                elif device_type == "display":
                    devices[f"{current_section}:{device_name}"] = f"[DSP] {device_name}"
                else:
                    # Generic device with section indicator
                    icon = "[OUT]" if current_section == "output" else "[MIC]"
                    devices[f"{current_section}:{device_name}"] = (
                        f"{icon} {device_name}"
                    )

                device_name = None  # Reset for next device

        # If we didn't find any devices, try fallback method
        if not devices:
            return get_macos_audio_devices_fallback()

        return devices

    except Exception as e:
        logger.error(f"Error getting macOS audio devices: {e}")
        return get_macos_audio_devices_fallback()


def get_macos_audio_devices_fallback() -> Dict[str, str]:
    """Fallback method for macOS audio device enumeration"""
    try:
        import subprocess

        devices = {"default": "Default Device"}

        # Try using audio device list command
        try:
            result = subprocess.run(
                ["audiodevice", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    if "Device:" in line:
                        device_info = line.split("Device:", 1)[1].strip()
                        devices[device_info] = device_info

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Try using SwitchAudioSource if available
        try:
            result = subprocess.run(
                ["SwitchAudioSource", "-a"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("("):
                        # Extract device name (usually before the first parenthesis)
                        device_name = line.split("(")[0].strip()
                        if device_name:
                            devices[device_name] = device_name

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return devices

    except Exception as e:
        logger.error(f"Error in macOS audio device fallback: {e}")
        return {"default": "Default Device"}


def get_linux_audio_devices() -> Dict[str, str]:
    """Get Linux audio devices using pulsectl with improved formatting"""
    try:
        import pulsectl

        devices = {}

        with pulsectl.Pulse("device-enumeration") as pulse:
            # Get output devices (speakers)
            for sink in pulse.sink_list():
                device_name = sink.description or f"Sink {sink.index}"
                # Add appropriate icon based on device type
                if (
                    "headphone" in device_name.lower()
                    or "headset" in device_name.lower()
                ):
                    icon = "[HEAD]"
                elif "speaker" in device_name.lower():
                    icon = "[SPK]"
                elif "hdmi" in device_name.lower() or "display" in device_name.lower():
                    icon = "[DSP]"
                else:
                    icon = "[OUT]"
                devices[f"sink:{sink.index}"] = f"{icon} {device_name}"

            # Get input devices (microphones)
            for source in pulse.source_list():
                if not source.name.endswith(".monitor"):  # Skip monitor sources
                    device_name = source.description or f"Source {source.index}"
                    # Add microphone icon
                    if (
                        "microphone" in device_name.lower()
                        or "mic" in device_name.lower()
                    ):
                        icon = "[MIC]"
                    elif "webcam" in device_name.lower():
                        icon = "[CAM]"
                    else:
                        icon = "[MIC]"
                    devices[f"source:{source.index}"] = f"{icon} {device_name}"

        return devices if devices else {"default": "Default Device"}
    except ImportError:
        logger.warning("pulsectl not available for Linux device enumeration")
        return {"default": "Default Device (pulsectl not installed)"}
    except Exception as e:
        logger.error(f"Error getting Linux audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_windows_applications() -> Dict[str, str]:
    """Get Windows applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "svchost",
                            "system",
                            "winlogon",
                            "csrss",
                            "smss",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Windows application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Windows applications: {e}")
        return {"default": "Default Application"}


def get_macos_applications() -> Dict[str, str]:
    """Get macOS applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in ["kernel", "launchd", "system", "windowserver"]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for macOS application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting macOS applications: {e}")
        return {"default": "Default Application"}


def get_linux_applications() -> Dict[str, str]:
    """Get Linux applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "systemd",
                            "init",
                            "kthreadd",
                            "ksoftirqd",
                            "migration",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Linux application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Linux applications: {e}")
        return {"default": "Default Application"}


def check_audio_permissions() -> Dict[str, Any]:
    """Check audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return check_macos_audio_permissions()
        elif system == "windows":
            return check_windows_audio_permissions()
        elif system == "linux":
            return check_linux_audio_permissions()
        else:
            return {
                "has_permissions": True,
                "message": "Unknown operating system",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": f"Error checking permissions: {e}",
            "can_request": False,
        }


def check_macos_audio_permissions() -> Dict[str, Any]:
    """Check macOS microphone and audio permissions"""
    try:
        # Check if we can access microphone using system_profiler
        import subprocess

        result = subprocess.run(
            ["system_profiler", "SPMicrophoneDataType"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0 and "No microphone" not in result.stdout:
            return {
                "has_permissions": True,
                "message": "Microphone access available",
                "can_request": False,  # Permissions are granted automatically when first requested
            }
        else:
            return {
                "has_permissions": False,
                "message": "Microphone access may be restricted. Check System Preferences > Security & Privacy > Microphone",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking macOS audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check microphone permissions",
            "can_request": False,
        }


def check_windows_audio_permissions() -> Dict[str, Any]:
    """Check Windows audio permissions and administrator status"""
    try:
        import ctypes

        # Check if running as administrator
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0

        if is_admin:
            return {
                "has_permissions": True,
                "message": "Running as administrator - full audio control available",
                "can_request": False,
                "is_admin": True,
            }
        else:
            return {
                "has_permissions": False,
                "message": "Not running as administrator. Some audio operations may be restricted.",
                "can_request": True,
                "is_admin": False,
                "request_message": "Restart the application as administrator to enable full audio control",
            }
    except Exception as e:
        logger.error(f"Error checking Windows audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check administrator status",
            "can_request": False,
        }


def check_linux_audio_permissions() -> Dict[str, Any]:
    """Check Linux audio permissions"""
    try:
        import grp
        import os
        import pwd

        # Get current user
        current_user = pwd.getpwuid(os.getuid()).pw_name

        # Check if user is in audio group
        try:
            audio_group = grp.getgrnam("audio")
            user_in_audio_group = current_user in audio_group.gr_mem
        except KeyError:
            # Audio group doesn't exist
            user_in_audio_group = False

        if user_in_audio_group:
            return {
                "has_permissions": True,
                "message": "User is in audio group - audio access available",
                "can_request": False,
            }
        else:
            return {
                "has_permissions": False,
                "message": "User is not in the 'audio' group. Audio device access may be restricted.",
                "can_request": True,
                "request_message": f"Add user to audio group: 'sudo usermod -a -G audio {current_user}' then restart the application",
            }
    except Exception as e:
        logger.error(f"Error checking Linux audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check audio group membership",
            "can_request": False,
        }


def request_audio_permissions() -> bool:
    """Request audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return request_macos_audio_permissions()
        elif system == "windows":
            return request_windows_audio_permissions()
        elif system == "linux":
            return request_linux_audio_permissions()
        else:
            logger.warning(f"Permission request not supported on {system}")
            return False
    except Exception as e:
        logger.error(f"Error requesting audio permissions: {e}")
        return False


def request_macos_audio_permissions() -> bool:
    """Request macOS audio permissions - opens System Preferences"""
    try:
        import subprocess

        # Try to open System Preferences to Microphone pane
        script = """
        tell application "System Preferences"
            activate
            reveal anchor "Privacy_Microphone" of pane id "com.apple.preference.security"
        end tell
        """

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Opened macOS System Preferences for microphone permissions")
            return True
        else:
            logger.error(f"Failed to open System Preferences: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting macOS permissions: {e}")
        return False


def request_windows_audio_permissions() -> bool:
    """Request Windows audio permissions by attempting to run as admin"""
    try:
        import os
        import subprocess
        import sys

        # Try to relaunch as administrator
        script = f"""
        If (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
            $arguments = "& '{sys.executable}'"
            foreach ($arg in $args) {{
                $arguments += " '$arg'"
            }}
            Start-Process powershell -Verb runAs -ArgumentList $arguments
            Exit
        }}
        """

        # For now, just show a message
        logger.info(
            "To run as administrator, right-click the application and select 'Run as administrator'"
        )
        return True
    except Exception as e:
        logger.error(f"Error requesting Windows permissions: {e}")
        return False


def request_linux_audio_permissions() -> bool:
    """Request Linux audio permissions by showing instructions"""
    try:
        import os
        import pwd

        current_user = pwd.getpwuid(os.getuid()).pw_name
        command = f"sudo usermod -a -G audio {current_user}"

        logger.info(f"To add audio permissions, run: {command}")
        logger.info("Then restart the application")

        # Try to run the command if we have sudo access
        import subprocess

        result = subprocess.run(
            ["sudo", "usermod", "-a", "-G", "audio", current_user],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Successfully added user to audio group")
            return True
        else:
            logger.warning(f"Failed to add user to audio group: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting Linux permissions: {e}")
        return False


def create_template_control_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for template control action"""
    if initial_config is None:
        initial_config = {}

    # Get available templates and their actions
    try:
        integration = connector_integration.get_integration()
        template_actions = integration.get_available_template_actions()
        logger.debug(f"Template actions received in UI: {template_actions}")

        template_options = {name: name.title() for name in template_actions.keys()}

        initial_template = initial_config.get("template_name") or None
        initial_action = initial_config.get("control_action") or None

        form_select(
        tooltip="Template",
            options=template_options,
            label="Template",
            value=initial_template,
            on_change=lambda e: handle_template_change(
                e.value, action_index, form_data, action_select, action_params_container
            ),
        ).classes("w-full mb-2 action-select")

        # Get initial action options if template is selected
        initial_action_options = {}
        if initial_template and initial_template in template_actions:
            actions = template_actions[initial_template]
            # actions is now a dict of {action_id: action_config}
            initial_action_options = {
                action_id: action_config.get("action_name", action_id)
                for action_id, action_config in actions.items()
            }
            logger.debug(
                f"Initial template '{initial_template}' has actions: {initial_action_options}"
            )
        else:
            logger.debug(
                f"No initial template or template '{initial_template}' not found in template_actions"
            )

        action_select = form_select(
        tooltip="Action",
            options=initial_action_options,
            label="Action",
            value=initial_action,
            on_change=lambda e: handle_action_change(
                e.value, action_index, form_data, action_params_container
            ),
        ).classes("w-full mb-2 action-select")

        # Container for action-specific parameters
        action_params_container = ui.element("div").classes("w-full mt-3")

        def handle_template_change(
            template_name,
            action_index,
            form_data,
            action_select,
            action_params_container,
        ):
            logger.debug(
                f"handle_template_change called with template_name: {template_name}"
            )
            # Update the form data
            update_action_config(
                action_index, "template_name", template_name, form_data
            )

            # Clear action parameters container
            action_params_container.clear()

            # Update the action options
            if template_name and template_name in template_actions:
                actions = template_actions[template_name]
                logger.debug(f"Found actions for {template_name}: {actions}")
                # actions is now a dict of {action_id: action_config}
                action_options = {
                    action_id: action_config.get("action_name", action_id)
                    for action_id, action_config in actions.items()
                }
                logger.debug(f"Setting action_options: {action_options}")
                action_select.options = action_options
                action_select.update()
                # Clear the current action selection since template changed
                action_select.value = None
                update_action_config(action_index, "control_action", None, form_data)
                # Update the action select handler with the new template
                action_select.on_value_change = lambda e: handle_action_change(
                    e.value, action_index, form_data, action_params_container
                )
            else:
                logger.debug(
                    f"No actions found for template {template_name} or template not in template_actions"
                )
                logger.debug(f"Available templates: {list(template_actions.keys())}")
                action_select.options = {}
                action_select.update()
                action_select.value = None
                update_action_config(action_index, "control_action", None, form_data)

        def handle_action_change(
            action_name, action_index, form_data, action_params_container
        ):
            logger.debug(f"handle_action_change called with action_name: {action_name}")
            # Update the form data
            update_action_config(action_index, "control_action", action_name, form_data)

            # Clear previous parameters
            action_params_container.clear()

            # Get the current template name from form data
            current_template = (
                form_data.get("actions", [{}])[action_index]
                .get("config", {})
                .get("template_name")
            )

            # Create action-specific parameters
            if current_template and action_name:
                create_template_action_params(
                    current_template,
                    action_name,
                    action_index,
                    form_data,
                    action_params_container,
                    initial_config,
                )

        # Initialize with existing data if present
        if initial_template and initial_action:
            create_template_action_params(
                initial_template,
                initial_action,
                action_index,
                form_data,
                action_params_container,
                initial_config,
            )

    except Exception as e:
        logger.error(f"Error creating template control config: {e}")
        ui.label("Error loading template actions").classes("text-red-400")


def create_template_action_params(
    template_name: str,
    action_name: str,
    action_index: int,
    form_data: dict,
    container,
    initial_config: dict = None,
):
    """Create template-specific action parameters UI from JSON configuration"""
    if initial_config is None:
        initial_config = {}

    logger.debug(f"Creating template action params for {template_name}.{action_name}")

    with container:
        try:
            # Get the connector integration to access template configs
            integration = connector_integration.get_integration()
            template_actions = integration.get_available_template_actions()

            # Get the specific action configuration
            if (
                template_name in template_actions
                and action_name in template_actions[template_name]
            ):
                action_config = template_actions[template_name][action_name]
                elements = action_config.get("elements", [])

                if not elements:
                    ui.label("No additional configuration needed").classes(
                        "text-sm secondary-text"
                    )
                    return

                # Generate UI elements from JSON configuration
                for element in elements:
                    create_element_from_config(
                        element, action_index, form_data, initial_config
                    )
            else:
                ui.label("Action configuration not found").classes(
                    "text-sm text-red-400"
                )

        except Exception as e:
            logger.error(f"Error creating template action params: {e}", exc_info=True)
            ui.label("Error loading action configuration").classes(
                "text-sm text-red-400"
            )


def create_element_from_config(
    element_config: dict, action_index: int, form_data: dict, initial_config: dict
):
    """Create a UI element from JSON configuration"""
    element_type = element_config.get("type")
    element_id = element_config.get("id")
    label = element_config.get("label", "")
    description = element_config.get("description", "")
    value = initial_config.get(element_id, element_config.get("value"))

    if element_type == "text":
        form_input(
        tooltip=label,
            label=label,
            placeholder=element_config.get("placeholder", ""),
            value=str(value) if value is not None else "",
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2 action-input")

    elif element_type == "number":
        min_val = element_config.get("min", 0)
        max_val = element_config.get("max", 1000)
        form_input(
        tooltip=label,
            label=label,
            placeholder=str(value) if value is not None else "",
            value=str(value) if value is not None else "",
            validation={"min": min_val, "max": max_val},
            on_change=lambda e, eid=element_id: update_action_config(
                action_index,
                eid,
                int(e.value)
                if e.value and e.value.lstrip("-").isdigit()
                else element_config.get("value", 0),
                form_data,
            ),
        ).classes("w-full mb-2 action-input")

    elif element_type == "select":
        options = element_config.get("options", [])
        if isinstance(options, list):
            # Convert list to dict for NiceGUI select
            option_dict = {str(opt): str(opt).title() for opt in options}
        else:
            option_dict = options

        form_select(
        tooltip=label,
            options=option_dict,
            label=label,
            value=str(value) if value is not None else "",
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2 action-select")

    elif element_type == "switch":
        ui.switch(
            text=label,
            value=bool(value) if value is not None else False,
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2")

    elif element_type == "separator":
        ui.label(label).classes("text-base font-medium secondary-text mb-2 mt-4")

    else:
        logger.warning(f"Unknown element type: {element_type}")
        ui.label(f"Unsupported element type: {element_type}").classes(
            "text-sm text-yellow-400"
        )

    # Add description if provided
    if description:
        ui.label(description).classes("text-xs muted-text mb-2")


def create_websocket_emit_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for WebSocket emit action"""
    if initial_config is None:
        initial_config = {}

    form_input(
        tooltip="Event Name",
        label="Event Name",
        placeholder="custom_event",
        value=initial_config.get("event_name", ""),
        on_change=lambda e: update_action_config(
            action_index, "event_name", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Event Data (JSON)",
        placeholder='{"key": "value", "message": "{{username}} triggered this!"}',
        value=initial_config.get("event_data", ""),
        on_change=lambda e: update_action_config(
            action_index, "event_data", e.value, form_data
        ),
    ).classes("w-full action-input")


def create_send_announcement_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for Twitch chat announcement action."""
    if initial_config is None:
        initial_config = {}

    ui.textarea(
        label="Announcement text",
        placeholder="Thanks {{username}}!",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full action-input mb-2")

    form_select(
        tooltip="Announcement color",
        options={
            "primary": "Primary",
            "blue": "Blue",
            "green": "Green",
            "orange": "Orange",
            "purple": "Purple",
        },
        label="Color",
        value=initial_config.get("color", "primary"),
        on_change=lambda e: update_action_config(
            action_index, "color", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    ui.label("Requires chatbot with moderator:manage:announcements scope.").classes(
        "text-xs muted-text"
    )


def create_chat_message_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for chat message action"""
    if initial_config is None:
        initial_config = {}

    ui.textarea(
        label="Message",
        placeholder="Thanks {{username}} for the {{amount}} bits!",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full action-input")

    # Default: both platforms for new actions; existing missing field → twitch only
    raw_targets = initial_config.get("reply_targets")
    if raw_targets is None and not initial_config.get("message"):
        current_targets = ["twitch", "youtube"]
    elif raw_targets is None:
        current_targets = ["twitch"]
    else:
        current_targets = list(raw_targets) if isinstance(raw_targets, list) else ["twitch"]
    if not current_targets:
        current_targets = ["twitch"]
    update_action_config(action_index, "reply_targets", current_targets, form_data)

    ui.label("Send message to").classes("text-sm font-medium text-theme-muted mt-2")

    def _toggle_reply_target(platform: str, enabled: bool) -> None:
        actions = form_data.get("actions") or []
        if action_index >= len(actions):
            return
        cfg = actions[action_index].setdefault("config", {})
        targets = list(cfg.get("reply_targets") or [])
        if enabled and platform not in targets:
            targets.append(platform)
        if not enabled and platform in targets:
            targets = [t for t in targets if t != platform]
        if not targets:
            targets = ["twitch"]
        update_action_config(action_index, "reply_targets", targets, form_data)

    with ui.row().classes("items-center gap-4 w-full mb-2"):
        ui.checkbox(
            text="Twitch",
            value="twitch" in current_targets,
            on_change=lambda e: _toggle_reply_target("twitch", bool(e.value)),
        )
        ui.checkbox(
            text="YouTube",
            value="youtube" in current_targets,
            on_change=lambda e: _toggle_reply_target("youtube", bool(e.value)),
        )

    if action_index > 0:
        aparts: List[str] = []
        for j in range(1, action_index + 1):
            aparts.append(
                f"{{action{j}.item_name}}  {{action{j}.quantity}}  "
                f"{{action{j}.resolved_name}}  {{action{j}.error}}"
            )
        ui.label("Prior action outputs: " + "  ".join(aparts)).classes(
            "text-xs muted-text"
        )
    ui.label("Use single-brace placeholders for event and connector data").classes(
        "text-xs muted-text"
    )


def create_discord_message_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for send Discord message action."""
    if initial_config is None:
        initial_config = {}

    from .. import discord_service
    from ..ui_settings_layout import THEME_CHIP_CLASSES, theme_chip_row

    ui.textarea(
        label="Message",
        placeholder="Thanks {username}! Stream updates in Discord.",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full action-input")

    channels = list(initial_config.get("channels") or [])
    if not isinstance(channels, list):
        channels = []
    update_action_config(action_index, "channels", channels, form_data)

    ui.label("Discord channels").classes("text-sm font-medium text-theme-muted mt-2")
    chip_row = theme_chip_row()

    def _channel_key(entry: dict) -> str:
        return f"{entry.get('guild_id')}:{entry.get('channel_id')}"

    def _persist(updated: list) -> None:
        update_action_config(action_index, "channels", updated, form_data)

    def _rebuild_chips() -> None:
        chip_row.clear()
        actions = form_data.get("actions") or []
        if action_index >= len(actions):
            return
        current = list(actions[action_index].get("config", {}).get("channels") or [])
        for entry in current:
            if not isinstance(entry, dict):
                continue
            guild_name = entry.get("guild_name") or entry.get("guild_id") or "?"
            channel_name = entry.get("channel_name") or entry.get("channel_id") or "?"
            label = f"{guild_name} / #{channel_name}"
            key = _channel_key(entry)
            with chip_row:
                with (
                    ui.element("div")
                    .classes(THEME_CHIP_CLASSES)
                    .style("white-space: nowrap;")
                ):
                    ui.label(label).classes("text-sm").style("white-space: nowrap;")
                    ui.button(
                        icon="close",
                        on_click=lambda _e, k=key: _remove(k),
                    ).props("flat dense round size=xs")

    def _remove(key: str) -> None:
        actions = form_data.get("actions") or []
        if action_index >= len(actions):
            return
        current = [
            e
            for e in (actions[action_index].get("config", {}).get("channels") or [])
            if isinstance(e, dict) and _channel_key(e) != key
        ]
        _persist(current)
        _rebuild_chips()

    guild_options: Dict[str, str] = {}
    channel_options: Dict[str, str] = {}
    try:
        if discord_service.discord_service.is_connected():
            for g in discord_service.list_guilds():
                guild_options[g["id"]] = g.get("name") or g["id"]
    except Exception:
        pass

    guild_select = ui.select(
        options=guild_options,
        label="Server",
        with_input=True,
    ).classes("w-full mb-2 action-select")

    channel_select = ui.select(
        options={},
        label="Channel",
        with_input=True,
    ).classes("w-full mb-2 action-select")

    def _on_guild(e) -> None:
        nonlocal channel_options
        gid = str(getattr(e, "value", None) or "").strip()
        channel_options = {}
        if gid:
            try:
                for c in discord_service.list_text_channels(gid):
                    channel_options[c["id"]] = f"#{c.get('name') or c['id']}"
            except Exception:
                pass
        channel_select.set_options(channel_options)
        channel_select.value = None

    def _add() -> None:
        guild_id = str(guild_select.value or "").strip()
        channel_id = str(channel_select.value or "").strip()
        if not guild_id or not channel_id:
            notify("Select a Discord server and channel", type="warning")
            return
        guild_name = guild_options.get(guild_id, guild_id)
        ch_label = channel_options.get(channel_id, channel_id)
        channel_name = str(ch_label).lstrip("#")
        entry = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "guild_name": guild_name,
            "channel_name": channel_name,
        }
        actions = form_data.get("actions") or []
        if action_index >= len(actions):
            return
        current = list(actions[action_index].get("config", {}).get("channels") or [])
        key = _channel_key(entry)
        if any(isinstance(e, dict) and _channel_key(e) == key for e in current):
            notify("Channel already added", type="warning")
            return
        current.append(entry)
        _persist(current)
        _rebuild_chips()

    guild_select.on_value_change(_on_guild)

    with ui.row().classes("w-full gap-2 mb-2"):
        outline_button("Add channel", _add, icon="add")
        if not guild_options:
            ui.label(
                "Connect Discord in Settings to list servers/channels."
            ).classes("text-xs muted-text")

    _rebuild_chips()

    if action_index > 0:
        aparts: List[str] = []
        for j in range(1, action_index + 1):
            aparts.append(
                f"{{action{j}.item_name}}  {{action{j}.quantity}}  "
                f"{{action{j}.resolved_name}}  {{action{j}.error}}"
            )
        ui.label("Prior action outputs: " + "  ".join(aparts)).classes(
            "text-xs muted-text"
        )
    ui.label("Use single-brace placeholders for event and connector data").classes(
        "text-xs muted-text"
    )


def create_write_file_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for write file action"""
    if initial_config is None:
        initial_config = {}

    form_input(
        tooltip="File Path",
        label="File Path",
        placeholder="logs/{{event_type}}.log",
        value=initial_config.get("file_path", ""),
        on_change=lambda e: update_action_config(
            action_index, "file_path", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Content",
        placeholder="{{timestamp}}: {{username}} - {{message}}",
        value=initial_config.get("content", ""),
        on_change=lambda e: update_action_config(
            action_index, "content", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Append to file",
        value=initial_config.get("append", False),
        on_change=lambda e: update_action_config(
            action_index, "append", e.value, form_data
        ),
    ).classes("w-full")


def create_add_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for add greeting action"""
    if initial_config is None:
        initial_config = {}

    form_input(
        tooltip="User ID",
        label="User ID",
        placeholder="{{user_id}}",
        value=initial_config.get("user_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "user_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    form_input(
        tooltip="Username",
        label="Username",
        placeholder="{{username}}",
        value=initial_config.get("username", ""),
        on_change=lambda e: update_action_config(
            action_index, "username", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Greeting Text",
        placeholder="Welcome to the stream, {{username}}!",
        value=initial_config.get("greeting_text", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_text", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Enabled",
        value=initial_config.get("enabled", True),
        on_change=lambda e: update_action_config(
            action_index, "enabled", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_update_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for update greeting action"""
    if initial_config is None:
        initial_config = {}

    form_input(
        tooltip="Greeting ID",
        label="Greeting ID",
        placeholder="{{greeting_id}}",
        value=initial_config.get("greeting_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="New Greeting Text (optional)",
        placeholder="Updated greeting for {{username}}",
        value=initial_config.get("greeting_text", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_text", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Enabled",
        value=initial_config.get("enabled", None),
        on_change=lambda e: update_action_config(
            action_index, "enabled", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Leave greeting text empty to only update enabled status").classes(
        "text-xs muted-text"
    )


def create_send_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for send greeting action"""
    if initial_config is None:
        initial_config = {}

    form_input(
        tooltip="User ID",
        label="User ID",
        placeholder="{{user_id}}",
        value=initial_config.get("user_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "user_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    form_input(
        tooltip="Username",
        label="Username",
        placeholder="{{username}}",
        value=initial_config.get("username", ""),
        on_change=lambda e: update_action_config(
            action_index, "username", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Force Send (ignore greeting cooldown)",
        value=initial_config.get("force_send", True),
        on_change=lambda e: update_action_config(
            action_index, "force_send", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_trigger_alert_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for trigger alert action"""
    if initial_config is None:
        initial_config = {}

    # Alert type selection
    alert_types = {
        "follow": "Follow Alert",
        "subscription": "Subscription Alert",
        "bits": "Bits Alert",
        "donation": "Donation Alert",
        "raid": "Raid Alert",
        "host": "Host Alert",
        "custom": "Custom Alert",
    }

    form_select(
        tooltip="Alert Type",
        options=alert_types,
        label="Alert Type",
        value=initial_config.get("alert_type")
        if initial_config.get("alert_type")
        else None,
        on_change=lambda e: update_action_config(
            action_index, "alert_type", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Quantity/amount
    form_input(
        tooltip="Amount/Quantity",
        label="Amount/Quantity",
        placeholder="e.g., 100 (for bits), 1 (for follows)",
        value=str(
            initial_config.get("amount", "")
            if initial_config.get("amount") is not None
            else ""
        ),
        on_change=lambda e: update_action_config(
            action_index, "amount", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Optional custom message
    form_input(
        tooltip="Custom Message (optional)",
        label="Custom Message (optional)",
        placeholder="Custom alert message",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label("Configure the type and amount for the alert to trigger").classes(
        "text-xs muted-text"
    )


def create_api_call_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for API call action"""
    if initial_config is None:
        initial_config = {}

    # HTTP Method
    http_methods = {
        "GET": "GET",
        "POST": "POST",
        "PUT": "PUT",
        "DELETE": "DELETE",
        "PATCH": "PATCH",
    }

    form_select(
        tooltip="HTTP Method",
        options=http_methods,
        label="HTTP Method",
        value=initial_config.get("method") or "GET",
        on_change=lambda e: update_action_config(
            action_index, "method", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # URL
    form_input(
        tooltip="URL",
        label="URL",
        placeholder="https://api.example.com/endpoint",
        value=initial_config.get("url", ""),
        on_change=lambda e: update_action_config(
            action_index, "url", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Headers
    ui.textarea(
        label="Headers (JSON)",
        placeholder='{"Content-Type": "application/json", "Authorization": "Bearer {{token}}"}',
        value=initial_config.get("headers", ""),
        on_change=lambda e: update_action_config(
            action_index, "headers", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Request Body
    ui.textarea(
        label="Request Body (JSON)",
        placeholder='{"message": "{{username}} triggered this!", "amount": {{amount}}}',
        value=initial_config.get("body", ""),
        on_change=lambda e: update_action_config(
            action_index, "body", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_execute_command_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for execute command action"""
    if initial_config is None:
        initial_config = {}

    # Command to execute
    form_input(
        tooltip="Command",
        label="Command",
        placeholder="echo 'Hello {{username}}!'",
        value=initial_config.get("command", ""),
        on_change=lambda e: update_action_config(
            action_index, "command", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Working directory (optional)
    form_input(
        tooltip="Working Directory (optional)",
        label="Working Directory (optional)",
        placeholder="/path/to/directory",
        value=initial_config.get("working_directory", ""),
        on_change=lambda e: update_action_config(
            action_index, "working_directory", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Timeout
    form_input(
        tooltip="Timeout (seconds)",
        label="Timeout (seconds)",
        placeholder="30",
        value=str(initial_config.get("timeout", 30)),
        on_change=lambda e: update_action_config(
            action_index, "timeout", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Use {field} placeholders to insert event data. Be careful with command execution!"
    ).classes("text-xs muted-text")


def update_action_config(action_index: int, key: str, value: Any, form_data: dict):
    """Update action configuration"""
    if "actions" not in form_data:
        form_data["actions"] = []
    if action_index >= len(form_data["actions"]):
        return
    if "config" not in form_data["actions"][action_index]:
        form_data["actions"][action_index]["config"] = {}

    form_data["actions"][action_index]["config"][key] = value


def update_action_delay(index: int, value: Any, form_data: dict):
    """Update per-action delay (seconds after previous enabled action)."""
    if "actions" not in form_data or index >= len(form_data["actions"]):
        return
    try:
        form_data["actions"][index]["delay_seconds"] = float(value or 0)
    except (TypeError, ValueError):
        form_data["actions"][index]["delay_seconds"] = 0


def _condition_operator_key(cond_data: dict) -> Optional[str]:
    """Operator key for save; UI defaults to equal when unset."""
    op = cond_data.get("operator")
    if op in (None, ""):
        return "equal"
    return str(op)


def update_condition_field(index: int, field: str, form_data: dict):
    """Update condition field"""
    if form_data.get("_rebuilding_conditions"):
        return
    if "trigger_conditions" not in form_data or index >= len(
        form_data["trigger_conditions"]
    ):
        return
    cond = form_data["trigger_conditions"][index]
    old_field = cond.get("field")
    cond["field"] = field
    if not cond.get("operator"):
        cond["operator"] = "equal"
    if _is_boolean_condition_field(field):
        cond["operator"] = "equal"
        cond["value"] = "true"
    if _is_boolean_condition_field(old_field) != _is_boolean_condition_field(field):
        _rebuild_all_conditions(form_data)
    else:
        _call_game_hook_hint_refresh(form_data)


def update_condition_operator(index: int, operator: str, form_data: dict):
    """Update condition operator"""
    if form_data.get("_rebuilding_conditions"):
        return
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"][index]["operator"] = operator
    _call_game_hook_hint_refresh(form_data)


def update_condition_value(index: int, value: str, form_data: dict):
    """Update condition value"""
    if form_data.get("_rebuilding_conditions"):
        return
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"][index]["value"] = value
    _call_game_hook_hint_refresh(form_data)


def remove_condition(index: int, form_data: dict, conditions_list):
    """Remove a condition"""
    if "trigger_conditions" not in form_data or index >= len(
        form_data["trigger_conditions"]
    ):
        return
    form_data["trigger_conditions"].pop(index)
    _normalize_form_select_values(form_data)
    # Snapshot before clear(); destroyed widgets may fire on_change and wipe form_data.
    conditions_snapshot = [
        {
            "field": c.get("field"),
            "operator": c.get("operator"),
            "value": c.get("value", ""),
        }
        for c in form_data["trigger_conditions"]
    ]
    conditions_list.clear()
    form_data["trigger_conditions"] = conditions_snapshot
    trigger_type = form_data.get("trigger_type")
    if trigger_type:
        for i, condition_data in enumerate(conditions_snapshot):
            add_condition_to_trigger_with_data_and_index(
                trigger_type, form_data, conditions_list, condition_data, i
            )
    _call_game_hook_hint_refresh(form_data)


def remove_action(index: int, form_data: dict, actions_container):
    """Remove an action"""
    if "actions" not in form_data or index >= len(form_data["actions"]):
        return
    form_data["actions"].pop(index)
    _normalize_form_select_values(form_data)
    # Snapshot before clear(); destroyed widgets may fire on_change and wipe form_data.
    actions_snapshot = [
        {
            "type": a.get("type"),
            "config": dict(a.get("config") or {}),
            "delay_seconds": float(a.get("delay_seconds", 0) or 0),
        }
        for a in form_data["actions"]
    ]
    actions_container.clear()
    form_data["actions"] = actions_snapshot
    for i, action_data in enumerate(actions_snapshot):
        add_action_to_form_with_data_and_index(
            form_data, actions_container, action_data, i
        )


def close_dialog_and_refresh():
    """Close the create dialog and refresh the connectors list"""
    global create_dialog, current_search
    try:
        logger.info(
            f"Closing dialog and refreshing connectors - create_dialog exists: {create_dialog is not None}"
        )
        if create_dialog:
            logger.info("Attempting to close dialog...")
            create_dialog.close()
            logger.info("Dialog close() called successfully")
        else:
            logger.warning("create_dialog is None, cannot close")

        logger.info("Refreshing connectors...")
        refresh_connectors()
        logger.info("Successfully closed dialog and refreshed")
    except Exception as e:
        logger.error(f"Error closing dialog and refreshing: {e}", exc_info=True)


def save_connector(form_data: dict):
    """Save the connector (create new or update existing)"""
    is_edit = form_data.get("connector_id") is not None

    if is_edit:
        save_updated_connector(form_data)
    else:
        save_new_connector(form_data)


def _create_action_from_form_data(
    action_data: dict, action_label: str
):
    """Build a connector action from form row data."""
    action_type = ActionType(action_data["type"])
    action_id = str(uuid.uuid4())
    action_config = action_data.get("config", {})
    delay_seconds = float(action_data.get("delay_seconds", 0) or 0)
    common = {
        "action_type": action_type,
        "action_id": action_id,
        "name": action_label,
        "delay_seconds": delay_seconds,
    }

    if action_type == ActionType.TEMPLATE_CONTROL:
        control_data = {}
        template_name = action_config.get("template_name", "")
        control_action = action_config.get("control_action", "")
        for key, value in action_config.items():
            if key not in ["template_name", "control_action"]:
                control_data[key] = value
        return connector_actions.create_action(
            **common,
            template_name=template_name,
            control_action=control_action,
            control_data=control_data,
        )
    if action_type == ActionType.GAME_HOOK:
        from ..game_hooks.ff7_hook import ff7_connector_config_to_hook_kwargs

        hook_args = ff7_connector_config_to_hook_kwargs(action_config)
        return connector_actions.create_action(
            **common,
            game_id=action_config.get("game_id", "ff7"),
            operation=action_config.get("operation", ""),
            hook_arguments=hook_args,
        )
    if action_type == ActionType.OBS_CONTROL:
        obs_arguments: Dict[str, Any] = {}
        for k, v in action_config.items():
            if k.startswith("arg_"):
                obs_arguments[k[4:]] = v
        return connector_actions.create_action(
            **common,
            operation=str(action_config.get("operation", "") or ""),
            obs_arguments=obs_arguments,
        )
    return connector_actions.create_action(**common, **action_config)


def save_new_connector(form_data: dict):
    """Save the new connector"""
    try:
        # Validate form data
        if not form_data.get("name"):
            notify("Connector name is required", type="negative")
            return

        if not form_data.get("trigger_type"):
            notify("Trigger type is required", type="negative")
            return

        if not form_data.get("actions"):
            notify("At least one action is required", type="negative")
            return

        # Create trigger
        logger.info(f"Creating trigger of type: {form_data['trigger_type']}")
        trigger_type = TriggerType(form_data["trigger_type"])
        trigger_id = str(uuid.uuid4())
        connector_id = str(uuid.uuid4())
        logger.info(f"Generated trigger_id: {trigger_id}")

        # Create conditions
        conditions = []
        for cond_data in form_data.get("trigger_conditions", []):
            operator_key = _condition_operator_key(cond_data)
            if cond_data.get("field") and operator_key:
                try:
                    operator = ComparisonOperator(operator_key)
                    condition = TriggerCondition(
                        field=cond_data["field"],
                        operator=operator,
                        value=cond_data["value"],
                    )
                    conditions.append(condition)
                except Exception as e:
                    logger.warning(f"Invalid condition: {e}")

        # Create trigger with trigger-specific configuration
        logger.info(f"About to create trigger with conditions: {conditions}")
        trigger_params = {
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "name": f"{form_data['name']} Trigger",
            "conditions": conditions,
        }

        # Add trigger-specific configuration
        if trigger_type == TriggerType.HOTKEY:
            trigger_config = form_data.get("trigger_config", {})
            trigger_params.update(
                {
                    "key_combination": trigger_config.get("key_combination", ""),
                    "is_global": trigger_config.get("is_global", True),
                }
            )
        elif trigger_type == TriggerType.STREAMDECK:
            trigger_params["connector_id"] = connector_id

        trigger = connector_triggers.create_trigger(**trigger_params)
        logger.info(f"Successfully created trigger: {trigger}")

        # Create actions
        logger.info(f"Creating {len(form_data.get('actions', []))} actions")
        actions = []
        for i, action_data in enumerate(form_data.get("actions", [])):
            logger.info(f"Creating action {i+1}: {action_data}")
            logger.info(
                f"Action config for action {i+1}: {action_data.get('config', {})}"
            )
            action = _create_action_from_form_data(
                action_data, f"{form_data['name']} Action {i+1}"
            )
            logger.info(f"Successfully created action {i+1}: {action}")
            actions.append(action)

        # Create connector
        logger.info("Creating final Connector object")
        connector = Connector(
            connector_id=connector_id,
            name=form_data["name"],
            description=form_data.get("description", ""),
            trigger=trigger,
            actions=actions,
        )
        logger.info(f"Successfully created Connector: {connector}")

        # Save connector
        logger.info("Getting connector manager and saving")
        manager = connector_manager.get_manager()
        success = manager.add_connector(connector)
        logger.info(f"Save result: {success}")

        if success:
            notify(
                f"Connector '{connector.name}' created successfully", type="positive"
            )
            # Direct execution for testing
            logger.info("About to close dialog and refresh")
            close_dialog_and_refresh()
            logger.info("Finished close dialog and refresh")
        else:
            notify("Failed to create connector", type="negative")

    except Exception as e:
        logger.error(f"Error saving connector: {e}", exc_info=True)
        notify(f"Error creating connector: {str(e)}", type="negative")


def save_updated_connector(form_data: dict):
    """Save the updated connector"""
    try:
        connector_id = form_data.get("connector_id")
        if not connector_id:
            notify("Invalid connector ID", type="negative")
            return

        # Validate form data
        if not form_data.get("name"):
            notify("Connector name is required", type="negative")
            return

        if not form_data.get("trigger_type"):
            notify("Trigger type is required", type="negative")
            return

        if not form_data.get("actions"):
            notify("At least one action is required", type="negative")
            return

        # Get the manager and existing connector
        manager = connector_manager.get_manager()
        existing_connector = manager.get_connector(connector_id)

        if not existing_connector:
            notify("Connector not found", type="negative")
            return

        # Create updated trigger
        logger.info(f"Updating trigger of type: {form_data['trigger_type']}")
        trigger_type = TriggerType(form_data["trigger_type"])
        trigger_id = (
            existing_connector.trigger.trigger_id
            if existing_connector.trigger
            else str(uuid.uuid4())
        )

        # Create conditions
        conditions = []
        for cond_data in form_data.get("trigger_conditions", []):
            operator_key = _condition_operator_key(cond_data)
            if cond_data.get("field") and operator_key:
                try:
                    operator = ComparisonOperator(operator_key)
                    condition = TriggerCondition(
                        field=cond_data["field"],
                        operator=operator,
                        value=cond_data["value"],
                    )
                    conditions.append(condition)
                except Exception as e:
                    logger.warning(f"Invalid condition: {e}")

        # Create updated trigger with trigger-specific configuration
        trigger_params = {
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "name": f"{form_data['name']} Trigger",
            "conditions": conditions,
        }

        # Add trigger-specific configuration
        if trigger_type == TriggerType.HOTKEY:
            trigger_config = form_data.get("trigger_config", {})
            trigger_params.update(
                {
                    "key_combination": trigger_config.get("key_combination", ""),
                    "is_global": trigger_config.get("is_global", True),
                }
            )
        elif trigger_type == TriggerType.STREAMDECK:
            trigger_params["connector_id"] = connector_id

        trigger = connector_triggers.create_trigger(**trigger_params)

        # Create updated actions
        actions = []
        for i, action_data in enumerate(form_data.get("actions", [])):
            action = _create_action_from_form_data(
                action_data, f"{form_data['name']} Action {i+1}"
            )
            actions.append(action)

        # Create updated connector
        updated_connector = Connector(
            connector_id=connector_id,
            name=form_data["name"],
            description=form_data.get("description", ""),
            trigger=trigger,
            actions=actions,
            enabled=existing_connector.enabled,  # Preserve enabled state
            trigger_count=existing_connector.trigger_count,  # Preserve statistics
            last_triggered=existing_connector.last_triggered,
        )

        # Update connector
        success = manager.update_connector(connector_id, updated_connector)

        if success:
            notify(
                f"Connector '{updated_connector.name}' updated successfully",
                type="positive",
            )
            close_dialog_and_refresh()
        else:
            notify("Failed to update connector", type="negative")

    except Exception as e:
        logger.error(f"Error updating connector: {e}", exc_info=True)
        notify(f"Error updating connector: {str(e)}", type="negative")


def show_edit_connector_dialog(connector_id: str):
    """Show the edit connector dialog"""
    show_connector_dialog(connector_id)


def test_connector(connector_id: str):
    """Test a connector with sample data"""
    try:
        manager = connector_manager.get_manager()
        connector = manager.get_connector(connector_id)

        if not connector:
            notify("Connector not found", type="negative")
            return

        # Create sample test data based on trigger type
        test_data = create_test_data_for_trigger(connector.trigger.trigger_type)
        if connector.trigger.trigger_type == TriggerType.STREAMDECK:
            test_data["connector_id"] = connector.connector_id

        # Test the connector
        async def run_test():
            result = await manager.test_connector(connector_id, test_data)
            if result.get("success"):
                if result.get("triggered"):
                    notify(
                        f"Test successful: Connector triggered and executed {result.get('action_count', 0)} actions",
                        type="positive",
                    )
                else:
                    notify(
                        "Test completed: Connector did not trigger (conditions not met)",
                        type="info",
                    )
            else:
                notify(
                    f"Test failed: {result.get('error', 'Unknown error')}",
                    type="negative",
                )

        background_tasks.create(run_test(), name="connector_test")

    except Exception as e:
        logger.error(f"Error testing connector: {e}", exc_info=True)
        notify(f"Error testing connector: {str(e)}", type="negative")


def create_test_data_for_trigger(trigger_type: TriggerType) -> Dict[str, Any]:
    """Create sample test data for a trigger type"""
    base_data = {"timestamp": time.time(), "username": "TestUser", "source": "test"}

    if trigger_type == TriggerType.TWITCH_BITS:
        return {
            **base_data,
            "event_type": "twitch_bits",
            "amount": 100,
            "message": "Test bits!",
        }
    elif trigger_type == TriggerType.TWITCH_SUB:
        return {
            **base_data,
            "event_type": "twitch_sub",
            "tier": 1,
            "months": 1,
            "message": "Test sub!",
        }
    elif trigger_type == TriggerType.TWITCH_FOLLOW:
        return {**base_data, "event_type": "twitch_follow"}
    elif trigger_type == TriggerType.TWITCH_CHAT_MESSAGE:
        return {
            **base_data,
            "event_type": "twitch_chat_message",
            "message": "Hello test!",
            "user_id": "12345",
            "is_moderator": False,
        }
    elif trigger_type == TriggerType.YOUTUBE_CHAT_MESSAGE:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_chat_message",
            "message": "Hello from YouTube!",
            "user_id": "UCtest",
        }
    elif trigger_type == TriggerType.YOUTUBE_MEMBER:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_member",
            "member_level": "Bronze",
            "message": "Welcome!",
        }
    elif trigger_type == TriggerType.YOUTUBE_MEMBER_MILESTONE:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_member_milestone",
            "months": 6,
            "member_level": "Silver",
            "message": "6 months!",
        }
    elif trigger_type == TriggerType.YOUTUBE_GIFT_MEMBERSHIP:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_gift_membership",
            "gift_count": 5,
            "quantity": 5,
            "member_level": "Bronze",
        }
    elif trigger_type == TriggerType.YOUTUBE_SUPERCHAT:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_superchat",
            "amount": 5.0,
            "currency": "USD",
            "display_amount": "$5.00",
            "message": "Test Super Chat!",
        }
    elif trigger_type == TriggerType.YOUTUBE_SUPERSTICKER:
        return {
            **base_data,
            "source": "youtube",
            "event_type": "youtube_supersticker",
            "amount": 2.0,
            "currency": "USD",
            "display_amount": "$2.00",
            "message": "Sticker",
        }
    elif trigger_type == TriggerType.ANY:
        return {
            **base_data,
            "event_type": "twitch_chat_message",
            "message": "Hello test!",
            "username": "TestUser",
            "amount": 100,
            "is_moderator": False,
        }

    elif trigger_type == TriggerType.DONATION:
        return {
            **base_data,
            "event_type": "donation",
            "amount": 5.0,
            "currency": "USD",
            "message": "Test donation!",
        }
    elif trigger_type == TriggerType.HOTKEY:
        return {
            **base_data,
            "event_type": "hotkey",
            "key_code": "f12",
            "modifiers": ["ctrl"],
            "is_global": True,
        }
    elif trigger_type == TriggerType.STREAMDECK:
        return {
            **base_data,
            "event_type": "streamdeck",
            "connector_id": "",
            "source": "streamdeck",
        }
    elif trigger_type == TriggerType.OBS_SCENE_CHANGED:
        return {
            **base_data,
            "source": "obs",
            "event_type": "obs_scene_changed",
            "scene_name": "Test Scene",
            "previous_scene_name": "",
        }
    elif trigger_type == TriggerType.OBS_STREAM_STATE:
        return {
            **base_data,
            "source": "obs",
            "event_type": "obs_stream_state",
            "output_active": False,
            "output_state": "OBS_WEBSOCKET_OUTPUT_STATE_STOPPED",
        }
    elif trigger_type == TriggerType.OBS_RECORD_STATE:
        return {
            **base_data,
            "source": "obs",
            "event_type": "obs_record_state",
            "output_active": False,
            "output_state": "OBS_WEBSOCKET_OUTPUT_STATE_STOPPED",
        }
    elif trigger_type == TriggerType.OBS_INPUT_MUTE:
        return {
            **base_data,
            "source": "obs",
            "event_type": "obs_input_mute",
            "input_name": "Mic/Aux",
            "input_muted": False,
        }
    else:
        return {**base_data, "event_type": "unknown"}


def toggle_connector(connector_id: str, enabled: bool):
    """Toggle a connector's enabled state"""
    try:
        manager = connector_manager.get_manager()
        success = manager.toggle_connector(connector_id)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Connector {status}", type="positive")
            refresh_connectors()
        else:
            notify("Failed to toggle connector", type="negative")
    except Exception as e:
        logger.error(f"Error toggling connector: {e}", exc_info=True)
        notify(f"Error toggling connector: {str(e)}", type="negative")


def delete_connector(connector_id: str):
    """Delete a connector with confirmation"""

    def confirm_delete():
        try:
            manager = connector_manager.get_manager()
            success = manager.remove_connector(connector_id)

            if success:
                notify("Connector deleted", type="positive")
                # Remove from card references before refreshing
                if connector_id in connector_cards:
                    del connector_cards[connector_id]
                refresh_connectors()
            else:
                notify("Failed to delete connector", type="negative")
        except Exception as e:
            logger.error(f"Error deleting connector: {e}", exc_info=True)
            notify(f"Error deleting connector: {str(e)}", type="negative")

    # Show confirmation dialog
    with ui.dialog().props("persistent") as dialog:
        with ui.card():
            ui.label("Confirm Delete").classes("text-lg font-semibold mb-2")
            ui.label(
                "Are you sure you want to delete this connector? This action cannot be undone."
            ).classes("mb-4")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete", on_click=lambda: [confirm_delete(), dialog.close()]
                ).classes("btn-danger")

    dialog.open()




def refresh_connectors():
    """Refresh the connectors display"""
    global current_search, connector_cards, folder_cards, connector_parent_folder, folder_tile_title_labels
    current_search = ""  # Clear search when refreshing
    connector_cards.clear()
    folder_cards.clear()
    connector_parent_folder.clear()
    folder_tile_title_labels.clear()
    if search_input:
        search_input.value = ""
    load_connectors()


def on_search_change(event):
    """Handle search input changes"""
    global current_search
    current_search = event.value.lower() if event.value else ""
    update_search_visibility()


def update_search_visibility():
    """Update connector visibility based on search term"""
    search_term = current_search.strip()

    visible_count = 0
    total_connectors = len(connector_cards)
    manager = connector_manager.get_manager()
    layout = connector_layout_store.load_layout()

    for folder_id, folder_el in folder_cards.items():
        try:
            spec = (layout.get("folders") or {}).get(folder_id) or {}
            member_ids = list(spec.get("connector_ids") or [])
            fname = (spec.get("name") or "").lower()

            st = _folder_floaters.get(folder_id)
            shell = st.get("shell") if st else None
            floater_open = shell is not None and not shell.is_deleted

            if not search_term:
                folder_el.classes(remove="hidden")
                if floater_open:
                    for cid in member_ids:
                        card_el = connector_cards.get(cid)
                        if card_el:
                            card_el.classes(remove="hidden")
                            visible_count += 1
                continue

            name_hit = search_term in fname
            matching = []
            for cid in member_ids:
                conn = manager.get_connector(cid)
                if conn and search_term in _connector_search_blob(conn):
                    matching.append(cid)
            match_set = set(matching)

            if name_hit or match_set:
                folder_el.classes(remove="hidden")
                if floater_open:
                    for cid in member_ids:
                        card_el = connector_cards.get(cid)
                        if not card_el:
                            continue
                        if name_hit or cid in match_set:
                            card_el.classes(remove="hidden")
                            visible_count += 1
                        else:
                            card_el.classes(add="hidden")
                else:
                    visible_count += 1
            else:
                folder_el.classes(add="hidden")
                if floater_open:
                    for cid in member_ids:
                        card_el = connector_cards.get(cid)
                        if card_el:
                            card_el.classes(add="hidden")
        except Exception as e:
            logger.error(f"Error updating visibility for folder {folder_id}: {e}")
            folder_el.classes(add="hidden")

    for connector_id, card_element in connector_cards.items():
        if connector_parent_folder.get(connector_id):
            continue
        try:
            connector = manager.get_connector(connector_id)

            if connector:
                searchable_text = _connector_search_blob(connector)

                if not search_term or search_term in searchable_text:
                    card_element.classes(remove="hidden")
                    visible_count += 1
                else:
                    card_element.classes(add="hidden")
            else:
                card_element.classes(add="hidden")
        except Exception as e:
            logger.error(f"Error updating visibility for connector {connector_id}: {e}")
            card_element.classes(add="hidden")

    # Update empty state visibility if we have a connectors container
    if connectors_container:
        # Find the empty state element among the container's children
        empty_state = None
        for child in connectors_container.default_slot.children:
            if hasattr(child, "classes") and "empty-state" in str(child.classes):
                empty_state = child
                break

        if empty_state:
            if total_connectors == 0:
                # No connectors at all
                empty_state.classes(remove="hidden")
                # Update the message label
                for child in empty_state.default_slot.children:
                    if hasattr(child, "text"):
                        child.text = "No connectors created yet"
                        break
            elif visible_count == 0 and search_term:
                # Search returned no results
                empty_state.classes(remove="hidden")
                # Update the message label
                for child in empty_state.default_slot.children:
                    if hasattr(child, "text"):
                        child.text = f"No connectors found matching '{search_term}'"
                        break
            else:
                # Hide empty state when there are visible connectors
                empty_state.classes(add="hidden")

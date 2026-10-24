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

import datetime
import html as html_lib
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from nicegui import context, ui
from ..notification_engine import notify
from ..ui_buttons import primary_button


from .. import (
    connector_layout_store,
    connector_manager,
)
from .connectors_styles import CONNECTORS_CSS
from .service_brand_icons import SERVICE_BRAND_SVG
from ..connector_core import (
    Connector,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Global references for UI state
connectors_container = None
search_input = None
current_search = ""
current_status = "all"  # All / On / Off filter
_filter_chips: Dict[str, Any] = {}
_bar_sub_label = None
_no_match_label = None
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


def create_connectors_tab():
    """Create the Connectors tab UI"""
    global connectors_container, _folder_dialog_host, _bar_sub_label, current_status
    from ..help_system.help_render import (
        inject_page_stylesheet,
        promote_color_importance,
        scope_selectors,
    )

    inject_page_stylesheet(
        "mycelian-connectors", promote_color_importance(scope_selectors(CONNECTORS_CSS, ".cnx"))
    )
    _install_drag_hooks()
    current_status = "all"

    with ui.element("div").classes("tab-surface cnx w-full h-full flex flex-col gap-3 p-4"):
        with ui.element("div").classes("cx-bar"):
            ui.html(
                '<span class="cx-bar-icon"><span class="cx-icon">hub</span></span>', sanitize=False
            ).classes("contents")
            with ui.element("div"):
                ui.label("Connectors").classes("cx-bar-title")
                _bar_sub_label = ui.label("When something happens, do something.").classes("cx-bar-sub")
            ui.element("div").classes("cx-spacer")
            global search_input
            search_input = (
                ui.input(placeholder="Search name, trigger or action", on_change=on_search_change)
                .props("dense outlined clearable")
                .classes("cx-search")
            )
            with search_input.add_slot("prepend"):
                ui.icon("search").classes("text-base")
            search_input.tooltip("Filter connectors by name, description, trigger or action")
            with ui.element("div").classes("cx-filters"):
                for key, label in (("all", "All"), ("on", "On"), ("off", "Off")):
                    chip = ui.element("div").on("click", lambda _e, k=key: _set_status_filter(k))
                    chip.classes("cx-filter is-on" if key == "all" else "cx-filter")
                    with chip:
                        ui.label(label)
                        count = ui.html("<b>0</b>", sanitize=False).classes("contents")
                    _filter_chips[key] = (chip, count)
            refresh_btn = ui.button(icon="refresh", on_click=refresh_connectors).props("outline dense color=primary")
            refresh_btn.classes("px-2").tooltip("Reload connectors")
            primary_button("New connector", show_create_connector_dialog, icon="add").props("dense").classes("px-4")

        with ui.element("div").classes("grow overflow-hidden relative"):
            with ui.scroll_area().classes("w-full h-full"):
                connectors_container = ui.element("div").classes("w-full pb-2")

            _ensure_folder_dialog_host()

        load_connectors()
        update_search_visibility()


def _install_drag_hooks() -> None:
    """Show the "move out of folder" bar only while a card from a folder is being dragged."""
    ui.run_javascript(
        """
        if (!window.__mycelianCxDrag) {
          window.__mycelianCxDrag = true;
          document.addEventListener('dragstart', (e) => {
            const card = e.target && e.target.closest && e.target.closest('[data-connector-id]');
            if (card && card.hasAttribute('data-folder-parent')) document.body.classList.add('cx-dragging');
          }, true);
          const clear = () => document.body.classList.remove('cx-dragging');
          document.addEventListener('dragend', clear, true);
          document.addEventListener('drop', () => setTimeout(clear, 0), true);
        }
        """
    )


def _set_status_filter(key: str) -> None:
    global current_status
    current_status = key
    for k, (chip, _count) in _filter_chips.items():
        if chip.is_deleted:
            continue
        if k == key:
            chip.classes(add="is-on")
        else:
            chip.classes(remove="is-on")
    update_search_visibility()


def _update_toolbar_counts(connectors: Dict[str, "Connector"], folder_count: int) -> None:
    total = len(connectors)
    on = sum(1 for c in connectors.values() if c.enabled)
    for key, value in (("all", total), ("on", on), ("off", total - on)):
        pair = _filter_chips.get(key)
        if pair and not pair[1].is_deleted:
            pair[1].set_content(f"<b>{value}</b>")
    if _bar_sub_label is not None and not _bar_sub_label.is_deleted:
        text = f"When something happens, do something. {total} connector{'s' if total != 1 else ''}"
        if folder_count:
            text += f" in {folder_count} folder{'s' if folder_count != 1 else ''}"
        _bar_sub_label.set_text(text + ".")


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
        shell = ui.element("div").classes("cx-float pointer-events-auto")
        shell.style(
            "position:fixed;"
            f"left:{min(40 + offset * 28, 280)}px;"
            f"top:{min(72 + offset * 24, 200)}px;"
            "width:min(92vw, 1000px);"
            "height:min(70vh, 620px);"
            "min-width:340px;"
            "min-height:260px;"
            "max-width:96vw;"
            "max-height:90vh;"
            "resize:both;"
            f"z-index:{z};"
        )
        with shell:
            head = ui.element("div").classes("cx-float-head")
            head.on(
                "mousedown.capture",
                js_handler=_folder_header_drag_js(f"c{shell.id}"),
            )
            n_on = sum(1 for cid in member_ids if connectors[cid].enabled)
            with head:
                ui.html(
                    '<span class="cx-tile is-folder"><span class="cx-icon">folder_open</span></span>',
                    sanitize=False,
                ).classes("contents")
                with ui.element("div").classes("cx-title"):
                    title_label = ui.label(title).classes("cx-name")
                    ui.label(
                        f"{len(member_ids)} connector{'s' if len(member_ids) != 1 else ''} · {n_on} on"
                        " · drag a card out to remove it"
                    ).classes("cx-desc")
                floater_enable_sw = ui.switch(
                    value=(None if fold_state == "mixed" else fold_state == "all_on"),
                    on_change=lambda e, fid=folder_id: set_folder_connectors_enabled(
                        fid, bool(e.value)
                    ),
                ).props("dense color=positive").tooltip(
                    "Turn every connector in this folder on or off"
                )
                if fold_state == "mixed":
                    floater_enable_sw.props("indeterminate color=warning")
                if not member_ids:
                    floater_enable_sw.disable()
                ui.button(
                    icon="edit", on_click=lambda f=folder_id: show_rename_folder_dialog(f)
                ).props("flat round dense size=sm").tooltip("Rename folder")
                ui.button(icon="close", on_click=lambda f=folder_id: _close_folder_floater(f)).props(
                    "flat round dense size=sm"
                ).tooltip("Close")

            body = ui.element("div").classes("cx-float-body")
            body.on("dragover.prevent", lambda _: None)
            body.on("drop.prevent", lambda _: _handle_drop_on_folder(folder_id))
            with body:
                body_grid = ui.element("div").classes("cx-grid")
                with body_grid:
                    for cid in member_ids:
                        create_connector_card(cid, connectors[cid], folder_id)
                    if not member_ids:
                        ui.label("Drag connectors here to add them to this folder.").classes(
                            "cx-members-empty"
                        )

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
            n_on = sum(1 for cid in member_ids if connectors[cid].enabled)
            fold_state = _folder_members_enabled_state(member_ids, connectors)

            wrapper = ui.element("div").classes("cx-card cx-folder")
            wrapper.props(f'data-folder-id="{folder_id}"')
            folder_cards[folder_id] = wrapper
            wrapper.on("dragover.prevent", lambda _: None)
            wrapper.on("drop.prevent", lambda _: _handle_drop_on_folder(folder_id))

            with wrapper:
                with ui.element("div").classes("cx-head"):
                    ui.html(
                        '<span class="cx-tile is-folder"><span class="cx-icon">folder</span></span>',
                        sanitize=False,
                    ).classes("contents")
                    with ui.element("div").classes("cx-title"):
                        title_lbl = ui.label(name).classes("cx-name")
                        folder_tile_title_labels[folder_id] = title_lbl
                        ui.label(
                            f"{count} connector{'s' if count != 1 else ''} · {n_on} on"
                        ).classes("cx-desc")
                    folder_enable_sw = ui.switch(
                        value=(None if fold_state == "mixed" else fold_state == "all_on"),
                        on_change=lambda e, fid=folder_id: set_folder_connectors_enabled(
                            fid, bool(e.value)
                        ),
                    ).props("dense color=positive").tooltip(
                        "Turn every connector in this folder on or off"
                    )
                    if fold_state == "mixed":
                        folder_enable_sw.props("indeterminate color=warning")
                    if not member_ids:
                        folder_enable_sw.disable()

                members = ui.element("div").classes("cx-members")
                members.on("click", lambda f=folder_id: _open_folder_floating_window(f))
                members.tooltip("Open folder")
                with members:
                    if not member_ids:
                        ui.label("Empty folder. Drag connectors here.").classes("cx-members-empty")
                    shown = member_ids[:_FOLDER_PREVIEW_LIMIT]
                    for cid in shown:
                        c = connectors[cid]
                        trig = c.trigger.trigger_type if c.trigger else None
                        icon_html = _trigger_glyph_html(trig) if trig else _icon_html("bolt")
                        trig_text = format_trigger_name(trig) if trig else "No trigger"
                        ui.html(
                            f'<div class="cx-member{"" if c.enabled else " is-off"}">'
                            '<span class="cx-member-dot"></span>'
                            f'<span class="cx-member-icon">{icon_html}</span>'
                            f'<span class="cx-member-name">{html_lib.escape((c.name or "").strip() or "Untitled")}</span>'
                            f'<span class="cx-member-trig">{html_lib.escape(trig_text)}</span></div>',
                            sanitize=False,
                        ).classes("contents")
                    if count > len(shown):
                        ui.label(f"+ {count - len(shown)} more").classes("cx-member-more")

                with ui.element("div").classes("cx-foot"):
                    ui.html(
                        '<span class="cx-stat is-muted"><span class="cx-icon">drag_indicator</span>Drop connectors here</span>',
                        sanitize=False,
                    ).classes("contents")
                    ui.element("div").classes("cx-spacer")
                    ui.button(
                        "Open", icon="open_in_new", on_click=lambda f=folder_id: _open_folder_floating_window(f)
                    ).props("flat dense no-caps").classes("cx-open px-2")
                    with ui.button(icon="more_horiz").props("flat round dense size=sm").tooltip("More"):
                        with ui.menu().props("auto-close"):
                            ui.menu_item("Rename folder", on_click=lambda f=folder_id: show_rename_folder_dialog(f))
                            ui.menu_item("Delete folder…", on_click=lambda f=folder_id: show_delete_folder_dialog(f))

        global _no_match_label
        folder_specs_all = layout.get("folders") or {}
        _update_toolbar_counts(connectors, len(folder_specs_all))
        with connectors_container:
            if not connectors:
                with ui.column().classes("cx-empty empty-state w-full"):
                    ui.html('<span class="cx-icon">hub</span>', sanitize=False).classes("contents")
                    ui.label("No connectors yet").classes("cx-empty-title")
                    ui.label(
                        "A connector does something when something happens on stream: "
                        "thank a big cheer in chat, switch scenes on a raid, run a hotkey."
                    ).classes("text-sm max-w-md")
                    primary_button(
                        "Create your first connector", show_create_connector_dialog, icon="add"
                    ).classes("mt-2")
                _no_match_label = None
            else:
                if folder_specs_all:
                    root_drop = ui.element("div").classes("cx-root-drop")
                    root_drop.props('data-root-drop="1"')
                    root_drop.on("dragover.prevent", lambda _: None)
                    root_drop.on("drop.prevent", lambda _: _handle_drop_on_root())
                    with root_drop:
                        ui.html(
                            '<span class="cx-icon">move_up</span><span>Drop here to take the connector out of its folder</span>',
                            sanitize=False,
                        ).classes("contents")

                with ui.element("div").classes("cx-grid"):
                    for item in layout.get("root_items") or []:
                        kind = item.get("kind")
                        iid = item.get("id")
                        if not kind or not iid:
                            continue
                        if kind == "connector":
                            if iid in connectors:
                                create_connector_card(iid, connectors[iid], None)
                        elif kind == "folder":
                            spec = folder_specs_all.get(iid)
                            if spec and isinstance(spec, dict):
                                render_folder(iid, spec)
                _no_match_label = ui.label("").classes("cx-empty hidden")

        folder_specs = layout.get("folders") or {}
        for fid in open_folder_ids:
            if fid in folder_specs:
                _open_folder_floating_window(fid)

    except Exception as e:
        logger.error(f"Error loading connectors: {e}", exc_info=True)
        with connectors_container:
            ui.label(f"Error loading connectors: {str(e)}").classes("text-red-400")


_FOLDER_PREVIEW_LIMIT = 5
_CARD_ACTION_LIMIT = 3
_CARD_CONDITION_LIMIT = 2

_TRIGGER_BRAND = {
    "twitch_": "twitch",
    "youtube_": "youtube",
    "obs_": "obs",
}
_TRIGGER_ICONS = {
    TriggerType.DONATION: "volunteer_activism",
    TriggerType.TIMER: "timer",
    TriggerType.SCHEDULE: "event",
    TriggerType.HOTKEY: "keyboard",
    TriggerType.STREAMDECK: "grid_view",
    TriggerType.WEBHOOK: "webhook",
    TriggerType.ANY: "all_inclusive",
}
_ACTION_ICONS = {
    "template_control": "widgets",
    "websocket_emit": "settings_ethernet",
    "trigger_alert": "notifications_active",
    "send_chat_message": "chat",
    "send_announcement": "campaign",
    "display_banner": "featured_video",
    "add_greeting": "waving_hand",
    "update_greeting": "waving_hand",
    "send_greeting": "waving_hand",
    "api_call": "http",
    "write_file": "description",
    "execute_command": "terminal",
    "key_press": "keyboard",
    "audio_control": "volume_up",
    "game_hook": "sports_esports",
}
_ACTION_BRANDS = {"send_discord_message": "discord", "obs_control": "obs"}


def _icon_html(name: str) -> str:
    return f'<span class="cx-icon">{name}</span>'


def _brand_html(key: str) -> str:
    svg = SERVICE_BRAND_SVG.get(key, "")
    return f'<span class="cx-brand is-{key}">{svg}</span>' if svg else ""


def _trigger_brand_key(trigger_type: Optional[TriggerType]) -> str:
    value = getattr(trigger_type, "value", "") or ""
    for prefix, key in _TRIGGER_BRAND.items():
        if value.startswith(prefix):
            return key
    return ""


def _trigger_glyph_html(trigger_type: Optional[TriggerType]) -> str:
    key = _trigger_brand_key(trigger_type)
    if key:
        return _brand_html(key) or _icon_html("bolt")
    return _icon_html(_TRIGGER_ICONS.get(trigger_type, "bolt"))


def _action_glyph_html(action) -> str:
    value = getattr(getattr(action, "action_type", None), "value", "") or ""
    brand = _ACTION_BRANDS.get(value)
    if brand:
        return _brand_html(brand) or _icon_html("play_arrow")
    if value == "audio_control" and getattr(action, "control_type", "") == "microphone":
        return _icon_html("mic")
    return _icon_html(_ACTION_ICONS.get(value, "play_arrow"))


def _chip_html(kind: str, glyph: str, text: str) -> str:
    safe = html_lib.escape(text)
    return f'<span class="cx-chip is-{kind}" title="{safe}">{glyph}<span>{safe}</span></span>'


def _relative_time(ts: float, now: Optional[float] = None) -> str:
    """'just now', '5m ago', '3h ago', 'Yesterday', '4 days ago', then a date."""
    if not ts:
        return ""
    now = time.time() if now is None else now
    delta = max(0, now - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    then = datetime.datetime.fromtimestamp(ts)
    today = datetime.datetime.fromtimestamp(now).date()
    if then.date() == today:
        return f"{int(delta // 3600)}h ago"
    days = (today - then.date()).days
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    fmt = "%b %d" if then.year == today.year else "%b %d, %Y"
    return then.strftime(fmt).replace(" 0", " ")


def _connector_flow_html(connector: Connector) -> str:
    """When / If / Then rows for a card."""
    trigger = connector.trigger
    rows = []
    if trigger:
        rows.append(("When", _chip_html("trigger", _trigger_glyph_html(trigger.trigger_type),
                                        format_trigger_name(trigger.trigger_type))))
        conditions = list(trigger.conditions or [])
        if conditions:
            chips = "".join(
                _chip_html("cond", _icon_html("filter_alt"), _format_condition_for_card(c, trigger.trigger_type))
                for c in conditions[:_CARD_CONDITION_LIMIT]
            )
            if len(conditions) > _CARD_CONDITION_LIMIT:
                chips += f'<span class="cx-more">+{len(conditions) - _CARD_CONDITION_LIMIT} more</span>'
            rows.append(("If", chips))
    else:
        rows.append(("When", _chip_html("none", _icon_html("help_outline"), "No trigger")))
    actions = list(connector.actions or [])
    if actions:
        chips = "".join(
            _chip_html("action", _action_glyph_html(a), get_action_display_name(a))
            for a in actions[:_CARD_ACTION_LIMIT]
        )
        if len(actions) > _CARD_ACTION_LIMIT:
            chips += f'<span class="cx-more">+{len(actions) - _CARD_ACTION_LIMIT} more</span>'
    else:
        chips = _chip_html("none", _icon_html("help_outline"), "No actions")
    rows.append(("Then", chips))
    return "".join(
        f'<div class="cx-flow-row"><span class="cx-flow-key">{k}</span><div class="cx-flow-val">{v}</div></div>'
        for k, v in rows
    )


def create_connector_card(
    connector_id: str, connector: Connector, folder_id: Optional[str] = None
):
    """Create a card display for a connector"""
    global connector_parent_folder

    card_element = (
        ui.element("div")
        .classes("cx-card" + ("" if connector.enabled else " is-off"))
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

    trigger_type = connector.trigger.trigger_type if connector.trigger else None
    brand = _trigger_brand_key(trigger_type)

    with card_element:
        with ui.element("div").classes("cx-head"):
            ui.html(
                f'<span class="cx-tile{" is-" + brand if brand else ""}">{_trigger_glyph_html(trigger_type)}</span>',
                sanitize=False,
            ).classes("contents")
            with ui.element("div").classes("cx-title"):
                name = ui.label(connector.name or "Untitled").classes("cx-name")
                name.on("dblclick", lambda cid=connector_id: show_edit_connector_dialog(cid))
                if connector.description:
                    ui.label(connector.description).classes("cx-desc").tooltip(connector.description)
                else:
                    ui.label("No description").classes("cx-desc is-empty")
            ui.switch(
                value=connector.enabled,
                on_change=lambda e, cid=connector_id: toggle_connector(cid, e.value),
            ).props("dense color=positive").tooltip("Turn this connector on or off")

        ui.html(_connector_flow_html(connector), sanitize=False).classes("cx-flow")

        with ui.element("div").classes("cx-foot"):
            if connector.trigger_count:
                when = _relative_time(connector.last_triggered)
                stat = (
                    f'<span class="cx-stat"><span class="cx-icon">bolt</span><b>{connector.trigger_count}</b>'
                    f' run{"s" if connector.trigger_count != 1 else ""}</span>'
                )
                if when:
                    stat += f'<span class="cx-stat">&nbsp;·&nbsp;{html_lib.escape(when)}</span>'
                stat_el = ui.html(stat, sanitize=False).classes("contents")
                if connector.last_triggered:
                    full = datetime.datetime.fromtimestamp(connector.last_triggered).strftime("%Y-%m-%d %H:%M")
                    stat_el.tooltip(f"Last run {full}")
            else:
                ui.html(
                    '<span class="cx-stat is-muted"><span class="cx-icon">schedule</span>Never run</span>',
                    sanitize=False,
                ).classes("contents")
            ui.element("div").classes("cx-spacer")
            ui.button(
                icon="play_arrow", on_click=lambda cid=connector_id: test_connector(cid)
            ).props("flat round dense size=sm").tooltip("Test connector")
            ui.button(
                icon="edit", on_click=lambda cid=connector_id: show_edit_connector_dialog(cid)
            ).props("flat round dense size=sm").tooltip("Edit connector")
            with ui.button(icon="more_horiz").props("flat round dense size=sm").tooltip("More"):
                with ui.menu().props("auto-close"):
                    ui.menu_item("Duplicate", on_click=lambda cid=connector_id: duplicate_connector(cid))
                    ui.menu_item("Delete…", on_click=lambda cid=connector_id: delete_connector(cid))


def format_trigger_name(trigger_type: TriggerType) -> str:
    """Trigger name as the connector editor shows it (the card icon carries the service)."""
    from .. import connector_variables

    info = connector_variables.trigger_info(trigger_type)
    if info and info.get("label"):
        return info["label"]
    return trigger_type.value.replace("_", " ").title()


def _catalog_label(catalog: List[Dict[str, Any]], op: str) -> str:
    entry = next((c for c in catalog if c.get("id") == op), None)
    return (entry or {}).get("label") or op.replace("_", " ")


def get_action_display_name(action) -> str:
    """Short card label for an action, using the editor's action names."""
    from .connector_action_schemas import spec_for

    try:
        action_type = action.action_type.value
        spec = spec_for(action_type)
        label = spec.label if spec else action_type.replace("_", " ").capitalize()
        if action_type == "send_chat_message":
            targets = getattr(action, "reply_targets", None) or ["twitch"]
            names = [{"twitch": "Twitch", "youtube": "YouTube"}.get(t, t) for t in targets]
            return f"{label} → {' + '.join(names)}"
        if action_type == "send_discord_message":
            channels = [c for c in (getattr(action, "channels", None) or []) if isinstance(c, dict)]
            if len(channels) == 1:
                return f"{label} → #{channels[0].get('channel_name') or channels[0].get('channel_id')}"
            return f"{label} → {len(channels)} channels"
        if action_type == "trigger_alert":
            from ..connector_actions import normalize_trigger_alert_type
            from ..connector_variables import ALERT_TYPES

            raw_type = str(getattr(action, "alert_type", "") or "")
            alert = dict(ALERT_TYPES).get(normalize_trigger_alert_type(raw_type), raw_type)
            return f"{label}: {alert}" if alert else label
        if action_type == "template_control":
            template = str(getattr(action, "template_name", "") or "")
            control = str(getattr(action, "control_action", "") or "")
            return f"{template.replace('_', ' ').title()} → {control.replace('_', ' ')}" if template and control else label
        if action_type == "display_banner" and getattr(action, "permanent", False):
            return f"{label} (until replaced)"
        if action_type == "obs_control":
            from ..obs_connector_catalog import OBS_CONNECTOR_CATALOG

            return _catalog_label(OBS_CONNECTOR_CATALOG, str(getattr(action, "operation", "") or ""))
        if action_type == "game_hook":
            from ..game_hooks.ff7_hook import FF7_CONNECTOR_CATALOG

            return f"FF7: {_catalog_label(FF7_CONNECTOR_CATALOG, str(getattr(action, 'operation', '') or ''))}"
        if action_type == "api_call":
            from urllib.parse import urlparse

            url = str(getattr(action, "url", "") or "")
            host = urlparse(url).netloc if url.startswith("http") else ""
            return f"{label} → {host}" if host else label
        if action_type == "write_file":
            name = str(getattr(action, "file_path", "") or "").replace("\\", "/").split("/")[-1]
            return f"{label}: {name}" if name else label
        if action_type == "key_press":
            if getattr(action, "input_type", "key") == "macro":
                return f"{label}: macro"
            keys = str(getattr(action, "key_sequence", "") or "").replace("_click", " click")
            return f"{label}: {keys}" if keys else label
        if action_type == "websocket_emit":
            event = str(getattr(action, "event_name", "") or "")
            return f"{label}: {event}" if event else label
        return label
    except Exception:
        return getattr(action, "name", "Unknown action")


def _unique_connector_name(base: str, taken: set) -> str:
    root = (base or "Connector").strip() or "Connector"
    candidate = f"{root} copy"
    number = 2
    taken_lower = {name.lower() for name in taken if name}
    while candidate.lower() in taken_lower:
        candidate = f"{root} copy {number}"
        number += 1
    return candidate


def duplicate_connector(connector_id: str) -> None:
    """Save a new connector with the same trigger and actions."""
    try:
        manager = connector_manager.get_manager()
        existing = manager.get_connector(connector_id)
        if existing is None:
            notify("Connector not found", type="negative")
            return
        data = manager._serialize_connector(existing)
        new_id = str(uuid.uuid4())
        data["connector_id"] = new_id
        data["name"] = _unique_connector_name(
            existing.name,
            {connector.name for connector in manager.get_all_connectors().values()},
        )
        data["created_at"] = time.time()
        data["last_triggered"] = 0
        data["trigger_count"] = 0
        trigger = data.get("trigger")
        if isinstance(trigger, dict):
            trigger["trigger_id"] = str(uuid.uuid4())
            trigger["last_triggered"] = 0
            if "connector_id" in trigger:
                trigger["connector_id"] = new_id
        for action in data.get("actions") or []:
            if isinstance(action, dict):
                action["action_id"] = str(uuid.uuid4())
        cloned = manager._deserialize_connector(data)
        if cloned is None or not manager.add_connector(cloned):
            notify("Could not duplicate connector", type="negative")
            return
        load_connectors()
        update_search_visibility()
        notify(f"Duplicated connector '{data['name']}'.", type="positive")
    except Exception as exc:
        logger.error("Error duplicating connector: %s", exc, exc_info=True)
        notify("Could not duplicate connector", type="negative")


_CONDITION_SYMBOLS = {
    "equal": "=", "not_equal": "≠", "greater_than": ">", "greater_than_or_equal": "≥", "less_than": "<",
    "less_than_or_equal": "≤", "contains": "contains", "not_contains": "doesn't contain", "starts_with": "first word",
    "begins_with": "starts with", "ends_with": "ends with", "regex_match": "matches",
}


def _format_condition_for_card(condition, trigger_type: TriggerType) -> str:
    """Human-readable condition text for connector card chips."""
    from .. import connector_variables

    fields = connector_variables.CONDITION_FIELDS.get(getattr(trigger_type, "value", ""), []) or []
    field_label = next((f.get("label") for f in fields if f.get("key") == condition.field), None) or condition.field or ""
    op_key = getattr(condition.operator, "value", condition.operator)
    value = condition.value
    value_text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return f"{field_label} {_CONDITION_SYMBOLS.get(op_key, str(op_key).replace('_', ' '))} {value_text}"


_active_editor = None


def show_create_connector_dialog():
    """Open the connector editor for a new connector."""
    show_connector_dialog()


def show_edit_connector_dialog(connector_id: str):
    """Open the connector editor for an existing connector."""
    show_connector_dialog(connector_id)


def show_connector_dialog(connector_id: str = None):
    """Open the New/Edit connector editor (see ``connector_editor``)."""
    global _active_editor
    from .connector_editor import open_connector_editor

    if _active_editor is not None and _active_editor.dialog is not None:
        # Drop the previous editor's elements instead of keeping every closed dialog around.
        try:
            _active_editor.dialog.delete()
        except Exception:
            pass
    try:
        _active_editor = open_connector_editor(connector_id, on_saved=refresh_connectors)
    except Exception as exc:
        logger.error("Could not open the connector editor: %s", exc, exc_info=True)
        notify("Could not open the connector editor", type="negative")


def test_connector(connector_id: str):
    """Run a connector once with a sample event for its trigger."""
    from .connector_editor import run_connector_test

    run_connector_test(connector_id)




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
    """Refresh the connectors display without clearing the search query."""
    global connector_cards, folder_cards, connector_parent_folder, folder_tile_title_labels
    connector_cards.clear()
    folder_cards.clear()
    connector_parent_folder.clear()
    folder_tile_title_labels.clear()
    load_connectors()
    update_search_visibility()


def on_search_change(event):
    """Handle search input changes"""
    global current_search
    current_search = event.value.lower() if event.value else ""
    update_search_visibility()


def _status_ok(connector) -> bool:
    if current_status == "on":
        return bool(connector.enabled)
    if current_status == "off":
        return not connector.enabled
    return True


def update_search_visibility():
    """Show cards and folders that match the search text and the All / On / Off filter."""
    search_term = current_search.strip()
    filtering = bool(search_term) or current_status != "all"

    visible_count = 0
    total_connectors = len(connector_cards)
    manager = connector_manager.get_manager()
    layout = connector_layout_store.load_layout()

    def matches(conn) -> bool:
        return bool(conn) and _status_ok(conn) and (not search_term or search_term in _connector_search_blob(conn))

    for folder_id, folder_el in folder_cards.items():
        try:
            spec = (layout.get("folders") or {}).get(folder_id) or {}
            member_ids = list(spec.get("connector_ids") or [])
            fname = (spec.get("name") or "").lower()
            members = {cid: manager.get_connector(cid) for cid in member_ids}

            st = _folder_floaters.get(folder_id)
            shell = st.get("shell") if st else None
            floater_open = shell is not None and not shell.is_deleted

            name_hit = bool(search_term) and search_term in fname
            shown = {
                cid
                for cid, conn in members.items()
                if conn and (matches(conn) or (name_hit and _status_ok(conn)))
            }
            folder_visible = not filtering or bool(shown) or (name_hit and current_status == "all")

            if folder_visible:
                folder_el.classes(remove="hidden")
                visible_count += 1
            else:
                folder_el.classes(add="hidden")
            if floater_open:
                for cid in member_ids:
                    card_el = connector_cards.get(cid)
                    if not card_el:
                        continue
                    if not filtering or cid in shown:
                        card_el.classes(remove="hidden")
                    else:
                        card_el.classes(add="hidden")
        except Exception as e:
            logger.error(f"Error updating visibility for folder {folder_id}: {e}")
            folder_el.classes(remove="hidden")

    for connector_id, card_element in connector_cards.items():
        if connector_parent_folder.get(connector_id):
            continue
        try:
            connector = manager.get_connector(connector_id)
            if connector and matches(connector):
                card_element.classes(remove="hidden")
                visible_count += 1
            else:
                card_element.classes(add="hidden")
        except Exception as e:
            logger.error(f"Error updating visibility for connector {connector_id}: {e}")
            card_element.classes(remove="hidden")

    label = _no_match_label
    if label is not None and not label.is_deleted:
        if total_connectors and visible_count == 0 and filtering:
            if search_term:
                text = f"No connectors match “{search_term}”"
                if current_status != "all":
                    text += f" among the {current_status} ones"
            else:
                text = "No connectors are on." if current_status == "on" else "No connectors are off."
            label.set_text(text)
            label.classes(remove="hidden")
        else:
            label.classes(add="hidden")

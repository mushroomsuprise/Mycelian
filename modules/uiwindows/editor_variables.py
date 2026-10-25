# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Insert-variable UI shared by the connector and chatbot editors.

``VariableKit`` owns the ``{ }`` button and its searchable menu, the date/time format view,
the Variables side panel, and inserting a token at the caret of the last text field the
user focused. Editors supply the variable list and a ``render`` function that fills
example values. Styles live in ``connector_editor_styles`` (``.ce-*`` / ``.ce-vmenu``).
"""

from __future__ import annotations

import html as html_lib
import json
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui

from ..notification_engine import notify

DEFAULT_DATE_FORMATS = (
    {"format": "%H:%M", "label": "24-hour time"},
    {"format": "%-I:%M %p", "label": "12-hour time"},
    {"format": "%Y-%m-%d", "label": "ISO date"},
    {"format": "%b %-d", "label": "Short date"},
    {"format": "%A, %B %-d, %Y", "label": "Long date"},
)
FORMAT_CODES = ("%H hour · %-I hour (12h) · %M minute · %S second · %p AM/PM · %A weekday · %a Mon · %B month · "
                "%b Jan · %-d day · %Y year")

VariablesFn = Callable[[Optional[int], Optional[List[dict]]], List[dict]]


def icon_html(name: str) -> str:
    return f'<span class="ce-icon">{name}</span>'


def esc(text: Any) -> str:
    return html_lib.escape(str(text if text is not None else ""))


def raw_html(markup: str, classes: str = "contents") -> ui.html:
    return ui.html(markup, sanitize=False).classes(classes)


def dom_id(element: ui.element) -> str:
    return getattr(element, "html_id", None) or f"c{element.id}"


class VariableKit:
    """Variable menu, format picker, side panel and caret insertion for one editor dialog."""

    def __init__(self, variables: VariablesFn, render: Callable[[str], str],
                 date_formats: Optional[Callable[[], List[dict]]] = None, empty_hint: str = "Type any {name} your event sends."):
        self.variables = variables
        self.render = render
        self.date_formats = date_formats or (lambda: list(DEFAULT_DATE_FORMATS))
        self.empty_hint = empty_hint
        self.target: Optional[ui.element] = None
        self.query = ""
        self._touched: set = set()

    def example(self, var: dict) -> str:
        token = "{" + var["key"] + "}"
        rendered = self.render(token)
        return str(var.get("example", "")) if rendered == token else rendered

    # -- caret insertion ---------------------------------------------------

    def focus(self, element: ui.element) -> None:
        self.target = element
        self._touched.add(element.id)

    def track(self, element: ui.element) -> None:
        """Remember ``element`` as the panel's insert target whenever it gets focus."""
        element.on("focus", lambda _e, el=element: self.focus(el))

    def insert(self, element: Optional[ui.element], token: str) -> None:
        if element is None or getattr(element, "is_deleted", False):
            ui.clipboard.write(token)
            notify(f"Copied {token}. Click into a text field to insert variables there.", type="info")
            return
        touched = element.id in self._touched
        self.focus(element)
        ui.run_javascript(f"""(() => {{
            const root = document.getElementById({json.dumps(dom_id(element))});
            const el = root && (root.matches('textarea, input') ? root : root.querySelector('textarea, input'));
            if (!el) return;
            const t = {json.dumps(token)};
            const touched = {json.dumps(touched)};
            const s = touched && el.selectionStart != null ? el.selectionStart : el.value.length;
            const e = touched && el.selectionEnd != null ? el.selectionEnd : s;
            el.value = el.value.slice(0, s) + t + el.value.slice(e);
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.focus();
            try {{ el.setSelectionRange(s + t.length, s + t.length); }} catch (_) {{}}
        }})()""")

    # -- { } button and menu -------------------------------------------------

    def button(self, element: ui.element, idx: Optional[int] = None, extra: Optional[List[dict]] = None) -> ui.button:
        btn = ui.button("{ }").props("flat dense no-caps").classes("ce-varbtn")
        btn.tooltip("Insert a variable")
        with btn:
            menu = ui.menu().props('anchor="bottom right" self="top right"').classes("ce-vmenu")
        btn.on("click", lambda: self.menu(menu, lambda token: self.insert(element, token), idx, extra))
        return btn

    def menu(self, menu: ui.menu, insert: Callable[[str], None], idx: Optional[int] = None, extra: Optional[List[dict]] = None) -> None:
        menu.clear()
        variables = self.variables(idx, extra)
        with menu:
            search = ui.input(placeholder="Search variables").props("outlined dense clearable autofocus").classes("w-full")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-sm")
            rows = ui.element("div").style("max-height: 320px; overflow: auto")

        def pick(var: dict) -> None:
            if var.get("date"):
                self.format_view(menu, var["key"], lambda t: (insert(t), menu.close()),
                                 back=lambda: self.menu(menu, insert, idx, extra))
            else:
                insert("{" + var["key"] + "}")
                menu.close()

        def fill(query: str = "") -> None:
            q = query.strip().lower()
            rows.clear()
            with rows:
                last_group = None
                count = 0
                for var in variables:
                    if q and q not in f"{var['key']} {var.get('label', '')}".lower():
                        continue
                    group = var.get("group") or "Event"
                    if group != last_group:
                        ui.label(group).classes("ce-vm-group")
                        last_group = group
                    row = ui.element("div").classes("ce-vm-row")
                    with row:
                        raw_html(f'<code>{{{esc(var["key"])}}}</code><span class="ce-vm-label">{esc(var.get("label", ""))}'
                                 f'{" · format…" if var.get("date") else ""}</span><span class="ce-vm-ex">{esc(self.example(var))}</span>')
                    row.on("click", lambda _e, v=var: pick(v))
                    count += 1
                if not count:
                    ui.label(f"No variables match. {self.empty_hint}").classes("ce-vm-empty")

        search.on_value_change(lambda e: fill(e.value or ""))
        fill()

    def format_view(self, container: ui.element, key: str, insert: Callable[[str], None],
                    back: Optional[Callable[[], None]] = None) -> None:
        container.clear()
        with container:
            with ui.element("div").classes("flex items-center gap-1").style("padding: 2px 4px 4px"):
                if back is not None:
                    ui.button(icon="arrow_back", on_click=back).props("flat round dense size=sm")
                raw_html(f'<span class="ce-vm-label">Format <code>{{{esc(key)}}}</code></span>', "")
            for opt in [{"format": "", "label": "As is"}] + list(self.date_formats()):
                token = "{" + key + (":" + opt["format"] if opt["format"] else "") + "}"
                row = ui.element("div").classes("ce-vm-row is-fmt")
                with row:
                    raw_html(f'<span class="ce-vm-label">{esc(opt.get("label", ""))}</span><span class="ce-vm-ex">{esc(self.render(token))}</span>')
                row.on("click", lambda _e, t=token: insert(t))
            ui.label("Custom").classes("ce-vm-group")
            with ui.element("div").classes("flex items-center gap-1").style("padding: 0 4px"):
                custom = ui.input(placeholder="%A at %-I:%M %p").props("outlined dense").classes("grow")
                ui.button(icon="check", on_click=lambda: insert("{" + key + ":" + custom.value + "}") if (custom.value or "").strip()
                          else None).props("flat round dense color=primary").tooltip("Insert")
            example = ui.label("").classes("ce-vm-ex").style("padding: 2px 8px; max-width: none")
            custom.on_value_change(lambda e: example.set_text(self.render("{" + key + ":" + e.value + "}") if (e.value or "").strip() else ""))
            ui.label(FORMAT_CODES).classes("ce-vm-empty").style("font-size: .66rem; line-height: 1.5")

    # -- side panel ------------------------------------------------------------

    def panel(self, container: ui.element, tag: str = "", empty_html: str = "",
              group_footer: Optional[Callable[[str], None]] = None, groups_always: Optional[Dict[str, str]] = None,
              row_tools: Optional[Callable[[dict], None]] = None) -> None:
        """Render the Variables panel into ``container``.

        ``group_footer(group)`` runs after each group's rows (e.g. a "New custom variable"
        button). ``groups_always`` maps a group name to a placeholder shown when the group
        has no variables yet, so its footer still appears. ``row_tools(var)`` can add small
        buttons to a row (e.g. edit for custom variables).
        """
        container.clear()
        with container:
            with ui.element("div").classes("ce-vars-head"):
                raw_html(icon_html("data_object"))
                ui.label("Variables")
                ui.element("div").classes("ce-spacer")
                if tag:
                    ui.label(tag).classes("ce-tag")
            if empty_html:
                raw_html(f'<div class="ce-vars-empty">{icon_html("bolt")}<span>{empty_html}</span></div>', "")
            search = ui.input(placeholder="Search variables", value=self.query).props("outlined dense clearable")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-sm")
            listing = ui.element("div").classes("flex flex-col")
            ui.label("Click a variable to insert it where the cursor was. Date and time variables can be formatted with the tune button.").classes(
                "ce-vars-tip")

        def fill(query: str) -> None:
            self.query = query
            q = query.strip().lower()
            listing.clear()
            seen_groups: List[str] = []
            with listing:
                last_group = None
                for var in self.variables(None, None):
                    if q and q not in f"{var['key']} {var.get('label', '')}".lower():
                        continue
                    group = var.get("group") or "Event"
                    if group != last_group:
                        if last_group is not None and group_footer:
                            group_footer(last_group)
                        ui.label(group).classes("ce-vgroup")
                        last_group = group
                        seen_groups.append(group)
                    self._panel_row(var, row_tools)
                if last_group is not None and group_footer:
                    group_footer(last_group)
                for group, placeholder in (groups_always or {}).items():
                    if group not in seen_groups and not q:
                        ui.label(group).classes("ce-vgroup")
                        if placeholder:
                            ui.label(placeholder).classes("ce-vars-tip").style("margin: 0 0 2px 8px")
                        if group_footer:
                            group_footer(group)

        search.on_value_change(lambda e: fill(e.value or ""))
        fill(self.query)

    def _panel_row(self, var: dict, row_tools: Optional[Callable[[dict], None]] = None) -> None:
        row = ui.element("div").classes("ce-var").tooltip(var.get("label", ""))
        with row:
            raw_html(f'<code>{{{esc(var["key"])}}}</code><span class="ce-var-ex">{esc(self.example(var))}</span>')
            if var.get("date"):
                tune = ui.button(icon="tune").props("flat round dense size=xs").tooltip("Insert with a date/time format")
                with tune:
                    fmenu = ui.menu().props('anchor="bottom right" self="top right"').classes("ce-vmenu ce-fmenu")
                tune.on("click.stop", lambda _e, k=var["key"], m=fmenu: self.format_view(
                    m, k, lambda t, mm=m: (self.insert(self.target, t), mm.close())))
            if row_tools is not None:
                row_tools(var)
        row.on("click", lambda _e, k=var["key"]: self.insert(self.target, "{" + k + "}"))

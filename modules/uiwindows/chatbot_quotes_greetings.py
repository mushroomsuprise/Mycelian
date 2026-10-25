# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Quotes and Greetings tabs of the Chatbot window: toolbars, settings strips, cards and dialogs.

Cards reuse the Connectors / chatbot card styles (``.cnx``); dialogs reuse the editor shell
(``.cxe``) and the shared insert-variable kit. All settings are saved together when the
user presses Save (nothing applies while a dialog is still open).
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from nicegui import background_tasks, run, ui

from ..chatbot_core import Greeting, Quote
from ..chatbot_manager import DEFAULT_QUOTE_REPLY_FORMAT, get_manager
from ..notification_engine import notify
from .chatbot_cards import _relative
from .editor_variables import VariableKit
from .editor_variables import icon_html as _ic
from .editor_variables import raw_html as _html

logger = logging.getLogger(__name__)

QG_CSS = """
.cnx .qg-strip { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; padding: 8px 12px; border-radius: 10px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); font-size: .76rem; color: var(--color-text-secondary); }
.cnx .qg-strip b { color: var(--color-text-primary); font-weight: 650; }
.cnx .qg-strip .cx-icon { font-size: 15px; color: var(--color-primary); }
.cnx .qg-item { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.cnx .qg-sep { width: 1px; height: 18px; background: var(--color-border-subtle); }
.cnx .qg-code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; padding: 1px 6px; border-radius: 5px;
    color: var(--color-primary); background: var(--color-primary-light); }
.cnx .qg-fmt { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; padding: 3px 8px; border-radius: 6px;
    background: var(--color-bg-base); border: 1px dashed var(--color-border-default); color: var(--color-text-secondary);
    max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle; }
.cnx .qg-strip .q-btn { min-height: 26px; font-size: .74rem; }
.cnx .qg-strip .q-field__control { height: 26px !important; min-height: 26px; }
.cnx .qg-strip .q-field__native, .cnx .qg-strip .q-field__marginal { min-height: 26px; height: 26px; font-size: .74rem; }
.cnx .qg-quote { position: relative; margin: 2px 12px 8px 15px; padding: 10px 12px 10px 34px; border-radius: 10px; font-size: .86rem; line-height: 1.5;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); color: var(--color-text-primary);
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; word-break: break-word; }
.cnx .qg-quote::before { content: '\\201C'; position: absolute; left: 10px; top: 0; font-size: 2.2rem; line-height: 1.2; color: var(--color-primary);
    opacity: .7; font-family: Georgia, 'Times New Roman', serif; }
.cnx .qg-num { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .74rem; font-weight: 800; padding: 2px 8px; border-radius: 6px;
    color: var(--color-primary); background: var(--color-primary-light); border: 1px solid var(--color-border-accent); flex: 0 0 auto; }
.cnx .qg-by { font-size: .78rem; font-weight: 650; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cnx .qg-by.is-empty { font-style: italic; font-weight: 500; color: var(--color-text-muted); }
.cnx .qg-meta { display: flex; flex-wrap: wrap; gap: 4px 10px; padding: 0 12px 10px 15px; font-size: .7rem; color: var(--color-text-muted); flex: 1 1 auto; align-content: flex-start; }
.cnx .qg-meta span { display: inline-flex; align-items: center; gap: 4px; }
.cnx .qg-meta .cx-icon { font-size: 13px; }
.cnx .cx-card.is-quote { box-shadow: inset 3px 0 0 var(--color-primary); }
.cnx .cx-card > .cx-foot { margin-top: auto; }
.cnx .qg-default { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px 14px; border-radius: 12px;
    background: linear-gradient(90deg, color-mix(in srgb, var(--color-primary) 10%, var(--color-bg-surface)), var(--color-bg-surface));
    border: 1px solid var(--color-border-accent); }
.cnx .qg-default.is-off { background: var(--color-bg-surface); border-color: var(--color-border-subtle); }
.cnx .qg-default.is-off .qg-default-msg { opacity: .55; }
.cnx .qg-default-t { font-size: .7rem; font-weight: 700; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: .06em; }
.cnx .qg-default-msg { font-size: .9rem; color: var(--color-text-primary); margin-top: 2px; word-break: break-word; }
.cnx .qg-default-sub { font-size: .72rem; color: var(--color-text-muted); margin-top: 2px; }
.cnx .qg-avatar { width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .82rem;
    font-weight: 800; color: #fff; flex: 0 0 auto; }
.cnx .qg-user { font-size: .9rem; font-weight: 650; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cnx .qg-login { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .68rem; color: var(--color-text-muted); }
.cnx .qg-section { display: flex; align-items: center; gap: 8px; font-size: .7rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    color: var(--color-text-muted); }
.cnx .qg-section::after { content: ''; flex: 1; height: 1px; background: var(--color-border-subtle); }
"""

QG_DIALOG_CSS = """
.cxe.qg-dlg { width: min(640px, 94vw); height: auto; max-height: 92vh; }
.qg-body { padding: 12px; background: var(--color-bg-base); overflow: auto; }
.qg-body > * + * { margin-top: 10px; }
.qg-found { display: inline-flex; align-items: center; gap: 6px; font-size: .74rem; font-weight: 600; color: var(--color-success); }
.qg-found.is-warn { color: var(--color-warning); }
.qg-found.is-busy { color: var(--color-text-muted); }
.qg-found .ce-icon { font-size: 16px; }
.qg-danger.q-btn { color: var(--color-error) !important; }
"""

_AVATAR_COLORS = ("#8b5cf6", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#14b8a6", "#6366f1")
_PERMISSIONS = (("everyone", "groups", "Everyone"), ("subscriber", "star", "Subscribers"), ("vip", "diamond", "VIPs"), ("moderator", "shield", "Mods"))
_PERMISSION_LABEL = {v: label for v, _i, label in _PERMISSIONS}
_SORTS = {"newest": "Newest first", "oldest": "Oldest first", "number": "By number", "used": "Most used"}
_RESET_CHOICES = {1: "hour", 3: "3 hours", 6: "6 hours", 12: "12 hours", 24: "24 hours", 48: "2 days", 168: "week"}
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{3,25}$")


def _icon(name: str) -> str:
    return f'<span class="cx-icon">{name}</span>'


def _esc(text: Any) -> str:
    return html_lib.escape(str(text if text is not None else ""))


def _avatar_color(name: str) -> str:
    return _AVATAR_COLORS[sum(map(ord, (name or "?").lower())) % len(_AVATAR_COLORS)]


def _date(ts: float) -> str:
    if not ts:
        return "Date unknown"
    when = datetime.fromtimestamp(ts)
    return f"{when:%b} {when.day}" + ("" if when.year == datetime.now().year else f", {when.year}")


def quote_usage() -> Dict[str, int]:
    try:
        from ..statistics_manager import get_statistics_manager

        return dict(get_statistics_manager().data.quotes.individual_quote_usage or {})
    except Exception:
        return {}


def _inject_styles() -> None:
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
    from .chatbot_editor_styles import CHATBOT_EDITOR_CSS
    from .connector_editor_styles import CONNECTOR_EDITOR_CSS, CONNECTOR_EDITOR_MENU_CSS

    inject_page_stylesheet("mycelian-chatbot-qg", promote_color_importance(scope_selectors(QG_CSS, ".cnx")))
    inject_page_stylesheet("mycelian-connector-editor", promote_color_importance(scope_selectors(CONNECTOR_EDITOR_CSS, ".cxe")))
    inject_page_stylesheet("mycelian-connector-editor-menus", CONNECTOR_EDITOR_MENU_CSS)
    inject_page_stylesheet("mycelian-chatbot-editor", promote_color_importance(scope_selectors(CHATBOT_EDITOR_CSS, ".cxe")))
    inject_page_stylesheet("mycelian-chatbot-qg-dialogs", promote_color_importance(scope_selectors(QG_DIALOG_CSS, ".cxe")))


def _toolbar(icon: str, title: str, sub: str, placeholder: str, new_label: str, on_new, on_search, on_refresh,
             extra: Optional[Callable[[], None]] = None) -> ui.label:
    with ui.element("div").classes("cx-bar"):
        _html(f'<span class="cx-bar-icon">{_icon(icon)}</span>')
        with ui.element("div"):
            ui.label(title).classes("cx-bar-title")
            sub_label = ui.label(sub).classes("cx-bar-sub")
        ui.element("div").classes("cx-spacer")
        search = ui.input(placeholder=placeholder).props("outlined dense clearable").classes("cx-search")
        with search.add_slot("prepend"):
            ui.icon("search").classes("text-base")
        search.on_value_change(lambda e: on_search((e.value or "").strip().lower()))
        if extra:
            extra()
        ui.button(icon="refresh", on_click=on_refresh).props("outline dense color=primary").tooltip("Reload")
        ui.button(new_label, icon="add", on_click=on_new).props("unelevated no-caps color=primary")
    return sub_label


def _dialog_shell(title: str, sub: str, icon: str):
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("cxe qg-dlg"):
        with ui.element("div").classes("ce-head"):
            _html(f'<span class="ce-tile">{_ic(icon)}</span>')
            with ui.element("div").classes("ce-head-text"):
                ui.label(title).classes("ce-step-title")
                ui.label(sub).classes("ce-step-sub")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        body = ui.element("div").classes("qg-body")
        foot = ui.element("div").classes("ce-foot")
    return dialog, body, foot


def _seg(options, current: str, on_pick: Callable[[str], None]) -> None:
    box = ui.element("div").classes("ce-seg")

    def render() -> None:
        box.clear()
        with box:
            for value, icon, label in options:
                opt = ui.element("div").classes("is-on" if value == state["value"] else "")
                with opt:
                    _html(_ic(icon))
                    ui.label(label)
                opt.on("click", lambda _e, v=value: pick(v))

    def pick(value: str) -> None:
        state["value"] = value
        on_pick(value)
        render()

    state = {"value": current}
    render()


# ---------------------------------------------------------------------------
#  Quotes
# ---------------------------------------------------------------------------


class QuotesPanel:
    def __init__(self) -> None:
        self.query = ""
        self.sort = "newest"
        self.grid: Optional[ui.element] = None
        self.strip: Optional[ui.element] = None
        self._sub: Optional[ui.label] = None

    def build(self, container: ui.element) -> None:
        _inject_styles()
        container.clear()
        with container:
            with ui.element("div").classes("cnx w-full h-full flex flex-col gap-3"):
                def sort_select() -> None:
                    ui.select(_SORTS, value=self.sort, on_change=lambda e: self._set_sort(e.value)).props(
                        "outlined dense options-dense").classes("cx-search").style("width: 160px").tooltip("Sort")

                self._sub = _toolbar("format_quote", "Quotes", "Memorable lines viewers can call up in chat.", "Search text, author or #",
                                     "New quote", lambda: open_quote_editor(None, self.refresh), self._set_query, self.refresh, sort_select)
                self.strip = ui.element("div").classes("qg-strip")
                with ui.scroll_area().classes("w-full grow"):
                    self.grid = ui.element("div").classes("w-full")
        self.refresh()

    def _set_query(self, query: str) -> None:
        self.query = query
        self._render_grid()

    def _set_sort(self, sort: str) -> None:
        self.sort = sort or "newest"
        self._render_grid()

    def refresh(self) -> None:
        self._render_strip()
        self._render_grid()

    def _render_strip(self) -> None:
        if self.strip is None or self.strip.is_deleted:
            return
        manager = get_manager()
        self.strip.clear()
        sample = next(iter(sorted(manager.get_all_quotes().values(), key=lambda q: q.quote_number)), None)
        if sample is None:
            sample = Quote(quote_number=1, text="Mushrooms are just trees that went to art school.", author="SporeLord", added_by="you")
        with self.strip:
            with ui.element("div").classes("qg-item"):
                ui.switch(value=manager.get_quotes_enabled(), on_change=lambda e: self._quick_enabled(bool(e.value))).props("dense")
                ui.html(f"<b>Quotes {'on' if manager.get_quotes_enabled() else 'off'}</b>", sanitize=False)
            ui.element("div").classes("qg-sep")
            _html('<span class="qg-item">Viewers type <span class="qg-code">!quote</span> <span class="qg-code">!quote 4</span> '
                  '<span class="qg-code">!quote word</span></span>')
            ui.element("div").classes("qg-sep")
            cooldown = manager.get_quote_cooldown()
            _html(f'<span class="qg-item">{_icon("timer")}Cooldown <b>{f"{cooldown} s" if cooldown else "none"}</b></span>')
            ui.element("div").classes("qg-sep")
            _html(f'<span class="qg-item">{_icon("shield")}<span class="qg-code">!add_quote</span> '
                  f'<b>{_esc(_PERMISSION_LABEL.get(manager.add_quote_permission, "Mods"))}</b></span>')
            ui.element("div").classes("qg-sep")
            reply = manager.format_quote_reply(sample)
            _html(f'<span class="qg-item">{_icon("chat")}Replies <span class="qg-fmt" title="{_esc(reply)}">{_esc(reply)}</span></span>')
            ui.element("div").classes("cx-spacer")
            ui.button("Quote settings", icon="tune", on_click=lambda: open_quote_settings(self.refresh)).props(
                "outline no-caps dense color=primary")

    def _quick_enabled(self, enabled: bool) -> None:
        manager = get_manager()
        if manager.save_quote_settings(enabled=enabled, cooldown_seconds=manager.get_quote_cooldown(),
                                       add_permission=manager.add_quote_permission, reply_format=manager.quote_reply_format):
            notify(f"Quotes turned {'on' if enabled else 'off'}", type="success", source="chatbot")
        else:
            notify("", type="error", source="chatbot", title="Couldn't change the quote setting", hint="Try again.")
        self._render_strip()

    def _render_grid(self) -> None:
        if self.grid is None or self.grid.is_deleted:
            return
        manager = get_manager()
        quotes = list(manager.get_all_quotes().values())
        usage = quote_usage()
        if self._sub is not None:
            n = len(quotes)
            self._sub.set_text(f"Memorable lines viewers can call up in chat. {n} quote{'s' if n != 1 else ''}." if n
                               else "Memorable lines viewers can call up in chat.")
        q = self.query
        if q:
            num = q.lstrip("#")
            quotes = [x for x in quotes if (num.isdigit() and x.quote_number == int(num))
                      or q in f"{x.text} {x.author} {x.added_by}".lower()]
        key = {"newest": lambda x: -float(x.date_added or 0), "oldest": lambda x: float(x.date_added or 0),
               "number": lambda x: x.quote_number, "used": lambda x: -usage.get(x.quote_id, 0)}[self.sort]
        quotes.sort(key=key)
        self.grid.clear()
        with self.grid:
            if not manager.get_all_quotes():
                with ui.element("div").classes("cx-empty"):
                    _html(_icon("format_quote"))
                    ui.label("No quotes yet").classes("cx-empty-title")
                    ui.label("Add one here, or let mods add them in chat with !add_quote \"the quote\" author.")
                    ui.button("Add a quote", icon="add", on_click=lambda: open_quote_editor(None, self.refresh)).props("unelevated no-caps color=primary")
                return
            if not quotes:
                with ui.element("div").classes("cx-empty"):
                    _html(_icon("search_off"))
                    ui.label("No quotes match your search.")
                return
            with ui.element("div").classes("cx-grid"):
                for quote in quotes:
                    self._card(quote, usage.get(quote.quote_id, 0))

    def _card(self, quote: Quote, used: int) -> None:
        with ui.element("div").classes("cx-card is-quote"):
            with ui.element("div").classes("cx-head"):
                ui.label(f"#{quote.quote_number}").classes("qg-num")
                with ui.element("div").classes("cx-title"):
                    if quote.author:
                        ui.label(f"— {quote.author}").classes("qg-by")
                    else:
                        ui.label("No author").classes("qg-by is-empty")
                ui.button(icon="send", on_click=lambda q=quote: post_quote(q)).props("flat round dense").tooltip("Post this quote in chat now")
            ui.label(quote.text).classes("qg-quote")
            _html(f'<div class="qg-meta"><span>{_icon("person_add")}Added by {_esc(quote.added_by or "unknown")}</span>'
                  f'<span>{_icon("event")}{_esc(_date(quote.date_added))}</span></div>')
            with ui.element("div").classes("cx-foot"):
                if used:
                    _html(f'<span class="cx-stat">{_icon("bolt")}<b>{used:,}</b>&nbsp;time{"s" if used != 1 else ""}</span>')
                else:
                    _html(f'<span class="cx-stat is-muted">{_icon("schedule")}Never used</span>')
                ui.element("div").classes("cx-spacer")
                ui.button(icon="content_copy", on_click=lambda q=quote: self._duplicate(q)).props("flat round dense").tooltip("Duplicate")
                ui.button(icon="edit", on_click=lambda q=quote: open_quote_editor(q.quote_id, self.refresh)).props("flat round dense").tooltip("Edit")
                with ui.button(icon="more_horiz").props("flat round dense").tooltip("More"):
                    with ui.menu().props("auto-close"):
                        ui.menu_item("Copy the quote text", on_click=lambda q=quote: (
                            ui.clipboard.write(q.text), notify(f"Quote #{q.quote_number} copied", type="success", source="chatbot")))
                        ui.menu_item("Delete…", on_click=lambda q=quote: confirm_delete_quote(q, self.refresh))
        return None

    def _duplicate(self, quote: Quote) -> None:
        ok, error, number = get_manager().add_quote(quote.text, quote.author, quote.added_by or "UI")
        if ok:
            notify(f"Duplicated as quote #{number}", type="success", source="chatbot")
            self.refresh()
        else:
            notify(error or "", type="error", source="chatbot", title="Couldn't duplicate the quote", hint="Try again.")


def post_quote(quote: Quote) -> None:
    text = get_manager().format_quote_reply(quote)

    async def go() -> None:
        from ..chatbot import send_chatbot_message

        ok = await run.io_bound(send_chatbot_message, text)
        if ok:
            notify(f"Quote #{quote.quote_number} posted in chat", type="success", source="chatbot")
        # On failure the chatbot already showed why and how to fix it (one notification).

    background_tasks.create(go(), name="post_quote")


def confirm_delete_quote(quote: Quote, on_done: Callable[[], None]) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
        ui.label(f"Delete quote #{quote.quote_number}?").classes("text-lg font-bold mb-1")
        ui.label(f"“{quote.text[:140]}{'…' if len(quote.text) > 140 else ''}”").classes("secondary-text mb-1")
        ui.label("Its number stays free; use Renumber in Quote settings to close gaps.").classes("text-xs muted-text mb-3")

        def delete() -> None:
            dialog.close()
            if get_manager().delete_quote(quote.quote_id):
                notify(f"Deleted quote #{quote.quote_number}", type="success", source="chatbot")
            else:
                # delete_quote is False when the quote is already gone; the refresh shows that.
                notify("That quote no longer exists. The list was refreshed.", type="info", source="chatbot")
            on_done()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("outline no-caps")
            ui.button("Delete", icon="delete", on_click=delete).props("color=negative unelevated no-caps")
    dialog.open()


def open_quote_editor(quote_id: Optional[str], on_saved: Callable[[], None]) -> None:
    _inject_styles()
    manager = get_manager()
    quote = manager.get_quote_by_id(quote_id) if quote_id else None
    if quote_id and quote is None:
        notify("That quote no longer exists. The list was refreshed.", type="info", source="chatbot")
        on_saved()
        return
    numbers = {q.quote_number for q in manager.get_all_quotes().values() if q is not quote}
    next_number = (max(numbers) + 1) if numbers else 1
    state = {"text": quote.text if quote else "", "author": quote.author if quote else "",
             "added_by": quote.added_by if quote else "", "number": quote.quote_number if quote else next_number}
    used = quote_usage().get(quote.quote_id, 0) if quote else 0
    sub = (f"Added {_date(quote.date_added)} · used {used} time{'s' if used != 1 else ''}" if quote
           else f"Will be quote #{next_number}. Viewers can call it with !quote {next_number}.")
    dialog, body, foot = _dialog_shell(f"Edit quote #{quote.quote_number}" if quote else "New quote", sub, "format_quote")
    with body:
        with ui.element("div").classes("ce-row"):
            ui.label("Number").classes("ce-mini")
            ui.number(value=state["number"], min=1, step=1, format="%d",
                      on_change=lambda e: (state.update(number=int(e.value or 0)), refresh())).props('outlined dense prefix="#"').style("width: 100px")
            number_note = ui.label("").classes("ce-unit")
        with ui.element("div").classes("ce-f"):
            ui.label("Quote").classes("ce-f-label")
            text = ui.textarea(value=state["text"], placeholder="Something memorable…",
                               on_change=lambda e: (state.update(text=e.value or ""), refresh())).props("outlined dense autogrow autofocus")
        with ui.element("div").classes("ce-cmdgrid").style("grid-template-columns: 1fr 1fr"):
            with ui.element("div").classes("ce-f"):
                ui.label("Who said it").classes("ce-f-label")
                ui.input(value=state["author"], placeholder="Optional",
                         on_change=lambda e: (state.update(author=e.value or ""), refresh())).props("outlined dense")
            with ui.element("div").classes("ce-f"):
                ui.label("Added by").classes("ce-f-label")
                ui.input(value=state["added_by"], placeholder="You",
                         on_change=lambda e: (state.update(added_by=e.value or ""), refresh())).props("outlined dense")
        with ui.element("div").classes("ce-preview") as preview:
            _html(_ic("visibility"))
            preview_label = ui.label("")
    with foot:
        status = ui.label("").classes("ce-f-error")
        ui.element("div").classes("ce-spacer")
        if quote:
            ui.button("Delete", icon="delete", on_click=lambda: (dialog.close(), confirm_delete_quote(quote, on_saved))).props(
                "flat no-caps color=negative")
        ui.button("Cancel", on_click=dialog.close).props("outline no-caps color=primary")
        ui.button("Save" if quote else "Add quote", icon="check", on_click=lambda: save()).props("unelevated no-caps color=primary")

    def problem() -> str:
        if not state["text"].strip():
            return "Write the quote."
        if state["number"] < 1:
            return "Use a number of 1 or more."
        if state["number"] in numbers:
            return f"#{state['number']} is already used by another quote."
        return ""

    def refresh() -> None:
        issue = problem()
        number_note.set_text(issue if "number" in issue or "#" in issue else "")
        sample = Quote(quote_number=state["number"], text=state["text"] or "…", author=state["author"], added_by=state["added_by"],
                       date_added=quote.date_added if quote else datetime.now().timestamp())
        preview_label.set_text(manager.format_quote_reply(sample))
        preview.set_visibility(bool(state["text"].strip()))
        status.set_text("")

    def save() -> None:
        issue = problem()
        if issue:
            status.set_text(issue)
            return
        if quote:
            ok = manager.update_quote(quote.quote_id, state["text"], state["author"], number=state["number"], added_by=state["added_by"])
        else:
            ok, error, number = manager.add_quote(state["text"].strip(), state["author"].strip(), state["added_by"].strip() or "UI")
            if ok and number != state["number"]:
                added = manager.get_quote_by_number(number)
                ok = bool(added) and manager.update_quote(added.quote_id, number=state["number"])
        if not ok:
            status.set_text("Couldn't save the quote. Try again.")
            return
        notify(f"Quote #{state['number']} saved" if quote else f"Added quote #{state['number']}", type="success", source="chatbot")
        dialog.close()
        on_saved()

    text.on("keydown.ctrl.enter", save)
    refresh()
    dialog.open()


def open_quote_settings(on_saved: Callable[[], None]) -> None:
    _inject_styles()
    manager = get_manager()
    state = {"enabled": manager.get_quotes_enabled(), "cooldown": manager.get_quote_cooldown(),
             "permission": manager.add_quote_permission, "format": manager.quote_reply_format or DEFAULT_QUOTE_REPLY_FORMAT}
    quotes = sorted(manager.get_all_quotes().values(), key=lambda q: q.quote_number)
    sample = next((q for q in quotes if q.author), quotes[0] if quotes else Quote(
        quote_number=4, text="Chat, I promise this is the last 'one more run'.", author="SporeLord", added_by="MossyMika"))
    variables = [
        {"key": "number", "label": "Quote number", "example": str(sample.quote_number), "group": "Quote"},
        {"key": "text", "label": "The quote", "example": sample.text, "group": "Quote"},
        {"key": "author", "label": "Who said it (left out when empty)", "example": sample.author, "group": "Quote"},
        {"key": "date_added", "label": "When it was added", "example": _date(sample.date_added), "group": "Quote"},
        {"key": "added_by", "label": "Who added it", "example": sample.added_by, "group": "Quote"},
    ]
    kit = VariableKit(lambda _i=None, _e=None: variables, lambda t: manager.format_quote_reply(sample, t))
    dialog, body, foot = _dialog_shell("Quote settings", "How !quote and !add_quote behave in chat", "tune")
    with body:
        with ui.element("div").classes("ce-row"):
            ui.label("Quotes").classes("ce-mini")
            ui.switch(value=state["enabled"], on_change=lambda e: state.update(enabled=bool(e.value))).props("dense")
            ui.label("!quote and !add_quote answer in chat").classes("ce-unit")
        with ui.element("div").classes("ce-row"):
            ui.label("Cooldown").classes("ce-mini")
            ui.number(value=state["cooldown"], min=0, step=1, format="%d", on_change=lambda e: state.update(cooldown=int(e.value or 0))).props(
                "outlined dense").style("width: 80px")
            ui.label("seconds between !quote replies (0 = none)").classes("ce-unit")
        with ui.element("div").classes("ce-row"):
            ui.label("Who can use !add_quote").classes("ce-mini")
            _seg(_PERMISSIONS, state["permission"], lambda v: state.update(permission=v))
        with ui.element("div").classes("ce-box"):
            with ui.element("div").classes("ce-f"):
                ui.label("!quote reply").classes("ce-f-label")
                with ui.element("div").classes("ce-varhost is-area"):
                    fmt = ui.textarea(value=state["format"], on_change=lambda e: (state.update(format=e.value or ""), refresh())).props(
                        "outlined dense autogrow")
                    kit.button(fmt)
                    kit.track(fmt)
            with ui.element("div").classes("ce-preview"):
                _html(_ic("visibility"))
                preview = ui.label("")
            with ui.element("div").classes("ce-row"):
                reset = ui.label("Reset to default").classes("ce-link").style("font-size: .74rem")
                reset.on("click", lambda: fmt.set_value(DEFAULT_QUOTE_REPLY_FORMAT))
                ui.label("When a quote has no author, the “ — {author}” part is left out.").classes("ce-unit")
        gaps = manager.quote_number_gaps()
        if gaps:
            with ui.element("div").classes("ce-box"):
                with ui.element("div").classes("ce-row"):
                    ui.label("Numbering").classes("ce-mini")
                    shown = ", ".join(f"#{n}" for n in gaps[:6]) + ("…" if len(gaps) > 6 else "")
                    ui.label(f"{shown} {'is' if len(gaps) == 1 else 'are'} missing (deleted quotes).").classes("ce-unit")
                    ui.element("div").classes("ce-spacer")

                    def renumber() -> None:
                        changed = manager.renumber_quotes()
                        notify(f"Renumbered {changed} quote{'s' if changed != 1 else ''}", type="success", source="chatbot")
                        dialog.close()
                        on_saved()

                    ui.button(f"Renumber 1–{len(quotes)}", icon="format_list_numbered", on_click=renumber).props(
                        "outline no-caps dense color=primary").tooltip("Number the quotes 1, 2, 3… in their current order")
    with foot:
        ui.element("div").classes("ce-spacer")
        ui.button("Cancel", on_click=dialog.close).props("outline no-caps color=primary")

        def save() -> None:
            if not manager.save_quote_settings(enabled=state["enabled"], cooldown_seconds=state["cooldown"],
                                               add_permission=state["permission"], reply_format=state["format"]):
                notify("Your changes are still in the dialog.", type="error", source="chatbot",
                       title="Couldn't save quote settings", hint="Try again.")
                return
            notify("Quote settings saved", type="success", source="chatbot")
            dialog.close()
            on_saved()

        ui.button("Save settings", icon="check", on_click=save).props("unelevated no-caps color=primary")

    def refresh() -> None:
        preview.set_text(manager.format_quote_reply(sample, state["format"] or DEFAULT_QUOTE_REPLY_FORMAT))

    refresh()
    dialog.open()


# ---------------------------------------------------------------------------
#  Greetings
# ---------------------------------------------------------------------------

def greeting_variables() -> List[dict]:
    out = [
        {"key": "username", "label": "The viewer's display name", "example": "SporeKid", "group": "Viewer"},
        {"key": "user_login", "label": "The viewer's login name", "example": "sporekid", "group": "Viewer"},
        {"key": "user_id", "label": "The viewer's Twitch ID", "example": "123456789", "group": "Viewer"},
    ]
    try:
        from .. import chatbot_variables as cv

        common = [dict(v) for v in cv.COMMON_VARIABLES if v.get("key") not in ("username", "user_login")]
        out += [v for v in common if v.get("group") in ("Basics", "Time")]
        out += [dict(v, group="Custom") for v in cv.custom_variables()]
        out += [v for v in common if v.get("group") not in ("Basics", "Time")]
    except Exception as exc:
        logger.debug("Greeting variables unavailable: %s", exc)
    return out


def render_greeting_preview(text: str, username: str = "SporeKid", login: str = "") -> str:
    try:
        from ..chatbot_custom_variables import preview_mode

        with preview_mode():
            return get_manager().render_greeting(text, "123456789", username, login or username.lower())
    except Exception:
        return text


class GreetingsPanel:
    def __init__(self) -> None:
        self.query = ""
        self.grid: Optional[ui.element] = None
        self.top: Optional[ui.element] = None
        self._sub: Optional[ui.label] = None

    def build(self, container: ui.element) -> None:
        _inject_styles()
        container.clear()
        with container:
            with ui.element("div").classes("cnx w-full h-full flex flex-col gap-3"):
                self._sub = _toolbar("waving_hand", "Greetings", "A welcome message the first time a viewer chats.", "Search viewer or message",
                                     "New greeting", lambda: open_greeting_editor(None, self.refresh), self._set_query, self.refresh)
                with ui.scroll_area().classes("w-full grow"):
                    with ui.element("div").classes("w-full flex flex-col gap-3"):
                        self.top = ui.element("div").classes("w-full flex flex-col gap-3")
                        ui.label("Personal greetings").classes("qg-section")
                        self.grid = ui.element("div").classes("w-full")
        self.refresh()

    def _set_query(self, query: str) -> None:
        self.query = query
        self._render_grid()

    def refresh(self) -> None:
        self._render_top()
        self._render_grid()

    def _render_top(self) -> None:
        if self.top is None or self.top.is_deleted:
            return
        manager = get_manager()
        on = manager.get_default_greeting_enabled()
        self.top.clear()
        with self.top:
            with ui.element("div").classes("qg-default" + ("" if on else " is-off")):
                _html(f'<span class="cx-tile" style="width:40px;height:40px">{_icon("waving_hand")}</span>')
                with ui.element("div"):
                    ui.label("Everyone else").classes("qg-default-t")
                    ui.label(render_greeting_preview(manager.get_default_greeting(), "NewViewer")).classes("qg-default-msg")
                    ui.label("Viewers without their own greeting get this the first time they chat."
                             if on else "Off: viewers without their own greeting aren't greeted.").classes("qg-default-sub")
                with ui.element("div").classes("flex items-center gap-2"):
                    ui.switch(value=on, on_change=lambda e: self._save_quick(default_enabled=bool(e.value))).props("dense color=positive").tooltip(
                        "Greet everyone else")
                    ui.button("Edit", icon="edit", on_click=lambda: open_default_greeting(self.refresh)).props("outline no-caps dense color=primary")
            stats = manager.greeted_since_reset()
            with ui.element("div").classes("qg-strip"):
                with ui.element("div").classes("qg-item"):
                    _html(_icon("history"))
                    ui.label("Greet each viewer once every")
                    hours = manager.get_greeting_reset_interval()
                    choices = dict(_RESET_CHOICES)
                    choices.setdefault(hours, f"{hours} hours")
                    ui.select(choices, value=hours, on_change=lambda e: self._save_quick(reset_interval_hours=int(e.value or 24))).props(
                        "outlined dense options-dense").style("width: 120px")
                ui.element("div").classes("qg-sep")
                since = stats.get("since") or 0
                since_text = f" since {datetime.fromtimestamp(since):%H:%M}" if since and since > datetime.now().timestamp() - 86400 else (
                    f" since {_date(since)}" if since else "")
                _html(f'<span class="qg-item">{_icon("group")}<b>{stats.get("count", 0):,}</b> viewer{"s" if stats.get("count", 0) != 1 else ""} greeted{since_text}</span>')
                ui.button("Greet everyone again", icon="restart_alt", on_click=self._confirm_reset_all).props("outline no-caps dense color=primary").tooltip(
                    "Forget who has been greeted, so everyone is greeted again next time they chat")
                ui.element("div").classes("qg-sep")
                with ui.element("div").classes("qg-item"):
                    ui.switch(value=manager.get_custom_greeting_enabled(),
                              on_change=lambda e: self._save_quick(custom_enabled=bool(e.value))).props("dense")
                    ui.label("Personal greetings on" if manager.get_custom_greeting_enabled() else "Personal greetings off")

    def _save_quick(self, **changes: Any) -> None:
        manager = get_manager()
        values = {"default_enabled": manager.get_default_greeting_enabled(), "custom_enabled": manager.get_custom_greeting_enabled(),
                  "default_text": manager.get_default_greeting(), "reset_interval_hours": manager.get_greeting_reset_interval()}
        values.update(changes)
        if manager.save_greeting_settings(**values):
            notify("Greeting settings saved", type="success", source="chatbot")
        else:
            # Only an empty default greeting is refused; the refresh puts the switch back.
            notify("The greeting for everyone else is empty.", type="warning", source="chatbot",
                   title="Couldn't save greeting settings", hint="Click Edit next to “Everyone else” and write a greeting first.")
        self.refresh()

    def _confirm_reset_all(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
            ui.label("Greet everyone again?").classes("text-lg font-bold mb-1")
            ui.label("Mycelian forgets who it has greeted, so every viewer is greeted again the next time they chat.").classes(
                "secondary-text mb-3")

            def reset() -> None:
                dialog.close()
                get_manager().reset_all_greeting_flags()
                notify("Everyone will be greeted again", type="success", source="chatbot")
                self.refresh()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("outline no-caps")
                ui.button("Greet everyone again", icon="restart_alt", on_click=reset).props("color=primary unelevated no-caps")
        dialog.open()

    def _render_grid(self) -> None:
        if self.grid is None or self.grid.is_deleted:
            return
        greetings = list(get_manager().get_all_greetings().values())
        if self._sub is not None:
            n = len(greetings)
            self._sub.set_text(f"A welcome message the first time a viewer chats. {n} personal greeting{'s' if n != 1 else ''}." if n
                               else "A welcome message the first time a viewer chats.")
        q = self.query
        shown = [g for g in greetings if not q or q in f"{g.username} {g.greeting_text}".lower()]
        shown.sort(key=lambda g: str(g.username).lower())
        self.grid.clear()
        with self.grid:
            if not greetings:
                with ui.element("div").classes("cx-empty"):
                    _html(_icon("waving_hand"))
                    ui.label("No personal greetings yet").classes("cx-empty-title")
                    ui.label("Give regulars their own welcome. Everyone else gets the greeting above.")
                    ui.button("Add a greeting", icon="add", on_click=lambda: open_greeting_editor(None, self.refresh)).props(
                        "unelevated no-caps color=primary")
                return
            if not shown:
                with ui.element("div").classes("cx-empty"):
                    _html(_icon("search_off"))
                    ui.label("No greetings match your search.")
                return
            with ui.element("div").classes("cx-grid"):
                for greeting in shown:
                    self._card(greeting)

    def _card(self, greeting: Greeting) -> None:
        card = ui.element("div").classes("cx-card" + ("" if greeting.enabled else " is-off"))
        name = greeting.username or "?"
        with card:
            with ui.element("div").classes("cx-head"):
                _html(f'<span class="qg-avatar" style="background:{_avatar_color(name)}">{_esc(name[:1].upper())}</span>')
                with ui.element("div").classes("cx-title"):
                    ui.label(name).classes("qg-user")
                    ui.label(f"@{name.lower()}").classes("qg-login")

                def flip(e, g=greeting, c=card) -> None:
                    get_manager().update_greeting(g.greeting_id, enabled=bool(e.value))
                    c.classes(remove="is-off") if e.value else c.classes(add="is-off")

                ui.switch(value=greeting.enabled, on_change=flip).props("dense color=positive").tooltip("Turn this greeting on or off")
            ui.label(render_greeting_preview(greeting.greeting_text, name)).classes("cx-reply")
            with ui.element("div").classes("cx-foot"):
                if greeting.last_greeted:
                    _html(f'<span class="cx-stat">{_icon("waving_hand")}Greeted {_esc(_relative(greeting.last_greeted) or _date(greeting.last_greeted))}</span>')
                else:
                    _html(f'<span class="cx-stat is-muted">{_icon("schedule")}Not greeted yet</span>')
                ui.element("div").classes("cx-spacer")
                ui.button(icon="restart_alt", on_click=lambda g=greeting: self._reset_one(g)).props("flat round dense").tooltip(
                    "Greet again the next time they chat")
                ui.button(icon="edit", on_click=lambda g=greeting: open_greeting_editor(g.greeting_id, self.refresh)).props("flat round dense").tooltip("Edit")
                with ui.button(icon="more_horiz").props("flat round dense").tooltip("More"):
                    with ui.menu().props("auto-close"):
                        ui.menu_item("Delete…", on_click=lambda g=greeting: confirm_delete_greeting(g, self.refresh))

    def _reset_one(self, greeting: Greeting) -> None:
        if get_manager().reset_greeting_for(greeting.greeting_id):
            notify(f"{greeting.username} will be greeted next time they chat", type="success", source="chatbot")
        else:
            notify("That greeting no longer exists. The list was refreshed.", type="info", source="chatbot")
        self.refresh()


def confirm_delete_greeting(greeting: Greeting, on_done: Callable[[], None]) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
        ui.label(f"Delete {greeting.username}'s greeting?").classes("text-lg font-bold mb-1")
        ui.label("They'll get the greeting for everyone else instead.").classes("secondary-text mb-3")

        def delete() -> None:
            dialog.close()
            if get_manager().remove_greeting(greeting.greeting_id):
                notify(f"Deleted {greeting.username}'s greeting", type="success", source="chatbot")
            else:
                notify("That greeting no longer exists. The list was refreshed.", type="info", source="chatbot")
            on_done()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("outline no-caps")
            ui.button("Delete", icon="delete", on_click=delete).props("color=negative unelevated no-caps")
    dialog.open()


def _greeting_text_field(kit: VariableKit, value: str, placeholder: str, on_change: Callable[[str], None]) -> ui.textarea:
    with ui.element("div").classes("ce-varhost is-area"):
        area = ui.textarea(value=value, placeholder=placeholder, on_change=lambda e: on_change(e.value or "")).props("outlined dense autogrow")
        kit.button(area)
        kit.track(area)
    return area


def open_greeting_editor(greeting_id: Optional[str], on_saved: Callable[[], None]) -> None:
    _inject_styles()
    manager = get_manager()
    greeting = manager.get_greeting(greeting_id) if greeting_id else None
    if greeting_id and greeting is None:
        notify("That greeting no longer exists. The list was refreshed.", type="info", source="chatbot")
        on_saved()
        return
    state = {"login": greeting.username if greeting else "", "text": greeting.greeting_text if greeting else "",
             "enabled": greeting.enabled if greeting else True, "user": (greeting.user_id, greeting.username) if greeting else None,
             "checking": 0}
    kit = VariableKit(lambda _i=None, _e=None: greeting_variables(), lambda t: render_greeting_preview(t, display_name()))
    dialog, body, foot = _dialog_shell(f"{greeting.username}'s greeting" if greeting else "New greeting",
                                       "A personal welcome for one viewer", "waving_hand")

    def display_name() -> str:
        return (state["user"] or (None, ""))[1] or state["login"] or "SporeKid"

    with body:
        with ui.element("div").classes("ce-row"):
            ui.label("Twitch viewer").classes("ce-mini")
            login = ui.input(value=state["login"], placeholder="sporekid").props('outlined dense prefix="@" debounce=600' + (" readonly" if greeting else ""))
            login.style("width: 240px")
            found = ui.html("", sanitize=False).classes("qg-found")
        _greeting_text_field(kit, state["text"], "Look who it is! {username} is here 🍄", lambda v: (state.update(text=v), refresh()))
        with ui.element("div").classes("ce-preview") as preview:
            _html(_ic("visibility"))
            preview_label = ui.label("")
        with ui.element("div").classes("ce-row"):
            ui.label("Greeting on").classes("ce-mini")
            ui.switch(value=state["enabled"], on_change=lambda e: state.update(enabled=bool(e.value))).props("dense")
            ui.element("div").classes("ce-spacer")
            ui.label("Uses the same variables as command replies.").classes("ce-unit")
    with foot:
        status = ui.label("").classes("ce-f-error")
        ui.element("div").classes("ce-spacer")
        if greeting:
            ui.button("Delete", icon="delete", on_click=lambda: (dialog.close(), confirm_delete_greeting(greeting, on_saved))).props(
                "flat no-caps color=negative")
        ui.button("Cancel", on_click=dialog.close).props("outline no-caps color=primary")
        save_btn = ui.button("Save" if greeting else "Add greeting", icon="check", on_click=lambda: save()).props("unelevated no-caps color=primary")

    def set_found(kind: str, text: str) -> None:
        icon = {"ok": "verified", "warn": "error_outline", "busy": "hourglass_empty"}.get(kind, "")
        found.classes(remove="is-warn is-busy", add={"warn": "is-warn", "busy": "is-busy"}.get(kind, ""))
        found.set_content(f"{_ic(icon)}<span>{_esc(text)}</span>" if text else "")

    async def check(name: str) -> None:
        name = name.strip().lstrip("@")
        state["checking"] += 1
        token = state["checking"]
        state["user"] = None
        if not name:
            set_found("", "")
            refresh()
            return
        if not _LOGIN_RE.match(name):
            set_found("warn", "That isn't a Twitch name")
            refresh()
            return
        set_found("busy", "Checking Twitch…")
        try:
            from .chatbot import lookup_twitch_user

            user_id, display = await lookup_twitch_user(name)
        except Exception as exc:
            if token == state["checking"]:
                message = str(exc)
                set_found("warn", "Twitch isn't connected" if "not connected" in message.lower() else f"Couldn't find {name} on Twitch")
                refresh()
            return
        if token != state["checking"]:
            return
        existing = manager.get_greeting_by_user_id(user_id)
        if existing is not None:
            set_found("warn", f"{display} already has a greeting")
            refresh()
            return
        state["user"] = (user_id, display)
        set_found("ok", f"Found {display} on Twitch")
        refresh()

    if not greeting:
        login.on_value_change(lambda e: (state.update(login=(e.value or "").strip()), background_tasks.create(check(e.value or ""), name="greeting_lookup")))

    def problem() -> str:
        if not greeting and not state["user"]:
            return "Enter a Twitch name that was found."
        if not state["text"].strip():
            return "Write the greeting."
        return ""

    def refresh() -> None:
        preview_label.set_text(render_greeting_preview(state["text"], display_name()))
        preview.set_visibility(bool(state["text"].strip()))
        save_btn.props(remove="disable") if not problem() else save_btn.props(add="disable")
        status.set_text("")

    def save() -> None:
        issue = problem()
        if issue:
            status.set_text(issue)
            return
        if greeting:
            ok = manager.update_greeting(greeting.greeting_id, state["text"].strip(), state["enabled"])
            error = ""
        else:
            user_id, display = state["user"]
            ok, error, _gid = manager.add_greeting(user_id, display, state["text"].strip(), state["enabled"])
        if not ok:
            status.set_text(error or "Couldn't save the greeting. Try again.")
            return
        notify(f"Greeting for {state['user'][1] if state.get('user') else state['login']} saved", type="success", source="chatbot")
        dialog.close()
        on_saved()

    refresh()
    dialog.open()


def open_default_greeting(on_saved: Callable[[], None]) -> None:
    _inject_styles()
    manager = get_manager()
    state = {"text": manager.get_default_greeting(), "hours": manager.get_greeting_reset_interval(),
             "enabled": manager.get_default_greeting_enabled()}
    kit = VariableKit(lambda _i=None, _e=None: greeting_variables(), lambda t: render_greeting_preview(t, "NewViewer"))
    dialog, body, foot = _dialog_shell("Greeting for everyone else", "Sent the first time a viewer without a personal greeting chats", "waving_hand")
    with body:
        _greeting_text_field(kit, state["text"], "Welcome to the stream, {username}!", lambda v: (state.update(text=v), refresh()))
        with ui.element("div").classes("ce-preview"):
            _html(_ic("visibility"))
            preview = ui.label("")
        with ui.element("div").classes("ce-row"):
            ui.label("Greet each viewer once every").classes("ce-mini")
            ui.number(value=state["hours"], min=1, step=1, format="%d", on_change=lambda e: state.update(hours=int(e.value or 24))).props(
                "outlined dense").style("width: 80px")
            ui.label("hours").classes("ce-unit")
        with ui.element("div").classes("ce-row"):
            ui.label("Greet everyone else").classes("ce-mini")
            ui.switch(value=state["enabled"], on_change=lambda e: state.update(enabled=bool(e.value))).props("dense")
    with foot:
        status = ui.label("").classes("ce-f-error")
        ui.element("div").classes("ce-spacer")
        ui.button("Cancel", on_click=dialog.close).props("outline no-caps color=primary")

        def save() -> None:
            if not state["text"].strip():
                status.set_text("Write the greeting.")
                return
            if not manager.save_greeting_settings(default_enabled=state["enabled"], custom_enabled=manager.get_custom_greeting_enabled(),
                                                  default_text=state["text"], reset_interval_hours=state["hours"]):
                status.set_text("Couldn't save the greeting. Try again.")
                return
            notify("Greeting for everyone else saved", type="success", source="chatbot")
            dialog.close()
            on_saved()

        ui.button("Save", icon="check", on_click=save).props("unelevated no-caps color=primary")

    def refresh() -> None:
        preview.set_text(render_greeting_preview(state["text"], "NewViewer"))

    refresh()
    dialog.open()


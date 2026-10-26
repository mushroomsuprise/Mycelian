# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared look and small helpers for the Chatbot Giveaways and Polls tabs.

The panels sit inside the Connectors / chatbot card styles (``.cnx``). ``GP_CSS`` adds the
status hero, the side cards, the entrant grid and the poll bars, using theme variables only.
"""

from __future__ import annotations

import html as html_lib
import webbrowser
from typing import Any, Callable, Optional

from nicegui import ui

from ..notification_engine import notify
from .editor_variables import raw_html

GP_CSS = """
.gp-wrap { display: flex; flex-direction: column; gap: 12px; padding-bottom: 12px; }
.gp-wrap > :empty, .gp-col > :empty { display: none; }
.gp-hero { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 12px 14px; border-radius: 12px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.gp-hero.is-live { background: linear-gradient(90deg, color-mix(in srgb, var(--color-success) 14%, var(--color-bg-surface)), var(--color-bg-surface) 70%);
    border-color: color-mix(in srgb, var(--color-success) 45%, transparent); }
.gp-hero.is-closed { background: linear-gradient(90deg, color-mix(in srgb, var(--color-warning) 12%, var(--color-bg-surface)), var(--color-bg-surface) 70%);
    border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); }
.gp-state { width: 44px; height: 44px; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.gp-state .cx-icon { font-size: 24px; }
.gp-hero.is-live .gp-state { color: var(--color-success); background: color-mix(in srgb, var(--color-success) 14%, transparent);
    border-color: color-mix(in srgb, var(--color-success) 40%, transparent); }
.gp-hero.is-closed .gp-state { color: var(--color-warning); background: color-mix(in srgb, var(--color-warning) 14%, transparent);
    border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); }
.gp-hero-text { min-width: 0; flex: 0 1 auto; }
.gp-kicker { font-size: .66rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--color-text-muted); display: flex; align-items: center; gap: 6px; }
.gp-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-success); box-shadow: 0 0 0 4px color-mix(in srgb, var(--color-success) 25%, transparent); }
.gp-head { font-size: 1.15rem; font-weight: 750; color: var(--color-text-primary); overflow-wrap: anywhere; }
.gp-head code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 1.05rem; color: var(--color-primary); padding: 0 6px;
    border-radius: 6px; background: var(--color-primary-light); }
.gp-sub { font-size: .76rem; color: var(--color-text-secondary); margin-top: 1px; }
.gp-sub b { color: var(--color-text-primary); }
.gp-stats { display: flex; align-items: center; }
.gp-stat { display: flex; flex-direction: column; align-items: center; padding: 0 14px; border-left: 1px solid var(--color-border-subtle); max-width: 220px; }
.gp-stat b { font-size: 1.35rem; font-weight: 800; color: var(--color-text-primary); line-height: 1.1; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gp-stat span { font-size: .66rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }
.gp-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.gp-actions .q-btn { min-height: 38px; border-radius: 9px; font-size: .86rem; font-weight: 700; padding: 0 14px; }
.gp-timerbar { height: 6px; border-radius: 3px; background: color-mix(in srgb, var(--color-success) 20%, transparent); overflow: hidden; width: 220px; margin-top: 6px; }
.gp-timerbar i { display: block; height: 100%; background: var(--color-success); transition: width .9s linear; }
.gp-alert { display: flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 10px; font-size: .78rem; color: var(--color-text-primary);
    background: color-mix(in srgb, var(--color-warning) 12%, var(--color-bg-surface)); border: 1px solid color-mix(in srgb, var(--color-warning) 40%, transparent); }
.gp-alert .cx-icon { color: var(--color-warning); font-size: 17px; }

.gp-cols { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 12px; align-items: start; }
@media (max-width: 1100px) { .gp-cols { grid-template-columns: minmax(0, 1fr); } }
.gp-col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.gp-card { border-radius: 12px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); overflow: hidden; }
.gp-card-h { display: flex; align-items: center; gap: 8px; min-height: 42px; padding: 6px 12px; border-bottom: 1px solid var(--color-border-subtle);
    font-size: .82rem; font-weight: 700; color: var(--color-text-primary); }
.gp-card-h > .cx-icon { font-size: 17px; color: var(--color-primary); }
.gp-card-h .q-btn { min-height: 28px; font-size: .74rem; }
.gp-card-b { padding: 10px 12px; display: flex; flex-direction: column; gap: 9px; }
.gp-badge { font-size: .7rem; font-weight: 700; padding: 1px 8px; border-radius: 999px; color: var(--color-primary); background: var(--color-primary-light); white-space: nowrap; }
.gp-badge.is-live { color: var(--color-success); background: color-mix(in srgb, var(--color-success) 14%, transparent); }
.gp-row { display: flex; align-items: center; gap: 8px; font-size: .78rem; color: var(--color-text-secondary); }
.gp-row b { color: var(--color-text-primary); font-weight: 650; }
.gp-row > .cx-icon { font-size: 16px; color: var(--color-text-muted); }
.gp-hint { font-size: .72rem; color: var(--color-text-muted); line-height: 1.45; }
.gp-hint .cx-icon { font-size: 14px; vertical-align: -3px; margin-right: 3px; }
.gp-lbl { font-size: .66rem; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .05em; }
.gp-grow { flex: 1 1 auto; min-width: 0; }
.gp-mono input, .gp-mono .q-field__native { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 700; color: var(--color-primary); }
.gp-mono input::placeholder { color: var(--color-text-muted); font-weight: 500; opacity: .7; }
.gp-card .q-field__control { min-height: 32px; border-radius: 8px; }
.gp-card .q-field:not(.q-textarea) .q-field__control { height: 32px; }
.gp-card .q-field__marginal { height: 32px; }
.gp-card .q-field__native { min-height: 32px; padding: 0; font-size: .84rem; }
.gp-card .q-textarea .q-field__native { padding: 7px 0; line-height: 1.5; }
.gp-card .q-field--outlined .q-field__control { padding: 0 8px 0 10px; }
.gp-card .q-field--with-bottom { padding-bottom: 0; }
.gp-card .q-field__bottom { display: none; }
.gp-step { display: inline-flex; align-items: center; border-radius: 8px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); overflow: hidden; }
.gp-step .q-btn { min-height: 30px; width: 30px; border-radius: 0; color: var(--color-primary); background: var(--color-primary-light); }
.gp-step-v { min-width: 38px; text-align: center; font-weight: 700; font-size: .86rem; color: var(--color-text-primary); }
.gp-toggle { display: flex; align-items: center; gap: 8px; font-size: .78rem; color: var(--color-text-primary); padding: 1px 0; }
.gp-toggle small { display: block; font-size: .68rem; color: var(--color-text-muted); }
.gp-toggle.is-disabled { opacity: .5; }
.gp-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.gp-chip { display: inline-flex; align-items: center; gap: 2px; padding: 1px 2px 1px 9px; border-radius: 999px; font-size: .72rem;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary); }
.gp-chip .q-btn { min-height: 18px; min-width: 18px; padding: 0; color: var(--color-text-muted); }
.gp-url { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; padding: 6px 8px; border-radius: 7px; cursor: pointer;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary); display: flex; align-items: center; gap: 6px; }
.gp-url:hover { border-color: var(--color-border-accent); }
.gp-url .gp-url-t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gp-url .cx-icon { font-size: 15px; color: var(--color-primary); }
.gp-btns { display: flex; gap: 6px; flex-wrap: wrap; }
.gp-btns .q-btn { min-height: 28px; font-size: .74rem; }
.gp-preview { display: flex; align-items: flex-start; gap: 6px; font-size: .76rem; line-height: 1.4; padding: 6px 10px; border-radius: 7px; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px dashed var(--color-border-default); overflow-wrap: anywhere; }
.gp-preview .cx-icon { font-size: 14px; color: var(--color-text-muted); margin-top: 1px; }
.gp-card .ce-varhost { position: relative; }
.gp-card .ce-varhost .ce-varbtn { position: absolute; right: 5px; top: 5px; z-index: 2; }
.gp-card .ce-varhost .q-field__control { padding-right: 44px !important; }
.gp-card .ce-varbtn { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; font-weight: 700; border-radius: 6px;
    background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.gp-card .ce-varbtn.q-btn { min-height: 22px; padding: 0 7px; line-height: 20px; }
.gp-card .ce-varbtn.q-btn .q-btn__content { color: var(--color-primary); }
.gp-empty { padding: 30px 16px; text-align: center; color: var(--color-text-muted); font-size: .8rem; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.gp-empty .cx-icon { font-size: 30px; }
.gp-seg { display: inline-flex; padding: 2px; border-radius: 8px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); gap: 2px; }
.gp-seg > div { padding: 3px 10px; border-radius: 6px; font-size: .74rem; font-weight: 600; color: var(--color-text-muted); cursor: pointer; user-select: none; }
.gp-seg > div:hover { color: var(--color-text-primary); }
.gp-seg > div.is-on { background: var(--color-primary); color: #fff; }

/* Giveaway entrants */
.gp-search.q-field { width: 200px; }
.gp-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 4px; padding: 8px; max-height: 470px; overflow: auto;
    background: var(--color-bg-base); align-content: start; }
.gp-entrant { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 6px 0 8px; border-radius: 8px; font-size: .8rem;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.gp-entrant:hover { border-color: var(--color-border-accent); }
.gp-av { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .66rem; font-weight: 800;
    color: #fff; flex: 0 0 auto; }
.gp-entrant .gp-name { font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-primary); }
.gp-entrant .gp-when { font-size: .66rem; color: var(--color-text-muted); white-space: nowrap; }
.gp-entrant .gp-tools { display: none; gap: 0; }
.gp-entrant:hover .gp-tools { display: flex; }
.gp-entrant:hover .gp-when { display: none; }
.gp-entrant .gp-tools .q-btn { min-height: 22px; min-width: 22px; padding: 0; color: var(--color-text-muted); }
.gp-entrant .gp-tools .q-btn:hover { color: var(--color-error); }
.gp-entrant.is-won { border-color: color-mix(in srgb, var(--color-warning) 60%, transparent); background: color-mix(in srgb, var(--color-warning) 10%, var(--color-bg-surface)); }
.gp-entrant.is-won .gp-trophy { color: var(--color-warning); font-size: 15px; }
.gp-more { grid-column: 1 / -1; text-align: center; font-size: .72rem; color: var(--color-text-muted); padding: 6px; }
.gp-winner { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; font-size: .95rem; font-weight: 750; color: var(--color-text-primary);
    background: linear-gradient(90deg, color-mix(in srgb, var(--color-warning) 18%, var(--color-bg-base)), var(--color-bg-base));
    border: 1px solid color-mix(in srgb, var(--color-warning) 45%, transparent); overflow-wrap: anywhere; }
.gp-winner .cx-icon { color: var(--color-warning); font-size: 20px; }

/* Poll options and results */
.gp-opt { display: grid; grid-template-columns: 24px minmax(0, 1fr) 170px 86px 28px 28px 28px; gap: 6px; align-items: center; }
.gp-opt.is-head { font-size: .64rem; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .04em; }
.gp-opt-n { width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: .7rem; font-weight: 800;
    color: var(--color-primary); background: var(--color-primary-light); }
.gp-opt .q-btn { min-height: 28px; min-width: 28px; padding: 0; }
.gp-auto { font-size: .6rem; font-weight: 800; color: var(--color-success); letter-spacing: .04em; }
.gp-add { display: flex; align-items: center; justify-content: center; gap: 6px; height: 34px; border-radius: 8px; cursor: pointer; font-size: .8rem; font-weight: 650;
    color: var(--color-primary); border: 1px dashed var(--color-border-accent); }
.gp-add:hover { background: var(--color-primary-light); }
.gp-add .cx-icon { font-size: 18px; }
.gp-add.is-disabled { opacity: .45; cursor: not-allowed; }
.gp-add.is-disabled:hover { background: none; }
.gp-bars { display: flex; flex-direction: column; gap: 10px; }
.gp-bar-top { display: flex; align-items: baseline; gap: 8px; font-size: .9rem; font-weight: 650; color: var(--color-text-primary); margin-bottom: 4px; }
.gp-bar-top code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; color: var(--color-text-muted); }
.gp-bar-top span { margin-left: auto; font-variant-numeric: tabular-nums; font-size: .82rem; color: var(--color-text-secondary); white-space: nowrap; }
.gp-bar-track { height: 14px; border-radius: 7px; overflow: hidden; }
.gp-bar-fill { height: 100%; border-radius: 7px; transition: width .6s ease; }
.gp-widget { padding: 12px; border-radius: 10px; font-family: Roboto, sans-serif; }
/* The widget's own text colour, kept over the app's light-mode text rule. */
.gp-widget, .gp-widget * { color: var(--gp-widget-text) !important; }
.gp-widget-row { margin-bottom: 7px; font-size: .74rem; }
.gp-widget-row:last-child { margin-bottom: 0; }
.gp-widget-top { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 3px; }
.gp-widget-top span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gp-widget-track { height: 7px; border-radius: 4px; overflow: hidden; }
.gp-widget-fill { height: 100%; border-radius: inherit; }
.gp-widget-title { font-weight: 800; font-size: .9rem; margin-bottom: 8px; }
.gp-widget-kw { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .62rem; font-weight: 700; padding: 0 5px;
    margin-left: 6px; border-radius: 4px; background: rgba(255,255,255,0.12); }
.gp-picker.q-field { width: 190px; }
"""

AVATAR_COLORS = ("#8b5cf6", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#14b8a6", "#6366f1")


def icon(name: str) -> str:
    return f'<span class="cx-icon">{name}</span>'


def esc(text: Any) -> str:
    return html_lib.escape(str(text if text is not None else ""))


def avatar_color(name: str) -> str:
    return AVATAR_COLORS[sum(map(ord, (name or "?").lower())) % len(AVATAR_COLORS)]


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def inject_styles() -> None:
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
    from .connector_editor_styles import CONNECTOR_EDITOR_MENU_CSS

    inject_page_stylesheet("mycelian-chatbot-gp", promote_color_importance(scope_selectors(GP_CSS, ".cnx")))
    inject_page_stylesheet("mycelian-connector-editor-menus", CONNECTOR_EDITOR_MENU_CSS)


def overlay_url(template: str) -> str:
    """The address OBS loads for ``template`` (the overlay server's local URL)."""
    host, port = "127.0.0.1", 5000
    try:
        from .. import web_engine

        engine = web_engine.web_engine_instance
        if engine is not None:
            port = int(getattr(engine, "port", port) or port)
            host = engine._local_connect_host()
    except Exception:
        pass
    return f"http://{host}:{port}/{template}"


def open_in_spore_studio(template: str) -> None:
    from ..help_system.contextual_help import navigate_to_main_tab
    from .spore_studio.editor_view import open_template

    navigate_to_main_tab("Spore Studio")
    if template:
        open_template(template)


def copy_text(text: str, message: str = "Copied") -> None:
    ui.clipboard.write(text)
    notify(message, type="success", source="chatbot")


def card(title: str, icon_name: str, badge: str = "", badge_live: bool = False) -> tuple:
    """A side card; returns (header, body) so callers can add header buttons and content."""
    with ui.element("div").classes("gp-card") as box:
        with ui.element("div").classes("gp-card-h") as head:
            raw_html(icon(icon_name))
            ui.label(title)
            if badge:
                ui.label(badge).classes("gp-badge" + (" is-live" if badge_live else ""))
            ui.element("div").classes("cx-spacer")
        body = ui.element("div").classes("gp-card-b")
    box.props(f'data-card="{esc(title)}"')
    return head, body


def toggle(label: str, hint: str, value: bool, on_change: Callable[[bool], None], enabled: bool = True) -> ui.switch:
    with ui.element("div").classes("gp-toggle" + ("" if enabled else " is-disabled")):
        switch = ui.switch(value=bool(value), on_change=lambda e: on_change(bool(e.value))).props("dense")
        with ui.element("div"):
            ui.label(label)
            if hint:
                ui.label(hint).classes("gp-hint").style("display:block")
    if not enabled:
        switch.disable()
    return switch


def seg(options: dict, current: Any, on_pick: Callable[[Any], None]) -> ui.element:
    box = ui.element("div").classes("gp-seg")
    state = {"value": current}

    def render() -> None:
        box.clear()
        with box:
            for value, label in options.items():
                opt = ui.element("div").classes("is-on" if value == state["value"] else "")
                with opt:
                    ui.label(label)
                opt.on("click", lambda _e, v=value: pick(v))

    def pick(value: Any) -> None:
        if value == state["value"]:
            return
        state["value"] = value
        render()
        on_pick(value)

    render()
    return box


def overlay_rows(template: str, on_customize: Callable[[], None], customize_label: str, customize_icon: str) -> None:
    """URL row (click to copy) plus Customize and Preview buttons, inside a card body."""
    url = overlay_url(template)
    row = ui.element("div").classes("gp-url").tooltip("Copy the address to add as a Browser Source in OBS")
    with row:
        raw_html(f'<span class="gp-url-t">{esc(url)}</span>{icon("content_copy")}')
    row.on("click", lambda: copy_text(url, "Overlay address copied"))
    with ui.element("div").classes("gp-btns"):
        ui.button(customize_label, icon=customize_icon, on_click=on_customize).props("outline no-caps dense color=primary")
        ui.button("Preview", icon="open_in_new", on_click=lambda: webbrowser.open(url, new=2)).props(
            "outline no-caps dense color=primary").tooltip("Open the overlay in your browser")


def ago(seconds: float) -> str:
    """Short elapsed time: just now, 4 min, 2 h 5 min."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def clock(seconds: Optional[float]) -> str:
    try:
        total = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds and seconds % 3600 == 0:
        return plural(seconds // 3600, "hour")
    if seconds and seconds % 60 == 0:
        return plural(seconds // 60, "minute")
    return plural(seconds, "second")

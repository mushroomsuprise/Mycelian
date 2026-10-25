# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The notification centre: the bell in the header, its unread badge and the side panel.

History, delivery and actions live in :mod:`notification_engine`; this module only draws.
"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from nicegui import ui

from . import notification_engine as ne
from .notify_settings import LEVEL_LABELS, SOURCE_LABELS, level_of, source_label

logger = logging.getLogger(__name__)

NC_CSS = """
.ncx-card { width: min(100vw, 430px); max-width: 100vw; height: 100%; padding: 0 !important; gap: 0 !important; display: flex; flex-direction: column;
  border-radius: 0 !important; border-left: 1px solid var(--color-border-default); background: var(--color-bg-surface); }
.ncx-head { display: flex; align-items: center; gap: 8px; padding: 12px 10px 10px 16px; }
.ncx-title { font-size: 1.05rem; font-weight: 700; color: var(--color-text-primary); }
.ncx-pill { display: inline-flex; align-items: center; height: 20px; padding: 0 8px; border-radius: 999px; font-size: .68rem; font-weight: 700;
  color: var(--color-text-muted); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ncx-spacer { flex: 1 1 auto; }
.ncx-link { font-size: .76rem; font-weight: 650; cursor: pointer; color: color-mix(in srgb, var(--color-primary) 55%, var(--color-text-primary)); }
.ncx-link:hover { text-decoration: underline; }
.ncx-chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 12px 10px 16px; }
.ncx-chip { --c: var(--color-text-muted); display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border-radius: 999px; cursor: pointer;
  font-size: .74rem; font-weight: 650; color: var(--color-text-secondary); border: 1px solid var(--color-border-default); user-select: none; }
.ncx-chip:hover { color: var(--color-text-primary); }
.ncx-chip .d { width: 7px; height: 7px; border-radius: 50%; background: var(--c); }
.ncx-chip b { font-weight: 800; color: var(--color-text-muted); }
.ncx-chip.is-on { color: var(--color-text-primary); background: var(--color-bg-base); border-color: var(--color-border-accent); }
.ncx-search { padding: 0 12px 10px 16px; }
.ncx-search > *, .ncx-search .q-field { width: 100% !important; }
.ncx-search .q-field__control { height: 32px; min-height: 32px; border-radius: 8px; }
.ncx-search .q-field__native, .ncx-search .q-field__marginal { height: 32px; min-height: 32px; font-size: .8rem; }
.ncx-scroll { flex: 1 1 auto; min-height: 0; border-top: 1px solid var(--color-border-subtle); }
.ncx-group { padding: 10px 16px 4px; font-size: .64rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--color-text-muted); }
.ncx-item { --c: var(--color-notify-info, var(--color-info)); position: relative; display: grid; grid-template-columns: auto minmax(0,1fr) auto; gap: 2px 10px;
  padding: 9px 12px 10px 16px; }
.ncx-item:hover { background: var(--color-bg-base); }
.ncx-item.is-error { --c: var(--color-notify-error, var(--color-error)); }
.ncx-item.is-warning { --c: var(--color-notify-warning, var(--color-warning)); }
.ncx-item.is-success { --c: var(--color-notify-success, var(--color-success)); }
.ncx-item.is-click { cursor: pointer; }
.ncx-ico { grid-row: span 3; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--c);
  background: color-mix(in srgb, var(--c) 15%, transparent); }
.ncx-ico .ncx-i { font-size: 16px; }
.ncx-item .ncx-ico, .ncx-item .ncx-ico .ncx-i { color: var(--c) !important; }
.ncx-hint .ncx-i { color: var(--color-warning) !important; }
.ncx-i { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap;
  font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }
.ncx-t { font-size: .84rem; font-weight: 650; line-height: 1.3; min-width: 0; overflow-wrap: anywhere; color: var(--color-text-primary); }
.ncx-time { font-size: .68rem; color: var(--color-text-muted); white-space: nowrap; }
.ncx-m { grid-column: 2 / span 2; font-size: .76rem; line-height: 1.4; color: var(--color-text-secondary); overflow-wrap: anywhere; white-space: pre-line;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ncx-hint { grid-column: 2 / span 2; display: flex; gap: 6px; margin-top: 4px; font-size: .74rem; line-height: 1.4; color: var(--color-text-secondary); }
.ncx-hint .ncx-i { font-size: 14px; color: var(--color-warning); margin-top: 1px; }
.ncx-meta { grid-column: 2 / span 2; display: flex; align-items: center; gap: 6px; margin-top: 2px; font-size: .68rem; color: var(--color-text-muted); }
.ncx-meta .src { font-weight: 700; }
.ncx-meta .tag { padding: 0 6px; border-radius: 999px; border: 1px solid var(--color-border-subtle); }
.ncx-unread { position: absolute; left: 6px; top: 20px; width: 6px; height: 6px; border-radius: 50%; background: var(--color-primary); }
.ncx-tools { grid-column: 2 / span 2; display: none; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.ncx-item:hover .ncx-tools, .ncx-item:focus-within .ncx-tools { display: flex; }
.ncx-tools .q-btn { min-height: 26px; padding: 0 8px; font-size: .72rem; font-weight: 650; }
.ncx-empty { padding: 40px 16px; text-align: center; font-size: .82rem; color: var(--color-text-muted); }
.ncx-empty .ncx-i { display: block; font-size: 34px; margin-bottom: 6px; }
.ncx-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 16px; border-top: 1px solid var(--color-border-subtle);
  font-size: .74rem; color: var(--color-text-muted); }
.ncx-spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid color-mix(in srgb, var(--c) 30%, transparent); border-top-color: var(--c);
  animation: ncx-spin .9s linear infinite; }
@keyframes ncx-spin { to { transform: rotate(360deg); } }
"""

_ICONS = {"error": "error", "warning": "warning", "info": "info", "success": "check_circle"}
_FILTERS = (("", "All", ""), ("error", "Errors", "error"), ("warning", "Warnings", "warning"), ("info", "Info", "info"), ("success", "Done", "success"))
_MAX_SHOWN = 200


class _CenterState:
    last_read_ts: float = time.time()
    level_filter: str = ""
    query: str = ""
    dialog: Optional[ui.dialog] = None
    chips: Optional[ui.element] = None
    listing: Optional[ui.element] = None
    scroll: Optional[ui.scroll_area] = None
    new_pill: Optional[ui.label] = None
    foot_count: Optional[ui.label] = None
    badge: Optional[ui.label] = None


S = _CenterState()


def _entry_level(entry: Dict[str, Any]) -> str:
    return str(entry.get("level") or level_of(entry.get("type")))


def panel_is_open() -> bool:
    dlg = S.dialog
    if dlg is None or getattr(dlg, "is_deleted", False):
        return False
    return bool(getattr(dlg, "value", False))


def unread_count() -> int:
    if panel_is_open():
        return 0
    return sum(1 for e in ne.get_history() if float(e.get("ts") or 0) > S.last_read_ts)


def _relative(ts: float) -> str:
    delta = max(0, int(time.time() - ts))
    if delta < 45:
        return "now"
    if delta < 3600:
        return f"{max(1, delta // 60)} min"
    if delta < 86400:
        return f"{delta // 3600} h"
    when = datetime.fromtimestamp(ts)
    return f"{when:%b} {when.day}"


def _group_label(ts: float) -> str:
    day = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    return "Earlier"


def _matches(entry: Dict[str, Any]) -> bool:
    level = _entry_level(entry)
    if S.level_filter and (level if level != "ongoing" else "info") != S.level_filter:
        return False
    if S.query:
        blob = " ".join(str(entry.get(k) or "") for k in ("title", "message", "hint")) + " " + source_label(entry.get("source"))
        if S.query not in blob.lower():
            return False
    return True


def update_badge() -> None:
    ref = S.badge
    if ref is None or getattr(ref, "is_deleted", False):
        return
    n = unread_count()
    if n <= 0:
        ref.set_text("")
        ref.classes(add="hidden")
        return
    text = "999+" if n > 999 else str(n)
    ref.set_text(text)
    ref.classes(remove="hidden badge-digits-1 badge-digits-2 badge-digits-3 badge-digits-4",
                add=f"badge-digits-{min(4, len(text))}")


def mark_all_read() -> None:
    S.last_read_ts = time.time()
    update_badge()
    render()


def render() -> None:
    """Rebuild the chips and the list (cheap; the history holds at most 200 entries)."""
    listing = S.listing
    if listing is None or getattr(listing, "is_deleted", False):
        return
    history = list(reversed(ne.get_history()))
    counts: Dict[str, int] = {key: 0 for key, _l, _c in _FILTERS}
    for entry in history:
        level = _entry_level(entry)
        level = "info" if level == "ongoing" else level
        counts[""] += 1
        if level in counts:
            counts[level] += 1
    if S.chips is not None and not getattr(S.chips, "is_deleted", False):
        S.chips.clear()
        with S.chips:
            for key, label, color in _FILTERS:
                chip = ui.element("div").classes("ncx-chip" + (" is-on" if S.level_filter == key else ""))
                if color:
                    chip.style(f"--c: var(--color-notify-{color})")
                with chip:
                    ui.html(("<span class='d'></span>" if color else "") + f"{label} <b>{counts[key]}</b>", sanitize=False).classes("contents")
                chip.on("click", lambda _e, k=key: _set_filter(k))
    unread = sum(1 for e in history if float(e.get("ts") or 0) > S.last_read_ts)
    if S.new_pill is not None and not getattr(S.new_pill, "is_deleted", False):
        S.new_pill.set_text(f"{unread} new")
        S.new_pill.set_visibility(unread > 0)
    shown = [e for e in history if _matches(e)]
    if S.foot_count is not None and not getattr(S.foot_count, "is_deleted", False):
        S.foot_count.set_text(f"Showing {min(len(shown), _MAX_SHOWN)} of {len(history)}")
    listing.clear()
    with listing:
        if not shown:
            text = "No notifications yet" if not history else "Nothing matches these filters"
            ui.html(f'<div class="ncx-empty"><span class="ncx-i">notifications_none</span>{text}</div>', sanitize=False)
            return
        group = None
        for entry in shown[:_MAX_SHOWN]:
            label = _group_label(float(entry.get("ts") or 0))
            if label != group:
                group = label
                ui.label(label).classes("ncx-group")
            _item(entry)


def _set_filter(key: str) -> None:
    S.level_filter = key
    render()


def _set_query(value: str) -> None:
    S.query = (value or "").strip().lower()
    render()


def _item(entry: Dict[str, Any]) -> None:
    level = _entry_level(entry)
    eid = str(entry.get("id") or "")
    actions: List[Dict[str, Any]] = list(entry.get("actions") or [])
    nav = next((a for a in actions if a.get("kind") == "navigate"), None)
    title = entry.get("title") or entry.get("message") or ""
    message = entry.get("message") if entry.get("title") else ""
    count = int(entry.get("count") or 1)
    item = ui.element("div").classes(f"ncx-item is-{'info' if level == 'ongoing' else level}" + (" is-click" if nav else ""))
    with item:
        if float(entry.get("ts") or 0) > S.last_read_ts:
            ui.element("span").classes("ncx-unread")
        icon = '<span class="ncx-spin"></span>' if level == "ongoing" else f'<span class="ncx-i">{_ICONS.get(level, "info")}</span>'
        ui.html(f'<div class="ncx-ico">{icon}</div>', sanitize=False).classes("contents")
        ui.label(title).classes("ncx-t")
        ui.label(_relative(float(entry.get("ts") or 0))).classes("ncx-time").tooltip(
            datetime.fromtimestamp(float(entry.get("ts") or 0)).strftime("%b %d, %Y %H:%M:%S"))
        if message:
            ui.label(message).classes("ncx-m")
        if entry.get("hint"):
            ui.html(f'<div class="ncx-hint"><span class="ncx-i">lightbulb</span><span>{html.escape(str(entry["hint"]))}</span></div>',
                    sanitize=False).classes("contents")
        meta = f'<span class="src">{html.escape(SOURCE_LABELS.get(str(entry.get("source") or ""), "App"))}</span>'
        if count > 1:
            meta += f"<span>· {count} times</span>"
        if entry.get("muted"):
            meta += '<span class="tag">Muted</span>'
        ui.html(f'<div class="ncx-meta">{meta}</div>', sanitize=False).classes("contents")
        with ui.element("div").classes("ncx-tools"):
            for index, action in enumerate(actions):
                if action.get("kind") not in ("navigate", "call"):
                    continue
                if action.get("kind") == "call" and eid not in ne._call_actions:
                    continue
                ui.button(ne._action_label(action), icon=ne._action_icon(action),
                          on_click=lambda _e, i=index: _run(eid, i)).props("outline dense no-caps color=primary")
            ui.button("Copy", icon="content_copy", on_click=lambda: _copy(eid)).props("flat dense no-caps")
            ui.button("Dismiss", icon="close", on_click=lambda: ne.remove_history_item(eid)).props("flat dense no-caps")
    if nav:
        item.on("click", lambda _e: _run(eid, actions.index(nav)))


def _run(entry_id: str, index: int) -> None:
    ne.run_entry_action(entry_id, index)


def _copy(entry_id: str) -> None:
    entry = ne._find_entry(entry_id)
    if entry is not None:
        ne.run_action({"kind": "copy"}, message=str(entry.get("message") or ""), entry=entry)


def _refresh() -> None:
    render()
    update_badge()


def create_notification_tray_button() -> None:
    """The bell (with unread badge) in the header row, and the side panel it opens."""
    from .help_system.help_render import inject_page_stylesheet, promote_color_importance

    inject_page_stylesheet("mycelian-notify-center", promote_color_importance(NC_CSS))
    ne.inject_notification_ui_assets()
    ne.register_history_refresh(_refresh)

    def toggle() -> None:
        if S.dialog is None:
            return
        if S.dialog.value:
            close()
        else:
            render()
            S.dialog.open()
            ui.run_javascript("window.MycelianToast&&MycelianToast.suppress(true)")
            S.last_read_ts = time.time()
            update_badge()

    def close() -> None:
        if S.dialog is not None:
            S.dialog.close()
        ui.run_javascript("window.MycelianToast&&MycelianToast.suppress(false)")
        S.last_read_ts = time.time()
        update_badge()
        render()

    def open_settings() -> None:
        close()
        from .help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab("App Settings", main_tab="Settings")

    # Seamless right panel: no backdrop, the app stays usable; only the close button dismisses it.
    with ui.dialog().props("seamless position=right persistent full-height no-esc-dismiss") as dlg, ui.card().classes("ncx-card"):
        S.dialog = dlg
        with ui.element("div").classes("ncx-head"):
            ui.label("Notifications").classes("ncx-title")
            S.new_pill = ui.label("").classes("ncx-pill")
            ui.element("div").classes("ncx-spacer")
            ui.label("Mark all read").classes("ncx-link").on("click", lambda: mark_all_read())
            ui.button(icon="delete_sweep", on_click=lambda: ne.clear_history()).props("flat round dense").tooltip("Clear all")
            ui.button(icon="close", on_click=close).props("flat round dense").tooltip("Close")
        S.chips = ui.element("div").classes("ncx-chips")
        with ui.element("div").classes("ncx-search"):
            search = ui.input(placeholder="Search notifications").props("outlined dense clearable").classes("w-full")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-base")
            search.on_value_change(lambda e: _set_query(e.value or ""))
        with ui.scroll_area().classes("ncx-scroll") as scroll:
            S.scroll = scroll
            S.listing = ui.element("div").classes("w-full")
        with ui.element("div").classes("ncx-foot"):
            S.foot_count = ui.label("")
            ui.html('<span class="ncx-i" style="font-size:15px;vertical-align:-3px">tune</span> Notification settings',
                    sanitize=False).classes("ncx-link").on("click", open_settings)

    with ui.row().classes("notification-tray-wrap relative inline-flex items-center justify-center shrink-0 overflow-visible"):
        ui.button(icon="notifications", on_click=toggle).props("flat round dense").tooltip("Notifications")
        S.badge = ui.label("").classes("notification-tray-badge badge-digits-1 hidden")
    _refresh()


__all__ = ["create_notification_tray_button", "render", "update_badge", "unread_count", "LEVEL_LABELS"]

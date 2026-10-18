#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot → Polls panel."""

from __future__ import annotations

import html

from nicegui import ui

from ..notification_engine import notify
from ..poll_manager import (
    get_poll_manager,
    list_poll_template_names,
    normalize_command,
    normalize_keyword,
)
from ..ui_buttons import themed_control_button
from ..ui_form_controls import form_input, form_number, form_select
from ..ui_timer import layout_schedule

_poll_votes_timer = None
_poll_selected_template = ""


def _format_remaining(seconds) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


_STATUS_COLORS = ("text-theme-success", "text-theme-warning", "secondary-text")


def _status_text(snapshot: dict) -> str:
    status = snapshot.get("status") or "idle"
    total = int(snapshot.get("total_votes") or 0)
    if status == "open" and snapshot.get("permanent"):
        return f"Open · {total} votes · until Stop"
    if status == "open":
        clock = _format_remaining(snapshot.get("remaining_seconds"))
        return f"Open · {total} votes · {clock} left"
    if status == "ended":
        if total <= 0:
            return "Ended · no votes"
        return f"Ended · {total} votes"
    return "Not running"


def _status_color(status: str) -> str:
    if status == "open":
        return "text-theme-success"
    if status == "ended":
        return "text-theme-warning"
    return "secondary-text"


def _apply_status(label, snapshot: dict, seen: dict) -> None:
    text = _status_text(snapshot)
    color = _status_color(str(snapshot.get("status") or "idle"))
    if seen.get("text") != text:
        label.set_text(text)
        seen["text"] = text
    if seen.get("color") != color:
        label.classes(remove=" ".join(_STATUS_COLORS), add=color)
        seen["color"] = color


def _winner_line(winners) -> str:
    parts = []
    for item in winners or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            votes = max(0, int(item.get("votes") or 0))
        except (TypeError, ValueError):
            votes = 0
        parts.append(f"{name} ({votes})")
    return ", ".join(parts)


def _live_feed_html(snapshot: dict) -> str:
    """Bars, counts, and percents in the same order as the overlay."""
    options = list(snapshot.get("options") or [])
    if not options:
        return '<div class="text-sm secondary-text italic py-4 text-center">No categories yet</div>'
    leader = 0
    for row in options:
        try:
            count = int(row.get("votes") or 0)
        except (TypeError, ValueError):
            count = 0
        if count > leader:
            leader = count
    blocks = []
    for row in options:
        name = html.escape(str(row.get("name") or ""))
        keyword = html.escape(str(row.get("keyword") or ""))
        try:
            votes = int(row.get("votes") or 0)
        except (TypeError, ValueError):
            votes = 0
        try:
            percent = float(row.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0
        width = 0 if leader <= 0 else max(0, min(100, (votes / leader) * 100))
        blocks.append(
            "<div>"
            '<div style="display:flex; justify-content:space-between; gap:12px; align-items:baseline;">'
            '<div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
            f'<span style="font-weight:600;">{name}</span>'
            f'<span class="secondary-text" style="margin-left:8px;">{keyword}</span>'
            "</div>"
            f'<span style="flex:none;">{votes} · {percent:g}%</span>'
            "</div>"
            '<div style="height:8px; margin-top:6px; border-radius:4px; overflow:hidden; '
            'background: var(--color-border-default);">'
            f'<div style="height:100%; width:{width:.4f}%; border-radius:4px; '
            'background: var(--color-primary);"></div>'
            "</div></div>"
        )
    return '<div style="display:flex; flex-direction:column; gap:12px;">' + "".join(blocks) + "</div>"


def render_polls_tab(container_el) -> None:
    """Build the Polls sub-tab for the selected overlay template."""
    global _poll_votes_timer, _poll_selected_template
    if _poll_votes_timer is not None:
        try:
            _poll_votes_timer.active = False
        except Exception:
            pass
        try:
            _poll_votes_timer.cancel()
        except Exception:
            pass
        _poll_votes_timer = None

    names = list_poll_template_names()
    manager = get_poll_manager()

    def reload_polls():
        from .chatbot import refresh_tab_content

        refresh_tab_content("polls")

    with container_el:
        with ui.column().classes("w-full h-full min-h-0 gap-3 p-4"):
            if not names:
                ui.label("No templates with a Poll widget yet").classes(
                    "text-base font-medium"
                )
                ui.label(
                    "Add a Poll block in Spore Studio and save the template. "
                    "The built-in poll template is listed here once it exists."
                ).classes("text-sm secondary-text")
                themed_control_button(
                    "Refresh",
                    reload_polls,
                    icon="refresh",
                    extra_classes="btn-secondary px-3 py-2 w-fit",
                )
                return

            if _poll_selected_template not in names:
                _poll_selected_template = names[0]
            template = _poll_selected_template
            manager.ensure_template(template)
            config = manager.get_config(template)
            snapshot = manager.get_overlay_state(template)
            running = snapshot.get("status") == "open"

            def on_template(event):
                global _poll_selected_template
                chosen = str(event.value or "").strip()
                if not chosen or chosen == _poll_selected_template:
                    return
                _poll_selected_template = chosen
                reload_polls()

            def _switch(text: str, tooltip: str, field: str, default: bool = False):
                from nicegui.elements.tooltip import Tooltip

                with ui.row().classes("items-center gap-1 shrink-0") as option_row:
                    ui.switch(
                        value=bool(config.get(field, default)),
                        on_change=lambda e, f=field: get_poll_manager().update_config(
                            template, {f: bool(e.value)}
                        ),
                    ).props("dense")
                    ui.label(text).classes("text-sm whitespace-nowrap")
                if tooltip:
                    tip = Tooltip(tooltip)
                    tip.classes("bg-theme-surface")
                    tip.props["target"] = f"#{option_row.html_id}"

            def do_start():
                try:
                    duration_value = int(duration_in.value)
                except (TypeError, ValueError, NameError):
                    duration_value = int(config.get("duration_seconds") or 0)
                try:
                    banner_duration = float(banner_dur.value)
                except (TypeError, ValueError, NameError):
                    banner_duration = float(config.get("banner_duration") or 10)
                try:
                    banner_message = (banner_msg.value or "").strip()
                except (NameError, AttributeError):
                    banner_message = config.get("banner_message") or ""
                get_poll_manager().update_config(
                    template,
                    {
                        "duration_seconds": duration_value,
                        "banner_duration": banner_duration,
                        "banner_message": banner_message,
                    },
                )
                ok, msg = get_poll_manager().start_poll(template)
                notify(msg, type="positive" if ok else "negative")
                reload_polls()

            def do_stop():
                ok, msg = get_poll_manager().stop_poll(template)
                notify(msg, type="positive" if ok else "warning")
                reload_polls()

            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                form_select(
                    tooltip="Overlay whose Poll widget this setup controls. Only templates with a Poll block are listed.",
                    options=names,
                    label="Poll template",
                    value=template,
                    on_change=on_template,
                    classes="w-44 shrink-0",
                )
                themed_control_button(
                    "Start",
                    do_start,
                    icon="play_arrow",
                    extra_classes="btn-success px-3 py-2",
                )
                themed_control_button(
                    "Stop",
                    do_stop,
                    icon="stop",
                    extra_classes="btn-cancel px-3 py-2",
                )
                themed_control_button(
                    "Refresh",
                    reload_polls,
                    icon="refresh",
                    extra_classes="btn-secondary px-3 py-2",
                )
                duration_in = form_number(
                    tooltip="How long the poll stays open, in seconds. 0 stays open until you press Stop. Applies the next time you start.",
                    label="Duration (seconds)",
                    value=int(config.get("duration_seconds") or 0),
                    min=0,
                    max=86400 * 7,
                    step=1,
                    classes="w-36 shrink-0",
                )

                def save_duration(_=None):
                    try:
                        value = int(duration_in.value)
                    except (TypeError, ValueError):
                        value = 0
                    get_poll_manager().update_config(
                        template, {"duration_seconds": value}
                    )

                duration_in.on("blur", save_duration)
                _switch(
                    "1 entry per user",
                    "The first matching keyword is that person's vote. Later messages are ignored unless vote switching is on.",
                    "one_entry_per_user",
                    True,
                )
                _switch(
                    "Allow vote switching",
                    "When 1 entry per user is on, a later keyword moves their vote. Off ignores every message after the first vote.",
                    "allow_vote_switch",
                    False,
                )
                _switch(
                    "Mods can start",
                    "A Twitch moderator or broadcaster, or a YouTube chat moderator or owner, can start this poll by sending the command at the beginning of a message.",
                    "mods_can_start",
                    False,
                )
                command_in = form_input(
                    tooltip="Chat command that starts only this poll. It always starts with !. The message must begin with this command. Another poll starts from the same message only when it uses this same command.",
                    label="Start command",
                    value=config.get("start_command") or "",
                    placeholder="!startpoll",
                    classes="w-40 shrink-0",
                )
                command_sync = {"on": False}

                def on_command_type(_=None):
                    if command_sync["on"]:
                        return
                    raw = command_in.value or ""
                    if not str(raw).strip():
                        return
                    normalized = normalize_command(raw)
                    if normalized and normalized != raw:
                        command_sync["on"] = True
                        command_in.value = normalized
                        command_sync["on"] = False

                def save_command(_=None):
                    command = normalize_command(command_in.value or "")
                    command_sync["on"] = True
                    command_in.value = command
                    command_sync["on"] = False
                    get_poll_manager().update_config(template, {"start_command": command})

                command_in.on_value_change(on_command_type)
                command_in.on("blur", save_command)

            err = manager.get_last_error(template)
            if err:
                ui.label(f"Last issue: {err}").classes("text-sm text-amber-600")

            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                _switch(
                    "Send winner as a chat banner",
                    "Show the winning option on the chat overlay. Use {option}, {options}, {keyword}, and {votes}.",
                    "announce_winners_as_banner",
                    False,
                )
                _switch(
                    "Send winner in chat",
                    "Post the same winner message in Twitch and YouTube chat.",
                    "announce_winners_in_chat",
                    False,
                )
                banner_msg = form_input(
                    tooltip="Use {option}, {options}, {keyword}, and {votes}. Tied winners fill {options}.",
                    label="Banner message",
                    value=config.get("banner_message") or "",
                    classes="flex-1 min-w-[12rem]",
                )
                banner_dur = form_number(
                    tooltip="How long the banner stays up, in seconds. 0 stays until another banner replaces it.",
                    label="Banner duration (seconds)",
                    value=int(float(config.get("banner_duration") if config.get("banner_duration") is not None else 10)),
                    min=0,
                    max=86400,
                    step=1,
                    classes="w-44 shrink-0",
                )

                def save_banner(_=None):
                    try:
                        duration = float(banner_dur.value)
                    except (TypeError, ValueError):
                        duration = 10
                    get_poll_manager().update_config(
                        template,
                        {
                            "banner_message": (banner_msg.value or "").strip(),
                            "banner_duration": duration,
                        },
                    )

                banner_msg.on("blur", save_banner)
                banner_dur.on("blur", save_banner)

            def add_category():
                ok, msg = get_poll_manager().add_category(template)
                if not ok:
                    notify(msg, type="negative")
                reload_polls()

            def remove_category(category_id: str):
                ok, msg = get_poll_manager().remove_category(template, category_id)
                if not ok:
                    notify(msg, type="negative")
                reload_polls()

            def move_category(category_id: str, delta: int):
                ok, msg = get_poll_manager().move_category(template, category_id, delta)
                if not ok and msg:
                    notify(msg, type="negative")
                reload_polls()

            vote_by_id = {
                row.get("id"): int(row.get("votes") or 0)
                for row in snapshot.get("options") or []
            }
            categories = config.get("categories") or []
            status_color = _status_color(str(snapshot.get("status") or "idle"))
            status_seen = {"text": _status_text(snapshot), "color": status_color}
            winner_text = _winner_line(config.get("last_winners"))
            live_view = None
            live_markup = {"value": ""}

            def show_winner(text: str) -> None:
                winner_label.set_text(text)
                tip = html.escape(text, quote=True)
                winner_label.props(f'title="{tip}"' if tip else "title=")

            with ui.column().classes(
                "w-full flex-1 min-h-0 gap-0 rounded-lg border "
                "border-[var(--color-border-default)] flex flex-col"
            ):
                with ui.element("div").classes(
                    "w-full flex-none px-4 py-2 gap-3 "
                    "bg-[var(--color-bg-elevated)] rounded-t-lg border-b "
                    "border-[var(--color-border-default)]"
                ).style(
                    "display:grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); "
                    "align-items:center; column-gap:12px;"
                ):
                    with ui.row().classes("items-center gap-3 min-w-0"):
                        ui.label("Categories").classes("text-lg font-medium shrink-0")
                        status_label = ui.label(status_seen["text"]).classes(
                            "text-sm font-medium truncate " + status_color
                        )
                    winner_label = ui.label(winner_text).classes(
                        "text-sm font-semibold text-center"
                    ).style(
                        "justify-self:center; max-width:min(46vw, 28rem); overflow:hidden; "
                        "text-overflow:ellipsis; white-space:nowrap;"
                    )
                    show_winner(winner_text)
                    with ui.row().classes("justify-end"):
                        add_button = themed_control_button(
                            "Add category",
                            add_category,
                            icon="add",
                            extra_classes="btn-primary px-3 py-2",
                        )
                        if running:
                            add_button.disable()

                with (
                    ui.scroll_area()
                    .classes("w-full flex-1 min-h-0")
                    .style(
                        "min-height: 0; flex: 1 1 auto; "
                        "background: var(--color-bg-sunken, #0d1117); "
                        "border-bottom-left-radius: 0.5rem; "
                        "border-bottom-right-radius: 0.5rem;"
                    )
                ):
                    if running:
                        live_markup["value"] = _live_feed_html(snapshot)
                        live_view = ui.html(live_markup["value"], sanitize=False).classes(
                            "w-full p-3"
                        )
                    else:
                        with ui.column().classes("w-full gap-2 p-3"):
                            if not categories:
                                ui.label("No categories yet").classes(
                                    "text-sm muted-text italic py-4 self-center"
                                )
                            for index, cat in enumerate(categories):
                                cat_id = cat["id"]
                                votes = int(vote_by_id.get(cat_id, cat.get("votes") or 0))
                                with ui.row().classes("w-full gap-2 items-end flex-nowrap"):
                                    name_in = form_input(
                                        tooltip="Name shown on the poll overlay. The keyword follows this name until you edit the keyword yourself.",
                                        label="Name",
                                        value=cat.get("name") or "",
                                        classes="flex-1 min-w-[8rem]",
                                    )
                                    name_in.props("debounce=0")
                                    keyword_in = form_input(
                                        tooltip="Chat token this category listens for. It always starts with #. It follows the name until you change it.",
                                        label="Keyword",
                                        value=cat.get("keyword") or "",
                                        classes="w-36 shrink-0",
                                    )
                                    votes_in = form_number(
                                        tooltip="Votes for this category. Starting a poll clears the counts.",
                                        label="Votes",
                                        value=votes,
                                        min=0,
                                        max=1000000,
                                        step=1,
                                        classes="w-24 shrink-0",
                                    )
                                    up_button = themed_control_button(
                                        "",
                                        lambda cid=cat_id: move_category(cid, -1),
                                        icon="arrow_upward",
                                        extra_classes="btn-secondary px-2 py-2",
                                    )
                                    down_button = themed_control_button(
                                        "",
                                        lambda cid=cat_id: move_category(cid, 1),
                                        icon="arrow_downward",
                                        extra_classes="btn-secondary px-2 py-2",
                                    )
                                    themed_control_button(
                                        "",
                                        lambda cid=cat_id: remove_category(cid),
                                        icon="delete",
                                        extra_classes="btn-danger px-2 py-2",
                                    )
                                    if index == 0:
                                        up_button.disable()
                                    if index == len(categories) - 1:
                                        down_button.disable()

                                    follow_keyword = {
                                        "on": (cat.get("keyword") or "")
                                        == normalize_keyword(cat.get("name") or ""),
                                        "syncing": False,
                                    }

                                    def on_name_type(
                                        _=None,
                                        name_box=name_in,
                                        keyword_box=keyword_in,
                                        follow=follow_keyword,
                                    ):
                                        if not follow["on"] or follow["syncing"]:
                                            return
                                        follow["syncing"] = True
                                        keyword_box.value = normalize_keyword(name_box.value or "")
                                        follow["syncing"] = False

                                    def on_keyword_type(
                                        _=None,
                                        name_box=name_in,
                                        keyword_box=keyword_in,
                                        follow=follow_keyword,
                                    ):
                                        if follow["syncing"]:
                                            return
                                        follow["on"] = (keyword_box.value or "") == normalize_keyword(
                                            name_box.value or ""
                                        )

                                    def save_category(
                                        _=None,
                                        cid=cat_id,
                                        name_box=name_in,
                                        keyword_box=keyword_in,
                                    ):
                                        ok, msg = get_poll_manager().update_category(
                                            template,
                                            cid,
                                            name=name_box.value,
                                            keyword=keyword_box.value,
                                        )
                                        if not ok:
                                            notify(msg, type="negative")
                                            return
                                        updated = get_poll_manager().get_config(template)
                                        match = next(
                                            (
                                                row
                                                for row in updated.get("categories") or []
                                                if row["id"] == cid
                                            ),
                                            None,
                                        )
                                        if match:
                                            name_box.value = match["name"]
                                            keyword_box.value = match["keyword"]

                                    def save_votes(_=None, cid=cat_id, box=votes_in):
                                        try:
                                            count = int(box.value)
                                        except (TypeError, ValueError):
                                            count = 0
                                        before = _winner_line(
                                            get_poll_manager().get_config(template).get("last_winners")
                                        )
                                        ok, msg = get_poll_manager().set_category_votes(
                                            template, cid, count
                                        )
                                        if not ok:
                                            notify(msg, type="negative")
                                            return
                                        box.value = max(0, count)
                                        after = _winner_line(
                                            get_poll_manager().get_config(template).get("last_winners")
                                        )
                                        if after != before:
                                            show_winner(after)

                                    name_in.on_value_change(on_name_type)
                                    keyword_in.on_value_change(on_keyword_type)
                                    name_in.on("blur", save_category)
                                    keyword_in.on("blur", save_category)
                                    votes_in.on("blur", save_votes)

            seen_status = {"value": snapshot.get("status")}

            def _set_live_markup(markup: str) -> None:
                if live_view is None or markup == live_markup["value"]:
                    return
                if hasattr(live_view, "set_content"):
                    live_view.set_content(markup)
                else:
                    live_view.content = markup
                live_markup["value"] = markup

            def _refresh_live():
                try:
                    if status_label.is_deleted:
                        return
                except Exception:
                    return
                current = get_poll_manager().get_overlay_state(template)
                try:
                    _apply_status(status_label, current, status_seen)
                except Exception:
                    return
                if current.get("status") != seen_status["value"]:
                    seen_status["value"] = current.get("status")
                    reload_polls()
                    return
                if current.get("status") != "open":
                    return
                _set_live_markup(_live_feed_html(current))

            _poll_votes_timer = layout_schedule(1.0, callback=_refresh_live)

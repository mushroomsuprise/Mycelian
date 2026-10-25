# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Commands and Events tabs of the Chatbot window: toolbar, filter chips and cards.

Cards reuse the Connectors card styles (``.cnx`` / ``cx-*`` in ``connectors_styles``) plus a
few chatbot-only rules below. Reply previews are rendered with sample values by
``chatbot_variables.render_preview`` (the real runtime substitution, no side effects).
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui

from ..notification_engine import notify
from .service_brand_icons import SERVICE_BRAND_SVG

logger = logging.getLogger(__name__)

CHATBOT_CARDS_CSS = """
.cnx .cx-cmdrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 2px; min-width: 0; }
.cnx .cx-cmd { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .74rem; font-weight: 700; color: var(--color-primary);
    padding: 1px 6px; border-radius: 5px; background: var(--color-primary-light); }
.cnx .cx-alias { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .68rem; color: var(--color-text-muted); }
.cnx .cx-trig2 { display: inline-flex; align-items: center; gap: 5px; font-size: .72rem; font-weight: 600; color: var(--color-text-secondary); margin-top: 2px; }
.cnx .cx-trig2 .cx-brand, .cnx .cx-trig2 .cx-icon { font-size: 13px; }
.cnx .cx-reply { margin: 0 12px 8px 15px; padding: 7px 10px; border-radius: 4px 10px 10px 10px; font-size: .78rem; line-height: 1.45;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; max-height: calc(2 * 1.45em + 14px);
    white-space: pre-line; word-break: break-word; }
.cnx .cx-reply.is-empty { font-style: italic; color: var(--color-text-muted); }
.cnx .cx-tags { display: flex; flex-wrap: wrap; gap: 4px; padding: 0 12px 10px 15px; flex: 1 1 auto; align-content: flex-start; }
.cnx .cx-tg { display: inline-flex; align-items: center; gap: 4px; padding: 2px 7px; border-radius: 6px; font-size: .68rem; font-weight: 600;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); white-space: nowrap;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.cnx .cx-tg .cx-icon, .cnx .cx-tg .cx-brand { font-size: 12px; flex: 0 0 auto; }
.cnx .cx-tg.is-mod { color: var(--color-success); }
.cnx .cx-tg.is-count { color: var(--color-warning); }
.cnx .cx-tg.is-api { color: var(--color-info); }
.cnx .cx-tg.is-warn { color: var(--color-warning); border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); }
.cnx .cx-filter .cx-count { font-size: .66rem; font-weight: 700; padding: 0 5px; border-radius: 6px; background: var(--color-bg-surface);
    color: var(--color-text-secondary); }
.cnx .cx-filter.is-on .cx-count { background: rgba(255, 255, 255, .22); color: #fff; }
.cnx .cx-tile.is-tips { background: color-mix(in srgb, var(--color-success) 12%, transparent); color: var(--color-success);
    border-color: color-mix(in srgb, var(--color-success) 30%, transparent); }
"""

_PERMISSION_LABELS = {"subscriber": "Subscribers", "vip": "VIPs", "moderator": "Mods only", "broadcaster": "Only you"}
_EVENT_GROUP_GLYPH = {"Twitch": "twitch", "YouTube": "youtube", "Tips": "volunteer_activism", "Time": "timer"}


def _icon(name: str) -> str:
    return f'<span class="cx-icon">{name}</span>'


def _brand(key: str) -> str:
    svg = SERVICE_BRAND_SVG.get(key)
    return f'<span class="cx-brand is-{key}">{svg}</span>' if svg else _icon(key)


def _esc(text: Any) -> str:
    return html_lib.escape(str(text if text is not None else ""))


def _tag(kind: str, glyph: str, text: str) -> str:
    return f'<span class="cx-tg {kind}" title="{_esc(text)}">{glyph}<span>{_esc(text)}</span></span>'


def _value(obj: Any, name: str, default: Any = None) -> Any:
    value = getattr(obj, name, default)
    return getattr(value, "value", value)


_TOKEN = re.compile(r"\{[A-Za-z_][\w.\-]*(?::[^{}]*)?\}")


def unfilled_tokens(template: str, rendered: str) -> List[str]:
    """Tokens from the template that are still literally present after filling sample values."""
    return [tok for tok in dict.fromkeys(_TOKEN.findall(template or "")) if tok in (rendered or "")]


def _relative(ts: float) -> str:
    try:
        from .connectors import _relative_time

        return _relative_time(ts)
    except Exception:
        return ""


def _duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"Every {n} hour{'s' if n != 1 else ''}"
    if seconds % 60 == 0:
        n = seconds // 60
        return f"Every {n} minute{'s' if n != 1 else ''}"
    return f"Every {seconds} seconds"


def _preview(text: str, kind: str, event_type: Optional[str] = None, item: Any = None) -> str:
    if not text:
        return ""
    try:
        from .. import chatbot_variables

        return chatbot_variables.render_preview(text, kind, event_type=event_type, item=item)
    except Exception as exc:
        logger.debug("Card preview failed: %s", exc)
        return text


def event_info(event_type: Optional[str]) -> Dict[str, Any]:
    try:
        from .. import chatbot_variables

        for entry in chatbot_variables.EVENTS:
            if entry.get("value") == event_type:
                return entry
    except Exception:
        pass
    label = (event_type or "").replace("_", " ").capitalize()
    group = "YouTube" if (event_type or "").startswith("youtube") else ("Time" if event_type in ("interval", "specific_time") else "Twitch")
    return {"value": event_type, "label": label, "group": group, "hint": ""}


def event_glyph_key(event_type: Optional[str]) -> str:
    return _EVENT_GROUP_GLYPH.get(event_info(event_type).get("group", ""), "bolt")


def lookup_label(lookup: Any) -> str:
    if not isinstance(lookup, dict) or not lookup.get("id"):
        return ""
    if lookup.get("id") == "advanced":
        return "Twitch API request"
    try:
        from .. import chatbot_lookups

        for entry in chatbot_lookups.LOOKUPS:
            if entry.get("id") == lookup["id"]:
                return f"Looks up {entry.get('label', '').lower()}"
    except Exception:
        pass
    return "Twitch lookup"


def _command_lookup(command: Any) -> Any:
    lookup = getattr(command, "lookup", None)
    if not lookup and getattr(command, "api_enabled", False):
        return {"id": "advanced"}
    return lookup


def _output_tags(item: Any) -> List[str]:
    tags = []
    if _value(item, "output_mode", "chat") == "banner":
        tags.append(_tag("", _icon("featured_video"), "Shows as banner"))
    else:
        for target in getattr(item, "reply_targets", None) or []:
            tags.append(_tag("", _brand(target), {"twitch": "Twitch", "youtube": "YouTube"}.get(target, target)))
    for channel in getattr(item, "discord_channels", None) or []:
        if isinstance(channel, dict):
            tags.append(_tag("", _brand("discord"), f"#{channel.get('channel_name') or channel.get('channel_id')}"))
    return tags


def command_tags(command: Any) -> List[str]:
    tags = []
    ctype = _value(command, "command_type", "basic")
    if ctype == "counter":
        tags.append(_tag("is-count", _icon("plus_one"), f"Counter · {getattr(command, 'counter_value', 0)}"))
    elif ctype == "reset":
        target = getattr(command, "reset_command", "") or "a counter"
        tags.append(_tag("is-count", _icon("restart_alt"), f"Resets !{target}" if target != "a counter" else "Resets a counter"))
    permission = getattr(command, "permission", None) or ("moderator" if getattr(command, "mod_only", False) else "everyone")
    if permission in _PERMISSION_LABELS:
        tags.append(_tag("is-mod", _icon("shield"), _PERMISSION_LABELS[permission]))
    lookup = lookup_label(_command_lookup(command))
    if lookup:
        tags.append(_tag("is-api", _icon("travel_explore"), lookup))
    cooldown = int(getattr(command, "cooldown", 0) or 0)
    if cooldown:
        tags.append(_tag("", _icon("timer"), f"{cooldown} s cooldown"))
    limit = int(getattr(command, "usage_limit", 0) or 0)
    if limit:
        tags.append(_tag("", _icon("group"), f"Limit {limit} use{'s' if limit != 1 else ''}"))
    return tags + _output_tags(command)


def event_filter_text(event: Any) -> str:
    et = _value(event, "event_type", "")
    g = lambda name: getattr(event, name, 0) or 0  # noqa: E731
    if et == "bits" and g("bits_quantity"):
        return f"{g('bits_quantity'):,}+ bits"
    if et == "subscription" and g("sub_tier"):
        return f"Tier {g('sub_tier')}"
    if et == "resubscription":
        parts = [f"{g('resub_months')}+ months" if g("resub_months") else "", f"Tier {g('resub_tier')}" if g("resub_tier") else ""]
        return " · ".join(p for p in parts if p)
    if et == "gift_subscription":
        parts = [f"{g('gift_sub_quantity')}+ gifts" if g("gift_sub_quantity") else "", f"Tier {g('gift_sub_tier')}" if g("gift_sub_tier") else ""]
        return " · ".join(p for p in parts if p)
    if et == "donation" and g("donation_amount"):
        return f"{g('donation_amount'):g}+ tips"
    if et == "raid":
        parts = [f"{g('raid_viewer_count')}+ viewers" if g("raid_viewer_count") else "",
                 f"From {getattr(event, 'raid_raider_name', '')}" if getattr(event, "raid_raider_name", "") else ""]
        return " · ".join(p for p in parts if p)
    if et == "channel_point_reward_name" or et == "channel_point_redemption":
        name = getattr(event, "channel_point_reward_name", "")
        return f"“{name}”" if name else ""
    if et in ("hype_train_start", "hype_train_progress") and g("hype_train_level"):
        return f"Level {g('hype_train_level')}+"
    if et == "hype_train_end" and g("hype_train_end_level"):
        return f"Level {g('hype_train_end_level')}+"
    if et in ("chat_message", "youtube_chat_message"):
        text = getattr(event, "chat_message_text", "") or ""
        if text:
            match = getattr(event, "chat_message_match_type", "exact") or "exact"
            verb = {"starts_with": "Starts with", "contains": "Contains"}.get(match, "Is")
            return f"{verb} “{text}”"
    return ""


def event_trigger_text(event: Any) -> str:
    et = _value(event, "event_type", "")
    if et == "interval":
        return _duration(getattr(event, "interval", 0)) or "Every few minutes"
    if et == "specific_time":
        at = getattr(event, "specific_time", "") or ""
        return f"Daily at {at}" if at else "At a set time"
    return event_info(et).get("label") or et


def event_tags(event: Any) -> List[str]:
    tags = []
    info = event_info(_value(event, "event_type", ""))
    filt = event_filter_text(event)
    if filt:
        tags.append(_tag("is-count", _icon("filter_alt"), filt))
    lookup = lookup_label(getattr(event, "lookup", None))
    if lookup:
        tags.append(_tag("is-api", _icon("travel_explore"), lookup))
    if info.get("warning"):
        tags.append(_tag("is-warn", _icon("warning_amber"), "May not fire"))
    return tags + _output_tags(event)


def search_blob(item: Any, kind: str) -> str:
    parts = [getattr(item, "name", ""), getattr(item, "description", ""), getattr(item, "response_text", "")]
    if kind == "commands":
        parts += [getattr(item, "command_name", "")] + list(getattr(item, "aliases", None) or [])
    else:
        parts += [event_trigger_text(item), event_info(_value(item, "event_type", "")).get("label", "")]
    return " ".join(str(p or "") for p in parts).lower()


@dataclass
class CardActions:
    new: Callable[[], Any]
    edit: Callable[[str], Any]
    test: Callable[[str], Any]
    toggle: Callable[[str, bool], Any]
    duplicate: Callable[[str], Any]
    delete: Callable[[str], Any]
    reset_counter: Optional[Callable[[str], Any]] = None
    refresh: Optional[Callable[[], Any]] = None


_KIND_TEXT = {
    "commands": ("chat", "Commands", "Viewers type these in chat.", "Search name, command or reply", "New command",
                 "No commands yet", "Create your first command, like !discord or !uptime.", "Create a command"),
    "events": ("celebration", "Events", "Automatic replies to follows, subs, raids, timers and more.", "Search name, event or reply", "New event",
               "No events yet", "Thank followers, cheer back at bits, or post a reminder every few minutes.", "Create an event"),
}


class ItemsPanel:
    """Toolbar + card grid for the Commands or Events tab."""

    def __init__(self, kind: str, items: Callable[[], Dict[str, Any]], actions: CardActions):
        self.kind = kind
        self.items = items
        self.actions = actions
        self.query = ""
        self.status = "all"
        self.grid: Optional[ui.element] = None
        self._sub: Optional[ui.label] = None
        self._chips: Dict[str, ui.element] = {}
        self._badges: Dict[str, ui.label] = {}

    def build(self, container: ui.element) -> None:
        icon, title, sub, placeholder, new_label, *_ = _KIND_TEXT[self.kind]
        with container:
            with ui.element("div").classes("cnx w-full h-full flex flex-col gap-3"):
                with ui.element("div").classes("cx-bar"):
                    ui.html(f'<span class="cx-bar-icon">{_icon(icon)}</span>', sanitize=False).classes("contents")
                    with ui.element("div"):
                        ui.label(title).classes("cx-bar-title")
                        self._sub = ui.label(sub).classes("cx-bar-sub")
                    ui.element("div").classes("cx-spacer")
                    search = ui.input(placeholder=placeholder).props("outlined dense clearable").classes("cx-search")
                    with search.add_slot("prepend"):
                        ui.icon("search").classes("text-base")
                    search.on_value_change(lambda e: self._set_query(e.value or ""))
                    with ui.element("div").classes("cx-filters"):
                        for key, label in (("all", "All"), ("on", "On"), ("off", "Off")):
                            chip = ui.element("div").classes("cx-filter" + (" is-on" if key == self.status else ""))
                            with chip:
                                ui.label(label)
                                self._badges[key] = ui.label("0").classes("cx-count")
                            chip.on("click", lambda _e, k=key: self._set_status(k))
                            self._chips[key] = chip
                    if self.actions.refresh:
                        ui.button(icon="refresh", on_click=self.actions.refresh).props("outline dense color=primary").tooltip("Reload")
                    ui.button(new_label, icon="add", on_click=self.actions.new).props("unelevated no-caps color=primary")
                with ui.scroll_area().classes("w-full grow"):
                    self.grid = ui.element("div").classes("w-full")
        self.refresh()

    def _set_query(self, query: str) -> None:
        self.query = query.strip().lower()
        self.refresh()

    def _set_status(self, key: str) -> None:
        self.status = key
        for k, chip in self._chips.items():
            chip.classes(add="is-on") if k == key else chip.classes(remove="is-on")
        self.refresh()

    def refresh(self) -> None:
        if self.grid is None or getattr(self.grid, "is_deleted", False):
            return
        try:
            items = dict(self.items() or {})
        except Exception as exc:
            logger.error("Could not load chatbot %s: %s", self.kind, exc, exc_info=True)
            items = {}
            notify(
                f"The list may look empty, but your {self.kind} haven't been deleted.",
                type="error",
                source="chatbot",
                title=f"Couldn't show your {self.kind}",
                hint="Switch to another tab and back to retry. If it keeps happening, restart Mycelian.",
                details=f"{type(exc).__name__}: {exc}",
                dedupe_key=f"chatbot:list_failed:{self.kind}",
                dedupe_cooldown_sec=300,
            )
        on = sum(1 for i in items.values() if getattr(i, "enabled", True))
        counts = {"all": len(items), "on": on, "off": len(items) - on}
        for key, badge in self._badges.items():
            badge.set_text(str(counts[key]))
        if self._sub is not None:
            noun = "command" if self.kind == "commands" else "event"
            base = _KIND_TEXT[self.kind][2]
            self._sub.set_text(f"{base} {len(items)} {noun}{'s' if len(items) != 1 else ''}, {on} on." if items else base)
        shown = {
            iid: item for iid, item in items.items()
            if (self.status == "all" or (self.status == "on") == bool(getattr(item, "enabled", True)))
            and (not self.query or self.query in search_blob(item, self.kind))
        }
        self.grid.clear()
        with self.grid:
            if not items:
                self._empty()
            elif not shown:
                with ui.element("div").classes("cx-empty"):
                    ui.html(_icon("search_off"), sanitize=False).classes("contents")
                    ui.label("Nothing matches your search or filter.")
            else:
                with ui.element("div").classes("cx-grid"):
                    for iid, item in sorted(shown.items(), key=lambda kv: str(getattr(kv[1], "name", "")).lower()):
                        try:
                            (self._command_card if self.kind == "commands" else self._event_card)(iid, item)
                        except Exception as exc:
                            logger.error("Could not draw chatbot card %s: %s", iid, exc, exc_info=True)
                            notify(
                                f"“{getattr(item, 'name', '') or iid}” is missing from the list but still works.",
                                type="warning",
                                source="chatbot",
                                title=f"Couldn't show one of your {self.kind}",
                                hint="Restart Mycelian. If it keeps happening, copy the details and report it.",
                                details=f"{iid}: {type(exc).__name__}: {exc}",
                                dedupe_key=f"chatbot:card_failed:{iid}",
                                dedupe_cooldown_sec=300,
                            )

    def _empty(self) -> None:
        icon, *_rest = _KIND_TEXT[self.kind]
        title, text, button = _KIND_TEXT[self.kind][5:8]
        with ui.element("div").classes("cx-empty"):
            ui.html(_icon(icon), sanitize=False).classes("contents")
            ui.label(title).classes("cx-empty-title")
            ui.label(text)
            ui.button(button, icon="add", on_click=self.actions.new).props("unelevated no-caps color=primary")

    def _card_shell(self, iid: str, item: Any, tile_html: str, subtitle_html: str, reply: str, tags: List[str],
                    noun: str, extra_buttons: Callable[[], None]) -> None:
        enabled = bool(getattr(item, "enabled", True))
        card = ui.element("div").classes("cx-card" + ("" if enabled else " is-off"))
        with card:
            with ui.element("div").classes("cx-head"):
                ui.html(tile_html, sanitize=False).classes("contents")
                with ui.element("div").classes("cx-title"):
                    name = ui.label(getattr(item, "name", "") or "Untitled").classes("cx-name")
                    name.on("dblclick", lambda _e, i=iid: self.actions.edit(i))
                    ui.html(subtitle_html, sanitize=False).classes("contents")
                    desc = getattr(item, "description", "") or ""
                    ui.label(desc or "No description").classes("cx-desc" + ("" if desc else " is-empty"))

                def flip(e, i=iid, c=card) -> None:
                    c.classes(remove="is-off") if e.value else c.classes(add="is-off")
                    self.actions.toggle(i, bool(e.value))

                ui.switch(value=enabled, on_change=flip).props("dense color=positive").tooltip("Turn on or off")
            ui.label(reply or "No reply yet").classes("cx-reply" + ("" if reply else " is-empty"))
            ui.html(f'<div class="cx-tags">{"".join(tags)}</div>', sanitize=False).classes("contents")
            with ui.element("div").classes("cx-foot"):
                runs = int(getattr(item, "trigger_count", 0) or getattr(item, "usage_count", 0) or 0)
                last = float(getattr(item, "last_used", 0) or getattr(item, "last_triggered", 0) or 0)
                if runs or last:
                    when = _relative(last)
                    ui.html(f'<span class="cx-stat">{_icon("bolt")}<b>{runs:,}</b>&nbsp;{noun}{"s" if runs != 1 else ""}</span>'
                            + (f'<span class="cx-stat is-muted">&nbsp;· {_esc(when)}</span>' if when else ""), sanitize=False).classes("contents")
                else:
                    ui.html(f'<span class="cx-stat is-muted">{_icon("schedule")}Never {"used" if noun == "use" else "run"}</span>',
                            sanitize=False).classes("contents")
                ui.element("div").classes("cx-spacer")
                extra_buttons()
                ui.button(icon="play_arrow", on_click=lambda i=iid: self.actions.test(i)).props("flat round dense").tooltip("Test with sample data")
                ui.button(icon="edit", on_click=lambda i=iid: self.actions.edit(i)).props("flat round dense").tooltip("Edit")
                with ui.button(icon="more_horiz").props("flat round dense").tooltip("More"):
                    with ui.menu().props("auto-close"):
                        ui.menu_item("Duplicate", on_click=lambda i=iid: self.actions.duplicate(i))
                        ui.menu_item("Delete…", on_click=lambda i=iid: self.actions.delete(i))

    def _command_card(self, iid: str, command: Any) -> None:
        ctype = _value(command, "command_type", "basic")
        lookup = _command_lookup(command)
        icon = "travel_explore" if lookup else {"counter": "plus_one", "reset": "restart_alt"}.get(ctype, "chat")
        aliases = "".join(f'<span class="cx-alias">!{_esc(a)}</span>' for a in (getattr(command, "aliases", None) or []) if str(a).strip())
        subtitle = f'<div class="cx-cmdrow"><span class="cx-cmd">!{_esc(getattr(command, "command_name", ""))}</span>{aliases}</div>'
        template = getattr(command, "response_text", "") or ""
        reply = _preview(template, "command", item=command)
        tags = command_tags(command)
        missing = unfilled_tokens(template, reply)
        if missing:
            tags.insert(0, _tag("is-warn", _icon("help_outline"), f"Check {missing[0]}" + (f" +{len(missing) - 1}" if len(missing) > 1 else "")))

        def extra() -> None:
            if ctype == "counter" and self.actions.reset_counter:
                ui.button(icon="restart_alt", on_click=lambda i=iid: self.actions.reset_counter(i)).props("flat round dense").tooltip(
                    "Reset the counter to 0")

        self._card_shell(iid, command, f'<span class="cx-tile">{_icon(icon)}</span>', subtitle, reply, tags, "use", extra)

    def _event_card(self, iid: str, event: Any) -> None:
        et = _value(event, "event_type", "")
        key = event_glyph_key(et)
        tile_cls = {"twitch": "is-twitch", "youtube": "is-youtube", "volunteer_activism": "is-tips"}.get(key, "")
        subtitle = f'<div class="cx-trig2">{_brand(key)}<span>{_esc(event_trigger_text(event))}</span></div>'
        template = getattr(event, "response_text", "") or ""
        reply = _preview(template, "event", event_type=et, item=event)
        tags = event_tags(event)
        missing = unfilled_tokens(template, reply)
        if missing:
            tags.insert(0, _tag("is-warn", _icon("help_outline"), f"Check {missing[0]}" + (f" +{len(missing) - 1}" if len(missing) > 1 else "")))
        self._card_shell(iid, event, f'<span class="cx-tile {tile_cls}">{_brand(key)}</span>', subtitle, reply, tags, "run",
                         lambda: None)

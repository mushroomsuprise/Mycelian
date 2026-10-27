# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Polls tab of the Chatbot window: overlay picker, options or live results, and side cards.

Each overlay (Spore Studio template with a Poll widget) has its own poll. The picker stays
usable while a poll runs, so another overlay's poll can be set up or started at the same time;
overlays with a live poll are marked in the list. Settings save as you change them. Live
numbers refresh every second while the tab is visible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from nicegui import run, ui

from .. import twitch_polls
from ..notification_engine import notify
from ..poll_manager import (
    DEFAULT_BANNER_MESSAGE,
    ONE_TWITCH_POLL_MESSAGE,
    TWITCH_CHOICES_MAX,
    format_banner_message,
    get_poll_manager,
    list_poll_template_names,
    normalize_command,
    normalize_keyword,
    poll_widget_info,
)
from ..ui_timer import layout_schedule
from .chatbot_gp_styles import card, clock, duration_text, esc, icon, inject_styles, open_in_spore_studio, overlay_rows, plural, seg, toggle
from .editor_variables import VariableKit, raw_html
from .tab_visibility import chatbot_subtab_hidden, on_chatbot_subtab_shown

logger = logging.getLogger(__name__)

_UNITS = {1: "seconds", 60: "minutes", 3600: "hours"}
_TWITCH_UNITS = {1: "seconds", 60: "minutes"}  # Twitch polls run 15 seconds to 30 minutes
DEFAULT_TIMER = 180

# Remembered across rebuilds so the tab reopens on the overlay the user last picked.
_selected_template = ""


def split_duration(seconds: int) -> tuple:
    """(amount, unit seconds) with the largest unit that divides evenly."""
    seconds = max(0, int(seconds or 0))
    for unit in (3600, 60):
        if seconds and seconds % unit == 0:
            return seconds // unit, unit
    return seconds, 1


def leader_text(snapshot: Dict[str, Any]) -> str:
    options = [o for o in snapshot.get("options") or [] if int(o.get("votes") or 0) > 0]
    if not options:
        return "—"
    top = int(options[0].get("votes") or 0)
    tied = [o for o in options if int(o.get("votes") or 0) == top]
    return str(tied[0].get("name") or "") if len(tied) == 1 else f"{len(tied)}-way tie"


def winners_text(winners: List[Dict[str, Any]]) -> str:
    names = [str(w.get("name") or "") for w in winners or [] if w.get("name")]
    if not names:
        return ""
    votes = int((winners[0] or {}).get("votes") or 0)
    return f"{' & '.join(names)} ({plural(votes, 'vote')})"


def poll_variables(sample: List[Dict[str, Any]]) -> List[dict]:
    first = sample[0] if sample else {}
    return [
        {"key": "option", "label": "Winning option (all of them when tied)", "example": format_banner_message("{option}", sample), "group": "Poll"},
        {"key": "options", "label": "Every winning option, comma separated", "example": format_banner_message("{options}", sample), "group": "Poll"},
        {"key": "keyword", "label": "The winner's keyword", "example": str(first.get("keyword") or ""), "group": "Poll"},
        {"key": "votes", "label": "The winning vote count", "example": str(first.get("votes") or 0), "group": "Poll"},
    ]


def bars_html(snapshot: Dict[str, Any], style: Dict[str, Any], live: bool) -> str:
    """Result bars in the widget's colours, sorted like the overlay (most votes first)."""
    options = list(snapshot.get("options") or [])
    if not options:
        return '<div class="gp-hint">No options yet.</div>'
    top = max((int(o.get("votes") or 0) for o in options), default=0)
    bar, win = esc(style.get("bar_color")), esc(style.get("winner_color"))
    rows = []
    for row in options:
        votes = int(row.get("votes") or 0)
        width = (votes / top * 100) if top else 0
        leading = votes > 0 and votes == top
        winner = bool(row.get("winner")) or (live and leading)
        fill = win if row.get("winner") else bar
        rows.append(
            f'<div><div class="gp-bar-top">{"🏆 " if winner else ""}{esc(row.get("name"))}<code>{esc(row.get("keyword"))}</code>'
            f'<span><b>{votes:,}</b> · {float(row.get("percent") or 0):g}%</span></div>'
            f'<div class="gp-bar-track" style="background:color-mix(in srgb, {bar} 16%, transparent)">'
            f'<div class="gp-bar-fill" style="width:{width:.2f}%;background:{fill}"></div></div></div>'
        )
    return '<div class="gp-bars">' + "".join(rows) + "</div>"


def widget_html(snapshot: Dict[str, Any], style: Dict[str, Any]) -> str:
    """A small stand-in for the Spore Studio Poll widget (title, keyword chips, bars) in its own colours."""
    options = list(snapshot.get("options") or [])
    if not options:
        options = [{"name": "Option 1", "votes": 0, "percent": 0}, {"name": "Option 2", "votes": 0, "percent": 0}]
    top = max((int(o.get("votes") or 0) for o in options), default=0)
    bar = esc(style.get("bar_color"))
    bar2 = esc(style.get("bar_color_2") or style.get("bar_color"))
    parts = []
    if style.get("title"):
        parts.append(f'<div class="gp-widget-title">{esc(style.get("title"))}</div>')
    for row in options[:6]:
        votes = int(row.get("votes") or 0)
        width = (votes / top * 100) if top else 0
        fill = esc(style.get("winner_color")) if row.get("winner") else f"linear-gradient(90deg, {bar}, {bar2})"
        numbers = []
        if style.get("show_counts", True):
            numbers.append(f"{votes:,}")
        if style.get("show_percent", True):
            numbers.append(f"{float(row.get('percent') or 0):g}%")
        chip = (f'<span class="gp-widget-kw">{esc(row.get("keyword"))}</span>'
                if style.get("show_keywords", True) and row.get("keyword") else "")
        parts.append(
            f'<div class="gp-widget-row"><div class="gp-widget-top"><span>{esc(row.get("name"))}{chip}</span><span>{" · ".join(numbers)}</span></div>'
            f'<div class="gp-widget-track" style="background:{esc(style.get("track_color"))}">'
            f'<div class="gp-widget-fill" style="width:{width:.2f}%;background:{fill}"></div></div></div>'
        )
    return (f'<div class="gp-widget" style="background:{esc(style.get("background_color"))};--gp-widget-text:{esc(style.get("color"))}">'
            + "".join(parts) + "</div>")


def _warn_option(msg: str) -> None:
    """A poll option change was refused (validation, e.g. a duplicate keyword)."""
    notify(msg or "", type="warning", source="chatbot", title="Poll option not changed",
           hint="Change the value and try again.")


class PollsPanel:
    def __init__(self) -> None:
        self.root: Optional[ui.element] = None
        self.names: List[str] = []
        self.template = ""
        self.picker: Optional[ui.select] = None
        self.hero_text: Optional[ui.html] = None
        self.hero_stats: Optional[ui.html] = None
        self.live_bars: Optional[ui.html] = None
        self.widget: Optional[ui.html] = None
        self.preview: Optional[ui.label] = None
        self._timer = None
        self._status = ""
        self._twitch_status: Optional[str] = None
        self._live_marks: Dict[str, bool] = {}
        self._seen: Dict[str, str] = {}
        self._last_timer = DEFAULT_TIMER
        self._style: Dict[str, Any] = {}
        self.timer_row: Optional[ui.element] = None
        self.amount: Optional[ui.number] = None
        self.unit: Optional[ui.select] = None

    # -- layout ------------------------------------------------------------

    def build(self, container: ui.element) -> None:
        inject_styles()
        self._stop_timer()
        container.clear()
        with container:
            with ui.scroll_area().classes("w-full grow"), ui.element("div").classes("cnx w-full"):
                self.root = ui.element("div").classes("gp-wrap w-full")
        self.refresh()
        on_chatbot_subtab_shown("Polls", self._tick)
        self._timer = layout_schedule(1.0, self._tick)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def refresh(self) -> None:
        """Rescan the overlays and rebuild everything for the selected one."""
        global _selected_template
        if self.root is None or self.root.is_deleted:
            return
        self.names = list_poll_template_names()
        if self.names and _selected_template not in self.names:
            _selected_template = self.names[0]
        self.template = _selected_template if self.names else ""
        self._render()

    def _render(self) -> None:
        if self.root is None or self.root.is_deleted:
            return
        self.root.clear()
        self.hero_text = self.hero_stats = self.live_bars = self.widget = self.preview = None
        self.picker = None
        self._seen = {}
        self._twitch_status = None
        if not self.template:
            self._render_empty()
            return
        manager = get_poll_manager()
        manager.ensure_template(self.template)
        config = manager.get_config(self.template)
        snapshot = manager.get_overlay_state(self.template)
        self._status = str(snapshot.get("status") or "idle")
        self._live_marks = self._live_statuses()
        live = self._status == "open"
        if config.get("duration_seconds"):
            self._last_timer = int(config["duration_seconds"])
        style = poll_widget_info(self.template)
        self._style = style["style"]
        with self.root:
            self._hero(config, snapshot)
            error = manager.get_last_error(self.template)
            if error:
                raw_html(f'<div class="gp-alert">{icon("warning")}<span>{esc(error)}</span></div>', "")
            with ui.element("div").classes("gp-cols"):
                # The Overlay card always sits under Options / Live results, so it doesn't jump columns on start.
                with ui.element("div").classes("gp-col"):
                    if live:
                        self._live_card(snapshot, style["style"])
                    else:
                        self._options_card(config, snapshot)
                    self._overlay_card(snapshot, style)
                with ui.element("div").classes("gp-col"):
                    self._twitch_card(config, live)
                    self._timing_card(config, live)
                    self._chat_card(config)
                    self._ends_card(config, snapshot)

    def _render_empty(self) -> None:
        with self.root:
            with ui.element("div").classes("cx-empty"):
                raw_html(icon("how_to_vote"))
                ui.label("No overlay has a Poll widget yet").classes("cx-empty-title")
                ui.label("Add a Poll widget to a template in Spore Studio and save it. Each overlay with a Poll widget gets its own poll here.")
                with ui.element("div").classes("flex gap-2"):
                    ui.button("Open Spore Studio", icon="auto_awesome", on_click=self._open_spore).props("unelevated no-caps color=primary")
                    ui.button("Check again", icon="refresh", on_click=self.refresh).props("outline no-caps color=primary")

    def _rescan(self) -> None:
        """Pick up overlays added in Spore Studio since the tab was built (runs when the list opens)."""
        names = list_poll_template_names()
        if names and names != self.names and self.template in names and self.picker is not None:
            self.names = names
            self._live_marks = self._live_statuses()
            self.picker.set_options(self._picker_options(), value=self.template)

    def _live_statuses(self) -> Dict[str, bool]:
        manager = get_poll_manager()
        return {name: manager.get_overlay_state(name).get("status") == "open" for name in self.names}

    # -- hero ----------------------------------------------------------------

    def _picker_options(self) -> Dict[str, str]:
        return {name: (f"{name} · live" if self._live_marks.get(name) else name) for name in self.names}

    def _hero(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        live = self._status == "open"
        with ui.element("div").classes("gp-hero" + (" is-live" if live else "")):
            raw_html(f'<span class="gp-state">{icon("how_to_vote")}</span>')
            self.hero_text = ui.html("", sanitize=False).classes("gp-hero-text")
            ui.element("div").classes("cx-spacer")
            self.hero_stats = ui.html("", sanitize=False).classes("gp-stats")
            with ui.element("div").classes("gp-actions"):
                ui.label("Overlay").classes("gp-lbl")
                self.picker = ui.select(self._picker_options(), value=self.template, on_change=lambda e: self._pick(e.value)).props(
                    "outlined dense options-dense").classes("gp-picker").tooltip(
                    "Overlays with a Poll widget. Each has its own poll, and you can switch while one is live.")
                self.picker.on("popup-show", self._rescan)
                if live:
                    ui.button("End poll now", icon="stop", on_click=self._stop).props("unelevated no-caps color=warning text-color=dark")
                else:
                    ui.button("Start poll", icon="play_arrow", on_click=self._start).props("unelevated no-caps color=positive")
        self._update_hero(config, snapshot)

    def _update_hero(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        total = int(snapshot.get("total_votes") or 0)
        options = config.get("categories") or []
        if self._status == "open":
            if snapshot.get("permanent"):
                head = f"Open until you end it · {plural(total, 'vote')}"
                bar = ""
            else:
                remaining = int(snapshot.get("remaining_seconds") or 0)
                duration = max(1, int(snapshot.get("duration_seconds") or 1))
                head = f"{clock(remaining)} left · {plural(total, 'vote')}"
                bar = f'<div class="gp-timerbar"><i style="width:{min(100, remaining / duration * 100):.1f}%"></i></div>'
            where = " · on Twitch" if snapshot.get("twitch_poll") else ""
            text = f'<div class="gp-kicker"><span class="gp-dot"></span>Poll open{where}</div><div class="gp-head">{esc(head)}</div>{bar}'
            stats = (f'<div class="gp-stat"><b>{total:,}</b><span>Votes</span></div>'
                     f'<div class="gp-stat"><b title="{esc(leader_text(snapshot))}">{esc(leader_text(snapshot))}</b><span>Leading</span></div>')
        else:
            if self._status == "ended":
                won = winners_text(snapshot.get("winners") or [])
                kicker = f"Ended · winner {won}" if won else "Ended · no votes"
            else:
                won = winners_text(config.get("last_winners") or [])
                kicker = f"Not running · last winner {won}" if won else "Not running"
            seconds = int(config.get("duration_seconds") or 0)
            runs = f"runs for {duration_text(seconds)}" if seconds else "runs until you end it"
            example = next((o.get("keyword") for o in options if o.get("keyword")), "#option")
            how = ("Viewers vote in the Twitch poll; chat keywords don't count." if config.get("twitch_poll")
                   else f"Viewers vote by typing an option's keyword, like <b>{esc(example)}</b>.")
            text = (f'<div class="gp-kicker">{esc(kicker)}</div><div class="gp-head">{plural(len(options), "option")} · {esc(runs)}</div>'
                    f'<div class="gp-sub">{how} Votes reset when you start.</div>')
            stats = ""
        self._set_html(self.hero_text, "hero_text", text)
        self._set_html(self.hero_stats, "hero_stats", stats)

    def _set_html(self, element: Optional[ui.html], key: str, markup: str) -> None:
        if element is None or element.is_deleted or self._seen.get(key) == markup:
            return
        self._seen[key] = markup
        element.set_content(markup)

    def _pick(self, name: Any) -> None:
        global _selected_template
        name = str(name or "")
        if not name or name == self.template:
            return
        _selected_template = name
        self.template = name
        self._render()

    # -- live refresh ---------------------------------------------------------

    def _tick(self) -> None:
        if self.root is None or self.root.is_deleted:
            self._stop_timer()
            return
        if not self.template or chatbot_subtab_hidden("Polls"):
            return
        manager = get_poll_manager()
        snapshot = manager.get_overlay_state(self.template)
        status = str(snapshot.get("status") or "idle")
        if status != self._status:
            self._render()
            return
        if self._twitch_status is not None and status != "open" and twitch_polls.readiness() != self._twitch_status:
            # Signed in again, connected, or the scope check finished: update the Twitch poll card.
            self._render()
            return
        marks = self._live_statuses()
        if marks != self._live_marks and self.picker is not None and not self.picker.is_deleted:
            self._live_marks = marks
            self.picker.set_options(self._picker_options(), value=self.template)
        if status != "open":
            return
        self._update_hero(manager.get_config(self.template), snapshot)
        self._set_html(self.live_bars, "bars", bars_html(snapshot, self._style, live=True))
        self._set_html(self.widget, "widget", widget_html(snapshot, self._style))

    # -- options / results ---------------------------------------------------------

    def _live_card(self, snapshot: Dict[str, Any], style: Dict[str, Any]) -> None:
        _head, body = card("Live results", "leaderboard", "updates every second", badge_live=True)
        with body:
            self.live_bars = ui.html("", sanitize=False)
            self._set_html(self.live_bars, "bars", bars_html(snapshot, style, live=True))
            source = "Votes come from the Twitch poll. " if snapshot.get("twitch_poll") else ""
            raw_html(f'<div class="gp-hint">{icon("info")}{source}Sorted like the overlay. Options can be changed again once the poll ends.</div>', "")

    def _options_card(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        categories = config.get("categories") or []
        on_twitch = bool(config.get("twitch_poll"))
        votes = {row.get("id"): int(row.get("votes") or 0) for row in snapshot.get("options") or []}
        _head, body = card("Options", "list", plural(len(categories), "option"))
        with body:
            extra = len(categories) - TWITCH_CHOICES_MAX if on_twitch else 0
            if extra > 0:
                raw_html(f'<div class="gp-alert is-error">{icon("block")}<span>Twitch polls have at most {TWITCH_CHOICES_MAX} options. '
                         f'Remove {plural(extra, "option")} (marked in red) to start this poll.</span></div>', "")
            if categories:
                raw_html('<div class="gp-opt is-head"><span></span><span>Option</span><span>Keyword</span><span>Votes</span>'
                         '<span></span><span></span><span></span></div>', "")
            for index, cat in enumerate(categories):
                self._option_row(index, cat, votes.get(cat["id"], int(cat.get("votes") or 0)), len(categories), on_twitch)
            full = on_twitch and len(categories) >= TWITCH_CHOICES_MAX
            add = ui.element("div").classes("gp-add" + (" is-disabled" if full else ""))
            with add:
                raw_html(f"{icon('add')}<span>Add option</span>")
            if full:
                add.tooltip(f"Twitch polls have at most {TWITCH_CHOICES_MAX} options")
            else:
                add.on("click", self._add_option)
            if len(categories) < 2:
                raw_html(f'<div class="gp-hint">{icon("info")}A poll needs at least two options.</div>', "")
            if on_twitch:
                raw_html(f'<div class="gp-hint">{icon("info")}Twitch poll: {twitch_polls.CHOICES_MIN} to {twitch_polls.CHOICES_MAX} '
                         f'options, names up to {twitch_polls.CHOICE_TITLE_MAX} characters. Keywords aren\'t used while it runs on Twitch.</div>', "")
            raw_html(f'<div class="gp-hint">{icon("info")}The keyword follows the name until you change it. Viewers can type it anywhere in a message. '
                     'Votes can be corrected by hand between polls.</div>', "")

    def _option_row(self, index: int, cat: Dict[str, Any], count: int, total: int, on_twitch: bool = False) -> None:
        cid = cat["id"]
        follow = {"on": (cat.get("keyword") or "") == normalize_keyword(cat.get("name") or ""), "syncing": False}
        over = on_twitch and index >= TWITCH_CHOICES_MAX
        with ui.element("div").classes("gp-opt" + (" is-over" if over else "")):
            number = ui.label(str(index + 1)).classes("gp-opt-n")
            if over:
                number.tooltip(f"Twitch polls have at most {TWITCH_CHOICES_MAX} options")
            name = ui.input(value=cat.get("name") or "").props("outlined dense").classes("gp-opt-name")
            if on_twitch:
                # Twitch option names are at most 25 characters (the manager trims saved names too).
                name.props(f"maxlength={twitch_polls.CHOICE_TITLE_MAX}")
            keyword = ui.input(value=cat.get("keyword") or "").props("outlined dense").classes("gp-mono")
            with keyword.add_slot("append"):
                auto = ui.label("AUTO").classes("gp-auto").tooltip("Follows the option name")
            auto.set_visibility(follow["on"])
            votes = ui.number(value=count, min=0, step=1, format="%d").props("outlined dense")
            ui.button(icon="arrow_upward", on_click=lambda: self._move(cid, -1)).props("flat round dense size=sm").tooltip(
                "Move up").set_enabled(index > 0)
            ui.button(icon="arrow_downward", on_click=lambda: self._move(cid, 1)).props("flat round dense size=sm").tooltip(
                "Move down").set_enabled(index < total - 1)
            ui.button(icon="close", on_click=lambda: self._remove(cid)).props("flat round dense size=sm color=negative").tooltip("Remove")

        def on_name(_=None) -> None:
            if follow["on"] and not follow["syncing"]:
                follow["syncing"] = True
                keyword.value = normalize_keyword(name.value or "")
                follow["syncing"] = False

        def on_keyword(_=None) -> None:
            if not follow["syncing"]:
                follow["on"] = (keyword.value or "") == normalize_keyword(name.value or "")
                auto.set_visibility(follow["on"])

        def save(_=None) -> None:
            manager = get_poll_manager()
            ok, msg = manager.update_category(self.template, cid, name=name.value, keyword=keyword.value)
            if not ok:
                _warn_option(msg)
            match = next((c for c in manager.get_config(self.template).get("categories") or [] if c["id"] == cid), None)
            if match:
                follow["syncing"] = True
                name.value, keyword.value = match["name"], match["keyword"]
                follow["syncing"] = False
            self._update_hero(manager.get_config(self.template), manager.get_overlay_state(self.template))

        def save_votes(_=None) -> None:
            manager = get_poll_manager()
            ok, msg = manager.set_category_votes(self.template, cid, int(votes.value or 0))
            if not ok:
                _warn_option(msg)
            self._update_hero(manager.get_config(self.template), manager.get_overlay_state(self.template))
            self._refresh_widget()

        name.on_value_change(on_name)
        keyword.on_value_change(on_keyword)
        for box in (name, keyword):
            box.on("blur", save)
            box.on("keydown.enter", save)
        votes.on("blur", save_votes)

    def _add_option(self) -> None:
        ok, msg = get_poll_manager().add_category(self.template)
        if not ok:
            _warn_option(msg)
        self._render()

    def _move(self, cid: str, delta: int) -> None:
        ok, msg = get_poll_manager().move_category(self.template, cid, delta)
        if not ok and msg:
            _warn_option(msg)
        self._render()

    def _remove(self, cid: str) -> None:
        ok, msg = get_poll_manager().remove_category(self.template, cid)
        if not ok:
            _warn_option(msg)
        self._render()

    # -- side cards ---------------------------------------------------------------

    def _save(self, **updates: Any) -> None:
        manager = get_poll_manager()
        manager.update_config(self.template, updates)
        if self._status != "open":
            self._update_hero(manager.get_config(self.template), manager.get_overlay_state(self.template))

    def _timing_card(self, config: Dict[str, Any], live: bool) -> None:
        seconds = int(config.get("duration_seconds") or 0)
        on_twitch = bool(config.get("twitch_poll"))
        _head, body = card("Timing & voting", "timer")
        with body:
            with ui.element("div").classes("gp-row"):
                ui.label("Runs for").classes("gp-lbl gp-grow")
                if on_twitch:
                    seg({"timer": "Timer"}, "timer", self._set_mode)
                else:
                    seg({"timer": "Timer", "open": "Until I stop it"}, "timer" if seconds else "open", self._set_mode)
            amount, unit = split_duration(seconds or self._last_timer)
            units = _TWITCH_UNITS if on_twitch else _UNITS
            if unit not in units:
                amount, unit = amount * unit // 60, 60
            self.timer_row = ui.element("div").classes("gp-row")
            with self.timer_row:
                self.amount = ui.number(value=amount, min=1, step=1, format="%d").props("outlined dense debounce=400").style("width: 90px")
                self.unit = ui.select(units, value=unit).props("outlined dense options-dense").style("width: 120px")
            self.timer_row.set_visibility(bool(seconds))
            self.amount.on_value_change(lambda _e: self._save_duration())
            self.unit.on_value_change(lambda _e: self._save_duration())
            if on_twitch:
                raw_html(f'<div class="gp-hint">{icon("info")}Twitch polls run 15 seconds to 30 minutes.</div>', "")
            if live:
                raw_html(f'<div class="gp-hint">{icon("info")}Timing changes apply the next time you start.</div>', "")
            vote_hint = "Twitch counts the votes while the poll runs on Twitch"
            toggle("One vote per viewer", vote_hint if on_twitch else "Their first keyword counts",
                   bool(config.get("one_entry_per_user", True)),
                   lambda v: (self._save(one_entry_per_user=v), self._render()), enabled=not on_twitch)
            toggle("Viewers can change their vote", "" if on_twitch else "A later keyword moves their vote",
                   bool(config.get("allow_vote_switch")), lambda v: self._save(allow_vote_switch=v),
                   enabled=bool(config.get("one_entry_per_user", True)) and not on_twitch)

    def _twitch_card(self, config: Dict[str, Any], live: bool) -> None:
        enabled = bool(config.get("twitch_poll"))
        status = twitch_polls.readiness()
        self._twitch_status = status
        _head, body = card("Twitch poll", "how_to_vote", "On" if enabled else "")
        with body:
            if status == "missing":
                raw_html(f'<div class="gp-alert is-error">{icon("lock")}<span>Twitch polls can\'t be used until you sign in to Twitch again: '
                         'Mycelian needs permission to manage polls. Open Settings → Twitch and click Connect.</span></div>', "")
                ui.button("Open Twitch settings", icon="settings", on_click=self._open_twitch_settings).props(
                    "outline no-caps color=primary dense").classes("self-start")
            elif status == "not_eligible":
                raw_html(f'<div class="gp-alert is-error">{icon("block")}<span>{esc(twitch_polls.readiness_message(status))}</span></div>', "")
            elif enabled and status == "offline":
                raw_html(f'<div class="gp-alert">{icon("link_off")}<span>Twitch isn\'t connected, so the Twitch poll can\'t be created '
                         'right now. Connect Twitch in Settings → Twitch.</span></div>', "")
            elif enabled and status == "unknown":
                raw_html(f'<div class="gp-hint">{icon("hourglass_empty")}Checking your Twitch sign-in…</div>', "")
            elif enabled and status == "ok" and not live:
                raw_html(f'<div class="gp-ok">{icon("check_circle")}Your Twitch sign-in can create polls.</div>', "")
            blocked = status in ("missing", "not_eligible")
            toggle("Also run it on Twitch", "Starting creates a Twitch poll. Viewers vote there, and chat keywords don't count, "
                   "so Mycelian and Twitch show the same numbers.", enabled, self._set_twitch,
                   enabled=not live and (not blocked or enabled))
            if not enabled:
                return
            with ui.element("div").classes("gp-row"):
                ui.label("Question").classes("gp-lbl gp-grow")
            title = ui.input(value=config.get("twitch_title") or "", placeholder="What should we play next?").props(
                f"outlined dense maxlength={twitch_polls.TITLE_MAX} counter").classes("w-full")
            title.set_enabled(not live)

            def save_title(_=None) -> None:
                value = (title.value or "").strip()
                if value != get_poll_manager().get_config(self.template).get("twitch_title"):
                    self._save(twitch_title=value)

            title.on("blur", save_title)
            title.on("keydown.enter", save_title)
            toggle("Channel Points voting", "Viewers can spend points for extra votes", bool(config.get("twitch_points_enabled")),
                   lambda v: (self._save(twitch_points_enabled=v), self._render()), enabled=not live)
            if config.get("twitch_points_enabled"):
                with ui.element("div").classes("gp-row"):
                    ui.label("Points per extra vote").classes("gp-lbl gp-grow")
                    points = ui.number(value=int(config.get("twitch_points_per_vote") or 100), min=twitch_polls.POINTS_MIN,
                                       max=twitch_polls.POINTS_MAX, step=1, format="%d").props("outlined dense debounce=400").style("width: 120px")
                points.set_enabled(not live)
                points.on_value_change(lambda e: self._save(twitch_points_per_vote=twitch_polls.clamp_points(e.value)))
            problems = [] if live else twitch_polls.config_problems(config)
            for problem in problems:
                raw_html(f'<div class="gp-alert">{icon("warning")}<span>{esc(problem)}</span></div>', "")
            raw_html(f'<div class="gp-hint">{icon("info")}Only partners and affiliates can run Twitch polls, and only one at a time. '
                     'Ending the poll early here ends it on Twitch too.</div>', "")

    def _set_twitch(self, value: bool) -> None:
        if value:
            status = twitch_polls.readiness()
            if status == "missing":
                notify(twitch_polls.readiness_message(status), type="warning", source="twitch",
                       title="Sign in to Twitch again to use Twitch polls",
                       hint="Open Settings → Twitch and click Connect, then approve Mycelian in your browser.",
                       dedupe_key="twitch:poll_scope_toggle", dedupe_cooldown_sec=5.0)
            elif status in ("offline", "not_eligible"):
                notify(twitch_polls.readiness_message(status), type="warning", source="chatbot", title="Twitch poll can't run yet",
                       dedupe_key=f"twitch:poll_{status}_toggle", dedupe_cooldown_sec=5.0)
            other = get_poll_manager().twitch_poll_running(exclude=self.template)
            if other:
                notify(ONE_TWITCH_POLL_MESSAGE.format(template=other), type="info", source="chatbot",
                       title="Only one Twitch poll at a time", hint="You can still set this one up; start it once the other ends.")
            categories = get_poll_manager().get_config(self.template).get("categories") or []
            trimmed = [c["name"] for c in categories if len(c.get("name") or "") > twitch_polls.CHOICE_TITLE_MAX]
            if trimmed:
                notify(f"Twitch option names can be at most {twitch_polls.CHOICE_TITLE_MAX} characters, so "
                       f"{plural(len(trimmed), 'name')} {'was' if len(trimmed) == 1 else 'were'} shortened.",
                       type="info", source="chatbot", title="Option names shortened for Twitch",
                       details="\n".join(trimmed))
        self._save(twitch_poll=value)
        self._render()

    def _open_twitch_settings(self) -> None:
        try:
            from ..help_system.contextual_help import navigate_to_settings_subtab

            navigate_to_settings_subtab("Twitch", main_tab="Settings")
        except Exception:
            logger.debug("Could not open Settings → Twitch", exc_info=True)

    def _set_mode(self, mode: str) -> None:
        if mode == "open":
            current = int(get_poll_manager().get_config(self.template).get("duration_seconds") or 0)
            if current:
                self._last_timer = current
            self._save(duration_seconds=0)
        else:
            self._save_duration()
        if self.timer_row is not None:
            self.timer_row.set_visibility(mode != "open")

    def _save_duration(self) -> None:
        if self.amount is None or self.unit is None:
            return
        try:
            amount = max(1, int(self.amount.value or 1))
        except (TypeError, ValueError):
            amount = 1
        seconds = min(86400 * 7, amount * int(self.unit.value or 60))
        if get_poll_manager().get_config(self.template).get("twitch_poll"):
            clamped = twitch_polls.clamp_duration(seconds)
            if clamped != seconds:
                # Show what Twitch allows (15 s to 30 min) instead of keeping the typed value.
                seconds = clamped
                new_amount, new_unit = split_duration(seconds)
                if new_unit not in _TWITCH_UNITS:
                    new_amount, new_unit = seconds, 1
                self.amount.value, self.unit.value = new_amount, new_unit
        self._last_timer = seconds
        self._save(duration_seconds=seconds)

    def _chat_card(self, config: Dict[str, Any]) -> None:
        _head, body = card("Start from chat", "terminal")
        with body:
            toggle("Start it with a chat command", "You or a moderator on Twitch or YouTube types the command",
                   bool(config.get("mods_can_start")), lambda v: self._save(mods_can_start=v))
            command = ui.input(value=config.get("start_command") or "", placeholder="!startpoll").props("outlined dense").classes("gp-mono")

            def save(_=None) -> None:
                value = normalize_command(command.value or "")
                if command.value != value:
                    command.value = value
                if value != get_poll_manager().get_config(self.template).get("start_command"):
                    self._save(start_command=value)

            command.on("blur", save)
            command.on("keydown.enter", save)
            ui.label("A message that begins with the command starts this overlay's poll. Overlays that share a command start "
                     "together.").classes("gp-hint")

    def _ends_card(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        sample = list(snapshot.get("winners") or config.get("last_winners") or [])
        if not sample:
            first = next(iter(snapshot.get("options") or []), None)
            sample = [{"name": first["name"], "keyword": first["keyword"], "votes": max(1, int(first.get("votes") or 0))}] if first else [
                {"name": "Hollow Knight", "keyword": "#hollowknight", "votes": 41}]
        _head, body = card("When it ends", "emoji_events")
        with body:
            toggle("Show the winner on the chat banner", "", bool(config.get("announce_winners_as_banner")),
                   lambda v: self._save(announce_winners_as_banner=v))
            toggle("Post the winner in chat", "Twitch and YouTube", bool(config.get("announce_winners_in_chat")),
                   lambda v: self._save(announce_winners_in_chat=v))
            kit = VariableKit(lambda _i=None, _e=None: poll_variables(sample), lambda t: format_banner_message(t, sample))
            with ui.element("div").classes("ce-varhost"):
                message = ui.textarea(value=config.get("banner_message") or "").props("outlined dense rows=2 debounce=400")
                kit.button(message)
                kit.track(message)
            with ui.element("div").classes("gp-preview"):
                raw_html(icon("visibility"))
                self.preview = ui.label(format_banner_message(message.value or "", sample))

            def save_message(e) -> None:
                text = (e.value or "").strip()
                if self.preview is not None:
                    self.preview.set_text(format_banner_message(text, sample))
                if text:
                    self._save(banner_message=text)

            def restore_default(_=None) -> None:
                # An empty message isn't allowed; leaving the box empty puts the default back.
                if not (message.value or "").strip():
                    message.value = DEFAULT_BANNER_MESSAGE

            message.on_value_change(save_message)
            message.on("blur", restore_default)
            with ui.element("div").classes("gp-row"):
                ui.label("Banner stays up").classes("gp-lbl gp-grow")
                banner = ui.number(value=int(float(config.get("banner_duration") or 0)), min=0, step=1, format="%d").props(
                    "outlined dense debounce=400").style("width: 80px")
                ui.label("sec").classes("gp-hint")
            banner.on_value_change(lambda e: self._save(banner_duration=float(e.value or 0)))
            ui.label("0 keeps the banner up until another one replaces it. Ties fill {option} with every winner.").classes("gp-hint")

    def _overlay_card(self, snapshot: Dict[str, Any], info: Dict[str, Any]) -> None:
        _head, body = card("Overlay", "cast", "Spore Studio")
        with body:
            self.widget = ui.html("", sanitize=False)
            self._set_html(self.widget, "widget", widget_html(snapshot, info["style"]))
            widget_id = info.get("id") or "poll"
            extra = f" (and {info['count'] - 1} more)" if info.get("count", 0) > 1 else ""
            raw_html(f'<div class="gp-row"><span>Poll widget <b>{esc(widget_id)}</b>{esc(extra)} in the <b>{esc(self.template)}</b> template. '
                     'Colours and layout are set in Spore Studio.</span></div>', "")
            overlay_rows(self.template, self._open_spore, "Edit in Spore Studio", "auto_awesome")

    def _refresh_widget(self) -> None:
        snapshot = get_poll_manager().get_overlay_state(self.template)
        self._set_html(self.widget, "widget", widget_html(snapshot, self._style))

    def _open_spore(self) -> None:
        open_in_spore_studio(self.template)

    # -- actions ---------------------------------------------------------------------

    async def _start(self, event=None) -> None:
        button = getattr(event, "sender", None)
        if button is not None:
            button.props("loading")
        template = self.template
        # Creating a Twitch poll waits on Twitch, so start off the UI loop.
        ok, msg = await run.io_bound(get_poll_manager().start_poll, template)
        if ok:
            notify(msg, type="success", source="chatbot")
        elif msg.startswith(ONE_TWITCH_POLL_MESSAGE.split("“", 1)[0]):
            notify(msg, type="warning", source="chatbot", title="Only one Twitch poll at a time",
                   hint="End the other Twitch poll first, or start this one without Twitch.")
        else:
            notify(msg, type="warning", source="chatbot", title="Can't start the poll yet",
                   hint="Fix the poll options, then click Start.")
        if button is not None and not button.is_deleted:
            button.props(remove="loading")
        self._render()

    def _stop(self) -> None:
        ok, msg = get_poll_manager().stop_poll(self.template)
        if ok:
            notify(msg, type="success", source="chatbot")
        else:
            notify(msg, type="info", source="chatbot")
        self._render()

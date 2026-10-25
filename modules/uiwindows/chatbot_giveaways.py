# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Giveaways tab of the Chatbot window: status hero, entrants, last draw, setup, rules, overlay.

Settings save as you change them. The hero, entrants and last-draw card refresh on a light
timer (skipped while the tab is hidden). The ``giveaway`` overlay template follows the same
manager events as before; removing an entrant re-syncs its list.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from nicegui import ui

from ..giveaway_manager import DEFAULT_CONFIG, format_winners_message, get_giveaway_manager
from ..notification_engine import nav_actions_settings, notify
from ..ui_timer import layout_schedule
from .chatbot_gp_styles import avatar_color, card, copy_text, esc, icon, inject_styles, open_in_spore_studio, overlay_rows, plural, toggle
from .chatbot_gp_styles import ago as _ago
from .editor_variables import VariableKit, raw_html
from .tab_visibility import chatbot_subtab_hidden, on_chatbot_subtab_shown

logger = logging.getLogger(__name__)

OVERLAY_TEMPLATE = "giveaway"
MAX_SHOWN = 400
SAMPLE_WINNERS = ("MossyMika", "SporeKid", "GlowShroom", "PixelPip", "AmberLeaf")


def winner_variables(sample: List[str]) -> List[dict]:
    return [
        {"key": "winners", "label": "Winner names, comma separated", "example": ", ".join(sample), "group": "Giveaway"},
        {"key": "winner", "label": "Same as {winners}", "example": ", ".join(sample), "group": "Giveaway"},
        {"key": "count", "label": "How many won", "example": str(len(sample)), "group": "Giveaway"},
    ]


def group_entrants(details: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per viewer (newest first) with their ticket count, for pools that allow repeats."""
    rows: Dict[str, Dict[str, Any]] = {}
    for entry in details:
        uid = str(entry.get("user_id") or entry.get("name") or "")
        row = rows.get(uid)
        if row is None:
            rows[uid] = {**entry, "tickets": 1}
        else:
            row["tickets"] += 1
    return list(rows.values())


def entry_rules_text(cfg: Dict[str, Any]) -> str:
    mods, vips = bool(cfg.get("exclude_mods")), bool(cfg.get("exclude_vips"))
    if mods and vips:
        return "mods and VIPs can't enter"
    if mods:
        return "mods can't enter"
    if vips:
        return "VIPs can't enter"
    return "everyone can enter"


def names_text(names: List[str], limit: int = 3) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} and {len(names) - limit} more"


def _warn_announce_failed(message: str, details: str = "") -> None:
    """Winners exist but the chat announcement (a Twitch announcement) didn't send."""
    notify(
        message,
        type="warning",
        source="chatbot",
        title="Giveaway announcement didn't send",
        hint="Check that the chatbot account is signed in (Settings → Twitch) and is a moderator "
             "in your channel, then click Announce again.",
        details=details or None,
        actions=nav_actions_settings("Twitch"),
    )


class GiveawaysPanel:
    def __init__(self) -> None:
        self.query = ""
        self.root: Optional[ui.element] = None
        self.hero: Optional[ui.element] = None
        self.alert: Optional[ui.element] = None
        self.entrants: Optional[ui.element] = None
        self.count_badge: Optional[ui.label] = None
        self.last_draw: Optional[ui.element] = None
        self.keyword_input: Optional[ui.input] = None
        self.blocked_box: Optional[ui.element] = None
        self.preview: Optional[ui.label] = None
        self.step_value: Optional[ui.label] = None
        self.stats: Optional[ui.html] = None
        self._stats_markup = ""
        self._timer = None
        self._signature: Any = None
        self._entrant_signature: Any = None

    # -- layout ------------------------------------------------------------

    def build(self, container: ui.element) -> None:
        inject_styles()
        self._stop_timer()
        container.clear()
        with container:
            with ui.scroll_area().classes("w-full grow"), ui.element("div").classes("cnx w-full"):
                self.root = ui.element("div").classes("gp-wrap w-full")
        self._render_all()
        on_chatbot_subtab_shown("Giveaways", self._tick)
        self._timer = layout_schedule(1.5, self._tick)

    def refresh(self) -> None:
        self._render_all()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def _render_all(self) -> None:
        if self.root is None or self.root.is_deleted:
            return
        gm = get_giveaway_manager()
        cfg = gm.get_config()
        self.root.clear()
        with self.root:
            self.hero = ui.element("div").classes("gp-hero")
            self.alert = ui.element("div").classes("w-full")
            with ui.element("div").classes("gp-cols"):
                with ui.element("div").classes("gp-col"):
                    self._entrants_card()
                with ui.element("div").classes("gp-col"):
                    self.last_draw = ui.element("div").classes("gp-col")
                    self._setup_card(cfg)
                    self._rules_card(cfg)
                    self._overlay_card()
        self._signature = None
        self._entrant_signature = None
        self._render_live(force=True)

    def _tick(self) -> None:
        if self.root is None or self.root.is_deleted:
            self._stop_timer()
            return
        if chatbot_subtab_hidden("Giveaways"):
            return
        self._render_live()

    def _render_live(self, force: bool = False) -> None:
        gm = get_giveaway_manager()
        status = gm.get_status()
        cfg = gm.get_config()
        details = gm.get_pool_details()
        minute = int(time.time() // 60)
        # While entries pour in only the counter changes; the hero (and its buttons) rebuild
        # when the state does, so a click is never lost to a rebuild.
        pool_key = status["pool_size"] > 0 if status["accepting"] else status["pool_size"]
        signature = (status["accepting"], status["keyword"], pool_key, status["started_at"],
                     repr(status["last_draw"]), status["last_error"], cfg.get("num_winners"),
                     cfg.get("exclude_mods"), cfg.get("exclude_vips"), minute)
        if force or signature != self._signature:
            self._signature = signature
            self._render_hero(status, cfg)
            self._render_alert(status)
            self._render_last_draw(status, cfg)
        self._update_stats(int(status["pool_size"]), int(cfg.get("num_winners") or 1) if status["accepting"] else None)
        entrant_signature = (tuple((d["user_id"], d["entered_at"]) for d in details), repr(status["last_draw"]), self.query, minute)
        if force or entrant_signature != self._entrant_signature:
            self._entrant_signature = entrant_signature
            self._render_entrants(details, status)

    # -- hero ----------------------------------------------------------------

    def _render_hero(self, status: Dict[str, Any], cfg: Dict[str, Any]) -> None:
        if self.hero is None or self.hero.is_deleted:
            return
        accepting = status["accepting"]
        pool = int(status["pool_size"])
        draw = status.get("last_draw") or {}
        winners = list(draw.get("winners") or [])
        per_draw = int(cfg.get("num_winners") or 1)
        closed = not accepting and (pool or winners)
        self.hero.classes(remove="is-live is-closed", add="is-live" if accepting else ("is-closed" if closed else ""))
        draw_label = "Draw winner" if per_draw == 1 else f"Draw {per_draw} winners"
        self.hero.clear()
        self.stats = None
        with self.hero:
            if accepting:
                started = status.get("started_at") or 0
                elapsed = _ago(time.time() - started)
                since = ("Started just now" if elapsed == "just now" else f"Started {elapsed} ago") if started else "Open"
                raw_html(f'<span class="gp-state">{icon("redeem")}</span>')
                raw_html(f'<div class="gp-hero-text"><div class="gp-kicker"><span class="gp-dot"></span>Accepting entries</div>'
                         f'<div class="gp-head">Viewers type <code>{esc(status["keyword"])}</code> in chat</div>'
                         f'<div class="gp-sub">{esc(since)} · {plural(per_draw, "winner")} per draw · {esc(entry_rules_text(cfg))}</div></div>', "contents")
                ui.element("div").classes("cx-spacer")
                self._stats(pool, per_draw)
                with ui.element("div").classes("gp-actions"):
                    ui.button("Stop entries", icon="stop_circle", on_click=self._stop).props("outline no-caps color=primary").tooltip(
                        "Close entries. The pool stays so you can draw.")
                    ui.button(draw_label, icon="casino", on_click=self._draw).props("unelevated no-caps color=primary").set_enabled(pool > 0)
            elif closed:
                raw_html(f'<span class="gp-state">{icon("emoji_events" if winners else "lock")}</span>')
                if winners:
                    at = datetime.fromtimestamp(draw.get("at") or time.time())
                    said = (f"Announced in chat at {at:%H:%M}" if draw.get("announced", True)
                            else f"Drawn at {at:%H:%M}, but the chat announcement failed")
                    kicker = "Entries closed · " + ("winner drawn" if len(winners) == 1 else "winners drawn")
                    head = f"{esc(names_text(winners))} won!"
                    sub = f"{esc(said)} · {plural(pool, 'entrant')} left in the pool"
                else:
                    kicker, head = "Entries closed", "Ready to draw"
                    sub = f"{plural(pool, 'entrant')} in the pool. Draw when you're ready, or reopen entries."
                raw_html(f'<div class="gp-hero-text"><div class="gp-kicker">{kicker}</div><div class="gp-head">{head}</div>'
                         f'<div class="gp-sub">{sub}</div></div>', "contents")
                ui.element("div").classes("cx-spacer")
                self._stats(pool, None)
                with ui.element("div").classes("gp-actions"):
                    ui.button("New giveaway", icon="restart_alt", on_click=self._confirm_new).props("outline no-caps color=primary").tooltip(
                        "Empty the pool and clear the keyword")
                    ui.button("Reopen entries", icon="play_circle", on_click=self._start).props("outline no-caps color=primary")
                    again = ("Draw again" if per_draw == 1 else f"Draw {per_draw} more") if winners else draw_label
                    ui.button(again, icon="casino", on_click=self._draw).props("unelevated no-caps color=primary").set_enabled(pool > 0)
            else:
                keyword = status["keyword"]
                raw_html(f'<span class="gp-state">{icon("redeem")}</span>')
                sub = (f"Viewers will enter by typing <b>{esc(keyword)}</b> in chat." if keyword
                       else "Set the entry keyword on the right, then start. Viewers enter by typing it in chat.")
                raw_html(f'<div class="gp-hero-text"><div class="gp-kicker">Not running</div><div class="gp-head">Ready when you are</div>'
                         f'<div class="gp-sub">{sub}</div></div>', "contents")
                ui.element("div").classes("cx-spacer")
                with ui.element("div").classes("gp-actions"):
                    ui.button("Start giveaway", icon="play_arrow", on_click=self._start).props("unelevated no-caps color=positive")

    def _stats(self, pool: int, per_draw: Optional[int]) -> None:
        self.stats = ui.html("", sanitize=False).classes("gp-stats")
        self._stats_markup = ""
        self._update_stats(pool, per_draw)

    def _update_stats(self, pool: int, per_draw: Optional[int]) -> None:
        if self.stats is None or self.stats.is_deleted:
            return
        markup = f'<div class="gp-stat"><b>{pool:,}</b><span>{"Entrant" if pool == 1 else "Entrants"}</span></div>'
        if per_draw is not None:
            markup += f'<div class="gp-stat"><b>{per_draw}</b><span>Per draw</span></div>'
        if markup != self._stats_markup:
            self._stats_markup = markup
            self.stats.set_content(markup)

    def _render_alert(self, status: Dict[str, Any]) -> None:
        if self.alert is None or self.alert.is_deleted:
            return
        self.alert.clear()
        error = status.get("last_error") or ""
        if error:
            with self.alert:
                raw_html(f'<div class="gp-alert">{icon("warning")}<span>{esc(error)}</span></div>', "")

    # -- entrants ------------------------------------------------------------

    def _entrants_card(self) -> None:
        head, body = card("Entrants", "groups")
        body.classes(remove="gp-card-b")
        with head:
            search = ui.input(placeholder="Find a viewer", value=self.query).props("outlined dense clearable").classes("gp-search")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-base")
            search.on_value_change(lambda e: self._set_query(e.value or ""))
            ui.button(icon="content_copy", on_click=self._copy_entrants).props("flat round dense").tooltip("Copy every entrant's name")
        # Badge after the title: move it in front of the spacer.
        with head:
            self.count_badge = ui.label("0").classes("gp-badge")
        self.count_badge.move(head, target_index=2)
        self.entrants = body

    def _set_query(self, query: str) -> None:
        self.query = query.strip().lower()
        self._render_live()

    def _render_entrants(self, details: List[Dict[str, Any]], status: Dict[str, Any]) -> None:
        if self.entrants is None or self.entrants.is_deleted:
            return
        rows = group_entrants(details)
        if self.count_badge is not None:
            self.count_badge.set_text(f"{len(details):,}")
        winners = {w.lower() for w in (status.get("last_draw") or {}).get("winners") or []}
        shown = [r for r in rows if not self.query or self.query in str(r.get("name", "")).lower()]
        shown.sort(key=lambda r: str(r.get("name", "")).lower() not in winners)
        now = time.time()
        self.entrants.clear()
        with self.entrants:
            if not rows:
                hint = ("Nobody yet. They appear here as they type the keyword." if status["accepting"]
                        else "No entrants yet. Start the giveaway and they appear here as they type the keyword.")
                raw_html(f'<div class="gp-empty">{icon("hourglass_empty")}<span>{hint}</span></div>', "")
                return
            if not shown:
                raw_html(f'<div class="gp-empty">{icon("search_off")}<span>No entrant matches “{esc(self.query)}”.</span></div>', "")
                return
            with ui.element("div").classes("gp-list"):
                for row in shown[:MAX_SHOWN]:
                    self._entrant(row, row["name"].lower() in winners, now)
                if len(shown) > MAX_SHOWN:
                    ui.label(f"…and {len(shown) - MAX_SHOWN:,} more. Search to find someone.").classes("gp-more")

    def _entrant(self, row: Dict[str, Any], won: bool, now: float) -> None:
        name = str(row.get("name") or "?")
        entered = float(row.get("entered_at") or 0)
        with ui.element("div").classes("gp-entrant" + (" is-won" if won else "")) as chip:
            raw_html(f'<span class="gp-av" style="background:{avatar_color(name)}">{esc(name[:1].upper())}</span>'
                     f'<span class="gp-name">{esc(name)}</span>')
            if won:
                raw_html('<span class="cx-icon gp-trophy" title="Won the last draw">emoji_events</span>')
            if row.get("tickets", 1) > 1:
                ui.label(f"×{row['tickets']}").classes("gp-badge").tooltip("Entries in the pool")
            ui.label(_ago(now - entered) if entered else "").classes("gp-when")
            with ui.element("div").classes("gp-tools"):
                ui.button(icon="close", on_click=lambda r=row: self._remove(r, block=False)).props("flat round dense size=sm").tooltip(
                    "Remove from the pool")
                ui.button(icon="block", on_click=lambda r=row: self._remove(r, block=True)).props("flat round dense size=sm").tooltip(
                    "Remove and block from future giveaways")
        if entered:
            chip.tooltip(f"Entered at {datetime.fromtimestamp(entered):%H:%M:%S}")

    def _remove(self, row: Dict[str, Any], block: bool) -> None:
        if get_giveaway_manager().remove_entry(str(row.get("user_id") or ""), block=block):
            notify(f"{row.get('name')} {'removed and blocked' if block else 'removed'}", type="success", source="chatbot")
        else:
            notify(f"{row.get('name')} is no longer in the pool.", type="info", source="chatbot")
        if block:
            self._render_blocked()
        self._render_live(force=True)

    def _copy_entrants(self) -> None:
        names = [r["name"] for r in group_entrants(get_giveaway_manager().get_pool_details())]
        if not names:
            notify("No entrants to copy yet", type="info", source="chatbot")
            return
        copy_text("\n".join(names), f"Copied {plural(len(names), 'name')}")

    # -- last draw -------------------------------------------------------------

    def _render_last_draw(self, status: Dict[str, Any], cfg: Dict[str, Any]) -> None:
        if self.last_draw is None or self.last_draw.is_deleted:
            return
        self.last_draw.clear()
        draw = status.get("last_draw") or {}
        winners = list(draw.get("winners") or [])
        if not winners:
            return
        with self.last_draw:
            _head, body = card("Last draw", "emoji_events", plural(len(winners), "winner"))
            with body:
                for name in winners:
                    raw_html(f'<div class="gp-winner">{icon("emoji_events")}<span>{esc(name)}</span></div>', "")
                at = datetime.fromtimestamp(draw.get("at") or time.time())
                where = "chat and the banner" if draw.get("banner", cfg.get("announce_winners_as_banner")) else "chat"
                note = (f"Drawn at {at:%H:%M} · announced in {where}" if draw.get("announced", True)
                        else f"Drawn at {at:%H:%M} · the chat announcement didn't send")
                raw_html(f'<div class="gp-row">{icon("schedule")}<span>{esc(note)}</span></div>', "")
                with ui.element("div").classes("gp-btns"):
                    ui.button("Announce again", icon="campaign", on_click=self._announce_again).props("outline no-caps dense color=primary")
                    ui.button("Copy names", icon="content_copy", on_click=lambda: copy_text(", ".join(winners), "Winner names copied")).props(
                        "outline no-caps dense color=primary")

    def _announce_again(self) -> None:
        gm = get_giveaway_manager()
        ok, msg = gm.announce_last_draw()
        if ok:
            notify(msg, type="success", source="chatbot")
        elif not (gm.get_status().get("last_draw") or {}).get("winners"):
            notify(msg, type="info", source="chatbot")
        else:
            _warn_announce_failed("The winners weren't announced in chat.")

    # -- setup -------------------------------------------------------------------

    def _setup_card(self, cfg: Dict[str, Any]) -> None:
        gm = get_giveaway_manager()
        _head, body = card("Setup", "tune")
        with body:
            ui.label("Entry keyword").classes("gp-lbl")
            self.keyword_input = ui.input(value=cfg.get("keyword") or "", placeholder="!enter").props("outlined dense").classes("gp-mono")
            self.keyword_input.on("blur", lambda: self._save_keyword())
            self.keyword_input.on("keydown.enter", lambda: self._save_keyword())
            ui.label("Viewers send exactly this message to enter (any letter case).").classes("gp-hint")
            with ui.element("div").classes("gp-row"):
                ui.label("Winners per draw").classes("gp-lbl gp-grow")
                with ui.element("div").classes("gp-step"):
                    ui.button("−", on_click=lambda: self._step(-1)).props("flat dense")
                    self.step_value = ui.label(str(int(cfg.get("num_winners") or 1))).classes("gp-step-v")
                    ui.button("+", on_click=lambda: self._step(1)).props("flat dense")
            ui.label("Winner announcement").classes("gp-lbl")
            kit = VariableKit(lambda _i=None, _e=None: winner_variables(self._sample()), lambda t: format_winners_message(t, self._sample()))
            with ui.element("div").classes("ce-varhost"):
                message = ui.textarea(value=cfg.get("winning_message_template") or "").props("outlined dense rows=3 debounce=400")
                kit.button(message)
                kit.track(message)
            with ui.element("div").classes("gp-preview"):
                raw_html(icon("visibility"))
                self.preview = ui.label(format_winners_message(message.value or "", self._sample()))

            def save_message(e) -> None:
                text = (e.value or "").strip()
                if self.preview is not None:
                    self.preview.set_text(format_winners_message(text, self._sample()))
                if text and text != gm.get_config().get("winning_message_template"):
                    gm.set_config_field("winning_message_template", text)

            def restore_default(_=None) -> None:
                # An empty announcement isn't allowed; leaving the box empty puts the default back.
                if not (message.value or "").strip():
                    message.value = DEFAULT_CONFIG["winning_message_template"]

            message.on_value_change(save_message)
            message.on("blur", restore_default)
            toggle("Also show on the chat banner", "The chat announcement is still sent", bool(cfg.get("announce_winners_as_banner")),
                   lambda v: self._save("announce_winners_as_banner", v))

    def _sample(self) -> List[str]:
        gm = get_giveaway_manager()
        winners = list((gm.get_status().get("last_draw") or {}).get("winners") or [])
        if winners:
            return winners
        n = max(1, min(int(gm.get_config().get("num_winners") or 1), len(SAMPLE_WINNERS)))
        return list(SAMPLE_WINNERS[:n])

    def _save(self, field: str, value: Any) -> None:
        get_giveaway_manager().set_config_field(field, value)
        self._render_live(force=True)

    def _save_keyword(self, announce: bool = True) -> None:
        if self.keyword_input is None:
            return
        gm = get_giveaway_manager()
        keyword = (self.keyword_input.value or "").strip()
        before = gm.get_status()
        if keyword == before["keyword"]:
            return
        gm.set_config_field("keyword", keyword)
        # Keep the overlay in step: a new keyword re-announces it, an empty one closes entries.
        if before["accepting"]:
            if keyword:
                ok, _msg = gm.start_giveaway()
                if ok and announce:
                    notify(f"Keyword changed: viewers now type {gm.get_status()['keyword']}", type="info", source="chatbot")
            else:
                gm.stop_accepting()
                notify("Entries closed because the keyword is empty.", type="warning", source="chatbot",
                       title="Giveaway entries closed", hint="Type a keyword and click Start to reopen entries.")
        self._render_live(force=True)

    def _step(self, delta: int) -> None:
        gm = get_giveaway_manager()
        value = max(1, min(100, int(gm.get_config().get("num_winners") or 1) + delta))
        gm.set_config_field("num_winners", value)
        if self.step_value is not None:
            self.step_value.set_text(str(value))
        if self.preview is not None and not (gm.get_status().get("last_draw") or {}).get("winners"):
            self.preview.set_text(format_winners_message(gm.get_config().get("winning_message_template") or "", self._sample()))
        self._render_live(force=True)

    # -- rules -------------------------------------------------------------------

    def _rules_card(self, cfg: Dict[str, Any]) -> None:
        _head, body = card("Rules", "rule")
        with body:
            for field, label, hint in (
                ("no_duplicate_entries", "One entry per viewer", "Typing the keyword again doesn't add more entries"),
                ("unique_winners_per_draw", "Can't win twice in one draw", "Each winner in a draw is a different viewer"),
                ("remove_winners_from_pool", "Remove winners after a draw", "So Draw again picks someone new"),
                ("exclude_mods", "Moderators can't enter", ""),
                ("exclude_vips", "VIPs can't enter", ""),
            ):
                toggle(label, hint, bool(cfg.get(field)), lambda v, f=field: self._save(f, v))
            ui.label("Blocked viewers").classes("gp-lbl").style("margin-top: 4px")
            self.blocked_box = ui.element("div").classes("gp-chips")
            blocked_in = ui.input(placeholder="Add a name and press Enter").props("outlined dense")

            def add(_=None) -> None:
                name = (blocked_in.value or "").strip().lstrip("@").lower()
                blocked_in.value = ""
                if not name:
                    return
                gm = get_giveaway_manager()
                names = list(gm.get_config().get("blocked_usernames") or [])
                if name not in names:
                    gm.set_config_field("blocked_usernames", names + [name])
                self._render_blocked()

            blocked_in.on("keydown.enter", add)
            blocked_in.on("blur", add)
        self._render_blocked()

    def _render_blocked(self) -> None:
        if self.blocked_box is None or self.blocked_box.is_deleted:
            return
        names = list(get_giveaway_manager().get_config().get("blocked_usernames") or [])
        self.blocked_box.clear()
        with self.blocked_box:
            if not names:
                ui.label("Nobody blocked. Bots like nightbot are worth adding.").classes("gp-hint")
            for name in names:
                with ui.element("div").classes("gp-chip"):
                    ui.label(name)
                    ui.button(icon="close", on_click=lambda n=name: self._unblock(n)).props("flat round dense size=xs").tooltip("Unblock")

    def _unblock(self, name: str) -> None:
        gm = get_giveaway_manager()
        gm.set_config_field("blocked_usernames", [n for n in gm.get_config().get("blocked_usernames") or [] if n != name])
        self._render_blocked()

    # -- overlay -------------------------------------------------------------------

    def _overlay_card(self) -> None:
        _head, body = card("Overlay", "cast", "Spore Studio")
        with body:
            ui.label("Shows the keyword, entrants and winners on stream, with confetti for the winner. "
                     "Change its look in Spore Studio.").classes("gp-row")
            overlay_rows(OVERLAY_TEMPLATE, lambda: open_in_spore_studio(OVERLAY_TEMPLATE), "Edit in Spore Studio", "auto_awesome")

    # -- actions ---------------------------------------------------------------------

    def _start(self) -> None:
        self._save_keyword(announce=False)
        gm = get_giveaway_manager()
        ok, msg = gm.start_giveaway()
        if ok:
            notify(f"Accepting entries: viewers type {gm.get_status()['keyword']}", type="success", source="chatbot")
        else:
            notify(msg or "", type="warning", source="chatbot", title="Couldn't start the giveaway",
                   hint="Type an entry keyword, then click Start.")
            if self.keyword_input is not None:
                self.keyword_input.run_method("focus")
        self._render_live(force=True)

    def _stop(self) -> None:
        get_giveaway_manager().stop_accepting()
        notify("Entries closed", type="info", source="chatbot")
        self._render_live(force=True)

    def _draw(self) -> None:
        gm = get_giveaway_manager()
        ok, msg, winners = gm.draw_winners()
        if not ok:
            notify(msg, type="info", source="chatbot", title="No winners drawn")
        elif (gm.get_status().get("last_draw") or {}).get("announced", True):
            notify(f"Drew {plural(len(winners), 'winner')}", type="success", source="chatbot")
        else:
            _warn_announce_failed(
                f"Drew {plural(len(winners), 'winner')}, but the chat announcement didn't send.",
                details=gm.get_last_error(),
            )
        if self.preview is not None:
            self.preview.set_text(format_winners_message(get_giveaway_manager().get_config().get("winning_message_template") or "",
                                                         self._sample()))
        self._render_live(force=True)

    def _confirm_new(self) -> None:
        gm = get_giveaway_manager()
        pool = gm.get_pool_size()
        with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
            ui.label("Start a new giveaway?").classes("text-lg font-bold mb-1")
            ui.label((f"This empties the pool ({plural(pool, 'entrant')}), " if pool else "This ")
                     + "clears the keyword and forgets the last draw. The overlay clears too.").classes("secondary-text mb-3")

            def go() -> None:
                dialog.close()
                gm.clear_giveaway()
                if self.keyword_input is not None:
                    self.keyword_input.value = ""
                notify("Cleared. Set a keyword to start the next giveaway.", type="info", source="chatbot")
                self._render_all()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("outline no-caps")
                ui.button("New giveaway", icon="restart_alt", on_click=go).props("color=primary unelevated no-caps")
        dialog.open()

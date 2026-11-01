# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Predictions tab of the Chatbot window: overlay picker, question and outcomes or live bets, side cards.

Each overlay (Spore Studio template with a Prediction widget) keeps its own prediction setup.
Predictions run on Twitch (viewers bet channel points there), one per channel at a time, so
starting is blocked while another is live. Settings save as you change them; live numbers refresh
every second while the tab is visible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from nicegui import run, ui

from .. import twitch_predictions as tp
from ..notification_engine import notify
from ..prediction_manager import (
    DEFAULT_REFUND_MESSAGE,
    DEFAULT_RESULT_MESSAGE,
    format_message,
    get_prediction_manager,
    list_prediction_template_names,
)
from ..ui_timer import layout_schedule
from .chatbot_gp_styles import ago, card, clock, duration_text, esc, icon, inject_styles, open_in_spore_studio, overlay_rows, plural, toggle
from .editor_variables import VariableKit, raw_html
from .tab_visibility import chatbot_subtab_hidden, on_chatbot_subtab_shown

logger = logging.getLogger(__name__)

TAB = "Predictions"
OUTCOME_COLORS = {"blue": "#387aff", "pink": "#f5009b"}
_UNITS = {1: "seconds", 60: "minutes"}
LIVE = ("active", "locked")

# Remembered across rebuilds so the tab reopens on the overlay the user last picked.
_selected_template = ""


def outcome_color(outcome: Dict[str, Any], index: int) -> str:
    return OUTCOME_COLORS.get(str(outcome.get("color") or ""), OUTCOME_COLORS["blue" if index == 0 else "pink"])


def sample_snapshot(config: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """A resolved-looking snapshot for message previews (the live one once it has bets)."""
    if snapshot.get("total_points"):
        sample = dict(snapshot)
    else:
        outcomes = [{"name": o["name"], "users": 0, "points": 0, "winner": False, "top_predictors": []}
                    for o in config.get("outcomes") or []] or [{"name": "Yes"}, {"name": "No"}]
        outcomes[0] = {**outcomes[0], "users": 23, "points": 41_250, "winner": True,
                       "top_predictors": [{"name": "MushroomFan", "points_used": 5000, "points_won": 9800}]}
        sample = {"title": config.get("title") or "Will we beat the boss?", "outcomes": outcomes,
                  "total_points": 78_600, "total_users": 51}
    if not any(o.get("winner") for o in sample.get("outcomes") or []):
        best = max(sample.get("outcomes") or [{}], key=lambda o: o.get("points") or 0)
        sample["outcomes"] = [{**o, "winner": o is best} for o in sample.get("outcomes") or []]
    return sample


def prediction_variables(sample: Dict[str, Any]) -> List[dict]:
    def ex(key: str) -> str:
        return format_message("{" + key + "}", sample)

    return [
        {"key": "title", "label": "The question", "example": ex("title"), "group": "Prediction"},
        {"key": "winner", "label": "The outcome that won", "example": ex("winner"), "group": "Prediction"},
        {"key": "points", "label": "Channel points bet in total", "example": ex("points"), "group": "Prediction"},
        {"key": "winners", "label": "Predictors who won", "example": ex("winners"), "group": "Prediction"},
        {"key": "users", "label": "Predictors in total", "example": ex("users"), "group": "Prediction"},
        {"key": "top", "label": "The biggest winner", "example": ex("top"), "group": "Prediction"},
    ]


def outcomes_html(snapshot: Dict[str, Any]) -> str:
    """Live bets per outcome: bar by share of points, points, predictors, payout ratio, top predictor."""
    outcomes = list(snapshot.get("outcomes") or [])
    if not outcomes:
        return '<div class="gp-hint">No outcomes yet.</div>'
    rows = []
    for i, o in enumerate(outcomes):
        color = esc(outcome_color(o, i))
        ratio = f"1:{float(o['ratio']):g}" if o.get("ratio") else "—"
        top = (o.get("top_predictors") or [None])[0]
        top_text = f' · top {esc(top["name"])} ({int(top.get("points_used") or 0):,})' if top else ""
        rows.append(
            f'<div><div class="gp-bar-top">{"🏆 " if o.get("winner") else ""}{esc(o.get("name"))}'
            f'<span><b>{int(o.get("points") or 0):,}</b> pts · {plural(int(o.get("users") or 0), "predictor")} · {ratio}{top_text}</span></div>'
            f'<div class="gp-bar-track" style="background:color-mix(in srgb, {color} 16%, transparent)">'
            f'<div class="gp-bar-fill" style="width:{float(o.get("percent") or 0):.2f}%;background:{color}"></div></div></div>'
        )
    return '<div class="gp-bars">' + "".join(rows) + "</div>"


def widget_html(snapshot: Dict[str, Any]) -> str:
    """A small stand-in for the Spore Studio Prediction widget."""
    parts = [f'<div class="gp-widget-title">{esc(snapshot.get("title") or "Your question")}</div>']
    for i, o in enumerate((snapshot.get("outcomes") or [])[:6]):
        color = esc(outcome_color(o, i))
        pct = float(o.get("percent") or 0)
        parts.append(
            f'<div class="gp-widget-row"><div class="gp-widget-top"><span>{"🏆 " if o.get("winner") else ""}{esc(o.get("name"))}</span>'
            f'<span>{int(o.get("points") or 0):,} · {pct:g}%</span></div>'
            f'<div class="gp-widget-track" style="background:rgba(255,255,255,.12)">'
            f'<div class="gp-widget-fill" style="width:{pct:.2f}%;background:{color}"></div></div></div>'
        )
    return '<div class="gp-widget" style="background:#18181b;--gp-widget-text:#efeff1">' + "".join(parts) + "</div>"


def _warn(msg: str, title: str = "Prediction not changed") -> None:
    notify(msg or "", type="warning", source="chatbot", title=title)


class PredictionsPanel:
    def __init__(self) -> None:
        self.root: Optional[ui.element] = None
        self.names: List[str] = []
        self.template = ""
        self.picker: Optional[ui.select] = None
        self.hero_text: Optional[ui.html] = None
        self.hero_stats: Optional[ui.html] = None
        self.live_html: Optional[ui.html] = None
        self.widget: Optional[ui.html] = None
        self.preview: Optional[ui.label] = None
        self._timer = None
        self._status = ""
        self._twitch_status: Optional[str] = None
        self._seen: Dict[str, str] = {}
        self._synced = False

    # -- layout ------------------------------------------------------------

    def build(self, container: ui.element) -> None:
        inject_styles()
        self._stop_timer()
        container.clear()
        with container:
            with ui.scroll_area().classes("w-full grow"), ui.element("div").classes("cnx w-full"):
                self.root = ui.element("div").classes("gp-wrap w-full")
        self.refresh()
        on_chatbot_subtab_shown(TAB, self._tick)
        self._timer = layout_schedule(1.0, self._tick)
        if not self._synced:
            self._synced = True
            self._sync_from_twitch()

    def _sync_from_twitch(self) -> None:
        """Pick up a prediction already running on Twitch (after a restart or a dashboard start)."""
        async def go() -> None:
            try:
                await run.io_bound(get_prediction_manager().sync_from_twitch)
            except Exception:
                logger.debug("prediction sync failed", exc_info=True)

        try:
            from nicegui import background_tasks

            background_tasks.create(go(), name="prediction_sync")
        except Exception:
            logger.debug("prediction sync not started", exc_info=True)

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
        self.names = list_prediction_template_names()
        live = get_prediction_manager().live_template()
        if live in self.names:
            _selected_template = live  # always open on the running prediction
        if self.names and _selected_template not in self.names:
            _selected_template = self.names[0]
        self.template = _selected_template if self.names else ""
        self._render()

    def _render(self) -> None:
        if self.root is None or self.root.is_deleted:
            return
        self.root.clear()
        self.hero_text = self.hero_stats = self.live_html = self.widget = self.preview = None
        self.picker = None
        self._seen = {}
        self._twitch_status = None
        if not self.template:
            self._render_empty()
            return
        manager = get_prediction_manager()
        config = manager.get_config(self.template)
        snapshot = manager.get_overlay_state(self.template)
        self._status = str(snapshot.get("status") or "idle")
        live = self._status in LIVE
        with self.root:
            self._hero(config, snapshot)
            error = manager.get_last_error(self.template)
            if error and not live:
                raw_html(f'<div class="gp-alert">{icon("warning")}<span>{esc(error)}</span></div>', "")
            with ui.element("div").classes("gp-cols"):
                with ui.element("div").classes("gp-col"):
                    if live or self._status in ("resolved", "canceled"):
                        self._live_card(snapshot)
                    if not live:
                        self._setup_card(config)
                    self._overlay_card(snapshot)
                with ui.element("div").classes("gp-col"):
                    self._twitch_card(live)
                    self._timing_card(config, live)
                    self._ends_card(config, snapshot)
                    self._last_card(config)

    def _render_empty(self) -> None:
        with self.root:
            with ui.element("div").classes("cx-empty"):
                raw_html(icon("insights"))
                ui.label("No overlay has a Prediction widget yet").classes("cx-empty-title")
                ui.label("Add a Prediction widget to a template in Spore Studio (or use the ready-made “predictions” overlay) "
                         "and save it. Each overlay with a Prediction widget gets its own prediction setup here.")
                with ui.element("div").classes("flex gap-2"):
                    ui.button("Open Spore Studio", icon="auto_awesome", on_click=lambda: open_in_spore_studio("predictions")).props(
                        "unelevated no-caps color=primary")
                    ui.button("Check again", icon="refresh", on_click=self.refresh).props("outline no-caps color=primary")

    # -- hero ----------------------------------------------------------------

    def _picker_options(self) -> Dict[str, str]:
        live = get_prediction_manager().live_template()
        return {name: (f"{name} · live" if name == live else name) for name in self.names}

    def _hero(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        live = self._status in LIVE
        with ui.element("div").classes("gp-hero" + (" is-live" if live else "")):
            raw_html(f'<span class="gp-state">{icon("insights")}</span>')
            self.hero_text = ui.html("", sanitize=False).classes("gp-hero-text")
            ui.element("div").classes("cx-spacer")
            self.hero_stats = ui.html("", sanitize=False).classes("gp-stats")
            with ui.element("div").classes("gp-actions"):
                ui.label("Overlay").classes("gp-lbl")
                self.picker = ui.select(self._picker_options(), value=self.template, on_change=lambda e: self._pick(e.value)).props(
                    "outlined dense options-dense").classes("gp-picker").tooltip(
                    "Overlays with a Prediction widget. Each keeps its own question and outcomes.")
                self.picker.on("popup-show", self._rescan)
                if self._status == "active":
                    ui.button("Lock bets", icon="lock", on_click=self._lock).props("unelevated no-caps color=warning text-color=dark")
                if live:
                    ui.button("Cancel & refund", icon="undo", on_click=self._cancel).props("outline no-caps color=negative")
                else:
                    ui.button("Start prediction", icon="play_arrow", on_click=self._start).props("unelevated no-caps color=positive")
        self._update_hero(config, snapshot)

    def _update_hero(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        points = int(snapshot.get("total_points") or 0)
        users = int(snapshot.get("total_users") or 0)
        title = snapshot.get("title") or config.get("title") or "No question yet"
        stats = (f'<div class="gp-stat"><b>{points:,}</b><span>Points</span></div>'
                 f'<div class="gp-stat"><b>{users:,}</b><span>Predictors</span></div>')
        bar = ""
        if self._status == "active":
            remaining = int(snapshot.get("remaining_seconds") or 0)
            window = max(1, int(config.get("window_seconds") or 1))
            kicker = f'<span class="gp-dot"></span>Taking bets · {clock(remaining)} left'
            bar = f'<div class="gp-timerbar"><i style="width:{min(100, remaining / window * 100):.1f}%"></i></div>'
            sub = "Lock the bets early, or wait for the timer. Pick the winner once you know it."
        elif self._status == "locked":
            kicker = "Bets locked · pick the outcome that won"
            sub = "Click “Won” on the winning outcome to pay out, or cancel to refund everyone."
        elif self._status == "resolved":
            winner = next((o.get("name") for o in snapshot.get("outcomes") or [] if o.get("winner")), "")
            kicker = f"Ended · {winner} won" if winner else "Ended"
            sub = "Start again to run this prediction once more."
        elif self._status == "canceled":
            kicker = "Canceled · points refunded"
            sub = "Start again to run this prediction once more."
        else:
            kicker = "Not running"
            sub = (f'{plural(len(config.get("outcomes") or []), "outcome")} · bets open for '
                   f'{duration_text(int(config.get("window_seconds") or 0))}. Viewers bet channel points on Twitch.')
            stats = ""
        text = (f'<div class="gp-kicker">{kicker}</div><div class="gp-head">{esc(title)}</div>{bar}'
                f'<div class="gp-sub">{esc(sub)}</div>')
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

    def _rescan(self) -> None:
        names = list_prediction_template_names()
        if names and names != self.names and self.template in names and self.picker is not None:
            self.names = names
            self.picker.set_options(self._picker_options(), value=self.template)

    # -- live refresh ---------------------------------------------------------

    def _tick(self) -> None:
        if self.root is None or self.root.is_deleted:
            self._stop_timer()
            return
        if chatbot_subtab_hidden(TAB):
            return
        manager = get_prediction_manager()
        live = manager.live_template()
        if live and live in list_prediction_template_names() and live != self.template:
            self.refresh()  # a prediction started elsewhere (Twitch dashboard, connector): show it
            return
        if not self.template:
            return
        snapshot = manager.get_overlay_state(self.template)
        status = str(snapshot.get("status") or "idle")
        if status != self._status:
            self._render()
            return
        if self._twitch_status is not None and status not in LIVE and tp.readiness() != self._twitch_status:
            self._render()
            return
        if status not in LIVE:
            return
        self._update_hero(manager.get_config(self.template), snapshot)
        self._set_html(self.live_html, "live", outcomes_html(snapshot))
        self._set_html(self.widget, "widget", widget_html(snapshot))

    # -- cards -----------------------------------------------------------------

    def _live_card(self, snapshot: Dict[str, Any]) -> None:
        live = self._status in LIVE
        _head, body = card("Live bets" if live else "Result", "leaderboard", "updates every second" if live else "",
                           badge_live=live)
        with body:
            self.live_html = ui.html("", sanitize=False)
            self._set_html(self.live_html, "live", outcomes_html(snapshot))
            if live:
                with ui.element("div").classes("gp-btns"):
                    for o in snapshot.get("outcomes") or []:
                        ui.button(f"{o['name']} won", icon="emoji_events",
                                  on_click=lambda _e, oid=o["id"], name=o["name"]: self._resolve(oid, name)).props(
                            "outline no-caps dense color=positive")
                raw_html(f'<div class="gp-hint">{icon("info")}Paying out ends the prediction on Twitch. Bets lock on their own '
                         'when the timer runs out.</div>', "")

    def _setup_card(self, config: Dict[str, Any]) -> None:
        outcomes = config.get("outcomes") or []
        _head, body = card("Question & outcomes", "list", plural(len(outcomes), "outcome"))
        with body:
            title = ui.input(value=config.get("title") or "", placeholder="Will we beat the boss on this try?").props(
                f"outlined dense maxlength={tp.TITLE_MAX} counter").classes("w-full")

            def save_title(_=None) -> None:
                value = (title.value or "").strip()
                if value != get_prediction_manager().get_config(self.template).get("title"):
                    self._save(title=value)

            title.on("blur", save_title)
            title.on("keydown.enter", save_title)
            for index, outcome in enumerate(outcomes):
                self._outcome_row(index, outcome, len(outcomes))
            full = len(outcomes) >= tp.OUTCOMES_MAX
            add = ui.element("div").classes("gp-add" + (" is-disabled" if full else ""))
            with add:
                raw_html(f"{icon('add')}<span>Add outcome</span>")
            if full:
                add.tooltip(f"Twitch predictions have at most {tp.OUTCOMES_MAX} outcomes")
            else:
                add.on("click", self._add_outcome)
            for problem in tp.config_problems(config):
                raw_html(f'<div class="gp-alert">{icon("warning")}<span>{esc(problem)}</span></div>', "")
            raw_html(f'<div class="gp-hint">{icon("info")}Question up to {tp.TITLE_MAX} characters; {tp.OUTCOMES_MIN} to '
                     f'{tp.OUTCOMES_MAX} outcomes, names up to {tp.OUTCOME_TITLE_MAX} characters. The first outcome is blue on '
                     'Twitch, the others pink.</div>', "")

    def _outcome_row(self, index: int, outcome: Dict[str, Any], total: int) -> None:
        oid = outcome["id"]
        with ui.element("div").classes("gp-row"):
            raw_html(f'<span class="gp-opt-n" style="color:{outcome_color(outcome, index)}">{index + 1}</span>', "")
            name = ui.input(value=outcome.get("name") or "").props(f"outlined dense maxlength={tp.OUTCOME_TITLE_MAX}").classes("gp-grow")
            ui.button(icon="close", on_click=lambda: self._remove_outcome(oid)).props("flat round dense size=sm color=negative").tooltip(
                "Remove").set_enabled(total > tp.OUTCOMES_MIN)

        def save(_=None) -> None:
            get_prediction_manager().rename_outcome(self.template, oid, name.value or "")

        name.on("blur", save)
        name.on("keydown.enter", save)

    def _add_outcome(self) -> None:
        ok, msg = get_prediction_manager().add_outcome(self.template)
        if not ok:
            _warn(msg)
        self._render()

    def _remove_outcome(self, oid: str) -> None:
        ok, msg = get_prediction_manager().remove_outcome(self.template, oid)
        if not ok:
            _warn(msg)
        self._render()

    def _save(self, **updates: Any) -> None:
        manager = get_prediction_manager()
        manager.update_config(self.template, **updates)
        if self._status not in LIVE:
            self._update_hero(manager.get_config(self.template), manager.get_overlay_state(self.template))

    def _twitch_card(self, live: bool) -> None:
        status = tp.readiness()
        self._twitch_status = status
        _head, body = card("Twitch", "insights")
        with body:
            if status == "missing":
                raw_html(f'<div class="gp-alert is-error">{icon("lock")}<span>{esc(tp.readiness_message(status))}</span></div>', "")
                ui.button("Open Twitch settings", icon="settings", on_click=self._open_twitch_settings).props(
                    "outline no-caps color=primary dense").classes("self-start")
            elif status in ("not_eligible", "offline"):
                raw_html(f'<div class="gp-alert is-error">{icon("block")}<span>{esc(tp.readiness_message(status))}</span></div>', "")
            elif status == "unknown":
                raw_html(f'<div class="gp-hint">{icon("hourglass_empty")}Checking your Twitch sign-in…</div>', "")
            elif not live:
                raw_html(f'<div class="gp-ok">{icon("check_circle")}Your Twitch sign-in can run predictions.</div>', "")
            raw_html(f'<div class="gp-hint">{icon("info")}Predictions run on Twitch: viewers bet channel points there, and only '
                     'one prediction runs per channel. One started from the Twitch dashboard shows here and on the overlay too.</div>', "")

    def _open_twitch_settings(self) -> None:
        try:
            from ..help_system.contextual_help import navigate_to_settings_subtab

            navigate_to_settings_subtab("Twitch", main_tab="Settings")
        except Exception:
            logger.debug("Could not open Settings → Twitch", exc_info=True)

    def _timing_card(self, config: Dict[str, Any], live: bool) -> None:
        seconds = int(config.get("window_seconds") or 120)
        amount, unit = (seconds // 60, 60) if seconds % 60 == 0 else (seconds, 1)
        _head, body = card("Betting time", "timer")
        with body:
            with ui.element("div").classes("gp-row"):
                ui.label("Bets stay open for").classes("gp-lbl gp-grow")
                number = ui.number(value=amount, min=1, step=1, format="%d").props("outlined dense debounce=400").style("width: 90px")
                units = ui.select(_UNITS, value=unit).props("outlined dense options-dense").style("width: 120px")

            def save(_=None) -> None:
                try:
                    total = max(1, int(number.value or 1)) * int(units.value or 60)
                except (TypeError, ValueError):
                    total = 120
                clamped = tp.clamp_window(total)
                if clamped != total:
                    number.value, units.value = (clamped // 60, 60) if clamped % 60 == 0 else (clamped, 1)
                self._save(window_seconds=clamped)

            number.on_value_change(save)
            units.on_value_change(save)
            hint = "Twitch takes bets for 30 seconds to 30 minutes."
            if live:
                hint += " Changes apply the next time you start."
            raw_html(f'<div class="gp-hint">{icon("info")}{esc(hint)}</div>', "")

    def _ends_card(self, config: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        sample = sample_snapshot(config, snapshot)
        _head, body = card("When it ends", "emoji_events")
        with body:
            toggle("Show the result on the chat banner", "", bool(config.get("announce_banner")),
                   lambda v: self._save(announce_banner=v))
            toggle("Post the result in chat", "Twitch chat", bool(config.get("announce_chat")),
                   lambda v: self._save(announce_chat=v))
            ui.label("When someone wins").classes("gp-lbl")
            kit = VariableKit(lambda _i=None, _e=None: prediction_variables(sample), lambda t: format_message(t, sample))
            with ui.element("div").classes("ce-varhost"):
                message = ui.textarea(value=config.get("result_message") or "").props("outlined dense rows=2 debounce=400")
                kit.button(message)
                kit.track(message)
            with ui.element("div").classes("gp-preview"):
                raw_html(icon("visibility"))
                self.preview = ui.label(format_message(message.value or "", sample))

            def save_result(e) -> None:
                text = (e.value or "").strip()
                if self.preview is not None:
                    self.preview.set_text(format_message(text, sample))
                if text:
                    self._save(result_message=text)

            message.on_value_change(save_result)
            message.on("blur", lambda _e: (not (message.value or "").strip()) and setattr(message, "value", DEFAULT_RESULT_MESSAGE))
            ui.label("When it's canceled").classes("gp-lbl")
            refund = ui.textarea(value=config.get("refund_message") or "").props("outlined dense rows=2 debounce=400")
            refund.on_value_change(lambda e: (e.value or "").strip() and self._save(refund_message=e.value.strip()))
            refund.on("blur", lambda _e: (not (refund.value or "").strip()) and setattr(refund, "value", DEFAULT_REFUND_MESSAGE))
            with ui.element("div").classes("gp-row"):
                ui.label("Banner stays up").classes("gp-lbl gp-grow")
                banner = ui.number(value=int(config.get("banner_duration") or 10), min=1, max=120, step=1, format="%d").props(
                    "outlined dense debounce=400").style("width: 80px")
                ui.label("sec").classes("gp-hint")
            banner.on_value_change(lambda e: self._save(banner_duration=int(e.value or 10)))

    def _last_card(self, config: Dict[str, Any]) -> None:
        last = config.get("last_result")
        if not isinstance(last, dict):
            return
        _head, body = card("Last prediction", "history", ago(float(last.get("ended_at") or 0)) if last.get("ended_at") else "")
        with body:
            if last.get("status") == "canceled":
                line = f"“{esc(last.get('title'))}” was canceled and refunded."
            else:
                line = (f"“{esc(last.get('title'))}”: <b>{esc(last.get('winner') or '—')}</b> won · "
                        f"{int(last.get('points') or 0):,} points from {plural(int(last.get('users') or 0), 'predictor')}")
            raw_html(f'<div class="gp-winner">{icon("emoji_events")}<span>{line}</span></div>', "")

    def _overlay_card(self, snapshot: Dict[str, Any]) -> None:
        _head, body = card("Overlay", "cast", "Spore Studio")
        with body:
            self.widget = ui.html("", sanitize=False)
            self._set_html(self.widget, "widget", widget_html(snapshot))
            raw_html(f'<div class="gp-row"><span>The Prediction widget in the <b>{esc(self.template)}</b> template shows the '
                     'live bets. Colours and layout are set in Spore Studio.</span></div>', "")
            overlay_rows(self.template, lambda: open_in_spore_studio(self.template), "Edit in Spore Studio", "auto_awesome")

    # -- actions ---------------------------------------------------------------------

    async def _run(self, event, fn, *args) -> tuple:
        button = getattr(event, "sender", None)
        if button is not None:
            button.props("loading")
        try:
            return await run.io_bound(fn, *args)
        finally:
            if button is not None and not button.is_deleted:
                button.props(remove="loading")

    async def _start(self, event=None) -> None:
        ok, msg = await self._run(event, get_prediction_manager().start_prediction, self.template)
        if ok:
            notify(msg, type="success", source="chatbot")
        else:
            notify(msg, type="warning", source="chatbot", title="Can't start the prediction yet")
        self._render()

    async def _lock(self, event=None) -> None:
        ok, msg = await self._run(event, get_prediction_manager().lock_prediction)
        notify(msg, type="success" if ok else "warning", source="chatbot", title=None if ok else "Couldn't lock the bets")
        self._render()

    async def _resolve(self, outcome_id: str, name: str) -> None:
        ok, msg = await run.io_bound(get_prediction_manager().resolve_prediction, outcome_id)
        if ok:
            notify(f"Paying out to “{name}”.", type="success", source="chatbot")
        else:
            notify(msg, type="warning", source="chatbot", title="Couldn't pick the winner")
        self._render()

    async def _cancel(self, event=None) -> None:
        ok, msg = await self._run(event, get_prediction_manager().cancel_prediction)
        notify(msg, type="success" if ok else "warning", source="chatbot", title=None if ok else "Couldn't cancel the prediction")
        self._render()

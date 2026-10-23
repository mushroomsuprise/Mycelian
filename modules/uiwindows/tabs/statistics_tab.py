# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Settings → Statistics dashboard.

Two data sources, both read-only here:

* The timestamped event log (``user_events``), queried through
  ``statistics_insights`` for anything scoped by the date range: headline tiles with
  deltas, activity over time, the weekly heatmap, leaderboards, viewer lookup and the
  highlights export.
* The lifetime aggregates held by ``StatisticsManager`` (``get_all_statistics``),
  shown on the Lifetime section and refreshed live.
"""

from __future__ import annotations

import base64
import html
import io
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from nicegui import ui

from ... import dataobjects
from ...notification_engine import notify
from ...statistics_insights import (
    EVENT_TYPES,
    METRICS,
    METRICS_BY_KEY,
    PRESETS,
    DateRange,
    Insights,
    Metric,
    compact,
    custom_range,
    pct_change,
    preset_range,
)
from ...statistics_manager import get_statistics_manager
from ...ui_buttons import primary_button
from ...ui_form_controls import form_input
from ...ui_timer import layout_schedule
from .statistics_charts import ChartColors, level_columns, time_columns, weekly_heatmap
from .statistics_styles import STATISTICS_CSS

logger = logging.getLogger(__name__)

_css_injected = False

SECTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("overview", "Overview", "insights"),
    ("leaders", "Leaderboards", "leaderboard"),
    ("lifetime", "Lifetime", "all_inclusive"),
    ("lookup", "Viewer lookup", "person_search"),
    ("export", "Export highlights", "photo_camera"),
)

# Leaderboard cards: (title, icon, event types, aggregation, value suffix)
LEADERBOARDS: Tuple[Tuple[str, str, Tuple[str, ...], str, str], ...] = (
    ("Top chatters", "chat", ("chat_message",), "count", "messages"),
    ("Top cheerers", "diamond", ("bit",), "sum", "bits"),
    ("Top gifters", "redeem", ("giftsub",), "sum", "gifted"),
    ("Channel points", "toll", ("point_redeem",), "sum", "points"),
    ("Subscribers", "star", ("sub", "resub"), "count", "subs"),
    ("Longest watch streaks", "local_fire_department", ("watch_streak",), "max", "streams"),
    ("Raiders", "groups_2", ("raid",), "count", "raids"),
    ("Giveaway entries", "confirmation_number", ("giveaway_entry",), "count", "entries"),
    ("Bot command users", "terminal", ("chatbot_command",), "count", "uses"),
)

LIVE_INTERVAL_SEC = 5
LIVE_REQUERY_EVERY = 6  # ticks: re-query the event log every 30s while live


def _icon(name: str, extra: str = "") -> str:
    return f'<span class="material-icons {extra}" aria-hidden="true">{html.escape(name)}</span>'


def _initial(name: str) -> str:
    name = (name or "?").strip()
    return html.escape(name[:1].upper() or "?")


def _sparkline(values: Sequence[float], width: int = 74, height: int = 24) -> str:
    """Tiny trend line in the de-emphasis ink with the latest point in the accent."""
    vals = [float(v or 0) for v in values]
    if len(vals) > 12:
        # About 12 points: sum into equal bins so daily on/off-stream gaps don't read as noise.
        size = len(vals) / 12
        vals = [sum(vals[int(i * size):int((i + 1) * size)]) for i in range(12)]
    if len(vals) < 2 or max(vals) <= 0:
        return f'<svg class="mst-spark" width="{width}" height="{height}"></svg>'
    lo, hi = 0.0, max(vals)
    step = (width - 6) / (len(vals) - 1)

    def y(v: float) -> float:
        return height - 3 - (v - lo) / (hi - lo or 1) * (height - 6)

    pts = " ".join(f"{3 + i * step:.1f},{y(v):.1f}" for i, v in enumerate(vals))
    lx, ly = 3 + (len(vals) - 1) * step, y(vals[-1])
    return (
        f'<svg class="mst-spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="var(--color-text-muted)" stroke-width="1.5" '
        'stroke-linejoin="round" stroke-linecap="round" opacity=".7"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="var(--color-primary)" stroke="var(--color-bg-surface)" stroke-width="1.5"/>'
        "</svg>"
    )


def _delta_html(current: float, previous: Optional[float], comparing: bool) -> str:
    """Signed change vs the previous period, with an arrow so it never relies on color."""
    if not comparing or previous is None:
        return ""
    change = pct_change(current, previous)
    if change is None:
        if current > 0:
            return f'<span class="mst-delta is-new" title="Nothing in the previous period">{_icon("fiber_new")}New</span>'
        return '<span class="mst-delta is-none">—</span>'
    if abs(change) < 0.5:
        return f'<span class="mst-delta is-flat" title="Previous period: {compact(previous)}">{_icon("trending_flat")}0%</span>'
    up = change > 0
    text = f"{abs(change):.0f}%" if abs(change) < 1000 else "999%+"
    return (
        f'<span class="mst-delta {"is-up" if up else "is-down"}" title="Previous period: {compact(previous)}">'
        f'{_icon("trending_up" if up else "trending_down")}{text}</span>'
    )


def _time_ago(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)}d ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %Y")


def _event_sentence(ev: Dict[str, Any]) -> Tuple[str, str]:
    """(main text, detail) for a timeline row."""
    et = EVENT_TYPES.get(ev["event_type"])
    label = et.label if et else ev["event_type"]
    amount = ev.get("amount") or 0
    detail = ev.get("alert_name") or ""
    if et and et.amount_label and amount:
        label = f"{label} · {compact(amount)} {et.amount_label}"
    return label, detail


class StatisticsTab:
    name = "Statistics"

    def __init__(self) -> None:
        self.dirty: bool = False  # nothing to save; statistics persist on their own
        self.ui_elements: Dict[str, Any] = {}

        self.section = "overview"
        self.range_key = "30d"
        self.custom_start: Optional[str] = None
        self.custom_end: Optional[str] = None
        self.metric_key = "chat"
        self.chart_as_line = False
        self.compare_previous = True
        self.heatmap_scope = "chat"
        self.lookup_username = ""
        self.lookup_types: set = set()
        self.export_layout = "wide"
        self.export_last_path: Optional[str] = None

        self.root = None
        self.toolbar = None
        self.nav = None
        self.body = None
        self.live_enabled = True
        self.live_timer = None
        self._tick = 0
        self._session_label = None
        self._save_label = None
        self._live_badge = None
        self._lifetime_labels: Dict[str, Any] = {}
        self._overview_refs: Dict[str, Any] = {}
        self._export_preview = None

    # ----- lifecycle (TabBase) -----

    def on_enter(self) -> None:
        if self.live_enabled:
            self._start_live()

    def on_exit(self) -> None:
        self._stop_live()

    def save(self) -> None:
        pass

    def discard(self) -> None:
        pass

    # ----- helpers -----

    @property
    def mgr(self):
        return get_statistics_manager()

    @property
    def insights(self) -> Insights:
        return Insights(self.mgr)

    def _colors(self) -> ChartColors:
        try:
            from ...theme_manager import get_theme_manager

            return ChartColors.from_theme(get_theme_manager().get_theme())
        except Exception:
            return ChartColors.from_theme(None)

    def _range(self) -> DateRange:
        if self.range_key == "custom" and self.custom_start and self.custom_end:
            try:
                return custom_range(
                    datetime.strptime(self.custom_start[:10], "%Y-%m-%d"),
                    datetime.strptime(self.custom_end[:10], "%Y-%m-%d"),
                )
            except ValueError:
                pass
        first = self.insights.first_event_ts() if self.range_key == "all" else None
        return preset_range(self.range_key if self.range_key != "custom" else "30d", first_event_ts=first)

    @property
    def _comparing(self) -> bool:
        return self.range_key != "all"

    def _range_includes_now(self, rng: DateRange) -> bool:
        return rng.end_ts >= time.time()

    @staticmethod
    def _button(html_content: str, classes: str, on_click: Callable[[], None], title: str = ""):
        el = ui.element("button").classes(classes).props(f'type=button title="{html.escape(title)}"' if title else "type=button")
        with el:
            ui.html(html_content, sanitize=False).classes("contents")
        el.on("click", lambda _e: on_click())
        return el

    # ----- building -----

    def build(self, parent_container) -> None:
        global _css_injected
        if not _css_injected:
            from ...help_system.help_render import promote_color_importance, scope_selectors

            ui.add_head_html(f"<style id='mycelian-statistics'>{promote_color_importance(scope_selectors(STATISTICS_CSS, ".mst"))}</style>", shared=True)
            _css_injected = True
        try:
            self.mgr.ensure_periodic_saving()
        except Exception as e:
            logger.debug("Could not ensure periodic saving: %s", e)

        with parent_container:
            self.root = ui.element("div").classes("mst")
            with self.root:
                self.toolbar = ui.element("div").classes("mst-toolbar")
                self.nav = ui.element("div").classes("mst-nav")
                self.body = ui.element("div").classes("mst").style("gap:12px")
        self._render_toolbar()
        self._render_nav()
        self._render_section()
        self._start_live()

    def _render_toolbar(self) -> None:
        self.toolbar.clear()
        rng = self._range()
        with self.toolbar:
            with ui.element("div").classes("mst-seg"):
                for key, label in (*PRESETS, ("custom", "Custom")):
                    icon = _icon("date_range") if key == "custom" else ""
                    self._button(
                        icon + html.escape(label),
                        "mst-seg-btn" + (" is-active" if key == self.range_key else ""),
                        lambda k=key: self._set_range(k),
                    )
            if self.range_key == "custom":
                start = form_input(tooltip="First day of the range", label="From", value=self.custom_start or rng.start.strftime("%Y-%m-%d"), classes="mst-date-input")
                start.props("type=date dense")
                end = form_input(tooltip="Last day of the range", label="To", value=self.custom_end or rng.end.strftime("%Y-%m-%d"), classes="mst-date-input")
                end.props("type=date dense")

                def apply_custom():
                    self.custom_start = str(start.value or "")[:10]
                    self.custom_end = str(end.value or "")[:10]
                    self._render_toolbar()
                    self._render_section()

                primary_button("Apply", apply_custom)
            caption = rng.label
            if self.range_key != "custom" and self.range_key != "today":
                caption = f"{rng.start:%b %d} – {rng.end:%b %d, %Y}"
            if self._comparing:
                caption += f" · compared with the previous {rng.days} day{'s' if rng.days != 1 else ''}"
            ui.label(caption).classes("mst-range-caption")
            ui.element("div").classes("mst-spacer")

            self._live_badge = ui.element("div").classes("mst-live" + (" is-on" if self.live_enabled else ""))
            with self._live_badge:
                ui.element("span").classes("mst-live-dot")
                ui.label("Live" if self.live_enabled else "Paused").classes("font-semibold")
                ui.label("·")
                self._session_label = ui.label(self._session_text()).tooltip("This session's running time")
                ui.label("·")
                self._save_label = ui.label(self._save_text()).tooltip("Lifetime totals are saved automatically")
            ui.switch(value=self.live_enabled, on_change=lambda e: self._set_live(bool(e.value))).props("dense").tooltip(
                "Update numbers automatically"
            )
            ui.button(icon="refresh", on_click=self._refresh).props("flat round dense").tooltip("Refresh now")
            ui.button(icon="save", on_click=self._save_now).props("flat round dense").tooltip("Save lifetime totals now")

    def _render_nav(self) -> None:
        self.nav.clear()
        with self.nav:
            for key, label, icon in SECTIONS:
                self._button(
                    _icon(icon) + html.escape(label),
                    "mst-nav-btn" + (" is-active" if key == self.section else ""),
                    lambda k=key: self._set_section(k),
                )

    def _render_section(self) -> None:
        self.body.clear()
        self._lifetime_labels = {}
        self._overview_refs = {}
        self._export_preview = None
        try:
            with self.body:
                {
                    "overview": self._build_overview,
                    "leaders": self._build_leaders,
                    "lifetime": self._build_lifetime,
                    "lookup": self._build_lookup,
                    "export": self._build_export,
                }[self.section]()
        except Exception as e:
            logger.error("Error building statistics section %s: %s", self.section, e, exc_info=True)
            with self.body:
                with ui.element("div").classes("mst-card mst-empty"):
                    ui.html(_icon("error_outline") + "Statistics could not be loaded. Check the log for details.", sanitize=False)

    # ----- state changes -----

    def _set_range(self, key: str) -> None:
        if key == "custom" and not self.custom_start:
            rng = self._range()
            self.custom_start, self.custom_end = rng.start.strftime("%Y-%m-%d"), rng.end.strftime("%Y-%m-%d")
        self.range_key = key
        self._render_toolbar()
        if key != "custom":
            self._render_section()

    def _set_section(self, key: str) -> None:
        self.section = key
        self._render_nav()
        self._render_section()

    def _open_viewer(self, username: str) -> None:
        if not username or username == "?":
            return
        self.lookup_username = username
        self.lookup_types = set()
        self._set_section("lookup")

    def _refresh(self) -> None:
        self._render_toolbar()
        self._render_section()

    def _save_now(self) -> None:
        try:
            self.mgr.force_save()
            notify("Statistics saved", type="positive")
            if self._save_label:
                self._save_label.set_text(self._save_text())
        except Exception as e:
            logger.error("Error force saving statistics: %s", e, exc_info=True)
            notify("Could not save statistics", type="negative")

    # ----- live updates -----

    def _session_text(self) -> str:
        try:
            start = self.mgr.data.session.start_time
            secs = int(time.time() - start)
            return f"session {secs // 3600:d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
        except Exception:
            return "session —"

    def _save_text(self) -> str:
        try:
            ts = self.mgr.data.session.last_save_time
            return f"saved {datetime.fromtimestamp(ts):%H:%M:%S}" if ts else "not saved yet"
        except Exception:
            return "saved —"

    def _set_live(self, enabled: bool) -> None:
        self.live_enabled = enabled
        if enabled:
            self._start_live()
        else:
            self._stop_live()
        self._render_toolbar()

    def _start_live(self) -> None:
        if not self.live_enabled or self.root is None:
            return
        if self.live_timer is not None and getattr(self.live_timer, "active", False):
            return
        self.live_timer = layout_schedule(LIVE_INTERVAL_SEC, self._live_tick, active=True)

    def _stop_live(self) -> None:
        if self.live_timer is not None:
            try:
                self.live_timer.active = False
            except Exception:
                pass
        self.live_timer = None

    def _live_tick(self) -> None:
        if self.root is None or getattr(self.root, "is_deleted", False):
            self._stop_live()
            return
        self._tick += 1
        try:
            if self._session_label:
                self._session_label.set_text(self._session_text())
            if self._save_label:
                self._save_label.set_text(self._save_text())
            if self.section == "lifetime":
                self._update_lifetime_labels()
            elif self.section == "overview" and self._tick % LIVE_REQUERY_EVERY == 0:
                rng = self._range()
                if self._range_includes_now(rng):
                    self._update_overview(rng)
        except Exception as e:
            logger.debug("statistics live tick failed: %s", e)

    # ======================================================================
    # Overview
    # ======================================================================

    def _overview_data(self, rng: DateRange) -> Dict[str, Any]:
        ins = self.insights
        summary = ins.summary(rng)
        prev = ins.summary(rng.previous()) if self._comparing else None
        series = ins.timeseries(rng)
        prev_series = ins.timeseries(rng.previous(), bucket=series["bucket"]) if self._comparing else None
        return {"summary": summary, "prev": prev, "series": series, "prev_series": prev_series}

    def _build_overview(self) -> None:
        rng = self._range()
        data = self._overview_data(rng)
        summary, series = data["summary"], data["series"]

        if summary["total_events"] == 0:
            self._empty_range_notice(rng)

        # KPI tiles: click one to chart it.
        tiles = {}
        with ui.element("div").classes("mst-kpis"):
            for metric in METRICS:
                tiles[metric.key] = self._metric_tile(metric, data)
        self._overview_refs["tiles"] = tiles

        with ui.element("div").classes("mst-grid mst-grid-main"):
            self._build_trend_card(rng, data)
            self._build_spotlight_card(rng)

        with ui.element("div").classes("mst-grid mst-grid-2"):
            self._build_mix_card(summary)
            self._build_heatmap_card(rng)
        self._overview_refs["series_bucket"] = series["bucket"]

    def _empty_range_notice(self, rng: DateRange) -> None:
        span = self.mgr.get_event_log_summary()
        with ui.element("div").classes("mst-card"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.icon("info").classes("text-theme-info")
                with ui.column().classes("gap-0"):
                    ui.label("No activity recorded in this range").classes("font-semibold")
                    if span.get("total_rows"):
                        d0 = datetime.fromtimestamp(span["min_timestamp"]).strftime("%b %d, %Y")
                        d1 = datetime.fromtimestamp(span["max_timestamp"]).strftime("%b %d, %Y")
                        ui.label(f"The event log covers {d0} to {d1} ({span['total_rows']:,} events). Try All time.").classes("mst-muted")
                    else:
                        ui.label("Mycelian records events while it runs: chat, alerts, commands, giveaways and more.").classes("mst-muted")
            if span.get("total_rows") and self.range_key != "all":
                ui.button("Show all time", on_click=lambda: self._set_range("all")).props("flat no-caps dense color=primary").classes("mt-2")

    def _metric_tile(self, metric: Metric, data: Dict[str, Any]) -> Dict[str, Any]:
        value = Insights.metric_value(data["summary"], metric)
        prev = Insights.metric_value(data["prev"], metric) if data["prev"] else None
        spark = Insights.metric_series(data["series"], metric)
        tile = ui.element("button").classes("mst-tile" + (" is-active" if metric.key == self.metric_key else "")).props(
            f'type=button title="Show {html.escape(metric.label.lower())} over time"'
        )
        with tile:
            ui.html(f'<div class="mst-tile-label">{_icon(metric.icon)}{html.escape(metric.label)}</div>', sanitize=False)
            value_label = ui.label(compact(value)).classes("mst-tile-value")
            foot = ui.html(self._tile_foot(value, prev, spark), sanitize=False).classes("mst-tile-foot")
        tile.on("click", lambda _e, k=metric.key: self._select_metric(k))
        return {"el": tile, "value": value_label, "foot": foot}

    def _tile_foot(self, value: float, prev: Optional[float], spark: Sequence[float]) -> str:
        delta = _delta_html(value, prev, self._comparing) or '<span class="mst-tile-note">in range</span>'
        return delta + _sparkline(spark)

    def _select_metric(self, key: str) -> None:
        if key == self.metric_key:
            return
        self.metric_key = key
        for k, ref in (self._overview_refs.get("tiles") or {}).items():
            ref["el"].classes(add="is-active") if k == key else ref["el"].classes(remove="is-active")
        self._redraw_trend()

    def _build_trend_card(self, rng: DateRange, data: Dict[str, Any]) -> None:
        with ui.element("div").classes("mst-card is-fill"):
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0 grow"):
                    self._overview_refs["trend_title"] = ui.label("").classes("mst-card-title")
                    self._overview_refs["trend_sub"] = ui.label("").classes("mst-card-sub")
                with ui.row().classes("items-center gap-2"):
                    if self._comparing:
                        ui.checkbox("Previous period average", value=self.compare_previous, on_change=lambda e: self._toggle_compare(bool(e.value))).props(
                            "dense"
                        ).classes("text-xs").tooltip("Average of the equally long period before this one")
                    with ui.element("div").classes("mst-seg"):
                        for as_line, icon, tip in ((False, "bar_chart", "Columns"), (True, "show_chart", "Line")):
                            self._button(
                                _icon(icon),
                                "mst-seg-btn" + (" is-active" if self.chart_as_line == as_line else ""),
                                lambda v=as_line: self._toggle_line(v),
                                title=tip,
                            )
            self._overview_refs["trend_chart"] = ui.echart({}).classes("mst-chart")
        self._overview_refs["data"] = data
        self._redraw_trend()

    def _toggle_compare(self, value: bool) -> None:
        self.compare_previous = value
        self._redraw_trend()

    def _toggle_line(self, value: bool) -> None:
        if self.chart_as_line == value:
            return
        self.chart_as_line = value
        self._render_section()

    def _redraw_trend(self) -> None:
        refs = self._overview_refs
        data = refs.get("data")
        chart = refs.get("trend_chart")
        if not data or chart is None:
            return
        metric = METRICS_BY_KEY.get(self.metric_key, METRICS[0])
        series = data["series"]
        values = Insights.metric_series(series, metric)
        prev = None
        if self._comparing and self.compare_previous and data.get("prev_series"):
            prev = Insights.metric_series(data["prev_series"], metric)[: len(values)]
        chart.options.clear()
        chart.options.update(
            time_columns(series["labels"], values, self._colors(), series_name=metric.label, previous=prev, as_line=self.chart_as_line)
        )
        chart.update()
        per = {"hour": "per hour", "day": "per day", "week": "per week"}[series["bucket"]]
        total = sum(values)
        sub = f"{per} · total {compact(total)}"
        if total and not metric.distinct_users:
            i = max(range(len(values)), key=lambda j: values[j])
            when = {"hour": " at ", "day": " on ", "week": ", "}[series["bucket"]]
            sub += f" · peak {compact(values[i])}{when}{series['labels'][i].replace('Week of', 'week of')}"
        elif metric.distinct_users:
            sub = f"{per} · distinct viewers in each {per.split()[-1]}"
        refs["trend_title"].set_text(f"{metric.label} over time")
        refs["trend_sub"].set_text(sub)

    def _update_overview(self, rng: DateRange) -> None:
        data = self._overview_data(rng)
        self._overview_refs["data"] = data
        for metric in METRICS:
            ref = (self._overview_refs.get("tiles") or {}).get(metric.key)
            if not ref:
                continue
            value = Insights.metric_value(data["summary"], metric)
            prev = Insights.metric_value(data["prev"], metric) if data["prev"] else None
            ref["value"].set_text(compact(value))
            ref["foot"].set_content(self._tile_foot(value, prev, Insights.metric_series(data["series"], metric)))
        self._redraw_trend()

    def _build_spotlight_card(self, rng: DateRange) -> None:
        ins = self.insights
        picks = (
            ("Most active viewer", "bolt", ins.leaders(rng, tuple(k for k in EVENT_TYPES if k != "giveaway_draw_complete"), "count", 1), "events"),
            ("Top chatter", "chat", ins.leaders(rng, ("chat_message",), "count", 1), "messages"),
            ("Biggest supporter (bits)", "diamond", ins.leaders(rng, ("bit",), "sum", 1), "bits"),
            ("Top gifter", "redeem", ins.leaders(rng, ("giftsub",), "sum", 1), "subs gifted"),
            ("Longest watch streak", "local_fire_department", ins.leaders(rng, ("watch_streak",), "max", 1), "streams"),
        )
        with ui.element("div").classes("mst-card"):
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0"):
                    ui.label("Spotlight").classes("mst-card-title")
                    ui.label("Click a name to see that viewer's activity").classes("mst-card-sub")
            with ui.element("div").classes("mst-grid").style("gap:8px"):
                shown = 0
                for kicker, icon, rows, unit in picks:
                    if not rows:
                        continue
                    shown += 1
                    row = rows[0]
                    spot = ui.element("div").classes("mst-spot")
                    with spot:
                        ui.html(f'<div class="mst-avatar">{_initial(row["username"])}</div>', sanitize=False)
                        with ui.column().classes("gap-0 min-w-0 grow"):
                            ui.html(f'<div class="mst-spot-kicker">{_icon(icon, "text-sm align-middle")} {html.escape(kicker)}</div>', sanitize=False)
                            ui.label(row["username"]).classes("mst-spot-name")
                            ui.label(f"{compact(row['value'])} {unit}").classes("mst-spot-value")
                    spot.on("click", lambda _e, u=row["username"]: self._open_viewer(u))
                if not shown:
                    ui.html(f'<div class="mst-empty">{_icon("emoji_events")}No viewer activity in this range yet.</div>', sanitize=False)

    def _build_mix_card(self, summary: Dict[str, Any]) -> None:
        metric_for_type = {}
        for m in METRICS:
            for et, _agg in m.parts:
                metric_for_type.setdefault(et, m.key)
        rows = sorted(
            ((k, v["count"]) for k, v in summary["types"].items() if v["count"] and k in EVENT_TYPES),
            key=lambda kv: kv[1],
            reverse=True,
        )
        peak = rows[0][1] if rows else 0
        with ui.element("div").classes("mst-card"):
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0"):
                    ui.label("What happened").classes("mst-card-title")
                    ui.label(f"{summary['total_events']:,} events from {summary['unique_users']:,} viewers").classes("mst-card-sub")
            if not rows:
                ui.html(f'<div class="mst-empty">{_icon("bar_chart")}Nothing recorded in this range.</div>', sanitize=False)
                return
            with ui.element("div").classes("mst-bars"):
                for et, count in rows:
                    meta = EVENT_TYPES[et]
                    metric_key = metric_for_type.get(et)
                    row = ui.element("div").classes("mst-bar-row" + (" is-link" if metric_key else "")).props(
                        f'title="{html.escape(meta.label)}: {count:,}' + (" · click to chart" if metric_key else "") + '"'
                    )
                    with row:
                        ui.html(f'<div class="mst-bar-label">{_icon(meta.icon)}{html.escape(meta.label)}</div>', sanitize=False)
                        ui.html(
                            f'<div class="mst-bar-track"><div class="mst-bar-fill" style="width:{max(count / peak * 100, 1):.1f}%"></div></div>',
                            sanitize=False,
                        )
                        ui.label(compact(count)).classes("mst-bar-value")
                    if metric_key:
                        row.on("click", lambda _e, k=metric_key: self._select_metric(k))

    def _build_heatmap_card(self, rng: DateRange) -> None:
        scopes = {"chat": ("Chat messages", ("chat_message",), "messages"), "all": ("All events", None, "events")}
        title, types, unit = scopes.get(self.heatmap_scope, scopes["chat"])
        grid = self.insights.heatmap(rng, types)
        total = sum(sum(r) for r in grid)
        with ui.element("div").classes("mst-card"):
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0 grow"):
                    ui.label("When your community is active").classes("mst-card-title")
                    sub = "Local time, by weekday and hour"
                    if total:
                        d, h = max(((d, h) for d in range(7) for h in range(24)), key=lambda dh: grid[dh[0]][dh[1]])
                        sub += f" · busiest {['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'][d]} at {h:02d}:00"
                    ui.label(sub).classes("mst-card-sub")
                with ui.element("div").classes("mst-seg"):
                    for key, (label, _t, _u) in scopes.items():
                        self._button(html.escape(label), "mst-seg-btn" + (" is-active" if key == self.heatmap_scope else ""), lambda k=key: self._set_heatmap(k))
            if not total:
                ui.html(f'<div class="mst-empty">{_icon("calendar_view_week")}No {unit} in this range.</div>', sanitize=False)
                return
            ui.echart(weekly_heatmap(grid, self._colors(), value_name=unit)).classes("mst-chart").style("height: 250px")

    def _set_heatmap(self, scope: str) -> None:
        self.heatmap_scope = scope
        self._render_section()

    # ======================================================================
    # Leaderboards
    # ======================================================================

    def _build_leaders(self) -> None:
        rng = self._range()
        ins = self.insights
        ui.label(f"Leaders for {rng.label.lower() if self.range_key != 'custom' else rng.label}. Click a viewer for details.").classes("mst-muted")
        with ui.element("div").classes("mst-grid mst-grid-3"):
            for title, icon, types, agg, unit in LEADERBOARDS:
                rows = ins.leaders(rng, types, agg, 10)
                with ui.element("div").classes("mst-card"):
                    with ui.element("div").classes("mst-card-head"):
                        ui.html(f'<div class="mst-tile-label" style="font-size:.95rem;color:var(--color-text-primary);font-weight:600">{_icon(icon)}{html.escape(title)}</div>', sanitize=False)
                    self._ranked_list(rows, unit)

    def _ranked_list(self, rows: List[Dict[str, Any]], unit: str, clickable: bool = True) -> None:
        if not rows:
            ui.html(f'<div class="mst-empty">{_icon("hourglass_empty")}Nobody yet in this range.</div>', sanitize=False)
            return
        peak = max(r["value"] for r in rows) or 1
        with ui.element("div").classes("mst-rank"):
            for i, r in enumerate(rows, start=1):
                row = ui.element("div").classes("mst-rank-row" + ("" if clickable else " is-static")).props(
                    f'title="{html.escape(r["username"])}: {r["value"]:,.0f} {html.escape(unit)}"'
                )
                with row:
                    ui.label(str(i)).classes("mst-rank-pos")
                    ui.html(
                        f'<div class="min-w-0"><div class="mst-rank-name">{html.escape(r["username"])}</div>'
                        f'<div class="mst-rank-bar" style="width:{max(r["value"] / peak * 100, 2):.1f}%"></div></div>',
                        sanitize=False,
                    )
                    ui.label(compact(r["value"])).classes("mst-rank-value")
                if clickable:
                    row.on("click", lambda _e, u=r["username"]: self._open_viewer(u))

    # ======================================================================
    # Lifetime (aggregates; live)
    # ======================================================================

    def _lifetime_values(self, stats: Dict[str, Any]) -> Dict[str, str]:
        a = stats.get("alerts", {}) or {}
        cb = stats.get("chatbot", {}) or {}
        cn = stats.get("connectors", {}) or {}
        q = stats.get("quotes", {}) or {}
        gw = stats.get("giveaways", {}) or {}
        pl = stats.get("polls", {}) or {}
        ch = stats.get("chat", {}) or {}
        mv = pl.get("most_votes") or {}
        mw = pl.get("most_wins") or {}

        def n(v) -> str:
            return f"{int(v or 0):,}"

        return {
            "alerts.bits": n(a.get("total_bits")),
            "alerts.bit_alerts": n(a.get("bit_alerts_played")),
            "alerts.new_subs": n(a.get("new_subs_played")),
            "alerts.resubs": n(a.get("resubs_played")),
            "alerts.gift_alerts": n(a.get("gift_subs_played")),
            "alerts.gift_total": n(a.get("total_gift_subs")),
            "alerts.follows": n(a.get("follow_alerts_played")),
            "alerts.raids": n(a.get("raids")),
            "alerts.point_alerts": n(a.get("point_alerts_redeemed")),
            "alerts.points": n(a.get("total_channel_points_redeemed")),
            "alerts.streak_alerts": n(a.get("watch_streak_alerts_played")),
            "alerts.streak_peak": n(a.get("highest_watch_streak_seen")),
            "chat.twitch": n(ch.get("twitch_messages_received")),
            "chatbot.commands": n(cb.get("commands_created")),
            "chatbot.commands_used": n(cb.get("commands_triggered")),
            "chatbot.events": n(cb.get("events_created")),
            "chatbot.events_used": n(cb.get("events_triggered")),
            "connectors.created": n(cn.get("connectors_created")),
            "connectors.unique": n(cn.get("connectors_triggered")),
            "connectors.total": n(cn.get("total_triggers")),
            "quotes.created": n(q.get("quotes_created")),
            "quotes.redeemed": n(q.get("total_quotes_redeemed")),
            "giveaways.completed": n(gw.get("giveaways_completed")),
            "giveaways.entries": n(gw.get("total_entry_events")),
            "giveaways.avg": f"{float(gw.get('average_entries_per_giveaway') or 0):.1f}",
            "polls.ran": n(pl.get("polls_completed")),
            "polls.votes": n(pl.get("total_votes")),
            "polls.avg": f"{float(pl.get('average_votes_per_poll') or 0):.1f}",
            "polls.most_votes": f"{mv['name']} ({n(mv.get('count'))})" if mv.get("name") else "—",
            "polls.most_wins": f"{mw['name']} ({n(mw.get('count'))})" if mw.get("name") else "—",
        }

    def _kv_grid(self, values: Dict[str, str], items: Sequence[Tuple[str, str]]) -> None:
        with ui.element("div").classes("mst-kv"):
            for key, label in items:
                with ui.element("div").classes("mst-kv-item"):
                    ui.label(label).classes("mst-kv-label")
                    self._lifetime_labels[key] = ui.label(values.get(key, "0")).classes("mst-kv-value")

    def _lifetime_card(self, title: str, icon: str, subtitle: str = ""):
        card = ui.element("div").classes("mst-card")
        with card:
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0"):
                    ui.html(f'<div class="mst-tile-label" style="font-size:.95rem;color:var(--color-text-primary);font-weight:600">{_icon(icon)}{html.escape(title)}</div>', sanitize=False)
                    if subtitle:
                        ui.label(subtitle).classes("mst-card-sub")
        return card

    def _build_lifetime(self) -> None:
        stats = self.mgr.get_all_statistics()
        values = self._lifetime_values(stats)
        ui.label("All-time totals since you started using Mycelian. These update live and do not follow the date range.").classes("mst-muted")

        with self._lifetime_card("Alerts", "notifications_active", "Alerts played and what they brought in"):
            self._kv_grid(values, (
                ("alerts.bits", "Bits cheered"), ("alerts.bit_alerts", "Bit alerts"),
                ("alerts.new_subs", "New subs"), ("alerts.resubs", "Resubs"),
                ("alerts.gift_total", "Subs gifted"), ("alerts.gift_alerts", "Gift sub alerts"),
                ("alerts.follows", "Follows"), ("alerts.raids", "Raids"),
                ("alerts.points", "Channel points spent"), ("alerts.point_alerts", "Point redeems"),
                ("alerts.streak_alerts", "Watch streak alerts"), ("alerts.streak_peak", "Longest watch streak"),
                ("chat.twitch", "Chat messages"),
            ))

        with ui.element("div").classes("mst-grid mst-grid-2"):
            with self._lifetime_card("Hype trains", "train", "Completions by level reached"):
                raw = (stats.get("hype_trains", {}) or {}).get("level_completions", {}) or {}
                levels: Dict[int, int] = {}
                for k, v in raw.items():
                    try:
                        levels[int(k)] = int(v or 0)
                    except (TypeError, ValueError):
                        pass
                top = max([5, *levels.keys()])
                keys = list(range(1, top + 1))
                counts = [levels.get(k, 0) for k in keys]
                if sum(counts):
                    ui.echart(level_columns(keys, counts, self._colors())).classes("mst-chart").style("height: 200px")
                else:
                    ui.html(f'<div class="mst-empty">{_icon("train")}No hype trains completed yet.</div>', sanitize=False)

            with self._lifetime_card("Chatbot", "smart_toy", "Commands and timed or event messages"):
                self._kv_grid(values, (
                    ("chatbot.commands", "Commands"), ("chatbot.commands_used", "Command uses"),
                    ("chatbot.events", "Bot events"), ("chatbot.events_used", "Event runs"),
                ))
                top = [{"username": c["command_name"], "value": c["usage_count"]} for c in self.mgr.get_top_commands(limit=5)]
                if top:
                    ui.label("Most used commands").classes("mst-section-title mt-3 mb-1")
                    self._ranked_list(top, "uses", clickable=False)

            with self._lifetime_card("Connectors", "cable", "Automations you've built and how often they ran"):
                self._kv_grid(values, (("connectors.created", "Connectors"), ("connectors.unique", "Distinct ran"), ("connectors.total", "Total runs")))
                top = [{"username": c["connector_name"], "value": c["trigger_count"]} for c in self.mgr.get_top_connectors(limit=5)]
                if top:
                    ui.label("Most active connectors").classes("mst-section-title mt-3 mb-1")
                    self._ranked_list(top, "runs", clickable=False)

            with self._lifetime_card("Quotes", "format_quote"):
                self._kv_grid(values, (("quotes.created", "Quotes saved"), ("quotes.redeemed", "Times redeemed")))
                usage = (stats.get("quotes", {}) or {}).get("individual_quote_usage", {}) or {}
                top = sorted(({"username": f"Quote #{k}", "value": v} for k, v in usage.items()), key=lambda r: r["value"], reverse=True)[:5]
                if top:
                    ui.label("Most requested").classes("mst-section-title mt-3 mb-1")
                    self._ranked_list(top, "requests", clickable=False)

            with self._lifetime_card("Giveaways", "emoji_events"):
                self._kv_grid(values, (("giveaways.completed", "Draws"), ("giveaways.entries", "Entries"), ("giveaways.avg", "Entries per draw")))
                wins = (stats.get("giveaways", {}) or {}).get("user_wins", {}) or {}
                top = sorted(({"username": k, "value": v} for k, v in wins.items()), key=lambda r: r["value"], reverse=True)[:5]
                if top:
                    ui.label("Luckiest viewers").classes("mst-section-title mt-3 mb-1")
                    self._ranked_list(top, "wins")

            with self._lifetime_card("Polls", "ballot"):
                self._kv_grid(values, (
                    ("polls.ran", "Polls run"), ("polls.votes", "Votes cast"), ("polls.avg", "Votes per poll"),
                    ("polls.most_votes", "Most voted option"), ("polls.most_wins", "Most winning option"),
                ))

        templates = stats.get("templates") or {}
        with_stats = {name: t.get("custom_stats") or {} for name, t in templates.items() if (t or {}).get("custom_stats")}
        if with_stats:
            with self._lifetime_card("Overlay statistics", "dashboard_customize", "Counters reported by your overlays"):
                for name, custom in with_stats.items():
                    ui.label(name).classes("mst-section-title mt-2 mb-1")
                    with ui.element("div").classes("mst-kv"):
                        for stat, val in custom.items():
                            with ui.element("div").classes("mst-kv-item"):
                                ui.label(stat.replace("_", " ").capitalize()).classes("mst-kv-label")
                                self._lifetime_labels[f"tpl.{name}.{stat}"] = ui.label(
                                    f"{val:,}" if isinstance(val, (int, float)) else str(val)
                                ).classes("mst-kv-value")

    def _update_lifetime_labels(self) -> None:
        if not self._lifetime_labels:
            return
        stats = self.mgr.get_all_statistics()
        values = self._lifetime_values(stats)
        for name, t in (stats.get("templates") or {}).items():
            for stat, val in ((t or {}).get("custom_stats") or {}).items():
                values[f"tpl.{name}.{stat}"] = f"{val:,}" if isinstance(val, (int, float)) else str(val)
        for key, label in self._lifetime_labels.items():
            text = values.get(key)
            if text is not None and label.text != text:
                label.set_text(text)

    # ======================================================================
    # Viewer lookup
    # ======================================================================

    def _build_lookup(self) -> None:
        known = []
        try:
            known = self.mgr.get_all_tracked_usernames()
        except Exception:
            pass
        with ui.element("div").classes("mst-card"):
            with ui.row().classes("w-full items-end gap-3 no-wrap"):
                search = form_input(
                    tooltip="Twitch username to look up in saved statistics",
                    label="Viewer",
                    placeholder="Type a username…",
                    value=self.lookup_username,
                    classes="grow",
                    autocomplete=known,
                )
                search.props("clearable")

                def go():
                    self.lookup_username = str(search.value or "").strip()
                    self.lookup_types = set()
                    self._render_lookup_results(results)

                search.on("keydown.enter", lambda _e: go())
                primary_button("Look up", go, icon="person_search")
            ui.label(f"{len(known):,} viewers have recorded activity. Date range: {self._range().label}.").classes("mst-muted mt-1")
        results = ui.element("div").classes("mst").style("gap:12px")
        self._render_lookup_results(results)

    def _render_lookup_results(self, container) -> None:
        container.clear()
        username = self.lookup_username
        with container:
            if not username:
                ui.html(f'<div class="mst-card mst-empty">{_icon("person_search")}Search for a viewer, or click a name on Overview or Leaderboards.</div>', sanitize=False)
                return
            rng = self._range()
            ins = self.insights
            first, last, lifetime_events = ins.user_span(username)
            summary = ins.summary(rng, username)
            stats = self.mgr.get_user_statistics(username) or {}
            display = stats.get("username") or username

            if not lifetime_events and not any(stats.get(k) for k in ("alerts", "chat", "chatbot", "connectors", "quotes", "giveaways")):
                ui.html(f'<div class="mst-card mst-empty">{_icon("person_off")}No statistics recorded for “{html.escape(username)}”.</div>', sanitize=False)
                return

            with ui.element("div").classes("mst-card"):
                with ui.element("div").classes("mst-profile"):
                    ui.html(f'<div class="mst-avatar is-lg">{_initial(display)}</div>', sanitize=False)
                    with ui.column().classes("gap-0 grow"):
                        ui.label(display).classes("mst-profile-name")
                        meta = []
                        if first:
                            meta.append(f'{_icon("history")}First seen {datetime.fromtimestamp(first):%b %d, %Y}')
                        if last:
                            meta.append(f'{_icon("schedule")}Last seen {_time_ago(last)}')
                        meta.append(f'{_icon("bolt")}{lifetime_events:,} events all time')
                        ui.html('<div class="mst-profile-meta">' + "".join(f"<span>{m}</span>" for m in meta) + "</div>", sanitize=False)
                    with ui.column().classes("items-end gap-0"):
                        ui.label(compact(summary["total_events"])).classes("mst-hero-value")
                        ui.label(f"events · {rng.label if self.range_key == 'custom' else rng.label.lower()}").classes("mst-muted")

            # Per-viewer tiles (only what this viewer did).
            prev = ins.summary(rng.previous(), username) if self._comparing else None
            tiles = [m for m in METRICS if not m.distinct_users and Insights.metric_value(summary, m)]
            if tiles:
                with ui.element("div").classes("mst-kpis is-auto"):
                    for m in tiles:
                        v = Insights.metric_value(summary, m)
                        pv = Insights.metric_value(prev, m) if prev else None
                        with ui.element("div").classes("mst-tile is-static"):
                            ui.html(f'<div class="mst-tile-label">{_icon(m.icon)}{html.escape(m.label)}</div>', sanitize=False)
                            ui.label(compact(v)).classes("mst-tile-value")
                            ui.html(_delta_html(v, pv, self._comparing) or '<span class="mst-tile-note">in range</span>', sanitize=False).classes("mst-tile-foot")

            with ui.element("div").classes("mst-grid mst-grid-main"):
                with ui.element("div").classes("mst-card"):
                    ui.label("Activity over time").classes("mst-card-title")
                    series = ins.timeseries(rng, username=username)
                    totals = [0.0] * len(series["keys"])
                    for row in series["types"].values():
                        for i, v in enumerate(row["count"]):
                            totals[i] += v
                    per = {"hour": "per hour", "day": "per day", "week": "per week"}[series["bucket"]]
                    ui.label(f"All events {per}").classes("mst-card-sub mb-2")
                    if sum(totals):
                        ui.echart(time_columns(series["labels"], totals, self._colors(), series_name="Events")).classes("mst-chart").style("height: 220px")
                    else:
                        ui.html(f'<div class="mst-empty">{_icon("bar_chart")}No activity in this range.</div>', sanitize=False)
                with ui.element("div").classes("mst-card"):
                    ui.label("Lifetime totals").classes("mst-card-title")
                    ui.label("Saved aggregates for this viewer, all time").classes("mst-card-sub mb-2")
                    self._viewer_lifetime(stats)

            self._viewer_timeline(rng, username, summary, container)

    def _viewer_lifetime(self, stats: Dict[str, Any]) -> None:
        a = stats.get("alerts") or {}
        items = [
            ("Chat messages", (stats.get("chat") or {}).get("twitch_messages_received")),
            ("Bit alerts", a.get("bit_alerts_played")),
            ("New subs", a.get("new_subs_played")),
            ("Resubs", a.get("resubs_played")),
            ("Gift sub alerts", a.get("gift_subs_played")),
            ("Donations", a.get("donations")),
            ("Follows", a.get("follow_alerts_played")),
            ("Raids", a.get("raids")),
            ("Point redeems", a.get("point_alerts_redeemed")),
            ("Longest watch streak", a.get("highest_watch_streak")),
            ("Bot commands", (stats.get("chatbot") or {}).get("commands_triggered")),
            ("Bot events", (stats.get("chatbot") or {}).get("events_triggered")),
            ("Connector runs", (stats.get("connectors") or {}).get("total_triggers")),
            ("Quotes requested", (stats.get("quotes") or {}).get("total_quotes_redeemed")),
            ("Giveaway wins", (stats.get("giveaways") or {}).get("giveaway_wins")),
        ]
        items = [(label, v) for label, v in items if v]
        if not items:
            ui.html(f'<div class="mst-empty">{_icon("inventory_2")}No lifetime totals saved.</div>', sanitize=False)
            return
        with ui.element("div").classes("mst-kv"):
            for label, v in items:
                with ui.element("div").classes("mst-kv-item"):
                    ui.label(label).classes("mst-kv-label")
                    ui.label(f"{int(v):,}").classes("mst-kv-value")

    def _viewer_timeline(self, rng: DateRange, username: str, summary: Dict[str, Any], container) -> None:
        counts = {k: v["count"] for k, v in summary["types"].items() if v["count"]}
        with ui.element("div").classes("mst-card"):
            with ui.element("div").classes("mst-card-head"):
                with ui.column().classes("gap-0 grow"):
                    ui.label("Recent activity").classes("mst-card-title")
                    ui.label("Newest first, up to 200 events. Filter by type:").classes("mst-card-sub")
            if not counts:
                ui.html(f'<div class="mst-empty">{_icon("history")}No events in this range.</div>', sanitize=False)
                return
            with ui.element("div").classes("mst-chips mb-2"):
                for et, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
                    meta = EVENT_TYPES.get(et)
                    label = meta.label if meta else et
                    chip = self._button(
                        _icon(meta.icon if meta else "label") + html.escape(label) + f' <span class="mst-chip-count">{count:,}</span>',
                        "mst-chip" + (" is-active" if et in self.lookup_types else ""),
                        lambda t=et: self._toggle_lookup_type(t, container),
                    )
                    chip.props("aria-pressed=" + ("true" if et in self.lookup_types else "false"))
            events = self.insights.recent_events(rng, username, sorted(self.lookup_types) or None, 200)
            with ui.element("div").classes("mst-timeline"):
                for ev in events:
                    meta = EVENT_TYPES.get(ev["event_type"])
                    main, detail = _event_sentence(ev)
                    ui.html(
                        '<div class="mst-tl-row">'
                        f'<div class="mst-tl-icon">{_icon(meta.icon if meta else "label")}</div>'
                        f'<div class="mst-tl-main">{html.escape(main)}'
                        + (f' <span class="mst-tl-detail">· {html.escape(detail)}</span>' if detail else "")
                        + "</div>"
                        f'<div class="mst-tl-time" title="{datetime.fromtimestamp(ev["timestamp"]):%b %d, %Y %H:%M:%S}">{_time_ago(ev["timestamp"])}</div>'
                        "</div>",
                        sanitize=False,
                    )

    def _toggle_lookup_type(self, event_type: str, container) -> None:
        if event_type in self.lookup_types:
            self.lookup_types.discard(event_type)
        else:
            self.lookup_types.add(event_type)
        self._render_lookup_results(container)

    # ======================================================================
    # Export highlights
    # ======================================================================

    def _build_export(self) -> None:
        rng = self._range()
        with ui.element("div").classes("mst-export"):
            with ui.element("div").classes("mst-card"):
                ui.label("Share your stream highlights").classes("mst-card-title")
                ui.label("A PNG summary of the selected date range: headline numbers, daily activity and top viewers. Colors follow your theme.").classes(
                    "mst-card-sub mb-3"
                )
                ui.label("Range").classes("mst-option-label")
                ui.label(rng.label if self.range_key in ("custom", "today", "all") else f"{rng.start:%b %d} – {rng.end:%b %d, %Y}").classes("font-semibold")
                ui.label("Change it in the bar above.").classes("mst-muted mb-3")
                ui.label("Layout").classes("mst-option-label")
                with ui.element("div").classes("mst-seg mb-3"):
                    for key, label, icon in (("wide", "Wide 16:9", "crop_16_9"), ("portrait", "Portrait 4:5", "crop_portrait")):
                        self._button(
                            _icon(icon) + html.escape(label),
                            "mst-seg-btn" + (" is-active" if key == self.export_layout else ""),
                            lambda k=key: self._set_export_layout(k),
                        )
                with ui.row().classes("gap-2"):
                    primary_button("Save PNG", self._export_save, icon="download")
                    ui.button(icon="folder_open", on_click=self._open_exports_folder).props("outline no-caps color=primary").tooltip("Open the exports folder")
                self._export_status = ui.column().classes("gap-1 mt-3 w-full")
                self._render_export_status()
            self._export_preview = ui.element("div").classes("mst-export-preview" + (" is-portrait" if self.export_layout == "portrait" else ""))
        self._render_export_preview()

    def _set_export_layout(self, key: str) -> None:
        self.export_layout = key
        self._render_section()

    def _render_export_status(self) -> None:
        if not getattr(self, "_export_status", None):
            return
        self._export_status.clear()
        if self.export_last_path:
            with self._export_status:
                ui.label("Last saved").classes("mst-option-label")
                ui.label(self.export_last_path).classes("mst-path")

    def _export_inputs(self, rng: DateRange) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
        highlights, source = self.mgr.get_highlights_for_export(rng.start_ts, rng.end_ts)
        ins = self.insights
        series = ins.timeseries(rng)
        totals = [0.0] * len(series["keys"])
        for et, row in series["types"].items():
            if et == "giveaway_draw_complete":
                continue
            for i, v in enumerate(row["count"]):
                totals[i] += v
        extra = {
            "activity_labels": series["labels"],
            "activity_values": totals,
            "activity_bucket": series["bucket"],
        }
        if self._comparing:
            cur, prev = ins.summary(rng), ins.summary(rng.previous())
            extra["deltas"] = {m.key: pct_change(Insights.metric_value(cur, m), Insights.metric_value(prev, m)) for m in METRICS}
        return highlights, source, extra

    def _render_image(self, rng: DateRange):
        from ...statistics_export import render_highlights_image
        from ...theme_manager import get_theme_manager

        highlights, source, extra = self._export_inputs(rng)
        if not highlights.get("total_events"):
            return None, source
        streamer = self._channel_name()
        image = render_highlights_image(
            highlights,
            rng.start,
            rng.end,
            streamer_name=streamer,
            theme=get_theme_manager().get_theme(),
            layout=self.export_layout,
            extra=extra,
            source=source,
        )
        return image, source

    @staticmethod
    def _channel_name() -> str:
        """Connected Twitch display name, else the stored streamer name."""
        try:
            from ... import twitch

            name = str(twitch.get_twitch_connection_status().get("user_name") or "").strip()
            if name and name not in ("N/A", "None", "Unknown"):
                return name
        except Exception:
            pass
        settings = dataobjects.state_manager.get_app_settings()
        return (getattr(settings, "streamer_name", "") or "Mycelian").strip() or "Mycelian"

    def _render_export_preview(self) -> None:
        box = self._export_preview
        if box is None:
            return
        box.clear()
        rng = self._range()
        try:
            image, _source = self._render_image(rng)
        except Exception as e:
            logger.error("Highlights preview failed: %s", e, exc_info=True)
            image = None
        with box:
            if image is None:
                ui.html(f'<div class="mst-empty">{_icon("image_not_supported")}Nothing to export for this range yet.</div>', sanitize=False)
                return
            preview = image.copy()
            preview.thumbnail((1280, 1280))
            buf = io.BytesIO()
            preview.convert("RGB").save(buf, "JPEG", quality=88)
            ui.html(f'<img alt="Highlights preview" src="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}">', sanitize=False).classes("w-full")

    def _export_save(self) -> None:
        from ...path_utils import get_data_path

        rng = self._range()
        try:
            image, source = self._render_image(rng)
            if image is None:
                notify("Nothing to export: no events were recorded in this date range.", type="warning")
                return
            export_dir = get_data_path("exports")
            os.makedirs(export_dir, exist_ok=True)
            suffix = "" if self.export_layout == "wide" else "_portrait"
            path = os.path.join(export_dir, f"highlights_{rng.start:%Y%m%d}_{rng.end:%Y%m%d}{suffix}.png")
            image.convert("RGB").save(path, "PNG")
            self.export_last_path = path
            self._render_export_status()
            note = " Numbers are lifetime totals because the event log has nothing in this range." if source == "lifetime" else ""
            notify(f"Highlights saved.{note}\n{path}", type="positive", close_button=True)
        except Exception as e:
            logger.error("Error exporting highlights: %s", e, exc_info=True)
            notify("Could not export the highlights image", type="negative")

    def _open_exports_folder(self) -> None:
        from pathlib import Path

        from ...path_utils import get_data_path, reveal_in_file_manager

        path = Path(self.export_last_path) if self.export_last_path else Path(get_data_path("exports"))
        try:
            os.makedirs(get_data_path("exports"), exist_ok=True)
            reveal_in_file_manager(path)
        except Exception as e:
            logger.debug("Could not open exports folder: %s", e)
            notify("Could not open the exports folder", type="warning")

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Statistics dashboard, part 1: HTML helpers, tab state and date ranges, the
toolbar, section navigation, live updates and the Overview section (KPI tiles, trend
chart, spotlight, event mix, weekly heatmap).

Uses a real StatisticsManager on a temp statistics.db; the UI runs in NiceGUI's user
simulation (tests/ui_sim.py)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402
from PIL import Image  # noqa: E402
from ui_sim import run_ui  # noqa: E402

import modules  # noqa: E402
import modules.statistics_manager as sm  # noqa: E402
import modules.uiwindows.tabs.statistics_tab as tabmod  # noqa: E402
from modules.statistics_insights import METRICS, METRICS_BY_KEY, DateRange, Insights, preset_range  # noqa: E402


# --------------------------------------------------------------------------- fixtures and helpers


class FakeTimer:
    def __init__(self, interval, callback, active=True):
        self.interval, self.callback, self.active = interval, callback, active


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    root = tmp_path / "data_root"
    root.mkdir()
    monkeypatch.setattr(sm, "get_data_path", lambda rel: os.path.join(str(root), rel))
    monkeypatch.setattr(sm, "get_data", lambda path: None)
    monkeypatch.setattr(sm, "set_data", lambda path, value: True)
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda *a, **k: None)
    m = sm.StatisticsManager()
    m._username_backfill_thread.join(2)
    monkeypatch.setattr(tabmod, "get_statistics_manager", lambda: m)

    import modules.help_system.help_render as hr

    sheets = []
    monkeypatch.setattr(hr, "inject_page_stylesheet", lambda sid, css: sheets.append((sid, css)))
    timers = []

    def schedule(interval, callback, *, once=False, active=True):
        t = FakeTimer(interval, callback, active)
        timers.append(t)
        return t

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)
    notes = []
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append(dict(kw, message=message)))

    from modules.theme_manager import ThemeColors
    import modules.theme_manager as tm

    theme = ThemeColors(**json.loads((ROOT / "themes" / "dark_theme.json").read_text()))
    monkeypatch.setattr(tm, "get_theme_manager", lambda: SimpleNamespace(get_theme=lambda: theme))
    settings = SimpleNamespace(streamer_name="Streamy")
    monkeypatch.setattr(tabmod.dataobjects, "state_manager", SimpleNamespace(get_app_settings=lambda: settings))
    status = {"user_name": "TwitchName"}
    twitch = ModuleType("modules.twitch")
    twitch.get_twitch_connection_status = lambda: status
    monkeypatch.setitem(sys.modules, "modules.twitch", twitch)
    monkeypatch.setattr(modules, "twitch", twitch, raising=False)

    import modules.statistics_export as ex

    renders = []

    def fake_render(highlights, start, end, **kw):
        renders.append(dict(kw, highlights=highlights, start=start, end=end))
        return Image.new("RGB", (64, 36), (10, 20, 30))

    monkeypatch.setattr(ex, "render_highlights_image", fake_render)
    import modules.path_utils as pu

    exports_root = tmp_path / "exports_root"
    monkeypatch.setattr(pu, "get_data_path", lambda rel: os.path.join(str(exports_root), rel))
    reveals = []
    monkeypatch.setattr(pu, "reveal_in_file_manager", lambda p: reveals.append(p))

    ns = SimpleNamespace(mgr=m, sheets=sheets, timers=timers, notes=notes, theme=theme, settings=settings,
                         status=status, renders=renders, reveals=reveals, exports_root=exports_root, twitch=twitch)
    yield ns
    m._close_statistics_db()
    m._event_flush_thread.join(2)


def recent(seconds_ago: float = 60) -> float:
    """A moment earlier today (never before midnight)."""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return max(midnight + 1, time.time() - seconds_ago)


def add(m, rows):
    """rows: (username, event_type, amount, alert_name, timestamp)."""
    conn = sqlite3.connect(m._stats_db_path)
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp, username_lower) "
        "VALUES (?, ?, ?, ?, ?, lower(?))",
        [(*r, r[0]) for r in rows],
    )
    conn.commit()
    conn.close()


def seed(m):
    t = recent()
    add(m, [
        ("Alice", "chat_message", 1, "", t), ("Alice", "chat_message", 1, "", t), ("Alice", "chat_message", 1, "", t),
        ("Bob", "chat_message", 1, "", t), ("Bob", "bit", 500, "Cheer", t), ("Cara", "giftsub", 3, "", t),
        ("Dan", "watch_streak", 12, "", t), ("Eve", "sub", 0, "", t), ("Eve", "follow", 0, "", t),
        ("__system__", "giveaway_draw_complete", 1, "", t),
        ("Alice", "chat_message", 1, "", time.time() - 45 * 86400),  # previous period
    ])


def clickable(el):
    while el is not None:
        if any(listener.type == "click" for listener in el._event_listeners.values()):
            return el
        el = el.parent_slot.parent if el.parent_slot else None
    return None


def click_text(user, text, index=0):
    """Click the nearest clickable ancestor of the index-th element showing ``text``."""
    targets = []
    for el in sorted(user.find(text).elements, key=lambda e: e.id):
        target = clickable(el)
        if target is not None and target not in targets:
            targets.append(target)
    assert targets, f"nothing clickable shows {text!r}"
    UserInteraction(user, {targets[index]}, None).click()


def icon_button(user, icon):
    buttons = [e for e in user.find(kind=ui.button).elements if e.props.get("icon") == icon]
    assert buttons, icon
    return buttons[0]


def run_tab(build_tab, check):
    def build():
        build_tab.build(ui.column())

    run_ui(build, check)


# --------------------------------------------------------------------------- module helpers


def test_icon_and_initial_escape_html():
    assert tabmod._icon("chat") == '<span class="material-icons " aria-hidden="true">chat</span>'
    assert 'class="material-icons big"' in tabmod._icon("x", "big")
    assert "&lt;b&gt;" in tabmod._icon("<b>")
    assert tabmod._initial("  bob") == "B"
    assert tabmod._initial("") == "?" and tabmod._initial(None) == "?" and tabmod._initial("   ") == "?"
    assert tabmod._initial("<x") == "&lt;"


def test_sparkline_empty_cases():
    for values in ([], [5], [0, 0, 0]):
        svg = tabmod._sparkline(values)
        assert svg == '<svg class="mst-spark" width="74" height="24"></svg>'


def test_sparkline_draws_the_trend_and_marks_the_latest_point():
    svg = tabmod._sparkline([0, 5, 10], width=46, height=16)
    assert 'viewBox="0 0 46 16"' in svg
    assert 'points="3.0,13.0 23.0,8.0 43.0,3.0"' in svg
    assert '<circle cx="43.0" cy="3.0"' in svg


def test_sparkline_bins_long_series_into_twelve_points():
    svg = tabmod._sparkline([1] * 30 + [None] * 6)
    points = svg.split('points="')[1].split('"')[0].split()
    assert len(points) == 12


def test_delta_html_variants():
    assert tabmod._delta_html(5, 3, False) == ""
    assert tabmod._delta_html(5, None, True) == ""
    assert "is-new" in tabmod._delta_html(5, 0, True) and "New" in tabmod._delta_html(5, 0, True)
    assert tabmod._delta_html(0, 0, True) == '<span class="mst-delta is-none">—</span>'
    flat = tabmod._delta_html(100.2, 100, True)
    assert "is-flat" in flat and "0%" in flat and 'title="Previous period: 100"' in flat
    up = tabmod._delta_html(150, 100, True)
    assert "is-up" in up and "trending_up" in up and "50%" in up
    down = tabmod._delta_html(25, 100, True)
    assert "is-down" in down and "trending_down" in down and "75%" in down
    assert "999%+" in tabmod._delta_html(5000, 1, True)


def test_time_ago():
    now = time.time()
    assert tabmod._time_ago(now - 10) == "just now"
    assert tabmod._time_ago(now - 150) == "2m ago"
    assert tabmod._time_ago(now - 2 * 3600 - 5) == "2h ago"
    assert tabmod._time_ago(now - 3 * 86400 - 5) == "3d ago"
    old = now - 30 * 86400
    assert tabmod._time_ago(old) == datetime.fromtimestamp(old).strftime("%b %d, %Y")


def test_event_sentence():
    assert tabmod._event_sentence({"event_type": "bit", "amount": 500, "alert_name": "Cheer"}) == ("Bits · 500 bits", "Cheer")
    assert tabmod._event_sentence({"event_type": "bit", "amount": 0}) == ("Bits", "")
    assert tabmod._event_sentence({"event_type": "follow", "amount": 3, "alert_name": None}) == ("Follows", "")
    assert tabmod._event_sentence({"event_type": "mystery", "amount": 9}) == ("mystery", "")


def test_constants_are_consistent():
    assert [s[0] for s in tabmod.SECTIONS] == ["overview", "leaders", "lifetime", "lookup", "export"]
    from modules.statistics_insights import EVENT_TYPES

    for title, icon, types, agg, unit in tabmod.LEADERBOARDS:
        assert set(types) <= set(EVENT_TYPES) and agg in ("count", "sum", "max") and unit
    assert tabmod.LIVE_INTERVAL_SEC * tabmod.LIVE_REQUERY_EVERY == 30


# --------------------------------------------------------------------------- tab state


def test_new_tab_defaults(ctx):
    tab = tabmod.StatisticsTab()
    assert tab.name == "Statistics" and tab.dirty is False
    assert (tab.section, tab.range_key, tab.metric_key, tab.heatmap_scope, tab.export_layout) == (
        "overview", "30d", "chat", "chat", "wide")
    assert tab.live_enabled and tab.compare_previous and not tab.chart_as_line
    assert tab.save() is None and tab.discard() is None
    assert tab.mgr is ctx.mgr
    assert isinstance(tab.insights, Insights) and tab.insights.mgr is ctx.mgr


def test_colors_follow_the_theme_and_survive_errors(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    assert tab._colors().primary == ctx.theme.primary
    import modules.theme_manager as tm

    monkeypatch.setattr(tm, "get_theme_manager", Mock(side_effect=RuntimeError("no themes")))
    assert tab._colors().primary == "rgb(115,0,255)"


def test_range_selection(ctx):
    tab = tabmod.StatisticsTab()
    assert tab._range().label == "Last 30 days" and tab._comparing
    tab.range_key = "7d"
    assert tab._range().days == 7
    tab.range_key = "custom"
    assert tab._range().label == "Last 30 days"  # no dates yet
    tab.custom_start, tab.custom_end = "2026-03-10T00:00", "2026-03-01"
    rng = tab._range()
    assert rng.start == datetime(2026, 3, 1) and rng.days == 10
    tab.custom_start = "not a date"
    assert tab._range().label == "Last 30 days"
    tab.range_key = "all"
    assert not tab._comparing
    add(ctx.mgr, [("Old", "follow", 0, "", datetime(2025, 5, 5, 12).timestamp())])
    rng = tab._range()
    assert rng.label == "All time" and rng.start == datetime(2025, 5, 5)


def test_range_includes_now(ctx):
    tab = tabmod.StatisticsTab()
    assert tab._range_includes_now(preset_range("today"))
    past = DateRange(datetime(2020, 1, 1), datetime(2020, 1, 2), "past")
    assert not tab._range_includes_now(past)


def test_session_and_save_text(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    ctx.mgr.data.session.start_time = time.time() - 3725
    assert tab._session_text() == "session 1:02:05"
    ctx.mgr.data.session.last_save_time = datetime(2026, 1, 1, 13, 4, 5).timestamp()
    assert tab._save_text() == "saved 13:04:05"
    ctx.mgr.data.session.last_save_time = 0
    assert tab._save_text() == "not saved yet"
    monkeypatch.setattr(tabmod, "get_statistics_manager", lambda: SimpleNamespace(data=None))
    assert tab._session_text() == "session —"
    assert tab._save_text() == "saved —"


# --------------------------------------------------------------------------- live updates


def test_live_timer_lifecycle(ctx):
    tab = tabmod.StatisticsTab()
    tab._start_live()
    assert ctx.timers == []  # not built yet
    tab.root = object()
    tab.on_enter()
    assert len(ctx.timers) == 1 and ctx.timers[0].interval == tabmod.LIVE_INTERVAL_SEC
    assert ctx.timers[0].callback == tab._live_tick
    tab._start_live()  # already running
    assert len(ctx.timers) == 1
    timer = tab.live_timer
    tab.on_exit()
    assert timer.active is False and tab.live_timer is None
    tab.live_enabled = False
    tab.on_enter()
    assert len(ctx.timers) == 1
    tab._stop_live()  # nothing to stop


def test_stop_live_tolerates_a_broken_timer(ctx):
    class Broken:
        @property
        def active(self):
            return True

        @active.setter
        def active(self, value):
            raise RuntimeError("deleted")

    tab = tabmod.StatisticsTab()
    tab.live_timer = Broken()
    tab._stop_live()
    assert tab.live_timer is None


def test_live_tick_stops_when_the_page_is_gone(ctx):
    tab = tabmod.StatisticsTab()
    tab.live_timer = FakeTimer(5, None)
    tab._live_tick()
    assert tab.live_timer is None and tab._tick == 0
    tab.root = SimpleNamespace(is_deleted=True)
    tab.live_timer = timer = FakeTimer(5, None)
    tab._live_tick()
    assert timer.active is False and tab._tick == 0


def test_live_tick_refreshes_labels_and_sections(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    tab.root = SimpleNamespace(is_deleted=False)
    tab._session_label = Mock()
    tab._save_label = Mock()
    life = Mock()
    over = Mock()
    monkeypatch.setattr(tab, "_update_lifetime_labels", life)
    monkeypatch.setattr(tab, "_update_overview", over)
    tab.section = "lifetime"
    tab._live_tick()
    life.assert_called_once()
    tab._session_label.set_text.assert_called_once()
    tab._save_label.set_text.assert_called_once()
    tab.section = "overview"
    for _ in range(tabmod.LIVE_REQUERY_EVERY - 1):
        tab._live_tick()
    assert over.call_count == 1 and tab._tick == tabmod.LIVE_REQUERY_EVERY
    tab.range_key = "custom"
    tab.custom_start, tab.custom_end = "2020-01-01", "2020-01-02"
    for _ in range(tabmod.LIVE_REQUERY_EVERY):
        tab._live_tick()
    assert over.call_count == 1  # a past range is not re-queried
    tab.section = "leaders"
    tab._live_tick()
    tab.section = "lifetime"
    life.side_effect = RuntimeError("boom")
    tab._live_tick()  # swallowed


# --------------------------------------------------------------------------- building and the toolbar


def test_build_shows_toolbar_nav_and_overview(ctx, monkeypatch):
    seed(ctx.mgr)
    ensure = Mock()
    monkeypatch.setattr(ctx.mgr, "ensure_periodic_saving", ensure)
    tab = tabmod.StatisticsTab()

    async def check(user):
        for text in ("Today", "7 days", "30 days", "90 days", "All time", "Custom", "Overview", "Leaderboards",
                     "Lifetime", "Viewer lookup", "Export highlights", "Live", "Spotlight", "What happened",
                     "When your community is active", "Chat messages over time", "Most active viewer", "Alice"):
            await user.should_see(text)
        await user.should_see("compared with the previous 30 days")
        await user.should_not_see("No activity recorded in this range")

    run_tab(tab, check)
    ensure.assert_called_once()
    assert ctx.sheets and ctx.sheets[0][0] == "mycelian-statistics" and ".mst" in ctx.sheets[0][1]
    assert len(ctx.timers) == 1 and ctx.timers[0].active
    tiles = tab._overview_refs["tiles"]
    assert set(tiles) == {m.key for m in METRICS}
    assert tiles["chat"]["value"].text == "4" and tiles["bits"]["value"].text == "500"
    assert "is-active" in tiles["chat"]["el"].classes
    assert tab._overview_refs["series_bucket"] == "day"
    assert tab._session_label is not None and tab._save_label is not None


def test_build_survives_periodic_saving_errors(ctx, monkeypatch):
    monkeypatch.setattr(ctx.mgr, "ensure_periodic_saving", Mock(side_effect=RuntimeError("x")))
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("Spotlight")

    run_tab(tab, check)


def test_empty_statistics_show_helpful_notices(ctx):
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("No activity recorded in this range")
        await user.should_see("Mycelian records events while it runs")
        await user.should_see("No viewer activity in this range yet.")
        await user.should_see("Nothing recorded in this range.")
        await user.should_see("No messages in this range.")
        await user.should_not_see("Show all time")

    run_tab(tab, check)
    assert tab._overview_refs["trend_sub"].text == "per day · total 0"


def test_old_events_offer_the_all_time_range(ctx):
    add(ctx.mgr, [("Old", "follow", 0, "", datetime(2024, 2, 3, 12).timestamp()),
                  ("Old", "follow", 0, "", datetime(2024, 2, 5, 12).timestamp())])
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("The event log covers Feb 03, 2024 to Feb 05, 2024 (2 events). Try All time.")
        user.find("Show all time").click()
        assert tab.range_key == "all"
        await user.should_see("Spotlight")
        await user.should_not_see("No activity recorded in this range")
        await user.should_not_see("compared with the previous")

    run_tab(tab, check)


def test_range_buttons_switch_presets(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        click_text(user, "7 days")
        assert tab.range_key == "7d"
        await user.should_see("compared with the previous 7 days")
        click_text(user, "Today")
        assert tab.range_key == "today"
        await user.should_see("Today · compared with the previous 1 day")
        assert tab._overview_refs["series_bucket"] == "hour"

    run_tab(tab, check)


def test_custom_range_inputs(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        click_text(user, "Custom")
        assert tab.range_key == "custom" and tab.custom_start and tab.custom_end
        start = next(e for e in user.find("From").elements if isinstance(e, ui.input))
        end = next(e for e in user.find("To").elements if isinstance(e, ui.input))
        assert start.value == tab.custom_start
        start.value = "2026-01-05"
        end.value = "2026-01-01T10:00"
        user.find("Apply").click()
        assert (tab.custom_start, tab.custom_end) == ("2026-01-05", "2026-01-01")
        await user.should_see("Jan 01, 2026 – Jan 05, 2026 · compared with the previous 5 days")
        # Switching back to custom keeps the chosen dates.
        click_text(user, "30 days")
        click_text(user, "Custom")
        assert tab.custom_start == "2026-01-05"

    run_tab(tab, check)


def test_live_switch_and_toolbar_buttons(ctx, monkeypatch):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    saved = Mock()
    monkeypatch.setattr(tab, "_save_now", saved)

    async def check(user):
        timer = tab.live_timer
        switch = user.find(kind=ui.switch)
        switch.click()
        assert tab.live_enabled is False and timer.active is False and tab.live_timer is None
        await user.should_see("Paused")
        user.find(kind=ui.switch).click()
        assert tab.live_enabled is True and tab.live_timer is not None and tab.live_timer.active
        await user.should_see("Live")
        UserInteraction(user, {icon_button(user, "refresh")}, None).click()
        await user.should_see("Spotlight")
        UserInteraction(user, {icon_button(user, "save")}, None).click()

    run_tab(tab, check)
    saved.assert_called_once()


def test_nav_buttons_switch_sections(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        click_text(user, "Leaderboards")
        assert tab.section == "leaders"
        await user.should_see("Top cheerers")
        click_text(user, "Lifetime")
        await user.should_see("All-time totals since you started using Mycelian")
        click_text(user, "Viewer lookup")
        await user.should_see("Search for a viewer")
        click_text(user, "Export highlights")
        await user.should_see("Share your stream highlights")
        click_text(user, "Overview")
        await user.should_see("Spotlight")
        nav = [c for c in tab.nav.default_slot.children]
        assert ["is-active" in c.classes for c in nav] == [True, False, False, False, False]

    run_tab(tab, check)


def test_section_errors_show_a_notice(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    monkeypatch.setattr(tab, "_build_leaders", Mock(side_effect=RuntimeError("bad query")))

    async def check(user):
        tab._set_section("leaders")
        await user.should_see("Statistics could not be loaded. Check the log for details.")

    run_tab(tab, check)


# --------------------------------------------------------------------------- overview interactions


def test_tiles_switch_the_charted_metric(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        click_text(user, "Bits cheered")
        assert tab.metric_key == "bits"
        tiles = tab._overview_refs["tiles"]
        assert "is-active" in tiles["bits"]["el"].classes and "is-active" not in tiles["chat"]["el"].classes
        assert tab._overview_refs["trend_title"].text == "Bits cheered over time"
        assert "total 500 · peak 500 on" in tab._overview_refs["trend_sub"].text
        tab._select_metric("bits")  # same metric: nothing to do
        tab._select_metric("viewers")
        assert tab._overview_refs["trend_sub"].text == "per day · distinct viewers in each day"
        chart = tab._overview_refs["trend_chart"]
        assert chart.options["series"][0]["name"] == "Active viewers"

    run_tab(tab, check)


def test_trend_chart_line_mode_and_previous_period(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        chart = tab._overview_refs["trend_chart"]
        assert chart.options["series"][0]["type"] == "bar"
        assert "markLine" in chart.options["series"][0]
        user.find("Previous period average").click()
        assert tab.compare_previous is False
        assert "markLine" not in tab._overview_refs["trend_chart"].options["series"][0]
        user.find("Previous period average").click()
        assert tab.compare_previous is True
        assert "markLine" in tab._overview_refs["trend_chart"].options["series"][0]
        click_text(user, "show_chart")
        assert tab.chart_as_line is True
        assert tab._overview_refs["trend_chart"].options["series"][0]["type"] == "line"
        tab._toggle_line(True)  # no change, no rebuild
        click_text(user, "bar_chart")
        assert tab.chart_as_line is False

    run_tab(tab, check)


def test_today_range_peaks_are_reported_by_hour(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.range_key = "today"

    async def check(user):
        assert " at " in tab._overview_refs["trend_sub"].text
        assert tab._overview_refs["trend_sub"].text.startswith("per hour · total 4")

    run_tab(tab, check)


def test_all_time_has_no_comparison(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.range_key = "all"

    async def check(user):
        await user.should_not_see("Previous period average")
        await user.should_see("in range")
        data = tab._overview_refs["data"]
        assert data["prev"] is None and data["prev_series"] is None

    run_tab(tab, check)


def test_redraw_trend_without_a_chart_is_a_no_op(ctx):
    tab = tabmod.StatisticsTab()
    tab._redraw_trend()
    tab._overview_refs = {"data": {"series": {}}}
    tab._redraw_trend()


def test_weekly_trend_labels(ctx):
    add(ctx.mgr, [("Alice", "chat_message", 1, "", recent())])
    tab = tabmod.StatisticsTab()
    tab.range_key = "custom"
    today = datetime.now()
    tab.custom_start = (today - timedelta(days=300)).strftime("%Y-%m-%d")
    tab.custom_end = today.strftime("%Y-%m-%d")

    async def check(user):
        assert tab._overview_refs["series_bucket"] == "week"
        assert "per week · total 1 · peak 1, week of" in tab._overview_refs["trend_sub"].text

    run_tab(tab, check)


def test_update_overview_refreshes_tiles_and_chart(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        add(ctx.mgr, [("Zed", "bit", 1000, "", recent())])
        tab._overview_refs["tiles"].pop("raids")  # a tile that is gone is skipped
        tab._update_overview(tab._range())
        assert tab._overview_refs["tiles"]["bits"]["value"].text == "1,500"
        tab._select_metric("bits")
        assert "total 1,500" in tab._overview_refs["trend_sub"].text

    run_tab(tab, check)


def test_spotlight_names_open_the_viewer(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("Top gifter")
        await user.should_see("Longest watch streak")
        await user.should_see("500 bits")
        click_text(user, "Cara")
        assert tab.section == "lookup" and tab.lookup_username == "Cara" and tab.lookup_types == set()
        await user.should_see("Recent activity")

    run_tab(tab, check)


def test_open_viewer_ignores_blank_and_unknown_names(ctx):
    tab = tabmod.StatisticsTab()
    tab._open_viewer("")
    tab._open_viewer("?")
    assert tab.section == "overview" and tab.lookup_username == ""


def test_event_mix_rows_chart_their_metric(ctx):
    seed(ctx.mgr)
    add(ctx.mgr, [("Kim", "connector", 1, "", recent())])
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("11 events from 6 viewers")
        click_text(user, "Gift subs")
        assert tab.metric_key == "gifts"
        rows = [e for e in user.find(kind=ui.element).elements if "mst-bar-row" in e.classes]
        linked = {"is-link" in r.classes for r in rows}
        assert linked == {True, False}  # connector runs have no metric to chart
        assert all("giveaway_draw_complete" not in str(r.props) for r in rows)

    run_tab(tab, check)


def test_heatmap_scopes(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("busiest")
        charts = [e for e in user.find(kind=ui.echart).elements if e.options.get("series", [{}])[0].get("type") == "heatmap"]
        assert sum(v[2] for v in charts[0].options["series"][0]["data"]) == 4  # chat only
        click_text(user, "All events")
        assert tab.heatmap_scope == "all"
        charts = [e for e in user.find(kind=ui.echart).elements if e.options.get("series", [{}])[0].get("type") == "heatmap"]
        assert sum(v[2] for v in charts[0].options["series"][0]["data"]) == 10
        tab.heatmap_scope = "bogus"
        tab._render_section()
        await user.should_see("Chat messages")

    run_tab(tab, check)


def test_heatmap_empty_for_all_events(ctx):
    tab = tabmod.StatisticsTab()
    tab.heatmap_scope = "all"

    async def check(user):
        await user.should_see("No events in this range.")

    run_tab(tab, check)


def test_metric_tile_without_previous_period(ctx):
    tab = tabmod.StatisticsTab()
    tab.range_key = "all"
    assert '<span class="mst-tile-note">in range</span>' in tab._tile_foot(5, None, [1, 2])
    tab.range_key = "30d"
    assert "is-up" in tab._tile_foot(5, 2, [1, 2])
    assert METRICS_BY_KEY["chat"].label == "Chat messages"


# --------------------------------------------------------------------------- Twitch profile pictures

PIC = "https://static-cdn.jtvnw.net/jtv_user_pictures/x-profile_image-300x300.png"


def test_avatar_html_variants():
    assert tabmod._avatar_html("bob") == '<div class="mst-avatar">B</div>'
    with_pic = tabmod._avatar_html("bob", PIC, "is-lg")
    assert with_pic.startswith('<div class="mst-avatar is-lg has-photo">B<img src="https://static-cdn')
    assert 'onerror="this.remove()"' in with_pic and 'referrerpolicy="no-referrer"' in with_pic
    assert "loading=" not in with_pic  # lazy images may never load once the page is open
    assert "<img" not in tabmod._avatar_html("bob", "http://plain/x.png")
    assert "&lt;" in tabmod._avatar_html("<x>")


def _avatar_htmls(user):
    return [e.content for e in user.find(kind=ui.html).elements if "mst-avatar" in str(e.content)]


def test_spotlight_shows_twitch_pictures_and_fills_late_ones(ctx, monkeypatch):
    from modules import twitch_avatars

    seed(ctx.mgr)
    known = {"Bob": PIC}
    asked = []
    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {n: known[n] for n in names if n in known})
    monkeypatch.setattr(twitch_avatars, "request_avatars", lambda names, on_update=None: asked.append((list(names), on_update)))
    hops = []
    monkeypatch.setattr(tabmod, "run_on_ui_loop", lambda fn: hops.append(fn))
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("Spotlight")
        avatars = _avatar_htmls(user)
        # Bob (bits) has a picture; Dan (watch streak) is a Twitch viewer still being fetched;
        # Alice (chat only, no Twitch counter) and Cara (gift subs) keep their initials.
        assert sum("<img" in a for a in avatars) == 1 and any(PIC in a and ">B<" in a for a in avatars)
        assert asked and sorted(asked[0][0]) == ["Bob", "Dan"]
        known["Dan"] = PIC
        asked[0][1]()  # the lookup thread reports back
        hops[0]()  # runs on the UI loop with no client context, as in the app
        assert sum("<img" in a for a in _avatar_htmls(user)) == 2
        # Alice is both most active and top chatter.
        assert [name for name, _x, _el in tab._pending_avatars] == ["Alice", "Alice", "Cara"]

    run_tab(tab, check)


def test_viewer_profile_shows_the_picture(ctx, monkeypatch):
    from modules import twitch_avatars

    seed(ctx.mgr)
    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {n: PIC for n in names})
    monkeypatch.setattr(twitch_avatars, "request_avatars", lambda *a, **k: pytest.fail("all known"))
    tab = tabmod.StatisticsTab()

    async def check(user):
        await user.should_see("Spotlight")
        click_text(user, "Dan")
        await user.should_see("Recent activity")
        assert any("is-lg has-photo" in a for a in _avatar_htmls(user))

    run_tab(tab, check)


def test_fill_pending_avatars_drops_deleted_elements(ctx, monkeypatch):
    from modules import twitch_avatars

    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {})
    tab = tabmod.StatisticsTab()
    gone = SimpleNamespace(is_deleted=True)
    waiting = SimpleNamespace(is_deleted=False, set_content=lambda c: pytest.fail("no picture yet"))
    tab._pending_avatars = [("a", "", gone), ("b", "", waiting)]
    tab._fill_pending_avatars()
    assert tab._pending_avatars == [("b", "", waiting)]

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Statistics dashboard, part 2: Save now, Leaderboards, Lifetime, Viewer lookup and
Export highlights (preview, PNG save, exports folder, channel name)."""
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


# --------------------------------------------------------------------------- save now


def test_save_now_reports_success_and_updates_the_label(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    force = Mock()
    monkeypatch.setattr(ctx.mgr, "force_save", force)
    tab._save_now()  # no label yet
    tab._save_label = Mock()
    tab._save_now()
    assert force.call_count == 2
    assert [n["title"] for n in ctx.notes] == ["Statistics saved"] * 2
    assert ctx.notes[0]["type"] == "success" and ctx.notes[0]["source"] == "statistics"
    tab._save_label.set_text.assert_called_once()


def test_save_now_stays_quiet_when_the_manager_already_reported(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()

    def failing_save():
        ctx.mgr.last_save_ok = False

    monkeypatch.setattr(ctx.mgr, "force_save", failing_save)
    tab._save_now()
    assert ctx.notes == []


def test_save_now_reports_errors(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    monkeypatch.setattr(ctx.mgr, "force_save", Mock(side_effect=OSError("read-only")))
    tab._save_now()
    note = ctx.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't save statistics"
    assert note["details"] == "OSError: read-only"


def test_save_now_really_saves(ctx):
    ctx.mgr.increment_twitch_messages("Ann")
    tab = tabmod.StatisticsTab()
    tab._save_now()
    assert ctx.notes[-1]["title"] == "Statistics saved"
    assert ctx.mgr._load_lifetime_totals_from_stats_db()["data"]["chat"]["total_messages"] == 1


# --------------------------------------------------------------------------- leaderboards


def test_leaderboards_rank_viewers_and_open_them(ctx):
    seed(ctx.mgr)
    add(ctx.mgr, [("Bob", "chat_message", 1, "", recent()), ("__system__", "chat_message", 1, "", recent())])
    tab = tabmod.StatisticsTab()
    tab.section = "leaders"

    async def check(user):
        await user.should_see("Leaders for last 30 days. Click a viewer for details.")
        for title, *_ in tabmod.LEADERBOARDS:
            await user.should_see(title)
        await user.should_see("Nobody yet in this range.")
        await user.should_not_see("__system__")
        rows = [e for e in user.find(kind=ui.element).elements if "mst-rank-row" in e.classes]
        assert any('title="Alice: 3 messages"' in str(r.props) or r.props.get("title") == "Alice: 3 messages" for r in rows)
        click_text(user, "Dan")
        assert tab.section == "lookup" and tab.lookup_username == "Dan"

    run_tab(tab, check)


def test_leaderboards_for_a_custom_range_keep_the_label_case(ctx):
    tab = tabmod.StatisticsTab()
    tab.section = "leaders"
    tab.range_key = "custom"
    tab.custom_start, tab.custom_end = "2026-01-01", "2026-01-31"

    async def check(user):
        await user.should_see("Leaders for Jan 01, 2026 – Jan 31, 2026.")

    run_tab(tab, check)


def test_ranked_list_static_rows_are_not_clickable(ctx):
    tab = tabmod.StatisticsTab()

    async def check(user):
        with tab.body:
            tab._ranked_list([{"username": "<b>x</b>", "value": 0}, {"username": "y", "value": 0}], "uses", clickable=False)
        rows = [e for e in user.find(kind=ui.element).elements if "mst-rank-row" in e.classes and "is-static" in e.classes]
        assert len(rows) == 2 and all(clickable(r) is None for r in rows)
        await user.should_see("&lt;b&gt;x&lt;/b&gt;")

    run_tab(tab, check)


# --------------------------------------------------------------------------- lifetime


def seed_lifetime(m):
    m.increment_bit_alerts(1500, "Alice")
    m.increment_gift_subs(5, "Bob")
    m.increment_hype_train_completion(2)
    m.increment_hype_train_completion(7)
    m.increment_commands_triggered("Cara", "!hello")
    m.increment_connectors_triggered("Cara", "Lights")
    m.increment_quote_redeemed("12", "Cara")
    m.record_giveaway_entry("Dan")
    m.record_giveaway_round_complete()
    m.record_giveaway_winner("Dan")
    m.record_poll_finished([{"keyword": "pizza", "name": "Pizza", "votes": 6}], [{"keyword": "pizza"}])
    m.increment_twitch_messages("Eve")
    m.submit_template_stat("Overlay A", "goal_count", 12345)
    m.submit_template_stat("Overlay A", "mood", "hype")
    m.submit_template_stat("Overlay B", "empty", 0)
    m.data.templates["Overlay B"].custom_stats.clear()


def test_lifetime_values_formatting(ctx):
    tab = tabmod.StatisticsTab()
    values = tab._lifetime_values({
        "alerts": {"total_bits": 12345, "highest_watch_streak_seen": None},
        "giveaways": {"average_entries_per_giveaway": 2.345},
        "polls": {"average_votes_per_poll": None, "most_votes": {"name": "Pizza", "count": 1200}, "most_wins": {"name": ""}},
        "chat": None,
    })
    assert values["alerts.bits"] == "12,345" and values["alerts.streak_peak"] == "0"
    assert values["giveaways.avg"] == "2.3" and values["polls.avg"] == "0.0"
    assert values["polls.most_votes"] == "Pizza (1,200)" and values["polls.most_wins"] == "—"
    assert values["chat.twitch"] == "0"
    assert len(tab._lifetime_values({})) == 30


def test_lifetime_section_shows_every_card(ctx):
    seed_lifetime(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.section = "lifetime"

    async def check(user):
        for text in ("Alerts", "Bits cheered", "1,500", "Hype trains", "Chatbot", "Most used commands", "!hello",
                     "Connectors", "Most active connectors", "Lights", "Quotes", "Most requested", "Quote #12",
                     "Giveaways", "Luckiest viewers", "Polls", "Pizza (6)", "Overlay statistics", "Overlay A",
                     "Goal count", "12,345", "hype"):
            await user.should_see(text)
        await user.should_not_see("Overlay B")
        await user.should_not_see("No hype trains completed yet.")
        charts = [e for e in user.find(kind=ui.echart).elements]
        levels = charts[0].options["xAxis"]["data"]
        assert levels == [f"Level {i}" for i in range(1, 8)] and charts[0].options["series"][0]["data"][1] == 1
        click_text(user, "Dan")
        assert tab.section == "lookup" and tab.lookup_username == "Dan"

    run_tab(tab, check)


def test_lifetime_section_when_empty(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    tab.section = "lifetime"
    real = ctx.mgr.get_all_statistics

    def with_bad_levels():
        stats = real()
        stats["hype_trains"]["level_completions"] = {"x": 1, "2": None}
        return stats

    monkeypatch.setattr(ctx.mgr, "get_all_statistics", with_bad_levels)

    async def check(user):
        await user.should_see("No hype trains completed yet.")
        await user.should_not_see("Most used commands")
        await user.should_not_see("Luckiest viewers")
        await user.should_not_see("Overlay statistics")

    run_tab(tab, check)


def test_lifetime_labels_update_live(ctx):
    seed_lifetime(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.section = "lifetime"

    async def check(user):
        assert tab._lifetime_labels["alerts.bits"].text == "1,500"
        ctx.mgr.increment_bit_alerts(500, "Alice")
        ctx.mgr.submit_template_stat("Overlay A", "mood", "calm")
        ctx.mgr.submit_template_stat("Overlay A", "goal_count", 1)
        tab.root = SimpleNamespace(is_deleted=False)
        tab._live_tick()
        assert tab._lifetime_labels["alerts.bits"].text == "2,000"
        assert tab._lifetime_labels["tpl.Overlay A.mood"].text == "calm"
        assert tab._lifetime_labels["tpl.Overlay A.goal_count"].text == "1"
        tab._lifetime_labels = {}
        tab._update_lifetime_labels()  # nothing to refresh

    run_tab(tab, check)


# --------------------------------------------------------------------------- viewer lookup


def test_lookup_starts_with_a_prompt_and_known_names(ctx):
    ctx.mgr.increment_twitch_messages("Ann")
    ctx.mgr._spill_raw_user_map("chat.user_stats", {"Stored": {"total_messages": 1}})
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"

    async def check(user):
        await user.should_see("Search for a viewer, or click a name on Overview or Leaderboards.")
        await user.should_see("2 viewers have recorded activity. Date range: Last 30 days.")
        search = next(e for e in user.find("Viewer").elements if isinstance(e, ui.input))
        assert search.props["_autocomplete"] == ["Ann", "Stored"]

    run_tab(tab, check)


def test_lookup_survives_a_failing_name_list(ctx, monkeypatch):
    monkeypatch.setattr(ctx.mgr, "get_all_tracked_usernames", Mock(side_effect=RuntimeError("db")))
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"

    async def check(user):
        await user.should_see("0 viewers have recorded activity.")

    run_tab(tab, check)


def test_lookup_search_shows_a_full_profile(ctx):
    seed(ctx.mgr)
    ctx.mgr.increment_bit_alerts(500, "Bob", "Cheer")
    ctx.mgr.increment_twitch_messages("Bob")
    ctx.mgr._flush_pending_events()
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"

    async def check(user):
        search = next(e for e in user.find("Viewer").elements if isinstance(e, ui.input))
        search.value = "  BOB "
        user.find("Look up").click()
        assert tab.lookup_username == "BOB"
        for text in ("First seen", "Last seen just now", "4 events all time", "Activity over time", "All events per day",
                     "Lifetime totals", "Bit alerts", "Recent activity", "Bits · 500 bits", "Cheer"):
            await user.should_see(text)
        profile = [e for e in user.find(kind=ui.label).elements if "mst-profile-name" in e.classes]
        assert profile[0].text == "Bob"
        tiles = [e for e in user.find(kind=ui.element).elements if "mst-tile" in e.classes and "is-static" in e.classes]
        assert len(tiles) == 2  # chat messages and bits
        # Filter the timeline by type, then clear the filter.
        click_text(user, "Chat messages", index=0)
        assert tab.lookup_types == {"chat_message"}
        await user.should_not_see("Bits · 500 bits")
        chip = [e for e in user.find(kind=ui.element).elements if "mst-chip" in e.classes and "is-active" in e.classes]
        assert chip and chip[0].props["aria-pressed"] == "true"
        click_text(user, "Chat messages", index=0)
        assert tab.lookup_types == set()
        await user.should_see("Bits · 500 bits")
        # Enter in the search box looks up too.
        search = next(e for e in user.find("Viewer").elements if isinstance(e, ui.input))
        search.value = "nobody here"
        UserInteraction(user, {search}, None).trigger("keydown.enter")
        await user.should_see("No statistics recorded for “nobody here”.")

    run_tab(tab, check)


def test_lookup_of_a_viewer_with_only_lifetime_totals(ctx):
    ctx.mgr._spill_raw_user_map("alerts.user_stats", {"Legacy": {"raids": 2, "total_alerts": 2}})
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"
    tab.lookup_username = "legacy"

    async def check(user):
        await user.should_see("0 events all time")
        await user.should_see("No activity in this range.")
        await user.should_see("No events in this range.")
        await user.should_see("Raids")
        await user.should_not_see("First seen")

    run_tab(tab, check)


def test_lookup_of_a_viewer_with_only_events(ctx):
    add(ctx.mgr, [("Ghost", "mystery_event", 0, "", recent()), ("Ghost", "follow", 0, "", time.time() - 45 * 86400)])
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"
    tab.lookup_username = "ghost"
    tab.range_key = "custom"
    tab.custom_start = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    tab.custom_end = datetime.now().strftime("%Y-%m-%d")

    async def check(user):
        await user.should_see("No lifetime totals saved.")
        await user.should_see("mystery_event")
        await user.should_see(f"events · {tab._range().label}")

    run_tab(tab, check)


def test_viewer_lifetime_lists_only_non_zero_totals(ctx):
    tab = tabmod.StatisticsTab()
    tab.section = "lookup"  # nothing else on the page mentions raids

    async def check(user):
        with tab.body:
            tab._viewer_lifetime({"chat": {"twitch_messages_received": 1200}, "giveaways": {"giveaway_wins": 2},
                                  "alerts": {"raids": 0}, "quotes": None})
        await user.should_see("Chat messages")
        await user.should_see("1,200")
        await user.should_see("Giveaway wins")
        await user.should_not_see("Raids")

    run_tab(tab, check)


# --------------------------------------------------------------------------- export


def test_export_section_previews_the_image(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.section = "export"

    async def check(user):
        await user.should_see("Share your stream highlights")
        await user.should_see("Change it in the bar above.")
        imgs = [e for e in user.find(kind=ui.html).elements if "data:image/jpeg;base64," in e.content]
        assert len(imgs) == 1
        r = ctx.renders[-1]
        assert r["layout"] == "wide" and r["streamer_name"] == "TwitchName" and r["theme"] is ctx.theme
        assert r["source"] == "event_log" and r["highlights"]["total_events"] == 10
        assert sum(r["extra"]["activity_values"]) == 9  # the giveaway draw is not activity
        assert r["extra"]["activity_bucket"] == "day" and set(r["extra"]["deltas"]) == {m.key for m in METRICS}
        click_text(user, "Portrait 4:5")
        assert tab.export_layout == "portrait" and ctx.renders[-1]["layout"] == "portrait"
        assert "is-portrait" in tab._export_preview.classes

    run_tab(tab, check)


def test_export_range_labels(ctx):
    tab = tabmod.StatisticsTab()
    tab.section = "export"
    tab.range_key = "today"

    async def check(user):
        await user.should_see("Today")
        await user.should_see("Nothing to export for this range yet.")
        tab._set_range("7d")
        rng = tab._range()
        await user.should_see(f"{rng.start:%b %d} – {rng.end:%b %d, %Y}")

    run_tab(tab, check)


def test_export_preview_failure(ctx, monkeypatch):
    seed(ctx.mgr)
    import modules.statistics_export as ex

    monkeypatch.setattr(ex, "render_highlights_image", Mock(side_effect=RuntimeError("font missing")))
    tab = tabmod.StatisticsTab()
    tab.section = "export"

    async def check(user):
        await user.should_see("Couldn't draw the preview. Details are in the log.")
        tab._export_preview = None
        tab._render_export_preview()  # no preview box: nothing to do

    run_tab(tab, check)


def test_export_inputs_without_comparison(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.range_key = "all"
    highlights, source, extra = tab._export_inputs(tab._range())
    assert source == "event_log" and "deltas" not in extra
    assert len(extra["activity_labels"]) == len(extra["activity_values"])


def test_save_png_writes_the_file_and_notifies(ctx):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    tab.section = "export"

    async def check(user):
        user.find("Save PNG").click()
        path = tab.export_last_path
        assert path and Path(path).parent == ctx.exports_root / "exports"
        assert Path(path).name.startswith("highlights_") and not path.endswith("_portrait.png")
        with Image.open(path) as img:
            assert img.format == "PNG" and img.size == (64, 36)
        await user.should_see("Last saved")
        await user.should_see(path)
        note = ctx.notes[-1]
        assert note["title"] == "Highlights image saved" and note["message"] == f"Saved to {path}."
        assert note["actions"][1] == {"kind": "copy", "text": path, "label": "Copy path"}
        note["actions"][0]["fn"]()
        assert ctx.reveals[-1] == Path(path)
        click_text(user, "Portrait 4:5")
        user.find("Save PNG").click()
        assert tab.export_last_path.endswith("_portrait.png")

    run_tab(tab, check)


def test_save_png_notes_lifetime_totals(ctx):
    ctx.mgr.increment_twitch_messages()
    tab = tabmod.StatisticsTab()
    tab._export_save()
    assert ctx.renders[-1]["source"] == "lifetime"
    assert "Numbers are lifetime totals" in ctx.notes[-1]["message"]
    assert tab.export_last_path


def test_save_png_with_nothing_to_export(ctx):
    tab = tabmod.StatisticsTab()
    tab._export_save()
    assert ctx.notes[-1]["title"] == "Nothing to export" and ctx.notes[-1]["type"] == "info"
    assert tab.export_last_path is None and ctx.renders == []


def test_save_png_failure_is_reported(ctx, monkeypatch):
    seed(ctx.mgr)
    tab = tabmod.StatisticsTab()
    import modules.statistics_export as ex

    monkeypatch.setattr(ex, "render_highlights_image", Mock(return_value=Mock(convert=Mock(side_effect=OSError("disk full")))))
    tab._export_save()
    note = ctx.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't save the highlights image"
    assert note["details"] == "OSError: disk full"


def test_open_exports_folder(ctx, monkeypatch):
    tab = tabmod.StatisticsTab()
    tab._open_exports_folder()
    assert ctx.reveals == [ctx.exports_root / "exports"] and (ctx.exports_root / "exports").is_dir()
    tab.export_last_path = str(ctx.exports_root / "exports" / "x.png")
    tab._open_exports_folder()
    assert ctx.reveals[-1] == Path(tab.export_last_path)
    import modules.path_utils as pu

    monkeypatch.setattr(pu, "reveal_in_file_manager", Mock(side_effect=OSError("no file manager")))
    tab._open_exports_folder()
    note = ctx.notes[-1]
    assert note["type"] == "warning" and note["source"] == "files"
    assert note["actions"] == [{"kind": "copy", "text": tab.export_last_path, "label": "Copy path"}]


def test_open_exports_folder_button(ctx):
    tab = tabmod.StatisticsTab()
    tab.section = "export"

    async def check(user):
        UserInteraction(user, {icon_button(user, "folder_open")}, None).click()
        assert ctx.reveals == [ctx.exports_root / "exports"]

    run_tab(tab, check)


def test_render_export_status_without_a_container(ctx):
    tab = tabmod.StatisticsTab()
    tab.export_last_path = "x"
    tab._render_export_status()  # nothing built yet


@pytest.mark.parametrize("twitch_name,streamer,expected", [
    ("TwitchName", "Streamy", "TwitchName"),
    ("N/A", "Streamy", "Streamy"),
    ("  ", "  ", "Mycelian"),
    (None, None, "Mycelian"),
    ("Unknown", "Streamy", "Streamy"),
])
def test_channel_name(ctx, twitch_name, streamer, expected):
    ctx.status["user_name"] = twitch_name
    ctx.settings.streamer_name = streamer
    assert tabmod.StatisticsTab._channel_name() == expected


def test_channel_name_when_twitch_fails(ctx):
    ctx.twitch.get_twitch_connection_status = Mock(side_effect=RuntimeError("offline"))
    assert tabmod.StatisticsTab._channel_name() == "Streamy"

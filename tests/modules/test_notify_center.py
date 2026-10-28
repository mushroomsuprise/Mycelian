# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.notify_center`` (header bell, unread badge and the notification side panel)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import notification_engine as ne
from modules import notify_center as nc
from modules.help_system import contextual_help as ch
from modules.help_system import help_render
from ui_sim import run_ui

NOW = datetime(2026, 10, 27, 12, 0, 0)
NOW_TS = NOW.timestamp()


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class Engine:
    """Stands in for the notification engine's history and actions."""

    def __init__(self):
        self.history = []
        self.calls = []
        self.refresh = []
        self.call_actions = {}


@pytest.fixture
def eng(monkeypatch):
    e = Engine()
    monkeypatch.setattr(nc, "S", nc._CenterState())
    nc.S.last_read_ts = NOW_TS - 3600
    monkeypatch.setattr(nc, "time", SimpleNamespace(time=lambda: NOW_TS))
    monkeypatch.setattr(nc, "datetime", FixedDateTime)
    monkeypatch.setattr(ne, "get_history", lambda: list(e.history))
    monkeypatch.setattr(ne, "_call_actions", e.call_actions)
    monkeypatch.setattr(ne, "remove_history_item", lambda i: e.calls.append(("remove", i)))
    monkeypatch.setattr(ne, "clear_history", lambda: e.calls.append(("clear",)))
    monkeypatch.setattr(ne, "run_entry_action", lambda i, idx: e.calls.append(("run", i, idx)))
    monkeypatch.setattr(ne, "run_action", lambda a, message, entry: e.calls.append(("action", a, message, entry["id"])))
    monkeypatch.setattr(ne, "_find_entry", lambda i: next((h for h in e.history if h.get("id") == i), None))
    monkeypatch.setattr(ne, "inject_notification_ui_assets", lambda: e.calls.append(("assets",)))
    monkeypatch.setattr(ne, "register_history_refresh", lambda cb: e.refresh.append(cb))
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: e.calls.append(("css", sid)))
    monkeypatch.setattr(ch, "help_button", lambda **kw: e.calls.append(("help", kw.get("topic_id"))))
    monkeypatch.setattr(ch, "navigate_to_settings_subtab", lambda sub, main_tab=None: e.calls.append(("nav", sub, main_tab)))
    return e


def entry(i, *, ago=10, **kw):
    base = {"id": str(i), "ts": NOW_TS - ago, "type": "info", "title": f"Title {i}", "message": f"Message {i}",
            "source": "app"}
    base.update(kw)
    return base


def click(user, element):
    UserInteraction(user, {element}, None).click()


# --------------------------------------------------------------------------- pure helpers

def test_entry_level_prefers_level_then_type():
    assert nc._entry_level({"level": "warning", "type": "negative"}) == "warning"
    assert nc._entry_level({"type": "negative"}) == "error"
    assert nc._entry_level({}) == "info"


def test_panel_is_open(eng):
    assert nc.panel_is_open() is False
    nc.S.dialog = SimpleNamespace(value=True, is_deleted=False)
    assert nc.panel_is_open() is True
    nc.S.dialog.value = False
    assert nc.panel_is_open() is False
    nc.S.dialog = SimpleNamespace(value=True, is_deleted=True)
    assert nc.panel_is_open() is False


def test_unread_count(eng):
    eng.history = [entry(1, ago=10), entry(2, ago=7200), entry(3, ts=None)]
    assert nc.unread_count() == 1
    nc.S.dialog = SimpleNamespace(value=True)
    assert nc.unread_count() == 0


@pytest.mark.parametrize("ago,text", [(-50, "now"), (0, "now"), (44, "now"), (45, "1 min"), (600, "10 min"),
                                      (3600, "1 h"), (86399, "23 h")])
def test_relative(eng, ago, text):
    assert nc._relative(NOW_TS - ago) == text


def test_relative_old_shows_date(eng):
    ts = (NOW - timedelta(days=5)).timestamp()
    assert nc._relative(ts) == "Oct 22"


def test_group_label(eng):
    assert nc._group_label(NOW_TS - 60) == "Today"
    assert nc._group_label((NOW - timedelta(days=1)).timestamp()) == "Yesterday"
    assert nc._group_label((NOW - timedelta(days=3)).timestamp()) == "Earlier"


def test_matches_filter_and_query(eng):
    e_err = entry(1, type="negative", title="OBS lost", source="obs")
    e_ongoing = entry(2, level="ongoing", title="Saving")
    assert nc._matches(e_err) and nc._matches(e_ongoing)
    nc.S.level_filter = "error"
    assert nc._matches(e_err) and not nc._matches(e_ongoing)
    nc.S.level_filter = "info"
    assert nc._matches(e_ongoing) and not nc._matches(e_err)
    nc.S.level_filter = ""
    nc.S.query = "obs"
    assert nc._matches(e_err) and not nc._matches(e_ongoing)
    nc.S.query = "playstation"
    assert nc._matches(entry(3, source="psn"))
    nc.S.query = "fix it"
    assert nc._matches(entry(4, hint="Fix it now", title=None, message=None))


class FakeBadge:
    def __init__(self):
        self.text = None
        self.class_calls = []
        self.is_deleted = False

    def set_text(self, text):
        self.text = text

    def classes(self, add=None, remove=None):
        self.class_calls.append((add, remove))


def test_update_badge_states(eng):
    nc.update_badge()  # no badge: nothing
    badge = FakeBadge()
    nc.S.badge = badge
    nc.update_badge()
    assert badge.text == "" and badge.class_calls[-1] == ("hidden", None)
    eng.history = [entry(i) for i in range(12)]
    nc.update_badge()
    assert badge.text == "12"
    add, remove = badge.class_calls[-1]
    assert add == "badge-digits-2" and "hidden" in remove
    eng.history = [entry(i) for i in range(1000)]
    nc.update_badge()
    assert badge.text == "999+" and badge.class_calls[-1][0] == "badge-digits-4"
    badge.is_deleted = True
    eng.history = []
    nc.update_badge()
    assert badge.text == "999+"


def test_mark_all_read(eng, monkeypatch):
    seen = []
    monkeypatch.setattr(nc, "update_badge", lambda: seen.append("badge"))
    monkeypatch.setattr(nc, "render", lambda: seen.append("render"))
    nc.mark_all_read()
    assert nc.S.last_read_ts == NOW_TS and seen == ["badge", "render"]


def test_set_filter_and_query(eng, monkeypatch):
    renders = []
    monkeypatch.setattr(nc, "render", lambda: renders.append(1))
    nc._set_filter("warning")
    nc._set_query("  HeLLo ")
    assert nc.S.level_filter == "warning" and nc.S.query == "hello"
    nc._set_query(None)
    assert nc.S.query == "" and len(renders) == 3


def test_render_without_listing_is_noop(eng):
    nc.render()
    nc.S.listing = SimpleNamespace(is_deleted=True)
    nc.render()


def test_run_and_copy(eng):
    eng.history = [entry("a", message="body")]
    nc._run("a", 2)
    nc._copy("a")
    nc._copy("missing")
    assert eng.calls == [("run", "a", 2), ("action", {"kind": "copy"}, "body", "a")]


def test_refresh_renders_and_updates_badge(eng, monkeypatch):
    seen = []
    monkeypatch.setattr(nc, "render", lambda: seen.append("render"))
    monkeypatch.setattr(nc, "update_badge", lambda: seen.append("badge"))
    nc._refresh()
    assert seen == ["render", "badge"]


# --------------------------------------------------------------------------- render / items in the UI

def _build_listing():
    nc.S.chips = ui.element("div")
    nc.S.new_pill = ui.label("")
    nc.S.foot_count = ui.label("")
    nc.S.listing = ui.element("div")
    nc.render()


def test_render_empty_history(eng):
    async def check(user):
        await user.should_see("No notifications yet")
        assert nc.S.foot_count.text == "Showing 0 of 0"
        assert nc.S.new_pill.visible is False

    run_ui(_build_listing, check)


def test_render_no_match(eng):
    eng.history = [entry(1)]
    nc.S.query = "zzz"

    async def check(user):
        await user.should_see("Nothing matches these filters")
        assert nc.S.foot_count.text == "Showing 0 of 1"

    run_ui(_build_listing, check)


def test_render_groups_chips_and_items(eng):
    eng.history = [
        entry("old", ago=3 * 86400, type="negative", title="Old error"),
        entry("y", ago=86400, type="warning", title="Yesterday warning", hint="Do <b>this</b>", count=3, muted=True,
              source="obs"),
        entry("t", ago=30, type="positive", title="", message="Only message"),
        entry("w", ago=20, level="ongoing", title="Working"),
    ]

    async def check(user):
        await user.should_see("Today")
        await user.should_see("Yesterday")
        await user.should_see("Earlier")
        await user.should_see("Only message")
        await user.should_see("Yesterday warning")
        await user.should_see("3 times")
        await user.should_see("Muted")
        await user.should_see("Do &lt;b&gt;this&lt;/b&gt;")
        chips = list(nc.S.chips.default_slot.children)
        assert len(chips) == 5
        assert "is-on" in chips[0].classes
        texts = [c.default_slot.children[0].content for c in chips]
        assert texts[0].endswith("All <b>4</b>")
        assert "Errors <b>1</b>" in texts[1] and "Warnings <b>1</b>" in texts[2]
        assert "Info <b>1</b>" in texts[3] and "Done <b>1</b>" in texts[4]
        assert nc.S.new_pill.text == "2 new" and nc.S.new_pill.visible
        assert nc.S.foot_count.text == "Showing 4 of 4"
        items = [c for c in nc.S.listing.default_slot.children if "ncx-item" in c.classes]
        classes = [" ".join(i.classes) for i in items]
        assert "is-info" in classes[0]  # ongoing shows as info, newest first
        assert "is-success" in classes[1] and "is-warning" in classes[2] and "is-error" in classes[3]
        assert any("ncx-spin" in getattr(e, "content", "") for e in items[0].descendants())
        unread = [any("ncx-unread" in d.classes for d in i.descendants()) for i in items]
        assert unread == [True, True, False, False]
        # chip click filters
        click(user, chips[1])
        assert nc.S.level_filter == "error"
        await user.should_see("Showing 1 of 4")
        await user.should_not_see("Only message")

    run_ui(_build_listing, check)


def test_render_caps_at_max_shown(eng, monkeypatch):
    monkeypatch.setattr(nc, "_MAX_SHOWN", 3)
    eng.history = [entry(i, ago=100 + i) for i in range(5)]

    async def check(user):
        assert nc.S.foot_count.text == "Showing 3 of 5"
        items = [c for c in nc.S.listing.default_slot.children if "ncx-item" in c.classes]
        assert len(items) == 3

    run_ui(_build_listing, check)


def test_item_actions_and_buttons(eng):
    eng.history = [entry("x", actions=[
        {"kind": "copy"},
        {"kind": "navigate", "main_tab": "Settings", "settings_subtab": "OBS"},
        {"kind": "call"},
        {"kind": "navigate", "main_tab": "Alerts", "label": "Go alerts"},
    ])]
    eng.call_actions["x"] = [{"kind": "call", "fn": lambda: None}]

    async def check(user):
        await user.should_see("Open OBS settings")
        await user.should_see("Go alerts")
        await user.should_see("Open")  # the call action (registered)
        user.find("Open OBS settings").click()
        user.find("Go alerts").click()
        user.find(kind=ui.button, content="Copy").click()
        user.find("Dismiss").click()
        item = next(c for c in nc.S.listing.default_slot.children if "ncx-item" in c.classes)
        assert "is-click" in item.classes
        click(user, item)
        assert eng.calls == [("run", "x", 1), ("run", "x", 3), ("action", {"kind": "copy"}, "Message x", "x"),
                             ("remove", "x"), ("run", "x", 1)]

    run_ui(_build_listing, check)


def test_unregistered_call_action_hidden_and_no_nav_click(eng):
    eng.history = [entry("y", actions=[{"kind": "call", "label": "Do it"}])]

    async def check(user):
        await user.should_not_see("Do it")
        item = next(c for c in nc.S.listing.default_slot.children if "ncx-item" in c.classes)
        assert "is-click" not in item.classes
        click(user, item)
        assert eng.calls == []

    run_ui(_build_listing, check)


# --------------------------------------------------------------------------- the bell + panel

def test_create_tray_button_full_flow(eng, monkeypatch):
    js = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, **kw: js.append(code))
    eng.history = [entry("a", ago=10, title="Hello there"), entry("b", ago=20, title="Other thing")]

    def build():
        nc.create_notification_tray_button()

    async def check(user):
        assert ("css", "mycelian-notify-center") in eng.calls and ("assets",) in eng.calls
        assert eng.refresh == [nc._refresh]
        assert ("help", "notifications") in eng.calls
        assert nc.S.badge.text == "2"
        dlg = nc.S.dialog
        assert dlg.value is False
        bell = next(b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "notifications")
        click(user, bell)
        assert dlg.value is True
        assert js[-1].endswith("suppress(true)")
        assert nc.S.last_read_ts == NOW_TS
        assert nc.S.badge.text == ""  # panel open -> nothing unread
        # search narrows the list
        search = user.find(kind=ui.input).elements.pop()
        search.value = "hello"
        await user.should_not_see("Other thing")
        assert nc.S.query == "hello"
        # mark all read link and clear-all
        click(user, next(iter(user.find("Mark all read").elements)))
        clear = next(b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "delete_sweep")
        click(user, clear)
        assert ("clear",) in eng.calls
        # bell again closes
        click(user, bell)
        assert dlg.value is False and js[-1].endswith("suppress(false)")
        # reopen, close via X
        click(user, bell)
        close_btn = next(b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "close"
                         and "Dismiss" not in str(b.props.get("label")))
        click(user, close_btn)
        assert dlg.value is False
        # notification settings link
        settings_link = next(e for e in user.find(content="Notification settings").elements)
        click(user, settings_link)
        assert eng.calls[-1] == ("nav", "App Settings", "Settings")

    run_ui(build, check)


def test_toggle_without_dialog_is_noop(eng, monkeypatch):
    monkeypatch.setattr(ui, "run_javascript", lambda code, **kw: None)

    def build():
        nc.create_notification_tray_button()

    async def check(user):
        bell = next(b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "notifications")
        nc.S.dialog = None
        click(user, bell)  # must not raise
        assert nc.S.dialog is None

    run_ui(build, check)


def test_exports():
    assert set(nc.__all__) == {"create_notification_tray_button", "render", "update_badge", "unread_count", "LEVEL_LABELS"}
    assert nc._MAX_SHOWN == 200 and nc._ICONS["success"] == "check_circle"

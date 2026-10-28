# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_polls.py: the pure helpers and the Polls tab
(overlay picker, options editor, live results, Twitch / timing / chat / ending cards and the
start / stop actions). A real in-memory PollManager (no saving, fake timers) backs the UI."""

from __future__ import annotations

import re
import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import poll_manager as pm  # noqa: E402
from modules import twitch_polls, web_engine  # noqa: E402
from modules.help_system import contextual_help, help_render  # noqa: E402
from modules.uiwindows import chatbot_gp_styles as gp  # noqa: E402
from modules.uiwindows import chatbot_polls as cp  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeTimer:
    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.daemon = False
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class FakeSchedule:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class Env(SimpleNamespace):
    pass


@pytest.fixture
def env(monkeypatch):
    e = Env(names=["alpha", "beta"], notes=[], schedules=[], shown={}, hidden=False, ready="ok",
            widget_count=1, opened=[], settings=[], created=[], ended=[])
    monkeypatch.setattr(pm, "threading", SimpleNamespace(Timer=FakeTimer, Thread=threading.Thread,
                                                         RLock=threading.RLock, Lock=threading.Lock,
                                                         Event=threading.Event))
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    manager = pm.PollManager(persist=False)
    e.manager = manager
    monkeypatch.setattr(cp, "get_poll_manager", lambda: manager)
    monkeypatch.setattr(cp, "list_poll_template_names", lambda: list(e.names))
    monkeypatch.setattr(cp, "poll_widget_info", lambda t: {"id": "w1" if e.widget_count else "", "count": e.widget_count,
                                                           "style": dict(pm._WIDGET_STYLE_DEFAULTS, title="Vote!")})
    monkeypatch.setattr(cp, "notify", lambda msg, **kw: e.notes.append((msg, kw)))

    def schedule(interval, callback):
        timer = FakeSchedule(interval, callback)
        e.schedules.append(timer)
        return timer

    monkeypatch.setattr(cp, "layout_schedule", schedule)
    monkeypatch.setattr(cp, "on_chatbot_subtab_shown", lambda tab, cb: e.shown.__setitem__(tab, cb))
    monkeypatch.setattr(cp, "chatbot_subtab_hidden", lambda tab: e.hidden)
    monkeypatch.setattr(twitch_polls, "readiness", lambda: e.ready)

    def create(title, choices, duration, points_enabled=False, points_per_vote=0):
        e.created.append((title, choices, duration))
        return True, {"id": "tp1", "choices": [{"id": f"c{i}", "title": c} for i, c in enumerate(choices)]}

    monkeypatch.setattr(twitch_polls, "create_poll", create)
    monkeypatch.setattr(twitch_polls, "end_poll", lambda poll_id: e.ended.append(poll_id))
    monkeypatch.setattr(cp, "_selected_template", "")
    monkeypatch.setattr(cp, "open_in_spore_studio", lambda t: e.opened.append(t))

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(cp, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(contextual_help, "navigate_to_settings_subtab",
                        lambda tab, main_tab=None: e.settings.append((tab, main_tab)))
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(gp.webbrowser, "open", lambda *a, **k: None)
    monkeypatch.setattr(gp, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(ui.clipboard, "write", lambda text: None)
    yield e
    manager.shutdown()


def add_options(env, template="alpha", names=("Yes", "No")):
    for n in names:
        env.manager.add_category(template, n)


def fire(user, element, event="click", args=None):
    UserInteraction(user, {element}, None).trigger(event, args)


def html_text(panel):
    out = []

    def walk(el):
        if isinstance(el, ui.html):
            out.append(el.content)
        for child in el.default_slot.children:
            walk(child)
        for slot in el.slots.values():
            if slot is el.default_slot:
                continue
            for child in slot.children:
                walk(child)

    walk(panel.root)
    return "\n".join(out)


def all_elements(panel, kind=ui.element):
    found = []

    def walk(el):
        if isinstance(el, kind):
            found.append(el)
        for slot in el.slots.values():
            for child in slot.children:
                walk(child)

    walk(panel.root)
    return found


def labels(panel):
    return [el.text for el in all_elements(panel, ui.label)]


def buttons(panel, text=None, icon_name=None):
    out = []
    for b in all_elements(panel, ui.button):
        if text is not None and b.text != text:
            continue
        if icon_name is not None and b.props.get("icon") != icon_name:
            continue
        out.append(b)
    return out


def switch(panel, label):
    for wrapper in all_elements(panel):
        if "gp-toggle" not in wrapper.classes:
            continue
        kids = wrapper.default_slot.children
        texts = [c.text for c in kids[1].default_slot.children if isinstance(c, ui.label)]
        if texts and texts[0] == label:
            return kids[0]
    raise AssertionError(f"no toggle {label!r}")


def seg_option(panel, label):
    for box in all_elements(panel):
        if "gp-seg" not in box.classes:
            continue
        for opt in box.default_slot.children:
            if any(getattr(c, "text", None) == label for c in opt.default_slot.children):
                return opt
    raise AssertionError(f"no seg option {label!r}")


def card_body(panel, title):
    for box in all_elements(panel):
        if "gp-card" in box.classes and box.props.get("data-card") == title:
            return box.default_slot.children[1]
    raise AssertionError(f"no card {title!r}")


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def mount(env, check, before=None):
    holder = {}

    def build():
        if before:
            before()
        panel = cp.PollsPanel()
        holder["panel"] = panel
        panel.build(ui.column())

    async def go(user):
        await check(user, holder["panel"])

    run_ui(build, go)
    return holder["panel"]


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize("seconds,expected", [(0, (0, 1)), (None, (0, 1)), (-5, (0, 1)), (120, (2, 60)),
                                              (3600, (1, 3600)), (90, (90, 1))])
def test_split_duration(seconds, expected):
    assert cp.split_duration(seconds) == expected


def test_leader_text():
    assert cp.leader_text({}) == "—"
    assert cp.leader_text({"options": [{"name": "A", "votes": 5}, {"name": "B", "votes": 2}]}) == "A"
    three = {"options": [{"name": n, "votes": 1} for n in "ABC"]}
    assert cp.leader_text(three) == "3-way tie"


def test_winners_text():
    assert cp.winners_text([]) == ""
    assert cp.winners_text(None) == ""
    assert cp.winners_text([{"name": ""}]) == ""
    assert cp.winners_text([{"name": "A", "votes": 2}, {"name": "B", "votes": 2}]) == "A & B (2 votes)"


def test_poll_variables():
    sample = [{"name": "Celeste", "keyword": "#celeste", "votes": 4}, {"name": "Hades", "keyword": "#hades", "votes": 4}]
    out = cp.poll_variables(sample)
    assert [v["key"] for v in out] == ["option", "options", "keyword", "votes"]
    assert out[0]["example"] == "Celeste, Hades" and out[2]["example"] == "#celeste" and out[3]["example"] == "4"
    empty = cp.poll_variables([])
    assert empty[2]["example"] == "" and empty[3]["example"] == "0"


def test_bars_html_variants():
    style = dict(pm._WIDGET_STYLE_DEFAULTS)
    assert "No options yet" in cp.bars_html({}, style, live=True)
    snap = {"options": [{"name": "A", "keyword": "#a", "votes": 4, "percent": 80},
                        {"name": "B", "keyword": "#b", "votes": 1, "percent": 20},
                        {"name": "Z", "keyword": "#z", "votes": 0, "percent": 0}]}
    live = cp.bars_html(snap, style, live=True)
    assert live.count("🏆") == 1 and "width:100.00%" in live and "width:25.00%" in live
    assert "<b>4</b> · 80%" in live
    ended = cp.bars_html(snap, style, live=False)
    assert "🏆" not in ended
    zero = cp.bars_html({"options": [{"name": "A", "votes": 0}]}, style, live=True)
    assert "width:0.00%" in zero and "🏆" not in zero


def test_widget_html_variants():
    style = dict(pm._WIDGET_STYLE_DEFAULTS, title="Pick <one>", bar_color_2="#000")
    snap = {"options": [{"name": f"O{i}", "keyword": f"#o{i}", "votes": i, "percent": i * 10, "winner": i == 6}
                        for i in range(7)]}
    out = cp.widget_html(snap, style)
    assert "Pick &lt;one&gt;" in out
    assert "O5" in out and "O6" not in out  # at most 6 rows
    assert "linear-gradient(90deg, #a855f7, #000)" in out and 'class="gp-widget-kw">#o1' in out
    plain = cp.widget_html({"options": [{"name": "A", "keyword": "#a", "votes": 1, "percent": 100, "winner": True}]},
                           dict(pm._WIDGET_STYLE_DEFAULTS, show_counts=False, show_percent=False, show_keywords=False))
    assert "gp-widget-kw" not in plain and pm._WIDGET_STYLE_DEFAULTS["winner_color"] in plain
    assert "<span></span></div>" in plain  # no numbers
    assert "gp-widget-title" not in plain


def test_warn_option(env):
    cp._warn_option(None)
    assert env.notes[-1] == ("", {"type": "warning", "source": "chatbot", "title": "Poll option not changed",
                                  "hint": "Change the value and try again."})


# --------------------------------------------------------------------------- empty state


def test_empty_state_and_check_again(env):
    env.names = []

    async def check(user, panel):
        await user.should_see("No overlay has a Poll widget yet")
        assert panel.template == ""
        user.find("Open Spore Studio").click()
        assert env.opened == [""]
        env.names = ["alpha"]
        user.find("Check again").click()
        await user.should_see("Start poll")
        assert panel.template == "alpha" and cp._selected_template == "alpha"

    mount(env, check)


def test_build_registers_tick_and_stops_old_timer(env):
    async def check(user, panel):
        assert env.shown["Polls"] == panel._tick
        assert env.schedules[-1].interval == 1.0 and env.schedules[-1].callback == panel._tick
        old = panel._timer
        with user.client:
            panel.build(ui.column())
        assert old.cancelled and panel._timer is env.schedules[-1] and panel._timer is not old

    mount(env, check)


def test_stop_timer_ignores_cancel_errors(env):
    panel = cp.PollsPanel()

    class Bad:
        def cancel(self):
            raise RuntimeError("gone")

    panel._timer = Bad()
    panel._stop_timer()
    assert panel._timer is None
    panel.refresh()  # no root: nothing happens
    panel._render()


# --------------------------------------------------------------------------- idle view


def test_idle_view_shows_cards_and_hero(env):
    add_options(env)
    env.manager.update_config("alpha", {"duration_seconds": 120})

    async def check(user, panel):
        await user.should_see("Start poll")
        for title in ("Options", "Overlay", "Twitch poll", "Timing & voting", "Start from chat", "When it ends"):
            await user.should_see(title)
        text = html_text(panel)
        assert "Not running" in text and "2 options · runs for 2 minutes" in text
        assert "<b>#Yes</b>" in text
        assert "Poll widget <b>w1</b> in the <b>alpha</b> template" in text
        assert panel.picker.options == {"alpha": "alpha", "beta": "beta"}
        assert panel._last_timer == 120

    mount(env, check)


def test_idle_hero_last_winner_and_no_options(env):
    env.manager.ensure_template("alpha")
    env.manager._templates["alpha"]["config"]["last_winners"] = [{"name": "Old", "votes": 3, "keyword": "#old"}]

    async def check(user, panel):
        text = html_text(panel)
        assert "Not running · last winner Old (3 votes)" in text
        assert "runs until you end it" in text and "<b>#option</b>" in text
        assert "A poll needs at least two options." in text

    mount(env, check)


def test_last_error_and_multiple_widgets(env):
    add_options(env)
    env.widget_count = 3
    env.manager._last_error["alpha"] = "Something <broke>"

    async def check(user, panel):
        text = html_text(panel)
        assert "Something &lt;broke&gt;" in text
        assert "(and 2 more)" in text

    mount(env, check)


def test_overlay_card_edit_opens_spore(env):
    add_options(env)

    async def check(user, panel):
        user.find("Edit in Spore Studio").click()
        assert env.opened == ["alpha"]

    mount(env, check)


def test_picker_switches_and_remembers(env):
    add_options(env)
    add_options(env, "beta", ("Red", "Blue"))

    async def check(user, panel):
        with user.client:
            panel.picker.value = "beta"
        assert panel.template == "beta" and cp._selected_template == "beta"
        assert "#Red" in html_text(panel)
        with user.client:
            panel._pick("beta")  # same: no change
            panel._pick("")
        assert panel.template == "beta"

    mount(env, check)


def test_selected_template_survives_rebuild(env, monkeypatch):
    monkeypatch.setattr(cp, "_selected_template", "beta")

    async def check(user, panel):
        assert panel.template == "beta"

    mount(env, check)


def test_rescan_updates_picker(env):
    add_options(env)

    async def check(user, panel):
        env.names = ["alpha", "beta", "gamma"]
        fire(user, panel.picker, "popupShow")
        assert panel.names == ["alpha", "beta", "gamma"]
        assert list(panel.picker.options) == ["alpha", "beta", "gamma"]
        env.names = ["beta"]  # current template gone: leave the picker alone
        with user.client:
            panel._rescan()
        assert panel.names == ["alpha", "beta", "gamma"]

    mount(env, check)


# --------------------------------------------------------------------------- options editor


def _option_inputs(panel):
    rows = [el for el in all_elements(panel) if "gp-opt" in el.classes and "is-head" not in el.classes
            and isinstance(el, ui.element) and not isinstance(el, ui.html)]
    out = []
    for row in rows:
        inputs = [c for c in row.default_slot.children if isinstance(c, ui.input)]
        number = [c for c in row.default_slot.children if isinstance(c, ui.number)]
        btns = [c for c in row.default_slot.children if isinstance(c, ui.button)]
        out.append(SimpleNamespace(row=row, name=inputs[0], keyword=inputs[1], votes=number[0], buttons=btns))
    return out


def test_option_name_keyword_follow_and_save(env):
    add_options(env)

    async def check(user, panel):
        rows = _option_inputs(panel)
        assert [r.name.value for r in rows] == ["Yes", "No"]
        first = rows[0]
        assert first.buttons[0].enabled is False and first.buttons[1].enabled is True
        assert rows[1].buttons[1].enabled is False
        with user.client:
            first.name.value = "Yes Please"
        assert first.keyword.value == "#YesPlease"  # keyword follows the name
        fire(user, first.name, "blur")
        cat = env.manager.get_config("alpha")["categories"][0]
        assert cat["name"] == "Yes Please" and cat["keyword"] == "#YesPlease"
        # a custom keyword stops following
        with user.client:
            first.keyword.value = "#y"
            first.name.value = "Yep"
        assert first.keyword.value == "#y"
        fire(user, first.keyword, "keydown.enter")
        cat = env.manager.get_config("alpha")["categories"][0]
        assert (cat["name"], cat["keyword"]) == ("Yep", "#y")

    mount(env, check)


def test_option_save_rejected_warns_and_restores(env):
    add_options(env)

    async def check(user, panel):
        rows = _option_inputs(panel)
        with user.client:
            rows[0].keyword.value = "#No"
        fire(user, rows[0].keyword, "blur")
        assert env.notes[-1][0] == "#No is already used by another option."
        assert rows[0].keyword.value == "#Yes"

    mount(env, check)


def test_option_save_when_option_vanished(env):
    add_options(env)

    async def check(user, panel):
        rows = _option_inputs(panel)
        cid = env.manager.get_config("alpha")["categories"][0]["id"]
        env.manager.remove_category("alpha", cid)
        fire(user, rows[0].name, "blur")
        assert env.notes[-1][0] == "Option not found."

    mount(env, check)


def test_option_votes_edit(env):
    add_options(env)

    async def check(user, panel):
        rows = _option_inputs(panel)
        with user.client:
            rows[1].votes.value = 7
        fire(user, rows[1].votes, "blur")
        assert env.manager.get_config("alpha")["categories"][1]["votes"] == 7
        assert "7 · 100%" in html_text(panel)  # the overlay preview refreshed
        # refused while open
        env.manager._templates["alpha"]["runtime"]["status"] = "open"
        with user.client:
            rows[1].votes.value = 3
        fire(user, rows[1].votes, "blur")
        assert env.notes[-1][0] == "End the poll before editing votes."

    mount(env, check)


def test_move_remove_and_add_options(env):
    add_options(env, names=("A", "B", "C"))

    async def check(user, panel):
        rows = _option_inputs(panel)
        UserInteraction(user, {rows[0].buttons[1]}, None).click()
        assert [c["name"] for c in env.manager.get_config("alpha")["categories"]] == ["B", "A", "C"]
        rows = _option_inputs(panel)
        UserInteraction(user, {rows[2].buttons[2]}, None).click()
        assert [c["name"] for c in env.manager.get_config("alpha")["categories"]] == ["B", "A"]
        add = [el for el in all_elements(panel) if "gp-add" in el.classes][0]
        fire(user, add)
        assert [c["name"] for c in env.manager.get_config("alpha")["categories"]] == ["B", "A", "Option 3"]

    mount(env, check)


def test_option_actions_refused_warn(env, monkeypatch):
    add_options(env)

    async def check(user, panel):
        monkeypatch.setattr(env.manager, "move_category", lambda *a: (False, "nope move"))
        monkeypatch.setattr(env.manager, "remove_category", lambda *a: (False, "nope remove"))
        monkeypatch.setattr(env.manager, "add_category", lambda *a: (False, "nope add"))
        with user.client:
            panel._move("x", 1)
            panel._remove("x")
            panel._add_option()
        assert [n[0] for n in env.notes[-3:]] == ["nope move", "nope remove", "nope add"]
        monkeypatch.setattr(env.manager, "move_category", lambda *a: (False, ""))
        n = len(env.notes)
        with user.client:
            panel._move("x", 1)
        assert len(env.notes) == n

    mount(env, check)


def test_twitch_options_over_limit_and_full(env):
    add_options(env, names=[f"O{i}" for i in range(6)])
    env.manager._templates["alpha"]["config"]["twitch_poll"] = True

    async def check(user, panel):
        text = html_text(panel)
        assert "Remove 1 option (marked in red)" in text
        assert "Keywords aren't used while it runs on Twitch" in text
        rows = _option_inputs(panel)
        assert "is-over" in rows[5].row.classes and "is-over" not in rows[4].row.classes
        assert str(rows[0].name.props.get("maxlength")) == str(twitch_polls.CHOICE_TITLE_MAX)
        add = [el for el in all_elements(panel) if "gp-add" in el.classes][0]
        assert "is-disabled" in add.classes
        fire(user, add)
        assert len(env.manager.get_config("alpha")["categories"]) == 6

    mount(env, check)


# --------------------------------------------------------------------------- timing card


def test_timing_mode_switch_and_duration(env):
    add_options(env)
    env.manager.update_config("alpha", {"duration_seconds": 120})

    async def check(user, panel):
        assert panel.timer_row.visible is True
        assert (panel.amount.value, panel.unit.value) == (2, 60)
        fire(user, seg_option(panel, "Until I stop it"))
        assert env.manager.get_config("alpha")["duration_seconds"] == 0
        assert panel.timer_row.visible is False and panel._last_timer == 120
        fire(user, seg_option(panel, "Timer"))
        assert env.manager.get_config("alpha")["duration_seconds"] == 120
        assert panel.timer_row.visible is True
        with user.client:
            panel.amount.value = 3
        assert env.manager.get_config("alpha")["duration_seconds"] == 180
        with user.client:
            panel.unit.value = 3600
        assert env.manager.get_config("alpha")["duration_seconds"] == 3 * 3600
        with user.client:
            panel.amount.value = None
        assert env.manager.get_config("alpha")["duration_seconds"] == 3600
        with user.client:
            panel.amount.value = 10 ** 9
        assert env.manager.get_config("alpha")["duration_seconds"] == 86400 * 7

    mount(env, check)


def test_set_mode_open_when_already_open(env):
    add_options(env)

    async def check(user, panel):
        panel._last_timer = 99
        with user.client:
            panel._set_mode("open")
        assert panel._last_timer == 99
        panel.timer_row = None
        with user.client:
            panel._set_mode("open")

    mount(env, check)


def test_save_duration_guards(env):
    panel = cp.PollsPanel()
    panel._save_duration()  # no inputs yet: nothing
    panel.amount = SimpleNamespace(value="bad")
    panel.unit = SimpleNamespace(value=None)
    panel.template = "alpha"
    env.manager.ensure_template("alpha")
    panel._status = "open"
    panel._save_duration()
    assert env.manager.get_config("alpha")["duration_seconds"] == 60


def test_twitch_duration_is_clamped(env):
    add_options(env)
    env.manager.update_config("alpha", {"twitch_poll": True, "twitch_title": "Q?", "duration_seconds": 7200})

    async def check(user, panel):
        # Twitch polls max out at 30 minutes; the stored value was already clamped.
        assert (panel.amount.value, panel.unit.value) == (30, 60)
        assert list(panel.unit.options) == [1, 60]
        assert "Twitch polls run 15 seconds to 30 minutes." in html_text(panel)
        with user.client:
            panel.amount.value = 45
        assert env.manager.get_config("alpha")["duration_seconds"] == 1800
        assert (panel.amount.value, panel.unit.value) == (30, 60)
        with user.client:
            panel.unit.value = 1
            panel.amount.value = 5
        assert env.manager.get_config("alpha")["duration_seconds"] == 15
        assert (panel.amount.value, panel.unit.value) == (15, 1)
        seg_texts = [c.text for c in all_elements(panel, ui.label)]
        assert "Until I stop it" not in seg_texts

    mount(env, check)


def test_twitch_hour_timer_falls_back_to_minutes(env, monkeypatch):
    add_options(env)
    monkeypatch.setattr(twitch_polls, "clamp_duration", lambda s: 3600)
    env.manager._templates["alpha"]["config"].update(twitch_poll=True, duration_seconds=3600)

    async def check(user, panel):
        assert (panel.amount.value, panel.unit.value) == (60, 60)
        with user.client:
            panel.amount.value = 2
        assert (panel.amount.value, panel.unit.value) == (3600, 1)

    mount(env, check)


def test_vote_toggles(env):
    add_options(env)

    async def check(user, panel):
        sw = switch(panel, "Viewers can change their vote")
        assert sw.enabled
        with user.client:
            sw.value = True
        assert env.manager.get_config("alpha")["allow_vote_switch"] is True
        with user.client:
            switch(panel, "One vote per viewer").value = False
        assert env.manager.get_config("alpha")["one_entry_per_user"] is False
        assert switch(panel, "Viewers can change their vote").enabled is False

    mount(env, check)


# --------------------------------------------------------------------------- twitch card


@pytest.mark.parametrize(
    "ready,enabled,expected",
    [
        ("missing", False, "sign in to Twitch again"),
        ("not_eligible", False, "partners and affiliates run polls"),
        ("offline", True, "Twitch isn't connected"),
        ("unknown", True, "Checking your Twitch sign-in"),
        ("ok", True, "Your Twitch sign-in can create polls."),
    ],
)
def test_twitch_card_states(env, ready, enabled, expected):
    add_options(env)
    env.ready = ready
    if enabled:
        env.manager.update_config("alpha", {"twitch_poll": True})

    async def check(user, panel):
        assert expected in html_text(panel)
        blocked = ready in ("missing", "not_eligible")
        assert switch(panel, "Also run it on Twitch").enabled is (not blocked or enabled)

    mount(env, check)


def test_twitch_settings_button(env, monkeypatch):
    add_options(env)
    env.ready = "missing"

    async def check(user, panel):
        user.find("Open Twitch settings").click()
        assert env.settings == [("Twitch", "Settings")]

        def boom(*_a, **_k):
            raise RuntimeError("no nav")

        monkeypatch.setattr(contextual_help, "navigate_to_settings_subtab", boom)
        with user.client:
            panel._open_twitch_settings()  # swallowed

    mount(env, check)


def test_enable_twitch_warns_and_shortens(env):
    env.manager.add_category("alpha", "A" * 30)
    env.manager.add_category("alpha", "B")
    env.manager.add_category("beta", "x")
    env.manager._templates["beta"]["runtime"] = {**pm._idle_runtime(), "status": "open", "twitch_poll_id": "z"}

    async def check(user, panel):
        env.ready = "missing"
        with user.client:
            panel._set_twitch(True)
        titles = [n[1].get("title") for n in env.notes]
        assert titles == ["Sign in to Twitch again to use Twitch polls", "Only one Twitch poll at a time",
                          "Option names shortened for Twitch"]
        shortened = env.notes[-1]
        assert "1 name was shortened" in shortened[0] and shortened[1]["details"] == "A" * 30
        # turning it on saved the shortened name
        assert env.manager.get_config("alpha")["categories"][0]["name"] == "A" * 25
        with user.client:
            panel._set_twitch(False)
        assert env.manager.get_config("alpha")["twitch_poll"] is False
        for ready in ("offline", "not_eligible"):
            env.ready = ready
            env.notes.clear()
            with user.client:
                panel._set_twitch(True)
            assert env.notes[0][1]["title"] == "Twitch poll can't run yet"
            assert env.notes[0][1]["dedupe_key"] == f"twitch:poll_{ready}_toggle"
            assert len(env.notes) == 2

    mount(env, check)


def test_enable_twitch_plural_shortened(env):
    env.manager.add_category("alpha", "A" * 30)
    env.manager.add_category("alpha", "B" * 30)

    async def check(user, panel):
        with user.client:
            panel._set_twitch(True)
        assert env.notes == [(f"Twitch option names can be at most {twitch_polls.CHOICE_TITLE_MAX} characters, so "
                              "2 names were shortened.", {"type": "info", "source": "chatbot",
                                                          "title": "Option names shortened for Twitch",
                                                          "details": "A" * 30 + "\n" + "B" * 30})]

    mount(env, check)


def test_enable_twitch_via_toggle_and_question(env):
    add_options(env)

    async def check(user, panel):
        with user.client:
            switch(panel, "Also run it on Twitch").value = True
        cfg = env.manager.get_config("alpha")
        assert cfg["twitch_poll"] is True and cfg["duration_seconds"] == 180
        text = html_text(panel)
        assert "Give the Twitch poll a question." in text
        title = [i for i in all_elements(panel, ui.input) if i.props.get("placeholder") == "What should we play next?"][0]
        with user.client:
            title.value = "  Next game?  "
        fire(user, title, "blur")
        assert env.manager.get_config("alpha")["twitch_title"] == "Next game?"
        fire(user, title, "keydown.enter")  # unchanged: no save needed
        with user.client:
            switch(panel, "Channel Points voting").value = True
        assert env.manager.get_config("alpha")["twitch_points_enabled"] is True
        points = [n for n in all_elements(panel, ui.number) if n.value == 100]
        with user.client:
            points[0].value = 0
        assert env.manager.get_config("alpha")["twitch_points_per_vote"] == twitch_polls.POINTS_MIN

    mount(env, check)


# --------------------------------------------------------------------------- chat + ends cards


def test_chat_command_normalized_and_saved(env):
    add_options(env)

    async def check(user, panel):
        with user.client:
            switch(panel, "Start it with a chat command").value = True
        assert env.manager.get_config("alpha")["mods_can_start"] is True
        cmd = [i for i in all_elements(panel, ui.input) if i.props.get("placeholder") == "!startpoll"][0]
        with user.client:
            cmd.value = "start poll"
        fire(user, cmd, "blur")
        assert cmd.value == "!startpoll"
        assert env.manager.get_config("alpha")["start_command"] == "!startpoll"
        fire(user, cmd, "keydown.enter")
        assert env.manager.get_config("alpha")["start_command"] == "!startpoll"

    mount(env, check)


def test_ends_card_message_preview_and_banner(env):
    add_options(env)

    async def check(user, panel):
        await user.should_see("Poll winner: Yes (1)")
        with user.client:
            switch(panel, "Show the winner on the chat banner").value = True
            switch(panel, "Post the winner in chat").value = True
        cfg = env.manager.get_config("alpha")
        assert cfg["announce_winners_as_banner"] and cfg["announce_winners_in_chat"]
        area = all_elements(panel, ui.textarea)[0]
        with user.client:
            area.value = "GG {option} with {votes}"
        assert panel.preview.text == "GG Yes with 1"
        assert env.manager.get_config("alpha")["banner_message"] == "GG {option} with {votes}"
        with user.client:
            area.value = "   "
        assert env.manager.get_config("alpha")["banner_message"] == "GG {option} with {votes}"
        fire(user, area, "blur")
        assert area.value == pm.DEFAULT_BANNER_MESSAGE
        fire(user, area, "blur")  # not empty: unchanged
        banner = [n for n in all_elements(panel, ui.number) if n.value == 10][0]
        with user.client:
            banner.value = 0
        assert env.manager.get_config("alpha")["banner_duration"] == 0.0

    mount(env, check)


def test_ends_card_sample_without_options(env):
    env.manager.ensure_template("alpha")

    async def check(user, panel):
        await user.should_see("Poll winner: Hollow Knight (41)")
        panel.preview = None
        area = all_elements(panel, ui.textarea)[0]
        with user.client:
            area.value = "x {option}"
        assert env.manager.get_config("alpha")["banner_message"] == "x {option}"

    mount(env, check)


# --------------------------------------------------------------------------- start / stop / live


def test_start_failure_and_success_then_live_view(env):
    env.manager.add_category("alpha", "Only")

    async def check(user, panel):
        user.find("Start poll").click()
        await settle()
        assert env.notes[-1][0] == "Add at least two options before starting."
        assert env.notes[-1][1]["title"] == "Can't start the poll yet"
        env.manager.add_category("alpha", "Other")
        with user.client:
            panel._render()
        user.find("Start poll").click()
        await settle()
        await user.should_see("End poll now")
        assert env.notes[-1] == ("Poll started.", {"type": "success", "source": "chatbot"})
        text = html_text(panel)
        assert "Poll open" in text and "Open until you end it · 0 votes" in text
        assert "Live results" in labels(panel)
        assert "Sorted like the overlay" in text
        assert panel.picker.options["alpha"] == "alpha · live"

    mount(env, check)


def test_start_button_loading_props(env):
    add_options(env)

    async def check(user, panel):
        button = buttons(panel, "Start poll")[0]
        await panel._start(SimpleNamespace(sender=button))
        assert "loading" not in button.props
        assert env.manager.get_overlay_state("alpha")["status"] == "open"

    mount(env, check)


def test_start_twitch_conflict_message(env):
    add_options(env)
    add_options(env, "beta")
    env.manager.update_config("alpha", {"twitch_poll": True, "twitch_title": "Q?"})
    env.manager._templates["beta"]["runtime"] = {**pm._idle_runtime(), "status": "open", "twitch_poll_id": "z"}

    async def check(user, panel):
        user.find("Start poll").click()
        await settle()
        assert env.notes[-1][1]["title"] == "Only one Twitch poll at a time"

    mount(env, check)


def test_live_view_ticks_and_stop(env):
    add_options(env)
    env.manager.update_config("alpha", {"duration_seconds": 60})
    env.manager.start_poll("alpha")

    async def check(user, panel):
        text = html_text(panel)
        # The countdown may already read 0:59 on a slow run.
        assert re.search(r"(1:00|0:5\d) left · 0 votes", text) and "gp-timerbar" in text
        assert "Timing changes apply the next time you start." in text
        env.manager.try_register_vote({"username": "u", "userid": "1", "message": "#Yes"})
        with user.client:
            panel._tick()
        text = html_text(panel)
        assert "1 vote" in text and "🏆" in text
        assert ">Yes</b><span>Leading" in text
        # unchanged tick keeps the same markup (no re-render)
        seen = dict(panel._seen)
        with user.client:
            panel._tick()
        assert panel._seen == seen
        user.find("End poll now").click()
        await user.should_see("Start poll")
        assert env.notes[-1] == ("Winner: Yes", {"type": "success", "source": "chatbot"})
        assert "Ended · winner Yes (1 vote)" in html_text(panel)
        with user.client:
            panel._stop()
        assert env.notes[-1] == ("Poll is not running.", {"type": "info", "source": "chatbot"})

    mount(env, check)


def test_tick_rerenders_on_status_change_and_live_marks(env):
    add_options(env)
    add_options(env, "beta")

    async def check(user, panel):
        env.manager.start_poll("beta")
        with user.client:
            panel._tick()
        assert panel.picker.options["beta"] == "beta · live"
        env.manager.start_poll("alpha")
        with user.client:
            panel._tick()
        assert panel._status == "open"
        await user.should_see("End poll now")

    mount(env, check)


def test_tick_rerenders_when_twitch_readiness_changes(env):
    add_options(env)
    env.ready = "missing"

    async def check(user, panel):
        assert "sign in to Twitch again" in html_text(panel)
        env.ready = "ok"
        with user.client:
            panel._tick()
        assert "sign in to Twitch again" not in html_text(panel)
        assert panel._twitch_status == "ok"

    mount(env, check)


def test_tick_skips_when_hidden_or_gone(env):
    add_options(env)

    async def check(user, panel):
        env.hidden = True
        env.manager.start_poll("alpha")
        with user.client:
            panel._tick()
        assert panel._status == "idle"  # hidden: no refresh
        env.hidden = False
        panel.template = ""
        with user.client:
            panel._tick()
        timer = panel._timer
        with user.client:
            panel.root.delete()
        panel._tick()
        assert timer.cancelled and panel._timer is None

    mount(env, check)


def test_set_html_skips_deleted_and_duplicates(env):
    panel = cp.PollsPanel()
    panel._set_html(None, "k", "x")
    gone = SimpleNamespace(is_deleted=True)
    panel._set_html(gone, "k", "x")
    assert panel._seen == {}
    calls = []
    el = SimpleNamespace(is_deleted=False, set_content=calls.append)
    panel._set_html(el, "k", "x")
    panel._set_html(el, "k", "x")
    assert calls == ["x"]


def test_twitch_live_view_and_hero(env):
    add_options(env)
    env.manager.update_config("alpha", {"twitch_poll": True, "twitch_title": "Q?", "duration_seconds": 60})
    env.manager.start_poll("alpha")

    async def check(user, panel):
        text = html_text(panel)
        assert "Poll open · on Twitch" in text
        assert "Votes come from the Twitch poll." in text
        assert switch(panel, "Also run it on Twitch").enabled is False
        title = [i for i in all_elements(panel, ui.input) if i.props.get("placeholder") == "What should we play next?"][0]
        assert title.enabled is False
        assert "Give the Twitch poll a question." not in text  # no problems listed while live

    mount(env, check)


def test_ended_no_votes_hero(env):
    add_options(env)
    env.manager.start_poll("alpha")
    env.manager.stop_poll("alpha")

    async def check(user, panel):
        assert "Ended · no votes" in html_text(panel)

    mount(env, check)


def test_save_while_open_skips_hero_update(env):
    add_options(env)

    async def check(user, panel):
        panel._status = "open"
        before = dict(panel._seen)
        with user.client:
            panel._save(allow_vote_switch=True)
        assert panel._seen == before
        assert env.manager.get_config("alpha")["allow_vote_switch"] is True

    mount(env, check)

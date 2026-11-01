# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_predictions.py: helpers and the Predictions tab (overlay picker,
question and outcomes, live bets with winner buttons, side cards and the start / lock / resolve /
cancel actions). A real in-memory PredictionManager with fake Twitch calls backs the UI."""

from __future__ import annotations

import asyncio
import sys
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

from modules import prediction_manager as pm  # noqa: E402
from modules import twitch_predictions as tp  # noqa: E402
from modules.help_system import contextual_help, help_render  # noqa: E402
from modules.uiwindows import chatbot_gp_styles as gp  # noqa: E402
from modules.uiwindows import chatbot_predictions as cpr  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeSchedule:
    def __init__(self, interval, callback):
        self.interval, self.callback, self.cancelled = interval, callback, False

    def cancel(self):
        self.cancelled = True


ROW = {"id": "p1", "title": "Win?", "status": "ACTIVE", "created_at": "2026-01-01T00:00:00Z", "prediction_window": 120,
       "outcomes": [{"id": "t1", "title": "Yes", "color": "BLUE"}, {"id": "t2", "title": "No", "color": "PINK"}]}


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(names=["alpha", "beta"], notes=[], schedules=[], shown={}, hidden=False, ready="ok",
                        opened=[], settings=[], ends=[], create=(True, dict(ROW)), end=(True, ""), current=None)
    manager = pm.PredictionManager(persist=False)
    monkeypatch.setattr(pm.PredictionManager, "_emit", lambda self, ev, d: None)
    monkeypatch.setattr(pm.PredictionManager, "_dispatch", lambda self, k, d: None)
    monkeypatch.setattr(pm.PredictionManager, "_announce", lambda self, t, s: None)
    e.manager = manager
    monkeypatch.setattr(cpr, "get_prediction_manager", lambda: manager)
    monkeypatch.setattr(pm, "list_prediction_template_names", lambda: list(e.names))
    monkeypatch.setattr(cpr, "list_prediction_template_names", lambda: list(e.names))
    monkeypatch.setattr(cpr, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(tp, "readiness", lambda: e.ready)
    monkeypatch.setattr(tp, "create_prediction", lambda title, outcomes, window: e.create)
    monkeypatch.setattr(tp, "end_prediction", lambda pid, status, winner=None: e.ends.append((status, winner)) or e.end)
    monkeypatch.setattr(tp, "get_current_prediction", lambda: e.current)

    def schedule(interval, callback):
        timer = FakeSchedule(interval, callback)
        e.schedules.append(timer)
        return timer

    monkeypatch.setattr(cpr, "layout_schedule", schedule)
    monkeypatch.setattr(cpr, "on_chatbot_subtab_shown", lambda tab, cb: e.shown.__setitem__(tab, cb))
    monkeypatch.setattr(cpr, "chatbot_subtab_hidden", lambda tab: e.hidden)
    monkeypatch.setattr(cpr, "_selected_template", "")
    monkeypatch.setattr(cpr, "open_in_spore_studio", lambda t: e.opened.append(t))

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(cpr, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(contextual_help, "navigate_to_settings_subtab", lambda tab, main_tab=None: e.settings.append((tab, main_tab)))
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(gp.webbrowser, "open", lambda *a, **k: None)
    monkeypatch.setattr(gp, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(ui.clipboard, "write", lambda text: None)
    return e


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def mount(env, check):
    holder = {}

    def build():
        panel = cpr.PredictionsPanel()
        holder["panel"] = panel
        panel.build(ui.column())

    async def go(user):
        await settle()
        await check(user, holder["panel"])

    run_ui(build, go)
    return holder["panel"]


def html_text(panel):
    out = []

    def walk(el):
        if isinstance(el, ui.html):
            out.append(el.content)
        for slot in el.slots.values():
            for child in slot.children:
                walk(child)

    walk(panel.root)
    return "\n".join(out)


def button(user, text):
    matches = [b for b in user.find(kind=ui.button).elements if b.text == text]
    assert matches, f"no button {text!r}"
    return matches[0]


def click(user, element):
    UserInteraction(user, {element}, None).click()


# --------------------------------------------------------------------------- helpers


def test_colours_samples_and_html():
    assert cpr.outcome_color({"color": "pink"}, 0) == cpr.OUTCOME_COLORS["pink"]
    assert cpr.outcome_color({}, 3) == cpr.OUTCOME_COLORS["pink"]
    sample = cpr.sample_snapshot({"title": "", "outcomes": [{"name": "A"}, {"name": "B"}]}, {})
    assert pm.format_message("{winner} {top}", sample) == "A MushroomFan"
    live = {"total_points": 10, "outcomes": [{"name": "A", "points": 2}, {"name": "B", "points": 8}], "title": "T"}
    assert [o["winner"] for o in cpr.sample_snapshot({}, live)["outcomes"]] == [False, True]
    keys = [v["key"] for v in cpr.prediction_variables(sample)]
    assert keys == ["title", "winner", "points", "winners", "users", "top"]
    assert "No outcomes" in cpr.outcomes_html({})
    html = cpr.outcomes_html({"outcomes": [{"name": "<A>", "points": 1500, "users": 2, "ratio": 1.5, "percent": 60, "winner": True,
                                            "top_predictors": [{"name": "Ann", "points_used": 900}]},
                                           {"name": "B", "points": 0, "users": 0, "ratio": None, "percent": 0}]})
    assert "&lt;A&gt;" in html and "1:1.5" in html and "top Ann (900)" in html and "🏆" in html and "—" in html
    assert "Your question" in cpr.widget_html({}) and "Yes" in cpr.widget_html({"title": "Q", "outcomes": [{"name": "Yes", "percent": 5}]})


# --------------------------------------------------------------------------- the tab


def test_empty_state(env):
    env.names = []

    async def check(user, panel):
        await user.should_see("No overlay has a Prediction widget yet")
        click(user, button(user, "Open Spore Studio"))
        assert env.opened == ["predictions"]
        env.names = ["alpha"]
        click(user, button(user, "Check again"))
        await user.should_see("Question & outcomes")

    mount(env, check)


def test_idle_setup_editing_and_picker(env):
    async def check(user, panel):
        await user.should_see("Not running")
        await user.should_see("Give the prediction a question.")
        assert env.shown["Predictions"] == panel._tick and env.schedules[-1].interval == 1.0
        inputs = user.find(kind=ui.input).elements
        title = next(i for i in inputs if str(i.props.get("maxlength")) == str(tp.TITLE_MAX))
        title.value = "Will we win?"
        UserInteraction(user, {title}, None).trigger("blur")
        assert env.manager.get_config("alpha")["title"] == "Will we win?"
        outcome = next(i for i in user.find(kind=ui.input).elements if i.value == "Yes")
        outcome.value = "Totally"
        UserInteraction(user, {outcome}, None).trigger("blur")
        assert env.manager.get_config("alpha")["outcomes"][0]["name"] == "Totally"
        add = next(el for el in panel.root.descendants() if "gp-add" in el.classes)
        click(user, add)
        assert len(env.manager.get_config("alpha")["outcomes"]) == 3
        remove = [b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "close"]
        click(user, remove[-1])
        assert len(env.manager.get_config("alpha")["outcomes"]) == 2
        panel._pick("beta")
        assert panel.template == "beta" and cpr._selected_template == "beta"
        panel._pick("beta")  # same: nothing to do

    mount(env, check)


def test_start_lock_resolve_and_tick(env):
    env.manager.update_config("alpha", title="Win?")

    async def check(user, panel):
        await user.should_see("Start prediction")
        click(user, button(user, "Start prediction"))
        await settle()
        assert env.notes[-1] == ("Prediction started.", {"type": "success", "source": "chatbot"})
        await user.should_see("Taking bets")
        await user.should_see("Live bets")
        env.manager.apply_twitch_update({**tp.normalize(ROW), "outcomes": [
            {"id": "t1", "name": "Yes", "color": "blue", "users": 2, "points": 300, "top_predictors": []},
            {"id": "t2", "name": "No", "color": "pink", "users": 1, "points": 100, "top_predictors": []}]}, kind="progress")
        panel._tick()
        assert "300" in html_text(panel)
        click(user, button(user, "Lock bets"))
        await settle()
        assert env.ends[-1] == ("LOCKED", None)
        await user.should_see("Bets locked")
        click(user, button(user, "No won"))
        await settle()
        assert env.ends[-1] == ("RESOLVED", "t2") and env.notes[-1][0] == "Paying out to “No”."
        env.manager.apply_twitch_update({**tp.normalize(ROW), "status": "resolved", "winning_outcome_id": "t2", "ended_at": 5.0},
                                        kind="end")
        panel._tick()
        await user.should_see("Ended · No won")
        await user.should_see("Last prediction")
        env.hidden = True
        panel._tick()  # hidden: nothing

    mount(env, check)


def test_failures_and_cancel(env):
    env.manager.update_config("alpha", title="Win?")

    async def check(user, panel):
        env.create = (False, "Twitch said: nope")
        click(user, button(user, "Start prediction"))
        await settle()
        assert env.notes[-1][1]["title"] == "Can't start the prediction yet"
        await user.should_see("Twitch said: nope")
        env.create = (True, dict(ROW))
        click(user, button(user, "Start prediction"))
        await settle()
        env.end = (False, "Twitch said: no")
        click(user, button(user, "Lock bets"))
        await settle()
        assert env.notes[-1][1]["title"] == "Couldn't lock the bets"
        click(user, button(user, "Yes won"))
        await settle()
        assert env.notes[-1][1]["title"] == "Couldn't pick the winner"
        env.end = (True, "")
        click(user, button(user, "Cancel & refund"))
        await settle()
        assert env.ends[-1] == ("CANCELED", None)

    mount(env, check)


@pytest.mark.parametrize("ready,text", [
    ("missing", "Open Twitch settings"),
    ("not_eligible", "partners and affiliates"),
    ("offline", "connected, so the prediction"),
    ("unknown", "Checking your Twitch sign-in"),
    ("ok", "can run predictions"),
])
def test_twitch_card_states(env, ready, text):
    env.ready = ready

    async def check(user, panel):
        await user.should_see(text)
        if ready == "missing":
            click(user, button(user, "Open Twitch settings"))
            assert env.settings == [("Twitch", "Settings")]
        env.ready = "ok"
        panel._tick()  # readiness changed: card rebuilt
        await user.should_see("can run predictions")

    mount(env, check)


def test_timing_and_ending_cards_save(env):
    async def check(user, panel):
        numbers = user.find(kind=ui.number).elements
        window = next(n for n in numbers if n.value == 2)  # 120 s shown as 2 minutes
        window.value = 45  # minutes: clamped to Twitch's 30
        assert env.manager.get_config("alpha")["window_seconds"] == 1800 and window.value == 30
        banner = next(n for n in user.find(kind=ui.number).elements if n.value == 10)
        banner.value = 20
        assert env.manager.get_config("alpha")["banner_duration"] == 20
        boxes = sorted(user.find(kind=ui.textarea).elements, key=lambda b: b.id)
        boxes[0].value = "Winner: {winner}"
        assert env.manager.get_config("alpha")["result_message"] == "Winner: {winner}"
        assert panel.preview.text.startswith("Winner: ")
        boxes[1].value = "Refunded!"
        assert env.manager.get_config("alpha")["refund_message"] == "Refunded!"
        switches = sorted(user.find(kind=ui.switch).elements, key=lambda b: b.id)
        switches[0].value = True
        assert env.manager.get_config("alpha")["announce_banner"] is True

    mount(env, check)


def test_a_dashboard_prediction_takes_over_the_tab(env):
    env.current = dict(ROW)

    async def check(user, panel):
        # The sync at build time picked up the running prediction for the first overlay.
        assert env.manager.live_template() == "alpha"
        panel._pick("beta")
        panel._tick()
        assert panel.template == "alpha"

    mount(env, check)


def test_stop_timer_survives_cancel_errors(env):
    panel = cpr.PredictionsPanel()

    class Bad:
        def cancel(self):
            raise RuntimeError("x")

    panel._timer = Bad()
    panel._stop_timer()
    assert panel._timer is None
    panel._tick()  # no root yet: stops quietly

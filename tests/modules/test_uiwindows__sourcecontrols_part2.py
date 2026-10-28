# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/sourcecontrols.py UI: the tab, template cards, every control type, dialogs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import chat_banner  # noqa: E402
from modules import notification_engine  # noqa: E402
from modules.help_system import contextual_help, help_render  # noqa: E402
from modules.uiwindows import sourcecontrols as sc  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeEngine:
    def __init__(self, configs=None, running=True, generation=None):
        self.configs = configs if configs is not None else {}
        self.is_running = running
        self.port = 5000
        self._template_config_generation = generation
        self.emitted = []
        self.registered = 0
        self.handlers = []
        self.register_error = None

    def _local_connect_host(self):
        return "127.0.0.1"

    def get_source_controls_configs(self):
        return self.configs

    def emit_template_control_event(self, template, action, data):
        self.emitted.append((template, action, data))

    def register_additional_control_handlers(self):
        if self.register_error:
            raise self.register_error
        self.registered += 1

    def get_registered_dynamic_handlers(self):
        if isinstance(self.handlers, Exception):
            raise self.handlers
        return self.handlers


class FakeParser:
    hidden = {}

    def get_config_files(self):
        return []

    def is_config_hidden(self, name):
        value = self.hidden.get(name, False)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def env(monkeypatch):
    engine = FakeEngine()
    state = {"notes": [], "nav": [], "banners": [], "timers": [], "styles": [], "scheduled": [], "clip": []}
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", engine, raising=False)
    monkeypatch.setattr(sc, "_dirty_names", set())
    monkeypatch.setattr(sc, "_dirty_state", {"all": False})
    monkeypatch.setattr(sc, "_emit_fail_last", {"t": None})
    monkeypatch.setattr(sc, "_view", None)
    monkeypatch.setattr(sc, "source_controls_container", None)
    FakeParser.hidden = {}
    monkeypatch.setattr(sc.template_config_parser, "TemplateConfigParser", lambda *a, **k: FakeParser())
    monkeypatch.setattr(sc, "notify", lambda msg, **kw: state["notes"].append((msg, kw)))
    monkeypatch.setattr(contextual_help, "navigate_to_source_settings_template", lambda n: state["nav"].append(n))
    monkeypatch.setattr(chat_banner, "emit_chat_banner", lambda *a, **kw: state["banners"].append((a, kw)))
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: state["styles"].append((sid, css)))

    def fake_timer(interval, callback, *a, **kw):
        state["timers"].append((interval, callback))
        return None

    monkeypatch.setattr(ui, "timer", fake_timer)

    class Handle:
        def __init__(self, cb):
            self.cb = cb
            self.active = True

    def fake_schedule(delay, cb, once=False):
        h = Handle(cb)
        state["scheduled"].append(h)
        return h

    monkeypatch.setattr(sc, "layout_schedule", fake_schedule)
    monkeypatch.setattr(ui.clipboard, "write", lambda t: state["clip"].append(t))
    state["engine"] = engine
    return state


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


def by_class(root, cls):
    return [el for el in root.descendants() if cls in el.classes]


def text_of(root):
    return [getattr(el, "text", "") for el in root.descendants() if getattr(el, "text", "")]


# ---------------------------------------------------------------- the tab

def test_create_tab_renders_cards_search_and_buttons(env, monkeypatch):
    env["engine"].configs = {
        "beta": {"elements": [{"type": "button", "action": "go", "label": "Go now"}], "hidden": True},
        "Alpha": {"elements": [{"type": "toggle", "action": "t", "label": "Lights", "value": True},
                               {"type": "spin_control", "label": "Wheel"}]},
        "empty": {"elements": []},
    }
    copies = []
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda t: copies.append(t) or True)

    def build():
        sc.create_source_controls_tab()

    async def check(user):
        view = sc._view
        assert view is not None and view.grid is sc.source_controls_container
        assert env["engine"].registered == 1
        assert [sid for sid, _ in env["styles"]] == [sc._STYLE_ID, sc._APP_STYLE_ID]
        assert env["timers"][0][0] == sc._REFRESH_TICK_SEC and env["timers"][0][1] == view.tick
        assert list(view.cards) == ["Alpha", "beta"]
        assert view.pill.text == "2 templates · 3 controls"
        await user.should_see("Source Controls")
        assert "Hidden" in text_of(view.cards["beta"].element)
        assert 0 in view.cards["Alpha"].controls
        # search
        search = next(el for el in user.client.layout.descendants() if el.tag == "input" and el.props.get("type") == "search")
        fire(user, search, "input", "lights")
        assert view.query == "lights"
        assert view.cards["Alpha"].element.visible and not view.cards["beta"].element.visible
        fire(user, search, "input", "nothing-matches")
        assert view.empty_search.visible
        fire(user, search, "input", None)
        assert view.query == ""
        # copy dock link header button
        head_btns = by_class(user.client.layout, "scx-hbtn")
        fire(user, head_btns[0])
        assert copies == ["http://127.0.0.1:5000/source_controls"]
        assert env["notes"][-1][1]["title"] == "OBS dock link copied"
        # refresh header button rebuilds
        env["engine"].configs = {}
        fire(user, head_btns[1])
        assert view.cards == {}
        assert view.pill.text == "0 templates · 0 controls"
        assert "No source controls yet" in text_of(view.grid)
        assert "is-icon" in head_btns[1].classes
        assert head_btns[1].props["aria-label"] == "Refresh"

    run_ui(build, check)


def test_load_source_controls_not_initialized(env, caplog):
    sc.load_source_controls()
    assert "Source controls container not initialized" in caplog.text


def test_load_source_controls_register_error_and_one_template(env):
    env["engine"].register_error = RuntimeError("x")
    env["engine"].configs = {"solo": {"elements": [{"type": "button", "action": "a"}]}}
    env["engine"]._template_config_generation = 3

    def build():
        sc.create_source_controls_tab()

    async def check(user):
        assert sc._view.pill.text == "1 template · 1 control"
        assert sc._view.generation == 3

    run_ui(build, check)


def test_load_source_controls_without_engine_and_errors(env, monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", None)
    monkeypatch.setattr(sc, "_fetch_source_controls", lambda: ({}, set(), [("bad", ValueError("v"))]))

    def build():
        sc.create_source_controls_tab()

    async def check(user):
        assert env["notes"][0][1]["title"] == "Couldn't read the controls for bad"

    run_ui(build, check)


def test_inject_styles_reads_css_and_handles_missing(env, monkeypatch, tmp_path, caplog):
    from modules import path_utils

    (tmp_path / "default_assets").mkdir()
    (tmp_path / "default_assets" / "source_controls.css").write_text(".scx-x { color: red; }", encoding="utf-8")
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel="": str(tmp_path / rel))
    sc._inject_styles()
    assert ".scx-x" in env["styles"][0][1]
    env["styles"].clear()
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel="": str(tmp_path / "missing" / rel))
    sc._inject_styles()
    assert "Source Controls stylesheet missing" in caplog.text
    assert len(env["styles"]) == 2


# ---------------------------------------------------------------- copy dock link

def test_copy_dock_link_falls_back_to_browser_clipboard(env, monkeypatch):
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda t: (_ for _ in ()).throw(OSError()))
    sc._copy_dock_link()
    assert env["clip"] == ["http://127.0.0.1:5000/source_controls"]
    assert env["notes"][-1][1]["type"] == "success"


def test_copy_dock_link_total_failure_warns(env, monkeypatch):
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda t: False)
    monkeypatch.setattr(ui.clipboard, "write", lambda t: (_ for _ in ()).throw(RuntimeError("no page")))
    sc._copy_dock_link()
    msg, kw = env["notes"][-1]
    assert kw["type"] == "warning" and kw["title"] == "Couldn't copy the OBS dock link"
    assert kw["actions"] == [{"kind": "copy", "text": msg, "label": "Copy link"}]


# ---------------------------------------------------------------- template card

def build_card(holder, name, config, hidden=None):
    def build():
        holder["card"] = sc.create_template_control_section(name, config, hidden=hidden)
    return build


def test_card_buttons_grid_wide_and_open(env):
    holder = {}
    config = {"elements": [
        {"type": "button", "action": "a1", "data": {"k": 1}, "description": "First"},
        {"type": "button", "action": "a2"},
        {"type": "button", "action": "a3"},
        {"type": "toggle", "action": "tg", "label": "T"},
        {"type": "spin_control", "label": "Wheel"},
        {"type": "spin_control", "action": "custom_spin", "button_text": "Whirl"},
        {"type": "mystery"},
    ]}

    async def check(user):
        card = holder["card"]
        root = card.element
        groups = by_class(root, "scx-btns")
        assert len(groups) == 2
        btns = by_class(root, "scx-btn")
        assert [b.text for b in btns] == ["A1", "A2", "A3", "Spin", "Whirl"]
        assert "is-wide" in btns[2].classes and "is-wide" not in btns[0].classes
        assert "is-spin" in btns[3].classes and "is-wide" not in btns[4].classes
        for b in btns:
            fire(user, b)
        assert env["engine"].emitted == [("t", "a1", {"k": 1}), ("t", "a2", {}), ("t", "a3", {}),
                                         ("t", "spin", {}), ("t", "custom_spin", {})]
        assert "Unknown control type: mystery" in text_of(root)
        assert list(card.controls) == [3]
        assert by_class(root, "scx-count")[0].text == "7"
        fire(user, by_class(root, "scx-open")[0])
        assert env["nav"] == ["t"]
        assert "Hidden" not in text_of(root)

    run_ui(build_card(holder, "t", config, hidden=False), check)


def test_card_hidden_lookup_via_parser(env):
    FakeParser.hidden = {"h": True, "err": RuntimeError("x")}
    holder = {}

    def build():
        holder["h"] = sc.create_template_control_section("h", {"elements": [{"type": "button"}]})
        holder["err"] = sc.create_template_control_section("err", {"elements": []})

    async def check(user):
        assert "Hidden" in text_of(holder["h"].element)
        assert "Hidden" not in text_of(holder["err"].element)
        assert by_class(holder["err"].element, "scx-count")[0].props["title"] == "0 controls"
        assert by_class(holder["h"].element, "scx-count")[0].props["title"] == "1 control"

    run_ui(build, check)


def test_toggle_control(env):
    holder = {}
    el = {"type": "toggle", "action": "lights", "value": False, "id": "x", "description": "Turns lights on"}

    async def check(user):
        live = holder["card"].controls[0]
        sw = live.el
        assert sw.props["aria-checked"] == "false" and "is-on" not in sw.classes
        assert sw.props["aria-label"] == "x"
        fire(user, sw)
        assert live.value is True and "is-on" in sw.classes and sw.props["aria-checked"] == "true"
        fire(user, sw, "keydown")
        assert live.value is False
        assert env["engine"].emitted == [("t", "lights", {"enabled": True}), ("t", "lights", {"enabled": False})]
        # description -> info icon
        assert any(getattr(e, "_props", {}).get("name") == "info_outline" for e in holder["card"].element.descendants())

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_toggle_starts_on(env):
    holder = {}

    async def check(user):
        sw = holder["card"].controls[0].el
        assert "is-on" in sw.classes and sw.props["aria-checked"] == "true"
        assert sw.props["aria-label"] == "Lamp"

    run_ui(build_card(holder, "t", {"elements": [{"type": "toggle", "label": "Lamp", "value": 1}]}), check)


def test_number_input_control(env):
    holder = {}
    el = {"type": "number_input", "action": "setn", "value": 4.0, "min": 0, "max": 10, "step": 0.5, "label": "N"}

    async def check(user):
        live = holder["card"].controls[0]
        box = live.el
        assert box.props["type"] == "number"
        assert box.props["defaultValue"] == "4"
        assert (box.props["min"], box.props["max"], box.props["step"]) == ("0", "10", "0.5")
        fire(user, box, "input", "7")
        assert live.value == 7.0 and box.props["defaultValue"] == "7"
        fire(user, box, "input", "")
        assert env["engine"].emitted == [("t", "setn", {"value": 7.0}), ("t", "setn", {"value": None})]

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_number_input_without_limits(env):
    holder = {}

    async def check(user):
        box = holder["card"].controls[0].el
        assert "min" not in box.props and "max" not in box.props and "step" not in box.props
        assert box.props["defaultValue"] == "0"
        assert box.props["aria-label"] == "Value"

    run_ui(build_card(holder, "t", {"elements": [{"type": "number_input"}]}), check)


def test_text_input_control_debounces(env):
    holder = {}
    el = {"type": "text_input", "action": "say", "value": None, "placeholder": "Type \"here\"", "label": "Msg"}

    async def check(user):
        live = holder["card"].controls[0]
        box = live.el
        assert box.props["defaultValue"] == ""
        assert box.props["placeholder"] == 'Type &quot;here&quot;'
        fire(user, box, "input", "he")
        fire(user, box, "input", "hello")
        first, second = env["scheduled"]
        assert first.active is False and second.active is True
        second.cb()
        assert env["engine"].emitted == [("t", "say", {"text": "hello"})]
        fire(user, box, "input", "")
        env["scheduled"][-1].cb()
        assert env["engine"].emitted[-1] == ("t", "say", {"text": ""})

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_text_input_with_value(env):
    holder = {}

    async def check(user):
        live = holder["card"].controls[0]
        assert live.value == "12" and live.el.props["defaultValue"] == "12"
        assert live.el.props["aria-label"] == "Text"

    run_ui(build_card(holder, "t", {"elements": [{"type": "text_input", "value": 12}]}), check)


def test_slider_control(env):
    holder = {}
    el = {"type": "slider", "action": "vol", "min": 0, "max": 50, "step": 5, "value": 10, "label": "Volume"}

    async def check(user):
        live = holder["card"].controls[0]
        rng = live.el
        assert rng.props["type"] == "range"
        assert rng._style["--p"] == "20.00%"
        assert live.chip.text == "10"
        listener = next(iter(rng._event_listeners.values()))
        assert listener.throttle == sc._SLIDER_THROTTLE_SEC
        fire(user, rng, "input", "25")
        assert live.value == 25 and live.chip.text == "25" and rng._style["--p"] == "50.00%"
        fire(user, rng, "input", "abc")
        assert env["engine"].emitted == [("t", "vol", {"value": 25})]

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_slider_defaults(env):
    holder = {}

    async def check(user):
        live = holder["card"].controls[0]
        assert live.value == 0 and live.lo == 0 and live.hi == 100
        assert live.el.props["aria-label"] == "Slider"

    run_ui(build_card(holder, "t", {"elements": [{"type": "slider"}]}), check)


def test_legacy_counter_control(env):
    holder = {}
    el = {"type": "counter_control", "action_increment": "up"}

    async def check(user):
        root = holder["card"].element
        assert "Counter" in text_of(root)
        fire(user, by_class(root, "minus")[0])
        fire(user, by_class(root, "plus")[0])
        fire(user, by_class(root, "scx-iconbtn")[0])
        assert env["engine"].emitted == [("t", "counter_decrement", {}), ("t", "up", {}), ("t", "counter_reset", {})]
        assert holder["card"].controls == {}

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_spore_counter_control(env):
    holder = {}
    el = {"type": "counter_control", "target_counter_id": "deaths", "step": 3, "label": "Deaths"}

    async def check(user):
        root = holder["card"].element
        box = next(e for e in root.descendants() if e.tag == "input")
        assert box.props["defaultValue"] == "3"
        fire(user, by_class(root, "plus")[0])
        fire(user, box, "input", "10")
        fire(user, by_class(root, "minus")[0])
        fire(user, box, "input", "abc")
        fire(user, by_class(root, "plus")[0])
        fire(user, box, "input", "0")
        fire(user, by_class(root, "plus")[0])
        fire(user, by_class(root, "scx-iconbtn")[0])
        delta = lambda n: {"kind": "fixed", "value": n}  # noqa: E731
        assert env["engine"].emitted == [
            ("t", "counter_adjust", {"target_counter_id": "deaths", "operation": "increment", "delta": delta(3)}),
            ("t", "counter_adjust", {"target_counter_id": "deaths", "operation": "decrement", "delta": delta(10)}),
            ("t", "counter_adjust", {"target_counter_id": "deaths", "operation": "increment", "delta": delta(3)}),
            ("t", "counter_adjust", {"target_counter_id": "deaths", "operation": "increment", "delta": delta(1)}),
            ("t", "counter_adjust", {"target_counter_id": "deaths", "operation": "reset"}),
        ]
        assert "Deaths" in text_of(root)

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_timer_control(env):
    holder = {}
    el = {"type": "timer_control", "target_timer_id": "tm1"}

    async def check(user):
        root = holder["card"].element
        seg = by_class(root, "scx-seg")[0]
        btns = [e for e in seg.descendants() if e.tag == "button"]
        assert len(btns) == 3
        assert btns[2].props["aria-label"] == "Reset"
        for b in btns:
            fire(user, b)
        assert [e[1] for e in env["engine"].emitted] == [
            sc.timer_control_event("tm1", "start"), sc.timer_control_event("tm1", "pause"),
            sc.timer_control_event("tm1", "reset")]
        assert all(e[2] == {"element_id": "tm1"} for e in env["engine"].emitted)
        assert "Timer" in text_of(root)

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_banner_control(env):
    holder = {}
    el = {"type": "banner_control", "placeholder": "Say it", "duration": 20, "min": 5, "max": 60}

    async def check(user):
        root = holder["card"].element
        banner = by_class(root, "scx-banner")[0]
        inputs = [e for e in banner.descendants() if e.tag == "input"]
        msg, dur = inputs
        assert msg.props["placeholder"] == "Say it"
        assert dur.props["defaultValue"] == "20" and dur.props["min"] == "5" and dur.props["max"] == "60"
        show, clear = by_class(banner, "scx-btn")
        fire(user, show)
        assert env["banners"][-1] == (("",), {"duration": 20.0, "permanent": False, "source": "manual"})
        fire(user, msg, "input", "Hello")
        fire(user, dur, "input", "1")
        fire(user, show)
        assert env["banners"][-1] == (("Hello",), {"duration": 5.0, "permanent": False, "source": "manual"})
        fire(user, dur, "input", "999")
        perm = by_class(banner, "scx-switch")[0]
        fire(user, perm)
        assert "is-on" in perm.classes
        fire(user, show)
        assert env["banners"][-1][1] == {"duration": 60.0, "permanent": True, "source": "manual"}
        fire(user, perm, "keydown")
        assert "is-on" not in perm.classes
        fire(user, dur, "input", "abc")
        fire(user, msg, "input", None)
        fire(user, show)
        assert env["banners"][-1] == (("",), {"duration": 20.0, "permanent": False, "source": "manual"})
        fire(user, clear)
        assert env["banners"][-1] == ((), {"action": "clear", "source": "manual"})

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_banner_control_bad_duration_and_no_limits(env, monkeypatch):
    holder = {}
    el = {"type": "banner_control", "duration": "soon", "min": None, "max": "x"}

    async def check(user):
        banner = by_class(holder["card"].element, "scx-banner")[0]
        dur = [e for e in banner.descendants() if e.tag == "input"][1]
        assert dur.props["defaultValue"] == "10"
        assert "min" not in dur.props and "max" not in dur.props
        fire(user, dur, "input", "-5")
        fire(user, by_class(banner, "scx-btn")[0])
        assert env["banners"][-1][1]["duration"] == -5.0
        assert "Banner" in text_of(banner)

    run_ui(build_card(holder, "t", {"elements": [el]}), check)


def test_create_control_element_returns_none_for_non_live(env):
    holder = {}

    def build():
        holder["r"] = [sc.create_control_element("t", {"type": t}) for t in
                       ("counter_control", "timer_control", "banner_control", "zzz")]
        holder["u"] = sc.create_control_element("t", {})

    async def check(user):
        assert holder["r"] == [None, None, None, None]
        await user.should_see("Unknown control type: unknown")

    run_ui(build, check)


def test_view_render_live_values_via_apply(env, monkeypatch):
    holder = {}
    configs = {"a": {"elements": [{"type": "number_input", "action": "n", "value": 1}]}}

    def build():
        v = sc._SourceControlsView()
        v.grid = ui.element("div")
        v.render(configs, set())
        holder["v"] = v

    async def check(user):
        v = holder["v"]
        live = v.cards["a"].controls[0]
        js = []
        monkeypatch.setattr(type(user.client), "run_javascript", lambda self, code, **kw: js.append(code))
        v.apply({"a": {"elements": [{"type": "number_input", "action": "n", "value": 9}]}}, set(), {"a"})
        assert live.value == 9.0 and live.el.props["defaultValue"] == "9"
        assert js and "e.value = \"9\"" in js[-1]

    run_ui(build, check)


# ---------------------------------------------------------------- debug dialog

def test_show_debug_info_lists_handlers(env):
    env["engine"].handlers = ["chat_clear", "alerts_skip"]

    def build():
        ui.button("open", on_click=sc.show_debug_info)

    async def check(user):
        user.find("open").click()
        await user.should_see("Registered Dynamic Handlers")
        await user.should_see("Total handlers: 2")
        await user.should_see("• chat_clear")
        user.find("Close").click()

    run_ui(build, check)


def test_show_debug_info_empty(env):
    def build():
        ui.button("open", on_click=sc.show_debug_info)

    async def check(user):
        user.find("open").click()
        await user.should_see("No dynamic handlers registered")

    run_ui(build, check)


def test_show_debug_info_no_engine(env, monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", None)
    sc.show_debug_info()
    assert env["notes"][-1][1]["title"] == "The overlay server isn't running yet"


def test_show_debug_info_error(env):
    env["engine"].handlers = RuntimeError("boom")
    sc.show_debug_info()
    assert env["notes"][-1][1]["title"] == "Couldn't show the handler list"
    assert env["notes"][-1][1]["details"] == "RuntimeError: boom"

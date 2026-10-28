# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/contextual_help.py: tab context detection, navigation helpers,
help target mapping and the help widgets (buttons, inline help, tooltips, accordions, cards)."""

from __future__ import annotations

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

from modules import notification_engine  # noqa: E402
from modules.help_system import contextual_help as ch  # noqa: E402
from modules.help_system import help_browser, help_manager  # noqa: E402
from modules.help_system.help_content import HELP_TOPICS, HelpCategory  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for name in ("_main_tabs", "_main_tab_panels", "_settings_tabs", "_settings_tab_panels",
                 "_alerts_tabs", "_chatbot_tabs"):
        monkeypatch.setattr(ch, name, None)
    monkeypatch.setattr(ch, "_main_tab_refs", {})
    monkeypatch.setattr(help_browser, "_help_system_styles_injected", False)
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)


@pytest.fixture
def shown(monkeypatch):
    """Record show_help_browser calls instead of opening help."""
    calls = []

    def fake(topic_id=None, category=None):
        calls.append((topic_id, category))

    monkeypatch.setattr(help_browser, "show_help_browser", fake)
    return calls


@pytest.fixture
def scheduled(monkeypatch):
    calls = []
    monkeypatch.setattr(ch, "layout_schedule", lambda delay, fn, once=False: calls.append((delay, fn, once)))
    return calls


class _Tabs:
    def __init__(self, value=None, fail=False):
        self._value = value
        self.fail = fail

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        if self.fail:
            raise RuntimeError("tab gone")
        self._value = v


def fire(user, el, event="click"):
    UserInteraction(user, {el}, None).trigger(event)


# ----------------------------------------------------------------- references

def test_reference_setters():
    tabs, panels = _Tabs(), object()
    ch.set_main_ui_references(tabs, panels)
    assert ch._main_tabs is tabs and ch._main_tab_panels is panels
    ch.set_settings_ui_references("s", "sp")
    assert (ch._settings_tabs, ch._settings_tab_panels) == ("s", "sp")
    ch.set_alerts_ui_references("a")
    ch.set_chatbot_ui_references("c")
    assert ch._alerts_tabs == "a" and ch._chatbot_tabs == "c"
    refs = {"Settings": object()}
    ch.register_main_tabs(refs)
    assert ch._main_tab_refs == refs and ch._main_tab_refs is not refs
    ch.register_main_tabs(None)
    assert ch._main_tab_refs == {}


# ----------------------------------------------------------------- navigation

def test_navigate_to_main_tab():
    ch.navigate_to_main_tab("Alerts")  # no tabs: no error
    tabs = _Tabs()
    ch._main_tabs = tabs
    ref = object()
    ch.register_main_tabs({"Settings": ref})
    ch.navigate_to_main_tab("Settings")
    assert tabs.value is ref
    ch.navigate_to_main_tab("Alerts")
    assert tabs.value == "Alerts"
    ch._main_tabs = _Tabs(fail=True)
    ch.navigate_to_main_tab("Alerts")  # error swallowed


def test_navigate_to_settings_subtab(scheduled):
    main, settings = _Tabs(), _Tabs()
    ch._main_tabs, ch._settings_tabs = main, settings
    ch.navigate_to_settings_subtab("Game Hooks")
    assert main.value == "Settings"
    assert [(d, once) for d, _, once in scheduled] == [(0.12, True), (0.45, True)]
    scheduled[0][1]()
    assert settings.value == "Game Hooks"
    ch._settings_tabs = _Tabs(fail=True)
    scheduled[1][1]()  # inner error swallowed
    ch._settings_tabs = None
    scheduled[1][1]()


def test_navigate_to_settings_subtab_custom_main_and_failure(monkeypatch, scheduled):
    main = _Tabs()
    ch._main_tabs = main
    ch.navigate_to_settings_subtab("Twitch", main_tab="Activity Feed")
    assert main.value == "Activity Feed"

    def boom(*a, **k):
        raise RuntimeError("no slot")

    monkeypatch.setattr(ch, "layout_schedule", boom)
    ch.navigate_to_settings_subtab("Twitch")  # swallowed


@pytest.mark.parametrize(
    "func, main_label, attr",
    [
        (ch.navigate_to_chatbot_subtab, "Chatbot", "_chatbot_tabs"),
        (ch.navigate_to_alerts_subtab, "Alerts", "_alerts_tabs"),
    ],
)
def test_subtab_navigation(func, main_label, attr, scheduled):
    main = _Tabs()
    ch._main_tabs = main
    func("Polls")
    assert main.value == main_label
    assert [d for d, _, _ in scheduled] == [0.12, 0.45]
    scheduled[0][1]()  # sub-tabs not built yet: nothing happens
    sub = _Tabs()
    setattr(ch, attr, sub)
    scheduled[0][1]()
    assert sub.value == "Polls"
    setattr(ch, attr, _Tabs(fail=True))
    scheduled[1][1]()  # error swallowed


def test_navigate_to_source_settings_template(monkeypatch, scheduled):
    import modules.uiwindows.customsources as cs

    selected = []
    monkeypatch.setattr(cs, "select_config", lambda name: selected.append(name))
    main = _Tabs()
    ch._main_tabs = main
    ch.navigate_to_source_settings_template("alerts.json")
    assert main.value == "Source Settings"
    assert [d for d, _, _ in scheduled] == [0.15, 0.6, 1.5]
    scheduled[0][1]()
    assert selected == ["alerts.json"]

    def boom(name):
        raise RuntimeError("unsaved")

    monkeypatch.setattr(cs, "select_config", boom)
    scheduled[1][1]()  # swallowed


# ----------------------------------------------------------------- context detection

def test_current_tab_context():
    assert ch.get_current_tab_context() == ("unknown", None)
    ch._main_tabs = _Tabs("Activity Feed")
    assert ch.get_current_tab_context() == ("Activity Feed", None)
    ch._main_tabs = _Tabs("Settings")
    assert ch.get_current_tab_context() == ("Settings", None)
    ch._settings_tabs = _Tabs("Twitch")
    assert ch.get_current_tab_context() == ("Settings", "Twitch")
    ch._main_tabs = _Tabs("Alerts")
    ch._alerts_tabs = _Tabs(SimpleNamespace(text="Media"))
    assert ch.get_current_tab_context() == ("Alerts", "Media")
    ch._main_tabs = _Tabs("Chatbot")
    ch._chatbot_tabs = _Tabs("Quotes")
    assert ch.get_current_tab_context() == ("Chatbot", "Quotes")


def test_current_tab_context_error():
    class _Broken:
        @property
        def value(self):
            raise RuntimeError("deleted")

    ch._main_tabs = _Broken()
    assert ch.get_current_tab_context() == ("unknown", None)


class _FalsyCallable:
    """Falsy but callable, so the attribute scan skips it and the props() fallback runs."""

    def __init__(self, data=None, exc=None):
        self.data, self.exc = data, exc

    def __bool__(self):
        return False

    def __call__(self):
        if self.exc:
            raise self.exc
        return self.data


class _CallableProps:
    def __init__(self, data=None, exc=None):
        self.props = _FalsyCallable(data, exc)

    def __str__(self):
        return "fallback-str"


class _MethodProps:
    def props(self):
        return {"label": "never read"}


@pytest.mark.parametrize(
    "tab, name",
    [
        ("Plain", "Plain"),
        (SimpleNamespace(text="T"), "T"),
        (SimpleNamespace(text="", label="L"), "L"),
        (SimpleNamespace(name="N"), "N"),
        (SimpleNamespace(_text="private"), "private"),
        (SimpleNamespace(_props={"label": "PL"}), "PL"),
        (SimpleNamespace(_props={"text": "", "name": "PN"}), "PN"),
        (SimpleNamespace(_props={"other": 1}, props={"label": "after"}), "after"),
        (_CallableProps({"label": "CL"}), "CL"),
        (_CallableProps({}), "fallback-str"),
        (_CallableProps(exc=RuntimeError("x")), "fallback-str"),
        (12, "12"),
    ],
)
def test_get_tab_name(tab, name):
    assert ch._get_tab_name(tab) == name


def test_get_tab_name_bound_props_method_is_stringified():
    # A truthy props() method is caught by the attribute scan and stringified as-is.
    assert ch._get_tab_name(_MethodProps()).startswith("<bound method _MethodProps.props")


def test_get_tab_name_with_nicegui_tab():
    made = {}

    def build():
        with ui.tabs():
            made["tab"] = ui.tab("Theme")

    run_ui(build)
    assert ch._get_tab_name(made["tab"]) == "Theme"


# ----------------------------------------------------------------- help targets

@pytest.mark.parametrize(
    "main, sub, target",
    [
        ("Activity Feed", None, "activity_feed"),
        ("Alerts", "Media", "alerts_overview"),
        ("Chatbot", None, "chatbot_commands"),
        ("Chatbot", "Polls", "chatbot_polls"),
        ("Chatbot", "Unknown", "chatbot_commands"),
        ("Settings", None, "settings"),
        ("Settings", "Twitch", "twitch_setup"),
        ("Settings", "Theme", "settings"),
        ("Settings", "Nope", None),
        ("unknown", None, None),
    ],
)
def test_help_target_for_tab(main, sub, target):
    assert ch.get_help_target_for_tab(main, sub) == target


def test_every_mapped_help_target_exists():
    """Every target is either a topic id or a help category (used as a category fallback)."""
    from modules.ui_tab_transitions import MAIN_TAB_ORDER, SETTINGS_TAB_ORDER

    categories = {c.value for c in HelpCategory}
    pairs = [(m, None) for m in MAIN_TAB_ORDER]
    pairs += [("Settings", s) for s in SETTINGS_TAB_ORDER]
    pairs += [("Chatbot", s) for s in ("Commands", "Events", "Quotes", "Greetings", "Giveaways", "Polls")]
    for main, sub in pairs:
        target = ch.get_help_target_for_tab(main, sub)
        assert target is not None, (main, sub)
        assert target in HELP_TOPICS or target in categories, (main, sub, target)


def test_open_help_target(shown):
    ch._open_help_target("twitch_setup")
    ch._open_help_target("settings")
    assert shown == [("twitch_setup", None), (None, "settings")]


# ----------------------------------------------------------------- help_button

def _click_button(build):
    made = {}

    def b():
        made["btn"] = build()

    async def check(user):
        fire(user, made["btn"])

    run_ui(b, check)
    return made["btn"]


def test_help_button_topic(shown):
    btn = _click_button(lambda: ch.help_button(topic_id="guide_psn", tooltip="PSN help"))
    assert shown == [("guide_psn", None)]
    assert btn.props.get("icon") == "help_outline"
    assert btn.props.get("flat") is True and btn.props.get("round") is True
    assert list(btn.classes) == ["help-inline-icon", "text-sm", "p-1"]


def test_help_button_context_found_and_missing(shown):
    _click_button(lambda: ch.help_button(context="settings.twitch"))
    _click_button(lambda: ch.help_button(context="no.such.context"))
    assert shown == [("twitch_setup", None), (None, None)]


def test_help_button_auto_context(shown):
    ch._main_tabs = _Tabs("Settings")
    ch._settings_tabs = _Tabs("Nope")
    _click_button(lambda: ch.help_button())  # no target -> general help
    ch._settings_tabs = _Tabs("Twitch")
    _click_button(lambda: ch.help_button())
    ch._main_tabs = _Tabs("Settings")
    ch._settings_tabs = _Tabs("Theme")
    _click_button(lambda: ch.help_button())  # "settings" is a category
    assert shown == [(None, None), ("twitch_setup", None), (None, "settings")]


def test_help_button_no_auto_context(shown):
    _click_button(lambda: ch.help_button(auto_context=False))
    assert shown == [(None, None)]


@pytest.mark.parametrize(
    "size, icon, extra",
    [("xs", "help", ["text-xs", "p-1"]), ("md", "help", ["text-base", "p-2"]),
     ("lg", "help", ["text-lg", "p-3"]), ("huge", "help", [])],
)
def test_help_button_sizes(size, icon, extra, shown):
    made = {}

    def build():
        made["btn"] = ch.help_button(topic_id="x", size=size, variant="outline")

    run_ui(build)
    btn = made["btn"]
    assert btn.props.get("icon") == icon
    assert btn.props.get("outline") is True
    assert list(btn.classes) == ["help-inline-icon", *extra]


def test_help_button_error_notifies(monkeypatch):
    notes = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **k: notes.append(k))

    def boom(*a, **k):
        raise RuntimeError("help broke")

    monkeypatch.setattr(help_browser, "show_help_browser", boom)
    _click_button(lambda: ch.help_button(topic_id="x", context="ctx"))
    assert notes[0]["title"] == "Couldn't open Help"
    assert notes[0]["details"] == "context: ctx\nRuntimeError: help broke"


@pytest.mark.parametrize(
    "factory, expected",
    [
        (ch.twitch_help_button, ("twitch_setup", None)),
        (ch.discord_help_button, ("guide_discord", None)),
        (ch.alerts_help_button, ("alerts_overview", None)),
        (ch.connectors_help_button, ("connectors_intro", None)),
        (ch.templates_help_button, ("templates_intro", None)),
        (ch.spore_studio_help_button, ("spore_studio_overview", None)),
        (ch.troubleshooting_help_button, ("troubleshooting_alerts", None)),
    ],
)
def test_convenience_buttons(factory, expected, shown):
    _click_button(factory)
    assert shown == [expected]
    assert expected[0] in HELP_TOPICS


def test_spore_studio_button_kwargs(shown):
    made = {}

    def build():
        made["btn"] = ch.spore_studio_help_button(size="lg")

    run_ui(build)
    assert "text-lg" in made["btn"].classes


# ----------------------------------------------------------------- inline_help

def test_inline_help_with_topic_and_icon(shown):
    made = {}

    def build():
        made["row"] = ch.inline_help("Some help", topic_id="guide_psn")

    async def check(user):
        await user.should_see("Some help")
        fire(user, [e for e in made["row"].descendants() if isinstance(e, ui.button)][0])

    run_ui(build, check)
    assert shown == [("guide_psn", None)]
    assert [e for e in made["row"].descendants() if isinstance(e, ui.icon)]
    assert help_browser._help_system_styles_injected


def test_inline_help_context_lookup_and_no_icon(shown):
    made = {}

    def build():
        made["found"] = ch.inline_help("ctx", context="settings.twitch", show_icon=False)
        made["missing"] = ch.inline_help("none", context="nope")

    run_ui(build)
    found_buttons = [e for e in made["found"].descendants() if isinstance(e, ui.button)]
    assert [b.text for b in found_buttons] == ["Learn more"]
    assert not [e for e in made["found"].descendants() if isinstance(e, ui.icon)]
    assert not [e for e in made["missing"].descendants() if isinstance(e, ui.button)]


def test_inline_help_fallback_on_error(monkeypatch):
    def boom():
        raise RuntimeError("styles")

    monkeypatch.setattr(ch, "_help_ui", boom)
    made = {}

    def build():
        made["el"] = ch.inline_help("Fallback text")

    run_ui(build)
    assert isinstance(made["el"], ui.label) and made["el"].text == "Fallback text"


# ----------------------------------------------------------------- help_tooltip

def _tooltips(el):
    return [t.text for t in el.client.elements.values()
            if isinstance(t, ui.tooltip) and t.props.get("target") == f"#{el.html_id}"]


def test_help_tooltip_variants(shown):
    made = {}

    def build():
        made["topic"] = ui.button("A")
        ch.help_tooltip(made["topic"], "Tip A", topic_id="guide_psn")
        made["ctx"] = ui.button("B")
        ch.help_tooltip(made["ctx"], "Tip B", context="settings.twitch")
        made["miss"] = ui.button("C")
        ch.help_tooltip(made["miss"], "Tip C", context="nope")
        made["plain"] = ui.label("D")
        ch.help_tooltip(made["plain"], "Tip D")
        made["label_ctx"] = ui.label("E")
        ch.help_tooltip(made["label_ctx"], "Tip E", topic_id="x")  # no on_click: just tooltip

    async def check(user):
        for key in ("topic", "ctx", "miss"):
            fire(user, made[key])

    run_ui(build, check)
    assert shown == [("guide_psn", None), ("twitch_setup", None)]
    assert _tooltips(made["plain"]) == ["Tip D"]
    assert _tooltips(made["label_ctx"]) == ["Tip E"]


def test_help_tooltip_error_logged(caplog):
    class _Bad:
        def tooltip(self, text):
            raise RuntimeError("deleted")

    ch.help_tooltip(_Bad(), "x")
    assert "Error adding help tooltip" in caplog.text


# ----------------------------------------------------------------- accordion / card / section

def test_help_accordion_with_link(shown):
    made = {}

    def build():
        made["a"] = ch.help_accordion("Title", "**Bold** text", topic_id="guide_psn")
        made["b"] = ch.help_accordion("Ctx", "c", context="settings.twitch")
        made["c"] = ch.help_accordion("None", "c", context="nope")

    async def check(user):
        btn = [e for e in made["a"].descendants() if isinstance(e, ui.button)][0]
        assert btn.text == "Read full documentation"
        fire(user, btn)

    run_ui(build, check)
    assert isinstance(made["a"], ui.expansion)
    assert shown == [("guide_psn", None)]
    assert [e for e in made["b"].descendants() if isinstance(e, ui.button)]
    assert not [e for e in made["c"].descendants() if isinstance(e, ui.button)]
    assert [e.content for e in made["a"].descendants() if isinstance(e, ui.markdown)] == ["**Bold** text"]


def test_help_accordion_fallback(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ch, "_help_ui", boom)
    made = {}

    def build():
        made["el"] = ch.help_accordion("T", "C")

    run_ui(build)
    assert made["el"].text == "T: C"


def test_help_card(shown):
    made = {}

    def build():
        made["full"] = ch.help_card("Card", "Sum", "twitch_setup")
        made["nocat"] = ch.help_card("Card2", "Sum2", "twitch_setup", show_category=False)
        made["missing"] = ch.help_card("Card3", "Sum3", "no_topic")

    async def check(user):
        fire(user, made["full"])

    run_ui(build, check)
    texts = [d.text for d in made["full"].descendants() if isinstance(d, ui.label)]
    assert texts[:3] == ["Connection Guides", "Card", "Sum"]
    assert texts[3:] == HELP_TOPICS["twitch_setup"].keywords[:3]
    nocat = [d.text for d in made["nocat"].descendants() if isinstance(d, ui.label)]
    assert nocat[0] == "Card2"
    missing = [d.text for d in made["missing"].descendants() if isinstance(d, ui.label)]
    assert missing == ["Card3", "Sum3"]
    assert shown == [("twitch_setup", None)]


def test_help_card_fallback(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ch, "_help_ui", boom)
    made = {}

    def build():
        made["el"] = ch.help_card("T", "S", "id")

    run_ui(build)
    assert isinstance(made["el"], ui.card)
    assert [d.text for d in made["el"].descendants() if isinstance(d, ui.label)] == ["T", "S"]


def test_create_help_section(shown):
    made = {}

    def build():
        with ui.column() as col:
            ch.create_help_section("Section", [("guide_psn", "PSN", "p"), ("twitch_setup", "Tw", "t")], columns=3)
        made["col"] = col

    run_ui(build)
    grids = [d for d in made["col"].descendants() if isinstance(d, ui.grid)]
    assert len(grids) == 1
    cards = [d for d in grids[0].descendants() if isinstance(d, ui.card)]
    assert len(cards) == 2
    assert [d.text for d in made["col"].descendants() if isinstance(d, ui.label)][0] == "Section"


def test_create_help_section_error(monkeypatch, caplog):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ch, "_help_ui", boom)
    ch.create_help_section("S", [])
    assert "Error creating help section 'S'" in caplog.text


def test_help_ui_returns_lazy_functions():
    ensure, show, get_mgr = ch._help_ui()
    assert ensure is help_browser.ensure_help_system_styles
    assert show is help_browser.show_help_browser
    assert get_mgr is help_manager.get_help_manager

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot.py: the Chatbot tab shell, item actions and dialogs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules.chatbot_core import ChatCommand, ChatEvent, Quote  # noqa: E402
from modules.uiwindows import chatbot as tab  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeManager:
    def __init__(self):
        self.commands = {}
        self.events = {}
        self.quotes = {}
        self.calls = []
        self.toggle_result = True
        self.reset_result = True
        self.remove_result = True
        self.add_result = True
        self.add_quote_result = (True, "", 7)

    def get_all_commands(self):
        return dict(self.commands)

    def get_all_events(self):
        return dict(self.events)

    def toggle_item(self, item_id, enabled):
        self.calls.append(("toggle", item_id, enabled))
        if isinstance(self.toggle_result, Exception):
            raise self.toggle_result
        return self.toggle_result

    def reset_command_counter(self, cid):
        self.calls.append(("reset", cid))
        if isinstance(self.reset_result, Exception):
            raise self.reset_result
        return self.reset_result

    def add_command(self, command):
        self.calls.append(("add_command", command))
        return self.add_result

    def add_event(self, event):
        self.calls.append(("add_event", event))
        return self.add_result

    def add_quote(self, text, author, added_by):
        self.calls.append(("add_quote", text, author, added_by))
        return self.add_quote_result

    def remove_command(self, iid):
        self.calls.append(("remove_command", iid))
        if isinstance(self.remove_result, Exception):
            raise self.remove_result
        return self.remove_result

    def remove_event(self, iid):
        self.calls.append(("remove_event", iid))
        return self.remove_result

    def delete_quote(self, iid):
        self.calls.append(("delete_quote", iid))
        return self.remove_result


class Notes:
    def __init__(self):
        self.calls = []

    def __call__(self, message="", **kwargs):
        self.calls.append((message, kwargs))

    @property
    def types(self):
        return [kw.get("type") for _, kw in self.calls]

    @property
    def titles(self):
        return [kw.get("title") for _, kw in self.calls]

    @property
    def messages(self):
        return [m for m, _ in self.calls]


class FakePanel:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.built = []
        self.refreshed = 0
        self.fail = None

    def build(self, container):
        if self.fail:
            raise self.fail
        self.built.append(container)

    def refresh(self):
        self.refreshed += 1


class FakeContainer:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


@pytest.fixture
def mgr(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(tab, "get_chatbot_manager", lambda: manager)
    return manager


@pytest.fixture
def notes(monkeypatch):
    n = Notes()
    monkeypatch.setattr(tab, "notify", n)
    return n


@pytest.fixture
def clean_globals(monkeypatch):
    """Reset every UI global so tests can't leak containers/panels into each other."""
    for name in ("commands_container", "events_container", "quotes_container", "greetings_container",
                 "giveaways_container", "polls_container", "create_dialog", "edit_dialog", "_active_editor"):
        monkeypatch.setattr(tab, name, None)
    monkeypatch.setattr(tab, "_items_panels", {})
    monkeypatch.setattr(tab, "_qg_panels", {})
    monkeypatch.setattr(tab, "_gp_panels", {})


@pytest.fixture
def refreshes(monkeypatch):
    seen = []
    monkeypatch.setattr(tab, "refresh_tab_content", lambda kind: seen.append(kind))
    return seen


def _cmd(cid="c1", name="Hello", word="hello", aliases=None, **kw):
    return ChatCommand(command_id=cid, name=name, command_name=word, aliases=aliases or [], response_text="hi", **kw)


def _evt(eid="e1", name="Follow"):
    return ChatEvent(event_id=eid, name=name, event_type="follow", response_text="thanks")


# --------------------------------------------------------------------------- small helpers


def test_wrap_variable_value_escapes_html():
    out = tab._wrap_variable_value("<b>&")
    assert "&lt;b&gt;&amp;" in out
    assert out.startswith("<span") and "var(--color-primary)" in out


def test_wrap_variable_value_stringifies():
    assert ">42<" in tab._wrap_variable_value(42)


def test_custom_css_is_balanced():
    assert tab.CUSTOM_CSS.count("{") == tab.CUSTOM_CSS.count("}")
    assert ".chatbot-card" in tab.CUSTOM_CSS


@pytest.mark.parametrize("pattern,text,group", [
    ("_PREVIEW_STATS_PATTERN", "{stats.followers}", "followers"),
    ("_PREVIEW_YOUTUBE_PATTERN", "{youtube.subs}", "subs"),
    ("_PREVIEW_CUSTOM_PATTERN", "{custom_x}", "x"),
    ("_PREVIEW_WORD_PATTERN", "{command_word_2}", "2"),
    ("_PREVIEW_DATE_TO_AGE_PATTERN", "date_to_age(1990-01-01)", "1990-01-01"),
    ("_PREVIEW_MATH_PATTERN", "math(1+2)", "1+2"),
    ("_PREVIEW_COMPARE_PATTERN", "compare(a,b)", "a,b"),
    ("_PREVIEW_VAR_PATTERN", "{username}", "username"),
    ("_PREVIEW_API_PATTERN", "{api_weather}", "weather"),
])
def test_preview_patterns(pattern, text, group):
    assert getattr(tab, pattern).search(text).group(1) == group


def test_preview_quote_and_dot_patterns():
    m = tab._PREVIEW_QUOTE_PATTERN.search("{quote.3.text}")
    assert m.groups() == ("3", "text")
    assert tab._PREVIEW_DOT_PATTERN.search("{a.b}").groups() == ("a.b", "b")
    assert tab._PREVIEW_USERNAME_PATTERN.sub("X", "hi {username}") == "hi X"


def test_unique_copy_label_basic_and_collisions():
    assert tab._unique_copy_label("Hello", set()) == "Hello copy"
    assert tab._unique_copy_label("Hello", {"hello COPY"}) == "Hello copy 2"
    assert tab._unique_copy_label("Hello", {"Hello copy", "Hello copy 2", None, ""}) == "Hello copy 3"


@pytest.mark.parametrize("base", ["", "   ", None])
def test_unique_copy_label_blank_falls_back_to_item(base):
    assert tab._unique_copy_label(base, set()) == "Item copy"


def test_unique_command_token():
    assert tab._unique_command_token("hi there", set()) == "hithere_copy"
    assert tab._unique_command_token("hi", {"HI_COPY"}) == "hi_copy2"
    assert tab._unique_command_token("hi", {"hi_copy", "hi_copy2", ""}) == "hi_copy3"
    assert tab._unique_command_token("", set()) == "command_copy"
    assert tab._unique_command_token(None, set()) == "command_copy"
    assert tab._unique_command_token("  \t ", set()) == "command_copy"


# --------------------------------------------------------------------------- containers / refresh


def test_get_container_for_tab_maps_every_tab(monkeypatch, clean_globals):
    sentinels = {}
    for kind in ("commands", "events", "quotes", "greetings", "giveaways", "polls"):
        sentinels[kind] = object()
        monkeypatch.setattr(tab, f"{kind}_container", sentinels[kind])
    for kind, obj in sentinels.items():
        assert tab.get_container_for_tab(kind) is obj
    assert tab.get_container_for_tab("nope") is None


def test_refresh_tab_content_without_container_logs_and_returns(clean_globals, caplog):
    tab._items_panels["commands"] = FakePanel()
    tab.refresh_tab_content("commands")
    assert tab._items_panels["commands"].refreshed == 0
    assert "not initialized" in caplog.text


@pytest.mark.parametrize("kind,registry", [
    ("commands", "_items_panels"), ("quotes", "_qg_panels"), ("polls", "_gp_panels"),
])
def test_refresh_tab_content_refreshes_the_right_panel(monkeypatch, clean_globals, kind, registry):
    monkeypatch.setattr(tab, f"{kind}_container", FakeContainer())
    panel = FakePanel()
    getattr(tab, registry)[kind] = panel
    tab.refresh_tab_content(kind)
    assert panel.refreshed == 1


def test_refresh_tab_content_without_panel_is_a_noop(monkeypatch, clean_globals, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger=tab.logger.name)
    monkeypatch.setattr(tab, "events_container", FakeContainer())
    tab.refresh_tab_content("events")
    assert "No panel" in caplog.text


def test_refresh_chatbot_items_refreshes_all_six(refreshes):
    tab.refresh_chatbot_items()
    assert refreshes == ["commands", "events", "quotes", "greetings", "giveaways", "polls"]


def test_load_chatbot_items_builds_every_panel(monkeypatch, clean_globals):
    containers = {k: FakeContainer() for k in ("commands", "events", "quotes", "greetings", "giveaways", "polls")}
    for k, c in containers.items():
        monkeypatch.setattr(tab, f"{k}_container", c)
    for name in ("QuotesPanel", "GreetingsPanel", "GiveawaysPanel", "PollsPanel"):
        monkeypatch.setattr(tab, name, type(name, (FakePanel,), {}))
    tab._items_panels["commands"] = FakePanel()
    tab._items_panels["events"] = FakePanel()

    tab.load_chatbot_items()

    assert containers["commands"].cleared == 1 and containers["events"].cleared == 1
    assert tab._items_panels["commands"].built == [containers["commands"]]
    assert type(tab._qg_panels["quotes"]).__name__ == "QuotesPanel"
    assert type(tab._qg_panels["greetings"]).__name__ == "GreetingsPanel"
    assert type(tab._gp_panels["giveaways"]).__name__ == "GiveawaysPanel"
    assert type(tab._gp_panels["polls"]).__name__ == "PollsPanel"
    assert tab._gp_panels["polls"].built == [containers["polls"]]

    # A second load reuses the existing panels (setdefault).
    quotes_panel = tab._qg_panels["quotes"]
    tab.load_chatbot_items()
    assert tab._qg_panels["quotes"] is quotes_panel and len(quotes_panel.built) == 2


def test_load_chatbot_items_skips_missing_containers(clean_globals):
    tab._items_panels["commands"] = FakePanel()
    tab.load_chatbot_items()  # nothing initialized: nothing built, no error
    assert tab._items_panels["commands"].built == []
    assert tab._qg_panels == {} and tab._gp_panels == {}


def test_load_chatbot_items_shows_error_label_when_panel_fails(monkeypatch, clean_globals):
    class Boom(FakePanel):
        def build(self, container):
            raise RuntimeError("kaput")

    monkeypatch.setattr(tab, "GiveawaysPanel", Boom)
    monkeypatch.setattr(tab, "PollsPanel", FakePanel)

    def build():
        c = ui.column()
        tab.giveaways_container = c
        tab.polls_container = ui.column()
        tab.load_chatbot_items()

    async def check(user):
        await user.should_see("Error loading giveaways: kaput")

    run_ui(build, check)


# --------------------------------------------------------------------------- open dialogs


def test_show_create_dialog_routes_quotes_and_greetings(monkeypatch, refreshes):
    seen = []
    monkeypatch.setattr(tab, "open_quote_editor", lambda item, cb: seen.append(("quote", item, cb)))
    monkeypatch.setattr(tab, "open_greeting_editor", lambda item, cb: seen.append(("greeting", item, cb)))
    monkeypatch.setattr(tab, "show_chatbot_dialog", lambda **kw: seen.append(("dialog", kw)))

    tab.show_create_chatbot_dialog("quote")
    tab.show_create_chatbot_dialog("greeting")
    tab.show_create_chatbot_dialog("command")

    assert seen[0][:2] == ("quote", None)
    assert seen[1][:2] == ("greeting", None)
    assert seen[2] == ("dialog", {"item_type": "command"})
    seen[0][2]()
    seen[1][2]()
    assert refreshes == ["quotes", "greetings"]


def test_show_chatbot_dialog_opens_editor_and_wires_refresh(monkeypatch, clean_globals, refreshes):
    from modules.uiwindows import chatbot_editor

    opened = []

    def fake_open(kind, item_id, on_saved):
        opened.append((kind, item_id))
        on_saved()
        return SimpleNamespace(dialog=None)

    monkeypatch.setattr(chatbot_editor, "open_chatbot_editor", fake_open)
    tab.show_chatbot_dialog("x1", "event")
    tab.show_chatbot_dialog(None, None)
    assert opened == [("event", "x1"), ("command", None)]
    assert refreshes == ["events", "commands"]


def test_show_chatbot_dialog_deletes_previous_editor_dialog(monkeypatch, clean_globals):
    from modules.uiwindows import chatbot_editor

    deleted = []

    class Dlg:
        def __init__(self, fail=False):
            self.fail = fail

        def delete(self):
            deleted.append(self)
            if self.fail:
                raise RuntimeError("already gone")

    first = Dlg(fail=True)
    monkeypatch.setattr(tab, "_active_editor", SimpleNamespace(dialog=first))
    new = SimpleNamespace(dialog=Dlg())
    monkeypatch.setattr(chatbot_editor, "open_chatbot_editor", lambda *a, **k: new)
    tab.show_chatbot_dialog("i", "command")  # the failing delete is swallowed
    assert deleted == [first]
    assert tab._active_editor is new


def test_show_chatbot_dialog_reports_open_failure(monkeypatch, clean_globals, notes):
    from modules.uiwindows import chatbot_editor

    def boom(*a, **k):
        raise ValueError("bad item")

    monkeypatch.setattr(chatbot_editor, "open_chatbot_editor", boom)
    tab.show_chatbot_dialog("i", "event")
    assert notes.types == ["error"]
    assert notes.titles == ["Couldn't open the event editor"]
    assert notes.calls[0][1]["details"] == "ValueError: bad item"


def test_show_edit_chatbot_dialog_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(tab, "show_chatbot_dialog", lambda *a: seen.append(a))
    tab.show_edit_chatbot_dialog("id9", "event")
    assert seen == [("id9", "event")]


@pytest.mark.parametrize("given,expected", [("event", "event"), ("command", "command"), ("other", "command")])
def test_test_chatbot_item_runs_editor_test(monkeypatch, given, expected):
    from modules.uiwindows import chatbot_editor

    seen = []
    monkeypatch.setattr(chatbot_editor, "run_item_test", lambda kind, iid: seen.append((kind, iid)))
    tab.test_chatbot_item("abc", given)
    assert seen == [(expected, "abc")]


def test_close_dialog_and_refresh(monkeypatch):
    closed, refreshed = [], []
    monkeypatch.setattr(tab, "create_dialog", SimpleNamespace(close=lambda: closed.append(1)))
    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))
    tab.close_dialog_and_refresh()
    assert closed == [1] and refreshed == [1]


def test_close_dialog_and_refresh_without_dialog_and_with_error(monkeypatch, caplog):
    monkeypatch.setattr(tab, "create_dialog", None)

    def boom():
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(tab, "refresh_chatbot_items", boom)
    tab.close_dialog_and_refresh()  # does not raise
    assert "refresh failed" in caplog.text


def test_close_edit_dialog(monkeypatch):
    closed = []
    monkeypatch.setattr(tab, "edit_dialog", SimpleNamespace(close=lambda: closed.append(1)))
    tab.close_edit_dialog()
    assert closed == [1] and tab.edit_dialog is None
    tab.close_edit_dialog()  # no dialog: nothing happens
    assert closed == [1]


# --------------------------------------------------------------------------- labels / toggle


def test_item_label_variants():
    m = FakeManager()
    m.commands["c"] = _cmd("c", word="hello")
    m.commands["n"] = _cmd("n", name="Named", word="")
    m.commands["b"] = _cmd("b", name="", word="")
    m.events["e"] = _evt("e", "Raid alert")
    m.events["e2"] = _evt("e2", "")
    m.quotes["q"] = Quote(quote_id="q", quote_number=4, text="t")
    assert tab._item_label(m, "c") == "!hello"
    assert tab._item_label(m, "n") == "Named"
    assert tab._item_label(m, "b") == "command"
    assert tab._item_label(m, "e") == "Raid alert"
    assert tab._item_label(m, "e2") == "event"
    assert tab._item_label(m, "q") == "quote #4"
    assert tab._item_label(m, "missing") == ""


@pytest.mark.parametrize("enabled,word", [(True, "on"), (False, "off")])
def test_toggle_success_notifies_and_refreshes(monkeypatch, mgr, notes, enabled, word):
    refreshed = []
    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))
    mgr.commands["c"] = _cmd("c", word="hi")
    tab.toggle_chatbot_item("c", enabled)
    assert mgr.calls == [("toggle", "c", enabled)]
    assert notes.messages == [f"“!hi” turned {word}"]
    assert notes.types == ["success"] and refreshed == [1]


def test_toggle_missing_item_is_info(monkeypatch, mgr, notes):
    refreshed = []
    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))
    mgr.toggle_result = False
    tab.toggle_chatbot_item("gone", True)
    assert notes.types == ["info"] and "no longer exists" in notes.messages[0]
    assert refreshed == [1]


def test_toggle_error_notifies_and_restores(monkeypatch, mgr, notes):
    refreshed = []
    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))
    mgr.toggle_result = RuntimeError("db locked")
    tab.toggle_chatbot_item("c", False)
    assert notes.types == ["error"]
    assert notes.titles == ["Couldn't turn the item off"]
    assert notes.calls[0][1]["details"] == "RuntimeError: db locked"
    assert refreshed == [1]


# --------------------------------------------------------------------------- duplicate


def test_duplicate_command_resets_counters_and_clears_aliases(mgr, notes, refreshes):
    src = _cmd("c1", name="Hello", word="hello", aliases=["hey"], counter_value=9, usage_count=3)
    other = _cmd("c2", name="Hello copy", word="hello_copy", aliases=["x"])
    mgr.commands = {"c1": src, "c2": other}
    tab.duplicate_chatbot_item("c1", "command")
    kind, new = mgr.calls[0]
    assert kind == "add_command"
    assert new.command_id not in ("c1", "c2") and len(new.command_id) == 36
    assert new.name == "Hello copy 2"
    assert new.command_name == "hello_copy2"
    assert new.aliases == []
    assert new.usage_count == 0 and new.counter_value == 0
    assert notes.titles == ["Duplicated as !hello_copy2"]
    assert refreshes == ["commands"]


def test_duplicate_command_avoids_alias_collisions(mgr, notes, refreshes):
    mgr.commands = {"c1": _cmd("c1", word="hi"), "c2": _cmd("c2", name="Other", word="other", aliases=["hi_copy"])}
    tab.duplicate_chatbot_item("c1", "command")
    assert mgr.calls[0][1].command_name == "hi_copy2"


def test_duplicate_command_add_failure(mgr, notes, refreshes):
    mgr.commands = {"c1": _cmd("c1")}
    mgr.add_result = False
    tab.duplicate_chatbot_item("c1", "command")
    assert notes.titles == ["Couldn't duplicate the command"] and notes.types == ["error"]
    assert refreshes == []


@pytest.mark.parametrize("kind,tabname", [("command", "commands"), ("event", "events"), ("quote", "quotes")])
def test_duplicate_missing_source(mgr, notes, refreshes, kind, tabname):
    tab.duplicate_chatbot_item("missing", kind)
    assert notes.types == ["info"]
    assert refreshes == [tabname]


def test_duplicate_event(mgr, notes, refreshes):
    src = _evt("e1", "Follow")
    src.trigger_count = 5
    mgr.events = {"e1": src}
    tab.duplicate_chatbot_item("e1", "event")
    kind, new = mgr.calls[0]
    assert kind == "add_event"
    assert new.name == "Follow copy" and new.trigger_count == 0 and new.event_id != "e1"
    assert notes.messages == ["Duplicated as “Follow copy”"]
    assert refreshes == ["events"]


def test_duplicate_event_add_failure(mgr, notes, refreshes):
    mgr.events = {"e1": _evt()}
    mgr.add_result = False
    tab.duplicate_chatbot_item("e1", "event")
    assert notes.titles == ["Couldn't duplicate the event"]


def test_duplicate_quote(mgr, notes, refreshes):
    q = Quote(quote_id="q1", quote_number=1, text="wise", author="me", added_by="mod")
    mgr.quotes = {"q1": q}
    tab.duplicate_chatbot_item("q1", "quote")
    assert mgr.calls == [("add_quote", "wise", "me", "mod")]
    assert notes.messages == ["Duplicated as quote #7"]
    assert refreshes == ["quotes"]


def test_duplicate_quote_without_added_by(mgr, notes, refreshes):
    q = SimpleNamespace(text="t", author="a")
    mgr.quotes = {"q1": q}
    tab.duplicate_chatbot_item("q1", "quote")
    assert mgr.calls == [("add_quote", "t", "a", "")]


def test_duplicate_quote_failure_passes_reason(mgr, notes, refreshes):
    mgr.quotes = {"q1": Quote(quote_id="q1", text="t")}
    mgr.add_quote_result = (False, "Quote already exists", None)
    tab.duplicate_chatbot_item("q1", "quote")
    assert notes.messages == ["Quote already exists"]
    assert notes.titles == ["Couldn't duplicate the quote"]


def test_duplicate_unsupported_type_logs(mgr, notes, caplog):
    tab.duplicate_chatbot_item("x", "greeting")
    assert "unsupported" in caplog.text
    assert notes.calls == []


def test_duplicate_exception_notifies(monkeypatch, notes):
    def boom():
        raise KeyError("manager")

    monkeypatch.setattr(tab, "get_chatbot_manager", boom)
    tab.duplicate_chatbot_item("x", "command")
    assert notes.titles == ["Couldn't duplicate the command"]
    assert notes.calls[0][1]["details"].startswith("KeyError")


def test_notify_duplicate_failed_defaults(notes):
    tab._notify_duplicate_failed("event")
    msg, kw = notes.calls[0]
    assert msg == "" and kw["details"] is None and kw["source"] == "chatbot"


# --------------------------------------------------------------------------- reset counter dialog


def _dialog_test(build_action, click, monkeypatch, refreshed):
    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))

    def build():
        ui.label("host")
        build_action()

    async def check(user):
        for text in click:
            user.find(kind=ui.button, content=text).click()
            await asyncio.sleep(0)

    run_ui(build, check)


def test_reset_counter_dialog_resets(monkeypatch, mgr, notes):
    mgr.commands["c"] = _cmd("c", word="deaths")
    refreshed = []
    shown = []

    def build():
        tab.reset_command_counter("c")

    async def check(user):
        await user.should_see("Reset command counter?")
        shown.append(True)
        user.find(kind=ui.button, content="Reset").click()

    monkeypatch.setattr(tab, "refresh_chatbot_items", lambda: refreshed.append(1))
    run_ui(build, check)
    assert shown
    assert mgr.calls == [("reset", "c")]
    assert notes.messages == ["Counter for “!deaths” reset to 0"]
    assert refreshed == [1]


def test_reset_counter_dialog_missing_command(monkeypatch, mgr, notes):
    mgr.reset_result = False
    refreshed = []
    _dialog_test(lambda: tab.reset_command_counter("gone"), ["Reset"], monkeypatch, refreshed)
    assert notes.types == ["info"] and refreshed == [1]


def test_reset_counter_dialog_error(monkeypatch, mgr, notes):
    mgr.reset_result = RuntimeError("nope")
    refreshed = []
    _dialog_test(lambda: tab.reset_command_counter("c"), ["Reset"], monkeypatch, refreshed)
    assert notes.titles == ["Couldn't reset the counter"]
    assert refreshed == []


def test_reset_counter_dialog_cancel(monkeypatch, mgr, notes):
    refreshed = []
    _dialog_test(lambda: tab.reset_command_counter("c"), ["Cancel"], monkeypatch, refreshed)
    assert mgr.calls == [] and notes.calls == []


# --------------------------------------------------------------------------- delete dialog


@pytest.mark.parametrize("kind,call", [
    ("command", "remove_command"), ("event", "remove_event"), ("quote", "delete_quote"),
])
def test_delete_dialog_deletes_each_kind(monkeypatch, mgr, notes, kind, call):
    refreshed = []
    _dialog_test(lambda: tab.delete_chatbot_item("x", kind), ["Delete"], monkeypatch, refreshed)
    assert mgr.calls == [(call, "x")]
    assert notes.messages == [f"Deleted “{kind}”"]
    assert refreshed == [1]


def test_delete_dialog_uses_item_label(monkeypatch, mgr, notes):
    mgr.commands["x"] = _cmd("x", word="bye")
    refreshed = []
    _dialog_test(lambda: tab.delete_chatbot_item("x", "command"), ["Delete"], monkeypatch, refreshed)
    assert notes.messages == ["Deleted “!bye”"]


def test_delete_dialog_already_gone(monkeypatch, mgr, notes):
    mgr.remove_result = False
    refreshed = []
    _dialog_test(lambda: tab.delete_chatbot_item("x", "event"), ["Delete"], monkeypatch, refreshed)
    assert notes.types == ["info"] and refreshed == [1]


def test_delete_dialog_error(monkeypatch, mgr, notes):
    mgr.remove_result = RuntimeError("locked")
    refreshed = []
    _dialog_test(lambda: tab.delete_chatbot_item("x", "command"), ["Delete"], monkeypatch, refreshed)
    assert notes.titles == ["Couldn't delete the command"]
    assert refreshed == []


def test_delete_dialog_shows_confirmation_and_cancel(monkeypatch, mgr, notes):
    def build():
        tab.delete_chatbot_item("x", "event")

    async def check(user):
        await user.should_see("Confirm Delete")
        await user.should_see("Are you sure you want to delete this event? This action cannot be undone.")
        user.find(kind=ui.button, content="Cancel").click()

    run_ui(build, check)
    assert mgr.calls == []


# --------------------------------------------------------------------------- twitch lookup


class FakeTwitch:
    def __init__(self, connected=True, response=None):
        self.is_connected = connected
        self.response = response if response is not None else {"data": [{"id": "42", "display_name": "Bob"}]}
        self.calls = []

    async def generic_api_call(self, url, method, params=None):
        self.calls.append((url, method, params))
        return self.response


def _patch_twitch(monkeypatch, api):
    import modules.twitch as twitch_mod

    monkeypatch.setattr(twitch_mod, "get_twitch_api", lambda: api)


def test_lookup_twitch_user_found(monkeypatch):
    api = FakeTwitch()
    _patch_twitch(monkeypatch, api)
    assert asyncio.run(tab.lookup_twitch_user("BoB")) == ("42", "Bob")
    assert api.calls == [("https://api.twitch.tv/helix/users", "GET", {"login": "bob"})]


@pytest.mark.parametrize("response", [{"data": []}, {}])
def test_lookup_twitch_user_not_found(monkeypatch, response):
    _patch_twitch(monkeypatch, FakeTwitch(response=response))
    with pytest.raises(Exception, match="not found"):
        asyncio.run(tab.lookup_twitch_user("ghost"))


@pytest.mark.parametrize("api", [None, FakeTwitch(connected=False)])
def test_lookup_twitch_user_not_connected(monkeypatch, api):
    _patch_twitch(monkeypatch, api)
    with pytest.raises(Exception, match="not connected"):
        asyncio.run(tab.lookup_twitch_user("bob"))


# --------------------------------------------------------------------------- full tab


def test_create_chatbot_tab_builds_all_sub_tabs(monkeypatch, clean_globals, mgr):
    refs = []
    monkeypatch.setattr(tab, "set_chatbot_ui_references", lambda tabs: refs.append(tabs))
    for name in ("QuotesPanel", "GreetingsPanel", "GiveawaysPanel", "PollsPanel"):
        monkeypatch.setattr(tab, name, FakePanel)
    loaded = []
    monkeypatch.setattr(tab, "load_custom_variables", lambda: loaded.append(1) or {"a": 1})
    mgr.commands["c1"] = _cmd("c1", word="dice")

    actions = {}

    class SpyItemsPanel(FakePanel):
        def __init__(self, kind, items, actions_):
            super().__init__()
            actions[kind] = (items, actions_)

    monkeypatch.setattr(tab, "ItemsPanel", SpyItemsPanel)

    async def check(user):
        for label in ("Commands", "Events", "Quotes", "Greetings", "Giveaways", "Polls"):
            await user.should_see(label)

    run_ui(tab.create_chatbot_tab, check)

    assert len(refs) == 1
    assert loaded == [1]
    for kind in ("commands", "events", "quotes", "greetings", "giveaways", "polls"):
        assert tab.get_container_for_tab(kind) is not None
    assert tab._items_panels["commands"].built and tab._items_panels["events"].built
    assert set(tab._qg_panels) == {"quotes", "greetings"}
    assert set(tab._gp_panels) == {"giveaways", "polls"}

    # The item providers read the manager.
    items_cmds, cmd_actions = actions["commands"]
    items_evts, evt_actions = actions["events"]
    assert "c1" in items_cmds()
    assert items_evts() == {}
    assert cmd_actions.toggle is tab.toggle_chatbot_item
    assert cmd_actions.reset_counter is tab.reset_command_counter


def test_create_chatbot_tab_action_lambdas_route_correctly(monkeypatch, clean_globals, mgr):
    monkeypatch.setattr(tab, "set_chatbot_ui_references", lambda tabs: None)
    for name in ("QuotesPanel", "GreetingsPanel", "GiveawaysPanel", "PollsPanel"):
        monkeypatch.setattr(tab, name, FakePanel)
    monkeypatch.setattr(tab, "load_custom_variables", lambda: {})
    captured = {}

    class SpyItemsPanel(FakePanel):
        def __init__(self, kind, items, actions_):
            super().__init__()
            captured[kind] = actions_

    monkeypatch.setattr(tab, "ItemsPanel", SpyItemsPanel)
    run_ui(tab.create_chatbot_tab)

    seen = []
    monkeypatch.setattr(tab, "show_create_chatbot_dialog", lambda t: seen.append(("new", t)))
    monkeypatch.setattr(tab, "show_edit_chatbot_dialog", lambda i, t: seen.append(("edit", i, t)))
    monkeypatch.setattr(tab, "test_chatbot_item", lambda i, t: seen.append(("test", i, t)))
    monkeypatch.setattr(tab, "duplicate_chatbot_item", lambda i, t: seen.append(("dup", i, t)))
    monkeypatch.setattr(tab, "delete_chatbot_item", lambda i, t: seen.append(("del", i, t)))
    monkeypatch.setattr(tab, "refresh_tab_content", lambda t: seen.append(("refresh", t)))
    for kind, single in (("commands", "command"), ("events", "event")):
        a = captured[kind]
        a.new()
        a.edit("i")
        a.test("i")
        a.duplicate("i")
        a.delete("i")
        a.refresh()
        assert seen[-6:] == [("new", single), ("edit", "i", single), ("test", "i", single),
                             ("dup", "i", single), ("del", "i", single), ("refresh", kind)]


def test_create_chatbot_tab_survives_custom_variable_load_error(monkeypatch, clean_globals, mgr, caplog):
    monkeypatch.setattr(tab, "set_chatbot_ui_references", lambda tabs: None)
    for name in ("ItemsPanel", "QuotesPanel", "GreetingsPanel", "GiveawaysPanel", "PollsPanel"):
        monkeypatch.setattr(tab, name, FakePanel)

    def boom():
        raise OSError("disk")

    monkeypatch.setattr(tab, "load_custom_variables", boom)

    async def check(user):
        await user.should_see("Commands")

    run_ui(tab.create_chatbot_tab, check)
    assert "Error loading custom variables" in caplog.text


def test_create_chatbot_tab_with_real_items_panel(monkeypatch, clean_globals, mgr):
    """The real card list renders a command from the manager."""
    monkeypatch.setattr(tab, "set_chatbot_ui_references", lambda tabs: None)
    for name in ("QuotesPanel", "GreetingsPanel", "GiveawaysPanel", "PollsPanel"):
        monkeypatch.setattr(tab, name, FakePanel)
    monkeypatch.setattr(tab, "load_custom_variables", lambda: {})
    mgr.commands["c1"] = _cmd("c1", name="Dice roller", word="dice")

    async def check(user):
        await user.should_see("Commands")
        html = " ".join(str(getattr(e, "content", "")) for e in user.find(ui.html).elements)
        assert "dice" in html

    run_ui(tab.create_chatbot_tab, check)

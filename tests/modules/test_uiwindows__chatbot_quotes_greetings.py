# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_quotes_greetings.py: Quotes and Greetings panels and dialogs."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
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
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules.chatbot_core import Greeting, Quote  # noqa: E402
from modules.uiwindows import chatbot_quotes_greetings as qg  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeManager:
    def __init__(self):
        self.quotes = {}
        self.greetings = {}
        self.calls = []
        self.quotes_enabled = True
        self.cooldown = 0
        self.add_quote_permission = "moderator"
        self.quote_reply_format = ""
        self.save_quote_ok = True
        self.add_quote_ok = True
        self.update_quote_ok = True
        self.delete_ok = True
        self.gaps = []
        self.renumbered = 2
        # greetings
        self.default_enabled = True
        self.custom_enabled = True
        self.default_text = "Welcome {username}!"
        self.reset_hours = 24
        self.greeted = {"count": 3, "since": 0}
        self.save_greeting_ok = True
        self.reset_one_ok = True
        self.remove_greeting_ok = True
        self.update_greeting_ok = True
        self.add_greeting_result = (True, "", "g-new")
        self.render_fail = False

    # quotes
    def get_all_quotes(self):
        return dict(self.quotes)

    def get_quotes_enabled(self):
        return self.quotes_enabled

    def get_quote_cooldown(self):
        return self.cooldown

    def format_quote_reply(self, quote, template=None):
        fmt = template if template is not None else (self.quote_reply_format or qg.DEFAULT_QUOTE_REPLY_FORMAT)
        return fmt.replace("{number}", str(quote.quote_number)).replace("{text}", quote.text).replace(
            "{author}", quote.author or "")

    def save_quote_settings(self, **kw):
        self.calls.append(("save_quote_settings", kw))
        return self.save_quote_ok

    def add_quote(self, text, author, added_by):
        self.calls.append(("add_quote", text, author, added_by))
        if not self.add_quote_ok:
            return False, "Duplicate quote", None
        number = max([q.quote_number for q in self.quotes.values()] or [0]) + 1
        q = Quote(quote_id=f"q{number}", quote_number=number, text=text, author=author, added_by=added_by)
        self.quotes[q.quote_id] = q
        return True, "", number

    def update_quote(self, quote_id, text=None, author=None, number=None, added_by=None):
        self.calls.append(("update_quote", quote_id, text, author, number, added_by))
        return self.update_quote_ok

    def get_quote_by_id(self, qid):
        return self.quotes.get(qid)

    def get_quote_by_number(self, number):
        return next((q for q in self.quotes.values() if q.quote_number == number), None)

    def delete_quote(self, qid):
        self.calls.append(("delete_quote", qid))
        return self.delete_ok

    def quote_number_gaps(self):
        return list(self.gaps)

    def renumber_quotes(self):
        self.calls.append(("renumber",))
        return self.renumbered

    # greetings
    def get_default_greeting_enabled(self):
        return self.default_enabled

    def get_custom_greeting_enabled(self):
        return self.custom_enabled

    def get_default_greeting(self):
        return self.default_text

    def get_greeting_reset_interval(self):
        return self.reset_hours

    def greeted_since_reset(self):
        return dict(self.greeted)

    def save_greeting_settings(self, **kw):
        self.calls.append(("save_greeting_settings", kw))
        return self.save_greeting_ok

    def reset_all_greeting_flags(self):
        self.calls.append(("reset_all",))

    def get_all_greetings(self):
        return dict(self.greetings)

    def update_greeting(self, gid, text=None, enabled=None):
        self.calls.append(("update_greeting", gid, text, enabled))
        return self.update_greeting_ok

    def reset_greeting_for(self, gid):
        self.calls.append(("reset_one", gid))
        return self.reset_one_ok

    def remove_greeting(self, gid):
        self.calls.append(("remove_greeting", gid))
        return self.remove_greeting_ok

    def get_greeting(self, gid):
        return self.greetings.get(gid)

    def get_greeting_by_user_id(self, uid):
        return next((g for g in self.greetings.values() if g.user_id == uid), None)

    def add_greeting(self, user_id, username, text, enabled):
        self.calls.append(("add_greeting", user_id, username, text, enabled))
        return self.add_greeting_result

    def render_greeting(self, text, user_id, username, login=""):
        if self.render_fail:
            raise RuntimeError("render failed")
        return (text or "").replace("{username}", username).replace("{user_login}", login)


class Notes:
    def __init__(self):
        self.calls = []

    def __call__(self, message="", **kwargs):
        self.calls.append((message, kwargs))

    @property
    def messages(self):
        return [m for m, _ in self.calls]

    @property
    def types(self):
        return [k.get("type") for _, k in self.calls]

    @property
    def titles(self):
        return [k.get("title") for _, k in self.calls]


class Tasks:
    """Stand-in for nicegui.background_tasks: keeps coroutines so a test can await them."""

    def __init__(self):
        self.pending = []

    def create(self, coro, name=None):
        self.pending.append((name, coro))

    async def drain(self):
        while self.pending:
            _name, coro = self.pending.pop(0)
            await coro

    def close(self):
        for _name, coro in self.pending:
            coro.close()
        self.pending.clear()


@pytest.fixture
def mgr(monkeypatch):
    m = FakeManager()
    monkeypatch.setattr(qg, "get_manager", lambda: m)
    return m


@pytest.fixture
def notes(monkeypatch):
    n = Notes()
    monkeypatch.setattr(qg, "notify", n)
    return n


@pytest.fixture
def tasks(monkeypatch):
    t = Tasks()
    monkeypatch.setattr(qg, "background_tasks", t)
    yield t
    t.close()


@pytest.fixture
def usage(monkeypatch):
    data = {}
    monkeypatch.setattr(qg, "quote_usage", lambda: data)
    return data


def _q(n, text="a quote", author="Bob", added_by="mod", date=None):
    return Quote(quote_id=f"q{n}", quote_number=n, text=text, author=author, added_by=added_by,
                 date_added=date if date is not None else 1_700_000_000 + n)


def _g(gid="g1", username="Alice", text="Hi {username}", enabled=True, last=0, user_id="u1"):
    return Greeting(greeting_id=gid, user_id=user_id, username=username, greeting_text=text,
                    enabled=enabled, last_greeted=last)


def buttons(user, icon=None, text=None):
    found = []
    for el in user.find(kind=ui.button).elements:
        if icon is not None and el.props.get("icon") != icon:
            continue
        if text is not None and getattr(el, "text", None) != text:
            continue
        found.append(el)
    return sorted(found, key=lambda e: e.id)


def click(user, element):
    UserInteraction(user, {element}, None).click()


def labels(user):
    return [e.text for e in user.find(kind=ui.label).elements]


def inputs(user):
    """Single-line inputs only (ui.textarea is a ui.input subclass)."""
    return sorted((e for e in user.find(kind=ui.input).elements if not isinstance(e, ui.textarea)), key=lambda e: e.id)


def menu_click(user, text):
    """Click the ui.menu_item whose label is *text*."""
    for el in sorted(user.find(text).elements, key=lambda e: e.id):
        node = el
        while node is not None and not isinstance(node, ui.menu_item):
            node = node.parent_slot.parent if node.parent_slot else None
        if node is not None:
            click(user, node)
            return
    raise AssertionError(f"no menu item {text!r}")


def label_parent_click(user, text):
    label = [e for e in user.find(kind=ui.label).elements if e.text == text][0]
    click(user, label.parent_slot.parent)


def set_value(user, element, value):
    with user.client:
        element.set_value(value)


# --------------------------------------------------------------------------- pure helpers


def test_icon_and_esc():
    assert qg._icon("timer") == '<span class="cx-icon">timer</span>'
    assert qg._esc("<a & b>") == "&lt;a &amp; b&gt;"
    assert qg._esc(None) == ""
    assert qg._esc(5) == "5"


def test_avatar_color_is_stable_and_case_insensitive():
    assert qg._avatar_color("Bob") == qg._avatar_color("bob")
    assert qg._avatar_color("") == qg._avatar_color("?")
    assert qg._avatar_color(None) in qg._AVATAR_COLORS
    colors = {qg._avatar_color(n) for n in ("a", "b", "c", "d", "e", "f", "g", "h")}
    assert len(colors) == 8


def test_date_formats():
    assert qg._date(0) == "Date unknown"
    assert qg._date(None) == "Date unknown"
    now = datetime.now()
    assert qg._date(now.timestamp()) == f"{now:%b} {now.day}"
    old = datetime(2001, 3, 9, 12).timestamp()
    assert qg._date(old) == "Mar 9, 2001"


def test_constants():
    assert qg._PERMISSION_LABEL == {"everyone": "Everyone", "subscriber": "Subscribers", "vip": "VIPs", "moderator": "Mods"}
    assert set(qg._SORTS) == {"newest", "oldest", "number", "used"}
    assert qg._LOGIN_RE.match("abc_123") and not qg._LOGIN_RE.match("ab") and not qg._LOGIN_RE.match("a b c")
    for css in (qg.QG_CSS, qg.QG_DIALOG_CSS):
        assert css.count("{") == css.count("}")


def test_quote_usage_reads_statistics(monkeypatch):
    from modules import statistics_manager as sm

    fake = SimpleNamespace(data=SimpleNamespace(quotes=SimpleNamespace(individual_quote_usage={"q1": 3})))
    monkeypatch.setattr(sm, "get_statistics_manager", lambda: fake)
    assert qg.quote_usage() == {"q1": 3}
    fake.data.quotes.individual_quote_usage = None
    assert qg.quote_usage() == {}


def test_quote_usage_returns_empty_on_error(monkeypatch):
    from modules import statistics_manager as sm

    def boom():
        raise RuntimeError("no stats")

    monkeypatch.setattr(sm, "get_statistics_manager", boom)
    assert qg.quote_usage() == {}


def test_greeting_variables_includes_viewer_and_common(monkeypatch):
    from modules import chatbot_variables as cv

    monkeypatch.setattr(cv, "COMMON_VARIABLES", [
        {"key": "username", "group": "Basics"}, {"key": "time", "group": "Time"},
        {"key": "followers", "group": "Stats"}, {"key": "user_login", "group": "Basics"},
    ])
    monkeypatch.setattr(cv, "custom_variables", lambda: [{"key": "custom_x", "group": "Mine"}])
    keys = [(v["key"], v["group"]) for v in qg.greeting_variables()]
    assert keys[:3] == [("username", "Viewer"), ("user_login", "Viewer"), ("user_id", "Viewer")]
    assert keys[3:] == [("time", "Time"), ("custom_x", "Custom"), ("followers", "Stats")]


def test_greeting_variables_survives_errors(monkeypatch):
    from modules import chatbot_variables as cv

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(cv, "custom_variables", boom)
    out = qg.greeting_variables()
    assert [v["key"] for v in out[:3]] == ["username", "user_login", "user_id"]


def test_render_greeting_preview(mgr):
    assert qg.render_greeting_preview("Hi {username} @{user_login}", "Bob") == "Hi Bob @bob"
    assert qg.render_greeting_preview("{user_login}", "Bob", "bobby") == "bobby"
    mgr.render_fail = True
    assert qg.render_greeting_preview("raw {username}") == "raw {username}"


# --------------------------------------------------------------------------- _seg / _toolbar / _dialog_shell


def test_seg_picks_value_and_marks_it(mgr):
    picked = []

    def build():
        qg._seg(qg._PERMISSIONS, "vip", picked.append)

    async def check(user):
        await user.should_see("VIPs")
        on = [e for e in user.find(kind=ui.element).elements if "is-on" in e.classes]
        assert len(on) == 1
        label_parent_click(user, "Mods")
        assert picked == ["moderator"]
        on = [e for e in user.find(kind=ui.element).elements if "is-on" in e.classes]
        assert len(on) == 1

    run_ui(build, check)


def test_toolbar_wires_search_refresh_and_new():
    seen = []

    def build():
        sub = qg._toolbar("x", "Title", "Sub text", "Search…", "New thing", lambda: seen.append("new"),
                          lambda q: seen.append(("q", q)), lambda: seen.append("refresh"),
                          extra=lambda: ui.label("EXTRA"))
        assert sub.text == "Sub text"

    async def check(user):
        await user.should_see("Title")
        await user.should_see("EXTRA")
        user.find(kind=ui.input).type("  HeLLo ")
        click(user, buttons(user, icon="refresh")[0])
        user.find(kind=ui.button, content="New thing").click()

    run_ui(build, check)
    assert ("q", "hello") in seen and "refresh" in seen and "new" in seen


def test_dialog_shell_close_button():
    holder = {}

    def build():
        dialog, body, foot = qg._dialog_shell("T", "S", "tune")
        holder["dialog"] = dialog
        with body:
            ui.label("inside")
        dialog.open()

    async def check(user):
        await user.should_see("inside")
        assert holder["dialog"].value is True
        click(user, buttons(user, icon="close")[0])
        assert holder["dialog"].value is False

    run_ui(build, check)


# --------------------------------------------------------------------------- QuotesPanel


def _build_quotes(holder):
    def build():
        panel = qg.QuotesPanel()
        holder["panel"] = panel
        panel.build(ui.column())
    return build


def test_quotes_panel_empty_state(mgr, notes, usage, monkeypatch):
    opened = []
    monkeypatch.setattr(qg, "open_quote_editor", lambda qid, cb: opened.append(qid))
    holder = {}

    async def check(user):
        await user.should_see("No quotes yet")
        await user.should_see("Quotes on")
        await user.should_see("Memorable lines viewers can call up in chat.")
        user.find(kind=ui.button, content="Add a quote").click()

    run_ui(_build_quotes(holder), check)
    assert opened == [None]


def test_quotes_panel_lists_cards_and_counts(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1, "first line", author=""), "q2": _q(2, "second line", author="Ann")}
    usage.update({"q2": 1234, "q1": 1})
    holder = {}

    async def check(user):
        await user.should_see("first line")
        await user.should_see("second line")
        await user.should_see("#1")
        await user.should_see("— Ann")
        await user.should_see("No author")
        await user.should_see("Memorable lines viewers can call up in chat. 2 quotes.")
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "1,234" in htmls and "&nbsp;times" in htmls and "&nbsp;time<" in htmls
        assert "Added by mod" in htmls
        # Strip shows the reply sample of the lowest-numbered quote.
        assert "#1: &quot;first line&quot;" in htmls
        assert "Cooldown <b>none</b>" in htmls
        assert "<b>Mods</b>" in htmls

    run_ui(_build_quotes(holder), check)


def test_quotes_panel_single_quote_and_never_used(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1)}
    mgr.cooldown = 30
    mgr.quotes_enabled = False
    mgr.add_quote_permission = "weird"
    holder = {}

    async def check(user):
        await user.should_see("Memorable lines viewers can call up in chat. 1 quote.")
        await user.should_see("Quotes off")
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "Never used" in htmls
        assert "Cooldown <b>30 s</b>" in htmls
        assert "<b>Mods</b>" in htmls  # unknown permission falls back to Mods

    run_ui(_build_quotes(holder), check)


def test_quotes_panel_search_by_text_and_number(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1, "apples"), "q2": _q(2, "bananas", added_by="Zed"), "q10": _q(10, "cherries")}
    holder = {}

    def shown(user):
        return sorted(e.text for e in user.find(kind=ui.label).elements if "qg-quote" in e.classes)

    async def check(user):
        panel = holder["panel"]
        with user.client:
            panel._set_query("banana")
        assert shown(user) == ["bananas"]
        with user.client:
            panel._set_query("#10")
        assert shown(user) == ["cherries"]
        with user.client:
            panel._set_query("zed")
        assert shown(user) == ["bananas"]
        with user.client:
            panel._set_query("nothing-matches")
        await user.should_see("No quotes match your search.")

    run_ui(_build_quotes(holder), check)


@pytest.mark.parametrize("sort,expected", [
    ("newest", ["#3", "#2", "#1"]),
    ("oldest", ["#1", "#2", "#3"]),
    ("number", ["#1", "#2", "#3"]),
    ("used", ["#2", "#3", "#1"]),
    (None, ["#3", "#2", "#1"]),
])
def test_quotes_panel_sorting(mgr, notes, usage, sort, expected):
    mgr.quotes = {"q1": _q(1, date=100), "q2": _q(2, date=200), "q3": _q(3, date=300)}
    usage.update({"q2": 10, "q3": 5})
    holder = {}

    async def check(user):
        with user.client:
            holder["panel"]._set_sort(sort)
        nums = [e.text for e in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id) if e.text.startswith("#")]
        assert nums == expected

    run_ui(_build_quotes(holder), check)


def test_quotes_panel_sort_select_changes_sort(mgr, notes, usage):
    holder = {}

    async def check(user):
        sel = user.find(kind=ui.select).elements.pop()
        set_value(user, sel, "number")
        assert holder["panel"].sort == "number"

    run_ui(_build_quotes(holder), check)


def test_quotes_panel_render_skips_when_not_built(mgr):
    panel = qg.QuotesPanel()
    panel.refresh()  # strip/grid are None: nothing to do, no error
    assert panel.query == "" and panel.sort == "newest"


@pytest.mark.parametrize("ok", [True, False])
def test_quotes_panel_quick_enable_switch(mgr, notes, usage, ok):
    mgr.save_quote_ok = ok
    mgr.cooldown = 5
    mgr.quote_reply_format = "F"
    holder = {}

    async def check(user):
        sw = user.find(kind=ui.switch).elements.pop()
        set_value(user, sw, False)

    run_ui(_build_quotes(holder), check)
    assert mgr.calls[-1] == ("save_quote_settings", {"enabled": False, "cooldown_seconds": 5,
                                                     "add_permission": "moderator", "reply_format": "F"})
    if ok:
        assert notes.messages == ["Quotes turned off"]
    else:
        assert notes.titles == ["Couldn't change the quote setting"]


def test_quotes_panel_card_buttons(mgr, notes, usage, monkeypatch):
    mgr.quotes = {"q1": _q(1, "first")}
    posted, edited, deleted, settings, clip = [], [], [], [], []
    monkeypatch.setattr(qg, "post_quote", lambda q: posted.append(q.quote_id))
    monkeypatch.setattr(qg, "open_quote_editor", lambda qid, cb: edited.append(qid))
    monkeypatch.setattr(qg, "confirm_delete_quote", lambda q, cb: deleted.append(q.quote_id))
    monkeypatch.setattr(qg, "open_quote_settings", lambda cb: settings.append(cb))
    monkeypatch.setattr(qg.ui.clipboard, "write", lambda text: clip.append(text))
    holder = {}

    async def check(user):
        click(user, buttons(user, icon="send")[0])
        click(user, buttons(user, icon="edit")[0])
        menu_click(user, "Delete…")
        menu_click(user, "Copy the quote text")
        user.find(kind=ui.button, content="Quote settings").click()
        user.find(kind=ui.button, content="New quote").click()
        click(user, buttons(user, icon="content_copy")[0])

    run_ui(_build_quotes(holder), check)
    assert posted == ["q1"]
    assert edited == ["q1", None]
    assert deleted == ["q1"]
    assert clip == ["first"]
    assert "Quote #1 copied" in notes.messages
    assert settings and settings[0] == holder["panel"].refresh
    assert ("add_quote", "first", "Bob", "mod") in mgr.calls
    assert "Duplicated as quote #2" in notes.messages


def test_quotes_panel_duplicate_failure_and_ui_fallback(mgr, notes, usage):
    mgr.add_quote_ok = False
    panel = qg.QuotesPanel()
    panel._duplicate(_q(1, added_by=""))
    assert mgr.calls == [("add_quote", "a quote", "Bob", "UI")]
    assert notes.titles == ["Couldn't duplicate the quote"]
    assert notes.messages == ["Duplicate quote"]


# --------------------------------------------------------------------------- post / delete quote


def test_post_quote_sends_and_notifies(mgr, notes, tasks, monkeypatch):
    import modules.chatbot as chatbot_mod

    sent = []
    monkeypatch.setattr(chatbot_mod, "send_chatbot_message", lambda text: sent.append(text) or True)

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(qg, "run", SimpleNamespace(io_bound=io_bound))
    qg.post_quote(_q(3, "hey", author="Z"))
    assert tasks.pending[0][0] == "post_quote"
    asyncio.run(tasks.drain())
    assert sent == ['#3: "hey" — Z']
    assert notes.messages == ["Quote #3 posted in chat"]


def test_post_quote_failure_is_silent(mgr, notes, tasks, monkeypatch):
    import modules.chatbot as chatbot_mod

    monkeypatch.setattr(chatbot_mod, "send_chatbot_message", lambda text: False)

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(qg, "run", SimpleNamespace(io_bound=io_bound))
    qg.post_quote(_q(3))
    asyncio.run(tasks.drain())
    assert notes.calls == []


@pytest.mark.parametrize("ok", [True, False])
def test_confirm_delete_quote(mgr, notes, ok):
    mgr.delete_ok = ok
    done = []
    long_text = "x" * 200

    def build():
        qg.confirm_delete_quote(_q(5, long_text), lambda: done.append(1))

    async def check(user):
        await user.should_see("Delete quote #5?")
        await user.should_see(f"“{'x' * 140}…”")
        user.find(kind=ui.button, content="Delete").click()

    run_ui(build, check)
    assert mgr.calls == [("delete_quote", "q5")]
    assert done == [1]
    assert notes.types == ["success" if ok else "info"]


def test_confirm_delete_quote_cancel_and_short_text(mgr, notes):
    def build():
        qg.confirm_delete_quote(_q(5, "short"), lambda: None)

    async def check(user):
        await user.should_see("“short”")
        user.find(kind=ui.button, content="Cancel").click()

    run_ui(build, check)
    assert mgr.calls == []


# --------------------------------------------------------------------------- quote editor


def test_quote_editor_missing_quote(mgr, notes):
    done = []

    def build():
        qg.open_quote_editor("gone", lambda: done.append(1))

    run_ui(build)
    assert notes.types == ["info"] and done == [1]


def test_quote_editor_new_quote_validation_and_save(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1), "q2": _q(2)}
    done = []

    def build():
        qg.open_quote_editor(None, lambda: done.append(1))

    async def check(user):
        await user.should_see("New quote")
        await user.should_see("Will be quote #3. Viewers can call it with !quote 3.")
        user.find(kind=ui.button, content="Add quote").click()
        await user.should_see("Write the quote.")
        user.find(kind=ui.textarea).type("  A line  ")
        fields = inputs(user)
        set_value(user, fields[0], "Ann")
        set_value(user, fields[1], "")
        await user.should_see('#3: "  A line  " — Ann')
        user.find(kind=ui.button, content="Add quote").click()

    run_ui(build, check)
    assert ("add_quote", "A line", "Ann", "UI") in mgr.calls
    assert notes.messages == ["Added quote #3"]
    assert done == [1]


def test_quote_editor_number_problems(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1)}

    def build():
        qg.open_quote_editor(None, lambda: None)

    async def check(user):
        user.find(kind=ui.textarea).type("text")
        num = user.find(kind=ui.number).elements.pop()
        set_value(user, num, 1)
        await user.should_see("#1 is already used by another quote.")
        set_value(user, num, 0)
        await user.should_see("Use a number of 1 or more.")
        user.find(kind=ui.button, content="Add quote").click()
        set_value(user, num, None)
        await user.should_see("Use a number of 1 or more.")

    run_ui(build, check)
    assert not any(c[0] == "add_quote" for c in mgr.calls)


def test_quote_editor_new_with_custom_number_renumbers(mgr, notes, usage):
    def build():
        qg.open_quote_editor(None, lambda: None)

    async def check(user):
        await user.should_see("Will be quote #1. Viewers can call it with !quote 1.")
        user.find(kind=ui.textarea).type("hello")
        set_value(user, user.find(kind=ui.number).elements.pop(), 9)
        user.find(kind=ui.button, content="Add quote").click()

    run_ui(build, check)
    assert ("update_quote", "q1", None, None, 9, None) in mgr.calls
    assert notes.messages == ["Added quote #9"]


def test_quote_editor_new_save_failure(mgr, notes, usage):
    mgr.add_quote_ok = False

    def build():
        qg.open_quote_editor(None, lambda: None)

    async def check(user):
        user.find(kind=ui.textarea).type("hello")
        user.find(kind=ui.button, content="Add quote").click()
        await user.should_see("Couldn't save the quote. Try again.")

    run_ui(build, check)
    assert notes.calls == []


def test_quote_editor_renumber_fails_when_added_quote_missing(mgr, notes, usage, monkeypatch):
    monkeypatch.setattr(mgr, "get_quote_by_number", lambda n: None)

    def build():
        qg.open_quote_editor(None, lambda: None)

    async def check(user):
        user.find(kind=ui.textarea).type("hello")
        set_value(user, user.find(kind=ui.number).elements.pop(), 4)
        user.find(kind=ui.button, content="Add quote").click()
        await user.should_see("Couldn't save the quote. Try again.")

    run_ui(build, check)


def test_quote_editor_edit_existing(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1, "old text", author="A", added_by="M"), "q2": _q(2)}
    usage["q1"] = 1
    done = []

    def build():
        qg.open_quote_editor("q1", lambda: done.append(1))

    async def check(user):
        await user.should_see("Edit quote #1")
        await user.should_see(f"Added {qg._date(mgr.quotes['q1'].date_added)} · used 1 time")
        area = user.find(kind=ui.textarea).elements.pop()
        assert area.value == "old text"
        set_value(user, area, "new text")
        user.find(kind=ui.button, content="Save").click()

    run_ui(build, check)
    assert ("update_quote", "q1", "new text", "A", 1, "M") in mgr.calls
    assert notes.messages == ["Quote #1 saved"]
    assert done == [1]


def test_quote_editor_edit_existing_failure_and_ctrl_enter(mgr, notes, usage):
    mgr.quotes = {"q1": _q(1)}
    mgr.update_quote_ok = False

    def build():
        qg.open_quote_editor("q1", lambda: None)

    async def check(user):
        await user.should_see("used 0 times")
        user.find(kind=ui.textarea).trigger("keydown.ctrl.enter")
        await user.should_see("Couldn't save the quote. Try again.")

    run_ui(build, check)


def test_quote_editor_delete_button_opens_confirm(mgr, notes, usage, monkeypatch):
    mgr.quotes = {"q1": _q(1)}
    seen = []
    monkeypatch.setattr(qg, "confirm_delete_quote", lambda q, cb: seen.append(q.quote_id))

    def build():
        qg.open_quote_editor("q1", lambda: None)

    async def check(user):
        user.find(kind=ui.button, content="Delete").click()

    run_ui(build, check)
    assert seen == ["q1"]


def test_quote_editor_preview_hidden_when_blank(mgr, notes, usage):
    def build():
        qg.open_quote_editor(None, lambda: None)

    async def check(user):
        hidden = [e for e in user.find(kind=ui.element).elements if "ce-preview" in e.classes]
        assert hidden == []  # invisible elements are not found

    run_ui(build, check)


# --------------------------------------------------------------------------- quote settings


def test_quote_settings_save(mgr, notes, monkeypatch):
    mgr.quotes = {"q1": _q(1, author=""), "q2": _q(2, author="Auth")}
    done = []

    def build():
        qg.open_quote_settings(lambda: done.append(1))

    async def check(user):
        await user.should_see("Quote settings")
        await user.should_see('#2: "a quote" — Auth')  # sample prefers a quote with an author
        sw = user.find(kind=ui.switch).elements.pop()
        set_value(user, sw, False)
        set_value(user, user.find(kind=ui.number).elements.pop(), 15)
        label_parent_click(user, "VIPs")
        area = user.find(kind=ui.textarea).elements.pop()
        set_value(user, area, "Q{number}")
        await user.should_see("Q2")
        user.find("Reset to default").click()
        assert area.value == qg.DEFAULT_QUOTE_REPLY_FORMAT
        set_value(user, area, "")
        await user.should_see('#2: "a quote" — Auth')
        set_value(user, area, "{text}!")
        user.find(kind=ui.button, content="Save settings").click()

    run_ui(build, check)
    assert mgr.calls[-1] == ("save_quote_settings", {"enabled": False, "cooldown_seconds": 15,
                                                     "add_permission": "vip", "reply_format": "{text}!"})
    assert notes.messages == ["Quote settings saved"]
    assert done == [1]


def test_quote_settings_save_failure_keeps_dialog(mgr, notes):
    mgr.save_quote_ok = False
    done = []

    def build():
        qg.open_quote_settings(lambda: done.append(1))

    async def check(user):
        # No quotes: the built-in sample is used.
        await user.should_see('#4: "Chat, I promise this is the last \'one more run\'." — SporeLord')
        user.find(kind=ui.button, content="Save settings").click()
        await user.should_see("Quote settings")

    run_ui(build, check)
    assert notes.titles == ["Couldn't save quote settings"]
    assert done == []


def test_quote_settings_sample_without_authors(mgr, notes):
    mgr.quotes = {"q3": _q(3, "only one", author="")}

    def build():
        qg.open_quote_settings(lambda: None)

    async def check(user):
        await user.should_see('#3: "only one"')

    run_ui(build, check)


@pytest.mark.parametrize("gaps,text", [
    ([2], "#2 is missing (deleted quotes)."),
    ([1, 2, 3, 4, 5, 6, 7], "#1, #2, #3, #4, #5, #6… are missing (deleted quotes)."),
])
def test_quote_settings_gaps_and_renumber(mgr, notes, gaps, text):
    mgr.quotes = {"q1": _q(1), "q3": _q(3)}
    mgr.gaps = gaps
    done = []

    def build():
        qg.open_quote_settings(lambda: done.append(1))

    async def check(user):
        await user.should_see(text)
        user.find(kind=ui.button, content="Renumber 1–2").click()

    run_ui(build, check)
    assert ("renumber",) in mgr.calls
    assert notes.messages == ["Renumbered 2 quotes"]
    assert done == [1]


def test_quote_settings_renumber_single(mgr, notes):
    mgr.quotes = {"q2": _q(2)}
    mgr.gaps = [1]
    mgr.renumbered = 1

    def build():
        qg.open_quote_settings(lambda: None)

    async def check(user):
        user.find(kind=ui.button, content="Renumber 1–1").click()

    run_ui(build, check)
    assert notes.messages == ["Renumbered 1 quote"]


# --------------------------------------------------------------------------- GreetingsPanel


def _build_greetings(holder):
    def build():
        panel = qg.GreetingsPanel()
        holder["panel"] = panel
        panel.build(ui.column())
    return build


def test_greetings_panel_empty(mgr, notes, monkeypatch):
    opened = []
    monkeypatch.setattr(qg, "open_greeting_editor", lambda gid, cb: opened.append(gid))
    holder = {}

    async def check(user):
        await user.should_see("No personal greetings yet")
        await user.should_see("Everyone else")
        await user.should_see("Welcome NewViewer!")
        await user.should_see("Viewers without their own greeting get this the first time they chat.")
        await user.should_see("Personal greetings on")
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "<b>3</b> viewers greeted<" in htmls
        user.find(kind=ui.button, content="Add a greeting").click()
        user.find(kind=ui.button, content="New greeting").click()

    run_ui(_build_greetings(holder), check)
    assert opened == [None, None]


def test_greetings_panel_off_state_and_since_texts(mgr, notes):
    mgr.default_enabled = False
    mgr.custom_enabled = False
    mgr.greeted = {"count": 1, "since": time.time() - 60}
    mgr.reset_hours = 5  # not one of the preset choices
    holder = {}

    async def check(user):
        await user.should_see("Off: viewers without their own greeting aren't greeted.")
        await user.should_see("Personal greetings off")
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "<b>1</b> viewer greeted since " in htmls
        sel = user.find(kind=ui.select).elements.pop()
        assert sel.options[5] == "5 hours" and sel.value == 5
        off = [e for e in user.find(kind=ui.element).elements if "is-off" in e.classes]
        assert off

    run_ui(_build_greetings(holder), check)


def test_greetings_panel_since_older_than_a_day(mgr, notes):
    old = datetime(2001, 3, 9, 12).timestamp()
    mgr.greeted = {"count": 2, "since": old}
    holder = {}

    async def check(user):
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "greeted since Mar 9, 2001" in htmls

    run_ui(_build_greetings(holder), check)


def test_greetings_panel_cards_search_and_actions(mgr, notes, monkeypatch):
    mgr.greetings = {
        "g1": _g("g1", "bob", "Yo {username}", last=time.time() - 30),
        "g2": _g("g2", "Alice", "Hi {username}", enabled=False),
        "g3": _g("g3", "", "anon"),
    }
    edited, deleted = [], []
    monkeypatch.setattr(qg, "open_greeting_editor", lambda gid, cb: edited.append(gid))
    monkeypatch.setattr(qg, "confirm_delete_greeting", lambda g, cb: deleted.append(g.greeting_id))
    holder = {}

    async def check(user):
        await user.should_see("A welcome message the first time a viewer chats. 3 personal greetings.")
        # sorted by username: "", alice, bob
        names = [e.text for e in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)
                 if "qg-user" in e.classes]
        assert names == ["?", "Alice", "bob"]
        await user.should_see("Yo bob")
        await user.should_see("@alice")
        htmls = " ".join(e.content for e in user.find(kind=ui.html).elements)
        assert "Not greeted yet" in htmls and "Greeted " in htmls
        click(user, buttons(user, icon="edit")[1])  # first is "Edit" default greeting... pick card edit
        menu_click(user, "Delete…")
        with user.client:
            holder["panel"]._set_query("alice")
        await user.should_not_see("Yo bob")
        await user.should_see("Hi Alice")
        with user.client:
            holder["panel"]._set_query("zzz")
        await user.should_see("No greetings match your search.")

    run_ui(_build_greetings(holder), check)
    assert edited and edited[0] in ("g1", "g2", "g3")
    assert deleted == ["g3"]


def test_greetings_panel_single_greeting_text(mgr, notes):
    mgr.greetings = {"g1": _g()}
    holder = {}

    async def check(user):
        await user.should_see("A welcome message the first time a viewer chats. 1 personal greeting.")

    run_ui(_build_greetings(holder), check)


def test_greetings_card_flip_switch(mgr, notes):
    mgr.greetings = {"g1": _g("g1", "Bob", enabled=True)}
    holder = {}

    async def check(user):
        switches = [s for s in user.find(kind=ui.switch).elements if s.props.get("color") == "positive"]
        card_switch = max(switches, key=lambda s: s.id)
        card = card_switch.parent_slot.parent.parent_slot.parent
        set_value(user, card_switch, False)
        assert "is-off" in card.classes
        set_value(user, card_switch, True)
        assert "is-off" not in card.classes

    run_ui(_build_greetings(holder), check)
    assert ("update_greeting", "g1", None, False) in mgr.calls
    assert ("update_greeting", "g1", None, True) in mgr.calls


@pytest.mark.parametrize("ok", [True, False])
def test_greetings_reset_one(mgr, notes, ok):
    mgr.greetings = {"g1": _g("g1", "Bob")}
    mgr.reset_one_ok = ok
    holder = {}

    async def check(user):
        click(user, buttons(user, icon="restart_alt")[-1])

    run_ui(_build_greetings(holder), check)
    assert ("reset_one", "g1") in mgr.calls
    assert notes.messages == (["Bob will be greeted next time they chat"] if ok
                              else ["That greeting no longer exists. The list was refreshed."])


@pytest.mark.parametrize("ok", [True, False])
def test_greetings_quick_save_switches(mgr, notes, ok):
    mgr.save_greeting_ok = ok
    holder = {}

    async def check(user):
        default_switch = [s for s in user.find(kind=ui.switch).elements if s.props.get("color") == "positive"][0]
        set_value(user, default_switch, False)

    run_ui(_build_greetings(holder), check)
    name, kw = mgr.calls[-1]
    assert name == "save_greeting_settings"
    assert kw == {"default_enabled": False, "custom_enabled": True, "default_text": "Welcome {username}!",
                  "reset_interval_hours": 24}
    assert notes.types == ["success" if ok else "warning"]


def test_greetings_quick_save_custom_and_interval(mgr, notes):
    holder = {}

    async def check(user):
        custom_switch = [s for s in user.find(kind=ui.switch).elements if s.props.get("color") != "positive"][0]
        set_value(user, custom_switch, False)
        sel = user.find(kind=ui.select).elements.pop()
        set_value(user, sel, 48)

    run_ui(_build_greetings(holder), check)
    saves = [kw for name, kw in mgr.calls if name == "save_greeting_settings"]
    assert saves[0]["custom_enabled"] is False
    assert saves[1]["reset_interval_hours"] == 48


def test_greetings_default_edit_button(mgr, notes, monkeypatch):
    seen = []
    monkeypatch.setattr(qg, "open_default_greeting", lambda cb: seen.append(cb))
    holder = {}

    async def check(user):
        user.find(kind=ui.button, content="Edit").click()

    run_ui(_build_greetings(holder), check)
    assert seen == [holder["panel"].refresh]


def test_greetings_confirm_reset_all(mgr, notes):
    holder = {}

    async def check(user):
        click(user, buttons(user, text="Greet everyone again")[0])
        await user.should_see("Greet everyone again?")
        click(user, buttons(user, text="Greet everyone again")[-1])

    run_ui(_build_greetings(holder), check)
    assert ("reset_all",) in mgr.calls
    assert notes.messages == ["Everyone will be greeted again"]


def test_greetings_panel_render_skips_when_not_built(mgr):
    panel = qg.GreetingsPanel()
    panel.refresh()
    assert panel.query == ""


# --------------------------------------------------------------------------- delete greeting


@pytest.mark.parametrize("ok", [True, False])
def test_confirm_delete_greeting(mgr, notes, ok):
    mgr.remove_greeting_ok = ok
    done = []

    def build():
        qg.confirm_delete_greeting(_g("g1", "Bob"), lambda: done.append(1))

    async def check(user):
        await user.should_see("Delete Bob's greeting?")
        user.find(kind=ui.button, content="Delete").click()

    run_ui(build, check)
    assert ("remove_greeting", "g1") in mgr.calls and done == [1]
    assert notes.messages == (["Deleted Bob's greeting"] if ok
                              else ["That greeting no longer exists. The list was refreshed."])


# --------------------------------------------------------------------------- greeting editor


def test_greeting_editor_missing(mgr, notes):
    done = []

    def build():
        qg.open_greeting_editor("nope", lambda: done.append(1))

    run_ui(build)
    assert notes.types == ["info"] and done == [1]


def _patch_lookup(monkeypatch, result):
    from modules.uiwindows import chatbot as chatbot_tab

    calls = []

    async def lookup(name):
        calls.append(name)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(chatbot_tab, "lookup_twitch_user", lookup)
    return calls


def _found_html(user):
    return " ".join(e.content for e in user.find(kind=ui.html).elements if "qg-found" in e.classes)


def test_greeting_editor_new_lookup_and_save(mgr, notes, tasks, monkeypatch):
    calls = _patch_lookup(monkeypatch, ("777", "SporeKid"))
    done = []

    def build():
        qg.open_greeting_editor(None, lambda: done.append(1))

    async def check(user):
        await user.should_see("New greeting")
        save = buttons(user, text="Add greeting")[0]
        assert "disable" in save.props
        user.find(kind=ui.button, content="Add greeting").click()  # disabled: click ignored
        login = inputs(user)[0]
        set_value(user, login, "@SporeKid")
        await tasks.drain()
        assert "Found SporeKid on Twitch" in _found_html(user)
        user.find(kind=ui.textarea).type("Hey {username}")
        await user.should_see("Hey SporeKid")
        assert "disable" not in save.props
        user.find(kind=ui.button, content="Add greeting").click()

    run_ui(build, check)
    assert calls == ["SporeKid"]
    assert ("add_greeting", "777", "SporeKid", "Hey {username}", True) in mgr.calls
    assert notes.messages == ["Greeting for SporeKid saved"]
    assert done == [1]


def test_greeting_editor_save_problem_messages(mgr, notes, tasks, monkeypatch):
    _patch_lookup(monkeypatch, ("777", "Kid"))
    holder = {}

    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        # Call save through the keyboard-less path: force-enable and click to see the message.
        save = buttons(user, text="Add greeting")[0]
        with user.client:
            save.enable()
            save.props(remove="disable")
        holder["save"] = save
        click(user, save)
        await user.should_see("Enter a Twitch name that was found.")
        set_value(user, inputs(user)[0], "kid_name")
        await tasks.drain()
        click(user, save)
        await user.should_see("Write the greeting.")

    run_ui(build, check)


@pytest.mark.parametrize("name,expect", [
    ("", ""),
    ("a b", "That isn&#x27;t a Twitch name"),
])
def test_greeting_editor_lookup_rejects_bad_names(mgr, notes, tasks, monkeypatch, name, expect):
    calls = _patch_lookup(monkeypatch, ("1", "X"))

    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        login = inputs(user)[0]
        set_value(user, login, "x")
        set_value(user, login, name)
        await tasks.drain()
        assert expect in _found_html(user)
        if not expect:
            assert _found_html(user) == ""

    run_ui(build, check)
    assert calls == []


@pytest.mark.parametrize("error,expect", [
    (Exception("Twitch API not connected. Please authenticate"), "Twitch isn&#x27;t connected"),
    (Exception("Twitch user 'zzz' not found"), "Couldn&#x27;t find zzz_ on Twitch"),
])
def test_greeting_editor_lookup_errors(mgr, notes, tasks, monkeypatch, error, expect):
    _patch_lookup(monkeypatch, error)

    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        set_value(user, inputs(user)[0], "zzz_")
        await tasks.drain()
        assert expect in _found_html(user)
        found = [e for e in user.find(kind=ui.html).elements if "qg-found" in e.classes][0]
        assert "is-warn" in found.classes

    run_ui(build, check)


def test_greeting_editor_lookup_existing_greeting(mgr, notes, tasks, monkeypatch):
    mgr.greetings = {"g1": _g("g1", "Bob", user_id="42")}
    _patch_lookup(monkeypatch, ("42", "Bob"))

    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        set_value(user, inputs(user)[0], "bob_b")
        await tasks.drain()
        assert "Bob already has a greeting" in _found_html(user)

    run_ui(build, check)


def _gated_lookup(monkeypatch, result):
    from modules.uiwindows import chatbot as chatbot_tab

    gate = asyncio.Event()

    async def lookup(name):
        await gate.wait()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(chatbot_tab, "lookup_twitch_user", lookup)
    return gate


@pytest.mark.parametrize("result", [("1", "First"), Exception("Twitch user not found")])
def test_greeting_editor_stale_lookups_are_ignored(mgr, notes, tasks, monkeypatch, result):
    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        gate = _gated_lookup(monkeypatch, result)
        login = inputs(user)[0]
        set_value(user, login, "first")
        _n, first = tasks.pending.pop(0)
        running = asyncio.create_task(first)
        await asyncio.sleep(0)  # the first lookup is now waiting on Twitch
        set_value(user, login, "")  # a newer check starts and finishes first
        await tasks.drain()
        gate.set()
        await running  # the stale result is dropped
        assert _found_html(user) == ""

    run_ui(build, check)


def test_greeting_editor_add_failure_shows_error(mgr, notes, tasks, monkeypatch):
    _patch_lookup(monkeypatch, ("9", "Nine"))
    mgr.add_greeting_result = (False, "Already exists", None)

    def build():
        qg.open_greeting_editor(None, lambda: None)

    async def check(user):
        set_value(user, inputs(user)[0], "nine")
        await tasks.drain()
        user.find(kind=ui.textarea).type("hello")
        user.find(kind=ui.button, content="Add greeting").click()
        await user.should_see("Already exists")

    run_ui(build, check)
    assert notes.calls == []


def test_greeting_editor_edit_existing(mgr, notes, tasks):
    mgr.greetings = {"g1": _g("g1", "Bob", "Old {username}")}
    done = []

    def build():
        qg.open_greeting_editor("g1", lambda: done.append(1))

    async def check(user):
        await user.should_see("Bob's greeting")
        await user.should_see("Old Bob")
        login = inputs(user)[0]
        assert "readonly" in login.props
        set_value(user, login, "other")  # no lookup wired for an existing greeting
        assert tasks.pending == []
        switch = user.find(kind=ui.switch).elements.pop()
        set_value(user, switch, False)
        area = user.find(kind=ui.textarea).elements.pop()
        set_value(user, area, "  New  ")
        user.find(kind=ui.button, content="Save").click()

    run_ui(build, check)
    assert ("update_greeting", "g1", "New", False) in mgr.calls
    assert notes.messages == ["Greeting for Bob saved"]
    assert done == [1]


def test_greeting_editor_edit_failure(mgr, notes):
    mgr.greetings = {"g1": _g("g1", "Bob")}
    mgr.update_greeting_ok = False

    def build():
        qg.open_greeting_editor("g1", lambda: None)

    async def check(user):
        user.find(kind=ui.button, content="Save").click()
        await user.should_see("Couldn't save the greeting. Try again.")

    run_ui(build, check)


def test_greeting_editor_delete_opens_confirm(mgr, notes, monkeypatch):
    mgr.greetings = {"g1": _g("g1", "Bob")}
    seen = []
    monkeypatch.setattr(qg, "confirm_delete_greeting", lambda g, cb: seen.append(g.greeting_id))

    def build():
        qg.open_greeting_editor("g1", lambda: None)

    async def check(user):
        user.find(kind=ui.button, content="Delete").click()

    run_ui(build, check)
    assert seen == ["g1"]


def test_greeting_editor_variable_kit_preview_uses_display_name(mgr, notes, monkeypatch):
    kits = []
    real = qg.VariableKit

    def spy(variables, preview):
        kit = real(variables, preview)
        kits.append((variables, preview))
        return kit

    monkeypatch.setattr(qg, "VariableKit", spy)

    def build():
        qg.open_greeting_editor(None, lambda: None)

    run_ui(build)
    variables, preview = kits[0]
    assert variables()[0]["key"] == "username"
    assert preview("Hi {username}") == "Hi SporeKid"


# --------------------------------------------------------------------------- default greeting


def test_default_greeting_save(mgr, notes, monkeypatch):
    kits = []
    real = qg.VariableKit
    monkeypatch.setattr(qg, "VariableKit", lambda v, p: kits.append((v, p)) or real(v, p))
    done = []

    def build():
        qg.open_default_greeting(lambda: done.append(1))

    async def check(user):
        await user.should_see("Greeting for everyone else")
        await user.should_see("Welcome NewViewer!")
        area = user.find(kind=ui.textarea).elements.pop()
        set_value(user, area, "Hello {username}")
        await user.should_see("Hello NewViewer")
        set_value(user, user.find(kind=ui.number).elements.pop(), 12)
        set_value(user, user.find(kind=ui.switch).elements.pop(), False)
        user.find(kind=ui.button, content="Save").click()

    run_ui(build, check)
    assert mgr.calls[-1] == ("save_greeting_settings", {"default_enabled": False, "custom_enabled": True,
                                                        "default_text": "Hello {username}", "reset_interval_hours": 12})
    assert notes.messages == ["Greeting for everyone else saved"]
    assert done == [1]
    variables, preview = kits[0]
    assert variables() and preview("x {username}") == "x NewViewer"


def test_default_greeting_validation_and_failure(mgr, notes):
    done = []

    def build():
        qg.open_default_greeting(lambda: done.append(1))

    async def check(user):
        area = user.find(kind=ui.textarea).elements.pop()
        set_value(user, area, "   ")
        user.find(kind=ui.button, content="Save").click()
        await user.should_see("Write the greeting.")
        set_value(user, area, "ok")
        set_value(user, user.find(kind=ui.number).elements.pop(), None)
        mgr.save_greeting_ok = False
        user.find(kind=ui.button, content="Save").click()
        await user.should_see("Couldn't save the greeting. Try again.")

    run_ui(build, check)
    assert done == [] and notes.calls == []
    assert mgr.calls[-1][1]["reset_interval_hours"] == 24


def test_quote_settings_variable_kit(mgr, notes, monkeypatch):
    kits = []
    real = qg.VariableKit
    monkeypatch.setattr(qg, "VariableKit", lambda v, p: kits.append((v, p)) or real(v, p))
    mgr.quotes = {"q1": _q(1, "txt", author="Au", added_by="Me")}

    run_ui(lambda: qg.open_quote_settings(lambda: None))
    variables, preview = kits[0]
    by_key = {v["key"]: v["example"] for v in variables()}
    assert by_key["number"] == "1" and by_key["text"] == "txt" and by_key["author"] == "Au"
    assert by_key["added_by"] == "Me" and by_key["date_added"] == qg._date(mgr.quotes["q1"].date_added)
    assert preview("{text}/{author}") == "txt/Au"

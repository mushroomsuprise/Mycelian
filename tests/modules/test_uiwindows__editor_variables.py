# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/editor_variables.py (VariableKit: { } menu, format view, panel, caret insert)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules.uiwindows import editor_variables as ev  # noqa: E402
from ui_sim import run_ui  # noqa: E402

VARS = [
    {"key": "user", "label": "User name", "example": "Alice", "group": "Event"},
    {"key": "amount", "label": "Amount", "example": "5"},
    {"key": "now", "label": "Current time", "example": "12:00", "group": "Time", "date": True},
]


def variables(idx=None, extra=None):
    return list(VARS) + list(extra or [])


def render(text):
    return text.replace("{amount}", "$5").replace("{now}", "NOW").replace("{now:%H:%M}", "13:37")


@pytest.fixture
def js(monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: calls.append(code))
    return calls


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


@pytest.fixture
def clip(monkeypatch):
    calls = []
    monkeypatch.setattr(ui.clipboard, "write", lambda t: calls.append(t))
    return calls


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


def texts(container, cls=None):
    out = []
    for el in container.descendants():
        if isinstance(el, ui.label) and (cls is None or cls in el.classes):
            out.append(el.text)
    return out


def htmls(container):
    return [el.content for el in container.descendants() if isinstance(el, ui.html)]


# ---------------------------------------------------------------- plain helpers

def test_icon_html_and_esc():
    assert ev.icon_html("bolt") == '<span class="ce-icon">bolt</span>'
    assert ev.esc("<a&b>") == "&lt;a&amp;b&gt;"
    assert ev.esc(None) == ""
    assert ev.esc(5) == "5"


def test_raw_html_and_dom_id():
    holder = {}

    def build():
        holder["h"] = ev.raw_html("<b>x</b>")
        holder["h2"] = ev.raw_html("<i>y</i>", "c2")
        holder["e"] = ui.element("div")

    async def check(user):
        assert holder["h"].content == "<b>x</b>"
        assert "contents" in holder["h"].classes
        assert holder["h2"].classes == ["c2"]
        assert ev.dom_id(holder["e"]) == holder["e"].html_id

    run_ui(build, check)

    class Bare:
        id = 7

    assert ev.dom_id(Bare()) == "c7"


def test_kit_defaults_and_example():
    kit = ev.VariableKit(variables, render)
    assert kit.date_formats() == list(ev.DEFAULT_DATE_FORMATS)
    assert kit.empty_hint.startswith("Type any")
    assert kit.target is None and kit.query == ""
    assert kit.example({"key": "amount", "example": "x"}) == "$5"  # rendered differs -> render wins
    assert kit.example({"key": "user", "example": "Alice"}) == "Alice"  # unrendered -> example
    assert kit.example({"key": "user"}) == ""
    kit2 = ev.VariableKit(variables, render, date_formats=lambda: [{"format": "%Y", "label": "Y"}], empty_hint="h")
    assert kit2.date_formats() == [{"format": "%Y", "label": "Y"}]
    assert kit2.empty_hint == "h"


# ---------------------------------------------------------------- insert / focus / track

def test_insert_without_target_copies_to_clipboard(notes, clip):
    kit = ev.VariableKit(variables, render)
    kit.insert(None, "{user}")
    assert clip == ["{user}"]
    assert notes[0][0].startswith("Copied {user}.")
    assert notes[0][1] == {"type": "info"}

    class Deleted:
        is_deleted = True

    kit.insert(Deleted(), "{amount}")
    assert clip == ["{user}", "{amount}"]


def test_insert_runs_js_with_touched_flag_and_tracks_focus(js, notes):
    kit = ev.VariableKit(variables, render)
    holder = {}

    def build():
        holder["inp"] = ui.input()
        kit.track(holder["inp"])

    async def check(user):
        inp = holder["inp"]
        kit.insert(inp, "{user}")
        assert kit.target is inp
        assert '"{user}"' in js[-1]
        assert "const touched = false" in js[-1]
        assert f'"{ev.dom_id(inp)}"' in js[-1]
        kit.insert(inp, "{amount}")
        assert "const touched = true" in js[-1]
        kit.target = None
        kit._touched.clear()
        fire(user, inp, "focus")
        assert kit.target is inp and inp.id in kit._touched

    run_ui(build, check)
    assert notes == []


# ---------------------------------------------------------------- { } button and menu

def test_button_opens_menu_with_groups_and_examples(js):
    kit = ev.VariableKit(variables, render)
    holder = {}

    def build():
        holder["inp"] = ui.input()
        holder["btn"] = kit.button(holder["inp"], idx=1, extra=[{"key": "x1", "label": "Extra", "group": "More"}])

    async def check(user):
        btn = holder["btn"]
        assert btn.text == "{ }"
        assert "ce-varbtn" in btn.classes
        fire(user, btn)
        menu = next(el for el in btn.descendants() if isinstance(el, ui.menu))
        assert texts(menu, "ce-vm-group") == ["Event", "Time", "More"]
        h = "".join(htmls(menu))
        assert "<code>{user}</code>" in h and "Alice" in h
        assert "$5" in h  # amount example via render
        assert " · format…" in h  # date var
        # click the "user" row -> inserts at the input
        rows = [el for el in menu.descendants() if "ce-vm-row" in el.classes]
        assert len(rows) == 4
        fire(user, rows[0])
        assert '"{user}"' in js[-1]

    run_ui(build, check)


def test_menu_search_filters_and_empty_hint():
    kit = ev.VariableKit(variables, render, empty_hint="Nothing here.")
    holder = {}

    def build():
        holder["menu"] = ui.menu()
        holder["inserted"] = []
        kit.menu(holder["menu"], holder["inserted"].append)

    async def check(user):
        menu = holder["menu"]
        search = next(el for el in menu.descendants() if isinstance(el, ui.input))
        search.value = "AMOUNT"
        rows = [el for el in menu.descendants() if "ce-vm-row" in el.classes]
        assert len(rows) == 1
        search.value = "time"  # matches label of "now"
        assert texts(menu, "ce-vm-group") == ["Time"]
        search.value = "zzz"
        assert texts(menu, "ce-vm-empty") == ["No variables match. Nothing here."]
        search.value = None
        assert len([el for el in menu.descendants() if "ce-vm-row" in el.classes]) == 3

    run_ui(build, check)


def test_menu_date_var_opens_format_view_and_back():
    kit = ev.VariableKit(variables, render)
    holder = {}

    def build():
        holder["menu"] = ui.menu()
        holder["inserted"] = []
        kit.menu(holder["menu"], holder["inserted"].append)

    async def check(user):
        menu = holder["menu"]
        rows = [el for el in menu.descendants() if "ce-vm-row" in el.classes]
        fire(user, rows[2])  # "now" (date)
        fmt_rows = [el for el in menu.descendants() if "is-fmt" in el.classes]
        assert len(fmt_rows) == 1 + len(ev.DEFAULT_DATE_FORMATS)
        assert "NOW" in "".join(htmls(menu))  # As is example
        assert "13:37" in "".join(htmls(menu))
        fire(user, fmt_rows[1])
        assert holder["inserted"] == ["{now:%H:%M}"]
        back = next(el for el in menu.descendants() if isinstance(el, ui.button) and el.props.get("icon") == "arrow_back")
        fire(user, back)
        assert len([el for el in menu.descendants() if "ce-vm-row" in el.classes and "is-fmt" not in el.classes]) == 3

    run_ui(build, check)


# ---------------------------------------------------------------- format view

def test_format_view_custom_format_and_example():
    kit = ev.VariableKit(variables, lambda t: f"R[{t}]", date_formats=lambda: [{"format": "%Y", "label": "Year"}])
    holder = {}

    def build():
        holder["box"] = ui.column()
        holder["got"] = []
        kit.format_view(holder["box"], "now", holder["got"].append)

    async def check(user):
        box = holder["box"]
        assert not any(isinstance(el, ui.button) and el.props.get("icon") == "arrow_back" for el in box.descendants())
        fmt_rows = [el for el in box.descendants() if "is-fmt" in el.classes]
        assert len(fmt_rows) == 2
        fire(user, fmt_rows[0])
        assert holder["got"] == ["{now}"]
        assert "R[{now:%Y}]" in "".join(htmls(box))
        custom = next(el for el in box.descendants() if isinstance(el, ui.input))
        check_btn = next(el for el in box.descendants() if isinstance(el, ui.button) and el.props.get("icon") == "check")
        example = next(el for el in box.descendants() if isinstance(el, ui.label) and "ce-vm-ex" in el.classes)
        fire(user, check_btn)  # empty custom -> nothing
        assert holder["got"] == ["{now}"]
        custom.value = "%A"
        assert example.text == "R[{now:%A}]"
        fire(user, check_btn)
        assert holder["got"] == ["{now}", "{now:%A}"]
        custom.value = "   "
        assert example.text == ""
        assert ev.FORMAT_CODES in texts(box)

    run_ui(build, check)


# ---------------------------------------------------------------- side panel

def test_panel_renders_groups_footers_placeholders_and_tools(js, notes, clip):
    kit = ev.VariableKit(variables, render)
    holder = {"footers": [], "tools": []}

    def build():
        holder["box"] = ui.column()
        kit.panel(holder["box"], tag="Twitch", empty_html="No <b>event</b>",
                  group_footer=lambda g: holder["footers"].append(g),
                  groups_always={"Custom": "No custom variables yet", "Event": "", "Blank": ""},
                  row_tools=lambda v: holder["tools"].append(v["key"]))

    async def check(user):
        box = holder["box"]
        labels = texts(box)
        assert "Variables" in labels and "Twitch" in labels
        assert texts(box, "ce-vgroup") == ["Event", "Time", "Custom", "Blank"]
        assert "No custom variables yet" in labels
        assert holder["footers"] == ["Event", "Time", "Custom", "Blank"]
        assert holder["tools"] == ["user", "amount", "now"]
        assert any("No <b>event</b>" in h for h in htmls(box))
        rows = [el for el in box.descendants() if "ce-var" in el.classes]
        assert len(rows) == 3
        # row click with no target -> clipboard
        fire(user, rows[0])
        assert clip == ["{user}"]
        # date row has a tune button that opens the format menu
        tune = next(el for el in rows[2].descendants() if isinstance(el, ui.button) and el.props.get("icon") == "tune")
        fire(user, tune, "click.stop")
        fmenu = next(el for el in rows[2].descendants() if isinstance(el, ui.menu))
        fmt_rows = [el for el in fmenu.descendants() if "is-fmt" in el.classes]
        fire(user, fmt_rows[0])
        assert clip[-1] == "{now}"
        # search filters and hides always-groups
        search = next(el for el in box.descendants() if isinstance(el, ui.input))
        holder["footers"].clear()
        search.value = "user"
        assert kit.query == "user"
        assert texts(box, "ce-vgroup") == ["Event"]
        assert holder["footers"] == ["Event"]
        search.value = None
        assert kit.query == ""

    run_ui(build, check)


def test_panel_minimal_keeps_query_and_no_footers():
    kit = ev.VariableKit(variables, render)
    kit.query = "amount"
    holder = {}

    def build():
        holder["box"] = ui.column()
        kit.panel(holder["box"])

    async def check(user):
        box = holder["box"]
        assert "ce-tag" not in " ".join(c for el in box.descendants() for c in el.classes)
        assert not any("ce-vars-empty" in h for h in htmls(box))
        rows = [el for el in box.descendants() if "ce-var" in el.classes]
        assert len(rows) == 1
        search = next(el for el in box.descendants() if isinstance(el, ui.input))
        assert search.value == "amount"
        search.value = "zzz"
        assert texts(box, "ce-vgroup") == []

    run_ui(build, check)


def test_panel_row_inserts_at_tracked_target(js):
    kit = ev.VariableKit(variables, render)
    holder = {}

    def build():
        holder["inp"] = ui.textarea()
        holder["box"] = ui.column()
        kit.panel(holder["box"])

    async def check(user):
        kit.focus(holder["inp"])
        rows = [el for el in holder["box"].descendants() if "ce-var" in el.classes]
        fire(user, rows[1])
        assert '"{amount}"' in js[-1]

    run_ui(build, check)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_gp_styles.py: the shared Giveaways/Polls helpers and
small widgets (card, toggle, segmented picker, overlay rows)."""

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

from modules import web_engine  # noqa: E402
from modules.help_system import contextual_help, help_render  # noqa: E402
from modules.uiwindows import chatbot_gp_styles as gp  # noqa: E402
from modules.uiwindows.spore_studio import editor_view  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def fire(user, element, event="click"):
    UserInteraction(user, {element}, None).trigger(event)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    notes = []
    monkeypatch.setattr(gp, "notify", lambda msg, **kw: notes.append((msg, kw)))
    clip = []
    monkeypatch.setattr(ui.clipboard, "write", lambda text: clip.append(text))
    opened = []
    monkeypatch.setattr(gp.webbrowser, "open", lambda url, new=0: opened.append((url, new)))
    return SimpleNamespace(notes=notes, clip=clip, opened=opened)


# --------------------------------------------------------------------------- pure helpers


def test_icon_and_esc():
    assert gp.icon("star") == '<span class="cx-icon">star</span>'
    assert gp.esc("<a & b>") == "&lt;a &amp; b&gt;"
    assert gp.esc(None) == ""
    assert gp.esc(5) == "5"


def test_avatar_color_is_stable_and_case_insensitive():
    assert gp.avatar_color("Bob") == gp.avatar_color("bob")
    assert gp.avatar_color("") in gp.AVATAR_COLORS
    assert gp.avatar_color(None) == gp.avatar_color("?")


@pytest.mark.parametrize(
    "n,word,many,expected",
    [(1, "vote", None, "1 vote"), (2, "vote", None, "2 votes"), (0, "entry", "entries", "0 entries"),
     (1234, "viewer", None, "1,234 viewers")],
)
def test_plural(n, word, many, expected):
    assert gp.plural(n, word, many) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(-5, "just now"), (59, "just now"), (60, "1 min"), (3599, "59 min"), (3600, "1 h"), (7500, "2 h 5 min")],
)
def test_ago(seconds, expected):
    assert gp.ago(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(None, "0:00"), (5, "0:05"), (65, "1:05"), (3600, "1:00:00"), (-3, "0:00"), ("x", ""), (59.9, "0:59")],
)
def test_clock(seconds, expected):
    assert gp.clock(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0 seconds"), (1, "1 second"), (60, "1 minute"), (90, "90 seconds"), (7200, "2 hours"), (-5, "0 seconds")],
)
def test_duration_text(seconds, expected):
    assert gp.duration_text(seconds) == expected


def test_overlay_url_defaults_and_engine(monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    assert gp.overlay_url("poll") == "http://127.0.0.1:5000/poll"
    engine = SimpleNamespace(port=6123, _local_connect_host=lambda: "localhost")
    monkeypatch.setattr(web_engine, "web_engine_instance", engine, raising=False)
    assert gp.overlay_url("giveaway") == "http://localhost:6123/giveaway"
    engine.port = None
    assert gp.overlay_url("x") == "http://localhost:5000/x"

    def broken():
        raise RuntimeError("no host")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(port=1, _local_connect_host=broken),
                        raising=False)
    assert gp.overlay_url("x") == "http://127.0.0.1:1/x"


def test_open_in_spore_studio(monkeypatch):
    calls = []
    monkeypatch.setattr(contextual_help, "navigate_to_main_tab", lambda tab: calls.append(("tab", tab)))
    monkeypatch.setattr(editor_view, "open_template", lambda name: calls.append(("open", name)))
    gp.open_in_spore_studio("poll")
    gp.open_in_spore_studio("")
    assert calls == [("tab", "Spore Studio"), ("open", "poll"), ("tab", "Spore Studio")]


def test_copy_text(_isolate):
    gp.copy_text("abc", "Done")
    assert _isolate.clip == ["abc"]
    assert _isolate.notes == [("Done", {"type": "success", "source": "chatbot"})]
    gp.copy_text("x")
    assert _isolate.notes[-1][0] == "Copied"


def test_gp_css_is_balanced():
    assert gp.GP_CSS.count("{") == gp.GP_CSS.count("}")
    assert ".gp-hero" in gp.GP_CSS


# --------------------------------------------------------------------------- widgets


def test_inject_styles_adds_both_stylesheets():
    def build():
        gp.inject_styles()

    run_ui(build)
    assert {"mycelian-chatbot-gp", "mycelian-connector-editor-menus"} <= help_render._shared_stylesheets


def test_card_returns_header_and_body():
    refs = {}

    def build():
        refs["plain"] = gp.card("Entrants", "group")
        with refs["plain"][1]:
            ui.label("inside body")
        refs["badged"] = gp.card("Live <now>", "bolt", badge="LIVE", badge_live=True)
        refs["quiet"] = gp.card("Quiet", "bolt", badge="3")

    async def check(user):
        await user.should_see("Entrants")
        await user.should_see("inside body")
        await user.should_see("LIVE")
        head, body = refs["plain"]
        assert "gp-card-h" in head.classes and "gp-card-b" in body.classes
        badges = {el.text: el.classes for el in user.find(ui.label).elements if "gp-badge" in el.classes}
        assert "is-live" in badges["LIVE"] and "is-live" not in badges["3"]
        box = refs["badged"][0].parent_slot.parent
        assert box.props["data-card"] == "Live &lt;now&gt;"

    run_ui(build, check)


def test_toggle_calls_back_with_bool_and_disabled_state():
    seen = []
    refs = {}

    def build():
        refs["on"] = gp.toggle("Allow switching", "Viewers can change vote", 1, seen.append)
        refs["off"] = gp.toggle("Locked", "", False, seen.append, enabled=False)

    async def check(user):
        await user.should_see("Allow switching")
        await user.should_see("Viewers can change vote")
        assert refs["on"].value is True and refs["on"].enabled
        assert refs["off"].enabled is False
        wrappers = [el for el in user.find(ui.element).elements if "gp-toggle" in el.classes]
        assert any("is-disabled" in w.classes for w in wrappers)
        with user.client:
            refs["on"].value = False
        assert seen == [False]
        hints = [el for el in user.find(ui.label).elements if "gp-hint" in el.classes]
        assert len(hints) == 1

    run_ui(build, check)


def test_seg_picks_once_and_rerenders():
    picked = []
    refs = {}

    def build():
        refs["box"] = gp.seg({"bars": "Bars", "pie": "Pie"}, "bars", picked.append)

    async def check(user):
        await user.should_see("Bars")
        box = refs["box"]

        def option(label):
            return next(child for child in box.default_slot.children
                        if any(getattr(c, "text", None) == label for c in child.default_slot.children))

        assert "is-on" in option("Bars").classes and "is-on" not in option("Pie").classes
        fire(user, option("Bars"))
        assert picked == []  # already selected
        fire(user, option("Pie"))
        assert picked == ["pie"]
        assert "is-on" in option("Pie").classes and "is-on" not in option("Bars").classes

    run_ui(build, check)


def test_overlay_rows_copy_customize_and_preview(_isolate, monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    customized = []
    refs = {}

    def build():
        gp.overlay_rows("poll", lambda: customized.append(1), "Customize poll", "brush")

    async def check(user):
        await user.should_see("Customize poll")
        url_row = [el for el in user.find(ui.element).elements if "gp-url" in el.classes][0]
        refs["row"] = url_row
        fire(user, url_row)
        assert _isolate.clip == ["http://127.0.0.1:5000/poll"]
        assert _isolate.notes[-1][0] == "Overlay address copied"
        user.find("Customize poll").click()
        assert customized == [1]
        user.find("Preview").click()
        assert _isolate.opened == [("http://127.0.0.1:5000/poll", 2)]

    run_ui(build, check)

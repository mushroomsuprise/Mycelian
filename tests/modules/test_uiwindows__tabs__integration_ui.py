# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/integration_ui.py (shared Settings integration page parts)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import notification_engine  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import service_brand_icons  # noqa: E402
from modules.uiwindows.tabs import integration_ui as itg  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_stylesheets(monkeypatch):
    # inject_page_stylesheet remembers ids in a module set; keep it per-test.
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())


@pytest.fixture
def notes(monkeypatch):
    calls: List[Dict[str, Any]] = []

    def fake_notify(message, **kw):
        calls.append({"message": message, **kw})

    monkeypatch.setattr(notification_engine, "notify", fake_notify)
    return calls


# --------------------------------------------------------------------------- pure helpers


def test_esc_escapes_and_handles_none():
    assert itg.esc("<b>&\"") == "&lt;b&gt;&amp;&quot;"
    assert itg.esc(None) == ""
    assert itg.esc(5) == "5"


def test_icon_html_escapes_name():
    assert itg.icon_html("check") == '<span class="itg-icon">check</span>'
    assert "&lt;x&gt;" in itg.icon_html("<x>")


def test_brand_svg_strips_size_and_recolors_obs():
    svg = itg.brand_svg("twitch")
    assert 'width="1.25em"' not in svg
    assert "#9146FF" in svg
    obs = itg.brand_svg("obs")
    assert 'fill="currentColor"' in obs
    assert 'fill="#302E31"' not in obs
    assert service_brand_icons.OBS_HALO_ATTRS not in obs
    assert itg.brand_svg("nope") == ""


def test_pill_html_dot_or_icon():
    dot = itg.pill_html("ok", "Connected")
    assert dot == '<span class="itg-pill is-ok"><span class="dot"></span>Connected</span>'
    with_icon = itg.pill_html("edit", "<x>", "edit")
    assert '<span class="itg-icon">edit</span>' in with_icon
    assert "&lt;x&gt;" in with_icon


def test_open_url_only_with_url(monkeypatch):
    opened = []
    monkeypatch.setattr(itg.webbrowser, "open", lambda url, new=0: opened.append((url, new)))
    itg.open_url("")
    itg.open_url("https://example.com")
    assert opened == [("https://example.com", 2)]


def test_js_string_is_json():
    assert itg._js_string('a"b') == '"a\\"b"'


def test_copy_to_clipboard_native(monkeypatch, notes):
    copied = []
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda t: copied.append(t) or True)
    js = []
    monkeypatch.setattr(itg.ui, "run_javascript", lambda code: js.append(code))
    itg.copy_to_clipboard("hello", "Got it")
    assert copied == ["hello"]
    assert js == []
    assert notes == [{"message": "", "type": "success", "source": "app", "title": "Got it"}]


def test_copy_to_clipboard_falls_back_to_js(monkeypatch, notes):
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda t: False)
    js = []
    monkeypatch.setattr(itg.ui, "run_javascript", lambda code: js.append(code))
    itg.copy_to_clipboard(None)
    assert js == ['navigator.clipboard.writeText("")']
    assert notes[-1]["title"] == "Copied"


def test_steps_html_marks_done():
    out = itg.steps_html([("<b>One</b>", True), ("Two", False)])
    assert out.startswith('<div class="itg-steps">')
    assert '<div class="itg-step is-done"><span><b>One</b></span></div>' in out
    assert '<div class="itg-step"><span>Two</span></div>' in out


def test_banner_html_and_media_html():
    b = itg.banner_html("Heads <up>", "text & more")
    assert "warning_amber" in b and "&lt;up&gt;" in b and "text &amp; more" in b
    m = itg.media_html("music_note", "Song", "Artist")
    assert 'class="itg-media"' in m and "itg-media-sub" in m and "Artist" in m
    muted = itg.media_html("music_note", "Nothing", muted=True)
    assert "is-muted" in muted and "itg-media-sub" not in muted


def test_initial_of():
    assert itg.initial_of("  _bob") == "B"
    assert itg.initial_of("") is None
    assert itg.initial_of(None) is None
    assert itg.initial_of("!!!") is None


class _FakeBtn:
    def __init__(self, text=""):
        self.text = text
        self.prop_calls = []

    def set_text(self, t):
        self.text = t

    def props(self, add=None, *, remove=None):
        self.prop_calls.append((add, remove))
        return self


def test_set_connect_style_toggles_and_skips_repeats():
    btn = _FakeBtn("Connect")
    itg.set_connect_style(btn, True)
    assert btn.text == "Reconnect" and btn.prop_calls == [("outline", None)]
    itg.set_connect_style(btn, True)  # unchanged: no extra calls
    assert len(btn.prop_calls) == 1
    itg.set_connect_style(btn, False, connect="Go")
    assert btn.text == "Go" and btn.prop_calls[-1] == (None, "outline")


def test_all_exports_exist():
    for name in itg.__all__:
        assert hasattr(itg, name)
    assert set(itg.BRAND_COLORS) >= {"twitch", "obs", "streamlabs", "streamelements"}


# --------------------------------------------------------------------------- IntegrationPage


def test_integration_page_header_status_and_dirty(monkeypatch):
    state = {"dirty": False}
    saved, discarded = [], []
    refs: Dict[str, Any] = {}
    helps = []
    monkeypatch.setattr(
        "modules.help_system.contextual_help.help_button",
        lambda **kw: helps.append(kw) or ui.button("?"),
    )

    def build():
        page = itg.IntegrationPage(
            ui.column(), "twitch", "Twitch", "Chat & alerts",
            on_save=lambda: saved.append(1), on_discard=lambda: discarded.append(1),
            is_dirty=lambda: state["dirty"], help_topic="guide_twitch",
        )
        refs["page"] = page
        with page.column():
            ui.label("left col")
        with page.wide(top=True):
            ui.label("wide top")
        with page.wide():
            ui.label("wide bottom")

    async def check(user):
        page = refs["page"]
        await user.should_see("left col")
        await user.should_see("wide top")
        assert page.root.style.get("--brand") == "#9146FF"
        assert helps == [{"topic_id": "guide_twitch", "tooltip": "Twitch setup help", "size": "sm"}]
        # not dirty: discard disabled, no tag
        assert "disable" in page.discard_button.props
        assert page._dirty_tag.content == ""
        state["dirty"] = True
        page.refresh_dirty()
        assert "Unsaved changes" in page._dirty_tag.content
        assert "disable" not in page.discard_button.props
        page.refresh_dirty()  # same state: early return
        page.set_status([("ok", "Connected"), ("warn", "Slow")])
        assert "is-ok" in page._pills.content and "Slow" in page._pills.content
        before = page._pills.content
        page.set_status([("ok", "Connected"), ("warn", "Slow")])
        assert page._pills.content == before
        page.set_active(False)
        assert page._timer.active is False
        page.set_active(True)
        assert page._timer.active is True
        user.find("Save").click()
        assert saved == [1]
        user.find("Discard").click()
        assert discarded == [1]

    run_ui(build, check)


def test_integration_page_icon_tile_and_dirty_error():
    refs: Dict[str, Any] = {}

    def boom():
        raise RuntimeError("nope")

    def build():
        refs["page"] = itg.IntegrationPage(
            ui.column(), "unknown", "Database", "Where data lives",
            on_save=lambda: None, on_discard=lambda: None, is_dirty=boom, icon="storage",
        )

    async def check(user):
        page = refs["page"]
        await user.should_see("Database")
        # is_dirty raising counts as clean
        assert page._last_dirty is False
        assert "disable" in page.discard_button.props
        assert page.root.style.get("--brand") == "var(--color-primary)"
        title_html = [e for e in user.find(ui.html).elements if "itg-title-name" in e.content][0].content
        assert "itg-tile" in title_html and "storage" in title_html

    run_ui(build, check)


# --------------------------------------------------------------------------- AccountCard + fold


def test_account_card_avatar_variants_and_meta():
    refs: Dict[str, Any] = {}

    def build():
        refs["card"] = itg.AccountCard("twitch", "Twitch <acct>")
        with refs["card"].actions:
            ui.label("ACTION")
        with refs["card"].extra():
            ui.label("EXTRA")

    async def check(user):
        card = refs["card"]
        await user.should_see("ACTION")
        await user.should_see("EXTRA")
        who = card._who
        # default: empty brand avatar
        assert "is-empty" in who.content and "Twitch &lt;acct&gt;" in who.content
        card.set("bob", "ok", "Live", initial="bob")
        assert '<div class="itg-ava">B</div>' in who.content
        assert "is-ok" in who.content
        card.set("bob", "ok", "Live", photo_url="https://img/x.png")
        assert "has-photo" in who.content and 'src="https://img/x.png"' in who.content
        card.set("bob", "ok", "Live", photo_url="http://insecure/x.png")
        assert "has-photo" not in who.content and "is-empty" in who.content
        card.set("", "ok", "Live", photo_url="https://img/y.png")
        assert ">?<img" in who.content
        card.set("x", "off", "", icon="storage")
        assert "itg-acct-tile" in who.content
        same = who.content
        card.set("x", "off", "", icon="storage")
        assert who.content == same
        # meta
        assert card._meta.visible is False
        card.set_meta([("OBS", "30.0", "sub", "is-ok"), ("WS", "v5", "", "")])
        assert card._meta.visible is True
        assert '<div class="s">sub</div>' in card._meta.content
        assert card._meta.content.count('<div class="s">') == 1
        card.set_meta([])
        assert card._meta.visible is False and card._meta.content == ""

    run_ui(build, check)


def test_credentials_fold_tags_and_open():
    refs: Dict[str, Any] = {}

    def build():
        card = itg.AccountCard("obs", "OBS")
        refs["fold"] = card.credentials("Keys", opened=False)
        with refs["fold"].body:
            ui.label("inside")

    async def check(user):
        fold = refs["fold"]
        assert "Keys" in fold._head.content and "itg-fold-tag" not in fold._head.content
        fold.set_saved(True)
        assert "Saved" in fold._head.content
        fold.set_saved(False, missing_text="Add <it>")
        assert "is-missing" in fold._head.content and "Add &lt;it&gt;" in fold._head.content
        assert fold.expansion.value is False
        fold.open()
        assert fold.expansion.value is True

    run_ui(build, check)


# --------------------------------------------------------------------------- field helpers


def test_fields_field_label_and_field(monkeypatch):
    refs: Dict[str, Any] = {}
    changes = []

    def build():
        with itg.fields() as f1:
            refs["f1"] = f1
            refs["plain"] = itg.field("Host", value=None, placeholder="localhost")
            refs["pw"] = itg.field("Secret", value=12, tip="Shh", password=True, readonly=True,
                                   on_change=lambda e: changes.append(e.value))
        refs["f2"] = itg.fields(one=True)
        refs["label"] = itg.field_label("Plain")

    async def check(user):
        assert "itg-fields" in refs["f1"].classes and "is-one" not in refs["f1"].classes
        assert "is-one" in refs["f2"].classes
        assert refs["plain"].value == ""
        assert refs["pw"].value == "12"
        assert "readonly" in refs["pw"].props
        assert refs["pw"].props.get("type") == "password" or refs["pw"].password is True
        assert "help_outline" not in refs["label"].content
        pw_label = [e for e in user.find(ui.html).elements if "Secret" in e.content][0]
        assert "help_outline" in pw_label.content
        refs["pw"].value = "new"
        assert changes == ["new"]

    run_ui(build, check)


def test_row_switch_row_steps_hint(monkeypatch):
    refs: Dict[str, Any] = {}
    got = []
    opened = []
    monkeypatch.setattr(itg.webbrowser, "open", lambda url, new=0: opened.append(url))

    def build():
        r, ctrl = itg.row("Title only")
        refs["row"] = r
        refs["sw"] = itg.switch_row("Enable", "Turn on", 1, lambda v: got.append(v), tooltip="tip")
        refs["sw2"] = itg.switch_row("Other", "", False, lambda v: got.append(("o", v)))
        refs["steps"] = itg.steps([("A", False)])
        refs["hint"] = itg.hint("Plain note")
        refs["hint2"] = itg.hint("Warn note", "warning", link_label="Docs", url="https://d", warn=True)

    async def check(user):
        await user.should_see("Docs")
        assert refs["sw"].value is True
        refs["sw"].value = False
        assert got == [False]
        refs["sw2"].value = True
        assert got[-1] == ("o", True)
        assert "is-warn" in refs["hint2"].classes and "is-warn" not in refs["hint"].classes
        assert "itg-steps" in refs["steps"].content
        texts = [e.content for e in user.find(ui.html).elements]
        assert any("itg-row-title" in t and "itg-row-sub" not in t and "Title only" in t for t in texts)
        user.find("Docs").trigger("click")
        assert opened == ["https://d"]

    run_ui(build, check)


def test_link_opens_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(itg.webbrowser, "open", lambda url, new=0: opened.append((url, new)))

    def build():
        itg.link("Site", "https://site")

    async def check(user):
        user.find("Site").trigger("click")
        assert opened == [("https://site", 2)]

    run_ui(build, check)


def test_tool_row_and_section():
    refs: Dict[str, Any] = {}

    def build():
        with itg.tool_row("build", "Rebuild", "Makes it again") as ctrl:
            ui.button("Run")
            refs["ctrl"] = ctrl
        with itg.section("cable", "Connection", right="<i>r</i>"):
            ui.label("in section")
        itg.section("cable", "No right")

    async def check(user):
        await user.should_see("Run")
        await user.should_see("in section")
        texts = [e.content for e in user.find(ui.html).elements]
        assert any("itg-tool-title" in t and "Rebuild" in t for t in texts)
        assert any("itg-sec-right" in t and "<i>r</i>" in t for t in texts)
        assert any("No right" in t and "itg-sec-right" not in t for t in texts)

    run_ui(build, check)


def test_copy_row_set_and_copy(monkeypatch):
    refs: Dict[str, Any] = {}
    copied = []
    extras = []
    monkeypatch.setattr(itg, "copy_to_clipboard", lambda text, what="Copied": copied.append((text, what)))

    def build():
        refs["row"] = itg.CopyRow("Invite", "", copied="Link copied", empty="(none)",
                                  extra=lambda: extras.append(ui.label("EXTRA")))

    async def check(user):
        row = refs["row"]
        await user.should_see("EXTRA")
        assert row._label.text == "(none)"
        assert "is-empty" in row._label.classes
        assert "disable" in row.button.props
        row._copy()
        assert copied == []
        row.set("https://inv")
        assert row._label.text == "https://inv"
        assert "is-empty" not in row._label.classes
        assert "disable" not in row.button.props
        row._copy()
        assert copied == [("https://inv", "Link copied")]

    run_ui(build, check)


def test_list_helpers_and_tokens():
    removed, clicked, inserted = [], [], []
    refs: Dict[str, Any] = {}
    long_name = "x" * 60

    def build():
        with itg.list_box(scroll=True) as box:
            refs["box"] = box
            refs["li"] = itg.list_item("person", "Alice", "mod", on_remove=lambda: removed.append(1),
                                       on_click=lambda: clicked.append(1), active=True)
            refs["li2"] = itg.list_item("person", long_name)
        refs["box2"] = itg.list_box()
        itg.list_empty("Nothing here")
        itg.list_foot("2 items")
        itg.tokens(["{a}", "{b}"], lambda n: inserted.append(n))
        refs["banner"] = itg.banner()

    async def check(user):
        await user.should_see("Nothing here")
        await user.should_see("2 items")
        await user.should_see("mod")
        assert "is-scroll" in refs["box"].classes and "is-scroll" not in refs["box2"].classes
        assert {"itg-li", "is-click", "is-active"} <= set(refs["li"].classes)
        assert "is-click" not in refs["li2"].classes
        for listener in refs["li"]._event_listeners.values():
            listener.handler(SimpleNamespace())
        assert clicked == [1]
        btn = [b for b in refs["li"].default_slot.children if isinstance(b, ui.button)][0]
        for listener in btn._event_listeners.values():
            listener.handler(SimpleNamespace())
        assert removed == [1]
        user.find("{b}").trigger("click")
        assert inserted == ["{b}"]
        assert refs["banner"].visible is False

    run_ui(build, check)


def test_inject_integration_styles_scopes_css(monkeypatch):
    seen = []
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: seen.append((sid, css)))
    itg.inject_integration_styles()
    sid, css = seen[0]
    assert sid == "mycelian-integrations"
    assert ".itg" in css and "!important" in css

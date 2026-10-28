# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/theme_tab.py: colour helpers, readability, and the Theme tab UI
(picker, preview, palette editor with undo/redo, pop-up look, Apply / Discard / Save as / Delete).
The theme manager and state manager are fakes, so no real theme or setting is ever saved."""

from __future__ import annotations

import copy
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

from modules import mainuiwindow, notification_engine  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.theme_manager import ThemeColors  # noqa: E402
from modules.uiwindows.tabs import theme_tab as tt  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# ----------------------------------------------------------------- fakes

def make_theme(name, display, kind="dark", **colors):
    base = dict(
        primary="rgb(115, 0, 255)", primary_hover="rgb(90, 0, 200)", primary_light="rgba(115, 0, 255, 0.15)",
        bg_base="rgb(18, 18, 24)", bg_elevated="rgb(25, 25, 35)", bg_surface="rgb(35, 35, 45)",
        bg_overlay="rgba(0, 0, 0, 0.5)", text_primary="rgb(240, 240, 255)", text_secondary="rgb(200, 200, 220)",
        text_muted="rgb(150, 150, 170)", text_inverse="#000", border_default="#444", border_subtle="#333",
        border_accent="#7300ff", success="#0f0", warning="#fa0", error="#f00", info="#0af",
    )
    base.update(colors)
    return ThemeColors(name=name, display_name=display, theme_type=kind, **base)


class FakeThemes:
    def __init__(self):
        self.themes = {
            "dark": make_theme("dark", "Dark"),
            "light": make_theme("light", "Light", "light", bg_base="#ffffff", bg_surface="#f5f5f5",
                                text_primary="#111", text_secondary="#333", text_muted="#666", primary="#1976d2"),
            "mine": make_theme("mine", "Mine"),
        }
        self.saved, self.updated, self.deleted = [], [], []
        self.save_ok = self.update_ok = self.delete_ok = True
        self.fail_list = False
        self.fail_get = False

    def get_available_themes(self):
        if self.fail_list:
            raise OSError("unreadable")
        return [(n, t.display_name) for n, t in self.themes.items()]

    def get_theme_by_name(self, name):
        if self.fail_get:
            raise ValueError("corrupt")
        t = self.themes.get(name)
        return copy.deepcopy(t) if t else None

    def get_theme_display_name(self, name):
        t = self.themes.get(name)
        return t.display_name if t else name

    def update_custom_theme(self, theme):
        self.updated.append(copy.deepcopy(theme))
        if self.update_ok:
            self.themes[theme.name] = copy.deepcopy(theme)
        return self.update_ok

    def theme_name_exists(self, name):
        return name in self.themes

    def save_custom_theme(self, theme):
        self.saved.append(copy.deepcopy(theme))
        if self.save_ok:
            self.themes[theme.name] = copy.deepcopy(theme)
        return self.save_ok

    def delete_theme(self, name):
        self.deleted.append(name)
        if self.delete_ok:
            self.themes.pop(name, None)
        return self.delete_ok


class FakeState:
    def __init__(self, theme="dark", minimal=False):
        self.settings = SimpleNamespace(current_theme=theme, status_footer_minimal=minimal)
        self.updates = []
        self.save_ok = True
        self.saves = 0

    def get_app_settings(self):
        return self.settings

    def update_app_setting(self, field, value):
        self.updates.append((field, value))
        setattr(self.settings, field, value)
        return True

    def save_changes(self):
        self.saves += 1
        return self.save_ok


@pytest.fixture
def themes(monkeypatch):
    fake = FakeThemes()
    monkeypatch.setattr(tt, "get_theme_manager", lambda: fake)
    return fake


@pytest.fixture
def state(monkeypatch):
    fake = FakeState()
    monkeypatch.setattr(tt, "state_manager", fake)
    return fake


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(tt, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


@pytest.fixture
def applied(monkeypatch):
    calls = []
    monkeypatch.setattr(mainuiwindow, "apply_theme", lambda name: calls.append(name))
    return calls


@pytest.fixture
def picker(monkeypatch):
    calls = []
    monkeypatch.setattr(tt, "open_color_picker", lambda anchor, value, **kw: calls.append((anchor, value, kw)))
    return calls


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(notification_engine, "current_footer_style_classes", lambda: "service-status-footer--full-stacked")


def fire(user, el, event="click"):
    UserInteraction(user, {el}, None).trigger(event)


def button(client, text=None, icon=None):
    for e in sorted(client.elements.values(), key=lambda e: e.id):
        if isinstance(e, ui.button) and (text is None or e.text == text) and (icon is None or e.props.get("icon") == icon):
            return e
    raise AssertionError(f"no button {text!r}/{icon!r}")


def by_class(client, cls):
    return sorted((e for e in client.elements.values() if cls in e.classes), key=lambda e: e.id)


def disabled(btn):
    return "disable" in btn.props


def build_tab(check):
    made = {}

    def build():
        tab = tt.ThemeTab()
        tab.build(ui.column())
        made["tab"] = tab

    async def run(user):
        with user.client:
            await check(user, made["tab"])

    run_ui(build, run)
    return made["tab"]


# ----------------------------------------------------------------- pure helpers

@pytest.mark.parametrize(
    "value, expected",
    [("#abcd", (170.0, 187.0, 204.0, 221 / 255)), ("#11223380", (17.0, 34.0, 51.0, 128 / 255)),
     ("#zzz", None), ("rgb(300, -1, 5)", None), ("rgb(300, 1, 5)", (255.0, 1.0, 5.0, 1.0)),
     ("rgba(1, 2, 3, 5)", (1.0, 2.0, 3.0, 1.0)), (None, None)],
)
def test_parse_color_more(value, expected):
    assert tt._parse_color(value) == expected


def test_over_and_luminance():
    assert tt._over((255, 255, 255, 0.5), (0, 0, 0, 1)) == (127.5, 127.5, 127.5, 1.0)
    assert tt._luminance((0, 0, 0)) == 0
    assert tt._luminance((255, 255, 255)) == pytest.approx(1.0)
    assert tt._luminance((5, 5, 5)) == pytest.approx(5 / 255 / 12.92)


def test_readability_checks_skip_unparseable_and_low_rating():
    assert tt.readability_checks(ThemeColors(bg_base="nope")) == []
    t = make_theme("x", "X", text_muted="nonsense")
    labels = [r[0] for r in tt.readability_checks(t)]
    assert "Hints" not in labels and "Text" in labels
    low = make_theme("y", "Y", kind="light", bg_base="#fff", bg_surface="#fff", text_primary="#888")
    ratings = {r[0]: r[4] for r in tt.readability_checks(low)}
    assert ratings["Text"] == "low"
    assert tt.readability_checks(ThemeColors(theme_type="", bg_base="#000", bg_surface="#000", text_primary="#fff"))[0][4] == "ok"


@pytest.mark.parametrize(
    "value, out",
    [("", "#000000"), ("#abc", "#aabbcc"), ("#11223344", "#112233"), ("rgba(300, 2, 3, 0.5)", "#ff0203"),
     ("rgba(x)", "#000000"), ("rgb(1, 2, 3)", "#010203"), ("rgb(bad)", "#000000"), ("a1b2c3", "#a1b2c3"),
     ("zzzzzz", "#000000"), ("teal", "#000000")],
)
def test_convert_to_hex(value, out):
    assert tt.ThemeTab()._convert_to_hex(value) == out


def test_describe_color_unparseable():
    assert tt.ThemeTab()._describe_color("teal") == "teal"


def test_safe_get_theme_attribute():
    tab = tt.ThemeTab()
    assert tab._safe_get_theme_attribute(None, "x", "d") == "d"
    assert tab._safe_get_theme_attribute(SimpleNamespace(x=1), "x") == 1

    class _Bad:
        def __getattr__(self, name):
            raise RuntimeError("x")

    assert tab._safe_get_theme_attribute(_Bad(), "x", "d") == "d"


def test_build_select_options():
    tab = tt.ThemeTab()
    assert tab._build_theme_select_options([("my_theme", ""), ("a", "Aaa")]) == {"my_theme": "My Theme", "a": "Aaa"}


def test_notify_sample_html_variants():
    tab = tt.ThemeTab()
    t = make_theme("x", "X", notify_style="glass", notify_radius="40", notify_shadow="strong", notify_timer="off",
                   notify_density="compact")
    out = tab._notify_sample_html(t)
    assert 'class="mt is-warning st-glass sh-strong is-compact"' in out
    assert "--notify-radius:28px" in out and "mt-bar" not in out
    assert "--color-notify-warning:#fa0" in out  # falls back to warning
    t2 = make_theme("y", "Y", notify_radius="abc")
    out2 = tab._notify_sample_html(t2)
    assert "--notify-radius:12px" in out2 and 'class="mt is-warning st-accent"' in out2 and "mt-bar" in out2


def test_dot_colors():
    t = ThemeColors(bg_base="a", bg_surface="b", primary="c")
    assert tt.ThemeTab._dot_colors(t) == ("a", "b", "c")
    assert tt.ThemeTab._dot_colors(ThemeColors()) == ("", "", "")


def test_readability_html():
    out = tt.ThemeTab._readability_html(make_theme("x", "X"))
    assert out.count('class="thm-read is-') == 4


# ----------------------------------------------------------------- error handling helpers

def test_theme_manager_unavailable(monkeypatch, notes):
    def boom():
        raise RuntimeError("no themes")

    monkeypatch.setattr(tt, "get_theme_manager", boom)
    tab = tt.ThemeTab()
    assert tab._get_theme_manager() is None
    assert notes[-1][1]["dedupe_key"] == "theme:manager"
    assert tab._get_current_theme() is None
    assert tab._display_name("x") == "x"


def test_available_themes_error(themes, notes):
    themes.fail_list = True
    tab = tt.ThemeTab()
    assert tab._get_available_themes(themes) == []
    assert notes[-1][1]["title"] == "Couldn't read the theme list"
    assert tab._get_available_themes(None) == []


def test_current_theme_error(themes, notes):
    themes.fail_get = True
    tab = tt.ThemeTab()
    assert tab._get_current_theme("mine") is None
    assert notes[-1][1]["title"] == "Couldn't load the theme “mine”"


def test_display_name_fallback(themes):
    tab = tt.ThemeTab()
    assert tab._display_name("mine") == "Mine"
    assert tab._display_name("unknown") == "unknown"


def test_lifecycle_on_enter_exit(themes, state):
    tab = tt.ThemeTab()
    tab.on_enter()
    assert tab._themes_loaded and tab.buffer == "dark" and tab._working_theme.name == "dark"
    assert tab.on_exit() is None


def test_palette_history_without_theme(themes, state):
    tab = tt.ThemeTab()
    tab._push_undo_snapshot()
    assert tab._undo_stack == []
    tab._undo_palette()
    tab._redo_palette()
    assert tab._palette_matches_disk() is True
    tab._working_theme = make_theme("ghost", "G")
    tab.buffer = "ghost"
    assert tab._palette_matches_disk() is True  # not on disk
    tab._set_notify_look("notify_style", "chip")  # works on the working copy
    tab._working_theme = None
    tab._set_notify_look("notify_style", "chip")
    tab._open_palette_color_picker("primary")


def test_undo_stack_is_capped(themes, state, monkeypatch):
    monkeypatch.setattr(tt, "_MAX_PALETTE_HISTORY", 3)
    tab = tt.ThemeTab()
    tab._working_theme = make_theme("mine", "Mine")
    for _ in range(5):
        tab._push_undo_snapshot()
    assert len(tab._undo_stack) == 3


# ----------------------------------------------------------------- build + view

def test_build_renders_header_preview_and_palette(themes, state, notes):
    async def check(user, tab):
        await user.should_see("Colors")
        await user.should_see("Pop-up notifications")
        sel = tab.ui_elements["theme_select"]
        assert sel.options == {"dark": "Dark", "light": "Light", "mine": "Mine"} and sel.value == "dark"
        assert "--preview-color-primary: rgb(115, 0, 255)" in tab._vars_el.content
        assert '.thm-optdots[data-thm="Mine"] i:nth-child(3){background:rgb(115, 0, 255)}' in tab._option_dots_el.content
        assert "thm-dots" in tab._select_dots.content
        tags = tab.ui_elements["theme_type_label"].content
        assert "Dark" in tags and "Built-in" in tags
        assert "Current theme" in tab.ui_elements["_preview_badge"].content
        assert disabled(tab.ui_elements["_discard_btn"]) and disabled(tab.ui_elements["delete_btn"])
        for key in ("palette_undo_btn", "palette_redo_btn", "palette_discard_btn"):
            assert disabled(tab.ui_elements[key])
        swatches = by_class(user.client, "thm-sw")
        assert len(swatches) == len(tt._PALETTE_COLOR_FIELDS)
        assert "is-active" in tab._swatch_els["primary"].classes
        assert 'thm-focus-name">Primary<' in tab.ui_elements["_focus"].content  # "Primary · Primary" collapsed
        assert "#7300FF" in tab.ui_elements["_focus"].content
        assert tab.ui_elements["_readability"].content.count("thm-read") == 4
        styles = by_class(user.client, "thm-nstyle")
        assert len(styles) == 5 and "is-on" in styles[0].classes
        assert "mt is-warning st-accent" in by_class(user.client, "thm-nsample")[0].content
        mock = tab.preview_container
        assert "theme-preview-container" in mock.classes

    build_tab(check)
    assert notes == []


def test_build_with_unknown_saved_theme_and_minimal(themes, state):
    state.settings.current_theme = "gone"
    state.settings.status_footer_minimal = True

    async def check(user, tab):
        assert tab.buffer == "dark"  # falls back to the first option
        assert "Previewing" in tab.ui_elements["_preview_badge"].content

    build_tab(check)


def test_build_with_settings_error_for_minimal(themes, state, monkeypatch):
    calls = {"n": 0}
    real = state.get_app_settings

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:  # the _build_stage lookup
            raise RuntimeError("x")
        return real()

    monkeypatch.setattr(state, "get_app_settings", flaky)

    async def check(user, tab):
        assert tab.preview_container is not None

    build_tab(check)


def test_build_without_theme_manager(monkeypatch, state, notes):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tt, "get_theme_manager", boom)

    async def check(user, tab):
        assert tab.ui_elements["theme_select"].options == {}
        assert tab._working_theme is None
        tab._render_option_dots()
        tab._refresh_theme_list_after_change()
        tab._build_palette_section()
        tab._refresh_focus()
        tab._apply_preview()

    build_tab(check)


def test_theme_change_preview_and_discard(themes, state, notes):
    async def check(user, tab):
        tab.ui_elements["theme_select"].set_value("light")
        assert tab.buffer == "light" and tab.dirty is True
        assert "Light" in tab.ui_elements["theme_type_label"].content
        assert "Previewing" in tab.ui_elements["_preview_badge"].content
        assert not disabled(tab.ui_elements["_discard_btn"])
        tab._on_theme_change("light")  # same: no-op
        tab._on_theme_change(None)
        fire(user, tab.ui_elements["_discard_btn"])
        assert tab.buffer == "dark" and tab.ui_elements["theme_select"].value == "dark" and tab.dirty is False
        # custom theme enables delete
        tab.ui_elements["theme_select"].set_value("mine")
        assert not disabled(tab.ui_elements["delete_btn"])
        assert "Built-in" not in tab.ui_elements["theme_type_label"].content

    build_tab(check)


def test_discard_error(themes, state, notes, monkeypatch):
    tab = tt.ThemeTab()
    monkeypatch.setattr(tab, "_load_from_state", lambda: 1 / 0)
    tab.discard()
    assert notes[-1][1]["title"] == "Couldn't reset the theme editor"


def test_apply_preview_without_buffer(themes, state):
    tab = tt.ThemeTab()
    tab.buffer = None
    tab._apply_preview()
    tab.buffer = "missing"
    tab._apply_preview()  # no theme: nothing
    tab._refresh_view()  # no elements: nothing to update


# ----------------------------------------------------------------- palette editing

def test_swatch_click_opens_picker_and_live_done(themes, state, picker, notes):
    async def check(user, tab):
        fire(user, tab._swatch_els["bg_base"])
        anchor, value, kw = picker[-1]
        assert anchor is tab._swatch_els["bg_base"] and value == "rgb(18, 18, 24)"
        assert kw["title"] == "Backgrounds · Base" and kw["allow_alpha"] is False
        assert kw["note"] == "The preview follows as you pick" and kw["preferred_format"] == "rgb"
        assert tab._focus_field == "bg_base" and "is-active" in tab._swatch_els["bg_base"].classes
        # live preview changes the working copy and the CSS variables
        kw["on_input"]("rgb(1, 2, 3)")
        assert tab._working_theme.bg_base == "rgb(1, 2, 3)"
        assert "rgb(1, 2, 3)" in tab._vars_el.content
        # cancel restores
        kw["on_cancel"]()
        assert tab._working_theme.bg_base == "rgb(18, 18, 24)"
        # done with the same value restores without an undo entry
        kw["on_done"]("rgb(18, 18, 24)")
        kw["on_done"]("")
        assert tab._undo_stack == []
        # done with a new value: undoable, dirty, swatch marked edited
        kw["on_done"]("rgb(9, 9, 9)")
        assert tab._working_theme.bg_base == "rgb(9, 9, 9)" and len(tab._undo_stack) == 1
        assert tab._palette_dirty and tab.dirty
        assert "is-edited" in tab._swatch_els["bg_base"].classes
        assert "Unsaved colors" in tab.ui_elements["_preview_badge"].content
        assert "Use Save as" in tab.ui_elements["_preview_badge"].content  # built-in theme
        assert "thm-focus-dot" in tab.ui_elements["_focus"].content
        assert not disabled(tab.ui_elements["palette_undo_btn"]) and not disabled(tab.ui_elements["palette_discard_btn"])
        # undo / redo through the buttons
        fire(user, tab.ui_elements["palette_undo_btn"])
        assert tab._working_theme.bg_base == "rgb(18, 18, 24)" and not tab._palette_dirty
        fire(user, tab.ui_elements["palette_redo_btn"])
        assert tab._working_theme.bg_base == "rgb(9, 9, 9)"
        # reset colours
        fire(user, tab.ui_elements["palette_discard_btn"])
        assert tab._working_theme.bg_base == "rgb(18, 18, 24)" and tab._undo_stack == [] and not tab._palette_dirty
        # live/done after the working theme went away do nothing
        tab._working_theme = None
        kw["on_input"]("x")
        kw["on_done"]("x")

    build_tab(check)


def test_picker_for_alpha_field_and_title_dedupe(themes, state, picker):
    async def check(user, tab):
        tab._open_palette_color_picker("bg_overlay", "Status · Status")
        kw = picker[-1][2]
        assert kw["title"] == "Status" and kw["allow_alpha"] is True and kw["note"] == "Started at 50% opacity"
        tab._open_palette_color_picker("text_inverse")
        assert picker[-1][2]["title"] == "Text Inverse"
        tab._swatch_els.pop("primary")
        n = len(picker)
        tab._open_palette_color_picker("primary")
        assert len(picker) == n
        tab._working_theme.text_inverse = "weird"
        tab._open_palette_color_picker("text_inverse")
        assert picker[-1][2]["allow_alpha"] is False
        tab._vars_el = None
        picker[-1][2]["on_input"]("#123")

    build_tab(check)


def test_focus_label_for_group_named_field(themes, state, picker):
    async def check(user, tab):
        tab._focus_field = "unknown_field"
        tab._refresh_focus()
        assert "not set" in tab.ui_elements["_focus"].content

    build_tab(check)


def test_notify_look_controls(themes, state):
    async def check(user, tab):
        styles = by_class(user.client, "thm-nstyle")
        fire(user, styles[4])  # glass
        assert tab._working_theme.notify_style == "glass" and len(tab._undo_stack) == 1
        assert "is-on" in by_class(user.client, "thm-nstyle")[4].classes
        tab._set_notify_look("notify_style", "glass")  # unchanged: no new undo entry
        assert len(tab._undo_stack) == 1
        def selects():  # the section is rebuilt after every change
            return [e for e in tab.notify_container.descendants() if isinstance(e, ui.select)]

        selects()[0].set_value("20")
        selects()[1].set_value("strong")
        selects()[2].set_value("compact")
        assert (tab._working_theme.notify_radius, tab._working_theme.notify_shadow, tab._working_theme.notify_density) == (
            "20", "strong", "compact")
        selects()[0].set_value(None)
        assert tab._working_theme.notify_radius == "12"
        sw = [e for e in tab.notify_container.descendants() if isinstance(e, ui.switch)][0]
        sw.set_value(False)
        assert tab._working_theme.notify_timer == "off"
        sample = by_class(user.client, "thm-nsample")[0].content
        assert "mt-bar" not in sample
        sw = [e for e in tab.notify_container.descendants() if isinstance(e, ui.switch)][0]
        sw.set_value(True)
        assert tab._working_theme.notify_timer == "on"
        # a deleted container is skipped
        tab.notify_container.delete()
        tab._build_notify_section(tab._working_theme)
        tab.notify_container = None
        tab._build_notify_section(tab._working_theme)

    build_tab(check)


# ----------------------------------------------------------------- apply (save)

def test_apply_theme_switch(themes, state, notes, applied):
    async def check(user, tab):
        tab.ui_elements["theme_select"].set_value("light")
        fire(user, button(user.client, "Apply"))
        assert state.updates == [("current_theme", "light")] and state.saves == 1
        assert applied == ["light"] and tab.dirty is False
        assert "Current theme" in tab.ui_elements["_preview_badge"].content

    build_tab(check)
    assert notes[-1][1]["title"] == "Theme “Light” applied"


def test_apply_custom_palette_edits(themes, state, notes, applied):
    state.settings.current_theme = "mine"

    async def check(user, tab):
        tab._working_theme.primary = "#123456"
        tab._mark_palette_dirty()
        tab._refresh_view()
        assert "Apply saves these colors" in tab.ui_elements["_preview_badge"].content
        tab.save()
        assert themes.updated[-1].primary == "#123456" and themes.updated[-1].display_name == "Mine"
        assert themes.themes["mine"].primary == "#123456"
        assert not tab._palette_dirty

    build_tab(check)
    assert applied == ["mine"]


def test_apply_custom_palette_when_theme_missing_on_disk(themes, state, notes, applied):
    async def check(user, tab):
        tab.buffer = "newone"
        tab._working_theme = make_theme("x", "X")
        tab._palette_dirty = True
        tab.save()
        assert themes.updated[-1].name == "newone" and themes.updated[-1].display_name == "X"

    build_tab(check)


def test_apply_custom_palette_update_fails(themes, state, notes, applied):
    state.settings.current_theme = "mine"
    themes.update_ok = False

    async def check(user, tab):
        tab._working_theme.primary = "#123456"
        tab._mark_palette_dirty()
        tab.save()

    build_tab(check)
    assert notes[-1][1]["title"] == "Couldn't save your color changes to “Mine”"
    assert state.updates == [] and applied == []


def test_apply_builtin_palette_edits_refused(themes, state, notes, applied):
    async def check(user, tab):
        tab._working_theme.primary = "#123456"
        tab._mark_palette_dirty()
        tab.save()

    build_tab(check)
    assert notes[-1][1]["title"] == "Built-in themes can't be changed"
    assert themes.updated == [] and state.updates == []


def test_apply_without_buffer(themes, state, notes):
    tab = tt.ThemeTab()
    tab.buffer = ""
    tab.save()
    assert notes[-1][1]["title"] == "Pick a theme first"


def test_apply_save_fails_or_apply_fails(themes, state, notes, monkeypatch):
    state.save_ok = False
    tab = tt.ThemeTab()
    tab.buffer = "light"
    tab.save()
    assert notes == []
    state.save_ok = True
    monkeypatch.setattr(mainuiwindow, "apply_theme", lambda name: False)
    tab.save()
    assert notes == []


def test_apply_exception(themes, state, notes, monkeypatch):
    def boom(name):
        raise RuntimeError("css")

    monkeypatch.setattr(mainuiwindow, "apply_theme", boom)
    tab = tt.ThemeTab()
    tab.buffer = "light"
    tab.save()
    assert notes[-1][1]["title"] == "Couldn't apply the theme" and "RuntimeError: css" in notes[-1][1]["details"]


def test_display_name_error(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tt, "get_theme_manager", boom)
    assert tt.ThemeTab()._display_name("abc") == "abc"


# ----------------------------------------------------------------- save as

def _fill_save_as(user, name, display, kind=None):
    inputs = [e for e in user.client.elements.values() if isinstance(e, ui.input)]
    by_placeholder = {e.props.get("placeholder"): e for e in inputs}
    by_placeholder["my_custom_theme"].set_value(name)
    by_placeholder["My Custom Theme"].set_value(display)
    if kind:
        [e for e in user.client.elements.values() if isinstance(e, ui.radio)][0].set_value(kind)


@pytest.mark.parametrize(
    "name, display, title",
    [("", "X", "Enter a theme name"), ("ok", "  ", "Enter a display name"),
     ("bad name!", "X", "Theme names can only use letters, numbers and underscores"),
     ("mine", "X", "A theme called “mine” already exists")],
)
def test_save_as_validation(themes, state, notes, name, display, title):
    async def check(user, tab):
        fire(user, button(user.client, "Save as"))
        await user.should_see("Save Theme As")
        _fill_save_as(user, name, display)
        fire(user, button(user.client, "Save"))

    build_tab(check)
    assert notes[-1][1]["title"] == title
    assert themes.saved == []


def test_save_as_success(themes, state, notes):
    async def check(user, tab):
        tab._working_theme.primary = "#abcdef"
        tab._open_save_as_dialog()
        _fill_save_as(user, "fresh", "Fresh One", "light")
        fire(user, button(user.client, "Save"))
        assert tab.buffer == "fresh" and tab.dirty is True
        assert tab.ui_elements["theme_select"].value == "fresh"
        assert "fresh" in tab.ui_elements["theme_select"].options

    build_tab(check)
    saved = themes.saved[-1]
    assert (saved.name, saved.display_name, saved.theme_type, saved.primary) == ("fresh", "Fresh One", "light", "#abcdef")
    assert notes[-1][1]["title"] == "Theme “Fresh One” created"


def test_save_as_save_fails_and_cancel(themes, state, notes):
    themes.save_ok = False

    async def check(user, tab):
        tab._open_save_as_dialog()
        _fill_save_as(user, "fresh", "Fresh")
        fire(user, button(user.client, "Save"))
        fire(user, button(user.client, "Cancel"))

    build_tab(check)
    assert notes[-1][1]["title"] == "Couldn't save the new theme"


def test_save_as_guards(themes, state, notes, monkeypatch):
    tab = tt.ThemeTab()
    tab._open_save_as_dialog()  # no working theme
    assert notes[-1][1]["title"] == "Pick a theme to base the new one on"
    monkeypatch.setattr(tab, "_get_theme_manager", lambda: None)
    assert tab._execute_save_as({}, None) is None


# ----------------------------------------------------------------- delete

def test_delete_flow_current_theme(themes, state, notes, applied):
    state.settings.current_theme = "mine"

    async def check(user, tab):
        fire(user, tab.ui_elements["delete_btn"])
        await user.should_see('Delete "Mine" (mine)?')
        fire(user, button(user.client, "Delete"))
        assert tab.buffer == "dark" and "mine" not in tab.ui_elements["theme_select"].options

    build_tab(check)
    assert themes.deleted == ["mine"]
    assert state.updates == [("current_theme", "dark")] and applied == ["dark"]
    assert notes[-1][1]["title"] == "Deleted “Mine”" and notes[-1][0] == "Switched to Dark."


def test_delete_non_current_theme_and_cancel(themes, state, notes, applied):
    async def check(user, tab):
        tab.ui_elements["theme_select"].set_value("mine")
        tab._confirm_delete_theme()
        fire(user, button(user.client, "Cancel"))
        assert themes.deleted == []
        tab._confirm_delete_theme()
        fire(user, button(user.client, "Delete"))

    build_tab(check)
    assert state.updates == [] and applied == []
    assert notes[-1][0] == ""


def test_delete_apply_error_and_failure(themes, state, notes, monkeypatch):
    state.settings.current_theme = "mine"

    def boom(name):
        raise RuntimeError("x")

    monkeypatch.setattr(mainuiwindow, "apply_theme", boom)
    closed = []
    dialog = SimpleNamespace(close=lambda: closed.append(1))
    tab = tt.ThemeTab()
    tab.buffer = "mine"
    tab._execute_delete_theme(dialog)
    assert notes[-1][0] == "" and closed == [1]
    themes.themes["other"] = make_theme("other", "Other")
    themes.delete_ok = False
    tab.buffer = "other"
    tab._execute_delete_theme(dialog)
    assert notes[-1][1]["title"] == "Couldn't delete “Other”"


def test_delete_guards(themes, state, notes, monkeypatch):
    tab = tt.ThemeTab()
    tab.buffer = ""
    tab._confirm_delete_theme()
    assert notes[-1][1]["title"] == "Pick a theme first"
    tab.buffer = "dark"
    tab._confirm_delete_theme()
    assert notes[-1][1]["title"] == "Built-in themes can't be deleted"
    closed = []
    dialog = SimpleNamespace(close=lambda: closed.append(1))
    tab._execute_delete_theme(dialog)
    tab.buffer = "mine"
    monkeypatch.setattr(tab, "_get_theme_manager", lambda: None)
    tab._execute_delete_theme(dialog)
    assert closed == [1, 1] and themes.deleted == []


def test_confirm_delete_without_manager(state, notes, monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tt, "get_theme_manager", boom)

    def build():
        tab = tt.ThemeTab()
        tab.buffer = "custom"
        tab._confirm_delete_theme()

    async def check(user):
        await user.should_see('Delete "custom" (custom)?')

    run_ui(build, check)


def test_refresh_theme_list(themes, state):
    async def check(user, tab):
        tab.buffer = "missing"
        tab._refresh_theme_list()
        assert tab.buffer == "dark"
        themes.themes = {}
        tab._refresh_theme_list_after_change("x")
        assert tab.ui_elements["theme_select"].options == {}
        del tab.ui_elements["theme_select"]
        tab._refresh_theme_list_after_change()

    build_tab(check)


def test_option_dots_skip_missing_theme_and_escape(themes, state):
    async def check(user, tab):
        themes.themes['q"uote'] = make_theme('q"uote', 'Q"uote', primary="red;}</style>")
        tab._render_option_dots([("ghost", "Ghost"), ('q"uote', 'Q"uote')])
        content = tab._option_dots_el.content
        assert "Ghost" not in content
        assert 'data-thm="Q\\"uote"' in content
        assert "i:nth-child(3){background:redstyle}" in content  # unsafe characters stripped
        assert content.count("</style>") == 1

    build_tab(check)

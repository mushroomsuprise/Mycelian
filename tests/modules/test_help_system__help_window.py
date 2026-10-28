# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/help_window.py: theme push, the /help page, registration, geometry
and opening (never opens a real native window)."""

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

from modules import dataobjects, theme_manager, tray_controller  # noqa: E402
from modules.help_system import help_browser, help_window  # noqa: E402
from modules.theme_manager import ThemeColors  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class _ThemeMgr:
    def __init__(self, theme, theme_type="dark"):
        self.theme = theme
        self.theme_type = theme_type
        self.asked = []

    def get_theme(self):
        return self.theme

    def get_theme_type(self, name):
        self.asked.append(name)
        return self.theme_type


class _State:
    def __init__(self, exc=None, **values):
        self.settings = SimpleNamespace(help_window_width=0, help_window_height=0, help_window_x=0,
                                        help_window_y=0, help_window_has_position=False, **values)
        self.exc = exc
        self.updates = []
        self.saved = 0

    def get_app_settings(self):
        if self.exc:
            raise self.exc
        return self.settings

    def update_app_setting(self, field, value):
        self.updates.append((field, value))
        setattr(self.settings, field, value)
        return True

    def save_changes(self):
        self.saved += 1
        return True


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(help_window, "_live", {})
    monkeypatch.setattr(help_window, "_registered", False)
    monkeypatch.setattr(help_browser, "_help_system_styles_injected", False)
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)


@pytest.fixture
def theme(monkeypatch):
    mgr = _ThemeMgr(ThemeColors())
    monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: mgr)
    return mgr


# ----------------------------------------------------------------- theme

def test_current_theme_uses_manager(theme):
    t, kind = help_window._current_theme()
    assert t is theme.theme and kind == "dark"
    assert theme.asked == [theme.theme.name]


def test_current_theme_without_theme(monkeypatch):
    mgr = _ThemeMgr(None, "light")
    monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: mgr)
    assert help_window._current_theme() == (None, "dark")
    assert mgr.asked == []


def test_theme_css_combines_variables_and_quasar_brand():
    from modules.mainuiwindow import _get_quasar_brand_css

    t = ThemeColors()
    css = help_window._theme_css(t)
    assert css == theme_manager.generate_css_variables(t) + _get_quasar_brand_css(t)
    assert "--color-primary" in css and "--q-primary" in css


@pytest.mark.parametrize(
    "value, expected",
    [("rgb(300, 0, 1)", "#ff0001"), ("  #00ff00 ", "#00ff00"), ("", None), (None, None), ("#abc", None)],
)
def test_rgb_to_hex_more(value, expected):
    assert help_window._rgb_to_hex(value) == expected


def test_push_theme_noop_without_windows(theme, monkeypatch):
    called = []
    monkeypatch.setattr(help_window, "_current_theme", lambda: called.append(1))
    help_window.push_theme()
    assert called == []


class _JSClient:
    def __init__(self, fail=False):
        self.js = []
        self.fail = fail

    def run_javascript(self, code):
        if self.fail:
            raise RuntimeError("socket closed")
        self.js.append(code)


@pytest.mark.parametrize("kind, dark", [("dark", "true"), ("light", "false")])
def test_push_theme_sends_css_to_every_window(monkeypatch, kind, dark):
    mgr = _ThemeMgr(ThemeColors(), kind)
    monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: mgr)
    monkeypatch.setattr(help_window, "_theme_css", lambda t: ".x{color:red}")
    good, bad = _JSClient(), _JSClient(fail=True)
    help_window._live.update({"a": (good, None), "b": (bad, None)})
    help_window.push_theme()
    assert len(good.js) == 1
    js = good.js[0]
    assert "style.textContent = \".x{color:red}\";" in js
    assert f"Quasar.Dark.set({dark})" in js
    assert help_window._THEME_STYLE_ID in js


def test_push_theme_without_theme(monkeypatch):
    monkeypatch.setattr(help_window, "_current_theme", lambda: (None, "dark"))
    c = _JSClient()
    help_window._live["a"] = (c, None)
    help_window.push_theme()
    assert c.js == []


def test_push_theme_theme_error(monkeypatch):
    def boom():
        raise RuntimeError("no themes")

    monkeypatch.setattr(help_window, "_current_theme", boom)
    c = _JSClient()
    help_window._live["a"] = (c, None)
    help_window.push_theme()
    assert c.js == []


# ----------------------------------------------------------------- page

def test_help_page_builds_browser_and_tracks_client(theme, monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State())
    made = {}

    def build():
        help_window._help_page(topic="twitch_setup")
        made["live"] = dict(help_window._live)

    async def check(user):
        await user.should_see("Connect Twitch")
        await user.should_see("Help Center")
        assert user.client.id in help_window._live
        # Closing the window deletes the client; its delete handlers drop the entry.
        for handler in user.client.delete_handlers:
            user.client.safe_invoke(handler)
        made["after_delete"] = dict(help_window._live)

    run_ui(build, check)
    assert len(made["live"]) == 1
    (client, browser), = made["live"].values()
    assert browser._page_mode is True
    assert browser.current_topic.id == "twitch_setup"
    assert help_window._THEME_STYLE_ID in client._head_html
    assert "help-window-root" in client._head_html
    assert made["after_delete"] == {}


def test_help_page_category_and_theme_failure(monkeypatch):
    def boom():
        raise RuntimeError("theme broken")

    monkeypatch.setattr(help_window, "_current_theme", boom)

    def build():
        help_window._help_page(category="alerts")

    async def check(user):
        await user.should_see("Alerts")
        assert any(b.current_topic and b.current_topic.category.value == "alerts"
                   for _, b in help_window._live.values())

    run_ui(build, check)


def test_help_page_welcome_by_default(theme):
    def build():
        help_window._help_page()

    async def check(user):
        await user.should_see("Welcome to Mycelian Help")

    run_ui(build, check)


# ----------------------------------------------------------------- geometry

@pytest.fixture
def state(monkeypatch):
    s = _State()
    monkeypatch.setattr(dataobjects, "state_manager", s)
    return s


def test_save_geometry_no_args(state):
    help_window._save_geometry(SimpleNamespace())
    help_window._save_geometry(SimpleNamespace(args=None))
    assert state.updates == [] and state.saved == 0


def test_save_geometry_unchanged_values_not_saved(state):
    state.settings.help_window_width = 800
    state.settings.help_window_height = 600
    help_window._save_geometry(SimpleNamespace(args={"width": 800, "height": 600}))
    assert state.updates == [] and state.saved == 0


def test_save_geometry_position_only(state):
    help_window._save_geometry(SimpleNamespace(args={"width": 10, "height": 10, "x": 0, "y": 5}))
    # x=0 equals the stored value, so only the changed fields are written
    assert state.updates == [("help_window_y", 5), ("help_window_has_position", True)]
    assert state.saved == 1


def test_save_geometry_update_returns_false(state, monkeypatch):
    monkeypatch.setattr(state, "update_app_setting", lambda f, v: False)
    help_window._save_geometry(SimpleNamespace(args={"width": 900, "height": 700}))
    assert state.saved == 0


def test_save_geometry_errors_swallowed(monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State(exc=RuntimeError("db")))
    help_window._save_geometry(SimpleNamespace(args={"width": "wide"}))  # bad int and bad state
    help_window._save_geometry(SimpleNamespace(args={"width": 900, "height": 700}))


# ----------------------------------------------------------------- registration

def test_register_help_page_once(monkeypatch):
    pages = []

    def fake_page(path, title=None):
        def deco(fn):
            pages.append((path, title, fn))
            return fn
        return deco

    monkeypatch.setattr(ui, "page", fake_page)
    from nicegui.native import event_manager as em_mod

    handlers = []
    monkeypatch.setattr(em_mod.event_manager, "on", lambda name, fn: handlers.append((name, fn)))
    help_window.register_help_page()
    help_window.register_help_page()
    assert pages == [(help_window.HELP_PATH, help_window.HELP_WINDOW_TITLE, help_window._help_page)]
    assert handlers == [("mycelian_help_geometry", help_window._save_geometry)]
    assert help_window._registered is True


def test_register_help_page_without_native_event_manager(monkeypatch):
    monkeypatch.setattr(ui, "page", lambda path, title=None: (lambda fn: fn))
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", None)  # import -> ImportError
    help_window.register_help_page()
    assert help_window._registered is True


# ----------------------------------------------------------------- opening helpers

def test_native_available(monkeypatch):
    from nicegui import app

    monkeypatch.setattr(app.native, "main_window", object(), raising=False)
    assert help_window._native_available() is True
    monkeypatch.setattr(app.native, "main_window", None, raising=False)
    assert help_window._native_available() is False


def test_native_available_error(monkeypatch):
    import nicegui

    class _App:
        @property
        def native(self):
            raise RuntimeError("no native")

    monkeypatch.setattr(nicegui, "app", _App())
    assert help_window._native_available() is False


def test_window_options_background_and_bad_settings(monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State(exc=RuntimeError("x")))
    monkeypatch.setattr(help_window, "_current_theme", lambda: (SimpleNamespace(bg_elevated="rgb(1, 2, 3)"), "dark"))
    opts = help_window._window_options()
    assert opts == {"title": help_window.HELP_WINDOW_TITLE, "background_color": "#010203"}


def test_window_options_theme_errors_and_no_color(monkeypatch, state):
    monkeypatch.setattr(help_window, "_current_theme", lambda: (None, "dark"))
    assert "background_color" not in help_window._window_options()

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(help_window, "_current_theme", boom)
    assert help_window._window_options() == {"title": help_window.HELP_WINDOW_TITLE}


def test_help_url(monkeypatch):
    monkeypatch.setattr(tray_controller, "get_window_url", lambda: "")
    assert help_window._help_url("x", None) is None
    monkeypatch.setattr(tray_controller, "get_window_url", lambda: "http://127.0.0.1:1/")
    assert help_window._help_url(None, None) == "http://127.0.0.1:1/help"
    assert help_window._help_url("a b", "c") == "http://127.0.0.1:1/help?topic=a+b&category=c"


def test_open_without_url_returns_false(monkeypatch):
    pushed = []
    monkeypatch.setattr(tray_controller, "push_window_method", lambda *a: pushed.append(a) or True)
    monkeypatch.setattr(help_window, "_native_available", lambda: True)
    monkeypatch.setattr(help_window, "_help_url", lambda t, c: None)
    assert help_window.open_help_window("x") is False
    assert pushed == []


class _CtxClient:
    has_socket_connection = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_open_live_window_category_and_plain_focus(monkeypatch):
    pushed = []
    monkeypatch.setattr(tray_controller, "push_window_method", lambda *a: pushed.append(a) or True)
    calls = []
    browser = SimpleNamespace(navigate_to=lambda t: calls.append(("topic", t)),
                              _navigate_to_category=lambda c: calls.append(("cat", c)))
    help_window._live["c"] = (_CtxClient(), browser)
    assert help_window.open_help_window(category="alerts") is True
    assert help_window.open_help_window() is True
    assert calls == [("cat", "alerts")]
    assert pushed == [("mycelian_focus_help_window",), ("mycelian_focus_help_window",)]


def test_open_live_navigation_failure_reopens(monkeypatch):
    pushed = []
    monkeypatch.setattr(tray_controller, "push_window_method", lambda *a: pushed.append(a) or "OPENED")
    monkeypatch.setattr(tray_controller, "get_window_url", lambda: "http://127.0.0.1:2")
    monkeypatch.setattr(help_window, "_native_available", lambda: True)
    monkeypatch.setattr(help_window, "_window_options", lambda: {"title": "T"})

    def broken(topic):
        raise RuntimeError("client gone")

    help_window._live["c"] = (_CtxClient(), SimpleNamespace(navigate_to=broken))
    assert help_window.open_help_window("guide_psn") == "OPENED"
    assert pushed == [("mycelian_open_help_window", "http://127.0.0.1:2/help?topic=guide_psn", {"title": "T"})]

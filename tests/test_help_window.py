# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Main-process logic of the separate help window (modules/help_system/help_window.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules import dataobjects, tray_controller
from modules.help_system import help_window


class FakeStateManager:
    def __init__(self, **values):
        self.settings = SimpleNamespace(
            help_window_width=0,
            help_window_height=0,
            help_window_x=0,
            help_window_y=0,
            help_window_has_position=False,
        )
        for k, v in values.items():
            setattr(self.settings, k, v)
        self.saved = 0

    def get_app_settings(self):
        return self.settings

    def update_app_setting(self, field, value):
        setattr(self.settings, field, value)
        return True

    def save_changes(self):
        self.saved += 1
        return True


@pytest.fixture
def state(monkeypatch):
    fake = FakeStateManager()
    monkeypatch.setattr(dataobjects, "state_manager", fake)
    return fake


@pytest.fixture
def pushed(monkeypatch):
    calls = []
    monkeypatch.setattr(tray_controller, "push_window_method", lambda name, *a: calls.append((name, a)) or True)
    monkeypatch.setattr(tray_controller, "get_window_url", lambda: "http://127.0.0.1:8080/")
    monkeypatch.setattr(help_window, "_live", {})
    return calls


def test_rgb_to_hex():
    assert help_window._rgb_to_hex("rgb(25, 25, 35)") == "#191923"
    assert help_window._rgb_to_hex("rgba(255, 0, 10, 0.5)") == "#ff000a"
    assert help_window._rgb_to_hex("#AbCdEf") == "#AbCdEf"
    assert help_window._rgb_to_hex("not a color") is None


def test_save_geometry_stores_size_and_position(state):
    help_window._save_geometry(SimpleNamespace(args={"width": 1000, "height": 700, "x": -1200, "y": 40}))
    s = state.settings
    assert (s.help_window_width, s.help_window_height) == (1000, 700)
    assert (s.help_window_x, s.help_window_y, s.help_window_has_position) == (-1200, 40, True)
    assert state.saved == 1


def test_save_geometry_ignores_tiny_sizes(state):
    help_window._save_geometry(SimpleNamespace(args={"width": 100, "height": 80}))
    assert state.settings.help_window_width == 0
    assert state.saved == 0


def test_window_options_use_saved_geometry(state):
    state.settings.help_window_width = 1000
    state.settings.help_window_height = 700
    state.settings.help_window_has_position = True
    state.settings.help_window_x = 15
    state.settings.help_window_y = 30
    options = help_window._window_options()
    assert options["width"] == 1000 and options["height"] == 700
    assert (options["x"], options["y"]) == (15, 30)
    assert options["title"] == help_window.HELP_WINDOW_TITLE


def test_window_options_without_saved_position(state):
    options = help_window._window_options()
    assert "x" not in options and "width" not in options


def test_open_returns_false_without_native(pushed, monkeypatch):
    monkeypatch.setattr(help_window, "_native_available", lambda: False)
    assert help_window.open_help_window("twitch_setup") is False
    assert pushed == []


def test_open_pushes_window_request(pushed, state, monkeypatch):
    monkeypatch.setattr(help_window, "_native_available", lambda: True)
    assert help_window.open_help_window("twitch_setup") is True
    name, (url, options) = pushed[0]
    assert name == "mycelian_open_help_window"
    assert url == "http://127.0.0.1:8080/help?topic=twitch_setup"
    assert options["title"] == help_window.HELP_WINDOW_TITLE


def test_open_category_url(pushed, state, monkeypatch):
    monkeypatch.setattr(help_window, "_native_available", lambda: True)
    help_window.open_help_window(category="connection_guides")
    assert pushed[0][1][0].endswith("/help?category=connection_guides")


def test_open_reuses_live_window(pushed, monkeypatch):
    navigated = []

    class FakeClient:
        has_socket_connection = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    browser = SimpleNamespace(navigate_to=navigated.append, _navigate_to_category=navigated.append)
    help_window._live["c1"] = (FakeClient(), browser)
    monkeypatch.setattr(help_window, "_native_available", lambda: True)

    assert help_window.open_help_window("guide_psn") is True
    assert navigated == ["guide_psn"]
    assert pushed == [("mycelian_focus_help_window", ())]


def test_disconnected_live_window_is_dropped(pushed, state, monkeypatch):
    help_window._live["gone"] = (SimpleNamespace(has_socket_connection=False), SimpleNamespace())
    monkeypatch.setattr(help_window, "_native_available", lambda: True)
    help_window.open_help_window("guide_psn")
    assert "gone" not in help_window._live
    assert pushed[0][0] == "mycelian_open_help_window"

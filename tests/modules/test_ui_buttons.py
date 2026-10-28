# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_buttons.py: button factories, their Quasar props, classes and click wiring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import ui_buttons as ub  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def _props(el):
    return dict(el.props)


def _classes(el):
    return list(el.classes)


@pytest.mark.parametrize(
    "factory, expected_props",
    [
        (ub.primary_button, {"color": "primary", "unelevated": True, "no-caps": True}),
        (ub.outline_button, {"outline": True, "color": "primary", "no-caps": True}),
        (ub.destructive_button, {"color": "negative", "outline": True, "no-caps": True}),
        (ub.success_button, {"color": "positive", "unelevated": True, "no-caps": True}),
    ],
)
def test_styled_factories_set_props_classes_and_click(factory, expected_props):
    made = {}
    clicks = []

    def build():
        made["btn"] = factory("Do it", lambda: clicks.append("x"), icon="add", extra_classes="extra-one")

    async def check(user):
        await user.should_see("Do it")
        user.find("Do it").click()

    run_ui(build, check)
    btn = made["btn"]
    props = _props(btn)
    for key, value in expected_props.items():
        assert props.get(key) == value
    assert props.get("icon") == "add"
    assert _classes(btn) == ["mycelian-btn", "extra-one"]
    assert clicks == ["x"]


def test_styled_button_without_extra_classes_only_base_class():
    made = {}

    def build():
        made["btn"] = ub.primary_button("Plain", lambda: None)

    run_ui(build)
    assert _classes(made["btn"]) == ["mycelian-btn"]
    assert "icon" not in _props(made["btn"])


@pytest.mark.parametrize("dense, has_dense", [(True, True), (False, False)])
def test_dock_control_button_dense_switch(dense, has_dense):
    made = {}
    clicks = []

    def build():
        made["btn"] = ub.dock_control_button("Dock", lambda: clicks.append(1), dense=dense, extra_classes="z")

    async def check(user):
        user.find("Dock").click()

    run_ui(build, check)
    props = _props(made["btn"])
    assert props.get("flat") is True and props.get("no-caps") is True
    assert ("dense" in props) is has_dense
    assert _classes(made["btn"]) == ["mycelian-btn", "control-button", "z"]
    assert clicks == [1]


def test_dock_control_button_default_classes():
    made = {}

    def build():
        made["btn"] = ub.dock_control_button("Dock", lambda: None)

    run_ui(build)
    assert _classes(made["btn"]) == ["mycelian-btn", "control-button"]


def test_warning_button():
    made = {}
    clicks = []

    def build():
        made["a"] = ub.warning_button("Spin", lambda: clicks.append("spin"), icon="refresh")
        made["b"] = ub.warning_button("Other", lambda: None, extra_classes="k")

    async def check(user):
        user.find("Spin").click()

    run_ui(build, check)
    assert _props(made["a"]).get("flat") is True
    assert _classes(made["a"]) == ["mycelian-btn", "btn-warning"]
    assert _classes(made["b"]) == ["mycelian-btn", "btn-warning", "k"]
    assert clicks == ["spin"]


def test_browser_link_opens_system_browser(monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new=0: opened.append((url, new)))
    made = {}

    def build():
        made["btn"] = ub.browser_link("Docs", "https://example.com/x")

    async def check(user):
        user.find("Docs").click()

    run_ui(build, check)
    assert opened == [("https://example.com/x", 2)]
    props = _props(made["btn"])
    assert props.get("align") == "left" and props.get("dense") is True
    assert _classes(made["btn"]) == ["text-theme-info", "px-0"]


@pytest.mark.parametrize("dense", [True, False])
def test_themed_control_button(dense):
    made = {}
    clicks = []

    def build():
        made["btn"] = ub.themed_control_button("Theme", lambda: clicks.append(1), icon="star", dense=dense)

    async def check(user):
        user.find("Theme").click()

    run_ui(build, check)
    props = _props(made["btn"])
    assert ("dense" in props) is dense
    assert props.get("flat") is True
    assert _classes(made["btn"]) == ["mycelian-btn", "control-button"]
    assert clicks == [1]


class _FakeBtn:
    def __init__(self):
        self.props_calls = []
        self.class_calls = []

    def props(self, value):
        self.props_calls.append(value)
        return self

    def classes(self, value):
        self.class_calls.append(value)
        return self


def test_apply_flat_btn_props_returns_same_button():
    b = _FakeBtn()
    assert ub.apply_flat_btn_props(b) is b
    assert ub.apply_flat_btn_props(b, dense=True) is b
    assert b.props_calls == ["flat no-caps", "flat no-caps dense"]


def test_apply_btn_classes_strips_whitespace():
    b = _FakeBtn()
    assert ub._apply_btn_classes(b, "") is b
    ub._apply_btn_classes(b, "a b")
    assert b.class_calls == ["mycelian-btn", "mycelian-btn a b"]

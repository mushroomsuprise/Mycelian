# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_form_controls.py: themed inputs, tooltips, masked fields and copy button."""

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

from modules import notification_engine  # noqa: E402
from modules import ui_form_controls as fc  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def _tooltips(el):
    """ui.tooltip(text) creates a sibling Tooltip whose target is the element's html id."""
    return [t for t in el.client.elements.values()
            if isinstance(t, ui.tooltip) and t.props.get("target") == f"#{el.html_id}"]


def _build(fn):
    made = {}

    def build():
        made["el"] = fn()

    run_ui(build)
    return made["el"]


def test_with_tooltip_adds_tooltip_class():
    made = {}

    def build():
        made["a"] = fc._with_tooltip(ui.label("x"), "Help text")
        made["b"] = fc._with_tooltip(ui.label("y"), "")

    run_ui(build)
    assert [t.text for t in _tooltips(made["a"])] == ["Help text"]
    assert "bg-theme-surface" in made["a"].classes
    assert _tooltips(made["b"]) == []
    assert "bg-theme-surface" not in made["b"].classes


def test_form_select():
    changes = []
    el = _build(lambda: fc.form_select(tooltip="Pick", options=["a", "b"], label="L", value="a",
                                       on_change=lambda e: changes.append(e.value)))
    assert isinstance(el, ui.select)
    assert el.value == "a"
    assert list(el.classes) == ["w-full", "bg-theme-surface"]
    assert el.props.get("outlined") is True and el.props.get("dense") is True
    assert el.props.get("label") == "L"
    el.set_value("b")
    assert changes == ["b"]
    assert [t.text for t in _tooltips(el)] == ["Pick"]


def test_form_select_dict_options_custom_classes_no_tooltip():
    el = _build(lambda: fc.form_select(tooltip="", options={"k": "Key"}, value="k", classes="w-40"))
    assert list(el.classes) == ["w-40"]
    assert _tooltips(el) == []


def test_form_input_defaults_and_readonly():
    el = _build(lambda: fc.form_input(tooltip="tip", label="Name", value="v", placeholder="ph", readonly=True))
    assert isinstance(el, ui.input)
    assert el.value == "v"
    assert el.props.get("readonly") is True
    assert el.props.get("placeholder") == "ph"
    assert el.props.get("outlined") is True


def test_form_input_not_readonly_and_change():
    seen = []
    el = _build(lambda: fc.form_input(tooltip="t", on_change=lambda e: seen.append(e.value)))
    assert "readonly" not in el.props
    el.set_value("typed")
    assert seen == ["typed"]


def test_form_sensitive_input_masks_with_toggle():
    el = _build(lambda: fc.form_sensitive_input(tooltip="Secret", label="Key", value="abc", readonly=True))
    assert el.props.get("type") == "password"
    assert el.props.get("readonly") is True
    assert el.value == "abc"
    # password toggle button is rendered in the append slot
    assert "append" in el.slots


def test_form_sensitive_input_unmasked():
    el = _build(lambda: fc.form_sensitive_input(tooltip="", password=False))
    assert el.props.get("type") != "password"


@pytest.mark.parametrize(
    "value, display",
    [(None, ""), (5.0, "5"), (7, "7"), (2.5, "2.5"), ("12", "12")],
)
def test_form_sensitive_number_display(value, display):
    el = _build(lambda: fc.form_sensitive_number(tooltip="n", value=value))
    assert el.value == display
    for key in ("min", "max", "step"):
        assert key not in el.props


def test_form_sensitive_number_bounds():
    el = _build(lambda: fc.form_sensitive_number(tooltip="n", value=3, min=1, max=10, step=0.5))
    assert str(el.props.get("min")) == "1"
    assert str(el.props.get("max")) == "10"
    assert str(el.props.get("step")) == "0.5"
    assert el.props.get("type") == "password"


def test_form_number_default_width_and_custom():
    el = _build(lambda: fc.form_number(tooltip="Num", label="N", value=4, min=0, max=9, step=1))
    assert isinstance(el, ui.number)
    assert el.value == 4
    assert list(el.classes) == ["w-24", "bg-theme-surface"]
    el2 = _build(lambda: fc.form_number(tooltip="", classes="w-full"))
    assert list(el2.classes) == ["w-full"]


def test_form_textarea_rows():
    el = _build(lambda: fc.form_textarea(tooltip="T", label="Body", value="hi", placeholder="p", rows=6))
    assert isinstance(el, ui.textarea)
    assert str(el.props.get("rows")) == "6"
    assert el.value == "hi"
    el2 = _build(lambda: fc.form_textarea(tooltip=""))
    assert str(el2.props.get("rows")) == "3"


def test_copy_text_button_copies_and_notifies(monkeypatch):
    notes = []
    js = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **k: notes.append((msg, k)))
    made = {}

    def build():
        made["btn"] = fc.copy_text_button('http://localhost/cb?"x"', tooltip="Copy URI")

    async def check(user):
        monkeypatch.setattr(fc.ui, "run_javascript", lambda code: js.append(code))
        made["btn"].props["label"] = "copybtn"
        user.find(ui.button).click()

    run_ui(build, check)
    assert js == ['navigator.clipboard.writeText("http://localhost/cb?\\"x\\"")']
    assert notes == [("Redirect URI copied", {"type": "positive"})]
    btn = made["btn"]
    assert btn.props.get("icon") == "content_copy"
    assert [t.text for t in _tooltips(btn)] == ["Copy URI"]

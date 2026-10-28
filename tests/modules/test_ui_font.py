# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_font.py: font CSS block, UI scale zoom and font injection JS."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nicegui  # noqa: E402
import pytest  # noqa: E402

from modules import dataobjects  # noqa: E402
from modules import ui_font  # noqa: E402
from modules.font_utils import SYSTEM_DEFAULT_LABEL  # noqa: E402


class _State:
    def __init__(self, settings=None, exc=None):
        self.settings = settings
        self.exc = exc

    def get_app_settings(self):
        if self.exc:
            raise self.exc
        return self.settings


@pytest.fixture
def js(monkeypatch):
    calls = []
    monkeypatch.setattr(ui_font, "ui", SimpleNamespace(run_javascript=lambda code: calls.append(code)))
    return calls


@pytest.fixture
def client_ctx(monkeypatch):
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=object()))


def test_css_block_explicit_family():
    css = ui_font.get_app_font_css_block("Fira Code")
    assert "--font-family-app: 'Fira Code', -apple-system" in css
    assert "font-family: var(--font-family-app) !important;" in css
    assert "<script" not in css
    assert css.count("{") == css.count("}")


def test_css_block_default_stack_for_system_default():
    css = ui_font.get_app_font_css_block(SYSTEM_DEFAULT_LABEL)
    assert "'Inter'" in css


def test_css_block_reads_settings(monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State(SimpleNamespace(ui_font_family="Comic O'Neil")))
    css = ui_font.get_app_font_css_block()
    assert "'Comic O\\'Neil'" in css


def test_css_block_settings_without_attr(monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State(SimpleNamespace(ui_font_family=None)))
    assert "'Inter'" in ui_font.get_app_font_css_block()


def test_css_block_settings_error_falls_back(monkeypatch):
    monkeypatch.setattr(dataobjects, "state_manager", _State(exc=RuntimeError("x")))
    assert "'Inter'" in ui_font.get_app_font_css_block()


@pytest.mark.parametrize(
    "percent, zoom",
    [(100, "1.00"), (50, "0.90"), (200, "1.25"), (110, "1.10"), ("115", "1.15"), ("abc", "1.00")],
)
def test_apply_ui_scale_clamps(percent, zoom, js, client_ctx):
    ui_font.apply_ui_scale(percent)
    assert js == ["(function(){document.documentElement.style.zoom=" + repr(zoom) + ";})();"]


def test_apply_ui_scale_reads_settings(monkeypatch, js, client_ctx):
    monkeypatch.setattr(dataobjects, "state_manager", _State(SimpleNamespace(ui_scale_percent=120)))
    ui_font.apply_ui_scale()
    assert "'1.20'" in js[0]


def test_apply_ui_scale_zero_setting_means_100(monkeypatch, js, client_ctx):
    monkeypatch.setattr(dataobjects, "state_manager", _State(SimpleNamespace(ui_scale_percent=0)))
    ui_font.apply_ui_scale()
    assert "'1.00'" in js[0]


def test_apply_ui_scale_settings_error(monkeypatch, js, client_ctx):
    monkeypatch.setattr(dataobjects, "state_manager", _State(exc=RuntimeError("x")))
    ui_font.apply_ui_scale()
    assert "'1.00'" in js[0]


def test_apply_ui_scale_no_client(monkeypatch, js):
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=None))
    ui_font.apply_ui_scale(110)
    assert js == []


def test_apply_ui_scale_swallows_js_errors(monkeypatch, client_ctx):
    def boom(code):
        raise RuntimeError("deleted")

    monkeypatch.setattr(ui_font, "ui", SimpleNamespace(run_javascript=boom))
    ui_font.apply_ui_scale(100)  # no raise


def test_apply_app_font_injects_style_and_scale(monkeypatch, js, client_ctx):
    monkeypatch.setattr(dataobjects, "state_manager", _State(SimpleNamespace(ui_scale_percent=100)))
    ui_font.apply_app_font("Weird`${x}")
    assert len(js) == 2
    font_js = js[0]
    assert "mycelian-font-override" in font_js
    assert "Weird\\`\\${x}" in font_js  # backtick and ${ escaped for the JS template literal
    assert "zoom" in js[1]


def test_apply_app_font_no_client(monkeypatch, js):
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=None))
    ui_font.apply_app_font("Arial")
    assert js == []


def test_apply_app_font_swallows_errors(monkeypatch, client_ctx):
    def boom(code):
        raise RuntimeError("gone")

    monkeypatch.setattr(ui_font, "ui", SimpleNamespace(run_javascript=boom))
    ui_font.apply_app_font("Arial")  # no raise

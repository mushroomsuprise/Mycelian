# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.uiwindows.tabs`` lazy tab-class exports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types

import pytest

import modules.uiwindows.tabs as tabs
from modules.uiwindows.tabs.base import TabBase


def test_tabbase_exported_eagerly():
    assert tabs.TabBase is TabBase
    assert "TabBase" in tabs.__all__


def test_all_matches_export_map():
    assert set(tabs.__all__) == {"TabBase"} | set(tabs._TAB_EXPORTS)
    for name, mod in tabs._TAB_EXPORTS.items():
        assert mod.startswith(".") and mod.endswith("_tab")


@pytest.mark.parametrize("name", sorted(tabs._TAB_EXPORTS))
def test_getattr_imports_module_and_returns_class(monkeypatch, name):
    calls = []
    fake_mod = types.ModuleType("fake")
    sentinel = type(name, (), {})
    setattr(fake_mod, name, sentinel)

    def fake_import(modname, package=None):
        calls.append((modname, package))
        return fake_mod

    monkeypatch.setattr("importlib.import_module", fake_import)
    assert tabs.__getattr__(name) is sentinel
    assert calls == [(tabs._TAB_EXPORTS[name], "modules.uiwindows.tabs")]


def test_getattr_unknown_name_raises():
    with pytest.raises(AttributeError, match="has no attribute 'Bogus'"):
        tabs.__getattr__("Bogus")


def test_real_lazy_import_of_a_tab_class():
    cls = tabs.ThemeTab
    assert isinstance(cls, type)
    assert cls.__name__ == "ThemeTab"
    assert cls.__module__ == "modules.uiwindows.tabs.theme_tab"

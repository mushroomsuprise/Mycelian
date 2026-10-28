# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.uiwindows`` lazy package exports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types

import pytest

import modules.uiwindows as uiw


def test_all_lists_lazy_submodules():
    assert uiw.__all__ == ["alertsettings", "customsources", "activity_feed"]


@pytest.mark.parametrize("name", ["alertsettings", "customsources", "activity_feed"])
def test_getattr_imports_listed_submodule(monkeypatch, name):
    calls = []
    fake = types.ModuleType(f"modules.uiwindows.{name}")

    def fake_import(modname, package=None):
        calls.append((modname, package))
        return fake

    monkeypatch.setattr("importlib.import_module", fake_import)
    assert uiw.__getattr__(name) is fake
    assert calls == [(f".{name}", "modules.uiwindows")]


def test_getattr_unknown_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        uiw.__getattr__("nope")
    with pytest.raises(AttributeError):
        getattr(uiw, "definitely_not_here")


def test_attribute_access_returns_real_submodule():
    mod = uiw.customsources
    assert mod.__name__ == "modules.uiwindows.customsources"

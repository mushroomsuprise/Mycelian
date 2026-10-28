# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/__init__.py: lazy attribute exports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.help_system as hs  # noqa: E402


@pytest.mark.parametrize(
    "name, module, attr",
    [
        ("get_help_manager", "modules.help_system.help_manager", "get_help_manager"),
        ("HelpManager", "modules.help_system.help_manager", "HelpManager"),
        ("show_help_browser", "modules.help_system.help_browser", "show_help_browser"),
        ("HelpBrowser", "modules.help_system.help_browser", "HelpBrowser"),
        ("help_button", "modules.help_system.contextual_help", "help_button"),
        ("inline_help", "modules.help_system.contextual_help", "inline_help"),
    ],
)
def test_lazy_exports_resolve_to_submodule_objects(name, module, attr):
    import importlib

    assert name in hs.__all__
    assert getattr(hs, name) is getattr(importlib.import_module(module), attr)


def test_all_lists_exactly_the_lazy_names():
    assert sorted(hs.__all__) == sorted(
        ["get_help_manager", "HelpManager", "show_help_browser", "HelpBrowser", "help_button", "inline_help"]
    )


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        hs.__getattr__("nope")
    assert not hasattr(hs, "definitely_missing")

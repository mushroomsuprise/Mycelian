# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/source_settings_styles.py (Source Settings stylesheet)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows.source_settings_styles import SOURCE_SETTINGS_CSS  # noqa: E402


def test_css_is_nonempty_string():
    assert isinstance(SOURCE_SETTINGS_CSS, str)
    assert len(SOURCE_SETTINGS_CSS) > 1000


def test_css_braces_balance():
    assert SOURCE_SETTINGS_CSS.count("{") == SOURCE_SETTINGS_CSS.count("}")


def test_css_rules_are_scoped_under_ssx():
    # Every top-level selector line should target the .ssx scope (or be an at-rule / body prefix).
    selectors = re.findall(r"(?m)^([^\s@/}][^{]*)\{", SOURCE_SETTINGS_CSS)
    assert selectors
    for sel in selectors:
        assert ".ssx" in sel or sel.strip().startswith(("from", "to", "0%", "100%")), sel


def test_css_uses_theme_variables():
    assert "var(--color-text-primary)" in SOURCE_SETTINGS_CSS
    assert "var(--color-bg-surface)" in SOURCE_SETTINGS_CSS

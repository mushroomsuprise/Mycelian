# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alerts_styles.py: the Alerts tab stylesheets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows.alerts_styles import ALERTS_CSS, ALERTS_TAB_CSS  # noqa: E402


def test_stylesheets_are_balanced_strings():
    for css in (ALERTS_CSS, ALERTS_TAB_CSS):
        assert isinstance(css, str) and css.strip()
        assert css.count("{") == css.count("}")
        assert css.count("(") == css.count(")")


def test_alerts_css_covers_layout_classes():
    for cls in (".alr-row", ".alr-media", ".alr-item", ".alr-sec"):
        assert cls in ALERTS_CSS
    assert "body--dark" in ALERTS_CSS or "body:not(.body--dark)" in ALERTS_CSS


def test_tab_css_styles_the_count_badge_only_outside_the_alerts_scope():
    assert ".alr-tab-count" in ALERTS_TAB_CSS
    assert ".alr " not in ALERTS_TAB_CSS

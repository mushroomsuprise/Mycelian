#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Status bar badge looks picked in App Settings."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import notification_engine as ne  # noqa: E402
from modules.dataobjects import AppSettings  # noqa: E402
from modules.ui_styles import COMPONENT_CSS  # noqa: E402
from modules.uiwindows.tabs import theme_mock  # noqa: E402


def test_defaults_are_name_over_status_and_tiles():
    s = AppSettings()
    assert s.status_footer_full_style == "stacked"
    assert s.status_footer_minimal_style == "tiles"
    assert s.status_footer_minimal is False


@pytest.mark.parametrize("minimal, full, mini, expected", [
    (False, "stacked", "tiles", "service-status-footer--full-stacked"),
    (False, "classic", "rings", "service-status-footer--full-classic"),
    (True, "pills", "summary", "service-status-footer--minimal service-status-footer--min-summary"),
    (False, "gone", "tiles", "service-status-footer--full-stacked"),
    (True, "bar", "", "service-status-footer--minimal service-status-footer--min-tiles"),
])
def test_style_classes(minimal, full, mini, expected):
    assert ne.footer_style_classes(minimal, full, mini) == expected


def test_current_classes_follow_app_settings():
    settings = SimpleNamespace(status_footer_minimal=True, status_footer_full_style="bar",
                               status_footer_minimal_style="capsule")
    with mock.patch("modules.dataobjects.state_manager.get_app_settings", return_value=settings):
        assert ne.current_footer_style_classes().endswith("--min-capsule")
    with mock.patch("modules.dataobjects.state_manager.get_app_settings", side_effect=RuntimeError):
        assert ne.current_footer_style_classes() == "service-status-footer--full-stacked"


@pytest.mark.parametrize("tiers, expected", [
    (["success", "success", "muted"], ("success", "All connected")),
    (["success", "info"], ("info", "Connecting")),
    (["success", "warning", "info"], ("warning", "1 needs attention")),
    (["error", "warning", "success"], ("error", "2 need attention")),
    ([], ("success", "All connected")),
])
def test_summary_chip(tiers, expected):
    assert ne.footer_summary(tiers) == expected


def test_every_style_has_css():
    for key in ne.FOOTER_FULL_STYLES:
        if key != "classic":  # the base footer rules are the classic look
            assert f".service-status-footer--full-{key}" in COMPONENT_CSS, key
    for key in ne.FOOTER_MINIMAL_STYLES:
        assert f".service-status-footer--min-{key}" in COMPONENT_CSS, key


def test_app_settings_has_the_style_pickers():
    src = (ROOT / "modules" / "uiwindows" / "tabs" / "app_settings_tab.py").read_text(encoding="utf-8")
    assert '"status_footer_full_style"' in src
    assert '"status_footer_minimal_style"' in src
    assert "FOOTER_FULL_STYLES" in src and "FOOTER_MINIMAL_STYLES" in src


def test_theme_preview_footer_uses_the_new_markup():
    html = theme_mock._footer(False)
    assert "service-status-footer--full-stacked" in html
    assert "service-status-summary" in html
    assert "service-status-item--svc" in html
    html = theme_mock._footer(True, "service-status-footer--minimal service-status-footer--min-rings")
    assert re.search(r'class="[^"]*service-status-footer--min-rings', html)

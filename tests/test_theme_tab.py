# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Theme: color parsing, readability checks, mock markup and stylesheet."""

from __future__ import annotations

import re

import pytest

from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.theme_manager import ThemeColors
from modules.uiwindows.tabs.theme_tab import (
    _PALETTE_COLOR_FIELDS,
    ThemeTab,
    _parse_color,
    contrast_ratio,
    readability_checks,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("#fff", (255, 255, 255, 1.0)),
        ("#7300FF", (115, 0, 255, 1.0)),
        ("rgb(14, 116, 178)", (14, 116, 178, 1.0)),
        ("rgba(0, 0, 0, 0.12)", (0, 0, 0, 0.12)),
        ("rgba(10, 20, 30, 50%)", (10, 20, 30, 0.5)),
        ("", None),
        ("teal", None),
    ],
)
def test_parse_color(value, expected):
    assert _parse_color(value) == expected


def test_contrast_ratio_extremes():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert contrast_ratio((90, 90, 90), (90, 90, 90)) == pytest.approx(1.0)


def test_readability_checks_rate_pairs():
    good = ThemeColors(
        theme_type="dark", bg_base="rgb(18, 18, 24)", bg_surface="rgb(35, 35, 45)",
        text_primary="rgb(240, 240, 255)", text_secondary="rgba(240, 240, 255, 0.8)",
        text_muted="rgba(240, 240, 255, 0.6)", primary="rgb(115, 0, 255)",
    )
    ratings = {label: rating for label, _fg, _bg, _ratio, rating in readability_checks(good)}
    assert ratings == {"Text": "ok", "Secondary": "ok", "Hints": "ok", "Buttons": "ok"}

    bad = ThemeColors(
        theme_type="light", bg_base="#ffffff", bg_surface="#ffffff",
        text_primary="#eeeeee", text_secondary="#dddddd", text_muted="#f5f5f5", primary="#ffff00",
    )
    assert {r[4] for r in readability_checks(bad)} == {"bad"}


def test_describe_color_shows_alpha():
    tab = ThemeTab()
    assert tab._describe_color("rgb(115, 0, 255)") == "#7300FF"
    assert tab._describe_color("rgba(14, 116, 178, 0.1)") == "#0E74B2 · 10%"
    assert tab._describe_color("") == "not set"


def test_editor_covers_every_theme_color():
    colors = {f for f in ThemeColors.__dataclass_fields__} - {"name", "display_name", "theme_type"}
    assert set(_PALETTE_COLOR_FIELDS) == colors


def test_mock_markup_is_balanced_and_has_footer():
    from modules.uiwindows.tabs.theme_mock import build_mock_html

    markup = build_mock_html()
    assert markup.count("<div") == markup.count("</div>")
    assert "service-status-footer" in markup and "service-status-footer--minimal" not in markup
    assert "service-status-footer--minimal" in build_mock_html(minimal_footer=True)
    assert 'class="thm-m-subtab is-active"' in markup


def test_theme_menu_css_is_unscoped():
    from modules.uiwindows.tabs.theme_styles import THEME_MENU_CSS

    assert ".thm-optdots" in THEME_MENU_CSS and ".thm " not in THEME_MENU_CSS


def test_theme_stylesheet_maps_every_preview_color():
    from modules.uiwindows.tabs.theme_styles import THEME_CSS

    out = promote_color_importance(scope_selectors(THEME_CSS, ".thm"))
    assert out.count("{") == out.count("}")
    assert ".thm .thm .thm-" not in out
    mock_rule = re.search(r"\.thm \.thm-mock \{([^}]*)\}", out).group(1)
    for field in _PALETTE_COLOR_FIELDS:
        var = field.replace("_", "-")
        assert f"--color-{var}: var(--preview-color-{var})" in mock_rule

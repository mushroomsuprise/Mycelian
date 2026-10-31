# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/update_dialog_styles.py: the update window stylesheet."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import update_dialog_styles as mod  # noqa: E402
from tests.modules.test_uiwindows__tabs__about_styles import assert_well_formed_css, css_selectors  # noqa: E402

CSS = mod.UPDATE_CSS


def test_well_formed():
    assert isinstance(CSS, str) and CSS.strip()
    assert_well_formed_css(CSS)


def test_every_rule_is_namespaced():
    heads = [h.strip() for h in re.findall(r"([^{}]+)\{", CSS) if not h.strip().startswith("@")]
    for head in heads:
        if head in ("from", "to"):
            continue
        assert ".upd" in head, head
    assert css_selectors(CSS)


def test_colors_come_from_theme_tokens():
    # Only white on filled buttons/bars and the shadow are literal; every other color is a theme token.
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", CSS))
    assert literals <= {"#fff", "rgba(0, 0, 0, .45)"}, literals
    for token in ("--color-primary", "--color-bg-surface", "--color-text-primary", "--color-error", "--color-success"):
        assert f"var({token}" in CSS


def test_no_warning_color():
    # Downgrades use the primary accent, not the warning yellow.
    assert "--color-warning" not in CSS


def test_scoped_stylesheet_builds():
    from modules.help_system.help_render import promote_color_importance, scope_selectors

    scoped = promote_color_importance(scope_selectors(CSS, ".upd"))
    assert ".upd .upd-x" in scoped and "color: var(--color-text-muted) !important" in scoped

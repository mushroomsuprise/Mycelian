# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/theme_styles.py: Theme tab stylesheets and the preview variable map."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows.tabs import theme_styles as ts  # noqa: E402


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _assert_well_formed(css: str) -> None:
    assert css.count("/*") == css.count("*/")
    body = _strip_comments(css)
    depth = 0
    for ch in body:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        assert depth >= 0
    assert depth == 0
    for block in re.findall(r"\{([^{}]*)\}", body):
        for decl in (d.strip() for d in block.split(";")):
            if decl:
                assert re.match(r"^-{0,2}[a-zA-Z][\w-]*\s*:", decl), f"malformed declaration {decl!r}"
    assert css.count("(") == css.count(")")


@pytest.mark.parametrize("name", ["THEME_CSS", "THEME_MENU_CSS"])
def test_stylesheets_well_formed(name):
    css = getattr(ts, name)
    assert css.strip()
    _assert_well_formed(css)


def test_mock_var_placeholder_replaced():
    assert "__MOCK_VARS__" not in ts.THEME_CSS
    assert ts._MOCK_VAR_MAP in ts.THEME_CSS


def test_preview_vars_unique_and_mapped():
    assert len(ts._PREVIEW_VARS) == len(set(ts._PREVIEW_VARS))
    for var in ts._PREVIEW_VARS:
        assert f"--color-{var}: var(--preview-color-{var});" in ts._MOCK_VAR_MAP
    for q, color in (("primary", "primary"), ("positive", "success"), ("negative", "error"),
                     ("warning", "warning"), ("info", "info")):
        assert f"--q-{q}: var(--preview-color-{color});" in ts._MOCK_VAR_MAP


def test_mock_block_points_every_variable_at_preview():
    m = re.search(r"\.thm-mock \{([^}]*)\}", ts.THEME_CSS)
    assert m
    decls = m.group(1)
    for var in ts._PREVIEW_VARS:
        assert f"--color-{var}: var(--preview-color-{var})" in decls


def test_preview_vars_match_theme_manager_variables():
    """Every previewed variable exists in the CSS the theme manager generates."""
    from modules.theme_manager import ThemeColors, generate_css_variables

    generated = generate_css_variables(ThemeColors())
    for var in ts._PREVIEW_VARS:
        assert f"--color-{var}:" in generated, var


def _split_top_level(head: str):
    parts, depth, cur = [], 0, ""
    for ch in head:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def test_theme_css_scoped_to_thm_and_menu_unscoped():
    body = _strip_comments(ts.THEME_CSS)
    heads = [h.strip() for h in re.findall(r"([^{}]+)\{", body) if not h.strip().startswith("@")]
    for head in heads:
        for sel in _split_top_level(head):
            assert ".thm" in sel, sel
    assert ".thm-optdots" in ts.THEME_MENU_CSS
    assert ".thm " not in ts.THEME_MENU_CSS

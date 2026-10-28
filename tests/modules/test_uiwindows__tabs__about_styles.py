# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/about_styles.py: the About stylesheet is well-formed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows.tabs import about_styles as mod  # noqa: E402


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def assert_well_formed_css(css: str) -> None:
    assert css.count("/*") == css.count("*/"), "unclosed comment"
    body = _strip_comments(css)
    depth = 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            assert depth >= 0, "closing brace without opener"
    assert depth == 0, "unbalanced braces"
    for block in re.findall(r"\{([^{}]*)\}", body):
        for decl in (d.strip() for d in block.split(";")):
            if decl:
                assert re.match(r"^-{0,2}[a-zA-Z][\w-]*\s*:", decl), f"malformed declaration {decl!r}"
    assert "<style" not in css and "<script" not in css
    assert css.count("(") == css.count(")"), "unbalanced parentheses"


def css_selectors(css: str):
    body = _strip_comments(css)
    out = []
    for head in re.findall(r"([^{}]+)\{", body):
        head = head.strip()
        if head and not head.startswith("@"):
            out.extend(s.strip() for s in head.split(","))
    return out


CSS = mod.ABOUT_CSS


def test_is_non_empty_string():
    assert isinstance(CSS, str) and CSS.strip()


def test_well_formed():
    assert_well_formed_css(CSS)


def test_selectors_use_module_prefix():
    sels = css_selectors(CSS)
    assert sels
    assert any(".abt" in s for s in sels)


def test_colors_come_from_theme_variables():
    body = _strip_comments(CSS)
    used = set(re.findall(r"var\((--[\w-]+)", body))
    assert used, "expected theme variables"
    local = set(re.findall(r"(--[\w-]+)\s*:", body))  # custom properties defined in this sheet
    for var in used - local:
        assert var.startswith(("--color-", "--q-", "--abt", "--font")), var
    hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", body)}
    assert hexes <= {"#fff", "#ffffff", "#000", "#000000"}, hexes


def test_assert_helper_rejects_bad_css():
    with pytest.raises(AssertionError):
        assert_well_formed_css(".a { color: red; ")
    with pytest.raises(AssertionError):
        assert_well_formed_css(".a { color red; }")

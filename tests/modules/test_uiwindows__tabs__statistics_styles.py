# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The Statistics dashboard stylesheet is well-formed CSS that covers every class the tab uses."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows.tabs.statistics_styles import STATISTICS_CSS  # noqa: E402

TAB_SOURCE = (ROOT / "modules" / "uiwindows" / "tabs" / "statistics_tab.py").read_text(encoding="utf-8")


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_comments_are_closed_and_braces_balance():
    assert STATISTICS_CSS.count("/*") == STATISTICS_CSS.count("*/")
    depth = 0
    for ch in _without_comments(STATISTICS_CSS):
        depth += ch == "{"
        depth -= ch == "}"
        assert depth >= 0
    assert depth == 0


def test_parses_without_errors():
    tinycss2 = pytest.importorskip("tinycss2")
    rules = tinycss2.parse_stylesheet(STATISTICS_CSS, skip_comments=True, skip_whitespace=True)
    errors = [r for r in rules if r.type == "error"]
    assert errors == []
    qualified = [r for r in rules if r.type == "qualified-rule"]
    assert len(qualified) > 50
    for rule in qualified:
        selector = tinycss2.serialize(rule.prelude).strip()
        assert selector, "empty selector"
        decls = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
        assert not [d for d in decls if d.type == "error"], selector
    for rule in (r for r in rules if r.type == "at-rule"):
        assert rule.at_keyword in ("media", "supports", "keyframes")


def test_every_rule_is_scoped_to_the_dashboard():
    tinycss2 = pytest.importorskip("tinycss2")
    rules = tinycss2.parse_stylesheet(STATISTICS_CSS, skip_comments=True, skip_whitespace=True)

    def selectors(rule_list):
        for r in rule_list:
            if r.type == "qualified-rule":
                yield tinycss2.serialize(r.prelude).strip()
            elif r.type == "at-rule" and r.content is not None and r.at_keyword != "keyframes":
                yield from selectors(tinycss2.parse_rule_list(r.content, skip_comments=True, skip_whitespace=True))

    def top_level_parts(selector):
        depth, part = 0, ""
        for ch in selector:
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                yield part
                part = ""
            else:
                part += ch
        yield part

    for sel in selectors(rules):
        for part in top_level_parts(sel):
            assert ".mst" in part, part


def test_colors_come_from_theme_variables():
    body = _without_comments(STATISTICS_CSS)
    # No hard-coded hex colors other than white text on the primary fill.
    assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", body)) <= {"#fff"}
    assert "var(--color-primary)" in body and "var(--color-bg-surface)" in body
    assert "var(--color-text-muted)" in body


def test_every_class_the_tab_uses_is_styled():
    used = set(re.findall(r"\bmst-[a-z0-9-]+", TAB_SOURCE))
    defined = set(re.findall(r"\.(mst-[a-z0-9-]+)", STATISTICS_CSS))
    assert used and used <= defined


def test_the_tab_can_scope_the_stylesheet():
    from modules.help_system.help_render import promote_color_importance, scope_selectors

    scoped = promote_color_importance(scope_selectors(STATISTICS_CSS, ".mst"))
    assert isinstance(scoped, str) and ".mst-toolbar" in scoped
    assert scoped.count("{") == scoped.count("}")

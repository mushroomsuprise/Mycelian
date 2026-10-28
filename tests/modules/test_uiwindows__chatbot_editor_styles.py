# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_editor_styles.py: the editor CSS is well-formed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import chatbot_editor_styles as styles  # noqa: E402

CSS = styles.CHATBOT_EDITOR_CSS


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_css_is_a_non_empty_string():
    assert isinstance(CSS, str)
    assert CSS.strip()


def test_braces_are_balanced_and_never_negative():
    depth = 0
    for ch in _strip_comments(CSS):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            assert depth >= 0
    assert depth == 0


def test_parentheses_are_balanced():
    css = _strip_comments(CSS)
    assert css.count("(") == css.count(")")


def test_comments_are_closed():
    assert CSS.count("/*") == CSS.count("*/")


def _rules(css: str):
    """Yield (selector, body) for every leaf rule, descending into @media blocks."""
    css = _strip_comments(css)
    i = 0
    while i < len(css):
        open_at = css.find("{", i)
        if open_at == -1:
            assert not css[i:].strip(), f"trailing text outside a rule: {css[i:]!r}"
            return
        selector = css[i:open_at].strip()
        depth, j = 1, open_at + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[open_at + 1:j - 1]
        if selector.startswith("@"):
            yield from _rules(body)
        else:
            yield selector, body
        i = j


def test_every_rule_has_a_selector_and_valid_declarations():
    rules = list(_rules(CSS))
    assert len(rules) > 20
    for selector, body in rules:
        assert selector, "rule without selector"
        assert "{" not in body and "}" not in body
        for decl in filter(None, (d.strip() for d in body.split(";"))):
            prop, sep, value = decl.partition(":")
            assert sep, f"declaration without colon in {selector!r}: {decl!r}"
            assert re.fullmatch(r"-?[a-z][a-z-]*", prop.strip()), f"bad property {prop!r} in {selector!r}"
            assert value.strip(), f"empty value for {prop!r} in {selector!r}"


def test_expected_editor_selectors_are_present():
    selectors = " ".join(sel for sel, _ in _rules(CSS))
    for name in (".ce-box", ".ce-row", ".ce-seg", ".ce-cmdgrid", ".ce-bang", ".ce-chips", ".cv-result", ".cv-foot"):
        assert name in selectors


def test_media_query_targets_narrow_screens():
    assert re.search(r"@media\s*\(max-width:\s*\d+px\)", CSS)


def test_colors_use_theme_variables():
    variables = set(re.findall(r"var\((--[a-z0-9-]+)\)", CSS))
    assert "--color-primary" in variables
    assert all(v.startswith("--color-") for v in variables)

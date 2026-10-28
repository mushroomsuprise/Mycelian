# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_styles.py: the base/component CSS strings and the sub-tab seam script."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import ui_styles  # noqa: E402


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


@pytest.mark.parametrize("name", ["BASE_CSS", "COMPONENT_CSS"])
def test_css_is_balanced_and_comment_closed(name):
    css = getattr(ui_styles, name)
    assert css.count("/*") == css.count("*/")
    body = _strip_comments(css)
    depth = 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            assert depth >= 0, f"{name}: closing brace without opener"
    assert depth == 0
    assert "<style" not in css and "<script" not in css


@pytest.mark.parametrize("name", ["BASE_CSS", "COMPONENT_CSS"])
def test_css_declarations_are_well_formed(name):
    body = _strip_comments(getattr(ui_styles, name))
    # Every declaration block's contents (innermost) should be "prop: value;" pairs.
    for block in re.findall(r"\{([^{}]*)\}", body):
        for decl in [d.strip() for d in block.split(";") if d.strip()]:
            assert ":" in decl, f"{name}: malformed declaration {decl!r}"


def test_css_uses_theme_variables_defined_somewhere():
    css = ui_styles.get_full_theme_css()
    used = set(re.findall(r"var\((--[\w-]+)", css))
    assert used, "theme CSS should use CSS variables"
    assert "--font-family-app" in used or any(v.startswith("--color") for v in used)


def test_full_theme_css_is_concatenation():
    assert ui_styles.get_full_theme_css() == ui_styles.BASE_CSS + ui_styles.COMPONENT_CSS


def test_component_css_contains_status_footer_badge_styles():
    assert ".service-status-footer--min-rings" in ui_styles.COMPONENT_CSS


def test_seam_script_is_single_guarded_script():
    s = ui_styles.SUB_TAB_SEAM_SCRIPT.strip()
    assert s.startswith('<script id="mycelian-subtab-seam-js">')
    assert s.endswith("</script>")
    assert s.count("<script") == 1 and s.count("</script>") == 1
    inner = s[s.index(">") + 1: s.rindex("</script>")]
    assert inner.count("{") == inner.count("}")
    assert inner.count("(") == inner.count(")")
    assert "window.__mycelianSubTabSeamInit" in inner
    for fn in ("mycelianUpdateTabSeams", "mycelianUpdateSubTabSeams", "mycelianInitTabSeams", "mycelianInitSubTabSeams"):
        assert f"window.{fn} =" in inner

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""uiwindows.connectors_styles: the CSS constants are non-empty, well-formed stylesheets."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.uiwindows import connectors_styles as styles

NAMES = [n for n in dir(styles) if n.endswith("_CSS")]


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_module_exports_css_constants():
    assert NAMES


@pytest.mark.parametrize("name", NAMES)
def test_css_is_well_formed(name):
    css = getattr(styles, name)
    assert isinstance(css, str) and css.strip()
    body = _strip_comments(css)
    assert "/*" not in body and "*/" not in body
    depth = 0
    for ch in body:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        assert depth >= 0, "closing brace without an opening one"
    assert depth == 0, "unbalanced braces"
    assert body.count("(") == body.count(")")
    assert body.count('"') % 2 == 0 and body.count("'") % 2 == 0
    assert "<style" not in body.lower() and "</" not in body


@pytest.mark.parametrize("name", NAMES)
def test_css_rules_have_selectors_and_declarations(name):
    body = _strip_comments(getattr(styles, name))
    for selector, block in re.findall(r"([^{}]*)\{([^{}]*)\}", body):
        assert selector.strip(), "rule without a selector"
        for decl in filter(None, (d.strip() for d in block.split(";"))):
            assert ":" in decl, f"declaration without a colon: {decl!r}"
            prop = decl.split(":", 1)[0].strip()
            assert re.fullmatch(r"-{0,2}[a-zA-Z][\w-]*", prop), f"bad property {prop!r}"

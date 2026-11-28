# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_tooltip.py: the shared tooltip attribute helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from nicegui import ui  # noqa: E402

from modules import ui_tooltip  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def test_tip_sets_attribute_without_a_tooltip_component():
    made = {}

    def build():
        made["a"] = ui_tooltip.tip(ui.button("x"), 'Say "hi" & <wave>')
        made["b"] = ui_tooltip.tip(ui.button("y"), "")

    run_ui(build)
    a, b = made["a"], made["b"]
    assert a.props.get(ui_tooltip.TOOLTIP_ATTR) == 'Say "hi" & <wave>'
    assert ui_tooltip.TOOLTIP_ATTR not in b.props
    assert not any(isinstance(el, ui.tooltip) for el in a.client.elements.values())


def test_head_html_is_self_contained():
    html = ui_tooltip.TOOLTIP_HEAD_HTML
    assert 'id="mycelian-tip-css"' in html and 'id="mycelian-tip-js"' in html
    # Runs once per page even if injected twice, and respects reduced motion.
    assert "window.__mycelianTip" in html and "prefers-reduced-motion" in html

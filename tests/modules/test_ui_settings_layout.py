# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_settings_layout.py: settings layout context managers and action rows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from nicegui import ui  # noqa: E402

from modules import ui_settings_layout as sl  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def _kids(el):
    return list(el.default_slot.children)


def _labels(el):
    return [d.text for d in el.descendants() if isinstance(d, ui.label)]


def test_settings_surface_wraps_in_column():
    made = {}

    def build():
        parent = ui.column()
        with sl.settings_surface(parent):
            made["inner"] = ui.label("inside")
        made["parent"] = parent

    run_ui(build)
    (col,) = _kids(made["parent"])
    assert "settings-tab-content" in col.classes
    assert made["inner"] in _kids(col)


def test_inner_panel_header_section_band_grid_toolbar():
    made = {}

    def build():
        with ui.column() as root:
            with sl.settings_inner_panel():
                made["p"] = ui.label("panel child")
            with sl.settings_header("Header Title"):
                made["h"] = ui.button("Act")
            with sl.settings_section("Sec", subtitle="Sub text"):
                ui.label("s")
            with sl.settings_section("NoSub"):
                pass
            with sl.settings_status_band():
                made["metric"] = sl.status_metric("Status", extra_classes="text-green")
                made["metric2"] = sl.status_metric("Plain")
            with sl.settings_form_grid(columns=3):
                made["grid_child"] = ui.label("g")
            with sl.settings_toolbar():
                ui.label("t")
            made["div"] = sl.settings_divider()
            made["chips"] = sl.theme_chip_row()
        made["root"] = root

    run_ui(build)
    kids = _kids(made["root"])
    panel = kids[0]
    assert {"content-card", "settings-inner-panel"} <= set(panel.classes)
    assert made["p"] in panel.descendants()

    header = kids[1]
    assert _labels(header)[0] == "Header Title"
    assert made["h"].parent_slot.parent.parent_slot.parent is header

    section = kids[2]
    assert "settings-section" in section.classes
    assert _labels(section)[:2] == ["Sec", "Sub text"]
    assert _labels(kids[3]) == ["NoSub"]

    band = kids[4]
    assert "settings-status-band" in band.classes
    assert made["metric"].text == "…"
    assert list(made["metric"].classes) == ["font-semibold", "text-sm", "text-green"]
    assert list(made["metric2"].classes) == ["font-semibold", "text-sm"]

    grid = kids[5]
    assert isinstance(grid, ui.grid)
    assert "settings-form-grid" in grid.classes
    assert "repeat(3" in str(grid.props.get("style", "")) or "repeat(3" in str(grid._style)

    assert "settings-toolbar" in kids[6].classes
    assert "settings-divider" in made["div"].classes and isinstance(made["div"], ui.separator)
    assert "min-h-[28px]" in made["chips"].classes
    assert sl.THEME_CHIP_CLASSES.startswith("theme-chip")


def test_settings_footer_order_and_clicks():
    calls = []

    def build():
        sl.settings_footer(discard=lambda: calls.append("discard"), save=lambda: calls.append("save"))

    async def check(user):
        buttons = [b.text for b in sorted(user.find(ui.button).elements, key=lambda b: b.id)]
        assert buttons == ["Discard", "Save"]
        user.find("Discard").click()
        user.find("Save").click()

    run_ui(build, check)
    assert calls == ["discard", "save"]


def test_settings_action_row_extra_buttons():
    calls = []

    def build():
        sl.settings_action_row(
            discard=lambda: calls.append("discard"),
            save=lambda: calls.append("save"),
            before_discard=[("Reset", lambda: calls.append("reset"), "restart_alt", False),
                            ("Pre", lambda: calls.append("pre"), None, True)],
            after_save=[("Apply", lambda: calls.append("apply"), "check", True),
                        ("Later", lambda: calls.append("later"), None, False)],
        )

    async def check(user):
        els = user.find(ui.button).elements
        by_text = {b.text: b for b in els}
        assert [b.text for b in sorted(els, key=lambda b: b.id)] == ["Reset", "Pre", "Discard", "Save", "Apply", "Later"]
        assert by_text["Reset"].props.get("outline") is True
        assert by_text["Pre"].props.get("unelevated") is True
        assert by_text["Apply"].props.get("icon") == "check"
        assert by_text["Later"].props.get("outline") is True
        for name in ("Reset", "Pre", "Apply", "Later"):
            user.find(name).click()

    run_ui(build, check)
    assert calls == ["reset", "pre", "apply", "later"]

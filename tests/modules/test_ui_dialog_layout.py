# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_dialog_layout.py: the themed form dialog shell and its close handling."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from nicegui import ui  # noqa: E402

from modules import ui_dialog_layout as dl  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def test_dialog_slots_and_close_callback():
    made = {}
    closed = []

    def build():
        with dl.app_form_dialog("Add Quote", on_close=lambda: closed.append(1)) as slots:
            with slots.body:
                ui.label("Body text")
            with slots.footer:
                ui.button("Cancel", on_click=slots.close)
        made["slots"] = slots
        slots.dialog.open()

    async def check(user):
        await user.should_see("Add Quote")
        await user.should_see("Body text")
        user.find("Cancel").click()

    run_ui(build, check)
    slots = made["slots"]
    assert isinstance(slots, dl.FormDialogSlots)
    assert isinstance(slots.dialog, ui.dialog)
    assert slots.dialog.props.get("persistent") is True
    assert slots.dialog.value is False
    assert closed == [1]
    assert "p-4" in slots.body.classes
    assert "justify-end" in slots.footer.classes
    cards = [d for d in slots.dialog.descendants() if isinstance(d, ui.card)]
    assert "min-w-[32rem]" in cards[0].classes


def test_dialog_header_close_button_without_callback_and_custom_width():
    made = {}

    def build():
        with dl.app_form_dialog("Edit", width="w-[20rem]") as slots:
            pass
        made["slots"] = slots
        slots.dialog.open()

    async def check(user):
        await user.should_see("Edit")
        close_btns = [b for b in user.find(ui.button).elements if b.props.get("icon") == "close"]
        assert len(close_btns) == 1
        close_btns[0].props["label"] = "closebtn"
        user.find("closebtn").click()

    run_ui(build, check)
    slots = made["slots"]
    assert slots.dialog.value is False
    cards = [d for d in slots.dialog.descendants() if isinstance(d, ui.card)]
    assert "w-[20rem]" in cards[0].classes

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_tab_transitions.py: tab label resolution, slide direction and tab index."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import ui_tab_transitions as tt  # noqa: E402


class _Panels:
    def __init__(self):
        self.calls = []

    def props(self, value):
        self.calls.append(value)
        return self


class _BadProps:
    def __getitem__(self, key):
        raise KeyError(key)


def test_order_constants_are_unique_and_complete():
    assert len(set(tt.MAIN_TAB_ORDER)) == len(tt.MAIN_TAB_ORDER) == 8
    assert tt.MAIN_TAB_ORDER[0] == "Activity Feed" and tt.MAIN_TAB_ORDER[-1] == "Settings"
    assert len(set(tt.SETTINGS_TAB_ORDER)) == len(tt.SETTINGS_TAB_ORDER)
    assert tt.SETTINGS_TAB_ORDER[0] == "Twitch" and tt.SETTINGS_TAB_ORDER[-1] == "About"
    assert "App Settings" in tt.SETTINGS_TAB_ORDER


@pytest.mark.parametrize(
    "tab, expected",
    [
        (None, ""),
        ("Alerts", "Alerts"),
        (SimpleNamespace(_props={"label": "From label"}), "From label"),
        (SimpleNamespace(_props={"label": "", "name": "From name"}), "From name"),
        (SimpleNamespace(_props={}, text="From text"), "From text"),
        (SimpleNamespace(_props=_BadProps(), label="Attr label"), "Attr label"),
        (SimpleNamespace(name="Attr name"), "Attr name"),
        (SimpleNamespace(text=lambda: "callable", label=None, name=""), ""),
        (SimpleNamespace(_props={"label": 0}), ""),
        (object(), ""),
        (SimpleNamespace(_props={"name": 42}), "42"),
    ],
)
def test_tab_label(tab, expected):
    assert tt._tab_label(tab) == expected


def test_slide_direction_moving_right():
    p = _Panels()
    tt.apply_tab_slide_direction(p, "Activity Feed", "Settings", tt.MAIN_TAB_ORDER)
    assert p.calls == ["transition-prev=slide-right transition-next=slide-left"]


def test_slide_direction_moving_left():
    p = _Panels()
    tt.apply_tab_slide_direction(p, "Settings", SimpleNamespace(_props={"label": "Alerts"}), tt.MAIN_TAB_ORDER)
    assert p.calls == ["transition-prev=slide-left transition-next=slide-right"]


@pytest.mark.parametrize(
    "prev, new",
    [(None, "Alerts"), ("Alerts", None), ("Alerts", "Alerts"), ("Nope", "Alerts"), ("Alerts", "Nope"), ("", "")],
)
def test_slide_direction_no_change_cases(prev, new):
    p = _Panels()
    tt.apply_tab_slide_direction(p, prev, new, tt.MAIN_TAB_ORDER)
    assert p.calls == []


def test_slide_direction_none_panels_is_noop():
    assert tt.apply_tab_slide_direction(None, "Alerts", "Settings", tt.MAIN_TAB_ORDER) is None


def test_tab_index():
    assert tt.tab_index("Twitch", tt.SETTINGS_TAB_ORDER) == 0
    assert tt.tab_index(SimpleNamespace(_props={"name": "About"}), tt.SETTINGS_TAB_ORDER) == len(tt.SETTINGS_TAB_ORDER) - 1
    assert tt.tab_index("Missing", tt.SETTINGS_TAB_ORDER) is None
    assert tt.tab_index(None, tt.SETTINGS_TAB_ORDER) is None

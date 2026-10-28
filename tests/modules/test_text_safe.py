# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.text_safe.safe_console_str."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.text_safe import safe_console_str


def test_none_becomes_empty_string():
    assert safe_console_str(None) == ""


@pytest.mark.parametrize(
    "value, expected",
    [
        ("plain", "plain"),
        ("", ""),
        ("caf\u00e9", "caf?"),
        ("\U0001f600 hi", "? hi"),
        ("\u65e5\u672c", "??"),
        (42, "42"),
        (3.5, "3.5"),
        (False, "False"),
        (["a", "\u00e9"], "['a', '?']"),
    ],
)
def test_converts_to_ascii_with_replacement(value, expected):
    out = safe_console_str(value)
    assert out == expected
    out.encode("ascii")  # never raises


def test_object_with_unicode_str():
    class Obj:
        def __str__(self):
            return "\u2603 snow"

    assert safe_console_str(Obj()) == "? snow"

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the ``TabBase`` settings-tab protocol."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Protocol

from modules.uiwindows.tabs.base import TabBase


def test_tabbase_is_a_protocol_with_expected_members():
    assert Protocol in TabBase.__mro__ or getattr(TabBase, "_is_protocol", False)
    for member in ("build", "on_enter", "on_exit", "save", "discard"):
        assert callable(getattr(TabBase, member))
    assert TabBase.__annotations__ == {"name": "str", "dirty": "bool"}


def test_protocol_method_stubs_return_none():
    class Impl(TabBase):
        name = "x"
        dirty = False

    impl = Impl()
    assert impl.build(object()) is None
    assert impl.on_enter() is None
    assert impl.on_exit() is None
    assert impl.save() is None
    assert impl.discard() is None
    assert impl.name == "x" and impl.dirty is False

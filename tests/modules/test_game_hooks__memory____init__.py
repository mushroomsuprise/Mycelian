# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/memory/__init__.py (backend selection by OS)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import memory  # noqa: E402


def test_exports():
    for name in memory.__all__:
        assert hasattr(memory, name)
    assert memory.find_process_pid is memory.base.find_process_pid


def test_backend_none_off_windows(monkeypatch):
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    assert memory.get_memory_backend() is None
    monkeypatch.setattr(memory.sys, "platform", "linux")
    assert memory.get_memory_backend() is None


def test_backend_windows(monkeypatch):
    made = []

    class FakeWin:
        def __init__(self):
            made.append(self)

    monkeypatch.setattr(memory.sys, "platform", "win32")
    monkeypatch.setattr(memory, "WindowsProcessMemory", FakeWin)
    backend = memory.get_memory_backend()
    assert isinstance(backend, FakeWin) and made == [backend]

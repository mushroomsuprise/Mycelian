# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/__init__.py (public exports and compatibility aliases)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.game_hooks as gh  # noqa: E402


def test_all_names_exist():
    for name in gh.__all__:
        assert hasattr(gh, name), name


def test_ff7reader_alias():
    assert gh.FF7Reader is gh.FF7Hook


def test_create_hook_instance_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(gh, "create_hook", lambda gid: seen.append(gid) or "HOOK")
    assert gh.create_hook_instance("ff7") == "HOOK"
    assert seen == ["ff7"]


def test_create_hook_instance_unknown_is_none():
    assert gh.create_hook_instance("no-such-game") is None

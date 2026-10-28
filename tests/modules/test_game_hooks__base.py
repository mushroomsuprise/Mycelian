# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/base.py (OS key, UI metadata, GameHook protocol)."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import base  # noqa: E402


@pytest.mark.parametrize(
    "plat,key",
    [("win32", "windows"), ("darwin", "darwin"), ("linux", "linux"), ("linux2", "linux"), ("freebsd13", "other"), ("cygwin", "other")],
)
def test_runtime_os_key(monkeypatch, plat, key):
    monkeypatch.setattr(base.sys, "platform", plat)
    assert base.runtime_os_key() == key


def test_metadata_frozen_and_equal():
    a = base.HookUiMetadata("x", "X", frozenset({"windows"}))
    b = base.HookUiMetadata("x", "X", frozenset({"windows"}))
    assert a == b and hash(a) == hash(b)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.title = "Y"  # type: ignore[misc]


def test_protocol_stubs_return_none():
    gh = base.GameHook
    d = object()
    assert gh.is_platform_supported(d) is None
    assert gh.is_process_running(d) is None
    assert gh.tick(d) is None
    assert gh.execute_operation(d, "op", {}) is None
    assert gh.close(d) is None
    assert gh.handle_command(d, "a", {}) is None
    assert gh.on_config_reloaded(d) is None
    assert gh.idle_snapshot(d, disabled=True) is None
    assert gh.set_write_enqueue(d, None) is None
    assert gh.on_attached(d) is None
    assert gh.on_detached(d) is None
    assert gh.drain_timed_jobs(d, None) is None

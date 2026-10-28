# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/registry.py (hook registry and enabled-flag cache)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import registry  # noqa: E402
from modules.game_hooks.base import HookUiMetadata  # noqa: E402


class _DB:
    def __init__(self, store=None, fail=()):
        self.store = dict(store or {})
        self.fail = set(fail)
        self.reads = []

    def get_data(self, path):
        self.reads.append(path)
        if path in self.fail:
            raise RuntimeError("db down")
        return self.store.get(path)


@pytest.fixture
def reg(monkeypatch):
    monkeypatch.setattr(registry, "_HOOK_TYPES", {})
    monkeypatch.setattr(registry, "_enabled_cache", {})
    monkeypatch.setattr(registry, "_enabled_cache_loaded", False)
    db = _DB()
    monkeypatch.setattr(registry, "database_manager", db)
    return db


def test_enabled_db_path():
    assert registry.enabled_db_path("ff7") == "GameHooks/ff7_enabled"


@pytest.mark.parametrize(
    "raw,expected",
    [({"enabled": True}, True), ({"enabled": 0}, False), ({"enabled": "x"}, True), ({}, False),
     (True, True), (False, False), (None, False), ("yes", False), (1, False)],
)
def test_parse_enabled_value(raw, expected):
    assert registry._parse_enabled_value(raw) is expected


def test_ensure_registered_loads_builtins(reg):
    ids = registry.registered_hook_ids()
    assert set(ids) == {"ff7", "factorio"}
    # idempotent
    registry._ensure_registered()
    assert len(registry._HOOK_TYPES) == 2


def test_register_hook_returns_class(reg):
    class H:
        hook_id = "zz"

    assert registry._register_hook(H) is H
    assert registry._HOOK_TYPES["zz"] is H
    # registry non-empty: no builtins added
    assert registry.registered_hook_ids() == ["zz"]


def test_list_hooks_for_ui(reg):
    metas = registry.list_hooks_for_ui()
    assert all(isinstance(m, HookUiMetadata) for m in metas)
    assert {m.hook_id for m in metas} == {"ff7", "factorio"}


def test_get_hook_class_normalizes(reg):
    assert registry.get_hook_class("  FF7 ").hook_id == "ff7"
    assert registry.get_hook_class(None) is None
    assert registry.get_hook_class("nope") is None


def test_create_hook(reg, monkeypatch):
    class H:
        hook_id = "zz"
        ui = None

    registry._register_hook(H)
    assert isinstance(registry.create_hook(" ZZ "), H)
    assert registry.create_hook("missing") is None
    assert registry.create_hook("") is None


def test_refresh_cache_reads_each_hook(reg):
    reg.store = {"GameHooks/ff7_enabled": {"enabled": True}, "GameHooks/factorio_enabled": False}
    registry.refresh_hook_enabled_cache()
    assert registry._enabled_cache == {"ff7": True, "factorio": False}
    assert registry._enabled_cache_loaded is True


def test_refresh_cache_db_error_means_disabled(reg):
    reg.store = {"GameHooks/factorio_enabled": True}
    reg.fail = {"GameHooks/ff7_enabled"}
    registry.refresh_hook_enabled_cache()
    assert registry._enabled_cache == {"ff7": False, "factorio": True}


def test_is_hook_enabled_loads_once(reg):
    reg.store = {"GameHooks/ff7_enabled": True}
    assert registry.is_hook_enabled("ff7") is True
    assert registry.is_hook_enabled("factorio") is False
    assert registry.is_hook_enabled("unknown") is False
    assert len(reg.reads) == 2  # one refresh only


def test_set_hook_enabled_cached_updates_value(reg):
    registry.refresh_hook_enabled_cache()
    registry.set_hook_enabled_cached("ff7", 1)
    assert registry._enabled_cache["ff7"] is True
    assert registry.is_hook_enabled("ff7") is True


@pytest.mark.xfail(
    strict=True,
    reason="BUG: set_hook_enabled_cached assigns a local _enabled_cache_loaded (no `global`), so the next "
    "is_hook_enabled re-reads the DB and overwrites the just-saved value",
)
def test_set_hook_enabled_cached_marks_cache_loaded(reg):
    reg.store = {"GameHooks/ff7_enabled": False}  # DB not yet updated (e.g. write still pending)
    registry.set_hook_enabled_cached("ff7", True)
    assert registry.is_hook_enabled("ff7") is True
    assert reg.reads == []

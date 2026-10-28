# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/__init__.py (package surface + sidecar migration)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.spore_studio as pkg
from modules.spore_studio import template_parser_back


def test_public_surface():
    for name in pkg.__all__:
        assert hasattr(pkg, name), name
    assert "ensure_sidecar_migration" in pkg.__all__


def test_migration_runs_once_and_logs(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(pkg, "_sidecars_migrated", False)
    monkeypatch.setattr(template_parser_back, "migrate_all_sidecars", lambda: calls.append(1) or 3)
    with caplog.at_level(logging.INFO, logger="modules.spore_studio"):
        pkg.ensure_sidecar_migration()
        pkg.ensure_sidecar_migration()
    assert calls == [1]
    assert pkg._sidecars_migrated is True
    assert "migrated 3 sidecar" in caplog.text


def test_migration_zero_is_quiet(monkeypatch, caplog):
    monkeypatch.setattr(pkg, "_sidecars_migrated", False)
    monkeypatch.setattr(template_parser_back, "migrate_all_sidecars", lambda: 0)
    with caplog.at_level(logging.INFO, logger="modules.spore_studio"):
        pkg.ensure_sidecar_migration()
    assert "migrated" not in caplog.text


def test_migration_error_is_non_fatal(monkeypatch, caplog):
    monkeypatch.setattr(pkg, "_sidecars_migrated", False)

    def boom():
        raise RuntimeError("disk")

    monkeypatch.setattr(template_parser_back, "migrate_all_sidecars", boom)
    pkg.ensure_sidecar_migration()
    assert pkg._sidecars_migrated is True
    assert "sidecar migration failed" in caplog.text


def test_migration_skipped_when_already_done(monkeypatch):
    monkeypatch.setattr(pkg, "_sidecars_migrated", True)
    monkeypatch.setattr(template_parser_back, "migrate_all_sidecars",
                        lambda: (_ for _ in ()).throw(AssertionError("should not run")))
    pkg.ensure_sidecar_migration()

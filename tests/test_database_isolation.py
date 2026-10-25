# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests never touch the developer's real database (see conftest._isolated_database)."""

from __future__ import annotations

from pathlib import Path


def test_global_database_points_at_a_temp_file():
    from modules import database_manager as dbm

    path = Path(dbm.database_manager._config.sql_database_path)
    assert path.is_absolute() and path.name == "mycelian-test.db"
    assert path.resolve() != (Path(__file__).resolve().parents[1] / "mycelian.db").resolve()

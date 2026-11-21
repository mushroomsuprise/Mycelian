# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The app database's WAL is folded into the file at shutdown (SVC-16)."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import database_manager as dm  # noqa: E402


def test_checkpoint_empties_the_wal(tmp_path):
    path = str(tmp_path / "app.db")
    db = object.__new__(dm.SQLDatabase)
    db.db_path = path
    db._initialized = True
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 200)
    conn.commit()
    assert os.path.getsize(path + "-wal") > 0
    assert db.checkpoint_wal() is True
    assert os.path.getsize(path + "-wal") == 0
    conn.close()
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 200


def test_checkpoint_is_skipped_when_not_ready_or_not_sqlite(tmp_path):
    db = object.__new__(dm.SQLDatabase)
    db._initialized = False
    assert db.checkpoint_wal() is False
    db._initialized = True
    db.db_path = str(tmp_path / "missing" / "x.db")
    assert db.checkpoint_wal() is False  # cannot open: logged, no error
    mgr = object.__new__(dm.DatabaseManager)
    mgr._database = SimpleNamespace()  # Firebase / Mongo: nothing to do
    assert mgr.checkpoint_wal() is False
    mgr._database = SimpleNamespace(checkpoint_wal=lambda: True)
    assert mgr.checkpoint_wal() is True

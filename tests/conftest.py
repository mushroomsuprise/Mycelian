# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Session-wide test guards.

The app's global database defaults to ``mycelian.db`` next to the code, which is the
developer's real data. Tests patch ``get_data``/``set_data`` with fakes, but a background
save thread (chatbot, giveaway, poll write-behind) can still be running after a test's
patch is removed and would then write fixture data into the real database. Point the
global database at a throwaway SQLite file for the whole session so that can't happen.

Every other data file (statistics, notification history, prefs) resolves through
``get_data_path``, which follows ``MYCELIAN_DATA_DIR``; that points at a temp folder too.
A guard fails the session if the real data changes anyway, and tests can't reach the network.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile

from pathlib import Path
from typing import Dict, List, Tuple

import pytest

# Shared helpers (ui_sim) are imported by name; importlib mode doesn't put tests/ on sys.path.
TESTS_DIR = str(Path(__file__).resolve().parent)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

# Data files (statistics, notification history, prefs, registries) go to a throwaway
# folder. Set before any app module is imported so module-level paths pick it up.
_DATA_DIR = tempfile.mkdtemp(prefix="mycelian-test-data-")
os.environ["MYCELIAN_DATA_DIR"] = _DATA_DIR


@pytest.fixture(scope="session", autouse=True)
def _isolated_database(tmp_path_factory):
    from modules import database_manager as dbm

    path = tmp_path_factory.mktemp("db") / "mycelian-test.db"
    # Config only: the manager still connects lazily (some tests rely on that), but
    # whenever it does, it opens this file instead of the real database.
    dbm.database_manager._config = dbm.DatabaseConfig(database_type="sql", sql_database_path=str(path))
    yield path


# --------------------------------------------------------------------------- real data guard

ROOT = Path(__file__).resolve().parents[1]

# The developer's real data. Tests must use tmp_path and fakes; the session fails if
# any of these change while the tests run.
_PROTECTED = (
    "mycelian.db", "mycelian.db-wal", "statistics.db", "config.json", "api_credentials.json",
    "notification_center_history.json", "template_preview_settings.json", ".encryption_key", "data",
    "templates", "assets",
)
_PROTECTED_STATE: Dict[str, Tuple[int, int]] = {}


def _protected_snapshot() -> Dict[str, Tuple[int, int]]:
    state: Dict[str, Tuple[int, int]] = {}
    for name in _PROTECTED:
        top = ROOT / name
        files = [top] if top.is_file() else (p for p in top.rglob("*") if p.is_file()) if top.is_dir() else []
        for path in files:
            if path.name.endswith("-shm") or "__pycache__" in path.parts:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            state[str(path.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
    return state


def protected_data_changes(before: Dict[str, Tuple[int, int]], after: Dict[str, Tuple[int, int]]) -> List[str]:
    changes = [f"changed: {k}" for k in sorted(before) if k in after and after[k] != before[k]]
    changes += [f"deleted: {k}" for k in sorted(before) if k not in after]
    changes += [f"created: {k}" for k in sorted(after) if k not in before]
    return changes


def pytest_sessionstart(session):
    _PROTECTED_STATE.clear()
    _PROTECTED_STATE.update(_protected_snapshot())


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)
    changes = protected_data_changes(_PROTECTED_STATE, _protected_snapshot())
    if not changes:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = ["REAL DATA CHANGED DURING THE TEST RUN (a test is not isolated):"] + [f"  {c}" for c in changes]
    lines.append("  If Mycelian itself was running during the tests, it may have made these changes.")
    if reporter is not None:
        reporter.write_sep("!", "real data guard", red=True)
        for line in lines:
            reporter.write_line(line, red=True)
    session.config._mycelian_protected_changes = changes
    if session.exitstatus == 0:
        session.exitstatus = 1


# --------------------------------------------------------------------------- network guard

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    host = address[0] if isinstance(address, tuple) and address else ""
    return str(host) in _LOCAL_HOSTS or str(host).startswith("127.")


def _guarded_connect(self, address):
    if not _is_local(address):
        raise ConnectionRefusedError(f"tests may not reach the network ({address!r}); mock the client instead")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        raise ConnectionRefusedError(f"tests may not reach the network ({address!r}); mock the client instead")
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield

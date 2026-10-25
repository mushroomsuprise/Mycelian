# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Session-wide test guards.

The app's global database defaults to ``mycelian.db`` next to the code, which is the
developer's real data. Tests patch ``get_data``/``set_data`` with fakes, but a background
save thread (chatbot, giveaway, poll write-behind) can still be running after a test's
patch is removed and would then write fixture data into the real database. Point the
global database at a throwaway SQLite file for the whole session so that can't happen.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_database(tmp_path_factory):
    from modules import database_manager as dbm

    path = tmp_path_factory.mktemp("db") / "mycelian-test.db"
    # Config only: the manager still connects lazily (some tests rely on that), but
    # whenever it does, it opens this file instead of the real database.
    dbm.database_manager._config = dbm.DatabaseConfig(database_type="sql", sql_database_path=str(path))
    yield path

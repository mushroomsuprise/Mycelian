# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/database_init.py: startup init, SQL fallback, settings migration, status."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import api_credentials_manager as acm
from modules import config_manager as cm
from modules import database_init as di
from modules import database_manager as dbm_mod
from modules.database_manager import DatabaseConfig


class FakeDBM:
    def __init__(self, results=(True,), init_errors=("",)):
        self.results = list(results)
        self.init_errors = list(init_errors)
        self.configs = []
        self.last_init_error = ""
        self._initialized = False
        self._config = DatabaseConfig()
        self.status = {"status": "Connected", "is_connected": True}

    def initialize(self, config):
        self.configs.append(DatabaseConfig(**config.__dict__))
        ok = self.results.pop(0) if self.results else True
        self.last_init_error = self.init_errors.pop(0) if self.init_errors else ""
        return ok

    def get_connection_status(self):
        return dict(self.status)

    def get_available_databases(self):
        return ["sql", "firebase"]

    def get_config(self):
        return self._config


class FakeConfig:
    def __init__(self, init_ok=True, data=None):
        self.init_ok = init_ok
        self._initialized = False
        self.config_path = "/tmp/fake-config.json"
        self.data = data if data is not None else {"database_type": "sql", "sql_database_path": "x.db"}
        self.set_types = []
        self.updates = []
        self.update_result = True

    def initialize(self):
        if self.init_ok:
            self._initialized = True
        return self.init_ok

    def get_database_config(self):
        return dict(self.data)

    def set_database_type(self, t):
        self.set_types.append(t)
        return True

    def update_database_config(self, **kw):
        self.updates.append(kw)
        return self.update_result


@pytest.fixture
def env(monkeypatch):
    fake_db = FakeDBM()
    fake_cfg = FakeConfig()
    notes = []
    migrations = []
    monkeypatch.setattr(di, "database_manager", fake_db)
    monkeypatch.setattr(cm, "config_manager", fake_cfg)
    monkeypatch.setattr(acm, "initialize_api_credentials", lambda: True)
    monkeypatch.setattr(di, "notify_critical", lambda *a, **k: notes.append((a, k)))
    monkeypatch.setattr(di, "last_startup_error", "")
    migrated = threading.Event()

    def fake_migrate(d):
        migrations.append(d)
        migrated.set()

    monkeypatch.setattr(di, "_migrate_old_database_settings_if_needed", fake_migrate)

    class Env:
        pass

    e = Env()
    e.db, e.cfg, e.notes, e.migrations, e.mp = fake_db, fake_cfg, notes, migrations, monkeypatch
    e.migrated = migrated
    return e


# ----------------------------------------------------------------- initialize_database_system

def test_set_startup_error(monkeypatch):
    monkeypatch.setattr(di, "last_startup_error", "")
    di._set_startup_error("boom")
    assert di.last_startup_error == "boom"


def test_initialize_success_builds_config_and_runs_migration(env):
    env.cfg.data = {"database_type": "sql", "sql_database_path": "my.db", "retry_attempts": 5}
    assert di.initialize_database_system() is True
    cfg = env.db.configs[0]
    assert cfg.database_type == "sql"
    assert cfg.sql_database_path == "my.db"
    assert cfg.retry_attempts == 5
    assert cfg.streamer_name == "mycelian"
    assert cfg.connection_timeout == 30
    assert env.migrated.wait(2.0)
    assert env.migrations == [env.cfg.data]
    assert di.last_startup_error == ""


def test_initialize_background_migration_error_swallowed(env):
    done = threading.Event()

    def failing(d):
        done.set()
        raise RuntimeError("x")

    env.mp.setattr(di, "_migrate_old_database_settings_if_needed", failing)
    assert di.initialize_database_system() is True
    assert done.wait(2.0)


def test_initialize_falls_back_to_sequential_when_loop_running(env, monkeypatch):
    import asyncio

    def fake_run(coro):
        coro.close()
        raise RuntimeError("loop running")

    monkeypatch.setattr(asyncio, "run", fake_run)
    assert di.initialize_database_system() is True
    assert env.cfg._initialized


def test_initialize_config_failure(env):
    env.cfg.init_ok = False
    assert di.initialize_database_system() is False
    assert "settings file" in di.last_startup_error
    assert "/tmp/fake-config.json" in di.last_startup_error
    assert env.db.configs == []


def test_initialize_api_credentials_failure(env, monkeypatch):
    monkeypatch.setattr(acm, "initialize_api_credentials", lambda: False)
    assert di.initialize_database_system() is False
    assert "API credentials" in di.last_startup_error


def test_initialize_sql_failure_sets_error(env):
    env.db.results = [False]
    env.db.init_errors = ["disk I/O error"]
    assert di.initialize_database_system() is False
    assert "local database" in di.last_startup_error
    assert "disk I/O error" in di.last_startup_error
    assert len(env.db.configs) == 1


def test_initialize_sql_failure_without_reason(env):
    env.db.results = [False]
    assert di.initialize_database_system() is False
    assert "See the log" in di.last_startup_error


def test_initialize_firebase_failure_falls_back_to_sql(env):
    env.cfg.data = {"database_type": "firebase"}
    env.db.results = [False, True]
    env.db.init_errors = ["auth failed", ""]
    assert di.initialize_database_system() is True
    assert [c.database_type for c in env.db.configs] == ["firebase", "sql"]
    assert env.cfg.set_types == ["sql"]
    assert len(env.notes) == 1
    kw = env.notes[0][1]
    assert kw["dedupe_key"] == "db:startup_fallback"
    assert kw["details"] == "auth failed"
    assert "Firebase" in kw["title"]


def test_initialize_fallback_also_fails(env):
    env.cfg.data = {"database_type": "mongodb"}
    env.db.results = [False, False]
    env.db.init_errors = ["mongo down", "sqlite locked"]
    assert di.initialize_database_system() is False
    assert "mongo down" in di.last_startup_error
    assert "sqlite locked" in di.last_startup_error
    assert env.cfg.set_types == []


def test_initialize_fallback_also_fails_without_reasons(env):
    env.cfg.data = {"database_type": "firebase"}
    env.db.results = [False, False]
    assert di.initialize_database_system() is False
    assert di.last_startup_error.count("see the log") == 2


def test_initialize_unexpected_exception(env):
    env.cfg.get_database_config = lambda: (_ for _ in ()).throw(ValueError("weird"))
    assert di.initialize_database_system() is False
    assert "weird" in di.last_startup_error


# ----------------------------------------------------------------- migration

@pytest.fixture
def mig(monkeypatch):
    cfg = FakeConfig()
    monkeypatch.setattr(cm, "config_manager", cfg)
    state = {"preload": None, "data": {}}
    monkeypatch.setattr(dbm_mod, "wait_for_startup_preload", lambda p, t: state["preload"])
    monkeypatch.setattr(dbm_mod, "get_data", lambda p: state["data"])
    return cfg, state


def test_migrate_empty_settings_skips(mig):
    cfg, state = mig
    assert di._migrate_old_database_settings_if_needed({}) is True
    assert cfg.updates == []


def test_migrate_get_data_none_skips(mig, monkeypatch):
    cfg, _ = mig
    monkeypatch.setattr(dbm_mod, "get_data", lambda p: None)
    assert di._migrate_old_database_settings_if_needed({}) is True
    assert cfg.updates == []


def test_migrate_already_migrated(mig):
    cfg, state = mig
    state["preload"] = {"firebase_database_url": "https://real.firebaseio.com/"}
    assert di._migrate_old_database_settings_if_needed({"database_settings_migrated": True}) is True
    assert cfg.updates == []


def test_migrate_moves_only_real_values(mig):
    cfg, state = mig
    state["data"] = {
        "firebase_database_url": "https://real.firebaseio.com/",
        "mongodb_connection_string": "mongodb://localhost:27017/",  # placeholder: skipped
        "sql_database_path": "custom.db",  # config already has a real value: skipped
        "mongodb_database_name": "",  # empty: skipped
        "firebase_service_account_path": "key.json",
        "not_a_field": "x",
    }
    config_data = {"sql_database_path": "keep.db", "firebase_service_account_path": "ServiceAccountKey.json"}
    assert di._migrate_old_database_settings_if_needed(config_data) is True
    assert cfg.updates == [
        {"firebase_database_url": "https://real.firebaseio.com/", "firebase_service_account_path": "key.json"},
        {"database_settings_migrated": True},
    ]


def test_migrate_nothing_to_move_still_marks_migrated(mig):
    cfg, state = mig
    state["preload"] = {"sql_database_path": "mycelian.db"}
    assert di._migrate_old_database_settings_if_needed({}) is True
    assert cfg.updates == [{"database_settings_migrated": True}]


def test_migrate_update_failure(mig):
    cfg, state = mig
    state["preload"] = {"sql_database_path": "other.db"}
    cfg.update_result = False
    assert di._migrate_old_database_settings_if_needed({}) is False
    assert cfg.updates == [{"sql_database_path": "other.db"}]


def test_migrate_inner_exception_returns_true(mig, monkeypatch):
    monkeypatch.setattr(dbm_mod, "wait_for_startup_preload",
                        lambda p, t: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert di._migrate_old_database_settings_if_needed({}) is True


def test_migrate_outer_exception_returns_true(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "dataobjects" and level == 1:
            raise ImportError("no dataobjects")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert di._migrate_old_database_settings_if_needed({}) is True


# ----------------------------------------------------------------- get_database_status

def test_status_with_config(env, monkeypatch):
    monkeypatch.setattr(di.os.path, "exists", lambda p: p == "ServiceAccountKey.json")
    env.cfg.data = {"database_type": "firebase"}
    s = di.get_database_status()
    assert s["is_connected"] is True
    assert s["available_databases"] == ["sql", "firebase"]
    assert s["firebase_key_exists"] is True
    assert s["settings_configured"] is True
    assert s["configured_database_type"] == "firebase"


def test_status_with_empty_config(env):
    env.cfg._initialized = True
    env.cfg.data = {}
    s = di.get_database_status()
    assert s["settings_configured"] is False
    assert s["configured_database_type"] is None


def test_status_config_init_fails(env):
    env.cfg.init_ok = False
    s = di.get_database_status()
    assert s["settings_configured"] is False
    assert s["configured_database_type"] is None


def test_status_config_raises(env):
    env.cfg.get_database_config = lambda: (_ for _ in ()).throw(RuntimeError())
    s = di.get_database_status()
    assert s["settings_configured"] is False


def test_status_db_error(env):
    env.db.get_connection_status = lambda: (_ for _ in ()).throw(RuntimeError("broken"))
    s = di.get_database_status()
    assert s["status"] == "Error: broken"
    assert s["is_connected"] is False
    assert s["available_databases"] == ["sql"]


# ----------------------------------------------------------------- ensure_database_initialized

def test_ensure_not_initialized_calls_init(env, monkeypatch):
    monkeypatch.setattr(di, "initialize_database_system", lambda: "init-called")
    assert di.ensure_database_initialized() == "init-called"


def test_ensure_initialized_same_type(env, monkeypatch):
    env.db._initialized = True
    monkeypatch.setattr(di, "initialize_database_system", lambda: pytest.fail("re-init"))
    assert di.ensure_database_initialized() is True
    assert env.cfg._initialized


def test_ensure_initialized_type_changed_reinitializes(env, monkeypatch):
    env.db._initialized = True
    env.cfg._initialized = True
    env.cfg.data = {"database_type": "firebase"}
    monkeypatch.setattr(di, "initialize_database_system", lambda: "re-init")
    assert di.ensure_database_initialized() == "re-init"
    assert env.db._initialized is False


def test_ensure_exception_sets_error(env):
    env.db._initialized = True
    env.db.get_config = lambda: (_ for _ in ()).throw(RuntimeError("cfg broken"))
    assert di.ensure_database_initialized() is False
    assert "cfg broken" in di.last_startup_error

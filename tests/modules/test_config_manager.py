# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/config_manager.py: AppConfig and ConfigManager file handling (tmp_path only)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import config_manager as cm


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(cm, "notify_critical", lambda *a, **k: calls.append(k))
    return calls


@pytest.fixture
def mgr(tmp_path):
    return cm.ConfigManager(str(tmp_path / "cfg" / "config.json"))


# ----------------------------------------------------------------- AppConfig / construction

def test_appconfig_defaults():
    c = cm.AppConfig()
    assert c.database_type == "sql"
    assert c.sql_database_path == "mycelian.db"
    assert c.retry_attempts == 3
    assert c.database_settings_migrated is False


def test_default_path_uses_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    m = cm.ConfigManager()
    assert m.config_path == tmp_path / "config.json"


def test_custom_path_used_as_is(tmp_path):
    m = cm.ConfigManager(str(tmp_path / "x.json"))
    assert m.config_path == tmp_path / "x.json"
    assert m._config is None and m._initialized is False


# ----------------------------------------------------------------- initialize

def test_initialize_creates_default_file(mgr, notes):
    assert mgr.initialize() is True
    assert mgr.config_path.exists()
    data = json.loads(mgr.config_path.read_text())
    assert data["database_type"] == "sql"
    assert data["last_updated"]
    assert notes == []
    # Second call short-circuits
    mgr.config_path.unlink()
    assert mgr.initialize() is True
    assert not mgr.config_path.exists()


def test_initialize_loads_existing_and_ignores_unknown_fields(mgr):
    mgr.config_path.parent.mkdir(parents=True)
    mgr.config_path.write_text(json.dumps({"database_type": "firebase", "bogus": 1, "retry_attempts": 7}))
    assert mgr.initialize()
    assert mgr.get_database_type() == "firebase"
    assert mgr.get_config().retry_attempts == 7


def test_load_invalid_json_falls_back_to_defaults_and_notifies(mgr, notes):
    mgr.config_path.parent.mkdir(parents=True)
    mgr.config_path.write_text("{not json")
    assert mgr.initialize()
    assert mgr.get_database_type() == "sql"
    assert notes and notes[0]["dedupe_key"] == "config:load_failed"
    assert notes[0]["actions"]
    # The bad file is not overwritten by load
    assert mgr.config_path.read_text() == "{not json"


def test_initialize_failure_notifies_and_returns_false(mgr, notes, monkeypatch):
    monkeypatch.setattr(mgr, "_create_default_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert mgr.initialize() is False
    assert notes[-1]["dedupe_key"] == "config:init_failed"
    assert "boom" in notes[-1]["details"]
    assert mgr._initialized is False


# ----------------------------------------------------------------- _save_config

def test_save_without_config_returns_false(mgr):
    assert mgr._save_config() is False


def test_save_is_atomic_and_sorted(mgr):
    mgr._config = cm.AppConfig(database_type="mongodb")
    assert mgr._save_config() is True
    text = mgr.config_path.read_text()
    data = json.loads(text)
    assert data["database_type"] == "mongodb"
    assert list(data) == sorted(data)
    leftovers = [p for p in mgr.config_path.parent.iterdir() if p.name.startswith(".config.")]
    assert leftovers == []


def test_save_failure_removes_temp_and_notifies(mgr, notes, monkeypatch):
    mgr._config = cm.AppConfig()
    monkeypatch.setattr(cm.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("replace failed")))
    assert mgr._save_config() is False
    assert notes[-1]["dedupe_key"] == "config:save_failed"
    assert list(mgr.config_path.parent.iterdir()) == []


def test_save_failure_unlink_error_ignored(mgr, notes, monkeypatch):
    mgr._config = cm.AppConfig()
    monkeypatch.setattr(cm.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("replace failed")))
    monkeypatch.setattr(cm.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("unlink failed")))
    assert mgr._save_config() is False
    assert notes


# ----------------------------------------------------------------- getters / setters

def test_get_config_initializes_lazily(mgr):
    cfg = mgr.get_config()
    assert isinstance(cfg, cm.AppConfig)
    assert mgr._initialized


def test_get_database_type_without_config_defaults_sql(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "initialize", lambda: False)
    assert mgr.get_database_type() == "sql"


@pytest.mark.parametrize("dbtype", ["sql", "firebase", "mongodb"])
def test_set_database_type_valid(mgr, dbtype):
    assert mgr.set_database_type(dbtype) is True
    assert json.loads(mgr.config_path.read_text())["database_type"] == dbtype


def test_set_database_type_invalid(mgr):
    assert mgr.set_database_type("postgres") is False
    assert mgr.get_database_type() == "sql"


def test_set_database_type_without_config(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "initialize", lambda: False)
    assert mgr.set_database_type("sql") is False


def test_set_database_type_reverts_on_save_failure(mgr, monkeypatch):
    mgr.initialize()
    monkeypatch.setattr(mgr, "_save_config", lambda: False)
    assert mgr.set_database_type("firebase") is False
    assert mgr.get_database_type() == "sql"


def test_update_database_config_without_config(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "initialize", lambda: False)
    assert mgr.update_database_config(retry_attempts=1) is False


def test_update_database_config_updates_and_normalizes(mgr, monkeypatch, caplog):
    calls = []

    def fake_norm(url):
        calls.append(url)
        return "https://normalized/"

    monkeypatch.setattr(cm, "normalize_firebase_database_url", fake_norm)
    caplog.set_level("INFO", logger=cm.logger.name)
    assert mgr.update_database_config(
        firebase_database_url="  raw  ",
        retry_attempts=9,
        mongodb_connection_string="mongodb://user:secretpw@host/",
        unknown_field="x",
    ) is True
    cfg = mgr.get_config()
    assert calls == ["  raw  "]
    assert cfg.firebase_database_url == "https://normalized/"
    assert cfg.retry_attempts == 9
    assert not hasattr(cfg, "unknown_field")
    saved = json.loads(mgr.config_path.read_text())
    assert saved["retry_attempts"] == 9
    assert "secretpw" not in caplog.text
    assert "mongodb_connection_string: (changed)" in caplog.text


def test_update_mongodb_unchanged_logged_as_unchanged(mgr, caplog):
    mgr.initialize()
    caplog.set_level("INFO", logger=cm.logger.name)
    current = mgr.get_config().mongodb_connection_string
    mgr.update_database_config(mongodb_connection_string=current)
    assert "(unchanged)" in caplog.text


def test_update_with_non_string_firebase_url_skips_normalize(mgr, monkeypatch):
    monkeypatch.setattr(cm, "normalize_firebase_database_url", lambda u: pytest.fail("called"))
    assert mgr.update_database_config(firebase_database_url=None) is True
    assert mgr.get_config().firebase_database_url is None


def test_update_with_no_known_fields_does_not_save(mgr, monkeypatch):
    mgr.initialize()
    monkeypatch.setattr(mgr, "_save_config", lambda: pytest.fail("saved"))
    assert mgr.update_database_config(nope=1) is True


def test_get_database_config(mgr):
    d = mgr.get_database_config()
    assert set(d) == {
        "database_type", "sql_database_path", "firebase_service_account_path", "firebase_database_url",
        "mongodb_connection_string", "mongodb_database_name", "connection_timeout", "retry_attempts",
    }
    assert d["database_type"] == "sql"


def test_get_database_config_without_config(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "initialize", lambda: False)
    assert mgr.get_database_config() == {}


# ----------------------------------------------------------------- export / import

def test_export_without_config(mgr, tmp_path):
    assert mgr.export_config(str(tmp_path / "out.json")) is False


def test_export_and_import_round_trip(mgr, tmp_path):
    mgr.initialize()
    mgr.set_database_type("mongodb")
    out = tmp_path / "exports" / "out.json"
    assert mgr.export_config(str(out)) is True
    assert json.loads(out.read_text())["database_type"] == "mongodb"

    other = cm.ConfigManager(str(tmp_path / "other.json"))
    data = json.loads(out.read_text())
    data["junk"] = True
    out.write_text(json.dumps(data))
    assert other.import_config(str(out)) is True
    assert other._config.database_type == "mongodb"
    assert json.loads((tmp_path / "other.json").read_text())["database_type"] == "mongodb"


def test_export_failure(mgr, tmp_path):
    mgr.initialize()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert mgr.export_config(str(blocker / "sub" / "out.json")) is False


def test_import_missing_file(mgr, tmp_path):
    assert mgr.import_config(str(tmp_path / "missing.json")) is False


def test_import_bad_json(mgr, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[")
    assert mgr.import_config(str(p)) is False


def test_import_save_failure(mgr, tmp_path, monkeypatch):
    p = tmp_path / "in.json"
    p.write_text(json.dumps(asdict(cm.AppConfig(database_type="firebase"))))
    monkeypatch.setattr(mgr, "_save_config", lambda: False)
    assert mgr.import_config(str(p)) is False
    assert mgr._config.database_type == "firebase"


# ----------------------------------------------------------------- module-level helpers

def test_module_helpers_delegate_to_global(monkeypatch, tmp_path):
    m = cm.ConfigManager(str(tmp_path / "global.json"))
    monkeypatch.setattr(cm, "config_manager", m)
    assert cm.initialize_config() is True
    assert cm.get_database_type() == "sql"
    assert cm.set_database_type("firebase") is True
    assert cm.get_database_config()["database_type"] == "firebase"
    assert cm.set_database_type("nope") is False


def test_global_instance_points_at_data_dir():
    assert str(cm.config_manager.config_path).startswith(os.environ["MYCELIAN_DATA_DIR"]) or \
        cm.config_manager.config_path.name == "config.json"

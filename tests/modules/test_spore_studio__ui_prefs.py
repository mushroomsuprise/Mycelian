# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/ui_prefs.py (persisted Spore Studio UI flags)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.spore_studio import ui_prefs


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    target = tmp_path / "data" / "spore_studio_ui_prefs.json"
    monkeypatch.setattr(ui_prefs, "get_data_path", lambda rel: str(tmp_path / rel))
    return target


def test_prefs_path_uses_data_folder(prefs_file):
    assert ui_prefs._prefs_path() == str(prefs_file)


def test_load_missing_file_returns_empty(prefs_file):
    assert ui_prefs.load_prefs() == {}


def test_load_reads_dict(prefs_file):
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text(json.dumps({"tour": True}), encoding="utf-8")
    assert ui_prefs.load_prefs() == {"tour": True}


def test_load_non_dict_returns_empty(prefs_file):
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text("[1, 2]", encoding="utf-8")
    assert ui_prefs.load_prefs() == {}


def test_load_corrupt_json_logs_and_returns_empty(prefs_file, caplog):
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text("{not json", encoding="utf-8")
    assert ui_prefs.load_prefs() == {}
    assert "Could not read" in caplog.text


def test_update_non_dict_returns_current(prefs_file):
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert ui_prefs.update_prefs(["nope"]) == {"a": 1}
    assert ui_prefs.update_prefs(None) == {"a": 1}


def test_update_creates_folder_and_persists(prefs_file):
    out = ui_prefs.update_prefs({"tour_offered": True, "zoom": 1.5})
    assert out == {"tour_offered": True, "zoom": 1.5}
    on_disk = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert on_disk == out
    assert not os.path.exists(str(prefs_file) + ".tmp")


def test_update_merges_and_none_removes(prefs_file):
    ui_prefs.update_prefs({"a": 1, "b": 2})
    out = ui_prefs.update_prefs({"a": None, "c": "x", "missing": None})
    assert out == {"b": 2, "c": "x"}
    assert ui_prefs.load_prefs() == {"b": 2, "c": "x"}


@pytest.mark.parametrize("key", [1, "", "k" * 81, None])
def test_update_skips_bad_keys(prefs_file, key):
    out = ui_prefs.update_prefs({key: "v", "ok": 1})
    assert out == {"ok": 1}


def test_update_key_of_80_chars_allowed(prefs_file):
    key = "k" * 80
    assert ui_prefs.update_prefs({key: 1}) == {key: 1}


def test_update_skips_values_too_long(prefs_file):
    out = ui_prefs.update_prefs({"big": "x" * 2000, "small": "y"})
    assert out == {"small": "y"}


def test_update_skips_unserializable_values(prefs_file):
    out = ui_prefs.update_prefs({"obj": object(), "nan_list": {1, 2}, "ok": True})
    assert out == {"ok": True}


def test_update_caps_number_of_keys(prefs_file):
    ui_prefs.update_prefs({f"k{i}": i for i in range(ui_prefs._MAX_KEYS)})
    out = ui_prefs.update_prefs({"new": 1, "k0": "changed"})
    assert "new" not in out
    assert out["k0"] == "changed"
    assert len(out) == ui_prefs._MAX_KEYS


def test_update_write_failure_logs_and_returns_merged(prefs_file, monkeypatch, caplog):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ui_prefs.os, "replace", boom)
    out = ui_prefs.update_prefs({"a": 1})
    assert out == {"a": 1}
    assert "Could not save" in caplog.text
    assert not prefs_file.exists()

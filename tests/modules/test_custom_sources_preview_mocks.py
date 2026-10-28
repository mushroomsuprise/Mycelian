# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/custom_sources_preview_mocks.py (preview toolbar mock actions)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import custom_sources_preview_mocks as mocks  # noqa: E402


@pytest.fixture
def tpl_dir(tmp_path, monkeypatch):
    (tmp_path / "template_configs").mkdir()
    monkeypatch.setattr(mocks, "get_template_path", lambda rel="": os.path.join(str(tmp_path), rel))
    return tmp_path / "template_configs"


def _write(tpl_dir, name, data):
    path = tpl_dir / f"{name}.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_empty_name_returns_empty(tpl_dir):
    assert mocks._load_preview_mocks_from_config("") == []


def test_missing_file_returns_empty(tpl_dir):
    assert mocks._load_preview_mocks_from_config("nope") == []


def test_bad_json_returns_empty(tpl_dir):
    _write(tpl_dir, "bad", "{not json")
    assert mocks._load_preview_mocks_from_config("bad") == []


def test_non_dict_json_returns_empty(tpl_dir):
    _write(tpl_dir, "lst", [1, 2])
    assert mocks._load_preview_mocks_from_config("lst") == []


@pytest.mark.parametrize("raw", [None, [], "x", {"a": 1}])
def test_missing_or_bad_preview_mocks_returns_empty(tpl_dir, raw):
    _write(tpl_dir, "cfg", {"preview_mocks": raw} if raw is not None else {})
    assert mocks._load_preview_mocks_from_config("cfg") == []


def test_open_error_returns_empty(tpl_dir, monkeypatch):
    _write(tpl_dir, "cfg", {"preview_mocks": [{"event": "x"}]})
    import builtins

    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path).endswith("cfg.json"):
            raise OSError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert mocks._load_preview_mocks_from_config("cfg") == []


def test_entries_are_normalized_and_invalid_ones_skipped(tpl_dir):
    _write(tpl_dir, "cfg", {"preview_mocks": [
        "not a dict",
        {"label": "no event"},
        {"event": 5},
        {"event": "  spin  "},
        {"event": "alert", "label": "Alert!", "alert_type": " sub ", "tooltip": 42},
        {"event": "x", "alert_type": 3, "tooltip": ""},
    ]})
    assert mocks._load_preview_mocks_from_config("cfg") == [
        {"event": "spin", "label": "  spin  "},
        {"event": "alert", "label": "Alert!", "alert_type": "sub", "tooltip": "42"},
        {"event": "x", "label": "x"},
    ]


def test_get_mock_actions_prefers_config(tpl_dir):
    _write(tpl_dir, "alerts", {"preview_mocks": [{"event": "custom"}]})
    assert mocks.get_mock_actions("alerts") == [{"event": "custom", "label": "custom"}]


def test_get_mock_actions_falls_back_to_static_map(tpl_dir):
    out = mocks.get_mock_actions("alerts")
    assert out == mocks._MOCKS["alerts"]
    out.append({"event": "extra"})
    assert {"event": "extra"} not in mocks._MOCKS["alerts"]


def test_get_mock_actions_unknown_and_empty(tpl_dir):
    assert mocks.get_mock_actions("unknown_template") == []
    assert mocks.get_mock_actions("ff7") == []
    assert mocks.get_mock_actions("") == []

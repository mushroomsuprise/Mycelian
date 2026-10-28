# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/template_preview_settings.py (previewer settings file).

Every test points settings_path() at tmp_path; the real template_preview_settings.json is never touched.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import template_preview_settings as tps  # noqa: E402

_REAL_SETTINGS_PATH = tps.settings_path


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "template_preview_settings.json"
    monkeypatch.setattr(tps, "settings_path", lambda: str(path))
    return path


def test_settings_path_uses_data_path(monkeypatch):
    monkeypatch.setattr(tps, "get_data_path", lambda name: f"/fake/{name}")
    assert _REAL_SETTINGS_PATH() == "/fake/template_preview_settings.json"


def test_defaults_when_missing(settings_file):
    data = tps.load_template_preview_settings()
    assert data == {
        "enable_preview_sounds": True,
        "show_mock_toolbar": True,
        "show_inline_preview": True,
        "split_editor_pct": 56.0,
        "resolutions": {},
    }
    # defaults dict is not shared
    data["resolutions"]["x"] = 1
    assert tps._DEFAULTS["resolutions"] == {}


def test_load_coerces_and_cleans(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({
        "enable_preview_sounds": 0,
        "show_mock_toolbar": "",
        "show_inline_preview": 1,
        "split_editor_pct": 500,
        "extra": "kept",
        "resolutions": {
            "ok": {"width": 1280, "height": 720},
            " padded ": {"width": "800", "height": 600.7},
            "": {"width": 1, "height": 1},
            "   ": {"width": 1, "height": 1},
            "notdict": [1, 2],
            "bad": {"width": "x", "height": 3},
            "huge": {"width": 99999, "height": 1},
        },
    }), encoding="utf-8")
    data = tps.load_template_preview_settings()
    assert data["enable_preview_sounds"] is False
    assert data["show_mock_toolbar"] is False
    assert data["show_inline_preview"] is True
    assert data["split_editor_pct"] == tps.SPLIT_EDITOR_PCT_MAX
    assert data["extra"] == "kept"
    assert data["resolutions"] == {
        "ok": {"width": 1280, "height": 720},
        "padded": {"width": 800, "height": 600},
        "huge": {"width": tps.PREVIEW_WIDTH_MAX, "height": tps.PREVIEW_HEIGHT_MIN},
    }


def test_load_non_dict_resolutions_reset(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({"resolutions": [1]}), encoding="utf-8")
    assert tps.load_template_preview_settings()["resolutions"] == {}


def test_load_non_dict_json_keeps_defaults(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("[1, 2]", encoding="utf-8")
    assert tps.load_template_preview_settings()["split_editor_pct"] == 56.0


def test_load_corrupt_json_warns_and_uses_defaults(settings_file, caplog):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{oops", encoding="utf-8")
    data = tps.load_template_preview_settings()
    assert data["enable_preview_sounds"] is True
    assert "Could not load template preview settings" in caplog.text


def test_save_creates_parent_and_writes_sorted_json(settings_file):
    assert tps.save_template_preview_settings({"enable_preview_sounds": False})
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    assert raw["enable_preview_sounds"] is False
    assert list(raw) == sorted(raw)
    assert not os.path.exists(str(settings_file) + ".tmp")


def test_save_resolution_merge_rules(settings_file):
    tps.save_template_preview_settings({"resolutions": {"a": {"width": 100, "height": 100},
                                                        "b": {"width": 200, "height": 200}}})
    tps.save_template_preview_settings({"resolutions": {
        "a": None,
        " c ": {"width": 300, "height": 300},
        "": {"width": 1, "height": 1},
        5: {"width": 1, "height": 1},
        "d": "notdict",
        "e": {"width": 0, "height": 5},
    }, "show_mock_toolbar": False})
    data = tps.load_template_preview_settings()
    assert data["resolutions"] == {"b": {"width": 200, "height": 200}, "c": {"width": 300, "height": 300}}
    assert data["show_mock_toolbar"] is False


def test_save_non_dict_resolutions_replaces_directly(settings_file):
    tps.save_template_preview_settings({"resolutions": {"a": {"width": 100, "height": 100}}})
    assert tps.save_template_preview_settings({"resolutions": "junk"})
    assert json.loads(settings_file.read_text())["resolutions"] == "junk"
    # Loader cleans it up again
    assert tps.load_template_preview_settings()["resolutions"] == {}


def test_save_failure_returns_false_and_removes_tmp(settings_file, monkeypatch, caplog):
    def bad_replace(src, dst):
        assert os.path.exists(src)
        raise OSError("disk full")

    monkeypatch.setattr(tps.os, "replace", bad_replace)
    assert tps.save_template_preview_settings({"x": 1}) is False
    assert not os.path.exists(str(settings_file) + ".tmp")
    assert "Could not save template preview settings" in caplog.text


def test_save_failure_cleanup_error_is_swallowed(settings_file, monkeypatch):
    monkeypatch.setattr(tps.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(tps.os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked")))
    assert tps.save_template_preview_settings({"x": 1}) is False


def test_save_without_parent_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tps, "settings_path", lambda: "plain.json")
    assert tps.save_template_preview_settings({"x": 2})
    assert json.loads((tmp_path / "plain.json").read_text())["x"] == 2


@pytest.mark.parametrize("key", ["", "   ", None])
def test_resolution_helpers_reject_blank_keys(key):
    assert tps.get_template_preview_resolution(key) is None
    assert tps.set_template_preview_resolution(key, 100, 100) is False
    assert tps.clear_template_preview_resolution(key) is False


def test_set_rejects_bad_size(settings_file):
    assert tps.set_template_preview_resolution("a", "x", 5) is False
    assert not settings_file.exists()


def test_set_get_clear_round_trip_with_strip(settings_file):
    assert tps.set_template_preview_resolution(" ff7 ", "1280.9", 720)
    assert tps.get_template_preview_resolution("ff7") == (1280, 720)
    assert tps.get_template_preview_resolution(" ff7 ") == (1280, 720)
    assert tps.clear_template_preview_resolution(" ff7 ")
    assert tps.get_template_preview_resolution("ff7") is None


def test_get_entry_not_dict(monkeypatch):
    monkeypatch.setattr(tps, "load_template_preview_settings", lambda: {"resolutions": {"k": [1, 2]}})
    assert tps.get_template_preview_resolution("k") is None
    monkeypatch.setattr(tps, "load_template_preview_settings", lambda: {"resolutions": None})
    assert tps.get_template_preview_resolution("k") is None


@pytest.mark.parametrize(
    "w,h,expected",
    [
        (1280, 720, (1280, 720)),
        ("640", "480.5", (640, 480)),
        (1, 1, (tps.PREVIEW_WIDTH_MIN, tps.PREVIEW_HEIGHT_MIN)),
        (10**6, 10**6, (tps.PREVIEW_WIDTH_MAX, tps.PREVIEW_HEIGHT_MAX)),
        (0, 10, None),
        (10, -1, None),
        (None, 10, None),
        ("abc", 10, None),
    ],
)
def test_clamp_preview_resolution(w, h, expected):
    assert tps.clamp_preview_resolution(w, h) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(50, 50.0), ("40.5", 40.5), (0, tps.SPLIT_EDITOR_PCT_MIN), (100, tps.SPLIT_EDITOR_PCT_MAX),
     (None, tps.SPLIT_EDITOR_PCT_DEFAULT), ("x", tps.SPLIT_EDITOR_PCT_DEFAULT),
     (float("nan"), tps.SPLIT_EDITOR_PCT_DEFAULT)],
)
def test_clamp_split_editor_pct(value, expected):
    assert tps.clamp_split_editor_pct(value) == expected

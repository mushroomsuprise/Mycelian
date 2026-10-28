# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for scripts/migrate_color_grid_configs.py, run only against tmp_path copies."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import migrate_color_grid_configs as mig  # noqa: E402



def _run_as_script(script: str, fake_file: Path, argv=None):
    """Execute the real script source as ``__main__`` with ``__file__`` pointing under tmp_path.

    The fake ``__file__`` has a different basename so coverage keeps the real file name.
    """
    real = ROOT / "scripts" / script
    code = compile(real.read_text(encoding="utf-8"), str(real), "exec")
    old_argv = sys.argv
    sys.argv = [str(fake_file), *(argv or [])]
    try:
        exec(code, {"__name__": "__main__", "__file__": str(fake_file), "__builtins__": __builtins__})
    finally:
        sys.argv = old_argv

def test_config_dir_points_at_template_configs():
    assert mig.CONFIG_DIR == ROOT / "templates" / "template_configs"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("transparent", True),
        ("  TRANSPARENT ", True),
        ("rgba(0, 0, 0, 0.5)", True),
        ("rgba(0,0,0,0)", True),
        ("RGBA(1,2,3,1)", False),
        ("rgba(1,2,3,1.0)", False),
        ("rgba(1,2,3)", False),
        ("rgba(1,2,3,abc)", False),
        ("rgb(1,2,3)", False),
        ("#ff000080", False),
        (None, False),
        (0.5, False),
    ],
)
def test_is_transparent_value(value, expected):
    assert mig._is_transparent_value(value) is expected


def test_migrate_element_converts_color_grid_select():
    el = {"type": "select", "display": "color_grid", "value": "transparent", "options": ["#fff"]}
    assert mig.migrate_element(el) is True
    assert el == {"type": "color", "value": "transparent", "options": ["#fff"], "transparent": True}


def test_migrate_element_opaque_value():
    el = {"type": "select", "display": "color_grid", "value": "#123456"}
    assert mig.migrate_element(el) is True
    assert el["transparent"] is False and "display" not in el


@pytest.mark.parametrize(
    "el",
    [
        {"type": "select", "display": "dropdown"},
        {"type": "color", "display": "color_grid"},
        {"type": "select"},
        {},
    ],
)
def test_migrate_element_ignores_others(el):
    before = dict(el)
    assert mig.migrate_element(el) is False
    assert el == before


def test_migrate_file_rewrites_when_changed(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "name": "x",
                "elements": [
                    {"type": "select", "display": "color_grid", "value": "rgba(0,0,0,0.2)"},
                    {"type": "text", "value": "hi"},
                    {"type": "select", "display": "color_grid", "value": "#fff"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert mig.migrate_file(path) == 2
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and '\n    "name"' in text  # indent=4 + trailing newline
    data = json.loads(text)
    assert [e["type"] for e in data["elements"]] == ["color", "text", "color"]
    assert [e.get("transparent") for e in data["elements"]] == [True, None, False]


def test_migrate_file_untouched_when_nothing_to_do(tmp_path):
    path = tmp_path / "a.json"
    original = '{"elements": [{"type": "text"}]}'
    path.write_text(original, encoding="utf-8")
    assert mig.migrate_file(path) == 0
    assert path.read_text(encoding="utf-8") == original


def test_migrate_file_without_elements(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")
    assert mig.migrate_file(path) == 0


def test_migrate_file_invalid_json_raises(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        mig.migrate_file(path)


def test_main_migrates_folder(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "template_configs"
    cfg.mkdir()
    (cfg / "b.json").write_text(json.dumps({"elements": [{"type": "select", "display": "color_grid"}] * 2}))
    (cfg / "a.json").write_text(json.dumps({"elements": [{"type": "text"}]}))
    (cfg / "c.json").write_text(json.dumps({"elements": [{"type": "select", "display": "color_grid"}]}))
    (cfg / "notes.txt").write_text("ignored")
    monkeypatch.setattr(mig, "CONFIG_DIR", cfg)
    assert mig.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["b.json: migrated 2 element(s)", "c.json: migrated 1 element(s)", "Done — 3 element(s) across 2 file(s)."]


def test_main_empty_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mig, "CONFIG_DIR", tmp_path / "missing")
    assert mig.main() == 0
    assert "Done — 0 element(s) across 0 file(s)." in capsys.readouterr().out


def test_script_entry_point(tmp_path):
    (tmp_path / "scripts").mkdir()
    cfg = tmp_path / "templates" / "template_configs"
    cfg.mkdir(parents=True)
    (cfg / "a.json").write_text(json.dumps({"elements": [{"type": "select", "display": "color_grid"}]}))
    with pytest.raises(SystemExit) as exc:
        _run_as_script("migrate_color_grid_configs.py", tmp_path / "scripts" / "script_under_test.py")
    assert exc.value.code == 0
    assert json.loads((cfg / "a.json").read_text())["elements"][0]["type"] == "color"

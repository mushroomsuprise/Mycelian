# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for merge_template_configs.py (the installer's template config merge helper).

Everything runs against folders under tmp_path; nothing touches the real templates.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import merge_template_configs as mtc  # noqa: E402


# --------------------------------------------------------------------------- helpers


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _run_main(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["merge_template_configs.py", *map(str, args)])
    return mtc.main()


# --------------------------------------------------------------------------- type helpers


@pytest.mark.parametrize(
    "value,expected",
    [([], True), ([1, 2], True), ("abc", False), ({}, False), ((1,), False), (None, False)],
)
def test_is_array(value, expected):
    assert mtc.is_array(value) is expected


@pytest.mark.parametrize(
    "value,expected", [({}, True), ({"a": 1}, True), ([], False), ("x", False), (None, False)]
)
def test_is_object(value, expected):
    assert mtc.is_object(value) is expected


def test_clone_value_none_returns_none():
    assert mtc.clone_value(None) is None


def test_clone_value_is_deep_copy():
    original = {"a": [1, {"b": 2}]}
    clone = mtc.clone_value(original)
    assert clone == original
    clone["a"][1]["b"] = 99
    assert original["a"][1]["b"] == 2


def test_clone_value_unserializable_returns_same_object():
    obj = {"s": {1, 2}}  # sets are not JSON serializable -> TypeError
    assert mtc.clone_value(obj) is obj


def test_clone_value_value_error_returns_same_object():
    circular: list = []
    circular.append(circular)  # json.dumps raises ValueError (circular reference)
    assert mtc.clone_value(circular) is circular


# --------------------------------------------------------------------------- merge_element_arrays


def test_merge_element_arrays_non_arrays_return_clone_of_new():
    assert mtc.merge_element_arrays({"a": 1}, [1]) == {"a": 1}
    assert mtc.merge_element_arrays([1, 2], "old") == [1, 2]


def test_merge_element_arrays_without_ids_uses_new_as_is():
    assert mtc.merge_element_arrays([1, 2, 3], [4, 5]) == [1, 2, 3]
    # New has ids but old does not -> new as-is
    new = [{"id": "a", "value": 1}]
    assert mtc.merge_element_arrays(new, [{"value": 9}]) == new


def test_merge_element_arrays_transfers_values_and_drops_old_only():
    new = [
        {"id": "color", "value": "#000", "label": "New label"},
        {"type": "separator"},
        {"id": "size", "value": 10},
        {"id": "fresh", "value": "default"},
    ]
    old = [
        {"id": "color", "value": "#fff", "label": "Old label"},
        {"id": "size", "value": 22},
        {"id": "gone", "value": "removed"},
        "not-a-dict",
    ]
    merged = mtc.merge_element_arrays(new, old)
    assert merged == [
        {"id": "color", "value": "#fff", "label": "New label"},
        {"type": "separator"},
        {"id": "size", "value": 22},
        {"id": "fresh", "value": "default"},
    ]
    # The result is a copy, not the new list's objects
    assert merged[0] is not new[0]


def test_merge_element_arrays_value_only_copied_when_both_have_value():
    new = [{"id": "a", "label": "no value key"}, {"id": "b", "value": 1}]
    old = [{"id": "a", "value": "x"}, {"id": "b", "label": "no value"}]
    assert mtc.merge_element_arrays(new, old) == [{"id": "a", "label": "no value key"}, {"id": "b", "value": 1}]


def test_merge_element_arrays_first_old_duplicate_id_wins():
    new = [{"id": 1, "value": 0}]
    old = [{"id": "1", "value": "first"}, {"id": 1, "value": "second"}]
    # ids are compared as strings; the first old entry wins
    assert mtc.merge_element_arrays(new, old) == [{"id": 1, "value": "first"}]


def test_merge_element_arrays_recurses_into_nested_elements():
    new = [{"id": "group", "elements": [{"id": "x", "value": 1}, {"id": "y", "value": 2}]}]
    old = [{"id": "group", "elements": [{"id": "x", "value": 100}, {"id": "z", "value": 3}]}]
    assert mtc.merge_element_arrays(new, old) == [
        {"id": "group", "elements": [{"id": "x", "value": 100}, {"id": "y", "value": 2}]}
    ]


# --------------------------------------------------------------------------- merge_config


def test_merge_config_non_objects_return_new():
    assert mtc.merge_config([1], {"a": 1}) == [1]
    assert mtc.merge_config({"a": 1}, None) == {"a": 1}
    assert mtc.merge_config("x", "y") == "x"


def test_merge_config_full_structure():
    new = {
        "name": "New name",
        "value": "default",
        "new_key": 5,
        "elements": [{"id": "a", "value": 1}],
        "nested": {"value": 0, "label": "new", "inner": {"value": "n"}},
        "options": ["one", "two"],
        "min": 0,
    }
    old = {
        "name": "Old name",
        "value": {"custom": True},
        "removed_key": "bye",
        "elements": [{"id": "a", "value": 50}],
        "nested": {"value": 7, "label": "old", "inner": {"value": "o"}},
        "options": ["old"],
        "min": "type mismatch keeps new",
    }
    merged = mtc.merge_config(new, old)
    assert merged == {
        "name": "New name",
        "value": {"custom": True},
        "new_key": 5,
        "elements": [{"id": "a", "value": 50}],
        "nested": {"value": 7, "label": "new", "inner": {"value": "o"}},
        "options": ["one", "two"],
        "min": 0,
    }
    assert "removed_key" not in merged
    # Inputs are untouched
    assert new["value"] == "default" and old["value"] == {"custom": True}


def test_merge_config_value_cloned_from_old():
    old_value = {"deep": [1]}
    merged = mtc.merge_config({"value": None}, {"value": old_value})
    merged["value"]["deep"].append(2)
    assert old_value == {"deep": [1]}


# --------------------------------------------------------------------------- filesystem helpers


def test_ensure_directory_creates_parents(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    mtc.ensure_directory(target)
    assert target.is_dir()
    mtc.ensure_directory(target)  # idempotent
    assert target.is_dir()


def test_backup_existing_configs_copies_only_json(tmp_path, capsys):
    final = tmp_path / "final"
    _write(final / "a.json", {"a": 1})
    _write(final / "b.json", {"b": 2})
    (final / "notes.txt").write_text("skip me")
    backup = tmp_path / "backup" / "nested"
    assert mtc.backup_existing_configs(final, backup) is True
    assert sorted(p.name for p in backup.iterdir()) == ["a.json", "b.json"]
    assert _read(backup / "a.json") == {"a": 1}
    out = capsys.readouterr().out
    assert "Backed up a.json" in out and "Backed up b.json" in out


def test_backup_existing_configs_error_returns_false(tmp_path, monkeypatch, capsys):
    final = tmp_path / "final"
    _write(final / "a.json", {})

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(mtc.shutil, "copy2", boom)
    assert mtc.backup_existing_configs(final, tmp_path / "bk") is False
    assert "Error backing up configuration files: disk full" in capsys.readouterr().out


def test_process_config_file_without_existing_leaves_new(tmp_path, capsys):
    new = _write(tmp_path / "temp" / "t.json", {"value": 1})
    before = new.read_text()
    assert mtc.process_config_file(new, tmp_path / "final" / "t.json") is True
    assert new.read_text() == before
    assert "New template: t.json" in capsys.readouterr().out


def test_process_config_file_merges_in_place(tmp_path, capsys):
    new = _write(tmp_path / "temp" / "t.json", {"elements": [{"id": "a", "value": "new"}], "label": "Ünïcode"})
    old = _write(tmp_path / "final" / "t.json", {"elements": [{"id": "a", "value": "mine"}]})
    assert mtc.process_config_file(new, old) is True
    assert _read(new) == {"elements": [{"id": "a", "value": "mine"}], "label": "Ünïcode"}
    # ensure_ascii=False and indent=2
    text = new.read_text(encoding="utf-8")
    assert "Ünïcode" in text and '\n  "elements"' in text
    assert "Successfully merged t.json" in capsys.readouterr().out


@pytest.mark.parametrize("new_data,old_data", [(None, {"a": 1}), ({"a": 1}, None)])
def test_process_config_file_null_json_returns_false(tmp_path, capsys, new_data, old_data):
    new = _write(tmp_path / "temp" / "t.json", new_data)
    old = _write(tmp_path / "final" / "t.json", old_data)
    assert mtc.process_config_file(new, old) is False
    assert "Could not parse JSON" in capsys.readouterr().out


def test_process_config_file_invalid_json_returns_false(tmp_path, capsys):
    new = tmp_path / "temp" / "t.json"
    new.parent.mkdir()
    new.write_text("{broken", encoding="utf-8")
    old = _write(tmp_path / "final" / "t.json", {})
    assert mtc.process_config_file(new, old) is False
    assert "Error merging t.json" in capsys.readouterr().out
    assert new.read_text() == "{broken"


# --------------------------------------------------------------------------- cleanup


def test_cleanup_backup_missing_dir_is_noop(tmp_path, capsys):
    mtc.cleanup_backup(tmp_path / "missing", [])
    assert capsys.readouterr().out == ""


def test_cleanup_backup_no_errors_removes_dir(tmp_path, capsys):
    bk = tmp_path / "bk"
    _write(bk / "a.json", {})
    mtc.cleanup_backup(bk, [])
    assert not bk.exists()
    assert "Backup directory removed." in capsys.readouterr().out


def test_cleanup_backup_rmtree_failure_warns(tmp_path, monkeypatch, capsys):
    bk = tmp_path / "bk"
    bk.mkdir()
    monkeypatch.setattr(mtc.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("locked")))
    mtc.cleanup_backup(bk, [])
    assert bk.exists()
    assert "Could not remove backup directory: locked" in capsys.readouterr().out


def test_cleanup_backup_keeps_only_error_files(tmp_path, capsys):
    bk = tmp_path / "bk"
    for name in ("bad.json", "good.json", "other.json"):
        _write(bk / name, {})
    mtc.cleanup_backup(bk, ["bad.json"])
    assert sorted(p.name for p in bk.iterdir()) == ["bad.json"]
    assert "Keeping backups for files with errors: bad.json" in capsys.readouterr().out


def test_cleanup_backup_removes_dir_when_nothing_left(tmp_path):
    bk = tmp_path / "bk"
    _write(bk / "good.json", {})
    # Error file that has no backup copy: everything else is removed, then the empty dir
    mtc.cleanup_backup(bk, ["missing.json"])
    assert not bk.exists()


def test_cleanup_backup_error_during_partial_cleanup_warns(tmp_path, monkeypatch, capsys):
    bk = tmp_path / "bk"
    _write(bk / "good.json", {})

    def boom(self, *a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "unlink", boom)
    mtc.cleanup_backup(bk, ["bad.json"])
    monkeypatch.undo()
    assert (bk / "good.json").exists()
    assert "Error during backup cleanup: nope" in capsys.readouterr().out


def test_cleanup_temp_directory_removes(tmp_path, capsys):
    temp = tmp_path / "temp"
    _write(temp / "a.json", {})
    mtc.cleanup_temp_directory(temp)
    assert not temp.exists()
    assert "Temporary directory removed successfully." in capsys.readouterr().out


def test_cleanup_temp_directory_missing_is_quiet(tmp_path, capsys):
    mtc.cleanup_temp_directory(tmp_path / "nope")
    out = capsys.readouterr().out
    assert "Cleaning up temporary directory..." in out
    assert "removed successfully" not in out


def test_cleanup_temp_directory_failure_warns(tmp_path, monkeypatch, capsys):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(mtc.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("busy")))
    mtc.cleanup_temp_directory(temp)
    assert temp.exists()
    assert "Could not remove temporary directory: busy" in capsys.readouterr().out


def test__cleanup_temp_directory_keep(tmp_path, capsys):
    temp = tmp_path / "temp"
    temp.mkdir()
    mtc._cleanup_temp_directory(temp, keep_temp=True)
    assert temp.exists()
    assert "Keeping temporary directory." in capsys.readouterr().out


def test__cleanup_temp_directory_remove(tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    mtc._cleanup_temp_directory(temp, keep_temp=False)
    assert not temp.exists()


# --------------------------------------------------------------------------- main


@pytest.fixture
def layout(tmp_path):
    """Installer layout: <app>/templates/template_configs (final) and a temp folder."""
    app = tmp_path / "app"
    final = app / "templates" / "template_configs"
    temp = tmp_path / "temp_templates"
    temp.mkdir()
    return app, final, temp


def test_main_requires_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["merge_template_configs.py"])
    with pytest.raises(SystemExit) as exc:
        mtc.main()
    assert exc.value.code == 2
    assert "--temp-dir" in capsys.readouterr().err


def test_main_missing_temp_dir(monkeypatch, tmp_path, capsys):
    rc = _run_main(monkeypatch, "--temp-dir", tmp_path / "nope", "--final-dir", tmp_path / "final")
    assert rc == 1
    assert "Temporary templates directory not found" in capsys.readouterr().out
    assert not (tmp_path / "final").exists()


def test_main_fresh_install_copies_all(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(temp / "a.json", {"value": 1})
    _write(temp / "b.json", {"value": 2})
    (temp / "readme.txt").write_text("not json")
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 0
    assert sorted(p.name for p in final.iterdir()) == ["a.json", "b.json"]
    assert not temp.exists()
    out = capsys.readouterr().out
    assert "Copied a.json" in out and "migration complete" in out


def test_main_fresh_install_keep_temp(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(temp / "a.json", {"value": 1})
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final, "--keep-temp")
    assert rc == 0
    assert (temp / "a.json").exists() and (final / "a.json").exists()
    assert "Keeping temporary directory." in capsys.readouterr().out


def test_main_fresh_install_copy_error(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(temp / "a.json", {})
    monkeypatch.setattr(mtc.shutil, "copy2", lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")))
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 1
    assert "Error copying files: denied" in capsys.readouterr().out
    assert temp.exists()


def test_main_existing_install_merges_and_cleans_up(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "a.json", {"elements": [{"id": "x", "value": "user"}, {"id": "old", "value": 1}]})
    _write(final / "legacy.json", {"value": "kept in place"})
    _write(temp / "a.json", {"elements": [{"id": "x", "value": "default"}, {"id": "new", "value": 2}]})
    _write(temp / "b.json", {"value": "brand new"})
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 0
    assert _read(final / "a.json") == {"elements": [{"id": "x", "value": "user"}, {"id": "new", "value": 2}]}
    assert _read(final / "b.json") == {"value": "brand new"}
    assert _read(final / "legacy.json") == {"value": "kept in place"}
    # Backup lived at <app>/template_configs_backup and was removed after success
    assert not (app / "template_configs_backup").exists()
    assert not temp.exists()
    out = capsys.readouterr().out
    assert "Processed 2/2 files successfully." in out
    assert "Template configuration migration complete." in out


def test_main_existing_install_backup_failure_aborts(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "a.json", {})
    _write(temp / "a.json", {})
    monkeypatch.setattr(mtc, "backup_existing_configs", lambda *_a: False)
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 1
    assert "Failed to back up existing configs" in capsys.readouterr().out
    assert temp.exists()


def test_main_existing_install_all_fail_aborts_and_keeps_backup(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "a.json", {"value": "user"})
    (temp / "a.json").write_text("{not json", encoding="utf-8")
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 1
    out = capsys.readouterr().out
    assert "No files were processed successfully" in out
    assert _read(app / "template_configs_backup" / "a.json") == {"value": "user"}
    # Final file untouched
    assert _read(final / "a.json") == {"value": "user"}


def test_main_existing_install_partial_errors_keep_error_backups(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "bad.json", {"value": "user bad"})
    _write(final / "good.json", {"value": "user good"})
    (temp / "bad.json").write_text("{nope", encoding="utf-8")
    _write(temp / "good.json", {"value": "default"})
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final, "--keep-temp")
    assert rc == 0
    backup = app / "template_configs_backup"
    assert sorted(p.name for p in backup.iterdir()) == ["bad.json"]
    assert _read(final / "good.json") == {"value": "user good"}
    assert temp.exists()
    out = capsys.readouterr().out
    assert "Migration complete with errors on 1 file(s)" in out


def test_main_existing_install_no_new_files(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "a.json", {"value": 1})
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 0
    assert "Processed 0/0 files successfully." in capsys.readouterr().out
    assert _read(final / "a.json") == {"value": 1}


def test_main_existing_install_install_step_error(monkeypatch, layout, capsys):
    app, final, temp = layout
    _write(final / "a.json", {"value": 1})
    _write(temp / "a.json", {"value": 2})
    real_copy = shutil.copy2

    def copy(src, dest, *a, **k):
        # Backups (into template_configs_backup) work; installing into final fails.
        if Path(dest).parent == final:
            raise OSError("final is read-only")
        return real_copy(src, dest, *a, **k)

    monkeypatch.setattr(mtc.shutil, "copy2", copy)
    rc = _run_main(monkeypatch, "--temp-dir", temp, "--final-dir", final)
    assert rc == 1
    out = capsys.readouterr().out
    assert "Error moving files to final directory: final is read-only" in out
    assert (app / "template_configs_backup" / "a.json").exists()


def test_script_entry_point_exits_with_main_code(monkeypatch, tmp_path):
    import runpy

    monkeypatch.setattr(sys, "argv", ["merge_template_configs.py", "--temp-dir", str(tmp_path / "x"), "--final-dir", str(tmp_path / "y")])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / "merge_template_configs.py"), run_name="__main__")
    assert exc.value.code == 1


# --------------------------------------------------------------------------- update / downgrade semantics

_OLDER = {
    "template_name": "demo",
    "elements": [
        {"type": "separator", "label": "Font"},
        {"id": "font", "type": "select", "label": "Font", "options": ["A", "B"], "value": "A"},
        {"id": "size", "type": "number", "label": "Size", "value": 20},
        {"id": "retired", "type": "text", "label": "Retired", "value": "x"},
        {"id": "bg", "type": "color", "label": "Background", "value": "#000000", "transparent": False},
    ],
    "connector_actions": {"bump": {"action_name": "Bump", "elements": [{"id": "step", "value": 1}]}},
}

_NEWER = {
    "template_name": "demo",
    "elements": [
        {"type": "separator", "label": "Font settings"},
        {"id": "font", "type": "select", "label": "Typeface", "options": ["A", "B", "C"], "value": "C"},
        {"id": "size", "type": "number", "label": "Size", "value": 24, "max": 200},
        {"id": "bg", "type": "color", "label": "Background", "value": "#111111", "transparent": False},
        {"id": "shadow", "type": "checkbox", "label": "Shadow", "value": True},
    ],
    "connector_actions": {
        "bump": {"action_name": "Bump it", "elements": [{"id": "step", "value": 5}]},
        "reset": {"action_name": "Reset", "elements": [{"id": "to", "value": 0}]},
    },
    "streamdeck_options": {"actions": {}},
}


def _user(config, **values):
    user = json.loads(json.dumps(config))
    for el in user["elements"]:
        if el.get("id") in values:
            el["value"] = values[el["id"]]
    return user


def _by_id(config):
    return {el["id"]: el for el in config["elements"] if "id" in el}


def test_update_adds_new_settings_drops_removed_and_keeps_user_values():
    installed = _user(_OLDER, font="B", size=31, retired="mine", bg="rgba(0,0,0,0)")
    _by_id(installed)["bg"]["transparent"] = True
    installed["connector_actions"]["bump"]["elements"][0]["value"] = 9

    merged = mtc.merge_config(_NEWER, installed)
    els = _by_id(merged)
    assert els["font"]["value"] == "B" and els["font"]["label"] == "Typeface"  # value kept, metadata new
    assert els["font"]["options"] == ["A", "B", "C"]
    assert els["size"]["value"] == 31 and els["size"]["max"] == 200
    assert els["bg"]["value"] == "rgba(0,0,0,0)" and els["bg"]["transparent"] is True
    assert els["shadow"]["value"] is True  # added with its default
    assert "retired" not in els  # removed setting is gone
    assert merged["elements"][0] == {"type": "separator", "label": "Font settings"}
    assert merged["connector_actions"]["bump"] == {"action_name": "Bump it", "elements": [{"id": "step", "value": 9}]}
    assert merged["connector_actions"]["reset"] == _NEWER["connector_actions"]["reset"]
    assert merged["streamdeck_options"] == {"actions": {}}


def test_downgrade_merges_newer_user_file_into_older_structure():
    installed = _user(_NEWER, font="C", size=48, shadow=False)
    installed["connector_actions"]["reset"]["elements"][0]["value"] = 7

    merged = mtc.merge_config(_OLDER, installed)
    els = _by_id(merged)
    assert [el.get("id") for el in merged["elements"]] == [None, "font", "size", "retired", "bg"]
    assert els["font"]["value"] == "C"  # kept even though the older options list lacks it
    assert els["font"]["options"] == ["A", "B"]
    assert els["size"]["value"] == 48 and "max" not in els["size"]
    assert els["retired"]["value"] == "x"  # comes back with the older default
    assert "shadow" not in els
    assert set(merged["connector_actions"]) == {"bump"}
    assert "streamdeck_options" not in merged


def test_round_trip_through_a_downgrade_keeps_shared_values():
    installed = _user(_NEWER, font="A", size=12)
    down = mtc.merge_config(_OLDER, installed)
    up = mtc.merge_config(_NEWER, down)
    assert _by_id(up)["font"]["value"] == "A" and _by_id(up)["size"]["value"] == 12
    assert _by_id(up)["shadow"]["value"] is True  # newer-only value returns as the default


@pytest.mark.parametrize("path", sorted((ROOT / "templates" / "template_configs").glob("*.json")), ids=lambda p: p.name)
def test_every_shipped_template_keeps_every_user_value(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    marker = "__user__"

    def mark(node):
        if isinstance(node, dict):
            return {k: (marker if k == "value" else mark(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [mark(v) for v in node]
        return node

    def unmarked(node):
        if isinstance(node, dict):
            return any((k == "value" and v != marker) or (k != "value" and unmarked(v)) for k, v in node.items())
        if isinstance(node, list):
            return any(unmarked(v) for v in node)
        return False

    assert not unmarked(mtc.merge_config(config, mark(config)))

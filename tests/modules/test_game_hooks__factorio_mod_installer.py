# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/factorio_mod_installer.py (installs only into tmp_path)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import factorio_mod_installer as inst  # noqa: E402


@pytest.fixture(autouse=True)
def _no_process_scan(monkeypatch):
    """Never resolve the real Factorio folder: process scans and data-dir lookups are faked."""
    monkeypatch.setattr(inst, "process_exe_path", lambda pid=None: None)
    monkeypatch.setattr(inst, "resolve_data_dir", lambda *a, **k: None)


def _bundle(tmp_path: Path, version="1.2.3", name="bundle") -> Path:
    b = tmp_path / name / inst.MOD_NAME
    b.mkdir(parents=True)
    (b / "info.json").write_text(json.dumps({"name": inst.MOD_NAME, "version": version}), encoding="utf-8")
    (b / "control.lua").write_text("-- lua", encoding="utf-8")
    return b


# --------------------------------------------------------------------------- bundled mod


def test_bundled_mod_dir_prefers_data_path(monkeypatch, tmp_path):
    data = _bundle(tmp_path, name="data")
    res = _bundle(tmp_path, name="res")
    monkeypatch.setattr(inst, "get_data_path", lambda rel: str(tmp_path / "data" / Path(rel).name))
    monkeypatch.setattr(inst, "get_resource_path", lambda rel: str(tmp_path / "res" / Path(rel).name))
    assert inst.bundled_mod_dir() == data
    shutil.rmtree(data)
    assert inst.bundled_mod_dir() == res


def test_bundled_mod_dir_repo_fallback_and_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "get_data_path", lambda rel: str(tmp_path / "none1"))
    monkeypatch.setattr(inst, "get_resource_path", lambda rel: str(tmp_path / "none2"))
    repo = Path(inst.__file__).resolve().parents[2] / "factorio_mod" / inst.MOD_NAME
    assert inst.bundled_mod_dir() == repo
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: False if self.name == "info.json" else real_is_file(self))
    assert inst.bundled_mod_dir() is None
    assert inst.bundled_version() == ""


def test_read_info_version(tmp_path):
    p = tmp_path / "info.json"
    assert inst.read_info_version(p) == ""
    p.write_text("{bad", encoding="utf-8")
    assert inst.read_info_version(p) == ""
    p.write_text("[1, 2]", encoding="utf-8")
    assert inst.read_info_version(p) == ""
    p.write_text(json.dumps({"version": " 2.0.1 "}), encoding="utf-8")
    assert inst.read_info_version(p) == "2.0.1"
    p.write_text(json.dumps({"version": None}), encoding="utf-8")
    assert inst.read_info_version(p) == ""


def test_bundled_version(monkeypatch, tmp_path):
    b = _bundle(tmp_path, "9.9.9")
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    assert inst.bundled_version() == "9.9.9"


# --------------------------------------------------------------------------- mods dir & installed version


def test_mods_dir_for(monkeypatch, tmp_path):
    assert inst.mods_dir_for(str(tmp_path)) == tmp_path / "mods"
    assert inst.mods_dir_for() is None
    seen = []
    monkeypatch.setattr(inst, "process_exe_path", lambda pid=None: "/x/factorio")
    monkeypatch.setattr(inst, "resolve_data_dir", lambda exe_path=None: seen.append(exe_path) or str(tmp_path))
    assert inst.mods_dir_for() == tmp_path / "mods"
    assert seen == ["/x/factorio"]


def test_installed_entries(tmp_path):
    mods = tmp_path / "mods"
    assert inst._installed_entries(mods) == []
    mods.mkdir()
    for n in (inst.MOD_NAME, inst.MOD_NAME + "_1.0.0.zip", "other_1.0", inst.MOD_NAME + "-extra"):
        (mods / n).mkdir()
    names = sorted(p.name for p in inst._installed_entries(mods))
    assert names == [inst.MOD_NAME, inst.MOD_NAME + "_1.0.0.zip"]


def test_installed_version_variants(monkeypatch, tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    assert inst.installed_version(mods) == ""
    (mods / f"{inst.MOD_NAME}_1.0.1.zip").write_bytes(b"PK")
    assert inst.installed_version(mods) == "1.0.1"
    (mods / f"{inst.MOD_NAME}_1.0.1.zip").unlink()
    folder = mods / f"{inst.MOD_NAME}_0.9.0"
    folder.mkdir()
    assert inst.installed_version(mods) == "0.9.0"  # name fallback (no info.json)
    (folder / "info.json").write_text(json.dumps({"version": "0.9.5"}), encoding="utf-8")
    assert inst.installed_version(mods) == "0.9.5"
    (folder / "info.json").write_text(json.dumps({}), encoding="utf-8")
    assert inst.installed_version(mods) == "0.9.0"
    shutil.rmtree(folder)
    (mods / inst.MOD_NAME).mkdir()  # unversioned folder, no info
    assert inst.installed_version(mods) == ""


def test_installed_version_default_lookup(monkeypatch, tmp_path):
    assert inst.installed_version() == ""  # mods_dir_for -> None
    monkeypatch.setattr(inst, "mods_dir_for", lambda data_dir=None: tmp_path / "mods")
    (tmp_path / "mods" / f"{inst.MOD_NAME}_3.0.0").mkdir(parents=True)
    assert inst.installed_version() == "3.0.0"


@pytest.mark.parametrize("want,have,state", [("1", "", "not_installed"), ("1", "0", "outdated"),
                                             ("1", "1", "current"), ("", "0", "current")])
def test_state_for(want, have, state):
    assert inst._state_for(want, have) == state


def test_detect_state_and_install_status(monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "bundled_version", lambda: "2.0.0")
    monkeypatch.setattr(inst, "mods_dir_for", lambda data_dir=None: tmp_path / "mods")
    assert inst.detect_state() == "not_installed"
    assert inst.install_status() == ("not_installed", "not installed · bundled 2.0.0", "muted")
    (tmp_path / "mods" / f"{inst.MOD_NAME}_1.0.0").mkdir(parents=True)
    assert inst.detect_state(str(tmp_path)) == "outdated"
    assert inst.install_status() == ("outdated", "v1.0.0  ·  new version 2.0.0", "warning")


def test_version_label_defaults(monkeypatch):
    monkeypatch.setattr(inst, "installed_version", lambda mods_dir=None: " 1.0 ")
    monkeypatch.setattr(inst, "bundled_version", lambda: "1.0")
    monkeypatch.setattr(inst, "detect_state", lambda data_dir=None: "current")
    assert inst.version_label() == ("v1.0", "ok")
    assert inst.version_label(installed="", bundled="", state="current") == ("not installed", "muted")
    assert inst.version_label(installed="1", bundled="", state="outdated") == ("v1", "warning")


# --------------------------------------------------------------------------- mod-list.json


def test_merge_mod_list_edge_cases():
    assert inst.merge_mod_list(None) == {"mods": [{"name": inst.MOD_NAME, "enabled": True}]}
    assert inst.merge_mod_list({"mods": "bad"}, "x", False) == {"mods": [{"name": "x", "enabled": False}]}
    data = {"mods": ["junk", {"name": None}, {"name": "x", "enabled": True}]}
    assert inst.merge_mod_list(data, "x", 0)["mods"][2] == {"name": "x", "enabled": False}


def test_atomic_write_json(tmp_path):
    p = tmp_path / "a" / "b" / "mod-list.json"
    inst._atomic_write_json(p, {"k": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": 1}
    assert p.read_text(encoding="utf-8").endswith("\n")
    assert not (p.parent / "mod-list.json.tmp").exists()


def test_enable_in_mod_list(tmp_path, caplog):
    mods = tmp_path / "mods"
    inst._enable_in_mod_list(mods)
    assert json.loads((mods / "mod-list.json").read_text())["mods"] == [{"name": inst.MOD_NAME, "enabled": True}]
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True}]}))
    inst._enable_in_mod_list(mods)
    assert [m["name"] for m in json.loads((mods / "mod-list.json").read_text())["mods"]] == ["base", inst.MOD_NAME]
    (mods / "mod-list.json").write_text("{corrupt")
    inst._enable_in_mod_list(mods)
    assert "Could not parse" in caplog.text
    assert json.loads((mods / "mod-list.json").read_text())["mods"] == [{"name": inst.MOD_NAME, "enabled": True}]


# --------------------------------------------------------------------------- install_mod


def test_install_missing_bundle(monkeypatch):
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: None)
    ok, msg = inst.install_mod("/nowhere")
    assert not ok and "missing" in msg


def test_install_no_data_dir(monkeypatch, tmp_path):
    b = _bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    ok, msg = inst.install_mod()
    assert (ok, msg) == (False, "Factorio user-data folder not found")


def test_install_mkdir_fails(monkeypatch, tmp_path):
    b = _bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    blocker = tmp_path / "data"
    blocker.write_text("file, not dir")
    ok, msg = inst.install_mod(str(blocker))
    assert not ok and msg.startswith("Could not create mods folder")


def test_install_fresh_replaces_old_and_enables(monkeypatch, tmp_path):
    b = _bundle(tmp_path, "1.2.3")
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    data = tmp_path / "data"
    mods = data / "mods"
    mods.mkdir(parents=True)
    (mods / f"{inst.MOD_NAME}_1.0.0").mkdir()
    (mods / f"{inst.MOD_NAME}_0.9.0.zip").write_bytes(b"PK")
    (mods / "other_1.0.zip").write_bytes(b"PK")
    ok, msg = inst.install_mod(str(data))
    assert ok and "1.2.3" in msg and "Restart Factorio" in msg
    names = sorted(p.name for p in mods.iterdir())
    assert names == ["mod-list.json", f"{inst.MOD_NAME}_1.2.3", "other_1.0.zip"]
    assert (mods / f"{inst.MOD_NAME}_1.2.3" / "control.lua").read_text() == "-- lua"
    assert inst.installed_version(mods) == "1.2.3"


def test_install_overwrites_same_version(monkeypatch, tmp_path):
    b = _bundle(tmp_path, "1.2.3")
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    dest = tmp_path / "data" / "mods" / f"{inst.MOD_NAME}_1.2.3"
    dest.mkdir(parents=True)
    (dest / "stale.lua").write_text("old")
    ok, _ = inst.install_mod(str(tmp_path / "data"))
    assert ok and not (dest / "stale.lua").exists() and (dest / "control.lua").exists()


def test_install_default_version_and_resolved_dir(monkeypatch, tmp_path):
    b = _bundle(tmp_path)
    (b / "info.json").write_text("{}")
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    monkeypatch.setattr(inst, "resolve_data_dir", lambda exe_path=None: str(tmp_path / "data"))
    ok, msg = inst.install_mod()
    assert ok and "1.0.0" in msg
    assert (tmp_path / "data" / "mods" / f"{inst.MOD_NAME}_1.0.0").is_dir()


def test_install_copy_error(monkeypatch, tmp_path, caplog):
    b = _bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(inst.shutil, "copytree", boom)
    ok, msg = inst.install_mod(str(tmp_path / "data"))
    assert (ok, msg) == (False, "Could not install mod: disk full")


# --------------------------------------------------------------------------- Factorio version


def _game_log(data: Path, version: str) -> None:
    data.mkdir(parents=True, exist_ok=True)
    (data / "factorio-current.log").write_text(
        f"   0.000 2026-10-29 19:39:30; Factorio {version} (build 87808, win64, steam, space-age)\n"
        "   0.000 Operating system: Windows 11\n",
        encoding="utf-8",
    )


def _versioned_bundle(tmp_path: Path, factorio="2.0", version="1.0.4") -> Path:
    b = _bundle(tmp_path, version)
    info = {"name": inst.MOD_NAME, "version": version, "factorio_version": factorio}
    (b / "info.json").write_text(json.dumps(info), encoding="utf-8")
    return b


def test_game_version_from_log(tmp_path):
    assert inst.game_version(None) == ""
    assert inst.game_version(tmp_path) == ""  # no log
    (tmp_path / "factorio-current.log").write_text("nothing useful\n", encoding="utf-8")
    assert inst.game_version(tmp_path) == ""
    _game_log(tmp_path, "2.1.24")
    assert inst.game_version(tmp_path) == "2.1"


@pytest.mark.parametrize("bundled,game,target", [
    ("2.0", "2.1", "2.1"),  # newer game: match it so it loads
    ("2.0", "2.0", "2.0"),
    ("2.0", "1.1", "2.0"),  # older game: the mod needs the 2.0 API
    ("2.0", "", "2.0"),
    ("", "2.1", ""),
    ("2.0", "2.10", "2.10"),  # compared as numbers, not text
])
def test_target_factorio_version(bundled, game, target):
    assert inst.target_factorio_version(bundled, game) == target


def test_install_matches_a_newer_game(monkeypatch, tmp_path):
    """Factorio 2.1 refuses a mod declaring factorio_version 2.0."""
    b = _versioned_bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    data = tmp_path / "data"
    _game_log(data, "2.1.24")
    ok, _ = inst.install_mod(str(data))
    installed = json.loads((data / "mods" / f"{inst.MOD_NAME}_1.0.4" / "info.json").read_text(encoding="utf-8"))
    assert ok and installed["factorio_version"] == "2.1" and installed["version"] == "1.0.4"
    assert json.loads((b / "info.json").read_text(encoding="utf-8"))["factorio_version"] == "2.0"  # bundle untouched


def test_install_keeps_bundled_version_for_same_or_unknown_game(monkeypatch, tmp_path):
    b = _versioned_bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    data = tmp_path / "data"
    inst.install_mod(str(data))  # no log yet
    info = data / "mods" / f"{inst.MOD_NAME}_1.0.4" / "info.json"
    assert inst.read_info_factorio_version(info) == "2.0"
    _game_log(data, "2.0.72")
    inst.install_mod(str(data))
    assert inst.read_info_factorio_version(info) == "2.0"


def test_status_flags_a_copy_built_for_another_factorio(monkeypatch, tmp_path):
    """Same mod version, but the game moved to 2.1: offer Update instead of 'Mod installed'."""
    b = _versioned_bundle(tmp_path)
    monkeypatch.setattr(inst, "bundled_mod_dir", lambda: b)
    data = tmp_path / "data"
    monkeypatch.setattr(inst, "mods_dir_for", lambda data_dir=None: data / "mods")
    _game_log(data, "2.0.72")
    inst.install_mod(str(data))
    assert inst.install_status() == ("current", "v1.0.4", "ok")
    _game_log(data, "2.1.24")
    assert inst.install_status() == ("outdated", "v1.0.4  ·  update for Factorio 2.1", "warning")
    assert inst.detect_state() == "outdated"
    inst.install_mod(str(data))
    assert inst.install_status() == ("current", "v1.0.4", "ok")


def test_installed_factorio_version_ignores_zips_and_missing(tmp_path):
    mods = tmp_path / "mods"
    assert inst.installed_factorio_version(None) == ""
    mods.mkdir()
    (mods / f"{inst.MOD_NAME}_1.0.0.zip").write_bytes(b"PK")
    assert inst.installed_factorio_version(mods) == ""
    folder = mods / f"{inst.MOD_NAME}_1.0.4"
    folder.mkdir()
    (folder / "info.json").write_text(json.dumps({"factorio_version": "2.1"}), encoding="utf-8")
    assert inst.installed_factorio_version(mods) == "2.1"

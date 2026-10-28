# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.path_utils (dev/frozen path resolution, data-dir override, file reveal)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import path_utils as pu


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "Mycelian.exe"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return exe_dir


def test_resource_path_dev_uses_cwd(dev, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert pu.get_resource_path("x/y.png") == os.path.join(str(tmp_path), "x/y.png")


def test_resource_path_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert pu.get_resource_path("a.txt") == os.path.join(str(tmp_path), "a.txt")


def test_install_dir_dev_is_repo_root(dev):
    assert pu._install_dir() == str(ROOT)


def test_install_dir_frozen_is_exe_dir(frozen):
    assert pu._install_dir() == str(frozen)


def test_data_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert pu.get_data_path("config.json") == os.path.join(str(tmp_path), "config.json")


def test_data_path_without_override_dev(monkeypatch, dev):
    monkeypatch.delenv("MYCELIAN_DATA_DIR", raising=False)
    assert pu.get_data_path("x.db") == os.path.join(str(ROOT), "x.db")
    monkeypatch.setenv("MYCELIAN_DATA_DIR", "")
    assert pu.get_data_path("x.db") == os.path.join(str(ROOT), "x.db")


def test_data_path_without_override_frozen(monkeypatch, frozen):
    monkeypatch.delenv("MYCELIAN_DATA_DIR", raising=False)
    assert pu.get_data_path("x.db") == os.path.join(str(frozen), "x.db")


def test_template_and_static_paths_dev(dev):
    assert pu.get_template_path() == os.path.join(str(ROOT), "templates")
    assert pu.get_template_path("a.html") == os.path.join(str(ROOT), "templates", "a.html")
    assert pu.get_static_path() == os.path.join(str(ROOT), "static")
    assert pu.get_static_path("s.css") == os.path.join(str(ROOT), "static", "s.css")


def test_template_path_frozen(frozen):
    assert pu.get_template_path("t") == os.path.join(str(frozen), "templates", "t")


def test_assets_path_dev(dev):
    assert pu.get_assets_path() == os.path.join(str(ROOT), "assets")
    assert pu.get_assets_path("i.png") == os.path.join(str(ROOT), "assets", "i.png")


def test_assets_path_frozen_prefers_exe_assets(frozen, monkeypatch, tmp_path):
    (frozen / "assets").mkdir()
    (frozen / "assets" / "i.png").write_bytes(b"")
    bundle = tmp_path / "meipass"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "i.png").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert pu.get_assets_path("i.png") == os.path.join(str(frozen), "assets", "i.png")
    assert pu.get_assets_path() == os.path.join(str(frozen), "assets")


def test_assets_path_frozen_falls_back_to_bundle(frozen, monkeypatch, tmp_path):
    (frozen / "assets").mkdir()  # exists but lacks the file
    bundle = tmp_path / "meipass"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "only.png").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert pu.get_assets_path("only.png") == os.path.join(str(bundle), "assets", "only.png")


def test_assets_path_frozen_missing_everywhere(frozen):
    # No exe assets dir, no _MEIPASS: falls through to install dir join.
    assert pu.get_assets_path("nope.png") == os.path.join(str(frozen), "assets", "nope.png")
    assert pu.get_assets_path() == os.path.join(str(frozen), "assets")


def test_ensure_directory_exists(tmp_path):
    target = tmp_path / "a" / "b"
    assert pu.ensure_directory_exists(str(target)) is True
    assert target.is_dir()
    assert pu.ensure_directory_exists(target) is True  # already exists


def test_ensure_directory_exists_failure(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert pu.ensure_directory_exists(str(f / "child")) is False


def test_is_frozen_and_dirs_dev(dev, monkeypatch, tmp_path):
    assert pu.is_frozen() is False
    assert pu.get_executable_dir() == str(ROOT)
    monkeypatch.chdir(tmp_path)
    assert pu.get_working_directory() == os.getcwd()


def test_is_frozen_and_dirs_frozen(frozen):
    assert pu.is_frozen() is True
    assert pu.get_executable_dir() == str(frozen)
    assert pu.get_working_directory() == str(frozen)


class RunRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))


@pytest.mark.parametrize(
    "platform, cmd0, check",
    [("darwin", "open", True), ("win32", "explorer", False), ("linux", "xdg-open", True)],
)
def test_reveal_in_file_manager_per_platform(monkeypatch, tmp_path, platform, cmd0, check):
    rec = RunRecorder()
    monkeypatch.setattr(pu.subprocess, "run", rec)
    monkeypatch.setattr(pu.sys, "platform", platform)
    pu.reveal_in_file_manager(tmp_path)
    assert rec.calls == [([cmd0, str(tmp_path.resolve())], {"check": check})]


def test_reveal_in_file_manager_missing_path(monkeypatch, tmp_path):
    rec = RunRecorder()
    monkeypatch.setattr(pu.subprocess, "run", rec)
    with pytest.raises(FileNotFoundError):
        pu.reveal_in_file_manager(tmp_path / "missing")
    assert rec.calls == []


def test_reveal_in_file_manager_unsupported_platform(monkeypatch, tmp_path):
    rec = RunRecorder()
    monkeypatch.setattr(pu.subprocess, "run", rec)
    monkeypatch.setattr(pu.sys, "platform", "sunos5")
    with pytest.raises(OSError, match="Unsupported platform"):
        pu.reveal_in_file_manager(str(tmp_path))
    assert rec.calls == []

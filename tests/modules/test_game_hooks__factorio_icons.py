# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/factorio_icons.py (game data root, icon index, PNG crop cache)."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import factorio_icons as fi  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(fi, "_index_cache", {})
    monkeypatch.setattr(fi, "_index_built_at", 0.0)
    monkeypatch.setattr(fi, "_index_root", None)
    monkeypatch.setattr(fi, "_png_cache", {})
    monkeypatch.setattr(fi, "find_factorio_pid", lambda: None)
    monkeypatch.setattr(fi, "process_exe_path", lambda pid=None: None)
    monkeypatch.setattr(fi, "resolve_data_dir", lambda *a, **k: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(fi.os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    return home


def _png(path: Path, size=(32, 64), color=(255, 0, 0, 255)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


def _data_root(base: Path) -> Path:
    (base / "base").mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------- resolve_game_data_root


def test_root_none_when_nothing_found():
    assert fi.resolve_game_data_root() is None


def test_root_uses_process_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(fi, "find_factorio_pid", lambda: 5)
    seen = []
    monkeypatch.setattr(fi, "process_exe_path", lambda pid=None: seen.append(pid) or str(tmp_path / "g" / "bin" / "x64" / "factorio"))
    _data_root(tmp_path / "g" / "data")
    assert fi.resolve_game_data_root() == str(tmp_path / "g" / "data")
    assert seen == [5]


def test_root_mac_bundle(tmp_path):
    exe = tmp_path / "factorio.app" / "Contents" / "MacOS" / "factorio"
    _data_root(tmp_path / "factorio.app" / "Contents" / "data")
    assert fi.resolve_game_data_root(str(exe)) == str(tmp_path / "factorio.app" / "Contents" / "data")


def test_root_from_log_read_path(monkeypatch, tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    game = _data_root(tmp_path / "install" / "data")
    (user / "factorio-current.log").write_text(f"Read data path: {game}\n")
    monkeypatch.setattr(fi, "resolve_data_dir", lambda exe_path=None: str(user))
    assert fi.resolve_game_data_root("/nowhere/bin/factorio") == str(game)


def test_root_log_without_path_and_user_data_fallback(monkeypatch, tmp_path):
    user = tmp_path / "user"
    _data_root(user / "data")
    (user / "factorio-current.log").write_text("nothing useful\n")
    monkeypatch.setattr(fi, "resolve_data_dir", lambda exe_path=None: str(user))
    assert fi.resolve_game_data_root("/nowhere/factorio") == str(user / "data")


def test_root_log_unreadable(monkeypatch, tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    (user / "factorio-current.log").write_text("x")
    monkeypatch.setattr(fi, "resolve_data_dir", lambda exe_path=None: str(user))
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("factorio-current.log"):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert fi.resolve_game_data_root("/nowhere/factorio") is None


def test_root_steam_home_paths(_isolate):
    steam_linux = _data_root(_isolate / ".steam" / "steam" / "steamapps" / "common" / "Factorio" / "data")
    assert fi.resolve_game_data_root() == str(steam_linux)
    steam_mac = _data_root(_isolate / "Library" / "Application Support" / "Steam" / "steamapps" / "common"
                           / "Factorio" / "factorio.app" / "Contents" / "data")
    assert fi.resolve_game_data_root() == str(steam_mac)


# --------------------------------------------------------------------------- index


def test_scan_index(tmp_path):
    root = tmp_path / "data"
    _png(root / "base" / "graphics" / "icons" / "iron_plate.png")
    _png(root / "space-age" / "graphics" / "icons" / "iron_plate.png")
    _png(root / "space-age" / "graphics" / "icons" / "fluid" / "lava.png")
    _png(root / "core" / "graphics" / "entity" / "thing.PNG")
    (root / "base" / "graphics" / "icons" / "readme.txt").write_text("x")
    (root / "quality").mkdir(parents=True)  # pack without icon dirs
    idx = fi._scan_index(str(root))
    assert idx["iron_plate"] == str(root / "base" / "graphics" / "icons" / "iron_plate.png")
    assert idx["iron-plate"] == idx["iron_plate"]
    assert "lava" in idx and "thing" in idx and "readme" not in idx


def test_scan_index_listdir_error(monkeypatch, tmp_path):
    root = tmp_path / "data"
    (root / "base" / "graphics" / "icons").mkdir(parents=True)

    def boom(path):
        raise OSError("perm")

    monkeypatch.setattr(fi.os, "listdir", boom)
    assert fi._scan_index(str(root)) == {}


def test_icon_index_caches_and_rebuilds(monkeypatch, tmp_path):
    root = str(tmp_path / "data")
    scans = []
    clock = {"t": 1000.0}
    monkeypatch.setattr(fi.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(fi, "resolve_game_data_root", lambda exe_path=None: root)
    monkeypatch.setattr(fi, "_scan_index", lambda r: scans.append(r) or {"a": "x"})
    assert fi.icon_index() == {"a": "x"}
    fi.icon_index()
    assert len(scans) == 1
    clock["t"] += fi._INDEX_TTL_SEC + 1
    fi.icon_index()
    assert len(scans) == 2
    fi.icon_index(force=True)
    assert len(scans) == 3
    monkeypatch.setattr(fi, "resolve_game_data_root", lambda exe_path=None: None)
    assert fi.icon_index() == {}
    assert fi._index_root is None


# --------------------------------------------------------------------------- icon_path_for


@pytest.fixture
def indexed(monkeypatch, tmp_path):
    root = tmp_path / "data"
    files = {
        "iron-plate": _png(root / "base" / "graphics" / "icons" / "iron-plate.png"),
        "starmap-planet-vulcanus": _png(root / "space-age" / "graphics" / "icons" / "starmap-planet-vulcanus.png"),
    }
    outside = _png(tmp_path / "outside.png")
    index = {k: str(v) for k, v in files.items()}
    index["escape"] = str(outside)
    index["ghost"] = str(root / "base" / "ghost.png")
    monkeypatch.setattr(fi, "resolve_game_data_root", lambda exe_path=None: str(root))
    monkeypatch.setattr(fi, "_scan_index", lambda r: dict(index))
    return root, files


def test_icon_path_for(indexed):
    root, files = indexed
    assert fi.icon_path_for("") is None
    assert fi.icon_path_for("../etc/passwd") is None
    assert fi.icon_path_for("iron-plate") == str(files["iron-plate"])
    assert fi.icon_path_for("iron_plate") == str(files["iron-plate"])
    assert fi.icon_path_for("vulcanus") == str(files["starmap-planet-vulcanus"])
    assert fi.icon_path_for("starmap-planet-nope") is None
    assert fi.icon_path_for("unknown") is None
    assert fi.icon_path_for("escape") is None  # outside the data root
    assert fi.icon_path_for("ghost") is None  # indexed but file missing


def test_icon_path_commonpath_error(indexed, monkeypatch):
    def boom(paths):
        raise ValueError("different drives")

    monkeypatch.setattr(fi.os.path, "commonpath", boom)
    assert fi.icon_path_for("iron-plate") is None


# --------------------------------------------------------------------------- cropped_png_bytes


def test_cropped_png_crops_tall_mipmap(indexed):
    from PIL import Image

    data = fi.cropped_png_bytes("iron-plate")
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (32, 32)
    # cached by mtime
    path = str(indexed[1]["iron-plate"])
    assert fi._png_cache[path][1] is data
    assert fi.cropped_png_bytes("iron-plate") is data


def test_cropped_png_square_untouched_and_mtime_change(indexed):
    from PIL import Image

    root, files = indexed
    p = files["starmap-planet-vulcanus"]
    _png(p, size=(16, 16))
    first = fi.cropped_png_bytes("vulcanus")
    with Image.open(io.BytesIO(first)) as im:
        assert im.size == (16, 16)
    _png(p, size=(8, 8))
    os.utime(p, (5, 5))
    second = fi.cropped_png_bytes("vulcanus")
    with Image.open(io.BytesIO(second)) as im:
        assert im.size == (8, 8)


def test_cropped_png_missing_name():
    assert fi.cropped_png_bytes("nope") is None


def test_cropped_png_getmtime_error(indexed, monkeypatch):
    def boom(p):
        raise OSError("gone")

    monkeypatch.setattr(fi.os.path, "getmtime", boom)
    assert fi.cropped_png_bytes("iron-plate") is None


def test_cropped_png_invalid_image_falls_back_to_raw(indexed):
    root, files = indexed
    files["iron-plate"].write_bytes(b"not a png")
    assert fi.cropped_png_bytes("iron-plate") == b"not a png"


def test_cropped_png_invalid_and_unreadable(indexed, monkeypatch):
    root, files = indexed
    files["iron-plate"].write_bytes(b"junk")
    real_open = open

    def fake_open(path, *a, **k):
        if str(path) == str(files["iron-plate"]) and "rb" in a:
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert fi.cropped_png_bytes("iron-plate") is None


def test_png_cache_bounded(indexed, monkeypatch):
    for i in range(513):
        fi._png_cache[f"k{i}"] = (0.0, b"")
    fi.cropped_png_bytes("iron-plate")
    assert len(fi._png_cache) == 513
    assert "k0" not in fi._png_cache

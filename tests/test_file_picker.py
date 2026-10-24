# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared file picker: path mapping, listing, safe upload names, upload handling."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.uiwindows import file_picker as fp


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "assets"
    (r / "default_assets" / "sounds" / "random").mkdir(parents=True)
    (r / "default_assets" / "images").mkdir(parents=True)
    for name in ("b10.mp3", "b2.mp3", "a.wav", "cover.png", ".hidden.mp3"):
        (r / "default_assets" / "sounds" / name).write_bytes(b"x" * 10)
    (r / "default_assets" / "sounds" / "random" / "r.ogg").write_bytes(b"x")
    return r


def test_web_path_round_trip(root):
    f = root / "default_assets" / "sounds" / "a.wav"
    assert fp.to_web_path(f, root) == "/assets/default_assets/sounds/a.wav"
    assert fp.to_web_path(f.parent, root, is_dir=True) == "/assets/default_assets/sounds/"
    assert fp.to_web_path(root, root, is_dir=True) == "/assets/"
    assert fp.from_web_path("/assets/default_assets/sounds/a.wav", root) == f
    assert fp.from_web_path("assets/default_assets/", root) == root / "default_assets"
    assert fp.from_web_path("/assets/../../etc/passwd", root) is None
    assert fp.from_web_path("C:/somewhere/else.gif", root) is None


def test_is_inside_rejects_symlink_escape(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert fp.is_inside(root / "default_assets", root)
    assert not fp.is_inside(outside, root)
    if sys.platform != "win32":
        link = root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        assert not fp.is_inside(link, root)
        entries, _err = fp.list_entries(root, (".mp3",), root=root)
        assert "escape" not in [e.name for e in entries]


def test_list_entries_sorts_filters_and_counts(root):
    sounds = root / "default_assets" / "sounds"
    entries, err = fp.list_entries(sounds, (".mp3", ".wav", ".ogg"), root=root)
    assert err is None
    names = [e.name for e in entries]
    assert names == ["random", "a.wav", "b2.mp3", "b10.mp3", "cover.png"]  # folders first, natural order, hidden skipped
    usable = {e.name: e.usable for e in entries}
    assert usable == {"random": True, "a.wav": True, "b2.mp3": True, "b10.mp3": True, "cover.png": False}
    folder = entries[0]
    assert (folder.item_count, folder.usable_count) == (1, 1)
    missing, err = fp.list_entries(sounds / "nope", (".mp3",))
    assert missing == [] and err


def test_nearest_existing_dir_stays_inside_root(root, tmp_path):
    deep = root / "default_assets" / "sounds" / "gone" / "deeper"
    assert fp.nearest_existing_dir(deep, root, root=root) == root / "default_assets" / "sounds"
    assert fp.nearest_existing_dir(tmp_path / "elsewhere", root, root=root) == root
    assert fp.nearest_existing_dir(None, root) == root


@pytest.mark.parametrize(
    "name, existing, expected",
    [
        ("cheer.mp3", [], "cheer.mp3"),
        ("cheer.mp3", ["CHEER.mp3"], "cheer (2).mp3"),
        ("cheer.mp3", ["cheer.mp3", "cheer (2).mp3"], "cheer (3).mp3"),
        ("..\\..\\evil.gif", [], "evil.gif"),
        ("C:/Users/me/a:b?.png", [], "a_b_.png"),
        ("con.wav", [], "_con.wav"),
        ("   ", [], "file"),
        ("trailing.gif. ", [], "trailing.gif"),
    ],
)
def test_safe_upload_name(name, existing, expected):
    assert fp.safe_upload_name(name, existing) == expected


def test_breadcrumbs_in_assets_scope(root):
    folder = root / "default_assets" / "sounds"
    assert [label for label, _p in fp.breadcrumbs(folder, root=root)] == ["assets", "default_assets", "sounds"]
    assert fp.breadcrumbs(root, root=root) == [("assets", root)]


def test_format_size_and_types():
    assert fp.format_size(512) == "512 B"
    assert fp.format_size(1536) == "1.5 KB"
    assert fp.format_size(5 * 1024 * 1024) == "5 MB"
    assert fp.media_kind(".gif") == "image" and fp.media_kind(".ogg") == "audio" and fp.media_kind(".webm") == "video"
    assert fp.type_name(".db") == "SQLite database"


def test_is_temp_bundle(monkeypatch, tmp_path):
    assert not fp.is_temp_bundle(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert fp.is_temp_bundle(tmp_path / "assets")
    assert not fp.is_temp_bundle(tmp_path.parent / "elsewhere")


def test_assets_places_only_existing(root):
    places = fp.assets_places(root, [("x", "Bits", root / "bits")])
    assert [label for _i, label, _p in places] == ["Assets", "Default assets", "Images", "Sounds"]


def _picker(root, monkeypatch, extensions=(".mp3",)):
    monkeypatch.setattr(fp, "assets_root", lambda: root)
    cfg = fp._PickerConfig(title="t", subtitle="s", icon="i", extensions=extensions, mode="file",
                           scope="assets", kind="audio", on_pick=lambda r: None)
    picker = fp._FilePicker(cfg, "/assets/default_assets/sounds/b2.mp3")
    picker.upload = None
    loaded = []
    picker._load_folder = lambda folder, select=None: loaded.append((folder, select))
    monkeypatch.setattr(fp, "notify", lambda *a, **k: None)
    return picker, loaded


class _FakeUpload:
    def __init__(self, name, data=b"abc"):
        self.name, self._data = name, data

    async def save(self, path):
        Path(path).write_bytes(self._data)


def test_picker_starts_in_the_fields_folder_with_file_selected(root, monkeypatch):
    picker, _loaded = _picker(root, monkeypatch)
    assert picker.folder == root / "default_assets" / "sounds"
    assert picker.preselect == root / "default_assets" / "sounds" / "b2.mp3"


def test_upload_saves_without_overwriting_and_rejects_other_types(root, monkeypatch):
    picker, loaded = _picker(root, monkeypatch)
    sounds = root / "default_assets" / "sounds"
    asyncio.run(picker._on_upload(SimpleNamespace(file=_FakeUpload("b2.mp3", b"new"))))
    assert (sounds / "b2 (2).mp3").read_bytes() == b"new"
    assert (sounds / "b2.mp3").read_bytes() == b"x" * 10
    assert loaded[-1] == (sounds, sounds / "b2 (2).mp3")
    asyncio.run(picker._on_upload(SimpleNamespace(file=_FakeUpload("virus.exe"))))
    assert not (sounds / "virus.exe").exists()


def test_upload_refused_in_temporary_bundle(root, monkeypatch):
    picker, _loaded = _picker(root, monkeypatch)
    monkeypatch.setattr(fp, "is_temp_bundle", lambda _p: True)
    asyncio.run(picker._on_upload(SimpleNamespace(file=_FakeUpload("new.mp3"))))
    assert not (root / "default_assets" / "sounds" / "new.mp3").exists()


def test_result_carries_web_paths(root, monkeypatch):
    picker, _loaded = _picker(root, monkeypatch)
    entry = fp.Entry(path=root / "default_assets" / "sounds" / "a.wav", name="a.wav", is_dir=False, ext=".wav", usable=True)
    picker.selected = entry
    result = picker._result()
    assert (result.web_dir, result.filename, result.web_path) == (
        "/assets/default_assets/sounds/", "a.wav", "/assets/default_assets/sounds/a.wav")
    picker.selected = fp.Entry(path=entry.path, name="a.wav", is_dir=False, ext=".wav", usable=False)
    assert picker._result() is None


def test_system_places_are_existing_folders():
    places = fp.system_places()
    assert places and all(Path(p).is_dir() for _i, _l, p in places)
    assert places[0][1] == "Home"
    assert len({os.path.normcase(str(p)) for _i, _l, p in places}) == len(places)


def test_within_limits_results(root, monkeypatch):
    monkeypatch.setattr(fp, "assets_root", lambda: root)
    alerts = root / "alerts"
    (alerts / "bits" / "random").mkdir(parents=True)
    cfg = fp._PickerConfig(title="t", subtitle="s", icon="i", extensions=(".mp3",), mode="folder", scope="assets",
                           kind="audio", on_pick=lambda r: None, within=alerts, within_hint="must be in alerts")
    picker = fp._FilePicker(cfg, None)
    assert picker.folder == alerts  # empty field starts in the allowed folder
    picker.selected = fp.Entry(path=root / "default_assets" / "sounds" / "random", name="random", is_dir=True)
    assert picker._result() is None
    picker.selected = fp.Entry(path=alerts / "bits" / "random", name="random", is_dir=True)
    assert picker._result().web_path == "/assets/alerts/bits/random/"

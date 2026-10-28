# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/file_picker.py: path/listing helpers on every platform branch, and the
picker dialog itself (places, crumbs, list/grid, preview, fills, confirm, add files, notices).
All file operations happen in tmp_path; the native file dialog is faked."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import path_utils  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import file_picker as fp  # noqa: E402
from modules.uiwindows import media_preview  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# ----------------------------------------------------------------- fixtures

def _png(path: Path, size=(4, 3)):
    from PIL import Image

    Image.new("RGB", size, (255, 0, 0)).save(path)


def _gif(path: Path):
    from PIL import Image

    frames = [Image.new("RGB", (2, 2), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "assets"
    images = r / "default_assets" / "images"
    sounds = r / "default_assets" / "sounds"
    images.mkdir(parents=True)
    (sounds / "random").mkdir(parents=True)
    (r / "empty").mkdir()
    _png(images / "pic.png")
    _gif(images / "anim.gif")
    (images / "notes.txt").write_text("x")
    for name in ("a.mp3", "b.wav"):
        (sounds / name).write_bytes(b"x" * 10)
    (sounds / "clip.mp4").write_bytes(b"x")
    (sounds / "random" / "r.ogg").write_bytes(b"x")
    monkeypatch.setattr(fp, "assets_root", lambda: r)
    return r


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(fp, "_media_route_root", None)
    monkeypatch.setattr(fp, "_last_preview_volume", 100)
    monkeypatch.setattr(media_preview, "_read_asset", lambda rel: "")


@pytest.fixture
def media(monkeypatch):
    """No real static route: media URLs are synthetic."""
    monkeypatch.setattr(fp, "_media_url", lambda path, root: f"/m/{path.name}")


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(fp, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


def cfg(**kw):
    base = dict(title="Pick", subtitle="Sub", icon="image", extensions=(".png", ".gif"), mode="file",
                scope="assets", kind="image", on_pick=lambda r: None)
    base.update(kw)
    return fp._PickerConfig(**base)


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


def by_class(client, cls):
    return sorted((e for e in client.elements.values() if cls in e.classes), key=lambda e: e.id)


def htmls(el):
    return " ".join(e.content for e in el.descendants() if isinstance(e, ui.html))


def labels(el):
    return [e.text for e in el.descendants() if isinstance(e, ui.label)]


def open_picker(config, start, check):
    made = {}

    def build():
        p = fp._FilePicker(config, start)
        p.open()
        made["p"] = p

    async def run(user):
        with user.client:
            await check(user, made["p"])

    run_ui(build, run)
    return made["p"]


# ----------------------------------------------------------------- pure helpers

def test_assets_root_uses_path_utils(monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_assets_path", lambda: str(tmp_path))
    assert fp.assets_root() == tmp_path.resolve()


def test_real_falls_back_on_error(monkeypatch):
    def boom(p):
        raise OSError("x")

    monkeypatch.setattr(fp.os.path, "realpath", boom)
    assert fp._real(Path("/x/y")) == Path("/x/y")
    assert fp.is_inside(Path("/x"), Path("/"))  is False


def test_is_inside_value_error(monkeypatch):
    def boom(paths):
        raise ValueError("different drives")

    monkeypatch.setattr(fp.os.path, "commonpath", boom)
    assert fp.is_inside(Path("/a"), Path("/a")) is False


def test_from_web_path_bare_assets(tmp_path):
    assert fp.from_web_path("/assets", tmp_path) == tmp_path
    assert fp.from_web_path("  /assets/./x\\y ", tmp_path) == tmp_path / "x" / "y"
    assert fp.from_web_path(None, tmp_path) is None


def test_nearest_existing_dir_oserror_and_fs_root(monkeypatch, tmp_path):
    real_is_dir = Path.is_dir

    def flaky(self):
        if self.name == "bad":
            raise OSError("denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky)
    assert fp.nearest_existing_dir(tmp_path / "bad", Path("/fallback")) == tmp_path
    # walking up to the filesystem root without finding a folder returns the fallback
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    assert fp.nearest_existing_dir(Path("/a/b"), Path("/fallback")) == Path("/fallback")


class _DirEntry:
    def __init__(self, name, attrs=0, exc=None):
        self.name = name
        self._attrs = attrs
        self._exc = exc

    def stat(self, follow_symlinks=True):
        if self._exc:
            raise self._exc
        return SimpleNamespace(st_file_attributes=self._attrs)


def test_is_hidden(monkeypatch):
    assert fp._is_hidden(_DirEntry(".git"))
    assert fp._is_hidden(_DirEntry("Thumbs.db"))
    assert not fp._is_hidden(_DirEntry("ok.png"))
    monkeypatch.setattr(fp.sys, "platform", "win32")
    monkeypatch.setattr(fp.stat, "FILE_ATTRIBUTE_HIDDEN", 2, raising=False)
    monkeypatch.setattr(fp.stat, "FILE_ATTRIBUTE_SYSTEM", 4, raising=False)
    assert fp._is_hidden(_DirEntry("h.png", attrs=2))
    assert fp._is_hidden(_DirEntry("s.png", attrs=4))
    assert not fp._is_hidden(_DirEntry("v.png", attrs=1))
    assert not fp._is_hidden(_DirEntry("e.png", exc=OSError("x")))


def test_natural_key():
    names = ["b10", "B2", "a", "b1"]
    assert sorted(names, key=fp.natural_key) == ["a", "b1", "B2", "b10"]


def test_count_usable_limit_and_errors(monkeypatch, tmp_path):
    for i in range(5):
        (tmp_path / f"{i}.png").write_bytes(b"")
    (tmp_path / ".hidden.png").write_bytes(b"")
    (tmp_path / "sub").mkdir()
    assert fp.count_usable(tmp_path, (".png",)) == (6, 5)
    monkeypatch.setattr(fp, "_FOLDER_SCAN_LIMIT", 2)
    items, usable = fp.count_usable(tmp_path, (".png",))
    assert items <= 2
    assert fp.count_usable(tmp_path / "missing", (".png",)) == (0, 0)


def test_count_usable_is_file_error(monkeypatch, tmp_path):
    class _E:
        name = "x.png"

        def is_file(self):
            raise OSError("gone")

    class _It:
        def __enter__(self):
            return iter([_E()])

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fp.os, "scandir", lambda p: _It())
    monkeypatch.setattr(fp, "_is_hidden", lambda e: False)
    assert fp.count_usable(tmp_path, (".png",)) == (1, 0)


@pytest.mark.parametrize(
    "exc, msg",
    [(PermissionError(), "permission"), (FileNotFoundError(), "no longer exists"),
     (OSError(5, "I/O error"), "can't be opened (I/O error)"), (OSError("plain"), "can't be opened (plain)")],
)
def test_list_entries_errors(monkeypatch, tmp_path, exc, msg):
    def boom(p):
        raise exc

    monkeypatch.setattr(fp.os, "scandir", boom)
    entries, err = fp.list_entries(tmp_path, (".png",))
    assert entries == [] and msg in err


def test_list_entries_skips_unstatable_and_no_counts(monkeypatch, tmp_path):
    (tmp_path / "a.png").write_bytes(b"12")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.png").write_bytes(b"")
    entries, err = fp.list_entries(tmp_path, (".png",), count_folders=False)
    assert [e.name for e in entries] == ["d", "a.png"]
    assert entries[0].item_count is None and entries[1].size == 2 and entries[1].usable

    class _Bad:
        name = "bad.png"
        path = str(tmp_path / "bad.png")

        def is_dir(self):
            raise OSError("x")

    class _It:
        def __enter__(self):
            return iter([_Bad()])

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fp.os, "scandir", lambda p: _It())
    monkeypatch.setattr(fp, "_is_hidden", lambda e: False)
    assert fp.list_entries(tmp_path, (".png",)) == ([], None)


def test_list_entries_folder_count_limit(monkeypatch, tmp_path):
    for i in range(3):
        (tmp_path / f"d{i}").mkdir()
    monkeypatch.setattr(fp, "_FOLDER_COUNT_LIMIT", 2)
    entries, _ = fp.list_entries(tmp_path, (".png",))
    assert [e.item_count for e in entries] == [0, 0, None]


@pytest.mark.parametrize(
    "size, text",
    [(0, "0 B"), (1023, "1023 B"), (1024, "1 KB"), (1024 * 1024 * 1.5, "1.5 MB"), (3 * 1024 ** 3, "3 GB"),
     (5 * 1024 ** 4, "5120 GB")],
)
def test_format_size(size, text):
    assert fp.format_size(size) == text


def test_format_time(monkeypatch):
    assert fp.format_time(0) == ""
    now = time.time()
    assert fp.format_time(now).startswith("Today, ")
    old = time.mktime((2020, 3, 5, 12, 0, 0, 0, 0, -1))
    assert fp.format_time(old) == "Mar 5, 2020"


@pytest.mark.parametrize("ext, name", [(".png", "PNG image"), (".xyz", "XYZ file"), ("", "File"), (".json", "JSON file")])
def test_type_name(ext, name):
    assert fp.type_name(ext) == name


@pytest.mark.parametrize(
    "ext, kind, icon",
    [(".png", "image", "image"), (".mp3", "audio", "music_note"), (".mp4", "video", "movie"),
     (".db", "file", "storage"), (".sqlite", "file", "storage"), (".txt", "file", "description")],
)
def test_media_kind_and_icon(ext, kind, icon):
    assert fp.media_kind(ext) == kind and fp.file_icon(ext) == icon


@pytest.mark.parametrize(
    "name, expected",
    [(".", "file"), ("..", "file"), (".bashrc", ".bashrc"), ("LPT1.tar.gz", "_LPT1.tar.gz"), ("a\x01b.png", "a_b.png"),
     (None, "file")],
)
def test_safe_upload_name_more(name, expected):
    assert fp.safe_upload_name(name, []) == expected


def test_windows_drives(monkeypatch):
    monkeypatch.setattr(fp.os, "listdrives", lambda: ["C:\\", "D:\\"], raising=False)
    assert fp._windows_drives() == ["C:\\", "D:\\"]

    def boom():
        raise OSError("x")

    monkeypatch.setattr(fp.os, "listdrives", boom, raising=False)
    monkeypatch.setattr(fp.os.path, "exists", lambda p: p in ("C:\\", "E:\\"))
    assert fp._windows_drives() == ["C:\\", "E:\\"]
    monkeypatch.delattr(fp.os, "listdrives", raising=False)
    assert fp._windows_drives() == ["C:\\", "E:\\"]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("Desktop", "Documents"):
        (home / name).mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(fp.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel="": str(data))
    return home, data


def test_system_places_windows(monkeypatch, fake_home, tmp_path):
    home, data = fake_home
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setattr(fp.sys, "platform", "win32")
    monkeypatch.setattr(fp, "_windows_drives", lambda: [str(drive) + "\\", str(tmp_path / "missing") + "\\"])
    places = fp.system_places()
    labels_ = [label for _, label, _ in places]
    assert labels_[:3] == ["Home", "Desktop", "Documents"]
    assert "Downloads" not in labels_ and "Mycelian folder" in labels_


def test_system_places_darwin(monkeypatch, fake_home, tmp_path):
    vols = tmp_path / "Volumes"
    (vols / "USB").mkdir(parents=True)
    (vols / "file").write_text("")
    real_listdir = fp._safe_listdir
    monkeypatch.setattr(fp.sys, "platform", "darwin")
    monkeypatch.setattr(fp, "_safe_listdir", lambda p: real_listdir(vols) if str(p) == "/Volumes" else [])
    real_is_dir = fp.Path.is_dir

    def is_dir(self):
        if str(self).startswith("/Volumes/"):
            return real_is_dir(vols / self.name)
        return real_is_dir(self)

    monkeypatch.setattr(fp.Path, "is_dir", is_dir)
    monkeypatch.setattr(fp.Path, "is_symlink", lambda self: False)
    places = fp.system_places()
    assert ("usb", "USB", Path("/Volumes/USB")) in places
    assert ("computer", "Computer", Path("/")) in places


def test_system_places_linux_and_dedupe(monkeypatch, fake_home, tmp_path):
    home, data = fake_home
    monkeypatch.setattr(fp.sys, "platform", "linux")
    monkeypatch.setenv("USER", "me")
    seen = []
    monkeypatch.setattr(fp, "_safe_listdir", lambda p: seen.append(p) or [])
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel="": str(home))  # same as Home -> deduped
    places = fp.system_places()
    assert [label for _, label, _ in places].count("Home") == 1
    assert "Mycelian folder" not in [label for _, label, _ in places]
    assert Path("/media/me") in seen and Path("/run/media/me") in seen and Path("/mnt") in seen


def test_system_places_is_dir_error(monkeypatch, fake_home):
    monkeypatch.setattr(fp.sys, "platform", "linux")
    monkeypatch.setattr(fp, "_safe_listdir", lambda p: [])

    def boom(self):
        raise OSError("x")

    monkeypatch.setattr(fp.Path, "is_dir", boom)
    assert fp.system_places() == []


def test_safe_listdir(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / ".h").mkdir()
    assert fp._safe_listdir(tmp_path) == ["a", "b"]
    assert fp._safe_listdir(tmp_path / "missing") == []
    assert fp._safe_listdir_all(tmp_path / "missing") == []
    assert sorted(fp._safe_listdir_all(tmp_path)) == [".h", "a", "b"]


def test_breadcrumbs_system(monkeypatch, fake_home, tmp_path):
    home, _ = fake_home
    crumbs = fp.breadcrumbs(home / "Documents", root=None)
    assert [c for c, _ in crumbs] == ["Home", "Documents"]
    assert fp.breadcrumbs(home, root=None) == [("Home", home)]
    other = tmp_path / "elsewhere" / "x"
    crumbs = fp.breadcrumbs(other, root=None)
    assert crumbs[0][0] == "/" and crumbs[-1] == ("x", other)
    rel = fp.breadcrumbs(Path("rel/dir"), root=None)
    assert [c for c, _ in rel] == ["/", "rel", "dir"]


def test_image_dimensions(tmp_path):
    _png(tmp_path / "a.png", (7, 5))
    _gif(tmp_path / "b.gif")
    assert fp.image_dimensions(tmp_path / "a.png") == "7 × 5"
    assert fp.image_dimensions(tmp_path / "b.gif") == "2 × 2 · 3 frames"
    (tmp_path / "c.png").write_text("not an image")
    assert fp.image_dimensions(tmp_path / "c.png") == ""


def test_media_url_registers_route_once(monkeypatch, tmp_path):
    from nicegui import app

    added = []
    monkeypatch.setattr(app, "add_media_files", lambda route, folder: added.append((route, folder)))
    (tmp_path / "a b.png").write_bytes(b"")
    url = fp._media_url(tmp_path / "a b.png", tmp_path)
    assert url.startswith(f"{fp._MEDIA_ROUTE}/a%20b.png?v=")
    assert fp._media_url(tmp_path / "gone.png", tmp_path).endswith("gone.png?v=0")
    assert added == [(fp._MEDIA_ROUTE, str(fp._real(tmp_path)))]


def test_media_url_route_failure(monkeypatch, tmp_path):
    from nicegui import app

    def boom(route, folder):
        raise RuntimeError("started")

    monkeypatch.setattr(app, "add_media_files", boom)
    assert fp._media_url(tmp_path / "x.png", tmp_path) == ""


def test_dialog_file_types_and_short_list():
    assert fp._dialog_file_types((".png", "", ".gif")) == ("Usable files (*.png;*.gif)", "All files (*.*)")
    assert fp._dialog_file_types(()) == ()
    assert fp._short_list(["a", "b"]) == "a, b"
    assert fp._short_list(["a", "b", "c", "d", "e"]) == "a, b, c and 2 more"
    assert fp._new_upload_batch() == {"added": [], "rejected": [], "blocked": [], "failed": [], "folder": "", "timer": None}


def test_native_window(monkeypatch):
    from nicegui import app

    win = object()
    monkeypatch.setattr(app.native, "main_window", win, raising=False)
    assert fp._native_window() is win
    import nicegui

    class _App:
        @property
        def native(self):
            raise RuntimeError("x")

    monkeypatch.setattr(nicegui, "app", _App())
    assert fp._native_window() is None


def test_icon_helper():
    assert fp._icon("x", "c") == '<span class="fpk-icon c">x</span>'


# ----------------------------------------------------------------- picker construction

def test_start_resolution(root, tmp_path):
    p = fp._FilePicker(cfg(), "/assets/default_assets/images/pic.png")
    assert p.folder == root / "default_assets" / "images" and p.preselect.name == "pic.png"
    assert p.view == "grid" and p.only_usable is True
    p = fp._FilePicker(cfg(kind="audio"), str(root / "default_assets" / "sounds"))  # absolute path inside root
    assert p.folder == root / "default_assets" / "sounds" and p.view == "list"
    p = fp._FilePicker(cfg(), str(tmp_path / "outside"))
    assert p.folder == root
    p = fp._FilePicker(cfg(scope="system"), str(root / "empty" / "missing.png"))
    assert p.root is None and p.folder == root / "empty"
    p = fp._FilePicker(cfg(mode="folder", volume=150), None)
    assert p.folder_mode and p.only_usable is False and p.volume == 100
    p = fp._FilePicker(cfg(volume=-5), None)
    assert p.volume == 0


def test_open_file_picker_builds_config(monkeypatch):
    seen = {}

    class _P:
        def __init__(self, config, start):
            seen["cfg"], seen["start"] = config, start

        def open(self):
            seen["opened"] = True

    monkeypatch.setattr(fp, "_FilePicker", _P)
    fp.open_file_picker(title="T", subtitle="S", extensions=[".PNG"], on_pick=lambda r: None, kind="image", start="x",
                        places=[("i", "L", Path("/p"))])
    c = seen["cfg"]
    assert c.icon == "image" and c.extensions == (".png",) and c.places_extra == (("i", "L", Path("/p")),)
    assert seen["start"] == "x" and seen["opened"]
    fp.open_file_picker(title="T", subtitle="S", extensions=[], on_pick=lambda r: None, mode="folder")
    assert seen["cfg"].icon == "folder"
    fp.open_file_picker(title="T", subtitle="S", extensions=[], on_pick=lambda r: None, kind="audio")
    assert seen["cfg"].icon == "music_note"
    fp.open_file_picker(title="T", subtitle="S", extensions=[], on_pick=lambda r: None, kind="other", icon="star")
    assert seen["cfg"].icon == "star"
    fp.open_file_picker(title="T", subtitle="S", extensions=[], on_pick=lambda r: None, kind="other")
    assert seen["cfg"].icon == "description"


# ----------------------------------------------------------------- dialog: assets, file mode

def test_open_grid_images(root, media, notes):
    picked = []

    async def check(user, p):
        await user.should_see("Pick")
        assert p.dialog.value is True
        # places: Assets, Default assets, Images (Sounds too), active one is Images
        places = by_class(user.client, "fpk-place")
        assert [("is-active" in pl.classes) for pl in places] == [False, False, True, False]
        assert "Overlays can only load files inside the assets folder." in htmls(p.side)
        # crumbs
        crumbs = by_class(user.client, "fpk-crumb")
        assert ["is-last" in c.classes for c in crumbs] == [False, False, True]
        # grid with thumbnails for images; notes.txt hidden (only usable)
        cards = by_class(user.client, "fpk-card")
        assert len(cards) == 2
        assert all('<img loading="lazy" src="/m/' in htmls(c) for c in cards)
        assert p.count_label.text == "3 files · 2 usable"
        # preselected file shows in preview and fills
        assert p.selected.name == "pic.png"
        assert "is-selected" in [c for c in cards if "pic.png" in htmls(c)][0].classes
        assert "Dimensions" in htmls(p.preview) and "4 × 3" in htmls(p.preview)
        assert "/assets/default_assets/images/" in p.fills.content and "pic.png" in p.fills.content
        assert "disable" not in p.confirm_btn.props
        # animated gif preview has a Length row
        gif = [c for c in cards if "anim.gif" in htmls(c)][0]
        fire(user, gif)
        assert p.selected.name == "anim.gif" and "Length" in htmls(p.preview)
        # show all files: notes.txt appears dimmed with a tooltip
        p.only_box.set_value(False)
        cards = by_class(user.client, "fpk-card")
        assert len(cards) == 3
        txt = [c for c in cards if "notes.txt" in htmls(c)][0]
        assert "is-dim" in txt.classes and "fpk-thumb" in htmls(txt) and "TXT" in htmls(txt)
        fire(user, txt)
        assert "Usable here" in htmls(p.preview) and "No" in htmls(p.preview)
        assert "disable" in p.confirm_btn.props
        # double-click a usable file confirms
        fire(user, [c for c in by_class(user.client, "fpk-card") if "pic.png" in htmls(c)][0], "dblclick")
        assert p.dialog.value is False

    open_picker(cfg(on_pick=picked.append), "/assets/default_assets/images/pic.png", check)
    assert len(picked) == 1
    r = picked[0]
    assert r.web_path == "/assets/default_assets/images/pic.png" and r.web_dir == "/assets/default_assets/images/"
    assert r.filename == "pic.png" and r.is_dir is False


def test_navigation_places_crumbs_up_filter_view(root, media, notes):
    async def check(user, p):
        assert p.folder == root
        assert "disable" in p.up_btn.props  # at top
        # folder rows navigate in file mode
        p._set_view("list")
        assert "is-on" in p.view_btns["list"].classes and "is-on" not in p.view_btns["grid"].classes
        rows = by_class(user.client, "fpk-row")
        assert [("default_assets" in htmls(r)) for r in rows][:1] == [True]
        assert "chevron_right" in htmls(rows[0])
        fire(user, rows[0])
        assert p.folder == root / "default_assets"
        assert "disable" not in p.up_btn.props
        # crumb back to assets
        fire(user, by_class(user.client, "fpk-crumb")[0])
        assert p.folder == root
        # place click
        fire(user, by_class(user.client, "fpk-place")[3])
        assert p.folder == root / "default_assets" / "sounds"
        fire(user, p.up_btn)
        assert p.folder == root / "default_assets"
        # up at the top is a no-op
        p._load_folder(root)
        p._go_up()
        assert p.folder == root
        # dblclick on a folder opens it
        p._on_dblclick(fp.Entry(path=root / "empty", name="empty", is_dir=True))
        assert p.folder == root / "empty"
        await user.should_see("This folder is empty. Use Add files… to copy files here.")
        # filter
        p._load_folder(root / "default_assets" / "images")
        p.filter.set_value("zzz")
        await user.should_see("Nothing here matches the filter.")
        p.filter.set_value(None)
        assert p.query == ""
        # grid toggle back
        fire(user, p.view_btns["grid"])
        assert p.view == "grid"

    open_picker(cfg(), None, check)


def test_folder_meta_and_messages(root, media, notes):
    async def check(user, p):
        sounds = root / "default_assets" / "sounds"
        p._load_folder(sounds)
        # only usable: clip.mp4 not usable for images -> "No usable files" message for files... folder "random" shown
        rows = by_class(user.client, "fpk-card")
        assert len(rows) == 1  # random folder only
        assert "1 item · no images" in htmls(rows[0])
        p._load_folder(root / "default_assets")
        meta = [htmls(r) for r in by_class(user.client, "fpk-card")]
        assert any("3 items · 2 images" in m for m in meta)
        # a folder with only unusable files
        (root / "junk").mkdir()
        (root / "junk" / "x.txt").write_text("")
        p._load_folder(root / "junk")
        await user.should_see("No usable files here. Untick “Only usable files” to see everything.")

    open_picker(cfg(), None, check)


def test_error_and_truncated_listing(root, media, notes, monkeypatch):
    async def check(user, p):
        monkeypatch.setattr(fp, "MAX_LIST_ENTRIES", 1)
        p._load_folder(root / "default_assets" / "images")
        await user.should_see("Showing the first 1 of 2. Use the filter to find others.")
        monkeypatch.setattr(fp, "list_entries", lambda *a, **k: ([], "This folder no longer exists."))
        p._load_folder(root / "default_assets")
        await user.should_see("This folder no longer exists.")

    open_picker(cfg(), None, check)


def test_load_folder_outside_root_warns(root, media, notes, tmp_path):
    async def check(user, p):
        p._load_folder(tmp_path)
        assert p.folder == root

    open_picker(cfg(), None, check)
    assert notes[-1][1]["title"] == "The picker only shows files inside the assets folder"


def test_audio_list_play_and_volume(root, media, notes, monkeypatch):
    js = []

    async def check(user, p):
        monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: js.append(code))
        rows = by_class(user.client, "fpk-row")
        names = [htmls(r) for r in rows]
        assert any("a.mp3" in n for n in names)
        play = by_class(user.client, "fpk-play")
        assert len(play) == 2  # a.mp3 and b.wav
        fire(user, play[0], "click.stop")
        assert p.selected.name == "a.mp3"
        pv = htmls(p.preview)
        assert "<audio" in pv and " autoplay" in pv and 'data-vol="40"' in pv
        assert "volume_up" in pv
        slider = [e for e in p.preview.descendants() if isinstance(e, ui.slider)][0]
        slider.set_value(75)
        assert p.volume == 75 and fp._last_preview_volume == 75
        assert "75%" in labels(p.preview)
        assert js[-1] == "window.MycelianPreview && window.MycelianPreview.setPickerVolume(75)"
        slider.set_value(None)
        assert p.volume == 0
        # untick usable: clip.mp4 (unusable here) appears with tooltip in list view
        p.only_box.set_value(False)
        clip = [r for r in by_class(user.client, "fpk-row") if "clip.mp4" in htmls(r)][0]
        assert "is-dim" in clip.classes
        tips = [t for t in user.client.elements.values() if isinstance(t, ui.tooltip)
                and t.props.get("target") == f"#{clip.html_id}"]
        assert tips and "Not usable here. Supported: MP3, WAV" in tips[0].text
        # a folder entry selected in file mode previews the folder contents
        p._load_folder(root / "default_assets")
        p.selected = None
        p._render_preview()
        assert "Items" in htmls(p.preview)
        assert "Click a file to preview it." in labels(p.preview)[-1]

    open_picker(cfg(kind="audio", extensions=(".mp3", ".wav"), volume=40), "/assets/default_assets/sounds/", check)


def test_video_preview(root, media, notes):
    async def check(user, p):
        fire(user, [r for r in by_class(user.client, "fpk-row") if "clip.mp4" in htmls(r)][0])
        pv = htmls(p.preview)
        assert "<video" in pv and "muted" in pv and "volume_off" in pv
        assert "Muted" in labels(p.preview)

    open_picker(cfg(kind="video", extensions=(".mp4",)), "/assets/default_assets/sounds/", check)


def test_unregistered_media_and_unknown_type_preview(root, notes, monkeypatch):
    monkeypatch.setattr(fp, "_media_url", lambda path, root: "")

    async def check(user, p):
        fire(user, [c for c in by_class(user.client, "fpk-card") if "pic.png" in htmls(c)][0])
        pv = htmls(p.preview)
        assert "<img" not in pv and "fpk-pv-media is-ok" in pv
        cards = by_class(user.client, "fpk-card")
        assert all("has-img" not in htmls(c) for c in cards)

    open_picker(cfg(), "/assets/default_assets/images/", check)


# ----------------------------------------------------------------- dialog: folder mode

def test_folder_mode(root, media, notes):
    async def check(user, p):
        assert p.only_box.text == "Show files" and p.only_box.value is False
        assert p.selected is None
        # with no selection the current folder is the result
        assert "Folder" in p.fills.content and "/assets/default_assets/" in p.fills.content
        p._set_view("list")
        rows = by_class(user.client, "fpk-row")
        assert [("images" in htmls(r)) or ("sounds" in htmls(r)) for r in rows] == [True, True]
        assert "Can use" in htmls(rows[0])  # images folder has usable images
        sounds_row = [r for r in rows if "sounds" in htmls(r)][0]
        assert "is-dim" in sounds_row.classes and "Can use" not in htmls(sounds_row)
        # click selects a folder (doesn't open it)
        fire(user, rows[0])
        assert p.folder == root / "default_assets" and p.selected.name == "images"
        assert "Images" in htmls(p.preview) and "Other items" in htmls(p.preview)
        # show files: files are dimmed and clicking them does nothing
        p.only_box.set_value(True)
        assert p.only_usable is False  # folder mode keeps only_usable off
        p._load_folder(root / "default_assets" / "images")
        cards = by_class(user.client, "fpk-row")
        assert cards and all("is-dim" in c.classes for c in cards)
        p._on_click(p.entries[0])
        assert p.selected is None
        # dblclick on a file in folder mode does nothing
        p._on_dblclick(p.entries[0])
        assert p.dialog.value is True
        # go up selects the folder we came from
        p._go_up()
        assert p.selected.name == "images"
        p.only_box.set_value(False)
        p._load_folder(root / "empty")
        await user.should_see("No folders here.")
        assert "Pick a folder below, or use this one." in labels(p.preview)
        fire(user, p.confirm_btn)
        assert p.dialog.value is False

    open_picker(cfg(mode="folder", kind="image"), "/assets/default_assets/", check)


def test_folder_mode_confirm_result(root, media, notes):
    picked = []

    async def check(user, p):
        p._confirm()

    open_picker(cfg(mode="folder", on_pick=picked.append, confirm_label="Use it"), "/assets/default_assets/", check)
    assert picked[0].is_dir and picked[0].web_path == "/assets/default_assets/" and picked[0].web_dir == "/assets/default_assets/"
    assert picked[0].filename == ""


def test_folder_preview_lists_six_and_more(root, media, notes):
    many = root / "many"
    many.mkdir()
    for i in range(8):
        _png(many / f"p{i}.png")

    async def check(user, p):
        p.selected = fp.Entry(path=many, name="many", is_dir=True)
        p._render_preview()
        assert "+ 2 more" in htmls(p.preview)
        assert htmls(p.preview).count("<span>p") == 6

    open_picker(cfg(mode="folder"), "/assets/", check)


def test_within_limits_and_hint(root, media, notes):
    alerts = root / "alerts"
    alerts.mkdir()

    async def check(user, p):
        assert p.folder == alerts
        p._load_folder(root / "default_assets")
        p._set_view("list")
        rows = by_class(user.client, "fpk-row")
        assert all("is-dim" in r.classes for r in rows)  # outside "within"
        assert "Must be in alerts" in p.fills.content
        assert "disable" in p.confirm_btn.props
        p._confirm()

    open_picker(cfg(mode="folder", within=alerts, within_hint="Must be in alerts"), None, check)
    assert notes[-1][1]["title"] == "Choose a folder first"


# ----------------------------------------------------------------- dialog: system scope

def test_system_scope(root, media, notes, monkeypatch, tmp_path):
    db = tmp_path / "dbs"
    db.mkdir()
    (db / "main.db").write_bytes(b"x" * 2048)
    monkeypatch.setattr(fp, "system_places", lambda: [("home", "Home", tmp_path), ("save", "DBs", db)])
    picked = []

    async def check(user, p):
        assert p.upload is None and p.add_btn is None
        assert "Overlays can only load" not in htmls(p.side)
        assert p.view == "list"
        fire(user, [r for r in by_class(user.client, "fpk-row") if "main.db" in htmls(r)][0])
        assert "storage" in htmls(p.preview) and "SQLite database" in htmls(p.preview) and "2 KB" in htmls(p.preview)
        assert "Path" in p.fills.content and str(db / "main.db") in p.fills.content
        crumbs = by_class(user.client, "fpk-crumb")
        assert crumbs and "fpk-icon" in htmls(crumbs[0]) and ">home<" in htmls(crumbs[0])
        assert crumbs[-1].classes.count("is-last") == 1

    open_picker(cfg(scope="system", extensions=(".db",), kind="file"), str(db / "main.db"), check)
    assert notes == []


def test_system_scope_pick_and_empty(root, media, notes, monkeypatch, tmp_path):
    db = tmp_path / "dbs2"
    db.mkdir()
    (db / "main.db").write_bytes(b"")
    empty = tmp_path / "empty2"
    empty.mkdir()
    monkeypatch.setattr(fp, "system_places", lambda: [("home", "Home", tmp_path)])
    picked = []

    async def check(user, p):
        p._confirm()
        assert p.dialog.value is False
        p._load_folder(empty)
        await user.should_see("This folder is empty.")
        assert "Add files" not in " ".join(labels(p.body))

    open_picker(cfg(scope="system", extensions=(".db",), on_pick=picked.append), str(db / "main.db"), check)
    assert picked[0].path == db / "main.db" and picked[0].web_path == ""


def test_system_scope_up_button_at_anchor(root, media, notes, monkeypatch):
    monkeypatch.setattr(fp, "system_places", lambda: [])

    async def check(user, p):
        p._load_folder(Path("/"))
        assert "disable" in p.up_btn.props

    open_picker(cfg(scope="system"), "/", check)


# ----------------------------------------------------------------- confirm

def test_confirm_without_selection_and_callback_error(root, media, notes):
    def boom(r):
        raise ValueError("bad field")

    async def check(user, p):
        p.selected = None
        p._confirm()
        assert notes[-1][1]["title"] == "Choose a file first"
        p._load_folder(root / "default_assets" / "images", select=root / "default_assets" / "images" / "pic.png")
        p._confirm()
        assert p.dialog.value is True
        # close buttons
        close = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.props.get("icon") == "close"][0]
        fire(user, close)
        assert p.dialog.value is False
        p.dialog.open()
        fire(user, [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Cancel"][0])
        assert p.dialog.value is False

    open_picker(cfg(on_pick=boom), None, check)
    msg, kw = notes[-1]
    assert kw["title"] == "Couldn't use pic.png" and "ValueError: bad field" in kw["details"]


# ----------------------------------------------------------------- add files

def test_add_button_states(root, media, notes, monkeypatch):
    async def check(user, p):
        assert "disable" not in p.add_btn.props
        assert p.add_tip.text == "Copy files from your computer into this folder"
        monkeypatch.setattr(fp, "is_temp_bundle", lambda path: True)
        p._update_add_button()
        assert "disable" in p.add_btn.props and "temporary folder" in p.add_tip.text
        monkeypatch.setattr(fp, "is_temp_bundle", lambda path: False)
        monkeypatch.setattr(fp.os, "access", lambda path, mode: False)
        p._update_add_button()
        assert p.add_tip.text == "Mycelian can't write to this folder."
        p.add_tip = None
        p._update_add_button()

    open_picker(cfg(), None, check)


def test_upload_events(root, media, notes, monkeypatch):
    monkeypatch.setattr(fp, "_UPLOAD_SUMMARY_DELAY_SEC", 0.0)
    images = root / "default_assets" / "images"

    class _Upload:
        def __init__(self, name, data=b"PNG", fail=False):
            self.name, self.data, self.fail = name, data, fail

        async def save(self, path):
            if self.fail:
                raise OSError("disk full")
            Path(path).write_bytes(self.data)

    async def check(user, p):
        resets = []
        p.upload.reset = lambda: resets.append(1)
        await p._on_upload(SimpleNamespace(file=_Upload("new.png")))
        assert (images / "new.png").read_bytes() == b"PNG"
        assert p.selected.name == "new.png" and resets == [1]
        # old event shape: name + content
        await p._on_upload(SimpleNamespace(name="old.png", content=SimpleNamespace(read=lambda: b"OLD")))
        assert (images / "old.png").read_bytes() == b"OLD"
        await p._on_upload(SimpleNamespace(file=_Upload("broken.png", fail=True)))
        assert not (images / "broken.png").exists()
        p.upload = None
        await p._on_upload(SimpleNamespace(file=_Upload("third.png")))
        await asyncio.sleep(0.01)

    open_picker(cfg(), "/assets/default_assets/images/", check)
    titles = [k["title"] for _, k in notes]
    assert "Couldn't add 1 file" in titles
    assert any(t.startswith("Added") for t in titles)


def test_add_local_file_copy_failure(root, media, notes, monkeypatch, tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"x")

    async def check(user, p):
        def boom(a, b):
            raise PermissionError("denied")

        monkeypatch.setattr(fp.shutil, "copyfile", boom)
        await p._add_local_file(src)
        p._flush_upload_notices()

    open_picker(cfg(), None, check)
    msg, kw = notes[-1]
    assert kw["title"] == "Couldn't add 1 file" and "PermissionError: denied" in kw["details"]


def test_native_dialog_failure_falls_back(root, media, notes, monkeypatch):
    class Window:
        async def create_file_dialog(self, **kw):
            raise RuntimeError("pywebview")

    monkeypatch.setattr(fp, "_native_window", lambda: Window())

    async def check(user, p):
        calls = []
        p.upload.run_method = lambda name: calls.append(name)
        await p._pick_uploads()
        assert calls == ["pickFiles"]
        p.upload = None
        await p._pick_uploads()  # nothing to fall back to

    open_picker(cfg(), None, check)


def test_native_dialog_cancelled(root, media, notes, monkeypatch):
    class Window:
        async def create_file_dialog(self, **kw):
            return None

    monkeypatch.setattr(fp, "_native_window", lambda: Window())

    async def check(user, p):
        p.upload.run_method = lambda name: pytest.fail("should not fall back")
        await p._pick_uploads()

    open_picker(cfg(), None, check)


def test_add_files_button_click(root, media, notes, monkeypatch):
    monkeypatch.setattr(fp, "_native_window", lambda: None)
    calls = []

    async def check(user, p):
        p.upload.run_method = lambda name: calls.append(name)
        fire(user, p.add_btn)
        await asyncio.sleep(0)

    open_picker(cfg(), None, check)
    assert calls == ["pickFiles"]


def test_writable_target_blocked_outside_root(root, notes, monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "assets_root", lambda: root)
    p = fp._FilePicker(cfg(), None)
    p.folder = tmp_path  # outside the assets folder
    assert p._writable_target("x.png") is None
    p.root = None
    assert p._writable_target("x.png") is None
    # no running loop: each notice is flushed right away
    assert [k["title"] for _, k in notes] == ["Files can't be added to this folder"] * 2
    assert notes[-1][0] == "x.png"


def test_flush_notice_variants(root, notes, monkeypatch):
    p = fp._FilePicker(cfg(), None)
    p._upload_batch.update(added=["a.png"], rejected=["x.exe", "y.exe"], failed=["f1", "f2"])
    p._flush_upload_notices()
    titles = [k["title"] for _, k in notes]
    assert titles == ["Added a.png", "2 files weren't added", "Couldn't add 2 files"]
    assert notes[1][1]["hint"] == "Only these file types can be used here: .png, .gif."
    notes.clear()
    p._upload_batch.update(added=["a.png", "b.png"])
    p._flush_upload_notices()
    assert notes[0][1]["title"] == "Added 2 files"
    assert p._upload_batch == fp._new_upload_batch()


def test_note_upload_without_loop_flushes_now(root, notes):
    p = fp._FilePicker(cfg(), None)
    p._note_upload("rejected", "bad.exe")
    assert notes[0][1]["title"] == "1 file wasn't added"


def test_note_upload_cancels_previous_timer(root, notes):
    p = fp._FilePicker(cfg(), None)
    cancelled = []
    p._upload_batch["timer"] = SimpleNamespace(cancel=lambda: cancelled.append(1))

    async def go():
        p._note_upload("added", "a.png")
        p._upload_batch["timer"].cancel()

    asyncio.run(go())
    assert cancelled == [1]


def test_result_variants(root):
    p = fp._FilePicker(cfg(), None)
    p.selected = fp.Entry(path=root / "default_assets", name="default_assets", is_dir=True)
    assert p._result() is None
    p.selected = None
    assert p._result() is None

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/fonts_registry.py (default-asset font listing)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.spore_studio import fonts_registry as fr


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    fdir = assets / "default_assets" / "fonts"
    fdir.mkdir(parents=True)
    monkeypatch.setattr(fr, "get_assets_path", lambda rel="": str(assets))
    monkeypatch.setattr(fr, "_fonts_listing_cache", None)
    return fdir


def _touch(fdir, *names):
    for n in names:
        (fdir / n).write_bytes(b"x")


def test_list_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "get_assets_path", lambda rel="": str(tmp_path / "none"))
    assert fr.list_default_font_files() == []


def test_list_filters_and_sorts_case_insensitive(fonts):
    _touch(fonts, "zeta.TTF", "Alpha.otf", "beta.woff2", "readme.txt", "c.woff")
    assert fr.list_default_font_files() == ["Alpha.otf", "beta.woff2", "c.woff", "zeta.TTF"]


def test_list_uses_cache_for_same_mtime(fonts, monkeypatch):
    _touch(fonts, "a.ttf")
    first = fr.list_default_font_files()
    calls = []
    monkeypatch.setattr(fr.os, "listdir", lambda d: calls.append(d) or [])
    second = fr.list_default_font_files()
    assert second == first == ["a.ttf"] and calls == []
    second.append("mutated")
    assert fr.list_default_font_files() == ["a.ttf"]


def test_list_getmtime_error(fonts, monkeypatch):
    def boom(_p):
        raise OSError

    monkeypatch.setattr(fr.os.path, "getmtime", boom)
    assert fr.list_default_font_files() == []


def test_list_listdir_error(fonts, monkeypatch):
    def boom(_p):
        raise OSError

    monkeypatch.setattr(fr.os, "listdir", boom)
    assert fr.list_default_font_files() == []


@pytest.mark.parametrize(
    "value,expected",
    [("a.ttf", True), (" A.WOFF2 ", True), ("dir/x.otf", True), ("dir\\x.woff", True),
     ("x.png", False), ("", False), (None, False), (5, False), ("ttf", False)],
)
def test_is_font_filename(value, expected):
    assert fr.is_font_filename(value) is expected


def test_resolve_font_filename(fonts):
    _touch(fonts, "Renogare.ttf", "Renogare.otf", "Solo.woff", "Pair.otf", "Pair.woff")
    assert fr.resolve_font_filename("") is None
    assert fr.resolve_font_filename(None) is None
    assert fr.resolve_font_filename("renogare.otf") == "Renogare.otf"  # exact match
    assert fr.resolve_font_filename("some/dir/Renogare") == "Renogare.ttf"  # ttf preferred
    assert fr.resolve_font_filename("solo") == "Solo.woff"  # single match
    assert fr.resolve_font_filename("Pair") == "Pair.otf"  # no ttf -> first
    assert fr.resolve_font_filename("Solo.ttf") == "Solo.woff"  # ext mismatch -> stem match
    assert fr.resolve_font_filename("Unknown") is None
    assert fr.resolve_font_filename(".ttf") is None  # dotfile: no extension, no stem match


def test_resolve_font_empty_stem(fonts):
    _touch(fonts, "a.ttf")
    # basename of a trailing slash is empty
    assert fr.resolve_font_filename("dir/") is None


def test_resolve_no_files(fonts):
    assert fr.resolve_font_filename("Renogare") is None


@pytest.mark.parametrize(
    "eid,expected",
    [("title", "spore-el-title"), ("a b.c", "spore-el-a_b_c"), ("", "spore-el-el"),
     (None, "spore-el-el"), ("!!", "spore-el-el"), ("x-1_y", "spore-el-x-1_y")],
)
def test_exposed_element_font_family(eid, expected):
    assert fr.exposed_element_font_family(eid) == expected


@pytest.mark.parametrize(
    "name,expected",
    [("Renogare.ttf", "spore-font-Renogare_ttf"), ("a b.otf", "spore-font-a_b_otf"),
     ("..", "spore-font-custom"), ("é.woff", "spore-font-é_woff")],
)
def test_font_css_family_name(name, expected):
    assert fr.font_css_family_name(name) == expected


def test_get_font_registry(fonts):
    _touch(fonts, "Renogare.ttf")
    assert fr.get_font_registry() == {"fonts": [{
        "filename": "Renogare.ttf",
        "url": "/assets/default_assets/fonts/Renogare.ttf",
        "css_family": "spore-font-Renogare_ttf",
        "label": "Renogare",
    }]}

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.font_utils (system font discovery and CSS font stack)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import font_utils as fu


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(fu, "_cached_families", None)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Arial", "Arial"),
        ("  Arial  ", "Arial"),
        ("", ""),
        ("   ", ""),
        ("Arial-Bold", "Arial"),
        ("Roboto_Italic", "Roboto"),
        ("Inter-regular", "Inter"),
        ("Open Sans-LightItalic", "Open Sans"),
        ("Foo-Medium-Extra", "Foo"),
        ("Bar_BLACK", "Bar"),
        ("Helvetica Neue", "Helvetica Neue"),
        ("Mono-Condensed", "Mono-Condensed"),
    ],
)
def test_normalize_family_name(raw, expected):
    assert fu._normalize_family_name(raw) == expected


def test_families_from_paths(tmp_path):
    d = tmp_path / "fonts"
    (d / "sub").mkdir(parents=True)
    for name in ["Arial-Bold.ttf", "Arial.TTF", "Inter.otf", "sub/Coll.ttc", "Web.woff", "Web2.woff2",
                 "readme.txt", ".hidden.ttf", "-Bold.ttf"]:
        (d / name).write_bytes(b"")
    found = fu._families_from_paths([d, tmp_path / "missing"])
    assert found == {"Arial", "Inter", "Coll", "Web", "Web2"}


def test_families_from_paths_oserror_logged(tmp_path, monkeypatch, caplog):
    d = tmp_path / "f"
    d.mkdir()

    def boom(self, pattern):
        raise OSError("denied")

    monkeypatch.setattr(Path, "rglob", boom)
    with caplog.at_level("DEBUG", logger=fu.logger.name):
        assert fu._families_from_paths([d]) == set()
    assert "Font scan skipped" in caplog.text


def test_fc_list_parses_output(monkeypatch):
    out = "DejaVu Sans,DejaVu Sans Condensed:style=Book\nNoto Serif:style=Regular\n\n , :x\nPlain\n"
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(stdout=out)

    monkeypatch.setattr(fu.subprocess, "run", fake_run)
    assert fu._families_fc_list() == {"DejaVu Sans", "DejaVu Sans Condensed", "Noto Serif", "Plain"}
    assert calls[0][0] == ["fc-list", ":family"]
    assert calls[0][1]["check"] is False


def test_fc_list_none_stdout(monkeypatch):
    monkeypatch.setattr(fu.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=None))
    assert fu._families_fc_list() == set()


@pytest.mark.parametrize("exc", [FileNotFoundError(), subprocess.TimeoutExpired("fc-list", 8), OSError()])
def test_fc_list_errors(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(fu.subprocess, "run", boom)
    assert fu._families_fc_list() == set()


def test_font_directories_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(fu.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fu.Path, "home", classmethod(lambda cls: tmp_path))
    assert fu._font_directories() == [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                                      tmp_path / "Library/Fonts"]


def test_font_directories_windows(monkeypatch):
    monkeypatch.setattr(fu.platform, "system", lambda: "Windows")
    monkeypatch.setenv("WINDIR", "/winroot")
    assert fu._font_directories() == [Path("/winroot") / "Fonts"]
    monkeypatch.delenv("WINDIR")
    assert fu._font_directories() == [Path(r"C:\Windows") / "Fonts"]


def test_font_directories_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(fu.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fu.Path, "home", classmethod(lambda cls: tmp_path))
    assert fu._font_directories() == [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                                      tmp_path / ".fonts", tmp_path / ".local/share/fonts"]


def test_get_available_font_families_sorted_and_cached(monkeypatch):
    monkeypatch.setattr(fu.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fu, "_font_directories", lambda: ["dirs"])
    scans = []
    monkeypatch.setattr(fu, "_families_from_paths", lambda p: scans.append(p) or {"zeta", "Alpha", "beta"})
    monkeypatch.setattr(fu, "_families_fc_list", lambda: pytest.fail("fc-list only on Linux"))
    first = fu.get_available_font_families()
    assert first == [fu.SYSTEM_DEFAULT_LABEL, "Alpha", "beta", "zeta"]
    first.append("mutated")
    assert fu.get_available_font_families() == [fu.SYSTEM_DEFAULT_LABEL, "Alpha", "beta", "zeta"]
    assert len(scans) == 1
    fu.get_available_font_families(refresh=True)
    assert len(scans) == 2


def test_get_available_font_families_linux_merges_fc_list(monkeypatch):
    monkeypatch.setattr(fu.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fu, "_font_directories", lambda: [])
    monkeypatch.setattr(fu, "_families_from_paths", lambda p: {"FileFont"})
    monkeypatch.setattr(fu, "_families_fc_list", lambda: {"FcFont", "FileFont"})
    assert fu.get_available_font_families() == [fu.SYSTEM_DEFAULT_LABEL, "FcFont", "FileFont"]


@pytest.mark.parametrize("family", [None, "", fu.SYSTEM_DEFAULT_LABEL])
def test_resolve_font_css_default(family):
    assert fu.resolve_font_css(family) == fu._DEFAULT_STACK


def test_resolve_font_css_escapes():
    assert fu.resolve_font_css("Fira Code") == "'Fira Code', -apple-system, BlinkMacSystemFont, sans-serif"
    assert fu.resolve_font_css("O'Neil\\X").startswith("'O\\'Neil\\\\X', ")

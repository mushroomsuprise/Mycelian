# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.theme_manager (CSS generation and the theme catalog, all under tmp_path)."""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import theme_manager as tm
from modules.theme_manager import PROTECTED_THEMES, ThemeColors, ThemeManager

FIELDS = [f.name for f in dataclasses.fields(ThemeColors)]


def theme_dict(name, display=None, theme_type="dark", **extra):
    data = {"name": name, "display_name": display or name.title(), "theme_type": theme_type,
            "primary": "rgb(1, 2, 3)", "bg_base": "#000"}
    data.update(extra)
    return data


def write_theme(folder: Path, filename: str, data) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


@pytest.fixture
def themes_dir(tmp_path):
    return tmp_path / "themes"


@pytest.fixture
def manager(monkeypatch, themes_dir):
    """A fresh ThemeManager reading tmp_path/themes; the real singleton is restored afterwards."""
    monkeypatch.setattr(ThemeManager, "_instance", None)
    monkeypatch.setattr(ThemeManager, "_themes_dir", themes_dir)
    monkeypatch.setattr(ThemeManager, "_loaded_themes", {})
    monkeypatch.setattr(ThemeManager, "_catalog_loaded", False)
    monkeypatch.setattr(ThemeManager, "_current_theme", "dark")
    return ThemeManager()


@pytest.fixture
def catalog(themes_dir):
    write_theme(themes_dir, "dark_theme.json", theme_dict("dark", "Dark"))
    write_theme(themes_dir, "light_theme.json", theme_dict("light", "Light", "light"))
    write_theme(themes_dir, "nord.json", theme_dict("nord", "Nord"))
    write_theme(themes_dir / "custom", "mine.json", theme_dict("mine", "Mine"))
    return themes_dir


# --------------------------------------------------------------------------- CSS generation

@pytest.mark.parametrize("raw, expected", [("", 12), ("abc", 12), (None, 12), ("8", 8), ("8.9", 8), ("-4", 0),
                                           ("100", 28), ("28", 28)])
def test_notify_radius(raw, expected):
    assert tm._notify_radius(ThemeColors(notify_radius=raw)) == expected


def test_generate_css_variables_uses_values_and_fallbacks():
    theme = ThemeColors(name="n", theme_type="dark", primary="P", success="S", warning="W", error="E", info="I",
                        notify_error="NE", focus_ring="F", notify_radius="5")
    css = tm.generate_css_variables(theme)
    assert ":root, body, html {" in css
    assert "--theme-name: n;" in css and "--color-primary: P;" in css
    assert "--color-notify-success: S;" in css and "--color-notify-error: NE;" in css
    assert "--color-notify-warning: W;" in css and "--color-notify-info: I;" in css
    assert "--notify-style: accent;" in css and "--notify-radius: 5px;" in css
    assert "--notify-shadow: soft;" in css and "--notify-timer: on;" in css
    assert "--notify-density: comfortable;" in css and "--color-focus-ring: F;" in css


def test_generate_css_variables_custom_look():
    theme = ThemeColors(notify_style="glass", notify_shadow="strong", notify_timer="off", notify_density="compact")
    css = tm.generate_css_variables(theme)
    for text in ("--notify-style: glass;", "--notify-shadow: strong;", "--notify-timer: off;",
                 "--notify-density: compact;", "--notify-radius: 12px;"):
        assert text in css


def test_generate_preview_css_defaults_for_empty_theme():
    css = tm.generate_preview_css_variables(ThemeColors())
    assert ".theme-preview-container,\n.theme-editor-panel {" in css
    for text in ("--preview-theme-type: dark;", "--preview-color-primary: #1976d2;",
                 "--preview-color-bg-base: #121212;", "--preview-color-text-primary: #ffffff;",
                 "--preview-color-notify-success: #4caf50;", "--preview-color-notify-info: #2196f3;",
                 "--preview-color-focus-ring: rgba(25, 118, 210, 0.5);"):
        assert text in css


def test_generate_preview_css_uses_theme_and_status_fallbacks():
    theme = ThemeColors(theme_type="light", primary="P", success="S", notify_warning="NW", error="E")
    css = tm.generate_preview_css_variables(theme)
    assert "--preview-theme-type: light;" in css and "--preview-color-primary: P;" in css
    assert "--preview-color-notify-success: S;" in css
    assert "--preview-color-notify-warning: NW;" in css
    assert "--preview-color-notify-error: E;" in css


def test_help_system_css_uses_theme_variables():
    assert "var(--color-text-primary)" in tm.HELP_SYSTEM_CSS
    assert tm.HELP_SYSTEM_CSS.count("{") == tm.HELP_SYSTEM_CSS.count("}")


# --------------------------------------------------------------------------- singleton

def test_singleton_and_default_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ThemeManager, "_instance", None)
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    a = tm.get_theme_manager()
    b = ThemeManager()
    assert a is b is ThemeManager.get_instance()
    assert ThemeManager._themes_dir == tmp_path / "themes"


def test_get_instance_creates_once(manager):
    assert ThemeManager.get_instance() is manager


# --------------------------------------------------------------------------- loading

def test_load_themes_creates_missing_dir(manager, themes_dir):
    assert manager.load_themes_from_directory() == []
    assert themes_dir.is_dir()
    assert manager._catalog_loaded is True


def test_load_themes_without_dir_configured(manager, monkeypatch):
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    assert manager.load_themes_from_directory() == []


def test_load_themes_scans_root_and_custom(manager, catalog, caplog):
    write_theme(catalog, "broken.json", "{not json")
    write_theme(catalog, "unknown_key.json", {"name": "x", "nope": 1})
    write_theme(catalog, "nameless.json", {"display_name": "No name"})
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        result = manager.load_themes_from_directory()
    assert result == [("dark", "Dark"), ("light", "Light"), ("mine", "Mine"), ("nord", "Nord")]
    assert "Error loading theme file" in caplog.text


def test_load_themes_per_file_exception_logged(manager, catalog, monkeypatch, caplog):
    def boom(self, path):
        raise RuntimeError("weird")

    monkeypatch.setattr(ThemeManager, "_load_theme_from_file", boom)
    with caplog.at_level(logging.WARNING, logger=tm.logger.name):
        assert manager.load_themes_from_directory() == []
    assert "Failed to load theme from" in caplog.text


def test_load_themes_clears_stale_entries(manager, catalog):
    manager._loaded_themes["ghost"] = ThemeColors(name="ghost")
    manager.load_themes_from_directory()
    assert "ghost" not in manager._loaded_themes


def test_load_theme_from_file(manager, tmp_path):
    p = write_theme(tmp_path, "t.json", theme_dict("t", notify_style="chip"))
    theme = manager._load_theme_from_file(p)
    assert theme.name == "t" and theme.notify_style == "chip"
    assert manager._load_theme_from_file(tmp_path / "missing.json") is None


def test_find_theme_file(manager, catalog, monkeypatch):
    assert manager._find_theme_file("nord") == catalog / "nord.json"
    assert manager._find_theme_file("dark") == catalog / "dark_theme.json"
    assert manager._find_theme_file("mine") == catalog / "custom" / "mine.json"
    assert manager._find_theme_file("absent") is None
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    assert manager._find_theme_file("nord") is None


def test_load_named_theme(manager, catalog, themes_dir):
    theme = manager._load_named_theme("nord")
    assert theme.display_name == "Nord" and manager._loaded_themes == {"nord": theme}
    assert manager._catalog_loaded is False
    assert manager._load_named_theme("absent") is None
    write_theme(themes_dir, "bad.json", "{")
    assert manager._load_named_theme("bad") is None
    write_theme(themes_dir, "anon.json", {"display_name": "Anon"})
    assert manager._load_named_theme("anon") is None


@pytest.mark.parametrize("name", [None, "", "   ", 5])
def test_load_active_theme_bad_name_uses_dark(manager, catalog, name):
    theme = manager.load_active_theme(name)
    assert theme.name == "dark"
    assert set(manager._loaded_themes) == {"dark"}  # no full scan needed


def test_load_active_theme_loads_single_file(manager, catalog):
    theme = manager.load_active_theme("  nord ")
    assert theme.name == "nord"
    assert manager._catalog_loaded is False
    assert manager.load_active_theme("nord") is theme  # cached


def test_load_active_theme_falls_back_to_full_scan(manager, catalog, themes_dir):
    # Stored name differs from the filename: only the full scan finds it.
    write_theme(themes_dir, "file_name_differs.json", theme_dict("ocean", "Ocean"))
    assert manager.load_active_theme("ocean").name == "ocean"
    assert manager._catalog_loaded is True


def test_load_active_theme_unknown_returns_dark(manager, catalog):
    assert manager.load_active_theme("nope").name == "dark"


def test_load_active_theme_with_loaded_catalog(manager, catalog):
    manager.load_themes_from_directory()
    assert manager.load_active_theme("nord").name == "nord"
    assert manager.load_active_theme("nope").name == "dark"


def test_get_available_themes_loads_once(manager, catalog, monkeypatch):
    first = manager.get_available_themes()
    assert [n for n, _ in first] == ["dark", "light", "mine", "nord"]
    monkeypatch.setattr(ThemeManager, "load_themes_from_directory", lambda self: pytest.fail("cached"))
    assert manager.get_available_themes() == first


# --------------------------------------------------------------------------- lookups

def test_get_theme_by_name_single_file_then_fallback(manager, catalog, caplog):
    assert manager.get_theme_by_name("nord").name == "nord"
    with caplog.at_level(logging.WARNING, logger=tm.logger.name):
        assert manager.get_theme_by_name("nope").name == "dark"
    assert "Theme 'nope' not found, falling back to 'dark'" in caplog.text


def test_get_theme_by_name_with_catalog_loaded(manager, catalog):
    manager.load_themes_from_directory()
    assert manager.get_theme_by_name("mine").name == "mine"
    assert manager.get_theme_by_name("nope").name == "dark"


def test_get_theme_by_name_without_any_themes(manager, themes_dir):
    assert manager.get_theme_by_name("x") is None
    assert manager._catalog_loaded is True


def test_display_name_and_type(manager, catalog):
    manager.load_themes_from_directory()
    assert manager.get_theme_display_name("nord") == "Nord"
    assert manager.get_theme_display_name("zzz") == "zzz"
    assert manager.get_theme_type("light") == "light"
    assert manager.get_theme_type("zzz") == "dark"


def test_get_theme_current_and_default(manager, catalog, themes_dir, monkeypatch):
    assert manager.get_theme().name == "dark"
    empty = themes_dir.parent / "empty"
    monkeypatch.setattr(ThemeManager, "_themes_dir", empty)
    monkeypatch.setattr(ThemeManager, "_loaded_themes", {})
    fresh = ThemeColors()
    m2 = manager
    m2._catalog_loaded = False
    assert m2.get_theme() == fresh


def test_set_theme_known(manager, catalog, caplog):
    with caplog.at_level(logging.INFO, logger=tm.logger.name):
        manager.set_theme(" nord ")
    assert manager._current_theme == "nord"
    assert "Theme changed to: nord" in caplog.text
    assert manager._catalog_loaded is False  # single-file load sufficed


def test_set_theme_unknown_falls_back(manager, catalog, caplog):
    with caplog.at_level(logging.WARNING, logger=tm.logger.name):
        manager.set_theme("nope")
    assert manager._current_theme == "dark"
    assert manager._catalog_loaded is True
    assert "Theme 'nope' not found" in caplog.text


@pytest.mark.parametrize("name", [None, "", "  "])
def test_set_theme_blank_means_dark(manager, catalog, name):
    manager._current_theme = "nord"
    manager.set_theme(name)
    assert manager._current_theme == "dark"


def test_set_theme_already_loaded(manager, catalog, monkeypatch):
    manager.load_themes_from_directory()
    monkeypatch.setattr(ThemeManager, "_load_named_theme", lambda self, n: pytest.fail("no reload"))
    manager.set_theme("light")
    assert manager._current_theme == "light"


def test_set_theme_reloads_empty_catalog(manager, catalog):
    manager._catalog_loaded = True  # flagged loaded, but cache was emptied
    manager.set_theme("mine")
    assert manager._current_theme == "mine"


def test_theme_name_exists(manager, catalog):
    assert manager.theme_name_exists("mine") is True
    assert manager.theme_name_exists("zzz") is False


@pytest.mark.parametrize("kind", ["dark", "light", "other"])
def test_default_colors_for_type(manager, kind):
    colors = manager.get_default_colors_for_type(kind)
    expected_base = "rgb(245, 245, 250)" if kind == "light" else "rgb(18, 18, 24)"
    assert colors["bg_base"] == expected_base
    assert set(colors) <= set(FIELDS)
    # every key is a real field, so the defaults build a valid theme
    ThemeColors(**colors)


def test_is_protected_theme(manager):
    assert PROTECTED_THEMES == {"dark", "light"}
    assert manager.is_protected_theme("dark") and manager.is_protected_theme("light")
    assert not manager.is_protected_theme("nord")


# --------------------------------------------------------------------------- persistence

def test_theme_to_dict_defaults(manager):
    d = manager._theme_to_dict(ThemeColors(name="x", primary="P"))
    assert d["display_name"] == "x" and d["theme_type"] == "dark" and d["primary"] == "P"
    ThemeColors(**d)  # loads back


def test_save_custom_theme(manager, catalog):
    theme = ThemeColors(name="ocean", display_name="Ocean", theme_type="light", primary="rgb(0, 0, 255)")
    assert manager.save_custom_theme(theme) is True
    path = catalog / "custom" / "ocean.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "ocean" and data["primary"] == "rgb(0, 0, 255)" and data["theme_type"] == "light"
    assert manager._loaded_themes["ocean"] is theme


def test_save_custom_theme_creates_custom_dir(manager, themes_dir):
    assert manager.save_custom_theme(ThemeColors(name="first")) is True
    assert (themes_dir / "custom" / "first.json").is_file()


@pytest.mark.parametrize("name", ["", "   "])
def test_save_custom_theme_rejects_empty_name(manager, catalog, name, caplog):
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.save_custom_theme(ThemeColors(name=name)) is False
    assert "Theme name cannot be empty" in caplog.text


def test_save_custom_theme_rejects_duplicate(manager, catalog, caplog):
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.save_custom_theme(ThemeColors(name="nord")) is False
    assert "Theme 'nord' already exists" in caplog.text


def test_save_custom_theme_without_dir(manager, monkeypatch):
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    assert manager.save_custom_theme(ThemeColors(name="x")) is False


def test_save_custom_theme_write_error(manager, catalog, monkeypatch, caplog):
    import builtins

    real_open = builtins.open

    def bad_open(file, mode="r", *a, **k):
        if "w" in mode:
            raise PermissionError("read-only")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", bad_open)
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.save_custom_theme(ThemeColors(name="ro")) is False
    assert "Failed to save theme 'ro': read-only" in caplog.text
    assert "ro" not in manager._loaded_themes


def test_update_custom_theme(manager, catalog):
    updated = ThemeColors(name="nord", display_name="Nord 2", primary="NEW")
    assert manager.update_custom_theme(updated) is True
    data = json.loads((catalog / "nord.json").read_text(encoding="utf-8"))
    assert data["primary"] == "NEW" and data["display_name"] == "Nord 2"
    assert manager._loaded_themes["nord"] is updated


def test_update_custom_theme_suffixed_file(manager, catalog):
    assert manager.update_custom_theme(ThemeColors(name="dark", primary="X")) is True
    assert json.loads((catalog / "dark_theme.json").read_text())["primary"] == "X"


def test_update_custom_theme_failures(manager, catalog, monkeypatch, caplog):
    assert manager.update_custom_theme(ThemeColors(name=" ")) is False
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.update_custom_theme(ThemeColors(name="absent")) is False
    assert "Theme file for 'absent' not found" in caplog.text
    monkeypatch.setattr(tm.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.update_custom_theme(ThemeColors(name="nord")) is False
    assert "Failed to update theme 'nord': disk full" in caplog.text
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    assert manager.update_custom_theme(ThemeColors(name="nord")) is False


def test_saved_theme_round_trips_every_field(manager, catalog):
    theme = ThemeColors(name="ocean", display_name="Ocean", theme_type="dark", notify_style="glass",
                        notify_radius="4", notify_shadow="strong", notify_timer="off", notify_density="compact")
    assert manager.save_custom_theme(theme) is True
    reloaded = manager._load_theme_from_file(catalog / "custom" / "ocean.json")
    assert reloaded == theme


def test_delete_theme(manager, catalog):
    assert manager.delete_theme("mine") is True
    assert not (catalog / "custom" / "mine.json").exists()
    assert "mine" not in manager._loaded_themes
    assert manager._catalog_loaded is True  # loaded on demand


@pytest.mark.parametrize("name", sorted(PROTECTED_THEMES))
def test_delete_protected_theme_refused(manager, catalog, name, caplog):
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.delete_theme(name) is False
    assert (catalog / f"{name}_theme.json").exists()
    assert f"Cannot delete protected theme: {name}" in caplog.text


def test_delete_theme_failures(manager, catalog, monkeypatch, caplog):
    assert manager.delete_theme("absent") is False

    def bad_unlink(self, *a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    with caplog.at_level(logging.ERROR, logger=tm.logger.name):
        assert manager.delete_theme("nord") is False
    assert "Failed to delete theme 'nord': locked" in caplog.text
    assert "nord" in manager._loaded_themes
    monkeypatch.setattr(ThemeManager, "_themes_dir", None)
    assert manager.delete_theme("nord") is False


def test_delete_theme_with_loaded_catalog_does_not_rescan(manager, catalog, monkeypatch):
    manager.load_themes_from_directory()
    monkeypatch.setattr(ThemeManager, "load_themes_from_directory", lambda self: pytest.fail("no rescan"))
    assert manager.delete_theme("nord") is True

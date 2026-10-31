# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/theme_event_colors.py and the event colours in the shipped themes."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import theme_event_colors as ev  # noqa: E402
from modules.theme_manager import ThemeColors, ThemeManager, generate_css_variables  # noqa: E402
from modules.uiwindows.activity_feed_model import KIND_META  # noqa: E402

THEME_FILES = sorted((ROOT / "themes").glob("*_theme.json"))
KEYS = [key for key, *_ in ev.EVENT_KINDS]
_RGB = re.compile(r"rgb\((\d+), (\d+), (\d+)\)")


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(int(x)) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_kind_has_a_field_and_feed_kind():
    assert len(KEYS) == len(set(KEYS)) == 17
    assert ev.EVENT_COLOR_FIELDS == tuple(f"event_{k}" for k in KEYS)
    for field in ev.EVENT_COLOR_FIELDS:
        assert hasattr(ThemeColors(), field)
    feed_kinds = {("other" if k == "alert" else k) for k in KIND_META}
    assert feed_kinds == set(KEYS), "every feed kind has a theme colour"
    groups = ev.event_groups()
    assert [g for g, _ in groups] == ["Twitch", "Tips", "YouTube", "Other"]
    assert sum(len(fields) for _, fields in groups) == 17


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_shipped_themes_carry_their_palette(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    palette = ev.BUILTIN_EVENT_COLORS[data["name"]]
    assert set(palette) == set(KEYS)
    for key in KEYS:
        assert data[f"event_{key}"] == palette[key]
        assert _RGB.fullmatch(palette[key]), palette[key]


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_event_colours_read_on_their_background(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["name"] == "dark":
        return  # Dark keeps the colours the feed always used
    bg = _RGB.fullmatch(data["bg_base"]).groups()
    for key in KEYS:
        assert _contrast(_RGB.fullmatch(data[f"event_{key}"]).groups(), bg) >= 3.0, key


def test_dark_keeps_the_previous_colours():
    dark = json.loads((ROOT / "themes" / "dark_theme.json").read_text(encoding="utf-8"))
    for key, *_rest, status in ev.EVENT_KINDS:
        assert dark[f"event_{key}"] == dark[status], key


def test_fill_builtin_and_fallbacks():
    theme = ThemeColors(name="mocha", primary="rgb(1, 2, 3)", event_raid="rgb(9, 9, 9)")
    ev.fill_builtin_event_colors(theme)
    assert theme.event_raid == "rgb(9, 9, 9)", "a value in the file wins"
    assert theme.event_follow == ev.BUILTIN_EVENT_COLORS["mocha"]["follow"]
    custom = ThemeColors(name="my theme", primary="rgb(1, 2, 3)", success="rgb(4, 5, 6)")
    ev.fill_builtin_event_colors(custom)
    assert custom.event_follow == "" and ev.event_color(custom, "follow") == "rgb(1, 2, 3)"
    assert ev.event_color(custom, "donation") == "rgb(4, 5, 6)"
    assert ev.event_color(SimpleNamespace(), "follow") == ""
    css = generate_css_variables(custom)
    assert "--color-event-follow: rgb(1, 2, 3);" in css and "--color-event-other: rgb(1, 2, 3);" in css


def test_theme_manager_fills_older_builtin_files_and_saves_event_colours(tmp_path, monkeypatch):
    old = json.loads((ROOT / "themes" / "nord_dark_theme.json").read_text(encoding="utf-8"))
    for field in ev.EVENT_COLOR_FIELDS:
        old.pop(field)
    path = tmp_path / "nord_dark_theme.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    manager = ThemeManager.get_instance()
    theme = manager._load_theme_from_file(path)
    assert theme.event_follow == ev.BUILTIN_EVENT_COLORS["nord_dark"]["follow"]
    saved = manager._theme_to_dict(theme)
    assert all(saved[field] for field in ev.EVENT_COLOR_FIELDS)
    for kind in ("dark", "light"):
        defaults = manager.get_default_colors_for_type(kind)
        assert defaults["event_bits"] == ev.BUILTIN_EVENT_COLORS[kind]["bits"]

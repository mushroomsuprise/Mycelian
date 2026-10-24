# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Alerts tab layout helpers: list badges, reward labels, file-card thumbnails, stylesheet."""

from __future__ import annotations

from types import SimpleNamespace

from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.uiwindows import alertsettings as al


def test_item_flags_follow_alert_data():
    assert al._item_flags({}) == []
    assert al._item_flags(None) == []
    flags = al._item_flags(
        {"single_audio_name": "a.mp3", "gif_name": "a.gif", "randomized_extra": True, "tts_enabled": True}
    )
    assert [icon for icon, _tip in flags] == ["volume_up", "image", "shuffle", "record_voice_over"]


def test_points_label_parses_title_and_cost():
    match = al._POINTS_LABEL_RE.match("Spin the wheel (2500 points)")
    assert match and match.group("title") == "Spin the wheel" and match.group("cost") == "2500"
    assert al._POINTS_LABEL_RE.match("Odd (title) (10 points)").group("title") == "Odd (title)"
    assert al._POINTS_LABEL_RE.match("No cost here") is None


def test_overlay_media_url(monkeypatch):
    from modules import web_engine

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(is_running=True, port=5123), raising=False)
    assert al._overlay_media_url("/assets/gifs/bits/", "cheer 500.gif") == "http://127.0.0.1:5123/assets/gifs/bits/cheer%20500.gif"
    assert al._overlay_media_url("/assets/gifs", "a.png").endswith("/assets/gifs/a.png")
    assert al._overlay_media_url("/assets/sounds/", "a.mp3") == ""  # not an image
    assert al._overlay_media_url("C:/Stream/gifs", "a.gif") == ""  # not served by the overlay server
    assert al._overlay_media_url("", "a.gif") == ""
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(is_running=False, port=5123), raising=False)
    assert al._overlay_media_url("/assets/gifs/", "a.gif") == ""


def test_alert_stylesheets_are_well_formed():
    from modules.uiwindows.alerts_styles import ALERTS_CSS, ALERTS_TAB_CSS

    out = promote_color_importance(scope_selectors(ALERTS_CSS, ".alr"))
    assert out.count("{") == out.count("}")
    assert ".alr .alr .alr-" not in out
    assert ".alr-tab-count" in ALERTS_TAB_CSS and ".alr " not in ALERTS_TAB_CSS


def test_every_tab_has_an_icon_and_amount_types_have_units():
    assert set(al._ALERT_TAB_ICONS) == set(al.ALERT_TAB_TO_TYPE)
    assert set(al._TRIGGER_UNITS) == al._QUANTITY_ALERT_TYPES

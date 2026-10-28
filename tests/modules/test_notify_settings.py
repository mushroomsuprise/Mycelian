# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.notify_settings`` (levels, sources and notification preferences)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace

import pytest

from modules import notify_settings as ns


def test_source_tables_are_consistent():
    ids = [sid for sid, _l, _i in ns.SOURCES]
    assert len(ids) == len(set(ids))
    assert ns.SOURCE_IDS == frozenset(ids)
    assert ns.SOURCE_LABELS["psn"] == "PlayStation"
    assert ns.SOURCE_ICONS["obs"] == "videocam"
    assert set(ns.LEVEL_LABELS) == set(ns.LEVELS)
    assert set(ns.DEFAULT_DURATIONS) == set(ns.LEVELS)


@pytest.mark.parametrize(
    "given,expected",
    [
        ("negative", "error"), ("ERROR", "error"), (" err ", "error"), ("critical", "error"),
        ("warning", "warning"), ("warn", "warning"),
        ("positive", "success"), ("success", "success"), ("ok", "success"),
        ("info", "info"), ("ongoing", "ongoing"), (None, "info"), ("", "info"), ("weird", "info"),
    ],
)
def test_level_of(given, expected):
    assert ns.level_of(given) == expected


def test_filter_level_maps_ongoing_to_info():
    assert ns.filter_level("ongoing") == "info"
    for level in ns.LEVELS:
        assert ns.filter_level(level) == level


@pytest.mark.parametrize("given,expected", [("OBS", "obs"), (" twitch ", "twitch"), (None, "app"), ("nope", "app")])
def test_normalize_source(given, expected):
    assert ns.normalize_source(given) == expected


def test_source_label():
    assert ns.source_label("spotify") == "Spotify"
    assert ns.source_label("unknown") == "App"
    assert ns.source_label(None) == "App"


def test_defaults():
    p = ns.NotifyPrefs()
    assert p.popups is True
    assert p.levels == ns.DEFAULT_LEVELS and p.levels is not ns.DEFAULT_LEVELS
    assert p.durations == ns.DEFAULT_DURATIONS
    assert p.position == ns.DEFAULT_POSITION
    assert p.max_visible == ns.DEFAULT_MAX_VISIBLE
    assert p.desktop_when_minimized is True and p.record_muted is True
    assert p.muted == {}


def test_validation_of_every_field():
    p = ns.NotifyPrefs(
        popups=0,
        levels={"error": 0, "bogus": True, "info": "yes"},
        durations={"warning": "12", "info": -5, "success": 10**9, "error": "abc", "bogus": 3},
        position="middle",
        max_visible=99,
        desktop_when_minimized=0,
        record_muted=None,
        muted={"obs": ["error", "nope", "error"], "notasource": ["error"], "twitch": [], "psn": None,
               "spotify": ["info", "warning"]},
    )
    assert p.popups is False
    assert p.levels == {"error": False, "warning": True, "info": True, "success": True}
    assert p.durations == {"error": 0, "warning": 12.0, "info": 0.0, "success": ns.MAX_DURATION_SECONDS}
    assert p.position == ns.DEFAULT_POSITION
    assert p.max_visible == 10
    assert p.desktop_when_minimized is False and p.record_muted is False
    assert p.muted == {"obs": ["error"], "spotify": ["info", "warning"]}


@pytest.mark.parametrize("given,expected", [(0, 1), (-3, 1), ("5", 5), ("x", ns.DEFAULT_MAX_VISIBLE), (None, ns.DEFAULT_MAX_VISIBLE)])
def test_max_visible_clamped(given, expected):
    assert ns.NotifyPrefs(max_visible=given).max_visible == expected


def test_valid_position_kept():
    assert ns.NotifyPrefs(position="bottom-left").position == "bottom-left"


def test_from_app_settings_none_gives_defaults():
    assert ns.NotifyPrefs.from_app_settings(None).to_settings() == ns.NotifyPrefs().to_settings()


def test_from_app_settings_reads_attributes():
    settings = SimpleNamespace(
        notifications_enabled=False, notify_levels={"info": False}, notify_durations={"info": 7},
        notify_position="top-center", notify_max_visible=2, notify_desktop_when_minimized=False,
        notify_record_muted=False, notify_muted={"obs": ["warning"]},
    )
    p = ns.NotifyPrefs.from_app_settings(settings)
    assert p.popups is False and p.levels["info"] is False and p.durations["info"] == 7.0
    assert p.position == "top-center" and p.max_visible == 2
    assert p.desktop_when_minimized is False and p.record_muted is False
    assert p.muted == {"obs": ["warning"]}


def test_from_app_settings_missing_attributes_use_defaults():
    p = ns.NotifyPrefs.from_app_settings(SimpleNamespace())
    assert p.to_settings() == ns.NotifyPrefs().to_settings()


def test_level_on_source_on_show_popup_is_muted():
    p = ns.NotifyPrefs(levels={"info": False}, muted={"obs": ["warning"]})
    assert p.level_on("error") and not p.level_on("info") and not p.level_on("ongoing")
    assert p.level_on("unknown")
    assert not p.source_on("obs", "warning") and p.source_on("obs", "error")
    assert not p.source_on("OBS", "warning")
    assert p.show_popup("error", "obs")
    assert not p.show_popup("warning", "obs")
    assert not p.show_popup("info", "app")
    assert p.is_muted("warning", "obs") and p.is_muted("ongoing", "app")
    assert not p.is_muted("error", "twitch")
    off = ns.NotifyPrefs(popups=False)
    assert not off.show_popup("error", "app")
    assert not off.is_muted("error", "app")  # master switch is not muting


def test_duration_ms():
    p = ns.NotifyPrefs(durations={"warning": 2.5})
    assert p.duration_ms("warning") == 2500
    assert p.duration_ms("warning", sticky=True) == 0
    assert p.duration_ms("ongoing") == 0
    assert p.duration_ms("error") == 0
    assert p.duration_ms("unknown-level") == 4000


def test_for_client_and_to_settings_round_trip():
    p = ns.NotifyPrefs(position="bottom-right", max_visible=3, muted={"twitch": ["info"]})
    client = p.for_client()
    assert client == {
        "popups": True,
        "levels": ns.DEFAULT_LEVELS,
        "durations": {"error": 0, "warning": 8000, "info": 4000, "success": 3000},
        "position": "bottom-right",
        "max": 3,
    }
    out = p.to_settings()
    assert out["notify_muted"] == {"twitch": ["info"]}
    out["notify_muted"]["twitch"].append("error")
    assert p.muted == {"twitch": ["info"]}  # copies, not live references
    again = ns.NotifyPrefs.from_app_settings(SimpleNamespace(**p.to_settings()))
    assert again.to_settings() == p.to_settings()


def test_current_prefs_reads_state_manager(monkeypatch):
    from modules import dataobjects

    fake = SimpleNamespace(get_app_settings=lambda: SimpleNamespace(notify_position="bottom-left"))
    monkeypatch.setattr(dataobjects, "state_manager", fake)
    assert ns.current_prefs().position == "bottom-left"


def test_current_prefs_falls_back_on_error(monkeypatch):
    from modules import dataobjects

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=boom))
    assert ns.current_prefs().to_settings() == ns.NotifyPrefs().to_settings()


@pytest.mark.parametrize("seconds,label", [(0, "Until dismissed"), (None, "Until dismissed"), (-1, "Until dismissed"),
                                           (5, "5 seconds"), (5.0, "5 seconds"), (2.5, "2.5 seconds")])
def test_duration_label(seconds, label):
    assert ns.duration_label(seconds) == label

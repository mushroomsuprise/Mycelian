# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Notification levels, sources (features) and the user's filtering preferences.

Pure helpers shared by the notification engine, the App Settings tab and the OBS
dock bridge. Nothing here touches NiceGUI.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

# Levels in display order. "ongoing" (work in progress) is filtered like "info".
LEVELS: Tuple[str, ...] = ("error", "warning", "info", "success")
LEVEL_LABELS: Dict[str, str] = {"error": "Errors", "warning": "Warnings", "info": "Info", "success": "Done"}
LEVEL_TAGS: Dict[str, str] = {"error": "Error", "warning": "Warning", "info": "Info", "success": "Done", "ongoing": "Working"}

DEFAULT_LEVELS: Dict[str, bool] = {level: True for level in LEVELS}
# Seconds a pop-up stays up; 0 means until dismissed.
DEFAULT_DURATIONS: Dict[str, float] = {"error": 0, "warning": 8, "info": 4, "success": 3}
# Longest a pop-up can be set to stay (seconds); 0 means until dismissed.
MAX_DURATION_SECONDS = 86400.0
POSITIONS: Dict[str, str] = {
    "top-right": "Top right",
    "bottom-right": "Bottom right",
    "top-center": "Top center",
    "bottom-left": "Bottom left",
}
DEFAULT_POSITION = "top-right"
DEFAULT_MAX_VISIBLE = 4

# (id, label, Material icon). Order is the order shown in App Settings.
SOURCES: Tuple[Tuple[str, str, str], ...] = (
    ("app", "App", "tune"),
    ("updates", "Updates", "system_update"),
    ("database", "Database", "storage"),
    ("theme", "Theme", "palette"),
    ("alerts", "Alerts", "campaign"),
    ("activity_feed", "Activity Feed", "dynamic_feed"),
    ("source_settings", "Source Settings", "tune"),
    ("source_controls", "Source Controls", "gamepad"),
    ("spore_studio", "Spore Studio", "auto_awesome"),
    ("chatbot", "Chatbot", "smart_toy"),
    ("connectors", "Connectors", "hub"),
    ("statistics", "Statistics", "insights"),
    ("overlay_server", "Overlay server", "dns"),
    ("obs", "OBS", "videocam"),
    ("twitch", "Twitch", "live_tv"),
    ("youtube", "YouTube", "smart_display"),
    ("discord", "Discord", "forum"),
    ("spotify", "Spotify", "music_note"),
    ("psn", "PlayStation", "sports_esports"),
    ("streamlabs", "Streamlabs", "volunteer_activism"),
    ("streamelements", "StreamElements", "volunteer_activism"),
    ("game_hooks", "Game Hooks", "videogame_asset"),
    ("files", "Files", "folder"),
    ("help", "Help", "help"),
)
SOURCE_IDS = frozenset(sid for sid, _label, _icon in SOURCES)
SOURCE_LABELS: Dict[str, str] = {sid: label for sid, label, _icon in SOURCES}
SOURCE_ICONS: Dict[str, str] = {sid: icon for sid, _label, icon in SOURCES}

_TYPE_TO_LEVEL = {
    "negative": "error", "error": "error", "err": "error", "critical": "error",
    "warning": "warning", "warn": "warning",
    "positive": "success", "success": "success", "ok": "success",
    "info": "info", "ongoing": "ongoing",
}
# Legacy Quasar type names, still stored in history for older readers.
LEVEL_TO_TYPE = {"error": "negative", "warning": "warning", "info": "info", "success": "positive", "ongoing": "ongoing"}


def level_of(notify_type: Optional[str]) -> str:
    """Map any accepted ``type`` (Quasar or plain) to error / warning / info / success / ongoing."""
    return _TYPE_TO_LEVEL.get(str(notify_type or "info").strip().lower(), "info")


def filter_level(level: str) -> str:
    """The level used for muting: work in progress counts as info."""
    return "info" if level == "ongoing" else level


def normalize_source(source: Optional[str]) -> str:
    sid = str(source or "app").strip().lower()
    return sid if sid in SOURCE_IDS else "app"


def source_label(source: Optional[str]) -> str:
    return SOURCE_LABELS.get(normalize_source(source), "App")


class NotifyPrefs:
    """A validated snapshot of the notification settings."""

    def __init__(
        self,
        *,
        popups: bool = True,
        levels: Optional[Dict[str, Any]] = None,
        durations: Optional[Dict[str, Any]] = None,
        position: str = DEFAULT_POSITION,
        max_visible: int = DEFAULT_MAX_VISIBLE,
        desktop_when_minimized: bool = True,
        record_muted: bool = True,
        muted: Optional[Dict[str, Iterable[str]]] = None,
    ) -> None:
        self.popups = bool(popups)
        self.levels = dict(DEFAULT_LEVELS)
        for level, on in (levels or {}).items():
            if level in self.levels:
                self.levels[level] = bool(on)
        self.durations = dict(DEFAULT_DURATIONS)
        for level, seconds in (durations or {}).items():
            if level in self.durations:
                try:
                    self.durations[level] = max(0.0, min(MAX_DURATION_SECONDS, float(seconds)))
                except (TypeError, ValueError):
                    pass
        self.position = position if position in POSITIONS else DEFAULT_POSITION
        try:
            self.max_visible = max(1, min(10, int(max_visible)))
        except (TypeError, ValueError):
            self.max_visible = DEFAULT_MAX_VISIBLE
        self.desktop_when_minimized = bool(desktop_when_minimized)
        self.record_muted = bool(record_muted)
        self.muted: Dict[str, List[str]] = {}
        for source, levels_off in (muted or {}).items():
            sid = str(source)
            if sid not in SOURCE_IDS:
                continue
            off = sorted({lv for lv in (levels_off or []) if lv in LEVELS})
            if off:
                self.muted[sid] = off

    @classmethod
    def from_app_settings(cls, settings: Any) -> "NotifyPrefs":
        if settings is None:
            return cls()
        return cls(
            popups=getattr(settings, "notifications_enabled", True),
            levels=getattr(settings, "notify_levels", None),
            durations=getattr(settings, "notify_durations", None),
            position=getattr(settings, "notify_position", DEFAULT_POSITION),
            max_visible=getattr(settings, "notify_max_visible", DEFAULT_MAX_VISIBLE),
            desktop_when_minimized=getattr(settings, "notify_desktop_when_minimized", True),
            record_muted=getattr(settings, "notify_record_muted", True),
            muted=getattr(settings, "notify_muted", None),
        )

    def level_on(self, level: str) -> bool:
        return self.levels.get(filter_level(level), True)

    def source_on(self, source: str, level: str) -> bool:
        return filter_level(level) not in self.muted.get(normalize_source(source), [])

    def show_popup(self, level: str, source: str) -> bool:
        """Should this notification pop up (it is recorded in history either way unless muted)?"""
        return self.popups and self.level_on(level) and self.source_on(source, level)

    def is_muted(self, level: str, source: str) -> bool:
        """Silenced by a level or feature setting (the master switch is not muting)."""
        return not (self.level_on(level) and self.source_on(source, level))

    def duration_ms(self, level: str, sticky: bool = False) -> int:
        """How long a pop-up stays up; 0 = until dismissed. Work in progress never times out."""
        if sticky or level == "ongoing":
            return 0
        return int(round(self.durations.get(level, 4) * 1000))

    def for_client(self) -> Dict[str, Any]:
        """What the browser renderer (and OBS docks) need."""
        return {
            "popups": self.popups,
            "levels": dict(self.levels),
            "durations": {lv: self.duration_ms(lv) for lv in LEVELS},
            "position": self.position,
            "max": self.max_visible,
        }

    def to_settings(self) -> Dict[str, Any]:
        return {
            "notifications_enabled": self.popups,
            "notify_levels": dict(self.levels),
            "notify_durations": dict(self.durations),
            "notify_position": self.position,
            "notify_max_visible": self.max_visible,
            "notify_desktop_when_minimized": self.desktop_when_minimized,
            "notify_record_muted": self.record_muted,
            "notify_muted": {k: list(v) for k, v in self.muted.items()},
        }


def current_prefs() -> NotifyPrefs:
    try:
        from .dataobjects import state_manager

        return NotifyPrefs.from_app_settings(state_manager.get_app_settings())
    except Exception:
        return NotifyPrefs()


def duration_label(seconds: float) -> str:
    seconds = float(seconds or 0)
    if seconds <= 0:
        return "Until dismissed"
    return f"{int(seconds) if seconds == int(seconds) else seconds} seconds"

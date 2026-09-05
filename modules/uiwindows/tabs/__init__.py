# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Per-tab settings components for Mycelian Settings UI.

Each tab module exposes a class implementing the following minimal API:
- name: str
- build(parent_container) -> None
- save() -> None
- discard() -> None
- on_enter() -> None
- on_exit() -> None

Tabs are intentionally independent and self-contained to avoid cross-tab state bleed.
Import tab classes from their modules (or from this package via ``__getattr__``)
so opening Settings does not load every integration stack up front.
"""

from .base import TabBase

__all__ = [
    "TabBase",
    "AppSettingsTab",
    "TwitchTab",
    "PSNTab",
    "SpotifyTab",
    "YouTubeTab",
    "DiscordTab",
    "ObsTab",
    "GameHooksTab",
    "DatabaseTab",
    "StatisticsTab",
    "ThemeTab",
]

_TAB_EXPORTS = {
    "AppSettingsTab": ".app_settings_tab",
    "TwitchTab": ".twitch_tab",
    "PSNTab": ".psn_tab",
    "SpotifyTab": ".spotify_tab",
    "YouTubeTab": ".youtube_tab",
    "DiscordTab": ".discord_tab",
    "ObsTab": ".obs_tab",
    "GameHooksTab": ".game_hooks_tab",
    "DatabaseTab": ".database_tab",
    "StatisticsTab": ".statistics_tab",
    "ThemeTab": ".theme_tab",
}


def __getattr__(name: str):
    module_name = _TAB_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    mod = import_module(module_name, __name__)
    return getattr(mod, name)

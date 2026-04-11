"""Per-tab settings components for Mycelian Settings UI.

Each tab module exposes a class implementing the following minimal API:
- name: str
- build(parent_container) -> None
- save() -> None
- discard() -> None
- on_enter() -> None
- on_exit() -> None

Tabs are intentionally independent and self-contained to avoid cross-tab state bleed.
"""

from .app_settings_tab import AppSettingsTab
from .base import TabBase  # re-export base class for convenience
from .database_tab import DatabaseTab
from .migration_tab import MigrationTab
from .psn_tab import PSNTab
from .spotify_tab import SpotifyTab
from .statistics_tab import StatisticsTab
from .streamlabs_tab import StreamlabsTab
from .theme_tab import ThemeTab
from .twitch_tab import TwitchTab
from .youtube_tab import YouTubeTab
from .game_hooks_tab import GameHooksTab

__all__ = [
    "TabBase",
    "AppSettingsTab",
    "TwitchTab",
    "StreamlabsTab",
    "PSNTab",
    "SpotifyTab",
    "YouTubeTab",
    "GameHooksTab",
    "DatabaseTab",
    "MigrationTab",
    "StatisticsTab",
    "ThemeTab",
]

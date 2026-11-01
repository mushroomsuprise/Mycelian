# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Steam state dataclasses (no Steam client import), mirrored to overlays as JSON."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SteamData:
    """Live Steam information for the tracked profile and the game it is playing.

    Achievement dicts (``current_game_achievements``, ``recent_unlocks``, ``last_unlock``) hold
    ``api_name``, ``name``, ``description``, ``icon_url``, ``icon_gray_url``, ``achieved``,
    ``unlock_time`` (unix seconds, 0 when locked), ``hidden`` and ``global_percent`` (or None).
    """

    # Profile
    steam_id: str | None = None
    persona_name: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None
    persona_state: str = "Offline"  # Online, Busy, Away, Snooze, Looking to trade, Looking to play
    is_online: bool = False
    steam_level: int | None = None
    connection_status: str = "Disconnected"
    # Current game
    current_game_name: str | None = None
    current_game_appid: int | None = None
    current_game_art_url: str | None = None  # Store header (460x215)
    current_game_capsule_url: str | None = None  # Library capsule (600x900)
    # Achievements for the current game
    has_achievements: bool = False
    achievements_private: bool = False  # Game details are private on the profile
    achievement_counts: dict[str, int] = field(default_factory=dict)  # {"unlocked": n, "total": m}
    current_game_progress: int | None = None  # Percent unlocked
    current_game_achievements: list[dict[str, Any]] = field(default_factory=list)
    recent_unlocks: list[dict[str, Any]] = field(default_factory=list)  # Newest first
    last_unlock: dict[str, Any] | None = None

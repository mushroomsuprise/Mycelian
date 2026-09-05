#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""PlayStation Network state dataclasses (no psnawp client import)."""

from dataclasses import dataclass, field


@dataclass
class PSNGameMismatch:
    """Tracks PSN game name mismatches between presence and trophy APIs."""

    presence_name: str  # Name from presence/social API
    np_title_id: str  # ID from presence data
    platform: str  # PS4, PS5, etc.
    detected_at: str  # ISO timestamp when mismatch was detected
    notified: bool = False  # Whether user has been notified about this mismatch


@dataclass
class PSNData:
    """Dataclass to store PSN information."""

    current_game_name: str | None = None
    current_game_art_url: str | None = None
    current_game_np_comm_id: str | None = (
        None  # np_communication_id for the current game
    )
    trophy_counts: dict[str, int] = field(
        default_factory=dict
    )  # e.g., {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    current_game_trophies: dict[str, int] = field(
        default_factory=dict
    )  # Trophies for the current game (base / selected trophy group)
    current_game_trophies_all: dict = field(
        default_factory=dict
    )  # All groups combined: {"earned": {...}, "defined": {...}} (base + DLC)
    current_game_progress: int | None = None
    all_games_data: dict[str, dict] = field(
        default_factory=dict
    )  # e.g., {"game_id": {"name": "Game Name", "icon_url": "...", ...}}
    npsso_code: str | None = None
    online_id: str | None = None
    account_id: str | None = None
    is_online: bool = False
    connection_status: str = "Disconnected"
    presence: dict = field(default_factory=dict)
    current_game_mismatch: PSNGameMismatch | None = None  # Active mismatch state

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/steam_data.py."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.steam_data import SteamData  # noqa: E402


def test_defaults_are_empty_and_independent():
    a, b = SteamData(), SteamData()
    a.recent_unlocks.append({"name": "x"})
    a.achievement_counts["total"] = 1
    assert b.recent_unlocks == [] and b.achievement_counts == {}
    assert (b.connection_status, b.persona_state, b.is_online, b.last_unlock) == ("Disconnected", "Offline", False, None)


def test_serialises_to_json_for_overlays():
    data = SteamData(persona_name="Mushy", current_game_appid=620, achievement_counts={"unlocked": 1, "total": 2})
    assert json.loads(json.dumps(asdict(data)))["current_game_appid"] == 620
    assert {f.name for f in fields(SteamData)} >= {
        "persona_name", "avatar_url", "current_game_name", "current_game_art_url", "achievement_counts",
        "current_game_progress", "current_game_achievements", "recent_unlocks", "last_unlock", "steam_level",
    }

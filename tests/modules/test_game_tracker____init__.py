# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""modules/game_tracker/__init__.py re-exports the core types."""

from modules import game_tracker
from modules.game_tracker import models


def test_package_exports_core_types():
    assert game_tracker.TrackedGame is models.TrackedGame
    assert game_tracker.GameEvent is models.GameEvent
    assert game_tracker.TrackerSettings is models.TrackerSettings
    assert set(game_tracker.__all__) == {"GameEvent", "TrackedGame", "TrackerSettings"}

"""Per-game memory hook implementations. Polling and Socket.IO live in game_hooks_service."""

from __future__ import annotations

from typing import Any, Optional

from .ff7_boss_tracker import Ff7BossTracker, is_boss_actor
from .ff7_hook import FF7_CONNECTOR_CATALOG, FF7Hook, FF7Reader

__all__ = [
    "FF7Hook",
    "FF7Reader",
    "FF7_CONNECTOR_CATALOG",
    "Ff7BossTracker",
    "create_hook_instance",
    "is_boss_actor",
]


def create_hook_instance(game_id: str) -> Optional[Any]:
    if game_id == "ff7":
        return FF7Hook()
    return None

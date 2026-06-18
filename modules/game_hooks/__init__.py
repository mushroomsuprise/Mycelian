# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Per-game memory hook implementations."""

from __future__ import annotations

from typing import Any, Optional

from .base import GameHook, HookUiMetadata, runtime_os_key
from .ff7_boss_tracker import Ff7BossTracker
from .ff7_hook import (
    FF7_CONNECTOR_CATALOG,
    FF7Hook,
    Ff7GameHook,
    catalog_entry_is_public,
    ff7_game_speed_select_options,
)
from .registry import (
    create_hook,
    enabled_db_path,
    is_hook_enabled,
    list_hooks_for_ui,
    registered_hook_ids,
)

__all__ = [
    "FF7Hook",
    "FF7Reader",
    "FF7_CONNECTOR_CATALOG",
    "Ff7BossTracker",
    "Ff7GameHook",
    "GameHook",
    "HookUiMetadata",
    "catalog_entry_is_public",
    "create_hook",
    "create_hook_instance",
    "enabled_db_path",
    "ff7_game_speed_select_options",
    "is_hook_enabled",
    "list_hooks_for_ui",
    "registered_hook_ids",
    "runtime_os_key",
]


# Backwards compatibility
FF7Reader = FF7Hook


def create_hook_instance(game_id: str) -> Optional[Any]:
    return create_hook(game_id)

"""Registry of available game hooks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Type

from ..database_manager import database_manager
from .base import GameHook, HookUiMetadata

logger = logging.getLogger(__name__)

_GAME_HOOKS_ROOT = "GameHooks"
_HOOK_TYPES: Dict[str, Type[Any]] = {}


def _register_hook(cls: Type[Any]) -> Type[Any]:
    _HOOK_TYPES[cls.hook_id] = cls
    return cls


def enabled_db_path(hook_id: str) -> str:
    return f"{_GAME_HOOKS_ROOT}/{hook_id}_enabled"


def is_hook_enabled(hook_id: str) -> bool:
    try:
        raw = database_manager.get_data(enabled_db_path(hook_id))
        if isinstance(raw, dict) and "enabled" in raw:
            return bool(raw["enabled"])
        if isinstance(raw, bool):
            return raw
    except Exception as e:
        logger.debug("is_hook_enabled(%s): %s", hook_id, e)
    return False


def list_hooks_for_ui() -> List[HookUiMetadata]:
    _ensure_registered()
    return [cls.ui for cls in _HOOK_TYPES.values()]


def registered_hook_ids() -> List[str]:
    _ensure_registered()
    return list(_HOOK_TYPES.keys())


def create_hook(hook_id: str) -> Optional[GameHook]:
    _ensure_registered()
    cls = _HOOK_TYPES.get(str(hook_id or "").strip().lower())
    if cls is None:
        return None
    return cls()


def _ensure_registered() -> None:
    if _HOOK_TYPES:
        return
    from .ff7_hook import Ff7GameHook

    _register_hook(Ff7GameHook)

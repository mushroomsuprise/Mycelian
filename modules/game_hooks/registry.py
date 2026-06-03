"""Registry of available game hooks."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Type

from ..database_manager import database_manager
from .base import GameHook, HookUiMetadata

logger = logging.getLogger(__name__)

_GAME_HOOKS_ROOT = "GameHooks"
_HOOK_TYPES: Dict[str, Type[Any]] = {}

_enabled_cache: Dict[str, bool] = {}
_enabled_cache_loaded = False
_enabled_cache_lock = threading.Lock()


def _register_hook(cls: Type[Any]) -> Type[Any]:
    _HOOK_TYPES[cls.hook_id] = cls
    return cls


def enabled_db_path(hook_id: str) -> str:
    return f"{_GAME_HOOKS_ROOT}/{hook_id}_enabled"


def _parse_enabled_value(raw: Any) -> bool:
    if isinstance(raw, dict) and "enabled" in raw:
        return bool(raw["enabled"])
    if isinstance(raw, bool):
        return raw
    return False


def refresh_hook_enabled_cache() -> None:
    """Load all hook enabled flags from the database (once per refresh)."""
    global _enabled_cache_loaded
    _ensure_registered()
    with _enabled_cache_lock:
        for hook_id in _HOOK_TYPES:
            try:
                raw = database_manager.get_data(enabled_db_path(hook_id))
                _enabled_cache[hook_id] = _parse_enabled_value(raw)
            except Exception as e:
                logger.debug("refresh_hook_enabled_cache(%s): %s", hook_id, e)
                _enabled_cache[hook_id] = False
        _enabled_cache_loaded = True


def set_hook_enabled_cached(hook_id: str, enabled: bool) -> None:
    """Update in-memory enabled flag after a settings save (no RTDB read)."""
    with _enabled_cache_lock:
        _enabled_cache[hook_id] = bool(enabled)
        _enabled_cache_loaded = True


def is_hook_enabled(hook_id: str) -> bool:
    global _enabled_cache_loaded
    _ensure_registered()
    with _enabled_cache_lock:
        if not _enabled_cache_loaded:
            refresh_hook_enabled_cache()
        return bool(_enabled_cache.get(hook_id, False))


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

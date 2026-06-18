# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Bridge NiceGUI native window_args into the webview subprocess (Windows spawn).

The native window runs in a separate process that imports a fresh ``nicegui.core.app``;
``app.native.window_args`` set in the main process is not visible there. This module
patches ``nicegui.native.native_mode._open_window`` so JSON in the environment
``MYCELIAN_NATIVE_WINDOW_ARGS`` is merged into ``core.app.native.window_args`` before
``webview.create_window`` runs.

The replacement must be a **module-level** function so multiprocessing spawn can pickle
``Process(target=native_mode._open_window, ...)`` on Windows (nested functions are not picklable).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_MYCELIAN_NATIVE_ENV = "MYCELIAN_NATIVE_WINDOW_ARGS"
_PATCH_ATTR = "_mycelian_native_window_bridge"
_ORIG_ATTR = "_mycelian_orig_open_window"


def _merge_window_args_from_env() -> None:
    raw = os.environ.get(_MYCELIAN_NATIVE_ENV, "")
    if not raw.strip():
        return
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid %s JSON; ignoring native window overrides", _MYCELIAN_NATIVE_ENV)
        return
    if not isinstance(extra, dict):
        return
    from nicegui import core

    for key, val in extra.items():
        if key == "min_size" and isinstance(val, list):
            val = tuple(val)
        core.app.native.window_args[key] = val


def _open_window_with_mycelian_env(*args, **kwargs):
    """Wrapper for ``nicegui.native.native_mode._open_window`` (must stay module-level for pickle).

    The positional signature of ``_open_window`` changed between NiceGUI 2.x and 3.x
    (3.x prepends ``protocol`` and adds ``event_sender``/``favicon``). To stay robust
    across versions we accept ``*args``/``**kwargs`` and forward them unchanged to the
    original implementation after merging native window args from the environment.
    """
    import nicegui.native.native_mode as nm

    _merge_window_args_from_env()
    orig = getattr(nm, _ORIG_ATTR, None)
    if orig is None:
        raise RuntimeError("native_window_bridge: original _open_window not installed")
    return orig(*args, **kwargs)


def _install_patch() -> None:
    try:
        import nicegui.native.native_mode as nm
    except Exception as e:
        logger.debug("NiceGUI native_mode not importable; skipping native bridge: %s", e)
        return

    if getattr(nm, _PATCH_ATTR, False):
        return

    setattr(nm, _ORIG_ATTR, nm._open_window)
    nm._open_window = _open_window_with_mycelian_env
    setattr(nm, _PATCH_ATTR, True)


_install_patch()

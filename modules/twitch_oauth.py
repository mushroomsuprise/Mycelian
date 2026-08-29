# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Serialize Twitch UserAuthenticator OAuth flows and ensure callback servers stop on shutdown.

twitchAPI binds a local server on port 17563; only one flow may run at a time.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_oauth_lock = threading.Lock()
_oauth_handoff_lock = threading.Lock()
_active_authenticator: Any = None


def is_oauth_in_progress() -> bool:
    """True while a UserAuthenticator.authenticate() call holds the global lock."""
    return _active_authenticator is not None


async def run_user_authentication(authenticator) -> Tuple[str, str]:
    """Run OAuth for one authenticator; blocks other callers until complete."""
    global _active_authenticator

    loop = asyncio.get_running_loop()
    cancelled = threading.Event()
    acquired_box = {"ok": False}

    def _acquire() -> bool:
        _oauth_lock.acquire()
        with _oauth_handoff_lock:
            if cancelled.is_set():
                _oauth_lock.release()
                return False
            acquired_box["ok"] = True
            return True

    try:
        got = await loop.run_in_executor(None, _acquire)
        if not got:
            raise asyncio.CancelledError()
        _active_authenticator = authenticator
        return await authenticator.authenticate()
    finally:
        with _oauth_handoff_lock:
            cancelled.set()
            if acquired_box["ok"]:
                _active_authenticator = None
                try:
                    _oauth_lock.release()
                except RuntimeError:
                    logger.debug("OAuth lock already released")
                acquired_box["ok"] = False


def stop_active_oauth(timeout: float = 2.0) -> None:
    """Stop the in-flight OAuth callback server, if any."""
    auth = _active_authenticator
    if auth is None:
        return

    try:
        auth.stop()
    except Exception as e:
        logger.debug("Error stopping active Twitch OAuth: %s", e)

    thread: Optional[threading.Thread] = getattr(auth, "_thread", None)
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("Twitch OAuth thread did not exit within %.1fs", timeout)

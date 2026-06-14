"""
Serialize Twitch UserAuthenticator OAuth flows and ensure callback servers stop on shutdown.

twitchAPI binds a local server on port 17563; only one flow may run at a time.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_oauth_lock = threading.Lock()
_active_authenticator: Any = None


def is_oauth_in_progress() -> bool:
    """True while a UserAuthenticator.authenticate() call holds the global lock."""
    return _active_authenticator is not None


async def run_user_authentication(authenticator) -> Tuple[str, str]:
    """Run OAuth for one authenticator; blocks other callers until complete."""
    global _active_authenticator

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _oauth_lock.acquire)
    _active_authenticator = authenticator
    try:
        return await authenticator.authenticate()
    finally:
        _active_authenticator = None
        _oauth_lock.release()


def stop_active_oauth(timeout: float = 2.0) -> None:
    """Stop the in-flight OAuth callback server, if any."""
    auth = _active_authenticator
    if auth is None:
        return

    try:
        auth.stop()
    except Exception as e:
        logger.debug("Error stopping active Twitch OAuth: %s", e)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(auth, "_is_closed", False):
            break
        thread: Optional[threading.Thread] = getattr(auth, "_thread", None)
        if thread is not None and not thread.is_alive():
            break
        time.sleep(0.05)

    thread = getattr(auth, "_thread", None)
    if thread is not None and thread.is_alive():
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
        if thread.is_alive():
            logger.warning("Twitch OAuth thread did not exit within %.1fs", timeout)

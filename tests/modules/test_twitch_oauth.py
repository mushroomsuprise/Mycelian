# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_oauth.py (serialized OAuth flows and shutdown)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import threading  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import twitch_oauth  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(twitch_oauth, "_active_authenticator", None)
    monkeypatch.setattr(twitch_oauth, "_oauth_lock", threading.Lock())
    monkeypatch.setattr(twitch_oauth, "_oauth_handoff_lock", threading.Lock())
    yield


def test_not_in_progress_by_default():
    assert twitch_oauth.is_oauth_in_progress() is False


def test_run_returns_tokens_and_releases_lock():
    seen = {}
    auth = MagicMock()

    async def authenticate():
        seen["active"] = twitch_oauth._active_authenticator
        return "a", "r"

    auth.authenticate = AsyncMock(side_effect=authenticate)
    result = asyncio.run(twitch_oauth.run_user_authentication(auth))
    assert result == ("a", "r")
    assert seen["active"] is auth
    assert twitch_oauth._active_authenticator is None
    assert not twitch_oauth._oauth_lock.locked()


def test_run_releases_lock_when_authenticate_raises():
    auth = MagicMock()
    auth.authenticate = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        asyncio.run(twitch_oauth.run_user_authentication(auth))
    assert not twitch_oauth._oauth_lock.locked()
    assert twitch_oauth.is_oauth_in_progress() is False


def test_run_cancelled_while_waiting_does_not_keep_lock():
    """A caller cancelled while waiting for the lock must give it back once it gets it."""
    twitch_oauth._oauth_lock.acquire()  # someone else holds the flow
    auth = MagicMock()
    auth.authenticate = AsyncMock(return_value=("x", "y"))

    async def go():
        task = asyncio.create_task(twitch_oauth.run_user_authentication(auth))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Release the holder; the executor thread then acquires, sees the cancel and releases.
        twitch_oauth._oauth_lock.release()
        for _ in range(100):
            await asyncio.sleep(0.005)
            if not twitch_oauth._oauth_lock.locked():
                break

    asyncio.run(go())
    assert not twitch_oauth._oauth_lock.locked()
    auth.authenticate.assert_not_called()


def test_run_raises_cancelled_when_acquire_reports_cancel(monkeypatch):
    class _Loop:
        async def run_in_executor(self, _ex, fn):
            return False

    auth = MagicMock()
    auth.authenticate = AsyncMock()

    async def go():
        monkeypatch.setattr(twitch_oauth.asyncio, "get_running_loop", lambda: _Loop())
        await twitch_oauth.run_user_authentication(auth)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())
    auth.authenticate.assert_not_called()


def test_finally_tolerates_lock_already_released():
    auth = MagicMock()

    async def authenticate():
        twitch_oauth._oauth_lock.release()  # released behind our back
        return "a", "b"

    auth.authenticate = AsyncMock(side_effect=authenticate)
    assert asyncio.run(twitch_oauth.run_user_authentication(auth)) == ("a", "b")
    assert twitch_oauth._active_authenticator is None


def test_stop_active_oauth_noop_without_authenticator():
    twitch_oauth.stop_active_oauth()  # no error


def test_stop_active_oauth_swallows_stop_errors_and_skips_dead_thread():
    auth = MagicMock()
    auth.stop.side_effect = RuntimeError("x")
    auth._thread = MagicMock()
    auth._thread.is_alive.return_value = False
    twitch_oauth._active_authenticator = auth
    twitch_oauth.stop_active_oauth()
    auth.stop.assert_called_once()
    auth._thread.join.assert_not_called()


def test_stop_active_oauth_without_thread_attr():
    auth = MagicMock(spec=["stop"])
    twitch_oauth._active_authenticator = auth
    twitch_oauth.stop_active_oauth()
    auth.stop.assert_called_once()


def test_stop_active_oauth_warns_when_thread_stays_alive(caplog):
    auth = MagicMock()
    thread = MagicMock()
    thread.is_alive.return_value = True
    auth._thread = thread
    twitch_oauth._active_authenticator = auth
    with caplog.at_level("WARNING", logger="modules.twitch_oauth"):
        twitch_oauth.stop_active_oauth(timeout=0.01)
    thread.join.assert_called_once_with(timeout=0.01)
    assert "did not exit" in caplog.text

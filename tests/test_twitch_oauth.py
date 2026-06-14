#!/usr/bin/env python3
"""Tests for serialized Twitch OAuth coordination."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from modules import twitch_oauth


class TwitchOAuthSerializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        twitch_oauth._active_authenticator = None
        if twitch_oauth._oauth_lock.locked():
            twitch_oauth._oauth_lock.release()

    async def test_concurrent_authenticate_calls_are_serialized(self) -> None:
        order: list[str] = []
        gate = asyncio.Event()

        async def slow_authenticate() -> tuple[str, str]:
            order.append("start")
            await gate.wait()
            order.append("end")
            return "access", "refresh"

        auth_a = MagicMock()
        auth_a.authenticate = AsyncMock(side_effect=slow_authenticate)
        auth_b = MagicMock()

        async def fast_authenticate() -> tuple[str, str]:
            order.append("b_start")
            order.append("b_end")
            return "access_b", "refresh_b"

        auth_b.authenticate = AsyncMock(side_effect=fast_authenticate)

        first = asyncio.create_task(
            twitch_oauth.run_user_authentication(auth_a)
        )
        for _ in range(100):
            if twitch_oauth.is_oauth_in_progress():
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("first OAuth call did not acquire the coordinator lock")

        second = asyncio.create_task(
            twitch_oauth.run_user_authentication(auth_b)
        )
        await asyncio.sleep(0.05)
        self.assertEqual(order, ["start"])

        gate.set()
        tokens_a = await first
        tokens_b = await second

        self.assertEqual(tokens_a, ("access", "refresh"))
        self.assertEqual(tokens_b, ("access_b", "refresh_b"))
        self.assertEqual(order, ["start", "end", "b_start", "b_end"])
        auth_a.authenticate.assert_awaited_once()
        auth_b.authenticate.assert_awaited_once()
        self.assertFalse(twitch_oauth.is_oauth_in_progress())

    def test_stop_active_oauth_signals_authenticator(self) -> None:
        auth = MagicMock()
        auth._is_closed = False
        auth._thread = threading.Thread(target=time.sleep, args=(0.2,))
        auth._thread.start()
        twitch_oauth._active_authenticator = auth

        twitch_oauth.stop_active_oauth(timeout=1.0)

        auth.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()

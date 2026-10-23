#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""EventSub loop stays responsive; replies keep their order; waits are bounded.

Covers TH-07/RT-04 (chatbot lane), TH-08 (timeouts honored), TH-09 (bounded
EventSub stop) and TH-10 (lock waits that cannot leak or block a loop).
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules import twitch as twitch_module
from modules.twitch import Twitch_API


def _chat(text: str, user: str = "viewer", uid: str = "1"):
    message = SimpleNamespace(text=text, fragments=None)
    event = SimpleNamespace(
        chatter_user_name=user,
        chatter_user_id=uid,
        message=message,
        message_id=f"id-{text}",
        color="",
        badges=None,
        message_type="text",
        reply=None,
    )
    return SimpleNamespace(event=event)


class _FakeChatbot:
    """Commands: !api waits 0.5 s (a Helix call), everything else answers at once."""

    def __init__(self, order: list):
        self.order = order
        self.threads: list = []

    def process_greeting(self, user_id, username):
        return None

    def message_may_call_api(self, msg):
        return msg.get("message", "").startswith("!api")

    def process_chat_message(self, msg):
        text = msg["message"]
        self.threads.append(threading.current_thread().name)
        if not text.startswith("!"):
            return None
        if text.startswith("!api"):
            time.sleep(0.5)
        return (f"reply to {text}", text[1:], ["twitch"], [], "chat")

    def process_event(self, event_type, data):
        return None


class ChatbotLaneTests(unittest.TestCase):
    def setUp(self):
        self.order: list = []
        self.bot = _FakeChatbot(self.order)
        lock = threading.Lock()

        def deliver(message, *_args, **_kwargs):
            with lock:
                self.order.append(message)
            return True

        giveaway = MagicMock()
        giveaway.try_register_entry.return_value = False
        poll = MagicMock()
        poll.try_moderator_start.return_value = False
        poll.try_register_vote.return_value = False
        self._patches = [
            patch.object(twitch_module, "get_chatbot_manager", return_value=self.bot),
            patch.object(twitch_module, "web_engine", SimpleNamespace(web_engine_instance=None)),
            patch.object(twitch_module, "statistics_manager", MagicMock()),
            patch("modules.chatbot.deliver_chatbot_output", deliver),
            patch("modules.giveaway_manager.get_giveaway_manager", return_value=giveaway),
            patch("modules.poll_manager.get_poll_manager", return_value=poll),
        ]
        for p in self._patches:
            p.start()
        self.api = object.__new__(Twitch_API)
        self.api._note_event_received = lambda: None

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def test_api_command_does_not_block_loop_and_replies_keep_order(self):
        async def main():
            ticks = 0
            done = asyncio.Event()

            async def ticker():
                nonlocal ticks
                while not done.is_set():
                    ticks += 1
                    await asyncio.sleep(0.01)

            tick_task = asyncio.create_task(ticker())
            loop = asyncio.get_running_loop()
            # twitchAPI creates one task per notification, in arrival order.
            tasks = [
                loop.create_task(self.api.on_chat_message(_chat("!api one"))),
                loop.create_task(self.api.on_chat_message(_chat("!fast two"))),
                loop.create_task(self.api.on_chat_message(_chat("hello"))),
            ]
            await asyncio.sleep(0.05)
            # An event reply (follow, sub...) arriving meanwhile queues behind them.
            twitch_module._dispatch_chatbot_event_response(("follow reply", ["twitch"]))
            await asyncio.gather(*tasks)
            await twitch_module.wait_for_chatbot_lane()
            done.set()
            await tick_task
            return ticks

        ticks = asyncio.run(main())
        self.assertGreater(ticks, 20, "socket loop was blocked during the API command")
        self.assertEqual(
            self.order,
            ["reply to !api one", "reply to !fast two", "follow reply"],
        )

    def test_plain_commands_still_run_inline_on_the_loop(self):
        async def main():
            await self.api.on_chat_message(_chat("!fast"))
            twitch_module._dispatch_chatbot_event_response(("follow reply", ["twitch"]))

        asyncio.run(main())
        self.assertEqual(self.order, ["reply to !fast", "follow reply"])
        self.assertEqual(self.bot.threads, [threading.current_thread().name])

    def test_lane_jobs_see_a_running_loop(self):
        seen = []

        def job():
            try:
                asyncio.get_running_loop()
                seen.append(True)
            except RuntimeError:
                seen.append(False)

        twitch_module._chatbot_lane.submit(job).result(2)
        self.assertEqual(seen, [True])


class ConnectorBarrierTests(unittest.TestCase):
    def test_connector_handler_runs_after_queued_chatbot_work(self):
        from modules.connector_integration import ConnectorIntegration

        order = []

        async def original(_data):
            twitch_module._chatbot_lane.submit(lambda: (time.sleep(0.2), order.append("chatbot")))

        async def connector(_data):
            order.append("connector")

        holder = SimpleNamespace(on_follow=original)
        integration = object.__new__(ConnectorIntegration)
        integration._original_twitch_handlers = {}
        integration._wrap_twitch_handler(holder, "on_follow", connector)
        asyncio.run(holder.on_follow(None))
        self.assertEqual(order, ["chatbot", "connector"])


class BoundedWaitTests(unittest.TestCase):
    def test_coro_timeout_is_honored_and_work_still_finishes(self):
        finished = threading.Event()

        async def slow():
            await asyncio.sleep(0.6)
            finished.set()
            return "late"

        async def caller():
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                twitch_module._run_twitch_coro_sync(slow(), timeout=0.1)
            return time.monotonic() - started

        elapsed = asyncio.run(caller())
        self.assertLess(elapsed, 0.4)
        self.assertTrue(finished.wait(2))

    def test_coro_success_returns_the_value(self):
        async def quick():
            return 42

        async def caller():
            return twitch_module._run_twitch_coro_sync(quick(), timeout=2)

        self.assertEqual(asyncio.run(caller()), 42)

    def test_stuck_eventsub_stop_no_longer_blocks_reconnect(self):
        never = threading.Event()

        class StuckEventSub:
            _socket_loop = None

            async def stop(self):
                await asyncio.get_running_loop().run_in_executor(None, never.wait, 5)

        api = object.__new__(Twitch_API)
        api.eventsub = StuckEventSub()
        api._reconnect_lock = threading.Lock()
        api._reconnect_in_progress = False
        api._eventsub_subscriptions_ready = True
        api._connection_epoch = 0
        api.is_connected = True
        api.last_health_check = None
        init_started = threading.Event()

        async def fake_init():
            init_started.set()

        api.intialize_twitch_api = fake_init
        with (
            patch.object(twitch_module, "_EVENTSUB_STOP_TIMEOUT", 0.3),
            patch("modules.connection_monitor.is_internet_available", return_value=True),
            patch("modules.connection_monitor.is_service_reachable", return_value=True),
        ):
            started = time.monotonic()
            api.reconnect()
            elapsed = time.monotonic() - started
        never.set()
        self.assertLess(elapsed, 2.0)
        self.assertIsNone(api.eventsub)
        self.assertTrue(init_started.wait(2))


class LockWaitTests(unittest.TestCase):
    def test_cancelled_token_refresh_waiter_never_owns_the_lock(self):
        api = object.__new__(Twitch_API)
        api._refresh_lock = threading.Lock()
        api._last_refresh_attempt = None
        api.is_token_expired = lambda: False

        async def main():
            api._refresh_lock.acquire()
            task = asyncio.create_task(api.refresh_auth_token())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            api._refresh_lock.release()
            await asyncio.sleep(0.2)
            return api._refresh_lock.locked()

        self.assertFalse(asyncio.run(main()))

    def test_channel_point_fetches_on_one_loop_do_not_deadlock(self):
        active = []
        overlaps = []

        async def generic_api_call(url, method):
            active.append(1)
            overlaps.append(len(active))
            await asyncio.sleep(0.1)
            active.pop()
            return {"data": [{"id": "r1"}]}

        fake_api = SimpleNamespace(
            is_connected=True, user_id="42", generic_api_call=generic_api_call
        )

        async def main():
            return await asyncio.wait_for(
                asyncio.gather(
                    *(twitch_module._fetch_channel_point_rewards_async() for _ in range(3))
                ),
                timeout=3,
            )

        with (
            patch.object(twitch_module, "twitch_api", fake_api),
            patch.object(twitch_module, "_channel_points_unlocked", None),
        ):
            results = asyncio.run(main())
        self.assertEqual(results, [([{"id": "r1"}], "ok")] * 3)
        self.assertEqual(overlaps, [1, 1, 1])
        self.assertFalse(twitch_module._channel_points_fetch_lock.locked())

    def test_moderator_cache_waiters_share_one_fetch_without_threads(self):
        from modules.twitch_moderators import ModeratorCache

        cache = ModeratorCache()
        fetches = []

        async def fake_fetch():
            fetches.append(1)
            await asyncio.sleep(0.3)
            cache._last_fetch = time.time()
            cache._last_fetch_success = True
            return True

        cache._fetch_moderators = fake_fetch

        async def main():
            before = threading.active_count()
            results = await asyncio.gather(*(cache.ensure_fresh() for _ in range(50)))
            return results, threading.active_count() - before

        results, extra = asyncio.run(main())
        self.assertTrue(all(results))
        self.assertEqual(len(fetches), 1)
        self.assertLessEqual(extra, 0)
        self.assertFalse(cache._refresh_lock.locked())

    def test_chatbot_refresh_waiter_does_not_block_its_loop(self):
        from modules import chatbot as chatbot_module

        bot = object.__new__(chatbot_module.Chatbot_API)
        bot._refresh_lock = threading.Lock()
        bot.auth_token = "tok"
        bot.client_id = "cid"

        async def holder():
            bot._refresh_lock.acquire()
            await asyncio.sleep(0.2)
            bot._refresh_lock.release()

        async def main():
            hold = asyncio.create_task(holder())
            await asyncio.sleep(0.02)
            with patch.object(chatbot_module, "is_token_currently_valid", AsyncMock(return_value=True)):
                result = await asyncio.wait_for(bot.refresh_auth_token(), timeout=2)
            await hold
            return result

        self.assertTrue(asyncio.run(main()))
        self.assertFalse(bot._refresh_lock.locked())


if __name__ == "__main__":
    unittest.main()

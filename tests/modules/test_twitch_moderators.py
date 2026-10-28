# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_moderators.py (cached Helix moderator list)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import twitch, twitch_moderators as tm  # noqa: E402


@pytest.fixture
def cache(monkeypatch):
    c = tm.ModeratorCache()
    monkeypatch.setattr(tm, "_cache", c)
    monkeypatch.setattr(tm, "_scope_warned", False)
    return c


def _api(responses):
    api = SimpleNamespace(user_id="b1")
    api.generic_api_call = AsyncMock(side_effect=list(responses))
    return api


def test_effective_ttl_and_staleness(cache, monkeypatch):
    assert cache._is_stale() is True  # never fetched
    cache._last_fetch = 1000.0
    monkeypatch.setattr(tm.time, "time", lambda: 1000.0 + 200)
    assert cache._effective_ttl() == tm._MODERATOR_CACHE_FAILURE_TTL_SECONDS
    assert cache._is_stale() is True  # failure ttl 120 < 200
    cache._last_fetch_success = True
    assert cache._effective_ttl() == tm._MODERATOR_CACHE_TTL_SECONDS
    assert cache._is_stale() is False


def test_refresh_delegates_to_ensure_fresh(cache):
    cache.ensure_fresh = AsyncMock(return_value=True)
    assert asyncio.run(cache.refresh(force=True)) is True
    cache.ensure_fresh.assert_awaited_once_with(force=True)


def test_ensure_fresh_waits_for_lock_then_returns_when_fresh(cache, monkeypatch):
    cache._refresh_lock.acquire()
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr(cache, "_fetch_moderators", fetch)

    async def go():
        task = asyncio.create_task(cache.ensure_fresh())
        await asyncio.sleep(0)
        # Another caller finished the fetch meanwhile.
        cache._last_fetch = time.time()
        cache._last_fetch_success = True
        return await task

    assert asyncio.run(go()) is True
    fetch.assert_not_awaited()
    cache._refresh_lock.release()


def test_ensure_fresh_double_check_after_lock(cache, monkeypatch):
    calls = {"n": 0}

    def stale():
        calls["n"] += 1
        return calls["n"] == 1  # stale on first check, fresh after lock acquired

    monkeypatch.setattr(cache, "_is_stale", stale)
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr(cache, "_fetch_moderators", fetch)
    assert asyncio.run(cache.ensure_fresh()) is True
    fetch.assert_not_awaited()
    assert not cache._refresh_lock.locked()


def test_ensure_fresh_release_error_is_ignored(cache, monkeypatch):
    async def fetch():
        cache._refresh_lock.release()  # released early
        return True

    monkeypatch.setattr(cache, "_fetch_moderators", fetch)
    assert asyncio.run(cache.ensure_fresh(force=True)) is True


def test_fetch_without_api_returns_false(cache, monkeypatch):
    monkeypatch.setattr(twitch, "twitch_api", None)
    assert asyncio.run(cache._fetch_moderators()) is False
    monkeypatch.setattr(twitch, "twitch_api", SimpleNamespace(user_id=""))
    assert asyncio.run(cache._fetch_moderators()) is False


def test_fetch_paginates_and_stores_ids(cache, monkeypatch):
    api = _api([
        {"data": [{"user_id": "1"}, {"user_id": None}, {"user_id": 2}], "pagination": {"cursor": "c1"}},
        {"data": [{"user_id": "3"}], "pagination": {}},
    ])
    monkeypatch.setattr(twitch, "twitch_api", api)
    assert asyncio.run(cache._fetch_moderators()) is True
    assert cache._moderator_ids == {"1", "2", "3"}
    assert cache._last_fetch_success is True
    first, second = api.generic_api_call.await_args_list
    assert first.kwargs["params"] == {"broadcaster_id": "b1", "first": 100}
    assert second.kwargs["params"]["after"] == "c1"


def test_fetch_stops_on_empty_response(cache, monkeypatch):
    monkeypatch.setattr(twitch, "twitch_api", _api([None]))
    assert asyncio.run(cache._fetch_moderators()) is True
    assert cache._moderator_ids == set()


def test_fetch_session_not_ready_error(cache, monkeypatch):
    api = SimpleNamespace(user_id="b1", generic_api_call=AsyncMock(side_effect=twitch.TwitchSessionNotReadyError("x")))
    monkeypatch.setattr(twitch, "twitch_api", api)
    assert asyncio.run(cache._fetch_moderators()) is False
    assert cache._last_fetch_success is False and cache._last_fetch > 0


def test_fetch_session_not_ready_message(cache, monkeypatch):
    api = SimpleNamespace(user_id="b1", generic_api_call=AsyncMock(side_effect=RuntimeError("Session not ready yet")))
    monkeypatch.setattr(twitch, "twitch_api", api)
    assert asyncio.run(cache._fetch_moderators()) is False


def test_fetch_forbidden_warns_once(cache, monkeypatch, caplog):
    api = SimpleNamespace(user_id="b1", generic_api_call=AsyncMock(side_effect=RuntimeError("403 Forbidden")))
    monkeypatch.setattr(twitch, "twitch_api", api)
    with caplog.at_level("WARNING", logger="modules.twitch_moderators"):
        assert asyncio.run(cache._fetch_moderators()) is False
        assert asyncio.run(cache._fetch_moderators()) is False
    assert caplog.text.count("Could not fetch moderators") == 1
    assert tm._scope_warned is True


def test_fetch_other_error_logs_error(cache, monkeypatch, caplog):
    api = SimpleNamespace(user_id="b1", generic_api_call=AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(twitch, "twitch_api", api)
    with caplog.at_level("ERROR", logger="modules.twitch_moderators"):
        assert asyncio.run(cache._fetch_moderators()) is False
    assert "Error refreshing moderator cache" in caplog.text


def test_is_cached_moderator(cache):
    cache._moderator_ids = {"5"}
    assert cache.is_cached_moderator(5) is True
    assert cache.is_cached_moderator("6") is False
    assert cache.is_cached_moderator(None) is False


def test_ensure_fresh_sync_noop_when_fresh(cache, monkeypatch):
    cache._last_fetch = time.time()
    cache._last_fetch_success = True
    started = MagicMock()
    monkeypatch.setattr(tm.threading, "Thread", started)
    cache.ensure_fresh_sync()
    started.assert_not_called()


def test_ensure_fresh_sync_without_loop_uses_thread(cache, monkeypatch):
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr(cache, "_fetch_moderators", fetch)
    threads = []
    real_thread = threading.Thread

    def make_thread(*a, **kw):
        t = real_thread(*a, **kw)
        threads.append(t)
        return t

    monkeypatch.setattr(tm.threading, "Thread", make_thread)
    cache.ensure_fresh_sync()
    for t in threads:
        t.join(2)
    assert threads and threads[0].name == "ModeratorCacheRefresh"
    fetch.assert_awaited_once()


def test_ensure_fresh_sync_inside_loop_creates_task(cache, monkeypatch):
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr(cache, "_fetch_moderators", fetch)

    async def go():
        cache.ensure_fresh_sync()
        await asyncio.sleep(0.01)

    asyncio.run(go())
    fetch.assert_awaited_once()


def test_ensure_fresh_sync_create_task_error_is_swallowed(cache, monkeypatch):
    class Loop:
        def create_task(self, coro):
            coro.close()
            raise RuntimeError("closed")

    monkeypatch.setattr(tm.asyncio, "get_running_loop", lambda: Loop())
    cache.ensure_fresh_sync()  # no raise


@pytest.mark.parametrize(
    "badges, expected",
    [
        (None, False),
        ([], False),
        ("broadcaster/1,moderator/1", True),
        ("subscriber/12", False),
        ([SimpleNamespace(set_id="vip"), SimpleNamespace(set_id="moderator")], True),
        ([{"set_id": "moderator"}], True),
        ([{"set_id": "vip"}, 42], False),
    ],
)
def test_badges_indicate_moderator(badges, expected):
    assert tm.badges_indicate_moderator(badges) is expected


def test_resolve_is_moderator_paths(cache, monkeypatch):
    monkeypatch.setattr(cache, "ensure_fresh_sync", MagicMock())
    assert tm.resolve_is_moderator("1", None, "1") is True
    assert tm.resolve_is_moderator("2", [{"set_id": "moderator"}], "1") is True
    cache.ensure_fresh_sync.assert_not_called()
    cache._moderator_ids = {"3"}
    assert tm.resolve_is_moderator("3", None, "1") is True
    assert tm.resolve_is_moderator("4", None, "1") is False
    assert cache.ensure_fresh_sync.call_count == 2


def test_resolve_is_moderator_async_paths(cache, monkeypatch):
    monkeypatch.setattr(cache, "ensure_fresh", AsyncMock(return_value=True))
    assert asyncio.run(tm.resolve_is_moderator_async("1", None, "1")) is True
    cache.ensure_fresh.assert_not_awaited()
    cache._moderator_ids = {"9"}
    assert asyncio.run(tm.resolve_is_moderator_async("9")) is True
    assert asyncio.run(tm.resolve_is_moderator_async("8")) is False


def test_get_moderator_cache_returns_singleton(cache):
    assert tm.get_moderator_cache() is cache


def test_ensure_fresh_returns_immediately_when_fresh(cache, monkeypatch):
    cache._last_fetch = time.time()
    cache._last_fetch_success = True
    fetch = AsyncMock()
    monkeypatch.setattr(cache, "_fetch_moderators", fetch)
    assert asyncio.run(cache.ensure_fresh()) is True
    fetch.assert_not_awaited()


def test_resolve_is_moderator_async_badge_skips_cache(cache, monkeypatch):
    monkeypatch.setattr(cache, "ensure_fresh", AsyncMock())
    assert asyncio.run(tm.resolve_is_moderator_async("2", "moderator/1", "1")) is True
    cache.ensure_fresh.assert_not_awaited()

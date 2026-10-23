"""RT-01: UI-loop connectivity reads never probe inline, and show the same labels."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import pytest

from modules import connection_monitor as cm
from modules import connection_status_tracker as tracker


@pytest.fixture()
def net(monkeypatch):
    state = {"up": True, "calls": 0}

    def fake_probe(url: str) -> bool:
        state["calls"] += 1
        time.sleep(0.05)
        return state["up"]

    monkeypatch.setattr(cm, "_http_probe", fake_probe)
    monkeypatch.setattr(cm, "is_shutdown_in_progress", lambda: False)
    monkeypatch.setattr(cm, "_external_fail_streak", 0)
    monkeypatch.setattr(cm, "_internet_online", True)
    monkeypatch.setattr(cm, "_internet_status_label", "Online")
    monkeypatch.setattr(cm, "_internet_settled_label", "Online")
    monkeypatch.setattr(cm, "_last_external_probe_mono", 0.0)
    monkeypatch.setattr(cm, "_service_probe_cache", {})
    monkeypatch.setattr(cm, "_bg_refresh_pending", [])
    monkeypatch.setattr(cm, "_bg_refresh_thread", None)
    monkeypatch.setattr(cm, "_bg_refresh_active", None)
    monkeypatch.setattr(cm, "_bg_refresh_listeners", [])
    monkeypatch.setattr(tracker, "_ui_last_overlay", {})
    return state


def _expire_caches() -> None:
    cm._last_external_probe_mono = -1e9
    for key, (result, _ts) in list(cm._service_probe_cache.items()):
        cm._service_probe_cache[key] = (result, -1e9)


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while cm._bg_refresh_thread is not None or cm._bg_refresh_pending:
        assert time.monotonic() < deadline, "background refresh did not finish"
        time.sleep(0.005)


SCRIPT = [True, True, False, False, False, False, True, True, False, True]


def _blocking_sequence(state) -> list:
    out = []
    for up in SCRIPT:
        state["up"] = up
        _expire_caches()
        overlay = tracker.get_connectivity_overlay("twitch")
        out.append((cm.get_internet_status(), overlay))
    return out


def test_ui_loop_reads_return_fast_and_match_inline_labels(net, monkeypatch):
    expected = _blocking_sequence(net)

    # Reset to the same starting state for the UI-loop run.
    cm._external_fail_streak = 0
    cm._internet_online = True
    cm._internet_status_label = "Online"
    cm._internet_settled_label = "Online"
    cm._last_external_probe_mono = 0.0
    cm._service_probe_cache.clear()

    main = threading.main_thread()
    monkeypatch.setattr(
        cm, "_on_ui_loop_thread", lambda: threading.current_thread() is main
    )
    refreshed = []
    cm.add_background_refresh_listener(lambda: refreshed.append(1))

    shown = []
    previous = None
    for up in SCRIPT:
        net["up"] = up
        _expire_caches()
        t0 = time.perf_counter()
        overlay = tracker.get_connectivity_overlay("twitch")
        inet = cm.get_internet_status()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.02, f"UI-loop read blocked for {elapsed:.3f}s"
        # While the probe runs, the UI keeps what it showed before (as when it was
        # blocked on the inline probe).
        if previous is not None:
            assert (inet, overlay) == previous
        _drain()
        overlay = tracker.get_connectivity_overlay("twitch")
        _drain()
        current = (cm.get_internet_status(), tracker.get_connectivity_overlay("twitch"))
        shown.append(current)
        previous = current

    assert shown == expected
    assert refreshed, "listeners are told when background probes land"


def test_background_refresh_is_deduped(net, monkeypatch):
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: True)
    cm._last_external_probe_mono = -1e9
    for _ in range(20):
        cm.is_internet_available()
    monkeypatch.setattr(cm, "_on_ui_loop_thread", lambda: False)
    _drain()
    # Two external URLs at most for a single round, never 20 rounds.
    assert net["calls"] <= len(cm._EXTERNAL_PROBE_URLS)


def test_non_ui_threads_still_probe_inline(net):
    cm._last_external_probe_mono = -1e9
    net["up"] = True
    assert cm.is_internet_available() is True
    assert net["calls"] == 1
    assert cm._bg_refresh_thread is None


def test_on_ui_loop_thread_detects_nicegui_loop(monkeypatch):
    loop = asyncio.new_event_loop()
    fake_core = types.SimpleNamespace(loop=loop)
    monkeypatch.setitem(sys.modules, "nicegui.core", fake_core)
    try:
        assert cm._on_ui_loop_thread() is False

        async def probe():
            return cm._on_ui_loop_thread()

        assert loop.run_until_complete(probe()) is True
    finally:
        loop.close()


def test_footer_repoll_is_coalesced(monkeypatch):
    from modules import notification_engine as ne

    queued = []
    monkeypatch.setattr(ne, "_service_watcher_started", True)
    monkeypatch.setattr(ne, "_connectivity_repoll_queued", False)
    monkeypatch.setattr(ne, "_run_on_ui_loop", lambda fn: queued.append(fn))
    polls = []
    monkeypatch.setattr(ne, "poll_service_status_changes", lambda: polls.append(1))

    ne._on_connectivity_refreshed()
    ne._on_connectivity_refreshed()
    assert len(queued) == 1
    queued[0]()
    assert polls == [1]
    ne._on_connectivity_refreshed()
    assert len(queued) == 2

"""UI-3: Database tab status refresh probes on a worker thread; same labels as inline."""

from __future__ import annotations

import asyncio
import threading
import time

from modules.uiwindows.tabs import database_tab as dt


class FakeLabel:
    def __init__(self):
        self.text = None
        self.cls = None

    def set_text(self, text):
        self.text = text

    def classes(self, replace=None, **_):
        self.cls = replace


STATUS = {
    "status": "Connected",
    "is_connected": True,
    "database_type": "Firebase",
    "last_check": "2026-10-22T10:00:00Z",
}


def _tab():
    tab = dt.DatabaseTab()
    tab.ui_elements = {
        "status_label": FakeLabel(),
        "type_label": FakeLabel(),
        "last_check_label": FakeLabel(),
    }
    return tab


def _snapshot(tab):
    return {k: (v.text, v.cls) for k, v in tab.ui_elements.items()}


def test_async_refresh_matches_inline_and_runs_off_loop(monkeypatch):
    threads = []

    def fake_status():
        threads.append(threading.current_thread())
        time.sleep(0.2)
        return dict(STATUS)

    monkeypatch.setattr(dt.database_manager, "get_connection_status", fake_status)

    async def fake_io_bound(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    monkeypatch.setattr(dt.run, "io_bound", fake_io_bound)

    inline = _tab()
    inline._refresh_status()

    async def scenario():
        tab = _tab()
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(10):
                await asyncio.sleep(0.01)
                ticks += 1

        await asyncio.gather(tab._refresh_status_async(), ticker())
        # A second timer tick while one is in flight is skipped.
        tab._status_refresh_inflight = True
        await tab._refresh_status_async()
        return tab, ticks

    tab, ticks = asyncio.run(scenario())
    assert ticks == 10  # the loop kept running during the 0.2 s probe
    assert _snapshot(tab) == _snapshot(inline)
    assert threads[-1] is not threading.main_thread()
    assert len(threads) == 2  # inline + one async; the skipped tick did not probe


def test_async_refresh_error_path(monkeypatch):
    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(dt.database_manager, "get_connection_status", boom)

    async def fake_io_bound(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    monkeypatch.setattr(dt.run, "io_bound", fake_io_bound)
    tab = _tab()
    asyncio.run(tab._refresh_status_async())
    assert tab.ui_elements["status_label"].text == "Status Error"
    assert tab._status_refresh_inflight is False

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the notification_engine test files (not collected: no ``test_`` prefix)."""

from __future__ import annotations

import pytest

from modules import notification_engine as ne
from modules import notify_settings as ns
from modules.service_alerts import ServiceAlertTracker


class FakeEl:
    """Records class/content changes like a NiceGUI element."""

    def __init__(self, html_id="c1", fail=False):
        self.cls = set()
        self.content = None
        self.html_id = html_id
        self.fail = fail
        self.replaced = None

    def classes(self, add=None, remove=None, replace=None):
        if self.fail:
            raise RuntimeError("element gone")
        if replace is not None:
            self.replaced = replace
            self.cls = set(replace.split())
        for c in (remove or "").split():
            self.cls.discard(c)
        for c in (add or "").split():
            self.cls.add(c)
        return self

    def set_content(self, content):
        if self.fail:
            raise RuntimeError("element gone")
        self.content = content


class Sched:
    def __init__(self):
        self.calls = []
        self.timers = []

    def __call__(self, interval, callback, **kw):
        self.calls.append((interval, callback, kw))
        t = type("T", (), {"active": kw.get("active", True)})()
        self.timers.append(t)
        return t


@pytest.fixture
def ne_env(monkeypatch, tmp_path):
    prefs = ns.NotifyPrefs()
    monkeypatch.setattr(ne, "current_prefs", lambda: prefs)
    monkeypatch.setattr(ne, "_history_path", lambda: tmp_path / "history.json")
    for name, value in {
        "_notification_leave_script_injected": False, "_toast_nav_handler_registered": False,
        "_last_emit_monotonic": {}, "_history": [], "_history_loaded": True, "_history_refresh_callbacks": [],
        "_pending_toasts": [], "_service_watcher_started": False, "_history_flush_timer": None,
        "_repeat_index": {}, "_call_actions": {}, "_toast_flush_timer": None, "_game_hook_suggested": set(),
        "_service_alerts": ServiceAlertTracker(), "_footer_container": None, "_footer_item_refs": {},
        "_trim_footer_refs": {}, "_trim_footer_poll_started": False, "_trim_footer_timer": None,
        "_trim_footer_last_signature": None, "_copy_footer_refs": {}, "_copy_footer_last_signature": None,
        "_footer_probe_scheduled": False, "_footer_probe_refresh_pending": False, "_footer_probe_running": False,
        "_footer_summary_refs": {}, "_connectivity_repoll_queued": False,
    }.items():
        monkeypatch.setattr(ne, name, value)
    sched = Sched()
    monkeypatch.setattr(ne, "_app_schedule", sched)
    ran = []
    monkeypatch.setattr(ne, "_run_on_ui_loop", lambda fn: ran.append(fn) or fn())
    flushes = []
    monkeypatch.setattr(ne, "_flush_history_soon", lambda: flushes.append(1))

    class Env:
        pass

    env = Env()
    env.prefs, env.sched, env.ran, env.flushes, env.tmp = prefs, sched, ran, flushes, tmp_path
    return env

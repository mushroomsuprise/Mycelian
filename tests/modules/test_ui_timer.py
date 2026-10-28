# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_timer.py: loop marshalling, app timers and layout-anchored timers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import logging  # noqa: E402

from nicegui import core, ui  # noqa: E402

from modules import ui_timer  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class _Loop:
    def __init__(self, running):
        self.running = running
        self.scheduled = []

    def is_running(self):
        return self.running

    def call_soon_threadsafe(self, fn):
        self.scheduled.append(fn)


def test_run_on_ui_loop_uses_running_loop(monkeypatch):
    loop = _Loop(True)
    monkeypatch.setattr(core, "loop", loop, raising=False)
    calls = []
    fn = lambda: calls.append(1)  # noqa: E731
    ui_timer.run_on_ui_loop(fn)
    assert loop.scheduled == [fn]
    assert calls == []


def test_run_on_ui_loop_runs_inline_when_loop_not_running(monkeypatch):
    loop = _Loop(False)
    monkeypatch.setattr(core, "loop", loop, raising=False)
    calls = []
    ui_timer.run_on_ui_loop(lambda: calls.append(1))
    assert calls == [1] and loop.scheduled == []


def test_run_on_ui_loop_runs_inline_when_no_loop(monkeypatch):
    monkeypatch.setattr(core, "loop", None, raising=False)
    calls = []
    ui_timer.run_on_ui_loop(lambda: calls.append(1))
    assert calls == [1]


def test_run_on_ui_loop_falls_back_when_loop_check_raises(monkeypatch):
    class _Broken:
        def is_running(self):
            raise RuntimeError("closed")

    monkeypatch.setattr(core, "loop", _Broken(), raising=False)
    calls = []
    ui_timer.run_on_ui_loop(lambda: calls.append(1))
    assert calls == [1]


def test_run_on_ui_loop_logs_callback_errors(monkeypatch, caplog):
    monkeypatch.setattr(core, "loop", None, raising=False)

    def boom():
        raise ValueError("bad")

    with caplog.at_level(logging.ERROR, logger=ui_timer.logger.name):
        ui_timer.run_on_ui_loop(boom)  # must not raise
    assert "loop callback failed" in caplog.text


def test_app_schedule_passes_arguments(monkeypatch):
    seen = []
    monkeypatch.setattr(ui_timer.app, "timer", lambda *a, **k: seen.append((a, k)) or "TIMER")
    cb = lambda: None  # noqa: E731
    assert ui_timer.app_schedule(1.5, cb, once=True, active=False) == "TIMER"
    assert seen == [((1.5, cb), {"once": True, "active": False})]


def test_layout_schedule_falls_back_without_slot(monkeypatch):
    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(ui_timer, "context", _Ctx())
    seen = []
    monkeypatch.setattr(ui_timer, "app_schedule", lambda *a, **k: seen.append((a, k)) or "APP")
    cb = lambda: None  # noqa: E731
    assert ui_timer.layout_schedule(2, cb, once=True) == "APP"
    assert seen == [((2, cb), {"once": True, "active": True})]


def test_layout_schedule_anchors_to_client_layout():
    made = {}

    def build():
        with ui.column() as col:
            made["timer"] = ui_timer.layout_schedule(100, lambda: None, active=False)
        made["col"] = col

    run_ui(build)
    timer = made["timer"]
    assert isinstance(timer, ui.timer)
    assert timer.active is False
    # Anchored to the client's layout, not the column it was created in.
    assert timer.parent_slot.parent is timer.client.layout
    assert timer not in made["col"].default_slot.children
    timer.cancel()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/background_writer.py (ordered, merged background saves)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import background_writer as bw  # noqa: E402
from modules import shutdown  # noqa: E402


def test_runs_jobs_in_order_off_the_caller_thread(monkeypatch):
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: None)
    w = bw.BackgroundWriter("t-order")
    seen = []
    caller = threading.get_ident()
    for i in range(5):
        w.submit(f"k{i}", lambda i=i: seen.append((i, threading.get_ident() != caller)))
    assert w.flush(2.0)
    assert seen == [(i, True) for i in range(5)]
    assert w.pending() == 0


def test_repeated_key_merges_into_latest_job(monkeypatch):
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: None)
    w = bw.BackgroundWriter("t-merge")
    gate, seen = threading.Event(), []
    w.submit("block", gate.wait)
    for value in ("a", "b", "c"):
        w.submit("pref", lambda value=value: seen.append(value))
    w.submit("other", lambda: seen.append("other"))
    gate.set()
    assert w.flush(2.0)
    assert seen == ["c", "other"]


def test_failures_are_logged_and_the_thread_keeps_going(monkeypatch, caplog):
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: None)
    w = bw.BackgroundWriter("t-fail")
    seen = []
    w.submit("bad", lambda: 1 / 0)
    w.submit("good", lambda: seen.append(1))
    assert w.flush(2.0)
    assert seen == [1] and "save 'bad' failed" in caplog.text


def test_flush_times_out_and_registers_once(monkeypatch):
    registered = []
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: registered.append(name))
    w = bw.BackgroundWriter("t-timeout")
    gate = threading.Event()
    w.submit("slow", gate.wait)
    w.submit("slow2", lambda: None)
    assert w.flush(0.05) is False
    gate.set()
    assert w.flush(2.0) is True
    assert registered == ["t-timeout"]

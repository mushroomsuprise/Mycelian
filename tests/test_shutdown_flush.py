# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shutdown flush registry used by write-behind persistence."""

from modules import shutdown


def test_registered_flushes_run_in_order_and_replace_by_name(monkeypatch):
    monkeypatch.setattr(shutdown, "_pending_flushes", [])
    calls = []
    shutdown.register_shutdown_flush("a", lambda: calls.append("a1"))
    shutdown.register_shutdown_flush("b", lambda: calls.append("b"))
    shutdown.register_shutdown_flush("a", lambda: calls.append("a2"))
    shutdown._run_registered_flushes()
    assert calls == ["b", "a2"]


def test_failing_flush_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(shutdown, "_pending_flushes", [])
    calls = []

    def boom():
        raise RuntimeError("nope")

    shutdown.register_shutdown_flush("boom", boom)
    shutdown.register_shutdown_flush("ok", lambda: calls.append("ok"))
    shutdown._run_registered_flushes()
    assert calls == ["ok"]

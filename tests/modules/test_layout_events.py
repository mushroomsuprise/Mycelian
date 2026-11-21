# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/layout_events.py (page events listened for before the first render)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import layout_events as le  # noqa: E402


class _Layout:
    def __init__(self):
        self.listeners = []

    def on(self, event, handler):
        self.listeners.append((event, handler))


class _Client:
    _next = 0

    def __init__(self):
        _Client._next += 1
        self.id = f"c{_Client._next}"
        self.layout = _Layout()
        self.deleted = []

    def on_delete(self, fn):
        self.deleted.append(fn)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(le, "_installed", set())
    monkeypatch.setattr(le, "_handlers", {})
    monkeypatch.setattr(le, "_defaults", {})


def _fire(client, event, args):
    handler = next(h for e, h in client.layout.listeners if e == event)
    return handler(SimpleNamespace(args=args))


def test_install_listens_for_every_event_once():
    client = _Client()
    assert le.install(client) is True
    assert le.install(client) is False
    assert [e for e, _ in client.layout.listeners] == list(le.EVENTS)


def test_handlers_added_after_install_add_no_listener():
    client = _Client()
    le.install(client)
    seen = []
    le.listen(client, le.EVENT_CS_SPLIT, lambda e: seen.append(e.args))
    assert len(client.layout.listeners) == len(le.EVENTS), "a late listener re-renders the page"
    _fire(client, le.EVENT_CS_SPLIT, 42)
    assert seen == [42]


def test_listen_installs_when_the_page_was_not_built_by_build_root_ui():
    client = _Client()
    le.listen(client, le.EVENT_FEED, lambda e: "ok")
    assert [e for e, _ in client.layout.listeners] == list(le.EVENTS)
    assert _fire(client, le.EVENT_FEED, {}) == "ok"


def test_page_handler_beats_default_and_pages_are_separate():
    a, b = _Client(), _Client()
    le.install(a)
    le.install(b)
    le.set_default(le.EVENT_TOAST_ACTION, lambda e: "default")
    le.listen(a, le.EVENT_TOAST_ACTION, lambda e: "page")
    assert _fire(a, le.EVENT_TOAST_ACTION, {}) == "page"
    assert _fire(b, le.EVENT_TOAST_ACTION, {}) == "default"
    assert _fire(b, le.EVENT_FEED, {}) is None  # no handler yet: ignored


def test_delete_forgets_the_page():
    client = _Client()
    le.listen(client, le.EVENT_GT_DRAWER_WIDTH, lambda e: 1)
    for fn in client.deleted:
        fn()
    assert le.handler_for(client, le.EVENT_GT_DRAWER_WIDTH) is None
    assert le.install(client) is True


def test_unknown_events_are_rejected():
    with pytest.raises(ValueError):
        le.listen(_Client(), "mycelian-nope", lambda e: None)
    with pytest.raises(ValueError):
        le.set_default("mycelian-nope", lambda e: None)


def test_install_current_without_context(monkeypatch):
    import nicegui

    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(nicegui, "context", _Ctx())
    le.install_current()  # no error


def test_no_module_adds_page_listeners_itself():
    """Every client.layout.on / ui.on call must go through layout_events."""
    offenders = []
    for path in (ROOT / "modules").rglob("*.py"):
        if path.name == "layout_events.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("layout.on(", "ui.on("):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_page_events_count_as_user_activity(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(le.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(le, "_last_activity", 0.0)
    assert le.idle_seconds() == 100.0
    client = _Client()
    le.install(client)
    _fire(client, le.EVENT_FEED, {})
    clock[0] += 2.5
    assert le.idle_seconds() == 2.5
    le.mark_activity()
    assert le.idle_seconds() == 0.0

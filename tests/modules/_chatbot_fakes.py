# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the modules/chatbot.py test files (not collected: no ``test_`` prefix)."""

from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

import pytest

from modules import chatbot
from modules import twitch as twitch_mod


class FakeThread:
    """Records a thread instead of starting it; ``run()`` calls the target in the test."""

    def __init__(self, registry, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.name = name
        self.started = False
        self.alive = False
        registry.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def run(self):
        return self.target(*self.args, **self.kwargs)


class Shown:
    def __init__(self):
        self.calls = []

    def __call__(self, message="", **kw):
        self.calls.append((message, kw))
        return "id"

    @property
    def titles(self):
        return [kw.get("title") for _m, kw in self.calls]

    @property
    def keys(self):
        return [kw.get("dedupe_key") for _m, kw in self.calls]


class FakeMainTwitch:
    """Stand-in for modules.twitch.twitch_api."""

    def __init__(self, connected=True, user_id="100", response=None, raise_exc=None):
        self.is_connected = connected
        self.user_id = user_id
        self.user = SimpleNamespace(display_name="Streamer")
        self.token_expiry = None
        self.auth_token = "main-token"
        self.response = response if response is not None else {"data": [{"is_sent": True}]}
        self.raise_exc = raise_exc
        self.calls = []

    async def generic_api_call(self, url, method="GET", params=None, json_data=None):
        self.calls.append((url, method, json_data))
        if self.raise_exc:
            raise self.raise_exc
        return self.response


@pytest.fixture
def cb(monkeypatch):
    """Fresh chatbot globals, recorded threads and notifications, no main Twitch account."""
    threads = []
    monkeypatch.setattr(chatbot, "threading", SimpleNamespace(
        Thread=lambda *a, **kw: FakeThread(threads, *a, **kw), Lock=threading.Lock))
    for name, value in (("chatbot_api", None), ("chatbot_connected", False), ("_initialized", False),
                        ("_chatbot_reconnect_in_progress", False), ("_chatbot_token_validation_warned", False),
                        ("_chatbot_send_worker_started", False)):
        monkeypatch.setattr(chatbot, name, value)
    monkeypatch.setattr(chatbot, "_recent_sent_chat", [])
    monkeypatch.setattr(chatbot, "_chatbot_send_queue", queue.Queue())
    shown = Shown()
    monkeypatch.setattr("modules.notification_engine.notify", shown)
    monkeypatch.setattr(twitch_mod, "twitch_api", None)
    return SimpleNamespace(threads=threads, shown=shown)

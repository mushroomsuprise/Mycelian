# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the chatbot_manager test files (not collected: no ``test_`` prefix)."""

from __future__ import annotations

import contextlib
import json
import threading
from types import SimpleNamespace

import pytest

from modules import chatbot_manager as cm
from modules import database_manager as dbm
from modules.chatbot_core import ChatCommand, ChatEvent

REAL_FLAGS = cm.GreetingFlagManager

PATHS = ["BotData/Commands", "BotData/Events", "BotData/Quotes", "BotData/Greetings", "BotData/Settings"]


# --------------------------------------------------------------------------- fakes


class FakeThread:
    def __init__(self, registry, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.name = name
        self.started = False
        self.alive = False
        self.joined = None
        registry.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined = timeout

    def run(self):
        return self.target(*self.args, **self.kwargs)


class FakeDB:
    def __init__(self):
        self.store = {}
        self.unreadable = set()
        self.missing = set()  # paths read back as None (nothing stored yet)
        self.fail_writes = set()
        self.writes = []
        self.problem = None
        self.raise_on_write = None

    def get_data_checked(self, path):
        if path in self.unreadable:
            return {}, False
        if path in self.missing:
            return None, True
        return json.loads(json.dumps(self.store.get(path, {}))), True

    def set_data(self, path, data):
        if self.raise_on_write:
            raise self.raise_on_write
        self.writes.append(path)
        if path in self.fail_writes:
            return False
        self.store[path] = json.loads(json.dumps(data))
        return True

    def last_write_problem(self):
        return self.problem


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    @property
    def titles(self):
        return [kw.get("title") for _a, kw in self.calls]


@pytest.fixture
def env(monkeypatch, tmp_path):
    threads = []
    fake_threading = SimpleNamespace(
        Thread=lambda *a, **kw: FakeThread(threads, *a, **kw),
        RLock=threading.RLock, Lock=threading.Lock, Event=threading.Event,
    )
    monkeypatch.setattr(cm, "threading", fake_threading)
    db = FakeDB()
    monkeypatch.setattr(dbm, "get_data_checked", db.get_data_checked)
    monkeypatch.setattr(dbm, "set_data", db.set_data)
    monkeypatch.setattr(dbm, "last_write_problem", db.last_write_problem)
    monkeypatch.setattr(dbm, "quiet_notifications", contextlib.nullcontext)
    notify, critical = Recorder(), Recorder()
    monkeypatch.setattr(cm, "notify", notify)
    monkeypatch.setattr(cm, "notify_critical", critical)
    stats = SimpleNamespace(calls=[])
    stats.increment_quote_redeemed = lambda qid, username=None: stats.calls.append(("quote", qid, username))
    stats.increment_commands_triggered = lambda **kw: stats.calls.append(("command", kw))
    stats.increment_events_triggered = lambda **kw: stats.calls.append(("event", kw))
    monkeypatch.setattr(cm, "get_statistics_manager", lambda: stats)
    from modules import statistics_manager as sm

    monkeypatch.setattr(sm, "get_statistics_manager", lambda: stats)
    flag_path = tmp_path / "greeting_flags.json"
    real_flags = REAL_FLAGS
    monkeypatch.setattr(cm, "GreetingFlagManager", lambda: real_flags(str(flag_path)))
    # Legacy migration looks in <repo>/data for old JSON files; point it at an empty folder.
    monkeypatch.setattr(cm.os.path, "dirname", _dirname_redirect(tmp_path))
    ns = SimpleNamespace(db=db, threads=threads, notify=notify, critical=critical, stats=stats,
                         flag_path=flag_path, tmp=tmp_path)

    def make():
        return cm.ChatbotManager()

    ns.make = make
    return ns


def _dirname_redirect(tmp_path):
    import os

    (tmp_path / "modules").mkdir(exist_ok=True)
    real = os.path.dirname
    module_dir = real(cm.__file__)

    def dirname(p):
        if p == cm.__file__:
            return str(tmp_path / "modules")
        return real(p)

    assert module_dir  # sanity
    return dirname


def _cmd(cid="c1", word="hello", **kw):
    kw.setdefault("response_text", "Hi {username}")
    return ChatCommand(command_id=cid, name=kw.pop("name", word.title()), command_name=word, **kw)


def _evt(eid="e1", etype="follow", **kw):
    kw.setdefault("response_text", "Thanks {username}")
    return ChatEvent(event_id=eid, name=kw.pop("name", eid), event_type=etype, **kw)



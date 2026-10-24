# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Hotkey normalisation / single event per press, and the connector webhook route."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from modules import connector_integration as ci
from modules import hotkey_listener as hl
from modules.connector_core import Connector, TriggerType
from modules.connector_manager import ConnectorManager
from modules.connector_triggers import create_trigger, schedule_is_due
from modules.hotkey_listener import normalize_hotkey_combo as norm


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Ctrl + Shift + F", "ctrl+shift+f"),
        ("shift+ctrl+f", "ctrl+shift+f"),
        ("Control+Option+K", "ctrl+alt+k"),
        ("Meta+ArrowUp", "cmd+up"),
        ("win+Return", "cmd+enter"),
        ("command+Escape", "cmd+esc"),
        ("ctrl+PageDown", "ctrl+page_down"),
        ("ctrl+ ", "ctrl+space"),
        ("Spacebar", "space"),
        ("alt+ƒ", "alt+f"),
        ("alt+shift+Ï", "alt+shift+f"),
        ("shift+!", "shift+1"),
        ("ctrl++", "ctrl+plus"),
        ("F5", "f5"),
        ("", ""),
    ],
)
def test_normalize_hotkey_combo(raw, expected):
    assert norm(raw) == expected
    assert norm(norm(raw)) == norm(raw)


def test_hotkey_trigger_matches_messy_stored_combo():
    trigger = create_trigger(TriggerType.HOTKEY, "t", "n", key_combination="Shift + Ctrl + K",
                             is_global=False)
    assert trigger.combo() == "ctrl+shift+k"
    event = hl.build_hotkey_event("ctrl+shift+k")
    assert trigger.should_trigger(event)  # is_global no longer rejects
    assert trigger.should_trigger({"event_type": "hotkey", "key_code": "K",
                                   "modifiers": ["shift", "control"]})
    assert not trigger.should_trigger(hl.build_hotkey_event("ctrl+k"))


def test_one_event_per_press_for_shared_combo(monkeypatch):
    listener = hl.HotkeyListener()
    listener.register_hotkey("c1", "Ctrl + K")
    listener.register_hotkey("c2", "ctrl+k")
    assert listener.get_registered_hotkeys() == {"ctrl+k": ["c1", "c2"]}

    sent = []

    class Loop:
        def is_running(self):
            return True

        def is_closed(self):
            return False

    import modules.connector_integration as integ
    import modules.connector_manager as cm

    async def add_event(event):
        sent.append(event)

    monkeypatch.setattr(cm, "get_connector_loop", lambda: Loop())
    monkeypatch.setattr(integ, "get_integration",
                        lambda: SimpleNamespace(manager=SimpleNamespace(add_event=add_event)))

    def fake_run(coro, loop):
        asyncio.run(coro)

    monkeypatch.setattr(hl.asyncio, "run_coroutine_threadsafe", fake_run)
    listener._on_hotkey_pressed("ctrl+k")
    assert len(sent) == 1
    assert sent[0]["combo"] == "ctrl+k" and sent[0]["modifiers"] == ["ctrl"]
    listener.unregister_hotkey("c1", "CTRL+K")
    assert listener.get_registered_hotkeys() == {"ctrl+k": ["c2"]}


# ---- schedule / timer ----------------------------------------------------


def test_schedule_days_of_week():
    trig = create_trigger(TriggerType.SCHEDULE, "s", "n", specific_time="19:30",
                          days_of_week=["mon", "2", 4, 9])
    assert trig.days_of_week == [0, 2, 4]
    monday = dt.datetime(2026, 10, 19, 19, 30)  # a Monday
    assert schedule_is_due(trig, monday)
    assert not schedule_is_due(trig, monday + dt.timedelta(days=1))
    assert not schedule_is_due(trig, monday + dt.timedelta(minutes=1))
    every = create_trigger(TriggerType.SCHEDULE, "s", "n", specific_time="07:05")
    assert every.days_of_week == []
    assert schedule_is_due(every, dt.datetime(2026, 10, 25, 7, 5))


def _conn(cid, trigger):
    return Connector(connector_id=cid, name=cid, trigger=trigger, actions=[])


def test_scheduler_targets_connectors():
    timer = create_trigger(TriggerType.TIMER, "tt", "n", interval_seconds=10)
    sched = create_trigger(TriggerType.SCHEDULE, "st", "n", specific_time="12:00")
    conns = [("t1", _conn("t1", timer)), ("s1", _conn("s1", sched))]
    last, nxt = {}, {}
    noon = dt.datetime(2026, 10, 24, 12, 0, 5)
    f = ConnectorManager._scheduler_due_events
    first = f(conns, noon, 100.0, last, nxt)
    assert [e["event_type"] for e in first] == ["schedule"]
    assert first[0]["target_connector_id"] == "s1"
    assert f(conns, noon, 105.0, last, nxt) == []  # same minute: once
    due = f(conns, noon, 110.0, last, nxt)
    assert len(due) == 1 and due[0]["event_type"] == "timer"
    assert due[0]["target_connector_id"] == "t1" and due[0]["interval_seconds"] == 10
    assert due[0]["timer_id"] == "tt"
    assert timer.should_trigger(due[0])


def test_any_trigger_ignores_internal_ticks_and_targeted_routing():
    any_trigger = create_trigger(TriggerType.ANY, "a", "n")
    assert not any_trigger.should_trigger({"event_type": "timer", "internal_tick": True})
    manager = ConnectorManager()
    fired = []

    class Spy:
        def __init__(self, cid):
            self.cid = cid
            self.enabled = True
            self.trigger = any_trigger
            self.name = cid

        async def process_event(self, event):
            fired.append(self.cid)
            return False

    manager.connectors = {"a": Spy("a"), "b": Spy("b")}
    asyncio.run(manager._process_single_event({"event_type": "webhook", "target_connector_id": "b"}))
    assert fired == ["b"]


# ---- webhook -------------------------------------------------------------


@pytest.fixture
def webhook_app(monkeypatch):
    flask = pytest.importorskip("flask")
    from modules.connector_webhook import install_connector_webhook_route

    manager = ConnectorManager()
    secret_trigger = create_trigger(TriggerType.WEBHOOK, "w", "n", secret_key="s3cret")
    manager.connectors = {
        "hook": _conn("hook", secret_trigger),
        "nokey": _conn("nokey", create_trigger(TriggerType.WEBHOOK, "w2", "n")),
        "bits": _conn("bits", create_trigger(TriggerType.TWITCH_BITS, "b", "n")),
    }
    queued = []
    monkeypatch.setattr(ci, "get_manager", lambda: manager)
    monkeypatch.setattr(ci, "_enqueue_connector_event", lambda ev: queued.append(ev) or True)
    app = flask.Flask("t")
    install_connector_webhook_route(app)
    return SimpleNamespace(client=app.test_client(), queued=queued, manager=manager)


def test_webhook_auth_and_dispatch(webhook_app):
    c = webhook_app.client
    r = c.post("/api/connectors/webhook/hook", json={"username": "Bob", "event_type": "x"})
    assert r.status_code == 401 and not webhook_app.queued
    r = c.post("/api/connectors/webhook/hook?key=wrong", json={})
    assert r.status_code == 401
    r = c.post("/api/connectors/webhook/hook", json={"username": "Bob", "amount": 5,
                                                      "event_type": "x"},
               headers={"X-Mycelian-Key": "s3cret"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    event = webhook_app.queued[-1]
    assert event["event_type"] == "webhook" and event["target_connector_id"] == "hook"
    assert event["username"] == "Bob" and event["amount"] == 5
    assert event["payload"] == {"username": "Bob", "amount": 5, "event_type": "x"}
    r = c.get("/api/connectors/webhook/hook?key=s3cret&username=Ann")
    assert r.status_code == 200 and webhook_app.queued[-1]["username"] == "Ann"
    assert "key" not in webhook_app.queued[-1]["payload"]
    r = c.post("/api/connectors/webhook/hook?key=s3cret", data={"username": "Form"})
    assert r.status_code == 200 and webhook_app.queued[-1]["username"] == "Form"
    assert c.post("/api/connectors/webhook/nokey?key=").status_code == 401
    assert c.post("/api/connectors/webhook/missing?key=x").status_code == 404
    assert c.post("/api/connectors/webhook/bits?key=x").status_code == 400
    webhook_app.manager.connectors["hook"].enabled = False
    assert c.post("/api/connectors/webhook/hook?key=s3cret").status_code == 409


def test_webhook_event_fires_only_webhook_trigger():
    event = ci.build_webhook_event({"username": "Bob"}, "hook")
    trigger = create_trigger(TriggerType.WEBHOOK, "w", "n", secret_key="k")
    assert trigger.should_trigger(event)

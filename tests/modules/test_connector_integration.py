# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_integration: Twitch handler wiring and retry, every Twitch event handler, hotkey /
timer / webhook events, template actions, text helpers and webhook dispatch (all faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import threading
import types
from types import SimpleNamespace

import pytest

import modules
from modules import connector_integration as ci
from modules.connector_core import Connector, TriggerType
from modules.connector_triggers import create_trigger


def run(coro):
    return asyncio.run(coro)


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


class FakeManager:
    def __init__(self):
        self.events = []
        self.connectors = {}
        self.is_running = True
        self.fail = False

    async def add_event(self, event):
        if self.fail:
            raise RuntimeError("queue down")
        self.events.append(event)

    def get_connector(self, cid):
        return self.connectors.get(cid)


@pytest.fixture
def integ(monkeypatch):
    mgr = FakeManager()
    monkeypatch.setattr(ci, "get_manager", lambda: mgr)
    import modules.connector_manager as cm

    monkeypatch.setattr(cm, "get_manager", lambda: mgr)
    obj = ci.ConnectorIntegration()
    assert obj.manager is mgr
    return SimpleNamespace(i=obj, mgr=mgr, mp=monkeypatch)


def ev(**kw):
    return SimpleNamespace(event=SimpleNamespace(**kw))


# --------------------------------------------------------------------------- Twitch wiring


class FakeApi:
    def __init__(self):
        self.calls = []
        for name in ("on_bits_use", "on_sub_gift", "on_follow", "on_raid", "on_points", "on_chat_message",
                     "on_hype_train_start", "on_hype_train_end", "on_stream_online", "on_stream_offline"):
            setattr(self, name, self._make(name))

    def _make(self, name):
        async def handler(*args, **kwargs):
            self.calls.append(name)
            return f"{name}-result"

        return handler


def test_setup_twitch_integration_wraps_handlers(integ):
    api = FakeApi()
    del api.on_stream_offline
    lane = []

    async def wait_lane():
        lane.append(1)

    fake_module(integ.mp, "twitch", twitch_api=api, wait_for_chatbot_lane=wait_lane)
    integ.i.setup_twitch_integration()
    assert integ.i._twitch_integration_setup
    assert set(integ.i._original_twitch_handlers) >= {"on_bits_use", "on_follow", "on_stream_online"}
    assert "on_stream_offline" not in integ.i._original_twitch_handlers
    handled = []

    async def follow(data):
        handled.append(data)

    integ.mp.setattr(integ.i, "_handle_twitch_follow", follow)
    integ.i.setup_twitch_integration()  # already set up: no-op
    assert run(api.on_follow("payload")) == "on_follow-result"
    assert api.calls == ["on_follow"] and lane == [1]


def test_wrapped_handler_survives_lane_and_connector_errors(integ):
    api = FakeApi()

    async def bad_lane():
        raise RuntimeError("lane")

    fake_module(integ.mp, "twitch", twitch_api=api, wait_for_chatbot_lane=bad_lane)
    integ.i._original_twitch_handlers = {}

    async def failing(*a, **k):
        raise RuntimeError("connector")

    integ.i._wrap_twitch_handler(api, "on_raid", failing)
    assert run(api.on_raid(1, x=2)) == "on_raid-result"


def test_wrap_handler_errors(integ):
    integ.i._original_twitch_handlers = {}
    integ.i._wrap_twitch_handler(SimpleNamespace(), "missing", None)

    class Frozen:
        __slots__ = ()

        async def on_x(self):
            return None

    integ.i._wrap_twitch_handler(Frozen(), "on_x", None)  # setattr fails: logged
    del integ.i._original_twitch_handlers
    integ.i._wrap_twitch_handler(FakeApi(), "on_raid", None)  # no store: logged


def test_setup_without_api_schedules_retry(integ):
    fake_module(integ.mp, "twitch", twitch_api=None)
    calls = []
    integ.mp.setattr(integ.i, "_schedule_twitch_integration_retry", lambda: calls.append(1))
    integ.i.setup_twitch_integration()
    assert calls == [1] and not integ.i._twitch_integration_setup


def test_setup_import_error_is_logged(integ):
    integ.mp.setitem(sys.modules, "modules.twitch", None)
    integ.mp.delattr(modules, "twitch", raising=False)
    integ.i.setup_twitch_integration()
    assert not integ.i._twitch_integration_setup


class SyncThread:
    def __init__(self, target=None, name=None, daemon=None):
        self.target, self.name, self.daemon = target, name, daemon
        self.alive = False

    def start(self):
        self.target()

    def is_alive(self):
        return self.alive


def _retry_env(integ, shutdown=False, times=None):
    integ.mp.setattr(threading, "Thread", SyncThread)
    fake_module(integ.mp, "shutdown", is_shutdown_in_progress=lambda: shutdown)
    clock = list(times or [0.0])
    integ.mp.setattr(ci.time, "time", lambda: clock.pop(0) if len(clock) > 1 else clock[0])
    integ.mp.setattr(ci.time, "sleep", lambda s: None)
    notes = []
    integ.mp.setattr(ci, "notify", lambda msg, **kw: notes.append(kw))
    integ.mp.setattr(ci, "nav_actions_settings", lambda tab: [("settings", tab)])
    return notes


def test_retry_wires_when_api_appears(integ):
    _retry_env(integ, times=[0.0, 1.0, 2.0])
    twitch = fake_module(integ.mp, "twitch", twitch_api=None)
    seen = []

    def setup():
        seen.append(1)
        integ.i._twitch_integration_setup = True

    integ.mp.setattr(integ.i, "setup_twitch_integration", setup)
    # first poll: not ready; second: ready
    polls = {"n": 0}

    def sleep(s):
        polls["n"] += 1
        if polls["n"] == 2:
            twitch.twitch_api = object()

    integ.mp.setattr(ci.time, "sleep", sleep)
    integ.i._schedule_twitch_integration_retry()
    assert seen == [1] and integ.i._twitch_retry_thread.name == "ConnectorTwitchIntegrationRetry"


def test_retry_stops_on_shutdown_or_setup(integ):
    notes = _retry_env(integ, shutdown=True, times=[0.0, 1.0])
    integ.i._schedule_twitch_integration_retry()
    assert notes == []
    integ.i._twitch_integration_setup = True
    integ.i._twitch_retry_thread = None
    integ.i._schedule_twitch_integration_retry()
    assert integ.i._twitch_retry_thread is None


def test_retry_skips_when_thread_alive(integ):
    alive = SyncThread()
    alive.alive = True
    integ.i._twitch_retry_thread = alive
    integ.i._schedule_twitch_integration_retry()
    assert integ.i._twitch_retry_thread is alive


def test_retry_gives_up_and_warns(integ):
    notes = _retry_env(integ, times=[0.0, 1.0, 500.0])
    integ.mp.setitem(sys.modules, "modules.twitch", None)
    integ.mp.delattr(modules, "twitch", raising=False)
    integ.mp.setattr(ci, "_has_enabled_twitch_connectors", lambda: True)
    integ.i._schedule_twitch_integration_retry()
    assert notes and notes[0]["dedupe_key"] == "connector:twitch_unwired"
    assert notes[0]["actions"] == [("settings", "Twitch")]


def test_retry_gives_up_quietly_without_twitch_connectors(integ):
    notes = _retry_env(integ, times=[0.0, 500.0])
    integ.mp.setattr(ci, "_has_enabled_twitch_connectors", lambda: False)
    integ.i._schedule_twitch_integration_retry()
    assert notes == []


# --------------------------------------------------------------------------- Twitch handlers


def test_handle_bits(integ):
    run(integ.i._handle_twitch_bits(ev(bits=100, user_name="Bob", message=SimpleNamespace(text="cheer"),
                                       user_id="7", type="cheer")))
    e = integ.mgr.events[-1]
    assert (e["event_type"], e["amount"], e["username"], e["message"], e["user_id"], e["bits_type"]) == (
        "twitch_bits", 100, "Bob", "cheer", "7", "cheer")
    run(integ.i._handle_twitch_bits(ev(bits="5", user_name="Bob", is_anonymous=True)))
    e = integ.mgr.events[-1]
    assert e["username"] == "Anonymous" and e["is_anonymous"] is True and e["user_id"] == ""
    run(integ.i._handle_twitch_bits(ev(bits="x", user_name="Bob")))  # bad amount: logged
    assert len(integ.mgr.events) == 2


def test_handle_giftsub(integ):
    run(integ.i._handle_twitch_giftsub(ev(tier="2000", user_name="G", total=5, cumulative_total=20, user_id="9")))
    e = integ.mgr.events[-1]
    assert (e["tier"], e["gift_count"], e["cumulative_total"], e["user_id"], e["username"]) == (2, 5, 20, "9", "G")
    run(integ.i._handle_twitch_giftsub(ev(user_name="G", is_anonymous=True, user_id="9", total=None)))
    e = integ.mgr.events[-1]
    assert e["username"] == "Anonymous" and e["user_id"] == "" and e["gift_count"] == 1
    assert e["cumulative_total"] == 0
    integ.mgr.fail = True
    run(integ.i._handle_twitch_giftsub(ev(user_name="G")))


def test_handle_follow_raid_points(integ):
    run(integ.i._handle_twitch_follow(ev(user_name="F", user_id="1", followed_at="now")))
    assert integ.mgr.events[-1]["followed_at"] == "now"
    run(integ.i._handle_twitch_raid(ev(from_broadcaster_user_name="R", viewers=12, from_broadcaster_user_id="a",
                                       to_broadcaster_user_id="b")))
    e = integ.mgr.events[-1]
    assert e["viewer_count"] == 12 and e["from_user_id"] == "a" and e["to_user_id"] == "b"
    reward = SimpleNamespace(id="r1", title="Hydrate", cost=100)
    run(integ.i._handle_twitch_points(ev(user_name="P", reward=reward, user_id="3", redeemed_at="t")))
    e = integ.mgr.events[-1]
    assert e["reward_name"] == e["reward_title"] == "Hydrate" and e["user_input"] == "" and e["timestamp"] == "t"
    n = len(integ.mgr.events)
    run(integ.i._handle_twitch_follow(ev()))
    run(integ.i._handle_twitch_raid(ev()))
    run(integ.i._handle_twitch_points(ev()))
    assert len(integ.mgr.events) == n


def _chat_env(integ, own=False, broadcaster="55"):
    mods = []

    async def resolve(user_id, badges=None, broadcaster_id=None):
        mods.append((user_id, badges, broadcaster_id))
        return True

    fake_module(integ.mp, "twitch_moderators", resolve_is_moderator_async=resolve)
    api = SimpleNamespace(user_id=broadcaster) if broadcaster is not None else None
    fake_module(integ.mp, "twitch", twitch_api=api)
    fake_module(integ.mp, "chatbot", is_own_chat_message=lambda uid, msg: own)
    return mods


def test_handle_chat(integ):
    mods = _chat_env(integ)
    msg = SimpleNamespace(text="!hello world", fragments=[
        {"type": "emote", "text": "Kappa"}, {"type": "text", "text": "x"},
        SimpleNamespace(type="emote", text="PogChamp"), SimpleNamespace(type="emote", text="")])
    badges = [{"set_id": "broadcaster"}, "vip", SimpleNamespace(set_id=None, id="sub"), {"id": None}]
    run(integ.i._handle_twitch_chat(ev(message=msg, chatter_user_id="u1", chatter_user_name="Bob", badges=badges)))
    e = integ.mgr.events[-1]
    assert e["is_command"] and e["command"] == "hello" and e["message"] == "!hello world"
    assert e["badges"] == "broadcaster,vip,sub" and e["emotes"] == "Kappa,PogChamp" and e["is_moderator"]
    assert mods[-1] == ("u1", badges, "55")


def test_handle_chat_plain_and_no_broadcaster(integ):
    mods = _chat_env(integ, broadcaster=None)
    run(integ.i._handle_twitch_chat(ev(message="!", chatter_user_id="u", chatter_user_name="B")))
    e = integ.mgr.events[-1]
    assert e["is_command"] and e["command"] == "" and mods[-1][2] is None
    _chat_env(integ, broadcaster="")
    run(integ.i._handle_twitch_chat(ev(message=None, chatter_user_id="u", chatter_user_name="B")))
    assert integ.mgr.events[-1]["is_command"] is False and integ.mgr.events[-1]["message"] == ""


def test_handle_chat_skips_own_echo_and_errors(integ):
    _chat_env(integ, own=True)
    run(integ.i._handle_twitch_chat(ev(message="hi", chatter_user_id="bot", chatter_user_name="Bot")))
    assert integ.mgr.events == []
    run(integ.i._handle_twitch_chat(ev()))  # missing attrs: logged


def test_handle_hype_train_and_stream(integ, monkeypatch):
    run(integ.i._handle_twitch_hype_train_start(ev(level=2, total=500, goal=800, started_at="s", expires_at="x")))
    e = integ.mgr.events[-1]
    assert e["event_type"] == "twitch_hype_train_start" and e["top_contributors"] == [] and e["timestamp"] == "s"
    run(integ.i._handle_twitch_hype_train_end(ev(level=3, total=900, started_at="s", ended_at="e",
                                                 cooldown_ends_at="c", top_contributions=["a"])))
    e = integ.mgr.events[-1]
    assert e["ended_at"] == "e" and e["top_contributors"] == ["a"] and e["timestamp"] == "e"
    monkeypatch.setattr(ci.time, "time", lambda: 42.0)
    run(integ.i._handle_twitch_stream_online(ev(id="sid", type="live", started_at="st")))
    assert integ.mgr.events[-1] == {"event_type": "twitch_stream_online", "stream_id": "sid", "stream_type": "live",
                                    "started_at": "st", "timestamp": 42.0, "source": "twitch"}
    run(integ.i._handle_twitch_stream_online(ev()))
    assert integ.mgr.events[-1]["stream_id"] == ""
    run(integ.i._handle_twitch_stream_offline(ev()))
    assert integ.mgr.events[-1] == {"event_type": "twitch_stream_offline", "timestamp": 42.0, "source": "twitch"}
    integ.mgr.fail = True
    n = len(integ.mgr.events)
    run(integ.i._handle_twitch_hype_train_start(ev()))
    run(integ.i._handle_twitch_hype_train_end(ev()))
    run(integ.i._handle_twitch_stream_online(ev()))
    run(integ.i._handle_twitch_stream_offline(ev()))
    assert len(integ.mgr.events) == n


# --------------------------------------------------------------------------- manual events


def test_send_hotkey_timer_webhook(integ, monkeypatch):
    monkeypatch.setattr(ci.time, "time", lambda: 7.0)
    run(integ.i.send_hotkey_event("f5"))
    assert integ.mgr.events[-1] == {"event_type": "hotkey", "key_code": "f5", "modifiers": [], "is_global": True,
                                    "timestamp": 7.0, "source": "hotkey"}
    run(integ.i.send_hotkey_event("a", ["ctrl"], is_global=False))
    assert integ.mgr.events[-1]["modifiers"] == ["ctrl"] and integ.mgr.events[-1]["is_global"] is False
    run(integ.i.send_timer_event("tid", 30))
    assert integ.mgr.events[-1]["internal_tick"] is True and "target_connector_id" not in integ.mgr.events[-1]
    run(integ.i.send_timer_event("tid", 30, connector_id="c1"))
    assert integ.mgr.events[-1]["target_connector_id"] == "c1" and "internal_tick" not in integ.mgr.events[-1]
    run(integ.i.send_webhook_event({"username": "W"}, "c2"))
    assert integ.mgr.events[-1]["username"] == "W" and integ.mgr.events[-1]["target_connector_id"] == "c2"
    integ.mgr.fail = True
    n = len(integ.mgr.events)
    run(integ.i.send_hotkey_event("x"))
    run(integ.i.send_timer_event("t", 1))
    run(integ.i.send_webhook_event({}))
    assert len(integ.mgr.events) == n


# --------------------------------------------------------------------------- template actions


def test_get_available_template_actions(integ):
    configs = {
        "wheel": {"connector_actions": {"spin": {"name": "Spin"}, "bad": "text"}},
        "plain": {"other": 1},
        "list": ["not a dict"],
        "broken": RuntimeError("bad file"),
        "onlybad": {"connector_actions": {"x": 5}},
    }

    class Parser:
        def get_config_files(self):
            return list(configs)

        def load_config(self, name, include_dynamic_controls=False):
            assert include_dynamic_controls is True
            value = configs[name]
            if isinstance(value, Exception):
                raise value
            return value

    fake_module(integ.mp, "template_config_parser", TemplateConfigParser=Parser)
    assert integ.i.get_available_template_actions() == {"wheel": {"spin": {"name": "Spin"}}}

    def broken():
        raise RuntimeError("no parser")

    fake_module(integ.mp, "template_config_parser", TemplateConfigParser=broken)
    assert integ.i.get_available_template_actions() == {}


# --------------------------------------------------------------------------- helpers


def test_message_badge_emote_helpers():
    assert ci._message_text(None) == "" and ci._message_text("hi") == "hi"
    assert ci._message_text(SimpleNamespace(text=None)) == "" and ci._message_text(SimpleNamespace(text=5)) == "5"
    assert ci._badge_text(None) == "" and ci._badge_text([SimpleNamespace(id="x")]) == "x"
    assert ci._emote_text(None) == "" and ci._emote_text(SimpleNamespace(fragments=None)) == ""


def test_is_own_chat_echo(monkeypatch):
    fake_module(monkeypatch, "chatbot", is_own_chat_message=lambda uid, msg: uid == "bot")
    assert ci._is_own_chat_echo("bot", "x") is True
    assert ci._is_own_chat_echo("user", "x") is False

    def boom(*a):
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot", is_own_chat_message=boom)
    assert ci._is_own_chat_echo("bot", "x") is False


def test_build_webhook_event(monkeypatch):
    monkeypatch.setattr(ci.time, "time", lambda: 9.0)
    payload = {"username": "W", "event_type": "hack", "source": "x", "internal_tick": True}
    e = ci.build_webhook_event(payload)
    assert e["event_type"] == "webhook" and e["source"] == "webhook" and e["username"] == "W"
    assert "internal_tick" not in e and e["payload"] == payload and e["timestamp"] == 9.0
    assert "target_connector_id" not in e
    e = ci.build_webhook_event({"timestamp": 3}, "c1")
    assert e["timestamp"] == 3 and e["connector_id"] == e["target_connector_id"] == "c1"
    assert ci.build_webhook_event(None)["payload"] == {}


# --------------------------------------------------------------------------- webhook dispatch


def webhook_connector(secret="s3cret", enabled=True):
    trig = create_trigger(TriggerType.WEBHOOK, "w", "W", secret_key=secret)
    return Connector(connector_id="c1", name="Hook", enabled=enabled, trigger=trig)


def test_handle_connector_webhook_statuses(integ):
    queued = []
    state = {"ok": True}
    integ.mp.setattr(ci, "_enqueue_connector_event", lambda e: queued.append(e) or state["ok"])
    h = ci.handle_connector_webhook
    assert h("", "k", {})[0] == 404
    assert h("c1", "k", {})[0] == 404
    integ.mgr.connectors["c1"] = Connector(connector_id="c1", name="n")
    assert h("c1", "k", {}) == (400, {"ok": False, "error": "Connector is not a webhook trigger"})
    integ.mgr.connectors["c1"] = Connector(connector_id="c1", name="n",
                                           trigger=create_trigger(TriggerType.TWITCH_BITS, "t", "T"))
    assert h("c1", "k", {})[0] == 400
    integ.mgr.connectors["c1"] = webhook_connector(secret="")
    assert h("c1", "k", {})[1]["error"] == "Connector has no webhook key set"
    integ.mgr.connectors["c1"] = webhook_connector()
    assert h("c1", None, {})[1]["error"] == "Invalid webhook key"
    assert h("c1", "wrong", {})[0] == 401
    integ.mgr.connectors["c1"] = webhook_connector(enabled=False)
    assert h("c1", "s3cret", {})[0] == 409
    integ.mgr.connectors["c1"] = webhook_connector()
    assert h("c1", "s3cret", {"a": 1}) == (200, {"ok": True})
    assert queued[-1]["target_connector_id"] == "c1" and queued[-1]["a"] == 1
    state["ok"] = False
    assert h("c1", "s3cret", {}) == (503, {"ok": False, "error": "Connector system is not ready"})


def test_enqueue_connector_event(integ):
    import modules.connector_manager as cm

    mgr = integ.mgr
    mgr.is_running = False
    assert ci._enqueue_connector_event({}) is False
    mgr.is_running = True
    integ.mp.setattr(cm, "get_connector_loop", lambda: None)
    assert ci._enqueue_connector_event({}) is False
    stopped = SimpleNamespace(is_running=lambda: False)
    integ.mp.setattr(cm, "get_connector_loop", lambda: stopped)
    assert ci._enqueue_connector_event({}) is False
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        integ.mp.setattr(cm, "get_connector_loop", lambda: loop)
        assert ci._enqueue_connector_event({"e": 1}) is True
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.01), loop).result(2)  # let it run

        def boom(coro, lp):
            coro.close()
            raise RuntimeError("closed")

        integ.mp.setattr(ci.asyncio, "run_coroutine_threadsafe", boom)
        assert ci._enqueue_connector_event({"e": 2}) is False
    finally:
        integ.mp.undo()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
    assert mgr.events == [{"e": 1}]


def test_has_enabled_twitch_connectors(integ):
    mgr = integ.mgr
    assert ci._has_enabled_twitch_connectors() is False
    mgr.connectors = {
        "off": Connector(connector_id="off", name="n", enabled=False,
                         trigger=create_trigger(TriggerType.TWITCH_BITS, "t", "T")),
        "hook": webhook_connector(),
        "none": Connector(connector_id="none", name="n"),
    }
    assert ci._has_enabled_twitch_connectors() is False
    mgr.connectors["tw"] = Connector(connector_id="tw", name="n",
                                     trigger=create_trigger(TriggerType.TWITCH_FOLLOW, "t", "T"))
    assert ci._has_enabled_twitch_connectors() is True
    mgr.connectors = None
    assert ci._has_enabled_twitch_connectors() is False


def test_initialize_integration_and_getter(monkeypatch):
    calls = []
    fake = SimpleNamespace(setup_twitch_integration=lambda: calls.append(1))
    monkeypatch.setattr(ci, "connector_integration", fake)
    ci.initialize_integration()
    assert calls == [1] and ci.get_integration() is fake
    notes = []

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ci, "connector_integration", SimpleNamespace(setup_twitch_integration=boom))
    monkeypatch.setattr(ci, "notify_critical", lambda msg, **kw: notes.append(kw))
    monkeypatch.setattr(ci, "nav_actions_main_tab", lambda t: [])
    ci.initialize_integration()
    assert notes[0]["dedupe_key"] == "connector:integration_init"

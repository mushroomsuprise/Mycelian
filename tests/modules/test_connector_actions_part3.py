# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_actions: greeting actions, game hook, OBS control, kwarg migration and the factory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import concurrent.futures
import types
from types import SimpleNamespace

import pytest

import modules
from modules import connector_actions as ca, connector_core as core
from modules.connector_core import ActionType

TRIGGER = {"trigger_id": "t1", "triggered_at": 123.0}


def run(coro):
    return asyncio.run(coro)


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


@pytest.fixture
def slot():
    data = {"connector_id": "cid", "connector_name": "Conn"}
    token = core._action_problem.set(data)
    yield data
    core._action_problem.reset(token)


class FakeGreetings:
    def __init__(self):
        self.calls = []
        self.add_result = (True, "", "gid")
        self.update_result = True
        self.process_result = None
        self.default = "Welcome {username}!"
        self.default_enabled = True

    def add_greeting(self, **kw):
        self.calls.append(("add", kw))
        return self.add_result

    def update_greeting(self, **kw):
        self.calls.append(("update", kw))
        return self.update_result

    def process_greeting(self, **kw):
        self.calls.append(("process", kw))
        return self.process_result

    def get_default_greeting(self):
        return self.default

    def get_default_greeting_enabled(self):
        return self.default_enabled


@pytest.fixture
def greet(monkeypatch):
    mgr = FakeGreetings()
    fake_module(monkeypatch, "chatbot_manager", get_manager=lambda: mgr)
    sent = []
    state = {"ok": True}
    fake_module(monkeypatch, "chatbot",
                dispatch_chatbot_response=lambda m, t: sent.append((m, t)) or state["ok"])
    return SimpleNamespace(mgr=mgr, sent=sent, state=state)


# --------------------------------------------------------------------------- add greeting


def test_add_greeting_success(greet):
    a = ca.create_action(ActionType.ADD_GREETING, "a", "n", user_id="{user_id}", username="{username}",
                         greeting_text="Hi {username}", greeting_enabled=0)
    assert a.greeting_enabled is False
    assert run(a.execute(TRIGGER, {"user_id": "42", "username": "Bob"}))
    assert greet.mgr.calls == [("add", {"user_id": "42", "username": "Bob", "greeting_text": "Hi Bob",
                                        "enabled": False})]


def test_add_greeting_already_exists_is_skipped(greet, slot):
    greet.mgr.add_result = (False, "User already has a greeting", None)
    a = ca.create_action(ActionType.ADD_GREETING, "a", "n", user_id="1", username="u", greeting_text="t")
    assert not run(a.execute(TRIGGER, {}))
    assert slot.get("skipped") is True and "reason" not in slot


def test_add_greeting_other_failure_reports(greet, slot):
    greet.mgr.add_result = (False, "Greeting text too long.", None)
    a = ca.create_action(ActionType.ADD_GREETING, "a", "n", user_id="1", username="u", greeting_text="t")
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "greeting text too long"
    greet.mgr.add_result = (False, None, None)
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the greeting couldn't be added"


def test_add_greeting_error_and_validate(monkeypatch):
    def boom():
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot_manager", get_manager=boom)
    a = ca.create_action(ActionType.ADD_GREETING, "a", "n")
    assert not run(a.execute(TRIGGER, {}))
    assert not a.validate_parameters()
    a.user_id = "1"
    assert not a.validate_parameters()
    a.username = "u"
    assert not a.validate_parameters()
    a.greeting_text = "t"
    assert a.validate_parameters()


# --------------------------------------------------------------------------- update greeting


def test_update_greeting_paths(greet):
    a = ca.create_action(ActionType.UPDATE_GREETING, "a", "n", greeting_id="g1", greeting_enabled=1)
    assert a.greeting_enabled is True
    assert run(a.execute(TRIGGER, {}))
    assert greet.mgr.calls[-1] == ("update", {"greeting_id": "g1", "greeting_text": None, "enabled": True})
    greet.mgr.update_result = False
    assert not run(a.execute(TRIGGER, {}))


def test_update_greeting_error_and_validate(monkeypatch):
    def boom():
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot_manager", get_manager=boom)
    a = ca.create_action(ActionType.UPDATE_GREETING, "a", "n")
    assert not run(a.execute(TRIGGER, {}))
    assert not a.validate_parameters()
    a.greeting_id = "g"
    assert not a.validate_parameters()
    a.greeting_enabled = False
    assert a.validate_parameters()
    a.greeting_enabled = None
    a.greeting_text = "t"
    assert a.validate_parameters()


# --------------------------------------------------------------------------- send greeting


def _send(**kw):
    kw.setdefault("user_id", "{user_id}")
    kw.setdefault("username", "{username}")
    return ca.create_action(ActionType.SEND_GREETING, "a", "n", **kw)


EVENT = {"user_id": "7", "username": "Bob"}


def test_send_greeting_processed_message(greet):
    greet.mgr.process_result = "Hello Bob"
    assert run(_send().execute(TRIGGER, EVENT))
    assert greet.mgr.calls[-1] == ("process", {"user_id": "7", "username": "Bob", "current_time": 123.0})
    assert greet.sent == [("Hello Bob", ["twitch"])]
    greet.state["ok"] = False
    assert not run(_send().execute(TRIGGER, EVENT))


def test_send_greeting_force_default(greet):
    assert run(_send().execute(TRIGGER, EVENT))
    assert greet.sent == [("Welcome Bob!", ["twitch"])]
    greet.state["ok"] = False
    assert not run(_send().execute(TRIGGER, EVENT))


@pytest.mark.parametrize("default,enabled,force", [("", True, True), ("Hi", False, True), ("Hi", True, False)])
def test_send_greeting_nothing_to_send(greet, default, enabled, force):
    greet.mgr.default, greet.mgr.default_enabled = default, enabled
    assert run(_send(force_send=force).execute(TRIGGER, EVENT))
    assert greet.sent == []


def test_send_greeting_error_and_validate(monkeypatch):
    def boom():
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot_manager", get_manager=boom)
    a = ca.create_action(ActionType.SEND_GREETING, "a", "n")
    assert not run(a.execute(TRIGGER, {}))
    assert not a.validate_parameters()
    a.user_id = "1"
    assert not a.validate_parameters()
    a.username = "u"
    assert a.validate_parameters()


# --------------------------------------------------------------------------- game hook


def _done(value):
    f = concurrent.futures.Future()
    f.set_result(value)
    return f


class FakeHooks:
    def __init__(self, ready=True, result=(True, "", {"x": 1})):
        self.ready = ready
        self.result = result
        self.calls = []

    def is_game_hook_ready(self, game_id):
        return self.ready

    def enqueue_game_hook_write(self, game_id, op, args):
        self.calls.append((game_id, op, args))
        if isinstance(self.result, Exception):
            raise self.result
        return _done(self.result)


def _hook(monkeypatch, **kw):
    svc = FakeHooks(**kw)
    fake_module(monkeypatch, "game_hooks_service", game_hooks_service=svc)
    return svc


def test_game_hook_not_ready_is_skipped(monkeypatch, slot):
    svc = _hook(monkeypatch, ready=False)
    a = ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="add_gil")
    assert a.game_id == "ff7"
    assert not run(a.execute(TRIGGER, {}))
    assert slot["skipped"] is True and svc.calls == []


def test_game_hook_success_records_outputs(monkeypatch):
    svc = _hook(monkeypatch, result=(True, "", {"gil": 100}))
    a = ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="add_gil",
                         hook_arguments={"amount": "{amount}", "n": 5})
    trig = dict(TRIGGER, action_results={}, action_index=2)
    assert run(a.execute(trig, {"amount": 50}))
    assert svc.calls == [("ff7", "add_gil", {"amount": "50", "n": 5})]
    assert trig["action_results"] == {"2": {"gil": 100}}


def test_game_hook_two_tuple_and_no_results(monkeypatch):
    _hook(monkeypatch, result=(True, ""))
    trig = dict(TRIGGER, action_results={}, action_index=0)
    assert run(ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="o").execute(trig, {}))
    assert trig["action_results"] == {}


def test_game_hook_failure_reports(monkeypatch, slot):
    _hook(monkeypatch, result=(False, "not attached ✓", None))
    a = ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="op")
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the game didn't accept the change" and "not attached" in slot["details"]


def test_game_hook_invalid_result(monkeypatch, slot):
    _hook(monkeypatch, result="garbage")
    assert not run(ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="op").execute(TRIGGER, {}))
    assert "invalid game hook result" in slot["details"]


def test_game_hook_timeout(monkeypatch, slot):
    _hook(monkeypatch)

    async def timeout(aw, timeout):
        aw.cancel() if hasattr(aw, "cancel") else None
        raise asyncio.TimeoutError

    monkeypatch.setattr(ca.asyncio, "wait_for", timeout)
    assert not run(ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="op").execute(TRIGGER, {}))
    assert slot["reason"] == "the game didn't respond in time"


def test_game_hook_exception(monkeypatch):
    _hook(monkeypatch, result=RuntimeError("x"))
    assert not run(ca.create_action(ActionType.GAME_HOOK, "g", "n", operation="op").execute(TRIGGER, {}))


# --------------------------------------------------------------------------- OBS control


class FakeObs:
    def __init__(self, result=(True, "")):
        self.result = result
        self.calls = []

    def enqueue_obs_request(self, op, args):
        self.calls.append((op, args))
        if isinstance(self.result, Exception):
            raise self.result
        return _done(self.result)


def _obs(monkeypatch, **kw):
    svc = FakeObs(**kw)
    fake_module(monkeypatch, "obs_service", obs_service=svc)
    return svc


def test_obs_control_validate():
    a = ca.create_action(ActionType.OBS_CONTROL, "o", "n")
    assert not a.validate_parameters()
    a.operation = "  "
    assert not a.validate_parameters()
    a.operation = "set_scene"
    assert a.validate_parameters()


def test_obs_control_success(monkeypatch):
    svc = _obs(monkeypatch)
    a = ca.create_action(ActionType.OBS_CONTROL, "o", "n", operation="set_scene",
                         obs_arguments={"scene": "{scene}", "flag": True})
    assert run(a.execute(TRIGGER, {"scene": "Main"}))
    assert svc.calls == [("set_scene", {"scene": "Main", "flag": True})]


@pytest.mark.parametrize("result,detail", [((False, "no scene"), "no scene"), ((False,), "unknown error"),
                                            ("bad", "unknown error"), ((), "unknown error")])
def test_obs_control_failure_reports(monkeypatch, slot, result, detail):
    _obs(monkeypatch, result=result)
    assert not run(ca.create_action(ActionType.OBS_CONTROL, "o", "n", operation="op").execute(TRIGGER, {}))
    assert slot["reason"] == "OBS didn't accept the request" and detail in slot["details"]


def test_obs_control_timeout_and_error(monkeypatch, slot):
    _obs(monkeypatch)

    async def timeout(aw, timeout):
        raise asyncio.TimeoutError

    monkeypatch.setattr(ca.asyncio, "wait_for", timeout)
    assert not run(ca.create_action(ActionType.OBS_CONTROL, "o", "n", operation="op").execute(TRIGGER, {}))
    assert slot["reason"] == "OBS didn't respond in time"
    monkeypatch.undo()
    _obs(monkeypatch, result=RuntimeError("x"))
    assert not run(ca.create_action(ActionType.OBS_CONTROL, "o", "n", operation="op").execute(TRIGGER, {}))


# --------------------------------------------------------------------------- migrate_action_kwargs / create_action


def test_migrate_greeting_enabled():
    m = ca.migrate_action_kwargs
    assert m(ActionType.ADD_GREETING, {"enabled": None}) == {"greeting_enabled": True, "enabled": True}
    assert m(ActionType.ADD_GREETING, {"enabled": 0}) == {"greeting_enabled": False, "enabled": True}
    assert m(ActionType.UPDATE_GREETING, {"enabled": None}) == {"greeting_enabled": None, "enabled": True}
    assert m(ActionType.UPDATE_GREETING, {"enabled": 1}) == {"greeting_enabled": True, "enabled": True}
    both = {"enabled": False, "greeting_enabled": True}
    assert m(ActionType.ADD_GREETING, both) == both
    assert m(ActionType.ADD_GREETING, {}) == {}


def test_migrate_execute_command_timeout():
    m = ca.migrate_action_kwargs
    assert m(ActionType.EXECUTE_COMMAND, {"timeout": 5}) == {"timeout_seconds": 5}
    assert m(ActionType.EXECUTE_COMMAND, {"timeout": 5, "timeout_seconds": 9}) == {"timeout": 5, "timeout_seconds": 9}


def test_migrate_trigger_alert():
    m = ca.migrate_action_kwargs
    out = m(ActionType.TRIGGER_ALERT, {"alert_type": "tips", "alert_data": '{"amount": "1"}',
                                       "amount": "9", "username": "u", "currency": "EUR"})
    assert out == {"alert_type": "donation", "alert_data": {"amount": "1", "username": "u", "currency": "EUR"}}
    assert m(ActionType.TRIGGER_ALERT, {"alert_type": "custom"})["alert_type"] == "custom"
    assert m(ActionType.TRIGGER_ALERT, {"alert_data": ""}) == {"alert_data": {}}
    assert m(ActionType.TRIGGER_ALERT, {"alert_data": "{bad"}) == {"alert_data": {}}
    assert m(ActionType.TRIGGER_ALERT, {"alert_data": [1]}) == {"alert_data": {}}
    src = {"alert_data": {"a": 1}, "reward_id": "r"}
    out = m(ActionType.TRIGGER_ALERT, src)
    assert out["alert_data"] == {"a": 1, "reward_id": "r"} and src["alert_data"] == {"a": 1}


def test_migrate_other_type_copies():
    src = {"x": 1}
    out = ca.migrate_action_kwargs(ActionType.API_CALL, src)
    assert out == src and out is not src


def test_create_action_every_type():
    for at in ActionType:
        try:
            action = ca.create_action(at, "id", "name", junk=1)
        except ValueError:
            continue
        assert action.action_type == at and action.action_id == "id" and action.name == "name"
        assert not hasattr(action, "junk")


def test_create_action_unknown_type():
    with pytest.raises(ValueError):
        ca.create_action("nope", "a", "n")

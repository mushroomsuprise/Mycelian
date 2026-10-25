#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot commands & events runtime: permissions, substitution, lookups, timers."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules import chatbot_core as core
from modules import chatbot_custom_variables as ccv
from modules import chatbot_lookups as lookups
from modules import chatbot_manager as cm
from modules import chatbot_variables as cv
from modules.chatbot_core import (
    ChatCommand,
    ChatEvent,
    CommandType,
    ComparisonOperator,
    EventType,
    TriggerCondition,
    render_template,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class _DB:
    def __init__(self):
        self.store: dict = {}
        self.writes: list = []
        self.lock = threading.Lock()

    def get_data(self, path, request_etag=False):
        with self.lock:
            return json.loads(json.dumps(self.store.get(path, {})))

    def get_data_checked(self, path):
        return self.get_data(path), True

    def set_data(self, path, data):
        with self.lock:
            self.writes.append(path)
            self.store[path] = json.loads(json.dumps(data))
            return True


@pytest.fixture()
def stats(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("modules.statistics_manager.get_statistics_manager", lambda: fake)
    return fake


@pytest.fixture()
def custom(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(ccv, "load_custom_variables", lambda: dict(store))
    return store


@pytest.fixture()
def db(monkeypatch, stats):
    fake = _DB()
    monkeypatch.setattr("modules.database_manager.get_data", fake.get_data)
    monkeypatch.setattr("modules.database_manager.get_data_checked", fake.get_data_checked)
    monkeypatch.setattr("modules.database_manager.set_data", fake.set_data)
    # No real flag file / background save thread (it calls time.time()).
    monkeypatch.setattr(cm, "GreetingFlagManager", lambda: MagicMock())
    return fake


@pytest.fixture()
def manager_factory(db):
    made = []

    def make():
        manager = cm.ChatbotManager()
        manager.flush_pending_saves()
        made.append(manager)
        return manager

    yield make
    for manager in made:
        manager.cleanup()


class FakeTwitch:
    """Stand-in for twitch.twitch_api with a scripted generic_api_call."""

    def __init__(self, routes=None):
        self.is_connected = True
        self.user_id = "1000"
        self.user = SimpleNamespace(login="streamer", display_name="Streamer")
        self.authscope = [SimpleNamespace(value=s) for s in (
            "moderator:read:followers", "channel:read:subscriptions", "bits:read",
            "moderator:manage:shoutouts", "channel:manage:broadcast", "channel:manage:raids",
            "channel:manage:ads",
        )]
        self.routes = routes or {}
        self.calls: list = []

    async def generic_api_call(self, url, method="GET", params=None, json_data=None, headers=None):
        self.calls.append({"url": url, "method": method, "params": dict(params or {}),
                           "json": json_data, "headers": headers})
        path = url.replace(lookups.HELIX, "")
        handler = self.routes.get((method, path))
        if handler is None:
            raise Exception(f"Twitch API Error 404: no route {method} {path}")
        result = handler(params or {}, json_data)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture()
def twitch(monkeypatch):
    fake = FakeTwitch()
    monkeypatch.setattr(lookups, "_api", lambda: fake)
    return fake


# --------------------------------------------------------------------------
# Permissions and blocking
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "badges,level",
    [
        (None, "everyone"),
        ("", "everyone"),
        ("subscriber/12", "subscriber"),
        ("founder/0", "subscriber"),
        ("vip/1,subscriber/3", "vip"),
        ("moderator/1,vip/1", "moderator"),
        ("broadcaster/1", "broadcaster"),
        ([{"set_id": "moderator", "id": "1"}], "moderator"),
    ],
)
def test_user_level_from_badges(badges, level):
    assert core.user_permission_level({"badges": badges}) == level


@pytest.mark.parametrize(
    "required,allowed",
    [
        ("everyone", {"everyone", "subscriber", "vip", "moderator", "broadcaster"}),
        ("subscriber", {"subscriber", "vip", "moderator", "broadcaster"}),
        ("vip", {"vip", "moderator", "broadcaster"}),
        ("moderator", {"moderator", "broadcaster"}),
    ],
)
def test_permission_ladder(required, allowed):
    samples = {
        "everyone": None, "subscriber": "subscriber/1", "vip": "vip/1",
        "moderator": "moderator/1", "broadcaster": "broadcaster/1",
    }
    for level, badges in samples.items():
        assert core.permission_allows(required, {"badges": badges}) == (level in allowed)


def test_mod_only_migrates_to_permission():
    command = ChatCommand.from_dict({"command_name": "x", "mod_only": True})
    assert command.permission == "moderator"
    assert command.to_dict()["permission"] == "moderator"
    assert ChatCommand(command_name="x", permission="vip").mod_only is False


def test_blocked_commands_stay_silent(manager_factory):
    manager = manager_factory()
    manager.add_command(ChatCommand(command_id="a", command_name="hi", response_text="first", cooldown=60))
    manager.add_command(ChatCommand(command_id="b", command_name="hi", response_text="second"))
    manager.add_command(ChatCommand(command_id="m", command_name="mods", response_text="ok", permission="moderator"))
    manager.add_command(ChatCommand(command_id="u", command_name="once", response_text="ok", usage_limit=1))

    assert manager.process_chat_message({"message": "!hi", "username": "v"})[0] == "first"
    blocked = manager.process_chat_message({"message": "!hi", "username": "v"})
    assert blocked is not None and blocked[0] == ""  # silent; "second" not tried
    assert manager.process_chat_message({"message": "!mods", "username": "v", "badges": "vip/1"})[0] == ""
    assert manager.process_chat_message({"message": "!mods", "username": "v", "badges": "moderator/1"})[0] == "ok"
    assert manager.process_chat_message({"message": "!once", "username": "v"})[0] == "ok"
    assert manager.process_chat_message({"message": "!once", "username": "v"})[0] == ""
    assert manager.reset_usage("u")
    assert manager.process_chat_message({"message": "!once", "username": "v"})[0] == "ok"


def test_quote_cooldown_is_silent(manager_factory):
    manager = manager_factory()
    manager.add_quote("hello", "a", "b")
    assert manager.process_chat_message({"message": "!quote", "username": "v"})[0].endswith("hello")
    assert manager.process_chat_message({"message": "!quote", "username": "v"})[0] == ""


def test_deliver_skips_empty_message():
    from modules import chatbot

    with patch.object(chatbot, "dispatch_chatbot_response") as dispatch:
        assert chatbot.deliver_chatbot_output("   ", ["twitch"]) is False
        dispatch.assert_not_called()


def test_command_names_and_aliases_are_cleaned():
    command = ChatCommand.from_dict({
        "command_name": "!Discord",
        "aliases": ["!DC", "<nicegui.events.ValueChangeEventArguments object>", "dc", "discord"],
    })
    assert command.command_name == "discord"
    assert command.aliases == ["dc"]
    assert cm._command_matches_name(command, "DC".lower())


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------


def test_trigger_condition_fixes():
    cond = TriggerCondition("amount", ComparisonOperator.GREATER_THAN_OR_EQUAL, "10")
    assert cond.evaluate({"amount": "12.5"})
    assert cond.value == "10"  # never mutated
    assert cond.evaluate({"amount": 10})
    assert not cond.evaluate({"amount": "9.99"})
    assert not cond.evaluate({})
    assert not cond.evaluate({"amount": None})
    assert TriggerCondition("x", ComparisonOperator.EQUAL, "Hello").evaluate({"x": "hello"})
    assert TriggerCondition("x", ComparisonOperator.EQUAL, "5").evaluate({"x": 5.0})
    assert TriggerCondition("x", ComparisonOperator.CONTAINS, "1").evaluate({"x": "100"})
    assert TriggerCondition("x", ComparisonOperator.STARTS_WITH, "AB").evaluate({"x": "abc"})
    assert not TriggerCondition("x", ComparisonOperator.GREATER_THAN, "b").evaluate({"x": "c"})


def test_conditions_round_trip():
    command = ChatCommand(command_name="x", conditions=[{"field": "amount", "operator": "greater_than", "value": 5}])
    again = ChatCommand.from_dict(json.loads(json.dumps(command.to_dict())))
    assert [c.to_dict() for c in again.conditions] == [{"field": "amount", "operator": "greater_than", "value": 5}]
    event = ChatEvent(conditions=[{"field": "tier", "operator": "equal", "value": "2"}])
    assert ChatEvent.from_dict(event.to_dict()).conditions[0].value == "2"


# --------------------------------------------------------------------------
# Substitution
# --------------------------------------------------------------------------


def test_substitution_is_single_pass(monkeypatch, custom):
    calls = []
    monkeypatch.setattr(core, "get_statistics_value", lambda name: calls.append(name) or "STAT")
    custom["custom_y"] = {"kind": "text", "params": {"template": "SECRET"}}
    command = ChatCommand(
        command_name="say",
        response_text="{username}: {command_message} / {command_word_2} / {who}",
        argument_mappings={"who": 1},
    )
    data = {"message": "!say {stats.x} {custom_y}", "username": "{custom_y}",
            "command_args": ["{stats.x}", "{custom_y}"]}
    out = command.render_response(data)
    assert out == "{custom_y}: {stats.x} {custom_y} / {custom_y} / {stats.x}"
    assert calls == []


def test_format_suffix_and_unknown_tokens():
    ctx = {"amount": 1234.5, "when": "2026-03-04T10:05:00", "n": "7"}
    assert render_template("{amount:,.2f}", ctx) == "1,234.50"
    assert render_template("{when:%-d/%-m %H:%M}", ctx) == "4/3 10:05"
    assert render_template("{n:03d}", ctx) == "007"
    assert render_template('{nope} {"a": 1} {UserName}', ctx) == '{nope} {"a": 1} {UserName}'
    assert render_template("{date:%Y}", {}) == str(datetime.now().year)
    assert len(render_template("{datetime}", {})) == len("2026-03-14 19:30")


def test_cooldown_and_usage_left_values():
    assert ChatCommand(command_name="a").render_response({}, None) == ""
    command = ChatCommand(command_name="a", response_text="{cooldown}|{usage_left}")
    assert command.render_response({}) == "0|unlimited"
    command = ChatCommand(command_name="a", response_text="{cooldown}|{usage_left}", cooldown=30,
                          usage_limit=5, usage_count=2)
    assert command.render_response({}) == "30|3"


def test_argument_defaults_and_serialization(custom):
    command = ChatCommand(
        command_name="w",
        response_text="{city}-{unit}",
        argument_mappings={"city": {"position": 1, "default": "near {username}"}, "unit": 2},
    )
    assert command.render_response({"username": "Ann", "command_args": []}) == "near Ann-"
    assert command.render_response({"username": "Ann", "command_args": ["Oslo", "C"]}) == "Oslo-C"
    assert command.to_dict()["argument_mappings"] == {
        "city": {"position": 1, "default": "near {username}"}, "unit": 2}


def test_context_username_wins_over_live_basics(custom):
    custom["custom_hi"] = "{custom_hi} - Custom: Hi {username}"
    command = ChatCommand(command_name="x", response_text="{custom_hi}")
    assert command.render_response({"username": "Chatter"}) == "Hi Chatter"


# --------------------------------------------------------------------------
# Safe evaluator and custom variables
# --------------------------------------------------------------------------


def test_safe_eval():
    assert ccv.safe_eval_math("((1 + 2) * (3 + (4 - 1))) / 2") == 9
    assert ccv.safe_eval_math("-2 ** 2 + round(2.567, 2) + max(1, 5) + floor(1.5)") == pytest.approx(-4 + 2.57 + 5 + 1)
    for bad in ("__import__('os')", "(1).real", "x + 1", "open('f')", "[1, 2]", "9 ** 9999",
                "'a' * 3", "lambda: 1", "abs(x=1)"):
        with pytest.raises(ValueError):
            ccv.safe_eval_math(bad)


def test_custom_variable_kinds(custom):
    ctx = {"amount": "10", "name": "Bob", "followed": "2020-01-01T00:00:00Z"}
    defs = {
        "math": {"kind": "math", "params": {"left": "{amount}", "op": "/", "right": "4", "decimals": 1}},
        "zero": {"kind": "math", "params": {"left": "{amount}", "op": "/", "right": "0"}},
        "ifk": {"kind": "if", "params": {"left": "{amount}", "op": "greater_than", "right": "9",
                                          "then": "big {name}", "else": "small"}},
        "age": {"kind": "age", "params": {"date": "{followed}", "style": "years"}},
        "text": {"kind": "text", "params": {"template": "hey {name}"}},
        "inj": {"kind": "math", "params": {"left": "{name}", "op": "+", "right": "1"}},
    }
    for name, definition in defs.items():
        custom[f"custom_{name}"] = definition
    assert ccv.evaluate_custom_variable("custom_math", ctx) == "2.5"
    assert ccv.evaluate_custom_variable("custom_zero", ctx) == "0"
    assert ccv.evaluate_custom_variable("custom_ifk", ctx) == "big Bob"
    assert int(ccv.evaluate_custom_variable("custom_age", ctx)) >= 6
    assert ccv.evaluate_custom_variable("custom_text", ctx) == "hey Bob"
    assert ccv.evaluate_custom_variable("custom_inj", ctx) == "0"
    assert ccv.evaluate_custom_variable("custom_missing", ctx) is None
    custom["custom_legacy"] = "{custom_legacy} - Custom: math(({amount} + 2) * (1 + (2 - 1)))"
    custom["custom_cmp"] = "{custom_cmp} - Custom: compare({amount}, >=, 10)"
    assert ccv.evaluate_custom_variable("custom_legacy", ctx) == "24"
    assert ccv.evaluate_custom_variable("custom_cmp", ctx) == "True"
    assert ccv.compile_custom_variable("math", defs["math"]["params"]) == "math(round({amount} / 4, 1))"
    assert ccv.preview_custom_variable(defs["text"], ctx) == "hey Bob"
    assert ccv.preview_custom_variable({"kind": "math", "params": {"left": "", "op": "+", "right": ""}}).startswith("Error")


def test_custom_definition_storage(db):
    ccv.invalidate_custom_variable_cache()
    db.store[ccv.CUSTOM_VARIABLES_PATH] = {"data": {"custom_old": "{custom_old} - Custom: 1+1"}}
    assert ccv.save_custom_definition("Big Num", {"kind": "math", "params": {"left": "2", "op": "*", "right": "3"}})
    stored = db.store[ccv.CUSTOM_VARIABLES_PATH]["data"]
    assert stored["custom_big_num"]["expression"] == "math(2 * 3)"
    assert stored["custom_old"] == "{custom_old} - Custom: 1+1"
    defs = ccv.load_custom_definitions()
    assert defs["custom_old"]["kind"] == "expression"
    assert ccv.evaluate_custom_variable("custom_big_num", {}) == "6"
    assert ccv.delete_custom_definition("big_num")
    assert "custom_big_num" not in db.store[ccv.CUSTOM_VARIABLES_PATH]["data"]
    assert not ccv.save_custom_definition("Bad!", {"kind": "text", "params": {"template": "x"}})
    ccv.invalidate_custom_variable_cache()


# --------------------------------------------------------------------------
# Migrations and persistence
# --------------------------------------------------------------------------


def test_repeating_command_becomes_interval_event(db, manager_factory):
    db.store["BotData/Commands"] = [{
        "command_id": "c1", "name": "Socials", "command_name": "socials", "response_text": "follow me",
        "repeating_enabled": True, "repeat_interval": 0, "enabled": False,
        "reply_targets": ["twitch"], "discord_channels": [{"guild_id": "g", "channel_id": "c"}],
    }]
    manager = manager_factory()
    command = manager.commands["c1"]
    assert command.repeating_enabled is False
    events = list(manager.events.values())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == EventType.INTERVAL
    assert event.interval == 60 and event.enabled is False
    assert event.response_text == "follow me"
    assert event.discord_channels == [{"guild_id": "g", "channel_id": "c"}]
    assert db.store["BotData/Events"][0]["event_type"] == "interval"
    assert db.store["BotData/Commands"][0]["repeating_enabled"] is False
    assert db.store["BotData/Commands"][0]["schema_version"] == 2


def test_persistent_counter(db, manager_factory):
    db.store["BotData/Commands"] = [
        {"command_id": "old", "command_name": "deaths", "command_type": "counter",
         "persistent_counter": False, "counter_value": 7},
        {"command_id": "new", "command_name": "wins", "command_type": "counter",
         "persistent_counter": False, "counter_value": 3, "schema_version": 2},
    ]
    manager = manager_factory()
    assert manager.commands["old"].counter_value == 7  # legacy: persisted
    assert manager.commands["old"].persistent_counter is True
    assert manager.commands["new"].counter_value == 0  # resets on start


def test_update_keeps_runtime_and_conditions(manager_factory):
    manager = manager_factory()
    manager.add_command(ChatCommand(command_id="c", command_name="x", response_text="a",
                                    conditions=[{"field": "a", "operator": "equal", "value": "1"}]))
    stored = manager.commands["c"]
    stored.counter_value, stored.usage_count, stored.trigger_count = 4, 5, 6
    manager.update_command("c", ChatCommand(command_name="x", response_text="b", permission="vip",
                                            lookup={"id": "uptime"}, lookup_fallback="offline"))
    updated = manager.commands["c"]
    assert (updated.counter_value, updated.usage_count, updated.trigger_count) == (4, 5, 6)
    assert updated.permission == "vip" and updated.lookup == {"id": "uptime"}
    assert len(updated.conditions) == 1
    manager.update_command("c", ChatCommand(command_name="x"), keep_runtime=False)
    assert manager.commands["c"].usage_count == 0


def test_legacy_api_migration():
    command = ChatCommand.from_dict({
        "command_name": "age", "api_enabled": True,
        "api_endpoint": "https://api.twitch.tv/helix/users", "api_method": "get",
        "api_parameters": {"id": "[Command user's ID]", "login": "{trigger_username}"},
        "api_variable_processing": ["days=math({api_view_count} + 1)"],
        "api_headers": {"X-Test": "1"},
    })
    assert command.api_enabled is False
    assert command.lookup == {
        "id": "advanced", "method": "GET", "path": "https://api.twitch.tv/helix/users",
        "params": {"id": "{chatter_id}", "login": "{chatter_login}"}, "body": "",
        "processing": ["days=math({api_view_count} + 1)"], "headers": {"X-Test": "1"},
    }
    again = ChatCommand.from_dict(command.to_dict())
    assert again.lookup == command.lookup


def test_reply_targets_discord_only():
    discord = [{"guild_id": "g", "channel_id": "c"}]
    assert ChatCommand(command_name="x", reply_targets=[], discord_channels=discord).reply_targets == []
    assert ChatCommand(command_name="x", reply_targets=[]).reply_targets == ["twitch"]
    assert ChatCommand(command_name="x", reply_targets=[], output_mode="banner").reply_targets == []
    assert ChatCommand(command_name="x").reply_targets == ["twitch"]
    from modules import chatbot

    with (
        patch.object(chatbot, "_send_chatbot_message_blocking") as twitch_send,
        patch("modules.discord_service.send_to_channels", return_value=True) as discord_send,
    ):
        assert chatbot._dispatch_chatbot_response_blocking("hi", [], discord_channels=discord)
    twitch_send.assert_not_called()
    discord_send.assert_called_once()


def test_split_chat_message():
    assert core.split_chat_message("short") == ["short"]
    parts = core.split_chat_message(("word " * 250).strip())
    assert len(parts) == 3 and all(len(p) <= 500 for p in parts)
    assert " ".join(parts) == ("word " * 250).strip()
    long_parts = core.split_chat_message("abc " * 1000)
    assert len(long_parts) == 3 and long_parts[-1].endswith("…")
    assert all(len(p) <= 500 for p in long_parts)
    assert core.split_chat_message("x" * 1200)[0] == "x" * 500


def test_long_reply_is_sent_in_parts(monkeypatch):
    from modules import chatbot

    sent = []
    monkeypatch.setattr(chatbot, "chatbot_api", object())
    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking",
                        lambda message, reply=None: sent.append((message, reply)) or True)
    assert chatbot._send_chatbot_message_blocking(("word " * 150).strip(), "parent")
    assert len(sent) == 2 and sent[0][1] == "parent" and sent[1][1] is None


# --------------------------------------------------------------------------
# Timers
# --------------------------------------------------------------------------


def test_interval_waits_one_full_interval(manager_factory):
    posts = []
    manager = manager_factory()
    with (
        patch("modules.chatbot.deliver_chatbot_output", lambda *a, **k: posts.append(time.monotonic()) or True),
        patch("modules.chatbot.is_chatbot_connected", return_value=True),
    ):
        started = time.monotonic()
        manager.add_event(ChatEvent(event_id="t", name="T", event_type=EventType.INTERVAL,
                                    interval=0.3, response_text="tick"))
        time.sleep(0.15)
        assert posts == []
        time.sleep(0.3)
        manager.toggle_item("t", False)
        assert len(posts) == 1 and posts[0] - started >= 0.29


def test_interval_skips_post_without_chat(manager_factory):
    posts = []
    manager = manager_factory()
    with (
        patch("modules.chatbot.deliver_chatbot_output", lambda *a, **k: posts.append(1) or True),
        patch("modules.chatbot.is_chatbot_connected", return_value=False),
    ):
        manager.add_event(ChatEvent(event_id="t", name="T", event_type=EventType.INTERVAL,
                                    interval=0.1, response_text="tick"))
        time.sleep(0.35)
        manager.toggle_item("t", False)
    assert posts == []


def test_timers_start_on_load_and_type_switch_stops_repeating(db, manager_factory):
    db.store["BotData/Events"] = [ChatEvent(event_id="e", name="T", event_type=EventType.INTERVAL,
                                            interval=600, response_text="x").to_dict()]
    manager = manager_factory()
    thread = manager.repeating_tasks.get("e")
    assert thread is not None and thread.is_alive()
    stop = manager._repeating_stops["e"]
    assert manager.update_event("e", ChatEvent(name="T", event_type=EventType.FOLLOW, interval=600,
                                               response_text="x"))
    assert stop.is_set() and "e" not in manager.repeating_tasks
    manager.add_event(ChatEvent(event_id="f", event_type=EventType.FOLLOW, interval=5, response_text="y"))
    assert "f" not in manager.repeating_tasks
    manager.toggle_item("e", False)
    manager.toggle_item("e", True)
    assert "e" not in manager.repeating_tasks


def test_toggle_keeps_settings(manager_factory):
    manager = manager_factory()
    manager.add_command(ChatCommand(command_id="c", command_name="x", response_text="a"))
    manager.toggle_item("c", False)
    manager.toggle_item("c", True)
    assert manager.commands["c"].enabled and manager.commands["c"].response_text == "a"


def test_fallback_mode_starts_timers():
    from modules import chatbot

    bot = object.__new__(chatbot.Chatbot_API)
    bot.client_id = ""
    bot.client_secret = ""
    bot.using_fallback = False
    bot.load_auth_data = lambda: False
    bot._migrate_legacy_token_expiry_if_needed = AsyncMock()
    fake_manager = MagicMock()
    with patch("modules.chatbot_manager.get_manager", return_value=fake_manager):
        assert asyncio.run(bot.stage_chatbot_api()) is True
    assert bot.using_fallback is True
    fake_manager.start_deferred_interval_events.assert_called_once()


def test_specific_time_validation_and_once_per_minute(manager_factory):
    manager = manager_factory()
    assert not manager.add_event(ChatEvent(event_type=EventType.SPECIFIC_TIME, specific_time="25:00"))
    assert not manager.add_event(ChatEvent(event_type=EventType.SPECIFIC_TIME, specific_time=""))
    event = ChatEvent(event_id="s", event_type=EventType.SPECIFIC_TIME, specific_time="9:05")
    assert manager.add_event(event) and event.specific_time == "09:05"
    now = datetime(2026, 3, 14, 9, 5, 10)
    due, key = cm.ChatbotManager._specific_time_due(event, now, None)
    assert due
    assert not cm.ChatbotManager._specific_time_due(event, now + timedelta(seconds=20), key)[0]
    assert not cm.ChatbotManager._specific_time_due(event, now + timedelta(minutes=1), key)[0]
    assert cm.ChatbotManager._specific_time_due(event, now + timedelta(days=1), key)[0]


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------


def _users(**rows):
    def handler(params, _body):
        key = params.get("id") or params.get("login")
        row = rows.get(key)
        return {"data": [row] if row else []}

    return handler


def test_follow_age_lookup(twitch):
    followed = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()
    twitch.routes[("GET", "/users")] = _users(**{"42": {"id": "42", "login": "bob", "display_name": "Bob"}})
    twitch.routes[("GET", "/channels/followers")] = lambda p, b: {"data": [{"followed_at": followed}]}
    out = asyncio.run(lookups.run_lookup("follow_age", {"id": "follow_age"}, {"user_id": "42", "username": "Bob"}))
    assert out["lookup_ok"] and out["is_following"] == "yes"
    assert out["followage"].startswith("2 years")
    assert out["follow_days"] in ("799", "800")
    assert isinstance(out["followed_since"], datetime)
    assert twitch.calls[-1]["params"] == {"broadcaster_id": "1000", "user_id": "42"}
    twitch.routes[("GET", "/channels/followers")] = lambda p, b: {"data": []}
    out = asyncio.run(lookups.run_lookup("follow_age", {}, {"user_id": "42"}))
    assert not out["lookup_ok"] and out["lookup_reason"] == "not_following"
    assert core.choose_lookup_outcome(out, "") == "normal"
    assert core.choose_lookup_outcome(out, "Not following!") == "fallback"
    assert core.choose_lookup_outcome({"lookup_ok": False, "lookup_reason": "error"}, "") == "silent"


def test_uptime_lookup(twitch):
    started = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=12, seconds=5)).isoformat()
    twitch.routes[("GET", "/streams")] = lambda p, b: {"data": [{"started_at": started, "viewer_count": 42,
                                                                "title": "T", "game_name": "G"}]}
    out = asyncio.run(lookups.run_lookup("uptime", {}, {}))
    assert out["uptime"] == "3 hours, 12 minutes" and out["is_live"] == "yes"
    assert out["viewer_count"] == "42"


def test_shoutout_target_from_input(twitch):
    twitch.routes[("GET", "/users")] = _users(coolstreamer={"id": "77", "login": "coolstreamer",
                                                            "display_name": "CoolStreamer"})
    twitch.routes[("GET", "/channels")] = lambda p, b: {"data": [{"game_name": "Celeste", "title": "Hi"}]}
    twitch.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    twitch.routes[("POST", "/chat/shoutouts")] = lambda p, b: {"status": 204, "data": ""}
    config = {"id": "shoutout", "target": {"from": "input", "input": "streamer"}}
    ctx = {"user_id": "5", "streamer": "@CoolStreamer", "command_args": ["@CoolStreamer"]}
    out = asyncio.run(lookups.run_lookup("shoutout", config, ctx))
    assert out["lookup_ok"] and out["target_name"] == "CoolStreamer"
    assert out["target_url"] == "https://twitch.tv/coolstreamer" and out["target_game"] == "Celeste"
    shout = [c for c in twitch.calls if c["url"].endswith("/chat/shoutouts")][0]
    assert shout["params"] == {"from_broadcaster_id": "1000", "to_broadcaster_id": "77", "moderator_id": "1000"}
    out = asyncio.run(lookups.run_lookup("shoutout", {"target": {"from": "input", "input": 1}}, {"command_args": []}))
    assert out["lookup_reason"] == "no_target"


def test_event_lookup_targets_event_user(twitch):
    twitch.routes[("GET", "/users")] = _users(**{"9": {"id": "9", "login": "raider", "display_name": "Raider"}})
    twitch.routes[("GET", "/channels")] = lambda p, b: {"data": [{"game_name": "G", "title": "T"}]}
    twitch.routes[("GET", "/streams")] = lambda p, b: {"data": [{"id": "s"}]}
    out = asyncio.run(lookups.run_lookup("channel_info", {}, {"user_id": "9", "username": "Raider"}, kind="event"))
    assert out["target_login"] == "raider" and out["target_live"] == "yes"


def test_set_title_body(twitch):
    twitch.routes[("PATCH", "/channels")] = lambda p, b: {"status": 204, "data": ""}
    out = asyncio.run(lookups.run_lookup("set_title", {"id": "set_title"}, {"command_message": "New {stats.x} run"}))
    assert out["new_title"] == "New {stats.x} run"
    assert twitch.calls[-1]["json"] == {"title": "New {stats.x} run"}
    assert twitch.calls[-1]["params"] == {"broadcaster_id": "1000"}
    out = asyncio.run(lookups.run_lookup("set_title", {}, {"command_message": ""}))
    assert out["lookup_reason"] == "no_text"


def test_missing_scope_and_not_connected(twitch):
    twitch.authscope = []
    ok, reason = lookups.lookup_available("follow_age")
    assert not ok and "moderator:read:followers" in reason
    assert lookups.lookup_available("uptime") == (True, "")
    twitch.is_connected = False
    out = asyncio.run(lookups.run_lookup("uptime", {}, {}))
    assert out["lookup_reason"] == "not_connected"


def test_advanced_raw_request(twitch):
    twitch.routes[("GET", "/channels/followers")] = lambda p, b: {
        "total": 12, "data": [{"user_name": "Bob", "followed_at": "2024-01-01T00:00:00Z"}]}
    out = asyncio.run(lookups.run_raw_request(
        "GET", "channels/followers", {"user_id": "{chatter_id}"}, "",
        {"user_id": "42", "username": "Bob"}, processing=["double=math({api_total} * 2)"],
        headers={"Authorization": "nope", "X-Extra": "{chatter_login}"},
    ))
    call = twitch.calls[-1]
    assert call["url"] == "https://api.twitch.tv/helix/channels/followers"
    assert call["params"] == {"user_id": "42", "broadcaster_id": "1000"}
    assert call["headers"] == {"X-Extra": "bob"}
    assert out["api_status"] == 200 and out["api_success"] and out["api_total"] == 12
    assert out["api_user_name"] == "Bob" and out["api_data_0_followed_at"] == "2024-01-01T00:00:00Z"
    assert out["double"] == "24" and out["lookup_ok"]

    twitch.routes[("POST", "/streams/markers")] = lambda p, b: {"data": [{"position_seconds": 5}]}
    asyncio.run(lookups.run_raw_request("POST", "/helix/streams/markers", {}, '{"description": "{command_message}"}',
                                        {"command_message": 'say "hi"'}))
    assert twitch.calls[-1]["json"] == {"description": 'say "hi"', "user_id": "1000"}

    twitch.routes[("GET", "/users")] = lambda p, b: Exception("Twitch API Error 400: bad")
    out = asyncio.run(lookups.run_raw_request("GET", "/users", {"login": ""}, "", {}))
    assert out["api_status"] == 400 and out["lookup_ok"] is False
    out = asyncio.run(lookups.run_raw_request("GET", "https://evil.example/helix/users", {}, "", {}))
    assert out["lookup_ok"] is False and "api.twitch.tv" in out["lookup_error"]


def test_command_with_lookup_runs_outside_lock(manager_factory, twitch, custom):
    twitch.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    manager = manager_factory()
    manager.add_command(ChatCommand(command_id="u", command_name="uptime",
                                    response_text="Live for {uptime}", lookup={"id": "uptime"}))
    assert manager.message_may_call_api({"message": "!uptime"})
    result = manager.process_chat_message({"message": "!uptime", "username": "v"})
    assert result[0] == "Live for offline"
    twitch.routes[("GET", "/streams")] = lambda p, b: Exception("Twitch API Error 500: down")
    manager.commands["u"].lookup_fallback = "Can't tell ({lookup_error})"
    manager.commands["u"].usage_count = 0
    result = manager.process_chat_message({"message": "!uptime", "username": "v"})
    assert result[0].startswith("Can't tell (Twitch said")
    assert manager.commands["u"].usage_count == 1  # failed lookup still counts


def test_event_with_lookup_is_delivered_in_background(manager_factory, twitch, custom):
    twitch.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    delivered = threading.Event()
    sent = []
    manager = manager_factory()
    manager.add_event(ChatEvent(event_id="f", event_type=EventType.FOLLOW,
                                response_text="Hi {username}, we are {is_live} live",
                                lookup={"id": "uptime"}))
    with patch("modules.chatbot.deliver_chatbot_output",
               lambda msg, *a, **k: sent.append(msg) or delivered.set() or True):
        assert manager.process_event(EventType.FOLLOW, {"username": "Ann"}) is None
        assert delivered.wait(5)
    assert sent == ["Hi Ann, we are no live"]


# --------------------------------------------------------------------------
# Chat path, tests and catalog
# --------------------------------------------------------------------------


def test_own_messages_do_not_run_commands():
    from modules import twitch as twitch_module

    bot = MagicMock()
    bot.process_greeting.return_value = None
    api = object.__new__(twitch_module.Twitch_API)
    api._note_event_received = lambda: None
    message = SimpleNamespace(text="!hi", fragments=None)
    data = SimpleNamespace(event=SimpleNamespace(
        chatter_user_name="Bot", chatter_user_id="99", chatter_user_login="bot", message=message,
        message_id="m1", color="", badges=None, message_type="text", reply=None))
    with (
        patch.object(twitch_module, "get_chatbot_manager", return_value=bot),
        patch.object(twitch_module, "web_engine", SimpleNamespace(web_engine_instance=None)),
        patch.object(twitch_module, "statistics_manager", MagicMock()),
        patch("modules.chatbot.is_own_chat_message", return_value=True),
    ):
        asyncio.run(api.on_chat_message(data))
    bot.process_chat_message.assert_not_called()
    bot.process_event.assert_not_called()


def test_test_command_has_no_side_effects(manager_factory, stats, custom):
    manager = manager_factory()
    manager.add_command(ChatCommand(command_id="c", command_name="deaths", command_type=CommandType.COUNTER,
                                    response_text="{count} by {username} ({usage_left})", cooldown=60,
                                    usage_limit=3, permission="moderator", counter_value=4))
    stats.reset_mock()
    with patch("modules.chatbot.deliver_chatbot_output") as deliver:
        result = manager.test_command("c")
        again = manager.test_command("c")
    assert result["ok"] and result["triggered"] and result["response"] == "5 by TestViewer (2)"
    assert again["response"] == result["response"]
    command = manager.commands["c"]
    assert (command.counter_value, command.usage_count, command.trigger_count, command.last_used) == (4, 0, 0, 0)
    stats.increment_commands_triggered.assert_not_called()
    deliver.assert_not_called()
    legacy = asyncio.run(_await(manager.test_command("c")))
    assert legacy["success"] and legacy["message"] == result["response"]
    assert manager.test_command("missing")["ok"] is False


async def _await(value):
    return await value


def test_test_event_uses_its_own_type(manager_factory, custom):
    manager = manager_factory()
    manager.add_event(ChatEvent(event_id="b", event_type=EventType.BITS, bits_quantity=1000,
                                response_text="{username} cheered {amount}", lookup={"id": "uptime"}))
    result = manager.test_event("b")
    assert result["triggered"] and result["response"] == "TestViewer cheered 1000"
    assert result["lookup"] == "example"
    assert manager.events["b"].trigger_count == 0


def test_sample_data_passes_every_event_type():
    for etype in EventType:
        plain = ChatEvent(event_type=etype, specific_time="12:00", interval=60)
        assert plain.should_trigger(cv.sample_data("event", etype.value, plain)), etype
        filtered = ChatEvent(
            event_type=etype, specific_time="18:30", interval=60, bits_quantity=2500, sub_tier=2,
            resub_months=24, resub_tier=3, gift_sub_quantity=10, gift_sub_tier=2, donation_amount=50,
            raid_viewer_count=100, raid_raider_name="@BigRaider", channel_point_reward_name="Hug",
            hype_train_level=4, hype_train_end_level=5, chat_message_text="Good Morning",
            chat_message_match_type="contains",
        )
        assert filtered.should_trigger(cv.sample_data("event", etype.value, filtered)), etype


def test_chat_message_matching():
    event = ChatEvent(event_type=EventType.CHAT_MESSAGE, chat_message_text=" Hello ")
    assert event.should_trigger({"message": "hello"})
    assert not event.should_trigger({"message": "hello there"})
    assert ChatEvent(event_type=EventType.YOUTUBE_CHAT_MESSAGE).should_trigger({"message": "anything"})
    yt = ChatEvent(event_type=EventType.YOUTUBE_CHAT_MESSAGE, chat_message_text="!yt",
                   chat_message_match_type="starts_with")
    assert yt.should_trigger({"message": "!YT now"}) and not yt.should_trigger({"message": "hi"})
    raid = ChatEvent(event_type=EventType.RAID, raid_raider_name="Bob")
    assert raid.should_trigger({"username": "bob"})
    gift = ChatEvent(event_type=EventType.GIFT_SUBSCRIPTION, gift_sub_quantity=5)
    assert not gift.should_trigger({"amount": 1, "total_gifts": 100})


def test_catalog_consistency():
    values = {entry["value"] for entry in cv.EVENTS}
    assert values == {e.value for e in EventType}
    assert set(cv.EVENT_VARIABLES) == values
    probe = ChatEvent()
    for entry in cv.EVENTS:
        assert entry["group"] in ("Twitch", "YouTube", "Tips", "Time")
        for spec in entry["filters"]:
            assert hasattr(probe, spec["key"]), spec["key"]
            assert spec["kind"] in ("number", "text", "select", "time", "duration", "match")
    hype = [e for e in cv.EVENTS if e["value"].startswith("hype_train")]
    assert all(e["warning"] for e in hype)
    assert not any(v["key"] == "username" for v in cv.EVENT_VARIABLES["hype_train_progress"])
    for lookup in lookups.LOOKUPS:
        assert lookup["id"] in lookups._IMPLEMENTATIONS
        assert lookup["group"] in ("Viewers", "Your stream", "Other channels", "Do something")
        assert not {"clips:edit", "moderator:read:chatters"} & set(lookup["scopes"])
        assert cv.lookup_variables({"id": lookup["id"]})
    keys = [v["key"] for v in cv.command_variables("counter", ["city"], {"id": "follow_age"})]
    assert {"count", "city", "followage", "command_word_N"} <= set(keys)
    assert [p[0] for p in cv.PERMISSIONS] == ["everyone", "subscriber", "vip", "moderator"]


def test_render_preview_uses_runtime(custom):
    custom["custom_twice"] = {"kind": "math", "params": {"left": "{amount}", "op": "*", "right": "2"}}
    text = cv.render_preview("{username} gave {amount} ({custom_twice}) {followage} {followed_since:%Y}",
                             "event", "bits", {"bits_quantity": 300, "lookup": {"id": "follow_age"}})
    assert text == "TestViewer gave 300 (600) 2 years, 3 months 2024"
    assert cv.render_preview("{command_word_2} {city}", "command", item={
        "command_name": "w", "argument_mappings": {"city": 4}}) == "world four"

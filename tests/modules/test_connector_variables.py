# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_variables: catalogs, lookups, sample events, preview context, condition
descriptions and webhook helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types
from types import SimpleNamespace

import pytest

import modules
from modules import connector_variables as cv
from modules.connector_core import ComparisonOperator as Op, TriggerCondition, TriggerType
from modules.connector_triggers import create_trigger


def test_catalog_shapes():
    for value, variables in cv.EVENT_VARIABLES.items():
        for item in variables:
            assert {"key", "label", "example", "group"} <= set(item)
    assert {t["value"] for t in cv.TRIGGERS} >= {t.value for t in TriggerType}
    for ops in cv.OPERATORS.values():
        for op_value, label in ops:
            assert Op(op_value) and label
    assert cv.CONDITION_FIELDS["any"][0]["key"] == "event_type"


def test_variables_for_and_lookups():
    bits = cv.variables_for(TriggerType.TWITCH_BITS)
    assert bits[-len(cv.ALWAYS_VARIABLES):] == cv.ALWAYS_VARIABLES
    assert cv.variables_for("unknown") == cv.ALWAYS_VARIABLES
    assert cv.variables_for(None) == cv.ALWAYS_VARIABLES
    field = cv.CONDITION_FIELDS["twitch_bits"][0]
    assert cv.condition_field("twitch_bits", field["key"]) == field
    assert cv.condition_field(TriggerType.TWITCH_BITS, "nope") is None
    assert cv.condition_field("unknown", "amount") is None
    assert cv.trigger_info(TriggerType.TIMER)["value"] == "timer"
    assert cv.trigger_info("nope") is None


def test_trigger_value_helper():
    assert cv._trigger_value(None, "k", 1) == 1
    assert cv._trigger_value({"k": 2}, "k") == 2
    assert cv._trigger_value(SimpleNamespace(k=3), "k") == 3
    assert cv._trigger_value(SimpleNamespace(), "k", 4) == 4


def test_sample_event_any_and_defaults():
    data = cv.sample_event("any")
    assert data["event_type"] == "twitch_chat_message" and data["source"] and "timestamp" in data
    unknown = cv.sample_event("unknown_type")
    assert unknown["event_type"] == "unknown_type" and unknown["source"] == "test"


def test_sample_event_hotkey_variants():
    trig = create_trigger(TriggerType.HOTKEY, "h", "n", key_combination="ctrl+shift+f")
    data = cv.sample_event("hotkey", trig)
    assert data["combo"] == "ctrl+shift+f" and data["modifiers"] == ["ctrl", "shift"] and data["key_code"] == "f"
    data = cv.sample_event("hotkey", {"key_combination": "Alt+F4"})
    assert data["combo"] == "alt+f4"
    default = cv._SAMPLES.get("hotkey", {}).get("combo")
    no_combo = SimpleNamespace(combo=lambda: "", key_combination="")
    assert cv.sample_event("hotkey", no_combo).get("combo") == default
    assert cv.sample_event("hotkey").get("combo") == default


def test_sample_event_streamdeck_timer_schedule_webhook():
    assert cv.sample_event("streamdeck", {"connector_id": "c9"})["connector_id"] == "c9"
    assert cv.sample_event("streamdeck")["connector_id"] == ""
    timer = cv.sample_event("timer", {"trigger_id": "t1", "interval_seconds": "30.0"})
    assert timer["timer_id"] == "t1" and timer["interval_seconds"] == 30
    bad = cv.sample_event("timer", {"interval_seconds": "soon"})
    assert bad["timer_id"] == "test"
    blank = cv.sample_event("timer", {"interval_seconds": ""})
    assert blank["timer_id"] == "test"
    sched = cv.sample_event("schedule", {"trigger_id": "s1", "specific_time": "10:30", "days_of_week": (0, 2)})
    assert sched["trigger_id"] == "s1" and sched["specific_time"] == "10:30" and sched["days_of_week"] == [0, 2]
    plain = cv.sample_event("schedule", {})
    assert "trigger_id" not in plain or plain["trigger_id"] != "s1"
    hook = cv.sample_event("webhook")
    assert set(hook["payload"]) <= {"username", "message", "amount"}


def test_example_context(monkeypatch):
    trig = create_trigger(TriggerType.TWITCH_CHAT_MESSAGE, "t", "n",
                          conditions=[TriggerCondition(field="message", operator=Op.BEGINS_WITH, value="Hello")])
    trig.conditions.append("not a condition")
    ctx = cv.example_context(TriggerType.TWITCH_CHAT_MESSAGE, "", 2, trig)
    assert ctx["connector_name"] == "My connector" and ctx["trigger_count"] == 3
    assert ctx["trigger_type"] == "twitch_chat_message"
    assert not ctx["message_after_conditions"].startswith("Hello")
    ctx = cv.example_context("timer", "Named", None)
    assert ctx["connector_name"] == "Named" and ctx["trigger_count"] == 1


def test_condition_parts_and_describe():
    obj = TriggerCondition(field="amount", operator=Op.GREATER_THAN, value=5, join=None)
    assert cv._condition_parts(obj) == ("amount", "greater_than", 5, "and")
    assert cv._condition_parts({"field": "x", "operator": Op.EQUAL, "value": 1, "join": None}) == ("x", "equal", 1, "and")
    assert cv._condition_parts({}) == ("", "", None, "and")
    conds = [
        {"field": "amount", "operator": "greater_than", "value": 100},
        {"field": "", "operator": "equal", "value": 1},
        {"field": "is_mod", "operator": "equal", "value": True, "join": "and"},
        {"field": "message", "operator": "weird_op", "value": "", "join": "or"},
        {"field": "flag", "operator": "equal", "value": False, "join": "OR"},
        {"field": "none", "operator": "equal", "value": None},
    ]
    text = cv.describe_conditions(conds, {"amount": "Bits"})
    assert text == ('Runs when Bits more than (>) 100 and is_mod is true, or when message weird op "", '
                    'or when flag is false and none is ""')
    assert cv.describe_conditions([]) == "Runs every time"
    assert cv.describe_conditions(None) == "Runs every time"
    assert cv.describe_conditions([{"field": ""}]) == "Runs every time"
    assert cv.describe_conditions([{"field": "a", "operator": "equal", "value": 1, "join": "or"}]) == "Runs when a is 1"


def test_generate_webhook_secret():
    a, b = cv.generate_webhook_secret(), cv.generate_webhook_secret()
    assert a != b and len(a) >= 24 and all(c.isalnum() or c in "-_" for c in a)


def test_webhook_url(monkeypatch):
    mod = types.ModuleType("modules.web_engine")
    mod.web_engine_instance = SimpleNamespace(port=8123)
    monkeypatch.setitem(sys.modules, "modules.web_engine", mod)
    monkeypatch.setattr(modules, "web_engine", mod, raising=False)
    assert cv.webhook_url("a/b", "k y") == "http://127.0.0.1:8123/api/connectors/webhook/a%2Fb?key=k%20y"
    mod.web_engine_instance = SimpleNamespace(port=None)
    assert cv.webhook_url("c") == "http://127.0.0.1:5000/api/connectors/webhook/c"
    mod.web_engine_instance = SimpleNamespace(port="bad")
    assert cv.webhook_url("c").startswith("http://127.0.0.1:5000/")
    mod.web_engine_instance = None
    assert cv.webhook_url("c").startswith("http://127.0.0.1:5000/")

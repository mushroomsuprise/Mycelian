# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Connector conditions: AND/OR groups, comparisons, persistence and migrations."""

from __future__ import annotations

from modules.connector_actions import create_action
from modules.connector_core import (
    ActionType,
    ComparisonOperator as Op,
    Connector,
    TriggerCondition,
    TriggerType,
    evaluate_condition_groups,
    normalize_sub_tier,
)
from modules.connector_manager import ConnectorManager
from modules.connector_triggers import create_trigger


def C(field, op, value, join="and", case_sensitive=True):
    return TriggerCondition(field, op, value, case_sensitive=case_sensitive, join=join)


def test_and_binds_tighter_than_or():
    # (amount >= 100 and username == a) or message contains hi
    conds = [
        C("amount", Op.GREATER_THAN_OR_EQUAL, 100),
        C("username", Op.EQUAL, "a"),
        C("message", Op.CONTAINS, "hi", join="or"),
    ]
    assert evaluate_condition_groups(conds, {"amount": 100, "username": "a", "message": ""})
    assert not evaluate_condition_groups(conds, {"amount": 100, "username": "b", "message": ""})
    assert evaluate_condition_groups(conds, {"amount": 1, "username": "b", "message": "oh hi"})
    assert not evaluate_condition_groups(conds, {"amount": 1, "username": "a", "message": "x"})


def test_first_join_ignored_and_empty_is_true():
    assert evaluate_condition_groups([], {})
    conds = [C("a", Op.EQUAL, 1, join="or"), C("b", Op.EQUAL, 2)]
    assert evaluate_condition_groups(conds, {"a": 1, "b": 2})
    assert not evaluate_condition_groups(conds, {"a": 1, "b": 3})


def test_trigger_uses_or_groups():
    trigger = create_trigger(
        TriggerType.TWITCH_BITS,
        "t",
        "n",
        conditions=[C("amount", Op.EQUAL, 1), C("amount", Op.EQUAL, 5, join="or")],
    )
    assert trigger.should_trigger({"event_type": "twitch_bits", "amount": 5})
    assert not trigger.should_trigger({"event_type": "twitch_bits", "amount": 3})


def test_zero_and_one_are_numbers_not_bools():
    assert not C("amount", Op.EQUAL, "true").evaluate({"amount": 1})
    assert C("amount", Op.EQUAL, "1").evaluate({"amount": 1})
    assert not C("amount", Op.EQUAL, 1).evaluate({"amount": 0})
    assert C("amount", Op.NOT_EQUAL, "0").evaluate({"amount": 5})
    assert C("amount", Op.EQUAL, 100).evaluate({"amount": "100.0"})


def test_bool_fields_compare_as_bool():
    assert C("is_gift", Op.EQUAL, "true").evaluate({"is_gift": True})
    assert C("is_gift", Op.EQUAL, "false").evaluate({"is_gift": False})
    assert C("is_gift", Op.EQUAL, 1).evaluate({"is_gift": True})
    assert C("is_gift", Op.NOT_EQUAL, True).evaluate({"is_gift": False})
    # bool condition value against "true"/"false" text
    assert C("flag", Op.EQUAL, True).evaluate({"flag": "true"})
    assert not C("flag", Op.EQUAL, True).evaluate({"flag": "yes please"})


def test_case_insensitive_everywhere():
    ev = {"message": "Hello World", "username": "Bob"}
    assert C("username", Op.EQUAL, "bob", case_sensitive=False).evaluate(ev)
    assert not C("username", Op.EQUAL, "bob").evaluate(ev)
    assert C("message", Op.CONTAINS, "WORLD", case_sensitive=False).evaluate(ev)
    assert C("message", Op.BEGINS_WITH, "hell", case_sensitive=False).evaluate(ev)
    assert C("message", Op.ENDS_WITH, "LD", case_sensitive=False).evaluate(ev)
    assert C("message", Op.STARTS_WITH, "hello", case_sensitive=False).evaluate(ev)
    assert not C("message", Op.STARTS_WITH, "hell", case_sensitive=False).evaluate(ev)
    assert C("message", Op.REGEX_MATCH, "^hello", case_sensitive=False).evaluate(ev)
    assert not C("message", Op.REGEX_MATCH, "^hello").evaluate(ev)
    assert C("message", Op.NOT_CONTAINS, "xyz", case_sensitive=False).evaluate(ev)


def test_missing_field():
    assert C("nope", Op.NOT_EQUAL, "x").evaluate({})
    assert C("nope", Op.NOT_CONTAINS, "x").evaluate({})
    for op in (Op.EQUAL, Op.CONTAINS, Op.GREATER_THAN, Op.REGEX_MATCH, Op.BEGINS_WITH):
        assert not C("nope", op, "x").evaluate({})


def test_numeric_ops_need_numbers():
    assert C("amount", Op.GREATER_THAN, "50").evaluate({"amount": "100"})
    assert not C("amount", Op.GREATER_THAN, "fifty").evaluate({"amount": 100})
    assert not C("amount", Op.LESS_THAN, 5).evaluate({"amount": True})


def test_normalize_sub_tier():
    assert [normalize_sub_tier(x) for x in ("1000", 2000, "3000", "prime", 2, None, "x")] == [
        1, 2, 3, 1, 2, 1, 1,
    ]


def _connector(trigger_type=TriggerType.TWITCH_BITS, conditions=None, **trigger_kwargs):
    trigger = create_trigger(
        trigger_type,
        "trig-1",
        "Trigger",
        conditions=conditions or [],
        cooldown_seconds=15,
        metadata={"m": 1},
        **trigger_kwargs,
    )
    action = create_action(
        ActionType.SEND_CHAT_MESSAGE,
        "act-1",
        "Say",
        message="hi {username}",
        enabled=False,
        description="desc",
    )
    return Connector(
        connector_id="c1",
        name="C",
        trigger=trigger,
        actions=[action],
        created_at=123.0,
        metadata={"folder": "x"},
    )


def test_serialization_round_trip_keeps_everything():
    manager = ConnectorManager()
    conds = [
        C("amount", Op.GREATER_THAN, 5, case_sensitive=False),
        C("message", Op.CONTAINS, "hi", join="or"),
    ]
    data = manager.connector_to_dict(_connector(conditions=conds))
    assert data["trigger"]["conditions"][0]["join"] == "and"
    assert data["trigger"]["conditions"][1]["join"] == "or"
    back = manager.connector_from_dict(data)
    assert [c.join for c in back.trigger.conditions] == ["and", "or"]
    assert back.trigger.conditions[0].case_sensitive is False
    assert back.trigger.cooldown_seconds == 15
    assert back.trigger.metadata == {"m": 1}
    assert back.created_at == 123.0
    assert back.metadata == {"folder": "x"}
    action = back.actions[0]
    assert (action.action_id, action.enabled, action.description) == ("act-1", False, "desc")


def test_old_conditions_get_defaults_and_tier_migration():
    manager = ConnectorManager()
    data = manager.connector_to_dict(_connector(TriggerType.TWITCH_SUB))
    data["trigger"]["conditions"] = [
        {"field": "tier", "operator": "equal", "value": 2000},
        {"field": "tier", "operator": "equal", "value": "3000"},
        {"field": "username", "operator": "equal", "value": "Bob"},
    ]
    back = manager.connector_from_dict(data)
    conds = back.trigger.conditions
    assert [c.join for c in conds] == ["and", "and", "and"]
    assert all(c.case_sensitive is True for c in conds)
    assert conds[0].value == 2 and conds[1].value == "3"
    assert conds[2].value == "Bob"


def test_unknown_stored_trigger_keys_are_ignored():
    manager = ConnectorManager()
    data = manager.connector_to_dict(_connector())
    data["trigger"]["some_removed_field"] = 1
    assert manager.connector_from_dict(data).trigger is not None


def test_update_connector_keep_stats(monkeypatch):
    manager = ConnectorManager()
    monkeypatch.setattr(manager, "save_connectors", lambda: None)
    old = _connector()
    old.trigger_count = 7
    old.last_triggered = 50.0
    old.trigger.last_triggered = 49.0
    manager.connectors["c1"] = old
    new = _connector()
    new.created_at = 999.0
    assert manager.update_connector("c1", new, keep_stats=True)
    stored = manager.connectors["c1"]
    assert stored is new
    assert (stored.trigger_count, stored.last_triggered, stored.created_at) == (7, 50.0, 123.0)
    assert stored.trigger.last_triggered == 49.0
    # Without keep_stats the passed object is stored untouched.
    newer = _connector()
    newer.created_at = 5.0
    assert manager.update_connector("c1", newer)
    assert manager.connectors["c1"].created_at == 5.0

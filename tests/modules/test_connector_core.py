# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_core: action problem slots, log capture, labels, failure notices, regex cache,
condition comparison, AND/OR groups, message stripping, triggers, Connector.process_event,
sub tiers and the EventData builders."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import logging
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import modules
from modules import connector_core as core
from modules.connector_core import (
    ActionType,
    BaseAction,
    BaseTrigger,
    ComparisonOperator as Op,
    Connector,
    EventData,
    TriggerCondition,
    TriggerType,
)


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


@pytest.fixture
def notices(monkeypatch):
    seen = []
    fake_module(monkeypatch, "notification_engine", notify=lambda msg, **kw: seen.append((msg, kw)),
                nav_actions_main_tab=lambda tab: [("nav", tab)])
    return seen


# --------------------------------------------------------------------------- problem slot helpers


def test_report_action_problem_outside_action_is_noop():
    core.report_action_problem("x", "y", "z")
    assert core.current_action_connector() is None
    core.mark_action_skipped()


def test_report_action_problem_fills_slot(slot):
    core.report_action_problem("  reason ", " hint ", "details")
    assert slot["reason"] == "reason" and slot["hint"] == "hint" and slot["details"] == "details"
    core.report_action_problem(None)
    assert slot["reason"] == ""
    assert core.current_action_connector() == ("cid", "Conn")
    core.mark_action_skipped()
    assert slot["skipped"] is True


def test_current_action_connector_needs_connector_id():
    token = core._action_problem.set({"x": 1})
    try:
        assert core.current_action_connector() is None
    finally:
        core._action_problem.reset(token)


def test_log_capture_keeps_limited_lines(slot):
    handler = core._ActionLogCapture()
    for i in range(core._MAX_CAPTURED_LOG_LINES + 3):
        handler.emit(logging.LogRecord("x", logging.WARNING, "f", 1, "msg %s", (i,), None))
    assert len(slot["log"]) == core._MAX_CAPTURED_LOG_LINES
    assert slot["log"][0] == "WARNING: msg 0"


def test_log_capture_outside_action_and_errors():
    handler = core._ActionLogCapture()
    handler.emit(logging.LogRecord("x", logging.ERROR, "f", 1, "m", None, None))
    token = core._action_problem.set({})
    try:
        bad = logging.LogRecord("x", logging.ERROR, "f", 1, "%d", ("notint",), None)
        handler.emit(bad)  # getMessage raises; swallowed
    finally:
        core._action_problem.reset(token)


def test_action_label():
    assert core.action_label(SimpleNamespace(name=" Mine ")) == "Mine"
    assert core.action_label(SimpleNamespace(name="", action_type=ActionType.API_CALL)) == "API call"
    assert core.action_label(SimpleNamespace(name=None, action_type=ActionType.WRITE_FILE)) == "Write file"
    assert core.action_label(SimpleNamespace()) == "Action"


def test_notify_action_failure(notices):
    conn = SimpleNamespace(name="C", connector_id="id1")
    core._notify_action_failure(conn, {"label": "L", "index": 2, "reason": "it broke", "hint": "", "details": ""})
    msg, kw = notices[0]
    assert msg == "Action 2 (L) didn't work: it broke."
    assert kw["dedupe_key"] == "connector:id1:2" and kw["details"] is None
    assert "Test" in kw["hint"] and kw["title"] == "Connector “C” had a problem"
    core._notify_action_failure(conn, {"label": "L", "index": 1, "hint": "H", "details": "D"})
    msg, kw = notices[1]
    assert msg == "Action 1 (L) didn't work." and kw["hint"] == "H" and kw["details"] == "D"


def test_notify_action_failure_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    fake_module(monkeypatch, "notification_engine", notify=boom, nav_actions_main_tab=lambda t: [])
    core._notify_action_failure(SimpleNamespace(name="C", connector_id="i"), {"label": "L", "index": 1})


# --------------------------------------------------------------------------- regex cache


def test_compiled_regex_cache(monkeypatch):
    monkeypatch.setattr(core, "_regex_match_cache", {})
    first = core._compiled_regex_match("a+")
    assert first is core._compiled_regex_match("a+")
    assert core._compiled_regex_match("a+", ignore_case=True).flags & 2
    assert core._compiled_regex_match("(") is None
    assert core._compiled_regex_match("(") is None  # cached invalid
    monkeypatch.setattr(core, "_REGEX_CACHE_MAX", 3)
    core._compiled_regex_match("z")
    assert list(core._regex_match_cache) == ["s:z"]


# --------------------------------------------------------------------------- conditions


def cond(field, op, value, case=True, join="and"):
    return TriggerCondition(field=field, operator=op, value=value, case_sensitive=case, join=join)


@pytest.mark.parametrize(
    "op,field_value,value,case,expected",
    [
        (Op.EQUAL, None, "x", True, False),
        (Op.NOT_EQUAL, None, "x", True, True),
        (Op.NOT_CONTAINS, None, "x", True, True),
        (Op.EQUAL, 5, "5.0", True, True),
        (Op.NOT_EQUAL, 5, "6", True, True),
        (Op.EQUAL, "Bob", "bob", False, True),
        (Op.EQUAL, "Bob", "bob", True, False),
        (Op.EQUAL, True, "yes", True, True),
        (Op.EQUAL, True, 0, True, False),
        (Op.EQUAL, True, 5, True, False),
        (Op.EQUAL, True, "maybe", True, False),
        (Op.EQUAL, "true", True, True, True),
        (Op.EQUAL, "FALSE", True, True, False),
        (Op.EQUAL, "other", True, True, False),
        (Op.GREATER_THAN, "10", 5, True, True),
        (Op.GREATER_THAN_OR_EQUAL, 5, 5, True, True),
        (Op.LESS_THAN, 4, "5", True, True),
        (Op.LESS_THAN_OR_EQUAL, 6, 5, True, False),
        (Op.GREATER_THAN, "abc", 5, True, False),
        (Op.GREATER_THAN, 5, "nan", True, False),
        (Op.REGEX_MATCH, "Hello World", "world$", False, True),
        (Op.REGEX_MATCH, "Hello World", "world$", True, False),
        (Op.REGEX_MATCH, "x", "(", True, False),
        (Op.CONTAINS, "Hello", "ELL", False, True),
        (Op.NOT_CONTAINS, "Hello", "xyz", True, True),
        (Op.STARTS_WITH, "!cmd arg", "!cmd", True, True),
        (Op.STARTS_WITH, "!cmdx arg", "!cmd", True, False),
        (Op.STARTS_WITH, "   ", "x", True, False),
        (Op.BEGINS_WITH, "!cmdx", "!cmd", True, True),
        (Op.ENDS_WITH, "file.TXT", ".txt", False, True),
        (Op.EQUAL, 2.0, "2", True, True),
        (Op.EQUAL, [1], "[1]", True, True),
    ],
)
def test_condition_compare(op, field_value, value, case, expected):
    assert cond("f", op, value, case).evaluate({"f": field_value}) is expected


def test_condition_unknown_operator_and_errors():
    c = cond("f", Op.EQUAL, "x")
    assert c._compare_values("x", "x", SimpleNamespace(value="weird")) is False

    class Bad:
        def __str__(self):
            raise RuntimeError("no str")

    assert cond("f", Op.CONTAINS, "x").evaluate({"f": Bad()}) is False


def test_condition_nested_field():
    c = cond("user.name", Op.EQUAL, "Bob")
    assert c.evaluate({"user": {"name": "Bob"}})
    assert not c.evaluate({"user": "Bob"})


def test_as_number_and_text_helpers():
    assert core._as_number(True) is None and core._as_number(None) is None
    assert core._as_number(" ") is None and core._as_number("inf") is None
    assert core._as_number([1]) is None and core._as_number("3") == 3.0
    assert core._as_bool(1) is True and core._as_bool("off") is False and core._as_bool(2) is None
    assert core._as_text(None) == "" and core._as_text(False) == "false" and core._as_text(1.5) == "1.5"
    assert core._as_text(2.0) == "2" and core._as_bool(False) is False
    assert cond("f", Op.CONTAINS, "2").evaluate({"f": 12.0})


def test_evaluate_condition_groups():
    a = cond("a", Op.EQUAL, 1)
    b = cond("b", Op.EQUAL, 1)
    c = cond("c", Op.EQUAL, 1, join="or")
    assert core.evaluate_condition_groups([], {}) is True
    assert core.evaluate_condition_groups([a, b, c], {"c": 1})
    assert core.evaluate_condition_groups([a, b, c], {"a": 1, "b": 1})
    assert not core.evaluate_condition_groups([a, b, c], {"a": 1})
    first_or = cond("a", Op.EQUAL, 1, join="or")
    assert not core.evaluate_condition_groups([first_or, b], {"a": 1})
    none_join = cond("b", Op.EQUAL, 1, join=None)
    assert core.evaluate_condition_groups([a, none_join], {"a": 1, "b": 1})


def test_message_after_trigger_conditions():
    f = core.message_after_trigger_conditions
    assert f(None, []) == ""
    assert f("!hi there", [cond("message", Op.STARTS_WITH, "!hi")]) == "there"
    assert f("!HI there", [cond("message", Op.STARTS_WITH, "!hi", case=False)]) == "there"
    assert f("!hi", [cond("message", Op.STARTS_WITH, "!hi")]) == ""
    assert f("!hix there", [cond("message", Op.STARTS_WITH, "!hi")]) == "!hix there"
    assert f("", [cond("message", Op.STARTS_WITH, "!hi")]) == ""
    assert f("say WORD now", [cond("message", Op.CONTAINS, "WORD")]) == "say  now"
    assert f("say word now", [cond("message", Op.CONTAINS, "WORD", case=False)]) == "say  now"
    assert f("say now", [cond("message", Op.CONTAINS, "zzz", case=False)]) == "say now"
    assert f("say now", [cond("message", Op.CONTAINS, "zzz")]) == "say now"
    assert f("!cmdrest", [cond("message", Op.BEGINS_WITH, "!CMD", case=False)]) == "rest"
    assert f("text END", [cond("message", Op.ENDS_WITH, "END")]) == "text"
    assert f("text end", [cond("message", Op.ENDS_WITH, "END")]) == "text end"
    skipped = [cond("user", Op.CONTAINS, "x"), cond("message", Op.EQUAL, "x"),
               cond("message", Op.CONTAINS, None), cond("message", Op.CONTAINS, "")]
    assert f(" keep ", skipped) == " keep "


# --------------------------------------------------------------------------- triggers and actions


@dataclass
class T(BaseTrigger):
    want: bool = True

    def should_trigger(self, event_data):
        return self.want and self.evaluate_conditions(event_data)


def trig(**kw):
    kw.setdefault("trigger_id", "tid")
    kw.setdefault("trigger_type", TriggerType.ANY)
    kw.setdefault("name", "T")
    return T(**kw)


def test_base_trigger_cooldown_and_disabled(monkeypatch):
    t = trig(cooldown_seconds=10)
    monkeypatch.setattr(core.time, "time", lambda: 100.0)
    assert t.trigger({}) and t.last_triggered == 100.0
    assert not t.can_trigger()
    monkeypatch.setattr(core.time, "time", lambda: 111.0)
    assert t.can_trigger()
    t.enabled = False
    assert not t.trigger({})
    t.enabled, t.want = True, False
    assert not t.trigger({})


def test_base_action_default_validate():
    @dataclass
    class A(BaseAction):
        async def execute(self, trigger_data, event_data):
            return await BaseAction.execute(self, trigger_data, event_data)

    a = A(action_id="a", action_type=ActionType.API_CALL, name="n")
    assert a.validate_parameters() is True
    assert run(a.execute({}, {})) is None
    assert BaseTrigger.should_trigger(trig(), {}) is None


@dataclass
class Act(BaseAction):
    result: object = True
    seen: list = None
    problem: tuple = None

    async def execute(self, trigger_data, event_data):
        if self.seen is not None:
            self.seen.append(trigger_data)
        if self.problem:
            core.report_action_problem(*self.problem)
        if isinstance(self.result, Exception):
            raise self.result
        if self.result == "skip":
            core.mark_action_skipped()
            return False
        if self.result == "log":
            logging.getLogger("modules.test").warning("captured warning")
            return False
        return self.result


def act(i, **kw):
    kw.setdefault("action_type", ActionType.API_CALL)
    return Act(action_id=f"a{i}", name=kw.pop("name", f"A{i}"), **kw)


def test_connector_process_event_runs_enabled_actions(monkeypatch, notices):
    seen = []
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(core.asyncio, "sleep", fake_sleep)
    actions = [act(1, seen=seen, delay_seconds=0.5), act(2, enabled=False, seen=seen), act(3, seen=seen)]
    c = Connector(connector_id="c1", name="Conn", trigger=trig(conditions=[cond("message", Op.STARTS_WITH, "!go")]),
                  actions=actions)
    assert run(c.process_event({"message": "!go now"}))
    assert c.trigger_count == 1 and sleeps == [0.5]
    assert [d["action_index"] for d in seen] == [1, 3]
    assert seen[0]["message_after_conditions"] == "now" and seen[0]["trigger_type"] == "any"
    assert seen[0]["action_results"] is seen[1]["action_results"]
    assert c.last_action_failures == [] and notices == []


def test_connector_process_event_reports_failures(monkeypatch, notices):
    actions = [
        act(1, result=False, problem=("bad thing", "fix it", "tech")),
        act(2, result=RuntimeError("kaput")),
        act(3, result="skip"),
        act(4, result="log", name=""),
    ]
    c = Connector(connector_id="c1", name="Conn", trigger=trig(), actions=actions)
    assert run(c.process_event({})) is False
    fails = c.last_action_failures
    assert [f["index"] for f in fails] == [1, 2, 4]
    assert fails[0] == {"index": 1, "label": "A1", "reason": "bad thing", "hint": "fix it", "details": "tech"}
    assert fails[1]["reason"] == "it stopped with an error" and "RuntimeError: kaput" in fails[1]["details"]
    assert "captured warning" in fails[2]["details"] and fails[2]["label"] == "API call"
    assert len(notices) == 3
    notices.clear()
    assert run(c.process_event({}, report_failures=False)) is False
    assert notices == [] and len(c.last_action_failures) == 3


def test_connector_process_event_short_circuits(monkeypatch):
    assert not run(Connector(connector_id="c", name="n", enabled=False, trigger=trig()).process_event({}))
    assert not run(Connector(connector_id="c", name="n").process_event({}))
    c = Connector(connector_id="c", name="n", trigger=trig(want=False), actions=[act(1)])
    assert not run(c.process_event({})) and c.trigger_count == 0


def test_game_hook_prerequisites(monkeypatch):
    ready = {"ff7": True, "ff8": False}
    fake_module(monkeypatch, "game_hooks_service",
                game_hooks_service=SimpleNamespace(is_game_hook_ready=lambda g: ready.get(g, False)))
    hook = SimpleNamespace(enabled=True, action_type=ActionType.GAME_HOOK, game_id=" ")
    off_hook = SimpleNamespace(enabled=False, action_type=ActionType.GAME_HOOK, game_id="ff8")
    other = SimpleNamespace(enabled=True, action_type=ActionType.API_CALL)
    c = Connector(connector_id="c", name="n", trigger=trig(), actions=[hook, off_hook, other])
    assert c._game_hook_prerequisites_unmet() is False
    assert Connector(connector_id="c", name="n", actions=[other])._game_hook_prerequisites_unmet() is False
    hook.game_id = "ff8"
    assert c._game_hook_prerequisites_unmet() is True
    assert run(c.process_event({})) is False and c.trigger_count == 0


# --------------------------------------------------------------------------- tiers and EventData


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 1), (True, 1), ("prime", 1), ("2000", 2), (3000, 3), ("2", 2), (3.0, 3), ("x", 1), (7, 1),
     (SimpleNamespace(value="3000"), 3)],
)
def test_normalize_sub_tier(raw, expected):
    assert core.normalize_sub_tier(raw) == expected


def test_event_data_builders(monkeypatch):
    monkeypatch.setattr(core.time, "time", lambda: 5.0)
    assert EventData.from_twitch_bits(100, "u", "m", extra=1) == {
        "event_type": "twitch_bits", "amount": 100, "username": "u", "message": "m", "timestamp": 5.0,
        "source": "twitch", "extra": 1}
    assert EventData.from_twitch_sub("2000", "u")["tier"] == 2
    resub = EventData.from_twitch_resub("prime", "u", None, months=4, streak_months=None)
    assert resub["message"] == "" and resub["streak_months"] == 0 and resub["tier"] == 1
    gift = EventData.from_twitch_giftsub(3000, "g", gift_count=5, is_anonymous=1)
    assert gift["tier"] == 3 and gift["is_anonymous"] is True and gift["gift_count"] == 5
    assert EventData.from_twitch_follow("u")["event_type"] == "twitch_follow"
    assert EventData.from_twitch_raid("u", 9)["viewer_count"] == 9
    chat = EventData.from_twitch_chat("u", "!hi", is_command=True, command="hi")
    assert chat["is_command"] and chat["command"] == "hi"
    don = EventData.from_donation(5.0, "u", currency="EUR")
    assert don["source"] == "donation" and don["currency"] == "EUR"
    assert EventData.from_donation(1, "u", source="kofi")["source"] == "kofi"
    assert EventData.from_youtube_chat("u", "m", "id")["user_id"] == "id"
    assert EventData.from_youtube_member("u", "Gold")["member_level"] == "Gold"
    ms = EventData.from_youtube_member_milestone("u", months=6)
    assert ms["months"] == 6 and ms["event_type"] == "youtube_member_milestone"
    gm = EventData.from_youtube_gift_membership("u", gift_count=3)
    assert gm["quantity"] == 3 and gm["gift_count"] == 3
    sc = EventData.from_youtube_superchat("u", 2.5, display_amount="$2.50")
    assert sc["event_type"] == "youtube_superchat" and sc["display_amount"] == "$2.50"
    st = EventData.from_youtube_superchat("u", 1, event_type="youtube_supersticker")
    assert st["event_type"] == "youtube_supersticker" and st["source"] == "youtube"

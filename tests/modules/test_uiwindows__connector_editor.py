# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""uiwindows.connector_editor (part 1): helpers, EditorState load / validate / build, the test
runner, the log capture, FF7 variables and template element fields (no UI)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import logging
from types import SimpleNamespace

import pytest

from modules import connector_manager as cm
from modules import connector_variables as cv
from modules.connector_actions import create_action
from modules.connector_core import ActionType as A
from modules.connector_core import ComparisonOperator as O
from modules.connector_core import Connector, TriggerCondition
from modules.connector_core import TriggerType as T
from modules.connector_triggers import create_trigger
from modules.uiwindows import connector_action_schemas as schemas
from modules.uiwindows import connector_editor as ce


@pytest.fixture(autouse=True)
def _no_device_lookups(monkeypatch):
    for spec in schemas.ACTION_SPECS.values():
        for f in spec.fields:
            if f.key == "target_device":
                monkeypatch.setattr(f, "options", lambda cfg: {"": "Default device", "spk": "Speakers"})
            elif f.key == "target_application":
                monkeypatch.setattr(f, "options", lambda cfg: {"obs": "OBS"})


@pytest.fixture
def notes(monkeypatch):
    seen = []
    monkeypatch.setattr(ce, "notify", lambda msg="", **kw: seen.append((msg, kw)))
    return seen


# --------------------------------------------------------------------------- helpers


def test_glyphs_and_trigger_info(monkeypatch):
    assert 'ce-brand is-twitch' in ce._glyph("twitch")
    assert ce._glyph("timer") == ce._ic("timer")
    assert ce._trigger_brand("youtube_member") == "youtube" and ce._trigger_brand(None) == ""
    assert ce._trigger_glyph("timer") == ce._ic("timer") and ce._trigger_glyph(None) == ce._ic("bolt")
    assert "is-obs" in ce._trigger_glyph("obs_scene_changed")
    assert ce._trigger_info("timer")["value"] == "timer"
    assert ce._trigger_info("made_up") == {"value": "made_up", "label": "Made Up", "group": "Other", "hint": ""}
    assert ce._trigger_info(None)["label"] == ""
    assert ce._condition_fields("twitch_bits") and ce._condition_fields(None) == []
    assert ce._operators("number")[0] == ("equal", "is")
    assert ce._operators("unknown") == ce._operators("text")
    monkeypatch.setattr(cv, "OPERATORS", {})
    assert ce._operators("text") == []


def test_as_float():
    assert ce._as_float("2.5") == 2.5 and ce._as_float("x", "3") == 3.0 and ce._as_float(None, "y") == 0.0


def _key_event(name, ctrl=False, alt=False, shift=False, meta=False, keyup=False, repeat=False):
    return SimpleNamespace(action=SimpleNamespace(keyup=keyup, repeat=repeat), key=SimpleNamespace(name=name),
                           modifiers=SimpleNamespace(ctrl=ctrl, alt=alt, shift=shift, meta=meta))


def test_hotkey_combo_from_event():
    assert ce._hotkey_combo_from_event(_key_event("F", ctrl=True, shift=True)) == "ctrl+shift+f"
    assert ce._hotkey_combo_from_event(_key_event("k", alt=True, meta=True)) == "alt+cmd+k"
    assert ce._hotkey_combo_from_event(_key_event("a", keyup=True)) == ""
    assert ce._hotkey_combo_from_event(_key_event("a", repeat=True)) == ""
    assert ce._hotkey_combo_from_event(_key_event("Shift", shift=True)) == ""
    assert ce._hotkey_combo_from_event(_key_event("")) == ""
    assert ce._hotkey_combo_from_event(SimpleNamespace()) == ""


def test_normalize_hotkey_fallback(monkeypatch):
    assert ce.normalize_hotkey("Shift + Ctrl + F") == "ctrl+shift+f"
    import modules.hotkey_listener as hl

    def boom(c):
        raise RuntimeError("x")

    monkeypatch.setattr(hl, "normalize_hotkey_combo", boom)
    assert ce.normalize_hotkey(" Ctrl + A ") == "ctrl+a" and ce.normalize_hotkey(None) == ""


def test_secret_and_webhook_url_fallbacks(monkeypatch):
    assert len(ce._new_secret()) >= 24
    assert "/api/connectors/webhook/c1?key=k" in ce._webhook_url("c1", "k")

    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(cv, "generate_webhook_secret", boom)
    monkeypatch.setattr(cv, "webhook_url", boom)
    assert len(ce._new_secret()) >= 24
    assert ce._webhook_url("c1", "k") == "http://127.0.0.1:5000/api/connectors/webhook/c1?key=k"


# --------------------------------------------------------------------------- state


def test_snapshot_ignores_open_flag():
    s = ce.EditorState(connector_id="c", is_new=True, actions=[{"type": "x", "open": True}])
    snap = s.snapshot()
    s.actions[0]["open"] = False
    assert s.snapshot() == snap


def test_new_action():
    a = ce._new_action("send_chat_message")
    assert a["config"]["reply_targets"] == ["twitch"] and a["open"] and a["enabled"]
    assert ce._new_action("nope")["config"] == {}


def test_state_from_blank_connector(monkeypatch):
    s = ce.state_from_connector(None)
    assert s.is_new and len(s.connector_id) == 36
    assert ce.state_from_connector(None, "fixed").connector_id == "fixed"


@pytest.mark.parametrize(
    "ttype,kw,expected",
    [
        (T.HOTKEY, {"key_combination": "Shift+Ctrl+K"}, {"key_combination": "ctrl+shift+k"}),
        (T.TIMER, {"interval_seconds": 90}, {"interval_seconds": 90}),
        (T.SCHEDULE, {"specific_time": "10:30", "days_of_week": [1, "3"]}, {"specific_time": "10:30", "days_of_week": [1, 3]}),
        (T.WEBHOOK, {"secret_key": "k"}, {"secret_key": "k"}),
        (T.TWITCH_BITS, {}, {}),
    ],
)
def test_state_from_connector_trigger_settings(ttype, kw, expected):
    trig = create_trigger(ttype, "t", "T", cooldown_seconds=5, **kw)
    conn = Connector(connector_id="c", name="N", trigger=trig, actions=[])
    s = ce.state_from_connector(conn)
    assert s.trigger == {"cooldown_seconds": 5.0, **expected} and not s.is_new and s.actions == []


def test_state_from_connector_conditions_and_actions():
    conds = [TriggerCondition("amount", O.GREATER_THAN, None), TriggerCondition("is_mod", O.EQUAL, True, join="OR")]
    conds[0].case_sensitive = False
    trig = create_trigger(T.TWITCH_BITS, "t", "T", conditions=conds)
    a1 = create_action(A.SEND_CHAT_MESSAGE, "a1", "n", message="m", delay_seconds=2, enabled=False)
    a2 = create_action(A.OBS_CONTROL, "a2", "n", operation="start_stream")
    conn = Connector(connector_id="c", name=None, description=None, trigger=trig, actions=[a1, a2])
    s = ce.state_from_connector(conn)
    assert s.conditions[0] == {"field": "amount", "operator": "greater_than", "value": "", "join": "and", "ignore_case": True}
    assert s.conditions[1]["value"] is True and s.conditions[1]["join"] == "or"
    assert s.actions[0]["delay_seconds"] == 2.0 and s.actions[0]["enabled"] is False and not s.actions[0]["open"]
    assert s.actions[1]["config"] == {"operation": "start_stream"} and s.name == "" and s.description == ""


def test_state_from_connector_unreadable_action(monkeypatch):
    def boom(action):
        raise ValueError("bad stored data")

    monkeypatch.setattr(schemas, "action_to_config", boom)
    a1 = create_action(A.WRITE_FILE, "a1", "n", file_path="x")
    conn = Connector(connector_id="c", name="N", trigger=None, actions=[a1])
    s = ce.state_from_connector(conn)
    assert s.actions[0]["unreadable"] and s.actions[0]["open"] and s.trigger_type is None
    assert s.actions[0]["loaded_config"] == s.actions[0]["config"]


def test_coerce_condition_value_and_field_kind():
    c = ce._coerce_condition_value
    assert c("bool", True) is True and c("bool", "Yes") is True and c("bool", "no") is False
    assert c("number", " 5 ") == 5 and c("number", "2.5") == 2.5 and c("number", "x") == "x"
    assert c("number", None) is None and c("text", None) == "" and c("text", 5) == "5"
    assert ce._field_kind("twitch_bits", "amount") == "number"
    assert ce._field_kind("twitch_bits", "nope") == "text"


def _state(**kw):
    kw.setdefault("connector_id", "c1")
    kw.setdefault("is_new", True)
    kw.setdefault("name", "N")
    kw.setdefault("trigger_type", "twitch_bits")
    kw.setdefault("actions", [ce._new_action("send_chat_message")])
    s = ce.EditorState(**kw)
    for a in s.actions:
        if a.get("type") == "send_chat_message":
            a["config"]["message"] = a["config"].get("message") or "hi"
    return s


def test_state_problems_trigger_settings():
    p = ce.state_problems
    assert p(_state(name=" ", trigger_type=None, actions=[])) == [
        "Name this connector.", "Choose a trigger.", "Add at least one action."]
    assert p(_state(trigger_type="hotkey", trigger={})) == ["Set the hotkey."]
    assert p(_state(trigger_type="schedule", trigger={"specific_time": "25:00"})) == ["Set the time the schedule runs (HH:MM)."]
    assert p(_state(trigger_type="schedule", trigger={"specific_time": "09:05"})) == []
    assert p(_state(trigger_type="timer", trigger={"interval_seconds": 0})) == ["Set how often the timer runs."]


def test_state_problems_conditions_and_json():
    conds = [{"field": ""}, {"field": "amount", "value": "abc"}, {"field": "message", "operator": "contains", "value": ""},
             {"field": "message", "operator": "equal", "value": ""}, {"field": "amount", "value": "5"}]
    assert ce.state_problems(_state(conditions=conds)) == [
        "Condition 1: choose a field.", "Condition 2: enter a number.", "Condition 3: enter a value."]
    bad = ce._new_action("websocket_emit")
    bad["config"].update(event_name="ev", event_data="{nope")
    probs = ce.state_problems(_state(actions=[bad]), {})
    assert probs[0].startswith("Action 1: Event data (JSON): Not valid JSON")
    empty = ce._new_action("send_chat_message")
    empty["config"]["message"] = " "
    unknown = {"type": "nope", "config": None}
    assert ce.state_problems(_state(actions=[empty, unknown])) == [
        "Action 1: Message is empty.", "Action 2: Choose what this action does."]


def test_build_connector_new_with_every_trigger_setting():
    s = _state(trigger_type="schedule", trigger={"specific_time": "10:00", "days_of_week": [3, 1, 3], "cooldown_seconds": -2},
               conditions=[{"field": "amount", "operator": "greater_than", "value": "100", "ignore_case": True}])
    conn = ce.build_connector(s)
    assert conn.trigger.days_of_week == [1, 3] and conn.trigger.cooldown_seconds == 0.0
    c = conn.trigger.conditions[0]
    assert c.value == "100" and c.case_sensitive is False and c.join == "and"  # no amount field on schedules
    bits = ce.build_connector(_state(conditions=[{"field": "amount", "operator": None, "value": "100", "join": "or"}]))
    assert bits.trigger.conditions[0].value == 100 and bits.trigger.conditions[0].operator == O.EQUAL
    assert conn.actions[0].name == "N Action 1" and conn.actions[0].message == "hi" and conn.created_at > 0
    for tt, tcfg, attr, value in [
        ("hotkey", {"key_combination": "Ctrl+B"}, "key_combination", "ctrl+b"),
        ("timer", {}, "interval_seconds", 60),
        ("webhook", {"secret_key": "s"}, "secret_key", "s"),
        ("streamdeck", {}, "connector_id", "c1"),
    ]:
        assert getattr(ce.build_connector(_state(trigger_type=tt, trigger=tcfg)).trigger, attr) == value


def test_build_connector_keeps_existing_fields():
    trig = create_trigger(T.TWITCH_BITS, "old-trig", "T", metadata={"m": 1})
    stored = create_action(A.WRITE_FILE, "keep-me", "n", file_path="x", content="secret")
    existing = Connector(connector_id="c1", name="Old", trigger=trig, actions=[stored], created_at=5.0,
                         last_triggered=6.0, trigger_count=7, metadata={"k": [1]})
    entry = {"type": "write_file", "config": {"file_path": "default"}, "action_id": "keep-me", "unreadable": True,
             "loaded_config": {"file_path": "default"}, "delay_seconds": 3, "enabled": False, "description": "d"}
    s = _state(actions=[entry])
    conn = ce.build_connector(s, existing)
    assert conn.trigger.trigger_id == "old-trig" and conn.trigger.metadata == {"m": 1}
    assert conn.actions[0].content == "secret" and conn.actions[0].delay_seconds == 3.0
    assert conn.actions[0].enabled is False and conn.actions[0].description == "d"
    assert (conn.created_at, conn.last_triggered, conn.trigger_count) == (5.0, 6.0, 7)
    assert conn.metadata == {"k": [1]} and conn.metadata is not existing.metadata
    # changed config: rebuilt from the form
    entry["config"] = {"file_path": "new.txt"}
    conn = ce.build_connector(s, existing)
    assert conn.actions[0].file_path == "new.txt" and conn.actions[0].content == ""
    # other trigger type: no metadata copy, fresh trigger id kept
    s.trigger_type = "twitch_sub"
    assert ce.build_connector(s, existing).trigger.metadata == {}


# --------------------------------------------------------------------------- run_connector_test


class FakeMgr:
    def __init__(self, connector=None, result=None):
        self.connector = connector
        self.result = result or {}
        self.calls = []

    def get_connector(self, cid):
        return self.connector

    async def test_connector(self, cid, data):
        self.calls.append((cid, data))
        return self.result


def _runner(monkeypatch, mgr):
    monkeypatch.setattr(cm, "get_manager", lambda: mgr)
    monkeypatch.setattr(ce.background_tasks, "create", lambda coro, name=None: asyncio.run(coro))


def _live(**kw):
    return Connector(connector_id="c", name="N", trigger=create_trigger(T.TWITCH_BITS, "t", "T"), actions=[], **kw)


def test_run_connector_test_not_ready(monkeypatch, notes):
    _runner(monkeypatch, FakeMgr(None))
    ce.run_connector_test("c")
    assert notes[-1][0] == "Save the connector first, then test it."
    _runner(monkeypatch, FakeMgr(Connector(connector_id="c", name="N")))
    ce.run_connector_test("c")
    assert notes[-1][0].startswith("Choose a trigger")
    _runner(monkeypatch, FakeMgr(_live(enabled=False)))
    ce.run_connector_test("c")
    assert notes[-1][0] == "Turn the connector on to test it."

    def boom(*a, **k):
        raise RuntimeError("sample")

    monkeypatch.setattr(cv, "sample_event", boom)
    _runner(monkeypatch, FakeMgr(_live()))
    ce.run_connector_test("c")
    assert notes[-1][1]["title"] == "Couldn't build a sample event for this trigger"


@pytest.mark.parametrize(
    "result,check",
    [
        ({"success": False, "error": "loop down"}, lambda n: n[1]["title"] == "The connector test couldn't run" and n[1]["details"] == "loop down"),
        ({"success": False}, lambda n: n[1]["details"] is None),
        ({"success": True, "triggered": True, "action_count": 1}, lambda n: n[0] == "Test ran 1 action with sample data"),
        ({"success": True, "triggered": True, "action_count": 3}, lambda n: n[0] == "Test ran 3 actions with sample data"),
        ({"success": True, "triggered": False}, lambda n: n[1]["title"] == "Test didn't run"),
        ({"success": True, "failures": [{"index": 1, "label": "Say", "reason": "no chat", "hint": "Sign in", "details": "D"},
                                        {"index": 2, "label": "Beep"}]},
         lambda n: n[0] == "Action 1 (Say): no chat; Action 2 (Beep)." and n[1]["hint"] == "Sign in"
         and n[1]["title"] == "Test ran, but 2 actions didn't work" and "no details" in n[1]["details"]),
        ({"success": True, "failures": [{"index": 1, "label": "Say"}]},
         lambda n: n[1]["title"] == "Test ran, but 1 action didn't work" and n[1]["hint"].startswith("Check the settings")),
    ],
)
def test_run_connector_test_results(monkeypatch, notes, result, check):
    mgr = FakeMgr(_live(), result)
    _runner(monkeypatch, mgr)
    ce.run_connector_test("c")
    assert mgr.calls[0][1]["event_type"] == "twitch_bits"
    assert check(notes[-1])


# --------------------------------------------------------------------------- misc


def test_log_capture():
    log = logging.getLogger("modules.test_capture")
    with ce._LogCapture("modules.test_capture") as cap:
        for i in range(10):
            log.warning("w%d", i)
        log.info("ignored")
    log.warning("after")
    assert cap.lines[0] == "WARNING: w0" and len(cap.lines) == 8
    assert cap not in log.handlers


def test_ff7_variables():
    assert ce._ff7_variables(None) == []
    keys = [v["key"] for v in ce._ff7_variables(["character", "enemy", "hooks_gil", "hooks_battle", "hooks_field",
                                                 "damage", "gear"])]
    assert "hooks.ff7.party.2.name" in keys and "random_character" in keys and "hooks.ff7.enemies.5.name" in keys
    assert "random_enemy" in keys and "hooks.ff7.gil" in keys and "hooks.ff7.battle" in keys
    assert "hooks.ff7.field_name" in keys and "random_damage.1.9999" in keys and "random_accessory" in keys
    only = [v["key"] for v in ce._ff7_variables(("hooks_enemy",))]
    assert "random_enemy" not in only and "message_after_conditions" in only
    assert all(v["group"] == "Game" for v in ce._ff7_variables(("hooks_party",)))


def test_template_element_field():
    f = ce.ConnectorEditor._template_element_field
    assert f({"type": "text"}) is None and f({"id": "x", "type": "separator"}) is None
    t = f({"id": "msg", "type": "text", "label": "Msg", "value": None, "placeholder": "p", "description": "d"})
    assert (t.kind, t.default, t.variables, t.placeholder, t.help) == ("text", "", True, "p", "d")
    n = f({"id": "n", "type": "number", "min": 1, "max": 5, "value": 3})
    assert (n.kind, n.default, n.min, n.max, n.label) == ("number", 3, 1, 5, "n")
    s = f({"id": "s", "type": "select", "options": ["slow_spin", "fast"], "value": "fast"})
    assert s.options == {"slow_spin": "Slow Spin", "fast": "Fast"} and s.default == "fast"
    s2 = f({"id": "s", "type": "select", "options": {"a": "A"}, "value": None})
    assert s2.options == {"a": "A"} and s2.default == ""
    sw = f({"id": "on", "type": "switch", "value": 1})
    assert sw.kind == "toggle" and sw.default is True

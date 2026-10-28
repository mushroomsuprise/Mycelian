# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_editor.py: catalogs, state, validation, build_item and test runs.

The dialog UI is tested in ``_part2`` (commands/events) and ``_part3`` (lookups, outputs, custom variables).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

from _chatbot_editor_fakes import cmd, ed_env, evt  # noqa: E402,F401  (ed_env: fixture)
from modules import chatbot_lookups as lk  # noqa: E402
from modules import chatbot_variables as cv  # noqa: E402
from modules.chatbot_core import ChatCommand, EventType  # noqa: E402
from modules.uiwindows import chatbot_editor as ce  # noqa: E402


# --------------------------------------------------------------------------- helpers / catalogs


def test_glyph_uses_brand_svg_or_icon():
    assert ce._glyph("twitch").startswith('<span class="ce-brand is-twitch">')
    assert "timer" in ce._glyph("timer") and "ce-brand" not in ce._glyph("timer")


def test_module_loader(monkeypatch):
    assert ce._module("chatbot_variables") is cv
    assert ce._module("does_not_exist") is None
    assert ce._cv() is cv and ce._lk() is lk
    assert ce._cvars().__name__ == "modules.chatbot_custom_variables"


def test_value_unwraps_enums():
    assert ce._value(SimpleNamespace(t=EventType.BITS), "t") == "bits"
    assert ce._value(SimpleNamespace(t="x"), "t") == "x"
    assert ce._value(SimpleNamespace(), "missing", "d") == "d"


def test_events_catalog_uses_variables_module_or_falls_back(monkeypatch):
    assert ce.events_catalog() == list(cv.EVENTS)
    monkeypatch.setattr(ce, "_cv", lambda: None)
    fallback = ce.events_catalog()
    assert len(fallback) == len(EventType) and {e["value"] for e in fallback} == {e.value for e in EventType}


def test_event_filters_and_all_keys():
    assert [f["key"] for f in ce.event_filters("bits")] == ["bits_quantity"]
    assert ce.event_filters("follow") == []
    assert ce.event_filters("nope") == []
    keys = ce.all_filter_keys()
    assert keys["bits_quantity"] == 0 and keys["raid_raider_name"] == "" and keys["interval"] == 600


def test_all_filter_keys_defaults_by_kind(monkeypatch):
    monkeypatch.setattr(ce, "events_catalog", lambda: [
        {"value": "a", "filters": [{"key": "n", "kind": "number"}, {"key": "t", "kind": "text"},
                                   {"key": "d", "kind": "duration"}, {"key": "s", "kind": "select"}]},
        {"value": "b"},
    ])
    assert ce.all_filter_keys() == {"n": 0, "t": "", "d": 0, "s": 0}


def test_lookups_catalog_and_info(monkeypatch):
    assert ce.lookups_catalog() == list(lk.LOOKUPS)
    assert ce.lookup_info("uptime")["group"] == "Your stream"
    assert ce.lookup_info("advanced")["label"] == "Twitch API request"
    assert ce.lookup_info("my_thing") == {"id": "my_thing", "label": "My thing", "hint": ""}
    assert ce.lookup_info(None)["label"] == ""
    monkeypatch.setattr(ce, "_lk", lambda: None)
    assert ce.lookups_catalog() == []


def test_lookup_available(monkeypatch):
    monkeypatch.setattr(lk, "lookup_available", lambda entry: (1, None))
    assert ce.lookup_available({"id": "x"}) == (True, "")

    def boom(entry):
        raise RuntimeError("twitch gone")

    monkeypatch.setattr(lk, "lookup_available", boom)
    assert ce.lookup_available({"id": "x"}) == (False, "twitch gone")
    monkeypatch.setattr(ce, "_lk", lambda: None)
    assert ce.lookup_available({"id": "x"}) == (True, "")


def test_permissions_and_date_formats(monkeypatch):
    assert ce.permissions_catalog() == [tuple(p) for p in cv.PERMISSIONS]
    assert ce.date_formats() == list(cv.DATE_FORMATS)
    monkeypatch.setattr(cv, "PERMISSIONS", None)
    monkeypatch.setattr(cv, "DATE_FORMATS", None)
    assert ce.permissions_catalog() == ce._PERMISSIONS_FALLBACK
    assert ce.date_formats() == list(ce.DEFAULT_DATE_FORMATS)
    monkeypatch.setattr(ce, "_cv", lambda: None)
    assert ce.permissions_catalog() == ce._PERMISSIONS_FALLBACK
    assert ce.date_formats() == list(ce.DEFAULT_DATE_FORMATS)


# --------------------------------------------------------------------------- state


def test_state_snapshot_is_stable_json():
    a = ce.ChatbotState(kind="command", item_id="x", is_new=True)
    b = ce.ChatbotState(kind="command", item_id="x", is_new=True)
    assert a.snapshot() == b.snapshot()
    b.name = "changed"
    assert a.snapshot() != b.snapshot()


def test_clean_word():
    assert ce._clean_word("  !Hello ") == "hello"
    assert ce._clean_word(None) == ""


def test_inputs_from_mappings_edge_cases():
    out = ce._inputs_from_mappings({"b": {"position": "x"}, "a": 0, "c": {"position": 3, "default": None}})
    assert out == [{"name": "a", "position": 1, "default": ""}, {"name": "b", "position": 1, "default": ""},
                   {"name": "c", "position": 3, "default": ""}]
    assert ce._inputs_from_mappings(None) == []


def test_mappings_from_inputs_skips_blank_names():
    assert ce._mappings_from_inputs([{"name": " ", "position": 1}, {"name": "x", "position": 0},
                                     {"name": "y", "position": 2, "default": "d"}]) == {"x": 1, "y": {"position": 2, "default": "d"}}


def test_state_from_new_item():
    s = ce.state_from_item("command", None)
    assert s.is_new and len(s.item_id) == 36 and s.reply_targets == ["twitch"]
    assert ce.state_from_item("event", None, "given").item_id == "given"


def test_state_from_command_legacy_mod_only_and_junk_aliases():
    legacy = SimpleNamespace(command_id="", name=None, description=None, enabled=False, lookup=None, lookup_fallback=None,
                             response_text=None, reply_targets=None, output_mode=None, discord_channels=None,
                             permission=None, mod_only=True, command_name="!X", aliases=["!a", "ValueChange junk", ""],
                             cooldown=None, usage_limit=None, command_type=None, persistent_counter=False,
                             counter_value=None, reset_command="!deaths", argument_mappings=None)
    s = ce.state_from_item("command", legacy, "fallback-id")
    assert s.item_id == "fallback-id" and s.permission == "moderator" and s.aliases == ["a"]
    assert s.command_name == "x" and s.reset_command == "deaths" and s.command_type == "basic"
    assert s.enabled is False and s.reply_targets == [] and s.output_mode == "chat"
    plain = SimpleNamespace(**{**vars(legacy), "mod_only": False})
    assert ce.state_from_item("command", plain).permission == "everyone"


def test_state_from_event_filters():
    s = ce.state_from_item("event", evt("e1", "raid", raid_viewer_count=5, raid_raider_name="bob"))
    assert s.filters == {"raid_viewer_count": 5, "raid_raider_name": "bob"} and s.event_type == "raid"
    timer = ce.state_from_item("event", evt("e2", "interval", interval=900))
    assert timer.filters == {"interval": 900}
    at = ce.state_from_item("event", evt("e3", "specific_time", specific_time="07:30"))
    assert at.filters == {"specific_time": "07:30"}


def test_state_from_event_time_filters_without_catalog(monkeypatch):
    monkeypatch.setattr(ce, "event_filters", lambda et: [])
    assert ce.state_from_item("event", evt("e2", "interval", interval=900)).filters == {"interval": 900}
    assert ce.state_from_item("event", evt("e3", "specific_time", specific_time="07:30")).filters == {"specific_time": "07:30"}
    nameless = SimpleNamespace(event_id="", event_type="follow", name="", enabled=True)
    assert ce.state_from_item("event", nameless, "id9").item_id == "id9"


def test_other_commands(ed_env):
    ed_env.mgr.commands = {"a": cmd("a"), "b": cmd("b", "bye")}
    s = ce.ChatbotState(kind="command", item_id="a", is_new=False)
    assert set(ce._other_commands(s)) == {"b"}


def test_other_commands_errors(monkeypatch):
    def boom():
        raise RuntimeError("no manager")

    monkeypatch.setattr(ce, "get_manager", boom)
    assert ce._other_commands(ce.ChatbotState(kind="command", item_id="a", is_new=True)) == {}


# --------------------------------------------------------------------------- problems


def _cmd_state(**kw):
    s = ce.ChatbotState(kind="command", item_id="me", is_new=True, name="N", command_name="go", response_text="r")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_problems_ok_command(ed_env):
    assert ce.state_problems(_cmd_state()) == []


@pytest.mark.parametrize("changes,problem", [
    ({"inputs": [{"name": "", "position": 1}]}, "Give every input a name."),
    ({"inputs": [{"name": "Bad Name", "position": 1}]}, "Input names use lowercase letters, digits and _ only."),
    ({"inputs": [{"name": "a", "position": 1}, {"name": "a", "position": 2}]}, "Input names must be different."),
    ({"reply_targets": [], "discord_channels": []}, "Choose where the reply goes."),
    ({"response_text": "  "}, "Write the reply."),
    ({"lookup": {"id": "advanced", "path": " "}}, "Enter the Twitch API path for the advanced request."),
])
def test_command_problems(ed_env, changes, problem):
    assert problem in ce.state_problems(_cmd_state(**changes))


def test_reset_command_needs_no_reply(ed_env):
    assert ce.state_problems(_cmd_state(command_type="reset", reset_command="deaths", response_text="")) == []


def test_alias_collisions_and_unnamed_owner(ed_env):
    ed_env.mgr.commands = {"o": SimpleNamespace(command_name="other", aliases=["dup"], name="")}
    assert "!dup is already used by “another command”." in ce.state_problems(_cmd_state(aliases=["dup"]))


def test_banner_or_discord_is_enough_output(ed_env):
    assert ce.state_problems(_cmd_state(reply_targets=[], output_mode="banner")) == []
    assert ce.state_problems(_cmd_state(reply_targets=[], discord_channels=[{"guild_id": "g"}])) == []


@pytest.mark.parametrize("etype,filters,problem", [
    (None, {}, "Choose an event."),
    ("interval", {"interval": 5}, "Timers need at least 10 seconds between messages."),
    ("interval", {}, "Timers need at least 10 seconds between messages."),
    ("specific_time", {"specific_time": "25:00"}, "Set the time of day (HH:MM)."),
    ("specific_time", {}, "Set the time of day (HH:MM)."),
])
def test_event_problems(etype, filters, problem):
    s = ce.ChatbotState(kind="event", item_id="e", is_new=True, name="", event_type=etype, filters=filters, response_text="x")
    problems = ce.state_problems(s)
    assert problems[0] == "Name this event." and problem in problems


def test_event_without_problems():
    s = ce.ChatbotState(kind="event", item_id="e", is_new=True, name="n", event_type="specific_time",
                        filters={"specific_time": "9:30"}, response_text="x")
    assert ce.state_problems(s) == []


# --------------------------------------------------------------------------- build_item


def test_build_command_from_scratch_and_bad_existing(ed_env):
    s = _cmd_state(aliases=["a", "a", "b"], permission="moderator", cooldown=-3, usage_limit=None,
                   inputs=[{"name": "who", "position": 1, "default": ""}], lookup={"id": "follow_age"})
    item = ce.build_item(s)
    assert isinstance(item, ChatCommand) and item.aliases == ["a", "b"] and item.mod_only
    assert item.cooldown == 0 and item.usage_limit == 0 and item.argument_mappings == {"who": {"position": 1, "default": ""}}
    assert item.lookup == {"id": "follow_age"} and item.api_enabled is False

    class Broken:
        lookup = None

        def to_dict(self):
            raise RuntimeError("bad")

    assert ce.build_item(_cmd_state(), Broken()).command_name == "go"


def test_build_event_resets_unused_filters_and_interval(ed_env):
    existing = evt("e1", "bits", bits_quantity=500, interval=60)
    s = ce.state_from_item("event", existing)
    s.event_type = "raid"
    s.filters = {"raid_viewer_count": 3}
    item = ce.build_item(s, existing)
    assert item.event_type is EventType.RAID and item.raid_viewer_count == 3
    assert item.bits_quantity == 0 and item.interval == 0
    s.event_type = "interval"
    s.filters = {"interval": 120}
    assert ce.build_item(s, existing).interval == 120


# --------------------------------------------------------------------------- run_item_test


def _run_test(ed_env, kind="command", item_id="c1"):
    ce.run_item_test(kind, item_id)
    name, coro = ed_env.tasks.pop()
    assert name == f"chatbot_test_{kind}"
    asyncio.run(coro)


def test_run_item_test_reply_notification(ed_env):
    ed_env.mgr.test_result = {"ok": True, "triggered": True, "response": "Hello there"}
    _run_test(ed_env)
    message, kw = ed_env.notes.calls[0]
    assert message == "Hello there" and kw["title"] == "Test reply"
    assert kw["actions"] == [{"kind": "copy", "text": "Hello there", "label": "Copy reply"}]
    assert ed_env.mgr.calls == [("test_command", "c1")]


def test_run_item_test_event_and_awaitable(ed_env):
    async def result():
        return {"ok": True, "triggered": True, "response": ""}

    ed_env.mgr.test_event = lambda eid: result()
    _run_test(ed_env, "event", "e1")
    assert ed_env.notes.messages == ["Test ran with sample data"]


@pytest.mark.parametrize("result,message,title", [
    ({"ok": True, "triggered": False, "reason": "Conditions not met"}, "Conditions not met", "Test didn't run"),
    (None, "Its filters didn't match the sample.", "Test didn't run"),
    ({"ok": False, "triggered": False, "reason": "Command not found"}, "Command not found", "Test didn't run"),
    ({"ok": False, "reason": "KeyError: x"}, "", "The command test couldn't run"),
])
def test_run_item_test_outcomes(ed_env, result, message, title):
    ed_env.mgr.test_result = result
    _run_test(ed_env)
    assert ed_env.notes.messages == [message] and ed_env.notes.titles == [title]


def test_run_item_test_exception(ed_env):
    def boom(cid):
        raise ValueError("bad sample")

    ed_env.mgr.test_command = boom
    _run_test(ed_env)
    assert ed_env.notes.titles == ["The command test couldn't run"]
    assert ed_env.notes.calls[0][1]["details"] == "ValueError: bad sample"


def test_run_item_test_missing_function(ed_env, monkeypatch):
    monkeypatch.setattr(ce, "get_manager", lambda: SimpleNamespace())
    _run_test(ed_env, "event", "e1")
    assert ed_env.notes.calls[0][1]["details"] == "No test function for event"


def test_compile_fallback():
    assert ce._compile_fallback("math", {"left": "1", "op": "*", "right": "2"}) == "math(1 * 2)"
    assert ce._compile_fallback("math", {"decimals": 1}) == "math(round(0 + 0, 1))"
    assert ce._compile_fallback("math", {"decimals": ""}) == "math(0 + 0)"
    assert ce._compile_fallback("age", {"date": "{x}"}) == "date_to_age({x})"
    assert ce._compile_fallback("text", {"template": "t"}) == "t"
    assert ce._compile_fallback("if", {"left": "a", "right": "b"}) == "compare(a, equal, b)"
    assert ce._compile_fallback("expression", {"expression": "e"}) == "e"
    assert ce._compile_fallback("twitch", {}) == ""

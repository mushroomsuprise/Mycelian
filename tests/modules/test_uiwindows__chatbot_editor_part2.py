# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_editor.py (part 2): the editor dialog for commands and events,
closing, and saving. NiceGUI user simulation; the chatbot manager is a fake."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from _chatbot_editor_fakes import (  # noqa: E402,F401  (ed_env: fixture)
    buttons, click, cmd, ed_env, evt, inputs, label_click, labels, set_value, htmls,
)
from modules.uiwindows import chatbot_editor as ce  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def run_editor(kind, item_id, check=None, on_saved=None):
    holder = {}

    def build():
        holder["ed"] = ce.open_chatbot_editor(kind, item_id, on_saved or (lambda: None))

    async def wrapped(user):
        if check is not None:
            await check(user, holder["ed"])

    run_ui(build, wrapped)
    return holder["ed"]


def status(ed):
    return ed.status_label.text


# --------------------------------------------------------------------------- new command


def test_new_command_opens_with_problems_and_becomes_ready(ed_env):
    async def check(user, ed):
        await user.should_see("New")
        await user.should_see("Create command")
        assert status(ed) == "Name this command.  (+2 more)"
        assert "is-warn" in ed.status_box.classes
        fields = inputs(user)
        set_value(user, fields[0], "Discord link")  # name
        set_value(user, fields[1], "A link")  # description
        set_value(user, fields[2], "!Discord now")  # command word
        assert ed.state.command_name == "discord now"
        assert status(ed).startswith("Commands can't contain spaces.")
        set_value(user, fields[2], "!Discord")
        assert ed.state.command_name == "discord"
        user.find(kind=ui.textarea).type("Join us")
        assert status(ed) == "Ready to save" and "is-ok" in ed.status_box.classes
        assert ed.state.name == "Discord link" and ed.state.description == "A link"

    ed = run_editor("command", None, check)
    assert ed.state.is_new and ed.kind == "command"


def test_word_blur_rewrites_display(ed_env):
    async def check(user, ed):
        word = inputs(user)[2]
        set_value(user, word, "!Hey")
        assert ed.state.command_name == "hey"
        with user.client:
            word.value = "!Hey"
        from nicegui.testing.user_interaction import UserInteraction

        UserInteraction(user, {word}, None).trigger("blur")
        assert word.value == "hey"
        UserInteraction(user, {word}, None).trigger("blur")  # already clean: nothing to do

    run_editor("command", None, check)


def test_enable_switch_updates_label(ed_env):
    async def check(user, ed):
        switch = sorted(user.find(kind=ui.switch).elements, key=lambda e: e.id)[0]
        set_value(user, switch, False)
        assert ed.state.enabled is False
        await user.should_see("Off")

    run_editor("command", None, check)


def test_permission_cooldown_and_limit(ed_env):
    async def check(user, ed):
        label_click(user, "Mods")
        assert ed.state.permission == "moderator"
        nums = sorted(user.find(kind=ui.number).elements, key=lambda e: e.id)
        set_value(user, nums[0], 30)
        set_value(user, nums[1], 5)
        assert ed.state.cooldown == 30 and ed.state.usage_limit == 5
        set_value(user, nums[1], None)
        assert ed.state.usage_limit == 0

    run_editor("command", None, check)


def test_command_types_counter_and_reset(ed_env):
    ed_env.mgr.commands = {"d": cmd("d", "deaths", command_type="counter"), "b": cmd("b", "basic"),
                           "x": cmd("x", "", command_type="counter")}

    async def check(user, ed):
        assert "Counters add 1 each time" in htmls(user)
        label_click(user, "Counter")
        assert ed.state.command_type == "counter"
        await user.should_see("Keep the count after a restart")
        count = sorted(user.find(kind=ui.number).elements, key=lambda e: e.id)[-1]
        set_value(user, count, 12)
        assert ed.state.counter_value == 12
        keep = [s for s in user.find(kind=ui.switch).elements if s.text == "Keep the count after a restart"][0]
        set_value(user, keep, False)
        assert ed.state.persistent_counter is False
        label_click(user, "Reset a counter")
        assert ed.state.command_type == "reset"
        sel = user.find(kind=ui.select).elements
        reset_sel = [s for s in sel if "deaths" in s.options][0]
        assert reset_sel.options == {"deaths": "!deaths · Deaths"}
        set_value(user, reset_sel, "deaths")
        assert ed.state.reset_command == "deaths"

    run_editor("command", None, check)


def test_reset_select_without_counters_and_missing_target(ed_env):
    ed_env.mgr.commands = {"r": cmd("r", "rs", command_type="reset", reset_command="gone")}

    async def check(user, ed):
        sel = [s for s in user.find(kind=ui.select).elements if "gone" in s.options][0]
        assert sel.options == {"gone": "!gone (not found)"}
        assert "Optional for reset commands" in htmls(user)
        with user.client:
            ed.state.reset_command = ""
            ed._rerender(ed.command_box, ed._build_command)
        sel = [s for s in user.find(kind=ui.select).elements if "" in s.options][0]
        assert sel.options == {"": "(no counter commands yet)"}

    run_editor("command", "r", check)


def test_usage_reset_link(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1", usage_count=3, usage_limit=10)}

    async def check(user, ed):
        await user.should_see("Edit")
        await user.should_see("Save")
        label_click(user, "used 3 · reset")
        assert ("reset_usage", "c1") in ed_env.mgr.calls
        assert ed_env.notes.messages == ["Uses reset to 0"]
        ed_env.mgr.reset_ok = False
        with user.client:
            ed._reset_usage()
        assert ed_env.notes.messages[-1].startswith("This command was deleted")
        ed.state.is_new = True
        with user.client:
            ed._reset_usage()
        assert ed_env.notes.messages[-1] == "Save the command first, then reset its uses."

    run_editor("command", "c1", check)


def test_aliases_add_and_remove(ed_env):
    from nicegui.testing.user_interaction import UserInteraction

    async def check(user, ed):
        with user.client:
            ed.state.command_name = "main"
        entry = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("Add another")][0]
        set_value(user, entry, "!One, main two")  # no trailing separator yet
        assert ed.state.aliases == []
        UserInteraction(user, {entry}, None).trigger("keydown.enter")
        assert ed.state.aliases == ["one", "two"]
        entry = [i for i in inputs(user) if i.props.get("placeholder", "") == "Add another…"][0]
        set_value(user, entry, "three ")  # trailing space commits
        assert ed.state.aliases == ["one", "two", "three"]
        entry = [i for i in inputs(user) if i.props.get("placeholder", "") == "Add another…"][0]
        UserInteraction(user, {entry}, None).trigger("blur")  # empty: nothing happens
        assert ed.state.aliases == ["one", "two", "three"]
        alias_label = [e for e in user.find(kind=ui.label).elements if e.text == "!two"][0]
        close = [b for b in alias_label.parent_slot.parent.default_slot.children if isinstance(b, ui.button)][0]
        click(user, close)
        assert ed.state.aliases == ["one", "three"]

    run_editor("command", None, check)


def test_inputs_add_edit_and_remove(ed_env):
    from nicegui.testing.user_interaction import UserInteraction

    async def check(user, ed):
        with user.client:
            ed.state.command_name = "so"
        label_click(user, "Add input")
        assert ed.state.inputs == [{"name": "target", "position": 1, "default": ""}]
        label_click(user, "Add input")
        assert ed.state.inputs[1] == {"name": "word2", "position": 2, "default": ""}
        assert ed.inputs_box is not None
        usage = [e for e in user.find(kind=ui.html).elements if "ce-usage" in e.classes][0]
        assert "!so</b> <i>&lt;target&gt;</i> <i>&lt;word2&gt;</i>" in usage.content
        name = [i for i in inputs(user) if i.value == "word2"][0]
        set_value(user, name, "Big Name!")
        assert ed.state.inputs[1]["name"] == "big_name_"
        UserInteraction(user, {name}, None).trigger("blur")
        assert name.value == "big_name_"
        UserInteraction(user, {name}, None).trigger("blur")
        pos = [s for s in user.find(kind=ui.select).elements if s.value == 2][0]
        set_value(user, pos, 4)
        assert ed.state.inputs[1]["position"] == 4
        assert "<i>…</i>" in usage.content
        default = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("If empty")][0]
        set_value(user, default, "{username}")
        assert ed.state.inputs[0]["default"] == "{username}"
        tag = [e for e in user.find(kind=ui.label).elements if "ce-tag" in e.classes and e.text == "2 inputs"]
        assert tag
        remove = buttons(user, icon="close")
        remove = [b for b in remove if "is-danger" in b.classes][0]
        click(user, remove)
        assert [i["name"] for i in ed.state.inputs] == ["big_name_"]
        with user.client:
            ed._remove_input(9)  # out of range: just re-renders
        assert len(ed.state.inputs) == 1

    run_editor("command", None, check)


def test_add_tenth_input(ed_env):
    async def check(user, ed):
        with user.client:
            ed.state.inputs = [{"name": f"w{p}", "position": p, "default": ""} for p in range(1, 10)]
            ed._rerender(ed.inputs_box, ed._build_inputs)
            ed._add_input()
        assert len(ed.state.inputs) == 10

    run_editor("command", None, check)


def test_add_input_when_ten_positions_are_taken(ed_env, monkeypatch):
    async def check(user, ed):
        monkeypatch.setattr(ed, "_rerender", lambda box, builder: None)
        monkeypatch.setattr(ed, "render_vars", lambda: None)
        ed.state.inputs = [{"name": f"w{p}", "position": p, "default": ""} for p in range(1, 11)]
        ed._add_input()
        assert ed.state.inputs[-1] == {"name": "word1", "position": 1, "default": ""}

    run_editor("command", None, check)


def test_count_helper():
    assert ce.ChatbotEditor._count(0, "input") == ""
    assert ce.ChatbotEditor._count(1, "input") == "1 input"
    assert ce.ChatbotEditor._count(3, "input") == "3 inputs"


# --------------------------------------------------------------------------- close


def test_close_without_changes(ed_env):
    async def check(user, ed):
        click(user, buttons(user, icon="close")[0])
        assert ed.dialog.value is False

    run_editor("command", None, check)


def test_close_with_changes_asks_first(ed_env):
    async def check(user, ed):
        set_value(user, inputs(user)[0], "changed")
        user.find(kind=ui.button, content="Cancel").click()
        await user.should_see("Unsaved changes")
        user.find(kind=ui.button, content="Keep editing").click()
        assert ed.dialog.value is True
        user.find(kind=ui.button, content="Cancel").click()
        user.find(kind=ui.button, content="Discard").click()
        assert ed.dialog.value is False

    run_editor("command", None, check)


# --------------------------------------------------------------------------- save


def _fill_command(user, ed):
    with user.client:
        ed.state.name = "Hi"
        ed.state.command_name = "hi"
        ed.state.response_text = "Hello"


def test_save_with_problems_warns(ed_env):
    async def check(user, ed):
        user.find(kind=ui.button, content="Create command").click()
        assert ed_env.notes.titles == ["Can't save the command yet"]
        assert ed_env.notes.messages[0] == "Name this command. (+2 more)"
        with user.client:
            ed.state.name, ed.state.command_name = "x", "x"
            ed.save()
        assert ed_env.notes.messages[-1] == "Write the reply."

    run_editor("command", None, check)


def test_save_new_command(ed_env):
    saved = []

    async def check(user, ed):
        _fill_command(user, ed)
        user.find(kind=ui.button, content="Create command").click()

    ed = run_editor("command", None, check, on_saved=lambda: saved.append(1))
    name, args, _kw = [c for c in ed_env.mgr.calls if c[0] == "add_command"][0]
    assert args[0].command_name == "hi"
    assert ed_env.notes.messages == ["Created “Hi”"] and saved == [1]
    assert ed.dialog.value is False and not ed._dirty()


def test_save_and_test_runs_item_test(ed_env):
    async def check(user, ed):
        _fill_command(user, ed)
        user.find(kind=ui.button, content="Save & test").click()

    def broken_on_saved():
        raise RuntimeError("refresh failed")

    run_editor("command", None, check, on_saved=broken_on_saved)
    assert ed_env.tasks and ed_env.tasks[-1][0] == "chatbot_test_command"


def test_save_validation_message_and_error(ed_env):
    ed_env.mgr.validate_message = "!hi is already used by another command."

    async def check(user, ed):
        _fill_command(user, ed)
        with user.client:
            ed.save()
        assert ed_env.notes.messages == ["!hi is already used by another command."]
        ed_env.mgr.validate_message = RuntimeError("validator broke")
        with user.client:
            ed.save()
        assert ed_env.notes.messages[-1] == "Created “Hi”"

    run_editor("command", None, check)


def test_save_build_failure(ed_env, monkeypatch):
    async def check(user, ed):
        _fill_command(user, ed)

        def boom(state, existing=None):
            raise ValueError("odd value")

        monkeypatch.setattr(ce, "build_item", boom)
        with user.client:
            ed.save()
        assert ed_env.notes.titles == ["Couldn't save the command"]
        assert ed_env.notes.calls[0][1]["details"] == "ValueError: odd value"
        assert ed.dialog.value is True

    run_editor("command", None, check)


def test_save_existing_command_keeps_or_drops_runtime(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1", command_type="counter", counter_value=4)}

    async def check(user, ed):
        with user.client:
            ed.save()
        with user.client:
            ed.state.counter_value = 9
            ed.save()

    run_editor("command", "c1", check)
    updates = [c for c in ed_env.mgr.calls if c[0] == "update_command"]
    assert updates[0][2] == {"keep_runtime": True} and updates[1][2] == {"keep_runtime": False}
    assert ed_env.notes.messages[0] == "Saved “Hello”"


def test_save_existing_deleted_meanwhile(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1")}
    ed_env.mgr.save_ok = False

    async def check(user, ed):
        with user.client:
            ed.save()
        assert ed_env.notes.messages[0].startswith("This command was deleted while you were editing it")
        assert ed.dialog.value is True

    run_editor("command", "c1", check)


def test_save_raises(ed_env):
    ed_env.mgr.save_error = RuntimeError("db locked")

    async def check(user, ed):
        _fill_command(user, ed)
        with user.client:
            ed.save()
        assert ed_env.notes.messages == ["Nothing was saved. Your changes are still in the editor."]
        assert ed_env.notes.calls[0][1]["details"] == "RuntimeError: db locked"

    run_editor("command", None, check)


def test_save_events(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "follow")}

    async def check(user, ed):
        await user.should_see("Save")
        with user.client:
            ed.save()

    run_editor("event", "e1", check)
    assert [c[0] for c in ed_env.mgr.calls if c[0].endswith("_event")] == ["validate_event", "update_event"]

    async def check_new(user, ed):
        await user.should_see("Create event")
        with user.client:
            ed.state.name = "F"
            ed.state.response_text = "x"
            ed._choose_event("follow")
            ed.save()

    run_editor("event", None, check_new)
    assert any(c[0] == "add_event" for c in ed_env.mgr.calls)


def test_save_without_validator(ed_env, monkeypatch):
    class Bare:
        def add_command(self, item):
            return True

    monkeypatch.setattr(ce, "get_manager", lambda: Bare())

    async def check(user, ed):
        _fill_command(user, ed)
        with user.client:
            ed.save()
        assert ed_env.notes.messages == ["Created “Hi”"]

    holder = {}

    def build():
        holder["ed"] = ce.ChatbotEditor("command", None, lambda: None)
        holder["ed"].open()

    async def wrapped(user):
        await check(user, holder["ed"])

    run_ui(build, wrapped)


# --------------------------------------------------------------------------- events


def test_new_event_starts_with_picker_and_search(ed_env):
    async def check(user, ed):
        await user.should_see("Choose an event")
        assert ed._picking_event and ed.state.reply_targets == ["twitch"]
        assert "Choose an event to see its variables" in htmls(user)
        search = [i for i in inputs(user) if i.props.get("placeholder") == "Search events"][0]
        set_value(user, search, "zzzz")
        await user.should_see("No events match.")
        set_value(user, search, "raid")
        await user.should_see("Raid")
        label_click(user, "Raid")
        assert ed.state.event_type == "raid" and not ed._picking_event
        assert ed.state.filters == {"raid_viewer_count": 0, "raid_raider_name": ""}

    run_editor("event", None, check)


def test_event_picker_marks_warnings(ed_env):
    async def check(user, ed):
        await user.should_see("May not fire")

    run_editor("event", None, check)


def test_event_change_and_cancel(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "bits", bits_quantity=100)}

    async def check(user, ed):
        await user.should_see("Bits")
        user.find(kind=ui.button, content="Change").click()
        assert ed._picking_event
        click(user, buttons(user, text="Cancel")[-1])  # the picker's Cancel, not the footer's
        assert not ed._picking_event and ed.state.event_type == "bits"
        with user.client:
            ed._start_event_pick()
            ed._choose_event("bits")  # same event keeps filters
        assert ed.state.filters == {"bits_quantity": 100}

    run_editor("event", "e1", check)


def test_choose_event_moves_chatter_lookup_target(ed_env):
    async def check(user, ed):
        with user.client:
            ed.state.lookup = {"id": "follow_age", "target": {"from": "chatter"}}
            ed._choose_event("subscription")
        assert ed.state.lookup["target"] == {"from": "event_user"}
        assert ed.state.filters == {"sub_tier": 0}
        with user.client:
            ed.state.lookup = {"id": "uptime"}
            ed._choose_event("follow")
        assert "target" not in ed.state.lookup

    run_editor("event", None, check)


def test_event_number_text_and_warning_filters(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "raid"), "h": evt("h", "hype_train_progress")}

    async def check(user, ed):
        num = user.find(kind=ui.number).elements.pop()
        set_value(user, num, 25)
        assert ed.state.filters["raid_viewer_count"] == 25
        set_value(user, num, None)
        assert ed.state.filters["raid_viewer_count"] == 0
        text = [i for i in inputs(user) if i.props.get("placeholder") == "(any)"][0]
        set_value(user, text, "bob")
        assert ed.state.filters["raid_raider_name"] == "bob"
        assert "At least: 0 = any · Only from: Channel name; empty = anyone" in htmls(user)
        await user.should_see("Only when")

    run_editor("event", "e1", check)

    async def check_warn(user, ed):
        assert "Twitch may no longer send Hype Train events" in htmls(user)

    run_editor("event", "h", check_warn)


def test_chat_message_match_and_paired_text(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "chat_message")}

    async def check(user, ed):
        match = [s for s in user.find(kind=ui.select).elements if "contains" in s.options][0]
        set_value(user, match, "contains")
        text = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("!lurk")][0]
        set_value(user, text, "!lurk")
        assert ed.state.filters == {"chat_message_text": "!lurk", "chat_message_match_type": "contains"}
        await user.should_see("Message")

    run_editor("event", "e1", check)


def test_interval_duration_widget(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "interval", interval=7200)}

    async def check(user, ed):
        await user.should_see("Runs")
        unit = [s for s in user.find(kind=ui.select).elements if "hr" in s.options][0]
        assert unit.value == "hr"
        amount = [n for n in user.find(kind=ui.number).elements][0]
        assert amount.value == 2
        set_value(user, amount, 3)
        assert ed.state.filters["interval"] == 10800
        set_value(user, unit, "min")
        assert ed.state.filters["interval"] == 180
        set_value(user, unit, None)
        assert ed.state.filters["interval"] == 3

    run_editor("event", "e1", check)


@pytest.mark.parametrize("seconds,unit,amount", [(0, "min", 20), (90, "sec", 90), (120, "min", 2)])
def test_duration_unit_choice(ed_env, seconds, unit, amount):
    ed_env.mgr.events = {"e1": evt("e1", "interval", interval=seconds)}

    async def check(user, ed):
        unit_sel = [s for s in user.find(kind=ui.select).elements if "hr" in s.options][0]
        assert unit_sel.value == unit
        assert user.find(kind=ui.number).elements.pop().value == amount

    run_editor("event", "e1", check)


def test_time_and_select_filters(ed_env):
    ed_env.mgr.events = {"t": evt("t", "specific_time", specific_time="08:15"), "s": evt("s", "subscription", sub_tier=2)}

    async def check(user, ed):
        t = [i for i in inputs(user) if i.props.get("type") == "time"][0]
        set_value(user, t, "21:45:00")
        assert ed.state.filters["specific_time"] == "21:45"
        set_value(user, t, None)
        assert ed.state.filters["specific_time"] == ""

    run_editor("event", "t", check)

    async def check_sel(user, ed):
        tier = [s for s in user.find(kind=ui.select).elements if 3 in s.options][0]
        assert tier.value == 2
        set_value(user, tier, 3)
        assert ed.state.filters["sub_tier"] == 3

    run_editor("event", "s", check_sel)


def test_custom_filter_kinds(ed_env, monkeypatch):
    catalog = [{"value": "bits", "label": "Bits", "group": "Twitch", "filters": [
        {"key": "f_num", "kind": "number", "default": 0.5},
        {"key": "f_sel", "kind": "select", "options": {"1": "One", "two": "Two"}, "default": "x"},
        {"key": "f_time", "kind": "time"},
        {"key": "f_dur", "kind": "duration"},
        {"key": "f_match", "kind": "match"},
        {"key": "f_text", "kind": "text"},
        {"key": "f_other", "kind": "weird", "placeholder": "type"},
    ]}]
    monkeypatch.setattr(ce, "events_catalog", lambda: catalog)
    ed_env.mgr.events = {"e1": evt("e1", "bits")}

    async def check(user, ed):
        num = [n for n in user.find(kind=ui.number).elements if n.value == 0.5][0]
        set_value(user, num, 1.5)
        assert ed.state.filters["f_num"] == 1.5
        sel = [s for s in user.find(kind=ui.select).elements if 1 in s.options][0]
        assert sel.options == {1: "One", "two": "Two"} and sel.value == 1
        match = [s for s in user.find(kind=ui.select).elements if "starts_with" in s.options][0]
        assert match.value == "exact"
        paired = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("!lurk")]
        assert paired  # text next to a match is the message box
        other = [i for i in inputs(user) if i.props.get("placeholder") == "type"][0]
        set_value(user, other, "v")
        assert ed.state.filters["f_other"] == "v" and ed.state.filters["f_dur"] == 1200

    run_editor("event", "e1", check)


def test_event_type_without_filters(ed_env):
    ed_env.mgr.events = {"e1": evt("e1", "follow")}

    async def check(user, ed):
        await user.should_see("New follower")
        assert not [e for e in user.find(kind=ui.label).elements if e.text == "Only when"]

    run_editor("event", "e1", check)


def test_unknown_event_type_info(ed_env, monkeypatch):
    monkeypatch.setattr(ce, "event_info", lambda et: {})
    ed_env.mgr.events = {"e1": evt("e1", "raid")}

    async def check(user, ed):
        await user.should_see("raid")

    run_editor("event", "e1", check)

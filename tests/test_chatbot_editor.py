# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot command/event editor state, save round trips, validation, and the tab cards."""

from __future__ import annotations

import pytest

from modules.chatbot_core import ChatCommand, ChatEvent, CommandType, EventType
from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.uiwindows import chatbot_cards as cards
from modules.uiwindows import chatbot_editor as ce
from modules.uiwindows.chatbot_cards import CHATBOT_CARDS_CSS
from modules.uiwindows.chatbot_editor_styles import CHATBOT_EDITOR_CSS


class _Manager:
    def __init__(self, commands=None):
        self.commands = commands or {}

    def get_all_commands(self):
        return dict(self.commands)


@pytest.fixture
def manager(monkeypatch):
    mgr = _Manager({
        "c1": ChatCommand(command_id="c1", name="Discord", command_name="discord", aliases=["dc"]),
        "c2": ChatCommand(command_id="c2", name="Deaths", command_name="death", command_type=CommandType.COUNTER),
    })
    monkeypatch.setattr(ce, "get_manager", lambda: mgr)
    return mgr


def test_inputs_round_trip_with_defaults():
    mappings = {"target": {"position": 1, "default": "{username}"}, "city": 2}
    inputs = ce._inputs_from_mappings(mappings)
    assert inputs == [{"name": "target", "position": 1, "default": "{username}"}, {"name": "city", "position": 2, "default": ""}]
    assert ce._mappings_from_inputs(inputs) == mappings


def test_command_round_trip_keeps_everything(manager):
    cmd = ChatCommand(command_id="k1", name="Follow age", description="d", command_name="!FollowAge", aliases=["fa"],
                      permission="vip", cooldown=10, usage_limit=5, response_text="{target} {followage}",
                      argument_mappings={"target": 1}, lookup={"id": "follow_age", "target": {"from": "input", "input": "target"}},
                      lookup_fallback="{target} isn't following", reply_targets=["twitch", "youtube"],
                      discord_channels=[{"guild_id": "g", "channel_id": "c"}], counter_value=3, usage_count=9, trigger_count=9)
    state = ce.state_from_item("command", cmd)
    assert state.command_name == "followage" and state.permission == "vip"
    rebuilt = ce.build_item(state, cmd)
    before, after = cmd.to_dict(), rebuilt.to_dict()
    assert {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)} == {}


def test_event_round_trip_and_type_switch(manager):
    ev = ChatEvent(event_id="e1", name="Big bits", event_type=EventType.BITS, bits_quantity=500, response_text="{username} {amount}")
    state = ce.state_from_item("event", ev)
    assert state.filters == {"bits_quantity": 500}
    assert ce.build_item(state, ev).to_dict() == ev.to_dict()

    timer = ChatEvent(event_id="e2", name="Socials", event_type=EventType.INTERVAL, interval=1200, response_text="hi")
    state = ce.state_from_item("event", timer)
    state.event_type = "follow"
    state.filters = {}
    rebuilt = ce.build_item(state, timer)
    assert rebuilt.event_type == EventType.FOLLOW and rebuilt.interval == 0


def test_problems(manager):
    state = ce.state_from_item("command", None)
    assert ce.state_problems(state)[:2] == ["Name this command.", "Type the command viewers use."]
    state.name, state.command_name, state.response_text = "x", "hello there", "hi"
    assert "Commands can't contain spaces." in ce.state_problems(state)
    state.command_name = "dc"
    assert any("already used by “Discord”" in p for p in ce.state_problems(state))
    state.command_name, state.command_type = "fresh", "reset"
    assert "Choose the counter to reset." in ce.state_problems(state)
    state.command_type = "basic"
    state.inputs = [{"name": "Bad Name", "position": 1, "default": ""}]
    assert "Input names use lowercase letters, digits and _ only." in ce.state_problems(state)
    state.inputs = []
    state.reply_targets, state.discord_channels = [], [{"guild_id": "g", "channel_id": "c"}]
    assert ce.state_problems(state) == []  # Discord-only is fine

    event = ce.state_from_item("event", None)
    event.name, event.event_type, event.response_text = "t", "specific_time", "hi"
    event.filters = {"specific_time": "25:00"}
    assert ce.state_problems(event) == ["Set the time of day (HH:MM)."]


def test_counter_options_list_other_counters(manager):
    state = ce.state_from_item("command", None)
    editor = ce.ChatbotEditor.__new__(ce.ChatbotEditor)
    editor.state = state
    assert editor._counter_options() == {"death": "!death · Deaths"}


def test_unknown_tokens_suggest_real_variables(manager):
    editor = ce.ChatbotEditor.__new__(ce.ChatbotEditor)
    editor.kind = "command"
    editor.state = ce.state_from_item("command", None)
    editor.state.inputs = [{"name": "target", "position": 1, "default": ""}]
    editor.existing = None
    found = dict(editor._unknown_tokens("Hi {user} {username} {target} {command_word_2} {stats.x} {custom_y} {time:%H}"))
    assert found == {"user": "username"}


def test_card_helpers():
    cmd = ChatCommand(name="Uptime", command_name="uptime", aliases=["live"], permission="moderator", cooldown=30,
                      lookup={"id": "uptime"}, reply_targets=["twitch"], discord_channels=[{"channel_name": "general"}])
    tags = "".join(cards.command_tags(cmd))
    assert "Mods only" in tags and "30 s cooldown" in tags and "#general" in tags and "Looks up" in tags
    ev = ChatEvent(name="Lurk", event_type=EventType.CHAT_MESSAGE, chat_message_text="!lurk", chat_message_match_type="starts_with")
    assert cards.event_filter_text(ev) == "Starts with “!lurk”"
    timer = ChatEvent(name="T", event_type=EventType.INTERVAL, interval=1200)
    assert cards.event_trigger_text(timer) == "Every 20 minutes"
    assert "uptime" in cards.search_blob(cmd, "commands") and "live" in cards.search_blob(cmd, "commands")


def test_stylesheets_are_well_formed():
    for css in (promote_color_importance(scope_selectors(CHATBOT_EDITOR_CSS, ".cxe")),
                promote_color_importance(scope_selectors(CHATBOT_CARDS_CSS, ".cnx"))):
        assert css.count("{") == css.count("}")
    assert ".cxe.cv {" in scope_selectors(CHATBOT_EDITOR_CSS, ".cxe")


def test_unfilled_tokens_flag_broken_variables():
    assert cards.unfilled_tokens("Hi {user} {username} {count:,}", "Hi {user} Mossy 1,000") == ["{user}"]
    assert cards.unfilled_tokens("No variables", "No variables") == []


def test_quotes_greetings_helpers(monkeypatch):
    from modules.uiwindows import chatbot_quotes_greetings as qg

    assert qg._avatar_color("Mika") == qg._avatar_color("mika")
    assert qg._date(0) == "Date unknown"
    names = [v["key"] for v in qg.greeting_variables()]
    assert names[:3] == ["username", "user_login", "user_id"] and "time" in names

    class _M:
        def render_greeting(self, text, user_id, username, login=""):
            return text.replace("{username}", username)

    monkeypatch.setattr(qg, "get_manager", lambda: _M())
    assert qg.render_greeting_preview("Hi {username}", "Mika") == "Hi Mika"


def test_quotes_greetings_stylesheets():
    from modules.uiwindows.chatbot_quotes_greetings import QG_CSS, QG_DIALOG_CSS

    for css, scope in ((QG_CSS, ".cnx"), (QG_DIALOG_CSS, ".cxe")):
        scoped = promote_color_importance(scope_selectors(css, scope))
        assert scoped.count("{") == scoped.count("}")

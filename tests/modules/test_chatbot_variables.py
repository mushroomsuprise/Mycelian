# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_variables.py: the editor's variable/event catalog, sample data
and previews."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import chatbot_custom_variables as ccv
from modules import chatbot_variables as cv
from modules import dataobjects, statistics_manager
from modules.chatbot_core import ChatCommand, ChatEvent, CommandType, EventType


def _keys(entries):
    return [e["key"] for e in entries]


# --------------------------------------------------------------------------- basics


def test_v_builds_entry_with_extras():
    assert cv._v("k", "L", 1, "G", date=True) == {"key": "k", "label": "L", "example": 1, "group": "G", "date": True}


def test_catalog_shapes():
    assert [p for p, _ in cv.PERMISSIONS] == ["everyone", "subscriber", "vip", "moderator"]
    keys = _keys(cv.COMMON_VARIABLES)
    assert keys[:2] == ["username", "user_login"]
    assert len(keys) == len(set(keys))
    assert all({"key", "label", "example", "group"} <= set(v) for v in cv.COMMON_VARIABLES)
    assert any(v.get("pattern") for v in cv.QUOTE_VARIABLES)
    assert cv.DATE_FORMATS  # re-exported


# --------------------------------------------------------------------------- hype levels


def test_hype_level_variables_explicit_levels():
    out = cv.hype_level_variables([7, 2])
    assert _keys(out) == [f"stats.hype_trains.level_completions.{n}" for n in (1, 2, 3, 4, 5, 7)]
    assert all(v["group"] == "Statistics" for v in out)


def test_hype_level_variables_reads_statistics(monkeypatch):
    sm = SimpleNamespace(get_all_statistics=lambda: {"hype_trains": {"level_completions": {"9": 1, "3": 2}}})
    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: sm)
    assert _keys(cv.hype_level_variables())[-1] == "stats.hype_trains.level_completions.9"


def test_hype_level_variables_statistics_error(monkeypatch):
    def boom():
        raise RuntimeError("no stats")

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", boom)
    assert len(cv.hype_level_variables()) == 5


def test_hype_level_variables_empty_iterable():
    assert len(cv.hype_level_variables([])) == 5


# --------------------------------------------------------------------------- youtube


def test_youtube_variables_per_channel(monkeypatch):
    channels = {
        "a": {"channel_title": "My Cool-Channel", "latest_video_title": "Vid", "channel_url": "https://y/c"},
        "b": {"channel_title": ""},
    }
    monkeypatch.setattr(dataobjects.state_manager, "get_youtube_data", lambda: SimpleNamespace(channels=channels))
    out = cv.youtube_variables()
    keys = _keys(out)
    assert keys[:6] == [
        "youtube.latest_video_title", "youtube.latest_video_url", "youtube.latest_video_channel",
        "youtube.latest_video_id", "youtube.connection_status", "youtube.channel_count",
    ]
    assert keys[6:] == ["youtube.MyCoolChannel_latest_video_title", "youtube.MyCoolChannel_latest_video_url",
                        "youtube.MyCoolChannel_channel_url"]
    by_key = {v["key"]: v for v in out}
    assert by_key["youtube.MyCoolChannel_latest_video_title"]["example"] == "Vid"
    assert by_key["youtube.MyCoolChannel_latest_video_url"]["example"] == ""
    assert by_key["youtube.MyCoolChannel_channel_url"]["label"] == "My Cool-Channel: channel link"


def test_youtube_variables_no_channels_and_error(monkeypatch):
    monkeypatch.setattr(dataobjects.state_manager, "get_youtube_data", lambda: SimpleNamespace(channels=None))
    assert len(cv.youtube_variables()) == 6

    def boom():
        raise RuntimeError("no state")

    monkeypatch.setattr(dataobjects.state_manager, "get_youtube_data", boom)
    assert len(cv.youtube_variables()) == 6


# --------------------------------------------------------------------------- lookups / custom


def test_lookup_variables_flags_and_error_entry(monkeypatch):
    from modules import chatbot_lookups

    specs = [{"key": "a", "label": "A", "example": "1"},
             {"key": "b", "label": "B", "example": "2", "date": True},
             {"key": "c_N", "label": "C", "example": "3", "pattern": True}]
    monkeypatch.setattr(chatbot_lookups, "lookup_variable_specs", lambda lookup: specs)
    out = cv.lookup_variables("x")
    assert _keys(out) == ["a", "b", "c_N", "lookup_error"]
    assert "date" not in out[0] and out[1]["date"] is True and out[2]["pattern"] is True
    assert all(v["group"] == "Lookup" for v in out)
    assert _keys(cv.lookup_variables(None)) == ["a", "b", "c_N"]


def test_lookup_variables_real_catalog():
    from modules import chatbot_lookups

    lookup_id = chatbot_lookups.LOOKUPS[0]["id"]
    keys = _keys(cv.lookup_variables(lookup_id))
    assert keys[-1] == "lookup_error"
    assert len(keys) == len(chatbot_lookups.lookup_variable_specs(lookup_id)) + 1


def test_example_lookup_variables_delegates(monkeypatch):
    from modules import chatbot_lookups

    monkeypatch.setattr(chatbot_lookups, "example_variables", lambda lookup: {"got": lookup})
    assert cv.example_lookup_variables("z") == {"got": "z"}


def test_custom_variables_lists_sorted_with_examples(monkeypatch):
    defs = {
        "custom_b_thing": {"kind": "text", "description": ""},
        "custom_a": {"kind": "math", "description": "Alpha"},
        "custom_err": {"kind": "random"},
    }
    monkeypatch.setattr(ccv, "load_custom_definitions", lambda: defs)

    def preview(definition, ctx):
        if definition.get("kind") == "random":
            raise ValueError("bad def")
        return f"{definition['kind']}:{ctx['marker']}"

    monkeypatch.setattr(ccv, "preview_custom_variable", preview)
    out = cv.custom_variables({"marker": "M"})
    assert _keys(out) == ["custom_a", "custom_b_thing", "custom_err"]
    assert out[0]["label"] == "Alpha" and out[0]["example"] == "math:M" and out[0]["kind"] == "math"
    assert out[1]["label"] == "b thing"
    assert out[2]["example"] == "Error: bad def"
    assert all(v["group"] == "Custom" for v in out)


def test_custom_variables_default_context(monkeypatch):
    seen = []
    monkeypatch.setattr(ccv, "load_custom_definitions", lambda: {"custom_x": {"kind": "text"}})
    monkeypatch.setattr(ccv, "preview_custom_variable", lambda d, ctx: seen.append(ctx) or "v")
    out = cv.custom_variables()
    assert out[0]["example"] == "v"
    assert seen[0]["username"] == "TestViewer"


# --------------------------------------------------------------------------- command variables


def test_command_variables_basic_counter_reset_and_args(monkeypatch):
    base = _keys(cv.command_variables())
    assert base == ["command_message", "command_first_word", "command_last_word", "command_word_N",
                    "cooldown", "usage_left"]
    assert _keys(cv.command_variables(None)) == base
    assert _keys(cv.command_variables(CommandType.COUNTER))[-1] == "count"
    assert _keys(cv.command_variables("reset"))[-2:] == ["target_command", "target_count"]
    args = cv.command_variables("basic", ["game", 5])
    assert args[-2]["key"] == "game" and args[-2]["group"] == "Arguments"
    assert args[-1]["key"] == "5" and args[-1]["label"] == "Argument: 5"
    monkeypatch.setattr(cv, "lookup_variables", lambda lid: [{"key": f"lk_{lid}"}])
    assert _keys(cv.command_variables("basic", None, "abc"))[-1] == "lk_abc"


# --------------------------------------------------------------------------- events


def test_event_index_and_filters():
    assert set(cv.EVENT_INDEX) == {e["value"] for e in cv.EVENTS}
    assert cv.EVENT_INDEX["hype_train_start"]["warning"] == cv._HYPE_WARNING
    assert cv.EVENT_INDEX["follow"]["filters"] == []
    f = cv._f("k", "L", "number", unit="u", default=1, help="h")
    assert f == {"key": "k", "label": "L", "kind": "number", "unit": "u", "options": None, "default": 1, "help": "h"}


def test_event_variables_copies_and_accepts_enum():
    a = cv.event_variables(EventType.FOLLOW)
    assert "followed_at" in _keys(a)
    a[0]["key"] = "mutated"
    assert cv.event_variables("follow")[0]["key"] != "mutated"
    assert cv.event_variables("nope") == []
    assert cv.event_variables("interval") == []


def test_preview_event_types_matches_catalog():
    types = cv.preview_event_types()
    assert set(types) <= set(cv.EVENT_INDEX)
    assert "follow" in types


# --------------------------------------------------------------------------- sample data helpers


def test_attr_handles_none_dict_and_object():
    assert cv._attr(None, "x", 3) == 3
    assert cv._attr({"x": 1}, "x") == 1
    assert cv._attr(SimpleNamespace(x=2), "x") == 2
    assert cv._attr(SimpleNamespace(), "x", "d") == "d"


@pytest.mark.parametrize(
    "item,expected",
    [
        (None, "Hello chat!"),
        ({"chat_message_text": "  "}, "Hello chat!"),
        ({"chat_message_text": "hi"}, "hi"),
        ({"chat_message_text": "hi", "chat_message_match_type": "starts_with"}, "hi everyone"),
        ({"chat_message_text": "hi", "chat_message_match_type": "contains"}, "well hi indeed"),
    ],
)
def test_chat_sample(item, expected):
    assert cv._chat_sample(item) == expected


def test_sample_data_command_words_cover_arguments():
    item = {"command_name": "so", "argument_mappings": {"a": {"position": 5}, "bad": "x"}}
    data = cv.sample_data("command", item=item)
    assert data["message"] == "!so hello world example four five"
    assert data["command_args"] == ["hello", "world", "example", "four", "five"]
    assert data["userid"] == data["user_id"] == "12345678"
    assert "broadcaster/1" in data["badges"]
    default = cv.sample_data("command")
    assert default["message"] == "!command hello world example"


def test_sample_data_many_words_wrap():
    item = {"argument_mappings": {"a": {"position": 12}}}
    assert len(cv.sample_data("command", item=item)["command_args"]) == 12


@pytest.mark.parametrize(
    "event,item,checks",
    [
        ("follow", None, {"followed_at": "2026-03-14T19:30:00Z"}),
        ("subscription", {"sub_tier": 3}, {"tier": 3, "months": 1}),
        ("subscription", {"sub_tier": "bad"}, {"tier": 1}),
        ("resubscription", {"resub_months": 30, "resub_tier": 0}, {"months": 30, "tier": 1}),
        ("resubscription", {"resub_months": "x"}, {"months": 12}),
        ("gift_subscription", {"gift_sub_quantity": 1}, {"amount": 5, "recipient": ""}),
        ("bits", {"bits_quantity": 500}, {"amount": 500}),
        ("donation", {"donation_amount": 1234.5}, {"formatted_amount": "$1,234.50", "source": "streamelements"}),
        ("raid", {"raid_raider_name": "@Bob"}, {"raider_name": "Bob", "user_login": "bob", "viewer_count": 25}),
        ("raid", None, {"username": "FriendlyRaider"}),
        ("channel_point_redemption", {"channel_point_reward_name": "Dance"}, {"reward_title": "Dance"}),
        ("channel_point_redemption", None, {"reward_name": "Hydrate"}),
        ("hype_train_start", None, {"level": 1}),
        ("hype_train_progress", {"hype_train_level": 6}, {"level": 6, "username": ""}),
        ("hype_train_end", None, {"level": 3, "amount": 1000}),
        ("chat_message", {"chat_message_text": "gg"}, {"message": "gg", "badges": ""}),
        ("youtube_chat_message", None, {"source": "youtube", "userid": "UC123", "message": "Hello chat!"}),
        ("youtube_membership", None, {"member_level": "Member", "months": 1}),
        ("youtube_membership_milestone", None, {"cumulative_months": 6}),
        ("youtube_gift_membership", None, {"amount": 5}),
        ("youtube_superchat", None, {"amount": 10.0, "display_amount": "$10.00"}),
        ("youtube_supersticker", None, {"amount": 2.0}),
        ("interval", None, {"source": "interval", "username": "System"}),
        ("specific_time", {"specific_time": "07:15"}, {"scheduled_time": "07:15"}),
        ("specific_time", None, {"scheduled_time": "18:00"}),
        ("mystery", None, {"username": "TestViewer", "source": "twitch"}),
    ],
)
def test_sample_data_events(event, item, checks):
    data = cv.sample_data("event", event, item)
    for key, value in checks.items():
        assert data[key] == value, key


def test_sample_data_gift_quantity_never_below_default():
    # at_least() never goes under the default 5, so the sample is always a multi-gift.
    data = cv.sample_data("event", EventType.GIFT_SUBSCRIPTION, {"gift_sub_quantity": 0})
    assert data["quantity"] == data["amount"] == 5
    assert data["recipient"] == "" and data["recipient_name"] == ""
    assert cv.sample_data("event", "gift_subscription", {"gift_sub_quantity": 20})["amount"] == 20


def test_sample_data_event_type_from_item_and_default():
    item = SimpleNamespace(event_type=EventType.BITS, bits_quantity=None)
    assert cv.sample_data("event", None, item)["amount"] == 100
    assert "followed_at" in cv.sample_data("event")


# --------------------------------------------------------------------------- items / context / preview


def test_as_item_variants():
    cmd = ChatCommand(command_name="hi")
    assert cv._as_item("command", None, cmd) is cmd
    built = cv._as_item("command", None, {"command_name": "yo"})
    assert isinstance(built, ChatCommand) and built.command_name == "yo"
    assert isinstance(cv._as_item("command", None, None), ChatCommand)
    ev = cv._as_item("event", EventType.RAID, {})
    assert isinstance(ev, ChatEvent) and ev.event_type == EventType.RAID
    kept = cv._as_item("event", "raid", {"event_type": "bits"})
    assert kept.event_type == EventType.BITS
    assert cv._as_item("event", None, "junk").event_type == EventType.FOLLOW


def test_example_context_command_reset_and_event():
    ctx = cv.example_context("command", item={"command_name": "reset", "command_type": "reset",
                                              "reset_command": "deaths2"})
    assert ctx["target_command"] == "deaths2"
    assert str(ctx["target_count"]) == "12"
    ctx = cv.example_context("command", item={"command_type": "reset"})
    assert ctx["target_command"] == "deaths"
    ev_ctx = cv.example_context("event", item={"event_type": "raid", "raid_raider_name": "Zed"})
    assert ev_ctx["username"] == "Zed"


def test_example_context_with_lookup(monkeypatch):
    from modules import chatbot_lookups

    lookup_id = chatbot_lookups.LOOKUPS[0]["id"]
    seen = []
    monkeypatch.setattr(cv, "example_lookup_variables", lambda lookup: seen.append(lookup) or {"lookup_ok": True})
    cv.example_context("command", item={"command_name": "x", "lookup": {"id": lookup_id}})
    assert seen and seen[0]["id"] == lookup_id


def test_render_preview_uses_preview_mode(monkeypatch):
    states = []
    monkeypatch.setattr(cv, "render_template", lambda text, ctx: states.append(ccv.in_preview()) or f"{text}|{ctx['username']}")
    assert cv.render_preview("Hi", "event", "follow") == "Hi|TestViewer"
    assert states == [True]
    assert ccv.in_preview() is False


def test_render_preview_real_runtime():
    out = cv.render_preview("Hey {username}, args {command_first_word}", "command", item={"command_name": "x"})
    assert out == "Hey TestViewer, args hello"

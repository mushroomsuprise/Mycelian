# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_triggers: every trigger class, timer/schedule/hotkey/streamdeck rules, weekday
normalizing, schedule parsing and the trigger factory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import datetime
from types import SimpleNamespace

import pytest

from modules import connector_triggers as ct
from modules.connector_core import ComparisonOperator as Op, TriggerCondition, TriggerType

SIMPLE = [
    (TriggerType.TWITCH_BITS, "twitch_bits"),
    (TriggerType.TWITCH_SUB, "twitch_sub"),
    (TriggerType.TWITCH_RESUB, "twitch_resub"),
    (TriggerType.TWITCH_GIFTSUB, "twitch_giftsub"),
    (TriggerType.TWITCH_FOLLOW, "twitch_follow"),
    (TriggerType.TWITCH_RAID, "twitch_raid"),
    (TriggerType.TWITCH_POINTS, "twitch_points"),
    (TriggerType.TWITCH_CHAT_MESSAGE, "twitch_chat_message"),
    (TriggerType.TWITCH_HYPE_TRAIN_START, "twitch_hype_train_start"),
    (TriggerType.TWITCH_HYPE_TRAIN_END, "twitch_hype_train_end"),
    (TriggerType.TWITCH_STREAM_ONLINE, "twitch_stream_online"),
    (TriggerType.TWITCH_STREAM_OFFLINE, "twitch_stream_offline"),
    (TriggerType.YOUTUBE_CHAT_MESSAGE, "youtube_chat_message"),
    (TriggerType.YOUTUBE_MEMBER, "youtube_member"),
    (TriggerType.YOUTUBE_MEMBER_MILESTONE, "youtube_member_milestone"),
    (TriggerType.YOUTUBE_GIFT_MEMBERSHIP, "youtube_gift_membership"),
    (TriggerType.YOUTUBE_SUPERCHAT, "youtube_superchat"),
    (TriggerType.YOUTUBE_SUPERSTICKER, "youtube_supersticker"),
    (TriggerType.DONATION, "donation"),
    (TriggerType.WEBHOOK, "webhook"),
    (TriggerType.OBS_SCENE_CHANGED, "obs_scene_changed"),
    (TriggerType.OBS_STREAM_STATE, "obs_stream_state"),
    (TriggerType.OBS_RECORD_STATE, "obs_record_state"),
    (TriggerType.OBS_INPUT_MUTE, "obs_input_mute"),
]

AMOUNT_OVER_5 = [TriggerCondition(field="amount", operator=Op.GREATER_THAN, value=5)]


@pytest.mark.parametrize("trigger_type,event_type", SIMPLE, ids=[s[1] for s in SIMPLE])
def test_simple_triggers(trigger_type, event_type):
    t = ct.create_trigger(trigger_type, "t", "n", conditions=list(AMOUNT_OVER_5))
    assert t.trigger_type == trigger_type
    assert t.should_trigger({"event_type": event_type, "amount": 10})
    assert not t.should_trigger({"event_type": event_type, "amount": 1})
    assert not t.should_trigger({"event_type": "something_else", "amount": 10})


def test_timer_trigger():
    t = ct.create_trigger(TriggerType.TIMER, "tid", "n", interval_seconds="0.4")
    assert t.interval_seconds == 1
    assert ct.create_trigger(TriggerType.TIMER, "tid", "n", interval_seconds="x").interval_seconds == 60
    assert ct.create_trigger(TriggerType.TIMER, "tid", "n", interval_seconds=None).interval_seconds == 60
    assert t.should_trigger({"event_type": "timer", "timer_id": "tid"})
    assert t.should_trigger({"event_type": "timer", "timer_id": "test"})
    assert t.should_trigger({"event_type": "timer"})
    assert not t.should_trigger({"event_type": "timer", "timer_id": "other"})
    assert not t.should_trigger({"event_type": "schedule"})
    t.trigger_id = ""
    assert t.should_trigger({"event_type": "timer", "timer_id": "other"})


def test_schedule_trigger():
    t = ct.create_trigger(TriggerType.SCHEDULE, "sid", "n", specific_time="10:30", days_of_week=["mon", "6"])
    assert t.days_of_week == [0, 6]
    assert t.should_trigger({"event_type": "schedule", "trigger_id": "sid"})
    assert t.should_trigger({"event_type": "schedule"})
    assert not t.should_trigger({"event_type": "schedule", "trigger_id": "other"})
    assert not t.should_trigger({"event_type": "timer"})


@pytest.mark.parametrize(
    "days,expected",
    [(None, []), ("", []), ("tues", [1]), (3, [3]), ([True, 2.0, "Sunday", " 4 ", "x", 9, -1, 2], [2, 4, 6]),
     (5.5, []), (("fri", "FRI"), [4])],
)
def test_normalize_days_of_week(days, expected):
    assert ct.normalize_days_of_week(days) == expected


@pytest.mark.parametrize(
    "specific,pattern,expected",
    [
        ("10:30", "", (10, 30)),
        ("", "15 8 * * *", (8, 15)),
        ("", "99 8 * * *", None),
        ("", "x 8 * * *", None),
        ("7:05 pm", "", (7, 5)),
        ("25:00", "", None),
        ("ab:cd", "", None),
        ("", "", None),
        ("noon", "5 6 * * *", (6, 5)),
        ("", "1 2 3:4 * *", (2, 1)),
    ],
)
def test_parse_schedule_hhmm(specific, pattern, expected):
    trig = SimpleNamespace(specific_time=specific, schedule_pattern=pattern)
    assert ct.parse_schedule_hhmm(trig) == expected


def test_parse_schedule_hhmm_none_values():
    assert ct.parse_schedule_hhmm(SimpleNamespace(specific_time=None, schedule_pattern=None)) is None
    assert ct.parse_schedule_hhmm(object()) is None


def test_schedule_is_due():
    trig = SimpleNamespace(specific_time="10:30", schedule_pattern="", days_of_week=[])
    monday_1030 = datetime(2026, 10, 26, 10, 30)
    assert ct.schedule_is_due(trig, monday_1030)
    assert not ct.schedule_is_due(trig, datetime(2026, 10, 26, 10, 31))
    assert not ct.schedule_is_due(trig, datetime(2026, 10, 26, 11, 30))
    trig.days_of_week = ["tue"]
    assert not ct.schedule_is_due(trig, monday_1030)
    trig.days_of_week = [0]
    assert ct.schedule_is_due(trig, monday_1030)
    assert not ct.schedule_is_due(SimpleNamespace(specific_time="", schedule_pattern=""), monday_1030)


def test_hotkey_trigger_parsing_and_combo():
    t = ct.create_trigger(TriggerType.HOTKEY, "h", "n", key_combination="Ctrl + Shift + F")
    assert t.modifiers == ["ctrl", "shift"] and t.key_code == "f"
    assert t.combo() == "ctrl+shift+f"
    built = ct.create_trigger(TriggerType.HOTKEY, "h", "n", modifiers=["alt"], key_code="f5")
    assert built.combo() == "alt+f5" and built.modifiers == ["alt"]
    blank = ct.create_trigger(TriggerType.HOTKEY, "h", "n", modifiers=None)
    assert blank.modifiers == [] and blank.combo() == ""
    blank.key_combination = ""
    blank.modifiers, blank.key_code = ["x"], "y"
    blank._parse_key_combination()
    assert blank.modifiers == [] and blank.key_code == ""
    keep = ct.create_trigger(TriggerType.HOTKEY, "h", "n", key_combination="ctrl+a", key_code="b")
    assert keep.key_code == "b"


def test_hotkey_trigger_should_trigger():
    t = ct.create_trigger(TriggerType.HOTKEY, "h", "n", key_combination="ctrl+shift+f")
    assert t.should_trigger({"event_type": "hotkey", "combo": "Shift+Ctrl+F"})
    assert t.should_trigger({"event_type": "hotkey", "modifiers": ["ctrl", "shift"], "key_code": "f"})
    assert not t.should_trigger({"event_type": "hotkey", "combo": "ctrl+f"})
    assert not t.should_trigger({"event_type": "webhook", "combo": "ctrl+shift+f"})
    assert not ct.create_trigger(TriggerType.HOTKEY, "h", "n").should_trigger({"event_type": "hotkey"})
    gated = ct.create_trigger(TriggerType.HOTKEY, "h", "n", key_combination="f1",
                              conditions=[TriggerCondition(field="x", operator=Op.EQUAL, value=1)])
    assert not gated.should_trigger({"event_type": "hotkey", "combo": "f1"})
    assert gated.should_trigger({"event_type": "hotkey", "combo": "f1", "x": 1})


def test_streamdeck_trigger():
    t = ct.create_trigger(TriggerType.STREAMDECK, "s", "n", connector_id="c1")
    assert t.should_trigger({"event_type": "streamdeck", "connector_id": "c1"})
    assert not t.should_trigger({"event_type": "streamdeck", "connector_id": "c2"})
    assert not t.should_trigger({"event_type": "hotkey", "connector_id": "c1"})
    assert not ct.create_trigger(TriggerType.STREAMDECK, "s", "n").should_trigger(
        {"event_type": "streamdeck", "connector_id": ""})


def test_any_trigger():
    t = ct.create_trigger(TriggerType.ANY, "a", "n")
    for event_type in ct.CONNECTOR_EVENT_TYPES:
        assert t.should_trigger({"event_type": event_type})
    assert not t.should_trigger({"event_type": "internal"})
    assert not t.should_trigger({"event_type": "timer", "internal_tick": True})


def test_create_trigger_every_type_and_unknown():
    for tt in TriggerType:
        t = ct.create_trigger(tt, "id", "nm", foreign_key=1)
        assert t.trigger_type == tt and t.trigger_id == "id" and not hasattr(t, "foreign_key")
    with pytest.raises(ValueError):
        ct.create_trigger("nope", "id", "n")

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Connector editor: action field schemas, edit-load/save round trips, validation and stylesheet."""

from __future__ import annotations

import json

import pytest

from modules import connector_variables as cv
from modules.connector_actions import create_action
from modules.connector_core import ActionType as A
from modules.connector_core import ComparisonOperator as O
from modules.connector_core import Connector, TriggerCondition
from modules.connector_core import TriggerType as T
from modules.connector_triggers import create_trigger
from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.uiwindows import connector_action_schemas as schemas
from modules.uiwindows import connector_editor as ce
from modules.uiwindows.connector_editor_styles import CONNECTOR_EDITOR_CSS, CONNECTOR_EDITOR_MENU_CSS


@pytest.fixture(autouse=True)
def _no_device_lookups(monkeypatch):
    """Audio device/app lists shell out on real systems."""
    monkeypatch.setattr(schemas, "_audio_device_options", lambda cfg: {"": "Default device", "spk": "Speakers"})
    monkeypatch.setattr(schemas, "_audio_app_options", lambda cfg: {"obs": "OBS"})
    for spec in schemas.ACTION_SPECS.values():
        for f in spec.fields:
            if f.key == "target_device":
                monkeypatch.setattr(f, "options", schemas._audio_device_options)
            elif f.key == "target_application":
                monkeypatch.setattr(f, "options", schemas._audio_app_options)


def _action(action_type, **kw):
    return create_action(action_type, "a1", "Action", **kw)


# -- schemas -----------------------------------------------------------------


def test_every_action_type_has_a_spec():
    assert set(schemas.ACTION_SPECS) == {a.value for a in A}


def test_apply_defaults_writes_visible_and_drops_hidden():
    spec = schemas.spec_for("key_press")
    cfg = schemas.apply_defaults(spec, {})
    assert cfg["input_type"] == "key" and cfg["action_mode"] == "press"
    assert "repeat_count" not in cfg and "macro_sequence" not in cfg
    cfg["action_mode"] = "repeat"
    schemas.apply_defaults(spec, cfg)
    assert cfg["repeat_count"] == 3 and cfg["repeat_interval"] == 0.1
    cfg["input_type"] = "macro"
    schemas.apply_defaults(spec, cfg)
    assert "repeat_count" not in cfg and "action_mode" not in cfg and cfg["macro_sequence"] == "[]"


def test_apply_defaults_resets_strict_selects_only():
    spec = schemas.spec_for("audio_control")
    cfg = {"control_type": "application", "action_mode": "increase", "target_application": "Gone.exe"}
    schemas.apply_defaults(spec, cfg)
    assert cfg["action_mode"] == "set"  # not offered for apps
    assert cfg["target_application"] == "Gone.exe"  # an app that isn't running stays selected


def test_json_fields_parse_on_save_and_keep_templates_as_text():
    kwargs = schemas.config_to_kwargs("api_call", {"method": "POST", "url": "https://x", "headers": '{"A": "1"}',
                                                   "body": '{"amount": {amount}}'})
    assert kwargs["headers"] == {"A": "1"}
    assert kwargs["body"] == '{"amount": {amount}}'
    assert "body" not in schemas.config_to_kwargs("api_call", {"method": "GET", "url": "https://x", "headers": "{}"})


def test_json_error_uses_example_values():
    ctx = cv.example_context("twitch_bits")
    assert schemas.json_error('{"amount": {amount}}', ctx) == ""
    assert "Not valid JSON" in schemas.json_error('{"amount": }', ctx)
    assert schemas.json_error("", ctx) == ""


def test_update_greeting_tristate():
    spec = schemas.spec_for("update_greeting")
    cfg = schemas.apply_defaults(spec, {"greeting_text": "hi"})
    assert schemas.config_to_kwargs("update_greeting", cfg)["greeting_enabled"] is None
    cfg["greeting_enabled"] = "off"
    assert schemas.config_to_kwargs("update_greeting", cfg)["greeting_enabled"] is False
    action = _action(A.UPDATE_GREETING, greeting_id="g", greeting_enabled=True)
    assert schemas.action_to_config(action)["greeting_enabled"] == "on"
    assert action.enabled is True


def test_trigger_alert_round_trip_and_legacy_type():
    cfg = {"alert_type": "point", "amount": "{amount}", "username": "", "message": "{message}", "reward_name": "Hydrate"}
    kwargs = schemas.config_to_kwargs("trigger_alert", cfg)
    assert kwargs == {"alert_type": "point", "alert_data": {"amount": "{amount}", "message": "{message}", "reward_name": "Hydrate"}}
    legacy = _action(A.TRIGGER_ALERT, alert_type="subscription", alert_data={"amount": "3"})
    assert schemas.action_to_config(legacy) == {"alert_type": "sub", "amount": "3"}


@pytest.mark.parametrize("action_type, kwargs", [
    (A.SEND_CHAT_MESSAGE, {"message": "hi {username}", "reply_targets": ["twitch", "youtube"]}),
    (A.SEND_ANNOUNCEMENT, {"message": "hey", "color": "green"}),
    (A.DISPLAY_BANNER, {"message": "b", "duration": 7, "permanent": False}),
    (A.EXECUTE_COMMAND, {"command": "echo {username}", "working_directory": "/tmp", "timeout_seconds": 12}),
    (A.WRITE_FILE, {"file_path": "x.txt", "content": "{time}", "append": False}),
    (A.KEY_PRESS, {"input_type": "key", "action_mode": "hold", "key_sequence": "ctrl+b", "hold_duration": 1.5}),
    (A.AUDIO_CONTROL, {"control_type": "device", "action_mode": "decrease", "volume_step": 15, "target_device": "spk"}),
    (A.API_CALL, {"method": "PUT", "url": "https://x", "headers": {"A": "1"}, "body": {"b": 2}}),
    (A.ADD_GREETING, {"username": "{username}", "user_id": "{user_id}", "greeting_text": "yo", "greeting_enabled": False}),
    (A.OBS_CONTROL, {"operation": "set_program_scene", "obs_arguments": {"scene_name": "Main"}}),
    (A.TEMPLATE_CONTROL, {"template_name": "roulette", "control_action": "spin", "control_data": {"speed": 3}}),
    (A.SEND_DISCORD_MESSAGE, {"message": "m", "channels": [{"guild_id": "g", "channel_id": "c"}]}),
])
def test_action_config_round_trip(action_type, kwargs):
    action = _action(action_type, **kwargs)
    cfg = schemas.action_to_config(action)
    spec = schemas.spec_for(action_type.value)
    if spec.fields and not spec.custom:
        schemas.apply_defaults(spec, cfg)
    rebuilt = _action(action_type, **schemas.config_to_kwargs(action_type.value, cfg))
    for key, value in kwargs.items():
        assert getattr(rebuilt, key) == value, key


def test_config_problems():
    assert schemas.config_problems("send_chat_message", {"message": " ", "reply_targets": []}) == [
        "Message is empty.", "Choose Twitch, YouTube or both."]
    assert schemas.config_problems("send_discord_message", {"message": "x", "channels": []}) == ["Add at least one Discord channel."]
    assert schemas.config_problems("key_press", {"input_type": "macro", "macro_sequence": '[{"type": "delay", "target": "1"}]'})
    assert schemas.config_problems(None, {}) == ["Choose what this action does."]


def test_summary_renders_example_values():
    render = lambda text: text.replace("{username}", "MossyMika")  # noqa: E731
    summary = schemas.summarize("send_chat_message", {"message": "Thanks {username}!", "reply_targets": ["twitch"]}, render)
    assert summary == "Twitch · “Thanks MossyMika!”"


# -- editor state ------------------------------------------------------------


def _bits_connector():
    conds = [TriggerCondition("amount", O.GREATER_THAN_OR_EQUAL, 1000), TriggerCondition("message", O.CONTAINS, "hype")]
    conds[1].case_sensitive = False
    or_cond = TriggerCondition("username", O.EQUAL, "SporeLord")
    or_cond.join = "or"
    conds.append(or_cond)
    return Connector(
        "c1", "Big cheer", "desc", enabled=False,
        trigger=create_trigger(T.TWITCH_BITS, "t1", "t", conditions=conds, cooldown_seconds=30),
        actions=[
            create_action(A.SEND_CHAT_MESSAGE, "a1", "x", message="Thanks {username}", reply_targets=["twitch"], delay_seconds=2),
            create_action(A.DISPLAY_BANNER, "a2", "x", message="hi", enabled=False, description="note"),
        ],
        created_at=123.0, trigger_count=42, last_triggered=99.0, metadata={"k": 1},
    )


def test_state_round_trip_keeps_hidden_fields():
    original = _bits_connector()
    state = ce.state_from_connector(original)
    assert [c["join"] for c in state.conditions] == ["and", "and", "or"]
    assert [c["ignore_case"] for c in state.conditions] == [False, True, False]
    rebuilt = ce.build_connector(state, original)
    assert (rebuilt.created_at, rebuilt.trigger_count, rebuilt.last_triggered, rebuilt.metadata) == (123.0, 42, 99.0, {"k": 1})
    assert rebuilt.enabled is False and rebuilt.trigger.cooldown_seconds == 30
    assert [a.action_id for a in rebuilt.actions] == ["a1", "a2"]
    assert rebuilt.actions[0].delay_seconds == 2 and rebuilt.actions[1].enabled is False and rebuilt.actions[1].description == "note"
    assert rebuilt.trigger.conditions[0].value == 1000
    assert rebuilt.trigger.conditions[2].join == "or"
    assert json.loads(ce.state_from_connector(rebuilt).snapshot()) == json.loads(state.snapshot())


def test_trigger_settings_round_trip():
    hotkey = Connector("h", "h", trigger=create_trigger(T.HOTKEY, "t", "t", key_combination="Ctrl + Shift + B"),
                       actions=[create_action(A.DISPLAY_BANNER, "a", "x", message="m")])
    state = ce.state_from_connector(hotkey)
    assert ce.build_connector(state, hotkey).trigger.key_combination == "ctrl+shift+b"

    sched = Connector("s", "s", trigger=create_trigger(T.SCHEDULE, "t", "t", specific_time="18:30", days_of_week=[4, 0]),
                      actions=[create_action(A.DISPLAY_BANNER, "a", "x", message="m")])
    built = ce.build_connector(ce.state_from_connector(sched), sched).trigger
    assert (built.specific_time, built.days_of_week) == ("18:30", [0, 4])

    state = ce.state_from_connector(None)
    state.name, state.trigger_type = "Hook", "webhook"
    state.trigger = {"secret_key": "abc"}
    state.actions = [{"type": "display_banner", "config": {"message": "x", "permanent": False, "duration": 5}}]
    built = ce.build_connector(state)
    assert built.trigger.secret_key == "abc" and built.connector_id == state.connector_id


def test_state_problems():
    state = ce.state_from_connector(None)
    assert ce.state_problems(state) == ["Name this connector.", "Choose a trigger.", "Add at least one action."]
    state.name, state.trigger_type = "x", "twitch_bits"
    state.conditions = [{"field": "amount", "operator": "greater_than", "value": "lots", "join": "and"}]
    state.actions = [{"type": "websocket_emit", "config": {"event_name": "e", "event_data": "{bad"}}]
    problems = ce.state_problems(state, cv.example_context("twitch_bits"))
    assert problems[0] == "Condition 1: enter a number."
    assert any(p.startswith("Action 1: Event data (JSON): Not valid JSON") for p in problems)
    state.trigger_type, state.conditions = "schedule", []
    assert "Set the time the schedule runs (HH:MM)." in ce.state_problems(state)


def test_ff7_variables_follow_hint_tags():
    keys = [v["key"] for v in ce._ff7_variables(("character", "hooks_gil"))]
    assert "random_character" in keys and "hooks.ff7.gil" in keys and "random_enemy" not in keys


def test_editor_stylesheets_are_well_formed():
    for css in (promote_color_importance(scope_selectors(CONNECTOR_EDITOR_CSS, ".cxe")), CONNECTOR_EDITOR_MENU_CSS):
        assert css.count("{") == css.count("}")
        assert "#" not in css.replace("#fff", "").replace("#9146ff", "").replace("#ff3b3b", "")

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""uiwindows.connector_action_schemas: Field / ActionSpec helpers, option providers and cache,
defaults, validation, config <-> kwargs conversion (round trips), summaries and listings."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import types

import pytest

import modules
from modules import connector_actions as ca
from modules.connector_core import ActionType
from modules.uiwindows import connector_action_schemas as S


def fake_module(monkeypatch, dotted, **attrs):
    mod = types.ModuleType(dotted)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, dotted, mod)
    parent_name, _, child = dotted.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        monkeypatch.setattr(parent, child, mod, raising=False)
    return mod


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(S, "_OPTION_CACHE", {})


# --------------------------------------------------------------------------- Field / helpers


def test_field_visible_and_option_map():
    f = S.Field("k", "K")
    assert f.visible({}) and f.option_map({}) == {}
    g = S.Field("k", "K", show=lambda cfg: cfg.get("x"), options=lambda cfg: {"a": cfg.get("x")})
    assert not g.visible({}) and g.visible({"x": 1})
    assert g.option_map({"x": "A"}) == {"a": "A"}
    h = S.Field("k", "K", options={"1": "one"})
    assert h.option_map({}) == {"1": "one"} and h.option_map({}) is not h.options


def test_is_and_not():
    assert S._is("m", "a", "b")({"m": "a"}) and not S._is("m", "a")({})
    assert S._not("m", "a")({"m": "c"}) and not S._not("m", "a")({"m": "a"})


def test_alert_type_options(monkeypatch):
    opts = S._alert_type_options({})
    assert opts["bit"] and "point" in opts
    fake_module(monkeypatch, "modules.connector_variables")  # no ALERT_TYPES -> fallback
    assert S._alert_type_options({})["giftsub"] == "Gift sub"


def test_cached_options(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(S.time, "monotonic", lambda: clock["t"])
    calls = []

    def load():
        calls.append(1)
        return {"a": "A"}

    first = S._cached_options("x", load)
    first["mutated"] = "yes"
    assert S._cached_options("x", load) == {"a": "A"} and len(calls) == 1
    clock["t"] += S._OPTION_TTL + 1
    assert S._cached_options("x", load) == {"a": "A"} and len(calls) == 2

    def boom():
        raise RuntimeError("shell failed")

    assert S._cached_options("y", boom) == {}
    assert S._cached_options("z", lambda: None) == {}


def test_audio_options(monkeypatch):
    fake_module(monkeypatch, "modules.audio_targets",
                get_available_audio_devices=lambda: {"dev1": "Speakers"},
                get_available_applications=lambda: {"game.exe": "Game"})
    assert S._audio_device_options({}) == {"dev1": "Speakers"}
    assert S._audio_app_options({}) == {"game.exe": "Game"}
    S._OPTION_CACHE.clear()
    fake_module(monkeypatch, "modules.audio_targets",
                get_available_audio_devices=lambda: {}, get_available_applications=lambda: {})
    assert S._audio_device_options({}) == {"": "Default device"}
    assert S._audio_app_options({}) == {}


def test_audio_mode_options():
    assert "increase" not in S._audio_mode_options({"control_type": "application"})
    assert "set_random" in S._audio_mode_options({"control_type": "device"})


# --------------------------------------------------------------------------- spec lookups


def test_specs_cover_action_types():
    assert set(S.ACTION_SPECS) == {t.value for t in ActionType}
    assert S.spec_for(None) is None and S.spec_for("send_chat_message").label == "Send chat message"
    assert S.action_type_options()["api_call"] == "Web request"
    groups = S.grouped_specs()
    assert [g[0] for g in groups] == [g for g, _ in S.ACTION_GROUPS]
    assert all(spec.group == g for g, _, specs in groups for spec in specs)
    assert "message" in S.variable_fields("send_chat_message") and S.variable_fields("nope") == []


def test_grouped_specs_skips_empty_group(monkeypatch):
    monkeypatch.setattr(S, "ACTION_GROUPS", S.ACTION_GROUPS + (("Empty", "x"),))
    assert "Empty" not in [g[0] for g in S.grouped_specs()]


def test_visible_fields_dedupes_keys():
    spec = S.spec_for("key_press")
    keys = [f.key for f in S.visible_fields(spec, {"input_type": "mouse", "action_mode": "repeat"})]
    assert keys.count("key_sequence") == 1 and "repeat_count" in keys and "hold_duration" not in keys
    mouse_field = next(f for f in S.visible_fields(spec, {"input_type": "mouse"}) if f.key == "key_sequence")
    assert mouse_field.kind == "select"


# --------------------------------------------------------------------------- defaults


def test_apply_defaults_custom_without_fields_untouched():
    cfg = {"x": 1}
    assert S.apply_defaults(S.spec_for("obs_control"), cfg) is cfg and cfg == {"x": 1}


def test_apply_defaults_chain_and_hidden_keys():
    cfg = {"hold_duration": 2, "macro_sequence": "[]"}
    S.apply_defaults(S.spec_for("key_press"), cfg)
    assert cfg["input_type"] == "key" and cfg["action_mode"] == "press"
    assert "hold_duration" not in cfg and "macro_sequence" not in cfg and cfg["key_sequence"] == ""
    targets = S.apply_defaults(S.spec_for("send_chat_message"), {"reply_targets": None})["reply_targets"]
    assert targets == ["twitch"] and targets is not S.spec_for("send_chat_message").fields[1].default


def test_apply_defaults_select_rules(monkeypatch):
    cfg = S.apply_defaults(S.spec_for("send_announcement"), {"color": "rainbow"})
    assert cfg["color"] == "primary"
    cfg = S.apply_defaults(S.spec_for("key_press"), {"input_type": "mouse", "key_sequence": "ctrl+c"})
    assert cfg["key_sequence"] == "left_click"
    spec = S.ActionSpec("x", "X", "G", "i", fields=[S.Field("s", "S", "select", default="zz", options={"a": "A"})])
    assert S.apply_defaults(spec, {})["s"] == "a"
    fake_module(monkeypatch, "modules.audio_targets",
                get_available_audio_devices=lambda: {"d1": "Dev"}, get_available_applications=lambda: {})
    cfg = S.apply_defaults(S.spec_for("audio_control"), {"control_type": "device", "target_device": "unplugged"})
    assert cfg["target_device"] == "unplugged"  # non-strict keeps a stored device
    S._OPTION_CACHE.clear()
    cfg = S.apply_defaults(S.spec_for("audio_control"), {"target_device": ""})
    assert cfg["target_device"] == "d1"


def test_apply_defaults_custom_spec_keeps_hidden_keys():
    spec = S.ActionSpec("c", "C", "G", "i", custom=True,
                        fields=[S.Field("a", "A", show=lambda cfg: False)])
    assert S.apply_defaults(spec, {"a": 1}) == {"a": 1}


# --------------------------------------------------------------------------- numbers / json


def test_coerce_number():
    f = S.Field("n", "N", "number", default=5, min=1, max=10, integer=True)
    assert S._coerce_number(f, "7.6") == 8 and S._coerce_number(f, "x") == 5
    assert S._coerce_number(f, -3) == 1 and S._coerce_number(f, 99) == 10
    g = S.Field("n", "N", "number", default=None)
    assert S._coerce_number(g, None) == 0.0 and S._coerce_number(g, "2.5") == 2.5


def test_json_error(monkeypatch):
    assert S.json_error(None) == "" and S.json_error("  ") == "" and S.json_error('{"a": 1}') == ""
    assert S.json_error("{bad").startswith("Not valid JSON (") and "line 1" in S.json_error("{bad")
    assert S.json_error('{"a": {amount}}', {"amount": 5}) == ""
    import modules.connector_placeholders as cp

    def boom(text, ctx):
        raise RuntimeError("x")

    monkeypatch.setattr(cp, "substitute_connector_placeholders", boom)
    assert S.json_error('{"a": {amount}}', {"amount": 5}).startswith("Not valid JSON")


def test_parse_json_field_and_json_text():
    assert S._parse_json_field({"a": 1}) == {"a": 1} and S._parse_json_field([1]) == [1]
    assert S._parse_json_field("") == {} and S._parse_json_field(None) == {}
    assert S._parse_json_field('{"a": 1}') == {"a": 1}
    assert S._parse_json_field('{"a": {amount}}') == '{"a": {amount}}'
    assert S._json_text(None) == "" and S._json_text({}) == "{}"
    assert S._json_text({"a": 1}) == '{\n  "a": 1\n}' and S._json_text("raw") == "raw"


# --------------------------------------------------------------------------- problems


def test_config_problems():
    assert S.config_problems("nope", {}) == ["Choose what this action does."]
    assert S.config_problems("send_chat_message", {"message": " ", "reply_targets": []}) == [
        "Message is empty.", "Choose Twitch, YouTube or both."]
    assert S.config_problems("send_chat_message", {"message": "hi", "reply_targets": ["twitch"]}) == []
    assert "Add at least one Discord channel." in S.config_problems("send_discord_message", {"message": "x"})
    assert S.config_problems("template_control", {"template_name": "t"}) == ["Choose an overlay and what it should do."]
    assert S.config_problems("obs_control", {}) == ["Choose an OBS action."]
    assert S.config_problems("game_hook", {}) == ["Choose a game hook operation."]
    assert S.config_problems("update_greeting", {"greeting_id": None}) == ["Greeting ID is empty."]


@pytest.mark.parametrize(
    "seq,ok",
    [("[]", False), ("not json", False), (None, False),
     ('[{"type": "delay", "target": "1"}, "junk"]', False),
     ('[{"type": "key", "target": " "}]', False), ('[{"type": "key", "target": "a"}]', True)],
)
def test_config_problems_macro(seq, ok):
    problems = S.config_problems("key_press", {"input_type": "macro", "macro_sequence": seq})
    assert (problems == []) is ok


# --------------------------------------------------------------------------- config_to_kwargs


def test_kwargs_template_control():
    assert S.config_to_kwargs("template_control", {"template_name": "wheel", "control_action": "spin",
                                                   "speed": 3, "_ui": 1}) == {
        "template_name": "wheel", "control_action": "spin", "control_data": {"speed": 3}}
    assert S.config_to_kwargs("template_control", None)["template_name"] == ""


def test_kwargs_game_hook(monkeypatch):
    fake_module(monkeypatch, "modules.game_hooks.ff7_hook",
                ff7_connector_config_to_hook_kwargs=lambda cfg: {"from": "hook"})
    assert S.config_to_kwargs("game_hook", {"operation": "add_gil"}) == {
        "game_id": "ff7", "operation": "add_gil", "hook_arguments": {"from": "hook"}}

    def boom(cfg):
        raise RuntimeError("x")

    fake_module(monkeypatch, "modules.game_hooks.ff7_hook", ff7_connector_config_to_hook_kwargs=boom)
    assert S.config_to_kwargs("game_hook", {"game_id": "", "arg_amount": 5, "other": 1}) == {
        "game_id": "ff7", "operation": "", "hook_arguments": {"amount": 5}}


def test_kwargs_obs_discord_alert():
    assert S.config_to_kwargs("obs_control", {"operation": "set_scene", "arg_scene": "Main"}) == {
        "operation": "set_scene", "obs_arguments": {"scene": "Main"}}
    assert S.config_to_kwargs("send_discord_message", {"message": None, "channels": ({"c": 1},)}) == {
        "message": "", "channels": [{"c": 1}]}
    assert S.config_to_kwargs("trigger_alert", {"alert_type": "point", "amount": " ", "username": "{username}",
                                                "reward_name": "Hydrate", "reward_id": ""}) == {
        "alert_type": "point", "alert_data": {"username": "{username}", "reward_name": "Hydrate"}}
    assert S.config_to_kwargs("trigger_alert", {"reward_name": "x"}) == {"alert_type": "bit", "alert_data": {}}


def test_kwargs_generic_fields():
    kw = S.config_to_kwargs("display_banner", {"message": " Hi ", "permanent": 0, "duration": "0"})
    assert kw == {"message": " Hi ", "permanent": False, "duration": 1}
    kw = S.config_to_kwargs("api_call", {"method": "GET", "url": " http://x ", "headers": '{"A": "1"}', "body": "x"})
    assert kw == {"method": "GET", "url": "http://x", "headers": {"A": "1"}}
    kw = S.config_to_kwargs("send_chat_message", {"message": " keep spaces ", "reply_targets": ["youtube"]})
    assert kw["message"] == " keep spaces "
    kw = S.config_to_kwargs("execute_command", {"command": "ls", "timeout_seconds": "2.4"})
    assert kw["timeout_seconds"] == 2 and kw["working_directory"] == ""
    assert S.config_to_kwargs("unknown", {"a": 1}) == {}


@pytest.mark.parametrize("choice,expected", [("on", True), ("off", False), ("", None)])
def test_kwargs_update_greeting(choice, expected):
    kw = S.config_to_kwargs("update_greeting", {"greeting_id": "g", "greeting_enabled": choice})
    assert kw["greeting_enabled"] is expected


# --------------------------------------------------------------------------- action_to_config


def test_action_to_config_round_trips():
    for action_type, cfg in [
        ("send_chat_message", {"message": "hi {username}", "reply_targets": ["twitch", "youtube"]}),
        ("api_call", {"method": "POST", "url": "http://x", "headers": "{}", "body": '{"a": {amount}}'}),
        ("websocket_emit", {"event_name": "ev", "event_data": '{\n  "a": 1\n}'}),
        ("update_greeting", {"greeting_id": "g", "greeting_text": "t", "greeting_enabled": "off"}),
        ("obs_control", {"operation": "set_scene", "arg_scene": "Main", "arg_flag": True}),
        ("template_control", {"template_name": "wheel", "control_action": "spin", "speed": 2}),
    ]:
        action = ca.create_action(ActionType(action_type), "a", "n", **S.config_to_kwargs(action_type, cfg))
        back = S.action_to_config(action)
        for k, v in cfg.items():
            assert back[k] == v, (action_type, k)


def test_action_to_config_game_hook_and_trigger_alert(monkeypatch):
    hook = ca.create_action(ActionType.GAME_HOOK, "a", "n", game_id="", operation="op",
                            hook_arguments={"amount": 5, "name": ["x"]})
    assert S.action_to_config(hook) == {"operation": "op", "game_id": "ff7", "arg_amount": 5, "arg_name": "['x']"}
    alert = ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="tips",
                             alert_data={"amount": "5", "reward_id": "r", "other": 1})
    assert S.action_to_config(alert) == {"alert_type": "donation", "amount": "5", "reward_id": "r"}

    def boom(v):
        raise RuntimeError("x")

    monkeypatch.setattr(ca, "normalize_trigger_alert_type", boom)
    alert.alert_type = ""
    assert S.action_to_config(alert)["alert_type"] == "bit"


def test_action_to_config_misc():
    tpl = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n", control_data={"enabled": True, "x": 1})
    assert S.action_to_config(tpl) == {"template_name": "", "control_action": "", "x": 1}
    ug = ca.create_action(ActionType.UPDATE_GREETING, "a", "n", greeting_id="g", greeting_enabled=True)
    assert S.action_to_config(ug)["greeting_enabled"] == "on"
    ug.greeting_enabled = None
    assert S.action_to_config(ug)["greeting_enabled"] == ""
    api = ca.create_action(ActionType.API_CALL, "a", "n", url="u")
    api.body = None
    assert S.action_to_config(api)["body"] == ""
    ex = ca.create_action(ActionType.EXECUTE_COMMAND, "a", "n", command="ls")
    assert S.action_to_config(ex)["timeout_seconds"] == 30
    assert S.action_to_config(object()) == {}

    class Legacy:
        pass

    import dataclasses

    @dataclasses.dataclass
    class OldCommand:
        action_type: object
        timeout: int = 9
        _private: int = 0
        name: str = "n"

    assert S.action_to_config(OldCommand(ActionType.EXECUTE_COMMAND)) == {"timeout_seconds": 9}


# --------------------------------------------------------------------------- summaries


def test_quote():
    assert S._quote("") == "" and S._quote("  a   b ") == "“a b”"
    assert S._quote("x" * 80, 10) == "“" + "x" * 9 + "…”"


def test_summaries():
    r = lambda s: s.replace("{username}", "Bob")  # noqa: E731
    sm = S.summarize
    assert sm("nope", {}) == "Choose what this action does"
    assert sm("send_chat_message", {"reply_targets": ["twitch", "youtube"], "message": "hi {username}"}, r) == \
        "Twitch + YouTube · “hi Bob”"
    assert sm("send_chat_message", None) == ""
    assert sm("send_announcement", {"message": "Hey"}) == "“Hey”"
    assert sm("display_banner", {"message": "B", "permanent": True}) == "“B” · until replaced"
    assert sm("display_banner", {"duration": 4}) == "4 s"
    assert sm("add_greeting", {"greeting_text": "G"}) == "“G”"
    assert sm("update_greeting", {}) == ""
    assert sm("send_discord_message", {"channels": [{"channel_name": "general"}, {"channel_id": "123"}, "x"],
                                       "message": "m"}) == "#general, #123 · “m”"
    assert sm("send_greeting", {}, r) == "Greeting for Bob"
    assert sm("trigger_alert", {"alert_type": "bit", "amount": "5"}, labels={"bit": "Bits"}) == "Bits alert · amount 5"
    assert sm("trigger_alert", {"alert_type": "follow"}) == "follow alert"
    assert sm("template_control", {"template_name": "wheel", "control_action": "spin_now"}) == "Wheel → spin now"
    assert sm("template_control", {"template_name": "wheel"}) == "Choose an overlay"
    assert sm("websocket_emit", {"event_name": "ev"}) == "ev" and sm("websocket_emit", {}) == "Choose an event name"
    assert sm("obs_control", {"operation": "set_scene", "arg_x": " ", "arg_scene": "Main"},
              labels={"set_scene": "Switch scene"}) == "Switch scene · “Main”"
    assert sm("obs_control", {}) == "Choose an OBS action"
    assert sm("game_hook", {"operation": "op"}, labels={"op": "Add gil"}) == "Add gil"
    assert sm("game_hook", {}) == "Choose an operation"


def test_summaries_input_audio_files():
    sm = S.summarize
    assert sm("key_press", {"input_type": "macro", "macro_sequence": "[1]"}) == "Macro · 1 step"
    assert sm("key_press", {"input_type": "macro", "macro_sequence": "[1, 2]"}) == "Macro · 2 steps"
    assert sm("key_press", {"input_type": "macro", "macro_sequence": "bad"}) == "Macro · 0 steps"
    assert sm("key_press", {"input_type": "mouse", "key_sequence": "right_click", "action_mode": "hold"}) == \
        "Right click hold"
    assert sm("key_press", {"input_type": "key", "key_sequence": "f1", "action_mode": "repeat", "repeat_count": 4}) \
        == "f1 ×4"
    assert sm("key_press", {}) == "No keys yet"
    assert sm("audio_control", {"control_type": "application", "target_application": "game.exe",
                                "action_mode": "mute"}) == "game.exe · mute"
    assert sm("audio_control", {}) == "Default device · set to 50%"
    assert sm("audio_control", {"action_mode": "increase"}) == "Default device · up 10%"
    assert sm("audio_control", {"action_mode": "weird"}) == "Default device · weird"
    assert sm("api_call", {"url": "http://x"}) == "POST http://x"
    assert sm("write_file", {"append": True, "file_path": "f.txt"}) == "Append to f.txt"
    assert sm("write_file", {}) == "Write"
    assert sm("execute_command", {"command": "ls -la"}) == "“ls -la”"


def test_summary_falls_back_to_label(monkeypatch):
    monkeypatch.setitem(S.ACTION_SPECS, "brand_new", S.ActionSpec("brand_new", "Brand new", "Games", "x"))
    assert S.summarize("brand_new", {}) == "Brand new"

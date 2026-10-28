# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/behavior_blocks.py (binding -> JS compiler and CSS selection)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.spore_studio import behavior_blocks as bb


def _balanced(js: str) -> bool:
    """Brackets balance outside string literals (a cheap syntax sanity check)."""
    stripped = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', '""', js)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in stripped:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


# ---------------------------------------------------------------- small helpers

@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("2.5", 2.5), (3, 3.0), ("x", None),
                                            ([1], None)])
def test_coerce_float_opt(value, expected):
    assert bb._coerce_float_opt(value) == expected


def test_inert_strings_only_touches_string_literals():
    js = 'var a = {"t": "{{x}} </script>", "b": 1};'
    out = bb._inert_strings(js)
    assert out == 'var a = {"t": "\\u007b\\u007bx}} \\u003c/script>", "b": 1};'


def test_js_string_and_value():
    assert bb._js_string(None) == '""'
    assert bb._js_string(5) == '"5"'
    assert bb._js_string('say "hi" {x}') == '"say \\"hi\\" \\u007bx}"'
    assert bb._js_value({"a": [1, None]}) == '{"a": [1, null]}'
    obj = object()
    assert bb._js_value(obj) == bb._js_string(obj)


def test_compile_filter():
    assert bb._compile_filter(None) == "true"
    assert bb._compile_filter({"a": "", "b": None}) == "true"
    assert bb._compile_filter({"alert_type": "sub", "x": ""}) == 'sporeMatchesFilter(payload, {"alert_type": "sub"})'


def test_twitch_helpers():
    assert bb._safe_element_token("a-b c") == "a_b_c"
    assert bb._safe_element_token(None) == "el"
    assert bb._safe_element_token("") == "el"
    assert bb._twitch_request_id("box", "3") == "spore_tw_box_3"
    assert bb._coerce_twitch_filter_cell(None) is None
    assert bb._coerce_twitch_filter_cell(5) == 5
    assert bb._coerce_twitch_filter_cell("  ") is None
    assert bb._coerce_twitch_filter_cell(" 42 ") == 42
    assert bb._coerce_twitch_filter_cell('{"a": 1}') == {"a": 1}
    assert bb._coerce_twitch_filter_cell("plain text") == "plain text"


def test_twitch_response_filter_dict():
    binding = {"twitch_filters": ["junk", {"key": ""}, {"key": "requestId", "value": "x"},
                                  {"key": "status", "value": "200"}, {"key": "skip", "value": ""},
                                  {"key": " name ", "value": "bob"}]}
    assert bb._twitch_response_filter_dict(binding, "rid") == {"requestId": "rid", "status": 200, "name": "bob"}
    assert bb._twitch_response_filter_dict({}, "rid") == {"requestId": "rid"}


def test_twitch_emit_iife_blank_endpoint():
    out = bb._compile_twitch_emit_iife({"twitch_api": "bad"}, "rid")
    assert "Twitch API binding skipped" in out and "socket.emit" not in out


def test_twitch_emit_iife_full():
    out = bb._compile_twitch_emit_iife({"twitch_api": {
        "endpoint": "https://api.twitch.tv/helix/users", "method": " post ",
        "params_json": '{"login": "x"}', "body_json": '{"a": 1}'}}, "rid")
    payload = json.loads(out.split("var __payload = ")[1].split(";\n")[0])
    assert payload == {"endpoint": "https://api.twitch.tv/helix/users", "method": "POST", "requestId": "rid",
                       "params": {"login": "x"}, "json_data": {"a": 1}}
    assert "socket.once('connect', __go)" in out and _balanced(out)


def test_twitch_emit_iife_default_method_and_blank_json():
    out = bb._compile_twitch_emit_iife({"twitch_api": {"endpoint": "e", "method": "  ", "params_json": "  ",
                                                       "body_json": None}}, "rid")
    payload = json.loads(out.split("var __payload = ")[1].split(";\n")[0])
    assert payload == {"endpoint": "e", "method": "GET", "requestId": "rid"}


@pytest.mark.parametrize("params,body,expect", [
    ("{bad", None, "params JSON:"),
    ("[1]", None, "params JSON must be an object"),
    (None, "{bad", "body JSON:"),
    (None, "5", "body JSON must be an object"),
    ("[1]", "[2]", "params JSON must be an object `{...}`; body JSON must be an object"),
])
def test_twitch_emit_iife_json_errors(params, body, expect):
    out = bb._compile_twitch_emit_iife({"twitch_api": {"endpoint": "e", "params_json": params,
                                                       "body_json": body}}, "rid")
    assert "Twitch API emit" in out and "socket.emit" not in out
    msg = json.loads(out.split("' + ")[1].split("); }")[0].replace("\\u007b", "{").replace("\\u003c", "<"))
    assert msg.startswith(expect)


# ---------------------------------------------------------------- _compile_action

def test_simple_actions():
    assert bb._compile_action("e", "show", {}) == ['sporeShow("e", null);']
    assert bb._compile_action("e", "hide", None) == ['sporeHide("e", null);']
    assert bb._compile_action("e", "toggle", {}) == ['sporeToggle("e");']
    assert bb._compile_action("e", "timer_start", {})[0].endswith('sporeTimerStart("e"); }')
    assert bb._compile_action("e", "timer_pause", {})[0].endswith('sporeTimerPause("e"); }')
    assert bb._compile_action("e", "timer_reset", {})[0].endswith('sporeTimerReset("e"); }')
    assert bb._compile_action("e", "list_clear", {}) == [
        "if (typeof sporeListClear === 'function') { sporeListClear(\"e\"); }"]
    assert bb._compile_action("e", "nope", {}) == ["// Unknown action: nope"]


def test_show_for_variants():
    out = "\n".join(bb._compile_action("e", "show_for", {"seconds": "2.5", "anim_in": "slideIn",
                                                         "anim_out": "none"}))
    assert 'sporeShow("e", {"animIn": "slideIn"});' in out and "}, 2500.0);" in out
    out = "\n".join(bb._compile_action("e", "show_for", {"seconds": "x", "anim_in": "", "anim_out": None}))
    assert 'sporeShow("e", null);' in out and "}, 5000.0);" in out
    out = "\n".join(bb._compile_action("e", "show_for", {"seconds": -4}))
    assert '{"animIn": "fade", "animOut": "fade"}' in out and "}, 0.0);" in out
    assert _balanced(out)


@pytest.mark.xfail(strict=True, reason="BUG: show_for with non-finite seconds writes `inf` into the overlay JS "
                                       "(ReferenceError); timing.py already treats it as 5 s")
def test_show_for_infinite_seconds_is_valid_js():
    out = "\n".join(bb._compile_action("e", "show_for", {"seconds": "inf"}))
    assert not re.search(r"\binf\b", out)


def test_set_text_and_images():
    assert bb._compile_action("e", "set_text", {"from_payload": "username"}) == [
        'sporeSetText("e", payload && payload["username"]);']
    assert bb._compile_action("e", "set_text", {"literal": "{{x}}"}) == ['sporeSetText("e", "\\u007b\\u007bx}}");']
    img = "\n".join(bb._compile_action("e", "set_image", {"from_payload": "avatar"}))
    assert 'var __src = payload && payload["avatar"];' in img and _balanced(img)
    img = "\n".join(bb._compile_action("e", "set_image", {"literal": "/a.png"}))
    assert 'var __src = "/a.png";' in img
    vis = "\n".join(bb._compile_action("e", "set_visual_src", {"from_payload": "url"}))
    assert 'payload["url"]' in vis and "__vd.load()" in vis and _balanced(vis)
    vis = "\n".join(bb._compile_action("e", "set_visual_src", {}))
    assert 'var __src = "";' in vis


def test_play_audio():
    assert bb._compile_action("e", "play_audio", {}) == ['sporePlayMediaAudio("e", {});']
    assert bb._compile_action("e", "play_audio", {"volume": 0.5, "fade_in_ms": 200}) == [
        'sporePlayMediaAudio("e", {"volume": 0.5, "fade_in_ms": 200});']
    assert bb._compile_action("e", "play_audio", {"volume": "", "fade_in_ms": None}) == [
        'sporePlayMediaAudio("e", {});']


def test_randomize_position():
    out = "\n".join(bb._compile_action("e", "randomize_position", {"x_min": "10", "x_max": "", "y_min": "bad",
                                                                   "y_max": 50}))
    assert "var __xMn = 10.0;" in out and "var __xMxCfg = -1.0;" in out
    assert "var __yMn = 0.0;" in out and "var __yMxCfg = 50.0;" in out and _balanced(out)
    out = "\n".join(bb._compile_action("e", "randomize_position", {"x_max": "bad"}))
    assert "var __xMxCfg = -1.0;" in out


@pytest.mark.parametrize("args,expected", [
    ({"rotate_deg": 45, "scale": "2", "translate_x": 1, "translate_y": "2"},
     "translate(1.0px, 2.0px) rotate(45.0deg) scale(2.0)"),
    ({"translate_x": 5}, "translateX(5.0px)"),
    ({"translate_y": 6}, "translateY(6.0px)"),
    ({"rotate_deg": "x", "scale": "y", "translate_x": "z"}, ""),
    ({}, ""),
])
def test_set_transform(args, expected):
    out = "\n".join(bb._compile_action("e", "set_transform", args))
    assert f"var __tf = {json.dumps(expected)};" in out


def test_transform_jitter():
    assert bb._compile_action("e", "transform_jitter", {}) == [
        "// transform_jitter: set rotate_range, translate_range, and/or scale_min/max"]
    assert bb._compile_action("e", "transform_jitter", {"rotate_range": "x", "translate_range": "y"})[0].startswith("//")
    out = "\n".join(bb._compile_action("e", "transform_jitter", {"rotate_range": -10, "translate_range": "3"}))
    assert "var __rr = 10.0;" in out and "var __tr = 3.0;" in out and "__sl" not in out and _balanced(out)
    out = "\n".join(bb._compile_action("e", "transform_jitter", {"scale_min": 2, "scale_max": "0.5"}))
    assert "var __sl = 0.5;" in out and "var __sh = 2.0;" in out and "var __rr = 0.0;" in out


def test_flash_class():
    out = "\n".join(bb._compile_action("e", "flash_class", {"class_name": " sporePop ", "duration_ms": "99999"}))
    assert 'var __c = "sporePop";' in out and "}, 60000);" in out and _balanced(out)
    out = "\n".join(bb._compile_action("e", "flash_class", {"duration_ms": "bad"}))
    assert "}, 500);" in out and 'var __c = "";' in out
    assert "}, 0);" in "\n".join(bb._compile_action("e", "flash_class", {"duration_ms": -5}))


def _delta(args):
    line = bb._compile_action("e", "counter_adjust", args)[1]
    return json.loads(line.split(", ", 2)[2].rsplit(", payload);", 1)[0])


def test_counter_adjust_variants():
    lines = bb._compile_action("e", "counter_adjust", {"counter_id": " c ", "operation": "set"})
    assert lines[0] == "window.__sporeLastPayload.event = payload || {};"
    assert lines[1].startswith('sporeCounterAdjust("c", "set", ')
    assert _delta({}) == {"kind": "fixed", "value": 1.0}
    assert _delta({"delta_value": "bad"}) == {"kind": "fixed", "value": 1}
    assert _delta({"delta_kind": "random_int", "delta_min": "2", "delta_max": "x"}) == {
        "kind": "random_int", "min": 2.0, "max": 5}
    assert _delta({"delta_kind": "random_float", "delta_min": "x", "delta_max": 9, "delta_decimals": "y"}) == {
        "kind": "random_float", "min": 1, "max": 9.0, "decimals": 0}
    assert _delta({"delta_kind": "random_float", "delta_decimals": "2"})["decimals"] == 2
    assert _delta({"delta_kind": "data_source", "delta_source": "alert.amount", "delta_value": "x"}) == {
        "kind": "data_source", "source": "alert.amount", "fallback": 1}
    assert _delta({"delta_kind": "data_source", "delta_value": "3"})["fallback"] == 3.0
    assert _delta({"delta_kind": "payload"}) == {"kind": "payload"}


def test_list_and_wheel_actions():
    lines = bb._compile_action("e", "list_add", {"text": "hi"})
    assert lines[1] == "if (typeof sporeListAdd === 'function') { sporeListAdd(\"e\", payload, \"hi\"); }"
    lines = bb._compile_action("e", "wheel_spin", {})
    assert lines[1] == "if (typeof sporeWheelSpin === 'function') { sporeWheelSpin(\"e\", payload); }"


# ---------------------------------------------------------------- steps / sequences

def test_coerce_delay_ms():
    assert bb._coerce_delay_ms("100") == 100.0
    assert bb._coerce_delay_ms(-5) == 0.0
    assert bb._coerce_delay_ms("x") == 0.0


def test_binding_action_steps():
    assert bb._binding_action_steps({}) == []
    assert bb._binding_action_steps({"action": 5}) == []
    steps = bb._binding_action_steps({"action": "show", "args": "bad", "chain": [
        "junk", {"action": ""}, {"action": "hide", "args": {"a": 1}, "delay_ms": "250"},
        {"action": "toggle", "args": "bad"}]})
    assert steps == [("show", {}, 0.0), ("hide", {"a": 1}, 250.0), ("toggle", {}, 0.0)]


@pytest.mark.parametrize("action,args,expected", [
    ("show_for", {}, True), ("show_for", {"anim_in": "none", "anim_out": ""}, False),
    ("show_for", {"anim_in": "none", "anim_out": "fade"}, True),
    ("show", {}, False), ("show", {"anim_in": "fade"}, True), ("show", {"anim_out": "none"}, False),
    ("show", None, False)])
def test_step_uses_animation(action, args, expected):
    assert bb._step_uses_animation(action, args) is expected


def test_compile_action_sequence():
    assert bb._compile_action_sequence("e", []) == []
    assert bb._compile_action_sequence("e", [("show", {}, 0.0)]) == ['sporeShow("e", null);']
    out = bb._compile_action_sequence("e", [("show", {}, 0.0), ("hide", {}, 100.4), ("toggle", {}, 2.6)])
    assert out == [
        'sporeShow("e", null);',
        "setTimeout(function () {",
        '    sporeHide("e", null);',
        "    setTimeout(function () {",
        '        sporeToggle("e");',
        "    }, 3);",
        "}, 100);",
    ]


# ---------------------------------------------------------------- CSS need detectors

def test_need_detectors():
    assert bb._bindings_need_toggle(["junk", {"bindings": ["x", {"action": "show"}]}]) is False
    assert bb._bindings_need_toggle([{"bindings": [{"action": "show", "chain": [{"action": "toggle"}]}]}]) is True
    assert bb._element_uses_animations({"animations": "x"}) is False
    assert bb._element_uses_animations({"animations": {"anim_in": " none "}}) is False
    assert bb._element_uses_animations({"animations": {"anim_out": "fade"}}) is True
    img = {"type": "image", "src_mode": "from_counter", "counter_image_transition": {"enabled": True, "type": "fade"}}
    assert bb._elements_need_counter_image_transition_css(["x", {"type": "text"}, {"type": "image"}, img]) is True
    assert bb._elements_need_counter_image_transition_css(
        [dict(img, counter_image_transition={"enabled": True, "type": "none"})]) is False
    assert bb._elements_need_counter_image_transition_css(
        [dict(img, counter_image_transition={"enabled": False})]) is False
    va = {"value_animation": {"enabled": True}}
    assert bb._elements_need_value_change_css(["x", dict(va, type="image")]) is False
    assert bb._elements_need_value_change_css([dict(va, type="text", text_mode="counter")]) is True
    assert bb._elements_need_value_change_css([{"type": "counter", "value_animation": {"enabled": False}}]) is False
    assert bb._elements_need_widget_css(["x", {"type": "Wheel"}]) is True
    assert bb._elements_need_widget_css([{"type": "text"}]) is False
    assert bb._elements_need_progress_bar_css(["x", {"type": "progress_bar"}]) is True
    assert bb._elements_need_progress_bar_css([{"type": "text"}]) is False
    assert bb._elements_need_marquee_css(["x", {"type": "marquee"}]) is True
    assert bb._elements_need_marquee_css([{"type": "text"}]) is False
    assert bb._bindings_need_preset_css(["x", {"animations": {"anim_in": "fade"}}]) is True
    assert bb._bindings_need_preset_css([{"bindings": ["x", {"action": "show", "chain": [
        {"action": "flash_class"}]}]}]) is True
    assert bb._bindings_need_preset_css([{"bindings": [{"action": "show"}]}]) is False


# ---------------------------------------------------------------- compile_bindings

def test_compile_bindings_empty():
    out = bb.compile_bindings([])
    assert out["js"].startswith("function sporeApplyBindings(eventName, payload) {")
    assert "socket.on" not in out["js"] and "sporeToggle" not in out["js"]
    assert out["css"] == '.spore-element[data-spore-hidden="true"] { display: none !important; }'


def test_compile_bindings_full():
    elements = [
        "junk",
        {"id": "", "bindings": [{"event": "x", "action": "show"}]},
        {"id": "nob", "bindings": "bad"},
        {"id": "title", "type": "text", "bindings": [
            "junk",
            {"event": "", "action": "show"},
            {"event": "next_alert", "action": "show_for", "filter": {"alert_type": "sub"}},
            {"event": "new-message", "action": "set_text", "args": {"from_payload": "message"},
             "chain": [{"action": "toggle", "delay_ms": 50}]},
            {"event": "next_alert", "action": 5},
        ]},
        {"id": "anim", "type": "marquee", "animations": {"anim_in": "fade"},
         "bindings": [{"event": "pause_status_update", "action": "hide"}]},
        {"id": "tw", "type": "image", "bindings": [
            {"event": "twitch-api-response", "action": "set_image", "args": {"from_payload": "url"},
             "twitch_api": {"endpoint": "https://api.twitch.tv/helix/x"},
             "twitch_filters": [{"key": "ok", "value": "true"}]},
            {"event": "twitch-api-response"},
        ]},
    ]
    out = bb.compile_bindings(elements)
    js = out["js"]
    assert js.startswith(bb._TOGGLE_JS)
    assert 'if (eventName === "next_alert") {' in js
    assert 'if (sporeMatchesFilter(payload, {"alert_type": "sub"})) {' in js
    assert 'if (sporeMatchesFilter(payload, {"requestId": "spore_tw_tw_0", "ok": true})) {' in js
    # auto listeners only for non-boilerplate events, sorted
    registered = re.findall(r'socket\.on\(("[^"]+")', js)
    assert registered == ['"new-message"', '"pause_status_update"', '"twitch-api-response"']
    assert js.count("socket.emit('twitch-api-request'") == 1
    assert '"requestId": "spore_tw_tw_0"' in js
    assert _balanced(js)
    css = out["css"]
    assert bb.ANIMATION_CSS in css and bb.MARQUEE_CSS in css and bb.ACCENT_CSS in css
    assert bb.WIDGET_CSS not in css and bb.PROGRESS_BAR_CSS not in css


def test_compile_bindings_css_parts():
    elements = [
        {"id": "c", "type": "counter", "value_animation": {"enabled": True}},
        {"id": "i", "type": "image", "src_mode": "from_counter",
         "counter_image_transition": {"enabled": True, "type": "slide"}},
        {"id": "p", "type": "progress_bar"},
        {"id": "w", "type": "wheel"},
    ]
    css = bb.compile_bindings(elements)["css"]
    for part in (bb.VALUE_CHANGE_ANIMATION_CSS, bb.COUNTER_IMAGE_TRANSITION_CSS, bb.PROGRESS_BAR_CSS,
                 bb.WIDGET_CSS):
        assert part in css
    assert bb.ANIMATION_CSS not in css and bb.ACCENT_CSS not in css


def test_compile_bindings_animation_from_step_only():
    elements = [{"id": "a", "bindings": [{"event": "e", "action": "show", "args": {"anim_in": "fade"}}]}]
    assert bb.ANIMATION_CSS in bb.compile_bindings(elements)["css"]


def test_compile_bindings_skips_twitch_emit_when_iife_empty(monkeypatch):
    monkeypatch.setattr(bb, "_compile_twitch_emit_iife", lambda binding, rid: "")
    elements = [{"id": "a", "bindings": ["junk", {"event": "twitch-api-response", "action": "show"}]},
                {"id": "", "bindings": []}, {"id": "b", "bindings": "bad"}, "junk"]
    js = bb.compile_bindings(elements)["js"]
    assert "twitch-api-request" not in js


def test_compile_bindings_only_primary_actions_without_steps(monkeypatch):
    monkeypatch.setattr(bb, "_binding_action_steps", lambda binding: [])
    js = bb.compile_bindings([{"id": "a", "bindings": [{"event": "e", "action": "show"}]}])["js"]
    assert 'eventName === "e"' not in js


# ---------------------------------------------------------------- CSS constants

@pytest.mark.parametrize("css", [bb.ANIMATION_CSS, bb.VALUE_CHANGE_ANIMATION_CSS, bb.COUNTER_IMAGE_TRANSITION_CSS,
                                 bb.PROGRESS_BAR_CSS, bb.MARQUEE_CSS, bb.WIDGET_CSS, bb.ACCENT_CSS])
def test_css_constants_are_balanced(css):
    assert css and css.count("{") == css.count("}")


def test_anim_class_table():
    assert bb._ANIM_CLASS["none"] == ("", "")
    assert bb._ANIM_CLASS["slideIn"] == bb._ANIM_CLASS["slideOut"]


@pytest.mark.parametrize("cls", [".spore-progress-bar", ".spore-wheel"])
@pytest.mark.xfail(strict=True, reason="BUG: widget CSS sets position: relative on .spore-progress-bar / "
                                       ".spore-wheel, overriding .spore-element {position: absolute} (same "
                                       "specificity, later rule), so these blocks leave absolute layout")
def test_widget_css_keeps_blocks_absolutely_positioned(cls):
    css = bb.PROGRESS_BAR_CSS + "\n" + bb.WIDGET_CSS
    rule = re.search(re.escape(cls) + r"\s*\{([^}]*)\}", css)
    assert rule is not None
    assert "position: relative" not in rule.group(1)

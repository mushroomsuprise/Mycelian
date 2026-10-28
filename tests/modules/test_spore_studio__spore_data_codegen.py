# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/spore_data_codegen.py (counters, displays, timers, feeds JS)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jinja2
import pytest

from modules import path_utils
from modules.spore_studio import spore_data_codegen as sdc


def _render(js, **variables):
    """Resolve the Jinja expressions the generated JS carries (as the overlay server would)."""
    return jinja2.Environment().from_string(js).render(**variables)


def _line(js, prefix):
    rows = [ln for ln in js.splitlines() if ln.startswith(prefix)]
    assert rows, prefix
    return rows


def _json_after(line, prefix, suffix=";"):
    body = line[len(prefix):]
    if body.endswith(suffix):
        body = body[: -len(suffix)]
    return json.loads(body)


# ---------------------------------------------------------------- runtime block

def test_load_data_runtime_js_reads_boilerplate(tmp_path, monkeypatch):
    bp = tmp_path / "_boilerplates"
    bp.mkdir()
    (bp / "spore_data_runtime.js").write_text("  var x = 1;  \n", encoding="utf-8")
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": os.path.join(str(tmp_path), rel))
    assert sdc.load_data_runtime_js() == "var x = 1;"


def test_load_data_runtime_js_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": os.path.join(str(tmp_path), rel))
    assert sdc.load_data_runtime_js() == ""


def test_inject_data_runtime_block_variants(monkeypatch):
    monkeypatch.setattr(sdc, "load_data_runtime_js", lambda: "RUNTIME();")
    html = "a\n// SPORE_STUDIO:data-runtime-begin\nOLD\n// SPORE_STUDIO:data-runtime-end\nb"
    assert sdc.inject_data_runtime_block(html) == (
        "a\n// SPORE_STUDIO:data-runtime-begin\nRUNTIME();\n// SPORE_STUDIO:data-runtime-end\nb")
    legacy = "x // SPORE_STUDIO:auto-begin y"
    out = sdc.inject_data_runtime_block(legacy)
    assert out.startswith("x // SPORE_STUDIO:data-runtime-begin\nRUNTIME();\n// SPORE_STUDIO:data-runtime-end")
    assert out.endswith("// SPORE_STUDIO:auto-begin y")
    assert sdc.inject_data_runtime_block("no markers") == "no markers"
    monkeypatch.setattr(sdc, "load_data_runtime_js", lambda: "")
    assert sdc.inject_data_runtime_block(html) == html


# ---------------------------------------------------------------- ids

def test_id_helpers():
    assert sdc._slugify_id("a b/c") == "a_b_c"
    assert sdc._slugify_id(None) == "el"
    assert sdc.counter_format_config_id("my id") == "my_id_format"
    assert sdc.counter_image_default_src_id("x") == "x_counter_default_src"
    assert sdc.counter_image_range_min_id("x", 2) == "x_range_2_min"
    assert sdc.counter_image_range_max_id("x", 2) == "x_range_2_max"
    assert sdc.counter_image_range_src_id("x", 2) == "x_range_2_src"
    assert sdc.progress_bar_max_config_id("p") == "p_max"
    assert sdc.progress_bar_max_kind_config_id("p") == "p_max_kind"
    assert sdc.timer_config_ids("t") == {"mode": "t_mode", "start_seconds": "t_start_seconds",
                                         "limit_seconds": "t_limit_seconds", "auto_start": "t_auto_start",
                                         "format": "t_format"}


@pytest.mark.parametrize("op,expected", [("PAUSE", "t_pause"), ("reset", "t_reset"), ("", "t_start"),
                                         ("bogus", "t_start"), (None, "t_start")])
def test_timer_control_event(op, expected):
    assert sdc.timer_control_event("t", op) == expected


# ---------------------------------------------------------------- timer props

def test_nonneg_int():
    assert sdc._nonneg_int("5.9", 1) == 5
    assert sdc._nonneg_int(-3, 1) == 0
    assert sdc._nonneg_int("x", 7) == 7


def test_timer_props():
    assert sdc.timer_mode(None) == "count_down"
    assert sdc.timer_mode({"mode": "COUNT_UP"}) == "count_up"
    assert sdc.timer_mode({"mode": "sideways"}) == "count_down"
    assert sdc.timer_start_seconds(None) == 300
    assert sdc.timer_start_seconds({"start_seconds": 10, "duration_seconds": 99}) == 10
    assert sdc.timer_start_seconds({"duration_seconds": "42"}) == 42
    assert sdc.timer_start_seconds({"start_seconds": "bad"}) == 300
    assert sdc.timer_limit_seconds(None) == 0
    assert sdc.timer_limit_seconds({"limit_seconds": "60"}) == 60
    assert sdc.timer_limit_seconds({"limit_seconds": "x"}) == 0
    assert sdc.timer_format(None) == "{mm}:{ss}"
    assert sdc.timer_format({"format": "   "}) == "{mm}:{ss}"
    assert sdc.timer_format({"format": " {hh}h "}) == "{hh}h"


def test_build_timer_connector_actions():
    acts = sdc.build_timer_connector_actions([{"type": "timer", "id": "t1"}, {"type": "text", "id": "x"},
                                              {"type": "TIMER"}])
    assert list(acts) == ["t1_start", "t1_pause", "t1_reset", "timer_start", "timer_pause", "timer_reset"]
    assert acts["t1_pause"] == {"action_name": "Pause t1", "trigger": "t1_pause", "elements": []}
    assert acts["timer_reset"]["action_name"] == "Reset timer"


def test_jinja_tojson_default_renders():
    expr = sdc._jinja_tojson_default("v", "a\"b")
    assert expr == '{{ v|default("a\\"b")|tojson }}'
    assert json.loads(_render(expr)) == 'a"b'
    assert json.loads(_render(expr, v="override")) == "override"


# ---------------------------------------------------------------- element kinds

def test_is_counter_and_display_element():
    assert sdc._is_counter_element({"type": "counter", "counter": {}}) is True
    assert sdc._is_counter_element({"type": "counter"}) is False
    assert sdc._is_counter_element({"type": "text", "text_mode": "counter", "counter": {}}) is True
    assert sdc._is_counter_element({"type": "text", "counter": {}}) is False
    assert sdc._is_counter_element({"type": "image"}) is False
    assert sdc._is_display_element({"type": "data_display"}) is True
    assert sdc._is_display_element({"type": "marquee", "text_mode": "data_display", "data_display": {}}) is True
    assert sdc._is_display_element({"type": "text", "text_mode": "data_display"}) is False
    assert sdc._is_display_element({"type": "image"}) is False


# ---------------------------------------------------------------- progressive goals

def test_normalize_progressive_goals():
    assert sdc.normalize_progressive_goals(None) == []
    assert sdc.normalize_progressive_goals([10, "20", True, "x", 15, 20, 30.5, None, 40.0]) == [10, 20, 30.5, 40]


def test_progressive_goal_state():
    assert sdc.progressive_goal_state(5, []) is None
    s = sdc.progressive_goal_state(15, [10, 20, 30])
    assert s == {"value": 15, "goals": [10, 20, 30], "floor": 10, "ceiling": 30,
                 "active_goal": 20, "active_index": 1}
    s = sdc.progressive_goal_state(5, [10, 20], min_bound="2")
    assert s["floor"] == 2 and s["active_goal"] == 10 and s["active_index"] == 0
    s = sdc.progressive_goal_state(-5, [10], min_bound=0)
    assert s["value"] == 0
    s = sdc.progressive_goal_state(99, [10, 20])
    assert s["value"] == 20 and s["active_index"] == 1 and s["active_goal"] == 20
    s = sdc.progressive_goal_state("junk", [10], min_bound="bad")
    assert s["value"] == 0 and s["floor"] is None
    s = sdc.progressive_goal_state(True, [10, 20.5])
    assert s["value"] == 0 and s["ceiling"] == 20.5
    s = sdc.progressive_goal_state(12.5, [10, 20])
    assert s["value"] == 12.5


def test_counter_cfg_by_id(monkeypatch):
    els = [{"type": "counter", "id": "a", "counter": {"counter_id": "Main"}},
           {"type": "counter", "id": "b", "counter": {}}]
    assert sdc._counter_cfg_by_id(els, "Main") == {"counter_id": "Main"}
    assert sdc._counter_cfg_by_id(els, "b") == {}
    assert sdc._counter_cfg_by_id(els, "zzz") is None
    monkeypatch.setattr(sdc, "_slugify_id", lambda v: "")
    assert sdc._counter_cfg_by_id(els, "x") is None


# ---------------------------------------------------------------- transforms

def test_normalize_transform_rule():
    assert sdc._normalize_transform_rule("x") is None
    assert sdc._normalize_transform_rule({"property": "opacity"}) is None
    r = sdc._normalize_transform_rule({})
    assert r == {"id": "", "enabled": True, "property": "scale", "source": {}, "in_min": 0, "in_max": 100,
                 "out_min": 0, "out_max": 1, "origin": "center"}
    r = sdc._normalize_transform_rule({"id": 3, "enabled": False, "property": "rotate",
                                       "source": {"kind": "counter"}, "in_min": "1.5", "in_max": "",
                                       "out_min": "bad", "out_max": 2.0, "origin": "  "})
    assert r == {"id": "3", "enabled": False, "property": "rotate", "source": {"kind": "counter"},
                 "in_min": 1.5, "in_max": 100, "out_min": 0, "out_max": 2, "origin": "center"}


def test_compile_transform_table():
    assert sdc._compile_transform_table({}, []) == ""
    js = sdc._compile_transform_table(
        {"canvas_transforms": [{"property": "scale"}, {"property": "bad"}]},
        ["junk", {"id": ""}, {"id": "a", "transforms": [{"property": "translate_x"}, "bad"]},
         {"id": "b", "transforms": []}])
    first, second = js.split("\n")
    table = _json_after(first, "window.__sporeTransforms = ")
    assert [r["property"] for r in table["canvas"]] == ["scale"]
    assert list(table["elements"]) == ["a"]
    assert second.startswith("if (typeof sporeInitTransforms")
    only_el = sdc._compile_transform_table({}, [{"id": "a", "transforms": [{}]}])
    assert '"canvas": []' in only_el


# ---------------------------------------------------------------- collectors / meta

def test_collectors():
    els = [
        "junk",
        {"type": "counter", "id": "c", "counter": {}},
        {"type": "image", "id": "i", "src_mode": "from_counter", "counter_src": {}},
        {"type": "image", "id": "i2", "src_mode": "from_counter", "counter_src": "bad"},
        {"type": "image", "id": "i3"},
        {"type": "progress_bar", "id": "p", "progress_source": {}},
        {"type": "progress_bar", "id": "p2"},
        {"type": "clock", "id": "k"},
        {"type": "timer", "id": "t"},
        {"type": "data_display", "id": "d", "data_display": "bad"},
    ]
    assert [e["id"] for e, _ in sdc._collect_counters(els)] == ["c"]
    assert [e["id"] for e, _ in sdc._collect_counter_images(els)] == ["i"]
    assert [e["id"] for e in sdc._collect_progress_bars(els)] == ["p"]
    assert [e["id"] for e in sdc._collect_clocks(els)] == ["k"]
    assert [e["id"] for e in sdc._collect_timers(els)] == ["t"]
    displays = sdc._collect_displays(els)
    assert [e["id"] for e, _ in displays] == ["d"]
    assert els[-1]["data_display"] == {}  # normalised in place


def test_collect_counters_fills_missing_cfg(monkeypatch):
    el = {"type": "counter", "counter": "bad"}
    monkeypatch.setattr(sdc, "_is_counter_element", lambda e: True)
    ((got, cfg),) = sdc._collect_counters([el])
    assert cfg == {} and el["counter"] == {}


def test_counter_image_transition_meta():
    assert sdc._counter_image_transition_meta({}) is None
    assert sdc._counter_image_transition_meta({"counter_image_transition": {"enabled": False}}) is None
    m = sdc._counter_image_transition_meta({"counter_image_transition": {"enabled": True, "type": "ROLL",
                                                                         "duration_ms": "250"}})
    assert m == {"enabled": True, "type": "roll", "duration_ms": 250, "easing": "ease-out"}
    m = sdc._counter_image_transition_meta({"counter_image_transition": {"enabled": True, "type": "zap",
                                                                         "duration_ms": "x", "easing": "linear"}})
    assert m == {"enabled": True, "type": "fade", "duration_ms": 400, "easing": "linear"}


def test_value_animation_meta():
    assert sdc._value_animation_meta({"value_animation": "x"}) is None
    m = sdc._value_animation_meta({"value_animation": {"enabled": True, "type": "tick_up", "duration_ms": -5,
                                                       "pulse": 1}})
    assert m == {"enabled": True, "type": "tick_up", "duration_ms": 0, "easing": "ease-out", "pulse": True}
    m = sdc._value_animation_meta({"value_animation": {"enabled": True, "type": "warp", "duration_ms": "x"}})
    assert m["type"] == "fade-in" and m["duration_ms"] == 500 and m["pulse"] is False


def test_counter_ids_unique():
    els = [{"type": "counter", "id": "a b", "counter": {}},
           {"type": "counter", "id": "x", "counter": {"counter_id": "shared"}},
           {"type": "counter", "counter": {}}]
    assert sdc._counter_ids_unique(els) == {"a_b": "a b", "shared": "x", "counter": ""}


# ---------------------------------------------------------------- compile_spore_data_features

def test_compile_minimal_model():
    js = sdc.compile_spore_data_features({})
    assert 'window.__sporeTemplateName = "untitled";' in js
    assert "window.__sporeFeeds = [];" in js and "window.__sporeOutputs = [];" in js
    assert "sporeInitWidgets" in js and "__sporeTransforms" not in js
    assert "__sporeCounterImages" not in js and "__sporeTimers" not in js


def test_compile_counter_images():
    model = {"template_name": "t", "elements": [
        {"type": "image", "id": "img", "src_mode": "from_counter", "props": {"src": "/a.png"},
         "counter_image_transition": {"enabled": True, "type": "slide"},
         "counter_src": {"counter_id": "c 1", "ranges": [
             {"min": 0, "max": 5, "src": "/low.png"}, {"min": "x", "max": "y", "src": "/mid.png"},
             {"min": 6, "src": "  "}, "junk"]}},
        {"type": "image", "id": "img2", "src_mode": "from_counter", "counter_src": {"default_src": "/d.png"}},
    ]}
    js = sdc.compile_spore_data_features(model)
    rows = _line(js, "window.__sporeCounterImages.push(")
    first = json.loads(_render(rows[0])[len("window.__sporeCounterImages.push("):-2])
    assert first == {"elementId": "img", "counter_id": "c_1", "default_src": "/a.png",
                     "ranges": [{"min": 0, "max": 5, "src": "/low.png"}, {"min": 0, "max": 0, "src": "/mid.png"}],
                     "range_transition": {"enabled": True, "type": "slide", "duration_ms": 400,
                                          "easing": "ease-out"}}
    assert "img_range_1_src" in rows[0]
    second = json.loads(_render(rows[1], img2_counter_default_src="/x.png")[len("window.__sporeCounterImages.push("):-2])
    assert second["default_src"] == "/x.png" and "range_transition" not in second


def test_compile_progress_bars_without_counters():
    model = {"elements": [
        {"type": "progress_bar", "id": "bar", "progress_source": {"counter_id": "c", "max_kind": "weird",
                                                                 "max": "bad"},
         "props": {"near_goal_threshold": "150", "near_goal_effect": "zap", "near_goal_pulse": True}},
        {"type": "progress_bar", "id": "bar2", "progress_source": {"max_kind": "counter", "max_counter_id": "m"},
         "props": {"near_goal_threshold": "x", "near_goal_effect": "SHIMMER"}},
    ]}
    js = sdc.compile_spore_data_features(model)
    rows = [json.loads(_render(r)[len("window.__sporeProgressBars.push("):-2])
            for r in _line(js, "window.__sporeProgressBars.push(")]
    assert rows[0] == {"elementId": "bar", "counter_id": "c", "max_kind": "fixed", "max": 100,
                       "max_counter_id": "el", "follow_counter_goals": False, "near_goal_threshold": 100,
                       "near_goal_effect": "pulse"}
    assert rows[1]["max_kind"] == "counter" and rows[1]["near_goal_threshold"] == 90
    assert rows[1]["near_goal_effect"] == "shimmer" and rows[1]["max_counter_id"] == "m"
    assert "if (typeof sporeUpdateAllProgressBars === 'function') { sporeUpdateAllProgressBars(); }" in js


def test_compile_progress_bar_follows_progressive_counter():
    model = {"template_name": "t", "elements": [
        {"type": "counter", "id": "c", "counter": {"progressive": True, "goals": [10, 20], "max": 50,
                                                   "persist": 1, "initial_value": 3,
                                                   "rules": [{"event": "next_alert"}]},
         "value_animation": {"enabled": True}},
        {"type": "progress_bar", "id": "bar", "progress_source": {"counter_id": "c"},
         "props": {"near_goal_effect": "none"}},
    ]}
    js = sdc.compile_spore_data_features(model)
    bar = _line(js, "window.__sporeProgressBars.push(")[0]
    assert '"follow_counter_goals": true' in bar
    meta_line = _line(js, 'window.__sporeCounterMeta["c"]')[0]
    meta = json.loads(meta_line.split("Object.assign(", 1)[1].split(', {"format"')[0])
    assert meta == {"elementId": "c", "counter_id": "c", "initial_value": 3, "min": None, "max": None,
                    "persist": True, "database_path": "t/counters", "database_key": "c",
                    "progressive": True, "goals": [10, 20],
                    "value_animation": {"enabled": True, "type": "fade-in", "duration_ms": 500,
                                        "easing": "ease-out", "pulse": False}}
    assert 'var __ids = ["c"];' in js and "sporeCounterHydrate(__c, __next);" in js
    # progress bar refresh comes from the hydrate chain, not the standalone call
    assert "\nif (typeof sporeUpdateAllProgressBars" not in js


def test_compile_counter_meta_non_progressive_and_format():
    model = {"template_name": "t", "elements": [
        {"type": "counter", "id": "c", "counter": {"counter_id": "Main", "max": 9, "format": "{value}/{max}",
                                                   "database_path": " p ", "database_key": " k ",
                                                   "progressive": True, "goals": []}}]}
    js = sdc.compile_spore_data_features(model)
    line = _line(js, 'window.__sporeCounterMeta["Main"]')[0]
    assert '"max": 9' in line and '"database_path": "p"' in line and '"database_key": "k"' in line
    assert "progressive" not in line
    rendered = _render(line)
    assert '"format": "{value}/{max}"' in rendered.replace("\\u007b", "{")


def test_compile_counter_rules():
    model = {"template_name": "t", "elements": [
        {"type": "counter", "id": "c", "counter": {"rules": [
            "junk",
            {"event": "next_alert", "operation": "decrement", "filter": {"alert_type": "sub", "tier": ""},
             "filters": {"alert_type": "raid"}, "delta": {"kind": "fixed", "value": 2}},
            {"event": ""},
            {"trigger": "streamdeck", "streamdeck_action": "bump"},
            {"trigger": "streamdeck"},
            {"trigger": "other", "event": "x"},
        ]}}]}
    js = sdc.compile_spore_data_features(model)
    assert 'var __ev = "next_alert";' in js and 'var __op = "decrement";' in js
    assert 'var __filter = {"alert_type": "sub"};' in js
    assert '"alert_type": "raid"' in js.split("var __shared = ")[1].split(";")[0]
    assert "data.actionName !== \"bump\"" in js
    assert js.count("socket.on('streamdeck_template_action'") == 1
    assert 'sporeCounterAdjust("c", "increment", {"kind": "fixed", "value": 1}, data.data || data);' in js


def test_compile_counter_rule_without_shared_filters():
    model = {"elements": [{"type": "counter", "id": "c", "counter": {"rules": [{"event": "e", "delta": 5}]}}]}
    js = sdc.compile_spore_data_features(model)
    assert "var __shared = null;" in js
    assert 'var __delta = {"kind": "fixed", "value": 1};' in js


def test_compile_displays_events_and_polling():
    model = {"elements": [
        {"type": "data_display", "id": "d1", "data_display": {
            "source": {"kind": "data_source", "source": "x"}, "default_text": None, "format": "{value}!",
            "refresh_on": ["b_ev", "a_ev", ""], "poll_interval_ms": 2000}, "value_animation": {"enabled": True},
         "data_feed": []},
        {"type": "data_display", "id": "d2", "data_display": {"default_text": "", "poll_interval_ms": "bad",
                                                              "refresh_on": ["c_ev"]},
         "data_feed": [{"enabled": True, "target": {"id": "set_text"}}]},
        {"type": "data_display", "id": "d3", "data_display": {"poll_interval_ms": "x"}, "data_feed": []},
    ]}
    js = sdc.compile_spore_data_features(model)
    pushes = _line(js, "window.__sporeDataDisplays.push(")
    d1 = json.loads(pushes[0].split("Object.assign(", 1)[1].split(', {"format"')[0])
    assert d1["default_text"] == "—" and "feed" not in d1 and d1["value_animation"]["enabled"] is True
    d2 = json.loads(pushes[1].split("Object.assign(", 1)[1].split(', {"format"')[0])
    assert d2["default_text"] == "" and d2["feed"] is True
    evs = re.findall(r'var __ev = "([a-z_]+)";\n    if', js)
    assert evs == ["a_ev", "b_ev"]  # sorted, d2 (feed driven) skipped
    assert "sporeRefreshAllDataDisplays(); }, 2000);" in js


def test_compile_dynamic_controls():
    model = {"template_name": "tpl", "dynamic_controls": {"elements": [
        "junk",
        {"action": ""},
        {"type": "timer_control", "action": "t_start"},
        {"type": "counter_control", "action": "bump", "target_counter_id": "c"},
        {"type": "button", "action": "counter_adjust", "operation": "set", "delta": {"kind": "fixed", "value": 4}},
        {"type": "button", "action": "counter_adjust", "target_counter_id": "dup"},
        {"type": "slider", "action": "level"},
        {"type": "button", "action": "go", "data": {"k": 1}},
        {"type": "button", "action": "plain"},
    ]}}
    js = sdc.compile_spore_data_features(model)
    blocks = js.split("var __tn = ")[1:]
    assert len(blocks) == 5
    assert 'socket.on("tpl_bump"' in js and 'var __act = "counter_adjust";' in blocks[0]
    assert 'var __base = {"target_counter_id": "c"};' in blocks[0]
    assert '"operation": "set"' in blocks[1] and '"value": 4' in blocks[1]
    assert 'var __base = {"value": "\\u007b\\u007bvalue}}"};' in blocks[2]
    assert 'var __base = {"k": 1};' in blocks[3]
    assert 'var __base = {};' in blocks[4]
    assert "tpl_t_start" not in js


def test_compile_dynamic_controls_non_dict():
    js = sdc.compile_spore_data_features({"dynamic_controls": ["x"]})
    assert "sporeDispatchControlAction" not in js


def test_compile_clocks_and_timers():
    model = {"template_name": "tpl", "elements": [
        {"type": "clock", "id": "k1", "props": {"timezone": "UTC", "timezone_offset_minutes": "bad"}},
        {"type": "clock", "id": "k2", "props": {"timezone": "mars", "timezone_offset_minutes": "90",
                                                "format": "HH:mm"}},
        {"type": "timer", "id": "t1", "props": {"mode": "count_up", "limit_seconds": 60, "auto_start": False}},
    ]}
    js = sdc.compile_spore_data_features(model)
    clocks = [json.loads(_render(r)[len("window.__sporeClocks.push("):-2]) for r in _line(js, "window.__sporeClocks.push(")]
    assert clocks[0] == {"elementId": "k1", "format": "HH:mm:ss", "timezone": "utc", "timezone_offset_minutes": 0}
    assert clocks[1] == {"elementId": "k2", "format": "HH:mm", "timezone": "local", "timezone_offset_minutes": 90}
    timer_line = _line(js, 'window.__sporeTimers["t1"]')[0]
    timer = json.loads(_render(timer_line).split(" = ", 1)[1].rstrip(";"))
    assert timer == {"elementId": "t1", "mode": "count_up", "start_seconds": 300, "limit_seconds": 60,
                     "duration_seconds": 300, "auto_start": False, "format": "{mm}:{ss}",
                     "running": False, "elapsed": 0, "started_at": None}
    for ev in ("tpl_t1_start", "tpl_t1_pause", "tpl_t1_reset"):
        assert f'__bind("{ev}"' in js
    assert "sporeInitClocks();" in js and "sporeInitTimers();" in js


def test_compile_feeds_and_list_row_template():
    model = {"template_name": "t", "elements": [
        "junk",
        {"type": "list", "id": "L", "data_feed": [
            {"id": "f1", "target": {"id": "list_add"}, "mapping": {"template": "{username} joined"}},
            {"id": "f2", "enabled": False},
            {"id": "f3", "target": {"id": "list_add"}, "mapping": {"template": "{x}"}},
        ]},
        {"type": "list", "id": "L2", "data_feed": [{"id": "g", "target": {"id": "list_add"}}]},
        {"type": "wheel", "id": "W"},
    ]}
    js = sdc.compile_spore_data_features(model)
    feeds_line = _line(js, "window.__sporeFeeds = ")[0]
    assert "L_row_template" in feeds_line
    feeds = json.loads(_render(feeds_line)[len("window.__sporeFeeds = "):-1])
    by_id = {(f["elementId"], f["id"]): f for f in feeds}
    assert ("L", "f2") not in by_id
    assert by_id[("L", "f1")]["mapping"]["template"] == "{username} joined"
    assert by_id[("L", "f3")]["mapping"]["template"] == "{x}"
    assert by_id[("L2", "g")]["mapping"]["template"] == ""
    custom = json.loads(_render(feeds_line, L_row_template="{a}!")[len("window.__sporeFeeds = "):-1])
    assert custom[0]["mapping"]["template"] == "{a}!"
    outputs = _json_after(_line(js, "window.__sporeOutputs = ")[0], "window.__sporeOutputs = ")
    assert {"element_id": "W", "event": "wheel_result", "enabled": True} in outputs
    assert {"element_id": "L", "event": "list_row_added", "enabled": False} in outputs
    assert "window.__sporeFeedsCompiled = true;" in js


def test_compile_includes_transforms():
    js = sdc.compile_spore_data_features({"elements": [{"id": "a", "transforms": [{"property": "rotate"}]}]})
    assert js.endswith("if (typeof sporeInitTransforms === 'function') { sporeInitTransforms(); }")


def test_compile_spore_data_css():
    assert sdc.compile_spore_data_css() == ""

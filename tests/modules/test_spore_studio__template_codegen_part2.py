# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/template_codegen.py: DOM tree, Stream Deck options,
the public JSON config, dynamic controls, the template logger and compile_model."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jinja2
import pytest

from modules.spore_studio import fonts_registry
from modules.spore_studio import template_codegen as tc
from modules.web_engine import WebEngine


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    fdir = tmp_path / "assets" / "default_assets" / "fonts"
    fdir.mkdir(parents=True)
    (fdir / "Renogare.ttf").write_bytes(b"x")
    monkeypatch.setattr(fonts_registry, "get_assets_path", lambda rel="": str(tmp_path / "assets"))
    monkeypatch.setattr(fonts_registry, "_fonts_listing_cache", None)


def _render(model):
    html, config = tc.compile_model(model)
    out = jinja2.Environment(autoescape=True).from_string(html).render(
        **WebEngine._template_variable_map(config), mycelian_html_stem="t", mycelian_preview_mode=False)
    return out, config


def _cfg_by_id(cfg):
    return {e["id"]: e for e in cfg["elements"] if isinstance(e, dict) and "id" in e}


def _model(elements, **extra):
    m = {"template_name": "demo", "alert_system": "queue", "design": {"width": 640, "height": 360},
         "elements": elements}
    m.update(extra)
    return m


# ---------------------------------------------------------------- DOM tree

def test_partition_elements_for_dom():
    els = [
        "junk", {"id": ""}, {"id": None},
        {"id": "box", "type": "container"},
        {"id": "kid", "parent_id": "box"},
        {"id": "orphan", "parent_id": "ghost"},
        {"id": "pic", "type": "image"},
        {"id": "under_pic", "parent_id": "pic"},
        {"id": "blank_parent", "parent_id": "  "},
        {"id": "typeless_parent"},
        {"id": "kid2", "parent_id": "typeless_parent"},
    ]
    roots, kids = tc._partition_elements_for_dom(els)
    assert [r["id"] for r in roots] == ["box", "orphan", "pic", "under_pic", "blank_parent", "typeless_parent"]
    assert {k: [e["id"] for e in v] for k, v in kids.items()} == {"box": ["kid"], "typeless_parent": ["kid2"]}


def test_render_dom_tree():
    assert tc._render_dom_tree("x") == ""
    assert tc._render_dom_tree(["junk"]) == ""
    out = tc._render_dom_tree([
        {"id": "box", "type": "container", "size": {"w": 100, "h": 50}},
        {"id": "kid", "type": "text", "parent_id": "box",
         "placement": {"anchor_h": "right", "anchor_v": "top"}, "size": {"w": 20, "h": 10}},
        {"id": "empty", "type": "container"},
    ])
    lines = out.split("\n")
    assert lines[0].startswith('        <div id="box"')
    assert lines[1].startswith('            <div id="kid"') and "left: 80px; top: 0px" in lines[1]
    assert lines[1].endswith("</div></div>")
    assert lines[2].startswith('        <div id="empty"') and lines[2].endswith('data-spore-type="container"></div>')


def test_boilerplate_paths():
    assert tc._boilerplate_path("queue").endswith("queue_alert.html")
    assert tc._boilerplate_path("instant").endswith("instant_alert.html")
    assert "SPORE_STUDIO:dom-begin" in tc._load_boilerplate("instant")


# ---------------------------------------------------------------- Stream Deck

def test_sanitize_streamdeck_options():
    assert tc._sanitize_streamdeck_options(None) == {"description": "", "actions": {}}
    assert tc._sanitize_streamdeck_options({"description": 5, "actions": "x"}) == {"description": "5",
                                                                                  "actions": {}}
    out = tc._sanitize_streamdeck_options({"actions": {
        "Go Now": {"event": " ev ", "default_data": {"a": 1}, "name": "", "description": None},
        "!!": {"event": "x"},
        "bad_spec": "x",
        "no_event": {"event": " "},
        "dd_list": {"event": "e", "default_data": [1]},
        "dd_none": {"event": "e", "default_data": None, "name": "N", "description": "D"},
    }})
    assert out["description"] == ""
    assert out["actions"] == {
        "Go_Now": {"name": "Go_Now", "description": "", "event": "ev", "default_data": {"a": 1}},
        "dd_list": {"name": "dd_list", "description": "", "event": "e", "default_data": {}},
        "dd_none": {"name": "N", "description": "D", "event": "e", "default_data": {}},
    }


def test_merge_streamdeck_binding_args():
    sdo = {"actions": {"go": {"event": "e", "default_data": {"keep": 1, "x": 0}},
                       "bad_dd": {"event": "e", "default_data": "x"}, "notdict": "x"}}
    elements = [
        "junk",
        {"bindings": [
            "junk",
            {"trigger": "event", "streamdeck_action": "go", "args": {"x": 9}},
            {"trigger": "streamdeck"},
            {"trigger": "streamdeck", "streamdeck_action": "missing", "args": {"x": 1}},
            {"trigger": "streamdeck", "streamdeck_action": "notdict", "args": {"x": 1}},
            {"trigger": "streamdeck", "streamdeck_action": "go", "args": {"x": 2},
             "chain": ["junk", {"args": "bad"}, {"args": {"x": 3, "y": 4}}]},
            {"trigger": "streamdeck", "streamdeck_action": "bad_dd", "args": {"z": 1}},
            {"trigger": "streamdeck", "streamdeck_action": "go"},
        ]},
    ]
    tc._merge_streamdeck_binding_args_into_actions(elements, sdo)
    assert sdo["actions"]["go"]["default_data"] == {"keep": 1, "x": 3, "y": 4}
    assert sdo["actions"]["bad_dd"]["default_data"] == {"z": 1}
    empty = {"actions": {}}
    tc._merge_streamdeck_binding_args_into_actions(elements, empty)
    assert empty == {"actions": {}}
    sdo2 = {"actions": {"go": {"default_data": {}}}}
    tc._merge_streamdeck_binding_args_into_actions("not-a-list", sdo2)
    assert sdo2["actions"]["go"]["default_data"] == {}


# ---------------------------------------------------------------- public JSON config

def test_derived_config_canvas_fields():
    cfg = tc._derived_json_config({"design": {}, "queued": 1, "title": "Nice", "elements": ["junk"]})
    ids = _cfg_by_id(cfg)
    assert cfg["template_name"] == "untitled" and cfg["spore_studio"] is True
    assert ids["DesignWidth"]["value"] == 800 and ids["DesignHeight"]["value"] == 200
    assert ids["TemplateTitle"]["value"] == "Nice" and ids["Queued"]["value"] is True
    assert ids["Duration"]["value"] == 5.0
    assert "dynamic_controls" not in cfg and "connector_actions" not in cfg and "outputs" not in cfg


def test_derived_config_text_modes_and_categories():
    cfg = tc._derived_json_config(_model([
        {"id": "t", "type": "text", "category": "  ", "props": {"text": "Hi"}},
        {"id": "c", "type": "counter", "category": "Counters", "counter": {"format": "{value}!"}},
        {"id": "cd", "type": "counter", "counter": "bad"},
        {"id": "d", "type": "data_display", "category": "Counters", "data_display": "bad"},
        {"id": "hidden", "type": "text", "source_settings_expose": {"text": False}},
        {"id": "hc", "type": "counter", "source_settings_expose": {"text": False}},
        {"id": "hd", "type": "data_display", "source_settings_expose": {"text": False}},
    ]))
    seps = [e["label"] for e in cfg["elements"] if e.get("type") == "separator"]
    assert seps == ["Canvas", "Elements", "Counters"]
    ids = _cfg_by_id(cfg)
    assert ids["tText"]["value"] == "Hi"
    assert ids["c_format"]["value"] == "{value}!" and ids["cd_format"]["value"] == "{value}"
    assert ids["d_format"]["value"] == "{value}"
    assert "hiddenText" not in ids and "hc_format" not in ids and "hd_format" not in ids


def test_derived_config_media_fields():
    cfg = tc._derived_json_config(_model([
        {"id": "img", "type": "image", "props": {"src": "/a.png"}},
        {"id": "ci", "type": "image", "src_mode": "from_counter", "counter_src": {"ranges": [
            {"min": 1, "max": 5, "src": "/x.png"}, "junk", {"min": "x", "max": "y"}]}},
        {"id": "ci2", "type": "image", "src_mode": "from_counter", "counter_src": "bad", "props": {"src": "/d.png"}},
        {"id": "aud", "type": "audio", "props": {"volume": 7, "fade_in_ms": "x", "fade_out_ms": -3}},
        {"id": "aud2", "type": "audio", "props": {"volume": "bad"},
         "source_settings_expose": {"volume": False, "fade_in_ms": False, "src": False}},
        {"id": "vid", "type": "video", "props": {"visual_kind": "odd", "audio_volume": "x",
                                                 "audio_fade_in_ms": "x", "loop": 1}},
        {"id": "vid2", "type": "video", "props": {"audio_volume": 3},
         "source_settings_expose": {"autoplay": False, "visual_kind": False, "audio_src": False,
                                    "audio_volume": False, "audio_fade_out_ms": False}},
    ]))
    ids = _cfg_by_id(cfg)
    assert ids["imgSrc"]["value"] == "/a.png"
    assert ids["ci_counter_default_src"]["value"] == ""
    assert (ids["ci_range_0_min"]["value"], ids["ci_range_0_max"]["value"]) == (1, 5)
    assert ids["ci_range_0_src"]["value"] == "/x.png"
    assert (ids["ci_range_2_min"]["value"], ids["ci_range_2_max"]["value"]) == (0, 0)
    assert ids["ci2_counter_default_src"]["value"] == "/d.png"
    assert ids["aud_volume"]["value"] == 1.0
    assert ids["aud_fade_in_ms"]["value"] == 0 and ids["aud_fade_out_ms"]["value"] == 0
    assert "aud2_volume" not in ids and "aud2_fade_in_ms" not in ids and "aud2Src" not in ids
    assert ids["aud2_fade_out_ms"]["value"] == 0
    assert ids["vid_autoplay"]["value"] is True and ids["vid_loop"]["value"] is True
    assert ids["vid_muted"]["value"] is True and ids["vid_visual_kind"]["value"] == "video"
    assert ids["vid_audio_volume"]["value"] == 1.0 and ids["vid_audio_fade_in_ms"]["value"] == 0
    assert ids["vidAudioSrc"]["value"] == ""
    assert "vid2_autoplay" not in ids and "vid2_visual_kind" not in ids and "vid2AudioSrc" not in ids
    assert "vid2_audio_volume" not in ids and "vid2_audio_fade_out_ms" not in ids
    assert ids["vid2_audio_fade_in_ms"]["value"] == 0


def test_derived_config_progress_bar_and_timer():
    cfg = tc._derived_json_config(_model([
        {"id": "p", "type": "progress_bar", "progress_source": {"max": "x", "max_kind": "weird"},
         "props": {"near_goal_effect": "explode", "near_goal_pulse": True}},
        {"id": "p2", "type": "progress_bar", "progress_source": "bad", "props": {"near_goal_effect": "zap"}},
        {"id": "p3", "type": "progress_bar",
         "source_settings_expose": {"max": False, "max_kind": False, "fill_animation_easing": False,
                                    "near_goal_effect": False}},
        {"id": "t", "type": "timer", "props": {"mode": "count_up", "limit_seconds": 30, "auto_start": False}},
        {"id": "t2", "type": "timer", "source_settings_expose": {
            "mode": False, "start_seconds": False, "limit_seconds": False, "format": False, "auto_start": False}},
    ]))
    ids = _cfg_by_id(cfg)
    assert ids["p_max"]["value"] == 100 and ids["p_max_kind"]["value"] == "fixed"
    assert ids["p_near_goal_effect"]["value"] == "pulse" and ids["p2_near_goal_effect"]["value"] == "none"
    assert ids["p_fill_animation_easing"]["value"] == "ease-out"
    assert not any(k.startswith("p3_") for k in ids)
    assert ids["t_mode"]["value"] == "count_up" and ids["t_limit_seconds"]["value"] == 30
    assert ids["t_start_seconds"]["value"] == 300 and ids["t_auto_start"]["value"] is False
    assert ids["t_format"]["value"] == "{mm}:{ss}"
    assert not any(k.startswith("t2_") for k in ids)
    assert set(cfg["connector_actions"]) == {"t_start", "t_pause", "t_reset", "t2_start", "t2_pause", "t2_reset"}


def test_derived_config_style_fields(fonts):
    cfg = tc._derived_json_config(_model([
        {"id": "s", "type": "text", "props": {
            "color": "#fff", "background_color": "", "font_family": "renogare", "track_color": "#111",
            "font_weight": "bold", "text_align": "center",
            "font_size": 24, "letter_spacing": "x", "border_radius": "4",
            "opacity": "0.5", "border_color": "#000"}},
        {"id": "o", "type": "shape", "props": {"opacity": "bad", "font_family": "Arial"},
         "source_settings_expose": {"text": False}},
        {"id": "n", "type": "shape", "props": {"color": "#1", "font_weight": "bold", "font_size": 1,
                                               "opacity": 1, "border_color": "#2"},
         "source_settings_expose": {"color": False, "font_weight": False, "font_size": False,
                                    "opacity": False, "border_color": False}},
    ]))
    ids = _cfg_by_id(cfg)
    assert ids["s_color"]["type"] == "color" and ids["s_track_color"]["value"] == "#111"
    assert "s_background_color" not in ids
    assert ids["s_font_family"] == {"type": "text", "id": "s_font_family", "label": "s font family",
                                    "value": "Renogare.ttf", "description": "Font Family for 's'."}
    assert ids["s_font_weight"]["value"] == "bold" and ids["s_text_align"]["value"] == "center"
    assert ids["s_font_size"]["value"] == 24 and ids["s_border_radius"]["value"] == 4
    assert "s_letter_spacing" not in ids
    assert ids["s_opacity"]["value"] == 0.5 and ids["s_border_color"]["value"] == "#000"
    assert "o_opacity" not in ids and ids["o_font_family"]["value"] == "Arial"
    assert not any(k.startswith("n_") for k in ids)


def test_fractional_style_values_survive_render():
    out, _cfg = _render(_model([{"id": "title", "type": "text", "position": {"x": 0, "y": 0},
                                 "size": {"w": 100, "h": 30},
                                 "props": {"text": "Hi", "letter_spacing": 1.5, "font_size": 20.5}}]))
    rule = re.search(r"#title \{[^}]*\}", out).group(0)
    assert "letter-spacing: 1.5px" in rule and "font-size: 20.5px" in rule


def test_append_widget_config_fields():
    out = []
    tc._append_widget_config_fields(out, {"type": "text"}, "x")
    assert out == []
    el = {"id": "P", "type": "poll", "props": {"show_rank": 1, "bar_height": "x", "max_items": "3",
                                                  "title": None, "style": "cards"},
          "source_settings_expose": {"foot_text": False}}
    tc._append_widget_config_fields(out, el, "P")
    by = {f["id"]: f for f in out}
    assert by["P_show_rank"]["value"] is True and by["P_bar_height"]["value"] == 0
    assert by["P_max_items"]["value"] == 3.0 and by["P_title"]["value"] == ""
    assert by["P_style"]["options"] == ["bars", "inside", "cards"] and by["P_style"]["value"] == "cards"
    assert by["P_autohide_seconds"]["value"] == 0
    assert "P_foot_text" not in by
    out2 = []
    tc._append_widget_config_fields(out2, {"type": "qr", "props": "bad"}, "q")
    assert [f["id"] for f in out2] == ["q_text", "q_foreground"]


def test_clone_dynamic_controls():
    dc = {"elements": [
        "junk", {"label": "no id"},
        {"id": "cc", "type": "counter_control", "step": "x", "operation": "inc", "delta": 1, "button_text": "b"},
        {"id": "cc2", "type": "counter_control", "action": " custom ", "step": -4},
        {"id": "tc", "type": "timer_control", "step": 1, "operation": "x"},
        {"id": "My Btn"},
        {"id": "b2", "type": " slider ", "action": "vol"},
    ]}
    out = tc._clone_dynamic_controls(dc)["elements"]
    assert out[0] == {"id": "cc", "type": "counter_control", "action": "counter_adjust", "step": 1}
    assert out[1]["action"] == "custom" and out[1]["step"] == 1
    assert out[2] == {"id": "tc", "type": "timer_control", "action": "timer_control", "target_timer_id": ""}
    assert out[3] == {"id": "My Btn", "type": "button", "action": "My_Btn"}
    assert out[4] == {"id": "b2", "type": "slider", "action": "vol"}
    assert dc["elements"][2]["operation"] == "inc"  # input untouched


def test_normalize_model_font_families(fonts):
    model = {"elements": ["junk", {"props": "x"}, {"props": {}}, {"props": {"font_family": "renogare"}},
                         {"props": {"font_family": "Arial"}}]}
    tc._normalize_model_font_families(model)
    assert model["elements"][3]["props"]["font_family"] == "Renogare.ttf"
    assert model["elements"][4]["props"]["font_family"] == "Arial"


# ---------------------------------------------------------------- template logger

LOGGER = '<script src="/assets/default_assets/template_logger.js"></script>'


def test_ensure_logger_replaces_stale_init():
    html = f"<head>\n    {LOGGER}\n    <script>MycelianLog.init({{ templateName: 'queue_alert' }});</script>\n</head>"
    out = tc.ensure_spore_template_logger(html, "my tpl")
    assert out.count("MycelianLog.init") == 1
    assert '<script>MycelianLog.init({ templateName: "my tpl" });</script>' in out


def test_ensure_logger_inserts_when_missing():
    out = tc.ensure_spore_template_logger("<html><head></head></html>", None)
    assert out.startswith("<html><head>    " + LOGGER)
    assert '"untitled"' in out
    out = tc.ensure_spore_template_logger("<body></body>", "a")
    assert out.index(LOGGER) < out.index("</body>")
    out = tc.ensure_spore_template_logger("<p>bare</p>", "a")
    assert out.startswith("<p>bare</p>\n" + LOGGER)


# ---------------------------------------------------------------- compile_model

def test_compile_model_full_render(fonts):
    model = _model([
        {"id": "title", "type": "text", "props": {"text": "Hi", "font_family": "Renogare"},
         "bindings": [{"event": "chat_message", "action": "show"}]},
        {"id": "code", "type": "qr", "props": {"text": "https://x"}},
    ], alert_system="instant", streamdeck_options={"actions": {"go": {"event": "demo_go"}}},
        dynamic_controls={"elements": [{"id": "btn"}]})
    out, cfg = _render(model)
    assert out.count("/assets/default_assets/spore_studio/qrcode.js") == 1
    assert "@font-face" in out and 'id="title"' in out
    assert cfg["alert_system"] == "instant"
    assert cfg["dynamic_controls"]["elements"][0]["action"] == "btn"
    assert cfg["streamdeck_options"]["actions"]["go"]["event"] == "demo_go"
    assert '<script>MycelianLog.init({ templateName: "demo" });</script>' in out


def test_compile_model_user_js_handling():
    html1, _ = tc.compile_model(_model([], advanced_js="console.log(1); // SPORE_STUDIO:user-end"))
    assert tc.extract_user_js(html1) == "console.log(1); // (SPORE_STUDIO user-end marker)"
    # No advanced_js key: the existing user block is kept
    html2, _ = tc.compile_model(_model([]), existing_html=html1)
    assert tc.extract_user_js(html2) == "console.log(1); // (SPORE_STUDIO user-end marker)"
    # Cleared advanced_js wins over the existing block
    html3, _ = tc.compile_model(_model([], advanced_js=""), existing_html=html1)
    assert tc.extract_user_js(html3) == ""
    # Recompiling is idempotent
    html4, _ = tc.compile_model(_model([], advanced_js=""), existing_html=html3)
    assert html4 == html3


def test_compile_model_empty_styles_block(monkeypatch):
    monkeypatch.setattr(tc, "compile_bindings", lambda els: {"js": "// none", "css": "  "})
    html, _ = tc.compile_model(_model([]))
    assert "<!-- SPORE_STUDIO:styles-begin -->\n\n<!-- SPORE_STUDIO:styles-end -->" in html


def test_compile_model_legacy_skips_migration(monkeypatch):
    from modules.spore_studio import data_feed_registry

    monkeypatch.setattr(data_feed_registry, "migrate_model_feeds",
                        lambda m: pytest.fail("legacy models are not migrated"))
    model = _model([{"id": "a", "type": "poll"}], legacy=True)
    tc.compile_model(model)
    assert "data_feed" not in model["elements"][0]


def test_neutralize_user_markers():
    js = "// SPORE_STUDIO:user-begin x // SPORE_STUDIO:user-end"
    assert tc._neutralize_user_markers(js) == "// (SPORE_STUDIO user-begin marker) x // (SPORE_STUDIO user-end marker)"


def test_widget_style_settings_are_exported_once():
    # Poll exposes color/font props both as shared style props and as widget fields;
    # the public config must list each setting id once (a duplicate id breaks Source Settings).
    keys = ("color", "background_color", "font_family", "font_size", "font_weight", "track_color")
    poll = {"id": "poll1", "type": "poll", "category": "Main", "position": {"x": 0, "y": 0},
            "size": {"w": 300, "h": 200},
            "props": {"color": "#ffffff", "background_color": "#000000", "font_family": "Arial",
                      "font_size": 20, "font_weight": "700", "track_color": "#333333"},
            "source_settings_expose": {k: True for k in keys}, "bindings": []}
    _html, config = tc.compile_model(_model([poll]))
    ids = [row["id"] for row in config["elements"] if row.get("id")]
    assert len(ids) == len(set(ids))
    assert {f"poll1_{k}" for k in keys} <= set(ids)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/template_codegen.py: helpers, CSS, fonts and block markup.

The JSON config builder, DOM tree and compile_model are in ``..._part2.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jinja2
import pytest

from modules.spore_studio import fonts_registry
from modules.spore_studio import template_codegen as tc


def _jinja(text, **variables):
    return jinja2.Environment(autoescape=True).from_string(text).render(**variables)


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    """A temp default_assets/fonts folder holding Renogare.ttf and Body.otf."""
    fdir = tmp_path / "assets" / "default_assets" / "fonts"
    fdir.mkdir(parents=True)
    for name in ("Renogare.ttf", "Body.otf", "My Font.woff"):
        (fdir / name).write_bytes(b"x")
    monkeypatch.setattr(fonts_registry, "get_assets_path", lambda rel="": str(tmp_path / "assets"))
    monkeypatch.setattr(fonts_registry, "_fonts_listing_cache", None)
    return fdir


@pytest.fixture
def no_fonts(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts_registry, "get_assets_path", lambda rel="": str(tmp_path / "none"))
    monkeypatch.setattr(fonts_registry, "_fonts_listing_cache", None)


# ---------------------------------------------------------------- blocks

def test_replace_block():
    src = "a\n  // B\nold\n  // E\nz"
    assert tc._replace_block(src, "// B", "// E", "new\n\n") == "a\n  // B\nnew\n// E\nz"
    assert tc._replace_block("no markers", "// B", "// E", "x") == "no markers"


def test_extract_block():
    assert tc._extract_block("x// B\n  code\n// Ey", "// B", "// E") == "  code"
    assert tc._extract_block("nothing", "// B", "// E") == ""


def test_slugify_id():
    assert tc._slugify_id("my id!") == "my_id"
    assert tc._slugify_id(None) == "el"
    assert tc._slugify_id("__") == "el"


@pytest.mark.parametrize(
    "default,literal,rendered",
    [(True, "true", "True"), (False, "false", "False"), (5, "5", "5"), (2.5, "2.5", "2.5"),
     ("it's \\ ok", "'it\\'s \\\\ ok'", "it&#39;s \\ ok")],
)
def test_html_jinja_default(default, literal, rendered):
    expr = tc._html_jinja_default("v", default)
    assert expr == "{{ v|default(" + literal + ") }}"
    assert _jinja(expr) == rendered


def test_apply_counter_format_tokens():
    assert tc._apply_counter_format_tokens("{value}/{max} (min {min})", 3, 0, 10) == "3/10 (min 0)"
    assert tc._apply_counter_format_tokens("", 7) == "7"
    assert tc._apply_counter_format_tokens("{min}-{max}", 1) == "-"


# ---------------------------------------------------------------- fonts

def test_register_font_lookup(fonts):
    lookup = {}
    tc._register_font_lookup(lookup, "renogare", "fam")
    assert lookup == {"renogare": "fam", "Renogare.ttf": "fam", "Renogare": "fam"}
    lookup = {}
    tc._register_font_lookup(lookup, "Unknown", "fam")
    assert lookup == {"Unknown": "fam"}


def test_emit_result_font_face_variants(fonts):
    lines, seen = [], {}
    static = {"id": "w", "source_settings_expose": {"result_font_family": False}}
    tc._emit_result_font_face(lines, seen, static, {})
    tc._emit_result_font_face(lines, seen, static, {"result_font_family": "Nope"})
    assert lines == []
    tc._emit_result_font_face(lines, seen, static, {"result_font_family": "Renogare"})
    assert lines == ['@font-face { font-family: "spore-font-Renogare_ttf"; '
                     'src: url("/assets/default_assets/fonts/Renogare.ttf"); }']
    tc._emit_result_font_face(lines, seen, dict(static, id="w2"), {"result_font_family": "Renogare.ttf"})
    assert len(lines) == 1  # already emitted
    el = {"id": "w3"}  # exposed by default
    tc._emit_result_font_face(lines, seen, el, {"result_font_family": "Body"})
    assert lines[-1].startswith('@font-face { font-family: "spore-el-w3-result"; ')
    assert '{{ w3_result_font_family|default("Body.otf") }}' in lines[-1]


def test_compile_spore_font_css(fonts):
    elements = [
        "junk",
        {"id": "a", "props": {}},
        {"id": "b", "props": {"font_family": "Renogare"}, "source_settings_expose": {"font_family": False}},
        {"id": "c", "props": {"font_family": "Renogare.ttf"}, "source_settings_expose": {"font_family": False}},
        {"id": "d", "props": {"font_family": "Body"}},  # exposed by default
        {"id": "e", "props": {"font_family": "Ghost"}},  # exposed, no file
        {"id": "f", "props": {"font_family": "Arial"}, "source_settings_expose": {"font_family": False}},
    ]
    css, lookup = tc._compile_spore_font_css(elements)
    rules = css.split("\n")
    assert rules[0] == ('@font-face { font-family: "spore-font-Renogare_ttf"; '
                        'src: url("/assets/default_assets/fonts/Renogare.ttf"); }')
    assert rules[1].startswith('@font-face { font-family: "spore-el-d"; ')
    assert len(rules) == 2
    assert lookup["Renogare"] == lookup["Renogare.ttf"] == "spore-font-Renogare_ttf"
    assert lookup["Body"] == "spore-el-d"
    assert "Arial" not in lookup and "Ghost" not in lookup
    assert tc._compile_spore_font_css(None) == ("", {})


# ---------------------------------------------------------------- gradients / fills

@pytest.mark.parametrize("value,expected", [
    ("linear-gradient(red, blue)", True), (" Repeating-Radial-Gradient(x)", True),
    ("conic-gradient(x)", True), ("#fff", False), (None, False), ("url(a.png)", False)])
def test_is_css_gradient(value, expected):
    assert tc._is_css_gradient(value) is expected


def test_fill_declarations():
    assert tc._fill_declarations("background-color", "#fff", {}) == [("background-color", "#fff")]
    grad = "linear-gradient(red, blue)"
    assert tc._fill_declarations("background-color", grad, {}) == [("background-image", grad)]
    props = {"background": "url(a.png) no-repeat, url(b.png)"}
    assert tc._fill_declarations("background-color", grad, props) == [
        ("background-image", f"url(a.png), url(b.png), {grad}")]


# ---------------------------------------------------------------- exposed styles

def test_exposed_style_keys():
    assert tc._exposed_style_keys(None) == []
    assert tc._exposed_style_keys({"legacy": True, "id": "a", "props": {"color": "red"}}) == []
    assert tc._exposed_style_keys({"id": "1bad", "props": {"color": "red"}}) == []
    el = {"id": "a", "props": {"color": "red", "background_color": "#000", "background": "url(x)",
                               "text_decoration": "underline", "text_underline": True,
                               "opacity": "", "font_size": 12, "border_width": 2},
          "source_settings_expose": {"border_width": False}}
    assert tc._exposed_style_keys(el) == ["color", "font_size"]
    assert tc._exposed_style_keys({"id": "a", "props": "bad"}) == []


def test_style_jinja_value_renders_and_strips():
    expr = tc._style_jinja_value("v", 12)
    assert expr.startswith("(v|default(12))")
    assert _jinja("{{ " + expr + " }}") == "12"
    assert _jinja("{{ " + tc._style_jinja_value("v", "red") + " }}", v="red;}{x") == "redx"
    assert _jinja("{{ " + tc._style_jinja_value("v", True) + " }}") == "True"


def test_exposed_style_css_renders_solid_and_gradient():
    els = [
        "junk",
        {"id": "a", "props": {"color": "#fff", "font_size": 20, "background_color": "#111"}},
        {"id": "b", "props": {"color": ""}},
    ]
    css = tc._exposed_style_css(els)
    assert css.startswith("#a { color: ")
    out = _jinja(css)
    assert "#a { color: #fff; background-color: #111; font-size: 20px; }" == out
    grad = _jinja(css, a_background_color="linear-gradient(90deg, red, blue)")
    assert "background-image: linear-gradient(90deg, red, blue)" in grad
    assert tc._exposed_style_css(None) == ""


# ---------------------------------------------------------------- _css_kv

def test_css_kv_non_dict():
    assert tc._css_kv("x") == []


def test_css_kv_full(fonts):
    props = {
        "text_underline": True, "text_strikethrough": True,
        "text": "skip", "src": "skip", "speed": 3,  # non-style keys
        "empty": "", "none": None,
        "vertical_align": "center",
        "line_height_px": 30,
        "font_size": 18, "padding": 4,
        "color": "#fff", "z_index": 3,
        "custom_thing": "x",
        "font_family": "Renogare",
    }
    out = tc._css_kv(props, font_map={"Renogare": "fam-r"})
    assert out[0] == ("text-decoration", "underline line-through")
    assert ("display", "flex") in out and ("justify-content", "center") in out
    assert ("line-height", "30px") in out and ("font-size", "18px") in out and ("padding", "4px") in out
    assert ("color", "#fff") in out and ("z-index", "3") in out and ("custom-thing", "x") in out
    assert ("font-family", "fam-r") in out
    assert not any(k in ("text", "src", "speed", "empty", "none") for k, _ in out)


@pytest.mark.parametrize("va,expected", [("bottom", "flex-end"), ("top", None)])
def test_css_kv_vertical_align(va, expected):
    out = tc._css_kv({"vertical_align": va})
    if expected:
        assert ("justify-content", expected) in out
    else:
        assert out == []


def test_css_kv_font_family_resolution(fonts):
    fmap = {"Renogare.ttf": "by-file", "Body": "by-stem"}
    assert tc._css_kv({"font_family": "renogare"}, font_map=fmap) == [("font-family", "by-file")]
    assert tc._css_kv({"font_family": "Body.woff"}, font_map=fmap) == [("font-family", "by-stem")]
    assert tc._css_kv({"font_family": "Arial"}, font_map=fmap) == [("font-family", "Arial")]
    assert tc._css_kv({"font_family": "Arial"}) == [("font-family", "Arial")]
    assert tc._css_kv({"font_family": "  "}, font_map=fmap) == []


def test_css_kv_background_and_gradient_order():
    grad = "linear-gradient(red, blue)"
    out = tc._css_kv({"background_color": grad, "background": "url(a.png)", "opacity": 0.5})
    assert out == [("background", "url(a.png)"), ("opacity", "0.5"),
                   ("background-image", f"url(a.png), {grad}")]
    assert tc._css_kv({"background_color": "#000"}) == [("background-color", "#000")]


def test_css_kv_skips_bound_keys():
    el = {"id": "a", "props": {"color": "#fff", "font_size": 12}}
    out = tc._css_kv(el["props"], element=el)
    assert out == []
    el2 = dict(el, source_settings_expose={"color": False, "font_size": False})
    assert tc._css_kv(el2["props"], element=el2) == [("color", "#fff"), ("font-size", "12px")]


# ---------------------------------------------------------------- visibility

def test_binding_has_show_action():
    assert tc._binding_has_show_action("x") is False
    assert tc._binding_has_show_action({"action": "show_for"}) is True
    assert tc._binding_has_show_action({"action": "hide", "chain": ["x", {"action": "show"}]}) is True
    assert tc._binding_has_show_action({"action": "hide", "chain": [{"action": "hide"}]}) is False


def test_element_start_hidden():
    assert tc._element_start_hidden({"start_hidden": 1}) is True
    assert tc._element_start_hidden({"start_hidden": False, "bindings": [{"action": "show"}]}) is False
    assert tc._element_start_hidden({"bindings": ["x", {"action": "hide"}, {"action": "show"}]}) is True
    assert tc._element_start_hidden({"bindings": None}) is False


# ---------------------------------------------------------------- placement / style

@pytest.mark.parametrize("ah,av,expected", [("left", "top", (0.0, 0.0)), ("CENTER", "center", (40.0, 15.0)),
                                            ("right", "bottom", (80.0, 30.0)), (None, None, (0.0, 0.0))])
def test_placement_base(ah, av, expected):
    assert tc._placement_base(100, 50, 20, 20, ah, av) == expected


def test_resolve_child_position():
    assert tc._resolve_child_position({"position": {"x": 3, "y": None}}, None) == (3, 0)
    assert tc._resolve_child_position({}, None) == (0, 0)
    parent = {"size": {"w": 100, "h": 50}}
    child = {"size": {"w": 20, "h": 10}, "placement": {"anchor_h": "center", "anchor_v": "bottom",
                                                       "offset_x": "5", "offset_y": -2}}
    assert tc._resolve_child_position(child, parent) == (45, 38)
    bad = {"size": {}, "placement": {"offset_x": "x", "offset_y": [1]}}
    assert tc._resolve_child_position(bad, {}) == (0, 0)
    legacy_child = {"position": {"x": 7, "y": 8}}
    assert tc._resolve_child_position(legacy_child, parent) == (7, 8)


def test_element_style():
    el = {"position": {"x": 1, "y": 2}, "size": {"w": 3}, "props": {"accent_color": "#f00",
                                                                    "accent_background": "#0f0"},
          "source_settings_expose": {}}
    assert tc._element_style(el) == ("left: 1px; top: 2px; width: 3px; --spore-accent: #f00; "
                                     "--spore-accent-bg: #0f0")
    assert tc._element_style({"size": {"h": 4}}) == "left: 0px; top: 0px; height: 4px"


# ---------------------------------------------------------------- data attributes

@pytest.mark.parametrize("vol,fin,fout,expected", [
    (None, None, None, ("1", "0", "0")), ("", "", "", ("1", "0", "0")), (2, 100, "250.7", ("1.0", "100", "250")),
    (-1, -5, "x", ("0.0", "0", "0")), ("bad", "x", None, ("1", "0", "0")), (0.25, 0, 0, ("0.25", "0", "0"))])
def test_fmt_audio_data_attrs(vol, fin, fout, expected):
    out = tc._fmt_audio_data_attrs(vol, fin, fout)
    assert out == (f'data-spore-audio-volume="{expected[0]}" data-spore-audio-fade-in="{expected[1]}" '
                   f'data-spore-audio-fade-out="{expected[2]}"')


def test_fmt_anim_data_attrs():
    assert tc._fmt_anim_data_attrs({}) == (
        'data-spore-anim-in="none" data-spore-anim-out="none" data-spore-anim-in-ms="300" '
        'data-spore-anim-out-ms="300" data-spore-anim-delay-ms="0" data-spore-anim-easing="ease-out"')
    out = tc._fmt_anim_data_attrs({"animations": {"anim_in": "fade", "anim_out": "warp", "anim_in_ms": "x",
                                                  "anim_out_ms": -5, "anim_delay_ms": "bad",
                                                  "anim_easing": " "}})
    assert 'data-spore-anim-in="fade"' in out and 'data-spore-anim-out="none"' in out
    assert 'data-spore-anim-in-ms="300"' in out and 'data-spore-anim-out-ms="0"' in out
    assert 'data-spore-anim-delay-ms="0"' in out and 'data-spore-anim-easing="ease-out"' in out
    out = tc._fmt_anim_data_attrs({"animations": {"anim_in": "bogus", "anim_out": "scaleOut", "anim_out_ms": "x",
                                                  "anim_delay_ms": 50, "anim_easing": "linear"}})
    assert 'anim-in="none"' in out and 'anim-out="scaleOut"' in out and 'out-ms="300"' in out
    assert 'delay-ms="50"' in out and 'easing="linear"' in out
    assert "anim-in=\"none\"" in tc._fmt_anim_data_attrs({"animations": "bad"})


# ---------------------------------------------------------------- text modes

@pytest.mark.parametrize("el,mode", [({"type": "counter"}, "counter"), ({"type": "data_display"}, "data_display"),
                                     ({"type": "text", "text_mode": " COUNTER "}, "counter"),
                                     ({"type": "text", "text_mode": "weird"}, "static"), ({}, "static"),
                                     (None, "static")])
def test_effective_text_mode(el, mode):
    assert tc._effective_text_mode(el) == mode


def test_wants_scroll():
    assert tc._wants_scroll({"type": "marquee"}) is True
    assert tc._wants_scroll({"type": "counter", "props": {"scroll": 1}}) is True
    assert tc._wants_scroll({"type": "data_display"}) is False
    assert tc._wants_scroll({"type": "text", "props": {"scroll": True}}) is False
    assert tc._wants_scroll(None) is False


def test_gradient_css():
    assert tc._gradient_css({}) == "linear-gradient(90deg, #a855f7 0%, #1e293b 100%)"
    props = {"color_stops": [{"color": "red", "stop": "10"}, "junk", {"stop": "x"}], "gradient_kind": "RADIAL"}
    assert tc._gradient_css(props) == "radial-gradient(circle, red 10%, #000000 0%)"
    assert tc._gradient_css({"color_stops": "bad", "angle": "x"}).startswith("linear-gradient(90deg")
    assert tc._gradient_css({"angle": "45.9"}).startswith("linear-gradient(45deg")


def test_text_element_display():
    assert tc._text_element_display({}, "t", "static", {"text": "Hi"}) == ("tText", "Hi")
    assert tc._text_element_display({"text_var": "Custom"}, "t", "static", {}) == ("Custom", "")
    el = {"counter": {"format": "{value}/{max}", "initial_value": 4, "max": 10}}
    assert tc._text_element_display(el, "c", "counter", {}) == ("c_format", "4/10")
    el = {"counter": {"progressive": True, "goals": [10, 20], "initial_value": 15}}
    assert tc._text_element_display(el, "c", "counter", {}) == ("c_format", "15")
    el = {"counter": {"progressive": True, "goals": [10, 20], "initial_value": 15, "format": "{value}/{max}"}}
    assert tc._text_element_display(el, "c", "counter", {})[1] == "15/20"
    el = {"counter": {"progressive": True, "goals": [], "format": "{value}/{max}", "max": 5}}
    assert tc._text_element_display(el, "c", "counter", {})[1] == "0/5"
    assert tc._text_element_display({"counter": "bad"}, "c", "counter", {})[1] == "0"
    assert tc._text_element_display({"data_display": {"default_text": ""}}, "d", "data_display", {}) == (
        "d_format", "")
    assert tc._text_element_display({"data_display": "bad"}, "d", "data_display", {}) == ("d_format", "—")


# ---------------------------------------------------------------- widget helpers

def test_widget_jinja_and_on_attr():
    expr = tc._widget_jinja("w", "k", "a\"b")
    assert expr == '{{ w_k|default("a\\"b") }}'
    assert _jinja(expr) == "a&#34;b"
    assert tc._on_attr({}, "x") == "true"
    assert tc._on_attr({}, "x", False) == "false"
    assert tc._on_attr({"x": " Off "}, "x") == "false"
    assert tc._on_attr({"x": "yes"}, "x") == "true"
    assert tc._on_attr({"x": 0}, "x") == "false"


def test_result_font_css_family(fonts):
    assert tc._result_font_css_family({}, {}) == "Arial"
    assert tc._result_font_css_family({}, {"result_font_family": " "}) == "Arial"
    assert tc._result_font_css_family({"id": "w"}, {"result_font_family": "Renogare"}) == "spore-el-w-result"
    el = {"id": "w", "source_settings_expose": {"result_font_family": False}}
    assert tc._result_font_css_family(el, {"result_font_family": "Renogare"}) == "spore-font-Renogare_ttf"


@pytest.mark.parametrize("value,expected", [(None, 7), ("", 7), ("3", 3), ("2.5", 2.5), ("x", 7), ([1], 7)])
def test_num_or(value, expected):
    assert tc._num_or(value, 7) == expected


def _widget(etype, props=None, **extra):
    el = {"id": "w", "type": etype, "props": props or {}}
    el.update(extra)
    return tc._render_widget_element(el, eid="w", etype=etype, style="left: 0px", classes="spore-element",
                                     hidden_attr="", anim_attrs="A")


def test_render_widget_unknown_returns_none():
    assert _widget("text") is None


def test_render_widget_list_and_confetti():
    html = _widget("list", {"max_items": 3, "row_lifetime_s": "x"})
    assert html.startswith('<div id="w" class="spore-element spore-widget spore-list" style="left: 0px" A ')
    assert 'data-spore-list-max="{{ w_max_items|default(3) }}"' in html
    assert 'data-spore-list-lifetime="{{ w_row_lifetime_s|default(0) }}"' in html
    assert html.endswith('<div class="spore-list-rows"></div></div>')
    conf = _widget("confetti", {"origin": "top"})
    assert 'data-spore-confetti-origin="{{ w_origin|default("top") }}"' in conf
    assert '<canvas class="spore-confetti-canvas"></canvas>' in conf


def test_render_widget_wheel(fonts):
    html = _widget("wheel", {"spin_ms": "bad", "variance": "no", "result_font_family": "Renogare"},
                   wheel_segments=[{"label": "A"}, "junk", {"label": 5, "color": "#123456"}])
    assert 'data-spore-wheel-ms="{{ w_spin_ms|default(4000) }}"' in html
    assert 'data-spore-wheel-variance="{{ w_variance|default("false") }}"' in html
    assert 'data-spore-wheel-result-font="spore-el-w-result"' in html
    segs = json.loads(_jinja(html.split('data-spore-wheel-segments="')[1].split('">')[0]).replace("&#34;", '"')
                      .replace("&quot;", '"'))
    assert segs == [{"label": "A", "color": "#a855f7"}, {"label": "5", "color": "#123456"}]
    default = _widget("wheel", {})
    assert "One" in default and "Two" in default


def test_render_widget_now_playing_qr_embed_stat():
    np = _widget("now_playing", {"show_art": False})
    assert 'data-spore-np-art="{{ w_show_art|default("false") }}"' in np
    assert 'data-spore-np-title="{{ w_show_title|default("true") }}"' in np
    qr = _widget("qr", {"text": "https://x"})
    assert 'data-spore-qr-text="{{ w_text|default("https://x") }}"' in qr
    emb = _widget("embed", {"scale": "x", "refresh_seconds": "y", "url": "https://e"})
    assert 'data-spore-embed-scale="{{ w_scale|default(1.0) }}"' in emb
    assert 'data-spore-embed-refresh="{{ w_refresh_seconds|default(0) }}"' in emb
    assert 'sandbox="allow-scripts allow-same-origin allow-popups"' in emb
    emb2 = _widget("embed", {"scale": "2", "refresh_seconds": "30"})
    assert "default(2.0)" in emb2 and "default(30)" in emb2
    stat = _widget("stat_card", {"label": "Subs"})
    assert 'data-spore-stat-label="{{ w_label|default("Subs") }}"' in stat


def test_render_widget_poll():
    html = _widget("poll", {"rotator_enabled": "off", "show_rank": True, "max_items": "4"})
    assert 'data-spore-poll-rotator="{{ w_rotator_enabled|default("false") }}"' in html
    assert 'data-spore-poll-rank="{{ w_show_rank|default("true") }}"' in html
    assert 'data-spore-poll-max="{{ w_max_items|default(4) }}"' in html
    assert 'data-spore-poll-dim="{{ w_dim_losers|default("true") }}"' in html
    assert html.endswith("</div>")
    rendered = _jinja(html)
    assert 'data-spore-poll-layout="vertical"' in rendered


# ---------------------------------------------------------------- _render_element per type

def _el(etype, props=None, **extra):
    el = {"id": "e1", "type": etype, "position": {"x": 1, "y": 2}, "size": {"w": 30, "h": 40},
          "props": props or {}}
    el.update(extra)
    return el


def test_render_text_counter_data_display():
    html = tc._render_element(_el("text", {"text": "Hello"}))
    assert html.endswith('data-spore-type="text" data-spore-text-mode="static">'
                         '{{ e1Text|default("Hello")|safe }}</div>')
    c = tc._render_element(_el("counter", counter={"format": "{value} pts", "initial_value": 2}))
    assert '{{ e1_format|default("2 pts")|safe }}' in c and 'data-spore-text-mode="counter"' in c
    d = tc._render_element(_el("data_display", data_display={"default_text": "?"}))
    assert '{{ e1_format|default("?")|safe }}' in d
    hidden = tc._render_element(_el("text", start_hidden=True))
    assert ' data-spore-hidden="true" ' in hidden


def test_render_image_static_and_counter():
    html = tc._render_element(_el("image", {"src": "/a.png"}))
    assert '<img id="e1"' in html and 'src="{{ e1Src|default("/a.png") }}"' in html
    html = tc._render_element(_el("image", {"src": "/p.png"}, src_var="Custom"))
    assert "{{ Custom|default" in html
    html = tc._render_element(_el("image", {"src": "/fallback.png"}, src_mode="from_counter",
                                  counter_src={"counter_id": "c 1"}))
    assert 'data-spore-src-mode="counter" data-spore-counter-id="c_1"' in html
    assert 'src="{{ e1_counter_default_src|default("/fallback.png") }}"' in html
    html = tc._render_element(_el("image", {}, src_mode="from_counter", counter_src="bad"))
    assert 'data-spore-counter-id="el"' in html


def test_render_video_variants():
    html = tc._render_element(_el("video", {"src": "/v.mp4", "autoplay": True, "loop": True,
                                            "audio_src": "/a.mp3", "audio_volume": 0.5}))
    assert '<video class="spore-video-visual" data-spore-type="video" autoplay loop muted playsinline>' in html
    assert '<source src="{{ e1Src|default("/v.mp4") }}">' in html
    assert '<audio id="e1_sporeAudio"' in html and 'data-spore-audio-volume="0.5"' in html
    assert '{{ e1AudioSrc|default("/a.mp3") }}' in html
    gif = tc._render_element(_el("video", {"visual_kind": "IMAGE", "muted": False, "src": "/g.gif"}))
    assert '<img class="spore-video-visual" src="{{ e1Src|default("/g.gif") }}"' in gif
    assert 'data-spore-visual-kind="image"' in gif
    odd = tc._render_element(_el("video", {"visual_kind": "hologram", "muted": False}))
    assert 'data-spore-visual-kind="video"' in odd and " muted " not in odd


def test_render_audio():
    html = tc._render_element(_el("audio", {"src": "/s.mp3", "volume": 0.3, "fade_in_ms": 100}))
    assert html.startswith('<audio id="e1" class="spore-element" data-spore-anim-in')
    assert 'data-spore-audio-volume="0.3" data-spore-audio-fade-in="100"' in html
    assert "style=" not in html


def test_render_progress_bar():
    html = tc._render_element(_el("progress_bar", {"border_radius": "x", "border_width": "y",
                                                   "fill_animation_duration_ms": "z"},
                                  progress_source={"counter_id": "c", "max_kind": "bogus"}))
    assert 'data-spore-counter-id="c" data-spore-max-kind="fixed">' in html
    assert "border-radius:8px" in html and "border:" not in html
    assert "{{ e1_fill_animation_duration_ms|default(500) }}ms" in html
    html = tc._render_element(_el("progress_bar", {"border_width": 2, "border_color": "#f00"},
                                  progress_source={"max_kind": "counter", "max_counter_id": "goal"}))
    assert 'data-spore-max-counter-id="goal"' in html
    assert 'border:2px solid {{ e1_border_color|default("#f00") }};' in html
    html = tc._render_element(_el("progress_bar", {}, progress_source={"max_kind": "counter"}))
    assert 'data-spore-max-counter-id="el"' in html
    rendered = _jinja(tc._render_element(_el("progress_bar", {"track_color": "#111", "fill_color": "#222"})))
    assert "background:#111;" in rendered and "background:#222;" in rendered


def test_render_shape():
    assert "border-radius:50%;" in tc._render_element(_el("shape", {"shape_kind": "ELLIPSE"}))
    assert "border-radius:6px;" in tc._render_element(_el("shape", {"border_radius": "6.4"}))
    plain = tc._render_element(_el("shape", {"shape_kind": "star", "border_radius": "x"}))
    assert 'data-spore-shape-kind="rectangle"' in plain and "border-radius:" not in plain.split("style=")[1].split('"')[1]


def test_render_divider():
    h = tc._render_element(_el("divider", {"thickness": 0}))
    assert 'data-spore-orientation="horizontal"' in h and "height:1px;width:100%;" in h
    v = tc._render_element(_el("divider", {"orientation": "Vertical", "thickness": "x", "color": "#abc"}))
    assert "background:#abc;width:2px;height:100%;" in v
    bad = tc._render_element(_el("divider", {"orientation": "diagonal"}))
    assert 'data-spore-orientation="horizontal"' in bad


def test_render_gradient():
    html = tc._render_element(_el("gradient", {"gradient_kind": "radial"}))
    assert "background:radial-gradient(circle," in html and 'data-spore-type="gradient"' in html


def test_render_marquee_and_scrolling_counter():
    html = tc._render_element(_el("marquee", {"text": "News", "speed": 0, "direction": "RIGHT", "gap": -5}))
    assert 'data-spore-type="marquee"' in html and 'data-spore-marquee-speed="1"' in html
    assert 'data-spore-marquee-direction="right"' in html and 'data-spore-marquee-gap="0"' in html
    assert html.count('{{ e1Text|default("News")|safe }}') == 2
    bad = tc._render_element(_el("marquee", {"speed": "x", "direction": "up", "gap": "y"}))
    assert 'speed="8"' in bad and 'direction="left"' in bad and 'gap="48"' in bad
    sc = tc._render_element(_el("counter", {"scroll": True}, counter={"initial_value": 9}))
    assert 'data-spore-type="counter"' in sc and "spore-marquee" in sc and "e1_format" in sc


def test_render_clock_and_timer():
    clock = tc._render_element(_el("clock", {"timezone": "OFFSET", "timezone_offset_minutes": "60"}))
    assert 'data-spore-clock-tz="offset" data-spore-clock-offset="60"' in clock
    assert '{{ e1_format|default("HH:mm:ss") }}' in clock
    clock = tc._render_element(_el("clock", {"timezone": "moon", "timezone_offset_minutes": "x"}))
    assert 'data-spore-clock-tz="local" data-spore-clock-offset="0"' in clock
    timer = tc._render_element(_el("timer", {"mode": "count_up", "limit_seconds": 90, "auto_start": False,
                                             "format": "{m}m"}))
    rendered = _jinja(timer)
    assert 'data-spore-timer-mode="count_up"' in rendered
    assert 'data-spore-timer-limit="90"' in rendered and 'data-spore-timer-start="300"' in rendered
    assert 'data-spore-timer-autostart="False"' in rendered and 'data-spore-timer-format="{m}m"' in rendered


def test_render_container_and_widget_dispatch():
    html = tc._render_element({"id": "box"}, children_inner_markup="<p>kid</p>")
    assert html.endswith('data-spore-type="container"><p>kid</p></div>')
    assert "spore-widget spore-qr" in tc._render_element(_el("qr"))


def test_render_element_accessible_after_jinja(fonts):
    """Every block type renders through Jinja without leaving template syntax behind."""
    for etype in ("text", "counter", "data_display", "marquee", "image", "video", "audio", "progress_bar",
                  "shape", "divider", "gradient", "clock", "timer", "list", "confetti", "wheel",
                  "now_playing", "qr", "embed", "stat_card", "poll", "container"):
        out = _jinja(tc._render_element(_el(etype, {"text": "x"})))
        assert "{{" not in out and "{%" not in out, etype
        assert 'id="e1"' in out, etype


def test_expose_field():
    assert tc._expose_field({}, "x") is True
    assert tc._expose_field({"source_settings_expose": "bad"}, "x") is True
    assert tc._expose_field({"source_settings_expose": {"y": False}}, "x") is True
    assert tc._expose_field({"source_settings_expose": {"x": 0}}, "x") is False

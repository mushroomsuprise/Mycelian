# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/template_reverse_parser.py (legacy template reverse parser).

Includes a round trip: build a model with every element type, compile it with
template_codegen, write it to a temp templates folder and reverse-parse it back.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import template_config_parser
from modules.spore_studio import template_codegen
from modules.spore_studio import template_reverse_parser as rp


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    (root / "template_configs").mkdir(parents=True)
    monkeypatch.setattr(rp, "get_template_path",
                        lambda rel="": os.path.join(str(root), rel) if rel else str(root))
    monkeypatch.setattr(template_config_parser, "get_data_path", lambda rel: str(tmp_path / rel))
    return root


def _write(root, name, html, config=None):
    (root / f"{name}.html").write_text(html, encoding="utf-8")
    if config is not None:
        (root / "template_configs" / f"{name}.json").write_text(json.dumps(config), encoding="utf-8")


# ---------------------------------------------------------------- CSS helpers

def test_strip_comments():
    assert rp._strip_comments("a/* x\n y */b/**/c") == "abc"


def test_split_top_rules_skips_at_rules_and_handles_nesting():
    css = """
    @import url(x.css);
    @charset "utf-8";
    @media (max-width: 10px) { #a { left: 1px; } }
    @keyframes k { from { opacity: 0 } to { opacity: 1 } }
    #a { left: 5px; }
    .b, .c { top: 2px; }
    .empty { }
    .nested { color: red; .inner { top: 1px } }
    .unterminated { left: 3px;
    """
    rules = rp._split_top_rules(css)
    assert rules[0] == ("#a", "left: 5px;")
    assert rules[1] == (".b, .c", "top: 2px;")
    assert rules[2][0] == ".nested" and ".inner" in rules[2][1]
    assert rules[3] == (".unterminated", "left: 3px;")
    assert len(rules) == 4


def test_split_top_rules_trailing_selector_without_brace():
    assert rp._split_top_rules("#a { left: 1px } dangling") == [("#a", "left: 1px")]
    assert rp._split_top_rules("@media x {") == []


def test_parse_declarations():
    assert rp._parse_declarations(" Left : 5px ;;color:red; bad; :x; y: ;a:b:c") == [
        ("left", "5px"), ("color", "red"), ("a", "b:c")]


@pytest.mark.parametrize(
    "selector,expected",
    [("#a", ("a", set(), None)),
     ("div.x.y", (None, {"x", "y"}, "div")),
     ("#root > .card:hover", (None, {"card"}, None)),
     ("ul li::before", (None, set(), "li")),
     ("a[href='x'] + SPAN#z.q", ("z", {"q"}, "span")),
     ("p:not(.x)", (None, set(), "p")),
     ("", (None, set(), None))],
)
def test_selector_atoms(selector, expected):
    assert rp._selector_atoms(selector) == expected


def test_selector_atoms_empty_split(monkeypatch):
    monkeypatch.setattr(rp.re, "split", lambda *a, **k: [])
    assert rp._selector_atoms("#a") == (None, set(), None)


# ---------------------------------------------------------------- config helpers

def test_build_vars_map():
    assert rp._build_vars_map({}) == {}
    assert rp._build_vars_map({"elements": "x"}) == {}
    cfg = {"elements": [{"type": "separator", "id": "S", "value": 1}, {"id": "A", "value": 5},
                        {"value": 3}, "junk", {"id": "B"}]}
    assert rp._build_vars_map(cfg) == {"A": 5, "B": None}


@pytest.mark.parametrize("value,expected",
                         [(None, None), ("", None), ("12.6", 13), (7, 7), ("x", None), ([1], None)])
def test_coerce_int(value, expected):
    assert rp._coerce_int(value) == expected


# ---------------------------------------------------------------- _resolve_decl

def test_resolve_decl_literal():
    assert rp._resolve_decl("10px", {}) == ("10px", None)


def test_resolve_decl_exact_binding():
    assert rp._resolve_decl("{{ Left }}px", {"Left": 12}) == ("12px", "Left")
    assert rp._resolve_decl("{{Col|default('#fff')}}", {"Col": "#000"}) == ("#000", "Col")


def test_resolve_decl_missing_or_empty_var():
    assert rp._resolve_decl("{{ Left }}px", {}) == (None, "Left")
    assert rp._resolve_decl("{{ Left }}px", {"Left": ""}) == (None, "Left")
    assert rp._resolve_decl("{{ A }} {{ B }}", {"A": 1}) == (None, "A")


def test_resolve_decl_compound_is_read_only():
    out = rp._resolve_decl("linear-gradient({{ A }}, {{ B }})", {"A": "red", "B": "blue"})
    assert out == ("linear-gradient(red, blue)", None)
    assert rp._resolve_decl("calc({{ A }}px + 2px)", {"A": 3}) == ("calc(3px + 2px)", None)


def test_resolve_decl_fractional_is_read_only():
    assert rp._resolve_decl("{{ W }}px", {"W": 10.5}) == ("10.5px", None)
    assert rp._resolve_decl("{{ W }}", {"W": 10.0}) == ("10.0", "W")


# ---------------------------------------------------------------- rule matching

def _d(prop, value, var=None):
    return rp._Decl(prop, value, var)


def test_select_rules_for():
    a = [_d("left", "1px")]
    b = [_d("top", "2px")]
    c = [_d("color", "red")]
    e = [_d("width", "9px")]
    rules = [("#x", a), (".card.big", b), ("span, div.card", c), ("*", [_d("x", "y")]),
             ("#other", e), ("p.card", e)]
    matched = rp._select_rules_for(rules, "x", {"card", "big"}, "div")
    assert matched == [a, b, c]
    assert rp._select_rules_for(rules, "y", {"card"}, "div") == [c]
    # tag None skips the tag check
    assert rp._select_rules_for([("p.card", e)], "y", {"card"}, None) == [e]


def test_decls_to_dict_cascade():
    first = [_d("left", "1px"), _d("top", "1px")]
    second = [_d("left", "2px")]
    out = rp._decls_to_dict([first, second])
    assert out["left"].value == "2px" and out["top"].value == "1px"


@pytest.mark.parametrize("value,expected",
                         [("12px", 12), ("-5.5px", -6), ("7", 7), ("3.4", 3), ("auto", None),
                          ("10%", None)])
def test_decl_pixel_value(value, expected):
    assert rp._decl_pixel_value(_d("x", value)) == expected


def test_decl_pixel_value_none():
    assert rp._decl_pixel_value(None) is None
    assert rp._decl_pixel_value(_d("x", None)) is None


def test_decl_pixel_value_bad_float(monkeypatch):
    class M:
        def group(self, _):
            return "nan-ish"

    monkeypatch.setattr(rp, "_PIXEL_RE", type("R", (), {"match": staticmethod(lambda t: M())}))
    assert rp._decl_pixel_value(_d("x", "1px")) is None


# ---------------------------------------------------------------- body parser

def test_body_parser_collects_ids_and_text():
    p = rp._BodyParser()
    p.feed(
        "<html><head><div id='ignored'></div></head><body>"
        "<div id='a' class='x y' style=' left:1px ' data-spore-hidden='TRUE'>Hello"
        "<span id='b'>  </span></div>"
        "<p>loose text</p>"
        "<script id='s'>var x = '<b id=no>';</script>"
        "<style id='st'>.x{}</style>"
        "<div id='c'><br></div>tail"
        "<img id='d' src='x'>"
        "</body><div id='after'></div>"
    )
    by_id = {r["id"]: r for r in p.found}
    assert list(by_id) == ["a", "b", "s", "st", "c", "d"]
    assert by_id["a"]["classes"] == {"x", "y"} and by_id["a"]["has_text"] is True
    assert by_id["a"]["style_attr"] == "left:1px" and by_id["a"]["data_spore_hidden"] is True
    assert by_id["b"]["has_text"] is False
    assert by_id["s"]["has_text"] is False and by_id["st"]["has_text"] is False
    assert by_id["d"]["tag"] == "img"


def test_body_parser_data_outside_open_tags():
    p = rp._BodyParser()
    p.feed("<body>text only</body>")
    assert p.found == []
    p.handle_data("x")  # not in body any more


# ---------------------------------------------------------------- file helpers

def test_read_template_html(tdir, monkeypatch, caplog):
    assert rp._read_template_html("none") is None
    _write(tdir, "a", "<html/>")
    assert rp._read_template_html("a") == "<html/>"
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("a.html"):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert rp._read_template_html("a") is None
    assert "cannot read" in caplog.text


def test_read_template_json(tdir, caplog):
    assert rp._read_template_json("none") is None
    _write(tdir, "a", "", {"elements": []})
    assert rp._read_template_json("a") == {"elements": []}
    (tdir / "template_configs" / "bad.json").write_text("{")
    assert rp._read_template_json("bad") is None
    assert "cannot read" in caplog.text


def test_extract_style_blocks():
    html = "<STYLE media='x'>a{}</STYLE><p></p><style>b{}</style>"
    assert rp._extract_style_blocks(html) == "a{}\nb{}"


def test_resolve_rules_drops_unresolved_and_empty():
    css = "/* c */ #a { left: {{ L }}px; top: {{ Missing }}px } #b { top: {{ Missing }}px }"
    rules = rp._resolve_rules(css, {"L": 4})
    assert len(rules) == 1
    sel, decls = rules[0]
    assert sel == "#a" and [(d.prop, d.value, d.var_id) for d in decls] == [("left", "4px", "L")]


def test_is_positioned():
    assert rp._is_positioned({}) is False
    assert rp._is_positioned({"position": _d("position", " Fixed ")}) is True
    assert rp._is_positioned({"position": _d("position", "absolute")}) is True
    assert rp._is_positioned({"position": _d("position", "relative")}) is False


def test_decls_to_inline():
    out = rp._decls_to_inline("left: 3px; color: red")
    assert out["left"].value == "3px" and out["left"].var_id is None


def test_design_size_fallbacks():
    assert rp._design_size({}) == (1920, 1080)
    cfg = {"elements": [{"id": "Width", "value": "300"}, {"id": "StreamHeight", "value": 200}]}
    assert rp._design_size(cfg) == (300, 200)
    cfg = {"elements": [{"id": "DesignWidth", "value": 640}, {"id": "Width", "value": 1},
                        {"id": "DesignHeight", "value": 360}]}
    assert rp._design_size(cfg) == (640, 360)


def test_classify_element():
    assert rp._classify_element({"tag": "img"}, {}) == "image"
    assert rp._classify_element({"tag": "video"}, {}) == "video"
    assert rp._classify_element({"tag": "audio"}, {}) == "audio"
    assert rp._classify_element({"tag": "div", "has_text": True}, {"font-size": _d("font-size", "1px")}) == "text"
    assert rp._classify_element({"tag": "div", "has_text": True}, {}) == "container"


# ---------------------------------------------------------------- _build_element

def _rec(**kw):
    rec = {"id": "el", "tag": "div", "has_text": False, "classes": set()}
    rec.update(kw)
    return rec


def test_build_element_without_geometry():
    assert rp._build_element(_rec(), {"color": _d("color", "red")}, 100, 100) is None


def test_build_element_left_top_with_bindings():
    decls = {
        "left": _d("left", "10px", "L"), "top": _d("top", "20px", "T"),
        "width": _d("width", "30px", "W"), "height": _d("height", "40px", "H"),
        "color": _d("color", "red", "C"), "background": _d("background", "blue", "BG"),
        "font-size": _d("font-size", "18px", "FS"), "border-radius": _d("border-radius", "4px", "BR"),
    }
    el = rp._build_element(_rec(has_text=True, data_spore_hidden=True), decls, 800, 600)
    assert el["position"] == {"x": 10, "y": 20} and el["size"] == {"w": 30, "h": 40}
    assert el["type"] == "text" and el["category"] == "Layout"
    assert el["props"] == {"color": "red", "background_color": "blue", "font_size": 18,
                           "border_radius": 4}
    assert el["legacy_bindings"] == {
        "position.x": "L", "position.y": "T", "size.w": "W", "size.h": "H",
        "props.color": "C", "props.background_color": "BG", "props.font_size": "FS",
        "props.border_radius": "BR"}
    assert el["legacy_anchor"] == {"x": "left", "y": "top"}
    assert el["start_hidden"] is True


def test_build_element_right_bottom_anchor_and_defaults():
    decls = {"right": _d("right", "10px", "R"), "bottom": _d("bottom", "20px", "B"),
             "background-color": _d("background-color", "#000"),
             "color": _d("color", "")}
    el = rp._build_element(_rec(), decls, 800, 600)
    # default size 200x80
    assert el["size"] == {"w": 200, "h": 80}
    assert el["position"] == {"x": 590, "y": 500}
    assert el["legacy_bindings"] == {"position.x": "R", "position.y": "B"}
    assert el["legacy_anchor"] == {"x": "right", "y": "bottom"}
    assert el["props"] == {"background_color": "#000"}
    assert "start_hidden" not in el


def test_build_element_clamps_and_missing_axes():
    el = rp._build_element(_rec(), {"right": _d("right", "900px"), "bottom": _d("bottom", "900px")}, 800, 600)
    assert el["position"] == {"x": 0, "y": 0}
    el = rp._build_element(_rec(), {"width": _d("width", "5px")}, 800, 600)
    assert el["position"] == {"x": 0, "y": 0} and el["legacy_bindings"] == {}


def test_build_element_unbound_values():
    decls = {"left": _d("left", "1px"), "top": _d("top", "2px"), "font-size": _d("font-size", "9px"),
             "border-radius": _d("border-radius", "3px")}
    el = rp._build_element(_rec(), decls, 10, 10)
    assert el["legacy_bindings"] == {}
    assert el["props"] == {"font_size": 9, "border_radius": 3}


# ---------------------------------------------------------------- reverse_parse_legacy

LEGACY_HTML = """<html><head>
<style>
  @media (max-width: 1px) { #title { left: 999px } }
  #title { position: absolute; left: {{ TitleLeft }}px; top: {{ TitleTop }}px;
           width: 300px; height: {{ TitleH }}px; font-size: {{ FontSize }}px; color: {{ Color }}; }
  .badge { position: absolute; right: {{ BadgeRight }}px; bottom: 10px; width: 50px; height: 50px; }
  #flow { position: relative; left: 5px; }
  #ghost { position: absolute; left: {{ Missing }}px; }
</style></head>
<body>
  <div id="title">{{ Message }}</div>
  <img id="logo" class="badge" src="x.png">
  <div id="inline" style="position: absolute; left: 7px; top: 8px">box</div>
  <div id="flow">flow</div>
  <div id="ghost"></div>
  <div id="title">duplicate</div>
  <div id="noxy" style="position:absolute"></div>
</body></html>"""

LEGACY_CFG = {"elements": [
    {"type": "separator", "label": "Layout"},
    {"id": "DesignWidth", "value": 1000}, {"id": "DesignHeight", "value": 500},
    {"id": "TitleLeft", "value": 40}, {"id": "TitleTop", "value": 30}, {"id": "TitleH", "value": 60},
    {"id": "FontSize", "value": 32}, {"id": "Color", "value": "#abcdef"},
    {"id": "BadgeRight", "value": 20}, {"id": "Missing", "value": ""},
]}


def test_reverse_parse_legacy_full(tdir):
    _write(tdir, "legacy", LEGACY_HTML, LEGACY_CFG)
    els = rp.reverse_parse_legacy("legacy")
    by_id = {e["id"]: e for e in els}
    assert list(by_id) == ["title", "logo", "inline"]
    t = by_id["title"]
    assert t["type"] == "text" and t["position"] == {"x": 40, "y": 30}
    assert t["size"] == {"w": 300, "h": 60}
    assert t["props"] == {"color": "#abcdef", "font_size": 32}
    assert t["legacy_bindings"] == {"position.x": "TitleLeft", "position.y": "TitleTop",
                                    "size.h": "TitleH", "props.color": "Color",
                                    "props.font_size": "FontSize"}
    logo = by_id["logo"]
    assert logo["type"] == "image"
    assert logo["position"] == {"x": 1000 - 20 - 50, "y": 500 - 10 - 50}
    assert logo["legacy_anchor"] == {"x": "right", "y": "bottom"}
    assert by_id["inline"]["position"] == {"x": 7, "y": 8}
    assert by_id["inline"]["legacy_bindings"] == {}
    assert rp.design_size_from_config("legacy") == (1000, 500)


def test_reverse_parse_legacy_none_cases(tdir):
    assert rp.reverse_parse_legacy("missing") is None
    _write(tdir, "empty", "")
    assert rp.reverse_parse_legacy("empty") is None
    _write(tdir, "noids", "<body><div>x</div></body>")
    assert rp.reverse_parse_legacy("noids") is None
    _write(tdir, "flowonly", "<body><div id='a' style='left:1px'>x</div></body>")
    assert rp.reverse_parse_legacy("flowonly") is None
    assert rp.design_size_from_config("missing") == (1920, 1080)


# ---------------------------------------------------------------- round trip through codegen

ALL_TYPES = [
    "text", "counter", "data_display", "marquee", "image", "video", "audio", "progress_bar",
    "shape", "divider", "gradient", "clock", "timer", "list", "confetti", "wheel",
    "now_playing", "qr", "embed", "stat_card", "poll", "container",
]
# Elements the overlay doesn't place on the canvas.
_UNPLACED = {"audio"}


def _round_trip_model():
    elements = []
    for i, etype in enumerate(ALL_TYPES):
        elements.append({
            "id": f"e_{etype}", "type": etype,
            "position": {"x": 10 + i * 3, "y": 5 + i * 7},
            "size": {"w": 100 + i, "h": 40 + i},
            "props": {"text": f"T{i}"},
            "animations": {"anim_in": "fade", "anim_out": "slideOut", "anim_in_ms": 200},
            "bindings": [{"event": "next_alert", "action": "show_for", "args": {"seconds": 2}}],
        })
        if etype in ("shape", "divider"):
            elements[-1]["start_hidden"] = etype == "shape"
    elements.append({"id": "child", "type": "text", "parent_id": "e_container",
                     "position": {"x": 3, "y": 4}, "size": {"w": 20, "h": 10},
                     "props": {"text": "c"}})
    elements.append({"id": "anchored", "type": "shape", "parent_id": "e_container",
                     "placement": {"anchor_h": "right", "anchor_v": "bottom",
                                   "offset_x": -2, "offset_y": -3},
                     "size": {"w": 20, "h": 10}, "props": {}})
    return {"template_name": "roundtrip", "alert_system": "queue",
            "design": {"width": 640, "height": 360}, "elements": elements, "advanced_js": ""}


_COMPILED = {}


@pytest.fixture
def compiled_round_trip(tdir):
    model = _round_trip_model()
    if "out" not in _COMPILED:  # compile once; each test still parses its own copy
        _COMPILED["out"] = template_codegen.compile_model(_round_trip_model())
    html, cfg = _COMPILED["out"]
    _write(tdir, "roundtrip", html, cfg)
    parsed = rp.reverse_parse_legacy("roundtrip")
    return model, cfg, {e["id"]: e for e in parsed}


def _expected_geometry(model, eid):
    by_id = {e["id"]: e for e in model["elements"]}
    el = by_id[eid]
    if eid == "anchored":
        parent = by_id["e_container"]
        x = parent["size"]["w"] - el["size"]["w"] - 2
        y = parent["size"]["h"] - el["size"]["h"] - 3
        return {"x": x, "y": y}, el["size"]
    return el["position"], el["size"]


@pytest.mark.parametrize("etype", [t for t in ALL_TYPES if t not in _UNPLACED])
def test_round_trip_geometry_per_type(compiled_round_trip, etype):
    model, _cfg, parsed = compiled_round_trip
    eid = f"e_{etype}"
    assert eid in parsed, f"{etype} was not recovered as an absolutely positioned element"
    pos, size = _expected_geometry(model, eid)
    assert parsed[eid]["position"] == pos
    assert parsed[eid]["size"] == size


def test_round_trip_children_and_flags(compiled_round_trip):
    model, cfg, parsed = compiled_round_trip
    assert "e_audio" not in parsed
    for eid in ("child", "anchored"):
        pos, size = _expected_geometry(model, eid)
        assert parsed[eid]["position"] == pos and parsed[eid]["size"] == size
    assert parsed["e_image"]["type"] == "image"
    assert parsed["e_shape"].get("start_hidden") is True
    assert parsed["e_text"].get("start_hidden") is True  # show_for binding starts hidden
    assert "start_hidden" not in parsed["e_divider"]  # explicit False wins over the binding
    assert parsed["child"].get("start_hidden") is None
    assert rp.design_size_from_config("roundtrip") == (640, 360)

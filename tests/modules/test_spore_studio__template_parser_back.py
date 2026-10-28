# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/template_parser_back.py (sidecar load/save, legacy fallback).

Includes a save -> parse_existing round trip through save_pipeline in a temp sandbox.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import modules
from modules import notification_engine, path_utils, template_config_parser
from modules.spore_studio import containers, save_pipeline, template_outputs
from modules.spore_studio import template_parser_back as pb
from modules.spore_studio import template_reverse_parser


class Sandbox(SimpleNamespace):
    def html(self, name):
        return self.templates / f"{name}.html"

    def config(self, name):
        return self.templates / "template_configs" / f"{name}.json"

    def sidecar(self, name):
        return self.templates / "_spore" / f"{name}.spore.json"

    def legacy_sidecar(self, name):
        return self.templates / "template_configs" / f"{name}.spore.json"

    def write_config(self, name, cfg):
        self.config(name).write_text(json.dumps(cfg) if not isinstance(cfg, str) else cfg,
                                     encoding="utf-8")

    def write_sidecar(self, name, model, legacy=False):
        p = self.legacy_sidecar(name) if legacy else self.sidecar(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(model) if not isinstance(model, str) else model, encoding="utf-8")


@pytest.fixture
def sb(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    assets = tmp_path / "assets"
    (templates / "template_configs").mkdir(parents=True)
    assets.mkdir()

    def tpl_path(rel=""):
        return os.path.join(str(templates), rel) if rel else str(templates)

    for mod in (pb, save_pipeline, template_reverse_parser, containers, path_utils):
        monkeypatch.setattr(mod, "get_template_path", tpl_path)
    monkeypatch.setattr(save_pipeline, "get_assets_path",
                        lambda rel="": os.path.join(str(assets), rel) if rel else str(assets))
    monkeypatch.setattr(template_config_parser, "get_data_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(modules, "web_engine", SimpleNamespace(web_engine_instance=None), raising=False)
    notes = []
    monkeypatch.setattr(notification_engine, "notify",
                        lambda *a, **k: notes.append(dict(k, message=a[0] if a else "")))
    invalidate = template_outputs.invalidate_cache
    invalidate()
    yield Sandbox(templates=templates, assets=assets, notes=notes, tmp=tmp_path)
    invalidate()


def _deny_open(monkeypatch, suffix):
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith(suffix):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)


# ---------------------------------------------------------------- hydrate streamdeck

def test_hydrate_streamdeck_keeps_existing_actions(sb):
    side = {"streamdeck_options": {"actions": {"a": {"event": "x"}}}}
    sb.write_config("t", {"streamdeck_options": {"actions": {"b": {"event": "y"}}}})
    pb._hydrate_streamdeck_from_public_config("t", side)
    assert side["streamdeck_options"] == {"actions": {"a": {"event": "x"}}}


@pytest.mark.parametrize(
    "cfg",
    [None, "{broken", [1], {"streamdeck_options": "x"},
     {"streamdeck_options": {"description": "", "actions": {"bad": "spec"}}}],
)
def test_hydrate_streamdeck_noop_cases(sb, cfg):
    if cfg is not None:
        sb.write_config("t", cfg)
    side = {"streamdeck_options": {"actions": {}}}
    pb._hydrate_streamdeck_from_public_config("t", side)
    assert side == {"streamdeck_options": {"actions": {}}}


def test_hydrate_streamdeck_copies_sanitized(sb):
    sb.write_config("t", {"streamdeck_options": {"description": "D", "actions": {
        "Do It": {"event": "ev", "default_data": {"k": 1}}}}})
    side = {}
    pb._hydrate_streamdeck_from_public_config("t", side)
    assert side["streamdeck_options"]["description"] == "D"
    (aid, spec), = side["streamdeck_options"]["actions"].items()
    assert spec["event"] == "ev" and spec["default_data"] == {"k": 1}


# ---------------------------------------------------------------- hydrate dynamic controls

def test_hydrate_dynamic_controls(sb):
    side = {"dynamic_controls": {"elements": [1]}}
    pb._hydrate_dynamic_controls_from_public_config("t", side)
    assert side == {"dynamic_controls": {"elements": [1]}}
    side = {}
    pb._hydrate_dynamic_controls_from_public_config("t", side)  # no config
    assert side == {}
    for bad in ("{x", "[1]", json.dumps({"dynamic_controls": [1]})):
        sb.write_config("t", bad)
        pb._hydrate_dynamic_controls_from_public_config("t", side)
        assert side == {}
    sb.write_config("t", {"dynamic_controls": {"elements": [{"id": "b"}]}})
    pb._hydrate_dynamic_controls_from_public_config("t", side)
    assert side == {"dynamic_controls": {"elements": [{"id": "b"}]}}


# ---------------------------------------------------------------- hydrate advanced js

USER_HTML = "<script>\n// SPORE_STUDIO:user-begin\n  alert(1);\n// SPORE_STUDIO:user-end\n</script>"


def test_hydrate_advanced_js(sb, monkeypatch, caplog):
    side = {"advanced_js": "old"}
    pb._hydrate_advanced_js_from_html("t", side)  # no html
    assert side["advanced_js"] == "old"
    sb.html("t").write_text(USER_HTML)
    pb._hydrate_advanced_js_from_html("t", side)
    assert side["advanced_js"] == "alert(1);"
    sb.html("e").write_text("<html></html>")
    side2 = {"advanced_js": "keep"}
    pb._hydrate_advanced_js_from_html("e", side2)  # nothing running -> keep sidecar
    assert side2["advanced_js"] == "keep"
    _deny_open(monkeypatch, "t.html")
    side3 = {}
    pb._hydrate_advanced_js_from_html("t", side3)
    assert side3 == {} and "Advanced JS" in caplog.text


# ---------------------------------------------------------------- hydrate canvas timing

def test_hydrate_canvas_timing(sb):
    side = {"duration_seconds": 3, "queued": False}
    pb._hydrate_canvas_timing_from_public_config("t", side)
    assert side == {"duration_seconds": 3, "queued": False}
    side = {}
    pb._hydrate_canvas_timing_from_public_config("t", side)  # no config
    assert side == {}
    for bad in ("{x", "[1]"):
        sb.write_config("t", bad)
        pb._hydrate_canvas_timing_from_public_config("t", side)
        assert side == {}
    sb.write_config("t", {"elements": [{"id": "Duration", "value": "7.5"},
                                       {"id": "Queued", "value": 1}]})
    pb._hydrate_canvas_timing_from_public_config("t", side)
    assert side == {"duration_seconds": 7.5, "queued": True}


def test_hydrate_canvas_timing_bad_and_missing_values(sb):
    sb.write_config("t", {"elements": [{"id": "Duration", "value": "abc"}]})
    side = {"queued": None}
    pb._hydrate_canvas_timing_from_public_config("t", side)
    assert side == {"queued": None}
    sb.write_config("t", {"elements": [{"id": "Duration", "value": ""}]})
    side = {"queued": True}
    pb._hydrate_canvas_timing_from_public_config("t", side)
    assert side == {"queued": True}


# ---------------------------------------------------------------- sidecar paths / migration

def test_sidecar_paths(sb):
    assert pb._spore_sidecar_path("a") == str(sb.sidecar("a"))
    assert pb._legacy_sidecar_path("a") == str(sb.legacy_sidecar("a"))


def test_migrate_legacy_sidecar(sb, caplog):
    pb._migrate_legacy_sidecar("a")  # nothing to do
    sb.write_sidecar("a", {"x": 1}, legacy=True)
    pb._migrate_legacy_sidecar("a")
    assert sb.sidecar("a").exists() and not sb.legacy_sidecar("a").exists()
    # new already exists -> legacy copy left alone
    sb.write_sidecar("a", {"x": 2}, legacy=True)
    pb._migrate_legacy_sidecar("a")
    assert sb.legacy_sidecar("a").exists()
    assert json.loads(sb.sidecar("a").read_text())["x"] == 1


def test_migrate_legacy_sidecar_error(sb, monkeypatch, caplog):
    sb.write_sidecar("a", {"x": 1}, legacy=True)

    def boom(*_a):
        raise OSError("ro")

    monkeypatch.setattr(pb.os, "replace", boom)
    pb._migrate_legacy_sidecar("a")
    assert "Could not migrate" in caplog.text and sb.legacy_sidecar("a").exists()


def test_migrate_all_sidecars(sb, monkeypatch):
    sb.write_sidecar("a", {}, legacy=True)
    sb.write_sidecar("b", {}, legacy=True)
    sb.write_sidecar("c", {}, legacy=True)
    sb.write_sidecar("c", {})  # already migrated
    (sb.templates / "template_configs" / ".spore.json").write_text("{}")  # empty stem
    assert pb.migrate_all_sidecars() == 2
    assert pb.migrate_all_sidecars() == 0


def test_migrate_all_sidecars_no_legacy_dir(sb, monkeypatch):
    monkeypatch.setattr(pb, "get_template_path", lambda rel="": str(sb.tmp / "nowhere" / rel))
    assert pb.migrate_all_sidecars() == 0


# ---------------------------------------------------------------- load / save / remove

def test_load_sidecar(sb, caplog):
    assert pb.load_sidecar("none") is None
    sb.write_sidecar("a", {"elements": []})
    assert pb.load_sidecar("a") == {"elements": []}
    sb.write_sidecar("list", "[1]")
    assert pb.load_sidecar("list") is None
    sb.write_sidecar("bad", "{oops")
    assert pb.load_sidecar("bad") is None
    assert "could not be loaded" in caplog.text
    sb.write_sidecar("old", {"v": 1}, legacy=True)
    assert pb.load_sidecar("old") == {"v": 1}


def test_save_sidecar(sb):
    assert pb.save_sidecar("a", {"name": "é"}) is True
    assert json.loads(sb.sidecar("a").read_text(encoding="utf-8")) == {"name": "é"}
    assert not Path(str(sb.sidecar("a")) + ".tmp").exists()


def test_save_sidecar_failure_cleans_tmp(sb, monkeypatch, caplog):
    def boom(*_a):
        raise OSError("full")

    monkeypatch.setattr(pb.os, "replace", boom)
    assert pb.save_sidecar("a", {"x": 1}) is False
    assert not Path(str(sb.sidecar("a")) + ".tmp").exists()
    assert "Failed to save spore sidecar" in caplog.text


def test_save_sidecar_failure_tmp_unlink_error(sb, monkeypatch):
    monkeypatch.setattr(pb.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert pb.save_sidecar("a", {}) is False


def test_remove_sidecar(sb, monkeypatch, caplog):
    sb.write_sidecar("a", {})
    sb.write_sidecar("a", {}, legacy=True)
    pb.remove_sidecar("a")
    assert not sb.sidecar("a").exists() and not sb.legacy_sidecar("a").exists()
    pb.remove_sidecar("a")  # nothing left; no error
    sb.write_sidecar("b", {})

    def boom(_p):
        raise OSError("busy")

    monkeypatch.setattr(pb.os, "remove", boom)
    pb.remove_sidecar("b")
    assert "Could not remove sidecar" in caplog.text


# ---------------------------------------------------------------- legacy synthesis

def test_synthesize_legacy_elements(sb, caplog):
    assert pb._synthesize_legacy_elements("none") == []
    sb.write_config("bad", "{x")
    assert pb._synthesize_legacy_elements("bad") == []
    assert "could not be parsed" in caplog.text
    sb.write_config("noel", {"elements": "x"})
    assert pb._synthesize_legacy_elements("noel") == []
    sb.write_config("t", {"elements": [
        {"id": "First", "type": "number", "value": 1},
        {"type": "separator", "label": "  Colors "},
        {"id": "Bg", "type": "COLOR", "value": "#000"},
        {"type": "separator", "label": ""},
        {"id": "Odd", "type": "fancy", "value": None},
        {"type": "text"},
        "junk",
    ]})
    out = pb._synthesize_legacy_elements("t")
    assert [(e["id"], e["type"], e["category"]) for e in out] == [
        ("First", "text", "Settings"), ("Bg", "text", "Colors"), ("Odd", "text", "Colors")]
    assert out[1]["props"] == {"value": "#000"} and out[1]["legacy_field"]["id"] == "Bg"
    assert out[0]["position"] is None and out[0]["size"] is None and out[0]["bindings"] == []


def test_has_public_config(sb, monkeypatch):
    assert pb._has_public_config("t") is False
    sb.write_config("t", {})
    assert pb._has_public_config("t") is True
    monkeypatch.setattr(template_config_parser.TemplateConfigParser, "get_config_path",
                        lambda self, n: (_ for _ in ()).throw(RuntimeError("x")))
    assert pb._has_public_config("t") is False


def test_warn_damaged_sidecar(sb, monkeypatch):
    pb._warn_damaged_sidecar("t")
    (note,) = sb.notes
    assert note["type"] == "warning" and note["details"] == str(sb.sidecar("t"))
    assert note["dedupe_key"] == "spore:sidecar_damaged:t"

    def boom(*a, **k):
        raise RuntimeError

    monkeypatch.setattr(notification_engine, "notify", boom)
    pb._warn_damaged_sidecar("t")  # swallowed


# ---------------------------------------------------------------- parse_existing

def test_parse_existing_sidecar_defaults_and_hydration(sb):
    sb.write_sidecar("t", {"elements": [{"id": "a", "type": "text", "text_mode": "static"}]})
    sb.write_config("t", {"elements": [{"id": "Duration", "value": 9}, {"id": "Queued", "value": True}],
                          "streamdeck_options": {"actions": {"go": {"event": "t_go"}}},
                          "dynamic_controls": {"elements": [{"id": "c"}]}})
    sb.html("t").write_text(USER_HTML)
    model = pb.parse_existing("t")
    assert model["template_name"] == "t" and model["alert_system"] == "queue"
    assert model["design"] == {"width": 800, "height": 200}
    assert model["advanced_js"] == "alert(1);"
    assert model["duration_seconds"] == 9.0 and model["queued"] is True
    assert model["streamdeck_options"]["actions"]["go"]["event"] == "t_go"
    assert model["dynamic_controls"] == {"elements": [{"id": "c"}]}
    assert "legacy" not in model


def test_parse_existing_sidecar_minimal(sb):
    sb.write_sidecar("t", {"alert_system": "instant"})
    model = pb.parse_existing("t")
    assert model["alert_system"] == "instant"
    assert model["dynamic_controls"] == {"elements": []}
    assert model["streamdeck_options"] == {"description": "", "actions": {}}


def test_parse_existing_damaged_sidecar_warns_and_falls_back(sb):
    sb.write_sidecar("t", "{damaged")
    sb.html("t").write_text("<body><p>hand made</p></body>")
    sb.write_config("t", {"elements": [{"id": "Msg", "type": "text", "value": "hi"}]})
    model = pb.parse_existing("t")
    assert model["legacy"] is True and model["legacy_source"] == "json_config"
    assert [e["id"] for e in model["elements"]] == ["Msg"]
    assert sb.notes and sb.notes[0]["dedupe_key"] == "spore:sidecar_damaged:t"


def test_parse_existing_missing_raises(sb):
    with pytest.raises(FileNotFoundError, match="No template named 'ghost'"):
        pb.parse_existing("ghost")


def test_parse_existing_config_only(sb):
    sb.write_config("cfgonly", {"elements": [{"id": "A", "value": 1}]})
    model = pb.parse_existing("cfgonly")
    assert model["advanced_js"] == "" and model["legacy_source"] == "json_config"


def test_parse_existing_html_unreadable(sb, monkeypatch, caplog):
    sb.html("t").write_text("x")
    _deny_open(monkeypatch, "t.html")
    model = pb.parse_existing("t")
    assert model["advanced_js"] == "" and "Could not read legacy template" in caplog.text


def test_parse_existing_html_parsed(sb):
    sb.html("old").write_text(
        "<style>#box { position: absolute; left: {{ BoxX }}px; top: 5px; width: 10px; height: 10px }</style>"
        "<body><div id='box'></div></body>" + USER_HTML)
    sb.write_config("old", {"elements": [{"id": "BoxX", "value": 33}, {"id": "DesignWidth", "value": 500},
                                         {"id": "DesignHeight", "value": 250}]})
    model = pb.parse_existing("old")
    assert model["legacy_source"] == "html_parsed" and model["legacy"] is True
    assert model["design"] == {"width": 500, "height": 250}
    assert model["elements"][0]["position"] == {"x": 33, "y": 5}
    assert model["elements"][0]["legacy_bindings"] == {"position.x": "BoxX"}
    assert model["advanced_js"] == "alert(1);"


# ---------------------------------------------------------------- round trips

def _rich_model(name="rt"):
    return {
        "template_name": name,
        "alert_system": "instant",
        "design": {"width": 1280, "height": 720},
        "duration_seconds": 8,
        "queued": True,
        "advanced_js": "console.log('hi');",
        "elements": [
            {"id": "title", "type": "text", "position": {"x": 1, "y": 2}, "size": {"w": 300, "h": 50},
             "props": {"text": "Hello {username}", "font_size": 30, "color": "#fff"},
             "animations": {"anim_in": "fade", "anim_out": "fade", "anim_in_ms": 250},
             "bindings": [{"event": "instant_alert", "action": "show_for",
                           "args": {"seconds": 3, "anim_in": "fade", "anim_out": "fade"},
                           "chain": [{"delay_ms": 100, "action": "flash_class",
                                      "args": {"duration_ms": 300}}]}]},
            {"id": "pic", "type": "image", "position": {"x": 10, "y": 60}, "size": {"w": 64, "h": 64},
             "props": {"src": "/assets/rt/pic.png"}},
            {"id": "box", "type": "container", "position": {"x": 400, "y": 10},
             "size": {"w": 200, "h": 100}, "props": {}},
            {"id": "inner", "type": "shape", "parent_id": "box", "position": {"x": 5, "y": 5},
             "size": {"w": 20, "h": 20}, "props": {"background_color": "#f00"}},
        ],
        "streamdeck_options": {"description": "Deck", "actions": {
            "go": {"name": "Go", "description": "", "event": "rt_go", "default_data": {}}}},
        "dynamic_controls": {"elements": [{"id": "btn", "type": "button", "label": "Press"}]},
    }


def test_round_trip_save_then_parse_existing(sb):
    model = _rich_model()
    saved = save_pipeline.save_template(copy.deepcopy(model))
    loaded = pb.parse_existing("rt")
    for key in ("template_name", "alert_system", "design", "duration_seconds", "queued",
                "advanced_js", "dynamic_controls"):
        assert loaded[key] == model[key], key
    assert loaded["streamdeck_options"]["actions"]["go"]["event"] == "rt_go"
    assert loaded["elements"] == saved["elements"]
    assert [e["id"] for e in loaded["elements"]] == ["title", "pic", "box", "inner"]
    assert loaded["elements"][0]["bindings"][0]["chain"][0]["action"] == "flash_class"
    # Saving what was loaded produces the same overlay again
    html1 = sb.html("rt").read_text(encoding="utf-8")
    save_pipeline.save_template(copy.deepcopy(loaded))
    assert sb.html("rt").read_text(encoding="utf-8") == html1


def test_round_trip_lost_sidecar_reverse_parses_geometry(sb):
    model = _rich_model("lost")
    save_pipeline.save_template(copy.deepcopy(model))
    sb.sidecar("lost").unlink()
    loaded = pb.parse_existing("lost")
    assert loaded["legacy"] is True and loaded["legacy_source"] == "html_parsed"
    assert loaded["design"] == {"width": 1280, "height": 720}
    assert loaded["advanced_js"] == "console.log('hi');"
    by_id = {e["id"]: e for e in loaded["elements"]}
    for el in model["elements"]:
        assert by_id[el["id"]]["position"] == el["position"]
        assert by_id[el["id"]]["size"] == el["size"]


def test_migrate_all_sidecars_skips_empty_stem(sb, monkeypatch):
    monkeypatch.setattr(pb.glob, "glob", lambda pattern: [os.path.join("x", ".spore.json")])
    assert pb.migrate_all_sidecars() == 0

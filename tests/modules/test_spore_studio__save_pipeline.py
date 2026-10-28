# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/save_pipeline.py (create / save / delete / list templates).

Every write goes into a temp ``templates`` / ``assets`` sandbox.
"""

from __future__ import annotations

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
from modules.spore_studio import containers, save_pipeline as sp
from modules.spore_studio import template_outputs, template_parser_back, template_reverse_parser


class Sandbox(SimpleNamespace):
    def html(self, name):
        return self.templates / f"{name}.html"

    def config(self, name):
        return self.templates / "template_configs" / f"{name}.json"

    def sidecar(self, name):
        return self.templates / "_spore" / f"{name}.spore.json"

    def write_config(self, name, cfg):
        self.config(name).parent.mkdir(parents=True, exist_ok=True)
        self.config(name).write_text(json.dumps(cfg), encoding="utf-8")

    def write_sidecar(self, name, model):
        self.sidecar(name).parent.mkdir(parents=True, exist_ok=True)
        self.sidecar(name).write_text(json.dumps(model), encoding="utf-8")


@pytest.fixture
def sb(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    assets = tmp_path / "assets"
    (templates / "template_configs").mkdir(parents=True)
    assets.mkdir()

    def tpl_path(rel=""):
        return os.path.join(str(templates), rel) if rel else str(templates)

    def assets_path(rel=""):
        return os.path.join(str(assets), rel) if rel else str(assets)

    for mod in (sp, template_parser_back, template_reverse_parser, containers):
        monkeypatch.setattr(mod, "get_template_path", tpl_path)
    monkeypatch.setattr(path_utils, "get_template_path", tpl_path)
    monkeypatch.setattr(sp, "get_assets_path", assets_path)
    monkeypatch.setattr(path_utils, "get_assets_path", assets_path)
    monkeypatch.setattr(template_config_parser, "get_data_path", lambda rel: str(tmp_path / rel))
    engine = SimpleNamespace(web_engine_instance=None)
    monkeypatch.setattr(modules, "web_engine", engine, raising=False)
    notes = []
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: notes.append(dict(k, message=a[0] if a else "")))
    invalidate = template_outputs.invalidate_cache
    invalidate()
    yield Sandbox(templates=templates, assets=assets, engine=engine, notes=notes, tmp=tmp_path)
    invalidate()


def _model(name="demo", **extra):
    model = {
        "template_name": name,
        "alert_system": "queue",
        "design": {"width": 640, "height": 360},
        "duration_seconds": 5,
        "queued": False,
        "elements": [
            {"id": "title", "type": "text", "position": {"x": 10, "y": 20},
             "size": {"w": 200, "h": 40}, "props": {"text": "Hello", "font_size": 24}},
        ],
        "advanced_js": "",
    }
    model.update(extra)
    return model


# ---------------------------------------------------------------- small helpers

def test_is_codegen_owned_config_value():
    assert sp._is_codegen_owned_config_value("DesignWidth")
    assert sp._is_codegen_owned_config_value("DesignHeight")
    assert sp._is_codegen_owned_config_value("cnt_format")
    assert not sp._is_codegen_owned_config_value("cnt_color")


def test_clone_deep_and_fallback():
    src = {"a": [1, {"b": 2}]}
    out = sp._clone(src)
    assert out == src and out is not src and out["a"][1] is not src["a"][1]
    obj = object()
    assert sp._clone(obj) is obj


def test_merge_element_arrays_non_lists_and_no_ids():
    assert sp._merge_element_arrays({"a": 1}, []) == {"a": 1}
    assert sp._merge_element_arrays([1], "x") == [1]
    assert sp._merge_element_arrays([{"x": 1}], [{"id": "a", "value": 2}]) == [{"x": 1}]
    assert sp._merge_element_arrays([{"id": "a", "value": 1}], [{"x": 1}]) == [{"id": "a", "value": 1}]


def test_merge_element_arrays_transfers_values():
    new = [
        {"type": "separator", "label": "S"},
        {"id": "color", "value": "#000", "label": "new label"},
        {"id": "cnt_format", "value": "{value}/{max}"},
        {"id": "DesignWidth", "value": 400},
        {"id": "nested", "value": 1, "elements": [{"id": "inner", "value": "n"}]},
        {"id": "novalue"},
        {"id": "fresh", "value": 5},
    ]
    old = [
        {"id": "color", "value": "#fff"},
        {"id": "color", "value": "dup ignored"},
        {"id": "cnt_format", "value": "{value}"},
        {"id": "DesignWidth", "value": 999},
        {"id": "nested", "value": 2, "elements": [{"id": "inner", "value": "o"}]},
        {"id": "novalue", "value": "x"},
        "junk",
    ]
    out = sp._merge_element_arrays(new, old)
    by_id = {e.get("id"): e for e in out if isinstance(e, dict)}
    assert out[0] == {"type": "separator", "label": "S"}
    assert by_id["color"] == {"id": "color", "value": "#fff", "label": "new label"}
    assert by_id["cnt_format"]["value"] == "{value}/{max}"
    assert by_id["DesignWidth"]["value"] == 400
    assert by_id["nested"]["value"] == 2
    assert by_id["nested"]["elements"] == [{"id": "inner", "value": "o"}]
    assert by_id["novalue"] == {"id": "novalue"}
    assert by_id["fresh"]["value"] == 5


def test_merge_config():
    assert sp._merge_config([1], {"a": 1}) == [1]
    assert sp._merge_config({"a": 1}, None) == {"a": 1}
    new = {"value": 1, "elements": [{"id": "a", "value": 1}], "sub": {"value": "n", "k": 1},
           "only_new": 3, "scalar": "new"}
    old = {"value": 9, "elements": [{"id": "a", "value": 7}], "sub": {"value": "o"},
           "scalar": "old"}
    assert sp._merge_config(new, old) == {
        "value": 9, "elements": [{"id": "a", "value": 7}], "sub": {"value": "o", "k": 1},
        "only_new": 3, "scalar": "new",
    }


# ---------------------------------------------------------------- name validation

@pytest.mark.parametrize(
    "name,msg",
    [(5, "must be a string"), ("   ", "cannot be empty"), ("a b", "may only contain"),
     ("_x", "cannot start with '_'"), ("API", "reserved"), ("default_assets", "reserved")],
)
def test_validate_name_rejects(sb, name, msg):
    with pytest.raises(sp.SporeStudioError, match=msg):
        sp._validate_name(name)


def test_validate_name_container_route(sb):
    (sb.templates / "_containers").mkdir()
    (sb.templates / "scene.html").write_text("container")
    path = containers.container_path("scene")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("x")
    with pytest.raises(sp.SporeStudioError, match="container route"):
        sp._validate_name("scene")


def test_validate_name_ok(sb):
    assert sp._validate_name("  my-tpl_2 ") == "my-tpl_2"


def test_validate_legacy_name(sb):
    for bad in (None, "", "_x", "a/b", "a\\b", "a..b"):
        with pytest.raises(sp.SporeStudioError, match="isn't valid"):
            sp._validate_legacy_name(bad)
    with pytest.raises(sp.SporeStudioError, match="no template named"):
        sp._validate_legacy_name("My Old")
    sb.html("My Old").write_text("<html></html>")
    assert sp._validate_legacy_name(" My Old ") == "My Old"


# ---------------------------------------------------------------- io helpers

def test_atomic_writes(sb):
    p = sb.tmp / "deep" / "f.txt"
    sp._atomic_write(str(p), "héllo")
    assert p.read_text(encoding="utf-8") == "héllo"
    j = sb.tmp / "deep2" / "f.json"
    sp._atomic_write_json(str(j), {"a": "é"})
    assert "é" in j.read_text(encoding="utf-8")
    assert json.loads(j.read_text(encoding="utf-8")) == {"a": "é"}
    assert not os.path.exists(str(j) + ".tmp")


def test_load_existing_html(sb, monkeypatch, caplog):
    assert sp._load_existing_html("none") is None
    sb.html("a").write_text("<p>x</p>", encoding="utf-8")
    assert sp._load_existing_html("a") == "<p>x</p>"
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("a.html"):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert sp._load_existing_html("a") is None
    assert "Could not read existing template" in caplog.text


def test_rewrite_copied_strings():
    model = {
        "a": "/assets/src/img.png",
        "b": ["src/counters", "src/counters/x", "bitsrc/counters", "src/countersX"],
        "c": {"n": 5, "d": "url(/assets/src/f.ttf) /assets/srcX/no"},
    }
    out = sp._rewrite_copied_strings(model, "src", "dst")
    assert out == {
        "a": "/assets/dst/img.png",
        "b": ["dst/counters", "dst/counters/x", "bitsrc/counters", "src/countersX"],
        "c": {"n": 5, "d": "url(/assets/dst/f.ttf) /assets/srcX/no"},
    }


# ---------------------------------------------------------------- compile / checks

def test_compile_wraps_errors(monkeypatch):
    def boom(model, existing_html=None):
        raise KeyError("bad")

    monkeypatch.setattr(sp.template_codegen, "compile_model", boom)
    with pytest.raises(sp.SporeStudioError, match="KeyError"):
        sp._compile({"template_name": "x"}, None)


def test_compile_passes_existing_html(monkeypatch):
    seen = {}

    def fake(model, existing_html=None):
        seen["html"] = existing_html
        return "<html>", {"a": 1}

    monkeypatch.setattr(sp.template_codegen, "compile_model", fake)
    assert sp._compile({}, "old") == ("<html>", {"a": 1})
    assert seen["html"] == "old"


def test_is_handwritten_template(sb, monkeypatch):
    assert sp._is_handwritten_template("none") is False
    sb.html("hand").write_text("<html>hand made</html>")
    assert sp._is_handwritten_template("hand") is True
    sb.html("spore").write_text("<!-- SPORE_STUDIO:dom-begin -->")
    assert sp._is_handwritten_template("spore") is False
    sb.write_sidecar("hand", {"elements": []})
    assert sp._is_handwritten_template("hand") is False
    sb.html("locked").write_text("x")
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("locked.html"):
            raise OSError
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert sp._is_handwritten_template("locked") is False


def test_check_element_ids():
    sp._check_element_ids({"elements": [{"id": "a"}, {"id": "_b2"}, "junk"]})
    sp._check_element_ids({})
    with pytest.raises(sp.SporeStudioError, match="'1x', '\\(empty\\)'"):
        sp._check_element_ids({"elements": [{"id": "1x"}, {"id": ""}]})
    many = {"elements": [{"id": f"bad-{i}"} for i in range(7)]}
    with pytest.raises(sp.SporeStudioError, match="and more"):
        sp._check_element_ids(many)
    with pytest.raises(sp.SporeStudioError, match="share the ID 'a'"):
        sp._check_element_ids({"elements": [{"id": "a"}, {"id": "a"}]})


def test_compile_preview_draft(sb):
    with pytest.raises(sp.SporeStudioError, match="JSON object"):
        sp.compile_preview_draft([])
    with pytest.raises(sp.SporeStudioError, match="only for Spore"):
        sp.compile_preview_draft({"legacy": True})
    with pytest.raises(sp.SporeStudioError, match="cannot be empty"):
        sp.compile_preview_draft({})
    html, cfg = sp.compile_preview_draft(_model())
    assert 'id="title"' in html and isinstance(cfg, dict)
    assert not sb.html("demo").exists()  # preview never writes


def test_ensure_template_assets_folder(sb):
    out = sp.ensure_template_assets_folder("abc")
    assert out == str(sb.assets / "abc") and os.path.isdir(out)


# ---------------------------------------------------------------- route refresh

def test_refresh_routes_no_instance(sb):
    assert sp._refresh_web_engine_routes("x") is True


def test_refresh_routes_import_failure(sb, monkeypatch):
    monkeypatch.delattr(modules, "web_engine", raising=False)
    monkeypatch.setitem(sys.modules, "modules.web_engine", None)
    assert sp._refresh_web_engine_routes("x") is True


def test_refresh_routes_calls_register(sb):
    calls = []
    sb.engine.web_engine_instance = SimpleNamespace(register_routes=lambda: calls.append(1))
    assert sp._refresh_web_engine_routes("x") is True and calls == [1]


@pytest.mark.parametrize("name,has_actions", [("demo", True), ("", False)])
def test_refresh_routes_failure_notifies(sb, name, has_actions):
    def boom():
        raise RuntimeError("route clash")

    sb.engine.web_engine_instance = SimpleNamespace(register_routes=boom)
    assert sp._refresh_web_engine_routes(name) is False
    (note,) = sb.notes
    assert note["type"] == "warning" and "route clash" in note["details"]
    assert bool(note["actions"]) is has_actions
    if name:
        assert "\u201cdemo\u201d was saved" in note["message"]
    else:
        assert note["message"].startswith("The template change is on disk")


def test_refresh_routes_notify_failure_swallowed(sb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError

    sb.engine.web_engine_instance = SimpleNamespace(register_routes=boom)
    monkeypatch.setattr(notification_engine, "notify", boom)
    assert sp._refresh_web_engine_routes("x") is False


# ---------------------------------------------------------------- save_template

def test_save_template_rejects(sb):
    with pytest.raises(sp.SporeStudioError, match="JSON object"):
        sp.save_template("x")
    with pytest.raises(sp.SporeStudioError, match="built-in overlay"):
        sp.save_template(_model("activity_feed"))
    sb.html("hand").write_text("<html>hand</html>")
    with pytest.raises(sp.SporeStudioError, match="wasn't made in Spore Studio"):
        sp.save_template(_model("hand"))
    bad = _model()
    bad["elements"].append({"id": "9bad"})
    with pytest.raises(sp.SporeStudioError, match="Element IDs"):
        sp.save_template(bad)
    assert not sb.html("demo").exists()


def test_save_template_writes_all_files(sb):
    sb.write_config("demo", {"outputs": {
        "custom": {"event": "my_evt", "label": "Mine"},
        "old_gen": {"event": "wheel_result", "generated": True},
    }})
    out = sp.save_template(_model(alert_system="bogus"))
    assert out["alert_system"] == "queue"
    html = sb.html("demo").read_text(encoding="utf-8")
    assert "SPORE_STUDIO:dom-begin" in html and 'id="title"' in html
    cfg = json.loads(sb.config("demo").read_text(encoding="utf-8"))
    assert cfg["outputs"]["custom"]["event"] == "my_evt"
    assert "old_gen" not in cfg["outputs"]
    side = json.loads(sb.sidecar("demo").read_text(encoding="utf-8"))
    assert side["template_name"] == "demo" and side["elements"][0]["id"] == "title"
    assert (sb.assets / "demo").is_dir()
    # A second save reuses the existing HTML and stays a Spore template
    sp.save_template(_model(alert_system="instant"))
    side = json.loads(sb.sidecar("demo").read_text(encoding="utf-8"))
    assert side["alert_system"] == "instant"


def test_save_template_sidecar_failure_raises_after_writing(sb, monkeypatch):
    monkeypatch.setattr(template_parser_back, "save_sidecar", lambda n, m: False)
    with pytest.raises(sp.SporeStudioError, match="couldn't write its own copy"):
        sp.save_template(_model())
    assert sb.html("demo").exists() and sb.config("demo").exists()


def test_save_template_invalidate_cache_failure_ignored(sb, monkeypatch):
    def boom():
        raise RuntimeError

    monkeypatch.setattr(template_outputs, "invalidate_cache", boom)
    sp.save_template(_model())
    assert sb.html("demo").exists()


def test_save_template_compile_error_writes_nothing(sb, monkeypatch):
    def boom(model, existing_html=None):
        raise ValueError("nope")

    monkeypatch.setattr(sp.template_codegen, "compile_model", boom)
    with pytest.raises(sp.SporeStudioError, match="ValueError: nope"):
        sp.save_template(_model())
    assert not sb.html("demo").exists() and not sb.sidecar("demo").exists()


# ---------------------------------------------------------------- carry outputs

def test_carry_handwritten_outputs_edge_cases(sb):
    cfg = {}
    sp._carry_handwritten_outputs(str(sb.tmp / "missing.json"), cfg)
    assert cfg == {}
    p = sb.tmp / "c.json"
    p.write_text("[1]")
    sp._carry_handwritten_outputs(str(p), cfg)
    p.write_text(json.dumps({"outputs": [1]}))
    sp._carry_handwritten_outputs(str(p), cfg)
    p.write_text(json.dumps({"outputs": {"g": {"generated": True}, "s": "str"}}))
    sp._carry_handwritten_outputs(str(p), cfg)
    assert cfg == {}
    p.write_text(json.dumps({"outputs": {"h": {"event": "old"}, "k": {"event": "kept"}}}))
    cfg = {"outputs": {"h": {"event": "new"}}}
    sp._carry_handwritten_outputs(str(p), cfg)
    assert cfg["outputs"] == {"h": {"event": "new"}, "k": {"event": "kept"}}


# ---------------------------------------------------------------- legacy save

def test_resolve_model_path():
    el = {"props": {"font_size": 12}, "position": {"x": 3}}
    assert sp._resolve_model_path(el, "props.font_size") == 12
    assert sp._resolve_model_path(el, "props.missing") is None
    assert sp._resolve_model_path(el, "position.x.deeper") is None


def test_resolved_legacy_binding_value_anchors():
    design = {"width": 1000, "height": 500}
    el = {"position": {"x": 100, "y": 50}, "size": {"w": 200, "h": 80},
          "legacy_anchor": {"x": "right", "y": "bottom"}}
    assert sp._resolved_legacy_binding_value(el, "position.x", design) == 700
    assert sp._resolved_legacy_binding_value(el, "position.y", design) == 370
    assert sp._resolved_legacy_binding_value(el, "size.w", design) == 200
    assert sp._resolved_legacy_binding_value(el, "props.none", design) is None
    plain = dict(el, legacy_anchor=None)
    assert sp._resolved_legacy_binding_value(plain, "position.x", design) == 100
    bad = {"position": {"x": "abc", "y": "abc"}, "size": {},
           "legacy_anchor": {"x": "right", "y": "bottom"}}
    assert sp._resolved_legacy_binding_value(bad, "position.x", design) == "abc"
    assert sp._resolved_legacy_binding_value(bad, "position.y", design) == "abc"


def test_save_legacy_template_updates_json_only(sb):
    sb.html("Old One").write_text("<html>hand made</html>")
    sb.write_config("Old One", {"elements": [
        {"type": "separator", "label": "S"},
        {"id": "Title", "value": "old"},
        {"id": "TitleLeft", "value": 1},
        {"id": "TitleBottom", "value": 2},
        {"id": "Untouched", "value": "same"},
        "junk",
    ]})
    sb.engine.web_engine_instance = SimpleNamespace(register_routes=lambda: None)
    model = {
        "template_name": "Old One", "legacy": True, "design": {"width": 800, "height": 600},
        "elements": [
            {"id": "Title", "props": {"value": "new"}},
            {"id": "box", "position": {"x": 15, "y": 100}, "size": {"w": 50, "h": 40},
             "legacy_anchor": {"y": "bottom"},
             "legacy_bindings": {"position.x": "TitleLeft", "position.y": "TitleBottom",
                                 "props.color": "Nope", "size.w": ""}},
            {"id": "weird", "legacy_bindings": ["not", "dict"]},
            "junk",
        ],
    }
    out = sp.save_template(model)
    assert out["template_name"] == "Old One"
    cfg = json.loads(sb.config("Old One").read_text(encoding="utf-8"))
    vals = {e["id"]: e["value"] for e in cfg["elements"] if isinstance(e, dict) and "id" in e}
    assert vals == {"Title": "new", "TitleLeft": 15, "TitleBottom": 460, "Untouched": "same"}
    assert sb.html("Old One").read_text() == "<html>hand made</html>"
    assert not sb.sidecar("Old One").exists()


def test_save_legacy_missing_or_bad_config(sb):
    sb.html("old").write_text("x")
    with pytest.raises(sp.SporeStudioError, match="no JSON config"):
        sp.save_template({"template_name": "old", "legacy": True})
    sb.config("old").write_text("{broken")
    with pytest.raises(sp.SporeStudioError, match="Could not read legacy config"):
        sp.save_template({"template_name": "old", "legacy": True})


def test_save_legacy_config_without_elements_list(sb):
    sb.html("old").write_text("x")
    sb.write_config("old", {"elements": "nope"})
    sp.save_template({"template_name": "old", "legacy": True,
                      "elements": [{"id": "a", "props": {"value": 1}}]})
    assert json.loads(sb.config("old").read_text()) == {"elements": "nope"}


# ---------------------------------------------------------------- create_template

def test_create_template_fresh(sb):
    model = sp.create_template("fresh", alert_system="instant", design_width=0, design_height=300)
    assert model["design"] == {"width": 800, "height": 300}
    assert model["alert_system"] == "instant"
    assert sb.html("fresh").exists() and sb.sidecar("fresh").exists()
    assert (sb.assets / "fresh").is_dir()


def test_create_template_rejects(sb):
    with pytest.raises(sp.SporeStudioError, match="alert_system"):
        sp.create_template("x", alert_system="both")
    sb.html("taken").write_text("x")
    with pytest.raises(sp.SporeStudioError, match="already exists"):
        sp.create_template("taken")
    (sb.assets / "busy").mkdir()
    (sb.assets / "busy" / "f.png").write_bytes(b"1")
    with pytest.raises(sp.SporeStudioError, match="isn't empty"):
        sp.create_template("busy")
    (sb.assets / "emptyok").mkdir()
    sp.create_template("emptyok")
    assert sb.sidecar("emptyok").exists()


def test_create_template_copy_from(sb):
    src = _model("src", legacy=True)
    src["elements"][0]["props"]["src"] = "/assets/src/pic.png"
    src["elements"][0]["props"]["counter_path"] = "src/counters/total"
    sb.write_sidecar("src", src)
    (sb.assets / "src").mkdir()
    (sb.assets / "src" / "pic.png").write_bytes(b"img")
    model = sp.create_template("dst", copy_from="src")
    assert model["template_name"] == "dst" and "legacy" not in model
    props = model["elements"][0]["props"]
    assert props["src"] == "/assets/dst/pic.png"
    assert props["counter_path"] == "dst/counters/total"
    assert (sb.assets / "dst" / "pic.png").read_bytes() == b"img"
    side = json.loads(sb.sidecar("dst").read_text())
    assert side["template_name"] == "dst"


def test_create_template_copy_from_without_assets_or_sidecar(sb):
    with pytest.raises(sp.SporeStudioError, match="not a Spore Studio template"):
        sp.create_template("dst", copy_from="ghost")
    sb.write_sidecar("src2", _model("src2"))
    sp.create_template("dst2", copy_from="src2")
    assert (sb.assets / "dst2").is_dir()


# ---------------------------------------------------------------- delete / list

def test_delete_template_rejects(sb):
    with pytest.raises(sp.SporeStudioError, match="protected"):
        sp.delete_template("source_controls")
    with pytest.raises(sp.SporeStudioError, match="no sidecar"):
        sp.delete_template("ghost")


def test_delete_template_removes_everything(sb):
    sp.create_template("gone")
    (sb.assets / "gone" / "a.png").write_bytes(b"1")
    calls = []
    sb.engine.web_engine_instance = SimpleNamespace(register_routes=lambda: calls.append(1))
    sp.delete_template("gone")
    assert not sb.html("gone").exists() and not sb.config("gone").exists()
    assert not sb.sidecar("gone").exists() and not (sb.assets / "gone").exists()
    assert calls == [1]


def test_delete_template_file_error(sb, monkeypatch):
    sp.create_template("stuck")
    real_remove = os.remove

    def fake_remove(p):
        if str(p).endswith("stuck.html"):
            raise OSError("busy")
        return real_remove(p)

    monkeypatch.setattr(sp.os, "remove", fake_remove)
    with pytest.raises(sp.SporeStudioError, match="Could not remove"):
        sp.delete_template("stuck")


def test_delete_template_assets_error_logged(sb, monkeypatch, caplog):
    sp.create_template("assetfail")

    def boom(p):
        raise OSError("perm")

    monkeypatch.setattr(sp.shutil, "rmtree", boom)
    sp.delete_template("assetfail")
    assert "Could not remove assets directory" in caplog.text
    assert not sb.html("assetfail").exists()


def test_list_spore_templates(sb, monkeypatch):
    for name in ("b_legacy", "_hidden", "activity_feed", "a_spore"):
        sb.html(name).write_text("x")
    (sb.templates / "notes.txt").write_text("x")
    sb.write_sidecar("a_spore", {"elements": []})
    assert sp.list_spore_templates() == (["a_spore"], ["b_legacy"])


def test_list_spore_templates_missing_dir(sb, monkeypatch):
    monkeypatch.setattr(sp, "get_template_path", lambda rel="": str(sb.tmp / "none"))
    assert sp.list_spore_templates() == ([], [])

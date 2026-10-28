# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/containers.py (overlay containers: validation, storage, solver, shell)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import notification_engine
from modules.spore_studio import containers as c
from modules.spore_studio import save_pipeline, template_parser_back


class _Root(type(Path())):
    """The temp templates folder, carrying the captured notifications."""

    def __init__(self, path, notes=None):
        super().__init__(path)
        self.notes = notes if notes is not None else []

    def with_segments(self, *segments):
        return type(Path())(*segments)


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    root.mkdir()

    def tpl(rel=""):
        return os.path.join(str(root), rel) if rel else str(root)

    for mod in (c, save_pipeline, template_parser_back):
        monkeypatch.setattr(mod, "get_template_path", tpl)
    monkeypatch.setattr(c, "_warned_bad_containers", set())
    notes = []
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: notes.append(k))
    return _Root(root, notes)


def _base(**kw):
    raw = {"name": "Scene", "route": "scene", "slots": []}
    raw.update(kw)
    return raw


def _slot(sid, **kw):
    s = {"id": sid, "template": f"t_{sid}", "x": 0, "y": 0, "w": 400, "h": 300}
    s.update(kw)
    return s


def _link(lid="L", axis="vertical", role="flexible", **kw):
    d = {"id": lid, "axis": axis, "role": role}
    d.update(kw)
    return d


# ---------------------------------------------------------------- paths / routes

def test_paths(tdir):
    assert c.containers_dir() == str(tdir / "_containers")
    assert c.container_path("x") == str(tdir / "_containers" / "x.json")
    assert c.template_html_path("t") == str(tdir / "t.html")


def test_template_html_exists(tdir):
    (tdir / "t.html").write_text("x")
    assert c.template_html_exists("t") is True
    for bad in ("", "a/b", "a\\b", "..", "missing"):
        assert c.template_html_exists(bad) is False


@pytest.mark.parametrize("name,expected", [
    ("My Cool Scene!", "my_cool_scene"), ("  ", "container"), ("API", "container"),
    ("__x__", "x"), ("a" * 60, "a" * 48), (None, "container")])
def test_suggest_route(name, expected):
    assert c.suggest_route(name) == expected


@pytest.mark.parametrize("route,msg", [(5, "must be a string"), ("  ", "cannot be empty"),
                                       ("_x", "may only contain"), ("a b", "may only contain"),
                                       ("Socket.io", "may only contain"), ("API", "reserved")])
def test_validate_route_syntax_errors(route, msg):
    with pytest.raises(c.ContainerError, match=msg):
        c.validate_route_syntax(route)


def test_validate_route_syntax_ok():
    assert c.validate_route_syntax(" a-1_b ") == "a-1_b"


def test_container_route_exists(tdir):
    assert c.container_route_exists("_bad") is False
    assert c.container_route_exists("scene") is False
    c.save_container(_base())
    assert c.container_route_exists("scene") is True


def test_atomic_write_json(tdir):
    p = tdir / "deep" / "a.json"
    c._atomic_write_json(str(p), {"é": 1})
    text = p.read_text(encoding="utf-8")
    assert text.endswith("\n") and json.loads(text) == {"é": 1}


# ---------------------------------------------------------------- coercion helpers

def test_as_bool():
    assert c._as_bool(None, True) is True
    assert c._as_bool(False, True) is False
    with pytest.raises(c.ContainerError, match="boolean"):
        c._as_bool("yes", False)


def test_as_int():
    assert c._as_int(None, 5, 0, 10, "X") == 5
    assert c._as_int("7.6", 0, 0, 10, "X") == 8
    with pytest.raises(c.ContainerError, match="X must be a number"):
        c._as_int("abc", 0, 0, 10, "X")
    with pytest.raises(c.ContainerError, match="between 0 and 10"):
        c._as_int(11, 0, 0, 10, "X")
    with pytest.raises(c.ContainerError, match="between 0 and 10"):
        c._as_int(-1, 0, 0, 10, "X")


def test_optional_int():
    assert c._optional_int(None, 0, 5, "M") is None
    assert c._optional_int("", 0, 5, "M") is None
    assert c._optional_int("3", 0, 5, "M") == 3


def test_padding():
    assert c._padding(None) == {"t": 0, "r": 0, "b": 0, "l": 0}
    assert c._padding({"t": 1, "l": "2"}) == {"t": 1, "r": 0, "b": 0, "l": 2}
    with pytest.raises(c.ContainerError, match="Padding must be an object"):
        c._padding([1])
    with pytest.raises(c.ContainerError, match="Padding top"):
        c._padding({"t": 999})


def test_normalize_link():
    assert c._normalize_link(None) is None
    out = c._normalize_link({"id": " L ", "max_px": "", "max_pct": 50})
    assert out == {"id": "L", "axis": "vertical", "role": "flexible", "min_px": 0, "min_pct": 0,
                   "max_px": None, "max_pct": 50}
    for bad, msg in [("x", "must be an object"), ({"id": ""}, "id is invalid"),
                     ({"id": "a/b"}, "id is invalid"), ({"id": "x" * 65}, "id is invalid"),
                     ({"id": "a", "axis": "diag"}, "axis"), ({"id": "a", "role": "boss"}, "role"),
                     ({"id": "a", "min_pct": 101}, "Minimum percent")]:
        with pytest.raises(c.ContainerError, match=msg):
            c._normalize_link(bad)


def test_normalize_slot_defaults():
    s = c._normalize_slot({"template": " alerts "}, 2)
    assert s["id"] == "slot3" and s["template"] == "alerts"
    assert (s["x"], s["y"], s["w"], s["h"], s["z"]) == (0, 0, 640, 360, 3)
    assert s["visible"] is True and s["locked_position"] is False and s["pointer_events"] is True
    assert s["fit"] == "native" and s["design_width"] is None and s["link"] is None
    assert s["audio"] == {"muted": False, "volume": 100}


def test_normalize_slot_full():
    s = c._normalize_slot({"id": "a", "template": "t", "fit": "scale", "design_width": 800,
                           "design_height": "600", "audio": {"muted": 1, "volume": 30},
                           "visible": False, "link": {"id": "L"}}, 0)
    assert s["fit"] == "scale" and s["design_height"] == 600
    assert s["audio"] == {"muted": True, "volume": 30} and s["visible"] is False
    assert s["link"]["id"] == "L"


@pytest.mark.parametrize("raw,msg", [
    ("x", "must be an object"), ({"id": "a/b", "template": "t"}, "Slot id is invalid"),
    ({"id": "x" * 65, "template": "t"}, "Slot id is invalid"),
    ({"id": "a"}, "template name is invalid"), ({"id": "a", "template": "../x"}, "template name is invalid"),
    ({"id": "a", "template": "a\\b"}, "template name is invalid"),
    ({"id": "a", "template": "t", "fit": "stretch"}, "Fit must be"),
    ({"id": "a", "template": "t", "w": 0}, "Width"),
    ({"id": "a", "template": "t", "visible": "no"}, "boolean")])
def test_normalize_slot_errors(raw, msg):
    with pytest.raises(c.ContainerError, match=msg):
        c._normalize_slot(raw, 0)


# ---------------------------------------------------------------- normalize_container

def test_normalize_container_defaults():
    m = c.normalize_container(_base(slots=None, background=None))
    assert m == {"version": 1, "name": "Scene", "route": "scene", "width": 1920, "height": 1080,
                 "background": "transparent", "spacing": 0, "snap": 10, "enabled": True, "slots": []}


def test_normalize_container_background_variants():
    assert c.normalize_container(_base(background=" TRANSPARENT "))["background"] == "transparent"
    assert c.normalize_container(_base(background=" #AbC "))["background"] == "#AbC"
    assert c.normalize_container(_base(background="#a1b2c3"))["background"] == "#a1b2c3"


@pytest.mark.parametrize("raw,msg", [
    ("x", "JSON object"), ({"name": ""}, "Name must be"), ({"name": "x" * 81}, "Name must be"),
    (_base(route=""), "cannot be empty"), (_base(background=5), "must be a string"),
    (_base(background="red"), "transparent or a hex"), (_base(slots="x"), "Slots must be a list"),
    (_base(slots=[_slot("a"), _slot("a")]), "unique"),
    (_base(slots=[_slot("a", link=_link()), _slot("b", link=_link(axis="horizontal"))]), "mixes"),
    (_base(width=10), "Width"), (_base(enabled="yes"), "boolean")])
def test_normalize_container_errors(raw, msg):
    with pytest.raises(c.ContainerError, match=msg):
        c.normalize_container(raw)


# ---------------------------------------------------------------- load / warn / list

def test_load_container_variants(tdir):
    assert c.load_container("_bad") is None
    assert c.load_container("missing") is None
    c.save_container(_base())
    assert c.load_container("scene")["name"] == "Scene"


def test_load_container_damaged_warns_once(tdir):
    d = tdir / "_containers"
    d.mkdir()
    (d / "broken.json").write_text("{nope")
    assert c.load_container("broken") is None
    assert c.load_container("broken") is None
    assert len(tdir.notes) == 1
    note = tdir.notes[0]
    assert note["type"] == "warning" and "/broken" in note["title"]
    assert note["dedupe_key"].startswith("spore:container_bad:")


def test_load_container_invalid_shape(tdir):
    d = tdir / "_containers"
    d.mkdir()
    (d / "bad.json").write_text(json.dumps({"name": "", "route": "bad"}))
    assert c.load_container("bad") is None
    assert "Name must be" in tdir.notes[0]["details"]


def test_warn_bad_container_getmtime_error_and_notify_failure(tdir, monkeypatch):
    c._warn_bad_container(str(tdir / "nofile.json"), "r", ValueError("x"))
    assert len(tdir.notes) == 1
    c._warn_bad_container(str(tdir / "nofile.json"), "r", ValueError("x"))
    assert len(tdir.notes) == 1  # same (path, mtime=0) key

    def boom(*a, **k):
        raise RuntimeError

    monkeypatch.setattr(notification_engine, "notify", boom)
    c._warn_bad_container(str(tdir / "other.json"), "r", ValueError("x"))


def test_list_models_routes_summaries(tdir):
    assert c.list_container_models() == []
    c.save_container(_base(name="beta", route="b", slots=[_slot("a")]))
    c.save_container(_base(name="Alpha", route="a", enabled=False))
    (tdir / "_containers" / "notes.txt").write_text("x")
    (tdir / "_containers" / "junk.json").write_text("[]")
    assert [m["name"] for m in c.list_container_models()] == ["Alpha", "beta"]
    assert c.list_enabled_routes() == ["b"]
    summaries = c.list_containers()
    assert summaries[1] == {"name": "beta", "route": "b", "enabled": True, "width": 1920,
                            "height": 1080, "template_count": 1, "templates": ["t_a"]}


def test_container_source_record():
    m = c.normalize_container(_base(slots=[_slot("a")]))
    rec = c.container_source_record(m, "http://h:5000/")
    assert rec["url"] == "http://h:5000/scene"
    assert rec["description"] == "Container combining 1 template: t_a"
    m2 = c.normalize_container(_base())
    rec2 = c.container_source_record(m2, None)
    assert rec2["url"] == "/scene" and rec2["description"].endswith("0 templates: none")
    m3 = c.normalize_container(_base(slots=[_slot("a"), _slot("b")]))
    assert "2 templates: t_a, t_b" in c.container_source_record(m3, "")["description"]


# ---------------------------------------------------------------- save / create / duplicate / delete

def test_save_container_conflicts(tdir):
    (tdir / "tpl.html").write_text("x")
    with pytest.raises(c.ContainerError, match="already a template"):
        c.save_container(_base(route="tpl"))
    c.save_container(_base())
    with pytest.raises(c.ContainerError, match="already a container"):
        c.save_container(_base())
    c.save_container(_base(name="Renamed"), previous_route="scene")  # same route allowed
    assert c.load_container("scene")["name"] == "Renamed"
    with pytest.raises(c.ContainerError, match="Previous route"):
        c.save_container(_base(route="x"), previous_route="_bad")
    with pytest.raises(c.ContainerError, match="cannot be nested"):
        c.save_container(_base(route="outer", slots=[{"id": "a", "template": "scene"}]))


def test_save_container_rename_removes_old(tdir, monkeypatch, caplog):
    c.save_container(_base())
    c.save_container(_base(route="scene2"), previous_route=" scene ")
    assert not (tdir / "_containers" / "scene.json").exists()
    assert (tdir / "_containers" / "scene2.json").exists()
    # previous file already gone -> fine
    c.save_container(_base(route="scene3"), previous_route="ghost")
    # remove failure is logged, not raised

    def boom(p):
        raise OSError("busy")

    monkeypatch.setattr(c.os, "remove", boom)
    c.save_container(_base(route="scene4"), previous_route="scene3")
    assert "Could not remove old container" in caplog.text


def test_create_container(tdir):
    m = c.create_container("My Scene", width=800, height=600)
    assert m["route"] == "my_scene" and m["width"] == 800 and m["slots"] == []
    with pytest.raises(c.ContainerError, match="already a container"):
        c.create_container("My Scene")
    (tdir / "taken.html").write_text("x")
    with pytest.raises(c.ContainerError, match="already a template"):
        c.create_container("x", "taken")


def test_duplicate_container(tdir):
    with pytest.raises(c.ContainerError, match="was not found"):
        c.duplicate_container("ghost", "n", "new")
    c.save_container(_base(slots=[_slot("a")]))
    m = c.duplicate_container("scene", "Copy", "copy")
    assert m["name"] == "Copy" and m["route"] == "copy" and m["slots"][0]["id"] == "a"
    assert c.load_container("scene")["name"] == "Scene"


def test_delete_container(tdir, monkeypatch):
    with pytest.raises(c.ContainerError, match="was not found"):
        c.delete_container("ghost")
    c.save_container(_base())
    c.save_container(_base(route="other"))

    def boom(p):
        raise OSError("busy")

    monkeypatch.setattr(c.os, "remove", boom)
    with pytest.raises(c.ContainerError, match="Could not delete"):
        c.delete_container("other")
    monkeypatch.undo()


def test_delete_container_ok(tdir):
    c.save_container(_base())
    c.delete_container("scene")
    assert not c.container_route_exists("scene")


def test_list_member_templates(tdir):
    for n in ("activity_feed", "source_controls", "_hidden", "legacy", "spore_one"):
        (tdir / f"{n}.html").write_text("x")
    (tdir / "readme.txt").write_text("x")
    sp = tdir / "_spore"
    sp.mkdir()
    (sp / "spore_one.spore.json").write_text("{}")
    assert c.list_member_templates() == {"spore": ["spore_one"], "legacy": ["legacy"],
                                         "builtin": ["activity_feed", "source_controls"]}


def test_list_member_templates_missing_folder(tmp_path, monkeypatch):
    missing = str(tmp_path / "none")
    monkeypatch.setattr(c, "get_template_path", lambda rel="": missing)
    monkeypatch.setattr(save_pipeline, "get_template_path", lambda rel="": missing)
    assert c.list_member_templates() == {"spore": [], "legacy": [], "builtin": []}


# ---------------------------------------------------------------- solver internals

def test_eff_min_max():
    assert c._eff_min({"min_px": 50, "min_pct": 10}, 1000) == 100
    assert c._eff_min({}, 1000) == 0
    assert c._eff_max({"max_px": None, "max_pct": None}, 1000) is None
    assert c._eff_max({"max_px": 300, "max_pct": 20}, 1000) == 200
    assert c._eff_max({"max_px": 100}, 1000) == 100


def test_axis_pads_and_designated():
    s = {"padding": {"t": 1, "r": 2, "b": 3, "l": 4}, "w": 10, "h": 20}
    assert c._axis_pads(s, "vertical") == (1, 3)
    assert c._axis_pads(s, "horizontal") == (4, 2)
    assert c._designated(s, "vertical") == 20 and c._designated(s, "horizontal") == 10


def test_measured_content():
    s = {"id": "a", "fit": "native"}
    assert c._measured_content({"id": "a", "fit": "scale"}, "vertical", {"a": {"h": 5}}) is None
    assert c._measured_content(s, "vertical", None) is None
    assert c._measured_content(s, "vertical", {"a": "x"}) is None
    assert c._measured_content(s, "vertical", {"a": {"explicit": True, "h": "12"}}) == 12.0
    assert c._measured_content(s, "horizontal", {"a": {"explicit": True}}) is None
    assert c._measured_content(s, "vertical", {"a": {"h": None}}) is None
    assert c._measured_content(s, "vertical", {"a": {}}) is None
    assert c._measured_content(s, "horizontal", {"a": {"w": 7}}) == 7.0
    assert c._measured_content(s, "vertical", {"a": {"h": "bad"}}) is None


def test_split_int():
    assert c._split_int(10, []) == {}
    assert c._split_int(0, [("a", 1), ("b", 1)]) == {"a": 0, "b": 0}
    assert c._split_int(10, [("a", 1), ("b", 1), ("c", 1)]) == {"a": 4, "b": 3, "c": 3}
    assert c._split_int(9, [("a", 0), ("b", 2)]) == {"a": 3, "b": 6}


def test_distribute_flexible():
    assert c._distribute_flexible(100, []) == ({}, False)
    sizes, over = c._distribute_flexible(10, [{"id": "a", "min": 20, "max": None, "weight": 1}])
    assert sizes == {"a": 20} and over is True
    items = [{"id": "a", "min": 0, "max": 30, "weight": 1}, {"id": "b", "min": 0, "max": None, "weight": 1}]
    sizes, over = c._distribute_flexible(100, items)
    assert sizes == {"a": 30, "b": 70} and over is False
    items = [{"id": "a", "min": 80, "max": None, "weight": 1}, {"id": "b", "min": 0, "max": None, "weight": 1}]
    assert c._distribute_flexible(100, items) == ({"a": 80, "b": 20}, False)
    # every slot capped: loop ends with nothing active
    items = [{"id": "a", "min": 0, "max": 10, "weight": 1}, {"id": "b", "min": 0, "max": 10, "weight": 1}]
    assert c._distribute_flexible(100, items) == ({"a": 10, "b": 10}, False)


def test_distribute_flexible_budget_negative(monkeypatch):
    # force a freeze that overspends the budget
    items = [{"id": "a", "min": 0, "max": 5, "weight": 1}, {"id": "b", "min": 0, "max": None, "weight": 1}]
    monkeypatch.setattr(c, "_split_int", lambda total, weights: {k: 10 for k, _ in weights})
    sizes, over = c._distribute_flexible(3, items)
    assert over is True and sizes == {"a": 5}


def test_content_and_iframe_boxes():
    slot = {"padding": {"t": 5, "r": 6, "b": 7, "l": 8}, "w": 100, "h": 80, "fit": "native", "link": None}
    content = c._content_box(slot, 100, 80)
    assert content == {"x": 8, "y": 5, "w": 86, "h": 68}
    assert c._content_box(slot, 1, 1) == {"x": 8, "y": 5, "w": 0, "h": 0}
    assert c._iframe_box(slot, None, content) == {"w": 86, "h": 68, "left": 8, "top": 5}
    scale = dict(slot, fit="scale", design_width=1920, design_height=None)
    assert c._iframe_box(scale, "vertical", content) == {"w": 1920, "h": 80, "left": 8, "top": 5}
    locked = dict(slot, link={"role": "locked"})
    assert c._iframe_box(locked, "vertical", {"x": 8, "y": 5, "w": 86, "h": 10})["h"] == 68
    assert c._iframe_box(locked, "horizontal", {"x": 8, "y": 5, "w": 10, "h": 68})["w"] == 86


def test_locked_outer_min_above_designated():
    slot = c._normalize_slot(_slot("a", h=100, link=_link(role="locked", min_px=300)), 0)
    warnings = []
    assert c._locked_outer(slot, "vertical", 1000, None, warnings) == 100
    assert warnings[0]["code"] == "min_above_designated"


def test_locked_outer_measured_and_capped():
    slot = c._normalize_slot(_slot("a", h=400, padding={"t": 10, "b": 10},
                                   link=_link(role="locked", max_pct=30)), 0)
    warnings = []
    # measured 100 content + 20 pad = 120, cap 300
    assert c._locked_outer(slot, "vertical", 1000, {"a": {"h": 100}}, warnings) == 120
    assert c._locked_outer(slot, "vertical", 1000, {"a": {"h": 9999}}, warnings) == 300
    assert c._locked_outer(slot, "vertical", 1000, {"a": {"h": -5}}, warnings) == 20
    assert warnings == []


# ---------------------------------------------------------------- solve_layout

def test_solve_layout_vertical_stack_with_spacing_and_overflow_warning():
    cont = _base(height=1000, spacing=10, slots=[
        _slot("top", y=0, h=200, link=_link(role="locked")),
        _slot("bot", y=500, h=600, link=_link(min_px=900)),
        _slot("free", x=50, y=60, w=70, h=80, visible=False, pointer_events=False),
    ])
    out = c.solve_layout(cont, used={"top": {"h": 100}})
    rects = {s["id"]: s for s in out["slots"]}
    assert (rects["top"]["y"], rects["top"]["h"]) == (0, 100)
    assert rects["bot"]["y"] == 110 and rects["bot"]["h"] == 900
    assert (rects["free"]["x"], rects["free"]["y"], rects["free"]["w"], rects["free"]["h"]) == (50, 60, 70, 80)
    assert [w["code"] for w in out["warnings"]] == ["min_overflow"]
    assert rects["top"]["role"] == "locked" and rects["top"]["link_id"] == "L"
    assert rects["free"]["role"] is None and rects["free"]["axis"] is None


def test_solve_layout_dedupes_warnings():
    cont = _base(slots=[_slot("a", h=10, link=_link(role="locked", min_px=500)),
                        _slot("b", y=20, h=10, link=_link(role="locked", min_px=500))])
    out = c.solve_layout(cont)
    assert [w["code"] for w in out["warnings"]] == ["min_above_designated"]


def test_solve_layout_horizontal():
    cont = _base(width=1000, slots=[_slot("l", x=0, w=300, link=_link(axis="horizontal")),
                                    _slot("r", x=600, w=300, link=_link(axis="horizontal"))])
    out = c.solve_layout(cont)
    rects = {s["id"]: s for s in out["slots"]}
    assert (rects["l"]["x"], rects["l"]["w"]) == (0, 500)
    assert (rects["r"]["x"], rects["r"]["w"]) == (500, 500)
    assert out["width"] == 1000 and out["background"] == "transparent"


# ---------------------------------------------------------------- embed / shell

def test_embed_src():
    assert c.embed_src("my tpl", {"volume": 40}) == "/my%20tpl?mycelian_embed=1&mycelian_volume=40&mycelian_muted=0"
    assert c.embed_src("t", {"muted": True, "volume": 80}).endswith("volume=0&mycelian_muted=1")
    assert c.embed_src("t", {"volume": 0}).endswith("volume=0&mycelian_muted=1")
    assert c.embed_src("t", {}).endswith("volume=100&mycelian_muted=0")


def test_embed_audio_script_levels():
    assert "var volume=0.5000;" in c.embed_audio_script(volume=50, muted=False)
    assert "var volume=0.0000;" in c.embed_audio_script(volume=50, muted=True)
    assert "var volume=1.0000;" in c.embed_audio_script(volume=500, muted=False)
    assert "var volume=0.0000;" in c.embed_audio_script(volume=-5, muted=False)


def test_inject_embed_audio():
    out = c.inject_embed_audio("<html><head></head></html>", volume=50, muted=False)
    assert out.startswith("<html><head><script id=\"mycelian-embed-audio\">")
    assert c.inject_embed_audio(out, volume=10, muted=True) == out
    bare = c.inject_embed_audio("<p>x</p>", volume=50, muted=False)
    assert bare.startswith("<script") and bare.endswith("<p>x</p>")


def test_render_shell(tdir, caplog):
    (tdir / "here.html").write_text("x")
    cont = _base(name="<Scene>", background="#112233", slots=[
        {"id": "a", "template": "here", "x": 10, "y": 20, "w": 100, "h": 50, "opacity": 50,
         "pointer_events": False, "padding": {"l": 5, "t": 4}},
        {"id": "b", "template": "gone", "w": 10, "h": 10},
        {"id": "c", "template": "here", "visible": False},
    ])
    page = c.render_shell(cont)
    assert "<title>&lt;Scene&gt;</title>" in page and "background:#112233;" in page
    assert 'data-slot="a" style="left:10px;top:20px;width:100px;height:50px;z-index:1;opacity:0.500;pointer-events:none;"' in page
    assert 'src="/here?mycelian_embed=1&amp;mycelian_volume=100&amp;mycelian_muted=0"' in page
    assert 'class="mc-clip" style="left:5px;top:4px;width:95px;height:46px"' in page
    assert '<div class="mc-slot" data-slot="b" style="left:0px;top:0px;width:10px;height:10px;z-index:2;opacity:1.000;"></div>' in page
    assert 'data-slot="c"' not in page.split('<script id="mc-container-data"')[0]
    assert "uses missing template gone" in caplog.text
    data = page.split('type="application/json">')[1].split("</script>")[0]
    payload = json.loads(data)
    assert payload["container"]["name"] == "<Scene>" and "\\u003c" in data
    assert "<html" not in data  # nothing inlined


def test_render_shell_transparent(tdir):
    page = c.render_shell(_base())
    assert "background:transparent;" in page and '<div id="mc-container"></div>' in page

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/customsources.py, part 2c: loading, selecting and rendering templates.

Template list loading, the unsaved-changes prompt, render_config_ui, every setting control
type, asset thumbnails / browse buttons, colour fields and form-data updates.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from _customsources_support import FakeEl, FakeParser, FakeSelect, fire, make_env  # noqa: E402
from modules.uiwindows import customsources as cs  # noqa: E402
from modules.uiwindows import file_picker  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = make_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cs, "_lookup_obs_size_blocking", lambda route, port: None)
    return e


def everything(user):
    return list(user.client.layout.descendants())


def texts(root):
    return [e.text for e in root.descendants() if getattr(e, "text", "") and not isinstance(e, ui.tooltip)]


# ---------------------------------------------------------------- load / select

def test_load_config_files_keeps_dirty_selection(env, monkeypatch):
    parser = FakeParser({"b": {"elements": [{"id": "X", "type": "text", "value": "disk"}]}, "a": {"elements": []}})
    rendered = []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: rendered.append(n))
    sel = FakeSelect()
    monkeypatch.setattr(cs, "_active_config_name", "b")
    cs.form_data_store["b"] = {"X": "edited"}
    cs._disk_baselines["b"] = {"X": "disk"}
    cs.load_config_files(parser, sel, object())
    assert sel.options == ["a", "b"] and sel.value == "b" and rendered == ["b"]
    assert cs.form_data_store["b"] == {"X": "edited"}  # unsaved edits survive a reload
    assert parser.invalidations == 1 and cs._suppress_config_change is False


def test_load_config_files_rereads_clean_selection(env, monkeypatch):
    parser = FakeParser({"b": {"elements": []}})
    parser.invalidate_cache = lambda name=None: (_ for _ in ()).throw(RuntimeError("cache"))
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: None)
    monkeypatch.setattr(cs, "_active_config_name", "b")
    cs.form_data_store["b"] = {"X": 1}
    cs._disk_baselines["b"] = {"X": 1}
    cs._preview_route_cache["b"] = "old"
    cs.load_config_files(parser, FakeSelect(), object())
    assert "b" not in cs.form_data_store and "b" not in cs._disk_baselines
    assert cs._preview_route_cache == {}


def test_load_config_files_previous_gone_picks_first(env, monkeypatch):
    parser = FakeParser({"z": {}, "m": {}})
    rendered = []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: rendered.append(n))
    monkeypatch.setattr(cs, "_active_config_name", "deleted")
    sel = FakeSelect()
    cs.load_config_files(parser, sel, object())
    assert sel.value == "m" and rendered == ["m"]


def test_load_config_files_empty(env, monkeypatch):
    flushed = []
    monkeypatch.setattr(cs, "_flush_template_preview", lambda: flushed.append(1))
    monkeypatch.setattr(cs, "_active_config_name", "old")
    holder = {}

    def build():
        holder["c"] = ui.element("div")
        holder["sel"] = FakeSelect("old", ["old"])
        cs.load_config_files(FakeParser({}), holder["sel"], holder["c"])

    async def check(user):
        assert holder["sel"].options == [] and holder["sel"].value is None
        assert cs._active_config_name is None and flushed == [1]
        assert "No templates yet" in texts(holder["c"])
        assert cs._form_state["config"] is None

    run_ui(build, check)


def test_apply_config_selection(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "render_config_ui", lambda p, n, c: calls.append(("render", n)))
    monkeypatch.setattr(cs, "_flush_template_preview", lambda: calls.append(("flush",)))
    cs._apply_config_selection("", None, None)
    assert calls == []
    cs._preview_route_cache["x"] = "r"
    cs._apply_config_selection("x", None, None)
    assert calls == [("render", "x"), ("flush",)] and cs._active_config_name == "x"
    assert "x" not in cs._preview_route_cache


def test_discard_and_suppress_select(env):
    cs.form_data_store["x"] = {}
    cs._discard_config_edits("x")
    cs._discard_config_edits("x")
    assert "x" not in cs.form_data_store
    seen = []

    class Sel:
        def __setattr__(self, k, v):
            seen.append(cs._suppress_config_change)
            object.__setattr__(self, k, v)

    s = Sel()
    cs._suppress_and_select(s, "y")
    assert s.value == "y" and seen == [True] and cs._suppress_config_change is False


def test_select_config(env):
    assert cs.select_config("a") is False
    sel = FakeSelect("a", ["a", "b"])
    cs._custom_sources_ctx["config_select"] = sel
    assert cs.select_config("zzz") is False
    assert cs.select_config("a") is True
    assert cs.select_config("b") is True and sel.value == "b"
    sel.options = None
    assert cs.select_config("b") is False
    sel.options = ["b"]
    sel.is_deleted = True
    assert cs.select_config("b") is False


def test_on_config_selected_paths(env, monkeypatch):
    applied, prompts = [], []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: applied.append(n))
    monkeypatch.setattr(cs, "_prompt_unsaved_config", lambda prev, nxt, p, s, c: prompts.append((prev, nxt)))
    e = SimpleNamespace(value="b")
    monkeypatch.setattr(cs, "_suppress_config_change", True)
    cs.on_config_selected(e, None, None)
    monkeypatch.setattr(cs, "_suppress_config_change", False)
    cs.on_config_selected(SimpleNamespace(value=""), None, None)
    assert applied == [] and prompts == []
    cs.on_config_selected(e, None, None)
    assert applied == ["b"]
    monkeypatch.setattr(cs, "_active_config_name", "a")
    cs._disk_baselines["a"] = {"x": 1}
    cs.form_data_store["a"] = {"x": 2}
    cs.on_config_selected(e, None, None)  # dirty but no select given -> just switch
    assert applied == ["b", "b"]
    sel = FakeSelect("b")
    cs.on_config_selected(e, None, None, sel)
    assert prompts == [("a", "b")] and sel.value == "a"


@pytest.fixture
def prompt_setup(env, monkeypatch):
    applied = []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: applied.append(n))
    cs._disk_baselines["a"] = {"x": 1}
    cs.form_data_store["a"] = {"x": 2}
    sel = FakeSelect("a", ["a", "b"])
    holder = {"applied": applied, "sel": sel}

    def build():
        ui.button("go", on_click=lambda: cs._prompt_unsaved_config("a", "b", "parser", sel, "container"))

    holder["build"] = build
    return holder


def _btn(user, text):
    return next(e for e in everything(user) if isinstance(e, ui.button) and e.text == text)


def test_prompt_stay(env, prompt_setup):
    async def check(user):
        user.find("go").click()
        await user.should_see("You have unsaved changes to a. Save them before switching?")
        fire(user, _btn(user, "Stay"))
        assert prompt_setup["applied"] == [] and prompt_setup["sel"].value == "a"
        assert cs.form_data_store["a"] == {"x": 2}

    run_ui(prompt_setup["build"], check)


def test_prompt_discard_and_switch(env, prompt_setup):
    async def check(user):
        user.find("go").click()
        fire(user, _btn(user, "Discard and switch"))
        assert "a" not in cs.form_data_store
        assert prompt_setup["applied"] == ["b"] and prompt_setup["sel"].value == "b"

    run_ui(prompt_setup["build"], check)


def test_prompt_save_and_switch(env, prompt_setup, monkeypatch):
    saves = []

    def save_ok(p, s, c):
        saves.append(s.value)
        cs._disk_baselines["a"] = dict(cs.form_data_store["a"])

    monkeypatch.setattr(cs, "save_config", save_ok)

    async def check(user):
        user.find("go").click()
        fire(user, _btn(user, "Save and switch"))
        assert saves == ["a"] and prompt_setup["applied"] == ["b"] and prompt_setup["sel"].value == "b"

    run_ui(prompt_setup["build"], check)


def test_prompt_save_fails_stays(env, prompt_setup, monkeypatch):
    monkeypatch.setattr(cs, "save_config", lambda p, s, c: None)

    async def check(user):
        user.find("go").click()
        fire(user, _btn(user, "Save and switch"))
        assert prompt_setup["applied"] == [] and prompt_setup["sel"].value == "a"

    run_ui(prompt_setup["build"], check)


# ---------------------------------------------------------------- render_config_ui

def test_build_form_data(env):
    cs.form_data_store["t"] = {"A": "edited"}
    cfg = {"elements": [{"id": "A", "value": "disk"}, {"id": "B", "value": 2}, {"id": "C"},
                        {"type": "separator", "id": "S", "label": "x"}, "junk", {"value": "noid"}]}
    assert cs._build_form_data("t", cfg) == {"A": "edited", "B": 2, "C": ""}
    assert cs._build_form_data("new", {}) == {}


def _sections_cfg(n):
    els = []
    for i in range(n):
        els.append({"type": "separator", "label": f"Sec{i}"})
        els.append({"type": "text", "id": f"T{i}", "label": f"Text {i}", "value": f"v{i}"})
    return {"elements": els}


def test_render_config_ui_sections_and_after_render(env, monkeypatch):
    parser = FakeParser({"t": _sections_cfg(4)})
    holder = {}
    cs._custom_sources_ctx.update(form_id="F", nav_list=FakeEl(), meta_label=FakeEl())

    def build():
        holder["c"] = ui.element("div")
        cs.render_config_ui(parser, "t", holder["c"], search_term="ignored")

    async def check(user):
        handles = cs._sections()
        assert [h.section.title for h in handles] == ["Sec0", "Sec1", "Sec2", "Sec3"]
        assert [h.is_open for h in handles] == [True, True, True, False]
        assert "is-closed" in handles[3].root.classes
        assert handles[0].dom_id == "F-s0" and handles[0].root.props["id"] == "F-s0"
        assert cs._custom_sources_ctx["meta_label"].text == "4 settings · 4 sections"
        assert cs._disk_baselines["t"] == {f"T{i}": f"v{i}" for i in range(4)}
        assert any("f.scrollTop = 0" in j for j in env.js)
        refreshed = []
        monkeypatch.setattr(cs, "_schedule_template_preview_refresh", lambda: refreshed.append(1))
        assert env.scheduled[-1].delay == 0.1
        env.scheduled[-1].cb()
        assert refreshed == [1]
        assert cs._form_state["empty"].visible is False

    run_ui(build, check)


def test_render_config_ui_keeps_baseline_and_edits(env):
    parser = FakeParser({"t": _sections_cfg(1)})
    cs.form_data_store["t"] = {"T0": "edited"}
    holder = {}

    def build():
        holder["c"] = ui.element("div")
        cs.render_config_ui(parser, "t", holder["c"])

    async def check(user):
        assert "t" not in cs._disk_baselines  # not fresh from disk: no baseline recorded
        inp = next(e for e in holder["c"].descendants() if isinstance(e, ui.input))
        assert inp.value == "edited"

    run_ui(build, check)


def test_render_config_ui_empty_template_and_filtering(env, monkeypatch):
    parser = FakeParser({"t": {"elements": []}})
    holder = {}
    cs._form_state["search"] = "x"
    applied = []
    real_apply = cs._apply_filters
    monkeypatch.setattr(cs, "_apply_filters", lambda: applied.append(1) or real_apply())
    monkeypatch.setattr(ui, "run_javascript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no page")))

    def build():
        holder["c"] = ui.element("div")
        cs.render_config_ui(parser, "t", holder["c"])

    async def check(user):
        assert "This template has no settings" in texts(holder["c"])
        assert applied == [1]

    run_ui(build, check)


def test_after_render_and_number_change(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_refresh_preview_dirty_label", lambda n: calls.append(("label", n)))
    monkeypatch.setattr(cs, "_schedule_template_preview_refresh", lambda: calls.append(("refresh",)))
    cs._after_render("t")
    assert calls == [("label", "t"), ("refresh",)]
    fd = {}
    cs.handle_number_change(SimpleNamespace(value=5), "N", fd)
    assert fd == {"N": 5}


# ---------------------------------------------------------------- assets

def test_asset_media_url(env, monkeypatch):
    (env.assets / "a.png").write_bytes(b"x")
    monkeypatch.setattr(file_picker, "_media_url", lambda path, root: f"/m/{path.name}")
    assert cs._asset_media_url("/assets/a.png") == "/m/a.png"
    assert cs._asset_media_url("/assets/missing.png") == ""
    assert cs._asset_media_url("/elsewhere/a.png") == ""
    monkeypatch.setattr(file_picker, "_media_url", lambda path, root: (_ for _ in ()).throw(RuntimeError()))
    assert cs._asset_media_url("/assets/a.png") == ""


@pytest.mark.parametrize(
    "spec,value,icon",
    [
        (cs.AssetSpec("audio", (), mode="folder"), "/assets/x/", "folder"),
        (cs.AssetSpec("image", ()), "/assets/missing.png", "image"),
        (cs.AssetSpec("image", ()), "a.png", "image"),
        (cs.AssetSpec("media", ()), "/assets/a.mp3", "music_note"),
        (cs.AssetSpec("audio", ()), "", "music_note"),
        (cs.AssetSpec("media", ()), "/assets/a.webm", "movie"),
        (cs.AssetSpec("video", ()), "", "movie"),
        (cs.AssetSpec("media", ()), "/assets/a.ttf", "font_download"),
        (cs.AssetSpec("font", ()), "x.bin", "font_download"),
        (cs.AssetSpec("image", ()), "", "image"),
        (cs.AssetSpec("media", ()), "/assets/a.bin", "insert_drive_file"),
    ],
)
def test_asset_thumb_html_icons(env, spec, value, icon):
    assert cs._asset_thumb_html(spec, value) == cs._icon_html(icon)


def test_asset_thumb_html_image_url(env, monkeypatch):
    monkeypatch.setattr(cs, "_asset_media_url", lambda web: '/m/a"b.png')
    assert cs._asset_thumb_html(cs.AssetSpec("image", ()), "/assets/a.png") == '<img src="/m/a&quot;b.png" alt="">'


@pytest.fixture
def picker(monkeypatch):
    calls = []
    monkeypatch.setattr(file_picker, "open_file_picker", lambda **kw: calls.append(kw))
    return calls


def test_attach_asset_browse_file(env, picker):
    holder = {}
    spec = cs.AssetSpec("image", (".png", ".gif"), style="relative")

    def build():
        holder["inp"] = ui.input(value="pics/a.png")
        holder["btn"] = cs._attach_asset_browse(holder["inp"], "Logo", spec)

    async def check(user):
        fire(user, holder["btn"])
        kw = picker[0]
        assert kw["title"] == "Choose an image for Logo"
        assert kw["subtitle"] == "PNG, GIF from your assets folder"
        assert kw["start"] == "/assets/pics/a.png" and kw["within"] is None and kw["within_hint"] == ""
        assert (kw["mode"], kw["kind"], kw["icon"]) == ("file", "image", "image")
        kw["on_pick"](SimpleNamespace(web_path="/assets/new/b.png", web_dir="/assets/new/"))
        assert holder["inp"].value == "new/b.png"
        kw["on_pick"](SimpleNamespace(web_path="", web_dir=""))
        assert holder["inp"].value == "new/b.png"

    run_ui(build, check)


def test_attach_asset_browse_folder_and_filename(env, picker):
    holder = {}
    folder_spec = cs.AssetSpec("audio", (".mp3",), mode="folder")
    font_spec = cs.AssetSpec("font", (".ttf",), style="filename", base_dir="/assets/default_assets/fonts/")

    def build():
        holder["a"] = ui.input(value="")
        holder["b1"] = cs._attach_asset_browse(holder["a"], "", folder_spec)
        holder["f"] = ui.input(value="x.ttf")
        holder["b2"] = cs._attach_asset_browse(holder["f"], "Font", font_spec)

    async def check(user):
        fire(user, holder["b1"])
        kw = picker[-1]
        assert kw["title"] == "Choose a folder" and kw["start"] is None
        assert (kw["mode"], kw["icon"]) == ("folder", "folder")
        kw["on_pick"](SimpleNamespace(web_path="/assets/s/a.mp3", web_dir="/assets/s/"))
        assert holder["a"].value == "/assets/s/"
        fire(user, holder["b2"])
        kw = picker[-1]
        assert kw["kind"] == "file" and kw["icon"] == "font_download"
        assert kw["within"] == env.assets.resolve() / "default_assets/fonts/"
        assert kw["within_hint"] == "This setting reads files from /assets/default_assets/fonts/"
        assert kw["start"] == "/assets/default_assets/fonts/x.ttf"

    run_ui(build, check)


def test_row_text_html():
    h = cs._row_text_html("Id<", "Label&", "")
    assert 'data-revert="Id&lt;"' in h and "Label&amp;" in h and "ssx-row-sub" not in h
    h2 = cs._row_text_html("Id", "L", 'Say "hi"')
    assert '<div class="ssx-row-sub" title="Say &quot;hi&quot;">Say &quot;hi&quot;</div>' in h2


# ---------------------------------------------------------------- controls

def render(holder, element, form_data, config_name="tpl"):
    def build():
        holder["fd"] = form_data
        cs.form_data_store[config_name] = form_data
        holder["h"] = cs.render_form_element(element, form_data, config_name)
    return build


def test_textarea_row(env):
    holder = {}

    async def check(user):
        h = holder["h"]
        assert "is-stack" in h.row.classes and h.element_id == "Body"
        area = next(e for e in h.row.descendants() if isinstance(e, ui.textarea))
        assert area.value == "hi"
        area.value = "changed"
        assert holder["fd"]["Body"] == "changed"
        h.set_value("again")
        assert area.value == "again"

    run_ui(render(holder, {"type": "textarea", "id": "Body", "value": "hi"}, {}), check)


def test_text_row_uses_form_value_and_label_default(env):
    holder = {}

    async def check(user):
        h = holder["h"]
        inp = next(e for e in h.row.descendants() if isinstance(e, ui.input))
        assert inp.value == "typed"
        html = next(e for e in h.row.descendants() if isinstance(e, ui.html)).content
        assert "Title" in html  # label falls back to the id
        inp.value = "new"
        assert holder["fd"]["Title"] == "new"
        assert env.scheduled  # preview refresh queued

    run_ui(render(holder, {"type": "text", "id": "Title", "value": "disk"}, {"Title": "typed"}), check)


def test_asset_text_row_updates_thumb(env, picker):
    holder = {}

    async def check(user):
        h = holder["h"]
        thumb = next(e for e in h.row.descendants() if "ssx-thumb" in e.classes)
        assert thumb.content == cs._icon_html("music_note")
        inp = next(e for e in h.row.descendants() if isinstance(e, ui.input))
        assert "is-asset" in inp.classes
        inp.value = "/assets/v.mp4"
        assert holder["fd"]["Sound"] == "/assets/v.mp4"
        assert thumb.content == cs._icon_html("movie")
        browse = next(e for e in h.row.descendants() if e.props.get("aria-label") == "Browse the assets folder")
        fire(user, browse)
        assert picker[0]["title"].endswith("for Death sound")

    run_ui(render(holder, {"type": "text", "id": "Sound", "label": "Death sound", "value": "/assets/a.mp3"}, {}), check)


def test_select_row_adds_unknown_value(env):
    holder = {}

    async def check(user):
        sel = next(e for e in holder["h"].row.descendants() if isinstance(e, ui.select))
        assert sel.options == ["custom", "a", "b"] and sel.value == "custom"
        sel.value = "b"
        assert holder["fd"]["Mode"] == "b"
        holder["h"].set_value("a")
        assert sel.value == "a"

    run_ui(render(holder, {"type": "select", "id": "Mode", "options": ["a", "b"], "value": "custom"}, {}), check)


@pytest.mark.parametrize(
    "fonts,value,expected",
    [
        (["a.ttf", "b.otf"], "c.woff", ["c.woff", "a.ttf", "b.otf"]),
        (["a.ttf"], "a.ttf", ["a.ttf"]),
        ([], "x.ttf", ["x.ttf"]),
        ([], None, []),
    ],
)
def test_select_row_fonts(env, monkeypatch, fonts, value, expected):
    monkeypatch.setattr(cs, "_list_default_font_basenames", lambda: list(fonts))
    holder = {}

    async def check(user):
        sel = next(e for e in holder["h"].row.descendants() if isinstance(e, ui.select))
        assert sel.options == expected

    element = {"type": "select", "id": "Font", "options_from": "fonts", "options": ["ignored"], "value": value}
    run_ui(render(holder, element, {}), check)


@pytest.mark.parametrize("element", [
    {"type": "select", "id": "Mode", "options": ["a", "b"]},
    {"type": "select", "id": "Font", "options_from": "fonts", "value": ""},
])
def test_select_row_with_empty_value_renders(env, monkeypatch, element):
    monkeypatch.setattr(cs, "_list_default_font_basenames", lambda: ["a.ttf"])
    holder = {}

    def build():
        try:
            render(holder, element, {})()
        except ValueError as exc:
            holder["error"] = exc

    run_ui(build)
    assert "error" not in holder, holder.get("error")


REAL_COLOR_PARAMS = cs._color_field_params()


def test_select_color_grid_uses_color_field(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "render_color_field", lambda **kw: calls.append(kw))
    monkeypatch.setattr(cs, "_color_field_params", lambda: REAL_COLOR_PARAMS)
    holder = {}

    async def check(user):
        assert calls[0]["preset_options"] == ["#fff", "#000"] and calls[0]["value"] == "#fff"
        calls[0]["on_change"]("#000")
        assert holder["fd"]["C"] == "#000"
        calls[0]["on_input"]("#111")
        assert holder["fd"]["C"] == "#111"
        holder["h"].set_value("#222")
        assert calls[-1]["value"] == "#222"

    element = {"type": "select", "id": "C", "display": "color_grid", "options": ["#fff", "#000"], "value": "#fff"}
    run_ui(render(holder, element, {}), check)


def test_color_row_variants(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "render_color_field", lambda **kw: calls.append(kw))
    monkeypatch.setattr(cs, "_color_field_params", lambda: {"element_id", "on_input", "allow_gradient"})
    holder = {}

    def build():
        fd = {}
        cs.form_data_store["tpl"] = fd
        holder["g"] = cs.render_form_element({"type": "color", "id": "G", "value": "linear-gradient(red, blue)"}, fd, "tpl")
        holder["p"] = cs.render_form_element({"type": "color", "id": "P", "value": "#fff", "options": ("#fff",)}, fd, "tpl")

    async def check(user):
        assert calls[0]["allow_gradient"] is True and calls[0]["preset_options"] is None
        assert "allow_gradient" not in calls[1] and calls[1]["preset_options"] == ["#fff"]

    run_ui(build, check)


def test_color_row_old_picker_without_on_input(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "render_color_field", lambda **kw: calls.append(kw))
    monkeypatch.setattr(cs, "_color_field_params", lambda: set())
    holder = {}

    async def check(user):
        assert "on_input" not in calls[0] and "allow_gradient" not in calls[0]

    run_ui(render(holder, {"type": "color", "id": "C", "value": "linear-gradient(red, blue)"}, {}), check)


def test_color_row_real_field_renders(env):
    holder = {}

    async def check(user):
        box = next(e for e in holder["h"].row.descendants() if "ssx-color" in e.classes)
        before = len(list(box.descendants()))
        holder["h"].set_value("#00ff00")
        assert len(list(box.descendants())) == before

    run_ui(render(holder, {"type": "color", "id": "C", "value": "#ff0000"}, {}), check)


def test_number_row_with_unit_and_step(env):
    holder = {}

    async def check(user):
        num = next(e for e in holder["h"].row.descendants() if isinstance(e, ui.number))
        assert num.props.get("step") == 0.5 and num.props.get("min") == 0
        unit = next(e for e in holder["h"].row.descendants() if "ssx-unit" in e.classes)
        assert unit.text == "px"
        num.value = 4
        assert holder["fd"]["W"] == 4

    run_ui(render(holder, {"type": "number", "id": "W", "label": "Width (px)", "value": 3, "min": 0, "step": 0.5}, {}), check)


def test_number_row_without_unit(env):
    holder = {}

    async def check(user):
        assert not any("ssx-unit" in e.classes for e in holder["h"].row.descendants())

    run_ui(render(holder, {"type": "number", "id": "N", "value": 1}, {}), check)


@pytest.mark.parametrize(
    "element,start,readout,new,new_readout",
    [
        ({"min": 0, "max": 10, "step": 1, "value": "4"}, 4, "4", 6.4, "6"),
        ({"min": 0, "max": 1, "step": 0.1, "value": "0.5"}, 0.5, "0.5", 0.7, "0.7"),
        ({"min": 2, "max": 9, "value": ""}, 2, "2", 3, "3"),
        ({"min": 0, "max": 1, "step": 0.5, "value": ""}, 0, "0.0", 1, "1.0"),
        ({"min": 1, "max": 9, "value": "abc"}, 1, "1", 2, "2"),
    ],
)
def test_slider_row(env, element, start, readout, new, new_readout):
    holder = {}

    async def check(user):
        slider = next(e for e in holder["h"].row.descendants() if isinstance(e, ui.slider))
        label = next(e for e in holder["h"].row.descendants() if "ssx-readout" in e.classes)
        assert slider.value == start and label.text == readout
        slider.value = new
        assert holder["fd"]["S"] == new and label.text == new_readout

    run_ui(render(holder, {"type": "slider", "id": "S", **element}, {}), check)


def test_slider_readout_non_number(env):
    holder = {}

    async def check(user):
        slider = next(e for e in holder["h"].row.descendants() if isinstance(e, ui.slider))
        label = next(e for e in holder["h"].row.descendants() if "ssx-readout" in e.classes)
        slider._change_handlers[-1](SimpleNamespace(value="n/a"))
        assert label.text == "n/a" and holder["fd"]["S"] == "n/a"

    run_ui(render(holder, {"type": "slider", "id": "S", "value": 0}, {}), check)


def test_checkbox_and_unknown_rows(env):
    holder = {}

    def build():
        fd = {}
        cs.form_data_store["tpl"] = fd
        holder["fd"] = fd
        holder["cb"] = cs.render_form_element({"type": "checkbox", "id": "On", "value": False}, fd, "tpl")
        holder["u"] = cs.render_form_element({"type": "weird", "id": "W", "value": "v"}, fd, "tpl")

    async def check(user):
        sw = next(e for e in holder["cb"].row.descendants() if isinstance(e, ui.switch))
        sw.value = True
        assert holder["fd"]["On"] is True
        inp = next(e for e in holder["u"].row.descendants() if isinstance(e, ui.input))
        inp.value = "w2"
        assert holder["fd"]["W"] == "w2"

    run_ui(build, check)


# ---------------------------------------------------------------- form data updates

def test_update_roulette_expansion_title(env):
    nav = FakeEl()
    cs._custom_sources_ctx["nav_list"] = nav
    sec = cs._SectionHandle(section=cs.SettingsSection(key="o2", title="Option 2", option_num=2), root=FakeEl(),
                            head=FakeEl(), rows=[], dom_id="d", is_open=True)
    other = cs._SectionHandle(section=cs.SettingsSection(key="g", title="General"), root=FakeEl(), head=FakeEl(),
                              rows=[], dom_id="d", is_open=True)
    cs._form_state.update(sections=[other, sec], config="roulette")
    cs.update_roulette_expansion_title(2, "Blue")
    assert sec.section.title == "Option 2 (Blue)" and "Option 2 (Blue)" in sec.head.content
    assert "Option 2 (Blue)" in nav.content
    cs.update_roulette_expansion_title(9, "x")


def test_update_form_data(env, monkeypatch, caplog):
    labels, titles = [], []
    monkeypatch.setattr(cs, "_refresh_preview_dirty_label", labels.append)
    monkeypatch.setattr(cs, "update_roulette_expansion_title", lambda n, v: titles.append((n, v)))
    orphan = {}
    cs.update_form_data(orphan, "x", 1)
    assert orphan == {"x": 1} and labels == [] and len(env.scheduled) == 1
    combo = {}
    cs.form_data_store["combobar"] = combo
    import logging

    with caplog.at_level(logging.INFO, logger=cs.logger.name):
        cs.update_form_data(combo, "Tier2EXP", 50)
    assert "ComboBar config change: Tier2EXP changed from NOT_SET to 50" in caplog.text
    assert labels == ["combobar"]
    roul = {}
    cs.form_data_store["roulette"] = roul
    cs.update_form_data(roul, "Option3Name", "Green")
    cs.update_form_data(roul, "Option3Enabled", True)
    cs.update_form_data(roul, "OptionXName", "?")
    assert titles == [(3, "Green")]


def test_notify_no_template_and_technical_error(env):
    cs._notify_no_template()
    assert env.titles() == ["Choose a template first"]
    plain = ValueError("Choose a different name.")
    assert cs._is_technical_error(plain) is False
    assert cs._is_technical_error(OSError("[Errno 13] denied")) is True
    assert cs._is_technical_error(ValueError("C:\\x")) is True
    wrapped = ValueError("x")
    wrapped.__cause__ = OSError("y")
    assert cs._is_technical_error(wrapped) is True

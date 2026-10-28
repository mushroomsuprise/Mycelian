# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/customsources.py, part 2: sections, search, asset specs and form state.

Pure helpers (sections, filters, units, steps, asset browse specs) and the form-state
helpers (outline, jump list, meta, dirty pill, filters, revert) driven with fake elements.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from _customsources_support import FakeEl, fire, make_env  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import customsources as cs  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    return make_env(monkeypatch, tmp_path)


def sep(label):
    return {"type": "separator", "label": label}


def el(eid, type_="text", **kw):
    return {"id": eid, "type": type_, "label": kw.pop("label", eid), "value": kw.pop("value", ""), **kw}


# ---------------------------------------------------------------- sections

def test_settings_section_defaults():
    s = cs.SettingsSection(key="k", title="T")
    assert s.elements == [] and s.option_num is None


def test_roulette_helpers():
    assert cs.roulette_option_number("Option12Name") == 12
    assert cs.roulette_option_number("Wheel") is None
    assert cs.roulette_option_number(None) is None
    assert cs.roulette_option_title(3, "") == "Option 3"
    assert cs.roulette_option_title(3, "Red") == "Option 3 (Red)"


def test_build_sections_blank_separator_and_none_elements():
    secs = cs.build_sections("x", [el("A"), sep(""), el("B"), sep("  "), el("C")])
    assert [s.title for s in secs] == ["General", "Settings"]
    assert [e["id"] for e in secs[1].elements] == ["B", "C"]
    assert [s.key for s in secs] == ["g", "s1"]
    assert cs.build_sections("x", None) == []


def test_build_sections_roulette_option_names():
    elements = [el("Option1Name", value="FromFile"), el("Option2Enabled", "checkbox"), el("Speed")]
    secs = cs.build_sections("roulette", elements, {})
    assert [s.title for s in secs] == ["General", "Option 1 (FromFile)", "Option 2"]
    assert secs[1].key == "o1" and secs[2].option_num == 2
    secs = cs.build_sections("roulette", elements, {"Option1Name": "Typed"})
    assert secs[1].title == "Option 1 (Typed)"


def test_group_config_elements():
    groups = cs.group_config_elements([el("A"), sep("Colors"), el("B"), sep("Empty"), sep("Colors"), el("C")])
    assert {k: [e["id"] for e in v] for k, v in groups.items()} == {"Other": ["A"], "Colors": ["B", "C"], "Empty": []}


def test_search_helpers():
    assert cs.normalize_search("  HeLLo ") == "hello"
    assert cs.normalize_search(None) == ""
    e = {"id": "FontSize", "description": "Size of the TEXT"}
    assert cs.setting_matches(e, "")
    assert cs.setting_matches(e, "fontsize")
    assert cs.setting_matches(e, "text")
    assert not cs.setting_matches(e, "color")
    assert cs.setting_matches({"label": "Color"}, "col")
    assert cs.row_matches_filters(e, "", False, set())
    assert not cs.row_matches_filters(e, "", True, set())
    assert cs.row_matches_filters(e, "font", True, {"FontSize"})
    assert not cs.row_matches_filters({}, "", True, {""}) is False  # empty id matches empty string id


def test_filter_sections_and_changed_ids():
    secs = cs.build_sections("x", [el("A", label="Alpha"), sep("S"), el("B", label="Beta")])
    out = cs.filter_sections(secs, "beta")
    assert [(s.title, [e["id"] for e in kept]) for s, kept in out] == [("S", ["B"])]
    assert cs.filter_sections(secs, "", True) == []
    assert len(cs.filter_sections(secs)) == 2
    assert cs.changed_setting_ids(None, None) == set()
    assert cs.changed_setting_ids({"a": 1, "b": "x"}, {"a": 1.0, "b": "y", "c": ""}) == {"b"}


@pytest.mark.parametrize(
    "element,unit",
    [
        ({"label": "Width (pixels)"}, "px"),
        ({"label": "Delay", "description": "Wait (Seconds) first"}, "s"),
        ({"label": "Turn (degrees)"}, "deg"),
        ({"label": "Fade (ms)"}, "ms"),
        ({"label": "Opacity (%)"}, "%"),
        ({"label": "Spin(deg)"}, ""),
        ({"label": "Plain"}, ""),
        ({}, ""),
    ],
)
def test_setting_unit(element, unit):
    assert cs.setting_unit(element) == unit


@pytest.mark.parametrize(
    "raw,step",
    [(None, None), (True, None), ("x", None), ([1], None), (0, None), (-1, None), (float("nan"), None),
     (5, 5), ("2.0", 2), (0.25, 0.25)],
)
def test_number_step(raw, step):
    assert cs.number_step({"step": raw} if raw is not None else {}) == step


def test_is_gradient_value():
    assert cs.is_gradient_value("linear-GRADIENT(red, blue)")
    assert not cs.is_gradient_value("#fff")
    assert not cs.is_gradient_value(None)


# ---------------------------------------------------------------- asset specs

def test_asset_ext_lists_and_small_helpers():
    lists = cs._asset_ext_lists()
    assert ".png" in lists["image"] and ".mp3" in lists["audio"] and ".mp4" in lists["video"]
    assert lists["font"] == cs.FONT_FILE_EXTS
    assert set(lists["media"]) == set(lists["image"]) | set(lists["audio"]) | set(lists["video"])
    assert cs._words("BackgroundImageURL") == ["background", "image", "url"]
    assert cs._words("bit_image_range_0_src") == ["bit", "image", "range", "0", "src"]
    assert cs._words("HTMLFile") == ["html", "file"]
    assert cs._words(None) == []
    assert cs._ext_of("/a/b.PNG?x=1#f") == ".png"
    assert cs._ext_of("noext") == ""
    assert cs._value_style("/assets/a.png") == "web"
    assert cs._value_style("assets\\a.png") == "assets"
    assert cs._value_style("sub/a.png") == "relative"
    assert cs._value_style("a.png") == "filename"


S = cs._asset_browse_spec


@pytest.mark.parametrize(
    "element,value,config,expect",
    [
        # not text/file
        ({"type": "number", "id": "ImageSize"}, "", "", None),
        # multi-value description
        ({"type": "text", "id": "Images", "description": "comma separated list of files"}, "", "", None),
        # value says image (web path)
        ({"type": "text", "id": "Thing"}, "/assets/x/a.png", "", ("image", "file", "web", "")),
        # saved value says audio filename -> base from config
        ({"type": "text", "id": "Thing", "value": "boom.mp3"}, "", "tpl", ("audio", "file", "filename", "/assets/tpl/")),
        # filename without config -> web
        ({"type": "text", "id": "Thing", "value": "boom.mp3"}, "", "", ("audio", "file", "web", "")),
        # filename with assets dir in description
        ({"type": "text", "id": "Sound", "value": "a.wav",
          "description": "File name in /assets/sounds/ folder"}, "", "tpl", ("audio", "file", "filename", "/assets/sounds/")),
        # path value with unknown extension -> media
        ({"type": "text", "id": "Thing"}, "assets/x/data.bin", "", ("media", "file", "assets", "")),
        # CSS-ish name never a file
        ({"type": "text", "id": "ImageShadow"}, "", "", None),
        # but "file" words override
        ({"type": "text", "id": "ImageSizeFile"}, "", "", ("image", "file", "web", "")),
        # accept attribute decides kind
        ({"type": "file", "id": "Upload", "accept": "video/*"}, "", "", ("video", "file", "web", "")),
        # file type without hints -> media
        ({"type": "file", "id": "Thing"}, "", "", ("media", "file", "web", "")),
        # name words: kind + file word
        ({"type": "text", "id": "DeathSoundFile"}, "", "", ("audio", "file", "web", "")),
        # media noun alone
        ({"type": "text", "id": "BannerBackgroundImage"}, "", "", ("image", "file", "web", "")),
        # kind word without file word or noun -> not a file
        ({"type": "text", "id": "SoundName"}, "", "", None),
        # description says URL
        ({"type": "text", "id": "Music", "description": "The url of the track"}, "", "", ("audio", "file", "web", "")),
        # font needs "file"
        ({"type": "text", "id": "FontName", "description": "path to it"}, "", "", None),
        ({"type": "text", "id": "FontFile"}, "", "", ("font", "file", "filename", cs.DEFAULT_FONTS_WEB_DIR)),
        ({"type": "text", "id": "Font", "description": "Font file for titles"}, "", "", ("font", "file", "filename", cs.DEFAULT_FONTS_WEB_DIR)),
        # folder mode
        ({"type": "text", "id": "SoundFolder"}, "/assets/sfx/", "", ("audio", "folder", "web", "")),
        # filename description without sample
        ({"type": "text", "id": "AlertImage", "description": "image file name"}, "", "tpl", ("image", "file", "filename", "/assets/tpl/")),
        # plain text
        ({"type": "text", "id": "Title"}, "Hello", "", None),
        ({"id": "Title"}, "", "", None),
    ],
)
def test_asset_browse_spec(element, value, config, expect):
    spec = S(element, value, config)
    if expect is None:
        assert spec is None
    else:
        assert (spec.kind, spec.mode, spec.style, spec.base_dir) == expect
        assert spec.extensions == cs._asset_ext_lists()[spec.kind]


@pytest.mark.parametrize(
    "spec,value,web",
    [
        (cs.AssetSpec("image", (), style="filename", base_dir="/assets/t/"), "", "/assets/t/"),
        (cs.AssetSpec("image", (), style="web"), "", ""),
        (cs.AssetSpec("image", ()), "/assets/a.png", "/assets/a.png"),
        (cs.AssetSpec("image", ()), "assets\\a.png", "/assets/a.png"),
        (cs.AssetSpec("image", (), style="filename", base_dir="/assets/t/"), "a.png", "/assets/t/a.png"),
        (cs.AssetSpec("image", (), style="relative"), "x.png", "/assets/x.png"),
        (cs.AssetSpec("image", ()), "/x/y.png", "/assets/x/y.png"),
        (cs.AssetSpec("image", ()), "a.png", ""),
    ],
)
def test_asset_value_to_web_path(spec, value, web):
    assert cs.asset_value_to_web_path(spec, value) == web


@pytest.mark.parametrize(
    "spec,picked,value",
    [
        (cs.AssetSpec("image", (), style="assets"), "/assets/a/b.png", "assets/a/b.png"),
        (cs.AssetSpec("image", (), style="relative"), "/assets/a/b.png", "a/b.png"),
        (cs.AssetSpec("image", (), style="relative"), "/other/b.png", "/other/b.png"),
        (cs.AssetSpec("image", (), style="filename", base_dir="/assets/a/"), "/assets/a/b.png", "b.png"),
        (cs.AssetSpec("image", (), style="filename", base_dir="/assets/a/"), "/assets/z/b.png", "/assets/z/b.png"),
        (cs.AssetSpec("image", ()), None, ""),
    ],
)
def test_asset_value_from_pick(spec, picked, value):
    assert cs.asset_value_from_pick(spec, picked) == value


# ---------------------------------------------------------------- small render helpers

def test_esc_and_icon_html():
    assert cs._esc('<"a">') == "&lt;&quot;a&quot;&gt;"
    assert cs._esc(None) == ""
    assert cs._icon_html("x<") == '<span class="ssx-mi">x&lt;</span>'


def test_icon_button(env):
    holder, clicks = {}, []

    def build():
        holder["b"] = cs._icon_button("delete", "Delete it", lambda: clicks.append(1), danger=True, small=True)
        holder["q"] = cs._icon_button("x", "", lambda: clicks.append(2))

    async def check(user):
        b = holder["b"]
        assert b.classes == ["ssx-ibtn", "is-danger", "is-small"]
        assert b.props["aria-label"] == "Delete it"
        tips = {e.props.get("target"): e.text for e in user.client.layout.descendants() if isinstance(e, ui.tooltip)}
        assert tips == {f"#{b.html_id}": "Delete it"}
        fire(user, b)
        fire(user, holder["q"])
        assert clicks == [1, 2]

    run_ui(build, check)


def test_inject_source_settings_styles(monkeypatch):
    calls = []
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: calls.append((sid, css)))
    cs._inject_source_settings_styles()
    assert calls[0][0] == "mycelian-source-settings" and ".ssx" in calls[0][1]


def test_color_field_params(monkeypatch):
    monkeypatch.setattr(cs, "_color_field_params_cache", None)
    params = cs._color_field_params()
    assert {"on_input", "allow_gradient", "element_id"} <= params
    assert cs._color_field_params() is params
    monkeypatch.setattr(cs, "_color_field_params_cache", None)
    monkeypatch.setattr(cs.inspect, "signature", lambda f: (_ for _ in ()).throw(ValueError("no sig")))
    assert cs._color_field_params() == set()


# ---------------------------------------------------------------- form state with fake elements

def make_state(env, sections_spec, *, config="tpl"):
    """sections_spec: list of (key, title, [element dicts]); builds handles with fake elements."""
    handles, rows = [], {}
    for key, title, elements in sections_spec:
        section = cs.SettingsSection(key=key, title=title, elements=list(elements))
        row_handles = []
        for element in elements:
            h = cs._RowHandle(element_id=element["id"], element=element, row=FakeEl(), set_value=lambda v: None)
            row_handles.append(h)
            rows[element["id"]] = h
        handles.append(cs._SectionHandle(section=section, root=FakeEl(), head=FakeEl(), rows=row_handles,
                                         dom_id=f"f-{key}", is_open=True))
    cs._form_state.update({"config": config, "sections": handles, "rows": rows, "changed": set(), "nav_html": None})
    return handles, rows


def test_sections_lookup_and_filtering(env):
    assert cs._sections() == []
    handles, _ = make_state(env, [("g", "General", [el("A")]), ("s1", "Colors", [el("B")])])
    assert cs._section_by_key("s1") is handles[1]
    assert cs._section_by_key("zz") is None
    assert cs._filtering() is False
    cs._form_state["search"] = "  x "
    assert cs._filtering() is True
    cs._form_state["search"] = ""
    cs._form_state["changed_only"] = True
    assert cs._filtering() is True


def test_section_head_and_open(env):
    handles, _ = make_state(env, [("g", "A<B", [el("A"), el("B")])])
    sec = handles[0]
    assert cs._section_head_html("T", 3) == (
        '<span class="ssx-sec-title" title="T">T</span><span class="n">3</span>'
        '<span class="ssx-mi chev">expand_more</span>')
    cs._update_section_head(sec)
    assert '<span class="n">2</span>' in sec.head.content and "A&lt;B" in sec.head.content
    sec.head.content = "same"
    cs._update_section_head(sec)
    assert sec.head.content == "same"  # unchanged html not resent
    cs._form_state["search"] = "a"
    sec.shown = 1
    cs._update_section_head(sec)
    assert '<span class="n">1</span>' in sec.head.content
    cs._set_section_open(sec, True)
    assert sec.root.class_ops == []
    cs._set_section_open(sec, False)
    assert sec.root.class_ops == [("is-closed", None)] and sec.is_open is False
    cs._set_section_open(sec, True)
    assert sec.root.class_ops[-1] == (None, "is-closed")


def test_sync_nav_js(env, monkeypatch):
    cs._sync_nav_js()
    assert env.js == []
    cs._custom_sources_ctx.update(form_id="f", nav_id="n")
    cs._sync_nav_js()
    assert env.js[-1] == 'window.mycelianSsxBindNav && window.mycelianSsxBindNav("f", "n")'
    monkeypatch.setattr(ui, "run_javascript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    cs._sync_nav_js()


def test_render_nav(env):
    cs._render_nav()  # no nav element
    nav = FakeEl()
    cs._custom_sources_ctx.update(nav_list=nav, form_id="f", nav_id="n")
    handles, _ = make_state(env, [("g", "General", [el("A"), el("B")]), ("s1", "Col&ors", [el("C")])])
    cs._form_state["changed"] = {"C"}
    cs._render_nav()
    assert nav.content.startswith('<div class="ssx-navhead">Sections</div>')
    assert 'data-nav="g"' in nav.content and '<span class="n">2</span>' in nav.content
    assert 'title="Col&amp;ors"' in nav.content
    assert nav.content.count('class="dot"') == 1
    assert len(env.js) == 1
    cs._render_nav()  # same html: no re-send
    assert len(env.js) == 1
    # filtering hides sections without matches and shows "No settings match."
    cs._form_state["search"] = "zzz"
    for h in handles:
        h.shown = 0
    cs._render_nav()
    assert "No settings match." in nav.content
    cs._form_state["config"] = None
    cs._render_nav()
    assert nav.content == '<div class="ssx-navhead">Sections</div>'
    handles[0].shown = 1
    cs._render_nav()
    assert 'data-nav="g"' in nav.content and 'data-nav="s1"' not in nav.content


def test_render_jump(env):
    holder = {}
    make_state(env, [("g", "General", [el("A")]), ("s1", "Colors", [el("B")])])

    def build():
        holder["sel"] = ui.element("select")
        cs._custom_sources_ctx["jump_select"] = holder["sel"]
        cs._render_jump()

    async def check(user):
        opts = [(e.text, e.props.get("value")) for e in holder["sel"].descendants()]
        assert opts == [("Jump to section…", ""), ("General", "g"), ("Colors", "s1")]

    run_ui(build, check)
    cs._custom_sources_ctx.pop("jump_select")
    cs._render_jump()


def test_refresh_meta(env):
    cs._refresh_meta()
    meta = FakeEl()
    cs._custom_sources_ctx["meta_label"] = meta
    cs._form_state["config"] = None
    cs._refresh_meta()
    assert meta.text == ""
    make_state(env, [("g", "General", [el("A")])])
    cs._refresh_meta()
    assert meta.text == "1 setting · 1 section"
    make_state(env, [("g", "General", [el("A"), el("B")]), ("s", "S", [el("C")])])
    cs._refresh_meta()
    assert meta.text == "3 settings · 2 sections"


def test_dirty_pill_html():
    assert cs._dirty_pill_html(None, 3) == ""
    assert "1 unsaved change<" in cs._dirty_pill_html("t", 1)
    assert "2 unsaved changes" in cs._dirty_pill_html("t", 2) and "is-warn" in cs._dirty_pill_html("t", 2)
    assert "Saved" in cs._dirty_pill_html("t", 0) and "is-ok" in cs._dirty_pill_html("t", 0)


def test_refresh_dirty_ui(env):
    pill, discard, nav = FakeEl(), FakeEl(), FakeEl()
    cs._custom_sources_ctx.update(dirty_pill=pill, discard_button=discard, nav_list=nav)
    _, rows = make_state(env, [("g", "General", [el("A"), el("B")])])
    cs._disk_baselines["tpl"] = {"A": 1, "B": 2}
    cs.form_data_store["tpl"] = {"A": 1, "B": 3}
    cs._refresh_dirty_ui()
    assert cs._form_state["changed"] == {"B"}
    assert rows["B"].row.class_ops == [("is-changed", None)] and rows["A"].row.class_ops == []
    assert "1 unsaved change" in pill.content and discard.enabled is True
    assert "dot" in nav.content
    pill.content = "kept"
    cs._refresh_dirty_ui()  # same pill html is not re-sent
    assert pill.content == "kept"
    cs.form_data_store["tpl"]["B"] = 2
    cs._refresh_dirty_ui()
    assert rows["B"].row.class_ops[-1] == (None, "is-changed")
    assert discard.enabled is False and "Saved" in pill.content
    # a changed id without a row is ignored; no baseline -> nothing changed
    cs._form_state["changed"] = {"ghost"}
    cs._disk_baselines.clear()
    cs._refresh_dirty_ui()
    assert cs._form_state["changed"] == set()


def test_refresh_dirty_ui_no_config(env):
    cs._form_state["config"] = None
    cs._refresh_dirty_ui()
    assert cs._form_state["changed"] == set()


def test_apply_filters(env):
    handles, rows = make_state(env, [
        ("g", "General", [el("Alpha", description="first"), el("Beta")]),
        ("s", "Colors", [el("Gamma")]),
    ])
    empty, title, sub = FakeEl(), FakeEl(), FakeEl()
    cs._form_state.update(empty=empty, empty_labels=(title, sub))
    handles[1].is_open = False
    cs._form_state["search"] = "gam"
    cs._apply_filters()
    assert rows["Alpha"].row.visible is False and rows["Gamma"].row.visible is True
    assert handles[0].root.visible is False and handles[0].visible is False
    assert handles[1].is_open is True and handles[1].shown == 1
    assert empty.visible is False
    cs._form_state["search"] = "nothing"
    cs._apply_filters()
    assert empty.visible is True and title.text == "No settings match"
    assert sub.text == "Search looks at setting names and descriptions."
    cs._form_state["changed_only"] = True
    cs._apply_filters()
    assert sub.text.endswith("among your unsaved changes.")
    cs._form_state["search"] = ""
    cs._apply_filters()
    assert title.text == "No unsaved changes"
    cs._form_state["changed_only"] = False
    cs._apply_filters()
    assert all(r.visible for r in rows.values()) and all(h.visible for h in handles)
    assert empty.visible is False


def test_apply_filters_without_empty_labels(env):
    make_state(env, [("g", "General", [el("A")])])
    cs._form_state.update(empty=FakeEl(), empty_labels=None)
    cs._form_state["search"] = "zzz"
    cs._apply_filters()
    cs._form_state.pop("empty")
    cs._apply_filters()


def test_on_search_debounced(env, monkeypatch):
    applied = []
    monkeypatch.setattr(cs, "_apply_filters", lambda: applied.append(cs._form_state["search"]))
    cs._on_search("a")
    cs._on_search("ab")
    assert [h.delay for h in env.scheduled] == [0.2, 0.2]
    env.run_scheduled()
    assert applied == ["ab"]
    cs._on_search(None)
    env.run_scheduled()
    assert applied == ["ab", ""]


def test_on_changed_only(env, monkeypatch):
    applied = []
    monkeypatch.setattr(cs, "_apply_filters", lambda: applied.append(cs._form_state["changed_only"]))
    cs._on_changed_only(1)
    cs._on_changed_only(None)
    assert applied == [True, False]


def test_goto_section(env, monkeypatch):
    handles, _ = make_state(env, [("g", "General", [el("A")]), ("s", "S", [el("B")])])
    cs._goto_section("zz")
    handles[1].visible = False
    cs._goto_section("s")
    assert env.js == []
    handles[1].visible = True
    handles[1].is_open = False
    cs._goto_section("s")  # no form id: opens only
    assert handles[1].is_open is True and env.js == []
    cs._custom_sources_ctx["form_id"] = "form"
    cs._goto_section("s")
    assert env.js[-1] == 'window.mycelianSsxScrollTo && window.mycelianSsxScrollTo("form", "f-s")'
    cs._goto_section(None)
    monkeypatch.setattr(ui, "run_javascript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    cs._goto_section("g")


class Ev:
    def __init__(self, args):
        self.args = args


def test_on_form_click(env, monkeypatch):
    reverted = []
    monkeypatch.setattr(cs, "_revert_setting", reverted.append)
    handles, _ = make_state(env, [("g", "General", [el("A")])])
    cs._on_form_click(Ev(None))
    cs._on_form_click(Ev("str"))
    cs._on_form_click(object())
    cs._on_form_click(Ev({"revert": "A"}))
    assert reverted == ["A"]
    cs._on_form_click(Ev({"sec": "g"}))
    assert handles[0].is_open is False
    cs._on_form_click(Ev({"sec": "g"}))
    assert handles[0].is_open is True
    cs._on_form_click(Ev({"sec": "nope"}))
    cs._on_form_click(Ev({"other": 1}))


def test_revert_setting(env, monkeypatch):
    applied, schedules = [], []
    monkeypatch.setattr(cs, "_apply_filters", lambda: applied.append(1))
    _, rows = make_state(env, [("g", "General", [el("A"), el("B")])])
    shown = []
    rows["A"].set_value = shown.append
    cs._revert_setting("A")  # no form data / baseline
    cs.form_data_store["tpl"] = {"A": [9], "B": 2}
    cs._disk_baselines["tpl"] = {"A": [1]}
    cs._revert_setting("B")  # not in baseline
    assert cs.form_data_store["tpl"]["B"] == 2
    cs._revert_setting("A")
    assert cs.form_data_store["tpl"]["A"] == [1] and shown == [[1]]
    assert cs.form_data_store["tpl"]["A"] is not cs._disk_baselines["tpl"]["A"]
    assert applied == []
    cs._form_state["changed_only"] = True

    def bad(v):
        raise RuntimeError("deleted")

    rows["A"].set_value = bad
    cs.form_data_store["tpl"]["A"] = [5]
    cs._revert_setting("A")
    assert cs.form_data_store["tpl"]["A"] == [1] and applied == [1]
    cs._form_state["rows"] = {}
    cs._revert_setting("A")
    cs._form_state["config"] = None
    cs._revert_setting("A")


def test_on_split_moved(env):
    cs._on_split_moved(Ev(60))
    assert cs._custom_sources_ctx["split_pct"] == 60.0
    assert env.saves[-1] == {"split_editor_pct": 60.0}
    cs._on_split_moved(object())
    assert cs._custom_sources_ctx["split_pct"] == 56.0

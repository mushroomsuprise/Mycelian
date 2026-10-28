# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/customsources.py, part 1: the preview pane.

Fonts list, preview iframe src/size, OBS size lookup, pop-out preview, zoom, split, dirty
tracking, live chip, preview route cache, hot pushes, sample-event toolbars and refresh.
Parts 2 and 3 cover the settings form and the template file actions.
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

from _customsources_support import FakeEl, FakeEngine, FakeParser, FakeSelect, fire, make_env  # noqa: E402
from modules.uiwindows import customsources as cs  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    return make_env(monkeypatch, tmp_path)


def js_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no client")

    monkeypatch.setattr(ui, "run_javascript", boom)


def add_html(env, route):
    (env.templates / f"{route}.html").write_text("<html></html>", encoding="utf-8")


# ---------------------------------------------------------------- fonts / html exists

def test_font_basenames(env, monkeypatch):
    assert cs._list_default_font_basenames() == []
    fonts = env.assets / "default_assets" / "fonts"
    fonts.mkdir(parents=True)
    for name in ("b.TTF", "a.woff2", "readme.txt", "C.otf"):
        (fonts / name).write_bytes(b"")
    assert cs._list_default_font_basenames() == ["a.woff2", "b.TTF", "C.otf"]
    monkeypatch.setattr(cs.os, "listdir", lambda p: (_ for _ in ()).throw(OSError("denied")))
    assert cs._list_default_font_basenames() == []


def test_route_and_template_html_exists(env):
    assert cs._route_html_exists("") is False
    assert cs._template_html_exists("") is False
    assert cs._route_html_exists("chat") is False
    add_html(env, "chat")
    assert cs._route_html_exists("chat") is True
    assert cs._template_html_exists("chat") is True


# ---------------------------------------------------------------- iframe src / clear

def test_assign_preview_iframe_src_with_scale(env):
    cs._assign_preview_iframe_src("inner1", "http://x/a?b=1", 800, 600, scale_outer_id="outer1", zoom_pct=150)
    js = env.js[-1]
    assert '"inner1"' in js and '"http://x/a?b=1"' in js
    assert "iframe.width=800;" in js and "iframe.style.height='600px';" in js
    assert 'window.mycelianCsPreviewScale("outer1","inner1",800,600,150,false);' in js
    assert env.scheduled[0].delay == 0.05
    env.run_scheduled()
    assert env.js[-1] == js and len(env.js) == 2


def test_assign_preview_iframe_src_without_scale(env):
    cs._assign_preview_iframe_src("i", "s", 10, 20)
    assert "onload" not in env.js[-1]


def test_assign_and_clear_errors_are_swallowed(env, monkeypatch):
    js_fails(monkeypatch)
    cs._assign_preview_iframe_src("i", "s", 10, 20)
    cs._clear_preview_iframe("i")
    assert env.scheduled == []


def test_clear_preview_iframe(env):
    cs._clear_preview_iframe(None)
    cs._clear_preview_iframe("")
    assert env.js == []
    cs._clear_preview_iframe("inner")
    assert "about:blank" in env.js[-1] and '"inner"' in env.js[-1]


# ---------------------------------------------------------------- design size

@pytest.mark.parametrize(
    "fd,size",
    [
        ({}, (1920, 1080)),
        ({"DesignWidth": "800", "DesignHeight": 600.7}, (800, 600)),
        ({"DesignWidth": "x", "StreamWidth": 1280, "AlertHeight": "720"}, (1280, 720)),
        ({"FeedWidth": 10, "PreviewHeight": 10}, (cs.PREVIEW_WIDTH_MIN, cs.PREVIEW_HEIGHT_MIN)),
        ({"OverlayWidth": 99999, "OverlayHeight": 99999}, (cs.PREVIEW_WIDTH_MAX, cs.PREVIEW_HEIGHT_MAX)),
        ({"CanvasWidth": None, "CanvasHeight": "bad"}, (1920, 1080)),
    ],
)
def test_estimate_design_size(fd, size):
    assert cs._estimate_design_size(fd) == size


def test_stored_or_estimated(env):
    assert cs._stored_or_estimated_size("", {"DesignWidth": 500}) == (500, 1080, "estimate")
    assert cs._stored_or_estimated_size("chat", None) == (1920, 1080, "estimate")
    env.settings["resolutions"]["chat"] = {"width": 640, "height": 480}
    assert cs._stored_or_estimated_size("chat", {}) == (640, 480, "stored")


def test_get_active_preview_size(env):
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs._set_active_preview_size("chat", 700, 400, source="obs", obs_source_name="Chat")
    assert cs._get_active_preview_size() == (700, 400)
    # different config -> stored/estimate from form data store
    cs.form_data_store["other"] = {"DesignWidth": 300, "DesignHeight": 200}
    cs._custom_sources_ctx["config_select"].value = "other"
    assert cs._get_active_preview_size() == (300, 200)
    assert cs._get_active_preview_size({"DesignWidth": 400}) == (400, 1080)
    # broken stored size falls back
    cs._active_preview_size.update({"config": "other", "width": "x"})
    assert cs._get_active_preview_size() == (300, 200)


def test_get_active_preview_size_without_select(env):
    cs._set_active_preview_size("alerts", 1000, 500, source="stored")
    assert cs._get_active_preview_size() == (1000, 500)
    cs._active_preview_size["config"] = ""
    assert cs._get_active_preview_size({}) == (1920, 1080)


def test_set_active_preview_size(env):
    cs._set_active_preview_size(None, "12", 34.0, source="estimate", obs_source_name=None)
    assert cs._active_preview_size == {"config": "", "width": 12, "height": 34, "source": "estimate",
                                       "obs_source_name": ""}


def test_sync_preview_size_inputs(env):
    w, h, hint = FakeEl(), FakeEl(), FakeEl()
    cs._custom_sources_ctx.update(preview_width_input=w, preview_height_input=h, preview_size_hint=hint)
    seen = []

    class Watch(FakeEl):
        def __setattr__(self, k, v):
            if k == "value" and "_suppress_size_input" in cs._custom_sources_ctx:
                seen.append(cs._custom_sources_ctx["_suppress_size_input"][0])
            object.__setattr__(self, k, v)

    cs._custom_sources_ctx["preview_width_input"] = Watch()
    cs._set_active_preview_size("c", 1, 1, source="obs", obs_source_name="Src")
    cs._sync_preview_size_inputs(640.0, 480)
    assert cs._custom_sources_ctx["preview_width_input"].value == 640 and h.value == 480
    assert seen[-1] is True and cs._custom_sources_ctx["_suppress_size_input"] == [False]
    assert "From OBS: Src" in hint.content and hint.visible is True
    cs._set_active_preview_size("c", 1, 1, source="estimate")
    cs._sync_preview_size_inputs(1, 1)
    assert hint.content == "" and hint.visible is False


def test_sync_preview_size_inputs_without_elements(env):
    cs._sync_preview_size_inputs(1, 2)
    assert cs._custom_sources_ctx["_suppress_size_input"] == [False]


def test_resize_preview_iframes(env, monkeypatch):
    cs._custom_sources_ctx.update(preview_outer_id="o", preview_inner_id="i", preview_zoom_pct=150,
                                  popout_preview_outer_id="po", popout_preview_inner_id="pi")
    cs._resize_preview_iframes(800, 600)
    assert len(env.js) == 1 and 'window.mycelianCsPreviewScale("o","i",800,600,150,false)' in env.js[0]
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    cs._resize_preview_iframes(800, 600)
    assert len(env.js) == 3 and '"po","pi"' in env.js[-1]
    cs._custom_sources_ctx["preview_outer_id"] = None
    cs._custom_sources_ctx["popout_preview_inner_id"] = None
    env.js.clear()
    cs._resize_preview_iframes(1, 1)
    assert env.js == []


def test_resize_preview_iframes_js_error(env, monkeypatch):
    cs._custom_sources_ctx.update(preview_outer_id="o", preview_inner_id="i")
    js_fails(monkeypatch)
    cs._resize_preview_iframes(1, 1)


def test_apply_preview_size(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_resize_preview_iframes", lambda w, h: calls.append((w, h)))
    cs._apply_preview_size("c", 300, 200, source="stored", resize=False)
    assert calls == [] and cs._active_preview_size["source"] == "stored"
    cs._apply_preview_size("c", 310, 210, source="obs", obs_source_name="S")
    assert calls == [(310, 210)] and cs._active_preview_size["obs_source_name"] == "S"


# ---------------------------------------------------------------- OBS lookup

def test_overlay_port(env, monkeypatch):
    env.engine.port = 6001
    assert cs._overlay_port() == 6001
    env.engine.port = 0
    assert cs._overlay_port() == 5000
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    assert cs._overlay_port() == 5000


def test_lookup_obs_size_blocking(monkeypatch):
    from modules import obs_service as obs_mod

    monkeypatch.setattr(obs_mod.obs_service, "lookup_browser_source_size",
                        lambda route, port: {"route": route, "port": port})
    assert cs._lookup_obs_size_blocking("chat", 5000) == {"route": "chat", "port": 5000}
    monkeypatch.setattr(obs_mod.obs_service, "lookup_browser_source_size",
                        lambda route, port: (_ for _ in ()).throw(ConnectionError("obs off")))
    assert cs._lookup_obs_size_blocking("chat", 5000) is None


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.entered = 0

    def __enter__(self):
        if self.fail:
            raise RuntimeError("client gone")
        self.entered += 1
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def obs(monkeypatch):
    import nicegui

    state = SimpleNamespace(payload=None, client=FakeClient(), calls=[])

    def lookup(route, port):
        state.calls.append((route, port))
        return state.payload

    monkeypatch.setattr(cs, "_lookup_obs_size_blocking", lookup)
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=state.client))
    return state


def test_schedule_obs_resolve_noops(env, obs, monkeypatch):
    cs._schedule_obs_preview_size_resolve("")
    monkeypatch.setattr(cs, "_effective_preview_route", lambda name: "")
    cs._schedule_obs_preview_size_resolve("chat")
    assert env.threads == [] and obs.calls == []


def test_schedule_obs_resolve_applies_size(env, obs):
    cs._custom_sources_ctx.update(config_select=FakeSelect("chat"), preview_outer_id="o", preview_inner_id="i")
    obs.payload = {"width": "1280", "height": 99999, "source_name": "Chat src"}
    env.engine.port = 5055
    cs._schedule_obs_preview_size_resolve("chat")
    assert env.threads == ["CsPreviewObsSize"] and obs.calls == [("chat", 5055)]
    assert env.app_scheduled[0].delay == 0.05
    env.run_scheduled()
    assert cs._active_preview_size == {"config": "chat", "width": 1280, "height": cs.PREVIEW_HEIGHT_MAX,
                                       "source": "obs", "obs_source_name": "Chat src"}
    assert obs.client.entered == 1
    assert any("mycelianCsPreviewScale" in j for j in env.js)


@pytest.mark.parametrize(
    "payload,select,bump",
    [
        ({"width": 1, "height": 1}, "other", False),  # user switched templates
        (None, "chat", False),  # OBS had nothing
        ({"width": "x", "height": 1}, "chat", False),  # bad numbers
        ({"width": 100, "height": 100}, "chat", True),  # a newer lookup started
    ],
)
def test_schedule_obs_resolve_ignored_results(env, obs, payload, select, bump):
    cs._custom_sources_ctx["config_select"] = FakeSelect(select)
    obs.payload = payload
    cs._schedule_obs_preview_size_resolve("chat")
    if bump:
        cs._preview_size_resolve_gen[0] += 1
    env.run_scheduled()
    assert cs._active_preview_size["source"] == "estimate"


def test_schedule_obs_resolve_no_select(env, obs):
    obs.payload = {"width": 100, "height": 100}
    cs._schedule_obs_preview_size_resolve("chat")
    env.run_scheduled()
    assert cs._active_preview_size["config"] == ""


def test_schedule_obs_resolve_client_failure_falls_back(env, obs, monkeypatch):
    import nicegui

    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=FakeClient(fail=True)))
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    obs.payload = {"width": 400, "height": 300}
    cs._schedule_obs_preview_size_resolve("chat")
    env.run_scheduled()
    assert cs._active_preview_size["width"] == 400


def test_schedule_obs_resolve_no_client_and_apply_error(env, obs, monkeypatch):
    import nicegui

    class NoClient:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(nicegui, "context", NoClient())
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    obs.payload = {"width": 400, "height": 300}
    monkeypatch.setattr(cs, "_apply_preview_size", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cs._schedule_obs_preview_size_resolve("chat")
    env.run_scheduled()  # error swallowed
    assert cs._active_preview_size["source"] == "estimate"


def test_schedule_obs_resolve_ui_loop_and_thread_errors(env, obs, monkeypatch):
    monkeypatch.setattr(cs, "run_on_ui_loop", lambda fn: (_ for _ in ()).throw(RuntimeError("loop closed")))
    cs._schedule_obs_preview_size_resolve("chat")
    assert env.scheduled == []

    import threading

    class BadThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", BadThread)
    cs._schedule_obs_preview_size_resolve("chat")  # logged, no exception


def test_resolve_preview_size_for_config(env, monkeypatch):
    scheduled = []
    monkeypatch.setattr(cs, "_schedule_obs_preview_size_resolve", scheduled.append)
    cs.form_data_store["chat"] = {"DesignWidth": 700}
    assert cs._resolve_preview_size_for_config("chat") == (700, 1080)
    assert scheduled == ["chat"] and cs._active_preview_size["source"] == "estimate"
    env.settings["resolutions"]["chat"] = {"width": 50, "height": 60}
    assert cs._resolve_preview_size_for_config("chat", {}, try_obs=False) == (50, 60)
    assert scheduled == ["chat"] and cs._active_preview_size["source"] == "stored"
    assert cs._resolve_preview_size_for_config("none", None, try_obs=False) == (1920, 1080)


# ---------------------------------------------------------------- scripts / pop-out

def test_ensure_preview_scale_script(env, monkeypatch):
    cs._ensure_preview_scale_script()
    assert env.js == [cs._PREVIEW_SCALE_IIFE, cs._POPOUT_DIALOG_IIFE, cs._SECTION_NAV_IIFE]
    js_fails(monkeypatch)
    cs._ensure_preview_scale_script()


def test_popout_preview_scale_args(env):
    cs._custom_sources_ctx.update(popout_preview_outer_id="po", popout_preview_inner_id="pi")
    assert cs._popout_preview_scale_args() == ("po", "pi", 1920, 1080, 100)
    cs._custom_sources_ctx.update(config_select=FakeSelect("chat"), preview_zoom_pct=75)
    cs.form_data_store["chat"] = {"DesignWidth": 640, "DesignHeight": 360}
    assert cs._popout_preview_scale_args() == ("po", "pi", 640, 360, 75)


@pytest.fixture
def popout(env, monkeypatch):
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    ph = FakeEl()
    cs._custom_sources_ctx.update(popout_preview_dialog_id="d", popout_preview_outer_id="o",
                                  popout_preview_inner_id="i", popout_preview_placeholder=ph)
    return ph


def test_load_popout_preview_closed_or_missing_ids(env, monkeypatch, popout):
    monkeypatch.setattr(cs, "_popout_preview_is_open", False)
    cs._load_popout_preview("chat", {})
    assert env.js == []
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    cs._custom_sources_ctx["popout_preview_dialog_id"] = None
    cs._load_popout_preview("chat", {})
    assert env.js == []


def test_load_popout_preview_no_config(env, popout):
    cs._load_popout_preview("", {})
    assert popout.text == "Select a configuration to preview." and popout.visible
    assert "about:blank" in env.js[-1]
    cs._custom_sources_ctx["popout_preview_placeholder"] = None
    cs._load_popout_preview("", {})


def test_load_popout_preview_server_not_ready(env, popout, monkeypatch):
    env.engine.is_running = False
    cs._load_popout_preview("chat", {})
    assert popout.text.startswith("Overlay server is not ready")
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    cs._custom_sources_ctx["popout_preview_placeholder"] = None
    cs._load_popout_preview("chat", {})
    assert env.js == []


def test_load_popout_preview_no_html(env, popout):
    cs._load_popout_preview("chat", {})
    assert popout.text == "No browser template at templates/chat.html — preview unavailable."
    cs._custom_sources_ctx["popout_preview_placeholder"] = None
    cs._load_popout_preview("chat", {})


def test_load_popout_preview_loads(env, popout, monkeypatch):
    add_html(env, "chat")
    env.engine.port = 0
    monkeypatch.setattr(cs.time, "time", lambda: 123.0)
    cs._load_popout_preview("chat", {"DesignWidth": 800, "DesignHeight": 450})
    assert popout.visible is False and popout.text == ""
    js = env.js[-1]
    assert js.startswith("window.mycelianCsPopoutLoadPreview && window.mycelianCsPopoutLoadPreview(")
    assert f"http://127.0.0.1:5000/chat?__preview_token={cs.CUSTOM_SOURCES_PREVIEW_TOKEN}&_cb=123.0" in js
    assert ", 800, 450, 100)" in js
    assert [h.delay for h in env.scheduled] == [0.15, 0.4]
    env.run_scheduled()
    assert env.js[-2:] == [js, js]


def test_load_popout_preview_without_placeholder_and_js_error(env, popout, monkeypatch):
    add_html(env, "chat")
    cs._custom_sources_ctx["popout_preview_placeholder"] = None
    js_fails(monkeypatch)
    cs._load_popout_preview("chat", {})
    assert env.scheduled == []


def test_open_close_popout(env, monkeypatch):
    cs._open_popout_preview_dialog("dlg")
    assert env.js[-1] == 'window.mycelianCsPopoutOpen && window.mycelianCsPopoutOpen("dlg")'
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    cs._close_popout_preview_dialog("dlg")
    assert cs._popout_preview_is_open is False
    assert env.js[-1] == 'window.mycelianCsPopoutClose && window.mycelianCsPopoutClose("dlg")'
    js_fails(monkeypatch)
    cs._open_popout_preview_dialog("dlg")
    cs._close_popout_preview_dialog_js("dlg")


# ---------------------------------------------------------------- zoom / scale / split

@pytest.mark.parametrize(
    "current,direction,expected",
    [(100, 1, 110), (100, -1, 90), (200, 1, 200), (25, -1, 25), ("x", 1, 110), (None, -1, 90), (30, 1, 33), (30, -1, 25)],
)
def test_next_preview_zoom(current, direction, expected):
    assert cs.next_preview_zoom(current, direction) == expected


@pytest.mark.parametrize("value,expected", [(None, 100), ("x", 100), (10, 25), (500, 200), ("150", 150)])
def test_current_preview_zoom_pct(env, value, expected):
    if value is not None:
        cs._custom_sources_ctx["preview_zoom_pct"] = value
    assert cs._current_preview_zoom_pct() == expected


def test_run_preview_scale_js(env, monkeypatch):
    cs._run_preview_scale_js(True)
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs._run_preview_scale_js(True)
    assert env.js == []
    cs._custom_sources_ctx.update(preview_outer_id="o", preview_inner_id="i")
    cs._run_preview_scale_js(True)
    assert env.js[-1].endswith('("o", "i", 1920, 1080, 100, true)')
    env.run_scheduled()
    assert len(env.js) == 2
    js_fails(monkeypatch)
    cs._run_preview_scale_js(False)


def test_run_preview_zoom_js(env, monkeypatch):
    cs._run_preview_zoom_js(False)
    assert env.js == []
    cs._custom_sources_ctx.update(preview_outer_id="o", preview_zoom_pct=50)
    cs._run_preview_zoom_js(True)
    assert env.js[-1] == 'window.mycelianCsPreviewSetZoom && window.mycelianCsPreviewSetZoom("o", 50, true)'
    js_fails(monkeypatch)
    cs._run_preview_zoom_js(False)


def test_apply_inline_preview_visibility(env, monkeypatch):
    scaled = []
    monkeypatch.setattr(cs, "_run_preview_scale_js", scaled.append)
    panel, divider, btn = FakeEl(), FakeEl(), FakeEl()
    cs._custom_sources_ctx.update(preview_panel=panel, split_divider=divider, show_preview_button=btn,
                                  preview_editor_panel_id="ed", split_pct=40)
    cs._apply_inline_preview_visibility(False)
    assert (panel.visible, divider.visible, btn.visible) == (False, False, True)
    assert "el.style.flex='100 100 0%'" in env.js[-1]
    assert env.saves[-1] == {"show_inline_preview": False}
    assert scaled == []
    cs._apply_inline_preview_visibility(True, persist=False)
    assert (panel.visible, divider.visible, btn.visible) == (True, True, False)
    assert "el.style.flex='40 40 0%'" in env.js[-1]
    assert len(env.saves) == 1 and scaled == [False]


def test_apply_inline_preview_visibility_minimal_ctx(env, monkeypatch):
    cs._apply_inline_preview_visibility(True, persist=False)
    assert env.js == []
    cs._custom_sources_ctx["preview_editor_panel_id"] = "ed"
    js_fails(monkeypatch)
    cs._apply_inline_preview_visibility(False, persist=False)


def test_bind_split_divider_js(env, monkeypatch):
    cs._bind_split_divider_js()
    assert env.js == []
    cs._custom_sources_ctx.update(preview_split_row_id="r", preview_editor_panel_id="l", preview_panel_id="p",
                                  split_pct=99)
    cs._bind_split_divider_js()
    assert env.js[-1] == 'window.mycelianCsSplitDrag && window.mycelianCsSplitDrag("r", "l", "p", 28, 78, 78)'
    assert env.scheduled[-1].delay == 0.2
    env.run_scheduled()
    assert env.js[-1] == env.js[-2]
    js_fails(monkeypatch)
    cs._bind_split_divider_js()


# ---------------------------------------------------------------- sync iframe

def test_sync_preview_iframe_branches(env, monkeypatch):
    ph = FakeEl()
    cs._sync_preview_iframe("i", ph, "", {})
    assert ph.text == "Select a configuration to preview." and "about:blank" in env.js[-1]
    cs._sync_preview_iframe("i", None, "", {})

    env.engine.is_running = False
    cs._sync_preview_iframe("i", ph, "chat", {})
    assert ph.text.startswith("Overlay server is not ready")
    cs._sync_preview_iframe("i", None, "chat", {})
    env.engine.is_running = True

    cs._sync_preview_iframe("i", ph, "chat", {})
    assert ph.text.endswith("templates/chat.html — preview unavailable.")
    cs._sync_preview_iframe("i", None, "chat", {})

    add_html(env, "chat")
    env.js.clear()
    cs._sync_preview_iframe("i", ph, "chat", {"DesignWidth": 500}, scale_outer_id="o", zoom_pct=125)
    assert ph.visible is False and ph.text == ""
    assert "iframe.width=500;" in env.js[0] and ",125,false);" in env.js[0]
    env.js.clear()
    cs._custom_sources_ctx["preview_zoom_pct"] = 50
    cs._sync_preview_iframe(None, None, "chat", {})
    assert env.js == []


def test_sync_preview_iframe_no_engine(env, monkeypatch):
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    ph = FakeEl()
    cs._sync_preview_iframe("i", ph, "chat", {})
    assert ph.text.startswith("Overlay server is not ready")


# ---------------------------------------------------------------- dirty tracking

class Weird:
    def __ne__(self, other):
        raise TypeError("no compare")

    def __eq__(self, other):
        if other == "":
            return False
        raise TypeError("no compare")


@pytest.mark.parametrize(
    "saved,current,differ",
    [
        (None, "", False),
        ("", None, False),
        ("a", "a", False),
        ("a", "b", True),
        ("true", True, False),
        ("False", True, True),
        (1, True, False),
        (0, True, True),
        (True, "true", False),
        (True, "no", True),
        (False, 0, False),
        (True, 1, False),
        (1, 1.0, False),
        (1, 2.5, True),
        ("1", 1, True),
        (Weird(), "x", True),
    ],
)
def test_preview_values_differ(saved, current, differ):
    assert cs._preview_values_differ(saved, current) is differ


def test_config_is_dirty_and_baseline(env):
    assert cs._config_is_dirty(None) is False
    assert cs._config_is_dirty("chat") is False  # no baseline
    fd = {"a": [1], "b": ""}
    cs._remember_disk_baseline("chat", fd)
    fd["a"].append(2)
    assert cs._disk_baselines["chat"] == {"a": [1], "b": ""}
    cs.form_data_store["chat"] = {"a": [1], "b": None}
    assert cs._config_is_dirty("chat") is False
    cs.form_data_store["chat"]["c"] = "new"
    assert cs._config_is_dirty("chat") is True


def test_source_settings_is_dirty(env, monkeypatch):
    assert cs.source_settings_is_dirty() is False
    monkeypatch.setattr(cs, "_active_config_name", "chat")
    cs._disk_baselines["chat"] = {"a": 1}
    cs.form_data_store["chat"] = {"a": 2}
    assert cs.source_settings_is_dirty() is True


def test_save_open_source_settings(env, monkeypatch):
    monkeypatch.setattr(cs, "_active_config_name", "chat")
    cs._disk_baselines["chat"] = {"a": 1}
    cs.form_data_store["chat"] = {"a": 2}
    assert cs.save_open_source_settings() is False  # tab not built: dirty, nothing saved
    saves = []

    def fake_save(parser, select, container):
        saves.append(select.value)
        cs._disk_baselines["chat"] = dict(cs.form_data_store["chat"])

    monkeypatch.setattr(cs, "save_config", fake_save)
    cs._custom_sources_ctx.update(config_parser=object(), config_select=FakeSelect("chat"), config_container=object())
    assert cs.save_open_source_settings() is True
    assert saves == ["chat"]
    cs._custom_sources_ctx["config_select"].value = None
    assert cs.save_open_source_settings() is True


def test_discard_open_source_settings(env, monkeypatch):
    resets = []
    monkeypatch.setattr(cs, "_perform_config_reset", lambda p, n, c: resets.append(n))
    cs.discard_open_source_settings()
    monkeypatch.setattr(cs, "_active_config_name", "chat")
    cs.discard_open_source_settings()
    assert resets == []
    cs._custom_sources_ctx.update(config_parser=object(), config_container=object())
    cs.discard_open_source_settings()
    assert resets == ["chat"]


# ---------------------------------------------------------------- live chip

def test_preview_live_state_all_branches():
    s = cs.preview_live_state
    assert s(None, server_running=True, has_template=True, dirty=False) == ("off", "No template")
    assert s("c", server_running=False, has_template=True, dirty=False) == ("off", "Waiting for the overlay server")
    assert s("c", server_running=True, has_template=False, dirty=False) == ("off", "No preview for this template")
    assert s("c", server_running=True, has_template=True, dirty=True) == ("live", "Live · shows unsaved changes")
    assert s("c", server_running=True, has_template=True, dirty=False) == ("live", "Live")


def test_chip_html():
    assert cs.preview_live_chip_html("live", "<Live>") == \
        '<span class="ssx-chip is-live"><span class="dot"></span>&lt;Live&gt;</span>'
    assert cs.preview_live_chip_html("off", "x") == f'<span class="ssx-chip">{cs._icon_html("hourglass_empty")}x</span>'
    assert cs.preview_size_chip_html("estimate") == ""
    assert "From OBS<" in cs.preview_size_chip_html("obs")
    assert 'title="From OBS: A&amp;B"' in cs.preview_size_chip_html("obs", "A&B")
    assert "Saved size" in cs.preview_size_chip_html("stored")


def test_refresh_live_chip(env, monkeypatch):
    cs._refresh_live_chip("chat")  # no chip: nothing
    chip = FakeEl()
    cs._custom_sources_ctx["preview_live_chip"] = chip
    cs._refresh_live_chip("chat")
    assert "No preview for this template" in chip.content
    add_html(env, "chat")
    cs._refresh_live_chip("chat")
    assert chip.content.endswith("Live</span>")
    chip.content = "sentinel"
    cs._refresh_live_chip("chat")  # same html -> not re-sent
    assert chip.content == "sentinel"
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    cs._refresh_live_chip(None)
    assert "No template" in chip.content


def test_refresh_preview_dirty_label(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_refresh_live_chip", lambda n: calls.append(("chip", n)))
    monkeypatch.setattr(cs, "_refresh_dirty_ui", lambda: calls.append(("dirty",)))
    cs._refresh_preview_dirty_label("chat")
    cs._form_state["config"] = "chat"
    cs._refresh_preview_dirty_label("chat")
    cs._refresh_preview_dirty_label("")
    assert calls == [("chip", "chat"), ("chip", "chat"), ("dirty",), ("chip", "")]


# ---------------------------------------------------------------- route cache

def test_effective_preview_route_and_cache(env):
    assert cs._effective_preview_route("") == ""
    assert cs._effective_preview_route("chat") == "chat"  # no parser
    cs._invalidate_preview_route_cache()
    parser = FakeParser({"tpl": {"template_name": "  Real  "}, "blank": {"template_name": "  "},
                         "num": {"template_name": 5}})
    cs._custom_sources_ctx["config_parser"] = parser
    assert cs._effective_preview_route("tpl") == "Real"
    assert cs._effective_preview_route("blank") == "blank"
    assert cs._effective_preview_route("num") == "num"
    parser.configs["tpl"]["template_name"] = "Changed"
    assert cs._effective_preview_route("tpl") == "Real"  # cached
    cs._invalidate_preview_route_cache("tpl")
    assert cs._effective_preview_route("tpl") == "Changed"
    assert "blank" in cs._preview_route_cache
    cs._invalidate_preview_route_cache()
    assert cs._preview_route_cache == {}
    parser.load_error = RuntimeError("bad json")
    assert cs._effective_preview_route("tpl") == "tpl"


# ---------------------------------------------------------------- hot push

def test_push_hot_preview_overrides(env, monkeypatch):
    cs._push_hot_preview_overrides()  # no select
    sel = FakeSelect("")
    cs._custom_sources_ctx["config_select"] = sel
    cs._push_hot_preview_overrides()
    sel.value = "chat"
    cs.form_data_store["chat"] = {"a": 1}
    env.engine.is_running = False
    cs._push_hot_preview_overrides()
    env.engine.is_running = True
    cs._push_hot_preview_overrides()  # no html
    assert env.engine.pushed == []
    add_html(env, "chat")
    cs._push_hot_preview_overrides()
    assert env.engine.pushed == [(cs.CUSTOM_SOURCES_PREVIEW_TOKEN, "chat", {"a": 1})]
    assert env.engine.refreshed == [cs.CUSTOM_SOURCES_PREVIEW_TOKEN]
    env.engine.push_error = RuntimeError("socket closed")
    cs._push_hot_preview_overrides()
    assert env.titles() == ["The preview didn't update"]
    assert env.notes[0][1]["details"] == "chat: RuntimeError: socket closed"
    assert env.notes[0][1]["dedupe_key"] == "source_settings:preview_push"


def test_push_hot_preview_no_engine(env, monkeypatch):
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs._push_hot_preview_overrides()


# ---------------------------------------------------------------- mock events

def test_emit_preview_mock_no_server(env, monkeypatch):
    env.engine.is_running = False
    cs._emit_preview_mock("next_alert", None, "alerts")
    assert env.titles() == ["The overlay server isn't running yet"]
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    cs._emit_preview_mock("next_alert", None, "alerts")
    assert len(env.notes) == 2


@pytest.mark.parametrize(
    "result,title,ntype",
    [
        ((True, None, None), None, None),
        ((False, "No preview iframe for token", None), "Open the preview first", "warning"),
        ((False, "No mock payload for x", None), "No sample data for this event", "warning"),
        ((False, "socket error", None), "Couldn't send the sample event", "error"),
        ((False, None, None), "Couldn't send the sample event", "error"),
    ],
)
def test_emit_preview_mock_results(env, result, title, ntype):
    env.engine.mock_result = result
    cs._emit_preview_mock("next_alert", "sub", "alerts")
    assert env.engine.mocks == [("next_alert", "sub")]
    if title is None:
        assert env.notes == []
    else:
        assert env.titles() == [title] and env.notes[0][1]["type"] == ntype
    if result == (False, None, None):
        assert env.notes[0][1]["details"] is None


def test_notify_preview_push_failed(env):
    cs._notify_preview_push_failed("chat", ValueError("x"))
    msg, kw = env.notes[0]
    assert msg == "The preview may show old values." and kw["details"] == "chat: ValueError: x"
    assert kw["dedupe_cooldown_sec"] == 60.0


def test_rebuild_mock_toolbars(env):
    holder = {}
    (env.templates / "template_configs" / "tpl.json").write_text(
        '{"preview_mocks": [{"event": "go", "label": "Go!", "alert_type": "sub", "tooltip": "Tip"}, {"event": "two"}]}',
        encoding="utf-8")

    def build():
        holder["row"] = ui.element("div")
        holder["prow"] = ui.row()
        cs._custom_sources_ctx.update(mock_toolbar_row=holder["row"], popout_mock_toolbar_row=holder["prow"])
        cs._rebuild_preview_mock_toolbar("tpl")
        cs._rebuild_popout_mock_toolbar("tpl")

    async def check(user):
        row, prow = holder["row"], holder["prow"]
        texts = [e.text for e in row.descendants() if getattr(e, "text", "") and not isinstance(e, ui.tooltip)]
        assert texts == ["Sample events", "Go!", "two"]
        tips = [e.text for e in row.descendants() if isinstance(e, ui.tooltip)]
        assert tips == ["Tip", "Play a sample «two» event in the preview"]
        btns = [e for e in row.descendants() if e.tag == "button"]
        fire(user, btns[0])
        fire(user, btns[1])
        assert env.engine.mocks == [("go", "sub"), ("two", None)]
        pbtns = [e for e in prow.descendants() if isinstance(e, ui.button)]
        assert [b.text for b in pbtns] == ["Go!", "two"]
        fire(user, pbtns[1])
        assert env.engine.mocks[-1] == ("two", None)
        # empty states + hidden toolbar setting
        env.settings["show_mock_toolbar"] = False
        with user.client:
            cs._rebuild_preview_mock_toolbar("")
            cs._rebuild_popout_mock_toolbar("unknown")
        assert row.visible is False and prow.visible is False
        assert "No sample events for this template." in [e.text for e in row.descendants() if getattr(e, "text", "")]
        assert "No mock events for this template." in [e.text for e in prow.descendants() if getattr(e, "text", "")]

    run_ui(build, check)


def test_rebuild_toolbars_without_rows(env):
    cs._rebuild_preview_mock_toolbar("alerts")
    cs._rebuild_popout_mock_toolbar("alerts")


# ---------------------------------------------------------------- flush / schedule

@pytest.fixture
def flush_env(env, monkeypatch):
    calls = SimpleNamespace(sync=[], popout=[], scale=[], toolbar=[], ptoolbar=[], resolve=[])
    monkeypatch.setattr(cs, "_sync_preview_iframe", lambda iid, ph, name, fd, **kw: calls.sync.append((iid, name, dict(fd))))
    monkeypatch.setattr(cs, "_load_popout_preview", lambda name, fd: calls.popout.append(name))
    monkeypatch.setattr(cs, "_run_preview_scale_js", calls.scale.append)
    monkeypatch.setattr(cs, "_rebuild_preview_mock_toolbar", calls.toolbar.append)
    monkeypatch.setattr(cs, "_rebuild_popout_mock_toolbar", calls.ptoolbar.append)
    monkeypatch.setattr(cs, "_resolve_preview_size_for_config",
                        lambda n, fd, try_obs=True: calls.resolve.append((n, try_obs)) or (1, 1))
    cs._custom_sources_ctx.update(preview_inner_id="i", popout_preview_inner_id="pi")
    return calls


def test_flush_without_select(env, flush_env):
    cs._flush_template_preview()
    assert flush_env.sync == [] and flush_env.scale == []


def test_flush_with_live_template(env, flush_env, monkeypatch):
    add_html(env, "chat")
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs.form_data_store["chat"] = {"a": 1}
    cs._flush_template_preview()
    assert env.engine.pushed == [(cs.CUSTOM_SOURCES_PREVIEW_TOKEN, "chat", {"a": 1})]
    assert flush_env.sync == [("i", "chat", {"a": 1})]
    assert flush_env.resolve == [("chat", True)]
    assert flush_env.popout == [] and flush_env.ptoolbar == []
    assert flush_env.scale == [True] and flush_env.toolbar == ["chat"]
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    env.engine.push_error = RuntimeError("x")
    cs._flush_template_preview()
    assert flush_env.popout == ["chat"] and flush_env.ptoolbar == ["chat"]
    assert env.titles() == ["The preview didn't update"]


def test_flush_without_html_and_popout(env, flush_env, monkeypatch):
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs.form_data_store["chat"] = {"a": 1}
    cs._flush_template_preview()
    assert flush_env.sync == [("i", "chat", {}), ("pi", "chat", {})]
    assert env.engine.pushed == []


def test_flush_without_config(env, flush_env, monkeypatch):
    cs._custom_sources_ctx["config_select"] = FakeSelect(None)
    cs._flush_template_preview()
    assert flush_env.sync == [("i", "", {})] and flush_env.toolbar == [""]
    monkeypatch.setattr(cs, "_popout_preview_is_open", True)
    cs._flush_template_preview()
    assert flush_env.sync[-1] == ("pi", "", {}) and flush_env.ptoolbar == [""]
    cs._custom_sources_ctx["popout_preview_inner_id"] = None
    flush_env.sync.clear()
    cs._flush_template_preview()
    assert flush_env.sync == [("i", "", {})]


def test_schedule_template_preview_refresh(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_flush_template_preview", lambda: calls.append("flush"))
    monkeypatch.setattr(cs, "_push_hot_preview_overrides", lambda: calls.append("push"))
    cs._schedule_template_preview_refresh()
    assert env.scheduled[0].delay == 0.32
    env.run_scheduled()
    assert calls == ["flush"]
    cs._custom_sources_ctx["config_select"] = FakeSelect("chat")
    cs._schedule_template_preview_refresh()
    env.run_scheduled()
    assert calls == ["flush", "push"]
    cs._schedule_template_preview_refresh()
    cs._schedule_template_preview_refresh()  # newer one wins
    env.run_scheduled()
    assert calls == ["flush", "push", "push"]

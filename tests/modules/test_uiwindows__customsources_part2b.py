# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/customsources.py, part 2b: the Source Settings tab UI.

Builds the real tab with a fake template parser (no files, no server) and drives its
header, outline, preview bar, size/zoom controls and preview-settings dialog.
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

from _customsources_support import FakeParser, fire, make_env  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import customsources as cs  # noqa: E402
from ui_sim import run_ui  # noqa: E402

CONFIGS = {
    "chat": {"template_name": "chat", "elements": [
        {"type": "text", "id": "Title", "label": "Title", "value": "Hello"},
        {"type": "separator", "label": "Look"},
        {"type": "number", "id": "Size", "label": "Size (px)", "value": 12},
    ]},
    "alerts": {"template_name": "alerts", "elements": [
        {"type": "checkbox", "id": "Enabled", "label": "Enabled", "value": True},
    ]},
    "secret": {"elements": []},
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = make_env(monkeypatch, tmp_path)
    e.parser = FakeParser({k: dict(v) for k, v in CONFIGS.items()}, hidden={"secret"})
    monkeypatch.setattr(cs, "TemplateConfigParser", lambda *a, **k: e.parser)
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: None)
    monkeypatch.setattr(cs, "_lookup_obs_size_blocking", lambda route, port: None)
    return e


def everything(user):
    return list(user.client.layout.descendants())


def by_label(user, label):
    found = [e for e in everything(user) if e.props.get("aria-label") == label]
    assert found, label
    return found[0]


def by_text(user, text, kind=None):
    found = [e for e in everything(user) if getattr(e, "text", None) == text and (kind is None or isinstance(e, kind))]
    assert found, text
    return found[0]


def build_tab():
    cs.create_custom_sources_tab()


# ---------------------------------------------------------------- build

def test_tab_builds_and_selects_first_template(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        sel = ctx["config_select"]
        assert sel.options == ["alerts", "chat"] and sel.value == "alerts"
        assert cs._active_config_name == "alerts"
        assert ctx["meta_label"].text == "1 setting · 1 section"
        assert "Saved" in ctx["dirty_pill"].content
        assert ctx["discard_button"].enabled is False
        assert ctx["split_pct"] == 56.0 and ctx["preview_zoom_pct"] == 100
        assert ctx["show_preview_button"].visible is False and ctx["preview_panel"].visible is True
        assert cs._form_state["config"] == "alerts" and "Enabled" in cs._form_state["rows"]
        assert cs._PREVIEW_SCALE_IIFE in env.js
        assert any("mycelianCsSplitDrag" in j for j in env.js)
        assert "alerts" in cs.form_data_store and cs._disk_baselines["alerts"] == {"Enabled": True}
        await user.should_see("Source Settings")
        await user.should_see("Enabled")

    run_ui(build_tab, check)


def test_tab_starts_with_preview_hidden(env):
    env.settings["show_inline_preview"] = False
    env.settings["split_editor_pct"] = 40

    async def check(user):
        ctx = cs._custom_sources_ctx
        assert ctx["show_preview_button"].visible is True and ctx["preview_panel"].visible is False
        assert ctx["split_pct"] == 40.0
        assert env.saves == []  # initial visibility is not persisted
        fire(user, ctx["show_preview_button"])
        assert ctx["preview_panel"].visible is True
        assert env.saves[-1] == {"show_inline_preview": True}

    run_ui(build_tab, check)


def test_tab_without_templates(env):
    env.parser.configs = {}

    async def check(user):
        await user.should_see("No templates yet")
        assert cs._custom_sources_ctx["config_select"].value is None
        assert cs._active_config_name is None
        assert cs._custom_sources_ctx["meta_label"].text == ""

    run_ui(build_tab, check)


# ---------------------------------------------------------------- header actions

def test_header_buttons_call_actions(env, monkeypatch):
    calls = []
    for name in ("duplicate_config", "rename_config", "delete_config", "reset_config", "save_config"):
        monkeypatch.setattr(cs, name, lambda p, s, c, n=name: calls.append((n, s.value)))

    async def check(user):
        for label in ("Duplicate this template", "Rename this template", "Delete this template"):
            fire(user, by_label(user, label))
        fire(user, [e for e in everything(user) if isinstance(e, ui.button) and e.text == "Save"][0])
        discard = cs._custom_sources_ctx["discard_button"]
        discard.enable()
        fire(user, discard)
        assert calls == [("duplicate_config", "alerts"), ("rename_config", "alerts"), ("delete_config", "alerts"),
                         ("save_config", "alerts"), ("reset_config", "alerts")]
        env.parser.configs["zeta"] = {"elements": []}
        fire(user, by_label(user, "Reload the template list"))
        assert cs._custom_sources_ctx["config_select"].options == ["alerts", "chat", "zeta"]

    run_ui(build_tab, check)


def test_switching_template_and_editing_marks_dirty(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        ctx["config_select"].value = "chat"
        assert cs._active_config_name == "chat"
        assert ctx["meta_label"].text == "2 settings · 2 sections"
        size = next(e for e in everything(user) if isinstance(e, ui.number) and e.value == 12)
        size.value = 14
        assert cs.form_data_store["chat"]["Size"] == 14
        assert "1 unsaved change" in ctx["dirty_pill"].content
        assert ctx["discard_button"].enabled is True
        assert "is-changed" in cs._form_state["rows"]["Size"].row.classes
        # revert through the delegated form click
        fire(user, ctx["config_container"], "click", {"revert": "Size"})
        assert cs.form_data_store["chat"]["Size"] == 12 and size.value == 12
        # fold a section from its header
        fire(user, ctx["config_container"], "click", {"sec": "g"})
        assert cs._section_by_key("g").is_open is False

    run_ui(build_tab, check)


def test_outline_search_changed_only_and_jump(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        ctx["config_select"].value = "chat"
        search = next(e for e in everything(user) if e.tag == "input" and e.props.get("aria-label") == "Search settings")
        fire(user, search, "input", "size")
        env.run_scheduled()
        assert cs._form_state["search"] == "size"
        assert cs._form_state["rows"]["Title"].visible is False
        fire(user, ctx["nav_list"], "click", "s1")
        assert any("mycelianSsxScrollTo" in j for j in env.js)
        fire(user, ctx["jump_select"], "change", "g")
        switch = next(e for e in everything(user) if isinstance(e, ui.switch) and e.text == "")
        switch.value = True
        assert cs._form_state["changed_only"] is True
        await user.should_see("No settings match")

    run_ui(build_tab, check)


# ---------------------------------------------------------------- preview bar

def test_preview_bar_buttons(env, monkeypatch):
    flushes, loads, opens = [], [], []

    async def check(user):
        ctx = cs._custom_sources_ctx
        monkeypatch.setattr(cs, "_flush_template_preview", lambda: flushes.append(1))
        monkeypatch.setattr(cs, "_load_popout_preview", lambda name, fd: loads.append(name))
        monkeypatch.setattr(cs, "_open_popout_preview_dialog", opens.append)
        fire(user, by_label(user, "Reload preview"))
        fire(user, by_label(user, "Pop out the preview"))
        assert cs._popout_preview_is_open is True and loads == ["alerts"]
        ctx["config_select"].value = None
        fire(user, by_label(user, "Pop out the preview"))
        assert opens == [ctx["popout_preview_dialog_id"]]
        fire(user, by_label(user, "Hide the preview"))
        assert ctx["preview_panel"].visible is False and ctx["show_preview_button"].visible is True
        # pop-out dialog head buttons
        popout_btns = [e for e in everything(user) if isinstance(e, ui.button) and e.props.get("icon") in ("refresh", "close")]
        for b in popout_btns:
            fire(user, b)
        assert len(flushes) == 2
        assert cs._popout_preview_is_open is False

    run_ui(build_tab, check)


def test_zoom_buttons(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        fire(user, by_label(user, "Zoom in"))
        assert ctx["preview_zoom_pct"] == 110
        fire(user, by_label(user, "Zoom out"))
        fire(user, by_label(user, "Zoom out"))
        assert ctx["preview_zoom_pct"] == 90
        assert env.js[-1].endswith(", 90, false)")
        fire(user, by_text(user, "Fit"))
        assert ctx["preview_zoom_pct"] == 100 and env.js[-1].endswith(", 100, true)")

    run_ui(build_tab, check)


def test_size_inputs_store_resolution(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        w, h = ctx["preview_width_input"], ctx["preview_height_input"]
        w.value = 99999
        assert env.settings["resolutions"]["alerts"] == {"width": cs.PREVIEW_WIDTH_MAX, "height": 1080}
        assert cs._active_preview_size["source"] == "stored"
        h.value = None  # unusable -> ignored
        assert env.settings["resolutions"]["alerts"]["height"] == 1080
        ctx["_suppress_size_input"][0] = True
        w.value = 640
        assert env.settings["resolutions"]["alerts"]["width"] == cs.PREVIEW_WIDTH_MAX
        ctx["_suppress_size_input"][0] = False
        ctx["config_select"].value = None
        w.value = 700
        assert env.settings["resolutions"]["alerts"]["width"] == cs.PREVIEW_WIDTH_MAX
        # reset clears the stored size
        ctx["config_select"].value = "alerts"
        fire(user, by_label(user, "Clear the saved size and detect it again (OBS or template defaults)"))
        assert "alerts" not in env.settings["resolutions"]
        assert cs._active_preview_size["source"] == "estimate"
        ctx["config_select"].value = None
        fire(user, by_label(user, "Clear the saved size and detect it again (OBS or template defaults)"))

    run_ui(build_tab, check)


def test_preview_settings_dialog(env):
    async def check(user):
        ctx = cs._custom_sources_ctx
        env.settings.update(enable_preview_sounds=False, show_mock_toolbar=True)
        fire(user, by_label(user, "Preview settings"))
        await user.should_see("Preview settings")
        sound = by_text(user, "Enable preview sounds", ui.switch)
        bar = by_text(user, "Show sample events bar", ui.switch)
        assert sound.value is False and bar.value is True
        bar.value = False
        save_btn = [e for e in everything(user) if isinstance(e, ui.button) and e.text == "Save"][-1]
        fire(user, save_btn)
        assert env.saves[-1] == {"enable_preview_sounds": False, "show_mock_toolbar": False}
        assert env.engine.settings_emits == [cs.CUSTOM_SOURCES_PREVIEW_TOKEN]
        assert ctx["mock_toolbar_row"].visible is False and ctx["popout_mock_toolbar_row"].visible is False
        assert env.titles()[-1] == "Preview settings saved"
        env.save_ok = False
        fire(user, save_btn)
        assert env.titles()[-1] == "Couldn't save preview settings"
        fire(user, [e for e in everything(user) if isinstance(e, ui.button) and e.text == "Cancel"][-1])

    run_ui(build_tab, check)


def test_preview_settings_save_without_engine_or_rows(env, monkeypatch):
    async def check(user):
        monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
        cs._custom_sources_ctx.pop("mock_toolbar_row")
        cs._custom_sources_ctx.pop("popout_mock_toolbar_row")
        save_btn = [e for e in everything(user) if isinstance(e, ui.button) and e.text == "Save"][-1]
        fire(user, save_btn)
        assert env.titles()[-1] == "Preview settings saved"

    run_ui(build_tab, check)

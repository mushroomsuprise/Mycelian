# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.mainuiwindow``: version, theme application, scheduled start-up work, start_ui,
window prefs, splash, lazy tab preload and the alert pause/mute toggles.

The main window shell (build_root_ui / create_ui_elements) is in ``test_mainuiwindow_part2.py``.
Nothing real starts: no window, server, thread, overlay engine or OS call.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import json
import os
import types
from types import SimpleNamespace

import pytest
from nicegui import app, ui

from modules import mainuiwindow as mw
from modules import tray_controller
from ui_sim import run_ui


class FakeThread:
    created: list = []

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.target, self.daemon, self.name = target, daemon, name
        self.started = False
        FakeThread.created.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def threads(monkeypatch):
    FakeThread.created = []
    monkeypatch.setattr(mw.threading, "Thread", FakeThread)
    return FakeThread.created


@pytest.fixture
def sched(monkeypatch):
    calls = []
    monkeypatch.setattr(mw, "app_schedule", lambda *a, **k: calls.append(("app", a, k)))
    monkeypatch.setattr(mw, "layout_schedule", lambda *a, **k: calls.append(("layout", a, k)))
    return calls


@pytest.fixture
def notes(monkeypatch):
    from modules import notification_engine as ne

    out = []
    monkeypatch.setattr(ne, "notify", lambda msg, **kw: out.append((msg, kw)))
    return out


# --------------------------------------------------------------------------- get_version

def test_get_version_reads_version_file(monkeypatch, tmp_path):
    (tmp_path / "version.txt").write_text("VSVersionInfo(\n  ffi=FixedFileInfo(\n    filevers=(2,5,7,0),\n")
    monkeypatch.chdir(tmp_path)
    assert mw.get_version() == "2.5.7"


def test_get_version_fallbacks(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert mw.get_version() == "1.3.3"
    (tmp_path / "version.txt").write_text("prodvers=garbage\nnothing")
    assert mw.get_version() == "1.3.3"


# --------------------------------------------------------------------------- Quasar brand colors

THEME = SimpleNamespace(primary="red", info="blue", success="green", warning="yellow", error="black", name="custom")
EMPTY_THEME = SimpleNamespace(primary="", info=None, success="", warning="", error="", name="x")


def test_quasar_brand_css():
    css = mw._get_quasar_brand_css(THEME)
    assert "--q-primary: red;" in css and "--q-secondary: blue;" in css and "--q-negative: black;" in css
    css = mw._get_quasar_brand_css(EMPTY_THEME)
    assert "--q-primary: rgb(115, 0, 255);" in css and "--q-info: rgb(59, 130, 246);" in css


def test_sync_quasar_brand_colors_js(monkeypatch):
    js = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, **kw: js.append(code))
    mw._sync_quasar_brand_colors_js(THEME)
    mw._sync_quasar_brand_colors_js(EMPTY_THEME)
    assert "setProperty('--q-warning', 'yellow')" in js[0]
    assert "setProperty('--q-negative', 'rgb(239, 68, 68)')" in js[1]


def test_has_ui_context(monkeypatch):
    from nicegui.slot import Slot

    monkeypatch.setattr(Slot, "get_stack", staticmethod(lambda: []))
    assert mw._has_ui_context() is False
    monkeypatch.setattr(Slot, "get_stack", staticmethod(lambda: [object()]))
    assert mw._has_ui_context() is True

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(Slot, "get_stack", staticmethod(boom))
    assert mw._has_ui_context() is False


# --------------------------------------------------------------------------- apply_theme

class FakeThemeManager:
    def __init__(self, theme=THEME, theme_type="dark"):
        self.theme, self.theme_type = theme, theme_type
        self.calls = []

    def load_active_theme(self, name):
        self.calls.append(("load", name))

    def set_theme(self, name):
        self.calls.append(("set", name))

    def get_theme(self):
        return self.theme

    def get_theme_type(self, name):
        return self.theme_type


@pytest.fixture
def theme_env(monkeypatch):
    tm = FakeThemeManager()
    monkeypatch.setattr(mw, "get_theme_manager", lambda: tm)
    monkeypatch.setattr(mw, "generate_css_variables", lambda theme: ":root{--x:1;}`${y}")
    heads, js, fonts = [], [], []
    monkeypatch.setattr(ui, "add_head_html", lambda html, shared=False: heads.append((html, shared)))
    monkeypatch.setattr(ui, "run_javascript", lambda code, **kw: js.append(code))
    monkeypatch.setattr(mw, "apply_app_font", lambda: fonts.append(1))
    monkeypatch.setattr(mw, "get_full_theme_css", lambda: "BASE")
    monkeypatch.setattr(mw, "get_app_font_css_block", lambda: "FONT")
    monkeypatch.setattr(mw, "_has_ui_context", lambda: False)
    monkeypatch.setattr(mw, "_base_css_injected", False)
    monkeypatch.delitem(sys.modules, "modules.help_system.help_window", raising=False)
    monkeypatch.delitem(sys.modules, "modules.web_engine", raising=False)
    return SimpleNamespace(tm=tm, heads=heads, js=js, fonts=fonts)


def test_apply_theme_first_call_injects_head_css(theme_env):
    assert mw.apply_theme("dark") is None
    tm = theme_env.tm
    assert tm.calls == [("load", "dark"), ("set", "dark")]
    ids = [h.split("'")[1] if h.startswith("<style") else "script" for h, _ in theme_env.heads]
    assert ids == ["mycelian-theme-vars", "mycelian-base-css", "mycelian-quasar-brand", "mycelian-font-override",
                   "script"]
    assert all(shared for _h, shared in theme_env.heads)
    assert mw._base_css_injected is True and theme_env.js == []


def test_apply_theme_non_string_name_loads_dark(theme_env):
    mw.apply_theme(True)
    assert theme_env.tm.calls[0] == ("load", "dark") and theme_env.tm.calls[1] == ("set", True)


def test_apply_theme_hot_swap_updates_clients(theme_env, monkeypatch):
    mw._base_css_injected = True
    pushed, broadcast = [], []
    help_window = types.ModuleType("modules.help_system.help_window")
    help_window.push_theme = lambda: pushed.append(1)
    monkeypatch.setitem(sys.modules, "modules.help_system.help_window", help_window)
    web_engine = types.ModuleType("modules.web_engine")
    web_engine.web_engine_instance = SimpleNamespace(broadcast_theme_update=lambda **kw: broadcast.append(kw))
    monkeypatch.setitem(sys.modules, "modules.web_engine", web_engine)
    mw.apply_theme("ocean")
    assert theme_env.fonts == [1] and theme_env.heads == []
    assert "style.textContent = `:root{--x:1;}\\`\\${y}`" in theme_env.js[0]
    assert "--q-primary" in theme_env.js[1]
    assert pushed == [1]
    assert broadcast == [{"theme_css": ":root{--x:1;}`${y}", "theme_name": "ocean", "theme_type": "dark"}]


def test_apply_theme_hot_swap_without_engine_instance(theme_env, monkeypatch):
    mw._base_css_injected = True
    web_engine = types.ModuleType("modules.web_engine")
    web_engine.web_engine_instance = None
    monkeypatch.setitem(sys.modules, "modules.web_engine", web_engine)
    mw.apply_theme("ocean")
    assert len(theme_env.js) == 2


@pytest.mark.parametrize("theme_type,method", [("dark", "enable"), ("light", "disable")])
def test_apply_theme_sets_dark_mode_with_ui_context(theme_env, monkeypatch, theme_type, method):
    theme_env.tm.theme_type = theme_type
    monkeypatch.setattr(mw, "_has_ui_context", lambda: True)
    calls = []
    monkeypatch.setattr(ui, "dark_mode", lambda: SimpleNamespace(enable=lambda: calls.append("enable"),
                                                                 disable=lambda: calls.append("disable")))
    mw.apply_theme("x")
    assert calls == [method]


def test_apply_theme_missing_theme_warns(theme_env, notes):
    theme_env.tm.theme = None
    assert mw.apply_theme("gone") is False
    ((msg, kw),) = notes
    assert kw["title"] == "Couldn't load the theme “gone”"
    assert kw["dedupe_key"] == "theme:load_failed:gone" and kw["actions"][0]["settings_subtab"] == "Theme"
    assert theme_env.heads == []


def test_apply_theme_missing_theme_non_string_is_quiet(theme_env, notes):
    theme_env.tm.theme = None
    assert mw.apply_theme(True) is False
    assert notes == []


def test_apply_theme_missing_theme_notify_failure_swallowed(theme_env, monkeypatch):
    from modules import notification_engine as ne

    theme_env.tm.theme = None

    def boom(*a, **k):
        raise RuntimeError("no ui")

    monkeypatch.setattr(ne, "notify", boom)
    assert mw.apply_theme("gone") is False


# --------------------------------------------------------------------------- one-shot schedulers

def test_inject_animation_observer_once(monkeypatch):
    heads = []
    monkeypatch.setattr(ui, "add_head_html", lambda html, shared=False: heads.append((html, shared)))
    monkeypatch.setattr(mw, "_animation_observer_injected", False)
    mw._inject_animation_observer()
    mw._inject_animation_observer()
    assert heads == [(mw.ANIMATION_OBSERVER_SCRIPT, True)]


@pytest.fixture
def legacy(monkeypatch, sched, notes):
    from modules import notification_engine as ne
    from modules.spore_studio import data_feed_registry as dfr

    state = SimpleNamespace(rows=[], history=[])
    monkeypatch.setattr(dfr, "templates_needing_widget_resave", lambda: state.rows)
    monkeypatch.setattr(ne, "get_history", lambda: state.history)
    mw._schedule_spore_legacy_widget_notice()
    ((kind, (delay, cb), kw),) = sched
    assert kind == "layout" and delay == 2.0 and kw == {"once": True}
    state.run = cb
    state.notes = notes
    return state


def test_legacy_notice_nothing_to_report(legacy):
    legacy.rows = [{"template": "  ", "mode": "counter"}]
    legacy.run()
    assert legacy.notes == []


def test_legacy_notice_single_template(legacy):
    legacy.rows = [{"template": "Card", "mode": "counter"}]
    legacy.run()
    ((msg, kw),) = legacy.notes
    assert kw["title"] == "“Card” needs a re-save in Spore Studio"
    assert "It still shows a counter as a Text block." in msg
    assert kw["actions"] == [{"kind": "navigate", "main_tab": "Spore Studio", "spore_template": "Card"}]
    assert kw["details"] == "Card: Text counter" and kw["dedupe_key"] == "spore:legacy_text_widgets"


def test_legacy_notice_many_templates(legacy):
    legacy.rows = [{"template": n, "mode": "data"} for n in ("g", "B", "a", "f", "e", "d", "C")]
    legacy.run()
    ((msg, kw),) = legacy.notes
    assert kw["title"] == "7 Spore templates need a re-save"
    assert msg.startswith("a, B, C, d, e and 2 more still show")
    assert kw["actions"] == [{"kind": "navigate", "main_tab": "Spore Studio"}]
    assert "a: Text data display" in kw["details"]


def test_legacy_notice_few_templates_no_more_suffix(legacy):
    legacy.rows = [{"template": "x"}, {"template": "y"}]
    legacy.run()
    assert legacy.notes[0][0].startswith("x, y still show")


def test_legacy_notice_skipped_when_already_in_history(legacy):
    legacy.rows = [{"template": "Card", "mode": "counter"}]
    legacy.history = [{"dedupe_key": "spore:legacy_text_widgets", "details": "Card: Text counter"}]
    legacy.run()
    assert legacy.notes == []
    legacy.history = [{"dedupe_key": "spore:legacy_text_widgets", "details": "Old: Text counter"}]
    legacy.run()
    assert len(legacy.notes) == 1


def test_legacy_notice_errors_logged(legacy, monkeypatch, caplog):
    from modules.spore_studio import data_feed_registry as dfr

    def boom():
        raise RuntimeError("registry")

    monkeypatch.setattr(dfr, "templates_needing_widget_resave", boom)
    legacy.run()
    assert "legacy widget notice failed" in caplog.text


def test_streamdeck_plugin_check_scheduled_once(monkeypatch, sched, caplog):
    from modules import streamdeck_plugin_utils as sdu

    monkeypatch.setattr(mw, "_streamdeck_plugin_check_scheduled", False)
    mw._schedule_streamdeck_plugin_version_check()
    mw._schedule_streamdeck_plugin_version_check()
    ((kind, (delay, cb), kw),) = sched
    assert kind == "app" and delay == 2.5 and kw == {"once": True}
    seen = []
    monkeypatch.setattr(sdu, "maybe_notify_streamdeck_plugin_outdated", lambda: seen.append(1))
    cb()
    assert seen == [1]

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(sdu, "maybe_notify_streamdeck_plugin_outdated", boom)
    cb()
    assert "Stream Deck plugin version check failed" in caplog.text


def test_update_manager_init_scheduled_once(monkeypatch, sched, caplog):
    from modules import updater

    monkeypatch.setattr(mw, "_update_manager_ui_scheduled", False)
    mw._schedule_update_manager_init()
    mw._schedule_update_manager_init()
    ((kind, (delay, cb), kw),) = sched
    assert kind == "app" and delay == 2.0
    seen = []
    monkeypatch.setattr(updater, "update_manager", SimpleNamespace(on_ui_ready=lambda: seen.append(1)))
    cb()
    assert seen == [1]
    monkeypatch.setattr(updater, "update_manager", SimpleNamespace())
    cb()
    assert "failed to initialize UpdateManager scheduling" in caplog.text


def test_start_connector_processing_once(monkeypatch, threads, caplog):
    from modules import connector_manager

    monkeypatch.setattr(mw, "_connector_processing_started", False)
    mw.start_connector_processing()
    mw.start_connector_processing()
    (t,) = threads
    assert t.started and t.daemon
    started = []
    monkeypatch.setattr(connector_manager, "get_manager",
                        lambda: SimpleNamespace(start_connector_thread=lambda: started.append(1)))
    t.target()
    assert started == [1]

    def boom():
        raise RuntimeError("db")

    monkeypatch.setattr(connector_manager, "get_manager", boom)
    t.target()
    assert "Error starting connector processing thread" in caplog.text


def test_initialize_background_ui_services(monkeypatch):
    seen = []
    monkeypatch.setattr(mw, "start_connector_processing", lambda: seen.append("connectors"))
    monkeypatch.setattr(mw, "run_on_ui_loop", lambda fn: seen.append(fn))
    mw.initialize_background_ui_services()
    assert seen == ["connectors", mw._schedule_update_manager_init]


def test_reschedule_periodic_update_timer(monkeypatch, caplog):
    from modules import updater

    seen = []
    monkeypatch.setattr(updater, "update_manager", SimpleNamespace(reschedule_periodic_timer=lambda: seen.append(1)))
    mw.reschedule_periodic_update_timer()
    assert seen == [1]
    monkeypatch.setattr(updater, "update_manager", SimpleNamespace())
    mw.reschedule_periodic_update_timer()
    assert "reschedule via UpdateManager failed" in caplog.text


# --------------------------------------------------------------------------- WebView2 (Windows only, faked)

@pytest.fixture
def win_env(monkeypatch, tmp_path):
    import ctypes
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("WEBVIEW2_USER_DATA_FOLDER", raising=False)
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    state = SimpleNamespace(admin=0)

    def is_admin():
        if isinstance(state.admin, Exception):
            raise state.admin
        return state.admin

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=is_admin)),
                        raising=False)
    state.tmp = tmp_path
    return state


def test_configure_webview2_skips_other_platforms(monkeypatch):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mw.os, "makedirs", lambda *a, **k: pytest.fail("no dirs on mac"))
    mw._configure_webview2()


def test_configure_webview2_user(win_env):
    mw._configure_webview2()
    data = win_env.tmp / "Mycelian" / "WebView2Data"
    assert data.is_dir() and os.environ["WEBVIEW2_USER_DATA_FOLDER"] == str(data)
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in os.environ


def test_configure_webview2_admin(win_env):
    win_env.admin = 1
    mw._configure_webview2()
    assert "--disable-web-security" in os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]


def test_configure_webview2_admin_check_fails(win_env):
    win_env.admin = OSError("no shell32")
    mw._configure_webview2()
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in os.environ


def test_configure_webview2_makedirs_error(win_env, monkeypatch, caplog):
    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(mw.os, "makedirs", boom)
    mw._configure_webview2()
    assert "Failed to configure WebView2" in caplog.text


# --------------------------------------------------------------------------- profiling marks / overlay / cleanup

def test_startup_profile_marks_disabled(monkeypatch):
    from modules import startup_profiler

    monkeypatch.setattr(startup_profiler, "is_startup_profiling_enabled", lambda: False)
    monkeypatch.setattr(app, "on_startup", lambda fn: pytest.fail("not profiling"))
    mw._register_startup_profile_marks()


def test_startup_profile_marks_enabled(monkeypatch):
    from modules import startup_profiler

    marks, startup, events = [], [], {}
    monkeypatch.setattr(startup_profiler, "is_startup_profiling_enabled", lambda: True)
    monkeypatch.setattr(startup_profiler, "mark_startup_milestone", marks.append)
    monkeypatch.setattr(app, "on_startup", startup.append)
    em = types.ModuleType("nicegui.native.event_manager")
    em.event_manager = SimpleNamespace(on=lambda name, fn: events.__setitem__(name, fn))
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", em)
    mw._register_startup_profile_marks()
    startup[0]()
    events["shown"](None)
    events["loaded"](None)
    assert marks == ["UI server started", "native window shown", "native page loaded"]


def test_startup_profile_marks_without_native_events(monkeypatch):
    from modules import startup_profiler

    startup = []
    monkeypatch.setattr(startup_profiler, "is_startup_profiling_enabled", lambda: True)
    monkeypatch.setattr(app, "on_startup", startup.append)
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", None)
    mw._register_startup_profile_marks()
    assert len(startup) == 1


def test_start_overlay_engine_background(monkeypatch, threads, caplog):
    from modules import alert_processor

    mw._start_overlay_engine_background()
    (t,) = threads
    assert t.started and t.daemon and t.name == "OverlayInit"
    seen = []
    monkeypatch.setattr(alert_processor, "initialize", lambda: seen.append(1))
    t.target()
    assert seen == [1]

    def boom():
        raise RuntimeError("port in use")

    monkeypatch.setattr(alert_processor, "initialize", boom)
    t.target()
    assert "Error initializing overlay engine" in caplog.text


def test_cleanup_resources(monkeypatch, caplog):
    from modules import shutdown

    seen = []
    monkeypatch.setattr(shutdown, "shutdown_application", lambda **kw: seen.append(kw))
    mw.cleanup_resources()
    assert seen == [{"reason": "nicegui_on_shutdown", "force": False}]

    def boom(**kw):
        raise RuntimeError("x")

    monkeypatch.setattr(shutdown, "shutdown_application", boom)
    mw.cleanup_resources()
    assert "Error during resource cleanup" in caplog.text


# --------------------------------------------------------------------------- remember_ui_pref

class FakeState:
    def __init__(self, settings=None, update_ok=True):
        self.settings = settings if settings is not None else SimpleNamespace(last_main_tab="Alerts")
        self.update_ok = update_ok
        self.updates, self.saves = [], 0

    def get_app_settings(self):
        if isinstance(self.settings, Exception):
            raise self.settings
        return self.settings

    def update_app_setting(self, field, value):
        self.updates.append((field, value))
        return self.update_ok

    def save_changes(self):
        self.saves += 1


@pytest.fixture
def state(monkeypatch):
    from modules import dataobjects

    fake = FakeState()
    monkeypatch.setattr(dataobjects, "state_manager", fake)
    return fake


def test_remember_ui_pref(state):
    mw.remember_ui_pref("last_main_tab", "Alerts")  # unchanged
    assert state.updates == []
    mw.remember_ui_pref("last_main_tab", "Chatbot")
    assert state.updates == [("last_main_tab", "Chatbot")] and state.saves == 1
    state.update_ok = False
    mw.remember_ui_pref("last_main_tab", "Settings")
    assert state.saves == 1
    state.settings = RuntimeError("db")
    mw.remember_ui_pref("last_main_tab", "x")  # swallowed


# --------------------------------------------------------------------------- start_ui

@pytest.fixture
def start_env(monkeypatch, state):
    from nicegui import core as ng_core

    from modules import native_window_bridge, shutdown
    from modules.help_system import help_window

    calls = []
    monkeypatch.setattr(mw, "_start_overlay_engine_background", lambda: calls.append("overlay"))
    monkeypatch.setattr(native_window_bridge, "install", lambda: calls.append("bridge"))
    monkeypatch.setattr(help_window, "register_help_page", lambda: calls.append("help"))
    monkeypatch.setattr(mw, "_configure_webview2", lambda: calls.append("webview2"))
    monkeypatch.setattr(app.native, "window_args", {})
    monkeypatch.delenv("MYCELIAN_NATIVE_WINDOW_ARGS", raising=False)
    monkeypatch.delenv("MYCELIAN_TRAY_MODE", raising=False)
    monkeypatch.setattr(mw, "_file_browser_qdialog_css_injected", False)
    heads = []
    monkeypatch.setattr(ui, "add_head_html", lambda html, shared=False: heads.append(html))
    monkeypatch.setattr(app, "on_shutdown", lambda fn: calls.append(("on_shutdown", fn)))
    monkeypatch.setattr(shutdown, "register_native_window_close_handler", lambda: calls.append("close_handler"))
    monkeypatch.setattr(tray_controller, "register_native_event_handlers", lambda: calls.append("tray_events"))
    monkeypatch.setattr(mw, "_register_startup_profile_marks", lambda: calls.append("marks"))
    monkeypatch.setattr(ng_core, "script_mode", False)
    monkeypatch.setattr(ng_core, "script_client", None)
    runs = []
    env = SimpleNamespace(calls=calls, heads=heads, runs=runs, state=state, core=ng_core, run_exc=None)

    def fake_run(root, **kw):
        runs.append((root, kw))
        if env.run_exc:
            raise env.run_exc

    monkeypatch.setattr(ui, "run", fake_run)
    return env


def _settings(**kw):
    base = dict(start_maximized=True, start_minimized=False, minimize_to_tray=False, window_width=0,
                window_height=0, window_x=-1, window_y=-1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_start_ui_default_maximized(start_env):
    start_env.state.settings = _settings()
    mw.start_ui()
    assert start_env.calls[:4] == ["overlay", "bridge", "help", "webview2"]
    assert ("on_shutdown", mw.cleanup_resources) in start_env.calls
    assert {"close_handler", "tray_events", "marks"} <= set(start_env.calls)
    root, kw = start_env.runs[0]
    assert root is mw.build_root_ui
    assert kw == {"native": True, "dark": True, "reload": False, "reconnect_timeout": 120}
    assert json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"]) == {"maximized": True, "min_size": [1400, 850]}
    assert os.environ["MYCELIAN_TRAY_MODE"] == "0"
    assert app.native.window_args == {"maximized": True}
    assert mw._file_browser_qdialog_css_injected is True and "mycelian-file-browser-qdialog" in start_env.heads[0]
    mw.start_ui()
    assert len(start_env.heads) == 1  # CSS injected once


def test_start_ui_restores_window_geometry(start_env):
    start_env.state.settings = _settings(start_maximized=False, window_width=1600, window_height=900,
                                         window_x=10, window_y=20, minimize_to_tray=True)
    mw.start_ui()
    args = json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"])
    assert args == {"maximized": False, "min_size": [1400, 850], "width": 1600, "height": 900, "x": 10, "y": 20}
    assert app.native.window_args["x"] == 10 and os.environ["MYCELIAN_TRAY_MODE"] == "1"


def test_start_ui_size_without_position_and_too_small(start_env):
    start_env.state.settings = _settings(start_maximized=False, window_width=1600, window_height=900)
    mw.start_ui()
    assert "x" not in json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"])
    start_env.state.settings = _settings(start_maximized=False, window_width=800, window_height=600)
    mw.start_ui()
    assert "width" not in json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"])


def test_start_ui_minimized_to_tray(start_env):
    start_env.state.settings = _settings(start_minimized=True)
    mw.start_ui()
    args = json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"])
    assert args["hidden"] is True and args["url"] == tray_controller.BLANK_URL
    assert app.native.window_args["hidden"] is True


def test_start_ui_settings_error_uses_defaults(start_env, caplog):
    start_env.state.settings = RuntimeError("db")
    mw.start_ui()
    assert json.loads(os.environ["MYCELIAN_NATIVE_WINDOW_ARGS"]) == {"maximized": True, "min_size": [1400, 850]}
    assert "Could not read window settings" in caplog.text


def test_start_ui_clears_accidental_script_mode(start_env, monkeypatch):
    start_env.state.settings = _settings()
    deleted = []
    monkeypatch.setattr(start_env.core, "script_mode", True)
    monkeypatch.setattr(start_env.core, "script_client", SimpleNamespace(delete=lambda: deleted.append(1)))
    monkeypatch.setattr(start_env.core, "app", SimpleNamespace(is_started=False))
    mw.start_ui()
    assert start_env.core.script_mode is False and start_env.core.script_client is None and deleted == [1]


def test_start_ui_script_client_delete_failure(start_env, monkeypatch):
    start_env.state.settings = _settings()

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(start_env.core, "script_mode", True)
    monkeypatch.setattr(start_env.core, "script_client", SimpleNamespace(delete=boom))
    monkeypatch.setattr(start_env.core, "app", SimpleNamespace(is_started=False))
    mw.start_ui()
    assert start_env.core.script_mode is False and len(start_env.runs) == 1


def test_start_ui_script_mode_check_failure(start_env, monkeypatch):
    start_env.state.settings = _settings()
    monkeypatch.setattr(start_env.core, "script_mode", True)
    monkeypatch.setattr(start_env.core, "app", None)
    mw.start_ui()
    assert len(start_env.runs) == 1


def _lpe(msg):
    from wsproto.utilities import LocalProtocolError

    return LocalProtocolError(msg)


def test_start_ui_benign_websocket_close_in_run(start_env, caplog):
    start_env.state.settings = _settings()
    start_env.run_exc = _lpe("Event CloseConnection cannot be sent in state ConnectionState.CLOSED.")
    mw.start_ui()
    assert "Benign websocket close" in caplog.text


def test_start_ui_other_errors_raise(start_env):
    start_env.state.settings = _settings()
    start_env.run_exc = RuntimeError("port busy")
    with pytest.raises(RuntimeError, match="port busy"):
        mw.start_ui()


def test_start_ui_benign_error_outside_run_returns(start_env, monkeypatch):
    def boom():
        raise _lpe("cannot be sent in state X")

    monkeypatch.setattr(mw, "_start_overlay_engine_background", boom)
    assert mw.start_ui() is None


def test_is_benign_shutdown_websocket_error(monkeypatch):
    assert mw._is_benign_shutdown_websocket_error(_lpe("ConnectionState.CLOSED")) is True
    assert mw._is_benign_shutdown_websocket_error(_lpe("Cannot Be Sent In State foo")) is True
    assert mw._is_benign_shutdown_websocket_error(_lpe("other")) is False
    assert mw._is_benign_shutdown_websocket_error(RuntimeError("ConnectionState.CLOSED")) is False
    monkeypatch.setitem(sys.modules, "wsproto.utilities", None)
    assert mw._is_benign_shutdown_websocket_error(RuntimeError("x")) is False


# --------------------------------------------------------------------------- splash

@pytest.fixture
def splash(monkeypatch):
    for name, value in {"_splash_dialog": None, "_splash_progress": None, "_splash_status": None,
                        "_splash_close_requested": False}.items():
        monkeypatch.setattr(mw, name, value)
    monkeypatch.setattr(mw, "run_on_ui_loop", lambda fn: fn())


def test_splash_lifecycle(splash):
    result = {}

    def build():
        result["refs"] = mw.create_splash_screen()

    async def check(user):
        dlg, progress, status = result["refs"]
        assert dlg.value is True and "persistent" in dlg.props
        await user.should_see("Streaming Toolkit")
        mw.update_splash_progress(0.5, "Loading Twitch...")
        assert progress.value == 0.5 and status.text == "Loading Twitch..."
        mw.close_splash_screen()
        assert dlg.value is False and mw._splash_dialog is None and mw._splash_close_requested is True
        mw._close_splash_dialog()  # already closed: no-op

    run_ui(build, check)


def test_splash_skipped_after_close_requested(splash):
    mw._splash_close_requested = True
    assert mw.create_splash_screen() == (None, None, None)


def test_apply_splash_progress_without_splash(splash):
    mw._apply_splash_progress(0.3, "x")  # nothing built: no error


# --------------------------------------------------------------------------- initialize_ui_shell

def test_initialize_ui_shell_uses_saved_theme(monkeypatch, state):
    applied, progress = [], []
    state.settings = SimpleNamespace(current_theme="forest")
    monkeypatch.setattr(mw, "apply_theme", applied.append)
    monkeypatch.setattr(mw, "update_splash_progress", lambda v, t: progress.append((v, t)))
    mw.initialize_ui_shell()
    assert applied == ["forest"] and progress == [(0.1, "Core components loaded")]


def test_initialize_ui_shell_without_theme_attr(monkeypatch, state):
    applied = []
    state.settings = SimpleNamespace()
    monkeypatch.setattr(mw, "apply_theme", applied.append)
    monkeypatch.setattr(mw, "update_splash_progress", lambda v, t: None)
    mw.initialize_ui_shell()
    assert applied == ["dark"]


def test_initialize_ui_shell_settings_error_falls_back(monkeypatch, state):
    applied = []
    state.settings = RuntimeError("db")
    monkeypatch.setattr(mw, "apply_theme", applied.append)
    monkeypatch.setattr(mw, "update_splash_progress", lambda v, t: None)
    mw.initialize_ui_shell()
    assert applied == ["dark", True]


def test_initialize_ui_shell_reraises_fatal_errors(monkeypatch, state):
    state.settings = RuntimeError("db")

    def boom(name):
        raise ValueError("theme engine broken")

    monkeypatch.setattr(mw, "apply_theme", boom)
    with pytest.raises(ValueError):
        mw.initialize_ui_shell()


# --------------------------------------------------------------------------- LazyTabPanel

def test_lazy_tab_panel_loads_once():
    built = []
    result = {}

    def build():
        panel = mw.LazyTabPanel("Alerts", lambda: built.append(ui.label("Alerts body")))
        panel.ensure_loaded()  # no container yet: nothing
        assert built == [] and not panel.loaded
        panel.container = ui.column()
        with panel.container:
            panel.spinner = ui.spinner()
        result["panel"] = panel

    async def check(user):
        panel = result["panel"]
        with user:
            panel.ensure_loaded()
            panel.ensure_loaded()
        assert panel.loaded and len(built) == 1 and panel.spinner.is_deleted
        await user.should_see("Alerts body")

    run_ui(build, check)


# --------------------------------------------------------------------------- toggles

class FakeEngineInstance:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def toggle_alerts(self):
        self.calls.append("toggle_alerts")

    def toggle_mute(self):
        self.calls.append("toggle_mute")

    def pause_status_update(self):
        self.calls.append("pause_status")
        if self.fail:
            raise RuntimeError("socket closed")

    def mute_status_update(self):
        self.calls.append("mute_status")
        if self.fail:
            raise RuntimeError("socket closed")


@pytest.fixture
def engine(monkeypatch, notes, sched):
    from modules.uiwindows import activity_feed

    we = types.ModuleType("fake_web_engine")
    we.web_engine_instance = None
    we.ALERTS_PAUSED = False
    we.ALERTS_MUTED = False

    def set_paused(v):
        we.ALERTS_PAUSED = v

    we.set_alerts_paused = set_paused
    monkeypatch.setattr(mw, "_web_engine", lambda: we)
    synced = []
    monkeypatch.setattr(activity_feed, "sync_pause_button_state", lambda: synced.append("pause"))
    monkeypatch.setattr(activity_feed, "sync_mute_button_state", lambda: synced.append("mute"))
    updates = []
    monkeypatch.setattr(ui, "update", lambda *a: updates.append(1))
    return SimpleNamespace(we=we, notes=notes, sched=sched, synced=synced, updates=updates)


def test_web_engine_accessor_returns_module():
    import modules.web_engine as real

    assert mw._web_engine() is real


def test_toggle_alerts_with_instance(engine):
    inst = FakeEngineInstance()
    engine.we.web_engine_instance = inst
    mw.toggle_alerts()
    assert inst.calls == ["toggle_alerts"] and engine.synced == ["pause"] and engine.updates == [1]
    assert engine.notes[-1][1]["title"] == "Alerts resumed"


def test_toggle_alerts_without_instance_schedules_broadcast(engine):
    mw.toggle_alerts()
    assert engine.we.ALERTS_PAUSED is True
    assert engine.notes[-1][1]["title"] == "Alerts paused"
    ((kind, (delay, retry), kw),) = engine.sched
    assert kind == "app" and delay == 1.0 and kw == {"once": True}
    assert retry() is False
    inst = FakeEngineInstance()
    engine.we.web_engine_instance = inst
    assert retry() is True and inst.calls == ["pause_status"]
    inst.fail = True
    assert retry() is False


def test_toggle_mute_with_and_without_instance(engine):
    inst = FakeEngineInstance()
    engine.we.web_engine_instance = inst
    mw.toggle_mute()
    assert inst.calls == ["toggle_mute"] and engine.notes[-1][1]["title"] == "Alert sound unmuted"
    engine.we.web_engine_instance = None
    mw.toggle_mute()
    assert engine.we.ALERTS_MUTED is True and engine.notes[-1][1]["title"] == "Alert sound muted"
    (_, (_, retry), _), = engine.sched
    assert retry() is False
    engine.we.web_engine_instance = FakeEngineInstance()
    assert retry() is True
    engine.we.web_engine_instance = FakeEngineInstance(fail=True)
    assert retry() is False
    assert engine.synced == ["mute", "mute"]


def test_toggle_immediate_broadcast_when_instance_appears(engine, monkeypatch):
    """No instance at toggle time but one appears before the broadcast: no retry is scheduled."""
    inst = FakeEngineInstance()

    def set_paused(v):
        engine.we.ALERTS_PAUSED = v
        engine.we.web_engine_instance = inst

    engine.we.set_alerts_paused = set_paused
    mw.toggle_alerts()
    assert inst.calls == ["pause_status"] and engine.sched == []


def test_toggle_sync_failures_are_tolerated(engine, monkeypatch):
    from modules.uiwindows import activity_feed

    def boom():
        raise RuntimeError("feed not built")

    monkeypatch.setattr(activity_feed, "sync_pause_button_state", boom)
    monkeypatch.setattr(activity_feed, "sync_mute_button_state", boom)
    engine.we.web_engine_instance = FakeEngineInstance()
    mw.toggle_alerts()
    mw.toggle_mute()
    assert len(engine.notes) == 2


@pytest.mark.parametrize("fn,title", [(mw.toggle_alerts, "Couldn't pause or resume alerts"),
                                      (mw.toggle_mute, "Couldn't mute or unmute alerts")])
def test_toggle_errors_notify(engine, monkeypatch, fn, title):
    def boom():
        raise RuntimeError("no engine")

    monkeypatch.setattr(mw, "_web_engine", boom)
    fn()
    ((msg, kw),) = engine.notes
    assert kw["title"] == title and kw["details"] == "RuntimeError: no engine"

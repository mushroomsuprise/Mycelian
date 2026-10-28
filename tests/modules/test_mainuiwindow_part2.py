# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.mainuiwindow``: the main window shell (build_root_ui, create_ui_elements, main-tab
switching with the unsaved-changes dialog, window geometry) and the lazy tab preload worker.

Every heavy tab and service is a stub; nothing real is built, started or saved.
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

import threading
import types
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import mainuiwindow as mw
from modules import notification_engine as ne
from modules import ui_health_monitor
from modules.help_system import contextual_help as ch
from modules.uiwindows import settings as st
from ui_sim import run_ui

LAZY = ["Alerts", "Source Settings", "Source Controls", "Connectors", "Chatbot", "Spore Studio", "Settings"]


class FakeSettingsUI:
    def __init__(self, log):
        self.log = log
        self.unsaved = False
        self.tabs = {"Twitch": SimpleNamespace(dirty=False, discard=lambda: log.append("discard Twitch")),
                     "OBS": SimpleNamespace(dirty=True, discard=lambda: log.append("discard OBS")),
                     "None": None}
        self._tabs_by_name = self.tabs
        self.save_raises = False
        self.save_clears = True
        self.next_results = [True, False]

    def has_unsaved_changes(self):
        return self.unsaved

    def _tab_is_dirty(self, name):
        return self.unsaved and name == "OBS"

    def save_tab(self, name):
        self.log.append(f"save {name}")
        if self.save_raises:
            raise ValueError("disk full")
        if self.save_clears:
            self.unsaved = False

    def pause_background_work(self):
        self.log.append("pause settings")

    def resume_background_work(self):
        self.log.append("resume settings")

    def load_next_unloaded_settings_tab(self):
        self.log.append("preload settings subtab")
        return self.next_results.pop(0) if self.next_results else False


@pytest.fixture
def shell(monkeypatch):
    log, timers, notes = [], [], []
    env = SimpleNamespace(log=log, timers=timers, notes=notes, remembered=[], alerts_dirty=False,
                          source_dirty=False, alert_save_ok=True, source_save_ok=True, preloads=[],
                          settings=SimpleNamespace(last_main_tab="Activity Feed", start_maximized=False,
                                                   window_width=0, window_height=0, window_x=0, window_y=0),
                          updates=[], saves=0)

    def fake_module(name, **attrs):
        mod = types.ModuleType(f"modules.uiwindows.{name}")
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
        return mod

    def builder(label):
        def build():
            ui.label(f"{label} body")
            log.append(f"build {label}")
        return build

    fake_module("activity_feed", create_activity_feed_tab=builder("Activity Feed"),
                register_client_lifecycle_hooks=lambda: log.append("lifecycle hooks"))
    fake_module("alertsettings", create_alert_settings_tab=builder("Alerts"),
                alerts_have_unsaved_changes=lambda: env.alerts_dirty,
                save_active_alert=lambda done: (log.append("save alert"), done(env.alert_save_ok)),
                discard_active_alert=lambda: log.append("discard alert"))
    fake_module("sourcecontrols", create_source_controls_tab=builder("Source Controls"))
    fake_module("connectors", create_connectors_tab=builder("Connectors"))
    fake_module("chatbot", create_chatbot_tab=builder("Chatbot"))
    fake_module("spore_studio", create_spore_studio_tab=builder("Spore Studio"))
    fake_module("customsources", create_custom_sources_tab=builder("Source Settings"),
                source_settings_is_dirty=lambda: env.source_dirty,
                save_open_source_settings=lambda: log.append("save source") or env.source_save_ok,
                discard_open_source_settings=lambda: log.append("discard source"))
    env.settings_ui = FakeSettingsUI(log)
    monkeypatch.setattr(st, "settings_ui", env.settings_ui)
    monkeypatch.setattr(st, "create_settings_tab", builder("Settings"))

    monkeypatch.setattr(ch, "help_button", lambda **kw: log.append("help button"))
    monkeypatch.setattr(ch, "register_main_tabs", lambda refs: log.append(("main tabs", sorted(refs))))
    monkeypatch.setattr(ch, "set_main_ui_references", lambda tabs, panels: log.append("main refs"))
    monkeypatch.setattr(ne, "create_notification_tray_button", lambda: log.append("bell"))
    monkeypatch.setattr(ne, "create_service_status_footer", lambda: log.append("footer"))
    monkeypatch.setattr(ne, "start_service_watcher_timer", lambda: log.append("watcher"))
    monkeypatch.setattr(ne, "notify", lambda msg, **kw: notes.append((msg, kw)))
    monkeypatch.setattr(ui_health_monitor, "arm_ui_health_monitor", lambda client: log.append("health monitor"))

    class Timer:
        def __init__(self, interval, cb, kw):
            self.interval, self.cb, self.kw = interval, cb, kw

    monkeypatch.setattr(mw, "layout_schedule", lambda i, cb, **kw: timers.append(Timer(i, cb, kw)))
    monkeypatch.setattr(mw, "apply_app_font", lambda: log.append("font"))
    monkeypatch.setattr(mw, "_start_lazy_tab_preload", lambda lazy, tabs: env.preloads.append((lazy, tabs)))
    monkeypatch.setattr(mw, "_schedule_streamdeck_plugin_version_check", lambda: log.append("streamdeck check"))
    monkeypatch.setattr(mw, "_schedule_spore_legacy_widget_notice", lambda: log.append("legacy notice"))
    monkeypatch.setattr(mw, "remember_ui_pref", lambda k, v: env.remembered.append((k, v)))
    monkeypatch.setattr(mw, "_ui_elements_created", False)
    monkeypatch.setattr(mw, "_ui_client_id", None)
    monkeypatch.setattr(mw, "_animation_observer_injected", True)
    for name, value in {"_splash_dialog": None, "_splash_progress": None, "_splash_status": None,
                        "_splash_close_requested": False}.items():
        monkeypatch.setattr(mw, name, value)

    tm = SimpleNamespace(get_theme=lambda: SimpleNamespace(name="dark"), get_theme_type=lambda n: env.theme_type)
    env.theme_type = "dark"
    monkeypatch.setattr(mw, "get_theme_manager", lambda: tm)

    from modules import dataobjects

    def update(field, value):
        env.updates.append((field, value))
        return True

    def save():
        env.saves += 1

    def get_settings():
        if isinstance(env.settings, Exception):
            raise env.settings
        return env.settings

    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=get_settings,
                                                                     update_app_setting=update, save_changes=save))
    return env


def tabs_el(user):
    return next(e for e in user.find(kind=ui.tabs).elements if "mycelian-main-tabs" in e.classes)


def set_main(user, tabs, label):
    with user:
        tabs.value = label


def button(user, text):
    return next(b for b in user.find(kind=ui.button).elements if b.text == text)


def click(user, el):
    UserInteraction(user, {el}, None).click()


# --------------------------------------------------------------------------- building

def test_build_root_ui_builds_shell(shell):
    async def check(user):
        log = shell.log
        for item in ("help button", "bell", "main refs", "build Activity Feed", "watcher", "footer",
                     "streamdeck check", "legacy notice", "lifecycle hooks", "health monitor"):
            assert item in log, item
        assert ("main tabs", sorted(["Activity Feed"] + LAZY)) in log
        assert mw._ui_elements_created is True and mw._ui_client_id == user.client.id
        await user.should_see("Activity Feed body")
        await user.should_see("Streaming Toolkit")  # splash
        tabs = tabs_el(user)
        assert tabs.value.props["name"] == "Activity Feed"
        lazy, preload_tabs = shell.preloads[0]
        assert set(lazy) == set(LAZY) and preload_tabs is tabs
        assert all(not p.loaded for p in lazy.values())
        intervals = sorted((t.interval, t.kw.get("once", False)) for t in shell.timers)
        assert intervals == [(0.5, True), (5.0, False)]
        next(t for t in shell.timers if t.interval == 0.5).cb()
        assert "font" in log

    run_ui(mw.build_root_ui, check)


def test_build_root_ui_light_theme_and_dark_mode_error(shell, monkeypatch):
    shell.theme_type = "light"

    async def check(user):
        assert "build Activity Feed" in shell.log

    run_ui(mw.build_root_ui, check)

    def boom():
        raise RuntimeError("no themes")

    monkeypatch.setattr(mw, "get_theme_manager", boom)
    monkeypatch.setattr(mw, "_ui_elements_created", False)
    run_ui(mw.build_root_ui, check)


def test_build_root_ui_same_client_skips_rebuild(shell, monkeypatch):
    calls = []
    def fake_create():
        calls.append(1)
        mw._ui_elements_created = True

    monkeypatch.setattr(mw, "create_ui_elements", fake_create)

    def build():
        mw.build_root_ui()
        mw.build_root_ui()

    run_ui(build)
    assert len(calls) == 1


def test_create_ui_elements_only_once(shell, monkeypatch):
    monkeypatch.setattr(mw, "_ui_elements_created", True)

    def build():
        mw.create_ui_elements()

    run_ui(build)
    assert "bell" not in shell.log


@pytest.mark.parametrize("saved,expected", [("Alerts", "Alerts"), ("Bogus", "Activity Feed"), (None, "Activity Feed")])
def test_restores_last_main_tab(shell, saved, expected):
    shell.settings.last_main_tab = saved

    async def check(user):
        assert tabs_el(user).value.props["name"] == expected
        if expected == "Alerts":
            assert "build Alerts" in shell.log

    run_ui(mw.create_ui_elements, check)


def test_saved_tab_read_error_uses_activity_feed(shell):
    shell.settings = RuntimeError("db")

    async def check(user):
        assert tabs_el(user).value.props["name"] == "Activity Feed"

    run_ui(mw.create_ui_elements, check)


# --------------------------------------------------------------------------- switching main tabs

def test_switch_lazy_loads_and_remembers(shell):
    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, "Connectors")
        assert "build Connectors" in shell.log and ("last_main_tab", "Connectors") in shell.remembered
        await user.should_see("Connectors body")
        set_main(user, tabs, "Settings")
        assert "resume settings" in shell.log and "build Settings" in shell.log
        set_main(user, tabs, "Chatbot")
        assert "pause settings" in shell.log
        assert shell.log.count("build Connectors") == 1
        set_main(user, tabs, "Connectors")
        assert shell.log.count("build Connectors") == 1

    run_ui(mw.create_ui_elements, check)


def test_settings_resume_failure_ignored(shell, monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(shell.settings_ui, "resume_background_work", boom)

    async def check(user):
        set_main(user, tabs_el(user), "Settings")
        assert ("last_main_tab", "Settings") in shell.remembered

    run_ui(mw.create_ui_elements, check)


def _leave(user, shell, start, target="Chatbot"):
    tabs = tabs_el(user)
    set_main(user, tabs, start)
    set_main(user, tabs, target)
    return tabs


@pytest.mark.parametrize("start,flag,message", [
    ("Settings", "settings", "You have unsaved changes in Settings."),
    ("Alerts", "alerts", "You have unsaved alert changes."),
    ("Source Settings", "source", "You have unsaved template changes."),
])
def test_unsaved_dialog_stay(shell, start, flag, message):
    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, start)
        shell.settings_ui.unsaved = flag == "settings"
        shell.alerts_dirty = flag == "alerts"
        shell.source_dirty = flag == "source"
        set_main(user, tabs, "Chatbot")
        await user.should_see(message)
        assert tabs.value == start
        # While the dialog is open, other switches are reverted
        set_main(user, tabs, "Connectors")
        assert "build Connectors" not in shell.log
        click(user, button(user, "Stay"))
        set_main(user, tabs, "Connectors")  # dialog closed and still dirty: prompts again
        await user.should_see(message)

    run_ui(mw.create_ui_elements, check)


@pytest.mark.parametrize("start,flag,discard", [
    ("Settings", "settings", "discard OBS"),
    ("Alerts", "alerts", "discard alert"),
    ("Source Settings", "source", "discard source"),
])
def test_unsaved_dialog_discard_and_leave(shell, start, flag, discard):
    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, start)
        shell.settings_ui.unsaved = flag == "settings"
        shell.alerts_dirty = flag == "alerts"
        shell.source_dirty = flag == "source"
        set_main(user, tabs, "Chatbot")

        # After discarding, the tab is clean, so the move to the target tab is allowed
        shell.settings_ui.unsaved = shell.alerts_dirty = shell.source_dirty = False
        click(user, button(user, "Discard and leave"))
        assert discard in shell.log
        assert "build Chatbot" in shell.log
        assert "discard Twitch" not in shell.log

    run_ui(mw.create_ui_elements, check)


@pytest.mark.parametrize("start,flag,save", [
    ("Settings", "settings", "save OBS"),
    ("Alerts", "alerts", "save alert"),
    ("Source Settings", "source", "save source"),
])
def test_unsaved_dialog_save_and_leave(shell, start, flag, save):
    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, start)
        shell.settings_ui.unsaved = flag == "settings"
        shell.alerts_dirty = flag == "alerts"
        shell.source_dirty = flag == "source"
        set_main(user, tabs, "Chatbot")
        shell.alerts_dirty = shell.source_dirty = False  # the save cleans them
        click(user, button(user, "Save and leave"))
        assert save in shell.log and "build Chatbot" in shell.log

    run_ui(mw.create_ui_elements, check)


@pytest.mark.parametrize("start,flag", [("Settings", "settings"), ("Alerts", "alerts"), ("Source Settings", "source")])
def test_unsaved_dialog_save_fails_stays(shell, start, flag):
    shell.settings_ui.save_clears = False
    shell.alert_save_ok = False
    shell.source_save_ok = False

    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, start)
        shell.settings_ui.unsaved = flag == "settings"
        shell.alerts_dirty = flag == "alerts"
        shell.source_dirty = flag == "source"
        set_main(user, tabs, "Chatbot")
        click(user, button(user, "Save and leave"))
        await user.should_see("Couldn't save, so you're still here and your changes are kept.")
        assert "build Chatbot" not in shell.log

    run_ui(mw.create_ui_elements, check)


def test_settings_save_exception_notifies(shell):
    shell.settings_ui.save_raises = True

    async def check(user):
        tabs = tabs_el(user)
        set_main(user, tabs, "Settings")
        shell.settings_ui.unsaved = True
        set_main(user, tabs, "Chatbot")
        click(user, button(user, "Save and leave"))
        (msg, kw), = shell.notes
        assert kw["title"] == "Couldn't save your settings" and kw["details"] == "ValueError: disk full"
        assert kw["actions"] == [{"kind": "navigate", "main_tab": "Settings"}]
        await user.should_see("Couldn't save, so you're still here")

    run_ui(mw.create_ui_elements, check)


def test_clean_leave_from_guarded_tabs(shell):
    async def check(user):
        tabs = tabs_el(user)
        for start in ("Settings", "Alerts", "Source Settings"):
            set_main(user, tabs, start)
            set_main(user, tabs, "Chatbot")
        assert shell.remembered[-1] == ("last_main_tab", "Chatbot")

    run_ui(mw.create_ui_elements, check)


def _set_silently(tabs, value):
    """Change the tab without firing value-change handlers (a change the event never reported)."""
    handlers = list(tabs._change_handlers)
    tabs._change_handlers.clear()
    try:
        tabs.value = value
    finally:
        tabs._change_handlers[:] = handlers


def test_tab_poll_fallback_handles_missed_change(shell):
    async def check(user):
        tabs = tabs_el(user)
        poll = next(t for t in shell.timers if t.interval == 5.0)
        poll.cb()  # nothing changed
        with user:
            _set_silently(tabs, "Spore Studio")
            poll.cb()
        assert "build Spore Studio" in shell.log
        assert ("last_main_tab", "Spore Studio") in shell.remembered
        assert tabs.value == "Spore Studio"
        # A missed change away from a dirty tab is reverted and prompts
        shell.alerts_dirty = True
        with user:
            _set_silently(tabs, "Alerts")
            poll.cb()
            _set_silently(tabs, "Connectors")
            poll.cb()
        await user.should_see("You have unsaved alert changes.")
        assert tabs.value == "Alerts"

    run_ui(mw.create_ui_elements, check)


def test_switch_to_every_lazy_tab(shell):
    async def check(user):
        tabs = tabs_el(user)
        for label in LAZY:
            set_main(user, tabs, label)
            assert f"build {label}" in shell.log

    run_ui(mw.create_ui_elements, check)


def test_tab_names_fall_back_when_labels_are_missing(shell, monkeypatch):
    from modules import ui_tab_transitions

    async def check(user):
        tabs = tabs_el(user)
        alerts_tab = next(t for t in user.find(kind=ui.tab).elements if t.props.get("name") == "Alerts")
        monkeypatch.setattr(ui_tab_transitions, "_tab_label", lambda t: t if isinstance(t, str) else "")
        set_main(user, tabs, alerts_tab)
        assert "build Alerts" in shell.log and ("last_main_tab", "Alerts") in shell.remembered
        set_main(user, tabs, SimpleNamespace(text="Chatbot"))
        assert "build Chatbot" in shell.log

    run_ui(mw.create_ui_elements, check)


# --------------------------------------------------------------------------- window geometry

def _geometry(user, args):
    UserInteraction(user, {user.client.layout}, None).trigger("mycelianWindowGeometry", args)  # NiceGUI camel-cases event names


def test_window_geometry_saved(shell):
    shell.settings.window_width = 1500

    async def check(user):
        _geometry(user, [1600, 900, 10, 20])
        assert shell.updates == [("window_width", 1600), ("window_height", 900), ("window_x", 10), ("window_y", 20)]
        assert shell.saves == 1

    run_ui(mw.create_ui_elements, check)


@pytest.mark.parametrize("args", [None, [1, 2], ["x", 900, 0, 0], [1000, 900, 0, 0], [1600, 800, 0, 0]])
def test_window_geometry_ignored(shell, args):
    async def check(user):
        _geometry(user, args)
        assert shell.updates == [] and shell.saves == 0

    run_ui(mw.create_ui_elements, check)


def test_window_geometry_unchanged_or_maximized(shell):
    shell.settings.window_width, shell.settings.window_height = 1600, 900

    async def check(user):
        _geometry(user, [1600, 900, 0, 0])
        assert shell.updates == [] and shell.saves == 0
        shell.settings.start_maximized = True
        _geometry(user, [1700, 900, 0, 0])
        assert shell.updates == []
        shell.settings = RuntimeError("db")
        _geometry(user, [1700, 900, 0, 0])
        assert shell.updates == []

    run_ui(mw.create_ui_elements, check)


# --------------------------------------------------------------------------- lazy tab preload worker

class FakeTime:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class Panel:
    def __init__(self, name, log, loaded=False, loads=True):
        self.name, self.log, self.loaded, self.loads = name, log, loaded, loads

    def ensure_loaded(self):
        self.log.append(f"preload {self.name}")
        if self.loads:
            self.loaded = True


@pytest.fixture
def preload(monkeypatch, shell):
    captured = []

    class CapThread:
        def __init__(self, target=None, daemon=None, name=None):
            self.target, self.daemon, self.name = target, daemon, name

        def start(self):
            captured.append(self)

    monkeypatch.setattr(mw.threading, "Thread", CapThread)
    ft = FakeTime()
    monkeypatch.setattr(mw, "time", ft)
    monkeypatch.setattr(mw, "run_on_ui_loop", lambda fn: fn())
    monkeypatch.setattr(mw, "layout_schedule", lambda i, cb, **kw: cb())
    from modules import shutdown

    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: False)
    return SimpleNamespace(captured=captured, time=ft)


def _start_preload(shell, lazy, tabs_value="Activity Feed"):
    """Build a client and start the (real) preload against it."""
    result = {}
    start = START_PRELOAD  # the real function (the shell fixture stubs mw._start_lazy_tab_preload)

    def build():
        from nicegui import context

        mw._ui_client_id = context.client.id
        tabs = SimpleNamespace(value=tabs_value)
        start(lazy, tabs)
        result["client"] = context.client

    return build, result


START_PRELOAD = mw._start_lazy_tab_preload


def test_preload_builds_main_tabs_then_settings_subtabs(shell, preload):
    log = shell.log
    lazy = {"Alerts": Panel("Alerts", log), "Source Settings": Panel("Source Settings", log, loaded=True),
            "Settings": Panel("Settings", log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        (t,) = preload.captured
        assert t.daemon and t.name == "LazyTabPreload"
        t.target()
        assert log.count("preload Alerts") == 1 and "preload Source Settings" not in log
        assert "preload Settings" in log and "pause settings" in log
        assert log.count("preload settings subtab") == 2
        assert preload.time.t >= 1000.0 + mw._LAZY_PRELOAD_DELAY_SEC

    run_ui(build, check)


def test_preload_does_not_pause_settings_when_showing(shell, preload):
    lazy = {"Settings": Panel("Settings", shell.log)}
    build, _ = _start_preload(shell, lazy, tabs_value="Settings")

    async def check(user):
        preload.captured[0].target()
        assert "preload Settings" in shell.log and "pause settings" not in shell.log

    run_ui(build, check)


def test_preload_settings_not_loaded_and_pause_error(shell, preload, monkeypatch):
    lazy = {"Settings": Panel("Settings", shell.log, loads=False), "Chatbot": Panel("Chatbot", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        preload.captured[0].target()
        assert "pause settings" not in shell.log
        lazy["Settings"].loads = True
        lazy["Settings"].loaded = False

        def boom():
            raise RuntimeError("x")

        monkeypatch.setattr(shell.settings_ui, "pause_background_work", boom)
        shell.settings_ui.next_results = [False]
        preload.captured.clear()
        with user:
            START_PRELOAD(lazy, SimpleNamespace(value="Alerts"))
        preload.captured[0].target()
        assert lazy["Settings"].loaded

    run_ui(build, check)


def test_preload_stops_when_client_changes(shell, preload):
    lazy = {"Alerts": Panel("Alerts", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        mw._ui_client_id = "someone-else"
        preload.captured[0].target()
        assert "preload Alerts" not in shell.log and preload.time.slept == []

    run_ui(build, check)


def test_preload_stops_during_shutdown(shell, preload, monkeypatch):
    from modules import shutdown

    lazy = {"Alerts": Panel("Alerts", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: True)
        preload.captured[0].target()
        assert "preload Alerts" not in shell.log

    run_ui(build, check)


def test_preload_shutdown_check_error_is_ignored(shell, preload, monkeypatch):
    from modules import shutdown

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", boom)
    lazy = {"Alerts": Panel("Alerts", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        shell.settings_ui.next_results = [False]
        preload.captured[0].target()
        assert "preload Alerts" in shell.log

    run_ui(build, check)


def test_preload_step_error_and_client_lost_mid_run(shell, preload, monkeypatch, caplog):
    class Bad(Panel):
        def ensure_loaded(self):
            raise RuntimeError("build blew up")

    lazy = {"Alerts": Bad("Alerts", shell.log), "Connectors": Panel("Connectors", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        shell.settings_ui.next_results = [False]
        preload.captured[0].target()
        assert "Lazy preload step failed" in caplog.text
        assert "preload Connectors" in shell.log

    run_ui(build, check)


def test_preload_schedule_failure_and_timeout(shell, preload, monkeypatch, caplog):
    lazy = {"Alerts": Panel("Alerts", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        def boom(*a, **k):
            raise RuntimeError("no slot")

        monkeypatch.setattr(mw, "layout_schedule", boom)
        preload.captured[0].target()
        assert "Could not schedule lazy preload step" in caplog.text
        assert "preload Alerts" not in shell.log

        class NeverDone:
            def set(self):
                pass

            def wait(self, timeout):
                return False

        monkeypatch.setattr(mw.threading, "Event", NeverDone)
        monkeypatch.setattr(mw, "run_on_ui_loop", lambda fn: None)
        preload.captured.clear()
        with user:
            START_PRELOAD(lazy, SimpleNamespace(value="Alerts"))
        preload.captured[0].target()
        assert "Lazy preload step timed out" in caplog.text

    run_ui(build, check)


def test_preload_client_changes_inside_steps(shell, preload, monkeypatch):
    """Each step re-checks the client: a client that goes away between steps stops the run."""
    log = shell.log

    class Leaver(Panel):
        def ensure_loaded(self):
            super().ensure_loaded()
            mw._ui_client_id = "gone"

    lazy = {"Alerts": Leaver("Alerts", log), "Connectors": Panel("Connectors", log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        preload.captured[0].target()
        assert "preload Alerts" in log and "preload Connectors" not in log

    run_ui(build, check)


def test_preload_schedule_sees_client_gone(shell, preload, monkeypatch):
    lazy = {"Alerts": Panel("Alerts", shell.log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        def gone_then_run(fn):
            mw._ui_client_id = "gone"
            fn()

        monkeypatch.setattr(mw, "run_on_ui_loop", gone_then_run)
        preload.captured[0].target()
        assert "preload Alerts" not in shell.log

    run_ui(build, check)


def test_preload_stops_at_every_checkpoint(shell, preload, monkeypatch):
    """Shutdown can start at any point in the run; each checkpoint stops the worker cleanly."""
    from modules import shutdown

    log = shell.log
    calls = {"n": 0, "limit": 0}

    def shutting_down():
        calls["n"] += 1
        return calls["n"] > calls["limit"]

    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", shutting_down)

    class Ticking(FakeTime):
        def monotonic(self):
            self.t += 0.3  # time passes between reads, so a gap can end mid-loop
            return self.t

    monkeypatch.setattr(mw, "time", Ticking())
    lazy = {"Alerts": Panel("Alerts", log), "Connectors": Panel("Connectors", log)}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        finished = 0
        for limit in range(0, 400, 3):
            calls["n"], calls["limit"] = 0, limit
            lazy["Alerts"].loaded = lazy["Connectors"].loaded = False
            shell.settings_ui.next_results = [True, True, False]
            preload.captured.clear()
            with user:
                START_PRELOAD(lazy, SimpleNamespace(value="Alerts"))
            preload.captured[0].target()
            if calls["n"] <= limit:
                finished += 1
        assert finished > 0  # the largest limits let the run finish

    run_ui(build, check)


def test_preload_settings_loop_stops_when_client_lost(shell, preload, monkeypatch):
    lazy = {}
    build, _ = _start_preload(shell, lazy)

    async def check(user):
        orig = shell.settings_ui.load_next_unloaded_settings_tab

        def load_and_leave():
            mw._ui_client_id = "gone"
            return orig()

        monkeypatch.setattr(shell.settings_ui, "load_next_unloaded_settings_tab", load_and_leave)
        preload.captured[0].target()
        assert shell.log.count("preload settings subtab") == 1

    run_ui(build, check)

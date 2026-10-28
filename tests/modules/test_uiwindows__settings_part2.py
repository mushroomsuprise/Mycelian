# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.uiwindows.settings``: the built Settings shell (sub-tab switching, lazy loading,
unsaved-changes dialog with Stay / Discard / Save and switch) and the Add-to-OBS dialog.

Every real settings tab is replaced by a fake module in ``sys.modules``; nothing real is built or saved.
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

import types
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import mainuiwindow
from modules.help_system import contextual_help as ch
from modules.uiwindows import settings as st
from ui_sim import run_ui

TAB_MODULES = {
    "app_settings_tab": ("AppSettingsTab", "App Settings"),
    "database_tab": ("DatabaseTab", "Database"),
    "discord_tab": ("DiscordTab", "Discord"),
    "game_hooks_tab": ("GameHooksTab", "Game Hooks"),
    "obs_tab": ("ObsTab", "OBS"),
    "psn_tab": ("PSNTab", "PSN"),
    "spotify_tab": ("SpotifyTab", "Spotify"),
    "statistics_tab": ("StatisticsTab", "Statistics"),
    "theme_tab": ("ThemeTab", "Theme"),
    "twitch_tab": ("TwitchTab", "Twitch"),
    "youtube_tab": ("YouTubeTab", "YouTube"),
    "streamlabs_tab": ("StreamlabsTab", "Streamlabs"),
    "streamelements_tab": ("StreamElementsTab", "StreamElements"),
}


class Shell:
    def __init__(self):
        self.log = []
        self.tabs = {}
        self.fail_build = set()
        self.save_raises = set()
        self.save_leaves_dirty = set()
        self.timers = []
        self.notes = []
        self.remembered = []
        self.ui = None


def make_tab_class(shell, cls_name, label):
    class FakeTab:
        name = label

        def __init__(self):
            self.dirty = False
            self.ui_elements = {}
            shell.tabs[label] = self

        def build(self, container):
            if label in shell.fail_build:
                raise RuntimeError(f"{label} exploded")
            with container:
                ui.label(f"{label} content")
                self.ui_elements["field"] = ui.input(f"{label} field", value="v")
            shell.log.append(("build", label))

        def save(self):
            shell.log.append(("save", label))
            if label in shell.save_raises:
                raise ValueError("disk full")
            if label not in shell.save_leaves_dirty:
                self.dirty = False

        def discard(self):
            shell.log.append(("discard", label, getattr(self, "_suppress_dirty", None)))
            self.dirty = False
            if "field" in self.ui_elements:
                self.ui_elements["field"].value = "v"

        def mark_clean(self):
            shell.log.append(("mark_clean", label))

        def on_enter(self):
            shell.log.append(("enter", label))

        def on_exit(self):
            shell.log.append(("exit", label))

    FakeTab.__name__ = cls_name
    return FakeTab


@pytest.fixture
def shell(monkeypatch):
    sh = Shell()
    for mod_name, (cls_name, label) in TAB_MODULES.items():
        mod = types.ModuleType(f"modules.uiwindows.tabs.{mod_name}")
        setattr(mod, cls_name, make_tab_class(sh, cls_name, label))
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    about = types.ModuleType("modules.uiwindows.tabs.about_tab")

    class AboutTab:
        def __init__(self, owner):
            self.owner = owner

        def build(self, container):
            with container:
                ui.label("About content")
            sh.log.append(("build", "About"))

    about.AboutTab = AboutTab
    monkeypatch.setitem(sys.modules, about.__name__, about)
    sh.settings = SimpleNamespace(last_settings_tab="Twitch")
    sh.state = SimpleNamespace(_initialized=True, initialize=lambda: sh.log.append(("init",)),
                               get_app_settings=lambda: sh.settings)
    monkeypatch.setattr(st, "state_manager", sh.state)
    monkeypatch.setattr(ch, "set_settings_ui_references", lambda tabs, panels: sh.log.append(("refs",)))

    class Timer:
        def __init__(self, interval, cb, kw):
            self.interval, self.cb, self.kw, self.active = interval, cb, kw, kw.get("active", True)

        def cancel(self):
            self.active = False

    def sched(interval, cb, **kw):
        t = Timer(interval, cb, kw)
        sh.timers.append(t)
        return t

    monkeypatch.setattr(st, "layout_schedule", sched)
    monkeypatch.setattr(st, "notify", lambda msg, **kw: sh.notes.append((msg, kw)))
    monkeypatch.setattr(mainuiwindow, "remember_ui_pref", lambda k, v: sh.remembered.append((k, v)))
    return sh


def build_shell(sh):
    def build():
        sh.ui = st.SettingsUI()
        sh.ui.build_ui_v2()

    return build


def timer(sh, interval, once=None):
    for t in sh.timers:
        if t.interval == interval and (once is None or t.kw.get("once", False) == once):
            return t
    raise AssertionError(f"no timer {interval}")


def set_tab(user, sh, name):
    with user:
        sh.ui._settings_tabs.value = name


def click(user, element):
    UserInteraction(user, {element}, None).click()


def button(user, text):
    return next(b for b in user.find(kind=ui.button).elements if b.text == text)


# --------------------------------------------------------------------------- building

def test_build_default_shell(shell):
    async def check(user):
        s = shell.ui
        assert set(s._tabs_by_name) == {label for _c, label in TAB_MODULES.values()}
        assert s._active_tab_name == "Twitch" and s._accepted_settings_tab == "Twitch"
        assert ("refs",) in shell.log
        # Eager tabs are built at once; the rest show a spinner
        built = [e[1] for e in shell.log if e[0] == "build"]
        assert built == ["Statistics", "App Settings", "About"]
        assert s._settings_loaded_tabs == {"Statistics", "App Settings", "About"}
        assert hasattr(s._settings_tab_containers["OBS"], "_loading_spinner")
        await user.should_see("Loading...")
        # Timers: tab poll, initial lazy load, unsaved-change poll
        assert timer(shell, 2.0).kw == {"active": True}
        initial = timer(shell, 0.05)
        assert initial.kw == {"once": True}
        initial.cb()
        assert ("build", "Twitch") in shell.log and "Twitch" in s._settings_loaded_tabs
        await user.should_see("Twitch content")
        assert not hasattr(s._settings_tab_containers["Twitch"], "_loading_label") or \
            s._settings_tab_containers["Twitch"]._loading_label.is_deleted
        assert s._settings_jump.value == "Twitch"
        assert s.has_unsaved_changes() is False

    run_ui(build_shell(shell), check)


@pytest.mark.parametrize("saved,expected", [("OBS", "OBS"), ("About", "About"), ("Nope", "Twitch"), ("", "Twitch"),
                                            (None, "Twitch")])
def test_build_restores_last_tab(shell, saved, expected):
    shell.settings.last_settings_tab = saved

    async def check(user):
        assert shell.ui._active_tab_name == expected
        assert shell.ui._settings_tabs.value == expected

    run_ui(build_shell(shell), check)


def test_build_initializes_state_when_needed(shell):
    shell.state._initialized = False

    async def check(user):
        assert ("init",) in shell.log

    run_ui(build_shell(shell), check)
    del shell.state._initialized
    shell.log.clear()
    run_ui(build_shell(shell), check)


def test_initial_tab_already_loaded_skips_lazy_load(shell):
    shell.settings.last_settings_tab = "App Settings"

    async def check(user):
        assert all(t.interval != 0.05 for t in shell.timers)

    run_ui(build_shell(shell), check)


def test_tab_change_event_lazy_loads(shell):
    async def check(user):
        s = shell.ui
        tabs = s._settings_tabs
        UserInteraction(user, {tabs}, None).trigger("change", {"value": "OBS"})
        assert "OBS" in s._settings_loaded_tabs
        await user.should_see("OBS content")
        # Already loaded / unknown / event without a value: nothing more happens
        n = len(shell.log)
        UserInteraction(user, {tabs}, None).trigger("change", {"value": "OBS"})
        UserInteraction(user, {tabs}, None).trigger("change", {"value": "Nope"})
        UserInteraction(user, {tabs}, None).trigger("change", None)
        assert len(shell.log) == n
        s._load_settings_tab_content("")
        s._load_settings_tab_content("Unknown tab")

    run_ui(build_shell(shell), check)


@pytest.mark.parametrize("label", ["Theme", "PSN", "Spotify", "YouTube", "Streamlabs", "StreamElements", "Discord",
                                   "Game Hooks", "Database"])
def test_every_lazy_tab_builds(shell, label):
    async def check(user):
        shell.ui._load_settings_tab_content(label)
        assert ("build", label) in shell.log
        assert label in shell.ui._settings_loaded_tabs

    run_ui(build_shell(shell), check)


def test_lazy_reload_of_eager_tabs_branches(shell):
    """The loader also knows how to build the eager tabs (used if they were skipped)."""
    async def check(user):
        s = shell.ui
        for label in ("App Settings", "Statistics", "About"):
            s._settings_loaded_tabs.discard(label)
            s._load_settings_tab_content(label)
            assert label in s._settings_loaded_tabs

    run_ui(build_shell(shell), check)


def test_lazy_load_failure_shows_message_and_notifies(shell):
    shell.fail_build.add("OBS")

    async def check(user):
        s = shell.ui
        s._load_settings_tab_content("OBS")
        await user.should_see("This page couldn't be loaded. Details are in the log.")
        assert "OBS" not in s._settings_loaded_tabs
        (msg, kw), = shell.notes
        assert kw["title"] == "Couldn't open OBS settings" and kw["dedupe_key"] == "settings:tab_load:OBS"
        assert kw["details"] == "RuntimeError: OBS exploded"
        # Background preload marks it failed and moves on
        assert s.load_next_unloaded_settings_tab() is True

    run_ui(build_shell(shell), check)


def test_loading_placeholders_already_removed_is_tolerated(shell):
    async def check(user):
        s = shell.ui
        c = s._settings_tab_containers["PSN"]
        c.remove(c._loading_spinner)  # gone already
        s._load_settings_tab_content("PSN")
        assert "PSN" in s._settings_loaded_tabs

    run_ui(build_shell(shell), check)


def test_tab_poll_timer_loads_tab_the_change_event_missed(shell):
    async def check(user):
        s = shell.ui
        poll = shell.timers[0]
        assert poll.interval == 2.0
        poll.cb()  # nothing changed
        set_tab(user, shell, "Spotify")
        poll.cb()
        assert "Spotify" in s._settings_loaded_tabs
        poll.cb()
        set_tab(user, shell, "App Settings")
        poll.cb()  # loaded already: only remembers the tab

    run_ui(build_shell(shell), check)


def test_background_preload_builds_and_pauses(shell):
    async def check(user):
        s = shell.ui
        assert s.load_next_unloaded_settings_tab() is True
        assert ("build", "Twitch") in shell.log
        # Twitch is the active tab: not paused
        assert ("exit", "Twitch") not in shell.log
        assert s.load_next_unloaded_settings_tab() is True
        assert ("build", "OBS") in shell.log and ("exit", "OBS") in shell.log

    run_ui(build_shell(shell), check)


# --------------------------------------------------------------------------- switching tabs

def test_clean_switch_enters_and_exits(shell):
    async def check(user):
        s = shell.ui
        timer(shell, 0.05).cb()
        set_tab(user, shell, "OBS")
        assert s._active_tab_name == "OBS" and s._accepted_settings_tab == "OBS"
        assert ("exit", "Twitch") in shell.log and ("enter", "OBS") in shell.log
        assert shell.remembered[-1] == ("last_settings_tab", "OBS")
        assert s._settings_jump.value == "OBS"
        set_tab(user, shell, "About")  # About has no tab object
        assert s._active_tab_name == "About"

    run_ui(build_shell(shell), check)


def test_jump_select_switches_tab(shell):
    async def check(user):
        s = shell.ui
        with user:
            s._settings_jump.value = "Database"
        assert s._settings_tabs.value == "Database" and s._active_tab_name == "Database"

    run_ui(build_shell(shell), check)


def _make_dirty(user, shell, label="Twitch"):
    timer(shell, 0.05).cb()
    with user:
        shell.tabs[label].ui_elements["field"].value = "edited"
    assert shell.ui._tab_is_dirty(label)


def test_dirty_switch_opens_dialog_and_stay(shell):
    async def check(user):
        s = shell.ui
        _make_dirty(user, shell)
        set_tab(user, shell, "OBS")
        await user.should_see("Unsaved changes")
        assert s._unsaved_dialog_open is True and s._settings_tabs.value == "Twitch"
        assert s._active_tab_name == "Twitch"
        # While the dialog is open, another switch is reverted
        set_tab(user, shell, "PSN")
        assert s._settings_tabs.value == "Twitch"
        click(user, button(user, "Stay"))
        assert s._unsaved_dialog_open is False and s._settings_tabs.value == "Twitch"
        assert s._settings_jump.value == "Twitch"
        assert ("enter", "OBS") not in shell.log

    run_ui(build_shell(shell), check)


def test_discard_and_switch(shell):
    async def check(user):
        s = shell.ui
        _make_dirty(user, shell)
        set_tab(user, shell, "OBS")
        click(user, button(user, "Discard and switch"))
        assert ("discard", "Twitch", True) in shell.log and ("mark_clean", "Twitch") in shell.log
        assert shell.tabs["Twitch"]._suppress_dirty is False
        assert s._active_tab_name == "OBS" and s._settings_tabs.value == "OBS"
        assert s._unsaved_dialog_open is False and not s._tab_is_dirty("Twitch")
        assert ("enter", "OBS") in shell.log

    run_ui(build_shell(shell), check)


def test_save_and_switch(shell):
    async def check(user):
        s = shell.ui
        _make_dirty(user, shell)
        set_tab(user, shell, "OBS")
        click(user, button(user, "Save and switch"))
        assert ("save", "Twitch") in shell.log
        assert s._active_tab_name == "OBS" and s._unsaved_dialog_open is False
        assert shell.tabs["Twitch"]._field_snapshot == {"field": "edited"}

    run_ui(build_shell(shell), check)


def test_save_and_switch_save_raises_keeps_dialog(shell):
    shell.save_raises.add("Twitch")

    async def check(user):
        s = shell.ui
        _make_dirty(user, shell)
        set_tab(user, shell, "OBS")
        click(user, button(user, "Save and switch"))
        await user.should_see("Couldn't save Twitch settings, so you're still on that tab.")
        assert s._unsaved_dialog_open is True and s._active_tab_name == "Twitch"
        (msg, kw), = shell.notes
        assert kw["title"] == "Couldn't save Twitch settings" and kw["details"] == "ValueError: disk full"

    run_ui(build_shell(shell), check)


def test_save_and_switch_still_dirty_keeps_dialog(shell):
    shell.save_leaves_dirty.add("Twitch")

    async def check(user):
        s = shell.ui
        _make_dirty(user, shell)
        shell.tabs["Twitch"].dirty = True
        set_tab(user, shell, "OBS")
        click(user, button(user, "Save and switch"))
        await user.should_see("Your changes are still here")
        assert s._active_tab_name == "Twitch" and shell.notes == []

    run_ui(build_shell(shell), check)


def test_dirty_other_tab_still_prompts(shell):
    """A dirty tab that isn't the one being left still prompts (label mismatch fallback)."""
    async def check(user):
        s = shell.ui
        shell.tabs["Theme"].dirty = True
        set_tab(user, shell, "OBS")
        assert s._unsaved_dialog_open is True and s._active_tab_name == "Theme"
        click(user, button(user, "Discard and switch"))
        assert s._active_tab_name == "OBS"

    run_ui(build_shell(shell), check)


def test_switch_guard_and_same_tab_events_ignored(shell):
    async def check(user):
        s = shell.ui
        s._settings_switch_guard = True
        set_tab(user, shell, "OBS")
        assert s._active_tab_name == "Twitch"
        s._settings_switch_guard = False
        set_tab(user, shell, "Twitch")  # back to the accepted tab: no-op
        assert s._active_tab_name == "Twitch"
        n = len(shell.log)
        set_tab(user, shell, None)  # no tab label: ignored
        assert len(shell.log) == n and s._active_tab_name == "Twitch"

    run_ui(build_shell(shell), check)


def test_subtab_poll_catches_missed_change(shell):
    async def check(user):
        s = shell.ui
        subpoll = shell.timers[-1]
        assert subpoll.interval == 2.0 and subpoll is not shell.timers[0]
        subpoll.cb()  # nothing to do
        s._settings_switch_guard = True
        with user:
            s._settings_tabs.value = "OBS"  # guarded: no handler work
        s._settings_switch_guard = False
        assert s._accepted_settings_tab == "Twitch"
        subpoll.cb()
        assert s._accepted_settings_tab == "OBS" and ("enter", "OBS") in shell.log
        s._unsaved_dialog_open = True
        with user:
            s._settings_tabs.value = "PSN"
        subpoll.cb()  # dialog open: skip

    run_ui(build_shell(shell), check)


def test_pause_and_resume_after_build(shell):
    async def check(user):
        s = shell.ui
        s.pause_background_work()
        assert shell.timers[0].active is False and shell.timers[-1].active is False
        assert ("exit", "Twitch") in shell.log
        s.resume_background_work()
        assert shell.timers[0].active is True and ("enter", "Twitch") in shell.log

    run_ui(build_shell(shell), check)


# --------------------------------------------------------------------------- Add to OBS

class FakeObs:
    def __init__(self):
        self.enabled = True
        self.connected = True
        self.refresh = (True, None)
        self.snapshot = {"scene_names": ["Main", "BRB", " "], "current_program_scene_name": "BRB",
                         "input_names": ["Mic"]}
        self.scan = (True, {"taken_names": ["Now Playing", "Mic"], "urls_by_scene": {"Main": ["http://x/t"]}})
        self.created = []
        self.create_result = (True, "Added it")
        self.disconnect_after_refresh = False

    def is_enabled(self):
        return self.enabled

    def is_connected(self):
        return self.connected

    def refresh_snapshot_blocking(self, timeout):
        if self.disconnect_after_refresh:
            self.connected = False
        return self.refresh

    def get_connector_snapshot(self):
        return self.snapshot

    def list_scene_browser_urls_blocking(self, timeout):
        return self.scan

    def create_browser_source_blocking(self, **kw):
        self.created.append(kw)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result


@pytest.fixture
def obs(monkeypatch, shell):
    from modules import obs_service as obs_mod

    fake = FakeObs()
    monkeypatch.setattr(obs_mod, "obs_service", fake)

    async def io_bound(fn, *args, **kw):
        return fn(*args, **kw)

    monkeypatch.setattr(st, "run", SimpleNamespace(io_bound=io_bound))
    return fake


URL_INFO = {"name": "now_playing", "url": "http://x/t"}


def test_prompt_add_source_obs_disabled(obs, shell):
    obs.enabled = False
    st.SettingsUI()._prompt_add_source_to_obs(URL_INFO)
    assert shell.notes[0][1]["title"] == "OBS WebSocket is turned off" and shell.timers == []


def test_prompt_add_source_obs_not_connected(obs, shell):
    obs.connected = False
    st.SettingsUI()._prompt_add_source_to_obs(URL_INFO)
    assert shell.notes[0][1]["title"] == "OBS isn't connected" and shell.timers == []


@pytest.mark.parametrize("case,title", [
    ("lost", "OBS isn't connected"),
    ("refresh_failed", "Couldn't get the scene list from OBS"),
    ("no_scenes", "OBS has no scenes yet"),
])
def test_prompt_add_source_open_failures(obs, shell, case, title):
    if case == "lost":
        obs.disconnect_after_refresh = True
    elif case == "refresh_failed":
        obs.refresh = (False, None)
    else:
        obs.snapshot = {"scene_names": ["", "  "]}
    s = st.SettingsUI()
    s._prompt_add_source_to_obs(URL_INFO)
    (t,) = shell.timers
    assert t.interval == 0 and t.kw == {"once": True}
    import asyncio

    asyncio.run(t.cb())
    assert shell.notes[-1][1]["title"] == title
    if case == "refresh_failed":
        assert shell.notes[-1][1]["details"] == "Timed out waiting for OBS"


@pytest.mark.parametrize("scan_ok", [True, False])
def test_prompt_add_source_opens_dialog(obs, shell, monkeypatch, scan_ok):
    if not scan_ok:
        obs.scan = (False, None)
    s = st.SettingsUI()
    seen = []
    monkeypatch.setattr(s, "_show_add_to_obs_dialog", lambda *a: seen.append(a))
    s._prompt_add_source_to_obs(URL_INFO)
    import asyncio

    asyncio.run(shell.timers[0].cb())
    (url_info, snap, scenes, scan), = seen
    assert scenes == ["Main", "BRB"]
    if scan_ok:
        assert scan["taken_names"] == ["Now Playing", "Mic"]
    else:
        assert scan == {"taken_names": ["Mic", "Main", "BRB"], "urls_by_scene": {}}


def _open_dialog(shell, obs, scan=None, url_info=URL_INFO, snap=None):
    def build():
        shell.ui = st.SettingsUI()
        shell.ui._show_add_to_obs_dialog(url_info, snap or obs.snapshot, ["Main", "BRB"],
                                         scan if scan is not None else obs.scan[1])

    return build


def _el(user, kind, label):
    return next(e for e in user.find(kind=kind).elements if e.props.get("label") == label)


async def _run_create(shell):
    t = shell.timers[-1]
    await t.cb()


def test_add_to_obs_dialog_defaults_and_warnings(obs, shell):
    async def check(user):
        await user.should_see("Add to OBS")
        await user.should_see("Now Playing")
        scene = _el(user, ui.select, "Scene")
        name = _el(user, ui.input, "Source name")
        assert scene.value == "BRB"  # current program scene preselected
        assert name.value == "Now Playing 2"  # "Now Playing" is taken
        await user.should_see('A source named "Now Playing" already exists in OBS, so the name was changed to "Now Playing 2".')
        with user:
            scene.value = "Main"
        await user.should_see('This template is already a browser source on "Main".')
        with user:
            scene.value = "BRB"
            name.value = "Mic"
        await user.should_see('A source named "Mic" already exists in OBS.')
        with user:
            name.value = ""
            name.value = "Fresh"
        await user.should_not_see('already exists in OBS.')
        fps_cb = next(c for c in user.find(kind=ui.checkbox).elements if c.text == "Use custom frame rate")
        assert not [e for e in user.find(kind=ui.number).elements if e.props.get("label") == "FPS"]
        with user:
            fps_cb.value = True
        assert _el(user, ui.number, "FPS").value == 60
        click(user, button(user, "Cancel"))

    run_ui(_open_dialog(shell, obs), check)


def test_add_to_obs_dialog_first_scene_when_current_unknown_and_fallback_taken_names(obs, shell):
    snap = {"scene_names": ["Main", "BRB"], "current_program_scene_name": "Gone", "input_names": ["Now Playing", " "]}

    async def check(user):
        assert _el(user, ui.select, "Scene").value == "Main"
        assert _el(user, ui.input, "Source name").value == "Now Playing 2"

    run_ui(_open_dialog(shell, obs, scan={}, snap=snap, url_info={"title": "Now Playing", "url": "u"}), check)


def test_add_to_obs_submit_success(obs, shell):
    async def check(user):
        with user:
            _el(user, ui.input, "Source name").value = "Overlay"
            next(c for c in user.find(kind=ui.checkbox).elements if c.text == "Control audio via OBS").value = True
            next(c for c in user.find(kind=ui.checkbox).elements if c.text == "Use custom frame rate").value = True
            _el(user, ui.number, "FPS").value = 30
        add = button(user, "Add")
        click(user, add)
        assert add.enabled is False
        await _run_create(shell)
        (kw,) = obs.created
        assert kw == {"scene_name": "BRB", "input_name": "Overlay", "url": "http://x/t", "width": 1920,
                      "height": 1080, "reroute_audio": True, "fps_custom": True, "fps": 30, "shutdown": True,
                      "restart_when_active": False}
        assert shell.notes[-1][1]["title"] == "Added it" and shell.notes[-1][1]["type"] == "success"

    run_ui(_open_dialog(shell, obs), check)


@pytest.mark.parametrize("result,details", [((False, "name taken"), "name taken"), ((False, ""), "Unknown error"),
                                            (RuntimeError("socket"), "socket"), (RuntimeError(""), "Could not add the browser source")])
def test_add_to_obs_submit_failure_reenables(obs, shell, result, details):
    obs.create_result = result

    async def check(user):
        with user:
            _el(user, ui.input, "Source name").value = "Overlay"
        add = button(user, "Add")
        click(user, add)
        await _run_create(shell)
        assert add.enabled is True
        assert shell.notes[-1][1]["title"] == "Couldn't add the browser source to OBS"
        assert shell.notes[-1][1]["details"] == details

    run_ui(_open_dialog(shell, obs), check)


def test_add_to_obs_success_default_title(obs, shell):
    obs.create_result = (True, "")

    async def check(user):
        with user:
            _el(user, ui.input, "Source name").value = "Overlay"
        click(user, button(user, "Add"))
        await _run_create(shell)
        assert shell.notes[-1][1]["title"] == "Browser source added"

    run_ui(_open_dialog(shell, obs), check)


@pytest.mark.parametrize("field,value,title", [
    ("scene", None, "Choose a scene"),
    ("name", "   ", "Enter a source name"),
    ("width", None, "Enter a width"),
    ("width", 10.5, "Width must be a whole number"),
    ("width", 0, "Width must be at least 1"),
    ("height", 0, "Height must be at least 1"),
    ("fps", 5000, "FPS must be between 1 and 1000"),
    ("fps", None, "Enter a fps"),
])
def test_add_to_obs_validation(obs, shell, field, value, title):
    async def check(user):
        with user:
            _el(user, ui.input, "Source name").value = "Overlay"
            if field == "scene":
                _el(user, ui.select, "Scene").value = value
            elif field == "name":
                _el(user, ui.input, "Source name").value = value
            elif field == "fps":
                next(c for c in user.find(kind=ui.checkbox).elements if c.text == "Use custom frame rate").value = True
                _el(user, ui.number, "FPS").value = value
            else:
                _el(user, ui.number, field.title()).value = value
        click(user, button(user, "Add"))
        assert shell.notes[-1][1]["title"] == title
        assert obs.created == [] and shell.timers == []

    run_ui(_open_dialog(shell, obs), check)


def test_add_to_obs_taken_name_is_adjusted_not_submitted(obs, shell):
    async def check(user):
        name = _el(user, ui.input, "Source name")
        with user:
            name.value = "Mic"
        click(user, button(user, "Add"))
        assert name.value == "Mic 2" and obs.created == []
        await user.should_see('A source named "Mic" already exists in OBS, so the name was changed to "Mic 2".')

    run_ui(_open_dialog(shell, obs), check)

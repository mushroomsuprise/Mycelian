# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.uiwindows.settings``: helpers, unsaved-change tracking, changelog, updates and
background-work control. The built Settings shell and its dialogs are in ``..._part2.py``."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import asyncio
import threading
import types
from types import SimpleNamespace

import aiohttp
import pytest
from nicegui import ui

from modules.uiwindows import settings as st
from ui_sim import run_ui


@pytest.fixture
def notes(monkeypatch):
    out = []
    monkeypatch.setattr(st, "notify", lambda msg, **kw: out.append((msg, kw)))
    return out


def new_ui():
    return st.SettingsUI()


class Input:  # _is_settings_field matches on the class name
    def __init__(self, value):
        self.value = value


class Broken:
    @property
    def value(self):
        raise RuntimeError("deleted")


Broken.__name__ = "Input"


class Tab:
    def __init__(self, **fields):
        self.ui_elements = {k: Input(v) for k, v in fields.items()}
        self.dirty = False
        self.calls = []

    def save(self):
        self.calls.append("save")

    def discard(self):
        self.calls.append("discard")

    def on_enter(self):
        self.calls.append("enter")

    def on_exit(self):
        self.calls.append("exit")


# --------------------------------------------------------------------------- init / small helpers

def test_init_defaults():
    s = new_ui()
    assert s.oauth_in_progress == {"spotify": False, "twitch": False}
    assert s._active_tab_name == "Twitch" and s._accepted_settings_tab == "Twitch"
    assert s._tabs_by_name == {} and s._unsaved_dialog_open is False and s._settings_jump is None
    assert s.is_initialized is False and s._psn_initialized is False


def test_format_source_url_title():
    assert st.SettingsUI._format_source_url_title("now_playing_card") == "Now Playing Card"


def test_open_logs_folder_success(monkeypatch, notes):
    opened = []
    monkeypatch.setattr(st, "get_log_dir", lambda: "/tmp/logs")
    monkeypatch.setattr(st, "reveal_in_file_manager", opened.append)
    new_ui().open_logs_folder()
    assert opened == ["/tmp/logs"] and notes == []


def test_open_logs_folder_reveal_fails(monkeypatch, notes):
    monkeypatch.setattr(st, "get_log_dir", lambda: "/tmp/logs")

    def boom(p):
        raise OSError("no file manager")

    monkeypatch.setattr(st, "reveal_in_file_manager", boom)
    new_ui().open_logs_folder()
    ((msg, kw),) = notes
    assert msg == "You can find it at /tmp/logs." and kw["title"] == "Couldn't open the logs folder"
    assert kw["actions"] == [{"kind": "copy", "text": "/tmp/logs", "label": "Copy path"}]
    assert kw["details"].startswith("/tmp/logs\n")


def test_open_logs_folder_no_dir(monkeypatch, notes):
    def boom():
        raise RuntimeError("no dir")

    monkeypatch.setattr(st, "get_log_dir", boom)
    new_ui().open_logs_folder()
    ((msg, kw),) = notes
    assert msg == "" and kw["actions"] is None and kw["details"] == "no dir"


@pytest.mark.parametrize("ok,title", [(True, "Copied!"), (False, "Couldn't copy to the clipboard")])
def test_copy_url_to_clipboard(monkeypatch, notes, ok, title):
    from modules import notification_engine as ne

    monkeypatch.setattr(ne, "copy_text_to_clipboard", lambda t: ok)
    new_ui()._copy_url_to_clipboard("http://x", notice="Copied!")
    assert notes[0][1]["title"] == title
    assert notes[0][1]["type"] == ("success" if ok else "warning")


def test_copy_url_default_notice(monkeypatch, notes):
    from modules import notification_engine as ne

    monkeypatch.setattr(ne, "copy_text_to_clipboard", lambda t: True)
    new_ui()._copy_url_to_clipboard("http://x")
    assert notes[0][1]["title"] == "Link copied"


def test_notify_obs_not_connected(notes):
    st.SettingsUI._notify_obs_not_connected()
    assert notes[0][1]["title"] == "OBS isn't connected"
    assert notes[0][1]["actions"][0]["settings_subtab"] == "OBS"


def test_check_for_updates_manual(monkeypatch, notes):
    from modules import updater

    calls = []
    monkeypatch.setattr(updater, "update_manager", SimpleNamespace(trigger_manual_check=lambda: calls.append(1)))
    new_ui().check_for_updates_manual()
    assert calls == [1] and notes == []

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(updater, "update_manager", SimpleNamespace(trigger_manual_check=boom))
    new_ui().check_for_updates_manual()
    assert notes[0][1]["title"] == "Couldn't check for updates" and notes[0][1]["details"] == "offline"


# --------------------------------------------------------------------------- field snapshots / dirty tracking

def test_is_settings_field():
    for name in ("Input", "Textarea", "Number", "Select", "Switch", "Checkbox", "Slider", "Toggle", "ColorInput"):
        assert st.SettingsUI._is_settings_field(type(name, (), {})())
    assert not st.SettingsUI._is_settings_field(object())
    assert not st.SettingsUI._is_settings_field(type("Button", (), {})())


@pytest.mark.parametrize("left,right,equal", [
    (1, 1, True), (None, "", True), ("", None, True), (None, None, True),
    (1, 1.0, True), (2, 2.5, False), (True, 1, True), (True, 2, False), (False, 0.0, True),
    ("a", "b", False), (0, "", False), ([1], [1], True),
])
def test_settings_values_equal(left, right, equal):
    assert st.SettingsUI._settings_values_equal(left, right) is equal


def test_settings_values_equal_bool_vs_number_not_coerced():
    # bools are excluded from the numeric comparison (only == applies)
    assert st.SettingsUI._settings_values_equal(True, 1.0) is True  # Python: True == 1.0
    assert st.SettingsUI._settings_values_equal(False, 2) is False


def test_snapshot_tab_fields_skips_non_fields_and_errors():
    tab = Tab(a="x", b=3)
    tab.ui_elements["btn"] = type("Button", (), {"value": "nope"})()
    tab.ui_elements["broken"] = Broken()
    s = new_ui()
    s._snapshot_tab_fields(tab)
    assert tab._field_snapshot == {"a": "x", "b": 3}
    plain = SimpleNamespace()  # no ui_elements at all
    s._snapshot_tab_fields(plain)
    assert plain._field_snapshot == {}


def test_note_settings_tab_loaded():
    s = new_ui()
    s._note_settings_tab_loaded("missing")
    tab = Tab(a=1)
    tab.dirty = True
    s._tabs_by_name = {"T": tab}
    s._note_settings_tab_loaded("T")
    assert tab.dirty is False and tab._field_snapshot == {"a": 1}


def test_tab_is_dirty_paths():
    s = new_ui()
    assert s._tab_is_dirty("none") is False
    t = Tab(a="x", gone="y")
    s._tabs_by_name = {"T": t}
    assert s._tab_is_dirty("T") is False  # no snapshot yet
    s._snapshot_tab_fields(t)
    assert s._tab_is_dirty("T") is False
    del t.ui_elements["gone"]
    t.ui_elements["a"].value = "changed"
    assert s._tab_is_dirty("T") is True
    t.ui_elements["a"].value = "x"
    t.ui_elements["broken"] = Broken()
    t._field_snapshot["broken"] = 1
    assert s._tab_is_dirty("T") is False
    t.dirty = True
    assert s._tab_is_dirty("T") is True


def test_tab_is_dirty_uses_custom_checker():
    s = new_ui()
    t = Tab()
    t.has_unsaved_changes = lambda: 1
    s._tabs_by_name = {"T": t}
    assert s._tab_is_dirty("T") is True

    def boom():
        raise RuntimeError("x")

    t.has_unsaved_changes = boom
    assert s._tab_is_dirty("T") is False  # falls back to the dirty flag / snapshot
    t.dirty = True
    assert s._tab_is_dirty("T") is True


def test_first_dirty_tab_name_and_has_unsaved():
    s = new_ui()
    assert s.has_unsaved_changes() is False
    a, b = Tab(), Tab()
    s._tabs_by_name = {"A": a, "B": b}
    assert s._first_dirty_tab_name() == "" and s.has_unsaved_changes() is False
    b.dirty = True
    assert s._first_dirty_tab_name() == "B" and s.has_unsaved_changes() is True
    s._tabs_by_name = None
    assert s.has_unsaved_changes() is False
    del s._tabs_by_name
    assert s.has_unsaved_changes() is False


def test_save_tab():
    s = new_ui()
    s.save_tab("missing")
    t = Tab(a=1)
    s._tabs_by_name = {"T": t}
    s._snapshot_tab_fields(t)
    t.ui_elements["a"].value = 2
    t.dirty = True
    s.save_tab("T")  # tab stays dirty: baseline kept
    assert t.calls == ["save"] and t._field_snapshot == {"a": 1}
    t.dirty = False
    s.save_tab("T")
    assert t._field_snapshot == {"a": 2}


# --------------------------------------------------------------------------- tab memory / jump select

def test_remember_settings_tab(monkeypatch):
    from modules import mainuiwindow

    seen = []
    monkeypatch.setattr(mainuiwindow, "remember_ui_pref", lambda k, v: seen.append((k, v)))
    s = new_ui()
    s._remember_settings_tab("")
    s._remember_settings_tab("OBS")
    assert seen == [("last_settings_tab", "OBS")]

    def boom(k, v):
        raise RuntimeError("x")

    monkeypatch.setattr(mainuiwindow, "remember_ui_pref", boom)
    s._remember_settings_tab("PSN")  # swallowed


def test_sync_settings_jump():
    s = new_ui()
    s._sync_settings_jump("OBS")  # no select yet
    assigned = []

    class Jump:
        def __init__(self):
            self._v = "Twitch"

        @property
        def value(self):
            return self._v

        @value.setter
        def value(self, v):
            assigned.append((v, s._jump_sync))
            self._v = v

    s._settings_jump = Jump()
    s._sync_settings_jump("")
    s._sync_settings_jump("Twitch")
    s._sync_settings_jump("OBS")
    assert assigned == [("OBS", True)] and s._jump_sync is False


def test_on_settings_jump():
    s = new_ui()
    tabs = SimpleNamespace(value="Twitch")
    s._jump_sync = True
    s._on_settings_jump(tabs, "OBS")
    s._jump_sync = False
    s._on_settings_jump(tabs, "")
    s._on_settings_jump(tabs, "Twitch")  # already active
    assert tabs.value == "Twitch"
    s._on_settings_jump(tabs, "OBS")
    assert tabs.value == "OBS"


def test_complete_settings_tab_switch(monkeypatch):
    s = new_ui()
    remembered = []
    monkeypatch.setattr(s, "_remember_settings_tab", remembered.append)
    a, b = Tab(), Tab(x=1)
    b.dirty = True
    s._tabs_by_name = {"A": a, "B": b}
    seen_guard = []

    class Tabs:
        @property
        def value(self):
            return None

        @value.setter
        def value(self, v):
            seen_guard.append((v, s._settings_switch_guard))

    s._complete_settings_tab_switch(Tabs(), "A", "B")
    assert a.calls == ["exit"] and b.calls == ["enter"] and b.dirty is False and b._field_snapshot == {"x": 1}
    assert s._active_tab_name == "B" and s._accepted_settings_tab == "B"
    assert seen_guard == [("B", True)] and s._settings_switch_guard is False and remembered == ["B"]
    s._complete_settings_tab_switch(Tabs(), "missing", "About")
    assert s._active_tab_name == "About"


# --------------------------------------------------------------------------- background preload / pause

def test_load_next_unloaded_settings_tab_not_built():
    s = new_ui()
    assert s.load_next_unloaded_settings_tab() is False
    s._load_settings_tab_content = lambda n: None
    s._settings_preload_order = []
    s._settings_loaded_tabs = set()
    assert s.load_next_unloaded_settings_tab() is False


def test_load_next_unloaded_settings_tab_walks_order(monkeypatch):
    s = new_ui()
    loaded = {"A"}
    s._settings_loaded_tabs = loaded
    s._settings_preload_order = ["A", "B", "C"]
    paused = []
    monkeypatch.setattr(s, "_pause_preloaded_tab", paused.append)

    def loader(name):
        if name == "C":
            loaded.add(name)

    s._load_settings_tab_content = loader
    assert s.load_next_unloaded_settings_tab() is True  # B fails
    assert s._settings_preload_failed == {"B"} and paused == []
    assert s.load_next_unloaded_settings_tab() is True  # C loads
    assert paused == ["C"]
    assert s.load_next_unloaded_settings_tab() is False


def test_pause_preloaded_tab():
    s = new_ui()
    t = Tab()
    s._tabs_by_name = {"T": t, "Active": Tab(), "NoExit": object()}
    s._active_tab_name = "Active"
    s._pause_preloaded_tab("Active")
    s._pause_preloaded_tab("missing")
    s._pause_preloaded_tab("NoExit")
    s._pause_preloaded_tab("T")
    assert t.calls == ["exit"]
    t.on_exit = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    s._pause_preloaded_tab("T")  # swallowed


def test_pause_and_resume_background_work():
    s = new_ui()
    s.resume_background_work()  # not paused: nothing
    poll, sub = SimpleNamespace(active=True), SimpleNamespace(active=True)

    class DeadTimer:
        def __setattr__(self, k, v):
            raise RuntimeError("gone")

    s._settings_tab_poll, s._settings_subtab_poll = poll, sub
    t = Tab()
    s._tabs_by_name = {"Twitch": t}
    s.pause_background_work()
    assert poll.active is False and sub.active is False and t.calls == ["exit"]
    s.resume_background_work()
    assert poll.active is True and sub.active is True and t.calls == ["exit", "enter"]
    assert s._settings_background_paused is False
    # timers missing/dead and failing tab hooks are tolerated
    s._settings_tab_poll, s._settings_subtab_poll = None, DeadTimer()
    t.on_exit = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    t.on_enter = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    s.pause_background_work()
    s.resume_background_work()
    s._tabs_by_name = None
    s.pause_background_work()
    s.resume_background_work()


def test_create_settings_tab_uses_singleton(monkeypatch):
    seen = []
    monkeypatch.setattr(st.settings_ui, "build_ui_v2", lambda: seen.append(1) or "built")
    assert st.create_settings_tab() == "built" and seen == [1]
    assert isinstance(st.settings_ui, st.SettingsUI)


# --------------------------------------------------------------------------- release notes formatting

def test_format_release_notes():
    s = new_ui()
    notes = "# Title\n## Sub\n### Small\n**bold** __b2__ *it* _i2_\n`code` ```block```\n[link](http://x)\n- one\n* two\nafter\n- tail"
    out = s._format_release_notes(notes)
    assert "<h2>Title</h2>" in out and "<h3>Sub</h3>" in out and "<h4>Small</h4>" in out
    assert "<strong>bold</strong>" in out and "<strong>b2</strong>" in out
    assert "<em>it</em>" in out and "<em>i2</em>" in out
    assert '<a href="http://x" target="_blank">link</a>' in out
    assert "<ul><br><li>one</li><br><li>two</li><br></ul><br>after" in out
    assert out.endswith("<li>tail</li><br></ul>")
    assert "\n" not in out


def test_format_release_notes_escapes_html():
    out = new_ui()._format_release_notes("<script>x</script>")
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_format_release_notes_failure_returns_original(monkeypatch):
    import html

    def boom(s):
        raise RuntimeError("x")

    monkeypatch.setattr(html, "escape", boom)
    assert new_ui()._format_release_notes("raw") == "raw"


# --------------------------------------------------------------------------- GitHub releases (faked aiohttp)

class FakeResponse:
    def __init__(self, status, data=None):
        self.status = status
        self.data = data

    async def json(self):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.urls = response, exc, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url):
        self.urls.append(url)
        if self.exc:
            raise self.exc
        return self.response


def _session(monkeypatch, **kw):
    sess = FakeSession(**kw)
    monkeypatch.setattr(st.aiohttp, "ClientSession", lambda: sess)
    return sess


def test_fetch_releases_parses_and_skips(monkeypatch):
    data = [
        {"draft": True, "tag_name": "v9"},
        {"tag_name": "v1.2.3", "published_at": "2026-01-02T03:04:05Z", "body": "notes", "html_url": "u",
         "prerelease": True},
        {"tag_name": "1.0", "published_at": "garbageTdate"},
        {"tag_name": "", "published_at": ""},
        {"tag_name": "v0.9"},
    ]
    sess = _session(monkeypatch, response=FakeResponse(200, data))
    out = asyncio.run(new_ui().fetch_all_releases_from_github())
    assert sess.urls == ["https://api.github.com/repos/mushroomsuprise/mycelian/releases"]
    assert out[0] == {"version": "1.2.3", "date": "January 02, 2026", "notes": "notes", "url": "u", "prerelease": True}
    assert out[1]["date"] == "garbage" and out[1]["notes"] == "No release notes available."
    assert out[2] == {"version": "0.9", "date": "", "notes": "No release notes available.", "url": "",
                      "prerelease": False}
    assert len(out) == 3


def test_fetch_releases_404(monkeypatch):
    _session(monkeypatch, response=FakeResponse(404))
    assert asyncio.run(new_ui().fetch_all_releases_from_github()) == []


def test_fetch_releases_http_error(monkeypatch):
    _session(monkeypatch, response=FakeResponse(503))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        asyncio.run(new_ui().fetch_all_releases_from_github())


def test_fetch_releases_network_error(monkeypatch):
    _session(monkeypatch, exc=aiohttp.ClientConnectionError("down"))
    with pytest.raises(RuntimeError, match="Network error reaching"):
        asyncio.run(new_ui().fetch_all_releases_from_github())


# --------------------------------------------------------------------------- changelog modal flow

class FakeWork:
    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append(("close",))

    def fail(self, title, **kw):
        self.calls.append(("fail", title, kw))

    def done(self, title, **kw):
        self.calls.append(("done", title, kw))


class SyncThread:
    started = []

    def __init__(self, target=None, **kw):
        self.target = target
        self.daemon = False

    def start(self):
        SyncThread.started.append(self)
        self.target()


@pytest.fixture
def changelog(monkeypatch):
    work = FakeWork()
    monkeypatch.setattr(st, "notify_progress", lambda title, source: work)
    SyncThread.started = []
    monkeypatch.setattr(threading, "Thread", SyncThread)
    timers = []

    class Timer:
        def __init__(self, cb):
            self.cb, self.cancelled = cb, False

        def cancel(self):
            self.cancelled = True

    def sched(interval, cb, **kw):
        t = Timer(cb)
        timers.append(t)
        return t

    monkeypatch.setattr(st, "layout_schedule", sched)
    return SimpleNamespace(work=work, timers=timers)


async def _releases(*_a):
    return [{"version": "1"}]


def test_changelog_opens_modal(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(s, "fetch_all_releases_from_github", lambda: _releases())
    opened = []
    monkeypatch.setattr(s, "_create_changelog_modal", lambda r: opened.append(r) or True)
    s.show_changelog_modal()
    (timer,) = changelog.timers
    assert SyncThread.started[0].daemon is True
    timer.cb()
    assert opened == [[{"version": "1"}]] and changelog.work.calls == [("close",)]
    assert timer.cancelled and s._changelog_loading is False


def test_changelog_modal_failure(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(s, "fetch_all_releases_from_github", lambda: _releases())
    monkeypatch.setattr(s, "_create_changelog_modal", lambda r: False)
    s.show_changelog_modal()
    changelog.timers[0].cb()
    assert changelog.work.calls[0][:2] == ("fail", "Couldn't open the changelog")


def test_changelog_no_releases(changelog, monkeypatch):
    s = new_ui()

    async def none():
        return []

    monkeypatch.setattr(s, "fetch_all_releases_from_github", none)
    s.show_changelog_modal()
    changelog.timers[0].cb()
    assert changelog.work.calls == [("done", "No releases published yet", {"type": "info"})]


def test_changelog_fetch_error(changelog, monkeypatch):
    s = new_ui()

    async def boom():
        raise RuntimeError("rate limited")

    monkeypatch.setattr(s, "fetch_all_releases_from_github", boom)
    s.show_changelog_modal()
    changelog.timers[0].cb()
    kind, title, kw = changelog.work.calls[0]
    assert title == "Couldn't load the changelog from GitHub" and kw["details"] == "rate limited"


def test_changelog_still_loading_keeps_timer_then_ignores_repeats(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(threading, "Thread", lambda target=None: SimpleNamespace(daemon=False, start=lambda: None))
    s.show_changelog_modal()
    timer = changelog.timers[0]
    timer.cb()
    assert not timer.cancelled and changelog.work.calls == []
    # a second click while loading is ignored
    s.show_changelog_modal()
    assert len(changelog.timers) == 1


def test_changelog_processed_result_stops(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(s, "fetch_all_releases_from_github", lambda: _releases())
    calls = []
    monkeypatch.setattr(s, "_create_changelog_modal", lambda r: calls.append(1) or True)
    s.show_changelog_modal()
    t = changelog.timers[0]
    t.cb()
    t.cb()  # already processed: no second modal
    assert calls == [1]


def test_changelog_check_error_fails_work(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(s, "fetch_all_releases_from_github", lambda: _releases())

    def boom(r):
        raise ValueError("bad data")

    monkeypatch.setattr(s, "_create_changelog_modal", boom)
    s.show_changelog_modal()
    changelog.timers[0].cb()
    kind, title, kw = changelog.work.calls[-1]
    assert kind == "fail" and kw["details"] == "bad data" and s._changelog_loading is False


def test_changelog_none_releases_counts_as_empty(changelog, monkeypatch):
    s = new_ui()

    async def weird():
        return None

    monkeypatch.setattr(s, "fetch_all_releases_from_github", weird)
    monkeypatch.setattr(s, "_create_changelog_modal", lambda r: pytest.fail("no releases"))
    s.show_changelog_modal()
    changelog.timers[0].cb()
    assert changelog.work.calls[0][0] == "done"


def test_changelog_setup_failure_with_and_without_work(monkeypatch, notes):
    s = new_ui()
    work = FakeWork()
    monkeypatch.setattr(st, "notify_progress", lambda title, source: work)

    def boom(*a, **k):
        raise RuntimeError("no threads")

    monkeypatch.setattr(threading, "Thread", boom)
    s.show_changelog_modal()
    assert work.calls[0][:2] == ("fail", "Couldn't open the changelog") and s._changelog_loading is False

    def boom2(title, source):
        raise RuntimeError("no notices")

    monkeypatch.setattr(st, "notify_progress", boom2)
    s.show_changelog_modal()
    assert notes[0][1]["title"] == "Couldn't open the changelog"


def test_changelog_thread_closes_loop_even_if_close_fails(changelog, monkeypatch):
    s = new_ui()
    monkeypatch.setattr(s, "fetch_all_releases_from_github", lambda: _releases())

    real_new_loop = asyncio.new_event_loop

    class Loop:
        def __init__(self):
            self.inner = real_new_loop()

        def run_until_complete(self, coro):
            return self.inner.run_until_complete(coro)

        def close(self):
            self.inner.close()
            raise RuntimeError("close failed")

    monkeypatch.setattr(st.asyncio, "new_event_loop", Loop)
    monkeypatch.setattr(st.asyncio, "set_event_loop", lambda loop: None)
    monkeypatch.setattr(s, "_create_changelog_modal", lambda r: True)
    s.show_changelog_modal()
    changelog.timers[0].cb()
    assert changelog.work.calls == [("close",)]


def test_create_changelog_modal_renders(monkeypatch):
    opened = []
    monkeypatch.setattr(st.webbrowser, "open", opened.append)
    releases = [
        {"version": "2.0", "prerelease": True, "date": "Jan 1", "url": "https://gh/r/2", "notes": "**new**"},
        {"version": "1.0", "notes": "   "},
    ]
    s = new_ui()
    result = {}

    def build():
        result["ok"] = s._create_changelog_modal(releases)

    async def check(user):
        assert result["ok"] is True
        await user.should_see("Mycelian Changelog")
        await user.should_see("Version 2.0 (Pre-release)")
        await user.should_see("Released Jan 1")
        await user.should_see("Version 1.0")
        await user.should_see("No release notes available.")
        user.find("View on GitHub").click()
        assert opened == ["https://gh/r/2"]

    run_ui(build, check)


def test_create_changelog_modal_empty_list_and_failure(monkeypatch):
    s = new_ui()
    result = {}

    def build():
        result["empty"] = s._create_changelog_modal([])
        result["bad"] = s._create_changelog_modal([{"no_version": 1}])

    async def check(user):
        await user.should_see("No releases found.")
        assert result == {"empty": True, "bad": False}

    run_ui(build, check)

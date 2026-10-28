# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/spotify_tab.py (Settings → Spotify).

State, credentials, the spotify service, notices and timers are faked; the OAuth worker
thread runs synchronously, so nothing opens a browser or reaches Spotify.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import dataobjects, spotify  # noqa: E402
from modules.dataobjects import SpotifyData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import spotify_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self):
        self.data = SpotifyData()
        self.updates: List[tuple] = []
        self.save_result = True
        self.saves = 0
        self.get_error = None

    def get_spotify_data(self):
        if self.get_error:
            raise self.get_error
        return self.data

    def update_spotify_field(self, field, value):
        self.updates.append((field, value))
        setattr(self.data, field, value)
        return True

    def save_changes(self):
        self.saves += 1
        return self.save_result


class FakeCreds:
    def __init__(self):
        self.creds = {"client_id": "", "client_secret": ""}
        self.updated: List[dict] = []

    def get_spotify_credentials(self):
        return dict(self.creds)

    def update_spotify_credentials(self, **kw):
        self.updated.append(kw)


class FakeWork:
    def __init__(self, title, **kw):
        self.title = title
        self.kw = kw
        self.finished = False
        self.result = None

    def done(self, title, message=None, **kw):
        self.finished = True
        self.result = ("done", title, message, kw)

    def fail(self, title, **kw):
        self.finished = True
        self.result = ("fail", title, kw)


class FakeTimer:
    def __init__(self, interval, cb, kw):
        self.interval = interval
        self.cb = cb
        self.kw = kw
        self.active = kw.get("active", True)
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeButton:
    def __init__(self, text=""):
        self.text = text
        self.enabled = True
        self.prop_calls = []
        self.value = None

    def set_text(self, t):
        self.text = t

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def props(self, *a, **k):
        self.prop_calls.append((a, k))
        return self


class Env:
    def __init__(self):
        self.state = FakeState()
        self.creds = FakeCreds()
        self.notes: List[dict] = []
        self.works: List[FakeWork] = []
        self.timers: List[FakeTimer] = []
        self.status = {"status": "Disconnected", "is_authenticated": False, "current_track": "N/A"}
        self.settings_calls: List[dict] = []
        self.oauth_result: Any = True
        self.test_result: Any = True


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    monkeypatch.setattr(tabmod, "state_manager", e.state)
    monkeypatch.setattr(dataobjects, "state_manager", e.state)
    monkeypatch.setattr(tabmod, "api_credentials_manager", e.creds)
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: e.notes.append({"message": message, **kw}))

    def progress(title, **kw):
        w = FakeWork(title, **kw)
        e.works.append(w)
        return w

    monkeypatch.setattr(tabmod, "notify_progress", progress)

    def schedule(interval, cb, **kw):
        t = FakeTimer(interval, cb, kw)
        e.timers.append(t)
        return t

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)
    monkeypatch.setattr(spotify, "get_spotify_status", lambda: dict(e.status))
    monkeypatch.setattr(spotify, "spotify_client", None)
    monkeypatch.setattr(spotify, "update_spotify_settings", lambda **kw: e.settings_calls.append(kw))

    def oauth():
        if isinstance(e.oauth_result, BaseException):
            raise e.oauth_result
        return e.oauth_result

    monkeypatch.setattr(spotify, "trigger_automatic_oauth_flow", oauth)

    def test_and_reconnect():
        if isinstance(e.test_result, BaseException):
            raise e.test_result
        return e.test_result

    monkeypatch.setattr(spotify, "test_and_reconnect", test_and_reconnect)
    return e


@pytest.fixture
def sync_threads(monkeypatch):
    class SyncThread(threading.Thread):
        def start(self):
            self.run()

    monkeypatch.setattr(threading, "Thread", SyncThread)


class Holder:
    def __init__(self):
        self.content = None
        self.visible = None
        self.sets = 0

    def set_content(self, c):
        self.content = c
        self.sets += 1

    def set_visibility(self, v):
        self.visible = v


class FakeCard:
    def __init__(self):
        self.sets = []

    def set(self, *a):
        self.sets.append(a)


class FakePage:
    def __init__(self):
        self.statuses = []
        self.active = []

    def set_status(self, pills):
        self.statuses.append(pills)

    def set_active(self, a):
        self.active.append(a)


def _tab(env, creds=None):
    if creds:
        env.creds.creds = dict(creds)
    tab = tabmod.SpotifyTab()
    tab._load_from_state()
    return tab


# --------------------------------------------------------------------------- helpers without UI


@pytest.mark.parametrize("event, expected", [
    (SimpleNamespace(value="abc"), "abc"),
    (SimpleNamespace(value=7), "7"),
    (SimpleNamespace(value=["x"], args="from-args"), "from-args"),
    (SimpleNamespace(value=None, args=("first", "second")), "first"),
    (SimpleNamespace(value=None, args=[]), ""),
    (SimpleNamespace(), ""),
])
def test_str_from_value_event(event, expected):
    assert tabmod.SpotifyTab._str_from_value_event(event) == expected


def test_describe_states(env):
    tab = tabmod.SpotifyTab()
    assert tab._describe("whatever", True) == ("ok", "Connected", "Connected · now playing is available", "ok",
                                               "Connected")
    assert tab._describe("Opening Browser...", False)[0] == "busy"
    tab._oauth_in_progress = True
    assert tab._describe("Disconnected", False) == ("busy", "Signing in…", "Disconnected", "busy", "Signing in")
    tab._oauth_in_progress = False
    assert tab._describe("Authorization Timeout", False) == ("warn", "Not connected", "Authorization Timeout",
                                                             "warn", "Authorization Timeout")
    assert tab._describe("OAuth Failed", False)[4] == "Connection error"
    assert tab._describe("Token Refresh Error", False)[0] == "error"
    assert tab._describe("Disconnected", False)[2] == "Add your app's Client ID and secret, then connect"
    tab._saved_creds = {"client_id": "a", "client_secret": "b"}
    assert tab._describe("Disconnected", False)[2] == "Click Connect and approve Mycelian in your browser"
    tab._saved_creds = None
    assert tab._describe("Disconnected", False)[0] == "off"


def test_load_set_and_set_cred(env):
    env.state.data = SpotifyData(market_country="GB", access_token="tok")
    tab = _tab(env, {"client_id": "id", "client_secret": "sec"})
    assert tab.buffer.market_country == "GB" and tab.buffer is not env.state.data
    assert tab._creds == {"client_id": "id", "client_secret": "sec"} and tab._saved_creds == tab._creds
    assert tab._creds is not tab._saved_creds
    tab._set("market_country", "GB")
    assert tab.dirty is False
    tab._set("market_country", "US")
    assert tab.dirty is True and tab.buffer.market_country == "US"
    tab.dirty = False
    tab._set_cred("client_id", "id")
    assert tab.dirty is False
    tab._set_cred("client_id", "id2")
    assert tab.dirty is True and tab._creds["client_id"] == "id2"


def test_on_enter_and_exit(env):
    tab = tabmod.SpotifyTab()
    tab.on_enter()  # nothing built yet
    tab.on_exit()
    assert env.timers[0].interval == 0.05 and env.timers[0].kw == {"once": True}
    assert env.timers[0].cb == tab._refresh_status
    tab._status_timer = SimpleNamespace(active=False)
    tab._page = FakePage()
    tab.on_enter()
    assert tab._status_timer.active is True and tab._page.active == [True]
    tab.on_exit()
    assert tab._status_timer.active is False and tab._page.active == [True, False]


def test_refresh_saved_tag(env):
    tab = tabmod.SpotifyTab()
    tab._refresh_saved_tag()  # no fold
    got = []
    tab._fold = SimpleNamespace(set_saved=got.append)
    tab._saved_creds = {"client_id": "a", "client_secret": ""}
    tab._refresh_saved_tag()
    tab._saved_creds = {"client_id": "a", "client_secret": "b"}
    tab._refresh_saved_tag()
    assert got == [False, True]


# --------------------------------------------------------------------------- now playing strip


def _client_with(**data):
    return SimpleNamespace(spotify_data=SimpleNamespace(**data))


def test_set_track_without_widget(env):
    tab = tabmod.SpotifyTab()
    tab._set_track({}, True)  # no widget: nothing happens


def test_set_track_shows_track(env, monkeypatch):
    tab = tabmod.SpotifyTab()
    tab._track, tab._track_box = Holder(), Holder()
    monkeypatch.setattr(spotify, "spotify_client",
                        _client_with(track_name=" Song ", artist_name="Artist", album_name="Nothing playing"))
    tab._set_track({"current_track": "Artist - Song"}, True)
    assert tab._track_box.visible is True
    assert tabmod.media_html("music_note", "Song", "Artist") == tab._track.content
    tab._set_track({"current_track": "Artist - Song"}, True)
    assert tab._track.sets == 1  # unchanged content is not re-sent

    monkeypatch.setattr(spotify, "spotify_client", _client_with(track_name="Song", artist_name="A", album_name="B"))
    tab._set_track({"current_track": "A - Song"}, True)
    assert "A · B" in tab._track.content


@pytest.mark.parametrize("client, info, connected, text", [
    (None, {"current_track": "x"}, True, "Spotify connected (playback may be restricted by region)"),
    ("nothing", {"current_track": "x"}, False, "Not connected"),
    ("song", {"current_track": "N/A"}, True, "Spotify connected (playback may be restricted by region)"),
    ("song", {"current_track": None}, False, "Not connected"),
    ("song", {}, False, "Not connected"),
])
def test_set_track_fallbacks(env, monkeypatch, client, info, connected, text):
    clients = {None: None, "nothing": _client_with(track_name="Nothing playing", artist_name="", album_name=""),
               "song": _client_with(track_name="Song", artist_name="", album_name="")}
    monkeypatch.setattr(spotify, "spotify_client", clients[client])
    tab = tabmod.SpotifyTab()
    tab._track = Holder()
    tab._set_track(info, connected)
    assert tab._track.content == tabmod.media_html("music_off", text, muted=True)


def test_set_track_client_error(env, monkeypatch):
    class Bad:
        @property
        def spotify_data(self):
            raise RuntimeError("gone")

    monkeypatch.setattr(spotify, "spotify_client", Bad())
    tab = tabmod.SpotifyTab()
    tab._track = Holder()
    tab._set_track({"current_track": "x"}, False)
    assert "Not connected" in tab._track.content


# --------------------------------------------------------------------------- status refresh


def _wired(env):
    tab = tabmod.SpotifyTab()
    tab._card, tab._page = FakeCard(), FakePage()
    tab._track, tab._track_box = Holder(), Holder()
    tab.ui_elements["connect_button"] = FakeButton("Connect")
    return tab


def test_refresh_status_connected_via_state(env):
    tab = _wired(env)
    env.state.data.connection_status = "Connected"
    tab._refresh_status()
    assert tab._card.sets[-1] == ("Connected", "ok", "Connected · now playing is available")
    assert tab._page.statuses[-1] == [("ok", "Connected")]
    assert tab.ui_elements["connect_button"].text == "Reconnect"
    assert tab._track_box.visible is False  # only is_authenticated shows the strip


def test_refresh_status_authenticated_and_fallback_status(env):
    tab = _wired(env)
    env.state.data.connection_status = ""
    env.status = {"status": "Token Refresh Failed", "is_authenticated": False, "current_track": "N/A"}
    tab._refresh_status()
    assert tab._card.sets[-1][1] == "error" and tab._page.statuses[-1] == [("error", "Connection error")]
    assert tab.ui_elements["connect_button"].text == "Connect"

    env.status = {"status": "x", "is_authenticated": True, "current_track": "A - B"}
    tab._refresh_status()
    assert tab._card.sets[-1][1] == "ok" and tab._track_box.visible is True


def test_refresh_status_state_none_and_oauth_in_progress(env, monkeypatch):
    tab = _wired(env)
    env.state.data = None
    tab._oauth_in_progress = True
    tab._refresh_status()
    assert tab._card.sets[-1][1] == "busy"
    assert tab.ui_elements["connect_button"].text == "Connect"  # untouched while signing in


def test_refresh_status_without_widgets_and_on_error(env):
    tab = tabmod.SpotifyTab()
    tab._refresh_status()  # nothing built: no error
    env.state.get_error = RuntimeError("db")
    tab._refresh_status()
    tab = _wired(env)
    tab._refresh_status()
    assert tab._card.sets[-1] == ("Not connected", "error", "Status Error")
    assert tab._page.statuses[-1] == [("error", "Status error")]


# --------------------------------------------------------------------------- save / discard / persist


def test_save_writes_everything(env):
    env.state.data = SpotifyData(market_country="")
    tab = _tab(env, {"client_id": "id", "client_secret": "old"})
    fold = []
    tab._fold = SimpleNamespace(set_saved=fold.append)
    tab._set_cred("client_secret", "new")
    tab._set("market_country", "US")
    tab.save()
    assert ("client_id", "id") in env.state.updates and ("client_secret", "new") in env.state.updates
    assert ("market_country", "US") in env.state.updates
    written = {f for f, _ in env.state.updates}
    assert written == set(SpotifyData.__dataclass_fields__)
    assert env.creds.updated == [{"client_id": "id", "client_secret": "new"}]
    assert tab._saved_creds == {"client_id": "id", "client_secret": "new"} and fold == [True]
    assert env.notes[-1]["title"] == "Spotify settings saved" and tab.dirty is False


def test_save_failure_keeps_dirty_and_no_buffer(env):
    tab = tabmod.SpotifyTab()
    tab.save()  # no buffer -> nothing
    assert env.state.updates == []
    tab = _tab(env)
    tab.dirty = True
    env.state.save_result = False
    tab.save()
    assert tab.dirty is True and env.notes == []


def test_persist_credentials_without_buffer(env):
    tab = tabmod.SpotifyTab()
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab._persist_credentials_to_state()
    assert env.state.updates == [("client_id", "a"), ("client_secret", "b")]


def test_save_settings_only(env, monkeypatch):
    tab = _tab(env, {"client_id": "a", "client_secret": "b"})
    assert tab._save_settings_only() is True
    env.state.save_result = False
    assert tab._save_settings_only() is False
    monkeypatch.setattr(env.creds, "update_spotify_credentials", lambda **kw: (_ for _ in ()).throw(OSError("ro")))
    assert tab._save_settings_only() is False
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't save your Spotify Client ID and Secret"
    assert note["actions"] == tabmod.nav_actions_settings("Database") and "OSError" in note["details"]


@pytest.mark.xfail(strict=True, reason="BUG: SpotifyTab.save() writes every field of the buffer loaded at build "
                                       "time, so saving after Connect overwrites the new OAuth tokens with the "
                                       "stale (empty) ones and signs the user out")
def test_save_after_oauth_keeps_new_tokens(env):
    tab = _tab(env, {"client_id": "id", "client_secret": "sec"})
    # OAuth completes in the background: the spotify client writes fresh tokens to state.
    env.state.update_spotify_field("access_token", "NEW_AT")
    env.state.update_spotify_field("refresh_token", "NEW_RT")
    tab._set("market_country", "US")
    tab.save()
    assert env.state.data.refresh_token == "NEW_RT"


# --------------------------------------------------------------------------- OAuth connect


def test_connect_requires_credentials(env):
    tab = _tab(env)
    tab._handle_oauth_connection()
    assert env.notes[-1]["title"] == "Enter your Spotify Client ID and Client Secret first"
    assert env.works == []


def test_connect_ignored_while_in_progress(env):
    tab = _tab(env, {"client_id": "a", "client_secret": "b"})
    tab._oauth_in_progress = True
    tab._handle_oauth_connection()
    assert env.works == [] and env.notes == []


def _start_connect(env, tab):
    tab._handle_oauth_connection()
    timer = env.timers[-1]
    assert timer.interval == 0.2
    return timer


def test_connect_success_flow(env, sync_threads):
    tab = _wired(env)
    tab._load_from_state()
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    btn = tab.ui_elements["connect_button"]
    assert btn.text == "Connecting..." and btn.enabled is False
    assert tab._oauth_in_progress is True and tab._active_timers == [timer]
    assert env.settings_calls == [{"client_id": "a", "client_secret": "b"}]
    assert env.creds.updated == [{"client_id": "a", "client_secret": "b"}]
    work = env.works[0]
    assert work.title == "Connecting to Spotify" and work.kw["source"] == "spotify"

    timer.cb()
    assert work.result[:2] == ("done", "Connected to Spotify")
    assert tab._oauth_in_progress is False and timer.active is False and timer.cancelled is True
    assert btn.enabled is True and tab._oauth_timer is None and tab._active_timers == []
    timer.cb()  # already reported: just cleans up again
    assert work.result[:2] == ("done", "Connected to Spotify")


@pytest.mark.parametrize("status, title, details", [
    ("Authorization Timeout", "Spotify sign-in timed out", "Authorization Timeout"),
    ("OAuth Failed", "Spotify sign-in didn't complete", "OAuth Failed"),
    ("", "Spotify sign-in didn't complete", None),
])
def test_connect_failure_flow(env, sync_threads, status, title, details):
    env.oauth_result = False
    tab = _wired(env)
    tab._load_from_state()
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    env.state.data.connection_status = status
    timer.cb()
    kind, got_title, kw = env.works[0].result
    assert kind == "fail" and got_title == title and kw["details"] == details


def test_connect_failure_status_read_error(env, sync_threads):
    env.oauth_result = False
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    env.state.get_error = RuntimeError("db")
    timer.cb()
    assert env.works[0].result[1] == "Spotify sign-in didn't complete"


def test_connect_result_ui_error_is_contained(env, sync_threads, monkeypatch):
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    monkeypatch.setattr(env.works[0], "done", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("toast")))
    timer.cb()
    assert tab._oauth_in_progress is False


def test_connect_worker_error(env, sync_threads):
    env.oauth_result = RuntimeError("server port busy")
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    timer.cb()
    kind, title, kw = env.works[0].result
    assert kind == "fail" and kw["details"] == "server port busy"


def test_connect_worker_error_report_fails(env, sync_threads, monkeypatch):
    env.oauth_result = RuntimeError("x")
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    monkeypatch.setattr(env.works[0], "fail", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("toast")))
    timer.cb()
    assert tab._oauth_in_progress is False


def test_connect_still_waiting_then_times_out(env, monkeypatch):
    started = []

    class IdleThread(threading.Thread):
        def start(self):
            started.append(self)  # never runs: result stays "connecting"

    monkeypatch.setattr(threading, "Thread", IdleThread)
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    assert started and started[0].daemon is True
    for _ in range(1649):
        timer.cb()
    assert env.works[0].finished is False and tab._oauth_in_progress is True
    timer.cb()
    kind, title, message, kw = env.works[0].result
    assert (kind, title, kw) == ("done", "Still waiting for Spotify sign-in", {"type": "warning"})
    assert tab._oauth_in_progress is False


def test_connect_unknown_result_status_cleans_up(env, monkeypatch):
    holder = {}

    class CaptureThread(threading.Thread):
        def start(self):
            holder["target"] = self._target

    monkeypatch.setattr(threading, "Thread", CaptureThread)
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    timer = _start_connect(env, tab)
    # Grab the shared result dict through the worker's closure and give it an odd status.
    cells = {c.cell_contents.__class__: c.cell_contents for c in holder["target"].__closure__
             if isinstance(c.cell_contents, dict)}
    cells[dict]["status"] = "weird"
    timer.cb()
    assert tab._oauth_in_progress is False and env.works[0].finished is False


def test_connect_start_error_with_and_without_work(env, monkeypatch):
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    monkeypatch.setattr(tab, "_save_settings_only", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    tab._handle_oauth_connection()
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't start Spotify sign-in"
    assert "RuntimeError: boom" in note["details"] and tab._oauth_in_progress is False

    monkeypatch.setattr(tab, "_save_settings_only", lambda: True)
    monkeypatch.setattr(tabmod, "layout_schedule", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no slot")))
    with_threads = []

    class NoThread(threading.Thread):
        def start(self):
            with_threads.append(self)

    monkeypatch.setattr(threading, "Thread", NoThread)
    tab._handle_oauth_connection()
    assert env.works[-1].result[0] == "fail" and env.works[-1].result[1] == "Couldn't start Spotify sign-in"


def test_cleanup_oauth_tolerates_bad_timers(env):
    class BadTimer:
        def __setattr__(self, k, v):
            raise RuntimeError("deleted")

        def cancel(self):
            raise RuntimeError("deleted")

    tab = _wired(env)
    tab._oauth_timer = BadTimer()
    tab._active_timers = [BadTimer()]
    tab._oauth_in_progress = True
    tab._cleanup_oauth()
    assert tab._oauth_in_progress is False and tab._oauth_timer is None

    tab.ui_elements["connect_button"] = SimpleNamespace(enable=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    tab._oauth_in_progress = True
    tab._cleanup_oauth()  # error logged
    assert tab._oauth_in_progress is False


# --------------------------------------------------------------------------- test connection


async def _run_inline(fn):
    return fn()


def test_test_connection_paths(env, monkeypatch):
    import asyncio

    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=_run_inline))
    tab = _wired(env)
    tab.ui_elements["test_button"] = FakeButton("Test")
    tab._creds = {"client_id": "a", "client_secret": "b"}

    asyncio.run(tab._test_connection())
    assert env.works[-1].title == "Testing the Spotify connection"
    assert env.works[-1].result[:2] == ("done", "Connected to Spotify")
    assert tab.ui_elements["test_button"].text == "Test" and tab.ui_elements["test_button"].enabled is True
    assert tab._test_in_progress is False

    env.test_result = False
    asyncio.run(tab._test_connection())
    assert env.works[-1].result[1] == "Couldn't reach Spotify with the saved sign-in"

    env.test_result = ValueError("bad")
    asyncio.run(tab._test_connection())
    kind, title, kw = env.works[-1].result
    assert title == "Couldn't test the Spotify connection" and kw["details"] == "ValueError: bad"

    tab._test_in_progress = True
    n = len(env.works)
    asyncio.run(tab._test_connection())
    assert len(env.works) == n


def test_test_connection_outer_error(env, monkeypatch):
    import asyncio

    tab = _wired(env)
    tab.ui_elements["test_button"] = SimpleNamespace(
        set_text=lambda t: (_ for _ in ()).throw(RuntimeError("deleted")),
        enable=lambda: None, disable=lambda: None)
    asyncio.run(tab._test_connection())
    assert env.notes[-1]["title"] == "Couldn't run the Spotify test"
    assert tab._test_in_progress is False


def test_cleanup_test_without_button_and_error(env):
    tab = tabmod.SpotifyTab()
    tab._test_in_progress = True
    tab._cleanup_test()
    assert tab._test_in_progress is False
    tab.ui_elements["test_button"] = SimpleNamespace(set_text=lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    tab._cleanup_test()  # logged


# --------------------------------------------------------------------------- the built page


def test_build_renders_and_edits(env):
    env.state.data = SpotifyData(market_country="JP", connection_status="Disconnected")
    holder = {}

    def build():
        tab = tabmod.SpotifyTab()
        holder["tab"] = tab
        tab.build(ui.column())

    async def check(user):
        tab = holder["tab"]
        await user.should_see("Now playing for overlays and chat commands")
        await user.should_see("Market country")
        assert "Client ID &amp; secret" in tab._fold._head.content
        assert tab._fold.expansion.value is True  # nothing saved yet -> opened
        assert tab.ui_elements["market_country"].options["JP"] == "JP"
        assert env.timers[0].interval == 5.0 and env.timers[0].kw == {"active": True}
        assert tab._page._status_html and "Not connected" in tab._page._status_html

        tab.ui_elements["client_id"].set_value("my-id")
        tab.ui_elements["client_secret"].set_value("my-secret")
        assert tab._creds == {"client_id": "my-id", "client_secret": "my-secret"} and tab.dirty is True
        tab.ui_elements["market_country"].set_value("GB")
        assert tab.buffer.market_country == "GB"

        tab.discard()
        assert tab.dirty is False
        assert tab.ui_elements["client_id"].value == "" and tab.ui_elements["market_country"].value == "JP"

        tab.ui_elements["client_id"].set_value("id2")
        user.find("Save").click()
        assert env.creds.updated[-1] == {"client_id": "id2", "client_secret": ""}
        assert env.notes[-1]["title"] == "Spotify settings saved"

        n = len(env.state.updates)
        user.find("Connect").click()  # secret missing -> warning
        assert env.notes[-1]["title"] == "Enter your Spotify Client ID and Client Secret first"
        assert len(env.state.updates) == n

    run_ui(build, check)


def test_build_with_saved_credentials_folds_closed(env):
    env.creds.creds = {"client_id": "a", "client_secret": "b"}
    env.state.data = SpotifyData(market_country="US")
    holder = {}

    def build():
        holder["tab"] = tabmod.SpotifyTab()
        holder["tab"].build(ui.column())

    async def check(user):
        tab = holder["tab"]
        assert tab._fold.expansion.value is False
        assert "JP" not in tab.ui_elements["market_country"].options
        await user.should_see("Click Connect and approve Mycelian in your browser")

    run_ui(build, check)

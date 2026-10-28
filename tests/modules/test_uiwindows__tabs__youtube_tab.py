# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/youtube_tab.py (Settings → YouTube).

State, credentials, the youtube service, notices and timers are faked; the OAuth worker
thread runs synchronously, so nothing opens a browser or reaches Google.
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

import asyncio  # noqa: E402
import copy  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import youtube  # noqa: E402
from modules.dataobjects import YouTubeData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import youtube_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self):
        self.data = YouTubeData()
        self.updates: List[tuple] = []
        self.set_calls: List[dict] = []
        self.save_result = True
        self.saves = 0
        self.get_error = None

    def get_youtube_data(self):
        if self.get_error:
            raise self.get_error
        return copy.deepcopy(self.data)

    def update_youtube_field(self, field, value):
        self.updates.append((field, value))
        setattr(self.data, field, value)

    def set_youtube_data(self, d):
        self.set_calls.append(d)
        for k, v in d.items():
            setattr(self.data, k, v)

    def save_changes(self):
        self.saves += 1
        return self.save_result


class FakeCreds:
    def __init__(self):
        self.creds = {"client_id": "", "client_secret": ""}
        self.updated: List[dict] = []

    def get_youtube_credentials(self):
        return dict(self.creds)

    def update_youtube_credentials(self, **kw):
        self.updated.append(kw)
        self.creds.update(kw)


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
        self.interval, self.cb, self.kw = interval, cb, kw
        self.active = kw.get("active", True)


class FakeButton:
    def __init__(self, text=""):
        self.text = text
        self.enabled = True
        self.visible = True

    def set_text(self, t):
        self.text = t

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def props(self, *a, **k):
        return self

    def set_visibility(self, v):
        self.visible = v


class Env:
    def __init__(self):
        self.state = FakeState()
        self.creds = FakeCreds()
        self.notes: List[dict] = []
        self.works: List[FakeWork] = []
        self.timers: List[FakeTimer] = []
        self.status = {"status": "Disconnected", "is_connected": False, "latest_video": "N/A"}
        self.restarts = 0
        self.restart_error = None
        self.oauth_result: Any = True
        self.test_result: Any = True
        self.disconnects = 0
        self.disconnect_error = None


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    monkeypatch.setattr(tabmod, "state_manager", e.state)
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
    monkeypatch.setattr(youtube, "get_youtube_status", lambda: dict(e.status))

    def restart():
        if e.restart_error:
            raise e.restart_error
        e.restarts += 1

    monkeypatch.setattr(youtube, "restart_youtube_live_chat_if_needed", restart)

    def oauth():
        if isinstance(e.oauth_result, BaseException):
            raise e.oauth_result
        return e.oauth_result

    monkeypatch.setattr(youtube, "trigger_youtube_oauth_flow", oauth)

    def test_connection():
        if isinstance(e.test_result, BaseException):
            raise e.test_result
        return e.test_result

    monkeypatch.setattr(youtube, "test_connection", test_connection)

    def disconnect():
        if e.disconnect_error:
            raise e.disconnect_error
        e.disconnects += 1

    monkeypatch.setattr(youtube, "disconnect_youtube_oauth", disconnect)
    return e


@pytest.fixture
def sync_threads(monkeypatch):
    class SyncThread(threading.Thread):
        def start(self):
            self.run()

    monkeypatch.setattr(threading, "Thread", SyncThread)


class FakeCard:
    def __init__(self):
        self.sets, self.metas = [], []

    def set(self, *a, **k):
        self.sets.append((a, k))

    def set_meta(self, rows):
        self.metas.append(list(rows))


class FakePage:
    def __init__(self):
        self.statuses, self.active = [], []

    def set_status(self, pills):
        self.statuses.append(pills)

    def set_active(self, a):
        self.active.append(a)


class FakeFold:
    def __init__(self):
        self.saved = []

    def set_saved(self, v):
        self.saved.append(v)


def _tab(env):
    tab = tabmod.YouTubeTab()
    tab._load_from_state()
    return tab


def _wired(env):
    tab = _tab(env)
    tab._page, tab._live_card, tab._lookup_card = FakePage(), FakeCard(), FakeCard()
    tab._oauth_fold, tab._key_fold = FakeFold(), FakeFold()
    tab._disconnect_button = FakeButton()
    tab.ui_elements["connect_button"] = FakeButton("Connect")
    return tab


# --------------------------------------------------------------------------- small helpers


@pytest.mark.parametrize("event, expected", [
    (SimpleNamespace(value="abc"), "abc"),
    (SimpleNamespace(value=3), "3"),
    (SimpleNamespace(value=("x",), args="from-args"), "from-args"),
    (SimpleNamespace(value=None, args=["first"]), "first"),
    (SimpleNamespace(value=None, args=()), ""),
    (SimpleNamespace(), ""),
])
def test_str_from_value_event(event, expected):
    assert tabmod.YouTubeTab._str_from_value_event(event) == expected


def test_restart_live_chat(env):
    tabmod._restart_live_chat()
    assert env.restarts == 1 and env.notes == []
    env.restart_error = RuntimeError("thread dead")
    tabmod._restart_live_chat()
    note = env.notes[-1]
    assert note["type"] == "warning" and note["title"] == "YouTube settings saved, but live chat couldn't restart"
    assert note["details"] == "RuntimeError: thread dead"


def test_on_enter_and_exit(env):
    tab = tabmod.YouTubeTab()
    tab.on_enter()
    tab.on_exit()
    assert env.timers[0].interval == 0.05 and env.timers[0].kw == {"once": True}
    tab._status_timer = SimpleNamespace(active=False)
    tab._page = FakePage()
    tab.on_enter()
    assert tab._status_timer.active is True
    tab.on_exit()
    assert tab._status_timer.active is False and tab._page.active == [True, False]


def test_load_from_state_prefers_creds_file(env):
    env.state.data = YouTubeData(oauth_client_id="state-id", oauth_client_secret="state-sec",
                                 playlist_filter=["Shorts"], channels={"a": {"x": 1}})
    tab = _tab(env)
    assert tab._creds == {"client_id": "state-id", "client_secret": "state-sec"}
    assert tab._saved_playlist_filter == ["Shorts"]
    tab.buffer.channels["a"]["x"] = 2
    assert env.state.data.channels["a"]["x"] == 1  # deep copy
    env.creds.creds = {"client_id": "file-id", "client_secret": "file-sec"}
    tab._load_from_state()
    assert tab._creds == {"client_id": "file-id", "client_secret": "file-sec"}
    env.state.data = YouTubeData()
    env.creds.creds = {}
    tab._load_from_state()
    assert tab._creds == {} and tab.dirty is False


def test_set_set_cred_and_suppress(env):
    tab = _tab(env)
    tab._set("api_key", "")
    assert tab.dirty is False
    tab._set("api_key", "k")
    assert tab.dirty is True
    tab.dirty = False
    tab._suppress_dirty = True
    tab._set("api_key", "k2")
    tab._set_cred("client_id", "x")
    assert tab.dirty is False and tab.buffer.api_key == "k2" and tab._creds["client_id"] == "x"
    tab._suppress_dirty = False
    tab._set_cred("client_id", "x")
    assert tab.dirty is False
    tab._set_cred("client_id", "y")
    assert tab.dirty is True


def test_playlist_dirty_logic(env):
    tab = tabmod.YouTubeTab()
    assert tab._playlist_is_dirty() is False  # no buffer
    tab = _tab(env)
    assert tab.has_unsaved_changes() is False
    tab._playlist_input = SimpleNamespace(value="  pending ")
    assert tab._playlist_is_dirty() is True and tab.has_unsaved_changes() is True
    tab._playlist_input.value = None
    tab.buffer.playlist_filter.append("New")
    assert tab._playlist_is_dirty() is True
    tab.mark_clean()
    assert tab._playlist_is_dirty() is False and tab._saved_playlist_filter == ["New"]
    assert tab._sync_playlist_dirty() is None
    tab.dirty = True
    assert tab.has_unsaved_changes() is True


def test_mark_clean_without_buffer(env):
    tab = tabmod.YouTubeTab()
    tab.dirty = True
    tab._playlist_input = SimpleNamespace(value="x")
    tab.mark_clean()
    assert tab.dirty is False and tab._playlist_input.value == ""


# --------------------------------------------------------------------------- status computation


def _status(env, tab, status=None, **data):
    env.state.data = YouTubeData(**data)
    if status is not None:
        env.status = status
    return tab._status()


def test_status_lookups_ok_and_live(env):
    tab = tabmod.YouTubeTab()
    lookups, live, oauth, has_key, title = _status(
        env, tab,
        {"status": "x", "is_connected": True, "latest_video": "Vid", "live_chat_status": "Live",
         "oauth_channel_title": "Chan", "oauth_connected": True},
        api_key="k", channel_urls="a|b", latest_video_channel="Chan", connection_status="")
    assert lookups == ("ok", "Channel lookups working", "2 channels watched", "Working · latest video found",
                       [("Latest video", "Vid", "Chan", "")])
    assert live == ("ok", "Live chat: Live", "Chan", "Live · reading chat", [("Live status", "Live", "", "is-ok")])
    assert (oauth, has_key, title) == (True, True, "Chan")


def test_status_lookups_connected_by_state_no_video(env):
    tab = tabmod.YouTubeTab()
    lookups, live, *_ = _status(env, tab, {"latest_video": "No video found", "live_chat_status": "Offline"},
                                api_key="k", connection_status="Connected", channels={"c1": {}})
    assert lookups[:4] == ("ok", "Channel lookups working", "1 channel watched", "Working")
    assert lookups[4] == [("Latest video", "No video found", "", "")]
    assert live[:4] == ("ok", "Live chat ready", "Not signed in", "Offline · signed in, reads chat when you go live")


def test_status_no_key_and_error(env):
    tab = tabmod.YouTubeTab()
    lookups, *_ = _status(env, tab, {}, connection_status="Disconnected")
    assert lookups == ("off", "Channel lookups: no API key", "No channels configured",
                       "Add an API key to look up channels", [])
    lookups, *_ = _status(env, tab, {"latest_video": None}, api_key="k", connection_status="Quota Exceeded")
    assert lookups[0] == "error" and lookups[1] == "Channel lookups: Quota Exceeded"
    assert lookups[4] == [("Latest video", "N/A", "", "")]


def test_status_live_variants(env):
    tab = tabmod.YouTubeTab()
    _, live, *_ = _status(env, tab, {"oauth_connected": True, "live_chat_status": "Error"})
    assert live[:4] == ("warn", "Live chat: Error", "Google account", "Error")
    _, live, *_ = _status(env, tab, {}, live_chat_status="Not authorized")
    assert live == ("off", "Live chat: not set up", "Not signed in", "Add OAuth credentials, then Connect", [])
    _, live, _, _, title = _status(env, tab, {}, live_chat_status="Token refresh failed",
                                   oauth_client_id="i", oauth_client_secret="s", oauth_channel_title="Saved")
    assert live[:4] == ("error", "Live chat: Token refresh failed", "Saved", "Token refresh failed")
    assert title == "Saved"


def test_status_state_none(env, monkeypatch):
    tab = tabmod.YouTubeTab()
    monkeypatch.setattr(env.state, "get_youtube_data", lambda: None)
    env.status = {"status": "From module"}
    lookups, live, oauth, has_key, title = tab._status()
    assert lookups[1] == "Channel lookups: no API key" and has_key is False
    assert live[0] == "off" and title == ""


def test_oauth_creds_saved(env):
    assert tabmod.YouTubeTab._oauth_creds_saved(None) is False
    env.creds.creds = {"client_id": "a", "client_secret": ""}
    assert tabmod.YouTubeTab._oauth_creds_saved(YouTubeData(oauth_client_secret="s")) is True
    env.creds.creds = {"client_id": None, "client_secret": None}
    assert tabmod.YouTubeTab._oauth_creds_saved(None) is False


# --------------------------------------------------------------------------- status refresh


def test_refresh_status_updates_everything(env):
    tab = _wired(env)
    env.state.data = YouTubeData(api_key="k", oauth_client_id="i", oauth_client_secret="s")
    env.status = {"is_connected": True, "latest_video": "Vid", "oauth_connected": True,
                  "oauth_channel_title": "my chan", "live_chat_status": "Live"}
    tab._refresh_status()
    assert tab._oauth_connected is True
    assert tab._page.statuses[-1] == [("ok", "Channel lookups working"), ("ok", "Live chat: Live")]
    args, kw = tab._live_card.sets[-1]
    assert args == ("my chan", "ok", "Live · reading chat") and kw == {"initial": "M"}
    assert tab._lookup_card.sets[-1][0][1] == "ok"
    assert tab._oauth_fold.saved[-1] is True and tab._key_fold.saved[-1] is True
    assert tab._disconnect_button.visible is True
    assert tab.ui_elements["connect_button"].text == "Reconnect"


def test_refresh_status_not_signed_in_and_in_progress(env):
    tab = _wired(env)
    tab._oauth_in_progress = True
    tab.ui_elements["connect_button"].text = "Connecting..."
    tab._refresh_status()
    args, kw = tab._live_card.sets[-1]
    assert kw == {"initial": None} and tab._disconnect_button.visible is False
    assert tab.ui_elements["connect_button"].text == "Connecting..."


def test_refresh_status_bare_and_error(env):
    tab = tabmod.YouTubeTab()
    tab._refresh_status()  # nothing built
    env.state.get_error = RuntimeError("db")
    tab._refresh_status()
    tab = tabmod.YouTubeTab()
    tab._page, tab._lookup_card = FakePage(), FakeCard()
    tab._refresh_status()
    assert tab._page.statuses[-1] == [("error", "Status error")]
    assert tab._lookup_card.sets[-1] == (("Channel lookups", "error", "Status error"), {})


# --------------------------------------------------------------------------- persistence


def test_persist_credentials(env):
    tab = _tab(env)
    tab._creds = {"client_id": "id", "client_secret": "sec"}
    tab.buffer.live_chat_enabled = False
    assert tab._persist_credentials_to_state(flush=False) is True
    assert env.state.updates == [("oauth_client_id", "id"), ("oauth_client_secret", "sec"),
                                 ("live_chat_enabled", False)]
    assert env.creds.updated == [{"client_id": "id", "client_secret": "sec"}]
    assert env.state.saves == 0 and env.restarts == 0
    assert tab._persist_credentials_to_state() is True
    assert env.state.saves == 1 and env.restarts == 1
    env.state.save_result = False
    assert tab._persist_credentials_to_state() is False
    assert env.restarts == 1


def test_persist_credentials_without_buffer(env):
    tab = tabmod.YouTubeTab()
    tab._persist_credentials_to_state(flush=False)
    assert [f for f, _ in env.state.updates] == ["oauth_client_id", "oauth_client_secret"]


def test_save(env):
    env.state.data = YouTubeData(oauth_client_id="old")
    tab = _wired(env)
    tab._creds = {"client_id": "new", "client_secret": "s"}
    tab._set("api_key", "KEY")
    tab.buffer.playlist_filter.append("Shorts")
    tab.save()
    saved = env.state.set_calls[-1]
    assert saved["oauth_client_id"] == "new" and saved["api_key"] == "KEY" and saved["playlist_filter"] == ["Shorts"]
    assert set(saved) == set(YouTubeData.__dataclass_fields__)
    assert env.notes[-1]["title"] == "YouTube settings saved" and env.restarts == 1
    assert tab.dirty is False and tab._saved_playlist_filter == ["Shorts"]
    assert tab._page.statuses  # status refreshed


def test_save_keeps_tokens_and_channels_updated_after_load(env):
    """Save writes the tab's fields onto the live state, not the stale copy loaded at build time."""
    tab = _wired(env)
    env.state.data.access_token = "NEW_AT"
    env.state.data.refresh_token = "NEW_RT"
    env.state.data.channels = {"@chan": {"channel_id": "UC1"}}
    tab._set("alerts_enabled", False)
    tab.save()
    saved = env.state.set_calls[-1]
    assert saved["access_token"] == "NEW_AT" and saved["refresh_token"] == "NEW_RT"
    assert saved["channels"] == {"@chan": {"channel_id": "UC1"}} and saved["alerts_enabled"] is False


def test_save_failure_and_no_buffer(env):
    tabmod.YouTubeTab().save()
    assert env.state.set_calls == []
    tab = _tab(env)
    tab.dirty = True
    env.state.save_result = False
    tab.save()
    assert tab.dirty is True and env.notes == [] and env.restarts == 0


# --------------------------------------------------------------------------- OAuth connect / disconnect


def test_connect_requires_credentials(env):
    tab = _wired(env)
    tab._creds = {"client_id": " ", "client_secret": "s"}
    tab._handle_oauth_connection()
    assert env.notes[-1]["title"] == "Enter your Google OAuth Client ID and Client Secret first"
    assert env.state.updates == []


def test_connect_ignored_while_in_progress_or_save_fails(env):
    tab = _wired(env)
    tab._oauth_in_progress = True
    tab._handle_oauth_connection()
    assert env.works == []
    tab._oauth_in_progress = False
    tab._creds = {"client_id": "a", "client_secret": "b"}
    env.state.save_result = False
    tab._handle_oauth_connection()
    assert env.works == [] and tab._oauth_in_progress is False


def test_connect_success(env, sync_threads):
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab.ui_elements["oauth_client_id"] = SimpleNamespace(value="")
    tab.ui_elements["oauth_client_secret"] = SimpleNamespace(value="")
    tab._handle_oauth_connection()
    btn = tab.ui_elements["connect_button"]
    assert btn.text == "Connecting..." and btn.enabled is False and tab._oauth_in_progress is True
    work = env.works[0]
    assert work.title == "Connecting YouTube live chat" and work.kw["source"] == "youtube"
    timer = env.timers[-1]
    assert timer.interval == 0.25 and tab._active_timers == [timer]
    env.status = {"oauth_connected": True, "live_chat_status": "Offline", "oauth_channel_title": "Me"}
    timer.cb()
    assert work.result[:2] == ("done", "YouTube live chat connected")
    assert tab.ui_elements["oauth_client_id"].value == "a" and tab.ui_elements["oauth_client_secret"].value == "b"
    assert tab._oauth_in_progress is False and timer.active is False and tab._active_timers == []
    assert btn.enabled is True and btn.text == "Reconnect"


def test_connect_success_without_cred_fields(env, sync_threads):
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab._handle_oauth_connection()
    env.timers[-1].cb()
    assert env.works[0].result[0] == "done"


def test_connect_not_finished_and_errors(env, sync_threads):
    env.oauth_result = False
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab._handle_oauth_connection()
    env.timers[-1].cb()
    assert env.works[-1].result[:2] == ("fail", "YouTube sign-in didn't finish")

    env.oauth_result = RuntimeError("port 9974 busy")
    tab._handle_oauth_connection()
    env.timers[-1].cb()
    kind, title, kw = env.works[-1].result
    assert kind == "fail" and kw["details"] == "port 9974 busy"
    assert tab._oauth_in_progress is False


def test_connect_still_waiting_then_unknown(env, monkeypatch):
    holder = {}

    class CaptureThread(threading.Thread):
        def start(self):
            holder["target"] = self._target

    monkeypatch.setattr(threading, "Thread", CaptureThread)
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab._handle_oauth_connection()
    timer = env.timers[-1]
    timer.cb()  # still connecting
    assert env.works[0].finished is False and tab._oauth_in_progress is True
    result = next(c.cell_contents for c in holder["target"].__closure__ if isinstance(c.cell_contents, dict))
    result["status"] = "weird"
    timer.cb()  # unknown status: nothing happens
    assert env.works[0].finished is False and tab._oauth_in_progress is True


def test_connect_start_error_paths(env, monkeypatch):
    tab = _wired(env)
    tab._creds = {"client_id": "a", "client_secret": "b"}
    monkeypatch.setattr(tab, "_persist_credentials_to_state", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    tab._handle_oauth_connection()
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't start YouTube sign-in"
    assert note["details"] == "RuntimeError: boom"

    monkeypatch.setattr(tab, "_persist_credentials_to_state", lambda: True)

    class NoThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", NoThread)
    tab._handle_oauth_connection()
    assert env.works[-1].result[:2] == ("fail", "Couldn't start YouTube sign-in")
    assert tab._oauth_in_progress is False


def test_cleanup_oauth_tolerates_bad_timers(env):
    class Bad:
        def __setattr__(self, k, v):
            raise RuntimeError("deleted")

    tab = tabmod.YouTubeTab()
    tab._active_timers = [Bad()]
    tab._oauth_in_progress = True
    tab._cleanup_oauth()  # no connect button, bad timer: fine
    assert tab._oauth_in_progress is False and tab._active_timers == []


def test_disconnect(env):
    tab = _wired(env)
    tab._handle_oauth_disconnect()
    assert env.disconnects == 1 and env.notes[-1]["title"] == "YouTube live chat disconnected"
    env.disconnect_error = RuntimeError("x")
    tab._handle_oauth_disconnect()
    assert env.notes[-1]["title"] == "Couldn't disconnect YouTube live chat"
    assert env.notes[-1]["details"] == "RuntimeError: x"


# --------------------------------------------------------------------------- test connection


async def _inline(fn):
    return fn()


def test_test_connection_paths(env, monkeypatch):
    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=_inline))
    tab = _wired(env)
    tab.ui_elements["test_button"] = FakeButton("Test")
    asyncio.run(tab._test_connection())
    assert env.works[-1].title == "Testing the YouTube connection"
    assert env.works[-1].result[:2] == ("done", "YouTube connection works")
    assert tab.ui_elements["test_button"].text == "Test" and tab.ui_elements["test_button"].enabled
    env.test_result = False
    asyncio.run(tab._test_connection())
    assert env.works[-1].result[1] == "Couldn't reach YouTube"
    env.test_result = ValueError("bad")
    asyncio.run(tab._test_connection())
    assert env.works[-1].result[1] == "Couldn't test the YouTube connection"
    assert env.works[-1].result[2]["details"] == "ValueError: bad"
    tab._test_in_progress = True
    n = len(env.works)
    asyncio.run(tab._test_connection())
    assert len(env.works) == n


def test_test_connection_outer_error_and_cleanup_error(env):
    tab = tabmod.YouTubeTab()
    tab.ui_elements["test_button"] = SimpleNamespace(set_text=lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    asyncio.run(tab._test_connection())
    assert env.notes[-1]["title"] == "Couldn't run the YouTube test"
    assert tab._test_in_progress is False
    tab2 = tabmod.YouTubeTab()
    tab2._cleanup_test()  # no button


# --------------------------------------------------------------------------- lists without UI


def test_playlist_and_channel_edits_without_widgets(env):
    tab = tabmod.YouTubeTab()
    tab._add_playlist_filter("x")  # no buffer
    tab._remove_playlist_filter("x")
    tab._on_playlist_input_enter()
    tab._on_channel_input_enter()
    tab._toggle_raw_channels()
    assert tab._channel_url_list() == []

    tab = _tab(env)
    tab._add_playlist_filter("Shorts")
    tab._add_playlist_filter("Shorts")
    assert tab.buffer.playlist_filter == ["Shorts"]
    assert env.notes[-1]["title"] == "“Shorts” is already in the filter list"
    tab._remove_playlist_filter("Missing")
    tab._remove_playlist_filter("Shorts")
    assert tab.buffer.playlist_filter == []

    tab._add_channel_url("  ")
    tab._add_channel_url(" https://youtube.com/@a ")
    tab._add_channel_url("https://youtube.com/@a")
    assert env.notes[-1]["title"] == "That channel is already listed"
    tab._add_channel_url("https://youtube.com/@b")
    assert tab.buffer.channel_urls == "https://youtube.com/@a|https://youtube.com/@b"
    tab._remove_channel_url("https://youtube.com/@a")
    assert tab._channel_url_list() == ["https://youtube.com/@b"] and tab.dirty is True

    tab._playlist_input = SimpleNamespace(value="  ")
    tab._on_playlist_input_enter()
    assert tab.buffer.playlist_filter == []
    tab._playlist_input.value = " VODs "
    tab._on_playlist_input_enter()
    assert tab.buffer.playlist_filter == ["VODs"] and tab._playlist_input.value == ""


def test_write_channel_urls_syncs_raw_field(env):
    tab = _tab(env)
    seen = []

    class Raw:
        def __init__(self):
            self._v = "old"

        @property
        def value(self):
            return self._v

        @value.setter
        def value(self, v):
            seen.append(tab._channel_ui_sync)
            self._v = v

    tab.ui_elements["channel_urls"] = Raw()
    tab._write_channel_urls(["a", "b"])
    assert tab.ui_elements["channel_urls"].value == "a|b" and seen == [True] and tab._channel_ui_sync is False
    tab._write_channel_urls(["a", "b"])  # unchanged: not rewritten
    assert seen == [True]

    tab._channel_ui_sync = True
    tab._on_channel_raw_change(SimpleNamespace(value="ignored"))
    assert tab.buffer.channel_urls == "a|b"
    tab._channel_ui_sync = False
    tab._on_channel_raw_change(SimpleNamespace(value=None))
    assert tab.buffer.channel_urls == ""


def test_list_heading():
    html = tabmod.YouTubeTab._list_heading("subscriptions", "A & B", 3)
    assert "A &amp; B" in html and '<span class="itg-sec-right">3</span>' in html


# --------------------------------------------------------------------------- the built page


def test_build_and_interact(env):
    env.state.data = YouTubeData(api_key="", channel_urls="https://youtube.com/@one", playlist_filter=["Shorts"])
    holder = {}

    def build():
        holder["tab"] = tabmod.YouTubeTab()
        holder["tab"].build(ui.column())

    async def check(user):
        tab = holder["tab"]
        await user.should_see("Latest videos, live chat, memberships and Super Chats")
        await user.should_see("Read live chat")
        await user.should_see("Process YouTube alerts")
        await user.should_see("https://youtube.com/@one")
        await user.should_see("Shorts")
        assert tab._oauth_fold.expansion.value is True and tab._key_fold.expansion.value is True
        assert "Channels to watch" in tab._channel_head.content and ">1<" in tab._channel_head.content
        assert env.timers[0].interval == 5.0
        assert tab._disconnect_button.visible is False

        # channels
        tab._channel_input.set_value("https://youtube.com/@two")
        tab._on_channel_input_enter()
        await user.should_see("https://youtube.com/@two")
        assert tab._channel_input.value == ""
        assert tab.ui_elements["channel_urls"].value == "https://youtube.com/@one|https://youtube.com/@two"
        tab._remove_channel_url("https://youtube.com/@one")
        await user.should_not_see("https://youtube.com/@one")
        tab._remove_channel_url("https://youtube.com/@two")
        await user.should_see("No channels yet. Add a channel URL below.")
        tab.ui_elements["channel_urls"].set_value("https://youtube.com/@raw")
        await user.should_see("https://youtube.com/@raw")

        assert tab._channel_raw_box.visible is False
        tab._toggle_raw_channels()
        assert tab._channel_raw_box.visible is True and tab._channel_raw_link.text == "Hide the list field"
        tab._toggle_raw_channels()
        assert tab._channel_raw_link.text == "Paste several at once"

        # playlists
        tab._playlist_input.set_value("VODs")
        assert tab.has_unsaved_changes() is True
        tab._on_playlist_input_enter()
        await user.should_see("VODs")
        tab._remove_playlist_filter("Shorts")
        tab._remove_playlist_filter("VODs")
        await user.should_see("No playlists ignored.")

        # fields and switches
        tab.ui_elements["api_key"].set_value("KEY")
        tab.ui_elements["oauth_client_id"].set_value("cid")
        tab.ui_elements["oauth_client_secret"].set_value("csec")
        tab.ui_elements["live_chat_enabled"].set_value(False)
        tab.ui_elements["alerts_enabled"].set_value(False)
        assert tab.buffer.api_key == "KEY" and tab._creds == {"client_id": "cid", "client_secret": "csec"}
        assert tab.buffer.live_chat_enabled is False and tab.buffer.alerts_enabled is False

        # discard restores everything from state
        tab.discard()
        assert tab.dirty is False and tab.has_unsaved_changes() is False
        assert tab.ui_elements["api_key"].value == "" and tab.ui_elements["oauth_client_id"].value == ""
        assert tab.ui_elements["live_chat_enabled"].value is True
        assert tab.ui_elements["channel_urls"].value == "https://youtube.com/@one"
        await user.should_see("https://youtube.com/@one")
        await user.should_see("Shorts")

        tab.ui_elements["api_key"].set_value("KEY2")
        user.find("Save").click()
        assert env.state.set_calls[-1]["api_key"] == "KEY2"
        assert env.notes[-1]["title"] == "YouTube settings saved"

    run_ui(build, check)


def test_build_with_saved_credentials(env):
    env.creds.creds = {"client_id": "a", "client_secret": "b"}
    env.state.data = YouTubeData(api_key="k", live_chat_enabled=False, alerts_enabled=False)
    env.status = {"oauth_connected": True, "live_chat_status": "Offline", "oauth_channel_title": "Me",
                  "is_connected": True, "latest_video": "Vid"}
    holder = {}

    def build():
        holder["tab"] = tabmod.YouTubeTab()
        holder["tab"].build(ui.column())

    async def check(user):
        tab = holder["tab"]
        assert tab._oauth_fold.expansion.value is False and tab._key_fold.expansion.value is False
        assert tab.ui_elements["live_chat_enabled"].value is False
        assert tab._disconnect_button.visible is True
        await user.should_see("No channels yet. Add a channel URL below.")
        await user.should_see("No playlists ignored.")
        tab._handle_oauth_disconnect()
        assert env.disconnects == 1

    run_ui(build, check)

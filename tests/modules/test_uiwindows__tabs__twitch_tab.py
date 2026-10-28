# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/twitch_tab.py (Settings → Twitch tab)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import chatbot, dataobjects, twitch  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import twitch_tab as tt  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self, save_ok=True):
        self.twitch = dataobjects.TwitchData(client_id="x")
        self.app = dataobjects.AppSettings()
        self.twitch_updates: Dict[str, Any] = {}
        self.app_updates: Dict[str, Any] = {}
        self.save_ok = save_ok
        self.saves = 0

    def get_twitch_data(self):
        return self.twitch

    def get_app_settings(self):
        return self.app

    def update_twitch_field(self, k, v):
        self.twitch_updates[k] = v

    def update_app_setting(self, k, v):
        self.app_updates[k] = v

    def save_changes(self):
        self.saves += 1
        return self.save_ok


class FakeCreds:
    def __init__(self, main=None, bot=None):
        self.main = dict(main or {})
        self.bot = dict(bot or {})
        self.twitch_calls: List[Dict[str, Any]] = []
        self.bot_calls: List[Dict[str, Any]] = []

    def get_twitch_credentials(self):
        return dict(self.main)

    def get_chatbot_credentials(self):
        return dict(self.bot)

    def update_twitch_credentials(self, **kw):
        self.twitch_calls.append(kw)

    def update_chatbot_credentials(self, **kw):
        self.bot_calls.append(kw)


class FakeCard:
    def __init__(self):
        self.sets: List[Any] = []
        self.metas: List[Any] = []

    def set(self, *args, **kw):
        self.sets.append((args, kw))

    def set_meta(self, rows):
        self.metas.append(list(rows))


class FakeFold:
    def __init__(self):
        self.saved = []

    def set_saved(self, v):
        self.saved.append(v)


class FakeBtn:
    def __init__(self):
        self.text = "Connect"
        self.enabled = True
        self._itg_connected = None
        self.props_calls = []

    def set_text(self, t):
        self.text = t

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def props(self, *a, **kw):
        self.props_calls.append((a, kw))
        return self


class FakeWork:
    def __init__(self, finished=False):
        self.finished = finished
        self.done_calls = []
        self.fail_calls = []

    def done(self, *a, **kw):
        self.done_calls.append((a, kw))

    def fail(self, *a, **kw):
        self.fail_calls.append((a, kw))


class SyncThread:
    def __init__(self, target=None, **kw):
        self._target = target
        self.daemon = False

    def start(self):
        self._target()


@pytest.fixture
def env(monkeypatch):
    state = FakeState()
    creds = FakeCreds({"client_id": "cid", "client_secret": "sec"}, {"client_id": "bid", "client_secret": "bsec"})
    notes: List[Dict[str, Any]] = []
    monkeypatch.setattr(tt, "state_manager", state)
    monkeypatch.setattr(tt, "api_credentials_manager", creds)
    monkeypatch.setattr(tt, "notify", lambda msg, **kw: notes.append({"message": msg, **kw}))
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    return SimpleNamespace(state=state, creds=creds, notes=notes)


def _main_status(**kw):
    base = {"status": "Connected", "is_valid": True, "user_name": "streamer", "avatar_url": "https://x/a.png",
            "last_update": "now", "token_refresh_countdown": "1h", "token_refresh_at": "12:00",
            "token_expires_countdown": "3h", "token_expires_at": "14:00"}
    base.update(kw)
    return base


def _tab_with_cards(env):
    tab = tt.TwitchTab()
    tab._load_from_state()
    tab._cards = {"main": FakeCard(), "chatbot": FakeCard()}
    tab._folds = {"main": FakeFold(), "chatbot": FakeFold()}
    tab._page = MagicMock()
    tab._steps = MagicMock()
    return tab


# --------------------------------------------------------------------------- helpers


def test_stop_ui_timer_handles_none_and_errors():
    tt._stop_ui_timer(None)
    t = MagicMock()
    tt._stop_ui_timer(t)
    assert t.active is False
    t.cancel.assert_called_once()

    class Bad:
        @property
        def active(self):
            return True

        @active.setter
        def active(self, v):
            raise RuntimeError("x")

        def cancel(self):
            raise RuntimeError("y")

    tt._stop_ui_timer(Bad())  # both errors swallowed


def test_fail_or_notify(env):
    work = FakeWork()
    tt._fail_or_notify(work, "twitch", "T", "D")
    assert work.fail_calls[0][0] == ("T",) and work.fail_calls[0][1]["details"] == "D"
    tt._fail_or_notify(FakeWork(finished=True), "twitch", "T2", "D2")
    tt._fail_or_notify(None, "chatbot", "T3", "D3")
    assert [n["title"] for n in env.notes] == ["T2", "T3"]
    assert env.notes[1]["source"] == "chatbot" and env.notes[1]["type"] == "error"


def test_has_token_and_is_fallback():
    assert tt._has_token({}) is False
    assert tt._has_token({"token_expires_countdown": None}) is False
    assert tt._has_token({"token_expires_countdown": "1h"}) is True
    assert tt._is_fallback("chatbot", {"status": "Using Main (Fallback)"}) is True
    assert tt._is_fallback("main", {"status": "fallback"}) is False


# --------------------------------------------------------------------------- describe / names


@pytest.mark.parametrize(
    "prefix, info, expected",
    [
        ("chatbot", {"status": "Fallback", "is_valid": True}, ("off", "Replies use your main account until you connect a bot", "warn", "Chatbot not connected")),
        ("chatbot", {"status": "Not Configured (fallback)"}, ("off", "Replies use your main account until you connect a bot", "warn", "Chatbot not connected")),
        ("chatbot", {"status": "Error fallback"}, ("warn", "Error fallback", "warn", "Chatbot not connected")),
        ("main", {"token_expired": True, "is_valid": True}, ("error", "Token expired · reconnect to sign in again", "error", "Main account token expired")),
        ("main", {"token_expired": True, "token_expires_countdown": "0s"}, ("error", "Token expired · reconnect to sign in again", "error", "Main account token expired")),
        ("main", {"is_valid": True, "degraded": True, "status": "Degraded"}, ("warn", "Degraded", "warn", "Main account degraded")),
        ("main", {"is_valid": True}, ("ok", "Connected · events flowing", "ok", "Main account connected")),
        ("chatbot", {"is_valid": True, "status": "Connected"}, ("ok", "Connected · replies come from this account", "ok", "Chatbot connected")),
        ("main", {"status": "No Internet"}, ("warn", "No Internet", "warn", "Main account disconnected")),
        ("main", {"status": "Authenticated but Disconnected"}, ("warn", "Authenticated but Disconnected", "warn", "Main account disconnected")),
        ("main", {"status": "Status Error"}, ("error", "Status Error", "error", "Main account status error")),
        ("main", {}, ("off", "Unknown", "warn", "Main account not connected")),
    ],
)
def test_describe(prefix, info, expected):
    assert tt.TwitchTab()._describe(prefix, info) == expected


@pytest.mark.parametrize(
    "prefix, info, expected",
    [
        ("chatbot", {"status": "fallback", "user_name": "x"}, ("Not connected", False)),
        ("main", {"user_name": " bob "}, ("bob", True)),
        ("main", {"user_name": "Unknown", "is_valid": True}, ("Twitch account", True)),
        ("main", {"user_name": "N/A", "token_expires_countdown": "1m"}, ("Twitch account", True)),
        ("main", {"user_name": "None"}, ("Not connected", False)),
    ],
)
def test_account_name(prefix, info, expected):
    assert tt.TwitchTab()._account_name(prefix, info) == expected


def test_countdown_class():
    assert tt.TwitchTab._countdown_class({"token_expired": True}, for_expiry=True) == "is-error"
    assert tt.TwitchTab._countdown_class({"token_refresh_due": True}, for_expiry=False) == "is-warn"
    assert tt.TwitchTab._countdown_class({"token_refresh_due": True}, for_expiry=True) == ""


# --------------------------------------------------------------------------- status application


def test_update_token_timing_labels(env):
    tab = _tab_with_cards(env)
    tab._update_token_timing_labels("nope", {})  # no card: no-op
    tab._update_token_timing_labels("chatbot", {"status": "fallback", "is_valid": True})
    assert tab._cards["chatbot"].metas == [[]]
    tab._update_token_timing_labels("main", {"is_valid": False})
    assert tab._cards["main"].metas == [[]]
    tab._update_token_timing_labels("main", _main_status(token_refresh_due=True))
    rows = tab._cards["main"].metas[-1]
    assert rows[0] == ("Updated", "now", "", "")
    assert rows[1] == ("Next refresh", "1h", "12:00", "is-warn")
    assert rows[2] == ("Token expires", "3h", "14:00", "")


def test_apply_status_updates_card_pills_and_overview(env):
    tab = _tab_with_cards(env)
    btn = FakeBtn()
    tab.ui_elements["main_connect_button"] = btn
    tab._apply_status("main", _main_status())
    args, kw = tab._cards["main"].sets[-1]
    assert args == ("streamer", "ok", "Connected · events flowing")
    assert kw == {"initial": "S", "photo_url": "https://x/a.png"}
    assert tab._valid["main"] is True and tab._connected["main"] is True
    assert tab._pills["main"] == ("ok", "Main account connected")
    assert btn.text == "Reconnect"
    tab._page.set_status.assert_called_with([("ok", "Main account connected")])
    assert tab._folds["main"].saved[-1] is True and tab._folds["chatbot"].saved[-1] is True
    tab._steps.set_content.assert_called_once()
    tab._refresh_overview()  # same content: no second set_content
    tab._steps.set_content.assert_called_once()


def test_apply_status_not_connected_has_no_avatar(env):
    tab = _tab_with_cards(env)
    tab._apply_status("chatbot", {"status": "fallback"})
    args, kw = tab._cards["chatbot"].sets[-1]
    assert args[0] == "Not connected" and kw == {"initial": None, "photo_url": None}
    assert tab._valid["chatbot"] is False


def test_apply_status_without_card_is_noop(env):
    tab = tt.TwitchTab()
    tab._apply_status("main", _main_status())
    tab._apply_status_error("main")
    assert tab._pills == {}


def test_apply_status_error(env):
    tab = _tab_with_cards(env)
    tab._apply_status_error("chatbot")
    assert tab._cards["chatbot"].sets[-1][0] == ("Not connected", "error", "Status Error")
    assert tab._pills["chatbot"] == ("error", "Chatbot status error")


def test_refresh_overview_unsaved_creds(env):
    tab = _tab_with_cards(env)
    tab._saved_creds = {}
    tab._page = None
    tab._steps = None
    tab._refresh_overview()
    assert tab._folds["main"].saved == [False] and tab._folds["chatbot"].saved == [False]


def test_refresh_status_calls_both(env, monkeypatch):
    tab = _tab_with_cards(env)
    monkeypatch.setattr(twitch, "get_twitch_connection_status", lambda: _main_status())
    monkeypatch.setattr(chatbot, "get_chatbot_connection_status", lambda: {"status": "Connected", "is_valid": True,
                                                                           "user_name": "bot"})
    tab._refresh_status()
    assert tab._cards["main"].sets[-1][0][0] == "streamer"
    assert tab._cards["chatbot"].sets[-1][0][0] == "bot"


def test_refresh_status_errors_show_status_error(env, monkeypatch):
    tab = _tab_with_cards(env)
    monkeypatch.setattr(twitch, "get_twitch_connection_status", MagicMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(chatbot, "get_chatbot_connection_status", MagicMock(side_effect=RuntimeError("y")))
    tab._refresh_status()
    assert tab._pills == {"main": ("error", "Main account status error"),
                          "chatbot": ("error", "Chatbot status error")}


def test_refresh_token_countdowns(env, monkeypatch):
    tab = _tab_with_cards(env)
    monkeypatch.setattr(twitch, "get_twitch_connection_status", lambda: _main_status())
    monkeypatch.setattr(chatbot, "get_chatbot_connection_status", lambda: {"status": "fallback"})
    tab._refresh_token_countdowns()
    assert len(tab._cards["main"].metas[-1]) == 3 and tab._cards["chatbot"].metas[-1] == []
    monkeypatch.setattr(twitch, "get_twitch_connection_status", MagicMock(side_effect=RuntimeError("x")))
    tab._refresh_token_countdowns()  # swallowed


# --------------------------------------------------------------------------- enter / exit


def test_on_enter_and_exit(env, monkeypatch):
    scheduled = []
    monkeypatch.setattr(tt, "layout_schedule", lambda i, cb, **kw: scheduled.append((i, cb, kw)))
    tab = tt.TwitchTab()
    tab.on_enter()  # no timers yet
    tab._status_timer, tab._token_timer, tab._page = MagicMock(), MagicMock(), MagicMock()
    tab.on_enter()
    assert tab._status_timer.active is True and tab._token_timer.active is True
    tab._page.set_active.assert_called_with(True)
    assert [s[1] for s in scheduled[-2:]] == [tab._refresh_status, tab._refresh_token_countdowns]
    assert all(s[2] == {"once": True} for s in scheduled)
    tab.on_exit()
    assert tab._status_timer.active is False and tab._token_timer.active is False
    tab._page.set_active.assert_called_with(False)
    tt.TwitchTab().on_exit()


# --------------------------------------------------------------------------- connect button


def test_connect_button_states(env):
    tab = tt.TwitchTab()
    tab._sync_connect_button("main")
    tab._start_connect_button("main")
    tab._reset_connect_button("main")  # no button: all no-ops
    btn = FakeBtn()
    tab.ui_elements["main_connect_button"] = btn
    tab._start_connect_button("main")
    assert btn.text == "Connecting..." and btn.enabled is False
    tab._connected["main"] = True
    tab._sync_connect_button("main")
    assert btn.text == "Connecting..."  # still connecting: untouched
    tab._reset_connect_button("main")
    assert btn.enabled is True and btn.text == "Reconnect"


# --------------------------------------------------------------------------- buffers / save / discard


def test_load_from_state_copies_buffers(env):
    tab = tt.TwitchTab()
    tab._load_from_state()
    assert tab.buffer == env.state.twitch and tab.buffer is not env.state.twitch
    assert tab._creds == {"client_id": "cid", "client_secret": "sec", "chatbot_client_id": "bid",
                          "chatbot_client_secret": "bsec"}
    assert tab._saved_creds == tab._creds and tab.dirty is False


def test_setters_mark_dirty_only_on_change(env):
    tab = tt.TwitchTab()
    tab._load_from_state()
    tab._set("alerts_enabled", True)
    tab._set_cred("client_id", "cid")
    tab._set_app_setting("auto_raid_chat_shoutout", True)
    assert tab.dirty is False
    tab._set("alerts_enabled", False)
    assert tab.dirty and tab.buffer.alerts_enabled is False
    tab.dirty = False
    tab._set_cred("client_id", "new")
    assert tab.dirty and tab._creds["client_id"] == "new"
    tab.dirty = False
    tab._set_app_setting("auto_raid_chat_shoutout", False)
    assert tab.dirty and tab._app_settings_buffer.auto_raid_chat_shoutout is False
    tab._app_settings_buffer = None
    tab._set_app_setting("auto_raid_chat_shoutout", True)  # no buffer: no-op


def test_save_without_buffer_is_noop(env):
    tt.TwitchTab().save()
    assert env.state.saves == 0


def test_save_persists_everything(env):
    tab = _tab_with_cards(env)
    tab._set("alerts_enabled", False)
    tab._set_cred("client_secret", "new")
    tab._set_app_setting("auto_raid_helix_shoutout", False)
    tab.save()
    assert env.state.twitch_updates["alerts_enabled"] is False
    assert env.creds.twitch_calls[-1] == {"client_id": "cid", "client_secret": "new"}
    assert env.creds.bot_calls[-1] == {"client_id": "bid", "client_secret": "bsec"}
    assert env.state.app_updates == {"auto_raid_helix_shoutout": False, "auto_raid_chat_shoutout": True}
    assert env.notes[-1]["title"] == "Twitch settings saved"
    assert tab.dirty is False and tab._saved_creds["client_secret"] == "new"


def test_save_failure_keeps_dirty(env):
    env.state.save_ok = False
    tab = _tab_with_cards(env)
    tab._app_settings_buffer = None
    tab._set("alerts_enabled", False)
    tab.save()
    assert tab.dirty is True and env.notes == []
    assert env.state.app_updates == {}


def test_discard_resets_controls(env):
    tab = _tab_with_cards(env)
    tab._set_cred("client_id", "typed")
    tab._set("alerts_enabled", False)
    tab._set_app_setting("auto_raid_chat_shoutout", False)
    el = lambda v: SimpleNamespace(value=v)  # noqa: E731
    tab.ui_elements = {
        "client_id": el("typed"), "chatbot_client_secret": el("x"), "auto_raid_chat_shoutout": el(False),
        "alerts_enabled": el(False), "main_connect_button": object(), "unrelated": el("keep"),
    }
    tab.discard()
    assert tab.ui_elements["client_id"].value == "cid"
    assert tab.ui_elements["chatbot_client_secret"].value == "bsec"
    assert tab.ui_elements["auto_raid_chat_shoutout"].value is True
    assert tab.ui_elements["alerts_enabled"].value is True
    assert tab.ui_elements["unrelated"].value == "keep"
    assert tab.dirty is False


def test_save_settings_only(env):
    tab = _tab_with_cards(env)
    tab._creds.update({"client_id": "c2", "chatbot_client_id": "  ", "chatbot_client_secret": " s "})
    tab._save_settings_only()
    assert env.creds.twitch_calls[-1] == {"client_id": "c2", "client_secret": "sec"}
    assert env.creds.bot_calls[-1] == {"client_id": None, "client_secret": "s"}
    assert tab._saved_creds["chatbot_client_id"] == "" and tab._saved_creds["chatbot_client_secret"] == "s"


def test_save_settings_only_skips_empty_bot_and_handles_errors(env, monkeypatch):
    tab = _tab_with_cards(env)
    tab._creds.update({"chatbot_client_id": "", "chatbot_client_secret": ""})
    tab._save_settings_only()
    assert env.creds.bot_calls == []
    monkeypatch.setattr(env.creds, "update_twitch_credentials", MagicMock(side_effect=RuntimeError("disk")))
    tab._save_settings_only()  # logged, overview still refreshed
    assert tab._folds["main"].saved


# --------------------------------------------------------------------------- OAuth handlers


@pytest.fixture
def oauth(env, monkeypatch):
    timers: List[Any] = []
    works: List[FakeWork] = []

    def schedule(interval, cb, **kw):
        t = MagicMock()
        t.cb = cb
        timers.append(t)
        return t

    def progress(title, **kw):
        w = FakeWork()
        w.title = title
        works.append(w)
        return w

    monkeypatch.setattr(tt, "layout_schedule", schedule)
    monkeypatch.setattr(tt, "notify_progress", progress)
    monkeypatch.setattr(threading, "Thread", SyncThread)
    return SimpleNamespace(timers=timers, works=works, env=env)


HANDLERS = [
    ("main", "_handle_main_oauth_connection", twitch, "trigger_oauth_reconnection", "client_id", "client_secret",
     "_refresh_main_status"),
    ("chatbot", "_handle_chatbot_oauth_connection", chatbot, "trigger_chatbot_oauth_reconnection",
     "chatbot_client_id", "chatbot_client_secret", "_refresh_chatbot_status"),
]


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_requires_credentials(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh):
    tab = _tab_with_cards(oauth.env)
    tab._creds[secret_key] = ""
    getattr(tab, handler)()
    assert oauth.env.notes[-1]["type"] == "warning"
    assert oauth.works == [] and oauth.timers == []


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
@pytest.mark.parametrize("success", [True, False])
def test_oauth_complete(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh, success):
    monkeypatch.setattr(mod, fn, lambda: success)
    tab = _tab_with_cards(oauth.env)
    btn = FakeBtn()
    tab.ui_elements[f"{prefix}_connect_button"] = btn
    monkeypatch.setattr(tab, refresh, MagicMock())
    getattr(tab, handler)()
    assert btn.text == "Connecting..." and btn.enabled is False
    assert oauth.env.creds.twitch_calls  # saved settings only
    timer = oauth.timers[0]
    assert tab._active_timers == [timer]
    timer.cb()
    work = oauth.works[0]
    if success:
        assert work.done_calls and not work.fail_calls
    else:
        assert work.fail_calls and not work.done_calls
    getattr(tab, refresh).assert_called_once()
    assert btn.enabled is True
    timer.cancel.assert_called()
    timer.cb()  # already shown -> just stops the timer again
    assert len(work.done_calls) + len(work.fail_calls) == 1


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_thread_error(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh):
    monkeypatch.setattr(mod, fn, MagicMock(side_effect=RuntimeError("browser gone")))
    tab = _tab_with_cards(oauth.env)
    getattr(tab, handler)()
    oauth.timers[0].cb()
    assert oauth.works[0].fail_calls[0][1]["details"] == "browser gone"
    oauth.timers[0].cb()
    assert len(oauth.works[0].fail_calls) == 1


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_still_connecting_then_timeout(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key,
                                             refresh):
    monkeypatch.setattr(threading, "Thread", lambda target=None, **kw: SimpleNamespace(start=lambda: None,
                                                                                       daemon=False))
    tab = _tab_with_cards(oauth.env)
    getattr(tab, handler)()
    timer = oauth.timers[0]
    timer.cb()  # still connecting: nothing happens
    assert oauth.works[0].done_calls == [] and timer.cancel.call_count == 0
    for _ in range(700):
        timer.cb()
        if oauth.works[0].done_calls:
            break
    assert oauth.works[0].done_calls[0][1] == {"type": "warning"}
    assert timer.cancel.called


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_ui_update_errors_are_logged(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key,
                                           refresh):
    monkeypatch.setattr(mod, fn, lambda: True)
    tab = _tab_with_cards(oauth.env)
    monkeypatch.setattr(tab, refresh, MagicMock(side_effect=RuntimeError("ui gone")))
    getattr(tab, handler)()
    oauth.timers[0].cb()  # error in refresh is logged, timer still stopped
    assert oauth.timers[0].cancel.called


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_error_path_ui_errors_are_logged(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key,
                                               refresh):
    monkeypatch.setattr(mod, fn, MagicMock(side_effect=RuntimeError("x")))
    tab = _tab_with_cards(oauth.env)
    getattr(tab, handler)()
    oauth.works[0].fail = MagicMock(side_effect=RuntimeError("toast gone"))
    oauth.timers[0].cb()
    assert oauth.timers[0].cancel.called


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_unknown_status_stops_timer(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh):
    """A result in an unexpected state just stops polling."""
    captured = {}

    class CaptureThread(SyncThread):
        def start(self):
            # Peek at the closure's result dict and put it in an odd state.
            cells = [c.cell_contents for c in self._target.__closure__ or []]
            captured["result"] = next(c for c in cells if isinstance(c, dict) and "status" in c)
            captured["result"]["status"] = "weird"

    monkeypatch.setattr(threading, "Thread", CaptureThread)
    tab = _tab_with_cards(oauth.env)
    getattr(tab, handler)()
    oauth.timers[0].cb()
    assert oauth.timers[0].cancel.called
    assert oauth.works[0].done_calls == [] and oauth.works[0].fail_calls == []


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_setup_error_before_progress(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh):
    monkeypatch.setattr(tt, "notify_progress", MagicMock(side_effect=RuntimeError("no ui")))
    tab = _tab_with_cards(oauth.env)
    btn = FakeBtn()
    tab.ui_elements[f"{prefix}_connect_button"] = btn
    getattr(tab, handler)()
    assert oauth.env.notes[-1]["type"] == "error"
    assert "RuntimeError: no ui" in oauth.env.notes[-1]["details"]
    assert btn.enabled is True


@pytest.mark.parametrize("prefix, handler, mod, fn, id_key, secret_key, refresh", HANDLERS)
def test_oauth_setup_error_after_progress(oauth, monkeypatch, prefix, handler, mod, fn, id_key, secret_key, refresh):
    monkeypatch.setattr(tt, "layout_schedule", MagicMock(side_effect=RuntimeError("no slot")))
    monkeypatch.setattr(mod, fn, lambda: True)
    tab = _tab_with_cards(oauth.env)
    getattr(tab, handler)()
    assert oauth.works[0].fail_calls and "no slot" in oauth.works[0].fail_calls[0][1]["details"]


# --------------------------------------------------------------------------- full UI build


def _patch_status(monkeypatch, main, bot):
    monkeypatch.setattr(twitch, "get_twitch_connection_status", lambda: main)
    monkeypatch.setattr(chatbot, "get_chatbot_connection_status", lambda: bot)


def test_build_connected(env, monkeypatch):
    _patch_status(monkeypatch, _main_status(), {"status": "Connected", "is_valid": True, "user_name": "mybot"})
    holder = {}

    def build():
        holder["tab"] = tab = tt.TwitchTab()
        tab.build(ui.column())

    async def check(user):
        await user.should_see("Main account")
        await user.should_see("Chatbot account")
        await user.should_see("Process Twitch alerts")
        await user.should_see("Send a native Twitch shoutout")
        await user.should_see("Send a chat raid shoutout")
        tab = holder["tab"]
        assert tab._pills == {"main": ("ok", "Main account connected"), "chatbot": ("ok", "Chatbot connected")}
        assert tab.ui_elements["main_connect_button"].text == "Reconnect"
        assert "streamer" in tab._cards["main"]._top
        assert tab._status_timer is not None and tab._token_timer is not None
        # Toggle the alerts switch through the UI.
        tab.ui_elements["alerts_enabled"].value = False
        assert tab.dirty is True and tab.buffer.alerts_enabled is False
        tab.ui_elements["auto_raid_chat_shoutout"].value = False
        assert tab._app_settings_buffer.auto_raid_chat_shoutout is False
        tab.ui_elements["client_id"].value = "typed"
        assert tab._creds["client_id"] == "typed"
        tab.ui_elements["client_secret"].value = None
        assert tab._creds["client_secret"] == ""
        tab.ui_elements["chatbot_client_id"].value = "b2"
        tab.ui_elements["chatbot_client_secret"].value = None
        assert tab._creds["chatbot_client_id"] == "b2" and tab._creds["chatbot_client_secret"] == ""
        tab.discard()
        assert tab.ui_elements["client_id"].value == "cid" and tab.dirty is False
        tab.on_exit()
        tab.on_enter()

    run_ui(build, check)


def test_build_disconnected_without_creds(env, monkeypatch):
    env.creds.main = {}
    env.creds.bot = {}
    _patch_status(monkeypatch, {"status": "Not Configured", "user_name": "N/A"},
                  {"status": "Using main account (fallback)"})
    holder = {}

    def build():
        holder["tab"] = tab = tt.TwitchTab()
        tab.build(ui.column())

    async def check(user):
        await user.should_see("Not connected")
        await user.should_see("Needed to connect")
        tab = holder["tab"]
        assert tab.ui_elements["main_connect_button"].text == "Connect"
        assert tab._pills["chatbot"] == ("warn", "Chatbot not connected")
        tab._handle_main_oauth_connection()  # no creds: warning, no flow
        assert env.notes[-1]["title"].startswith("Enter your Twitch Client ID")
        user.find("Save").click()
        assert env.state.saves == 1

    run_ui(build, check)

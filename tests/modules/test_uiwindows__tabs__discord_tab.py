# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/discord_tab.py (Settings → Discord) with a fake Discord service."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules.dataobjects import DiscordData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import discord_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeState:
    def __init__(self) -> None:
        self.data = DiscordData()
        self.updates: List[tuple] = []
        self.save_ok = True
        self.save_error: Any = None
        self.wipe_token_on_save = False

    def get_discord_data(self):
        return self.data

    def update_discord_field(self, name, value):
        self.updates.append((name, value))
        setattr(self.data, name, value)

    def save_changes(self):
        if self.save_error:
            raise self.save_error
        if self.wipe_token_on_save:
            self.data.bot_token = ""
        return self.save_ok


class FakeService:
    def __init__(self) -> None:
        self.status: Dict[str, Any] = {"status": "Disconnected", "is_connected": False}
        self.invite = ""
        self.guilds: List[dict] = []
        self.channels: Dict[str, List[dict]] = {}
        self.connect_result: Any = True
        self.disconnect_error: Any = None
        self.connects: List[str] = []
        self.disconnects = 0
        self.list_error: Any = None
        self.status_error: Any = None
        self.discord_service = SimpleNamespace(is_connected=lambda: bool(self.status.get("is_connected")))

    def get_discord_status(self):
        if self.status_error:
            raise self.status_error
        return dict(self.status)

    def get_invite_url(self):
        return self.invite

    def list_guilds(self):
        if self.list_error:
            raise self.list_error
        return list(self.guilds)

    def list_text_channels(self, gid):
        if self.list_error:
            raise self.list_error
        return list(self.channels.get(gid, []))

    def connect_with_token(self, token):
        self.connects.append(token)
        if isinstance(self.connect_result, BaseException):
            raise self.connect_result
        return self.connect_result

    def disconnect(self):
        if self.disconnect_error:
            raise self.disconnect_error
        self.disconnects += 1


class SyncThread:
    def __init__(self, target, daemon=False, name=None):
        self.target = target
        self.name = name

    def start(self):
        self.target()


@pytest.fixture
def env(monkeypatch):
    st = FakeState()
    svc = FakeService()
    notes: List[dict] = []
    scheduled: List[tuple] = []
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "state_manager", st)
    monkeypatch.setattr(tabmod, "discord_service", svc)
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append({"message": message, **kw}))

    def schedule(interval, cb, *, once=False, active=True):
        timer = SimpleNamespace(interval=interval, cb=cb, once=once, active=active)
        scheduled.append(timer)
        return timer

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)
    monkeypatch.setattr(tabmod, "threading", SimpleNamespace(Thread=SyncThread))
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    return SimpleNamespace(state=st, svc=svc, notes=notes, scheduled=scheduled, mp=monkeypatch)


def titles(env):
    return [n.get("title") for n in env.notes]


def fire(env, cb_owner=None):
    """Run every scheduled one-shot callback (re-scheduled polls included)."""
    for _ in range(10):
        pending = [t for t in env.scheduled if t.once and not getattr(t, "fired", False)]
        if not pending:
            return
        for t in pending:
            t.fired = True
            t.cb()


class FakeSelect:
    def __init__(self, value=None):
        self.value = value
        self.options = None

    def set_options(self, options):
        self.options = options


class FakeButton:
    def __init__(self):
        self.visible = None
        self.enabled = True
        self.text = ""
        self.fail = False

    def set_visibility(self, v):
        self.visible = v

    def disable(self):
        if self.fail:
            raise RuntimeError("deleted")
        self.enabled = False

    def enable(self):
        if self.fail:
            raise RuntimeError("deleted")
        self.enabled = True

    def set_text(self, t):
        self.text = t


def loaded_tab(env):
    tab = tabmod.DiscordTab()
    tab._load_from_state()
    return tab


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "event,expected",
    [
        (SimpleNamespace(value="abc.def"), "abc.def"),
        (SimpleNamespace(value=7), "7"),
        (SimpleNamespace(value=["x"], args="whole"), "whole"),
        (SimpleNamespace(value=None, args=("first", "second")), "first"),
        (SimpleNamespace(value=None, args=[]), ""),
        (SimpleNamespace(), ""),
    ],
)
def test_str_from_value_event(event, expected):
    assert tabmod.DiscordTab._str_from_value_event(event) == expected


def test_load_from_state_copies_and_normalises(env):
    env.state.data = DiscordData(bot_token=" tok ", go_live_channels=[{"guild_id": "1", "channel_id": "2"}])
    tab = loaded_tab(env)
    assert tab._creds == {"bot_token": "tok"} and tab._token_saved is True and tab.dirty is False
    assert tab.buffer.go_live_channels == env.state.data.go_live_channels
    assert tab.buffer.go_live_channels is not env.state.data.go_live_channels
    assert tab._saved_go_live_channels == (("1", "2"),)


def test_load_from_state_none_channels(env):
    env.state.data = DiscordData()
    env.state.data.go_live_channels = None
    tab = loaded_tab(env)
    assert tab.buffer.go_live_channels == [] and tab._token_saved is False


def test_go_live_signature_and_dirty(env):
    tab = tabmod.DiscordTab()
    assert tab._go_live_signature() == ()
    assert tab._go_live_is_dirty() is False
    tab = loaded_tab(env)
    tab.buffer.go_live_channels = [{"guild_id": 2, "channel_id": None}, "junk", {"guild_id": "1", "channel_id": "9"}]
    assert tab._go_live_signature() == (("1", "9"), ("2", ""))
    assert tab._go_live_is_dirty() is True and tab.has_unsaved_changes() is True
    assert tab._sync_go_live_dirty() is None


def test_set_marks_dirty_and_suppression(env):
    tab = tabmod.DiscordTab()
    tab._set("go_live_enabled", True)  # no buffer
    tab = loaded_tab(env)
    tab._set("go_live_enabled", False)  # unchanged
    assert tab.dirty is False
    tab._set("go_live_enabled", True)
    assert tab.buffer.go_live_enabled is True and tab.dirty is True
    tab.dirty = False
    tab._suppress_dirty = True
    tab._set("go_live_message", "x")
    assert tab.buffer.go_live_message == "x" and tab.dirty is False


def test_set_cred(env):
    tab = loaded_tab(env)
    tab._set_cred("bot_token", "  ")
    assert tab.dirty is False
    tab._set_cred("bot_token", " a.b_c+d= ")
    assert tab._creds["bot_token"] == "a.b_c+d=" and tab.dirty is True
    tab.dirty = False
    tab._set_cred("bot_token", "")  # blank never wipes
    assert tab._creds["bot_token"] == "a.b_c+d="
    tab._set_cred("bot_token", None)
    tab._suppress_dirty = True
    tab._set_cred("bot_token", "new")
    assert tab._creds["bot_token"] == "new" and tab.dirty is False


def test_channel_key(env):
    assert tabmod.DiscordTab()._channel_key({"guild_id": "1", "channel_id": "2"}) == "1:2"


# --------------------------------------------------------------------------- save


def test_save_settings_only_success(env):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = " tok "
    tab.buffer.go_live_enabled = True
    assert tab._save_settings_only() is True
    assert env.state.updates[0] == ("bot_token", "tok")
    names = [n for n, _ in env.state.updates]
    assert names.count("bot_token") == 1 and "go_live_enabled" in names and "go_live_channels" in names
    # Status fields belong to the bot service; a stale buffer copy must not overwrite them.
    assert not {"connection_status", "bot_user_id", "bot_username", "application_id"} & set(names)
    assert tab._token_saved is True


def test_save_settings_only_without_buffer(env):
    tab = tabmod.DiscordTab()
    tab._creds["bot_token"] = ""
    assert tab._save_settings_only() is True
    assert env.state.updates == [("bot_token", "")] and tab._token_saved is False


def test_save_settings_only_save_changes_false(env):
    tab = loaded_tab(env)
    env.state.save_ok = False
    assert tab._save_settings_only() is False
    assert env.notes == []  # save_changes reports its own failure


def test_save_settings_only_readback_empty(env):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = "tok"
    env.state.wipe_token_on_save = True
    assert tab._save_settings_only() is False
    assert titles(env) == ["Couldn't save Discord settings"]
    assert "didn't stick" in env.notes[0]["details"]


def test_save_settings_only_exception(env):
    tab = loaded_tab(env)
    env.state.save_error = RuntimeError("db gone")
    assert tab._save_settings_only() is False
    assert env.notes[0]["details"] == "RuntimeError: db gone"
    assert env.notes[0]["actions"] == tabmod.nav_actions_settings("Database")


def test_save_public(env):
    tab = tabmod.DiscordTab()
    tab.save()  # no buffer -> nothing
    assert env.state.updates == []
    tab = loaded_tab(env)
    tab.dirty = True
    tab.buffer.go_live_channels = [{"guild_id": "1", "channel_id": "2"}]
    tab.save()
    assert tab.dirty is False and tab._saved_go_live_channels == (("1", "2"),)
    assert titles(env) == ["Discord settings saved"]
    tab.save(silent=True)
    assert titles(env) == ["Discord settings saved"]
    env.state.save_ok = False
    tab.dirty = True
    tab.save()
    assert tab.dirty is True


# --------------------------------------------------------------------------- status view


def test_status_view_connected(env):
    tab = tabmod.DiscordTab()
    assert tab._status_view({"is_connected": True, "bot_username": "Bot"}) == (
        "ok", "Bot connected", "Signed in as", "Bot", "Connected")
    tab._guilds_cache = [{"id": "1"}]
    assert tab._status_view({"is_connected": True})[3:] == ("Discord bot", "Connected · in 1 server")
    tab._guilds_cache = [{"id": "1"}, {"id": "2"}]
    assert tab._status_view({"is_connected": True})[4] == "Connected · in 2 servers"


def test_status_view_busy_states(env):
    tab = tabmod.DiscordTab()
    assert tab._status_view({"status": "Connecting"}) == ("busy", "Connecting", "Discord bot", "Not connected",
                                                          "Connecting…")
    assert tab._status_view({"status": "Reconnecting"})[1] == "Reconnecting"
    tab._connect_in_progress = True
    assert tab._status_view({"status": "Disconnected"})[1] == "Connecting"


def test_status_view_errors_and_off(env):
    tab = tabmod.DiscordTab()
    assert tab._status_view({"status": "Auth Failed"})[:2] == ("error", "Auth failed")
    assert tab._status_view({})[4] == "Paste the bot token, then connect"
    tab._token_saved = True
    assert tab._status_view({"status": "Disconnected"}) == ("off", "Not connected", "Discord bot", "Not connected",
                                                            "Press Connect to start the bot")
    assert tab._status_view({"status": "Error"}) == ("error", "Error", "Discord bot", "Not connected",
                                                     "Error · press Connect to try again")


def test_setup_steps(env):
    tab = loaded_tab(env)
    assert [done for _t, done in tab._setup_steps(False)] == [False, False, False, False]
    tab._token_saved = True
    tab._invite_url = "u"
    tab.buffer.go_live_channels = [{"guild_id": "1", "channel_id": "2"}, "junk"]
    assert [done for _t, done in tab._setup_steps(True)] == [True, True, True, True]


def test_sync_connect_buttons(env):
    tab = tabmod.DiscordTab()
    tab._sync_connect_buttons(True)  # no buttons -> noop
    p, s = FakeButton(), FakeButton()
    tab._connect_button, tab._reconnect_button = p, s
    tab._sync_connect_buttons(True)
    assert (p.visible, s.visible, p.text, s.text) == (False, True, "Connect", "Reconnect")
    tab._connect_in_progress = True
    tab._sync_connect_buttons(False)
    assert p.visible is True and not p.enabled and not s.enabled and p.text == s.text == "Connecting…"


def test_refresh_status_without_ui_and_error(env):
    tab = loaded_tab(env)
    env.svc.invite = "https://invite"
    tab._refresh_status()
    assert tab._invite_url == "https://invite" and tab._connected is False
    env.svc.status_error = RuntimeError("x")
    tab._refresh_status()  # swallowed


def test_refresh_status_background_connect_reloads_guilds(env):
    tab = loaded_tab(env)
    calls = []
    env.mp.setattr(tab, "_reload_guilds", lambda report=False: calls.append(report))
    tab._refresh_status()
    env.svc.status = {"status": "Connected", "is_connected": True, "invite_url": "https://i"}
    tab._refresh_status()
    assert calls == [False]
    tab._refresh_status()  # still connected -> no reload
    assert calls == [False]


# --------------------------------------------------------------------------- guilds / channels


def test_reload_guilds_not_connected_clears(env):
    tab = loaded_tab(env)
    tab._guilds_cache = [{"id": "1"}]
    tab._guild_select, tab._channel_select = FakeSelect("1"), FakeSelect("2")
    tab._reload_guilds()
    assert tab._guilds_cache == [] and tab._guild_select.options == {} and tab._channel_select.options == {}
    tab._guild_select = tab._channel_select = None
    tab._reload_guilds()


def test_reload_guilds_connected_selects_first(env):
    env.svc.status = {"is_connected": True}
    env.svc.guilds = [{"id": "1", "name": "One"}, {"id": "2", "name": ""}]
    env.svc.channels = {"1": [{"id": "10", "name": "general"}, {"id": "11"}]}
    tab = loaded_tab(env)
    tab._guild_select, tab._channel_select = FakeSelect(), FakeSelect("old")
    tab._reload_guilds()
    assert tab._guild_select.options == {"1": "One", "2": "2"} and tab._guild_select.value == "1"
    assert tab._channel_select.options == {"10": "#general", "11": "#11"} and tab._channel_select.value is None
    # already selected: keep
    tab._guild_select.value = "2"
    tab._reload_guilds()
    assert tab._guild_select.value == "2"
    tab._guild_select = None
    tab._reload_guilds()
    assert len(tab._guilds_cache) == 2


def test_reload_guilds_error_reports_only_when_asked(env):
    env.svc.status = {"is_connected": True}
    env.svc.list_error = RuntimeError("forbidden")
    tab = loaded_tab(env)
    tab._reload_guilds()
    assert env.notes == []
    tab._reload_guilds(report=True)
    assert titles(env) == ["Couldn't list your Discord servers"]
    assert env.notes[0]["details"] == "RuntimeError: forbidden"


def test_on_guild_changed(env):
    tab = loaded_tab(env)
    tab._on_guild_changed(None)
    tab._on_guild_changed("  ")
    env.svc.channels = {"5": [{"id": "50", "name": "x"}]}
    tab._on_guild_changed(5)
    assert tab._channels_cache == [{"id": "50", "name": "x"}]
    env.svc.list_error = RuntimeError("x")
    tab._on_guild_changed("5")  # swallowed


# --------------------------------------------------------------------------- add / remove channels (logic)


def test_add_channel_guards(env):
    tab = tabmod.DiscordTab()
    tab._add_go_live_channel()  # no buffer
    tab = loaded_tab(env)
    tab._add_go_live_channel()  # no selects
    tab._guild_select, tab._channel_select = FakeSelect(""), FakeSelect("2")
    tab._add_go_live_channel()
    assert titles(env) == ["Pick a server and channel first"]


def test_add_channel_names_and_duplicate(env):
    tab = loaded_tab(env)
    tab._guild_select, tab._channel_select = FakeSelect("1"), FakeSelect("2")
    tab._guilds_cache = [{"id": "1", "name": "Guild"}]
    tab._channels_cache = [{"id": "2", "name": "news"}]
    tab._add_go_live_channel()
    assert tab.buffer.go_live_channels == [{"guild_id": "1", "channel_id": "2", "guild_name": "Guild",
                                            "channel_name": "news"}]
    assert tab.has_unsaved_changes() is True
    tab._add_go_live_channel()
    assert titles(env) == ["#news is already in the go-live list"]
    tab._channel_select.value = "3"  # unknown channel and guild names fall back to ids
    tab._guilds_cache = [{"id": "1", "name": None}]
    tab._add_go_live_channel()
    assert tab.buffer.go_live_channels[-1] == {"guild_id": "1", "channel_id": "3", "guild_name": "1",
                                               "channel_name": "3"}


def test_remove_channel(env):
    tab = tabmod.DiscordTab()
    tab._remove_go_live_channel("1:2")  # no buffer
    tab = loaded_tab(env)
    tab.buffer.go_live_channels = [{"guild_id": "1", "channel_id": "2"}, {"guild_id": "1", "channel_id": "3"}, "junk"]
    tab._remove_go_live_channel("1:2")
    assert tab.buffer.go_live_channels == [{"guild_id": "1", "channel_id": "3"}]


def test_chip_helpers_without_container(env):
    tab = loaded_tab(env)
    tab._rebuild_go_live_chips()
    tab._create_channel_chip({"guild_id": "1", "channel_id": "2"})
    tab._refresh_steps()


def test_insert_token(env):
    tab = tabmod.DiscordTab()
    tab._insert_token("{url}")  # no element
    el = SimpleNamespace(value=None)
    tab.ui_elements["go_live_message"] = el
    tab._insert_token("{url}")
    assert el.value == "{url}"
    tab._insert_token("{title}")
    assert el.value == "{url} {title}"
    el.value = "Live! "
    tab._insert_token("{platform}")
    assert el.value == "Live! {platform}"


# --------------------------------------------------------------------------- connect / disconnect (logic)


def test_connect_guards(env):
    tab = tabmod.DiscordTab()
    tab._connect_in_progress = True
    tab._handle_connect()
    tab._connect_in_progress = False
    tab._handle_connect()  # no buffer
    assert env.svc.connects == []


def test_connect_without_token_opens_fold(env):
    tab = loaded_tab(env)
    opened = []
    tab._fold = SimpleNamespace(open=lambda: opened.append(1), set_saved=lambda s: None)
    tab._handle_connect()
    assert titles(env) == ["Enter a Discord bot token first"] and opened == [1]
    tab._fold = None
    tab._handle_connect()
    assert env.svc.connects == []


def test_connect_save_failure_aborts(env):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = "tok"
    env.state.save_ok = False
    tab._handle_connect()
    assert env.svc.connects == [] and tab._connect_in_progress is False


def test_connect_success_flow(env):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = "tok"
    reloads = []
    env.mp.setattr(tab, "_reload_guilds", lambda report=False: reloads.append(report))
    tab.ui_elements["bot_token"] = SimpleNamespace(value="")
    env.state.data.bot_token = ""

    real_load = tab._load_from_state

    def load_wiping():
        real_load()
        tab._creds["bot_token"] = ""  # state returned blank token: tab keeps what it connected with

    env.mp.setattr(tab, "_load_from_state", load_wiping)
    tab._handle_connect()
    assert env.svc.connects == ["tok"] and tab._connect_in_progress is True
    fire(env)
    assert tab._connect_in_progress is False
    assert titles(env) == ["Discord bot connected"]
    assert tab.ui_elements["bot_token"].value == "tok" and reloads == [True]


def test_connect_poll_waits_until_done(env):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = "tok"
    pending = []
    env.mp.setattr(tabmod, "threading", SimpleNamespace(Thread=lambda target, daemon, name: SimpleNamespace(
        start=lambda: pending.append(target))))
    tab._handle_connect()
    poll = env.scheduled[-1]
    poll.fired = True
    poll.cb()  # worker not done -> reschedules
    assert env.scheduled[-1] is not poll and tab._connect_in_progress is True
    pending[0]()
    fire(env)
    assert tab._connect_in_progress is False


@pytest.mark.parametrize("result,details", [(False, "Discord didn't accept the connection."),
                                            (RuntimeError("socket"), "socket")])
def test_connect_failure_notice(env, result, details):
    tab = loaded_tab(env)
    tab._creds["bot_token"] = "tok"
    env.svc.connect_result = result
    tab._handle_connect()
    fire(env)
    assert titles(env) == ["Couldn't connect the Discord bot"]
    assert env.notes[0]["details"] == details


def test_disconnect_flow(env):
    tab = loaded_tab(env)
    btn = FakeButton()
    tab.ui_elements["disconnect_button"] = btn
    tab._handle_disconnect()
    assert btn.enabled is False and tab._disconnect_in_progress is True
    tab._handle_disconnect()  # in progress -> ignored
    fire(env)
    assert env.svc.disconnects == 1 and btn.enabled is True
    assert titles(env) == ["Discord bot disconnected"]


def test_disconnect_failure_and_button_errors(env):
    tab = loaded_tab(env)
    btn = FakeButton()
    btn.fail = True
    tab.ui_elements["disconnect_button"] = btn
    env.svc.disconnect_error = RuntimeError("stuck")
    tab._handle_disconnect()
    fire(env)
    assert titles(env) == ["Couldn't disconnect the Discord bot cleanly"]
    assert env.notes[0]["details"] == "stuck"


def test_disconnect_without_button_and_poll_wait(env):
    tab = loaded_tab(env)
    pending = []
    env.mp.setattr(tabmod, "threading", SimpleNamespace(Thread=lambda target, daemon, name: SimpleNamespace(
        start=lambda: pending.append(target))))
    tab._handle_disconnect()
    poll = env.scheduled[-1]
    poll.fired = True
    poll.cb()
    assert tab._disconnect_in_progress is True
    pending[0]()
    fire(env)
    assert tab._disconnect_in_progress is False and env.svc.disconnects == 1


# --------------------------------------------------------------------------- invite


def test_copy_invite(env):
    tab = loaded_tab(env)
    written = []
    env.mp.setattr(tabmod.ui.clipboard, "write", lambda text: written.append(text))
    tab._copy_invite_url()
    assert titles(env) == ["Connect the bot first"]
    env.svc.invite = "https://svc-invite"
    tab._copy_invite_url()
    assert written == ["https://svc-invite"] and titles(env)[-1] == "Invite link copied"
    tab._invite_url = " https://cached "
    tab._copy_invite_url()
    assert written[-1] == "https://cached"


def test_copy_invite_clipboard_failure(env):
    tab = loaded_tab(env)
    tab._invite_url = "https://x"
    env.mp.setattr(tabmod.ui.clipboard, "write", lambda text: (_ for _ in ()).throw(RuntimeError("no client")))
    tab._copy_invite_url()
    n = env.notes[-1]
    assert n["title"] == "Couldn't copy the invite link automatically" and n["message"] == "https://x"
    assert n["actions"] == [{"kind": "copy", "text": "https://x", "label": "Copy link"}]


def test_open_invite(env):
    tab = loaded_tab(env)
    opened = []
    env.mp.setattr(tabmod.ui.navigate, "to", lambda url, new_tab=False: opened.append((url, new_tab)))
    tab._open_invite()
    assert titles(env) == ["Connect the bot first"] and opened == []
    tab._invite_url = "https://cached"
    tab._open_invite()
    env.svc.invite = "https://svc"
    tab._open_invite()
    assert opened == [("https://cached", True), ("https://svc", True)]


def test_invite_row_copy_delegates(env):
    calls = []

    def build():
        row = tabmod._InviteRow(lambda: calls.append(1), "Invite link", empty="none")
        row.set("https://x")
        row._copy()

    run_ui(build)
    assert calls == [1]


# --------------------------------------------------------------------------- enter / exit / discard / mark clean


def test_on_enter_and_exit(env):
    tab = tabmod.DiscordTab()
    tab.on_enter()
    tab.on_exit()
    assert [t.interval for t in env.scheduled] == [0.05, 0.15]
    timer = SimpleNamespace(active=False)
    page_states = []
    tab._status_timer = timer
    tab._page = SimpleNamespace(set_active=lambda a: page_states.append(a))
    tab.on_enter()
    assert timer.active is True
    tab.on_exit()
    assert timer.active is False and page_states == [True, False]


def test_discard_without_ui_and_with_elements(env):
    tab = tabmod.DiscordTab()
    env.mp.setattr(tab, "_load_from_state", lambda: None)
    tab.discard()  # buffer stays None -> returns early
    tab = loaded_tab(env)
    env.state.data = DiscordData(bot_token="t", go_live_enabled=True, go_live_message="")
    els = {k: SimpleNamespace(value=None) for k in ("bot_token", "go_live_enabled", "go_live_message")}
    tab.ui_elements.update(els)
    tab.dirty = True
    tab.discard()
    assert els["bot_token"].value == "t" and els["go_live_enabled"].value is True
    assert els["go_live_message"].value == tabmod._DEFAULT_GO_LIVE_MESSAGE and tab.dirty is False


def test_mark_clean(env):
    tab = loaded_tab(env)
    tab.buffer.go_live_channels = [{"guild_id": "1", "channel_id": "2"}]
    tab.dirty = True
    tab.mark_clean()
    assert tab.has_unsaved_changes() is False


# --------------------------------------------------------------------------- full UI


def click_el(user, element, event_type):
    from nicegui import events

    with user.client:
        for listener in list(element._event_listeners.values()):
            if listener.type == event_type:
                events.handle_event(listener.handler, events.GenericEventArguments(
                    sender=element, client=user.client, args={}))


def _build(env, tab_holder):
    def build():
        tab = tabmod.DiscordTab()
        tab_holder.append(tab)
        tab.build(ui.column())

    return build


def test_build_disconnected_shows_setup(env):
    tabs: List[Any] = []

    async def check(user):
        await user.should_see("Go-live announcements from your own bot")
        await user.should_see("Paste the bot token, then connect")
        await user.should_see("Connect the bot to pick servers and channels.")
        await user.should_see("No channels yet. Pick a server and channel below.")
        await user.should_see("Connect to generate an invite link")
        tab = tabs[0]
        assert tab._connect_button.visible is True and tab._reconnect_button.visible is False
        assert tab.ui_elements["disconnect_button"].visible is False
        assert tab._add_row.visible is False and tab._fold.expansion.value is True
        assert tab.ui_elements["go_live_message"].value == "Stream is live on {platform}! {url}"
        assert env.scheduled[0].interval == 5.0 and env.scheduled[0].active is True

    run_ui(_build(env, tabs), check)


def test_build_connected_full_interaction(env):
    env.state.data = DiscordData(bot_token="tok", go_live_enabled=False, go_live_message="Hi {url}",
                                 go_live_channels=[{"guild_id": "1", "channel_id": "10", "guild_name": "Guild",
                                                    "channel_name": "general"}])
    env.svc.status = {"status": "Connected", "is_connected": True, "bot_username": "MyBot",
                      "bot_avatar_url": "https://cdn/a.png", "invite_url": "https://discord.com/invite"}
    env.svc.guilds = [{"id": "1", "name": "Guild"}]
    env.svc.channels = {"1": [{"id": "10", "name": "general"}, {"id": "11", "name": "news"}]}
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Bot connected")
        await user.should_see("MyBot")
        tab._refresh_status()  # the 5 s status timer: guild count now known
        await user.should_see("Connected · in 1 server")
        await user.should_see("https://discord.com/invite")
        await user.should_see("general")
        assert tab._connect_button.visible is False and tab._reconnect_button.visible is True
        assert tab._add_row.visible is True and tab._picker_hint.visible is False
        assert tab._guild_select.value == "1"
        assert tab._fold.expansion.value is False
        # switch toggles buffer + dirty
        user.find(ui.switch).click()
        assert tab.buffer.go_live_enabled is True and tab.dirty is True
        # message edit
        msg = tab.ui_elements["go_live_message"]
        msg.value = "New {title}"
        assert tab.buffer.go_live_message == "New {title}"
        msg.value = None
        assert tab.buffer.go_live_message == ""
        # placeholder token chip
        user.find("{platform}").trigger("click")
        assert tab.buffer.go_live_message == "{platform}"
        # token input
        tok = tab.ui_elements["bot_token"]
        tok.value = "newtoken"
        assert tab._creds["bot_token"] == "newtoken"
        # add a channel via the picker
        tab._channel_select.value = "11"
        user.find("Add").click()
        assert [c["channel_id"] for c in tab.buffer.go_live_channels] == ["10", "11"]
        await user.should_see("news")
        # remove the first chip
        close_buttons = sorted((b for b in user.find(ui.button).elements if b.props.get("icon") == "close"),
                               key=lambda b: b.id)
        click_el(user, close_buttons[0], "click.stop")
        assert [c["channel_id"] for c in tab.buffer.go_live_channels] == ["11"]
        # reload button reports errors
        env.svc.list_error = RuntimeError("gone")
        refresh = [b for b in user.find(ui.button).elements if b.props.get("icon") == "refresh"][0]
        click_el(user, refresh, "click")
        assert "Couldn't list your Discord servers" in titles(env)
        env.svc.list_error = None
        # save via header button
        user.find("Save").click()
        assert "Discord settings saved" in titles(env)
        assert ("go_live_enabled", True) in env.state.updates
        assert tab.has_unsaved_changes() is False

    run_ui(_build(env, tabs), check)


def test_build_discard_button_restores(env):
    env.state.data = DiscordData(bot_token="tok", go_live_message="Saved msg")
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.ui_elements["go_live_message"].value = "changed"
        assert tab.dirty is True
        user.find("Discard").click()
        assert tab.ui_elements["go_live_message"].value == "Saved msg"
        assert tab.dirty is False

    run_ui(_build(env, tabs), check)


def test_build_connect_button_runs_connect(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.ui_elements["bot_token"].value = "tok"
        user.find(kind=ui.button, content="Connect").click()
        assert env.svc.connects == ["tok"]
        assert tab._connect_button.text == "Connecting…"
        env.svc.status = {"status": "Connected", "is_connected": True, "bot_username": "B"}
        fire(env)
        assert "Discord bot connected" in titles(env)
        assert tab._reconnect_button.visible is True

    run_ui(_build(env, tabs), check)


def test_build_status_variants(env):
    env.state.data = DiscordData(bot_token="tok")
    env.svc.status = {"status": "Auth Failed", "is_connected": False}
    tabs: List[Any] = []

    async def check(user):
        await user.should_see("Discord rejected the token. Reset it in the Developer Portal, then connect.")
        tab = tabs[0]
        assert tab.ui_elements["disconnect_button"].visible is True
        env.svc.status = {"status": "Disconnected", "is_connected": False}
        tab._refresh_status()
        await user.should_see("Press Connect to start the bot")
        assert tab.ui_elements["disconnect_button"].visible is False

    run_ui(_build(env, tabs), check)

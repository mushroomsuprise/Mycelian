# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/streamlabs_tab.py (Settings → Streamlabs)."""

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

from modules.donation_settings import DonationSettings  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import integration_ui  # noqa: E402
from modules.uiwindows.tabs import streamlabs_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class Progress:
    def __init__(self, title, **kw):
        self.title = title
        self.kw = kw
        self.result: Any = None

    def done(self, title=None, **kw):
        self.result = ("done", title, kw)

    def fail(self, title=None, **kw):
        self.result = ("fail", title, kw)


class Env:
    def __init__(self) -> None:
        self.settings = DonationSettings(alert_currency="gbp")
        self.creds = {"client_id": " cid ", "client_secret": None}
        self.cred_updates: List[dict] = []
        self.updates: List[dict] = []
        self.notes: List[dict] = []
        self.progress: List[Progress] = []
        self.status = {"status": "Disconnected", "error": ""}
        self.oauth_result: Any = ""
        self.oauth_calls: List[tuple] = []
        self.disconnects = 0
        self.disconnect_error: Any = None
        self.save_error: Any = None
        self.threads: List[dict] = []
        self.scheduled: List[tuple] = []


class SyncThread:
    """Runs the target when started (the OAuth wait is faked anyway)."""

    def __init__(self, env, target=None, name=None, daemon=None):
        self.env, self.target = env, target
        env.threads.append({"name": name, "daemon": daemon})

    def start(self):
        self.target()


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "get_settings", lambda: e.settings)
    monkeypatch.setattr(tabmod, "get_streamlabs_credentials", lambda: dict(e.creds))

    def update_creds(**kw):
        if e.save_error:
            raise e.save_error
        e.cred_updates.append(kw)
        e.creds = dict(kw)

    monkeypatch.setattr(tabmod, "update_streamlabs_credentials", update_creds)
    monkeypatch.setattr(tabmod, "update_settings", lambda **kw: e.updates.append(kw))
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: e.notes.append({"message": message, **kw}))

    def progress(title, **kw):
        p = Progress(title, **kw)
        e.progress.append(p)
        return p

    monkeypatch.setattr(tabmod, "notify_progress", progress)
    monkeypatch.setattr(tabmod, "get_streamlabs_status", lambda: dict(e.status))

    def begin_oauth(cid, secret):
        e.oauth_calls.append((cid, secret))
        if isinstance(e.oauth_result, BaseException):
            raise e.oauth_result
        return e.oauth_result

    monkeypatch.setattr(tabmod, "get_streamlabs_client", lambda: SimpleNamespace(begin_oauth=begin_oauth))

    def disconnect():
        if e.disconnect_error:
            raise e.disconnect_error
        e.disconnects += 1

    monkeypatch.setattr(tabmod, "disconnect_streamlabs", disconnect)
    monkeypatch.setattr(tabmod, "layout_schedule", lambda *a, **k: e.scheduled.append((a, k)))
    monkeypatch.setattr(
        tabmod, "threading",
        SimpleNamespace(Thread=lambda target=None, name=None, daemon=None: SyncThread(e, target, name, daemon)),
    )
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    return e


# --------------------------------------------------------------------------- error text


@pytest.mark.parametrize(
    "error,title",
    [
        ("Authorization is already in progress.", "Streamlabs sign-in is already open"),
        ("OAuth callback port 9975 is in use.", "Another app is using the port Streamlabs sign-in needs (9975)"),
        ("Authorization was not completed.", "Streamlabs sign-in timed out"),
        ("access_denied", "Streamlabs sign-in was cancelled"),
        ("User denied", "Streamlabs sign-in was cancelled"),
        ("", "Couldn't connect to Streamlabs"),
        (None, "Couldn't connect to Streamlabs"),
        ("Streamlabs rejected the authorization code.", "Couldn't connect to Streamlabs"),
    ],
)
def test_streamlabs_error_text(error, title):
    got_title, hint = tabmod._streamlabs_error_text(error)
    assert got_title == title
    assert hint


# --------------------------------------------------------------------------- logic without UI


def test_load(env):
    tab = tabmod.StreamlabsTab()
    tab.dirty = True
    tab._load()
    assert tab._creds == {"client_id": "cid", "client_secret": ""}
    assert tab._creds_saved is False
    assert tab._currency == "GBP" and tab.dirty is False
    env.creds = {"client_id": "a", "client_secret": "b"}
    env.settings.alert_currency = "zzz"
    tab._load()
    assert tab._creds_saved is True and tab._currency == "USD"
    env.settings.alert_currency = None
    tab._load()
    assert tab._currency == "USD"


@pytest.mark.parametrize(
    "event,expected",
    [
        (SimpleNamespace(value="abc"), "abc"),
        (SimpleNamespace(value=("t",), args="x"), "x"),
        (SimpleNamespace(value=None, args=("a", "b")), "a"),
        (SimpleNamespace(value=None, args=None), ""),
    ],
)
def test_str_from_value_event(env, event, expected):
    assert tabmod.StreamlabsTab()._str_from_value_event(event) == expected


def test_set_cred_and_currency(env):
    tab = tabmod.StreamlabsTab()
    tab._set_cred("client_id", " id ")
    assert tab._creds["client_id"] == "id" and tab.dirty
    tab.dirty = False
    tab._set_cred("client_id", "id")
    assert tab.dirty is False
    tab._set_cred("client_secret", "sec")
    tab.dirty = False
    tab._set_cred("client_secret", "  ")  # blank never wipes a stored secret
    assert tab._creds["client_secret"] == "sec" and tab.dirty is False
    tab._set_cred("client_id", "")  # the id may be cleared
    assert tab._creds["client_id"] == "" and tab.dirty
    tab.dirty = False
    tab._set_currency("usd")
    assert tab.dirty is False
    tab._set_currency("eur")
    assert tab._currency == "EUR" and tab.has_unsaved_changes()
    tab._set_currency("")
    assert tab._currency == "USD"


def test_save_success_and_silent(env):
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "a", "client_secret": "b"}
    tab._currency = ""
    tab.dirty = True
    tab.save()
    assert env.cred_updates == [{"client_id": "a", "client_secret": "b"}]
    assert env.updates == [{"alert_currency": "USD"}]
    assert tab.dirty is False and tab._creds_saved is True
    assert env.notes[-1]["title"] == "Streamlabs settings saved"
    tab._creds = {"client_id": "a", "client_secret": ""}
    tab.save(silent=True)
    assert tab._creds_saved is False
    assert len(env.notes) == 1


def test_save_failure(env):
    env.save_error = OSError("disk")
    tab = tabmod.StreamlabsTab()
    tab.dirty = True
    tab.save()
    assert tab.dirty is True
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't save Streamlabs settings"
    assert note["details"] == "OSError: disk"


def test_discard_without_ui(env):
    env.creds = {"client_id": "stored", "client_secret": "s"}
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "edited", "client_secret": ""}
    tab.dirty = True
    tab.discard()
    assert tab._creds["client_id"] == "stored" and tab.dirty is False


@pytest.mark.parametrize(
    "status,error,saved,busy,expected",
    [
        ("Connected", "", False, False, ("ok", "Connected", "Connected", "Listening for donations", True)),
        ("Connected", "x", False, True, ("ok", "Connected", "Connected", "x", True)),
        ("Disconnected", "", True, True,
         ("busy", "Connecting", "Connecting", "Approve Mycelian in the Streamlabs page that opens", False)),
        ("Connecting", "Reconnecting", False, False, ("busy", "Connecting", "Connecting", "Reconnecting", False)),
        ("Disconnected", "why", False, False, ("off", "Not connected", "Not connected", "why", False)),
        ("Disconnected", "", True, False,
         ("off", "Not connected", "Not connected", "Press Connect to sign in to Streamlabs", False)),
        (None, "", False, False,
         ("off", "Not connected", "Not connected", "Add your app's client ID and secret, then connect", False)),
        ("Token expired", "", False, False, ("error", "Token expired", "Token expired", "Press Connect to try again", False)),
        ("Error", "boom", False, False, ("error", "Error", "Error", "boom", False)),
    ],
)
def test_status_mapping(env, status, error, saved, busy, expected):
    env.status = {"status": status, "error": error}
    tab = tabmod.StreamlabsTab()
    tab._creds_saved = saved
    tab._connect_in_progress = busy
    assert tab._status() == expected


def test_refresh_status_swallows_errors(env, monkeypatch):
    tab = tabmod.StreamlabsTab()
    tab._refresh_status()

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tabmod, "get_streamlabs_status", boom)
    tab._refresh_status()


def test_on_enter_exit(env):
    tab = tabmod.StreamlabsTab()
    tab.on_enter()
    tab.on_exit()
    assert env.scheduled[0][0][0] == 0.05 and env.scheduled[0][1] == {"once": True}
    tab._status_timer = SimpleNamespace(active=False)
    seen = []
    tab._page = SimpleNamespace(set_active=lambda a: seen.append(a))
    tab.on_enter()
    assert tab._status_timer.active is True
    tab.on_exit()
    assert tab._status_timer.active is False and seen == [True, False]


def test_connect_ignored_while_in_progress(env):
    tab = tabmod.StreamlabsTab()
    tab._connect_in_progress = True
    tab._connect()
    assert env.cred_updates == [] and env.progress == []


def test_connect_requires_credentials(env):
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "id", "client_secret": ""}
    tab._connect()
    assert env.notes[-1]["type"] == "warning"
    assert env.notes[-1]["title"] == "Enter your Streamlabs Client ID and Client Secret first"
    assert env.progress == [] and tab._connect_in_progress is False


def test_connect_success(env):
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "id", "client_secret": "sec"}
    tab._connect()
    assert env.oauth_calls == [("id", "sec")]
    (p,) = env.progress
    assert p.title == "Connecting to Streamlabs" and p.kw["source"] == "streamlabs"
    assert p.result == ("done", "Connected to Streamlabs", {})
    assert env.threads == [{"name": "StreamlabsOAuth", "daemon": True}]
    assert tab._connect_in_progress is False


def test_connect_oauth_error_maps_to_friendly_text(env):
    env.oauth_result = "access_denied"
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "id", "client_secret": "sec"}
    tab._connect()
    kind, title, kw = env.progress[0].result
    assert kind == "fail" and title == "Streamlabs sign-in was cancelled"
    assert kw["details"] == "access_denied" and kw["hint"]


def test_connect_oauth_exception(env):
    env.oauth_result = RuntimeError("socket died")
    tab = tabmod.StreamlabsTab()
    tab._creds = {"client_id": "id", "client_secret": "sec"}
    tab._connect()
    kind, title, kw = env.progress[0].result
    assert kind == "fail" and title == "Couldn't connect to Streamlabs"
    assert kw["details"] == "RuntimeError: socket died"
    assert tab._connect_in_progress is False


def test_disconnect_paths(env):
    tab = tabmod.StreamlabsTab()
    env.disconnect_error = RuntimeError("busy")
    tab._disconnect()
    assert env.notes[-1]["title"] == "Couldn't disconnect Streamlabs"
    assert env.notes[-1]["details"] == "RuntimeError: busy"
    env.disconnect_error = None
    tab._disconnect()
    assert env.disconnects == 1
    assert env.notes[-1]["title"] == "Streamlabs disconnected"


# --------------------------------------------------------------------------- built UI


def test_build_without_credentials(env):
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamlabsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        await user.should_see("Donations from Streamlabs as alerts")
        await user.should_see("Client ID")
        await user.should_see("Redirect URI")
        await user.should_see(tabmod.STREAMLABS_OAUTH_REDIRECT_URI)
        await user.should_see("dev.streamlabs.com")
        assert tab._fold.expansion.value is True
        assert "Needed to connect" in tab._fold._head.content
        assert "client ID and secret" in tab._card._who.content
        assert tab._disconnect_button.visible is False
        assert tab._connect_button.visible is True and tab._connect_button.text == "Connect"
        assert tab.ui_elements["client_id"].value == "cid"
        assert tab.ui_elements["currency"].value == "GBP"
        # edits
        tab.ui_elements["client_id"].value = "new-id"
        tab.ui_elements["client_secret"].value = "new-secret"
        tab.ui_elements["currency"].value = "EUR"
        assert tab._creds == {"client_id": "new-id", "client_secret": "new-secret"}
        assert tab._currency == "EUR" and tab.dirty
        # discard resets the fields
        tab.discard()
        assert tab.ui_elements["client_id"].value == "cid"
        assert tab.ui_elements["client_secret"].value == ""
        assert tab.ui_elements["currency"].value == "GBP"
        assert tab.dirty is False
        # while a sign-in is running the button says so and is disabled
        tab._connect_in_progress = True
        tab._refresh_status()
        assert tab._connect_button.text == "Connecting…" and not tab._connect_button.enabled
        assert tab._disconnect_button.visible is True  # busy is not "off"
        tab._connect_in_progress = False
        tab._refresh_status()
        assert tab._connect_button.text == "Connect" and tab._connect_button.enabled

    run_ui(build, check)


def test_build_connected_save_and_connect_click(env):
    env.creds = {"client_id": "id", "client_secret": "sec"}
    env.status = {"status": "Connected", "error": ""}
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamlabsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        assert tab._fold.expansion.value is False
        assert "Saved" in tab._fold._head.content
        assert "Listening for donations" in tab._card._who.content
        assert tab._connect_button.visible is False
        assert "is-ok" in tab._page._pills.content
        tab.ui_elements["currency"].value = "USD"
        user.find(kind=ui.button, content="Save").click()
        assert env.updates[-1] == {"alert_currency": "USD"}
        env.status = {"status": "Disconnected", "error": ""}
        tab._refresh_status()
        user.find(kind=ui.button, content="Connect").click()
        assert env.oauth_calls == [("id", "sec")]
        assert env.progress[-1].result[0] == "done"
        tab.on_exit()
        assert tab._status_timer.active is False

    run_ui(build, check)


def test_guide_dialog_and_register_hint(env, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url))
    monkeypatch.setattr(integration_ui.webbrowser, "open", lambda url, new=0: opened.append(url))
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamlabsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        user.find(kind=ui.label, content="dev.streamlabs.com").trigger("click")
        assert opened == [tabmod._REGISTER_URL]
        # the inline "How to connect" link in the hint opens the guide too
        user.find(kind=ui.label, content="How to connect").trigger("click")
        await user.should_see("Connect Streamlabs")
        await user.should_see("3. Use this redirect URI exactly")
        user.find(kind=ui.button, content="Streamlabs apps").click()
        assert opened[-1] == "https://streamlabs.com/dashboard#/apps"
        dialogs = user.find(kind=ui.dialog).elements
        assert len(dialogs) == 1 and next(iter(dialogs)).value is True
        user.find(kind=ui.button, content="Close").click()
        assert next(iter(dialogs)).value is False
        # the outline button opens a second guide
        user.find(kind=ui.button, content="How to connect").click()
        assert len(user.find(kind=ui.dialog).elements) == 2

    run_ui(build, check)


def test_disconnect_button_click(env):
    env.creds = {"client_id": "id", "client_secret": "sec"}
    env.status = {"status": "Connected", "error": ""}
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamlabsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        env.status = {"status": "Disconnected", "error": ""}
        for listener in tab._disconnect_button._event_listeners.values():
            listener.handler(SimpleNamespace())
        assert env.disconnects == 1
        assert tab._disconnect_button.visible is False
        assert env.notes[-1]["title"] == "Streamlabs disconnected"

    run_ui(build, check)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/streamelements_tab.py (Settings → StreamElements)."""

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
from modules.uiwindows.tabs import streamelements_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class Env:
    def __init__(self) -> None:
        self.settings = DonationSettings(alert_currency="eur", streamelements_jwt="  ")
        self.updates: List[dict] = []
        self.notes: List[dict] = []
        self.status = {"status": "Disconnected", "error": ""}
        self.connect_result = ""
        self.connected: List[str] = []
        self.disconnects = 0
        self.disconnect_error: Any = None
        self.update_error: Any = None
        self.scheduled: List[tuple] = []


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "get_settings", lambda: e.settings)

    def update(**kw):
        if e.update_error:
            raise e.update_error
        e.updates.append(kw)

    monkeypatch.setattr(tabmod, "update_settings", update)
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: e.notes.append({"message": message, **kw}))
    monkeypatch.setattr(tabmod, "get_streamelements_status", lambda: dict(e.status))

    def connect(jwt):
        e.connected.append(jwt)
        return e.connect_result

    monkeypatch.setattr(tabmod, "connect_streamelements", connect)

    def disconnect():
        if e.disconnect_error:
            raise e.disconnect_error
        e.disconnects += 1

    monkeypatch.setattr(tabmod, "disconnect_streamelements", disconnect)
    monkeypatch.setattr(tabmod, "layout_schedule", lambda *a, **k: e.scheduled.append((a, k)))
    monkeypatch.setattr(
        "modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help")
    )
    return e


# --------------------------------------------------------------------------- logic without UI


def test_load_normalises(env):
    tab = tabmod.StreamElementsTab()
    tab.dirty = True
    tab._load()
    assert tab._jwt == "" and tab._jwt_saved is False
    assert tab._currency == "EUR" and tab.dirty is False
    env.settings.alert_currency = "XYZ"
    env.settings.streamelements_jwt = " tok "
    tab._load()
    assert tab._currency == "USD" and tab._jwt == "tok" and tab._jwt_saved is True
    env.settings.alert_currency = ""
    env.settings.streamelements_jwt = None
    tab._load()
    assert tab._currency == "USD" and tab._jwt == ""


@pytest.mark.parametrize(
    "event,expected",
    [
        (SimpleNamespace(value="abc"), "abc"),
        (SimpleNamespace(value=5), "5"),
        (SimpleNamespace(value=["x"], args="from-args"), "from-args"),
        (SimpleNamespace(value=None, args=["first", "second"]), "first"),
        (SimpleNamespace(value=None, args=[]), ""),
        (SimpleNamespace(), ""),
    ],
)
def test_str_from_value_event(env, event, expected):
    assert tabmod.StreamElementsTab()._str_from_value_event(event) == expected


def test_set_jwt_and_currency(env):
    tab = tabmod.StreamElementsTab()
    tab._set_jwt("  ")
    assert tab.dirty is False
    tab._set_jwt(" new ")
    assert tab._jwt == "new" and tab.dirty is True
    tab.dirty = False
    tab._set_jwt("")  # blank never clears a stored token
    tab._set_jwt("new")
    assert tab._jwt == "new" and tab.dirty is False
    tab._set_currency("usd")
    assert tab.dirty is False
    tab._set_currency("gbp")
    assert tab._currency == "GBP" and tab.has_unsaved_changes() is True
    tab._set_currency(None)
    assert tab._currency == "USD"


def test_save_with_and_without_jwt(env):
    tab = tabmod.StreamElementsTab()
    tab._currency = "GBP"
    tab.dirty = True
    tab.save()
    assert env.updates == [{"alert_currency": "GBP"}]
    assert tab.dirty is False and tab._jwt_saved is False
    assert env.notes[-1]["title"] == "StreamElements settings saved"
    tab._jwt = "tok"
    tab._currency = ""
    tab.save(silent=True)
    assert env.updates[-1] == {"streamelements_jwt": "tok", "alert_currency": "USD"}
    assert tab._jwt_saved is True
    assert len(env.notes) == 1


def test_save_failure_reports_and_keeps_dirty(env):
    env.update_error = RuntimeError("db locked")
    tab = tabmod.StreamElementsTab()
    tab.dirty = True
    tab.save()
    assert tab.dirty is True
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't save StreamElements settings"
    assert note["details"] == "RuntimeError: db locked"
    assert note["actions"] == [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "Database"}]


@pytest.mark.parametrize(
    "status,error,saved,expected",
    [
        ("Connected", "", False, ("ok", "Connected", "Connected", "Listening for tips", True)),
        ("Connected", "note", False, ("ok", "Connected", "Connected", "note", True)),
        ("Connecting", "", False, ("busy", "Connecting", "Connecting", "Opening the StreamElements connection", False)),
        ("connecting", "Reconnecting", False, ("busy", "Connecting", "Connecting", "Reconnecting", False)),
        ("Disconnected", "gone", False, ("off", "Not connected", "Not connected", "gone", False)),
        ("Disconnected", "", True, ("off", "Not connected", "Not connected", "Press Connect to start listening for tips", False)),
        ("", "", False, ("off", "Not connected", "Not connected", "Paste your channel's JWT token, then connect", False)),
        ("Token expired", "", False, ("error", "Token expired", "Token expired", "Press Connect to try again", False)),
        ("Error", "bad", False, ("error", "Error", "Error", "bad", False)),
    ],
)
def test_status_mapping(env, status, error, saved, expected):
    env.status = {"status": status, "error": error}
    tab = tabmod.StreamElementsTab()
    tab._jwt_saved = saved
    assert tab._status() == expected


def test_refresh_status_without_ui_and_with_error(env, monkeypatch):
    tab = tabmod.StreamElementsTab()
    tab._refresh_status()  # nothing built: no-op

    def boom():
        raise RuntimeError("status down")

    monkeypatch.setattr(tabmod, "get_streamelements_status", boom)
    tab._refresh_status()  # swallowed


def test_on_enter_and_exit_without_build(env):
    tab = tabmod.StreamElementsTab()
    tab.on_enter()
    tab.on_exit()
    (args, kw), = env.scheduled
    assert args[0] == 0.05 and args[1] == tab._refresh_status and kw == {"once": True}


def test_on_enter_and_exit_toggle_timers(env):
    tab = tabmod.StreamElementsTab()
    tab._status_timer = SimpleNamespace(active=False)
    active = []
    tab._page = SimpleNamespace(set_active=lambda a: active.append(a))
    tab.on_enter()
    assert tab._status_timer.active is True
    tab.on_exit()
    assert tab._status_timer.active is False
    assert active == [True, False]


def test_connect_error_warns(env):
    env.connect_result = "Paste a StreamElements JWT first."
    tab = tabmod.StreamElementsTab()
    tab._connect()
    assert env.connected == [""]
    note = env.notes[-1]
    assert note["type"] == "warning" and note["title"] == "Paste a StreamElements JWT first."
    assert tab._jwt_saved is False


def test_connect_success(env):
    tab = tabmod.StreamElementsTab()
    tab._jwt = "tok"
    tab._connect()
    assert env.connected == ["tok"]
    assert tab._jwt_saved is True
    assert env.updates[-1]["streamelements_jwt"] == "tok"
    assert not any(n["type"] == "success" for n in env.notes)  # silent save


def test_disconnect_failure(env):
    env.disconnect_error = RuntimeError("nope")
    tab = tabmod.StreamElementsTab()
    tab._jwt = "tok"
    tab._disconnect()
    assert tab._jwt == "tok"
    assert env.notes[-1]["title"] == "Couldn't disconnect StreamElements"
    assert env.notes[-1]["details"] == "RuntimeError: nope"


def test_disconnect_success_without_ui(env):
    tab = tabmod.StreamElementsTab()
    tab._jwt, tab._jwt_saved, tab.dirty = "tok", True, True
    tab._disconnect()
    assert env.disconnects == 1
    assert (tab._jwt, tab._jwt_saved, tab.dirty) == ("", False, False)
    assert env.notes[-1]["title"] == "StreamElements disconnected"


def test_discard_without_ui(env):
    env.settings.streamelements_jwt = "stored"
    tab = tabmod.StreamElementsTab()
    tab._jwt = "edited"
    tab.dirty = True
    tab.discard()
    assert tab._jwt == "stored" and tab.dirty is False


# --------------------------------------------------------------------------- built UI


def test_build_not_connected_then_edit_and_connect(env):
    env.settings.streamelements_jwt = ""
    refs: Dict[str, Any] = {}

    def build():
        tab = tabmod.StreamElementsTab()
        refs["tab"] = tab
        tab.build(ui.column())

    async def check(user):
        tab = refs["tab"]
        await user.should_see("Tips from StreamElements as alerts")
        await user.should_see("JWT token")
        await user.should_see("Alert currency")
        assert "Paste your channel&#x27;s JWT token, then connect" in tab._card._who.content
        assert tab._fold.expansion.value is True  # opened while no JWT saved
        assert "Needed to connect" in tab._fold._head.content
        assert tab._disconnect_button.visible is False
        assert tab._connect_button.visible is True
        assert "is-error" in tab._page._pills.content and "Not connected" in tab._page._pills.content
        # typing a JWT marks the page dirty
        tab.ui_elements["jwt"].value = " my.jwt.token "
        assert tab._jwt == "my.jwt.token" and tab.dirty is True
        tab.ui_elements["currency"].value = "GBP"
        assert tab._currency == "GBP"
        tab._page.refresh_dirty()
        assert "Unsaved changes" in tab._page._dirty_tag.content
        # discard puts the stored values back in the fields
        tab.discard()
        assert tab.ui_elements["jwt"].value == ""
        assert tab.ui_elements["currency"].value == "EUR"
        assert tab.dirty is False
        # connect now: status flips to Connecting
        tab.ui_elements["jwt"].value = "tok"
        env.status = {"status": "Connecting", "error": ""}
        user.find(kind=ui.button, content="Connect").click()
        assert env.connected == ["tok"]
        assert "Opening the StreamElements connection" in tab._card._who.content
        assert tab._disconnect_button.visible is True
        assert "Saved" in tab._fold._head.content

    run_ui(build, check)


def test_build_connected_and_disconnect(env):
    env.settings.streamelements_jwt = "tok"
    env.status = {"status": "Connected", "error": ""}
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamElementsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        assert "Listening for tips" in tab._card._who.content
        assert tab._fold.expansion.value is False
        assert tab._connect_button.visible is False
        assert tab._disconnect_button.visible is True
        assert "is-ok" in tab._page._pills.content
        assert tab._status_timer is not None
        env.status = {"status": "Disconnected", "error": ""}
        tab._disconnect()
        assert env.disconnects == 1
        assert tab.ui_elements["jwt"].value == ""
        assert tab._disconnect_button.visible is False
        assert "Paste your channel&#x27;s JWT token" in tab._card._who.content
        tab.on_exit()
        assert tab._status_timer.active is False and tab._page._timer.active is False
        tab.on_enter()
        assert tab._status_timer.active is True

    run_ui(build, check)


def test_save_button_and_guide_dialog(env, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url))
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.StreamElementsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        tab.ui_elements["currency"].value = "GBP"
        user.find("Save").click()
        assert env.updates[-1] == {"alert_currency": "GBP"}
        assert env.notes[-1]["title"] == "StreamElements settings saved"
        user.find("How to connect").click()
        await user.should_see("Connect StreamElements")
        await user.should_see("4. Turn on Show secrets and copy the JWT token")
        user.find(kind=ui.button, content="Account → Channels").click()
        assert opened == ["https://streamelements.com/dashboard/account/channels"]
        (dialog,) = user.find(kind=ui.dialog).elements
        assert dialog.value is True
        user.find(kind=ui.button, content="Close").click()
        assert dialog.value is False

    run_ui(build, check)

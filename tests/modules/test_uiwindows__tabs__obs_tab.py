# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/obs_tab.py (Settings → OBS)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import connection_status_tracker  # noqa: E402
from modules.dataobjects import OBSData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.service_alerts import OBS_OPEN_HINT  # noqa: E402
from modules.uiwindows.tabs import obs_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeState:
    def __init__(self) -> None:
        self.data = OBSData(host="studio.local", port=4460, password="pw", enabled=True)
        self.set_calls: List[dict] = []
        self.save_ok = True
        self.set_error: Any = None

    def get_obs_data(self):
        return self.data

    def set_obs_data(self, payload):
        if self.set_error:
            raise self.set_error
        self.set_calls.append(dict(payload))

    def save_changes(self):
        return self.save_ok


class FakeObs:
    def __init__(self) -> None:
        self.phase = "disconnected"
        self.details = (False, "", "")
        self.auth = False
        self.applied = 0
        self.apply_error: Any = None
        self.snapshot_result = (True, "")
        self.snapshot_timeouts: List[float] = []

    def get_connection_phase(self):
        return self.phase

    def connection_details(self):
        return self.details

    def auth_failed(self):
        return self.auth

    def apply_settings(self):
        if self.apply_error:
            raise self.apply_error
        self.applied += 1

    def refresh_snapshot_blocking(self, timeout):
        self.snapshot_timeouts.append(timeout)
        return self.snapshot_result


class Progress:
    def __init__(self, title, **kw):
        self.title, self.kw = title, kw
        self.result: Any = None

    def done(self, title=None, **kw):
        self.result = ("done", title, kw)

    def fail(self, title=None, *args, **kw):
        self.result = ("fail", title, args, kw)

    def close(self):
        self.result = ("close",)


class Env:
    def __init__(self):
        self.state = FakeState()
        self.obs = FakeObs()
        self.notes: List[dict] = []
        self.progress: List[Progress] = []
        self.why = "Disconnected"
        self.scheduled: List[tuple] = []


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "state_manager", e.state)
    monkeypatch.setattr(tabmod, "obs_service", e.obs)
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: e.notes.append({"message": message, **kw}))

    def progress(title, **kw):
        p = Progress(title, **kw)
        e.progress.append(p)
        return p

    monkeypatch.setattr(tabmod, "notify_progress", progress)
    monkeypatch.setattr(connection_status_tracker, "obs_disconnected_status", lambda: e.why)

    def schedule(*a, **k):
        e.scheduled.append((a, k))
        return SimpleNamespace(active=k.get("active", True))

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    return e


def loaded_tab(env):
    tab = tabmod.ObsTab()
    tab._load_from_state()
    return tab


# --------------------------------------------------------------------------- state + status


def test_load_from_state_copies(env):
    tab = loaded_tab(env)
    assert tab.buffer == env.state.data
    assert tab.buffer is not env.state.data
    tab.buffer.host = "changed"
    assert env.state.data.host == "studio.local"
    assert tab.dirty is False


def test_set_marks_dirty_only_on_change(env):
    tab = tabmod.ObsTab()
    tab._set("host", "x")  # no buffer: ignored
    assert tab.dirty is False
    tab._load_from_state()
    tab._set("host", "studio.local")
    assert tab.dirty is False
    tab._set("host", "other")
    assert tab.buffer.host == "other" and tab.dirty is True


@pytest.mark.parametrize(
    "value,ok",
    [("4455", True), (" 1 ", True), (65535, True), ("0", False), ("65536", False), ("abc", False), (None, False)],
)
def test_port_ok(value, ok):
    assert tabmod.ObsTab._port_ok(value) is ok


def test_on_port(env):
    tab = loaded_tab(env)
    refreshed = []
    tab._refresh_status = lambda: refreshed.append(1)
    tab._on_port(SimpleNamespace(value="bad"))
    assert tab.buffer.port == 4460 and refreshed == []
    tab._on_port(SimpleNamespace(value=" 4470 "))
    assert tab.buffer.port == 4470 and tab.dirty and refreshed == [1]


def test_status_turned_off(env):
    tab = loaded_tab(env)
    tab.buffer.enabled = False
    assert tab._status()[:2] == ("off", "Turned off")


@pytest.mark.parametrize("phase,short", [("connecting", "Connecting"), ("disconnecting", "Disconnecting")])
def test_status_busy_phases(env, phase, short):
    env.obs.phase = phase
    assert tabmod.ObsTab()._status() == ("busy", short, f"{short} {'to' if phase == 'connecting' else 'from'} OBS…", [])


def test_status_connected_meta(env):
    env.obs.phase = "connected"
    env.obs.details = (True, "30.2.0", "5.5")
    kind, short, line, meta = tabmod.ObsTab()._status()
    assert (kind, short) == ("ok", "Connected")
    assert meta == [("OBS", "30.2.0", "", ""), ("WebSocket", "v5.5", "", "")]
    env.obs.details = (True, "", "")
    assert tabmod.ObsTab()._status()[3] == []


def test_status_wrong_password(env):
    env.obs.auth = True
    assert tabmod.ObsTab()._status()[:2] == ("error", "Wrong password")


@pytest.mark.parametrize(
    "why,kind,short",
    [("Not running", "off", "OBS not running"), ("Can't connect", "warn", "Can't connect"),
     ("Disconnected", "warn", "Not connected")],
)
def test_status_disconnected_reasons(env, why, kind, short):
    env.why = why
    assert tabmod.ObsTab()._status()[:2] == (kind, short)


def test_refresh_status_without_ui_and_errors(env, monkeypatch):
    tab = tabmod.ObsTab()
    tab._refresh_status()

    def boom():
        raise RuntimeError("x")

    env.obs.get_connection_phase = boom
    tab._refresh_status()  # swallowed


def test_refresh_status_card_defaults(env):
    tab = tabmod.ObsTab()
    pages, cards, metas = [], [], []
    tab._page = SimpleNamespace(set_status=lambda p: pages.append(p))
    tab._card = SimpleNamespace(set=lambda *a: cards.append(a), set_meta=lambda m: metas.append(m))
    env.why = "Not running"
    tab._refresh_status()
    assert pages == [[("off", "OBS not running")]]
    assert cards[-1][0] == "localhost:4455" and cards[-1][1] == "off"
    tab._load_from_state()
    tab.buffer.host, tab.buffer.port = "", 0
    env.obs.phase = "connecting"
    tab._refresh_status()
    assert cards[-1][:2] == ("localhost:4455", "busy")
    env.obs.phase = "disconnected"
    env.obs.auth = True
    tab._refresh_status()
    assert cards[-1][1] == "error"
    env.obs.auth = False
    env.why = "Can't connect"
    tab._refresh_status()
    assert cards[-1][1] == "warn"
    env.obs.phase, env.obs.details = "connected", (True, "30", "5")
    tab._refresh_status()
    assert cards[-1][1] == "ok" and metas[-1][0][0] == "OBS"


def test_on_enter_exit(env):
    tab = tabmod.ObsTab()
    tab.on_enter()
    tab.on_exit()
    assert env.scheduled[-1][0][0] == 0.05 and env.scheduled[-1][1] == {"once": True}
    tab._status_timer = SimpleNamespace(active=False)
    seen = []
    tab._page = SimpleNamespace(set_active=lambda a: seen.append(a))
    tab.on_enter()
    assert tab._status_timer.active is True
    tab.on_exit()
    assert tab._status_timer.active is False and seen == [True, False]


# --------------------------------------------------------------------------- save / discard


def test_save_without_buffer(env):
    tabmod.ObsTab().save()
    assert env.state.set_calls == []


def test_save_success(env):
    tab = loaded_tab(env)
    tab._set("password", "new")
    tab.save()
    assert env.state.set_calls[-1]["password"] == "new"
    assert set(env.state.set_calls[-1]) == set(OBSData.__dataclass_fields__)
    assert env.notes[-1]["title"] == "OBS settings saved"
    assert tab.dirty is False and env.obs.applied == 1


def test_save_failure_keeps_dirty(env):
    env.state.save_ok = False
    tab = loaded_tab(env)
    tab._set("password", "new")
    tab.save()
    assert tab.dirty is True and env.notes == [] and env.obs.applied == 0


def test_discard_without_ui(env):
    tab = tabmod.ObsTab()
    env.state.data = OBSData(host="h", port=None, password="", enabled=False)
    tab.ui_elements = {"host": SimpleNamespace(value="x"), "port": SimpleNamespace(value="1"),
                       "password": object(), "enabled": SimpleNamespace(value=True)}
    tab.dirty = True
    tab.discard()
    assert tab.ui_elements["host"].value == "h"
    assert tab.ui_elements["port"].value == "4455"
    assert tab.ui_elements["enabled"].value is False
    assert tab.dirty is False


def test_discard_bails_if_load_leaves_no_buffer(env):
    tab = tabmod.ObsTab()
    tab._load_from_state = lambda: None
    tab.dirty = True
    tab.discard()
    assert tab.dirty is True


# --------------------------------------------------------------------------- test connection


def run_test(tab):
    asyncio.run(tab._test_connection())


def test_test_connection_guards(env):
    tab = tabmod.ObsTab()
    run_test(tab)  # no buffer
    assert env.state.set_calls == []
    tab._load_from_state()
    tab._test_in_progress = True
    run_test(tab)
    assert env.state.set_calls == []


def test_test_connection_save_fails(env):
    env.state.save_ok = False
    tab = loaded_tab(env)
    run_test(tab)
    assert env.progress == [] and tab._test_in_progress is False
    assert env.state.set_calls[-1]["host"] == "studio.local"


def test_test_connection_ok(env):
    tab = loaded_tab(env)
    refreshed = []
    tab._refresh_status = lambda: refreshed.append(1)
    run_test(tab)
    (p,) = env.progress
    assert p.title == "Testing the OBS connection" and p.kw["message"] == "Waiting for OBS at studio.local:4460…"
    assert p.result == ("done", "Connected to OBS", {})
    assert env.obs.applied == 1 and env.obs.snapshot_timeouts == [25.0]
    assert refreshed == [1] and tab._test_in_progress is False


def test_test_connection_auth_failed_closes_progress(env):
    env.obs.snapshot_result = (False, "auth")
    env.obs.auth = True
    tab = loaded_tab(env)
    run_test(tab)
    assert env.progress[0].result == ("close",)


@pytest.mark.parametrize(
    "why,msg,title,details,hint",
    [
        ("Not running", "", "OBS isn't open", "No OBS process found on this computer.", "Open OBS, then test again."),
        ("Not running", "gone", "OBS isn't open", "gone", "Open OBS, then test again."),
        ("Can't connect", "", "OBS is open, but its WebSocket server isn't answering",
         "studio.local:4460: No answer within 25 seconds.", OBS_OPEN_HINT),
        ("Can't connect", "refused", "OBS is open, but its WebSocket server isn't answering",
         "studio.local:4460: refused", OBS_OPEN_HINT),
        ("Disconnected", "", "Couldn't reach OBS at studio.local:4460", "No answer within 25 seconds.", None),
        ("Disconnected", "timeout", "Couldn't reach OBS at studio.local:4460", "timeout", None),
    ],
)
def test_test_connection_failures(env, why, msg, title, details, hint):
    env.obs.snapshot_result = (False, msg)
    env.why = why
    tab = loaded_tab(env)
    run_test(tab)
    kind, got_title, args, kw = env.progress[0].result
    assert kind == "fail" and got_title == title
    assert kw["details"] == details
    if hint is not None:
        assert kw["hint"] == hint
    else:
        assert "Tools → WebSocket Server Settings" in kw["hint"]
    if why == "Not running":
        assert args == ("Mycelian connects to OBS on its own once it's running.",)


def test_test_connection_error_before_progress(env):
    env.state.set_error = RuntimeError("db gone")
    tab = loaded_tab(env)
    run_test(tab)
    assert env.progress == []
    note = env.notes[-1]
    assert note["type"] == "error" and note["title"] == "Couldn't test the OBS connection"
    assert note["details"] == "RuntimeError: db gone"
    assert tab._test_in_progress is False


def test_test_connection_error_after_progress(env):
    env.obs.apply_error = RuntimeError("queue full")
    tab = loaded_tab(env)
    run_test(tab)
    kind, title, _args, kw = env.progress[0].result
    assert kind == "fail" and title == "Couldn't test the OBS connection"
    assert kw["details"] == "RuntimeError: queue full"
    assert env.notes == []


# --------------------------------------------------------------------------- built UI


def test_build_and_edit_fields(env):
    env.obs.phase = "connected"
    env.obs.details = (True, "30.2", "5.5")
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.ObsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        tab = refs["tab"]
        await user.should_see("Scenes, sources and recording control over OBS WebSocket")
        await user.should_see("Enable OBS integration")
        await user.should_see("WebSocket password")
        await user.should_see("WebSocket checklist")
        assert "studio.local:4460" in tab._card._who.content
        assert "v5.5" in tab._card._meta.content
        assert "is-ok" in tab._page._pills.content
        assert env.scheduled[-1][0][0] == 5.0 and env.scheduled[-1][1] == {"active": True}
        assert tab.ui_elements["host"].value == "studio.local"
        assert tab.ui_elements["port"].value == "4460"
        # edits flow into the buffer
        tab.ui_elements["host"].value = " 10.0.0.2 "
        assert tab.buffer.host == "10.0.0.2" and tab.dirty
        tab.ui_elements["host"].value = None
        assert tab.buffer.host == ""
        tab.ui_elements["port"].value = "99999"
        assert tab.buffer.port == 4460
        tab.ui_elements["port"].value = "4470"
        assert tab.buffer.port == 4470
        assert tab.ui_elements["port"].validation["Enter a port from 1 to 65535"]("70000") is False
        tab.ui_elements["password"].value = "secret"
        assert tab.buffer.password == "secret"
        tab.ui_elements["password"].value = None
        assert tab.buffer.password == ""
        tab.ui_elements["enabled"].value = False
        assert tab.buffer.enabled is False
        assert "Turned off" in tab._page._pills.content
        # page dirty polling uses the tab flag
        tab._page.refresh_dirty()
        assert "Unsaved changes" in tab._page._dirty_tag.content
        # discard puts the stored values back
        user.find(kind=ui.button, content="Discard").click()
        assert tab.ui_elements["host"].value == "studio.local"
        assert tab.ui_elements["port"].value == "4460"
        assert tab.ui_elements["password"].value == "pw"
        assert tab.ui_elements["enabled"].value is True
        assert tab.dirty is False
        # save button
        tab.ui_elements["password"].value = "pw2"
        user.find(kind=ui.button, content="Save").click()
        assert env.state.set_calls[-1]["password"] == "pw2"
        assert env.obs.applied == 1

    run_ui(build, check)


def test_build_test_button_runs_connection_test(env):
    refs: Dict[str, Any] = {}

    def build():
        refs["tab"] = tabmod.ObsTab()
        refs["tab"].build(ui.column())

    async def check(user):
        user.find(kind=ui.button, content="Test").click()
        for _ in range(50):
            if env.progress and env.progress[0].result:
                break
            await asyncio.sleep(0.01)
        assert env.progress[0].result[0] == "done"

    run_ui(build, check)

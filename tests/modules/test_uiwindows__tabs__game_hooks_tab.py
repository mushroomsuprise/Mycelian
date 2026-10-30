# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/game_hooks_tab.py (Settings > Game Hooks cards, status, save)."""

from __future__ import annotations

import asyncio
import contextlib
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

from modules import game_hooks_service  # noqa: E402
from modules.game_hooks.base import HookUiMetadata  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import game_hooks_tab as ght  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture(autouse=True)
def _drop_simulated_clients():
    """run_ui leaves its client in Client.instances, which turns off NiceGUI's script-mode fallback for
    later tests that create UI outside a page; remove whatever this test added."""
    from nicegui import Client

    before = set(Client.instances)
    yield
    for key in [k for k in list(Client.instances) if k not in before]:
        Client.instances.pop(key, None)


# --------------------------------------------------------------------------- fakes


FF7 = HookUiMetadata("ff7", "Final Fantasy VII", frozenset({"windows"}))
FAC = HookUiMetadata("factorio", "Factorio", frozenset({"windows", "darwin", "linux"}))


class FakeDB:
    def __init__(self):
        self.store: Dict[str, Any] = {}
        self.writes: List = []
        self.ok = True
        self.raise_on_set = None

    def get_data(self, path):
        return self.store.get(path)

    def set_data(self, path, data):
        if self.raise_on_set:
            raise self.raise_on_set
        self.writes.append((path, data))
        if self.ok:
            self.store[path] = data
        return self.ok


class FakeEl:
    def __init__(self):
        self.cls = None
        self.content = None
        self.text = ""
        self.enabled = True
        self.props: Dict[str, Any] = {}
        self.html_id = "c1"
        self.value = None
        self.active = True

    def classes(self, add=None, *, replace=None):
        self.cls = replace if replace is not None else add
        return self

    def set_content(self, c):
        self.content = c

    def set_text(self, t):
        self.text = t

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False


class BrokenEl:
    """Every setter raises: refresh must swallow it."""

    html_id = "x"

    def __setattr__(self, k, v):
        raise RuntimeError("deleted element")

    def enable(self):
        raise RuntimeError("x")

    def disable(self):
        raise RuntimeError("x")

    def classes(self, *a, **k):
        raise RuntimeError("x")


@pytest.fixture
def env(monkeypatch):
    """Patch every collaborator the tab touches (DB, registry, service, notify, timers)."""
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    db = FakeDB()
    metas = [FF7, FAC]
    os_key = {"v": "darwin"}
    notes: List[Dict[str, Any]] = []
    cached: List = []
    schedules: List = []
    problem = {"v": None}
    actions = {"factorio": [], "ff7": None}
    snaps: Dict[str, Any] = {"service_running": True}
    service_error = {"v": None}

    class HookCls:
        process_names = ("game.exe",)

        def __init__(self, hid):
            self.hid = hid

    def make_cls(hid):
        acts = actions.get(hid)
        ns = {"process_names": ("factorio.exe",) if hid == "factorio" else ()}
        if acts is not None:
            def ui_actions(cls=None, _hid=hid):
                a = actions[_hid]
                if isinstance(a, Exception):
                    raise a
                return a
            ns["ui_actions"] = staticmethod(ui_actions)
        return type(f"Hook_{hid}", (), ns)

    monkeypatch.setattr(ght, "database_manager", db)
    monkeypatch.setattr(ght, "list_hooks_for_ui", lambda: list(metas))
    monkeypatch.setattr(ght, "get_hook_class", lambda hid: make_cls(hid) if hid in ("ff7", "factorio") else None)
    monkeypatch.setattr(ght, "runtime_os_key", lambda: os_key["v"])
    monkeypatch.setattr(ght, "notify", lambda msg, **kw: notes.append({"message": msg, **kw}))
    monkeypatch.setattr(ght, "nav_actions_settings", lambda name: [("nav", name)])
    monkeypatch.setattr(ght, "set_hook_enabled_cached", lambda hid, v: cached.append((hid, v)))
    monkeypatch.setattr(ght, "quiet_notifications", contextlib.nullcontext)
    monkeypatch.setattr(ght, "clear_last_write_problem", lambda: None)
    monkeypatch.setattr(ght, "last_write_problem", lambda: problem["v"])

    def fake_schedule(interval, cb, **kw):
        t = SimpleNamespace(active=kw.get("active", True), interval=interval, cb=cb, kw=kw)
        schedules.append(t)
        return t

    monkeypatch.setattr(ght, "layout_schedule", fake_schedule)

    def get_snapshot(hid):
        if service_error["v"]:
            raise service_error["v"]
        return {"service_running": snaps.get("service_running"), hid: snaps.get(hid)}

    monkeypatch.setattr(game_hooks_service.game_hooks_service, "get_hook_ui_snapshot", get_snapshot)
    monkeypatch.setattr(ght, "_HOOK_ERROR_TOASTED", {})
    ght._cover_data_uri.cache_clear()
    yield SimpleNamespace(db=db, metas=metas, os_key=os_key, notes=notes, cached=cached, schedules=schedules,
                          problem=problem, actions=actions, snaps=snaps, service_error=service_error)
    ght._cover_data_uri.cache_clear()


def _tab_with_fake_elements(env, hids=("ff7", "factorio")):
    tab = ght.GameHooksTab()
    tab._load_from_db()
    for hid in hids:
        tab.ui_elements[f"{hid}_card"] = FakeEl()
        tab.ui_elements[f"{hid}_line"] = FakeEl()
        tab.ui_elements[f"{hid}_line_tip"] = FakeEl()
        tab.ui_elements[f"{hid}_toggle"] = FakeEl()
    return tab


# --------------------------------------------------------------------------- module helpers


def test_cover_data_uri_real_asset_and_missing():
    ght._cover_data_uri.cache_clear()
    uri = ght._cover_data_uri("ff7")
    assert uri.startswith("data:image/jpeg;base64,")
    assert ght._cover_data_uri("no-such-game") is None
    ght._cover_data_uri.cache_clear()


@pytest.mark.parametrize(
    "title,expected",
    [("Final Fantasy VII", "FFV"), ("Factorio", "F"), ("", "?"), (None, "?"), ("A (B) C D", "ABC"), ("-- --", "?")],
)
def test_initials(title, expected):
    assert ght._initials(title) == expected


def test_is_ff7_hook_enabled(monkeypatch):
    monkeypatch.setattr(ght, "is_hook_enabled", lambda hid: hid == "ff7")
    assert ght.is_ff7_hook_enabled() is True


def test_os_labels():
    assert ght._OS_LABELS == {"windows": "Windows", "linux": "Linux", "darwin": "macOS"}


# --------------------------------------------------------------------------- pure methods


def test_load_from_db(env):
    env.db.store = {"GameHooks/ff7_enabled": True, "GameHooks/factorio_enabled": {"enabled": 1}}
    tab = ght.GameHooksTab()
    tab.dirty = True
    tab._load_from_db()
    assert tab._loaded_enabled == {"ff7": True, "factorio": True}
    assert tab._buffer_enabled == tab._loaded_enabled
    assert tab._hook_supported == {"ff7": False, "factorio": True}
    assert tab.dirty is False
    env.db.store = {"GameHooks/ff7_enabled": "junk"}
    tab._load_from_db()
    assert tab._loaded_enabled == {"ff7": False, "factorio": False}


def test_os_chips_html():
    html = ght.GameHooksTab._os_chips_html(frozenset({"windows"}))
    assert html.startswith('<div class="itg-os">')
    assert 'title="Windows: supported"' in html
    assert 'title="macOS: not supported"' in html and html.count("is-no") == 2
    all_html = ght.GameHooksTab._os_chips_html(frozenset({"windows", "linux", "darwin"}))
    assert "is-no" not in all_html and 'fill="#f5f5f7"' not in all_html


def test_truncate():
    assert ght.GameHooksTab._truncate("  short ") == "short"
    long = "x" * 200
    out = ght.GameHooksTab._truncate(long, 10)
    assert out == "x" * 9 + "…"


def test_is_unsupported(env):
    tab = ght.GameHooksTab()
    tab._hook_supported = {"ff7": False, "factorio": True}
    assert tab._is_unsupported("ff7") is True
    assert tab._is_unsupported("factorio") is False
    assert tab._is_unsupported("factorio", {"debug": {"stage": "unsupported_os"}}) is True
    assert tab._is_unsupported("factorio", {"error": "FF7 memory hook is only supported on Windows"}) is True
    assert tab._is_unsupported("factorio", {"error": RuntimeError("Requires Windows")}) is True
    assert tab._is_unsupported("factorio", {"error": None, "debug": "x"}) is False


@pytest.mark.parametrize(
    "snap,expected",
    [
        ({"disabled": True, "error": "boom"}, ("neutral", None)),
        ({"error": "Only supported on Windows"}, ("unsupported", None)),
        ({"debug": {"stage": "unsupported_os"}}, ("unsupported", None)),
        ({"attached": True}, ("success", None)),
        ({"attached": False}, ("warning", None)),
        ({"attached": True, "error": "   "}, ("warning", None)),
        ({"error": "Process not found"}, ("warning", None)),
        ({"error": "hook not attached"}, ("warning", None)),
        ({"error": ValueError("read failed")}, ("error", "read failed")),
        ({"error": "e" * 200}, ("error", "e" * 119 + "…")),
    ],
)
def test_state_from_snapshot(snap, expected):
    assert ght.GameHooksTab._state_from_snapshot(snap) == expected


def test_unsupported_and_waiting_text(env):
    tab = ght.GameHooksTab()
    assert tab._unsupported_text("ff7") == "Not available on macOS · Windows only"
    env.os_key["v"] = "other"
    assert tab._unsupported_text("factorio") == "Not available on this computer · macOS, Linux, Windows only"
    assert tab._unsupported_text("unknown") == "Not available on this computer"
    assert tab._waiting_text("factorio") == "Waiting for the game · looking for factorio.exe"
    assert tab._waiting_text("ff7") == "Waiting for the game to start"
    assert tab._waiting_text("unknown") == "Waiting for the game to start"


def test_apply_card_state(env):
    tab = _tab_with_fake_elements(env)
    tab._loaded_enabled["factorio"] = False
    tab._apply_card_state("factorio", "neutral")
    assert tab._card_state["factorio"] == ("neutral", "Off")
    card, line, tip = (tab.ui_elements[f"factorio_{k}"] for k in ("card", "line", "line_tip"))
    assert card.cls == "itg-hook is-neutral" and line.cls == "itg-hook-line is-neutral"
    assert line.content == '<span class="dot"></span><span>Off</span>' and tip.text == "Off"
    tab._loaded_enabled["factorio"] = True
    tab._apply_card_state("factorio", "neutral")
    assert tab._card_state["factorio"] == ("neutral", "Starting…")
    # unchanged state: no element update
    line.content = None
    tab._apply_card_state("factorio", "neutral", "Starting…")
    assert line.content is None
    tab._apply_card_state("factorio", "error", "<b>")
    assert "&lt;b&gt;" in line.content
    # no elements registered: state still recorded
    tab._apply_card_state("ghost", "success", "ok")
    assert tab._card_state["ghost"] == ("success", "ok")
    del tab.ui_elements["ff7_line_tip"]
    tab._apply_card_state("ff7", "warning", "w")
    assert tab.ui_elements["ff7_line"].cls == "itg-hook-line is-warning"


def test_refresh_header(env):
    tab = ght.GameHooksTab()
    tab._refresh_header()  # no page: nothing
    pills = []
    tab._page = SimpleNamespace(set_status=lambda p: pills.append(p))
    tab._refresh_header()
    assert pills[-1] == [("off", "No hooks on")]
    tab._loaded_enabled = {"ff7": True, "factorio": False}
    tab._hook_supported = {"ff7": False}
    tab._refresh_header()
    assert pills[-1] == [("off", "No hooks on")]  # enabled but unsupported does not count
    tab._loaded_enabled["factorio"] = True
    tab._refresh_header()
    assert pills[-1] == [("warn", "Waiting for a game")]
    tab._card_state = {"a": ("success", ""), "b": ("error", ""), "c": ("success", "")}
    tab._refresh_header()
    assert pills[-1] == [("ok", "2 games running"), ("error", "1 with a problem")]
    tab._card_state = {"a": ("success", "")}
    tab._refresh_header()
    assert pills[-1] == [("ok", "1 game running")]


def test_maybe_notify_error_rate_limited(env, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(ght.time, "monotonic", lambda: clock["t"])
    tab = ght.GameHooksTab()
    tab._maybe_notify_error("ff7", "boom", "FF7")
    assert len(env.notes) == 1
    n = env.notes[0]
    assert n["type"] == "warning" and n["details"] == "boom" and n["title"] == "The FF7 hook has a problem"
    assert n["dedupe_key"] == "gamehook:ff7:err" and n["actions"] == [("nav", "Game Hooks")]
    tab._maybe_notify_error("ff7", "boom", "FF7")  # same message: silent
    tab._maybe_notify_error("ff7", "boom 2", "FF7")  # new text but within gap: silent
    assert len(env.notes) == 1 and tab._last_error_notified["ff7"] == "boom 2"
    tab._maybe_notify_error("ff7", None, "FF7")
    assert tab._last_error_notified["ff7"] == ""
    tab._maybe_notify_error("ff7", None, "FF7")  # nothing to clear
    clock["t"] += ght._HOOK_ERROR_TOAST_GAP_SEC
    tab._maybe_notify_error("ff7", "boom 3", "FF7")
    assert len(env.notes) == 2


def test_state_text(env):
    tab = ght.GameHooksTab()
    assert tab._state_text("factorio", "success", None) == "Attached · reading live data"
    assert tab._state_text("factorio", "warning", None).startswith("Waiting for the game")
    assert tab._state_text("factorio", "error", "bad") == "Problem: bad"
    assert tab._state_text("ff7", "unsupported", None).startswith("Not available")
    assert tab._state_text("ff7", "neutral", None) == "Off"


# --------------------------------------------------------------------------- runtime status


def _status(tab, hid):
    return tab._card_state.get(hid)


def test_refresh_runtime_status_paths(env):
    tab = _tab_with_fake_elements(env)
    # ff7 unsupported on macOS
    tab._last_error_notified["ff7"] = "x"
    tab._refresh_runtime_status("ff7")
    assert _status(tab, "ff7")[0] == "unsupported" and "ff7" not in tab._last_error_notified

    # factorio: service raises
    env.service_error["v"] = RuntimeError("no service")
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("error", "Status unavailable")
    assert env.notes[-1]["details"] == "Status unavailable"
    assert env.notes[-1]["title"] == "The Factorio hook has a problem"
    env.service_error["v"] = None

    # snapshot says unsupported
    env.snaps["factorio"] = {"debug": {"stage": "unsupported_os"}}
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio")[0] == "unsupported"

    # disabled in settings
    env.snaps["factorio"] = {"attached": True}
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("neutral", "Off")

    tab._loaded_enabled["factorio"] = True
    env.snaps["service_running"] = False
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("warning", "Waiting for the game hooks service to start")

    env.snaps["service_running"] = True
    env.snaps["factorio"] = None
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("neutral", "Starting…")

    env.snaps["factorio"] = {"attached": True}
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("success", "Attached · reading live data")

    env.snaps["factorio"] = {"error": "mod crashed"}
    tab._refresh_runtime_status("factorio")
    assert _status(tab, "factorio") == ("error", "Problem: mod crashed")


def test_refresh_runtime_status_unknown_hook_title(env):
    tab = ght.GameHooksTab()
    tab._loaded_enabled["zzz"] = True
    env.snaps["zzz"] = {"error": "kaput"}
    tab._refresh_runtime_status("zzz")
    assert env.notes[-1]["title"] == "The zzz hook has a problem"


def test_refresh_all_runtime_status(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    seen = []
    monkeypatch.setattr(tab, "_refresh_hook_actions", lambda hid, actions=None: seen.append(hid))
    header = []
    monkeypatch.setattr(tab, "_refresh_header", lambda: header.append(1))
    tab._refresh_all_runtime_status()
    assert seen == ["ff7", "factorio"] and header == [1]


def test_refresh_async(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    applied = []
    monkeypatch.setattr(tab, "_refresh_hook_actions", lambda hid, actions=None: applied.append((hid, actions)))

    async def fake_io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(ght.run, "io_bound", fake_io_bound)
    env.actions["factorio"] = [{"id": "a"}]
    asyncio.run(tab._refresh_all_runtime_status_async())
    assert applied == [("ff7", []), ("factorio", [{"id": "a"}])]
    assert tab._refresh_inflight is False

    # in-flight guard
    tab._refresh_inflight = True
    applied.clear()
    asyncio.run(tab._refresh_all_runtime_status_async())
    assert applied == []
    tab._refresh_inflight = False

    async def none_io(fn, *args):
        return None

    monkeypatch.setattr(ght.run, "io_bound", none_io)
    asyncio.run(tab._refresh_all_runtime_status_async())
    assert applied == [("ff7", []), ("factorio", [])]

    async def boom(fn, *args):
        raise RuntimeError("pool gone")

    monkeypatch.setattr(ght.run, "io_bound", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(tab._refresh_all_runtime_status_async())
    assert tab._refresh_inflight is False


# --------------------------------------------------------------------------- actions


def test_hook_ui_actions(env):
    tab = ght.GameHooksTab()
    assert tab._hook_ui_actions("unknown") == []
    assert tab._hook_ui_actions("ff7") == []  # class has no ui_actions
    env.actions["factorio"] = ({"id": "x"},)
    assert tab._hook_ui_actions("factorio") == [{"id": "x"}]
    env.actions["factorio"] = None
    # ui_actions present but returns None
    env.actions["factorio"] = []
    assert tab._hook_ui_actions("factorio") == []
    env.actions["factorio"] = RuntimeError("scan failed")
    assert tab._hook_ui_actions("factorio") == []
    assert tab._collect_hook_ui_actions(["ff7"]) == {"ff7": []}


def test_detail_classes():
    assert ght.GameHooksTab._detail_classes("warning") == "itg-hook-detail is-warning"
    assert ght.GameHooksTab._detail_classes("ok") == "itg-hook-detail is-ok"
    assert ght.GameHooksTab._detail_classes("") == "itg-hook-detail"


def test_set_action_tooltip_reuses(monkeypatch):
    made = []

    class Tip(FakeEl):
        def __init__(self, text):
            super().__init__()
            self.text = text
            made.append(self)

    monkeypatch.setattr(ght.ui, "tooltip", Tip)
    tab = ght.GameHooksTab()
    btn = FakeEl()
    btn.html_id = "c9"
    tab._set_action_tooltip("h", "a", btn, "one")
    tab._set_action_tooltip("h", "a", btn, "one")
    tab._set_action_tooltip("h", "a", btn, "two")
    assert len(made) == 1 and made[0].props["target"] == "#c9" and made[0].text == "two"


def test_refresh_hook_actions_updates_elements(env, monkeypatch):
    tab = ght.GameHooksTab()
    btn, detail = FakeEl(), FakeEl()
    tab.ui_elements["factorio_action_inst"] = btn
    tab.ui_elements["factorio_action_inst_detail"] = detail
    tab.ui_elements["factorio_action_nodetail"] = FakeEl()
    tips = []
    monkeypatch.setattr(tab, "_set_action_tooltip", lambda *a: tips.append(a))
    env.actions["factorio"] = [
        "junk",
        {"id": "missing"},
        {"id": "inst", "label": "Update", "enabled": True, "run": lambda: None, "tooltip": "tip",
         "detail": " v1 ", "detail_tone": "warning"},
        {"id": "nodetail", "enabled": False},
    ]
    tab._refresh_hook_actions("factorio")
    assert btn.text == "Update" and btn.enabled is True
    assert detail.text == "v1" and detail.cls == "itg-hook-detail is-warning"
    assert tips == [("factorio", "inst", btn, "tip")]
    assert tab.ui_elements["factorio_action_nodetail"].enabled is False
    assert tab.ui_elements["factorio_action_nodetail"].text == "Action"
    # enabled but no callable run -> disabled
    tab._refresh_hook_actions("factorio", [{"id": "inst", "enabled": True, "run": None}])
    assert btn.enabled is False


def test_refresh_hook_actions_swallows_element_errors(env, monkeypatch):
    tab = ght.GameHooksTab()
    tab.ui_elements["f_action_a"] = BrokenEl()
    tab.ui_elements["f_action_a_detail"] = BrokenEl()

    def bad_tip(*a):
        raise RuntimeError("tip")

    monkeypatch.setattr(tab, "_set_action_tooltip", bad_tip)
    for enabled in (True, False):
        tab._refresh_hook_actions("f", [{"id": "a", "enabled": enabled, "run": print, "tooltip": "t", "detail": "d"}])


def test_run_hook_action_results(env, monkeypatch):
    tab = ght.GameHooksTab()
    refreshed = []
    monkeypatch.setattr(tab, "_refresh_hook_actions", lambda hid, actions=None: refreshed.append(hid))
    results = {"v": (True, "Installed")}
    calls = []

    def run():
        calls.append(1)
        r = results["v"]
        if isinstance(r, Exception):
            raise r
        return r

    env.actions["factorio"] = ["junk", {"id": "other", "run": run}, {"id": "inst", "label": "Install mod", "run": run}]
    tab._run_hook_action("factorio", "inst")
    assert env.notes[-1] == {"message": "Installed", "type": "success", "source": "game_hooks",
                             "title": "Factorio: Install mod"}
    assert refreshed == ["factorio"] and calls == [1]

    results["v"] = (False, "No folder")
    tab._run_hook_action("factorio", "inst")
    assert env.notes[-1]["type"] == "error" and env.notes[-1]["message"] == "No folder"
    assert "didn't work" in env.notes[-1]["title"]

    results["v"] = "plain string"
    tab._run_hook_action("factorio", "inst")
    assert env.notes[-1]["message"] == "plain string" and env.notes[-1]["type"] == "success"

    n = len(env.notes)
    for silent in ((True,), (True, None), None, ()):
        results["v"] = silent
        tab._run_hook_action("factorio", "inst")
    assert len(env.notes) == n

    results["v"] = OSError("disk")
    tab._run_hook_action("factorio", "inst")
    assert env.notes[-1]["details"] == "OSError: disk" and env.notes[-1]["type"] == "error"


def test_run_hook_action_not_callable_or_missing(env):
    tab = ght.GameHooksTab()
    env.actions["factorio"] = [{"id": "inst", "run": "nope"}]
    tab._run_hook_action("factorio", "inst")
    tab._run_hook_action("factorio", "missing")
    assert env.notes == []


def test_hook_title(env):
    assert ght.GameHooksTab._hook_title("ff7") == "Final Fantasy VII"
    assert ght.GameHooksTab._hook_title("zz") == "zz"


def test_on_toggle(env):
    tab = ght.GameHooksTab()
    tab._load_from_db()
    tab._on_toggle("ff7", True)  # unsupported here: ignored
    assert tab.dirty is False and tab._buffer_enabled["ff7"] is False
    tab._on_toggle("factorio", False)  # unchanged
    assert tab.dirty is False
    tab._on_toggle("factorio", True)
    assert tab.dirty is True and tab._buffer_enabled["factorio"] is True


def test_on_enter_exit(env):
    tab = ght.GameHooksTab()
    tab.on_enter()
    tab.on_exit()  # no timer/page yet
    assert env.schedules[-1].kw == {"once": True}
    active = []
    tab._status_timer = SimpleNamespace(active=False)
    tab._page = SimpleNamespace(set_active=lambda v: active.append(v))
    tab.on_enter()
    assert tab._status_timer.active is True and active == [True]
    tab.on_exit()
    assert tab._status_timer.active is False and active == [True, False]


# --------------------------------------------------------------------------- save / discard


def test_save_success(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: None)
    tab._buffer_enabled = {"ff7": True, "factorio": True}
    tab.dirty = True
    tab.save()
    assert env.db.writes == [("GameHooks/ff7_enabled", {"enabled": False}),
                             ("GameHooks/factorio_enabled", {"enabled": True})]
    assert env.cached == [("ff7", False), ("factorio", True)]
    assert tab._loaded_enabled == {"ff7": False, "factorio": True}
    assert tab.dirty is False
    assert env.notes[-1]["title"] == "Game Hooks saved"


def test_saved_toggle_is_not_unsaved_when_leaving_settings_tab(env, monkeypatch):
    """The page's Save calls save() directly, so the Settings field snapshot taken when
    the tab opened is never refreshed. The tab's own flag must decide instead."""
    from modules.uiwindows import settings as st

    class Switch:  # _is_settings_field matches on the class name
        def __init__(self, value):
            self.value = value

    tab = ght.GameHooksTab()
    tab._load_from_db()
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: None)
    tab.ui_elements["factorio_toggle"] = Switch(False)
    s = st.SettingsUI()
    s._tabs_by_name = {"Game Hooks": tab}
    s._snapshot_tab_fields(tab)  # taken when the tab is opened

    tab.ui_elements["factorio_toggle"].value = True
    tab._on_toggle("factorio", True)
    assert s._tab_is_dirty("Game Hooks") is True
    tab.save()
    assert s._tab_is_dirty("Game Hooks") is False
    assert s.has_unsaved_changes() is False


def test_save_superseded_counts_as_ok(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: None)
    env.db.ok = False
    env.problem["v"] = ("superseded", "")
    tab.save()
    assert len(env.cached) == 2 and env.notes[-1]["title"] == "Game Hooks saved"


def test_save_failure_reports_each_hook(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: None)
    tab.dirty = True
    env.db.ok = False
    env.problem["v"] = ("offline", "no connection")
    tab.save()
    n = env.notes[-1]
    assert n["type"] == "error" and n["title"] == "Couldn't save Game Hooks settings"
    assert n["details"] == "Final Fantasy VII: offline - no connection\nFactorio: offline - no connection"
    assert n["actions"] == [("nav", "Database")]
    assert tab.dirty is True and env.cached == []
    env.problem["v"] = None
    tab.save()
    assert env.notes[-1]["details"] == "Final Fantasy VII: failed\nFactorio: failed"


def test_save_exception(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    refreshed = []
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: refreshed.append(1))
    env.db.raise_on_set = ValueError("bad")
    tab.save()
    assert env.notes[-1]["details"] == "ValueError: bad"
    assert refreshed == [1]


def test_discard(env, monkeypatch):
    tab = _tab_with_fake_elements(env)
    monkeypatch.setattr(tab, "_refresh_all_runtime_status", lambda: None)
    env.db.store = {"GameHooks/ff7_enabled": True, "GameHooks/factorio_enabled": True}
    tab._buffer_enabled["factorio"] = False
    tab.dirty = True
    del tab.ui_elements["ff7_toggle"]
    tab.discard()
    assert tab.dirty is False
    assert tab.ui_elements["factorio_toggle"].value is True
    tab.ui_elements["ff7_toggle"] = FakeEl()
    tab.discard()
    assert tab.ui_elements["ff7_toggle"].value is False  # unsupported here


# --------------------------------------------------------------------------- full UI build


def _build_tab(holder):
    def build():
        tab = ght.GameHooksTab()
        holder["tab"] = tab
        tab.build(ui.column())

    return build


def test_build_renders_cards(env):
    env.db.store = {"GameHooks/factorio_enabled": True}
    env.actions["factorio"] = [
        {"id": "install_mod", "label": "Install mod", "tooltip": "Copy the mod", "enabled": True,
         "run": lambda: (True, "ok"), "detail": "not installed", "detail_tone": "muted"},
        {"id": "noop", "label": "Disabled", "enabled": False},
        "junk",
    ]
    holder: Dict[str, Any] = {}

    async def check(user):
        await user.should_see("Game Hooks")
        await user.should_see("Final Fantasy VII")
        await user.should_see("Factorio")
        await user.should_see("2 games")
        await user.should_see("Install mod")
        await user.should_see("not installed")
        tab = holder["tab"]
        assert tab._card_state["ff7"] == ("unsupported", "Not available on macOS · Windows only")
        assert tab._card_state["factorio"] == ("neutral", "Starting…")
        assert tab.ui_elements["ff7_toggle"].enabled is False
        assert tab.ui_elements["factorio_toggle"].value is True
        assert tab.ui_elements["factorio_action_noop"].enabled is False
        assert tab.ui_elements["factorio_action_install_mod"].enabled is True
        assert "factorio_action_install_mod_tooltip" in tab.ui_elements
        # timers: repeating status refresh + one-shot initial refresh
        assert env.schedules[0].interval == 0.5 and env.schedules[0].kw == {"active": True}
        assert env.schedules[1].kw == {"once": True}
        assert tab._status_timer is env.schedules[0]
        # clicking the action runs it and notifies
        user.find("Install mod").click()
        assert env.notes[-1]["message"] == "ok"
        # toggling the factorio switch marks the tab dirty
        user.find(kind=ui.switch).trigger("update:modelValue", False)
        assert tab.dirty is True and tab._buffer_enabled["factorio"] is False
        # the async refresh runs against the live page
        env.snaps["factorio"] = {"attached": False}
        tab._loaded_enabled["factorio"] = True
        await tab._refresh_all_runtime_status_async()
        assert tab._card_state["factorio"][1] == "Waiting for the game · looking for factorio.exe"

    run_ui(_build_tab(holder), check)


def test_build_single_hook_without_cover_on_windows(env, monkeypatch):
    env.metas[:] = [HookUiMetadata("zz", "Zany Zone", frozenset({"windows"}))]
    env.os_key["v"] = "windows"
    holder: Dict[str, Any] = {}

    async def check(user):
        await user.should_see("1 game")
        await user.should_see("ZZ")  # initials instead of a cover image
        tab = holder["tab"]
        assert tab.ui_elements["zz_toggle"].enabled is True
        assert "zz_action_" not in " ".join(tab.ui_elements)

    run_ui(_build_tab(holder), check)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/psn_tab.py (Settings → PSN): helpers, status, settings save, the
game cache editor and the built UI. The NPSSO sign-in flow is in test_uiwindows__tabs__psn_tab_part2.py."""

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
import contextlib  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import dataobjects  # noqa: E402
from modules.dataobjects import PSNSettingsData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.psn_data import PSNData, PSNGameMismatch  # noqa: E402
from modules.uiwindows.tabs import psn_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeState:
    def __init__(self) -> None:
        self.settings = PSNSettingsData()
        self.live: Any = None
        self.updates: List[tuple] = []
        self.saves = 0
        self.save_ok = True

    def get_psn_settings_data(self):
        return self.settings

    def get_live_psn_data(self):
        return self.live

    def update_psn_setting(self, key, value):
        self.updates.append((key, value))
        setattr(self.settings, key, value)

    def save_changes(self):
        self.saves += 1
        return self.save_ok


class FakeDBM:
    def __init__(self) -> None:
        self.problem: Any = None
        self.quiet = 0

    @contextlib.contextmanager
    def quiet_notifications(self):
        self.quiet += 1
        yield

    def clear_last_write_problem(self):
        pass

    def last_write_problem(self):
        return self.problem


class FakeWork:
    def __init__(self, title, **kw):
        self.title = title
        self.kw = kw
        self.events: List[tuple] = []

    def close(self):
        self.events.append(("close",))

    def done(self, title, message=""):
        self.events.append(("done", title))

    def fail(self, title, **kw):
        self.events.append(("fail", title, kw))


class FakeButton:
    def __init__(self, text="Connect to PSN"):
        self.text = text
        self.enabled = True
        self._props: Dict[str, Any] = {}

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def set_text(self, t):
        self.text = t

    def props(self, add=None, *, remove=None):
        if add:
            self._props[add] = True
        if remove:
            self._props.pop(remove, None)
        return self


def games():
    return [
        {"np_communication_id": "NPWR10_00", "trophy_name": "Game 10", "presence_name": "Game Ten",
         "platform": "PS5", "np_title_id": "PPSA10"},
        {"np_communication_id": "NPWR2_00", "trophy_name": "Game 2", "platform": "PS4",
         "cover_art_url": "https://img/2.png", "last_updated": "2026-01-01"},
        {"np_communication_id": "NPWR3_00", "presence_name": "Astro"},
    ]


@pytest.fixture
def env(monkeypatch):
    st = FakeState()
    dbm = FakeDBM()
    notes: List[dict] = []
    scheduled: List[Any] = []
    progress: List[FakeWork] = []
    cache = {"docs": games(), "load_error": None, "update_ok": True, "delete_ok": True,
             "updates": [], "deletes": [], "update_error": None}
    service = SimpleNamespace(display=("Not Connected", "N/A", "text-theme-error"), changes=0, change_error=None)

    def display():
        if isinstance(service.display, BaseException):
            raise service.display
        return service.display

    def change():
        service.changes += 1
        if service.change_error:
            raise service.change_error

    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "state_manager", st)
    monkeypatch.setattr(dataobjects, "state_manager", st)
    monkeypatch.setattr(tabmod, "_dbm", dbm)
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append({"message": message, **kw}))

    def notify_progress(title, **kw):
        w = FakeWork(title, **kw)
        progress.append(w)
        return w

    monkeypatch.setattr(tabmod, "notify_progress", notify_progress)
    monkeypatch.setattr(tabmod, "psn_service", SimpleNamespace(get_psn_status_display=display,
                                                               handle_psn_settings_change=change))

    def schedule(interval, cb, *, once=False, active=True):
        t = SimpleNamespace(interval=interval, cb=cb, once=once, active=active, cancelled=False)
        t.cancel = lambda: setattr(t, "cancelled", True)
        scheduled.append(t)
        return t

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)

    def load():
        if cache["load_error"]:
            raise cache["load_error"]
        return [dict(d) for d in cache["docs"]]

    def update(cid, upd):
        if cache["update_error"]:
            raise cache["update_error"]
        cache["updates"].append((cid, dict(upd)))
        if cache["update_ok"]:
            for d in cache["docs"]:
                if d["np_communication_id"] == cid:
                    d.update(upd)
        return cache["update_ok"]

    def delete(cid):
        cache["deletes"].append(cid)
        if cache["delete_ok"]:
            cache["docs"] = [d for d in cache["docs"] if d["np_communication_id"] != cid]
        return cache["delete_ok"]

    monkeypatch.setattr(tabmod, "load_all_psn_game_cache_docs_from_db", load)
    monkeypatch.setattr(tabmod, "update_psn_game_cache_in_db", update)
    monkeypatch.setattr(tabmod, "delete_psn_game_cache_in_db", delete)

    async def io_bound(fn, *a):
        return fn(*a)

    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=io_bound))
    tasks: List[Any] = []
    import nicegui.background_tasks as bt

    real_create = bt.create

    def create(*args, name="unnamed task", **kw):
        if name == "psn_save_settings_change":
            tasks.append(args[0] if args else kw.get("coroutine"))
            return None
        return real_create(*args, name=name, **kw)

    monkeypatch.setattr(bt, "create", create)
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    return SimpleNamespace(state=st, dbm=dbm, notes=notes, scheduled=scheduled, progress=progress, cache=cache,
                           service=service, tasks=tasks, mp=monkeypatch)


def titles(env):
    return [n.get("title") for n in env.notes]


def run_tasks(env):
    while env.tasks:
        asyncio.run(env.tasks.pop(0))


class Val:
    def __init__(self, value=""):
        self.value = value


def tab_with_cache_fields(env):
    tab = tabmod.PSNTab()
    tab._load_from_state()
    tab.ui_elements["cache_presence_name"] = Val()
    tab.ui_elements["cache_np_title_id"] = Val()
    tab.ui_elements["cache_save_btn"] = FakeButton()
    tab._load_cached_games()
    tab._reindex_cache()
    return tab


# --------------------------------------------------------------------------- module helpers


def test_cache_write_ok_and_failures(env):
    assert tabmod._cache_write(lambda *a: True, "x") == (True, "")
    assert env.dbm.quiet == 1
    assert tabmod._cache_write(lambda *a: False) == (False, "Not written (the cached game couldn't be found or read).")
    env.dbm.problem = ("HTTP 500", "server")
    assert tabmod._cache_write(lambda: 0) == (False, "HTTP 500 - server")
    env.dbm.problem = ("Timeout", "")
    assert tabmod._cache_write(lambda: None) == (False, "Timeout")


def test_notify_cache_write_failed(env):
    tabmod._notify_cache_write_failed("T", "")
    tabmod._notify_cache_write_failed("T2", "d")
    assert env.notes[0]["details"] is None and env.notes[1]["details"] == "d"
    assert env.notes[0]["type"] == "error" and env.notes[0]["actions"] == tabmod.nav_actions_settings("Database")


def test_npsso_trace_prints(capsys):
    tabmod._npsso_trace("hello")
    out = capsys.readouterr().out
    assert "[NPSSO_TRACE]" in out and "hello" in out and "thread=" in out


@pytest.mark.parametrize("game,name", [
    ({"trophy_name": "T", "presence_name": "P"}, "T"),
    ({"presence_name": "P", "np_communication_id": "N"}, "P"),
    ({"np_communication_id": "N"}, "N"),
    ({}, "Unknown"),
])
def test_game_name(game, name):
    assert tabmod._game_name(game) == name


def test_game_sort_key_natural_order():
    names = ["Game 10", "game 2", "Alpha", "Game 1b"]
    ordered = sorted(({"trophy_name": n} for n in names), key=tabmod._game_sort_key)
    assert [g["trophy_name"] for g in ordered] == ["Alpha", "Game 1b", "game 2", "Game 10"]


def test_game_search_key():
    g = {"trophy_name": "T", "presence_name": None, "platform": "PS5", "np_communication_id": "NPWR1",
         "np_title_id": "PPSA1"}
    assert tabmod._game_search_key(g) == "t ps5 npwr1 ppsa1"


@pytest.mark.parametrize(
    "event,expected",
    [
        (SimpleNamespace(value="abc"), "abc"),
        (SimpleNamespace(value=3), "3"),
        (SimpleNamespace(value=("x",), args="whole"), "whole"),
        (SimpleNamespace(value=None, args=["a", "b"]), "a"),
        (SimpleNamespace(value=None, args=()), ""),
        (SimpleNamespace(), ""),
    ],
)
def test_str_from_value_event(event, expected):
    assert tabmod.PSNTab._str_from_value_event(event) == expected


# --------------------------------------------------------------------------- enter / exit / load / set


def test_on_enter_exit(env):
    tab = tabmod.PSNTab()
    tab.on_enter()
    tab.on_exit()
    assert [t.interval for t in env.scheduled] == [0.05, 0.2]
    st, mm = SimpleNamespace(active=False), SimpleNamespace(active=False)
    active = []
    tab._status_timer, tab._mismatch_timer = st, mm
    tab._page = SimpleNamespace(set_active=lambda a: active.append(a))
    tab.on_enter()
    assert st.active and mm.active
    tab.on_exit()
    assert not st.active and not mm.active and active == [True, False]


def test_load_and_set(env):
    env.state.settings = PSNSettingsData(npsso_code="tok", psn_username="pal")
    tab = tabmod.PSNTab()
    tab._load_from_state()
    assert tab.buffer == env.state.settings and tab.buffer is not env.state.settings
    tab._set("psn_username", "pal")
    assert tab.dirty is False
    tab._set("psn_username", "other")
    assert tab.dirty is True and tab.buffer.psn_username == "other"
    tab.dirty = False
    tab._suppress_dirty = True
    tab._set("npsso_code", "x")
    assert tab.dirty is False


# --------------------------------------------------------------------------- persist / save / discard


def test_persist_without_buffer(env):
    assert tabmod.PSNTab()._persist_psn_settings() is None


def test_persist_reads_inputs(env):
    tab = tabmod.PSNTab()
    tab._load_from_state()
    tab.ui_elements["npsso_code"] = Val("typed")
    tab.ui_elements["psn_username"] = Val(None)
    tab.ui_elements["no_value"] = object()
    assert tab._persist_psn_settings() is True
    assert env.state.updates == [("npsso_code", "typed"), ("psn_username", "")]


def test_save_success_runs_service_and_notifies(env):
    tab = tabmod.PSNTab()
    tab._load_from_state()
    tab.dirty = True
    tab.save()
    assert len(env.tasks) == 1
    run_tasks(env)
    assert env.service.changes == 1 and tab.dirty is False
    assert titles(env) == ["PSN settings saved"]
    tab.save(suppress_saved_notification=True)
    run_tasks(env)
    assert titles(env) == ["PSN settings saved"]


def test_save_service_failure_warns(env):
    tab = tabmod.PSNTab()
    tab._load_from_state()
    tab.dirty = True
    env.service.change_error = RuntimeError("no net")
    tab.save()
    run_tasks(env)
    assert titles(env) == ["PSN settings saved, but PSN couldn't reconnect"]
    assert env.notes[0]["details"] == "RuntimeError: no net" and tab.dirty is True


def test_save_no_buffer_or_failed_save(env):
    tab = tabmod.PSNTab()
    tab.save()
    assert env.tasks == []
    tab._load_from_state()
    env.state.save_ok = False
    tab.save()
    assert env.tasks == []


def test_save_includes_pending_game_edit(env):
    tab = tab_with_cache_fields(env)
    tab._on_game_selected("NPWR2_00")
    tab.ui_elements["cache_presence_name"].value = "Renamed"
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is True
    tab.save()
    assert env.cache["updates"] == [("NPWR2_00", {"presence_name": "Renamed"})]
    run_tasks(env)


def test_discard_restores_inputs_and_game_fields(env):
    env.state.settings = PSNSettingsData(npsso_code="tok", psn_username="")
    tab = tab_with_cache_fields(env)
    tab.ui_elements["npsso_code"] = Val("changed")
    tab.ui_elements["psn_username"] = Val("x")
    tab._field_snapshot = {}
    tab._on_game_selected("NPWR10_00")
    tab.ui_elements["cache_presence_name"].value = "edited"
    tab._on_cache_field_changed()
    tab.dirty = True
    tab.discard()
    assert tab.ui_elements["npsso_code"].value == "tok" and tab.ui_elements["psn_username"].value == ""
    assert tab.ui_elements["cache_presence_name"].value == "Game Ten"
    assert tab._field_snapshot == {"cache_presence_name": "Game Ten", "cache_np_title_id": "PPSA10"}
    assert tab.dirty is False and tab._game_cache_dirty is False
    assert tab.ui_elements["cache_save_btn"].enabled is False
    assert tab._suppress_dirty is False


def test_mark_clean_without_button(env):
    tab = tabmod.PSNTab()
    tab.dirty = tab._game_cache_dirty = True
    tab.mark_clean()
    assert not tab.has_unsaved_changes()


# --------------------------------------------------------------------------- status


def test_status_not_connected_without_code(env):
    tab = tabmod.PSNTab()
    assert tab._status() == ("off", "Not connected", "Not signed in", "off", "Connect to PSN to sign in", [])


def test_status_error_with_code(env):
    env.state.settings.npsso_code = "tok"
    env.service.display = ("Token Expired", "N/A", "text-theme-error")
    assert tabmod.PSNTab()._status() == ("error", "Token Expired", "PlayStation account", "error",
                                         "Token Expired", [])


def test_status_connected_own_account_playing(env):
    env.state.settings.npsso_code = "tok"
    env.service.display = ("Connected as me", "me (own account)", "text-theme-success")
    env.state.live = PSNData(online_id="me", current_game_name="Returnal")
    assert tabmod.PSNTab()._status() == (
        "ok", "Connected", "me", "ok", "Connected as me",
        [("Tracking", "me", "own account", ""), ("Now playing", "Returnal", "", "")])


def test_status_offline_tracking_target(env):
    env.state.settings.npsso_code = "tok"
    env.service.display = ("Offline - Tracking pal", "pal (target)", "text-theme-warning")
    assert tabmod.PSNTab()._status() == ("warn", "Offline", "pal", "warn", "Offline · Tracking pal",
                                         [("Tracking", "pal", "tracking target", "")])


def test_status_connected_unknown_user(env):
    env.state.settings.npsso_code = "tok"
    env.service.display = ("Connected", None, "text-theme-success")
    assert tabmod.PSNTab()._status()[2] == "PlayStation account"


def test_refresh_status_without_ui_and_error_path(env):
    tab = tabmod.PSNTab()
    tab._refresh_status()
    assert tab._connected is False
    env.state.settings.npsso_code = "tok"
    env.service.display = ("Connected", "me (own account)", "text-theme-success")
    tab._refresh_status()
    assert tab._connected is True
    env.service.display = RuntimeError("svc")
    tab._refresh_status()
    assert tab._connected is False


def test_refresh_status_error_updates_page_and_card(env):
    tab = tabmod.PSNTab()
    calls = []
    tab._page = SimpleNamespace(set_status=lambda pills: calls.append(("page", pills)))
    tab._card = SimpleNamespace(set=lambda *a, **k: calls.append(("card", a)),
                                set_meta=lambda rows: calls.append(("meta", rows)))
    env.service.display = RuntimeError("svc")
    tab._refresh_status()
    assert calls == [("page", [("error", "Error")]), ("card", ("PlayStation account", "error", "Error")),
                     ("meta", [])]


def test_sync_connect_button(env):
    tab = tabmod.PSNTab()
    tab._sync_connect_button()  # no button
    btn = FakeButton()
    tab.connect_button = btn
    tab._sync_connect_button()
    assert btn.text == "Connect to PSN"
    tab._load_from_state()
    tab.buffer.npsso_code = "tok"
    tab._sync_connect_button()
    assert btn.text == "Sign in again" and "outline" not in btn._props
    tab._connected = True
    tab._sync_connect_button()
    assert btn.text == "Sign in again" and btn._props.get("outline")
    tab._auth_in_progress = True
    btn.text = "Connecting..."
    tab._sync_connect_button()
    assert btn.text == "Connecting..."


# --------------------------------------------------------------------------- mismatch banner


class FakeBanner:
    def __init__(self):
        self.content = ""
        self.visible = None

    def set_content(self, c):
        self.content = c

    def set_visibility(self, v):
        self.visible = v


def test_check_mismatch(env):
    tab = tabmod.PSNTab()
    tab._check_mismatch()  # no banner
    tab._banner = FakeBanner()
    tab._check_mismatch()
    assert tab._banner.visible is False
    live = PSNData()
    live.current_game_mismatch = PSNGameMismatch("Odd <Game>", "PPSA1", "PS5", "now")
    env.state.live = live
    tab._check_mismatch()
    assert tab._banner.visible is True
    assert "Odd &lt;Game&gt;" in tab._banner.content and "scrollIntoView" not in tab._banner.content
    tab._cache_section = SimpleNamespace(html_id="c42")
    tab._check_mismatch()
    assert "document.getElementById(&#x27;c42&#x27;)" in tab._banner.content


def test_check_mismatch_error_swallowed(env):
    tab = tabmod.PSNTab()
    tab._banner = FakeBanner()
    env.state.get_live_psn_data = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    tab._check_mismatch()
    assert tab._banner.visible is None


# --------------------------------------------------------------------------- game cache logic


def test_fetch_cached_games_error_notifies(env):
    env.cache["load_error"] = RuntimeError("db")
    assert tabmod.PSNTab._fetch_cached_games() == []
    n = env.notes[0]
    assert n["title"] == "Couldn't load the PSN game cache" and n["dedupe_key"] == "psn:cache_load"


def test_reindex_sorted(env):
    tab = tab_with_cache_fields(env)
    assert [g["np_communication_id"] for g, _ in tab._cache_index] == ["NPWR3_00", "NPWR2_00", "NPWR10_00"]


def test_update_cache_head_and_rebuild_without_ui(env):
    tab = tabmod.PSNTab()
    tab._update_cache_head(1, None)
    tab._rebuild_cache_list()
    tab._highlight_selected()
    tab._show_details(True)


def test_on_cache_search_debounces(env):
    tab = tabmod.PSNTab()
    applied = []
    env.mp.setattr(tab, "_apply_cache_filter", lambda text: applied.append(text))
    tab._on_cache_search(SimpleNamespace(value="ab"))
    first = env.scheduled[-1]
    tab._on_cache_search(SimpleNamespace(value=None))
    assert first.cancelled is True
    env.scheduled[-1].cb()
    assert applied == [""]
    tab._cache_filter_timer = SimpleNamespace(cancel=lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    tab._on_cache_search(SimpleNamespace(value="x"))
    assert env.scheduled[-1].interval == 0.25


def test_apply_cache_filter(env):
    tab = tabmod.PSNTab()
    rebuilt = []
    env.mp.setattr(tab, "_rebuild_cache_list", lambda: rebuilt.append(tab._cache_filter))
    tab._cache_filter_timer = object()
    tab._apply_cache_filter("  ")
    assert rebuilt == [] and tab._cache_filter_timer is None
    tab._apply_cache_filter(" astro ")
    assert rebuilt == ["astro"]
    tab._apply_cache_filter("astro")
    assert rebuilt == ["astro"]


def test_game_selection_and_edits(env):
    tab = tab_with_cache_fields(env)
    tab._field_snapshot = {}
    tab._on_game_selected("missing")
    assert tab._selected_game is None
    tab._on_game_selected("NPWR10_00")
    assert tab._selected_game["presence_name"] == "Game Ten"
    assert tab.ui_elements["cache_presence_name"].value == "Game Ten"
    assert tab.ui_elements["cache_np_title_id"].value == "PPSA10"
    assert tab._game_cache_dirty is False and tab.ui_elements["cache_save_btn"].enabled is False
    assert tab._cache_edits() == {}
    tab.ui_elements["cache_np_title_id"].value = "PPSA99"
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is True and tab.ui_elements["cache_save_btn"].enabled is True
    assert tab._cache_edits() == {"np_title_id": "PPSA99"}
    tab.ui_elements["cache_np_title_id"].value = "PPSA10"
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is False and tab.ui_elements["cache_save_btn"].enabled is False
    tab._on_game_selected("")
    assert tab._selected_game is None


def test_cache_field_change_ignored_while_populating(env):
    tab = tab_with_cache_fields(env)
    tab._populating_game = True
    tab._game_cache_dirty = True
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is True
    tab._populating_game = False
    tab._suppress_dirty = True
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is True


def test_cache_edits_without_selection_or_fields(env):
    tab = tabmod.PSNTab()
    assert tab._cache_edits() == {}
    tab._selected_game = {"presence_name": "x"}
    assert tab._cache_edits() == {}
    tab._populate_game_details()  # no ui elements: still fine
    tab._selected_game = None
    tab._populate_game_details()
    tab._adopt_cache_field_snapshot()


def test_on_cache_field_changed_without_button(env):
    tab = tabmod.PSNTab()
    tab._on_cache_field_changed()
    assert tab._game_cache_dirty is False


def test_save_game_cache_guards(env):
    tab = tab_with_cache_fields(env)
    tab._save_game_cache()
    assert titles(env) == ["Pick a game from the list first"]
    tab._selected_game = {"presence_name": "x"}
    tab._save_game_cache()
    assert titles(env)[-1] == "That cache entry can't be edited"
    tab._selected_game = {"np_communication_id": "NPWR2_00"}
    tab._save_game_cache()  # nothing changed
    assert env.cache["updates"] == []


def test_save_game_cache_success(env):
    tab = tab_with_cache_fields(env)
    tab._on_game_selected("NPWR2_00")
    tab.ui_elements["cache_presence_name"].value = "Two"
    tab._on_cache_field_changed()
    tab._save_game_cache()
    assert env.cache["updates"] == [("NPWR2_00", {"presence_name": "Two"})]
    assert titles(env) == ["Saved changes to the game"]
    assert tab._game_cache_dirty is False and tab._selected_game["presence_name"] == "Two"
    assert tab.ui_elements["cache_save_btn"].enabled is False


def test_save_game_cache_failure_and_exception(env):
    tab = tab_with_cache_fields(env)
    tab._on_game_selected("NPWR10_00")
    tab.ui_elements["cache_presence_name"].value = "New"
    env.cache["update_ok"] = False
    env.dbm.problem = ("HTTP 503", "")
    tab._save_game_cache()
    assert titles(env) == ["Couldn't save changes to Game Ten"] and env.notes[0]["details"] == "HTTP 503"
    env.cache["update_error"] = RuntimeError("boom")
    tab._save_game_cache()
    assert env.notes[-1]["details"] == "RuntimeError: boom"


def test_save_game_cache_title_name_fallback(env):
    tab = tab_with_cache_fields(env)
    tab._selected_game = {"np_communication_id": "NPWR2_00", "title_name": "TN"}
    tab.ui_elements["cache_presence_name"].value = "P"
    tab._save_game_cache()
    assert titles(env) == ["Saved changes to TN"]


def test_remove_cache_chip(env):
    tab = tab_with_cache_fields(env)
    tab._on_remove_cache_chip("")
    assert env.cache["deletes"] == []
    tab._on_game_selected("NPWR2_00")
    tab._on_remove_cache_chip("NPWR10_00")
    assert tab._selected_game["np_communication_id"] == "NPWR2_00"
    tab._on_remove_cache_chip("NPWR2_00")
    assert tab._selected_game is None
    assert [g["np_communication_id"] for g in tab._cached_games] == ["NPWR3_00"]
    env.cache["delete_ok"] = False
    tab._on_remove_cache_chip("NPWR3_00")
    assert titles(env) == ["Couldn't remove that game from the cache"]


def test_refresh_game_cache_keeps_unsaved_edits(env):
    tab = tab_with_cache_fields(env)
    tab._on_game_selected("NPWR10_00")
    tab.ui_elements["cache_presence_name"].value = "unsaved"
    tab._on_cache_field_changed()
    env.cache["docs"][0]["trophy_name"] = "Game 10 (new)"
    tab.refresh_game_cache()
    assert tab.ui_elements["cache_presence_name"].value == "unsaved" and tab._game_cache_dirty is True
    tab._game_cache_dirty = False
    tab.refresh_game_cache()
    assert tab.ui_elements["cache_presence_name"].value == "Game Ten"


def test_refresh_game_cache_async(env):
    tab = tab_with_cache_fields(env)
    env.cache["docs"] = env.cache["docs"][:1]
    asyncio.run(tab._refresh_game_cache_async())
    assert len(tab._cached_games) == 1


def test_refresh_game_cache_async_none(env):
    tab = tabmod.PSNTab()
    tab._cached_games = [{"x": 1}]
    env.mp.setattr(tabmod.PSNTab, "_fetch_cached_games", staticmethod(lambda: None))
    asyncio.run(tab._refresh_game_cache_async())
    assert tab._cached_games == [{"x": 1}]


# --------------------------------------------------------------------------- built UI


def click_el(user, element, event_type="click"):
    from nicegui import events

    with user.client:
        for listener in list(element._event_listeners.values()):
            if listener.type == event_type:
                events.handle_event(listener.handler, events.GenericEventArguments(
                    sender=element, client=user.client, args={}))


def build_into(holder):
    def build():
        tab = tabmod.PSNTab()
        holder.append(tab)
        tab.build(ui.column())

    return build


def test_build_not_connected(env):
    env.cache["docs"] = []
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Trophies, presence and the current game for overlays")
        await user.should_see("Connect to PSN to sign in")
        await user.should_see("No cached games yet. Play a game to fill the cache.")
        await user.should_see("Pick a game from the list to see and edit its details.")
        assert tab._fold.expansion.value is True
        assert tab.connect_button.text == "Connect to PSN"
        intervals = sorted(t.interval for t in env.scheduled)
        assert intervals == [0.1, 0.5, 0.8, 3.0, 10.0]
        # inputs update the buffer
        tab.ui_elements["psn_username"].value = "pal"
        tab.ui_elements["npsso_code"].value = "abc"
        assert tab.buffer.psn_username == "pal" and tab.buffer.npsso_code == "abc" and tab.dirty
        assert tab._banner.visible is False

    run_ui(build_into(tabs), check)


def test_build_connected_cache_browse_and_edit(env):
    env.state.settings = PSNSettingsData(npsso_code="tok", psn_username="")
    env.service.display = ("Connected as me", "me (own account)", "text-theme-success")
    env.state.live = PSNData(online_id="me", current_game_name="Game Ten")
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Game 10")
        await user.should_see("Astro")
        assert tab._fold.expansion.value is False
        assert tab.connect_button.text == "Sign in again"
        assert set(tab._cache_rows) == {"NPWR10_00", "NPWR2_00", "NPWR3_00"}
        # select a game by clicking its row
        click_el(user, tab._cache_rows["NPWR10_00"])
        assert tab._selected_game["np_communication_id"] == "NPWR10_00"
        assert "is-active" in tab._cache_rows["NPWR10_00"].classes
        assert tab.ui_elements["game_details_container"].visible is True
        assert tab.ui_elements["cache_presence_name"].value == "Game Ten"
        # edit and save via the Save game button
        tab.ui_elements["cache_presence_name"].value = "Game Ten Renamed"
        assert tab._game_cache_dirty is True and tab.ui_elements["cache_save_btn"].enabled
        user.find("Save game").click()
        assert env.cache["updates"] == [("NPWR10_00", {"presence_name": "Game Ten Renamed"})]
        # search filters (debounced timer driven by hand)
        search = next(iter(user.find(ui.input).elements))
        for el in user.find(ui.input).elements:
            if el.props.get("placeholder") == "Search cached games":
                search = el
        search.value = "ps4"
        env.scheduled[-1].cb()
        assert set(tab._cache_rows) == {"NPWR2_00"}
        assert tab._cache_head.content.endswith("1 of 3 games</span>")
        search.value = "zzz"
        env.scheduled[-1].cb()
        await user.should_see("No cached games match “zzz”.")
        search.value = ""
        env.scheduled[-1].cb()
        # remove a row with its close button
        close = [b for b in user.find(ui.button).elements if b.props.get("icon") == "close"]
        row_for_astro = tab._cache_rows["NPWR3_00"]
        btn = next(b for b in close if b.parent_slot.parent is row_for_astro)
        click_el(user, btn, "click.stop")
        assert env.cache["deletes"] == ["NPWR3_00"] and "NPWR3_00" not in tab._cache_rows

    run_ui(build_into(tabs), check)


def test_build_list_limit_footer(env, monkeypatch):
    monkeypatch.setattr(tabmod, "_CACHE_LIST_LIMIT", 2)
    env.cache["docs"] = games() + [{"trophy_name": "No id"}]
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Showing 2 of 4 — type to narrow the list")
        assert len(tab._cache_rows) == 2

    run_ui(build_into(tabs), check)


def test_build_row_without_id_is_not_clickable(env):
    env.cache["docs"] = [{"trophy_name": "No id game", "platform": " PS5 "}]
    tabs: List[Any] = []

    async def check(user):
        await user.should_see("No id game")
        assert tabs[0]._cache_rows == {}
        assert tabs[0]._cache_head.content.endswith("1 game</span>")

    run_ui(build_into(tabs), check)


def test_build_help_dialog(env):
    tabs: List[Any] = []

    async def check(user):
        user.find("How to connect").click()
        await user.should_see("Getting Your NPSSO Token")
        await user.should_see("5. Save and Restart")
        first = max(user.find(ui.dialog).elements, key=lambda d: d.id)
        assert first.value is True
        user.find("Close").click()
        assert first.value is False
        click_el(user, next(iter(user.find("How to get an NPSSO code").elements)))
        second = max(user.find(ui.dialog).elements, key=lambda d: d.id)
        assert second is not first and second.value is True

    run_ui(build_into(tabs), check)


def test_build_mismatch_banner_and_status_error(env):
    env.state.settings = PSNSettingsData(npsso_code="tok")
    env.service.display = ("Token Expired", "N/A", "text-theme-error")
    live = PSNData()
    live.current_game_mismatch = PSNGameMismatch("Weird Game", "PPSA1", "PS5", "now")
    env.state.live = live
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab._check_mismatch()
        assert tab._banner.visible is True and "Weird Game" in tab._banner.content
        assert "Token Expired" in tab._card._top
        assert tab.connect_button.text == "Sign in again"

    run_ui(build_into(tabs), check)


def test_build_discard_and_save_buttons(env):
    env.state.settings = PSNSettingsData(npsso_code="tok", psn_username="me")
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.ui_elements["psn_username"].value = "other"
        assert tab.dirty
        user.find("Discard").click()
        assert tab.ui_elements["psn_username"].value == "me" and not tab.dirty
        tab.ui_elements["psn_username"].value = "friend"
        user.find("Save").click()
        assert ("psn_username", "friend") in env.state.updates
        while env.tasks:
            await env.tasks.pop(0)
        assert "PSN settings saved" in titles(env)

    run_ui(build_into(tabs), check)


# --------------------------------------------------------------------------- NPSSO sign-in flow

TOKEN = "N" * 64


@pytest.fixture
def auth(env, monkeypatch):
    import threading as _threading

    ctl = SimpleNamespace(dialog={}, dialog_error=None, sub=(True, TOKEN, ""), sub_error=None,
                          thread_mode="sync", pending=[], app=[], tab=None)

    def show(on_continue, on_cancel=None):
        if ctl.dialog_error:
            raise ctl.dialog_error
        ctl.dialog = {"cont": on_continue, "cancel": on_cancel}

    def run_sub():
        if ctl.sub_error:
            raise ctl.sub_error
        return ctl.sub

    class T:
        def __init__(self, target, daemon=False, name=None):
            self.target = target

        def start(self):
            if ctl.thread_mode == "sync":
                self.target()
            else:
                ctl.pending.append(self.target)

    async def fast_sleep(_t):
        return None

    monkeypatch.setattr(tabmod, "show_npsso_instruction_dialog", show)
    monkeypatch.setattr(tabmod, "_run_npsso_capture_subprocess", run_sub)
    monkeypatch.setattr(tabmod, "threading", SimpleNamespace(Thread=T, current_thread=_threading.current_thread))
    monkeypatch.setattr(tabmod, "app_schedule", lambda i, cb, once=False, active=True: ctl.app.append(cb))
    monkeypatch.setattr(tabmod, "run_on_ui_loop", lambda fn: fn())
    monkeypatch.setattr(tabmod, "asyncio", SimpleNamespace(sleep=fast_sleep, get_running_loop=asyncio.get_running_loop))
    tab = tabmod.PSNTab()
    tab._load_from_state()
    tab.connect_button = FakeButton()
    tab.ui_elements["npsso_code"] = Val("")
    ctl.tab = tab
    return ctl


def poll_timer(env):
    return [t for t in env.scheduled if t.interval == 0.2 and not t.once][-1]


def deferred(env):
    return [t for t in env.scheduled if t.interval == 0.01][-1]


def test_auth_success_full_flow(env, auth, capsys):
    tab = auth.tab
    tab._start_npsso_auth()
    assert tab._auth_in_progress and tab.connect_button.enabled is False
    assert tab.connect_button.text == "Connecting..."
    tab._start_npsso_auth()  # already running: ignored
    auth.dialog["cont"]()
    # worker persisted the token itself
    assert ("npsso_code", TOKEN) in env.state.updates and env.state.saves == 1
    assert len(auth.app) == 1
    auth.app[0]()  # apply on UI loop
    assert tab.ui_elements["npsso_code"].value == TOKEN and tab.buffer.npsso_code == TOKEN
    work = env.progress[0]
    assert work.title == "Connecting to PlayStation"
    assert tab._auth_in_progress is True  # reset deferred until save finishes
    asyncio.run(deferred(env).cb())
    assert env.service.changes == 1 and ("done", "Signed in to PlayStation") in work.events
    assert tab.dirty is False and tab._auth_in_progress is False and tab.connect_button.enabled
    # the poll timer sees the finished worker, cancels itself; result already applied
    timer = poll_timer(env)
    timer.cb()
    assert timer.cancelled is True and tab._npsso_capture_timer is None
    assert len(env.progress) == 1
    assert "[NPSSO_TRACE]" in capsys.readouterr().out


@pytest.mark.parametrize("save_ok,expect", [(False, "close"), (None, "close")])
def test_auth_deferred_save_not_persisted(env, auth, save_ok, expect):
    tab = auth.tab
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    if save_ok is None:
        tab.buffer = None
    else:
        env.state.save_ok = False
    asyncio.run(deferred(env).cb())
    assert env.progress[0].events == [("close",)] and env.service.changes == 0
    assert tab._auth_in_progress is False


def test_auth_deferred_service_error(env, auth):
    tab = auth.tab
    env.service.change_error = RuntimeError("psn down")
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    asyncio.run(deferred(env).cb())
    ev = env.progress[0].events[0]
    assert ev[0] == "fail" and ev[1] == "Signed in, but couldn't connect to PSN"
    assert ev[2]["details"] == "RuntimeError: psn down" and tab._auth_in_progress is False


@pytest.mark.parametrize("sub,details", [((False, "", "window closed"), "window closed"),
                                         ((False, "", ""), "NPSSO capture failed.")])
def test_auth_capture_failure(env, auth, sub, details):
    auth.sub = sub
    tab = auth.tab
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    assert titles(env) == ["PlayStation sign-in didn't finish"] and env.notes[0]["details"] == details
    assert tab._auth_in_progress is False and env.state.updates == []


def test_auth_capture_exception(env, auth):
    auth.sub_error = OSError("spawn failed")
    auth.tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    assert env.notes[0]["details"] == "spawn failed"


def test_auth_worker_persist_failure_still_applies(env, auth):
    def boom(k, v):
        raise RuntimeError("db")

    env.state.update_psn_setting = boom
    tab = auth.tab
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    assert tab.ui_elements["npsso_code"].value == TOKEN


def test_auth_apply_error_reports(env, auth):
    tab = auth.tab
    del tab.ui_elements["npsso_code"]
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    auth.app[0]()
    assert titles(env) == ["Signed in, but Mycelian couldn't use the PSN sign-in"]
    assert tab._auth_in_progress is False


def test_auth_poll_waits_then_times_out(env, auth):
    auth.thread_mode = "deferred"
    tab = auth.tab
    tab._npsso_capture_timer = SimpleNamespace(cancel=lambda: (_ for _ in ()).throw(RuntimeError("old")))
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    timer = poll_timer(env)
    assert tab._npsso_capture_timer is timer
    timer.cb()
    assert timer.cancelled is False and env.notes == []
    tab._npsso_capture_timer = SimpleNamespace(cancel=lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    for _ in range(2999):
        timer.cb()
    assert titles(env) == ["PlayStation sign-in didn't finish"]
    assert "Timed out" in env.notes[0]["details"] and tab._npsso_capture_timer is None
    # the worker finishing later does not apply a second result
    auth.pending[0]()
    auth.app[0]()
    assert len(env.notes) == 1


def test_auth_poll_done_with_timer_cancel_error(env, auth):
    auth.thread_mode = "deferred"
    tab = auth.tab
    tab._start_npsso_auth()
    auth.dialog["cont"]()
    timer = poll_timer(env)
    auth.pending[0]()
    tab._npsso_capture_timer = SimpleNamespace(cancel=lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    timer.cb()  # applies result first (before the UI-loop hop)
    assert tab.ui_elements["npsso_code"].value == TOKEN
    auth.app[0]()  # second apply is a no-op
    assert len(env.progress) == 1


def test_auth_dialog_cancel_resets(env, auth):
    tab = auth.tab
    tab._start_npsso_auth()
    auth.dialog["cancel"]()
    assert tab._auth_in_progress is False and tab.connect_button.enabled


def test_auth_dialog_error(env, auth):
    tab = auth.tab
    del tab._auth_in_progress  # older instances may lack the flag
    auth.dialog_error = RuntimeError("no webview")
    tab._start_npsso_auth()
    assert titles(env) == ["Couldn't open the PlayStation sign-in window"]
    assert env.notes[0]["details"] == "RuntimeError: no webview"
    assert tab._auth_in_progress is False and tab.connect_button.enabled

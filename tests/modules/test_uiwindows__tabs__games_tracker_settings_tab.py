# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Games Tracker: the options, Save / Discard, milestones, cover cache and baseline reset."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for p in (str(ROOT), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules.game_tracker import service as svc  # noqa: E402
from modules.game_tracker.models import PSN, TrackedGame, TrackerSettings  # noqa: E402
from modules.game_tracker.store import GameTrackerStore  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import games_tracker as gt  # noqa: E402
from modules.uiwindows.tabs import games_tracker_settings_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    service = svc.GameTrackerService(store, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    svc.set_service(service)
    notes: List[dict] = []
    nav: List[str] = []

    async def io_bound(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append({"message": message, **kw}))
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    monkeypatch.setattr("modules.help_system.contextual_help.navigate_to_main_tab", lambda name: nav.append(name))
    monkeypatch.setattr(gt, "_tab", None)
    yield SimpleNamespace(store=store, service=service, notes=notes, nav=nav)
    svc.set_service(None)


def build_into(holder):
    def build():
        tab = tabmod.GamesTrackerSettingsTab()
        holder.append(tab)
        tab.build(ui.column())

    return build


def test_builds_with_save_and_discard(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Track PlayStation library")
        await user.should_see("Progress milestones")
        await user.should_see("Reset baseline")
        assert tab._page.save_button is not None and tab._page.discard_button is not None
        assert tab.has_unsaved_changes() is False
        tab.on_exit()
        assert tab._page._timer.active is False
        tab.on_enter()
        assert tab._page._timer.active is True

    run_ui(build_into(tabs), check)


def test_options_dirty_save_and_discard(env):
    tabs: List[Any] = []
    library = SimpleNamespace(_needs_refresh=False)

    async def check(user):
        tab = tabs[0]
        gt._tab = library
        tab._set_opt(sync_minutes=30, steam_enabled=False)
        tab._add_milestone(10)
        tab._add_milestone(10)  # already there
        tab._add_milestone("x")
        tab._add_milestone(150)  # out of range
        tab._remove_milestone(75)
        assert tab.dirty and tab._buffer.milestones == [10, 25, 50]
        tab.save()
        saved = env.store.load_settings()
        assert saved.sync_minutes == 30 and saved.steam_enabled is False and saved.milestones == [10, 25, 50]
        assert tab.dirty is False and env.notes[-1]["title"] == "Games Tracker settings saved"
        assert library._needs_refresh  # the main tab redraws with the new options
        tab._set_opt(record_stats=False)
        tab.ui_elements["opt_stats"].value = False
        assert tab.dirty
        tab.discard()
        assert tab.dirty is False and tab._buffer.record_stats is True
        assert tab.ui_elements["opt_stats"].value is True

    run_ui(build_into(tabs), check)


def test_save_keeps_the_main_tab_view_and_export(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        # The main tab remembers filters / export choices after this tab was built.
        current = env.service.settings
        env.service.save_settings(TrackerSettings.from_dict({**current.to_dict(), "view": {"backlog": {"status": "complete"}}}))
        tab._set_opt(backlog_section=False)
        tab.save()
        saved = env.store.load_settings()
        assert saved.backlog_section is False and saved.view["backlog"]["status"] == "complete"

    run_ui(build_into(tabs), check)


def test_save_failure_notifies(env, monkeypatch):
    tabs: List[Any] = []

    def boom(_s):
        raise OSError("read-only")

    async def check(user):
        tab = tabs[0]
        monkeypatch.setattr(env.service, "save_settings", boom)
        tab._set_opt(sync_minutes=20)
        tab.save()
        assert tab.dirty and env.notes[-1]["title"] == "Couldn't save the Games Tracker settings"

    run_ui(build_into(tabs), check)


def test_clear_covers_reset_baseline_and_open_library(env, monkeypatch):
    monkeypatch.setattr(tabmod.covers, "clear_covers", lambda: 3)
    env.store.upsert_games([TrackedGame(platform=PSN, game_id="NPWR00001_00", title="ELDEN RING", platform_label="PS5")])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await tab._clear_covers()
        assert env.notes[-1]["title"] == "Cover art cleared" and "3 covers" in env.notes[-1]["message"]
        tab._reset_baseline()
        assert env.store.get_game(PSN, "NPWR00001_00").baselined is False
        tab._confirm_reset()
        await user.should_see("Reset the baseline?")
        tabmod._open_games_tracker()
        assert env.nav == ["Games Tracker"]

    run_ui(build_into(tabs), check)


def test_without_a_page():
    tab = tabmod.GamesTrackerSettingsTab()
    tab.on_enter()
    tab.on_exit()
    assert tab.has_unsaved_changes() is False
    d = tab._settings_dict(TrackerSettings())
    assert "export" not in d and "view" not in d

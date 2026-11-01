# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/steam_tab.py (Settings → Steam)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from types import SimpleNamespace  # noqa: E402
from typing import Any, List  # noqa: E402

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import dataobjects  # noqa: E402
from modules.dataobjects import SteamSettingsData  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.steam_data import SteamData  # noqa: E402
from modules.uiwindows.tabs import steam_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402

KEY = "0123456789ABCDEF0123456789abcdef"


class FakeState:
    def __init__(self):
        self.settings = SteamSettingsData()
        self.live: Any = None
        self.updates: List[tuple] = []
        self.saves = 0
        self.save_ok = True

    def get_steam_settings_data(self):
        return SteamSettingsData(**vars(self.settings))

    def get_live_steam_data(self):
        return self.live

    def update_steam_setting(self, key, value):
        self.updates.append((key, value))
        setattr(self.settings, key, value)
        return True

    def save_changes(self):
        self.saves += 1
        return self.save_ok


def in_game(**over):
    data = SteamData(
        steam_id="76561198000000001", persona_name="Mushy", persona_state="Online", is_online=True,
        connection_status="Connected", avatar_url="https://img/me.jpg", steam_level=12,
        current_game_appid=620, current_game_name="Portal 2", current_game_art_url="https://cdn/620/header.jpg",
        has_achievements=True, achievement_counts={"unlocked": 1, "total": 3}, current_game_progress=33,
        current_game_achievements=[
            {"name": "First", "description": "Do it", "icon_url": "https://cdn/a.jpg", "icon_gray_url": "https://cdn/a_g.jpg",
             "achieved": True, "hidden": False, "global_percent": 70.5},
            {"name": "Secret", "description": "Spoiler", "icon_url": "https://cdn/b.jpg", "icon_gray_url": "",
             "achieved": False, "hidden": True, "global_percent": 2.0},
            {"name": "<b>Rare</b>", "description": "", "icon_url": None, "icon_gray_url": None,
             "achieved": False, "hidden": False, "global_percent": None},
        ],
    )
    for k, v in over.items():
        setattr(data, k, v)
    return data


@pytest.fixture
def env(monkeypatch):
    st = FakeState()
    notes: List[dict] = []
    scheduled: List[Any] = []
    service = SimpleNamespace(display=("Not Connected", "", "text-theme-error"), changes=0, change_error=None)

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
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append({"message": message, **kw}))
    monkeypatch.setattr(tabmod, "steam_service", SimpleNamespace(get_steam_status_display=display,
                                                                 handle_steam_settings_change=change))

    def schedule(interval, cb, *, once=False, active=True):
        t = SimpleNamespace(interval=interval, cb=cb, once=once, active=active)
        scheduled.append(t)
        return t

    monkeypatch.setattr(tabmod, "layout_schedule", schedule)

    async def io_bound(fn, *a):
        return fn(*a)

    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=io_bound))
    tasks: List[Any] = []
    import nicegui.background_tasks as bt

    real_create = bt.create

    def create(*args, name="unnamed task", **kw):
        if name == "steam_save_settings_change":
            tasks.append(args[0] if args else kw.get("coroutine"))
            return None
        return real_create(*args, name=name, **kw)

    monkeypatch.setattr(bt, "create", create)
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    opened: List[str] = []
    monkeypatch.setattr(tabmod, "open_url", opened.append)
    return SimpleNamespace(state=st, notes=notes, scheduled=scheduled, service=service, tasks=tasks, opened=opened)


def build_into(holder):
    def build():
        tab = tabmod.SteamTab()
        holder.append(tab)
        tab.build(ui.column())

    return build


# --------------------------------------------------------------------------- pure helpers


def test_game_panel_html_states():
    assert "Connect to Steam" in tabmod.game_panel_html(None)
    assert "Not in a game right now (Away)" in tabmod.game_panel_html(SteamData(steam_id="1", persona_state="Away"))
    html = tabmod.game_panel_html(in_game())
    assert "Portal 2" in html and "1 of 3 achievements · 33%" in html and 'width:33%' in html
    assert "Hidden achievement" in html and "Spoiler" not in html  # locked hidden ones stay hidden
    assert "&lt;b&gt;Rare&lt;/b&gt;" in html and "<b>Rare" not in html
    assert "70.5% of players" not in html and "Unlocked" in html and "2% of players" in html and "Locked" in html
    assert "https://cdn/a.jpg" in html  # unlocked: colour icon
    assert "achievements are private" in tabmod.game_panel_html(in_game(achievements_private=True)).lower()
    assert "no Steam achievements" in tabmod.game_panel_html(in_game(has_achievements=False, current_game_art_url=None))
    assert "stm-bar" not in tabmod.game_panel_html(in_game(current_game_progress=None))
    assert "Unknown game" in tabmod.game_panel_html(in_game(current_game_name=None))


def test_validation(env):
    tab = tabmod.SteamTab()
    tab._load_from_state()
    assert tab._validation_error() is None
    tab.buffer.api_key = "short"
    assert "32 letters" in tab._validation_error()
    tab.buffer.api_key = KEY
    assert tab._validation_error() is None  # the key alone can be saved; the profile can follow
    tab.buffer.steam_profile = "not a profile!"
    assert "profile link" in tab._validation_error()
    tab.buffer.steam_profile = "mushy"
    assert tab._validation_error() is None


# --------------------------------------------------------------------------- status


def test_status(env):
    tab = tabmod.SteamTab()
    assert tab._status() == ("off", "Not connected", "Not connected", "off", "Add your Web API key to connect", [])
    env.state.settings.api_key = KEY
    env.service.display = ("Invalid API Key", "", "text-theme-error")
    assert tab._status() == ("error", "Invalid API Key", "Steam profile", "error", "Steam refused the Web API key", [])
    env.service.display = ("Profile Not Found", "", "text-theme-error")
    assert tab._status()[4] == "No Steam profile matches what you entered"
    env.service.display = ("Profile Not Set", "", "text-theme-warning")
    assert tab._status() == ("warn", "Profile needed", "Steam profile", "warn", "Enter your Steam profile under Tracking", [])
    env.service.display = ("Connected", "Mushy", "text-theme-success")
    env.state.live = in_game()
    assert tab._status() == ("ok", "Connected", "Mushy", "ok", "Online · Playing Portal 2",
                             [("Steam level", "12", "", ""), ("Now playing", "Portal 2", "1 of 3 achievements", "")])
    env.service.display = ("Offline", "Mushy", "text-theme-warning")
    env.state.live = SteamData(persona_state="Offline", steam_level=None)
    assert tab._status() == ("warn", "Offline", "Mushy", "warn", "Offline", [])
    env.state.live = in_game(achievement_counts={})
    assert tab._status()[5][-1] == ("Now playing", "Portal 2", "", "")


def test_refresh_status_error_path(env):
    tab = tabmod.SteamTab()
    calls = []
    tab._page = SimpleNamespace(set_status=lambda pills: calls.append(("page", pills)))
    tab._card = SimpleNamespace(set=lambda *a, **k: calls.append(("card", a)), set_meta=lambda rows: calls.append(("meta", rows)))
    env.service.display = RuntimeError("svc")
    tab._refresh_status()
    assert calls == [("page", [("error", "Error")]), ("card", ("Steam profile", "error", "Error")), ("meta", [])]


# --------------------------------------------------------------------------- the page


def test_build_not_connected_then_save(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Achievements, presence and the current game for overlays")
        await user.should_see("Add your Web API key to connect")
        await user.should_see("Connect to Steam to see the game you are playing.")
        assert tab._fold.expansion.value is True and tab.connect_button.text == "Connect"
        assert sorted(t.interval for t in env.scheduled) == [0.8, 3.0]
        user.find("Get an API key").click()
        assert env.opened == [tabmod.API_KEY_URL]
        # Connect without a key opens the key fold and says why
        user.find("Connect").click()
        assert env.notes[-1]["title"] == "Steam API key needed" and env.state.saves == 0
        tab.ui_elements["api_key"].value = KEY
        tab.ui_elements["steam_profile"].value = " https://steamcommunity.com/id/mushy "
        assert tab.dirty and tab.buffer.steam_profile == "https://steamcommunity.com/id/mushy"
        user.find("Connect").click()
        assert env.state.updates == [("api_key", KEY), ("steam_profile", "https://steamcommunity.com/id/mushy")]
        assert env.state.saves == 1 and not tab.dirty and len(env.tasks) == 1
        tab.save()  # still applying: ignored
        assert env.state.saves == 1
        await env.tasks.pop()
        assert env.service.changes == 1 and tab._saving is False
        assert not any(n.get("title") == "Steam settings saved" for n in env.notes)  # Connect is quiet

    run_ui(build_into(tabs), check)


def test_save_paths(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.ui_elements["api_key"].value = "bad"
        tab.save()
        assert env.notes[-1]["title"] == "Steam settings not saved" and env.state.saves == 0
        tab.ui_elements["api_key"].value = KEY
        tab.ui_elements["steam_profile"].value = "mushy"
        env.state.save_ok = False
        tab.save()
        assert env.state.saves == 1 and tab.dirty and env.tasks == []
        env.state.save_ok = True
        tab.save()
        await env.tasks.pop()
        assert env.notes[-1]["title"] == "Steam settings saved"
        env.service.change_error = RuntimeError("nope")
        tab.save()
        await env.tasks.pop()
        assert env.notes[-1]["title"] == "Steam settings saved, but Steam couldn't connect"
        # discard puts the saved values back
        tab.ui_elements["steam_profile"].value = "someone_else"
        tab.discard()
        assert tab.ui_elements["steam_profile"].value == "mushy" and not tab.has_unsaved_changes()
        tab.mark_clean()
        tab.buffer = None
        tab.save()  # nothing to save

    run_ui(build_into(tabs), check)


def test_build_connected_shows_profile_game_and_achievements(env):
    env.state.settings = SteamSettingsData(api_key=KEY, steam_profile="mushy")
    env.service.display = ("Connected", "Mushy", "text-theme-success")
    env.state.live = in_game()
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await user.should_see("Mushy")
        assert tab._fold.expansion.value is False and tab.connect_button.text == "Reconnect"
        assert 'src="https://img/me.jpg"' in tab._card._who.content
        assert "Portal 2" in tab._game.content and "First" in tab._game.content
        first = tab._game.content
        tab._refresh_status()
        assert tab._game.content == first
        env.state.live = in_game(current_game_appid=None, current_game_name=None, persona_state="Away")
        tab._refresh_status()
        assert "Not in a game right now (Away)" in tab._game.content
        tab.on_exit()
        assert tab._status_timer.active is False
        tab.on_enter()
        assert tab._status_timer.active is True

    run_ui(build_into(tabs), check)


@pytest.mark.parametrize("pasted,expected", [
    (KEY, KEY),
    (f"  Key: {KEY}\n", KEY),
    (f"Domain Name: localhost Key: {KEY} Revoke", KEY),
    ("not a key", "not a key"),
    ("0123456789ABCDEF0123456789abcdef0", "0123456789ABCDEF0123456789abcdef0"),  # 33 hex: left for validation
])
def test_pasted_key_is_cleaned(env, pasted, expected):
    tab = tabmod.SteamTab()
    tab._load_from_state()
    tab._set("api_key", pasted)
    assert tab.buffer.api_key == expected


def test_key_without_a_profile_saves_and_asks_for_the_profile(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.ui_elements["api_key"].value = f"Key: {KEY}"
        user.find("Connect").click()
        assert env.state.updates == [("api_key", KEY), ("steam_profile", "")] and env.state.saves == 1
        await env.tasks.pop()
        assert env.notes[-1]["title"] == "Steam API key saved" and "under Tracking" in env.notes[-1]["message"]
        env.service.display = ("Profile Not Set", "", "text-theme-warning")
        tab._refresh_status()
        assert tab._connected is False and tab.connect_button.text == "Connect"
        assert "Enter your Steam profile under Tracking" in tab._card._who.content

    run_ui(build_into(tabs), check)


def test_lifecycle_without_a_page(env):
    tab = tabmod.SteamTab()
    tab.on_enter()
    tab.on_exit()
    assert tab.has_unsaved_changes() is False

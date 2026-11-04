# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Games Tracker: helpers, the built tab, filters, options, the game drawer and the export dialog."""

from __future__ import annotations

import inspect
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from modules import psnapi  # noqa: E402
from modules.game_tracker import service as svc  # noqa: E402
from modules.game_tracker.models import PSN, STEAM, GameEvent, TrackedGame, TrackerSettings  # noqa: E402
from modules.game_tracker.store import GameTrackerStore  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows.tabs import games_tracker_tab as tabmod  # noqa: E402
from ui_sim import run_ui  # noqa: E402

NOW = time.time()


def psn_game(gid="NPWR00001_00", title="ELDEN RING", **kw):
    base = dict(platform=PSN, game_id=gid, title=title, platform_label="PS5",
                earned={"bronze": 5, "silver": 2, "gold": 1}, defined={"bronze": 10, "silver": 4, "gold": 2, "platinum": 1},
                progress=40, last_played=NOW - 3600)
    base.update(kw)
    return TrackedGame(**base)


def steam_game(gid="10", title="Hades", **kw):
    base = dict(platform=STEAM, game_id=gid, title=title, platform_label="Steam", unlocked_ids=["a", "b"],
                total_achievements=2, progress=100, playtime_min=600, last_played=NOW - 86400 * 2)
    base.update(kw)
    return TrackedGame(**base)


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    service = svc.GameTrackerService(store, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    svc.set_service(service)
    notes: List[dict] = []
    scheduled: List[Any] = []

    def schedule(interval, cb, *, once=False, active=True):
        t = SimpleNamespace(interval=interval, cb=cb, once=once, active=active)
        scheduled.append(t)
        return t

    async def io_bound(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(tabmod, "layout_schedule", schedule)
    monkeypatch.setattr(tabmod, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(tabmod, "notify", lambda message, **kw: notes.append({"message": message, **kw}))
    monkeypatch.setattr("modules.help_system.contextual_help.help_button", lambda **kw: ui.button("Help"))
    monkeypatch.setattr(tabmod.covers, "request_covers", lambda items: 0)
    yield SimpleNamespace(store=store, service=service, notes=notes, scheduled=scheduled)
    svc.set_service(None)


async def run_scheduled(env, name):
    for t in list(env.scheduled):
        if getattr(t.cb, "__name__", "") == name:
            result = t.cb()
            if inspect.isawaitable(result):
                await result
            return True
    return False


def build_into(holder):
    def build():
        tab = tabmod.GamesTrackerTab()
        holder.append(tab)
        tab.build(ui.column())

    return build


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize("delta,text", [
    (10, "just now"), (120, "2 minutes ago"), (5 * 3600, "5 hours ago"), (86400 * 1.5, "yesterday"),
    (86400 * 3, "3 days ago"), (86400 * 10, "1 week ago"), (86400 * 60, "2 months ago"), (86400 * 800, "2 years ago"),
])
def test_ago(delta, text):
    assert tabmod.ago(1_000_000 - delta, now=1_000_000) == text


def test_ago_never_and_initials_and_tone():
    assert tabmod.ago(0) == "never"
    assert tabmod.initials("God of War: Ragnarök") == "GO"
    assert tabmod.initials("") == "?"
    assert tabmod.cover_tone("psn:A") in tabmod._COVER_TONES


def test_filter_games():
    games = [psn_game(), steam_game(), steam_game("11", "Balatro", progress=40, unlocked_ids=["a"], total_achievements=3,
                                                   last_played=NOW), psn_game("NPWR00009_00", "Hidden One", hidden=True)]
    assert [g.title for g in tabmod.filter_games(games)] == ["Balatro", "ELDEN RING", "Hades"]
    assert [g.title for g in tabmod.filter_games(games, platform=STEAM, sort="name")] == ["Balatro", "Hades"]
    assert [g.title for g in tabmod.filter_games(games, status="done")] == ["Hades"]
    assert [g.title for g in tabmod.filter_games(games, status="progress", sort="progress")] == ["Balatro", "ELDEN RING"]  # tie: by name
    assert [g.title for g in tabmod.filter_games(games, status="hidden")] == ["Hidden One"]
    assert [g.title for g in tabmod.filter_games(games, q="  elden ")] == ["ELDEN RING"]
    assert [g.title for g in tabmod.filter_games(games, q="npwr00001_00")] == ["ELDEN RING"]


def test_card_parts():
    p = tabmod.card_parts(psn_game(title="<b>X</b>"), now=NOW)
    assert "&lt;b&gt;X&lt;/b&gt;" in p["top"] and "<b>X" not in p["top"]
    assert "8 / 17 trophies" == p["detail"] and 'id="gtr-tg-platinum"' in p["foot"] and p["cls"] == "itg-hook gtr-card"
    assert "Played 1 hour ago" in p["top"] and 'width:40%' in p["top"]
    s = tabmod.card_parts(steam_game(hidden=True), now=NOW)
    assert "Perfect" in s["top"] and "Hidden" in s["top"] and "is-success" in s["cls"] and "is-hidden" in s["cls"]
    assert s["detail"] == "10 h played" and "2 / 2 achievements" in s["foot"]
    assert "No achievements" in tabmod.card_parts(steam_game(total_achievements=0, unlocked_ids=[], playtime_min=30))["foot"]
    assert tabmod.card_parts(steam_game(playtime_min=30))["detail"] == "30 min played"


def test_overview_status_and_deltas():
    summary = svc.summarize([psn_game(earned={"platinum": 1}, progress=100), steam_game()])
    html = tabmod.overview_html(summary, {"trophies": 3})
    assert "1 PlayStation · 1 Steam" in html and "+3 this week" in html and "Avg completion" in html
    assert tabmod.week_deltas({"grade:gold": 2, "grade:platinum": 1, "grade:achievement": 4}) == {
        "platinum": 1, "trophies": 3, "achievements": 4, "perfect": 0}
    status = {
        PSN: {"enabled": True, "state": "syncing", "done": 3, "total": 9, "first_import": True},
        STEAM: {"enabled": False},
    }
    assert tabmod.status_pills(status, summary) == [("busy", "PSN · importing 3 / 9"), ("off", "Steam off")]
    status = {PSN: {"enabled": True, "state": "waiting", "baselined": False}, STEAM: {"enabled": True, "state": "error"}}
    assert tabmod.status_pills(status, summary) == [("warn", "PSN not connected"), ("error", "Steam · sync problem")]
    status = {PSN: {"enabled": True, "state": "idle", "baselined": True}, STEAM: {"enabled": True, "state": "syncing"}}
    assert tabmod.status_pills(status, summary) == [("ok", "PSN · 1 games"), ("busy", "Steam · syncing…")]


def test_css_uses_theme_tokens_except_grade_colours():
    hexes = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", tabmod.GAMES_TRACKER_CSS))
    assert hexes == {"#fff"}
    assert "rgb(" not in tabmod.GAMES_TRACKER_CSS.replace("rgba(0, 0, 0", "")


def test_cover_src_registers_route_once(monkeypatch, tmp_path):
    monkeypatch.setattr(tabmod.covers, "cover_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tabmod, "_media_route_dir", None)
    added = []
    import nicegui

    monkeypatch.setattr(nicegui.app, "add_media_files", lambda route, d: added.append((route, d)))
    assert tabmod.cover_src(PSN, "NPWR1_00") is None
    (tmp_path / "psn_NPWR1_00.jpg").write_bytes(b"x")
    url = tabmod.cover_src(PSN, "NPWR1_00")
    assert url.startswith("/_mycelian_game_covers/psn_NPWR1_00.jpg?v=")
    tabmod.cover_src(PSN, "NPWR1_00")
    assert added == [("/_mycelian_game_covers", str(tmp_path))]
    assert '<img class="fg" src="/_mycelian_game_covers/psn_NPWR1_00.jpg' in tabmod.cover_html(psn_game(gid="NPWR1_00"))


# --------------------------------------------------------------------------- registration


def test_tab_is_registered_after_game_hooks():
    from modules import notify_settings, ui_tab_transitions
    from modules.uiwindows.tabs import theme_mock

    order = ui_tab_transitions.SETTINGS_TAB_ORDER
    assert order[order.index("Game Hooks") + 1] == "Games Tracker"
    labels = [t[-1] for t in theme_mock._SUB_TABS]
    assert labels[labels.index("Game Hooks") + 1] == "Games Tracker"
    assert "game_tracker" in notify_settings.SOURCE_IDS
    src = (ROOT / "modules" / "uiwindows" / "settings.py").read_text()
    assert src.index('ui.tab("Game Hooks"') < src.index('ui.tab("Games Tracker", icon="emoji_events")') < src.index('ui.tab("Database"')
    assert '"Games Tracker": games_tracker_tab' in src
    from modules.help_system.help_content import HELP_TOPICS

    assert "games_tracker" in HELP_TOPICS


# --------------------------------------------------------------------------- the page


def test_empty_library_shows_setup(env):
    tabs: List[Any] = []

    async def check(user):
        await run_scheduled(env, "_refresh")
        await user.should_see("No games yet")
        await user.should_see("Set up PSN")
        assert tabs[0].ui_elements["library"].visible is False

    run_ui(build_into(tabs), check)


def test_library_cards_filters_and_show_more(env, monkeypatch):
    monkeypatch.setattr(tabmod, "PAGE_SIZE", 2)
    env.store.upsert_games([psn_game(), steam_game(), steam_game("11", "Balatro", progress=40, unlocked_ids=["a"],
                                                                 total_achievements=3, last_played=NOW)])
    env.store.set_baseline(PSN, "a")
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        await user.should_see("Balatro")
        await user.should_see("ELDEN RING")
        await user.should_not_see("Hades")
        assert tab.ui_elements["more"].visible and tab.ui_elements["more_btn"].text == "Show more (1)"
        tab._show_more()
        await user.should_see("Hades")
        assert tab.ui_elements["more"].visible is False
        tab.ui_elements["plat_btns"][PSN].run_method("click")
        tab._set_filter(platform=PSN)
        await user.should_not_see("Balatro")
        assert [g.title for g in tab.visible_games()] == ["ELDEN RING"]
        assert "is-on" in tab.ui_elements["plat_btns"][PSN].classes
        tab._set_filter(q="zzz")
        assert tab.ui_elements["none"].visible
        assert any(p[1].startswith("PSN · 1 games") for p in tabmod.status_pills(tab._status, tab._summary))

    run_ui(build_into(tabs), check)


def test_tick_refreshes_when_the_service_changes(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        env.store.upsert_games([steam_game()])
        tab._mark_changed()
        await tab._tick()
        await user.should_see("Hades")
        tab._on_cover("steam_10")
        assert tab._needs_refresh
        tab.on_exit()
        tab.on_enter()

    run_ui(build_into(tabs), check)


def test_options_dirty_save_and_discard(env):
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        assert tab.has_unsaved_changes() is False
        tab._set_opt(sync_minutes=30, steam_enabled=False)
        tab._add_milestone(10)
        tab._add_milestone(10)  # already there
        tab._add_milestone("x")
        tab._remove_milestone(75)
        assert tab.dirty and tab._buffer.milestones == [10, 25, 50]
        assert "Sync every 30 min" in tab.ui_elements["opts_head"].content
        tab.save()
        saved = env.store.load_settings()
        assert saved.sync_minutes == 30 and saved.steam_enabled is False and saved.milestones == [10, 25, 50]
        assert tab.dirty is False and env.notes[-1]["title"] == "Games Tracker settings saved"
        tab._set_opt(record_stats=False)
        tab.discard()
        assert tab.dirty is False and tab._buffer.record_stats is True

    run_ui(build_into(tabs), check)


def test_clear_covers_and_reset_baseline(env, monkeypatch):
    monkeypatch.setattr(tabmod.covers, "clear_covers", lambda: 3)
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await tab._clear_covers()
        assert env.notes[-1]["title"] == "Cover art cleared" and "3 covers" in env.notes[-1]["message"]
        tab._reset_baseline()
        assert env.store.get_game(PSN, "NPWR00001_00").baselined is False
        tab._confirm_reset()
        await user.should_see("Reset the baseline?")
        tab._sync_now()
        assert env.notes[-1]["title"] == "Games Tracker sync started"

    run_ui(build_into(tabs), check)


# --------------------------------------------------------------------------- the game drawer


@pytest.fixture
def cache(monkeypatch):
    docs = {"NPWR00001_00": {"np_communication_id": "NPWR00001_00", "presence_name": "Elden Ring", "trophy_name": "ELDEN RING"}}
    monkeypatch.setattr(psnapi, "get_psn_game_cache_by_comm_id", lambda cid: dict(docs[cid]) if cid in docs else None)
    monkeypatch.setattr(psnapi, "update_psn_game_cache_in_db", lambda cid, u: docs[cid].update(u) or True)
    monkeypatch.setattr(psnapi, "store_psn_game_cache_in_db", lambda d: docs.__setitem__(d["np_communication_id"], dict(d)) or True)
    monkeypatch.setattr(psnapi, "delete_psn_game_cache_in_db", lambda cid: docs.pop(cid, None) is not None)
    return docs


def test_drawer_edit_save_psn(env, cache):
    env.store.upsert_games([psn_game()])
    env.store.record_events([GameEvent(kind="unlock", platform=PSN, game_id="NPWR00001_00", game_title="E",
                                       unlock_name="Legendary Armaments", unlock_grade="gold")])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        await user.should_see("Legendary Armaments")
        await user.should_see("NPWR ID")
        await user.should_see("Re-resolve NPWR from Title ID")
        assert tab.drawer_changes() == {}
        tab._edit("title", "ELDEN RING")  # same as shown: no change
        assert tab.drawer_changes() == {}
        tab._edit("title", "Elden Ring")
        tab._edit("np_title_id", "ppsa04609_00")
        tab._edit("hidden", True)
        assert tab.drawer_changes() == {"title": "Elden Ring", "np_title_id": "PPSA04609_00", "hidden": True}
        await tab._save_drawer()
        g = env.store.get_game(PSN, "NPWR00001_00")
        assert g.title_override == "Elden Ring" and g.np_title_id == "PPSA04609_00" and g.hidden
        assert cache["NPWR00001_00"]["np_title_id"] == "PPSA04609_00"
        assert env.notes[-1]["title"] == "Saved Elden Ring"

    run_ui(build_into(tabs), check)


def test_drawer_bad_npwr_and_resolve(env, cache, monkeypatch):
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        tab._edit("np_communication_id", "nope")
        await tab._save_drawer()
        assert env.notes[-1]["title"] == "Couldn't save the game" and "NPWR12345_00" in env.notes[-1]["message"]
        tab._drawer_edits = {}
        await tab._resolve_npwr()
        assert env.notes[-1]["title"] == "No NP Title ID"
        tab._edit("np_title_id", "PPSA1_00")
        monkeypatch.setattr(env.service, "resolve_npwr", lambda ntid: None)
        await tab._resolve_npwr()
        assert env.notes[-1]["title"] == "Couldn't resolve the NPWR ID"
        monkeypatch.setattr(env.service, "resolve_npwr", lambda ntid: "NPWR00077_00")
        await tab._resolve_npwr()
        assert tab.ui_elements["d_npwr"].value == "NPWR00077_00"
        assert tab.drawer_changes()["np_communication_id"] == "NPWR00077_00"
        await tab._save_drawer()
        assert env.store.get_game(PSN, "NPWR00077_00") is not None and "NPWR00077_00" in cache

    run_ui(build_into(tabs), check)


def test_drawer_groups_and_remove(env, cache, monkeypatch):
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        game = tab._games[0]
        tab.open_drawer(game)
        monkeypatch.setattr(env.service, "group_progress", lambda gid: None)
        await tab._load_groups(game)
        assert "needs PSN to be connected" in tab.ui_elements["groups"].content
        monkeypatch.setattr(env.service, "group_progress",
                            lambda gid: [{"name": "Shadow of the Erdtree", "earned": 4, "defined": 10, "progress": 40}])
        await tab._load_groups(game)
        assert "Shadow of the Erdtree" in tab.ui_elements["groups"].content and "4 / 10" in tab.ui_elements["groups"].content
        tab._confirm_remove()
        await user.should_see("Remove ELDEN RING?")
        await tab._remove(game, SimpleNamespace(close=lambda: None))
        assert env.store.get_game(PSN, game.game_id) is None and "NPWR00001_00" not in cache

    run_ui(build_into(tabs), check)


def test_steam_drawer_is_read_only_ids(env):
    env.store.upsert_games([steam_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        await user.should_see("App ID")
        await user.should_see("Steam IDs come from your library")
        assert "d_npwr" not in tab.ui_elements
        tab._edit("title", "My Hades")
        await tab._save_drawer()
        assert env.store.get_game(STEAM, "10").display_title == "My Hades"

    run_ui(build_into(tabs), check)


# --------------------------------------------------------------------------- export dialog


def test_export_dialog_preview_save_and_remember(env, monkeypatch, tmp_path):
    env.store.upsert_games([psn_game(), steam_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_export()
        await user.should_see("Library recap")
        await user.should_see("Headline tiles")
        assert tab._export_opts.template == "library"
        tab._set_export(rebuild=True, layout="square")
        await user.should_see("Square images show the first three tiles")
        tab._set_export(rebuild=True, pick="picked")
        tab._toggle_pick("steam:10", True)
        assert tab._export_opts.picked == ["steam:10"]
        await tab._render_preview(tab._export_render_seq)
        assert "data:image/jpeg;base64," in tab.ui_elements["exp_img"].content
        await tab._save_export()
        path = env.notes[-1]["message"].removeprefix("Saved to ").rstrip(".")
        assert env.notes[-1]["title"] == "Games image saved" and Path(path).is_file() and path.endswith("_square.png")
        assert env.store.load_settings().export["layout"] == "square"
        tab._close_export()

    run_ui(build_into(tabs), check)


def test_export_card_from_drawer_and_nothing_to_draw(env):
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        tab.open_export(tab._games[0])
        assert tab._export_opts.template == "game" and tab._export_opts.game_key == "psn:NPWR00001_00"
        await user.should_see("Game spotlight")
        await user.should_see("Grade breakdown")
        assert tab._render_export(as_png=True)[:8] == b"\x89PNG\r\n\x1a\n"
        tab._set_export(rebuild=True, template="library", platform=STEAM)
        await tab._render_preview(tab._export_render_seq)
        assert "Nothing to export" in tab.ui_elements["exp_img"].content
        await tab._save_export()
        assert env.notes[-1]["title"] == "Nothing to export"
        await tab._copy_export()
        assert env.notes[-1]["title"] == "Nothing to copy"

    run_ui(build_into(tabs), check)


def test_preview_failure_shows_message(env, monkeypatch):
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        tab.open_export()

        def boom(*a, **k):
            raise RuntimeError("render broke")

        monkeypatch.setattr(tab, "_render_export", boom)
        await tab._render_preview(tab._export_render_seq)
        assert "draw the preview. Details are in the log." in tab.ui_elements["exp_img"].content
        stale = tab._export_render_seq
        tab._schedule_preview()
        await tab._render_preview(stale)  # superseded: ignored

    run_ui(build_into(tabs), check)


def test_lifecycle_without_a_page(env):
    tab = tabmod.GamesTrackerTab()
    tab.on_enter()
    tab.on_exit()
    assert tab.has_unsaved_changes() is False and tab.drawer_changes() == {}
    assert tab.service is env.service
    assert isinstance(tab._settings_dict(TrackerSettings()), dict) and "export" not in tab._settings_dict(TrackerSettings())


def test_steam_private_and_unread_states():
    private = steam_game(total_achievements=0, unlocked_ids=[], extra={"private": True})
    unread = steam_game("11", total_achievements=0, unlocked_ids=[], baselined=False)
    assert "Achievements private" in tabmod.card_parts(private)["foot"]
    assert "Achievements not read yet" in tabmod.card_parts(unread)["foot"]
    banner = tabmod.steam_private_banner([psn_game(), private])
    assert "Steam achievements are private" in banner and "Game details" in banner
    assert tabmod.steam_private_banner([psn_game(), unread]) == ""


def test_export_dialog_spreadsheet_mode(env):
    env.store.upsert_games([psn_game(), steam_game(), steam_game("12", "Secret", hidden=True)])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        await user.should_see("Export")
        tab.open_export()
        assert tab._export_format == "image" and tab.ui_elements["exp_save_png"].visible
        tab._set_export_format("spreadsheet")
        await user.should_see("Unlock history sheet")
        await user.should_not_see("Headline tiles")
        assert tab.ui_elements["exp_save_xlsx"].visible and not tab.ui_elements["exp_save_png"].visible
        assert not tab.ui_elements["exp_copy"].visible
        await tab._render_preview(tab._export_render_seq)
        content = tab.ui_elements["exp_img"].content
        assert "<table>" in content and "Hades" in content and "Secret" not in content and "Unlock history" in content
        assert tab.ui_elements["exp_size"].text.startswith("2 games · 0 history rows")
        tab._set_sheet(include_hidden=True, include_history=False)
        await tab._render_preview(tab._export_render_seq)
        assert "Secret" in tab.ui_elements["exp_img"].content and "Unlock history" not in tab.ui_elements["exp_img"].content
        await tab._save_spreadsheet()
        path = env.notes[-1]["message"].removeprefix("Saved to ").rstrip(".")
        assert env.notes[-1]["title"] == "Games spreadsheet saved" and path.endswith(".xlsx") and Path(path).is_file()
        saved = env.store.load_settings().export
        assert saved["format"] == "spreadsheet" and saved["spreadsheet"] == {"platform": "all", "include_hidden": True,
                                                                            "include_history": False}
        tab._close_export()
        tab.open_export()  # remembered
        assert tab._export_format == "spreadsheet" and tab._sheet_opts.include_hidden is True
        tab.open_export(tab._games[0])  # Export card always opens the image
        assert tab._export_format == "image" and tab._export_opts.template == "game"
        tab._set_export_format("spreadsheet")
        tab._set_sheet(platform="psn", include_hidden=False)
        env.store.delete_game(PSN, "NPWR00001_00")
        await tab._render_preview(tab._export_render_seq)
        assert "No games to list" in tab.ui_elements["exp_img"].content
        await tab._save_spreadsheet()
        assert env.notes[-1]["title"] == "Nothing to export"

    run_ui(build_into(tabs), check)


def test_sheet_preview_html_escapes_and_counts():
    html = tabmod.sheet_preview_html({"games": 5, "headers": ["Game"], "rows": [["<b>x</b>"]], "sheets": ["Games", "Summary"]})
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "+ 4 more rows" in html and 'class="is-on">Games' in html


def test_trophy_icons_drawn_or_official(monkeypatch, tmp_path):
    from PIL import Image

    tabmod._official_trophies.cache_clear()
    monkeypatch.setattr("modules.path_utils.get_assets_path", lambda rel: str(tmp_path / rel))
    for grade in ("bronze", "silver", "gold", "platinum"):
        svg = tabmod.trophy_icon(grade)
        assert svg.startswith('<svg class="gtr-trophy"') and f'id="gtr-tg-{grade}"' in svg
        assert tabmod._TROPHY_TONES[grade][1] in svg
    assert 'id="gtr-tg-bronze"' in tabmod.trophy_icon("nope")
    (tmp_path / "trophies").mkdir()
    Image.new("RGBA", (500, 500), (200, 200, 255, 255)).save(tmp_path / "trophies" / "platinum.png")
    tabmod._official_trophies.cache_clear()
    assert tabmod.trophy_icon("platinum").startswith("<svg")  # one file alone isn't a set
    for grade in ("bronze", "silver", "gold"):
        Image.new("RGBA", (256, 256), (180, 120, 60, 255)).save(tmp_path / "trophies" / f"{grade}.png")
    tabmod._official_trophies.cache_clear()
    try:
        icon = tabmod.trophy_icon("gold")
        assert icon.startswith('<img class="gtr-trophy" src="data:image/png;base64,')
        assert tabmod.grades_html(psn_game()).count("<img") == 4
    finally:
        tabmod._official_trophies.cache_clear()


def test_cover_html_fits_and_drawer_reports_failed_covers(env, monkeypatch):
    monkeypatch.setattr(tabmod, "cover_src", lambda platform, gid: "/_mycelian_game_covers/x.jpg?v=1")
    html = tabmod.cover_html(steam_game())
    assert 'class="bg"' in html and 'class="fg"' in html
    assert ".gtr-cover-art img.fg { object-fit: contain; }" in tabmod.GAMES_TRACKER_CSS
    tab = tabmod.GamesTrackerTab()
    now = time.time()
    monkeypatch.setattr(tabmod.covers, "cover_info", lambda p, g: {"cached": False, "fetched_at": now - 60,
                                                                  "expires_at": now + 5 * 3600, "ok": False})
    assert "download · tries again in 5 hours (or click Sync now)" in tab._kv_html(steam_game())
    monkeypatch.setattr(tabmod.covers, "cover_info", lambda p, g: {"cached": False, "fetched_at": 0, "expires_at": 0, "ok": False})
    assert "Not downloaded yet" in tab._kv_html(steam_game())
    monkeypatch.setattr(tabmod.covers, "cover_info", lambda p, g: {"cached": True, "fetched_at": now - 86400 * 2,
                                                                  "expires_at": now + 86400 * 28, "ok": True})
    assert "refreshes in 28 days" in tab._kv_html(steam_game())
    forgot = []
    monkeypatch.setattr(tabmod.covers, "forget_failures", lambda: forgot.append(1) or 0)
    tab._sync_now()
    assert forgot == [1]


# --------------------------------------------------------------------------- goals and the backlog


def _goals(*pairs):
    from modules.game_tracker.goals import GameGoal

    return [GameGoal.from_dict(dict(platform=p, game_id=gid, **kw)) for p, gid, kw in pairs]


def test_filter_games_goal_statuses_and_new_sorts():
    games = [psn_game(), steam_game(), steam_game("11", "Balatro", progress=0, unlocked_ids=[], total_achievements=3,
                                                   playtime_min=900, first_seen=NOW, last_played=NOW)]
    goals = {"psn:NPWR00001_00": _goals((PSN, "NPWR00001_00", {"kind": "manual", "name": "X"}))}
    assert [g.title for g in tabmod.filter_games(games, status="goals", goals=goals)] == ["ELDEN RING"]
    assert [g.title for g in tabmod.filter_games(games, status="no_goals", goals=goals)] == ["Balatro", "Hades"]
    assert [g.title for g in tabmod.filter_games(games, exclude_goal_games=True, goals=goals)] == ["Balatro", "Hades"]
    assert [g.title for g in tabmod.filter_games(games, status="not_started")] == ["Balatro"]
    assert [g.title for g in tabmod.filter_games(games, sort="least")] == ["Balatro", "ELDEN RING", "Hades"]
    assert [g.title for g in tabmod.filter_games(games, sort="name_desc")] == ["Hades", "ELDEN RING", "Balatro"]
    assert [g.title for g in tabmod.filter_games(games, sort="playtime")][0] == "Balatro"
    assert [g.title for g in tabmod.filter_games(games, sort="added")][0] == "Balatro"
    assert [g.title for g in tabmod.filter_games(games, sort="earned")][0] == "ELDEN RING"


def test_filter_backlog():
    games = [psn_game(), steam_game(), steam_game("11", "Balatro", hidden=True)]
    goals = {
        "psn:NPWR00001_00": _goals((PSN, "NPWR00001_00", {"kind": "manual", "name": "A", "target": 4, "current": 1})),
        "steam:10": _goals((STEAM, "10", {"kind": "complete"}), (STEAM, "10", {"kind": "manual", "name": "B", "order": 1})),
        "steam:11": _goals((STEAM, "11", {"kind": "manual", "name": "Hidden"})),
    }
    assert [g.title for g in tabmod.filter_backlog(games, goals)] == ["Hades", "ELDEN RING"]  # 50% before 25%
    assert [g.title for g in tabmod.filter_backlog(games, goals, sort="least")] == ["ELDEN RING", "Hades"]
    assert [g.title for g in tabmod.filter_backlog(games, goals, sort="most_goals")] == ["Hades", "ELDEN RING"]
    goals["psn:NPWR00001_00"][0].current = 4
    assert [g.title for g in tabmod.filter_backlog(games, goals, status="complete")] == ["ELDEN RING"]
    assert [g.title for g in tabmod.filter_backlog(games, goals, status="active", platform=STEAM)] == ["Hades"]
    assert tabmod.filter_backlog(games, goals, q="zzz") == []


def test_card_parts_with_goals_and_achievement_options():
    goals = _goals((PSN, "NPWR00001_00", {"kind": "manual", "name": "<i>Endings</i>", "target": 3, "current": 1}),
                   (PSN, "NPWR00001_00", {"kind": "count", "target": 5, "order": 1}),
                   (PSN, "NPWR00001_00", {"kind": "percent", "target": 90, "order": 2}),
                   (PSN, "NPWR00001_00", {"kind": "complete", "order": 3}))
    p = tabmod.card_parts(psn_game(), now=NOW, goals=goals)
    assert "&lt;i&gt;Endings&lt;/i&gt;" in p["top"] and "1 / 3" in p["top"] and "+1 more goal" in p["top"]
    assert "1 / 4 goals" in p["top"]
    assert "gtr-goals" not in tabmod.card_parts(psn_game(), now=NOW)["top"]
    assert tabmod.achievement_options([{"id": "7", "name": "Elden Lord", "grade": "gold", "earned": True},
                                       {"id": "A", "name": "First", "grade": "achievement"}]) == {
        "7": "Elden Lord · Gold · earned", "A": "First"}


def test_backlog_section_splits_the_library(env):
    env.store.upsert_games([psn_game(), steam_game()])
    env.service.add_goal(PSN, "NPWR00001_00", kind="manual", name="Get all endings", target=3)
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        assert tab.ui_elements["backlog"].visible
        assert [g.title for g in tab.visible_backlog()] == ["ELDEN RING"]
        assert [g.title for g in tab.visible_games()] == ["Hades"]
        await user.should_see("Get all endings")
        tab._set_filter("backlog", status="complete")
        assert tab.visible_backlog() == [] and tab.ui_elements["bl_none"].visible
        assert env.store.load_settings().view["backlog"]["status"] == "complete"  # remembered right away
        assert tab.dirty is False
        tab._set_opt(backlog_section=False)
        tab.save()
        assert tab.ui_elements["backlog"].visible is False
        assert [g.title for g in tab.visible_games()] == ["ELDEN RING", "Hades"]
        assert env.store.load_settings().view["backlog"]["status"] == "complete"  # Save kept the remembered filters

    run_ui(build_into(tabs), check)


def test_drawer_goals_add_count_move_edit_delete(env):
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        await user.should_see("No goals yet")
        await tab._add_goal()  # a manual goal needs a name
        assert env.notes[-1]["title"] == "Name the goal"
        tab._set_goal_form(name="Get all endings", target=2)
        await tab._add_goal()
        tab._set_goal_form(kind="percent")
        assert tab._goal_form["target"] == 50
        await tab._add_goal()
        goals = env.service.goals_for(PSN, "NPWR00001_00")
        assert [g.kind for g in goals] == ["manual", "percent"]
        await user.should_see("Get all endings")
        await tab._goal_op("adjust", goals[0].id, "add")
        await tab._goal_op("adjust", goals[1].id, "add")
        assert env.notes[-1]["title"] == "Couldn't change the goal"
        assert env.service.goals_for(PSN, "NPWR00001_00")[0].current == 1
        await tab._goal_op("move", goals[1].id, -1)
        assert [g.kind for g in env.service.goals_for(PSN, "NPWR00001_00")] == ["percent", "manual"]
        tab._edit_goal_dialog(goals[0])
        await user.should_see("Edit goal")
        await tab._goal_op("remove", goals[1].id)
        assert [g.kind for g in env.service.goals_for(PSN, "NPWR00001_00")] == ["manual"]
        assert tab._needs_refresh

    run_ui(build_into(tabs), check)


def test_drawer_unlock_goal_picker(env, monkeypatch):
    env.store.upsert_games([psn_game()])
    monkeypatch.setattr(env.service, "achievement_list", lambda p, gid: [
        {"id": "7", "name": "Elden Lord", "grade": "gold", "icon": "i.png", "earned": False}])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        tab._set_goal_form(kind="unlock")
        assert "disable" in tab.ui_elements["gf_add"].props
        await tab._load_achievements()
        assert tab.ui_elements["gf_unlock"].options == {"7": "Elden Lord · Gold"}
        tab._set_goal_form(unlock="7")
        await tab._add_goal()
        (goal,) = env.service.goals_for(PSN, "NPWR00001_00")
        assert (goal.kind, goal.unlock_id, goal.unlock_name, goal.unlock_icon) == ("unlock", "7", "Elden Lord", "i.png")

    run_ui(build_into(tabs), check)


def test_drawer_width_is_clamped_saved_and_applied(env):
    assert tabmod.clamp_drawer_width(None) == tabmod.DRAWER_DEFAULT_WIDTH
    assert tabmod.clamp_drawer_width("12") == tabmod.DRAWER_MIN_WIDTH
    assert tabmod.clamp_drawer_width(99999) == tabmod.DRAWER_MAX_WIDTH
    tabmod._on_drawer_width(SimpleNamespace(args=612.4))
    assert env.store.load_settings().view["drawer_width"] == 612
    assert "__MIN__" not in tabmod._DRAWER_RESIZE_JS and "mycelian-gtr-drawer-width" in tabmod._DRAWER_RESIZE_JS
    env.store.upsert_games([psn_game()])
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab._set_filter("library", sort="name")  # remembering list filters keeps the width
        assert env.store.load_settings().view["drawer_width"] == 612
        tab.open_drawer(tab._games[0])
        assert tab.ui_elements["drawer_body"]._style.get("width") == "612px"
        assert any("gtr-dresize" in c.classes for c in tab.ui_elements["drawer_body"].default_slot.children)
        await user.should_see("Goal name")

    run_ui(build_into(tabs), check)


# --------------------------------------------------------------------------- speedrun info


SPLITS = ('<?xml version="1.0" encoding="UTF-8"?><Run version="1.7.0"><GameName>Elden Ring</GameName>'
          '<CategoryName>{cat}</CategoryName><AttemptCount>72</AttemptCount><AttemptHistory>'
          '<Attempt id="1"><RealTime>01:36:25.5516722</RealTime></Attempt><Attempt id="2" /></AttemptHistory>'
          '<Segments><Segment><Name>End</Name><SplitTimes><SplitTime name="Personal Best"><RealTime>01:36:25.5516722</RealTime>'
          '</SplitTime></SplitTimes></Segment></Segments></Run>')


def test_speedrun_rows_html():
    rows = [{"id": "a", "category": "<Any%>", "pb": "1:36:25.55", "attempts": 72, "finished": 8, "read": True, "error": "",
             "file": "C:/x.lss"},
            {"id": "b", "category": "100%", "pb": "", "attempts": 3, "finished": 0, "read": True, "error": "Bad file", "file": "y"},
            {"id": "c", "category": "Low%", "pb": "", "attempts": 0, "finished": 0, "read": False, "error": "", "file": "z"}]
    html = tabmod.speedrun_rows_html(rows, 2)
    assert "&lt;Any%&gt;" in html and "1:36:25.55" in html and "72 attempts · 8 finished" in html
    assert "No finished run yet" in html and 'title="Bad file"' in html and "+1 more category" in html
    assert "Not read yet" in tabmod.speedrun_rows_html(rows[2:])
    assert tabmod.speedrun_rows_html([]) == ""
    assert "1:36:25.55" in tabmod.card_parts(psn_game(), now=NOW, speedruns=rows[:1])["top"]


def test_drawer_links_split_files_with_categories(env, tmp_path):
    env.store.upsert_games([psn_game()])
    any_pct = tmp_path / "any.lss"
    any_pct.write_text(SPLITS.format(cat="PC Any% 30FPS"), encoding="utf-8")
    hundred = tmp_path / "100.lss"
    hundred.write_text(SPLITS.format(cat="100%"), encoding="utf-8")
    tabs: List[Any] = []

    async def check(user):
        tab = tabs[0]
        await run_scheduled(env, "_refresh")
        tab.open_drawer(tab._games[0])
        await user.should_see("Speedrun info")
        await user.should_see("No split files yet")
        await tab._link_split_file(str(any_pct))  # blank category: the file's own
        tab.ui_elements["sr_category"].value = "Hundo"
        await tab._link_split_file(str(hundred), "Hundo")
        assert tab.ui_elements["sr_category"].value == ""
        await user.should_see("PC Any% 30FPS")
        await user.should_see("1:36:25.55")
        rows = tab._speedruns[tab._games[0].key]
        assert [r["category"] for r in rows] == ["PC Any% 30FPS", "Hundo"]
        await tab._link_split_file(str(any_pct))
        assert env.notes[-1]["title"] == "Couldn't link the split file"
        await tab._speedrun_op("move", rows[1]["id"], -1)
        assert [r["category"] for r in tab._speedruns[tab._games[0].key]] == ["Hundo", "PC Any% 30FPS"]
        tab._edit_speedrun_dialog(rows[0])
        await user.should_see("Edit split file")
        await tab._speedrun_op("remove", rows[0]["id"])
        await tab._speedrun_op("remove", rows[1]["id"])
        assert tab.ui_elements["d_speedruns"] is not None and tab.service.speedruns_for(PSN, "NPWR00001_00") == []

    run_ui(build_into(tabs), check)

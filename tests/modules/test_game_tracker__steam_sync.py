# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Steam library sync: played games only, silent first import, incremental sweeps, live observe."""

import threading

import pytest

from modules.game_tracker import steam_sync
from modules.game_tracker.models import STEAM, TrackerSettings
from modules.game_tracker.store import GameTrackerStore
from modules.steamapi import SteamAPIError, SteamConnectionError, SteamRateLimited


class FakeSteam:
    def __init__(self, owned, achievements, steam_id="76561190000000001"):
        self.owned = owned
        self.achievements = achievements
        self.steam_id = steam_id
        self.calls = []

    def resolve_steam_id(self):
        return self.steam_id

    def get_owned_games(self):
        return [dict(g) for g in self.owned]

    def get_game_achievements(self, appid):
        self.calls.append(appid)
        value = self.achievements.get(appid)
        if isinstance(value, Exception):
            raise value
        return value or {"has_achievements": False, "private": False, "total": 0, "unlocked": {}}

    def global_percents(self, appid):
        return {"b": 4.5}


def owned(appid, playtime=60, last=1000, name=None):
    return {"appid": appid, "name": name or f"Game {appid}", "playtime_min": playtime, "last_played": last, "icon_hash": ""}


def ach(total, *names):
    return {"has_achievements": total > 0, "private": False, "total": total,
            "unlocked": {n: {"name": n.upper(), "description": f"{n} desc", "unlock_time": i} for i, n in enumerate(names)}}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(steam_sync, "REQUEST_GAP_SEC", 0)


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


SETTINGS = TrackerSettings(milestones=[50])


def test_first_import_is_silent_and_skips_unplayed(db):
    client = FakeSteam([owned(10), owned(20, playtime=0), owned(30)], {10: ach(4, "a"), 30: ach(0)})
    assert steam_sync.sync_library(client, db, SETTINGS) == []
    games = db.games_by_id(STEAM)
    assert set(games) == {"10", "30"}
    assert games["10"].progress == 25 and games["10"].cover_url.endswith("/10/header.jpg")
    assert games["30"].has_achievements is False
    assert db.baseline(STEAM)["account"] == client.steam_id


def test_only_changed_games_are_reread_and_unlocks_named(db):
    client = FakeSteam([owned(10, last=1000), owned(30, last=1000)], {10: ach(4, "a"), 30: ach(2)})
    steam_sync.sync_library(client, db, SETTINGS)
    client.calls.clear()
    client.owned = [owned(10, last=2000, playtime=90), owned(30, last=1000)]
    client.achievements[10] = ach(4, "a", "b", "c")
    events = steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [10]
    assert [(e.kind, e.unlock_name or e.milestone) for e in events] == [("unlock", "B"), ("unlock", "C"), ("milestone", 50)]
    b = next(e for e in events if e.unlock_name == "B")
    assert b.unlock_rarity == 4.5 and b.unlock_description == "b desc"
    assert db.get_game(STEAM, "10").playtime_min == 90


def test_new_game_after_import_counts_all_its_unlocks(db):
    client = FakeSteam([owned(10)], {10: ach(2)})
    steam_sync.sync_library(client, db, SETTINGS)
    client.owned.append(owned(40, last=5000))
    client.achievements[40] = ach(2, "x", "y")
    events = steam_sync.sync_library(client, db, SETTINGS)
    assert [e.kind for e in events] == ["started", "unlock", "unlock", "milestone", "completed"]


def test_failed_first_read_is_silent_later(db, monkeypatch):
    client = FakeSteam([owned(10)], {10: SteamAPIError("500 oops", 500)})
    steam_sync.sync_library(client, db, SETTINGS)
    assert db.get_game(STEAM, "10").baselined is False
    client.achievements[10] = ach(2, "a", "b")
    client.calls.clear()
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == []  # a failed read waits before it's tried again
    later = steam_sync.time.time() + steam_sync.RETRY_READ_SEC + 1
    monkeypatch.setattr(steam_sync.time, "time", lambda: later)
    assert steam_sync.sync_library(client, db, SETTINGS) == []
    assert db.get_game(STEAM, "10").baselined is True
    assert steam_sync.FAILED_AT not in db.get_game(STEAM, "10").extra


def test_private_profile_stops_the_sweep(db):
    private = {"has_achievements": True, "private": True, "total": 0, "unlocked": {}}
    client = FakeSteam([owned(10, last=3), owned(20, last=2), owned(30, last=1)], {10: private})
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [10]
    assert db.get_game(STEAM, "10").extra.get("private") is True
    assert db.get_game(STEAM, "20").baselined is False


def test_connection_errors_bubble_and_unavailable_returns_none(db):
    assert steam_sync.sync_library(None, db, SETTINGS) is None
    client = FakeSteam([owned(10)], {10: SteamConnectionError("down")})
    with pytest.raises(SteamConnectionError):
        steam_sync.sync_library(client, db, SETTINGS)

    class NoLibrary(FakeSteam):
        def get_owned_games(self):
            raise SteamAPIError("403", 403)

    assert steam_sync.sync_library(NoLibrary([], {}), db, SETTINGS) is None
    assert steam_sync.sync_library(FakeSteam([], {}, steam_id=None), db, SETTINGS) is None


def test_stop_event_interrupts_first_import_without_baseline(db):
    stop = threading.Event()
    stop.set()
    client = FakeSteam([owned(10)], {10: ach(1)})
    steam_sync.sync_library(client, db, SETTINGS, stop_event=stop)
    assert db.baseline(STEAM) is None


def test_profile_switch_reimports_silently(db):
    steam_sync.sync_library(FakeSteam([owned(10)], {10: ach(1)}), db, SETTINGS)
    other = FakeSteam([owned(99)], {99: ach(1, "z")}, steam_id="76561190000000002")
    assert steam_sync.sync_library(other, db, SETTINGS) == []
    assert set(db.games_by_id(STEAM)) == {"99"}


def test_observe_game_uses_live_list(db):
    client = FakeSteam([owned(10)], {10: ach(3, "a")})
    live = [
        {"api_name": "a", "name": "A", "achieved": True, "unlock_time": 1, "global_percent": 50.0, "icon_url": "https://i/a"},
        {"api_name": "b", "name": "B", "achieved": True, "unlock_time": 2, "global_percent": 2.0, "icon_url": "https://i/b"},
        {"api_name": "c", "name": "C", "achieved": False},
    ]
    assert steam_sync.observe_game(db, SETTINGS, client.steam_id, 10, "Game 10", live) == []  # before import
    steam_sync.sync_library(client, db, SETTINGS)
    events = steam_sync.observe_game(db, SETTINGS, client.steam_id, 10, "Game 10", live)
    assert [(e.kind, e.unlock_name) for e in events if e.kind == "unlock"] == [("unlock", "B")]
    assert events[0].unlock_rarity == 2.0 and events[0].unlock_icon == "https://i/b"
    assert [e.milestone for e in events if e.kind == "milestone"] == [50]
    assert steam_sync.observe_game(db, SETTINGS, "someone-else", 10, "Game 10", live) == []


def test_cover_items():
    from modules.game_tracker.models import TrackedGame

    assert steam_sync.cover_items([TrackedGame(platform=STEAM, game_id="1", cover_url="https://c")]) == [(STEAM, "1", "https://c")]


def test_store_header_url(monkeypatch):
    import requests

    calls = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    bodies = {
        "3527290": {"3527290": {"success": True, "data": {"header_image": "https://shared/3527290/abc/header.jpg?t=1"}}},
        "9": {"9": {"success": False}},
        "8": {"8": {"success": True, "data": {"header_image": "http://plain"}}},
    }

    def get(url, params, timeout):
        calls.append((url, params, timeout))
        return Resp(bodies[params["appids"]])

    monkeypatch.setattr(requests, "get", get)
    assert steam_sync.store_header_url(3527290) == "https://shared/3527290/abc/header.jpg?t=1"
    assert steam_sync.store_header_url("9") is None and steam_sync.store_header_url("8") is None
    assert calls[0] == (steam_sync.STORE_DETAILS_URL, {"appids": "3527290", "filters": "basic"}, 15)
    from modules.game_tracker import covers

    assert covers._fallbacks[STEAM] is steam_sync.store_header_url


def test_rate_limit_stops_the_sweep_and_keeps_old_values(db):
    client = FakeSteam([owned(10, last=1000), owned(20, last=1000), owned(30, last=1000)],
                       {10: ach(4, "a"), 20: ach(2), 30: ach(2)})
    steam_sync.sync_library(client, db, SETTINGS)
    client.calls.clear()
    client.owned = [owned(10, last=3000), owned(20, last=2000), owned(30, last=1500)]
    client.achievements[10] = ach(4, "a", "b")
    client.achievements[20] = SteamRateLimited("429", 429)
    events = steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [10, 20]  # stopped at the 429: 30 is left for the next sweep
    assert [e.unlock_name for e in events if e.kind == "unlock"] == ["B"]
    games = db.games_by_id(STEAM)
    assert games["20"].baselined and games["20"].total_achievements == 2  # kept its last read


def test_rate_limit_during_first_import_leaves_the_baseline_unset(db):
    client = FakeSteam([owned(10, last=3000), owned(20, last=2000)], {10: ach(4, "a"), 20: SteamRateLimited("429", 429)})
    steam_sync.sync_library(client, db, SETTINGS)
    assert db.baseline(STEAM) is None
    assert set(db.games_by_id(STEAM)) == {"10", "20"}


def test_private_game_is_rechecked_every_six_hours_not_every_sync(db, monkeypatch):
    private = {"has_achievements": True, "private": True, "total": 0, "unlocked": {}}
    client = FakeSteam([owned(10, last=3)], {10: private})
    steam_sync.sync_library(client, db, SETTINGS)
    client.calls.clear()
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == []
    later = steam_sync.time.time() + steam_sync.RETRY_READ_SEC + 1
    monkeypatch.setattr(steam_sync.time, "time", lambda: later)
    client.achievements[10] = ach(2, "a")
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [10] and "private" not in db.get_game(STEAM, "10").extra


def test_the_game_the_live_poller_watches_is_not_reread(db):
    from types import SimpleNamespace

    client = FakeSteam([owned(10, last=1000), owned(20, last=1000)], {10: ach(4, "a"), 20: ach(2)})
    steam_sync.sync_library(client, db, SETTINGS)
    client.calls.clear()
    client.connected = True
    client.steam_data = SimpleNamespace(current_game_appid=10, has_achievements=True, achievements_private=False)
    client.owned = [owned(10, last=2000, playtime=120), owned(20, last=2000, playtime=90)]
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [20]  # 10 is read every few seconds by the poller already
    assert db.get_game(STEAM, "10").playtime_min == 120  # the library's playtime still lands
    client.steam_data.current_game_appid = None  # the session ended: read it once more
    client.owned = [owned(10, last=3000, playtime=130), owned(20, last=2000, playtime=90)]
    client.calls.clear()
    steam_sync.sync_library(client, db, SETTINGS)
    assert client.calls == [10]


def test_live_appid_needs_a_connected_poller_with_public_achievements():
    from types import SimpleNamespace

    data = SimpleNamespace(current_game_appid=10, has_achievements=True, achievements_private=False)
    assert steam_sync._live_appid(SimpleNamespace(connected=True, steam_data=data)) == 10
    assert steam_sync._live_appid(SimpleNamespace(connected=False, steam_data=data)) is None
    assert steam_sync._live_appid(SimpleNamespace()) is None
    data.achievements_private = True
    assert steam_sync._live_appid(SimpleNamespace(connected=True, steam_data=data)) is None

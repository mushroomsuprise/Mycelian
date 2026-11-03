# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""PlayStation library sync: silent first import, deltas, cache entries, naming, edits."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from psnawp_api.models.trophies import PlatformType

from modules import psnapi
from modules.game_tracker import psn_sync
from modules.game_tracker.models import PSN, TrackerSettings
from modules.game_tracker.store import GameTrackerStore


def ts(**kw):
    return SimpleNamespace(bronze=kw.get("b", 0), silver=kw.get("s", 0), gold=kw.get("g", 0), platinum=kw.get("p", 0))


def title(comm_id, name, earned, defined, progress, platform="PS5", updated=None):
    return SimpleNamespace(
        np_communication_id=comm_id, title_name=name, title_platform=frozenset({PlatformType(platform)}),
        title_icon_url=f"https://img/{comm_id}.png", earned_trophies=earned, defined_trophies=defined, progress=progress,
        last_updated_datetime=updated or datetime(2026, 1, 2, tzinfo=timezone.utc), has_trophy_groups=False,
    )


def trophy(name, grade, earned_at, rate=12.5):
    return SimpleNamespace(trophy_name=name, trophy_detail=f"{name} detail", trophy_icon_url=f"https://t/{name}",
                           trophy_type=SimpleNamespace(value=grade), earned=True, trophy_earn_rate=rate,
                           earned_date_time=datetime.fromtimestamp(earned_at, tz=timezone.utc))


class FakeUser:
    def __init__(self, client):
        self.client = client

    def trophy_titles(self, limit=None, offset=0, page_size=50):
        return iter(self.client.titles[offset:offset + page_size])

    def trophies(self, np_communication_id, platform, include_progress, trophy_group_id):
        self.client.trophy_calls.append((np_communication_id, platform, trophy_group_id))
        return iter(self.client.trophies.get(np_communication_id, []))


class FakeClient:
    def __init__(self, titles=(), account="acct-1"):
        self.titles = list(titles)
        self.trophies = {}
        self.trophy_calls = []
        self.account = account
        self.connected = True
        self.api = SimpleNamespace(user=lambda account_id: FakeUser(self))

    def is_connected(self):
        return self.connected

    def get_trophy_target_account_id(self):
        return self.account

    def resolve_np_communication_id_from_np_title_id(self, np_title_id):
        return {"PPSA00001_00": "NPWR99999_00"}.get(np_title_id)


@pytest.fixture
def cache(monkeypatch):
    docs = {}
    monkeypatch.setattr(psnapi, "load_all_psn_game_cache_docs_from_db", lambda: [dict(d) for d in docs.values()])
    monkeypatch.setattr(psnapi, "get_psn_game_cache_by_comm_id", lambda cid: dict(docs[cid]) if cid in docs else None)

    def store(doc):
        docs[doc["np_communication_id"]] = dict(doc)
        return True

    def update(cid, updates):
        if cid not in docs:
            return False
        docs[cid].update(updates)
        return True

    monkeypatch.setattr(psnapi, "store_psn_game_cache_in_db", store)
    monkeypatch.setattr(psnapi, "update_psn_game_cache_in_db", update)
    monkeypatch.setattr(psnapi, "delete_psn_game_cache_in_db", lambda cid: docs.pop(cid, None) is not None)
    return docs


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


SETTINGS = TrackerSettings(milestones=[25, 50, 75])


def test_platform_label_prefers_newest_console():
    assert psn_sync.platform_label(frozenset({PlatformType.PS4, PlatformType.PS5})) == "PS5"
    assert psn_sync.platform_label("PSVITA") == "PS Vita"
    assert psn_sync.platform_label(["PSPC"]) == "PC"
    assert psn_sync.platform_label(None) == "" and psn_sync.platform_label([PlatformType.UNKNOWN]) == ""


def test_first_import_is_silent_and_fills_store_and_cache(db, cache):
    client = FakeClient([title("NPWR00001_00", "ELDEN RING", ts(b=5), ts(b=10, p=1), 30),
                         title("NPWR00002_00", "Astro", ts(b=2, p=1), ts(b=2, p=1), 100, platform="PS4")])
    seen = []
    events = psn_sync.sync_library(client, db, SETTINGS, progress=lambda d, t: seen.append((d, t)))
    assert events == []
    assert db.baseline(PSN)["account"] == "acct-1"
    games = db.games_by_id(PSN)
    assert games["NPWR00001_00"].progress == 30 and games["NPWR00002_00"].platform_label == "PS4"
    assert games["NPWR00001_00"].last_played > 0 and games["NPWR00001_00"].first_seen > 0
    assert set(cache) == {"NPWR00001_00", "NPWR00002_00"}
    assert cache["NPWR00001_00"]["trophy_name"] == "ELDEN RING" and cache["NPWR00001_00"]["cover_art_url"]
    assert seen[-1] == (2, 2)
    assert client.trophy_calls == []


def test_later_sync_reports_unlocks_with_names(db, cache):
    client = FakeClient([title("NPWR00001_00", "ELDEN RING", ts(b=5), ts(b=10, g=1), 20)])
    psn_sync.sync_library(client, db, SETTINGS)
    client.titles = [title("NPWR00001_00", "ELDEN RING", ts(b=6, g=1), ts(b=10, g=1), 40)]
    client.trophies["NPWR00001_00"] = [trophy("Gold one", "gold", 200), trophy("Newest bronze", "bronze", 300),
                                       trophy("Old bronze", "bronze", 100)]
    events = psn_sync.sync_library(client, db, SETTINGS)
    assert [(e.kind, e.unlock_grade, e.unlock_name) for e in events if e.kind == "unlock"] == [
        ("unlock", "bronze", "Newest bronze"), ("unlock", "gold", "Gold one")]
    assert [e.milestone for e in events if e.kind == "milestone"] == [25]
    assert events[0].unlock_rarity == 12.5 and events[0].unlock_description == "Newest bronze detail"


def test_new_game_after_import_is_started(db, cache):
    client = FakeClient([title("NPWR00001_00", "A", ts(b=1), ts(b=10), 5)])
    psn_sync.sync_library(client, db, SETTINGS)
    client.titles.append(title("NPWR00003_00", "New", ts(b=1), ts(b=10), 5))
    events = psn_sync.sync_library(client, db, SETTINGS)
    assert [e.kind for e in events] == ["started", "unlock"]


def test_account_switch_wipes_and_reimports_silently(db, cache):
    client = FakeClient([title("NPWR00001_00", "A", ts(b=1), ts(b=10), 5)])
    psn_sync.sync_library(client, db, SETTINGS)
    other = FakeClient([title("NPWR00007_00", "B", ts(b=9), ts(b=10), 90)], account="acct-2")
    assert psn_sync.sync_library(other, db, SETTINGS) == []
    assert set(db.games_by_id(PSN)) == {"NPWR00007_00"}
    assert db.baseline(PSN)["account"] == "acct-2"


def test_unavailable_client_returns_none(db, cache):
    assert psn_sync.sync_library(None, db, SETTINGS) is None
    c = FakeClient()
    c.connected = False
    assert psn_sync.sync_library(c, db, SETTINGS) is None

    class Broken(FakeClient):
        def __init__(self):
            super().__init__()
            self.api = SimpleNamespace(user=lambda account_id: (_ for _ in ()).throw(RuntimeError("boom")))

    assert psn_sync.sync_library(Broken(), db, SETTINGS) is None
    assert db.baseline(PSN) is None


def test_user_edits_survive_syncs(db, cache):
    client = FakeClient([title("NPWR00001_00", "ELDEN RING", ts(b=1), ts(b=10), 5)])
    psn_sync.sync_library(client, db, SETTINGS)
    game, err = psn_sync.apply_edit(db, "NPWR00001_00", title="Elden Ring", np_title_id="ppsa04609_00", hidden=True)
    assert err == "" and game.display_title == "Elden Ring" and game.np_title_id == "PPSA04609_00"
    assert cache["NPWR00001_00"]["presence_name"] == "Elden Ring" and cache["NPWR00001_00"]["np_title_id"] == "PPSA04609_00"
    psn_sync.sync_library(client, db, SETTINGS)
    again = db.get_game(PSN, "NPWR00001_00")
    assert again.title_override == "Elden Ring" and again.hidden and again.np_title_id == "PPSA04609_00"


def test_existing_cache_entry_keeps_presence_name(db, cache):
    cache["NPWR00001_00"] = {"np_communication_id": "NPWR00001_00", "presence_name": "Elden Ring", "np_title_id": "PPSA1_00",
                             "trophy_name": "", "cover_art_url": None}
    psn_sync.sync_library(FakeClient([title("NPWR00001_00", "ELDEN RING", ts(), ts(b=1), 0)]), db, SETTINGS)
    g = db.get_game(PSN, "NPWR00001_00")
    assert g.display_title == "Elden Ring" and g.np_title_id == "PPSA1_00"
    assert cache["NPWR00001_00"]["presence_name"] == "Elden Ring"
    assert cache["NPWR00001_00"]["trophy_name"] == "ELDEN RING" and cache["NPWR00001_00"]["cover_art_url"]


def test_npwr_edit_moves_game_and_cache_entry(db, cache):
    psn_sync.sync_library(FakeClient([title("NPWR00001_00", "A", ts(), ts(b=1), 0)]), db, SETTINGS)
    game, err = psn_sync.apply_edit(db, "NPWR00001_00", np_communication_id="npwr00005_00")
    assert err == "" and game.game_id == "NPWR00005_00"
    assert "NPWR00005_00" in cache and "NPWR00001_00" not in cache
    assert db.get_game(PSN, "NPWR00001_00") is None


@pytest.mark.parametrize("value", ["NPWR1_00", "CUSA00001_00", ""])
def test_bad_npwr_is_refused(db, cache, value):
    psn_sync.sync_library(FakeClient([title("NPWR00001_00", "A", ts(), ts(b=1), 0)]), db, SETTINGS)
    game, err = psn_sync.apply_edit(db, "NPWR00001_00", np_communication_id=value)
    assert game is None and "NPWR12345_00" in err


def test_npwr_already_used_and_unknown_game(db, cache):
    psn_sync.sync_library(FakeClient([title("NPWR00001_00", "A", ts(), ts(b=1), 0),
                                      title("NPWR00002_00", "B", ts(), ts(b=1), 0)]), db, SETTINGS)
    assert "already tracked" in psn_sync.apply_edit(db, "NPWR00001_00", np_communication_id="NPWR00002_00")[1]
    assert psn_sync.apply_edit(db, "NPWR00404_00", title="x")[0] is None


def test_observe_title_waits_for_import_then_reports(db, cache):
    client = FakeClient([title("NPWR00001_00", "A", ts(b=1), ts(b=10), 10)])
    result = {"np_communication_id": "NPWR00001_00", "np_title_id": "PPSA1_00", "title_name": "A",
              "earned_trophies": {"bronze": 3, "total": 3}, "defined_trophies": {"bronze": 10, "total": 10},
              "progress": 30, "platform": frozenset({PlatformType.PS5}), "icon_url": "https://i"}
    assert psn_sync.observe_title(client, db, SETTINGS, result) == []  # no import yet
    psn_sync.sync_library(client, db, SETTINGS)
    events = psn_sync.observe_title(client, db, SETTINGS, result)
    assert [e.kind for e in events] == ["unlock", "unlock", "milestone"]
    g = db.get_game(PSN, "NPWR00001_00")
    assert g.np_title_id == "PPSA1_00" and g.earned["bronze"] == 3
    assert psn_sync.observe_title(client, db, SETTINGS, {}) == []


def test_resolve_and_remove(db, cache):
    assert psn_sync.resolve_npwr(FakeClient(), "ppsa00001_00") == "NPWR99999_00"
    assert psn_sync.resolve_npwr(None, "x") is None
    psn_sync.sync_library(FakeClient([title("NPWR00001_00", "A", ts(), ts(b=1), 0)]), db, SETTINGS)
    assert psn_sync.remove_game(db, "NPWR00001_00") and "NPWR00001_00" not in cache
    assert psn_sync.cover_items(db.list_games()) == []


def test_group_progress_is_cached_and_safe(monkeypatch):
    monkeypatch.setattr(psn_sync, "_groups_cache", {})
    calls = []

    class User(FakeUser):
        def trophy_groups_summary(self, np_communication_id, platform, include_progress):
            calls.append((np_communication_id, platform))
            return SimpleNamespace(trophy_groups=[
                SimpleNamespace(trophy_group_id="default", trophy_group_name=None, earned_trophies=ts(b=3),
                                defined_trophies=ts(b=4), progress=None),
                SimpleNamespace(trophy_group_id="001", trophy_group_name="DLC", earned_trophies=ts(g=1),
                                defined_trophies=ts(g=2), progress=50),
            ])

    client = FakeClient()
    client.api = SimpleNamespace(user=lambda account_id: User(client))
    groups = psn_sync.group_progress(client, "NPWR00001_00", "PS4")
    assert groups == [
        {"id": "default", "name": "Base game", "earned": 3, "defined": 4, "progress": 75},
        {"id": "001", "name": "DLC", "earned": 1, "defined": 2, "progress": 50},
    ]
    assert psn_sync.group_progress(client, "NPWR00001_00", "PS4") is groups and len(calls) == 1
    assert calls[0][1] == PlatformType.PS4
    assert psn_sync.group_progress(None, "NPWR00002_00", "PS5") is None
    broken = FakeClient()
    broken.api = SimpleNamespace(user=lambda account_id: (_ for _ in ()).throw(RuntimeError("x")))
    assert psn_sync.group_progress(broken, "NPWR00003_00", "PS5") is None


def test_service_group_progress(db, monkeypatch):
    from modules.game_tracker import service as svc

    service = svc.GameTrackerService(db, psn_client_fn=lambda: "client", steam_client_fn=lambda: None)
    assert service.group_progress("NPWR00404_00") is None
    db.upsert_games([psn_sync.title_to_game({"np_communication_id": "NPWR00001_00", "name": "A", "platform": "PS4"})])
    monkeypatch.setattr(psn_sync, "group_progress", lambda client, cid, label: [(client, cid, label)])
    assert service.group_progress("NPWR00001_00") == [("client", "NPWR00001_00", "PS4")]

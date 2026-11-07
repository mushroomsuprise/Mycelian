# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The Games Tracker catalog: rarely-changing game data kept in game_tracker.db."""

from types import SimpleNamespace

import pytest

from modules.game_tracker import catalog
from modules.game_tracker.store import GameTrackerStore


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


def test_store_catalog_roundtrip(db):
    assert db.catalog_get("k", "1") is None
    db.catalog_put("k", 1, {"a": [1, 2]}, fetched_at=50)
    assert db.catalog_get("k", "1") == ({"a": [1, 2]}, 50.0)
    db.catalog_put("k", "1", {"b": 1})
    data, at = db.catalog_get("k", "1")
    assert data == {"b": 1} and at > 50
    db.catalog_put("k", "2", [])
    assert db.catalog_delete("k", "1") == 1 and db.catalog_get("k", "1") is None
    assert db.catalog_delete("k") == 1 and db.catalog_get("k", "2") is None


class Defs:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.trophies = [SimpleNamespace(trophy_id=0, trophy_group_id=None, trophy_type=SimpleNamespace(value="Gold"),
                                         trophy_name="Win", trophy_detail="d", trophy_icon_url="i", trophy_hidden=True)]

    def trophies_fn(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return iter(self.trophies)


def client_for(defs):
    user = SimpleNamespace(trophies=defs.trophies_fn)
    return SimpleNamespace(api=object(), is_connected=lambda: True, get_trophy_target_account_id=lambda: "acct",
                           user_for=lambda account: user)


def test_psn_definitions_cache_ttl_and_stale_fallback(db, monkeypatch):
    defs = Defs()
    client = client_for(defs)
    out = catalog.psn_definitions(client, "NPWR1_00", "PS5", {"gold": 1}, store=db)
    assert out == [{"id": "0", "group": "default", "grade": "gold", "name": "Win", "description": "d", "icon": "i", "hidden": True}]
    assert catalog.psn_definitions(client, "NPWR1_00", "PS5", {"gold": 1}, store=db) == out and defs.calls == 1
    assert catalog.psn_definitions(client, "NPWR1_00", "PS5", None, store=db) == out and defs.calls == 1  # unknown counts fit
    assert catalog.psn_definitions(client, "NPWR1_00", "PS5", {}, store=db) == out and defs.calls == 1
    now = catalog.time.time()
    monkeypatch.setattr(catalog.time, "time", lambda: now + catalog.PSN_DEFS_TTL_SEC + 1)
    defs.fail = True
    assert catalog.psn_definitions(client, "NPWR1_00", "PS5", {"gold": 1}, store=db) == out  # stale beats nothing
    assert defs.calls == 2
    assert catalog.psn_definitions(client, "NPWR9_00", "PS5", None, store=db) is None


def test_psn_needs_a_connected_client(db):
    off = SimpleNamespace(api=object(), is_connected=lambda: False)
    assert catalog.psn_definitions(off, "NPWR1_00", "PS5", store=db) is None
    assert catalog.psn_groups(None, "NPWR1_00", "PS5", store=db) is None


def test_steam_entries(db):
    assert catalog.steam_schema(620, store=db) is None and catalog.steam_percents(620, store=db) is None
    catalog.save_steam_schema(620, {"A": {"displayName": "x"}}, store=db)
    catalog.save_steam_percents(620, {"A": 2.5}, store=db)
    assert catalog.steam_schema(620, store=db)[0] == {"A": {"displayName": "x"}}
    assert catalog.steam_percents("620", store=db)[0] == {"A": 2.5}


def test_catalog_errors_are_swallowed():
    class Broken:
        def catalog_get(self, *a):
            raise OSError("locked")

        def catalog_put(self, *a):
            raise OSError("locked")

    assert catalog.steam_schema(1, store=Broken()) is None
    catalog.save_steam_schema(1, {}, store=Broken())  # logged, not raised


# --------------------------------------------------------------------------- the store's memory cache


def test_store_reads_from_memory_and_stays_in_step(db, tmp_path):
    from modules.game_tracker.models import PSN, STEAM, TrackedGame

    db.upsert_games([TrackedGame(platform=PSN, game_id="NPWR1_00", title="A"),
                     TrackedGame(platform=STEAM, game_id="10", title="B")])
    v = db.version
    assert {g.game_id for g in db.list_games()} == {"NPWR1_00", "10"} and db.version == v  # reads don't count
    game = db.get_game(PSN, "NPWR1_00")
    game.extra["x"] = 1  # a caller's copy: the cache isn't touched
    assert db.get_game(PSN, "NPWR1_00").extra == {}
    db.upsert_games([game])
    assert db.get_game(PSN, "NPWR1_00").extra == {"x": 1} and db.version == v + 1
    db.rekey_game(PSN, "NPWR1_00", "NPWR2_00")
    assert db.get_game(PSN, "NPWR1_00") is None and db.get_game(PSN, "NPWR2_00").title == "A"
    assert db.delete_game(STEAM, "10") and db.get_game(STEAM, "10") is None
    db.clear_platform(PSN)
    assert db.list_games() == [] and db.version > v + 1
    # Another store on the same file sees what was written (the DB is still the source of truth).
    db.upsert_games([TrackedGame(platform=STEAM, game_id="20", title="C")])
    again = GameTrackerStore(db_path=db.path)
    assert [g.game_id for g in again.list_games(STEAM)] == ["20"]


def test_meta_is_cached_and_written_through(db):
    assert db.get_meta("k", "dflt") == "dflt"
    db.set_meta("k", {"a": 1})
    value = db.get_meta("k")
    value["a"] = 2  # a copy
    assert db.get_meta("k") == {"a": 1}
    assert GameTrackerStore(db_path=db.path).get_meta("k") == {"a": 1}
    db.delete_meta("k")
    assert db.get_meta("k") is None

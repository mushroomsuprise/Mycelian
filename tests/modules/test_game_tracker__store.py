# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The tracker's SQLite store: games, change history, settings, baselines."""

import time

import pytest

from modules.game_tracker import store as store_mod
from modules.game_tracker.models import PSN, STEAM, GameEvent, TrackedGame, TrackerSettings
from modules.game_tracker.store import GameTrackerStore


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


def game(platform=PSN, gid="NPWR00001_00", **kw):
    return TrackedGame(platform=platform, game_id=gid, title=kw.pop("title", "Game"), **kw)


def test_upsert_list_get_delete(db):
    db.upsert_games([game(), game(STEAM, "10", title="S")])
    assert {g.key for g in db.list_games()} == {"psn:NPWR00001_00", "steam:10"}
    assert [g.game_id for g in db.list_games(STEAM)] == ["10"]
    db.upsert_games([game(title="Renamed")])
    assert db.get_game(PSN, "NPWR00001_00").title == "Renamed"
    assert db.get_game(PSN, "missing") is None
    assert db.delete_game(STEAM, "10") and not db.delete_game(STEAM, "10")
    assert set(db.games_by_id(PSN)) == {"NPWR00001_00"}


def test_rekey_moves_game_and_its_history(db):
    db.upsert_games([game()])
    db.record_events([GameEvent(kind="unlock", platform=PSN, game_id="NPWR00001_00", game_title="Game", unlock_grade="gold")])
    moved = db.rekey_game(PSN, "NPWR00001_00", "NPWR00002_00")
    assert moved.game_id == "NPWR00002_00"
    assert db.get_game(PSN, "NPWR00001_00") is None and db.get_game(PSN, "NPWR00002_00")
    assert db.recent_events(game_id="NPWR00002_00")[0].unlock_grade == "gold"
    assert db.rekey_game(PSN, "nope", "x") is None


def test_events_recent_counts_and_trim(db, monkeypatch):
    now = time.time()
    events = [
        GameEvent(kind="unlock", platform=PSN, game_id="A", game_title="A", unlock_grade="bronze", ts=now - 100),
        GameEvent(kind="unlock", platform=STEAM, game_id="B", game_title="B", unlock_grade="achievement", ts=now - 50),
        GameEvent(kind="completed", platform=PSN, game_id="A", game_title="A", ts=now - 10),
        GameEvent(kind="started", platform=PSN, game_id="C", game_title="C", ts=now - 99999),
    ]
    assert db.record_events(events) == 4
    assert [e.game_id for e in db.recent_events(5)] == ["B", "A"]
    assert [e.kind for e in db.recent_events(5, kinds=("completed", "started"))] == ["completed", "started"]
    assert db.recent_events(5, platform=STEAM)[0].game_id == "B"
    counts = db.event_counts(since=now - 1000)
    assert counts == {"unlock": 2, "grade:bronze": 1, "grade:achievement": 1, "completed": 1}
    assert db.event_counts(since=0, platform=PSN)["started"] == 1

    monkeypatch.setattr(store_mod, "EVENT_LOG_MAX", 2)
    db.record_events([GameEvent(kind="unlock", platform=PSN, game_id="Z", game_title="Z", ts=now)])
    assert len(db.recent_events(10, kinds=("unlock", "completed", "started"))) == 2


def test_settings_round_trip_and_defaults(db):
    assert db.load_settings() == TrackerSettings()
    db.save_settings(TrackerSettings(sync_minutes=30, milestones=[50, 10], export={"layout": "square"}))
    s = db.load_settings()
    assert s.sync_minutes == 30 and s.milestones == [10, 50] and s.export == {"layout": "square"}


def test_baselines_and_reset(db):
    assert db.baseline(PSN) is None
    db.set_baseline(PSN, "acct-1")
    assert db.baseline(PSN)["account"] == "acct-1" and db.baseline(STEAM) is None
    db.upsert_games([game()])
    db.reset_baseline(PSN)
    assert db.get_game(PSN, "NPWR00001_00").baselined is False
    db.delete_meta("baseline:psn")
    assert db.baseline(PSN) is None


def test_clear_platform_only_touches_that_platform(db):
    db.upsert_games([game(), game(STEAM, "10")])
    db.record_events([GameEvent(kind="unlock", platform=PSN, game_id="NPWR00001_00", game_title="G")])
    db.clear_platform(PSN)
    assert [g.platform for g in db.list_games()] == [STEAM]
    assert db.recent_events(5) == []


def test_unreadable_rows_and_meta_are_skipped(db):
    conn = db._connect()
    conn.execute("INSERT INTO games VALUES ('psn', 'bad', 'not json', 0)")
    conn.execute("INSERT INTO meta VALUES ('x', 'not json')")
    conn.commit()
    conn.close()
    assert db.list_games() == []
    assert db.get_meta("x", "fallback") == "fallback"


def test_shared_store_can_be_swapped(tmp_path):
    custom = GameTrackerStore(db_path=str(tmp_path / "other.db"))
    store_mod.set_store(custom)
    try:
        assert store_mod.get_store() is custom
    finally:
        store_mod.set_store(None)
    assert store_mod.get_store().path.endswith("game_tracker.db")
    store_mod.set_store(None)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Speedrun links kept in memory by the store, and the watcher idling with nothing linked (SVC-14)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.game_tracker.models import PSN, STEAM, TrackedGame  # noqa: E402
from modules.game_tracker.speedrun import SpeedrunLink, SpeedrunWatcher  # noqa: E402
from modules.game_tracker.store import GameTrackerStore  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


def link(platform=PSN, gid="NPWR1_00", path="/runs/a.lss", **kw):
    return SpeedrunLink(platform=platform, game_id=gid, path=path, **kw)


def test_speedrun_links_round_trip_and_survive_a_new_store(db, tmp_path):
    a, b, c = link(order=1), link(path="/runs/b.lss", order=0), link(STEAM, "10", "/runs/c.lss")
    for x in (a, b, c):
        db.upsert_speedrun(x)
    assert [x.path for x in db.list_speedruns(PSN, "NPWR1_00")] == ["/runs/b.lss", "/runs/a.lss"]
    assert {x.id for x in db.list_speedruns()} == {a.id, b.id, c.id}
    assert [x.id for x in db.list_speedruns(STEAM)] == [c.id]
    a.category = "Any%"
    db.upsert_speedrun(a)
    assert db.get_speedrun(a.id).category == "Any%"
    assert db.delete_speedrun(b.id) and not db.delete_speedrun(b.id)
    assert db.get_speedrun(b.id) is None
    fresh = GameTrackerStore(db_path=str(tmp_path / "gt.db"))  # what is on disk matches memory
    assert {x.id: x.category for x in fresh.list_speedruns()} == {a.id: "Any%", c.id: ""}


def test_listing_speedruns_does_not_open_the_database(db, monkeypatch):
    db.upsert_speedrun(link())
    db.list_speedruns()  # first read loads them
    monkeypatch.setattr(db, "_connect", lambda: pytest.fail("opened the database"))
    for _ in range(3):
        assert len(db.list_speedruns()) == 1
        assert db.get_speedrun(db.list_speedruns()[0].id) is not None


def test_deleting_a_game_drops_its_links_unless_goals_are_kept(db):
    db.upsert_games([TrackedGame(platform=PSN, game_id="NPWR1_00", title="A")])
    keep, gone = link(), link(STEAM, "10", "/runs/c.lss")
    db.upsert_speedrun(keep)
    db.upsert_speedrun(gone)
    db.list_speedruns()
    db.delete_game(STEAM, "10")
    assert [x.id for x in db.list_speedruns()] == [keep.id]
    db.delete_game(PSN, "NPWR1_00", keep_goals=True)
    assert [x.id for x in db.list_speedruns()] == [keep.id]


def test_rekey_moves_links(db):
    db.upsert_games([TrackedGame(platform=PSN, game_id="OLD", title="A")])
    db.upsert_speedrun(link(gid="OLD"))
    db.rekey_game(PSN, "OLD", "NEW")
    assert [x.game_id for x in db.list_speedruns()] == ["NEW"]


def test_watcher_sleeps_until_poked_when_nothing_is_linked():
    links = []
    loads = []
    watcher = SpeedrunWatcher(lambda: loads.append(1) or list(links), lambda l: None, lambda keys: None, poll_sec=0.01)
    watcher.start()
    try:
        deadline = threading.Event()
        deadline.wait(0.2)
        assert len(loads) == 1, "no links: no polling"
        links.append(link(path="/nope.lss"))
        watcher.poke()
        deadline.wait(0.2)
        assert len(loads) > 3, "with a link it polls again"
    finally:
        watcher.stop()

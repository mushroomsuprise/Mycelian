# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker statistics: record_game_event, lifetime save/load/reset, summary keys and insights event types."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from types import SimpleNamespace

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.statistics_insights as si  # noqa: E402
import modules.statistics_manager as sm  # noqa: E402
from modules.game_tracker.models import GameEvent  # noqa: E402

SYS = "__system__"


def _shutdown(m):
    m.is_running = False
    try:
        m._close_statistics_db()
    except Exception:
        pass
    for t in (m._event_flush_thread, m._username_backfill_thread, m._periodic_thread):
        if isinstance(t, threading.Thread) and t is not threading.current_thread():
            t.join(2)
    with m._stats_db_pool_lock:
        for c in m._stats_db_pool:
            c.close()
        m._stats_db_pool.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "data_root"
    root.mkdir()
    monkeypatch.setattr(sm, "get_data_path", lambda rel: os.path.join(str(root), rel))
    monkeypatch.setattr(sm, "get_data", lambda path: None)
    monkeypatch.setattr(sm, "set_data", lambda path, value: True)
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda message, **kw: None)
    managers = []

    def make():
        m = sm.StatisticsManager()
        if m._username_backfill_thread is not None:
            m._username_backfill_thread.join(2)
        managers.append(m)
        return m

    yield SimpleNamespace(make=make)
    for m in managers:
        _shutdown(m)


@pytest.fixture
def mgr(env):
    return env.make()


def _evt(kind, platform="psn", grade="", title="Elden Ring"):
    return GameEvent(kind=kind, platform=platform, game_id="G", game_title=title, unlock_grade=grade)


def _events(m):
    m._flush_pending_events()
    conn = sqlite3.connect(m._stats_db_path)
    try:
        return conn.execute("SELECT username, event_type, amount, alert_name FROM user_events ORDER BY id").fetchall()
    finally:
        conn.close()


def test_unlocks_count_by_grade_and_steam_achievements(mgr):
    for grade in ("bronze", "bronze", "silver", "gold", "platinum"):
        mgr.record_game_event(_evt("unlock", grade=grade))
    mgr.record_game_event(_evt("unlock", platform="steam", grade="achievement", title="Hades"))
    gt = mgr.data.game_tracker
    assert (gt.trophies_bronze, gt.trophies_silver, gt.trophies_gold, gt.trophies_platinum) == (2, 1, 1, 1)
    assert gt.steam_achievements == 1
    rows = _events(mgr)
    assert rows[0] == (SYS, "game_unlock", 1.0, "Elden Ring")
    assert rows[-1] == (SYS, "game_unlock", 1.0, "Hades")


def test_completed_started_milestone(mgr):
    mgr.record_game_event(_evt("completed"))
    mgr.record_game_event(_evt("completed", platform="steam"))
    mgr.record_game_event(_evt("started"))
    mgr.record_game_event(_evt("milestone"))
    mgr.record_game_event(_evt("milestone"))
    gt = mgr.data.game_tracker
    assert (gt.platinums, gt.perfect_games, gt.completed_games) == (1, 1, 2)
    assert (gt.games_started, gt.milestones) == (1, 2)
    kinds = [r[1] for r in _events(mgr)]
    assert kinds == ["game_completed", "game_completed", "game_started", "game_milestone", "game_milestone"]


def test_unknown_kind_is_ignored(mgr):
    mgr.record_game_event(_evt("mystery"))
    mgr.record_game_event(None)
    assert mgr.data.game_tracker == sm.GameTrackerStatistics()
    assert _events(mgr) == []


def test_summary_and_reset(mgr):
    mgr.record_game_event(_evt("unlock", grade="gold"))
    mgr.record_game_event(_evt("unlock", grade="bronze"))
    summary = mgr.get_all_statistics()["game_tracker"]
    assert summary["trophies_gold"] == 1 and summary["trophies"] == 2
    assert set(sm._GAME_TRACKER_FIELDS) <= set(summary)
    mgr.reset_statistics("game_tracker")
    assert mgr.data.game_tracker == sm.GameTrackerStatistics()


def test_save_and_load_roundtrip(env, mgr):
    mgr.record_game_event(_evt("unlock", grade="silver"))
    mgr.record_game_event(_evt("completed", platform="steam"))
    mgr._save_to_database()
    conn = sqlite3.connect(mgr._stats_db_path)
    try:
        row = conn.execute("SELECT data_json FROM lifetime_totals WHERE category = 'game_tracker'").fetchone()
    finally:
        conn.close()
    saved = json.loads(row[0])
    assert saved["trophies_silver"] == 1 and saved["perfect_games"] == 1
    _shutdown(mgr)
    again = env.make()
    gt = again.data.game_tracker
    assert (gt.trophies_silver, gt.perfect_games, gt.completed_games) == (1, 1, 1)


def test_load_tolerates_bad_values(env, mgr):
    conn = sqlite3.connect(mgr._stats_db_path)
    conn.execute(
        "INSERT OR REPLACE INTO lifetime_totals (category, data_json) VALUES (?, ?)",
        ("game_tracker", json.dumps({"trophies_gold": "3", "milestones": "lots", "platinums": None})),
    )
    conn.commit()
    conn.close()
    _shutdown(mgr)
    gt = env.make().data.game_tracker
    assert (gt.trophies_gold, gt.milestones, gt.platinums) == (3, 0, 0)


def test_insights_know_the_game_event_types():
    for key in ("game_unlock", "game_completed", "game_started", "game_milestone"):
        assert key in si.EVENT_TYPES and si.EVENT_TYPES[key].label and si.EVENT_TYPES[key].icon


def test_dispatch_reaches_the_recorder(monkeypatch):
    from modules.game_tracker import dispatch as gd

    seen = []
    monkeypatch.setattr(sm, "get_statistics_manager", lambda: SimpleNamespace(record_game_event=seen.append))
    gd._to_statistics(_evt("started"))
    assert seen and seen[0].kind == "started"

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Changes fan out to every sink; settings switch sinks off; one failing sink doesn't stop the rest."""

import pytest

from modules.game_tracker import dispatch as dispatch_mod
from modules.game_tracker.models import PSN, GameEvent, TrackerSettings
from modules.game_tracker.store import GameTrackerStore


@pytest.fixture
def sinks(monkeypatch):
    seen = {"statistics": [], "chatbot": [], "connectors": [], "overlays": []}
    for name in seen:
        monkeypatch.setattr(dispatch_mod, f"_to_{name}", lambda e, n=name: seen[n].append(e.kind))
    return seen


def ev(kind="unlock"):
    return GameEvent(kind=kind, platform=PSN, game_id="NPWR00001_00", game_title="Game", unlock_grade="gold")


def test_every_sink_gets_every_event_and_history_is_saved(sinks, tmp_path):
    db = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    sent = dispatch_mod.dispatch([ev(), ev("completed"), ev("bogus")], TrackerSettings(), db)
    assert [e.kind for e in sent] == ["unlock", "completed"]
    assert all(v == ["unlock", "completed"] for v in sinks.values())
    assert [e.kind for e in db.recent_events(5, kinds=("unlock", "completed"))] == ["completed", "unlock"]


def test_settings_turn_sinks_off(sinks):
    dispatch_mod.dispatch([ev()], TrackerSettings(record_stats=False, send_events=False))
    assert sinks == {"statistics": [], "chatbot": [], "connectors": [], "overlays": ["unlock"]}


def test_failing_sink_does_not_stop_others(sinks, monkeypatch):
    def boom(e):
        raise RuntimeError("chatbot down")

    monkeypatch.setattr(dispatch_mod, "_to_chatbot", boom)
    dispatch_mod.dispatch([ev(), ev("started")], TrackerSettings())
    assert sinks["connectors"] == ["unlock", "started"] and sinks["statistics"] == ["unlock", "started"]


def test_nothing_to_send():
    assert dispatch_mod.dispatch([], TrackerSettings()) == []


def test_real_sinks_reach_their_targets(monkeypatch):
    from modules import connector_integration, statistics_manager

    recorded, emitted = [], []

    class Mgr:
        def record_game_event(self, e):
            recorded.append(e.kind)

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: Mgr())
    monkeypatch.setattr(connector_integration, "emit_event", lambda src, et, f: emitted.append((src, et, f["game_title"])))
    dispatch_mod._to_statistics(ev())
    dispatch_mod._to_connectors(ev("milestone"))
    assert recorded == ["unlock"]
    assert emitted == [("game_tracker", "game_milestone", "Game")]


def test_overlay_sink_emits_when_web_engine_is_up(monkeypatch):
    from modules import web_engine

    out = []

    class Engine:
        def safe_emit(self, name, payload):
            out.append((name, payload["kind"]))

    monkeypatch.setattr(web_engine, "web_engine_instance", Engine(), raising=False)
    dispatch_mod._to_overlays(ev())
    assert out == [("game_tracker_event", "unlock")]


def test_event_type_names():
    assert dispatch_mod.EVENT_TYPES == {
        "unlock": "game_unlock", "completed": "game_completed", "started": "game_started", "milestone": "game_milestone",
    }

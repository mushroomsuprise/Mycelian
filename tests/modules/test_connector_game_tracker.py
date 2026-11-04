# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker in connectors: triggers and their filters, the game lookup action,
``{games.*}`` placeholders, the generic connector event entry point and the overlay events."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import connector_actions as ca, connector_core as core  # noqa: E402
from modules import connector_integration as ci, connector_placeholders as cp, connector_variables as cv  # noqa: E402
from modules.connector_core import ActionType, TriggerType  # noqa: E402
from modules.connector_triggers import CONNECTOR_EVENT_TYPES, create_trigger  # noqa: E402
from modules.game_tracker import service as gt_service  # noqa: E402
from modules.game_tracker.models import TrackedGame  # noqa: E402
from modules.spore_studio.event_registry import get_event  # noqa: E402
from modules.uiwindows import connector_action_schemas as schemas  # noqa: E402
from modules.uiwindows.connector_editor import ACTION_OUTPUTS  # noqa: E402

GAME_TRIGGERS = ("game_unlock", "game_completed", "game_started", "game_milestone")


def run(action, event=None, index=1):
    """Run an action like the connector does; returns (ok, problem slot, action_results)."""
    slot: dict = {}
    results: dict = {}
    trigger_data = {"trigger_type": "game_unlock", "connector_name": "Test", "action_index": index,
                    "action_results": results}
    token = core._action_problem.set(slot)
    try:
        ok = asyncio.run(action.execute(trigger_data, dict(event or cv.sample_event("game_unlock"))))
    finally:
        core._action_problem.reset(token)
    return ok, slot, results


# --------------------------------------------------------------------------- triggers


@pytest.mark.parametrize("value", GAME_TRIGGERS)
def test_game_triggers_fire_on_their_own_event_only(value):
    trigger = create_trigger(TriggerType(value), "t1", "T")
    assert trigger.should_trigger(cv.sample_event(value))
    assert not trigger.should_trigger(cv.sample_event("twitch_raid"))
    other = next(v for v in GAME_TRIGGERS if v != value)
    assert not trigger.should_trigger(cv.sample_event(other))
    assert value in CONNECTOR_EVENT_TYPES
    info = cv.trigger_info(value)
    assert info["group"] == "Games" and info["label"]
    assert cv.variables_for(value) and cv.CONDITION_FIELDS[value]


@pytest.mark.parametrize("value", GAME_TRIGGERS)
def test_platform_filter(value):
    psn_only = create_trigger(TriggerType(value), "t", "T", platform="psn")
    steam_only = create_trigger(TriggerType(value), "t", "T", platform="steam")
    event = dict(cv.sample_event(value), platform="psn")
    assert psn_only.should_trigger(event) and not steam_only.should_trigger(event)
    assert create_trigger(TriggerType(value), "t", "T", platform="any").should_trigger(event)
    # The sample follows the trigger's filter, so a test run fires.
    assert steam_only.should_trigger(cv.sample_event(value, steam_only))


def test_unlock_grade_filter():
    gold = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", grade="gold")
    platinum = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", grade="Platinum")
    event = dict(cv.sample_event("game_unlock"), unlock_grade="gold")
    assert gold.should_trigger(event) and not platinum.should_trigger(event)
    assert platinum.should_trigger(cv.sample_event("game_unlock", platinum))
    steam = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", platform="steam")
    assert cv.sample_event("game_unlock", steam)["unlock_grade"] == "achievement"


def test_milestone_filter():
    fifty = create_trigger(TriggerType.GAME_MILESTONE, "t", "T", milestone="50")
    assert fifty.milestone == 50
    assert fifty.should_trigger(dict(cv.sample_event("game_milestone"), milestone=50))
    assert not fifty.should_trigger(dict(cv.sample_event("game_milestone"), milestone=75))
    assert not fifty.should_trigger(dict(cv.sample_event("game_milestone"), milestone="?"))
    anyone = create_trigger(TriggerType.GAME_MILESTONE, "t", "T", milestone="junk")
    assert anyone.milestone == 0 and anyone.should_trigger(dict(cv.sample_event("game_milestone"), milestone=75))
    seventy_five = create_trigger(TriggerType.GAME_MILESTONE, "t", "T", milestone=75)
    assert seventy_five.should_trigger(cv.sample_event("game_milestone", seventy_five))


def test_conditions_still_apply():
    from modules.connector_core import ComparisonOperator, TriggerCondition

    cond = TriggerCondition(field="progress", operator=ComparisonOperator.GREATER_THAN, value=90)
    trigger = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", conditions=[cond])
    assert not trigger.should_trigger(cv.sample_event("game_unlock"))
    assert trigger.should_trigger(dict(cv.sample_event("game_unlock"), progress=95))


def test_filters_survive_save_and_load():
    from modules.connector_manager import ConnectorManager

    manager = ConnectorManager.__new__(ConnectorManager)
    trigger = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", platform="psn", grade="gold")
    data = manager._serialize_trigger(trigger)
    assert data["platform"] == "psn" and data["grade"] == "gold"
    again = manager._deserialize_trigger(data)
    assert (again.platform, again.grade) == ("psn", "gold")


# --------------------------------------------------------------------------- game lookup action


def _game(platform, game_id, title, progress, last_played, **extra):
    return TrackedGame(platform=platform, game_id=game_id, title=title, progress=progress, last_played=last_played, **extra)


@pytest.fixture
def library(monkeypatch):
    games = [
        _game("psn", "NPWR22810_00", "ELDEN RING", 78, 300.0, earned={"bronze": 30, "gold": 3},
              defined={"bronze": 34, "silver": 4, "gold": 3, "platinum": 1}, extra={"presence_name": "Elden Ring"}),
        _game("steam", "1145360", "Hades", 100, 200.0, unlocked_ids=["a", "b"], total_achievements=2),
        _game("psn", "NPWR39127_00", "Astro Bot", 100, 900.0, hidden=True,
              earned={"platinum": 1}, defined={"platinum": 1}),
    ]
    store = SimpleNamespace(list_games=lambda: list(games))
    fake = SimpleNamespace(store=store, summary=lambda: {"games": 2, "current": {"title": "Elden Ring"}})
    monkeypatch.setattr(gt_service, "get_service", lambda: fake)
    return games


def test_find_tracked_game(library):
    find = ca.find_tracked_game
    assert find("").game_id == "NPWR22810_00"  # most recent visible game (Astro Bot is hidden)
    assert find("elden ring").game_id == "NPWR22810_00"  # display title, any case
    assert find("ELDEN RING").game_id == "NPWR22810_00"
    assert find("had").game_id == "1145360"  # partial match
    assert find("steam:1145360").title == "Hades"
    assert find("psn:npwr39127_00").title == "Astro Bot"  # an exact ID finds hidden games too
    assert find("hades", platform="psn") is None
    assert find("", platform="steam").game_id == "1145360"
    assert find("nothing like it") is None


def test_game_lookup_outputs(library):
    action = ca.create_action(ActionType.GAME_LOOKUP, "a", "Lookup", game="{game_title}")
    ok, slot, results = run(action, index=2)
    assert ok and slot == {}
    out = results["2"]
    assert out["title"] == "Elden Ring" and out["platform"] == "PlayStation"
    assert (out["progress"], out["earned"], out["total"], out["completed"]) == (78, 33, 42, "no")
    assert out["last_played"]
    ok, _slot, results = run(ca.create_action(ActionType.GAME_LOOKUP, "a", "x", game="Hades"))
    assert ok and results["1"]["completed"] == "yes" and results["1"]["platform"] == "Steam"


def test_game_lookup_failures(library, monkeypatch):
    ok, slot, results = run(ca.create_action(ActionType.GAME_LOOKUP, "a", "x", game="Missing Game"))
    assert not ok and "no match" in slot["reason"] and "Missing Game" in slot["reason"] and results == {}

    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(gt_service, "get_service", boom)
    ok, slot, _ = run(ca.create_action(ActionType.GAME_LOOKUP, "a", "x"))
    assert not ok and "failed" in slot["reason"] and "db gone" in slot["details"]


def test_game_lookup_spec_and_outputs():
    spec = schemas.spec_for("game_lookup")
    assert spec is not None and spec.group == "Games"
    assert {f.key for f in spec.fields} == {"game", "platform"}
    assert schemas.summarize("game_lookup", {"game": "Hades"}) == "Progress of Hades"
    assert schemas.summarize("game_lookup", {}) == "Progress of the latest game"
    assert {k for k, _ in ACTION_OUTPUTS["game_lookup"]} == {
        "title", "platform", "progress", "earned", "total", "completed", "last_played",
        "goals", "goals_done", "goals_total", "goal_next", "goal_next_progress", "goal_1_name", "goal_1_progress"}


# --------------------------------------------------------------------------- placeholders


def test_placeholder_context_has_games(library):
    ctx = cp.build_connector_placeholder_context({"event_type": "game_unlock"}, {})
    assert ctx["games"]["current"]["title"] == "Elden Ring"
    assert cp.substitute_connector_placeholders("{games.current.title} · {games.games}", ctx) == "Elden Ring · 2"


def test_placeholder_context_survives_tracker_errors(monkeypatch):
    def boom():
        raise RuntimeError("no tracker")

    monkeypatch.setattr(gt_service, "get_service", boom)
    ctx = cp.build_connector_placeholder_context({"event_type": "timer", "games": "mine"}, {})
    assert ctx["games"] == "mine" and ctx["event_type"] == "timer"


def test_preview_context_has_sample_games():
    ctx = cv.example_context("game_unlock")
    assert cp.substitute_connector_placeholders("{games.current.title} {games.platinums}", ctx) == "Elden Ring 23"
    assert {"Games Tracker"} <= {v["group"] for v in cv.ALWAYS_VARIABLES}


# --------------------------------------------------------------------------- entry points


class FakeManager:
    def __init__(self, running=True, scheduled=True):
        self.is_running = running
        self.scheduled = scheduled
        self.events = []

    def _schedule_event_on_connector_loop(self, event_data):
        self.events.append(event_data)
        return self.scheduled


def test_emit_event_sets_source_and_schedules(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(ci, "get_manager", lambda: manager)
    ci.emit_event("game_tracker", "game_unlock", {"game_title": "Hades", "source": "ignored", "event_type": "x"})
    (event,) = manager.events
    assert event["event_type"] == "game_unlock" and event["game_title"] == "Hades"
    assert event["source"] == "game_tracker" and event["timestamp"] > 0
    ci.emit_twitch_event("twitch_ad_break_begin", {"duration_seconds": 90})
    assert manager.events[-1]["source"] == "twitch"
    manager.scheduled = False
    ci.emit_event("game_tracker", "game_started", None)  # loop not ready: dropped quietly
    stopped = FakeManager(running=False)
    monkeypatch.setattr(ci, "get_manager", lambda: stopped)
    ci.emit_event("game_tracker", "game_started", {})
    assert stopped.events == []
    monkeypatch.setattr(ci, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError()))
    ci.emit_event("game_tracker", "game_started", {})  # never raises


def test_emitted_fields_fire_the_trigger(monkeypatch):
    from modules.game_tracker.models import GameEvent

    manager = FakeManager()
    monkeypatch.setattr(ci, "get_manager", lambda: manager)
    event = GameEvent(kind="unlock", platform="steam", game_id="1", game_title="Hades", unlock_grade="achievement")
    ci.emit_event("game_tracker", "game_unlock", event.fields_for_events())
    trigger = create_trigger(TriggerType.GAME_UNLOCK, "t", "T", platform="steam", grade="achievement")
    assert trigger.should_trigger(manager.events[0])


# --------------------------------------------------------------------------- overlay events


def test_overlay_events_registered():
    change = get_event("game_tracker_event")
    keys = {p["key"] for p in change["payload"]}
    assert {"kind", "platform", "game_title", "unlock_name", "unlock_grade", "milestone", "progress"} <= keys
    totals = get_event("game_tracker_update")
    assert {"games", "trophies", "platinums", "current.title"} <= {p["key"] for p in totals["payload"]}

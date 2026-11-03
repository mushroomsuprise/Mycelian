# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker chatbot events: event types, filters, persistence, catalog entries and {games.*} values."""

from __future__ import annotations

from types import SimpleNamespace

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.chatbot_core as cc  # noqa: E402
import modules.chatbot_variables as cv  # noqa: E402
from modules.chatbot_core import ChatEvent, EventType  # noqa: E402
from modules.game_tracker.models import GameEvent  # noqa: E402

GAME_TYPES = ("game_unlock", "game_completed", "game_started", "game_milestone")


def _data(platform="psn", grade="gold", milestone=""):
    return GameEvent(kind="unlock", platform=platform, game_id="G", game_title="Elden Ring", unlock_grade=grade,
                     milestone=milestone or None).fields_for_events()


def test_event_type_values():
    for value in GAME_TYPES:
        assert EventType(value).value == value


def test_defaults_match_everything():
    for value in GAME_TYPES:
        ev = ChatEvent(event_type=value)
        assert (ev.game_platform, ev.game_unlock_grade, ev.game_milestone) == ("any", "any", 0)
        assert ev.should_trigger(_data())


@pytest.mark.parametrize("event_type", GAME_TYPES)
def test_platform_filter(event_type):
    ev = ChatEvent(event_type=event_type, game_platform="steam")
    assert ev.should_trigger(_data(platform="steam", grade="achievement", milestone=50))
    assert not ev.should_trigger(_data(platform="psn", milestone=50))


def test_grade_filter_only_for_unlocks():
    ev = ChatEvent(event_type="game_unlock", game_unlock_grade="platinum")
    assert ev.should_trigger(_data(grade="platinum"))
    assert not ev.should_trigger(_data(grade="gold"))
    other = ChatEvent(event_type="game_completed", game_unlock_grade="platinum")
    assert other.should_trigger(_data(grade=""))


def test_milestone_filter():
    ev = ChatEvent(event_type="game_milestone", game_milestone=75)
    assert ev.should_trigger(_data(milestone=75))
    assert not ev.should_trigger(_data(milestone=50))
    assert not ev.should_trigger(_data())
    assert ChatEvent(event_type="game_milestone").should_trigger(_data(milestone=25))


def test_bad_filter_values_fall_back_to_any():
    ev = ChatEvent(event_type="game_unlock", game_platform="xbox", game_unlock_grade="diamond", game_milestone="x")
    assert (ev.game_platform, ev.game_unlock_grade, ev.game_milestone) == ("any", "any", 0)


def test_filters_persist():
    ev = ChatEvent(event_type="game_milestone", game_platform="psn", game_unlock_grade="gold", game_milestone=50)
    again = ChatEvent.from_dict(ev.to_dict())
    assert again.event_type == EventType.GAME_MILESTONE
    assert (again.game_platform, again.game_unlock_grade, again.game_milestone) == ("psn", "gold", 50)


def test_disabled_event_never_triggers():
    assert not ChatEvent(event_type="game_unlock", enabled=False).should_trigger(_data())


# --------------------------------------------------------------------------- {games.*}


SUMMARY = {
    "games": 650, "psn_games": 412, "steam_games": 238, "platinums": 23, "perfect": 11, "completed": 34,
    "trophies": 4812, "trophies_by_grade": {"bronze": 3537, "silver": 940, "gold": 312, "platinum": 23},
    "achievements": 2904, "avg_completion": 46,
    "current": {"title": "Elden Ring", "platform_label": "PS5", "progress": 78, "earned": 33, "total": 42},
}


@pytest.fixture
def fake_service(monkeypatch):
    import modules.game_tracker.service as svc

    state = {"summary": dict(SUMMARY)}
    monkeypatch.setattr(svc, "get_service", lambda: SimpleNamespace(summary=lambda: state["summary"]))
    return state


@pytest.mark.parametrize("key,expected", [
    ("total", "650"), ("completed", "34"), ("avg_completion", "46"), ("psn.games", "412"), ("psn.platinums", "23"),
    ("psn.trophies", "4812"), ("psn.gold", "312"), ("steam.games", "238"), ("steam.perfect", "11"),
    ("steam.achievements", "2904"), ("current.title", "Elden Ring"), ("current.platform", "PS5"),
    ("current.progress", "78"), ("current.earned", "33"), ("current.total", "42"),
])
def test_games_values(fake_service, key, expected):
    assert cc.get_games_value(key) == expected
    assert cc.lookup_template_value(f"games.{key}", {}) == (True, expected)


def test_games_template_render(fake_service):
    assert cc.render_template("{games.psn.platinums} plats, playing {games.current.title}", {}) == "23 plats, playing Elden Ring"


def test_games_unknown_key_is_left_alone(fake_service):
    assert cc.get_games_value("nope") is None
    assert cc.lookup_template_value("games.nope", {}) == (False, None)


def test_games_no_current_game(fake_service):
    fake_service["summary"] = {**SUMMARY, "current": None}
    assert cc.get_games_value("current.title") == ""


def test_games_service_failure_never_raises(monkeypatch):
    import modules.game_tracker.service as svc

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(svc, "get_service", boom)
    assert cc.get_games_value("total") == ""


# --------------------------------------------------------------------------- catalog


def test_catalog_has_games_events_with_variables_and_filters():
    for value in GAME_TYPES:
        entry = cv.EVENT_INDEX[value]
        assert entry["group"] == "Games"
        assert any(f["key"] == "game_platform" for f in entry["filters"])
        keys = {v["key"] for v in cv.event_variables(value)}
        assert {"game_title", "platform_name", "progress", "earned", "total"} <= keys
    assert {"unlock_name", "unlock_grade", "unlock_rarity"} <= {v["key"] for v in cv.event_variables("game_unlock")}
    assert "milestone" in {v["key"] for v in cv.event_variables("game_milestone")}
    assert set(GAME_TYPES) <= set(cv.preview_event_types())


def test_games_variables_in_common_list():
    keys = {v["key"] for v in cv.COMMON_VARIABLES}
    assert {"games.total", "games.psn.platinums", "games.current.title"} <= keys


@pytest.mark.parametrize("item,check", [
    ({}, lambda d: d["platform"] == "psn" and d["unlock_grade"] == "gold"),
    ({"game_platform": "steam"}, lambda d: d["platform"] == "steam" and d["unlock_grade"] == "achievement"),
    ({"game_unlock_grade": "achievement"}, lambda d: d["platform"] == "steam"),
    ({"game_unlock_grade": "platinum"}, lambda d: d["unlock_grade"] == "platinum"),
])
def test_unlock_sample_passes_its_filters(item, check):
    data = cv.sample_data("event", "game_unlock", item)
    assert check(data)
    assert ChatEvent(event_type="game_unlock", **item).should_trigger(data)


def test_milestone_sample_uses_the_filter():
    data = cv.sample_data("event", "game_milestone", {"game_milestone": 75})
    assert data["milestone"] == 75
    assert ChatEvent(event_type="game_milestone", game_milestone=75).should_trigger(data)


def test_preview_renders_game_variables(fake_service):
    text = cv.render_preview("{game_title} {unlock_name} ({unlock_grade}) {progress}%", "event", "game_unlock", {})
    assert text == "Elden Ring Legendary Armaments (gold) 78%"


def test_dispatch_to_chatbot_uses_the_event_types(monkeypatch):
    from modules import chatbot, chatbot_manager
    from modules.game_tracker import dispatch as gd

    seen = []
    monkeypatch.setattr(chatbot_manager, "get_manager",
                        lambda: SimpleNamespace(process_event=lambda et, data: seen.append((et, data)) or "result"))
    monkeypatch.setattr(chatbot, "dispatch_process_event_result", lambda result, default_targets=None: seen.append(result))
    for kind in ("unlock", "completed", "started", "milestone"):
        gd._to_chatbot(GameEvent(kind=kind, platform="psn", game_id="G", game_title="T"))
    assert [s[0] for s in seen if isinstance(s, tuple)] == [EventType(v) for v in GAME_TYPES]

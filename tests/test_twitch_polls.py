# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot polls run on Twitch: limits, start/stop, EventSub updates, the scope gate and the layout."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import twitch_polls, twitch_token_auth  # noqa: E402
from modules import poll_manager as pm  # noqa: E402
from modules.poll_manager import ONE_TWITCH_POLL_MESSAGE, PollManager  # noqa: E402


@pytest.fixture
def twitch(monkeypatch):
    """Fake Twitch side: scope ok, create returns ids in order, end calls are recorded."""
    calls = {"create": [], "end": []}

    def create(title, choices, duration, *, points_enabled=False, points_per_vote=0):
        calls["create"].append({"title": title, "choices": list(choices), "duration": duration,
                                "points": (points_enabled, points_per_vote)})
        return True, {"id": f"poll-{len(calls['create'])}", "choices": [{"id": f"c{i}", "title": c} for i, c in enumerate(choices)]}

    monkeypatch.setattr(twitch_polls, "scope_status", lambda: "ok")
    monkeypatch.setattr(twitch_polls, "create_poll", create)
    monkeypatch.setattr(twitch_polls, "end_poll", lambda poll_id, **kw: calls["end"].append(poll_id))
    return calls


def _manager(template="poll", names=("Yes", "No"), **config) -> PollManager:
    manager = PollManager(persist=False)
    manager.ensure_template(template)
    for name in names:
        manager.add_category(template, name)
    settings = {"twitch_poll": True, "twitch_title": "Best game?", "duration_seconds": 60}
    settings.update(config)
    manager.update_config(template, settings)
    return manager


def _votes(manager, template="poll"):
    return {row["name"]: row["votes"] for row in manager.get_overlay_state(template)["options"]}


# --------------------------------------------------------------------------- config + limits


def test_twitch_settings_normalise_to_twitch_limits():
    manager = PollManager(persist=False)
    manager.ensure_template("p")
    config = manager.get_config("p")
    assert (config["twitch_poll"], config["twitch_title"], config["twitch_points_enabled"], config["twitch_points_per_vote"]) == (
        False, "", False, 100)
    manager.update_config("p", {"twitch_poll": True})
    assert manager.get_config("p")["duration_seconds"] == 180  # "until I stop it" isn't possible on Twitch
    manager.update_config("p", {"duration_seconds": 3600, "twitch_title": "x" * 80, "twitch_points_per_vote": 5_000_000})
    config = manager.get_config("p")
    assert config["duration_seconds"] == 1800 and len(config["twitch_title"]) == 60 and config["twitch_points_per_vote"] == 1_000_000
    manager.update_config("p", {"duration_seconds": 5})
    assert manager.get_config("p")["duration_seconds"] == 15
    manager.update_config("p", {"twitch_poll": False, "duration_seconds": 0})
    assert manager.get_config("p")["duration_seconds"] == 0


def test_config_problems():
    good = {"twitch_title": "Q", "categories": [{"name": "A"}, {"name": "B"}], "duration_seconds": 60}
    assert twitch_polls.config_problems(good) == []
    assert "question" in twitch_polls.config_problems({**good, "twitch_title": " "})[0]
    assert "2 to 5" in twitch_polls.config_problems({**good, "categories": [{"name": "A"}]})[0]
    assert "2 to 5" in twitch_polls.config_problems({**good, "categories": [{"name": str(i)} for i in range(6)]})[0]
    assert "25 characters" in twitch_polls.config_problems({**good, "categories": [{"name": "A"}, {"name": "B" * 26}]})[0]
    assert "15 seconds" in twitch_polls.config_problems({**good, "duration_seconds": 0})[0]


# --------------------------------------------------------------------------- start / votes / end


def test_start_creates_the_twitch_poll_and_ignores_chat_votes(twitch):
    manager = _manager(twitch_points_enabled=True, twitch_points_per_vote=250)
    ok, message = manager.start_poll("poll")
    assert ok and "Twitch" in message
    assert twitch["create"] == [{"title": "Best game?", "choices": ["Yes", "No"], "duration": 60, "points": (True, 250)}]
    snapshot = manager.get_overlay_state("poll")
    assert snapshot["status"] == "open" and snapshot["twitch_poll"] is True
    # Chat keywords don't count while the poll runs on Twitch.
    assert manager.try_register_vote({"message": "#Yes", "username": "viewer"}) is False
    assert manager.apply_twitch_votes("poll-1", {"c0": 4, "c1": 9}) is True
    assert _votes(manager) == {"No": 9, "Yes": 4}
    assert manager.apply_twitch_votes("other-poll", {"c0": 100}) is False
    manager.shutdown()


def test_twitch_end_event_finishes_the_poll_without_ending_it_again(twitch):
    manager = _manager()
    manager.start_poll("poll")
    assert manager.twitch_poll_ended("poll-1", {"c0": 12, "c1": 3}, "completed") is True
    snapshot = manager.get_overlay_state("poll")
    assert snapshot["status"] == "ended" and [w["name"] for w in snapshot["winners"]] == ["Yes"]
    assert twitch["end"] == []


def test_ending_early_ends_it_on_twitch_and_keeps_its_final_counts(twitch):
    manager = _manager()
    manager.start_poll("poll")
    manager.apply_twitch_votes("poll-1", {"c0": 2, "c1": 1})
    ok, _ = manager.stop_poll("poll")
    assert ok and twitch["end"] == ["poll-1"]
    # Twitch's end event arrives after; its counts win.
    manager.twitch_poll_ended("poll-1", {"c0": 2, "c1": 5}, "terminated")
    snapshot = manager.get_overlay_state("poll")
    assert _votes(manager) == {"No": 5, "Yes": 2} and [w["name"] for w in snapshot["winners"]] == ["No"]


def test_local_timer_never_ends_the_twitch_side(twitch):
    manager = _manager()
    manager.start_poll("poll")
    generation = manager._generations["poll"]
    manager._timer_fired("poll", generation)
    assert manager.get_overlay_state("poll")["status"] == "ended" and twitch["end"] == []


def test_begin_event_lines_up_the_end_time(twitch):
    manager = _manager()
    manager.start_poll("poll")
    ends = time.time() + 42
    assert manager.twitch_poll_began("poll-1", ends) is True
    assert abs(manager.get_overlay_state("poll")["ends_at"] - ends) < 0.01
    manager.shutdown()


def test_only_one_twitch_poll_at_a_time(twitch):
    manager = _manager()
    manager.ensure_template("second")
    manager.add_category("second", "A")
    manager.add_category("second", "B")
    manager.update_config("second", {"twitch_poll": True, "twitch_title": "Other?", "duration_seconds": 30})
    assert manager.start_poll("poll")[0]
    ok, message = manager.start_poll("second")
    assert not ok and message == ONE_TWITCH_POLL_MESSAGE.format(template="poll")
    assert len(twitch["create"]) == 1 and manager.twitch_poll_running() == "poll"
    # A chat-only poll can still run beside it.
    manager.update_config("second", {"twitch_poll": False})
    assert manager.start_poll("second")[0]
    manager.shutdown()


def test_start_refused_without_scope_or_when_twitch_fails(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(twitch_polls, "scope_status", lambda: "missing")
    ok, message = manager.start_poll("poll")
    assert not ok and "sign in to twitch again" in message.lower() and manager.get_overlay_state("poll")["status"] == "idle"
    monkeypatch.setattr(twitch_polls, "scope_status", lambda: "ok")
    monkeypatch.setattr(twitch_polls, "create_poll", lambda *a, **k: (False, "Twitch only lets partners and affiliates run polls."))
    ok, message = manager.start_poll("poll")
    assert not ok and "partners" in message and manager.get_overlay_state("poll")["status"] == "idle"


def test_invalid_twitch_setup_blocks_start(twitch):
    manager = _manager(names=("A",))
    ok, message = manager.start_poll("poll")
    assert not ok and twitch["create"] == []


def test_chat_command_start_runs_in_the_background_and_reports_problems(monkeypatch):
    manager = _manager(mods_can_start=True, start_command="!poll")
    monkeypatch.setattr(twitch_polls, "scope_status", lambda: "missing")
    problems = []
    monkeypatch.setattr(pm, "_notify_poll_problem", lambda title, *a, **k: problems.append(title))
    assert manager.try_moderator_start({"message": "!poll", "is_broadcaster": True, "username": "me"}) is True
    for _ in range(100):
        if problems:
            break
        time.sleep(0.02)
    assert problems == ["Couldn't start the poll from chat"]
    assert manager.get_overlay_state("poll")["status"] == "idle"


def test_twitch_ids_survive_a_restart(twitch):
    manager = _manager()
    manager.start_poll("poll")
    with manager._lock:
        _seq, payload = manager._build_payload_locked()
    restored = PollManager(persist=False)
    with restored._lock:
        templates, _timers = restored._parse_stored(payload, time.time(), schedule=False)
    runtime = templates["poll"]["runtime"]
    assert runtime["twitch_poll_id"] == "poll-1" and set(runtime["twitch_choices"]) == {"c0", "c1"}
    manager.shutdown()


# --------------------------------------------------------------------------- Twitch side


def test_event_votes_and_timestamps():
    event = SimpleNamespace(id="p", choices=[SimpleNamespace(id="a", votes=3), {"id": "b", "votes": "7"}, {"id": "", "votes": 1}])
    assert twitch_polls.event_choice_votes(event) == {"a": 3, "b": 7}
    assert twitch_polls.parse_timestamp("2026-01-01T00:00:00Z") == pytest.approx(1767225600.0)
    assert twitch_polls.parse_timestamp(None) is None and twitch_polls.parse_timestamp("nope") is None
    assert twitch_polls.parse_timestamp("2021-03-19T06:08:33.871278372Z") == pytest.approx(1616134113.871278)


def test_create_poll_body_and_errors(monkeypatch):
    sent = {}

    class Api:
        async def generic_api_call(self, url, method, json_data=None, **kw):
            sent.update(url=url, method=method, body=json_data)
            return {"data": [{"id": "p1", "choices": [{"id": "c1"}, {"id": "c2"}]}]}

    monkeypatch.setattr(twitch_polls, "_broadcaster_and_api", lambda: (Api(), "123"))
    ok, poll = twitch_polls.create_poll("Q" * 70, ["A" * 30, "B"], 9999, points_enabled=True, points_per_vote=0)
    assert ok and poll["id"] == "p1"
    assert sent["method"] == "POST" and sent["url"] == twitch_polls.HELIX_POLLS
    body = sent["body"]
    assert len(body["title"]) == 60 and body["choices"][0]["title"] == "A" * 25 and body["duration"] == 1800
    assert body["channel_points_voting_enabled"] is True and body["channel_points_per_vote"] == 1 and body["broadcaster_id"] == "123"

    class Broken:
        async def generic_api_call(self, *a, **k):
            raise RuntimeError("API Error 403: The broadcaster must be a partner or affiliate")

    monkeypatch.setattr(twitch_polls, "_broadcaster_and_api", lambda: (Broken(), "123"))
    ok, message = twitch_polls.create_poll("Q", ["A", "B"], 60)
    assert not ok and "partners and affiliates" in message


def test_scope_status(monkeypatch):
    from modules import twitch

    api = SimpleNamespace(is_connected=True, auth_token="tok-1")
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: api)
    twitch_token_auth._token_scopes.clear()
    assert twitch_polls.scope_status() == "unknown"
    twitch_token_auth.remember_token_scopes("tok-1", ["chat:read"])
    assert twitch_polls.scope_status() == "missing"
    twitch_token_auth.remember_token_scopes("tok-1", ["chat:read", "channel:manage:polls"])
    assert twitch_polls.scope_status() == "ok"
    api.is_connected = False
    assert twitch_polls.scope_status() == "offline"


def test_optional_poll_scope_does_not_block_sign_in(monkeypatch):
    from twitchAPI.type import AuthScope

    seen = {}

    class FakeTwitch:
        async def set_user_authentication(self, token, scopes, refresh):
            seen["scopes"] = list(scopes)

    scopes = [AuthScope.CHAT_READ, AuthScope.CHANNEL_MANAGE_POLLS]
    twitch_token_auth._token_scopes.clear()
    twitch_token_auth.remember_token_scopes("old", ["chat:read"])
    asyncio.run(twitch_token_auth.apply_user_authentication(FakeTwitch(), "old", "r", scopes))
    assert seen["scopes"] == [AuthScope.CHAT_READ]
    twitch_token_auth.remember_token_scopes("new", ["chat:read", "channel:manage:polls"])
    asyncio.run(twitch_token_auth.apply_user_authentication(FakeTwitch(), "new", "r", scopes))
    assert seen["scopes"] == scopes


# --------------------------------------------------------------------------- layout


def test_overlay_cards_stay_in_the_main_column():
    polls = (ROOT / "modules" / "uiwindows" / "chatbot_polls.py").read_text(encoding="utf-8")
    render = polls[polls.index("    def _render(self)"):polls.index("    def _render_empty")]
    assert render.count("self._overlay_card(") == 1
    assert render.index("self._overlay_card(") < render.index("self._twitch_card(")
    giveaways = (ROOT / "modules" / "uiwindows" / "chatbot_giveaways.py").read_text(encoding="utf-8")
    block = giveaways[giveaways.index("    def _render_all"):giveaways.index("    def _tick")]
    assert block.index("self._entrants_card()") < block.index("self._overlay_card()") < block.index("self._setup_card(cfg)")


# Twitch's own Create Poll example (request + response), from the API reference.
_EXAMPLE_RESPONSE = {
    "data": [{
        "id": "ed961efd-8a3f-4cf5-a9d0-e616c590cd2a", "broadcaster_id": "141981764", "broadcaster_name": "TwitchDev",
        "broadcaster_login": "twitchdev", "title": "Heads or Tails?",
        "choices": [
            {"id": "4c123012-1351-4f33-84b7-43856e7a0f47", "title": "Heads", "votes": 0, "channel_points_votes": 0, "bits_votes": 0},
            {"id": "279087e3-54a7-467e-bcd0-c1393fcea4f0", "title": "Tails", "votes": 0, "channel_points_votes": 0, "bits_votes": 0},
        ],
        "bits_voting_enabled": False, "bits_per_vote": 0, "channel_points_voting_enabled": True, "channel_points_per_vote": 100,
        "status": "ACTIVE", "duration": 1800, "started_at": "2021-03-19T06:08:33.871278372Z",
    }]
}


def test_twitch_create_poll_example(monkeypatch):
    sent = {}

    class Api:
        async def generic_api_call(self, url, method, json_data=None, **kw):
            sent.update(body=json_data)
            return _EXAMPLE_RESPONSE

    monkeypatch.setattr(twitch_polls, "_broadcaster_and_api", lambda: (Api(), "141981764"))
    ok, poll = twitch_polls.create_poll("Heads or Tails?", ["Heads", "Tails"], 1800, points_enabled=True, points_per_vote=100)
    assert ok
    assert sent["body"] == {"broadcaster_id": "141981764", "title": "Heads or Tails?", "choices": [{"title": "Heads"}, {"title": "Tails"}],
                            "channel_points_voting_enabled": True, "channel_points_per_vote": 100, "duration": 1800}
    categories = [{"id": "tails-id", "name": "Tails"}, {"id": "heads-id", "name": "Heads"}]
    # Matched by title, whatever the order.
    assert twitch_polls.choice_map(poll, categories) == {
        "4c123012-1351-4f33-84b7-43856e7a0f47": "heads-id", "279087e3-54a7-467e-bcd0-c1393fcea4f0": "tails-id"}


def test_start_uses_twitchs_start_time(monkeypatch):
    started = time.time() - 4
    poll = {"id": "p9", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started)) + ".5Z",
            "choices": [{"id": "a", "title": "Yes"}, {"id": "b", "title": "No"}]}
    monkeypatch.setattr(twitch_polls, "scope_status", lambda: "ok")
    monkeypatch.setattr(twitch_polls, "create_poll", lambda *a, **k: (True, poll))
    manager = _manager()
    assert manager.start_poll("poll")[0]
    runtime = manager._templates["poll"]["runtime"]
    assert abs(runtime["ends_at"] - (int(started) + 0.5 + 60)) < 0.01
    assert manager.apply_twitch_votes("p9", {"a": 3, "b": 1}) and _votes(manager) == {"Yes": 3, "No": 1}
    manager.shutdown()

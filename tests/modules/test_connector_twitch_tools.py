# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch tools as connector actions, the newer Twitch triggers, and the direct event entry points."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import chatbot_lookups, connector_actions as ca, connector_core as core  # noqa: E402
from modules import connector_integration as ci, connector_variables as cv  # noqa: E402
from modules.connector_core import ActionType, TriggerType  # noqa: E402
from modules.connector_triggers import CONNECTOR_EVENT_TYPES, create_trigger  # noqa: E402
from modules.uiwindows import connector_action_schemas as schemas  # noqa: E402
from modules.uiwindows.connector_editor import ACTION_OUTPUTS  # noqa: E402

NEW_TRIGGERS = (
    "twitch_hype_train_progress", "twitch_poll_begin", "twitch_poll_end", "twitch_prediction_begin",
    "twitch_prediction_lock", "twitch_prediction_end", "twitch_ad_break_begin", "twitch_channel_update",
    "twitch_shoutout_received",
)


def run(action, event=None, index=1):
    """Run an action like the connector does; returns (ok, problem slot, action_results)."""
    slot: dict = {}
    results: dict = {}
    trigger_data = {"trigger_type": "twitch_raid", "connector_name": "Test", "action_index": index,
                    "action_results": results}
    token = core._action_problem.set(slot)
    try:
        ok = asyncio.run(action.execute(trigger_data, dict(event or {"username": "Raider", "event_type": "twitch_raid"})))
    finally:
        core._action_problem.reset(token)
    return ok, slot, results


@pytest.fixture
def lookups(monkeypatch):
    calls = []
    answers = {}

    async def fake(lookup_id, config=None, context=None, *, kind="command"):
        calls.append((lookup_id, config, context, kind))
        answer = answers.get(lookup_id, {"lookup_ok": True})
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    monkeypatch.setattr(chatbot_lookups, "run_lookup", fake)
    return SimpleNamespace(calls=calls, answers=answers)


# --------------------------------------------------------------------------- triggers


@pytest.mark.parametrize("value", NEW_TRIGGERS)
def test_new_triggers_fire_on_their_own_event_only(value):
    trigger = create_trigger(TriggerType(value), "t1", "T")
    assert trigger.should_trigger(cv.sample_event(value))
    assert not trigger.should_trigger(cv.sample_event("twitch_raid"))
    assert value in CONNECTOR_EVENT_TYPES
    info = cv.trigger_info(value)
    assert info["group"] == "Twitch" and info["label"]


def test_hype_triggers_explain_themselves():
    assert "every contribution" in cv.trigger_info("twitch_hype_train_progress")["warning"]
    assert "no longer" not in cv.trigger_info("twitch_hype_train_start")["warning"]


# --------------------------------------------------------------------------- lookup actions


def test_shoutout_renders_the_target_and_shares_outputs(lookups):
    lookups.answers["shoutout"] = {"lookup_ok": True, "shoutout_sent": "yes", "target_name": "Raider",
                                   "target_url": "https://twitch.tv/raider"}
    action = ca.create_action(ActionType.TWITCH_SHOUTOUT, "a", "SO")
    ok, slot, results = run(action, index=2)
    assert ok and slot == {}
    assert lookups.calls == [("shoutout", {"target": {"from": "text", "text": "Raider"}}, {}, "event")]
    assert results == {"2": {"shoutout_sent": "yes", "target_name": "Raider", "target_url": "https://twitch.tv/raider"}}


def test_shoutout_not_sent_is_a_failure(lookups):
    lookups.answers["shoutout"] = {"lookup_ok": True, "shoutout_sent": "no"}
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_SHOUTOUT, "a", "SO"))
    assert not ok and "didn't send it" in slot["reason"]


@pytest.mark.parametrize("action_type,kwargs,lookup_id,config", [
    (ActionType.TWITCH_RAID, {"target": "{username}"}, "raid", {"target": {"from": "text", "text": "Raider"}}),
    (ActionType.TWITCH_STREAM_MARKER, {"description": "{username} raided"}, "stream_marker", {"text": "Raider raided"}),
    (ActionType.TWITCH_STREAM_MARKER, {}, "stream_marker", {"text": ""}),
    (ActionType.TWITCH_SET_TITLE, {"title": "Thanks {username}!"}, "set_title", {"text": "Thanks Raider!"}),
    (ActionType.TWITCH_SET_GAME, {"game": "Celeste"}, "set_game", {"text": "Celeste"}),
    (ActionType.TWITCH_SNOOZE_AD, {}, "snooze_ad", {"text": ""}),
])
def test_lookup_actions_call_their_lookup(lookups, action_type, kwargs, lookup_id, config):
    ok, _slot, _ = run(ca.create_action(action_type, "a", "x", **kwargs))
    assert ok and lookups.calls[-1][:2] == (lookup_id, config)


def test_lookup_action_failures(lookups):
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_SET_TITLE, "a", "x", title="  "))
    assert not ok and "came out empty" in slot["reason"] and lookups.calls == []
    lookups.answers["raid"] = {"lookup_ok": False, "lookup_reason": "missing_scope", "lookup_error": "scope"}
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_RAID, "a", "x", target="pal"))
    assert not ok and "doesn't allow this" in slot["reason"] and slot["details"] == "scope"
    lookups.answers["set_game"] = {"lookup_ok": False, "lookup_reason": "weird"}
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_SET_GAME, "a", "x", game="x"))
    assert not ok and "Twitch refused it" in slot["reason"]
    lookups.answers["snooze_ad"] = RuntimeError("boom")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_SNOOZE_AD, "a", "x"))
    assert not ok and "RuntimeError" in slot["details"]


def test_outputs_are_kept_out_of_lookup_bookkeeping_and_bad_index_is_ignored():
    data = {"action_results": {}, "action_index": "x"}
    ca._store_outputs(data, {"a": 1})
    assert data["action_results"] == {}
    data["action_index"] = 3
    ca._store_outputs(data, {"a": 1, "lookup_ok": True})
    assert data["action_results"] == {"3": {"a": 1}}


# --------------------------------------------------------------------------- polls and predictions


class FakePolls:
    def __init__(self):
        self.calls = []
        self.start = (True, "")
        self.stop = (True, "")

    def start_poll(self, template):
        self.calls.append(("start", template))
        if isinstance(self.start, BaseException):
            raise self.start
        return self.start

    def stop_poll(self, template):
        self.calls.append(("stop", template))
        if isinstance(self.stop, BaseException):
            raise self.stop
        return self.stop


def test_poll_actions(monkeypatch):
    import modules.poll_manager as pm

    polls = FakePolls()
    monkeypatch.setattr(pm, "get_poll_manager", lambda: polls)
    ok, _, _ = run(ca.create_action(ActionType.TWITCH_START_POLL, "a", "x", template="poll"))
    assert ok and polls.calls == [("start", "poll")]
    polls.start = (False, "Add at least two options.")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_START_POLL, "a", "x", template="poll"))
    assert not ok and "Add at least two options." in slot["reason"]
    polls.start = RuntimeError("db")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_START_POLL, "a", "x", template="poll"))
    assert not ok and "db" in slot["reason"]
    ok, _, _ = run(ca.create_action(ActionType.TWITCH_END_POLL, "a", "x", template="poll"))
    assert ok
    polls.stop = (False, "Poll is not running.")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_END_POLL, "a", "x", template="poll"))
    assert not ok and slot.get("skipped") is True
    polls.stop = (False, "Twitch error")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_END_POLL, "a", "x", template="poll"))
    assert not ok and "Twitch error" in slot["reason"]
    polls.stop = RuntimeError("db")
    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_END_POLL, "a", "x", template="poll"))
    assert not ok


class FakePredictions:
    def __init__(self):
        self.calls = []
        self.result = (True, "")

    def _answer(self, *call):
        self.calls.append(call)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def start_prediction(self, template):
        return self._answer("start", template)

    def lock_prediction(self, template=None):
        return self._answer("lock")

    def resolve_prediction(self, outcome, template=None):
        return self._answer("resolve", outcome)

    def cancel_prediction(self, template=None):
        return self._answer("cancel")


def test_prediction_actions(monkeypatch):
    import modules.prediction_manager as prm

    fake = FakePredictions()
    monkeypatch.setattr(prm, "get_prediction_manager", lambda: fake)
    assert run(ca.create_action(ActionType.TWITCH_START_PREDICTION, "a", "x", template="predictions"))[0]
    assert run(ca.create_action(ActionType.TWITCH_LOCK_PREDICTION, "a", "x"))[0]
    assert run(ca.create_action(ActionType.TWITCH_RESOLVE_PREDICTION, "a", "x", outcome="{username}"))[0]
    assert run(ca.create_action(ActionType.TWITCH_CANCEL_PREDICTION, "a", "x"))[0]
    assert fake.calls == [("start", "predictions"), ("lock",), ("resolve", "Raider"), ("cancel",)]

    ok, slot, _ = run(ca.create_action(ActionType.TWITCH_RESOLVE_PREDICTION, "a", "x", outcome=" "))
    assert not ok and "no outcome" in slot["reason"]
    fake.result = (False, "No prediction is running.")
    for action_type in (ActionType.TWITCH_START_PREDICTION, ActionType.TWITCH_LOCK_PREDICTION,
                        ActionType.TWITCH_RESOLVE_PREDICTION, ActionType.TWITCH_CANCEL_PREDICTION):
        ok, slot, _ = run(ca.create_action(action_type, "a", "x"))
        assert not ok and "No prediction is running." in slot["reason"]
    fake.result = RuntimeError("down")
    for action_type in (ActionType.TWITCH_START_PREDICTION, ActionType.TWITCH_LOCK_PREDICTION,
                        ActionType.TWITCH_RESOLVE_PREDICTION, ActionType.TWITCH_CANCEL_PREDICTION):
        ok, slot, _ = run(ca.create_action(action_type, "a", "x"))
        assert not ok and "down" in slot["reason"]


# --------------------------------------------------------------------------- editor schemas


def test_twitch_group_specs_summaries_and_outputs(monkeypatch):
    twitch_specs = [s for s in schemas.ACTION_SPECS.values() if s.group == "Twitch"]
    assert {s.value for s in twitch_specs} == {t.value for t in ActionType if t.value.startswith("twitch_")}
    assert ("Twitch", "twitch") in schemas.ACTION_GROUPS
    render = lambda t: t.replace("{username}", "Raider")  # noqa: E731
    assert schemas.summarize("twitch_shoutout", {"target": "{username}"}, render) == "Shoutout to Raider"
    assert schemas.summarize("twitch_raid", {"target": "pal"}) == "Raid pal"
    assert schemas.summarize("twitch_stream_marker", {"description": "big"}) == "Marker · “big”"
    assert schemas.summarize("twitch_set_title", {}) == "Choose a title"
    assert schemas.summarize("twitch_set_game", {"game": "Celeste"}) == "Celeste"
    assert schemas.summarize("twitch_start_poll", {"template": "poll"}) == "Start poll · poll"
    assert schemas.summarize("twitch_start_prediction", {}) == "Choose an overlay"
    assert schemas.summarize("twitch_resolve_prediction", {"outcome": "2"}) == "Winner: 2"
    assert schemas.summarize("twitch_snooze_ad", {}) == "Snooze next ad"
    assert schemas.config_problems("twitch_set_title", {"title": ""}) == ["New title is empty."]
    assert set(ACTION_OUTPUTS) >= {"game_hook", "twitch_shoutout", "twitch_set_title", "twitch_snooze_ad"}

    import modules.poll_manager as pm
    import modules.prediction_manager as prm

    schemas._OPTION_CACHE.clear()
    monkeypatch.setattr(pm, "list_poll_template_names", lambda: ["poll"])
    monkeypatch.setattr(prm, "list_prediction_template_names", lambda: ["predictions"])
    assert schemas._poll_template_options({}) == {"poll": "poll"}
    assert schemas._prediction_template_options({}) == {"predictions": "predictions"}
    schemas._OPTION_CACHE.clear()


# --------------------------------------------------------------------------- entry points


class FakeManager:
    def __init__(self, running=True, scheduled=True):
        self.is_running = running
        self.scheduled = scheduled
        self.events = []

    def _schedule_event_on_connector_loop(self, event_data):
        self.events.append(event_data)
        return self.scheduled


def test_emit_twitch_event(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(ci, "get_manager", lambda: manager)
    ci.emit_twitch_event("twitch_ad_break_begin", {"duration_seconds": 90, "source": "ignored"})
    (event,) = manager.events
    assert event["event_type"] == "twitch_ad_break_begin" and event["duration_seconds"] == 90
    assert event["source"] == "twitch" and event["timestamp"] > 0
    manager.scheduled = False
    ci.emit_twitch_event("twitch_channel_update", None)  # loop not ready: dropped quietly
    stopped = FakeManager(running=False)
    monkeypatch.setattr(ci, "get_manager", lambda: stopped)
    ci.emit_twitch_event("twitch_channel_update", {})
    assert stopped.events == []
    monkeypatch.setattr(ci, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError()))
    ci.emit_twitch_event("twitch_channel_update", {})  # never raises


def test_handle_twitch_prediction(monkeypatch):
    sent = []
    monkeypatch.setattr(ci, "emit_twitch_event", lambda event_type, fields: sent.append((event_type, fields)))
    snapshot = {"template": "predictions", "status": "resolved", "title": "Boss?", "prediction_id": "p1",
                "outcomes": [{"id": "o1", "name": "Yes", "users": 3, "points": 300, "winner": False},
                             {"id": "o2", "name": "No", "users": 1, "points": 50}],
                "total_points": 350, "total_users": 4, "winning_outcome_id": "o2"}
    for kind in ("progress", "sync", "bogus"):
        ci.handle_twitch_prediction(kind, snapshot)
    ci.handle_twitch_prediction("begin", None)
    assert sent == []
    ci.handle_twitch_prediction("resolved", snapshot)
    ci.handle_twitch_prediction("LOCK", dict(snapshot, winning_outcome_id=None, status="locked"))
    ci.handle_twitch_prediction("begin", {"outcomes": "junk"})
    assert sent[0] == ("twitch_prediction_end", {
        "prediction_id": "p1", "title": "Boss?", "outcomes": "Yes, No", "status": "resolved", "winner": "No",
        "points": 350, "users": 4, "template": "predictions"})
    assert sent[1][0] == "twitch_prediction_lock" and sent[1][1]["winner"] == ""
    assert sent[2][0] == "twitch_prediction_begin" and sent[2][1]["outcomes"] == ""
    ci.handle_twitch_prediction("end", {"total_points": "nan?"})  # bad numbers: dropped quietly
    assert len(sent) == 3

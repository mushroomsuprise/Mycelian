# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_predictions.py: limits, readiness, Helix calls, EventSub normalising."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import twitch_predictions as tp  # noqa: E402


class FakeApi:
    def __init__(self, response=None, error=None, *, connected=True, broadcaster_type="affiliate", token="tok"):
        self.calls = []
        self.response = response
        self.error = error
        self.is_connected = connected
        self.user_id = "42"
        self.auth_token = token
        self.user = SimpleNamespace(broadcaster_type=broadcaster_type)

    async def generic_api_call(self, url, method, params=None, json_data=None):
        self.calls.append((url, method, params, json_data))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def api(monkeypatch):
    import modules.twitch as twitch
    import modules.twitch_token_auth as tta

    holder = {"api": FakeApi(), "scopes": frozenset({tp.PREDICTION_SCOPE})}
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: holder["api"])
    monkeypatch.setattr(tta, "token_scopes", lambda token: holder["scopes"])
    return holder


def test_limits_and_validation():
    assert tp.clamp_title("x" * 60) == "x" * 45
    assert tp.clamp_outcome_name("  " + "y" * 30) == "y" * 25
    assert (tp.clamp_window(5), tp.clamp_window(5000), tp.clamp_window("nope"), tp.clamp_window(90)) == (30, 1800, 120, 90)
    good = {"title": "Win?", "outcomes": [{"name": "Yes"}, {"name": "No"}], "window_seconds": 60}
    assert tp.config_problems(good) == []
    bad = {"title": "", "outcomes": [{"name": "Yes"}], "window_seconds": 10}
    problems = tp.config_problems(bad)
    assert any("question" in p for p in problems) and any("2 to 10" in p for p in problems) and any("30 seconds" in p for p in problems)
    assert tp.config_problems({"title": "x" * 46, "outcomes": [{"name": "a"}, {"name": "A"}], "window_seconds": 60}) == [
        "The question can be at most 45 characters.", "Two outcomes have the same name."]
    assert "Every outcome needs a name." in tp.config_problems({"title": "t", "outcomes": [{"name": "a"}, {"name": ""}], "window_seconds": 60})
    assert any("25 characters" in p for p in tp.config_problems({"title": "t", "outcomes": [{"name": "a"}, {"name": "b" * 26}],
                                                                 "window_seconds": 60}))


def test_readiness(api, monkeypatch):
    assert tp.scope_status() == "ok" and tp.readiness() == "ok"
    api["api"].user.broadcaster_type = ""
    assert tp.readiness() == "not_eligible"
    api["scopes"] = frozenset()
    assert tp.readiness() == "missing" and "sign in" in tp.readiness_message("missing").lower()
    checks = []
    monkeypatch.setattr(tp, "check_scopes_in_background", lambda: checks.append(1))
    api["scopes"] = None
    assert tp.readiness() == "unknown" and checks == [1]
    api["api"].is_connected = False
    assert tp.scope_status() == "offline"
    assert tp.readiness_message("bogus") == ""


def test_create_and_end(api):
    api["api"].response = {"data": [{"id": "p1", "title": "Win?", "outcomes": []}]}
    ok, row = tp.create_prediction("Win?", ["Yes", "No"], 10)
    assert ok and row["id"] == "p1"
    url, method, _p, body = api["api"].calls[-1]
    assert (url, method) == (tp.HELIX_PREDICTIONS, "POST")
    assert body == {"title": "Win?", "outcomes": [{"title": "Yes"}, {"title": "No"}], "prediction_window": 30, "broadcaster_id": "42"}
    assert tp.end_prediction("p1", "locked") == (True, "")
    assert api["api"].calls[-1][3] == {"broadcaster_id": "42", "id": "p1", "status": "LOCKED"}
    assert tp.end_prediction("p1", "RESOLVED", "o2") == (True, "")
    assert api["api"].calls[-1][3]["winning_outcome_id"] == "o2"
    assert tp.end_prediction("p1", "RESOLVED") == (False, "Pick the outcome that won.")
    assert tp.end_prediction("p1", "weird")[0] is False
    api["api"].response = {"data": []}
    assert tp.create_prediction("Win?", ["Yes", "No"], 60) == (False, "Twitch didn't return the new prediction.")
    assert tp.get_current_prediction() is None
    api["api"].response = {"data": [{"id": "p2"}]}
    assert tp.get_current_prediction() == {"id": "p2"}


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("must be partner or affiliate"), "partners and affiliates"),
    (RuntimeError("an active prediction already exists"), "already running"),
    (RuntimeError("401 missing scope"), "sign in"),
    (RuntimeError("boom"), "Twitch said: boom"),
])
def test_errors_are_readable(api, error, expected):
    api["api"].error = error
    ok, message = tp.create_prediction("Win?", ["Yes", "No"], 60)
    assert ok is False and expected.lower() in message.lower()
    assert tp.end_prediction("p1", "CANCELED")[0] is False
    assert tp.get_current_prediction() is None


def test_offline_is_an_error(api):
    api["api"].is_connected = False
    ok, message = tp.create_prediction("Win?", ["Yes", "No"], 60)
    assert ok is False and "not connected" in message.lower()


def outcome(oid, title, points, users, color=None, top=None):
    return SimpleNamespace(id=oid, title=title, color=color, channel_points=points, users=users, top_predictors=top or [])


def test_normalize_events_and_rows(monkeypatch):
    top = [SimpleNamespace(user_name="Ann", user_login="ann", channel_points_used=500, channel_points_won=None)]
    event = SimpleNamespace(id="p1", title="Win?", outcomes=[outcome("o1", "Yes", 1000, 3, "BLUE", top), outcome("o2", "No", 0, 0)],
                            started_at="2026-01-01T00:00:00.123456789Z", locks_at="2026-01-01T00:02:00Z")
    begin = tp.normalize(event, kind="begin")
    assert begin["status"] == "active" and begin["locks_at"] - begin["started_at"] == pytest.approx(119.876544, abs=1e-3)
    assert begin["outcomes"][0] == {"id": "o1", "name": "Yes", "color": "blue", "users": 3, "points": 1000,
                                    "top_predictors": [{"name": "Ann", "points_used": 500, "points_won": None}]}
    assert begin["outcomes"][1]["color"] == "pink"
    monkeypatch.setattr(tp.time, "time", lambda: 99.0)
    locked = tp.normalize(event, kind="lock")
    assert locked["status"] == "locked" and locked["locked_at"] == 99.0
    end = SimpleNamespace(id="p1", title="Win?", outcomes=[], status=SimpleNamespace(value="resolved"),
                          winning_outcome_id="o1", ended_at="2026-01-01T00:05:00Z")
    done = tp.normalize(end, kind="end")
    assert done["status"] == "resolved" and done["winning_outcome_id"] == "o1" and done["ended_at"]
    row = {"id": "p2", "title": "T", "status": "LOCKED", "outcomes": [], "created_at": "2026-01-01T00:00:00Z", "prediction_window": 60,
           "locked_at": "2026-01-01T00:01:00Z"}
    norm = tp.normalize(row)
    assert norm["status"] == "locked" and norm["locks_at"] - norm["started_at"] == 60 and norm["ended_at"] is None
    assert tp.normalize({"id": "p3"})["status"] == "active"


def test_handle_event_forwards_to_the_manager(monkeypatch):
    import modules.prediction_manager as pm

    seen = []
    monkeypatch.setattr(pm, "get_prediction_manager", lambda: SimpleNamespace(apply_twitch_update=lambda u, kind: seen.append((u["id"], kind))))
    tp.handle_event("progress", SimpleNamespace(id="p1", title="t", outcomes=[]))
    assert seen == [("p1", "progress")]


def test_scope_notice(monkeypatch):
    import modules.notification_engine as ne

    notes = []
    monkeypatch.setattr(ne, "notify", lambda *a, **k: notes.append(k))
    tp.notify_scope_missing()
    assert notes[0]["dedupe_key"] == "twitch:prediction_scope_missing"
    monkeypatch.setattr(ne, "notify", lambda *a, **k: 1 / 0)
    tp.notify_scope_missing()  # never raises

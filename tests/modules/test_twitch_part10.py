# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 10: prediction / goal / ad-break / shoutout subscriptions and
handlers, the new optional scopes, and the connector triggers fired from Twitch events."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402
from twitchAPI.type import AuthScope  # noqa: E402

from modules import connector_integration, dataobjects, twitch as tw, twitch_goals, twitch_polls, twitch_predictions  # noqa: E402
from modules.twitch_token_auth import OPTIONAL_SCOPES  # noqa: E402


def run(coro):
    return asyncio.run(coro)


ALL_SCOPES = {"channel:manage:predictions", "channel:read:goals", "channel:read:ads", "moderator:manage:shoutouts"}
LISTENS = ("listen_channel_prediction_begin", "listen_channel_prediction_progress", "listen_channel_prediction_lock",
           "listen_channel_prediction_end", "listen_goal_begin", "listen_goal_progress", "listen_goal_end",
           "listen_channel_ad_break_begin", "listen_channel_shoutout_receive")


@pytest.fixture
def api(monkeypatch):
    state = MagicMock()
    state.get_twitch_data.return_value = SimpleNamespace(current_category="Old Game")
    state.save_changes.return_value = True
    monkeypatch.setattr(dataobjects, "state_manager", state)
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42")
    a.user_id = "42"
    a.auth_token = "tok"
    return a


@pytest.fixture
def events(monkeypatch):
    sent = []
    monkeypatch.setattr(connector_integration, "emit_twitch_event", lambda kind, fields: sent.append((kind, fields)))
    return sent


def eventsub():
    es = MagicMock()
    for name in LISTENS:
        setattr(es, name, AsyncMock())
    return es


def test_new_scopes_are_requested_and_optional(api):
    for scope in (AuthScope.CHANNEL_READ_GOALS, AuthScope.CHANNEL_READ_ADS, AuthScope.CHANNEL_MANAGE_PREDICTIONS):
        assert scope in api.authscope and scope in OPTIONAL_SCOPES


def test_subscribe_extra_events_with_every_scope(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: ALL_SCOPES)
    refreshed = []
    monkeypatch.setattr(twitch_goals, "refresh_from_twitch", lambda: refreshed.append(1) or (True, ""))
    es = eventsub()
    run(api._subscribe_extra_events(es))
    es.listen_channel_prediction_begin.assert_awaited_once_with("42", api.on_prediction_begin)
    es.listen_channel_prediction_lock.assert_awaited_once_with("42", api.on_prediction_lock)
    es.listen_goal_progress.assert_awaited_once_with("42", api.on_goal_progress)
    es.listen_channel_ad_break_begin.assert_awaited_once_with("42", api.on_ad_break_begin)
    es.listen_channel_shoutout_receive.assert_awaited_once_with("42", "42", api.on_shoutout_receive)
    assert refreshed == [1]


def test_missing_scopes_switch_features_off_and_ask_to_sign_in(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: {"moderator:manage:shoutouts"})
    asked = []
    monkeypatch.setattr(twitch_predictions, "notify_scope_missing", lambda: asked.append("predictions"))
    monkeypatch.setattr(twitch_goals, "notify_scope_missing", lambda: asked.append("goals"))
    es = eventsub()
    run(api._subscribe_extra_events(es))
    assert asked == ["predictions", "goals"]
    es.listen_channel_prediction_begin.assert_not_called()
    es.listen_goal_begin.assert_not_called()
    es.listen_channel_ad_break_begin.assert_not_called()  # no notice for ads: the trigger just stays quiet
    es.listen_channel_shoutout_receive.assert_awaited_once()


def test_unknown_scopes_are_read_first_then_skipped(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: None)
    validate = AsyncMock()
    monkeypatch.setattr(tw, "validate_access_token", validate)
    es = eventsub()
    run(api._subscribe_extra_events(es))
    validate.assert_awaited_once_with("tok")
    es.listen_channel_prediction_begin.assert_not_called()


def test_one_failing_subscription_does_not_stop_the_others(api, monkeypatch):
    monkeypatch.setattr(tw, "token_scopes", lambda t: ALL_SCOPES)
    monkeypatch.setattr(twitch_goals, "refresh_from_twitch", lambda: (True, ""))
    es = eventsub()
    es.listen_channel_prediction_begin = AsyncMock(side_effect=RuntimeError("409"))
    es.listen_goal_begin = AsyncMock(side_effect=RuntimeError("403"))
    run(api._subscribe_extra_events(es))
    es.listen_channel_ad_break_begin.assert_awaited_once()
    es.listen_channel_shoutout_receive.assert_awaited_once()


@pytest.mark.parametrize("method,kind", [("on_prediction_begin", "begin"), ("on_prediction_progress", "progress"),
                                         ("on_prediction_lock", "lock"), ("on_prediction_end", "end")])
def test_prediction_handlers_forward(api, monkeypatch, method, kind):
    seen = []
    monkeypatch.setattr(twitch_predictions, "handle_event", lambda k, ev: seen.append((k, ev)))
    run(getattr(api, method)(SimpleNamespace(event="EV")))
    assert seen == [(kind, "EV")] and api.last_event_time is not None


@pytest.mark.parametrize("method,kind", [("on_goal_begin", "begin"), ("on_goal_progress", "progress"), ("on_goal_end", "end")])
def test_goal_handlers_forward(api, monkeypatch, method, kind):
    seen = []
    monkeypatch.setattr(twitch_goals, "handle_event", lambda k, ev: seen.append((k, ev)))
    run(getattr(api, method)(SimpleNamespace(event="EV")))
    assert seen == [(kind, "EV")]


def test_ad_break_and_shoutout_triggers(api, events):
    run(api.on_ad_break_begin(SimpleNamespace(event=SimpleNamespace(duration_seconds=90, is_automatic=True, requester_user_name=None))))
    run(api.on_shoutout_receive(SimpleNamespace(event=SimpleNamespace(
        from_broadcaster_user_name="Pal", from_broadcaster_user_login="pal", viewer_count=12))))
    assert events == [
        ("twitch_ad_break_begin", {"duration_seconds": 90, "is_automatic": True, "requester": ""}),
        ("twitch_shoutout_received", {"username": "Pal", "user_login": "pal", "viewer_count": 12}),
    ]


def test_poll_triggers(api, monkeypatch, events):
    monkeypatch.setattr(twitch_polls, "handle_begin", lambda ev: None)
    monkeypatch.setattr(twitch_polls, "handle_end", lambda ev: None)
    choices = [SimpleNamespace(title="Tacos", votes=5), SimpleNamespace(title="Pizza", votes=9)]
    run(api.on_poll_begin(SimpleNamespace(event=SimpleNamespace(id="p", title="Dinner?", choices=choices))))
    run(api.on_poll_end(SimpleNamespace(event=SimpleNamespace(id="p", title="Dinner?", choices=choices,
                                                              status=SimpleNamespace(value="COMPLETED")))))
    assert events[0] == ("twitch_poll_begin", {"poll_id": "p", "title": "Dinner?", "choices": "Tacos, Pizza", "total_votes": 14})
    assert events[1][1]["winner"] == "Pizza" and events[1][1]["winner_votes"] == 9 and events[1][1]["status"] == "completed"
    assert tw._poll_trigger_fields(SimpleNamespace(), ended=True)["winner"] == ""


def test_channel_update_trigger_knows_the_previous_category(api, monkeypatch, events):
    import modules.web_engine as we

    monkeypatch.setattr(we, "web_engine_instance", None, raising=False)
    monkeypatch.setattr(api, "_broadcast_twitch_data_update", lambda *a: None)
    run(api.on_update(SimpleNamespace(event=SimpleNamespace(category_name="New Game", title="Hi", language="en"))))
    assert events[-1] == ("twitch_channel_update", {"title": "Hi", "category": "New Game", "previous_category": "Old Game",
                                                    "category_changed": True, "language": "en"})
    dataobjects.state_manager.get_twitch_data.side_effect = RuntimeError("no data")
    run(api.on_update(SimpleNamespace(event=SimpleNamespace(category_name="New Game", title="Hi", language="en"))))
    assert events[-1][1]["previous_category"] == "" and events[-1][1]["category_changed"] is False


def test_hype_progress_trigger(api, monkeypatch, events):
    import modules.web_engine as we

    monkeypatch.setattr(we, "web_engine_instance", None, raising=False)
    monkeypatch.setattr(api, "_queue_hype_train_level_alerts", lambda ev: None)
    monkeypatch.setattr(tw, "_notify_chatbot_event", lambda *a, **k: None)
    event = SimpleNamespace(level=3, progress=40, goal=100, total=900, type=SimpleNamespace(value="golden_kappa"),
                            is_shared_train=False, all_time_high_level=5)
    run(api.on_hype_train_progress(SimpleNamespace(event=event)))
    assert events[-1] == ("twitch_hype_train_progress", {"level": 3, "progress": 40, "goal": 100, "total": 900,
                                                         "type": "golden_kappa", "is_shared_train": False,
                                                         "all_time_high_level": 5})


def test_connector_emit_never_raises(monkeypatch):
    monkeypatch.setattr(connector_integration, "emit_twitch_event", lambda *a: 1 / 0)
    tw._emit_connector_event("twitch_poll_begin", {})

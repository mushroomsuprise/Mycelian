# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_polls.py (scope gate, validation, Helix create/end, EventSub)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import threading  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import notification_engine, poll_manager, twitch, twitch_polls as tp, twitch_token_auth  # noqa: E402


class _SyncThread:
    """Runs the target inline at start() (keeps background workers deterministic)."""

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._target, self.name = target, name

    def start(self):
        self._target()

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(tp, "_checked_tokens", set())


def _api(**kw):
    base = dict(is_connected=True, auth_token="tok", user_id="b1", user=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _set_api(monkeypatch, api):
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: api)


# ------------------------------------------------------------------ readiness


def test_scope_status_offline(monkeypatch):
    _set_api(monkeypatch, None)
    assert tp.scope_status() == "offline"
    _set_api(monkeypatch, _api(is_connected=False))
    assert tp.scope_status() == "offline"
    _set_api(monkeypatch, _api(auth_token=""))
    assert tp.scope_status() == "offline"


@pytest.mark.parametrize("granted, expected", [(None, "unknown"), ({"channel:manage:polls"}, "ok"), (set(), "missing")])
def test_scope_status_from_token(monkeypatch, granted, expected):
    _set_api(monkeypatch, _api())
    monkeypatch.setattr(twitch_token_auth, "token_scopes", lambda tok: granted)
    assert tp.scope_status() == expected
    assert tp.scope_missing() is (expected == "missing")


def test_scope_status_exception_is_unknown(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(twitch, "get_twitch_api", boom)
    assert tp.scope_status() == "unknown"


def test_readiness_unknown_starts_background_check(monkeypatch):
    monkeypatch.setattr(tp, "scope_status", lambda: "unknown")
    check = MagicMock()
    monkeypatch.setattr(tp, "check_scopes_in_background", check)
    assert tp.readiness() == "unknown"
    check.assert_called_once()


def test_readiness_passes_through_non_ok(monkeypatch):
    monkeypatch.setattr(tp, "scope_status", lambda: "missing")
    assert tp.readiness() == "missing"


@pytest.mark.parametrize(
    "user, expected",
    [
        (None, "ok"),
        (SimpleNamespace(broadcaster_type=SimpleNamespace(value="")), "not_eligible"),
        (SimpleNamespace(broadcaster_type=""), "not_eligible"),
        (SimpleNamespace(broadcaster_type=SimpleNamespace(value="affiliate")), "ok"),
        (SimpleNamespace(broadcaster_type="partner"), "ok"),
    ],
)
def test_readiness_broadcaster_type(monkeypatch, user, expected):
    monkeypatch.setattr(tp, "scope_status", lambda: "ok")
    _set_api(monkeypatch, _api(user=user))
    assert tp.readiness() == expected


def test_readiness_ok_when_user_lookup_fails(monkeypatch):
    monkeypatch.setattr(tp, "scope_status", lambda: "ok")
    monkeypatch.setattr(twitch, "get_twitch_api", MagicMock(side_effect=RuntimeError("x")))
    assert tp.readiness() == "ok"


def test_check_scopes_noop_without_token_or_on_error(monkeypatch):
    monkeypatch.setattr(tp.threading, "Thread", MagicMock())
    monkeypatch.setattr(twitch, "get_twitch_api", MagicMock(side_effect=RuntimeError("x")))
    tp.check_scopes_in_background()
    _set_api(monkeypatch, _api(auth_token="  "))
    tp.check_scopes_in_background()
    tp.threading.Thread.assert_not_called()


def test_check_scopes_runs_once_per_token(monkeypatch):
    _set_api(monkeypatch, _api(auth_token="abc"))
    monkeypatch.setattr(tp.threading, "Thread", _SyncThread)
    validate = AsyncMock(return_value={"scopes": []})
    monkeypatch.setattr(twitch_token_auth, "validate_access_token", validate)
    tp.check_scopes_in_background()
    tp.check_scopes_in_background()
    validate.assert_awaited_once_with("abc")
    assert "abc" in tp._checked_tokens


def test_check_scopes_failure_forgets_token(monkeypatch):
    _set_api(monkeypatch, _api(auth_token="abc"))
    monkeypatch.setattr(tp.threading, "Thread", _SyncThread)
    monkeypatch.setattr(twitch_token_auth, "validate_access_token", AsyncMock(side_effect=RuntimeError("net")))
    tp.check_scopes_in_background()
    assert "abc" not in tp._checked_tokens


def test_readiness_message():
    for key in ("missing", "offline", "unknown", "not_eligible"):
        assert tp.readiness_message(key)
    assert tp.readiness_message("ok") == ""


def test_clamp_choice_name():
    assert tp.clamp_choice_name(None) == ""
    assert tp.clamp_choice_name("  hi  ") == "hi"
    assert tp.clamp_choice_name("x" * 24 + " yz") == "x" * 24


def test_notify_scope_missing_calls_notify(monkeypatch):
    notify = MagicMock()
    monkeypatch.setattr(notification_engine, "notify", notify)
    tp.notify_scope_missing()
    notify.assert_called_once()
    assert notify.call_args.kwargs["dedupe_key"] == "twitch:poll_scope_missing"
    assert notify.call_args.kwargs["source"] == "twitch"


def test_notify_scope_missing_swallows_errors(monkeypatch):
    monkeypatch.setattr(notification_engine, "notify", MagicMock(side_effect=RuntimeError("x")))
    tp.notify_scope_missing()


# ------------------------------------------------------------------ validation


def test_config_problems_ok():
    cfg = {"twitch_title": "Q?", "categories": [{"name": "a"}, {"name": "b"}], "duration_seconds": 60}
    assert tp.config_problems(cfg) == []


def test_config_problems_all_bad():
    cfg = {"twitch_title": "", "categories": [{"name": "x" * 30}], "duration_seconds": 5}
    problems = tp.config_problems(cfg)
    assert len(problems) == 4
    assert "question" in problems[0]
    assert "x" * 30 in problems[2]


def test_config_problems_long_title():
    cfg = {"twitch_title": "t" * 61, "categories": [{"name": "a"}, {"name": None}], "duration_seconds": 1800}
    assert tp.config_problems(cfg) == ["The Twitch poll question can be at most 60 characters."]


@pytest.mark.parametrize("value, expected", [(0, 180), (None, 180), (-5, 180), (5, 15), (100, 100), (9999, 1800)])
def test_clamp_duration(value, expected):
    assert tp.clamp_duration(value) == expected


@pytest.mark.parametrize("value, expected", [("50", 50), (0, 1), (10**9, 1_000_000), ("x", 100), (None, 100)])
def test_clamp_points(value, expected):
    assert tp.clamp_points(value) == expected


# ------------------------------------------------------------------ _run / helix


def test_run_returns_value_and_raises_errors():
    async def ok():
        return 5

    async def bad():
        raise ValueError("bad")

    assert tp._run(ok()) == 5
    with pytest.raises(ValueError):
        tp._run(bad())


def test_run_times_out(monkeypatch):
    class Stuck(_SyncThread):
        def start(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(tp.threading, "Thread", Stuck)

    async def never():
        return 1

    coro = never()
    with pytest.raises(TimeoutError):
        tp._run(coro, timeout=0.01)
    coro.close()


def test_broadcaster_and_api(monkeypatch):
    _set_api(monkeypatch, None)
    with pytest.raises(RuntimeError, match="not connected"):
        tp._broadcaster_and_api()
    _set_api(monkeypatch, _api(user_id=""))
    with pytest.raises(RuntimeError, match="isn't known"):
        tp._broadcaster_and_api()
    api = _api()
    _set_api(monkeypatch, api)
    assert tp._broadcaster_and_api() == (api, "b1")


@pytest.mark.parametrize(
    "msg, expected",
    [
        ("Not a partner", "partners and affiliates"),
        ("poll already active", "already running"),
        ("401 unauthorized", "permission"),
        ("missing scope", "permission"),
        ("other", "Twitch said: other"),
    ],
)
def test_twitch_error_text(msg, expected):
    assert expected in tp._twitch_error_text(RuntimeError(msg))


def test_twitch_error_text_empty_message_uses_type_name():
    assert tp._twitch_error_text(KeyError()) == "Twitch said: KeyError"


def test_create_poll_success_with_points(monkeypatch):
    api = _api()
    api.generic_api_call = AsyncMock(return_value={"data": [{"id": "p1", "choices": []}]})
    _set_api(monkeypatch, api)
    ok, poll = tp.create_poll("T" * 70, ["a" * 30, "b"], 5, points_enabled=True, points_per_vote="x")
    assert ok is True and poll["id"] == "p1"
    url, method = api.generic_api_call.await_args.args
    body = api.generic_api_call.await_args.kwargs["json_data"]
    assert (url, method) == (tp.HELIX_POLLS, "POST")
    assert body["title"] == "T" * 60
    assert body["choices"][0]["title"] == "a" * 25
    assert body["duration"] == 15
    assert body["channel_points_per_vote"] == 100 and body["channel_points_voting_enabled"] is True
    assert body["broadcaster_id"] == "b1"


@pytest.mark.parametrize("data", [None, {"data": []}, {"data": [{"id": ""}]}, {"data": ["x"]}, []])
def test_create_poll_bad_response(monkeypatch, data):
    api = _api()
    api.generic_api_call = AsyncMock(return_value=data)
    _set_api(monkeypatch, api)
    assert tp.create_poll("t", ["a", "b"], 60) == (False, "Twitch didn't return the new poll.")


def test_create_poll_error(monkeypatch):
    _set_api(monkeypatch, None)
    ok, msg = tp.create_poll("t", ["a", "b"], 60)
    assert ok is False and "not connected" in msg


def test_choice_map_by_title_then_position():
    poll = {"choices": [
        {"id": "c1", "title": "Blue"},
        {"id": "c2", "title": "renamed"},
        {"id": "", "title": "skip"},
        "junk",
        {"id": "c3", "title": "Red"},
    ]}
    cats = [{"id": "o-red", "name": "Red"}, {"id": "o-blue", "name": "Blue"}, {"id": "o-green", "name": "Green"}]
    assert tp.choice_map(poll, cats) == {"c1": "o-blue", "c3": "o-red", "c2": "o-green"}
    assert tp.choice_map({}, cats) == {}


def test_end_poll_ignores_blank_id(monkeypatch):
    monkeypatch.setattr(tp.threading, "Thread", MagicMock())
    tp.end_poll("  ")
    tp.threading.Thread.assert_not_called()


@pytest.mark.parametrize("show, status", [(True, "TERMINATED"), (False, "ARCHIVED")])
def test_end_poll_patches(monkeypatch, show, status):
    api = _api()
    api.generic_api_call = AsyncMock(return_value={})
    _set_api(monkeypatch, api)
    started = []

    class T(_SyncThread):
        def start(self):
            started.append(self.name)
            super().start()

    monkeypatch.setattr(tp.threading, "Thread", T)
    tp.end_poll("p1", show_results=show)
    assert "TwitchPollEnd" in started
    assert api.generic_api_call.await_args.kwargs["json_data"] == {"broadcaster_id": "b1", "id": "p1", "status": status}


def test_end_poll_failure_notifies(monkeypatch):
    _set_api(monkeypatch, None)
    monkeypatch.setattr(tp.threading, "Thread", _SyncThread)
    notify = MagicMock()
    monkeypatch.setattr(notification_engine, "notify", notify)
    tp.end_poll("p1")
    notify.assert_called_once()
    assert notify.call_args.kwargs["dedupe_key"] == "twitch:poll_end_failed"


def test_end_poll_failure_notify_error_swallowed(monkeypatch):
    _set_api(monkeypatch, None)
    monkeypatch.setattr(tp.threading, "Thread", _SyncThread)
    monkeypatch.setattr(notification_engine, "notify", MagicMock(side_effect=RuntimeError("x")))
    tp.end_poll("p1")


# ------------------------------------------------------------------ EventSub


def test_value_dict_and_attr():
    assert tp._value({"a": 1}, "a") == 1
    assert tp._value(SimpleNamespace(a=2), "a") == 2
    assert tp._value(SimpleNamespace(), "a", 3) == 3


def test_event_choice_votes():
    event = {"choices": [
        {"id": "a", "votes": 3},
        SimpleNamespace(id="b", votes=None),
        {"id": "c", "votes": -2},
        {"id": "d", "votes": "lots"},
        {"id": "", "votes": 9},
    ]}
    assert tp.event_choice_votes(event) == {"a": 3, "b": 0, "c": 0, "d": 0}
    assert tp.event_choice_votes(SimpleNamespace()) == {}


def test_parse_timestamp():
    assert tp.parse_timestamp(None) is None
    assert tp.parse_timestamp("") is None
    dt = datetime(2021, 3, 19, 6, 8, 33, tzinfo=timezone.utc)
    assert tp.parse_timestamp(dt) == dt.timestamp()
    assert tp.parse_timestamp("2021-03-19T06:08:33.871278372Z") == pytest.approx(dt.timestamp() + 0.871278)
    assert tp.parse_timestamp("garbage") is None

    class BadTs:
        def timestamp(self):
            raise OSError("x")

    assert tp.parse_timestamp(BadTs()) is None


def test_handlers_forward_to_poll_manager(monkeypatch):
    pm = MagicMock()
    monkeypatch.setattr(poll_manager, "get_poll_manager", lambda: pm)
    tp.handle_begin({"id": "p", "ends_at": "2021-03-19T06:08:33Z"})
    pm.twitch_poll_began.assert_called_once()
    assert pm.twitch_poll_began.call_args.args[0] == "p"
    assert isinstance(pm.twitch_poll_began.call_args.args[1], float)

    tp.handle_progress({"id": "p", "choices": [{"id": "a", "votes": 2}]})
    pm.apply_twitch_votes.assert_called_once_with("p", {"a": 2})

    tp.handle_end(SimpleNamespace(id="p", status=SimpleNamespace(value="COMPLETED"), choices=[]))
    pm.twitch_poll_ended.assert_called_once_with("p", {}, "completed")
    tp.handle_end({"id": None, "status": None})
    assert pm.twitch_poll_ended.call_args.args == ("", {}, "")

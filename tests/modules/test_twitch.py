# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 1: module-level helpers before ``class Twitch_API``.

Covers the lazy web engine, the alert/feed/instant helpers, chat-notice parsing, the
chatbot lane, gift recipient / gift duration matching, the single-gift wait, badge
harvesting and the connector new-sub / resub emitters.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import concurrent.futures  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

import modules  # noqa: E402
from modules import (  # noqa: E402
    alert_processor,
    alertutils,
    chatbot,
    connector_manager,
    dataobjects,
    twitch as tw,
    twitch_subscriber_registry,
)
from modules.uiwindows import activity_feed, activity_feed_model  # noqa: E402


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _fresh_globals(monkeypatch):
    for name in ("_pending_single_gifts", "_recent_gift_recipients", "_gift_duration_notices",
                 "_gift_duration_watchers"):
        monkeypatch.setattr(tw, name, [])
    monkeypatch.setattr(tw, "_gift_alert_gifters", {})
    monkeypatch.setattr(tw, "_cheer_bits_cache", {})
    monkeypatch.setattr(tw, "_alert_user_message_cache", {})


@pytest.fixture
def engine(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=inst))
    return inst


@pytest.fixture
def pipeline(monkeypatch, engine):
    """Capture queue / store / feed / instant alert calls."""
    rec = SimpleNamespace(stored=[], queued=[], feed=[], updates=[], engine=engine)
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda aid, payload: rec.stored.append((aid, payload)))
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: rec.queued.append(a))
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda *a, **kw: rec.feed.append(kw))
    monkeypatch.setattr(activity_feed, "update_alert_in_feed", lambda aid, **kw: rec.updates.append((aid, kw)))
    monkeypatch.setattr(activity_feed, "show_sub_duration", lambda *a: True)
    return rec


def _gift_alert(**kw):
    a = alertutils.AlertObj()
    a.alert_id = kw.pop("alert_id", "A1")
    a.username = kw.pop("username", "gifter")
    a.tier = kw.pop("tier", 1)
    a.gift_qty = kw.pop("gift_qty", 1)
    a.timestamp = kw.pop("timestamp", 1000.0)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# --------------------------------------------------------------------------- lazy web engine


def test_lazy_web_engine_loads_once_and_forwards(monkeypatch):
    fake = SimpleNamespace(value=1)
    monkeypatch.setitem(sys.modules, "modules.web_engine", fake)
    monkeypatch.setattr(modules, "web_engine", fake, raising=False)
    lazy = tw._LazyWebEngine()
    assert lazy.value == 1
    lazy.value = 5
    assert fake.value == 5
    assert object.__getattribute__(lazy, "_mod") is fake
    assert lazy._load() is fake


# --------------------------------------------------------------------------- alert pipeline helpers


def test_twitch_alerts_enabled(monkeypatch):
    state = MagicMock()
    monkeypatch.setattr(dataobjects, "state_manager", state)
    state.get_twitch_data.return_value = None
    assert tw._twitch_alerts_enabled() is True
    state.get_twitch_data.return_value = SimpleNamespace(alerts_enabled=False)
    assert tw._twitch_alerts_enabled() is False
    state.get_twitch_data.return_value = SimpleNamespace()
    assert tw._twitch_alerts_enabled() is True
    state.get_twitch_data.side_effect = RuntimeError("x")
    assert tw._twitch_alerts_enabled() is True


def test_queue_twitch_alert(pipeline, monkeypatch):
    alert = _gift_alert()
    assert tw._queue_twitch_alert(alert) is True
    assert pipeline.stored[0][0] == "A1" and pipeline.stored[0][1]["username"] == "gifter"
    assert pipeline.queued == [alert]
    assert tw._queue_twitch_alert(alert, stored={"x": 1}, play=False) is True
    assert pipeline.stored[1] == ("A1", {"x": 1}) and len(pipeline.queued) == 1
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: False)
    assert tw._queue_twitch_alert(alert) is False
    assert len(pipeline.stored) == 2


def test_send_twitch_instant_alert(engine, monkeypatch):
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    tw._send_twitch_instant_alert({"a": 1})
    engine.instant_alert.assert_called_once_with({"a": 1})
    engine.instant_alert.side_effect = RuntimeError("x")
    tw._send_twitch_instant_alert({"a": 2})  # logged
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: False)
    tw._send_twitch_instant_alert({"a": 3})
    assert engine.instant_alert.call_count == 2


def test_send_twitch_instant_alert_without_engine(monkeypatch):
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    tw._send_twitch_instant_alert({})


def test_add_twitch_alert_to_feed_filters_unknown_kwargs(monkeypatch):
    calls = []

    def feed(alert_type, message, raider_count=None):
        calls.append((alert_type, message, raider_count))

    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", feed)
    tw._add_twitch_alert_to_feed(alert_type="Raid", message="m", raider_count=5, game_name="x")
    assert calls == [("Raid", "m", 5)]


def test_add_twitch_alert_to_feed_passes_all_with_var_kwargs(monkeypatch):
    calls = []
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda *a, **kw: calls.append(kw))
    tw._add_twitch_alert_to_feed(alert_type="Raid", game_name="x")
    assert calls == [{"alert_type": "Raid", "game_name": "x"}]


def test_add_twitch_alert_to_feed_signature_error_and_disabled(monkeypatch):
    calls = []

    class Feed:
        def __call__(self, **kw):
            calls.append(kw)

    feed = Feed()
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", feed)
    monkeypatch.setattr("inspect.signature", MagicMock(side_effect=ValueError("no sig")))
    tw._add_twitch_alert_to_feed(alert_type="X", extra=1)
    assert calls == [{"alert_type": "X", "extra": 1}]
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: False)
    tw._add_twitch_alert_to_feed(alert_type="Y")
    assert len(calls) == 1


# --------------------------------------------------------------------------- chat notice parsing


class _Enum:
    def __init__(self, value):
        self.value = value


def test_normalize_chat_notice_type():
    assert tw._normalize_chat_notice_type(None) == ""
    assert tw._normalize_chat_notice_type(" Sub_Gift ") == "sub_gift"
    assert tw._normalize_chat_notice_type(_Enum("RESUB")) == "resub"
    assert tw._normalize_chat_notice_type(42) == "42"

    class CallableValue:
        def value(self):
            return "x"

        def __str__(self):
            return "Fallback"

    assert tw._normalize_chat_notice_type(CallableValue()) == "fallback"


def test_is_watch_streak_notice():
    assert tw._is_watch_streak_notice("watch_streak")
    assert tw._is_watch_streak_notice("shared_chat_watch_streak")
    assert not tw._is_watch_streak_notice("")
    assert not tw._is_watch_streak_notice(None)


def test_int_from_watch_field():
    assert tw._int_from_watch_field(None, "k") == 0
    assert tw._int_from_watch_field({"k": "5"}, "k") == 5
    assert tw._int_from_watch_field(SimpleNamespace(k=3), "k") == 3
    assert tw._int_from_watch_field(SimpleNamespace(to_dict=lambda: {"k": 7}), "k") == 7
    assert tw._int_from_watch_field(SimpleNamespace(to_dict=lambda: None), "k") == 0

    def boom():
        raise RuntimeError("x")

    assert tw._int_from_watch_field(SimpleNamespace(to_dict=boom), "k") == 0
    assert tw._int_from_watch_field({"k": "abc"}, "k") == 0
    assert tw._int_from_watch_field(SimpleNamespace(), "k") == 0


def test_streak_count_from_system_message():
    assert tw._streak_count_from_system_message(None, "", "nothing here") == 0
    assert tw._streak_count_from_system_message("x watched 12 consecutive streams") == 12
    assert tw._streak_count_from_system_message(None, "5 Consecutive Streams!") == 5


def test_streak_count_bad_int(monkeypatch):
    class M:
        def group(self, n):
            return "x"

    monkeypatch.setattr(tw, "_WATCH_STREAK_COUNT_RE", SimpleNamespace(search=lambda t: M()))
    assert tw._streak_count_from_system_message("a", "b") == 0


@pytest.mark.parametrize(
    "raw, expected",
    [("1000", 1), ("2000", 2), ("3000", 3), (3000, 3), ("2", 2), ("Prime", 1), (None, 1), ("0000", 1),
     ("7", 1), ("12000", 12)],
)
def test_paid_tier_from_plan(raw, expected):
    assert tw._paid_tier_from_plan(raw) == expected


def test_modiversary_months_from_texts(monkeypatch):
    assert tw._modiversary_months_from_texts(None, "no number") == 0
    assert tw._modiversary_months_from_texts("modded for 13 months") == 13
    assert tw._modiversary_months_from_texts("", "1 month") == 1

    class M:
        def group(self, n):
            return "x"

    monkeypatch.setattr(tw, "_MODIVERSARY_MONTHS_RE", SimpleNamespace(search=lambda t: M()))
    assert tw._modiversary_months_from_texts("a") == 0


def test_send_chat_event_line(engine):
    tw._send_chat_event_line("   ")
    engine.new_message.assert_not_called()
    tw._send_chat_event_line(" Hello ", message_id=55, event_kind=" raid ")
    payload = engine.new_message.call_args.args[0]
    assert payload["id"] == "55" and payload["message"] == "Hello" and payload["type"] == "event"
    assert payload["event_kind"] == "raid" and payload["username"] == ""
    tw._send_chat_event_line("x")
    payload = engine.new_message.call_args.args[0]
    assert payload["id"].startswith("event-") and "event_kind" not in payload
    engine.new_message.side_effect = RuntimeError("x")
    tw._send_chat_event_line("y")  # logged, no raise


def test_is_chat_announcement_notice():
    assert tw.is_chat_announcement_notice("announcement")
    assert tw.is_chat_announcement_notice(_Enum("shared_chat_announcement"))
    assert not tw.is_chat_announcement_notice("sub")


def test_chat_notification_message_text():
    assert tw._chat_notification_message_text(SimpleNamespace()) is None
    assert tw._chat_notification_message_text(SimpleNamespace(message={"text": "hi"})) == "hi"
    assert tw._chat_notification_message_text(SimpleNamespace(message={"text": ""})) is None
    assert tw._chat_notification_message_text(SimpleNamespace(message=SimpleNamespace(text="yo"))) == "yo"
    assert tw._chat_notification_message_text(SimpleNamespace(message=SimpleNamespace(text=None))) is None


def test_announcement_banner_fields():
    ev = SimpleNamespace(message=SimpleNamespace(text="", fragments=[{"type": "text", "text": "frag"}]),
                         system_message="System says")
    fields = tw.announcement_banner_fields(ev)
    assert fields["message"] == "System says"
    assert fields["fragments"] == [{"text": "frag", "type": "text"}]
    assert tw.announcement_banner_fields(SimpleNamespace(message=None)) == {"message": "", "fragments": None}


def test_announcement_banner_fields_serialize_error(monkeypatch):
    monkeypatch.setattr(tw, "serialize_chat_message_fragments", MagicMock(side_effect=RuntimeError("x")))
    ev = SimpleNamespace(message=SimpleNamespace(text="t", fragments=[1]))
    assert tw.announcement_banner_fields(ev) == {"message": "t", "fragments": None}


def test_emit_announcement_banner(monkeypatch):
    from modules import chat_banner

    emit = MagicMock(return_value=True)
    monkeypatch.setattr(chat_banner, "emit_chat_banner", emit)
    assert tw.emit_announcement_banner_from_notice(SimpleNamespace(message=None)) is False
    emit.assert_not_called()
    ev = SimpleNamespace(message=SimpleNamespace(text="Big news", fragments=None))
    assert tw.emit_announcement_banner_from_notice(ev) is True
    emit.assert_called_once_with("Big news", source="twitch_announcement", fragments=None)


# --------------------------------------------------------------------------- chatbot lane


class _StopJobs:
    def __bool__(self):
        return True

    def popleft(self):
        raise SystemExit


def _stop_lane(lane):
    with lane._cond:
        lane._jobs = _StopJobs()
        lane._cond.notify_all()
    if lane._thread is not None:
        lane._thread.join(2)


def test_chatbot_lane_runs_jobs_in_order_and_reports_errors():
    lane = tw._ChatbotLane()
    try:
        assert lane.busy() is False and lane.on_lane_thread() is False
        order = []
        gate = threading.Event()
        f1 = lane.submit(lambda: (gate.wait(2), order.append(1), lane.on_lane_thread())[-1])
        assert lane.busy() is True
        f2 = lane.submit(lambda: order.append(2) or "two")

        def bad():
            raise ValueError("bad job")

        f3 = lane.submit(bad)
        gate.set()
        assert f1.result(2) is True
        assert f2.result(2) == "two"
        with pytest.raises(ValueError):
            f3.result(2)
        assert order == [1, 2]
        for _ in range(100):
            if not lane.busy():
                break
            time.sleep(0.005)
        assert lane.busy() is False
    finally:
        _stop_lane(lane)


def test_chatbot_lane_restarts_dead_thread():
    lane = tw._ChatbotLane()
    lane._thread = SimpleNamespace(is_alive=lambda: False)
    try:
        assert lane.submit(lambda: 3).result(2) == 3
        assert lane._thread.name == "ChatbotLane"
    finally:
        _stop_lane(lane)


def test_run_chatbot_in_order(monkeypatch):
    lane = MagicMock()
    monkeypatch.setattr(tw, "_chatbot_lane", lane)
    fn = MagicMock()
    lane.on_lane_thread.return_value = False
    lane.busy.return_value = False
    tw._run_chatbot_in_order(fn)
    fn.assert_called_once()
    lane.busy.return_value = True
    tw._run_chatbot_in_order(fn)
    lane.submit.assert_called_once_with(fn)
    lane.on_lane_thread.return_value = True
    tw._run_chatbot_in_order(fn)
    assert fn.call_count == 2


def test_wait_for_chatbot_lane(monkeypatch):
    lane = MagicMock()
    monkeypatch.setattr(tw, "_chatbot_lane", lane)
    lane.on_lane_thread.return_value = False
    lane.busy.return_value = False
    asyncio.run(tw.wait_for_chatbot_lane())
    lane.submit.assert_not_called()
    lane.busy.return_value = True
    fut = concurrent.futures.Future()
    fut.set_result(None)
    lane.submit.return_value = fut
    asyncio.run(tw.wait_for_chatbot_lane())
    lane.submit.assert_called_once()


def test_dispatch_chatbot_event_response(monkeypatch):
    seen = []
    monkeypatch.setattr(chatbot, "dispatch_process_event_result", lambda r: seen.append(r))
    monkeypatch.setattr(tw, "_run_chatbot_in_order", lambda fn: fn())
    tw._dispatch_chatbot_event_response(("hi", ["twitch"]))
    assert seen == [("hi", ["twitch"])]


def test_chat_message_may_call_api(monkeypatch):
    mgr = MagicMock()
    mgr.message_may_call_api.return_value = True
    monkeypatch.setattr(tw, "get_chatbot_manager", lambda: mgr)
    assert tw._chat_message_may_call_api({"m": 1}) is True
    mgr.message_may_call_api.side_effect = RuntimeError("x")
    assert tw._chat_message_may_call_api({}) is False


def test_points_chatbot_data():
    alert = SimpleNamespace(username="U", alert_name="Hydrate", point_cost=None, message=None, timestamp=5)
    data = SimpleNamespace(event=SimpleNamespace(user_login="u", user_id=9))
    assert tw._points_chatbot_data(data, alert) == {
        "username": "U", "user_login": "u", "user_id": "9", "reward_title": "Hydrate", "reward_name": "Hydrate",
        "amount": 0, "message": "", "timestamp": 5, "source": "twitch"}


def test_notify_chatbot_event(monkeypatch):
    mgr = MagicMock()
    mgr.process_event.return_value = "result"
    dispatched = []
    monkeypatch.setattr(tw, "get_chatbot_manager", lambda: mgr)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", lambda r: dispatched.append(r))
    monkeypatch.setattr(tw, "_run_chatbot_in_order", lambda fn: fn())
    tw._notify_chatbot_event(tw.EventType.FOLLOW, {"username": "x"})
    mgr.process_event.assert_called_once_with(tw.EventType.FOLLOW, {"username": "x"})
    assert dispatched == ["result"]
    mgr.process_event.side_effect = RuntimeError("boom")
    tw._notify_chatbot_event_now("raw", {})  # swallowed


def test_overlay_alert_or_placeholder():
    alert = object()
    assert tw._overlay_alert_or_placeholder(alert, username="u", alert_type="Follow", timestamp=1) == (alert, True)
    ph, emit = tw._overlay_alert_or_placeholder(None, username=None, alert_type="Follow", timestamp=2)
    assert emit is False and ph.alert_type == "Follow" and ph.username == "" and ph.timestamp == 2
    assert ph.alert_id


# --------------------------------------------------------------------------- user lookup / tokens


@pytest.mark.parametrize(
    "name, anon, expected",
    [("bob", False, True), ("bob", True, False), ("", False, False), ("two words", False, False),
     ("Anonymous", False, False), ("anonymous gifter", False, False)],
)
def test_twitch_user_lookup_allowed(name, anon, expected):
    assert tw.twitch_user_lookup_allowed(name, anonymous=anon) is expected


def test_parse_helix_users_login():
    assert tw.parse_helix_users_login("x", {"login": " bob "}) == "bob"
    assert tw.parse_helix_users_login("x", {"login": ["a", "b"]}) == "a"
    assert tw.parse_helix_users_login("x", {"login": []}) is None
    assert tw.parse_helix_users_login("https://api.twitch.tv/helix/users?login=carl") == "carl"
    assert tw.parse_helix_users_login("https://api.twitch.tv/helix/users?id=1") is None
    assert tw.parse_helix_users_login("https://api.twitch.tv/helix/streams?login=x") is None
    assert tw.parse_helix_users_login("https://api.twitch.tv/helix/users?login=") is None


def test_parse_helix_users_login_bad_url(monkeypatch):
    monkeypatch.setattr(tw, "urlparse", MagicMock(side_effect=ValueError("x")))
    assert tw.parse_helix_users_login("::") is None


@pytest.mark.parametrize(
    "secs, expected",
    [(0, "Due now"), (-5, "Due now"), (5, "5s"), (65, "1m 5s"), (3725, "1h 2m 5s")],
)
def test_format_token_countdown(secs, expected):
    assert tw.format_token_countdown(timedelta(seconds=secs)) == expected


def test_build_token_timing_fields():
    empty = tw.build_token_timing_fields(token_expiry=None, has_auth_token=False)
    assert empty["token_refresh_countdown"] == "—" and empty["token_expired"] is False
    unknown = tw.build_token_timing_fields(token_expiry=None, has_auth_token=True)
    assert unknown["token_expires_at"] == "Unknown"
    future = tw.build_token_timing_fields(token_expiry=datetime.now() + timedelta(hours=2), has_auth_token=True)
    assert future["token_refresh_due"] is False and future["token_expired"] is False
    assert future["token_expires_countdown"].startswith("1h") or future["token_expires_countdown"].startswith("2h")
    past = tw.build_token_timing_fields(token_expiry=datetime.now() - timedelta(minutes=1), has_auth_token=True)
    assert past["token_refresh_due"] is True and past["token_expired"] is True
    assert past["token_refresh_countdown"] == "Due now"


# --------------------------------------------------------------------------- gift recipients


def test_gifter_keys_match():
    assert tw._gifter_keys_match("a", "1", None, None) is True
    assert tw._gifter_keys_match(None, None, "a", "1") is True
    assert tw._gifter_keys_match("x", "1", "y", "1") is True
    assert tw._gifter_keys_match("Bob", None, "bob", None) is True
    assert tw._gifter_keys_match("bob", "1", "carl", "2") is False


def test_prune_recent_gift_recipients(monkeypatch):
    tw._recent_gift_recipients.extend([{"ts": 1.0}, {"ts": 100.0}])
    tw._prune_recent_gift_recipients(now=10.0)
    assert tw._recent_gift_recipients == [{"ts": 100.0}]
    monkeypatch.setattr(tw.time, "time", lambda: 1000.0)
    tw._prune_recent_gift_recipients()
    assert tw._recent_gift_recipients == []


def test_take_and_peek_recent_recipient():
    now = time.time()
    tw._recent_gift_recipients.append({"recipient": " r1 ", "gifter_login": "g", "gifter_id": None, "ts": now})
    assert tw._peek_matching_recent_recipient("g") == "r1"
    assert tw._peek_matching_recent_recipient("other", "9") == ""
    assert tw._take_matching_recent_recipient("g") == "r1"
    assert tw._recent_gift_recipients == []
    # Two ambiguous matches: nothing taken.
    tw._recent_gift_recipients.extend([
        {"recipient": "a", "gifter_login": None, "gifter_id": None, "ts": now},
        {"recipient": "b", "gifter_login": None, "gifter_id": None, "ts": now},
    ])
    assert tw._take_matching_recent_recipient("g") == ""
    assert tw._peek_matching_recent_recipient("g") == ""
    assert len(tw._recent_gift_recipients) == 2


def test_attach_recipient_to_pending_gift():
    assert tw._attach_recipient_to_pending_gift("  ") is False
    alert = _gift_alert()
    task = MagicMock()
    task.done.return_value = False
    tw._pending_single_gifts.extend([
        {"alert": alert, "gifter_login": "g", "gifter_id": None, "resolved": False, "task": task},
        {"alert": _gift_alert(), "gifter_login": "g", "gifter_id": None, "resolved": True},
        {"alert": None, "gifter_login": "g", "gifter_id": None, "resolved": False},
        {"alert": _gift_alert(), "gifter_login": "other", "gifter_id": "5", "resolved": False},
    ])
    assert tw._attach_recipient_to_pending_gift("rec", gifter_login="g", gifter_id="1") is True
    assert alert.recipient == "rec" and tw._pending_single_gifts[0]["resolved"] is True
    task.cancel.assert_called_once()


def test_attach_recipient_ambiguous():
    tw._pending_single_gifts.extend([
        {"alert": _gift_alert(), "gifter_login": None, "gifter_id": None, "resolved": False},
        {"alert": _gift_alert(), "gifter_login": None, "gifter_id": None, "resolved": False, "task": None},
    ])
    assert tw._attach_recipient_to_pending_gift("rec") is False


def test_note_gift_recipient_attaches_and_finalizes(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    alert = _gift_alert()
    tw._pending_single_gifts.append({"alert": alert, "gifter_login": "g", "gifter_id": None, "resolved": False,
                                     "task": None})
    assert tw.note_gift_recipient("rec", gifter_login="g") is True
    assert emitted == [alert] and tw._pending_single_gifts == []


def test_note_gift_recipient_buffers_when_nothing_pending():
    assert tw.note_gift_recipient("") is False
    assert tw.note_gift_recipient("rec", gifter_login="g", gifter_id=5) is False
    entry = tw._recent_gift_recipients[0]
    assert entry["recipient"] == "rec" and entry["gifter_id"] == "5"


def test_finalize_keeps_unresolved(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    keep = {"alert": _gift_alert(), "resolved": False}
    done = {"alert": _gift_alert(alert_id="B"), "resolved": True}
    tw._pending_single_gifts.extend([keep, done])
    tw._finalize_resolved_pending_single_gifts()
    assert tw._pending_single_gifts == [keep] and emitted == [done["alert"]]


def test_duration_months_of():
    assert tw._duration_months_of(None) == 0
    assert tw._duration_months_of({"duration_months": "3"}) == 3
    assert tw._duration_months_of(SimpleNamespace(duration_months=-2)) == 0
    assert tw._duration_months_of(SimpleNamespace(duration_months="x")) == 0
    assert tw._duration_months_of(SimpleNamespace()) == 0


def test_show_sub_duration(monkeypatch):
    monkeypatch.setattr(activity_feed, "show_sub_duration", lambda *a: False)
    assert tw._show_sub_duration() is False
    monkeypatch.setattr(activity_feed, "show_sub_duration", MagicMock(side_effect=RuntimeError("x")))
    assert tw._show_sub_duration() is True


# --------------------------------------------------------------------------- gift durations


def test_note_gift_alert_gifter_and_prune(monkeypatch):
    tw._note_gift_alert_gifter("A1", gifter_login="g", gifter_id=7)
    assert tw._gift_alert_gifters["A1"]["gifter_id"] == "7"
    tw._note_gift_alert_gifter("A2", gifter_login="g", gifter_id=None)
    assert tw._gift_alert_gifters["A2"]["gifter_id"] is None
    tw._gift_duration_notices.append({"ts": 0})
    tw._gift_duration_watchers.append({"ts": 0})
    tw._prune_gift_durations(time.time() + 120)
    assert tw._gift_duration_notices == [] and tw._gift_duration_watchers == [] and tw._gift_alert_gifters == {}


def test_gift_duration_gifter_matches():
    m = tw._gift_duration_gifter_matches
    assert m({"anonymous": True}, {"anonymous": True}) is True
    assert m({"anonymous": True}, {"anonymous": False}) is False
    assert m({"gifter_id": "1"}, {"gifter_id": "1", "gifter_login": "x"}) is True
    assert m({"gifter_id": "1"}, {"gifter_id": "2"}) is False
    assert m({"gifter_login": "Bob"}, {"gifter_login": "bob", "gifter_id": "1"}) is True
    assert m({"gifter_login": None}, {"gifter_login": "bob"}) is False


def _notice(**kw):
    base = {"gifter_login": "g", "gifter_id": "1", "anonymous": False, "tier": 1, "duration": 3,
            "community_gift_id": None, "recipient": None, "ts": time.time()}
    base.update(kw)
    return base


def _target(**kw):
    base = {"gifter_login": "g", "gifter_id": "1", "anonymous": False, "tier": 1, "qty": 1, "recipient": None,
            "community_gift_id": None}
    base.update(kw)
    return base


def test_match_gift_duration_single():
    tw._gift_duration_notices.extend([_notice(recipient="other", duration=6), _notice(recipient="rec")])
    status, months, used = tw._match_gift_duration(_target(recipient="REC"))
    assert (status, months) == ("done", 3) and used[0]["recipient"] == "rec"


def test_match_gift_duration_single_never_takes_other_recipient():
    tw._gift_duration_notices.append(_notice(recipient="other"))
    assert tw._match_gift_duration(_target(recipient="rec"))[0] == "wait"


def test_match_gift_duration_single_ambiguous_and_same_duration():
    tw._gift_duration_notices.extend([_notice(duration=3), _notice(duration=6)])
    assert tw._match_gift_duration(_target())[0] == "wait"
    tw._gift_duration_notices[:] = [_notice(duration=3), _notice(duration=3, community_gift_id="c")]
    assert tw._match_gift_duration(_target())[:2] == ("done", 3)


def test_match_gift_duration_bomb():
    tw._gift_duration_notices.extend([_notice(community_gift_id="c1") for _ in range(2)])
    assert tw._match_gift_duration(_target(qty=3))[0] == "wait"
    tw._gift_duration_notices.append(_notice(community_gift_id="c1"))
    target = _target(qty=3)
    status, months, used = tw._match_gift_duration(target)
    assert (status, months, len(used)) == ("done", 3, 3) and target["community_gift_id"] == "c1"


def test_match_gift_duration_bomb_mixed_and_ambiguous_ids():
    tw._gift_duration_notices.extend([_notice(community_gift_id="c1", duration=3),
                                      _notice(community_gift_id="c1", duration=6)])
    assert tw._match_gift_duration(_target(qty=2, community_gift_id="c1"))[:2] == ("done", 0)
    tw._gift_duration_notices.append(_notice(community_gift_id="c2"))
    assert tw._match_gift_duration(_target(qty=2))[0] == "wait"


def test_drop_gift_notices():
    a, b = _notice(), _notice()
    tw._gift_duration_notices.extend([a, b])
    tw._drop_gift_notices([a])
    assert tw._gift_duration_notices == [b]


def test_gift_duration_at_emit_without_gifter():
    assert tw._gift_duration_at_emit(_gift_alert()) == 0
    assert tw._gift_duration_watchers == []


def test_gift_duration_at_emit_done_and_wait():
    alert = _gift_alert()
    tw._note_gift_alert_gifter("A1", gifter_login="g", gifter_id="1")
    tw._gift_duration_notices.append(_notice(duration=6))
    assert tw._gift_duration_at_emit(alert) == 6
    assert tw._gift_duration_notices == []
    # One-month notices don't count as multi-month.
    tw._note_gift_alert_gifter("A2", gifter_login="g", gifter_id="1")
    tw._gift_duration_notices.append(_notice(duration=1))
    assert tw._gift_duration_at_emit(_gift_alert(alert_id="A2")) == 0
    # Anonymous with no notices: waits.
    assert tw._gift_duration_at_emit(_gift_alert(alert_id="A3", anonymous=True)) == 0
    assert len(tw._gift_duration_watchers) == 1 and tw._gift_duration_watchers[0]["anonymous"] is True


def test_note_gift_duration_finishes_waiting_alert(monkeypatch):
    applied = []
    monkeypatch.setattr(tw, "_apply_late_gift_duration", lambda a, m: applied.append((a, m)))
    assert tw.note_gift_duration(duration_months=0) == 0
    alert = _gift_alert()
    tw._gift_duration_watchers.append(dict(_target(), alert=alert, ts=time.time()))
    tw._gift_duration_watchers.append(dict(_target(gifter_id="9", gifter_login="z"), alert=_gift_alert(),
                                           ts=time.time()))
    assert tw.note_gift_duration(gifter_login="g", gifter_id=1, tier=1, duration_months=6,
                                 community_gift_id=5, recipient=" r ") == 1
    assert applied == [(alert, 6)]
    assert len(tw._gift_duration_watchers) == 1


def test_note_gift_duration_anonymous_one_month_no_update(monkeypatch):
    applied = []
    monkeypatch.setattr(tw, "_apply_late_gift_duration", lambda a, m: applied.append((a, m)))
    tw._gift_duration_watchers.append(dict(_target(anonymous=True, gifter_login=None, gifter_id=None),
                                           alert=_gift_alert(), ts=time.time()))
    assert tw.note_gift_duration(anonymous=True, gifter_login="x", duration_months=1) == 0
    assert applied == [] and tw._gift_duration_watchers == []


def test_giftsub_feed_message(monkeypatch):
    seen = {}

    def msg(user, tier, qty, recipient, months, show_duration):
        seen.update(user=user, tier=tier, qty=qty, recipient=recipient, months=months, show=show_duration)
        return "MSG"

    monkeypatch.setattr(activity_feed_model, "giftsub_message", msg)
    monkeypatch.setattr(tw, "_show_sub_duration", lambda: False)
    alert = _gift_alert(username="", gift_qty=0, recipient=" ", duration_months=3)
    assert tw._giftsub_feed_message(alert) == "MSG"
    assert seen == {"user": "Anonymous", "tier": 1, "qty": 1, "recipient": None, "months": 3, "show": False}


def test_apply_late_gift_duration(pipeline, monkeypatch):
    monkeypatch.setattr(alertutils.alert_state_manager, "get_stored_alert_by_id", lambda aid: {"a": 1})
    monkeypatch.setattr(tw, "_giftsub_feed_message", lambda a: "M")
    alert = _gift_alert()
    tw._apply_late_gift_duration(alert, 6)
    assert alert.duration_months == 6
    assert pipeline.stored == [("A1", {"a": 1, "duration_months": 6})]
    assert pipeline.updates == [("A1", {"duration_months": 6, "message": "M"})]


def test_apply_late_gift_duration_errors_and_disabled(pipeline, monkeypatch):
    monkeypatch.setattr(alertutils.alert_state_manager, "get_stored_alert_by_id",
                        MagicMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(activity_feed, "update_alert_in_feed", MagicMock(side_effect=RuntimeError("ui")))
    monkeypatch.setattr(tw, "_giftsub_feed_message", lambda a: "M")
    tw._apply_late_gift_duration(_gift_alert(), 3)  # both errors logged
    monkeypatch.setattr(alertutils.alert_state_manager, "get_stored_alert_by_id", lambda aid: None)
    tw._apply_late_gift_duration(_gift_alert(), 3)
    assert pipeline.stored == []
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: False)
    alert = _gift_alert()
    tw._apply_late_gift_duration(alert, 4)
    assert alert.duration_months == 4


def test_emit_giftsub_alert_and_instant(pipeline, monkeypatch):
    monkeypatch.setattr(tw, "_gift_duration_at_emit", lambda a: 3)
    monkeypatch.setattr(tw, "_giftsub_feed_message", lambda a: "M")
    alert = _gift_alert(recipient=" rec ", anonymous=False)
    tw._emit_giftsub_alert_and_instant(alert)
    assert alert.duration_months == 3 and pipeline.queued == [alert]
    feed = pipeline.feed[0]
    assert feed["alert_type"] == "Giftsub" and feed["recipient"] == "rec" and feed["duration_months"] == 3
    assert feed["timestamp"] == "1000"
    data = pipeline.engine.instant_alert.call_args.args[0]
    assert data["type"] == "giftsub" and data["recipient"] == "rec" and data["duration_months"] == 3


def test_emit_giftsub_one_month_and_instant_error(pipeline, monkeypatch):
    monkeypatch.setattr(tw, "_gift_duration_at_emit", lambda a: 0)
    monkeypatch.setattr(tw, "_giftsub_feed_message", lambda a: "M")
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    tw._emit_giftsub_alert_and_instant(_gift_alert())
    assert pipeline.feed[0]["recipient"] is None and pipeline.feed[0]["duration_months"] is None


def test_clear_pending_single_gifts_for_tests():
    task = MagicMock()
    task.done.return_value = False
    tw._pending_single_gifts.extend([{"task": task}, {"task": None}])
    tw._recent_gift_recipients.append({})
    tw._gift_duration_notices.append({})
    tw._gift_duration_watchers.append({})
    tw._gift_alert_gifters["x"] = {}
    tw._clear_pending_single_gifts_for_tests()
    task.cancel.assert_called_once()
    assert not (tw._pending_single_gifts or tw._recent_gift_recipients or tw._gift_duration_notices
                or tw._gift_duration_watchers or tw._gift_alert_gifters)


def test_wait_and_emit_single_giftsub(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    monkeypatch.setattr(tw.asyncio, "sleep", AsyncMock())
    alert = _gift_alert()
    entry = {"alert": alert, "resolved": False}
    tw._pending_single_gifts.append(entry)
    asyncio.run(tw._wait_and_emit_single_giftsub(entry))
    assert emitted == [alert] and entry["resolved"] is True and tw._pending_single_gifts == []
    # Already finalized elsewhere: no second emit.
    asyncio.run(tw._wait_and_emit_single_giftsub(entry))
    assert emitted == [alert]


def test_wait_and_emit_cancelled_sleep_still_emits(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    monkeypatch.setattr(tw.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    alert = _gift_alert(recipient="r")
    entry = {"alert": alert}
    tw._pending_single_gifts.append(entry)
    asyncio.run(tw._wait_and_emit_single_giftsub(entry))
    assert emitted == [alert]


def test_begin_single_gift_wait_uses_recent_recipient(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    tw._recent_gift_recipients.append({"recipient": "rec", "gifter_login": "g", "gifter_id": None,
                                       "ts": time.time()})
    alert = _gift_alert()
    assert tw.begin_single_gift_wait(alert, gifter_login="g") is True
    assert alert.recipient == "rec" and emitted == [alert]


def test_begin_single_gift_wait_without_loop_emits_now(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    alert = _gift_alert()
    assert tw.begin_single_gift_wait(alert, gifter_login="g", gifter_id=5) is True
    assert emitted == [alert] and tw._pending_single_gifts == []


def test_begin_single_gift_wait_in_loop_then_recipient_arrives(monkeypatch):
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    monkeypatch.setattr(tw, "_SINGLE_GIFT_RECIPIENT_WAIT_SECONDS", 0.05)
    alert = _gift_alert()

    async def go():
        assert tw.begin_single_gift_wait(alert, gifter_login="g", gifter_id=5) is False
        assert len(tw._pending_single_gifts) == 1 and tw._pending_single_gifts[0]["gifter_id"] == "5"
        task = tw._pending_single_gifts[0]["task"]
        assert tw.note_gift_recipient("rec", gifter_login="g") is True
        await asyncio.sleep(0)
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    assert emitted == [alert] and alert.recipient == "rec"


# --------------------------------------------------------------------------- badges / registry


def test_badge_field_and_months():
    assert tw._badge_field({"set_id": "x"}, "set_id") == "x"
    assert tw._badge_field(SimpleNamespace(set_id="y"), "set_id") == "y"
    assert tw._parse_badge_months({"info": "12", "id": "6"}) == 12
    assert tw._parse_badge_months({"info": "", "id": "3006"}) == 3006
    assert tw._parse_badge_months({"info": "abc", "id": "x"}) is None
    assert tw._parse_badge_months({}) is None


def test_subscriber_months_from_badges():
    assert tw._subscriber_months_from_badges(None) is None
    assert tw._subscriber_months_from_badges([{"set_id": "vip"}, {"set_id": "subscriber", "info": "1"}]) is None
    assert tw._subscriber_months_from_badges([{"set_id": "subscriber", "info": "9"}]) == 9


@pytest.fixture
def registry(monkeypatch):
    reg = MagicMock()
    reg.has_pending.return_value = False
    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", lambda: reg)
    return reg


def test_harvest_badges_variants(registry):
    tw._harvest_chat_badges_into_subscriber_registry(None)
    registry.record.assert_not_called()
    tw._harvest_chat_badges_into_subscriber_registry([{"set_id": "vip"}], user_id="1")
    registry.record.assert_not_called()
    tw._harvest_chat_badges_into_subscriber_registry(
        [{"set_id": "founder"}, {"set_id": "subscriber", "info": "3"}], user_id="1", user_login="a")
    registry.cancel_pending.assert_called_with("1")
    assert registry.record.call_args.kwargs == {"user_id": "1", "user_login": "a", "source": "chat.badge.founder",
                                                "cumulative_months": 3}
    tw._harvest_chat_badges_into_subscriber_registry(
        [{"set_id": "subscriber", "info": "2"}, {"set_id": "subscriber", "info": "5"}], user_id="2")
    assert registry.record.call_args.kwargs["cumulative_months"] == 5
    assert registry.record.call_args.kwargs["source"] == "chat.badge.subscriber"


def test_harvest_badges_unparseable_month_counts_as_one(registry):
    tw._harvest_chat_badges_into_subscriber_registry([{"set_id": "subscriber", "info": "x", "id": "y"}],
                                                     user_id="3")
    assert registry.record.call_args.kwargs["cumulative_months"] == 1
    registry.record.reset_mock()
    registry.has_pending.return_value = True
    tw._harvest_chat_badges_into_subscriber_registry([{"set_id": "subscriber", "info": "1"}], user_id="3")
    registry.record.assert_not_called()


def test_record_known_subscriber_swallows_errors(registry):
    registry.record.side_effect = RuntimeError("db")
    tw._record_known_subscriber(user_id="1")  # logged


# --------------------------------------------------------------------------- connector events


def test_emit_connector_new_sub_and_resub(monkeypatch):
    mgr = MagicMock()
    mgr.add_event = AsyncMock()
    monkeypatch.setattr(connector_manager, "get_manager", lambda: mgr)
    asyncio.run(tw._emit_connector_new_sub(tier=1, username="u", message=None, user_id="1", is_gift=1))
    ev = mgr.add_event.await_args.args[0]
    assert ev["event_type"] == "twitch_sub" and ev["username"] == "u" and ev["message"] == ""
    asyncio.run(tw._emit_connector_resub(tier=2, username="u", months=5, streak_months=None, user_id="1"))
    assert mgr.add_event.await_count == 2
    resub = mgr.add_event.await_args.args[0]
    assert resub["username"] == "u" and resub["tier"] == 2


def test_emit_connector_errors_are_logged(monkeypatch):
    monkeypatch.setattr(connector_manager, "get_manager", MagicMock(side_effect=RuntimeError("x")))
    asyncio.run(tw._emit_connector_new_sub(tier=1, username="u"))
    asyncio.run(tw._emit_connector_resub(tier=1, username="u"))

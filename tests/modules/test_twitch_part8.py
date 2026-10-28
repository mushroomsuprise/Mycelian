# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 8: subscription handlers (sub, resub, gift recipient,
chat-notice new subs, modiversaries, subscription end, gift subs)."""

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

from modules import (  # noqa: E402
    alert_processor,
    alertutils,
    chatbot,
    statistics_manager,
    twitch as tw,
    twitch_subscriber_registry,
)
from modules.uiwindows import activity_feed  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("_pending_single_gifts", "_recent_gift_recipients", "_gift_duration_notices",
                 "_gift_duration_watchers"):
        monkeypatch.setattr(tw, name, [])
    monkeypatch.setattr(tw, "_gift_alert_gifters", {})
    monkeypatch.setattr(tw, "_alert_user_message_cache", {})


@pytest.fixture
def pipe(monkeypatch):
    rec = SimpleNamespace(stored=[], queued=[], feed=[], dispatched=[], enabled=True)
    rec.engine = MagicMock()
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=rec.engine))
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: rec.enabled)
    monkeypatch.setattr(tw, "_show_sub_duration", lambda: True)
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda aid, payload: rec.stored.append((aid, payload)))
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: rec.queued.append(a))
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda *a, **kw: rec.feed.append(kw))
    rec.stats = MagicMock()
    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: rec.stats)
    rec.bot = MagicMock()
    rec.bot.process_event.return_value = None
    monkeypatch.setattr(tw, "get_chatbot_manager", lambda: rec.bot)
    monkeypatch.setattr(tw, "_run_chatbot_in_order", lambda fn: fn())
    monkeypatch.setattr(chatbot, "dispatch_process_event_result", lambda r: rec.dispatched.append(r))
    rec.registry = MagicMock()
    rec.registry.was_new_sub_alerted_recently.return_value = False
    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", lambda: rec.registry)
    rec.new_sub = AsyncMock()
    rec.resub = AsyncMock()
    monkeypatch.setattr(tw, "_emit_connector_new_sub", rec.new_sub)
    monkeypatch.setattr(tw, "_emit_connector_resub", rec.resub)
    return rec


@pytest.fixture
def api():
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42")
    return a


def _alert(name="SubAlert"):
    a = alertutils.AlertObj()
    a.alert_name = name
    return a


def _sub_event(months=1, message="hi", tier="1000", **kw):
    ev = SimpleNamespace(user_name="Sam", user_id="9", user_login="sam", tier=tier, cumulative_months=months,
                         streak_months=None, duration_months=0,
                         message=SimpleNamespace(text=message, emotes=[{"begin": 0, "end": 1, "id": "5"}])
                         if message is not None else None)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


# --------------------------------------------------------------------------- on_sub


def test_on_sub_new_with_alert(api, pipe, monkeypatch):
    alert = _alert()
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: alert)
    pipe.bot.process_event.return_value = ("ty",)
    run(api.on_sub(_sub_event(duration_months=3, tier="2000")))
    pipe.registry.cancel_pending.assert_called_with("9")
    pipe.registry.mark_new_sub_alerted.assert_called_once_with("9")
    assert pipe.queued == [alert] and alert.tier == 2 and alert.duration_months == 3
    assert alert.emotes == [{"begin": 0, "end": 1, "id": "5"}]
    assert tw.is_alert_user_message("9", "hi")
    pipe.stats.increment_new_subs.assert_called_once_with(username="Sam")
    assert pipe.engine.instant_alert.call_args.args[0]["type"] == "sub"
    assert pipe.bot.process_event.call_args.args[0] == tw.EventType.SUBSCRIPTION
    assert pipe.dispatched == [("ty",)]
    assert pipe.feed[0]["alert_type"] == "Sub" and pipe.feed[0]["duration_months"] == 3
    pipe.new_sub.assert_awaited_once()
    assert pipe.new_sub.await_args.kwargs == {"tier": 2, "username": "Sam", "message": "hi", "user_id": "9",
                                              "is_gift": False}


def test_on_sub_without_alert_message_or_ids(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: None)
    pipe.stats.increment_new_subs.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_sub(_sub_event(months=None, message=None, tier="", user_id=None, user_login=None)))
    assert pipe.queued == [] and len(pipe.stored) == 1
    pipe.engine.instant_alert.assert_not_called()
    assert pipe.feed[0]["duration_months"] is None and pipe.feed[0]["tier"] == 1


def test_on_sub_errors_in_instant_and_chatbot(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: _alert())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_sub(_sub_event()))
    assert len(pipe.feed) == 1


def test_on_sub_duplicate_new_sub_skipped(api, pipe):
    pipe.registry.was_new_sub_alerted_recently.return_value = True
    run(api.on_sub(_sub_event()))
    assert pipe.queued == [] and pipe.feed == []


def test_on_sub_resub_delegates_or_skips(api, pipe, monkeypatch):
    resub = AsyncMock()
    monkeypatch.setattr(api, "on_resub", resub)
    run(api.on_sub(_sub_event(months=4)))
    resub.assert_awaited_once()
    pipe.registry.was_new_sub_alerted_recently.return_value = True
    run(api.on_sub(_sub_event(months=4)))
    resub.assert_awaited_once()


# --------------------------------------------------------------------------- on_resub


def test_on_resub(api, pipe, monkeypatch):
    alert = _alert("Resub")
    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: alert)
    pipe.bot.process_event.return_value = ("ty",)
    run(api.on_resub(_sub_event(months=6, streak_months=4, duration_months=3, tier="3000")))
    assert pipe.queued == [alert]
    assert (alert.resub_month, alert.tier, alert.months_prepaid, alert.duration_months) == (6, 3, 3, 3)
    pipe.stats.increment_resubs.assert_called_once_with(username="Sam")
    data = pipe.engine.instant_alert.call_args.args[0]
    assert data["type"] == "resub" and data["cumulative_months"] == 6
    resub_data = pipe.bot.process_event.call_args.args[1]
    assert resub_data["streak_months"] == 4
    assert pipe.feed[0]["alert_type"] == "Resub" and pipe.feed[0]["duration_months"] == 3
    assert pipe.resub.await_args.kwargs["months"] == 6 and pipe.resub.await_args.kwargs["streak_months"] == 4


def test_on_resub_placeholder_and_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: None)
    pipe.stats.increment_resubs.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_resub(_sub_event(months=None, message=None, tier="")))
    assert pipe.queued == [] and pipe.feed[0]["duration_months"] is None
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    monkeypatch.setattr(alertutils, "fetch_resub_alert", lambda m: _alert())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_resub(_sub_event(months=2)))
    assert len(pipe.feed) == 2


# --------------------------------------------------------------------------- on_new_sub


def test_on_new_sub_ignores_non_gift(api, pipe, monkeypatch):
    note = MagicMock()
    monkeypatch.setattr(tw, "note_gift_recipient", note)
    run(api.on_new_sub(SimpleNamespace(event=SimpleNamespace(user_name="x", user_id="1", is_gift=False))))
    note.assert_not_called()
    pipe.registry.record.assert_not_called()


def test_on_new_sub_gift_records_recipient(api, pipe, monkeypatch):
    note = MagicMock()
    monkeypatch.setattr(tw, "note_gift_recipient", note)
    ev = SimpleNamespace(user_name="Rec", user_id="3", user_login="rec", is_gift=True, gifter_user_id=None,
                         gifter_id=11, gifter_user_login=None, gifter_login="giver")
    run(api.on_new_sub(SimpleNamespace(event=ev)))
    assert pipe.registry.record.call_args.kwargs["source"] == "channel.subscribe.gift"
    note.assert_called_once_with("Rec", gifter_login="giver", gifter_id="11")
    ev2 = SimpleNamespace(user_name=None, user_id=None, user_login=None, is_gift=True)
    run(api.on_new_sub(SimpleNamespace(event=ev2)))
    assert note.call_args.args == ("",) and note.call_args.kwargs["gifter_id"] is None


# --------------------------------------------------------------------------- chat notice new sub


@pytest.mark.parametrize(
    "sub, tier",
    [(None, 1), ({"sub_tier": "2000", "duration_months": 6}, 2), ({"tier": "3000"}, 3),
     (SimpleNamespace(sub_tier=None, tier="2000", duration_months=None), 2)],
)
def test_emit_new_sub_from_chat_notice(api, pipe, monkeypatch, sub, tier):
    emit = AsyncMock()
    monkeypatch.setattr(api, "_emit_verified_new_sub", emit)
    ev = SimpleNamespace(chatter_user_name=None, chatter_user_id=None, chatter_user_login=None, sub=sub,
                         message=SimpleNamespace(text="m", emotes=None))
    run(api._emit_new_sub_from_chat_notice(ev))
    kw = emit.await_args.kwargs
    assert kw["username"] == "Someone" and kw["user_id"] is None and kw["tier"] == tier and kw["user_msg"] == "m"
    assert kw["duration_months"] == (6 if isinstance(sub, dict) and sub.get("duration_months") else 0)


# --------------------------------------------------------------------------- modiversary


def test_modiversary_feed_and_chat_line(api, pipe):
    ev = SimpleNamespace(chatter_user_name="Mod", modiversary={"months": 12}, system_message="", message=None,
                         message_id="x1")
    api._emit_modiversary_notice(ev)
    assert pipe.feed[0]["alert_type"] == "Modiversary" and pipe.feed[0]["always_broadcast_html"] is True
    pipe.enabled = False
    ev2 = SimpleNamespace(chatter_user_name=None, modiversary=None, system_message="modded for 3 months",
                          message=None, message_id="x2")
    api._emit_modiversary_notice(ev2)
    payload = pipe.engine.new_message.call_args.args[0]
    assert payload["event_kind"] == "modiversary" and payload["id"] == "x2"


def test_modiversary_without_months_skipped(api, pipe):
    api._emit_modiversary_notice(SimpleNamespace(chatter_user_name="M", modiversary=None, system_message="",
                                                 message=None))
    assert pipe.feed == [] and not pipe.engine.new_message.called


# --------------------------------------------------------------------------- verified new sub


def test_emit_verified_new_sub(api, pipe, monkeypatch):
    alert = _alert()
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: alert)
    pipe.bot.process_event.return_value = ("ty",)
    run(api._emit_verified_new_sub(username="New", user_id="5", user_login=None, tier=2, tier_raw="2000",
                                   user_msg="hey", emotes=[1], duration_months=3))
    pipe.registry.mark_new_sub_alerted.assert_called_once_with("5")
    assert pipe.registry.record.call_args.kwargs["user_login"] == "New"
    assert pipe.queued == [alert] and alert.duration_months == 3 and alert.emotes == [1]
    assert pipe.bot.process_event.call_args.args[1]["user_login"] == "new"
    assert pipe.dispatched == [("ty",)]
    assert pipe.feed[0]["duration_months"] == 3
    assert pipe.new_sub.await_args.kwargs["tier"] == "2000"


def test_emit_verified_new_sub_default_alert_and_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_sub_alert", lambda m: None)
    pipe.stats.increment_new_subs.side_effect = RuntimeError("db")
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api._emit_verified_new_sub(username="New", duration_months=None))
    assert len(pipe.queued) == 1 and pipe.queued[0].alert_type == "sub"
    assert pipe.new_sub.await_args.kwargs["tier"] == 1
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api._emit_verified_new_sub(username="New2"))
    assert len(pipe.feed) == 2


def test_emit_verified_new_sub_dedup(api, pipe):
    pipe.registry.was_new_sub_alerted_recently.return_value = True
    run(api._emit_verified_new_sub(username="New", user_id="5"))
    assert pipe.queued == []


# --------------------------------------------------------------------------- subscription end


def test_on_subscription_end(api, pipe, monkeypatch):
    run(api.on_subscription_end(SimpleNamespace(event=SimpleNamespace(user_id=4, user_login=None, user_name="Lap"))))
    kw = pipe.registry.record.call_args.kwargs
    assert kw == {"user_id": "4", "user_login": "Lap", "source": "channel.subscription.end",
                  "cumulative_months": None}
    pipe.registry.cancel_pending.assert_called_once_with("4")
    pipe.registry.cancel_pending.side_effect = RuntimeError("x")
    run(api.on_subscription_end(SimpleNamespace(event=SimpleNamespace(user_id=None))))


# --------------------------------------------------------------------------- gift subs


def _gift_event(total=5, anonymous=False, **kw):
    ev = SimpleNamespace(user_name="Giver", user_login="giver", user_id="8", total=total, tier="1000",
                         is_anonymous=anonymous, cumulative_total=20)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


def test_on_sub_gift_bomb_emits_now(api, pipe, monkeypatch):
    alert = _alert("Bomb")
    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda n: alert)
    emitted = []
    monkeypatch.setattr(tw, "_emit_giftsub_alert_and_instant", lambda a: emitted.append(a))
    pipe.bot.process_event.return_value = ("thx",)
    run(api.on_sub_gift(_gift_event(5)))
    assert emitted == [alert] and alert.gift_qty == 5 and alert.username == "Giver"
    assert tw._gift_alert_gifters[alert.alert_id]["gifter_id"] == "8"
    data = pipe.bot.process_event.call_args.args[1]
    assert data["total_gifts"] == 20 and data["recipient"] == "" and data["user_login"] == "giver"
    assert pipe.dispatched == [("thx",)]
    pipe.stats.increment_gift_subs.assert_called_once_with(gift_quantity=5, username="Giver", alert_name="Bomb")


def test_on_sub_gift_single_waits_for_recipient(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda n: None)
    waits = []
    monkeypatch.setattr(tw, "begin_single_gift_wait", lambda a, **kw: waits.append((a, kw)))
    tw._recent_gift_recipients.append({"recipient": "Lucky", "gifter_login": "giver", "gifter_id": "8",
                                       "ts": tw.time.time()})
    run(api.on_sub_gift(_gift_event(1, cumulative_total=None)))
    alert, kw = waits[0]
    assert alert.alert_type == "giftsub" and kw == {"gifter_login": "giver", "gifter_id": "8"}
    data = pipe.bot.process_event.call_args.args[1]
    assert data["recipient"] == "Lucky" and data["total_gifts"] == ""


def test_on_sub_gift_anonymous_and_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_giftsub_alert", lambda n: _alert())
    waits = []
    monkeypatch.setattr(tw, "begin_single_gift_wait", lambda a, **kw: waits.append((a, kw)))
    pipe.stats.increment_gift_subs.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_sub_gift(_gift_event(1, anonymous=True, user_login=None, user_id=None)))
    alert, kw = waits[0]
    assert alert.username == "Anonymous" and alert.anonymous is True
    assert kw == {"gifter_login": None, "gifter_id": None}
    data = pipe.bot.process_event.call_args.args[1]
    assert data["user_login"] == "" and data["user_id"] == "" and data["is_anonymous"] is True
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_sub_gift(_gift_event(1, user_id=None)))
    assert waits[1][1]["gifter_id"] is None

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 7: EventSub chat handlers (chat messages, chat
notifications incl. watch streaks / gift harvesting / announcements), moderation,
channel updates / category seeding and follows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import concurrent.futures  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import (  # noqa: E402
    alert_processor,
    alertutils,
    chatbot,
    dataobjects,
    giveaway_manager,
    notification_engine,
    poll_manager,
    statistics_manager,
    twitch as tw,
    twitch_subscriber_registry,
)
from modules.uiwindows import activity_feed  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(tw, "_alert_user_message_cache", {})
    monkeypatch.setattr(tw, "_cheer_bits_cache", {})


@pytest.fixture
def pipe(monkeypatch):
    """Fake every collaborator an EventSub handler touches."""
    rec = SimpleNamespace(stored=[], queued=[], feed=[], dispatched=[], delivered=[], enabled=True)
    rec.engine = MagicMock()
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=rec.engine))
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: rec.enabled)
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda aid, payload: rec.stored.append((aid, payload)))
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: rec.queued.append(a))
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda *a, **kw: rec.feed.append(kw))
    rec.stats = MagicMock()
    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: rec.stats)
    rec.bot = MagicMock()
    rec.bot.process_greeting.return_value = None
    rec.bot.process_chat_message.return_value = None
    rec.bot.process_event.return_value = None
    rec.bot.message_may_call_api.return_value = False
    monkeypatch.setattr(tw, "get_chatbot_manager", lambda: rec.bot)
    monkeypatch.setattr(tw, "_run_chatbot_in_order", lambda fn: fn())
    monkeypatch.setattr(chatbot, "dispatch_process_event_result", lambda r: rec.dispatched.append(r))
    monkeypatch.setattr(chatbot, "is_own_chat_message", lambda uid, msg: False)
    monkeypatch.setattr(chatbot, "send_chatbot_message", MagicMock())
    monkeypatch.setattr(chatbot, "deliver_chatbot_output", lambda *a, **kw: rec.delivered.append((a, kw)))
    rec.giveaway = MagicMock()
    rec.giveaway.try_register_entry.return_value = False
    monkeypatch.setattr(giveaway_manager, "get_giveaway_manager", lambda: rec.giveaway)
    rec.polls = MagicMock()
    rec.polls.try_moderator_start.return_value = False
    rec.polls.try_register_vote.return_value = False
    monkeypatch.setattr(poll_manager, "get_poll_manager", lambda: rec.polls)
    rec.registry = MagicMock()
    rec.registry.has_pending.return_value = False
    monkeypatch.setattr(twitch_subscriber_registry, "get_subscriber_registry", lambda: rec.registry)
    lane = MagicMock()
    lane.busy.return_value = False
    lane.on_lane_thread.return_value = False
    monkeypatch.setattr(tw, "_chatbot_lane", lane)
    rec.lane = lane
    return rec


@pytest.fixture
def api():
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42", display_name="Me")
    a.user_id = "42"
    return a


def _frag(text, emote_id=None, type_="text"):
    return SimpleNamespace(text=text, type="emote" if emote_id else type_,
                           emote=SimpleNamespace(id=emote_id, name=text) if emote_id else None)


def _chat(message="hello", fragments=None, badges=None, **kw):
    ev = SimpleNamespace(
        chatter_user_name="Bob", chatter_user_id="7", chatter_user_login="bob", message_id="m1",
        color="#fff", badges=badges, message=SimpleNamespace(text=message, fragments=fragments),
        message_type="text", reply=None)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


# --------------------------------------------------------------------------- on_chat_message


def test_chat_message_builds_payload_and_runs_chatbot(api, pipe):
    frags = [_frag("hi "), _frag("Kappa", "25"), _frag(" "), _frag("Kappa", "25")]
    badges = [SimpleNamespace(set_id="subscriber", id="12", info="12")]
    reply = SimpleNamespace(parent_message_id="p", parent_message_body="b", parent_user_name="U",
                            parent_user_login="u", thread_message_id="t")
    tw.register_alert_user_message("7", "hi Kappa Kappa")
    run(api.on_chat_message(_chat("hi Kappa Kappa", frags, badges, reply=reply, color=None)))
    msg = pipe.engine.new_message.call_args.args[0]
    assert msg["emotes"] == "25:3-7,9-13"
    assert msg["badges"] == "subscriber/12" and msg["color"] == ""
    assert msg["reply"]["parent_message_id"] == "p" and msg["is_alert_user_message"] is True
    assert msg["fragments"][1]["type"] == "emote"
    pipe.stats.increment_twitch_messages.assert_called_once_with(username="Bob")
    pipe.registry.cancel_pending.assert_called_with("7")
    pipe.bot.process_greeting.assert_called_once_with("7", "Bob")
    pipe.bot.process_chat_message.assert_called_once()
    pipe.giveaway.try_register_entry.assert_called_once()
    pipe.polls.try_register_vote.assert_called_once()
    assert pipe.bot.process_event.call_args.args[0] == tw.EventType.CHAT_MESSAGE


def test_chat_message_color_non_string_and_fragment_error(api, pipe, monkeypatch):
    monkeypatch.setattr(tw, "serialize_chat_message_fragments", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_chat_message(_chat("x", [_frag("x")], color=123)))
    msg = pipe.engine.new_message.call_args.args[0]
    assert msg["color"] == "123" and msg["fragments"] is None and msg["emotes"] is None


def test_chat_message_slash_command_hidden_and_no_engine(api, pipe, monkeypatch):
    run(api.on_chat_message(_chat("/ban someone")))
    pipe.engine.new_message.assert_not_called()
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    run(api.on_chat_message(_chat("hi")))  # warning only


def test_chat_message_overlay_and_stats_errors(api, pipe):
    pipe.engine.new_message.side_effect = RuntimeError("socket")
    pipe.stats.increment_twitch_messages.side_effect = RuntimeError("db")
    pipe.registry.cancel_pending.side_effect = RuntimeError("reg")
    run(api.on_chat_message(_chat("hi", badges=[SimpleNamespace(set_id="founder", id="0", info=None)])))
    pipe.bot.process_chat_message.assert_called_once()


def test_chat_message_badge_harvest_error(api, pipe, monkeypatch):
    monkeypatch.setattr(tw, "_harvest_chat_badges_into_subscriber_registry", MagicMock(side_effect=RuntimeError()))
    run(api.on_chat_message(_chat("hi", badges=[SimpleNamespace(set_id="vip", id="1")])))


def test_chat_message_greeting_sent(api, pipe):
    pipe.bot.process_greeting.return_value = "Welcome!"
    run(api.on_chat_message(_chat()))
    chatbot.send_chatbot_message.assert_called_once_with("Welcome!")
    chatbot.send_chatbot_message.side_effect = RuntimeError("x")
    run(api.on_chat_message(_chat()))  # send error logged
    pipe.bot.process_greeting.side_effect = RuntimeError("greet")
    run(api.on_chat_message(_chat()))  # greeting error logged, rest continues
    assert pipe.bot.process_chat_message.call_count == 3


@pytest.mark.parametrize(
    "response, targets, mode",
    [
        (("pong", "ping"), ["twitch"], "chat"),
        (("pong", "ping", ["discord"]), ["discord"], "chat"),
        (("pong", "ping", ["twitch"], ["c1"]), ["twitch"], "chat"),
        (("pong", "ping", ["twitch"], ["c1"], "banner"), ["twitch"], "banner"),
    ],
)
def test_chat_message_command_responses(api, pipe, response, targets, mode):
    pipe.bot.process_chat_message.return_value = response
    run(api.on_chat_message(_chat("!ping")))
    (msg, tgt), kw = pipe.delivered[0]
    assert msg == "pong" and tgt == targets and kw["output_mode"] == mode
    pipe.giveaway.try_register_entry.assert_not_called()


def test_chat_message_silent_reply_and_send_error(api, pipe, monkeypatch):
    pipe.bot.process_chat_message.return_value = ("  ", "cmd")
    run(api.on_chat_message(_chat("!cmd")))
    assert pipe.delivered == []
    pipe.bot.process_chat_message.return_value = ("x", "cmd")
    monkeypatch.setattr(chatbot, "deliver_chatbot_output", MagicMock(side_effect=RuntimeError("send")))
    run(api.on_chat_message(_chat("!cmd")))


def test_chat_message_giveaway_poll_and_event(api, pipe):
    pipe.giveaway.try_register_entry.return_value = True
    pipe.polls.try_moderator_start.return_value = True
    pipe.polls.try_register_vote.return_value = True
    pipe.bot.process_event.return_value = ("evt", ["twitch"])
    run(api.on_chat_message(_chat("!join")))
    assert pipe.dispatched == [("evt", ["twitch"])]


def test_chat_message_giveaway_poll_event_errors(api, pipe, monkeypatch):
    pipe.giveaway.try_register_entry.side_effect = RuntimeError("g")
    pipe.polls.try_moderator_start.side_effect = RuntimeError("p")
    pipe.bot.process_event.return_value = ("evt",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_chat_message(_chat()))
    pipe.bot.process_event.side_effect = RuntimeError("e")
    run(api.on_chat_message(_chat()))


def test_chat_message_chatbot_outer_error(api, pipe):
    pipe.bot.process_chat_message.side_effect = RuntimeError("bot down")
    run(api.on_chat_message(_chat()))
    pipe.bot.process_event.assert_not_called()


def test_chat_message_own_message_skips_chatbot(api, pipe, monkeypatch):
    monkeypatch.setattr(chatbot, "is_own_chat_message", lambda uid, msg: True)
    run(api.on_chat_message(_chat()))
    pipe.bot.process_chat_message.assert_not_called()
    monkeypatch.setattr(chatbot, "is_own_chat_message", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_chat_message(_chat()))
    pipe.bot.process_chat_message.assert_called_once()


def test_chat_message_api_command_runs_on_lane(api, pipe):
    pipe.bot.message_may_call_api.return_value = True

    def submit(fn):
        fut = concurrent.futures.Future()
        fn()
        fut.set_result(None)
        return fut

    pipe.lane.submit.side_effect = submit
    run(api.on_chat_message(_chat("!followage")))
    pipe.lane.submit.assert_called_once()
    pipe.bot.process_chat_message.assert_called_once()


def test_chat_message_lane_error_logged(api, pipe):
    pipe.lane.busy.return_value = True
    fut = concurrent.futures.Future()
    fut.set_exception(RuntimeError("lane"))
    pipe.lane.submit.return_value = fut
    run(api.on_chat_message(_chat()))


# --------------------------------------------------------------------------- on_chat_notification


def _notice(notice_type, **kw):
    ev = SimpleNamespace(notice_type=notice_type, chatter_user_id="7", chatter_user_login="bob",
                         chatter_user_name="Bob", chatter_is_anonymous=False, message=None, system_message="",
                         message_id="n1")
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


def test_notification_resub_harvest(api, pipe):
    run(api.on_chat_notification(_notice("resub", resub=SimpleNamespace(cumulative_months=5))))
    pipe.registry.cancel_pending.assert_called_once_with("7")
    kw = pipe.registry.record.call_args.kwargs
    assert kw["source"] == "chat.notification.resub" and kw["cumulative_months"] == 5


def test_notification_sub_gift_harvest_notes_duration_and_recipient(api, pipe, monkeypatch):
    notes, recips = [], []
    monkeypatch.setattr(tw, "note_gift_duration", lambda **kw: notes.append(kw))
    monkeypatch.setattr(tw, "note_gift_recipient", lambda r, **kw: recips.append((r, kw)))
    gift = SimpleNamespace(recipient_user_id="9", recipient_user_login="carl", recipient_user_name="Carl",
                           sub_tier="2000", duration_months=3, community_gift_id=None)
    run(api.on_chat_notification(_notice("sub_gift", sub_gift=gift)))
    sources = [c.kwargs["source"] for c in pipe.registry.record.call_args_list]
    assert "chat.notification.sub_gift.recipient" in sources and "chat.notification.sub_gift" in sources
    assert notes[0]["tier"] == 2 and notes[0]["duration_months"] == 3 and notes[0]["recipient"] == "Carl"
    assert recips == [("Carl", {"gifter_login": "bob", "gifter_id": "7"})]


def test_notification_shared_gift_without_recipient_and_no_chatter(api, pipe, monkeypatch):
    recips = []
    monkeypatch.setattr(tw, "note_gift_recipient", lambda r, **kw: recips.append(r))
    gift = SimpleNamespace(recipient_user_id=None, recipient_user_login=None, recipient_user_name=None)
    run(api.on_chat_notification(_notice("shared_chat_sub_gift", shared_chat_sub_gift=gift, chatter_user_id=None,
                                         chatter_user_login=None, chatter_user_name=None)))
    pipe.registry.record.assert_not_called()
    assert recips == []


def test_notification_harvest_error_swallowed(api, pipe, monkeypatch):
    monkeypatch.setattr(tw, "_record_known_subscriber", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_chat_notification(_notice("community_sub_gift")))


def test_notification_sub_emits_new_sub(api, pipe, monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(api, "_emit_new_sub_from_chat_notice", emit)
    run(api.on_chat_notification(_notice("sub")))
    emit.assert_awaited_once()


def test_notification_modiversary(api, pipe, monkeypatch):
    emit = MagicMock()
    monkeypatch.setattr(api, "_emit_modiversary_notice", emit)
    run(api.on_chat_notification(_notice("modiversary")))
    emit.assert_called_once()


def test_notification_announcement(api, pipe, monkeypatch):
    emit = MagicMock()
    monkeypatch.setattr(tw, "emit_announcement_banner_from_notice", emit)
    run(api.on_chat_notification(_notice("announcement")))
    emit.assert_called_once()
    emit.side_effect = RuntimeError("x")
    run(api.on_chat_notification(_notice("announcement")))


def test_notification_other_type_ignored(api, pipe):
    run(api.on_chat_notification(_notice("raid")))
    pipe.engine.new_message.assert_not_called() and pipe.feed == []


def test_watch_streak_without_count_skipped(api, pipe):
    run(api.on_chat_notification(_notice("watch_streak", watch_streak=None)))
    assert pipe.feed == [] and pipe.queued == []


def test_watch_streak_with_alert(api, pipe, monkeypatch):
    alert = alertutils.AlertObj()
    alert.alert_name = "Streaky"
    monkeypatch.setattr(alertutils, "fetch_streak_alert", lambda n: alert)
    ev = _notice("watch_streak", watch_streak={"streak_count": 5, "channel_points_awarded": 350},
                 message={"text": "yay"})
    run(api.on_chat_notification(ev))
    assert pipe.queued == [alert] and alert.streak_count == 5 and alert.message == "yay"
    data = pipe.engine.instant_alert.call_args.args[0]
    assert data["type"] == "streak" and data["channel_points_awarded"] == 350
    assert pipe.feed[0]["alert_type"] == "Streak" and pipe.feed[0]["streak_count"] == 5
    pipe.stats.increment_watch_streak_alerts.assert_called_once_with(streak_count=5, username="Bob",
                                                                     alert_name="Streaky")


def test_watch_streak_from_system_message_no_alert(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_streak_alert", lambda n: None)
    pipe.stats.increment_watch_streak_alerts.side_effect = RuntimeError("db")
    ev = _notice("watch_streak", watch_streak=None, system_message="Bob watched 7 consecutive streams")
    run(api.on_chat_notification(ev))
    aid, stored = pipe.stored[0]
    assert stored["streak_count"] == 7 and stored["message"] == "Bob watched 7 consecutive streams"
    assert pipe.feed[0]["user_message"] == "Bob watched 7 consecutive streams"


def test_watch_streak_alerts_disabled_sends_chat_line(api, pipe, monkeypatch):
    pipe.enabled = False
    monkeypatch.setattr(alertutils, "fetch_streak_alert", lambda n: None)
    run(api.on_chat_notification(_notice("watch_streak", watch_streak={"streak_count": 3})))
    assert pipe.stored == [] and pipe.feed == []
    payload = pipe.engine.new_message.call_args.args[0]
    assert payload["id"] == "n1" and payload["type"] == "event"


def test_watch_streak_instant_alert_error(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_streak_alert", lambda n: alertutils.AlertObj())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_chat_notification(_notice("watch_streak", watch_streak={"streak_count": 3})))
    assert len(pipe.queued) == 1


# --------------------------------------------------------------------------- moderation


def test_on_moderate_all_actions(api, pipe):
    ev = SimpleNamespace(
        moderator_user_id="m", moderator_user_name="Mod",
        warn=SimpleNamespace(user_id="1", user_name="a", reason="spam"),
        timeout=SimpleNamespace(user_id="2", user_name="b", duration_seconds=60, reason="r"),
        delete=SimpleNamespace(user_id="3", user_name="c", message_id="mid"),
        ban=SimpleNamespace(user_id="4", user_name="d", reason="x", ends_at=None),
    )
    run(api.on_moderate(SimpleNamespace(event=ev)))
    data = pipe.engine.message_moderation.call_args.args[0]
    assert set(data["actions"]) == {"warning", "timeout", "delete", "ban"}
    assert data["actions"]["timeout"]["duration"] == 60 and data["actions"]["ban"]["permanent"] is True


def test_on_moderate_minimal_objects_and_errors(api, pipe, monkeypatch):
    ev = SimpleNamespace(
        moderator_user_id="m", moderator_user_name="Mod",
        warn=SimpleNamespace(user_id="1", user_name="a"),
        timeout=SimpleNamespace(user_id="2", user_name="b"),
        delete=SimpleNamespace(user_id="3", user_name="c"),
        ban=SimpleNamespace(user_id="4", user_name="d", ends_at="2030"),
    )
    run(api.on_moderate(SimpleNamespace(event=ev)))
    acts = pipe.engine.message_moderation.call_args.args[0]["actions"]
    assert acts["warning"]["reason"] is None and acts["timeout"]["duration"] is None
    assert acts["delete"]["message_id"] is None and acts["ban"]["permanent"] is False
    pipe.engine.message_moderation.side_effect = RuntimeError("x")
    empty = SimpleNamespace(moderator_user_id="m", moderator_user_name="M", warn=None, timeout=None, delete=None,
                            ban=None)
    run(api.on_moderate(SimpleNamespace(event=empty)))
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    run(api.on_moderate(SimpleNamespace(event=empty)))


# --------------------------------------------------------------------------- channel update / category


@pytest.fixture
def state(monkeypatch):
    st = MagicMock()
    st.get_twitch_data.return_value = dataobjects.TwitchData(auth_token="secret", client_secret="s",
                                                             current_category="Old")
    monkeypatch.setattr(dataobjects, "state_manager", st)
    return st


def test_on_update(api, pipe, state, monkeypatch):
    suggest = MagicMock()
    monkeypatch.setattr(notification_engine, "maybe_suggest_game_hook_for_category", suggest)
    run(api.on_update(SimpleNamespace(event=SimpleNamespace(category_name="Factorio", title="T"))))
    state.update_twitch_field.assert_called_once_with("current_category", "Factorio")
    state.save_changes.assert_called_once()
    suggest.assert_called_once_with("Factorio")
    assert pipe.engine.instant_alert.call_args.args[0]["category"] == "Factorio"
    event, payload = pipe.engine.safe_emit.call_args.args
    assert event == "twitch_data_update" and payload["auth_token"] == "" and payload["client_secret"] == ""


def test_on_update_errors(api, pipe, state, monkeypatch):
    state.update_twitch_field.side_effect = RuntimeError("db")
    monkeypatch.setattr(notification_engine, "maybe_suggest_game_hook_for_category",
                        MagicMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_update(SimpleNamespace(event=SimpleNamespace(category_name="G", title="T"))))
    pipe.engine.safe_emit.assert_called_once()


def test_broadcast_twitch_data_update_guards(api, pipe, state, monkeypatch):
    state.get_twitch_data.return_value = None
    api._broadcast_twitch_data_update("x")
    pipe.engine.safe_emit.assert_not_called()
    state.get_twitch_data.side_effect = RuntimeError("db")
    api._broadcast_twitch_data_update("x")
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    api._broadcast_twitch_data_update("x")


def test_seed_current_category(api, pipe, state):
    api.twitch = MagicMock()
    api.twitch.get_channel_information = AsyncMock(return_value=[SimpleNamespace(game_name="New")])
    run(api._seed_current_category())
    state.update_twitch_field.assert_called_once_with("current_category", "New")
    pipe.engine.safe_emit.assert_called_once()


def test_seed_current_category_noops(api, pipe, state):
    api.twitch = MagicMock()
    api.twitch.get_channel_information = AsyncMock(return_value=[SimpleNamespace(game_name="Old")])
    run(api._seed_current_category())  # unchanged
    api.twitch.get_channel_information = AsyncMock(return_value=[])
    run(api._seed_current_category())
    api.twitch.get_channel_information = AsyncMock(side_effect=RuntimeError("x"))
    run(api._seed_current_category())
    api.user = None
    run(api._seed_current_category())
    state.update_twitch_field.assert_not_called()


# --------------------------------------------------------------------------- follow


def _follow():
    return SimpleNamespace(event=SimpleNamespace(user_name="Fan", user_login="fan", user_id="5",
                                                 followed_at="2026"))


def test_on_follow_with_alert(api, pipe, monkeypatch):
    alert = alertutils.AlertObj()
    alert.alert_name = "FollowAlert"
    monkeypatch.setattr(alertutils, "fetch_follow_alert", lambda: alert)
    pipe.bot.process_event.return_value = ("thanks", ["twitch"])
    run(api.on_follow(_follow()))
    assert pipe.queued == [alert] and alert.username == "Fan" and alert.alert_type == "follow"
    assert pipe.engine.instant_alert.call_args.args[0]["type"] == "follow"
    pipe.stats.increment_follow_alerts.assert_called_once_with(username="Fan", alert_name="FollowAlert")
    event_type, data = pipe.bot.process_event.call_args.args
    assert event_type == tw.EventType.FOLLOW and data["user_id"] == "5"
    assert pipe.dispatched == [("thanks", ["twitch"])]
    assert pipe.feed[0]["message"] == "Fan just followed!"


def test_on_follow_without_alert_and_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_follow_alert", lambda: None)
    pipe.stats.increment_follow_alerts.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_follow(_follow()))
    assert pipe.queued == [] and len(pipe.stored) == 1
    pipe.engine.instant_alert.assert_not_called()
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_follow(_follow()))
    assert len(pipe.feed) == 2


def test_on_follow_instant_alert_error(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_follow_alert", lambda: alertutils.AlertObj())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_follow(_follow()))
    assert len(pipe.queued) == 1

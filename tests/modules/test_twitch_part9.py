# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 9: bits / cheers, raids, channel point redemptions
and hype train handlers."""

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
)
from modules.uiwindows import activity_feed  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(tw, "_cheer_bits_cache", {})
    monkeypatch.setattr(tw, "_alert_user_message_cache", {})


@pytest.fixture
def pipe(monkeypatch):
    rec = SimpleNamespace(stored=[], queued=[], feed=[], events=[], enabled=True, sent=[])
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
    rec.bot.process_event.return_value = None
    monkeypatch.setattr(tw, "get_chatbot_manager", lambda: rec.bot)
    monkeypatch.setattr(tw, "_run_chatbot_in_order", lambda fn: fn())
    rec.dispatched = []
    monkeypatch.setattr(chatbot, "dispatch_process_event_result", lambda r: rec.dispatched.append(r))
    monkeypatch.setattr(chatbot, "send_chatbot_message", lambda m: rec.sent.append(m))
    monkeypatch.setattr(tw, "_notify_chatbot_event", lambda t, d: rec.events.append((t, d)))
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template", lambda name: None)
    return rec


@pytest.fixture
def api():
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42")
    a.user_id = "42"
    a.twitch = MagicMock()
    a.twitch.get_channel_information = AsyncMock(return_value=[SimpleNamespace(game_name="Factorio")])
    return a


def _alert(name="A"):
    a = alertutils.AlertObj()
    a.alert_name = name
    return a


# --------------------------------------------------------------------------- bits use


def _bits(type_="cheer", message=None, power_up=None, anonymous=False, **kw):
    ev = SimpleNamespace(user_name="Bitty", user_login="bitty", user_id="3", bits=100, type=type_,
                         message=message, power_up=power_up, is_anonymous=anonymous)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


def test_bits_use_cheer_caches_message(api, pipe):
    frags = [SimpleNamespace(type="text", text="go ", emote=None, cheermote=None),
             SimpleNamespace(type="emote", text="Kappa", emote=SimpleNamespace(id="25"), cheermote=None)]
    run(api.on_bits_use(_bits(message=SimpleNamespace(text="go Kappa", fragments=frags))))
    cached = tw._cheer_bits_cache["3:100"]
    assert cached["text"] == "go Kappa" and cached["emotes"] == [{"begin": 3, "end": 7, "id": "25"}]
    assert pipe.queued == [] and pipe.feed == []


def test_bits_use_cheer_without_fragments(api, pipe):
    run(api.on_bits_use(_bits(message=SimpleNamespace(text="hi", fragments=None), user_id=None)))
    assert tw._cheer_bits_cache["anonymous:100"]["fragments"] is None


def test_bits_use_power_up(api, pipe, monkeypatch):
    alert = _alert("Power")
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: alert)
    pipe.bot.process_event.return_value = ("ty",)
    power = SimpleNamespace(type="gigantify_an_emote", emote=SimpleNamespace(id="9", name="Big"))
    run(api.on_bits_use(_bits("power_up", message=SimpleNamespace(text="x", fragments=None), power_up=power)))
    assert pipe.queued == [alert] and alert.is_gigantified_emote is True and alert.amt_cheered == 100
    data = pipe.engine.instant_alert.call_args.args[0]
    assert data["type"] == "bits_use" and data["power_up_type"] == "Gigantify An Emote"
    bits_data = pipe.bot.process_event.call_args.args[1]
    assert bits_data["message"] == "x" and bits_data["user_id"] == "3"
    assert pipe.dispatched == [("ty",)]
    pipe.stats.increment_bit_alerts.assert_called_once_with(bit_amount=100, username="Bitty", alert_name="Power")
    assert pipe.feed[0]["message"] == "Bitty has redeemed Gigantify An Emote for 100 bits!"


def test_bits_use_power_up_anonymous_no_alert_and_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: None)
    pipe.stats.increment_bit_alerts.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    power = SimpleNamespace(type="celebration")
    run(api.on_bits_use(_bits("power_up", message="raw text", power_up=power, anonymous=True)))
    assert pipe.queued == [] and len(pipe.stored) == 1
    pipe.engine.instant_alert.assert_not_called()
    data = pipe.bot.process_event.call_args.args[1]
    assert data["username"] == "Anonymous" and data["user_login"] == "" and data["message"] == "raw text"


def test_bits_use_power_up_instant_and_chatbot_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: _alert())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_bits_use(_bits("power_up", power_up=SimpleNamespace(type="message_effect"))))
    assert len(pipe.feed) == 1


def test_bits_use_other_type_without_power_up_does_nothing(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: _alert())
    run(api.on_bits_use(_bits("power_up", power_up=None)))
    assert pipe.queued == [] and pipe.feed == []


# --------------------------------------------------------------------------- cheer


def _cheer(anonymous=False, message="Cheer100 nice", **kw):
    ev = SimpleNamespace(user_name="Cheery", user_login="cheery", user_id="4", bits=100, is_anonymous=anonymous,
                         message=message)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


def test_cheer_uses_cached_bits_message(api, pipe, monkeypatch):
    alert = _alert("Cheer")
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: alert)
    tw._store_cheer_bits_message("4", 100, "nice", [{"type": "text"}], [{"id": "1"}])
    pipe.bot.process_event.return_value = ("ty",)
    run(api.on_cheer(_cheer()))
    assert alert.message == "nice" and alert.fragments == [{"type": "text"}] and alert.emotes == [{"id": "1"}]
    assert pipe.queued == [alert]
    assert pipe.engine.instant_alert.call_args.args[0]["type"] == "cheer"
    assert pipe.bot.process_event.call_args.args[1]["user_id"] == "4"
    assert pipe.dispatched == [("ty",)]
    assert pipe.feed[0]["message"] == "Cheery cheered 100 bits!"


def test_cheer_without_cache_anonymous(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: None)
    pipe.stats.increment_bit_alerts.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_cheer(_cheer(anonymous=True, message=None)))
    assert pipe.queued == [] and len(pipe.stored) == 1
    data = pipe.bot.process_event.call_args.args[1]
    assert data["username"] == "Anonymous" and data["user_login"] == "" and data["message"] == ""


def test_cheer_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_cheer_alert", lambda b: _alert())
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_cheer(_cheer(user_id=None)))
    assert tw.is_alert_user_message("anonymous", "Cheer100 nice")
    assert len(pipe.feed) == 1


# --------------------------------------------------------------------------- raid


def _raid(**kw):
    ev = SimpleNamespace(from_broadcaster_user_name="Raider", from_broadcaster_user_id="77",
                         from_broadcaster_user_login="raider", viewers=25)
    for k, v in kw.items():
        setattr(ev, k, v)
    return SimpleNamespace(event=ev)


def test_raid_full(api, pipe, monkeypatch):
    alert = _alert("RaidA")
    monkeypatch.setattr(alertutils, "fetch_raid_alert", lambda n: alert)
    monkeypatch.setattr(api, "_raid_shoutout_enabled", lambda kind: True)
    monkeypatch.setattr(api, "send_shoutout", AsyncMock(return_value=True))
    pipe.bot.process_event.return_value = ("welcome",)
    run(api.on_raid(_raid()))
    assert pipe.queued == [alert] and alert.raider_count == 25 and alert.game_name == "Factorio"
    assert pipe.engine.instant_alert.call_args.args[0]["type"] == "raid"
    pipe.stats.increment_raids.assert_called_once_with(username="Raider", alert_name="RaidA")
    api.send_shoutout.assert_awaited_once_with("77", raider_name="Raider")
    assert "How was Factorio?" in pipe.sent[0]
    raid_data = pipe.bot.process_event.call_args.args[1]
    assert raid_data["game"] == "Factorio" and raid_data["viewer_count"] == 25
    assert pipe.dispatched == [("welcome",)]
    assert pipe.feed[0]["raider_count"] == 25 and pipe.feed[0]["game_name"] == "Factorio"


def test_raid_without_alert_or_game_and_shoutouts_off(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_raid_alert", lambda n: None)
    api.twitch.get_channel_information = AsyncMock(side_effect=RuntimeError("404"))
    monkeypatch.setattr(api, "_raid_shoutout_enabled", lambda kind: False)
    monkeypatch.setattr(api, "send_shoutout", AsyncMock())
    pipe.stats.increment_raids.side_effect = RuntimeError("db")
    pipe.bot.process_event.return_value = ("x",)
    monkeypatch.setattr(tw, "_dispatch_chatbot_event_response", MagicMock(side_effect=RuntimeError("d")))
    run(api.on_raid(_raid()))
    assert pipe.queued == [] and pipe.sent == []
    api.send_shoutout.assert_not_awaited()
    pipe.engine.instant_alert.assert_not_called()
    assert pipe.feed[0]["game_name"] is None


def test_raid_errors(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_raid_alert", lambda n: _alert())
    api.twitch.get_channel_information = AsyncMock(return_value=[SimpleNamespace(game_name="")])
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(api, "_raid_shoutout_enabled", lambda kind: True)
    monkeypatch.setattr(api, "send_shoutout", AsyncMock(side_effect=RuntimeError("so")))
    pipe.bot.process_event.side_effect = RuntimeError("bot")
    run(api.on_raid(_raid()))
    assert len(pipe.feed) == 1


# --------------------------------------------------------------------------- points


def _points(cost=500, user_input="hello"):
    reward = SimpleNamespace(title="Hydrate", id="rw1", cost=cost)
    return SimpleNamespace(event=SimpleNamespace(user_name="Pat", user_login="pat", user_id="6",
                                                 user_input=user_input, reward=reward))


def test_points_without_configured_alert(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: None)
    run(api.on_points(_points()))
    aid, stored = pipe.stored[0]
    assert stored["alert_name"] == "Hydrate" and stored["point_cost"] == 500
    data = pipe.engine.instant_alert.call_args.args[0]
    assert data["type"] == "points" and data["point_cost"] == 500
    assert pipe.feed[0]["message"] == "Pat redeemed 'Hydrate'!"
    event_type, payload = pipe.events[0]
    assert event_type == tw.EventType.CHANNEL_POINT_REDEMPTION and payload["amount"] == 500


def test_points_without_alert_dedicated_template_queues_hold(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: None)
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template",
                        lambda name: {"queued": True, "duration_seconds": 7})
    run(api.on_points(_points(cost=None)))
    hold = pipe.queued[0]
    assert hold.hold_queue_only is True and hold.duration == 7.0 and hold.enable_alert is False
    pipe.engine.instant_alert.assert_not_called()


def test_points_without_alert_disabled_and_instant_error(api, pipe, monkeypatch):
    pipe.enabled = False
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: None)
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template",
                        lambda name: {"queued": True, "duration_seconds": 7})
    run(api.on_points(_points()))
    assert pipe.stored == [] and pipe.queued == []
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template", lambda name: None)
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_points(_points()))


def test_points_configured_alert(api, pipe, monkeypatch):
    alert = _alert()
    alert.enable_alert = True
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: alert)
    run(api.on_points(_points()))
    assert pipe.queued == [alert] and alert.point_cost == 500 and alert.username == "Pat"
    pipe.stats.increment_point_alerts.assert_called_once_with(username="Pat", alert_name="Hydrate",
                                                              points_amount=500)
    pipe.stats.increment_channel_points_redeemed.assert_called_once_with(points_amount=500, username="Pat")
    assert pipe.events[0][1]["reward_title"] == "Hydrate"


def test_points_dedicated_template_without_media_holds_queue(api, pipe, monkeypatch):
    alert = _alert()
    alert.enable_alert = True
    alert.gif_dir = alert.gif_name = ""
    alert.single_audio_dir = alert.single_audio_name = ""
    alert.randomized = False
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: alert)
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template",
                        lambda name: {"queued": True, "duration_seconds": 4})
    run(api.on_points(_points()))
    assert alert.hold_queue_only is True and alert.duration == 4.0


def test_points_dedicated_template_with_media_keeps_alert(api, pipe, monkeypatch):
    alert = _alert()
    alert.enable_alert = True
    alert.gif_dir, alert.gif_name = "d", "g.gif"
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: alert)
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template",
                        lambda name: {"queued": True, "duration_seconds": 4})
    run(api.on_points(_points()))
    assert alert.hold_queue_only is False


def test_points_disabled_alert_stores_without_playing(api, pipe, monkeypatch):
    alert = _alert()
    alert.enable_alert = False
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: alert)
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    pipe.stats.increment_point_alerts.side_effect = RuntimeError("db")
    run(api.on_points(_points(cost=None)))
    assert pipe.queued == [] and len(pipe.stored) == 1
    assert pipe.feed[0]["point_cost"] == 0


# --------------------------------------------------------------------------- hype train


def test_hype_train_start(api, pipe):
    ev = SimpleNamespace(conductor_user_name=None, conductor_user_login="c", conductor_user_id=1, level="2",
                         type="golden_kappa")
    run(api.on_hype_train_start(SimpleNamespace(event=ev)))
    aid, stored = pipe.stored[0]
    assert stored["hype_train_level"] == 2 and stored["username"] == "A viewer"
    assert pipe.engine.instant_alert.call_args.args[0]["type"] == "hype_train_start"
    assert pipe.feed[0]["hype_train_type"] == "golden_kappa"
    pipe.engine.hype_train_status_update.assert_called_once()
    assert pipe.engine.hype_train_status_update.call_args.args[0] is True
    assert pipe.events[0][0] == tw.EventType.HYPE_TRAIN_START and pipe.events[0][1]["user_id"] == "1"


def test_hype_train_start_bad_level_disabled_and_errors(api, pipe, monkeypatch):
    pipe.enabled = False
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_hype_train_start(SimpleNamespace(event=SimpleNamespace(conductor_user_name="C", level="high"))))
    assert pipe.stored == []
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    run(api.on_hype_train_start(SimpleNamespace(event=SimpleNamespace(conductor_user_name="C", level=None))))
    assert len(pipe.events) == 2


def test_hype_train_progress(api, pipe, monkeypatch):
    ev = SimpleNamespace(level=3, progress=50, goal=100)
    run(api.on_hype_train_progress(SimpleNamespace(event=ev)))
    assert pipe.engine.instant_alert.call_args.args[0]["progress"] == 50
    assert pipe.engine.hype_train_status_update.call_args.args[1]["event_type"] == "progress"
    assert pipe.events[0][0] == tw.EventType.HYPE_TRAIN_PROGRESS
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_hype_train_progress(SimpleNamespace(event=ev)))
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    run(api.on_hype_train_progress(SimpleNamespace(event=ev)))
    assert len(pipe.events) == 3


def test_hype_train_end(api, pipe):
    contribs = [SimpleNamespace(user_name="A", total=500, type="bits"),
                SimpleNamespace(user_name="B", total=1, type="subscription"),
                SimpleNamespace(user_name="C", total=1, type="other")]
    ev = SimpleNamespace(level=4, total=2000, top_contributions=contribs, type=None)
    run(api.on_hype_train_end(SimpleNamespace(event=ev)))
    assert pipe.stored[0][1]["hype_train_level"] == 4
    data = pipe.engine.instant_alert.call_args.args[0]
    assert data["top_contributions"] == ["A (500 bits)", "B (1 subscription)"]
    assert pipe.feed[0]["user_message"] == "Top: A (500 bits), B (1 subscription)"
    pipe.stats.increment_hype_train_completion.assert_called_once_with(4)
    assert pipe.engine.hype_train_status_update.call_args.args[0] is False
    assert pipe.events[0][1]["amount"] == 2000


def test_hype_train_end_minimal_and_errors(api, pipe, monkeypatch):
    pipe.enabled = False
    pipe.stats.increment_hype_train_completion.side_effect = RuntimeError("db")
    monkeypatch.setattr(tw, "_send_twitch_instant_alert", MagicMock(side_effect=RuntimeError("x")))
    contrib = SimpleNamespace(user_name="Z", total=3)
    run(api.on_hype_train_end(SimpleNamespace(event=SimpleNamespace(level=1, total=3, top_contributions=[contrib]))))
    assert pipe.stored == []
    pipe.enabled = True
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=None))
    run(api.on_hype_train_end(SimpleNamespace(event=SimpleNamespace(level=1, total=0, top_contributions=None))))
    assert pipe.feed[-1]["user_message"] is None

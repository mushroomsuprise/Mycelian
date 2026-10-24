# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_variables consistency, and the runtime events it describes
(Twitch handlers, subs, donations)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules import connector_variables as cv
from modules.connector_core import ComparisonOperator as Op, TriggerCondition, TriggerType
from modules.connector_placeholders import substitute_connector_placeholders as sub
from modules.connector_triggers import create_trigger


def _trigger_for(value):
    kwargs = {}
    if value == "hotkey":
        kwargs["key_combination"] = "Ctrl + Alt + J"
    elif value == "streamdeck":
        kwargs["connector_id"] = "cid"
    elif value == "schedule":
        kwargs.update(specific_time="08:15", days_of_week=[1])
    elif value == "timer":
        kwargs["interval_seconds"] = 90
    return create_trigger(TriggerType(value), "trig", "n", **kwargs)


def test_every_trigger_has_variables_fields_and_a_firing_sample():
    values = [t["value"] for t in cv.TRIGGERS]
    assert len(values) == len(set(values))
    assert set(values) == {t.value for t in TriggerType}
    for t in cv.TRIGGERS:
        assert t["group"] in {"Twitch", "YouTube", "Tips", "OBS", "Controls", "Time", "Other"}
        assert t["fires"] is True and t["label"] and t["hint"]
        value = t["value"]
        assert value in cv.EVENT_VARIABLES and value in cv.CONDITION_FIELDS
        trigger = _trigger_for(value)
        event = cv.sample_event(value, trigger)
        assert event["event_type"] == ("twitch_chat_message" if value == "any" else value)
        for field in cv.CONDITION_FIELDS[value]:
            assert field["key"] in event, (value, field["key"])
            assert field["type"] in cv.OPERATORS
        for var in cv.EVENT_VARIABLES[value]:
            assert var["key"] in event, (value, var["key"])
            assert var["group"] in {"Event", "Stream"}
        assert trigger.should_trigger(event), value
    hype = [t for t in cv.TRIGGERS if "hype" in t["value"]]
    assert all(t["warning"] for t in hype)


def test_any_fields_are_union_plus_event_type():
    keys = [f["key"] for f in cv.CONDITION_FIELDS["any"]]
    assert keys[0] == "event_type" and len(keys) == len(set(keys))
    for value, fields in cv.CONDITION_FIELDS.items():
        for field in fields:
            assert field["key"] in keys


def test_always_variables_and_formats():
    groups = {v["group"] for v in cv.ALWAYS_VARIABLES}
    assert groups == {"Connector", "Time", "Message helpers"}
    keys = {v["key"] for v in cv.ALWAYS_VARIABLES}
    assert {"connector_name", "trigger_count", "time", "date", "datetime"} <= keys
    dated = {v["key"] for v in cv.ALWAYS_VARIABLES if v.get("date")}
    assert {"time", "date", "datetime", "triggered_at"} <= dated
    assert {"format", "label"} <= set(cv.DATE_FORMATS[0])
    assert len(cv.DATE_FORMATS) >= 10
    ops = dict(cv.OPERATORS["text"])
    assert ops["begins_with"] == "starts with" and ops["starts_with"] == "first word is"
    assert [v for v, _ in cv.ALERT_TYPES] == [
        "bit", "sub", "resub", "giftsub", "streak", "donation", "raid", "follow", "point",
    ]


def test_example_context_renders_preview():
    ctx = cv.example_context("twitch_follow", "Greeter", 4)
    text = sub("{connector_name} #{trigger_count}: {username} at {followed_at:%Y} {nope}", ctx)
    assert text == "Greeter #5: TestUser at 2026 {nope}"
    for fmt in cv.DATE_FORMATS:
        for key in ("time", "date", "datetime", "triggered_at"):
            out = sub("{%s:%s}" % (key, fmt["format"]), ctx)
            assert out and "{" not in out and "%" not in out


def test_describe_conditions():
    labels = {f["key"]: f["label"] for f in cv.CONDITION_FIELDS["twitch_bits"]}
    conds = [
        TriggerCondition("amount", Op.GREATER_THAN_OR_EQUAL, 100),
        TriggerCondition("username", Op.NOT_EQUAL, "bot"),
        TriggerCondition("message", Op.CONTAINS, "hi", join="or"),
    ]
    assert cv.describe_conditions(conds, labels) == (
        "Runs when Bits at least (≥) 100 and Username is not bot, or when Message contains hi"
    )
    assert cv.describe_conditions([], labels) == "Runs every time"
    assert cv.describe_conditions(
        [{"field": "amount", "operator": "equal", "value": 5}]
    ) == "Runs when amount is 5"


def test_webhook_url_uses_engine_port(monkeypatch):
    from modules import web_engine

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(port=5123))
    assert cv.webhook_url("abc", "k y") == (
        "http://127.0.0.1:5123/api/connectors/webhook/abc?key=k%20y"
    )
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    assert cv.webhook_url("abc") == "http://127.0.0.1:5000/api/connectors/webhook/abc"
    assert len(cv.generate_webhook_secret()) >= 24


# ---- runtime events ------------------------------------------------------


def _capture_manager():
    events = []

    async def add_event(event):
        events.append(event)

    return SimpleNamespace(add_event=add_event, events=events)


def test_bits_and_giftsub_events_are_plain():
    from modules.connector_integration import ConnectorIntegration

    integ = ConnectorIntegration.__new__(ConnectorIntegration)
    integ.manager = _capture_manager()
    bits = SimpleNamespace(event=SimpleNamespace(
        bits=100, user_name="Bob", user_id="1", is_anonymous=False, type="cheer",
        message=SimpleNamespace(text="Cheer100 hi", fragments=[]),
    ))
    asyncio.run(integ._handle_twitch_bits(bits))
    gift = SimpleNamespace(event=SimpleNamespace(
        user_name=None, user_id=None, is_anonymous=True, tier="2000", total=5,
        cumulative_total=None,
    ))
    asyncio.run(integ._handle_twitch_giftsub(gift))
    bits_event, gift_event = integ.manager.events
    assert bits_event["message"] == "Cheer100 hi" and bits_event["amount"] == 100
    assert gift_event["username"] == "Anonymous" and gift_event["tier"] == 2
    assert gift_event["gift_count"] == 5 and "recipient" not in gift_event


def test_chat_event_plain_badges_and_own_echo_skipped(monkeypatch):
    from modules import chatbot, connector_integration as ci, twitch
    from modules import twitch_moderators

    integ = ci.ConnectorIntegration.__new__(ci.ConnectorIntegration)
    integ.manager = _capture_manager()
    monkeypatch.setattr(twitch, "twitch_api", SimpleNamespace(user_id="100"))
    monkeypatch.setattr(twitch_moderators, "resolve_is_moderator_async", AsyncMock(return_value=True))
    monkeypatch.setattr(chatbot, "chatbot_api", SimpleNamespace(using_fallback=False, user_id="200"))

    def chat(user_id, text):
        return SimpleNamespace(event=SimpleNamespace(
            chatter_user_id=user_id, chatter_user_name="u" + user_id,
            badges=[SimpleNamespace(set_id="moderator"), SimpleNamespace(set_id="vip")],
            message=SimpleNamespace(text=text, fragments=[SimpleNamespace(type="emote", text="Kappa")]),
        ))

    asyncio.run(integ._handle_twitch_chat(chat("300", "!hi there")))
    event = integ.manager.events[-1]
    assert event["badges"] == "moderator,vip" and event["emotes"] == "Kappa"
    assert event["command"] == "hi" and event["is_moderator"] is True
    asyncio.run(integ._handle_twitch_chat(chat("200", "bot says hi")))  # dedicated bot
    assert len(integ.manager.events) == 1
    chatbot.note_sent_chat_message("thanks Bob")
    asyncio.run(integ._handle_twitch_chat(chat("100", "thanks Bob")))  # echo from broadcaster
    assert len(integ.manager.events) == 1
    asyncio.run(integ._handle_twitch_chat(chat("100", "thanks Bob")))  # streamer typed it again
    assert len(integ.manager.events) == 2


def test_subs_are_not_wrapped(monkeypatch):
    from modules import connector_integration as ci, twitch

    api = SimpleNamespace()
    for name in ("on_bits_use", "on_sub", "on_resub", "on_sub_gift", "on_follow", "on_raid",
                 "on_points", "on_chat_message", "on_hype_train_start", "on_hype_train_end",
                 "on_stream_online", "on_stream_offline"):
        setattr(api, name, AsyncMock())
    originals = {"on_sub": api.on_sub, "on_resub": api.on_resub}
    monkeypatch.setattr(twitch, "twitch_api", api)
    integ = ci.ConnectorIntegration.__new__(ci.ConnectorIntegration)
    integ.manager = _capture_manager()
    integ._twitch_integration_setup = False
    integ._twitch_retry_thread = None
    integ.setup_twitch_integration()
    assert api.on_sub is originals["on_sub"] and api.on_resub is originals["on_resub"]
    assert "on_bits_use" in integ._original_twitch_handlers


def test_resub_through_on_sub_emits_only_resub(tmp_path):
    from modules import twitch as tw
    from modules.twitch_subscriber_registry import SubscriberRegistry

    registry = SubscriberRegistry(db_path=str(tmp_path / "subs.db"))
    api = MagicMock()

    async def on_resub(data):
        await tw.Twitch_API.on_resub(api, data)

    api.on_resub = on_resub
    data = SimpleNamespace(event=SimpleNamespace(
        user_name="Bob", user_id="7", user_login="bob", tier="2000", cumulative_months=12,
        streak_months=4, duration_months=1, message=SimpleNamespace(text="yay", emotes=None),
    ))
    alert = MagicMock()
    new_sub, resub = AsyncMock(), AsyncMock()
    with patch("modules.twitch_subscriber_registry.get_subscriber_registry",
               return_value=registry), \
            patch.object(tw.alertutils, "fetch_resub_alert", return_value=alert), \
            patch.object(tw, "_queue_twitch_alert"), \
            patch.object(tw, "_send_twitch_instant_alert"), \
            patch.object(tw, "_add_twitch_alert_to_feed"), \
            patch.object(tw, "register_alert_user_message"), \
            patch.object(tw, "_emit_connector_new_sub", new=new_sub), \
            patch.object(tw, "_emit_connector_resub", new=resub), \
            patch.object(tw, "get_chatbot_manager",
                         return_value=MagicMock(process_event=MagicMock(return_value=None))), \
            patch.object(tw.statistics_manager, "get_statistics_manager",
                         return_value=MagicMock()), \
            patch.object(tw, "web_engine") as web:
        web.web_engine_instance = None
        asyncio.run(tw.Twitch_API.on_sub(api, data))
    new_sub.assert_not_awaited()
    resub.assert_awaited_once()
    kwargs = resub.await_args.kwargs
    assert kwargs["months"] == 12 and kwargs["tier"] == 2 and kwargs["streak_months"] == 4


def test_resub_event_shape():
    from modules.connector_core import EventData

    event = EventData.from_twitch_resub(tier="3000", username="Bob", months=5)
    assert event["event_type"] == "twitch_resub" and event["tier"] == 3
    assert EventData.from_twitch_sub(tier="prime", username="Bob")["tier"] == 1


def test_donation_emits_connector_event(monkeypatch):
    from modules import donation_alerts as da

    scheduled = []
    manager = SimpleNamespace(
        is_running=True,
        _schedule_event_on_connector_loop=lambda ev: scheduled.append(ev) or True,
    )
    import modules.connector_manager as cm

    monkeypatch.setattr(cm, "get_manager", lambda: manager)
    monkeypatch.setattr(da, "get_settings", lambda: SimpleNamespace(alert_currency="USD"))
    monkeypatch.setattr(da, "_fire_chatbot", lambda *a, **k: None)
    import modules.alertutils as au

    monkeypatch.setattr(au, "fetch_donation_alert", lambda q: au.AlertObj())
    da.emit_donation_alert(username="Ann", amount="7.5", currency="USD", message="gg",
                           source="streamelements", event_id="evt-test-1", enqueue=False)
    assert len(scheduled) == 1
    event = scheduled[0]
    assert event["event_type"] == "donation" and event["source"] == "streamelements"
    assert event["amount"] == 7.5 and event["currency"] == "USD"
    assert event["display_amount"] == event["formatted_amount"] and event["message"] == "gg"
    trigger = create_trigger(TriggerType.DONATION, "d", "n")
    assert trigger.should_trigger(event)
    for field in cv.CONDITION_FIELDS["donation"]:
        assert field["key"] in event


def test_donation_safe_when_connectors_stopped(monkeypatch):
    from modules import donation_alerts as da
    import modules.connector_manager as cm

    monkeypatch.setattr(cm, "get_manager", lambda: SimpleNamespace(is_running=False))
    monkeypatch.setattr(da, "get_settings", lambda: SimpleNamespace(alert_currency="USD"))
    monkeypatch.setattr(da, "_fire_chatbot", lambda *a, **k: None)
    import modules.alertutils as au

    monkeypatch.setattr(au, "fetch_donation_alert", lambda q: au.AlertObj())
    assert da.emit_donation_alert(username="Ann", amount=1, currency="USD", source="streamlabs",
                                  event_id="evt-test-2", enqueue=False) is not None


@pytest.mark.parametrize("value", ["timer", "schedule", "hotkey", "streamdeck"])
def test_sample_events_are_tailored(value):
    trigger = _trigger_for(value)
    event = cv.sample_event(value, trigger)
    if value == "hotkey":
        assert event["combo"] == "ctrl+alt+j"
    elif value == "streamdeck":
        assert event["connector_id"] == "cid"
    elif value == "schedule":
        assert event["trigger_id"] == "trig" and event["specific_time"] == "08:15"
    else:
        assert event["timer_id"] == "trig" and event["interval_seconds"] == 90

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Multi-month subs and gifts (Twitch duration_months) in the Activity Feed."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alertutils  # noqa: E402
from modules import twitch as twitch_module  # noqa: E402
from modules.uiwindows import activity_feed as feed  # noqa: E402
from modules.uiwindows import activity_feed_model as model  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_gift_state():
    twitch_module._clear_pending_single_gifts_for_tests()
    yield
    twitch_module._clear_pending_single_gifts_for_tests()


def _payload(*args, **kwargs):
    with (
        patch.object(feed.alertutils.alert_state_manager, "initialize"),
        patch.object(feed.alertutils.alert_state_manager, "get_stored_alert_by_id", return_value=None),
        patch.object(feed.alertutils, "resolve_chat_alert_media", return_value=None),
    ):
        return feed.build_activity_feed_alert_payload(*args, **kwargs)


# --------------------------------------------------------------------------- setting


def test_setting_defaults_on_and_normalises():
    assert model.normalize_feed_settings(None)["sub_duration"] is True
    assert model.normalize_feed_settings({"activity_feed_show_sub_duration": "false"})["sub_duration"] is False
    assert model.normalize_feed_settings({"sub_duration": 0})["sub_duration"] is False
    assert model.feed_setting_value("sub_duration", "on") is True
    assert model.FEED_SETTINGS["sub_duration"][0] == "activity_feed_show_sub_duration"
    norm = model.normalize_feed_settings(None)
    assert (norm["sub_duration_style"], norm["sub_duration_place"]) == ("split", "inline")
    norm = model.normalize_feed_settings({"activity_feed_sub_duration_style": "dots", "activity_feed_sub_duration_place": "nowhere"})
    assert (norm["sub_duration_style"], norm["sub_duration_place"]) == ("dots", "inline")
    from modules.dataobjects import AppSettings
    assert (AppSettings().activity_feed_sub_duration_style, AppSettings().activity_feed_sub_duration_place) == ("split", "inline")


def test_captions_full_and_short():
    assert model.sub_duration_caption(12, "sub") == "12 months in advance"
    assert model.sub_duration_caption(12, "sub", short=True) == "12 mo up front"
    assert model.sub_duration_caption(6, "resub", short=True) == "6 mo ahead"
    assert model.sub_duration_caption(3, model.sub_duration_kind("giftsub", 1)) == "3 months gifted"
    assert model.sub_duration_caption(3, model.sub_duration_kind("giftsub", 5)) == "3 months each"
    assert model.sub_duration_caption(1, "sub") == "" and model.sub_duration_caption(None, "gift") == ""


def test_show_sub_duration_reads_app_settings():
    assert feed.show_sub_duration(SimpleNamespace(activity_feed_show_sub_duration=False)) is False
    assert feed.show_sub_duration(SimpleNamespace(activity_feed_show_sub_duration=True)) is True
    assert feed.show_sub_duration(SimpleNamespace()) is True  # older settings without the field


def test_changing_the_setting_rebuilds_by_person(monkeypatch):
    monkeypatch.setattr(feed, "broadcast_feed_settings", lambda to=None: {"sub_duration": False})
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: None)
    rebuilt = []
    monkeypatch.setattr(feed, "schedule_condensed_view_update", rebuilt.append)
    feed.apply_feed_settings_changed({"sub_duration"})
    assert rebuilt == ["sub_duration"]


# --------------------------------------------------------------------------- text + rows


def test_plain_text_messages():
    assert model.sub_message("Ann", 1, 6) == "Ann subscribed for 6 months (Tier 1)!"
    assert model.sub_message("Ann", 1, 6, show_duration=False) == "Ann subscribed (Tier 1)!"
    assert model.sub_message("Ann", 2, 1) == "Ann subscribed (Tier 2)!"
    assert model.resub_message("Bo", 14, 1, 6) == "Bo resubscribed for 14 months (Tier 1) with a 6-month sub!"
    assert model.resub_message("Bo", 14, 1, 1) == "Bo resubscribed for 14 months (Tier 1)!"
    assert model.giftsub_message("Cy", 1, 1, "Dee", 3) == "Cy gifted a 3-month Tier 1 sub to Dee!"
    assert model.giftsub_message("Cy", 1, 1, None, 3) == "Cy gifted a 3-month Tier 1 sub!"
    assert model.giftsub_message("Cy", 1, 1, "Dee", 3, show_duration=False) == "Cy gifted a Tier 1 sub to Dee!"
    assert model.giftsub_message("Cy", 2, 5, None, 3) == "Cy gifted 5 Tier 2 subs (3 months each)!"
    assert model.giftsub_message("Cy", 2, 5, None, 0) == "Cy gifted 5 Tier 2 subs!"


def test_row_duration_only_for_multi_month_twitch_subs():
    gift = model.feed_row_model({"type": "Giftsub", "badge_type": "giftsub", "username": "Cy", "tier": 1,
                                 "gift_qty": 1, "recipient": "Dee", "duration_months": 3})
    assert gift["duration"] == "3 months gifted" and gift["what"] == "gifted a Tier 1 sub to Dee"
    assert (gift["months"], gift["duration_short"], gift["duration_kind"]) == (3, "3 mo gift", "gift")
    assert gift["what_tagged"] == "gifted Dee a Tier 1 sub"
    assert gift["what_full"] == "gifted Dee a 3-month Tier 1 sub"
    one = model.feed_row_model({"type": "Sub", "badge_type": "sub", "username": "A", "tier": 1, "duration_months": 1})
    assert one["duration"] == "" and one["months"] == 0 and one["what_full"] == "" and one["what_tagged"] == ""
    # resubs stored before this change kept the months in months_prepaid
    resub = model.feed_row_model({"type": "Resub", "badge_type": "resub", "username": "B", "tier": 1,
                                  "stored_alert_data": {"resub_month": 14, "months_prepaid": 6, "duration_months": 0}})
    assert resub["duration"] == "6 months paid ahead" and resub["tier"] == "T1"
    assert resub["duration_short"] == "6 mo ahead"
    assert resub["what_tagged"] == "resubscribed · 14 months total"
    assert resub["what_full"] == "resubscribed for 6 months · 14 months total"
    sub = model.feed_row_model({"type": "Sub", "badge_type": "sub", "username": "A", "tier": 1, "duration_months": 12})
    assert (sub["duration"], sub["duration_short"]) == ("12 months in advance", "12 mo up front")
    assert (sub["what"], sub["what_tagged"], sub["what_full"]) == ("subscribed", "subscribed", "subscribed for 12 months")
    bits = model.feed_row_model({"type": "Bits", "badge_type": "bits", "username": "C", "duration_months": 3})
    assert bits["duration"] == ""
    member = model.feed_row_model({"type": "Membership", "badge_type": "membership", "source": "youtube", "duration_months": 3})
    assert member["duration"] == ""


def test_feed_payload_carries_duration():
    payload = _payload("Giftsub", "Cy gifted 5 Tier 1 subs (3 months each)!", "giftsub", tier=1, gift_qty=5,
                       alert_id="G1", username="Cy", duration_months="3")
    assert payload["duration_months"] == 3
    assert payload["row"]["duration"] == "3 months each" and payload["row"]["amount"] == "×5"
    assert payload["row"]["duration_kind"] == "each"
    assert payload["row"]["what_full"] == "gifted 5 Tier 1 subs · 3 months each"
    json.dumps(feed.serialize_live_alert(payload))
    plain = _payload("Sub", "A subscribed (Tier 1)!", "sub", tier=1, username="A")
    assert "duration_months" not in plain and plain["row"]["duration"] == ""


def test_history_conversion_with_duration(monkeypatch):
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: True)
    gift = feed.convert_stored_alert_to_feed_format(
        {"alert_type": "giftsub", "username": "Cy", "tier": 1, "gift_qty": 1, "recipient": "Dee",
         "duration_months": 3, "alert_id": "G", "timestamp": 1}
    )
    assert gift["message"] == "Cy gifted a 3-month Tier 1 sub to Dee!"
    assert gift["duration_months"] == 3 and gift["row"]["duration"] == "3 months gifted"
    resub = feed.convert_stored_alert_to_feed_format(
        {"alert_type": "resub", "username": "Bo", "tier": 1, "resub_month": 14, "months_prepaid": 6, "timestamp": 1}
    )
    assert resub["message"] == "Bo resubscribed for 14 months with a 6-month sub!"
    assert resub["row"]["duration"] == "6 months paid ahead"
    sub = feed.convert_stored_alert_to_feed_format(
        {"alert_type": "sub", "username": "Ann", "tier": 2, "duration_months": 6, "timestamp": 1}
    )
    assert sub["message"] == "Ann subscribed for 6 months (Tier 2)!" and sub["row"]["duration"] == "6 months in advance"

    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: False)
    off = feed.convert_stored_alert_to_feed_format(
        {"alert_type": "giftsub", "username": "Cy", "tier": 1, "gift_qty": 1, "recipient": "Dee",
         "duration_months": 3, "timestamp": 1}
    )
    assert off["message"] == "Cy gifted a Tier 1 sub to Dee!"
    # The row keeps the value; the renderer hides the tag while the setting is off.
    assert off["row"]["duration"] == "3 months gifted"


def test_fallback_list_follows_the_setting(monkeypatch):
    # No tag in the plain list, so a multi-month row uses its full sentence.
    row = {"user": "Cy", "what": "gifted a Tier 1 sub to Dee", "what_full": "gifted Dee a 3-month Tier 1 sub",
           "duration": "3 months gifted", "ts": time.time()}
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: True)
    assert "gifted Dee a 3-month Tier 1 sub" in feed.render_fallback_html([row])
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: False)
    html = feed.render_fallback_html([row])
    assert "gifted a Tier 1 sub to Dee" in html and "3-month" not in html


# --------------------------------------------------------------------------- by person


def _gift(user, qty, months, ts):
    return {"type": "Giftsub", "badge_type": "giftsub", "username": user, "tier": 1, "gift_qty": qty,
            "message": f"{user} gifted {qty} Tier 1 subs!", "created_at": ts,
            "stored_alert_data": {"gift_qty": qty, "duration_months": months}}


def test_by_person_lines_show_duration_and_keep_lengths_apart():
    now = time.time()
    alerts = [_gift("cy", 5, 3, now - 30), _gift("cy", 2, 3, now - 20), _gift("cy", 1, 0, now - 10),
              {"type": "Resub", "badge_type": "resub", "username": "cy", "tier": 1, "message": "cy resubscribed",
               "created_at": now - 5, "stored_alert_data": {"resub_month": 14, "months_prepaid": 6}}]
    users, _ex, _unk = feed._group_alerts_for_condensed(alerts, filter_state={"all": True}, search="", show_duration=True)
    group = feed.serialize_condensed_groups(users)[0]
    texts = sorted(i["text"] for i in group["items"])
    assert "Gifted 7 Tier 1 subs · 3 months each" in texts
    assert "Gifted 1 Tier 1 sub" in texts
    assert "Resubscribed for 14 months (Tier 1) · 6 months paid ahead" in texts
    assert "Gifted 7 Tier 1 subs (3 months)!" in group["lines"]

    users, _ex, _unk = feed._group_alerts_for_condensed(alerts, filter_state={"all": True}, search="", show_duration=False)
    group = feed.serialize_condensed_groups(users)[0]
    texts = sorted(i["text"] for i in group["items"])
    assert "Gifted 8 Tier 1 subs" in texts and not any("months" in t and "Gifted" in t for t in texts)


# --------------------------------------------------------------------------- live update + replay


def test_update_alert_in_feed_repaints_in_place(monkeypatch):
    payload = _payload("Giftsub", "Cy gifted 3 Tier 1 subs!", "giftsub", tier=1, gift_qty=3, alert_id="G9", username="Cy")
    payload["seq"] = 7
    other = _payload("Follow", "F just followed!", "follow", alert_id="F1")
    monkeypatch.setattr(feed.activity_feed_state, "live_alerts", [other, payload])
    monkeypatch.setattr(feed, "_schedule_live_push", lambda: None)
    sent = []
    monkeypatch.setattr(feed, "_emit_overlay_event", lambda event, data, to=None: sent.append((event, data)) or True)
    assert feed.update_alert_in_feed("G9", duration_months=3, message="Cy gifted 3 Tier 1 subs (3 months each)!") is True
    entry = feed.live_alerts_snapshot()[1]
    assert entry["row"]["duration"] == "3 months each" and entry["row"]["seq"] == 7
    assert [a.get("alert_id") for a in feed.live_alerts_snapshot()] == ["F1", "G9"]  # order kept
    event, data = sent[-1]
    assert event == "activity_feed_view" and data["live_alerts"][1]["row"]["duration"] == "3 months each"
    assert feed.update_alert_in_feed("missing", duration_months=3) is False


def test_replay_keeps_duration():
    assert "duration_months" in alertutils._REPLAY_EVENT_FIELDS
    payload = alertutils.build_replay_instant_payload(
        {"alert_type": "giftsub", "username": "Cy", "tier": 1, "gift_qty": 1, "duration_months": 3, "alert_id": "G"}, "R1"
    )
    assert payload["duration_months"] == 3
    assert alertutils.AlertObj().duration_months == 0


# --------------------------------------------------------------------------- Twitch handlers


def _feed_calls():
    return patch.object(twitch_module, "_add_twitch_alert_to_feed")


def test_chat_notice_sub_passes_duration():
    api = MagicMock()
    api._emit_verified_new_sub = AsyncMock()
    ev = SimpleNamespace(chatter_user_name="Ann", chatter_user_id="5", chatter_user_login="ann",
                         sub=SimpleNamespace(sub_tier="1000", is_prime=False, duration_months=6),
                         message=SimpleNamespace(text=""), system_message="")
    asyncio.run(twitch_module.Twitch_API._emit_new_sub_from_chat_notice(api, ev))
    assert api._emit_verified_new_sub.await_args.kwargs["duration_months"] == 6


def test_verified_new_sub_shows_duration_in_feed():
    api = MagicMock()
    registry = MagicMock(was_new_sub_alerted_recently=MagicMock(return_value=False))
    alert = alertutils.AlertObj()
    with (
        patch("modules.twitch_subscriber_registry.get_subscriber_registry", return_value=registry),
        patch.object(twitch_module, "_record_known_subscriber"),
        patch.object(twitch_module.alertutils, "fetch_sub_alert", return_value=alert),
        patch.object(twitch_module, "_queue_twitch_alert"),
        patch.object(twitch_module, "_send_twitch_instant_alert") as instant,
        _feed_calls() as add_feed,
        patch.object(twitch_module, "_emit_connector_new_sub", new=AsyncMock()),
        patch.object(twitch_module, "get_chatbot_manager", return_value=MagicMock(process_event=MagicMock(return_value=None))),
        patch.object(twitch_module.statistics_manager, "get_statistics_manager", return_value=MagicMock()),
        patch.object(twitch_module, "_show_sub_duration", return_value=True),
        patch.object(twitch_module, "web_engine") as web,
    ):
        web.web_engine_instance = MagicMock()
        asyncio.run(twitch_module.Twitch_API._emit_verified_new_sub(
            api, username="Ann", user_id="5", user_login="ann", tier=1, duration_months=6))
    kwargs = add_feed.call_args.kwargs
    assert kwargs["duration_months"] == 6
    assert kwargs["message"] == "Ann subscribed for 6 months (Tier 1)!"
    assert alert.duration_months == 6
    assert instant.call_args.args[0]["duration_months"] == 6


def _sub_message_event(cumulative, duration):
    return SimpleNamespace(event=SimpleNamespace(
        user_name="Bo", user_login="bo", user_id="7", tier="1000", message=None,
        cumulative_months=cumulative, duration_months=duration, streak_months=3))


def _patch_sub_handlers(alert):
    return (
        patch("modules.twitch_subscriber_registry.get_subscriber_registry",
              return_value=MagicMock(was_new_sub_alerted_recently=MagicMock(return_value=False))),
        patch.object(twitch_module, "_record_known_subscriber"),
        patch.object(twitch_module, "_overlay_alert_or_placeholder", return_value=(alert, True)),
        # Evaluated before _overlay_alert_or_placeholder: never read real alert configs.
        patch.object(twitch_module.alertutils, "fetch_resub_alert", return_value=None),
        patch.object(twitch_module.alertutils, "fetch_sub_alert", return_value=None),
        patch.object(twitch_module, "register_alert_user_message"),
        patch.object(twitch_module, "_queue_twitch_alert"),
        patch.object(twitch_module, "_send_twitch_instant_alert"),
        patch.object(twitch_module, "_emit_connector_resub", new=AsyncMock()),
        patch.object(twitch_module, "_emit_connector_new_sub", new=AsyncMock()),
        patch.object(twitch_module, "get_chatbot_manager", return_value=MagicMock(process_event=MagicMock(return_value=None))),
        patch.object(twitch_module.statistics_manager, "get_statistics_manager", return_value=MagicMock()),
        patch.object(twitch_module, "_show_sub_duration", return_value=True),
        patch.object(twitch_module, "web_engine", SimpleNamespace(web_engine_instance=None)),
    )


def test_resub_and_sub_message_capture_duration():
    from contextlib import ExitStack

    api = MagicMock()
    api._note_event_received = MagicMock()
    api.on_resub = lambda data: twitch_module.Twitch_API.on_resub(api, data)

    alert = alertutils.AlertObj()
    with ExitStack() as stack:
        for p in _patch_sub_handlers(alert):
            stack.enter_context(p)
        add_feed = stack.enter_context(_feed_calls())
        asyncio.run(twitch_module.Twitch_API.on_sub(api, _sub_message_event(14, 6)))
    kwargs = add_feed.call_args.kwargs
    assert kwargs["badge_type"] == "resub" and kwargs["duration_months"] == 6
    assert kwargs["message"] == "Bo resubscribed for 14 months (Tier 1) with a 6-month sub!"
    assert alert.duration_months == 6 and alert.months_prepaid == 6

    alert = alertutils.AlertObj()
    with ExitStack() as stack:
        for p in _patch_sub_handlers(alert):
            stack.enter_context(p)
        add_feed = stack.enter_context(_feed_calls())
        asyncio.run(twitch_module.Twitch_API.on_sub(api, _sub_message_event(1, 3)))
    kwargs = add_feed.call_args.kwargs
    assert kwargs["badge_type"] == "sub" and kwargs["duration_months"] == 3
    assert kwargs["message"] == "Bo subscribed for 3 months (Tier 1)!"


def _gift_alert(qty=1, alert_id="GA", gifter="CoolGifter", gifter_id="111", recipient=""):
    alert = alertutils.AlertObj()
    alert.alert_type = "giftsub"
    alert.username = gifter
    alert.gift_qty = qty
    alert.tier = 1
    alert.alert_id = alert_id
    alert.timestamp = 1.0
    alert.recipient = recipient
    twitch_module._note_gift_alert_gifter(alert_id, gifter_login=gifter.lower(), gifter_id=gifter_id)
    return alert


def _notice(months, recipient="Dee", community_gift_id=None, gifter_id="111"):
    return twitch_module.note_gift_duration(
        gifter_login="coolgifter", gifter_id=gifter_id, tier=1, duration_months=months,
        community_gift_id=community_gift_id, recipient=recipient)


def test_single_gift_notice_before_emit():
    _notice(3, recipient="Dee")
    alert = _gift_alert(recipient="Dee")
    with (
        patch.object(twitch_module, "_queue_twitch_alert"),
        patch.object(twitch_module, "_send_twitch_instant_alert") as instant,
        patch.object(twitch_module, "_show_sub_duration", return_value=True),
        _feed_calls() as add_feed,
    ):
        twitch_module._emit_giftsub_alert_and_instant(alert)
    kwargs = add_feed.call_args.kwargs
    assert kwargs["duration_months"] == 3
    assert kwargs["message"] == "CoolGifter gifted a 3-month Tier 1 sub to Dee!"
    assert instant.call_args.args[0]["duration_months"] == 3
    assert twitch_module._gift_duration_notices == []  # used up


def test_single_gift_ignores_another_recipients_notice():
    _notice(6, recipient="Someone Else")
    alert = _gift_alert(recipient="Dee")
    with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(twitch_module, "_send_twitch_instant_alert"), _feed_calls() as add_feed:
        twitch_module._emit_giftsub_alert_and_instant(alert)
    assert add_feed.call_args.kwargs["duration_months"] is None
    assert len(twitch_module._gift_duration_watchers) == 1


def test_single_gift_notice_after_emit_updates_the_feed():
    alert = _gift_alert(recipient="Dee")
    with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(twitch_module, "_send_twitch_instant_alert"), _feed_calls():
        twitch_module._emit_giftsub_alert_and_instant(alert)
    stored = {"alert_id": "GA", "alert_type": "giftsub", "duration_months": 0}
    manager = MagicMock()
    manager.get_stored_alert_by_id.return_value = stored
    with (
        patch.object(twitch_module.alertutils, "alert_state_manager", manager),
        patch.object(twitch_module, "_twitch_alerts_enabled", return_value=True),
        patch.object(twitch_module, "_show_sub_duration", return_value=True),
        patch("modules.uiwindows.activity_feed.update_alert_in_feed") as update,
    ):
        assert _notice(3, recipient="Dee") == 1
    assert alert.duration_months == 3
    assert manager.store_completed_alert.call_args.args[1]["duration_months"] == 3
    assert update.call_args.kwargs == {"duration_months": 3, "message": "CoolGifter gifted a 3-month Tier 1 sub to Dee!"}
    assert twitch_module._gift_duration_watchers == []


def _emit_bomb(qty=3):
    alert = _gift_alert(qty=qty, alert_id="BOMB")
    with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(twitch_module, "_send_twitch_instant_alert"), _feed_calls():
        twitch_module._emit_giftsub_alert_and_instant(alert)
    return alert


def test_gift_bomb_gets_months_only_when_every_gift_agrees():
    alert = _emit_bomb(3)
    with (
        patch.object(twitch_module, "_apply_late_gift_duration") as late,
    ):
        assert _notice(3, recipient="r1", community_gift_id="C1") == 0
        assert _notice(3, recipient="r2", community_gift_id="C1") == 0
        late.assert_not_called()
        assert _notice(3, recipient="r3", community_gift_id="C1") == 1
    late.assert_called_once_with(alert, 3)


def test_gift_bomb_with_mixed_months_shows_none():
    _emit_bomb(2)
    with patch.object(twitch_module, "_apply_late_gift_duration") as late:
        _notice(3, recipient="r1", community_gift_id="C2")
        _notice(1, recipient="r2", community_gift_id="C2")
    late.assert_not_called()
    assert twitch_module._gift_duration_watchers == []  # settled: no months


def test_gift_bomb_notices_before_the_gift_event():
    for r in ("r1", "r2"):
        _notice(6, recipient=r, community_gift_id="C3")
    alert = _gift_alert(qty=2, alert_id="BOMB2")
    with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(twitch_module, "_send_twitch_instant_alert"), _feed_calls() as add_feed:
        twitch_module._emit_giftsub_alert_and_instant(alert)
    assert add_feed.call_args.kwargs["duration_months"] == 6
    assert alert.duration_months == 6


def test_other_gifters_notices_are_ignored():
    _notice(3, recipient="Dee", gifter_id="999")
    alert = _gift_alert(recipient="Dee")
    with patch.object(twitch_module, "_queue_twitch_alert"), patch.object(twitch_module, "_send_twitch_instant_alert"), _feed_calls() as add_feed:
        twitch_module._emit_giftsub_alert_and_instant(alert)
    assert add_feed.call_args.kwargs["duration_months"] is None


def test_chat_notification_sub_gift_records_duration(tmp_path):
    from modules.twitch_subscriber_registry import SubscriberRegistry

    registry = SubscriberRegistry(db_path=str(tmp_path / "subs.db"))
    api = MagicMock()
    ev = SimpleNamespace(
        notice_type="sub_gift", chatter_user_id="111", chatter_user_login="coolgifter", chatter_user_name="CoolGifter",
        chatter_is_anonymous=False, message_id="m", system_message="", message=None,
        sub_gift=SimpleNamespace(duration_months=3, cumulative_total=None, recipient_user_id="2",
                                 recipient_user_name="Dee", recipient_user_login="dee", sub_tier="2000",
                                 community_gift_id=None),
    )
    with (
        patch("modules.twitch_subscriber_registry.get_subscriber_registry", return_value=registry),
        patch.object(twitch_module, "_record_known_subscriber"),
        patch.object(twitch_module, "note_gift_recipient") as recipient,
    ):
        asyncio.run(twitch_module.Twitch_API.on_chat_notification(api, SimpleNamespace(event=ev)))
    recipient.assert_called_once()
    notice = twitch_module._gift_duration_notices[0]
    assert (notice["duration"], notice["tier"], notice["recipient"], notice["gifter_id"]) == (3, 2, "Dee", "111")


# --------------------------------------------------------------------------- renderer


_NODE_HARNESS = r"""
global.window = global;
global.document = { querySelectorAll: function () { return []; }, addEventListener: function () {} };
require(process.argv[2]);
var F = window.MycelianFeed;
var resub = { months: 6, duration: '6 months paid ahead', duration_short: '6 mo ahead', duration_kind: 'resub' };
var tags = {};
['split', 'chip', 'calendar', 'dots'].forEach(function (k) { tags[k] = F.monthsTag(resub, k); });
var gift = F.rowFromAlert({ type: 'Giftsub', badge_type: 'giftsub', username: 'c', message: 'c gifted', duration_months: 3 });
console.log(JSON.stringify({
  on: F.normSettings({}).sub_duration,
  off: F.normSettings({ sub_duration: false }).sub_duration,
  gift: gift.duration,
  gift_short: gift.duration_short,
  bits: F.rowFromAlert({ type: 'Bits', badge_type: 'bits', username: 'c', message: 'c cheered', duration_months: 3 }).duration,
  style: F.normSettings({}).sub_duration_style,
  place: F.normSettings({}).sub_duration_place,
  bad: [F.normSettings({ sub_duration_style: 'x', sub_duration_place: 'y' }).sub_duration_style,
        F.normSettings({ sub_duration_style: 'x', sub_duration_place: 'y' }).sub_duration_place],
  dots: F.normSettings({ sub_duration_style: 'DOTS', sub_duration_place: 'below' }),
  tags: tags,
  none: F.monthsTag({ months: 1, duration: '' }, 'split')
}));
process.exit(0);
"""


def test_renderer_setting_and_fallback_rows(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    harness = tmp_path / "h.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    js = ROOT / "assets" / "default_assets" / "activity_feed.js"
    done = subprocess.run([node, str(harness), str(js)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert (out["on"], out["off"], out["gift"], out["gift_short"], out["bits"]) == (True, False, "3 months gifted", "3 mo gift", "")
    assert (out["style"], out["place"], out["bad"]) == ("split", "inline", ["split", "inline"])
    assert (out["dots"]["sub_duration_style"], out["dots"]["sub_duration_place"]) == ("dots", "below")
    tags = out["tags"]
    # Every style carries both captions; CSS shows the short one only on .is-short.
    for style, tag in tags.items():
        assert f'class="mm mm-{style}"' in tag and 'title="Renewed 6 months at once"' in tag
        assert '<span class="s">' in tag and '<span class="l">' in tag
    assert '<b class="n">6</b><span class="cap"><span class="l">months paid ahead</span><span class="s">mo ahead</span>' in tags["split"]
    assert '<span class="l">6 months paid ahead</span><span class="s">6 mo ahead</span>' in tags["chip"]
    assert '<span class="cal"><i></i><b class="n">6</b></span>' in tags["calendar"]
    assert tags["dots"].count("<i></i>") == 6 and '<span class="l">6 months paid ahead</span>' in tags["dots"]
    assert out["none"] == ""
    source = js.read_text(encoding="utf-8")
    assert "Feed.prototype.fitMonths" in source and "this.fitMonths();" in source
    css = (ROOT / "assets" / "default_assets" / "activity_feed.css").read_text(encoding="utf-8")
    for rule in (".afx-row .mm-split", ".afx-row .mm-chip", ".afx-row .mm-calendar", ".afx-row .mm-dots",
                 ".afx-row .mm.is-short .cap .s", ".afx-row .mm-below"):
        assert rule in css

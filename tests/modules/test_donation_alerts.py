# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/donation_alerts.py: dedupe, conversion, enqueue, chatbot/connector/feed fan-out."""

from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alert_processor, alertutils, donation_alerts as da  # noqa: E402
from modules import chatbot, chatbot_manager, connector_manager, notification_engine, web_engine  # noqa: E402
from modules.uiwindows import activity_feed  # noqa: E402


class Recorder:
    def __init__(self):
        self.stored = []
        self.enqueued = []
        self.chatbot = []
        self.dispatched = []
        self.connector = []
        self.instant = []
        self.feed = []
        self.notices = []


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(da, "_seen_ids", OrderedDict())
    monkeypatch.setattr(da, "get_settings", lambda: SimpleNamespace(alert_currency="usd"))
    monkeypatch.setattr(da, "convert_amount", lambda amt, src, tgt: (amt * 2, False) if src != tgt else (amt, False))
    monkeypatch.setattr(alertutils, "fetch_donation_alert", lambda q: alertutils.AlertObj(alert_name=f"tier{q}"))
    monkeypatch.setattr(alertutils, "new_alert_id", lambda prefix: f"{prefix}NEW")
    monkeypatch.setattr(alertutils, "alert_state_manager",
                        SimpleNamespace(store_completed_alert=lambda aid, data: r.stored.append((aid, data))))
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: r.enqueued.append(a))

    class FakeChatbot:
        def process_event(self, event_type, data):
            r.chatbot.append((event_type, data))
            return "result"

    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: FakeChatbot())
    monkeypatch.setattr(chatbot, "dispatch_process_event_result",
                        lambda result, default_targets: r.dispatched.append((result, default_targets)))

    class FakeConnector:
        is_running = True

        def _schedule_event_on_connector_loop(self, event):
            r.connector.append(event)

    monkeypatch.setattr(connector_manager, "get_manager", lambda: FakeConnector())
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(instant_alert=lambda payload: r.instant.append(payload)), raising=False)
    monkeypatch.setattr(activity_feed, "add_alert_to_feed", lambda **kw: r.feed.append(kw))
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: r.notices.append((a, k)))
    monkeypatch.setattr(da.time, "time", lambda: 1234.9)
    return r


# ------------------------------------------------------------------ _remember_event

def test_remember_event_blank_never_dedupes(monkeypatch):
    monkeypatch.setattr(da, "_seen_ids", OrderedDict())
    assert da._remember_event("") is False
    assert da._remember_event("   ") is False
    assert da._remember_event(None) is False
    assert len(da._seen_ids) == 0


def test_remember_event_dedupes_and_evicts(monkeypatch):
    monkeypatch.setattr(da, "_seen_ids", OrderedDict())
    monkeypatch.setattr(da, "_SEEN_LIMIT", 3)
    assert da._remember_event(" a ") is False
    assert da._remember_event("a") is True
    for k in ("b", "c", "d"):
        assert da._remember_event(k) is False
    assert list(da._seen_ids) == ["b", "c", "d"]
    assert da._remember_event("a") is False  # evicted, so new again


# ------------------------------------------------------------------ emit_donation_alert

def test_emit_full_path(rec):
    alert = da.emit_donation_alert(username="  Ann ", amount="5", currency="eur", message=" hi ",
                                   source="streamlabs", event_id="ev/1 2")
    assert alert.alert_name == "tier10"  # 5 EUR * 2 -> 10 USD
    assert alert.username == "Ann"
    assert alert.alert_type == "donation"
    assert alert.donation_amount == 10.0 and alert.currency == "USD"
    assert alert.original_amount == 5.0 and alert.original_currency == "EUR"
    assert alert.donation_source == "streamlabs"
    assert alert.donation_text == "€5.00 ($10.00)"
    assert alert.message == "hi"
    assert alert.alert_id == "Donationstreamlabsev12"
    assert alert.timestamp == 1234.9
    assert not hasattr(alert, "is_supersticker")

    aid, stored = rec.stored[0]
    assert aid == alert.alert_id and stored["source"] == "streamlabs"
    assert rec.enqueued == [alert]

    etype, data = rec.chatbot[0]
    assert data["username"] == "Ann" and data["amount"] == 10.0 and data["currency"] == "USD"
    assert data["formatted_amount"] == "€5.00 ($10.00)" and data["donation_message"] == "hi"
    assert data["original_currency"] == "EUR" and data["source"] == "streamlabs"
    assert rec.dispatched == [("result", ["twitch"])]

    ev = rec.connector[0]
    assert ev is not None

    payload = rec.instant[0]
    assert payload["type"] == "donation" and payload["donation_text"] == "€5.00 ($10.00)"
    assert payload["source"] == "streamlabs" and payload["is_supersticker"] is False

    feed = rec.feed[0]
    assert feed["message"] == "Ann donated €5.00 ($10.00)!"
    assert feed["timestamp"] == "1234" and feed["user_message"] == "hi"
    assert feed["donation_source"] == "streamlabs" and "is_supersticker" not in feed


def test_emit_duplicate_event_is_skipped(rec):
    assert da.emit_donation_alert(username="A", amount=1, currency="USD", source="s", event_id="x") is not None
    assert da.emit_donation_alert(username="A", amount=1, currency="USD", source="s", event_id="x") is None
    assert len(rec.enqueued) == 1
    # Same id from another source is a different event.
    assert da.emit_donation_alert(username="A", amount=1, currency="USD", source="t", event_id="x") is not None


def test_emit_anonymous_bad_amount_new_id_youtube(rec):
    alert = da.emit_donation_alert(username="  ", amount="abc", currency="", source="youtube",
                                   event_id="!!!", is_supersticker=True)
    assert alert.username == "Anonymous"
    assert alert.original_amount == 0.0
    assert alert.alert_id == "DonationyoutubeNEW"  # event id had no safe characters
    assert alert.is_supersticker is True
    assert rec.dispatched[0][1] == ["youtube"]
    assert rec.instant[0]["is_supersticker"] is True
    assert rec.feed[0]["is_supersticker"] is True
    assert rec.feed[0]["user_message"] is None


def test_emit_supersticker_feed_without_kwarg_support(rec, monkeypatch):
    def feed(alert_type, message, badge_type, timestamp, user_message, alert_id, donation_source):
        rec.feed.append(message)

    monkeypatch.setattr(activity_feed, "add_alert_to_feed", feed)
    da.emit_donation_alert(username="A", amount=1, currency="USD", source="youtube", is_supersticker=True)
    assert rec.feed == ["A donated $1.00!"]


def test_emit_used_original_when_no_rate(rec, monkeypatch):
    monkeypatch.setattr(da, "convert_amount", lambda a, s, t: (a, True))
    alert = da.emit_donation_alert(username="B", amount=3.4, currency="GBP", source="streamelements")
    assert alert.currency == "GBP" and alert.donation_amount == 3.4
    assert alert.donation_text == "£3.40"
    assert alert.alert_name == "tier3"


def test_emit_no_matching_alert_uses_blank_alertobj(rec, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_donation_alert", lambda q: None)
    alert = da.emit_donation_alert(username="C", amount=1, currency="USD", source="x")
    assert isinstance(alert, alertutils.AlertObj) and alert.username == "C"


def test_emit_without_enqueue_skips_queue_but_fires_events(rec):
    alert = da.emit_donation_alert(username="D", amount=2, currency="USD", source="youtube", enqueue=False)
    assert alert is not None
    assert rec.enqueued == [] and rec.stored == [] and rec.instant == [] and rec.feed == []
    assert len(rec.chatbot) == 1 and len(rec.connector) == 1


@pytest.mark.parametrize("source,service", [("streamlabs", "Streamlabs"), ("StreamElements", "StreamElements"),
                                            ("youtube", "youtube")])
def test_emit_enqueue_failure_notifies(rec, monkeypatch, source, service):
    def boom(a):
        raise RuntimeError("queue dead")

    monkeypatch.setattr(alert_processor, "enqueue_alert", boom)
    alert = da.emit_donation_alert(username="E", amount=5, currency="USD", source=source, event_id="z")
    assert alert is not None
    (args, kwargs), = rec.notices
    assert f"{service} donation from E" in args[0]
    assert kwargs["type"] == "error" and kwargs["dedupe_key"] == "alert:donation_enqueue"
    assert "RuntimeError: queue dead" in kwargs["details"]
    assert rec.instant == [] and rec.feed == []


def test_emit_enqueue_failure_notice_failure_is_swallowed(rec, monkeypatch):
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("store")))

    def bad_notify(*a, **k):
        raise RuntimeError("notify")

    monkeypatch.setattr(notification_engine, "notify", bad_notify)
    assert da.emit_donation_alert(username="F", amount=1, currency="USD", source="s") is not None
    assert rec.enqueued == []


# ------------------------------------------------------------------ helpers' failure paths

def test_fire_chatbot_swallows_errors(rec, monkeypatch):
    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    da._fire_chatbot("u", 1, "USD", "", "$1.00", "twitch", 1, "USD", 0)
    assert rec.dispatched == []


def test_fire_connector_not_running_or_missing(rec, monkeypatch):
    monkeypatch.setattr(connector_manager, "get_manager", lambda: SimpleNamespace(is_running=False))
    da._fire_connector("u", 1, "USD", "", "s", 1, "USD", "$1")
    monkeypatch.setattr(connector_manager, "get_manager", lambda: None)
    da._fire_connector("u", 1, "USD", "", "s", 1, "USD", "$1")
    assert rec.connector == []


def test_fire_connector_builds_event(rec, monkeypatch):
    from modules import connector_core

    seen = {}
    monkeypatch.setattr(connector_core.EventData, "from_donation",
                        classmethod(lambda cls, **kw: seen.update(kw) or "EVENT"))
    da._fire_connector("u", 4.0, "USD", "m", "src", 2.0, "EUR", "€2.00 ($4.00)")
    assert rec.connector == ["EVENT"]
    assert seen == {"amount": 4.0, "username": "u", "message": "m", "currency": "USD", "source": "src",
                    "original_amount": 2.0, "original_currency": "EUR",
                    "formatted_amount": "€2.00 ($4.00)", "display_amount": "€2.00 ($4.00)"}


def test_fire_connector_swallows_errors(rec, monkeypatch):
    monkeypatch.setattr(connector_manager, "get_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    da._fire_connector("u", 1, "USD", "", "s", 1, "USD", "$1")


def test_send_instant_no_engine_and_error(rec, monkeypatch):
    alert = alertutils.AlertObj(username="u")
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    da._send_instant(alert, "s", "$1")
    assert rec.instant == []

    def boom(payload):
        raise RuntimeError("socket")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(instant_alert=boom), raising=False)
    da._send_instant(alert, "s", "$1")  # no raise


def test_send_feed_error_is_logged(rec, monkeypatch, caplog):
    def boom(**kw):
        raise RuntimeError("feed")

    monkeypatch.setattr(activity_feed, "add_alert_to_feed", boom)
    alert = SimpleNamespace(username="u", timestamp=1.0, message="", alert_id="a")
    with caplog.at_level("ERROR", logger=da.logger.name):
        da._send_feed(alert, "$1")
    assert "Donation activity feed update failed" in caplog.text

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/activity_feed_model.py: feed settings, multi-month sub wording,
event kinds, row models for every kind, filters, queue state, view modes and by-person totals."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows import activity_feed_model as m  # noqa: E402


# ----------------------------------------------------------------- settings

@pytest.mark.parametrize(
    "value, default, out",
    [(True, False, True), (0, True, False), (2.5, False, True), (" YES ", False, True), ("off", True, False),
     ("", True, False), ("maybe", True, True), (None, False, False), ([1], True, True)],
)
def test_coerce_bool(value, default, out):
    assert m._coerce_bool(value, default) is out


@pytest.mark.parametrize("value, out", [("7", 7), (3.9, 3), ("x", 5), (None, 5)])
def test_coerce_int(value, out):
    assert m._coerce_int(value, 5) == out


def test_read_sources():
    assert m._read(None, "f", "k") is None
    assert m._read({"f": 1, "k": 2}, "f", "k") == 1
    assert m._read({"k": 2}, "f", "k") == 2
    assert m._read(type("S", (), {"f": 3})(), "f", "k") == 3


def test_normalize_defaults_and_mapping_lookup():
    defaults = m.normalize_feed_settings()
    assert defaults["layout"] == "rows" and defaults["icons"] is True and defaults["person_hours"] == 12
    assert set(defaults) == set(m.FEED_SETTINGS)
    out = m.normalize_feed_settings({"layout": " CARDS ", "density": "huge", "icons": "no", "earlier_minutes": "60",
                                     "new_minutes": 7, "up_next": "x"})
    assert out["layout"] == "cards" and out["density"] == "comfortable" and out["icons"] is False
    assert out["earlier_minutes"] == 60 and out["new_minutes"] == 5 and out["up_next"] == 1


@pytest.mark.parametrize(
    "stored, legacy, out",
    [(0, None, 12), (0, 6, 6), (0, 0, 12), (0, "bad", 12), (5, None, 5), (100000, None, 720), (-3, 4, 4)],
)
def test_person_hours(stored, legacy, out):
    assert m.normalize_feed_settings({"person_hours": stored}, legacy_person_hours=legacy)["person_hours"] == out


def test_feed_setting_value():
    with pytest.raises(ValueError, match="unknown feed setting"):
        m.feed_setting_value("nope", 1)
    assert m.feed_setting_value("layout", "Ticker") == "ticker"
    assert m.feed_setting_value("person_hours", 0) == 12
    assert m.feed_setting_value("person_hours", "48") == 48
    assert m.feed_setting_value("person_hours", "x") == 12
    assert m.FIELD_TO_FEED_KEY["activity_feed_layout"] == "layout"
    assert m.FEED_SETTING_FIELDS["layout"] == "activity_feed_layout"


# ----------------------------------------------------------------- multi-month subs

@pytest.mark.parametrize("value, out", [(6, 6), ("1,2", 12), (1, 0), (0, 0), ("x", 0), (None, 0)])
def test_duration_months_value(value, out):
    assert m.duration_months_value(value) == out


def test_alert_duration_months():
    assert m.alert_duration_months("not a mapping") == 0
    assert m.alert_duration_months({"duration_months": 3}) == 3
    assert m.alert_duration_months({"stored_alert_data": {"months_prepaid": 6}}) == 6


def test_sub_duration_kind_and_caption():
    assert m.sub_duration_kind("giftsub", 5) == "each"
    assert m.sub_duration_kind("giftsub", None) == "gift"
    assert m.sub_duration_kind("resub") == "resub"
    assert m.sub_duration_kind("sub") == "sub"
    assert m.sub_duration_caption(6, "resub") == "6 months paid ahead"
    assert m.sub_duration_caption(6, "gift", short=True) == "6 mo gift"
    assert m.sub_duration_caption(6, "weird") == "6 months in advance"
    assert m.sub_duration_caption(1, "sub") == ""


def test_messages():
    assert m.sub_message("A", 1) == "A subscribed (Tier 1)!"
    assert m.sub_message("A", 1, 3) == "A subscribed for 3 months (Tier 1)!"
    assert m.sub_message("A", 1, 3, show_duration=False) == "A subscribed (Tier 1)!"
    assert m.resub_message("A", 12) == "A resubscribed for 12 months!"
    assert m.resub_message("A", 12, 2, 6) == "A resubscribed for 12 months (Tier 2) with a 6-month sub!"
    assert m.resub_message("A", 12, 2, 6, show_duration=False) == "A resubscribed for 12 months (Tier 2)!"
    assert m.giftsub_message("G", 1) == "G gifted a Tier 1 sub!"
    assert m.giftsub_message("G", 1, 1, " R ", 3) == "G gifted a 3-month Tier 1 sub to R!"
    assert m.giftsub_message("G", 1, "bad") == "G gifted a Tier 1 sub!"
    assert m.giftsub_message("G", 2, 1500, None, 3) == "G gifted 1,500 Tier 2 subs (3 months each)!"
    assert m.giftsub_message("G", 2, 5, show_duration=False, duration_months=3) == "G gifted 5 Tier 2 subs!"


# ----------------------------------------------------------------- kinds

@pytest.mark.parametrize(
    "alert, kind",
    [
        ("x", "alert"),
        ({}, "alert"),
        ({"type": "cheer"}, "bits"),
        ({"badge_type": "Hype Train", "type": "raid"}, "hype"),
        ({"alert_type": "gift_sub"}, "giftsub"),
        ({"type": "unknown"}, "alert"),
        ({"type": "sub", "source": "YouTube"}, "member"),
        ({"type": "resub", "source": "youtube"}, "milestone"),
        ({"type": "giftsub", "source": "youtube"}, "giftmember"),
        ({"type": "donation", "source": "youtube"}, "superchat"),
        ({"type": "donation", "source": "youtube", "is_supersticker": True}, "supersticker"),
        ({"type": "donation", "source": "youtube", "stored_alert_data": {"is_supersticker": True}}, "supersticker"),
        ({"type": "follow", "source": "youtube"}, "follow"),
    ],
)
def test_feed_kind(alert, kind):
    assert m.feed_kind(alert) == kind


def test_kind_filter_key():
    assert m.kind_filter_key("bits") == "bits"
    assert m.kind_filter_key("zzz") == ""


# ----------------------------------------------------------------- small helpers

def test_pick_and_numbers():
    alert = {"a": "", "stored_alert_data": {"a": "stored", "b": 0}}
    assert m._pick(alert, "a") == "stored"
    assert m._pick(alert, "b") == 0
    assert m._pick(alert, "c") is None
    assert m._stored({"stored_alert_data": "x"}) == {}
    assert m._to_int("1,234.9") == 1234 and m._to_int("") is None and m._to_int("x") is None
    assert m._to_float("2,000.5") == 2000.5 and m._to_float(None) is None and m._to_float("x") is None


@pytest.mark.parametrize(
    "amount, currency, out",
    [(5, None, "$5.00"), ("1,234.5", "eur", "€1,234.50"), (3, "SEK", "3.00 SEK"), (3, " ", "$3.00"),
     ("lots", "USD", "lots"), (None, None, "")],
)
def test_format_money(amount, currency, out):
    assert m.format_money(amount, currency) == out


def test_username_fallbacks():
    assert m._username({"username": " Bob "}, "follow") == "Bob"
    assert m._username({"username": "Unknown", "message": "Ann followed"}, "follow") == "Ann"
    assert m._username({"message": "x"}, "hype") == "Hype Train"
    assert m._username({"message": "Ann did a thing"}, "alert") == "Someone"
    assert m._username({}, "follow") == "Someone"


def test_strip_user_and_ts(monkeypatch):
    assert m._strip_user("Bob did it!!", "bob") == "did it"
    assert m._strip_user("did it!", "") == "did it"
    assert m._ts({"timestamp": "now", "created_at": "12.5"}) == 12.5
    assert m._ts({"timestamp": "bad", "stored_alert_data": {"timestamp": 7}}) == 7.0
    monkeypatch.setattr(m.time, "time", lambda: 99.0)
    assert m._ts({}) == 99.0


# ----------------------------------------------------------------- feed_row_model

def row(**alert):
    return m.feed_row_model(alert)


def test_row_not_mapping_and_ids(monkeypatch):
    monkeypatch.setattr(m.time, "time", lambda: 1.5)
    r = m.feed_row_model("junk")
    assert r["kind"] == "alert" and r["user"] == "Someone" and r["what"] == "alert" and r["id"] == "row-1500"
    assert row(type="follow", alert_id="abc")["id"] == "abc"
    assert row(type="follow", alert_id="generated_1", seq="4")["id"] == "row-4"
    assert m.feed_row_model({"type": "follow"}, seq=9)["seq"] == 9


def test_row_follow_and_sub():
    r = row(type="follow", username="A", timestamp=5)
    assert (r["what"], r["platform"], r["ts"], r["months"], r["what_full"]) == ("followed", "twitch", 5.0, 0, "")
    r = row(type="sub", username="A", tier=2, duration_months=3)
    assert r["tier"] == "T2" and r["what"] == "subscribed" and r["what_full"] == "subscribed for 3 months"
    assert r["duration"] == "3 months in advance" and r["duration_short"] == "3 mo up front"
    assert r["what_tagged"] == "subscribed" and r["duration_kind"] == "sub"
    assert row(type="sub", tier=5)["tier"] is None


def test_row_resub():
    r = row(type="resub", username="A", months=12, duration_months=6)
    assert r["what"] == "resubscribed · 12 months"
    assert r["what_tagged"] == "resubscribed · 12 months total"
    assert r["what_full"] == "resubscribed for 6 months · 12 months total"
    r = row(type="resub", username="A", message="A resubscribed for 4 months!")
    assert r["what"] == "resubscribed · 4 months"
    r = row(type="resub", username="A", message="A resubscribed", duration_months=2)
    assert r["what"] == "resubscribed" and r["what_tagged"] == "resubscribed" and r["what_full"] == "resubscribed for 2 months"


def test_row_giftsub():
    r = row(type="giftsub", username="G", tier=2, recipient="R", duration_months=3)
    assert r["what"] == "gifted a Tier 2 sub to R" and r["what_tagged"] == "gifted R a Tier 2 sub"
    assert r["what_full"] == "gifted R a 3-month Tier 2 sub" and r["duration"] == "3 months gifted"
    r = row(type="giftsub", username="G", duration_months=3)
    assert r["what"] == "gifted a Tier 1 sub" and r["what_full"] == "gifted a 3-month Tier 1 sub"
    r = row(type="giftsub", username="G", message="G gifted 5 subs", duration_months=2)
    assert r["what"] == "gifted 5 Tier 1 subs" and r["amount"] == "×5"
    assert r["what_full"] == "gifted 5 Tier 1 subs · 2 months each" and r["duration_kind"] == "each"
    r = row(type="giftsub", username="G", gift_qty=10)
    assert r["what"] == "gifted 10 Tier 1 subs" and r["what_full"] == ""
    r = row(type="giftsub", username="G", message="G gifted some subs")
    assert r["what"] == "gifted a Tier 1 sub"


def test_row_bits_points_raid():
    assert row(type="bits", username="B", bits=1500)["amount"] == "1,500 bits"
    assert row(type="bits", username="B", message="B cheered 100 bits")["amount"] == "100 bits"
    assert row(type="bits", username="B", message="B cheered")["amount"] == ""
    r = row(type="points", username="P", reward_title="Hydrate", point_cost=500)
    assert r["what"] == "redeemed “Hydrate”" and r["amount"] == "500 pts"
    assert row(type="points", username="P", message="P redeemed 'Dance'!")["what"] == "redeemed “Dance”"
    assert row(type="points", username="P", message="P did")["what"] == "redeemed a reward"
    r = row(type="raid", username="R", raider_count=0, game_name="Celeste")
    assert r["what"] == "raided · last playing Celeste" and r["amount"] == "0 viewers"
    r = row(type="raid", username="R", message="R raided with 1,200 viewers, last playing Hades.")
    assert r["amount"] == "1,200 viewers" and r["what"] == "raided · last playing Hades"
    r = row(type="raid", username="R", message="R raided")
    assert r["what"] == "raided" and r["amount"] == ""


def test_row_donation():
    r = row(type="donation", username="D", donation_amount=5, currency="GBP", donation_source="Streamlabs")
    assert r["amount"] == "£5.00" and r["platform"] == "streamlabs" and r["what"] == "tipped"
    assert row(type="tip", username="D", donation_text="$3 USD")["amount"] == "$3 USD"
    r = row(type="donation", username="D", message="D donated €4.00!")
    assert r["amount"] == "€4.00" and r["platform"] == ""
    assert row(type="donation", username="D", message="D sent money")["amount"] == ""
    assert row(type="follow", username="D", source="streamelements")["platform"] == "streamelements"


def test_row_streak_hype_modiversary():
    assert row(type="streak", username="S", streak_count=5)["what"] == "watched 5 streams in a row"
    assert row(type="streak", username="S", message="S watched 3 consecutive streams")["what"] == "watched 3 streams in a row"
    assert row(type="streak", username="S")["what"] == "is on a watch streak"
    r = row(type="streak", username="S", streak_count=5, user_message="watched 5 streams in a row!")
    assert r["msg"] == ""
    assert row(type="streak", username="S", user_message="hi")["msg"] == "hi"
    r = row(type="hype_train_start", message="Hype train level 3 started")
    assert r["user"] == "Hype Train" and r["what"] == "started · level 3"
    r = row(type="hype_train", username="Mod", level=4, message="Hype Train ended")
    assert r["what"] == "Hype Train ended at level 4"
    r = row(type="hype", alert_type="hype_train_end", username="X")
    assert r["what"] == "Hype Train ended"
    assert row(type="hypetrain", username="X")["what"] == "Hype Train started"
    assert row(type="modiversary", username="M", months=1)["what"] == "has been a moderator for 1 month"
    assert row(type="modiversary", username="M", message="M 24 months")["what"] == "has been a moderator for 24 months"
    assert row(type="modiversary", username="M")["what"] == "celebrated a modiversary"


def test_row_youtube_kinds():
    r = row(type="membership", username="Y", member_level="Gold")
    assert r["what"] == "became a member · Gold" and r["platform"] == "youtube"
    assert row(type="member", username="Y")["what"] == "became a member"
    assert row(type="member_milestone", username="Y", months=8)["what"] == "member for 8 months"
    assert row(type="member_milestone", username="Y", message="Y 9 months")["what"] == "member for 9 months"
    assert row(type="member_milestone", username="Y")["what"] == "reached a member milestone"
    r = row(type="gift_membership", username="Y", gift_qty=3)
    assert r["what"] == "gifted 3 memberships" and r["amount"] == "×3"
    r = row(type="gift_membership", username="Y", message="Y gifted 1")
    assert r["what"] == "gifted 1 membership" and r["amount"] == ""
    assert row(type="gift_membership", username="Y")["what"] == "gifted 1 membership"
    r = row(type="superchat", username="Y", display_amount="¥500")
    assert r["what"] == "sent a Super Chat" and r["amount"] == "¥500"
    assert row(type="super sticker", username="Y", amount=2)["amount"] == "$2.00"
    assert row(type="super sticker", username="Y")["what"] == "sent a Super Sticker"
    assert row(type="superchat", username="Y", message="Y sent (CA$5.00)!")["amount"] == "CA$5.00"
    assert row(type="superchat", username="Y", message="no amount")["amount"] == ""
    assert row(type="follow", username="Y", source="youtube")["platform"] == "youtube"


def test_row_generic_and_flags():
    r = row(type="weird", message="Something happened!", user_message="  hey  ", is_restored=1)
    assert r["what"] == "Something happened" and r["msg"] == "hey" and r["restored"] is True
    assert row(type="weird")["what"] == "alert"


# ----------------------------------------------------------------- filters

def test_row_matches():
    r = {"filter": "bits", "user": "Bob", "what": "cheered", "msg": "", "amount": "100 bits", "label": "Bits"}
    assert m.row_matches(r, {})
    assert not m.row_matches(r, {"all": False, "bits": False})
    assert m.row_matches(r, {"all": False})
    assert not m.row_matches({**r, "filter": ""}, {"all": False})
    assert m.row_matches(r, {}, "  BOB ")
    assert not m.row_matches(r, {}, "alice")


# ----------------------------------------------------------------- queue

def test_queue_entry():
    assert m._queue_entry("x") is None
    assert m._queue_entry({"alert_type": "bits", "is_replay": 1}) == {
        "alert_id": None, "type": "bits", "username": "", "is_replay": True}


def test_normalize_queue_state_full():
    raw = {"paused": True, "muted": False, "server_now_ms": "5000", "revision": 3,
           "playing": {"alert_id": "a1", "type": "sub", "username": "U", "started_at_ms": "100", "duration_ms": 0, "queue_seq": 7},
           "queue": [{"alert_id": "a2"}, "junk", {"alert_id": "a3"}]}
    s = m.normalize_queue_state(raw)
    assert s["paused"] is True and s["muted"] is False and s["contract"] is True
    assert s["playing"]["started_at_ms"] == 100 and s["playing"]["duration_ms"] is None and s["playing"]["queue_seq"] == 7
    assert [q["alert_id"] for q in s["queue"]] == ["a2", "a3"]
    assert s["server_now_ms"] == 5000 and s["revision"] == 3


def test_normalize_queue_state_from_flags():
    s = m.normalize_queue_state(None, paused=True, muted=True, now_ms=42)
    assert s == {"paused": True, "muted": True, "playing": None, "queue": [], "server_now_ms": 42, "revision": -1,
                 "contract": False}
    s = m.normalize_queue_state({"playing": {"duration_ms": "2500"}})
    assert s["playing"]["duration_ms"] == 2500 and s["server_now_ms"] > 0


def test_queue_tags_and_text():
    state = {"playing": {"alert_id": "p"}, "queue": [{"alert_id": "p"}, {"alert_id": "q"}, "x", {}]}
    tags = m.queue_tags(state)
    assert tags == {"p": {"state": "playing", "pos": 0}, "q": {"state": "queued", "pos": 2}}
    assert m.queue_tags(None) == {}
    assert m.queue_tags({"playing": {"alert_id": None}}) == {}
    assert m.queue_tag_text({"state": "playing"}, paused=False) == "Playing"
    assert m.queue_tag_text({"state": "queued", "pos": 2}, paused=False) == "Up next · 2nd"
    assert m.queue_tag_text({"state": "queued"}, paused=True) == "Waiting · 1st"


@pytest.mark.parametrize("n, out", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
                                    (13, "13th"), (21, "21st"), (112, "112th"), (102, "102nd")])
def test_ordinal(n, out):
    assert m.ordinal(n) == out


# ----------------------------------------------------------------- view modes

def test_view_modes():
    assert m.view_mode(None) == "live"
    assert m.view_mode({"tab": "previous"}) == "history"
    assert m.view_mode({"condensed": True}) == "person"
    assert m.view_mode({}) == "live"
    assert m.mode_patch("history") == {"tab": "previous", "page": 1}
    assert m.mode_patch("person") == {"tab": "current", "condensed": True}
    assert m.mode_patch("live") == {"tab": "current", "condensed": False}
    for mode in m.VIEW_MODES:
        assert m.view_mode(m.mode_patch(mode)) == mode
    assert m.normalize_search("  a   b \n c ") == "a b c"
    assert m.normalize_search("x" * 200) == "x" * 120
    assert m.normalize_search(None) == ""


# ----------------------------------------------------------------- person totals

def test_person_totals_every_kind():
    items = [
        {"kind": "bits", "amount": 100}, {"kind": "bits", "amount": 50.5},
        {"kind": "donation", "amount": 5}, {"kind": "superchat"},
        {"kind": "giftsub", "amount": 1}, {"kind": "giftmember", "count": 3},
        {"kind": "raid", "max": 40}, {"kind": "raid", "max": 1200},
        {"kind": "resub", "max": 6}, {"kind": "milestone"},
        {"kind": "sub", "tier": 2}, {"kind": "follow"}, {"kind": "streak", "max": 4}, {"kind": "streak", "count": 1},
        {"kind": "points", "count": 2}, {},
    ]
    totals = {t["kind"]: t["text"] for t in m.person_totals(items)}
    assert totals == {
        "bits": "150 bits", "donation": "$5.00", "superchat": "1", "giftsub": "1 gift", "giftmember": "3 gifts",
        "raid": "1,200", "resub": "6 mo", "milestone": "1", "sub": "T2", "follow": "Follow", "streak": "4 streak",
        "points": "2", "alert": "1",
    }
    by_kind = {t["kind"]: t for t in m.person_totals([{"kind": "sub"}, {"kind": "streak"}, {"kind": "mystery"}])}
    assert by_kind["sub"]["text"] == "Sub" and by_kind["streak"]["text"] == "Streak"
    assert by_kind["mystery"]["icon"] == m.KIND_META["alert"][0]
    assert by_kind["sub"]["icon"] == "star" and by_kind["sub"]["color"] == "var(--color-info)"

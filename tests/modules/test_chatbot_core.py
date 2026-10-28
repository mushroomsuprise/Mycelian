# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_core.py: variables, template engine, normalizers and the data model."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import chatbot_core as core  # noqa: E402
from modules.chatbot_core import (  # noqa: E402
    ChatCommand,
    ChatEvent,
    CommandType,
    ComparisonOperator,
    EventType,
    Greeting,
    Quote,
    TriggerCondition,
)

FIXED = datetime(2024, 1, 2, 19, 5, 7)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = FIXED
        return base if tz is None else base.replace(tzinfo=timezone.utc).astimezone(tz)


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(core, "datetime", FixedDateTime)
    return FIXED


class FakeStats:
    def __init__(self, data=None, fail=False):
        self.data = data or {}
        self.fail = fail
        self.calls = []

    def get_all_statistics(self):
        if self.fail:
            raise RuntimeError("stats down")
        return self.data

    def increment_commands_triggered(self, **kw):
        if self.fail:
            raise RuntimeError("stats down")
        self.calls.append(("command", kw))

    def increment_events_triggered(self, **kw):
        self.calls.append(("event", kw))


@pytest.fixture
def stats(monkeypatch):
    from modules import statistics_manager as sm

    fake = FakeStats()
    monkeypatch.setattr(sm, "get_statistics_manager", lambda: fake)
    return fake


# --------------------------------------------------------------------------- statistics


def test_stat_child_variants():
    assert core._stat_child({"a": 1}, "a") == (True, 1)
    assert core._stat_child({3: "x"}, "3") == (True, "x")
    assert core._stat_child({-2: "neg"}, "-2") == (True, "neg")
    assert core._stat_child({"a": 1}, "b") == (False, None)
    assert core._stat_child([1, 2], "0") == (False, None)


def test_get_statistics_value_paths(stats):
    stats.data = {"follows": {"total": 12, "rate": 1.23456, "none": None, "flag": True}, "list": [1]}
    assert core.get_statistics_value("stats.follows.total") == "12"
    assert core.get_statistics_value("follows.total") == "12"
    assert core.get_statistics_value("follows.rate") == "1.23"
    assert core.get_statistics_value("follows.none") == "0"
    assert core.get_statistics_value("follows.flag") == "true"
    assert core.get_statistics_value("follows.missing") == "0"
    assert core.get_statistics_value("list.0") == "0"


def test_get_statistics_value_error_returns_zero(stats, caplog):
    stats.fail = True
    assert core.get_statistics_value("anything") == "0"
    assert "Error getting statistics value" in caplog.text


# --------------------------------------------------------------------------- youtube


def _yt(monkeypatch, data):
    from modules import dataobjects

    if isinstance(data, Exception):
        def get():
            raise data
    else:
        def get():
            return data
    monkeypatch.setattr(dataobjects.state_manager, "get_youtube_data", get)


def test_youtube_unavailable(monkeypatch):
    _yt(monkeypatch, None)
    assert core.get_youtube_value("latest_video_url") == "YouTube data unavailable"


def test_youtube_global_variables(monkeypatch):
    data = SimpleNamespace(latest_video_url="u", latest_video_title="t", latest_video_id="i",
                           latest_video_channel="c", connection_status="Connected",
                           channels={"a": {}, "b": {}})
    _yt(monkeypatch, data)
    assert core.get_youtube_value("latest_video_url") == "u"
    assert core.get_youtube_value("latest_video_title") == "t"
    assert core.get_youtube_value("latest_video_id") == "i"
    assert core.get_youtube_value("latest_video_channel") == "c"
    assert core.get_youtube_value("connection_status") == "Connected"
    assert core.get_youtube_value("channel_count") == "2"
    assert core.get_youtube_value("bogus") == "Unknown YouTube variable: bogus"


def test_youtube_global_defaults(monkeypatch):
    data = SimpleNamespace(latest_video_url="", latest_video_title=None, latest_video_id="",
                           latest_video_channel="", connection_status="", channels={})
    _yt(monkeypatch, data)
    assert core.get_youtube_value("latest_video_url") == "No video available"
    assert core.get_youtube_value("latest_video_title") == "No video available"
    assert core.get_youtube_value("latest_video_id") == "No video available"
    assert core.get_youtube_value("latest_video_channel") == "Unknown channel"
    assert core.get_youtube_value("connection_status") == "Disconnected"
    assert core.get_youtube_value("channel_count") == "0"


def test_youtube_channel_specific(monkeypatch):
    channel = {"channel_title": "My Chan-nel", "latest_video_url": "url1", "latest_video_title": "T1",
               "latest_video_id": "id1", "channel_url": "curl", "last_updated": "today"}
    data = SimpleNamespace(channels={"x": {"channel_title": "Other"}, "k": channel}, latest_video_url="",
                           latest_video_title="", latest_video_id="", latest_video_channel="",
                           connection_status="", )
    _yt(monkeypatch, data)
    assert core.get_youtube_value("mychannel_latest_video_url") == "url1"
    assert core.get_youtube_value("MyChannel_latest_video_title") == "T1"
    assert core.get_youtube_value("mychannel_latest_video_id") == "id1"
    assert core.get_youtube_value("mychannel_channel_title") == "My Chan-nel"
    assert core.get_youtube_value("mychannel_channel_url") == "curl"
    assert core.get_youtube_value("mychannel_last_updated") == "today"
    assert core.get_youtube_value("mychannel_nope") == "Unknown field: nope"
    assert core.get_youtube_value("ghost_latest_video_url") == "Channel not found: ghost"


def test_youtube_channel_defaults(monkeypatch):
    data = SimpleNamespace(channels={"k": {"channel_title": "abc"}})
    _yt(monkeypatch, data)
    assert core.get_youtube_value("abc_latest_video_url") == "No video available"
    assert core.get_youtube_value("abc_channel_url") == "No channel URL"
    assert core.get_youtube_value("abc_last_updated") == "Never updated"


def test_youtube_error(monkeypatch, caplog):
    _yt(monkeypatch, RuntimeError("boom"))
    assert core.get_youtube_value("latest_video_url") == "YouTube error: boom"


# --------------------------------------------------------------------------- quotes


class FakeQuoteManager:
    def __init__(self, quote=None, fail=False):
        self.quote = quote
        self.fail = fail
        self.asked = []

    def get_quote_by_number(self, n):
        if self.fail:
            raise RuntimeError("db")
        self.asked.append(("number", n))
        return self.quote

    def get_random_quote(self):
        self.asked.append(("random",))
        return self.quote


@pytest.fixture
def quote_mgr(monkeypatch):
    from modules import chatbot_manager

    fake = FakeQuoteManager(Quote(quote_id="q", quote_number=5, text="hello", author="Ann",
                                  added_by="mod", date_added=FIXED.timestamp()))
    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: fake)
    return fake


def test_quote_value_fields(quote_mgr):
    assert core.get_quote_value("5.text") == "hello"
    assert quote_mgr.asked[-1] == ("number", 5)
    assert core.get_quote_value("random.author") == "Ann"
    assert quote_mgr.asked[-1] == ("random",)
    assert core.get_quote_value("id.quote_number") == "5"
    assert core.get_quote_value("any.number") == "5"
    assert core.get_quote_value("5.date_added") == "2024-01-02"
    assert core.get_quote_value("5.added_by") == "mod"
    assert core.get_quote_value("5.formatted") == '"hello" - Ann'


def test_quote_value_unknown_and_missing(quote_mgr):
    assert core.get_quote_value("5.colour") is None
    assert core.get_quote_value("") is None
    assert core.get_quote_value(None) is None
    assert core.get_quote_value("latest.text") is None
    quote_mgr.quote = None
    assert core.get_quote_value("5.text") == ""


def test_quote_value_empty_optional_fields(quote_mgr):
    quote_mgr.quote = Quote(quote_number=1, text="t", author=None, added_by=None, date_added="bad")
    assert core.get_quote_value("1.author") == ""
    assert core.get_quote_value("1.added_by") == ""
    assert core.get_quote_value("1.date_added") == ""


def test_quote_value_manager_error(quote_mgr):
    quote_mgr.fail = True
    assert core.get_quote_value("3.text") == ""


# --------------------------------------------------------------------------- time formatting


@pytest.mark.parametrize("filters,expected", [
    ("", "19:05:07"),
    ("24", "19:05:07"),
    ("12", "07:05:07"),
    ("12.ampm", "07:05:07 PM"),
    ("12.ampm.noampm", "07:05:07"),
    ("nosec", "19:05"),
    ("noseconds.12", "07:05"),
    ("nosec.sec", "19:05:07"),
    ("seconds", "19:05:07"),
    ("hh:mm", "19:05"),
    ("h:mm.12", "7:05"),
    ("hh:mm:ss.12", "07:05:07"),
    ("h:mm:ss", "19:05:07"),
    ("..unknownthing..", "19:05:07"),
    (" 12 . AMPM ", "07:05:07 PM"),
])
def test_format_time_variable_filters(fixed_now, filters, expected):
    assert core.format_time_variable(filters) == expected


def test_format_time_tz_label_local(fixed_now):
    out = core.format_time_variable("tz")
    assert out.startswith("19:05:07 ")
    assert out.endswith("LOCAL")  # naive datetime has no %Z


def test_format_time_utc_with_pytz(fixed_now):
    pytest.importorskip("pytz")
    expected = FIXED.astimezone(timezone.utc).strftime("%H:%M:%S")
    assert core.format_time_variable("UTC.tz") == f"{expected} UTC"


def test_format_time_named_zone_with_pytz(fixed_now):
    pytz = pytest.importorskip("pytz")
    expected = FIXED.astimezone(pytz.timezone("US/Pacific")).strftime("%H:%M")
    assert core.format_time_variable("pst.nosec") == expected


def test_format_time_without_pytz_uses_fixed_offsets(fixed_now, monkeypatch):
    monkeypatch.setitem(sys.modules, "pytz", None)  # import raises ImportError
    expected = FIXED.astimezone(timezone(timedelta(hours=-5))).strftime("%H:%M:%S")
    assert core.format_time_variable("EST.tz") == f"{expected} EST"


def test_format_time_zone_conversion_error_is_ignored(fixed_now, monkeypatch):
    fake = types.ModuleType("pytz")
    fake.UTC = timezone.utc

    def bad(name):
        raise RuntimeError("unknown zone")

    fake.timezone = bad
    monkeypatch.setitem(sys.modules, "pytz", fake)
    assert core.format_time_variable("CST") == "19:05:07"


def test_format_time_layout_falls_back_on_platforms_without_dash_flags(monkeypatch):
    class NoDash(FixedDateTime):
        @classmethod
        def now(cls, tz=None):
            return NoDash(2024, 1, 2, 9, 5, 7)

        def strftime(self, fmt):
            if "%-" in fmt:
                raise ValueError("Invalid format string")
            return datetime.strftime(self, fmt)

    monkeypatch.setattr(core, "datetime", NoDash)
    assert core.format_time_variable("h:mm") == "09:05"
    assert core.format_time_variable("h:mm.12") == "09:05"


def test_format_time_unexpected_error_returns_plain_clock(monkeypatch, caplog):
    calls = []

    class Flaky(FixedDateTime):
        @classmethod
        def now(cls, tz=None):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("clock broke")
            return FIXED

    monkeypatch.setattr(core, "datetime", Flaky)
    assert core.format_time_variable("12") == "19:05:07"
    assert "Error formatting time" in caplog.text


def test_replace_time_variables(fixed_now):
    assert core.replace_time_variables("at {time} / {time.12.ampm} / {TIME.nosec}") == "at 19:05:07 / 07:05:07 PM / 19:05"
    assert core.replace_time_variables("no tokens") == "no tokens"


def test_module_patterns():
    assert core.COMMAND_WORD_PATTERN.findall("{command_word_1} {command_word_22}") == ["1", "22"]
    assert core.YOUTUBE_VAR_PATTERN.findall("{youtube.a}") == ["a"]
    assert core.STATS_VAR_PATTERN.findall("{stats.b.c}") == ["b.c"]
    assert core.CUSTOM_VAR_PATTERN.findall("{custom_x}") == ["x"]
    assert core.PROCESSED_VARS_PATTERN.findall("{a}{b}") == ["a", "b"]


# --------------------------------------------------------------------------- template engine


def test_clock_value_classes_print_their_formats(fixed_now):
    assert str(core._clock_value("timestamp")) == "2024-01-02 19:05:07"
    assert str(core._clock_value("date")) == "2024-01-02"
    assert str(core._clock_value("datetime")) == "2024-01-02 19:05"
    assert str(core._clock_value("time")) == "19:05:07"


def test_lookup_template_value_clock_and_sources(fixed_now, stats, quote_mgr, monkeypatch):
    found, value = core.lookup_template_value("DATE", {})
    assert found and str(value) == "2024-01-02"
    stats.data = {"x": 4}
    assert core.lookup_template_value("stats.x", {}) == (True, "4")
    monkeypatch.setattr(core, "get_youtube_value", lambda v: f"yt:{v}")
    assert core.lookup_template_value("youtube.latest", {}) == (True, "yt:latest")
    assert core.lookup_template_value("quote.5.text", {}) == (True, "hello")
    assert core.lookup_template_value("quote.5.bad", {}) == (False, None)


def test_lookup_template_value_custom_variables(monkeypatch):
    from modules import chatbot_custom_variables as ccv

    seen = []

    def evaluate(key, ctx, _depth=0):
        seen.append((key, _depth))
        return None if key == "custom_none" else f"val:{key}"

    monkeypatch.setattr(ccv, "evaluate_custom_variable", evaluate)
    assert core.lookup_template_value("custom_a", {}, depth=2) == (True, "val:custom_a")
    assert seen == [("custom_a", 3)]
    assert core.lookup_template_value("custom_none", {}) == (False, None)
    assert core.lookup_template_value("custom_deep", {}, depth=core._MAX_TEMPLATE_DEPTH + 1) == (False, None)
    assert len(seen) == 2


def test_lookup_template_value_command_words_and_context():
    ctx = {"command_args": ["a", "b"], "user": {"name": "Bob"}, "plain": 1}
    assert core.lookup_template_value("command_word_1", ctx) == (True, "a")
    assert core.lookup_template_value("command_word_2", ctx) == (True, "b")
    assert core.lookup_template_value("command_word_3", ctx) == (True, "")
    assert core.lookup_template_value("command_word_0", ctx) == (True, "")
    # Without command_args the token is a regular context key.
    assert core.lookup_template_value("command_word_1", {"command_word_1": "z"}) == (True, "z")
    assert core.lookup_template_value("plain", ctx) == (True, 1)
    assert core.lookup_template_value("user.name", ctx) == (True, "Bob")
    assert core.lookup_template_value("user.age", ctx) == (False, None)
    assert core.lookup_template_value("nothing", ctx) == (False, None)


def test_resolve_template_token(fixed_now):
    ctx = {"amount": 1234.5, "flag": False, "name": "Ann"}
    assert core.resolve_template_token("time", ctx) == "19:05:07"
    assert core.resolve_template_token("time.12", ctx) == "07:05:07"
    assert core.resolve_template_token("amount:,.2f", ctx) == "1,234.50"
    assert core.resolve_template_token("flag", ctx) == "false"
    assert core.resolve_template_token("name", ctx) == "Ann"
    assert core.resolve_template_token("bad key!", ctx) is None
    assert core.resolve_template_token("missing", ctx) is None
    assert core.resolve_template_token("date:%Y", ctx) == "2024"


def test_render_template_basic_cases(fixed_now):
    assert core.render_template(None) == ""
    assert core.render_template(42) == "42"
    assert core.render_template("no braces") == "no braces"
    assert core.render_template("hi {name}!", {"name": "Bob"}) == "hi Bob!"
    assert core.render_template("{unknown} stays", {}) == "{unknown} stays"
    assert core.render_template("{name}", None) == "{name}"
    assert core.render_template("{none}", {"none": None}) == ""


def test_render_template_is_single_pass():
    ctx = {"message": "{stats.followers} {name}", "name": "X"}
    assert core.render_template("said: {message}", ctx) == "said: {stats.followers} {name}"


def test_resolve_raw_template_value():
    when = datetime(2000, 1, 1)
    ctx = {"born": when, "n": 3}
    assert core.resolve_raw_template_value(" {born} ", ctx) is when
    assert core.resolve_raw_template_value("{born:%Y}", ctx) == "2000"
    assert core.resolve_raw_template_value("{missing}", ctx) == "{missing}"
    assert core.resolve_raw_template_value("n={n}", ctx) == "n=3"
    assert core.resolve_raw_template_value(None, ctx) == ""


# --------------------------------------------------------------------------- permissions


@pytest.mark.parametrize("value,mod_only,expected", [
    ("mod", False, "moderator"), ("MODS", False, "moderator"), ("moderators", False, "moderator"),
    ("sub", False, "subscriber"), ("subs", False, "subscriber"), ("subscribers", False, "subscriber"),
    ("vips", False, "vip"), ("all", False, "everyone"), ("everyone", True, "everyone"),
    ("broadcaster", False, "moderator"), ("junk", False, "everyone"), ("junk", True, "moderator"),
    (None, False, "everyone"), (None, True, "moderator"), (" VIP ", False, "vip"),
])
def test_normalize_permission(value, mod_only, expected):
    assert core.normalize_permission(value, mod_only) == expected


def test_badge_set_ids_formats():
    assert core.badge_set_ids(None) == set()
    assert core.badge_set_ids("") == set()
    assert core.badge_set_ids("Broadcaster/1, subscriber/12,") == {"broadcaster", "subscriber"}
    assert core.badge_set_ids({"vip": "1", "Moderator": "1"}) == {"vip", "moderator"}
    assert core.badge_set_ids([{"set_id": "founder"}, {"id": "VIP"}, {"x": 1}, "sub/3",
                               SimpleNamespace(set_id="turbo"), SimpleNamespace()]) == {"founder", "vip", "sub", "turbo"}
    assert core.badge_set_ids(("a/1",)) == {"a"}
    assert core.badge_set_ids({"x/1"}) == {"x"}
    assert core.badge_set_ids(12345) == set()


def test_broadcaster_user_id(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.twitch", SimpleNamespace(twitch_api=SimpleNamespace(user_id=99)))
    assert core._broadcaster_user_id() == "99"
    monkeypatch.setitem(sys.modules, "modules.twitch", SimpleNamespace(twitch_api=None))
    assert core._broadcaster_user_id() == ""
    monkeypatch.delitem(sys.modules, "modules.twitch")
    assert core._broadcaster_user_id() == ""


@pytest.mark.parametrize("data,level", [
    ({"badges": "broadcaster/1"}, "broadcaster"),
    ({"is_broadcaster": True}, "broadcaster"),
    ({"user_id": "777"}, "broadcaster"),
    ({"userid": 777}, "broadcaster"),
    ({"badges": "lead_moderator/1"}, "moderator"),
    ({"is_mod": True}, "moderator"),
    ({"is_moderator": True}, "moderator"),
    ({"badges": "vip/1"}, "vip"),
    ({"is_vip": 1}, "vip"),
    ({"badges": "founder/0"}, "subscriber"),
    ({"is_subscriber": True}, "subscriber"),
    ({"user_id": "5"}, "everyone"),
    ({}, "everyone"),
    (None, "everyone"),
])
def test_user_permission_level(monkeypatch, data, level):
    monkeypatch.setattr(core, "_broadcaster_user_id", lambda: "777")
    assert core.user_permission_level(data) == level


def test_permission_allows(monkeypatch):
    monkeypatch.setattr(core, "_broadcaster_user_id", lambda: "")
    assert core.permission_allows("everyone", {})
    assert not core.permission_allows("subscriber", {})
    assert core.permission_allows("subscriber", {"is_vip": True})
    assert not core.permission_allows("moderator", {"is_vip": True})
    assert core.permission_allows("moderator", {"badges": "broadcaster/1"})
    assert core.permission_allows("junk", {})


# --------------------------------------------------------------------------- normalizers


def test_normalize_reply_targets():
    assert core._normalize_reply_targets(None) == ["twitch"]
    assert core._normalize_reply_targets(None, default=["youtube"]) == ["youtube"]
    assert core._normalize_reply_targets("YouTube") == ["youtube"]
    assert core._normalize_reply_targets(5) == ["twitch"]
    assert core._normalize_reply_targets(["twitch", "TWITCH", "youtube", None]) == ["twitch", "youtube"]
    assert core._normalize_reply_targets(["discord", "all"]) == ["twitch", "youtube"]
    assert core._normalize_reply_targets(["junk"]) == ["twitch"]
    assert core._normalize_reply_targets([], allow_empty=True) == []
    assert core._normalize_reply_targets(("youtube",)) == ["youtube"]


def test_normalize_output_mode():
    assert core._normalize_output_mode("BANNER ") == "banner"
    assert core._normalize_output_mode(None) == "chat"
    assert core._normalize_output_mode("other") == "chat"


def test_resolve_output():
    assert core._resolve_output({}) == (["twitch"], "chat", [])
    assert core._resolve_output({"reply_targets": [], "discord_channels": ["c1"]}) == ([], "chat", ["c1"])
    assert core._resolve_output({"reply_targets": [], "output_mode": "banner"}) == ([], "banner", [])
    assert core._resolve_output({"reply_targets": [], "discord_channels": "notalist"}) == (["twitch"], "chat", [])


@pytest.mark.parametrize("value,default,expected", [
    (True, 0, 1), (False, 0, 0), (3, 0, 3), (2.5, 0, 2.5), (" 4 ", 0, 4), ("4.5", 0, 4.5),
    ("abc", 7, 7), (None, 9, 9), ([1], 1, 1),
])
def test_to_number(value, default, expected):
    out = core._to_number(value, default)
    assert out == expected and type(out) is type(expected)


def test_to_int():
    assert core._to_int("3.9") == 3
    assert core._to_int("x", 5) == 5
    assert core._to_int(float("nan"), 2) == 2  # int(nan) raises ValueError


@pytest.mark.xfail(strict=True, reason="BUG: _to_int lets OverflowError escape for 'inf'/'1e999', so a stored "
                                       "usage_limit/counter of inf crashes ChatCommand/ChatEvent loading")
def test_to_int_infinite_values_fall_back_to_default():
    assert core._to_int("inf", 2) == 2
    assert ChatCommand(command_name="x", usage_limit="1e999").usage_limit == 0


@pytest.mark.parametrize("value,expected", [
    ("!Hello world", "hello"), ("  !!Hi ", "hi"), ("", ""), (None, ""), ("!", ""),
    ("<ValueChangeEventArguments x>", ""), (42, "42"),
])
def test_clean_command_word(value, expected):
    assert core.clean_command_word(value) == expected


def test_clean_aliases():
    assert core.clean_aliases(None) == []
    assert core.clean_aliases("!a, b  c,,A") == ["a", "b", "c"]
    assert core.clean_aliases("ValueChangeEventArguments(...)") == []
    assert core.clean_aliases(["!X", "x", "y", "", None], exclude="y") == ["x"]
    assert core.clean_aliases(("q",)) == ["q"]
    assert core.clean_aliases(5) == []


def test_normalize_argument_mappings():
    assert core.normalize_argument_mappings("nope") == {}
    raw = {"{target}": 1, "amount": {"position": "2", "default": "{username}"}, "bad key": 1,
           "": 2, "zero": 0, "neg": {"position": -1}, "nodefault": {"position": 3, "default": None}}
    assert core.normalize_argument_mappings(raw) == {
        "target": {"position": 1, "default": ""},
        "amount": {"position": 2, "default": "{username}"},
        "nodefault": {"position": 3, "default": ""},
    }


def test_serialize_argument_mappings():
    assert core.serialize_argument_mappings(None) == {}
    assert core.serialize_argument_mappings({"a": {"position": 1, "default": ""},
                                             "b": {"position": "2", "default": 5}}) == {
        "a": 1, "b": {"position": 2, "default": "5"}}


def test_normalize_lookup():
    assert core.normalize_lookup(None) is None
    assert core.normalize_lookup({"id": "  "}) is None
    out = core.normalize_lookup({"target": "user", "id": " Follow_Age "})
    assert out == {"id": "follow_age", "target": "user"}
    assert list(out)[0] == "id"


@pytest.mark.parametrize("value,expected", [
    ("9:05", "09:05"), (" 23:59 ", "23:59"), ("24:00", None), ("12:60", None), ("noon", None),
    (None, None), ("1:5", None),
])
def test_normalize_specific_time(value, expected):
    assert core.normalize_specific_time(value) == expected


# --------------------------------------------------------------------------- split chat message


def test_split_short_and_empty():
    assert core.split_chat_message("") == []
    assert core.split_chat_message(None) == []
    assert core.split_chat_message("  hi  ") == ["hi"]


def test_split_at_word_boundaries():
    text = " ".join(["word"] * 30)  # 149 chars
    parts = core.split_chat_message(text, limit=50, max_parts=5)
    assert all(len(p) <= 50 for p in parts)
    assert " ".join(parts) == text


def test_split_hard_cuts_words_without_spaces():
    parts = core.split_chat_message("x" * 120, limit=50, max_parts=5)
    assert parts == ["x" * 50, "x" * 50, "x" * 20]


def test_split_truncates_with_ellipsis_at_max_parts():
    text = " ".join(["abcdefghi"] * 40)
    parts = core.split_chat_message(text, limit=50, max_parts=2)
    assert len(parts) == 2
    assert parts[-1].endswith("…") and len(parts[-1]) <= 50
    assert not parts[-1][:-1].endswith(" ")


def test_split_truncates_hard_when_last_space_is_early():
    text = "a " + "y" * 200
    parts = core.split_chat_message(text, limit=50, max_parts=1)
    assert parts == ["a " + "y" * 47 + "…"]


# --------------------------------------------------------------------------- small helpers


def test_clean_event_data():
    out = core._clean_event_data({"message": None, "badges": None, "username": None, "user_login": None,
                                  "user_id": None, "userid": 12, "other": None})
    assert out == {"message": "", "badges": "", "username": "", "user_login": "", "user_id": "12",
                   "userid": 12, "other": None}
    assert core._clean_event_data(None) == {}
    assert core._clean_event_data({"user_id": "5", "userid": 6})["user_id"] == "5"


@pytest.mark.parametrize("lookup_vars,fallback,expected", [
    (None, "", "normal"), ({}, "x", "normal"), ({"lookup_ok": True}, "", "normal"),
    ({"lookup_ok": False}, "fallback", "fallback"),
    ({"lookup_ok": False, "lookup_reason": "not_following"}, "  ", "normal"),
    ({"lookup_ok": False, "lookup_reason": "error"}, "", "silent"),
])
def test_choose_lookup_outcome(lookup_vars, fallback, expected):
    assert core.choose_lookup_outcome(lookup_vars, fallback) == expected


def test_sanitize_value():
    class WithValue:
        value = EventType.BITS

    class Unprintable:
        def __str__(self):
            raise RuntimeError("no")

    assert core._sanitize_value(None) is None
    assert core._sanitize_value((1, "a", [True])) == [1, "a", [True]]
    assert core._sanitize_value({1: {2: 3.5}}) == {"1": {"2": 3.5}}
    assert core._sanitize_value(CommandType.COUNTER) == "counter"
    assert core._sanitize_value(WithValue()) == "bits"
    assert core._sanitize_value(datetime(2020, 1, 1)) == "2020-01-01 00:00:00"
    assert core._sanitize_value(Unprintable()) == ""


def test_track_stat(stats):
    core._track_stat("command", "bob", "hello")
    core._track_stat("event", "amy", "Follow")
    assert stats.calls == [("command", {"username": "bob", "command_name": "hello"}),
                           ("event", {"username": "amy", "event_name": "Follow"})]


def test_track_stat_errors_are_logged(stats, caplog):
    stats.fail = True
    core._track_stat("command", "bob", "hello")
    assert "Error tracking command statistics" in caplog.text


def test_run_lookup_for(monkeypatch):
    from modules import chatbot_lookups

    seen = []
    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking",
                        lambda lookup, ctx, kind: seen.append((lookup, ctx, kind)) or {"lookup_ok": True})
    assert core._run_lookup_for(SimpleNamespace(lookup=None), {}) is None
    cmd = ChatCommand(command_name="x", lookup={"id": "weather"})
    evt = ChatEvent(name="e", lookup={"id": "follow_age"})
    assert core._run_lookup_for(cmd, {"a": 1}) == {"lookup_ok": True}
    core._run_lookup_for(evt, {})
    assert seen[0] == ({"id": "weather"}, {"a": 1}, "command")
    assert seen[1][2] == "event"


# --------------------------------------------------------------------------- conditions


def test_trigger_condition_evaluate():
    c = TriggerCondition("amount", ComparisonOperator.GREATER_THAN_OR_EQUAL, 100)
    assert c.evaluate({"amount": "150"})
    assert not c.evaluate({"amount": 50})
    assert not c.evaluate({})
    assert not c.evaluate({"amount": None})
    assert not c.evaluate("not a dict")
    assert TriggerCondition("msg", ComparisonOperator.CONTAINS, "HI").evaluate({"msg": "oh hi there"})
    assert TriggerCondition("msg", ComparisonOperator.EQUAL, "Abc").evaluate({"msg": "aBC"})


def test_trigger_condition_evaluate_bad_operator_is_false(caplog):
    c = TriggerCondition("x", "bogus-op", 1)
    assert c.evaluate({"x": 1}) is False


def test_trigger_condition_to_dict():
    c = TriggerCondition("f", ComparisonOperator.EQUAL, [1, 2])
    assert c.to_dict() == {"field": "f", "operator": "equal", "value": "[1, 2]"}
    assert TriggerCondition(5, "raw", None).to_dict() == {"field": "5", "operator": "raw", "value": None}
    assert TriggerCondition("f", ComparisonOperator.LESS_THAN, 2.5).to_dict()["value"] == 2.5


def test_trigger_condition_from_any():
    existing = TriggerCondition("a", ComparisonOperator.EQUAL, 1)
    assert TriggerCondition.from_any(existing) is existing
    assert TriggerCondition.from_any("x") is None
    assert TriggerCondition.from_any({"field": ""}) is None
    assert TriggerCondition.from_any({"field": "a", "operator": "nope"}) is None
    c = TriggerCondition.from_any({"field": "a", "operator": "ends_with", "value": "z"})
    assert (c.field, c.operator, c.value) == ("a", ComparisonOperator.ENDS_WITH, "z")


def test_parse_conditions():
    out = core._parse_conditions([{"field": "a", "operator": "equal", "value": 1}, {"bad": 1}, None])
    assert len(out) == 1
    assert core._parse_conditions(None) == []


# --------------------------------------------------------------------------- ChatCommand


def test_command_init_normalizes(monkeypatch):
    c = ChatCommand(command_name="!Hello there", aliases="!hi, hello, HEY", permission=None, mod_only=True,
                    cooldown="-5", command_type="nonsense", usage_limit="3", api_headers='{"A": "1"}',
                    counter_value="7", conditions=[{"field": "x", "operator": "equal", "value": 1}],
                    argument_mappings={"a": 1}, reply_targets=["youtube"], response_text=None)
    assert c.command_name == "hello"
    assert c.aliases == ["hi", "hey"]
    assert c.permission == "moderator" and c.mod_only
    assert c.cooldown == 0
    assert c.command_type is CommandType.BASIC
    assert c.usage_limit == 3
    assert c.api_headers == {"A": "1"}
    assert c.counter_value == 7
    assert len(c.conditions) == 1
    assert c.argument_mappings == {"a": {"position": 1, "default": ""}}
    assert c.reply_targets == ["youtube"]
    assert c.response_text == ""


def test_command_init_bad_headers_and_enum_type():
    assert ChatCommand(api_headers="{not json").api_headers == {}
    assert ChatCommand(api_headers=["x"]).api_headers == {}
    assert ChatCommand(command_type=CommandType.COUNTER).command_type is CommandType.COUNTER
    assert ChatCommand(command_type="reset").command_type is CommandType.RESET


def test_command_legacy_api_fields_migrate_to_lookup(monkeypatch):
    from modules import chatbot_lookups

    monkeypatch.setattr(chatbot_lookups, "migrate_legacy_api_fields", lambda cmd: {"id": "custom_api", "url": cmd.api_endpoint})
    c = ChatCommand(api_enabled=True, api_endpoint="https://example.invalid/x")
    assert c.lookup == {"id": "custom_api", "url": "https://example.invalid/x"}
    assert c.api_enabled is False
    # An explicit lookup wins and legacy fields stay as they are.
    c2 = ChatCommand(api_enabled=True, api_endpoint="u", lookup={"id": "weather"})
    assert c2.lookup == {"id": "weather"} and c2.api_enabled is True


def test_command_mod_only_setter():
    c = ChatCommand(permission="vip")
    c.mod_only = False
    assert c.permission == "vip"
    c.mod_only = True
    assert c.permission == "moderator"
    c.mod_only = False
    assert c.permission == "everyone"


def test_command_all_names_and_should_trigger():
    c = ChatCommand(command_name="a", aliases=["b"])
    assert c.all_names() == ["a", "b"]
    assert ChatCommand(command_name="", aliases=["z"]).all_names() == ["z"]
    assert c.should_trigger({})
    c.conditions = [TriggerCondition("x", ComparisonOperator.EQUAL, 1)]
    assert not c.should_trigger({"x": 2})
    assert c.should_trigger({"x": 1})
    c.enabled = False
    assert not c.should_trigger({"x": 1})


def test_command_can_use(monkeypatch):
    monkeypatch.setattr(core, "_broadcaster_user_id", lambda: "")
    monkeypatch.setattr(core.time, "time", lambda: 1000.0)
    c = ChatCommand(command_name="a")
    assert c.can_use({}) == (True, "")
    c.enabled = False
    assert c.can_use({}) == (False, "Command is disabled")
    c.enabled = True
    c.permission = "subscriber"
    assert c.can_use({}) == (False, "Requires subscriber")
    c.permission = "everyone"
    c.cooldown = 30
    c.last_used = 990.0
    assert c.can_use({}) == (False, "Command is on cooldown (20s remaining)")
    c.last_used = 900.0
    c.usage_limit = 2
    c.usage_count = 2
    assert c.can_use({}) == (False, "Command has reached its usage limit")


def test_command_begin_use(monkeypatch, stats):
    monkeypatch.setattr(core.time, "time", lambda: 50.0)
    c = ChatCommand(command_name="deaths", command_type="counter")
    c.begin_use({"username": "bob"})
    assert (c.usage_count, c.trigger_count, c.counter_value, c.last_used, c.last_triggered) == (1, 1, 1, 50.0, 50.0)
    assert stats.calls == [("command", {"username": "bob", "command_name": "deaths"})]
    basic = ChatCommand(command_name="b")
    basic.begin_use(None, track_stats=False)
    assert basic.counter_value == 0 and len(stats.calls) == 1


def test_command_build_context_from_message():
    c = ChatCommand(command_name="so", cooldown=2.5, usage_limit=5, usage_count=2,
                    argument_mappings={"target": 1, "extra": {"position": 3, "default": "def-{username}"}, "none": 4})
    ctx = c.build_context({"message": "!so Alice Bob", "username": "Mod", "user_id": None, "userid": 9},
                          lookup_vars={"username": "lookup-loses", "lookup_ok": True})
    assert ctx["username"] == "Mod" and ctx["lookup_ok"] is True
    assert ctx["user_login"] == "mod"
    assert ctx["command_args"] == ["Alice", "Bob"]
    assert ctx["command_message"] == "Alice Bob"
    assert ctx["command_first_word"] == "Alice" and ctx["command_last_word"] == "Bob"
    assert ctx["chatter_id"] == "9" and ctx["chatter_login"] == "mod"
    assert ctx["command_name"] == "so" and ctx["cooldown"] == "2.5"
    assert ctx["usage_left"] == "3"
    assert ctx["target"] == "Alice" and ctx["extra"] == "def-Mod" and ctx["none"] == ""
    assert "count" not in ctx


def test_command_build_context_defaults_and_types():
    counter = ChatCommand(command_name="c", command_type="counter", counter_value=4)
    ctx = counter.build_context({})
    assert ctx["username"] == "Unknown" and ctx["user_login"] == "unknown"
    assert ctx["command_args"] == [] and ctx["command_first_word"] == "" and ctx["command_last_word"] == ""
    assert ctx["usage_left"] == "unlimited" and ctx["count"] == 4
    # message without a leading ! has no args
    assert counter.build_context({"message": "hello there"})["command_args"] == []
    # explicit command_args win and are stringified
    assert counter.build_context({"command_args": (1, 2)})["command_args"] == ["1", "2"]
    reset = ChatCommand(command_name="r", command_type="reset")
    rctx = reset.build_context({})
    assert rctx["target_command"] == "" and rctx["target_count"] == ""
    assert reset.build_context({"target_command": "deaths"})["target_command"] == "deaths"


def test_command_render_and_fallback():
    c = ChatCommand(command_name="w", response_text="Hi {username}", lookup_fallback="Sorry {username}")
    assert c.render_response({"username": "A"}) == "Hi A"
    assert c.render_response({"username": "A"}, use_fallback=True) == "Sorry A"
    assert c.respond_after_lookup({"username": "A"}, {"lookup_ok": False}) == "Sorry A"
    assert c.respond_after_lookup({"username": "A"}, {"lookup_ok": True}) == "Hi A"
    c.lookup_fallback = ""
    assert c.respond_after_lookup({"username": "A"}, {"lookup_ok": False, "lookup_error": "x"}) == ""


def test_command_use_command(monkeypatch, stats):
    c = ChatCommand(command_name="w", response_text="T={temp}", lookup_fallback="no data")
    monkeypatch.setattr(core, "_run_lookup_for", lambda item, ctx: None)
    assert c.use_command({"username": "a"}) == "T={temp}"
    assert c.usage_count == 1
    monkeypatch.setattr(core, "_run_lookup_for", lambda item, ctx: {"lookup_ok": True, "temp": 20})
    assert c.use_command({"username": "a"}) == "T=20"
    monkeypatch.setattr(core, "_run_lookup_for", lambda item, ctx: {"lookup_ok": False})
    assert c.use_command({"username": "a"}) == "no data"


def test_command_reset_counter(caplog):
    import logging

    caplog.set_level(logging.INFO, logger=core.logger.name)
    c = ChatCommand(command_name="d", counter_value=9)
    c.reset_counter()
    assert c.counter_value == 0
    assert "Reset counter for command d" in caplog.text


def test_command_to_dict_round_trip():
    c = ChatCommand(command_id="id", name="N", command_name="n", aliases=["m"], permission="vip",
                    command_type="counter", argument_mappings={"a": {"position": 1, "default": "x"}},
                    conditions=[{"field": "f", "operator": "equal", "value": 1}], discord_channels=["d"],
                    reply_targets=["twitch", "youtube"], output_mode="banner", lookup={"id": "x"})
    d = c.to_dict()
    assert d["command_type"] == "counter" and d["permission"] == "vip" and d["mod_only"] is False
    assert d["argument_mappings"] == {"a": {"position": 1, "default": "x"}}
    assert d["conditions"] == [{"field": "f", "operator": "equal", "value": 1}]
    assert d["schema_version"] == core.SCHEMA_VERSION
    assert d["output_mode"] == "banner" and d["discord_channels"] == ["d"]
    again = ChatCommand.from_dict(d)
    assert again.to_dict() == d


def test_command_from_dict_migrates_schema_one():
    old = ChatCommand.from_dict({"command_name": "x", "persistent_counter": False})
    assert old.persistent_counter is True
    new = ChatCommand.from_dict({"command_name": "x", "persistent_counter": False, "schema_version": 2})
    assert new.persistent_counter is False
    assert ChatCommand.from_dict(None).command_name == ""


# --------------------------------------------------------------------------- events


def test_number_helpers():
    assert core._number_or_none("1,234") == 1234.0
    assert core._number_or_none("x") is None
    assert core._at_least({"a": "", "b": 5}, ("a", "b"), 5)
    assert not core._at_least({"a": "x"}, ("a",), 1)
    assert not core._at_least({}, ("a",), 1)
    assert not core._at_least({"a": 2}, ("a", "b"), 3)
    assert core._equals_number({"tier": "2"}, "tier", 2)
    assert not core._equals_number({}, "tier", 2)


@pytest.mark.parametrize("message,pattern,mode,expected", [
    ("Hello", "", "exact", True),
    (" HELLO ", "hello", "exact", True),
    ("hello there", "hello", "exact", False),
    ("hello there", "HELLO", "starts_with", True),
    ("oh hello", "hello", "starts_with", False),
    ("oh hello there", "hello", "contains", True),
    (None, "x", "contains", False),
])
def test_chat_text_matches(message, pattern, mode, expected):
    assert core.chat_text_matches(message, pattern, mode) is expected


def test_event_init_normalizes():
    e = ChatEvent(event_type="nope", interval="-3", specific_time="7:30", chat_message_match_type="regex",
                  donation_amount="2.5", bits_quantity="100", response_text=None)
    assert e.event_type is EventType.FOLLOW
    assert e.interval == 0
    assert e.specific_time == "07:30"
    assert e.chat_message_match_type == "exact"
    assert e.donation_amount == 2.5 and e.bits_quantity == 100
    assert e.response_text == ""
    assert ChatEvent(specific_time="bad").specific_time == "bad"
    assert ChatEvent(event_type=EventType.RAID).event_type is EventType.RAID
    assert ChatEvent(chat_message_match_type="contains").chat_message_match_type == "contains"


def test_event_repeats():
    assert ChatEvent(event_type="interval", interval=60).repeats()
    assert not ChatEvent(event_type="interval", interval=0).repeats()
    assert not ChatEvent(event_type="follow", interval=60).repeats()


def test_event_should_trigger_disabled_and_conditions():
    e = ChatEvent(event_type="follow", enabled=False)
    assert not e.should_trigger({})
    e = ChatEvent(event_type="follow", conditions=[{"field": "x", "operator": "equal", "value": 1}])
    assert not e.should_trigger({"x": 2})
    assert e.should_trigger({"x": 1})
    assert ChatEvent(event_type="follow").should_trigger(None)


def test_event_should_trigger_specific_time_and_chat():
    assert ChatEvent(event_type="specific_time", specific_time="10:00").should_trigger({})
    assert not ChatEvent(event_type="specific_time", specific_time="").should_trigger({})
    chat = ChatEvent(event_type="chat_message", chat_message_text="!lurk", chat_message_match_type="starts_with")
    assert chat.should_trigger({"message": "!LURK now"})
    assert not chat.should_trigger({"message": "hi"})
    yt = ChatEvent(event_type="youtube_chat_message", chat_message_text="hey", chat_message_match_type="contains")
    assert yt.should_trigger({"message": "oh hey"})


@pytest.mark.parametrize("kwargs,data,expected", [
    ({"event_type": "bits"}, {}, True),
    ({"event_type": "bits", "bits_quantity": 100}, {"amount": 150}, True),
    ({"event_type": "bits", "bits_quantity": 100}, {"amount": 50}, False),
    ({"event_type": "hype_train_progress"}, {}, True),
    ({"event_type": "hype_train_progress", "hype_train_level": 3}, {"level": 3}, True),
    ({"event_type": "hype_train_progress", "hype_train_level": 3}, {"level": 2}, False),
    ({"event_type": "hype_train_end", "hype_train_end_level": 2}, {"level": 5}, True),
    ({"event_type": "hype_train_end", "hype_train_end_level": 2}, {"level": 1}, False),
    ({"event_type": "hype_train_end"}, {}, True),
    ({"event_type": "gift_subscription"}, {}, True),
    ({"event_type": "gift_subscription", "gift_sub_quantity": 5}, {"quantity": 5}, True),
    ({"event_type": "gift_subscription", "gift_sub_quantity": 5}, {"gift_qty": "10"}, True),
    ({"event_type": "gift_subscription", "gift_sub_quantity": 5}, {"amount": 1}, False),
    ({"event_type": "gift_subscription", "gift_sub_tier": 2}, {"tier": 2}, True),
    ({"event_type": "gift_subscription", "gift_sub_tier": 2}, {"tier": 1}, False),
    ({"event_type": "resubscription"}, {}, True),
    ({"event_type": "resubscription", "resub_months": 12}, {"cumulative_months": 13}, True),
    ({"event_type": "resubscription", "resub_months": 12}, {"months": 2}, False),
    ({"event_type": "resubscription", "resub_tier": 3}, {"tier": 3}, True),
    ({"event_type": "resubscription", "resub_tier": 3}, {"tier": 1}, False),
    ({"event_type": "subscription"}, {}, True),
    ({"event_type": "subscription", "sub_tier": 1}, {"tier": "1"}, True),
    ({"event_type": "subscription", "sub_tier": 1}, {"tier": 2}, False),
    ({"event_type": "donation"}, {}, True),
    ({"event_type": "donation", "donation_amount": 5}, {"amount": "5.00"}, True),
    ({"event_type": "donation", "donation_amount": 5}, {"amount": 4.99}, False),
    ({"event_type": "raid"}, {}, True),
    ({"event_type": "raid", "raid_viewer_count": 10}, {"viewer_count": 10}, True),
    ({"event_type": "raid", "raid_viewer_count": 10}, {"viewer_count": 9}, False),
    ({"event_type": "raid", "raid_raider_name": "@Bob"}, {"raider_name": "bob"}, True),
    ({"event_type": "raid", "raid_raider_name": "bob"}, {"user_login": "@BOB"}, True),
    ({"event_type": "raid", "raid_raider_name": "bob"}, {"username": "amy"}, False),
    ({"event_type": "channel_point_redemption"}, {}, True),
    ({"event_type": "channel_point_redemption", "channel_point_reward_name": "Hydrate"},
     {"reward_title": " hydrate "}, True),
    ({"event_type": "channel_point_redemption", "channel_point_reward_name": "Hydrate"},
     {"reward_name": "Hydrate"}, True),
    ({"event_type": "channel_point_redemption", "channel_point_reward_name": "Hydrate"},
     {"reward_title": "Dance"}, False),
    ({"event_type": "youtube_superchat"}, {}, True),
])
def test_event_type_filters(kwargs, data, expected):
    assert ChatEvent(**kwargs).should_trigger(data) is expected


def test_event_begin_trigger(monkeypatch, stats):
    monkeypatch.setattr(core.time, "time", lambda: 77.0)
    e = ChatEvent(name="Follow")
    e.begin_trigger({"username": "x"})
    assert e.trigger_count == 1 and e.last_triggered == 77.0
    assert stats.calls == [("event", {"username": "x", "event_name": "Follow"})]
    e.begin_trigger(None, track_stats=False)
    assert e.trigger_count == 2 and len(stats.calls) == 1


def test_event_build_context():
    e = ChatEvent(name="Raid", argument_mappings={"first": 1, "third": {"position": 3, "default": "d:{username}"},
                                                  "blank": 4})
    ctx = e.build_context({"username": "Bob", "command_args": ["x", 2]}, {"lookup_ok": True})
    assert ctx["event_name"] == "Raid" and ctx["lookup_ok"]
    assert ctx["command_args"] == ["x", "2"] and ctx["command_message"] == "x 2"
    assert ctx["command_first_word"] == "x" and ctx["command_last_word"] == "2"
    assert ctx["first"] == "x" and ctx["third"] == "d:Bob" and ctx["blank"] == ""
    plain = e.build_context({})
    assert plain["username"] == "Unknown" and plain["user_login"] == "unknown"
    assert "command_args" not in plain and plain["first"] == ""
    empty_args = e.build_context({"command_args": []})
    assert empty_args["command_first_word"] == "" and empty_args["command_last_word"] == ""


def test_event_render_and_lookup_outcomes(monkeypatch, stats):
    e = ChatEvent(name="F", response_text="Thanks {username}", lookup_fallback="Fallback {username}")
    assert e.render_response({"username": "A"}) == "Thanks A"
    assert e.render_response({"username": "A"}, use_fallback=True) == "Fallback A"
    assert e.respond_after_lookup({"username": "A"}, {"lookup_ok": False}) == "Fallback A"
    e.lookup_fallback = ""
    assert e.respond_after_lookup({"username": "A"}, {"lookup_ok": False}) == ""
    monkeypatch.setattr(core, "_run_lookup_for", lambda item, ctx: None)
    assert e.trigger_event({"username": "B"}) == "Thanks B"
    monkeypatch.setattr(core, "_run_lookup_for", lambda item, ctx: {"lookup_ok": True})
    assert e.trigger_event({"username": "C"}) == "Thanks C"
    assert e.trigger_count == 2


@pytest.mark.parametrize("text,ok,interval", [
    ("", True, 0), ("   ", True, 0), (None, True, 0), ("01:02:03", True, 3723), ("10:00", False, None),
    ("a:b:c", False, None), ("00:60:00", False, None), ("00:00:60", False, None), ("-1:00:00", False, None),
    (123, False, None),
])
def test_event_set_interval_from_string(text, ok, interval):
    e = ChatEvent(interval=5)
    assert e.set_interval_from_string(text) is ok
    assert e.interval == (interval if ok else 5)


def test_event_get_interval_string():
    assert ChatEvent(interval=0).get_interval_string() == ""
    assert ChatEvent(interval=3723).get_interval_string() == "01:02:03"
    assert ChatEvent(interval=90061.7).get_interval_string() == "25:01:01"


def test_event_to_dict_round_trip():
    e = ChatEvent(event_id="e", name="N", event_type="raid", interval=10, raid_raider_name="x",
                  conditions=[{"field": "f", "operator": "contains", "value": "a"}], lookup={"id": "y"},
                  argument_mappings={"a": 2}, reply_targets="youtube")
    d = e.to_dict()
    assert d["event_type"] == "raid" and d["reply_targets"] == ["youtube"]
    assert d["argument_mappings"] == {"a": 2} and d["schema_version"] == core.SCHEMA_VERSION
    assert ChatEvent.from_dict(d).to_dict() == d
    assert ChatEvent.from_dict(None).event_type is EventType.FOLLOW


# --------------------------------------------------------------------------- quotes / greetings


def test_quote_model():
    q = Quote(quote_id="q", quote_number=2, text="Hi", author="", date_added=5, added_by="m")
    assert q.format_quote() == '"Hi"'
    q.author = "Ann"
    assert q.format_quote() == '"Hi" - Ann'
    assert q.get_search_text() == "hi ann"
    d = q.to_dict()
    assert d == {"quote_id": "q", "quote_number": 2, "text": "Hi", "author": "Ann", "date_added": 5, "added_by": "m"}
    assert Quote.from_dict(d).to_dict() == d


def test_greeting_model(monkeypatch):
    monkeypatch.setattr(core.time, "time", lambda: 10_000.0)
    g = Greeting(greeting_id="g", user_id="1", username="Bob", greeting_text="Yo")
    assert g.date_created == 10_000.0
    assert g.get_display_text() == "@Bob: Yo"
    assert g.should_greet(0)
    g.update_last_greeted()
    assert g.last_greeted == 10_000.0
    assert not g.should_greet(10_000.0 + 3600, cooldown_hours=24)
    assert g.should_greet(10_000.0 + 24 * 3600, cooldown_hours=24)
    g.update_last_greeted(5.0)
    assert g.last_greeted == 5.0
    g.enabled = False
    assert not g.should_greet(1e9)
    monkeypatch.setattr(core.time, "time", lambda: 20_000.0)
    g.update_username("Bob")
    assert g.last_username_update == 10_000.0
    g.update_username("Robert")
    assert g.username == "Robert" and g.last_username_update == 20_000.0
    d = g.to_dict()
    assert Greeting.from_dict(d).to_dict() == d


def test_enums_have_expected_values():
    assert CommandType("counter") is CommandType.COUNTER
    assert EventType("youtube_supersticker") is EventType.YOUTUBE_SUPERSTICKER
    assert ComparisonOperator("starts_with") is ComparisonOperator.STARTS_WITH
    assert core.VariableType("usage_left") is core.VariableType.USAGE_LEFT
    assert core.PERMISSION_CHOICES == ("everyone", "subscriber", "vip", "moderator")
    assert core.PERMISSION_LEVELS["broadcaster"] > core.PERMISSION_LEVELS["moderator"]

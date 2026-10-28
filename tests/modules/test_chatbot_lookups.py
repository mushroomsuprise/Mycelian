# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_lookups.py: catalog, Twitch access, targets, every curated
lookup, blocking runner, advanced raw requests and legacy migration. Twitch is faked."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import chatbot_lookups as lk


ALL_SCOPES = (
    "moderator:read:followers", "channel:read:subscriptions", "bits:read",
    "moderator:manage:shoutouts", "channel:manage:broadcast", "channel:manage:raids",
    "channel:manage:ads",
)


class FakeTwitch:
    def __init__(self):
        self.is_connected = True
        self.user_id = "1000"
        self.user = SimpleNamespace(login="streamer")
        self.authscope = [SimpleNamespace(value=s) for s in ALL_SCOPES]
        self.routes = {}
        self.calls = []

    async def generic_api_call(self, url, method="GET", params=None, json_data=None, headers=None):
        self.calls.append({"url": url, "method": method, "params": dict(params or {}),
                           "json": json_data, "headers": headers})
        handler = self.routes.get((method, url.replace(lk.HELIX, "")))
        if handler is None:
            raise Exception(f"Twitch API Error 404: no route {method} {url}")
        result = handler(params or {}, json_data)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def tw(monkeypatch):
    fake = FakeTwitch()
    monkeypatch.setattr(lk, "_api", lambda: fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def users(**rows):
    def handler(params, _body):
        row = rows.get(params.get("id") or params.get("login"))
        return {"data": [row] if row else []}

    return handler


BOB = {"id": "42", "login": "bob", "display_name": "Bob", "created_at": "2020-01-01T00:00:00Z"}


def iso_ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


# --------------------------------------------------------------------------- dates / catalog


def test_friendly_dates_print_nicely():
    assert str(lk.FriendlyDate(2024, 3, 4)) == "March 4, 2024"
    assert str(lk.FriendlyDateTime(2024, 3, 4, 9, 5)) == "March 4, 2024 09:05"
    assert format(lk.FriendlyDate(2024, 3, 4), "%d/%m/%Y") == "04/03/2024"


def test_date_value():
    assert lk._date_value(None) == ""
    assert lk._date_value("garbage") == ""
    value = lk._date_value(datetime(2024, 5, 6, 7, 8, 9), lk.FriendlyDateTime)
    assert isinstance(value, lk.FriendlyDateTime)
    assert (value.hour, value.minute, value.second) == (7, 8, 9)


def test_catalog_is_consistent():
    ids = [e["id"] for e in lk.LOOKUPS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(lk._IMPLEMENTATIONS)
    for entry in lk.LOOKUPS:
        assert lk.LOOKUP_INDEX[entry["id"]] is entry
        for var in entry["variables"]:
            assert {"key", "label", "example"} <= set(var)


def test_get_lookup():
    assert lk.get_lookup("UPTIME ")["id"] == "uptime"
    assert lk.get_lookup({"id": "raid"})["id"] == "raid"
    assert lk.get_lookup(None) is None
    assert lk.get_lookup("nope") is None


def test_lookup_variable_specs_advanced_and_curated():
    adv = lk.lookup_variable_specs({"id": "advanced", "processing": ["total=math(1+1)", "bad name=1", "noeq", 5]})
    keys = [s["key"] for s in adv]
    assert keys[: len(lk.ADVANCED_VARIABLES)] == [s["key"] for s in lk.ADVANCED_VARIABLES]
    assert keys[len(lk.ADVANCED_VARIABLES):] == ["total"]
    assert len(lk.lookup_variable_specs("ADVANCED")) == len(lk.ADVANCED_VARIABLES)
    specs = lk.lookup_variable_specs("uptime")
    specs[0]["key"] = "mutated"
    assert lk.get_lookup("uptime")["variables"][0]["key"] != "mutated"
    assert lk.lookup_variable_specs("nope") == []


def test_example_variables_skips_patterns():
    out = lk.example_variables("advanced")
    assert out["lookup_ok"] is True and out["lookup_reason"] == "ok" and out["lookup_error"] == ""
    assert "api_status" in out and "api_FIELD" not in out


def test_exceptions_carry_details():
    f = lk.LookupFailure("not_found", "gone", {"a": 1})
    assert (f.reason, f.message, f.variables, str(f)) == ("not_found", "gone", {"a": 1}, "gone")
    assert lk.LookupFailure("x", "y").variables == {}
    h = lk.HelixError(403, "nope")
    assert h.status == 403 and str(h) == "nope"


# --------------------------------------------------------------------------- Twitch access


def test_api_reads_loaded_twitch_module(monkeypatch):
    api = object()
    monkeypatch.setitem(sys.modules, "modules.twitch", SimpleNamespace(twitch_api=api))
    assert lk._api() is api


def test_api_import_failure_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.twitch", None)
    assert lk._api() is None


def test_api_imports_when_not_loaded(monkeypatch):
    import modules

    fake_mod = SimpleNamespace(twitch_api="API")
    monkeypatch.delitem(sys.modules, "modules.twitch", raising=False)
    monkeypatch.setattr(modules, "twitch", fake_mod, raising=False)
    # "from . import twitch" finds the attribute already set on the package.
    assert lk._api() == "API"


def test_scopes_and_broadcaster(tw):
    assert "bits:read" in lk.granted_scopes()
    assert lk.broadcaster_id() == "1000"
    assert lk.broadcaster_login() == "streamer"
    tw.authscope = ["plain:scope"]
    assert lk.granted_scopes() == {"plain:scope"}


def test_scopes_and_broadcaster_without_api(monkeypatch):
    monkeypatch.setattr(lk, "_api", lambda: None)
    assert lk.granted_scopes() == set()
    assert lk.broadcaster_id() == ""
    assert lk.broadcaster_login() == ""


def test_lookup_available(tw, monkeypatch):
    assert lk.lookup_available("nope") == (False, "Unknown lookup.")
    assert lk.lookup_available({"id": "advanced"}) == (True, "")
    assert lk.lookup_available(lk.get_lookup("uptime")["id"]) == (True, "")
    tw.authscope = []
    ok, msg = lk.lookup_available("snooze_ad")
    assert not ok and msg == "Reconnect Twitch to allow: channel:manage:ads"
    tw.is_connected = False
    assert lk.lookup_available("uptime")[1].startswith("Connect your Twitch account")
    monkeypatch.setattr(lk, "_api", lambda: None)
    assert lk.lookup_available("advanced")[0] is False


@pytest.mark.parametrize(
    "exc,status",
    [
        (SimpleNamespace(status=418), 418),
        (type("TwitchPermissionError", (Exception,), {})("x"), 403),
        (Exception("Twitch API Error 429: slow"), 429),
        (Exception("bad status (500)"), 500),
        (Exception("nothing here"), 0),
    ],
)
def test_status_from_exception(exc, status):
    assert lk._status_from_exception(exc) == status


def test_helix_request_paths(tw, monkeypatch):
    tw.routes[("GET", "/a")] = lambda p, b: {"status": 204, "data": ""}
    tw.routes[("GET", "/b")] = lambda p, b: {"data": [1], "status": 200, "x": 1}
    tw.routes[("GET", "/c")] = lambda p, b: lk.LookupFailure("error", "inner")
    assert run(lk.helix_request("get", f"{lk.HELIX}/a", headers={"X": "1"})) == (204, {"status": 204, "data": ""})
    assert tw.calls[-1]["headers"] == {"X": "1"} and tw.calls[-1]["method"] == "GET"
    assert run(lk.helix_request("GET", f"{lk.HELIX}/b", {"q": 1}))[0] == 200
    with pytest.raises(lk.LookupFailure):
        run(lk.helix_request("GET", f"{lk.HELIX}/c"))
    with pytest.raises(lk.HelixError) as err:
        run(lk.helix_request("GET", f"{lk.HELIX}/missing"))
    assert err.value.status == 404
    tw.is_connected = False
    with pytest.raises(lk.LookupFailure) as nc:
        run(lk.helix_request("GET", f"{lk.HELIX}/a"))
    assert nc.value.reason == "not_connected"


def test_get_wraps_helix_errors(tw):
    with pytest.raises(lk.LookupFailure) as err:
        run(lk._get("/missing", {}))
    assert err.value.reason == "error" and err.value.message.startswith("Twitch said:")


def test_rows_and_yes():
    assert lk._rows({"data": [{"a": 1}, "x"]}) == [{"a": 1}]
    assert lk._rows({"data": "x"}) == []
    assert lk._rows(None) == []
    assert (lk._yes(1), lk._yes(0)) == ("yes", "no")


# --------------------------------------------------------------------------- targets


@pytest.mark.parametrize(
    "value,expected",
    [
        ("@Bob", "bob"),
        ("  @@Bob extra words", "bob"),
        ("https://twitch.tv/Cool/", "cool"),
        ("twitch.tv/x", "x"),
        ("https://www.twitch.tv/Y", "y"),
        ("", ""),
        (None, ""),
        ("@", ""),
    ],
)
def test_clean_login(value, expected):
    assert lk.clean_login(value) == expected


def test_target_spec_defaults():
    assert lk._target_spec({}, "event")["from"] == "event_user"
    assert lk._target_spec({"target": "x"}, "command")["from"] == "chatter"
    assert lk._target_spec({"target": {"from": " INPUT ", "input": 2}}, "command") == {"from": "input", "input": 2}


def test_target_login_or_id_sources():
    ctx = {"user_id": "5", "username": "@Viewer", "command_args": ["@Friend"], "who": "pal"}
    assert lk._target_login_or_id({}, ctx, "command") == ("viewer", "5")
    assert lk._target_login_or_id({}, {"userid": "6", "user_login": "v"}, "event") == ("v", "6")
    assert lk._target_login_or_id({"target": {"from": "input", "input": "1"}}, ctx, "command") == ("friend", "")
    assert lk._target_login_or_id({"target": {"from": "input", "input": "{who}"}}, ctx, "command") == ("pal", "")
    # input missing -> falls back to the chatter
    assert lk._target_login_or_id({"target": {"from": "input", "input": 3}}, ctx, "command") == ("viewer", "5")
    assert lk._target_login_or_id({"target": {"from": "text", "text": "@{who}"}}, ctx, "command") == ("pal", "")


def test_resolve_target_user(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB, "bob": BOB})
    assert run(lk.resolve_target_user({}, {"user_id": "42"}))["login"] == "bob"
    # id unknown -> falls back to login
    assert run(lk.resolve_target_user({}, {"user_id": "99", "username": "bob"}))["id"] == "42"
    with pytest.raises(lk.LookupFailure) as e1:
        run(lk.resolve_target_user({}, {}))
    assert e1.value.reason == "no_target"
    with pytest.raises(lk.LookupFailure) as e2:
        run(lk.resolve_target_user({"target": {"from": "text", "text": "bad-name!"}}, {}))
    assert e2.value.reason == "not_found" and e2.value.variables == {"target_name": "bad-name!"}
    with pytest.raises(lk.LookupFailure) as e3:
        run(lk.resolve_target_user({}, {"username": "ghost"}))
    assert e3.value.message == "Couldn't find ghost on Twitch"


def test_target_basics():
    assert lk._target_basics({"login": "a"}) == {"target_name": "a", "target_login": "a",
                                                 "target_url": "https://twitch.tv/a"}
    assert lk._target_basics({})["target_url"] == ""


def test_channel_vars(tw):
    tw.routes[("GET", "/channels")] = lambda p, b: {"data": []}
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    out = run(lk._channel_vars(BOB))
    assert out["target_game"] == "" and out["target_live"] == "no" and out["target_name"] == "Bob"


def test_text_arg():
    entry = {"text_arg": {"default": "{command_message}"}}
    assert lk._text_arg({}, {"command_message": " hi "}, entry) == "hi"
    assert lk._text_arg({"text": "x {a}"}, {"a": 1}, entry) == "x 1"
    assert lk._text_arg({}, {}, {}) == ""


# --------------------------------------------------------------------------- curated lookups


def test_follow_age_bad_date(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB})
    tw.routes[("GET", "/channels/followers")] = lambda p, b: {"data": [{"followed_at": None}]}
    out = run(lk.run_lookup("follow_age", {}, {"user_id": "42"}))
    assert out["lookup_ok"] and out["followage"] == "" and out["follow_days"] == "0"
    assert out["followed_since"] == ""


def test_account_age(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB, "x": {"id": "7", "login": "x"}})
    out = run(lk.run_lookup("account_age", {}, {"user_id": "42"}))
    assert out["lookup_ok"] and out["display_name"] == "Bob"
    assert int(out["account_age_days"]) > 365 and out["account_age"]
    assert isinstance(out["account_created"], lk.FriendlyDate)
    out = run(lk.run_lookup("account_age", {}, {"username": "x"}))
    assert out["account_age"] == "" and out["account_age_days"] == "0" and out["display_name"] == "x"


def test_subscription(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB})
    tw.routes[("GET", "/subscriptions")] = lambda p, b: {"data": [{"tier": "3000", "is_gift": True, "gifter_name": "G"}]}
    out = run(lk.run_lookup("subscription", {}, {"user_id": "42"}))
    assert (out["is_subscribed"], out["sub_tier"], out["is_gift"], out["gifter"]) == ("yes", "3", "yes", "G")
    tw.routes[("GET", "/subscriptions")] = lambda p, b: {"data": [{"tier": "Prime"}]}
    assert run(lk.run_lookup("subscription", {}, {"user_id": "42"}))["sub_tier"] == "1"
    tw.routes[("GET", "/subscriptions")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("subscription", {}, {"user_id": "42"}))
    assert out["is_subscribed"] == "no" and out["sub_tier"] == "0" and out["lookup_ok"]
    tw.routes[("GET", "/subscriptions")] = lambda p, b: Exception("Twitch API Error 500: down")
    out = run(lk.run_lookup("subscription", {}, {"user_id": "42"}))
    assert out["lookup_reason"] == "error" and "Twitch said" in out["lookup_error"]


def test_uptime_offline_and_bad_start(tw):
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("uptime", {}, {}))
    assert out["uptime"] == "offline" and out["is_live"] == "no"
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": [{"started_at": None}]}
    out = run(lk.run_lookup("uptime", {}, {}))
    assert out["uptime"] == "" and out["viewer_count"] == "0" and out["live_since"] == ""


def test_follow_age_not_following(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB})
    tw.routes[("GET", "/channels/followers")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("follow_age", {}, {"user_id": "42"}))
    assert out["lookup_reason"] == "not_following" and out["lookup_error"] == "Bob is not following"
    assert out["followage"] == "not following" and out["is_following"] == "no" and out["target_name"] == "Bob"


def test_channel_info(tw):
    tw.routes[("GET", "/users")] = users(bob=BOB)
    tw.routes[("GET", "/channels")] = lambda p, b: {"data": [{"game_name": "G", "title": "T"}]}
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("channel_info", {"target": {"from": "input", "input": 1}}, {"command_args": ["@bob"]}))
    assert (out["target_game"], out["target_title"], out["target_live"]) == ("G", "T", "no")
    assert out["lookup_ok"] is True


def test_title_game(tw):
    tw.routes[("GET", "/channels")] = lambda p, b: {"data": [{"title": "T", "game_name": "G", "tags": ["a", "b"],
                                                               "broadcaster_language": "en"}]}
    out = run(lk.run_lookup("title_game", {}, {}))
    assert (out["title"], out["game"], out["tags"], out["language"]) == ("T", "G", "a, b", "en")
    tw.routes[("GET", "/channels")] = lambda p, b: {"data": []}
    assert run(lk.run_lookup("title_game", {}, {}))["title"] == ""


def test_follower_and_subscriber_counts(tw):
    tw.routes[("GET", "/channels/followers")] = lambda p, b: {"total": 77, "data": []}
    tw.routes[("GET", "/subscriptions")] = lambda p, b: {"total": 5, "points": 9, "data": []}
    assert run(lk.run_lookup("follower_count", {}, {}))["follower_count"] == "77"
    out = run(lk.run_lookup("subscriber_count", {}, {}))
    assert (out["sub_count"], out["sub_points"]) == ("5", "9")
    tw.routes[("GET", "/channels/followers")] = lambda p, b: ["not", "dict"]
    tw.routes[("GET", "/subscriptions")] = lambda p, b: None
    assert run(lk.run_lookup("follower_count", {}, {}))["follower_count"] == "0"
    assert run(lk.run_lookup("subscriber_count", {}, {}))["sub_count"] == "0"


def test_top_cheerer(tw):
    tw.routes[("GET", "/bits/leaderboard")] = lambda p, b: {"data": [{"user_name": "Rich", "score": 900}]}
    out = run(lk.run_lookup("top_cheerer", {"period": "week"}, {}))
    assert (out["top_cheerer"], out["top_cheer_amount"]) == ("Rich", "900")
    assert tw.calls[-1]["params"]["period"] == "week"
    run(lk.run_lookup("top_cheerer", {"period": "decade"}, {}))
    assert tw.calls[-1]["params"]["period"] == "all"
    tw.routes[("GET", "/bits/leaderboard")] = lambda p, b: {"data": []}
    assert run(lk.run_lookup("top_cheerer", {}, {}))["top_cheer_amount"] == "0"


def test_shoutout_failure_still_replies(tw):
    tw.routes[("GET", "/users")] = users(**{"42": BOB})
    tw.routes[("GET", "/channels")] = lambda p, b: {"data": [{"game_name": "G"}]}
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": [{}]}
    tw.routes[("POST", "/chat/shoutouts")] = lambda p, b: Exception("Twitch API Error 429: cooldown")
    out = run(lk.run_lookup("shoutout", {}, {"user_id": "42"}))
    assert out["lookup_ok"] and out["shoutout_sent"] == "no" and out["target_live"] == "yes"
    tw.routes[("POST", "/chat/shoutouts")] = lambda p, b: {"status": 204, "data": ""}
    assert run(lk.run_lookup("shoutout", {}, {"user_id": "42"}))["shoutout_sent"] == "yes"


def test_set_title_trims_and_errors(tw):
    tw.routes[("PATCH", "/channels")] = lambda p, b: {"status": 204, "data": ""}
    assert run(lk.run_lookup("set_title", {}, {"command_message": " "}))["lookup_reason"] == "no_text"
    out = run(lk.run_lookup("set_title", {"text": "x" * 200}, {}))
    assert len(out["new_title"]) == 140
    tw.routes[("PATCH", "/channels")] = lambda p, b: Exception("Twitch API Error 401: no")
    out = run(lk.run_lookup("set_title", {"text": "T"}, {}))
    assert out["lookup_reason"] == "error" and out["lookup_error"].startswith("Twitch said:")


def test_set_game(tw):
    tw.routes[("GET", "/search/categories")] = lambda p, b: {"data": [{"id": "1", "name": "Celeste 2"},
                                                                        {"id": "2", "name": "Celeste"}]}
    tw.routes[("PATCH", "/channels")] = lambda p, b: {"status": 204, "data": ""}
    out = run(lk.run_lookup("set_game", {}, {"command_message": "celeste"}))
    assert out["new_game"] == "Celeste" and tw.calls[-1]["json"] == {"game_id": "2"}
    out = run(lk.run_lookup("set_game", {}, {"command_message": "cel"}))
    assert out["new_game"] == "Celeste 2"
    assert run(lk.run_lookup("set_game", {}, {"command_message": ""}))["lookup_reason"] == "no_text"
    tw.routes[("GET", "/search/categories")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("set_game", {}, {"command_message": "zzz"}))
    assert out["lookup_reason"] == "not_found" and out["new_game"] == "zzz"


def test_stream_marker(tw):
    tw.routes[("POST", "/streams/markers")] = lambda p, b: {"data": [{"position_seconds": 3725}]}
    out = run(lk.run_lookup("stream_marker", {}, {"command_message": "d" * 200}))
    assert out["marker_time"] == "1:02:05"
    assert len(tw.calls[-1]["json"]["description"]) == 140
    tw.routes[("POST", "/streams/markers")] = lambda p, b: {"data": []}
    out = run(lk.run_lookup("stream_marker", {}, {"command_message": ""}))
    assert out["marker_time"] == "0:00:00" and tw.calls[-1]["json"] == {"user_id": "1000"}
    tw.routes[("POST", "/streams/markers")] = lambda p, b: Exception("Twitch API Error 404: offline")
    assert run(lk.run_lookup("stream_marker", {}, {}))["lookup_reason"] == "error"


def test_raid(tw):
    tw.routes[("GET", "/users")] = users(bob=BOB)
    tw.routes[("POST", "/raids")] = lambda p, b: {"data": [{}]}
    out = run(lk.run_lookup("raid", {"target": {"from": "text", "text": "bob"}}, {}))
    assert out["lookup_ok"] and out["target_login"] == "bob"
    assert tw.calls[-1]["params"] == {"from_broadcaster_id": "1000", "to_broadcaster_id": "42"}
    tw.routes[("POST", "/raids")] = lambda p, b: Exception("Twitch API Error 400: self")
    out = run(lk.run_lookup("raid", {"target": {"from": "text", "text": "bob"}}, {}))
    assert out["lookup_reason"] == "error" and out["target_name"] == "Bob"


def test_snooze_ad(tw):
    tw.routes[("POST", "/channels/ads/schedule/snooze")] = lambda p, b: {"data": [{"snooze_count": 2}]}
    assert run(lk.run_lookup("snooze_ad", {}, {}))["snooze_count"] == "2"
    tw.routes[("POST", "/channels/ads/schedule/snooze")] = lambda p, b: {"data": []}
    assert run(lk.run_lookup("snooze_ad", {}, {}))["snooze_count"] == "0"
    tw.routes[("POST", "/channels/ads/schedule/snooze")] = lambda p, b: Exception("Twitch API Error 400: no")
    assert run(lk.run_lookup("snooze_ad", {}, {}))["lookup_reason"] == "error"


# --------------------------------------------------------------------------- run_lookup


def test_failure_helper():
    assert lk._failure("r", "m", {"a": 1}) == {"a": 1, "lookup_ok": False, "lookup_reason": "r", "lookup_error": "m"}


def test_run_lookup_unknown_and_scope_and_crash(tw, monkeypatch):
    out = run(lk.run_lookup("", None, None))
    assert out["lookup_reason"] == "unknown"
    assert run(lk.run_lookup(None, {"id": "nope"}))["lookup_error"] == "Unknown lookup 'nope'"
    tw.authscope = []
    assert run(lk.run_lookup("raid", {}, {}))["lookup_reason"] == "missing_scope"

    async def crash(*_a):
        raise ValueError("kaboom")

    tw.authscope = [SimpleNamespace(value=s) for s in ALL_SCOPES]
    monkeypatch.setitem(lk._IMPLEMENTATIONS, "uptime", crash)
    assert run(lk.run_lookup("uptime", {}, {})) == lk._failure("error", "kaboom")


def test_run_lookup_advanced_passes_config(monkeypatch):
    seen = {}

    async def raw(method, path, params, body, context, *, headers=None, processing=None, response_format=None):
        seen.update(method=method, path=path, params=params, body=body, ctx=context, headers=headers,
                    processing=processing, fmt=response_format)
        return {"lookup_ok": True}

    monkeypatch.setattr(lk, "run_raw_request", raw)
    run(lk.run_lookup("advanced", {"path": "/users", "headers": {"A": "b"}, "processing": ["x=1"],
                                   "response_format": "a"}, {"c": 1}))
    assert seen == {"method": "GET", "path": "/users", "params": {}, "body": "", "ctx": {"c": 1},
                    "headers": {"A": "b"}, "processing": ["x=1"], "fmt": "a"}


# --------------------------------------------------------------------------- blocking runner


def test_run_lookup_blocking_no_config():
    assert lk.run_lookup_blocking(None)["lookup_error"] == "No lookup configured"
    assert lk.run_lookup_blocking({"id": ""})["lookup_reason"] == "unknown"


def test_run_lookup_blocking_off_loop(tw):
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": []}
    assert lk.run_lookup_blocking({"id": "uptime"})["uptime"] == "offline"


def test_run_lookup_blocking_on_loop_uses_thread(tw):
    tw.routes[("GET", "/streams")] = lambda p, b: {"data": []}

    async def inside():
        return lk.run_lookup_blocking({"id": "uptime"})

    assert run(inside())["is_live"] == "no"


def test_run_lookup_blocking_timeout_and_error(monkeypatch):
    async def slow(*_a, **_k):
        await asyncio.sleep(1)

    monkeypatch.setattr(lk, "run_lookup", slow)
    out = lk.run_lookup_blocking({"id": "uptime"}, timeout=0.01)
    assert out["lookup_error"] == "Twitch did not answer in time"

    async def broken(*_a, **_k):
        raise RuntimeError("weird")

    monkeypatch.setattr(lk, "run_lookup", broken)
    assert lk.run_lookup_blocking({"id": "uptime"})["lookup_error"] == "weird"


def test_run_coro_in_thread_propagates_exception():
    async def boom():
        raise KeyError("k")

    with pytest.raises(KeyError):
        lk._run_coro_in_thread(boom(), 2)


# --------------------------------------------------------------------------- advanced helpers


def test_required_params_builds_index_once(monkeypatch):
    from modules import twitch_api_reference

    builds = []

    class FakeRef:
        def get_all_endpoints(self):
            builds.append(1)
            ep = SimpleNamespace(method="get", endpoint="/things/", required_params={"broadcaster_id": "s"})
            ep2 = SimpleNamespace(method="GET", endpoint="/things", required_params=None)
            return {"cat": {"a": ep, "b": ep2}}

    monkeypatch.setattr(lk, "_reference_index", None)
    monkeypatch.setattr(twitch_api_reference, "TwitchAPIReference", FakeRef)
    assert lk._required_params("GET", "/things/") == {"broadcaster_id": "s"}
    assert lk._required_params("POST", "/things") == {}
    assert builds == [1]


def test_required_params_reference_failure(monkeypatch):
    from modules import twitch_api_reference

    class Broken:
        def get_all_endpoints(self):
            raise RuntimeError("no ref")

    monkeypatch.setattr(lk, "_reference_index", None)
    monkeypatch.setattr(twitch_api_reference, "TwitchAPIReference", Broken)
    assert lk._required_params("GET", "/x") == {}
    assert lk._reference_index == {}


def test_raw_request_context(tw):
    ctx = lk.raw_request_context({"username": "@Bob", "user_id": "42", "message": "!so a b"})
    assert ctx["broadcaster_id"] == ctx["moderator_id"] == "1000"
    assert ctx["broadcaster_login"] == "streamer"
    assert (ctx["chatter_id"], ctx["chatter_login"]) == ("42", "bob")
    assert ctx["command_args"] == ["a", "b"]
    ctx2 = lk.raw_request_context({"message": "hello there", "broadcaster_id": "x"})
    assert ctx2["command_args"] == [] and ctx2["broadcaster_id"] == "x"
    assert ctx2["user_id"] == "" and ctx2["user_login"] == ""
    assert lk.raw_request_context({"command_args": ("k",)})["command_args"] == ("k",)
    assert lk.raw_request_context(None)["command_args"] == []


def test_render_transform_keeps_unknown_tokens():
    out = lk._render_transform("/u/{name}/{missing}", {"name": "a b"}, lambda v: str(v).upper())
    assert out == "/u/A B/{missing}"


def test_substitute_json_text():
    ctx = {"s": 'say "hi"', "n": 5, "flag": "true"}
    assert lk.substitute_json_text('{"a": "{s}", "b": {n}, "c": {flag}, "d": {nope}}', ctx) == \
        '{"a": "say \\"hi\\"", "b": 5, "c": true, "d": {nope}}'


def test_substitute_obj():
    out = lk._substitute_obj({"{k}": ["{v}", 3, {"x": "{v}"}], "n": None}, {"k": "key", "v": "val"})
    assert out == {"key": ["val", 3, {"x": "val"}], "n": None}


@pytest.mark.parametrize(
    "raw,url,path,params",
    [
        ("users?login={u}", f"{lk.HELIX}/users", "/users", {"login": "a b"}),
        ("/helix/streams", f"{lk.HELIX}/streams", "/streams", {}),
        ("/helix", f"{lk.HELIX}/", "/", {}),
        ("https://api.twitch.tv/helix/users?id=1&x=", f"{lk.HELIX}/users", "/users", {"id": "1", "x": ""}),
    ],
)
def test_build_helix_url(raw, url, path, params):
    assert lk.build_helix_url(raw, {"u": "a b"}) == (url, path, params)


@pytest.mark.parametrize("raw", ["", "http://api.twitch.tv/helix/users", "https://evil.example/x"])
def test_build_helix_url_rejects(raw):
    with pytest.raises(lk.LookupFailure):
        lk.build_helix_url(raw, {})


def test_flat():
    assert lk._flat(["a", 1, None, True]) == "a, 1, , True"
    assert lk._flat([{"a": 1}]) == [{"a": 1}]
    assert lk._flat("x") == "x"


def test_flatten_response():
    data = {"data": [{"id": "1", "tags": ["a", "b"], "created_at": "2020-01-01T00:00:00Z"}, "junk", {"id": "2"}],
            "total": 3, "pagination": {"c": 1}, "status": 200, "nested": {"x": 1}, "flag": None}
    out = lk.flatten_response(data, 200)
    assert out["api_success"] is True and out["api_count"] == 3
    assert out["api_id"] == "1" and out["api_tags"] == "a, b"
    assert out["api_data_2_id"] == "2" and "api_data_1_id" not in out
    assert out["api_total"] == 3 and out["api_flag"] is None
    assert "api_pagination" not in out and "api_nested" not in out and "api_status" in out
    assert out["account_age_years"] >= 5
    assert out["account_created_datetime"].startswith("20")
    obj = lk.flatten_response({"data": {"k": [1, 2]}}, 404)
    assert obj["api_k"] == "1, 2" and obj["api_success"] is False
    assert lk.flatten_response(None, 500)["api_response"] is None


def test_apply_response_format():
    data = {"data": [{"name": "Bob"}], "total": 4}
    out = lk._apply_response_format("who={data.0.name}, cnt=total, miss=nope, data.0.name, , absent", data)
    assert out == {"who": "Bob", "cnt": 4, "miss": "nope", "api_data_0_name": "Bob"}


def test_apply_processing(monkeypatch):
    out = lk.apply_processing(["double=math({n} * 2)", "", "math(1 + 1)", "again={double}"], {"n": 4}, {"z": 1})
    assert out["double"] == "8" and out["again"] == "8"
    assert out["processed_math_1_1"] == "2"

    def boom(*_a):
        raise ValueError("bad")

    monkeypatch.setattr(lk, "evaluate_expression", boom)
    assert lk.apply_processing(["x=1"], {}, {}) == {"x": ""}
    assert lk.apply_processing(None, {}, {}) == {}


# --------------------------------------------------------------------------- run_raw_request


def test_raw_request_fills_ids_and_body(tw, monkeypatch):
    monkeypatch.setattr(lk, "_required_params", lambda m, p: {"broadcaster_id": "s", "user_id": "s"})
    tw.routes[("POST", "/things")] = lambda p, b: {"data": [{"ok": True}]}
    out = run(lk.run_raw_request("bogus", "/things", {}, "", {}))
    assert tw.calls[-1]["method"] == "GET"  # unknown method falls back to GET
    out = run(lk.run_raw_request(
        "post", "/things", {"moderator_id": "", "{k}": "{v}", "": "x", "none": None},
        {"broadcaster_id": "", "keep": "{v}"}, {"k": "key", "v": "val"}))
    call = tw.calls[-1]
    assert call["params"] == {"moderator_id": "1000", "key": "val", "none": "", "broadcaster_id": "1000"}
    assert call["json"] == {"broadcaster_id": "1000", "keep": "val"}
    assert out["lookup_ok"] and out["api_ok"] is True


def test_raw_request_body_text_and_invalid_json(tw):
    tw.routes[("PUT", "/x")] = lambda p, b: {"data": []}
    run(lk.run_raw_request("PUT", "/x", {}, '[{"a": "{v}"}]', {"v": "1"}))
    assert tw.calls[-1]["json"] == [{"a": "1"}]
    run(lk.run_raw_request("PUT", "/x", {}, "not json {v}", {"v": "1"}))
    assert tw.calls[-1]["json"] == "not json 1"
    run(lk.run_raw_request("PUT", "/x", {}, [{"a": "{v}"}], {"v": "2"}))
    assert tw.calls[-1]["json"] == [{"a": "2"}]
    run(lk.run_raw_request("GET", "/x", {}, '{"a": 1}', {}))  # GET never sends a body
    assert tw.calls[-1]["json"] is None


def test_raw_request_marker_user_id_paths(tw):
    tw.routes[("POST", "/streams/markers")] = lambda p, b: {"data": [{}]}
    run(lk.run_raw_request("POST", "/streams/markers", {"user_id": ""}, "", {}))
    assert tw.calls[-1]["json"] == {"user_id": "1000"}
    assert tw.calls[-1]["params"]["user_id"] == "1000"
    run(lk.run_raw_request("POST", "/streams/markers", {}, "[1]", {}))
    assert tw.calls[-1]["json"] == {"user_id": "1000"}


def test_raw_request_headers_and_format(tw):
    tw.routes[("GET", "/users")] = lambda p, b: {"data": [{"login": "bob"}]}
    out = run(lk.run_raw_request("GET", "/users", {}, "", {}, headers={"Client-Id": "x", " ": "y"},
                                 response_format="who=data.0.login"))
    assert tw.calls[-1]["headers"] is None
    assert out["who"] == "bob"


def test_raw_request_failures(tw, monkeypatch):
    out = run(lk.run_raw_request("GET", "", {}, "", {}))
    assert out["lookup_error"] == "No API path given"
    tw.routes[("GET", "/x")] = lambda p, b: {"status": 404, "data": []}
    out = run(lk.run_raw_request("GET", "/x", {}, "", {}))
    assert out["lookup_ok"] is False and out["lookup_error"] == "HTTP 404"
    tw.routes[("GET", "/y")] = lambda p, b: Exception("Twitch API Error 403: forbidden")
    out = run(lk.run_raw_request("GET", "/y", {}, "", {}, response_format="a=b", processing=["z=1"]))
    assert out["api_status"] == 403 and out["lookup_error"].endswith("forbidden")
    assert out["a"] == "b" and out["z"] == "1"
    tw.is_connected = False
    out = run(lk.run_raw_request("GET", "/x", {}, "", {}))
    assert out["lookup_reason"] == "not_connected"


# --------------------------------------------------------------------------- legacy migration


def test_migrate_text():
    assert lk._migrate_text(5, "1") == 5
    assert lk._migrate_text("[Command user's ID]-{trigger_username}", "") == "{chatter_id}-{chatter_login}"
    assert lk._migrate_text(" 1000 ", "1000") == "{broadcaster_id}"
    assert lk._migrate_text("id=1000&x=10001", "1000") == "id={broadcaster_id}&x=10001"
    assert lk._migrate_text('{"b":"1000"}', "1000") == '{"b":"{broadcaster_id}"}'


def test_migrate_legacy_api_fields(tw):
    cmd = SimpleNamespace(api_parameters={"broadcaster_id": "1000", "user": None}, api_body='{"id":"1000"}',
                          api_method="post", api_endpoint="channels?broadcaster_id=1000",
                          api_variable_processing=["a=1"], api_headers={"X": "1"}, api_response_format="n=data")
    out = lk.migrate_legacy_api_fields(cmd)
    assert out == {
        "id": "advanced", "method": "POST", "path": "channels?broadcaster_id={broadcaster_id}",
        "params": {"broadcaster_id": "{broadcaster_id}", "user": ""}, "body": '{"id":"{broadcaster_id}"}',
        "processing": ["a=1"], "headers": {"X": "1"}, "response_format": "n=data",
    }
    bare = lk.migrate_legacy_api_fields(SimpleNamespace(api_body={"k": 1}))
    assert bare["method"] == "GET" and bare["body"] == {"k": 1} and "headers" not in bare
    assert "response_format" not in bare


def test_set_title_from_event_does_not_post_placeholder(tw):
    tw.routes[("PATCH", "/channels")] = lambda p, b: {"status": 204, "data": ""}
    # The editor stores the text_arg default ("{command_message}") for events too.
    event_ctx = {"username": "Raider", "user_id": "9"}
    out = run(lk.run_lookup("set_title", {"id": "set_title", "text": "{command_message}"}, event_ctx, kind="event"))
    assert not any(c["method"] == "PATCH" for c in tw.calls)
    assert out["lookup_reason"] == "no_text"

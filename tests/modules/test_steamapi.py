# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/steamapi.py: profile parsing, the timeout client and SteamClient (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import steamapi  # noqa: E402
from modules.steam_data import SteamData  # noqa: E402

KEY = "A" * 32
ME = "76561198000000001"


class Resp:
    def __init__(self, status=200, body=None, text=None, reason="OK", headers=None):
        self.status_code = status
        self.reason = reason
        self.headers = dict(headers or {})
        self._body = body
        self.text = text if text is not None else ("" if body is None else json.dumps(body))

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Answers by API path; a callable route gets the query params."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []
        self.headers = {}

    def request(self, method, url, headers=None, timeout=None):
        assert timeout == steamapi.REQUEST_TIMEOUT
        path, _, query = url.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        self.calls.append((path.replace(steamapi.API_BASE_URL, ""), params))
        for key, answer in self.routes.items():
            if path.endswith(key) or path.endswith(key.rstrip("/")):
                if isinstance(answer, BaseException):
                    raise answer
                return answer(params) if callable(answer) else answer
        return Resp(404, {"error": "no route"}, reason="Not Found")


@pytest.fixture(autouse=True)
def fresh_catalog(tmp_path):
    """Schemas and percentages are kept in the Games Tracker catalog: a new one per test."""
    from modules.game_tracker import store as gstore

    gstore.set_store(gstore.GameTrackerStore(db_path=str(tmp_path / "gt.db")))
    yield
    gstore.set_store(None)


def player(**over):
    p = {"steamid": ME, "personaname": "Mushy", "profileurl": "https://steamcommunity.com/id/mushy/",
         "avatarfull": "http://avatars.steamstatic.com/abc_full.jpg", "personastate": 1}
    p.update(over)
    return p


def summary(p):
    return Resp(200, {"response": {"players": [p] if p else []}})


SCHEMA = {"game": {"gameName": "Portal 2", "availableGameStats": {"achievements": [
    {"name": "ACH_A", "displayName": "First", "description": "Do a thing", "icon": "https://cdn/a.jpg", "icongray": "https://cdn/a_g.jpg", "hidden": 0},
    {"name": "ACH_B", "displayName": "Second", "description": "", "icon": "b", "icongray": "http://cdn/b_g.jpg", "hidden": 1},
    {"name": "ACH_C", "displayName": "Third", "description": "Rare", "icon": "", "icongray": "", "hidden": 0},
]}}}
PERCENTS = {"achievementpercentages": {"achievements": [
    {"name": "ACH_A", "percent": "71.25"}, {"name": "ACH_B", "percent": 2.0}, {"name": "ACH_C", "percent": "x"}]}}


def player_ach(unlocked):
    return Resp(200, {"playerstats": {"steamID": ME, "success": True, "achievements": [
        {"apiname": name, "achieved": 1 if name in unlocked else 0, "unlocktime": unlocked.get(name, 0)}
        for name in ("ACH_A", "ACH_B", "ACH_C")]}})


def make(routes=None, profile=ME, key=KEY):
    c = steamapi.SteamClient(key, profile)
    session = FakeSession(routes)
    users, apps, client = c._api()
    client._session = session
    return c, session


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize("text,expected", [
    ("", None),
    (None, None),
    (ME, ("id", ME)),
    (" https://steamcommunity.com/profiles/76561198000000001/ ", ("id", ME)),
    ("https://steamcommunity.com/profiles/123", None),
    ("steamcommunity.com/id/Mushy_Name?x=1", ("vanity", "Mushy_Name")),
    ("mushy-name", ("vanity", "mushy-name")),
    ("has spaces", None),
    ("x", None),
])
def test_parse_profile(text, expected):
    assert steamapi.parse_profile(text) == expected


def test_redact_key_and_game_art():
    assert steamapi.redact_key(f"GET https://x?key={KEY}&steamids=1 failed", KEY) == "GET https://x?key=***&steamids=1 failed"
    assert steamapi.redact_key("url?key=abc&x=1", "") == "url?key=***&x=1"
    assert steamapi.game_art(None) == (None, None)
    header, capsule = steamapi.game_art(620)
    assert header.endswith("/620/header.jpg") and capsule.endswith("/620/library_600x900.jpg")


# --------------------------------------------------------------------------- the HTTP client


def test_client_errors_are_typed_and_never_show_the_key():
    c, session = make({
        "/GetPlayerSummaries/v2/": Resp(403, None, text="<html>Access is denied. Please verify your key= parameter</html>", reason="Forbidden"),
        "/GetPlayerAchievements/v1/": Resp(400, {"playerstats": {"error": "Requested app has no stats", "success": False}}, reason="Bad Request"),
        "/GetSteamLevel/v1/": Resp(500, None, text="oops", reason="Server Error"),
        "/ResolveVanityURL/v1": Resp(200, None, text="not json"),
        "/GetSchemaForGame/v2/": requests.ConnectionError(f"Max retries for url /x?key={KEY}"),
    })
    _users, apps, client = c._api()
    with pytest.raises(steamapi.SteamKeyError) as e:
        client.request("get", "/ISteamUser/GetPlayerSummaries/v2/", params={"steamids": ME})
    assert e.value.status == 403
    with pytest.raises(steamapi.SteamAPIError, match="no stats") as e:
        apps.get_user_achievements(ME, 1)
    assert not isinstance(e.value, steamapi.SteamKeyError)
    with pytest.raises(steamapi.SteamAPIError, match="500"):
        client.request("get", "/IPlayerService/GetSteamLevel/v1/")
    with pytest.raises(steamapi.SteamAPIError, match="not a JSON"):
        client.request("get", "/ISteamUser/ResolveVanityURL/v1")
    with pytest.raises(steamapi.SteamConnectionError) as e:
        client.request("get", "/ISteamUserStats/GetSchemaForGame/v2/")
    assert KEY not in str(e.value) and "key=***" in str(e.value)
    assert all(p.get("key") == KEY for _path, p in session.calls)


# --------------------------------------------------------------------------- connect / summary


def test_connect_without_a_key_or_with_a_bad_profile():
    c = steamapi.SteamClient("", ME)
    assert c.connect() is False and c.steam_data.connection_status == "Not Connected"
    c = steamapi.SteamClient(KEY, "  ")
    assert c.connect() is False and c.steam_data.connection_status == "Profile Not Set"
    c, _ = make(profile="no spaces allowed")
    assert c.connect() is False and c.steam_data.connection_status == "Profile Not Found"


def test_connect_resolves_a_custom_url_once_and_fills_the_profile():
    routes = {
        "/ResolveVanityURL/v1": Resp(200, {"response": {"success": 1, "steamid": ME}}),
        "/GetPlayerSummaries/v2/": summary(player()),
    }
    c, session = make(routes, profile="https://steamcommunity.com/id/mushy")
    assert c.connect() is True and c.is_connected()
    d = c.steam_data
    assert (d.steam_id, d.persona_name, d.persona_state, d.is_online, d.connection_status) == (ME, "Mushy", "Online", True, "Connected")
    assert d.avatar_url == "https://avatars.steamstatic.com/abc_full.jpg"
    assert c.connect() is True
    assert [p for p, _ in session.calls].count("/ISteamUser/ResolveVanityURL/v1") == 1


def test_connect_failures():
    c, _ = make({"/ResolveVanityURL/v1": Resp(200, {"response": {"success": 42, "message": "No match"}})}, profile="nobody")
    assert c.connect() is False and c.steam_data.connection_status == "Profile Not Found"
    c, _ = make({"/GetPlayerSummaries/v2/": summary(None)})
    assert c.connect() is False and c.steam_data.connection_status == "Profile Not Found"
    c, _ = make({"/GetPlayerSummaries/v2/": Resp(403, None, text="Forbidden", reason="Forbidden")})
    assert c.connect() is False and c.steam_data.connection_status == "Invalid API Key"
    c, _ = make({"/GetPlayerSummaries/v2/": requests.Timeout("slow")})
    assert c.connect() is False and c.steam_data.connection_status == "Disconnected"
    c, _ = make({"/GetPlayerSummaries/v2/": lambda p: (_ for _ in ()).throw(RuntimeError(f"boom key={KEY}"))})
    assert c.connect() is False and c.steam_data.connection_status == "Disconnected"


def test_summary_tracks_the_game_and_offline_state():
    state = {"p": player(personastate=0)}
    c, _ = make({"/GetPlayerSummaries/v2/": lambda _p: summary(state["p"])})
    assert c.connect()
    assert (c.steam_data.persona_state, c.steam_data.connection_status, c.steam_data.current_game_appid) == ("Offline", "Offline", None)
    state["p"] = player(personastate=3, gameid="620", gameextrainfo="Portal 2", avatarfull="")
    c.steam_data.achievement_counts = {"unlocked": 1, "total": 2}
    assert c.refresh_summary()
    d = c.steam_data
    assert (d.persona_state, d.current_game_appid, d.current_game_name) == ("Away", 620, "Portal 2")
    assert d.current_game_art_url.endswith("/620/header.jpg") and d.achievement_counts == {}
    assert d.avatar_url is None
    state["p"] = player(personastate=9, gameid="not-a-number")
    assert c.refresh_summary()
    assert d.current_game_appid is None and d.current_game_name is None and d.persona_state == "Online"


def test_level():
    c, _ = make({"/GetSteamLevel/v1/": Resp(200, {"response": {"player_level": 42}})})
    c.steam_id = ME
    c.refresh_level()
    assert c.steam_data.steam_level == 42
    c, _ = make({"/GetSteamLevel/v1/": Resp(200, {"response": {}})})
    c.steam_id = ME
    c.refresh_level()
    assert c.steam_data.steam_level is None


# --------------------------------------------------------------------------- achievements


def in_game(routes, unlocked_holder):
    base = {
        "/GetPlayerSummaries/v2/": summary(player(gameid="620", gameextrainfo="Portal 2")),
        "/GetSchemaForGame/v2/": Resp(200, SCHEMA),
        "/GetGlobalAchievementPercentagesForApp/v2/": Resp(200, PERCENTS),
        "/GetPlayerAchievements/v1/": lambda _p: player_ach(unlocked_holder["u"]),
    }
    base.update(routes)
    c, session = make(base)
    assert c.connect()
    return c, session


def test_achievements_merge_sort_and_detect_new_unlocks():
    holder = {"u": {"ACH_A": 100}}
    c, session = in_game({}, holder)
    assert c.refresh_achievements() == []  # the first look is the baseline
    d = c.steam_data
    assert d.has_achievements and d.achievement_counts == {"unlocked": 1, "total": 3} and d.current_game_progress == 33
    names = [a["api_name"] for a in d.current_game_achievements]
    assert names == ["ACH_A", "ACH_B", "ACH_C"]  # unlocked, then locked by global percent (2.0 > none)
    a, b, c3 = d.current_game_achievements
    assert a["icon_url"] == "https://cdn/a.jpg" and a["global_percent"] == 71.2 and a["unlock_time"] == 100
    assert b["icon_url"].endswith("/620/b.jpg") and b["icon_gray_url"] == "https://cdn/b_g.jpg" and b["hidden"] is True
    assert c3["icon_url"] is None and c3["global_percent"] is None and c3["unlock_time"] == 0

    holder["u"] = {"ACH_A": 100, "ACH_C": 300, "ACH_B": 200}
    new = c.refresh_achievements()
    assert [n["api_name"] for n in new] == ["ACH_B", "ACH_C"]  # oldest first
    assert d.current_game_progress == 100 and [r["api_name"] for r in d.recent_unlocks] == ["ACH_C", "ACH_B"]
    assert d.last_unlock["api_name"] == "ACH_C" and d.last_unlock["game_name"] == "Portal 2" and d.last_unlock["appid"] == 620
    assert c.refresh_achievements() == []
    paths = [p for p, _ in session.calls]
    assert paths.count("/ISteamUserStats/GetSchemaForGame/v2/") == 1  # cached
    assert paths.count("/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/") == 1


def test_recent_unlocks_are_capped(monkeypatch):
    monkeypatch.setattr(steamapi, "RECENT_UNLOCKS_MAX", 1)
    holder = {"u": {}}
    c, _ = in_game({}, holder)
    c.refresh_achievements()
    holder["u"] = {"ACH_A": 1, "ACH_B": 2}
    c.refresh_achievements()
    assert [r["api_name"] for r in c.steam_data.recent_unlocks] == ["ACH_B"]


def test_private_games_games_without_achievements_and_errors():
    c, _ = in_game({"/GetPlayerAchievements/v1/": Resp(403, {"playerstats": {"error": "Profile is not public", "success": False}})}, {"u": {}})
    assert c.refresh_achievements() == []
    d = c.steam_data
    assert d.achievements_private and d.achievement_counts == {"unlocked": 0, "total": 3} and d.current_game_progress is None

    c, _ = in_game({"/GetPlayerAchievements/v1/": Resp(400, {"playerstats": {"error": "Requested app has no stats", "success": False}})}, {"u": {}})
    with pytest.raises(steamapi.SteamAPIError):
        c.refresh_achievements()

    from modules.game_tracker.store import get_store

    get_store().catalog_delete("steam_schema")  # the catalog would otherwise share the schema read above
    c, _ = in_game({"/GetSchemaForGame/v2/": Resp(200, {"game": {}})}, {"u": {}})
    assert c.refresh_achievements() == [] and c.steam_data.has_achievements is False

    c, _ = in_game({"/GetGlobalAchievementPercentagesForApp/v2/": Resp(500, None, text="x", reason="Error")}, {"u": {}})
    c.refresh_achievements()
    assert all(a["global_percent"] is None for a in c.steam_data.current_game_achievements)

    c, _ = make({"/GetPlayerSummaries/v2/": summary(player())})
    assert c.connect() and c.refresh_achievements() == []  # not in a game


def test_schema_supplies_a_missing_game_name_and_refetches_when_stale(monkeypatch):
    c, session = in_game({"/GetPlayerSummaries/v2/": summary(player(gameid="620"))}, {"u": {}})
    assert c.steam_data.current_game_name is None
    c.refresh_achievements()
    assert c.steam_data.current_game_name == "Portal 2"
    clock = [steamapi.time.time() + steamapi.SCHEMA_TTL_SEC + 1]
    monkeypatch.setattr(steamapi.time, "time", lambda: clock[0])
    c.refresh_achievements()
    assert [p for p, _ in session.calls].count("/ISteamUserStats/GetSchemaForGame/v2/") == 2


def test_update_credentials_resets_what_depends_on_them():
    c, _ = make({"/GetPlayerSummaries/v2/": summary(player())})
    assert c.connect()
    c._known_unlocked[1] = {"x"}
    client = c._client
    c.update_credentials(KEY, ME)
    assert c._client is client and c.steam_id == ME and not c.is_connected()
    c.update_credentials(KEY, "someone_else")
    assert c.steam_id is None and c._known_unlocked == {} and c.steam_data.connection_status == "Disconnected"
    c.update_credentials("", "someone_else")
    assert c._client is None and c.steam_data.connection_status == "Not Connected"


def test_public_payload_is_plain_json():
    payload = steamapi.public_payload(SteamData(persona_name="x", achievement_counts={"unlocked": 1, "total": 2}))
    assert payload["persona_name"] == "x" and json.loads(json.dumps(payload)) == payload
    assert "api_key" not in payload


# --------------------------------------------------------------------------- rate limits and usage


@pytest.fixture
def fresh_limit(monkeypatch):
    from modules import api_usage

    api_usage.reset()
    limit = api_usage.Backoff(base=60, cap=900)
    monkeypatch.setattr(steamapi, "rate_limit", limit)
    yield limit
    api_usage.reset()


def test_429_pauses_every_request_and_is_counted(fresh_limit):
    from modules import api_usage

    c, session = make({"/GetPlayerSummaries/v2/": Resp(429, None, text="slow down", reason="Too Many Requests",
                                                       headers={"Retry-After": "30"})})
    _users, _apps, client = c._api()
    with pytest.raises(steamapi.SteamRateLimited):
        client.request("get", "/ISteamUser/GetPlayerSummaries/v2/")
    assert fresh_limit.active() and 29 <= fresh_limit.remaining() <= 30
    with pytest.raises(steamapi.SteamRateLimited):
        client.request("get", "/ISteamUser/GetPlayerSummaries/v2/")
    assert len(session.calls) == 1  # the second one never left
    snap = api_usage.snapshot("Steam")
    assert snap["hour"] == 1 and snap["rate_limited_hour"] == 1
    assert snap["endpoints"] == {"/ISteamUser/GetPlayerSummaries/v2": 1}


def test_success_resets_the_backoff_and_network_errors_count(fresh_limit):
    from modules import api_usage

    c, session = make({"/GetSteamLevel/v1/": Resp(200, {"response": {"player_level": 3}}),
                       "/GetSchemaForGame/v2/": requests.ConnectionError("down")})
    fresh_limit.hit(now=0)  # an old pause, long over
    _users, _apps, client = c._api()
    client.request("get", "/IPlayerService/GetSteamLevel/v1/")
    assert fresh_limit._delay == 0
    with pytest.raises(steamapi.SteamConnectionError):
        client.request("get", "/ISteamUserStats/GetSchemaForGame/v2/")
    assert api_usage.snapshot("Steam")["errors_hour"] == 1


def test_rate_limited_percentages_are_not_cached(fresh_limit):
    c, session = make({"/GetGlobalAchievementPercentagesForApp/v2/": Resp(429, None, reason="Too Many Requests")})
    with pytest.raises(steamapi.SteamRateLimited):
        c._percents(620)
    assert 620 not in c._global_percents



# --------------------------------------------------------------------------- catalog: read once, survive restarts


def test_schema_and_percentages_survive_a_new_client():
    c, session = in_game({}, {"u": {"ACH_A": 100}})
    c.refresh_achievements()
    fresh, session2 = in_game({}, {"u": {"ACH_A": 100}})  # e.g. after a restart
    fresh.refresh_achievements()
    paths = [p for p, _ in session2.calls]
    assert "/ISteamUserStats/GetSchemaForGame/v2/" not in paths
    assert "/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/" not in paths
    assert "/ISteamUserStats/GetPlayerAchievements/v1/" in paths  # the player's half is always read


def test_schema_is_reread_once_when_the_game_gains_achievements():
    bigger = Resp(200, {"playerstats": {"steamID": ME, "success": True, "achievements": [
        {"apiname": n, "achieved": 0, "unlocktime": 0} for n in ("ACH_A", "ACH_B", "ACH_C", "ACH_D")]}})
    c, session = in_game({"/GetPlayerAchievements/v1/": bigger}, {"u": {}})
    c.refresh_achievements()
    c.refresh_achievements()
    c.refresh_achievements()
    schema_reads = [p for p, _ in session.calls].count("/ISteamUserStats/GetSchemaForGame/v2/")
    assert schema_reads == 2  # the first read, then once more for the new size, not every poll


def test_failed_percentages_are_not_saved():
    c, session = in_game({"/GetGlobalAchievementPercentagesForApp/v2/": Resp(500, None, text="x", reason="Error")}, {"u": {}})
    c.refresh_achievements()
    from modules.game_tracker import catalog

    assert catalog.steam_percents(620) is None


def test_achievement_list_with_known_unlocks_makes_no_player_request():
    c, session = make({"/GetSchemaForGame/v2/": Resp(200, SCHEMA)})
    out = c.achievement_list(620, earned=["ACH_B"])
    assert [(a["id"], a["earned"]) for a in out] == [("ACH_A", False), ("ACH_B", True), ("ACH_C", False)]
    assert [p for p, _ in session.calls] == ["/ISteamUserStats/GetSchemaForGame/v2/"]
    assert c.achievement_list(620, earned=[]) and len(session.calls) == 1  # schema from memory

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""API request counting: per-minute history kept in data/api_usage.db, limits per platform,
graph series, the HTTP hooks, and the 429 backoff / broadcast change gate."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import api_usage  # noqa: E402


@pytest.fixture(autouse=True)
def clean(tmp_path):
    api_usage.configure_storage(enabled=True, path=str(tmp_path / "api_usage.db"))
    api_usage.reset()
    yield
    api_usage.reset()
    api_usage.configure_storage(enabled=False)


def ts(text):
    return datetime.fromisoformat(text).timestamp()


@pytest.mark.parametrize("url,expected", [
    ("https://m.np.playstation.com/api/trophy/v1/users/1234567890123456789/trophyTitles?limit=50",
     "/trophy/v1/users/{id}/trophyTitles"),
    ("https://m.np.playstation.com/api/trophy/v1/npCommunicationIds/NPWR12345_00/trophyGroups/all/trophies",
     "/trophy/v1/npCommunicationIds/{id}/trophyGroups/all/trophies"),
    ("https://m.np.playstation.com/api/userProfile/v1/internal/users/me/basicPresences", "/userProfile/v1/internal/users/{id}/basicPresences"),
    ("/ISteamUser/GetPlayerSummaries/v2/", "/ISteamUser/GetPlayerSummaries/v2"),
    ("/x/PPSA01234_00/y", "/x/{id}/y"),
    ("", "/"),
])
def test_endpoint_name_hides_ids(url, expected):
    assert api_usage.endpoint_name(url) == expected


def test_counts_last_hour_today_errors_and_endpoints():
    now = ts("2026-11-07T15:00:30")
    api_usage.record("PSN", "/a", 200, now=now - 7200)  # today, not the last hour
    api_usage.record("PSN", "/a", 200, now=now - 60)
    api_usage.record("PSN", "/b", 429, now=now - 30)
    api_usage.record("PSN", "/b", None, now=now - 10)
    api_usage.record("Steam", "/s", 200, now=now)
    s = api_usage.snapshot("PSN", now=now)
    assert s == {"hour": 3, "today": 4, "errors_hour": 2, "rate_limited_hour": 1, "endpoints": {"/b": 2, "/a": 1}}
    assert api_usage.snapshot("Steam", now=now)["hour"] == 1
    assert api_usage.snapshot("nothing", now=now)["today"] == 0
    assert api_usage.summary_text("PSN", now=now) == (
        "1.0% of the estimated limit (3 of ~300 requests in the last 15 min) · 1 rate-limited in the last hour")
    assert api_usage.summary_text("Steam", now=now) == "<0.1% of the limit (1 of 100,000 requests in the last 24 h)"
    assert api_usage.summary_text("Spotify", now=now) == "0 requests in the last hour · 0 today"


def test_today_starts_at_local_midnight():
    now = ts("2026-11-07T00:30:00")
    api_usage.record("Steam", "/s", 200, now=now - 3600 + 70)  # yesterday 23:31 (the hour is 60 whole minutes)
    api_usage.record("Steam", "/s", 200, now=now)
    s = api_usage.snapshot("Steam", now=now)
    assert s["hour"] == 2 and s["today"] == 1


def test_history_is_stored_and_read_back(tmp_path):
    now = ts("2026-11-07T15:00:30")
    api_usage.record("PSN", "/a", 200, now=now - 120)
    api_usage.record("PSN", "/a", 429, now=now - 120)
    api_usage.record("YouTube", "/youtube/v3/search", 200, cost=100, now=now)
    api_usage.flush()
    api_usage.reset()
    api_usage.configure_storage(enabled=True, path=str(tmp_path / "api_usage.db"))  # a restart
    assert api_usage.snapshot("PSN", now=now)["hour"] == 2  # survives the restart
    assert api_usage.snapshot("PSN", now=now)["rate_limited_hour"] == 1
    assert api_usage.usage("YouTube", now=now)["count"] == 100


def test_old_history_is_dropped_from_the_file(tmp_path):
    import sqlite3

    api_usage.record("Steam", "/s", 200, now=ts("2026-01-01T00:00:00"))
    api_usage.flush()
    conn = sqlite3.connect(str(tmp_path / "api_usage.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 0  # older than KEEP_DAYS
    finally:
        conn.close()


def test_storage_off_and_unwritable(tmp_path):
    api_usage.configure_storage(enabled=False)
    api_usage.record("Steam", "/s", 200)
    api_usage.flush()  # nothing written, no error
    assert not (tmp_path / "api_usage.db").exists()
    api_usage.configure_storage(enabled=True, path=str(tmp_path))  # a directory: can't be opened
    api_usage.record("Steam", "/s", 200)
    api_usage.flush()  # logged, kept for the next try
    assert api_usage._dirty


def test_usage_against_rolling_windows():
    now = 1_000_000_000.0
    for i in range(30):
        api_usage.record("PSN", "/p", 200, now=now - i * 60)  # one a minute for 30 minutes
    u = api_usage.usage("PSN", now=now)
    assert u["count"] == 15 and u["percent"] == 5.0 and u["limit"].window_sec == 900
    for _ in range(80):
        api_usage.record("Twitch", "/helix/users", 200, now=now)
    assert api_usage.usage("Twitch", now=now)["percent"] == 10.0  # 80 of 800 a minute
    assert api_usage.usage("Twitch", now=now + 120)["count"] == 0  # a minute window
    assert api_usage.usage("Spotify", now=now) == {"count": 0, "percent": 0.0, "limit": None}


def test_youtube_quota_counts_units_since_midnight_pacific():
    before = ts("2026-07-01T06:59:00+00:00")  # 23:59 PDT
    after = ts("2026-07-01T07:01:00+00:00")  # 00:01 PDT: the quota day reset
    api_usage.record("YouTube", "/youtube/v3/search", 200, cost=100, now=before)
    api_usage.record("YouTube", "/youtube/v3/videos", 200, cost=1, now=after)
    assert api_usage.usage("YouTube", now=before)["count"] == 100
    assert api_usage.usage("YouTube", now=after)["count"] == 1
    assert api_usage.summary_text("YouTube", now=after) == "<0.1% of the limit (1 of 10,000 units today)"


def test_pacific_day_start_handles_daylight_saving(monkeypatch):
    winter, summer = ts("2026-11-07T15:00:00+00:00"), ts("2026-07-01T07:30:00+00:00")
    assert api_usage.pacific_day_start(winter) == ts("2026-11-07T08:00:00+00:00")
    assert api_usage.pacific_day_start(summer) == ts("2026-07-01T07:00:00+00:00")
    import zoneinfo

    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda name: (_ for _ in ()).throw(zoneinfo.ZoneInfoNotFoundError(name)))
    assert api_usage.pacific_day_start(winter) == ts("2026-11-07T08:00:00+00:00")  # no tz data (Windows)
    assert api_usage.pacific_day_start(summer) == ts("2026-07-01T07:00:00+00:00")


def test_series_percent_follows_the_window():
    now = 1_000_000_020.0
    for i in range(30):
        api_usage.record("PSN", "/p", 429 if i == 0 else 200, now=now - i * 60)
    data = api_usage.series(["PSN", "Spotify"], span="1h", now=now)
    assert len(data["labels"]) == 60 and data["step_minutes"] == 1
    assert data["count"]["PSN"][-1] == 15 and data["percent"]["PSN"][-1] == 5.0
    assert data["count"]["PSN"][-20] == 11  # 19 minutes ago the window held 11 of them
    assert data["count"]["PSN"][0] == 0
    assert data["limited"]["PSN"][-1] == 1 and data["limited"]["PSN"][-2] == 0
    assert data["requests"]["PSN"][-1] == 1
    assert data["percent"]["Spotify"] is None and data["requests"]["Spotify"] == [0] * 60


@pytest.mark.parametrize("span,points,step", [("1h", 60, 1), ("24h", 96, 15), ("7d", 84, 120), ("bogus", 60, 1)])
def test_series_ranges(span, points, step):
    now = 1_000_000_020.0
    api_usage.record("Spotify", "/v1/me/player", 200, now=now - 3 * 3600)
    data = api_usage.series(["Spotify"], span=span, now=now)
    assert len(data["labels"]) == points and data["step_minutes"] == step
    assert sum(data["requests"]["Spotify"]) == (0 if span in ("1h", "bogus") else 1)


def test_series_steam_day_window_over_a_week():
    now = 1_000_000_020.0
    api_usage.record("Steam", "/s", 200, now=now - 30 * 3600)  # 30 hours ago
    data = api_usage.series(["Steam"], span="7d", now=now)
    assert data["count"]["Steam"][-1] == 0  # out of today's 24 h window
    assert max(data["count"]["Steam"]) == 1  # inside it, earlier in the week


@pytest.mark.parametrize("value,text", [(0, "0%"), (0.04, "<0.1%"), (0.9, "0.9%"), (9.95, "9.9%"), (41.4, "41%"), (130, "130%")])
def test_percent_text(value, text):
    assert api_usage.percent_text(value) == text


def test_limit_notes():
    assert "community estimate" in api_usage.limit_note("PSN")
    assert "midnight Pacific" in api_usage.limit_note("YouTube")
    assert "30 seconds" in api_usage.limit_note("Spotify")
    assert api_usage.limit_note("Other") == ""


def test_hourly_log_line(caplog, monkeypatch):
    monkeypatch.setattr(api_usage, "_last_log", 0.0)
    with caplog.at_level(logging.INFO, logger="modules.api_usage"):
        api_usage.record("PSN", "/trophy", 200)
    assert "PSN API: 1 requests in the last hour" in caplog.text and "/trophy 1" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="modules.api_usage"):
        api_usage.record("PSN", "/trophy", 200)  # not due again yet
    assert caplog.text == ""


# --------------------------------------------------------------------------- HTTP hooks


@pytest.mark.parametrize("url,platform", [
    ("https://api.twitch.tv/helix/users?id=1", "Twitch"),
    ("https://id.twitch.tv/oauth2/validate", None),  # not the Helix bucket
    ("https://gql.twitch.tv/gql", None),
    ("https://www.googleapis.com/youtube/v3/search?q=x", "YouTube"),
    ("https://oauth2.googleapis.com/token", None),
    ("https://api.spotify.com/v1/me/player", "Spotify"),
    ("https://accounts.spotify.com/api/token", "Spotify"),
    ("https://streamlabs.com/api/v2.0/donations", "Streamlabs"),
    ("https://streamlabs.com/dashboard", None),
    ("https://api.streamelements.com/kappa/v2/channels/me", "StreamElements"),
    ("https://api.steampowered.com/ISteamUser/x", None),  # Steam counts in its own client
    ("not a url", None),
])
def test_platform_for_url(url, platform):
    assert api_usage.platform_for_url(url) == platform


def test_youtube_units_by_endpoint():
    api_usage.count_url("https://www.googleapis.com/youtube/v3/search?part=snippet", 200)
    api_usage.count_url("https://www.googleapis.com/youtube/v3/liveChat/messages?liveChatId=1", 200)
    api_usage.count_url("https://www.googleapis.com/youtube/v3/videos?id=1", 200)
    api_usage.count_url("https://example.com/", 200)  # not tracked
    assert api_usage.usage("YouTube")["count"] == 106
    assert api_usage.snapshot("YouTube")["hour"] == 3


def test_requests_hook_counts_tracked_hosts(monkeypatch):
    import requests
    from requests.adapters import HTTPAdapter

    assert hasattr(HTTPAdapter.send, "__wrapped__")  # installed when api_usage was imported

    def fake_send(self, request, *a, **k):
        if "fail" in request.url:
            raise requests.ConnectionError("offline")
        resp = requests.Response()
        resp.status_code = 429 if "slow" in request.url else 200
        resp.url = request.url
        return resp

    # Wrap a fake adapter instead, so no real network is touched (monkeypatch restores the real one).
    monkeypatch.setattr(api_usage, "_hooks_installed", False)
    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    api_usage.install_http_hooks()
    requests.get("https://api.spotify.com/v1/me/player")
    requests.get("https://api.spotify.com/v1/slow")
    with pytest.raises(requests.ConnectionError):
        requests.get("https://api.spotify.com/v1/fail")
    requests.get("https://example.com/")
    s = api_usage.snapshot("Spotify")
    assert s["hour"] == 3 and s["errors_hour"] == 2 and s["rate_limited_hour"] == 1
    assert api_usage.snapshot("example").get("hour") == 0


def test_aiohttp_hook_counts_helix(monkeypatch):
    import asyncio

    import aiohttp

    async def fake_request(self, method, url, *a, **k):
        if "fail" in str(url):
            raise aiohttp.ClientConnectionError("offline")
        return type("R", (), {"status": 200})()

    monkeypatch.setattr(api_usage, "_hooks_installed", False)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", fake_request)
    monkeypatch.setattr("requests.adapters.HTTPAdapter.send", lambda *a, **k: None)
    api_usage.install_http_hooks()

    async def go():
        async with aiohttp.ClientSession() as session:
            await session._request("GET", "https://api.twitch.tv/helix/users")
            await session._request("GET", "https://eventsub.wss.twitch.tv/ws")  # not counted
            with pytest.raises(aiohttp.ClientConnectionError):
                await session._request("GET", "https://api.twitch.tv/helix/fail")

    asyncio.run(go())
    s = api_usage.snapshot("Twitch")
    assert s["hour"] == 2 and s["errors_hour"] == 1


# --------------------------------------------------------------------------- helpers


def test_change_gate_sends_changes_and_a_heartbeat():
    gate = api_usage.ChangeGate(heartbeat=60)
    assert gate.should_send({"a": 1}, now=0) is True
    assert gate.should_send({"a": 1}, now=30) is False
    assert gate.should_send({"a": 2}, now=31) is True
    assert gate.should_send({"a": 2}, now=90) is False
    assert gate.should_send({"a": 2}, now=91.5) is True  # heartbeat
    assert gate.should_send({"x": object()}, now=92) is True  # unhashable payloads always go
    gate.reset()
    assert gate.should_send({"a": 2}, now=93) is True


def test_backoff_doubles_honours_retry_after_and_resets():
    b = api_usage.Backoff(base=10, cap=40)
    assert not b.active(now=0) and b.remaining(now=0) == 0
    assert b.hit(now=0) == 10 and b.active(now=5) and b.remaining(now=5) == 5
    assert b.hit(now=0) == 20 and b.until == 20
    assert b.hit(now=0) == 40
    assert b.hit(now=0) == 40  # capped
    b.ok()
    assert b.hit(now=100) == 10
    assert b.hit("3", now=200) == 3 and b.until == 203
    assert b.hit("999", now=300) == 40  # Retry-After is capped too
    assert b.hit("soon", now=400) == 40  # unreadable header: the doubled delay
    assert not b.active(now=10_000)


def test_twitch_minute_wording():
    api_usage.record("Twitch", "/helix/users", 200)
    assert api_usage.summary_text("Twitch") == "0.1% of the limit (1 of 800 requests in the last minute)"

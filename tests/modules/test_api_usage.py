# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""PSN / Steam request counting and the 429 backoff helper."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import api_usage  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    api_usage.reset()
    yield
    api_usage.reset()


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
    now = datetime(2026, 11, 7, 15, 0).timestamp()
    api_usage.record("PSN", "/a", 200, now=now - 7200)  # today, not the last hour
    api_usage.record("PSN", "/a", 200, now=now - 60)
    api_usage.record("PSN", "/b", 429, now=now - 30)
    api_usage.record("PSN", "/b", None, now=now - 10)
    api_usage.record("Steam", "/s", 200, now=now)
    s = api_usage.snapshot("PSN", now=now)
    assert s == {"hour": 3, "today": 4, "errors_hour": 2, "rate_limited_hour": 1, "endpoints": {"/b": 2, "/a": 1}}
    assert api_usage.snapshot("Steam", now=now)["hour"] == 1
    assert api_usage.snapshot("nothing", now=now)["today"] == 0
    assert api_usage.summary_text("PSN", now=now) == "API calls: 3 last hour · 4 today · 1 rate-limited"
    assert api_usage.summary_text("Steam", now=now) == "API calls: 1 last hour · 1 today"


def test_today_starts_at_midnight_and_old_entries_are_dropped():
    now = datetime(2026, 11, 7, 0, 30).timestamp()
    api_usage.record("Steam", "/s", 200, now=now - 2 * 86400)  # pruned on the next record
    api_usage.record("Steam", "/s", 200, now=now - 3600 + 10)  # yesterday 23:30
    api_usage.record("Steam", "/s", 200, now=now)
    s = api_usage.snapshot("Steam", now=now)
    assert s["hour"] == 2 and s["today"] == 1
    assert len(api_usage._entries["Steam"]) == 2


def test_hourly_log_line(caplog, monkeypatch):
    monkeypatch.setattr(api_usage, "_last_log", 0.0)
    with caplog.at_level(logging.INFO, logger="modules.api_usage"):
        api_usage.record("PSN", "/trophy", 200)
    assert "PSN API: 1 requests in the last hour" in caplog.text and "/trophy 1" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="modules.api_usage"):
        api_usage.record("PSN", "/trophy", 200)  # not due again yet
    assert caplog.text == ""


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

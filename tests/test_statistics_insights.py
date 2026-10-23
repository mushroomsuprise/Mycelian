# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Read-only analytics behind the Statistics tab, and the highlights image renderer."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from modules.statistics_insights import (
    METRICS_BY_KEY,
    SYSTEM_USERNAME,
    Insights,
    bucket_for,
    bucket_keys,
    compact,
    custom_range,
    pct_change,
    preset_range,
)

ROOT = Path(__file__).resolve().parent.parent


class FakeManager:
    """Just the connection-pool surface Insights uses."""

    def __init__(self, path: Path):
        self.path = path
        self._stats_db_initialized = True

    def _get_stats_db_connection(self):
        return sqlite3.connect(self.path)

    def _return_stats_db_connection(self, conn):
        if conn is not None:
            conn.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "statistics.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE user_events (id INTEGER PRIMARY KEY, username TEXT, event_type TEXT, amount REAL, "
        "alert_name TEXT, timestamp REAL, created_at TIMESTAMP, username_lower TEXT)"
    )
    conn.commit()
    conn.close()
    return path


def add(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp, username_lower) "
        "VALUES (?, ?, ?, '', ?, lower(?))",
        [(u, t, a, ts.timestamp(), u) for u, t, a, ts in rows],
    )
    conn.commit()
    conn.close()


NOW = datetime(2026, 10, 22, 20, 30)


def test_summary_counts_sums_and_unique_viewers(db):
    day = NOW.replace(hour=19)
    add(db, [
        ("Alice", "chat_message", 1, day),
        ("alice", "chat_message", 1, day),  # same viewer, different case
        ("Bob", "bit", 500, day),
        ("Bob", "bit", 100, day),
        ("Cara", "sub", 0, day),
        ("Cara", "resub", 0, day),
        (SYSTEM_USERNAME, "poll_complete", 12, day),
    ])
    ins = Insights(FakeManager(db))
    s = ins.summary(preset_range("today", now=NOW))
    assert s["types"]["chat_message"]["count"] == 2
    assert s["types"]["bit"]["total"] == 600
    assert s["unique_users"] == 3  # system user excluded
    assert s["total_events"] == 7
    assert Insights.metric_value(s, METRICS_BY_KEY["subs"]) == 2
    assert Insights.metric_value(s, METRICS_BY_KEY["bits"]) == 600
    assert Insights.metric_value(s, METRICS_BY_KEY["viewers"]) == 3


def test_timeseries_is_zero_filled_per_day(db):
    rng = preset_range("7d", now=NOW)
    add(db, [
        ("Alice", "chat_message", 1, NOW - timedelta(days=6)),
        ("Alice", "chat_message", 1, NOW),
        ("Bob", "chat_message", 1, NOW),
        ("Bob", "bit", 50, NOW),
    ])
    ins = Insights(FakeManager(db))
    series = ins.timeseries(rng)
    assert series["bucket"] == "day" and len(series["keys"]) == 7
    chat = Insights.metric_series(series, METRICS_BY_KEY["chat"])
    assert chat == [1, 0, 0, 0, 0, 0, 2]
    assert Insights.metric_series(series, METRICS_BY_KEY["bits"])[-1] == 50
    assert series["viewers"][-1] == 2


def test_today_uses_hour_buckets(db):
    rng = preset_range("today", now=NOW)
    assert bucket_for(rng) == "hour"
    add(db, [("Alice", "chat_message", 1, NOW.replace(hour=19, minute=5))])
    series = Insights(FakeManager(db)).timeseries(rng)
    assert len(series["keys"]) == 24
    assert series["labels"][19] == "19:00"
    assert series["types"]["chat_message"]["count"][19] == 1


def test_week_buckets_start_on_monday():
    rng = custom_range(datetime(2025, 1, 1), datetime(2025, 12, 31))
    assert bucket_for(rng) == "week"
    keys = bucket_keys(rng, "week")
    assert all(datetime.strptime(k, "%Y-%m-%d").weekday() == 0 for k in keys)


def test_heatmap_rows_are_monday_first(db):
    wed_9pm = datetime(2026, 10, 21, 21, 15)  # a Wednesday
    add(db, [("Alice", "chat_message", 1, wed_9pm), ("Bob", "bit", 10, wed_9pm)])
    grid = Insights(FakeManager(db)).heatmap(preset_range("7d", now=NOW), ["chat_message"])
    assert grid[2][21] == 1
    assert sum(map(sum, grid)) == 1


def test_leaders_merge_case_and_aggregate(db):
    add(db, [
        ("Alice", "bit", 100, NOW),
        ("ALICE", "bit", 300, NOW),
        ("Bob", "bit", 350, NOW),
        ("Bob", "watch_streak", 9, NOW),
        ("Bob", "watch_streak", 12, NOW),
        (SYSTEM_USERNAME, "bit", 9999, NOW),
    ])
    ins = Insights(FakeManager(db))
    rng = preset_range("today", now=NOW)
    top = ins.leaders(rng, ["bit"], "sum")
    assert [(r["username"].lower(), r["value"]) for r in top] == [("alice", 400), ("bob", 350)]
    assert ins.leaders(rng, ["watch_streak"], "max")[0]["value"] == 12
    assert ins.leaders(rng, ["bit"], "count")[0]["value"] == 2


def test_viewer_scoped_queries(db):
    add(db, [
        ("Alice", "chat_message", 1, NOW - timedelta(days=40)),
        ("Alice", "chat_message", 1, NOW),
        ("Alice", "follow", 0, NOW),
        ("Bob", "chat_message", 1, NOW),
    ])
    ins = Insights(FakeManager(db))
    rng = preset_range("30d", now=NOW)
    assert ins.summary(rng, "alice")["total_events"] == 2
    first, last, n = ins.user_span("ALICE")
    assert n == 3 and first < last
    events = ins.recent_events(rng, "alice", ["follow"])
    assert [e["event_type"] for e in events] == ["follow"]


def test_previous_period_is_same_length_and_adjacent():
    rng = preset_range("30d", now=NOW)
    prev = rng.previous()
    assert prev.days == rng.days == 30
    assert (rng.start - prev.start).days == 30


def test_pct_change_and_compact():
    assert pct_change(150, 100) == 50
    assert pct_change(5, 0) is None
    assert compact(1284) == "1,284"
    assert compact(12_900) == "12.9K"
    assert compact(4_200_000) == "4.2M"


def test_missing_database_returns_empty_results(tmp_path):
    mgr = FakeManager(tmp_path / "nope.db")
    mgr._stats_db_initialized = False
    ins = Insights(mgr)
    assert ins.summary(preset_range("7d", now=NOW))["total_events"] == 0
    assert ins.leaders(preset_range("7d", now=NOW), ["bit"]) == []


# ----- highlights image -----

def _theme(name: str):
    from modules.theme_manager import ThemeColors

    return ThemeColors(**json.loads((ROOT / "themes" / f"{name}_theme.json").read_text()))


FULL_HIGHLIGHTS = {
    "total_events": 900, "unique_users": 42, "total_chat_messages": 700, "total_bits": 12_500,
    "total_new_subs": 9, "total_resubs": 20, "total_subs": 29, "total_gift_subs": 15,
    "total_follows": 31, "total_raids": 2, "total_channel_points": 48_000, "total_donations": 3,
    "total_giveaway_entries": 80,
    "top_chat_messages": [{"username": f"chatter_with_a_very_long_name_{i}", "count": 100 - i} for i in range(5)],
    "top_bits": [{"username": "Alice", "total": 5000}], "top_gift_subs": [{"username": "Bob", "total": 10}],
    "top_channel_points": [], "most_active_user": {"username": "Alice", "event_count": 222},
    "biggest_bit_donation": {"username": "Alice", "amount": 1000},
    "biggest_watch_streak": {"username": "Cara", "amount": 33}, "top_chatter": {"username": "Alice", "event_count": 150},
}


@pytest.mark.parametrize("theme", ["dark", "light", "cyberpunk"])
@pytest.mark.parametrize("layout,size", [("wide", (1920, 1080)), ("portrait", (1080, 1350))])
def test_render_highlights_layouts(theme, layout, size):
    from modules.statistics_export import render_highlights_image

    extra = {
        "activity_labels": [f"Oct {d:02d}" for d in range(1, 31)],
        "activity_values": [((d * 37) % 11) * 10 for d in range(30)],
        "activity_bucket": "day",
        "deltas": {"viewers": 12.0, "chat": -8.0, "bits": None, "subs": 0.1, "gifts": 2500.0},
    }
    img = render_highlights_image(
        FULL_HIGHLIGHTS, datetime(2026, 10, 1), datetime(2026, 10, 30),
        streamer_name="A streamer name long enough to need truncating in the header",
        theme=_theme(theme), layout=layout, extra=extra,
    )
    assert img.size == size


def test_render_highlights_with_sparse_data(tmp_path):
    from modules.statistics_export import generate_highlights_image

    out = tmp_path / "out.png"
    ok = generate_highlights_image({"total_events": 1}, datetime(2026, 10, 1), datetime(2026, 10, 1), str(out), theme=_theme("dark"))
    assert ok and out.stat().st_size > 0

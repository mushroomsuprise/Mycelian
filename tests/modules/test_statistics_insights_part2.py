# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""statistics_insights: event types and metrics, date ranges and presets, buckets, and the
read-only Insights queries (summary, time series, heatmap, leaders, recent events, spans).

Part 2: tests/test_statistics_insights.py already covers the main query paths; this file
fills in every remaining branch. (Named _part2 so its module name does not collide with
that file.)"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.statistics_insights as si  # noqa: E402

NOW = datetime(2026, 10, 22, 20, 30)


class FakeManager:
    def __init__(self, path):
        self.path = path
        self._stats_db_initialized = True
        self.returned = []

    def _get_stats_db_connection(self):
        return sqlite3.connect(self.path)

    def _return_stats_db_connection(self, conn):
        self.returned.append(conn)
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


def add(path, rows, lower=True):
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp, username_lower) VALUES (?, ?, ?, ?, ?, ?)",
        [(u, t, a, n, ts.timestamp(), (u.lower() if (lower and u) else None)) for u, t, a, n, ts in rows],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ins(db):
    return si.Insights(FakeManager(db))


# --------------------------------------------------------------------------- catalogue


def test_event_types_cover_every_recorded_event():
    expected = {
        "chat_message", "bit", "sub", "resub", "giftsub", "donation", "point_redeem", "follow", "raid",
        "watch_streak", "connector", "chatbot_command", "chatbot_event", "quote_redeem", "giveaway_entry",
        "giveaway_win", "giveaway_draw_complete", "poll_complete", "poll_win",
    }
    assert set(si.EVENT_TYPES) == expected
    assert all(k == v.key and v.label and v.icon for k, v in si.EVENT_TYPES.items())
    assert si.EVENT_TYPES["bit"].amount_label == "bits"
    assert si.EVENT_TYPES["chat_message"].amount_label == ""


def test_metrics_reference_known_event_types():
    assert [m.key for m in si.METRICS] == list(si.METRICS_BY_KEY)
    for m in si.METRICS:
        for event_type, agg in m.parts:
            assert event_type in si.EVENT_TYPES and agg in ("count", "sum")
    assert si.METRICS_BY_KEY["viewers"].distinct_users and not si.METRICS_BY_KEY["viewers"].parts
    with pytest.raises(Exception):
        si.METRICS[0].key = "x"  # frozen


# --------------------------------------------------------------------------- date ranges


def test_date_range_properties():
    rng = si.DateRange(datetime(2026, 1, 1), datetime(2026, 1, 3, 23, 59, 59), "x")
    assert rng.start_ts == datetime(2026, 1, 1).timestamp()
    assert rng.end_ts == datetime(2026, 1, 3, 23, 59, 59).timestamp()
    assert rng.days == 3
    prev = rng.previous()
    assert prev.label == "previous period" and prev.start == datetime(2025, 12, 29) and prev.days == 3
    backwards = si.DateRange(datetime(2026, 1, 3), datetime(2026, 1, 1), "y")
    assert backwards.days == 1  # never below one


def test_day_helpers():
    assert si._day_start(NOW) == datetime(2026, 10, 22)
    assert si._day_end(NOW) == datetime(2026, 10, 22, 23, 59, 59, 999999)


def test_presets():
    assert [k for k, _ in si.PRESETS] == ["today", "7d", "30d", "90d", "all"]
    today = si.preset_range("today", now=NOW)
    assert (today.start, today.label, today.days) == (datetime(2026, 10, 22), "Today", 1)
    week = si.preset_range("7d", now=NOW)
    assert week.start == datetime(2026, 10, 16) and week.label == "Last 7 days" and week.days == 7
    assert si.preset_range("90d", now=NOW).days == 90
    assert si.preset_range("bogus", now=NOW).label == "Last 30 days"


def test_all_time_starts_at_the_first_event_or_a_month_back():
    first = datetime(2025, 3, 4, 15, 0).timestamp()
    rng = si.preset_range("all", now=NOW, first_event_ts=first)
    assert rng.start == datetime(2025, 3, 4) and rng.label == "All time"
    fallback = si.preset_range("all", now=NOW)
    assert fallback.start == datetime(2026, 9, 23) and fallback.days == 30


def test_preset_defaults_to_the_current_time():
    rng = si.preset_range("today")
    assert rng.start.date() == datetime.now().date()


def test_custom_range_orders_and_labels():
    rng = si.custom_range(datetime(2026, 2, 10, 15), datetime(2026, 2, 1, 9))
    assert rng.start == datetime(2026, 2, 1) and rng.end == datetime(2026, 2, 10, 23, 59, 59, 999999)
    assert rng.label == "Feb 01, 2026 – Feb 10, 2026"


# --------------------------------------------------------------------------- buckets


def test_bucket_for_range_lengths():
    assert si.bucket_for(si.preset_range("today", now=NOW)) == "hour"
    assert si.bucket_for(si.preset_range("90d", now=NOW)) == "day"
    assert si.bucket_for(si.custom_range(datetime(2026, 1, 1), datetime(2026, 7, 9))) == "day"  # 190 days
    assert si.bucket_for(si.custom_range(datetime(2026, 1, 1), datetime(2026, 7, 10))) == "week"


def test_bucket_keys_and_labels():
    today = si.preset_range("today", now=NOW)
    hours = si.bucket_keys(today, "hour")
    assert len(hours) == 24 and hours[0] == "2026-10-22 00" and hours[-1] == "2026-10-22 23"
    assert si.bucket_label(hours[5], "hour") == "05:00"
    days = si.bucket_keys(si.preset_range("7d", now=NOW), "day")
    assert days[0] == "2026-10-16" and len(days) == 7
    assert si.bucket_label(days[0], "day") == "Oct 16"
    weeks = si.bucket_keys(si.custom_range(datetime(2026, 10, 1), datetime(2026, 10, 31)), "week")
    assert weeks[0] == "2026-09-28" and weeks[-1] == "2026-10-26"
    assert si.bucket_label(weeks[0], "week") == "Week of Sep 28"


def test_bucket_sql_expressions_match_the_python_keys(db, ins):
    # A Wednesday evening in local time lands in the same key Python computes.
    ts = datetime(2026, 10, 21, 21, 15)
    add(db, [("A", "chat_message", 1, "", ts)])
    conn = sqlite3.connect(db)
    for bucket, expected in (("hour", "2026-10-21 21"), ("day", "2026-10-21"), ("week", "2026-10-19")):
        got = conn.execute(f"SELECT {si._BUCKET_SQL[bucket]} FROM user_events").fetchone()[0]
        assert got == expected, bucket
    conn.close()


# --------------------------------------------------------------------------- query plumbing


def test_query_returns_rows_as_tuples_and_returns_the_connection(db):
    mgr = FakeManager(db)
    add(db, [("A", "bit", 5, "", NOW)])
    rows = si.Insights(mgr)._query("SELECT username, amount FROM user_events WHERE amount > ?", [1])
    assert rows == [("A", 5.0)]
    assert len(mgr.returned) == 1


def test_query_without_database_or_connection(db):
    off = FakeManager(db)
    off._stats_db_initialized = False
    assert si.Insights(off)._query("SELECT 1") == []
    assert si.Insights(object())._query("SELECT 1") == []  # a manager without the flag
    none = FakeManager(db)
    none._get_stats_db_connection = lambda: None
    assert si.Insights(none)._query("SELECT 1") == []
    assert none.returned == [None]


def test_query_errors_are_logged_and_return_nothing(db, caplog):
    mgr = FakeManager(db)
    assert si.Insights(mgr)._query("SELECT nope FROM nowhere") == []
    assert "statistics insights query failed" in caplog.text
    assert len(mgr.returned) == 1


def test_user_clause():
    assert si.Insights._user_clause(None) == ("", [])
    assert si.Insights._user_clause("") == ("", [])
    clause, params = si.Insights._user_clause("  MixedCase ")
    assert "IFNULL(username_lower, lower(username)) = ?" in clause and params == ["mixedcase"]


# --------------------------------------------------------------------------- queries


def test_first_event_ts(db, ins):
    assert ins.first_event_ts() is None
    add(db, [("A", "x", 0, "", NOW), ("B", "x", 0, "", NOW - timedelta(days=3))])
    assert ins.first_event_ts() == (NOW - timedelta(days=3)).timestamp()


def test_summary_includes_peaks_and_rows_awaiting_backfill(db, ins):
    add(db, [("Kim", "watch_streak", 4, "", NOW), ("Kim", "watch_streak", 9, "", NOW)])
    add(db, [("Pending", "chat_message", 1, "", NOW)], lower=False)
    s = ins.summary(si.preset_range("today", now=NOW))
    assert s["types"]["watch_streak"] == {"count": 2, "total": 13.0, "peak": 9.0}
    assert s["unique_users"] == 2 and s["total_events"] == 3
    assert ins.summary(si.preset_range("today", now=NOW), "pending")["total_events"] == 1


def test_metric_value_handles_missing_types():
    summary = {"types": {"bit": {"count": 2, "total": 300.0}}, "unique_users": 4}
    assert si.Insights.metric_value(summary, si.METRICS_BY_KEY["bits"]) == 300.0
    assert si.Insights.metric_value(summary, si.METRICS_BY_KEY["subs"]) == 0.0
    assert si.Insights.metric_value(summary, si.METRICS_BY_KEY["viewers"]) == 4.0
    assert si.Insights.metric_value({}, si.METRICS_BY_KEY["viewers"]) == 0.0
    assert si.Insights.metric_value({}, si.METRICS_BY_KEY["chat"]) == 0.0


def test_timeseries_with_an_explicit_bucket_and_viewer(db, ins):
    add(db, [
        ("Ann", "chat_message", 1, "", NOW),
        ("Ann", "bit", 25, "", NOW - timedelta(days=1)),
        ("Bob", "chat_message", 1, "", NOW),
        ("__system__", "poll_complete", 3, "", NOW),
    ])
    rng = si.preset_range("7d", now=NOW)
    series = ins.timeseries(rng, bucket="week")
    assert series["bucket"] == "week" and series["keys"] == ["2026-10-12", "2026-10-19"]
    assert series["labels"] == ["Week of Oct 12", "Week of Oct 19"]
    assert series["types"]["chat_message"]["count"] == [0, 2]
    assert series["viewers"] == [0, 2]
    ann = ins.timeseries(rng, username="ANN")
    assert sum(ann["types"]["chat_message"]["count"]) == 1 and ann["types"]["bit"]["total"][-2] == 25.0
    assert "poll_complete" not in ann["types"]


def test_timeseries_skips_buckets_outside_the_key_list(db):
    mgr = FakeManager(db)
    ins = si.Insights(mgr)
    rows = iter([[("1999-01-01", "bit", 1, 5.0), ("2026-10-22", "bit", 2, 7.0)], [("1999-01-01", 3), ("2026-10-22", 1)]])
    ins._query = Mock(side_effect=lambda *a, **k: next(rows))
    series = ins.timeseries(si.preset_range("today", now=NOW), bucket="day")
    assert series["types"]["bit"] == {"count": [2], "total": [7.0]}
    assert series["viewers"] == [1]


def test_metric_series():
    series = {
        "keys": ["a", "b"],
        "types": {"sub": {"count": [1, 0], "total": [0.0, 0.0]}, "resub": {"count": [2, 3], "total": [0.0, 0.0]},
                  "bit": {"count": [1, 1], "total": [100.0, 5.0]}},
        "viewers": [4, 5],
    }
    assert si.Insights.metric_series(series, si.METRICS_BY_KEY["subs"]) == [3, 3]
    assert si.Insights.metric_series(series, si.METRICS_BY_KEY["bits"]) == [100.0, 5.0]
    assert si.Insights.metric_series(series, si.METRICS_BY_KEY["follows"]) == [0.0, 0.0]
    assert si.Insights.metric_series(series, si.METRICS_BY_KEY["viewers"]) == [4, 5]
    assert si.Insights.metric_series({"keys": ["a"]}, si.METRICS_BY_KEY["viewers"]) == [0]


def test_heatmap_for_all_types(db, ins):
    sunday_noon = datetime(2026, 10, 18, 12, 0)
    add(db, [("A", "chat_message", 1, "", sunday_noon), ("B", "bit", 5, "", sunday_noon)])
    grid = ins.heatmap(si.preset_range("7d", now=NOW))
    assert len(grid) == 7 and all(len(r) == 24 for r in grid)
    assert grid[6][12] == 2 and sum(map(sum, grid)) == 2


def test_leaders_need_event_types_and_order_ties_by_event_count(db, ins):
    rng = si.preset_range("today", now=NOW)
    assert ins.leaders(rng, []) == []
    add(db, [("Ann", "bit", 100, "", NOW), ("Bob", "bit", 50, "", NOW), ("Bob", "bit", 50, "", NOW)])
    top = ins.leaders(rng, ("bit",), "sum", limit=5)
    assert [(r["username"], r["value"], r["events"]) for r in top] == [("Bob", 100.0, 2), ("Ann", 100.0, 1)]
    assert len(ins.leaders(rng, ("bit",), "sum", limit=1)) == 1
    with pytest.raises(KeyError):
        ins.leaders(rng, ("bit",), "median")


def test_recent_events_all_types_newest_first(db, ins):
    add(db, [
        ("Ann", "follow", 0, "", NOW - timedelta(hours=2)),
        ("Ann", "bit", 10, "Cheer", NOW - timedelta(hours=1)),
        ("Ann", "chat_message", 1, None, NOW),
    ])
    evs = ins.recent_events(si.preset_range("today", now=NOW), "ann")
    assert [e["event_type"] for e in evs] == ["chat_message", "bit", "follow"]
    assert evs[1] == {"event_type": "bit", "amount": 10.0, "alert_name": "Cheer", "timestamp": (NOW - timedelta(hours=1)).timestamp()}
    assert evs[0]["alert_name"] == ""
    assert len(ins.recent_events(si.preset_range("today", now=NOW), "ann", limit=1)) == 1


def test_user_span_for_an_unknown_viewer(ins):
    assert ins.user_span("nobody") == (None, None, 0)


def test_user_span_when_the_query_fails(db):
    mgr = FakeManager(db)
    mgr._stats_db_initialized = False
    assert si.Insights(mgr).user_span("x") == (None, None, 0)


# --------------------------------------------------------------------------- formatting


@pytest.mark.parametrize("cur,prev,expected", [(150, 100, 50.0), (50, 100, -50.0), (5, 0, None), (5, -1, None), (0, 10, -100.0)])
def test_pct_change(cur, prev, expected):
    assert si.pct_change(cur, prev) == expected


@pytest.mark.parametrize("n,text", [
    (None, "0"), (0, "0"), (7, "7"), (1284, "1,284"), (2.5, "2.5"), (1234.56, "1,234.6"), (9999, "9,999"),
    (10_000, "10K"), (12_900, "12.9K"), (-15_000, "-15K"), (1_000_000, "1M"), (4_250_000, "4.2M"),
])
def test_compact(n, text):
    assert si.compact(n) == text


def test_type_filters_and_viewer_span(db, ins):
    wed = datetime(2026, 10, 21, 9, 0)
    add(db, [("Ann", "chat_message", 1, "", wed), ("Ann", "bit", 5, "", wed + timedelta(hours=1)),
             ("Ann", "follow", 0, "", wed + timedelta(hours=2))])
    rng = si.preset_range("7d", now=NOW)
    grid = ins.heatmap(rng, ["bit", "follow"])
    assert grid[2][10] == 1 and grid[2][11] == 1 and grid[2][9] == 0
    evs = ins.recent_events(rng, "ann", ["bit", "chat_message"])
    assert [e["event_type"] for e in evs] == ["bit", "chat_message"]
    assert ins.user_span("ANN") == (wed.timestamp(), (wed + timedelta(hours=2)).timestamp(), 3)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""StatisticsManager event-log queries: per-user events, date-range summaries, top users,
watch streaks, export highlights (event log, fallback and lifetime sources) and the
diagnostics that describe the event log."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.statistics_manager as sm  # noqa: E402

T0 = 1_700_000_000.0
SYS = "__system__"


def _shutdown(m):
    m.is_running = False
    try:
        m._close_statistics_db()
    except Exception:
        pass
    for t in (m._event_flush_thread, m._username_backfill_thread):
        if isinstance(t, threading.Thread):
            t.join(2)
    with m._stats_db_pool_lock:
        for c in m._stats_db_pool:
            c.close()
        m._stats_db_pool.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "data_root"
    root.mkdir()
    monkeypatch.setattr(sm, "get_data_path", lambda rel: os.path.join(str(root), rel))
    monkeypatch.setattr(sm, "get_data", lambda path: None)
    monkeypatch.setattr(sm, "set_data", lambda path, value: True)
    import modules.notification_engine as ne

    notes = []
    monkeypatch.setattr(ne, "notify", lambda message, **kw: notes.append(kw))
    managers = []

    def make():
        m = sm.StatisticsManager()
        if m._username_backfill_thread is not None:
            m._username_backfill_thread.join(2)
        managers.append(m)
        return m

    yield SimpleNamespace(root=root, make=make, notes=notes)
    for m in managers:
        _shutdown(m)


@pytest.fixture
def mgr(env):
    return env.make()


def add(m, rows):
    """rows: (username, event_type, amount, alert_name, timestamp)."""
    conn = sqlite3.connect(m._stats_db_path)
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp, username_lower) "
        "VALUES (?, ?, ?, ?, ?, lower(?))",
        [(*r, r[0]) for r in rows],
    )
    conn.commit()
    conn.close()


class BrokenConn:
    def cursor(self):
        raise sqlite3.OperationalError("boom")

    def execute(self, *a, **k):
        raise sqlite3.OperationalError("boom")

    def commit(self):
        pass

    def close(self):
        pass


def use_conn(monkeypatch, m, conn):
    monkeypatch.setattr(m, "_get_stats_db_connection", lambda **k: conn)
    monkeypatch.setattr(m, "_return_stats_db_connection", lambda c: None)


def loose_db():
    """An in-memory user_events table that allows NULL usernames and odd timestamps."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE user_events (id INTEGER PRIMARY KEY, username TEXT, event_type TEXT, amount REAL, "
        "alert_name TEXT, timestamp, username_lower TEXT)"
    )
    return conn


# --------------------------------------------------------------------------- simple queries


QUERY_DEFAULTS = [
    ("get_user_events", ("Alice",), []),
    ("get_date_range_summary", (0, 1), {}),
    ("get_top_users_in_range", (0, 1), []),
    ("get_max_watch_streak_for_user", ("Alice",), 0),
    ("get_highest_watch_streak_event_in_range", (0, 1), None),
    ("get_top_users_by_peak_watch_streak", (0, 1), []),
    ("get_date_range_highlights", (0, 1), {}),
    ("get_event_log_summary", (), {"total_rows": 0, "min_timestamp": None, "max_timestamp": None}),
]


@pytest.mark.parametrize("name,args,default", QUERY_DEFAULTS)
@pytest.mark.parametrize("state", ["off", "no_connection", "broken"])
def test_queries_fall_back_to_empty_results(mgr, monkeypatch, name, args, default, state):
    if state == "off":
        monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    elif state == "no_connection":
        monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    else:
        use_conn(monkeypatch, mgr, BrokenConn())
    assert getattr(mgr, name)(*args) == default


def test_watch_streak_for_blank_user_is_zero(mgr):
    assert mgr.get_max_watch_streak_for_user("") == 0


def test_get_user_events_filters_and_orders(mgr):
    add(mgr, [
        ("Alice", "bit", 10, "a", T0),
        ("alice", "chat_message", 1, "", T0 + 10),
        ("ALICE", "bit", 20, "b", T0 + 20),
        ("Bob", "bit", 99, "", T0 + 5),
    ])
    # A row still waiting on the username_lower backfill is found too.
    conn = sqlite3.connect(mgr._stats_db_path)
    conn.execute("INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) VALUES ('AlIcE', 'follow', 0, '', ?)", (T0 + 30,))
    conn.commit()
    conn.close()
    all_rows = mgr.get_user_events("alice")
    assert [r["event_type"] for r in all_rows] == ["follow", "bit", "chat_message", "bit"]
    assert set(all_rows[0]) >= {"id", "username", "event_type", "amount", "alert_name", "timestamp", "username_lower", "created_at"}
    assert [r["amount"] for r in mgr.get_user_events("Alice", event_type="bit")] == [20.0, 10.0]
    window = mgr.get_user_events("alice", start_time=T0 + 5, end_time=T0 + 20)
    assert [r["timestamp"] for r in window] == [T0 + 20, T0 + 10]
    assert len(mgr.get_user_events("alice", limit=2)) == 2
    assert mgr.get_user_events("nobody") == []


def test_date_range_summary_groups_types_and_counts_users(mgr):
    add(mgr, [
        ("Alice", "bit", 10, "", T0),
        ("Alice", "bit", 15, "", T0 + 1),
        ("Bob", "follow", 0, "", T0 + 2),
        ("Old", "bit", 500, "", T0 - 100),
    ])
    s = mgr.get_date_range_summary(T0, T0 + 10)
    assert s == {
        "bit": {"count": 2, "total_amount": 25.0},
        "follow": {"count": 1, "total_amount": 0},
        "unique_users": 2,
        "total_events": 3,
    }


def test_top_users_in_range_by_type_and_overall(mgr):
    add(mgr, [
        ("Alice", "bit", 10, "", T0),
        ("Alice", "bit", 10, "", T0),
        ("Bob", "bit", 100, "", T0),
        ("Cara", "chat_message", 1, "", T0),
        ("Cara", "chat_message", 1, "", T0),
        ("Cara", "chat_message", 1, "", T0),
    ])
    by_bits = mgr.get_top_users_in_range(T0, T0, "bit")
    assert by_bits == [
        {"username": "Bob", "event_count": 1, "total_amount": 100.0},
        {"username": "Alice", "event_count": 2, "total_amount": 20.0},
    ]
    overall = mgr.get_top_users_in_range(T0, T0, limit=2)
    assert [r["username"] for r in overall] == ["Cara", "Alice"]
    assert overall[0]["total_amount"] == 3.0


def test_watch_streak_queries(mgr):
    add(mgr, [
        ("Kim", "watch_streak", 7, "Streak", T0),
        ("Kim", "watch_streak", 12, "Streak", T0 + 10),
        ("Lee", "watch_streak", 12, "", T0 + 5),
        ("Lee", "watch_streak", 3, "", T0 + 6),
        ("Lee", "watch_streak", 4, "", T0 + 7),
        ("Max", "watch_streak", 2, "", T0 + 8),
        ("Kim", "bit", 999, "", T0),
    ])
    assert mgr.get_max_watch_streak_for_user("KIM") == 12
    assert mgr.get_max_watch_streak_for_user("kim", start_time=T0, end_time=T0 + 5) == 7
    assert mgr.get_max_watch_streak_for_user("nobody") == 0
    top = mgr.get_highest_watch_streak_event_in_range(T0, T0 + 20)
    assert top == {"username": "Kim", "streak_count": 12, "alert_name": "Streak"}  # newest of the tie
    assert mgr.get_highest_watch_streak_event_in_range(T0 + 100, T0 + 200) is None
    peaks = mgr.get_top_users_by_peak_watch_streak(T0, T0 + 20)
    assert peaks == [
        {"username": "Lee", "max_streak": 12, "event_count": 3},
        {"username": "Kim", "max_streak": 12, "event_count": 2},
        {"username": "Max", "max_streak": 2, "event_count": 1},
    ]
    assert len(mgr.get_top_users_by_peak_watch_streak(T0, T0 + 20, limit=1)) == 1


def test_watch_streak_queries_report_missing_names_as_question_marks(mgr, monkeypatch):
    conn = loose_db()
    conn.execute("INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) VALUES (NULL, 'watch_streak', NULL, NULL, 5)")
    conn.row_factory = sqlite3.Row
    use_conn(monkeypatch, mgr, conn)
    assert mgr.get_highest_watch_streak_event_in_range(0, 10) == {"username": "?", "streak_count": 0, "alert_name": ""}
    assert mgr.get_top_users_by_peak_watch_streak(0, 10) == [{"username": "?", "max_streak": 0, "event_count": 1}]
    conn.close()


# --------------------------------------------------------------------------- highlights from the event log


def seed_highlights(m):
    rows = [
        ("Alice", "bit", 100, "", T0), ("Alice", "bit", 50, "", T0), ("Bob", "bit", 300, "", T0),
        ("Cara", "sub", 0, "", T0), ("Dan", "resub", 0, "", T0), ("Dan", "resub", 0, "", T0),
        ("Eve", "giftsub", 5, "", T0), ("Fay", "donation", 0, "", T0), ("Gus", "point_redeem", 1000, "", T0),
        ("Hal", "follow", 0, "", T0), ("Ivy", "raid", 0, "", T0),
        ("Alice", "chat_message", 1, "", T0), ("Alice", "chat_message", 1, "", T0), ("Alice", "chat_message", 1, "", T0),
        ("Bob", "chat_message", 1, "", T0),
        *[(SYS, "chat_message", 1, "", T0)] * 5,
        ("Jo", "giveaway_entry", 1, "", T0), ("Jo", "giveaway_win", 1, "", T0), (SYS, "giveaway_draw_complete", 1, "", T0),
        (SYS, "giveaway_entry", 1, "", T0),
        ("Kim", "watch_streak", 7, "", T0), ("Kim", "watch_streak", 12, "", T0), ("Lee", "watch_streak", 3, "", T0),
        ("Zed", "bit", 9999, "", T0 - 1000),  # outside the range
    ]
    add(m, rows)


def test_highlights_from_the_event_log(mgr):
    seed_highlights(mgr)
    conn = sqlite3.connect(mgr._stats_db_path)
    h = mgr._compute_highlights_from_user_events(conn.cursor(), T0 - 1, T0 + 1)
    conn.close()
    assert h["top_bits"] == [{"username": "Bob", "total": 300}, {"username": "Alice", "total": 150}]
    assert h["top_bit_donor"] == {"username": "Bob", "total": 300}
    assert h["total_bits"] == 450
    assert h["biggest_bit_donation"] == {"username": "Bob", "amount": 300}
    assert (h["total_new_subs"], h["total_resubs"], h["total_subs"]) == (1, 2, 3)
    assert h["top_new_subs"] == [{"username": "Cara", "count": 1}]
    assert h["top_resubs"] == [{"username": "Dan", "count": 2}]
    assert h["total_gift_subs"] == 5 and h["top_gift_subs"] == [{"username": "Eve", "total": 5}]
    assert h["total_donations"] == 1 and h["top_donations"] == [{"username": "Fay", "count": 1}]
    assert h["total_channel_points"] == 1000 and h["top_channel_points"] == [{"username": "Gus", "total": 1000}]
    assert h["total_follows"] == 1 and h["top_follows"] == [{"username": "Hal", "count": 1}]
    assert h["total_raids"] == 1 and h["top_raids"] == [{"username": "Ivy", "count": 1}]
    assert h["total_chat_messages"] == 9
    assert h["top_chat_messages"] == [{"username": "Alice", "count": 3}, {"username": "Bob", "count": 1}]
    assert (h["total_giveaway_entries"], h["total_giveaway_wins"], h["total_giveaway_rounds"]) == (2, 1, 1)
    assert h["top_giveaway_entries"] == [{"username": "Jo", "count": 1}]
    assert h["top_giveaway_wins"] == [{"username": "Jo", "count": 1}]
    assert h["total_watch_streak_alerts"] == 3
    assert h["biggest_watch_streak"] == {"username": "Kim", "amount": 12}
    assert h["top_watch_streak_peaks"] == [{"username": "Kim", "max_streak": 12}, {"username": "Lee", "max_streak": 3}]
    assert h["top_chatter"] == {"username": "Alice", "event_count": 3}
    assert h["most_active_user"] == {"username": "Alice", "event_count": 5}
    assert h["unique_users"] == 12
    assert h["total_events"] == 27


def test_highlights_from_an_empty_window(mgr):
    conn = sqlite3.connect(mgr._stats_db_path)
    h = mgr._compute_highlights_from_user_events(conn.cursor(), 0, 1)
    conn.close()
    assert h["top_bits"] == [] and h["top_bit_donor"] is None
    assert h["biggest_bit_donation"] is None and h["biggest_watch_streak"] is None
    assert h["top_chatter"] is None and h["most_active_user"] is None
    assert h["total_events"] == 0 and h["unique_users"] == 0 and h["total_bits"] == 0


def test_highlights_name_missing_users_with_a_question_mark(mgr):
    conn = loose_db()
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, timestamp, username_lower) VALUES (?, ?, ?, 5, ?)",
        [
            (None, "bit", None, None), (None, "sub", 0, None), (None, "watch_streak", 4, None),
            (None, "chat_message", 1, "ghost"),
        ],
    )
    h = mgr._compute_highlights_from_user_events(conn.cursor(), 0, 10)
    conn.close()
    assert h["top_bits"] == [{"username": "?", "total": 0}]
    assert h["top_bit_donor"] == {"username": "?", "total": 0}
    assert h["biggest_bit_donation"] == {"username": "?", "amount": 0}
    assert h["top_new_subs"] == [{"username": "?", "count": 1}]
    assert h["biggest_watch_streak"] == {"username": "?", "amount": 4}
    assert h["top_watch_streak_peaks"] == [{"username": "?", "max_streak": 4}]
    assert h["top_chatter"] == {"username": "?", "event_count": 1}
    assert h["most_active_user"] == {"username": "?", "event_count": 1}


def test_empty_highlights_template():
    t = sm.StatisticsManager._empty_highlights_template(7)
    assert t["total_events"] == 7 and t["_fallback_partial"] is True
    assert t["top_bits"] == [] and t["top_chatter"] is None
    assert sm.StatisticsManager._empty_highlights_template(None)["total_events"] == 0


def test_highlight_ranking_helpers_drop_zeros_and_sort():
    pairs = [("a", 1), ("b", 0), (None, 5), ("c", None), ("d", 3)]
    assert sm.StatisticsManager._highlights_top_by_total(pairs) == [
        {"username": "?", "total": 5}, {"username": "d", "total": 3}, {"username": "a", "total": 1}]
    assert sm.StatisticsManager._highlights_top_by_count(pairs, limit=2) == [
        {"username": "?", "count": 5}, {"username": "d", "count": 3}]


# --------------------------------------------------------------------------- fallback aggregation


class ScriptedCursor:
    """Wrap a cursor; statements containing any of ``fail`` raise sqlite3.Error."""

    def __init__(self, cursor, fail=()):
        self.cursor = cursor
        self.fail = fail
        self.seen = []

    def execute(self, sql, params=()):
        self.seen.append(sql)
        if any(f in sql for f in self.fail):
            raise sqlite3.OperationalError("scripted failure")
        return self.cursor.execute(sql, params)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


def test_fallback_counts_each_total_independently(mgr):
    seed_highlights(mgr)
    conn = sqlite3.connect(mgr._stats_db_path)
    cur = ScriptedCursor(conn.cursor(), fail=("'raid'",))
    out = mgr._compute_highlights_fallback(cur, T0 - 1, T0 + 1)
    conn.close()
    assert out["total_events"] == 27
    assert out["total_bits"] == 450
    assert (out["total_new_subs"], out["total_resubs"], out["total_subs"]) == (1, 2, 3)
    assert out["total_gift_subs"] == 5 and out["total_channel_points"] == 1000
    assert out["total_raids"] == 0  # that query failed; the default stays
    assert out["total_chat_messages"] == 9 and out["total_watch_streak_alerts"] == 3
    assert (out["total_giveaway_entries"], out["total_giveaway_wins"], out["total_giveaway_rounds"]) == (2, 1, 1)
    assert out["_fallback_partial"] is True and out["top_bits"] == []


def test_fallback_stops_early_when_empty_or_unreadable(mgr):
    conn = sqlite3.connect(mgr._stats_db_path)
    empty = mgr._compute_highlights_fallback(conn.cursor(), 0, 1)
    assert empty["total_events"] == 0
    broken = mgr._compute_highlights_fallback(ScriptedCursor(conn.cursor(), fail=("COUNT(*)",)), 0, 1)
    assert broken == sm.StatisticsManager._empty_highlights_template(0)
    conn.close()


def test_fallback_treats_a_missing_row_as_zero(mgr):
    cur = Mock()
    cur.fetchone.return_value = None
    out = mgr._compute_highlights_fallback(cur, 0, 1)
    assert out["total_events"] == 0


# --------------------------------------------------------------------------- export entry points


def test_date_range_highlights_uses_the_full_computation(mgr):
    seed_highlights(mgr)
    h = mgr.get_date_range_highlights(T0 - 1, T0 + 1)
    assert h["total_events"] == 27 and "_fallback_partial" not in h


def test_date_range_highlights_falls_back_when_full_computation_fails(mgr, monkeypatch):
    seed_highlights(mgr)
    monkeypatch.setattr(mgr, "_compute_highlights_from_user_events", Mock(side_effect=RuntimeError("bad row")))
    h = mgr.get_date_range_highlights(T0 - 1, T0 + 1)
    assert h["_fallback_partial"] is True and h["total_events"] == 27 and h["total_bits"] == 450


def test_date_range_highlights_outer_error_returns_empty(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_debug_print_user_events_db_layout", Mock(side_effect=RuntimeError("x")))
    assert mgr.get_date_range_highlights(0, 1) == {}


def test_highlights_for_export_prefers_the_event_log(mgr):
    seed_highlights(mgr)
    h, source = mgr.get_highlights_for_export(T0 - 1, T0 + 1)
    assert source == "event_log" and h["total_events"] == 27


def test_highlights_for_export_marks_fallback_results(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "get_date_range_highlights", lambda a, b: {"total_events": 3, "_fallback_partial": True})
    assert mgr.get_highlights_for_export(0, 1) == ({"total_events": 3, "_fallback_partial": True}, "event_log_fallback")


def test_highlights_for_export_uses_lifetime_totals_when_the_window_is_empty(mgr):
    mgr.increment_twitch_messages("Alice")
    h, source = mgr.get_highlights_for_export(0, 1)
    assert source == "lifetime" and h["_lifetime_fallback"] is True and h["total_chat_messages"] == 1


def test_highlights_for_export_empty(mgr):
    assert mgr.get_highlights_for_export(0, 1) == ({}, "empty")


# --------------------------------------------------------------------------- lifetime highlights


def test_lifetime_highlights_empty_when_nothing_recorded(mgr):
    assert mgr._compute_highlights_from_lifetime() == {}


def test_lifetime_highlights_from_in_memory_and_stored_users(mgr):
    a = mgr.data.alerts
    a.total_bits, a.bit_alerts_played, a.new_subs_played, a.resubs_played = 600, 3, 2, 1
    a.total_gift_subs, a.donations, a.follow_alerts_played, a.raids = 4, 1, 2, 1
    a.watch_streak_alerts_played, a.total_channel_points_redeemed = 2, 700
    a.user_stats = {
        "Alice": sm.UserAlertStatistics(total_bits=500, channel_points_redeemed=700, new_subs_played=2,
                                        total_gift_subs=4, donations=1, follow_alerts_played=1, raids=1,
                                        highest_watch_streak=9, total_alerts=8),
        "Bob": sm.UserAlertStatistics(total_bits=100, resubs_played=1, follow_alerts_played=1, highest_watch_streak=3, total_alerts=2),
        SYS: sm.UserAlertStatistics(total_bits=10_000, total_alerts=100),
        "NoPeak": sm.UserAlertStatistics(),
    }
    mgr.data.chat.total_messages = 12
    mgr.data.chat.user_stats = {
        "Cara": sm.UserChatStatistics(total_messages=10),
        "Dan": sm.UserChatStatistics(total_messages=0, twitch_messages_received=2),
        "Quiet": sm.UserChatStatistics(),
        SYS: sm.UserChatStatistics(total_messages=50),
    }
    mgr.data.connectors.total_triggers = 1
    mgr.data.connectors.user_connector_stats = {"Eve": sm.UserConnectorStatistics(total_triggers=1)}
    mgr.data.chatbot.total_interactions = 1
    mgr.data.giveaways.total_entry_events = 5
    mgr.data.giveaways.giveaways_completed = 2
    mgr.data.giveaways.user_wins = {"Fay": 2, SYS: 9, "Nil": 0}
    # Stored users the export has never touched this session.
    mgr._spill_raw_user_map("connectors.user_stats", {"Gus": {"total_triggers": 4}})
    mgr._spill_raw_user_map("chatbot.user_stats", {"Hal": {"total_interactions": 3}})
    mgr._spill_raw_user_map("giveaways.user_wins", {"Ivy": {"wins": 1}})

    h = mgr._compute_highlights_from_lifetime()
    assert h["_lifetime_fallback"] is True and h["_fallback_partial"] is True
    assert h["top_bits"] == [{"username": "Alice", "total": 500}, {"username": "Bob", "total": 100}]
    assert h["top_bit_donor"] == {"username": "Alice", "total": 500}
    assert h["total_bits"] == 600 and h["biggest_bit_donation"] is None
    assert h["top_channel_points"] == [{"username": "Alice", "total": 700}]
    assert h["top_new_subs"] == [{"username": "Alice", "count": 2}]
    assert h["top_resubs"] == [{"username": "Bob", "count": 1}]
    assert h["top_gift_subs"] == [{"username": "Alice", "total": 4}]
    assert h["top_donations"] == [{"username": "Alice", "count": 1}]
    assert [r["username"] for r in h["top_follows"]] == ["Alice", "Bob"]
    assert h["top_raids"] == [{"username": "Alice", "count": 1}]
    assert h["top_watch_streak_peaks"] == [{"username": "Alice", "max_streak": 9}, {"username": "Bob", "max_streak": 3}]
    assert h["biggest_watch_streak"] == {"username": "Alice", "amount": 9}
    assert h["top_chat_messages"] == [{"username": "Cara", "count": 10}, {"username": "Dan", "count": 2}]
    assert h["top_chatter"] == {"username": "Cara", "event_count": 10}
    assert h["top_giveaway_wins"] == [{"username": "Fay", "count": 2}, {"username": "Ivy", "count": 1}]
    assert h["total_giveaway_wins"] == 12  # includes every stored win, system too
    assert h["most_active_user"] == {"username": "Cara", "event_count": 10}
    assert h["unique_users"] == len({"Alice", "Bob", "NoPeak", "Cara", "Dan", "Quiet", "Eve", "Gus", "Hal", "Fay", "Ivy", "Nil"})
    assert (h["total_subs"], h["total_giveaway_rounds"], h["total_giveaway_entries"]) == (3, 2, 5)
    expected_score = 12 + 3 + 600 + 700 + 2 + 1 + 4 + 1 + 2 + 1 + 2 + 1 + 5 + 1 + 2
    assert h["total_events"] == expected_score
    assert h["top_giveaway_entries"] == []


def test_lifetime_highlights_with_only_users_counts_at_least_one_event(mgr):
    mgr.data.chat.user_stats = {"Lurker": sm.UserChatStatistics()}
    h = mgr._compute_highlights_from_lifetime()
    assert h["total_events"] == 1 and h["unique_users"] == 1
    assert h["most_active_user"] is None and h["top_chatter"] is None
    assert h["biggest_watch_streak"] is None and h["top_bit_donor"] is None


# --------------------------------------------------------------------------- diagnostics


def test_debug_layout_logs_paths_ranges_and_samples(mgr, caplog, tmp_path):
    add(mgr, [("A", "bit", 1, "", T0 * 1000), ("B", "follow", 0, "", T0)])
    (Path(mgr._stats_db_path).parent.parent / "statistics.db").write_bytes(b"x")  # a legacy root file
    conn = sqlite3.connect(mgr._stats_db_path)
    with caplog.at_level(logging.DEBUG, logger="modules.statistics_manager"):
        mgr._debug_print_user_events_db_layout(conn.cursor(), T0, T0 + 10)
    conn.close()
    text = caplog.text
    assert "legacy_root exists=True size_bytes=1" in text
    assert "total_rows= 2" in text
    assert "values may be milliseconds" in text
    assert "rows_in_export_range= 1" in text
    assert "event_type counts" in text and "sample rows" in text
    assert "---- end layout ----" in text


def test_debug_layout_with_empty_table_and_unusual_values(mgr, caplog, monkeypatch):
    conn = loose_db()
    monkeypatch.setattr(mgr, "_stats_db_path", None)
    with caplog.at_level(logging.DEBUG, logger="modules.statistics_manager"):
        mgr._debug_print_user_events_db_layout(conn.cursor(), None, "not-a-time")
        conn.execute("INSERT INTO user_events (username, event_type, timestamp) VALUES ('a', 'x', 'abc')")
        mgr._debug_print_user_events_db_layout(conn.cursor(), float("nan"), 0)
    conn.close()
    text = caplog.text
    assert "active: (no path)" in text
    assert "start_time=None" in text and "end_time='not-a-time' (no iso" in text
    assert "max_timestamp='abc' (no iso" in text
    assert "start_time=nan (no iso" in text


def test_debug_layout_handles_a_database_without_the_table(mgr, caplog):
    conn = sqlite3.connect(":memory:")
    with caplog.at_level(logging.DEBUG, logger="modules.statistics_manager"):
        mgr._debug_print_user_events_db_layout(conn.cursor(), 0, 1)
    conn.close()
    assert "no table user_events in this database file" in caplog.text


def test_debug_layout_survives_failing_statements(mgr, caplog, monkeypatch):
    conn = sqlite3.connect(mgr._stats_db_path)
    failing = ScriptedCursor(conn.cursor(), fail=("PRAGMA", "SELECT sql", "MIN(timestamp)",
                                                  "COUNT(*) FROM user_events WHERE", "GROUP BY event_type",
                                                  "ORDER BY timestamp"))
    monkeypatch.setattr(sm.os.path, "getsize", Mock(side_effect=OSError("stat failed")))
    with caplog.at_level(logging.DEBUG, logger="modules.statistics_manager"):
        mgr._debug_print_user_events_db_layout(failing, 0, 1)
        mgr._debug_print_user_events_db_layout(ScriptedCursor(conn.cursor(), fail=("sqlite_master",)), 0, 1)
    conn.close()
    text = caplog.text
    for msg in ("PRAGMA table_info failed", "sqlite_master sql fetch failed", "COUNT/MIN/MAX timestamp failed",
                "rows_in_range query failed", "event_type breakdown failed", "sample rows failed",
                "path stat error", "sqlite_master check failed"):
        assert msg in text, msg


def test_event_log_summary(mgr):
    assert mgr.get_event_log_summary() == {"total_rows": 0, "min_timestamp": None, "max_timestamp": None}
    add(mgr, [("A", "x", 0, "", 5.0), ("B", "x", 0, "", 9.0)])
    assert mgr.get_event_log_summary() == {"total_rows": 2, "min_timestamp": 5.0, "max_timestamp": 9.0}


def test_event_log_summary_with_null_timestamps(mgr, monkeypatch):
    conn = loose_db()
    conn.execute("INSERT INTO user_events (username, event_type) VALUES ('a', 'x')")
    use_conn(monkeypatch, mgr, conn)
    assert mgr.get_event_log_summary() == {"total_rows": 1, "min_timestamp": None, "max_timestamp": None}
    conn.close()


# --------------------------------------------------------------------------- tracked usernames


def test_in_memory_tracked_usernames_cover_every_map(mgr):
    d = mgr.data
    d.alerts.user_stats["a"] = sm.UserAlertStatistics()
    d.connectors.user_stats["b"] = sm.UserConnectorStatistics()
    d.connectors.user_connector_stats["c"] = sm.UserConnectorStatistics()
    d.chatbot.user_stats["d"] = sm.UserChatbotStatistics()
    d.quotes.user_stats["e"] = sm.UserQuoteStatistics()
    d.chat.user_stats["f"] = sm.UserChatStatistics()
    d.giveaways.user_wins["g"] = 1
    assert mgr._in_memory_tracked_usernames() == set("abcdefg")


@pytest.mark.parametrize("name,expected", [("ALICE", "Alice"), ("  alice  ", "Alice"), ("bob", None), ("", None), ("   ", None), (None, None)])
def test_dict_key_case_insensitive(name, expected):
    assert sm.StatisticsManager._dict_key_case_insensitive({"Alice": 1, "Zed": 2}, name) == expected


def test_all_tracked_usernames_merges_memory_and_stored_rows(mgr, monkeypatch):
    mgr.data.chat.user_stats["Live"] = sm.UserChatStatistics()
    mgr._spill_raw_user_map("alerts.user_stats", {"Stored": {}, "Live": {}})
    assert mgr.get_all_tracked_usernames() == ["Live", "Stored"]
    use_conn(monkeypatch, mgr, BrokenConn())
    assert mgr.get_all_tracked_usernames() == ["Live"]
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    assert mgr.get_all_tracked_usernames() == ["Live"]
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    assert mgr.get_all_tracked_usernames() == ["Live"]

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""StatisticsManager storage layer: statistics.db setup, legacy migrations, the connection
pool, batched event writes, per-user lifetime rows and lifetime totals."""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- fixtures


def _shutdown(m: sm.StatisticsManager) -> None:
    m.is_running = False
    try:
        m._close_statistics_db()
    except Exception:
        pass
    for t in (m._event_flush_thread, m._username_backfill_thread, m._periodic_thread):
        if isinstance(t, threading.Thread) and t is not threading.current_thread():
            t.join(2)
    with m._stats_db_pool_lock:
        for c in m._stats_db_pool:
            try:
                c.close()
            except Exception:
                pass
        m._stats_db_pool.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Each test gets its own data folder, fake legacy store and notification log."""
    root = tmp_path / "data_root"
    root.mkdir()
    monkeypatch.setattr(sm, "get_data_path", lambda rel: os.path.join(str(root), rel))
    old = {"value": None}
    monkeypatch.setattr(sm, "get_data", lambda path: old["value"])
    saved = []
    set_result = {"value": True}

    def fake_set_data(path, value):
        saved.append((path, value))
        return set_result["value"]

    monkeypatch.setattr(sm, "set_data", fake_set_data)
    notes = []
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda message, **kw: notes.append(dict(kw, message=message)))
    managers = []

    def make():
        m = sm.StatisticsManager()
        if m._username_backfill_thread is not None:
            m._username_backfill_thread.join(2)
        managers.append(m)
        return m

    ns = SimpleNamespace(root=root, old=old, saved=saved, set_result=set_result, notes=notes, make=make)
    yield ns
    for m in managers:
        _shutdown(m)


@pytest.fixture
def mgr(env):
    return env.make()


def q(m, sql, params=()):
    conn = sqlite3.connect(m._stats_db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def run_sql(m, sql, params=()):
    conn = sqlite3.connect(m._stats_db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def stop_flusher(m):
    m._event_batch_stop.set()
    m._event_flush_now.set()
    m._event_flush_wake.set()
    m._event_flush_thread.join(2)
    assert not m._event_flush_thread.is_alive()


class BrokenConn:
    """A pooled connection that fails every statement."""

    def cursor(self):
        raise sqlite3.OperationalError("boom")

    def execute(self, *a, **k):
        raise sqlite3.OperationalError("boom")

    def executemany(self, *a, **k):
        raise sqlite3.OperationalError("boom")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def use_conn(monkeypatch, m, conn):
    monkeypatch.setattr(m, "_get_stats_db_connection", lambda **k: conn)
    monkeypatch.setattr(m, "_return_stats_db_connection", lambda c: None)


def make_legacy_db(path: Path, rows, *, table=True, columns="username, event_type, amount, alert_name, timestamp"):
    conn = sqlite3.connect(path)
    if table:
        col_defs = ", ".join(f"{c.strip()} {'REAL' if c.strip() in ('amount', 'timestamp') else 'TEXT'}" for c in columns.split(","))
        conn.execute(f"CREATE TABLE user_events (id INTEGER PRIMARY KEY AUTOINCREMENT, {col_defs})")
        placeholders = ",".join("?" * len(columns.split(",")))
        conn.executemany(f"INSERT INTO user_events ({columns}) VALUES ({placeholders})", rows)
    else:
        conn.execute("CREATE TABLE other (x)")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- dataclasses, helpers


def test_dataclass_defaults_are_fresh_per_instance():
    a, b = sm.StatisticsData(), sm.StatisticsData()
    assert a.alerts.user_stats is not b.alerts.user_stats
    assert a.hype_trains.level_completions == {} and a.hype_trains.total_completions == 0
    assert a.polls == sm.PollStatistics(polls_completed=0, total_votes=0, options={})
    assert a.giveaways.user_wins == {} and a.templates == {}
    assert isinstance(a.session.start_time, float) and a.session.session_duration == 0.0
    u = sm.UserAlertStatistics()
    assert (u.highest_watch_streak, u.total_bits, u.total_alerts) == (0, 0, 0)
    assert sm.UserConnectorStatistics().connector_usage == {}
    assert sm.UserCommandStatistics().command_usage == {}
    assert sm.UserEventStatistics().event_usage == {}
    assert sm.UserQuoteStatistics().individual_quote_usage == {}
    assert sm.IndividualCommandStatistics(command_name="!hi").usage_count == 0
    assert sm.IndividualEventStatistics(event_name="e").trigger_count == 0
    assert sm.IndividualConnectorStatistics(connector_name="c").trigger_count == 0
    assert sm.IndividualAlertTypeStatistics(alert_type="bit").alert_name == ""
    assert sm.TemplateStatistics(template_name="t").custom_stats == {}
    assert sm.UserChatStatistics().total_messages == 0
    assert sm.UserChatbotStatistics().total_interactions == 0


def test_notify_stats_problem_passes_every_field(env):
    sm._notify_stats_problem("Title", "Msg", hint="Hint", details="Det", key="k1", cooldown=12, type="error")
    assert env.notes == [{
        "message": "Msg",
        "type": "error",
        "source": "statistics",
        "title": "Title",
        "hint": "Hint",
        "details": "Det",
        "dedupe_key": "k1",
        "dedupe_cooldown_sec": 12,
        "actions": [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "Statistics"}],
    }]


def test_notify_stats_problem_defaults_to_warning(env):
    sm._notify_stats_problem("T", "M", hint="", details="", key="k", cooldown=1)
    assert env.notes[0]["type"] == "warning"


def test_notify_stats_problem_never_raises(monkeypatch):
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", Mock(side_effect=RuntimeError("toast broke")))
    sm._notify_stats_problem("T", "M", hint="", details="", key="k", cooldown=1)  # no exception


def test_stats_data_locked_holds_the_lock_and_keeps_metadata():
    class Obj:
        def __init__(self):
            self._data_lock = threading.RLock()

        @sm._stats_data_locked
        def work(self, x):
            """Doc text."""
            got = []
            t = threading.Thread(target=lambda: got.append(self._data_lock.acquire(blocking=False)))
            t.start()
            t.join()
            return x, got[0]

    assert Obj().work(3) == (3, False)
    assert Obj.work.__name__ == "work"
    assert Obj.work.__doc__ == "Doc text."


# --------------------------------------------------------------------------- init and schema


def test_init_creates_database_tables_indexes_and_threads(mgr):
    assert mgr._stats_db_initialized is True
    assert mgr._stats_db_path.endswith(os.path.join("data", "statistics.db"))
    tables = {r[0] for r in q(mgr, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"user_events", "user_lifetime", "lifetime_totals", "migrations"} <= tables
    cols = {r[1] for r in q(mgr, "PRAGMA table_info(user_events)")}
    assert {"username", "event_type", "amount", "alert_name", "timestamp", "username_lower"} <= cols
    indexes = {r[0] for r in q(mgr, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_ue_username", "idx_ue_event_type", "idx_ue_timestamp", "idx_ue_user_type_ts",
            "idx_ue_type_ts", "idx_ue_user_lower_type_ts"} <= indexes
    assert q(mgr, "PRAGMA journal_mode")[0][0] == "wal"
    assert mgr._event_flush_thread.name == "StatsEventFlush"
    assert mgr._event_flush_thread.is_alive()
    assert mgr._username_backfill_thread.name == "StatsUsernameBackfill"
    assert mgr.save_interval == 300 and mgr.database_path == "statistics"


def test_init_failure_turns_statistics_off_and_tells_the_user(env):
    # A directory where the database file should be: sqlite cannot open it.
    (env.root / "data" / "statistics.db").mkdir(parents=True)
    m = env.make()
    assert m._stats_db_initialized is False
    assert m._event_flush_thread is None
    note = env.notes[-1]
    assert note["dedupe_key"] == "stats:init_failed"
    assert note["type"] == "error"
    assert note["dedupe_cooldown_sec"] == 86400
    assert "statistics.db" in note["details"]
    assert note["title"] == "Statistics are off for this session"


def test_init_failure_before_path_is_known_names_the_default_file(env, monkeypatch):
    def boom(rel):
        raise OSError("no data folder")

    monkeypatch.setattr(sm, "get_data_path", boom)
    m = env.make()
    assert m._stats_db_initialized is False
    assert env.notes[-1]["details"].startswith("statistics.db: no data folder")


def test_create_tables_is_idempotent(mgr):
    conn = sqlite3.connect(mgr._stats_db_path)
    try:
        mgr._create_stats_tables(conn)
        mgr._create_stats_tables(conn)
    finally:
        conn.close()
    cols = [r[1] for r in q(mgr, "PRAGMA table_info(user_events)")]
    assert cols.count("username_lower") == 1


def test_username_lower_column_added_to_an_old_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE user_events (id INTEGER PRIMARY KEY, username TEXT, event_type TEXT, timestamp REAL)")
    cur = conn.cursor()
    sm.StatisticsManager._ensure_username_lower_column(None, cur)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_events)")}
    assert "username_lower" in cols
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_ue_user_lower_type_ts" in names
    conn.close()


def test_username_match_clause_matches_lowercase_and_backfill_pending(mgr):
    clause, params = mgr._username_match_clause("ViewerOne")
    assert params == ["viewerone", "viewerone"]
    assert "username_lower = ?" in clause and "lower(username) = ?" in clause


# --------------------------------------------------------------------------- username_lower backfill


def test_backfill_fills_missing_lowercase_names_then_stops(mgr):
    conn = sqlite3.connect(mgr._stats_db_path)
    conn.executemany(
        "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) VALUES (?, 'chat_message', 1, '', 1.0)",
        [("MixedCase",), ("UPPER",)],
    )
    conn.commit()
    conn.close()
    mgr._username_backfill_stop.clear()
    mgr._backfill_username_lower_loop()
    assert sorted(q(mgr, "SELECT username_lower FROM user_events")) == [("mixedcase",), ("upper",)]


def test_backfill_returns_when_stopped_between_batches(mgr, monkeypatch):
    run_sql(mgr, "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) VALUES ('Abc', 'x', 0, '', 1)")
    mgr._username_backfill_stop.clear()
    original = mgr._return_stats_db_connection

    def return_and_stop(conn):
        original(conn)
        mgr._username_backfill_stop.set()

    calls = []
    monkeypatch.setattr(mgr, "_return_stats_db_connection", lambda c: (calls.append(c), return_and_stop(c)))
    mgr._backfill_username_lower_loop()
    assert len(calls) == 1  # one batch, then the stop was honoured
    assert q(mgr, "SELECT username_lower FROM user_events") == [("abc",)]


def test_backfill_without_a_connection_or_with_an_error_just_returns(mgr, monkeypatch):
    mgr._username_backfill_stop.clear()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    mgr._backfill_username_lower_loop()
    use_conn(monkeypatch, mgr, BrokenConn())
    mgr._backfill_username_lower_loop()  # logged, no exception


def test_backfill_does_nothing_when_already_stopped(mgr, monkeypatch):
    mgr._username_backfill_stop.set()
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._backfill_username_lower_loop()
    get.assert_not_called()


# --------------------------------------------------------------------------- legacy root DB migration


def test_legacy_root_statistics_db_is_imported_once(env):
    make_legacy_db(env.root / "statistics.db", [("Alice", "bit", 100, "Cheer", 10.0), ("Bob", "follow", 0, "", 20.0)])
    m = env.make()
    rows = q(m, "SELECT username, event_type, amount, alert_name, timestamp, username_lower FROM user_events ORDER BY timestamp")
    assert rows == [("Alice", "bit", 100.0, "Cheer", 10.0, "alice"), ("Bob", "follow", 0.0, "", 20.0, "bob")]
    assert q(m, "SELECT migration_name FROM migrations") == [("legacy_root_statistics_user_events",)]


def test_legacy_import_works_on_an_autocommit_connection(mgr, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    make_legacy_db(legacy, [("Alice", "bit", 100, "Cheer", 10.0), ("Bob", "follow", 0, "", 20.0)])
    monkeypatch.setattr(sm, "get_data_path", lambda rel: str(legacy))
    target = sqlite3.connect(":memory:", isolation_level=None)
    mgr._create_stats_tables(target)
    mgr._migrate_legacy_root_statistics_db_if_needed(target)
    assert target.execute("SELECT username, event_type, amount, alert_name, timestamp FROM user_events ORDER BY timestamp").fetchall() == [
        ("Alice", "bit", 100.0, "Cheer", 10.0), ("Bob", "follow", 0.0, "", 20.0)]
    assert target.execute("SELECT migration_name FROM migrations").fetchall() == [("legacy_root_statistics_user_events",)]
    mgr._migrate_legacy_root_statistics_db_if_needed(target)  # recorded: runs once
    assert target.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 2
    target.close()


def test_legacy_import_skipped_when_current_db_already_has_events(env):
    m = env.make()
    run_sql(m, "INSERT INTO user_events (username, event_type, amount, alert_name, timestamp) VALUES ('Cur', 'x', 0, '', 1)")
    _shutdown(m)
    make_legacy_db(env.root / "statistics.db", [("Alice", "bit", 1, "", 1.0)])
    again = env.make()
    assert q(again, "SELECT username FROM user_events") == [("Cur",)]
    assert q(again, "SELECT COUNT(*) FROM migrations")[0][0] == 0


@pytest.mark.parametrize("kind", ["missing", "no_table", "empty", "corrupt"])
def test_legacy_import_ignores_unusable_legacy_files(env, kind):
    legacy = env.root / "statistics.db"
    if kind == "no_table":
        make_legacy_db(legacy, [], table=False)
    elif kind == "empty":
        make_legacy_db(legacy, [])
    elif kind == "corrupt":
        legacy.write_bytes(b"this is not a sqlite database" * 50)
    m = env.make()
    assert m._stats_db_initialized
    assert q(m, "SELECT COUNT(*) FROM user_events")[0][0] == 0
    assert q(m, "SELECT COUNT(*) FROM migrations")[0][0] == 0


def test_legacy_import_failure_rolls_back_and_retries_next_time(env):
    # The legacy table has no alert_name column, so the INSERT ... SELECT fails.
    make_legacy_db(env.root / "statistics.db", [("Alice", "bit", 5, 1.0)], columns="username, event_type, amount, timestamp")
    m = env.make()
    assert q(m, "SELECT COUNT(*) FROM user_events")[0][0] == 0
    assert q(m, "SELECT COUNT(*) FROM migrations")[0][0] == 0
    # The connection is still usable (legacy detached).
    assert q(m, "PRAGMA database_list")[0][1] == "main"


def test_legacy_import_survives_attach_and_rollback_errors(mgr, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    make_legacy_db(legacy, [("Alice", "bit", 5, "", 1.0)])
    monkeypatch.setattr(sm, "get_data_path", lambda rel: str(legacy))
    real = sqlite3.connect(":memory:")
    mgr._create_stats_tables(real)

    class AttachFails:
        def cursor(self):
            return real.cursor()

        def execute(self, sql, *a):
            if "ATTACH" in sql or "DETACH" in sql:
                raise sqlite3.OperationalError("cannot attach")
            return real.execute(sql, *a)

        def rollback(self):
            raise RuntimeError("rollback failed too")

        def commit(self):
            real.commit()

    mgr._migrate_legacy_root_statistics_db_if_needed(AttachFails())
    assert real.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 0
    real.close()


def test_legacy_import_skipped_when_legacy_is_the_active_file(mgr, monkeypatch, tmp_path):
    same = tmp_path / "same.db"
    make_legacy_db(same, [("Alice", "bit", 5, "", 1.0)])
    monkeypatch.setattr(sm, "get_data_path", lambda rel: str(same))
    monkeypatch.setattr(mgr, "_stats_db_path", str(same))
    target = sqlite3.connect(":memory:")
    mgr._create_stats_tables(target)
    mgr._migrate_legacy_root_statistics_db_if_needed(target)
    assert target.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 0
    target.close()


def test_legacy_import_precheck_error_is_ignored(mgr):
    bare = sqlite3.connect(":memory:")  # no migrations table
    mgr._migrate_legacy_root_statistics_db_if_needed(bare)
    bare.close()


# --------------------------------------------------------------------------- connection pool


def test_configure_connection_tolerates_pragma_errors():
    conn = Mock()
    conn.execute.side_effect = sqlite3.OperationalError("read only")
    sm.StatisticsManager._configure_stats_sqlite_connection(conn)
    conn.execute.assert_called_once_with("PRAGMA journal_mode=WAL")


def test_connection_pool_reuses_and_caps_connections(mgr):
    with mgr._stats_db_pool_lock:
        for c in mgr._stats_db_pool:
            c.close()
        mgr._stats_db_pool.clear()
    conns = [mgr._get_stats_db_connection(flush_events=False) for _ in range(4)]
    assert len({id(c) for c in conns}) == 4
    assert all(c.row_factory is sqlite3.Row for c in conns)
    for c in conns:
        mgr._return_stats_db_connection(c)
    assert mgr._stats_db_pool == conns[:3]
    with pytest.raises(sqlite3.ProgrammingError):
        conns[3].execute("SELECT 1")  # the extra one was closed
    assert mgr._get_stats_db_connection(flush_events=False) is conns[2]
    mgr._return_stats_db_connection(conns[2])
    mgr._return_stats_db_connection(None)  # ignored
    assert len(mgr._stats_db_pool) == 3


def test_no_connection_when_statistics_are_off(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    assert mgr._get_stats_db_connection() is None
    monkeypatch.setattr(mgr, "_stats_db_initialized", True)
    monkeypatch.setattr(mgr, "_stats_db_path", None)
    assert mgr._get_stats_db_connection() is None


def test_getting_a_connection_flushes_pending_events_first(mgr):
    stop_flusher(mgr)
    mgr._event_batch.append(("Zed", "follow", 0, "", 5.0))
    conn = mgr._get_stats_db_connection()
    mgr._return_stats_db_connection(conn)
    assert q(mgr, "SELECT username, username_lower FROM user_events") == [("Zed", "zed")]
    assert mgr._event_batch == []


def test_close_flushes_pending_events_and_closes_pool(mgr):
    stop_flusher(mgr)
    mgr._event_batch.append(("Late", "chat_message", 1, "", 9.0))
    pooled = mgr._get_stats_db_connection(flush_events=False)
    mgr._return_stats_db_connection(pooled)
    mgr._close_statistics_db()
    assert mgr._stats_db_pool == []
    assert mgr._username_backfill_stop.is_set() and mgr._event_batch_stop.is_set()
    assert q(mgr, "SELECT username FROM user_events") == [("Late",)]
    with pytest.raises(sqlite3.ProgrammingError):
        pooled.execute("SELECT 1")


def test_close_ignores_connections_that_fail_to_close(mgr):
    stop_flusher(mgr)
    bad = Mock()
    bad.close.side_effect = RuntimeError("already gone")
    with mgr._stats_db_pool_lock:
        mgr._stats_db_pool[:] = [bad]
    mgr._close_statistics_db()
    bad.close.assert_called_once()
    assert mgr._stats_db_pool == []


# --------------------------------------------------------------------------- event batching


def test_record_event_ignores_missing_user_or_database(mgr, monkeypatch):
    stop_flusher(mgr)
    mgr._record_event("", "chat_message")
    mgr._record_event(None, "chat_message")
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    mgr._record_event("Alice", "chat_message")
    assert mgr._event_batch == []


def test_record_event_queues_a_timestamped_row_and_wakes_the_flusher(mgr, monkeypatch):
    stop_flusher(mgr)
    mgr._event_flush_wake.clear()
    monkeypatch.setattr(sm.time, "time", lambda: 123.5)
    mgr._record_event("Alice", "bit", 50, "Cheer")
    assert mgr._event_batch == [("Alice", "bit", 50, "Cheer", 123.5)]
    assert mgr._event_flush_wake.is_set()


def test_full_batch_with_live_flusher_signals_instead_of_writing(mgr, monkeypatch):
    stop_flusher(mgr)
    mgr._event_flush_now.clear()
    monkeypatch.setattr(mgr, "_event_flush_thread", Mock(is_alive=Mock(return_value=True)))
    flush = Mock()
    monkeypatch.setattr(mgr, "_flush_event_rows", flush)
    for i in range(50):
        mgr._record_event(f"u{i}", "chat_message", 1.0)
    flush.assert_not_called()
    assert len(mgr._event_batch) == 50
    assert mgr._event_flush_now.is_set()


def test_full_batch_without_flusher_is_written_inline(mgr):
    stop_flusher(mgr)
    for i in range(49):
        mgr._record_event(f"u{i}", "chat_message", 1.0)
    assert len(mgr._event_batch) == 49
    mgr._record_event("last", "chat_message", 1.0)
    assert mgr._event_batch == []
    assert q(mgr, "SELECT COUNT(*) FROM user_events")[0][0] == 50


def test_flush_event_rows_ignores_empty_input_and_off_database(mgr, monkeypatch):
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._flush_event_rows([])
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    mgr._flush_event_rows([("a", "b", 0, "", 1.0)])
    get.assert_not_called()


def test_flush_without_connection_puts_rows_back_in_front(mgr, monkeypatch):
    stop_flusher(mgr)
    mgr._event_batch = [("later", "x", 0, "", 3.0)]
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    rows = [("a", "x", 0, "", 1.0), ("b", "x", 0, "", 2.0)]
    mgr._flush_event_rows(rows)
    assert [r[0] for r in mgr._event_batch] == ["a", "b", "later"]


def test_repeated_write_failures_keep_rows_and_notify_once(mgr, env, monkeypatch):
    stop_flusher(mgr)
    use_conn(monkeypatch, mgr, BrokenConn())
    row = ("a", "x", 0, "", 1.0)
    for _ in range(2):
        mgr._flush_event_rows([row])
    assert not [n for n in env.notes if n["dedupe_key"] == "stats:events_write"]
    mgr._flush_event_rows([row])
    mgr._flush_event_rows([row])
    notes = [n for n in env.notes if n["dedupe_key"] == "stats:events_write"]
    assert len(notes) == 1 and notes[0]["details"] == "boom" and notes[0]["dedupe_cooldown_sec"] == 1800
    assert mgr._event_write_failures == 4
    assert mgr._event_batch == [row] * 4
    monkeypatch.undo()
    mgr._flush_pending_events()
    assert mgr._event_write_failures == 0
    assert q(mgr, "SELECT COUNT(*) FROM user_events")[0][0] == 4


def test_flush_loop_with_stop_already_set_only_flushes_once(mgr):
    stop_flusher(mgr)
    mgr._event_batch = [("Solo", "follow", 0, "", 1.0)]
    mgr._event_flush_loop()
    assert q(mgr, "SELECT username FROM user_events") == [("Solo",)]


def test_flush_loop_flushes_each_wakeup_and_exits_on_stop(mgr):
    stop_flusher(mgr)
    stop = threading.Event()
    wakes = []

    class Wake:
        def wait(self, timeout=None):
            wakes.append(timeout)
            if wakes.count(None) == 2:
                stop.set()
            return True

        def clear(self):
            wakes.append("clear")

    now = threading.Event()
    now.set()
    mgr._event_batch_stop = stop
    mgr._event_flush_wake = Wake()
    mgr._event_flush_now = now
    mgr._event_batch = [("A", "follow", 0, "", 1.0)]
    mgr._event_flush_loop()
    assert wakes == [None, "clear", None]
    assert not now.is_set()  # cleared after the first wake
    assert q(mgr, "SELECT username FROM user_events") == [("A",)]


def test_background_flusher_writes_a_single_event(mgr):
    mgr._record_event("Bg", "follow", 0, "")
    mgr._event_flush_now.set()
    for _ in range(100):
        if q(mgr, "SELECT COUNT(*) FROM user_events")[0][0]:
            break
        threading.Event().wait(0.01)
    assert q(mgr, "SELECT username FROM user_events") == [("Bg",)]


# --------------------------------------------------------------------------- per-user lifetime rows


def lifetime_rows(m, category=None):
    sql = "SELECT username_lower, category, username, data_json FROM user_lifetime"
    if category:
        return q(m, sql + " WHERE category = ? ORDER BY username_lower", (category,))
    return q(m, sql + " ORDER BY category, username_lower")


def test_spill_skips_rows_that_are_already_stored(mgr, monkeypatch):
    users = {"Ann": {"total_messages": 1}}
    mgr._spill_raw_user_map("chat.user_stats", users)
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._spill_raw_user_map("chat.user_stats", dict(users))
    get.assert_not_called()


def test_spill_skips_non_dict_payloads(mgr):
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 2}, "Bad": 5, "Worse": None})
    assert lifetime_rows(mgr) == [("ann", "chat.user_stats", "Ann", '{"total_messages": 2}')]


def test_spill_does_nothing_for_empty_maps_or_off_database(mgr, monkeypatch):
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._spill_raw_user_map("chat.user_stats", {})
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {}})
    get.assert_not_called()


def test_spill_without_connection_remembers_nothing(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 1}})
    assert mgr._spilled_user_digests == {}


def test_spill_failure_is_logged_and_retried_next_time(mgr, monkeypatch):
    use_conn(monkeypatch, mgr, BrokenConn())
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 1}})
    assert mgr._spilled_user_digests == {}
    monkeypatch.undo()
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 1}})
    assert len(lifetime_rows(mgr)) == 1


def test_persist_user_maps_moves_every_per_user_map_out_of_the_totals(mgr):
    data = {
        "alerts": {"bit_alerts_played": 1, "user_stats": {"A1": {"raids": 1}}},
        "connectors": {"user_stats": {"C1": {"total_triggers": 2}}, "user_connector_stats": {"C2": {"total_triggers": 3}}},
        "chatbot": {"user_stats": {"B1": {}}, "user_command_stats": {"B2": {}}, "user_event_stats": {"B3": {}}},
        "quotes": {"user_stats": {"Q1": {"total_quotes_redeemed": 1}}},
        "chat": {"user_stats": {"Ch": {"total_messages": 9}}},
        "giveaways": {"giveaways_completed": 2, "user_wins": {"Winner": 3}},
        "templates": "not a dict block",
    }
    mgr._persist_user_maps(data)
    assert "user_stats" not in data["alerts"] and data["alerts"]["bit_alerts_played"] == 1
    assert data["connectors"] == {} and data["chatbot"] == {} and data["quotes"] == {} and data["chat"] == {}
    assert data["giveaways"] == {"giveaways_completed": 2}
    cats = {(r[1], r[2]) for r in lifetime_rows(mgr)}
    assert cats == {
        ("alerts.user_stats", "A1"), ("connectors.user_stats", "C1"), ("connectors.user_connector_stats", "C2"),
        ("chatbot.user_stats", "B1"), ("chatbot.user_command_stats", "B2"), ("chatbot.user_event_stats", "B3"),
        ("quotes.user_stats", "Q1"), ("chat.user_stats", "Ch"), ("giveaways.user_wins", "Winner"),
    }
    assert lifetime_rows(mgr, "giveaways.user_wins")[0][3] == '{"wins": 3}'


def test_persist_user_maps_ignores_missing_and_odd_blocks(mgr):
    data = {"alerts": "x", "chat": {"user_stats": "not a map"}, "giveaways": {"user_wins": []}}
    mgr._persist_user_maps(data)
    assert data == {"alerts": "x", "chat": {"user_stats": "not a map"}, "giveaways": {"user_wins": []}}
    assert lifetime_rows(mgr) == []


def test_take_user_map(mgr):
    mgr._take_user_map("not a dict", "user_stats", "chat.user_stats")
    empty = {"user_stats": {}}
    mgr._take_user_map(empty, "user_stats", "chat.user_stats")
    assert empty == {}
    full = {"user_stats": {"Ann": {"total_messages": 1}}, "other": 1}
    mgr._take_user_map(full, "user_stats", "chat.user_stats")
    assert full == {"other": 1}
    assert [r[2] for r in lifetime_rows(mgr)] == ["Ann"]


def test_load_user_payload_variants(mgr, monkeypatch):
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 4}})
    assert mgr._load_user_payload("chat.user_stats", "ANN") == {"username": "Ann", "data": {"total_messages": 4}}
    assert mgr._load_user_payload("chat.user_stats", "nobody") is None
    assert mgr._load_user_payload("chat.user_stats", "") is None
    run_sql(mgr, "INSERT INTO user_lifetime VALUES ('lst', 'chat.user_stats', 'Lst', '[1, 2]')")
    run_sql(mgr, "INSERT INTO user_lifetime VALUES ('bad', 'chat.user_stats', 'Bad', '{not json')")
    assert mgr._load_user_payload("chat.user_stats", "lst") is None
    assert mgr._load_user_payload("chat.user_stats", "bad") is None
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    assert mgr._load_user_payload("chat.user_stats", "Ann") is None
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    assert mgr._load_user_payload("chat.user_stats", "Ann") is None


def test_remember_user_loads_one_stored_user(mgr):
    mapping = {}
    mgr._spill_raw_user_map("chat.user_stats", {"Ann": {"total_messages": 4, "twitch_messages_received": 4}})
    mgr._remember_user(mapping, "ann", sm.UserChatStatistics, "chat.user_stats")
    assert list(mapping) == ["Ann"] and mapping["Ann"].total_messages == 4
    existing = mapping["Ann"]
    mgr._remember_user(mapping, "ANN", sm.UserChatStatistics, "chat.user_stats")
    assert mapping["Ann"] is existing
    mgr._remember_user(mapping, "ghost", sm.UserChatStatistics, "chat.user_stats")
    assert list(mapping) == ["Ann"]


def test_remember_user_skips_rows_that_do_not_fit_the_dataclass(mgr):
    mgr._spill_raw_user_map("chat.user_stats", {"Odd": {"no_such_field": 1}})
    mapping = {}
    mgr._remember_user(mapping, "odd", sm.UserChatStatistics, "chat.user_stats")
    assert mapping == {}


def test_user_record_existing_stored_new_and_bad_rows(mgr):
    mapping = {"Present": sm.UserChatStatistics(total_messages=1)}
    assert mgr._user_record(mapping, "present", sm.UserChatStatistics, "chat.user_stats") is mapping["Present"]
    mgr._spill_raw_user_map("chat.user_stats", {"Stored": {"total_messages": 7}, "Odd": {"nope": 1}})
    stored = mgr._user_record(mapping, "stored", sm.UserChatStatistics, "chat.user_stats")
    assert stored.total_messages == 7 and mapping["Stored"] is stored
    odd = mgr._user_record(mapping, "odd", sm.UserChatStatistics, "chat.user_stats")
    assert odd.total_messages == 0 and mapping["Odd"] is odd
    new = mgr._user_record(mapping, "Brand", sm.UserChatStatistics, "chat.user_stats")
    assert mapping["Brand"] is new and new.total_messages == 0


def test_giveaway_wins_become_resident_once(mgr, monkeypatch):
    mgr._spill_raw_user_map("giveaways.user_wins", {"Win": {"wins": 3}, "Kept": {"wins": 9}, "Zero": {}})
    run_sql(mgr, "INSERT INTO user_lifetime VALUES ('bad', 'giveaways.user_wins', 'Bad', '{\"wins\": \"x\"}')")
    run_sql(mgr, "INSERT INTO user_lifetime VALUES ('typ', 'giveaways.user_wins', 'Typ', '{\"wins\": [1]}')")
    mgr.data.giveaways.user_wins["Kept"] = 1
    mgr._ensure_giveaway_wins_resident()
    assert mgr.data.giveaways.user_wins == {"Kept": 1, "Win": 3, "Zero": 0}
    assert "giveaways.user_wins" in mgr._lifetime_resident
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._ensure_giveaway_wins_resident()
    get.assert_not_called()


def test_giveaway_wins_residency_with_no_db_or_errors(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    mgr._ensure_giveaway_wins_resident()
    assert "giveaways.user_wins" not in mgr._lifetime_resident
    use_conn(monkeypatch, mgr, BrokenConn())
    mgr._ensure_giveaway_wins_resident()
    assert "giveaways.user_wins" not in mgr._lifetime_resident
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    mgr._ensure_giveaway_wins_resident()
    assert "giveaways.user_wins" in mgr._lifetime_resident


def test_category_becomes_resident_with_tolerant_row_parsing(mgr, monkeypatch):
    mgr._spill_raw_user_map("chat.user_stats", {"Good": {"total_messages": 5}, "Odd": {"unknown": 1}, "Here": {"total_messages": 1}})
    run_sql(mgr, "INSERT INTO user_lifetime VALUES ('lst', 'chat.user_stats', 'Lst', '[1]')")
    mapping = {"HERE": sm.UserChatStatistics(total_messages=99)}
    mgr._ensure_category_resident(mapping, sm.UserChatStatistics, "chat.user_stats")
    assert set(mapping) == {"HERE", "Good", "Odd", "Lst"}
    assert mapping["Good"].total_messages == 5
    assert mapping["Odd"].total_messages == 0 and mapping["Lst"].total_messages == 0
    assert mapping["HERE"].total_messages == 99
    get = Mock()
    monkeypatch.setattr(mgr, "_get_stats_db_connection", get)
    mgr._ensure_category_resident(mapping, sm.UserChatStatistics, "chat.user_stats")
    get.assert_not_called()


def test_category_residency_with_no_db_or_errors(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    mgr._ensure_category_resident({}, sm.UserChatStatistics, "chat.user_stats")
    assert "chat.user_stats" not in mgr._lifetime_resident
    use_conn(monkeypatch, mgr, BrokenConn())
    mgr._ensure_category_resident({}, sm.UserChatStatistics, "chat.user_stats")
    assert "chat.user_stats" not in mgr._lifetime_resident
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    mgr._ensure_category_resident({}, sm.UserChatStatistics, "chat.user_stats")
    assert "chat.user_stats" in mgr._lifetime_resident


# --------------------------------------------------------------------------- lifetime totals


def test_lifetime_totals_round_trip(mgr):
    assert mgr._load_lifetime_totals_from_stats_db() is None
    mgr._save_lifetime_totals_to_stats_db({"chat": {"total_messages": 3}, "polls": {"polls_completed": 1}})
    mgr._save_lifetime_totals_to_stats_db({"chat": {"total_messages": 4}})
    assert mgr._load_lifetime_totals_from_stats_db() == {
        "data": {"chat": {"total_messages": 4}, "polls": {"polls_completed": 1}},
        "version": "1.0",
    }


def test_lifetime_totals_load_errors_and_missing_connection(mgr, monkeypatch):
    run_sql(mgr, "INSERT INTO lifetime_totals (category, data_json) VALUES ('chat', '{broken')")
    assert mgr._load_lifetime_totals_from_stats_db() is None
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    assert mgr._load_lifetime_totals_from_stats_db() is None
    mgr._save_lifetime_totals_to_stats_db({"chat": {}})  # quietly nothing


def test_lifetime_totals_save_failure_notifies(mgr, env, monkeypatch):
    use_conn(monkeypatch, mgr, BrokenConn())
    mgr._save_lifetime_totals_to_stats_db({"chat": {}})
    assert mgr.last_save_ok is False
    note = env.notes[-1]
    assert note["dedupe_key"] == "stats:save_failed" and note["title"] == "Couldn't save statistics"


# --------------------------------------------------------------------------- migration from database_manager


def test_migrate_from_database_manager_copies_old_totals_once(mgr, env):
    env.old["value"] = {"data": {"chat": {"total_messages": 8}, "alerts": {"raids": 2}}, "version": "1.0"}
    assert mgr._migrate_from_database_manager() is True
    rows = dict(q(mgr, "SELECT category, data_json FROM lifetime_totals"))
    assert json.loads(rows["chat"]) == {"total_messages": 8}
    assert json.loads(rows["alerts"]) == {"raids": 2}
    assert q(mgr, "SELECT migration_name FROM migrations WHERE migration_name='initial_migration'")
    assert mgr._migrate_from_database_manager() is False  # already done


@pytest.mark.parametrize("old", [None, {}, {"version": "1.0"}])
def test_migrate_without_old_data_marks_done(mgr, env, old):
    env.old["value"] = old
    assert mgr._migrate_from_database_manager() is False
    assert q(mgr, "SELECT COUNT(*) FROM migrations WHERE migration_name='initial_migration'")[0][0] == 1
    assert q(mgr, "SELECT COUNT(*) FROM lifetime_totals")[0][0] == 0


def test_migrate_off_database_no_connection_and_errors(mgr, env, monkeypatch):
    monkeypatch.setattr(mgr, "_get_stats_db_connection", lambda **k: None)
    assert mgr._migrate_from_database_manager() is False
    use_conn(monkeypatch, mgr, BrokenConn())
    assert mgr._migrate_from_database_manager() is False
    assert env.notes[-1]["dedupe_key"] == "stats:migrate"
    monkeypatch.setattr(mgr, "_stats_db_initialized", False)
    assert mgr._migrate_from_database_manager() is False


def test_new_manager_migrates_old_statistics_on_first_start(env):
    env.old["value"] = {"data": {"chat": {"twitch_messages_received": 5, "total_messages": 5}}}
    m = env.make()
    assert m.data.chat.total_messages == 5
    assert json.loads(dict(q(m, "SELECT category, data_json FROM lifetime_totals"))["chat"])["total_messages"] == 5

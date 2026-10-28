# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_subscriber_registry.py (ever-subscribed registry, SQLite + Helix sync)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import sqlite3  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import path_utils, shutdown, twitch, twitch_subscriber_registry as tsr  # noqa: E402


@pytest.fixture
def reg(tmp_path):
    r = tsr.SubscriberRegistry(str(tmp_path / "sub" / "reg.db"))
    yield r
    r.flush()


def _rows(reg, sql):
    conn = sqlite3.connect(reg._db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_default_db_path_uses_data_path(monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel: str(tmp_path / rel))
    assert tsr.SubscriberRegistry._default_db_path() == str(tmp_path / "data" / "twitch_subscriber_registry.db")


def test_constructor_with_default_path(monkeypatch, tmp_path):
    monkeypatch.setattr(tsr.SubscriberRegistry, "_default_db_path", staticmethod(lambda: str(tmp_path / "x.db")))
    r = tsr.SubscriberRegistry()
    assert r._db_path == str(tmp_path / "x.db")
    assert (tmp_path / "x.db").exists()


def test_connect_without_parent_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = tsr.SubscriberRegistry("plain.db")
    assert (tmp_path / "plain.db").exists()
    r.flush()


def test_ensure_db_and_load_errors_are_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(tsr.SubscriberRegistry, "_connect", MagicMock(side_effect=sqlite3.OperationalError("nope")))
    with caplog.at_level("ERROR", logger="modules.twitch_subscriber_registry"):
        r = tsr.SubscriberRegistry(str(tmp_path / "r.db"))
    assert "Failed to initialize subscriber registry DB" in caplog.text
    assert "Failed to load subscriber registry" in caplog.text
    assert r.is_known("1") is False


def test_load_from_db_reads_both_tables(tmp_path):
    path = str(tmp_path / "r.db")
    r = tsr.SubscriberRegistry(path)
    r.record("10", "Alice")
    r.record(None, "LoginOnly")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO known_subscribers VALUES ('', NULL, 1, 1, '', NULL)")
    conn.execute("INSERT INTO known_subscriber_logins VALUES ('', 1, 1, '')")
    conn.commit()
    conn.close()
    r.flush()
    r2 = tsr.SubscriberRegistry(path)
    assert r2.is_known("10") and r2.is_known(user_login="alice") and r2.is_known(user_login="loginonly")
    assert "" not in r2._user_ids and "" not in r2._user_logins


def test_session_ready_flags(reg):
    assert reg.is_session_ready() is False and reg.is_helix_forbidden() is False
    reg.set_session_ready(1)
    assert reg.is_session_ready() is True
    reg._helix_snapshot_forbidden = True
    reg.reset_session_ready()
    assert reg.is_session_ready() is False and reg.is_helix_forbidden() is False


def test_normalizers():
    assert tsr.SubscriberRegistry._normalize_login(None) is None
    assert tsr.SubscriberRegistry._normalize_login("  ") is None
    assert tsr.SubscriberRegistry._normalize_login(" Bob ") == "bob"
    assert tsr.SubscriberRegistry._normalize_id(None) is None
    assert tsr.SubscriberRegistry._normalize_id(" ") is None
    assert tsr.SubscriberRegistry._normalize_id(12) == "12"


def test_record_ignores_empty_and_persists(reg):
    reg.record(None, None)
    assert _rows(reg, "SELECT count(*) FROM known_subscribers") == [(0,)]
    reg.record("1", "Bob", source="x", cumulative_months=3)
    assert reg.is_known("1") and reg.is_known(user_login="BOB")
    assert _rows(reg, "SELECT user_id, user_login, source, cumulative_months FROM known_subscribers") == [
        ("1", "bob", "x", 3)]


def test_record_with_id_removes_login_only_row(reg):
    reg.record(None, "carol", source="seed")
    assert _rows(reg, "SELECT user_login FROM known_subscriber_logins") == [("carol",)]
    reg.record("7", "carol")
    assert _rows(reg, "SELECT user_login FROM known_subscriber_logins") == []
    # upsert keeps old source when new one is empty and keeps months
    reg.record("7", None, source="", cumulative_months=None)
    assert _rows(reg, "SELECT user_login FROM known_subscribers WHERE user_id='7'") == [("carol",)]


def test_record_many(reg):
    reg.record_many(None)
    reg.record_many([(None, None, "s", None)])
    assert _rows(reg, "SELECT count(*) FROM known_subscribers") == [(0,)]
    reg.record_many([("1", "a", None, 2), (None, "b", "seed", None), ("3", None, "s", None)])
    assert reg.is_known("1") and reg.is_known(user_login="b") and reg.is_known("3")
    assert sorted(_rows(reg, "SELECT user_id FROM known_subscribers")) == [("1",), ("3",)]
    assert _rows(reg, "SELECT user_login, source FROM known_subscriber_logins") == [("b", "seed")]


def test_submit_write_inside_loop_wakes_writer(reg, monkeypatch):
    woken = MagicMock()
    monkeypatch.setattr(reg, "_wake_writer", woken)

    async def go():
        reg.record("5", "e")

    asyncio.run(go())
    woken.assert_called_once()
    assert len(reg._pending_writes) == 1
    reg._drain_writes()
    assert reg._pending_writes == type(reg._pending_writes)()
    assert _rows(reg, "SELECT user_id FROM known_subscribers") == [("5",)]


def test_wake_writer_starts_thread_once(reg, monkeypatch):
    created = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target, self.name, self.alive = target, name, False
            created.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(tsr.threading, "Thread", FakeThread)
    reg._wake_writer()
    reg._wake_writer()
    assert len(created) == 1 and created[0].name == "SubscriberRegistryWriter"
    assert reg._writer_wake.is_set()


def test_writer_loop_drains_then_waits(reg, monkeypatch):
    calls = []

    class Wake:
        def __init__(self):
            self.n = 0

        def wait(self):
            self.n += 1
            if self.n > 1:
                raise SystemExit  # stop the infinite loop after one pass

        def clear(self):
            calls.append("clear")

    reg._writer_wake = Wake()
    monkeypatch.setattr(reg, "_drain_writes", lambda: calls.append("drain"))
    with pytest.raises(SystemExit):
        reg._writer_loop()
    assert calls == ["clear", "drain"]


def test_flush_closes_connection_and_ignores_close_errors(reg):
    reg.record("1", "a")
    assert reg._write_conn is not None
    bad = MagicMock()
    bad.close.side_effect = RuntimeError("x")
    reg._write_conn = bad
    reg.flush()
    assert reg._write_conn is None
    reg.flush()  # nothing open: no error


@pytest.mark.parametrize("kind", ["one", "many"])
def test_execute_write_error_drops_connection(reg, caplog, kind):
    conn = MagicMock()
    conn.cursor.side_effect = sqlite3.OperationalError("locked")
    conn.close.side_effect = RuntimeError("close")
    reg._write_conn = conn
    rows = ("1", "a", "", None) if kind == "one" else [("1", "a", "", None)]
    with caplog.at_level("ERROR", logger="modules.twitch_subscriber_registry"):
        reg._execute_write((kind, rows, time.time()))
    assert reg._write_conn is None
    assert ("batch" in caplog.text) if kind == "many" else ("id=1 login=a" in caplog.text)


def test_execute_write_error_without_connection(reg, monkeypatch):
    monkeypatch.setattr(reg, "_connect", MagicMock(side_effect=sqlite3.OperationalError("x")))
    reg._execute_write(("one", ("1", None, "", None), 0.0))
    assert reg._write_conn is None


def test_new_sub_alert_dedup_and_expiry(reg, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tsr.time, "time", lambda: now[0])
    reg.mark_new_sub_alerted(None)
    assert reg.was_new_sub_alerted_recently(None) is False
    reg.mark_new_sub_alerted("old")
    now[0] += 100
    reg.mark_new_sub_alerted("new")  # evicts "old"
    assert "old" not in reg._recent_new_sub_alerts
    assert reg.was_new_sub_alerted_recently("new") is True
    assert reg.was_new_sub_alerted_recently("unknown") is False
    now[0] += 61
    assert reg.was_new_sub_alerted_recently("new") is False
    assert "new" not in reg._recent_new_sub_alerts


def test_pending_tasks(reg):
    def task(done):
        t = MagicMock()
        t.done.return_value = done
        return t

    reg.store_pending_task("", task(False))
    assert reg._pending_tasks == {}
    first = task(False)
    reg.store_pending_task("1", first)
    assert reg.has_pending("1") is True
    second = task(False)
    reg.store_pending_task("1", second)
    first.cancel.assert_called_once()
    done_old = task(True)
    reg.store_pending_task("2", done_old)
    reg.store_pending_task("2", task(False))
    done_old.cancel.assert_not_called()
    assert reg.cancel_pending("1") is True
    second.cancel.assert_called_once()
    assert reg.cancel_pending("1") is False
    assert reg.cancel_pending(None) is False
    assert reg.has_pending(None) is False
    reg.clear_pending(None)
    reg.clear_pending("2")
    assert reg.has_pending("2") is False


def _stats_db(path, with_table=True, rows=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if with_table:
        conn.execute("CREATE TABLE user_events (username TEXT, event_type TEXT)")
        conn.executemany("INSERT INTO user_events VALUES (?, ?)", rows)
    else:
        conn.execute("CREATE TABLE other (x)")
    conn.commit()
    conn.close()


def test_seed_from_statistics(reg, monkeypatch, tmp_path):
    root = tmp_path / "stats"
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel: str(root / rel))
    assert reg.seed_from_statistics() == 0  # no file anywhere
    _stats_db(root / "statistics.db", rows=[("Legacy", "sub")])
    assert reg.seed_from_statistics() == 1  # legacy location
    _stats_db(root / "data" / "statistics.db", rows=[
        ("Alice", "sub"), ("bob", "resub"), ("carl", "follow"), ("  ", "sub"), ("legacy", "sub")])
    reg.record(None, "bob")
    assert reg.seed_from_statistics() == 1  # only alice is new
    assert reg.is_known(user_login="alice") and not reg.is_known(user_login="carl")


def test_seed_from_statistics_without_table(reg, monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel: str(tmp_path / rel))
    _stats_db(tmp_path / "data" / "statistics.db", with_table=False)
    assert reg.seed_from_statistics() == 0


def test_seed_from_statistics_error(reg, monkeypatch):
    monkeypatch.setattr(path_utils, "get_data_path", MagicMock(side_effect=RuntimeError("x")))
    assert reg.seed_from_statistics() == 0


def test_seed_skips_blank_after_normalize(reg, monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_data_path", lambda rel: str(tmp_path / rel))
    _stats_db(tmp_path / "data" / "statistics.db", rows=[("\t", "sub")])
    assert reg.seed_from_statistics() == 0


# ------------------------------------------------------------------ Helix


def _api(responses=None, error=None):
    api = SimpleNamespace(user_id="b1")
    api.generic_api_call = AsyncMock(side_effect=error) if error else AsyncMock(side_effect=list(responses))
    return api


def test_sync_from_helix_sets_ready_on_success(reg, monkeypatch):
    monkeypatch.setattr(tsr.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(twitch, "twitch_api", _api([
        {"data": [{"user_id": "1", "user_login": "a"}, {"user_id": None, "user_name": "B"}],
         "pagination": {"cursor": "c"}},
        {"data": [{"user_id": "2", "user_login": "c"}]},
    ]))
    assert asyncio.run(reg.sync_from_helix()) is True
    assert reg.is_session_ready() is True
    assert reg.is_known("1") and reg.is_known(user_login="b") and reg.is_known("2")
    assert twitch.twitch_api.generic_api_call.await_args_list[1].kwargs["params"]["after"] == "c"
    assert not reg._sync_lock.locked()


def test_sync_from_helix_waits_for_lock_and_tolerates_release_error(reg, monkeypatch):
    reg._sync_lock.acquire()
    real_sleep = asyncio.sleep

    async def fake_sleep(_d):
        reg._sync_lock.release()
        await real_sleep(0)

    monkeypatch.setattr(tsr.asyncio, "sleep", fake_sleep)

    async def fetch():
        reg._sync_lock.release()  # released early -> RuntimeError in finally is swallowed
        return False

    monkeypatch.setattr(reg, "_fetch_helix_subscribers", fetch)
    assert asyncio.run(reg.sync_from_helix()) is False
    assert reg.is_session_ready() is False


def test_fetch_helix_without_api(reg, monkeypatch):
    monkeypatch.setattr(twitch, "twitch_api", None)
    assert asyncio.run(reg._fetch_helix_subscribers()) is False
    monkeypatch.setattr(twitch, "twitch_api", SimpleNamespace(user_id=None))
    assert asyncio.run(reg._fetch_helix_subscribers()) is False


def test_fetch_helix_empty_response(reg, monkeypatch):
    monkeypatch.setattr(twitch, "twitch_api", _api([None]))
    assert asyncio.run(reg._fetch_helix_subscribers()) is True


@pytest.mark.parametrize(
    "error, forbidden",
    [
        (twitch.TwitchSessionNotReadyError("x"), False),
        (twitch.TwitchPermissionError("403"), True),
        (RuntimeError("Session not ready"), False),
        (RuntimeError("must be partner or affiliate"), True),
        (RuntimeError("403 Forbidden"), True),
        (RuntimeError("boom"), False),
    ],
)
def test_fetch_helix_errors(reg, monkeypatch, error, forbidden):
    monkeypatch.setattr(twitch, "twitch_api", _api(error=error))
    assert asyncio.run(reg._fetch_helix_subscribers()) is False
    assert reg.is_helix_forbidden() is forbidden


def test_register_registry_flush(monkeypatch):
    calls = []
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: calls.append((name, fn)))
    tsr._register_registry_flush()
    assert calls == [("subscriber_registry", tsr._registry.flush)]


def test_register_registry_flush_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.shutdown", None)
    tsr._register_registry_flush()  # ImportError path, no raise


def test_get_subscriber_registry_singleton():
    assert tsr.get_subscriber_registry() is tsr._registry

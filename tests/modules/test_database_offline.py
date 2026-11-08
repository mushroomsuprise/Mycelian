# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Database writes during an internet outage: kept in memory, read back, written once the
connection returns, with one notice each way."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests" / "modules"), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from modules import database_manager as dbm  # noqa: E402
from modules import database_offline as off  # noqa: E402
from modules.database_manager import DatabaseConfig  # noqa: E402


class Backend:
    """A remote database that can drop off the network."""

    def __init__(self):
        self.data = {}
        self.up = True
        self.writes = []

    def initialize(self):
        return True

    def get_data(self, path, request_etag=False):
        if not self.up:
            raise ConnectionError("offline")
        value = self.data.get(path, {})
        return {"data": value, "etag": None} if request_etag else value

    def _write(self, op, path, value=None):
        if not self.up:
            return False  # like FirebaseDatabase: the error is logged, False comes back
        self.writes.append((op, path))
        if op == "set":
            self.data[path] = value
        elif op == "update":
            self.data.setdefault(path, {}).update(value)
        else:
            self.data.pop(path, None)
        return True

    def set_data(self, path, data):
        return self._write("set", path, data)

    def update_data(self, path, data):
        return self._write("update", path, data)

    def delete_data(self, path):
        return self._write("delete", path)

    def test_connection(self):
        return self.up

    def get_connection_status(self):
        return {"connected": self.up}


@pytest.fixture
def env(monkeypatch):
    notes = []
    monkeypatch.setattr("modules.notification_engine.notify", lambda msg, **kw: notes.append({"message": msg, **kw}))
    monkeypatch.setattr(dbm, "notify", lambda *a, **k: notes.append({"message": a[0] if a else "", **k}))
    monkeypatch.setattr(dbm, "notify_critical", lambda *a, **k: notes.append({"message": a[0] if a else "", **k}))
    internet = {"up": True}
    monkeypatch.setattr(off, "_internet_up", lambda: internet["up"])
    backend = Backend()
    factory = type("BackendFactory", (), {"__new__": lambda cls, cfg: backend})
    for name in ("SQLDatabase", "FirebaseDatabase", "MongoDatabase"):
        monkeypatch.setattr(dbm, name, factory)
    m = dbm.DatabaseManager()
    m._config = DatabaseConfig(database_type="firebase")
    assert m.initialize()
    m._offline_writes._ensure_thread = lambda: None  # the tests flush by hand
    dbm.clear_last_write_problem()
    yield m, backend, internet, notes
    dbm.clear_last_write_problem()


def test_writes_during_an_outage_are_kept_and_flushed(env):
    m, backend, internet, notes = env
    backend.up, internet["up"] = False, False
    assert m.set_data("Stats/A", {"n": 1}) is True  # kept, not lost
    assert m.offline_status() == {"offline": True, "pending": 1}
    offline = [n for n in notes if n.get("dedupe_key") == "db:offline"]
    assert len(offline) == 1 and offline[0]["type"] == "warning" and "Firebase" in offline[0]["title"]
    assert not any(n.get("dedupe_key", "").startswith("db:write") for n in notes)  # no "couldn't save" toasts
    # More writes go straight to memory (no 30 s waits on the network)
    assert m.update_data("Stats/A", {"m": 2}) is True
    assert m.set_data("Other", {"x": 1}) is True
    assert m.delete_data("Gone") is True
    assert backend.writes == []
    assert m.get_data("Stats/A") == {"n": 1, "m": 2}  # reads see the waiting change
    assert m.get_data_checked("Stats/A") == ({"n": 1, "m": 2}, True)
    assert m.get_data("Gone") == {}
    # Still offline: nothing is written
    assert m._offline_writes.try_flush() is False and m.offline_status()["pending"] == 3
    backend.up, internet["up"] = True, True
    assert m._offline_writes.try_flush() is True
    assert backend.writes == [("set", "Stats/A"), ("set", "Other"), ("delete", "Gone")]
    assert backend.data["Stats/A"] == {"n": 1, "m": 2}
    assert m.offline_status() == {"offline": False, "pending": 0}
    back = [n for n in notes if n.get("type") == "success" and n.get("dedupe_key") == "db:offline"]
    assert len(back) == 1 and "Saved 3 changes" in back[0]["message"]
    assert m.set_data("Stats/A", {"n": 5}) is True and backend.data["Stats/A"] == {"n": 5}  # online again


def test_a_refused_write_is_not_mistaken_for_an_outage(env, monkeypatch):
    m, backend, internet, notes = env
    backend.set_data = lambda path, data: False  # the database answers, but says no (rules)
    assert m.set_data("Locked", {"x": 1}) is False
    assert m.offline_status()["offline"] is False
    assert any(n.get("dedupe_key") == "db:write_failed" for n in notes)


def test_sqlite_never_queues(env):
    m, backend, internet, notes = env
    m._config = DatabaseConfig(database_type="sql")
    backend.up = False
    internet["up"] = False
    assert m.set_data("A", {"x": 1}) is False
    assert m.offline_status()["pending"] == 0


def test_queue_merges_per_path():
    q = off.OfflineWrites(lambda *a: True, lambda: True, lambda: "Firebase")
    q.queue("update", "A", {"a": 1})
    q.queue("update", "A", {"b": 2})
    assert q._pending["A"] == ("update", {"a": 1, "b": 2})
    q.queue("set", "A", {"c": 3})
    q.queue("update", "A", {"d": 4})
    assert q._pending["A"] == ("set", {"c": 3, "d": 4})
    q.queue("delete", "A")
    assert q.overlay("A", {"old": 1}) == {}
    q.queue("update", "A", {"e": 5})
    assert q._pending["A"] == ("update", {"e": 5})
    assert q.overlay("B", {"x": 1}) == {"x": 1}  # nothing waiting


def test_queued_data_is_a_copy():
    q = off.OfflineWrites(lambda *a: True, lambda: True, lambda: "Firebase")
    data = {"list": [1]}
    q.queue("set", "A", data)
    data["list"].append(2)
    assert q.overlay("A", {}) == {"list": [1]}


def test_flush_stops_at_the_first_failure_and_keeps_the_rest(monkeypatch):
    monkeypatch.setattr(off, "_internet_up", lambda: True)
    results = iter([True, False])
    q = off.OfflineWrites(lambda *a: next(results), lambda: True, lambda: "Firebase")
    q._notify_back = lambda n: None
    q.queue("set", "A", {})
    q.queue("set", "B", {})
    q.queue("set", "C", {})
    assert q.try_flush() is False
    assert list(q._pending) == ["B", "C"]


def test_shutdown_flush_gives_up_quietly(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(off, "_internet_up", lambda: False)
    monkeypatch.setattr(off, "SHUTDOWN_FLUSH_SEC", 0.2)
    q = off.OfflineWrites(lambda *a: True, lambda: True, lambda: "Firebase")
    q.queue("set", "A", {})
    with caplog.at_level(logging.WARNING, logger=off.__name__):
        q.flush_on_shutdown()
    assert "1 database change(s) not written" in caplog.text
    empty = off.OfflineWrites(lambda *a: True, lambda: True, lambda: "Firebase")
    empty.flush_on_shutdown()  # nothing waiting: returns at once


@pytest.mark.parametrize("status,reason,outage", [
    ("timeout", "", True), ("failed", "HTTPSConnectionPool: Max retries exceeded", True),
    ("failed", "ServerSelectionTimeoutError", True), ("failed", "Permission denied", False), ("failed", "", False),
])
def test_looks_like_outage(status, reason, outage):
    assert off.looks_like_outage(status, reason) is outage

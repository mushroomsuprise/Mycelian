# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the modules/alertutils.py tests (not a test file itself).

``install(monkeypatch)`` swaps alertutils' database_manager for an in-memory fake,
runs every thread the manager would start synchronously (the config writer drains
its queue and returns), restores the module-level alert collections afterwards and
returns ``(fake_db, manager)``.
"""

from __future__ import annotations

import contextlib
import copy
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alertutils  # noqa: E402

COLLECTION_NAMES = (
    "BitAlerts", "BitRangeAlerts", "SubAlerts", "SubRangeAlerts", "GiftsubAlerts", "GiftsubRangeAlerts",
    "DonationAlerts", "DonationRangeAlerts", "RaidAlerts", "RaidRangeAlerts", "PointAlerts",
    "FollowAlerts", "StreakAlerts", "StreakRangeAlerts",
)


class FakeBackend:
    """Stands in for database_manager.database_manager (the DatabaseManager instance)."""

    def __init__(self, db: "FakeDB"):
        self.db = db
        self.listeners = []
        self.database_type = "sql"
        self.config_error = False
        self.storage_result = None  # fetch_alert_storage return (None = backend not open)
        self.storage_calls = []
        self.meta = None
        self.meta_error = False
        self._initialized = False
        self._database = None
        self.delete_paths_calls = []
        self.delete_paths_result = True
        self.delete_paths_error = False

    def add_write_listener(self, cb):
        self.listeners.append(cb)

    def get_config(self):
        if self.config_error:
            raise RuntimeError("no config")
        return SimpleNamespace(database_type=self.database_type)

    def fetch_alert_storage(self, max_alerts=None):
        self.storage_calls.append(max_alerts)
        if isinstance(self.storage_result, Exception):
            raise self.storage_result
        return copy.deepcopy(self.storage_result)

    def list_alert_storage_meta(self):
        if self.meta_error:
            raise RuntimeError("meta failed")
        return self.meta

    def delete_paths(self, paths):
        self.delete_paths_calls.append(list(paths))
        if self.delete_paths_error:
            raise RuntimeError("batch delete failed")
        if self.delete_paths_result:
            for p in paths:
                self.db.store.pop(p, None)
        return self.delete_paths_result


class FakeDB:
    """Stands in for the modules.database_manager module."""

    def __init__(self):
        self.store = {}
        self.read_fail = set()
        self.read_error = set()
        self.write_fail = set()
        self.write_error = set()
        self.delete_error = set()
        self.writes = []
        self.deletes = []
        self.quiet_used = 0
        self.problem = None
        self.problem_error = False
        self.startup_failed = set()
        self.hub = False
        self.limited = None
        self.limited_calls = []
        self.multi_error = False
        self.database_manager = FakeBackend(self)

    # reads
    def get_data_checked(self, path):
        if path in self.read_error:
            raise RuntimeError(f"read error {path}")
        if path in self.read_fail:
            return {}, False
        return copy.deepcopy(self.store.get(path)), True

    def get_data(self, path):
        if path in self.read_error:
            raise RuntimeError(f"read error {path}")
        return copy.deepcopy(self.store.get(path))

    async def get_multiple_data_async(self, paths):
        if self.multi_error:
            raise RuntimeError("multi read failed")
        return {p: copy.deepcopy(self.store.get(p)) for p in paths}

    def get_alert_storage_limited(self, max_alerts):
        self.limited_calls.append(max_alerts)
        return copy.deepcopy(self.limited)

    # writes
    def set_data(self, path, data):
        self.writes.append((path, copy.deepcopy(data)))
        if path in self.write_error:
            raise RuntimeError(f"write error {path}")
        if path in self.write_fail:
            return False
        self.store[path] = copy.deepcopy(data)
        return True

    def delete_data(self, path):
        self.deletes.append(path)
        if path in self.delete_error:
            raise RuntimeError(f"delete error {path}")
        self.store.pop(path, None)
        return True

    @contextlib.contextmanager
    def quiet_notifications(self):
        self.quiet_used += 1
        yield

    def last_write_problem(self):
        if self.problem_error:
            raise RuntimeError("no problem info")
        return self.problem

    def startup_read_failed(self, path):
        return path in self.startup_failed

    def _caller_is_overlay_hub(self):
        return self.hub


class SyncThread:
    """threading.Thread stand-in that runs the target inline when started."""

    started = []

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self.target = target
        self.name = name
        self.args = args
        self.kwargs = kwargs or {}
        self.ran = False

    def start(self):
        SyncThread.started.append(self.name)
        self.ran = True
        if self.name == "AlertConfigWriter":
            mgr = self.target.__self__
            mgr._write_queue.put(None)  # sentinel: unpacking None ends the endless loop
            try:
                self.target()
            except TypeError:
                pass
            return
        self.target(*self.args, **self.kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


def threading_namespace(thread_cls=SyncThread):
    return SimpleNamespace(
        Thread=thread_cls,
        Event=threading.Event,
        Lock=threading.Lock,
        RLock=threading.RLock,
        Condition=threading.Condition,
        current_thread=threading.current_thread,
    )


def install(monkeypatch, *, initialized=True, sync_threads=True):
    """Install the fakes; return (fake_db, manager). The manager is also alertutils.alert_state_manager."""
    from modules import notification_engine, shutdown

    db = FakeDB()
    monkeypatch.setattr(alertutils, "database_manager", db)
    if sync_threads:
        SyncThread.started = []
        monkeypatch.setattr(alertutils, "threading", threading_namespace())
    flushes = []
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: flushes.append(name))
    polls = []
    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll", lambda: polls.append(1),
                        raising=False)
    db.flushes = flushes
    db.polls = polls
    # module-level collections are global: restore their contents afterwards
    for name in COLLECTION_NAMES:
        coll = getattr(alertutils, name)
        saved = dict(coll)

        def _restore(coll=coll, saved=saved):
            coll.clear()
            coll.update(saved)

        _RESTORERS.append(_restore)
    monkeypatch.setattr(alertutils, "_ALERT_ID_LAST_MS", 0)
    monkeypatch.setattr(alertutils, "_ALERT_ID_COUNTER", 0)
    mgr = alertutils.AlertStateManager()
    mgr._initialized = initialized
    mgr._startup_trim_done = True
    monkeypatch.setattr(alertutils, "alert_state_manager", mgr)
    return db, mgr


_RESTORERS = []


def restore_collections():
    while _RESTORERS:
        _RESTORERS.pop()()

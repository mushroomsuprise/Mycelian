#!/usr/bin/env python3
"""Behavior checks for app-database reads, writes, and stored-alert paging."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database_manager import (  # noqa: E402
    DatabaseConfig,
    FirebaseDatabase,
    MongoDatabase,
    SQLDatabase,
)


def _alert(username: str, timestamp: float) -> dict:
    return {"username": username, "timestamp": timestamp, "message": "same"}


class _FirebaseNode:
    def __init__(self, data: dict):
        self.data = data
        self.updates = []

    def child(self, _path: str):
        return self

    def order_by_child(self, _name: str):
        return self

    def limit_to_last(self, count: int):
        cloned = _FirebaseNode(dict(self.data))
        cloned._limit = int(count)
        return cloned

    def get(self, shallow: bool = False):
        if shallow:
            return {key: True for key in self.data}
        items = sorted(
            self.data.items(),
            key=lambda item: float((item[1] or {}).get("timestamp") or 0),
        )
        limit = getattr(self, "_limit", None)
        if limit:
            items = items[-limit:]
        return {key: value for key, value in items}

    def update(self, updates: dict) -> None:
        self.updates.append(dict(updates))
        for path, value in updates.items():
            if value is None and path.startswith("Alerts/AlertStorage/"):
                self.data.pop(path.rsplit("/", 1)[-1], None)


class _MongoCursor:
    def __init__(self, docs: list):
        self._docs = list(docs)

    def sort(self, _spec):
        self._docs.sort(
            key=lambda doc: float(((doc.get("data") or {}).get("timestamp") or 0)),
            reverse=True,
        )
        return self

    def skip(self, count: int):
        self._docs = self._docs[int(count) :]
        return self

    def limit(self, count: int):
        self._docs = self._docs[: int(count)]
        return self

    def __iter__(self):
        return iter(self._docs)


class _MongoCollection:
    def __init__(self, docs: list):
        self.docs = docs

    def create_index(self, *_args, **_kwargs):
        return "idx"

    def find_one(self, query: dict):
        for doc in self.docs:
            if doc.get("data_path") == query.get("data_path"):
                return doc
        return None

    def _matches_prefix(self, doc: dict) -> bool:
        path = doc.get("data_path") or ""
        spec = query_range = {
            "gte": "Alerts/AlertStorage/",
            "lt": "Alerts/AlertStorage0",
        }
        return spec["gte"] <= path < spec["lt"]

    def find(self, query: dict, _projection=None):
        if "data_path" in query and isinstance(query["data_path"], dict):
            matched = [doc for doc in self.docs if self._matches_prefix(doc)]
        elif "$in" in query.get("data_path", {}):
            wanted = set(query["data_path"]["$in"])
            matched = [doc for doc in self.docs if doc.get("data_path") in wanted]
        else:
            matched = []
        return _MongoCursor(matched)

    def count_documents(self, query: dict) -> int:
        return len(list(self.find(query)))

    def delete_many(self, query: dict):
        wanted = set(query.get("data_path", {}).get("$in", []))
        self.docs = [doc for doc in self.docs if doc.get("data_path") not in wanted]


class SqlDatabasePerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = DatabaseConfig(
            database_type="sql",
            sql_database_path=os.path.join(self._tmpdir.name, "app.db"),
            streamer_name="tester",
        )
        self.db = SQLDatabase(config)
        self.assertTrue(self.db.initialize())

    def tearDown(self) -> None:
        for conn in list(self.db._connection_pool):
            conn.close()
        self.db._connection_pool.clear()
        if self.db._connection is not None:
            self.db._connection.close()
            self.db._connection = None
        self._tmpdir.cleanup()

    def test_upsert_keeps_created_at_and_replaces_json(self) -> None:
        self.assertTrue(self.db.set_data("Settings/App", {"theme": "dark"}))
        conn = self.db._get_connection()
        try:
            row = conn.execute(
                "SELECT id, created_at, data_json FROM app_data WHERE data_path = ?",
                ("Settings/App",),
            ).fetchone()
            original_id = row["id"]
            original_created = row["created_at"]
        finally:
            self.db._return_connection(conn)

        self.assertTrue(self.db.set_data("Settings/App", {"theme": "light"}))
        self.assertEqual(self.db.get_data("Settings/App"), {"theme": "light"})
        conn = self.db._get_connection()
        try:
            row = conn.execute(
                "SELECT id, created_at FROM app_data WHERE data_path = ?",
                ("Settings/App",),
            ).fetchone()
            self.assertEqual(row["id"], original_id)
            self.assertEqual(row["created_at"], original_created)
            names = {
                item[0]
                for item in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        finally:
            self.db._return_connection(conn)
        self.assertNotIn("idx_streamer_path", names)
        self.assertIn("idx_alert_storage_ts", names)

    def test_update_merges_on_one_connection(self) -> None:
        self.db.set_data("Settings/App", {"theme": "dark", "volume": 1})
        self.assertTrue(self.db.update_data("Settings/App", {"volume": 3}))
        self.assertEqual(
            self.db.get_data("Settings/App"), {"theme": "dark", "volume": 3}
        )

    def test_snapshot_includes_empty_documents(self) -> None:
        self.db.set_data("Alerts/BitAlerts", {})
        self.db.set_data("Nested/Child", {"ok": True})
        snapshot = self.db.get_snapshot()
        self.assertEqual(snapshot["Alerts"]["BitAlerts"], {})
        self.assertEqual(snapshot["Nested"]["Child"], {"ok": True})

    def test_multi_get_returns_missing_paths_as_empty(self) -> None:
        self.db.set_data("One", {"a": 1})
        loaded = self.db._get_multiple_data(["One", "Missing", "/One"])
        self.assertEqual(loaded["One"], {"a": 1})
        self.assertEqual(loaded["/One"], {"a": 1})
        self.assertEqual(loaded["Missing"], {})

    def test_alert_page_keeps_json_and_newest_first(self) -> None:
        older = _alert("older", 10)
        newer = _alert("newer", 50)
        self.db.set_data("Alerts/AlertStorage/old", older)
        self.db.set_data("Alerts/AlertStorage/new", newer)
        page = self.db.query_alert_storage_page(1, 1)
        self.assertEqual(page["total_count"], 2)
        self.assertEqual(page["alerts"][0]["alert_id"], "new")
        self.assertEqual(page["alerts"][0]["username"], "newer")
        self.assertEqual(self.db.get_data("Alerts/AlertStorage/old"), older)
        self.assertEqual(self.db.get_data("Alerts/AlertStorage/new"), newer)
        second = self.db.query_alert_storage_page(2, 1)
        self.assertEqual(second["alerts"][0]["alert_id"], "old")
        self.assertTrue(
            self.db.delete_paths(
                ["Alerts/AlertStorage/old", "Alerts/AlertStorage/new"]
            )
        )
        self.assertEqual(self.db.get_data("Alerts/AlertStorage/new"), {})


class FirebaseAlertStorageTests(unittest.TestCase):
    def test_page_and_delete_do_not_rewrite_documents(self) -> None:
        stored = {
            "old": _alert("older", 10),
            "new": _alert("newer", 50),
        }
        original = {key: dict(value) for key, value in stored.items()}
        db = FirebaseDatabase(DatabaseConfig(database_type="firebase"))
        db._initialized = True
        db._root_ref = _FirebaseNode(stored)
        page = db.query_alert_storage_page(1, 1)
        self.assertEqual(page["total_count"], 2)
        self.assertEqual(page["alerts"][0]["username"], "newer")
        self.assertEqual(stored, original)
        self.assertTrue(db.delete_paths(["Alerts/AlertStorage/old"]))
        self.assertNotIn("old", stored)
        self.assertEqual(stored["new"], original["new"])


class MongoAlertStorageTests(unittest.TestCase):
    def _db(self, docs: list) -> MongoDatabase:
        db = MongoDatabase(
            DatabaseConfig(database_type="mongodb", mongodb_database_name="mycelian")
        )
        db._initialized = True
        db._collection = _MongoCollection(docs)
        return db

    def test_child_documents_page_without_rewriting_json(self) -> None:
        older = _alert("older", 10)
        newer = _alert("newer", 50)
        docs = [
            {"data_path": "Alerts/AlertStorage/old", "data": dict(older)},
            {"data_path": "Alerts/AlertStorage/new", "data": dict(newer)},
        ]
        db = self._db(docs)
        page = db.query_alert_storage_page(1, 1)
        self.assertEqual(page["total_count"], 2)
        self.assertEqual(page["alerts"][0]["alert_id"], "new")
        self.assertEqual(docs[0]["data"], older)
        self.assertEqual(docs[1]["data"], newer)
        loaded = db._get_multiple_data(
            ["Alerts/AlertStorage/new", "Missing"]
        )
        self.assertEqual(loaded["Alerts/AlertStorage/new"], newer)
        self.assertEqual(loaded["Missing"], {})

    def test_parent_document_is_still_readable(self) -> None:
        blob = {"legacy": _alert("legacy", 3)}
        docs = [{"data_path": "Alerts/AlertStorage", "data": blob}]
        db = self._db(docs)
        fetched = db.fetch_alert_storage()
        self.assertEqual(fetched["legacy"]["username"], "legacy")
        self.assertEqual(docs[0]["data"], blob)

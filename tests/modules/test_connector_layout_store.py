# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_layout_store: load / parse / save (incl. recovery after a failed read and write
failures), reconcile with existing connectors, and every folder / ordering operation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import contextlib
from types import SimpleNamespace

import pytest

from modules import connector_layout_store as ls


class FakeDB:
    def __init__(self):
        self.stored = None
        self.read_ok = True
        self.write_ok = True
        self.problem = None
        self.writes = []
        self.raise_on_write = None

    def get_data_checked(self, key):
        assert key == ls.LAYOUT_PATH
        return (self.stored if self.read_ok else None), self.read_ok

    def set_data(self, key, value):
        if self.raise_on_write:
            raise self.raise_on_write
        self.writes.append(value)
        if self.write_ok:
            self.stored = value
        return self.write_ok

    @contextlib.contextmanager
    def quiet_notifications(self):
        yield

    def last_write_problem(self):
        return self.problem


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in ("get_data_checked", "set_data", "quiet_notifications", "last_write_problem"):
        monkeypatch.setattr(ls.database_manager, name, getattr(fake, name))
    fake.notes = []
    monkeypatch.setattr(ls, "notify", lambda msg, **kw: fake.notes.append(kw))
    monkeypatch.setattr(ls, "nav_actions_main_tab", lambda tab: [("nav", tab)])
    monkeypatch.setattr(ls, "_layout_read_failed", False)
    return fake


def C(cid):
    return {"kind": "connector", "id": cid}


def F(fid):
    return {"kind": "folder", "id": fid}


def layout(root, folders=None):
    return {"version": 1, "root_items": root, "folders": folders or {}}


# --------------------------------------------------------------------------- load / parse


def test_default_layout():
    assert ls.default_layout() == {"version": 1, "root_items": [], "folders": {}}
    assert ls.default_layout() is not ls.default_layout()


@pytest.mark.parametrize("raw", [None, {}, "x", {"version": 99, "root_items": [C("a")]}])
def test_parse_layout_defaults(raw):
    assert ls._parse_layout(raw) == ls.default_layout()


def test_parse_layout_normalizes():
    raw = {"version": 1, "root_items": None,
           "folders": {1: {"name": "", "connector_ids": ["a", "", None, 5]}, "bad": "x"}}
    assert ls._parse_layout(raw) == {"version": 1, "root_items": [],
                                     "folders": {"1": {"name": "Folder", "connector_ids": ["a", "5"]}}}
    assert ls._parse_layout({"version": 1, "folders": ["not", "dict"]})["folders"] == {}


def test_load_layout_and_failed_read(db):
    db.stored = layout([C("a")])
    assert ls.load_layout()["root_items"] == [C("a")]
    assert ls._layout_read_failed is False
    db.read_ok = False
    assert ls.load_layout() == ls.default_layout()
    assert ls._layout_read_failed is True


# --------------------------------------------------------------------------- save


def test_save_layout_payload(db):
    lay = layout([C("a"), F("f")], {"f": {"name": "", "connector_ids": None}, "x": "bad"})
    assert ls.save_layout(lay) is True
    assert db.stored == {"version": 1, "root_items": [C("a"), F("f")],
                         "folders": {"f": {"name": "Folder", "connector_ids": []}}}
    assert ls.save_layout({}) is True and db.stored == ls.default_layout()


def test_save_layout_write_false_with_problem(db):
    db.write_ok = False
    db.problem = ("locked", "", "db")
    assert ls.save_layout(layout([])) is False
    assert db.notes[-1]["details"] == "Database write returned no success for connectors_layout (locked: db)"
    assert db.notes[-1]["dedupe_key"] == "connectors:layout_save"
    db.problem = None
    assert ls.save_layout(layout([])) is False
    assert db.notes[-1]["details"].endswith("connectors_layout")


def test_save_layout_exception(db):
    db.raise_on_write = OSError("disk")
    assert ls.save_layout(layout([])) is False
    assert db.notes[-1]["details"] == "OSError: disk" and db.notes[-1]["actions"] == [("nav", "Connectors")]


def test_save_after_failed_read_still_unreadable(db, monkeypatch):
    monkeypatch.setattr(ls, "_layout_read_failed", True)
    db.read_ok = False
    assert ls.save_layout(layout([C("a")])) is False
    assert db.writes == [] and ls._layout_read_failed is True


def test_save_after_failed_read_empty_store(db, monkeypatch):
    monkeypatch.setattr(ls, "_layout_read_failed", True)
    db.stored = None
    assert ls.save_layout(layout([C("a")])) is True
    assert db.stored["root_items"] == [C("a")] and ls._layout_read_failed is False


def test_save_after_failed_read_merges_into_stored(db, monkeypatch):
    monkeypatch.setattr(ls, "_layout_read_failed", True)
    db.stored = layout([C("a"), F("old"), C("b")], {"old": {"name": "Old", "connector_ids": ["c", "d"]}})
    local = layout([F("new"), C("e")], {"new": {"name": "New", "connector_ids": ["b", "c"]}})
    assert ls.save_layout(local) is True
    assert db.stored["root_items"] == [C("a"), F("old"), F("new"), C("e")]
    assert db.stored["folders"] == {"old": {"name": "Old", "connector_ids": ["d"]},
                                    "new": {"name": "New", "connector_ids": ["b", "c"]}}


def test_merge_into_stored_edge_cases():
    stored = {"root_items": [C("a"), "junk"], "folders": {"f": {"name": "F", "connector_ids": ["b"]}}}
    local = {"root_items": ["junk", C("a"), F("f"), F("ghost"), C("z")],
             "folders": {"f": {"name": "Changed", "connector_ids": ["q"]}, "bad": "x"}}
    merged = ls._merge_into_stored(stored, local)
    assert merged["root_items"] == [C("a"), "junk", C("z")]
    assert merged["folders"] == {"f": {"name": "F", "connector_ids": ["b"]}}
    assert stored["root_items"] == [C("a"), "junk"]  # not mutated
    assert ls._merge_into_stored({}, {}) == {"folders": {}, "root_items": []}


# --------------------------------------------------------------------------- reconcile


def test_normalize_root_item():
    assert ls._normalize_root_item("x") is None
    assert ls._normalize_root_item({"kind": "other", "id": "a"}) is None
    assert ls._normalize_root_item({"kind": "folder", "id": ""}) is None
    assert ls._normalize_root_item({"kind": "connector", "id": 5}) == ("connector", "5")


def test_reconcile_layout():
    lay = layout(
        [C("a"), C("a"), C("gone"), "junk", F("f1"), F("f_empty"), F("missing"), F("f_dupe"), F("f_unknown")],
        {
            "f1": {"name": None, "connector_ids": ["b", "b", "gone", "c"]},
            "f_empty": {"name": "E", "connector_ids": ["gone"]},
            "f_dupe": {"name": "D", "connector_ids": ["a"]},
            "f_unknown": {"name": "U", "connector_ids": ["d"]},
            "orphan": {"name": "O", "connector_ids": ["e"]},
            "bad": "x",
        },
    )
    out = ls.reconcile_layout(lay, {"a", "b", "c", "d", "e", "f"})
    assert out["root_items"] == [C("a"), F("f1"), F("f_unknown"), C("e"), C("f")]
    assert out["folders"] == {"f1": {"name": "Folder", "connector_ids": ["b", "c"]},
                              "f_unknown": {"name": "U", "connector_ids": ["d"]}}
    assert lay["root_items"][0] == C("a") and "orphan" in lay["folders"]  # input untouched


def test_reconcile_layout_wrong_version_and_folder_without_members():
    out = ls.reconcile_layout({"version": 0, "root_items": [C("x")]}, {"x", "y"})
    assert out == {"version": 1, "root_items": [C("x"), C("y")], "folders": {}}
    # a folder whose member list is empty at the root pass (members filtered earlier)
    lay = layout([F("f")], {"f": {"name": "F", "connector_ids": ["a"]}})
    lay["folders"]["f"]["connector_ids"] = ["a"]
    out = ls.reconcile_layout(lay, set())
    assert out["root_items"] == [] and out["folders"] == {}


# --------------------------------------------------------------------------- operations


def base():
    return layout([C("a"), F("f"), C("d")], {"f": {"name": "F", "connector_ids": ["b", "c"]}})


def test_find_root_slot_index():
    lay = base()
    assert ls.find_root_slot_index(lay, "a") == 0
    assert ls.find_root_slot_index(lay, "c") == 1
    assert ls.find_root_slot_index(lay, "d") == 2
    assert ls.find_root_slot_index(lay, "zz") == 3
    assert ls.find_root_slot_index({}, "a") == 0
    assert ls.find_root_slot_index({"root_items": [F("nofolder")]}, "a") == 1


def test_remove_connector_from_layout():
    lay = base()
    ls.remove_connector_from_layout(lay, "a")
    assert lay["root_items"] == [F("f"), C("d")]
    ls.remove_connector_from_layout(lay, "b")
    assert lay["folders"]["f"]["connector_ids"] == ["c"]
    ls.remove_connector_from_layout(lay, "c")
    assert lay["folders"] == {} and lay["root_items"] == [C("d")]
    empty = {}
    ls.remove_connector_from_layout(empty, "x")
    assert empty == {"folders": {}, "root_items": []}


def test_merge_into_new_folder(monkeypatch):
    monkeypatch.setattr(ls.uuid, "uuid4", lambda: "NEW")
    lay = base()
    assert ls.merge_into_new_folder(lay, "a", "a") == (lay, "")
    out, fid = ls.merge_into_new_folder(lay, "a", "d", "Mine")
    assert fid == "NEW" and out["folders"]["NEW"] == {"name": "Mine", "connector_ids": ["d", "a"]}
    assert out["root_items"] == [F("f"), F("NEW")]
    assert lay == base()
    # target inside a folder: the new folder takes the old folder's slot
    out, _ = ls.merge_into_new_folder(base(), "d", "b")
    assert out["root_items"] == [C("a"), F("NEW"), F("f")]
    assert out["folders"]["NEW"]["connector_ids"] == ["b", "d"] and out["folders"]["f"]["connector_ids"] == ["c"]
    # insert index past the end is clamped
    out, _ = ls.merge_into_new_folder(layout([C("x"), C("y")]), "x", "missing")
    assert out["root_items"] == [C("y"), F("NEW")]


def test_move_connector_to_folder():
    lay = base()
    out = ls.move_connector_to_folder(lay, "a", "f")
    assert out["folders"]["f"]["connector_ids"] == ["b", "c", "a"] and out["root_items"] == [F("f"), C("d")]
    assert ls.move_connector_to_folder(lay, "a", "missing") == lay
    off_root = layout([C("a")], {"f": {"name": "F", "connector_ids": ["b"]}})
    assert ls.move_connector_to_folder(off_root, "a", "f") == off_root
    two = layout([F("f"), F("g")], {"f": {"name": "F", "connector_ids": ["b", "c"]},
                                     "g": {"name": "G", "connector_ids": ["e"]}})
    out = ls.move_connector_to_folder(two, "b", "g")
    assert out["folders"]["g"]["connector_ids"] == ["e", "b"] and out["folders"]["f"]["connector_ids"] == ["c"]
    # already the folder's only member: no-op (the folder is not dropped)
    solo = layout([F("g")], {"g": {"name": "G", "connector_ids": ["e"]}})
    assert ls.move_connector_to_folder(solo, "e", "g") == solo


def test_move_connector_to_root():
    out = ls.move_connector_to_root(base(), "b")
    assert out["root_items"] == [C("a"), F("f"), C("d"), C("b")]
    assert out["folders"]["f"]["connector_ids"] == ["c"]
    assert ls.move_connector_to_root({}, "z")["root_items"] == [C("z")]


def test_rename_folder():
    out = ls.rename_folder(base(), "f", "  Clips ")
    assert out["folders"]["f"]["name"] == "Clips"
    assert ls.rename_folder(base(), "f", "   ")["folders"]["f"]["name"] == "Folder"
    assert ls.rename_folder(base(), "nope", "x") == base()
    assert ls.rename_folder({}, "f", "x") == {"folders": {}}


def test_delete_folder_keep_connectors():
    out = ls.delete_folder_keep_connectors(base(), "f")
    assert out["root_items"] == [C("a"), C("b"), C("c"), C("d")] and out["folders"] == {}
    out = ls.delete_folder_keep_connectors(base(), "missing")
    assert out["root_items"] == base()["root_items"]
    off_root = layout([C("a")], {"f": {"name": "F", "connector_ids": ["b"]}})
    assert ls.delete_folder_keep_connectors(off_root, "f")["root_items"] == [C("a"), C("b")]


def test_list_folder_ids_and_delete_record_only():
    assert ls.list_folder_connector_ids(base(), "f") == ["b", "c"]
    assert ls.list_folder_connector_ids(base(), "x") == [] and ls.list_folder_connector_ids({}, "f") == []
    out, members = ls.delete_folder_record_only(base(), "f")
    assert members == ["b", "c"] and out["root_items"] == [C("a"), C("d")] and out["folders"] == {}
    out, members = ls.delete_folder_record_only({}, "f")
    assert members == [] and out == {"folders": {}, "root_items": []}
